"""HTTP 서버 — `http.server.ThreadingHTTPServer` · 라우트 · 토큰 인증 · hardening.

라우트(PLAN.md 「서버 API」):
  POST /jobs · PUT /jobs/{id}/tree · GET /jobs/{id}?tail=N · GET /jobs/{id}/log?offset=N ·
  POST /jobs/{id}/cancel · GET /api/status · GET /api/health · GET /api/whoami ·
  POST /pause · POST /resume · `/worker/*`(원격 워커, `remote_workers.py`)

hardening: 소켓 타임아웃(일반 10초, 업로드 60초) · `Content-Length` 필수(chunked 는 411) ·
JSON 본문 64KB · 동시 요청 `max_concurrent_requests` 초과 503 · 경로 정규화 ·
405/400/401/403/404/409/411/413 명확히 · 예외는 500 한 줄(스택·토큰·경로 없음).
요청 로그는 debug 에만.

M0 에서 `/api/status` 는 요청 때마다 DB 에서 다시 만든다(이벤트 갱신 모델과 SSE 는 M1).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.resources
import json
import re
import signal
import socket
import socketserver
import sqlite3
import sys
import tarfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from remote_ci_monitor import __version__
from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.gitref import validate_ref
from remote_ci_monitor.core.inputs import InputError, duration_key, validate_inputs
from remote_ci_monitor.core.manifest import ManifestError, missing_hashes, validate_manifest
from remote_ci_monitor.core.model import (
    BUSY_STATES,
    CANCELLED,
    DEFAULT_POOL,
    MODE_GIT_REF,
    MODE_TREE,
    QUEUED,
    TOKEN_WORKER,
    UPLOADING,
    HostSample,
    Job,
    Median,
    Paused,
    Pool,
    Preset,
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
    priority_from_name,
    split_by_pool,
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
from remote_ci_monitor.gitops import STDERR_TAIL_LINES, GitError, GitTimeout, resolve_ref
from remote_ci_monitor.hostsample import HostSampler
from remote_ci_monitor.janitor import Janitor
from remote_ci_monitor.materialize import blob_path
from remote_ci_monitor.notify import Notifier
from remote_ci_monitor.remote_workers import MAX_WORKER_LOG_BODY, RemoteWorkersMixin
from remote_ci_monitor.store import Store, TokenInfo
from remote_ci_monitor.worker import Worker, start_workers, tail_lines

MAX_JSON_BODY = 64 * 1024
MAX_MANIFEST_BODY = 32 * 1024 * 1024  # 팀 트리(수만 파일)의 manifest 는 64 KB 를 훌쩍 넘는다
UPLOAD_CHUNK = 64 * 1024
REQUEST_TIMEOUT = 10
UPLOAD_TIMEOUT = 60
DEFAULT_TAIL = 5
MAX_TAIL = 50
JANITOR_SECONDS = 5.0
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-_\[\]:]{1,255}$")  # Host 헤더 — URL 에 넣을 만한 모양만
RESOLVE_CONCURRENCY = 2  # 동시에 원격 ls-remote 를 도는 제출 수. 핸들러 32개가 묶이지 않게
MANIFEST_CONCURRENCY = 4  # 동시에 메모리에 올리는 manifest 수(32 MB × 핸들러 32개를 막는다)
_JOB_RE = re.compile(r"^/jobs/(\d+)(/tree/manifest|/tree|/log|/cancel|/priority)?$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_EVENTS_RE = re.compile(r"^/jobs/(\d+)/events$")
_WORKER_RE = re.compile(r"^/worker/(register|claim|heartbeat)$")
_WORKER_JOB_RE = re.compile(r"^/worker/jobs/(\d+)/(tree|phase|log|finish)$")
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
    challenge = "bearer"  # 401 의 WWW-Authenticate 종류. 읽기 라우트는 basic 모드에서 "basic"

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
    pool_medians: dict[str, dict[str, Median]] = field(default_factory=dict)  # 기본 풀 밖 (M5b)


class App(RemoteWorkersMixin):
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
        self.retention: Janitor | None = None
        self.notifier: Notifier | None = None
        self._resolve_sem = threading.BoundedSemaphore(RESOLVE_CONCURRENCY)
        self.manifest_slots = threading.BoundedSemaphore(MANIFEST_CONCURRENCY)
        self.bus = EventBus()
        self.sampler: HostSampler | None = None
        self._snap: _DbSnapshot | None = None
        self._snap_lock = threading.Lock()
        self._dirty = True
        self._sse_lock = threading.Lock()
        self._sse_connections = 0
        self._remote_init()

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
        self.retention = Janitor(
            self.store,
            self.config,
            now_fn=self.now_fn,
            on_error=self.record_error,
            log=self.log,
            stop=self.stop,
        )
        self.retention.start()
        self.notifier = Notifier(
            self.store,
            self.config,
            self.bus,
            now_fn=self.now_fn,
            log=self.log,
            base_url=self.base_url(),
            stop=self.stop,
        )
        self.notifier.start()
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
        if self.retention is not None:
            self.retention.stop()
        if self.notifier is not None:
            self.notifier.stop()

    @property
    def notify_failures(self) -> int:
        return self.notifier.failures if self.notifier is not None else 0

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
            try:
                self.mark_lost_workers(self.now_fn())
            except Exception as e:  # noqa: BLE001
                self.record_error(f"worker janitor: {type(e).__name__}: {_safe(str(e))}")

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
        """로컬 레인(같은 프로세스)."""
        if self.workers:
            return [w.info() for w in self.workers]
        # 워커를 띄우지 않은 상태(테스트)에서는 설정된 레인 수만큼 idle 로 본다
        return [
            WorkerInfo(lane=n, state="idle", since=self.started_at)
            for n in range(1, self.config.server.lanes + 1)
        ]

    def pool_workers(self, pool: str, now: datetime) -> list[WorkerInfo]:
        """그 풀의 레인 전부 — 기본 풀은 로컬 + 원격 `default` 워커, 다른 풀은 원격만(M5b-2)."""
        remote = self.remote_worker_infos(pool, now)
        if pool == DEFAULT_POOL:
            return [*self.worker_infos(), *remote]
        return remote

    def all_worker_infos(self, now: datetime) -> list[WorkerInfo]:
        """로컬 레인 먼저, 원격은 워커 이름순(`server.workers[]`)."""
        return [*self.worker_infos(), *self.remote_worker_infos(None, now)]

    def pool_lanes(self, pool: str, now: datetime) -> int:
        local = self.config.server.lanes if pool == DEFAULT_POOL else 0
        return local + self.remote_lanes(pool, now)

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
                    {"lane": w.lane, "state": w.state, "job_id": w.job_id, "worker": w.worker}
                    for w in self.all_worker_infos(self.now_fn())
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

    def base_url(self, host: str | None = None) -> str:
        """잡 url 의 앞부분. public_url > 요청의 Host(세션이 실제로 쓴 주소) > bind:port.

        bind 가 0.0.0.0 이면 bind:port 는 다른 컴퓨터에서 열리지 않는다(사용자 검사 U2 발견).
        """
        s = self.config.server
        if s.public_url:
            return s.public_url.rstrip("/")
        if host and _HOST_RE.fullmatch(host):
            return f"http://{host}"
        return f"http://{s.bind}:{s.port}"

    # ── 인증 ────────────────────────────────────────────────────────────────

    def authenticate(self, header: str | None) -> TokenInfo | None:
        if not header:
            return None
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            return None
        return self.store.verify_token(value.strip())

    def authenticate_read(self, header: str | None) -> TokenInfo | None:
        """읽기 라우트 인증. Bearer 는 언제나, Basic(`<토큰 이름>:<토큰>`)은 `read_auth = basic`
        일 때만 받는다. 쓰기 라우트는 `authenticate`(Bearer 만) — 브라우저가 Basic 을 자동으로
        붙이므로 쓰기에 허용하면 내부망 CSRF 로 잡 실행·취소가 가능해진다."""
        token = self.authenticate(header)
        if token is not None or not header or self.config.server.read_auth != "basic":
            return token
        scheme, _, value = header.partition(" ")
        if scheme.lower() != "basic" or not value.strip():
            return None
        try:
            raw = base64.b64decode(value.strip(), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        name, sep, secret = raw.partition(":")
        if not sep or not name or not secret:
            return None
        info = self.store.verify_token(secret)
        if info is None or not hmac.compare_digest(info.name.encode(), name.encode()):
            return None
        return info

    def require_token(self, token: TokenInfo | None) -> TokenInfo:
        if token is None:
            raise ApiError(401, "a valid bearer token is required")
        return token

    def require_client_token(self, token: TokenInfo | None) -> TokenInfo:
        """클라이언트 API(제출 · 취소 · 정지 …). 워커 토큰은 `/worker/*` 만 쓴다(M5b-2)."""
        t = self.require_token(token)
        if t.kind == TOKEN_WORKER:
            raise ApiError(403, "worker tokens cannot use the client API")
        return t

    def require_admin(self, token: TokenInfo | None) -> TokenInfo:
        t = self.require_client_token(token)
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
            queue_error = _error_text(e)
        medians: dict[str, Median] | None
        medians_error = None
        pool_medians: dict[str, dict[str, Median]] = {}
        try:
            since = now - timedelta(days=cfg.sample_days)
            samples = split_by_pool(self.store.list_samples(since))
            medians = medians_from(samples.get(DEFAULT_POOL, []), now, cfg)
            for name, sample_jobs in samples.items():
                if name != DEFAULT_POOL:
                    pool_medians[name] = medians_from(sample_jobs, now, cfg)
        except Exception as e:  # noqa: BLE001
            medians, medians_error = None, _error_text(e)
        recent: list[Job] | None
        recent_error = None
        try:
            recent = self.store.list_recent(self.config.server.recent_count)
        except Exception as e:  # noqa: BLE001
            recent, recent_error = None, _error_text(e)
        try:
            paused = self.store.get_paused()
        except Exception:  # noqa: BLE001
            paused = None
        return _DbSnapshot(
            pool_medians=pool_medians,
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

    def _queue_rows(
        self, now: datetime, snap: _DbSnapshot, pool: str | None = None
    ) -> list[QueueRow]:
        """큐 행. `pool=None` 이면 모든 풀(풀마다 따로 계산해 이어 붙인다 — 그룹·레인은 풀 단위)."""
        if snap.queue_error is not None:
            raise RuntimeError(snap.queue_error)
        progress = {
            j.id: p
            for j in snap.jobs
            if j.id in snap.markers
            and (p := progress_for_job(j, snap.markers[j.id], now)) is not None
        }
        rows: list[QueueRow] = []
        by_pool = split_by_pool(snap.jobs)
        names = [pool] if pool is not None else list(by_pool) or [DEFAULT_POOL]
        for name in names:
            jobs = by_pool.get(name, [])
            # 풀의 레인 = 로컬(기본 풀) + 살아 있는 원격 워커. 없거나 다 down 이면 worker_down
            rows.extend(
                compute_queue(
                    jobs,
                    workers=self.pool_workers(name, now),
                    paused=snap.paused is not None,
                    medians=self._pool_medians(snap, name) or {},
                    presets={p.name: p for p in self.config.presets},
                    cfg=self.queue_config(),
                    now=now,
                    progress=progress,
                )
            )
        return rows

    def _pool_medians(self, snap: _DbSnapshot, pool: str) -> dict[str, Median] | None:
        """풀별 중앙값(같은 키라도 머신이 다르면 소요가 다르다). 기본 풀은 스냅샷 값 그대로."""
        if snap.medians is None:
            return None
        if pool == DEFAULT_POOL:
            return snap.medians
        return snap.pool_medians.get(pool, {})

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

    def status(self, token: TokenInfo | None, host: str | None = None) -> dict[str, Any]:
        now = self.now_fn()
        snap = self._snapshot()
        queue: list[QueueRow] | None
        queue_error = None
        try:
            queue = self._queue_rows(now, snap)
        except Exception as e:  # noqa: BLE001
            queue, queue_error = None, _error_text(e)
        hosts, hosts_error = self._hosts()
        blob_count = blob_bytes = None
        if self.config.server.snapshot_cache:
            try:
                blob_count, blob_bytes = self.store.blob_stats()
            except Exception:  # noqa: BLE001 — 통계 실패가 상태 전체를 막으면 안 된다
                blob_count = blob_bytes = None
        server = ServerInfo(
            version=self.version,
            uptime_seconds=(now - self.started_at).total_seconds(),
            lanes=self.config.server.lanes,
            paused=snap.paused,
            last_error=self.last_error,
            workers=tuple(self.all_worker_infos(now)),
            sse_connections=self.sse_connections,
            snapshot_cache_blobs=blob_count,
            snapshot_cache_bytes=blob_bytes,
            notify_failures=self.notify_failures,
        )
        try:
            pool_names = self.store.list_pools()
        except Exception:  # noqa: BLE001
            pool_names = [DEFAULT_POOL]
        for row in self._workers():  # 잡이 없어도 워커가 등록된 풀은 보인다(M5b-2)
            if row.pool not in pool_names:
                pool_names.append(row.pool)
        # list_pools 가 실패해도 큐·최근에 보이는 풀은 떨어뜨리지 않는다(격리 검증 리뷰 노트)
        for job in [*(r.job for r in queue or []), *(snap.recent or [])]:
            if job.pool not in pool_names:
                pool_names.append(job.pool)
        pools: list[Pool] = []
        for name in pool_names:
            local = name == DEFAULT_POOL
            pool_queue = [r for r in queue if r.job.pool == name] if queue is not None else None
            recent = [j for j in snap.recent if j.pool == name] if snap.recent is not None else None
            remote_hosts = self.remote_hosts(name, now)
            pool_hosts: tuple[HostSample, ...] | None
            if local:
                pool_hosts = None if hosts is None else (*hosts, *remote_hosts)
            else:
                pool_hosts = remote_hosts
            pools.append(
                Pool(
                    name=name,
                    lanes=self.pool_lanes(name, now),
                    queue=tuple(pool_queue) if pool_queue is not None else None,
                    queue_error=queue_error,
                    recent=tuple(recent) if recent is not None else None,
                    recent_error=snap.recent_error,
                    recent_count=self.config.server.recent_count,
                    medians=self._pool_medians(snap, name),
                    medians_error=snap.medians_error,
                    hosts=pool_hosts,  # 원격 워커 표본은 heartbeat 에서(M5b-2)
                    hosts_error=hosts_error if local else None,
                )
            )
        model = StatusModel(
            generated_at=now,
            display_timezone=self.config.display.timezone or None,
            server=server,
            presets=tuple(self.config.presets),
            pools=tuple(pools),
            base_url=self.base_url(host),
        )
        tails: dict[int, list[str]] = {}
        if queue and token is not None:
            for row in queue:
                if row.job.state in BUSY_STATES and self.can_read_log(row.job, token):
                    t = tail_lines(self.log_path(row.job.id), DEFAULT_TAIL)
                    if t is not None:
                        tails[row.job.id] = t
        return status_json(model, log_tails=tails)

    def job_view(
        self, job_id: int, token: TokenInfo | None, tail: int, host: str | None = None
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        now = self.now_fn()
        if job.is_terminal:
            return recent_json(job, base_url=self.base_url(host))
        self._mark_dirty()  # 방금 읽은 잡이 캐시보다 새로울 수 있다
        rows = self._queue_rows(now, self._snapshot())
        row = next((r for r in rows if r.job.id == job_id), None)
        if row is None:  # 방금 끝났다
            job = self.store.get_job(job_id)
            assert job is not None
            return recent_json(job, base_url=self.base_url(host))
        log_tail = None
        if tail > 0 and row.job.state in BUSY_STATES and self.can_read_log(row.job, token):
            log_tail = tail_lines(self.log_path(job_id), min(tail, MAX_TAIL))
        return queue_row_json(row, base_url=self.base_url(host), log_tail=log_tail)

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
        priority = self._parse_priority(body.get("priority"), preset.priority)
        pool = self._requested_pool(body, preset)
        row, ahead = eta_for_new(
            snap.jobs,
            preset=preset,
            key=duration_key(preset, inputs),
            inputs=inputs,
            workers=self.pool_workers(pool, now),
            paused=snap.paused is not None,
            medians=self._pool_medians(snap, pool) or {},
            presets={p.name: p for p in self.config.presets},
            cfg=self.queue_config(),
            now=now,
            priority=priority,
            pool=pool,
        )
        doc = queue_row_json(row, base_url=None)
        doc["id"] = None
        doc["url"] = None
        return {"job": doc, "ahead": ahead, "generated_at": iso(now)}

    # ── 제출 ────────────────────────────────────────────────────────────────

    def submit(
        self, body: dict[str, Any], token: TokenInfo, host: str | None = None
    ) -> tuple[int, dict[str, Any]]:
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
        label = body.get("requester_label") or f"{token.name}"
        if not isinstance(label, str) or len(label) > 120:
            raise ApiError(400, "requester_label must be a string of at most 120 characters")
        priority = self._requested_priority(body, preset, token)
        pool = self._requested_pool(body, preset)
        if mode == MODE_GIT_REF:
            return self._submit_git_ref(
                preset, inputs, src, label, token, body, host, priority, pool
            )
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
            existing = self.store.join_or_bump(jk, token.name, label, priority, now)
            if existing is not None:
                self._publish_job(None, existing.id)
                return 200, {
                    "job_id": existing.id,
                    "joined": True,
                    "state": existing.state,
                    "priority": existing.priority,
                    "url": f"{self.base_url(host)}/#/jobs/{existing.id}",
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
            priority=priority,
            pool=pool,
        )
        self._publish_job(job, job.id)
        return 201, {
            "job_id": job.id,
            "joined": False,
            "state": job.state,
            "priority": job.priority,
            "pool": job.pool,
            "cache": bool(self.config.server.snapshot_cache),
            "upload": f"/jobs/{job.id}/tree",
            "url": f"{self.base_url(host)}/#/jobs/{job.id}",
        }

    def _parse_priority(self, raw: Any, default: int) -> int:
        """`priority` 값(이름 또는 -1·0·1). 없으면 default(프리셋 기본)."""
        if raw is None:
            return default
        if isinstance(raw, bool):
            raise ApiError(400, "priority must be low, normal, high or -1/0/1")
        if isinstance(raw, int):
            if raw in (-1, 0, 1):
                return raw
            raise ApiError(400, "priority must be low, normal, high or -1/0/1")
        try:
            return priority_from_name(raw)
        except ValueError as e:
            raise ApiError(400, str(e)) from e

    def _requested_pool(self, body: dict[str, Any], preset: Preset) -> str:
        """`pool` 은 프리셋의 기본 풀 또는 `pools` 에 있는 것만. 없으면 프리셋 기본."""
        raw = body.get("pool")
        if raw is None:
            return preset.pool
        allowed = [preset.pool, *preset.pools]
        if not isinstance(raw, str) or raw not in allowed:
            raise ApiError(
                400, f"preset '{preset.name}' runs in pools: {', '.join(allowed)} — not {raw!r}"
            )
        return raw

    def _requested_priority(self, body: dict[str, Any], preset: Preset, token: TokenInfo) -> int:
        priority = self._parse_priority(body.get("priority"), preset.priority)
        if priority > preset.priority and not token.admin:
            raise ApiError(403, "priority above the preset default needs an admin token")
        return priority

    def set_job_priority(
        self, job_id: int, body: dict[str, Any], token: TokenInfo
    ) -> dict[str, Any]:
        """`POST /jobs/{id}/priority` — admin 이 대기 잡의 우선순위를 바꾼다(`rcm bump`)."""
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        if body.get("priority") is None:
            raise ApiError(400, "priority is required: low, normal or high")
        priority = self._parse_priority(body.get("priority"), 0)
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        if not self.store.set_priority(job_id, priority, self.now_fn()):
            raise ApiError(409, f"job is {job.state}, not waiting", state=job.state)
        self._publish_job(None, job_id)
        self.wake.set()
        return {"job_id": job_id, "priority": priority}

    # ── 내용 주소 스냅샷 캐시 (M5) ──────────────────────────────────────────

    def blobs_dir(self) -> Path:
        return self.config.data_dir / "blobs"

    def _blob_prefix(self, token: TokenInfo) -> str:
        return f"{token.name}/" if self.config.server.snapshot_cache_scope == "token" else ""

    def receive_manifest(self, job_id: int, token: TokenInfo, body: Any) -> dict[str, Any]:
        """manifest 를 받아 저장하고 빠진 blob 해시를 돌려준다. 빠진 게 없으면 바로 queued."""
        if not self.config.server.snapshot_cache:
            raise ApiError(404, "snapshot cache is disabled on this server")
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        if job.requester.name != token.name and not token.admin:
            raise ApiError(403, "not your job")
        if job.source.mode == MODE_GIT_REF:
            raise ApiError(409, "job takes no tree upload (git_ref source)", state=job.state)
        if job.state != UPLOADING:
            raise ApiError(409, f"job is {job.state}, not uploading", state=job.state)
        limit = self.config.server.max_snapshot_bytes
        try:
            manifest = validate_manifest(body, max_bytes=limit)
        except ManifestError as e:
            if "exceeds" in str(e):
                total = 0
                for f in body.get("files", []) if isinstance(body, dict) else []:
                    size = f.get("size") if isinstance(f, dict) else None
                    if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                        total += size
                summary = f"snapshot {_mb(total)} exceeds {_mb(limit)}"
                self.store.finish(
                    job_id, CANCELLED, now=self.now_fn(), summary=summary, cancelled_by="server"
                )
                self._publish_job(None, job_id)
                raise ApiError(413, f"{summary} — exclude build outputs via .rcmignore") from e
            raise ApiError(400, f"manifest rejected: {e}") from e
        prefix = self._blob_prefix(token)
        have_keys = self.store.have_blobs(prefix + h for h in manifest.unique_hashes)
        have = {k[len(prefix) :] for k in have_keys}
        missing = missing_hashes(manifest, have)
        now = self.now_fn()
        if have_keys:
            self.store.touch_blobs(have_keys, now)
        sizes: dict[str, int] = {}
        for f in manifest.files:
            sizes.setdefault(f.sha256, f.size)
        missing_set = set(missing)
        cached_bytes = sum(f.size for f in manifest.files if f.sha256 not in missing_set)
        doc = {
            "files": [
                {"path": f.path, "mode": f.mode, "size": f.size, "sha256": f.sha256}
                for f in manifest.files
            ],
            "links": [{"path": link.path, "target": link.target} for link in manifest.links],
            "missing": missing,
            "blob_prefix": prefix,
        }
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        tmp = job_dir / ".manifest.json.tmp"
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        tmp.replace(job_dir / "manifest.json")
        self.store.update_source_fields(job_id, cached_bytes=cached_bytes, uploaded_bytes=0)
        self.store.update_received(job_id, 0, now)  # PUT 이 안 오면 abandon 경로가 덮는다
        state = UPLOADING
        if not missing:
            declared = job.source.bytes or 0  # source.bytes 는 세션이 선언한 트리 크기 그대로
            if not self.store.mark_uploaded(job_id, declared, now):
                current = self.store.get_job(job_id)
                st = current.state if current else "unknown"
                raise ApiError(409, f"job was {st} during upload", state=st)
            state = QUEUED
            self.wake.set()
        self._publish_job(None, job_id)
        return {
            "missing": missing,
            "missing_bytes": sum(sizes.get(h, 0) for h in missing),
            "state": state,
        }

    def receive_blobs(self, job: Job, reader: Any, length: int) -> dict[str, Any]:
        """`PUT …/tree` + `X-RCM-Tree: blobs`: 멤버 이름이 sha256 인 tar.gz → blob 저장소."""
        job_dir = self.job_dir(job.id)
        manifest_path = job_dir / "manifest.json"
        if not manifest_path.is_file():
            raise ApiError(409, "send the manifest before the blobs", state=job.state)
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected: dict[str, int] = {}
        for f in doc.get("files", []):
            expected.setdefault(f["sha256"], int(f["size"]))
        missing = set(doc.get("missing") or [])
        prefix = doc.get("blob_prefix") or ""
        part = job_dir / "blobs.tar.gz.part"
        received = 0
        try:
            with part.open("wb") as fh:
                while received < length:
                    chunk = reader.read(min(UPLOAD_CHUNK, length - received))
                    if not chunk:
                        break
                    fh.write(chunk)
                    received += len(chunk)
        except (OSError, TimeoutError) as e:
            self._interrupted(job, received, part)
            raise ApiError(400, f"upload interrupted: {type(e).__name__}") from e
        if received < length:
            self._interrupted(job, received, part)
            raise ApiError(400, f"upload interrupted after {_mb(received)}")
        got: set[str] = set()
        stored: list[tuple[str, int]] = []
        thread_id = threading.get_ident()
        try:
            with tarfile.open(part, "r:gz") as tf:
                for member in tf:
                    name = member.name
                    if not _SHA_RE.fullmatch(name) or not member.isfile():
                        raise ApiError(400, "snapshot rejected: blob member is not a sha256 file")
                    if name not in missing:
                        raise ApiError(400, "snapshot rejected: blob not in the missing list")
                    if member.size != expected.get(name):
                        # tar 헤더의 크기가 manifest 와 다르면 내용도 다르다(해시가 맞을 수 없다) —
                        # 디스크에 쓰기 전에 거른다(gzip 폭탄이 선언 크기 이상을 쓰지 못하게)
                        raise ApiError(400, "snapshot rejected: blob hash mismatch (size differs)")
                    src = tf.extractfile(member)
                    if src is None:
                        raise ApiError(400, "snapshot rejected: unreadable blob")
                    final = blob_path(self.blobs_dir(), prefix + name)
                    final.parent.mkdir(parents=True, exist_ok=True)
                    tmp = final.parent / f".{name}.{job.id}.{thread_id}.part"
                    h = hashlib.sha256()
                    size = 0
                    try:
                        with tmp.open("wb") as out:
                            while True:
                                chunk = src.read(UPLOAD_CHUNK)
                                if not chunk:
                                    break
                                h.update(chunk)
                                size += len(chunk)
                                out.write(chunk)
                        if h.hexdigest() != name:
                            raise ApiError(400, "snapshot rejected: blob hash mismatch")
                        if size != expected.get(name):
                            raise ApiError(400, "snapshot rejected: blob size mismatch")
                        if not final.exists():  # 있으면 다른 잡이 먼저 올렸다 — 내용이 같다
                            tmp.replace(final)
                    finally:
                        tmp.unlink(missing_ok=True)  # 실패 · 중복 · OSError 어느 쪽이든 .part 없음
                    got.add(name)
                    stored.append((prefix + name, size))
        except ApiError as e:
            part.unlink(missing_ok=True)
            self.store.finish(
                job.id,
                CANCELLED,
                now=self.now_fn(),
                summary=e.message[:200],
                cancelled_by="server",
                only_from=(UPLOADING,),
            )
            self._publish_job(None, job.id)
            raise
        except (tarfile.TarError, EOFError, OSError) as e:
            part.unlink(missing_ok=True)
            self.store.finish(
                job.id,
                CANCELLED,
                now=self.now_fn(),
                summary=f"snapshot rejected: {type(e).__name__}",
                cancelled_by="server",
                only_from=(UPLOADING,),
            )
            self._publish_job(None, job.id)
            raise ApiError(400, "snapshot rejected: not a valid tar.gz") from e
        part.unlink(missing_ok=True)
        absent = missing - got
        if absent:
            summary = f"snapshot rejected: {len(absent)} blob(s) missing in upload"
            self.store.finish(
                job.id,
                CANCELLED,
                now=self.now_fn(),
                summary=summary,
                cancelled_by="server",
                only_from=(UPLOADING,),
            )
            self._publish_job(None, job.id)
            raise ApiError(400, summary)
        now = self.now_fn()
        if stored:
            self.store.record_blobs(stored, now)
        self.store.update_source_fields(job.id, uploaded_bytes=received)
        declared = job.source.bytes if job.source.bytes is not None else received
        if not self.store.mark_uploaded(job.id, declared, now):
            current = self.store.get_job(job.id)
            state = current.state if current else "unknown"
            raise ApiError(409, f"job was {state} during upload", state=state)
        self._publish_job(None, job.id)
        self.wake.set()
        return {"job_id": job.id, "state": QUEUED, "bytes": received, "blobs": len(stored)}

    def _submit_git_ref(
        self,
        preset: Preset,
        inputs: dict[str, Any],
        src: dict[str, Any],
        label: str,
        token: TokenInfo,
        body: dict[str, Any],
        host: str | None = None,
        priority: int = 0,
        pool: str = DEFAULT_POOL,
    ) -> tuple[int, dict[str, Any]]:
        """git_ref 제출: ref 검증 → 원격에서 sha 확정(DB 락 밖) → 합류 판정 → 바로 queued."""
        repo = self.config.repo(preset.repo)
        if repo is None:
            raise ApiError(400, f"preset '{preset.name}' has no repo configured")
        raw_ref = src.get("ref")
        if not isinstance(raw_ref, str):
            raise ApiError(400, "source.ref must be a string")
        try:
            ref = validate_ref(raw_ref)
        except ValueError as e:
            raise ApiError(400, f"source.ref: {e}") from e
        timeout = self.config.server.git_resolve_timeout_seconds
        if not self._resolve_sem.acquire(timeout=timeout):
            raise ApiError(503, "too many ref resolutions in flight — retry shortly")
        try:
            sha = resolve_ref(repo.url, ref, timeout=timeout)
        except GitTimeout as e:
            raise ApiError(504, f"resolving '{ref}' timed out after {timeout}s") from e
        except GitError as e:
            # 잡이 없으니 잡 로그도 없다 — git 의 stderr(URL 이 섞일 수 있다)는 서버 로그에만 남긴다
            for line in (e.stderr or "").strip().splitlines()[-STDERR_TAIL_LINES:]:
                self.log(f"resolve '{ref}' in repo '{repo.name}': [git] {line}")
            raise ApiError(502, f"cannot resolve '{ref}' in repo '{repo.name}': {e}") from e
        finally:
            self._resolve_sem.release()
        source = Source(
            mode=MODE_GIT_REF, repo=repo.name, ref=ref, sha=sha, base_sha=sha, dirty=False
        )
        now = self.now_fn()
        key = duration_key(preset, inputs)
        jk = join_key(preset.name, inputs, source.identity)
        want_join = self.config.server.join_duplicates and body.get("join", True) is not False
        if want_join:
            existing = self.store.join_or_bump(jk, token.name, label, priority, now)
            if existing is not None:
                self._publish_job(None, existing.id)
                return 200, {
                    "job_id": existing.id,
                    "joined": True,
                    "state": existing.state,
                    "priority": existing.priority,
                    "sha": existing.source.sha,
                    "url": f"{self.base_url(host)}/#/jobs/{existing.id}",
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
            state=QUEUED,
            priority=priority,
            pool=pool,
        )
        self._publish_job(job, job.id)
        self.wake.set()
        return 201, {
            "job_id": job.id,
            "joined": False,
            "state": job.state,
            "priority": job.priority,
            "pool": job.pool,
            "sha": sha,
            "url": f"{self.base_url(host)}/#/jobs/{job.id}",
        }

    # ── 업로드 ──────────────────────────────────────────────────────────────

    def begin_upload(self, job_id: int, token: TokenInfo, length: int) -> Job:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        if job.requester.name != token.name and not token.admin:
            raise ApiError(403, "not your job")
        if job.source.mode == MODE_GIT_REF:
            raise ApiError(409, "job takes no tree upload (git_ref source)", state=job.state)
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
        self.store.update_source_fields(job.id, uploaded_bytes=received, cached_bytes=0)
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
            if not job.is_terminal:
                return b"", 0, True  # 아직 시작 전 — 빈 본문, 계속 따라가라
            if job.artifacts_purged_at is not None:
                raise ApiError(404, "log expired — retention removed it") from None
            # 대기 중 취소 · 프리셋 소멸 · 스냅샷 거부 — 프로세스가 뜨기 전에 끝나 로그가 없던 잡
            raise ApiError(404, "no log — the job ended before its process started") from None
        return data, next_offset, not job.is_terminal

    def health(self) -> tuple[int, dict[str, Any]]:
        db_ok = self.store.healthy()
        infos = self.worker_infos()
        alive = [w.is_alive() for w in self.workers] if self.workers else []
        down = [i.lane for i in infos if i.state == "down"] + [
            self.workers[i].lane for i, ok in enumerate(alive) if not ok
        ]
        janitor_error: str | None = None
        if self.retention is not None:
            if not self.retention.is_alive():
                janitor_error = self.retention.dead or "janitor thread dead"
            elif self.retention.stale(self.now_fn()):
                janitor_error = "janitor stale"
        ok = db_ok and not down and janitor_error is None
        try:
            idle_pools = self.pools_without_workers(self.now_fn())
        except Exception:  # noqa: BLE001
            idle_pools = []
        body = {
            "ok": ok,
            "db": db_ok,
            "workers_down": sorted(set(down)),
            "janitor": janitor_error is None,
            "lanes": self.config.server.lanes,
            "version": self.version,
            "pools_without_workers": idle_pools,  # 등록된 원격 워커가 전부 down 인 풀(정보)
        }
        if not ok:
            if not db_ok:
                body["error"] = "database unavailable"
            elif down:
                body["error"] = f"worker down: lanes {sorted(set(down))}"
            else:
                body["error"] = janitor_error
        return (200 if ok else 503), body


def read_web_asset(name: str) -> bytes | None:
    """패키지 안의 `web/<name>` 을 읽는다(wheel 에 같이 들어간다). 없으면 None."""
    try:
        path = importlib.resources.files("remote_ci_monitor") / "web" / name
        return path.read_bytes()
    except (FileNotFoundError, OSError, TypeError):
        return None


def _error_text(e: BaseException) -> str:
    """섹션 오류 문구. DB 오류는 「database error: …」 로 — 예외 이름 사슬은 사람이 못 읽는다."""
    if isinstance(e, sqlite3.Error):
        return f"database error: {_safe(str(e))}"
    return f"{type(e).__name__}: {_safe(str(e))}"


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
            if e.challenge == "basic":
                self.send_header("WWW-Authenticate", 'Basic realm="rcm", charset="UTF-8"')
            else:
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

    # 모르는 메서드도 우리 라우터로 — 표준 라이브러리의 HTML 501 대신 JSON 405/404 를 낸다
    do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = do_PATCH = do_OPTIONS = _dispatch

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

    def _json_body(self, limit: int = MAX_JSON_BODY) -> Any:
        n = self._content_length()
        if n > limit:
            raise ApiError(413, f"JSON body larger than {limit} bytes")
        data = self.rfile.read(n) if n else b""
        if not data:
            return {}
        try:
            return json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ApiError(400, "body is not valid JSON") from e

    def _read_token(self) -> TokenInfo | None:
        """읽기 라우트의 신원. Bearer 또는(basic 모드) Basic."""
        return self.app.authenticate_read(self.headers.get("Authorization"))

    def _read_only_ok(self) -> None:
        """읽기 인증. `none` 이면 누구나, `basic` 이면 Bearer 나 Basic 자격이 있어야 한다."""
        if self.config.server.read_auth != "none" and self._read_token() is None:
            raise self._read_401("read access requires a token on this server")

    def _read_401(self, message: str) -> ApiError:
        """읽기 라우트의 401. basic 모드면 브라우저 프롬프트를 여는 Basic 챌린지를 단다."""
        err = ApiError(401, message)
        if self.config.server.read_auth == "basic":
            err.challenge = "basic"
        return err

    def _require_read_token(self) -> TokenInfo:
        """토큰이 꼭 필요한 읽기 라우트(whoami · 로그). 401 챌린지는 읽기 규칙을 따른다."""
        token = self._read_token()
        if token is None:
            raise self._read_401("a valid token is required")
        return token

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
            t = self._require_read_token()
            self._send_json(200, {"name": t.name, "admin": t.admin, "kind": t.kind})
            return
        if path == "/api/status":
            self._only(method, "GET")
            self._read_only_ok()
            doc = self.app.status(self._read_token(), host=self.headers.get("Host"))
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
            t = self.app.require_client_token(self._token())
            status, body = self.app.submit(self._json_body(), t, host=self.headers.get("Host"))
            self._send_json(status, body)
            return
        if path == "/api/eta":
            self._only(method, "POST")
            self._read_only_ok()
            self._no_worker_token()
            self._send_json(200, self.app.eta(self._json_body()))
            return
        if path.startswith("/worker/"):
            self._worker_route(method, path)
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
                host = self.headers.get("Host")
                self._send_json(200, self.app.job_view(job_id, self._read_token(), tail, host))
                return
            if sub == "/tree/manifest":
                self._only(method, "POST")
                t = self.app.require_client_token(self._token())
                with self.app.manifest_slots:  # 본문 읽기·파싱·검증을 몇 개만 동시에
                    body = self._json_body(limit=MAX_MANIFEST_BODY)
                    self._send_json(200, self.app.receive_manifest(job_id, t, body))
                return
            if sub == "/priority":
                self._only(method, "POST")
                t = self.app.require_admin(self._token())
                self._send_json(200, self.app.set_job_priority(job_id, self._json_body(), t))
                return
            if sub == "/tree":
                self._only(method, "PUT")
                t = self.app.require_client_token(self._token())
                length = self._content_length()
                job = self.app.begin_upload(job_id, t, length)
                blobs = self.headers.get("X-RCM-Tree", "").strip().lower() == "blobs"
                self.connection.settimeout(UPLOAD_TIMEOUT)
                try:
                    if blobs:
                        body = self.app.receive_blobs(job, self.rfile, length)
                    else:
                        body = self.app.receive_upload(job, self.rfile, length)
                finally:
                    self.connection.settimeout(REQUEST_TIMEOUT)
                self._send_json(200, body)
                return
            if sub == "/log":
                self._only(method, "GET")
                t = self._require_read_token()
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
                t = self.app.require_client_token(self._token())
                self._json_body()
                self._send_json(200, self.app.cancel(job_id, t))
                return
        if path in _STATIC_FILES or path.startswith("/static/"):
            self._only(method, "GET")
            self._read_only_ok()
            self._static(path)
            return
        raise ApiError(404, "not found")

    def _no_worker_token(self) -> None:
        """읽기 규칙의 라우트라도 워커 토큰이 제시되면 거절한다(워커 토큰은 `/worker/*` 만)."""
        t = self._token()
        if t is not None and t.kind == TOKEN_WORKER:
            raise ApiError(403, "worker tokens cannot use the client API")

    def _worker_route(self, method: str, path: str) -> None:
        """`/worker/*` — 워커 토큰만. 인증을 먼저 해 라우트 존재 여부를 익명에게 알리지 않는다."""
        t = self.app.require_worker_token(self._token())
        m = _WORKER_RE.match(path)
        if m:
            self._only(method, "POST")
            what = m.group(1)
            body = self._json_body()
            if what == "register":
                self._send_json(200, self.app.worker_register(t, body))
            elif what == "claim":
                self.connection.settimeout(UPLOAD_TIMEOUT)  # long-poll 은 일반 타임아웃보다 길다
                try:
                    out = self.app.worker_claim(t, body)
                finally:
                    self.connection.settimeout(REQUEST_TIMEOUT)
                if out is None:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    self._send_json(200, out)
            else:
                self._send_json(200, self.app.worker_heartbeat(t, body))
            return
        m = _WORKER_JOB_RE.match(path)
        if not m:
            raise ApiError(404, "not found")
        job_id = int(m.group(1))
        what = m.group(2)
        if what == "tree":
            self._only(method, "GET")
            tar_path = self.app.worker_tree_path(t, job_id)
            size = tar_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/gzip")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            self.connection.settimeout(UPLOAD_TIMEOUT)
            try:
                with tar_path.open("rb") as fh:
                    while True:
                        chunk = fh.read(UPLOAD_CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            finally:
                self.connection.settimeout(REQUEST_TIMEOUT)
            return
        self._only(method, "POST")
        if what == "log":
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/octet-stream":
                raise ApiError(415, "log body must be application/octet-stream")
            n = self._content_length()
            if n > MAX_WORKER_LOG_BODY:
                raise ApiError(413, f"log body larger than {MAX_WORKER_LOG_BODY} bytes")
            self.connection.settimeout(UPLOAD_TIMEOUT)
            try:
                data = self.rfile.read(n) if n else b""
            finally:
                self.connection.settimeout(REQUEST_TIMEOUT)
            if len(data) < n:
                raise ApiError(400, "log body interrupted")
            self._send_json(200, self.app.worker_log(t, job_id, data))
            return
        body = self._json_body()
        if what == "phase":
            self._send_json(200, self.app.worker_phase(t, job_id, body))
        else:
            self._send_json(200, self.app.worker_finish(t, job_id, body))

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
        # 잡별 스트림도 읽기 라우트 — 인증 먼저(basic 모드의 익명 스트림 차단), 존재 여부는 그 뒤
        self._read_only_ok()
        if job_id is not None:
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
