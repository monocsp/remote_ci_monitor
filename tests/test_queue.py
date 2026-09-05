"""큐 규칙 — v1 ci_queue 자기검증 21 시나리오 이식 + v2.1 규칙(position · 살아 있는 레인 ·
그룹 하한 · reason · stuck · 합류 키). 뮤테이션 ①(잔여 하한)·②(합류 키 inputs)는 여기서 빨개진다."""

from dataclasses import replace
from datetime import timedelta

from jobfactory import (
    CFG,
    DEPLOY,
    GATE,
    MEDIANS,
    NOW,
    PRESETS,
    QA,
    ago,
    default_workers,
    job,
    workers,
)
from remote_ci_monitor.core.model import (
    CANCELLING,
    FAILED,
    PHASE_MATERIALIZING,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    CancelInfo,
    Source,
)
from remote_ci_monitor.core.queue import (
    QueueConfig,
    compute_queue,
    confidence,
    eta_for_new,
    expected_for,
    join_key,
    medians_from,
    remaining_seconds,
)


def rows_for(jobs, *, lanes=1, busy=None, paused=False, medians=MEDIANS, now=NOW, cfg=CFG, wk=None):
    wk = wk if wk is not None else default_workers(busy or [], lanes=lanes)
    return compute_queue(
        jobs, workers=wk, paused=paused, medians=medians, presets=PRESETS, cfg=cfg, now=now
    )


def eta(jobs, key="gate:full", preset=GATE, *, lanes=1, busy=None, paused=False):
    row, ahead = eta_for_new(
        jobs,
        preset=preset,
        key=key,
        workers=default_workers(busy or [], lanes=lanes),
        paused=paused,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
    )
    return row, ahead


# ── v1 시나리오 1~6: 대기·잔여 ────────────────────────────────────────────────


def test_empty_queue_wait_zero_total_is_own_duration():
    row, ahead = eta([])
    assert row.estimate.wait_seconds == 0
    assert row.estimate.expected_seconds == 400
    assert row.estimate.finish_at == NOW + timedelta(seconds=400)
    assert ahead == 0


def test_running_job_ahead_counts_its_remaining():
    running = job(1, state=RUNNING, created_min=2, started_min=2)
    row, ahead = eta([running], busy=[1])
    assert row.estimate.wait_seconds == 280
    assert (row.estimate.finish_at - NOW).total_seconds() == 680
    assert ahead == 1


def test_queued_job_ahead_adds_its_full_expected():
    jobs = [
        job(1, state=RUNNING, created_min=2, started_min=2),
        job(2, "deploy-dev", created_min=1),
    ]
    row, ahead = eta(jobs, busy=[1])
    assert row.estimate.wait_seconds == 880
    assert ahead == 2


def test_overdue_running_job_floors_wait_to_30s():
    row, _ = eta([job(1, state=RUNNING, created_min=30, started_min=30)], busy=[1])
    assert row.estimate.wait_seconds == 30


def test_jobs_behind_me_are_not_counted_nor_myself():
    jobs = [job(9, created_min=6), job(10, created_min=5), job(11, "deploy-dev", created_min=1)]
    rows = {r.job.id: r for r in rows_for(jobs)}
    assert rows[10].position == 2
    assert rows[10].estimate.wait_seconds == 400  # 앞의 #9 만
    assert rows[10].ahead_job_id == 9
    assert rows[11].estimate.wait_seconds == 800


def test_my_running_job_has_zero_wait_and_remaining():
    me = job(10, state=RUNNING, created_min=3, started_min=3)
    row = rows_for([me], busy=[10])[0]
    assert row.estimate.wait_seconds == 0
    assert row.estimate.remaining_seconds == 220
    assert row.position is None


# ── v1 시나리오 7~8: 표본 ────────────────────────────────────────────────────


def sample(id, key, job_seconds, age_days=1, state=SUCCEEDED, waited=20):
    started = NOW - timedelta(days=age_days)
    return replace(
        job(id, key, state),
        created_at=started - timedelta(seconds=waited),
        started_at=started,
        finished_at=None if job_seconds is None else started + timedelta(seconds=job_seconds),
    )


def test_single_sample_is_not_measured_but_two_are():
    one = medians_from([sample(1, "gate:fast", 100)], NOW, CFG)
    assert one["gate:fast"].sample_count == 1
    assert expected_for("gate:fast", GATE, one, CFG) == (480, "preset", 1)
    two = medians_from([sample(1, "gate:fast", 100), sample(2, "gate:fast", 100)], NOW, CFG)
    assert expected_for("gate:fast", GATE, two, CFG) == (100.0, "measured", 2)


def test_duration_is_started_to_finished_not_created():
    # created 는 started 보다 훨씬 앞 — 큐 대기가 소요에 섞이면 안 된다
    m = medians_from(
        [sample(1, "gate:fast", 70, waited=600), sample(2, "gate:fast", 70, waited=600)], NOW, CFG
    )
    assert m["gate:fast"].seconds == 70.0
    assert m["gate:fast"].wait_seconds == 600.0


