"""저장소(M5b-2) — 스키마 v5(`tokens.kind` · `jobs.worker_name` · `workers`) · 4→5 마이그레이션 ·
`add_token(kind=)` · `verify_token`/`list_tokens` 의 kind · `register_worker`/`touch_worker`/
`get_worker`/`list_workers` · `claim(..., worker_name=)` + `LaneBusy` · `jobs_of_worker` ·
`mark_lost_for_worker` · `recover_on_start` 는 로컬 잡만.
명세는 docs/m5b2-workplan.md §1(토큰) · §2(등록) · §3 「서버 재시작」 · §6 「모델」·「저장소」.
구현 전이라 빨간 것이 정상이다.

시각은 jobfactory 의 고정 NOW 기준으로 직접 찍는다(스레드 없음 · sleep 없음). 워커는 `claim`
전에 `register_worker` 로 등록해 둔다 — 명세는 저장소 `claim` 에 등록 검사를 요구하지 않지만
서버는 늘 등록된 워커로만 부르므로 그 모양을 따른다.
"""

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jobfactory import NOW, job
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    DEFAULT_POOL,
    FAILED,
    LOST,
    PHASE_MATERIALIZING,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TOKEN_KINDS,
    UPLOADING,
    Job,
    Requester,
    Source,
    Transition,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import DB_VERSION, LaneBusy, Store, TokenInfo, WorkerRow

ALICE = Requester(name="alice-laptop", label="alice@laptop")
HIGH, NORMAL = 1, 0
LINUX = "linux"


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.db")
    yield s
    s.close()


def enqueue(
    store: Store,
    *,
    key: str = "gate:full",
    inputs: dict[str, Any] | None = None,
    group: str | None = None,
    state: str = QUEUED,
    tree: str = "9f8e",
    now: datetime = NOW,
    req: Requester = ALICE,
    priority: int = NORMAL,
    pool: str = DEFAULT_POOL,
) -> Job:
    inputs = inputs if inputs is not None else {"scope": "full"}
    src = Source(
        mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree, bytes=None
    )
    preset = key.split(":")[0]
    return store.create_job(
        preset=preset,
        inputs=inputs,
        key=key,
        concurrency_group=group,
        source=src,
        requester=req,
        timeout_seconds=1200,
        join_key=join_key(preset, inputs, tree),
        now=now,
        state=state,
        priority=priority,
        pool=pool,
    )


def register(
    store: Store,
    name: str = "build-02",
    *,
    pool: str = LINUX,
    lanes: int = 2,
    now: datetime = NOW,
    host_name: str | None = "build-02.local",
    version: str | None = "0.2.0",
) -> WorkerRow:
    return store.register_worker(
        name, pool=pool, lanes=lanes, host_name=host_name, version=version, now=now
    )


def remote_running(
    store: Store, worker: str, *, lane: int, tree: str, t: float, pool: str = LINUX
) -> Job:
    """그 풀에 잡을 넣고(at(t)) 그 워커의 레인으로 바로 claim 한다(at(t + 0.5)).

    그 풀에 다른 queued 잡이 없을 때 부른다(claim 이 이 잡을 잡아야 한다).
    """
    j = enqueue(store, tree=tree, pool=pool, now=at(t))
    got = store.claim(lane, at(t + 0.5), pool=pool, worker_name=worker)
    assert got is not None and got.id == j.id
    return got


def columns(path: Path, table: str) -> dict[str, sqlite3.Row]:
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        return {r["name"]: r for r in c.execute(f"PRAGMA table_info({table})")}


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def token_rows(path: Path) -> list[tuple[str, str, int]]:
    with sqlite3.connect(path) as c:
        return c.execute("SELECT name, kind, admin FROM tokens ORDER BY created_at").fetchall()


# ── 모델 이름 (§6) ────────────────────────────────────────────────────────────


def test_model_fixes_token_kinds_and_worker_name_defaults():
    """§6 모델: `TOKEN_KINDS` 의 값과 순서 · `Job.worker_name` · `WorkerInfo.worker` ·
    `TokenInfo.kind` 는 전부 기본값이 있어 기존 호출 모양이 그대로 돈다."""
    assert TOKEN_KINDS == ("client", "admin", "worker")
    j = job(1)
    assert j.worker_name is None
    assert replace(j, worker_name="build-02").worker_name == "build-02"
    assert WorkerInfo(lane=1, state="idle").worker is None
    assert WorkerInfo(lane=1, state="busy", job_id=7, worker="build-02").worker == "build-02"
    info = TokenInfo(name="alice-laptop", admin=False, created_at=NOW)
    assert info.kind == "client"


