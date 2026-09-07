"""원격 워커 프로토콜(M5b-2) — `/worker/register · claim · heartbeat` 와
`/worker/jobs/{id}/tree|phase|log|finish`.

서버 쪽만이다(워커 프로세스 `rcm worker` 는 M5b-3). `App` 이 이 믹스인을 상속해 규칙을 갖고,
HTTP 핸들러는 얇게 여기를 부른다.

규칙(docs/m5b2-workplan.md):
- 워커 토큰(`kind = worker`)만 `/worker/*` 를 쓴다. 워커 이름 = 토큰 이름.
- 워커 상태는 **서버가 받은 시각** `last_seen_at` 로만: `now − last_seen_at` 이
  `worker_timeout_seconds` 를 넘으면 down 이고 그 워커의 running·cancelling 잡은 lost.
  워커 payload 의 시각은 어디에도 안 쓴다.
- 레인 상태의 정본은 DB(그 워커의 running 잡 `lane`). heartbeat 의 `jobs` 목록은 조정용 —
  서버가 아는 running 잡이 목록에 없으면 워커가 잊은 잡이라 lost.
- `register` 는 새 프로세스라는 뜻 — 그 이름의 활성 잡을 먼저 lost 로 닫는다.
- 워커는 자기가 claim 한 잡만 보고한다(`jobs.worker_name`). 종료된 잡에 대한 늦은 보고는 409 로
  거절하고 아무것도 바꾸지 않는다.
- 취소: 워커가 heartbeat 은 계속 보내면서 `kill_at + 2 × heartbeat` 가 지나도 finish 를 안 하면
  서버가 cancelled 로 닫는다. 워커가 닿지 않으면 lost 가 우선한다.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from remote_ci_monitor.config import _NAME_RE
from remote_ci_monitor.core.hostparse import sample_from_json
from remote_ci_monitor.core.model import (
    BUSY_STATES,
    CANCELLED,
    CANCELLING,
    DEFAULT_POOL,
    FAILED,
    LOST,
    MODE_GIT_REF,
    PHASE_EXECUTING,
    PHASE_MATERIALIZING,
    SUCCEEDED,
    TIMED_OUT,
    TOKEN_WORKER,
    WORKER_BUSY,
    WORKER_DOWN,
    WORKER_IDLE,
    HostSample,
    Job,
    WorkerInfo,
)
from remote_ci_monitor.core.progress import parse_marker
from remote_ci_monitor.core.status import source_json
from remote_ci_monitor.materialize import MaterializeError, assemble_tar_from_manifest
from remote_ci_monitor.store import LaneBusy, TokenInfo, WorkerRow

if TYPE_CHECKING:
    from remote_ci_monitor.config import ServerConfig
    from remote_ci_monitor.store import Store

CLAIM_WAIT_SLOTS = 8  # 동시에 long-poll 로 기다리는 claim 수. 넘치면 바로 204
CLAIM_POLL_SECONDS = 0.5
MAX_WORKER_LOG_BODY = 4 * 1024 * 1024
MAX_WORKER_LANES = 64
WORKER_PHASES = (PHASE_MATERIALIZING, PHASE_EXECUTING)
WORKER_OUTCOMES = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST)
SUMMARY_RESTARTED = "worker {name} restarted without the job"
SUMMARY_UNREACHABLE = "worker {name} unreachable for {seconds}s"
SUMMARY_CANCEL_UNCONFIRMED = "worker did not confirm the cancel"


def _api_error(status: int, message: str, **extra: Any) -> Exception:
    from remote_ci_monitor.server import ApiError  # 순환 import 를 피한다

    return ApiError(status, message, **extra)


def _int_field(body: dict[str, Any], key: str, lo: int, hi: int, *, default: int | None = None):
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise _api_error(400, f"{key} must be an integer")
    if not lo <= v <= hi:
        raise _api_error(400, f"{key} must be between {lo} and {hi}")
    return v


class RemoteWorkersMixin:
    """`App` 에 섞이는 원격 워커 규칙. 아래 속성은 `App` 이 준다."""

    store: Store
    config: ServerConfig
    version: str
    wake: threading.Event
    stop: threading.Event

    # App 이 정의하는 메서드(타입 힌트용)
    def now_fn(self) -> datetime: ...  # type: ignore[empty-body]
    def job_dir(self, job_id: int) -> Path: ...  # type: ignore[empty-body]
    def log_path(self, job_id: int) -> Path: ...  # type: ignore[empty-body]
    def log(self, msg: str) -> None: ...
    def _publish_job(self, job: Job | None, job_id: int) -> None: ...
    def _publish_server(self) -> None: ...
    def _mark_dirty(self) -> None: ...
    def _on_marker(self, job_id: int, kind: str, value: str) -> None: ...

    def _remote_init(self) -> None:
        self._claim_slots = threading.BoundedSemaphore(CLAIM_WAIT_SLOTS)
        self._worker_samples: dict[str, HostSample] = {}
        self._log_partial: dict[int, bytes] = {}
        self._remote_lock = threading.Lock()

    # ── 인증 ────────────────────────────────────────────────────────────────

    def require_worker_token(self, token: TokenInfo | None) -> TokenInfo:
        if token is None:
            raise _api_error(401, "a valid worker token is required")
        if token.kind != TOKEN_WORKER:
            raise _api_error(403, "worker token required")
        return token

    # ── 워커 상태 (DB 로 계산) ───────────────────────────────────────────────

    def worker_alive(self, row: WorkerRow, now: datetime) -> bool:
        timeout = self.config.server.worker_timeout_seconds
        return (now - row.last_seen_at).total_seconds() <= timeout

    def _workers(self) -> list[WorkerRow]:
        try:
            return self.store.list_workers()
        except Exception:  # noqa: BLE001 — 표가 없거나 DB 오류면 원격 워커는 없는 셈
            return []

    def remote_worker_infos(self, pool: str | None, now: datetime) -> list[WorkerInfo]:
        """그 풀(None 이면 전부)의 원격 레인. busy 는 DB 의 running·cancelling 잡, 나머지 idle,
        heartbeat 이 오래됐으면 전부 down. 워커 이름순 · 레인순."""
        infos: list[WorkerInfo] = []
        for row in self._workers():
            if pool is not None and row.pool != pool:
                continue
            alive = self.worker_alive(row, now)
            busy: dict[int, Job] = {}
            if alive:
                for job in self.store.jobs_of_worker(row.name):
                    if job.lane is not None:
                        busy[job.lane] = job
            for lane in range(1, row.lanes + 1):
                job = busy.get(lane)
                if not alive:
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_DOWN,
                            error="no heartbeat",
                            since=row.last_seen_at,
                            worker=row.name,
                        )
                    )
                elif job is not None:
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_BUSY,
                            job_id=job.id,
                            since=job.started_at,
                            worker=row.name,
                        )
                    )
                else:
                    infos.append(
                        WorkerInfo(
                            lane=lane, state=WORKER_IDLE, since=row.registered_at, worker=row.name
                        )
                    )
        return infos

    def remote_lanes(self, pool: str, now: datetime) -> int:
        """살아 있는 워커의 레인 합."""
        return sum(r.lanes for r in self._workers() if r.pool == pool and self.worker_alive(r, now))

    def remote_hosts(self, pool: str, now: datetime) -> tuple[HostSample, ...]:
        """살아 있는 워커의 마지막 heartbeat 표본(이름순). 없으면 `()` — 「표본 없음」이지 실패가
        아니다."""
        out: list[HostSample] = []
        with self._remote_lock:
            samples = dict(self._worker_samples)
        for row in self._workers():
            if row.pool != pool or not self.worker_alive(row, now):
                continue
            sample = samples.get(row.name)
            if sample is not None:
                out.append(sample)
        return tuple(out)

    def pools_without_workers(self, now: datetime) -> list[str]:
        """등록된 워커가 있는데 전부 down 인 풀(기본 풀은 로컬 레인이 있으니 제외)."""
        seen: dict[str, bool] = {}
        for row in self._workers():
            if row.pool == DEFAULT_POOL:
                continue
            seen[row.pool] = seen.get(row.pool, False) or self.worker_alive(row, now)
        return sorted(name for name, alive in seen.items() if not alive)

    # ── 등록 ────────────────────────────────────────────────────────────────

    def worker_register(self, token: TokenInfo, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        pool = body.get("pool", DEFAULT_POOL)
        if not isinstance(pool, str) or not _NAME_RE.match(pool):
            raise _api_error(400, "pool must be a name (letters, digits, . _ -)")
        lanes = _int_field(body, "lanes", 1, MAX_WORKER_LANES, default=1)
        host_name = body.get("host_name")
        if host_name is not None and not isinstance(host_name, str):
            raise _api_error(400, "host_name must be a string")
        version = body.get("version")
        if version is not None and not isinstance(version, str):
            raise _api_error(400, "version must be a string")
        if version != self.version:
            raise _api_error(
                409,
                f"worker version {version or '?'}, server {self.version} — "
                "install the same release",
            )
        now = self.now_fn()
        # 등록 = 새 프로세스. 옛 프로세스가 잡고 있던 잡은 아무도 이어 받지 않는다(재현성)
        lost = self.store.mark_lost_for_worker(
            token.name, now, SUMMARY_RESTARTED.format(name=token.name)
        )
        for job_id in lost:
            self._publish_job(None, job_id)
        if lost:
            self.log(f"worker {token.name} re-registered: lost={lost}")
        s = self.config.server
        row = self.store.register_worker(
            token.name,
            pool=pool,
            lanes=lanes,
            host_name=host_name[:200] if host_name else None,
            version=version,
            now=now,
        )
        self._mark_dirty()
        self._publish_server()
        return {
            "name": row.name,
            "pool": row.pool,
            "lanes": row.lanes,
            "heartbeat_seconds": s.worker_heartbeat_seconds,
            "worker_timeout_seconds": s.worker_timeout_seconds,
            "claim_wait_seconds": s.worker_claim_wait_seconds,
        }

    def _registered(self, token: TokenInfo) -> WorkerRow:
        row = self.store.get_worker(token.name)
        if row is None:
            raise _api_error(409, f"worker {token.name} is not registered")
        return row

    # ── claim ───────────────────────────────────────────────────────────────

    def _claim_payload(self, job: Job) -> dict[str, Any]:
        preset = self.config.preset(job.preset)
        preset_doc: dict[str, Any] | None = None
        if preset is not None:
            preset_doc = {
                "name": preset.name,
                "argv": list(preset.argv),
                "timeout_seconds": preset.timeout_seconds,
                "env": dict(preset.env),
                "env_passthrough": list(preset.env_passthrough),
                "source_modes": list(preset.source_modes),
                "repo": preset.repo or None,
            }
        return {
            "job": {
                "id": job.id,
                "preset": job.preset,
                "pool": job.pool,
                "priority": job.priority,
                "source": source_json(job.source),
                "inputs": dict(job.inputs),
                "requested_by": job.requester.label,
                "group": job.concurrency_group,
                "timeout_seconds": job.timeout_seconds,
                "lane": job.lane,
            },
            "tree_url": f"/worker/jobs/{job.id}/tree",
            "preset": preset_doc,
        }

    def worker_claim(self, token: TokenInfo, body: Any) -> dict[str, Any] | None:
        """잡이 있으면 payload, 없으면 None(204). 기다림은 `claim_wait_seconds` 상한."""
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        row = self._registered(token)
        lane = _int_field(body, "lane", 1, row.lanes, default=1)
        s = self.config.server
        wait = _int_field(body, "wait_seconds", 0, 60, default=s.worker_claim_wait_seconds)
        wait = min(wait, s.worker_claim_wait_seconds)
        now = self.now_fn()
        self.store.touch_worker(token.name, now)
        job = self._try_claim(token.name, row.pool, lane, now)
        if job is not None:
            return self._claim_payload(job)
        if wait <= 0 or not self._claim_slots.acquire(blocking=False):
            return None
        try:
            deadline = now + timedelta(seconds=wait)
            while not self.stop.is_set():
                self.wake.wait(CLAIM_POLL_SECONDS)
                now = self.now_fn()
                job = self._try_claim(token.name, row.pool, lane, now)
                if job is not None:
                    return self._claim_payload(job)
                if now >= deadline:
                    break
        finally:
            self._claim_slots.release()
        return None

    def _try_claim(self, name: str, pool: str, lane: int, now: datetime) -> Job | None:
        if self.store.get_paused() is not None:
            return None
        try:
            job = self.store.claim(lane, now, pool=pool, worker_name=name)
        except LaneBusy as e:
            raise _api_error(409, str(e)) from e
        if job is not None:
            self._publish_job(job, job.id)
            self._publish_server()
        return job

    # ── 잡 보고 ─────────────────────────────────────────────────────────────

    def _owned_active(self, token: TokenInfo, job_id: int) -> Job:
        """이 워커가 claim 한 활성 잡. 남의 잡 403 · 종료 잡 409(늦은 보고는 무시)."""
        job = self.store.get_job(job_id)
        if job is None:
            raise _api_error(404, "no such job")
        if job.worker_name != token.name:
            raise _api_error(403, "not your job")
        if job.state not in BUSY_STATES:
            raise _api_error(409, f"job #{job.id} is {job.state}", state=job.state)
        return job

    def worker_tree_path(self, token: TokenInfo, job_id: int) -> Path:
        job = self._owned_active(token, job_id)
        if job.source.mode == MODE_GIT_REF:
            raise _api_error(404, "git_ref jobs are fetched by the worker")
        job_dir = self.job_dir(job.id)
        tar_path = job_dir / "tree.tar.gz"
        if tar_path.is_file():
            return tar_path
        manifest = job_dir / "manifest.json"
        if manifest.is_file():
            try:
                assemble_tar_from_manifest(manifest, self.config.data_dir / "blobs", tar_path)
            except MaterializeError as e:
                raise _api_error(409, str(e)) from e
            return tar_path
        raise _api_error(404, "snapshot file is missing")

    def worker_phase(self, token: TokenInfo, job_id: int, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        job = self._owned_active(token, job_id)
        phase = body.get("phase")
        if phase not in WORKER_PHASES:
            raise _api_error(400, f"phase must be one of {', '.join(WORKER_PHASES)}")
        now = self.now_fn()
        self.store.set_phase(job.id, phase)
        if phase == PHASE_EXECUTING:
            self.store.set_last_output(job.id, now)
        self._publish_job(None, job.id)
        return {"job_id": job.id, "phase": phase}

    def worker_log(self, token: TokenInfo, job_id: int, data: bytes) -> dict[str, Any]:
        """raw 바이트를 `log.txt` 에 붙이고 줄 단위로 마커를 파싱한다. 잘린 마지막 줄은 다음
        요청과 이어 붙인다."""
        job = self._owned_active(token, job_id)
        now = self.now_fn()
        path = self.log_path(job.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as fh:
            fh.write(data)
        with self._remote_lock:
            buf = self._log_partial.pop(job.id, b"") + data
            *lines, rest = buf.split(b"\n")
            if rest:
                self._log_partial[job.id] = rest[-4096:]
        markers = 0
        for raw in lines:
            parsed = parse_marker(raw.decode("utf-8", errors="replace"))
            if parsed is None:
                continue
            self.store.add_marker(job.id, parsed[0], parsed[1], now)
            self._on_marker(job.id, parsed[0], parsed[1])
            markers += 1
        if data:
            self.store.set_last_output(job.id, now)
            self._mark_dirty()
        return {"job_id": job.id, "bytes": len(data), "markers": markers}

    def worker_finish(self, token: TokenInfo, job_id: int, body: Any) -> dict[str, Any]:
        from remote_ci_monitor.worker import outcome_for

        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        job = self._owned_active(token, job_id)
        outcome = body.get("outcome")
        if outcome not in WORKER_OUTCOMES:
            raise _api_error(400, f"outcome must be one of {', '.join(WORKER_OUTCOMES)}")
        rc = body.get("exit_code")
        if rc is not None and (isinstance(rc, bool) or not isinstance(rc, int)):
            raise _api_error(400, "exit_code must be an integer or null")
        given = body.get("summary")
        if given is not None and not isinstance(given, str):
            raise _api_error(400, "summary must be a string")
        now = self.now_fn()
        markers = self.store.markers(job.id)
        state, summary, failed_step = outcome_for(
            job,
            markers,
            started=job.started_at or now,
            finished=now,
            rc=rc,
            cancelled=outcome == CANCELLED,
            timed_out=outcome == TIMED_OUT,
            lost=outcome == LOST,
            lost_summary=(given or "worker stopped while running")[:200],
        )
        if given and state in (FAILED, SUCCEEDED) and not summary:
            summary = given[:200]
        with self._remote_lock:
            self._log_partial.pop(job.id, None)
        if not self.store.finish(
            job.id, state, now=now, exit_code=rc, summary=summary, failed_step=failed_step
        ):
            current = self.store.get_job(job.id)
            st = current.state if current else "unknown"
            raise _api_error(409, f"job #{job.id} is {st}", state=st)
        self._publish_job(None, job.id)
        self._publish_server()
        self.wake.set()
        return {"job_id": job.id, "state": state}

    # ── heartbeat ───────────────────────────────────────────────────────────

    def worker_heartbeat(self, token: TokenInfo, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        row = self._registered(token)
        now = self.now_fn()
        self.store.touch_worker(token.name, now)
        active = self.store.jobs_of_worker(token.name)
        known = body.get("jobs")
        if known is not None:
            if not isinstance(known, list) or any(
                isinstance(x, bool) or not isinstance(x, int) for x in known
            ):
                raise _api_error(400, "jobs must be a list of job ids")
            forgotten = [j for j in active if j.id not in set(known)]
            for job in forgotten:
                self.store.finish(
                    job.id, LOST, now=now, summary=SUMMARY_RESTARTED.format(name=token.name)
                )
                self._publish_job(None, job.id)
            if forgotten:
                active = [j for j in active if j.id not in {f.id for f in forgotten}]
        sample = body.get("host_sample")
        if sample is not None:
            try:
                parsed = sample_from_json(sample, name=token.name, source="worker", sampled_at=now)
            except ValueError:
                parsed = None  # 표본만 버린다 — heartbeat 자체는 유효하다
            if parsed is not None:
                with self._remote_lock:
                    self._worker_samples[token.name] = parsed
        cancel = [j.id for j in active if j.state == CANCELLING]
        self._close_unconfirmed_cancels(active, now)
        self._mark_dirty()
        return {
            "cancel": cancel,
            "paused": self.store.get_paused() is not None,
            "timeout_seconds": self.config.server.worker_timeout_seconds,
            "pool": row.pool,
        }

    def _close_unconfirmed_cancels(self, jobs: list[Job], now: datetime) -> None:
        """살아 있는 워커가 취소를 확인하지 않으면 `kill_at + 2 × heartbeat` 뒤 서버가 닫는다."""
        slack = timedelta(seconds=2 * self.config.server.worker_heartbeat_seconds)
        for job in jobs:
            if job.state != CANCELLING or job.cancel is None or job.cancel.kill_at is None:
                continue
            if now >= job.cancel.kill_at + slack:
                if self.store.finish(
                    job.id,
                    CANCELLED,
                    now=now,
                    summary=SUMMARY_CANCEL_UNCONFIRMED,
                    only_from=(CANCELLING,),
                ):
                    self._publish_job(None, job.id)

    # ── down · lost 판정 (janitor 루프) ─────────────────────────────────────

    def mark_lost_workers(self, now: datetime) -> list[int]:
        """heartbeat 이 끊긴 워커의 활성 잡을 lost 로. 살아 있는 워커의 미확인 취소도 닫는다."""
        lost: list[int] = []
        for row in self._workers():
            if self.worker_alive(row, now):
                self._close_unconfirmed_cancels(self.store.jobs_of_worker(row.name), now)
                continue
            gone = int((now - row.last_seen_at).total_seconds())
            ids = self.store.mark_lost_for_worker(
                row.name, now, SUMMARY_UNREACHABLE.format(name=row.name, seconds=gone)
            )
            if ids:
                self.log(f"worker {row.name} unreachable for {gone}s: lost={ids}")
                for job_id in ids:
                    self._publish_job(None, job_id)
                lost.extend(ids)
            with self._remote_lock:
                self._worker_samples.pop(row.name, None)
        if lost:
            self._publish_server()
            self.wake.set()
        return lost


__all__ = [
    "CLAIM_WAIT_SLOTS",
    "MAX_WORKER_LOG_BODY",
    "RemoteWorkersMixin",
    "SUMMARY_CANCEL_UNCONFIRMED",
    "SUMMARY_RESTARTED",
    "SUMMARY_UNREACHABLE",
]
