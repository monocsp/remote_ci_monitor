"""회계의 두 눈금과 사후 측정(M5i · PR 3) — 청소기 쪽. **구현보다 먼저 썼다.**

명세 `docs/gate-replay-fixes-workplan.md` §3 B3 · B4 · I6 · §4-4 · 결정 76.

- `shared_bytes` 는 **일반 파일**(`S_ISREG`)의 `nlink > 1` 블록만 센다. 디렉터리의 `nlink > 1` 은
  하위 디렉터리 때문에 정상이다. 스냅샷 tar 도 같은 규칙.
- 바닥 · would free · latch 분모는 `estimated_reclaimable = charged − shared`. 예산과 항목 표시는
  charged 그대로.
- latch 는 `floor_attempted` 로 걸고, 분모는 **삭제에 성공한 항목**의 예상 회수량이다. 분모 0 이면
  판정하지 않는다.
- 측정 실패는 이름을 지킨다: `measure_<errname>` 이 `inventory_error`·`error_code`·로그에.
- `measured_at` 은 합계에 기여한 측정 중 **가장 오래된** 것. `inventory_checked_at` 은 계획 시각.
- `gc_report` 의 `storage_before` 는 그 계획이 잰 스냅샷이고 `storage_after` 는 지운 **뒤** 다시 잰
  값이다 — 계획을 영수증처럼 내지 않는다.
"""

from __future__ import annotations

import errno
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import remote_ci_monitor.janitor as janitor_mod
from remote_ci_monitor.core.model import FAILED
from remote_ci_monitor.store import Store
from test_janitor import DAY, NOW, finished, make_janitor
from test_janitor_m5g import snapshot, workspace
from test_worker import make_config

GB = 1024**3
BLOCK = 64 * 1024  # 어느 파일 시스템에서든 블록 몇 개는 쥐는 크기


