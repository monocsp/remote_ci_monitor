"""워커 — 레인마다 스레드 하나. claim → 워크스페이스 → Popen(argv) → 로그·마커 → 종료 상태.

- 프리셋의 `argv` 만 실행한다. 셸 없음. 입력은 `RCM_INPUT_<NAME>` env 로만.
- `start_new_session=True` 로 프로세스 그룹을 만들어 취소·타임아웃 때 손자까지 `killpg` 한다.
  SIGTERM → `grace_seconds` → SIGKILL.
- 로그는 `<data_dir>/jobs/<id>/log.txt` 에 줄 단위 flush. 마커는 **수신 시각**과 함께 DB 이벤트로.
- 실행 자체(Popen · 펌프 · 신호)는 `runner.run_job`(M5b-3) — 원격 워커와 같은 코드. 이 모듈은
  claim · 자재화 · DB 기록(관찰자) · 종료 규칙(`outcome_for`)을 맡는다.
- 워커 스레드가 예외로 죽으면 `down` + `error` 로 남기고 재시작하지 않는다(사람이 봐야 한다).
  죽기 전에 잡고 있던 잡은 `failed`(exit_code null) 로 닫는다 — 큐에서 사라지는 잡은 없다.
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    FAILED,
    LOST,
    MODE_TREE,
    SUCCEEDED,
    TIMED_OUT,
    WORKER_BUSY,
    WORKER_DOWN,
    WORKER_IDLE,
    Job,
    Preset,
    WorkerInfo,
)
from remote_ci_monitor.core.progress import Marker, parse_marker, progress_from_markers
from remote_ci_monitor.materialize import (
    MaterializeError,
    assemble_from_manifest,
    extract_tree,
    prepare_git_ref,
)
from remote_ci_monitor.runner import (
    MAX_LINE_BYTES,
    POLL_SECONDS,
    READ_CHUNK,
    RunnerError,
    RunSpec,
    run_job,
    safe_error,
)
from remote_ci_monitor.store import Store

IDLE_WAIT_SECONDS = 0.5


def _utcnow() -> datetime:
    return datetime.now(UTC)


_safe_error = safe_error  # 옛 이름(테스트·다른 모듈) 호환


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
        state = FAILED
        summary = progress.summary or (f"exit {rc}" if rc is not None else None)
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
        self._shutting_down = False

    def shutdown(self) -> None:
        """서버 종료. 도는 잡은 실행기가 `should_stop` 을 보고 프로세스 그룹에 SIGTERM 을 보내
        `lost` 로 남긴다(1초 폴링 안에)."""
        self._shutting_down = True
        self.stop_event.set()
        self.wake.set()

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

    def _materialize(self, job: Job, preset: Preset, workspace: Path, log_path: Path) -> None:
        job_dir = self.config.data_dir / "jobs" / str(job.id)
        if job.source.mode == MODE_TREE:
            manifest_path = job_dir / "manifest.json"
            tar_path = job_dir / "tree.tar.gz"
            if manifest_path.is_file():  # M5 캐시 업로드
                assemble_from_manifest(manifest_path, self.config.data_dir / "blobs", workspace)
            elif tar_path.is_file():
                extract_tree(tar_path, workspace)
            else:
                raise MaterializeError("snapshot file is missing")
            return
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

    def execute(self, job: Job) -> None:
        job_dir, workspace, log_path = self._paths(job)
        job_dir.mkdir(parents=True, exist_ok=True)
        preset = self.config.preset(job.preset)
        if preset is None:
            self._fail(job, f"preset '{job.preset}' is no longer configured")
            return
        spec = RunSpec(
            job_id=job.id,
            preset_name=preset.name,
            argv=tuple(preset.argv),
            env=preset.env,
            env_passthrough=tuple(preset.env_passthrough),
            timeout_seconds=job.timeout_seconds,
            inputs=job.inputs,
            requester_label=job.requester.label,
            source=job.source,
            workspace=workspace,
            log_path=log_path,
            grace_seconds=self.config.server.grace_seconds,
        )
        observer = _LocalObserver(self, job)
        try:
            result = run_job(
                spec,
                observer,
                now_fn=self.now_fn,
                environ=self.environ,
                materialize=lambda _spec: self._materialize(job, preset, workspace, log_path),
            )
        except (MaterializeError, RunnerError) as e:
            self._fail(job, str(e))
            return
        markers = self.store.markers(job.id)
        job_now = self.store.get_job(job.id) or job
        state, summary, failed_step = outcome_for(
            job_now,
            markers,
            started=result.started,
            finished=result.finished,
            rc=result.rc,
            cancelled=result.cancelled,
            timed_out=result.timed_out,
            lost=result.lost,
        )
        self.store.finish(
            job.id,
            state,
            now=result.finished,
            exit_code=result.rc,
            summary=summary,
            failed_step=failed_step,
        )
        # ── 정리 ──
        keep = state != SUCCEEDED and self.config.server.keep_workspace_on_failure
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)


class _LocalObserver:
    """로컬 워커의 관찰자 — phase·마커·last_output 을 DB 에 직접 쓴다(오늘의 `_pump` 규칙)."""

    def __init__(self, worker: Worker, job: Job):
        self.worker = worker
        self.job = job
        self._last_output_write: datetime | None = None

    def phase(self, phase: str) -> None:
        now = self.worker.now_fn()
        self.worker.store.set_phase(self.job.id, phase)
        self.worker.store.set_last_output(self.job.id, now)
        self._last_output_write = now
        self.worker._changed(self.job.id)  # materializing → executing

    def output(self, data: bytes) -> None:
        now = self.worker.now_fn()
        for raw in data.split(b"\n"):
            if not raw:
                continue
            parsed = parse_marker(raw.decode("utf-8", errors="replace"))
            if parsed is None:
                continue
            self.worker.store.add_marker(self.job.id, parsed[0], parsed[1], now)
            if self.worker.on_marker is not None:
                try:
                    self.worker.on_marker(self.job.id, parsed[0], parsed[1])
                except Exception:  # noqa: BLE001
                    pass
            self.worker._changed(self.job.id)
        last = self._last_output_write
        if last is None or (now - last).total_seconds() >= 1.0:
            self.worker.store.set_last_output(self.job.id, now)
            self._last_output_write = now

    def should_cancel(self) -> bool:
        current = self.worker.store.get_job(self.job.id)
        return current is not None and current.state == CANCELLING

    def should_stop(self) -> bool:
        return self.worker._shutting_down


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


__all__ = [
    "Worker",
    "start_workers",
    "tail_lines",
    "format_limit",
    "outcome_for",
    "MAX_LINE_BYTES",
    "POLL_SECONDS",
    "READ_CHUNK",
]
