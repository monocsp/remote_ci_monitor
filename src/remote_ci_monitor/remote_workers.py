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

import hashlib
import secrets
import sqlite3
import tarfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from remote_ci_monitor.collect import CollectResult
from remote_ci_monitor.config import _NAME_RE
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.artifacts import BundleFile
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
    WORKER_HELD,
    WORKER_IDLE,
    HostSample,
    Job,
    WorkerInfo,
)
from remote_ci_monitor.core.progress import parse_marker
from remote_ci_monitor.core.status import iso, source_json
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
#: 문장은 `core.outcome` 표가 만든다(결정 37). 이 이름들은 옛 호출부와 테스트를 위해 남긴다.
SUMMARY_RESTARTED = "worker {name} restarted without the job"
SUMMARY_UNREACHABLE = "worker {name} unreachable for {seconds}s"
SUMMARY_CANCEL_UNCONFIRMED = "worker did not confirm the cancel"


def _api_error(status: int, message: str, **extra: Any) -> Exception:
    from remote_ci_monitor.server import ApiError  # 순환 import 를 피한다

    return ApiError(status, message, **extra)


#: SQLite 의 「지금 바쁘다」. 이것만 일시 오류(503)로 본다 — `no such table` 같은 영구 결함은
#: 그대로 500 으로 올린다.
_BUSY_MARKERS = ("locked", "busy")


def _is_busy_error(e: sqlite3.OperationalError) -> bool:
    return any(m in str(e).lower() for m in _BUSY_MARKERS)


def _api_error_type() -> type[Exception]:
    from remote_ci_monitor.server import ApiError

    return ApiError


def _int_field(body: dict[str, Any], key: str, lo: int, hi: int, *, default: int | None = None):
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise _api_error(400, f"{key} must be an integer")
    if not lo <= v <= hi:
        raise _api_error(400, f"{key} must be between {lo} and {hi}")
    return v


def _finish_outcome(code: str, **args: Any) -> dict[str, Any]:
    """`store.finish` 에 바로 넣을 요약 세 값(결정 37)."""
    text, code, clean = outcome.summary(code, **args)
    return {"summary": text, "summary_code": code, "summary_args": clean}


def _stream_to_file(stream: Any, dest: Path, length: int) -> str:
    """`length` 바이트를 그대로 파일에 흘려 넣고 sha256 을 돌려준다. 메모리에 안 올린다."""
    digest = hashlib.sha256()
    left = length
    with dest.open("wb") as fh:
        while left > 0:
            chunk = stream.read(min(1 << 16, left))
            if not chunk:
                raise _api_error(400, "artifact upload ended early")
            digest.update(chunk)
            fh.write(chunk)
            left -= len(chunk)
    return digest.hexdigest()


def _manifest_from_tar(path: Path, max_files: int) -> list[BundleFile] | None:
    """올라온 tar 에서 매니페스트를 만든다 — 목록은 서버가 **본 것**이지 워커가 말한 것이 아니다.

    finish 본문으로 받지 않는 이유: 파일 만 개짜리 목록은 JSON 본문 상한을 훌쩍 넘는다.

    읽을 수 없는 묶음이면 `None` — 「목록을 모른다」다. 바이트와 해시는 그대로 검증하고
    저장한다. 못 본 파일을 봤다고 말하지 않는다(§3). 안전하지 않은 경로는 그때도 거절한다.
    """
    files: list[BundleFile] = []
    try:
        tar = tarfile.open(path, "r:")
    except (tarfile.TarError, OSError):
        return None
    with tar:
        for member in tar:
            if not member.isfile():
                continue
            if len(files) >= max_files:
                raise _api_error(413, f"artifact bundle has more than {max_files} files")
            try:
                art.check_path(member.name)
            except art.PolicyError as e:
                raise _api_error(400, f"artifact bundle has an unsafe path: {e}") from e
            fh = tar.extractfile(member)
            digest = hashlib.sha256()
            if fh is not None:
                while True:
                    chunk = fh.read(1 << 16)
                    if not chunk:
                        break
                    digest.update(chunk)
            files.append(
                BundleFile(
                    path=member.name,
                    size=member.size,
                    sha256=digest.hexdigest(),
                    mode=0o755 if member.mode & 0o111 else 0o644,
                )
            )
    return files


