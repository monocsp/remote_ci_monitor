"""보관함(M5p) 청소 — `Janitor._sweep_shelf` · 기준 루트를 받는 삭제 · 묘비와 실패 행 정리 · hold ·
안전판 · `rcm gc` 가 보관함을 안 건드림 · dry-run 계획 함수.

명세는 docs/m5p-workplan.md §4.5 · §5.4 · §15.2 · §15.3. 시나리오 A37~A41 · A63 · A64 · A74~A78 ·
A82 · A96~A98 · A122 · A124 · A125 · A150.

시각은 고정 `NOW` 를 `sweep_once(now)` 에 넘긴다(스레드 없음, sleep 없음).
"""

from __future__ import annotations

import errno
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor.janitor as janitor_mod
from remote_ci_monitor.janitor import Janitor
from remote_ci_monitor.store import Store
from test_janitor import Recorder
from test_janitor_m5e import ARTIFACT_CONFIG
from test_store_m5e import collect_result as m5e_result
from test_store_m5e import install_files, policy, raw
from test_worker import make_config

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
D = timedelta(days=1)
H = timedelta(hours=1)


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, retention_days_success=1, retention_days_failure=2)
    for key, value in ARTIFACT_CONFIG.items():
        setattr(cfg.server, key, value)
    cfg.server.shelf_dir = str(tmp_path / "shelf")
    store = Store(cfg.data_dir / "rcm.sqlite3")
    store.shelf_enabled = True
    startup = janitor_mod.reconcile_shelf_on_start(store, cfg, now=NOW)
    assert startup.available
    yield store, cfg
    store.close()


def janitor(store: Store, cfg: Any, **kw: Any) -> tuple[Janitor, Recorder]:
    rec = Recorder()
    jan = Janitor(store, cfg, now_fn=lambda: NOW, on_error=rec.error, log=rec.log, **kw)
    return jan, rec


def shelf_row(store: Store, job_id: int, state: str, **cols: Any) -> None:
    values: dict[str, Any] = {
        "job_id": job_id,
        "state": state,
        "preset": "golden-update",
        "branch": "dev",
        "requested_at": (NOW - 400 * D).timestamp(),
    }
    values.update(cols)
    names = ", ".join(values)
    marks = ", ".join("?" * len(values))
    with raw(store) as conn:
        conn.execute(f"INSERT INTO shelf_items ({names}) VALUES ({marks})", tuple(values.values()))


def row(store: Store, job_id: int) -> dict[str, Any] | None:
    with raw(store) as conn:
        r = conn.execute("SELECT * FROM shelf_items WHERE job_id=?", (job_id,)).fetchone()
    return None if r is None else dict(r)


def shelf_folder(cfg: Any, job_id: int, files: dict[str, bytes] | None = None) -> Path:
    files = files or {"out/home.png": b"png", "run.log": b"log"}
    d = cfg.shelf_dir / str(job_id)
    for rel, data in files.items():
        p = d / "files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    listed = [{"path": k, "size": len(v)} for k, v in files.items()]
    (d / "manifest.json").write_text(
        json.dumps({"job": job_id, "files": listed, "expand_skipped": []})
    )
    return d


def active(store: Store, cfg: Any, job_id: int, *, used: datetime, state: str = "active") -> Path:
    shelf_row(
        store,
        job_id,
        state,
        last_used_at=used.timestamp(),
        shelved_at=used.timestamp(),
        total_bytes=6,
        file_count=2,
        stale_since=used.timestamp() if state == "stale" else None,
    )
    return shelf_folder(cfg, job_id)


# ── 기준 루트를 받는 삭제 (§4.5-2 · 검토 P1-5) ───────────────────────────────


def test_remove_under_root(tmp_path):
    root = tmp_path / "root"
    (root / "d" / "sub").mkdir(parents=True)
    (root / "d" / "sub" / "x").write_text("x")
    (root / "f").write_text("f")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    (root / "link").symlink_to(outside)
    rr = root.resolve()
    assert janitor_mod.remove_under_root(root / "d", rr) is True
    assert janitor_mod.remove_under_root(root / "f", rr) is True
    assert janitor_mod.remove_under_root(root / "link", rr) is True  # 링크만
    assert janitor_mod.remove_under_root(root / "missing", rr) is False
    assert (outside / "keep").is_file()
    assert not (root / "d").exists() and not (root / "link").exists()
    with pytest.raises(OSError) as e:
        janitor_mod.remove_under_root(outside, rr)
    assert e.value.errno == errno.EXDEV
    assert (outside / "keep").is_file()


# ── 정리 대기 표시 (A37 · A74 · A125) ────────────────────────────────────────


