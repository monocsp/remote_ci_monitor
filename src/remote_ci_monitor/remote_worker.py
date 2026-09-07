"""원격 워커 프로세스(M5b-3) — `rcm worker --server URL --pool NAME --lanes N`.

루프:
- 등록(`/worker/register`; 서버에 못 닿으면 5초마다 재시도, 409 는 그대로 출력하고 종료 2).
- heartbeat 스레드: `heartbeat_seconds` 마다 `{jobs: [도는 잡…], host_sample}`. 응답 `cancel` 은
  레인에 전달, `paused` 면 claim 을 쉰다. 연속 실패는 경고 한 줄 — 잡은 계속 돈다(서버가 timeout
  뒤 lost 로 닫으면 finish 가 409 를 받고 워크스페이스만 정리한다).
- 레인 스레드 × lanes: claim(long-poll) → 스냅샷 받기(`extract_tree`) 또는 git_ref fetch →
  `runner.run_job`(관찰자가 phase·raw 로그를 HTTP 로) → finish. 보고 실패는 1·2·4초 재시도.
- SIGTERM/SIGINT: 레인의 `should_stop` → 도는 잡 SIGTERM → grace → KILL → `finish lost`
  (summary `worker stopped`) → 종료 0. 두 번째 신호는 즉시 종료.

서버에서 워커로 오는 연결은 없다(NAT 뒤에서도 된다). 로그·표본은 데이터로만 보낸다.
"""

from __future__ import annotations

import shutil
import socket
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from remote_ci_monitor import __version__
from remote_ci_monitor.client import ClientError, WorkerClient
from remote_ci_monitor.config import WorkerConfig
from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    LOST,
    MODE_GIT_REF,
    MODE_TREE,
    PHASE_MATERIALIZING,
    SUCCEEDED,
    TIMED_OUT,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.hostsample import HostSampler
from remote_ci_monitor.materialize import MaterializeError, extract_tree, prepare_git_ref
from remote_ci_monitor.runner import RunnerError, RunSpec, run_job
from remote_ci_monitor.worker import format_limit

