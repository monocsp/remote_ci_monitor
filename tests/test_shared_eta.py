"""같이 도는 잡의 ETA (M5f §6).

레인을 올리면 잡 둘이 한 머신을 나눠 쓴다. 그런데 중앙값은 **혼자 잰 것**이라 같이 도는
동안은 예상보다 오래 걸리고, `overdue = elapsed > expected` 가 일찍 뜨면서 `finish_at` 이
사라진다(`core/queue.py`). 배지 한 칸보다 큰 회귀다.

여기서 하는 것은 둘뿐이다:
- **지금부터 데이터를 모은다** — `jobs.concurrent_at_start`. 안 모으면 45일 뒤에도 소급해서
  못 얻는다. 계층 대체(§6 의 (가))는 그 표본이 쌓인 뒤에 근거를 보고 정한다.
- **정직한 신호** — `Estimate.shared`. 혼자 잰 중앙값을 나눠 쓰는 동안 신뢰도를 한 칸 내린다.
  배수를 지어내지 않는다(그건 없는 숫자를 만드는 것이다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jobfactory import CFG, MEDIANS, NOW, PRESETS, ago, job
from remote_ci_monitor.core.model import QUEUED, RUNNING, Requester, Source, WorkerInfo
from remote_ci_monitor.core.queue import compute_queue, confidence
from remote_ci_monitor.store import Store

T0 = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def enqueue(store, tree: str, *, pool: str = "default"):
    return store.create_job(
        preset="gate",
        inputs={},
        key="gate:full",
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", tree_hash=tree),
        requester=ALICE,
        timeout_seconds=1200,
        join_key=f"jk-{tree}",
        now=T0,
        state=QUEUED,
        pool=pool,
    )


# ── 표본을 지금부터 모은다 ──────────────────────────────────────────────────


def test_a_job_records_how_many_were_running_when_it_started(store):
    """claim 하는 **그 트랜잭션 안에서** 센다 — 나중에는 알 수 없는 값이다."""
    a = enqueue(store, "aa")
    enqueue(store, "bb")
    first = store.claim(1, T0)
    assert first.id == a.id and store.concurrent_at_start(a.id) == 1  # 혼자 시작했다
    second = store.claim(2, T0 + timedelta(seconds=1))
    assert store.concurrent_at_start(second.id) == 2  # 하나가 이미 돌고 있었다


def test_the_count_is_per_pool(store):
    """다른 풀의 잡은 이 머신을 안 나눠 쓴다."""
    enqueue(store, "aa", pool="linux")
    store.claim(1, T0, pool="linux", worker_name="lin-01")
    mine = enqueue(store, "bb")
    store.claim(1, T0 + timedelta(seconds=1))
    assert store.concurrent_at_start(mine.id) == 1


def test_old_rows_have_no_count_rather_than_a_wrong_one(store):
    """마이그레이션 이전 잡은 **모른다** — 0 은 「혼자 돌았다」가 아니다."""
    j = enqueue(store, "aa")
    store._conn().execute("UPDATE jobs SET concurrent_at_start=NULL WHERE id=?", (j.id,))
    assert store.concurrent_at_start(j.id) is None


# ── 정직한 신호 ─────────────────────────────────────────────────────────────


def rows_for(jobs, wk):
    return compute_queue(
        jobs, workers=wk, paused=False, medians=MEDIANS, presets=PRESETS, cfg=CFG, now=NOW
    )


def two_lanes():
    return [
        WorkerInfo(lane=1, state="busy", job_id=1, since=ago(minutes=2)),
        WorkerInfo(lane=2, state="busy", job_id=2, since=ago(minutes=2)),
    ]


def test_a_job_sharing_its_pool_is_marked_shared():
    a = job(1, state=RUNNING, created_min=5, started_min=2, lane=1)
    b = job(2, state=RUNNING, created_min=5, started_min=2, lane=2)
    rows = {r.job.id: r for r in rows_for([a, b], two_lanes())}
    assert rows[1].estimate.shared and rows[2].estimate.shared


def test_a_job_running_alone_is_not_marked_shared():
    a = job(1, state=RUNNING, created_min=5, started_min=2, lane=1)
    wk = [WorkerInfo(lane=1, state="busy", job_id=1, since=ago(minutes=2))]
    assert rows_for([a], wk)[0].estimate.shared is False


def test_a_waiting_job_is_not_shared_yet():
    """아직 안 돌고 있다 — 「지금 나눠 쓰는 중」이라는 사실만 싣는다."""
    a = job(1, state=RUNNING, created_min=5, started_min=2, lane=1)
    b = job(2, created_min=1)
    rows = {r.job.id: r for r in rows_for([a, b], two_lanes())}
    assert rows[2].estimate.shared is False


def test_jobs_in_different_pools_do_not_share():
    from dataclasses import replace

    a = job(1, state=RUNNING, created_min=5, started_min=2, lane=1)
    b = replace(job(2, state=RUNNING, created_min=5, started_min=2, lane=1), pool="linux")
    wk = [
        WorkerInfo(lane=1, state="busy", job_id=1, since=ago(minutes=2)),
        WorkerInfo(lane=1, state="busy", job_id=2, since=ago(minutes=2), worker="lin-01"),
    ]
    rows = {r.job.id: r for r in rows_for([a, b], wk)}
    assert rows[1].estimate.shared is False and rows[2].estimate.shared is False


# ── 신뢰도 ──────────────────────────────────────────────────────────────────


def test_sharing_drops_a_measured_badge_one_step():
    """혼자 잰 중앙값을 나눠 쓰는 중이다 — 덜 확실하다."""
    assert confidence("measured", 7) == "high"
    assert confidence("measured", 7, shared=True) == "med"
    assert confidence("measured", 3) == "med"
    assert confidence("measured", 3, shared=True) == "low"


def test_sharing_cannot_push_a_badge_below_low():
    assert confidence("preset", 0, shared=True) == "low"
    assert confidence("default", 0, shared=True) == "low"


def test_overdue_and_group_wait_still_win():
    """이미 더 급한 말을 하고 있다 — `confidence` 가 먼저 반환한다."""
    assert confidence("measured", 7, shared=True, overdue=True) == "overdue"
    assert confidence("measured", 7, shared=True, group_wait=True) == "group wait"


def test_the_three_renderers_agree(tmp_path):
    """`confidence` 는 `compute_queue` 가 아니라 **렌더 시점에 세 곳에서 따로** 계산된다.
    `shared` 를 `Estimate` 에 싣지 않으면 `rcm top` 과 웹이 다른 배지를 찍는다."""
    from remote_ci_monitor.core.status import estimate_json

    a = job(1, state=RUNNING, created_min=5, started_min=2, lane=1)
    b = job(2, state=RUNNING, created_min=5, started_min=2, lane=2)
    row = {r.job.id: r for r in rows_for([a, b], two_lanes())}[1]
    doc = estimate_json(
        row.estimate,
        confidence=confidence(
            row.estimate.source, row.estimate.sample_count, shared=row.estimate.shared
        ),
    )
    assert doc["shared"] is True  # 화면이 스스로 계산할 수 있게 사실을 싣는다
