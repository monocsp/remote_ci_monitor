"""스텝 마커 프로토콜 — 로그 줄 → 마커 → Progress. PLAN.md 「진행 — 스텝 마커 프로토콜」.

마커는 줄 맨 앞에 온다:
    ::rcm::steps::<N>          앞으로 스텝이 N 개(선택)
    ::rcm::step::<이름>        새 스텝 시작(앞 스텝은 이 시각에 끝)
    ::rcm::step-end::<ok|fail> 스텝 끝을 명시(선택)
    ::rcm::summary::<한 줄>    결과 요약(선택, 마지막 것)

스텝 시각은 **서버 수신 시각**이다(`timing: "as_received"`). 자식 프로세스의 버퍼링으로
마커가 몰려서 올 수 있어 실제보다 늦을 수 있다는 걸 스키마가 밝힌다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from remote_ci_monitor.core.model import (
    PHASE_EXECUTING,
    WAITING_STATES,
    Job,
    Progress,
    Step,
)

MARKER_PREFIX = "::rcm::"
KIND_STEPS = "steps"
KIND_STEP = "step"
KIND_STEP_END = "step-end"
KIND_SUMMARY = "summary"
MARKER_KINDS = (KIND_STEPS, KIND_STEP, KIND_STEP_END, KIND_SUMMARY)
MAX_STEP_NAME = 120
MAX_SUMMARY = 200


@dataclass(frozen=True)
class Marker:
    """수신 시각이 붙은 마커 하나. `at` 은 서버가 그 줄을 받은 시각."""

    at: datetime
    kind: str
    value: str


def parse_marker(line: str) -> tuple[str, str] | None:
    """로그 한 줄이 마커면 (kind, value), 아니면 None. 줄 맨 앞이어야 한다."""
    if not line.startswith(MARKER_PREFIX):
        return None
    rest = line[len(MARKER_PREFIX) :].rstrip("\r\n")
    kind, sep, value = rest.partition("::")
    if not sep or kind not in MARKER_KINDS:
        return None
    value = value.strip()
    if kind == KIND_STEPS:
        if not value.isdigit():
            return None
    elif kind == KIND_STEP:
        if not value:
            return None
        value = value[:MAX_STEP_NAME]
    elif kind == KIND_STEP_END:
        if value not in ("ok", "fail"):
            return None
    else:
        value = value[:MAX_SUMMARY]
    return kind, value


@dataclass
class _Open:
    index: int
    name: str
    started: datetime
    ended: datetime | None = None
    ok: bool | None = None
    #: `ok` 가 `step-end::` 마커에서 왔는가. 아니면 종료 코드로 미뤄 짐작한 값이다 —
    #: 실패를 지목할 때 「확정」과 「추측」을 가르는 유일한 근거다.
    marked: bool = False


def progress_from_markers(
    markers: Sequence[Marker],
    *,
    started_at: datetime,
    finished_at: datetime | None,
    now: datetime,
    exit_code: int | None,
    phase: str | None = None,
    last_output_at: datetime | None = None,
) -> Progress:
    """마커 목록을 Progress 로. 잡이 끝났으면 `finished_at`, 아니면 `now` 가 마지막 스텝의 끝."""
    end = finished_at or now
    declared: int | None = None
    steps: list[_Open] = []
    summary: str | None = None
    for m in markers:
        if m.kind == KIND_STEPS:
            try:
                declared = int(m.value)
            except ValueError:
                continue
        elif m.kind == KIND_STEP:
            if steps and steps[-1].ended is None:
                steps[-1].ended = m.at
                if steps[-1].ok is None:
                    steps[-1].ok = True
            steps.append(_Open(index=len(steps) + 1, name=m.value, started=m.at))
        elif m.kind == KIND_STEP_END:
            if steps and steps[-1].ended is None:
                steps[-1].ended = m.at
                steps[-1].ok = m.value == "ok"
                steps[-1].marked = True  # 스크립트가 직접 말했다
        elif m.kind == KIND_SUMMARY:
            summary = m.value
    current: _Open | None = None
    if steps and steps[-1].ended is None:
        if finished_at is not None:
            steps[-1].ended = finished_at
            if steps[-1].ok is None:
                steps[-1].ok = exit_code == 0
        else:
            current = steps[-1]
    out_steps = tuple(
        Step(
            index=s.index,
            name=s.name,
            state="done" if s.ended is not None else "running",
            ok=s.ok,
            seconds=(s.ended - s.started).total_seconds()
            if s.ended is not None
            else (now - s.started).total_seconds(),
            started_at=s.started,
            ended_at=s.ended,
        )
        for s in steps
    )
    done = sum(1 for s in steps if s.ended is not None)
    if declared is not None:
        total: int | None = max(declared, len(steps))
        partial = False
    else:
        total = len(steps) if steps else None
        partial = True
    # 실패 스텝은 **확정** 아니면 **추측**이다(PLAN.md 함정 #7).
    #   확정 — 스크립트가 `::rcm::step-end::fail` 을 찍은 스텝.
    #   추측 ① 잡이 0 이 아닌 코드로 끝날 때 아직 안 닫힌 마지막 스텝이 그 코드를 물려받은 것.
    #   추측 ② 스텝이 전부 닫혔는데 종료 코드만 0 이 아니라 마지막 스텝을 고른 것.
    # 스텝을 병렬로 돌리고 마커를 나중에 몰아 내보내는 스크립트에서는 마지막 마커가 **성공한**
    # 스텝일 수 있다. 마커만 보고 진짜 범인을 고를 방법은 없으니, 더 똑똑하게 짐작하는 대신
    # 짐작이라고 밝힌다 — 무죄인 스텝을 자신있게 지목하는 것이 아무 이름도 안 대는 것보다 나쁘다.
    blamed = next((s for s in steps if s.ok is False), None)
    if blamed is not None:
        failed: str | None = blamed.name
        guessed = not blamed.marked
    elif exit_code not in (None, 0) and steps:
        failed, guessed = steps[-1].name, True
    else:
        failed, guessed = None, False
    return Progress(
        phase=phase or PHASE_EXECUTING,
        steps=out_steps,
        steps_total=total,
        steps_total_partial=partial,
        steps_done=done,
        current_index=current.index if current else None,
        current_name=current.name if current else None,
        current_seconds=(now - current.started).total_seconds() if current else None,
        job_seconds=(end - started_at).total_seconds(),
        failed_step=failed,
        failed_step_guessed=guessed,
        summary=summary,
        last_output_at=last_output_at,
        started_at=started_at,
    )


def progress_for_job(job: Job, markers: Sequence[Marker], now: datetime) -> Progress | None:
    """잡 상태에 맞춰 Progress 를 만든다. 시작 전(uploading·queued)이면 None — 0/0 은 없다."""
    if job.state in WAITING_STATES or job.started_at is None:
        return None
    return progress_from_markers(
        markers,
        started_at=job.started_at,
        finished_at=job.finished_at,
        now=now,
        exit_code=job.exit_code,
        phase=job.phase,
        last_output_at=job.last_output_at,
    )


def markers_from_log(lines: Sequence[tuple[datetime, str]]) -> list[Marker]:
    """(수신 시각, 줄) 목록에서 마커만 뽑는다. 테스트·로그 재파싱용."""
    out: list[Marker] = []
    for at, line in lines:
        parsed = parse_marker(line)
        if parsed is not None:
            out.append(Marker(at=at, kind=parsed[0], value=parsed[1]))
    return out
