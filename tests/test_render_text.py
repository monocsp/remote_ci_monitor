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


def test_host_line_uses_gib_and_two_decimal_load():
    """실기(24 GB Mac mini)에서 `25.8 GB` 와 `load 6.60693359375` 가 찍혔다 — GiB 눈금·두 자리."""
    from datetime import timedelta

    from remote_ci_monitor.core.model import HostSample

    host = HostSample(
        name="macmini",
        source="local",
        sampled_at=NOW - timedelta(seconds=4),
        interval_seconds=5,
        os="darwin",
        cores=10,
        load=(6.60693359375, 5.77, 3.36),
        cpu={"user": 44.2, "sys": 12.7, "idle": 43.1, "busy": 56.9},
        memory={"total_bytes": 24 * 2**30, "used_bytes": 15_100_000_000, "compressed_bytes": None},
        gpu=None,
        gpu_note="disabled",
    )
    out = text(queue=[], hosts=[host])
    assert "load 6.61 / 10 cores" in out, out
    assert "mem 14.1 GB / 24.0 GB" in out, out  # 1e9 로 나누면 25.8 GB 가 되어 기계 사양과 어긋난다
    assert "GPU —" in out


# ── 멈춤 / 조용함 문면 ───────────────────────────────────────────────────────


def reason_text(reason, *, progress=None, **est):
    """이유 칸 한 줄. `est` 는 그대로 `estimate` 가 된다."""
    from remote_ci_monitor.core.render_text import _reason_text

    return _reason_text({"reason": reason, "estimate": est, "progress": progress})


def test_the_stall_reasons_read_differently():
    """근거마다 다른 문장이어야 「무엇을 볼지」가 문면에서 나온다.

    오늘 이 자리를 잠그는 시험이 하나도 없었다 — `"⚠ likely stuck"` 한 줄이 세 가지 사실을
    똑같이 말했다.
    """
    over_step = reason_text(
        "stuck",
        stuck_code="over_step",
        step_expected_seconds=540.0,
        progress={"current_name": "build web"},
    )
    over_elapsed = reason_text(
        "stuck", stuck_code="over_elapsed", elapsed_seconds=1500.0, expected_seconds=400.0
    )
    no_output = reason_text("stuck", stuck_code="no_output")
    quiet = reason_text("quiet")
    assert "build web" in over_step and "9m" in over_step
    assert "3x" in over_elapsed
    assert "no output" in no_output
    assert quiet == "output has gone quiet"
    assert len({over_step, over_elapsed, no_output, quiet}) == 4
    for line in (over_step, over_elapsed, no_output, quiet):
        assert "⚠" not in line and "likely stuck" not in line


def test_an_old_document_without_a_stuck_code_never_says_one_time():
    """옛 서버 문서에는 `stuck_code` 가 없다. 「예상의 1배」는 절대 안 나온다."""
    one = reason_text("stuck", elapsed_seconds=1044.0, expected_seconds=1020.0)
    assert one == "Not responding" and "1x" not in one
    three = reason_text("stuck", elapsed_seconds=1500.0, expected_seconds=400.0)
    assert three == "Not responding · 3x longer than usual"


def test_an_unknown_reason_is_returned_as_is():
    """모르는 값에 던지지 않는다 — 새 서버 + 옛 CLI 가 이 자리로 온다."""
    assert reason_text("brand_new") == "brand_new"


def test_the_queue_line_shows_quiet_and_not_responding():
    """표 한 줄에 실제로 실려 나오는지 — 이유 칸이 조립되는 자리까지 본다."""
    from dataclasses import replace

    j = job(1, state=RUNNING, created_min=6, started_min=5)
    row = rows([j], busy=[1])[0]
    quiet = replace(row, reason="quiet", estimate=replace(row.estimate, quiet=True))
    out = text(queue=[quiet])
    assert "output has gone quiet" in out and "⚠" not in out
