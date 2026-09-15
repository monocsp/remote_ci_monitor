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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from remote_ci_monitor.core.model import (
    ACTIVE_STATES,
    CANCELLING,
    DEFAULT_POOL,
    FAILED,
    PHASE_MATERIALIZING,
    PRIORITY_NAMES,
    PRIORITY_NORMAL,
    QUEUED,
    REASON_BLOCKED_BY_GROUP,
    REASON_CANCELLING,
    REASON_HELD_BY_LOAD,
    REASON_MATERIALIZING,
    REASON_NOT_SCHEDULED,
    REASON_OVERDUE,
    REASON_PAUSED,
    REASON_QUIET,
    REASON_RUNNING,
    REASON_STUCK,
    REASON_UPLOAD_STALLED,
    REASON_UPLOADING,
    REASON_WAITING_FOR_LANE,
    REASON_WORKER_DOWN,
    STUCK_ELAPSED,
    STUCK_NO_OUTPUT,
    STUCK_STEP,
    SUCCEEDED,
    TIMED_OUT,
    UPLOADING,
    WORKER_DOWN,
    WORKER_HELD,
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
    Step,
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
    #: 현재 단계가 자기 실측 중앙값의 몇 배를 넘으면 stuck 인가
    step_stuck_multiplier: float = 3.0
    #: 단계 중앙값을 믿기 시작하는 표본(실행) 수. 잡 중앙값의 `min_samples`(2)보다 높다 —
    #: 단계 소요는 잡 소요보다 훨씬 시끄럽다(캐시 적중·병렬도·기계 부하가 단계 단위로 다르다).
    step_min_samples: int = 3
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


@dataclass(frozen=True)
class StepMedian:
    """한 key 의 한 **단계 이름**이 평소 얼마나 걸리나. `medians_from` 의 단계판이다."""

    seconds: float
    sample_count: int


def step_medians_from(
    runs: Mapping[str, Sequence[Sequence[Step]]], cfg: QueueConfig
) -> dict[str, dict[str, StepMedian]]:
    """key → 단계 이름 → 중앙값. `step_min_samples` 미만이면 넣지 않는다.

    **단계의 신원은 이름이다**(번호가 아니다). 되재생·병렬 스크립트에서 번호는 실행마다
    흔들리지만 이름은 잡이 선언한 것이다(M5h 결정 63 이 `failed_step` 에서 배운 것과 같다).
    끝난(`state == "done"`) 단계의 `seconds` 만 센다 — 도는 중인 단계는 아직 표본이 아니다.

    `sample_count` 는 **실행 수**다(출현 수가 아니다). 한 실행 안에서 같은 이름이 여러 번
    나오면(루프 도는 스크립트) 그 실행의 **중앙값 하나**로 접는다. 출현 수를 세면 루프
    하나가 혼자 표본을 채워 **한 번의 실행이 「평소」를 정의**하게 된다. 비교 대상인
    `Progress.current_seconds` 도 한 번의 출현이라 표본의 단위가 그래야 맞는다.

    표가 빈 key 는 아예 넣지 않는다 — 빈 dict 를 남기면 「몇 개 key 를 쟀나」가 거짓말이 된다.
    """
    out: dict[str, dict[str, StepMedian]] = {}
    for key, key_runs in runs.items():
        per_name: dict[str, list[float]] = {}
        for steps in key_runs:
            in_run: dict[str, list[float]] = {}
            for s in steps:
                if s.state != "done" or s.seconds is None:
                    continue
                in_run.setdefault(s.name, []).append(float(s.seconds))
            for name, values in in_run.items():
                per_name.setdefault(name, []).append(float(median(values)))
        table = {
            name: StepMedian(seconds=float(median(values)), sample_count=len(values))
            for name, values in per_name.items()
            if len(values) >= cfg.step_min_samples
        }
        if table:
            out[key] = table
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
    source: str,
    sample_count: int,
    *,
    group_wait: bool = False,
    overdue: bool = False,
    shared: bool = False,
) -> str:
    """화면 배지의 신뢰도. measured n≥5 → high, n<5 → med, preset/default → low.

    같이 도는 중이면(`shared`) 실측 배지를 **한 칸 내린다** — 중앙값은 혼자 잰 것이라 그동안은
    덜 맞는다. 배수를 지어내 `expected` 를 늘리지는 않는다(없는 숫자를 만드는 것이다).
    `low` 는 더 내려갈 곳이 없다. `overdue`·`group wait` 는 이미 더 급한 말이라 먼저 반환된다.
    """
    if overdue:
        return "overdue"
    if group_wait:
        return "group wait"
    if source == SOURCE_MEASURED:
        high = sample_count >= 5
        if shared:
            return "med" if high else "low"
        return "high" if high else "med"
    return "low"


