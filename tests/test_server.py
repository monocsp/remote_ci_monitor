"""서버 — in-process HTTP: 401 · 합류 · 413 · tar 탈출 거부 · 로그 인증 · 취소 권한 · hardening."""

import http.client
import io
import json
import socket
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor.config import ServerConfig, parse_preset
from remote_ci_monitor.server import App, make_server
from remote_ci_monitor.store import Store

TREE_HASH = "9f" * 32


def sh(name, script, **extra):
    return {"name": name, "argv": ["sh", "-c", script], "timeout_seconds": 60, **extra}


PRESETS = [
    sh("ok", "echo '::rcm::step::a'; cat hello.txt; echo '::rcm::summary::green'; exit 0"),
    sh("bad", "echo '::rcm::step::t'; exit 2"),
    sh("slow", "echo '::rcm::step::wait'; echo line1; echo line2; sleep 20"),
    sh(
        "gate",
        "echo scope=$RCM_INPUT_SCOPE",
        inputs=[
            {"name": "scope", "type": "choice", "choices": ["full", "fast"], "default": "full"}
        ],
    ),
]


def make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


TAR = make_tar({"hello.txt": b"hello\n"})


class Server:
    def __init__(self, tmp_path: Path, *, workers: bool, **server_overrides):
        cfg = ServerConfig()
        cfg.server.data_dir = str(tmp_path / "data")
        cfg.server.grace_seconds = 1
        cfg.server.max_snapshot_bytes = 10_000
        for k, v in server_overrides.items():
            setattr(cfg.server, k, v)
        cfg.presets = tuple(parse_preset(p) for p in PRESETS)
        self.cfg = cfg
        self.store = Store(cfg.data_dir / "rcm.sqlite3")
        now = datetime.now(UTC)
        self.tokens = {
            "alice": self.store.add_token("alice-laptop", admin=False, now=now),
            "bob": self.store.add_token("bob-desk", admin=False, now=now),
            "admin": self.store.add_token("macmini-admin", admin=True, now=now),
        }
        self.app = App(cfg, self.store)
        self.httpd = make_server(self.app, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.1}
        )
        self.thread.daemon = True
        self.thread.start()
        if workers:
            self.app.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.shutdown()
        self.store.close()

    def req(self, method, path, *, token=None, json_body=None, body=None, headers=None, raw=False):
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

    def submit(
        self, token="alice", preset="ok", inputs=None, tree_hash=TREE_HASH, size=None, join=None
    ):
        body = {
            "preset": preset,
            "inputs": inputs or {},
            "source": {
                "mode": "tree",
                "repo": "org/app",
                "base_sha": "abc123f",
                "dirty": True,
                "tree_hash": tree_hash,
                "bytes": size if size is not None else len(TAR),
            },
            "requester_label": f"{token}@host",
        }
        if join is not None:
            body["join"] = join
        return self.req("POST", "/jobs", token=token, json_body=body)

    def upload(self, job_id, token="alice", data=TAR, **kw):
        return self.req("PUT", f"/jobs/{job_id}/tree", token=token, body=data, **kw)

    def wait_terminal(self, job_id, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j = self.store.get_job(job_id)
            if j.is_terminal:
                return j
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish: {self.store.get_job(job_id).state}")

    def wait_state(self, job_id, state, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            j = self.store.get_job(job_id)
            if j.state == state and (state != "running" or j.phase == "executing"):
                return j
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} not {state}: {self.store.get_job(job_id).state}")


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


# ── 인증 · 건강 ───────────────────────────────────────────────────────────────


def test_health_and_whoami(srv):
    status, body = srv.req("GET", "/api/health")
    assert status == 200 and body["ok"] is True and body["db"] is True
    assert srv.req("GET", "/api/whoami")[0] == 401
    assert srv.req("GET", "/api/whoami", token="garbage")[0] == 401
    status, body = srv.req("GET", "/api/whoami", token="admin")
    assert status == 200 and body == {"name": "macmini-admin", "admin": True}


def test_writes_require_token_and_errors_are_one_line(srv):
    status, body = srv.req("POST", "/jobs", json_body={"preset": "ok"})
    assert status == 401 and set(body) == {"error"}
    status, headers, _ = srv.req("POST", "/jobs", json_body={"preset": "ok"}, raw=True)
    assert headers.get("WWW-Authenticate", "").startswith("Bearer")
    assert srv.req("PUT", "/jobs/1/tree", body=b"x")[0] == 401
    assert srv.req("POST", "/jobs/1/cancel", json_body={})[0] == 401
    assert srv.req("GET", "/jobs/1/log")[0] == 401


def test_submit_validation(srv):
    assert srv.submit(preset="nope")[1]["error"] == "unknown preset 'nope'"
    status, body = srv.submit(preset="gate", inputs={"scope": "huge"})
    assert status == 400 and "'huge' is not one of" in body["error"]
    status, body = srv.submit(tree_hash="short")
    assert status == 400 and "tree_hash" in body["error"]
    status, body = srv.submit(size=10**9)
    assert status == 413 and ".rcmignore" in body["error"]
    status, body = srv.req(
        "POST",
        "/jobs",
        token="alice",
        body=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert status == 400 and body["error"] == "body is not valid JSON"


def test_submit_creates_uploading_job_with_upload_path(srv):
    status, body = srv.submit()
    assert status == 201
    assert body["joined"] is False and body["state"] == "uploading"
    assert body["upload"] == f"/jobs/{body['job_id']}/tree"
    assert body["url"].endswith(f"/#/jobs/{body['job_id']}")
    status, view = srv.req("GET", f"/jobs/{body['job_id']}")
    assert status == 200 and view["state"] == "uploading" and view["position"] == 1
    assert view["reason"] == "uploading" and view["requester"] == {
        "name": "alice-laptop",
        "label": "alice@host",
    }


def test_join_same_tree_and_inputs_adds_joiner(srv):
    first = srv.submit()[1]
    status, second = srv.submit(token="bob")
    assert status == 200 and second["joined"] is True and second["job_id"] == first["job_id"]
    status, again = srv.submit(token="alice")  # 같은 사람이 다시 넣어도 새 잡은 없다
    assert again["joined"] is True
    view = srv.req("GET", f"/jobs/{first['job_id']}")[1]
    assert [j["name"] for j in view["joiners"]] == ["bob-desk"]
    other = srv.submit(token="bob", tree_hash="ab" * 32)[1]  # 다른 트리는 새 잡
    assert other["joined"] is False and other["job_id"] != first["job_id"]
    nojoin = srv.submit(token="bob", join=False)[1]
    assert nojoin["joined"] is False


def test_upload_requires_content_length_and_rejects_chunked_and_too_big(srv):
    jid = srv.submit()[1]["job_id"]
    status, body = srv.req(
        "PUT",
        f"/jobs/{jid}/tree",
        token="alice",
        body=b"x",
        headers={"Transfer-Encoding": "chunked"},
    )
    assert status == 411
    status, body = srv.upload(jid, data=b"x" * 20_000)
    assert status == 413 and "exceeds" in body["error"]
    j = srv.store.get_job(jid)
    assert j.state == "cancelled" and j.summary == "snapshot 20 KB exceeds 10 KB"
    assert srv.upload(jid)[0] == 409  # 이미 cancelled
    jid2 = srv.submit(token="bob", tree_hash="cd" * 32)[1]["job_id"]
    assert srv.upload(jid2, token="alice")[0] == 403


def test_upload_then_worker_runs_job_to_success(live):
    jid = live.submit()[1]["job_id"]
    status, body = live.upload(jid)
    assert status == 200 and body == {"job_id": jid, "state": "queued", "bytes": len(TAR)}
    j = live.wait_terminal(jid)
    assert j.state == "succeeded" and j.summary == "green"
    status, view = live.req("GET", f"/jobs/{jid}")
    assert view["state"] == "succeeded" and view["exit_code"] == 0 and view["summary"] == "green"
    assert [t["state"] for t in view["transitions"]] == [
        "uploading",
        "queued",
        "running",
        "succeeded",
    ]
    assert view["job_seconds"] is not None and view["waited_seconds"] is not None


def test_failed_job_reports_exit_code_and_failed_step(live):
    jid = live.submit(preset="bad")[1]["job_id"]
    live.upload(jid)
    j = live.wait_terminal(jid)
    assert j.state == "failed" and j.exit_code == 2 and j.failed_step == "t"
    view = live.req("GET", f"/jobs/{jid}")[1]
    assert view["failed_step"] == "t" and view["summary"] == "exit 2"


def test_tar_escape_is_rejected_and_left_as_failed(live):
    jid = live.submit()[1]["job_id"]
    evil = make_tar({"../../escape.txt": b"x"})
    assert live.upload(jid, data=evil)[0] == 200  # 저장만 하고 풀지 않는다
    j = live.wait_terminal(jid)
    assert j.state == "failed" and j.exit_code is None
    assert j.summary == "snapshot rejected: member escapes the workspace"
    assert not list(live.cfg.data_dir.parent.glob("escape.txt"))


def test_interrupted_upload_is_cancelled_not_deleted(live):
    jid = live.submit()[1]["job_id"]
    s = socket.create_connection(("127.0.0.1", live.port), timeout=5)
    head = (
        f"PUT /jobs/{jid}/tree HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {live.tokens['alice']}\r\n"
        f"Content-Length: {len(TAR) + 5000}\r\n\r\n"
    ).encode()
    s.sendall(head + TAR[:100])
    time.sleep(0.2)
    s.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and live.store.get_job(jid).state == "uploading":
        time.sleep(0.05)
    j = live.store.get_job(jid)
    assert j.state == "cancelled" and j.summary.startswith("upload interrupted after")
    assert not (live.app.job_dir(jid) / "tree.tar.gz.part").exists()


def test_log_tail_and_log_endpoint_need_owner_or_admin(live):
    jid = live.submit(preset="slow")[1]["job_id"]
    live.submit(token="bob", preset="slow")  # bob 합류
    live.upload(jid)
    live.wait_state(jid, "running")
    time.sleep(0.5)
    assert live.req("GET", f"/jobs/{jid}")[1]["log_tail"] is None  # 토큰 없음
    view = live.req("GET", f"/jobs/{jid}?tail=2", token="alice")[1]
    assert view["state"] == "running" and view["position"] is None
    assert view["log_tail"] == ["line1", "line2"]
    assert (
        view["progress"]["current_name"] == "wait" and view["progress"]["timing"] == "as_received"
    )
    assert live.req("GET", f"/jobs/{jid}", token="bob")[1]["log_tail"] is not None  # 합류자
    # 로그 스트림
    status, headers, data = live.req("GET", f"/jobs/{jid}/log", token="alice", raw=True)
    assert status == 200 and b"line1\nline2\n" in data and headers["X-RCM-More"] == "1"
    nxt = int(headers["X-RCM-Next-Offset"])
    status, headers, data = live.req(
        "GET", f"/jobs/{jid}/log?offset={nxt}", token="alice", raw=True
    )
    assert status == 200 and data == b""
    other = live.store.add_token("carol-x", admin=False, now=datetime.now(UTC))
    assert live.req("GET", f"/jobs/{jid}/log", token=other)[0] == 403
    assert live.req("GET", f"/jobs/{jid}/log", token="admin")[0] == 200
    # 상태 모델의 log_tail 도 토큰 조건
    doc = live.req("GET", "/api/status")[1]
    assert doc["pools"][0]["queue"][0]["log_tail"] is None
    doc = live.req("GET", "/api/status", token="alice")[1]
    assert doc["pools"][0]["queue"][0]["log_tail"][-2:] == ["line1", "line2"]  # 기본 5줄
    assert (
        live.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})[1]["state"]
        == "cancelling"
    )
    j = live.wait_terminal(jid)
    assert j.state == "cancelled"


