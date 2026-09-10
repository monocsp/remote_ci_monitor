"""`rcm gc` 와 `rcm check` 의 문구(M5i · PR 3) — 두 눈금과 회계의 나이. **구현보다 먼저 썼다.**

- dry-run 의 「would free」는 **예상 회수량**(charged − shared)이고, charged 와 공유 몫을 옆에
  적는다.
- 「would remain」은 그 계획이 잰 스냅샷(`storage_before`)에서 charged 를 뺀 값이다.
- 실제 gc 는 지운 뒤 다시 잰 `storage_after` 와 여유의 전후를 적는다.
- `rcm check` 의 storage 행은 회계가 몇 분 전 것인지 말한다(`measured 57m ago`).
"""

from __future__ import annotations

from remote_ci_monitor.core.render_text import render_gc, storage_row
from test_cli_m5g import base

GB = 10**9  # 렌더의 눈금(`_bytes` 는 10 진 GB 다) — `base()` 의 1024³ 과 섞지 않는다


def planned(job_id: int, *, ws: int, shared: int = 0, reason: str = "age") -> dict:
    return {
        "job_id": job_id,
        "workspace_bytes": ws,
        "snapshot_bytes": 0,
        "shared_bytes": shared,
        "estimated_reclaimable_bytes": ws - shared,
        "reason": reason,
    }


def dry_run(**over) -> dict:
    body = {
        "dry_run": True,
        "planned": [planned(118, ws=2 * GB, shared=GB), planned(131, ws=GB, reason="budget")],
        "deleted": [],
        "failed": [],
        "freed_bytes": 0,
        "deleted_charged_bytes": 0,
        "estimated_reclaimable_bytes": 2 * GB,
        "free_bytes_before": 600 * GB,
        "free_bytes_after": None,
        "storage_before": base(volume_bytes=30 * GB),
        "storage_after": None,
    }
    body.update(over)
    return body


# ── dry-run ──────────────────────────────────────────────────────────────────


def test_would_free_is_the_reclaimable_estimate_not_the_charged_sum() -> None:
    text = render_gc(dry_run())
    assert "would free 2.0 GB" in text  # 3 GB charged − 1 GB shared
    assert "3.0 GB charged" in text and "1.0 GB shared" in text


def test_would_remain_is_the_snapshot_minus_the_charged_bytes() -> None:
    text = render_gc(dry_run())
    assert "27.0 GB would remain" in text  # 30 − 3: 인벤토리는 charged 로 잰다


def test_no_shared_bytes_means_no_parenthesis() -> None:
    text = render_gc(dry_run(planned=[planned(1, ws=GB)], estimated_reclaimable_bytes=GB))
    assert "would free 1.0 GB from 1 jobs" in text and "charged" not in text


def test_an_offline_body_without_the_new_keys_still_renders() -> None:
    """PR 2 의 오프라인 dry-run 은 항목만 준다 — 문구는 항목에서 계산한다."""
    body = {
        "dry_run": True,
        "planned": [planned(118, ws=2 * GB, shared=GB)],
        "deleted": [],
        "failed": [],
        "freed_bytes": 0,
        "storage_before": base(volume_bytes=30 * GB),
        "storage_after": None,
    }
    text = render_gc(body)
    assert "would free 1.0 GB" in text and "28.0 GB would remain" in text


def test_an_old_server_without_shared_bytes_falls_back_to_charged() -> None:
    old = {"job_id": 1, "workspace_bytes": GB, "snapshot_bytes": 0, "reason": "age"}
    text = render_gc(dry_run(planned=[old]))
    assert "would free 1.0 GB" in text


def test_an_unknown_size_is_counted_apart_not_as_zero() -> None:
    unknown = {"job_id": 2, "workspace_bytes": None, "snapshot_bytes": None, "reason": "age"}
    text = render_gc(dry_run(planned=[planned(1, ws=GB), unknown]))
    assert "would free 1.0 GB" in text and "1 of unknown size" in text


# ── 실제 gc ──────────────────────────────────────────────────────────────────


def real_run() -> dict:
    return {
        "dry_run": False,
        "planned": [planned(118, ws=2 * GB, shared=GB), planned(131, ws=GB)],
        "deleted": [planned(118, ws=2 * GB, shared=GB)],
        "failed": [{"job_id": 131, "error_code": "EACCES"}],
        "freed_bytes": 2 * GB,
        "deleted_charged_bytes": 2 * GB,
        "estimated_reclaimable_bytes": GB,
        "free_bytes_before": 600 * GB,
        "free_bytes_after": 601 * GB,
        "storage_before": base(volume_bytes=30 * GB),
        "storage_after": base(volume_bytes=28 * GB, free_bytes=601 * GB),
    }


def test_the_receipt_says_what_was_deleted_and_what_free_space_did() -> None:
    text = render_gc(real_run())
    assert "freed 2.0 GB from 1 jobs" in text
    assert "est. 1.0 GB reclaimable" in text
    assert "free 600.0 GB → 601.0 GB" in text
    assert "28.0 GB left" in text  # 지운 뒤 다시 잰 값
    assert "1 failed (EACCES)" in text


def test_the_receipt_without_free_space_numbers_says_nothing_about_them() -> None:
    body = real_run()
    body["free_bytes_before"] = body["free_bytes_after"] = None
    text = render_gc(body)
    assert "free " not in text.split("\n")[-1].replace("freed", "")


# ── `rcm check` 의 storage 행 — 회계의 나이 (I6) ─────────────────────────────


def test_the_ok_row_says_how_old_the_measurement_is() -> None:
    _, ok, detail = storage_row(
        base(measured_at="2026-09-09T04:21:03Z"), now="2026-09-09T05:18:03Z"
    )
    assert ok is True and "measured 57m ago" in detail


def test_the_over_budget_row_says_it_too() -> None:
    doc = base(volume_bytes=118 * GB, measured_at="2026-09-09T05:17:03Z")
    _, ok, detail = storage_row(doc, now="2026-09-09T05:18:03Z")
    assert ok is None and "measured 1m ago" in detail


def test_the_age_is_coarse_like_the_web() -> None:
    now = "2026-09-09T05:18:03Z"
    assert "measured 12s ago" in storage_row(base(measured_at="2026-09-09T05:17:51Z"), now=now)[2]
    assert "measured 3h ago" in storage_row(base(measured_at="2026-09-09T02:00:03Z"), now=now)[2]


def test_no_now_means_no_age() -> None:
    _, _, detail = storage_row(base(), now=None)
    assert "measured" not in detail and "rcm data" in detail
