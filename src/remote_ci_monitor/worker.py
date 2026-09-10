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
import secrets
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from remote_ci_monitor.collect import CollectResult, collect
from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core import outcome
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
    WORKER_HELD,
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
    RequiredToolMissing,
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


@dataclass(frozen=True)
class Outcome:
    """잡을 끝낼 때 필요한 값.

    `summary` 는 사람이 읽는 문장, `code`/`args` 는 화면이 자기 말로 다시 그릴 재료다(결정 37).
    """

    state: str
    summary: str | None
    failed_step: str | None
    code: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    #: 「끝났을 때 어디였나」. 실패 이름 셋과 함께 움직인다 — 취소·유실은 넷 다 비어 있다 (M5h)
    last_step: str | None = None
    fail_names: tuple[str, ...] = ()
    fail_truncated: bool = False

    def __iter__(self):
        """옛 3-튜플처럼 풀 수 있게 — `state, summary, failed_step = outcome_for(...)`."""
        return iter((self.state, self.summary, self.failed_step))


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
    lost_code: str | None = "server_stopped_while_running",
    lost_args: dict[str, Any] | None = None,
) -> Outcome:
    """종료 규칙 — 상태 · 요약 · failed_step, 그리고 서버가 만든 요약이면 코드와 인자(결정 37).

    로컬 워커와 원격 워커 보고(`/worker/.../finish`)가 같은 함수를 쓴다(M5b-2). 요약은 마커의
    `summary`, 없으면 `exit N`; 취소는 요청자 이름. **잡이 찍은 요약에는 코드가 없다** — 팀이 쓴
    문장이라 번역 대상이 아니다.
    """
    forced = cancelled or timed_out or lost
    progress = progress_from_markers(
        markers,
        started_at=started,
        finished_at=finished,
        now=finished,
        # 강제 종료에 1 을 넣으면 마지막 열린 스텝이 실패로 물든다 — 그게 #176 이었다(M5h).
        exit_code=None if forced else rc,
    )
    code: str | None = None
    args: dict[str, Any] = {}
    if lost:
        state, summary, code, args = LOST, lost_summary, lost_code, dict(lost_args or {})
    elif cancelled:
        state = CANCELLED
        if job.cancel is not None:
            summary, code, args = outcome.summary("cancelled_by", by=job.cancel.by)
        else:
            summary = None
    elif timed_out:
        state = TIMED_OUT
        summary, code, args = outcome.summary("timed_out", seconds=job.timeout_seconds)
    elif rc == 0:
        state, summary = SUCCEEDED, progress.summary  # 잡이 찍은 문장 — 코드 없음
    else:
        state = FAILED
        if progress.summary:
            summary = progress.summary  # 잡이 찍은 문장 — 코드 없음
        elif rc is not None:
            summary, code, args = outcome.summary("exit_code", code=rc)
        else:
            summary = None
    # 스텝 라벨과 실패 이름은 `failed`·`timed_out` 만 갖는다(결정 64). 취소·유실 잡에 스텝
    # 이름을 붙이면 사람이 「그게 깨져서 멈췄나」로 읽는다. 성공 잡은 자신의 판정이 이긴다.
    #
    # dev 의 PR #71 이 이 자리에서 옳은 관찰을 했다 — 「게이트가 `test` 실패를 찍고도 계속
    # 돌다가 `build web` 에서 타임아웃으로 죽는 모양이 실제로 나온다」. M5h 에서는 그 잡이
    # `failed_step: test`(스크립트가 선언했다 — 참)와 `last_step: build web`(끝났을 때 거기
    # 있었다 — 참)을 **둘 다** 낸다. 두 사실이 서로 다른 칸에 있어서 추측 표시가 필요 없다.
    labelled = state in (FAILED, TIMED_OUT)
    return Outcome(
        state=state,
        summary=summary,
        failed_step=progress.failed_step if labelled else None,
        code=code,
        args=args,
        last_step=progress.last_step if labelled else None,
        fail_names=progress.fail_names if labelled else (),
        fail_truncated=progress.fail_truncated if labelled else False,
    )


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
        admit: Callable[[int, datetime, Callable[[], Job | None]], Any] | None = None,
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
        # 부하 게이트(M5f). None 이면 게이트 없이 오늘처럼 집는다(테스트·단독 사용).
        self.admit = admit
        self.environ = environ if environ is not None else dict(os.environ)
        self._lock = threading.Lock()
        self._state = WORKER_IDLE
        self._job_id: int | None = None
        self._error: str | None = None
        self._since: datetime = now_fn()
        self._hold: Any = None
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
            hold = self._hold if self._state == WORKER_HELD else None
            return WorkerInfo(
                lane=self.lane,
                state=self._state,
                job_id=self._job_id,
                error=self._error,
                since=self._since,
                hold_code=hold.code if hold else None,
                hold_detail=dict(hold.detail) if hold and hold.detail else None,
                held_since=self._since if hold else None,
            )

    def _set(self, state: str, job_id: int | None = None, error: str | None = None) -> None:
        with self._lock:
            if state != self._state or job_id != self._job_id:
                self._since = self.now_fn()
            self._state = state
            self._job_id = job_id
            self._error = error

    def _claim(self, now: datetime) -> Job | None:
        return self.store.claim(self.lane, now)

    def _set_hold(self, hold: Any) -> None:
        """부하로 막혔으면 `held`, 아니면 `idle`. `_set` 이 상태가 바뀔 때만 `_since` 를 되감으므로
        같은 이유로 계속 막혀 있는 동안 `held_since` 는 유지된다."""
        if hold is None:
            self._set(WORKER_IDLE)
            return
        with self._lock:
            if self._state != WORKER_HELD:
                self._since = self.now_fn()
            self._state = WORKER_HELD
            self._job_id = None
            self._error = None
            self._hold = hold

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
                now = self.now_fn()
                if self.admit is None:
                    current, hold = self.store.claim(self.lane, now), None
                else:
                    current, hold = self.admit(self.lane, now, partial(self._claim, now))
                if current is None:
                    self._set_hold(hold)
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
                    text, code, args = outcome.summary("worker_error", detail=str(err))
                    self.store.finish(
                        current.id,
                        FAILED,
                        now=self.now_fn(),
                        summary=text,
                        summary_code=code,
                        summary_args=args,
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

    def _collect_artifacts(
        self, job: Job, preset: Preset, workspace: Path, result: Any
    ) -> CollectResult | None:
        """산출물을 모아 `artifacts/<id>/bundle.tar` 에 설치한다. 없으면 None(행을 안 만든다).

        **오류는 여기서 잡는다.** 바깥 워커 예외 경로로 새면 잡이 실패하고 레인이 `down` 이 된다 —
        산출물 때문에 그러면 안 된다(명세 §5).
        """
        policy = artifact_policy(self.config, preset)
        # 예정 종료 상태로 판정한다 — 수집은 종료를 커밋하기 **전에** 일어난다(M5e §5).
        state = art.prospective_state(
            result.rc, cancelled=result.cancelled, timed_out=result.timed_out
        )
        if not policy.collects_for(state):
            return None
        data = self.config.data_dir
        staging = data / "artifacts" / ".staging" / f"{job.id}.{secrets.token_hex(8)}"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            self.store.start_collect(job.id, policy, self.now_fn())
            # 취소·타임아웃 뒤에는 짧은 예산만 쓴다 — 서버가 미확인 취소를 곧 닫는다(§5)
            forced = result.cancelled or result.timed_out
            out = collect(
                workspace,
                policy,
                staging,
                now_fn=self.now_fn,
                budget_seconds=policy.cancel_timeout_seconds if forced else None,
            )
            return self._install_bundle(job, out)
        except Exception as e:  # noqa: BLE001 — 수집 실패가 잡을 죽이지 않는다
            self._note(f"artifacts: job {job.id}: {type(e).__name__}")
            return CollectResult(
                state=art.FAILED,
                reason_code="collect_failed",
                detail={"error": type(e).__name__},
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _note(self, msg: str) -> None:
        """워커에는 로그 통로가 없다 — 산출물 사정은 잡의 묶음 행에 남는다(§3). 여기선 흘린다."""

    def _install_bundle(self, job: Job, out: CollectResult) -> CollectResult:
        """스테이징의 tar 를 `artifacts/<id>/` 로 옮긴다. 전체 상한을 넘으면 버린다(§8)."""
        if out.state != art.READY or out.bundle_path is None:
            return out
        limit = self.config.server.artifact_storage_max_bytes
        if not self.store.reserve_bundle_bytes(job.id, out.bundle_bytes, limit, self.now_fn()):
            return CollectResult(
                state=art.DROPPED,
                skipped_count=out.skipped_count,
                reason_code="storage_full",
                reason_args={"limit": limit},
            )
        dest = self.config.data_dir / "artifacts" / str(job.id)
        dest.mkdir(parents=True, exist_ok=True)
        out.bundle_path.replace(dest / "bundle.tar")
        return replace(out, bundle_path=dest / "bundle.tar")

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
            requires=tuple(preset.requires),
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
        except RequiredToolMissing as e:
            # 최종 환경에 도구가 없다 — 프로세스는 뜨지 않았다(M5j G4 · 결정 85). 서버가 만든
            # 코드라 라벨(`failed_step`·`last_step`)도 대장 행도 없다(M5h 불변식). 인자는 이름
            # 하나뿐이다 — PATH 와 경로는 상태에 싣지 않는다(PLAN 「보안」).
            text, code, args = outcome.summary("tool_missing", tool=e.public_name)
            self.store.finish(
                job.id,
                FAILED,
                now=self.now_fn(),
                exit_code=None,
                summary=text,
                summary_code=code,
                summary_args=args,
            )
            if not self.config.server.keep_workspace_on_failure:
                shutil.rmtree(workspace, ignore_errors=True)
            return
        except (MaterializeError, RunnerError) as e:
            self._fail(job, str(e))
            return
        # 워크스페이스를 지우기 **전에**, 그리고 종료를 커밋하기 **전에** 모은다(명세 §5).
        bundle = self._collect_artifacts(job, preset, workspace, result)
        markers = self.store.markers(job.id)
        job_now = self.store.get_job(job.id) or job
        # 수집하는 동안 수용된 취소가 이긴다 — `outcome_for` 도 `finish` 도 자동으로 안 해 준다.
        cancelled = result.cancelled or job_now.state == CANCELLING
        oc = outcome_for(
            job_now,
            markers,
            started=result.started,
            finished=result.finished,
            rc=result.rc,
            cancelled=cancelled,
            timed_out=result.timed_out,
            lost=result.lost,
        )
        self.store.finish(
            job.id,
            oc.state,
            now=result.finished,
            exit_code=result.rc,
            summary=oc.summary,
            summary_code=oc.code,
            summary_args=oc.args,
            failed_step=oc.failed_step,
            last_step=oc.last_step,
            fail_names=oc.fail_names,
            fail_truncated=oc.fail_truncated,
            bundle=bundle,
            ttl_hours=self.config.server.artifact_retention_hours,
        )
        # ── 정리 ──
        keep = oc.state != SUCCEEDED and self.config.server.keep_workspace_on_failure
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)


def artifact_policy(config: ServerConfig, preset: Preset) -> art.ArtifactPolicy:
    """그 잡에 얼리는 정책 — 프리셋의 글롭 + 서버의 잡당 상한(명세 §9)."""
    s = config.server
    return art.ArtifactPolicy(
        globs=tuple(preset.artifacts),
        max_bytes=s.max_artifact_bytes,
        max_files=s.max_artifact_files,
        timeout_seconds=s.artifact_timeout_seconds,
        cancel_timeout_seconds=s.artifact_cancel_timeout_seconds,
        collect_on=preset.artifacts_on,
    )


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
    admit: Callable[[int, datetime, Callable[[], Job | None]], Any] | None = None,
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
            admit=admit,
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
    "Outcome",
    "MAX_LINE_BYTES",
    "POLL_SECONDS",
    "READ_CHUNK",
]
