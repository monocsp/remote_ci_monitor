"""스텝 마커 프로토콜 — 로그 줄 → 마커 → Progress. PLAN.md 「진행 — 스텝 마커 프로토콜」.

마커는 줄 맨 앞에 온다:
    ::rcm::steps::<N>          앞으로 스텝이 N 개(선택)
    ::rcm::step::<이름>        새 스텝 시작(앞 스텝은 이 시각에 끝)
    ::rcm::step-end::<ok|fail> 스텝 끝을 명시(선택)
    ::rcm::summary::<한 줄>    결과 요약(선택, 마지막 것)
    ::rcm::fail::<이름>        무엇이 실패했는지 이름으로 지목(선택, M5h)

**실패 스텝은 선언된 것만이다**(결정 63). `step-end::fail` 이나 `fail` 마커로 밝힌 스텝만
`failed_step` 이고, 없으면 `None` 이다 — 종료 코드로 스텝을 고르지 않는다. 되재생·병렬
스크립트에서 마지막 머리말이 남의 실패를 뒤집어쓰던 폴백을 없앤 것이다(운영 잡 #162).
「끝났을 때 어디였나」는 `last_step` 이 인과 없이 말한다.

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
KIND_FAIL = "fail"
MARKER_KINDS = (KIND_STEPS, KIND_STEP, KIND_STEP_END, KIND_SUMMARY, KIND_FAIL)
MAX_STEP_NAME = 120
MAX_SUMMARY = 200
#: 한 잡이 남길 수 있는 **서로 다른** 실패 이름 수. 11,000줄짜리 테스트 출력이 DB 를 채우면 안 된다.
MAX_FAIL_NAMES = 100


def clean_name(text: str) -> str:
    """스텝·실패 이름에서 제어문자와 홀로 남은 서로게이트를 지운다(줄바꿈·탭도 지운다).

    `core/notify.sanitize_text` 와 같은 규칙이되 **한 줄 이름**이라 개행도 안 남긴다.
    """
    return "".join(
        ch for ch in text if ord(ch) >= 0x20 and ord(ch) != 0x7F and not 0xD800 <= ord(ch) <= 0xDFFF
    ).strip()


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
    elif kind in (KIND_STEP, KIND_FAIL):  # 이름 규칙이 같다 — 비면 마커가 아니고 120자에서 자른다
        # 제어문자를 여기서 지운다. 이름은 잡의 출력에서 오고(명세 §6 의 `sed` 권고를 보라)
        # 터미널까지 그대로 흐른다 — `\r` 하나면 앞 줄을 지우고 ESC 하나면 색을 바꿔
        # 「all green」이라고 써 놓을 수 있다. 대장·JSON·화면 **전부**의 입구가 여기다.
        value = clean_name(value)
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
    fail_names: list[str] = []  # 잡이 찍은 순서 그대로 — 서로 다른 이름만
    fail_seen: set[str] = set()
    fail_truncated = False
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
        elif m.kind == KIND_SUMMARY:
            summary = m.value
        elif m.kind == KIND_FAIL:
            # 중복 제거가 먼저다 — 상한은 **서로 다른** 이름에 걸린다. 이미 센 이름이 다시
            # 와도 잘림이 아니다(같은 테스트가 여러 번 빨간 것은 한 가지 사실이다).
            if m.value in fail_seen:
                continue
            if len(fail_names) >= MAX_FAIL_NAMES:
                fail_truncated = True
                continue
            fail_seen.add(m.value)
            fail_names.append(m.value)
    current: _Open | None = None
    if steps and steps[-1].ended is None:
        if finished_at is not None:
            steps[-1].ended = finished_at
            if steps[-1].ok is None:
                # exit 0 이면 「이 스텝까지 무사히 왔다」가 참이다. 실패한 잡에서는 **모른다** —
                # 여기서 False 를 넣으면 그것이 곧 옛 폴백이다(#162).
                steps[-1].ok = True if exit_code == 0 else None
        else:
            current = steps[-1]
    # 선언이 추론을 이긴다. 스텝을 **표시만** 하고 닫지는 않는다 — 「깨졌다」고 말하고도 계속
    # 도는 스크립트가 있다. 마커가 스텝보다 먼저 와도 되게 루프가 끝난 뒤에 한 번에 칠한다.
    for s in steps:
        if s.name in fail_seen:
            s.ok = False
    # `::rcm::step-end::fail` 도 **선언**이다 — 대장에 안 남기면 그 잡은 나중 창에서
    # 「이름 없이 실패했다」로 세어진다(검증 라운드 5). 스텝 순서대로 뒤에 붙인다.
    for s in steps:
        if s.ok is False and s.name not in fail_seen and len(fail_names) < MAX_FAIL_NAMES:
            fail_seen.add(s.name)
            fail_names.append(s.name)
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
    # 선언된 것만이다(결정 63) — 종료 코드로 스텝을 고르는 폴백은 없다.
    failed = next((s.name for s in steps if s.ok is False), None)
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
        last_step=steps[-1].name if steps else None,
        fail_names=tuple(fail_names),
        fail_truncated=fail_truncated,
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
