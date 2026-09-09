"""M5h §1.1 — 새 마커 `::rcm::fail::<이름>` 과 「선언된 것만」 규칙 R1~R6 (test-first).

시나리오 표는 `docs/m5h-test-scenarios-a.md`. 증인은 운영 잡 #162 의 마커 순서
(`docs/m5h-workplan.md` §2.1)다 — 되재생된 머리말을 실패로 부르던 폴백이 여기서 빨개진다.
뮤테이션 ⑬(`failed`를 `steps[-1].name` 폴백으로 되돌리기)의 표적이기도 하다.
시계도 I/O 도 없다. 시각은 `jobfactory.NOW` 하나에서만 온다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from jobfactory import NOW, ago, job
from remote_ci_monitor.core.model import FAILED, PHASE_EXECUTING, Progress
from remote_ci_monitor.core.progress import (
    KIND_FAIL,
    MARKER_KINDS,
    MAX_FAIL_NAMES,
    MAX_STEP_NAME,
    Marker,
    markers_from_log,
    parse_marker,
    progress_for_job,
    progress_from_markers,
)

START = ago(minutes=11)  # #162 는 11분 0초를 돌고 exit 1 로 끝났다
FINISHED = ago(seconds=5)


def markers(lines: Sequence[str], *, start: datetime = START, step: float = 1.0) -> list[Marker]:
    """로그 줄에 수신 시각(start + i×step)을 붙여 마커만 뽑는다. 파서까지 같이 지난다."""
    timed = [(start + timedelta(seconds=i * step), line) for i, line in enumerate(lines)]
    return markers_from_log(timed)


def progress(
    lines: Sequence[str],
    *,
    exit_code: int | None = 1,
    finished: datetime | None = FINISHED,
    start: datetime = START,
) -> Progress:
    """끝난 잡이 기본이다. 도는 잡은 `finished=None` · `exit_code=None`."""
    return progress_from_markers(
        markers(lines, start=start),
        started_at=start,
        finished_at=finished,
        now=NOW,
        exit_code=exit_code,
    )


def fails(*names: str) -> list[str]:
    return [f"::rcm::fail::{n}" for n in names]


#: 상한을 시험할 이름 100개. 잡이 찍은 순서가 그대로 답이다.
NAMES_100 = [f"case{i:03d}" for i in range(1, 101)]

#: 운영 잡 #162 의 마커 순서 그대로(workplan §2.1). 병렬 셋을 돌리고 로그를 되재생한다.
JOB_162 = (
    "::rcm::step::무거운 셋 병렬 시작 (test[동시 9] · gitleaks · build web)",
    "FAIL: test (exit 1) — 깨진 테스트 1건(최대 80):",
    "  … just_audio_screen_music_port_test.dart: fadeIn — 0 에서 스며든다",
    "::rcm::step::test",
    "::rcm::step::secret-scan (gitleaks — 전체 히스토리)",
    "::rcm::step::build web (release — 셰이더 impellerc 컴파일 회귀 포함)",
)
HEAVY_162 = "무거운 셋 병렬 시작 (test[동시 9] · gitleaks · build web)"
SCAN_162 = "secret-scan (gitleaks — 전체 히스토리)"
LAST_162 = "build web (release — 셰이더 impellerc 컴파일 회귀 포함)"
#: 실제 게이트 스크립트가 찍을 자리 — 되재생된 `::rcm::step::test` **위**다.
JOB_162_DECLARED = JOB_162[:3] + ("::rcm::fail::test",) + JOB_162[3:]


# ── A. 파서 — 새 kind 하나와 옛 서버 호환 (§1.1 · 결정 65) ──────────────────


def test_parse_marker_reads_the_fail_kind():
    assert parse_marker("::rcm::fail::test") == (KIND_FAIL, "test")
    assert parse_marker("::rcm::fail::just_audio_port_test.dart\r\n") == (
        KIND_FAIL,
        "just_audio_port_test.dart",
    )
    assert parse_marker("::rcm::fail::  build web  ") == (KIND_FAIL, "build web")


def test_an_empty_fail_value_is_not_a_marker():
    """빈 값은 마커가 아니다 — `KIND_STEP` 과 같은 규칙이다."""
    assert parse_marker("::rcm::fail::") is None
    assert parse_marker("::rcm::fail::   ") is None
    assert parse_marker("::rcm::fail::\n") is None
    assert parse_marker("::rcm::fail") is None


def test_a_fail_name_is_cut_at_the_step_name_limit():
    kind, value = parse_marker("::rcm::fail::" + "x" * 200)
    assert kind == KIND_FAIL and value == "x" * MAX_STEP_NAME and MAX_STEP_NAME == 120


def test_an_unknown_marker_kind_is_still_ignored():
    """옛 서버 호환의 거울 — 모르는 kind 는 `None` 이고 줄은 로그에 그냥 남는다."""
    for line in ("::rcm::flaky::x", "::rcm::fail-name::x", "::rcm::FAIL::x", "::rcm::failed::x"):
        assert parse_marker(line) is None, line


def test_a_fail_marker_must_start_the_line():
    assert parse_marker("prefix ::rcm::fail::x") is None
    assert parse_marker(" ::rcm::fail::x") is None


def test_a_fail_name_may_contain_the_separator():
    """`partition` 은 첫 `::` 에서만 자른다 — 이름 안의 `::` 는 이름의 일부다."""
    assert parse_marker("::rcm::fail::pkg::test_a") == (KIND_FAIL, "pkg::test_a")


def test_the_fail_kind_and_the_cap_are_locked():
    assert KIND_FAIL == "fail"
    assert MARKER_KINDS == ("steps", "step", "step-end", "summary", KIND_FAIL)
    assert MAX_FAIL_NAMES == 100


def test_markers_from_log_keeps_fail_lines_in_receive_order():
    got = markers(["::rcm::step::a", "::rcm::fail::a", "::rcm::flaky::a", "noise"])
    assert [(m.kind, m.value) for m in got] == [("step", "a"), (KIND_FAIL, "a")]
    assert got[1].at == START + timedelta(seconds=1)


# ── B. 증인 — 운영 잡 #162 (§2.1 · 완료 기준 1 · 뮤테이션 ⑬) ─────────────────


def test_job_162_marker_order_leaves_the_failed_step_empty():
    """되재생된 머리말은 실패가 아니다. 마지막 스텝(`build web`)은 **성공한** 스텝이었다."""
    p = progress(JOB_162, exit_code=1)
    assert p.failed_step is None
    assert p.last_step == LAST_162
    assert [s.name for s in p.steps] == [HEAVY_162, "test", SCAN_162, LAST_162]
    assert [s.ok for s in p.steps] == [True, True, True, None]  # R3 · R4
    assert p.steps_done == 4 and p.current_index is None
    assert p.fail_names == () and p.fail_truncated is False


def test_job_162_with_a_fail_marker_names_the_step():
    """스크립트가 `::rcm::fail::test` 를 찍으면 되재생 순서와 상관없이 `test` 가 실패다."""
    p = progress(JOB_162_DECLARED, exit_code=1)
    assert p.failed_step == "test"
    assert p.last_step == LAST_162
    assert [s.ok for s in p.steps] == [True, False, True, None]
    assert p.fail_names == ("test",) and p.fail_truncated is False


def test_the_fail_marker_of_job_162_does_not_add_a_step():
    """마커는 스텝 경계가 아니다 — 스텝 목록도 개수도 그대로다."""
    plain, declared = progress(JOB_162), progress(JOB_162_DECLARED)
    assert [s.name for s in declared.steps] == [s.name for s in plain.steps]
    assert declared.steps_total == plain.steps_total == 4
    assert declared.steps_done == plain.steps_done == 4


# ── C. R1 · R2 — 선언이 없으면 빈칸이다 ──────────────────────────────────────


def test_a_nonzero_exit_without_any_declaration_names_no_step():
    """**폴백을 없앤다.** exit code 로 스텝을 고르지 않는다(R2 · 뮤테이션 ⑬)."""
    p = progress(["::rcm::step::a", "::rcm::step::b"], exit_code=2)
    assert p.failed_step is None and p.last_step == "b"
    assert [s.ok for s in p.steps] == [True, None]


def test_a_job_with_no_steps_has_no_failed_step_and_no_last_step():
    p = progress(["just output"], exit_code=1)
    assert p.steps == () and p.failed_step is None and p.last_step is None


def test_step_end_fail_still_names_the_step():
    """오늘 있는 마커는 그대로 산다(R1)."""
    p = progress(["::rcm::step::a", "::rcm::step-end::fail", "::rcm::step::b"], exit_code=1)
    assert p.failed_step == "a" and p.last_step == "b"
    assert [s.ok for s in p.steps] == [False, None]


def test_a_declaration_on_a_zero_exit_job_still_names_the_step():
    """순수 계층은 정직하다 — 성공한 잡의 억제는 `outcome_for` 의 일이다(§1.2)."""
    p = progress(["::rcm::step::a", "::rcm::fail::a"], exit_code=0)
    assert p.failed_step == "a" and p.fail_names == ("a",)


def test_the_failed_step_is_the_first_marked_step_not_the_first_marker():
    """R1 은 「`ok is False` 인 **첫 스텝**」이다. 이름 목록은 잡이 찍은 순서를 지킨다."""
    p = progress(
        ["::rcm::step::a", "::rcm::step::b", "::rcm::step::c", "::rcm::fail::c", "::rcm::fail::b"],
        exit_code=1,
    )
    assert p.failed_step == "b"
    assert [s.ok for s in p.steps] == [True, False, False]
    assert p.fail_names == ("c", "b")


def test_a_step_closed_ok_before_a_nonzero_exit_names_no_step():
    p = progress(["::rcm::step::a", "::rcm::step-end::ok"], exit_code=1)
    assert p.failed_step is None and p.last_step == "a"
    assert p.steps[0].ok is True and p.steps_done == 1


# ── D. R3 — 암묵적 닫힘은 True, 선언은 그것을 덮는다 ─────────────────────────


def test_an_implicitly_closed_step_stays_ok():
    p = progress(["::rcm::step::a", "::rcm::step::b", "::rcm::step::c"], exit_code=0)
    assert [s.ok for s in p.steps] == [True, True, True] and p.failed_step is None


def test_a_declaration_overrides_the_implicit_true():
    p = progress(["::rcm::step::a", "::rcm::step::b", "::rcm::fail::a"], exit_code=0)
    assert [s.ok for s in p.steps] == [False, True]
    assert p.failed_step == "a" and p.last_step == "b"


def test_a_fail_marker_before_its_step_still_marks_it():
    """#162 의 핵심 — 선언은 스텝보다 먼저 와도 된다(루프가 끝난 뒤에 적용한다)."""
    p = progress(["::rcm::fail::b", "::rcm::step::a", "::rcm::step::b"], exit_code=1)
    assert p.failed_step == "b" and [s.ok for s in p.steps] == [True, False]


