"""HTTP 서버 — `http.server.ThreadingHTTPServer` · 라우트 · 토큰 인증 · hardening.

라우트(PLAN.md 「서버 API」):
  POST /jobs · PUT /jobs/{id}/tree · GET /jobs/{id}?tail=N · GET /jobs/{id}/log?offset=N ·
  POST /jobs/{id}/cancel · GET /api/status · GET /api/health · GET /api/whoami ·
  POST /pause · POST /resume

hardening: 소켓 타임아웃(일반 10초, 업로드 60초) · `Content-Length` 필수(chunked 는 411) ·
JSON 본문 64KB · 동시 요청 `max_concurrent_requests` 초과 503 · 경로 정규화 ·
405/400/401/403/404/409/411/413 명확히 · 예외는 500 한 줄(스택·토큰·경로 없음).
요청 로그는 debug 에만.

M0 에서 `/api/status` 는 요청 때마다 DB 에서 다시 만든다(이벤트 갱신 모델과 SSE 는 M1).
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import re
import signal
import socket
import socketserver
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from remote_ci_monitor import __version__
from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.inputs import InputError, duration_key, validate_inputs
from remote_ci_monitor.core.model import (
    BUSY_STATES,
    CANCELLED,
    MODE_GIT_REF,
    MODE_TREE,
    QUEUED,
    UPLOADING,
    HostSample,
    Job,
    Median,
    Paused,
    Pool,
    QueueRow,
    Requester,
    ServerInfo,
    Source,
    StatusModel,
    WorkerInfo,
)
from remote_ci_monitor.core.progress import Marker, progress_for_job
from remote_ci_monitor.core.queue import (
    QueueConfig,
    compute_queue,
    eta_for_new,
    join_key,
    medians_from,
)
from remote_ci_monitor.core.status import iso, queue_row_json, recent_json, status_json
from remote_ci_monitor.events import (
    JOB_KINDS,
    KIND_HOST_SAMPLE,
    KIND_JOB_CHANGED,
    KIND_JOB_FINISHED,
    KIND_MARKER,
    KIND_SERVER,
    EventBus,
)
from remote_ci_monitor.hostsample import HostSampler
from remote_ci_monitor.store import Store, TokenInfo
from remote_ci_monitor.worker import Worker, start_workers, tail_lines

MAX_JSON_BODY = 64 * 1024
UPLOAD_CHUNK = 64 * 1024
REQUEST_TIMEOUT = 10
UPLOAD_TIMEOUT = 60
DEFAULT_TAIL = 5
MAX_TAIL = 50
JANITOR_SECONDS = 5.0
_JOB_RE = re.compile(r"^/jobs/(\d+)(/tree|/log|/cancel)?$")
_JOB_EVENTS_RE = re.compile(r"^/jobs/(\d+)/events$")
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
}
SNAPSHOT_MAX_AGE_SECONDS = 0.2
SSE_TICK_SECONDS = 1.0
SSE_WRITE_TIMEOUT_SECONDS = 30.0
_PATH_RE = re.compile(r"/[^\s'\"]+")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _safe(text: str) -> str:
    return _PATH_RE.sub("<path>", text)[:200]


def _mb(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


@dataclass
class _DbSnapshot:
    """DB 에서 읽은 것의 캐시. 잡 이벤트가 오면 dirty, 아니면 짧은 TTL 로 다시 읽는다(명세 0-A)."""

    loaded_at: float
    jobs: list[Job]
    markers: dict[int, list[Marker]]
    queue_error: str | None
    recent: list[Job] | None
    recent_error: str | None
    medians: dict[str, Median] | None
    medians_error: str | None
    paused: Paused | None


class App:
    """서버의 상태와 동작. HTTP 핸들러는 얇고, 규칙은 여기에 있다(테스트하기 쉽게)."""

    def __init__(
        self,
        config: ServerConfig,
        store: Store,
        *,
        now_fn: Callable[[], datetime] = _utcnow,
        version: str = __version__,
        debug: bool = False,
    ):
        self.config = config
        self.store = store
        self.now_fn = now_fn
        self.version = version
        self.debug = debug
        self.started_at = now_fn()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.workers: list[Worker] = []
        self._last_error: str | None = None
        self._lock = threading.Lock()
        self._janitor: threading.Thread | None = None
        self.bus = EventBus()
        self.sampler: HostSampler | None = None
        self._snap: _DbSnapshot | None = None
        self._snap_lock = threading.Lock()
        self._dirty = True
        self._sse_lock = threading.Lock()
        self._sse_connections = 0

    # ── 수명 ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        lost, cancelled = self.store.recover_on_start(self.now_fn())
        if lost or cancelled:
            self.log(f"recovered on start: lost={lost} cancelled_uploads={cancelled}")
            for job_id in [*lost, *cancelled]:
                self._publish_job(None, job_id)
        self.workers = start_workers(
            self.store,
            self.config,
            wake=self.wake,
            stop=self.stop,
            on_change=self._on_job_change,
            on_marker=self._on_marker,
            now_fn=self.now_fn,
        )
        self._janitor = threading.Thread(target=self._janitor_loop, name="rcm-janitor", daemon=True)
        self._janitor.start()
        host = socket.gethostname().split(".")[0] or "host"
        self.sampler = HostSampler(
            self.config.host, name=host, publish=self.publish, stop=self.stop, now_fn=self.now_fn
        )
        self.sampler.start()

    def shutdown(self) -> None:
        self.stop.set()
        self.wake.set()
        self.bus.shutdown()
        for w in self.workers:
            w.shutdown()
        for w in self.workers:
            w.join(timeout=self.config.server.grace_seconds + 10)

    def _janitor_loop(self) -> None:
        while not self.stop.wait(JANITOR_SECONDS):
            try:
                gone = self.store.abandon_stale_uploads(
                    self.now_fn(), self.config.server.upload_abandon_seconds
                )
                if gone:
                    self.log(f"abandoned uploads: {gone}")
                    for job_id in gone:
                        self._publish_job(None, job_id)
            except Exception as e:  # noqa: BLE001
                self.record_error(f"janitor: {type(e).__name__}: {_safe(str(e))}")

    def log(self, msg: str) -> None:
        print(f"[rcm] {msg}", file=sys.stderr, flush=True)

    def record_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = msg[:200]
        self.log(f"error: {msg}")

    @property
    def last_error(self) -> str | None:
        with self._lock:
            err = self._last_error
        for w in self.workers:
            info = w.info()
            if info.state == "down" and info.error:
                return f"lane {info.lane} down: {info.error}"
        return err

    def worker_infos(self) -> list[WorkerInfo]:
        if self.workers:
            return [w.info() for w in self.workers]
        # 워커를 띄우지 않은 상태(테스트)에서는 설정된 레인 수만큼 idle 로 본다
        return [
            WorkerInfo(lane=n, state="idle", since=self.started_at)
            for n in range(1, self.config.server.lanes + 1)
        ]

    # ── 이벤트 ──────────────────────────────────────────────────────────────

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        self.bus.publish(kind, data, at=self.now_fn())

    def _mark_dirty(self) -> None:
        with self._snap_lock:
            self._dirty = True

    def _publish_job(self, job: Job | None, job_id: int) -> None:
        """잡 하나의 상태 변화를 이벤트로. 종료 상태면 job_finished, 아니면 job_changed."""
        self._mark_dirty()
        if job is None:
            try:
                job = self.store.get_job(job_id)
            except Exception:  # noqa: BLE001
                job = None
        if job is None:
            return
        if job.is_terminal:
            self.publish(
                KIND_JOB_FINISHED,
                {"job_id": job.id, "state": job.state, "exit_code": job.exit_code},
            )
        else:
            self.publish(KIND_JOB_CHANGED, {"job_id": job.id, "state": job.state})

    def _on_job_change(self, job_id: int) -> None:
        self._publish_job(None, job_id)
        self._publish_server()

    def _on_marker(self, job_id: int, kind: str, value: str) -> None:
        self._mark_dirty()
        self.publish(KIND_MARKER, {"job_id": job_id, "kind": kind, "value": value})

    def _publish_server(self) -> None:
        try:
            paused = self.store.get_paused()
        except Exception:  # noqa: BLE001
            paused = None
        self.publish(
            KIND_SERVER,
            {
                "paused": {"by": paused.by, "at": iso(paused.at)} if paused else None,
                "workers": [
                    {"lane": w.lane, "state": w.state, "job_id": w.job_id}
                    for w in self.worker_infos()
                ],
            },
        )

    def sse_acquire(self) -> bool:
        with self._sse_lock:
            if self._sse_connections >= self.config.server.sse_max_connections:
                return False
            self._sse_connections += 1
            return True

    def sse_release(self) -> None:
        with self._sse_lock:
            self._sse_connections = max(0, self._sse_connections - 1)

    @property
    def sse_connections(self) -> int:
        with self._sse_lock:
            return self._sse_connections

    # ── 경로 ────────────────────────────────────────────────────────────────

    def job_dir(self, job_id: int) -> Path:
        return self.config.data_dir / "jobs" / str(job_id)

    def log_path(self, job_id: int) -> Path:
        return self.job_dir(job_id) / "log.txt"

    def base_url(self) -> str:
        s = self.config.server
        return s.public_url.rstrip("/") if s.public_url else f"http://{s.bind}:{s.port}"

    # ── 인증 ────────────────────────────────────────────────────────────────

    def authenticate(self, header: str | None) -> TokenInfo | None:
        if not header:
            return None
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            return None
        return self.store.verify_token(value.strip())

    def require_token(self, token: TokenInfo | None) -> TokenInfo:
        if token is None:
            raise ApiError(401, "a valid bearer token is required")
        return token

    def require_admin(self, token: TokenInfo | None) -> TokenInfo:
        t = self.require_token(token)
        if not t.admin:
            raise ApiError(403, "admin token required")
        return t

    def can_read_log(self, job: Job, token: TokenInfo | None) -> bool:
        return token is not None and (token.admin or job.owned_by(token.name))

    # ── 상태 모델 ───────────────────────────────────────────────────────────

    def queue_config(self) -> QueueConfig:
        e = self.config.estimate
        s = self.config.server
        return QueueConfig(
            default_seconds=e.default_seconds,
            floor_remaining_seconds=e.floor_remaining_seconds,
            stuck_multiplier=e.stuck_multiplier,
            no_output_seconds=e.no_output_seconds,
            upload_stall_seconds=s.upload_stall_seconds,
            min_samples=e.min_samples,
            min_job_seconds=e.min_job_seconds,
            sample_days=e.sample_days,
            sample_policy=e.sample_policy,
        )

    def _load_snapshot(self) -> _DbSnapshot:
        """DB 를 한 번 읽는다. 섹션마다 실패는 그 섹션의 `*_error` 로만 남긴다."""
        now = self.now_fn()
        cfg = self.queue_config()
        jobs: list[Job] = []
        markers: dict[int, list[Marker]] = {}
        queue_error = None
        try:
            jobs = self.store.list_active()
            markers = self.store.markers_for([j.id for j in jobs if j.state in BUSY_STATES])
        except Exception as e:  # noqa: BLE001
            queue_error = f"{type(e).__name__}: {_safe(str(e))}"
        medians: dict[str, Median] | None
        medians_error = None
        try:
            since = now - timedelta(days=cfg.sample_days)
            medians = medians_from(self.store.list_samples(since), now, cfg)
        except Exception as e:  # noqa: BLE001
            medians, medians_error = None, f"{type(e).__name__}: {_safe(str(e))}"
        recent: list[Job] | None
        recent_error = None
        try:
            recent = self.store.list_recent(self.config.server.recent_count)
        except Exception as e:  # noqa: BLE001
            recent, recent_error = None, f"{type(e).__name__}: {_safe(str(e))}"
        try:
            paused = self.store.get_paused()
        except Exception:  # noqa: BLE001
            paused = None
        return _DbSnapshot(
            loaded_at=time.monotonic(),
            jobs=jobs,
            markers=markers,
            queue_error=queue_error,
            recent=recent,
            recent_error=recent_error,
            medians=medians,
            medians_error=medians_error,
            paused=paused,
        )

    def _snapshot(self) -> _DbSnapshot:
        """dirty 이거나 TTL 이 지났으면 다시 읽고, 아니면 캐시. status 는 이걸로 순수 계산만."""
        with self._snap_lock:
            snap = self._snap
            fresh = (
                snap is not None
                and not self._dirty
                and time.monotonic() - snap.loaded_at < SNAPSHOT_MAX_AGE_SECONDS
            )
            if fresh:
                return snap  # type: ignore[return-value]
            snap = self._load_snapshot()
            self._snap = snap
            self._dirty = False
            return snap

    def _queue_rows(self, now: datetime, snap: _DbSnapshot) -> list[QueueRow]:
        if snap.queue_error is not None:
            raise RuntimeError(snap.queue_error)
        progress = {
            j.id: p
            for j in snap.jobs
            if j.id in snap.markers
            and (p := progress_for_job(j, snap.markers[j.id], now)) is not None
        }
        return compute_queue(
            snap.jobs,
            workers=self.worker_infos(),
            paused=snap.paused is not None,
            medians=snap.medians or {},
            presets={p.name: p for p in self.config.presets},
            cfg=self.queue_config(),
            now=now,
            progress=progress,
        )

    def _hosts(self) -> tuple[tuple[HostSample, ...] | None, str | None]:
        if self.sampler is None:
            return (), None  # 샘플러 없음 = 표본 없음이지 실패가 아니다
        try:
            hosts, error = self.sampler.latest()
        except Exception as e:  # noqa: BLE001
            return None, f"sampler: {type(e).__name__}"
        if error:
            return None, error
        return tuple(hosts), None

    def status(self, token: TokenInfo | None) -> dict[str, Any]:
        now = self.now_fn()
        snap = self._snapshot()
        queue: list[QueueRow] | None
        queue_error = None
        try:
            queue = self._queue_rows(now, snap)
        except Exception as e:  # noqa: BLE001
            queue, queue_error = None, f"{type(e).__name__}: {_safe(str(e))}"
        hosts, hosts_error = self._hosts()
        server = ServerInfo(
            version=self.version,
            uptime_seconds=(now - self.started_at).total_seconds(),
            lanes=self.config.server.lanes,
            paused=snap.paused,
            last_error=self.last_error,
            workers=tuple(self.worker_infos()),
            sse_connections=self.sse_connections,
        )
        pool = Pool(
            name="default",
            lanes=self.config.server.lanes,
            queue=tuple(queue) if queue is not None else None,
            queue_error=queue_error,
            recent=tuple(snap.recent) if snap.recent is not None else None,
            recent_error=snap.recent_error,
            recent_count=self.config.server.recent_count,
            medians=snap.medians,
            medians_error=snap.medians_error,
            hosts=hosts,
            hosts_error=hosts_error,
        )
        model = StatusModel(
            generated_at=now,
            display_timezone=self.config.display.timezone or None,
            server=server,
            presets=tuple(self.config.presets),
            pools=(pool,),
            base_url=self.base_url(),
        )
        tails: dict[int, list[str]] = {}
        if queue and token is not None:
            for row in queue:
                if row.job.state in BUSY_STATES and self.can_read_log(row.job, token):
                    t = tail_lines(self.log_path(row.job.id), DEFAULT_TAIL)
                    if t is not None:
                        tails[row.job.id] = t
        return status_json(model, log_tails=tails)

    def job_view(self, job_id: int, token: TokenInfo | None, tail: int) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        now = self.now_fn()
        if job.is_terminal:
            return recent_json(job, base_url=self.base_url())
        self._mark_dirty()  # 방금 읽은 잡이 캐시보다 새로울 수 있다
        rows = self._queue_rows(now, self._snapshot())
        row = next((r for r in rows if r.job.id == job_id), None)
        if row is None:  # 방금 끝났다
            job = self.store.get_job(job_id)
            assert job is not None
            return recent_json(job, base_url=self.base_url())
        log_tail = None
        if tail > 0 and row.job.state in BUSY_STATES and self.can_read_log(row.job, token):
            log_tail = tail_lines(self.log_path(job_id), min(tail, MAX_TAIL))
        return queue_row_json(row, base_url=self.base_url(), log_tail=log_tail)

    def eta(self, body: dict[str, Any]) -> dict[str, Any]:
        """`POST /api/eta` — 이 프리셋·입력의 잡을 지금 넣으면 어디에 서나(가상 잡, 명세 0-G)."""
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        name = body.get("preset")
        preset = self.config.preset(name) if isinstance(name, str) else None
        if preset is None:
            raise ApiError(400, f"unknown preset {name!r}")
        try:
            inputs = validate_inputs(preset, body.get("inputs") or {})
        except InputError as e:
            raise ApiError(400, str(e)) from e
        now = self.now_fn()
        snap = self._snapshot()
        if snap.queue_error is not None:
            raise ApiError(503, f"queue unavailable: {snap.queue_error}")
        row, ahead = eta_for_new(
            snap.jobs,
            preset=preset,
            key=duration_key(preset, inputs),
            inputs=inputs,
            workers=self.worker_infos(),
            paused=snap.paused is not None,
            medians=snap.medians or {},
            presets={p.name: p for p in self.config.presets},
            cfg=self.queue_config(),
            now=now,
        )
        doc = queue_row_json(row, base_url=None)
        doc["id"] = None
        doc["url"] = None
        return {"job": doc, "ahead": ahead, "generated_at": iso(now)}

    # ── 제출 ────────────────────────────────────────────────────────────────

    def submit(self, body: dict[str, Any], token: TokenInfo) -> tuple[int, dict[str, Any]]:
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        name = body.get("preset")
        preset = self.config.preset(name) if isinstance(name, str) else None
        if preset is None:
            raise ApiError(400, f"unknown preset {name!r}")
        try:
            inputs = validate_inputs(preset, body.get("inputs") or {})
        except InputError as e:
            raise ApiError(400, str(e)) from e
        src = body.get("source") or {}
        if not isinstance(src, dict):
            raise ApiError(400, "source must be an object")
        mode = src.get("mode", MODE_TREE)
        if mode not in preset.source_modes:
            allowed = ", ".join(preset.source_modes)
            raise ApiError(400, f"preset '{preset.name}' accepts source modes: {allowed}")
        if mode == MODE_GIT_REF:
            raise ApiError(400, "git_ref source mode is not implemented yet (planned for M3)")
        if mode != MODE_TREE:
            raise ApiError(400, f"unknown source mode {mode!r}")
        tree_hash = src.get("tree_hash")
        if not isinstance(tree_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", tree_hash):
            raise ApiError(400, "source.tree_hash must be a sha256 hex string")
        size = src.get("bytes")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise ApiError(400, "source.bytes must be a non-negative integer")
        if size is not None and size > self.config.server.max_snapshot_bytes:
            limit = _mb(self.config.server.max_snapshot_bytes)
            raise ApiError(
                413, f"snapshot {_mb(size)} exceeds {limit} — exclude build outputs via .rcmignore"
            )
        label = body.get("requester_label") or f"{token.name}"
        if not isinstance(label, str) or len(label) > 120:
            raise ApiError(400, "requester_label must be a string of at most 120 characters")
        source = Source(
            mode=MODE_TREE,
            repo=_opt_str(src.get("repo"), 200),
            base_sha=_opt_str(src.get("base_sha"), 64),
            dirty=bool(src.get("dirty")) if src.get("dirty") is not None else None,
            tree_hash=tree_hash,
            bytes=size,
        )
        now = self.now_fn()
        key = duration_key(preset, inputs)
        jk = join_key(preset.name, inputs, source.identity)
        want_join = self.config.server.join_duplicates and body.get("join", True) is not False
        if want_join:
            existing = self.store.find_joinable(jk)
            if existing is not None:
                if existing.requester.name != token.name:
                    self.store.add_joiner(existing.id, token.name, label, now)
                    self._publish_job(None, existing.id)
                return 200, {
                    "job_id": existing.id,
                    "joined": True,
                    "state": existing.state,
                    "url": f"{self.base_url()}/#/jobs/{existing.id}",
                }
        job = self.store.create_job(
            preset=preset.name,
            inputs=inputs,
            key=key,
            concurrency_group=preset.concurrency_group,
            source=source,
            requester=Requester(name=token.name, label=label),
            timeout_seconds=preset.timeout_seconds,
            join_key=jk,
            now=now,
            state=UPLOADING,
        )
        self._publish_job(job, job.id)
        return 201, {
            "job_id": job.id,
            "joined": False,
            "state": job.state,
            "upload": f"/jobs/{job.id}/tree",
            "url": f"{self.base_url()}/#/jobs/{job.id}",
        }

    # ── 업로드 ──────────────────────────────────────────────────────────────

    def begin_upload(self, job_id: int, token: TokenInfo, length: int) -> Job:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        if job.requester.name != token.name and not token.admin:
            raise ApiError(403, "not your job")
        if job.state != UPLOADING:
            raise ApiError(409, f"job is {job.state}, not uploading", state=job.state)
        limit = self.config.server.max_snapshot_bytes
        if length > limit:
            summary = f"snapshot {_mb(length)} exceeds {_mb(limit)}"
            self.store.finish(
                job_id, CANCELLED, now=self.now_fn(), summary=summary, cancelled_by="server"
            )
            self._publish_job(None, job_id)
            raise ApiError(413, f"{summary} — exclude build outputs via .rcmignore")
        return job

    def receive_upload(self, job: Job, reader: Any, length: int) -> dict[str, Any]:
        """본문을 64KB 씩 파일로 흘린다. 끊기면 cancelled 로 남기고 예외."""
        job_dir = self.job_dir(job.id)
        job_dir.mkdir(parents=True, exist_ok=True)
        part = job_dir / "tree.tar.gz.part"
        final = job_dir / "tree.tar.gz"
        received = 0
        last_db = time.monotonic()
        try:
            with part.open("wb") as fh:
                while received < length:
                    chunk = reader.read(min(UPLOAD_CHUNK, length - received))
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
                    if time.monotonic() - last_db >= 1.0:
                        self.store.update_received(job.id, received, self.now_fn())
                        last_db = time.monotonic()
        except (OSError, TimeoutError) as e:
            self._interrupted(job, received, part)
            raise ApiError(400, f"upload interrupted: {type(e).__name__}") from e
        if received < length:
            self._interrupted(job, received, part)
            raise ApiError(400, f"upload interrupted after {_mb(received)}")
        part.replace(final)
        if not self.store.mark_uploaded(job.id, received, self.now_fn()):
            final.unlink(missing_ok=True)
            current = self.store.get_job(job.id)
            state = current.state if current else "unknown"
            raise ApiError(409, f"job was {state} during upload", state=state)
        self._publish_job(None, job.id)
        self.wake.set()
        return {"job_id": job.id, "state": QUEUED, "bytes": received}

    def _interrupted(self, job: Job, received: int, part: Path) -> None:
        part.unlink(missing_ok=True)
        self.store.finish(
            job.id,
            CANCELLED,
            now=self.now_fn(),
            summary=f"upload interrupted after {_mb(received)}",
            cancelled_by="server",
            only_from=(UPLOADING,),
        )
        self._publish_job(None, job.id)

    # ── 취소 · 정지 ─────────────────────────────────────────────────────────

    def cancel(self, job_id: int, token: TokenInfo) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        is_requester = job.requester.name == token.name
        is_joiner = any(j.name == token.name for j in job.joiners)
        if not (token.admin or is_requester or is_joiner):
            raise ApiError(403, "not your job")
        if is_joiner and not is_requester and not token.admin:
            # 합류자는 자기 대기만 빠진다(오너 결정 16)
            self.store.remove_joiner(job_id, token.name)
            self._publish_job(None, job_id)
            return {"left": True, "job_id": job_id, "job_state": job.state}
        if job.is_terminal:
            raise ApiError(409, f"job already finished ({job.state})", state=job.state)
        new_state = self.store.request_cancel(
            job_id, token.name, self.now_fn(), self.config.server.grace_seconds
        )
        self._publish_job(None, job_id)
        self.wake.set()
        return {"job_id": job_id, "state": new_state or job.state}

    def pause(self, token: TokenInfo) -> dict[str, Any]:
        self.store.set_paused(token.name, self.now_fn())
        p = self.store.get_paused()
        self._mark_dirty()
        self._publish_server()
        return {"paused": {"by": p.by, "at": iso(p.at)} if p else None}  # 다른 시각과 같은 Z 표기

    def resume(self) -> dict[str, Any]:
        self.store.clear_paused()
        self._mark_dirty()
        self._publish_server()
        self.wake.set()
        return {"paused": None}

    # ── 로그 · 건강 ─────────────────────────────────────────────────────────

    def log_bytes(self, job_id: int, token: TokenInfo, offset: int) -> tuple[bytes, int, bool]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        if not self.can_read_log(job, token):
            raise ApiError(403, "not your job")
        path = self.log_path(job_id)
        try:
            with path.open("rb") as fh:
                fh.seek(max(0, offset))
                data = fh.read(4 * 1024 * 1024)
                next_offset = fh.tell()
        except FileNotFoundError:
            return b"", 0, not job.is_terminal
        return data, next_offset, not job.is_terminal

    def health(self) -> tuple[int, dict[str, Any]]:
        db_ok = self.store.healthy()
        infos = self.worker_infos()
        alive = [w.is_alive() for w in self.workers] if self.workers else []
        down = [i.lane for i in infos if i.state == "down"] + [
            self.workers[i].lane for i, ok in enumerate(alive) if not ok
        ]
        ok = db_ok and not down
        body = {
            "ok": ok,
            "db": db_ok,
            "workers_down": sorted(set(down)),
            "lanes": self.config.server.lanes,
            "version": self.version,
        }
        if not ok:
            body["error"] = (
                "database unavailable" if not db_ok else f"worker down: lanes {sorted(set(down))}"
            )
        return (200 if ok else 503), body


def read_web_asset(name: str) -> bytes | None:
    """패키지 안의 `web/<name>` 을 읽는다(wheel 에 같이 들어간다). 없으면 None."""
    try:
        path = importlib.resources.files("remote_ci_monitor") / "web" / name
        return path.read_bytes()
    except (FileNotFoundError, OSError, TypeError):
        return None


def _opt_str(v: Any, limit: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ApiError(400, "source fields must be strings")
    return v[:limit]


# ── HTTP 핸들러 ──────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    server_version = f"rcm/{__version__}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT
    app: App  # 서버가 채운다

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.app.debug:
            super().log_message(fmt, *args)

    # ── 응답 ────────────────────────────────────────────────────────────────

    def _send_json(
        self, status: int, obj: Any, *, extra_headers: dict[str, str] | None = None
    ) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error(self, e: ApiError, *, close: bool = False) -> None:
        obj = {"error": e.message, **e.extra}
        if e.status == 401:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="rcm"')
            body = json.dumps(obj).encode()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if close:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json(e.status, obj, extra_headers={"Connection": "close"} if close else None)
        if close:
            self.close_connection = True

    # ── 요청 처리 ───────────────────────────────────────────────────────────

    def _dispatch(self) -> None:
        path = urlsplit(self.path).path.rstrip("/")
        m = _JOB_EVENTS_RE.match(path)
        if path == "/events" or m:
            try:
                self._sse(int(m.group(1)) if m else None)
            except ApiError as e:
                self._send_error(e, close=True)
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
            return
        sem = self.server.slots  # type: ignore[attr-defined]
        if not sem.acquire(blocking=False):
            self._send_error(ApiError(503, "too many concurrent requests"), close=True)
            return
        try:
            self._route()
        except ApiError as e:
            self._send_error(e, close=e.status in (413, 411))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 — 스택은 로그에만, 응답은 한 줄
            self.app.record_error(f"{self.command} {self.path.split('?')[0]}: {type(e).__name__}")
            if self.app.debug:
                import traceback

                traceback.print_exc()
            try:
                self._send_error(ApiError(500, "internal error"), close=True)
            except Exception:  # noqa: BLE001
                self.close_connection = True
        finally:
            sem.release()

    do_GET = do_POST = do_PUT = do_HEAD = _dispatch

    def _token(self) -> TokenInfo | None:
        return self.app.authenticate(self.headers.get("Authorization"))

    def _content_length(self) -> int:
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            raise ApiError(411, "chunked transfer encoding is not supported; send Content-Length")
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise ApiError(411, "Content-Length is required")
        try:
            n = int(raw)
        except ValueError as e:
            raise ApiError(400, "invalid Content-Length") from e
        if n < 0:
            raise ApiError(400, "invalid Content-Length")
        return n

    def _json_body(self) -> Any:
        n = self._content_length()
        if n > MAX_JSON_BODY:
            raise ApiError(413, f"JSON body larger than {MAX_JSON_BODY} bytes")
        data = self.rfile.read(n) if n else b""
        if not data:
            return {}
        try:
            return json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ApiError(400, "body is not valid JSON") from e

    def _read_only_ok(self) -> None:
        """읽기 인증. `none` 이면 누구나, 아니면 유효한 토큰이 있어야 한다."""
        if self.config.server.read_auth != "none" and self._token() is None:
            raise ApiError(401, "read access requires a token on this server")

    @property
    def config(self) -> ServerConfig:
        return self.app.config

    def _route(self) -> None:  # noqa: C901 — 라우트 표는 한 곳에 있는 게 읽기 쉽다
        parts = urlsplit(self.path)
        path = parts.path
        if "//" in path or ".." in path.split("/") or "\\" in path:
            raise ApiError(400, "bad path")
        path = path.rstrip("/") or "/"
        query = parse_qs(parts.query)
        method = self.command
        if method == "HEAD":
            method = "GET"

        if path == "/api/health":
            self._only(method, "GET")
            status, body = self.app.health()
            self._send_json(status, body)
            return
        if path == "/api/whoami":
            self._only(method, "GET")
            t = self.app.require_token(self._token())
            self._send_json(200, {"name": t.name, "admin": t.admin})
            return
        if path == "/api/status":
            self._only(method, "GET")
            self._read_only_ok()
            doc = self.app.status(self._token())
            body = json.dumps(doc, separators=(",", ":")).encode()
            etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("ETag", etag)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        if path == "/jobs":
            self._only(method, "POST")
            t = self.app.require_token(self._token())
            status, body = self.app.submit(self._json_body(), t)
            self._send_json(status, body)
            return
        if path == "/api/eta":
            self._only(method, "POST")
            self._read_only_ok()
            self._send_json(200, self.app.eta(self._json_body()))
            return
        if path == "/pause" or path == "/resume":
            self._only(method, "POST")
            t = self.app.require_admin(self._token())
            self._json_body()  # 본문은 무시하되 읽어서 연결을 깨끗이 둔다
            self._send_json(200, self.app.pause(t) if path == "/pause" else self.app.resume())
            return
        m = _JOB_RE.match(path)
        if m:
            job_id = int(m.group(1))
            sub = m.group(2)
            if sub is None:
                self._only(method, "GET")
                self._read_only_ok()
                tail = _int_param(query, "tail", DEFAULT_TAIL, 0, MAX_TAIL)
                self._send_json(200, self.app.job_view(job_id, self._token(), tail))
                return
            if sub == "/tree":
                self._only(method, "PUT")
                t = self.app.require_token(self._token())
                length = self._content_length()
                job = self.app.begin_upload(job_id, t, length)
                self.connection.settimeout(UPLOAD_TIMEOUT)
                try:
                    body = self.app.receive_upload(job, self.rfile, length)
                finally:
                    self.connection.settimeout(REQUEST_TIMEOUT)
                self._send_json(200, body)
                return
            if sub == "/log":
                self._only(method, "GET")
                t = self.app.require_token(self._token())
                offset = _int_param(query, "offset", 0, 0, None)
                data, next_offset, more = self.app.log_bytes(job_id, t, offset)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-RCM-Next-Offset", str(next_offset))
                self.send_header("X-RCM-More", "1" if more else "0")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                return
            if sub == "/cancel":
                self._only(method, "POST")
                t = self.app.require_token(self._token())
                self._json_body()
                self._send_json(200, self.app.cancel(job_id, t))
                return
        if path in _STATIC_FILES or path.startswith("/static/"):
            self._only(method, "GET")
            self._read_only_ok()
            self._static(path)
            return
        raise ApiError(404, "not found")

    def _static(self, path: str) -> None:
        """정적 UI. 세 파일만 준다. ETag 는 sha256 앞 16자, 나머지 /static/* 는 404."""
        entry = _STATIC_FILES.get(path)
        if entry is None:
            raise ApiError(404, "not found")
        name, ctype = entry
        body = read_web_asset(name)
        if body is None:
            raise ApiError(404, "web assets missing from this installation")
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")  # ETag 재검증은 살리고 캐시 사용은 막는다
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("ETag", etag)
        if name == "index.html":
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; "
                "frame-ancestors 'none'",
            )
            self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _only(self, method: str, allowed: str) -> None:
        if method != allowed:
            raise ApiError(405, f"method not allowed; use {allowed}")

    # ── SSE ─────────────────────────────────────────────────────────────────

    def _sse_write(self, kind: str, event_id: int | None, data: dict[str, Any]) -> None:
        frame = f"event: {kind}\n"
        if event_id is not None:
            frame += f"id: {event_id}\n"
        frame += "data: " + json.dumps(data, separators=(",", ":")) + "\n\n"
        self.wfile.write(frame.encode())
        self.wfile.flush()

    def _sse(self, job_id: int | None) -> None:
        """`GET /events` · `GET /jobs/{id}/events`. 세마포어 대신 `sse_max_connections` 로 센다."""
        if self.command != "GET":
            raise ApiError(405, "method not allowed; use GET")
        app = self.app
        job: Job | None = None
        if job_id is None:
            self._read_only_ok()
        else:
            job = app.store.get_job(job_id)
            if job is None:
                raise ApiError(404, "no such job")
        if not app.sse_acquire():
            body = json.dumps(
                {"error": "too many event streams", "fallback": "poll", "poll_seconds": 10}
            ).encode()
            self.send_response(503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "10")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return
        raw_last = self.headers.get("Last-Event-ID")
        last_id: int | None = None
        if raw_last:
            try:
                last_id = int(raw_last)
            except ValueError:
                last_id = None
        sub = app.bus.subscribe(last_id=last_id)
        keepalive = float(self.config.server.sse_keepalive_seconds)
        try:
            self.connection.settimeout(
                SSE_WRITE_TIMEOUT_SECONDS
            )  # 느린 클라이언트에 shutdown 이 안 걸리게
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            now = app.now_fn()
            self._sse_write(
                "hello",
                app.bus.last_id,
                {
                    "last_id": app.bus.last_id,
                    "generated_at": iso(now),
                    "server": {
                        "version": app.version,
                        "uptime_seconds": round((now - app.started_at).total_seconds()),
                    },
                },
            )
            if job is not None and job.is_terminal:
                self._sse_write(
                    KIND_JOB_FINISHED,
                    app.bus.last_id,
                    {"job_id": job.id, "state": job.state, "exit_code": job.exit_code},
                )
                return
            last_write = time.monotonic()
            while not app.stop.is_set():
                ev = sub.get(timeout=min(keepalive, SSE_TICK_SECONDS))
                if ev is not None and ev.kind == KIND_SERVER and ev.data.get("shutdown"):
                    return
                if ev is None:
                    if time.monotonic() - last_write >= keepalive:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        last_write = time.monotonic()
                    continue
                if job_id is not None and ev.kind in JOB_KINDS and ev.data.get("job_id") != job_id:
                    continue
                if job_id is not None and ev.kind == KIND_HOST_SAMPLE:
                    continue
                self._sse_write(ev.kind, ev.id, ev.data)
                last_write = time.monotonic()
                if job_id is not None and ev.kind == KIND_JOB_FINISHED:
                    return
        finally:
            app.bus.unsubscribe(sub)
            app.sse_release()
            self.close_connection = True


def _int_param(
    query: dict[str, list[str]], name: str, default: int, lo: int, hi: int | None
) -> int:
    raw = query.get(name)
    if not raw:
        return default
    try:
        v = int(raw[0])
    except ValueError as e:
        raise ApiError(400, f"{name} must be an integer") from e
    if v < lo or (hi is not None and v > hi):
        raise ApiError(400, f"{name} out of range")
    return v


class RcmHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App):
        handler = type("BoundHandler", (Handler,), {"app": app})
        super().__init__(address, handler)
        self.app = app
        self.slots = threading.BoundedSemaphore(app.config.server.max_concurrent_requests)

    def server_bind(self) -> None:
        # HTTPServer.server_bind 는 socket.getfqdn() 으로 역방향 DNS 를 조회한다 — macOS 에서
        # 수십 초 멈출 수 있다(CI 에서 실측). 이름은 쓰지 않으므로 조회를 건너뛴다.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # 소켓 오류 스택을 stderr 에 쏟지 않는다(debug 에만)
        if self.app.debug:
            super().handle_error(request, client_address)


def make_server(app: App, *, bind: str | None = None, port: int | None = None) -> RcmHTTPServer:
    address = (bind or app.config.server.bind, app.config.server.port if port is None else port)
    return RcmHTTPServer(address, app)


def serve(config: ServerConfig, *, debug: bool = False) -> int:
    """`rcm serve` 본체. SIGINT/SIGTERM 으로 멈춘다."""
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    store = Store(data_dir / "rcm.sqlite3")
    app = App(config, store, debug=debug)
    httpd = make_server(app)
    app.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    app.log(
        f"rcm {app.version} listening on http://{host}:{port} · lanes {config.server.lanes} · "
        f"presets {', '.join(p.name for p in config.presets) or '(none)'} · data {data_dir}"
    )
    if (
        config.server.bind not in ("127.0.0.1", "localhost", "::1")
        and config.server.read_auth == "none"
    ):
        app.log(
            "warning: bound to a non-loopback address with read_auth = none — LAN/Tailscale only"
        )

    def _stop(signum: int, _frame: Any) -> None:
        app.log(f"signal {signum}: shutting down")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        app.shutdown()
        store.close()
        app.log("stopped")
    return 0