def test_a37_active_turns_stale_after_182_days(env):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 182 * D)
    changed: list[int] = []
    jan, rec = janitor(store, cfg, on_shelf_change=changed.append)
    jan.sweep_once(NOW)
    r = row(store, 101)
    assert r["state"] == "stale" and r["stale_since"] == NOW.timestamp()
    assert d.is_dir()
    stale_logs = [m for m in rec.logs if "stale" in m]
    assert len(stale_logs) == 1  # §15.3 — 이벤트는 서버 로그 한 줄과 SSE 잡 갱신
    assert changed == [101]
    assert rec.errors == []


def test_a74_stale_is_marked_once(env):
    store, cfg = env
    active(store, cfg, 101, used=NOW - 182 * D)
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    jan.sweep_once(NOW + H)
    assert row(store, 101)["stale_since"] == NOW.timestamp()
    assert len([m for m in rec.logs if "stale" in m]) == 1


def test_a125_a_longer_stale_setting_brings_items_back(env):
    store, cfg = env
    active(store, cfg, 101, used=NOW - 200 * D, state="stale")
    cfg.server.shelf_stale_days = 300
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    r = row(store, 101)
    assert r["state"] == "active" and r["stale_since"] is None


# ── 삭제 (A38 · A40 · A41 · A63 · A75 · A76 · A96 · A122 · A150) ─────────────


def test_a38_the_row_is_marked_after_the_folder_is_gone(env, monkeypatch):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 365 * D, state="stale")
    seen: list[str] = []
    real = janitor_mod.remove_under_root

    def spy(path: Path, root: Path) -> bool:
        seen.append(row(store, 101)["state"])
        return real(path, root)

    monkeypatch.setattr(janitor_mod, "remove_under_root", spy)
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert seen == ["stale"]
    r = row(store, 101)
    assert not d.exists()
    assert r["state"] == "deleted" and r["deleted_at"] == NOW.timestamp()
    assert r["total_bytes"] == 0 and r["error_code"] is None
    assert rec.errors == []


def test_a40_a_shelf_outside_data_dir(tmp_path):
    cfg = make_config(tmp_path)
    cfg.server.shelf_dir = str(tmp_path / "other-disk" / "shelf")
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        assert janitor_mod.reconcile_shelf_on_start(store, cfg, now=NOW).available
        d = active(store, cfg, 101, used=NOW - 400 * D)
        jan, rec = janitor(store, cfg)
        jan.sweep_once(NOW)
        assert not d.exists()
        assert row(store, 101)["state"] == "deleted"
        assert not any("EXDEV" in m for m in rec.errors), rec.errors
    finally:
        store.close()


def test_a41_the_sweep_runs_while_the_shelf_is_off(env):
    store, cfg = env
    cfg.server.shelf_max_bytes = 0
    active(store, cfg, 101, used=NOW - 182 * D)
    gone = active(store, cfg, 102, used=NOW - 365 * D, state="stale")
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert row(store, 101)["state"] == "stale"
    assert row(store, 102)["state"] == "deleted" and not gone.exists()


def test_a63_a_failed_delete_leaves_the_row_and_retries(env, monkeypatch):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 365 * D, state="stale")
    real = shutil.rmtree  # 같은 모듈 객체다 — 바꾸기 전에 쥐어 둔다

    def boom(path: Any, *a: Any, **kw: Any) -> None:
        raise PermissionError(errno.EACCES, "denied", str(path))

    monkeypatch.setattr(janitor_mod.shutil, "rmtree", boom)
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    r = row(store, 101)
    assert r["state"] == "stale" and "EACCES" in (r["error_code"] or "")
    assert d.is_dir()
    shelf_errors = [m for m in rec.errors if "101" in m]
    assert len(shelf_errors) == 1 and str(cfg.shelf_dir) not in shelf_errors[0]
    monkeypatch.setattr(janitor_mod.shutil, "rmtree", real)
    jan.sweep_once(NOW + H)
    r = row(store, 101)
    assert r["state"] == "deleted" and r["error_code"] is None and not d.exists()


def test_a96_a_half_done_delete_finishes_next_round(env, monkeypatch):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 365 * D, state="stale")
    real = shutil.rmtree

    def half(path: Any, *a: Any, **kw: Any) -> None:
        (Path(path) / "files" / "run.log").unlink()
        raise OSError(errno.EIO, "io")

    monkeypatch.setattr(janitor_mod.shutil, "rmtree", half)
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    r = row(store, 101)
    assert r["state"] == "stale" and r["error_code"]
    monkeypatch.setattr(janitor_mod.shutil, "rmtree", real)
    jan.sweep_once(NOW + H)
    assert row(store, 101)["state"] == "deleted" and not d.exists()