def test_jobs_without_timestamps_are_excluded():
    assert (
        medians_from([sample(1, "gate:full", None), sample(2, "gate:full", None)], NOW, CFG) == {}
    )


def test_short_and_old_samples_are_excluded():
    assert medians_from([sample(i, "gate:full", 5) for i in range(3)], NOW, CFG) == {}
    old = [sample(i, "gate:full", 200, age_days=CFG.sample_days + 1) for i in range(3)]
    assert medians_from(old, NOW, CFG) == {}
    fresh = [sample(i, "gate:full", 200) for i in range(3)]
    assert medians_from(fresh, NOW, CFG)["gate:full"].seconds == 200.0


def test_sample_policy_success_excludes_failures_completed_includes():
    jobs = [sample(1, "gate:full", 200, state=FAILED), sample(2, "gate:full", 200, state=FAILED)]
    assert medians_from(jobs, NOW, CFG) == {}
    cfg = QueueConfig(sample_policy="completed")
    assert medians_from(jobs, NOW, cfg)["gate:full"].sample_count == 2


def test_running_elapsed_counts_from_started_at_not_created_at():
    j = job(1, "gate:full", RUNNING, created_min=10, started_min=1)
    row = rows_for([j], busy=[1])[0]
    assert row.estimate.elapsed_seconds == 60
    assert row.estimate.waited_seconds == 540
    assert row.estimate.remaining_seconds == 340


# ── v1 시나리오 10~11: 레인 · 목록 ───────────────────────────────────────────


def test_more_lanes_shorten_wait():
    two = [job(1, created_min=3), job(2, created_min=2)]
    assert eta(two, lanes=1)[0].estimate.wait_seconds == 800
    assert eta(two, lanes=2)[0].estimate.wait_seconds == 400


def test_queue_rows_fifo_remaining_and_cumulative_finish():
    jobs = [
        job(2, "deploy-dev", created_min=1),
        job(1, state=RUNNING, created_min=2, started_min=2),
    ]
    rows = rows_for(jobs, busy=[1])
    assert [r.job.id for r in rows] == [1, 2]
    assert [r.estimate.remaining_seconds for r in rows] == [280, 600]
    assert [(r.estimate.finish_at - NOW).total_seconds() for r in rows] == [280, 880]


def test_terminal_jobs_are_not_in_the_queue():
    done = job(3, state=SUCCEEDED, created_min=9, started_min=8, finished_min=1)
    assert rows_for([done]) == []


# ── v2.1: position · reason · 살아 있는 레인 · 그룹 ───────────────────────────


def test_position_only_for_waiting_jobs_and_output_order():
    jobs = [
        job(1, state=RUNNING, created_min=5, started_min=4),
        job(2, created_min=3),
        job(
            3,
            state=CANCELLING,
            created_min=6,
            started_min=5,
            lane=2,
            cancel=CancelInfo(requested_at=ago(seconds=2), by="alice-laptop", kill_at=NOW),
        ),
        job(4, state=UPLOADING, created_min=1, tree_hash=None),
    ]
    rows = rows_for(jobs, lanes=2, busy=[1, 3])
    assert [(r.job.id, r.position) for r in rows] == [(1, None), (3, None), (2, 1), (4, 2)]
    assert rows[1].reason == "cancelling"
    assert rows[1].estimate.finish_at is None
    assert rows[3].reason == "uploading"


def test_paused_gives_null_wait_and_finish_with_reason_paused():
    rows = rows_for([job(1, created_min=1)], paused=True)
    assert rows[0].reason == "paused"
    assert rows[0].estimate.wait_seconds is None and rows[0].estimate.finish_at is None
    assert rows[0].estimate.expected_seconds == 400  # 기대치는 여전히 안다


def test_all_lanes_down_gives_worker_down_and_null_eta():
    rows = rows_for([job(1, created_min=1)], wk=workers("down:ENOSPC"))
    assert rows[0].reason == "worker_down"
    assert rows[0].estimate.finish_at is None


def test_wait_uses_live_lanes_only():
    jobs = [job(1, created_min=3), job(2, created_min=2)]
    live_two = rows_for(jobs, wk=workers("idle", "idle"))
    assert live_two[1].estimate.wait_seconds == 0
    one_down = rows_for(jobs, wk=workers("idle", "down"))
    assert one_down[1].estimate.wait_seconds == 400


