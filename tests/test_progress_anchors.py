"""진행 시각의 기준점(§4.6 (다)) — 화면이 초를 스스로 세게 만든다.

명세는 docs/m5d-workplan.md §4.6 (다). 구현 전이라 빨간 것이 정상이다.

`current_seconds`·`job_seconds` 는 **서버가 문서를 만든 순간**의 값이라 다음 문서가 올 때까지
얼어 있다가 한 번에 뛴다(「9초에서 갑자기 26초」). 고침은 초를 지우는 게 아니라 **시각을 같이
싣는 것**이다 — `job_started_at` 과 `steps[].started_at`·`ended_at` 이 있으면 화면이 1초마다
스스로 세고, 서버가 준 숫자와도 어긋나지 않는다.

키는 **더하기만** 한다. 기존 키는 하나도 사라지지 않는다(스키마 v1 그대로).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from jobfactory import CFG, MEDIANS, NOW, PRESETS, ago, default_workers, job
from remote_ci_monitor.core.model import RUNNING, SUCCEEDED, Progress
from remote_ci_monitor.core.progress import Marker, progress_for_job, progress_from_markers
from remote_ci_monitor.core.queue import compute_queue
from remote_ci_monitor.core.status import iso, parse_iso, progress_json, queue_row_json

# ── 이 변경 직전의 키 집합 ────────────────────────────────────────────────────

BASE_PROGRESS = frozenset(
    {
        "timing",
        "phase",
        "last_output_at",
        "steps_total",
        "steps_total_partial",
        "steps_done",
        "current_index",
        "current_name",
        "current_seconds",
        "job_seconds",
        "failed_step",
        "steps",
    }
)
BASE_STEP = frozenset({"index", "name", "state", "ok", "seconds"})

#: §4.6 (다) 가 더해도 되는 키. 이 밖의 키가 늘면 스키마 v1 약속을 넘은 것이다.
#: `failed_step_guessed` 는 2026-09-08 운영 사고의 후속 — 무죄인 스텝을 지목한 것이 추측임을
#: 밝히는 키다(`tests/test_failed_step_guess.py`).
NEW_PROGRESS = frozenset({"job_started_at", "failed_step_guessed"})
NEW_STEP = frozenset({"started_at", "ended_at"})

JOB_START = ago(seconds=40)
BUILD_AT = ago(seconds=32)
ANALYZE_AT = ago(seconds=12)
FINISH_AT = ago(seconds=4)
#: 스텝 두 개 — build 는 analyze 가 시작할 때 끝났고, analyze 는 아직 돈다.
MARKERS = [Marker(BUILD_AT, "step", "build"), Marker(ANALYZE_AT, "step", "analyze")]


def running() -> Progress:
    """도는 잡 — 마지막 스텝(analyze)이 아직 안 끝났다."""
    return progress_from_markers(
        MARKERS, started_at=JOB_START, finished_at=None, now=NOW, exit_code=None
    )


def done() -> Progress:
    """끝난 잡 — 마지막 스텝은 잡이 끝난 시각에 닫힌다."""
    return progress_from_markers(
        MARKERS, started_at=JOB_START, finished_at=FINISH_AT, now=NOW, exit_code=0
    )


# ── 키는 더하기만 한다 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("build", [running, done])
def test_no_progress_key_that_exists_today_disappears(build):
    doc = progress_json(build())
    assert doc is not None
    assert BASE_PROGRESS <= set(doc)
    assert set(doc) - BASE_PROGRESS <= NEW_PROGRESS
    for step in doc["steps"]:
        assert BASE_STEP <= set(step)
        assert set(step) - BASE_STEP <= NEW_STEP


# ── Progress 의 기준점 ────────────────────────────────────────────────────────


def test_progress_started_at_defaults_to_none():
    """옛 호출부는 `started_at` 을 모른다 — 인자를 안 줘도 만들어진다."""
    p = Progress(phase="executing")
    assert p.started_at is None
    doc = progress_json(p)
    assert doc is not None and doc["job_started_at"] is None


@pytest.mark.parametrize("build", [running, done])
def test_progress_from_markers_keeps_the_started_at_it_was_given(build):
    assert build().started_at == JOB_START


def test_progress_for_job_anchors_on_the_job_start():
    j = job(412, state=RUNNING, created_min=2, started_min=1)
    p = progress_for_job(j, MARKERS, NOW)
    assert p is not None
    assert p.started_at == j.started_at


# ── 도는 잡: 마지막 스텝은 열려 있다 ─────────────────────────────────────────


def test_a_running_job_leaves_the_last_step_without_an_end():
    first, last = running().steps
    assert first.name == "build"
    assert first.started_at == BUILD_AT and first.ended_at == ANALYZE_AT
    assert last.name == "analyze" and last.state == "running"
    assert last.started_at == ANALYZE_AT
    assert last.ended_at is None  # 도는 중 — 끝난 척하지 않는다


def test_a_finished_job_has_an_end_for_every_step():
    p = done()
    assert all(s.ended_at is not None for s in p.steps)
    assert p.steps[0].ended_at == ANALYZE_AT
    assert p.steps[-1].ended_at == FINISH_AT  # 마지막 스텝은 잡이 끝난 시각에 끝난다
    assert p.steps[-1].state == "done"


# ── 문서의 기준점은 다른 시각 키와 같은 꼴이다 ───────────────────────────────


def test_progress_json_carries_the_anchors_as_iso_strings():
    doc = progress_json(running())
    assert doc is not None
    assert doc["job_started_at"] == iso(JOB_START)
    assert [s["started_at"] for s in doc["steps"]] == [iso(BUILD_AT), iso(ANALYZE_AT)]
    assert doc["steps"][0]["ended_at"] == iso(ANALYZE_AT)
    assert doc["steps"][-1]["ended_at"] is None


def test_a_finished_job_ships_an_end_for_every_step_in_the_document():
    doc = progress_json(done())
    assert doc is not None
    assert [s["ended_at"] for s in doc["steps"]] == [iso(ANALYZE_AT), iso(FINISH_AT)]
    assert all(isinstance(s["started_at"], str) for s in doc["steps"])


def test_the_anchors_use_the_same_iso_shape_as_every_other_time_key():
    """`iso()` 는 UTC · 초 단위 · `Z` 다 — 마이크로초가 새면 화면이 다시 못 센다."""
    start = datetime(2026, 9, 8, 3, 4, 5, 123456, tzinfo=UTC)
    step_at = datetime(2026, 9, 8, 3, 4, 9, 987654, tzinfo=UTC)
    now = datetime(2026, 9, 8, 3, 5, 0, tzinfo=UTC)
    p = progress_from_markers(
        [Marker(step_at, "step", "analyze")],
        started_at=start,
        finished_at=None,
        now=now,
        exit_code=None,
    )
    doc = progress_json(p)
    assert doc is not None
    assert doc["job_started_at"] == "2026-09-08T03:04:05Z" == iso(start)
    assert doc["steps"][0]["started_at"] == "2026-09-08T03:04:09Z" == iso(step_at)


# ── 화면이 다시 셀 수 있다 ───────────────────────────────────────────────────


def test_the_browser_can_recount_the_seconds_from_the_anchors_alone():
    """기준점 + `now` 만으로 `current_seconds`·`job_seconds` 가 나온다 — 화면이 그렇게 센다."""
    doc = progress_json(running())
    assert doc is not None
    job_start = parse_iso(doc["job_started_at"])
    step_start = parse_iso(doc["steps"][-1]["started_at"])
    assert job_start is not None and step_start is not None
    assert (NOW - step_start).total_seconds() == pytest.approx(doc["current_seconds"])
    assert (NOW - job_start).total_seconds() == pytest.approx(doc["job_seconds"])
    assert doc["current_seconds"] == pytest.approx(12)
    assert doc["job_seconds"] == pytest.approx(40)


def test_a_done_step_reads_the_same_seconds_from_its_two_anchors():
    """끝난 스텝의 초는 고정이다 — `ended_at - started_at` 과 어긋나면 안 된다."""
    doc = progress_json(done())
    assert doc is not None
    for step in doc["steps"]:
        began = parse_iso(step["started_at"])
        ended = parse_iso(step["ended_at"])
        assert began is not None and ended is not None
        assert (ended - began).total_seconds() == pytest.approx(step["seconds"])


def test_a_finished_job_is_not_given_a_moving_clock():
    """끝난 잡의 초는 서버 값 그대로 고정이다 — 기준점이 그 사실을 바꾸지 않는다."""
    doc = progress_json(done())
    assert doc is not None
    assert doc["current_index"] is None and doc["current_name"] is None
    assert doc["current_seconds"] is None
    assert doc["job_seconds"] == pytest.approx((FINISH_AT - JOB_START).total_seconds())
    assert doc["steps"][-1]["ended_at"] == iso(FINISH_AT)


# ── 화면이 실제로 읽는 자리 ──────────────────────────────────────────────────


def test_the_anchors_travel_in_the_queue_row_the_screen_reads():
    """화면은 `pools[].queue[].progress` 를 읽는다 — 거기까지 실려야 쓸모가 있다."""
    j = job(412, state=RUNNING, created_min=2, started_min=1)
    p = progress_for_job(j, MARKERS, NOW)
    rows = compute_queue(
        [j],
        workers=default_workers([412]),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
        progress={j.id: p},
    )
    row = queue_row_json(rows[0])
    doc = row["progress"]
    assert doc is not None
    assert doc["job_started_at"] == iso(j.started_at)
    assert doc["steps"][-1]["started_at"] == iso(ANALYZE_AT)
    assert doc["steps"][-1]["ended_at"] is None


def test_progress_for_a_finished_job_anchors_on_its_start_and_closes_every_step():
    j = job(413, state=SUCCEEDED, created_min=3, started_min=2, finished_min=0.05, exit_code=0)
    p = progress_for_job(j, MARKERS, NOW)
    assert p is not None
    doc = progress_json(p)
    assert doc is not None
    assert doc["job_started_at"] == iso(j.started_at)
    assert all(s["ended_at"] is not None for s in doc["steps"])
    assert doc["steps"][-1]["ended_at"] == iso(j.finished_at)
