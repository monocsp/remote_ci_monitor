"""서버 로그 쓰기 절차 · 오프셋 헤더 · 기동 정리(M5t-b §4.5 · 결정 323 · 329 · 338).

서버는 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 로그 POST 는 세션 헤더와
`X-RCM-Log-Offset` 을 직접 싣는다 — 도우미의 기본값에 기대지 않는다. 고장은 App 의 시험 훅
`_before_log_commit`(쓰기와 커밋 사이) · `store.commit_log` · 파일 쓰기에 주입한다.
"""

from __future__ import annotations

import errno
import hashlib
import sqlite3
import threading
from typing import Any

import pytest

from remote_ci_monitor.core.model import LOST, SUCCEEDED
from remote_ci_monitor.server import App
from test_worker_api import OCTET, OMIT, WorkerServer

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


def post_log(
    srv: WorkerServer, jid: int, data: bytes, offset: Any, *, session: str | None = None
) -> tuple[int, Any]:
    headers = {**OCTET, "X-RCM-Worker-Session": session or srv.session(W)}
    headers["X-RCM-Log-Offset"] = OMIT if offset is None else str(offset)
    return srv.req("POST", f"/worker/jobs/{jid}/log", token=W, body=data, headers=headers)


def log_file(srv: WorkerServer, jid: int) -> bytes:
    path = srv.app.log_path(jid)
    return path.read_bytes() if path.exists() else b""


def row(srv: WorkerServer, jid: int) -> dict[str, Any]:
    out = srv.store.log_row(jid)
    assert out is not None
    return out


def fin(srv: WorkerServer, jid: int, outcome: str, **body: Any) -> tuple[int, Any]:
    payload = {"outcome": outcome, **body}
    return srv.req("POST", f"/worker/jobs/{jid}/finish", token=W, json_body=payload)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── 헤더(step 1) ───────────────────────────────────────────────────────────────


def test_rl16_session_without_offset_header_is_400_and_writes_nothing(srv):
    jid = running(srv)
    status, body = post_log(srv, jid, b"line\n", None)
    assert status == 400, body
    assert set(body) == {"error"}  # `code` 없음(F58)
    assert log_file(srv, jid) == b"" and row(srv, jid)["log_offset"] is None


@pytest.mark.parametrize("bad", ["-1", "abc", "1.5", "", "+3", "0x1"])
def test_rl16_offset_header_not_a_non_negative_integer_is_400(srv, bad):
    jid = running(srv)
    status, body = post_log(srv, jid, b"line\n", bad)
    assert status == 400 and set(body) == {"error"}, (bad, body)
    assert log_file(srv, jid) == b""


# ── 오프셋(step 5) ─────────────────────────────────────────────────────────────


def test_rl16_a_resent_chunk_is_ignored_and_the_file_stays_byte_equal(srv):
    jid = running(srv)
    stream = b"one\ntwo\nthree\n"
    assert post_log(srv, jid, stream[:8], 0) == (
        200,
        {"job_id": jid, "bytes": 8, "markers": 0, "offset": 8},
    )
    # 응답을 잃은 워커가 같은 조각을 다시 보낸다 — 이미 받은 범위는 버린다
    status, body = post_log(srv, jid, stream[:8], 0)
    assert (status, body["bytes"], body["offset"]) == (200, 0, 8)
    # 겹친 조각(앞 4바이트가 이미 받은 범위)
    status, body = post_log(srv, jid, stream[4:], 4)
    assert (status, body["bytes"], body["offset"]) == (200, 6, 14)
    assert sha(log_file(srv, jid)) == sha(stream)
    r = row(srv, jid)
    assert (r["log_offset"], r["log_file_bytes"]) == (14, 14)


def test_rl16_b_offset_ahead_of_the_server_is_409_log_gap_with_expected_offset(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"a\n", 0)[0] == 200
    status, body = post_log(srv, jid, b"c\n", 6)  # 2..6 를 잃었다
    assert status == 409 and body["code"] == "log_gap" and body["expected_offset"] == 2, body
    assert log_file(srv, jid) == b"a\n" and row(srv, jid)["log_offset"] == 2
    # 워커는 그 오프셋부터 다시 보낸다
    assert post_log(srv, jid, b"bb\nc\n", 2)[0] == 200
    assert log_file(srv, jid) == b"a\nbb\nc\n"