def test_cancel_permissions_and_joiner_leaves(srv):
    jid = srv.submit()[1]["job_id"]
    srv.submit(token="bob")
    other = srv.store.add_token("carol-x", admin=False, now=datetime.now(UTC))
    assert srv.req("POST", f"/jobs/{jid}/cancel", token=other, json_body={})[0] == 403
    status, body = srv.req("POST", f"/jobs/{jid}/cancel", token="bob", json_body={})
    assert status == 200 and body == {"left": True, "job_id": jid, "job_state": "uploading"}
    assert srv.store.get_job(jid).joiners == () and srv.store.get_job(jid).state == "uploading"
    status, body = srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
    assert status == 200 and body == {"job_id": jid, "state": "cancelled"}
    status, body = srv.req("POST", f"/jobs/{jid}/cancel", token="admin", json_body={})
    assert status == 409 and body["state"] == "cancelled"
    assert srv.req("POST", "/jobs/999/cancel", token="admin", json_body={})[0] == 404
    jid2 = srv.submit(token="bob", tree_hash="ef" * 32)[1]["job_id"]
    assert (
        srv.req("POST", f"/jobs/{jid2}/cancel", token="admin", json_body={})[1]["state"]
        == "cancelled"
    )
    assert srv.store.get_job(jid2).cancelled_by == "macmini-admin"


