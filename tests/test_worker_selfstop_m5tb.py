"""끊김 유예 설정 · 자기 멈춤(M5t-b §4.3 · 결정 253 · 263 · 320 · 335).

설정 검증과 claim 얼림을 잠근다. 시험 서버는 `test_worker_api.WorkerServer`(주입 시계)다.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import remote_worker as rw
from remote_ci_monitor.client import ClientError
from remote_ci_monitor.config import (
    DISCONNECT_GRACE_MARGIN_SECONDS,
    ConfigError,
    ServerSection,
    WorkerConfig,
    disconnect_grace,
    load_server_config,
)
from remote_ci_monitor.core.model import CANCELLED, LOST, RUNNING, SUCCEEDED
from remote_ci_monitor.runner import RunResult
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


# ══ 워커 쪽: 닫힌 잡 통보(W4) · 자기 멈춤 감시(W3) ═══════════════════════════════
#
# 워커는 가짜 클라이언트로, 러너는 관찰자를 보며 도는 대역으로 돌린다(§7.5 「가짜 러너」). 벽시계와
# 단조 시계는 따로 주입한다(RL26 · RL34).


T0 = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)


class Clocks:
    """벽시계와 단조 시계를 따로 민다 — 잠든 머신은 벽만 가고 단조는 거의 멈춘다(RL26)."""

    def __init__(self) -> None:
        self.wall = T0
        self.mono = 1000.0

    def now(self) -> datetime:
        return self.wall

    def mono_fn(self) -> float:
        return self.mono

    def advance(self, wall: float, mono: float | None = None) -> None:
        self.wall += timedelta(seconds=wall)
        self.mono += wall if mono is None else mono


class FakeClient:
    """`/worker/*` 대역. heartbeat 는 `hb_gate` 가 열릴 때까지 매달릴 수 있다."""

    server = "http://fake"

    def __init__(self) -> None:
        self.finishes: list[dict[str, Any]] = []
        self.hb_out: dict[str, Any] = {}
        self.hb_gate: threading.Event | None = None
        self.hb_calls = 0
        self.log_error: ClientError | None = None
        self.finish_error: ClientError | None = None
        self.session_id = ""

    def heartbeat(self, jobs, host=None, *, seq=None, claims_settled=None):
        self.hb_calls += 1
        if self.hb_gate is not None:
            self.hb_gate.wait(30)
        return dict(self.hb_out)

    def phase(self, job_id, phase):
        return {}

    def log(self, job_id, chunk):
        if self.log_error is not None:
            raise self.log_error
        return {}

    def finish(self, job_id, outcome, rc, summary, **extra):
        self.finishes.append({"job": job_id, "outcome": outcome, "summary": summary, **extra})
        if self.finish_error is not None:
            raise self.finish_error
        return {}


def make_worker(tmp_path: Path, clocks: Clocks | None = None, **cfg: Any):
    clocks = clocks or Clocks()
    fake = FakeClient()
    config = WorkerConfig(
        server="http://fake", token="t", data_dir=str(tmp_path / "w"), lanes=1, **cfg
    )
    logs: list[str] = []
    w = rw.RemoteWorker(
        config, client=fake, now_fn=clocks.now, mono_fn=clocks.mono_fn, log=logs.append
    )
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    return w, fake, clocks, logs


def claimed(job_id: int = 7, *, irreversible: bool, grace: int | None = 600) -> dict[str, Any]:
    return {
        "job": {
            "id": job_id,
            "preset": "ship" if irreversible else "ok",
            "state": RUNNING,
            "irreversible": irreversible,
            "disconnect_grace_seconds": grace,
            "source": {"mode": "tree"},
        },
        "preset": {"name": "ship" if irreversible else "ok", "argv": ["sh", "-c", "sleep 30"]},
    }


class FakeRunner:
    """`run_job` 대역 — 관찰자의 멈춤 · 취소를 0.01초마다 본다. `release` 가 서면 성공으로 끝."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.observer: Any = None

    def __call__(self, spec, observer, **kw):
        self.observer = observer
        self.started.set()
        at = kw["now_fn"]()
        for _ in range(3000):
            if observer.should_stop():
                return RunResult(rc=None, started=at, finished=at, lost=True)
            if observer.should_cancel():
                return RunResult(rc=-15, started=at, finished=at, cancelled=True)
            if self.release.is_set():
                return RunResult(rc=0, started=at, finished=at)
            time.sleep(0.01)
        raise AssertionError("fake runner was never stopped")


def start_job(w, monkeypatch, job: dict[str, Any]):
    runner = FakeRunner()
    monkeypatch.setattr(rw, "run_job", runner)
    t = threading.Thread(target=w.run_claimed, args=(1, job), daemon=True)
    t.start()
    assert runner.started.wait(5)
    return runner, t


