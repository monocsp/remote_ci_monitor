"""회수 영수증·storage 행의 문구(M5l L2 · 리뷰 #88 B1·B3·B4). **구현보다 먼저 썼다.**

- 크기를 모르는 채 지운 항목이 있으면 `freed ≥ … (M of unknown size)` — 아는 합을 전체처럼
  확정 표시하지 않는다.
- 측정 실패는 바닥 분기보다 **먼저** 읽힌다 — 못 잰 것을 「지울 것이 없다」로 말하지 않는다.
- `budget_unreachable` 도 숫자를 보여 주므로 측정 나이를 붙인다.
"""

from __future__ import annotations

from remote_ci_monitor.core.render_text import render_gc, storage_row
from test_cli_m5g import base
from test_render_gc_m5i import GB, planned, real_run

NOW = "2026-09-09T05:18:03Z"


def unknown_item(job_id: int) -> dict:
    return {
        "job_id": job_id,
        "workspace_bytes": None,
        "snapshot_bytes": None,
        "shared_bytes": None,
        "estimated_reclaimable_bytes": None,
        "reason": "age",
    }


# ── B1: freed ≥ ──────────────────────────────────────────────────────────────


def test_the_receipt_gives_a_lower_bound_when_a_deleted_size_was_unknown() -> None:
    body = real_run()
    body["deleted"].append(unknown_item(140))
    body["unknown_count"] = 1
    text = render_gc(body)
    assert "freed ≥ 2.0 GB from 2 jobs (1 of unknown size)" in text
    assert "est. ≥ 1.0 GB reclaimable" in text
    assert "freed 2.0 GB" not in text


def test_only_unknown_deletes_never_read_as_freed_zero() -> None:
    body = real_run()
    body.update(
        deleted=[unknown_item(140)],
        failed=[],
        freed_bytes=0,
        deleted_charged_bytes=0,
        estimated_reclaimable_bytes=0,
        unknown_count=1,
    )
    text = render_gc(body)
    assert "freed ≥ 0 B from 1 jobs (1 of unknown size)" in text
    assert "freed 0 B" not in text


def test_an_old_server_without_unknown_count_is_read_from_its_deleted_items() -> None:
    body = real_run()
    body["deleted"].append(unknown_item(140))
    assert "unknown_count" not in body
    assert "freed ≥ 2.0 GB from 2 jobs (1 of unknown size)" in render_gc(body)


def test_a_receipt_with_every_size_known_has_no_bound() -> None:
    body = real_run()
    body["unknown_count"] = 0
    text = render_gc(body)
    assert "freed 2.0 GB from 1 jobs" in text and "≥" not in text


# ── B2: 부분 삭제의 표시 ─────────────────────────────────────────────────────


def test_a_partly_deleted_failure_is_named_in_the_receipt() -> None:
    body = real_run()
    body["failed"] = [{"job_id": 131, "error_code": "EACCES", "removed": ["workspace"]}]
    assert "1 failed (EACCES · 1 partly deleted)" in render_gc(body)


def test_a_failure_that_removed_nothing_is_just_a_failure() -> None:
    body = real_run()
    body["failed"] = [{"job_id": 131, "error_code": "EACCES", "removed": []}]
    text = render_gc(body)
    assert "1 failed (EACCES)" in text and "partly" not in text


# ── B3: 측정 실패가 바닥 분기보다 먼저 ─────────────────────────────────────────


def test_a_measurement_failure_under_the_floor_reads_as_a_measurement_failure() -> None:
    doc = base(
        volume_bytes=None,
        evictable_bytes=None,
        non_evictable_bytes=None,
        free_bytes=1,
        min_free_bytes=10,
        measured_at=None,
        error_code="measure_EACCES",
    )
    _, ok, detail = storage_row(doc, now=NOW)
    assert ok is None  # warn — 다음 sweep 이 다시 잰다. 사람을 부르는 FAIL 이 아니다
    assert "could not be measured" in detail and "measure_EACCES" in detail
    assert "nothing left to delete" not in detail


def test_under_the_floor_with_nothing_evictable_still_fails_when_measured() -> None:
    doc = base(free_bytes=4 * GB, evictable_bytes=0, projected_short_free_bytes=6 * GB)
    _, ok, detail = storage_row(doc, now=NOW)
    assert ok is False and "nothing left to delete" in detail


# ── B4: budget_unreachable 에도 나이 ────────────────────────────────────────


def test_the_budget_unreachable_row_says_how_old_the_measurement_is() -> None:
    doc = base(
        volume_bytes=118 * GB,
        budget_unreachable=True,
        non_evictable_bytes=118 * GB,
        measured_at="2026-09-09T05:17:03Z",
    )
    _, ok, detail = storage_row(doc, now=NOW)
    assert ok is False and "measured 1m ago" in detail


def test_the_planned_helper_is_still_importable() -> None:
    assert planned(1, ws=GB)["job_id"] == 1