def test_a_fail_name_that_is_not_a_step_marks_nothing():
    """스텝 이름이 아니면 실패한 **단위**다 — 이름만 남고 스텝은 안 물든다(§4.2)."""
    p = progress(
        ["::rcm::step::a", "::rcm::step::b", "::rcm::fail::just_audio_port_test.dart"],
        exit_code=1,
    )
    assert p.failed_step is None and p.last_step == "b"
    assert [s.ok for s in p.steps] == [True, None]
    assert p.fail_names == ("just_audio_port_test.dart",)


def test_a_fail_marker_does_not_open_or_close_a_step():
    p = progress(
        ["::rcm::steps::2", "::rcm::step::a", "::rcm::fail::x"], exit_code=None, finished=None
    )
    assert len(p.steps) == 1 and p.steps_total == 2 and p.steps_done == 0
    assert p.current_index == 1 and p.current_name == "a"
    assert p.fail_names == ("x",)


# ── E. R4 — 잡이 끝나며 닫히는 마지막 스텝 ──────────────────────────────────


def test_the_last_open_step_closes_true_on_exit_zero():
    p = progress(["::rcm::step::a"], exit_code=0)
    assert p.steps[0].ok is True and p.failed_step is None and p.last_step == "a"


def test_the_last_open_step_closes_unknown_on_a_nonzero_exit():
    """모르는 것은 `None` 이다 — `False` 로 물들이면 그게 곧 폴백이다."""
    p = progress(["::rcm::step::a"], exit_code=1)
    assert p.steps[0].ok is None and p.steps[0].state == "done"
    assert p.steps_done == 1 and p.failed_step is None and p.last_step == "a"


