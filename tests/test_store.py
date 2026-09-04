"""저장소 — enqueue · claim 원자성 · 그룹 배제 · 재시작 lost · 마이그레이션 · 토큰 · 합류자."""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import DB_VERSION, Store, StoreError, hash_token

NOW = datetime(2026, 9, 4, 0, 52, 12, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def enqueue(
    store,
    *,
    key="gate:full",
    inputs=None,
    group=None,
    state=QUEUED,
    tree="9f8e",
    now=NOW,
    req=ALICE,
):
    inputs = inputs if inputs is not None else {"scope": "full"}
    src = Source(
        mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree, bytes=None
    )
    return store.create_job(
        preset=key.split(":")[0],
        inputs=inputs,
        key=key,
        concurrency_group=group,
        source=src,
        requester=req,
        timeout_seconds=1200,
        join_key=join_key(key.split(":")[0], inputs, tree),
        now=now,
        state=state,
    )


def test_create_and_get_roundtrip(store):
    j = enqueue(store, state=UPLOADING)
    got = store.get_job(j.id)
    assert got.state == UPLOADING and got.queued_at is None
    assert got.inputs == {"scope": "full"} and got.key == "gate:full"
    assert (
        got.source.tree_hash == "9f8e" and got.source.repo == "org/app" and got.source.dirty is True
    )
    assert got.requester == ALICE and got.timeout_seconds == 1200
    assert got.created_at == NOW
    assert [t.state for t in got.transitions] == [UPLOADING]
    assert store.get_job(999) is None


def test_empty_group_is_normalized_to_null(store):
    j = enqueue(store, group="")
    assert store.get_job(j.id).concurrency_group is None


def test_upload_progress_then_queued(store):
    j = enqueue(store, state=UPLOADING)
    store.update_received(j.id, 1000, at(1))
    got = store.get_job(j.id)
    assert got.source.received_bytes == 1000 and got.source.last_received_at == at(1)
    assert store.mark_uploaded(j.id, 2000, at(2)) is True
    got = store.get_job(j.id)
    assert got.state == QUEUED and got.queued_at == at(2) and got.source.bytes == 2000
    assert [t.state for t in got.transitions] == [UPLOADING, QUEUED]
    assert store.mark_uploaded(j.id, 2000, at(3)) is False  # 이미 queued


def test_claim_is_fifo_and_records_transition(store):
    a = enqueue(store)
    b = enqueue(store, now=at(1))
    got = store.claim(1, at(5))
    assert got.id == a.id and got.state == RUNNING and got.lane == 1 and got.started_at == at(5)
    assert got.phase == "materializing"
    assert [t.state for t in got.transitions] == [QUEUED, RUNNING]
    assert store.claim(2, at(6)).id == b.id
    assert store.claim(3, at(7)) is None


def test_claim_skips_jobs_whose_group_is_busy(store):
    first = enqueue(store, key="qa", group="devices")
    second = enqueue(store, key="qa", group="devices", now=at(1))
    plain = enqueue(store, key="gate:fast", inputs={"scope": "fast"}, now=at(2))
    assert store.claim(1, at(5)).id == first.id
    nxt = store.claim(2, at(6))
    assert nxt.id == plain.id  # 같은 그룹 두 번째는 건너뛰고 평범한 잡을 잡는다
    assert store.claim(3, at(7)) is None
    store.finish(first.id, SUCCEEDED, now=at(8), exit_code=0)
    assert store.claim(1, at(9)).id == second.id


def test_claim_is_atomic_under_concurrent_workers(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    for i in range(20):
        enqueue(s, now=at(i))
    claimed: list[int] = []
    lock = threading.Lock()

    def worker(lane: int):
        local = Store(path)
        while True:
            j = local.claim(lane, at(100 + lane))
            if j is None:
                break
            with lock:
                claimed.append(j.id)
        local.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(1, 7)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(claimed) == list(range(1, 21))  # 각 잡은 정확히 한 번
    assert len(claimed) == len(set(claimed))
    assert all(j.state == RUNNING for j in s.list_active())


def test_restart_marks_running_lost_and_uploading_cancelled_but_keeps_queued(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    running = enqueue(s)
    s.claim(1, at(1))
    cancelling = enqueue(s, key="gate:fast", inputs={"scope": "fast"}, now=at(2))
    s.claim(2, at(3))
    s.request_cancel(cancelling.id, "alice-laptop", at(4), 10)
    queued = enqueue(s, now=at(5))
    uploading = enqueue(s, state=UPLOADING, now=at(6))
    s.close()

    s2 = Store(path)  # 서버를 죽였다 살렸다
    lost, cancelled = s2.recover_on_start(at(60))
    assert lost == [running.id, cancelling.id] and cancelled == [uploading.id]
    r = s2.get_job(running.id)
    assert r.state == LOST and r.lane is None and r.finished_at == at(60)
    assert r.summary.startswith("server restarted 2026-09-04")
    assert [t.state for t in r.transitions] == [QUEUED, RUNNING, LOST]
    assert s2.get_job(queued.id).state == QUEUED  # 큐는 남는다
    u = s2.get_job(uploading.id)
    assert u.state == CANCELLED and u.summary == "server restarted during upload"
    assert s2.get_job(cancelling.id).state == LOST
    assert s2.recover_on_start(at(61)) == ([], [])  # 두 번째 시작은 정리할 게 없다


def test_migration_sets_user_version_and_is_idempotent(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    assert s.user_version() == DB_VERSION
    s.close()
    s2 = Store(path)
    assert s2.user_version() == DB_VERSION and s2.healthy()
    s2.close()
    with sqlite3.connect(path) as c:
        c.execute("PRAGMA user_version=99")
    with pytest.raises(StoreError):
        Store(path)


def test_tokens_store_sha256_only_and_verify_with_revoke(store, tmp_path):
    secret = store.add_token("alice-laptop", admin=False, now=NOW)
    admin = store.add_token("macmini-admin", admin=True, now=at(1))
    assert len(secret) >= 32 and secret != admin
    with sqlite3.connect(tmp_path / "rcm.sqlite3") as c:
        rows = c.execute("SELECT name, sha256 FROM tokens ORDER BY name").fetchall()
    assert rows == [("alice-laptop", hash_token(secret)), ("macmini-admin", hash_token(admin))]
    assert secret not in str(rows)
    info = store.verify_token(secret)
    assert info.name == "alice-laptop" and info.admin is False
    assert store.verify_token(admin).admin is True
    assert store.verify_token("nope") is None and store.verify_token(None) is None
    assert store.revoke_token("alice-laptop", at(2)) is True
    assert store.verify_token(secret) is None
    assert store.revoke_token("alice-laptop", at(3)) is False
    names = [(t.name, t.admin, t.revoked_at is not None) for t in store.list_tokens()]
    assert names == [("alice-laptop", False, True), ("macmini-admin", True, False)]
    with pytest.raises(StoreError):
        store.add_token("macmini-admin", admin=False, now=at(4))


def test_find_joinable_only_matches_active_jobs_with_same_key(store):
    j = enqueue(store)
    key = join_key("gate", {"scope": "full"}, "9f8e")
    assert store.find_joinable(key).id == j.id
    assert store.find_joinable(join_key("gate", {"scope": "fast"}, "9f8e")) is None
    store.claim(1, at(1))
    assert store.find_joinable(key).id == j.id  # running 도 합류 대상
    store.finish(j.id, SUCCEEDED, now=at(2), exit_code=0)
    assert store.find_joinable(key) is None


def test_joiners_add_remove_and_ownership(store):
    j = enqueue(store)
    assert store.add_joiner(j.id, "eve-ci", "eve@ci", at(1)) is True
    assert store.add_joiner(j.id, "eve-ci", "eve@ci", at(2)) is False  # 중복은 무시
    got = store.get_job(j.id)
    assert [(x.name, x.label, x.joined_at) for x in got.joiners] == [("eve-ci", "eve@ci", at(1))]
    assert got.owned_by("eve-ci") and got.owned_by("alice-laptop") and not got.owned_by("bob")
    assert store.remove_joiner(j.id, "eve-ci") is True
    assert store.remove_joiner(j.id, "eve-ci") is False
    assert store.get_job(j.id).joiners == ()


def test_cancel_waiting_is_immediate_running_goes_through_cancelling(store):
    waiting = enqueue(store)
    assert store.request_cancel(waiting.id, "alice-laptop", at(1), 10) == CANCELLED
    w = store.get_job(waiting.id)
    assert w.state == CANCELLED and w.cancelled_by == "alice-laptop" and w.finished_at == at(1)
    assert w.summary == "cancelled before start"

    running = enqueue(store, now=at(2))
    store.claim(1, at(3))
    assert store.request_cancel(running.id, "macmini-admin", at(4), 10) == CANCELLING
    r = store.get_job(running.id)
    assert r.state == CANCELLING and r.cancel.by == "macmini-admin" and r.cancel.kill_at == at(14)
    assert store.request_cancel(running.id, "x", at(5), 10) == CANCELLING  # 두 번째는 그대로
    assert store.finish(running.id, CANCELLED, now=at(15), exit_code=-15) is True
    r = store.get_job(running.id)
    assert r.state == CANCELLED and r.cancelled_by == "macmini-admin" and r.cancel is None
    assert store.request_cancel(running.id, "x", at(16), 10) is None  # 종료 잡
    assert store.finish(running.id, SUCCEEDED, now=at(17)) is False  # 종료 상태는 못 바꾼다


def test_finish_only_from_guards_races(store):
    j = enqueue(store)
    assert store.finish(j.id, CANCELLED, now=at(1), only_from=(RUNNING,)) is False
    assert store.get_job(j.id).state == QUEUED
    with pytest.raises(StoreError):
        store.finish(j.id, RUNNING, now=at(1))


def test_recent_samples_and_active_listing(store):
    ids = []
    for i in range(3):
        j = enqueue(store, now=at(i))
        store.claim(1, at(10 + i))
        store.finish(j.id, SUCCEEDED, now=at(100 + i * 10), exit_code=0)
        ids.append(j.id)
    active = enqueue(store, now=at(50))
    recent = store.list_recent(2)
    assert [j.id for j in recent] == [ids[2], ids[1]]  # 최근 완료 순
    samples = store.list_samples(at(0))
    assert [j.id for j in samples] == ids
    assert all(j.started_at and j.finished_at for j in samples)
    assert [j.id for j in store.list_active()] == [active.id]


def test_markers_roundtrip_and_last_output(store):
    j = enqueue(store)
    store.claim(1, at(1))
    store.add_marker(j.id, "steps", "3", at(2))
    store.add_marker(j.id, "step", "analyze", at(3))
    store.set_last_output(j.id, at(4))
    store.set_phase(j.id, "executing")
    ms = store.markers(j.id)
    assert [(m.kind, m.value, m.at) for m in ms] == [
        ("steps", "3", at(2)),
        ("step", "analyze", at(3)),
    ]
    got = store.get_job(j.id)
    assert got.last_output_at == at(4) and got.phase == "executing"
    assert store.markers_for([j.id, 999]) == {j.id: ms, 999: []}


def test_paused_state(store):
    assert store.get_paused() is None
    store.set_paused("macmini-admin", NOW)
    p = store.get_paused()
    assert p.by == "macmini-admin" and p.at == NOW
    store.clear_paused()
    assert store.get_paused() is None


def test_abandon_stale_uploads_keeps_the_job_as_cancelled(store):
    fresh = enqueue(store, state=UPLOADING, now=at(0))
    store.update_received(fresh.id, 10, at(290))
    stale = enqueue(store, state=UPLOADING, now=at(1))
    store.update_received(stale.id, 10, at(2))
    assert store.abandon_stale_uploads(at(310), 300) == [stale.id]
    s = store.get_job(stale.id)
    assert (
        s.state == CANCELLED
        and s.summary == "upload abandoned after 5m"
        and s.cancelled_by == "server"
    )
    assert store.get_job(fresh.id).state == UPLOADING