def test_group_blocked_job_waits_for_blocker_and_has_floor():
    blocker = job(
        409, "qa", RUNNING, created_min=7, started_min=6, lane=2, group="devices", preset="qa"
    )
    blocked = job(413, "qa", QUEUED, created_min=2, group="devices", preset="qa")
    rows = {r.job.id: r for r in rows_for([blocker, blocked], wk=workers("idle", "busy:409"))}
    b = rows[413]
    assert b.reason == "blocked_by_group"
    assert b.blocked_by.job_id == 409 and b.blocked_by.group == "devices"
    blocker_finish = rows[409].estimate.finish_at
    assert b.estimate.finish_at >= blocker_finish + timedelta(seconds=540)
    assert b.estimate.wait_seconds == rows[409].estimate.remaining_seconds
    # 레인 1 은 비어 있지만 그룹 때문에 못 올라간다 — 신뢰도는 group wait
    assert confidence(b.estimate.source, b.estimate.sample_count, group_wait=True) == "group wait"


def test_overdue_running_job_has_null_finish_and_reason_overdue():
    j = job(1, state=RUNNING, created_min=10, started_min=9)  # expected 400 < elapsed 540
    row = rows_for([j], busy=[1])[0]
    assert row.estimate.overdue is True and row.estimate.stuck is False
    assert row.estimate.finish_at is None
    assert row.reason == "overdue"
    assert row.estimate.remaining_seconds == 30


def test_stuck_by_multiplier_and_by_no_output():
    long = job(1, state=RUNNING, created_min=25, started_min=25, last_output_at=ago(seconds=5))
    assert rows_for([long], busy=[1])[0].reason == "stuck"  # 1500s > 3×400
    silent = job(2, state=RUNNING, created_min=6, started_min=5, last_output_at=ago(seconds=300))
    assert rows_for([silent], busy=[2])[0].reason == "stuck"  # 300s > 240s 무출력
    talking = job(3, state=RUNNING, created_min=6, started_min=5, last_output_at=ago(seconds=10))
    assert rows_for([talking], busy=[3])[0].reason == "running"


def test_materializing_phase_is_its_own_reason_and_not_stuck():
    j = job(1, state=RUNNING, created_min=6, started_min=5, phase=PHASE_MATERIALIZING)
    row = rows_for([j], busy=[1])[0]
    assert row.reason == "materializing" and row.estimate.stuck is False


def test_upload_stalled_reason():
    fresh = replace(
        job(1, state=UPLOADING, created_min=1),
        source=Source(mode="tree", last_received_at=ago(seconds=5)),
    )
    stalled = replace(
        job(2, state=UPLOADING, created_min=3),
        source=Source(mode="tree", last_received_at=ago(seconds=90)),
    )
    rows = rows_for([fresh, stalled])
    assert [r.reason for r in rows] == ["uploading", "upload_stalled"]


def test_not_scheduled_when_idle_lane_sits_for_10s():
    j = job(1, created_min=1, queued_min=1)
    assert rows_for([j], wk=workers("idle", since=ago(minutes=5)))[0].reason == "not_scheduled"
    j2 = job(2, created_min=1, queued_min=0)  # 방금 queued 가 됐다
    assert rows_for([j2], wk=workers("idle", since=ago(minutes=5)))[0].reason == "waiting_for_lane"
    # 두 잡이 있으면 첫 잡만 not_scheduled, 둘째는 정상 대기
    rows = rows_for([j, j2], wk=workers("idle", since=ago(minutes=5)))
    assert [r.reason for r in rows] == ["not_scheduled", "waiting_for_lane"]


def test_waiting_for_lane_names_the_job_ahead():
    jobs = [job(1, state=RUNNING, created_min=2, started_min=1), job(2, created_min=1)]
    row = rows_for(jobs, busy=[1])[1]
    assert row.reason == "waiting_for_lane" and row.ahead_job_id == 1


def test_confidence_rule():
    assert confidence("measured", 7) == "high"
    assert confidence("measured", 3) == "med"
    assert confidence("preset", 1) == "low"
    assert confidence("default", 0) == "low"
    assert confidence("measured", 9, overdue=True) == "overdue"


def test_expected_falls_back_preset_then_default():
    assert expected_for("qa", QA, {}, CFG) == (540.0, "preset", 0)
    assert expected_for("x", None, {}, CFG) == (600.0, "default", 0)
    assert expected_for("deploy-dev", DEPLOY, MEDIANS, CFG) == (600.0, "measured", 3)


# ── 뮤테이션 표적 ────────────────────────────────────────────────────────────


def test_remaining_floor_never_goes_negative():
    assert remaining_seconds(400, 1000, CFG) == 30
    assert remaining_seconds(400, 100, CFG) == 300
    assert remaining_seconds(400, None, CFG) == 400


def test_join_key_differs_by_inputs_and_source_identity():
    a = join_key("gate", {"scope": "full"}, "9f8e")
    assert a == join_key("gate", {"scope": "full"}, "9f8e")
    assert a != join_key("gate", {"scope": "fast"}, "9f8e")
    assert a != join_key("gate", {"scope": "full"}, "0000")
    assert a != join_key("gate-fast", {"scope": "full"}, "9f8e")
    assert join_key("gate", {"b": 1, "a": 2}, None) == join_key("gate", {"a": 2, "b": 1}, None)
