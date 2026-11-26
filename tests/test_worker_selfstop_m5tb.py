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
    parse_preset,
)
from remote_ci_monitor.core.model import CANCELLED, CANCELLING, LOST, RUNNING, SUCCEEDED
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


@pytest.mark.parametrize(
    ("timeout", "beat"),
    [(60, 5), (10, 1), (120, 5), (300, 5)],  # 기본 · e2e · 둘 · 큰 T
)
def test_rl30_the_lowest_accepted_grace_still_lets_the_worker_wait_at_least_l(
    tmp_path, timeout, beat
):
    """config 가 받아들이는 가장 작은 G(하한 L + 여유)에서도 기본 워커 grace 10 의 자기 멈춤
    문턱 `G − m` 이 L 아래로 내려가지 않는다(결정 320 · §4.1.3). 하한 여유와 워커의 m 이 따로
    움직이면 — 예를 들어 한쪽만 바꾸면 — 여기서 문턱이 L 과 달라진다."""
    w, *_ = make_worker(tmp_path)
    w.timeout_seconds, w.heartbeat_seconds = timeout, beat
    floor_l = w.connected_age_floor()
    lowest = floor_l + DISCONNECT_GRACE_MARGIN_SECONDS
    cfg = load(
        tmp_path,
        f"worker_timeout_seconds = {timeout}\nworker_heartbeat_seconds = {beat}\n"
        f"disconnect_grace_seconds = {lowest}",
    )
    assert disconnect_grace(cfg.server) == lowest  # 하한 그 자체는 받는다
    with pytest.raises(ConfigError):
        load(
            tmp_path,
            f"worker_timeout_seconds = {timeout}\nworker_heartbeat_seconds = {beat}\n"
            f"disconnect_grace_seconds = {lowest - 1}",
        )
    assert w.self_stop_threshold(lowest, 10) == floor_l  # 가장 작은 G 에서 문턱은 정확히 L


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


def _reload_with(srv: WorkerServer, monkeypatch, edit: Any) -> Any:
    """`App.reload_config` 를 진짜로 돌린다 — 파일 대신 지금 설정의 사본을 `edit` 로 고쳐 읽힌다."""
    import copy

    from remote_ci_monitor import server as server_mod

    fresh = copy.deepcopy(srv.app.config)
    edit(fresh)
    srv.app.config.path = Path("server.toml")
    fresh.path = srv.app.config.path
    monkeypatch.setattr(server_mod, "load_server_config", lambda path: fresh)
    return srv.app.reload_config()


def test_rl30_a_reload_that_changes_the_grace_is_refused_whole_and_keeps_the_old_values(
    tmp_path, monkeypatch
):
    """`[server]` 를 바꾼 reload(여기서는 `disconnect_grace_seconds`)는 오늘처럼 통째로 거절되고
    옛 값이 남는다 — 같은 파일의 새 프리셋도 들어가지 않고, 다음 claim 도 옛 G 를 얼린다."""
    from remote_ci_monitor.server import ApiError

    srv = WorkerServer(tmp_path, presets=SHIP, lanes=0, disconnect_grace_seconds=300)
    try:

        def edit(fresh: Any) -> None:
            fresh.server.disconnect_grace_seconds = 900
            fresh.presets = (*fresh.presets, parse_preset(sh("extra", "true")))

        with pytest.raises(ApiError) as e:
            _reload_with(srv, monkeypatch, edit)
        assert e.value.status == 409 and "[server]" in str(e.value)
        assert srv.app.config.server.disconnect_grace_seconds == 300
        assert srv.app.config.preset("extra") is None
        srv.registered("build-02")
        a = srv.queued_job(preset="ship")
        status, body = srv.claim("build-02", 1)
        assert status == 200 and body["job"]["id"] == a
        assert body["job"]["disconnect_grace_seconds"] == 300
    finally:
        srv.close()


def test_rl30_irreversible_changed_by_a_reload_while_a_job_runs_reaches_only_the_next_claim(
    tmp_path, monkeypatch
):
    """도는 잡은 claim 때 얼린 `irreversible` 을 쓴다 — reload 로 표식을 뗀 프리셋의 도는 잡은 G 에
    닫힐 때 여전히 그룹을 홀드하고, 다음 claim 부터 새 값(표식 없음)을 얼린다."""
    srv = WorkerServer(tmp_path, presets=SHIP, lanes=0)
    try:
        srv.registered("build-02", lanes=2)
        a = srv.queued_job(preset="ship")
        assert srv.claimed("build-02", 1) == a

        def edit(fresh: Any) -> None:
            fresh.presets = tuple(
                parse_preset(sh("ship", "echo ship")) if p.name == "ship" else p
                for p in fresh.presets
            )

        out = _reload_with(srv, monkeypatch, edit)
        assert out["presets_changed"] == ["ship"]
        assert srv.app.config.preset("ship").irreversible is False
        assert srv.store.get_job(a).irreversible is True  # 얼린 값
        b = srv.queued_job(preset="ship")
        status, body = srv.claim("build-02", 2)
        assert status == 200 and body["job"]["id"] == b  # 홀드 · 표식 그룹 없이 들어간다
        assert body["job"]["irreversible"] is False
        srv.clock.advance(G + 1)
        assert sorted(srv.app.mark_lost_workers(srv.clock.now)) == [a, b]
        assert [(h["job_id"], h["cause"]) for h in holds(srv)] == [(a, "worker_unreachable")]
    finally:
        srv.close()