# ── 스키마 v5 ─────────────────────────────────────────────────────────────────


def test_fresh_db_is_schema_v5_with_token_kind_worker_name_and_workers_table(store, tmp_path):
    """§1 · §2: 새 DB 는 v5 — `tokens.kind TEXT NOT NULL DEFAULT 'client'`(admin 열은 유지) ·
    `jobs.worker_name TEXT`(NULL 허용) · `workers` 표의 열·PK·NOT NULL 이 명세 그대로."""
    path = tmp_path / "rcm.db"
    assert DB_VERSION == 5 and store.user_version() == 5
    tok = columns(path, "tokens")
    assert tok["kind"]["type"].upper() == "TEXT" and tok["kind"]["notnull"] == 1
    assert str(tok["kind"]["dflt_value"]).strip("'\"") == "client"
    assert "admin" in tok  # 기존 열은 남긴다(0/1) — kind 가 정본일 뿐
    jobs = columns(path, "jobs")
    assert jobs["worker_name"]["type"].upper() == "TEXT" and jobs["worker_name"]["notnull"] == 0
    assert "workers" in table_names(path)
    w = columns(path, "workers")
    assert set(w) == {
        "name",
        "pool",
        "lanes",
        "host_name",
        "version",
        "registered_at",
        "last_seen_at",
    }
    assert w["name"]["pk"] == 1 and w["name"]["type"].upper() == "TEXT"
    assert w["pool"]["notnull"] == 1 and w["pool"]["type"].upper() == "TEXT"
    assert w["lanes"]["notnull"] == 1 and w["lanes"]["type"].upper() == "INTEGER"
    assert w["host_name"]["notnull"] == 0 and w["version"]["notnull"] == 0
    for col in ("registered_at", "last_seen_at"):
        assert w[col]["notnull"] == 1 and w[col]["type"].upper() == "REAL"
    assert store.list_workers() == []


