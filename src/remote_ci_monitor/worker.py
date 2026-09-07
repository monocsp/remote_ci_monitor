"""워커 — 레인마다 스레드 하나. claim → 워크스페이스 → Popen(argv) → 로그·마커 → 종료 상태.

- 프리셋의 `argv` 만 실행한다. 셸 없음. 입력은 `RCM_INPUT_<NAME>` env 로만.
- `start_new_session=True` 로 프로세스 그룹을 만들어 취소·타임아웃 때 손자까지 `killpg` 한다.
  SIGTERM → `grace_seconds` → SIGKILL.
- 로그는 `<data_dir>/jobs/<id>/log.txt` 에 줄 단위 flush. 마커는 **수신 시각**과 함께 DB 이벤트로.
- 워커 스레드가 예외로 죽으면 `down` + `error` 로 남기고 재시작하지 않는다(사람이 봐야 한다).
  죽기 전에 잡고 있던 잡은 `failed`(exit_code null) 로 닫는다 — 큐에서 사라지는 잡은 없다.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.inputs import env_for_inputs
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    FAILED,
    LOST,
    MODE_TREE,
    PHASE_EXECUTING,
    SUCCEEDED,
    TIMED_OUT,
    WORKER_BUSY,
    WORKER_DOWN,
    WORKER_IDLE,
    Job,
    WorkerInfo,
)
from remote_ci_monitor.core.progress import Marker, parse_marker, progress_from_markers
from remote_ci_monitor.materialize import (
    MaterializeError,
    assemble_from_manifest,
    extract_tree,
    prepare_git_ref,
)
from remote_ci_monitor.store import Store

READ_CHUNK = 65536
POLL_SECONDS = 1.0
IDLE_WAIT_SECONDS = 0.5
MAX_LINE_BYTES = 64 * 1024
_PATH_RE = re.compile(r"/[^\s'\"]+")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _safe_error(e: BaseException) -> str:
    """오류 문구에서 절대 경로를 지운다(상태 JSON 에 실린다)."""
    text = getattr(e, "strerror", None) or str(e)
    text = _PATH_RE.sub("<path>", text)[:160]
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


def format_limit(seconds: int | None) -> str:
    if seconds is None:
        return "limit"
    if seconds % 3600 == 0:
        return f"limit {seconds // 3600}h"
    if seconds % 60 == 0:
        return f"limit {seconds // 60}m"
    return f"limit {seconds}s"


def tail_lines(path: Path, n: int = 5, max_bytes: int = 8192) -> list[str] | None:
    """로그 파일의 마지막 n 줄(최대 max_bytes). 파일이 없으면 None."""
    if n <= 0:
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # 잘린 첫 줄은 버린다
    return lines[-n:]


@dataclass
class _Outcome:
    cancelled: bool = False
    timed_out: bool = False
    lost: bool = False
    term_sent_at: datetime | None = None
    kill_sent: bool = False


def outcome_for(
    job: Job,
    markers: Sequence[Marker],
    *,
    started: datetime,
    finished: datetime,
    rc: int | None,
    cancelled: bool = False,
    timed_out: bool = False,
    lost: bool = False,
    lost_summary: str = "server stopped while running",
) -> tuple[str, str | None, str | None]:
    """종료 규칙 — (상태, 요약, failed_step). 로컬 워커와 원격 워커 보고(`/worker/.../finish`)가
    같은 함수를 쓴다(M5b-2). 요약은 마커의 `summary`, 없으면 `exit N`; 취소는 요청자 이름."""
    forced = cancelled or timed_out or lost
    progress = progress_from_markers(
        markers,
        started_at=started,
        finished_at=finished,
        now=finished,
        exit_code=rc if not forced else 1,
    )
    if lost:
        state, summary = LOST, lost_summary
    elif cancelled:
        state = CANCELLED
        summary = f"cancelled by {job.cancel.by}" if job.cancel is not None else None
    elif timed_out:
        state, summary = TIMED_OUT, format_limit(job.timeout_seconds)
    elif rc == 0:
        state, summary = SUCCEEDED, progress.summary
    else:
        state, summary = FAILED, progress.summary or f"exit {rc}"
    failed_step = progress.failed_step if state != SUCCEEDED else None
    return state, summary, failed_step


class Worker(threading.Thread):
    """레인 하나. `wake` 이벤트로 깨우고, `stop` 이벤트로 멈춘다(도는 잡은 끝까지 기다린다)."""

    def __init__(
        self,
        lane: int,
        store: Store,
        config: ServerConfig,
        *,
        wake: threading.Event | None = None,
        stop: threading.Event | None = None,
        on_change: Callable[[int], None] | None = None,
        on_marker: Callable[[int, str, str], None] | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        environ: dict[str, str] | None = None,
    ):
        super().__init__(name=f"rcm-worker-{lane}", daemon=True)
        self.lane = lane
        self.store = store
        self.config = config
        self.wake = wake or threading.Event()
        self.stop_event = stop or threading.Event()
        self.on_change = on_change
        self.on_marker = on_marker
        self.now_fn = now_fn
        self.environ = environ if environ is not None else dict(os.environ)
        self._lock = threading.Lock()
        self._state = WORKER_IDLE
        self._job_id: int | None = None
        self._error: str | None = None
        self._since: datetime = now_fn()
        self._proc: subprocess.Popen | None = None
        self._shutting_down = False

    def shutdown(self) -> None:
        """서버 종료. 도는 잡의 프로세스 그룹을 죽이고 잡은 `lost` 로 남긴다."""
        self._shutting_down = True
        self.stop_event.set()
        self.wake.set()
        proc = self._proc
        if proc is not None:
            self._signal(proc, signal.SIGTERM)

    # ── 상태 ────────────────────────────────────────────────────────────────

    def info(self) -> WorkerInfo:
        with self._lock:
            return WorkerInfo(
                lane=self.lane,
                state=self._state,
                job_id=self._job_id,
                error=self._error,
                since=self._since,
            )

    def _set(self, state: str, job_id: int | None = None, error: str | None = None) -> None:
        with self._lock:
            if state != self._state or job_id != self._job_id:
                self._since = self.now_fn()
            self._state = state
            self._job_id = job_id
            self._error = error

    def _changed(self, job_id: int) -> None:
        if self.on_change is not None:
            try:
                self.on_change(job_id)
            except Exception:  # noqa: BLE001 — 콜백 오류가 워커를 죽이면 안 된다
                pass

    # ── 루프 ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        current: Job | None = None
        try:
            while not self.stop_event.is_set():
                if self.store.get_paused() is not None:
                    self.wake.wait(IDLE_WAIT_SECONDS)
                    self.wake.clear()
                    continue
                current = self.store.claim(self.lane, self.now_fn())
                if current is None:
                    self.wake.wait(IDLE_WAIT_SECONDS)
                    self.wake.clear()
                    continue
                self._set(WORKER_BUSY, current.id)
                self._changed(current.id)
                self.execute(current)  # 예외는 바깥 except 가 잡고 current 를 닫는다
                self._changed(current.id)
                self._set(WORKER_IDLE)
                current = None
        except Exception as e:  # noqa: BLE001 — 어떤 예외든 down 으로 남긴다
            err = _safe_error(e)
            if current is not None:
                try:
                    self.store.finish(
                        current.id,
                        FAILED,
                        now=self.now_fn(),
                        summary=f"worker error: {err}"[:200],
                    )
                    self._changed(current.id)
                except Exception:  # noqa: BLE001
                    pass
            self._set(WORKER_DOWN, None, err)

    # ── 실행 ────────────────────────────────────────────────────────────────

    def _paths(self, job: Job) -> tuple[Path, Path, Path]:
        data = self.config.data_dir
        job_dir = data / "jobs" / str(job.id)
        workspace = data / "workspaces" / str(job.id)
        return job_dir, workspace, job_dir / "log.txt"

    def _fail(self, job: Job, summary: str) -> None:
        self.store.finish(job.id, FAILED, now=self.now_fn(), exit_code=None, summary=summary[:200])

    @staticmethod
    def _append_log(log_path: Path, line: str) -> None:
        """자재화 단계(프로세스가 뜨기 전)의 줄을 잡 로그에 남긴다. 실패해도 잡을 막지 않는다."""
        try:
            with log_path.open("ab") as fh:
                fh.write(line.encode("utf-8", errors="replace") + b"\n")
        except OSError:
            pass

    def execute(self, job: Job) -> None:
        job_dir, workspace, log_path = self._paths(job)
        job_dir.mkdir(parents=True, exist_ok=True)
        preset = self.config.preset(job.preset)
        if preset is None:
            self._fail(job, f"preset '{job.preset}' is no longer configured")
            return
        # ── 자재화 ──
        try:
            if job.source.mode == MODE_TREE:
                manifest_path = job_dir / "manifest.json"
                tar_path = job_dir / "tree.tar.gz"
                if manifest_path.is_file():  # M5 캐시 업로드
                    assemble_from_manifest(manifest_path, self.config.data_dir / "blobs", workspace)
                elif tar_path.is_file():
                    extract_tree(tar_path, workspace)
                else:
                    raise MaterializeError("snapshot file is missing")
            else:
                repo = self.config.repo(job.source.repo)
                if repo is None:
                    raise MaterializeError(
                        f"repo '{job.source.repo or preset.repo}' is no longer configured"
                    )
                prepare_git_ref(
                    job,
                    workspace,
                    repo_name=repo.name,
                    repo_url=repo.url,
                    mirror=self.config.data_dir / "mirrors" / repo.name,
                    timeout=self.config.server.git_fetch_timeout_seconds,
                    log=lambda line: self._append_log(log_path, line),
                )
        except MaterializeError as e:
            self._fail(job, str(e))
            return
        # ── 실행 ──
        env = self._env(job, preset, workspace, log_path)
        started = self.now_fn()
        self.store.set_phase(job.id, PHASE_EXECUTING)
        self.store.set_last_output(job.id, started)
        self._changed(job.id)  # materializing → executing
        try:
            log = log_path.open("ab")
        except OSError as e:
            self._fail(job, f"cannot open log file: {_safe_error(e)}")
            return
        with log:
            try:
                proc = subprocess.Popen(
                    list(preset.argv),
                    cwd=str(workspace),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as e:
                self._fail(job, f"cannot start {preset.argv[0]!r}: {_safe_error(e)}")
                return
            self._proc = proc
            try:
                outcome = self._pump(job, proc, log, started)
            finally:
                self._proc = None
        rc = proc.returncode
        markers = self.store.markers(job.id)
        finished = self.now_fn()
        job_now = self.store.get_job(job.id) or job
        state, summary, failed_step = outcome_for(
            job_now,
            markers,
            started=started,
            finished=finished,
            rc=rc,
            cancelled=outcome.cancelled,
            timed_out=outcome.timed_out,
            lost=outcome.lost,
        )
        self.store.finish(
            job.id, state, now=finished, exit_code=rc, summary=summary, failed_step=failed_step
        )
        # ── 정리 ──
        keep = state != SUCCEEDED and self.config.server.keep_workspace_on_failure
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)

    def _env(self, job: Job, preset, workspace: Path, log_path: Path) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in preset.env_passthrough:
            if key in self.environ:
                env[key] = self.environ[key]
        env.update(preset.env)
        env.update(
            {
                "RCM_JOB_ID": str(job.id),
                "RCM_PRESET": job.preset,
                "RCM_REQUESTER": job.requester.label,
                "RCM_SOURCE_MODE": job.source.mode,
                "RCM_REF": job.source.ref or "",
                "RCM_BASE_SHA": job.source.base_sha or "",
                "RCM_DIRTY": "1" if job.source.dirty else "0",
                "RCM_WORKSPACE": str(workspace),
                "RCM_LOG_FILE": str(log_path),
            }
        )
        env.update(env_for_inputs(job.inputs))
        return env

    def _pump(self, job: Job, proc: subprocess.Popen, log, started: datetime) -> _Outcome:
        """stdout 을 파일로 흘리며 마커·취소·타임아웃을 본다. 프로세스가 끝날 때까지 돈다."""
        assert proc.stdout is not None
        fd = proc.stdout.fileno()
        chunks: queue.Queue[bytes | None] = queue.Queue()

        def reader() -> None:
            try:
                while True:
                    data = os.read(fd, READ_CHUNK)
                    if not data:
                        break
                    chunks.put(data)
            except OSError:
                pass
            finally:
                chunks.put(None)

        threading.Thread(target=reader, name=f"rcm-reader-{job.id}", daemon=True).start()
        outcome = _Outcome()
        buf = b""
        eof = False
        last_check = started
        last_output_write = started
        grace = timedelta(seconds=self.config.server.grace_seconds)
        timeout = timedelta(seconds=job.timeout_seconds) if job.timeout_seconds else None
        while not eof:
            try:
                data = chunks.get(timeout=POLL_SECONDS)
            except queue.Empty:
                data = b""
            now = self.now_fn()
            if data is None:
                eof = True
            elif data:
                buf += data
                *lines, buf = buf.split(b"\n")
                if len(buf) > MAX_LINE_BYTES:  # 개행 없는 긴 출력은 잘라서 흘린다
                    lines.append(buf)
                    buf = b""
                for raw in lines:
                    log.write(raw + b"\n")
                    line = raw.decode("utf-8", errors="replace")
                    parsed = parse_marker(line)
                    if parsed is not None:
                        self.store.add_marker(job.id, parsed[0], parsed[1], now)
                        if self.on_marker is not None:
                            try:
                                self.on_marker(job.id, parsed[0], parsed[1])
                            except Exception:  # noqa: BLE001
                                pass
                        self._changed(job.id)
                if lines:
                    log.flush()
                    if (now - last_output_write).total_seconds() >= 1.0:
                        self.store.set_last_output(job.id, now)
                        last_output_write = now
            # ── 취소 · 타임아웃 (1초마다) ──
            if (now - last_check).total_seconds() >= POLL_SECONDS or eof:
                last_check = now
                if self._shutting_down and outcome.term_sent_at is None:
                    outcome.lost = True
                    self._signal(proc, signal.SIGTERM)
                    outcome.term_sent_at = now
                if not (outcome.cancelled or outcome.timed_out or outcome.lost):
                    current = self.store.get_job(job.id)
                    if current is not None and current.state == CANCELLING:
                        outcome.cancelled = True
                        self._signal(proc, signal.SIGTERM)
                        outcome.term_sent_at = now
                    elif timeout is not None and now - started > timeout and current is not None:
                        outcome.timed_out = True
                        self._signal(proc, signal.SIGTERM)
                        outcome.term_sent_at = now
                if outcome.term_sent_at is not None and not outcome.kill_sent:
                    if now - outcome.term_sent_at >= grace:
                        self._signal(proc, signal.SIGKILL)
                        outcome.kill_sent = True
            if not eof and outcome.kill_sent and proc.poll() is not None:
                # 손자가 파이프를 잡고 있어도 더 기다리지 않는다
                break
        if buf:
            log.write(buf + b"\n")
            log.flush()
        try:
            proc.wait(timeout=self.config.server.grace_seconds + 5)
        except subprocess.TimeoutExpired:
            self._signal(proc, signal.SIGKILL)
            proc.wait()
        try:
            proc.stdout.close()
        except OSError:
            pass
        return outcome

    @staticmethod
    def _signal(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass


def start_workers(
    store: Store,
    config: ServerConfig,
    *,
    wake: threading.Event,
    stop: threading.Event,
    on_change: Callable[[int], None] | None = None,
    on_marker: Callable[[int, str, str], None] | None = None,
    now_fn: Callable[[], datetime] = _utcnow,
) -> list[Worker]:
    workers = [
        Worker(
            lane,
            store,
            config,
            wake=wake,
            stop=stop,
            on_change=on_change,
            on_marker=on_marker,
            now_fn=now_fn,
        )
        for lane in range(1, config.server.lanes + 1)
    ]
    for w in workers:
        w.start()
    return workers


__all__ = ["Worker", "start_workers", "tail_lines", "format_limit", "outcome_for"]