def finish_of(fake: FakeClient, t: threading.Thread) -> dict[str, Any]:
    t.join(10)
    assert not t.is_alive()
    assert len(fake.finishes) == 1, fake.finishes
    return fake.finishes[0]


def closed_hb(w, fake: FakeClient, job_id: int, state: str, had_cancel: bool) -> None:
    fake.hb_out = {"closed_jobs": [{"id": job_id, "state": state, "had_cancel": had_cancel}]}
    assert w.heartbeat_once() is True


# ── W4 표 (RL35 · RL5 · RL8 워커 쪽) ──────────────────────────────────────────


@pytest.mark.parametrize(
    "state, had_cancel, irreversible, stops",
    [
        (LOST, False, False, False),  # 일반 · 취소 없음 → 계속(결정 253 · 322)
        (LOST, True, False, True),  # had_cancel → 멈춤(RL8 b)
        (LOST, False, True, True),  # 표식 → 멈춤
        (CANCELLED, False, False, True),
        (CANCELLED, False, True, True),
    ],
)
def test_rl35_closed_jobs_follow_the_w4_table(
    tmp_path, monkeypatch, state, had_cancel, irreversible, stops
):
    """W4 표(§4.1.2, V2-6): 멈춤은 `closed_stop` → `should_cancel` 길이다(결정 334). 멈추지 않는
    일반 `lost` 잡은 끝나면 finish 를 보낸다(서버는 그것을 `late_result` 로만 남긴다)."""
    # RL8 (b) 워커 쪽: 취소 요청이 있던 일반 잡은 다시 붙은 뒤 첫 2xx 의 had_cancel 로 멈춘다
    w, fake, _, _ = make_worker(tmp_path)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=irreversible))
    closed_hb(w, fake, 7, state, had_cancel)
    time.sleep(0.1)
    assert runner.observer.should_stop() is False  # 자기 멈춤 · 프로세스 정지가 아니다
    assert runner.observer.should_cancel() is stops
    if not stops:
        assert t.is_alive()  # 일반 lost 잡은 죽지 않는다
        runner.release.set()
    body = finish_of(fake, t)
    assert body["outcome"] == (CANCELLED if stops else SUCCEEDED)


@pytest.mark.parametrize("state", [SUCCEEDED, "failed", "timed_out", "retried", "exhausted"])
def test_rl35_a_recorded_finish_drops_the_parked_report_and_is_not_sent_again(
    tmp_path, monkeypatch, state
):
    """이 세션의 finish 가 이미 기록됐고 2xx 만 잃었다 — 큐 항목을 전달됨으로 지우고 다시 보내지
    않는다(W4 표 첫 행). heartbeat 목록에서도 빠진다."""
    w, fake, _, _ = make_worker(tmp_path)
    fake.finish_error = ClientError(503, "busy")
    monkeypatch.setattr(rw, "REPORT_RETRIES", ())
    runner, t = start_job(w, monkeypatch, claimed(irreversible=False))
    runner.release.set()
    t.join(10)
    assert 7 in w._unreported and 7 in w.running  # 못 보내서 미뤄 뒀다
    sent = len(fake.finishes)
    fake.finish_error = None
    closed_hb(w, fake, 7, state, False)
    assert 7 not in w._unreported and 7 not in w.running
    assert len(fake.finishes) == sent  # 다시 보내지 않는다


def test_rl35_a_still_running_job_the_server_recorded_as_finished_stops_without_a_finish(
    tmp_path, monkeypatch
):
    """서버가 이 잡을 `timed_out` 으로 안다면 워커가 그 잡을 더 돌릴 까닭이 없다(RL35 의 「timed_out
    이면 그룹을 멈춘다」). 서버에 기록된 결과를 덮지 않게 finish 는 보내지 않는다(W4 표)."""
    w, fake, _, _ = make_worker(tmp_path)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=False))
    closed_hb(w, fake, 7, "timed_out", False)
    t.join(10)
    assert not t.is_alive() and fake.finishes == []
    assert 7 not in w.running


@pytest.mark.parametrize("state", ["expired", "scheduled", "queued", "uploading"])
def test_rl35_an_impossible_state_only_logs(tmp_path, monkeypatch, state):
    w, fake, _, logs = make_worker(tmp_path)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True))
    closed_hb(w, fake, 7, state, True)
    assert runner.observer.should_cancel() is False
    assert any("#7" in m and state in m for m in logs)
    runner.release.set()
    assert finish_of(fake, t)["outcome"] == SUCCEEDED


