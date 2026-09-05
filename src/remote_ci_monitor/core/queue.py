"""큐 규칙 — FIFO · 살아 있는 레인 · concurrency 그룹 · 합류 키 · 잔여/대기/완료 시각 · 중앙값.

순수 함수만 있다. 시계는 보지 않고 `now` 를 받는다. 규칙은 PLAN.md 「큐 규칙」:

- `position` 은 대기 잡(uploading·queued)에만 1부터. running·cancelling 은 None.
- 대기 계산은 `state != down` 인 워커 수로. 0 이거나 paused 면 `wait_seconds`·`finish_at` 은 None.
- 잔여는 `max(expected − elapsed, floor)`. 초과 실행·stuck 잡의 `finish_at` 은 None
  (하한이 만든 「자신있는 틀린 시각」을 보이지 않는다). 하한은 뒤 잡의 대기 계산에만 쓴다.
- 그룹에 막힌 잡은 `finish_at ≥ 막는 잡의 finish_at + 자기 expected`.
- `reason` 은 단일 표시 사유. `overdue`·`stuck` 은 `Estimate` 의 근거 플래그.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from remote_ci_monitor.core.model import (
    ACTIVE_STATES,
    CANCELLING,
    FAILED,
    PHASE_MATERIALIZING,
    QUEUED,
    REASON_BLOCKED_BY_GROUP,
    REASON_CANCELLING,
    REASON_MATERIALIZING,
    REASON_NOT_SCHEDULED,
    REASON_OVERDUE,
    REASON_PAUSED,
    REASON_RUNNING,
    REASON_STUCK,
    REASON_UPLOAD_STALLED,
    REASON_UPLOADING,
    REASON_WAITING_FOR_LANE,
    REASON_WORKER_DOWN,
    SUCCEEDED,
    TIMED_OUT,
    UPLOADING,
    WORKER_DOWN,
    WORKER_IDLE,
    BlockedBy,
    Estimate,
    Job,
    Median,
    Preset,
    Progress,
    QueueRow,
    Requester,
    Source,
    WorkerInfo,
)

SOURCE_MEASURED = "measured"
SOURCE_PRESET = "preset"
SOURCE_DEFAULT = "default"


@dataclass(frozen=True)
class QueueConfig:
    """계산에 필요한 설정만. 서버 설정(`config.py`)에서 뽑아 만든다."""

    default_seconds: float = 600
    floor_remaining_seconds: float = 30
    stuck_multiplier: float = 3.0
    no_output_seconds: float = 240
    upload_stall_seconds: float = 60
    not_scheduled_seconds: float = 10
    min_samples: int = 2
    min_job_seconds: float = 30
    sample_days: float = 45
    sample_policy: str = "success"


def _seconds(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds()


# ── 합류 키 ──────────────────────────────────────────────────────────────────


def join_key(preset: str, inputs: Mapping[str, Any], source_identity: str | None) -> str:
    """합류 판정 키. 같은 프리셋 · 같은 입력 · 같은 소스 신원이면 같은 잡이다."""
    canonical = [preset, dict(sorted(inputs.items())), source_identity]
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


# ── 표본 · 중앙값 ────────────────────────────────────────────────────────────

_SAMPLE_STATES = {"success": {SUCCEEDED}, "completed": {SUCCEEDED, FAILED, TIMED_OUT}}


def medians_from(jobs: Sequence[Job], now: datetime, cfg: QueueConfig) -> dict[str, Median]:
    """키별 실측 소요 중앙값. `sample_count` 가 `min_samples` 미만이면 추정에 쓰지 않는다.

    낡은 표본(`sample_days`)·즉사 잡(`min_job_seconds` 미만)·잡 시각이 없는 잡은 뺀다.
    소요는 `started_at`~`finished_at` 이라 큐 대기가 섞이지 않는다.
    """
    allowed = _SAMPLE_STATES.get(cfg.sample_policy, _SAMPLE_STATES["success"])
    cutoff = now - timedelta(days=cfg.sample_days)
    durations: dict[str, list[float]] = {}
    waits: dict[str, list[float]] = {}
    for job in jobs:
        if job.state not in allowed or job.started_at is None or job.finished_at is None:
            continue
        if job.started_at < cutoff:
            continue
        dur = _seconds(job.started_at, job.finished_at)
        if dur < cfg.min_job_seconds:
            continue
        durations.setdefault(job.key, []).append(dur)
        waits.setdefault(job.key, []).append(_seconds(job.created_at, job.started_at))
    out: dict[str, Median] = {}
    for key, ds in durations.items():
        out[key] = Median(
            seconds=float(median(ds)),
            wait_seconds=float(median(waits[key])) if waits.get(key) else None,
            sample_count=len(ds),
        )
    return out


def expected_for(
    key: str,
    preset: Preset | None,
    medians: Mapping[str, Median],
    cfg: QueueConfig,
) -> tuple[float, str, int]:
    """(expected_seconds, source, sample_count). measured → preset → default 순."""
    m = medians.get(key)
    n = m.sample_count if m else 0
    if m is not None and n >= cfg.min_samples:
        return m.seconds, SOURCE_MEASURED, n
    if preset is not None and preset.expected_seconds:
        return float(preset.expected_seconds), SOURCE_PRESET, n
    return float(cfg.default_seconds), SOURCE_DEFAULT, n


def confidence(
    source: str, sample_count: int, *, group_wait: bool = False, overdue: bool = False
) -> str:
    """화면 배지의 신뢰도. measured n≥5 → high, n<5 → med, preset/default → low."""
    if overdue:
        return "overdue"
    if group_wait:
        return "group wait"
    if source == SOURCE_MEASURED:
        return "high" if sample_count >= 5 else "med"
    return "low"


# ── 잔여 ─────────────────────────────────────────────────────────────────────


def remaining_seconds(expected: float, elapsed: float | None, cfg: QueueConfig) -> float:
    """대기 잡은 expected 전체. 실행 중이면 하한(floor) — 음수로 새면 큐 전체가 앞당겨진다."""
    if elapsed is None:
        return expected
    return max(expected - elapsed, float(cfg.floor_remaining_seconds))


# ── 큐 계산 ──────────────────────────────────────────────────────────────────


def _busy_estimate(
    job: Job, expected: float, source: str, n: int, now: datetime, cfg: QueueConfig
) -> Estimate:
    started = job.started_at or now
    elapsed = _seconds(started, now)
    waited = _seconds(job.created_at, started)
    remaining = remaining_seconds(expected, elapsed, cfg)
    overdue = elapsed > expected
    last_output = job.last_output_at or started
    stuck = job.state != CANCELLING and (
        elapsed > cfg.stuck_multiplier * expected
        or (job.phase != PHASE_MATERIALIZING and _seconds(last_output, now) > cfg.no_output_seconds)
    )
    finish = (
        None
        if (overdue or stuck or job.state == CANCELLING)
        else now + timedelta(seconds=remaining)
    )
    return Estimate(
        expected_seconds=expected,
        source=source,
        sample_count=n,
        elapsed_seconds=elapsed,
        waited_seconds=waited,
        remaining_seconds=remaining,
        wait_seconds=0.0,
        overdue=overdue,
        stuck=stuck,
        finish_at=finish,
    )


def _busy_reason(job: Job, est: Estimate) -> str:
    if job.state == CANCELLING:
        return REASON_CANCELLING
    if job.phase == PHASE_MATERIALIZING:
        return REASON_MATERIALIZING
    if est.stuck:
        return REASON_STUCK
    if est.overdue:
        return REASON_OVERDUE
    return REASON_RUNNING


def compute_queue(
    jobs: Sequence[Job],
    *,
    workers: Sequence[WorkerInfo],
    paused: bool,
    medians: Mapping[str, Median],
    presets: Mapping[str, Preset],
    cfg: QueueConfig,
    now: datetime,
    progress: Mapping[int, Progress] | None = None,
) -> list[QueueRow]:
    """활성 잡 → 큐 행. 출력 순서는 running → cancelling → 대기(순번순)."""
    progress = progress or {}
    active = sorted((j for j in jobs if j.state in ACTIVE_STATES), key=lambda j: j.id)
    busy = [j for j in active if j.is_busy]
    waiting = [j for j in active if j.is_waiting]

    live = [w for w in workers if w.state != WORKER_DOWN]
    live_lanes = [w.lane for w in live]
    lane_free: dict[int, datetime] = {w.lane: now for w in live}
    lane_last_job: dict[int, int | None] = {w.lane: None for w in live}
    idle_since: dict[int, datetime] = {}
    for w in live:
        if w.state == WORKER_IDLE and w.job_id is None:
            idle_since[w.lane] = w.since or now
    group_free: dict[str, datetime] = {}
    group_holder: dict[str, tuple[Job, Estimate]] = {}

    rows: list[QueueRow] = []
    for job in busy:
        expected, source, n = expected_for(job.key, presets.get(job.preset), medians, cfg)
        est = _busy_estimate(job, expected, source, n, now, cfg)
        free_at = now + timedelta(seconds=est.remaining_seconds or 0)
        if job.lane is not None and job.lane in lane_free:
            lane_free[job.lane] = max(lane_free[job.lane], free_at)
            lane_last_job[job.lane] = job.id
            idle_since.pop(job.lane, None)
        if job.concurrency_group:
            group_free[job.concurrency_group] = max(
                group_free.get(job.concurrency_group, now), free_at
            )
            group_holder[job.concurrency_group] = (job, est)
        rows.append(
            QueueRow(
                job=job,
                position=None,
                reason=_busy_reason(job, est),
                lane=job.lane,
                ahead_job_id=None,
                blocked_by=None,
                estimate=est,
                progress=progress.get(job.id),
            )
        )
    # running 먼저, cancelling 그 다음 (각각 id 순)
    rows.sort(key=lambda r: (r.job.state == CANCELLING, r.job.id))

    can_start = not paused and bool(live_lanes)
    for position, job in enumerate(waiting, start=1):
        expected, source, n = expected_for(job.key, presets.get(job.preset), medians, cfg)
        waited = _seconds(job.created_at, now)
        holder = group_holder.get(job.concurrency_group) if job.concurrency_group else None
        blocked = None
        if holder is not None:
            hjob, hest = holder
            blocked = BlockedBy(
                job_id=hjob.id,
                group=job.concurrency_group or "",
                remaining_seconds=hest.remaining_seconds,
            )
        wait: float | None = None
        finish: datetime | None = None
        ahead: int | None = None
        if can_start:
            lane = min(live_lanes, key=lambda ln: (lane_free[ln], ln))
            start = lane_free[lane]
            ahead = lane_last_job[lane]
            if job.concurrency_group and job.concurrency_group in group_free:
                start = max(start, group_free[job.concurrency_group])
            finish = start + timedelta(seconds=expected)
            wait = _seconds(now, start)
            lane_free[lane] = finish
            lane_last_job[lane] = job.id
            if job.concurrency_group:
                group_free[job.concurrency_group] = finish
        # reason
        if job.state == UPLOADING:
            last = job.source.last_received_at or job.created_at
            stalled = _seconds(last, now) > cfg.upload_stall_seconds
            reason = REASON_UPLOAD_STALLED if stalled else REASON_UPLOADING
        elif paused:
            reason = REASON_PAUSED
        elif not live_lanes:
            reason = REASON_WORKER_DOWN
        elif blocked is not None:
            reason = REASON_BLOCKED_BY_GROUP
        else:
            reason = REASON_WAITING_FOR_LANE
            eligible = job.queued_at or job.created_at
            for since in list(idle_since.values()):
                if _seconds(max(since, eligible), now) > cfg.not_scheduled_seconds:
                    reason = REASON_NOT_SCHEDULED
                    break
            # 이 잡이 그 idle 레인을 차지한다고 보고 다음 잡은 정상 대기로 센다
            if idle_since:
                idle_since.pop(next(iter(idle_since)))
        est = Estimate(
            expected_seconds=expected,
            source=source,
            sample_count=n,
            elapsed_seconds=None,
            waited_seconds=waited,
            remaining_seconds=expected,
            wait_seconds=wait,
            overdue=False,
            stuck=False,
            finish_at=finish,
        )
        rows.append(
            QueueRow(
                job=job,
                position=position,
                reason=reason,
                lane=None,
                ahead_job_id=ahead,
                blocked_by=blocked,
                estimate=est,
                progress=None,
            )
        )
    return rows


def eta_for_new(
    jobs: Sequence[Job],
    *,
    preset: Preset,
    key: str,
    workers: Sequence[WorkerInfo],
    inputs: Mapping[str, Any] | None = None,
    paused: bool,
    medians: Mapping[str, Median],
    presets: Mapping[str, Preset],
    cfg: QueueConfig,
    now: datetime,
) -> tuple[QueueRow, int]:
    """지금 이 키의 잡을 넣으면 어떻게 되나 — 가상 잡을 맨 뒤에 붙여 계산한다(`rcm eta`).

    반환: (가상 잡의 큐 행, 앞선 활성 잡 수).
    """
    next_id = max((j.id for j in jobs), default=0) + 1
    ghost = Job(
        id=next_id,
        preset=preset.name,
        inputs=dict(inputs or {}),
        key=key,
        concurrency_group=preset.concurrency_group,
        source=Source(mode="tree"),
        requester=Requester(name="", label=""),
        state=QUEUED,
        created_at=now,
        queued_at=now,
    )
    rows = compute_queue(
        list(jobs) + [ghost],
        workers=workers,
        paused=paused,
        medians=medians,
        presets=presets,
        cfg=cfg,
        now=now,
    )
    row = next(r for r in rows if r.job.id == next_id)
    ahead = sum(1 for j in jobs if j.state in ACTIVE_STATES)
    return row, ahead