def test_migration_v4_to_v5_fills_kind_from_admin_and_adds_worker_name_and_workers(tmp_path):
    """§1: v4 → v5 는 `admin = 1` 인 토큰을 kind admin, 나머지를 client 로 채우고, 옛 잡은
    `worker_name` NULL(로컬)로 읽히며, `workers` 표가 생긴다. 두 번째 열기는 아무것도 안 바꾼다."""
    path = tmp_path / "rcm.db"
    s = Store(path)
    client_secret = s.add_token("alice-laptop", admin=False, now=NOW)
    admin_secret = s.add_token("macmini-admin", admin=True, now=at(1))
    running = enqueue(s)
    assert s.claim(1, at(2)).id == running.id  # 활성(running) 행과 queued 행 둘 다 남긴다
    queued = enqueue(s, tree="q", pool=LINUX, now=at(3))
    s.close()
    # v4 데이터베이스를 흉내 낸다: 새 열(과 그 인덱스)·새 표를 떼고 user_version 을 4 로 되돌린다
    c = sqlite3.connect(path)
    try:
        for (name,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND ("
            "(tbl_name='jobs' AND sql LIKE '%worker_name%') OR "
            "(tbl_name='tokens' AND sql LIKE '%kind%'))"
        ).fetchall():
            c.execute(f"DROP INDEX {name}")
        c.execute("ALTER TABLE tokens DROP COLUMN kind")
        c.execute("ALTER TABLE jobs DROP COLUMN worker_name")
        c.execute("DROP TABLE workers")
        c.execute("PRAGMA user_version=4")
        c.commit()
        assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        c.close()
    assert "kind" not in columns(path, "tokens") and "worker_name" not in columns(path, "jobs")
    assert "workers" not in table_names(path)
    s2 = Store(path)  # 4 → 5 마이그레이션이 여기서 돈다
    try:
        assert s2.user_version() == 5 and s2.healthy()
        tok = columns(path, "tokens")
        assert tok["kind"]["notnull"] == 1
        assert str(tok["kind"]["dflt_value"]).strip("'\"") == "client"
        kinds = {t.name: (t.kind, t.admin) for t in s2.list_tokens()}
        assert kinds == {"alice-laptop": ("client", False), "macmini-admin": ("admin", True)}
        assert s2.verify_token(client_secret).kind == "client"
        assert s2.verify_token(admin_secret).kind == "admin"
        assert token_rows(path) == [("alice-laptop", "client", 0), ("macmini-admin", "admin", 1)]
        got = s2.get_job(running.id)
        assert got is not None and got.state == RUNNING and got.lane == 1
        assert got.worker_name is None  # 옛 행은 전부 로컬 레인의 잡
        assert s2.get_job(queued.id).worker_name is None
        assert s2.list_workers() == []  # 새 표를 쓰는 조회가 돈다
        s2.register_worker(
            "build-02", pool=LINUX, lanes=1, host_name="b2", version="0.2.0", now=at(10)
        )
        assert [w.name for w in s2.list_workers()] == ["build-02"]
        claimed = s2.claim(1, at(11), pool=LINUX, worker_name="build-02")  # 새 열을 쓰는 claim
        assert claimed.id == queued.id and claimed.worker_name == "build-02"
    finally:
        s2.close()
    s3 = Store(path)  # 두 번째 열기는 아무것도 바꾸지 않는다
    assert s3.user_version() == 5
    assert s3.verify_token(admin_secret).kind == "admin"
    assert s3.get_job(queued.id).worker_name == "build-02"
    s3.close()


# ── 토큰 종류 (§1 · §6) ──────────────────────────────────────────────────────


def test_add_token_derives_kind_from_admin_when_kind_is_omitted(store, tmp_path):
    """§6: `add_token(name, *, admin=False, now, kind=None)` — kind 를 안 주면 admin 으로 정한다
    (False → client · True → admin). `admin` 은 이제 선택 인자다."""
    plain = store.add_token("alice-laptop", now=NOW)  # admin 기본값 False
    client = store.add_token("bob-desk", admin=False, now=at(1))
    admin = store.add_token("macmini-admin", admin=True, now=at(2))
    assert len(plain) >= 32 and len({plain, client, admin}) == 3
    p = store.verify_token(plain)
    assert p.name == "alice-laptop" and p.kind == "client" and p.admin is False
    assert store.verify_token(client).kind == "client"
    a = store.verify_token(admin)
    assert a.name == "macmini-admin" and a.kind == "admin" and a.admin is True
    listed = [(t.name, t.kind, t.admin) for t in store.list_tokens()]
    assert listed == [
        ("alice-laptop", "client", False),
        ("bob-desk", "client", False),
        ("macmini-admin", "admin", True),
    ]
    assert token_rows(tmp_path / "rcm.db") == [
        ("alice-laptop", "client", 0),
        ("bob-desk", "client", 0),
        ("macmini-admin", "admin", 1),
    ]


def test_add_token_kind_worker_is_never_admin_and_kind_admin_sets_the_flag(store, tmp_path):
    """§1: `kind` 가 정본 — worker 토큰은 admin False, `kind="admin"` 이면 admin 열도 1 이다.
    `admin` 불리언은 언제나 `kind == "admin"` 과 같다. 폐기는 종류와 무관하게 동작한다."""
    worker = store.add_token("build-02", kind="worker", now=NOW)
    admin = store.add_token("ops", kind="admin", now=at(1))
    client = store.add_token("carol-x", kind="client", now=at(2))
    w = store.verify_token(worker)
    assert w.name == "build-02" and w.kind == "worker" and w.admin is False
    a = store.verify_token(admin)
    assert a.kind == "admin" and a.admin is True
    c = store.verify_token(client)
    assert c.kind == "client" and c.admin is False
    assert token_rows(tmp_path / "rcm.db") == [
        ("build-02", "worker", 0),
        ("ops", "admin", 1),
        ("carol-x", "client", 0),
    ]
    tokens = store.list_tokens()
    assert [t.kind for t in tokens] == ["worker", "admin", "client"]
    assert all(t.kind in TOKEN_KINDS and t.admin is (t.kind == "admin") for t in tokens)
    assert store.revoke_token("build-02", at(3)) is True
    assert store.verify_token(worker) is None
    revoked = {t.name: (t.kind, t.revoked_at) for t in store.list_tokens()}
    assert revoked["build-02"] == ("worker", at(3))  # 폐기돼도 종류는 보인다
    with pytest.raises(Exception):  # noqa: B017 — 이름 중복은 기존 규칙(StoreError)대로
        store.add_token("build-02", kind="worker", now=at(4))


def test_add_token_rejects_unknown_kind_without_inserting(store):
    """§1: kind 는 client·admin·worker 뿐 — 그 밖의 값은 `ValueError` 이고 행을 남기지 않는다
    (같은 이름을 곧바로 다시 쓸 수 있다)."""
    for bad in ("root", "", "clients"):
        with pytest.raises(ValueError):
            store.add_token("build-02", kind=bad, now=NOW)
    assert store.list_tokens() == []
    secret = store.add_token("build-02", kind="worker", now=at(1))  # 이름이 소모되지 않았다
    assert store.verify_token(secret).kind == "worker"


# ── 워커 등록 (§2 · §6) ──────────────────────────────────────────────────────


def test_register_worker_stores_the_row_and_stamps_registered_and_last_seen(store):
    """§2 · §6: `register_worker` 는 `WorkerRow` 를 돌려주고 `registered_at`·`last_seen_at` 을
    서버 시각 now 로 찍는다. `get_worker` 는 같은 행, 모르는 이름은 None.
    host_name·version 은 없어도 된다."""
    row = store.register_worker(
        "build-02", pool=LINUX, lanes=2, host_name="build-02.local", version="0.2.0", now=NOW
    )
    assert isinstance(row, WorkerRow)
    assert row == WorkerRow(
        name="build-02",
        pool=LINUX,
        lanes=2,
        host_name="build-02.local",
        version="0.2.0",
        registered_at=NOW,
        last_seen_at=NOW,
    )
    assert row.registered_at.tzinfo is not None and row.last_seen_at.tzinfo is not None
    assert store.get_worker("build-02") == row
    assert store.list_workers() == [row]
    assert store.get_worker("build-03") is None
    bare = store.register_worker(
        "build-03", pool=DEFAULT_POOL, lanes=1, host_name=None, version=None, now=at(1)
    )
    assert bare.host_name is None and bare.version is None and bare.pool == DEFAULT_POOL
    assert bare.lanes == 1 and isinstance(bare.lanes, int)
    assert store.get_worker("build-03") == bare


def test_register_worker_again_updates_pool_lanes_version_and_last_seen(store):
    """§2: 이미 있으면 갱신(upsert) — 풀 바꾸기 허용, lanes·host_name·version 도 새 값,
    `last_seen_at = now`. 행은 여전히 하나다."""
    first = register(store, now=NOW)
    again = store.register_worker(
        "build-02", pool="mac2", lanes=4, host_name="b2.lan", version="0.2.1", now=at(30)
    )
    assert (again.name, again.pool, again.lanes) == ("build-02", "mac2", 4)
    assert (again.host_name, again.version) == ("b2.lan", "0.2.1")
    assert again.last_seen_at == at(30)
    assert store.get_worker("build-02") == again
    assert [w.name for w in store.list_workers()] == ["build-02"]
    assert first.pool == LINUX and first.lanes == 2  # 먼저 받은 행은 불변 스냅샷


def test_list_workers_is_sorted_by_name_not_by_registration_order(store):
    """§6: `list_workers()` 는 이름순 — 등록 순서·풀·last_seen 과 무관하다."""
    for name, t in (("zeta", 0), ("alpha", 1), ("mid", 2)):
        register(store, name, pool=LINUX if t else DEFAULT_POOL, now=at(t))
    assert [w.name for w in store.list_workers()] == ["alpha", "mid", "zeta"]
    assert all(isinstance(w, WorkerRow) for w in store.list_workers())
    assert store.touch_worker("zeta", at(50)) is True  # 최근에 봤어도 순서는 이름
    assert [w.name for w in store.list_workers()] == ["alpha", "mid", "zeta"]
    assert store.get_worker("nope") is None


def test_touch_worker_updates_last_seen_only_and_is_false_for_unknown(store):
    """§2 · §6: heartbeat 은 `touch_worker(name, now)` — `last_seen_at` 만 서버 시각으로 바꾼다.
    `registered_at`·풀·레인은 그대로. 모르는 워커는 False 이고 행을 만들지 않는다."""
    row = register(store, now=NOW)
    assert store.touch_worker("build-02", at(5)) is True
    seen = store.get_worker("build-02")
    assert seen.last_seen_at == at(5)
    assert seen == replace(row, last_seen_at=at(5))
    assert store.touch_worker("build-02", at(9)) is True
    assert store.get_worker("build-02").last_seen_at == at(9)
    assert store.get_worker("build-02").registered_at == NOW
    assert store.touch_worker("ghost", at(10)) is False
    assert [w.name for w in store.list_workers()] == ["build-02"]


# ── claim(worker_name=) · LaneBusy (§3 · §6) ─────────────────────────────────


def test_claim_with_worker_name_records_the_worker_and_lane(store):
    """§6: `claim(lane, now, pool=, worker_name=)` 은 `Job.worker_name`·`Job.lane` 을 남긴다.
    로컬 claim(worker_name 없음)은 NULL. 종료 뒤에도 worker_name 은 남는다(누가 돌렸는지)."""
    register(store, "build-02", lanes=2)
    lin = enqueue(store, pool=LINUX)
    got = store.claim(2, at(1), pool=LINUX, worker_name="build-02")
    assert got.id == lin.id and got.state == RUNNING
    assert got.worker_name == "build-02" and got.lane == 2 and got.pool == LINUX
    assert got.started_at == at(1) and got.phase == PHASE_MATERIALIZING
    assert store.get_job(lin.id).worker_name == "build-02"
    assert [j.worker_name for j in store.list_active()] == ["build-02"]
    local = enqueue(store, tree="l", now=at(2))
    mine = store.claim(1, at(3))  # 로컬 레인은 worker_name 없이
    assert mine.id == local.id and mine.worker_name is None and mine.lane == 1
    assert store.claim(1, at(4), pool=DEFAULT_POOL, worker_name=None) is None  # 명시적 None 도 같다
    store.finish(lin.id, SUCCEEDED, now=at(10), exit_code=0)
    done = store.get_job(lin.id)
    assert done.state == SUCCEEDED and done.worker_name == "build-02" and done.lane is None
    assert store.list_recent(5)[0].worker_name == "build-02"


def test_claim_with_worker_name_takes_only_jobs_of_the_workers_pool(store):
    """§3: 워커는 등록된 풀의 queued 잡만 받는다. 기본 풀도 원격 워커(`pool = default`)가
    받을 수 있고, 그러면 로컬 레인에는 남는 게 없다."""
    register(store, "build-02", pool=LINUX)
    register(store, "build-01", pool=DEFAULT_POOL, lanes=1)
    lin = enqueue(store, pool=LINUX, now=at(0))
    plain = enqueue(store, tree="p", now=at(1))
    assert store.claim(1, at(2), pool="mac2", worker_name="build-09") is None  # 잡 없는 풀
    assert store.claim(1, at(3), pool=LINUX, worker_name="build-02").id == lin.id
    assert (
        store.claim(2, at(4), pool=LINUX, worker_name="build-02") is None
    )  # 기본 풀 잡은 안 잡는다
    assert store.get_job(plain.id).state == QUEUED
    got = store.claim(1, at(5), pool=DEFAULT_POOL, worker_name="build-01")
    assert got.id == plain.id and got.worker_name == "build-01" and got.pool == DEFAULT_POOL
    assert store.claim(1, at(6)) is None  # 로컬 레인에는 남은 게 없다


def test_claim_with_worker_name_keeps_priority_order_and_group_exclusion_in_the_pool(store):
    """§3: 원격 claim 도 `(-priority, id)` 순서와 풀 안의 그룹 배제를 그대로 따른다 — 다른 워커가
    돌리는 같은 풀의 `devices` 는 막고, 기본 풀의 `devices` 는 상관없다."""
    register(store, "build-02", pool=LINUX, lanes=3)
    register(store, "build-03", pool=LINUX, lanes=2, now=at(0))
    older = enqueue(store, key="qa", group="devices", pool=LINUX, tree="o", now=at(0))
    high = enqueue(store, tree="h", pool=LINUX, priority=HIGH, now=at(1))
    plain_high = enqueue(store, tree="d", priority=HIGH, now=at(2))  # 기본 풀의 high 는 무관
    assert store.claim(1, at(3), pool=LINUX, worker_name="build-02").id == high.id
    assert store.claim(2, at(4), pool=LINUX, worker_name="build-02").id == older.id
    blocked = enqueue(
        store, key="qa", group="devices", pool=LINUX, tree="b", priority=HIGH, now=at(5)
    )
    plain = enqueue(
        store, key="gate:fast", inputs={"scope": "fast"}, pool=LINUX, tree="p", now=at(6)
    )
    assert store.claim(1, at(7), pool=LINUX, worker_name="build-03").id == plain.id
    assert store.claim(2, at(8), pool=LINUX, worker_name="build-03") is None
    assert store.get_job(blocked.id).state == QUEUED
    d = enqueue(store, key="qa", group="devices", tree="dd", now=at(9))
    assert store.claim(1, at(10)).id == plain_high.id  # 기본 풀은 자기 순서대로
    assert store.claim(2, at(11)).id == d.id  # 리눅스 devices 가 돌아도 기본 풀 devices 는 된다
    store.finish(older.id, SUCCEEDED, now=at(12), exit_code=0)
    assert store.claim(2, at(13), pool=LINUX, worker_name="build-03").id == blocked.id


def test_claim_raises_lane_busy_while_that_worker_lane_has_an_active_job(store):
    """§3 · §6(리뷰 반영 — 레인 과할당 금지): 그 `(worker_name, lane)` 에 running·cancelling 잡이
    있으면 `LaneBusy` 이고 아무것도 바꾸지 않는다. 같은 워커의 다른 레인, 다른 워커의 같은
    레인 번호는 된다. 종료되면 그 레인으로 다시 잡는다. 잡을 게 없어도 레인이 바쁘면 LaneBusy."""
    register(store, "build-02", pool=LINUX, lanes=2)
    register(store, "build-03", pool=LINUX, lanes=1, now=at(0))
    first = enqueue(store, pool=LINUX, now=at(0))
    second = enqueue(store, tree="s", pool=LINUX, now=at(1))
    assert store.claim(1, at(2), pool=LINUX, worker_name="build-02").id == first.id
    with pytest.raises(LaneBusy):
        store.claim(1, at(3), pool=LINUX, worker_name="build-02")
    waiting = store.get_job(second.id)
    assert waiting.state == QUEUED and waiting.worker_name is None and waiting.lane is None
    assert store.get_job(first.id).state == RUNNING and store.get_job(first.id).lane == 1
    assert store.claim(2, at(4), pool=LINUX, worker_name="build-02").id == second.id
    third = enqueue(store, tree="t", pool=LINUX, now=at(5))
    assert store.claim(1, at(6), pool=LINUX, worker_name="build-03").id == third.id
    assert store.request_cancel(first.id, "alice-laptop", at(7), 10) == CANCELLING
    fourth = enqueue(store, tree="u", pool=LINUX, now=at(8))
    with pytest.raises(LaneBusy):  # cancelling 도 활성 — 레인은 여전히 바쁘다
        store.claim(1, at(9), pool=LINUX, worker_name="build-02")
    assert store.get_job(fourth.id).state == QUEUED
    store.finish(first.id, CANCELLED, now=at(10), exit_code=-15)
    got = store.claim(1, at(11), pool=LINUX, worker_name="build-02")
    assert got.id == fourth.id and got.lane == 1 and got.worker_name == "build-02"
    assert store.list_jobs_by_state([QUEUED]) == []
    with pytest.raises(LaneBusy):  # 큐가 비어도 None 이 아니라 LaneBusy
        store.claim(1, at(12), pool=LINUX, worker_name="build-02")
    assert isinstance(LaneBusy("lane 1 already has job #1"), Exception)


def test_lane_busy_is_per_worker_so_local_and_remote_lane_numbers_never_collide(store):
    """§6: 레인 배타는 `(worker_name, lane)` 단위 — 원격 `build-01/1` 이 바빠도 로컬 레인 1 은
    잡고, 로컬 레인 1 이 바빠도 원격 워커의 레인 1 은 잡는다(로컬 claim 은 worker_name None)."""
    register(store, "build-01", pool=DEFAULT_POOL, lanes=1)
    a = enqueue(store, now=at(0))
    b = enqueue(store, tree="b", now=at(1))
    c = enqueue(store, tree="c", now=at(2))
    remote = store.claim(1, at(3), pool=DEFAULT_POOL, worker_name="build-01")
    assert remote.id == a.id and remote.lane == 1 and remote.worker_name == "build-01"
    local = store.claim(1, at(4))
    assert local.id == b.id and local.lane == 1 and local.worker_name is None
    store.finish(a.id, SUCCEEDED, now=at(5), exit_code=0)
    again = store.claim(1, at(6), pool=DEFAULT_POOL, worker_name="build-01")
    assert again.id == c.id and again.lane == 1
    assert store.get_job(b.id).state == RUNNING and store.get_job(b.id).lane == 1


# ── jobs_of_worker · mark_lost_for_worker (§3 · §4 · §6) ─────────────────────


def test_jobs_of_worker_lists_running_and_cancelling_jobs_of_that_worker_only(store):
    """§6: `jobs_of_worker(name)` 은 그 워커의 running·cancelling 잡(id 순)만 — 종료 잡·queued ·
    다른 워커·로컬 잡은 빠진다. 온전한 `Job`(취소 정보 포함)을 돌려준다."""
    register(store, "build-02", pool=LINUX, lanes=3)
    register(store, "build-03", pool=LINUX, lanes=1, now=at(0))
    assert store.jobs_of_worker("build-02") == []
    a = remote_running(store, "build-02", lane=1, tree="a", t=1)
    b = remote_running(store, "build-02", lane=2, tree="b", t=2)
    c = remote_running(store, "build-02", lane=3, tree="c", t=3)
    d = remote_running(store, "build-03", lane=1, tree="d", t=4)
    local = enqueue(store, tree="l", now=at(5))
    assert store.claim(1, at(6)).id == local.id
    assert store.request_cancel(b.id, "alice-laptop", at(7), 10) == CANCELLING
    store.finish(c.id, SUCCEEDED, now=at(8), exit_code=0)
    enqueue(store, tree="q", pool=LINUX, now=at(9))  # queued 는 아직 누구 것도 아니다
    got = store.jobs_of_worker("build-02")
    assert [(j.id, j.state, j.lane) for j in got] == [(a.id, RUNNING, 1), (b.id, CANCELLING, 2)]
    assert all(isinstance(j, Job) and j.worker_name == "build-02" for j in got)
    assert got[1].cancel is not None and got[1].cancel.kill_at == at(17)
    assert [j.id for j in store.jobs_of_worker("build-03")] == [d.id]
    assert store.jobs_of_worker("ghost") == []
    store.finish(a.id, FAILED, now=at(10), exit_code=1)
    assert [j.id for j in store.jobs_of_worker("build-02")] == [b.id]


def test_mark_lost_for_worker_closes_only_that_workers_active_jobs(store):
    """§4 · §6: `mark_lost_for_worker(name, now, summary)` 는 그 워커의 running·cancelling 잡을
    lost 로 닫고(summary · finished_at · 전이 이벤트) id 목록을 돌려준다. 다른 워커·로컬·queued
    잡은 그대로. 두 번째 호출·모르는 워커는 []. 레인이 비었으니 그 워커는 다시 claim 할 수 있다."""
    register(store, "build-02", pool=LINUX, lanes=2)
    register(store, "build-03", pool=LINUX, lanes=1, now=at(0))
    a = remote_running(store, "build-02", lane=1, tree="a", t=1)
    b = remote_running(store, "build-02", lane=2, tree="b", t=2)
    d = remote_running(store, "build-03", lane=1, tree="d", t=3)
    local = enqueue(store, tree="l", now=at(4))
    assert store.claim(1, at(5)).id == local.id
    queued = enqueue(store, tree="q", pool=LINUX, now=at(6))
    assert store.request_cancel(b.id, "alice-laptop", at(7), 10) == CANCELLING
    summary = "worker build-02 unreachable for 61s"
    assert store.mark_lost_for_worker("build-02", at(100), summary) == [a.id, b.id]
    for jid in (a.id, b.id):
        j = store.get_job(jid)
        assert j.state == LOST and j.summary == summary and j.finished_at == at(100)
        assert j.lane is None and j.phase is None and j.worker_name == "build-02"
        assert j.transitions[-1] == Transition(LOST, at(100))
        assert j.is_terminal
    assert [t.state for t in store.get_job(b.id).transitions] == [QUEUED, RUNNING, CANCELLING, LOST]
    assert store.get_job(d.id).state == RUNNING and store.get_job(d.id).worker_name == "build-03"
    assert store.get_job(local.id).state == RUNNING and store.get_job(local.id).lane == 1
    assert store.get_job(queued.id).state == QUEUED
    assert store.jobs_of_worker("build-02") == []
    assert store.mark_lost_for_worker("build-02", at(101), summary) == []
    assert store.mark_lost_for_worker("ghost", at(102), summary) == []
    assert store.get_job(a.id).finished_at == at(100)  # 다시 불러도 안 바뀐다
    assert [j.id for j in store.list_recent(5)] == [b.id, a.id]
    again = store.claim(1, at(103), pool=LINUX, worker_name="build-02")
    assert again.id == queued.id and again.lane == 1


# ── recover_on_start 는 로컬 잡만 (§3 「서버 재시작」 · 리뷰 must-fix) ─────────


def test_recover_on_start_marks_only_local_jobs_lost_and_keeps_remote_ones(tmp_path):
    """§3: 서버 재시작은 `worker_name IS NULL` 인 running·cancelling 만 lost 로. 원격 워커의 잡과
    `workers.last_seen_at` 은 한 글자도 안 바뀌고, timeout 뒤 `mark_lost_for_worker` 가 닫는다."""
    path = tmp_path / "rcm.db"
    s = Store(path)
    register(s, "build-02", pool=LINUX, lanes=2)
    local_running = enqueue(s, now=at(0))
    assert s.claim(1, at(1)).id == local_running.id
    local_cancelling = enqueue(s, key="gate:fast", inputs={"scope": "fast"}, now=at(2))
    assert s.claim(2, at(3)).id == local_cancelling.id
    assert s.request_cancel(local_cancelling.id, "alice-laptop", at(4), 10) == CANCELLING
    remote_run = remote_running(s, "build-02", lane=1, tree="r", t=5)
    remote_can = remote_running(s, "build-02", lane=2, tree="rc", t=6)
    assert s.request_cancel(remote_can.id, "alice-laptop", at(7), 10) == CANCELLING
    remote_can = s.get_job(remote_can.id)
    s.close()

    s2 = Store(path)  # 서버를 죽였다 살렸다
    lost, cancelled = s2.recover_on_start(at(60))
    assert lost == [local_running.id, local_cancelling.id] and cancelled == []
    for jid in lost:
        j = s2.get_job(jid)
        assert j.state == LOST and j.finished_at == at(60) and j.worker_name is None
        assert j.summary.startswith("server restarted 2026-09-04")
    assert s2.get_job(remote_run.id) == remote_run  # 원격 running 은 그대로
    assert s2.get_job(remote_can.id) == remote_can and remote_can.state == CANCELLING
    assert s2.get_job(remote_run.id).lane == 1 and s2.get_job(remote_run.id).started_at == at(5.5)
    assert s2.get_worker("build-02").last_seen_at == NOW  # workers 표도 그대로 — 곧 timeout 판정
    assert [j.id for j in s2.jobs_of_worker("build-02")] == [remote_run.id, remote_can.id]
    assert s2.recover_on_start(at(61)) == ([], [])  # 두 번째 시작은 정리할 게 없다
    summary = "worker build-02 unreachable for 61s"
    assert s2.mark_lost_for_worker("build-02", at(120), summary) == [remote_run.id, remote_can.id]
    assert s2.get_job(remote_run.id).state == LOST and s2.get_job(remote_can.id).state == LOST
    assert s2.get_job(remote_run.id).summary == summary
    s2.close()


def test_recover_on_start_still_cancels_uploads_and_keeps_queued_jobs_in_every_pool(tmp_path):
    """§3: 업로드 중이던 잡은 풀과 무관하게 `cancelled`(기존 규칙), queued 는 어느 풀이든 남고,
    원격 running 잡은 건드리지 않는다."""
    path = tmp_path / "rcm.db"
    s = Store(path)
    register(s, "build-02", pool=LINUX, lanes=1)
    remote = remote_running(s, "build-02", lane=1, tree="r", t=0)  # 리눅스 큐가 비었을 때 먼저
    up_local = enqueue(s, state=UPLOADING, now=at(1))
    up_linux = enqueue(s, tree="ul", state=UPLOADING, pool=LINUX, now=at(2))
    q_local = enqueue(s, tree="ql", now=at(3))
    q_linux = enqueue(s, tree="qx", pool=LINUX, now=at(4))
    s.close()

    s2 = Store(path)
    assert s2.recover_on_start(at(60)) == ([], [up_local.id, up_linux.id])
    for jid in (up_local.id, up_linux.id):
        u = s2.get_job(jid)
        assert u.state == CANCELLED and u.summary == "server restarted during upload"
        assert u.cancelled_by == "server" and u.finished_at == at(60)
    assert s2.get_job(up_linux.id).pool == LINUX
    assert s2.get_job(q_local.id).state == QUEUED and s2.get_job(q_linux.id).state == QUEUED
    assert s2.get_job(remote.id) == remote and remote.state == RUNNING
    assert s2.claim(1, at(61)).id == q_local.id  # 로컬 레인은 재시작 뒤 바로 다시 돈다
    assert s2.get_job(q_linux.id).state == QUEUED  # 리눅스 잡은 그 풀의 워커 몫
    s2.close()
