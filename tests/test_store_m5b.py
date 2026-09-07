"""저장소(M5b-1) — 스키마 v4(`jobs.pool`) · 3→4 마이그레이션 · `create_job(pool=)` 왕복 ·
풀별 `claim(lane, now, pool=)` · 그룹 배제는 풀 안에서 · `list_pools()` 순서·중복 제거.
명세는 docs/m5-workplan.md 「M5b. 원격 워커」 모델 · 프로토콜(`/worker/claim`) · 순서 3(M5b-1).
구현 전이라 빨간 것이 정상이다.

시각은 고정 NOW 기준으로 직접 찍는다(스레드 없음). `list_active`/`list_recent` 는 그대로
모든 풀을 돌려준다 — 풀별로 나누는 건 순수 계층(`split_by_pool`)의 몫이다.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import DB_VERSION, Store

NOW = datetime(2026, 9, 6, 9, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
HIGH, NORMAL = 1, 0


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
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
    pool: str | None = None,
) -> Job:
    """`pool=None` 이면 인자를 아예 안 넘긴다(기본값이 기본 풀인지 본다)."""
    inputs = inputs if inputs is not None else {"scope": "full"}
    src = Source(
        mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree, bytes=None
    )
    preset = key.split(":")[0]
    kw: dict[str, Any] = {} if pool is None else {"pool": pool}
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
        **kw,
    )


def finished_job(
    store: Store,
    *,
    pool: str,
    finished: datetime,
    tree: str,
    created: datetime = NOW,
    state: str = SUCCEEDED,
) -> Job:
    """enqueue → 그 풀에서 claim → finish. 그 풀에 다른 queued 잡이 없을 때 부른다."""
    j = enqueue(store, tree=tree, now=created, pool=pool)
    claimed = store.claim(1, created + timedelta(seconds=1), pool=pool)
    assert claimed is not None and claimed.id == j.id
    store.finish(j.id, state, now=finished, exit_code=0 if state == SUCCEEDED else 1)
    got = store.get_job(j.id)
    assert got is not None
    return got


def job_columns(path: Path) -> dict[str, sqlite3.Row]:
    with sqlite3.connect(path) as c:
        c.row_factory = sqlite3.Row
        return {r["name"]: r for r in c.execute("PRAGMA table_info(jobs)")}


# ── 스키마 v4 ─────────────────────────────────────────────────────────────────


def test_fresh_db_is_schema_v4_with_a_not_null_pool_column(store, tmp_path):
    assert DB_VERSION == 5 and store.user_version() == 5
    cols = job_columns(tmp_path / "rcm.sqlite3")
    assert "pool" in cols
    assert cols["pool"]["type"].upper() == "TEXT"
    assert cols["pool"]["notnull"] == 1
    assert str(cols["pool"]["dflt_value"]).strip("'\"") == "default"


def test_migration_v3_to_v4_adds_pool_and_reads_old_rows_as_default(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = enqueue(s)
    done = enqueue(s, tree="d", now=at(1))
    assert s.claim(1, at(2)).id == j.id  # 활성(running) 행과 queued 행 둘 다 남긴다
    s.close()
    # v3 데이터베이스를 흉내 낸다: pool 열(과 그 인덱스)을 떼고 user_version 을 3 으로 되돌린다
    c = sqlite3.connect(path)
    try:
        for (name,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%pool%'"
        ).fetchall():
            c.execute(f"DROP INDEX {name}")
        # v5(M5b-2)가 더한 것도 뗀다(worker_name · tokens.kind · workers)
        c.execute("DROP INDEX IF EXISTS jobs_worker")
        c.execute("ALTER TABLE jobs DROP COLUMN worker_name")
        c.execute("ALTER TABLE tokens DROP COLUMN kind")
        c.execute("DROP TABLE IF EXISTS workers")
        c.execute("ALTER TABLE jobs DROP COLUMN pool")
        c.execute("PRAGMA user_version=3")
        c.commit()
        assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        c.close()
    assert "pool" not in job_columns(path)
    s2 = Store(path)  # 3 → 5 마이그레이션이 여기서 돈다
    try:
        assert s2.user_version() == 5 and s2.healthy()
        cols = job_columns(path)
        assert cols["pool"]["notnull"] == 1
        assert str(cols["pool"]["dflt_value"]).strip("'\"") == "default"
        got = s2.get_job(j.id)
        assert got is not None and got.state == RUNNING and got.key == "gate:full"
        assert got.pool == "default"  # 기존 행은 기본 풀
        assert s2.get_job(done.id).pool == "default"
        assert s2.list_pools() == ["default"]  # 새 열을 쓰는 조회가 돈다
        assert s2.claim(2, at(3)).id == done.id  # pool 을 쓰는 claim SQL 이 돈다
        assert s2.claim(3, at(4), pool="linux") is None
    finally:
        s2.close()
    s3 = Store(path)  # 두 번째 열기는 아무것도 바꾸지 않는다
    assert s3.user_version() == 5 and s3.get_job(j.id).pool == "default"
    s3.close()


# ── create_job(pool=) 왕복 ────────────────────────────────────────────────────


def test_create_job_stores_pool_and_defaults_to_default(store):
    plain = enqueue(store)
    lin = enqueue(store, tree="l", pool="linux", now=at(1))
    up = enqueue(store, tree="u", pool="mac2", state=UPLOADING, now=at(2))
    assert plain.pool == "default" and store.get_job(plain.id).pool == "default"
    assert lin.pool == "linux" and store.get_job(lin.id).pool == "linux"
    assert store.get_job(up.id).pool == "mac2" and store.get_job(up.id).state == UPLOADING
    assert enqueue(store, tree="e", pool="default", now=at(3)).pool == "default"


def test_list_active_and_list_recent_span_all_pools(store):
    a = enqueue(store)
    b = enqueue(store, tree="b", pool="linux", now=at(1))
    assert [(j.id, j.pool) for j in store.list_active()] == [(a.id, "default"), (b.id, "linux")]
    assert store.claim(1, at(2)).id == a.id
    assert store.claim(1, at(3), pool="linux").id == b.id
    store.finish(a.id, SUCCEEDED, now=at(10), exit_code=0)
    store.finish(b.id, SUCCEEDED, now=at(11), exit_code=0)
    assert [(j.id, j.pool) for j in store.list_recent(5)] == [(b.id, "linux"), (a.id, "default")]
    assert [(j.id, j.pool) for j in store.list_samples(at(0))] == [
        (a.id, "default"),
        (b.id, "linux"),
    ]
    assert store.list_active() == []


def test_pool_survives_claim_cancel_and_finish(store):
    lin = enqueue(store, tree="l", pool="linux")
    running = store.claim(1, at(1), pool="linux")
    assert running.id == lin.id and running.pool == "linux" and running.state == RUNNING
    assert store.request_cancel(lin.id, "alice-laptop", at(2), 10) == "cancelling"
    assert store.get_job(lin.id).pool == "linux"
    store.finish(lin.id, CANCELLED, now=at(12), exit_code=-15)
    got = store.get_job(lin.id)
    assert got.state == CANCELLED and got.pool == "linux"


# ── 풀별 claim ────────────────────────────────────────────────────────────────


def test_claim_takes_only_jobs_of_the_requested_pool(store):
    lin = enqueue(store, tree="l", pool="linux", now=at(0))  # 더 오래된 리눅스 잡
    plain = enqueue(store, tree="p", now=at(1))
    assert store.claim(1, at(5)).id == plain.id  # 기본 풀: 더 오래된 리눅스 잡을 건너뛴다
    assert store.claim(2, at(6)) is None
    assert store.claim(2, at(7), pool="default") is None  # pool="default" 는 생략과 같다
    assert store.get_job(lin.id).state == QUEUED
    got = store.claim(1, at(8), pool="linux")
    assert got.id == lin.id and got.state == RUNNING and got.lane == 1 and got.pool == "linux"
    assert store.claim(1, at(9), pool="linux") is None
    assert store.claim(1, at(10), pool="mac2") is None  # 아무도 모르는 풀은 그냥 비어 있다


def test_claim_keeps_priority_order_within_the_pool(store):
    older = enqueue(store, tree="o", pool="linux", now=at(0))
    high = enqueue(store, tree="h", pool="linux", priority=HIGH, now=at(1))
    enqueue(store, tree="d", priority=HIGH, now=at(2))  # 기본 풀의 high 는 리눅스 순서와 무관
    assert store.claim(1, at(3), pool="linux").id == high.id
    assert store.claim(2, at(4), pool="linux").id == older.id
    assert store.claim(3, at(5), pool="linux") is None


def test_group_exclusion_is_per_pool(store):
    """그룹은 풀 단위 자원 — 기본 풀에서 도는 `devices` 는 리눅스 풀의 `devices` 를 막지 않는다."""
    busy = enqueue(store, key="qa", group="devices")  # 기본 풀
    assert store.claim(1, at(1)).id == busy.id
    lin1 = enqueue(store, key="qa", group="devices", pool="linux", tree="l1", now=at(2))
    lin2 = enqueue(store, key="qa", group="devices", pool="linux", tree="l2", now=at(3))
    assert store.claim(1, at(4), pool="linux").id == lin1.id  # 기본 풀의 busy 는 상관없다
    assert store.claim(2, at(5), pool="linux") is None  # 리눅스 풀 안에서는 여전히 하나만
    plain = enqueue(
        store, key="gate:fast", inputs={"scope": "fast"}, pool="linux", tree="p", now=at(6)
    )
    assert store.claim(2, at(7), pool="linux").id == plain.id  # 그룹 없는 잡은 건너뛰어 잡는다
    store.finish(lin1.id, SUCCEEDED, now=at(8), exit_code=0)
    assert store.claim(1, at(9), pool="linux").id == lin2.id
    # 반대 방향: 리눅스에서 도는 devices(lin2) 는 기본 풀의 devices 를 막지 않는다
    store.finish(busy.id, SUCCEEDED, now=at(10), exit_code=0)
    again = enqueue(store, key="qa", group="devices", tree="d2", now=at(11))
    assert store.get_job(lin2.id).state == RUNNING
    assert store.claim(1, at(12)).id == again.id


def test_group_exclusion_still_holds_inside_the_default_pool(store):
    """회귀: 풀 조건을 더해도 기본 풀 안의 그룹 배제는 그대로다."""
    first = enqueue(store, key="qa", group="devices")
    second = enqueue(store, key="qa", group="devices", tree="s", now=at(1))
    assert store.claim(1, at(2)).id == first.id
    assert store.claim(2, at(3)) is None
    store.finish(first.id, SUCCEEDED, now=at(4), exit_code=0)
    assert store.claim(1, at(5)).id == second.id


# ── list_pools ────────────────────────────────────────────────────────────────


def test_list_pools_puts_default_first_then_sorted_and_dedupes(store):
    assert store.list_pools() == ["default"]  # 빈 DB 에도 기본 풀은 있다
    enqueue(store, tree="m", pool="mac2")
    enqueue(store, tree="l1", pool="linux", now=at(1))
    enqueue(store, tree="l2", pool="linux", now=at(2))  # 같은 풀은 한 번만
    assert store.list_pools() == ["default", "linux", "mac2"]  # 기본 풀에 잡이 없어도 먼저
    enqueue(store, tree="d", now=at(3))
    enqueue(store, tree="a", pool="arm", state=UPLOADING, now=at(4))  # uploading 도 활성
    assert store.list_pools() == ["default", "arm", "linux", "mac2"]


def test_list_pools_includes_pools_that_only_have_finished_jobs(store):
    done = finished_job(store, pool="linux", finished=at(10), tree="l")
    assert done.state == SUCCEEDED and store.list_active() == []
    assert store.list_pools() == ["default", "linux"]  # 최근 완료에 남은 풀도 보인다
    m = enqueue(store, tree="m", pool="mac2", now=at(20))
    assert store.request_cancel(m.id, "alice-laptop", at(21), 10) == CANCELLED
    assert store.list_pools() == ["default", "linux", "mac2"]
    assert store.get_job(m.id).pool == "mac2"


def test_list_pools_returns_plain_strings_without_duplicates(store):
    for i, pool in enumerate(("linux", "default", "linux", "arm", "default")):
        enqueue(store, tree=f"t{i}", pool=pool, now=at(i))
    pools = store.list_pools()
    assert pools == ["default", "arm", "linux"]
    assert all(isinstance(p, str) for p in pools) and len(pools) == len(set(pools))