def test_a_forced_end_with_no_exit_code_leaves_the_last_step_unknown():
    """취소·시간초과·유실은 `exit_code=None` 으로 온다(§1.2) — 1 을 넣지 않는다."""
    p = progress(["::rcm::step::a"], exit_code=None)
    assert p.steps[0].ok is None and p.failed_step is None


def test_an_explicitly_closed_last_step_keeps_its_own_verdict():
    ok = progress(["::rcm::step::a", "::rcm::step-end::ok"], exit_code=1)
    bad = progress(["::rcm::step::a", "::rcm::step-end::fail"], exit_code=0)
    assert ok.steps[0].ok is True and ok.failed_step is None
    assert bad.steps[0].ok is False and bad.failed_step == "a"


def test_a_running_job_keeps_the_last_step_open():
    p = progress(["::rcm::step::a", "::rcm::step::b"], exit_code=None, finished=None)
    assert p.steps[-1].state == "running" and p.steps[-1].ok is None
    assert p.current_name == "b" and p.last_step == "b" and p.failed_step is None


def test_a_declaration_marks_a_still_running_step():
    """도는 잡의 큐 행이 `✘ {failed_step}` 을 찍는 자리(§1.5) — 선언은 열린 스텝도 물들인다."""
    p = progress(["::rcm::step::a", "::rcm::fail::a"], exit_code=None, finished=None)
    assert p.steps[0].ok is False and p.steps[0].state == "running"
    assert p.current_name == "a" and p.failed_step == "a" and p.steps_done == 0


