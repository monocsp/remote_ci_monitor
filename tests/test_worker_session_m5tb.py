"""워커 세션 · 펜싱 · 프로토콜 2(M5t-b §4.2 · §4.7).

결정 255 · 258 · 261 · 262 · 265 · 336 · 339-a 를 잠근다.

서버는 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 워커 이름마다 한 프로세스를
흉내 내고 `new_process` 가 같은 토큰의 새 프로세스를 만든다. 세션 id 는 실행 중에 만든다(§8).
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor import remote_worker as rw
from remote_ci_monitor.client import ClientError
from remote_ci_monitor.config import WorkerConfig, parse_preset
from remote_ci_monitor.core.model import CANCELLING, LOST, QUEUED, RUNNING, SUCCEEDED
from remote_ci_monitor.remote_worker import RemoteWorker, WorkerExit
from test_worker_api import WORKER_PRESETS, WorkerServer, sh

PROTOCOL_TEXT = "worker protocol 2 required: upgrade rcm on this worker"
EXIT_PROTOCOL = 3  # 프로토콜 불일치 — 감독자가 되살린다(결정 258)
PRESETS = [
    *WORKER_PRESETS,
    sh("ship", "echo ship", irreversible=True),  # 그룹 없음 → irreversible:ship · global
]


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, presets=PRESETS)
    yield s
    s.close()


def job_of(srv: WorkerServer, jid: int):
    return srv.store.get_job(jid)


def hold_rows(srv: WorkerServer) -> list[dict[str, Any]]:
    return [dict(r) for r in srv.store._conn().execute("SELECT * FROM irreversible_holds")]


def running(srv: WorkerServer, worker: str = "build-02", *, preset: str = "ok", lane: int = 1):
    jid = srv.queued_job(preset=preset)
    assert srv.claimed(worker, lane) == jid
    return jid


# ── 펜싱(T8 · W5 · RL10 · RL11) ────────────────────────────────────────────────


def test_rl10_a_second_process_fences_the_first_and_the_first_can_only_confirm(srv):
    srv.registered("build-02", lanes=2)
    jid = running(srv)
    old = srv.new_process("build-02")  # 같은 토큰으로 프로세스 B
    srv.registered("build-02", lanes=2)
    rows = (
        srv.store._conn()
        .execute(
            "SELECT id, fenced_at, fenced_by FROM worker_sessions WHERE worker_name='build-02'"
        )
        .fetchall()
    )
    current = [r["id"] for r in rows if r["fenced_at"] is None]
    assert current == [srv.session("build-02")]  # B 가 유일한 현재 세션
    assert {r["id"]: r["fenced_by"] for r in rows}[old] == srv.session("build-02")
    # 확인 전까지 X 는 busy — 그룹 · 레인이 풀리지 않고 B 의 그 레인 claim 은 204
    assert job_of(srv, jid).state == RUNNING
    srv.queued_job()
    assert srv.claim("build-02", 1)[0] == 204
    # A 의 다음 요청은 무엇이든 409 session_fenced(job_closed 와 구별)
    a = {"worker_session_id": old}
    for status, body in (
        srv.req(
            "POST",
            "/worker/heartbeat",
            token="build-02",
            json_body={**a, "seq": 9, "claims_settled": 1, "jobs": [jid]},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/phase",
            token="build-02",
            json_body={**a, "phase": "executing"},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-02",
            json_body={**a, "outcome": "failed", "exit_code": 1},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/log",
            token="build-02",
            body=b"x\n",
            headers={"Content-Type": "application/octet-stream", "X-RCM-Worker-Session": old},
        ),
    ):
        assert status == 409 and body["code"] == "session_fenced", body
    assert job_of(srv, jid).state == RUNNING
    # 좁은 확인 finish 만 받는다 — lost + worker_replaced, group_gone 참이면 홀드 0
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            **a,
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "summary_args": {},
            "group_gone": True,
        },
    )
    assert status == 200, body
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_replaced"
    assert j.summary_args == {"name": "build-02"}
    assert hold_rows(srv) == []
    # 펜싱된 세션의 재등록은 409 session_fenced — 핑퐁 없음
    status, body = srv.register("build-02", worker_session_id=old, lanes=2)
    assert status == 409 and body["code"] == "session_fenced"


def test_rl10_an_irreversible_job_confirmed_without_group_gone_holds(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    old = srv.new_process("build-02")
    srv.registered("build-02")
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "worker_session_id": old,
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "group_gone": False,
        },
    )
    assert status == 200, body
    assert [(h["job_id"], h["cause"], h["concurrency_group"]) for h in hold_rows(srv)] == [
        (jid, "group_not_gone", "irreversible:ship")
    ]
    # RL44 (e): 홀드가 남는다. 그 뒤 group_gone: true 좁은 확인은 홀드를 푼다(확인 2)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "worker_session_id": old,
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "group_gone": True,
        },
    )
    assert status == 200 and body["hold_released"] is True, body
    (h,) = hold_rows(srv)
    assert h["release_kind"] == "worker" and h["released_by"] == "build-02"


def test_rl11_unknown_foreign_and_malformed_sessions(srv):
    srv.registered("build-02")
    srv.registered("build-03")
    jid = running(srv)
    # 표에 없는 세션 → 409 not_registered(남의 토큰 403 과 구별)
    stranger = secrets.token_hex(16)
    status, body = srv.req(
        "POST",
        "/worker/heartbeat",
        token="build-02",
        json_body={"worker_session_id": stranger, "seq": 1, "claims_settled": 0, "jobs": []},
    )
    assert status == 409 and body["code"] == "not_registered", body
    # 다른 이름의 토큰이 남의 현재 세션 id 를 실으면 403, `heard` 가 오르지 않는다
    theirs = srv.session("build-02")
    before = srv.app.heard_at(theirs)
    srv.clock.advance(30)
    for status, body in (
        srv.req(
            "POST",
            "/worker/heartbeat",
            token="build-03",
            json_body={"worker_session_id": theirs, "seq": 1, "claims_settled": 0, "jobs": [jid]},
        ),
        srv.req(
            "POST",
            "/worker/register",
            token="build-03",
            json_body={
                "worker_session_id": theirs,
                "pool": "default",
                "lanes": 1,
                "version": __version__,
            },
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-03",
            json_body={"worker_session_id": theirs, "outcome": "succeeded", "exit_code": 0},
        ),
    ):
        assert status == 403 and set(body) == {"error"}, body
    assert srv.app.heard_at(theirs) == before
    assert job_of(srv, jid).state == RUNNING
    # 모양이 틀린 세션 헤더 · 본문은 400 이고 본문 키는 error 하나
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/log",
        token="build-02",
        body=b"x\n",
        headers={"Content-Type": "application/octet-stream", "X-RCM-Worker-Session": "nope"},
    )
    assert status == 400 and set(body) == {"error"}, body
    status, body = srv.req(
        "POST",
        "/worker/heartbeat",
        token="build-02",
        json_body={"worker_session_id": "XYZ", "seq": 1, "claims_settled": 0},
    )
    assert status == 400 and set(body) == {"error"}


def test_rl11_two_processes_registering_at_once_leave_exactly_one_current_session(srv):
    sids = [secrets.token_hex(16) for _ in range(6)]
    out: list[int] = []

    def reg(sid: str) -> None:
        status, _ = srv.req(
            "POST",
            "/worker/register",
            token="build-02",
            json_body={
                "worker_session_id": sid,
                "pool": "default",
                "lanes": 1,
                "version": __version__,
            },
        )
        out.append(status)

    threads = [threading.Thread(target=reg, args=(s,)) for s in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert out.count(200) == len(sids)
    current = (
        srv.store._conn()
        .execute(
            "SELECT id FROM worker_sessions WHERE worker_name='build-02' AND fenced_at IS NULL"
        )
        .fetchall()
    )
    assert len(current) == 1


# ── 같은 세션의 재등록 · 순서(T10 · RL12 · RL13) ─────────────────────────────


def test_rl12_reregistering_the_same_session_is_idempotent(srv):
    srv.registered("build-02", lanes=2)
    a = running(srv, lane=1)
    srv.registered("build-02", lanes=2)  # 다른 레인이 not_registered 409 를 받아 재등록
    assert job_of(srv, a).state == RUNNING
    rows = srv.store._conn().execute("SELECT fenced_at FROM worker_sessions").fetchall()
    assert [r["fenced_at"] for r in rows] == [None]


def test_rl12_spec_does_not_create_a_session(srv):
    """`rcm worker --check` · `rcm doctor` 는 spec · health · whoami 만 부른다(F46)."""
    srv.registered("build-02")
    jid = running(srv)
    assert srv.req("GET", "/worker/spec", token="build-02")[0] == 200
    assert srv.store._conn().execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1
    assert srv.heartbeat("build-02", jobs=[jid])[0] == 200


def test_rl13_a_version_mismatch_answers_first_with_no_side_effect(srv):
    srv.registered("build-02")
    jid = running(srv)
    srv.new_process("build-02")
    status, body = srv.register("build-02", version="0.0.1")
    assert status == 409 and body["server_version"] == __version__
    assert "code" not in body
    assert job_of(srv, jid).state == RUNNING
    assert srv.store._conn().execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1


# ── 레거시 · 옛 워커(T11 · T12 · T17 · RL24 · RL47) ───────────────────────────


def _legacy_running(srv: WorkerServer, preset: str = "ok") -> int:
    """v26 이전에 claim 된 원격 잡 — 세션 · 얼린 값이 NULL 이다."""
    jid = srv.queued_job(preset=preset)
    srv.store._conn().execute(
        "UPDATE jobs SET state=?, worker_name='build-02', lane=1, started_at=? WHERE id=?",
        (RUNNING, srv.clock().timestamp(), jid),
    )
    return jid


def test_rl24_an_old_worker_finishes_its_legacy_job_but_cannot_claim_or_register(srv):
    srv.store.register_worker(
        "build-02", pool="default", lanes=1, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv)
    srv.queued_job()
    for path, body in (
        ("/worker/claim", {"lane": 1}),
        ("/worker/register", {"pool": "default", "lanes": 1, "version": __version__}),
    ):
        status, out = srv.req("POST", path, token="build-02", json_body=body, legacy=True)
        assert status == 409 and out["code"] == "protocol_required", out
        assert out["error"] == PROTOCOL_TEXT and "not registered" not in out["error"]
        assert out["worker_protocol"] == 2
    # 레거시 busy 잡이 있는 동안 세션 없는 heartbeat · log · finish 는 받는다
    status, _ = srv.req(
        "POST", "/worker/heartbeat", token="build-02", json_body={"jobs": [jid]}, legacy=True
    )
    assert status == 200
    status, _ = srv.req(
        "POST",
        f"/worker/jobs/{jid}/log",
        token="build-02",
        body=b"hi\n",
        headers={"Content-Type": "application/octet-stream"},
        legacy=True,
    )
    assert status == 200
    status, out = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "succeeded", "exit_code": 0},
        legacy=True,
    )
    assert status == 200, out
    assert job_of(srv, jid).state == SUCCEEDED
    status, out = srv.req(
        "POST", "/worker/heartbeat", token="build-02", json_body={"jobs": []}, legacy=True
    )
    assert status == 409 and out["code"] == "protocol_required"
    # claim 은 한 건도 나가지 않았다
    assert [j.state for j in srv.store.list_active() if j.preset == "ok"] == [QUEUED]


@pytest.mark.parametrize(("preset", "held"), [("ship", True), ("ok", False)])
def test_rl47_the_first_session_closes_legacy_jobs_and_holds_irreversible_ones(srv, preset, held):
    srv.store.register_worker(
        "build-02", pool="default", lanes=1, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv, preset)
    srv.registered("build-02")
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_restarted_without_job"
    rows = hold_rows(srv)
    if held:
        assert [(h["job_id"], h["cause"], h["concurrency_group"]) for h in rows] == [
            (jid, "worker_restarted_without_job", "irreversible:ship")
        ]
    else:
        assert rows == []
        nxt = srv.queued_job()
        assert srv.claimed("build-02", 1) == nxt  # 409 없이 다음 잡


def test_rl47_legacy_and_session_heard_do_not_feed_each_other(srv):
    srv.store.register_worker(
        "build-02", pool="default", lanes=2, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv)
    key = "legacy:build-02"
    srv.clock.advance(10)
    srv.req("POST", "/worker/heartbeat", token="build-02", json_body={"jobs": [jid]}, legacy=True)
    legacy_at = srv.app.heard_at(key)
    srv.clock.advance(10)
    srv.registered("build-02", lanes=2)  # 첫 등록이 레거시 잡을 닫는다
    assert srv.app.heard_at(key) == legacy_at
    assert srv.app.heard_at(srv.session("build-02")) > legacy_at


# ── T5 순번 · 끝난 claim(RL9 · RL50) ───────────────────────────────────────────


@pytest.mark.parametrize(("preset", "held"), [("ok", False), ("ship", True)])
def test_rl9_a_lost_claim_answer_closes_after_the_claim_settled_and_holds_if_irreversible(
    srv, preset, held
):
    srv.registered("build-02")
    jid = srv.queued_job(preset=preset)
    status, _ = srv.claim("build-02", 1)  # 서버는 확정 — 워커는 응답을 잃었다고 친다
    assert status == 200
    assert srv.heartbeat("build-02", jobs=[])[0] == 200
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_restarted_without_job"
    causes = [(h["job_id"], h["cause"]) for h in hold_rows(srv)]
    assert causes == ([(jid, "worker_restarted_without_job")] if held else [])


def test_rl50_stale_or_unsettled_heartbeats_do_not_close_a_running_job(srv):
    srv.registered("build-02")
    jid = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == jid
    claim_seq = job_of(srv, jid).claim_seq
    assert srv.heartbeat("build-02", jobs=[jid], seq=3, claims_settled=claim_seq)[0] == 200
    assert srv.heartbeat("build-02", jobs=[jid], seq=5, claims_settled=claim_seq)[0] == 200
    # 늦게 온 seq 4(claim 전에 찍은 목록) — 받아들인 seq 이하라 판정하지 않는다
    assert srv.heartbeat("build-02", jobs=[], seq=4, claims_settled=claim_seq)[0] == 200
    assert job_of(srv, jid).state == RUNNING
    # seq 는 새것이지만 claims_settled 가 그 claim 보다 작다
    assert srv.heartbeat("build-02", jobs=[], seq=6, claims_settled=claim_seq - 1)[0] == 200
    assert job_of(srv, jid).state == RUNNING
    assert hold_rows(srv) == []
    # 같은 그룹 둘째 잡은 claim 되지 않는다(합성 그룹 · global)
    srv.registered("build-03")
    srv.queued_job(preset="ship")
    assert srv.claim("build-03", 1)[0] == 204


@pytest.mark.parametrize("bad", [True, "1", -1, 2**53])
def test_rl50_seq_and_claims_settled_must_be_json_integers(srv, bad):
    srv.registered("build-02")
    status, body = srv.heartbeat("build-02", jobs=[], seq=bad)
    assert status == 400 and set(body) == {"error"}
    status, body = srv.req(
        "POST", "/worker/claim", token="build-02", json_body={"lane": 1, "claim_seq": bad}
    )
    assert status == 400 and set(body) == {"error"}


# ── 닫힌 잡 통보(RL35) ─────────────────────────────────────────────────────────


def test_rl35_heartbeat_reports_this_sessions_closed_jobs_with_had_cancel(srv):
    srv.registered("build-02", lanes=2)
    a = running(srv, lane=1)
    b = running(srv, lane=2)
    assert srv.cancel(b)[1]["state"] == CANCELLING
    srv.store.finish(a, LOST, now=srv.clock(), summary="x")
    srv.store.finish(b, LOST, now=srv.clock(), summary="x")
    status, body = srv.heartbeat("build-02", jobs=[a, b, 999])
    assert status == 200
    assert body["closed_jobs"] == [
        {"id": a, "state": LOST, "had_cancel": False},
        {"id": b, "state": LOST, "had_cancel": True},
    ]
    srv.registered("build-03")
    status, body = srv.heartbeat("build-03", jobs=[a, b])
    assert body["closed_jobs"] == []  # 다른 세션의 잡은 안 실림


# ── 홀드 풀기 · 인증된 확인(RL44) ─────────────────────────────────────────────


def test_rl44_previous_sessions_release_a_hold_and_a_foreign_name_cannot(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    srv.store.finish(jid, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    assert len(hold_rows(srv)) == 1
    old = srv.session("build-02")
    # (d) 다른 이름의 워커 토큰이 그 세션 id 를 previous_sessions 로 보낸다 → 403, 홀드 그대로
    srv.registered("build-03")
    srv.new_process("build-03")
    status, body = srv.register("build-03", previous_sessions=[old])
    assert status == 403 and set(body) == {"error"}
    assert hold_rows(srv)[0]["released_at"] is None
    # (a) 같은 이름의 새 프로세스가 D 의 그룹이 없음을 확인 → 풀림, 대기 잡이 시작
    waiting = srv.queued_job(preset="ship")
    srv.restarted("build-02")
    (h,) = hold_rows(srv)
    assert h["release_kind"] == "worker" and h["released_by"] == "build-02"
    assert srv.claimed("build-02", 1) == waiting


def test_rl44_a_late_finish_with_group_gone_releases_the_hold(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    srv.store.finish(jid, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    status, body = srv.finish("build-02", jid, "lost")  # group_gone 없음 — 그대로
    assert status == 409 and body["code"] == "job_closed" and body["hold_released"] is False
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "lost", "exit_code": None, "group_gone": True},
    )
    assert status == 409 and body["hold_released"] is True and body["state"] == LOST
    assert hold_rows(srv)[0]["release_kind"] == "worker"


def test_rl44_a_live_irreversible_finish_without_group_gone_holds(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    assert srv.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
    assert [h["cause"] for h in hold_rows(srv)] == ["group_not_gone"]
    nxt = srv.queued_job(preset="ship")
    assert srv.claim("build-02", 1)[0] == 204  # 그 그룹은 막혔다
    assert job_of(srv, nxt).state == QUEUED


# ── 워커 쪽: 등록 전 프로토콜 확인 · 종료 코드(T13 · W5 · W6 · RL25 · RL32) ─────


class _OldServer:
    """프로토콜 키가 없는 옛 서버의 워커 클라이언트 흉내. 부른 것을 적는다."""

    def __init__(self, version: str, *, spec_status: int = 200):
        self.server = "http://old"
        self.token = "t"
        self.calls: list[str] = []
        self.version = version
        self.spec_status = spec_status

    def spec(self) -> dict[str, Any]:
        self.calls.append("GET /worker/spec")
        if self.spec_status != 200:
            raise ClientError(self.spec_status, "not found")
        return {"generation": "1:0", "presets": []}

    def health(self) -> dict[str, Any]:
        self.calls.append("GET /api/health")
        return {"version": self.version}

    def register(self, **kw: Any) -> dict[str, Any]:
        self.calls.append("POST /worker/register")
        raise ClientError(409, "worker version x, server y", {"server_version": self.version})


@pytest.mark.parametrize("spec_status", [200, 404])
def test_rl25_a_new_worker_against_a_protocol_1_server_exits_3_before_registering(
    tmp_path, spec_status
):
    fake = _OldServer(__version__, spec_status=spec_status)
    cfg = WorkerConfig(server="http://old", token="t", data_dir=str(tmp_path / "w"))
    for _ in range(3):  # 감독자가 되살려도 같다
        w = RemoteWorker(cfg, client=fake, log=lambda m: None)
        with pytest.raises(WorkerExit) as e:
            w.run()
        assert e.value.code == EXIT_PROTOCOL
        assert "protocol" in e.value.message
    assert "POST /worker/register" not in fake.calls
    assert set(fake.calls) <= {"GET /worker/spec", "GET /api/health"}
    assert not (tmp_path / "w" / "worker-sessions").exists()


def test_rl32_a_different_version_goes_the_register_path_and_follows(tmp_path):
    fake = _OldServer("9.9.9")
    cfg = WorkerConfig(server="http://old", token="t", data_dir=str(tmp_path / "w"))
    w = RemoteWorker(cfg, client=fake, log=lambda m: None)
    with pytest.raises(WorkerExit) as e:
        w.run()
    assert e.value.code == 2  # WorkerFollow(따라가기) 또는 종료 2 — 종료 3 이 아니다
    assert fake.calls[-1] == "POST /worker/register"


def test_w5_a_fenced_worker_process_stops_and_exits_zero(srv, tmp_path):
    """W5: 살아 있는 프로세스의 요청이 409 session_fenced 면 모든 레인을 멈추고 종료 0."""
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    w = RemoteWorker(cfg, log=lambda m: None)
    register = w.register

    def short_polls() -> dict[str, Any]:
        # 레인의 long-poll 을 1초로 — 펜싱 뒤 레인이 20초 claim 을 다 기다리지 않게(시험 시간만)
        answer = register()
        w.claim_wait_seconds = 1
        return answer

    w.register = short_polls  # type: ignore[method-assign]
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.update(code=w.run()), daemon=True)
    t.start()
    for _ in range(200):
        if srv.store.get_session(w.session_id) is not None:
            break
        threading.Event().wait(0.05)
    srv.new_process("build-02")
    srv.registered("build-02")  # 같은 토큰의 새 프로세스가 등록 → w 는 펜싱됐다
    w.heartbeat_once()
    t.join(30)
    assert not t.is_alive()
    assert out["code"] == 0 and w.fenced.is_set()


def test_parse_preset_accepts_the_irreversible_key_used_here():
    assert parse_preset(sh("ship", "echo ship", irreversible=True)).irreversible is True


# ── 세션 폴더 · 고아 정리(§4.4 · RL14 b · RL15 · RL31 · RL38) ─────────────────


def _spawn_group(argv: list[str]) -> subprocess.Popen:
    p = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL)
    # 진짜 고아는 init 이 거둔다 — 시험의 자식이 좀비로 남아 그룹을 붙들지 않게 곧바로 거둔다
    threading.Thread(target=p.wait, daemon=True).start()
    return p


def _kill(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _dead_job(tmp_path: Path, proc: dict[str, Any] | None, job_id: int = 5) -> Path:
    d = tmp_path / "jobs" / str(job_id)
    d.mkdir(parents=True)
    if proc is not None:
        (d / "proc.json").write_text(json.dumps(proc), encoding="utf-8")
    return d


def _gone(p: subprocess.Popen, seconds: float = 5.0) -> bool:
    for _ in range(int(seconds / 0.02)):
        if p.poll() is not None:
            return True
        time.sleep(0.02)
    return False


def test_rl15_linux_start_reads_field_22_after_the_last_parenthesis(tmp_path):
    root = tmp_path / "proc"
    (root / "42").mkdir(parents=True)
    fields = " ".join(["S"] + [str(n) for n in range(4, 22)] + ["987654"] + ["0"] * 30)
    (root / "42" / "stat").write_text(f"42 (we) ird (name)) {fields}\n", encoding="ascii")
    assert rw.linux_start(42, root) == "linux:987654"
    assert rw.linux_start(43, root) is None


def test_rl15_ps_is_read_in_the_c_locale_and_utc_and_a_locale_answer_is_unknown(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_run(argv, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(argv, 0, stdout=seen["answer"], stderr="")

    monkeypatch.setattr(rw.subprocess, "run", fake_run)
    seen["answer"] = " Wed Nov 25 18:32:46 2026\n"
    assert rw.ps_start(1) == "ps:Wed Nov 25 18:32:46 2026"
    assert seen.get("LC_ALL") == "C" and seen.get("TZ") == "UTC"
    seen["answer"] = "2026년 11월 26일 목요일 03시 32분 46초\n"
    assert rw.ps_start(1) is None


def test_rl15_the_same_group_is_stopped_and_confirmed(tmp_path):
    p = _spawn_group(["sleep", "30"])
    try:
        d = _dead_job(tmp_path, {"pgid": p.pid, "start": rw.process_start(p.pid)})
        logs: list[str] = []
        assert rw.judge_orphan(d, 1.0, logs.append) == rw.CONFIRMED
        assert _gone(p)
        assert any("stopped process group" in m for m in logs)
    finally:
        _kill(p.pid)


def test_rl15_a_reused_pid_is_left_alone_and_confirmed(tmp_path):
    p = _spawn_group(["sleep", "30"])
    try:
        other = "ps:Mon Jan  1 00:00:00 2001" if sys.platform != "linux" else "linux:1"
        d = _dead_job(tmp_path, {"pgid": p.pid, "start": other})
        assert rw.judge_orphan(d, 1.0, lambda m: None) == rw.CONFIRMED
        assert p.poll() is None  # 무관한 프로세스가 산다
    finally:
        _kill(p.pid)


def test_rl31_identity_unknown_cases_send_no_signal(tmp_path, monkeypatch):
    # (c) pgid null — 띄우는 순간 죽었을 수 있다
    assert rw.judge_orphan(_dead_job(tmp_path, {"pgid": None, "start": None}, 1), 1, print) == (
        rw.UNKNOWN
    )
    # proc.json 이 없다 — 띄운 적 없음(결정 333)
    assert rw.judge_orphan(_dead_job(tmp_path, None, 2), 1, print) == rw.CONFIRMED
    # (a) 신원을 못 읽는다
    p = _spawn_group(["sleep", "30"])
    try:
        d = _dead_job(tmp_path, {"pgid": p.pid, "start": rw.process_start(p.pid)}, 3)
        monkeypatch.setattr(rw, "process_start", lambda pid: None)
        assert rw.judge_orphan(d, 1, lambda m: None) == rw.UNKNOWN
        assert p.poll() is None
    finally:
        _kill(p.pid)


def test_rl31_a_leaderless_group_is_unknown_and_left_alone(tmp_path):
    """(b) 리더(pid = pgid)가 죽고 멤버만 산다(F59 · W13)."""
    pidfile = tmp_path / "member.pid"
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(30); os._exit(0)\n"
        f"while not os.path.exists({str(pidfile)!r}): time.sleep(0.02)\n"
    )
    leader = _spawn_group([sys.executable, "-c", code])
    leader.wait(10)
    member = int(pidfile.read_text())
    try:
        d = _dead_job(tmp_path, {"pgid": leader.pid, "start": "ps:Mon Jan  1 00:00:00 2001"})
        assert rw.judge_orphan(d, 1, lambda m: None) == rw.UNKNOWN
        os.kill(member, 0)  # 아직 산다
    finally:
        _kill(leader.pid)


def _worker(tmp_path: Path, fake: Any, **cfg: Any) -> RemoteWorker:
    config = WorkerConfig(
        server="http://srv", token="t", data_dir=str(tmp_path / "w"), lanes=1, **cfg
    )
    w = RemoteWorker(config, client=fake, log=lambda m: None)
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    return w


class _Who:
    """whoami · register · finish 만 하는 대역."""

    server = "http://srv"

    def __init__(self, name: str = "build-02") -> None:
        self.name = name
        self.registered: list[dict[str, Any]] = []
        self.finished: list[tuple[str, int, dict[str, Any]]] = []
        self.session_id = ""

    def whoami(self) -> dict[str, Any]:
        return {"name": self.name, "kind": "worker", "admin": False}

    def register(self, **kw: Any) -> dict[str, Any]:
        self.registered.append(kw)
        return {"name": self.name, "heartbeat_seconds": 5, "worker_timeout_seconds": 60}

    def finish(self, job_id: int, outcome: str, rc: Any, summary: Any, **extra: Any) -> dict:
        self.finished.append((self.session_id, job_id, {"outcome": outcome, **extra}))
        return {}


def _dead_folder(w: RemoteWorker, owner: dict[str, str] | None, jobs: dict[int, Any]) -> Path:
    sid = secrets.token_hex(16)
    d = w.config.data_path / rw.SESSIONS_DIR / sid
    d.mkdir(parents=True)
    (d / "lock").write_text("", encoding="utf-8")
    if owner is not None:
        (d / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
    for jid, proc in jobs.items():
        j = d / "jobs" / str(jid)
        j.mkdir(parents=True)
        (j / "claim.json").write_text(
            json.dumps({"irreversible": True, "disconnect_grace_seconds": 600}), encoding="utf-8"
        )
        if proc is not None:
            (j / "proc.json").write_text(json.dumps(proc), encoding="utf-8")
    return d


def test_rl14_dead_sessions_go_to_previous_or_unverified_in_the_first_register(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    gone = _dead_folder(w, owner, {3: None})  # 띄운 적 없음 → 확인됨
    unsure = _dead_folder(w, owner, {4: {"pgid": None, "start": None}})  # 모름
    w.prepare_sessions()
    w.register()
    assert fake.registered[0].get("previous_sessions") == [gone.name]
    assert fake.registered[0]["unverified_sessions"] == [unsure.name]
    w.register()  # 다시 등록할 때는 싣지 않는다
    assert "previous_sessions" not in fake.registered[1]
    own = w.session_dir
    assert own is not None and own.name == w.session_id
    assert json.loads((own / "owner.json").read_text()) == owner
    assert "t" not in json.dumps(owner).replace("token_name", "")  # 토큰 값은 안 적는다


def test_rl38_a_foreign_or_ownerless_folder_is_not_locked_judged_or_touched(tmp_path):
    p = _spawn_group(["sleep", "30"])
    try:
        fake = _Who()
        w = _worker(tmp_path, fake)
        proc = {"pgid": p.pid, "start": rw.process_start(p.pid)}
        other = rw.owner_of(
            WorkerConfig(server="http://other", token="x", data_dir=str(tmp_path)), "tester"
        )
        foreign = _dead_folder(w, other, {9: proc})
        ownerless = _dead_folder(w, None, {10: proc})
        before = {
            f: f.read_bytes() for d in (foreign, ownerless) for f in d.rglob("*") if f.is_file()
        }
        w.prepare_sessions()
        assert p.poll() is None  # 남의 고아를 죽이지 않는다
        assert w.previous_sessions == [] and w.unverified_sessions == []
        after = {
            f: f.read_bytes() for d in (foreign, ownerless) for f in d.rglob("*") if f.is_file()
        }
        assert after == before
    finally:
        _kill(p.pid)


def test_rl38_the_sweep_deletes_only_old_and_lockable_folders(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    old = _dead_folder(w, None, {})
    held = _dead_folder(w, None, {})
    fresh = _dead_folder(w, None, {})
    week = time.time() - 8 * 86400
    for d in (old, held):
        os.utime(d, (week, week))
    fd = rw.try_lock(held / "lock")
    try:
        rw.sweep_sessions(w.config.data_path)
        assert not old.exists() and held.exists() and fresh.exists()
    finally:
        assert fd is not None
        os.close(fd)


def test_rl31_the_recheck_confirms_and_sends_one_narrow_finish_as_the_dead_session(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    d = _dead_folder(w, owner, {4: {"pgid": None, "start": None}})
    w.prepare_sessions()
    (dead,) = w.dead_sessions
    assert w.recheck(dead, 4) == rw.UNKNOWN and fake.finished == []  # 그래도 모름 — 안 보냄
    (d / "jobs" / "4" / "proc.json").unlink()  # 이제 확인됨(띄운 적 없음)
    assert w.recheck(dead, 4) == rw.CONFIRMED
    (sent,) = fake.finished
    assert sent[0] == d.name and sent[1] == 4  # D 의 세션 id 로
    assert sent[2] == {
        "outcome": LOST,
        "summary_code": "worker_replaced",
        "summary_args": {},
        "group_gone": True,
    }
    assert fake.session_id == w.session_id  # 살아 있는 세션의 클라이언트는 그대로


def test_rl31_the_recheck_is_scheduled_at_ok_at_plus_g_minus_m(tmp_path, monkeypatch):
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    d = _dead_folder(w, owner, {4: {"pgid": None, "start": None}})
    ok = w._epoch() - 100
    (d / "ok_at.json").write_text(
        json.dumps({"wall": datetime.fromtimestamp(ok, UTC).isoformat()}), encoding="utf-8"
    )
    timers: list[float] = []

    class T:
        def __init__(self, delay, fn, args=()):
            timers.append(delay)
            self.daemon = False
            self.name = ""

        def start(self):
            pass

    monkeypatch.setattr(rw.threading, "Timer", T)
    w.prepare_sessions()
    w.schedule_rechecks()
    (delay,) = timers
    assert abs(delay - (600 - 35 - 100)) < 2  # D.ok_at + G − m − 지금


def test_rl38_the_session_files_follow_the_job(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()
    w._write_claim(7, True, 600)
    w._write_proc(7, None)
    jd = w.session_dir / "jobs" / "7"
    assert json.loads((jd / "proc.json").read_text()) == {"pgid": None, "start": None}
    w._write_proc(7, os.getpid())
    proc = json.loads((jd / "proc.json").read_text())
    assert proc["pgid"] == os.getpid() and rw._start_valid(proc["start"])
    assert json.loads((jd / "claim.json").read_text()) == {
        "irreversible": True,
        "disconnect_grace_seconds": 600,
    }
    w.mark_ok(w._epoch(), 10_000.0)
    assert "wall" in json.loads((w.session_dir / "ok_at.json").read_text())
    w._forget_job_dir(7)
    assert not jd.exists()


# ── admin 이 푸는 길(RL45 PR1 몫 · §4.12 · 결정 330) ────────────────────────────


def _held(srv: WorkerServer) -> int:
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    srv.store.finish(jid, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    assert len(hold_rows(srv)) == 1
    return jid


def test_rl45_an_admin_releases_the_hold_with_a_cleaned_reason(srv):
    jid = _held(srv)
    waiting = srv.queued_job(preset="ship")
    assert srv.claim("build-02", 1)[0] == 204  # 홀드가 그룹을 막는다
    reason = "checked \x1b[31mbuild-02\x07: no process\n" + "x" * 300
    status, body = srv.req(
        "POST", f"/jobs/{jid}/release-hold", token="admin", json_body={"reason": reason}
    )
    assert status == 200
    assert set(body) == {"job_id", "group", "released_at", "released_by", "release_kind"}
    assert body["job_id"] == jid and body["group"] == "irreversible:ship"
    assert body["release_kind"] == "admin" and body["released_by"] == "macmini-admin"
    (h,) = hold_rows(srv)
    assert h["release_kind"] == "admin"
    assert len(h["release_reason"]) == 200
    assert not any(ord(c) < 32 or ord(c) == 127 for c in h["release_reason"])
    assert srv.claimed("build-02", 1) == waiting  # 그룹이 다시 열렸다


def test_rl45_release_hold_answers_403_404_and_409_no_hold(srv):
    jid = _held(srv)
    status, body = srv.req("POST", f"/jobs/{jid}/release-hold", token="alice", json_body={})
    assert status == 403
    assert hold_rows(srv)[0]["released_at"] is None
    status, body = srv.req("POST", "/jobs/999/release-hold", token="admin", json_body={})
    assert status == 404
    assert srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={})[0] == 200
    status, body = srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={})
    assert status == 409 and body["code"] == "no_hold" and "error" in body
    status, _ = srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={"reason": 5})
    assert status == 400


class _HoldClient:
    server = "http://srv"
    token = "t"

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[int, str | None]] = []

    def release_hold(self, job_id: int, *, reason: str | None = None) -> dict[str, Any]:
        self.calls.append((job_id, reason))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize(
    "answer, code",
    [
        ({"job_id": 7, "group": "g", "released_at": "t", "released_by": "a"}, 0),
        (ClientError(409, "job #7 has no open hold", {"code": "no_hold"}), 1),
        (ClientError(403, "admin token required"), 2),
        (ClientError(404, "no job #7"), 2),
        (ClientError(401, "token required"), 2),
        (ClientError(0, "connection refused"), 3),
    ],
)
def test_rl45_rcm_release_hold_exit_codes(monkeypatch, capsys, answer, code):
    from remote_ci_monitor import cli

    fake = _HoldClient(answer)
    monkeypatch.setattr(cli, "_client", lambda args: fake)
    assert cli.main(["release-hold", "7", "--reason", "checked", "--json"]) == code
    assert fake.calls == [(7, "checked")]
    out, err = capsys.readouterr()
    if code == 0:
        assert json.loads(out)["group"] == "g"
        assert "released the hold on group g (job #7)" in err


def test_rl45_rcm_release_hold_is_its_own_command_with_a_help_line(capsys):
    from remote_ci_monitor import cli

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "release-hold" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["release-hold", "--help"])
    out = capsys.readouterr().out
    assert "after you checked the machine" in out and "--reason" in out


# ── 홀드의 기계 판독 표면(RL45 의 PR1 몫 · §4.8 · 결정 330 「어디서나 needs_action」 · 350) ──

HOLD_ITEM_KEYS = {"job_id", "preset", "group", "scope", "worker", "cause", "since"}
HOLD_DOC_KEYS = {
    "cause",
    "created_at",
    "released_at",
    "release_kind",
    "released_by",
    "release_reason",
    "history",
}


@pytest.fixture
def srv2(tmp_path):
    s = WorkerServer(tmp_path, presets=[*PRESETS, sh("ship2", "echo ship2", irreversible=True)])
    yield s
    s.close()


def test_rl45_a_job_waiting_on_a_held_group_needs_action_with_the_hold_as_blocker(srv):
    jid = _held(srv)
    waiting = srv.queued_job(preset="ship")
    other = srv.queued_job(preset="ok")  # 다른 그룹 — 홀드와 무관
    row = srv.row(waiting)
    assert row["reason"] == "needs_action"
    assert row["action"]["code"] == "irreversible_hold"
    assert f"rcm release-hold {jid}" in row["action"]["note"]
    assert row["action"]["at"] is not None
    assert row["blocked_by"] == {
        "job_id": jid,
        "group": "irreversible:ship",
        "remaining_seconds": None,
    }
    assert row["estimate"]["finish_at"] is None
    assert row["estimate"]["wait_seconds"] is None
    assert srv.row(other)["reason"] != "needs_action"
    assert srv.row(other)["blocked_by"] is None
    # 풀면 그 사유가 사라진다
    assert srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={})[0] == 200
    after = srv.row(waiting)
    assert after["reason"] != "needs_action" and after["action"] is None


def test_rl45_status_and_health_list_every_open_hold_and_health_stays_as_it_was(srv2):
    srv = srv2
    srv.registered("build-02")
    code0, health0 = srv.req("GET", "/api/health")
    assert health0["irreversible_holds"] == []
    assert srv.status()["irreversible_holds"] == []
    a = _held(srv)
    b = running(srv, preset="ship2")
    srv.store.finish(b, LOST, now=srv.clock(), summary="x", hold_cause="orphan_unverified")
    srv.queued_job(preset="ship")  # 첫 홀드에만 대기 잡이 있다
    holds = srv.status()["irreversible_holds"]
    assert [h["job_id"] for h in holds] == [a, b]
    assert all(set(h) == HOLD_ITEM_KEYS for h in holds)
    assert holds[0]["group"] == "irreversible:ship" and holds[0]["scope"] == "global"
    assert holds[0]["preset"] == "ship" and holds[0]["worker"] == "build-02"
    assert holds[1]["cause"] == "orphan_unverified"
    assert holds[0]["since"].endswith("Z")
    code1, health1 = srv.req("GET", "/api/health")
    assert [h["job_id"] for h in health1["irreversible_holds"]] == [a, b]
    # 정보일 뿐이다 — 홀드로 ok · 503 이 바뀌지 않는다
    assert (code1, health1["ok"]) == (code0, health0["ok"])
    srv.req("POST", f"/jobs/{a}/release-hold", token="admin", json_body={})
    assert [h["job_id"] for h in srv.status()["irreversible_holds"]] == [b]


def test_rl45_the_job_document_carries_the_hold_and_its_release(srv):
    srv.registered("build-02")
    plain = running(srv, preset="ok")
    srv.finish("build-02", plain, "succeeded", exit_code=0)
    assert srv.req("GET", f"/jobs/{plain}", token="alice")[1]["hold"] is None
    jid = _held(srv)
    doc = srv.req("GET", f"/jobs/{jid}", token="alice")[1]
    assert set(doc["hold"]) == HOLD_DOC_KEYS
    assert doc["hold"]["cause"] == "worker_unreachable"
    assert doc["hold"]["released_at"] is None
    assert [h["action"] for h in doc["hold"]["history"]] == ["created"]
    srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={"reason": "ps -g: none"})
    hold = srv.req("GET", f"/jobs/{jid}", token="alice")[1]["hold"]
    assert hold["released_by"] == "macmini-admin"
    assert hold["release_reason"] == "ps -g: none"
    assert hold["release_kind"] == "admin" and hold["released_at"].endswith("Z")
    assert [h["action"] for h in hold["history"]] == ["created", "released"]
    # 대기 잡 문서도 `hold` 키를 싣는다(그 잡 자신의 홀드 — 없으면 null)
    waiting = srv.queued_job(preset="ship")
    assert srv.req("GET", f"/jobs/{waiting}", token="alice")[1]["hold"] is None
