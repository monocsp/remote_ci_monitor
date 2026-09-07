"""우선순위 규칙(M5a-1) — 대기 잡 정렬 키 `(-priority, id)` · position · `eta_for_new` 배치 ·
이름 ↔ 값 · 합류 시 max · running 은 영향 없음 · 그룹/blocked_by/reason 규칙 불변.

순수 `core/queue.py` · `core/model.py` 만 본다. `store.claim` 의 `ORDER BY priority DESC, id` 와
`join_or_bump` 트랜잭션, 403/409 라우트는 B 의 몫이다.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any

import pytest

from jobfactory import (
    CFG,
    GATE,
    MEDIANS,
    NOW,
    PRESETS,
    ago,
    default_workers,
    job,
    workers,
)
from remote_ci_monitor.core.model import (
    CANCELLING,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NAMES,
    PRIORITY_NORMAL,
    QUEUED,
    RUNNING,
    UPLOADING,
    CancelInfo,
    Job,
    Preset,
)
from remote_ci_monitor.core.queue import (
    compute_queue,
    eta_for_new,
    join_key,
    join_priority,
    priority_from_name,
)


def rows_for(
    jobs: list[Job],
    *,
    lanes: int = 1,
    busy: list[int] | None = None,
    paused: bool = False,
    wk: Any = None,
) -> list:
    wk = wk if wk is not None else default_workers(busy or [], lanes=lanes)
    return compute_queue(
        jobs, workers=wk, paused=paused, medians=MEDIANS, presets=PRESETS, cfg=CFG, now=NOW
    )


def eta(
    jobs: list[Job],
    *,
    priority: int = PRIORITY_NORMAL,
    lanes: int = 1,
    busy: list[int] | None = None,
) -> tuple:
    return eta_for_new(
        jobs,
        preset=GATE,
        key="gate:full",
        workers=default_workers(busy or [], lanes=lanes),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
        priority=priority,
    )


# ── 상수 · 모델 기본값 ───────────────────────────────────────────────────────


def test_priority_constants_are_three_levels() -> None:
    assert (PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH) == (-1, 0, 1)
    assert PRIORITY_NAMES == {"low": -1, "normal": 0, "high": 1}


def test_job_and_preset_default_to_normal() -> None:
    assert job(1).priority == PRIORITY_NORMAL
    assert job(2, priority=PRIORITY_HIGH).priority == 1
    assert GATE.priority == PRIORITY_NORMAL
    assert Preset(name="hot", argv=("true",), priority=PRIORITY_HIGH).priority == 1


# ── 대기 잡 정렬 · position ─────────────────────────────────────────────────


def test_waiting_order_is_priority_desc_then_id() -> None:
    jobs = [
        job(1, created_min=4),
        job(2, created_min=3, priority=PRIORITY_HIGH),
        job(3, created_min=2, priority=PRIORITY_LOW),
        job(4, created_min=1, priority=PRIORITY_HIGH),
    ]
    rows = rows_for(jobs)
    assert [(r.job.id, r.position) for r in rows] == [(2, 1), (4, 2), (1, 3), (3, 4)]


def test_same_priority_ties_break_by_id_not_created_at() -> None:
    # 생성 시각이 뒤집혀 있어도(있으면 안 되지만) 키는 id 다
    jobs = [
        job(8, created_min=5, priority=PRIORITY_HIGH),
        job(7, created_min=1, priority=PRIORITY_HIGH),
    ]
    assert [r.job.id for r in rows_for(jobs)] == [7, 8]


def test_positions_are_consecutive_from_one_across_mixed_priorities() -> None:
    jobs = [job(i, created_min=10 - i, priority=(i % 3) - 1) for i in range(1, 8)]
    rows = rows_for(jobs)
    assert [r.position for r in rows] == list(range(1, 8))
    assert [r.job.priority for r in rows] == sorted((r.job.priority for r in rows), reverse=True)


def test_high_job_takes_the_lane_before_an_older_normal_job() -> None:
    jobs = [job(1, created_min=3), job(2, created_min=1, priority=PRIORITY_HIGH)]
    # 레인이 방금 비었다(오래 놀던 레인 + 오래 기다린 잡은 PLAN 의 not_scheduled 가 맞다)
    rows = {r.job.id: r for r in rows_for(jobs, wk=workers("idle", since=NOW))}
    assert rows[2].position == 1 and rows[2].estimate.wait_seconds == 0
    assert rows[1].position == 2 and rows[1].estimate.wait_seconds == 400
    assert rows[1].ahead_job_id == 2
    assert rows[1].estimate.finish_at == NOW + timedelta(seconds=800)
    # 우선순위는 reason 이 아니다 — 대기 사유는 그대로 waiting_for_lane
    assert rows[1].reason == "waiting_for_lane" and rows[2].reason == "waiting_for_lane"


def test_low_job_waits_behind_every_normal_job_even_newer_ones() -> None:
    jobs = [
        job(1, created_min=3, priority=PRIORITY_LOW),
        job(2, created_min=2),
        job(3, "deploy-dev", created_min=1),
    ]
    rows = rows_for(jobs)
    assert [r.job.id for r in rows] == [2, 3, 1]
    assert rows[2].estimate.wait_seconds == 400 + 600
    assert rows[2].ahead_job_id == 3


def test_uploading_jobs_are_ordered_by_priority_too() -> None:
    # 대기 잡 = uploading · queued. uploading high 는 queued normal 앞이다
    jobs = [
        job(1, created_min=2),
        job(2, state=UPLOADING, created_min=1, tree_hash=None, priority=PRIORITY_HIGH),
    ]
    rows = rows_for(jobs, wk=workers("idle", since=NOW))
    assert [(r.job.id, r.position, r.reason) for r in rows] == [
        (2, 1, "uploading"),
        (1, 2, "waiting_for_lane"),
    ]


def test_two_lanes_high_job_is_placed_first() -> None:
    jobs = [job(1, created_min=3), job(2, created_min=2), job(3, created_min=1, priority=1)]
    rows = {r.job.id: r for r in rows_for(jobs, lanes=2)}
    assert rows[3].position == 1 and rows[3].estimate.wait_seconds == 0
    assert rows[1].position == 2 and rows[1].estimate.wait_seconds == 0
    assert rows[2].position == 3 and rows[2].estimate.wait_seconds == 400


def test_paused_keeps_priority_order_with_null_eta() -> None:
    rows = rows_for([job(1, created_min=2), job(2, created_min=1, priority=1)], paused=True)
    assert [(r.job.id, r.position, r.reason) for r in rows] == [(2, 1, "paused"), (1, 2, "paused")]
    assert all(r.estimate.wait_seconds is None and r.estimate.finish_at is None for r in rows)


# ── running · cancelling 은 영향 없음 ───────────────────────────────────────


def test_running_job_is_never_displaced_by_a_high_waiting_job() -> None:
    running = job(1, state=RUNNING, created_min=2, started_min=2, priority=PRIORITY_LOW)
    high = job(2, created_min=1, priority=PRIORITY_HIGH)
    rows = rows_for([running, high], busy=[1])
    assert [(r.job.id, r.position) for r in rows] == [(1, None), (2, 1)]
    assert rows[0].reason == "running" and rows[0].estimate.wait_seconds == 0
    assert rows[1].estimate.wait_seconds == 280  # 400 − 120 경과. high 라도 기다린다
    assert rows[1].ahead_job_id == 1


def test_output_order_running_cancelling_then_waiting_by_priority() -> None:
    jobs = [
        job(1, state=RUNNING, created_min=6, started_min=5, priority=PRIORITY_LOW),
        job(
            2,
            state=CANCELLING,
            created_min=7,
            started_min=6,
            lane=2,
            priority=PRIORITY_HIGH,
            cancel=CancelInfo(requested_at=ago(seconds=2), by="alice-laptop", kill_at=NOW),
        ),
        job(3, created_min=3),
        job(4, created_min=2, priority=PRIORITY_HIGH),
        job(5, state=UPLOADING, created_min=1, tree_hash=None, priority=PRIORITY_HIGH),
    ]
    rows = rows_for(jobs, lanes=2, busy=[1, 2])
    assert [(r.job.id, r.position) for r in rows] == [
        (1, None),
        (2, None),
        (4, 1),
        (5, 2),
        (3, 3),
    ]


# ── reason · 그룹 규칙은 그대로 ─────────────────────────────────────────────


def test_not_scheduled_flags_the_first_job_in_priority_order() -> None:
    normal = job(1, created_min=1, queued_min=1)
    high = job(2, created_min=1, queued_min=1, priority=PRIORITY_HIGH)
    rows = rows_for([normal, high], wk=workers("idle", since=ago(minutes=5)))
    assert [(r.job.id, r.reason) for r in rows] == [
        (2, "not_scheduled"),
        (1, "waiting_for_lane"),
    ]


def test_group_blocking_ignores_priority() -> None:
    blocker = job(
        409,
        "qa",
        RUNNING,
        created_min=7,
        started_min=6,
        lane=2,
        group="devices",
        preset="qa",
        priority=PRIORITY_LOW,
    )
    blocked = job(
        413, "qa", QUEUED, created_min=2, group="devices", preset="qa", priority=PRIORITY_HIGH
    )
    rows = {r.job.id: r for r in rows_for([blocker, blocked], wk=workers("idle", "busy:409"))}
    b = rows[413]
    assert b.position == 1 and b.reason == "blocked_by_group"
    assert b.blocked_by.job_id == 409 and b.blocked_by.group == "devices"
    assert b.estimate.finish_at >= rows[409].estimate.finish_at + timedelta(seconds=540)
    assert b.estimate.wait_seconds == rows[409].estimate.remaining_seconds


def test_join_key_does_not_depend_on_priority() -> None:
    # 합류 판정은 priority 와 무관 — 키 함수에 priority 인자가 없다
    assert "priority" not in inspect.signature(join_key).parameters


# ── eta_for_new ──────────────────────────────────────────────────────────────


def test_eta_high_goes_ahead_of_waiting_jobs_but_behind_running() -> None:
    jobs = [
        job(1, state=RUNNING, created_min=2, started_min=2),
        job(2, created_min=1),
        job(3, "deploy-dev", created_min=1),
    ]
    row, ahead = eta(jobs, priority=PRIORITY_HIGH, busy=[1])
    assert row.job.priority == PRIORITY_HIGH
    assert row.position == 1
    assert row.estimate.wait_seconds == 280  # running 의 잔여만 기다린다
    assert ahead == 1  # 실제로 앞에 있는 잡 수


def test_eta_normal_goes_after_high_and_normal_but_before_low() -> None:
    jobs = [
        job(1, created_min=3, priority=PRIORITY_LOW),
        job(2, created_min=2),
        job(3, created_min=1, priority=PRIORITY_HIGH),
    ]
    row, ahead = eta(jobs)
    assert row.position == 3  # high(3) · normal(2) 뒤, low(1) 앞
    assert row.estimate.wait_seconds == 800
    assert ahead == 2


def test_eta_low_goes_last_after_existing_low_jobs() -> None:
    jobs = [job(1, created_min=2, priority=PRIORITY_LOW), job(2, created_min=1)]
    row, ahead = eta(jobs, priority=PRIORITY_LOW)
    assert row.position == 3 and row.estimate.wait_seconds == 800 and ahead == 2


def test_eta_priority_defaults_to_normal_and_all_normal_is_plain_fifo() -> None:
    jobs = [job(1, created_min=2), job(2, created_min=1)]
    row, ahead = eta_for_new(
        jobs,
        preset=GATE,
        key="gate:full",
        workers=default_workers(),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
    )
    assert row.job.priority == PRIORITY_NORMAL
    assert (row.position, row.estimate.wait_seconds, ahead) == (3, 800, 2)
    assert eta(jobs)[0].position == 3


def test_eta_high_on_two_lanes_takes_the_earliest_free_lane() -> None:
    jobs = [
        job(1, state=RUNNING, created_min=2, started_min=2, lane=1),
        job(2, state=RUNNING, created_min=1, started_min=1, lane=2),
        job(3, created_min=1),
    ]
    row, ahead = eta(jobs, priority=PRIORITY_HIGH, lanes=2, busy=[1, 2])
    assert row.position == 1
    assert row.estimate.wait_seconds == 280  # 레인 1 이 먼저 빈다(잔여 280 < 340)
    assert ahead == 2


# ── 이름 ↔ 값 · 합류 max ────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "value"), [("low", -1), ("normal", 0), ("high", 1)])
def test_priority_from_name(name: str, value: int) -> None:
    assert priority_from_name(name) == value
    assert PRIORITY_NAMES[name] == value


@pytest.mark.parametrize("bad", ["urgent", "", "High", "HIGH", "1", "0", "-1", " high", "high "])
def test_priority_from_name_rejects_unknown_names(bad: str) -> None:
    with pytest.raises(ValueError) as e:
        priority_from_name(bad)
    msg = str(e.value)
    assert msg and ("priority" in msg.lower() or "low" in msg)


@pytest.mark.parametrize(
    ("existing", "requested", "want"),
    [
        (0, 1, 1),
        (1, 0, 1),
        (-1, 0, 0),
        (0, -1, 0),
        (1, 1, 1),
        (0, 0, 0),
        (-1, -1, -1),
        (-1, 1, 1),
        (1, -1, 1),
    ],
)
def test_join_priority_is_max_and_never_lowers(existing: int, requested: int, want: int) -> None:
    assert join_priority(existing, requested) == want
