"""로그 읽는 쪽 상한(M5t-b §4.5 4판 · 결정 339-f · RL55).

원격 잡의 로그 조각이 파일에 써졌지만 커밋 전인 동안(App 의 시험 훅 `_before_log_commit` 에서
멈춤) `GET /jobs/N/log` · 상태의 로그 꼬리 · 잡 문서 꼬리는 `log_file_bytes` 까지만 읽는다.
로컬 · 레거시 잡(`log_file_bytes` NULL)은 오늘처럼 파일 길이까지다.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from remote_ci_monitor.worker import tail_lines
from test_worker_api import OCTET, WorkerServer

W = "build-02"


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


def running(srv: WorkerServer) -> int:
    srv.registered(W)
    jid = srv.queued_job()
    assert srv.claimed(W) == jid
    return jid


def post_log(srv: WorkerServer, jid: int, data: bytes, offset: int) -> tuple[int, Any]:
    headers = {**OCTET, "X-RCM-Log-Offset": str(offset)}
    return srv.req("POST", f"/worker/jobs/{jid}/log", token=W, body=data, headers=headers)


def read_log(srv: WorkerServer, jid: int, offset: int = 0) -> tuple[bytes, int]:
    status, headers, body = srv.req(
        "GET", f"/jobs/{jid}/log?offset={offset}", token="alice", raw=True
    )
    assert status == 200, body
    return body, int(headers["X-RCM-Next-Offset"])


def stuck_between_write_and_commit(srv: WorkerServer, monkeypatch, jid: int, data: bytes, at: int):
    """조각을 파일에 쓴 뒤 커밋 전에 멈춘 요청 하나. 돌려준 함수가 놓아준다."""
    inside, go = threading.Event(), threading.Event()

    def pause(job_id: int) -> None:
        inside.set()
        assert go.wait(10)

    monkeypatch.setattr(srv.app, "_before_log_commit", pause)
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.setdefault("r", post_log(srv, jid, data, at)))
    t.start()
    assert inside.wait(10)

    def release() -> tuple[int, Any]:
        go.set()
        t.join(10)
        return out["r"]

    return release


def test_rl55_log_route_stops_at_the_committed_length_until_the_commit(srv, monkeypatch):
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    release = stuck_between_write_and_commit(srv, monkeypatch, jid, b"two\n", 4)
    assert srv.app.log_path(jid).read_bytes() == b"one\ntwo\n"  # 파일에는 이미 있다
    body, nxt = read_log(srv, jid)
    assert (body, nxt) == (b"one\n", 4)
    assert release()[0] == 200
    body, nxt = read_log(srv, jid, nxt)
    assert (body, nxt) == (b"two\n", 8)  # 커밋 뒤 다음 읽기에 온다 — 빠짐 · 중복 0


def test_rl55_status_and_job_document_tails_end_at_the_committed_length(srv, monkeypatch):
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    release = stuck_between_write_and_commit(srv, monkeypatch, jid, b"uncommitted\n", 4)
    try:
        status, doc = srv.req("GET", f"/jobs/{jid}?tail=5", token="alice")
        assert status == 200 and doc["log_tail"] == ["one"], doc.get("log_tail")
        pools = srv.req("GET", "/api/status", token="alice")[1]["pools"]
        rows = {r["id"]: r for p in pools for r in p["queue"] or []}
        assert rows[jid]["log_tail"] == ["one"], rows[jid].get("log_tail")
    finally:
        assert release()[0] == 200
    status, doc = srv.req("GET", f"/jobs/{jid}?tail=5", token="alice")
    assert doc["log_tail"] == ["one", "uncommitted"]


def test_rl55_a_failed_commit_never_shows_its_bytes(srv, monkeypatch):
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    seen: list[tuple[bytes, int]] = []

    def read_then_fail(job_id: int) -> None:
        seen.append(read_log(srv, jid))
        raise RuntimeError("commit fails")

    monkeypatch.setattr(srv.app, "_before_log_commit", read_then_fail)
    assert post_log(srv, jid, b"lost\n", 4)[0] == 500
    assert seen == [(b"one\n", 4)]
    assert read_log(srv, jid) == (b"one\n", 4)


def test_rl55_local_and_legacy_jobs_read_to_the_file_length(srv):
    """`log_file_bytes` 가 NULL 인 잡은 오늘처럼 파일 길이까지 읽는다."""
    jid = srv.queued_job()
    path = srv.app.log_path(jid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"local one\nlocal two\n")
    assert read_log(srv, jid) == (b"local one\nlocal two\n", 20)


def test_rl55_a_short_file_reads_to_its_end(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"one\ntwo\n", 0)[0] == 200
    with srv.app.log_path(jid).open("r+b") as fh:
        fh.truncate(4)
    assert read_log(srv, jid) == (b"one\n", 4)


def test_rl55_tail_lines_takes_an_end_length(tmp_path):
    path = tmp_path / "log.txt"
    path.write_bytes(b"a\nb\nc-uncommitted\n")
    assert tail_lines(path, 5, end=4) == ["a", "b"]
    assert tail_lines(path, 5) == ["a", "b", "c-uncommitted"]
    assert tail_lines(path, 1, end=4) == ["b"]