def _parse_iso(raw: Any) -> datetime | None:
    """워커가 보낸 ISO Z 시각. 이상하면 None — 조용히 수신 시각으로 물러선다."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        rows = [r for r in self._workers() if pool is None or r.pool == pool]
        # 워커마다 묻지 않고 **한 문장**으로 읽는다.
        # 실패를 삼키지 않는다: 빈 map 으로 물러서면 도는 레인이 `idle` 로, `since` 까지 등록
        # 시각으로 바뀌어 「그 레인은 등록 이후 계속 놀았다」는 **없는 사실**을 지어낸다.
        # 모르는 것은 모르는 대로 올린다(fail-open 금지).
        lanes_busy = self.store.active_worker_lanes() if rows else {}
        for row in rows:
            alive = self.worker_alive(row, now)
            for lane in range(1, row.lanes + 1):
                job = lanes_busy.get((row.name, lane)) if alive else None
                if not alive:
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_DOWN,
                            error="no heartbeat",
                            since=row.last_seen_at,
                            worker=row.name,
                            pool=row.pool,
                        )
                    )
                elif job is not None:
                    job_id, started_at = job
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_BUSY,
                            job_id=job_id,
                            since=started_at,
                            worker=row.name,
                            pool=row.pool,
                        )
                    )
                else:
                    # 상태 경로는 **다시 판정하지 않는다** — claim 경로가 남긴 것을 읽는다.
                    # 두 번 부르면 화면과 실제가 어긋난다(§4.5).
                    hold, held_since = self.hold_of(lane, row.name, now)
                    infos.append(
                        WorkerInfo(
                            lane=lane,
                            state=WORKER_HELD if hold else WORKER_IDLE,
                            since=held_since or row.registered_at,
                            worker=row.name,
                            pool=row.pool,
                            hold_code=hold.code if hold else None,
                            hold_detail=dict(hold.detail) if hold and hold.detail else None,
                            held_since=held_since,
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
        text, code, args = outcome.summary("worker_restarted_without_job", name=token.name)
        lost = self.store.mark_lost_for_worker(
            token.name, now, text, summary_code=code, summary_args=args
        )
        for job_id in lost:
            self._publish_job(None, job_id)
        if lost:
            self._drop_log_partial(lost)
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
                "state": job.state,
                "lane": job.lane,
                "source": source_json(job.source),
                "inputs": dict(job.inputs),
                "requester": {"name": job.requester.name, "label": job.requester.label},
                "requested_by": job.requester.label,
                "concurrency_group": job.concurrency_group,
                "group": job.concurrency_group,
                "timeout_seconds": job.timeout_seconds,
                "started_at": iso(job.started_at),
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
        if not self.store.touch_worker(token.name, now):
            # 이제 워커 행이 사라질 수 있다(은퇴 정리) — 그 뒤에 claim 하면 아무도 못 거두는
            # 잡이 된다. 다시 등록하라고 말한다.
            raise _api_error(409, "worker is not registered")
        job, hold = self._try_claim(token.name, row.pool, lane, now)
        if job is not None:
            return self._claim_payload(job)
        # 보류된 레인은 **슬롯을 안 잡는다**. 8개뿐인 long-poll 슬롯을 보류 레인이 먹으면
        # 정작 열린 레인이 즉시 204 를 받고 1초 폴링으로 떨어진다(실측: 0.003초 → 0.394초).
        if hold is not None:
            return None
        if wait <= 0 or not self._claim_slots.acquire(blocking=False):
            return None
        try:
            deadline = time.monotonic() + wait  # 주입 시계(now_fn)가 아니라 실제 경과 시간
            while not self.stop.is_set():
                woke = self.wake.wait(CLAIM_POLL_SECONDS)
                job, _hold = self._try_claim(token.name, row.pool, lane, self.now_fn())
                if job is not None:
                    return self._claim_payload(job)
                if time.monotonic() >= deadline:
                    break
                if woke:
                    # `wake` 는 로컬 워커 스레드가 지우는 이벤트라 로컬 레인이 바쁘면 세워진 채로
                    # 남는다 — 그대로 돌면 매 반복 DB 를 두드리므로 한 주기 쉬고 다시 본다
                    self.stop.wait(CLAIM_POLL_SECONDS)
        finally:
            self._claim_slots.release()
        return None

    def _try_claim(self, name: str, pool: str, lane: int, now: datetime):
        """(잡, 보류) — 둘 다 None 이면 큐가 빈 것이다."""
        try:
            if self.store.get_paused() is not None:
                return None, None
            job, hold = self.admit(
                lane, name, now, lambda: self.store.claim(lane, now, pool=pool, worker_name=name)
            )
        except LaneBusy as e:
            raise _api_error(409, str(e)) from e
        except sqlite3.OperationalError as e:
            # 잠금·바쁨만 일시 오류다. `no such table` 같은 영구 결함을 「다시 해 보라」고 하면
            # 워커가 영원히 재시도한다. 워커는 503 을 이미 일시 오류로 처리한다.
            if not _is_busy_error(e):
                raise
            raise _api_error(
                503,
                "database is busy",
                headers={"Retry-After": "1"},
                code="database_busy",
                retry_after=1,
            ) from e
        if job is not None:
            self._publish_job(job, job.id)
            self._publish_server()
        return job, hold

    # ── 잡 보고 ─────────────────────────────────────────────────────────────

    def _drop_log_partial(self, job_ids: list[int]) -> None:
        """종료된 잡의 잘린 마지막 줄 조각을 버린다 — 늦은 보고는 409 라 다시 쓰일 일이 없고, 두면
        lost 로 닫힌 잡마다 조각이 남는다."""
        with self._remote_lock:
            for job_id in job_ids:
                self._log_partial.pop(job_id, None)

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
        # 마커는 **한 트랜잭션**으로 쓰고, 발행은 커밋 뒤에 한다. 줄마다 트랜잭션을 열면 다른
        # 레인의 claim 이 밀린다(256 KB flush 하나에 0.03 ms → 275.9 ms).
        parsed_markers = [
            p for raw in lines if (p := parse_marker(raw.decode("utf-8", errors="replace")))
        ]
        self.store.add_markers(job.id, parsed_markers, now)
        for kind, value in parsed_markers:
            self._on_marker(job.id, kind, value)
        markers = len(parsed_markers)
        if data:
            self.store.set_last_output(job.id, now)
            self._mark_dirty()
        return {"job_id": job.id, "bytes": len(data), "markers": markers}

    # ── 잡 산출물 (M5e) ─────────────────────────────────────────────────────

    def _worker_job(self, token: TokenInfo, job_id: int) -> Job:
        """그 워커가 잡은 잡. **종료됐어도 답한다** — 모호한 finish 를 푸는 것이 이 라우트다(§6)."""
        job = self.store.get_job(job_id)
        if job is None:
            raise _api_error(404, "no such job")
        if job.worker_name != token.name:
            raise _api_error(403, "not your job")
        return job

    def worker_artifacts_status(self, token: TokenInfo, job_id: int) -> dict[str, Any]:
        """업로드 처리 상태와 잡 상태. 내용은 주지 않는다(§6)."""
        job = self._worker_job(token, job_id)
        row = self.store.get_bundle(job.id)
        return {
            "job_id": job.id,
            "state": row["state"] if row else None,
            "bundle_sha256": row["bundle_sha256"] if row else None,
            "job_state": job.state,
        }

    def job_artifact_policy(self, job: Job) -> Any:
        """그 잡에 얼린 정책. 프리셋의 글롭 + 서버의 잡당 상한(§9)."""
        s = self.config.server
        preset = self.config.preset(job.preset)
        return art.ArtifactPolicy(
            globs=tuple(preset.artifacts) if preset is not None else (),
            max_bytes=s.max_artifact_bytes,
            max_files=s.max_artifact_files,
            timeout_seconds=s.artifact_timeout_seconds,
            cancel_timeout_seconds=s.artifact_cancel_timeout_seconds,
        )

    def worker_receive_artifacts(
        self, token: TokenInfo, job_id: int, stream: Any, length: int
    ) -> tuple[int, dict[str, Any]]:
        """워커가 올린 묶음을 받는다. (상태 코드, 본문). 같은 해시의 재시도는 멱등이다(§6)."""
        job = self._owned_active(token, job_id)  # 종료된 잡은 409 — 죽은 잡을 되살리지 않는다
        policy = self.job_artifact_policy(job)
        allowance = art.archive_allowance(policy.max_bytes, policy.max_files)
        if length > allowance:
            raise _api_error(413, f"artifact bundle larger than {allowance} bytes")
        now = self.now_fn()
        limit = self.config.server.artifact_storage_max_bytes
        if not self.store.reserve_bundle_bytes(job.id, length, limit, now):
            raise _api_error(503, "artifact storage is full")
        root = self.config.data_dir / "artifacts"
        staging = root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        part = staging / f"{job.id}.{secrets.token_hex(8)}.part"
        try:
            digest = _stream_to_file(stream, part, length)
            row = self.store.get_bundle(job.id)
            stored = row["bundle_sha256"] if row else None
            if stored and stored != digest:
                # 묶음은 불변이다 — 한 번 받은 내용은 바뀌지 않는다(§3)
                raise _api_error(409, "a different bundle is already stored for this job")
            if stored == digest:
                return 200, {"job_id": job.id, "bundle_sha256": digest, "bytes": length}
            dest = root / str(job.id)
            dest.mkdir(parents=True, exist_ok=True)
            files = _manifest_from_tar(part, policy.max_files) or []
            part.replace(dest / "bundle.tar")
        except _api_error_type():
            part.unlink(missing_ok=True)
            self.store.release_bundle_bytes(job.id)
            raise
        except OSError as e:
            part.unlink(missing_ok=True)
            self.store.release_bundle_bytes(job.id)
            raise _api_error(503, f"cannot store the bundle ({type(e).__name__})") from e
        result = CollectResult(
            state=art.UPLOADING,
            files=tuple(files),
            total_bytes=sum(f.size for f in files),
            bundle_bytes=length,
            bundle_sha256=digest,
        )
        self.store.publish_bundle(
            job.id,
            result,
            now=now,
            # 영수증만 남고 finish 가 안 오면 sweep 이 가져간다 — 만료 없는 행을 안 만든다(§9 ⑤)
            expires_at=art.expires_at(now, self.config.server.artifact_retention_hours),
        )
        self.store.set_bundle_state(job.id, art.UPLOADING)
        self.publish_artifacts(job.id, art.UPLOADING)
        return 201, {"job_id": job.id, "bundle_sha256": digest, "bytes": length}

    def worker_finish(self, token: TokenInfo, job_id: int, body: Any) -> dict[str, Any]:
        from remote_ci_monitor.worker import outcome_for

        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        job = self._owned_active(token, job_id)
        reported = body.get("outcome")
        if reported not in WORKER_OUTCOMES:
            raise _api_error(400, f"outcome must be one of {', '.join(WORKER_OUTCOMES)}")
        rc = body.get("exit_code")
        if rc is not None and (isinstance(rc, bool) or not isinstance(rc, int)):
            raise _api_error(400, "exit_code must be an integer or null")
        given = body.get("summary")
        if given is not None and not isinstance(given, str):
            raise _api_error(400, "summary must be a string")
        now = self.now_fn()
        # 실행 종료 시각(선택). 수집·업로드가 끼어도 v1 의 `finished_at`·`job_seconds` 가
        # 밀리지 않게 워커가 실어 보낸다(§10). 이상하면 조용히 수신 시각으로 물러선다.
        ended = _parse_iso(body.get("finished_at")) or now
        disposition = body.get("artifacts")
        if disposition is not None and not isinstance(disposition, dict):
            raise _api_error(400, "artifacts must be an object")
        markers = self.store.markers(job.id)
        lost_text, lost_code, lost_args = outcome.summary("worker_stopped_while_running")
        if given:  # 워커가 자기 문장을 보냈으면 그대로 쓴다 — 코드는 붙이지 않는다
            lost_text, lost_code, lost_args = given[:200], None, {}
        oc = outcome_for(
            job,
            markers,
            started=job.started_at or ended,
            finished=ended,
            rc=rc,
            cancelled=reported == CANCELLED,
            timed_out=reported == TIMED_OUT,
            lost=reported == LOST,
            lost_summary=lost_text,
            lost_code=lost_code,
            lost_args=lost_args,
        )
        state, summary, failed_step = oc.state, oc.summary, oc.failed_step
        code, args = oc.code, oc.args
        if state in (FAILED, SUCCEEDED) and not summary:
            if given:
                summary, code, args = given[:200], None, {}
            elif state == FAILED:
                summary, code, args = outcome.summary("worker_failed")
        self._drop_log_partial([job.id])
        if disposition is not None:
            self._record_disposition(job.id, disposition, now)
        if not self.store.finish(
            job.id,
            state,
            now=ended,
            exit_code=rc,
            summary=summary,
            summary_code=code,
            summary_args=args,
            failed_step=failed_step,
            failed_step_guessed=oc.failed_step_guessed,
        ):
            current = self.store.get_job(job.id)
            st = current.state if current else "unknown"
            raise _api_error(409, f"job #{job.id} is {st}", state=st)
        self._publish_job(None, job.id)
        self._publish_server()
        self.wake.set()
        return {"job_id": job.id, "state": state}

    def _record_disposition(self, job_id: int, reported: dict[str, Any], now: datetime) -> None:
        """워커가 보고한 산출물 처분을 쓴다(§5).

        필드가 통째로 없으면 이 함수를 부르지 않는다 — 그게 `unknown` 이고 `empty` 와 다르다.
        """
        state = reported.get("state")
        if state not in art.ARTIFACT_STATES:
            raise _api_error(400, "artifacts.state is not a known state")
        row = self.store.get_bundle(job_id)
        if state == art.READY:
            # 무엇을 모았는지는 **워커가** 안다. 서버는 자기가 받은 바이트를 안다 — 올라온 행이
            # 있으면 그 해시·바이트를 쓰고, 없으면 워커 말을 그대로 적는다. 파일이 실제로 없으면
            # 내려받기가 503 을 내고 재시작 화해가 `unavailable` 로 고친다(§3 · §9).
            files = tuple(
                BundleFile(path=f["path"], size=f["size"], sha256=f["sha256"], mode=f["mode"])
                for f in ((row or {}).get("files") or [])
            )
            digest = (row or {}).get("bundle_sha256") or reported.get("bundle_sha256")
            self.store.publish_bundle(
                job_id,
                CollectResult(
                    state=art.READY,
                    files=files,
                    total_bytes=int(reported.get("total_bytes") or 0),
                    bundle_bytes=int((row or {}).get("bundle_bytes") or 0),
                    skipped_count=int(reported.get("skipped_count") or 0),
                    bundle_sha256=digest if isinstance(digest, str) else None,
                ),
                now=now,
                expires_at=art.expires_at(now, self.config.server.artifact_retention_hours),
            )
            if reported.get("file_count") is not None and not files:
                # 매니페스트는 못 봤지만 워커가 센 수는 있다 — 지어내지 말고 그대로 적는다
                self.store.set_bundle_counts(job_id, int(reported["file_count"]))
            return
        reason = reported.get("reason_code")
        if reason is not None and reason not in art.REASONS:
            raise _api_error(400, "artifacts.reason_code is not a known code")
        args = reported.get("reason_args")
        if args is not None and not isinstance(args, dict):
            raise _api_error(400, "artifacts.reason_args must be an object")
        if state == art.EMPTY:
            self.store.publish_bundle(
                job_id,
                CollectResult(
                    state=art.EMPTY,
                    total_bytes=int(reported.get("total_bytes") or 0),
                    skipped_count=int(reported.get("skipped_count") or 0),
                    reason_code=reason,
                    reason_args=args,
                ),
                now=now,
                expires_at=art.expires_at(now, self.config.server.artifact_retention_hours),
            )
            return
        self.store.set_bundle_failed(job_id, state, reason or "collect_failed", args, now)

    # ── heartbeat ───────────────────────────────────────────────────────────

    def worker_heartbeat(self, token: TokenInfo, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise _api_error(400, "body must be a JSON object")
        self._registered(token)
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
                text, code, args = outcome.summary("worker_restarted_without_job", name=token.name)
                self.store.finish(
                    job.id, LOST, now=now, summary=text, summary_code=code, summary_args=args
                )
                self._publish_job(None, job.id)
            if forgotten:
                self._drop_log_partial([f.id for f in forgotten])
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
                    **_finish_outcome("cancel_unconfirmed"),
                    only_from=(CANCELLING,),
                ):
                    self._drop_log_partial([job.id])
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
            text, code, args = outcome.summary("worker_unreachable", name=row.name, seconds=gone)
            ids = self.store.mark_lost_for_worker(
                row.name, now, text, summary_code=code, summary_args=args
            )
            if ids:
                self.log(f"worker {row.name} unreachable for {gone}s: lost={ids}")
                self._drop_log_partial(ids)
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