def test_pause_resume_admin_only_and_visible_in_status(live):
    assert live.req("POST", "/pause", token="alice", json_body={})[0] == 403
    assert live.req("POST", "/pause", json_body={})[0] == 401
    status, body = live.req("POST", "/pause", token="admin", json_body={})
    assert status == 200 and body["paused"]["by"] == "macmini-admin"
    assert body["paused"]["at"].endswith("Z")  # 다른 시각과 같은 UTC·초 단위 표기
    jid = live.submit()[1]["job_id"]
    live.upload(jid)
    time.sleep(1.0)
    doc = live.req("GET", "/api/status")[1]
    assert doc["server"]["paused"]["by"] == "macmini-admin"
    row = doc["pools"][0]["queue"][0]
    assert (
        row["state"] == "queued"
        and row["reason"] == "paused"
        and row["estimate"]["finish_at"] is None
    )
    assert live.req("POST", "/resume", token="admin", json_body={})[1] == {"paused": None}
    assert live.wait_terminal(jid).state == "succeeded"


def test_status_schema_v1_shape_and_etag(srv):
    status, headers, raw = srv.req("GET", "/api/status", raw=True)
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    doc = json.loads(raw)
    assert doc["schema_version"] == 1 and len(doc["pools"]) == 1
    pool = doc["pools"][0]
    assert (
        pool["queue"] == []
        and pool["recent"] == []
        and pool["hosts"] == []
        and pool["medians"] == {}
    )
    assert pool["queue_error"] is None and pool["hosts_error"] is None
    assert doc["server"]["workers"] == [
        {
            "lane": 1,
            "state": "idle",
            "job_id": None,
            "error": None,
            "since": doc["server"]["workers"][0]["since"],
        }
    ]
    assert [p["name"] for p in doc["presets"]] == ["ok", "bad", "slow", "gate"]
    assert doc["presets"][3]["inputs"][0]["choices"] == ["full", "fast"]
    etag = headers["ETag"]
    status, headers2, raw2 = srv.req(
        "GET", "/api/status", headers={"If-None-Match": etag}, raw=True
    )
    assert status in (200, 304)  # generated_at 이 초 단위라 같은 초면 304


