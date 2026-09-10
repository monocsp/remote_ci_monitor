"""부피 회수의 두 눈금(M5i B4 · 결정 76) — 순수 규칙. **구현보다 먼저 썼다.**

명세 `docs/gate-replay-fixes-workplan.md` §3 B4 · `docs/m5g-workplan.md` §4.4.

- `charged`(= `bytes`, 링크마다 센다)는 **항목 표시값과 예산 규칙**의 눈금이다.
- `estimated_reclaimable = charged − shared` 는 **바닥 규칙 · would free · latch 분모**의 눈금이다.
  하드링크된 블록은 지워도 여유가 안 는다(미러가 아직 쥐고 있다) — 바닥이 「지우면 이만큼 는다」고
  말할 때 거짓말하지 않게 한다.
- `floor_attempted` 는 「여유를 알았고 바닥이 켜져 있고 계획 시작 시 바닥 아래였다」다 — latch 는
  이것으로 판정한다(`reason == "free"` 인 항목이 있었나가 아니다).
"""

from __future__ import annotations

from datetime import timedelta

from jobfactory import NOW
from remote_ci_monitor.core.model import FAILED
from remote_ci_monitor.core.retention import (
    REASON_AGE,
    REASON_BUDGET,
    REASON_FREE,
    PurgeItem,
    PurgePlan,
    VolumeItem,
    WorkspaceBudget,
    workspaces_to_purge,
)

GB = 1024**3


def item(
    job_id: int,
    *,
    age_hours: float = 0.0,
    workspace_bytes: int | None = GB,
    snapshot_bytes: int | None = 0,
    shared_bytes: int | None = 0,
    state: str | None = FAILED,
) -> VolumeItem:
    ended = NOW - timedelta(hours=age_hours)
    return VolumeItem(
        job_id=job_id,
        state=state,
        finished_at=ended,
        created_at=ended - timedelta(minutes=5),
        workspace_bytes=workspace_bytes,
        snapshot_bytes=snapshot_bytes,
        shared_bytes=shared_bytes,
    )


def reasons(plan: PurgePlan) -> dict[int, str]:
    return {i.job_id: i.reason for i in plan.items}


# ── 항목의 두 눈금 ───────────────────────────────────────────────────────────


def test_an_item_keeps_charged_and_reclaimable_apart() -> None:
    it = item(1, workspace_bytes=3 * GB, snapshot_bytes=GB, shared_bytes=2 * GB)
    assert it.bytes == 4 * GB  # charged — 링크마다
    assert it.estimated_reclaimable_bytes == 2 * GB  # charged − shared


def test_reclaimable_is_unknown_when_either_side_is_unknown() -> None:
    assert item(1, workspace_bytes=None).estimated_reclaimable_bytes is None
    assert item(1, shared_bytes=None).estimated_reclaimable_bytes is None


def test_shared_defaults_to_zero_so_older_callers_still_construct_an_item() -> None:
    it = VolumeItem(
        job_id=1,
        state=FAILED,
        finished_at=NOW,
        created_at=NOW,
        workspace_bytes=GB,
        snapshot_bytes=0,
    )
    assert it.shared_bytes == 0 and it.estimated_reclaimable_bytes == GB


def test_a_purge_item_carries_shared_and_reclaimable_too() -> None:
    p = PurgeItem(
        job_id=1, workspace_bytes=3 * GB, snapshot_bytes=0, reason=REASON_AGE, shared_bytes=GB
    )
    assert p.estimated_reclaimable_bytes == 2 * GB


# ── 예산은 charged 로 잰다 ───────────────────────────────────────────────────


def test_the_budget_measures_charged_bytes_not_reclaimable() -> None:
    """공유 블록도 예산에는 실린다 — 예산이 보수적으로 동작한다(m5g §4.4)."""
    items = [
        item(1, age_hours=2, workspace_bytes=6 * GB, shared_bytes=5 * GB),
        item(2, age_hours=1),
    ]
    budget = WorkspaceBudget(days=1, max_bytes=6 * GB, min_free_bytes=0)
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    # charged 7 GB > 6 GB → 가장 오래된 #1 을 지우면 1 GB. reclaimable 로 쟀다면 2 GB 라 안 넘었다
    assert reasons(plan) == {1: REASON_BUDGET}
    assert plan.volume_bytes == 7 * GB and plan.over_budget_bytes == 0


# ── 바닥은 reclaimable 로 잰다 ───────────────────────────────────────────────


def test_the_floor_projects_with_reclaimable_bytes_not_charged() -> None:
    """#1 은 charged 6 GB 지만 4 GB 가 미러와 공유라 지워도 2 GB 만 는다 — 그래서 #2 도 고른다."""
    items = [
        item(1, age_hours=2, workspace_bytes=6 * GB, shared_bytes=4 * GB),
        item(2, age_hours=1, workspace_bytes=3 * GB),
    ]
    budget = WorkspaceBudget(days=30, max_bytes=0, min_free_bytes=10 * GB)
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=5 * GB)
    assert reasons(plan) == {1: REASON_FREE, 2: REASON_FREE}
    assert plan.projected_short_free_bytes == 0  # 5 + 2 + 3 = 10


