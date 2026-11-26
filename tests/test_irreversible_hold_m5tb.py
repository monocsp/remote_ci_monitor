"""되돌릴 수 없는 잡의 그룹 홀드(M5t-b 결정 330 · 339-d) — 저장소 층과 설정 층.

명세는 docs/m5tb-workplan.md §4.9(DB v26) · §4.12(홀드) · §4.3(얼림). 시험 이름의 RL 번호는 §7.1 의
시나리오 행이다. 시각은 고정 NOW 기준으로 직접 찍는다(스레드 · sleep 없음).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

import pytest

from jobfactory import NOW
from remote_ci_monitor import store as store_mod
from remote_ci_monitor.config import ConfigError, parse_preset
from remote_ci_monitor.core.model import (
    CANCELLING,
    DEFAULT_POOL,
    LOST,
    QUEUED,
    RUNNING,
    SCOPE_GLOBAL,
    SUCCEEDED,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import DB_VERSION, Store, StoreError

ALICE = Requester(name="alice-laptop", label="alice@laptop")
LINUX = "linux"
SYNTH = "irreversible:deploy"


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.db")
    s.irreversible = {"deploy": (SYNTH, SCOPE_GLOBAL)}
    s.known_presets = frozenset({"deploy", "gate", "qa"})
    yield s
    s.close()


def enqueue(
    store: Store,
    preset: str = "gate",
    *,
    group: str | None = None,
    scope: str = "pool",
    pool: str = DEFAULT_POOL,
    tree: str = "9f8e",
    now: datetime = NOW,
):
    src = Source(mode="tree", repo="org/app", base_sha="abc", dirty=True, tree_hash=tree)
    return store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=group,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key(preset, {}, tree),
        now=now,
        state=QUEUED,
        pool=pool,
        concurrency_scope=scope,
    )


def register(store: Store, name: str, pool: str = DEFAULT_POOL, lanes: int = 2) -> None:
    store.register_worker(name, pool=pool, lanes=lanes, host_name=None, version="x", now=NOW)


def claim_remote(store: Store, name: str, lane: int = 1, pool: str = DEFAULT_POOL, **kw: Any):
    return store.claim(lane, NOW, pool=pool, worker_name=name, **kw)


def holds(store: Store) -> list[dict[str, Any]]:
    return [dict(r) for r in store._conn().execute("SELECT * FROM irreversible_holds")]


def hold_events(store: Store, job_id: int) -> list[str]:
    import json

    return [
        json.loads(r["payload"])["action"]
        for r in store._conn().execute(
            "SELECT payload FROM events WHERE job_id=? AND kind='hold' ORDER BY id", (job_id,)
        )
    ]


# ── DB v26 ────────────────────────────────────────────────────────────────────

V26_JOB_COLUMNS = (
    "worker_session_id",
    "irreversible",
    "disconnect_grace_seconds",
    "claim_seq",
    "log_offset",
    "log_file_bytes",
    "log_complete",
)


def test_rl24_a_v25_database_migrates_to_v26_and_keeps_every_job(tmp_path):
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    register(s, "build-02")
    running = enqueue(s, tree="r")
    s.claim(1, NOW, worker_name="build-02")
    lost = enqueue(s, tree="l")
    s.claim(2, NOW, worker_name="build-02")
    s.finish(lost.id, LOST, now=at(5), summary="worker build-02 unreachable for 70s")
    cancelling = enqueue(s, tree="c")
    s.claim(2, NOW, worker_name="build-02")
    s.request_cancel(cancelling.id, "alice-laptop", at(6), 10)
    queued = enqueue(s, tree="q")
    before = {
        r["id"]: (r["state"], r["summary"], r["worker_name"])
        for r in s._conn().execute("SELECT * FROM jobs")
    }
    assert before[running.id][0] == RUNNING and before[cancelling.id][0] == CANCELLING
    assert before[queued.id][0] == QUEUED
    s.close()
    # v25 로 되돌린다 — v26 이 더한 표 · 열을 지운다
    c = sqlite3.connect(path)
    c.execute("DROP TABLE worker_sessions")
    c.execute("DROP TABLE irreversible_holds")
    for col in reversed(V26_JOB_COLUMNS):
        c.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
    c.execute("PRAGMA user_version=25")
    c.commit()
    c.close()
    s = Store(path)
    try:
        assert DB_VERSION == 26
        assert s.user_version() == 26
        after = {
            r["id"]: (r["state"], r["summary"], r["worker_name"])
            for r in s._conn().execute("SELECT * FROM jobs")
        }
        assert after == before
        for r in s._conn().execute("SELECT * FROM jobs"):
            for col in V26_JOB_COLUMNS:
                assert r[col] is None  # 옛 행은 로컬 또는 레거시다(T11)
        assert s._conn().execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 0
        assert s._conn().execute("SELECT count(*) FROM irreversible_holds").fetchone()[0] == 0
    finally:
        s.close()
    assert (path.parent / "backup" / "rcm.sqlite3.v25.bak").exists()


def test_rl24_an_older_build_refuses_the_v26_database(tmp_path, monkeypatch):
    path = tmp_path / "rcm.sqlite3"
    Store(path).close()
    monkeypatch.setattr(store_mod, "DB_VERSION", 25)
    with pytest.raises(StoreError, match="newer than this build"):
        Store(path)


def test_rl24_new_database_has_the_v26_tables_and_columns(tmp_path):
    s = Store(tmp_path / "rcm.db")
    try:
        cols = [r[1] for r in s._conn().execute("PRAGMA table_info(jobs)")]
        assert tuple(cols[-len(V26_JOB_COLUMNS) :]) == V26_JOB_COLUMNS
        sess = [r[1] for r in s._conn().execute("PRAGMA table_info(worker_sessions)")]
        assert sess == [
            "id",
            "worker_name",
            "registered_at",
            "fenced_at",
            "fenced_by",
            "orphan_unverified",
        ]
        hcols = [r[1] for r in s._conn().execute("PRAGMA table_info(irreversible_holds)")]
        assert hcols == [
            "job_id",
            "concurrency_group",
            "concurrency_scope",
            "pool",
            "host_key",
            "worker_name",
            "cause",
            "created_at",
            "released_at",
            "release_kind",
            "released_by",
            "release_reason",
        ]
    finally:
        s.close()


# ── 얼림 · 합성 그룹 ───────────────────────────────────────────────────────────


def test_rl48_claim_freezes_irreversible_grace_session_and_claim_seq(store):
    register(store, "build-02")
    j = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL)
    got = claim_remote(store, "build-02", session_id="s" * 32, claim_seq=7, grace_seconds=600)
    assert got is not None and got.id == j.id
    assert got.irreversible is True
    assert got.disconnect_grace_seconds == 600
    assert got.worker_session_id == "s" * 32
    assert got.claim_seq == 7
    g = enqueue(store, "gate", tree="g2")
    got2 = claim_remote(store, "build-02", lane=2, session_id="s" * 32, claim_seq=8)
    assert got2.id == g.id and got2.irreversible is False


def test_rl48_local_claim_freezes_irreversible_without_session_or_grace(store):
    j = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL)
    got = store.claim(1, NOW)
    assert got.id == j.id and got.irreversible is True
    assert got.worker_session_id is None and got.disconnect_grace_seconds is None


def test_rl48_a_queued_job_without_group_gets_the_synthetic_group_at_claim(store):
    """표식을 달기 전에 제출된 대기 잡(그룹 NULL)과 그 프리셋의 새 제출(합성 그룹)은 어느 쪽이 먼저
    claim 되든 함께 돌지 않는다 — 다른 풀이어도(합성 그룹은 global)."""
    register(store, "build-02")
    register(store, "lin-01", pool=LINUX)
    old = enqueue(store, "deploy", group=None, tree="old")  # 표식 전 제출
    new = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, pool=LINUX, tree="new")
    first = claim_remote(store, "build-02")
    assert first.id == old.id
    assert first.concurrency_group == SYNTH and first.concurrency_scope == SCOPE_GLOBAL
    assert claim_remote(store, "lin-01", pool=LINUX) is None  # 새 제출이 막힌다
    assert store.get_job(new.id).state == QUEUED


def test_rl48_the_other_order_blocks_too(store):
    register(store, "build-02")
    register(store, "lin-01", pool=LINUX)
    new = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, pool=LINUX, tree="new")
    enqueue(store, "deploy", group=None, tree="old")
    assert claim_remote(store, "lin-01", pool=LINUX).id == new.id
    assert claim_remote(store, "build-02") is None  # 그룹 NULL 옛 잡도 유효 그룹으로 막힌다


def test_rl48_config_fills_a_synthetic_global_group_for_an_irreversible_preset():
    p = parse_preset({"name": "ship", "argv": ["sh", "-c", "true"], "irreversible": True})
    assert p.irreversible is True
    assert p.concurrency_group == "irreversible:ship"
    assert p.concurrency_scope == SCOPE_GLOBAL
    mine = parse_preset(
        {
            "name": "ship2",
            "argv": ["true"],
            "irreversible": True,
            "concurrency_group": "store",
            "concurrency_scope": "host",
        }
    )
    assert mine.concurrency_group == "store" and mine.concurrency_scope == "host"
    plain = parse_preset({"name": "gate", "argv": ["true"]})
    assert plain.irreversible is False and plain.concurrency_group is None
    with pytest.raises(ConfigError, match="irreversible must be true or false"):
        parse_preset({"name": "bad", "argv": ["true"], "irreversible": "yes"})


# ── 홀드 만들기 · 막기 · 남기 ──────────────────────────────────────────────────


def test_rl42_an_unconfirmed_close_holds_the_group_across_pools_restarts_and_gc(tmp_path):
    path = tmp_path / "rcm.db"
    s = Store(path)
    s.irreversible = {"deploy": (SYNTH, SCOPE_GLOBAL)}
    s.known_presets = frozenset({"deploy", "gate"})
    register(s, "build-02")
    register(s, "lin-01", pool=LINUX)
    x = enqueue(s, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, tree="x")
    claim_remote(s, "build-02", session_id="a" * 32, claim_seq=1, grace_seconds=600)
    same1 = enqueue(s, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, tree="s1")
    same2 = enqueue(s, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, pool=LINUX, tree="s2")
    other = enqueue(s, "gate", group="devices", tree="o")
    assert s.finish(
        x.id,
        LOST,
        now=at(700),
        summary="unreachable",
        summary_code="worker_unreachable",
        summary_args={"name": "build-02", "seconds": 700},
        hold_cause="worker_unreachable",
    )
    rows = holds(s)
    assert len(rows) == 1
    h = rows[0]
    assert (h["job_id"], h["concurrency_group"], h["concurrency_scope"]) == (x.id, SYNTH, "global")
    assert h["cause"] == "worker_unreachable" and h["worker_name"] == "build-02"
    assert h["released_at"] is None
    assert hold_events(s, x.id) == ["created"]
    # 같은 그룹 대기 잡은 어느 풀에서도 claim 되지 않는다. 다른 그룹 잡은 돈다
    assert claim_remote(s, "lin-01", pool=LINUX) is None
    got = claim_remote(s, "build-02")
    assert got is not None and got.id == other.id
    assert claim_remote(s, "build-02", lane=2) is None
    assert {j.id for j in s.list_active() if j.state == QUEUED} == {same1.id, same2.id}
    s.close()
    s = Store(path)  # 서버 재시작 — 홀드는 DB 에 남는다
    s.irreversible = {"deploy": (SYNTH, SCOPE_GLOBAL)}
    try:
        assert s.claim(1, at(9000), pool=LINUX, worker_name="lin-01") is None
        assert [h["job_id"] for h in s.open_holds()] == [x.id]
        # 오래된 잡 지우기는 열린 홀드의 잡을 지우지 않는다
        s._conn().execute("UPDATE jobs SET artifacts_purged_at=? WHERE id=?", (1.0, x.id))
        s.delete_old_jobs(at(10**9))
        assert s.get_job(x.id) is not None
        # admin 이 풀면 감사 행이 남고 대기 잡이 시작한다
        out = s.release_hold(x.id, kind="admin", by="macmini-admin", reason="checked", now=at(9100))
        assert out is not None and out["release_kind"] == "admin"
        again = s.release_hold(x.id, kind="admin", by="macmini-admin", reason=None, now=at(9200))
        assert again is None
        assert hold_events(s, x.id) == ["created", "released"]
        rows = holds(s)
        assert rows[0]["released_by"] == "macmini-admin" and rows[0]["release_reason"] == "checked"
        assert s.claim(1, at(9300), pool=LINUX, worker_name="lin-01") is not None
    finally:
        s.close()


def test_rl42_a_reversible_job_never_holds(store):
    register(store, "build-02")
    j = enqueue(store, "gate", group="devices", tree="g")
    claim_remote(store, "build-02", session_id="a" * 32, claim_seq=1, grace_seconds=600)
    nxt = enqueue(store, "gate", group="devices", tree="g2")
    assert store.finish(j.id, LOST, now=at(700), summary="x", hold_cause="worker_unreachable")
    assert holds(store) == []
    assert claim_remote(store, "build-02").id == nxt.id  # 결정 253: 일반 잡은 곧바로 다음


def test_rl42_a_confirmed_close_does_not_hold(store):
    register(store, "build-02")
    j = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL)
    claim_remote(store, "build-02", session_id="a" * 32, claim_seq=1, grace_seconds=600)
    assert store.finish(j.id, SUCCEEDED, now=at(10), exit_code=0)
    assert holds(store) == []


def test_rl42_local_irreversible_job_lost_on_restart_holds(tmp_path):
    path = tmp_path / "rcm.db"
    s = Store(path)
    s.irreversible = {"deploy": (SYNTH, SCOPE_GLOBAL)}
    s.known_presets = frozenset({"deploy", "gate"})
    x = enqueue(s, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, tree="x")
    g = enqueue(s, "gate", tree="g")
    s.claim(1, NOW)
    s.claim(2, NOW)
    lost, _ = s.recover_on_start(at(30))
    assert sorted(lost) == sorted([x.id, g.id])
    rows = holds(s)
    assert [(r["job_id"], r["cause"], r["worker_name"]) for r in rows] == [
        (x.id, "server_restarted", None)
    ]
    s.close()


# ── 홀드의 그룹은 잡 행에서 (결정 339-d) ──────────────────────────────────────


def test_rl53_hold_group_comes_from_the_job_row_not_the_current_config(store):
    register(store, "build-02")
    old = enqueue(store, "deploy", group=None, tree="o")
    claim_remote(store, "build-02", session_id="a" * 32, claim_seq=1, grace_seconds=600)
    # (a) 프리셋을 설정에서 지우고 reload — 표식도 사라진다
    store.irreversible = {}
    store.known_presets = frozenset({"gate"})
    assert store.finish(old.id, LOST, now=at(700), summary="x", hold_cause="worker_unreachable")
    rows = holds(store)
    assert [(r["concurrency_group"], r["concurrency_scope"]) for r in rows] == [(SYNTH, "global")]
    waiting = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, tree="w")
    assert claim_remote(store, "build-02") is None
    assert store.get_job(waiting.id).state == QUEUED


def _legacy_running(store: Store, preset: str, group: str | None = None) -> int:
    """v26 이전에 claim 된 잡 흉내 — 세션 · 얼린 값이 NULL 이다."""
    j = enqueue(store, preset, group=group, tree=f"legacy-{preset}-{group}")
    store._conn().execute(
        "UPDATE jobs SET state=?, worker_name=?, lane=1, started_at=? WHERE id=?",
        (RUNNING, "build-02", NOW.timestamp(), j.id),
    )
    return j.id


@pytest.mark.parametrize(
    ("irr", "known", "held"),
    [
        ({"deploy": (SYNTH, SCOPE_GLOBAL)}, {"deploy"}, True),  # (d) 지금 표식
        ({}, {"gate"}, True),  # (e) 설정에 없음 — 모름은 닫힌 쪽
        ({}, {"deploy"}, False),  # (f) 표식 없이 있음
    ],
)
def test_rl53_a_legacy_job_holds_when_its_preset_is_irreversible_or_gone(store, irr, known, held):
    register(store, "build-02")
    jid = _legacy_running(store, "deploy")
    store.irreversible = irr
    store.known_presets = frozenset(known)
    assert store.finish(jid, LOST, now=at(700), summary="x", hold_cause="worker_unreachable")
    rows = holds(store)
    if held:
        assert [(r["job_id"], r["concurrency_group"], r["concurrency_scope"]) for r in rows] == [
            (jid, SYNTH, "global")
        ]
    else:
        assert rows == []
