"""회수 영수증의 사실성(M5l L2 · 리뷰 #88 B1·B2) — 청소기 쪽. **구현보다 먼저 썼다.**

- 크기를 못 잰 항목을 지웠으면 그 수를 `unknown_count` 로 싣는다. 아는 합만 `freed_bytes` 에
  두고 모르는 것을 0 으로 세지 않는다(「never invent a number」).
- 부분 삭제(워크스페이스는 지웠는데 tar 에서 실패)는 `failed[]` 항목에 `removed` 로 무엇이
  사라졌는지 적고, 디스크가 변했으니 **다시 재고** 크기 캐시도 버린다.
"""

from __future__ import annotations

import errno
from pathlib import Path

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