def test_the_shortfall_the_floor_reports_is_in_reclaimable_bytes() -> None:
    items = [item(1, age_hours=2, workspace_bytes=6 * GB, shared_bytes=4 * GB)]
    budget = WorkspaceBudget(days=30, max_bytes=0, min_free_bytes=10 * GB)
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=5 * GB)
    assert reasons(plan) == {1: REASON_FREE}
    assert plan.projected_short_free_bytes == 3 * GB  # 10 − (5 + 2). charged 로 쟀다면 0 이었다


def test_what_the_age_rule_already_took_counts_toward_the_floor_in_reclaimable_bytes() -> None:
    items = [
        item(1, age_hours=48, workspace_bytes=6 * GB, shared_bytes=4 * GB),
        item(2, age_hours=1),
    ]
    budget = WorkspaceBudget(days=1, max_bytes=0, min_free_bytes=8 * GB)
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=5 * GB)
    # 나이로 #1 → 예상 7 GB, 아직 8 GB 아래 → 바닥이 #2 도 고른다
    assert reasons(plan) == {1: REASON_AGE, 2: REASON_FREE}


# ── 계획의 합계 ──────────────────────────────────────────────────────────────


def test_the_plan_sums_charged_and_reclaimable_of_what_it_picked_separately() -> None:
    items = [
        item(1, age_hours=48, workspace_bytes=6 * GB, shared_bytes=4 * GB),
        item(2, age_hours=48, workspace_bytes=3 * GB),
        item(3, age_hours=1, workspace_bytes=GB, shared_bytes=GB),  # 안 고른다
    ]
    plan = workspaces_to_purge(items, NOW, WorkspaceBudget(1, 0, 0), free_bytes=None)
    assert plan.known_freed_bytes == 9 * GB  # charged
    assert plan.estimated_reclaimable_bytes == 5 * GB  # 2 + 3
    assert plan.shared_bytes == 5 * GB  # 인벤토리 전체의 공유 몫
    assert plan.evictable_reclaimable_bytes == 5 * GB  # 지울 수 있는 것을 다 지우면 이만큼 는다


def test_an_unknown_shared_count_makes_the_shared_totals_unknown_not_zero() -> None:
    items = [item(1, age_hours=48, shared_bytes=None), item(2, age_hours=48)]
    plan = workspaces_to_purge(items, NOW, WorkspaceBudget(1, 0, 0), free_bytes=None)
    assert plan.shared_bytes is None and plan.evictable_reclaimable_bytes is None
    assert plan.volume_bytes == 2 * GB  # charged 는 안다
    assert plan.estimated_reclaimable_bytes == GB  # 아는 것만 더한다 — 모르는 것은 0 으로 안 센다


def test_the_purge_items_carry_their_shared_bytes_for_the_receipt() -> None:
    items = [item(1, age_hours=48, workspace_bytes=6 * GB, shared_bytes=4 * GB)]
    plan = workspaces_to_purge(items, NOW, WorkspaceBudget(1, 0, 0), free_bytes=None)
    assert plan.items[0].shared_bytes == 4 * GB
    assert plan.items[0].estimated_reclaimable_bytes == 2 * GB


# ── floor_attempted ──────────────────────────────────────────────────────────


def test_floor_attempted_is_true_when_the_plan_started_under_the_floor() -> None:
    items = [item(1, age_hours=48)]
    budget = WorkspaceBudget(days=1, max_bytes=0, min_free_bytes=10 * GB)
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=5 * GB)
    assert plan.floor_attempted is True
    assert reasons(plan) == {1: REASON_AGE}  # 나이가 먼저 채웠다 — reason 은 free 가 아니다


def test_floor_attempted_is_false_above_the_floor_or_when_it_cannot_be_judged() -> None:
    items = [item(1, age_hours=48)]
    above = WorkspaceBudget(days=1, max_bytes=0, min_free_bytes=10 * GB)
    assert workspaces_to_purge(items, NOW, above, free_bytes=20 * GB).floor_attempted is False
    assert workspaces_to_purge(items, NOW, above, free_bytes=None).floor_attempted is False
    off = WorkspaceBudget(days=1, max_bytes=0, min_free_bytes=0)
    assert workspaces_to_purge(items, NOW, off, free_bytes=0).floor_attempted is False


def test_an_empty_plan_has_the_new_fields_at_their_zero_values() -> None:
    plan = PurgePlan()
    assert plan.estimated_reclaimable_bytes == 0 and plan.floor_attempted is False
    assert plan.shared_bytes == 0 and plan.evictable_reclaimable_bytes == 0