# ── 잔여 ─────────────────────────────────────────────────────────────────────


def remaining_seconds(expected: float, elapsed: float | None, cfg: QueueConfig) -> float:
    """대기 잡은 expected 전체. 실행 중이면 하한(floor) — 음수로 새면 큐 전체가 앞당겨진다."""
    if elapsed is None:
        return expected
    return max(expected - elapsed, float(cfg.floor_remaining_seconds))


# ── 큐 계산 ──────────────────────────────────────────────────────────────────


def _busy_estimate(
    job: Job,
    expected: float,
    source: str,
    n: int,
    now: datetime,
    cfg: QueueConfig,
    *,
    progress: Progress | None = None,
    step_medians: Mapping[str, StepMedian] | None = None,
) -> Estimate:
    """도는 잡 하나의 추정과 **멈춤/조용함 판정**.

    침묵만으로는 `stuck` 이 아니다. 침묵은 `quiet` — 관측이지 경보가 아니다. 죽었다는 말은
    **현재 단계의 실측**이 있을 때만 그 실측으로 한다.
    """
    started = job.started_at or now
    elapsed = _seconds(started, now)
    waited = _seconds(job.created_at, started)
    remaining = remaining_seconds(expected, elapsed, cfg)
    overdue = elapsed > expected
    last_output = job.last_output_at or started
    silent = job.phase != PHASE_MATERIALIZING and _seconds(last_output, now) > cfg.no_output_seconds

    medians = step_medians or {}
    current = progress.current_name if progress is not None else None
    m = medians.get(current) if current else None
    step_stuck = False
    stuck_code: str | None = None
    step_expected: float | None = None
    if m is not None:
        # ① 이 단계의 실측이 있다 — 그것으로 판정한다.
        step_expected = m.seconds
        current_seconds = (progress.current_seconds if progress is not None else None) or 0.0
        # 하한이 곱셈보다 세다. **침묵 규칙이 낼 수 있었던 것보다 빨리 죽었다고 말하지
        # 않는다** — 중앙값 0.5초짜리 `lint` 의 3배(1.5초)로 「죽었다」고 하는 것이 그 짓이고,
        # 중앙값이 정확히 0 초인 단계(마커 두 줄이 같은 `at` 을 받았다)는 곱셈으로는 아예
        # 못 막는다(임계가 0 이라 시작하자마자 stuck 이다). 새 설정 키는 만들지 않고
        # `no_output_seconds` 를 그대로 하한으로 쓴다 — 규칙이 한 문장으로 읽힌다.
        threshold = max(cfg.step_stuck_multiplier * m.seconds, cfg.no_output_seconds)
        step_stuck = current_seconds > threshold
        if step_stuck:
            stuck_code = STUCK_STEP
    elif progress is None or not progress.steps:
        # ② 단계 이야기를 아예 안 한다 — 침묵이 우리가 가진 유일한 신호다(옛 규칙 그대로).
        step_stuck = silent
        if step_stuck:
            stuck_code = STUCK_NO_OUTPUT
    # ③ 단계는 도는데 그 이름의 실측이 아직 없다 → `stuck` 이 아니다. `quiet` 만 낸다.
    #    「몇 번째 단계에 있다」가 이미 살아 있다는 증거이고, 그 단계가 얼마나 걸려야 하는지
    #    **모르는 것은 모르는 것**이지 죽은 것이 아니다. 여기를 ② 로 보내면 이력이 없는 새
    #    key 는 오늘과 똑같이 매번 빨개진다 — 고치려던 바로 그 사고다.
    #    **단계를 다 끝낸 뒤(`current_name is None`, `steps` 는 남아 있다)의 침묵도 ③ 이다.**
    #    의도된 구멍이다: 마지막 단계를 끝내고 산출물 업로드에서 죽으면 여기로 온다. 안전망은
    #    `elapsed > stuck_multiplier × expected` 와 프리셋 `timeout_seconds` 둘이다.

    stuck = job.state != CANCELLING and (elapsed > cfg.stuck_multiplier * expected or step_stuck)
    if not stuck:
        stuck_code = None
    elif stuck_code is None:
        stuck_code = STUCK_ELAPSED
    quiet = silent and not stuck and job.state != CANCELLING
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
        quiet=quiet,
        stuck_code=stuck_code,
        step_expected_seconds=step_expected,
    )


