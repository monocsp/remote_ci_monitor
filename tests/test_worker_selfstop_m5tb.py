"""끊김 유예 설정 · 자기 멈춤(M5t-b §4.3 · 결정 253 · 263 · 320 · 335).

설정 검증과 claim 얼림을 잠근다. 시험 서버는 `test_worker_api.WorkerServer`(주입 시계)다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_ci_monitor.config import (
    DISCONNECT_GRACE_MARGIN_SECONDS,
    ConfigError,
    ServerSection,
    disconnect_grace,
    load_server_config,
)
from remote_ci_monitor.core.model import LOST, RUNNING
from test_worker_api import WORKER_PRESETS, WorkerServer, sh

GOOD = '[[presets]]\nname = "gate"\nargv = ["true"]\n'


def load(tmp_path: Path, server: str) -> object:
    p = tmp_path / "server.toml"
    p.write_text("[server]\n" + server + "\n" + GOOD, encoding="utf-8")
    return load_server_config(p, environ={})


def test_rl30_the_default_grace_is_600_and_a_large_timeout_moves_it_up(tmp_path):
    assert disconnect_grace(ServerSection()) == 600
    cfg = load(tmp_path, "")
    assert cfg.server.disconnect_grace_seconds is None  # 적지 않음
    assert disconnect_grace(cfg.server) == 600
    # 키를 안 적은 큰 worker_timeout_seconds(업그레이드) — 기동 실패 없이 max(600, L + 35)
    cfg = load(tmp_path, "worker_timeout_seconds = 900")
    assert disconnect_grace(cfg.server) == 935


@pytest.mark.parametrize(
    ("keys", "floor"),
    [
        ("", 95),  # T 60 · H 5 → L = max(60, 10 + 45 + 5) = 60
        ("worker_timeout_seconds = 10\nworker_heartbeat_seconds = 1", 87),  # e2e 설정(§7.5)
        ("worker_timeout_seconds = 120\nworker_heartbeat_seconds = 5", 155),
    ],
)
def test_rl30_the_floor_is_l_plus_35_and_the_floor_itself_passes(tmp_path, keys, floor):
    cfg = load(tmp_path, f"{keys}\ndisconnect_grace_seconds = {floor}")
    assert disconnect_grace(cfg.server) == floor
    with pytest.raises(ConfigError) as e:
        load(tmp_path, f"{keys}\ndisconnect_grace_seconds = {floor - 1}")
    text = str(e.value)
    assert "[server] disconnect_grace_seconds" in text
    assert str(floor) in text and "remove the key" in text


@pytest.mark.parametrize("value", ["86401", "-5", "0", '"soon"', "true", "1.5"])
def test_rl30_out_of_range_or_not_an_integer_fails_to_start(tmp_path, value):
    with pytest.raises(ConfigError, match=r"\[server\] disconnect_grace_seconds"):
        load(tmp_path, f"disconnect_grace_seconds = {value}")


def test_rl30_the_margin_is_the_default_m():
    """config 의 하한 여유와 기본 워커 grace 10 일 때의 m(15 + 10 + 10)이 같다(결정 320)."""
    assert DISCONNECT_GRACE_MARGIN_SECONDS == 15 + 10 + 10


def test_rl30_claim_freezes_the_grace_and_a_reload_changes_only_the_next_claim(tmp_path):
    srv = WorkerServer(
        tmp_path,
        presets=[*WORKER_PRESETS, sh("ship", "echo ship", irreversible=True)],
        disconnect_grace_seconds=300,
    )
    try:
        srv.registered("build-02", lanes=2)
        a = srv.queued_job(preset="ship")
        status, body = srv.claim("build-02", 1)
        assert status == 200
        assert body["job"]["irreversible"] is True
        assert body["job"]["disconnect_grace_seconds"] == 300
        assert set(body) == {"artifacts", "job", "tree_url", "preset"}  # 최상위 키는 그대로
        # 서버 설정이 바뀌어도(재시작 흉내) 도는 잡은 얼린 값을 쓴다
        srv.cfg.server.disconnect_grace_seconds = 900
        b = srv.queued_job()
        status, body = srv.claim("build-02", 2)
        assert body["job"]["id"] == b and body["job"]["irreversible"] is False
        assert body["job"]["disconnect_grace_seconds"] == 900
        assert srv.store.get_job(a).disconnect_grace_seconds == 300
    finally:
        srv.close()


# ── 서버 유예(T2 · T3 · T4 · T8 · 결정 266 · 328 · 330 · 339-g) ─────────────────

G = 600
SHIP = [*WORKER_PRESETS, sh("ship", "echo ship", irreversible=True)]


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, presets=SHIP, lanes=0)
    yield s
    s.close()


def holds(srv: WorkerServer) -> list[dict]:
    return [dict(r) for r in srv.store._conn().execute("SELECT * FROM irreversible_holds")]


def test_rl4_the_grace_boundary_rechecks_heard_and_closes_exactly_once(srv):
    srv.registered("build-02", lanes=2)
    a = srv.queued_job(preset="ship")
    b = srv.queued_job()
    assert srv.claimed("build-02", 1) == a
    assert srv.claimed("build-02", 2) == b
    srv.clock.advance(G)  # gap == G — 아직 닫지 않는다(gap > G 일 때만)
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    srv.clock.advance(1)
    # 경계에서 heartbeat 이 먼저 들면 닫지 않는다 — 판정은 세션 잠금 안에서 heard 를 다시 본다
    assert srv.heartbeat("build-02", jobs=[a, b])[0] == 200
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    srv.clock.advance(G + 1)
    assert sorted(srv.app.mark_lost_workers(srv.clock.now)) == sorted([a, b])
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    # 늦은 finish 는 상태를 뒤집지 않는다(409 job_closed)
    status, body = srv.finish("build-02", b, "succeeded", exit_code=0)
    assert status == 409 and body["code"] == "job_closed"
    assert srv.store.get_job(b).state == LOST
    # 표식 잡의 확인 없는 닫힘은 홀드, 일반 잡은 홀드 0(RL5)
    assert [(h["job_id"], h["cause"]) for h in holds(srv)] == [(a, "worker_unreachable")]


def test_rl27_a_wall_clock_jump_does_not_close_a_heard_job(srv):
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    srv.clock.advance(5)
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    srv.clock.jump(G * 3)  # NTP 단계 보정 — 벽시계만 뛴다
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert srv.store.get_job(a).state == RUNNING


def test_rl33_a_fenced_sessions_cancelling_job_is_not_closed_as_unconfirmed(srv):
    srv.registered("build-02")
    a = srv.queued_job()
    assert srv.claimed("build-02", 1) == a
    assert srv.cancel(a)[1]["state"] == "cancelling"
    srv.new_process("build-02")
    srv.registered("build-02")  # B 가 살아서 heartbeat 를 보낸다
    for _ in range(10):
        srv.clock.advance(5)
        assert srv.heartbeat("build-02", jobs=[])[0] == 200
        assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert srv.store.get_job(a).state == "cancelling"  # kill_at + 2H 가 지났어도
    srv.clock.advance(G)
    assert srv.app.mark_lost_workers(srv.clock.now) == [a]
    j = srv.store.get_job(a)
    assert j.state == LOST and j.summary_code == "worker_unreachable"


@pytest.mark.parametrize(
    ("preset", "code", "held"),
    [
        ("ship", "orphan_unverified", True),
        ("ok", "worker_unreachable", False),
    ],
)
def test_rl31_an_unverified_dead_session_closes_at_grace_as_orphan_unverified(
    srv, preset, code, held
):
    srv.registered("build-02")
    a = srv.queued_job(preset=preset)
    assert srv.claimed("build-02", 1) == a
    old = srv.new_process("build-02")
    srv.registered("build-02", unverified_sessions=[old])  # 고아 신원을 확인하지 못했다
    assert srv.store.get_job(a).state == RUNNING  # 즉시 닫지 않는다 — G 까지 그룹을 쥔다
    srv.clock.advance(G + 1)
    assert srv.app.mark_lost_workers(srv.clock.now) == [a]
    j = srv.store.get_job(a)
    assert j.state == LOST and j.summary_code == code
    assert [h["cause"] for h in holds(srv)] == ([code] if held else [])


def test_rl1_rl2_a_returning_workers_group_gone_finish_releases_the_grace_hold(srv):
    """서버가 먼저 닫으면 그 닫힘에 홀드가 걸리고, 돌아온 워커의 finish(`group_gone: true`)가
    푼다."""
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    waiting = srv.queued_job(preset="ship")
    srv.clock.advance(G + 1)
    assert srv.app.mark_lost_workers(srv.clock.now) == [a]
    assert srv.claim("build-02", 1)[0] == 204  # 홀드가 그룹을 막는다(RL3 · RL42)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{a}/finish",
        token="build-02",
        json_body={
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_self_stopped",
            "summary_args": {"seconds": 566},
            "group_gone": True,
        },
    )
    assert status == 409 and body["hold_released"] is True
    assert srv.store.get_job(a).summary_code == "worker_unreachable"  # 첫 닫힘이 이긴다
    assert srv.claimed("build-02", 1) == waiting


def test_rl56_a_tick_closes_at_most_twenty_and_other_sessions_are_not_blocked(srv, monkeypatch):
    srv.registered("build-02", lanes=30)
    srv.registered("build-03")
    jobs = [srv.queued_job() for _ in range(30)]
    for lane, jid in enumerate(jobs, start=1):
        assert srv.claimed("build-02", lane) == jid
    srv.clock.advance(G + 1)
    first = srv.app.mark_lost_workers(srv.clock.now)
    assert len(first) == 20
    second = srv.app.mark_lost_workers(srv.clock.now)
    assert len(second) == 10 and set(first) | set(second) == set(jobs)
    # 닫기 트랜잭션 하나가 멈춘 동안에도 다른 세션의 heartbeat 은 2xx 를 받는다
    import threading

    gate, entered = threading.Event(), threading.Event()
    real = srv.store.finish

    def slow_finish(*a, **kw):
        entered.set()
        gate.wait(5)
        return real(*a, **kw)

    c = srv.queued_job()
    assert srv.claimed("build-03", 1) == c
    srv.clock.advance(G + 1)
    srv.registered("build-02", lanes=30)  # build-02 는 다시 들림 — build-03 만 조용하다
    monkeypatch.setattr(srv.store, "finish", slow_finish)
    t = threading.Thread(target=lambda: srv.app.mark_lost_workers(srv.clock.now))
    t.start()
    assert entered.wait(5)
    status, _ = srv.heartbeat("build-02", jobs=[])
    gate.set()
    t.join(10)
    assert status == 200
    assert srv.store.get_job(c).state == LOST
