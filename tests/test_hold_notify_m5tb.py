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


def test_rl54_on_accepts_irreversible_hold_and_the_default_leaves_it_out():
    assert "irreversible_hold" in rule("h", on=("irreversible_hold",)).on
    assert rule("d").on == frozenset(NOTIFY_DEFAULT_STATES)
    assert "irreversible_hold" not in rule("d").on
    with pytest.raises(ConfigError):
        rule("bad", on=("held",))