def _busy_reason(job: Job, est: Estimate) -> str:
    """표시 사유 하나. `cancelling → materializing → stuck → overdue → quiet → running`.

    초과 실행이면서 조용한 잡은 `overdue` 로 낸다 — 더 행동 가능한 사실이 이긴다. 뒤집으면
    초과 실행이 회색 「조용함」 뒤에 숨는다.
    """
    if job.state == CANCELLING:
        return REASON_CANCELLING
    if job.phase == PHASE_MATERIALIZING:
        return REASON_MATERIALIZING
    if est.stuck:
        return REASON_STUCK
    if est.overdue:
        return REASON_OVERDUE
    if est.quiet:
        return REASON_QUIET
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
    step_medians: Mapping[str, Mapping[str, StepMedian]] | None = None,
) -> list[QueueRow]:
    """활성 잡 → 큐 행. 출력 순서는 running → cancelling → 대기(순번순).

    `step_medians` 는 key → 단계 이름 → 중앙값. 없으면 멈춤 판정은 옛 규칙(침묵)으로 돈다.
    """
    progress = progress or {}
    step_medians = step_medians or {}
    active = sorted((j for j in jobs if j.state in ACTIVE_STATES), key=lambda j: j.id)
    busy = [j for j in active if j.is_busy]
    # 같은 풀에서 둘 이상이 도는 중이면 서로 머신을 나눠 쓰고 있다(M5f §6)
    busy_per_pool: dict[str, int] = {}
    for j in busy:
        busy_per_pool[j.pool] = busy_per_pool.get(j.pool, 0) + 1
    # 대기 순서 = (우선순위 높은 것 먼저, 같은 우선순위는 id) — store.claim 의 ORDER BY 와 같은 키
    waiting = sorted((j for j in active if j.is_waiting), key=lambda j: (-j.priority, j.id))

    live = [w for w in workers if w.state != WORKER_DOWN]
    # 레인은 **번호가 아니라 (워커, 번호)** 로 센다. 기본 풀에는 로컬 레인 1..N 과 원격 워커의
    # 레인 1..M 이 함께 들어오므로(`server.pool_workers`), 번호로 키를 잡으면 로컬 레인 2 와
    # `build-02/2` 가 뭉개져 4레인 풀이 2레인처럼 계산된다(M5f).
    live_lanes = [(w.worker, w.lane) for w in live]
    # 부하로 보류된 레인은 **살아 있지만 지금은 못 집는다**(M5f). 언제 열릴지는 모르는 값이라
    # 그리디에 넣으면 PLAN 이 금지한 「자신있는 틀린 시각」이 된다 — 빼면 늦게 잡히고, 레인이
    # 열리면 앞당겨진다.
    open_lanes = [(w.worker, w.lane) for w in live if w.state != WORKER_HELD]
    held_lanes = [(w.worker, w.lane) for w in live if w.state == WORKER_HELD]
    lane_free: dict[tuple[str | None, int], datetime] = {k: now for k in live_lanes}
    lane_last_job: dict[tuple[str | None, int], int | None] = {k: None for k in live_lanes}
    idle_since: dict[tuple[str | None, int], datetime] = {}
    for w in live:
        if w.state == WORKER_IDLE and w.job_id is None:
            idle_since[(w.worker, w.lane)] = w.since or now
    group_free: dict[str, datetime] = {}
    group_holder: dict[str, tuple[Job, Estimate]] = {}

    rows: list[QueueRow] = []
    for job in busy:
        expected, source, n = expected_for(job.key, presets.get(job.preset), medians, cfg)
        est = _busy_estimate(
            job,
            expected,
            source,
            n,
            now,
            cfg,
            progress=progress.get(job.id),
            step_medians=step_medians.get(job.key),
        )
        if busy_per_pool.get(job.pool, 0) > 1:
            est = replace(est, shared=True)
        free_at = now + timedelta(seconds=est.remaining_seconds or 0)
        holder_lane = (job.worker_name, job.lane) if job.lane is not None else None
        if holder_lane is not None and holder_lane in lane_free:
            lane_free[holder_lane] = max(lane_free[holder_lane], free_at)
            lane_last_job[holder_lane] = job.id
            idle_since.pop(holder_lane, None)
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

    can_start = not paused and bool(open_lanes)
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
            # None(로컬)과 워커 이름을 같이 정렬하려면 키를 평평하게 만들어야 한다
            lane = min(open_lanes, key=lambda ln: (lane_free[ln], ln[0] or "", ln[1]))
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
            elif held_lanes:
                # 「너를 집었을 레인이 부하로 막혀 있다」 — idle 통과 held 통은 별개다.
                # 대기 잡 하나가 보류 레인 하나를 쓰고, 뒤 잡은 정직하게 waiting_for_lane 이다.
                reason = REASON_HELD_BY_LOAD
                held_lanes.pop()
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
    priority: int = PRIORITY_NORMAL,
    pool: str = DEFAULT_POOL,
) -> tuple[QueueRow, int]:
    """지금 이 키의 잡을 넣으면 어떻게 되나 — 가상 잡을 붙여 계산한다(`rcm eta`).

    가상 잡은 같은 우선순위의 맨 뒤에 선다(더 낮은 우선순위보다는 앞). 다른 풀의 잡은 세지 않는다.
    반환: (가상 잡의 큐 행, 실제로 앞선 활성 잡 수 — 실행 중 + 우선순위가 같거나 높은 대기 잡).
    """
    jobs = [j for j in jobs if j.pool == pool]
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
        priority=priority,
        pool=pool,
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
    ahead = sum(
        1 for j in jobs if j.state in ACTIVE_STATES and (j.is_busy or j.priority >= priority)
    )
    return row, ahead


def split_by_pool(jobs: Iterable[Job]) -> dict[str, list[Job]]:
    """풀별로 나눈다(상태로 거르지 않는다). 키: 기본 풀 먼저, 나머지 이름순. 풀 안은 입력 순서."""
    by: dict[str, list[Job]] = {}
    for job in jobs:
        by.setdefault(job.pool, []).append(job)
    ordered: dict[str, list[Job]] = {}
    if DEFAULT_POOL in by:
        ordered[DEFAULT_POOL] = by[DEFAULT_POOL]
    for name in sorted(k for k in by if k != DEFAULT_POOL):
        ordered[name] = by[name]
    return ordered


def priority_from_name(name: str) -> int:
    """`low` · `normal` · `high` → -1 · 0 · 1. 다른 값은 ValueError(대소문자·공백 그대로 비교)."""
    if not isinstance(name, str) or name not in PRIORITY_NAMES:
        raise ValueError(f"priority must be one of low, normal, high — got {name!r}")
    return PRIORITY_NAMES[name]


def join_priority(existing: int, requested: int) -> int:
    """합류하면 기존 잡의 우선순위는 올라가기만 한다(내리지 않는다)."""
    return max(existing, requested)
