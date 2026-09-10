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

import dataclasses
import random
import secrets
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
from remote_ci_monitor.collect import CollectResult, collect
from remote_ci_monitor.config import WorkerConfig
from remote_ci_monitor.core import artifacts as art
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
from remote_ci_monitor.core.outcome import summary as outcome_summary
from remote_ci_monitor.hostsample import HostSampler
from remote_ci_monitor.materialize import MaterializeError, extract_tree, prepare_git_ref
from remote_ci_monitor.runner import RequiredToolMissing, RunnerError, RunSpec, run_job
from remote_ci_monitor.worker import format_limit

REGISTER_RETRY_SECONDS = 5.0
CLAIM_MIN_INTERVAL = 1.0  # 빈 204 가 이보다 빨리 오면 이만큼 쉰다
RETRY_WAIT_SECONDS = 2.0  # 503 · 연결 실패 뒤 다시 claim 하기까지
REPORT_RETRIES = (1.0, 2.0, 4.0)
LOG_FLUSH_SECONDS = 1.0
LOG_BATCH_BYTES = 256 * 1024
LOG_KEEP_BYTES = 4 * 1024 * 1024  # 서버에 못 보낸 로그를 메모리에 두는 상한
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
        self._pending_failures = 0
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
        """로그 배치를 한 번만 보낸다 — 펌프(취소·정지 감지)를 재시도로 막지 않는다. 서버에 못
        닿으면 되돌려 두고 다음 배치와 함께 다시 보낸다(`LOG_KEEP_BYTES` 까지; 넘치면 오래된 쪽을
        버린다 — 워커 로컬 `log.txt` 에는 남는다). 409 는 서버가 잡을 닫은 것."""
        with self._lock:
            if not self._buf:
                return
            chunk = bytes(self._buf)
            self._buf.clear()
            self._last_flush = time.monotonic()
        try:
            self.proc.client.log(self.job_id, chunk)
        except ClientError as e:
            if e.status == 409:
                self.proc.log(f"#{self.job_id} log: server says {e.message}")
                self.closed_by_server = True
                return
            if e.status and e.status != 503:
                self.proc.log(f"#{self.job_id} log: {e.message}")
                return
            with self._lock:
                self._buf[:0] = chunk  # 되돌린다
                if len(self._buf) > LOG_KEEP_BYTES:
                    del self._buf[: len(self._buf) - LOG_KEEP_BYTES]
                self._pending_failures += 1
            if self._pending_failures in (1, 10) or self._pending_failures % 60 == 0:
                self.proc.log(f"#{self.job_id} log: {e.message} — keeping {len(self._buf)} bytes")
            return
        self._pending_failures = 0

    def final_flush(self) -> None:
        """종료 직전 — 남은 로그는 재시도까지 해서 보낸다(그 뒤 finish 가 간다)."""
        self.flush()
        with self._lock:
            chunk = bytes(self._buf)
            self._buf.clear()
        if chunk and not self.closed_by_server:
            ok = self.proc.report(
                lambda: self.proc.client.log(self.job_id, chunk), f"#{self.job_id} log"
            )
            if ok is None:
                self.closed_by_server = True

    def should_cancel(self) -> bool:
        return self.job_id in self.proc.cancel_requested or self.closed_by_server

    def should_stop(self) -> bool:
        return self.proc.stopping.is_set()


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _policy_from_claim(claimed: dict[str, Any]) -> art.ArtifactPolicy | None:
    """claim 응답에 실려 온 **얼린 정책**(§9). 서버가 안 보냈으면 산출물을 안 모은다."""
    raw = claimed.get("artifacts")
    if not isinstance(raw, dict):
        preset = claimed.get("preset")
        globs = preset.get("artifacts") if isinstance(preset, dict) else None
        if not isinstance(globs, list) or not globs:
            return None
        raw = {"globs": globs}
    globs = raw.get("globs")
    if not isinstance(globs, list) or not globs:
        return None
    defaults = art.ArtifactPolicy()
    return art.ArtifactPolicy(
        globs=tuple(str(g) for g in globs),
        max_bytes=int(raw.get("max_bytes") or defaults.max_bytes),
        max_files=int(raw.get("max_files") or defaults.max_files),
        timeout_seconds=int(raw.get("timeout_seconds") or defaults.timeout_seconds),
        cancel_timeout_seconds=int(
            raw.get("cancel_timeout_seconds") or defaults.cancel_timeout_seconds
        ),
        # 필드가 없는 옛 서버는 `"always"` — **옛 동작이 기본**이다. 조용히 안 모으게 만들지 않는다.
        collect_on=str(raw.get("collect_on") or defaults.collect_on),
    )