def test_rl30_the_worker_grace_seconds_keeps_its_meaning_and_shrinks_only_for_an_irreversible_job(
    tmp_path,
):
    """새 키(`disconnect_grace_seconds`)는 워커의 `grace_seconds`(SIGTERM→KILL)와 겹치지 않는다.
    워커 grace 가 커서 `G − m < L` 이 되면 그 표식 잡에서만 grace 를 줄여 쓰고 로그 한 줄을
    남긴다. 일반 잡과 설정 값은 그대로다."""
    w, _fake, _clocks, logs = make_worker(tmp_path, grace_seconds=300)
    floor_l = w.connected_age_floor()
    lowest = floor_l + DISCONNECT_GRACE_MARGIN_SECONDS  # 서버가 받는 가장 작은 G
    assert w._grace_for(7, False, lowest) == 300  # 일반 잡 — 그대로
    assert logs == []
    grace = w._grace_for(8, True, lowest)
    assert grace < 300
    assert w.self_stop_threshold(lowest, grace) >= floor_l  # 줄인 grace 로 문턱이 L 이상
    assert logs == [
        f"#8: grace_seconds 300 lowered to {grace} for this job — fits disconnect_grace_seconds"
    ]
    assert w.config.grace_seconds == 300  # 설정은 그대로
    assert w._grace_for(9, True, 86400) == 300  # G 가 넉넉하면 줄이지 않는다
    assert len(logs) == 1


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
    # A 는 취소를 들었다 — 들리지 않은 세션의 잡은 그 이유만으로도 닫히지 않으니(T4), 펜싱된
    # 세션이라서 닫히지 않는다는 것을 보려면 A 가 들린 뒤여야 한다
    srv.clock.advance(1)
    assert srv.heartbeat("build-02", jobs=[a])[1]["cancel"] == [a]
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
    t0 = time.monotonic()
    status, _ = srv.heartbeat("build-02", jobs=[])
    took = time.monotonic() - t0
    gate.set()
    t.join(10)
    assert status == 200
    # 닫기가 멈춘 동안 다른 세션의 heartbeat 은 기다리지 않는다 — 전역 잠금이면 5초를 기다린다
    assert took < 2.5, took
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

    def log(self, job_id, chunk, offset=None):
        if self.log_error is not None:
            raise self.log_error
        return {}

    def finish(self, job_id, outcome, rc, summary, **extra):
        self.finishes.append({"job": job_id, "outcome": outcome, "summary": summary, **extra})
        if self.finish_error is not None:
            raise self.finish_error
        return {}


