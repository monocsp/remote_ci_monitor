"""finish · 늦은 결과 · 로그 완전성 · 산출물 처분 (M5t-b §4.6 · 결정 256 · 259 · 274 · 275 · 277 ·
330).

서버는 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 유예 · 취소로 닫히는 것은
`store.finish` 를 직접 불러 흉내 낸다(janitor 와 같은 호출이다).
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import threading
from datetime import timedelta
from typing import Any

import pytest

from remote_ci_monitor.core.model import LOST, RUNNING, SUCCEEDED
from remote_ci_monitor.core.status import iso
from test_worker_api import OCTET, WORKER_PRESETS, WorkerServer, sh

W = "build-02"
PRESETS = [
    *WORKER_PRESETS,
    sh("ship", "echo ship", irreversible=True),
    sh("arts", "echo arts", artifacts=["out/**"]),
]


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, presets=PRESETS)
    yield s
    s.close()


def running(srv: WorkerServer, preset: str = "ok") -> int:
    if W not in srv.sessions:
        srv.registered(W)
    jid = srv.queued_job(preset=preset)
    assert srv.claimed(W) == jid
    return jid


def fin(srv: WorkerServer, jid: int, outcome: str = SUCCEEDED, **body: Any) -> tuple[int, Any]:
    body.setdefault("exit_code", 0 if outcome == SUCCEEDED else None)
    payload = {"outcome": outcome, **body}
    return srv.req("POST", f"/worker/jobs/{jid}/finish", token=W, json_body=payload)


def post_log(srv: WorkerServer, jid: int, data: bytes, offset: int) -> tuple[int, Any]:
    headers = {**OCTET, "X-RCM-Log-Offset": str(offset)}
    return srv.req("POST", f"/worker/jobs/{jid}/log", token=W, body=data, headers=headers)


def close_lost(srv: WorkerServer, jid: int, *, hold: str | None = None) -> None:
    """서버가 먼저 닫는다(유예 만료 흉내)."""
    from remote_ci_monitor.core import outcome

    text, code, args = outcome.summary("worker_unreachable", name=W, seconds=90)
    assert srv.store.finish(
        jid,
        LOST,
        now=srv.clock(),
        summary=text,
        summary_code=code,
        summary_args=args,
        hold_cause=hold,
    )


def late_events(srv: WorkerServer, jid: int) -> list[dict[str, Any]]:
    rows = srv.store._conn().execute(
        "SELECT payload FROM events WHERE job_id=? AND kind='late_result'", (jid,)
    )
    return [dict(r) for r in rows]


def job_doc(srv: WorkerServer, jid: int) -> dict[str, Any]:
    status, doc = srv.req("GET", f"/jobs/{jid}", token="alice")
    assert status == 200, doc
    return doc


# ── 늦은 결과(T14 · RL4 · RL5 · RL8 · RL21) ────────────────────────────────────


def test_rl21_b_late_finish_after_lost_is_recorded_once_and_changes_nothing(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    srv.clock.advance(30)
    close_lost(srv, jid)
    before = srv.store.get_job(jid)
    srv.clock.advance(5)
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=4, group_gone=True)
    assert status == 409 and body["code"] == "job_closed" and body["state"] == LOST, body
    assert body["late_result_recorded"] is True and body["hold_released"] is False, body
    after = srv.store.get_job(jid)
    assert (after.state, after.summary, after.finished_at) == (
        before.state,
        before.summary,
        before.finished_at,
    )
    doc = job_doc(srv, jid)
    assert doc["state"] == LOST and doc["summary_code"] == "worker_unreachable"
    late = doc["late_result"]
    assert set(late) == {"outcome", "exit_code", "finished_at", "received_at", "log_complete"}
    assert (late["outcome"], late["exit_code"], late["log_complete"]) == (SUCCEEDED, 0, True)
    assert late["received_at"] == iso(srv.clock())
    assert doc["log_complete"] is True
    # 같은 보고가 또 와도(재시작한 워커의 큐) 한 건이다
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=4, group_gone=True)
    assert status == 409 and body["late_result_recorded"] is False, body
    assert len(late_events(srv, jid)) == 1


def test_rl4_late_finish_never_flips_lost(srv):
    """`lost` 가 된 뒤 같은 잡이 `succeeded` 등으로 뒤집히지 않는다(늦은 finish 는 기록만)."""
    for outcome in (SUCCEEDED, "failed", "timed_out", "cancelled"):
        jid = running(srv)
        close_lost(srv, jid)
        fin(srv, jid, outcome, exit_code=0 if outcome == SUCCEEDED else 1, group_gone=True)
        assert srv.store.get_job(jid).state == LOST, outcome
        assert len(late_events(srv, jid)) == 1


def test_rl21_c_two_concurrent_late_finishes_record_exactly_one(srv):
    jid = running(srv)
    close_lost(srv, jid)
    answers: list[Any] = []
    barrier = threading.Barrier(2)

    def send() -> None:
        barrier.wait(5)
        answers.append(fin(srv, jid, SUCCEEDED, group_gone=True))

    threads = [threading.Thread(target=send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert len(late_events(srv, jid)) == 1
    flags = sorted(a[1]["late_result_recorded"] for a in answers)
    assert flags == [False, True], answers


def test_rl21_a_duplicate_finish_is_409_and_records_nothing(srv):
    jid = running(srv)
    assert fin(srv, jid, SUCCEEDED, log_bytes=0, group_gone=True)[0] == 200
    first = srv.store.get_job(jid)
    srv.clock.advance(10)
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=0, group_gone=True)
    assert status == 409 and body["code"] == "job_closed", body
    assert body.get("late_result_recorded") is False
    assert srv.store.get_job(jid).finished_at == first.finished_at
    assert late_events(srv, jid) == [] and job_doc(srv, jid)["late_result"] is None


def test_rl11_fenced_sessions_late_finish_is_not_recorded(srv):
    jid = running(srv)
    old = srv.session(W)
    close_lost(srv, jid)
    srv.new_process(W)
    srv.registered(W)  # 옛 세션을 펜싱한다
    body = {"outcome": SUCCEEDED, "exit_code": 0, "worker_session_id": old}
    status, answer = srv.req("POST", f"/worker/jobs/{jid}/finish", token=W, json_body=body)
    assert (status, answer.get("code")) == (409, "session_fenced"), answer
    assert late_events(srv, jid) == []


def test_rl44_c_late_finish_with_group_gone_releases_the_hold_in_the_same_answer(srv):
    jid = running(srv, "ship")
    close_lost(srv, jid, hold="worker_unreachable")
    assert srv.store.has_open_hold(jid)
    status, body = fin(srv, jid, SUCCEEDED, group_gone=True)
    assert status == 409 and body["late_result_recorded"] is True, body
    assert body["hold_released"] is True
    assert not srv.store.has_open_hold(jid)
    assert len(late_events(srv, jid)) == 1


def test_rl44_c_late_finish_without_group_gone_keeps_the_hold(srv):
    jid = running(srv, "ship")
    close_lost(srv, jid, hold="worker_unreachable")
    status, body = fin(srv, jid, SUCCEEDED)
    assert body["late_result_recorded"] is True and body["hold_released"] is False, body
    assert srv.store.has_open_hold(jid)


def test_rl20_late_result_log_complete_is_false_when_bytes_are_missing(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    close_lost(srv, jid)
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=40, group_gone=True)
    assert body["late_result_recorded"] is True
    doc = job_doc(srv, jid)
    assert doc["late_result"]["log_complete"] is False and doc["log_complete"] is False


# ── finished_at 자르기(결정 277 · RL27 b) ─────────────────────────────────────


@pytest.mark.parametrize("skew", [3600, -3600])
def test_rl27_b_worker_finished_at_is_clamped_into_started_and_received(srv, skew):
    jid = running(srv)
    srv.clock.advance(20)
    worker_clock = srv.clock() + timedelta(seconds=skew)
    assert fin(srv, jid, SUCCEEDED, finished_at=iso(worker_clock), group_gone=True)[0] == 200
    job = srv.store.get_job(jid)
    assert job.started_at <= job.finished_at <= srv.clock()
    doc = job_doc(srv, jid)
    assert doc["job_seconds"] >= 0
    assert doc["finished_at"] == iso(srv.clock() if skew > 0 else job.started_at)


def test_rl27_b_late_result_finished_at_is_clamped_too(srv):
    jid = running(srv)
    close_lost(srv, jid)
    future = srv.clock() + timedelta(hours=1)
    fin(srv, jid, SUCCEEDED, finished_at=iso(future), group_gone=True)
    assert job_doc(srv, jid)["late_result"]["finished_at"] == iso(srv.clock())


# ── 로그 완전성(§4.6 · RL20) ─────────────────────────────────────────────────


def test_rl20_finish_with_fewer_bytes_received_than_produced_is_log_incomplete(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    assert fin(srv, jid, SUCCEEDED, log_bytes=12 * 1024 * 1024, group_gone=True)[0] == 200
    doc = job_doc(srv, jid)
    assert doc["log_complete"] is False
    recent = {r["id"]: r for p in srv.status()["pools"] for r in p["recent"] or []}
    assert recent[jid]["log_complete"] is False


def test_rl20_finish_with_every_byte_received_is_log_complete_and_legacy_is_unknown(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    assert fin(srv, jid, SUCCEEDED, log_bytes=4, group_gone=True)[0] == 200
    assert job_doc(srv, jid)["log_complete"] is True
    other = running(srv)
    assert fin(srv, other, SUCCEEDED, group_gone=True)[0] == 200  # log_bytes 없음(옛 워커 꼴)
    assert job_doc(srv, other)["log_complete"] is None


# ── 산출물 처분과 `ready` 조건(결정 275 · F63 · RL22) ────────────────────────


def bundle_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("out/a.txt")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"hi"))
    return buf.getvalue()


def upload(srv: WorkerServer, jid: int, data: bytes) -> tuple[int, Any]:
    headers = {"Content-Type": "application/x-tar"}
    return srv.req("PUT", f"/worker/jobs/{jid}/artifacts", token=W, body=data, headers=headers)


def ready(digest: str | None) -> dict[str, Any]:
    return {"state": "ready", "file_count": 1, "total_bytes": 2, "bundle_sha256": digest}


def test_rl22_d_ready_without_an_upload_is_upload_failed(srv):
    jid = running(srv, "arts")
    digest = hashlib.sha256(bundle_bytes()).hexdigest()
    assert fin(srv, jid, SUCCEEDED, artifacts=ready(digest), group_gone=True)[0] == 200
    doc = job_doc(srv, jid)
    assert doc["state"] == SUCCEEDED  # 잡 결과는 산출물과 무관하다
    assert (doc["artifacts"]["state"], doc["artifacts"]["reason_code"]) == (
        "failed",
        "upload_failed",
    ), doc["artifacts"]


def test_rl22_e_ready_with_the_row_but_no_file_is_upload_failed(srv):
    jid = running(srv, "arts")
    data = bundle_bytes()
    status, receipt = upload(srv, jid, data)
    assert status == 201, receipt
    (srv.app.artifacts_dir(jid) / "bundle.tar").unlink()
    assert fin(srv, jid, SUCCEEDED, artifacts=ready(receipt["bundle_sha256"]))[0] == 200
    a = job_doc(srv, jid)["artifacts"]
    assert (a["state"], a["reason_code"]) == ("failed", "upload_failed"), a


def test_rl22_ready_with_a_different_hash_is_hash_mismatch(srv):
    jid = running(srv, "arts")
    status, receipt = upload(srv, jid, bundle_bytes())
    assert status == 201
    assert fin(srv, jid, SUCCEEDED, artifacts=ready("0" * 64), group_gone=True)[0] == 200
    a = job_doc(srv, jid)["artifacts"]
    assert (a["state"], a["reason_code"]) == ("failed", "hash_mismatch"), a


def test_rl22_b_reupload_is_idempotent_and_ready_needs_row_hash_and_file(srv):
    jid = running(srv, "arts")
    data = bundle_bytes()
    first = upload(srv, jid, data)
    again = upload(srv, jid, data)  # 응답을 잃어 다시 올린다
    assert first[0] == 201 and again[0] == 200, (first, again)
    assert first[1]["bundle_sha256"] == again[1]["bundle_sha256"]
    assert fin(srv, jid, SUCCEEDED, artifacts=ready(first[1]["bundle_sha256"]))[0] == 200
    a = job_doc(srv, jid)["artifacts"]
    assert a["state"] == "ready", a
    count = srv.store._conn().execute("SELECT COUNT(*) FROM job_artifacts WHERE job_id=?", (jid,))
    assert count.fetchone()[0] == 1
    assert srv.store.get_job(jid).state != RUNNING