@pytest.mark.parametrize("irreversible, stops", [(False, False), (True, True)])
def test_rl5_a_409_job_closed_on_a_log_chunk_kills_only_an_irreversible_job(
    tmp_path, monkeypatch, irreversible, stops
):
    """로그 조각의 409 `job_closed`(state lost)는 W4 표로 간다. `closed_by_server` 는 더는
    `should_cancel` 을 세우지 않는다(F66 · 결정 334)."""
    w, fake, _, _ = make_worker(tmp_path)
    fake.log_error = ClientError(409, "job #7 is lost", {"code": "job_closed", "state": LOST})
    runner, t = start_job(w, monkeypatch, claimed(irreversible=irreversible))
    runner.observer.output(b"x" * (rw.LOG_BATCH_BYTES + 1))
    assert runner.observer.closed_by_server is True
    time.sleep(0.1)
    assert runner.observer.should_cancel() is stops
    if not stops:
        assert t.is_alive()
        runner.release.set()
    finish_of(fake, t)


# ── 자기 멈춤 감시(W3 · RL1 · RL2 · RL26 · RL34) ──────────────────────────────


def test_rl34_the_worker_margin_matches_the_config_margin():
    """config 의 하한 여유와 워커의 m 공식이 따로 움직이지 않는다(§4.3 설정 검증)."""
    from remote_ci_monitor import config

    assert rw.HEARTBEAT_DEADLINE_SECONDS == config.HEARTBEAT_DEADLINE_SECONDS == 15
    assert (
        config.DISCONNECT_GRACE_MARGIN_SECONDS
        == rw.HEARTBEAT_DEADLINE_SECONDS + 10 + rw.SELF_STOP_SLACK_SECONDS
    )
    assert rw.WATCHDOG_TICK_SECONDS == 1 and rw.HEARTBEAT_INFLIGHT_MAX == 4


@pytest.mark.parametrize(
    "timeout, beat, floor",
    [(60, 5, 60), (10, 1, 52), (300, 5, 300)],  # 기본 · e2e(T 10 · H 1) · 큰 T
)
def test_rl34_the_connected_age_floor_is_the_third_edition_formula(tmp_path, timeout, beat, floor):
    w, *_ = make_worker(tmp_path)
    w.timeout_seconds, w.heartbeat_seconds = timeout, beat
    assert w.connected_age_floor() == floor


@pytest.mark.parametrize(
    "grace, conf_grace, want_grace, threshold",
    [
        (600, 10, 10, 565),  # 기본: 600 − (15 + 10 + 10)
        (95, 10, 10, 60),  # 하한 G = L + 35 — 문턱이 L 과 같다
        (95, 60, 10, 60),  # 큰 워커 grace 는 그 잡에서만 줄인다
        (600, 60, 60, 515),
    ],
)
def test_rl34_grace_j_and_the_threshold(tmp_path, grace, conf_grace, want_grace, threshold):
    w, _, _, logs = make_worker(tmp_path, grace_seconds=conf_grace)
    spec, _ = w._spec_from_claim(claimed(irreversible=True, grace=grace), 1)
    assert spec.irreversible is True and spec.grace_seconds == want_grace
    assert w.self_stop_threshold(grace, want_grace) == threshold
    assert threshold >= w.connected_age_floor()  # 문턱은 늘 ≥ L(§4.1.3)
    said = [m for m in logs if "grace_seconds" in m]
    assert bool(said) is (want_grace != conf_grace)
    plain, _ = w._spec_from_claim(claimed(irreversible=False, grace=grace), 1)
    assert plain.irreversible is False and plain.grace_seconds == conf_grace


def _stopped(runner: FakeRunner) -> bool:
    # 관찰자를 러너에서 읽는다 — 멈춘 잡은 finish 뒤 워커의 관찰자 표에서 빠진다
    return runner.observer.self_stop.is_set()


def test_rl34_the_watchdog_stops_an_irreversible_job_exactly_at_g_minus_m(tmp_path, monkeypatch):
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)  # claim 을 보낸 시각
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True))
    clocks.advance(564)
    w.watchdog_tick()
    assert not _stopped(runner)
    clocks.advance(1)
    w.watchdog_tick()
    assert _stopped(runner)
    body = finish_of(fake, t)
    # `cancelled`(2)가 아니라 lost + worker_self_stopped(F66 · 결정 334)
    assert body["outcome"] == LOST and body["summary_code"] == "worker_self_stopped"
    assert body["summary_args"] == {"seconds": 565}


def test_rl26_waking_from_sleep_stops_on_the_first_tick_by_the_wall_clock(tmp_path, monkeypatch):
    """잠든 사이 벽시계만 G 를 넘었다. 단조 시계만 보면 「몇 초 안 지났다」고 읽는다
    (self-stop-monotonic-only)."""
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True))
    clocks.advance(600 + 30, mono=2)
    w.watchdog_tick()
    assert _stopped(runner)
    assert finish_of(fake, t)["summary_args"] == {"seconds": 630}