class _Unreachable:
    """모든 보고가 연결 실패인 클라이언트(못 한 보고가 쌓인 잡 흉내)."""

    def __getattr__(self, name: str) -> Any:
        def fail(*a: Any, **kw: Any) -> Any:
            raise ClientError(0, "connection refused")

        return fail


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
    않는다(W4 표 첫 행). heartbeat 목록에서도 빠진다. M5t-b PR2: 못 보낸 finish 는 그 잡의 보고 큐에
    남고 레인은 큐가 끝날 때까지 기다린다(§4.5)."""
    w, fake, _, _ = make_worker(tmp_path)
    fake.finish_error = ClientError(503, "busy")
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.01)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.01)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=False))
    runner.release.set()
    deadline = time.monotonic() + 10
    while not fake.finishes and time.monotonic() < deadline:
        time.sleep(0.01)
    assert t.is_alive() and 7 in w._senders and 7 in w.running  # 못 보내 큐에 남았다
    closed_hb(w, fake, 7, state, False)
    t.join(10)
    assert not t.is_alive()
    sent = len(fake.finishes)
    fake.finish_error = None
    time.sleep(0.05)
    assert 7 not in w._senders and 7 not in w.running
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
    # 배치는 따로 한 스레드가 보낸다(RL5 — 펌프를 막지 않는다). 그 전송의 409 를 기다린다
    deadline = time.monotonic() + 5
    while not runner.observer.closed_by_server and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.observer.closed_by_server is True
    time.sleep(0.1)
    assert runner.observer.should_cancel() is stops
    if not stops:
        assert t.is_alive()
        runner.release.set()
    finish_of(fake, t)


# ── 자기 멈춤 감시(W3 · RL1 · RL2 · RL26 · RL34) ──────────────────────────────


def test_rl34_the_worker_margin_matches_the_config_margin():
    """잠금: 명세 §4.3 이 정한 상수 값(heartbeat 전체 기한 15초 · 감시 틱 1초 · 동시에 매달리는
    heartbeat 4개 · m = 15 + 10 + 10)을 그대로 적어 둔다. 행동은 위의
    `test_rl30_the_lowest_accepted_grace_still_lets_the_worker_wait_at_least_l` 과 아래의 감시
    시험들이 본다 — 이 시험은 값이 몰래 바뀌는 것만 막는다."""
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


def test_rl34_f_the_worker_hands_its_monotonic_clock_to_the_runner(tmp_path, monkeypatch):
    """러너의 점검 박자 · SIGTERM→SIGKILL 은 단조 시계다. 워커에 주입한 단조 시계가 러너에 닿지
    않으면 시험과 감시 스레드가 보는 시계와 러너의 시계가 갈린다(P3 1라운드 LOW)."""
    w, fake, clocks, _ = make_worker(tmp_path)
    runner = FakeRunner()
    seen: dict[str, Any] = {}

    def recording(spec, observer, **kw):
        seen.update(kw)
        runner.release.set()
        return runner(spec, observer, **kw)

    monkeypatch.setattr(rw, "run_job", recording)
    w.run_claimed(1, claimed(irreversible=True))
    assert seen.get("mono_fn") == clocks.mono_fn


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


def _tick_until_stopped(w, clocks: Clocks, runner: FakeRunner, limit: int) -> int | None:
    """감시 틱을 1초씩 민다. 멈춘 틱의 경과 초(없으면 None)."""
    for n in range(1, limit + 1):
        clocks.advance(1)
        w.watchdog_tick()
        if _stopped(runner):
            return n
    return None


@pytest.mark.parametrize("grace_g", [95, 600])
@pytest.mark.parametrize("conf_grace", [10, 60])
@pytest.mark.parametrize("backlog", [False, True])
def test_rl34_a_b_the_group_is_gone_before_the_server_may_close(
    tmp_path, monkeypatch, grace_g, conf_grace, backlog
):
    """(a)(b) 마지막 2xx 를 보낸 시각에서 `G − m` 에 감시가 멈추고, 감시 틱 + 러너 멈춤(`grace_J`)
    을 더해도 서버가 닫을 수 있는 가장 이른 시각 `heard + G`(heard ≥ 그 2xx 를 보낸 시각)보다
    앞이다. (b) 돌아온 첫 heartbeat 뒤 못 한 보고가 쌓여 있어도(다시 보내도 실패) 같다. 기본
    grace · 큰 grace, 가장 작은 G · 기본 G 의 모든 조합."""
    w, fake, clocks, _ = make_worker(tmp_path, grace_seconds=conf_grace)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True, grace=grace_g))
    clocks.advance(30)
    if backlog:
        # 못 한 보고가 쌓인 다른 잡 — 그 잡의 보내는 스레드가 연결 실패를 다시 하는 중이다
        stuck = rw._JobSender(w, 99, rw.JobOutbox(None), client=_Unreachable())
        stuck.ob.finish = {"outcome": SUCCEEDED, "exit_code": 0}
        stuck.ob.ended_at = clocks.now().timestamp()
        w._senders[99] = stuck.start()
    sent = clocks.now().timestamp()
    assert w.heartbeat_once() is True  # 돌아온 첫 heartbeat — 마지막 2xx
    assert w.ok_at() == (sent, clocks.mono)
    assert (99 in w._senders) is backlog
    spec, _ = w._spec_from_claim(claimed(irreversible=True, grace=grace_g), 1)
    grace_j = spec.grace_seconds
    stopped = _tick_until_stopped(w, clocks, runner, grace_g + 10)  # 분단
    assert stopped == w.self_stop_threshold(grace_g, grace_j)
    group_gone_by = stopped + rw.WATCHDOG_TICK_SECONDS + grace_j
    assert group_gone_by < grace_g  # `heard + G` 보다 앞(heard ≥ sent)
    body = finish_of(fake, t)
    assert body["outcome"] == LOST and body["summary_code"] == "worker_self_stopped"


@pytest.mark.parametrize(
    ("timeout", "beat"),
    [(60, 5), (10, 1)],  # 기본 설정 · e2e 설정(T 10 · H 1 · G 90)
)
def test_rl34_c_fourteen_second_heartbeats_keep_the_job_even_losing_one_2xx(
    tmp_path, monkeypatch, timeout, beat
):
    """(c) 연결은 되는데 heartbeat 마다 14초 걸려 2xx 가 온다. 그 사이 2xx 하나를 잃어도(`ok_at`
    나이 ≤ 14 + H + 14 + H + 15 < L) 가장 작은 G 에서조차 자기 멈춤이 걸리지 않는다."""
    w, fake, clocks, _ = make_worker(tmp_path)
    w.timeout_seconds, w.heartbeat_seconds = timeout, beat
    lowest = w.connected_age_floor() + DISCONNECT_GRACE_MARGIN_SECONDS
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True, grace=lowest))
    lose = {3}  # 넷째 회차의 2xx 를 잃는다
    rounds = {"n": 0}

    def slow_heartbeat(jobs, host=None, *, seq=None, claims_settled=None):
        n = rounds["n"]
        rounds["n"] += 1
        for _ in range(14):  # 14초 걸린다 — 그동안 감시는 1초마다 본다
            clocks.advance(1)
            w.watchdog_tick()
        if n in lose:
            raise ClientError(0, "connection reset")  # 그 2xx 를 잃었다
        return {}

    fake.heartbeat = slow_heartbeat  # type: ignore[method-assign]
    for _ in range(12):
        w.heartbeat_once()
        for _ in range(beat):
            clocks.advance(1)
            w.watchdog_tick()
        assert not _stopped(runner), rounds
    runner.release.set()
    assert finish_of(fake, t)["outcome"] == SUCCEEDED


def test_rl34_d_heartbeats_that_keep_getting_5xx_stop_the_job_at_g_minus_m(tmp_path, monkeypatch):
    """(d) 연결은 되는데 heartbeat 가 5xx 만 받는다. 서버는 요청을 받으므로(heard 가 오른다) 닫지
    않고, 워커는 마지막 2xx 의 `G − m` 에 표식 잡을 멈춰 `lost`(`worker_self_stopped`)로 보고한다.
    멈춤 + 틱 + `grace_J` 는 그 2xx 의 `+ G` 보다 앞이다 — 두 번 돌지 않는 쪽."""
    w, fake, clocks, _ = make_worker(tmp_path)
    w.mark_ok(clocks.now().timestamp(), clocks.mono)
    runner, t = start_job(w, monkeypatch, claimed(irreversible=True, grace=600))
    # M5t-b PR2: phase 는 그 잡의 보내는 스레드가 보낸다 — 시계를 밀기 전에 보냈는지 본다(그 2xx 도
    # `ok_at` 을 올린다)
    deadline = time.monotonic() + 5
    while w._senders[7].ob.phase is not None and time.monotonic() < deadline:
        time.sleep(0.01)

    def five_xx(jobs, host=None, *, seq=None, claims_settled=None):
        raise ClientError(503, "server busy")

    fake.heartbeat = five_xx  # type: ignore[method-assign]
    stopped = None
    for n in range(1, 700):
        clocks.advance(1)
        if n % 5 == 0:
            assert w.heartbeat_once() is False
        w.watchdog_tick()
        if _stopped(runner):
            stopped = n
            break
    assert stopped == w.self_stop_threshold(600, 10) == 565
    assert stopped + rw.WATCHDOG_TICK_SECONDS + 10 < 600
    body = finish_of(fake, t)
    assert body["outcome"] == LOST and body["summary_code"] == "worker_self_stopped"


class _DribblingServer:
    """응답 머리를 보낸 뒤 몸통을 0.2초마다 한 바이트씩 흘리는 HTTP 서버(소켓 시한 안쪽). 받은
    요청 수를 센다."""

    def __init__(self) -> None:
        import socket

        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.requests = 0
        self.stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: Any) -> None:
        try:
            conn.settimeout(5)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            self.requests += 1
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 4096\r\n\r\n"
            )
            while not self.stop.is_set():
                conn.sendall(b" ")
                time.sleep(0.2)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.stop.set()
        self.sock.close()


def test_rl34_e_a_dribbling_server_is_cut_by_the_deadline_and_the_next_beat_goes(
    tmp_path, monkeypatch
):
    """(e) 서버가 응답 바이트를 조금씩 흘려(소켓 시한 안쪽) heartbeat 하나가 기한을 넘긴다. 진짜
    `WorkerClient` 로 보낸다 — 전체 기한이 그 회차를 끊고 다음 박자가 간다(박자가 막히지 않는다).
    기한만 줄여 시험 시간을 짧게 한다."""
    from remote_ci_monitor.client import WorkerClient

    monkeypatch.setattr(rw, "HEARTBEAT_DEADLINE_SECONDS", 1.0)
    srv = _DribblingServer()
    try:
        client = WorkerClient(f"http://127.0.0.1:{srv.port}", "t")
        client.session_id = "0" * 32
        config = WorkerConfig(
            server=client.server, token="t", data_dir=str(tmp_path / "w"), lanes=1
        )
        w = rw.RemoteWorker(config, client=client, log=lambda m: None)
        for n in (1, 2):
            began = time.monotonic()
            assert w.heartbeat_once() is False
            assert time.monotonic() - began < 1.0 + 1.0  # 기한에 끊겼다
            for _ in range(100):
                if srv.requests >= n:
                    break
                time.sleep(0.02)
            assert srv.requests == n  # 다음 박자가 갔다
        assert w.ok_at() is None
    finally:
        srv.close()


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


def test_rl41_a_self_stop_report_inside_g_closes_lost_without_a_step_or_a_hold(srv):
    """(a)~(c) 의 끝: 자기 멈춤 보고(`lost` · `worker_self_stopped` · `group_gone: true`)가 G 안에
    서버에 닿으면 잡은 `lost`(종료 3)이고 `failed_step` 이 없으며 홀드 0 이다 — 그룹은 다음 잡에
    열린다."""
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    srv.clock.advance(G - 40)  # G 안
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{a}/finish",
        token="build-02",
        json_body={
            "outcome": LOST,
            "exit_code": None,
            "summary_code": "worker_self_stopped",
            "summary_args": {"seconds": G - 35},
            "group_gone": True,
        },
    )
    assert status == 200, body
    j = srv.store.get_job(a)
    assert j.state == LOST and j.summary_code == "worker_self_stopped"
    assert srv.view(a).get("failed_step") is None
    assert holds(srv) == []
    b = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == b  # 그룹이 풀렸다


# ── 얼린 유예로 판정 · 늦은 heartbeat 응답 · claim 번호와 running(RL30 · RL50) ──────────


def test_rl30_the_janitor_closes_by_the_frozen_grace_not_the_current_one(tmp_path):
    srv = WorkerServer(tmp_path, presets=list(WORKER_PRESETS), disconnect_grace_seconds=300)
    try:
        srv.registered("build-02", lanes=2)
        a = srv.queued_job()
        assert srv.claimed("build-02", 1) == a
        srv.cfg.server.disconnect_grace_seconds = 900  # 재시작 · reload 흉내 — 다음 claim 부터다
        b = srv.queued_job()
        assert srv.claimed("build-02", 2) == b
        srv.clock.advance(301)
        srv.app.mark_lost_workers(srv.clock.now)
        assert srv.store.get_job(a).state == LOST  # 얼린 300 초
        assert srv.store.get_job(b).state == RUNNING  # 얼린 900 초
    finally:
        srv.close()


def test_rl50_a_late_heartbeat_answer_does_not_undo_a_newer_cancel(tmp_path):
    w, fake, _clocks, _logs = make_worker(tmp_path)
    gate = threading.Event()
    seqs: list[Any] = []

    def heartbeat(jobs, host=None, *, seq=None, claims_settled=None):
        seqs.append(seq)
        if len(seqs) == 1:
            gate.wait(10)  # A — 늦게 돌아온다
            return {"cancel": []}
        return {"cancel": [7]}  # B — 먼저 돌아온다

    fake.heartbeat = heartbeat
    t = threading.Thread(target=w.heartbeat_once, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while not seqs:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert w.heartbeat_once() is True
    assert 7 in w.cancel_requested
    gate.set()
    t.join(10)
    assert seqs[0] < seqs[1]
    assert 7 in w.cancel_requested  # 작은 seq 의 빈 cancel 이 새 취소를 지우지 않는다


@pytest.mark.parametrize("newer", [True, False])
def test_rl50_a_late_heartbeat_answer_does_not_undo_a_newer_paused(tmp_path, newer):
    """RL50 「`seq` 가 이미 적용한 것보다 작은 응답의 `cancel` · `paused` 를 적용하지 않는다」 —
    `paused` 쪽. 먼저 돌아온 새 응답 B 의 값이 늦게 돌아온 A 의 반대 값에 되돌려지지 않는다."""
    w, fake, _clocks, _logs = make_worker(tmp_path)
    w.paused = not newer
    gate = threading.Event()
    seqs: list[Any] = []

    def heartbeat(jobs, host=None, *, seq=None, claims_settled=None):
        seqs.append(seq)
        if len(seqs) == 1:
            gate.wait(10)  # A — 늦게 돌아온다
            return {"cancel": [], "paused": not newer}
        return {"cancel": [], "paused": newer}  # B — 먼저 돌아온다

    fake.heartbeat = heartbeat
    t = threading.Thread(target=w.heartbeat_once, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while not seqs:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert w.heartbeat_once() is True
    assert w.paused is newer
    gate.set()
    t.join(10)
    assert seqs[0] < seqs[1]
    assert w.paused is newer  # 작은 seq 의 paused 가 새 상태를 되돌리지 않는다


def test_rl50_the_claimed_job_is_running_in_the_lock_section_that_settles_its_claim(tmp_path):
    """R20: heartbeat 이 찍는 「끝난 claim 까지」에 든 claim 의 잡은 그 순간 `running` 에 있다."""
    w, fake, _clocks, _logs = make_worker(tmp_path)
    fake.claim = lambda lane, wait, claim_seq=None: claimed(7, irreversible=False)
    w.run_claimed = lambda lane, c: w.stopping.set()
    inner = w._lock
    seen: list[tuple[int, bool]] = []

    class Watch:
        def __enter__(self):
            inner.__enter__()
            return self

        def __exit__(self, *exc):
            seen.append((w.claims_settled(), 7 in w.running))
            return inner.__exit__(*exc)

    w._lock = Watch()
    w._lane_loop(1)
    assert 7 in w.running
    assert [s for s in seen if s[0] >= 1 and not s[1]] == []  # 정리됐는데 running 에 없던 순간 0


# ══ P2-5: 계획↔시험 대조가 약하다고 한 행(서버 쪽 RL3 · RL4 · RL8 · RL56) ═══════════════

SUP = [
    *SHIP,
    sh("shipsup", "echo s", irreversible=True, supersede="all"),
    sh("oksup", "echo o", supersede="all", concurrency_group="sup"),
    sh("ship2", "echo s", irreversible=True),  # 그룹 irreversible:ship2
    sh("ship3", "echo s", irreversible=True),
]
QUIET = 61  # 서버의 worker_timeout_seconds(60)를 넘긴 조용함 — `kill_at` 규칙이 멈춘다(T4)


@pytest.fixture
def sup(tmp_path):
    s = WorkerServer(tmp_path, presets=SUP, lanes=0)
    yield s
    s.close()


def _submitted(srv: WorkerServer, preset: str, branch: str = "feat") -> int:
    from shelfkit import fixtures, submit, upload

    status, body = submit(srv, preset, branch=branch)
    assert status == 201, (status, body)
    jid = int(body["job_id"])
    upload(srv, jid, fixtures())
    return jid


def test_rl3_inside_and_past_g_a_same_group_submit_waits_and_never_runs_beside_the_first(sup):
    """분단 중(G 안 · 밖) 같은 그룹 제출은 `queued` 로 남는다. G 안에서는 `blocked_by_group`,
    첫 잡이 `lost`(표식 → 홀드)가 된 뒤에는 홀드가 막는다. 어느 쪽에서도 둘째가 claim 되지
    않는다."""
    sup.registered("build-02")
    sup.registered("build-03")
    a = _submitted(sup, "ship")
    assert sup.claimed("build-02", 1) == a
    sup.clock.advance(G // 2)  # 분단 — build-02 는 조용하다
    b = _submitted(sup, "ship", branch="other")
    assert sup.claim("build-03", 1)[0] == 204
    assert sup.store.get_job(b).state == "queued" and sup.row(b)["reason"] == "blocked_by_group"
    assert sup.heartbeat("build-03", jobs=[])[0] == 200
    sup.clock.advance(G // 2 + 1)
    assert sup.app.mark_lost_workers(sup.clock.now) == [a]
    assert sup.claim("build-03", 1)[0] == 204  # 홀드가 그룹을 쥔다
    assert sup.store.get_job(b).state == "queued" and sup.row(b)["reason"] == "needs_action"


@pytest.mark.parametrize("preset", ["shipsup", "oksup"])
def test_rl3_rl8_a_supersede_inside_g_leaves_the_first_cancelling_and_holding_the_group(
    sup, preset
):
    """supersede 가 켜진 프리셋: G 안의 새 제출이 첫 잡을 `cancelling` 으로 둔다. 첫 잡은 그룹을
    계속 쥐어 새 잡이 claim 되지 않는다. 복귀 없이 G 가 지나면 `lost`(`worker_unreachable`)이고
    `cancelled` 나 `cancel_unconfirmed` 로 위장하지 않는다(RL8 b). 홀드는 표식 프리셋에만."""
    sup.registered("build-02")
    sup.registered("build-03")
    a = _submitted(sup, preset)
    assert sup.claimed("build-02", 1) == a
    sup.clock.advance(QUIET)  # 「조용함」(gap > T)이 된 뒤의 supersede
    b = _submitted(sup, preset)
    assert sup.store.get_job(a).state == CANCELLING
    for _ in range(6):  # kill_at + 2 × heartbeat 가 지나도 조용한 세션에는 걸리지 않는다
        sup.clock.advance(20)
        assert sup.heartbeat("build-03", jobs=[])[0] == 200
        assert sup.app.mark_lost_workers(sup.clock.now) == []
        assert sup.claim("build-03", 1)[0] == 204
    assert sup.store.get_job(a).state == CANCELLING
    sup.clock.advance(G)
    assert sup.app.mark_lost_workers(sup.clock.now) == [a]
    j = sup.store.get_job(a)
    assert j.state == LOST and j.summary_code == "worker_unreachable"
    assert [h["cause"] for h in holds(sup)] == (
        ["worker_unreachable"] if preset == "shipsup" else []
    )
    if preset == "oksup":
        assert sup.claimed("build-03", 1) == b


def test_rl8_a_cancel_during_the_partition_reaches_the_worker_inside_g_and_ends_cancelled(srv):
    """(a) 일반 프리셋: 분단 중 취소는 `cancelling` 으로 남고(조용한 세션에는 `kill_at` 규칙이
    걸리지 않는다), G 안에 돌아온 첫 heartbeat 응답의 `cancel` 이 그 잡을 싣는다. 끝 상태는
    `cancelled`(종료 2)이고 `lost` 가 아니다. 홀드 0.

    표식 프리셋의 (a) 는 아래 `test_rl8_a_irreversible_…` 둘이다(조용했던 시간은 세지 않는다)."""
    srv.registered("build-02")
    a = srv.queued_job()
    assert srv.claimed("build-02", 1) == a
    srv.clock.advance(QUIET)  # 분단 — 조용함
    assert srv.cancel(a)[1]["state"] == "cancelling"
    srv.clock.advance(60)
    assert srv.app.mark_lost_workers(srv.clock.now) == []  # 조용한 세션 — 미확인 닫기 없음
    assert srv.store.get_job(a).state == CANCELLING
    status, body = srv.heartbeat("build-02", jobs=[a])  # G 안에 복귀
    assert status == 200 and body["cancel"] == [a]
    srv.finish("build-02", a, "cancelled", exit_code=-15)
    j = srv.store.get_job(a)
    assert j.state == CANCELLED
    assert holds(srv) == []


def test_rl8_a_superseded_job_confirmed_inside_g_keeps_superseded_by_n(sup):
    sup.registered("build-02")
    a = _submitted(sup, "oksup")
    assert sup.claimed("build-02", 1) == a
    sup.clock.advance(30)
    b = _submitted(sup, "oksup")
    status, body = sup.heartbeat("build-02", jobs=[a])
    assert status == 200 and body["cancel"] == [a]
    assert sup.finish("build-02", a, "cancelled", exit_code=-15)[0] == 200  # 확인
    j = sup.store.get_job(a)
    assert j.state == CANCELLED and j.summary_code == "superseded"
    assert j.summary_args == {"by": b}


def test_rl8_a_live_session_that_never_confirms_an_irreversible_cancel_holds(srv):
    """들리는 세션이 취소를 확인하지 않으면 `kill_at + 2 × heartbeat` 에 `cancel_unconfirmed` 로
    닫히고 홀드가 걸린다. 그 뒤 워커의 finish 는 상태를 바꾸지 않는다."""
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    assert srv.cancel(a)[1]["state"] == "cancelling"
    for _ in range(12):
        srv.clock.advance(5)
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200
        if srv.store.get_job(a).state != CANCELLING:
            break
    j = srv.store.get_job(a)
    assert j.state == CANCELLED and j.summary_code == "cancel_unconfirmed"
    assert [(h["job_id"], h["cause"]) for h in holds(srv)] == [(a, "cancel_unconfirmed")]
    status, body = srv.finish("build-02", a, "succeeded", exit_code=0)
    assert status == 409 and body["code"] == "job_closed"
    assert srv.store.get_job(a).state == CANCELLED


def _quiet_cancel(srv: WorkerServer, preset: str) -> int:
    """분단 중(조용한 세션) 취소를 받고 `kill_at + 2H` 보다 오래 조용히 둔다."""
    srv.registered("build-02")
    a = srv.queued_job(preset=preset)
    assert srv.claimed("build-02", 1) == a
    srv.clock.advance(QUIET)  # 분단 — 조용함
    assert srv.cancel(a)[1]["state"] == "cancelling"
    srv.clock.advance(60)  # 옛 `kill_at + 2H` 가 조용한 동안 지났다
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    return a


def _confirm_cancel(srv: WorkerServer, jid: int) -> tuple[int, dict[str, Any]]:
    return srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "cancelled", "exit_code": -15, "group_gone": True},
    )


def test_rl8_a_irreversible_cancel_during_the_partition_is_confirmed_inside_the_new_window(srv):
    """(a) 표식 프리셋 — T4 의 「조용해지면 `kill_at` 규칙 멈춤」은 조용했던 시간을 세지 않는다는
    뜻이다(P2-6 메인 세션 답). 돌아온 첫 heartbeat 는 그 잡을 닫지 않고 `cancel` 로 전한다. 워커가
    다시 들린 때부터 잰 `kill_at + 2H` 창 안에 멈춤을 확인하면 `cancelled`(종료 2) · 홀드 0."""
    a = _quiet_cancel(srv, "ship")
    status, body = srv.heartbeat("build-02", jobs=[a])  # G 안에 복귀
    assert status == 200 and body["cancel"] == [a]
    assert srv.store.get_job(a).state == CANCELLING  # 조용했던 시간으로 바로 닫지 않는다
    srv.clock.advance(5)
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert srv.store.get_job(a).state == CANCELLING
    status, body = _confirm_cancel(srv, a)
    assert status == 200, body
    j = srv.store.get_job(a)
    assert j.state == CANCELLED and j.summary_code != "cancel_unconfirmed"
    assert holds(srv) == []


@pytest.mark.parametrize("by", ["heartbeat", "janitor"])
def test_rl8_a_irreversible_cancel_unconfirmed_in_the_new_window_closes_and_holds(srv, by):
    """돌아온 세션이 새 창(다시 들린 때 + (`kill_at` − 요청) + 2H) 안에 확인하지 않으면 그때
    `cancel_unconfirmed` + 홀드 — 창 전에는 닫지 않는다."""
    a = _quiet_cancel(srv, "ship")
    j = srv.store.get_job(a)
    window = (j.cancel.kill_at - j.cancel.requested_at).total_seconds() + 2 * 5  # H = 5
    steps = int(window // 5)
    assert steps * 5 == window and steps >= 2
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200  # 다시 들림 — 창이 여기서 시작
    for _ in range(steps - 1):
        srv.clock.advance(5)
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200
        assert srv.app.mark_lost_workers(srv.clock.now) == []
        assert srv.store.get_job(a).state == CANCELLING
    srv.clock.advance(5)
    if by == "heartbeat":
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    else:
        srv.app.mark_lost_workers(srv.clock.now)
    j = srv.store.get_job(a)
    assert j.state == CANCELLED and j.summary_code == "cancel_unconfirmed"
    assert [(h["job_id"], h["cause"]) for h in holds(srv)] == [(a, "cancel_unconfirmed")]


@pytest.mark.parametrize("worker", ["confirms", "stays_silent"])
def test_rl8_a_a_partition_shorter_than_worker_timeout_starts_the_cancel_window_again(srv, worker):
    """분단이 `kill 창 + 2H` 보다 길고 `worker_timeout` 보다 짧다(P3 2라운드 LOW). 워커는 분단 중에
    취소를 들을 수 없었다. 돌아온 첫 heartbeat 가 그 잡을 `cancel_unconfirmed` 로 닫으면 안 되고
    `cancel` 로 전한다 — 마지막으로 들은 뒤 kill 창보다 오래 조용했으면 창을 다시 들린 때부터
    새로 잰다. 새 창 안에 확인하면 `cancelled` · 홀드 0, 확인이 없으면 그때 닫고 홀드한다."""
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    srv.clock.advance(1)
    assert srv.cancel(a)[1]["state"] == "cancelling"
    j = srv.store.get_job(a)
    window = (j.cancel.kill_at - j.cancel.requested_at).total_seconds() + 2 * 5  # H = 5
    srv.clock.advance(window + 10)  # 분단 — 옛 창은 조용한 동안 지났다
    assert window + 11 < 60  # worker_timeout 안이다
    srv.app.mark_lost_workers(srv.clock.now)  # 분단 중의 janitor — 워커는 취소를 모른다
    assert srv.store.get_job(a).state == CANCELLING and holds(srv) == []
    status, body = srv.heartbeat("build-02", jobs=[a])
    assert status == 200 and body["cancel"] == [a]
    assert srv.store.get_job(a).state == CANCELLING
    if worker == "confirms":
        srv.clock.advance(5)
        assert _confirm_cancel(srv, a)[0] == 200
        j = srv.store.get_job(a)
        assert j.state == CANCELLED and j.summary_code != "cancel_unconfirmed"
        assert holds(srv) == []
        return
    steps = int(window // 5)
    for _ in range(steps):
        srv.clock.advance(5)
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    j = srv.store.get_job(a)
    assert j.state == CANCELLED and j.summary_code == "cancel_unconfirmed"
    assert [(h["job_id"], h["cause"]) for h in holds(srv)] == [(a, "cancel_unconfirmed")]


def test_rl8_a_many_short_silences_after_the_cancel_do_not_drop_where_its_window_began(srv):
    """P3 3라운드 LOW(A4): 세션마다 「다시 들림」 기록을 최근 8개만 두면, 요청을 걸친 침묵을 끝낸
    기록(창의 시작)이 그 뒤의 짧은 침묵 기록들에 밀려 지워진다. 그러면 창이 요청 때부터로 돌아가
    워커가 취소를 들은 지 창보다 짧은데 `cancel_unconfirmed` 로 닫힌다. 기록은 열린 취소의 가장
    이른 요청보다 오래된 것만 지운다."""
    srv.cfg.server.grace_seconds = 120  # kill 창 120초 — 짧은 침묵 여러 번이 창 안에 든다
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    srv.clock.advance(1)
    assert srv.cancel(a)[1]["state"] == "cancelling"
    srv.clock.advance(50)  # 요청을 걸친 침묵(2H 넘고 worker_timeout 안)
    status, body = srv.heartbeat("build-02", jobs=[a])
    assert status == 200 and body["cancel"] == [a]  # 창이 여기서 시작한다
    heard = srv.clock()
    for _ in range(9):  # 2H(10초)를 넘는 짧은 침묵 아홉 번 — 기록 아홉 개가 더 쌓인다
        srv.clock.advance(11)
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200
        assert srv.store.get_job(a).state == CANCELLING  # 들은 지 창(120 + 2H)이 안 지났다
    while (srv.clock() - heard).total_seconds() < 120 + 2 * 5:
        srv.clock.advance(5)
        srv.heartbeat("build-02", jobs=[a])
        if (srv.clock() - heard).total_seconds() < 120 + 2 * 5:
            assert srv.store.get_job(a).state == CANCELLING
    j = srv.store.get_job(a)
    assert j.state == CANCELLED and j.summary_code == "cancel_unconfirmed"


def test_rl8_a_resumed_records_older_than_every_open_cancel_are_dropped(srv):
    """A4 의 반대쪽: 열린 취소가 없는 세션의 「다시 들림」 기록은 쌓이지 않는다 — 다음 취소의 요청은
    지금 뒤라 지금까지의 기록을 보지 않는다."""
    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    for _ in range(12):
        srv.clock.advance(11)
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    session = srv.store.get_job(a).worker_session_id
    assert srv.app._resumed.get(session, []) == []


def test_rl7_a_cancel_window_that_ran_out_while_the_server_was_down_starts_again(srv):
    """서버가 꺼진 동안 취소 중 잡의 `kill_at + 2H` 가 지났다. 다시 뜬 서버의 첫 틱과, 워커가
    `worker_timeout` 안에 돌아와 보낸 첫 heartbeat 가 그 잡을 `cancel_unconfirmed` 로 닫으면 안
    된다 — 꺼져 있던 시간은 세션이 조용했던 시간이다(P3 1라운드 LOW). 창은 다시 들린 때부터
    새로 재고, 그 창 안에 확인이 없으면 그때 닫는다(fail-open 아님)."""
    from remote_ci_monitor.server import App

    srv.registered("build-02")
    a = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == a
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    assert srv.cancel(a)[1]["state"] == "cancelling"
    j = srv.store.get_job(a)
    window = (j.cancel.kill_at - j.cancel.requested_at).total_seconds() + 2 * 5
    srv.clock.advance(window + 5)  # 서버가 꺼져 있던 동안 옛 창이 지났다(worker_timeout 60 안)
    app2 = App(srv.cfg, srv.store, now_fn=srv.clock, mono_fn=srv.clock.mono)  # 다시 뜬 서버
    app2.start()
    try:
        token = srv.store.verify_token(srv.tokens["build-02"])
        seq = iter(range(100, 200))

        def beat() -> dict[str, Any]:
            n = next(seq)
            body = {
                "worker_session_id": srv.session("build-02"),
                "seq": n,
                "claims_settled": 1,
                "jobs": [a],
            }
            return app2.worker_heartbeat(token, body)

        app2.mark_lost_workers(srv.clock.now)  # 기동 직후 첫 틱 — 아직 안 들린 세션
        assert srv.store.get_job(a).state == CANCELLING
        assert beat()["cancel"] == [a]  # 돌아온 첫 heartbeat 는 취소를 전한다
        assert srv.store.get_job(a).state == CANCELLING
        app2.mark_lost_workers(srv.clock.now)
        assert srv.store.get_job(a).state == CANCELLING
        srv.clock.advance(window + 1)
        beat()
        j = srv.store.get_job(a)
        assert j.state == CANCELLED and j.summary_code == "cancel_unconfirmed"
    finally:
        app2.shutdown()


def _race_at_the_boundary(s: WorkerServer, jid: int) -> dict[str, Any]:
    """janitor 의 틱과 돌아온 heartbeat + finish 를 한 장벽에서 함께 출발시킨다."""
    go = threading.Barrier(2)
    out: dict[str, Any] = {}

    def tick() -> None:
        go.wait()
        out["closed"] = s.app.mark_lost_workers(s.clock.now)

    def back() -> None:
        go.wait()
        out["hb"] = s.heartbeat("build-02", jobs=[jid])[0]
        out["fin"] = s.finish("build-02", jid, "succeeded", exit_code=0)

    threads = [threading.Thread(target=tick), threading.Thread(target=back)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return out


@pytest.mark.parametrize("preset", ["ship", "ok"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_rl4_racing_the_janitor_and_the_returning_heartbeat_leaves_one_final_state(
    tmp_path, preset, offset
):
    """G 만료 경계(±1 틱)에서 janitor 와 돌아온 heartbeat · finish 가 겹친다. 여러 번 되풀이해도
    최종 상태는 하나이고, `lost` 가 된 잡은 늦은 finish 로 뒤집히지 않는다(409 `job_closed`)."""
    for rep in range(5):
        s = WorkerServer(tmp_path / f"{preset}{offset}{rep}", presets=SHIP, lanes=0)
        try:
            s.registered("build-02")
            a = s.queued_job(preset=preset)
            assert s.claimed("build-02", 1) == a
            s.clock.advance(G + offset)
            out = _race_at_the_boundary(s, a)
            final = s.store.get_job(a)
            assert final.state in (LOST, SUCCEEDED), final.state
            if final.state == LOST:
                assert out["closed"] == [a]
                assert out["fin"][0] == 409 and out["fin"][1]["code"] == "job_closed"
            else:
                assert out["closed"] == [] and out["fin"][0] == 200
            if offset <= 0:
                assert final.state == SUCCEEDED  # gap ≤ G — 닫지 않는다
            # 다음 틱 · 다음 finish 도 뒤집지 않는다
            assert s.app.mark_lost_workers(s.clock.now) == []
            assert s.finish("build-02", a, "failed", exit_code=1)[0] == 409
            assert s.store.get_job(a).state == final.state
        finally:
            s.close()


def test_rl56_thirty_mixed_jobs_close_once_each_and_only_irreversible_ones_hold(sup):
    srv = sup
    srv.registered("build-02", lanes=30)
    jobs = {}
    marked = {10: "ship", 20: "ship2", 30: "ship3"}  # 그룹이 서로 달라 함께 돈다
    for lane in range(1, 31):
        preset = marked.get(lane, "ok")
        jid = srv.queued_job(preset=preset)
        assert srv.claimed("build-02", lane) == jid
        jobs[jid] = preset
    srv.clock.advance(G + 1)
    first = srv.app.mark_lost_workers(srv.clock.now)
    second = srv.app.mark_lost_workers(srv.clock.now)
    assert len(first) == 20 and len(second) == 10 and not set(first) & set(second)
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert all(srv.store.get_job(j).state == LOST for j in jobs)
    assert sorted(h["job_id"] for h in holds(srv)) == sorted(
        j for j, p in jobs.items() if p != "ok"
    )
    states = srv.store._conn().execute(
        "SELECT COUNT(*) FROM events WHERE kind='hold' AND json_extract(payload, '$.action')"
        " = 'created'"
    )
    assert states.fetchone()[0] == 3  # 홀드 사건도 표식 잡마다 하나


def test_rl56_the_same_sessions_heartbeat_waits_for_the_close_and_sees_it(srv, monkeypatch):
    """닫기 트랜잭션이 멈춘 동안 같은 세션의 heartbeat 는 그 닫힘이 끝난 뒤 처리되고, 응답의
    `closed_jobs` 가 그 잡을 싣는다."""
    srv.registered("build-02")
    a = srv.queued_job()
    assert srv.claimed("build-02", 1) == a
    srv.clock.advance(G + 1)
    gate, entered = threading.Event(), threading.Event()
    real = srv.store.finish

    def slow_finish(*args, **kw):
        entered.set()
        gate.wait(5)
        return real(*args, **kw)

    monkeypatch.setattr(srv.store, "finish", slow_finish)
    tick = threading.Thread(target=lambda: srv.app.mark_lost_workers(srv.clock.now))
    tick.start()
    assert entered.wait(5)
    out: dict[str, Any] = {}
    hb = threading.Thread(target=lambda: out.setdefault("hb", srv.heartbeat("build-02", jobs=[a])))
    hb.start()
    hb.join(0.5)
    assert hb.is_alive()  # 같은 세션 — 닫기가 끝나기를 기다린다
    gate.set()
    tick.join(10)
    hb.join(10)
    status, body = out["hb"]
    assert status == 200
    assert [c["id"] for c in body["closed_jobs"]] == [a]
    assert srv.store.get_job(a).state == LOST


def test_rl56_a_heard_that_rises_between_the_judgement_and_the_lock_keeps_the_job_open(
    srv, monkeypatch
):
    srv.registered("build-02")
    a = srv.queued_job()
    assert srv.claimed("build-02", 1) == a
    srv.clock.advance(G + 1)
    real_lock = srv.app._session_lock
    session = srv.session("build-02")
    fired: list[bool] = []

    def lock_after_a_heartbeat(key):
        if key == session and not fired:
            fired.append(True)
            # 판정(잠금 밖) 뒤 · 잠금 전에 heartbeat 가 들었다 — `_hear` 가 하는 일을 그대로
            with srv.app._heard_lock:
                srv.app._heard[session] = srv.app.mono_fn()
        return real_lock(key)

    monkeypatch.setattr(srv.app, "_session_lock", lock_after_a_heartbeat)
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert fired and srv.store.get_job(a).state == RUNNING


# ── RL5: 분단 중에도 펌프가 돈다 — 로그 전송이 러너를 막지 않는다 ─────────────────────


def test_rl5_a_hanging_log_post_does_not_stall_the_runner_pump(tmp_path, monkeypatch):
    """분단 중 로그 전송 한 번은 요청 시한(60초 이상)까지 매달린다. 그 전송이 러너의 펌프 스레드에서
    돌면 펌프가 멈춰 워커 로컬 `log.txt` 가 자라지 않고 취소 · 시간초과 · 자기 멈춤 점검도 멈춘다
    (P2-5 에서 e2e RL5 가 찾았다). `output()` 은 전송을 기다리지 않고 돌아와야 하고, 끝의
    `final_flush` 는 모든 바이트를 순서대로 보낸다."""
    monkeypatch.setattr(rw, "LOG_FLUSH_SECONDS", 0)
    w, fake, _clocks, _logs = make_worker(tmp_path)
    gate = threading.Event()
    sent: list[bytes] = []

    def hanging_log(job_id, chunk, offset=None):
        sent.append(chunk)
        gate.wait(10)  # 분단 — 시한까지 매달린다
        return {}

    fake.log = hanging_log
    obs = rw._RemoteObserver(w, 7)
    began = time.monotonic()
    for line in (b"a\n", b"b\n", b"c\n"):
        obs.output(line)
    took = time.monotonic() - began
    gate.set()
    obs.final_flush(wait=10)  # M5t-b PR2: 보내는 스레드가 순서대로 보낸다 — 다 보낼 때까지 기다린다
    # 펌프에서 보냈다면 한 줄마다 10초를 기다린다(기준선 30초)
    assert took < 3, took
    assert b"".join(sent) == b"a\nb\nc\n"  # 빠짐 · 겹침 · 뒤바뀜 없이
