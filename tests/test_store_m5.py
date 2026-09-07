"""저장소(M5a) — 스키마 v3(`jobs.priority` · `blobs` · `notifications`) · 우선순위 claim 순서 ·
`join_or_bump` · `set_priority` · blob 행 CRUD · 알림 claim 유일성 · 미알림 종료 잡 조회.
명세는 docs/m5-workplan.md M5a-1 · M5a-2 「저장 · 정리」 · M5a-3.

시각은 고정 NOW 기준으로 직접 찍는다(스레드 없음). 아직 구현 전이라 빨간 것이 정상이다.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    UPLOADING,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import DB_VERSION, Store

NOW = datetime(2026, 9, 6, 9, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
HIGH, NORMAL, LOW = 1, 0, -1
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def attr_of(row: Any, name: str) -> Any:
    """blob 행이 dataclass 든 매핑이든 같은 이름으로 읽는다(모양은 구현이 정한다)."""
    if hasattr(row, name):
        return getattr(row, name)
    return row[name]


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
):
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
    )


def finished_job(
    store: Store, *, state: str = SUCCEEDED, finished: datetime, tree: str, created: datetime = NOW
):
    """enqueue → claim → finish. 다른 queued 잡이 없을 때 부른다(claim 이 이 잡을 잡아야 한다)."""
    j = enqueue(store, tree=tree, now=created)
    claimed = store.claim(1, created + timedelta(seconds=1))
    assert claimed is not None and claimed.id == j.id
    store.finish(j.id, state, now=finished, exit_code=0 if state == SUCCEEDED else 1)
    return store.get_job(j.id)


def table_names(path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def job_columns(path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(jobs)")}


# ── 스키마 v3 ─────────────────────────────────────────────────────────────────


def test_fresh_db_is_schema_v3_with_priority_blobs_and_notifications(store, tmp_path):
    path = tmp_path / "rcm.sqlite3"
    assert DB_VERSION == 5 and store.user_version() == 5
    assert "priority" in job_columns(path)
    assert {"blobs", "notifications"} <= table_names(path)
    with sqlite3.connect(path) as c:
        blob_cols = {r[1] for r in c.execute("PRAGMA table_info(blobs)")}
        note_cols = {r[1] for r in c.execute("PRAGMA table_info(notifications)")}
    assert {"sha256", "size", "created_at", "last_used_at"} <= blob_cols
    assert {"job_id", "notify_name", "claimed_at", "delivered_at", "failed"} <= note_cols


def test_migration_v2_to_v3_adds_priority_and_tables_and_keeps_rows(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = enqueue(s)
    s.close()
    # v2 데이터베이스를 흉내 낸다: 새 열·테이블을 떼고 user_version 을 2 로 되돌린다
    c = sqlite3.connect(path)
    try:
        for (name,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%priority%'"
        ).fetchall():
            c.execute(f"DROP INDEX {name}")
        c.execute("ALTER TABLE jobs DROP COLUMN priority")
        # v5(M5b-2)가 더한 것도 뗀다(worker_name · tokens.kind · workers)
        c.execute("DROP INDEX IF EXISTS jobs_worker")
        c.execute("ALTER TABLE jobs DROP COLUMN worker_name")
        c.execute("ALTER TABLE tokens DROP COLUMN kind")
        c.execute("DROP TABLE IF EXISTS workers")
        c.execute("ALTER TABLE jobs DROP COLUMN pool")
        c.execute("DROP TABLE IF EXISTS blobs")
        c.execute("DROP TABLE IF EXISTS notifications")
        c.execute("PRAGMA user_version=2")
        c.commit()
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        c.close()
    assert "priority" not in job_columns(path)
    s2 = Store(path)  # 2 → 3 마이그레이션이 여기서 돈다
    try:
        assert s2.user_version() == 5 and s2.healthy()
        got = s2.get_job(j.id)
        assert got is not None and got.state == QUEUED and got.key == "gate:full"
        assert got.priority == NORMAL  # 기존 행은 normal
        assert s2.list_blobs() == []  # 새 테이블을 쓰는 조회가 돈다
        assert s2.claim_notification(j.id, "slack", at(1)) is True
        assert s2.claim(1, at(2)).id == j.id  # priority 를 쓰는 claim SQL 이 돈다
    finally:
        s2.close()
    s3 = Store(path)  # 두 번째 열기는 아무것도 바꾸지 않는다
    assert s3.user_version() == 5 and s3.get_job(j.id).priority == NORMAL
    s3.close()


def test_create_job_stores_priority_and_defaults_to_normal(store):
    plain = enqueue(store)
    high = enqueue(store, tree="h", priority=HIGH, now=at(1))
    low = enqueue(store, tree="l", priority=LOW, state=UPLOADING, now=at(2))
    assert store.get_job(plain.id).priority == NORMAL
    assert store.get_job(high.id).priority == HIGH and high.priority == HIGH
    assert store.get_job(low.id).priority == LOW
    assert [j.priority for j in store.list_active()] == [NORMAL, HIGH, LOW]  # 목록도 싣는다


# ── claim 순서 ───────────────────────────────────────────────────────────────


def test_claim_takes_high_before_older_normal_and_ties_by_id(store):
    a = enqueue(store, now=at(0))  # normal · 가장 오래됨
    b = enqueue(store, tree="b", priority=HIGH, now=at(1))
    c = enqueue(store, tree="c", priority=LOW, now=at(2))
    d = enqueue(store, tree="d", now=at(3))  # normal
    e = enqueue(store, tree="e", priority=HIGH, now=at(4))
    order = [store.claim(n, at(10 + n)).id for n in range(1, 6)]
    assert order == [b.id, e.id, a.id, d.id, c.id]  # priority DESC, id
    assert store.claim(6, at(20)) is None
    assert all(store.get_job(i).state == RUNNING for i in order)


def test_claim_priority_does_not_override_group_exclusion(store):
    busy = enqueue(store, key="qa", group="devices")
    assert store.claim(1, at(1)).id == busy.id
    blocked = enqueue(store, key="qa", group="devices", tree="h", priority=HIGH, now=at(2))
    plain = enqueue(store, key="gate:fast", inputs={"scope": "fast"}, tree="p", now=at(3))
    assert store.claim(2, at(4)).id == plain.id  # high 라도 그룹이 바쁘면 건너뛴다
    assert store.claim(3, at(5)) is None
    store.finish(busy.id, SUCCEEDED, now=at(6), exit_code=0)
    assert store.claim(1, at(7)).id == blocked.id


def test_uploading_jobs_are_not_claimed_whatever_their_priority(store):
    enqueue(store, state=UPLOADING, priority=HIGH)
    q = enqueue(store, tree="q", now=at(1))
    assert store.claim(1, at(2)).id == q.id
    assert store.claim(2, at(3)) is None


# ── join_or_bump ─────────────────────────────────────────────────────────────


def test_join_or_bump_returns_none_without_a_joinable_job(store):
    key = join_key("gate", {"scope": "full"}, "9f8e")
    assert store.join_or_bump(key, "bob-desk", "bob@desk", HIGH, at(1)) is None
    j = enqueue(store)
    other = join_key("gate", {"scope": "fast"}, "9f8e")
    assert store.join_or_bump(other, "bob-desk", "bob@desk", HIGH, at(2)) is None
    assert store.get_job(j.id).priority == NORMAL and store.get_job(j.id).joiners == ()


def test_join_or_bump_adds_the_joiner_once_and_only_raises_priority(store):
    j = enqueue(store)
    key = join_key("gate", {"scope": "full"}, "9f8e")
    got = store.join_or_bump(key, "bob-desk", "bob@desk", HIGH, at(1))
    assert got is not None and got.id == j.id and got.priority == HIGH
    joiners = [(x.name, x.label, x.joined_at) for x in got.joiners]
    assert joiners == [("bob-desk", "bob@desk", at(1))]
    assert store.get_job(j.id).priority == HIGH  # 돌려준 잡과 DB 가 같다
    again = store.join_or_bump(key, "bob-desk", "bob@desk", LOW, at(2))
    assert again.priority == HIGH  # 낮추지 않는다
    assert len(again.joiners) == 1 and again.joiners[0].joined_at == at(1)  # 합류자는 한 번만
    same = store.join_or_bump(key, "bob-desk", "bob@desk", HIGH, at(3))
    assert same.priority == HIGH and len(same.joiners) == 1
    carol = store.join_or_bump(key, "carol-x", "carol@x", NORMAL, at(4))
    assert [x.name for x in carol.joiners] == ["bob-desk", "carol-x"]
    assert carol.priority == HIGH
    assert store.get_job(j.id).state == QUEUED  # 상태는 건드리지 않는다


def test_join_or_bump_by_the_requester_adds_no_joiner_but_still_bumps(store):
    j = enqueue(store, priority=LOW)
    key = join_key("gate", {"scope": "full"}, "9f8e")
    got = store.join_or_bump(key, ALICE.name, ALICE.label, NORMAL, at(1))
    assert got is not None and got.id == j.id
    assert got.joiners == () and got.priority == NORMAL
    assert store.get_job(j.id).priority == NORMAL


def test_join_or_bump_sees_running_jobs_but_not_finished_ones(store):
    j = enqueue(store)
    key = join_key("gate", {"scope": "full"}, "9f8e")
    assert store.claim(1, at(1)).id == j.id
    running = store.join_or_bump(key, "bob-desk", "bob@desk", HIGH, at(2))
    assert running is not None and running.state == RUNNING  # running 도 합류 대상
    assert [x.name for x in running.joiners] == ["bob-desk"]
    store.finish(j.id, SUCCEEDED, now=at(3), exit_code=0)
    assert store.join_or_bump(key, "carol-x", "carol@x", HIGH, at(4)) is None
    assert [x.name for x in store.get_job(j.id).joiners] == ["bob-desk"]


def test_join_or_bump_bump_shows_in_claim_order(store):
    first = enqueue(store, now=at(0))  # normal
    second = enqueue(store, tree="s", now=at(1))  # normal · 뒤
    key = join_key("gate", {"scope": "full"}, "s")
    assert store.join_or_bump(key, "bob-desk", "bob@desk", HIGH, at(2)).id == second.id
    assert store.claim(1, at(3)).id == second.id  # 합류로 올라간 잡이 먼저
    assert store.claim(2, at(4)).id == first.id


# ── set_priority ─────────────────────────────────────────────────────────────


def test_set_priority_only_for_waiting_jobs(store):
    up = enqueue(store, state=UPLOADING)
    q = enqueue(store, tree="q", now=at(1))
    assert store.set_priority(up.id, HIGH, at(2)) is True
    assert store.get_job(up.id).priority == HIGH
    assert store.set_priority(q.id, LOW, at(3)) is True
    assert store.get_job(q.id).priority == LOW
    assert store.set_priority(q.id, LOW, at(4)) is True  # 같은 값도 대기 잡이면 True
    r = enqueue(store, tree="r", now=at(5))
    assert store.claim(1, at(6)).id == r.id  # normal 이 low(q) 보다 먼저
    assert store.set_priority(r.id, HIGH, at(7)) is False  # running
    assert store.get_job(r.id).priority == NORMAL
    assert store.request_cancel(r.id, "alice-laptop", at(8), 10) == CANCELLING
    assert store.set_priority(r.id, HIGH, at(9)) is False  # cancelling
    assert store.request_cancel(q.id, "alice-laptop", at(10), 10) == CANCELLED
    assert store.set_priority(q.id, HIGH, at(11)) is False  # 종료
    assert store.get_job(q.id).priority == LOW
    assert store.set_priority(999, HIGH, at(12)) is False  # 없는 잡


# ── blobs ─────────────────────────────────────────────────────────────────────


def created_at_of(path, digest: str) -> float | None:
    """행 객체의 모양(BlobInfo 등)을 정하지 않으려고 `created_at` 은 테이블에서 직접 읽는다."""
    with sqlite3.connect(path) as c:
        row = c.execute("SELECT created_at FROM blobs WHERE sha256=?", (digest,)).fetchone()
    return row[0] if row else None


def test_blobs_record_list_touch_delete(store, tmp_path):
    db = tmp_path / "rcm.sqlite3"
    assert store.list_blobs() == []
    store.record_blobs([(SHA_A, 10), (SHA_B, 20)], at(0))
    rows = {attr_of(r, "sha256"): r for r in store.list_blobs()}
    assert set(rows) == {SHA_A, SHA_B}
    assert attr_of(rows[SHA_A], "size") == 10 and attr_of(rows[SHA_B], "size") == 20
    assert created_at_of(db, SHA_A) == at(0).timestamp()
    assert attr_of(rows[SHA_A], "last_used_at") == at(0)
    store.touch_blobs([SHA_A], at(5))
    rows = {attr_of(r, "sha256"): r for r in store.list_blobs()}
    assert attr_of(rows[SHA_A], "last_used_at") == at(5)
    assert created_at_of(db, SHA_A) == at(0).timestamp()  # touch 는 생성 시각을 안 바꾼다
    assert attr_of(rows[SHA_B], "last_used_at") == at(0)
    store.touch_blobs([], at(6))  # 빈 목록은 아무 일도 없다
    store.touch_blobs([SHA_C], at(7))  # 없는 해시는 무시
    assert {attr_of(r, "sha256") for r in store.list_blobs()} == {SHA_A, SHA_B}
    store.delete_blobs([SHA_A, SHA_C])  # 없는 것이 섞여도 오류 없다
    assert [attr_of(r, "sha256") for r in store.list_blobs()] == [SHA_B]
    store.delete_blobs([])
    assert [attr_of(r, "sha256") for r in store.list_blobs()] == [SHA_B]


def test_record_blobs_is_idempotent_per_sha(store):
    store.record_blobs([(SHA_A, 10)], at(0))
    store.record_blobs([(SHA_A, 10), (SHA_B, 5)], at(3))  # 같은 blob 을 다시 받았다
    rows = store.list_blobs()
    assert len(rows) == 2
    a = next(r for r in rows if attr_of(r, "sha256") == SHA_A)
    assert attr_of(a, "size") == 10
    assert attr_of(a, "last_used_at") >= at(0)
    store.record_blobs([], at(4))
    assert len(store.list_blobs()) == 2


def test_blob_keys_may_carry_a_token_scope_prefix(store):
    """`snapshot_cache_scope = "token"` 이면 키가 `<token name>/<sha>` 다 — 저장소엔 문자열."""
    scoped = f"alice-laptop/{SHA_A}"
    store.record_blobs([(scoped, 10), (SHA_A, 10)], at(0))
    assert {attr_of(r, "sha256") for r in store.list_blobs()} == {scoped, SHA_A}
    store.delete_blobs([scoped])
    assert [attr_of(r, "sha256") for r in store.list_blobs()] == [SHA_A]


# ── notifications ────────────────────────────────────────────────────────────


def test_claim_notification_is_unique_per_job_and_rule(store):
    j = finished_job(store, state=FAILED, finished=at(10), tree="f")
    assert store.claim_notification(j.id, "slack-fail", at(11)) is True
    assert store.claim_notification(j.id, "slack-fail", at(12)) is False  # 두 번째는 못 잡는다
    assert store.claim_notification(j.id, "pager", at(13)) is True  # 다른 규칙은 별개
    other = finished_job(store, state=SUCCEEDED, finished=at(20), tree="o")
    assert store.claim_notification(other.id, "slack-fail", at(21)) is True  # 다른 잡도 별개


def test_mark_notification_records_delivery_or_failure(store, tmp_path):
    j = finished_job(store, state=FAILED, finished=at(10), tree="f")
    assert store.claim_notification(j.id, "slack-fail", at(11)) is True
    assert store.claim_notification(j.id, "pager", at(11)) is True
    store.mark_notification(j.id, "slack-fail", delivered=True, now=at(12))
    store.mark_notification(j.id, "pager", delivered=False, now=at(13))
    with sqlite3.connect(tmp_path / "rcm.sqlite3") as c:
        rows = {
            r[0]: r[1:]
            for r in c.execute(
                "SELECT notify_name, claimed_at, delivered_at, failed FROM notifications "
                "WHERE job_id=?",
                (j.id,),
            )
        }
    assert rows["slack-fail"][0] == at(11).timestamp()
    assert rows["slack-fail"][1] == at(12).timestamp() and not rows["slack-fail"][2]
    assert rows["pager"][1] is None and rows["pager"][2]
    # 표시해도 claim 은 여전히 잡혀 있다(재시도 없음)
    assert store.claim_notification(j.id, "pager", at(14)) is False


def test_list_unnotified_finished_excludes_active_claimed_and_old_jobs(store):
    old = finished_job(store, finished=at(-1000), tree="o", created=at(-1010))
    done = finished_job(store, state=FAILED, finished=at(100), tree="d", created=at(90))
    claimed = finished_job(store, state=TIMED_OUT, finished=at(110), tree="c", created=at(100))
    assert store.claim_notification(claimed.id, "slack", at(111)) is True
    later = finished_job(store, finished=at(120), tree="l", created=at(115))
    running = enqueue(store, tree="r", now=at(130))
    assert store.claim(1, at(131)).id == running.id
    enqueue(store, tree="q", now=at(132))  # queued
    enqueue(store, tree="u", state=UPLOADING, now=at(133))
    got = store.list_unnotified_finished(at(0))
    assert [j.id for j in got] == [done.id, later.id]  # finished_at 오름차순 · 활성·claim 제외
    assert all(j.is_terminal for j in got)
    assert old.id not in [j.id for j in got]  # since 이전은 제외
    assert [j.id for j in store.list_unnotified_finished(at(-2000))][0] == old.id
    store.claim_notification(done.id, "slack", at(200))
    assert [j.id for j in store.list_unnotified_finished(at(0))] == [later.id]
    assert store.list_unnotified_finished(at(1000)) == []
