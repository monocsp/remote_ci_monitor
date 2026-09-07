"""워커 API(M5b-2) — `/worker/*` 전 라우트를 HTTP 로 워커 역할을 흉내 내어 잠근다.

명세는 docs/m5b2-workplan.md §1–§4 · §6(이름 고정). 구현 전이라 빨간 것이 정상이다.

- 서버는 in-process HTTP(test_server.Server 와 같은 모양) + **주입한 시계**(`now_fn`) — 시간은
  `srv.clock.advance()` 로만 민다. 로컬 워커 스레드는 띄우지 않는다(`lanes = 1` 인 로컬 레인은
  idle 로만 보인다) — 원격 워커는 이 파일의 HTTP 호출이 전부다.
- 워커 토큰 `build-02` · `build-03`(기본 풀용) · `lin-01`(linux 풀용). 워커 이름 = 토큰 이름.
- 프리셋: `ok`(기본 풀) · `lin`(linux) · `qa`/`qal`(그룹 `devices`, 풀별 하나) · `deploy`(git_ref —
  40 hex ref 는 git 을 부르지 않아 레포 fixture 없이 queued 가 된다).
- 벽시계 sleep 은 long-poll 시험 하나뿐이다
  (`test_claim_long_polls_and_wakes_when_a_job_is_uploaded`).
"""

from __future__ import annotations

import http.client
import io
import json
import socket
import tarfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.config import RepoConfig, ServerConfig, parse_preset
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    DEFAULT_POOL,
    FAILED,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
)
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.server import App, make_server
from remote_ci_monitor.store import Store
from test_server import PRESETS, TAR, TREE_HASH, sh
from test_server_m5 import blobs_for, manifest_of

T0 = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)
TIMEOUT = 60  # [server] worker_timeout_seconds
HEARTBEAT = 5  # [server] worker_heartbeat_seconds
CLAIM_WAIT = 20  # [server] worker_claim_wait_seconds
GRACE = 10  # grace_seconds → cancelling 잡의 kill_at = 취소 시각 + 10초
GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
OCTET = {"Content-Type": "application/octet-stream"}
WORKER_TOKEN_REQUIRED = "worker token required"
NOT_CLIENT = "worker tokens cannot use the client API"
MAX_LOG_BODY = 4 * 1024 * 1024

#: `ok`·`bad`·`slow`·`gate`(기본 풀) 에 풀·그룹·git_ref 프리셋을 더한다.
#: 로컬 워커는 안 띄우므로 어느 것도 실행되지 않는다.
WORKER_PRESETS = [
    *PRESETS,
    sh("lin", "echo linux", pool="linux"),
    sh("qa", "echo qa", concurrency_group="devices"),
    sh("qal", "echo qa on linux", concurrency_group="devices", pool="linux"),
    sh("deploy", "echo deploy", source_modes=["git_ref"], repo="app"),
]

#: 워커가 보내는 호스트 표본 — `hosts[]` 항목 모양. `name`·`source`·`sampled_at` 은 서버가 덮어쓴다.
SAMPLE: dict[str, Any] = {
    "name": "spoofed-name",
    "source": "local",
    "sampled_at": "2020-01-01T00:00:00Z",
    "interval_seconds": 5,
    "os": "linux",
    "cores": 8,
    "load": [1.5, 1.0, 0.5],
    "cpu": {"user": 10.0, "sys": 2.5, "idle": 87.5, "busy": 12.5},
    "memory": {
        "total_bytes": 16_000_000_000,
        "used_bytes": 4_000_000_000,
        "compressed_bytes": None,
    },
    "gpu": None,
    "gpu_note": "nvidia-smi not found",
    "top": [{"comm": "cc1", "cpu": 90.0, "rss_mb": 512}],
    "history": [
        {
            "at": "2020-01-01T00:00:00Z",
            "cpu_busy": 12.5,
            "mem_used_bytes": 4_000_000_000,
            "gpu_util_pct": None,
        }
    ],
}


# ── 도우미 ───────────────────────────────────────────────────────────────────


