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


# ── 실패 · 요약 · 단계 ────────────────────────────────────────────────────────


def test_failed_fixture_marks_failed_step_and_summary():
    start = ago(minutes=3)
    finished = ago(seconds=10)
    p = progress_from_markers(
        load("failed.txt", start), started_at=start, finished_at=finished, now=NOW, exit_code=1
    )
    assert p.failed_step == "test"
    assert p.summary == "2 tests failed"
    assert [s.ok for s in p.steps] == [True, False]
    assert p.steps_total == 3 and p.steps_done == 2  # 3개 선언, 2개만 돌았다


def test_nonzero_exit_without_fail_marker_blames_last_step():
    start = ago(minutes=2)
    markers = [Marker(ago(seconds=90), "step", "a"), Marker(ago(seconds=60), "step", "b")]
    p = progress_from_markers(
        markers, started_at=start, finished_at=ago(seconds=1), now=NOW, exit_code=2
    )
    assert p.failed_step == "b" and p.steps[-1].ok is False


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
