"""스텝 마커 — PLAN.md 「진행 규칙」 표의 함정 6개(이름 그대로) + 파서 · 픽스처 3종."""

from datetime import timedelta
from pathlib import Path

from jobfactory import CFG, NOW, ago, job
from remote_ci_monitor.core.model import PHASE_MATERIALIZING, QUEUED, RUNNING, SUCCEEDED, UPLOADING
from remote_ci_monitor.core.progress import (
    Marker,
    markers_from_log,
    parse_marker,
    progress_for_job,
    progress_from_markers,
)
from remote_ci_monitor.core.queue import remaining_seconds

FIXTURES = Path(__file__).parent / "fixtures" / "logs"


def load(name: str, start, step_seconds: float = 10.0):
    """픽스처의 줄마다 수신 시각을 start + i×step 으로 붙여 마커만 뽑는다."""
    lines = (FIXTURES / name).read_text().splitlines()
    timed = [(start + timedelta(seconds=i * step_seconds), line) for i, line in enumerate(lines)]
    return markers_from_log(timed)


# ── 파서 ─────────────────────────────────────────────────────────────────────


def test_parse_marker_accepts_only_line_start_and_known_kinds():
    assert parse_marker("::rcm::step::test") == ("step", "test")
    assert parse_marker("::rcm::steps::8") == ("steps", "8")
    assert parse_marker("::rcm::step-end::fail\n") == ("step-end", "fail")
    assert parse_marker("::rcm::summary:: 2 tests failed ") == ("summary", "2 tests failed")
    assert parse_marker("prefix ::rcm::step::x") is None
    assert parse_marker("::rcm::unknown::x") is None
    assert parse_marker("::rcm::steps::eight") is None
    assert parse_marker("::rcm::step-end::maybe") is None
    assert parse_marker("::rcm::step::") is None


# ── 표의 함정 6개 ─────────────────────────────────────────────────────────────


def test_step_total_partial_without_declaration():
    start = ago(minutes=2)
    p = progress_from_markers(
        load("undeclared.txt", start), started_at=start, finished_at=None, now=NOW, exit_code=None
    )
    assert p.steps_total_partial is True
    assert p.steps_total == 4  # 지금까지 알려진 수 (so far)
    assert p.steps_done == 3 and p.current_index == 4 and p.current_name == "package"


def test_last_step_ends_at_job_end():
    start = ago(minutes=2)
    finished = ago(seconds=5)
    p = progress_from_markers(
        load("declared.txt", start), started_at=start, finished_at=finished, now=NOW, exit_code=0
    )
    assert p.steps_total == 4 and p.steps_total_partial is False
    assert p.steps_done == 4 and p.current_index is None
    last = p.steps[-1]
    assert last.name == "lint" and last.ended_at == finished and last.ok is True
    assert p.job_seconds == (finished - start).total_seconds()
    assert p.summary == "all 4 steps green"


def test_marker_timestamps_are_receive_times():
    # 버퍼링으로 두 마커가 같은 순간에 몰려 왔다 — 스텝 시각은 파싱한 게 아니라 수신 시각이다
    start = ago(minutes=1)
    burst = ago(seconds=3)
    markers = [Marker(burst, "step", "a"), Marker(burst, "step", "b")]
    p = progress_from_markers(markers, started_at=start, finished_at=None, now=NOW, exit_code=None)
    assert p.timing == "as_received"
    assert p.steps[0].started_at == burst and p.steps[0].seconds == 0
    assert p.steps[1].started_at == burst and p.steps[1].seconds == 3


def test_duplicate_step_names_by_index():
    start = ago(minutes=2)
    p = progress_from_markers(
        load("undeclared.txt", start), started_at=start, finished_at=None, now=NOW, exit_code=None
    )
    builds = [s for s in p.steps if s.name == "build"]
    assert [s.index for s in builds] == [2, 3]
    assert len({s.index for s in p.steps}) == len(p.steps)


def test_queued_job_has_no_progress():
    assert progress_for_job(job(1, state=QUEUED), [], NOW) is None
    assert progress_for_job(job(2, state=UPLOADING), [], NOW) is None
    running = job(3, state=RUNNING, created_min=2, started_min=1)
    p = progress_for_job(running, [], NOW)
    assert p is not None and p.steps == () and p.steps_total is None and p.steps_done == 0


def test_overdue_run_floors_remaining():
    assert remaining_seconds(400, 1000, CFG) == CFG.floor_remaining_seconds
    assert remaining_seconds(400, 1000, CFG) > 0