# ── F. R5 — 같은 이름의 스텝 전부 (매트릭스 · 함정 4) ────────────────────────


def test_every_step_with_the_declared_name_is_marked():
    p = progress(
        ["::rcm::step::test", "::rcm::step::build", "::rcm::step::test", "::rcm::fail::test"],
        exit_code=1,
    )
    assert [s.ok for s in p.steps] == [False, True, False]
    assert [s.index for s in p.steps if s.ok is False] == [1, 3]
    assert p.failed_step == "test" and p.last_step == "test"


def test_a_repeated_declaration_is_one_name():
    p = progress(
        ["::rcm::step::test", "::rcm::fail::test", "::rcm::step::b", "::rcm::fail::test"],
        exit_code=1,
    )
    assert p.fail_names == ("test",) and p.fail_truncated is False


# ── G. R6 — 이름 120자 · 잡당 100개 ─────────────────────────────────────────


def test_a_long_fail_name_is_cut_like_a_step_name():
    """스텝과 마커가 **같은 자리**에서 잘리므로 잘린 뒤에도 서로 맞는다."""
    long = "s" * 200
    p = progress([f"::rcm::step::{long}", f"::rcm::fail::{long}"], exit_code=1)
    assert p.fail_names == ("s" * 120,)
    assert p.steps[0].name == "s" * 120 and p.steps[0].ok is False
    assert p.failed_step == "s" * 120


def test_a_hundred_names_are_all_kept():
    p = progress(fails(*NAMES_100), exit_code=1)
    assert p.fail_names == tuple(NAMES_100) and p.fail_truncated is False


def test_the_hundred_and_first_name_is_dropped_and_flagged():
    p = progress(fails(*NAMES_100, "case101"), exit_code=1)
    assert len(p.fail_names) == MAX_FAIL_NAMES and p.fail_truncated is True
    assert p.fail_names == tuple(NAMES_100) and "case101" not in p.fail_names


def test_duplicates_are_counted_once_and_the_order_is_the_jobs_own():
    p = progress(fails("b", "a", "b", "c", "a"), exit_code=1)
    assert p.fail_names == ("b", "a", "c") and p.fail_truncated is False


def test_a_duplicate_after_the_cap_is_not_a_truncation():
    """이미 센 이름은 아무것도 안 버린다 — 분모의 품질을 거짓말하지 않는다."""
    p = progress(fails(*NAMES_100, "case001"), exit_code=1)
    assert p.fail_names == tuple(NAMES_100) and p.fail_truncated is False


def test_a_dropped_name_does_not_mark_its_step():
    """버린 이름은 없는 이름이다 — 대장에도 안 가고 스텝도 안 물들인다."""
    p = progress(["::rcm::step::test", *fails(*NAMES_100), "::rcm::fail::test"], exit_code=1)
    assert p.fail_truncated is True and "test" not in p.fail_names
    assert p.steps[0].ok is None and p.failed_step is None and p.last_step == "test"


# ── H. Progress 의 새 칸 · `progress_for_job` ───────────────────────────────


def test_the_new_progress_fields_default_to_empty():
    """모르는 값은 `null`, 이름은 빈 튜플 — 스키마 v1 은 키를 더하되 값을 안 바꾼다."""
    p = Progress(phase=PHASE_EXECUTING)
    assert p.last_step is None and p.fail_names == () and p.fail_truncated is False
    assert isinstance(p.fail_names, tuple)


def test_progress_from_markers_returns_a_tuple_of_names():
    p = progress(fails("a", "b"), exit_code=1)
    assert isinstance(p.fail_names, tuple) and p.fail_names == ("a", "b")


def test_progress_for_job_carries_the_new_fields():
    j = job(162, state=FAILED, created_min=12, started_min=11, finished_min=0, exit_code=1)
    plain = progress_for_job(j, markers(JOB_162, start=j.started_at), NOW)
    declared = progress_for_job(j, markers(JOB_162_DECLARED, start=j.started_at), NOW)
    assert plain.failed_step is None and plain.last_step == LAST_162
    assert declared.failed_step == "test" and declared.last_step == LAST_162
    assert declared.fail_names == ("test",)