REGISTER_RETRY_SECONDS = 5.0
REPORT_RETRIES = (1.0, 2.0, 4.0)
LOG_FLUSH_SECONDS = 1.0
LOG_BATCH_BYTES = 256 * 1024
WORKSPACE_KEEP_DAYS = 7
STOP_SUMMARY = "worker stopped"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkerExit(Exception):
    """치명적 — 메시지 그대로 출력하고 종료 코드로."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _fmt_seconds(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


class _RemoteObserver:
    """실행기 관찰자 — phase 는 즉시, 로그는 1초/256 KB 배치로 서버에. 마커는 서버가 파싱한다."""

    def __init__(self, proc: RemoteWorker, job_id: int):
        self.proc = proc
        self.job_id = job_id
        self._buf = bytearray()
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()
        self.closed_by_server = False  # 409: 서버가 이미 닫았다(lost 판정 등)

    def phase(self, phase: str) -> None:
        self.proc.report(
            lambda: self.proc.client.phase(self.job_id, phase), f"#{self.job_id} phase"
        )

    def output(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)
            due = (
                len(self._buf) >= LOG_BATCH_BYTES
                or time.monotonic() - self._last_flush >= LOG_FLUSH_SECONDS
            )
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buf:
                return
            chunk = bytes(self._buf)
            self._buf.clear()
            self._last_flush = time.monotonic()
        ok = self.proc.report(
            lambda: self.proc.client.log(self.job_id, chunk), f"#{self.job_id} log"
        )
        if ok is None:
            self.closed_by_server = True

    def should_cancel(self) -> bool:
        return self.job_id in self.proc.cancel_requested or self.closed_by_server

    def should_stop(self) -> bool:
        return self.proc.stopping.is_set()


class RemoteWorker:
    """워커 프로세스 본체. `run()` 이 블로킹으로 돈다."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        client: WorkerClient | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        log: Callable[[str], None] | None = None,
        environ: dict[str, str] | None = None,
        once: bool = False,
    ):
        self.config = config
        self.client = client or WorkerClient(config.server, config.token)
        self.now_fn = now_fn
        self.log = log or (lambda msg: print(f"[rcm worker] {msg}", file=sys.stderr, flush=True))
        self.environ = environ
        self.once = once
        self.stopping = threading.Event()
        self.paused = False
        self.cancel_requested: set[int] = set()
        self.running: dict[int, int] = {}  # job_id → lane
        self._lock = threading.Lock()
        self.heartbeat_seconds = 5
        self.claim_wait_seconds = 20
        self.timeout_seconds = 60
        self.name = config.name or socket.gethostname().split(".")[0]
        self.sampler: HostSampler | None = None
        self._threads: list[threading.Thread] = []
        self._hb_failures = 0
        self.processed = 0

    # ── 보고 (재시도) ────────────────────────────────────────────────────────

    def report(self, call: Callable[[], Any], what: str) -> Any:
        """서버에 보고. 연결 오류는 1·2·4초 재시도, 409(잡이 이미 닫힘)는 None, 다른 4xx 는 로그.
        반환: 응답(성공) · None(409) · False(포기)."""
        for i, delay in enumerate((*REPORT_RETRIES, None)):
            try:
                out = call()
                return {} if out is None else out
            except ClientError as e:
                if e.status == 409:
                    self.log(f"{what}: server says {e.message}")
                    return None
                if e.status and e.status != 503:
                    self.log(f"{what}: {e.message}")
                    return False
                if delay is None or self.stopping.is_set() and i >= 1:
                    self.log(f"{what}: giving up — {e.message}")
                    return False
                time.sleep(delay)
        return False

    # ── 등록 · heartbeat ─────────────────────────────────────────────────────

    def register(self) -> dict[str, Any]:
        while True:
            try:
                out = self.client.register(
                    pool=self.config.pool,
                    lanes=self.config.lanes,
                    host_name=self.name,
                    version=__version__,
                )
            except ClientError as e:
                if e.status == 403:
                    raise WorkerExit(
                        2, f"{e.message} (rcm token add NAME --worker on the server)"
                    ) from e
                if e.status == 401:
                    raise WorkerExit(2, f"{e.message} — set RCM_WORKER_TOKEN") from e
                if e.status:
                    raise WorkerExit(2, e.message) from e
                self.log(f"register: {e.message} — retrying in {int(REGISTER_RETRY_SECONDS)}s")
                if self.stopping.wait(REGISTER_RETRY_SECONDS):
                    raise WorkerExit(0, "stopped before registering") from e
                continue
            self.heartbeat_seconds = int(out.get("heartbeat_seconds") or 5)
            self.claim_wait_seconds = int(out.get("claim_wait_seconds") or 20)
            self.timeout_seconds = int(out.get("worker_timeout_seconds") or 60)
            self.name = str(out.get("name") or self.name)
            return out

    def _host_sample(self) -> dict[str, Any] | None:
        if self.sampler is None:
            return None
        try:
            hosts, _error = self.sampler.latest()
        except Exception:  # noqa: BLE001
            return None
        if not hosts:
            return None
        from remote_ci_monitor.core.status import host_json

        doc = host_json(hosts[0], now=self.now_fn())
        for key in ("name", "source", "sampled_at", "age_seconds", "stale"):
            doc.pop(key, None)
        return doc

    def heartbeat_once(self) -> bool:
        with self._lock:
            jobs = sorted(self.running)
        try:
            out = self.client.heartbeat(jobs, self._host_sample())
        except ClientError as e:
            self._hb_failures += 1
            if self._hb_failures in (3, 12) or self._hb_failures % 60 == 0:
                gone = self._hb_failures * self.heartbeat_seconds
                self.log(f"heartbeat: server unreachable for {gone}s ({e.message})")
            return False
        if self._hb_failures >= 3:
            self.log("heartbeat: server reachable again")
        self._hb_failures = 0
        cancel = out.get("cancel") or []
        with self._lock:
            self.cancel_requested = {int(j) for j in cancel if isinstance(j, int)}
            self.paused = bool(out.get("paused"))
        return True

    def _heartbeat_loop(self) -> None:
        while not self.stopping.wait(self.heartbeat_seconds):
            try:
                self.heartbeat_once()
            except Exception as e:  # noqa: BLE001 — heartbeat 이 죽으면 서버가 lost 로 본다
                self.log(f"heartbeat: {type(e).__name__}")

    # ── 레인 ────────────────────────────────────────────────────────────────

    def _lane_loop(self, lane: int) -> None:
        while not self.stopping.is_set():
            if self.paused:
                if self.stopping.wait(1.0):
                    break
                continue
            try:
                claimed = self.client.claim(lane, self.claim_wait_seconds)
            except ClientError as e:
                if e.status == 409 and "not registered" in e.message:
                    self.log(f"lane {lane}: {e.message} — registering again")
                    try:
                        self.register()
                    except WorkerExit as x:
                        self.log(x.message)
                        self.stopping.set()
                        break
                    continue
                if e.status == 0 or e.status == 503:
                    self.stopping.wait(2.0)
                    continue
                self.log(f"lane {lane}: claim: {e.message}")
                self.stopping.wait(2.0)
                continue
            if claimed is None:
                continue
            try:
                self.run_claimed(lane, claimed)
            except Exception as e:  # noqa: BLE001 — 잡 하나의 오류가 레인을 죽이면 안 된다
                self.log(f"lane {lane}: {type(e).__name__}: {str(e)[:120]}")
            self.processed += 1
            if self.once:
                self.stopping.set()
                break

    def _spec_from_claim(self, claimed: dict[str, Any], lane: int) -> tuple[RunSpec, Job]:
        job = claimed.get("job") or {}
        preset = claimed.get("preset") or {}
        job_id = int(job["id"])
        src = job.get("source") or {}
        source = Source(
            mode=src.get("mode", MODE_TREE),
            repo=src.get("repo"),
            base_sha=src.get("base_sha"),
            dirty=src.get("dirty"),
            tree_hash=src.get("tree_hash"),
            bytes=src.get("bytes"),
            ref=src.get("ref"),
            sha=src.get("sha"),
        )
        requester = job.get("requester") or {}
        data = self.config.data_path
        spec = RunSpec(
            job_id=job_id,
            preset_name=str(job.get("preset") or preset.get("name") or ""),
            argv=tuple(str(a) for a in (preset.get("argv") or [])),
            env={str(k): str(v) for k, v in (preset.get("env") or {}).items()},
            env_passthrough=tuple(str(k) for k in (preset.get("env_passthrough") or [])),
            timeout_seconds=job.get("timeout_seconds") or preset.get("timeout_seconds"),
            inputs=dict(job.get("inputs") or {}),
            requester_label=str(requester.get("label") or job.get("requested_by") or ""),
            source=source,
            workspace=data / "workspaces" / str(job_id),
            log_path=data / "jobs" / str(job_id) / "log.txt",
            grace_seconds=self.config.grace_seconds,
        )
        model = Job(
            id=job_id,
            preset=spec.preset_name,
            inputs=spec.inputs,
            key="",
            concurrency_group=job.get("concurrency_group"),
            source=source,
            requester=Requester(name=str(requester.get("name") or ""), label=spec.requester_label),
            state=str(job.get("state") or "running"),
            created_at=self.now_fn(),
            timeout_seconds=spec.timeout_seconds,
            lane=lane,
        )
        return spec, model

    def _materialize(self, spec: RunSpec, job: Job) -> None:
        if spec.workspace.exists():
            shutil.rmtree(spec.workspace, ignore_errors=True)
        if job.source.mode == MODE_GIT_REF:
            repo = self.config.repo(job.source.repo)
            if repo is None:
                raise MaterializeError(
                    f"repo '{job.source.repo or '?'}' is not configured on this worker"
                )
            spec.workspace.parent.mkdir(parents=True, exist_ok=True)
            spec.log_path.parent.mkdir(parents=True, exist_ok=True)

            def _log(line: str) -> None:
                try:
                    with spec.log_path.open("ab") as fh:
                        fh.write(line.encode("utf-8", errors="replace") + b"\n")
                except OSError:
                    pass

            prepare_git_ref(
                job,
                spec.workspace,
                repo_name=repo.name,
                repo_url=repo.url,
                mirror=self.config.data_path / "mirrors" / repo.name,
                timeout=self.config.git_fetch_timeout_seconds,
                log=_log,
            )
            return
        tar_path = self.config.data_path / "jobs" / str(job.id) / "tree.tar.gz"
        try:
            self.client.download_tree(job.id, tar_path)
        except ClientError as e:
            raise MaterializeError(f"cannot download snapshot: {e.message}") from e
        try:
            extract_tree(tar_path, spec.workspace)
        finally:
            tar_path.unlink(missing_ok=True)

    def run_claimed(self, lane: int, claimed: dict[str, Any]) -> None:
        spec, job = self._spec_from_claim(claimed, lane)
        with self._lock:
            self.running[job.id] = lane
        self.log(f"lane {lane}: claimed #{job.id} {job.preset}")
        observer = _RemoteObserver(self, job.id)
        started = time.monotonic()
        outcome = FAILED
        rc: int | None = None
        summary: str | None = None
        try:
            if not spec.argv:
                raise RunnerError("preset has no argv (server sent no preset)")
            self.report(lambda: self.client.phase(job.id, PHASE_MATERIALIZING), f"#{job.id} phase")
            result = run_job(
                spec,
                observer,
                now_fn=self.now_fn,
                environ=self.environ,
                materialize=lambda s: self._materialize(s, job),
            )
        except (MaterializeError, RunnerError) as e:
            observer.flush()
            summary = str(e)[:200]
            self._finish(job.id, FAILED, None, summary, observer)
            self.log(f"lane {lane}: #{job.id} failed — {summary}")
            self._cleanup(spec, failed=True)
            return
        observer.flush()
        rc = result.rc
        if result.lost:
            outcome, summary = LOST, STOP_SUMMARY
        elif result.cancelled:
            outcome = CANCELLED
        elif result.timed_out:
            outcome, summary = TIMED_OUT, format_limit(spec.timeout_seconds)
        elif rc == 0:
            outcome = SUCCEEDED
        else:
            outcome = FAILED
        self._finish(job.id, outcome, rc, summary, observer)
        self.log(f"lane {lane}: #{job.id} {outcome} in {_fmt_seconds(time.monotonic() - started)}")
        self._cleanup(spec, failed=outcome != SUCCEEDED)

    def _finish(
        self, job_id: int, outcome: str, rc: int | None, summary: str | None, observer: Any
    ) -> None:
        with self._lock:
            self.running.pop(job_id, None)
            self.cancel_requested.discard(job_id)
        if getattr(observer, "closed_by_server", False):
            return
        self.report(lambda: self.client.finish(job_id, outcome, rc, summary), f"#{job_id} finish")

    def _cleanup(self, spec: RunSpec, *, failed: bool) -> None:
        keep = failed and self.config.keep_workspace_on_failure
        if not keep:
            shutil.rmtree(spec.workspace, ignore_errors=True)

    def _sweep_workspaces(self) -> None:
        """시작 시 `WORKSPACE_KEEP_DAYS` 넘은 워크스페이스·잡 디렉터리를 지운다."""
        cutoff = time.time() - WORKSPACE_KEEP_DAYS * 86400
        for base in (self.config.data_path / "workspaces", self.config.data_path / "jobs"):
            if not base.is_dir():
                continue
            for entry in base.iterdir():
                try:
                    if entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                except OSError:
                    continue

    # ── 수명 ────────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> int:
        """블로킹. 등록 → 스레드 → 정지까지. 종료 코드."""
        self.config.data_path.mkdir(parents=True, exist_ok=True)
        self._sweep_workspaces()
        out = self.register()
        self.log(
            f"rcm worker {self.name} · pool {out.get('pool')} · lanes {out.get('lanes')} · "
            f"server {self.client.server} · heartbeat {self.heartbeat_seconds}s"
        )
        self.sampler = HostSampler(
            self.config.host,
            name=self.name,
            publish=lambda kind, data: None,
            stop=self.stopping,
            now_fn=self.now_fn,
        )
        self.sampler.start()
        self.heartbeat_once()
        hb = threading.Thread(target=self._heartbeat_loop, name="rcm-worker-heartbeat", daemon=True)
        hb.start()
        self._threads = [
            threading.Thread(target=self._lane_loop, args=(lane,), name=f"rcm-worker-lane-{lane}")
            for lane in range(1, self.config.lanes + 1)
        ]
        for t in self._threads:
            t.start()
        try:
            while any(t.is_alive() for t in self._threads):
                for t in self._threads:
                    t.join(timeout=0.5)
        finally:
            self.stopping.set()
            for t in self._threads:
                t.join(timeout=self.config.grace_seconds + 10)
            hb.join(timeout=2)
            self.log("stopped")
        return 0


__all__ = ["RemoteWorker", "WorkerExit", "STOP_SUMMARY"]