def test_a_parallel_script_never_blames_the_step_it_ended_on():
    """함정 #7 — 스텝을 병렬로 돌리고 마커를 나중에 몰아 내보내는 스크립트.

    2026-09-08 운영에서 나온 모양이다: 게이트가 test·gitleaks·build web 을 동시에 돌린 뒤
    마커를 정해진 순서로 몰아 찍는다. `::rcm::step-end::fail` 은 하나도 안 나오고 마지막
    마커는 언제나 `build web` 이라, 실제로 깨진 건 `test` 인데 폴백이 `build web` 을 범인으로
    찍었다(실패 55건 중 16건). 마커만 보고 `test` 를 골라낼 방법은 없다.

    PR #71 은 그 이름을 남기고 「추측」이라고 밝혔고, **M5h(결정 63)는 이름을 아예 안 댄다** —
    `last_step` 이 「끝났을 때 어디였나」만 말한다. 스크립트가 `::rcm::fail::test` 를 찍으면
    그때 이름이 돌아온다(`tests/test_progress_m5h.py`).
    """
    start = ago(minutes=5)
    names = ["test", "gitleaks", "build web"]
    markers = [Marker(start + timedelta(seconds=i), "step", n) for i, n in enumerate(names)]
    p = progress_from_markers(
        markers, started_at=start, finished_at=ago(seconds=1), now=NOW, exit_code=1
    )
    assert p.failed_step is None  # 무죄인 스텝을 범인으로 지목하지 않는다
    assert p.last_step == "build web"  # 어디였나만 말한다 — 인과는 주장하지 않는다


def test_a_fail_marker_anywhere_makes_the_blame_certain():
    """같은 스크립트가 `step-end::fail` 을 하나라도 찍으면 폴백을 안 탄다 — 확정이다."""
    start = ago(minutes=5)
    markers = [
        Marker(start + timedelta(seconds=1), "step", "test"),
        Marker(start + timedelta(seconds=2), "step-end", "fail"),
        Marker(start + timedelta(seconds=3), "step", "build web"),
    ]
    p = progress_from_markers(
        markers, started_at=start, finished_at=ago(seconds=1), now=NOW, exit_code=1
    )
    assert p.failed_step == "test"  # `::rcm::fail::` 이 지목했다 — 선언이다


def test_a_green_job_has_nothing_to_guess():
    start = ago(minutes=5)
    markers = [Marker(start + timedelta(seconds=1), "step", "test")]
    p = progress_from_markers(
        markers, started_at=start, finished_at=ago(seconds=1), now=NOW, exit_code=0
    )
    assert p.failed_step is None


def test_a_job_with_no_markers_at_all_guesses_nothing():
    """스텝이 없으면 지목할 이름도 없다 — `failed_step` 은 null 이고 추측도 아니다."""
    start = ago(minutes=5)
    p = progress_from_markers(
        [], started_at=start, finished_at=ago(seconds=1), now=NOW, exit_code=1
    )
    assert p.failed_step is None


# ── 실패 · 요약 · 단계 ────────────────────────────────────────────────────────


def test_failed_fixture_marks_failed_step_and_summary():
    start = ago(minutes=3)
    finished = ago(seconds=10)
    p = progress_from_markers(
        load("failed.txt", start), started_at=start, finished_at=finished, now=NOW, exit_code=1
    )
    assert p.failed_step == "test"
    assert p.steps[-1].ok is False  # step-end::fail 이 찍혔다 — 선언이다
    assert p.summary == "2 tests failed"
    assert [s.ok for s in p.steps] == [True, False]
    assert p.steps_total == 3 and p.steps_done == 2  # 3개 선언, 2개만 돌았다


def test_nonzero_exit_without_a_declaration_names_no_step():
    """M5h 결정 63 — 옛 규칙은 「종료 코드 ≠ 0 이면 마지막 스텝」이었다. 그 추론이 되재생·병렬
    스크립트에서 **성공한 스텝을 실패로 불렀다**(운영 잡 #162). 이제 모르면 `None` 이고
    「어디였나」는 `last_step` 이 인과 없이 말한다. 선언된 실패는 `test_progress_m5h.py`."""
    start = ago(minutes=2)
    markers = [Marker(ago(seconds=90), "step", "a"), Marker(ago(seconds=60), "step", "b")]
    p = progress_from_markers(
        markers, started_at=start, finished_at=ago(seconds=1), now=NOW, exit_code=2
    )
    assert p.failed_step is None
    assert p.last_step == "b" and p.steps[-1].ok is None


def test_step_end_fail_while_still_running_keeps_going():
    start = ago(minutes=2)
    markers = [
        Marker(ago(seconds=90), "step", "test"),
        Marker(ago(seconds=60), "step-end", "fail"),
        Marker(ago(seconds=59), "step", "report"),
    ]
    p = progress_from_markers(markers, started_at=start, finished_at=None, now=NOW, exit_code=None)
    assert p.failed_step == "test"
    assert p.current_name == "report" and p.steps_done == 1


def test_phase_and_last_output_are_carried():
    j = job(
        1,
        state=RUNNING,
        created_min=2,
        started_min=1,
        phase=PHASE_MATERIALIZING,
        last_output_at=ago(seconds=4),
    )
    p = progress_for_job(j, [], NOW)
    assert p.phase == "materializing" and p.last_output_at == ago(seconds=4)


def test_finished_job_progress_uses_finished_at_not_now():
    j = job(1, state=SUCCEEDED, created_min=5, started_min=4, finished_min=1, exit_code=0)
    p = progress_for_job(j, [Marker(ago(minutes=3), "step", "x")], NOW)
    assert p.job_seconds == 180 and p.steps[0].seconds == 120
