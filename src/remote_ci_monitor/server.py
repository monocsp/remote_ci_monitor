"""HTTP 서버 — `http.server.ThreadingHTTPServer` · 라우트 · 토큰 인증 · hardening.

라우트(PLAN.md 「서버 API」):
  POST /jobs · PUT /jobs/{id}/tree · GET /jobs/{id}?tail=N · GET /jobs/{id}/log?offset=N ·
  POST /jobs/{id}/cancel · GET /api/status · GET /api/health · GET /api/whoami ·
  POST /pause · POST /resume · `/worker/*`(원격 워커, `remote_workers.py`) ·
  GET /client/remote_ci_monitor-<X>-py3-none-any.whl(자기 클라이언트 wheel, `clientwheel.py`)

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
import shutil
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
from remote_ci_monitor.clientwheel import (
    MIN_CLIENT_VERSION,
    WheelBuildError,
    build_wheel,
    wheel_filename,
)
from remote_ci_monitor.config import (
    LOOPBACK_BINDS,
    ServerConfig,
    admission_warnings,
    advertise_enabled,
    advertise_warning,
    retention_warning,
)
from remote_ci_monitor.core import admission, outcome
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.failures import failures_json
from remote_ci_monitor.core.gitref import validate_ref
from remote_ci_monitor.core.inputs import InputError, duration_key, validate_inputs
from remote_ci_monitor.core.manifest import ManifestError, missing_hashes, validate_manifest
from remote_ci_monitor.core.mdns import instance_name
from remote_ci_monitor.core.model import (
    BUSY_STATES,
    CANCELLED,
    DEFAULT_POOL,
    FAILED,
    MODE_GIT_REF,
    MODE_TREE,
    QUEUED,
    TIMED_OUT,
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
from remote_ci_monitor.core.status import (
    artifacts_json,
    iso,
    queue_row_json,
    recent_json,
    status_json,
)
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
from remote_ci_monitor.mdns import Responder
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
_JOB_RE = re.compile(
    r"^/jobs/(\d+)"
    r"(/tree/manifest|/tree|/log|/cancel|/priority"
    r"|/artifacts/archive|/artifacts/ack|/artifacts)?$"
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_EVENTS_RE = re.compile(r"^/jobs/(\d+)/events$")
_WORKER_RE = re.compile(r"^/worker/(register|claim|heartbeat)$")
_WORKER_JOB_RE = re.compile(r"^/worker/jobs/(\d+)/(tree|phase|log|finish|artifacts)$")
_ID_IN_PATH = re.compile(r"/(\d+)")
#: 404 가 길을 알려 준다(M5h · 결정 69). 별칭 라우트는 만들지 않는다 — 한 가지에 이름 하나다.
_ROUTES_HINT = (
    "routes: GET /api/status · GET /api/health · GET /jobs/<id> · GET /jobs/<id>/log · POST /jobs"
)


def not_found_hint(path: str) -> str:
    """모르는 경로에 맞는 길 한 줄. 숫자가 있으면 그 잡의 진짜 경로, 없으면 주요 라우트.

    `/api/status` 가 `/api` 아래인데 잡은 `/jobs` 아래라 `/api/jobs/162` 는 자연스러운
    오추측이다. 그 오추측에 아무 말도 안 하면 사람이 로그를 못 찾는다(신고 2).
    """
    found = _ID_IN_PATH.findall(path)
    if not found:
        return _ROUTES_HINT
    n = found[-1]  # `/v1/jobs/162` 의 잡 번호는 앞의 버전이 아니라 **뒤의 숫자**다
    return (
        f"job #{n} is GET /jobs/{n} · its log is GET /jobs/{n}/log "
        f"with that job's token (try: rcm logs {n})"
    )


_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/static/i18n.js": ("i18n.js", "application/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
}
SNAPSHOT_MAX_AGE_SECONDS = 0.2
#: 중앙값은 잡이 끝날 때만 다시 재지만, 잡이 하나도 안 끝나는 동안에도 45일 창은 흘러간다 —
#: 그래서 시간으로도 상한을 둔다. 무효화 경로를 하나 놓쳐도 이 안에 스스로 낫는다.
MEDIANS_MAX_AGE_SECONDS = 300.0
SSE_TICK_SECONDS = 1.0
SSE_WRITE_TIMEOUT_SECONDS = 30.0
_PATH_RE = re.compile(r"/[^\s'\"]+")
#: 제어문자(개행 포함) — 로그 줄 위조를 막는다. 문면은 남기고 한 줄로 접는다.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _safe(text: str) -> str:
    """오류 문구를 로그·상태 문서에 실을 수 있게 다듬는다 — 절대 경로를 지우고 한 줄로 접는다.

    **경로 지우개지 비밀 지우개가 아니다.** 경로 밖에 맨몸으로 있는 토큰은 못 지운다. 그래서
    자세한 문구는 로그에만 두고, 인증 없이 읽히는 `last_error` 에는 예외 이름까지만 낸다.

    제어문자를 공백으로 바꾸는 이유: 문구 안의 `\\n` 이 그대로 나가면 진짜 `[rcm] error:` 줄처럼
    생긴 두 번째 줄이 로그에 찍힌다 — 사람도 로그를 긁는 경보도 속는다.
    """
    return _CTRL_RE.sub(" ", _PATH_RE.sub("<path>", text))[:200]


def _finish_outcome(code: str, **args: Any) -> dict[str, Any]:
    """`store.finish` 에 바로 넣을 요약 세 값. 결정 37 — 문장은 outcome 표가 만든다."""
    text, code, clean = outcome.summary(code, **args)
    return {"summary": text, "summary_code": code, "summary_args": clean}


def _mb(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


class ApiError(Exception):
    challenge = "bearer"  # 401 의 WWW-Authenticate 종류. 읽기 라우트는 basic 모드에서 "basic"

    def __init__(
        self, status: int, message: str, *, headers: dict[str, str] | None = None, **extra: Any
    ):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra
        self.headers = headers or {}  # Retry-After 같은 응답 헤더


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
    #: 섹션 실패의 **종류**(결정 37). 원문은 위의 `*_error` 에 남는다.
    queue_error_code: str | None = None
    recent_error_code: str | None = None
    medians_error_code: str | None = None


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
        self.transfer_slots = threading.BoundedSemaphore(
            max(1, config.server.max_concurrent_artifact_transfers)
        )
        self.bus = EventBus()
        self.sampler: HostSampler | None = None
        self._snap: _DbSnapshot | None = None
        self._snap_lock = threading.Lock()
        self._dirty = True
        # 중앙값은 **잡이 끝났을 때만** 다시 잰다. 45일치 완료 잡을 매 요청 읽으면
        # `/api/status` 가 보존된 잡 수에 선형으로 끌려간다(1만 행 168 ms) — 그런데 45일치
        # 중앙값은 `::rcm::step::` 한 줄로 바뀌지 않는다(M5f 결정 49).
        self._medians: (
            tuple[dict[str, Median] | None, dict[str, dict[str, Median]], str | None, str | None]
            | None
        ) = None
        self._medians_dirty = True
        self._medians_loaded_at = 0.0
        self._sse_lock = threading.Lock()
        self._sse_connections = 0
        # 부하 게이트(M5f §4.5). 락은 **하나(전역)** 이고 게이트를 지나는 레인(≥ 2)만 잡는다 —
        # 재 보니 머신별로 쪼개면 burst 에서 22배 느리다. 전역 락이 BEGIN IMMEDIATE 를 줄
        # 세우는 유일한 장치라, 없애면 스레드들이 SQLite writer 락의 거친 백오프에 걸린다.
        self._admit_lock = threading.Lock()
        # 머신 = 워커 등록 단위. 로컬은 None, 원격은 워커 이름. 한 머신에서 serve 와 worker 를
        # 같이 돌리면 게이트 없는 레인이 둘이 된다 — 자동 병합은 안 한다(결정 46).
        self._last_admit: dict[str | None, datetime] = {}
        # 상태 경로가 **다시 판정하지 않게** claim 경로의 결과를 남긴다. 두 번 부르면 화면과
        # 실제가 어긋나고, Worker._set 이 상태가 바뀔 때마다 _since 를 되감아 not_scheduled
        # 알람이 죽는다(§4.5).
        self._hold: dict[tuple[str | None, int], tuple[admission.Hold | None, datetime]] = {}
        # 클라이언트 wheel(I8-1 · 결정 81) — 기동 때 한 번 조립해 메모리에 든다. (바이트, sha256)
        # 또는 실패 코드. 실패도 한 번만 — 요청마다 다시 시도해서 다른 답을 주지 않는다.
        self._wheel: tuple[bytes, str] | None = None
        self._wheel_error: str | None = None
        self._wheel_done = False
        self._wheel_lock = threading.Lock()
        self._remote_init()

    # ── 수명 ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._build_client_wheel()  # 「도는 것을 준다」 — 기동 시점의 파일로 고정한다
        if self._wheel is not None:
            size = _mb(len(self._wheel[0]))
            self.log(f"client wheel ready: {wheel_filename(self.version)} ({size})")
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
            admit=self._admit_local,
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
            self.config.host,
            name=host,
            publish=self.publish,
            stop=self.stop,
            now_fn=self.now_fn,
            # 푼 경로(프로퍼티)다 — 원시 문자열 "~/…" 는 disk_usage 가 못 읽는다(M5i B5)
            disk_path=str(self.config.data_dir),
        )
        self.sampler.start()
        s = self.config.server
        if s.lanes >= 2 and s.admission == "load":
            # 안 그러면 첫 증상이 「두 번째 레인이 갑자기 멈췄다」다
            self.log(
                f"admission: load (cpu <= {s.cpu_max_percent:g}%, lanes 2+; lane 1 always claims)"
            )
        warning = retention_warning(s)
        if warning:
            self.log(warning)
        for warning in admission_warnings(s, self.config.host):
            self.log(warning)
        self.responder = None
        if advertise_enabled(self.config.server):
            name = self.config.server.advertise_name or host
            self.responder = Responder(
                instance_name(name),
                f"{host}.local.",
                self.config.server.port,
                txt_fn=lambda: {
                    "v": self.version,
                    "name": name,
                    "lanes": str(self.config.server.lanes),
                },
                log=self.log,
            )
            self.responder.start()
            warning = advertise_warning(self.config.server)
            if warning:
                self.log(warning)

    def shutdown(self) -> None:
        self.stop.set()
        if getattr(self, "responder", None) is not None:
            self.responder.stop()
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

    def record_error(self, msg: str, *, detail: str | None = None) -> None:
        """`msg` 는 공개되는 `server.last_error`(짧게), `detail` 은 서버 로그에만(자세히).

        `/api/status` 는 `read_auth = none` 이 기본이라 `last_error` 를 인증 없이 읽는다. 예외
        문구에는 경로나 남의 입력이 실릴 수 있어 공개면은 안 넓힌다. 로그는 서버를 가진 사람만
        보므로 거기엔 원문을 남긴다 — 2026-09-08 사고 때 로그에 `OperationalError` 만 314줄이
        남아 「database is locked」인지 「unable to open database file」인지 못 갈랐다.
        `detail` 은 부르는 쪽이 `_safe()` 로 씻어서 준다.
        """
        with self._lock:
            self._last_error = msg[:200]
        self.log(f"error: {msg}" + (f": {detail}" if detail else ""))

    @property
    def last_error(self) -> str | None:
        with self._lock:
            err = self._last_error
        for w in self.workers:
            info = w.info()
            if info.state == "down" and info.error:
                return f"lane {info.lane} down: {info.error}"
        return err

    # ── 부하 게이트 (M5f) ───────────────────────────────────────────────────

    def admission_config(self, *, remote: bool) -> admission.AdmissionConfig:
        """서버 설정 → 순수 계층의 설정. `core/queue.QueueConfig` 와 같은 방식이다.

        원격 표본은 서버가 받은 시각으로 다시 찍히므로 나이가 곧 마지막 heartbeat 이후 시간이다.
        그래서 낡음 상한에 heartbeat 주기도 함께 본다.
        """
        s = self.config.server
        stale = admission.STALE_MULTIPLIER * self.config.host.interval_seconds
        if remote:
            stale = max(stale, admission.STALE_MULTIPLIER * s.worker_heartbeat_seconds)
        return admission.AdmissionConfig(
            policy=s.admission,
            cpu_max_percent=s.cpu_max_percent,
            samples=s.admission_samples,
            cooldown_seconds=float(s.admission_cooldown_seconds),
            stale_seconds=float(stale),
        )

    def _machine_sample(self, worker: str | None) -> HostSample | None:
        """그 머신의 마지막 표본. 로컬은 프로세스 안 샘플러, 원격은 heartbeat 로 받은 것."""
        if worker is None:
            hosts, _error = self._hosts()
            return hosts[0] if hosts else None
        with self._remote_lock:
            return self._worker_samples.get(worker)

    def _decide_admission(self, lane: int, worker: str | None, now: datetime):
        hold = admission.decide(
            lane=lane,
            sample=self._machine_sample(worker),
            now=now,
            last_admit_at=self._last_admit.get(worker),
            cfg=self.admission_config(remote=worker is not None),
        )
        previous = self._hold.get((worker, lane))
        # held_since 는 **막히기 시작한 시각**이다 — 같은 이유로 계속 막혀 있으면 유지한다
        if hold is not None and previous and previous[0] is not None:
            self._hold[(worker, lane)] = (hold, previous[1])
        else:
            self._hold[(worker, lane)] = (hold, now)
        return hold

    def hold_of(self, lane: int, worker: str | None, now: datetime):
        """상태 경로용 — **다시 판정하지 않고** claim 경로가 남긴 것을 읽는다.

        기록이 claim 주기보다 오래됐으면 fail-closed 로 본다(그 레인이 안 도는 것이다).
        """
        entry = self._hold.get((worker, lane))
        if entry is None:
            return None, None
        hold, since = entry
        return hold, (since if hold is not None else None)

    def admit(self, lane: int, worker: str | None, now: datetime, claim):
        """게이트를 지나 claim 한다. 레인 1 은 락을 안 기다리고, 판정도 안 지난다.

        게이트를 지나는 레인은 `판정 → claim → 쿨다운 기록` 을 **락 안에서** 한다. 안 그러면
        레인 넷이 같은 순간에 깨어 전부 통과한다(실측: 락 없이 20회 중 20회).
        """
        if lane <= 1 or self.config.server.admission != "load":
            job = claim()
            if job is not None:
                self._last_admit[worker] = now  # 레인 1 도 **기록은 한다**(결정 41)
            return job, None
        with self._admit_lock:
            hold = self._decide_admission(lane, worker, now)
            if hold is not None:
                return None, hold
            job = claim()
            if job is not None:  # 큐가 비어 헛돈 것은 쿨다운을 쓰지 않는다
                self._last_admit[worker] = now
            return job, None

    def _admit_local(self, lane: int, now: datetime, claim):
        """로컬 레인 스레드가 부르는 게이트. 머신 키는 None(서버 자신)이다."""
        return self.admit(lane, None, now, claim)

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

    def _mark_medians_dirty(self) -> None:
        """새 표본이 생겼다 — 잡이 종료 상태에 이르렀을 때만."""
        with self._snap_lock:
            self._medians_dirty = True

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
            # 종료 상태였는지 못 읽었다 — 표본을 놓치느니 한 번 더 재는 쪽을 고른다
            self._mark_medians_dirty()
            return
        if job.is_terminal:
            self._mark_medians_dirty()  # 새 표본이 생겼다 — 여기서만
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
                    {
                        "lane": w.lane,
                        "state": w.state,
                        "job_id": w.job_id,
                        "worker": w.worker,
                        "hold_code": w.hold_code,  # 필이 이유 없는 `held` 로 남지 않게
                    }
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

    # ── 잡 산출물 (M5e) ─────────────────────────────────────────────────────

    def artifacts_dir(self, job_id: int) -> Path:
        return self.config.data_dir / "artifacts" / str(int(job_id))

    def artifact_state(self, job: Job, row: dict[str, Any] | None) -> str:
        """행이 없을 때의 상태는 **파생**이다(명세 §9).

        글롭을 선언하지 않은 프리셋은 `disabled` · 아직 안 끝난 잡은 `pending` · 끝났는데 아무
        보고가 없으면 `unknown` 이다. **`empty` 로 소급하지 않는다** — 모르는 것과 없는 것은 다르다.
        """
        if row is not None:
            return str(row["state"])
        preset = self.config.preset(job.preset)
        if preset is not None and not preset.artifacts:
            return art.DISABLED
        if not job.is_terminal:
            return art.PENDING
        return art.UNKNOWN

    def artifacts_public(self, job: Job) -> dict[str, Any]:
        """잡 JSON·상태 문서에 더하는 객체. 경로는 절대 안 싣는다(§10)."""
        try:
            row = self.store.get_bundle(job.id)
        except Exception:  # noqa: BLE001 — 산출물 조회 실패가 상태 전체를 막으면 안 된다
            row = None
        return artifacts_json(row, self.artifact_state(job, row))

    def job_storage(self) -> dict[str, Any]:
        """부피 회계(M5g §5.1). 청소기가 **마지막에 잰 값**을 쓴다 — 상태 요청이 디스크를 훑지
        않는다. 실패해도 상태 문서를 막지 않는다."""
        if self.retention is None:
            return Janitor(self.store, self.config, now_fn=self.now_fn).storage(self.now_fn())
        try:
            return self.retention.storage(self.now_fn())
        except Exception as e:  # noqa: BLE001
            return {"error_code": _error_code(e)}

    def gc(self, body: Any) -> dict[str, Any]:
        """`POST /gc`(admin). 청소기와 **같은 계획 함수**를 돌린다(§5.5).

        보장되는 것은 「같은 입력에 같은 판정」이지 「보여준 것과 실제가 같다」가 아니다 — 두
        요청 사이에 잡이 끝나고 디렉터리가 생긴다.
        """
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        unknown = set(body) - {"dry_run"}
        if unknown:
            raise ApiError(400, f"unknown key '{sorted(unknown)[0]}'")
        dry_run = body.get("dry_run", False)
        if not isinstance(dry_run, bool):  # "false" 는 참이 아니라 오류다
            raise ApiError(400, "dry_run must be true or false")
        if self.retention is None:
            raise ApiError(503, "retention is not running")
        # `storage_before`·`storage_after` 는 청소기가 같은 락 안에서 낸다 — 계획이 잰 스냅샷과
        # 지운 뒤 다시 잰 값이다. 여기서 먼저 읽으면 **직전** 측정값(기동 직후 0 B)이 된다(B3).
        return self.retention.gc_report(self.now_fn(), dry_run=dry_run)

    def artifact_storage(self) -> dict[str, Any]:
        """서버 전체 회계(§10). 실패해도 상태 문서를 막지 않는다."""
        try:
            stored, reserved = self.store.bundle_storage_totals()
            error_code = None
        except Exception as e:  # noqa: BLE001
            stored = reserved = None
            error_code = _error_code(e)
        # ⚠️ 청소기의 속성 이름은 `retention` 이다. 예전에 여기서 `getattr(self, "janitor")` 로
        # 찾는 바람에 이 값이 **언제나 null** 이었다(M5g §3.1 A).
        last = self.retention.last_sweep_at if self.retention is not None else None
        return {
            "stored_bytes": stored,
            "reserved_bytes": reserved,
            "limit_bytes": self.config.server.artifact_storage_max_bytes,
            "last_sweep_at": iso(last),
            "error_code": error_code,
        }

    def _artifact_job(self, job_id: int, token: TokenInfo | None) -> Job:
        """산출물 라우트의 자격 — 로그와 같되 **워커 토큰은 명시적으로 거부한다**(§6)."""
        if token is not None and token.kind == TOKEN_WORKER:
            raise ApiError(403, "worker tokens cannot read job artifacts")
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        if not self.can_read_log(job, token):
            raise ApiError(403, "not your job")
        return job

    def artifacts_view(self, job_id: int, token: TokenInfo | None) -> dict[str, Any]:
        """보호 문서. `ready` 일 때만 매니페스트와 `detail` 을 담는다(§6)."""
        job = self._artifact_job(job_id, token)
        row = self.store.get_bundle(job.id)
        state = self.artifact_state(job, row)
        doc = artifacts_json(row, state)
        doc["bundle_sha256"] = row["bundle_sha256"] if row else None
        ready = state == art.READY
        doc["files"] = (row or {}).get("files", []) if ready else []
        doc["detail"] = (row or {}).get("detail") if ready else None
        return doc

    def artifact_archive(self, job_id: int, token: TokenInfo | None) -> tuple[Path, int, int]:
        """아카이브를 내려보낼 준비. (파일, 바이트, 잡 번호). 상태별 코드는 §6 표다."""
        job = self._artifact_job(job_id, token)
        row = self.store.get_bundle(job.id)
        state = self.artifact_state(job, row)
        if state in art.GONE_STATES:
            raise ApiError(410, "artifacts are gone")
        if state in art.PROGRESS_STATES:
            raise ApiError(409, f"artifacts are {state}")
        if state in art.NOTHING_STATES:
            raise ApiError(404, "no artifacts for this job")
        if state != art.READY or row is None:
            raise ApiError(503, "artifacts are unavailable")
        expires = row["expires_at"]
        if expires is not None and self.now_fn() >= expires:
            # 청소기가 아직 안 왔어도 만료가 지나면 새 다운로드는 안 준다(§6)
            raise ApiError(410, "artifacts expired")
        path = self.artifacts_dir(job.id) / "bundle.tar"
        try:
            size = path.stat().st_size
        except OSError:
            raise ApiError(503, "artifacts are unavailable") from None
        return path, size, job.id

    def purge_bundle(self, job_id: int, state: str, now: datetime) -> bool:
        """묶음 파일을 지우고 표시한다. **물리적으로 지운 뒤에** 지웠다고 말한다(§7)."""
        try:
            shutil.rmtree(self.artifacts_dir(job_id))
        except FileNotFoundError:
            pass
        except OSError as e:
            self.log(f"artifacts: job {job_id}: {type(e).__name__}")
            return False
        self.store.mark_bundles_purged([job_id], state, now)
        return True

    def publish_artifacts(self, job_id: int, state: str) -> None:
        """`artifacts_changed` — `_publish_job` 을 쓰면 끝난 잡에 `job_finished` 가 다시 나간다."""
        self._mark_dirty()
        self.publish("artifacts_changed", {"job_id": int(job_id), "state": state})

    def ack_artifacts(self, job_id: int, token: TokenInfo, body: Any) -> dict[str, Any]:
        """확인(ack). 자격자가 「다 받아서 트리에 썼다」고 말하는 자리다(§7)."""
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        sent = body.get("bundle_sha256")
        if not isinstance(sent, str) or not re.fullmatch(r"[0-9a-f]{64}", sent):
            raise ApiError(400, "bundle_sha256 must be a sha256 hex string")
        job = self._artifact_job(job_id, token)
        owner = job.owned_by(token.name)  # admin 이라도 남의 몫을 대신 확인해 주지 않는다
        now = self.now_fn()
        decision = self.store.ack_bundle(job.id, sent, owner=owner, now=now)
        if decision.status == 409:
            raise ApiError(409, "artifacts are not in a state to acknowledge")
        if decision.status == 410:
            raise ApiError(410, "artifacts are gone")
        purged = decision.purge and self.purge_bundle(job.id, art.PURGED, now)
        if purged:
            self.publish_artifacts(job.id, art.PURGED)
        return {"job_id": job.id, "purged": bool(purged), "reason_code": decision.reason_code}

    def finalize_bundle_hold(self, job_id: int) -> None:
        """마지막 독자가 닫았다. 만료로 파일이 이미 간 묶음이면 그때 회계를 돌려준다(§6)."""
        if self.store.bundle_is_held(job_id):
            return
        row = self.store.get_bundle(job_id)
        if row is not None and row["state"] == art.EXPIRED and row["purged_at"] is None:
            self.store.mark_bundles_purged([job_id], art.EXPIRED, self.now_fn())
            self.publish_artifacts(job_id, art.EXPIRED)

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
        queue_error_code: str | None = None
        try:
            jobs = self.store.list_active()
            markers = self.store.markers_for([j.id for j in jobs if j.state in BUSY_STATES])
        except Exception as e:  # noqa: BLE001
            queue_error, queue_error_code = _error_text(e), _error_code(e)
        medians, pool_medians, medians_error, medians_error_code = self._load_medians(now, cfg)
        recent: list[Job] | None
        recent_error = None
        recent_error_code: str | None = None
        try:
            recent = self.store.list_recent(self.config.server.recent_count)
        except Exception as e:  # noqa: BLE001
            recent, recent_error = None, _error_text(e)
            recent_error_code = _error_code(e)
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
            queue_error_code=queue_error_code,
            recent=recent,
            recent_error=recent_error,
            recent_error_code=recent_error_code,
            medians=medians,
            medians_error=medians_error,
            medians_error_code=medians_error_code,
            paused=paused,
        )

    def _load_medians(
        self, now: datetime, cfg: QueueConfig
    ) -> tuple[dict[str, Median] | None, dict[str, dict[str, Median]], str | None, str | None]:
        """45일치 표본 → 풀별 중앙값. `_medians_dirty` 이거나 TTL 이 지났을 때만 실제로 읽는다.

        **호출자가 `_snap_lock` 을 들고 있어야 한다** — `self._medians*` 를 잠금 없이 만진다.
        지금 호출자는 `_load_snapshot` 하나뿐이고 그건 `_snapshot` 의 잠금 안에서 돈다.

        읽을 때도 무거운 `Job` 이 아니라 `Store.list_sample_rows` 의 가벼운 행을 쓴다 —
        중앙값이 보는 것은 여섯 칸뿐이다.
        """
        fresh = (
            not self._medians_dirty
            and self._medians is not None
            and time.monotonic() - self._medians_loaded_at < MEDIANS_MAX_AGE_SECONDS
        )
        if fresh:
            return self._medians  # type: ignore[return-value]
        medians: dict[str, Median] | None
        medians_error: str | None = None
        medians_error_code: str | None = None
        pool_medians: dict[str, dict[str, Median]] = {}
        try:
            since = now - timedelta(days=cfg.sample_days)
            samples = split_by_pool(self.store.list_sample_rows(since))
            medians = medians_from(samples.get(DEFAULT_POOL, []), now, cfg)
            for name, sample_rows in samples.items():
                if name != DEFAULT_POOL:
                    pool_medians[name] = medians_from(sample_rows, now, cfg)
        except Exception as e:  # noqa: BLE001
            # **실패는 캐시하지 않는다.** 캐시하면 SQLite 가 잠깐 잠긴 것만으로 `medians: null`
            # 이 다음 잡이 끝날 때까지 모든 상태 문서에 박힌다 — 한가한 서버면 몇 시간이다.
            # 다음 요청이 다시 시도한다(옛 동작 그대로).
            return (None, {}, _error_text(e), _error_code(e))
        out = (medians, pool_medians, medians_error, medians_error_code)
        self._medians = out
        self._medians_dirty = False
        self._medians_loaded_at = time.monotonic()
        return out

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
        queue_error_code: str | None = None
        try:
            queue = self._queue_rows(now, snap)
        except Exception as e:  # noqa: BLE001
            queue, queue_error = None, _error_text(e)
            queue_error_code = _error_code(e)
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
                    queue_error_code=queue_error_code,
                    recent=tuple(recent) if recent is not None else None,
                    recent_error=snap.recent_error,
                    recent_error_code=snap.recent_error_code,
                    recent_count=self.config.server.recent_count,
                    medians=self._pool_medians(snap, name),
                    medians_error=snap.medians_error,
                    medians_error_code=snap.medians_error_code,
                    hosts=pool_hosts,  # 원격 워커 표본은 heartbeat 에서(M5b-2)
                    hosts_error=hosts_error if local else None,
                    hosts_error_code=("sampler_failed" if hosts_error and local else None),
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
        doc = status_json(model, log_tails=tails)
        self._inject_artifacts(doc, model)
        return doc

    def _inject_artifacts(self, doc: dict[str, Any], model: StatusModel) -> None:
        """상태 문서에 산출물 처분과 전체 회계를 **더한다**(§10).

        순수 층(`core/status.py`)은 DB 를 모른다 — 모양은 거기가 정하고 값은 여기서 채운다.
        """
        by_id: dict[int, Job] = {}
        for pool in model.pools:
            for row in pool.queue or ():
                by_id[row.job.id] = row.job
            for job in pool.recent or ():
                by_id[job.id] = job
        for pool in doc.get("pools", []):
            for row in [*pool.get("queue", []), *pool.get("recent", [])]:
                job = by_id.get(row.get("id"))
                if job is not None:
                    row["artifacts"] = self.artifacts_public(job)
        doc["server"]["artifact_storage"] = self.artifact_storage()
        doc["server"]["job_storage"] = self.job_storage()

    def job_view(
        self, job_id: int, token: TokenInfo | None, tail: int, host: str | None = None
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        now = self.now_fn()
        if job.is_terminal:
            return self._terminal_view(job, host)
        self._mark_dirty()  # 방금 읽은 잡이 캐시보다 새로울 수 있다
        rows = self._queue_rows(now, self._snapshot())
        row = next((r for r in rows if r.job.id == job_id), None)
        if row is None:  # 방금 끝났다
            job = self.store.get_job(job_id)
            assert job is not None
            return self._terminal_view(job, host)
        log_tail = None
        if tail > 0 and row.job.state in BUSY_STATES and self.can_read_log(row.job, token):
            log_tail = tail_lines(self.log_path(job_id), min(tail, MAX_TAIL))
        return self._with_artifacts(
            queue_row_json(row, base_url=self.base_url(host), log_tail=log_tail), row.job
        )

    def _terminal_view(self, job: Job, host: str | None) -> dict[str, Any]:
        """종료 잡의 문서 — 최근 행 모양 + 산출물 + 이름별 이력(M5h)."""
        doc = recent_json(job, base_url=self.base_url(host))
        return self._with_failures(self._with_artifacts(doc, job), job)

    def _with_artifacts(self, doc: dict[str, Any], job: Job) -> dict[str, Any]:
        """잡 행 JSON 에 산출물 처분을 **더한다**. 기존 키는 손대지 않는다(스키마 v1, §10)."""
        doc["artifacts"] = self.artifacts_public(job)
        return doc

    def _with_failures(self, doc: dict[str, Any], job: Job) -> dict[str, Any]:
        """이름별 최근 이력을 **종료된 실패 잡에만** 더한다(M5h · 결정 67).

        `/api/status` 는 이 길로 안 온다 — 최근 행마다 창 질의를 붙이면 이미 가장 뜨거운
        요청 위에 짐을 얹는다(결정 49). 질의가 깨지면 **키를 아예 안 싣는다**: 빈 배열은
        「이름을 안 남겼다」는 뜻이라 「못 읽었다」와 다르다.
        """
        if job.state not in (FAILED, TIMED_OUT):
            return doc
        cfg = self.config.server
        try:
            rows, window, unnamed = self.store.failure_stats(
                job.id, job.key, job.finished_at, window=cfg.failure_window_jobs
            )
        except sqlite3.Error:
            return doc
        doc["failures"] = failures_json(
            rows,
            # 종료 잡에는 `progress` 가 없다 — 스텝 이름으로 아는 것은 이 두 칸뿐이다(§2.3)
            steps={job.failed_step, job.last_step},
            window=window,
            window_unnamed=unnamed,
            min_jobs=cfg.failure_min_jobs,
        )
        doc["failures_truncated"] = job.fail_truncated
        return doc

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
            branch=_opt_str(src.get("branch"), 200),  # 표시용 — 신원에는 안 들어간다 (M5h)
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
                summary, code, args = outcome.summary("snapshot_too_big", bytes=total, limit=limit)
                self.store.finish(
                    job_id,
                    CANCELLED,
                    now=self.now_fn(),
                    summary=summary,
                    summary_code=code,
                    summary_args=args,
                    cancelled_by="server",
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
                **_finish_outcome("snapshot_rejected", kind=type(e).__name__),
                cancelled_by="server",
                only_from=(UPLOADING,),
            )
            self._publish_job(None, job.id)
            raise ApiError(400, "snapshot rejected: not a valid tar.gz") from e
        part.unlink(missing_ok=True)
        absent = missing - got
        if absent:
            summary, _code, _args = outcome.summary("snapshot_blobs_missing", count=len(absent))
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
            summary, code, args = outcome.summary("snapshot_too_big", bytes=length, limit=limit)
            self.store.finish(
                job_id,
                CANCELLED,
                now=self.now_fn(),
                summary=summary,
                summary_code=code,
                summary_args=args,
                cancelled_by="server",
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
            **_finish_outcome("upload_interrupted", bytes=received),
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

    def _advertise_json(self) -> dict[str, Any]:
        r = getattr(self, "responder", None)
        if r is None:
            return {"on": False, "name": None, "error": None}
        return {"on": r.error is None, "name": r.instance.split("._rcm.")[0], "error": r.error}

    def _health_storage(self) -> dict[str, Any]:
        """health 판 회계 — **경로는 안 싣는다**(토큰 없이 열린다).

        예산 초과·바닥 아래는 **503 조건이 아니다.** 다음 sweep 이 할 일이고, 청소기가 죽는 것은
        이미 503 이다. 사실만 싣고 판단은 `rcm check` 와 사람에게 맡긴다.
        """
        doc = self.job_storage()
        free, floor = doc.get("free_bytes"), doc.get("min_free_bytes")
        return {
            "volume_bytes": doc.get("volume_bytes"),
            "free_bytes": free,
            "limit_bytes": doc.get("limit_bytes"),
            "min_free_bytes": floor,
            "last_sweep_at": doc.get("last_sweep_at"),
            "next_sweep_at": doc.get("next_sweep_at"),
            "budget_unreachable": bool(doc.get("budget_unreachable")),
            "no_progress": bool(doc.get("no_progress")),
            "under_floor": bool(floor and free is not None and free < floor),
        }

    # ── 클라이언트 wheel (I8-1) ─────────────────────────────────────────────

    def _build_client_wheel(self) -> None:
        """한 번만 조립한다. `start()` 가 부르고, 안 불렸으면(테스트) 첫 요청이 부른다."""
        with self._wheel_lock:
            if self._wheel_done:
                return
            self._wheel_done = True
            try:
                data = build_wheel(self.version)
            except WheelBuildError as e:
                self._wheel_error = e.code
                self.log(f"client wheel not available: {e}")
                return
            except Exception as e:  # noqa: BLE001 — 조립은 부가 기능이라 서버를 죽이지 않는다
                self._wheel_error = type(e).__name__
                self.log(f"client wheel not available: {_error_text(e)}")
                return
            self._wheel = (data, hashlib.sha256(data).hexdigest())

    def client_wheel(self) -> dict[str, Any] | None:
        """health 의 `client_wheel` — `{path, sha256, bytes}`, 실패면 None(`client_wheel_error`)."""
        self._build_client_wheel()
        if self._wheel is None:
            return None
        data, sha = self._wheel
        path = "/client/" + wheel_filename(self.version)
        return {"path": path, "sha256": sha, "bytes": len(data)}

    def client_wheel_error(self) -> str | None:
        self._build_client_wheel()
        return self._wheel_error

    def client_wheel_bytes(self) -> tuple[bytes, str]:
        """(바이트, sha256). 조립 실패면 503 — 옛 것·빈 것을 주지 않는다(fail-open 금지)."""
        self._build_client_wheel()
        if self._wheel is None:
            raise ApiError(
                503,
                f"client wheel unavailable: {self._wheel_error}",
                client_wheel_error=self._wheel_error,
            )
        return self._wheel

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
            "storage": self._health_storage(),  # 부피 회계(M5g, 정보 — 503 조건은 아니다)
            "pools_without_workers": idle_pools,  # 등록된 원격 워커가 전부 down 인 풀(정보)
            "advertise": self._advertise_json(),  # mDNS 광고 상태(M5c, 정보)
            # 클라이언트가 서버 버전을 따라오는 길(M5i I8 · 결정 81·83, 정보 — 503 조건은 아니다)
            "client_wheel": self.client_wheel(),
            "client_wheel_error": self.client_wheel_error(),
            "min_client_version": MIN_CLIENT_VERSION,
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


def _error_code(e: BaseException) -> str:
    """오류의 **종류**(결정 37). 원문은 `_error_text` 가 만든다 — 화면은 종류를 자기 말로 쓴다."""
    return "database_unavailable" if isinstance(e, sqlite3.Error) else "internal_error"


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
        extra_headers = dict(e.headers)
        if close:
            extra_headers["Connection"] = "close"
        self._send_json(e.status, obj, extra_headers=extra_headers or None)
        if close:
            self.close_connection = True

    # ── 요청 처리 ───────────────────────────────────────────────────────────

    def _dispatch(self) -> None:
        # 요청마다 스레드가 생기고 스레드마다 DB 연결이 생긴다 — 끝나면 꼭 닫는다
        # (안 닫으면 핸들이 쌓여 'Too many open files' → 모든 요청이 500)
        try:
            self._dispatch_inner()
        finally:
            try:
                self.app.store.close()
            except Exception:  # noqa: BLE001
                pass

    def _dispatch_inner(self) -> None:
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
            # 본문을 읽기 전에 거절한 응답(411·413·415)은 연결을 닫는다 — HTTP/1.1 keep-alive 에서
            # 안 읽은 본문이 다음 요청으로 파싱되지 않게
            self._send_error(e, close=e.status in (413, 411, 415))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 — 스택은 로그에만, 응답은 한 줄
            self.app.record_error(
                f"{self.command} {self.path.split('?')[0]}: {type(e).__name__}",
                detail=_safe(str(e)),  # 로그에만 — 공개되는 last_error 는 예외 이름까지다
            )
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
        if path == "/gc":
            self._only(method, "POST")
            self.app.require_admin(self._token())
            self._send_json(200, self.app.gc(self._json_body()))
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
                # 404 가 여기로 보냈다(§3) — 문 앞에서 말이 끊기면 안내가 반쪽이다
                try:
                    t = self._require_read_token()
                except ApiError as e:
                    if e.status in (401, 403):
                        e.extra.setdefault(
                            "hint", f"job logs need that job's token — rcm logs {job_id}"
                        )
                    raise
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
            if sub == "/artifacts":
                self._only(method, "GET")
                # 산출물은 공개 읽기가 아니다 — `read_auth = none` 이어도 토큰을 요구한다(§6)
                self._send_json(200, self.app.artifacts_view(job_id, self._require_read_token()))
                return
            if sub == "/artifacts/archive":
                self._only(method, "GET")
                self._artifact_archive(job_id)
                return
            if sub == "/artifacts/ack":
                self._only(method, "POST")
                t = self.app.require_client_token(self._token())  # 쓰기다 — Bearer 만(CSRF)
                self._send_json(200, self.app.ack_artifacts(job_id, t, self._json_body()))
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
        if path == "/client" or path.startswith("/client/"):
            # 공개 저장소의 코드라 산출물(언제나 토큰)과 달리 `/api/status` 의 읽기 규칙을 따른다
            self._only(method, "GET")
            self._read_only_ok()
            self._client_wheel(path.removeprefix("/client").removeprefix("/"))
            return
        raise ApiError(404, "not found", hint=not_found_hint(path))

    def _artifact_archive(self, job_id: int) -> None:
        """묶음을 흘려보낸다. 전송 슬롯은 **기다리지 않는다** — 일반 슬롯을 쥔 채 기다리면
        heartbeat·cancel·status 가 굶는다(§6)."""
        path, size, jid = self.app.artifact_archive(job_id, self._require_read_token())
        if not self.app.transfer_slots.acquire(blocking=False):
            raise ApiError(
                503,
                "too many concurrent artifact transfers",
                headers={"Retry-After": "5"},
            )
        try:
            with self.app.store.hold_bundle(jid):
                try:
                    fh = path.open("rb")
                except OSError:
                    raise ApiError(503, "artifacts are unavailable") from None
                with fh:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-tar")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="job-{jid}-artifacts.tar"',
                    )
                    self.end_headers()
                    if self.command == "HEAD":
                        return
                    self.connection.settimeout(self.config.server.artifact_transfer_timeout_seconds)
                    try:
                        while True:
                            chunk = fh.read(UPLOAD_CHUNK)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                    finally:
                        self.connection.settimeout(REQUEST_TIMEOUT)
        finally:
            self.app.transfer_slots.release()
            self.app.finalize_bundle_hold(jid)

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
        if what == "artifacts":
            if method == "GET":
                self._send_json(200, self.app.worker_artifacts_status(t, job_id))
                return
            self._only(method, "PUT")
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/x-tar":
                raise ApiError(415, "artifact bundle must be application/x-tar")
            length = self._content_length()
            if not self.app.transfer_slots.acquire(blocking=False):
                raise ApiError(
                    503, "too many concurrent artifact transfers", headers={"Retry-After": "5"}
                )
            self.connection.settimeout(self.config.server.artifact_transfer_timeout_seconds)
            try:
                status, body = self.app.worker_receive_artifacts(t, job_id, self.rfile, length)
            finally:
                self.connection.settimeout(REQUEST_TIMEOUT)
                self.app.transfer_slots.release()
            self._send_json(status, body)
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

    def _client_wheel(self, name: str) -> None:
        """`/client/<파일명>` — 도는 버전의 **정확한 파일명**만 200. 다른 이름은 404 + 맞는 이름.

        pip 는 URL 의 마지막 마디로 파일 종류를 정하므로 이름 없는 별칭은 만들지 않는다.
        """
        expected = wheel_filename(self.app.version)
        if name != expected:
            raise ApiError(
                404, "not found", hint=f"the client wheel for this server is /client/{expected}"
            )
        data, sha = self.app.client_wheel_bytes()
        etag = f'"{sha}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{expected}"')
        self.send_header("Cache-Control", "no-cache")  # ETag 재검증은 살리고 캐시 사용은 막는다
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("ETag", etag)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

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
                if job_id is not None and ev.kind in (KIND_HOST_SAMPLE, KIND_SERVER):
                    # 잡별 스트림은 그 잡의 job_changed·job_finished·marker 만(PLAN).
                    # `server`(레인 상태)는 마커 한 줄에도 발행되므로 여기서 걸러야 한다
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

    def __init__(self, address: tuple[str, int], app: App | None = None):
        # `app` 없이도 만들 수 있다 — `serve()` 는 포트를 **먼저** 잡고 그 다음에 DB 를 연다
        # (M5l L5 · 리뷰 pr-94 B-1). 요청은 `attach()` 뒤 `serve_forever()` 에서야 처리된다.
        self._handler = type("BoundHandler", (Handler,), {"app": None})
        super().__init__(address, self._handler)  # 여기서 bind + listen
        self.app: App | None = None
        self.slots: threading.BoundedSemaphore | None = None
        if app is not None:
            self.attach(app)

    def attach(self, app: App) -> None:
        """바인딩된 소켓에 앱을 붙인다 — 이때부터 핸들러가 `app` 을 본다."""
        self._handler.app = app
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
        if self.app is not None and self.app.debug:
            super().handle_error(request, client_address)


def make_server(app: App, *, bind: str | None = None, port: int | None = None) -> RcmHTTPServer:
    address = (bind or app.config.server.bind, app.config.server.port if port is None else port)
    return RcmHTTPServer(address, app)


def serve(config: ServerConfig, *, debug: bool = False) -> int:
    """`rcm serve` 본체. SIGINT/SIGTERM 으로 멈춘다."""
    data_dir = config.data_dir
    # 순서가 곧 안전장치다: 포트 → DB → 앱. 포트를 못 잡으면(도는 서비스 곁에서 다른 빌드로
    # `rcm serve --config <같은 설정>`) DB 를 열지도 않는다 — 열면 마이그레이션이 먼저 일어나고
    # 옛 서비스는 다음 재시작에서 못 뜬다(2026-09-10 사고 · M5l L5 · 리뷰 pr-94 B-1).
    httpd = RcmHTTPServer((config.server.bind, config.server.port))
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        store = Store(data_dir / "rcm.sqlite3")
    except BaseException:
        httpd.server_close()
        raise
    try:
        app = App(config, store, debug=debug)
        httpd.attach(app)
    except BaseException:
        httpd.server_close()
        store.close()
        raise
    app.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    app.log(
        f"rcm {app.version} listening on http://{host}:{port} · lanes {config.server.lanes} · "
        f"presets {', '.join(p.name for p in config.presets) or '(none)'} · data {data_dir}"
    )
    if config.server.bind not in LOOPBACK_BINDS and config.server.read_auth == "none":
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
