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
    Median,
    Progress,
    Source,
    Step,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import (
    QueueConfig,
    StepMedian,
    compute_queue,
    confidence,
    eta_for_new,
    expected_for,
    join_key,
    medians_from,
    remaining_seconds,
    step_medians_from,
)


def rows_for(
    jobs,
    *,
    lanes=1,
    busy=None,
    paused=False,
    medians=MEDIANS,
    now=NOW,
    cfg=CFG,
    wk=None,
    progress=None,
    step_medians=None,
):
    wk = wk if wk is not None else default_workers(busy or [], lanes=lanes)
    return compute_queue(
        jobs,
        workers=wk,
        paused=paused,
        medians=medians,
        presets=PRESETS,
        cfg=cfg,
        now=now,
        progress=progress,
        step_medians=step_medians,
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
    assert row.estimate.quiet is False  # 방금 출력이 있었다


def test_compute_queue_without_step_medians_is_todays_rule():
    """`step_medians` 를 안 받으면 단계 이야기를 안 하는 잡과 같다 — 옛 규칙 그대로 돈다.

    (옛 이름: `test_stuck_by_multiplier_and_by_no_output`. 기존 호출자 — `rows_for` 픽스처 ·
    `render_text` · CLI — 가 전부 그대로 살아야 한다.)
    """
    long = job(1, state=RUNNING, created_min=25, started_min=25, last_output_at=ago(seconds=5))
    row = rows_for([long], busy=[1])[0]
    assert row.reason == "stuck" and row.estimate.stuck_code == "over_elapsed"  # 1500s > 3×400
    silent = job(2, state=RUNNING, created_min=6, started_min=5, last_output_at=ago(seconds=300))
    row = rows_for([silent], busy=[2])[0]
    assert row.reason == "stuck" and row.estimate.stuck_code == "no_output"  # 300s > 240s 무출력
    assert row.estimate.quiet is False  # 죽었다고 말했으면 「조용하다」고 또 말하지 않는다
    talking = job(3, state=RUNNING, created_min=6, started_min=5, last_output_at=ago(seconds=10))
    row = rows_for([talking], busy=[3])[0]
    assert row.reason == "running" and row.estimate.stuck_code is None


def test_materializing_phase_is_its_own_reason_and_not_stuck():
    j = job(1, state=RUNNING, created_min=6, started_min=5, phase=PHASE_MATERIALIZING)
    row = rows_for([j], busy=[1])[0]
    assert row.reason == "materializing" and row.estimate.stuck is False
    assert row.estimate.quiet is False


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


# ── M5f: 레인 배정은 레인 **번호**가 아니라 (worker, lane) 로 센다 ────────────
#
# 기본 풀에는 로컬 레인 1..N 과 모든 원격 `default` 워커의 레인 1..M 이 함께 들어온다
# (`server.pool_workers`). `rcm worker` 의 pool 기본값이 `default` 라 이건 예외가 아니라
# 기본 설정이다. 레인 번호로 키를 잡으면 로컬 레인 2 와 `build-02/2` 가 뭉개진다.


def mixed_lanes(*, held_lane2: bool = False, drop_local2: bool = False):
    """로컬 레인 1·2 + 원격 build-02 의 레인 1·2 — 서로 다른 레인 넷."""
    out = [WorkerInfo(lane=1, state="idle", since=ago(minutes=30))]
    if not drop_local2:
        out.append(
            WorkerInfo(lane=2, state="held" if held_lane2 else "idle", since=ago(minutes=30))
        )
    out += [
        WorkerInfo(lane=1, state="idle", since=ago(minutes=30), worker="build-02"),
        WorkerInfo(lane=2, state="idle", since=ago(minutes=30), worker="build-02"),
    ]
    return out


def test_four_lanes_across_a_local_and_a_remote_worker_are_four_lanes():
    """오늘은 [0, 0, 400, 400] — 2레인 풀과 바이트 단위로 같다."""
    jobs = [job(i, created_min=5 - i) for i in (1, 2, 3, 4)]
    rows = rows_for(jobs, wk=mixed_lanes())
    assert [r.estimate.wait_seconds for r in rows] == [0, 0, 0, 0]


def test_busy_job_is_attributed_to_its_own_worker_lane():
    """로컬 `#500` 이 레인 1 을 쓰는 것이 원격 `build-02/1` 을 막으면 안 된다."""
    running = job(500, state=RUNNING, created_min=10, started_min=5, lane=1)  # 로컬
    waiting = job(501, created_min=1)
    wk = [
        WorkerInfo(lane=1, state="busy", job_id=500, since=ago(minutes=5)),
        WorkerInfo(lane=1, state="idle", since=ago(minutes=30), worker="build-02"),
    ]
    row = {r.job.id: r for r in rows_for([running, waiting], wk=wk)}[501]
    assert row.estimate.wait_seconds == 0 and row.ahead_job_id is None


def test_ahead_job_id_names_the_lane_that_frees_first():
    """`lane_last_job` 도 뭉개져 「내가 누구 뒤인가」가 엉뚱한 잡을 가리킨다."""
    remote = replace(
        job(500, state=RUNNING, created_min=10, started_min=5, lane=1), worker_name="build-02"
    )
    local = job(501, state=RUNNING, created_min=10, started_min=1, lane=1)  # 340초 뒤 빔
    waiting = job(502, created_min=1)
    wk = [
        WorkerInfo(lane=1, state="busy", job_id=501, since=ago(minutes=1)),
        WorkerInfo(lane=1, state="busy", job_id=500, since=ago(minutes=5), worker="build-02"),
    ]
    row = {r.job.id: r for r in rows_for([remote, local, waiting], wk=wk)}[502]
    assert row.ahead_job_id == 500 and row.estimate.wait_seconds == 100  # 먼저 비는 레인


def test_idle_since_counts_every_idle_lane_not_every_lane_number():
    """진짜 idle 레인 셋인데 키가 둘이면 세 번째 잡이 not_scheduled 를 놓친다."""
    jobs = [job(i, created_min=1, queued_min=1) for i in (1, 2, 3)]
    wk = [
        WorkerInfo(lane=1, state="idle", since=ago(minutes=5)),
        WorkerInfo(lane=1, state="idle", since=ago(minutes=5), worker="build-02"),
        WorkerInfo(lane=2, state="idle", since=ago(minutes=5), worker="build-02"),
    ]
    assert [r.reason for r in rows_for(jobs, wk=wk)] == ["not_scheduled"] * 3


def test_removing_a_held_lane_actually_changes_the_eta():
    """오늘은 뺀 것과 안 뺀 것이 완전히 같다 — 키 `2` 를 원격이 다시 채우기 때문이다."""
    jobs = [job(i, created_min=5 - i) for i in (1, 2, 3, 4)]
    with_all = [r.estimate.wait_seconds for r in rows_for(jobs, wk=mixed_lanes())]
    without = [r.estimate.wait_seconds for r in rows_for(jobs, wk=mixed_lanes(drop_local2=True))]
    assert with_all == [0, 0, 0, 0] and without == [0, 0, 0, 400]


# ── M5f: 보류 레인(held)과 사유 held_by_load ────────────────────────────────


def held(lane: int, *, worker: str | None = None, code: str = "cpu_busy"):
    return WorkerInfo(
        lane=lane,
        state="held",
        since=ago(minutes=2),
        worker=worker,
        hold_code=code,
        hold_detail={"cpu_busy": 92.4} if code == "cpu_busy" else None,
        held_since=ago(minutes=2),
    )


def test_a_held_lane_is_live_but_not_schedulable():
    """`down` 이 아니므로 살아 있다 — 그러나 그리디에서는 빠진다(언제 열릴지 모른다)."""
    jobs = [job(1, created_min=2), job(2, created_min=1)]
    wk = [WorkerInfo(lane=1, state="idle", since=ago(minutes=30)), held(2)]
    rows = rows_for(jobs, wk=wk)
    assert [r.estimate.wait_seconds for r in rows] == [0, 400]  # 레인 하나만 센다
    assert [r.reason for r in rows] == ["not_scheduled", "held_by_load"]


def test_one_waiting_job_consumes_one_held_lane():
    """뒤 잡은 정직하게 `waiting_for_lane` 이다 — 보류 레인은 하나뿐이다."""
    jobs = [job(i, created_min=4 - i, queued_min=0) for i in (1, 2, 3)]
    wk = [WorkerInfo(lane=1, state="busy", job_id=9, since=ago(minutes=1)), held(2)]
    running = job(9, state=RUNNING, created_min=5, started_min=1)
    rows = {r.job.id: r for r in rows_for([running, *jobs], wk=wk)}
    assert rows[1].reason == "held_by_load"
    assert rows[2].reason == "waiting_for_lane" and rows[3].reason == "waiting_for_lane"


def test_group_blocking_beats_held_by_load():
    blocker = job(409, "qa", RUNNING, created_min=7, started_min=6, group="devices", preset="qa")
    blocked = job(413, "qa", QUEUED, created_min=2, group="devices", preset="qa")
    wk = [WorkerInfo(lane=1, state="busy", job_id=409, since=ago(minutes=6)), held(2)]
    rows = {r.job.id: r for r in rows_for([blocker, blocked], wk=wk)}
    assert rows[413].reason == "blocked_by_group"


def test_a_held_lane_never_triggers_not_scheduled():
    """`idle_since` 는 `WORKER_IDLE` 만 담는다 — 보류 레인이 스케줄러 이상 알람을 울리면 안 된다."""
    j = job(1, created_min=1, queued_min=1)
    wk = [WorkerInfo(lane=1, state="busy", job_id=9, since=ago(minutes=5)), held(2)]
    running = job(9, state=RUNNING, created_min=6, started_min=5)
    rows = {r.job.id: r for r in rows_for([running, j], wk=wk)}
    assert rows[1].reason == "held_by_load"


def test_lane_one_down_and_lane_two_held_gives_no_eta():
    """`open` 이 비고 `live` 는 안 빈 새 경우 — 시작할 수 없는 잡에 시각을 주지 않는다.

    진짜 원인인 죽은 레인 1 은 `workers[]` 의 `down` 필과 머리줄에 보인다.
    """
    j = job(1, created_min=1)
    wk = [WorkerInfo(lane=1, state="down", error="ENOSPC", since=ago(minutes=5)), held(2)]
    row = rows_for([j], wk=wk)[0]
    assert row.reason == "held_by_load"
    assert row.estimate.wait_seconds is None and row.estimate.finish_at is None


def test_all_lanes_down_still_says_worker_down():
    row = rows_for([job(1, created_min=1)], wk=workers("down:ENOSPC", "down:ENOSPC"))[0]
    assert row.reason == "worker_down"


def test_held_by_load_is_not_in_the_not_moving_list():
    """의도된·자가 치유되는 상태다. 늘 켜져 있으면 worker_down·stuck 이 묻힌다(결정 45)."""
    from remote_ci_monitor.core.model import ACTIONABLE_REASONS, REASON_HELD_BY_LOAD

    assert REASON_HELD_BY_LOAD not in ACTIONABLE_REASONS


# ── 멈춤 / 조용함 판정 — 단계 실측 (2026-09-15 사고) ───────────────────────────
#
# 규칙: 침묵만으로는 `stuck` 이 아니다. 침묵은 `quiet`(관측, 경보 아님)다. 죽었다는 말은
# **현재 단계의 실측**이 있을 때 그 실측으로만 한다. 단계 이야기를 아예 안 하는 잡만
# 옛 침묵 규칙으로 떨어진다.

KEY = "gate:full"


def stalling(
    *,
    elapsed: float,
    expected: float,
    silent: float | None = 0.0,
    current: str | None = None,
    current_seconds: float | None = None,
    steps: tuple[str, ...] | None = None,
    step_medians: dict[str, StepMedian] | None = None,
    state: str = RUNNING,
    phase: str | None = None,
):
    """도는 잡 한 줄 — 멈춤/조용함 판정에 필요한 것만 조립한다.

    `steps=None` 이면 Progress 자체가 없다(마커를 안 찍는 잡). `silent=None` 이면
    `last_output_at` 이 아예 없다(한 줄도 안 뱉었다).
    """
    started = NOW - timedelta(seconds=elapsed)
    j = replace(
        job(1, KEY, state, lane=1),
        created_at=started,
        queued_at=started,
        started_at=started,
        last_output_at=None if silent is None else NOW - timedelta(seconds=silent),
        phase=phase,
    )
    progress = None
    if steps is not None:
        progress = {
            1: Progress(
                phase="executing",
                steps=tuple(
                    Step(index=i + 1, name=name, state="done", ok=True, seconds=1.0)
                    for i, name in enumerate(steps)
                ),
                steps_total=len(steps),
                steps_done=len(steps),
                current_name=current,
                current_seconds=current_seconds,
            )
        }
    return rows_for(
        [j],
        busy=[1],
        medians={KEY: Median(seconds=expected, wait_seconds=0.0, sample_count=6)},
        progress=progress,
        step_medians={KEY: step_medians} if step_medians else None,
    )[0]


def measured(seconds: float, samples: int = 3) -> dict[str, StepMedian]:
    return StepMedian(seconds=seconds, sample_count=samples)


def run_of(*pairs, state: str = "done") -> tuple[Step, ...]:
    """(이름, 초) 쌍으로 한 실행의 단계 목록을 만든다."""
    return tuple(
        Step(index=i + 1, name=name, state=state, ok=True, seconds=seconds)
        for i, (name, seconds) in enumerate(pairs)
    )


def test_a_step_exactly_at_the_multiplier_is_not_stuck():
    """C-1 — 경계는 `>` 다. `>=` 로 두면 정상 실행이 매번 빨개진다."""
    common = dict(elapsed=1900, expected=3600, current="test", steps=("lint", "test"))
    at = stalling(**common, current_seconds=1800.0, step_medians={"test": measured(600.0)})
    assert at.estimate.stuck is False and at.estimate.stuck_code is None
    assert at.estimate.step_expected_seconds == 600.0
    over = stalling(**common, current_seconds=1800.001, step_medians={"test": measured(600.0)})
    assert over.estimate.stuck is True and over.estimate.stuck_code == "over_step"


def test_a_half_second_step_never_goes_stuck_in_two_seconds():
    """C-2 — 단계 임계의 하한은 `no_output_seconds` 다.

    침묵 규칙이 낼 수 있었던 것보다 빨리 죽었다고 말하지 않는다. 하한이 없으면 짧은 단계를
    가진 모든 프리셋이 매 실행마다 1.5초 만에 빨개진다.
    """
    common = dict(elapsed=40, expected=120, current="lint", steps=("lint",))
    short = measured(0.5)
    assert (
        stalling(**common, current_seconds=2.0, step_medians={"lint": short}).estimate.stuck
        is False
    )
    at = stalling(**common, current_seconds=240.0, step_medians={"lint": short})
    assert at.estimate.stuck is False  # 경계는 `>`
    over = stalling(**common, current_seconds=240.001, step_medians={"lint": short})
    assert over.estimate.stuck is True and over.estimate.stuck_code == "over_step"


def test_a_zero_second_median_does_not_make_every_step_stuck():
    """C-3 — 0 은 실측이다(지어내지 않는다). 곱셈으로는 못 막고 하한만이 막는다."""
    runs = {KEY: [run_of(("noop", 0.0)), run_of(("noop", 0.0)), run_of(("noop", 0.0))]}
    table = step_medians_from(runs, CFG)
    assert table[KEY]["noop"] == StepMedian(seconds=0.0, sample_count=3)
    row = stalling(
        elapsed=10,
        expected=600,
        current="noop",
        current_seconds=0.2,
        steps=("noop",),
        step_medians=table[KEY],
    )
    assert row.estimate.stuck is False


def test_two_samples_are_not_enough_for_a_step_median():
    """C-4 — 2개는 「중앙값」이 아니라 「둘 중 하나」다. 호출자가 거르게 하지 않는다."""
    runs = {KEY: [run_of(("build", 100.0)), run_of(("build", 140.0))]}
    assert step_medians_from(runs, CFG) == {}


def test_three_samples_give_the_median_not_the_mean():
    """C-5 — `mean` 을 쓰면 한 번의 느린 실행이 임계를 두 배로 만든다."""
    runs = {KEY: [run_of(("build", 100.0)), run_of(("build", 140.0)), run_of(("build", 400.0))]}
    assert step_medians_from(runs, CFG)[KEY]["build"] == StepMedian(seconds=140.0, sample_count=3)


def test_a_step_name_the_history_never_saw_only_goes_quiet():
    """C-6 — 스크립트에 단계 하나 추가한 날 전부 빨개지면 안 된다."""
    row = stalling(
        elapsed=1000,
        expected=1800,
        silent=420,
        current="e2e",
        current_seconds=900,
        steps=("lint", "test", "build"),
        step_medians={"lint": measured(60.0), "test": measured(600.0)},
    )
    assert row.estimate.stuck is False and row.estimate.stuck_code is None
    assert row.estimate.quiet is True and row.reason == "quiet"
    assert row.estimate.step_expected_seconds is None


def test_a_conditional_step_counts_only_the_runs_that_had_it():
    """C-7 — 「단계의 신원은 이름」이 무너지는 첫 자리. 번호로 맞추면 안 된다."""
    runs = {
        KEY: [
            run_of(("lint", 60.0), ("e2e", 300.0)),
            run_of(("lint", 62.0), ("e2e", 340.0)),
            run_of(("lint", 58.0)),
            run_of(("lint", 61.0)),
            run_of(("lint", 59.0)),
        ]
    }
    table = step_medians_from(runs, CFG)[KEY]
    assert "e2e" not in table and table["lint"].sample_count == 5
    row = stalling(
        elapsed=900,
        expected=1800,
        current="e2e",
        current_seconds=700,
        steps=("lint", "e2e"),
        step_medians=table,
    )
    assert row.estimate.stuck is False


def test_a_looping_step_contributes_one_sample_per_run():
    """C-8 — `sample_count` 는 **실행 수**다.

    출현 수를 세면 루프 도는 스크립트 하나가 혼자 표본을 채워 한 번의 실행이 「평소」를
    정의하게 된다. 비교 대상인 `current_seconds` 는 한 번의 출현이다.
    """
    one = {KEY: [run_of(("retry", 10.0), ("retry", 20.0), ("retry", 30.0))]}
    assert step_medians_from(one, CFG) == {}  # 실행 1개 < step_min_samples

    three = {
        KEY: [
            run_of(("retry", 10.0), ("retry", 20.0), ("retry", 30.0)),  # 이 실행의 중앙값 20
            run_of(("retry", 40.0), ("retry", 50.0), ("retry", 60.0)),  # 50
            run_of(("retry", 70.0), ("retry", 80.0), ("retry", 90.0)),  # 80
        ]
    }
    assert step_medians_from(three, CFG)[KEY]["retry"] == StepMedian(seconds=50.0, sample_count=3)


def test_a_none_current_seconds_is_zero_not_an_error():
    """C-9 · C-10 — `None` 을 큰 수로 읽거나 예외를 내면 `/api/status` 가 500 이 된다."""
    unknown = stalling(
        elapsed=1000,
        expected=1800,
        silent=420,
        current="build",
        current_seconds=None,
        steps=("build",),
        step_medians={"build": measured(600.0)},
    )
    assert unknown.estimate.stuck is False and unknown.estimate.quiet is True
    fresh = stalling(
        elapsed=1000,
        expected=1800,
        current="build",
        current_seconds=0.0,
        steps=("build",),
        step_medians={"build": measured(600.0)},
    )
    assert fresh.estimate.stuck is False


def test_a_job_with_no_steps_still_falls_back_to_silence():
    """C-11 · C-12 — 마커를 전혀 안 찍는 잡의 안전망이 여기 하나뿐이다."""
    none = stalling(elapsed=500, expected=1800, silent=300, steps=None)
    assert none.estimate.stuck is True and none.estimate.stuck_code == "no_output"
    assert none.estimate.quiet is False and none.reason == "stuck"
    # Progress 는 있는데 단계가 하나도 없다 — `progress is None` 만 보면 여기가 샌다
    empty = stalling(elapsed=500, expected=1800, silent=300, steps=())
    assert empty.estimate.stuck is True and empty.estimate.stuck_code == "no_output"


def test_a_job_past_its_last_step_is_quiet_not_stuck():
    """C-13 — 선언한 단계를 다 끝낸 뒤의 침묵은 **의도된 구멍**이다.

    안전망은 `elapsed > 3 × expected` 와 프리셋 `timeout_seconds` 둘뿐이다.
    """
    row = stalling(
        elapsed=2000,
        expected=1800,
        silent=1200,
        current=None,
        steps=tuple(f"s{i}" for i in range(49)),
        step_medians={"s0": measured(60.0)},
    )
    assert row.estimate.stuck is False and row.estimate.quiet is True
    assert row.reason == "overdue"  # overdue 가 quiet 을 이긴다


def test_cancelling_is_neither_stuck_nor_quiet():
    """C-14 — 취소 중인 잡은 정의상 조용하다. `quiet` 를 내면 취소마다 회색 칩이 뜬다."""
    row = stalling(
        elapsed=10000,
        expected=600,
        silent=1000,
        current="build",
        current_seconds=6000,
        steps=("build",),
        step_medians={"build": measured(600.0)},
        state=CANCELLING,
    )
    assert row.estimate.stuck is False and row.estimate.quiet is False
    assert row.estimate.stuck_code is None and row.reason == "cancelling"
    assert row.estimate.overdue is True  # overdue 는 상태를 안 본다 — 옛 동작 그대로


def test_materializing_is_neither_stuck_nor_quiet():
    """C-15 — 48 MB 트리를 푸는 동안 출력이 없는 것은 정상이다."""
    row = stalling(elapsed=900, expected=1800, silent=900, steps=None, phase=PHASE_MATERIALIZING)
    assert row.estimate.stuck is False and row.estimate.quiet is False
    assert row.reason == "materializing"


def test_overdue_beats_quiet_in_the_reason():
    """C-16 — 뒤집으면 초과 실행이 회색 「조용함」 뒤에 숨는다."""
    row = stalling(
        elapsed=2000,
        expected=1800,
        silent=500,
        current="build",
        current_seconds=400,
        steps=("build",),
        step_medians={"build": measured(600.0)},
    )
    assert row.estimate.overdue is True and row.estimate.quiet is True
    assert row.estimate.stuck is False and row.reason == "overdue"


def test_stuck_and_quiet_are_mutually_exclusive():
    """C-17 — 둘 다 참이면 화면이 어느 색을 쓸지 서버가 안 정해 준 셈이다."""
    over_step = stalling(
        elapsed=3000,
        expected=6000,
        silent=500,
        current="build",
        current_seconds=2500,
        steps=("build",),
        step_medians={"build": measured(600.0)},
    )
    over_elapsed = stalling(elapsed=2000, expected=400, silent=500, steps=None)
    no_progress = stalling(elapsed=500, expected=1800, silent=300, steps=None)
    no_median = stalling(
        elapsed=500,
        expected=1800,
        silent=300,
        current="build",
        current_seconds=300,
        steps=("build",),
    )
    for row in (over_step, over_elapsed, no_progress, no_median):
        assert not (row.estimate.stuck and row.estimate.quiet)
    assert [r.estimate.stuck for r in (over_step, over_elapsed, no_progress)] == [True] * 3
    assert over_step.estimate.stuck_code == "over_step"
    # ② 는 침묵 **이면서** 3배 초과다. 단계 이야기를 안 하는 잡이라 근거는 침묵이 먼저 잡는다
    assert over_elapsed.estimate.stuck_code == "no_output"
    assert no_progress.estimate.stuck_code == "no_output"
    assert no_median.estimate.stuck is False and no_median.estimate.quiet is True
    # 조용하지 않은데 3배를 넘었으면 근거는 경과다
    talking = stalling(elapsed=2000, expected=400, silent=0, steps=None)
    assert talking.estimate.stuck is True and talking.estimate.stuck_code == "over_elapsed"


def test_a_job_that_never_printed_measures_silence_from_its_start():
    """C-18 — `last_output_at` 이 없으면 침묵은 시작 시각부터 잰다(옛 동작 그대로)."""
    young = stalling(elapsed=5, expected=1800, silent=None, steps=None)
    assert young.estimate.stuck is False and young.estimate.quiet is False
    assert young.reason == "running"
    old = stalling(elapsed=300, expected=1800, silent=None, steps=None)
    assert old.estimate.stuck is True and old.estimate.stuck_code == "no_output"


def test_a_clock_that_went_backwards_raises_no_alarm():
    """C-19 — 노트북 뚜껑을 닫았다 여는 것만으로 화면이 빨개지면 안 된다."""
    row = stalling(
        elapsed=-120,
        expected=1800,
        silent=-60,
        current="build",
        current_seconds=-30.0,
        steps=("build",),
        step_medians={"build": measured(600.0)},
    )
    assert row.estimate.elapsed_seconds == -120.0
    assert row.estimate.overdue is False and row.estimate.stuck is False
    assert row.estimate.quiet is False and row.reason == "running"
    assert row.estimate.remaining_seconds >= CFG.floor_remaining_seconds


def test_the_2026_09_15_gate_incident_is_quiet_not_stuck():
    """C-20 — 이 한 줄이 이 작업의 존재 이유다.

    경과 17m 24s · 예상 18m · 7m 53s 무출력 · 단계 49/49 진행 중이었고 화면은 「멈춘 듯」에
    「예상의 1배」를 붙였다. `build web` 은 평소 9분 걸리는 단계다.
    """
    row = stalling(
        elapsed=1044,
        expected=1080.0,
        silent=473,
        current="build web",
        current_seconds=473.0,
        steps=("lint", "test", "build web"),
        step_medians={"build web": measured(540.0, samples=5)},
    )
    assert row.estimate.stuck is False and row.estimate.stuck_code is None
    assert row.estimate.step_expected_seconds == 540.0
    assert row.estimate.overdue is False  # 1044 < 1080
    assert row.estimate.quiet is True and row.reason == "quiet"

    from remote_ci_monitor.core.status import estimate_json

    out = estimate_json(row.estimate)
    assert out["quiet"] is True
    assert out["stuck_code"] is None and out["step_expected_seconds"] == 540.0


def test_the_same_incident_one_minute_later_reads_as_overdue():
    """C-21 — 사고 기록의 「예상 약 17m」쪽 해석. 둘 다 「멈춘 듯」과 「1배」가 사라진다."""
    row = stalling(
        elapsed=1044,
        expected=1020.0,
        silent=473,
        current="build web",
        current_seconds=473.0,
        steps=("lint", "test", "build web"),
        step_medians={"build web": measured(540.0, samples=5)},
    )
    assert row.estimate.overdue is True and row.estimate.quiet is True
    assert row.estimate.stuck is False and row.reason == "overdue"


def test_a_running_step_is_not_a_sample():
    """C-22 — 도는 중인 단계는 아직 「평소 얼마나 걸리나」가 아니다."""
    one = run_of(("build", 600.0)) + (
        Step(index=2, name="deploy", state="running", ok=None, seconds=900.0),
    )
    table = step_medians_from({KEY: [one, one, one]}, CFG)[KEY]
    assert set(table) == {"build"}


def test_a_step_with_no_duration_is_skipped():
    """C-23 — `statistics.median([600, None, ...])` 로 TypeError 가 나면 안 된다."""
    runs = {
        KEY: [
            run_of(("build", 600.0)),
            (Step(index=1, name="build", state="done", ok=True, seconds=None),),
            run_of(("build", 640.0)),
            run_of(("build", 620.0)),
        ]
    }
    assert step_medians_from(runs, CFG)[KEY]["build"].sample_count == 3


def test_step_medians_from_handles_empty_input():
    """C-24 — 빈 key 는 아예 넣지 않는다(빈 dict 를 남기면 「몇 개를 쟀나」가 거짓말이 된다)."""
    assert step_medians_from({}, CFG) == {}
    assert step_medians_from({KEY: []}, CFG) == {}
    assert step_medians_from({KEY: [(), (), ()]}, CFG) == {}


def test_a_step_closed_by_job_end_is_still_a_sample():
    """C-25 — 끝 마커가 없는 마지막 단계는 `finished_at` 이 닫는다.

    「끝 마커가 없으니 버린다」로 고치면 `gate` 의 마지막 단계는 표본을 영원히 못 채우고,
    사고가 난 바로 그 단계가 판정 대상에서 빠진다. 중앙값이 실제 단계보다 길어지는 쪽이라
    오경보가 줄어드는 방향이다.
    """
    from remote_ci_monitor.core.progress import Marker, progress_from_markers

    runs = []
    for extra in (30, 60, 90):  # 산출물 업로드에 걸린 시간이 실행마다 다르다
        start = NOW - timedelta(seconds=1000)
        markers = [Marker(at=start, kind="step", value="build web")]
        p = progress_from_markers(
            markers,
            started_at=start,
            finished_at=start + timedelta(seconds=600 + extra),
            now=NOW,
            exit_code=0,
        )
        assert p.steps[0].state == "done"
        runs.append(p.steps)
    table = step_medians_from({KEY: runs}, CFG)[KEY]
    assert table["build web"] == StepMedian(seconds=660.0, sample_count=3)


def test_jobs_with_no_markers_produce_no_step_medians():
    """C-62 — 마커가 한 줄도 없는 잡은 아무 표본도 안 낸다. 예외도 없다."""
    from remote_ci_monitor.core.progress import progress_from_markers

    start = NOW - timedelta(seconds=1000)
    silent = progress_from_markers(
        [], started_at=start, finished_at=NOW, now=NOW, exit_code=0
    ).steps
    assert silent == ()
    assert step_medians_from({"silent:job": [silent, silent, silent]}, CFG) == {}


def test_quiet_is_not_in_the_not_moving_list():
    """`held_by_load` 와 같은 종류다 — 의도되지 않았지만 경보가 아니다.

    올리면 오늘의 빨간 소음이 이름만 바꿔 「확인이 필요한 작업」에 그대로 남는다.
    """
    from remote_ci_monitor.core.model import ACTIONABLE_REASONS, REASON_QUIET

    assert REASON_QUIET not in ACTIONABLE_REASONS