def test_rl26_a_wall_clock_going_back_does_not_hide_the_gap(tmp_path, monkeypatch):
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True))
    clocks.advance(-3600, mono=566)
    w.watchdog_tick()
    assert _stopped(runner)
    finish_of(fake, t)


def test_rl26_a_plain_job_is_never_self_stopped(tmp_path, monkeypatch):
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=False))
    clocks.advance(5000)
    w.watchdog_tick()
    assert not _stopped(runner) and t.is_alive()
    runner.release.set()
    assert finish_of(fake, t)["outcome"] == SUCCEEDED


def test_rl34_a_2xx_moves_ok_at_and_the_watchdog_waits_again(tmp_path, monkeypatch):
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True))
    clocks.advance(500)
    assert w.heartbeat_once() is True  # 2xx — 보낸 시각이 새 ok_at
    clocks.advance(500)
    w.watchdog_tick()
    assert not _stopped(runner)
    clocks.advance(65)
    w.watchdog_tick()
    assert _stopped(runner)
    finish_of(fake, t)


def test_rl34_the_watchdog_judges_while_heartbeat_hangs(tmp_path, monkeypatch):
    """heartbeat 가 매달려 있어도 감시 스레드가 따로 판정한다(self-stop-in-heartbeat-loop)."""
    monkeypatch.setattr(rw, "WATCHDOG_TICK_SECONDS", 0.01)
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True))
    fake.hb_gate = threading.Event()
    hb = threading.Thread(target=w.heartbeat_once, daemon=True)
    hb.start()
    dog = w.start_watchdog()
    assert dog.name == "rcm-worker-watchdog"
    clocks.advance(566)
    for _ in range(300):
        if _stopped(runner):
            break
        time.sleep(0.01)
    assert _stopped(runner)
    finish_of(fake, t)
    fake.hb_gate.set()
    w.stopping.set()
    dog.join(5)


def test_rl34_a_hanging_heartbeat_is_cut_at_its_deadline_and_a_late_2xx_moves_ok_at(
    tmp_path, monkeypatch
):
    """(e) 조금씩 흘리는 서버: 전체 기한이 회차를 끊는다. 늦은 2xx 는 보낸 시각으로 ok_at 을
    올린다."""
    monkeypatch.setattr(rw, "HEARTBEAT_DEADLINE_SECONDS", 0.2)
    w, fake, clocks, _ = make_worker(tmp_path)
    fake.hb_gate = threading.Event()
    sent_wall = clocks.now().timestamp()
    began = time.monotonic()
    assert w.heartbeat_once() is False
    assert time.monotonic() - began < 2
    assert w.ok_at() is None
    clocks.advance(30)
    fake.hb_gate.set()
    for _ in range(200):
        if w.ok_at() is not None:
            break
        time.sleep(0.01)
    assert w.ok_at() == (sent_wall, 1000.0)


def test_rl34_at_most_four_heartbeats_hang_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "HEARTBEAT_DEADLINE_SECONDS", 0.05)
    w, fake, _, logs = make_worker(tmp_path)
    fake.hb_gate = threading.Event()
    for _ in range(4):
        assert w.heartbeat_once() is False
    assert fake.hb_calls == 4
    assert w.heartbeat_once() is False
    assert fake.hb_calls == 4  # 다섯째는 보내지 않는다
    assert any("in flight" in m for m in logs)
    fake.hb_gate.set()


@pytest.mark.parametrize("where", ["after_run", "requires", "prepare"])
def test_rl41_every_lost_path_reports_worker_self_stopped(tmp_path, monkeypatch, where):
    """실행 뒤 · requires 실패 뒤 · 준비 실패 뒤 세 곳이 모두 자기 멈춤을 `worker_self_stopped` 로
    보고한다(§4.3, `group_gone` 은 프로세스가 없었던 두 곳에서 참)."""
    from remote_ci_monitor.runner import RequiredToolMissing, RunnerError

    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)

    def fake_run(spec, observer, **kw):
        clocks.advance(570)
        w.watchdog_tick()
        assert observer.should_stop()
        if where == "requires":
            raise RequiredToolMissing("xcodebuild")
        if where == "prepare":
            raise RunnerError("log_unavailable", error="OSError")
        at = kw["now_fn"]()
        return RunResult(rc=None, started=at, finished=at, lost=True)

    monkeypatch.setattr(rw, "run_job", fake_run)
    w.run_claimed(1, claimed(irreversible=True))
    (body,) = fake.finishes
    assert body["outcome"] == LOST and body["summary_code"] == "worker_self_stopped"
    assert body["summary_args"] == {"seconds": 570}
    if where != "after_run":
        assert body["group_gone"] is True