def test_read_auth_basic_requires_token(tmp_path):
    s = Server(tmp_path, workers=False, read_auth="basic")
    try:
        assert s.req("GET", "/api/status")[0] == 401
        assert s.req("GET", "/api/status", token="alice")[0] == 200
        assert s.req("GET", "/api/health")[0] == 200  # health 는 항상 열려 있다
    finally:
        s.close()


def test_hardening_404_405_400_413(srv):
    assert srv.req("GET", "/nope")[0] == 404
    assert srv.req("GET", "/jobs")[0] == 405
    assert srv.req("POST", "/api/status", json_body={})[0] == 405
    assert srv.req("GET", "/jobs/../etc")[0] == 400
    assert srv.req("GET", "/jobs/abc")[0] == 404
    assert srv.req("GET", "/jobs/1?tail=999")[0] == 400
    big = b"{" + b'"a":"' + b"x" * 70_000 + b'"}'
    assert (
        srv.req(
            "POST", "/jobs", token="alice", body=big, headers={"Content-Type": "application/json"}
        )[0]
        == 413
    )
    status, body = srv.req(
        "POST", "/jobs", token="alice", body=b"", headers={"Content-Type": "application/json"}
    )
    assert status == 400 and "unknown preset" in body["error"]
    status, body = srv.req("GET", "/")
    assert status == 200 and b"<!doctype html>" in body.lower()  # M2: 정적 UI


def test_recent_and_medians_appear_after_jobs_finish(live):
    for _ in range(2):
        jid = live.submit(tree_hash="00" * 32)[1]["job_id"]
        live.upload(jid)
        live.wait_terminal(jid)
    doc = live.req("GET", "/api/status")[1]
    pool = doc["pools"][0]
    assert [r["id"] for r in pool["recent"]] == [2, 1]  # 끝난 잡은 합류 대상이 아니다
    assert pool["recent"][0]["state"] == "succeeded"
    jid = live.submit(tree_hash="01" * 32)[1]["job_id"]
    live.upload(jid)
    live.wait_terminal(jid)
    pool = live.req("GET", "/api/status")[1]["pools"][0]
    assert [r["id"] for r in pool["recent"]] == [jid, 2, 1]
    assert pool["medians"] == {}  # 30초 미만 잡은 표본이 아니다


def test_shutdown_marks_running_job_lost(tmp_path):
    s = Server(tmp_path, workers=True)
    jid = s.submit(preset="slow")[1]["job_id"]
    s.upload(jid)
    s.wait_state(jid, "running")
    t0 = time.monotonic()
    s.close()
    j = s.store.get_job(jid) if False else Store(s.cfg.data_dir / "rcm.sqlite3").get_job(jid)
    assert j.state == "lost" and j.summary == "server stopped while running"
    assert time.monotonic() - t0 < 15
