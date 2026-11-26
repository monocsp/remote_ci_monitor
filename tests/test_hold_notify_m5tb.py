"""그룹 홀드의 알림(M5t-b 결정 339-e 6차 · RL54).

명세는 docs/m5tb-workplan.md §4.12 「알림」. 모든 알림의 env 와 URL 본문은 홀드를 말하는 네 키와
`event` · `hold` 를 늘 싣는다. 홀드를 만든 잡의 잡 끝 알림이 그 홀드를 싣고, `on` 에
`irreversible_hold` 를 적은 규칙만 홀드마다 한 번 따로 불린다. 중복 막기 행은
`(job_id, "hold:" + 규칙 이름)` 이고 잡 끝 알림의 기동 스캔을 막지 않는다.

훅 실행은 `test_notify` 의 가짜 `run` · `opener` 로 기록만 한다. 시각은 고정이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from remote_ci_monitor.config import ConfigError, ServerConfig, parse_notify
from remote_ci_monitor.core.model import LOST, NOTIFY_DEFAULT_STATES, QUEUED, Requester, Source
from remote_ci_monitor.core.notify import NotifyRule
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.events import KIND_IRREVERSIBLE_HOLD, EventBus
from remote_ci_monitor.notify import Notifier
from remote_ci_monitor.store import Store
from test_notify import FakeOpener, FakeRun
from test_notify_rules import ENV_KEYS

NOW = datetime(2026, 11, 26, 9, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
HOLD_ENV_KEYS = ENV_KEYS | {"RCM_HOLD_CAUSE", "RCM_HOLD_WORKER"}
NOTE = "group held — run rcm release-hold {}"


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def rule(name: str, on: tuple[str, ...] | None = None, **kw: Any) -> NotifyRule:
    raw: dict[str, Any] = {"name": name, "argv": ["/bin/true"], **kw}
    if "url" in kw:
        raw.pop("argv")
    if on is not None:
        raw["on"] = list(on)
    return parse_notify(raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.db")
    s.irreversible = {"ship": ("irreversible:ship", "global")}
    s.known_presets = frozenset({"ship", "ok"})
    yield s
    s.close()


def notifier(store: Store, rules: list[NotifyRule], **kw: Any) -> Notifier:
    cfg = ServerConfig()
    cfg.notify = tuple(rules)
    return Notifier(
        store, cfg, EventBus(), now_fn=lambda: at(60), base_url="http://build:8787", **kw
    )


def running(store: Store, preset: str, worker: str | None = "build-02", tree: str = "t1") -> int:
    src = Source(mode="tree", repo="org/app", base_sha="abc", dirty=True, tree_hash=tree)
    job = store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key(preset, {}, tree),
        now=NOW,
        state=QUEUED,
    )
    if worker is not None:
        store.register_worker(worker, pool="default", lanes=1, host_name=None, version="x", now=NOW)
    got = store.claim(1, at(1), worker_name=worker)
    assert got is not None and got.id == job.id
    return job.id


def held(store: Store, preset: str = "ship", tree: str = "t1") -> int:
    jid = running(store, preset, tree=tree)
    store.finish(jid, LOST, now=at(30), summary="x", hold_cause="worker_unreachable")
    return jid


def envs(run: FakeRun) -> list[dict[str, str]]:
    return run.envs()


# ── 모든 알림이 홀드를 말한다(RL54 · ENV_KEYS) ──────────────────────────────────


def test_rl54_the_job_end_notification_of_a_held_job_carries_the_hold(store):
    jid = held(store)
    run = FakeRun()
    n = notifier(store, [rule("all"), rule("lost-only", on=("lost",))], run=run)
    assert n.deliver(store.get_job(jid)) == 2
    assert len(run.calls) == 2  # 규칙마다 한 번 — 홀드 알림이 따로 오지 않는다
    for env in envs(run):
        assert set(env) - {"PATH", "HOME", "LANG"} == ENV_KEYS
        assert env["RCM_EVENT"] == "job_finished"
        assert env["RCM_HOLD"] == "1"
        assert env["RCM_HOLD_GROUP"] == "irreversible:ship"
        assert env["RCM_HOLD_NOTE"] == NOTE.format(jid)


def test_rl54_a_job_without_a_hold_has_the_same_keys_with_empty_values(store):
    jid = running(store, "ok")
    store.finish(jid, LOST, now=at(30), summary="x", hold_cause="worker_unreachable")
    assert store.open_holds() == []
    run = FakeRun()
    notifier(store, [rule("all")], run=run).deliver(store.get_job(jid))
    (env,) = envs(run)
    assert set(env) - {"PATH", "HOME", "LANG"} == ENV_KEYS
    assert (env["RCM_EVENT"], env["RCM_HOLD"], env["RCM_HOLD_GROUP"], env["RCM_HOLD_NOTE"]) == (
        "job_finished",
        "0",
        "",
        "",
    )


def test_rl54_the_url_body_always_has_event_and_hold(store):
    plain = running(store, "ok", tree="t2")
    store.finish(plain, LOST, now=at(30), summary="x")
    jid = held(store)
    opener = FakeOpener(200)
    n = notifier(store, [rule("hook", url="https://hooks.example/x")], opener=opener)
    n.deliver(store.get_job(plain))
    n.deliver(store.get_job(jid))
    bodies = [json.loads(r.data) for r in opener.requests]
    assert [b["event"] for b in bodies] == ["job_finished", "job_finished"]
    assert bodies[0]["hold"] == {"held": False, "group": "", "note": ""}
    assert bodies[1]["hold"] == {
        "held": True,
        "group": "irreversible:ship",
        "note": NOTE.format(jid),
    }


# ── opt-in `irreversible_hold` 규칙 ──────────────────────────────────────────


def test_rl54_only_a_rule_that_names_irreversible_hold_gets_one_hold_notification(store):
    jid = held(store)
    run = FakeRun()
    rules = [rule("all"), rule("lost-only", on=("lost",)), rule("holds", on=("irreversible_hold",))]
    n = notifier(store, rules, run=run)
    assert n.deliver_hold(jid) == 1
    assert n.deliver_hold(jid) == 0  # 같은 홀드는 한 번만
    (env,) = envs(run)
    assert env["RCM_NOTIFY"] == "holds"
    assert set(env) - {"PATH", "HOME", "LANG"} == HOLD_ENV_KEYS
    assert env["RCM_EVENT"] == "irreversible_hold"
    assert env["RCM_HOLD"] == "1" and env["RCM_HOLD_GROUP"] == "irreversible:ship"
    assert env["RCM_HOLD_CAUSE"] == "worker_unreachable"
    assert env["RCM_HOLD_WORKER"] == "build-02"
    assert env["RCM_SUMMARY"] == (
        f"group irreversible:ship is held: job #{jid} (ship) on build-02 closed without a stop "
        f"confirmation — check build-02, then rcm release-hold {jid}"
    )
    # 잡 끝 행은 아직 없다 — `hold:` 행이 잡 끝 알림을 막지 않는다
    assert n.deliver(store.get_job(jid)) == 2  # 잡 끝 규칙 둘. `holds` 규칙은 잡 끝에 안 울린다
    names = sorted(r[0] for r in store._conn().execute("SELECT notify_name FROM notifications"))
    assert names == ["all", "hold:holds", "lost-only"]


def test_rl54_the_hold_url_body_has_the_eight_hold_fields(store):
    jid = held(store)
    opener = FakeOpener(200)
    n = notifier(
        store,
        [rule("hook", on=("irreversible_hold",), url="https://hooks.example/h")],
        opener=opener,
    )
    assert n.deliver_hold(jid) == 1
    (req,) = opener.requests
    body = json.loads(req.data)
    assert body["notify"] == "hook" and body["event"] == "irreversible_hold"
    assert body["job"]["id"] == jid
    hold = body["hold"]
    assert set(hold) == {
        "held",
        "group",
        "note",
        "scope",
        "cause",
        "worker",
        "since",
        "release_command",
    }
    assert hold["held"] is True and hold["group"] == "irreversible:ship"
    assert hold["note"] == NOTE.format(jid) and hold["scope"] == "global"
    assert hold["cause"] == "worker_unreachable" and hold["worker"] == "build-02"
    assert hold["since"].endswith("Z") and hold["release_command"] == f"rcm release-hold {jid}"


def test_rl54_presets_filter_the_hold_rule_and_a_released_hold_is_not_announced(store):
    jid = held(store)
    run = FakeRun()
    rules = [
        rule("other", on=("irreversible_hold",), presets=["ok"]),
        rule("ship-holds", on=("irreversible_hold",), presets=["ship"]),
    ]
    n = notifier(store, rules, run=run)
    store.release_hold(jid, kind="admin", by="admin", reason=None, now=at(40))
    assert n.deliver_hold(jid) == 0  # 이미 풀린 홀드는 알리지 않는다
    jid2 = held(store, tree="t3")
    assert n.deliver_hold(jid2) == 1
    assert [e["RCM_NOTIFY"] for e in envs(run)] == ["ship-holds"]


# ── 기동 스캔 ──────────────────────────────────────────────────────────────────


def test_rl54_the_start_scan_sends_what_was_missed_and_never_twice(tmp_path, store):
    jid = held(store)
    run = FakeRun()
    rules = [rule("all"), rule("holds", on=("irreversible_hold",))]
    # 보내기 전에 서버가 죽었다 — 다시 연 store 에서 기동 스캔이 둘 다 보낸다
    store.close()
    again = Store(tmp_path / "rcm.db")
    try:
        n = notifier(again, rules, run=run)
        assert n.scan() == 2
        assert sorted(e["RCM_EVENT"] for e in envs(run)) == ["irreversible_hold", "job_finished"]
        assert {e["RCM_JOB_ID"] for e in envs(run)} == {str(jid)}
        assert n.scan() == 0  # 보낸 것은 다시 보내지 않는다
    finally:
        again.close()


def test_rl54_a_hold_row_does_not_hide_the_job_from_the_job_end_scan(store):
    jid = held(store)
    run = FakeRun()
    n = notifier(store, [rule("all"), rule("holds", on=("irreversible_hold",))], run=run)
    assert n.deliver_hold(jid) == 1  # 홀드 알림만 먼저 갔다
    assert [j.id for j in store.list_unnotified_finished(NOW)] == [jid]
    assert n.scan() == 1  # 잡 끝 알림 하나
    assert [e["RCM_EVENT"] for e in envs(run)] == ["irreversible_hold", "job_finished"]


def test_rl54_a_local_recover_on_start_hold_is_announced(store):
    jid = running(store, "ship", worker=None)
    lost, _ = store.recover_on_start(at(50))
    assert lost == [jid] and store.has_open_hold(jid)
    run = FakeRun()
    n = notifier(store, [rule("holds", on=("irreversible_hold",))], run=run)
    assert n.scan() == 1
    (env,) = envs(run)
    assert env["RCM_HOLD_CAUSE"] == "server_restarted"
    assert env["RCM_HOLD_WORKER"] == ""  # 로컬 레인
    assert "on the server's local lane" in env["RCM_SUMMARY"]


# ── 버스 사건과 설정 ──────────────────────────────────────────────────────────


def test_rl54_the_notifier_thread_sends_the_hold_on_the_bus_event(store):
    import time

    jid = held(store)
    run = FakeRun()
    n = notifier(store, [rule("holds", on=("irreversible_hold",))], run=run)
    # 보존 기간 밖으로 돌려 기동 스캔이 집지 않게 한다 — 이 시험은 버스 사건의 길만 본다
    old = (NOW - timedelta(days=4000)).timestamp()
    store._conn().execute("UPDATE irreversible_holds SET created_at=?", (old,))
    n.start()
    try:
        deadline = time.monotonic() + 5
        while not n._thread or not n.bus.subscriber_count:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        n.bus.publish(KIND_IRREVERSIBLE_HOLD, {"job_id": jid}, at=at(2))
        while len(run.calls) < 1:
            assert time.monotonic() < deadline, "no hold notification from the bus event"
            time.sleep(0.02)
    finally:
        n.stop()
    assert [e["RCM_EVENT"] for e in envs(run)] == ["irreversible_hold"]


def test_rl54_the_server_publishes_the_hold_event_after_it_holds(tmp_path):
    from test_worker_api import WorkerServer, sh

    srv = WorkerServer(tmp_path, presets=[sh("ship", "echo ship", irreversible=True)])
    try:
        sub = srv.app.bus.subscribe()
        srv.registered("build-02")
        jid = srv.queued_job(preset="ship")
        assert srv.claimed("build-02", 1) == jid
        # 표식 잡의 finish 에 group_gone 이 없다 — 확인 없는 닫힘이라 서버가 홀드를 건다(§4.12)
        assert srv.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
        assert srv.store.has_open_hold(jid)
        kinds = []
        while (ev := sub.get(timeout=0.2)) is not None:
            kinds.append((ev.kind, ev.data.get("job_id")))
        assert (KIND_IRREVERSIBLE_HOLD, jid) in kinds
    finally:
        srv.close()


# ── 홀드를 만드는 모든 길이 알린다(P3 1라운드 MED) ──────────────────────────────


@pytest.fixture
def wsrv(tmp_path):
    from test_worker_api import WorkerServer, sh

    s = WorkerServer(
        tmp_path, presets=[sh("ship", "echo ship", irreversible=True), sh("ok", "echo ok")]
    )
    yield s
    s.close()


def hold_events(sub: Any) -> list[int]:
    out = []
    while (ev := sub.get(timeout=0.2)) is not None:
        if ev.kind == KIND_IRREVERSIBLE_HOLD:
            out.append(ev.data.get("job_id"))
    return out


@pytest.mark.parametrize("by", ["heartbeat", "janitor"])
def test_rl54_a_cancel_unconfirmed_hold_is_announced_once(wsrv, by):
    """들리는 세션이 취소를 확인하지 않아 `cancel_unconfirmed` 로 닫힌 표식 잡의 홀드 — heartbeat
    가 닫든 janitor 가 닫든 버스 사건 `irreversible_hold` 가 한 번 나간다."""
    wsrv.registered("build-02")
    jid = wsrv.queued_job(preset="ship")
    assert wsrv.claimed("build-02", 1) == jid
    assert wsrv.cancel(jid)[1]["state"] == "cancelling"
    # 워커가 취소를 들었다 — 요청 뒤로 들리지 않은 세션은 취소를 모르니 닫지 않는다(T4 · P3 2라운드)
    wsrv.clock.advance(1)
    assert wsrv.heartbeat("build-02", jobs=[jid])[1]["cancel"] == [jid]
    job = wsrv.store.get_job(jid)
    past = (job.cancel.kill_at - wsrv.clock()).total_seconds() + 2 * 5 + 1
    assert past < 60  # 세션은 worker_timeout 안에 들린 채다
    sub = wsrv.app.bus.subscribe()
    wsrv.clock.advance(past)
    if by == "heartbeat":
        assert wsrv.heartbeat("build-02", jobs=[jid])[0] == 200
    else:
        wsrv.app.mark_lost_workers(wsrv.clock.now)
    assert wsrv.store.get_job(jid).summary_code == "cancel_unconfirmed"
    assert wsrv.store.has_open_hold(jid)
    assert hold_events(sub) == [jid]
    wsrv.app.mark_lost_workers(wsrv.clock.now)
    assert hold_events(sub) == []  # 두 번 알리지 않는다


def test_rl54_a_legacy_worker_forgetting_its_irreversible_job_announces_the_hold(wsrv):
    """옛 워커(세션 없음)의 heartbeat 목록에서 빠진 레거시 표식 잡 — `worker_restarted_without_job`
    으로 닫히며 홀드가 걸리고 그 사건이 한 번 나간다."""
    from remote_ci_monitor import __version__
    from remote_ci_monitor.core.model import RUNNING

    wsrv.store.register_worker(
        "build-02", pool="default", lanes=1, host_name=None, version=__version__, now=wsrv.clock()
    )
    jid = wsrv.queued_job(preset="ship")
    wsrv.store._conn().execute(
        "UPDATE jobs SET state=?, worker_name='build-02', lane=1, started_at=? WHERE id=?",
        (RUNNING, wsrv.clock().timestamp(), jid),
    )
    sub = wsrv.app.bus.subscribe()
    status, _ = wsrv.req(
        "POST", "/worker/heartbeat", token="build-02", json_body={"jobs": []}, legacy=True
    )
    assert status == 200
    assert wsrv.store.get_job(jid).summary_code == "worker_restarted_without_job"
    assert wsrv.store.has_open_hold(jid)
    assert hold_events(sub) == [jid]


# ── 닫기 한 무리가 중간에 끊겨도 커밋된 홀드는 알린다(P3 3라운드 LOW B1) ────────────


@pytest.fixture
def two(tmp_path):
    """표식 프리셋 둘(그룹이 따로라 한 세션이 둘을 같이 돌린다)을 한 세션이 집은 서버."""
    from test_worker_api import WorkerServer, sh

    s = WorkerServer(
        tmp_path,
        presets=[
            sh("ship", "echo ship", irreversible=True),
            sh("ship2", "echo 2", irreversible=True),
        ],
    )
    s.registered("build-02", lanes=2)
    a = s.queued_job(preset="ship")
    b = s.queued_job(preset="ship2")
    assert s.claimed("build-02", 1) == a and s.claimed("build-02", 2) == b
    yield s, a, b
    s.close()


def _second_finish_breaks(monkeypatch, store: Store) -> None:
    """두 번째 `store.finish` 가 커밋하지 못하고 터진다(디스크 오류처럼 — DB 바쁨이 아니다)."""
    real = store.finish
    calls: list[int] = []

    def finish(job_id: int, *a: Any, **kw: Any) -> bool:
        calls.append(job_id)
        if len(calls) == 2:
            raise RuntimeError("disk I/O error")
        return real(job_id, *a, **kw)

    monkeypatch.setattr(store, "finish", finish)


def _closed_one(srv: Any, a: int, b: int) -> int:
    states = {j: srv.store.get_job(j).state for j in (a, b)}
    first = [j for j, st in states.items() if st == LOST]
    assert len(first) == 1, states
    return first[0]


def test_rl54_a_janitor_tick_that_breaks_after_a_commit_still_announces_that_hold(two, monkeypatch):
    """janitor 한 틱이 유예로 닫는 둘 가운데 첫째를 커밋하고 둘째에서 터진다. 알림을 틱 끝에 모아
    두면 첫째 홀드의 알림이 빠진다 — 커밋된 홀드는 예외가 나가도 알린다."""
    srv, a, b = two
    srv.clock.advance(601)  # 유예 G(600) 를 넘겼다
    _second_finish_breaks(monkeypatch, srv.store)
    sub = srv.app.bus.subscribe()
    with pytest.raises(RuntimeError):
        srv.app.mark_lost_workers(srv.clock.now)
    first = _closed_one(srv, a, b)
    assert srv.store.has_open_hold(first)
    assert hold_events(sub) == [first]


def test_rl54_a_heartbeat_that_breaks_after_a_commit_still_announces_that_hold(two, monkeypatch):
    """T5 로 잊힌 잡 둘을 닫는 heartbeat 가 첫째를 커밋하고 둘째에서 터진다 — 첫째 홀드는 알린다."""
    srv, a, b = two
    _second_finish_breaks(monkeypatch, srv.store)
    sub = srv.app.bus.subscribe()
    status, _ = srv.heartbeat("build-02", jobs=[], seq=5, claims_settled=10)
    assert status == 500
    first = _closed_one(srv, a, b)
    assert srv.store.has_open_hold(first)
    assert hold_events(sub) == [first]


def test_rl54_a_failing_hold_announcement_does_not_stop_the_rest_of_the_tick(two, monkeypatch):
    """홀드 알림 하나가 터져도 같은 틱의 다른 알림 · 발행은 계속된다 — 로컬 레인의
    `Worker._hold_created` 와 같이 알림 오류를 삼키고 로그에 남긴다."""
    srv, a, b = two
    srv.clock.advance(601)
    seen: list[int] = []

    def flaky(job_id: int) -> None:
        seen.append(job_id)
        if len(seen) == 1:
            raise RuntimeError("bus broke")

    monkeypatch.setattr(srv.app, "_on_hold_created", flaky)
    lost = srv.app.mark_lost_workers(srv.clock.now)
    assert sorted(lost) == sorted([a, b])
    assert sorted(seen) == sorted([a, b])


HOLD_MAKERS = {"finish", "recover_on_start", "open_session"}


def _calls(fn: Any, attr: str) -> list[Any]:
    import ast

    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
    ]


def test_rl54_every_path_that_makes_a_hold_calls_hold_created():
    """홀드를 넣는 store 메서드와, 그것을 부르는 서버 쪽 함수를 짝지어 잠근다. 홀드를 만들 수 있는
    호출(`store.finish(…, hold_cause=…)` · `store.recover_on_start` · `store.open_session`)이 있는
    함수는 `_hold_created` 를 부르거나, 그 함수를 부르는 모든 함수가 부른다(잠금 밖으로 미루는 한
    단계). 새 길이 알림 없이 홀드를 만들면 여기서 빨갛다."""
    import ast
    from pathlib import Path

    import remote_ci_monitor

    root = Path(remote_ci_monitor.__file__).parent
    store_tree = ast.parse((root / "store.py").read_text(encoding="utf-8"))
    makers = {
        fn.name
        for fn in ast.walk(store_tree)
        if isinstance(fn, ast.FunctionDef) and fn.name != "_hold_tx" and _calls(fn, "_hold_tx")
    }
    assert makers == HOLD_MAKERS  # 새 메서드가 홀드를 넣으면 이 짝 맞추기에 더한다
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "store.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        announces = {f.name for f in funcs if _calls(f, "_hold_created")}
        for fn in funcs:
            making = [
                c
                for m in HOLD_MAKERS
                for c in _calls(fn, m)
                if isinstance(c.func.value, ast.Attribute)
                and c.func.value.attr == "store"
                and (m != "finish" or any(k.arg == "hold_cause" for k in c.keywords))
            ]
            if not making or fn.name in announces:
                continue
            callers = {f.name for f in funcs if _calls(f, fn.name)}
            if not callers or not callers <= announces:
                problems.append(f"{path.name}:{fn.lineno} {fn.name}")
    assert problems == []


def test_rl54_on_accepts_irreversible_hold_and_the_default_leaves_it_out():
    assert "irreversible_hold" in rule("h", on=("irreversible_hold",)).on
    assert rule("d").on == frozenset(NOTIFY_DEFAULT_STATES)
    assert "irreversible_hold" not in rule("d").on
    with pytest.raises(ConfigError):
        rule("bad", on=("held",))


# ── 로컬 레인도 러너의 `group_gone` 을 읽는다(P3 2라운드 HIGH · §4.12 확인 표 4행) ────────


def _local_lane_run(
    tmp_path,
    monkeypatch,
    how: str,
    gone: bool | None,
    *,
    rc: int | None = 0,
    flags: dict[str, bool] | None = None,
    race: str | None = None,
):
    """로컬 레인 하나가 표식 잡 하나를 가짜 러너로 돌린다. 러너는 `how` 대로 끝난다 — 결과
    (`result`, `rc` · `flags` 는 그 결과의 종료 코드와 취소 · 시간 초과 · 멈춤 표시) ·
    `RunnerError`(`runner_error`) · 관찰자 예외(`crash`), 그리고 띄우기 전의 같은 둘
    (`*_before_spawn`, `spawning` 을 부르지 않는다). `race` 는 러너 뒤의 경합이다.

    - `crash_after_result`: 러너가 결과를 낸 뒤 레인의 종료 규칙이 터진다(바깥 처리기).
    - `cancel_before_retried`: 75 반복 결과를 닫기 직전에 취소가 들어온다(`retried` 가 진다).
    - `cancel_before_fail`: 띄운 뒤의 `RunnerError` 를 닫기 직전에 취소가 들어온다.

    (잡 id, 홀드가 있는가, 버스 사건, 닫힌 잡)을 돌려준다."""
    import time

    from remote_ci_monitor import worker as worker_mod
    from remote_ci_monitor.runner import RunnerError, RunResult
    from test_worker_api import GIT_SHA, WorkerServer, sh

    def cancel_now(store: Store, jid: int) -> None:
        assert store.request_cancel(jid, "alice", datetime.now(UTC), 1) == "cancelling"

    def fake_run_job(spec, observer, **_kw):
        assert spec.irreversible is True
        if not how.endswith("_before_spawn"):
            spawning = getattr(observer, "spawning", None)
            if callable(spawning):
                spawning()
        at = datetime.now(UTC)
        if how == "result":
            return RunResult(rc=rc, started=at, finished=at, group_gone=gone, **(flags or {}))
        err: Exception = (
            RunnerError("launch_failed", error="OSError")
            if how.startswith("runner_error")
            else RuntimeError("observer broke")
        )
        if gone is not None:
            err.group_gone = gone  # type: ignore[attr-defined]
        if race == "cancel_before_fail":
            cancel_now(observer.worker.store, spec.job_id)
        raise err

    real_outcome_for = worker_mod.outcome_for

    def racing_outcome_for(job, *a: Any, **kw: Any):
        if race == "crash_after_result":
            raise RuntimeError("outcome rule broke")
        oc = real_outcome_for(job, *a, **kw)
        if race == "cancel_before_retried":
            cancel_now(srv.store, job.id)
        return oc

    monkeypatch.setattr(worker_mod, "run_job", fake_run_job)
    monkeypatch.setattr(worker_mod, "outcome_for", racing_outcome_for)
    # 반복은 git_ref 프리셋에만 있다(M5s) — 가짜 러너라 자재화는 돌지 않는다
    retrying = race == "cancel_before_retried"
    retry = {
        "source_modes": ["git_ref"],
        "repo": "app",
        "retry": {"on_exit": 75, "interval_seconds": 60, "max_attempts": 3},
    }
    preset = sh("ship", "echo ship", irreversible=True, **(retry if retrying else {}))
    if retrying:
        # 설정 로드는 표식 프리셋의 `[retry]` 를 거절한다(P6 GAP4). 그래도 표식을 달기 전에
        # `[retry]` 로 제출된 잡은 반복 정책을 얼린 채 남고 표식 뒤에 claim 되면 표식 잡이다 —
        # 그 잡의 상태를 로더를 비켜 만든다(제출 때 반복을 얼리고 claim 때 표식을 얼린다)
        import dataclasses

        import test_worker_api

        real_parse = test_worker_api.parse_preset

        def marked_after_submit(raw: dict[str, Any]) -> Any:
            if raw.get("name") != "ship":
                return real_parse(raw)
            plain = real_parse({k: v for k, v in raw.items() if k != "irreversible"})
            return dataclasses.replace(
                plain,
                irreversible=True,
                concurrency_group="irreversible:ship",
                concurrency_scope="global",
                group_synthetic=True,
            )

        monkeypatch.setattr(test_worker_api, "parse_preset", marked_after_submit)
    # 서버가 제 레인을 띄운다(`App.start`) — 홀드 알림을 넘기는 배선까지 본다
    srv = WorkerServer(tmp_path, workers=True, presets=[preset])
    try:
        sub = srv.app.bus.subscribe()
        if retrying:
            body = {
                "preset": "ship",
                "inputs": {},
                "source": {"mode": "git_ref", "ref": GIT_SHA},
                "requester_label": "alice@host",
                "join": False,
            }
            status, resp = srv.req("POST", "/jobs", token="alice", json_body=body)
            assert status == 201, resp
            jid = int(resp["job_id"])
        else:
            jid = srv.queued_job(preset="ship")
        deadline = time.monotonic() + 10
        while not srv.store.get_job(jid).is_terminal:
            assert time.monotonic() < deadline, "the local lane did not close the job"
            time.sleep(0.02)
        return jid, srv.store.has_open_hold(jid), hold_events(sub), srv.store.get_job(jid)
    finally:
        srv.close()


@pytest.mark.parametrize(
    "how, gone, held",
    [
        ("result", False, True),  # SIGKILL 2초 뒤에도 그룹이 살아 있다(`_reap_group`)
        ("result", None, True),  # 모름
        ("result", True, False),
        ("runner_error", False, True),
        ("runner_error", None, True),
        ("runner_error", True, False),
        ("crash", False, True),  # `_abandon` 이 그룹을 못 끝냈다
        ("crash", None, True),
        ("crash", True, False),
        ("runner_error_before_spawn", None, False),  # 띄운 적 없다 — 확인된 것이다
        ("crash_before_spawn", None, False),
    ],
)
def test_rl54_a_local_lane_irreversible_job_holds_unless_its_group_is_gone(
    tmp_path, monkeypatch, how, gone, held
):
    """§4.12 확인 표 4행: 로컬 레인은 러너가 돌아온 뒤 `group_gone` 을 서버가 직접 본다. 사라졌음이
    확인되지 않은 닫힘은 그 그룹을 홀드하고 한 번 알린다. 확인됐으면 홀드도 알림도 없다."""
    jid, has_hold, events, _job = _local_lane_run(tmp_path, monkeypatch, how, gone)
    assert has_hold is held
    assert events == ([jid] if held else [])


@pytest.mark.parametrize(
    "flags, rc, state",
    [
        ({"cancelled": True}, -15, "cancelled"),
        ({"timed_out": True}, -15, "timed_out"),
        ({"lost": True}, -15, "lost"),  # 멈춤(`should_stop`) — 서버가 내려가는 중
        ({}, 2, "failed"),
    ],
    ids=["cancelled", "timed_out", "stopped", "exit_2"],
)
@pytest.mark.parametrize("gone, held", [(False, True), (None, True), (True, False)])
def test_rl54_a_local_lane_result_that_is_not_exit_0_follows_the_same_hold_rule(
    tmp_path, monkeypatch, flags, rc, state, gone, held
):
    """P3 3라운드 LOW(A2): 끝 상태가 무엇이든(취소 · 시간 초과 · 멈춤 · 0 아닌 종료) 홀드는
    `group_gone` 하나로 정한다."""
    jid, has_hold, events, job = _local_lane_run(
        tmp_path, monkeypatch, "result", gone, rc=rc, flags=flags
    )
    assert job.state == state
    assert has_hold is held
    assert events == ([jid] if held else [])


@pytest.mark.parametrize("gone, held", [(True, False), (False, True), (None, True)])
def test_rl54_a_local_lane_error_after_the_runner_returned_keeps_that_result(
    tmp_path, monkeypatch, gone, held
):
    """P3 3라운드 LOW(A1): 러너가 결과를 낸 뒤 레인이 터지면 바깥 처리기가 잡을 닫는다. 그때 러너의
    결과와 그 `group_gone` 을 버리지 않는다 — 원격 워커의 바깥 안전망과 같다. 버리면 그룹이
    사라졌다고 확인된 잡도 홀드되고, 끝난 잡이 실패로 남는다."""
    jid, has_hold, events, job = _local_lane_run(
        tmp_path, monkeypatch, "result", gone, race="crash_after_result"
    )
    assert (job.state, job.exit_code) == ("succeeded", 0)
    assert has_hold is held
    assert events == ([jid] if held else [])


@pytest.mark.parametrize("race", ["cancel_before_retried", "cancel_before_fail"])
@pytest.mark.parametrize("gone, held", [(False, True), (True, False)])
def test_rl54_a_local_lane_close_that_loses_to_a_cancel_keeps_the_hold_decision(
    tmp_path, monkeypatch, race, gone, held
):
    """P3 3라운드 LOW(A2): 로컬 레인의 닫기가 취소에 지면 `_close_cancelled_before_start` 가
    닫는다. 75 반복 결과(`retried` 는 `running` 에서만 간다)와 띄운 뒤의 `RunnerError`
    (`_fail_before_start`)가 그렇다. 두 자리 모두 러너가 정한 홀드를 그대로 넘긴다."""
    how = "result" if race == "cancel_before_retried" else "runner_error"
    jid, has_hold, events, job = _local_lane_run(tmp_path, monkeypatch, how, gone, rc=75, race=race)
    assert job.state == "cancelled"
    assert has_hold is held
    assert events == ([jid] if held else [])


def test_rl54_a_local_claim_that_commits_and_then_cannot_read_the_job_closes_it_unheld(
    tmp_path, monkeypatch
):
    """P3 3라운드 LOW(B2): 로컬 claim 이 커밋한 뒤 잡을 다시 읽다(`get_job`) 터지면 레인은 집힌 잡
    id 를 몰라 그 잡이 `running` 으로 남았다. 재시작하면 `recover_on_start` 가 표식 잡의 그룹을
    홀드한다 — 명령은 돈 적이 없는데. claim 은 그 id 를 예외에 실어 넘기고, 레인은 그 잡을 홀드
    없이 닫는다(띄운 적 없다)."""
    import sqlite3
    import threading
    import time

    from remote_ci_monitor.core.model import FAILED
    from test_worker_api import WorkerServer, sh

    launched: list[int] = []
    srv = WorkerServer(
        tmp_path, presets=[sh("ship", "echo ship", irreversible=True)], workers=False
    )
    try:
        store = srv.store
        real_claim, real_get = store.claim, store.get_job
        # 읽기 실패는 claim 하는 그 스레드의 `get_job` 에만 넣는다. 레인이 claim 하는 동안 다른
        # 스레드(보관 · 청소 · 알림)도 `get_job` 을 부른다 — 그쪽이 실패를 가져가면 claim 은 잡을
        # 읽고 명령을 띄운다(CI 잡 4324 의 `assert [1] == []`, 그 스레드는 rcm-retention 이었다)
        state: dict[str, Any] = {"claimer": None, "raised": False}

        def claim(*a: Any, **kw: Any):
            state["claimer"] = threading.get_ident()
            try:
                if not state["raised"]:
                    time.sleep(0.3)  # 다른 스레드가 `get_job` 을 부를 틈을 일부러 연다
                return real_claim(*a, **kw)
            finally:
                state["claimer"] = None

        def get_job(job_id: int):
            if state["claimer"] == threading.get_ident() and not state["raised"]:
                state["raised"] = True
                raise sqlite3.OperationalError("disk I/O error")
            return real_get(job_id)

        monkeypatch.setattr(store, "claim", claim)
        monkeypatch.setattr(store, "get_job", get_job)
        from remote_ci_monitor import worker as worker_mod

        monkeypatch.setattr(worker_mod, "run_job", lambda spec, *_a, **_k: launched.append(1))
        jid = srv.queued_job(preset="ship")
        srv.app.start()
        deadline = time.monotonic() + 10
        while not real_get(jid).is_terminal:
            assert time.monotonic() < deadline, f"job {jid} stayed {real_get(jid).state}"
            time.sleep(0.02)
        assert state["raised"] is True
        j = real_get(jid)
        assert (j.state, j.summary_code) == (FAILED, "worker_error")
        assert launched == []
        assert not store.has_open_hold(jid)
    finally:
        srv.close()


def test_rl54_the_server_hands_its_hold_announcer_to_the_local_lanes(tmp_path, monkeypatch):
    """서버가 로컬 레인을 띄울 때 홀드 알림(`_hold_created`)을 넘긴다 — 안 넘기면 위 홀드가
    알림 없이 남는다."""
    from remote_ci_monitor import server as server_mod
    from test_worker_api import WorkerServer, sh

    seen: dict[str, Any] = {}

    def fake_start_workers(*_a: Any, **kw: Any) -> list[Any]:
        seen.update(kw)
        return []

    monkeypatch.setattr(server_mod, "start_workers", fake_start_workers)
    srv = WorkerServer(tmp_path, presets=[sh("ship", "echo ship", irreversible=True)])
    try:
        srv.app.start()
        assert seen.get("on_hold") == srv.app._hold_created
    finally:
        srv.close()


#: 홀드 판단 없이 잡을 닫아도 되는 `store.finish` — 프로세스가 돈 적 없는 상태에서만 닫는다
PRE_RUN_ONLY = {"(UPLOADING,)"}
#: `hold_cause=` 를 받아 `store.finish` 로 넘기는 로컬 레인의 닫기 도우미(`worker.py`). 이것을
#: 부르는 자리도 홀드를 판단해야 한다 — 기본값이 있으면 판단을 건너뛴 호출이 보이지 않는다
HOLD_PASSERS = {"_fail_before_start", "_close_cancelled_before_start"}


def _undecided_closes(source: str, name: str) -> list[str]:
    """`source` 에서 홀드를 판단하지 않고 잡을 닫는 호출(`파일:줄`).

    판단은 `hold_cause=` 에 식을 넘기는 것이다. 상수 `None` 은 판단이 아니다 — 「이 닫힘은
    늘 확인됐다」를 말하려면 그 확인(`_hold_cause(job, True)` 처럼)을 식으로 적는다(P3 3라운드
    LOW). 문자열 상수는 「늘 홀드」라는 판단이다."""
    import ast

    tree = ast.parse(source)
    problems: list[str] = []
    calls = [*_calls(tree, "finish"), *(c for m in sorted(HOLD_PASSERS) for c in _calls(tree, m))]
    for call in sorted(calls, key=lambda c: c.lineno):
        if call.func.attr == "finish" and not (
            isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "store"
        ):
            continue
        kw = {k.arg: k.value for k in call.keywords}
        cause = kw.get("hold_cause")
        if cause is not None and not (isinstance(cause, ast.Constant) and cause.value is None):
            continue
        only_from = kw.get("only_from")
        if cause is None and only_from is not None and ast.unparse(only_from) in PRE_RUN_ONLY:
            continue
        problems.append(f"{name}:{call.lineno}")
    return problems


def test_rl54_the_close_check_rejects_a_constant_none_and_a_skipped_helper_argument():
    """P3 3라운드 LOW(A3): 정적 잠금이 `hold_cause=None` 상수를 판단으로 셌다. 그 잠금 자체를
    잠근다 — 상수 None · 빠진 인자는 걸리고, 식 · 문자열 상수 · 시작 전 상태의 닫기는 지난다."""
    src = (
        "def f(self, job, gone):\n"
        "    self.store.finish(1, 'lost', hold_cause=None)\n"
        "    self.store.finish(2, 'lost')\n"
        "    self._fail_before_start(job, 'preset_missing')\n"
        "    self._close_cancelled_before_start(job, hold_cause=None)\n"
        "    self.store.finish(5, 'lost', hold_cause=self._hold_cause(job, gone))\n"
        "    self.store.finish(6, 'lost', hold_cause='worker_unreachable')\n"
        "    self.store.finish(7, 'failed', only_from=(UPLOADING,))\n"
        "    self._fail_before_start(job, 'x', hold_cause=self._hold_cause(job, True))\n"
        "    other.finish(9)\n"
    )
    assert _undecided_closes(src, "f.py") == ["f.py:2", "f.py:3", "f.py:4", "f.py:5"]


def test_rl54_every_finish_outside_the_store_decides_the_hold():
    """P2-9 의 짝 맞추기는 `hold_cause=` 를 **넘긴** 호출만 봤다 — 넘기지 않은 호출은 홀드를 만들지
    않으니 알림을 빠뜨릴 수도 없어서 보이지 않았다(로컬 레인이 그 길이었다). 그래서 반대쪽도
    잠근다: store 밖의 모든 `store.finish` 와 로컬 레인의 닫기 도우미는 `hold_cause=` 에 식을
    넘겨 확인 없는 닫힘인지 정하거나, 프로세스가 돈 적 없는 상태(`only_from=(UPLOADING,)`)에서만
    닫는다. 상수 None 은 판단이 아니다(`_undecided_closes`)."""
    from pathlib import Path

    import remote_ci_monitor

    root = Path(remote_ci_monitor.__file__).parent
    problems: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "store.py":
            continue
        problems += _undecided_closes(path.read_text(encoding="utf-8"), path.name)
    assert problems == []