def test_rl16_d_empty_body_is_an_offset_query(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    status, body = post_log(srv, jid, b"", 0)
    assert (status, body) == (200, {"job_id": jid, "bytes": 0, "markers": 0, "offset": 4})
    assert post_log(srv, jid, b"", 4)[1]["offset"] == 4


def test_rl18_d_masked_bytes_keep_raw_offsets(srv, monkeypatch):
    """가리기로 길이가 달라져도 오프셋은 원본 바이트로 센다(`log_offset ≠ log_file_bytes`)."""
    jid = running(srv)
    secret = b"tok-0123456789"
    monkeypatch.setattr(srv.app, "mask_for_preset", lambda name: (secret,))
    stream = b"start\nkey=" + secret + b"\nnext\n"
    cut = stream.index(b"\nnext") + 1
    assert post_log(srv, jid, stream[:cut], 0)[1]["offset"] == cut
    assert post_log(srv, jid, stream[:cut], 0)[1]["bytes"] == 0  # 재전송
    assert post_log(srv, jid, stream[cut:], cut)[1]["offset"] == len(stream)
    text = log_file(srv, jid)
    assert secret not in text and text.endswith(b"\nnext\n")
    r = row(srv, jid)
    assert r["log_offset"] == len(stream) and r["log_file_bytes"] == len(text) != len(stream)


def test_rl17_b_marker_in_a_chunk_is_committed_once(srv):
    jid = running(srv)
    data = b"::rcm::step::build\nhello\n"
    assert post_log(srv, jid, data, 0)[1]["markers"] == 1
    assert post_log(srv, jid, data, 0)[1]["markers"] == 0  # 재전송은 마커를 두 번 넣지 않는다
    assert [m.value for m in srv.store.markers(jid)] == ["build"]


# ── 쓰기 실패 · 동시 요청 · 커밋 0행(T15 · RL36) ───────────────────────────────


def test_rl36_a_database_busy_truncates_and_the_resend_fills_once(srv, monkeypatch):
    jid = running(srv)
    assert post_log(srv, jid, b"a\n", 0)[0] == 200
    real = srv.store.commit_log
    calls = {"n": 0}

    def busy_once(*args: Any, **kw: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(*args, **kw)

    monkeypatch.setattr(srv.store, "commit_log", busy_once)
    status, body = post_log(srv, jid, b"b\n", 2)
    assert (status, body.get("code")) == (503, "database_busy"), body
    assert log_file(srv, jid) == b"a\n"  # 되자름 — 미커밋 꼬리 0
    assert (row(srv, jid)["log_offset"], row(srv, jid)["log_file_bytes"]) == (2, 2)
    assert post_log(srv, jid, b"b\n", 2)[0] == 200
    assert log_file(srv, jid) == b"a\nb\n"


def test_rl36_b_two_requests_at_the_same_offset_queue_and_the_second_writes_nothing(
    srv, monkeypatch
):
    jid = running(srv)
    first_in = threading.Event()
    go = threading.Event()
    seen: list[int] = []

    def pause(job_id: int) -> None:
        seen.append(job_id)
        if len(seen) == 1:
            first_in.set()
            assert go.wait(10)

    monkeypatch.setattr(srv.app, "_before_log_commit", pause)
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.setdefault("a", post_log(srv, jid, b"x\ny\n", 0)))
    t.start()
    assert first_in.wait(10)
    u = threading.Thread(target=lambda: out.setdefault("b", post_log(srv, jid, b"x\ny\n", 0)))
    u.start()
    u.join(0.5)
    assert u.is_alive()  # 잡별 잠금에서 줄을 선다
    go.set()
    t.join(10)
    u.join(10)
    assert out["a"][1]["bytes"] == 4 and out["b"][1]["bytes"] == 0, out
    assert log_file(srv, jid) == b"x\ny\n"
    assert row(srv, jid)["log_file_bytes"] == 4


def test_rl36_c_write_error_is_503_log_write_failed_and_truncates(srv, monkeypatch):
    jid = running(srv)
    assert post_log(srv, jid, b"a\n", 0)[0] == 200
    import remote_ci_monitor.remote_workers as rws

    real = rws._write_at

    def full(fh: Any, pos: int, data: bytes) -> None:
        fh.seek(pos)
        fh.write(data[:1])
        fh.flush()
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(rws, "_write_at", full)
    status, body = post_log(srv, jid, b"bb\n", 2)
    assert (status, body.get("code")) == (503, "log_write_failed"), body
    assert log_file(srv, jid) == b"a\n" and row(srv, jid)["log_offset"] == 2
    monkeypatch.setattr(rws, "_write_at", real)  # 공간이 돌아왔다
    assert post_log(srv, jid, b"bb\n", 2)[0] == 200
    assert log_file(srv, jid) == b"a\nbb\n"


def test_rl36_d_job_closed_between_write_and_commit_is_409_and_leaves_no_bytes(srv, monkeypatch):
    jid = running(srv)
    assert post_log(srv, jid, b"a\n", 0)[0] == 200

    def close_it(job_id: int) -> None:
        assert srv.store.finish(job_id, LOST, now=srv.clock())

    monkeypatch.setattr(srv.app, "_before_log_commit", close_it)
    status, body = post_log(srv, jid, b"late\n", 2)
    assert (status, body.get("code"), body.get("state")) == (409, "job_closed", LOST), body
    assert log_file(srv, jid) == b"a\n"
    assert row(srv, jid)["log_offset"] == 2


def test_rl36_e_fence_between_write_and_commit_is_409_session_fenced(srv, monkeypatch):
    """step 2 를 지난 요청이 쓰는 사이 같은 이름의 새 세션이 등록해 펜싱한다(결정 338 · R17)."""
    jid = running(srv)
    old = srv.session(W)
    assert post_log(srv, jid, b"a\n", 0)[0] == 200

    def fence(job_id: int) -> None:
        srv.new_process(W)
        srv.registered(W)

    monkeypatch.setattr(srv.app, "_before_log_commit", fence)
    status, body = post_log(srv, jid, b"after\n", 2, session=old)
    assert (status, body.get("code")) == (409, "session_fenced"), body
    assert log_file(srv, jid) == b"a\n"
    assert row(srv, jid)["log_offset"] == 2  # 펜스 뒤 반영 0


# ── 짧은 파일(T16 · RL37) · 기동 정리(RL16 c) ─────────────────────────────────


def test_rl37_a_short_file_continues_at_its_end_and_pins_log_incomplete(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"aaaa\n", 0)[0] == 200
    path = srv.app.log_path(jid)
    with path.open("r+b") as fh:
        fh.truncate(2)  # 서버 OS 충돌 · fsync 없음 흉내
    lines: list[str] = []
    srv.app.log = lines.append  # type: ignore[method-assign]
    assert post_log(srv, jid, b"bb\n", 5)[0] == 200
    data = log_file(srv, jid)
    assert data == b"aabb\n" and b"\0" not in data
    assert row(srv, jid)["log_complete"] == 0
    assert any("log gap" in s and f"#{jid}" in s for s in lines), lines
    # finish 가 `log_offset ≥ log_bytes` 여도 거짓으로 남는다
    status, body = fin(srv, jid, SUCCEEDED, exit_code=0, log_bytes=8, group_gone=True)
    assert status == 200, body
    assert row(srv, jid)["log_complete"] == 0


def test_rl16_c_start_trims_an_uncommitted_tail_and_the_resend_fills_it(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    with srv.app.log_path(jid).open("ab") as fh:
        fh.write(b"two\n")  # 썼지만 커밋 전에 죽었다
    srv.app.check_logs_on_start()
    assert log_file(srv, jid) == b"one\n"
    assert post_log(srv, jid, b"two\n", 4)[0] == 200
    assert log_file(srv, jid) == b"one\ntwo\n"
    assert row(srv, jid)["log_complete"] is None


def test_rl37_b_start_pins_a_short_busy_file_incomplete(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"one\ntwo\n", 0)[0] == 200
    with srv.app.log_path(jid).open("r+b") as fh:
        fh.truncate(3)
    lines: list[str] = []
    srv.app.log = lines.append  # type: ignore[method-assign]
    srv.app.check_logs_on_start()
    assert log_file(srv, jid) == b"one"  # 짧으면 자르지 않는다
    assert row(srv, jid)["log_complete"] == 0
    assert any("log gap" in s for s in lines), lines


def test_rl37_c_start_checks_finished_remote_jobs_and_leaves_missing_files(srv):
    short, gone, longer = running(srv), None, None
    assert post_log(srv, short, b"aaaa\n", 0)[0] == 200
    assert fin(srv, short, SUCCEEDED, exit_code=0, log_bytes=5, group_gone=True)[0] == 200
    gone = srv.queued_job()
    assert srv.claimed(W) == gone
    assert post_log(srv, gone, b"bbbb\n", 0)[0] == 200
    assert fin(srv, gone, SUCCEEDED, exit_code=0, log_bytes=5, group_gone=True)[0] == 200
    longer = srv.queued_job()
    assert srv.claimed(W) == longer
    assert post_log(srv, longer, b"cccc\n", 0)[0] == 200
    assert fin(srv, longer, SUCCEEDED, exit_code=0, log_bytes=5, group_gone=True)[0] == 200
    for j in (short, gone, longer):
        assert row(srv, j)["log_complete"] == 1
    with srv.app.log_path(short).open("r+b") as fh:
        fh.truncate(2)
    srv.app.log_path(gone).unlink()  # gc 가 지웠을 수 있다
    with srv.app.log_path(longer).open("ab") as fh:
        fh.write(b"extra\n")
    lines: list[str] = []
    srv.app.log = lines.append  # type: ignore[method-assign]
    srv.app.check_logs_on_start()
    assert row(srv, short)["log_complete"] == 0
    assert row(srv, gone)["log_complete"] == 1
    assert row(srv, longer)["log_complete"] == 1
    assert log_file(srv, longer) == b"cccc\nextra\n"  # 끝난 잡은 자르지 않는다
    assert sum("log gap" in s for s in lines) == 1, lines


def test_rl16_c_app_start_runs_the_log_check(srv, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(App, "check_logs_on_start", lambda self: calls.append(True))
    app = App(srv.cfg, srv.store, now_fn=srv.clock, mono_fn=srv.clock.mono)
    try:
        app.start()
    finally:
        app.shutdown()
    assert calls == [True]
