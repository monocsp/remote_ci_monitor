"""보관함(M5p) 저장소 — DB v24 · keep 얼리기 · 합류 · supersede · `_upsert_bundle` 의 보관 행 ·
`set_job_keep` 여덟 갈래와 경쟁 · ack 미루기 · `bundles_due` · 보관 행 함수 · 잡 행보다 오래 사는
항목.

명세는 docs/m5p-workplan.md §4.1 · §4.2 · §5.1 · §6 · §15(앞 절보다 이긴다). 시나리오 표는
docs/m5p-test-scenarios-a.md 의 A18~A36 · A59~A62 · A79~A83 · A88 · A90~A93 · A99~A121 · A129 ·
A137 · A147~A149.

- 도우미는 `test_store_m5e` 의 진짜 순서(`start_collect` → 파일 설치 → `publish_bundle`)를 쓴다.
- 원격 잡의 순서는 `remote_workers.worker_finish` 그대로다: `publish_bundle(READY)`(잡은 아직
  running) 다음에 `finish(...)`(bundle 인자 없음).
- 「같은 트랜잭션인가」는 실패 주입(`block`)과 끼어들기(`interleave`) 두 가지로 본다.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import (
    CANCELLING,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import _SHELF_ITEMS_SQL, DB_VERSION, Store, StoreError
from test_store_m5e import (
    ALICE,
    NOW,
    TTL_HOURS,
    artifacts_core,
    at,
    bundle_dir,
    collect_result,
    install_files,
    join_count,
    policy,
    raw,
    ready_bundle,
)
from test_store_m5n import columns, downgrade_to_v22, file_version, index_columns

BOB = Requester(name="bob-desk", label="bob@desk")
D = timedelta(days=1)
H = timedelta(hours=1)
M = timedelta(minutes=1)
_trees = itertools.count(1)

SHELF_COLUMNS = [
    "job_id",
    "state",
    "preset",
    "inputs_json",
    "repo",
    "branch",
    "sha",
    "source_mode",
    "requester",
    "job_state",
    "job_finished_at",
    "requested_at",
    "shelved_at",
    "last_used_at",
    "stale_since",
    "deleted_at",
    "file_count",
    "total_bytes",
    "reason_code",
    "reason_args",
    "error_code",
    "failed_at",
]


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def store(data_dir: Path):
    s = Store(data_dir / "rcm.sqlite3")
    s.shelf_enabled = True  # App 이 설정을 읽고 넣는 값(§15.1). 기본은 False 다
    yield s
    s.close()


def enqueue_keep(
    store: Store,
    *,
    keep: bool = True,
    state: str = QUEUED,
    branch: str | None = "dev",
    preset: str = "golden-update",
    requester: Requester = ALICE,
    priority: int = 0,
    now=NOW,
):
    tree = f"tree-{next(_trees)}"
    src = Source(
        mode="tree", repo="org/app", branch=branch, base_sha="abc123f", dirty=False, tree_hash=tree
    )
    inputs = {"paths": "test/feature/home/"}
    return store.create_job(
        preset=preset,
        inputs=inputs,
        key=preset,
        concurrency_group=None,
        source=src,
        requester=requester,
        timeout_seconds=1200,
        join_key=join_key(preset, inputs, tree),
        now=now,
        state=state,
        priority=priority,
        keep=keep,
    )


def running_keep(
    store: Store, *, keep: bool = True, worker: str | None = None, lane: int = 1, **kw: Any
):
    j = enqueue_keep(store, keep=keep, **kw)
    got = store.claim(lane, at(1), worker_name=worker)
    assert got is not None and got.id == j.id
    return j


def ready_result(store: Store, job_id: int, payload: bytes = b"tar bytes") -> Any:
    """로컬 finish 에 넘길 ready 묶음 — 파일을 실제로 놓는다."""
    core = artifacts_core()
    digest = install_files(store, job_id, payload)
    store.start_collect(job_id, policy(), at(2))
    return collect_result(
        state=core.READY,
        files=(),
        total_bytes=3,
        bundle_bytes=len(payload),
        skipped_count=0,
        bundle_sha256=digest,
        bundle_path=bundle_dir(store, job_id) / "bundle.tar",
    )


def local_finish(store: Store, job_id: int, *, now=None, state: str = SUCCEEDED) -> str:
    """로컬 레인의 finish(bundle=READY). 묶음 sha 를 돌려준다."""
    result = ready_result(store, job_id)
    assert store.finish(job_id, state, now=now or at(60), exit_code=0, bundle=result)
    return result.bundle_sha256


def finished_with_bundle(store: Store, *, keep: bool = False, ready_at=NOW, **kw: Any) -> tuple:
    """끝난 잡 + ready 묶음(원격 순서가 아니어도 된다 — 판정 시험용). (잡, sha)."""
    j = running_keep(store, keep=keep, **kw)
    sha = ready_bundle(store, j.id, ready_at=ready_at)
    assert store.finish(j.id, SUCCEEDED, now=ready_at, exit_code=0)
    return j, sha


def finished_bare(store: Store, *, keep: bool = False, state: str = SUCCEEDED) -> Any:
    j = running_keep(store, keep=keep)
    assert store.finish(j.id, state, now=at(60), exit_code=0 if state == SUCCEEDED else 1)
    return j


def shelf_row(store: Store, job_id: int, state: str, **cols: Any) -> None:
    """copier 없이 보관 행을 SQL 로 넣는다."""
    values: dict[str, Any] = {
        "job_id": job_id,
        "state": state,
        "preset": "golden-update",
        "branch": "dev",
        "requested_at": NOW.timestamp(),
    }
    values.update(cols)
    names = ", ".join(values)
    marks = ", ".join("?" * len(values))
    with raw(store) as conn:
        conn.execute(f"INSERT INTO shelf_items ({names}) VALUES ({marks})", tuple(values.values()))


def row(store: Store, job_id: int) -> dict[str, Any] | None:
    with raw(store) as conn:
        r = conn.execute("SELECT * FROM shelf_items WHERE job_id=?", (job_id,)).fetchone()
    return None if r is None else dict(r)


def keep_col(store: Store, job_id: int) -> int:
    with raw(store) as conn:
        return int(conn.execute("SELECT keep FROM jobs WHERE id=?", (job_id,)).fetchone()[0])


def bundle_col(store: Store, job_id: int, col: str) -> Any:
    with raw(store) as conn:
        r = conn.execute(f"SELECT {col} FROM job_artifacts WHERE job_id=?", (job_id,)).fetchone()
    return None if r is None else r[0]


def set_bundle(store: Store, job_id: int, **cols: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in cols)
    with raw(store) as conn:
        conn.execute(f"UPDATE job_artifacts SET {sets} WHERE job_id=?", (*cols.values(), job_id))


def block(store: Store, op: str, table: str, *, of: str = "") -> None:
    """그 표의 그 쓰기를 막는 트리거. 호출이 예외로 끝나면 같은 트랜잭션의 다른 쓰기도 없다."""
    name = f"block_{op.lower()}_{table}_{of or 'all'}"
    target = f"{op} OF {of} ON {table}" if of else f"{op} ON {table}"
    with raw(store) as conn:
        conn.execute(
            f"CREATE TRIGGER {name} BEFORE {target} BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )


class _Proxy:
    """연결을 감싸 `BEGIN IMMEDIATE` 다음 `after_sql` 로 시작하는 첫 문장 뒤 `hook()` 을 부른다."""

    def __init__(self, conn: sqlite3.Connection, after_sql: str, hook: Callable[[], None]):
        self._conn = conn
        self._after = after_sql.upper()
        self._hook = hook
        self._in_tx = False
        self._fired = False

    def execute(self, sql: str, *args: Any) -> Any:
        out = self._conn.execute(sql, *args)
        text = sql.strip().upper()
        if text.startswith("BEGIN IMMEDIATE"):
            self._in_tx = True
        elif self._in_tx and not self._fired and text.startswith(self._after):
            self._fired = True
            self._hook()
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def interleave(store: Store, after_sql: str, other: Callable[[], Any], call: Callable[[], Any]):
    """`call()` 의 트랜잭션 한가운데에서 `other()` 를 다른 스레드로 시작한다.

    (call 의 결과, other 의 결과, 0.3초 뒤에도 other 가 막혀 있었나). 판정이 쓰기 잠금 안에
    있으면 other 는 busy_timeout 에 막혀 아직 살아 있다.
    """
    box: dict[str, Any] = {}

    def run_other() -> None:
        try:
            box["value"] = other()
        except BaseException as e:  # noqa: BLE001 — 시험이 결과를 본다
            box["error"] = e

    thread = threading.Thread(target=run_other)
    seen: dict[str, bool] = {}

    def hook() -> None:
        thread.start()
        thread.join(0.3)
        seen["blocked"] = thread.is_alive()

    real = store._conn()
    proxy = _Proxy(real, after_sql, hook)
    store._local.conn = proxy
    try:
        result = call()
    finally:
        store._local.conn = real
    thread.join(10)
    assert not thread.is_alive()
    if "error" in box:
        raise box["error"]
    assert "blocked" in seen, f"no statement starting with {after_sql!r} inside the transaction"
    return result, box.get("value"), seen["blocked"]


#: v25(M5s)가 jobs 에 붙인 열 — v23 · v24 DB 에는 없다
V25_COLUMNS = [
    "not_before",
    "start_deadline",
    "no_supersede",
    "chain_id",
    "retry_of",
    "attempt",
    "retry_on_exit",
    "retry_interval_seconds",
    "retry_max_attempts",
    "retry_disabled",
]


def downgrade_to_v23(path: Path) -> None:
    """v23 DB 를 흉내 낸다 — M5p 가 더한 것과 그 뒤 판(v25)이 더한 것 중 **있는 것만** 지운다."""
    c = sqlite3.connect(path)
    try:
        c.execute("DROP INDEX IF EXISTS jobs_chain")
        have = [r[1] for r in c.execute("PRAGMA table_info(jobs)")]
        for col in reversed(V25_COLUMNS):
            if col in have:
                c.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
        c.execute("DROP INDEX IF EXISTS shelf_items_state")
        c.execute("DROP INDEX IF EXISTS shelf_items_latest")
        c.execute("DROP TABLE IF EXISTS shelf_items")
        if "keep" in [r[1] for r in c.execute("PRAGMA table_info(jobs)")]:
            c.execute("ALTER TABLE jobs DROP COLUMN keep")
        c.execute("PRAGMA user_version=23")
        c.commit()
    finally:
        c.close()


def schema_rows(path: Path) -> list[tuple[str, str, str, str]]:
    c = sqlite3.connect(path)
    try:
        rows = c.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name='shelf_items' "
            "ORDER BY type, name"
        ).fetchall()
    finally:
        c.close()
    return [(t, n, tb, re.sub(r"\s+", " ", s or "").strip()) for t, n, tb, s in rows]


def keep_info(path: Path) -> tuple[Any, ...]:
    c = sqlite3.connect(path)
    try:
        info = c.execute("PRAGMA table_info(jobs)").fetchall()
    finally:
        c.close()
    # 맨 끝이던 `keep` 뒤에 v25(M5s) 열이 붙었다 — 이름으로 찾는다
    row = next(r for r in info if r[1] == "keep")
    return row[1], row[2], row[3], str(row[4])


# ══ DB v24 (§5.1) ═══════════════════════════════════════════════════════════


def test_this_build_is_database_version_24():
    assert DB_VERSION == 27  # v25 는 M5s · v26 은 M5t-b · v27 은 M5t-c — v24 는 그 아래에 있다


def test_a18_v23_migrates_to_v24_after_a_v23_backup(data_dir):
    path = data_dir / "rcm.sqlite3"
    s = Store(path)
    old = enqueue_keep(s, keep=False)
    s.close()
    downgrade_to_v23(path)
    assert "keep" not in columns(path, "jobs")
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert s.get_job(old.id).keep is False
        with raw(s) as conn:
            assert conn.execute("SELECT keep FROM jobs WHERE id=?", (old.id,)).fetchone()[0] == 0
    finally:
        s.close()
    bak = path.parent / "backup" / "rcm.sqlite3.v23.bak"
    assert bak.exists() and file_version(bak) == 23
    assert "keep" not in columns(bak, "jobs")
    assert columns(path, "shelf_items") == SHELF_COLUMNS
    assert columns(path, "jobs")[-1 - len(V25_COLUMNS)] == "keep"  # 그 뒤는 v25(M5s) 열이다
    assert columns(path, "jobs")[-len(V25_COLUMNS) :] == V25_COLUMNS
    assert index_columns(path, "shelf_items_state") == ["state", "last_used_at"]
    assert index_columns(path, "shelf_items_latest") == ["preset", "branch", "shelved_at"]


def test_a19_fresh_and_upgraded_schemas_are_the_same(tmp_path):
    fresh = tmp_path / "fresh" / "rcm.sqlite3"
    Store(fresh).close()
    up = tmp_path / "up" / "rcm.sqlite3"
    Store(up).close()
    downgrade_to_v23(up)
    Store(up).close()
    assert schema_rows(fresh) == schema_rows(up)
    assert len(schema_rows(fresh)) == 3  # 표 하나 + 인덱스 둘
    assert keep_info(fresh) == keep_info(up) == ("keep", "INTEGER", 1, "0")
    assert columns(fresh, "shelf_items") == SHELF_COLUMNS


def test_a83_columns_and_tables_already_there(data_dir):
    path = data_dir / "rcm.sqlite3"
    Store(path).close()
    downgrade_to_v23(path)
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE jobs ADD COLUMN keep INTEGER NOT NULL DEFAULT 0")
    # 번호가 밀린 옛 빌드가 같은 모양의 표와 인덱스 하나를 먼저 만든 DB(M5h · M5n 의 교훈)
    c.execute(_SHELF_ITEMS_SQL.replace(" IF NOT EXISTS", ""))
    c.execute("CREATE INDEX shelf_items_state ON shelf_items(state)")  # 모양이 달라도 견딘다
    c.commit()
    c.close()
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
    finally:
        s.close()


def test_a129_a_v22_database_goes_to_24_in_one_open(data_dir):
    path = data_dir / "rcm.sqlite3"
    Store(path).close()
    downgrade_to_v23(path)
    downgrade_to_v22(path)
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
    finally:
        s.close()
    baks = sorted(p.name for p in (path.parent / "backup").iterdir())
    assert baks == ["rcm.sqlite3.v22.bak"]


# ══ keep 얼리기 (§4.1 · 결정 127) ═══════════════════════════════════════════


def test_a20_create_job_takes_keep(store):
    a = enqueue_keep(store, keep=True)
    b, _sid, _tok = store.create_job_with_submission(
        participant="alice-laptop",
        preset="golden-update",
        inputs={},
        key="golden-update",
        concurrency_group=None,
        source=Source(mode="tree", tree_hash=f"tree-{next(_trees)}"),
        requester=ALICE,
        timeout_seconds=60,
        join_key=None,
        now=NOW,
        state=QUEUED,
        keep=True,
    )
    c = store.create_job(
        preset="ok",
        inputs={},
        key="ok",
        concurrency_group=None,
        source=Source(mode="tree", tree_hash=f"tree-{next(_trees)}"),
        requester=ALICE,
        timeout_seconds=60,
        join_key=None,
        now=NOW,
        state=QUEUED,
    )
    assert (keep_col(store, a.id), keep_col(store, b.id), keep_col(store, c.id)) == (1, 1, 0)
    assert (a.keep, store.get_job(b.id).keep, c.keep) == (True, True, False)


# ══ 보관 행 세우기 (§4.2 · 결정 129 · §15.1) ═════════════════════════════════


def test_a21_local_finish_requests_a_shelf_row(store):
    j = running_keep(store)
    plain = None
    local_finish(store, j.id, now=at(60))
    r = row(store, j.id)
    assert r is not None and r["state"] == "pending"
    assert r["preset"] == "golden-update"
    assert r["inputs_json"] == '{"paths":"test/feature/home/"}'
    assert r["repo"] == "org/app" and r["branch"] == "dev"
    assert r["source_mode"] == "tree" and r["sha"] == j.source.tree_hash
    assert r["requester"] == ALICE.label
    assert r["requested_at"] == at(60).timestamp()
    # §15.1 — 로컬 finish 는 같은 트랜잭션에서 잡을 끝냈으므로 잡 상태가 이미 있다
    assert r["job_state"] == SUCCEEDED and r["job_finished_at"] == at(60).timestamp()
    plain = running_keep(store, keep=False)
    local_finish(store, plain.id)
    assert row(store, plain.id) is None


def test_a21_git_ref_jobs_copy_the_commit_and_the_ref(store):
    src = Source(mode="git_ref", repo="dolomood", ref="dev", sha="c0ffee" * 6 + "abcd")
    j = store.create_job(
        preset="golden-update",
        inputs={},
        key="golden-update",
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=60,
        join_key=None,
        now=NOW,
        state=QUEUED,
        keep=True,
    )
    assert store.claim(1, at(1)) is not None
    local_finish(store, j.id)
    r = row(store, j.id)
    assert (r["source_mode"], r["branch"], r["sha"], r["repo"]) == (
        "git_ref",
        "dev",
        src.sha,
        "dolomood",
    )


def test_a22_remote_publish_requests_the_row_and_active_copies_the_job_state(store):
    j = running_keep(store, worker="mac2")
    ready_bundle(store, j.id, ready_at=at(50))
    r = row(store, j.id)
    assert store.get_job(j.id).state == RUNNING
    assert r["state"] == "pending" and r["job_state"] is None and r["job_finished_at"] is None
    assert store.finish(j.id, SUCCEEDED, now=at(60), exit_code=0)
    # §15.1 — finish 가 같은 트랜잭션에서 보관 행의 잡 상태를 고친다(copier 를 기다리지 않는다)
    assert row(store, j.id)["job_state"] == SUCCEEDED
    got = store.claim_shelf_item(at(61))
    assert got is not None and got.job_id == j.id
    assert store.finish_shelf_item(
        j.id, ok=True, file_count=1, total_bytes=3, reason_code=None, now=at(70)
    )
    r = row(store, j.id)
    assert r["state"] == "active"
    assert r["job_state"] == SUCCEEDED and r["job_finished_at"] == at(60).timestamp()


def test_a116_the_copier_may_take_a_row_whose_job_is_still_running(store):
    """§15.1 — copier 는 안 끝난 잡의 행도 집는다. 잡 상태는 finish 가 나중에 채운다."""
    j = running_keep(store, worker="mac2")
    ready_bundle(store, j.id, ready_at=at(50))
    got = store.claim_shelf_item(at(51))
    assert got is not None and got.job_id == j.id and got.state == "copying"
    assert store.finish_shelf_item(
        j.id, ok=True, file_count=1, total_bytes=3, reason_code=None, now=at(52)
    )
    r = row(store, j.id)
    assert r["state"] == "active" and r["job_state"] is None  # running 을 박지 않는다
    assert store.finish(j.id, FAILED, now=at(60), exit_code=1)
    r = row(store, j.id)
    assert r["job_state"] == FAILED and r["job_finished_at"] == at(60).timestamp()


def test_a106_writes_that_are_not_ready_make_no_row(store):
    core = artifacts_core()
    a = running_keep(store, worker="mac2")
    store.start_collect(a.id, policy(), at(2))
    store.publish_bundle(
        a.id, collect_result(state=core.UPLOADING), now=at(3), expires_at=at(3) + 24 * H
    )
    b = running_keep(store, worker="mac2", lane=2)
    store.publish_bundle(b.id, collect_result(state=core.EMPTY), now=at(3), expires_at=at(9))
    c = running_keep(store, worker="mac2", lane=3)
    store.set_bundle_failed(c.id, core.FAILED, "collect_failed", None, at(3))
    assert (row(store, a.id), row(store, b.id), row(store, c.id)) == (None, None, None)


def test_a107_an_immutable_bundle_does_not_touch_the_row(store):
    j = running_keep(store, worker="mac2")
    ready_bundle(store, j.id, ready_at=at(50))
    first = row(store, j.id)
    ready_bundle(store, j.id, ready_at=at(80))  # finish 재시도가 같은 묶음을 다시 낸다
    assert row(store, j.id) == first


def test_a108_insert_or_ignore_keeps_an_existing_row(store):
    j = running_keep(store)
    shelf_row(store, j.id, "failed", reason_code="shelf_full")
    local_finish(store, j.id)
    r = row(store, j.id)
    assert r["state"] == "failed" and r["reason_code"] == "shelf_full"


def test_a61_shelf_off_marks_the_row_failed_at_once_and_ack_purges(store):
    store.shelf_enabled = False
    j = running_keep(store)
    sha = local_finish(store, j.id)
    r = row(store, j.id)
    assert r["state"] == "failed" and r["reason_code"] == "disabled"
    assert r["failed_at"] == at(60).timestamp()
    d = store.ack_bundle(j.id, sha, owner=True, now=at(70))
    assert d.purge is True and d.deferred is False


def test_an_unavailable_shelf_marks_the_row_failed_unavailable(store):
    store.shelf_available = False
    j = running_keep(store)
    local_finish(store, j.id)
    r = row(store, j.id)
    assert r["state"] == "failed" and r["reason_code"] == "unavailable"


def test_a85_failure_only_collection_success_has_no_bundle_and_no_row(store):
    ok = finished_bare(store, keep=True)
    assert row(store, ok.id) is None
    bad = running_keep(store)
    local_finish(store, bad.id, state=FAILED)
    assert row(store, bad.id)["state"] == "pending"


def test_a90_the_row_is_in_the_finish_transaction(store):
    j = running_keep(store)
    result = ready_result(store, j.id)
    block(store, "INSERT", "shelf_items")
    with pytest.raises((sqlite3.Error, StoreError)):
        store.finish(j.id, SUCCEEDED, now=at(60), exit_code=0, bundle=result)
    assert store.get_job(j.id).state == RUNNING
    assert bundle_col(store, j.id, "state") == artifacts_core().COLLECTING
    assert row(store, j.id) is None


def test_a91_the_row_is_in_the_publish_transaction(store):
    j = running_keep(store, worker="mac2")
    block(store, "INSERT", "shelf_items")
    with pytest.raises((sqlite3.Error, StoreError)):
        ready_bundle(store, j.id, ready_at=at(50))
    assert bundle_col(store, j.id, "state") != artifacts_core().READY


# ══ set_job_keep (§4.1 · §15.1) ════════════════════════════════════════════


@pytest.mark.parametrize("state", [UPLOADING, QUEUED, RUNNING, CANCELLING])
def test_a23_armed(store, state):
    j = enqueue_keep(store, keep=False, state=UPLOADING if state == UPLOADING else QUEUED)
    if state in (RUNNING, CANCELLING):
        assert store.claim(1, at(1)) is not None
    if state == CANCELLING:
        assert store.request_cancel(j.id, "alice-laptop", at(2), grace_seconds=10)
    assert store.get_job(j.id).state == state
    assert store.set_job_keep(j.id, at(5), enabled=True) == "armed"
    assert keep_col(store, j.id) == 1 and row(store, j.id) is None


def test_a23_an_armed_job_gets_its_row_when_it_finishes(store):
    j = enqueue_keep(store, keep=False)
    assert store.set_job_keep(j.id, at(5), enabled=True) == "armed"
    assert store.claim(1, at(6)) is not None
    local_finish(store, j.id)
    assert row(store, j.id)["state"] == "pending"


def test_a24_requested(store):
    j, _ = finished_with_bundle(store)
    assert store.set_job_keep(j.id, at(10), enabled=True) == "requested"
    assert keep_col(store, j.id) == 1
    r = row(store, j.id)
    assert r["state"] == "pending" and r["requested_at"] == at(10).timestamp()


@pytest.mark.parametrize("state", ["pending", "copying"])
def test_already_requested(store, state):
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, state, at(5))
    assert store.set_job_keep(j.id, at(10), enabled=True) == "already_requested"
    assert row(store, j.id)["state"] == state


def test_a25_retouched_active_and_stale(store):
    a, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(a.id, "active", at(5), last_used_at=(NOW - 10 * D).timestamp())
    b, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(
        b.id,
        "stale",
        at(5),
        last_used_at=(NOW - 200 * D).timestamp(),
        stale_since=(NOW - 18 * D).timestamp(),
    )
    assert store.set_job_keep(a.id, at(10), enabled=True) == "retouched"
    assert store.set_job_keep(b.id, at(10), enabled=True) == "retouched"
    assert row(store, a.id)["last_used_at"] == at(10).timestamp()
    rb = row(store, b.id)
    assert rb["state"] == "active" and rb["stale_since"] is None
    assert rb["last_used_at"] == at(10).timestamp()


def test_a149_retouched_ignores_the_one_hour_rule(store):
    """§15.1 — `retouched` 는 1시간 규칙 없이 시계를 돌린다."""
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, "active", at(5), last_used_at=(at(600) - 10 * M).timestamp())
    assert store.set_job_keep(j.id, at(600), enabled=True) == "retouched"
    assert row(store, j.id)["last_used_at"] == at(600).timestamp()


def test_a25_retouched_works_while_the_shelf_is_off(store):
    """§3.1 — 꺼져도 기존 항목은 보이고 쓸 수 있다. 새 보관만 거절한다."""
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, "active", at(5), last_used_at=(NOW - 10 * D).timestamp())
    assert store.set_job_keep(j.id, at(10), enabled=False) == "retouched"


def test_a26_retried(store):
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, "failed", at(5), reason_code="shelf_full")
    assert store.set_job_keep(j.id, at(10), enabled=True) == "retried"
    r = row(store, j.id)
    assert r["state"] == "pending" and r["reason_code"] is None and r["failed_at"] is None
    assert r["requested_at"] == at(10).timestamp()


def test_a26_a_failed_row_without_a_live_bundle_is_gone(store):
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, "failed", at(5), reason_code="timeout")
    set_bundle(store, j.id, state="expired", purged_at=at(6).timestamp())
    assert store.set_job_keep(j.id, at(10), enabled=True) == "gone"
    assert row(store, j.id)["state"] == "failed"


def test_a148_a_deleted_row_with_a_live_bundle_is_requested_again(store):
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, "deleted", at(5), total_bytes=0)
    assert store.set_job_keep(j.id, at(10), enabled=True) == "requested"
    r = row(store, j.id)
    assert r["state"] == "pending" and r["deleted_at"] is None


def test_a59_no_bundle(store):
    core = artifacts_core()
    plain = finished_bare(store)  # 글롭 없는 프리셋 — 묶음 행이 없다
    assert store.set_job_keep(plain.id, at(70), enabled=True) == "no_bundle"
    assert keep_col(store, plain.id) == 0 and row(store, plain.id) is None
    for state in (core.EMPTY, core.DROPPED, core.FAILED, core.SKIPPED, core.DISABLED):
        j, _ = finished_with_bundle(store)
        set_bundle(store, j.id, state=state)
        assert store.set_job_keep(j.id, at(70), enabled=True) == "no_bundle", state
        assert keep_col(store, j.id) == 0 and row(store, j.id) is None


def test_a60_gone(store):
    core = artifacts_core()
    for state in (core.PURGED, core.EXPIRED, core.UNAVAILABLE):
        j, _ = finished_with_bundle(store)
        set_bundle(store, j.id, state=state)
        assert store.set_job_keep(j.id, at(70), enabled=True) == "gone", state
        assert row(store, j.id) is None
    late, _ = finished_with_bundle(store)
    assert store.set_job_keep(late.id, NOW + TTL_HOURS * H, enabled=True) == "gone"
    acked, sha = finished_with_bundle(store)
    store.ack_bundle(acked.id, sha, owner=True, now=at(5))
    assert store.set_job_keep(acked.id, at(10), enabled=True) == "gone"
    assert row(store, acked.id) is None


def test_a109_the_sixty_second_margin_before_expiry(store):
    """§15.1 — 만료가 60초 안이면 `gone`. copier 가 집기 전에 TTL 청소가 가져가지 않게."""
    expiry = NOW + TTL_HOURS * H
    a, _ = finished_with_bundle(store)
    assert store.set_job_keep(a.id, expiry - 60 * timedelta(seconds=1), enabled=True) == "requested"
    b, _ = finished_with_bundle(store)
    assert store.set_job_keep(b.id, expiry - 59 * timedelta(seconds=1), enabled=True) == "gone"


def test_a110_acked_but_joined_is_still_requested(store):
    j, sha = finished_with_bundle(store)
    with raw(store) as conn:
        conn.execute("UPDATE jobs SET join_count=1 WHERE id=?", (j.id,))
    store.ack_bundle(j.id, sha, owner=True, now=at(5))
    assert bundle_col(store, j.id, "acked_at") is not None
    assert store.set_job_keep(j.id, at(10), enabled=True) == "requested"


def test_disabled_when_the_shelf_is_off_or_unavailable(store):
    j, _ = finished_with_bundle(store)
    assert store.set_job_keep(j.id, at(10), enabled=False) == "disabled"
    assert keep_col(store, j.id) == 0 and row(store, j.id) is None
    running = running_keep(store, keep=False)
    assert store.set_job_keep(running.id, at(10), enabled=False) == "disabled"
    assert keep_col(store, running.id) == 0
    store.shelf_available = False
    assert store.set_job_keep(j.id, at(10), enabled=True) == "disabled"


def test_set_job_keep_defaults_to_the_store_setting(store):
    j, _ = finished_with_bundle(store)
    store.shelf_enabled = False
    assert store.set_job_keep(j.id, at(10)) == "disabled"
    store.shelf_enabled = True
    assert store.set_job_keep(j.id, at(10)) == "requested"


def test_a101_unknown_job(store):
    with pytest.raises(StoreError):
        store.set_job_keep(9999, at(10), enabled=True)
    with raw(store) as conn:
        assert conn.execute("SELECT count(*) FROM shelf_items").fetchone()[0] == 0


def test_a148_a_finished_job_whose_bundle_is_still_moving_has_no_bundle(store):
    """P2 격리 검증 D2 — 끝난 잡의 묶음은 더 오지 않는다(로컬은 종료와 같은 트랜잭션에서 ready,
    원격은 finish 전에 ready). 그래서 묶음이 아직 collecting · uploading 이면 끊긴 것이다 —
    `armed` 를 주면 영원히 기다린다. 예전 판은 이것을 `armed` 로 잠갔다."""
    core = artifacts_core()
    j = finished_bare(store)
    store.start_collect(j.id, policy(), at(70))  # collecting
    assert store.set_job_keep(j.id, at(71), enabled=True) == "no_bundle"
    k = finished_bare(store)
    store.start_collect(k.id, policy(), at(70))
    set_bundle(store, k.id, state=core.UPLOADING)
    assert store.set_job_keep(k.id, at(71), enabled=True) == "no_bundle"
    u = finished_bare(store)
    store.start_collect(u.id, policy(), at(70))
    set_bundle(store, u.id, state=core.UNKNOWN)
    assert store.set_job_keep(u.id, at(71), enabled=True) == "no_bundle"


def test_a102_the_remote_gap_keep_after_ready_before_finish(store):
    """§15.1 — 묶음이 ready 면 잡 상태와 무관하게 `requested`.

    「keep 1 · ready · 행 없음」은 없다."""
    j = running_keep(store, keep=False, worker="mac2")
    ready_bundle(store, j.id, ready_at=at(50))
    assert store.set_job_keep(j.id, at(55), enabled=True) == "requested"
    assert store.finish(j.id, SUCCEEDED, now=at(60), exit_code=0)
    r = row(store, j.id)
    assert r is not None and r["state"] == "pending" and r["job_state"] == SUCCEEDED


def test_a103_the_remote_gap_join_keep(store):
    j = running_keep(store, keep=False, worker="mac2")
    ready_bundle(store, j.id, ready_at=at(50))
    key = join_key("golden-update", {"paths": "test/feature/home/"}, j.source.tree_hash)
    assert store.join_or_bump(key, "bob-desk", "bob@desk", 0, at(55), keep=True) is not None
    assert keep_col(store, j.id) == 1
    assert row(store, j.id)["state"] == "pending"


def test_a79_set_job_keep_racing_the_finish(store, data_dir):
    """판정이 쓰기 잠금 안에 있어야 한다 — 끼어든 finish 는 0.3초 뒤에도 막혀 있다."""
    other = Store(data_dir / "rcm.sqlite3")
    other.shelf_enabled = True
    try:
        j = running_keep(store, keep=False)
        result = ready_result(store, j.id)
        got, finished, blocked = interleave(
            store,
            "SELECT",
            lambda: other.finish(j.id, SUCCEEDED, now=at(60), exit_code=0, bundle=result),
            lambda: store.set_job_keep(j.id, at(59), enabled=True),
        )
        assert blocked is True
        assert got == "armed" and finished is True
        assert row(store, j.id)["state"] == "pending"
        # 반대 차례 — finish 가 먼저
        k = running_keep(store, keep=False)
        local_finish(store, k.id)
        assert store.set_job_keep(k.id, at(70), enabled=True) == "requested"
        assert row(store, k.id)["state"] == "pending"
    finally:
        other.close()


def test_a104_set_job_keep_racing_the_ack(store, data_dir):
    other = Store(data_dir / "rcm.sqlite3")
    other.shelf_enabled = True
    try:
        j, sha = finished_with_bundle(store)
        got, decision, blocked = interleave(
            store,
            "SELECT",
            lambda: other.ack_bundle(j.id, sha, owner=True, now=at(11)),
            lambda: store.set_job_keep(j.id, at(10), enabled=True),
        )
        assert blocked is True
        assert got == "requested" and decision.deferred is True and decision.purge is False
        k, sha2 = finished_with_bundle(store)
        d = store.ack_bundle(k.id, sha2, owner=True, now=at(10))
        assert d.purge is True
        assert store.set_job_keep(k.id, at(11), enabled=True) == "gone"
    finally:
        other.close()


# ══ 합류 · supersede (§4.1 · 결정 128) ══════════════════════════════════════


def _key(job: Any) -> str:
    return join_key(job.preset, job.inputs, job.source.tree_hash)


def test_a27_joining_turns_keep_on(store):
    a = enqueue_keep(store, keep=False)
    assert store.join_or_bump(_key(a), "bob-desk", "bob@desk", 0, at(5), keep=True) is not None
    b = enqueue_keep(store, keep=False)
    got = store.join_or_bump_with_submission(_key(b), "bob-desk", "bob@desk", 0, at(5), keep=True)
    assert got is not None
    assert (keep_col(store, a.id), keep_col(store, b.id)) == (1, 1)
    assert (join_count(store, a.id), join_count(store, b.id)) == (1, 1)


def test_a80_joining_never_turns_keep_off(store):
    a = enqueue_keep(store, keep=True)
    assert store.join_or_bump(_key(a), "bob-desk", "bob@desk", 0, at(5), keep=False) is not None
    assert keep_col(store, a.id) == 1
    assert store.join_or_bump(_key(a), "bob-desk", "bob@desk", 0, at(6)) is not None
    assert keep_col(store, a.id) == 1


def test_a93_join_keep_is_in_the_join_transaction(store):
    a = enqueue_keep(store, keep=False)
    block(store, "UPDATE", "jobs", of="keep")
    with pytest.raises((sqlite3.Error, StoreError)):
        store.join_or_bump(_key(a), "bob-desk", "bob@desk", 0, at(5), keep=True)
    assert join_count(store, a.id) == 0
    assert store.get_job(a.id).joiners == ()


def test_a28_supersede_passes_keep_on(store):
    old = enqueue_keep(store, keep=True)
    new = enqueue_keep(store, keep=False)
    assert store.supersede(new.id, [old.id], at(5), mode="all", grace_seconds=1) == [old.id]
    assert keep_col(store, new.id) == 1 and keep_col(store, old.id) == 1


def test_a92_supersede_keep_is_in_the_supersede_transaction(store):
    old = enqueue_keep(store, keep=True, priority=1)
    new = enqueue_keep(store, keep=False)
    block(store, "UPDATE", "jobs", of="keep")
    with pytest.raises((sqlite3.Error, StoreError)):
        store.supersede(new.id, [old.id], at(5), mode="all", grace_seconds=1)
    assert store.get_job(old.id).state == QUEUED
    assert store.get_job(new.id).priority == 0


def test_a147_supersede_passes_keep_only_from_jobs_it_took(store):
    done = finished_bare(store)  # 먼저 — claim 은 가장 작은 queued 잡을 집는다
    kept_by_bob = enqueue_keep(store, keep=True)
    store.add_joiner(kept_by_bob.id, "bob-desk", "bob@desk", at(1))  # 남의 대기가 있어 남는다
    new = enqueue_keep(store, keep=False)
    assert store.supersede(new.id, [kept_by_bob.id], at(5), mode="all", grace_seconds=1) == []
    assert keep_col(store, new.id) == 0
    old = enqueue_keep(store, keep=True)
    assert store.supersede(done.id, [old.id], at(5), mode="all", grace_seconds=1) == []
    assert keep_col(store, done.id) == 0


# ══ ack 미루기 · TTL (§4.2 · §15.2 · 결정 130) ══════════════════════════════


def test_a88_ack_decision_positional_construction_still_works():
    d = artifacts_core().AckDecision(200, False, False, None)
    assert d.deferred is False


def test_a29_owner_ack_is_deferred_while_shelving(store):
    j = running_keep(store)
    sha = local_finish(store, j.id)
    d = store.ack_bundle(j.id, sha, owner=True, now=at(70))
    assert (d.status, d.purge, d.deferred, d.reason_code) == (200, False, True, "shelving")
    assert bundle_col(store, j.id, "acked_at") == at(70).timestamp()
    assert bundle_col(store, j.id, "state") == "ready"
    assert (bundle_dir(store, j.id) / "bundle.tar").is_file()


def test_a81_copying_is_deferred_too(store):
    j = running_keep(store)
    sha = local_finish(store, j.id)
    assert store.claim_shelf_item(at(65)) is not None
    d = store.ack_bundle(j.id, sha, owner=True, now=at(70))
    assert d.purge is False and d.deferred is True


def test_a111_a_second_ack_after_deferring_does_not_purge(store):
    j = running_keep(store)
    sha = local_finish(store, j.id)
    store.ack_bundle(j.id, sha, owner=True, now=at(70))
    d = store.ack_bundle(j.id, sha, owner=True, now=at(71))
    assert d.purge is False and d.deferred is False


def test_a112_an_ack_that_would_not_purge_is_not_deferred(store):
    """§15.2 — `deferred` 는 「원래 지울 상황」일 때만 True 다."""
    j = running_keep(store)
    sha = local_finish(store, j.id)
    with raw(store) as conn:
        conn.execute("UPDATE jobs SET join_count=1 WHERE id=?", (j.id,))
    d = store.ack_bundle(j.id, sha, owner=True, now=at(70))
    assert d.purge is False and d.deferred is False
    k = running_keep(store)
    sha2 = local_finish(store, k.id)
    admin = store.ack_bundle(k.id, sha2, owner=False, now=at(70))
    assert admin.purge is False and admin.deferred is False


def test_a113_an_active_item_does_not_hold_the_bundle(store):
    j = running_keep(store)
    sha = local_finish(store, j.id)
    store.mark_shelf(j.id, "active", at(65))
    d = store.ack_bundle(j.id, sha, owner=True, now=at(70))
    assert d.purge is True and d.deferred is False


def test_a30_bundles_due_skips_bundles_being_shelved(store):
    rows = {}
    for state in ("pending", "copying", "active"):
        j, _ = finished_with_bundle(store, keep=True)
        store.mark_shelf(j.id, state, at(5))
        rows[state] = j.id
    due = {b.job_id for b in store.bundles_due(NOW + TTL_HOURS * H)}
    assert due == {rows["active"]}


def test_a105_the_ttl_purge_mark_is_conditional(store):
    """§15.2 — `bundles_due` 와 `set_job_keep` 이 엇갈려도 pending 행의 묶음은 purged 가 안 된다."""
    j, _ = finished_with_bundle(store)
    due = [b.job_id for b in store.bundles_due(NOW + TTL_HOURS * H)]
    assert due == [j.id]
    assert store.set_job_keep(j.id, NOW + (TTL_HOURS - 1) * H, enabled=True) == "requested"
    assert store.mark_bundles_purged(due, artifacts_core().EXPIRED, NOW + TTL_HOURS * H) == 0
    assert bundle_col(store, j.id, "purged_at") is None


def test_release_deferred_bundle_after_shelving(store):
    j = running_keep(store)
    sha = local_finish(store, j.id)
    assert store.ack_bundle(j.id, sha, owner=True, now=at(70)).deferred is True
    assert store.release_deferred_bundle(j.id, now=at(71)) is None  # 아직 pending
    store.claim_shelf_item(at(72))
    store.finish_shelf_item(
        j.id, ok=True, file_count=1, total_bytes=3, reason_code=None, now=at(73)
    )
    path = store.release_deferred_bundle(j.id, now=at(74))
    assert path == bundle_dir(store, j.id)
    assert bundle_col(store, j.id, "state") == artifacts_core().PURGED
    assert bundle_col(store, j.id, "purged_at") == at(74).timestamp()
    assert store.release_deferred_bundle(j.id, now=at(75)) is None  # 두 번 지우라고 하지 않는다


def test_release_deferred_bundle_leaves_joined_and_unacked_bundles(store):
    j = running_keep(store)
    local_finish(store, j.id)
    store.mark_shelf(j.id, "active", at(65))
    assert store.release_deferred_bundle(j.id, now=at(70)) is None  # ack 없음
    k = running_keep(store)
    sha = local_finish(store, k.id)
    store.ack_bundle(k.id, sha, owner=True, now=at(70))
    with raw(store) as conn:
        conn.execute("UPDATE jobs SET join_count=1 WHERE id=?", (k.id,))  # 그새 합류
    store.mark_shelf(k.id, "failed", at(71), reason_code="shelf_full")
    assert store.release_deferred_bundle(k.id, now=at(72)) is None


# ══ 보관 행 함수 (§6) ════════════════════════════════════════════════════════


def test_a31_claim_oldest_first(store):
    a = finished_bare(store)
    b = finished_bare(store)
    shelf_row(store, b.id, "pending", requested_at=(NOW + 60 * timedelta(seconds=1)).timestamp())
    shelf_row(store, a.id, "pending", requested_at=NOW.timestamp())
    first = store.claim_shelf_item(at(100))
    assert first is not None and first.job_id == a.id and first.state == "copying"
    assert row(store, a.id)["state"] == "copying"
    second = store.claim_shelf_item(at(100))
    assert second is not None and second.job_id == b.id
    assert store.claim_shelf_item(at(100)) is None


def test_a115_ties_go_to_the_smaller_job(store):
    a, b = finished_bare(store), finished_bare(store)
    shelf_row(store, b.id, "pending")
    shelf_row(store, a.id, "pending")
    assert store.claim_shelf_item(at(1)).job_id == min(a.id, b.id)


def test_a114_two_copiers_take_one_row_once(store, data_dir):
    j = finished_bare(store)
    shelf_row(store, j.id, "pending")
    stores = [Store(data_dir / "rcm.sqlite3") for _ in range(2)]
    barrier = threading.Barrier(2)
    got: list[Any] = []

    def take(s: Store) -> None:
        barrier.wait()
        got.append(s.claim_shelf_item(at(1)))
        s.close()

    threads = [threading.Thread(target=take, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(x is None for x in got) == [False, True]


def test_a32_finishing_a_copy(store):
    j = finished_bare(store, keep=True)
    shelf_row(store, j.id, "copying")
    assert store.finish_shelf_item(
        j.id, ok=True, file_count=3, total_bytes=1234, reason_code=None, now=at(100)
    )
    r = row(store, j.id)
    assert r["state"] == "active"
    assert r["shelved_at"] == r["last_used_at"] == at(100).timestamp()
    assert (r["file_count"], r["total_bytes"]) == (3, 1234)
    assert r["job_state"] == SUCCEEDED and r["job_finished_at"] == at(60).timestamp()


def test_a62_a_failed_copy_leaves_the_job_and_the_bundle(store):
    j, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(j.id, "copying", at(5))
    job_before, bundle_before = store.get_job(j.id), store.get_bundle(j.id)
    assert store.finish_shelf_item(
        j.id, ok=False, file_count=None, total_bytes=None, reason_code="shelf_full", now=at(9)
    )
    r = row(store, j.id)
    assert r["state"] == "failed" and r["reason_code"] == "shelf_full"
    assert r["failed_at"] == at(9).timestamp()
    assert store.get_job(j.id) == job_before and store.get_bundle(j.id) == bundle_before


def test_a117_finish_only_moves_a_copying_row(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "failed", reason_code="timeout")
    assert not store.finish_shelf_item(
        j.id, ok=True, file_count=1, total_bytes=1, reason_code=None, now=at(9)
    )
    assert row(store, j.id)["state"] == "failed"


def test_a118_finish_after_the_job_row_is_gone(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "copying", job_state="succeeded", job_finished_at=123.0)
    with raw(store) as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (j.id,))
    assert store.finish_shelf_item(
        j.id, ok=True, file_count=1, total_bytes=1, reason_code=None, now=at(9)
    )
    r = row(store, j.id)
    assert r["state"] == "active" and r["job_state"] == "succeeded"
    assert r["job_finished_at"] == 123.0


def test_a33_touch(store):
    t = NOW.timestamp()
    a, b = finished_bare(store), finished_bare(store)
    shelf_row(store, a.id, "active", last_used_at=t)
    shelf_row(store, b.id, "stale", last_used_at=t - 200 * 86400, stale_since=t - 86400)
    assert store.touch_shelf_item(a.id, NOW + 61 * M) is True
    assert store.touch_shelf_item(b.id, NOW) is True
    assert row(store, a.id)["last_used_at"] == (NOW + 61 * M).timestamp()
    rb = row(store, b.id)
    assert rb["state"] == "active" and rb["stale_since"] is None
    assert rb["last_used_at"] == t


def test_a71_store_touch_respects_the_hour(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "active", last_used_at=NOW.timestamp())
    assert store.touch_shelf_item(j.id, NOW + 59 * M) is False
    assert row(store, j.id)["last_used_at"] == NOW.timestamp()
    assert store.touch_shelf_item(j.id, NOW + 60 * M) is True


def test_a72_store_stale_touch_is_immediate(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "stale", last_used_at=NOW.timestamp(), stale_since=NOW.timestamp())
    assert store.touch_shelf_item(j.id, NOW + M) is True
    assert row(store, j.id)["state"] == "active"


def test_a100_nothing_to_do(store):
    assert store.claim_shelf_item(at(1)) is None
    for state in ("pending", "copying", "failed", "deleted"):
        j = finished_bare(store)
        shelf_row(store, j.id, state, last_used_at=(NOW - 30 * D).timestamp())
        before = row(store, j.id)
        assert store.touch_shelf_item(j.id, NOW) is False, state
        assert row(store, j.id) == before
    assert store.touch_shelf_item(9999, NOW) is False


def test_a119_two_touches_at_once_write_once(store, data_dir):
    j = finished_bare(store)
    shelf_row(store, j.id, "active", last_used_at=(NOW - 2 * H).timestamp())
    stores = [Store(data_dir / "rcm.sqlite3") for _ in range(2)]
    barrier = threading.Barrier(2)
    got: list[bool] = []

    def touch(s: Store) -> None:
        barrier.wait()
        got.append(s.touch_shelf_item(j.id, NOW))
        s.close()

    threads = [threading.Thread(target=touch, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(got) == [False, True]
    assert row(store, j.id)["last_used_at"] == NOW.timestamp()


def test_a34_latest_is_the_biggest_job_among_active_and_stale(store):
    """§15.5 — 「가장 최근」은 active · stale 가운데 잡 번호가 가장 큰 것."""
    ids = [finished_bare(store).id for _ in range(5)]
    t = NOW.timestamp()
    shelf_row(store, ids[0], "active", shelved_at=t + 1)
    shelf_row(store, ids[1], "active", shelved_at=t + 2)
    shelf_row(store, ids[2], "pending")
    shelf_row(store, ids[3], "active", shelved_at=t + 9, branch="main")
    shelf_row(store, ids[4], "failed")
    got = store.latest_shelf_item("golden-update", "dev")
    assert got is not None and got.job_id == ids[1]
    assert store.latest_shelf_item("golden-update", None).job_id == ids[3]
    store.mark_shelf(ids[1], "stale", at(5))
    assert store.latest_shelf_item("golden-update", "dev").job_id == ids[1]
    assert store.latest_shelf_item("other", "dev") is None


def test_a120_latest_edges(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "pending")
    assert store.latest_shelf_item("golden-update", "dev") is None


def test_a35_list_one_mark_forget_totals(store):
    ids = [finished_bare(store).id for _ in range(5)]
    shelf_row(store, ids[0], "active", total_bytes=100)
    shelf_row(store, ids[1], "stale", total_bytes=50)
    shelf_row(store, ids[2], "pending")
    shelf_row(store, ids[3], "copying")
    shelf_row(store, ids[4], "failed", preset="scenario-qa", total_bytes=999)
    assert [i.job_id for i in store.shelf_items(state="active")] == [ids[0]]
    assert [i.job_id for i in store.shelf_items(preset="scenario-qa")] == [ids[4]]
    assert [i.job_id for i in store.shelf_items(state=("active", "stale"))] == [ids[1], ids[0]]
    assert len(store.shelf_items(limit=2)) == 2
    assert len(store.shelf_items(limit=None)) == 5
    assert store.shelf_item(9999) is None
    one = store.shelf_item(ids[0])
    assert one is not None and one.job_id == ids[0] and one.total_bytes == 100
    before = row(store, ids[0])
    assert store.mark_shelf(ids[0], "stale", at(5), stale_since=at(5).timestamp()) is True
    after = row(store, ids[0])
    assert after["state"] == "stale" and after["stale_since"] == at(5).timestamp()
    assert {k: v for k, v in after.items() if k not in ("state", "stale_since")} == {
        k: v for k, v in before.items() if k not in ("state", "stale_since")
    }
    store.mark_shelf(ids[0], "active", at(6), stale_since=None)
    totals = store.shelf_totals()
    assert totals["stored_bytes"] == 150
    # §15.3 — 합계의 `pending` 은 copying 을 포함한다
    assert totals["items"] == {"active": 1, "stale": 1, "pending": 2, "failed": 1}
    assert store.forget_shelf(ids[4]) is True
    assert store.shelf_item(ids[4]) is None
    assert store.forget_shelf(ids[4]) is False


def test_mark_shelf_only_from(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "active")
    assert store.mark_shelf(j.id, "stale", at(5), only_from=("stale",)) is False
    assert row(store, j.id)["state"] == "active"
    assert store.mark_shelf(j.id, "stale", at(5), only_from=("active",)) is True


def test_mark_shelf_fills_the_state_time_when_not_given(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "active")
    store.mark_shelf(j.id, "failed", at(5), reason_code="missing")
    assert row(store, j.id)["failed_at"] == at(5).timestamp()
    store.mark_shelf(j.id, "deleted", at(6))
    assert row(store, j.id)["deleted_at"] == at(6).timestamp()


def test_a99_unknown_fields_and_states(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "active")
    before = row(store, j.id)
    with pytest.raises(StoreError):
        store.mark_shelf(j.id, "stale", at(5), bogus=1)
    with pytest.raises(StoreError):
        store.mark_shelf(j.id, "nope", at(5))
    with pytest.raises(StoreError):
        store.mark_shelf(j.id, "stale", at(5), **{"state=1; --": 1})
    assert row(store, j.id) == before


def test_a121_totals_edges(store):
    ids = [finished_bare(store).id for _ in range(3)]
    shelf_row(store, ids[0], "failed", total_bytes=10)
    shelf_row(store, ids[1], "deleted", total_bytes=20)
    shelf_row(store, ids[2], "copying", total_bytes=30)
    totals = store.shelf_totals()
    assert totals["stored_bytes"] == 0
    assert totals["items"]["pending"] == 1


def test_a15_shelf_item_fields_come_back_typed(store):
    j = finished_bare(store)
    shelf_row(store, j.id, "failed", reason_code="shelf_full", reason_args='{"limit":5}')
    got = store.shelf_item(j.id)
    assert dataclasses.is_dataclass(got)
    assert got.reason_args == {"limit": 5}
    assert isinstance(got.requested_at, float)


# ══ 잡 행보다 오래 산다 (§4.5 · 결정 125) ═══════════════════════════════════


def test_a36_the_item_outlives_the_job_row(store):
    j, _ = finished_with_bundle(store, keep=True, ready_at=NOW - 181 * D)
    with raw(store) as conn:
        conn.execute(
            "UPDATE jobs SET finished_at=?, artifacts_purged_at=? WHERE id=?",
            ((NOW - 181 * D).timestamp(), (NOW - 170 * D).timestamp(), j.id),
        )
    store.mark_shelf(j.id, "active", at(5), job_state=SUCCEEDED)
    set_bundle(store, j.id, state="purged", purged_at=(NOW - 180 * D).timestamp())
    assert store.delete_old_jobs(NOW - 180 * D) == 1
    assert store.get_job(j.id) is None
    item = store.shelf_item(j.id)
    assert item is not None
    assert (item.preset, item.branch, item.sha, item.requester, item.job_state) == (
        "golden-update",
        "dev",
        j.source.tree_hash,
        ALICE.label,
        SUCCEEDED,
    )


def test_a137_a_pending_item_keeps_its_job(store):
    j, _ = finished_with_bundle(store, keep=True, ready_at=NOW - 181 * D)
    with raw(store) as conn:
        conn.execute(
            "UPDATE jobs SET finished_at=?, artifacts_purged_at=? WHERE id=?",
            ((NOW - 181 * D).timestamp(), (NOW - 170 * D).timestamp(), j.id),
        )
    assert row(store, j.id)["state"] == "pending"
    assert store.delete_old_jobs(NOW - 180 * D) == 0
    assert store.get_job(j.id) is not None


# ══ 안전판 (§15.2) ══════════════════════════════════════════════════════════


def test_time_out_shelf_requests(store):
    """`pending`(copying 은 아님) 행인데 묶음 만료 + 24시간이 지났으면 `failed(timeout)`."""
    a, _ = finished_with_bundle(store, keep=True)
    b, _ = finished_with_bundle(store, keep=True)
    store.mark_shelf(b.id, "copying", at(5))
    expiry = NOW + TTL_HOURS * H
    assert store.time_out_shelf_requests(expiry + 24 * H - timedelta(seconds=1)) == []
    assert store.time_out_shelf_requests(expiry + 24 * H) == [a.id]
    r = row(store, a.id)
    assert r["state"] == "failed" and r["reason_code"] == "timeout"
    assert row(store, b.id)["state"] == "copying"


# ══ 격리 검증이 찾은 것 (PR #217 · D3) ══════════════════════════════════════


def test_d3_a_remote_job_lost_after_shelving_copies_lost_into_the_row(store):
    j = running_keep(store, worker="mac2")
    ready_bundle(store, j.id, ready_at=at(50))
    assert store.claim_shelf_item(at(51)).job_id == j.id
    assert store.finish_shelf_item(
        j.id, ok=True, file_count=1, total_bytes=3, reason_code=None, now=at(52)
    )
    # 원격 워커를 잃었다 — janitor 의 닫기(`mark_lost_workers`)와 같은 호출
    assert store.finish(
        j.id,
        "lost",
        now=at(120),
        summary="worker gone",
        only_from=("running", "cancelling"),
        hold_cause="worker_unreachable",
    )
    r = row(store, j.id)
    assert r["state"] == "active"
    assert r["job_state"] == "lost" and r["job_finished_at"] == at(120).timestamp()


def test_d3_every_terminal_transition_copies_the_job_state(store):
    """`Store.finish` 를 거치지 않는 종료(취소 · 기동 복구 · supersede)도 보관 행을 고친다."""
    waiting = enqueue_keep(store)
    shelf_row(store, waiting.id, "active")
    assert store.request_cancel(waiting.id, "alice-laptop", at(5), grace_seconds=10)
    r = row(store, waiting.id)
    assert r["job_state"] == "cancelled" and r["job_finished_at"] == at(5).timestamp()
    local = running_keep(store)
    shelf_row(store, local.id, "active")
    lost, _ = store.recover_on_start(at(9))
    assert lost == [local.id]
    assert row(store, local.id)["job_state"] == "lost"
