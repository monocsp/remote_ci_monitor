"""회수 영수증의 사실성(M5l L2 · 리뷰 #88 B1·B2) — 청소기 쪽. **구현보다 먼저 썼다.**

- 크기를 못 잰 항목을 지웠으면 그 수를 `unknown_count` 로 싣는다. 아는 합만 `freed_bytes` 에
  두고 모르는 것을 0 으로 세지 않는다(「never invent a number」).
- 부분 삭제(워크스페이스는 지웠는데 tar 에서 실패)는 `failed[]` 항목에 `removed` 로 무엇이
  사라졌는지 적고, 디스크가 변했으니 **다시 재고** 크기 캐시도 버린다.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from remote_ci_monitor.core.model import FAILED
from remote_ci_monitor.core.render_text import render_gc
from test_janitor import DAY, NOW, finished, make_janitor
from test_janitor_m5g import snapshot, workspace
from test_janitor_m5i import BLOCK, env  # noqa: F401 — 픽스처

_ = env


def _raise(code: int = errno.EACCES):
    raise OSError(code, "nope")


# ── B1: 크기를 모르는 채 지운 항목 ───────────────────────────────────────────


def test_a_deleted_item_of_unknown_size_is_counted_apart_not_as_zero(env, monkeypatch):
    """측정은 EACCES 로 실패했지만 나이 규칙이 골랐고 삭제는 됐다 — `freed 0 B` 가 아니다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 4 * BLOCK})
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_measure_dir", lambda p: _raise())
    body = jan.gc_report(NOW, dry_run=False)
    assert [d["job_id"] for d in body["deleted"]] == [job]
    assert body["unknown_count"] == 1
    assert body["freed_bytes"] == 0 == body["deleted_charged_bytes"]  # 아는 합 — 전체가 아니다
    assert not (cfg.data_dir / "workspaces" / str(job)).exists()
    text = render_gc(body)
    assert "freed ≥ 0 B from 1 jobs (1 of unknown size)" in text
    assert "freed 0 B" not in text


def test_no_unknown_deletes_means_unknown_count_zero_and_no_bound(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 4 * BLOCK})
    jan, _ = make_janitor(store, cfg)
    body = jan.gc_report(NOW, dry_run=False)
    assert body["unknown_count"] == 0 and body["freed_bytes"] > 0
    assert "≥" not in render_gc(body)


def test_a_dry_run_carries_unknown_count_zero(env):
    store, cfg = env
    jan, _ = make_janitor(store, cfg)
    assert jan.gc_report(NOW, dry_run=True)["unknown_count"] == 0


# ── B2: 부분 삭제 ────────────────────────────────────────────────────────────


def _tar_fails(jan, monkeypatch):
    """`_remove_tree` 가 워크스페이스는 진짜로 지우고 tar 에서 EACCES."""
    real = jan._remove_tree

    def remove(path: Path):
        if path.name == "tree.tar.gz":
            _raise()
        return real(path)

    monkeypatch.setattr(jan, "_remove_tree", remove)


def test_a_partial_delete_says_what_went_and_measures_again(env, monkeypatch):
    """워크스페이스는 사라졌는데 tar 에서 실패 — 디스크는 변했다. 회계도 따라와야 한다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 4 * BLOCK})
    snapshot(cfg, job, size=BLOCK)
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    assert job in jan._sizes  # 캐시에 들어 있다
    _tar_fails(jan, monkeypatch)
    body = jan.gc_report(NOW, dry_run=False)
    assert body["deleted"] == []
    assert body["failed"] == [{"job_id": job, "error_code": "EACCES", "removed": ["workspace"]}]
    assert not (cfg.data_dir / "workspaces" / str(job)).exists()
    assert (cfg.data_dir / "jobs" / str(job) / "tree.tar.gz").exists()
    before, after = body["storage_before"]["volume_bytes"], body["storage_after"]["volume_bytes"]
    assert after < before  # 지운 **뒤** 다시 쟀다
    assert after >= BLOCK  # tar 은 남았다
    assert job not in jan._sizes  # 캐시 무효화 — 다음 sweep 이 옛 크기를 들고 있지 않다
    assert jan.storage(NOW)["volume_bytes"] == after  # `/api/status` 도 같은 값
    assert "1 failed (EACCES · 1 partly deleted)" in render_gc(body)


def test_apply_reports_the_partial_delete_apart_from_the_deleted(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job)
    snapshot(cfg, job)
    jan, _ = make_janitor(store, cfg)
    _tar_fails(jan, monkeypatch)
    result = jan.apply(jan.plan(NOW), NOW)
    assert result.deleted == () and result.failed == ((job, "EACCES"),)
    assert result.partial == ((job, ("workspace",)),)
    assert result.charged_bytes == 0  # 반만 지운 것을 다 지운 것처럼 더하지 않는다


def test_a_failed_delete_that_removed_nothing_has_an_empty_removed_list(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    monkeypatch.setattr(jan, "_remove_tree", lambda path: _raise(errno.EIO))
    body = jan.gc_report(NOW, dry_run=False)
    assert body["failed"] == [{"job_id": job, "error_code": "EIO", "removed": []}]
    assert body["storage_after"]["volume_bytes"] == body["storage_before"]["volume_bytes"]
    assert "partly" not in render_gc(body)


def test_the_periodic_sweep_remeasures_after_a_partial_delete_too(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 4 * BLOCK})
    snapshot(cfg, job, size=BLOCK)
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    before = jan.storage(NOW)["volume_bytes"]
    _tar_fails(jan, monkeypatch)
    jan.sweep_volume(NOW)
    assert jan.storage(NOW)["volume_bytes"] < before


# ── L2.2: 읽을 수 없는 `jobs/<id>` 는 그 항목만 모른다 — 인벤토리 전체가 아니다 ────────


@pytest.mark.skipif(os.geteuid() == 0, reason="root 는 권한 비트를 무시한다")
def test_an_unreadable_job_dir_is_one_unknown_item_not_a_lost_inventory(env):
    """`chmod 000 jobs/<id>` — tar 의 lstat 이 EACCES. `Path.is_file()` 은 EACCES 를 삼키지 않아
    스캔 전체가 `scan_EACCES` 로 죽고 나이 규칙까지 멈췄다(M5l L2.2 실기). 그 항목의 스냅샷만
    모르고, 코드는 `measure_EACCES`, 계획은 `1 of unknown size` 여야 한다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 4 * BLOCK})
    snapshot(cfg, job, size=BLOCK)
    other = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, other, files={"a": 4 * BLOCK})
    job_dir = cfg.data_dir / "jobs" / str(job)
    job_dir.chmod(0)
    try:
        jan, rec = make_janitor(store, cfg)
        plan = jan.plan(NOW)
        assert plan.inventory_error == "measure_EACCES"
        assert sorted(i.job_id for i in plan.items) == sorted([job, other])
        hit = next(i for i in plan.items if i.job_id == job)
        assert hit.snapshot_bytes is None and hit.workspace_bytes is not None
        assert "retention: measure" in " ".join(rec.errors)
        body = jan.gc_report(NOW, dry_run=True)
        assert body["storage_before"]["error_code"] == "measure_EACCES"
        assert "1 of unknown size" in render_gc(body)
    finally:
        job_dir.chmod(0o755)