@pytest.fixture
def env(tmp_path):
    """`test_janitor_m5g.env` 와 같다 — 예산·바닥은 끄고 시작한다(테스트가 켠다)."""
    cfg = make_config(
        tmp_path,
        retention_days_success=14,
        retention_days_failure=30,
        workspace_retention_days=1,
        workspace_storage_max_bytes=0,
        min_free_bytes=0,
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def linked_workspace(cfg, job_id: int) -> tuple[Path, int]:
    """unique 하나 + 서로 하드링크인 둘 → charged ≈ 3 × reclaimable.

    (경로, 링크된 파일 하나가 쥔 블록 바이트)를 돌려준다.
    """
    ws = workspace(cfg, job_id, files={"unique": BLOCK})
    (ws / "pack-a").write_bytes(b"p" * BLOCK)
    os.link(ws / "pack-a", ws / "pack-b")
    return ws, os.lstat(ws / "pack-a").st_blocks * 512


def blocks_of(path: Path) -> int:
    return os.lstat(path).st_blocks * 512


def inv_of(jan, now=NOW):
    return {i.job_id: i for i in jan.inventory(now)}


# ── shared_bytes: 일반 파일의 nlink > 1 블록만 ──────────────────────────────


def test_hard_linked_files_are_charged_per_link_but_reclaimable_once(env):
    """운영 실측: 워크스페이스 54.5 GB 중 17.5 GB 가 미러와 공유하는 pack 이었다(§3 B4)."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    _, linked = linked_workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    it = inv_of(jan)[job]
    assert it.shared_bytes == 2 * linked  # pack-a · pack-b 둘 다 nlink 2
    assert it.workspace_bytes >= 3 * linked  # charged 는 링크마다 + 디렉터리 블록
    reclaimable = it.estimated_reclaimable_bytes
    assert reclaimable == it.workspace_bytes - 2 * linked
    assert 2.5 <= it.workspace_bytes / reclaimable <= 3.5  # ≈ 3×


def test_a_file_shared_with_a_mirror_outside_the_workspace_frees_nothing(env, tmp_path):
    """`git clone` 이 미러의 객체를 하드링크한다 — 워크스페이스를 지워도 미러가 그 블록을 쥔다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    ws = workspace(cfg, job, files={})
    mirror = tmp_path / "mirrors" / "repo" / "objects" / "pack"
    mirror.mkdir(parents=True)
    (mirror / "pack-1.pack").write_bytes(b"g" * BLOCK)
    os.link(mirror / "pack-1.pack", ws / "pack-1.pack")
    jan, _ = make_janitor(store, cfg)
    it = inv_of(jan)[job]
    assert it.shared_bytes == blocks_of(ws / "pack-1.pack")
    assert it.estimated_reclaimable_bytes == it.workspace_bytes - it.shared_bytes
    assert it.estimated_reclaimable_bytes < BLOCK  # 디렉터리 블록뿐


def test_a_directory_with_many_links_is_not_shared(env, monkeypatch):
    """디렉터리의 `nlink > 1` 은 하위 디렉터리 때문에 정상이다 — 블록을 쥐어도(ext4) 공유가 아니다.

    APFS 는 디렉터리 블록을 0 으로 보고하므로 여기서는 항목의 stat 을 ext4 모양으로 바꿔 준다:
    조건이 `S_ISREG` 를 안 보면 디렉터리 블록이 shared 로 새어 들어온다(mutcheck 변이).
    """
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    ws = workspace(cfg, job, files={"a": BLOCK})
    (ws / "sub" / "deeper").mkdir()
    real_scandir = os.scandir

    class Entry:
        def __init__(self, e):
            self._e = e
            self.name, self.path = e.name, e.path

        def is_dir(self, *, follow_symlinks=True):
            return self._e.is_dir(follow_symlinks=follow_symlinks)

        def stat(self, *, follow_symlinks=True):
            st = self._e.stat(follow_symlinks=follow_symlinks)
            if not self._e.is_dir(follow_symlinks=False):
                return st
            return SimpleNamespace(st_mode=st.st_mode, st_nlink=3, st_blocks=8)

    class Scan:
        def __init__(self, path):
            self._it = real_scandir(path)

        def __enter__(self):
            return (Entry(e) for e in self._it)

        def __exit__(self, *a):
            self._it.close()

    monkeypatch.setattr(janitor_mod.os, "scandir", Scan)
    jan, _ = make_janitor(store, cfg)
    it = inv_of(jan)[job]
    # 디렉터리 블록(가짜 8 블록 × 2)이 charged 에는 들어간다 — shared 에는 안 들어간다
    assert it.workspace_bytes >= blocks_of(ws / "a") + 2 * 4096
    assert it.shared_bytes == 0


def test_a_hard_linked_snapshot_tar_is_shared_by_the_same_rule(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    tar = snapshot(cfg, job, size=BLOCK)
    os.link(tar, tar.with_name("tree.tar.gz.keep"))
    jan, _ = make_janitor(store, cfg)
    it = inv_of(jan)[job]
    assert it.snapshot_bytes == blocks_of(tar) and it.shared_bytes == blocks_of(tar)
    assert it.estimated_reclaimable_bytes == 0


def test_the_cache_remembers_shared_bytes_with_the_size(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    linked_workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    first = inv_of(jan)[job]
    calls = []
    real = jan._measure_dir
    jan._measure_dir = lambda p: (calls.append(p), real(p))[1]
    second = inv_of(jan)[job]
    assert calls == [] and second.shared_bytes == first.shared_bytes > 0


# ── 예산은 charged, 바닥과 would free 는 reclaimable ─────────────────────────


def test_the_budget_counts_charged_bytes(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    _, linked = linked_workspace(cfg, job)
    # reclaimable(≈ 1 블록분 + 디렉터리)이면 안 넘고 charged(3 블록분)면 넘는 천장
    cfg.server.workspace_storage_max_bytes = 2 * linked + 4096
    jan, _ = make_janitor(store, cfg)
    plan = jan.plan(NOW, free_bytes=None)
    assert [i.job_id for i in plan.items] == [job] and plan.items[0].reason == "budget"


def test_the_dry_run_says_what_it_would_free_in_reclaimable_bytes(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    _, linked = linked_workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    body = jan.gc_report(NOW, dry_run=True)
    item = body["planned"][0]
    assert item["shared_bytes"] == 2 * linked
    assert item["estimated_reclaimable_bytes"] == item["workspace_bytes"] - 2 * linked
    assert body["estimated_reclaimable_bytes"] == item["estimated_reclaimable_bytes"]
    assert body["estimated_reclaimable_bytes"] < item["workspace_bytes"]  # charged 로 과장 않는다


def test_the_floor_keeps_taking_when_the_first_pick_is_mostly_shared(env, monkeypatch):
    store, cfg = env
    shared_job = finished(store, state=FAILED, finished_at=NOW - timedelta(hours=2))
    plain_job = finished(store, state=FAILED, finished_at=NOW - timedelta(hours=1))
    ws = workspace(cfg, shared_job, files={})
    (ws / "pack").write_bytes(b"p" * (4 * BLOCK))
    os.link(ws / "pack", ws / "pack-2")  # charged 8 블록분, reclaimable ≈ 0
    workspace(cfg, plain_job, files={"a": 2 * BLOCK})
    cfg.server.min_free_bytes = 2 * BLOCK
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_free_bytes", lambda: 0)
    plan = jan.plan(NOW)
    # charged 로 쟀다면 첫 것(8 블록분)으로 끝났다 — 실제로는 거의 안 늘어 둘째까지 간다
    assert {i.job_id for i in plan.items} == {shared_job, plain_job}


# ── 무진전 latch: floor_attempted 와 성공분 분모 ─────────────────────────────


def test_the_latch_arms_when_age_picks_already_covered_the_floor(env, monkeypatch):
    """`reason == "free"` 인 항목이 없어도 바닥 아래에서 지웠고 여유가 안 늘었다면 latch 다."""
    store, cfg = env
    old = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, old, files={"a": 2 * BLOCK})
    cfg.server.min_free_bytes = BLOCK  # 나이로 뽑힌 2 블록분이 바닥 부족분을 이미 채운다
    jan, rec = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_free_bytes", lambda: 0)  # 지워도 여유가 안 는다
    plan = jan.sweep_volume(NOW)
    assert [i.reason for i in plan.items] == ["age"] and plan.floor_attempted is True
    assert jan.no_progress is True
    assert any("free space did not move" in e for e in rec.errors)


def test_the_latch_denominator_is_what_was_actually_deleted(env, monkeypatch):
    """둘을 계획했는데 하나만 지워졌고 그 하나만큼은 여유가 늘었다 — 진전이 있었다."""
    store, cfg = env
    a = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    b = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, a, files={"a": 2 * BLOCK})
    workspace(cfg, b, files={"b": 8 * BLOCK})
    cfg.server.min_free_bytes = 100 * GB
    jan, _ = make_janitor(store, cfg)
    real = jan._purge_volume
    monkeypatch.setattr(
        jan, "_purge_volume", lambda job_id: real(job_id) if job_id == a else _raise()
    )
    a_bytes = inv_of(jan)[a].estimated_reclaimable_bytes
    frees = iter([0, a_bytes])  # 지운 만큼만 늘었다
    monkeypatch.setattr(jan, "_free_bytes", lambda: next(frees, a_bytes))
    jan.sweep_volume(NOW)
    assert jan.no_progress is False  # 계획 전체(10 블록분)를 분모로 삼았다면 latch 가 섰다


def test_no_judgement_when_nothing_was_deleted(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 2 * BLOCK})
    cfg.server.min_free_bytes = 100 * GB
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_purge_volume", lambda job_id: _raise())
    monkeypatch.setattr(jan, "_free_bytes", lambda: 0)
    jan.sweep_volume(NOW)
    assert jan.no_progress is False


def test_apply_reports_what_it_deleted_and_its_reclaimable_bytes(env, monkeypatch):
    store, cfg = env
    a = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    b = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    _, linked = linked_workspace(cfg, a)
    workspace(cfg, b)
    jan, _ = make_janitor(store, cfg)
    plan = jan.plan(NOW)
    real = jan._purge_volume
    monkeypatch.setattr(
        jan, "_purge_volume", lambda job_id: real(job_id) if job_id == a else _raise()
    )
    result = jan.apply(plan, NOW)
    assert result.deleted == (a,)
    assert [j for j, _ in result.failed] == [b]
    picked = {i.job_id: i for i in plan.items}
    assert result.charged_bytes == picked[a].workspace_bytes
    assert result.estimated_reclaimable_bytes == picked[a].workspace_bytes - 2 * linked


def _raise():
    raise OSError(errno.EACCES, "nope")


# ── 측정 실패는 이름을 지킨다 (§4-4) ─────────────────────────────────────────


def test_a_measurement_failure_keeps_its_error_name(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job)
    jan, rec = make_janitor(store, cfg)

    def boom(path):
        raise OSError(errno.EACCES, "nope")

    monkeypatch.setattr(jan, "_measure_dir", boom)
    plan = jan.plan(NOW)
    assert plan.inventory_error == "measure_EACCES"
    assert plan.volume_bytes is None
    assert jan.storage(NOW)["error_code"] == "measure_EACCES"
    assert any("measure_EACCES" in e for e in rec.errors)


def test_a_snapshot_measurement_failure_keeps_its_error_name_too(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    snapshot(cfg, job)
    jan, _ = make_janitor(store, cfg)
    real_lstat = Path.lstat

    def lstat(self):
        if self.name == "tree.tar.gz":
            raise OSError(errno.EIO, "bad disk")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert jan.plan(NOW).inventory_error == "measure_EIO"


def test_the_age_rule_still_runs_when_a_measurement_failed(env, monkeypatch):
    store, cfg = env
    old = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, old)
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_measure_dir", lambda p: _raise())
    plan = jan.plan(NOW)
    assert plan.inventory_error == "measure_EACCES" and [i.job_id for i in plan.items] == [old]


# ── 회계의 나이 (I6) ─────────────────────────────────────────────────────────


def test_measured_at_is_the_oldest_measurement_that_contributed(env):
    """캐시된 값이 합계에 들어 있으면 그 시각이 회계의 나이다 — 「방금 쟀다」는 거짓말을 않는다."""
    store, cfg = env
    a = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, a)
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    later = NOW + timedelta(hours=1)
    b = finished(store, state=FAILED, finished_at=later)
    workspace(cfg, b)
    jan.plan(later)  # a 는 캐시(NOW), b 는 지금(later)
    doc = jan.storage(later)
    assert doc["measured_at"] == "2026-09-05T12:00:00Z"
    assert doc["inventory_checked_at"] == "2026-09-05T13:00:00Z"


def test_measured_at_moves_when_everything_is_remeasured(env):
    store, cfg = env
    a = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, a)
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    later = NOW + timedelta(days=2)  # MEASURE_MAX_AGE 를 넘겨 다시 잰다
    jan.plan(later)
    assert jan.storage(later)["measured_at"] == "2026-09-07T12:00:00Z"


