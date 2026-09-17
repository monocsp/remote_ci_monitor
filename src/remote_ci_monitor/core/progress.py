"""스텝 마커 프로토콜 — 로그 줄 → 마커 → Progress. PLAN.md 「진행 — 스텝 마커 프로토콜」.

마커는 줄 맨 앞에 온다:
    ::rcm::steps::<N>          앞으로 스텝이 N 개(선택)
    ::rcm::step::<이름>        새 스텝 시작(앞 스텝은 이 시각에 끝)
    ::rcm::step-end::<ok|fail> 스텝 끝을 명시(선택)
    ::rcm::summary::<한 줄>    결과 요약(선택, 마지막 것)
    ::rcm::fail::<이름>        무엇이 실패했는지 이름으로 지목(선택, M5h)
    ::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]
                               현재 스텝 **안**의 세부 진행(선택, `docs/release-contract.md` §3)

`progress` 는 스텝 안의 이야기다 — 새 `::rcm::step::` 이 오면 지워진다. 마지막 마커가 `sub`,
단위별 마지막 상태가 `units[]`(처음 본 순서, MAX_PROGRESS_UNITS 까지)다. 스크립트는 **아는**
분모만 찍는다(모르면 안 찍는다). `done > total`·`total < 1`·모르는 state 는 마커가 아니다.

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
    ProgressUnit,
    Step,
    SubProgress,
)

MARKER_PREFIX = "::rcm::"
KIND_STEPS = "steps"
KIND_STEP = "step"
KIND_STEP_END = "step-end"
KIND_SUMMARY = "summary"
KIND_FAIL = "fail"
KIND_PROGRESS = "progress"
MARKER_KINDS = (KIND_STEPS, KIND_STEP, KIND_STEP_END, KIND_SUMMARY, KIND_FAIL, KIND_PROGRESS)
MAX_STEP_NAME = 120
MAX_SUMMARY = 200
#: `::rcm::progress::` 의 state 어휘. 프로젝트 이름은 없다 — 청크·자식·락 어느 쪽에도 맞는 말만.
PROGRESS_STATES = ("run", "ok", "fail", "skip", "env", "review", "blocked", "wait")
#: 한 스텝 안에서 격자에 올리는 **서로 다른** 단위 수. 넘어도 `done/total` 은 계속 센다.
MAX_PROGRESS_UNITS = 500
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


@dataclass(frozen=True)
class ProgressMark:
    """`::rcm::progress::` 한 줄의 값 — 저장 문자열(`value`)과 서로 오간다."""

    done: int
    total: int
    unit: str
    state: str
    note: str | None

    @property
    def value(self) -> str:
        """저장·발행용 정규형. `parse_progress_value` 가 그대로 되읽는다."""
        head = f"{self.done}/{self.total}::{self.unit}::{self.state}"
        return head if self.note is None else f"{head}::{self.note}"


def _ascii_int(text: str) -> bool:
    text = text.strip()
    return bool(text) and text.isascii() and text.isdigit()


def parse_progress_value(value: str) -> ProgressMark | None:
    """`<done>/<total>::<unit>::<state>[::<note>]` 를 읽는다. 문법이 어긋나면 None(마커가 아니다).

    분모는 스크립트가 아는 것만 온다는 약속이라 검증이 엄하다 — `total ≥ 1`, `0 ≤ done ≤ total`,
    정수만. 단위 이름은 스텝 이름과 같은 규칙(제어문자 제거 · 120자), note 는 요약과 같은 규칙.
    """
    parts = value.split("::", 3)
    if len(parts) < 3:
        return None
    ratio, unit, state = parts[0].strip(), parts[1], parts[2].strip()
    note = parts[3] if len(parts) == 4 else None
    done_s, slash, total_s = ratio.partition("/")
    # ASCII 숫자만 — `str.isdigit()` 은 `²` 같은 유니코드 숫자도 참인데 `int()` 는 그걸 못 읽는다
    if not slash or not _ascii_int(done_s) or not _ascii_int(total_s):
        return None
    done, total = int(done_s), int(total_s)
    if total < 1 or done > total:
        return None
    unit = clean_name(unit)[:MAX_STEP_NAME]
    if not unit or state not in PROGRESS_STATES:
        return None
    if note is not None:
        note = clean_name(note)[:MAX_SUMMARY] or None
    return ProgressMark(done=done, total=total, unit=unit, state=state, note=note)


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
    elif kind == KIND_PROGRESS:
        # 정규형으로 저장한다 — 공백·제어문자·길이를 여기서 한 번 정리하면 되읽기는 늘 성공한다
        mark = parse_progress_value(value)
        if mark is None:
            return None
        value = mark.value
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
    # 현재 스텝 안의 세부 진행. 새 스텝이 열리면 통째로 비운다 — 스텝 안의 이야기라서다.
    # 첫 스텝 마커 전에 온 progress 도 그대로 센다(스텝 없는 스크립트도 세부 진행은 찍을 수 있다).
    sub: SubProgress | None = None
    units: list[ProgressUnit] = []
    unit_pos: dict[str, int] = {}
    units_truncated = False
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
            sub, units, unit_pos, units_truncated = None, [], {}, False
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
        elif m.kind == KIND_PROGRESS:
            mark = parse_progress_value(m.value)
            if mark is None:  # 저장소에 정규형으로만 들어가지만, 깨진 행이 진행을 막지는 않는다
                continue
            sub = SubProgress(
                done=mark.done,
                total=mark.total,
                unit=mark.unit,
                state=mark.state,
                note=mark.note,
                at=m.at,
            )
            cell = ProgressUnit(unit=mark.unit, state=mark.state, note=mark.note, at=m.at)
            pos = unit_pos.get(mark.unit)
            if pos is not None:
                units[pos] = cell  # 자리는 처음 본 순서, 값은 마지막 상태
            elif len(units) >= MAX_PROGRESS_UNITS:
                units_truncated = True  # 격자에는 못 올려도 `sub` 의 done/total 은 위에서 갱신됐다
            else:
                unit_pos[mark.unit] = len(units)
                units.append(cell)
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
    # 실패 스텝은 **선언된 것만**이다(M5h 결정 63). 확정은 `::rcm::step-end::fail` 과
    # `::rcm::fail::<이름>` 둘뿐이고, 아니면 `None` 이다.
    #
    # dev 의 앞선 답(PR #71, `failed_step_guessed`)은 추측한 이름을 남기고 「추측」이라고
    # 밝히는 쪽이었다. 그 커밋의 주석이 이유를 정확히 적었다 — 「무죄인 스텝을 자신있게
    # 지목하는 것이 아무 이름도 안 대는 것보다 나쁘다」. M5h 는 그 문장을 끝까지 밀어
    # **이름을 안 댄다**: 「어디였나」는 `last_step` 이 인과 없이 말하고, 「무엇이 깨졌나」는
    # 스크립트가 이름으로 말한다(`::rcm::fail::`). 그래서 `failed_step_guessed` 는 늘 거짓이
    # 되어 사라졌다(둘 다 미출시라 이 교체는 사용자를 지나치지 않았다).
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
        sub=sub,
        units=tuple(units),
        units_truncated=units_truncated,
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