class Clock:
    """주입하는 시계 — 테스트가 `advance` 로만 시간을 민다(벽시계 sleep 없음)."""

    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class WorkerServer:
    """in-process HTTP 서버 + 주입 시계 + client/admin/worker 토큰.

    로컬 워커 스레드는 `workers=True` 일 때만 띄운다(기본은 안 띄운다 — 다른 서버 테스트와 같다).
    """

    def __init__(
        self, tmp_path: Path, *, workers: bool = False, lanes: int = 1, **server_overrides: Any
    ):
        cfg = ServerConfig()
        cfg.server.data_dir = str(tmp_path / "data")
        cfg.server.lanes = lanes
        cfg.server.grace_seconds = GRACE
        cfg.server.max_snapshot_bytes = 10_000
        cfg.server.snapshot_cache = True
        cfg.server.worker_timeout_seconds = TIMEOUT
        cfg.server.worker_heartbeat_seconds = HEARTBEAT
        cfg.server.worker_claim_wait_seconds = CLAIM_WAIT
        for k, v in server_overrides.items():
            setattr(cfg.server, k, v)
        cfg.repos = (RepoConfig(name="app", url=str(tmp_path / "nowhere.git")),)
        cfg.presets = tuple(parse_preset(p) for p in WORKER_PRESETS)
        self.cfg = cfg
        self.clock = Clock()
        self.store = Store(cfg.data_dir / "rcm.sqlite3")
        self.tokens: dict[str, str] = {
            "alice": self.store.add_token("alice-laptop", admin=False, now=T0),
            "bob": self.store.add_token("bob-desk", admin=False, now=T0),
            "admin": self.store.add_token("macmini-admin", admin=True, now=T0),
        }
        for name in ("build-02", "build-03", "lin-01"):
            self.worker_token(name)
        self.app = App(cfg, self.store, now_fn=self.clock)
        self.httpd = make_server(self.app, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        )
        self.thread.start()
        if workers:
            self.app.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.shutdown()
        self.store.close()

    def worker_token(self, name: str) -> str:
        """워커 토큰 하나(kind=worker). 워커 이름은 토큰 이름이다(§2)."""
        secret = self.store.add_token(name, admin=False, now=T0, kind="worker")
        self.tokens[name] = secret
        return secret

    # ── HTTP ──

    def req(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: Any = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = dict(headers or {})
        if token:
            hdrs["Authorization"] = f"Bearer {self.tokens.get(token, token)}"
        data = body
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        if data is not None and "Content-Length" not in hdrs and "Transfer-Encoding" not in hdrs:
            hdrs["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        if raw:
            return resp.status, dict(resp.getheaders()), payload
        try:
            return resp.status, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return resp.status, payload

    # ── 클라이언트 쪽(잡 만들기) ──

    def new_job(
        self,
        token: str = "alice",
        *,
        preset: str = "ok",
        priority: Any = None,
        pool: str | None = None,
        tree_hash: str = TREE_HASH,
    ) -> int:
        """tree 잡 제출(uploading). 합류는 끈다 — 같은 트리를 여러 번 넣어도 새 잡."""
        body: dict[str, Any] = {
            "preset": preset,
            "inputs": {},
            "source": {
                "mode": "tree",
                "repo": "org/app",
                "base_sha": "abc123f",
                "dirty": True,
                "tree_hash": tree_hash,
                "bytes": len(TAR),
            },
            "requester_label": f"{token}@host",
            "join": False,
        }
        if priority is not None:
            body["priority"] = priority
        if pool is not None:
            body["pool"] = pool
        status, resp = self.req("POST", "/jobs", token=token, json_body=body)
        assert status == 201, resp
        return int(resp["job_id"])

    def queued_job(self, token: str = "alice", **kw: Any) -> int:
        """제출 + tar 업로드 → queued."""
        jid = self.new_job(token, **kw)
        status, resp = self.req("PUT", f"/jobs/{jid}/tree", token=token, body=TAR)
        assert status == 200 and resp["state"] == QUEUED, resp
        return jid

    def git_ref_job(self, token: str = "alice") -> int:
        """git_ref 잡 — 40 hex ref 는 원격을 부르지 않으므로 바로 queued."""
        body = {
            "preset": "deploy",
            "inputs": {},
            "source": {"mode": "git_ref", "ref": GIT_SHA},
            "requester_label": f"{token}@host",
            "join": False,
        }
        status, resp = self.req("POST", "/jobs", token=token, json_body=body)
        assert status == 201 and resp["state"] == QUEUED, resp
        return int(resp["job_id"])

    def cache_job(
        self,
        files: dict[str, bytes],
        links: dict[str, str] | None = None,
        *,
        exec_paths: tuple[str, ...] = (),
        token: str = "alice",
    ) -> int:
        """캐시 업로드(manifest → missing → blob PUT) 로 queued 가 된 잡. tree.tar.gz 는 없다."""
        jid = self.new_job(token)
        manifest = manifest_of(files, links, exec_paths=exec_paths)
        status, m = self.req("POST", f"/jobs/{jid}/tree/manifest", token=token, json_body=manifest)
        assert status == 200, m
        if m["missing"]:
            status, resp = self.req(
                "PUT",
                f"/jobs/{jid}/tree",
                token=token,
                body=blobs_for(files, m["missing"]),
                headers={"X-RCM-Tree": "blobs"},
            )
            assert status == 200, resp
        assert self.store.get_job(jid).state == QUEUED
        assert not (self.app.job_dir(jid) / "tree.tar.gz").exists()
        return jid

    def cancel(self, jid: int, token: str = "alice"):
        return self.req("POST", f"/jobs/{jid}/cancel", token=token, json_body={})

    # ── 워커 쪽 ──

    def register(
        self,
        worker: str,
        *,
        pool: Any = DEFAULT_POOL,
        lanes: Any = 1,
        version: str = __version__,
        host_name: str | None = None,
    ):
        body = {
            "pool": pool,
            "lanes": lanes,
            "host_name": host_name or f"{worker}.local",
            "version": version,
        }
        return self.req("POST", "/worker/register", token=worker, json_body=body)

    def registered(self, worker: str, **kw: Any) -> dict[str, Any]:
        status, body = self.register(worker, **kw)
        assert status == 200, body
        return body

    def claim(self, worker: str, lane: int = 1, *, wait_seconds: int = 0):
        body = {"lane": lane, "wait_seconds": wait_seconds}
        return self.req("POST", "/worker/claim", token=worker, json_body=body)

    def claimed(self, worker: str, lane: int = 1) -> int:
        status, body = self.claim(worker, lane)
        assert status == 200, (status, body)
        return int(body["job"]["id"])

    def heartbeat(
        self,
        worker: str,
        *,
        jobs: list[int] | None = None,
        host_sample: Any = None,
    ):
        """`jobs` 를 안 주면 키 자체를 안 보낸다(선택 필드) — `[]` 와는 다르다."""
        body: dict[str, Any] = {}
        if jobs is not None:
            body["jobs"] = jobs
        if host_sample is not None:
            body["host_sample"] = host_sample
        return self.req("POST", "/worker/heartbeat", token=worker, json_body=body)

    def phase(self, worker: str, jid: int, phase: Any):
        return self.req(
            "POST", f"/worker/jobs/{jid}/phase", token=worker, json_body={"phase": phase}
        )

    def log(self, worker: str, jid: int, data: bytes, headers: dict[str, str] | None = None):
        return self.req(
            "POST",
            f"/worker/jobs/{jid}/log",
            token=worker,
            body=data,
            headers={**OCTET, **(headers or {})},
        )

    def finish(
        self,
        worker: str,
        jid: int,
        outcome: Any,
        *,
        exit_code: Any = None,
        summary: str | None = None,
    ):
        body: dict[str, Any] = {"outcome": outcome, "exit_code": exit_code}
        if summary is not None:
            body["summary"] = summary
        return self.req("POST", f"/worker/jobs/{jid}/finish", token=worker, json_body=body)

    def tree(self, worker: str, jid: int):
        return self.req("GET", f"/worker/jobs/{jid}/tree", token=worker, raw=True)

    # ── 상태 읽기 ──

    def status(self) -> dict[str, Any]:
        status, doc = self.req("GET", "/api/status")
        assert status == 200, doc
        return doc

    def pools(self) -> dict[str, dict[str, Any]]:
        return {p["name"]: p for p in self.status()["pools"]}

    def workers(self) -> list[dict[str, Any]]:
        return self.status()["server"]["workers"]

    def worker_lane(self, name: str | None, lane: int) -> dict[str, Any]:
        """`server.workers[]` 에서 (워커 이름, 레인) 항목 하나. 로컬 레인은 `name=None`."""
        found = [w for w in self.workers() if w["worker"] == name and w["lane"] == lane]
        assert len(found) == 1, (name, lane, self.workers())
        return found[0]

    def row(self, jid: int) -> dict[str, Any]:
        for pool in self.status()["pools"]:
            for r in pool["queue"] or []:
                if r["id"] == jid:
                    return r
        raise AssertionError(f"job {jid} is not in any queue")

    def view(self, jid: int, token: str | None = None) -> dict[str, Any]:
        status, body = self.req("GET", f"/jobs/{jid}", token=token)
        assert status == 200, body
        return body

    def log_text(self, jid: int) -> str:
        status, _headers, data = self.req("GET", f"/jobs/{jid}/log", token="admin", raw=True)
        assert status == 200, data
        return data.decode("utf-8")


def tar_members(data: bytes) -> dict[str, tarfile.TarInfo]:
    """tar.gz 바이트의 파일·심링크 멤버(디렉터리는 뺀다)."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        return {m.name: m for m in tf.getmembers() if m.isfile() or m.issym()}


def tar_read(data: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        fh = tf.extractfile(name)
        assert fh is not None, name
        return fh.read()


def raw_http(port: int, request: bytes) -> tuple[int, bytes]:
    """헤더를 직접 써서 보낸다(Content-Length 를 뺀 요청 등). (상태, 본문)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.sendall(request)
        resp = http.client.HTTPResponse(s, method="POST")
        resp.begin()
        return resp.status, resp.read()
    finally:
        s.close()


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


def running_job(srv: WorkerServer, worker: str = "build-02", **kw: Any) -> int:
    """워커 등록 + queued 잡 하나 + claim 까지. 보고 라우트 시험의 출발점."""
    jid = srv.queued_job(**kw)
    srv.registered(worker)
    assert srv.claimed(worker) == jid
    return jid


# ── 인증 규칙 (§1 · §6 인증) ─────────────────────────────────────────────────

WORKER_ROUTES = [
    ("POST", "/worker/register"),
    ("POST", "/worker/claim"),
    ("POST", "/worker/heartbeat"),
    ("GET", "/worker/jobs/1/tree"),
    ("POST", "/worker/jobs/1/phase"),
    ("POST", "/worker/jobs/1/log"),
    ("POST", "/worker/jobs/1/finish"),
]


def _call_worker_route(srv: WorkerServer, method: str, path: str, token: str | None):
    if path.endswith("/log"):
        return srv.req(method, path, token=token, body=b"x", headers=OCTET, raw=True)
    if method == "GET":
        return srv.req(method, path, token=token, raw=True)
    return srv.req(method, path, token=token, json_body={}, raw=True)


@pytest.mark.parametrize(("method", "path"), WORKER_ROUTES)
def test_worker_routes_require_a_worker_token(srv, method, path):
    """§1: `/worker/*` 는 워커 토큰만 — 토큰 없음·무효는 401(Bearer 챌린지), client·admin 은 403
    `worker token required`. 인증이 본문 검증·잡 존재 확인보다 먼저다(잡 1 은 없다)."""
    status, headers, raw = _call_worker_route(srv, method, path, None)
    assert status == 401, raw
    assert headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert set(json.loads(raw)) == {"error"}
    status, _headers, raw = _call_worker_route(srv, method, path, "garbage-token")
    assert status == 401, raw
    for who in ("alice", "admin"):
        status, _headers, raw = _call_worker_route(srv, method, path, who)
        assert status == 403, (who, raw)
        assert json.loads(raw) == {"error": WORKER_TOKEN_REQUIRED}


CLIENT_ROUTES = [
    ("POST", "/jobs", {"preset": "ok"}),
    ("POST", "/jobs/1/cancel", {}),
    ("POST", "/jobs/1/priority", {"priority": "high"}),
    ("POST", "/jobs/1/tree/manifest", {"files": []}),
    ("PUT", "/jobs/1/tree", None),
    ("POST", "/pause", {}),
    ("POST", "/resume", {}),
    ("POST", "/api/eta", {"preset": "ok"}),
]


@pytest.mark.parametrize(("method", "path", "body"), CLIENT_ROUTES)
def test_worker_token_cannot_use_the_client_api(srv, method, path, body):
    """§1: 워커 토큰으로 클라이언트 라우트(제출·취소·우선순위·업로드·정지·eta)를 부르면 403
    `worker tokens cannot use the client API`. `/api/eta` 는 토큰이 없어도 되는 라우트지만 워커
    토큰을 내밀면 거절한다(§6). 아무것도 바뀌지 않는다."""
    if body is None:
        status, resp = srv.req(method, path, token="build-02", body=b"x")
    else:
        status, resp = srv.req(method, path, token="build-02", json_body=body)
    assert status == 403, resp
    assert resp == {"error": NOT_CLIENT}
    assert srv.store.list_active() == [] and srv.store.get_paused() is None


def test_worker_token_still_reads_status_under_basic_read_auth(tmp_path):
    """§1: 읽기 라우트는 `read_auth` 규칙 그대로 — basic 모드에서 워커 토큰(Bearer)으로
    `/api/status` 를 읽을 수 있고, `/api/health` 는 늘 열려 있다."""
    s = WorkerServer(tmp_path, read_auth="basic")
    try:
        assert s.req("GET", "/api/status")[0] == 401
        assert s.req("GET", "/api/status", token="build-02")[0] == 200
        assert s.req("GET", "/api/health")[0] == 200
        status, body = s.req("GET", "/api/whoami", token="build-02")
        assert status == 200 and body["name"] == "build-02"
    finally:
        s.close()


# ── register (§2) ────────────────────────────────────────────────────────────


def test_register_new_worker_returns_the_protocol_parameters(srv):
    """§2: `POST /worker/register` → 200 `{name, pool, lanes, heartbeat_seconds,
    worker_timeout_seconds, claim_wait_seconds}` — name 은 토큰 이름. 저장 행은 `registered_at`
    = `last_seen_at` = 서버 시각. 레인은 `server.workers[]` 에 `<name>/<lane>` 으로, 그 풀은
    `pools[]` 에 (잡이 없어도) lanes 와 함께 보인다."""
    status, body = srv.register("build-02", pool="linux", lanes=2, host_name="b02.local")
    assert status == 200, body
    assert body == {
        "name": "build-02",
        "pool": "linux",
        "lanes": 2,
        "heartbeat_seconds": HEARTBEAT,
        "worker_timeout_seconds": TIMEOUT,
        "claim_wait_seconds": CLAIM_WAIT,
    }
    row = srv.store.get_worker("build-02")
    assert row is not None
    assert (row.name, row.pool, row.lanes, row.host_name) == ("build-02", "linux", 2, "b02.local")
    assert row.version == __version__
    assert row.registered_at == T0 and row.last_seen_at == T0
    lanes = [w for w in srv.workers() if w["worker"] == "build-02"]
    assert [(w["lane"], w["display_name"], w["state"], w["job_id"], w["since"]) for w in lanes] == [
        (1, "build-02/1", "idle", None, iso(T0)),
        (2, "build-02/2", "idle", None, iso(T0)),
    ]
    assert all(isinstance(w["lane"], int) for w in lanes)  # lane 은 int 유지(스키마)
    local = srv.worker_lane(None, 1)
    assert local["display_name"] is None and local["state"] == "idle"
    pools = srv.pools()
    assert pools["linux"]["lanes"] == 2 and pools["default"]["lanes"] == 1
    assert srv.status()["server"]["lanes"] == 1  # server.lanes 는 로컬 레인 수 그대로


def test_register_again_updates_lanes_pool_and_last_seen(srv):
    """§2·§3: 이미 있으면 갱신(upsert) — 풀을 바꿔도 200(등록 시점엔 그 워커의 잡이 없다, §3
    「워커 재시작」). `last_seen_at` 은 갱신 시각. 행은 하나뿐이다."""
    srv.registered("build-02", pool="linux", lanes=2)
    srv.clock.advance(30)
    body = srv.registered("build-02", pool=DEFAULT_POOL, lanes=3)
    assert body["name"] == "build-02" and body["pool"] == DEFAULT_POOL and body["lanes"] == 3
    rows = srv.store.list_workers()
    assert [(r.name, r.pool, r.lanes) for r in rows] == [("build-02", DEFAULT_POOL, 3)]
    assert rows[0].last_seen_at == at(30)
    pools = srv.pools()
    assert pools["default"]["lanes"] == 1 + 3
    assert pools.get("linux", {"lanes": 0})["lanes"] == 0
    assert [w["display_name"] for w in srv.workers()] == [
        None,
        "build-02/1",
        "build-02/2",
        "build-02/3",
    ]


def test_register_rejects_a_different_release(srv):
    """§2: `version` ≠ 서버 `__version__` → 409 `worker version X, server Y — install the same
    release`. 등록되지 않으므로 claim 은 409 `worker <name> is not registered`."""
    status, body = srv.register("build-02", version="0.0.1")
    assert status == 409, body
    assert body["error"].startswith(f"worker version 0.0.1, server {__version__}")
    assert "install the same release" in body["error"]
    assert srv.store.get_worker("build-02") is None
    status, body = srv.claim("build-02")
    assert status == 409 and body["error"] == "worker build-02 is not registered"


@pytest.mark.parametrize(
    "bad",
    [
        {"pool": "bad pool"},
        {"pool": ""},
        {"pool": "-x"},
        {"pool": 5},
        {"lanes": 0},
        {"lanes": 65},
        {"lanes": "2"},
        {"lanes": True},
    ],
)
def test_register_rejects_bad_pool_names_and_lane_counts(srv, bad):
    """§2: `pool` 은 이름 규칙(프리셋 풀과 같다) · `lanes` 는 1~64 의 정수(bool·문자열은 아니다)
    — 어긋나면 400 이고 등록되지 않는다."""
    status, body = srv.register("build-02", **bad)
    assert status == 400 and set(body) == {"error"}, (bad, body)
    assert srv.store.get_worker("build-02") is None


def test_register_marks_the_workers_running_jobs_lost(srv):
    """§3 「워커 재시작」: register 는 새 프로세스라는 뜻 — 그 워커 이름의 running·cancelling 잡을
    먼저 lost(summary `worker <name> restarted without the job`) 로 닫고 등록한다. 레인이 비어
    새 claim 이 겹치지 않는다. 다른 워커의 잡은 건드리지 않는다."""
    a = srv.queued_job()
    b = srv.queued_job(token="bob")
    c = srv.queued_job(token="admin")
    srv.registered("build-02", lanes=2)
    srv.registered("build-03")
    assert srv.claimed("build-02", 1) == a
    assert srv.claimed("build-02", 2) == b
    assert srv.claimed("build-03", 1) == c
    assert srv.cancel(b, token="bob")[1]["state"] == CANCELLING
    srv.clock.advance(20)
    srv.registered("build-02", lanes=2)  # 재시작
    for jid in (a, b):
        j = srv.store.get_job(jid)
        assert j.state == LOST, (jid, j.state)
        assert j.summary == "worker build-02 restarted without the job"
        assert j.finished_at == at(20)
    assert srv.store.get_job(c).state == RUNNING  # build-03 의 잡은 그대로
    assert srv.worker_lane("build-02", 1)["state"] == "idle"
    assert srv.worker_lane("build-02", 2)["state"] == "idle"
    d = srv.queued_job()
    assert srv.claimed("build-02", 1) == d  # 레인 1 은 비었다
    assert srv.store.get_job(d).worker_name == "build-02"


# ── claim (§3) ───────────────────────────────────────────────────────────────


def test_claim_returns_204_when_empty_and_the_job_with_preset_when_not(srv):
    """§3: 빈 풀 + `wait_seconds: 0` → 204(본문 없음). 잡이 있으면 200
    `{job: <queue 행 JSON>, tree_url, preset: {argv, timeout_seconds, env, env_passthrough,
    source_modes, repo?}}` — 워커가 실행에 필요한 전부(프리셋 argv 는 여기서만 워커에 간다).
    잡은 running · `worker_name`/`lane` 이 붙고 `server.workers[]` 에 busy 로 보인다."""
    srv.registered("build-02")
    status, body = srv.claim("build-02")
    assert status == 204 and body is None
    jid = srv.queued_job()
    srv.clock.advance(10)
    status, body = srv.claim("build-02")
    assert status == 200, body
    assert set(body) == {"job", "tree_url", "preset"}
    job = body["job"]
    assert job["id"] == jid and job["preset"] == "ok" and job["pool"] == DEFAULT_POOL
    assert job["priority"] == 0 and job["inputs"] == {} and job["concurrency_group"] is None
    assert job["requester"] == {"name": "alice-laptop", "label": "alice@host"}
    assert job["source"]["mode"] == "tree" and job["source"]["tree_hash"] == TREE_HASH
    assert job["state"] == RUNNING and job["lane"] == 1
    assert body["tree_url"] == f"/worker/jobs/{jid}/tree"
    preset = body["preset"]
    ok = srv.cfg.preset("ok")
    assert preset["argv"] == list(ok.argv) and preset["timeout_seconds"] == 60
    assert preset["env"] == {} and preset["env_passthrough"] == ["PATH", "HOME", "LANG"]
    assert preset["source_modes"] == ["tree"] and not preset.get("repo")
    # DB · 상태 JSON
    j = srv.store.get_job(jid)
    assert j.state == RUNNING and j.worker_name == "build-02" and j.lane == 1
    assert j.started_at == at(10)
    row = srv.row(jid)
    assert row["state"] == RUNNING and row["lane"] == 1 and row["reason"] == "materializing"
    assert row["progress"]["phase"] == "materializing"
    lane = srv.worker_lane("build-02", 1)
    assert lane == {
        "lane": 1,
        "state": "busy",
        "job_id": jid,
        "error": None,
        "since": iso(at(10)),
        "worker": "build-02",
        "display_name": "build-02/1",
    }
    assert srv.worker_lane(None, 1)["state"] == "idle"  # 로컬 레인은 놀고 있다
    assert srv.claim("build-02")[0] == 409  # 같은 레인은 잡을 하나만


def test_claim_is_isolated_per_pool(srv):
    """§3: claim 은 **등록된 풀**의 queued 잡만 — linux 워커는 기본 풀 잡을, 기본 풀 워커는 linux
    잡을 절대 받지 않는다."""
    ok = srv.queued_job()
    lin = srv.queued_job(token="bob", preset="lin")
    srv.registered("lin-01", pool="linux", lanes=2)
    srv.registered("build-02", lanes=2)
    assert srv.claimed("lin-01", 1) == lin
    assert srv.claim("lin-01", 2)[0] == 204  # 기본 풀의 `ok` 는 안 보인다
    assert srv.claimed("build-02", 1) == ok
    assert srv.claim("build-02", 2)[0] == 204
    assert srv.store.get_job(ok).worker_name == "build-02"
    assert srv.store.get_job(lin).worker_name == "lin-01"
    assert srv.row(lin)["pool"] == "linux" and srv.row(ok)["pool"] == DEFAULT_POOL


def test_group_exclusion_applies_within_the_pool_only(srv):
    """§3(M5b 모델): 그룹 배제는 **풀 안에서** — 같은 그룹 `devices` 라도 다른 풀의 잡은 동시에
    돈다. 그룹 보유 잡이 끝나면 다음 claim 이 막혔던 잡을 받는다."""
    a = srv.queued_job(preset="qa")
    b = srv.queued_job(token="bob", preset="qa")
    c = srv.queued_job(token="admin", preset="qal")
    srv.registered("build-02", lanes=2)
    srv.registered("lin-01", pool="linux")
    assert srv.claimed("build-02", 1) == a
    assert srv.claim("build-02", 2)[0] == 204  # b 는 그룹에 막혔다
    assert srv.claimed("lin-01", 1) == c  # 다른 풀 — 그룹 이름이 같아도 막히지 않는다
    row = srv.row(b)
    assert row["reason"] == "blocked_by_group" and row["blocked_by"]["job_id"] == a
    assert srv.finish("build-02", a, "succeeded", exit_code=0)[0] == 200
    assert srv.claimed("build-02", 2) == b


def test_claim_refuses_a_lane_that_already_has_a_job(srv):
    """§3(리뷰 반영 — 레인 과할당 금지): 그 `(worker, lane)` 에 running·cancelling 잡이 있으면
    409 `lane N already has job #M`. 다른 레인은 된다."""
    a = srv.queued_job()
    b = srv.queued_job(token="bob")
    srv.registered("build-02", lanes=2)
    assert srv.claimed("build-02", 1) == a
    status, body = srv.claim("build-02", 1)
    assert status == 409 and body["error"] == f"lane 1 already has job #{a}", body
    assert srv.store.get_job(b).state == QUEUED
    assert srv.cancel(a)[1]["state"] == CANCELLING  # 취소 진행 중인 잡도 레인을 잡고 있다
    status, body = srv.claim("build-02", 1)
    assert status == 409 and body["error"] == f"lane 1 already has job #{a}"
    assert srv.claimed("build-02", 2) == b


@pytest.mark.parametrize("lane", [0, 3, -1, "1", True])
def test_claim_validates_the_lane_against_the_registered_count(srv, lane):
    """§3: `1 ≤ lane ≤ 등록 lanes` 가 아니면(정수가 아니어도) 400 — 잡은 그대로 queued."""
    jid = srv.queued_job()
    srv.registered("build-02", lanes=2)
    body: dict[str, Any] = {"wait_seconds": 0, "lane": lane}
    status, resp = srv.req("POST", "/worker/claim", token="build-02", json_body=body)
    assert status == 400 and "lane" in resp["error"], (lane, resp)
    assert srv.store.get_job(jid).state == QUEUED


def test_claim_and_heartbeat_need_a_registered_worker(srv):
    """§3: 등록 전의 워커 토큰은 claim·heartbeat 모두 409 `worker <name> is not registered`."""
    jid = srv.queued_job()
    status, body = srv.claim("build-03")
    assert status == 409 and body["error"] == "worker build-03 is not registered"
    status, body = srv.heartbeat("build-03", jobs=[])
    assert status == 409 and body["error"] == "worker build-03 is not registered"
    assert srv.store.get_job(jid).state == QUEUED


def test_claim_hands_out_jobs_in_priority_then_id_order(srv):
    """§3: `store.claim` 의 순서(우선순위 높은 것 먼저, 같으면 id) 그대로 — low·normal·high 를
    넣으면 high → normal → low."""
    low = srv.queued_job(priority="low")
    normal = srv.queued_job(token="bob")
    high = srv.queued_job(token="admin", priority="high")
    srv.registered("build-02", lanes=3)
    assert [srv.claimed("build-02", n) for n in (1, 2, 3)] == [high, normal, low]


def test_claim_hands_out_nothing_while_the_server_is_paused(srv):
    """§3: 정지 중엔 claim 이 잡을 주지 않는다(로컬 워커와 같다) — 204. heartbeat 의 `paused` 가
    이를 알린다. resume 뒤에는 준다."""
    jid = srv.queued_job()
    srv.registered("build-02")
    assert srv.req("POST", "/pause", token="admin", json_body={})[0] == 200
    assert srv.claim("build-02")[0] == 204
    assert srv.heartbeat("build-02")[1]["paused"] is True
    assert srv.req("POST", "/resume", token="admin", json_body={})[0] == 200
    assert srv.claimed("build-02") == jid


def test_claim_long_polls_and_wakes_when_a_job_is_uploaded(srv):
    """§3: 빈 풀의 claim 은 `wait_seconds` 까지 `wake` 를 기다리다 잡이 오면 **바로** 돌려준다 —
    2초를 주고 0.3초 뒤에 올리면 1.5초 안에 200. (이 파일의 유일한 벽시계 시험)"""
    srv.registered("build-02")
    ids: list[int] = []
    timer = threading.Timer(0.3, lambda: ids.append(srv.queued_job()))
    t0 = time.monotonic()
    timer.start()
    status, body = srv.claim("build-02", wait_seconds=2)
    elapsed = time.monotonic() - t0
    timer.join(5)
    assert status == 200, (status, body)
    assert ids and body["job"]["id"] == ids[0]
    assert 0.2 <= elapsed < 1.5, elapsed


# ── tree (§3) ────────────────────────────────────────────────────────────────


def test_tree_returns_the_uploaded_tar_with_length_and_gzip_type(srv):
    """§3: `GET /worker/jobs/{id}/tree` → 올린 tar.gz 바이트 그대로, `Content-Length` 와
    `Content-Type: application/gzip`."""
    jid = running_job(srv)
    status, headers, data = srv.tree("build-02", jid)
    assert status == 200, data
    assert data == TAR
    assert headers["Content-Length"] == str(len(TAR))
    assert headers["Content-Type"] == "application/gzip"


def test_tree_of_a_cache_job_is_assembled_from_manifest_and_blobs(srv):
    """§3(리뷰 반영): 캐시 잡(manifest)은 서버가 manifest + blob 으로 tar.gz 를 조립해 준다 —
    멤버(경로·크기·내용·실행 비트·심링크)가 manifest 와 같고 `Content-Length` 가 붙는다. 조립본은
    `jobs/<id>/tree.tar.gz` 에 남아 두 번째 요청은 같은 바이트다(§6)."""
    files = {"a.txt": b"aaa", "bin/run.sh": b"#!/bin/sh\necho hi\n", "sub/deep/c.txt": b"cc"}
    links = {"link.txt": "a.txt"}
    jid = srv.cache_job(files, links, exec_paths=("bin/run.sh",))
    srv.registered("build-02")
    assert srv.claimed("build-02") == jid
    status, headers, data = srv.tree("build-02", jid)
    assert status == 200, data
    assert headers["Content-Type"] == "application/gzip"
    assert headers["Content-Length"] == str(len(data))
    members = tar_members(data)
    assert set(members) == set(files) | set(links)
    for path, content in files.items():
        m = members[path]
        assert m.isfile() and m.size == len(content), path
        assert tar_read(data, path) == content
    assert members["bin/run.sh"].mode & 0o111
    assert not members["a.txt"].mode & 0o111
    assert members["link.txt"].issym() and members["link.txt"].linkname == "a.txt"
    assert (srv.app.job_dir(jid) / "tree.tar.gz").is_file()
    status, _headers, again = srv.tree("build-02", jid)
    assert status == 200 and again == data


def test_tree_of_a_git_ref_job_is_404(srv):
    """§3: git_ref 잡은 워커가 fetch 한다 — 404 `git_ref jobs are fetched by the worker`. claim
    응답은 `source` 에 repo·ref·sha 를, `preset` 에 `repo` 와 `source_modes` 를 싣는다."""
    jid = srv.git_ref_job()
    srv.registered("build-02")
    status, body = srv.claim("build-02")
    assert status == 200 and body["job"]["id"] == jid, body
    assert body["job"]["source"] == {
        "mode": "git_ref",
        "repo": "app",
        "ref": GIT_SHA,
        "sha": GIT_SHA,
    }
    assert body["preset"]["source_modes"] == ["git_ref"] and body["preset"]["repo"] == "app"
    status, _headers, data = srv.tree("build-02", jid)
    assert status == 404, data
    assert json.loads(data) == {"error": "git_ref jobs are fetched by the worker"}


def test_tree_is_only_for_the_worker_that_claimed_the_job(srv):
    """§3: 다른 워커의 잡(또는 아직 아무도 안 잡은 잡)은 403, 클라이언트 토큰은 403 `worker token
    required`, 없는 잡은 404."""
    jid = running_job(srv)
    other = srv.queued_job(token="bob")
    srv.registered("build-03")
    assert srv.tree("build-03", jid)[0] == 403
    assert srv.tree("build-03", other)[0] == 403  # queued — worker_name 이 없다
    assert srv.tree("build-02", other)[0] == 403
    status, _headers, data = srv.tree("alice", jid)
    assert status == 403 and json.loads(data) == {"error": WORKER_TOKEN_REQUIRED}
    assert srv.tree("build-02", 999)[0] == 404


# ── phase (§3) ───────────────────────────────────────────────────────────────


def test_phase_is_reflected_in_status_and_validated(srv):
    """§3: `POST /worker/jobs/{id}/phase {phase}` — `materializing`·`executing` 만. 큐 행의
    `progress.phase`·`reason` 이 따라간다. 모르는 phase 는 400, 다른 워커는 403, 없는 잡은 404."""
    jid = running_job(srv)
    assert srv.row(jid)["progress"]["phase"] == "materializing"  # claim 직후
    assert srv.phase("build-02", jid, "executing")[0] == 200
    row = srv.row(jid)
    assert row["progress"]["phase"] == "executing" and row["reason"] == "running"
    assert srv.store.get_job(jid).phase == "executing"
    assert srv.phase("build-02", jid, "materializing")[0] == 200
    assert srv.row(jid)["progress"]["phase"] == "materializing"
    for bad in ("cooking", "", None, 3):
        status, body = srv.phase("build-02", jid, bad)
        assert status == 400 and "phase" in body["error"], (bad, body)
    assert srv.store.get_job(jid).phase == "materializing"
    srv.registered("build-03")
    assert srv.phase("build-03", jid, "executing")[0] == 403
    assert srv.phase("build-02", 999, "executing")[0] == 404


# ── log (§3 · §6 로그) ───────────────────────────────────────────────────────


def test_log_appends_raw_bytes_and_parses_markers_on_the_server(srv):
    """§3(리뷰 반영): 본문 raw 바이트를 `jobs/<id>/log.txt` 에 append 하고 **서버가** 줄 단위로
    `parse_marker` → 마커·이벤트. 마커 시각은 서버 수신 시각, `last_output_at` 도 갱신된다.
    클라이언트는 `GET /jobs/{id}/log` 로 같은 바이트를 본다."""
    jid = running_job(srv)
    assert srv.phase("build-02", jid, "executing")[0] == 200
    first = b"::rcm::steps::2\n::rcm::step::build\nhello world\n"
    assert srv.log("build-02", jid, first)[0] == 200
    assert srv.log_text(jid) == first.decode()
    p = srv.row(jid)["progress"]
    assert p["steps_total"] == 2 and p["steps_total_partial"] is False
    assert p["current_name"] == "build" and p["current_index"] == 1 and p["steps_done"] == 0
    assert p["last_output_at"] == iso(T0)
    srv.clock.advance(7)
    second = b"::rcm::step::test\n"
    assert srv.log("build-02", jid, second)[0] == 200
    assert srv.log_text(jid) == (first + second).decode()
    p = srv.row(jid)["progress"]
    assert p["current_name"] == "test" and p["steps_done"] == 1
    assert p["steps"][0] == {"index": 1, "name": "build", "state": "done", "ok": True, "seconds": 7}
    assert p["last_output_at"] == iso(at(7))
    markers = srv.store.markers(jid)
    assert [(m.kind, m.value, m.at) for m in markers] == [
        ("steps", "2", T0),
        ("step", "build", T0),
        ("step", "test", at(7)),
    ]


def test_log_joins_a_marker_split_across_two_requests(srv):
    """§6 로그: 마지막 조각 줄은 다음 요청과 이어 붙인다(`App._log_partial[job_id]`) — 개행이 오기
    전엔 마커가 아니고, 오면 하나의 마커가 된다. 파일에는 조각도 그대로 append 된다."""
    jid = running_job(srv)
    assert srv.log("build-02", jid, b"::rcm::step::bu")[0] == 200
    assert srv.log_text(jid) == "::rcm::step::bu"
    assert srv.row(jid)["progress"]["steps"] == []
    assert srv.log("build-02", jid, b"ild\n::rcm::sum")[0] == 200
    assert srv.log("build-02", jid, b"mary::all good\n")[0] == 200
    assert srv.log_text(jid) == "::rcm::step::build\n::rcm::summary::all good\n"
    p = srv.row(jid)["progress"]
    assert p["current_name"] == "build" and len(p["steps"]) == 1
    assert [(m.kind, m.value) for m in srv.store.markers(jid)] == [
        ("step", "build"),
        ("summary", "all good"),
    ]


def test_log_requires_content_length_and_rejects_chunked(srv):
    """§6 로그: `Content-Length` 필수 — chunked 도, 아예 없는 것도 411. 파일은 그대로."""
    jid = running_job(srv)
    assert srv.log("build-02", jid, b"before\n")[0] == 200
    status, body = srv.log("build-02", jid, b"x", headers={"Transfer-Encoding": "chunked"})
    assert status == 411, body
    request = (
        f"POST /worker/jobs/{jid}/log HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {srv.tokens['build-02']}\r\n"
        "Content-Type: application/octet-stream\r\nConnection: close\r\n\r\n"
    ).encode()
    status, _payload = raw_http(srv.port, request)
    assert status == 411
    assert srv.log_text(jid) == "before\n"


def test_log_rejects_bodies_over_4_mib_before_reading_them(srv):
    """§6 로그: `Content-Length` > 4 MiB 는 본문을 읽기 전에 413(업로드 라우트와 같은 규칙) —
    1바이트만 보내도 즉시 답한다(본문을 기다리면 소켓 타임아웃 10초가 걸린다)."""
    jid = running_job(srv)
    t0 = time.monotonic()
    status, body = srv.log("build-02", jid, b"x", headers={"Content-Length": str(MAX_LOG_BODY + 1)})
    assert status == 413, body
    assert time.monotonic() - t0 < 5
    assert srv.store.get_job(jid).state == RUNNING
    assert srv.log_text(jid) == ""


def test_log_needs_the_octet_stream_content_type(srv):
    """§6 로그: `Content-Type: application/octet-stream` 이 아니면 415. 파일은 그대로."""
    jid = running_job(srv)
    for ctype in ("text/plain", "application/json"):
        status, body = srv.log("build-02", jid, b"x\n", headers={"Content-Type": ctype})
        assert status == 415, (ctype, body)
    assert srv.log_text(jid) == ""
    assert srv.log("build-02", jid, b"ok\n")[0] == 200
    assert srv.log_text(jid) == "ok\n"


def test_log_is_only_for_the_worker_that_claimed_the_job(srv):
    """§3: 다른 워커의 잡·아직 안 잡힌 잡은 403, 없는 잡은 404 — 파일은 생기지 않는다."""
    jid = running_job(srv)
    other = srv.queued_job(token="bob")
    srv.registered("build-03")
    assert srv.log("build-03", jid, b"x\n")[0] == 403
    assert srv.log("build-02", other, b"x\n")[0] == 403
    assert srv.log("build-02", 999, b"x\n")[0] == 404
    assert srv.log_text(jid) == ""
    assert not srv.app.log_path(other).exists()


# ── finish (§3) ──────────────────────────────────────────────────────────────


def test_finish_succeeded_takes_the_summary_from_markers(srv):
    """§3: `finish {outcome: succeeded, exit_code: 0}` → `store.finish` 로컬 규칙 — summary 는
    마커의 것, failed_step 은 없다. 잡 시간은 claim 부터 finish 까지. 레인은 idle 로 돌아간다."""
    jid = running_job(srv)
    srv.log("build-02", jid, b"::rcm::step::build\n::rcm::summary::green\n")
    srv.clock.advance(30)
    status, body = srv.finish("build-02", jid, "succeeded", exit_code=0)
    assert status == 200, body
    assert body["job_id"] == jid and body["state"] == SUCCEEDED
    v = srv.view(jid)
    assert v["state"] == SUCCEEDED and v["exit_code"] == 0 and v["summary"] == "green"
    assert v["failed_step"] is None and v["job_seconds"] == 30
    assert [t["state"] for t in v["transitions"]] == ["uploading", "queued", "running", "succeeded"]
    j = srv.store.get_job(jid)
    assert j.finished_at == at(30) and j.lane is None and j.phase is None
    lane = srv.worker_lane("build-02", 1)
    assert lane["state"] == "idle" and lane["job_id"] is None
    assert [r["id"] for r in srv.pools()["default"]["recent"]] == [jid]


def test_finish_failed_uses_exit_code_and_the_failing_step(srv):
    """§3: `failed` 이고 마커 summary 가 없으면 summary `exit N`, failed_step 은 마커 규칙(step-end
    fail 이 있으면 그 스텝, 없으면 마지막 스텝)."""
    a = running_job(srv)
    srv.log("build-02", a, b"::rcm::step::build\n::rcm::step::test\n")
    assert srv.finish("build-02", a, "failed", exit_code=3)[0] == 200
    v = srv.view(a)
    assert v["state"] == FAILED and v["exit_code"] == 3
    assert v["summary"] == "exit 3" and v["failed_step"] == "test"
    b = srv.queued_job(token="bob")
    assert srv.claimed("build-02") == b
    srv.log("build-02", b, b"::rcm::step::build\n::rcm::step-end::fail\n::rcm::step::test\n")
    assert srv.finish("build-02", b, "failed", exit_code=1)[0] == 200
    v = srv.view(b)
    assert v["summary"] == "exit 1" and v["failed_step"] == "build"


def test_finish_failed_with_a_summary_and_no_exit_code_keeps_the_summary(srv):
    """§3: 워커 쪽 자재화 실패처럼 프로세스가 뜨기 전에 끝나면 `exit_code: null` + `summary` 로
    보고한다 — 요약은 보낸 그대로, exit_code 는 null."""
    jid = running_job(srv)
    status, body = srv.finish(
        "build-02", jid, "failed", exit_code=None, summary="snapshot blob missing 1234567"
    )
    assert status == 200, body
    v = srv.view(jid)
    assert v["state"] == FAILED and v["exit_code"] is None
    assert v["summary"] == "snapshot blob missing 1234567" and v["failed_step"] is None


def test_finish_timed_out_uses_the_limit_summary(srv):
    """§3: `timed_out` → summary 는 `format_limit(timeout_seconds)`(60초 프리셋이면 `limit 1m`),
    failed_step 은 열려 있던 마지막 스텝."""
    jid = running_job(srv)
    srv.log("build-02", jid, b"::rcm::step::build\n")
    assert srv.finish("build-02", jid, "timed_out", exit_code=-9)[0] == 200
    v = srv.view(jid)
    assert v["state"] == TIMED_OUT and v["exit_code"] == -9
    assert v["summary"] == "limit 1m" and v["failed_step"] == "build"
    assert v["timeout_seconds"] == 60


def test_finish_cancelled_after_the_client_asked_names_the_canceller(srv):
    """§3 취소: 클라이언트 cancel → cancelling → heartbeat `cancel` 목록 → 워커 finish cancelled
    → summary `cancelled by <이름>`, `cancelled_by` 는 취소한 토큰."""
    jid = running_job(srv)
    assert srv.cancel(jid)[1] == {"job_id": jid, "state": CANCELLING}
    assert srv.heartbeat("build-02", jobs=[jid])[1]["cancel"] == [jid]
    assert srv.finish("build-02", jid, "cancelled", exit_code=-15)[0] == 200
    v = srv.view(jid)
    assert v["state"] == CANCELLED and v["exit_code"] == -15
    assert v["summary"] == "cancelled by alice-laptop" and v["cancelled_by"] == "alice-laptop"
    assert srv.worker_lane("build-02", 1)["state"] == "idle"


def test_finish_lost_keeps_the_workers_summary(srv):
    """§3: 워커가 멈추며 보내는 `lost` — 상태 lost, 요약은 워커가 보낸 것."""
    jid = running_job(srv)
    assert srv.finish("build-02", jid, "lost", exit_code=None, summary="worker stopping")[0] == 200
    v = srv.view(jid)
    assert v["state"] == LOST and v["exit_code"] is None and v["summary"] == "worker stopping"


def test_finish_validates_outcome_and_exit_code(srv):
    """§3: `outcome` 은 succeeded·failed·timed_out·cancelled·lost 만, `exit_code` 는 정수나
    null — 아니면 400 이고 잡은 running 그대로."""
    jid = running_job(srv)
    for bad in ("done", "", None, "running", 1):
        status, body = srv.finish("build-02", jid, bad, exit_code=0)
        assert status == 400 and "outcome" in body["error"], (bad, body)
    for bad in ("zero", True, 1.5):
        status, body = srv.finish("build-02", jid, "failed", exit_code=bad)
        assert status == 400 and "exit_code" in body["error"], (bad, body)
    assert srv.store.get_job(jid).state == RUNNING


def test_late_reports_after_a_terminal_state_are_409_and_change_nothing(srv):
    """§3(리뷰 반영 — 늦은 보고는 무시): 종료된 잡에 오는 phase·log·finish 는 전부 409
    `job #N is <state>` — 로그 파일·마커·상태 무엇도 바뀌지 않는다."""
    jid = running_job(srv)
    srv.log("build-02", jid, b"::rcm::step::build\nline\n")
    assert srv.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
    before = srv.log_text(jid)
    markers = srv.store.markers(jid)
    for status, body in (
        srv.finish("build-02", jid, "failed", exit_code=1),
        srv.phase("build-02", jid, "executing"),
        srv.log("build-02", jid, b"::rcm::step::late\nmore\n"),
    ):
        assert status == 409, body
        assert body["error"] == f"job #{jid} is succeeded"
    assert srv.log_text(jid) == before
    assert srv.store.markers(jid) == markers
    j = srv.store.get_job(jid)
    assert j.state == SUCCEEDED and j.summary is None and j.exit_code == 0
    assert j.phase is None


def test_finish_is_only_for_the_worker_that_claimed_the_job(srv):
    """§3: 다른 워커 토큰의 finish 는 403, 없는 잡은 404 — 잡은 running 그대로."""
    jid = running_job(srv)
    srv.registered("build-03")
    assert srv.finish("build-03", jid, "succeeded", exit_code=0)[0] == 403
    assert srv.finish("build-02", 999, "succeeded", exit_code=0)[0] == 404
    assert srv.store.get_job(jid).state == RUNNING


# ── heartbeat (§3) ───────────────────────────────────────────────────────────


def test_heartbeat_response_lists_only_this_workers_cancelling_jobs(srv):
    """§3: 응답은 `{cancel: [이 워커의 cancelling 잡], paused, timeout_seconds}` 뿐. 다른 워커의
    취소는 목록에 없다. `paused` 는 `/pause` 를 비춘다."""
    a = srv.queued_job()
    b = srv.queued_job()
    c = srv.queued_job()
    srv.registered("build-02", lanes=2)
    srv.registered("build-03")
    assert srv.claimed("build-02", 1) == a
    assert srv.claimed("build-02", 2) == b
    assert srv.claimed("build-03", 1) == c
    status, body = srv.heartbeat("build-02", jobs=[a, b])
    assert status == 200, body
    assert body == {"cancel": [], "paused": False, "timeout_seconds": TIMEOUT}
    assert srv.cancel(a)[1]["state"] == CANCELLING
    assert srv.cancel(c)[1]["state"] == CANCELLING
    assert srv.heartbeat("build-02", jobs=[a, b])[1]["cancel"] == [a]
    assert srv.heartbeat("build-03", jobs=[c])[1]["cancel"] == [c]
    assert srv.req("POST", "/pause", token="admin", json_body={})[0] == 200
    body = srv.heartbeat("build-02")[1]
    assert body["paused"] is True and body["cancel"] == [a]
    assert srv.req("POST", "/resume", token="admin", json_body={})[0] == 200
    assert srv.heartbeat("build-02")[1]["paused"] is False


def test_heartbeat_updates_last_seen_and_brings_the_worker_back(srv):
    """§2·§4: 워커 상태는 서버가 받은 시각 `last_seen_at` 로만 — timeout(60초)을 넘기면 down,
    heartbeat 이 오면 다시 idle. payload 의 시각은 어디에도 쓰지 않는다."""
    srv.registered("build-02", lanes=2)
    srv.clock.advance(TIMEOUT)  # 정확히 timeout — 아직 아니다
    assert srv.worker_lane("build-02", 1)["state"] == "idle"
    srv.clock.advance(1)
    assert [w["state"] for w in srv.workers() if w["worker"] == "build-02"] == ["down", "down"]
    assert srv.pools()["default"]["lanes"] == 1
    assert (
        srv.heartbeat("build-02", host_sample={**SAMPLE, "sampled_at": "1999-01-01T00:00:00Z"})[0]
        == 200
    )
    assert srv.store.get_worker("build-02").last_seen_at == at(TIMEOUT + 1)
    assert [w["state"] for w in srv.workers() if w["worker"] == "build-02"] == ["idle", "idle"]
    assert srv.pools()["default"]["lanes"] == 3
    srv.clock.advance(TIMEOUT + 1)
    assert srv.worker_lane("build-02", 2)["state"] == "down"


def test_heartbeat_without_the_running_job_marks_it_lost(srv):
    """§3(리뷰 반영 — heartbeat 은 조정용): `jobs` 를 보냈는데 서버가 아는 이 워커의 running 잡이
    빠져 있으면 워커가 잊은 잡 → lost `worker <name> restarted without the job`. `jobs` 를 아예
    안 보내면 조정하지 않는다. 다른 워커의 잡은 건드리지 않는다."""
    a = srv.queued_job()
    b = srv.queued_job(token="bob")
    c = srv.queued_job(token="admin")
    srv.registered("build-02", lanes=2)
    srv.registered("build-03")
    assert srv.claimed("build-02", 1) == a
    assert srv.claimed("build-02", 2) == b
    assert srv.claimed("build-03", 1) == c
    assert srv.heartbeat("build-02")[0] == 200  # jobs 없음 — 그대로
    assert srv.heartbeat("build-02", jobs=[a, b])[0] == 200  # 다 알고 있다 — 그대로
    assert [srv.store.get_job(j).state for j in (a, b, c)] == [RUNNING, RUNNING, RUNNING]
    srv.clock.advance(20)
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200  # b 를 잊었다
    j = srv.store.get_job(b)
    assert j.state == LOST and j.summary == "worker build-02 restarted without the job"
    assert j.finished_at == at(20)
    assert srv.store.get_job(a).state == RUNNING and srv.store.get_job(c).state == RUNNING
    assert srv.worker_lane("build-02", 2)["state"] == "idle"
    srv.clock.advance(5)
    assert srv.heartbeat("build-02", jobs=[])[0] == 200  # 전부 잊었다
    assert srv.store.get_job(a).state == LOST
    assert srv.store.get_job(a).finished_at == at(25)
    assert srv.store.get_job(c).state == RUNNING
    assert [r["id"] for r in srv.pools()["default"]["recent"]] == [a, b]  # 최근 종료순


def test_heartbeat_host_sample_becomes_the_pools_hosts_entry(srv):
    """§3·§6 호스트 표본: `host_sample` 은 그 풀의 `hosts[]` 항목 — `name` = 워커 이름,
    `source = "worker"`, `sampled_at` = **서버 시각**(표본의 시각·이름·source 는 덮어쓴다).
    기본 풀은 로컬 표본(없음) + 원격 default 워커 표본, linux 풀은 그 풀 워커 표본만."""
    srv.registered("build-02")
    srv.registered("lin-01", pool="linux")
    srv.clock.advance(15)
    assert srv.heartbeat("build-02", host_sample=SAMPLE)[0] == 200
    assert srv.heartbeat("lin-01", host_sample={**SAMPLE, "cores": 32})[0] == 200
    pools = srv.pools()
    (h,) = pools["default"]["hosts"]
    assert h["name"] == "build-02" and h["source"] == "worker"
    assert h["sampled_at"] == iso(at(15)) and h["age_seconds"] == 0 and h["stale"] is False
    assert h["os"] == "linux" and h["cores"] == 8 and h["load"] == [1.5, 1.0, 0.5]
    assert h["cpu"]["busy"] == 12.5 and h["memory"]["used_bytes"] == 4_000_000_000
    assert h["gpu"] is None and h["gpu_note"] == "nvidia-smi not found"
    assert h["top"] == SAMPLE["top"] and h["history"] == SAMPLE["history"]
    (lh,) = pools["linux"]["hosts"]
    assert lh["name"] == "lin-01" and lh["source"] == "worker" and lh["cores"] == 32
    assert pools["default"]["hosts_error"] is None and pools["linux"]["hosts_error"] is None
    srv.clock.advance(20)
    assert srv.heartbeat("build-02", host_sample={**SAMPLE, "cores": 16})[0] == 200
    (h,) = srv.pools()["default"]["hosts"]
    assert h["cores"] == 16 and h["sampled_at"] == iso(at(35))  # 최신 표본으로 바뀐다


@pytest.mark.parametrize(
    "sample",
    [
        {**SAMPLE, "cores": True},  # bool 은 숫자가 아니다
        {**SAMPLE, "cpu": {**SAMPLE["cpu"], "busy": "NaN"}},  # 숫자 자리의 문자열
        {**SAMPLE, "cpu": {**SAMPLE["cpu"], "busy": float("nan")}},  # JSON NaN 리터럴
        {**SAMPLE, "load": [float("inf"), 1.0, 0.5]},  # Infinity
        {"foo": 1, "bar": [2]},  # 아는 키가 하나도 없다
        {**SAMPLE, "memory": {"foo": 1}},  # 중첩 dict 는 알려진 키만
        "not a dict",
        [1, 2],
    ],
)
def test_heartbeat_drops_an_invalid_host_sample_but_still_returns_200(srv, sample):
    """§6 호스트 표본: 검증에 어긋나는 표본(bool 숫자 · NaN/Infinity · 모르는 키만 · dict 아님)은
    표본만 버리고 heartbeat 은 200 — `last_seen_at` 은 갱신되고 hosts[] 는 비어 있다."""
    srv.registered("build-02")
    srv.clock.advance(3)
    status, body = srv.heartbeat("build-02", host_sample=sample)
    assert status == 200, body
    assert set(body) >= {
        "cancel",
        "paused",
        "timeout_seconds",
    }  # 정확한 키 집합은 응답 모양 시험에서
    assert srv.store.get_worker("build-02").last_seen_at == at(3)
    assert srv.pools()["default"]["hosts"] == []


def test_heartbeat_truncates_an_oversized_top_list(srv):
    """§6 호스트 표본: `top` 은 10 개까지 — 넘치면 앞 10 개만 남기고 표본은 받는다."""
    srv.registered("build-02")
    top = [{"comm": f"p{n}", "cpu": float(100 - n), "rss_mb": n} for n in range(12)]
    assert srv.heartbeat("build-02", host_sample={**SAMPLE, "top": top})[0] == 200
    (h,) = srv.pools()["default"]["hosts"]
    assert h["top"] == top[:10]
