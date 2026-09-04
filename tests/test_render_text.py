"""터미널 렌더 — 빈 큐와 조회 실패가 다르게, 초과 실행은 ETA 대신 —, 표기 규칙."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from jobfactory import CFG, MEDIANS, NOW, PRESETS, default_workers, job
from remote_ci_monitor.core.model import FAILED, RUNNING
from remote_ci_monitor.core.queue import compute_queue
from remote_ci_monitor.core.render_text import fmt_clock, fmt_duration, render
from test_status_schema import model


def rows(jobs, busy=None):
    return compute_queue(
        jobs,
        workers=default_workers(busy or []),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
    )


def text(**kw):
    from remote_ci_monitor.core.status import status_json

    return render(status_json(model(**kw)), tz=UTC)


def test_fmt_duration_and_clock():
    assert fmt_duration(None) == "—"
    assert fmt_duration(12) == "12s"
    assert fmt_duration(310) == "5m 10s"
    assert fmt_duration(3720) == "1h 02m"
    assert fmt_clock("2026-09-04T00:57:22Z", ZoneInfo("Asia/Seoul")) == "09:57"
    assert (
        fmt_clock("2026-09-03T14:40:00Z", UTC, now=datetime(2026, 9, 4, tzinfo=UTC))
        == "Sep 3 · 14:40"
    )
    assert fmt_clock(None, UTC) == "—"


def test_empty_queue_and_query_failure_look_different():
    empty = text(queue=[], recent=[], hosts=[], medians={})
    failed = text(
        queue=None,
        queue_error="database locked",
        recent=None,
        recent_error="db",
        hosts=None,
        hosts_error="x",
        medians=None,
        medians_error="db",
    )
    assert "queue — empty" in empty and "unavailable" not in empty
    assert "queue — unavailable: database locked" in failed and "empty" not in failed
    assert "recent — no completed jobs yet" in empty
    assert "recent — unavailable: db" in failed
    assert "host — no sample yet" in empty
    assert "host — unavailable: x" in failed
    assert "no samples yet" in empty and "medians — unavailable" in failed


def test_running_and_queued_rows_show_eta_reason_and_confidence():
    out = text(
        queue=rows(
            [job(412, state=RUNNING, created_min=2, started_min=1), job(413, created_min=1)],
            busy=[412],
        )
    )
    assert "1 running · 1 waiting" in out
    assert "#412 gate:full" in out and "eta 00:57" in out and "high · measured n=7" in out
    assert "running · lane 1" in out
    assert "  1. · queued" in out and "waiting for lane · behind #412" in out
    assert "waiting 1m 00s" in out


def test_overdue_row_has_no_eta_but_over_by():
    out = text(queue=rows([job(412, state=RUNNING, created_min=10, started_min=9)], busy=[412]))
    assert "eta —" in out and "over by 2m 20s · expected 6m 40s" in out and "(overdue" in out


def test_recent_row_shows_exit_and_failed_step():
    failed = job(
        411,
        "gate:fast",
        FAILED,
        created_min=9,
        started_min=8,
        finished_min=6,
        exit_code=1,
        summary="2 tests failed",
        failed_step="test",
    )
    out = text(queue=[], recent=[failed])
    assert (
        "❌ failed · exit 1 gate:fast" in out
        and "2 tests failed (step test)" in out
        and "2m 00s" in out
    )


def test_header_shows_single_lane_as_one_worker_and_paused():
    from remote_ci_monitor.core.model import Paused

    out = text(queue=[], paused=Paused(by="admin", at=NOW))
    assert "worker busy #412" in out and "PAUSED by admin" in out
    assert "empty but paused" in out