def test_an_empty_inventory_was_still_checked_now(env):
    store, cfg = env
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    doc = jan.storage(NOW)
    assert doc["measured_at"] == "2026-09-05T12:00:00Z" == doc["inventory_checked_at"]


# ── gc_report: 스냅샷과 사후 측정 (B3) ───────────────────────────────────────


def test_a_dry_run_reports_the_storage_its_own_plan_measured(env):
    """기동 직후 `rcm gc --dry-run` 이 「0 B would remain」이라고 하던 것 — 직전 측정값이 아니다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job, files={"a": 4 * BLOCK})
    jan, _ = make_janitor(store, cfg)  # 아직 한 번도 안 쟀다
    body = jan.gc_report(NOW, dry_run=True)
    assert body["storage_before"]["volume_bytes"] >= 4 * BLOCK
    assert body["storage_after"] is None
    assert body["free_bytes_before"] is not None and body["free_bytes_after"] is None
    assert body["deleted_charged_bytes"] == 0 and body["freed_bytes"] == 0


def test_a_real_gc_measures_again_after_deleting(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    _, linked = linked_workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    before_bytes = inv_of(jan)[job].workspace_bytes
    body = jan.gc_report(NOW, dry_run=False)
    assert body["storage_before"]["volume_bytes"] == before_bytes
    assert body["storage_after"]["volume_bytes"] == 0  # 지운 **뒤** 잰 값 — 계획 전 값이 아니다
    assert body["deleted_charged_bytes"] == before_bytes == body["freed_bytes"]
    assert body["estimated_reclaimable_bytes"] == before_bytes - 2 * linked
    assert body["free_bytes_before"] is not None and body["free_bytes_after"] is not None


def test_a_failed_delete_is_not_in_the_deleted_bytes(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_purge_volume", lambda job_id: _raise())
    body = jan.gc_report(NOW, dry_run=False)
    assert body["deleted_charged_bytes"] == 0 and body["estimated_reclaimable_bytes"] == 0
    assert body["failed"] == [{"job_id": job, "error_code": "EACCES"}]
    assert body["storage_after"]["volume_bytes"] == body["storage_before"]["volume_bytes"]


def test_a_manual_gc_that_frees_nothing_arms_the_latch_again(env, monkeypatch):
    """사람이 풀고 한 번 더 해 봤는데도 안 늘었다 — 그것도 증거다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, job, files={"a": 2 * BLOCK})
    cfg.server.min_free_bytes = 100 * GB
    jan, _ = make_janitor(store, cfg)
    jan.no_progress = True
    monkeypatch.setattr(jan, "_free_bytes", lambda: 0)
    body = jan.gc_report(NOW, dry_run=False)
    assert body["deleted"] and body["storage_after"]["no_progress"] is True


# ── 회계 문서의 새 키 ────────────────────────────────────────────────────────


def test_storage_carries_shared_and_reclaimable_totals(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    _, linked = linked_workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    jan.plan(NOW)
    doc = jan.storage(NOW)
    assert doc["shared_bytes"] == 2 * linked
    assert doc["estimated_reclaimable_bytes"] == doc["evictable_bytes"] - 2 * linked


@pytest.mark.parametrize(
    "key", ["shared_bytes", "estimated_reclaimable_bytes", "inventory_checked_at"]
)
def test_the_new_keys_are_null_before_the_first_plan(env, key):
    store, cfg = env
    jan, _ = make_janitor(store, cfg)
    assert jan.storage(NOW)[key] is None
