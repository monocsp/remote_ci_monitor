"""풀(M5b-1) — 순수 규칙: `split_by_pool` · 풀별 `compute_queue`(그룹 배제는 풀 안에서) ·
`eta_for_new(pool=)` · `Job.pool`/`Preset.pool`/`Preset.pools`/`DEFAULT_POOL` 기본값.
명세는 docs/m5-workplan.md 「M5b. 원격 워커」 모델 · 프로토콜(claim) · 순서 3(M5b-1).
구현 전이라 빨간 것이 정상이다.

`compute_queue`·`medians_from` 의 시그니처는 그대로다 — 호출자가 그 풀의 잡·워커만 넘긴다.
잡은 `jobfactory.job()` 으로 만들고 `pool=` 은 `**kw` 로 넘긴다. 시각은 고정 NOW.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from typing import Any

from jobfactory import CFG, GATE, MEDIANS, NOW, PRESETS, QA, default_workers, job, workers
from remote_ci_monitor.core.model import (
    DEFAULT_POOL,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    Job,
    Preset,
    QueueRow,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import compute_queue, eta_for_new, medians_from, split_by_pool

# ── 도우미 ───────────────────────────────────────────────────────────────────


def rows_for(
    jobs: Sequence[Job], *, wk: Sequence[WorkerInfo] | None = None, medians=MEDIANS
) -> list[QueueRow]:
    """test_queue.rows_for 와 같은 모양. 워커를 안 주면 idle 레인 하나."""
    wk = wk if wk is not None else default_workers([])
    return compute_queue(
        jobs, workers=wk, paused=False, medians=medians, presets=PRESETS, cfg=CFG, now=NOW
    )


def eta(
    jobs: Sequence[Job],
    *,
    pool: str | None = None,
    preset: Preset = GATE,
    key: str = "gate:full",
    wk: Sequence[WorkerInfo] | None = None,
) -> tuple[QueueRow, int]:
    """`pool=None` 이면 인자를 아예 안 넘긴다(기본값이 기본 풀인지 본다)."""
    kw: dict[str, Any] = {} if pool is None else {"pool": pool}
    return eta_for_new(
        jobs,
        preset=preset,
        key=key,
        workers=wk if wk is not None else default_workers([]),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
        **kw,
    )


def sample(id: int, key: str, job_seconds: float, *, pool: str = DEFAULT_POOL) -> Job:
    """하루 전에 시작해 `job_seconds` 만에 성공한 표본 잡."""
    started = NOW - timedelta(days=1)
    return replace(
        job(id, key, SUCCEEDED, pool=pool),
        created_at=started - timedelta(seconds=20),
        started_at=started,
        finished_at=started + timedelta(seconds=job_seconds),
    )


# ── 모델 기본값 ───────────────────────────────────────────────────────────────


def test_default_pool_constant_and_job_pool_default():
    assert DEFAULT_POOL == "default"
    assert job(1).pool == DEFAULT_POOL  # 팩토리는 pool 을 안 넘긴다 — 모델 기본값이다
    assert job(2, pool="linux").pool == "linux"
    assert replace(job(3), pool="mac2").pool == "mac2"


def test_preset_pool_and_pools_defaults():
    assert GATE.pool == DEFAULT_POOL and GATE.pools == ()
    assert QA.pool == DEFAULT_POOL and QA.pools == ()
    lin = Preset(name="lin", argv=("sh", "-c", "true"), pool="linux", pools=("default",))
    assert lin.pool == "linux" and lin.pools == ("default",)
    only = Preset(name="strict", argv=("sh", "-c", "true"), pool="linux")
    assert only.pool == "linux" and only.pools == ()  # 기본 풀은 pools 에 없어도 허용(서버 규칙)


# ── split_by_pool ─────────────────────────────────────────────────────────────


def test_split_by_pool_puts_default_first_then_sorted_names_and_keeps_order():
    jobs = [
        job(1, pool="mac2"),
        job(2, pool="linux"),
        job(3),
        job(4, pool="arm"),
        job(5, pool="linux"),
        job(6),
    ]
    out = split_by_pool(jobs)
    assert list(out) == ["default", "arm", "linux", "mac2"]  # 기본 풀 먼저, 나머지는 이름순
    assert [j.id for j in out["default"]] == [3, 6]  # 풀 안에서는 입력 순서 그대로
    assert [j.id for j in out["linux"]] == [2, 5]
    assert [j.id for j in out["mac2"]] == [1] and [j.id for j in out["arm"]] == [4]
    assert sum(len(v) for v in out.values()) == len(jobs)  # 잡을 잃지도 늘리지도 않는다
    assert all(j.pool == name for name, js in out.items() for j in js)


def test_split_by_pool_empty_and_without_default_jobs():
    empty = split_by_pool([])
    assert set(empty) <= {DEFAULT_POOL}  # 빈 dict 이거나 빈 기본 풀뿐 — 없는 풀을 지어내지 않는다
    assert all(v == [] for v in empty.values())
    out = split_by_pool([job(1, pool="linux"), job(2, pool="linux")])
    assert [k for k in out if k != DEFAULT_POOL] == ["linux"]
    assert [j.id for j in out["linux"]] == [1, 2]
    assert not out.get(DEFAULT_POOL)  # 기본 풀 잡이 없으면 (있더라도) 빈 목록


def test_split_by_pool_does_not_filter_by_state():
    """상태로 거르지 않는다 — 표본(종료 잡)도 같은 함수로 풀별로 나눈다."""
    done = sample(1, "gate:full", 100, pool="linux")
    running = job(2, state=RUNNING, created_min=2, started_min=1, pool="linux")
    out = split_by_pool([done, running, job(3)])
    assert [j.id for j in out["linux"]] == [1, 2]
    assert [j.id for j in out["default"]] == [3]


# ── 풀별 compute_queue ────────────────────────────────────────────────────────


def test_group_exclusion_applies_within_a_pool_only():
    """기본 풀에서 도는 `devices` 잡은 리눅스 풀의 `devices` 를 막지 않는다(그룹은 풀 단위 자원)."""
    blocker = job(409, "qa", RUNNING, created_min=7, started_min=6, group="devices", preset="qa")
    blocked = job(413, "qa", QUEUED, created_min=2, group="devices", preset="qa")
    free = job(
        414, "qa", QUEUED, created_min=1, queued_min=0, group="devices", preset="qa", pool="linux"
    )
    by_pool = split_by_pool([blocker, blocked, free])

    default_rows = {r.job.id: r for r in rows_for(by_pool["default"], wk=default_workers([409]))}
    assert set(default_rows) == {409, 413}
    assert default_rows[413].reason == "blocked_by_group"
    assert default_rows[413].blocked_by is not None and default_rows[413].blocked_by.job_id == 409

    linux_rows = rows_for(by_pool["linux"], wk=workers("idle", since=NOW))
    assert [r.job.id for r in linux_rows] == [414]
    row = linux_rows[0]
    assert row.reason == "waiting_for_lane" and row.blocked_by is None
    assert row.position == 1 and row.estimate.wait_seconds == 0
    assert row.estimate.finish_at == NOW + timedelta(seconds=540)  # qa 프리셋 expected


def test_positions_and_waits_restart_per_pool():
    jobs = [
        job(1, created_min=3),
        job(2, created_min=2, pool="linux"),
        job(3, created_min=1),
        job(4, created_min=1, pool="linux"),
    ]
    by_pool = split_by_pool(jobs)
    default_rows = rows_for(by_pool["default"], wk=workers("idle", since=NOW))
    linux_rows = rows_for(by_pool["linux"], wk=workers("idle", since=NOW))
    assert [(r.job.id, r.position) for r in default_rows] == [(1, 1), (3, 2)]
    assert [(r.job.id, r.position) for r in linux_rows] == [(2, 1), (4, 2)]
    # 기본 풀의 둘째 잡은 앞의 기본 풀 잡(400초)만 기다린다 — 리눅스 잡의 소요는 안 섞인다
    assert default_rows[1].estimate.wait_seconds == 400 and default_rows[1].ahead_job_id == 1
    assert linux_rows[1].estimate.wait_seconds == 400 and linux_rows[1].ahead_job_id == 2


def test_pool_without_workers_is_worker_down_with_null_times():
    """M5b-1 의 원격 풀: 워커가 아직 없다 → 시작 못 하는 잡에 시각을 주지 않는다(fail-open 금지)."""
    rows = rows_for([job(1, created_min=1, pool="linux")], wk=[])
    assert [r.reason for r in rows] == ["worker_down"]
    assert rows[0].position == 1
    assert rows[0].estimate.finish_at is None and rows[0].estimate.wait_seconds is None
    assert rows[0].estimate.expected_seconds == 400  # 기대치는 여전히 안다


# ── eta_for_new(pool=) ────────────────────────────────────────────────────────


def test_eta_for_new_in_an_empty_linux_pool_ignores_default_pool_jobs():
    jobs = [
        job(1, state=RUNNING, created_min=2, started_min=2),
        job(2, created_min=1),
        job(3, "deploy-dev", created_min=1),
    ]
    row, ahead = eta(jobs, pool="linux", wk=workers("idle", since=NOW))
    assert row.job.pool == "linux"  # 가상 잡이 풀을 싣는다
    assert row.position == 1 and ahead == 0
    assert row.estimate.wait_seconds == 0 and row.ahead_job_id is None
    assert row.estimate.finish_at == NOW + timedelta(seconds=400)
    # 같은 잡 목록으로 기본 풀을 물으면 예전 답 그대로(실행 중 1 + 대기 2 뒤)
    row, ahead = eta(jobs, pool="default", wk=default_workers([1]))
    assert row.job.pool == DEFAULT_POOL and row.position == 3 and ahead == 3
    assert row.estimate.wait_seconds == 280 + 400 + 600


def test_eta_for_new_defaults_to_the_default_pool_and_skips_other_pools():
    jobs = [job(1, created_min=3, pool="linux"), job(2, created_min=2, pool="linux")]
    row, ahead = eta(jobs)  # pool 인자 생략
    assert row.job.pool == DEFAULT_POOL
    assert row.position == 1 and ahead == 0 and row.estimate.wait_seconds == 0


def test_eta_for_new_in_a_pool_without_workers_has_no_finish_time():
    jobs = [job(1, created_min=3, pool="linux")]
    row, ahead = eta(jobs, pool="linux", wk=[])
    assert row.position == 2 and ahead == 1  # 리눅스 풀의 잡 하나가 앞에 있다
    assert row.reason == "worker_down"
    assert row.estimate.finish_at is None and row.estimate.wait_seconds is None


def test_eta_for_new_ghost_id_is_unique_across_pools():
    jobs = [job(7, pool="linux"), job(9)]
    row, _ = eta(jobs, pool="linux", wk=workers("idle", since=NOW))
    assert row.job.id not in {7, 9}


# ── 풀별 중앙값 ───────────────────────────────────────────────────────────────


def test_medians_are_per_pool_when_callers_split_the_samples():
    """같은 키라도 머신이 다르면 소요가 다르다 — 호출자가 풀별로 나눠 넘긴다."""
    samples = [
        sample(1, "gate:full", 100),
        sample(2, "gate:full", 100),
        sample(3, "gate:full", 300, pool="linux"),
        sample(4, "gate:full", 300, pool="linux"),
    ]
    by_pool = split_by_pool(samples)
    default = medians_from(by_pool["default"], NOW, CFG)["gate:full"]
    linux = medians_from(by_pool["linux"], NOW, CFG)["gate:full"]
    assert (default.seconds, default.sample_count) == (100.0, 2)
    assert (linux.seconds, linux.sample_count) == (300.0, 2)
    # 섞어서 계산하면 두 풀의 값이 뒤섞인다 — 나눠 넘겨야 하는 이유
    assert medians_from(samples, NOW, CFG)["gate:full"].sample_count == 4


def test_pool_medians_feed_the_estimate_of_that_pool_only():
    linux_medians = {"gate:full": replace(MEDIANS["gate:full"], seconds=120.0, sample_count=3)}
    default_row = rows_for([job(1, created_min=1)], wk=workers("idle", since=NOW))[0]
    linux_row = rows_for(
        [job(2, created_min=1, pool="linux")], wk=workers("idle", since=NOW), medians=linux_medians
    )[0]
    assert default_row.estimate.expected_seconds == 400
    assert linux_row.estimate.expected_seconds == 120
    assert linux_row.estimate.finish_at == NOW + timedelta(seconds=120)
