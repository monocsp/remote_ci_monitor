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


@pytest.mark.parametrize("claimant", [False, True])
def test_gap3_a_job_running_before_the_mark_blocks_the_synthetic_group(store, claimant):
    """# GAP3 reload 로 `irreversible = true` 가 붙는 날, 이미 도는 그 프리셋의 잡은 그룹이
    NULL 이다(claim 때 표식이 없었다). 그 잡도 유효 그룹 `irreversible:<프리셋>` 으로 센다 —
    아니면 새 제출이 막히지 않고 같은 명령이 두 번 돈다. claim 의 두 길(옛 풀 길 · M5n 후보 길)
    모두에서."""
    from remote_ci_monitor.store import Claimant

    store.irreversible = {}  # 표식 전
    register(store, "build-02")
    register(store, "lin-01", pool=LINUX)
    old = enqueue(store, "deploy", group=None, tree="old")
    got = claim_remote(store, "build-02")
    assert got.id == old.id and got.concurrency_group is None
    store.irreversible = {"deploy": (SYNTH, SCOPE_GLOBAL)}  # reload 가 표식을 붙였다
    new = enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL, pool=LINUX, tree="new")
    if claimant:
        c = Claimant(worker="lin-01", pool=LINUX, host_key=None, fingerprint_hash=None)
        second = store.claim(1, NOW, claimant=c)
    else:
        second = claim_remote(store, "lin-01", pool=LINUX)
    assert second is None
    assert store.get_job(new.id).state == QUEUED


def test_gap4_an_irreversible_preset_cannot_have_retry():
    """# GAP4 재시도는 명령을 다시 돌린다 — 표식 프리셋에 `[retry]` 가 있으면 재시도 종료 코드에서
    그룹이 사라진 채 retried 로 닫히고 다시 돈다(명령 두 번). 설정 로드가 거절한다(메인 세션
    결정). 표식이 없으면 `[retry]` 는 오늘처럼 받는다."""
    base = {
        "name": "ship",
        "argv": ["sh", "-c", "true"],
        "source_modes": ["git_ref"],
        "retry": {"on_exit": 75, "max_attempts": 2},
    }
    with pytest.raises(ConfigError, match="irreversible presets cannot have"):
        parse_preset({**base, "irreversible": True})
    assert parse_preset(base).retry is not None


@pytest.mark.parametrize("irreversible, shown", [(True, None), (False, 3), (None, 3)])
def test_p63_2_a_job_claimed_as_irreversible_shows_no_retry_limit(irreversible, shown):
    """# P6-3 #2 표식 전에 `[retry]` 로 제출된 잡은 반복 상한(3)을 얼린다. 표식 잡으로 claim
    되면 반복하지 않는다(`Job.retries_on`, P6-2 #1) — 잡 문서의 `max_attempts` 가 3 을 보이면
    「3번까지 다시 돈다」로 읽힌다. 반복하지 않는 잡의 관례(`retry: false`)대로 null 이다.
    아직 claim 전(None)이거나 표식이 아닌 잡은 얼린 상한 그대로다."""
    from jobfactory import job
    from remote_ci_monitor.core.status import chain_json

    j = job(
        7,
        "deploy",
        RUNNING,
        retry_on_exit=75,
        retry_interval_seconds=60,
        retry_max_attempts=3,
        irreversible=irreversible,
    )
    assert chain_json(j)["max_attempts"] == shown


