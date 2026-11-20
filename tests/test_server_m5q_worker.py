"""서버 쪽 워커 와이어 (M5q PR B1 · `docs/m5q-test-scenarios-b.md` 의 srv · 명세 §5.3).

버전 불일치 409 본문에 `server_version` 이 더해진다(키 추가뿐 — 옛 워커는 무시한다). 오류 문장은
글자까지 그대로다. 버전 검사는 등록이 그 워커의 도는 잡을 lost 로 닫기 **전에** 있어야 한다 —
따라가기 전의 409 가 잡을 잃게 하면 안 된다. claim 은 버전을 보지 않는다 — 서버를 올린 뒤에도
이미 등록된 워커는 다음 재시작까지 잡을 받는다(§3.1).

heartbeat 응답에도 `server_version` 이 더해진다(시나리오 B16, PR B2). 워커는 그 값을 보고
드레인한다.
시험을 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.core.model import RUNNING
from test_worker_api import WorkerServer, running_job


def event_count(srv: WorkerServer, jid: int) -> int:
    row = srv.store._conn().execute("SELECT COUNT(*) FROM events WHERE job_id=?", (jid,))
    return int(row.fetchone()[0])


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


def test_version_409_carries_the_server_version(srv):
    # B15 (§5.3): 문장은 오늘과 글자까지 같고 본문에 `server_version` 이 더해진다
    status, body = srv.register("build-02", version="0.0.1")
    assert status == 409, body
    assert body["error"] == (
        f"worker version 0.0.1, server {__version__} — install the same release"
    )
    assert body["server_version"] == __version__
    srv.app.version = "99.0.1"
    status, body = srv.register("build-02", version="0.0.1")
    assert status == 409 and body["server_version"] == "99.0.1", body
    assert body["error"] == "worker version 0.0.1, server 99.0.1 — install the same release"


def test_version_409_without_a_version_still_names_the_server(srv):
    # B15: 버전을 안 보낸 아주 옛 워커도 같은 꼴이다
    body = {"pool": "default", "lanes": 1, "host_name": "old.local"}
    status, body = srv.req("POST", "/worker/register", token="build-02", json_body=body)
    assert status == 409, body
    assert body["error"].startswith("worker version ?, server ")
    assert body["server_version"] == __version__


def test_other_register_errors_have_no_server_version(srv):
    # 버전 불일치가 아닌 거절(400)에는 `server_version` 을 싣지 않는다 — 워커가 따라갈 이유가 아니다
    status, body = srv.register("build-02", pool="bad pool!")
    assert status == 400, body
    assert "server_version" not in body


def test_version_409_does_not_close_running_jobs(srv):
    # B17: 버전 검사가 lost 처리보다 먼저다 — 409 는 도는 잡을 건드리지 않는다
    jid = running_job(srv)
    events_before = event_count(srv, jid)
    srv.app.version = "99.0.1"
    status, body = srv.register("build-02")  # 옛 버전(설치본)으로 다시 등록
    assert status == 409, body
    job = srv.store.get_job(jid)
    assert job.state == RUNNING, (job.state, job.summary)
    assert event_count(srv, jid) == events_before


def test_claim_does_not_check_the_version(srv):
    # B18 (§3.1): 서버를 올린 뒤에도 이미 등록된 옛 워커의 claim 은 잡을 받는다
    srv.registered("build-02")
    srv.app.version = "99.0.1"
    jid = srv.queued_job()
    assert srv.claimed("build-02") == jid


def test_heartbeat_carries_the_server_version(srv):
    # B16 (§5.3 · PR B2): heartbeat 응답에 `server_version` 이 health 의 `version` 과 같게 실린다.
    # 기존 키는 그대로다. 서버 버전이 바뀌면 다음 응답부터 새 값이다
    srv.registered("build-02")
    status, body = srv.heartbeat("build-02", jobs=[])
    assert status == 200, body
    assert {"cancel", "paused", "timeout_seconds", "generation"} <= set(body)
    assert body["server_version"] == __version__
    assert body["server_version"] == srv.req("GET", "/api/health")[1]["version"]
    srv.app.version = "99.0.1"
    status, body = srv.heartbeat("build-02", jobs=[])
    assert status == 200 and body["server_version"] == "99.0.1", body


def test_heartbeat_of_an_unregistered_worker_is_a_409(srv):
    # E16: 워커 행이 없으면 heartbeat 는 409 다 — 워커는 이것을 「서버에 못 닿음」으로 보지 않는다
    status, body = srv.heartbeat("build-02", jobs=[])
    assert status == 409, body
    assert "not registered" in body["error"]
