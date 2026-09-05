"""보존 정리 순수 규칙 — 상태별 보존 기간 · 경계(`>=`) · 활성 보호 · 이미 지운 잡 제외 · 정렬.

mutcheck ⑦: 활성 상태를 걸러내는 줄을 없애면
`test_active_states_are_never_due_even_with_bogus_finished_at` 가 빨개진다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from jobfactory import NOW, job
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
    Job,
)
from remote_ci_monitor.core.retention import RetentionPolicy, due_for_purge, retention_seconds

DAY = 86400
POLICY = RetentionPolicy(success_days=14, failure_days=30)
FAILURE_STATES = (FAILED, TIMED_OUT, CANCELLED, LOST)
ACTIVE = (UPLOADING, QUEUED, RUNNING, CANCELLING)


def finished(
    id: int,
    state: str = SUCCEEDED,
    *,
    age: timedelta,
    purged_at: datetime | None = None,
) -> Job:
    """`age` 만큼 전에 끝난 잡. 시작·생성 시각은 그보다 몇 분 앞."""
    end = NOW - age
    j = job(id, state=state)
    return replace(
        j,
        created_at=end - timedelta(minutes=5),
        queued_at=end - timedelta(minutes=5),
        started_at=end - timedelta(minutes=4),
        finished_at=end,
        artifacts_purged_at=purged_at,
    )


def ids(jobs: list[Job]) -> list[int]:
    return [j.id for j in jobs]


# ── retention_seconds ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (SUCCEEDED, 14 * DAY),
        (FAILED, 30 * DAY),
        (TIMED_OUT, 30 * DAY),
        (CANCELLED, 30 * DAY),
        (LOST, 30 * DAY),
        (UPLOADING, None),
        (QUEUED, None),
        (RUNNING, None),
        (CANCELLING, None),
    ],
)
def test_retention_seconds_by_state(state: str, expected: float | None) -> None:
    assert retention_seconds(state, POLICY) == expected


def test_policy_field_order_is_success_then_failure() -> None:
    assert RetentionPolicy(14, 30) == RetentionPolicy(success_days=14, failure_days=30)
    assert retention_seconds(SUCCEEDED, RetentionPolicy(1, 2)) == 1 * DAY
    assert retention_seconds(FAILED, RetentionPolicy(1, 2)) == 2 * DAY


# ── due_for_purge — 경계 ─────────────────────────────────────────────────────


def test_due_exactly_at_retention_boundary() -> None:
    j = finished(1, SUCCEEDED, age=timedelta(days=14))
    got = due_for_purge([j], NOW, POLICY)
    assert ids(got) == [1]
    assert got[0] is j  # 같은 Job 객체를 돌려준다(복사·재생성 없음)


def test_not_due_one_second_before_retention() -> None:
    j = finished(1, SUCCEEDED, age=timedelta(days=14) - timedelta(seconds=1))
    assert due_for_purge([j], NOW, POLICY) == []


@pytest.mark.parametrize("state", FAILURE_STATES)
def test_failure_states_use_failure_days(state: str) -> None:
    # 성공 기준(14일)은 지났지만 실패 기준(30일)은 아직인 잡은 남는다
    young = finished(1, state, age=timedelta(days=20))
    old = finished(2, state, age=timedelta(days=30))
    assert ids(due_for_purge([young, old], NOW, POLICY)) == [2]


def test_zero_days_purges_on_the_next_sweep() -> None:
    policy = RetentionPolicy(success_days=0, failure_days=0)
    ok = finished(1, SUCCEEDED, age=timedelta(0))
    bad = finished(2, FAILED, age=timedelta(0))
    assert ids(due_for_purge([ok, bad], NOW, policy)) == [1, 2]


# ── due_for_purge — 제외 규칙 ────────────────────────────────────────────────


def test_already_purged_jobs_are_excluded() -> None:
    purged = finished(1, SUCCEEDED, age=timedelta(days=40), purged_at=NOW - timedelta(days=20))
    fresh = finished(2, SUCCEEDED, age=timedelta(days=40))
    assert ids(due_for_purge([purged, fresh], NOW, POLICY)) == [2]


@pytest.mark.parametrize("state", ACTIVE)
def test_active_states_are_never_due_even_with_bogus_finished_at(state: str) -> None:
    """활성 잡은 `finished_at` 이 어떻게 찍혀 있어도 지우지 않는다(mutcheck ⑦ 의 표적)."""
    j = replace(job(1, state=state), finished_at=NOW - timedelta(days=400))
    assert j.finished_at is not None and not j.is_terminal
    assert due_for_purge([j], NOW, POLICY) == []
    assert due_for_purge([j], NOW, RetentionPolicy(0, 0)) == []


def test_terminal_job_without_finished_at_falls_back_to_created_at() -> None:
    # 있으면 안 되는 행이지만 있으면 created_at 기준으로 판정한다(영원히 남기지 않는다)
    base = job(1, state=SUCCEEDED)
    old = replace(base, created_at=NOW - timedelta(days=14), finished_at=None)
    young = replace(base, id=2, created_at=NOW - timedelta(days=13), finished_at=None)
    assert old.finished_at is None and young.finished_at is None
    assert ids(due_for_purge([young, old], NOW, POLICY)) == [1]


# ── due_for_purge — 모양 ─────────────────────────────────────────────────────


def test_result_is_ordered_by_finished_at_ascending() -> None:
    jobs = [
        finished(1, SUCCEEDED, age=timedelta(days=20)),
        finished(2, FAILED, age=timedelta(days=45)),
        finished(3, SUCCEEDED, age=timedelta(days=15)),
        finished(4, CANCELLED, age=timedelta(days=31)),
        finished(5, SUCCEEDED, age=timedelta(days=10)),  # 아직 안 됨
    ]
    assert ids(due_for_purge(jobs, NOW, POLICY)) == [2, 4, 1, 3]


def test_empty_input_gives_empty_list() -> None:
    assert due_for_purge([], NOW, POLICY) == []


def test_accepts_any_iterable() -> None:
    gen = (finished(i, SUCCEEDED, age=timedelta(days=30 - i)) for i in (1, 2, 3))
    assert ids(due_for_purge(gen, NOW, POLICY)) == [1, 2, 3]


def test_now_is_the_reference_not_wall_clock() -> None:
    j = finished(1, SUCCEEDED, age=timedelta(days=14))
    assert ids(due_for_purge([j], NOW + timedelta(days=1), POLICY)) == [1]
    assert due_for_purge([j], NOW - timedelta(seconds=1), POLICY) == []