def test_gap5_a_worker_with_an_open_hold_is_not_forgotten(store):
    """# GAP5 오래 떠나 있던 워커를 잊으면 세션 행도 지워진다. 그 워커가 돌아와 죽은 세션을
    `previous_sessions` 로 넘겨도 서버가 그 세션을 몰라 홀드를 스스로 풀지 못한다. 열린 홀드가
    있는 워커는 잊지 않는다 — 홀드가 풀리면 다음 쓸기에서 잊는다."""
    sid = "a" * 32
    store.open_session(
        "build-02",
        sid,
        previous=[],
        unverified=[],
        now=NOW,
        pool=DEFAULT_POOL,
        lanes=1,
        host_name=None,
        version="x",
    )
    register(store, "gone-01")
    enqueue(store, "deploy", group=SYNTH, scope=SCOPE_GLOBAL)
    job = claim_remote(store, "build-02", session_id=sid, claim_seq=1, grace_seconds=600)
    assert store.finish(job.id, LOST, now=at(1), summary="x", hold_cause="worker_unreachable")
    assert store.has_open_hold(job.id)
    later = NOW + timedelta(days=30)
    assert store.forget_workers(later) == ["gone-01"]
    assert store.get_worker("build-02") is not None
    assert store.get_session(sid) is not None
    store.release_hold(job.id, kind="admin", by="admin", reason="checked", now=at(2))
    assert store.forget_workers(later) == ["build-02"]


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


def test_rl48_the_server_logs_one_startup_line_per_synthetic_group(tmp_path, capsys, monkeypatch):
    """설정 로드 뒤 그룹 없는 표식 프리셋마다 기동 로그에 한 줄(그룹 `irreversible:<이름>` ·
    `global`). 사용자 그룹을 적은 표식 프리셋과 일반 프리셋은 줄이 없고, reload 가 다시 쓰지
    않는다."""
    import copy

    from remote_ci_monitor import server as server_mod
    from test_worker_api import WORKER_PRESETS, WorkerServer, sh

    presets = [
        *WORKER_PRESETS,
        sh("ship", "echo ship", irreversible=True),
        {**sh("ship2", "echo ship2", irreversible=True), "concurrency_group": "store"},
    ]
    capsys.readouterr()
    srv = WorkerServer(tmp_path, presets=presets, lanes=0)
    try:
        lines = [ln for ln in capsys.readouterr().err.splitlines() if "concurrency_group" in ln]
        assert lines == [
            "[rcm] preset ship: irreversible without concurrency_group — using group "
            "irreversible:ship (global)"
        ]
        fresh = copy.deepcopy(srv.app.config)
        srv.app.config.path = fresh.path = tmp_path / "server.toml"
        monkeypatch.setattr(server_mod, "load_server_config", lambda path: fresh)
        srv.app.reload_config()
        assert "concurrency_group" not in capsys.readouterr().err  # 프리셋마다 한 번
    finally:
        srv.close()


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


def test_rl53_a_renamed_group_in_the_config_does_not_move_the_hold(store):
    register(store, "build-02")
    old = enqueue(store, "deploy", group=None, tree="r")
    claim_remote(store, "build-02", session_id="b" * 32, claim_seq=1, grace_seconds=600)
    store.irreversible = {"deploy": ("renamed", SCOPE_GLOBAL)}  # 그룹 이름을 바꾸고 reload
    assert store.finish(old.id, LOST, now=at(700), summary="x", hold_cause="worker_unreachable")
    assert [r["concurrency_group"] for r in holds(store)] == [SYNTH]  # claim 때의 그룹


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


# ── 로컬 레인의 표식 잡은 감독자 아래에서 돈다(F76 · 결정 331, §4.13) ─────────


@pytest.mark.parametrize("irreversible", [True, False])
def test_rl43_a_local_lane_irreversible_job_runs_under_the_supervisor(tmp_path, irreversible):
    """로컬 레인 표식 잡의 부모는 서버 프로세스가 아니라 감독자다(서버가 죽으면 감독자가 끊는다).
    일반 잡은 감독자 없이 오늘처럼 돈다(결정 253)."""
    import os

    import test_worker as tw
    from remote_ci_monitor.config import ServerConfig

    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.grace_seconds = 1
    preset = tw.sh("ship", "echo parent=$PPID", irreversible=irreversible)
    cfg.presets = (parse_preset(preset),)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        if irreversible:
            store.irreversible = {"ship": ("irreversible:ship", "global")}
        jid = tw.enqueue(store, cfg, "ship")
        tw.run_one(store, cfg, jid)
        assert store.get_job(jid).state == SUCCEEDED
        log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
        parent = int(log.split("parent=")[1].split()[0])
        assert (parent != os.getpid()) is irreversible
    finally:
        store.close()