def test_a75_an_item_folder_that_is_a_link(env, tmp_path):
    store, cfg = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.png").write_bytes(b"x")
    shelf_row(store, 101, "stale", last_used_at=(NOW - 400 * D).timestamp())
    (cfg.shelf_dir / "101").symlink_to(outside)
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert not (cfg.shelf_dir / "101").exists() and not (cfg.shelf_dir / "101").is_symlink()
    assert (outside / "keep.png").is_file()
    assert row(store, 101)["state"] == "deleted"


def test_a76_a_link_inside_pointing_out(env, tmp_path):
    store, cfg = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.png").write_bytes(b"x")
    d = active(store, cfg, 101, used=NOW - 400 * D)
    (d / "files" / "evil").symlink_to(outside)
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert not d.exists()
    assert (outside / "keep.png").is_file()


def test_a122_a_folder_already_gone_is_missing(env):
    """§15.3 — 삭제 직전 폴더가 없으면 `deleted` 가 아니라 `failed(missing)`."""
    store, cfg = env
    shelf_row(store, 101, "stale", last_used_at=(NOW - 400 * D).timestamp())
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    r = row(store, 101)
    assert r["state"] == "failed" and r["reason_code"] == "missing"
    assert r["failed_at"] == NOW.timestamp()


def test_a150_a_shelf_dir_that_is_a_link(tmp_path):
    real_dir = tmp_path / "disk2" / "shelf"
    real_dir.mkdir(parents=True)
    cfg = make_config(tmp_path)
    link = tmp_path / "shelf-link"
    link.symlink_to(real_dir)
    cfg.server.shelf_dir = str(link)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        assert janitor_mod.reconcile_shelf_on_start(store, cfg, now=NOW).available
        d = active(store, cfg, 101, used=NOW - 400 * D)
        jan, _ = janitor(store, cfg)
        jan.sweep_once(NOW)
        assert not d.exists() and row(store, 101)["state"] == "deleted"
        assert link.is_symlink() and real_dir.is_dir()
    finally:
        store.close()


# ── 묘비 · 실패 행 (A39 · A77) ───────────────────────────────────────────────


def test_a39_tombstones_and_failed_rows_go_after_30_days(env):
    store, cfg = env
    shelf_row(store, 101, "deleted", deleted_at=(NOW - 30 * D).timestamp(), total_bytes=0)
    shelf_row(store, 102, "failed", failed_at=(NOW - 30 * D).timestamp(), reason_code="x")
    shelf_row(store, 103, "deleted", deleted_at=(NOW - 30 * D).timestamp() + 1)
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert row(store, 101) is None and row(store, 102) is None
    assert row(store, 103) is not None


# ── hold (A78 · §15.3) ───────────────────────────────────────────────────────


def test_a78_a_held_item_is_skipped_this_round(env):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 400 * D)
    held = {101}
    jan, rec = janitor(store, cfg, shelf_hold=lambda job_id: job_id in held)
    jan.sweep_once(NOW)
    assert d.is_dir() and row(store, 101)["state"] == "active" and rec.errors == []
    held.clear()
    jan.sweep_once(NOW + H)
    assert row(store, 101)["state"] == "deleted" and not d.exists()


# ── 안전판 (A64 · A82 · §15.2) ───────────────────────────────────────────────


def _keep_job_with_bundle(store: Store, expires: datetime) -> int:
    from remote_ci_monitor.core import artifacts
    from remote_ci_monitor.core.model import QUEUED, SUCCEEDED, Requester, Source

    job = store.create_job(
        preset="golden-update",
        inputs={},
        key="golden-update",
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", branch="dev", tree_hash=f"t-{expires}"),
        requester=Requester(name="alice-laptop", label="alice@laptop"),
        timeout_seconds=60,
        join_key=None,
        now=expires - 30 * H,
        state=QUEUED,
        keep=True,
    )
    assert store.claim(1, expires - 30 * H) is not None
    digest = install_files(store, job.id, b"tar")
    store.start_collect(job.id, policy(), expires - 25 * H)
    store.publish_bundle(
        job.id,
        m5e_result(state=artifacts.READY, bundle_bytes=3, bundle_sha256=digest),
        now=expires - 24 * H,
        expires_at=expires,
    )
    assert store.finish(job.id, SUCCEEDED, now=expires - 24 * H, exit_code=0)
    assert row(store, job.id)["state"] == "pending"
    return job.id


