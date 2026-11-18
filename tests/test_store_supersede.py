"""M5o A — `Store.supersede`: 한 트랜잭션에서 옛 잡을 치우고 새 잡에 우선순위를 물려준다.

명세 `docs/m5o-workplan.md` §2.3 · §2.5, 시나리오 `docs/m5o-test-scenarios-a.md`
(A1 · A14 · A15 · A17 · A18 · A23).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    QUEUED,
    RUNNING,
    UPLOADING,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.core.supersede import SUPERSEDED, successor_of
from remote_ci_monitor.store import Store

NOW = datetime(2026, 11, 18, 3, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
BOB = Requester(name="bob-desk", label="bob@desk")
GRACE = 10.0


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


_seq = iter(range(1, 10_000))


def enqueue(store, *, state=QUEUED, branch="feat/x", req=ALICE, priority=0, target=None):
    tree = f"{next(_seq):064x}"
    src = Source(mode="tree", repo="org/app", branch=branch, tree_hash=tree, bytes=10)
    return store.create_job(
        preset="gate",
        inputs={"scope": "full"},
        key="gate:full",
        concurrency_group=None,
        source=src,
        requester=req,
        timeout_seconds=1200,
        join_key=join_key("gate", {"scope": "full"}, tree),
        now=NOW,
        state=state,
        priority=priority,
        target_worker=target,
    )


def running(store, **kw):
    j = enqueue(store, **kw)
    got = store.claim(1, NOW)
    assert got is not None and got.id == j.id
    return store.get_job(j.id)


# ── A1: 기다리는 잡 → 바로 cancelled ─────────────────────────────────────────


def test_waiting_old_job_is_cancelled_with_the_superseded_summary(store):
    o = enqueue(store)
    n = enqueue(store)
    assert store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE) == [o.id]
    got = store.get_job(o.id)
    assert got.state == CANCELLED
    assert got.cancelled_by == SUPERSEDED
    assert got.summary == f"superseded by #{n.id}"
    assert got.summary_code == "superseded"
    assert got.summary_args == {"by": n.id}
    assert got.finished_at == NOW
    assert successor_of(got) == n.id
    assert store.get_job(n.id).state == QUEUED


def test_uploading_old_job_is_cancelled_too(store):
    o = enqueue(store, state=UPLOADING)
    n = enqueue(store)
    assert store.supersede(n.id, [o.id], NOW, mode="waiting", grace_seconds=GRACE) == [o.id]
    assert store.get_job(o.id).state == CANCELLED


# ── A2 · A3: 도는 잡 ─────────────────────────────────────────────────────────


def test_running_old_job_goes_to_cancelling_by_superseded(store):
    o = running(store)
    n = enqueue(store)
    assert store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE) == [o.id]
    got = store.get_job(o.id)
    assert got.state == CANCELLING
    assert got.cancel is not None and got.cancel.by == SUPERSEDED
    assert got.cancel.kill_at == NOW + timedelta(seconds=GRACE)
    assert successor_of(got) == n.id


def test_waiting_mode_leaves_a_running_job_even_when_asked(store):
    o = running(store)
    n = enqueue(store)
    assert store.supersede(n.id, [o.id], NOW, mode="waiting", grace_seconds=GRACE) == []
    assert store.get_job(o.id).state == RUNNING


# ── A14: 셋 ──────────────────────────────────────────────────────────────────


def test_three_old_jobs_all_handled(store):
    r = running(store)
    q1 = enqueue(store)
    q2 = enqueue(store)
    n = enqueue(store)
    got = store.supersede(n.id, [q2.id, r.id, q1.id], NOW, mode="all", grace_seconds=GRACE)
    assert got == sorted([r.id, q1.id, q2.id])
    assert [store.get_job(i).state for i in (r.id, q1.id, q2.id)] == [
        CANCELLING,
        CANCELLED,
        CANCELLED,
    ]


# ── A15: 우선순위 ────────────────────────────────────────────────────────────


def test_new_job_inherits_a_higher_priority(store):
    o = enqueue(store, priority=PRIORITY_HIGH)
    n = enqueue(store, priority=PRIORITY_NORMAL)
    store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE)
    assert store.get_job(n.id).priority == PRIORITY_HIGH


def test_new_job_never_drops_priority(store):
    o = enqueue(store, priority=PRIORITY_LOW)
    n = enqueue(store, priority=PRIORITY_NORMAL)
    store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE)
    assert store.get_job(n.id).priority == PRIORITY_NORMAL


def test_priority_comes_only_from_jobs_actually_taken(store):
    """다시 판정에서 빠진 잡(다른 사람의 잡)의 우선순위는 물려받지 않는다."""
    other = enqueue(store, req=BOB, priority=PRIORITY_HIGH)
    n = enqueue(store)
    assert store.supersede(n.id, [other.id], NOW, mode="all", grace_seconds=GRACE) == []
    assert store.get_job(n.id).priority == PRIORITY_NORMAL
    assert store.get_job(other.id).state == QUEUED


# ── A18: 이미 취소 중 ────────────────────────────────────────────────────────


def test_already_cancelling_job_is_not_touched(store):
    o = running(store)
    assert store.request_cancel(o.id, "alice-laptop", NOW, GRACE) == CANCELLING
    before = store.get_job(o.id)
    n = enqueue(store)
    later = NOW + timedelta(seconds=3)
    assert store.supersede(n.id, [o.id], later, mode="all", grace_seconds=GRACE) == []
    after = store.get_job(o.id)
    assert after.cancel == before.cancel
    assert after.summary_code is None and successor_of(after) is None


# ── 트랜잭션 안에서 다시 판정한다 ────────────────────────────────────────────


def test_a_joiner_added_after_the_decision_keeps_the_old_job(store):
    o = enqueue(store)
    n = enqueue(store)
    store.add_joiner(o.id, "bob-desk", "bob@desk", NOW)
    assert store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE) == []
    assert store.get_job(o.id).state == QUEUED


def test_old_job_that_finished_in_between_is_skipped(store):
    o = enqueue(store)
    n = enqueue(store)
    store.request_cancel(o.id, "alice-laptop", NOW, GRACE)
    assert store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE) == []
    assert store.get_job(o.id).summary_code == "cancelled_before_start"


def test_a_newer_id_passed_by_mistake_is_never_taken(store):
    n = enqueue(store)
    newer = enqueue(store)
    assert store.supersede(n.id, [newer.id], NOW, mode="all", grace_seconds=GRACE) == []
    assert store.get_job(newer.id).state == QUEUED


def test_targeted_job_is_never_taken(store):
    o = enqueue(store, target="build-02")
    n = enqueue(store)
    assert store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE) == []


# ── A23: 새 잡이 이미 끝났다 ─────────────────────────────────────────────────


def test_new_job_already_finished_takes_nothing(store):
    o = enqueue(store)
    n = enqueue(store)
    store.request_cancel(n.id, "alice-laptop", NOW, GRACE)
    assert store.supersede(n.id, [o.id], NOW, mode="all", grace_seconds=GRACE) == []
    assert store.get_job(o.id).state == QUEUED


def test_missing_new_job_takes_nothing(store):
    o = enqueue(store)
    assert store.supersede(9999, [o.id], NOW, mode="all", grace_seconds=GRACE) == []


def test_off_mode_takes_nothing(store):
    o = enqueue(store)
    n = enqueue(store)
    assert store.supersede(n.id, [o.id], NOW, mode="off", grace_seconds=GRACE) == []


def test_empty_list_is_a_noop(store):
    n = enqueue(store)
    assert store.supersede(n.id, [], NOW, mode="all", grace_seconds=GRACE) == []


# ── A17: 두 새 잡이 동시에 ──────────────────────────────────────────────────


def test_two_new_jobs_racing_never_cancel_each_other(store, tmp_path):
    """O < N1 < N2. 두 스레드가 같은 순간 서로를 후보로 넘겨도 큰 id 하나만 남는다."""
    o = enqueue(store)
    n1 = enqueue(store)
    n2 = enqueue(store)
    barrier = threading.Barrier(2)
    results: dict[int, list[int]] = {}

    def go(new_id: int, olds: list[int]) -> None:
        s = Store(tmp_path / "rcm.sqlite3")  # 스레드마다 연결 — 서버와 같다
        try:
            barrier.wait()
            results[new_id] = s.supersede(new_id, olds, NOW, mode="all", grace_seconds=GRACE)
        finally:
            s.close()

    # N1 은 (잘못) N2 까지 후보로 넘긴다 — 규칙 7 이 트랜잭션 안에서 막아야 한다
    t1 = threading.Thread(target=go, args=(n1.id, [o.id, n2.id]))
    t2 = threading.Thread(target=go, args=(n2.id, [o.id, n1.id]))
    t1.start()
    t2.start()
    t1.join(10)
    t2.join(10)
    assert store.get_job(n2.id).state == QUEUED
    assert store.get_job(n1.id).state == CANCELLED
    assert store.get_job(o.id).state == CANCELLED
    assert n2.id not in results[n1.id]
    # O 는 정확히 한 번 치워졌다 — 둘 중 먼저 커밋한 쪽이 가졌다
    taken = results[n1.id] + results[n2.id]
    assert taken.count(o.id) == 1
    assert successor_of(store.get_job(n1.id)) == n2.id


# ── 취소 중인 잡에는 합류하지 않는다(검증에서 나온 것 · 결정 113) ──────────────


def test_a_cancelling_job_is_never_joined(store):
    o = running(store)
    assert store.request_cancel(o.id, "alice-laptop", NOW, GRACE) == CANCELLING
    assert store.find_joinable(_jk(store, o.id)) is None
    assert store.join_or_bump(_jk(store, o.id), "bob-desk", "bob@desk", 0, NOW) is None
    assert store.join_or_bump_with_submission(_jk(store, o.id), "bob-desk", "b", 0, NOW) is None
    assert store.get_job(o.id).joiners == ()


def test_queued_and_running_jobs_are_still_joined(store):
    r = running(store)
    assert store.join_or_bump(_jk(store, r.id), "bob-desk", "bob@desk", 0, NOW).id == r.id
    o = enqueue(store)
    assert store.join_or_bump(_jk(store, o.id), "bob-desk", "bob@desk", 0, NOW).id == o.id


def _jk(store, job_id):
    row = store._conn().execute("SELECT join_key FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row["join_key"]