def _disposition(bundle: CollectResult | None) -> dict[str, Any] | None:
    """`finish` 에 싣는 구조화된 처분(§5). 해시 하나로는 빈 수집도 시한 초과도 말할 수 없다.

    `None` 이면 필드를 아예 안 보낸다 — 그게 `unknown` 이고 `empty` 와 다르다.
    """
    if bundle is None:
        return None
    return {
        "state": bundle.state,
        "bundle_sha256": bundle.bundle_sha256,
        "file_count": len(bundle.files) if bundle.files else 0,
        "total_bytes": bundle.total_bytes,
        "skipped_count": bundle.skipped_count,
        "reason_code": bundle.reason_code,
        "reason_args": bundle.reason_args,
    }


class RemoteWorker:
    """워커 프로세스 본체. `run()` 이 블로킹으로 돈다."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        client: WorkerClient | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
        rand_fn: Callable[[], float] = random.random,  # 지터 — 테스트가 주입한다
        log: Callable[[str], None] | None = None,
        environ: dict[str, str] | None = None,
        once: bool = False,
    ):
        self.config = config
        self.client = client or WorkerClient(config.server, config.token)
        self.now_fn = now_fn
        self.rand_fn = rand_fn
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
        # 낡은 표본은 **아예 안 보낸다**(M5f §3.1). 서버는 받은 시각으로 sampled_at 을 다시
        # 찍으므로, 샘플러가 죽어도 heartbeat 만 살아 있으면 굳은 표본을 영원히 「새것」으로
        # 본다 — 부하 게이트가 못 보면서 열리는 fail-open 구멍이다. 안 보내면 서버가 든
        # 표본이 늙어 게이트가 닫힌다.
        if doc.get("stale"):
            return None
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

    def _backoff(self, base: float) -> float:
        """`[base, 2 × base)` 의 대기. **하한을 낮추지 않는다** — `uniform(0, base)` 로 하면 뜨거운
        루프가 된다.

        지터가 없으면 빈 204 를 받은 레인들이 같은 1초 격자에 묶여 영영 안 흩어진다. 실측: 같은
        격자 32레인이면 요청 세마포어(`max_concurrent_requests`)가 정확히 가득 차고, 48레인에서
        첫 `/api/status` 503 이 난다(M5f §15-C4).
        """
        return base + self.rand_fn() * base

    def _lane_loop(self, lane: int) -> None:
        while not self.stopping.is_set():
            if self.paused:
                if self.stopping.wait(1.0):
                    break
                continue
            asked_at = time.monotonic()
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
                    self.stopping.wait(self._backoff(RETRY_WAIT_SECONDS))
                    continue
                self.log(f"lane {lane}: claim: {e.message}")
                self.stopping.wait(self._backoff(RETRY_WAIT_SECONDS))
                continue
            if claimed is None:
                # 서버가 기다리지 않고 204 를 줬다(wait 0 · long-poll 슬롯 소진) — 뜨거운 루프 금지
                if time.monotonic() - asked_at < CLAIM_MIN_INTERVAL:
                    self.stopping.wait(self._backoff(CLAIM_MIN_INTERVAL))
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
            requires=tuple(str(k) for k in (preset.get("requires") or [])),
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
        except RequiredToolMissing as e:
            observer.final_flush()
            # 최종 환경에 도구가 없다 — 프로세스는 뜨지 않았다. 구조화된 코드로 보고한다(M5j G4);
            # 서버가 로컬 워커와 같은 `tool_missing` 을 남긴다. PATH 는 어디에도 싣지 않는다.
            summary, code, args = outcome_summary("tool_missing", tool=e.public_name)
            skipped = CollectResult(state=art.SKIPPED, reason_code="not_run")
            self._finish(
                job.id,
                FAILED,
                None,
                summary,
                observer,
                artifacts=_disposition(skipped),
                summary_code=code,
                summary_args=args,
            )
            self.log(f"lane {lane}: #{job.id} failed — {summary}")
            self._cleanup(spec, failed=True)
            return
        except (MaterializeError, RunnerError) as e:
            observer.final_flush()
            summary = str(e)[:200]
            # 실행이 시작되지 못했다 — 조용히 비우지 않고 그 사실을 처분으로 남긴다(§5)
            skipped = CollectResult(state=art.SKIPPED, reason_code="not_run")
            self._finish(job.id, FAILED, None, summary, observer, artifacts=_disposition(skipped))
            self.log(f"lane {lane}: #{job.id} failed — {summary}")
            self._cleanup(spec, failed=True)
            return
        observer.final_flush()
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
        # 워크스페이스를 지우기 전에 모으고, `self.running` 에서 빠지기 전에 올린다(§5)
        bundle = self._collect_artifacts(job.id, claimed, spec, result)
        self._finish(
            job.id, outcome, rc, summary, observer, artifacts=_disposition(bundle), finished=result
        )
        self.log(f"lane {lane}: #{job.id} {outcome} in {_fmt_seconds(time.monotonic() - started)}")
        self._cleanup(spec, failed=outcome != SUCCEEDED)

    def _collect_artifacts(
        self, job_id: int, claimed: dict[str, Any], spec: RunSpec, result: Any
    ) -> CollectResult | None:
        """모아서 서버에 올린다. 오류는 여기서 잡는다 — 산출물 때문에 잡이 죽지 않는다(§5)."""
        policy = _policy_from_claim(claimed)
        state = art.prospective_state(
            result.rc, cancelled=result.cancelled, timed_out=result.timed_out
        )
        if policy is None or not policy.collects_for(state):
            return None
        # 스풀은 **지우지 않는다.** 전송 결과가 불확실하면 다시 보낼 수 있어야 한다(§5).
        # 무한정 쌓이지 않게 시작할 때 `_sweep_workspaces` 가 나이로 쓸어 간다.
        staging = self.config.data_path / "artifacts" / f"{job_id}.{secrets.token_hex(8)}"
        try:
            staging.mkdir(parents=True, exist_ok=True)
            forced = result.cancelled or result.timed_out
            out = collect(
                spec.workspace,
                policy,
                staging,
                now_fn=self.now_fn,
                budget_seconds=policy.cancel_timeout_seconds if forced else None,
            )
            if out.state != art.READY or out.bundle_path is None:
                return out
            receipt = self.client.upload_bundle(job_id, out.bundle_path)
            return dataclasses.replace(out, bundle_sha256=receipt.get("bundle_sha256"))
        except Exception as e:  # noqa: BLE001 — 수집·업로드 실패가 잡을 죽이지 않는다
            self.log(f"lane: #{job_id} artifacts: {type(e).__name__}")
            return CollectResult(state=art.FAILED, reason_code="upload_failed")

    def _finish(
        self,
        job_id: int,
        outcome: str,
        rc: int | None,
        summary: str | None,
        observer: Any,
        *,
        artifacts: dict[str, Any] | None = None,
        finished: Any = None,
        summary_code: str | None = None,
        summary_args: dict[str, Any] | None = None,
    ) -> None:
        """완료를 보고하고 **그 뒤에** heartbeat 목록에서 뺀다(§5).

        먼저 빼면 그사이의 heartbeat 에 잡이 안 실려 서버가 `lost` 로 닫는다 — 수집·업로드가
        길어질수록 그 창이 넓어진다.
        """
        with self._lock:
            self.cancel_requested.discard(job_id)
        if not getattr(observer, "closed_by_server", False):
            extra: dict[str, Any] = {}
            if artifacts is not None:
                extra["artifacts"] = artifacts
            if finished is not None and getattr(finished, "finished", None) is not None:
                # 실행 종료 시각 — 수집·업로드 시간이 job_seconds 에 섞이지 않게(§10)
                extra["finished_at"] = _iso(finished.finished)
            if summary_code is not None:
                extra["summary_code"] = summary_code
                extra["summary_args"] = dict(summary_args or {})
            self.report(
                lambda: self.client.finish(job_id, outcome, rc, summary, **extra),
                f"#{job_id} finish",
            )
        with self._lock:
            self.running.pop(job_id, None)

    def _cleanup(self, spec: RunSpec, *, failed: bool) -> None:
        keep = failed and self.config.keep_workspace_on_failure
        if not keep:
            shutil.rmtree(spec.workspace, ignore_errors=True)

    def _sweep_workspaces(self) -> None:
        """시작 시 `WORKSPACE_KEEP_DAYS` 넘은 워크스페이스·잡 디렉터리를 지운다."""
        cutoff = time.time() - WORKSPACE_KEEP_DAYS * 86400
        for base in (
            self.config.data_path / "workspaces",
            self.config.data_path / "jobs",
            self.config.data_path / "artifacts",  # 올리고 남긴 묶음 스풀(M5e)
        ):
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
            disk_path=str(self.config.data_path),
        )
        self.sampler.start()
        self.heartbeat_once()
        hb = threading.Thread(target=self._heartbeat_loop, name="rcm-worker-heartbeat", daemon=True)
        hb.start()
        self._threads = [
            threading.Thread(
                target=self._lane_loop, args=(lane,), name=f"rcm-worker-lane-{lane}", daemon=True
            )
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