def test_a64_the_safety_valve(env):
    store, cfg = env
    j = _keep_job_with_bundle(store, NOW - 24 * H)
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    r = row(store, j)
    assert r["state"] == "failed" and r["reason_code"] == "timeout"
    # 안전판을 묶음 청소 **앞**에서 돌리므로 같은 회차에 묶음도 정상 청소로 간다
    assert store.get_bundle(j)["state"] == "expired"
    assert not (cfg.data_dir / "artifacts" / str(j)).exists()


def test_a82_the_safety_valve_boundary(env):
    store, cfg = env
    j = _keep_job_with_bundle(store, NOW - 24 * H + timedelta(seconds=1))
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert row(store, j)["state"] == "pending"
    assert store.get_bundle(j)["state"] == "ready"
    assert (cfg.data_dir / "artifacts" / str(j) / "bundle.tar").is_file()


# ── 꺼짐 · 디스크 빠짐 · DB 오류 (A97 · A124 · §15.3) ─────────────────────────


def test_an_unavailable_shelf_is_skipped(env):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 400 * D)
    store.shelf_available = False
    jan, _ = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert d.is_dir() and row(store, 101)["state"] == "active"


def test_a124_a_disk_pulled_out_while_running_keeps_the_index(env):
    store, cfg = env
    active(store, cfg, 101, used=NOW - 400 * D)
    shutil.rmtree(cfg.shelf_dir)  # 다른 디스크가 빠졌다 — 표식도 같이 사라졌다
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert row(store, 101)["state"] == "active"
    assert any("shelf" in m for m in rec.errors)
    assert jan.shelf_error_code == "shelf_dir_missing"


def test_no_marker_and_nothing_shelved_is_quiet(env):
    """표식이 없어도 보관된 항목이 없으면 지울 폴더가 없다 — 회차마다 오류를 내지 않는다
    (P1 만 배포된 서버는 아직 기동 화해를 안 부른다)."""
    store, cfg = env
    shutil.rmtree(cfg.shelf_dir)
    shelf_row(store, 101, "deleted", deleted_at=(NOW - 31 * D).timestamp())
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert rec.errors == [] and jan.shelf_error_code is None
    assert row(store, 101) is None


def test_a97_a_database_error_does_not_kill_the_janitor(env):
    store, cfg = env
    d = active(store, cfg, 101, used=NOW - 400 * D)
    expired = _keep_job_with_bundle(store, NOW - 48 * H)
    store.mark_shelf(expired, "failed", NOW - 30 * H, reason_code="shelf_full")
    with raw(store) as conn:
        conn.execute(
            "CREATE TRIGGER block_shelf BEFORE UPDATE ON shelf_items "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
    jan, rec = janitor(store, cfg)
    jan.sweep_once(NOW)
    assert jan.dead is None
    assert any(m.startswith("shelf") for m in rec.errors), rec.errors
    assert store.get_bundle(expired)["state"] == "expired"
    with raw(store) as conn:
        conn.execute("DROP TRIGGER block_shelf")
    jan.sweep_once(NOW + H)
    assert row(store, 101)["state"] == "deleted" and not d.exists()


def test_shelf_sweep_records_its_time(env):
    store, cfg = env
    jan, _ = janitor(store, cfg)
    assert jan.shelf_last_sweep_at is None
    jan.sweep_once(NOW)
    assert jan.shelf_last_sweep_at == NOW and jan.shelf_error_code is None


# ── rcm gc (A98 · §5.4) ──────────────────────────────────────────────────────


def test_a98_gc_leaves_the_shelf_alone(env):
    store, cfg = env
    gone = active(store, cfg, 101, used=NOW - 400 * D)
    active(store, cfg, 102, used=NOW - 200 * D)
    jan, _ = janitor(store, cfg)
    jan.gc(NOW, dry_run=False)
    jan.gc(NOW, dry_run=True)
    assert gone.is_dir()
    assert row(store, 101)["state"] == "active" and row(store, 102)["state"] == "active"


def test_the_dry_run_plan_function_changes_nothing(env):
    """§5.4 — `rcm gc --dry-run` 의 보관함 한 줄(정리 대기 수 · 삭제 예정 수)의 계획 함수(P1)."""
    store, cfg = env
    active(store, cfg, 101, used=NOW - 400 * D)
    active(store, cfg, 102, used=NOW - 200 * D)
    active(store, cfg, 103, used=NOW - 10 * D)
    jan, _ = janitor(store, cfg)
    plan = jan.plan_shelf(NOW)
    assert [i.job_id for i in plan.to_delete] == [101]
    assert [i.job_id for i in plan.to_stale] == [102]
    assert all(row(store, j)["state"] == "active" for j in (101, 102, 103))
