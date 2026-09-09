"""부피(워크스페이스 + 스냅샷 tar) 회수의 순수 규칙 — 명세 `docs/m5g-workplan.md` §4.3.

나이 → 예산 → 바닥, 셋 다 오래된 것부터. 활성 잡과 고아는 어떤 규칙으로도 후보가 아니다.
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

이 파일이 지키는 안전 성질 둘:
- **못 재면 압박 삭제(예산·바닥)만 멈춘다.** 나이 규칙은 크기를 안 보므로 그대로 돈다.
  둘을 한 문장으로 묶어 잠그면 디스크가 차는 동안 나이 규칙까지 멈춘다.
- **못 이룰 목표를 위해 증거를 태우지 않는다.** 지울 수 없는 바이트(도는 잡 + 고아)만으로
  이미 예산을 넘으면 예산 규칙은 아무것도 안 고른다(`budget_unreachable`).

mutcheck ⑬ 활성 필터 제거 · ⑭ 못 잰 크기를 0 으로 · ⑮ `budget_unreachable` 분기 제거가
여기서 빨개진다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from jobfactory import NOW
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    FAILED,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    UPLOADING,
)
from remote_ci_monitor.core.retention import (
    REASON_AGE,
    REASON_BUDGET,
    REASON_FREE,
    VolumeItem,
    WorkspaceBudget,
    workspaces_to_purge,
)

DAY = 86_400
GB = 1024**3
ACTIVE = (UPLOADING, QUEUED, RUNNING, CANCELLING)
TERMINAL = (SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, LOST)

#: 날짜 규칙만 켜 둔 예산(예산·바닥 끔).
AGE_ONLY = WorkspaceBudget(days=1, max_bytes=0, min_free_bytes=0)


def item(
    job_id: int,
    *,
    state: str | None = FAILED,
    age_hours: float = 0.0,
    workspace_bytes: int | None = GB,
    snapshot_bytes: int | None = 0,
    finished: bool = True,
) -> VolumeItem:
    """`age_hours` 전에 끝난 잡 하나. `state=None` 은 잡 행이 없는 고아다."""
    ended = NOW - timedelta(hours=age_hours)
    return VolumeItem(
        job_id=job_id,
        state=state,
        finished_at=ended if finished else None,
        created_at=ended - timedelta(minutes=5),
        workspace_bytes=workspace_bytes,
        snapshot_bytes=snapshot_bytes,
    )


def ids(plan) -> list[int]:
    return [i.job_id for i in plan.items]


def reasons(plan) -> dict[int, str]:
    return {i.job_id: i.reason for i in plan.items}


# ── 나이 ─────────────────────────────────────────────────────────────────────


def test_exactly_at_the_boundary_is_due() -> None:
    plan = workspaces_to_purge([item(1, age_hours=24)], NOW, AGE_ONLY, free_bytes=None)
    assert ids(plan) == [1] and reasons(plan)[1] == REASON_AGE


def test_one_second_before_the_boundary_is_not_due() -> None:
    ws = item(1, age_hours=24)
    ws = VolumeItem(**{**ws.__dict__, "finished_at": ws.finished_at + timedelta(seconds=1)})
    assert workspaces_to_purge([ws], NOW, AGE_ONLY, free_bytes=None).items == ()


@pytest.mark.parametrize("state", TERMINAL)
def test_every_terminal_state_ages_out_including_succeeded(state: str) -> None:
    """성공 잡의 워크스페이스는 보통 없지만
    `rmtree(ignore_errors=True)` 가 조용히 실패하면 남는다."""
    plan = workspaces_to_purge([item(1, state=state, age_hours=48)], NOW, AGE_ONLY, free_bytes=None)
    assert ids(plan) == [1]


def test_zero_days_purges_on_the_next_sweep() -> None:
    budget = WorkspaceBudget(days=0, max_bytes=0, min_free_bytes=0)
    plan = workspaces_to_purge([item(1, age_hours=0)], NOW, budget, free_bytes=None)
    assert ids(plan) == [1]


def test_terminal_job_without_finished_at_falls_back_to_created_at() -> None:
    ws = item(1, age_hours=25, finished=False)
    assert ids(workspaces_to_purge([ws], NOW, AGE_ONLY, free_bytes=None)) == [1]


def test_age_does_not_need_a_measurement() -> None:
    """나이는 크기를 안 본다 — 못 잰 워크스페이스도 기간이 지나면 지운다."""
    ws = item(1, age_hours=48, workspace_bytes=None)
    plan = workspaces_to_purge([ws], NOW, AGE_ONLY, free_bytes=None)
    assert ids(plan) == [1] and plan.volume_bytes is None


# ── 활성 잡과 고아는 후보가 아니다 (mutcheck ⑬) ──────────────────────────────


@pytest.mark.parametrize("state", ACTIVE)
def test_active_states_are_never_due_even_with_a_bogus_finished_at(state: str) -> None:
    ws = item(1, state=state, age_hours=999)
    assert workspaces_to_purge([ws], NOW, AGE_ONLY, free_bytes=None).items == ()


def test_an_orphan_directory_is_never_due() -> None:
    """잡 행이 없는 디렉터리는 회계에만 싣는다 — 주인을 못 찾는 데이터를 지우지 않는다."""
    ws = item(1, state=None, age_hours=999)
    plan = workspaces_to_purge([ws], NOW, AGE_ONLY, free_bytes=None)
    assert plan.items == () and plan.volume_bytes == GB


@pytest.mark.parametrize("state", ACTIVE)
def test_pressure_never_takes_an_active_job_even_when_far_over_budget(state: str) -> None:
    budget = WorkspaceBudget(days=99, max_bytes=1, min_free_bytes=10 * GB)
    items = [item(1, state=state, age_hours=999)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=0)
    assert plan.items == ()


# ── 회계 산식 ────────────────────────────────────────────────────────────────


def test_volume_is_workspace_plus_snapshot_and_splits_into_evictable_and_not() -> None:
    items = [
        item(1, age_hours=0, workspace_bytes=3, snapshot_bytes=4),  # 종료 — 지울 수 있다
        item(2, state=RUNNING, workspace_bytes=5, snapshot_bytes=6),  # 활성
        item(3, state=None, workspace_bytes=7, snapshot_bytes=8),  # 고아
    ]
    plan = workspaces_to_purge(items, NOW, AGE_ONLY, free_bytes=None)
    assert plan.volume_bytes == 33
    assert plan.evictable_bytes == 7
    assert plan.non_evictable_bytes == 26
    assert plan.volume_bytes == plan.evictable_bytes + plan.non_evictable_bytes


# ── 예산 ─────────────────────────────────────────────────────────────────────


def test_budget_takes_the_oldest_until_it_fits() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=2 * GB, min_free_bytes=0)
    items = [item(i, age_hours=10 - i, workspace_bytes=GB) for i in (1, 2, 3, 4)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    assert ids(plan) == [1, 2]  # 오래된 둘. 남은 둘이 2 GiB 로 상한과 같다
    assert set(reasons(plan).values()) == {REASON_BUDGET}
    assert plan.over_budget_bytes == 0


def test_budget_zero_means_no_limit() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=0, min_free_bytes=0)
    items = [item(i, workspace_bytes=100 * GB) for i in (1, 2)]
    assert workspaces_to_purge(items, NOW, budget, free_bytes=None).items == ()


def test_over_budget_is_only_ever_nonzero_when_the_budget_is_unreachable() -> None:
    """예산이 닿을 수 있으면 계획이 끝까지 지워 0 이 된다 — 남는 초과분은 **못 이룰 때만** 생긴다.

    그래서 이 수는 「지웠는데도 남았다」가 아니라 「지울 수 없는 것이 이만큼이다」로 읽는다.
    """
    budget = WorkspaceBudget(days=99, max_bytes=GB, min_free_bytes=0)
    items = [item(1, workspace_bytes=2 * GB), item(2, state=RUNNING, workspace_bytes=3 * GB)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    assert plan.budget_unreachable is True and plan.items == ()
    assert plan.over_budget_bytes == 4 * GB  # 총량 5 GiB − 상한 1 GiB


# ── 예산: 못 이룰 목표에는 손대지 않는다 (mutcheck ⑮) ────────────────────────


def test_budget_takes_nothing_when_non_evictable_alone_is_over_the_limit() -> None:
    """도는 잡 + 고아만으로 이미 상한을 넘으면 종료 잡을 전부 태워도 못 닿는다."""
    budget = WorkspaceBudget(days=99, max_bytes=2 * GB, min_free_bytes=0)
    items = [
        item(1, age_hours=99, workspace_bytes=GB),  # 종료 — 태울 수 있지만 소용없다
        item(2, state=RUNNING, workspace_bytes=3 * GB),
    ]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    assert plan.items == () and plan.budget_unreachable is True


def test_the_floor_still_runs_when_the_budget_is_unreachable() -> None:
    """바닥은 회계가 아니라 응급이다 — 목표에 못 닿아도 한 걸음은 이득이다."""
    budget = WorkspaceBudget(days=99, max_bytes=2 * GB, min_free_bytes=10 * GB)
    items = [
        item(1, age_hours=99, workspace_bytes=GB),
        item(2, state=RUNNING, workspace_bytes=3 * GB),
    ]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=0)
    assert ids(plan) == [1] and reasons(plan)[1] == REASON_FREE
    assert plan.budget_unreachable is True


def test_budget_is_reachable_when_evicting_everything_would_fit() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=2 * GB, min_free_bytes=0)
    items = [
        item(1, age_hours=99, workspace_bytes=2 * GB),
        item(2, state=RUNNING, workspace_bytes=GB),
    ]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    assert plan.budget_unreachable is False and ids(plan) == [1]


# ── 바닥 ─────────────────────────────────────────────────────────────────────


def test_floor_takes_the_oldest_until_the_projection_clears_it() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=0, min_free_bytes=3 * GB)
    items = [item(i, age_hours=10 - i, workspace_bytes=GB) for i in (1, 2, 3, 4)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=GB)
    assert ids(plan) == [1, 2] and set(reasons(plan).values()) == {REASON_FREE}
    assert plan.projected_short_free_bytes == 0


def test_floor_zero_is_off() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=0, min_free_bytes=0)
    assert workspaces_to_purge([item(1)], NOW, budget, free_bytes=0).items == ()


def test_floor_does_nothing_when_free_space_is_unknown() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=0, min_free_bytes=10 * GB)
    plan = workspaces_to_purge([item(1)], NOW, budget, free_bytes=None)
    assert plan.items == () and plan.projected_short_free_bytes is None


def test_floor_reports_the_shortfall_it_could_not_close() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=0, min_free_bytes=10 * GB)
    plan = workspaces_to_purge([item(1, workspace_bytes=GB)], NOW, budget, free_bytes=0)
    assert ids(plan) == [1] and plan.projected_short_free_bytes == 9 * GB


# ── 못 재면 압박 삭제만 멈춘다 (mutcheck ⑭) ──────────────────────────────────


@pytest.mark.parametrize("state", [FAILED, RUNNING, None])
def test_one_unmeasured_item_anywhere_stops_the_budget(state: str | None) -> None:
    """종료 후보든 활성이든 고아든 — 인벤토리 어디든 하나면 총량을 모른다."""
    budget = WorkspaceBudget(days=99, max_bytes=1, min_free_bytes=0)
    items = [item(1, age_hours=99, workspace_bytes=GB), item(2, state=state, workspace_bytes=None)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    assert plan.items == () and plan.volume_bytes is None


def test_one_unmeasured_item_stops_the_floor_too() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=0, min_free_bytes=10 * GB)
    items = [item(1, age_hours=99, workspace_bytes=GB), item(2, snapshot_bytes=None)]
    assert workspaces_to_purge(items, NOW, budget, free_bytes=0).items == ()


def test_the_age_rule_keeps_running_when_a_size_is_unknown() -> None:
    """압박 삭제가 멈춰도 나이는 돈다 — 아니면 디스크가 차는 동안 아무것도 안 지운다."""
    budget = WorkspaceBudget(days=1, max_bytes=GB, min_free_bytes=10 * GB)
    items = [item(1, age_hours=48, workspace_bytes=None), item(2, age_hours=0, workspace_bytes=GB)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=0)
    assert ids(plan) == [1] and reasons(plan)[1] == REASON_AGE


def test_an_inventory_error_stops_the_pressure_rules_but_not_the_age_rule() -> None:
    budget = WorkspaceBudget(days=1, max_bytes=GB, min_free_bytes=10 * GB)
    items = [item(1, age_hours=48, workspace_bytes=GB), item(2, age_hours=0, workspace_bytes=GB)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=0, inventory_error="scan")
    assert ids(plan) == [1] and plan.volume_bytes is None and plan.inventory_error == "scan"


def test_derived_numbers_are_none_when_the_input_is_unknown() -> None:
    """모르는 숫자는 0 이 아니라 null 이다 — 0 은 「지키고 있다」는 거짓말이 된다.

    다만 **정말로 아는 0 은 0 으로 낸다**: 여기서 지울 수 없는 항목은 하나도 없으므로
    `non_evictable_bytes` 는 모르는 게 아니라 0 이다. 모르지 않은 것을 모른다고 말하지 않는다.
    """
    budget = WorkspaceBudget(days=99, max_bytes=GB, min_free_bytes=GB)
    plan = workspaces_to_purge([item(1, workspace_bytes=None)], NOW, budget, free_bytes=None)
    assert plan.volume_bytes is None
    assert plan.evictable_bytes is None
    assert plan.non_evictable_bytes == 0
    assert plan.over_budget_bytes is None
    assert plan.projected_short_free_bytes is None


def test_an_unmeasured_active_job_makes_the_non_evictable_total_unknown_too() -> None:
    budget = WorkspaceBudget(days=99, max_bytes=GB, min_free_bytes=0)
    items = [item(1, workspace_bytes=GB), item(2, state=RUNNING, workspace_bytes=None)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=None)
    assert plan.non_evictable_bytes is None and plan.evictable_bytes == GB
    assert plan.volume_bytes is None and plan.items == ()


# ── 회수 바이트는 아는 것과 모르는 것을 섞지 않는다 ──────────────────────────


def test_freed_bytes_keeps_the_known_sum_and_the_unknown_count_apart() -> None:
    items = [item(1, age_hours=48, workspace_bytes=7), item(2, age_hours=48, workspace_bytes=None)]
    plan = workspaces_to_purge(items, NOW, AGE_ONLY, free_bytes=None)
    assert ids(plan) == [1, 2]
    assert plan.known_freed_bytes == 7
    assert plan.unknown_freed_count == 1


# ── 정렬 · 중복 ──────────────────────────────────────────────────────────────


def test_items_come_back_oldest_first() -> None:
    items = [item(3, age_hours=48), item(1, age_hours=96), item(2, age_hours=72)]
    assert ids(workspaces_to_purge(items, NOW, AGE_ONLY, free_bytes=None)) == [1, 2, 3]


def test_a_job_caught_by_two_rules_is_listed_once_with_the_first_reason() -> None:
    budget = WorkspaceBudget(days=1, max_bytes=1, min_free_bytes=10 * GB)
    items = [item(1, age_hours=48, workspace_bytes=GB)]
    plan = workspaces_to_purge(items, NOW, budget, free_bytes=0)
    assert ids(plan) == [1] and reasons(plan)[1] == REASON_AGE


def test_empty_input_is_an_empty_plan() -> None:
    plan = workspaces_to_purge([], NOW, AGE_ONLY, free_bytes=None)
    assert plan.items == () and plan.volume_bytes == 0 and plan.budget_unreachable is False


def test_now_is_the_reference_not_the_wall_clock() -> None:
    later = NOW + timedelta(days=10)
    assert ids(workspaces_to_purge([item(1, age_hours=0)], later, AGE_ONLY, free_bytes=None)) == [1]
