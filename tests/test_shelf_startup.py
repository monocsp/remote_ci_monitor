"""보관함(M5p) 기동 화해 — `reconcile_shelf_on_start(store, cfg)` 와 그것을 부르지 않는 곳.

명세는 docs/m5p-workplan.md §5.3 · §15.3(앞 절보다 이긴다). 시나리오 A42~A46 · A65~A68 · A131~A136.
`App.start` 가 이 함수를 부르는지는 P2 가 잠근다(§6 「서버」).
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor.janitor as janitor_mod
from remote_ci_monitor.janitor import Janitor
from remote_ci_monitor.store import Store
from test_janitor import Recorder
from test_store_m5e import ALICE, install_files, policy, raw
from test_store_m5e import collect_result as m5e_result
from test_worker import make_config

NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
D = timedelta(days=1)


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    cfg.server.shelf_dir = str(tmp_path / "shelf")
    store = Store(cfg.data_dir / "rcm.sqlite3")
    store.shelf_enabled = True
    yield store, cfg
    store.close()


def reconcile(store: Store, cfg: Any, rec: Recorder | None = None) -> Any:
    return janitor_mod.reconcile_shelf_on_start(store, cfg, now=NOW, log=(rec.log if rec else None))


def shelf_row(store: Store, job_id: int, state: str, **cols: Any) -> None:
    values: dict[str, Any] = {
        "job_id": job_id,
        "state": state,
        "preset": "golden-update",
        "requested_at": (NOW - D).timestamp(),
    }
    values.update(cols)
    names = ", ".join(values)
    marks = ", ".join("?" * len(values))
    with raw(store) as conn:
        conn.execute(f"INSERT INTO shelf_items ({names}) VALUES ({marks})", tuple(values.values()))


def all_rows(store: Store) -> list[dict[str, Any]]:
    with raw(store) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM shelf_items ORDER BY job_id")]


def row(store: Store, job_id: int) -> dict[str, Any] | None:
    with raw(store) as conn:
        r = conn.execute("SELECT * FROM shelf_items WHERE job_id=?", (job_id,)).fetchone()
    return None if r is None else dict(r)


def marker(cfg: Any) -> Path:
    return cfg.shelf_dir / ".rcm-shelf"


def make_shelf(cfg: Any) -> None:
    cfg.shelf_dir.mkdir(parents=True, exist_ok=True)
    marker(cfg).write_text("rcm shelf\n")


def folder(cfg: Any, job_id: int, files: dict[str, bytes], *, manifest: Any = "auto") -> Path:
    d = cfg.shelf_dir / str(job_id)
    for rel, data in files.items():
        p = d / "files" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    if manifest == "auto":
        manifest = {
            "job": job_id,
            "files": [
                {"path": k, "size": len(v), "sha256": "x", "type": "f"} for k, v in files.items()
            ],
            "expand_skipped": [],
        }
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (d / "manifest.json").write_text(text)
    return d


def job_with_bundle(store: Store, *, state: str = "ready") -> int:
    """끝난 잡 + 묶음. keep 이 아니라 보관 행이 없다 — 시험이 원하는 상태로 넣는다."""
    from remote_ci_monitor.core import artifacts
    from remote_ci_monitor.core.model import QUEUED, SUCCEEDED, Source

    job = store.create_job(
        preset="golden-update",
        inputs={},
        key="golden-update",
        concurrency_group=None,
        source=Source(
            mode="tree", repo="org/app", branch="dev", tree_hash=f"t{os.urandom(4).hex()}"
        ),
        requester=ALICE,
        timeout_seconds=60,
        join_key=None,
        now=NOW - D,
        state=QUEUED,
    )
    assert store.claim(1, NOW - D) is not None
    digest = install_files(store, job.id, b"tar")
    store.start_collect(job.id, policy(), NOW - D)
    store.publish_bundle(
        job.id,
        m5e_result(state=artifacts.READY, bundle_bytes=3, bundle_sha256=digest),
        now=NOW - D,
        expires_at=NOW + D,
    )
    assert store.finish(job.id, SUCCEEDED, now=NOW - D, exit_code=0)
    if state != "ready":
        with raw(store) as conn:
            conn.execute(
                "UPDATE job_artifacts SET state=?, purged_at=? WHERE job_id=?",
                (state, NOW.timestamp(), job.id),
            )
    return job.id


# ── 처음 기동 · staging (A42 · A43 · A135) ───────────────────────────────────


def test_a42_first_start_makes_the_folder_and_the_marker(env):
    store, cfg = env
    assert not cfg.shelf_dir.exists()
    got = reconcile(store, cfg)
    assert got == janitor_mod.ShelfStartup(available=True, error_code=None)
    assert marker(cfg).is_file()
    assert store.shelf_available is True


def test_a43_staging_is_emptied(env):
    store, cfg = env
    make_shelf(cfg)
    (cfg.shelf_dir / ".staging" / "412.ab12" / "files").mkdir(parents=True)
    (cfg.shelf_dir / ".staging" / "412.ab12" / "files" / "x").write_text("x")
    (cfg.shelf_dir / ".staging" / "stray").write_text("s")
    reconcile(store, cfg)
    assert list((cfg.shelf_dir / ".staging").iterdir()) == []


def test_a135_a_link_in_staging_is_removed_without_following(env, tmp_path):
    store, cfg = env
    make_shelf(cfg)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    (cfg.shelf_dir / ".staging").mkdir()
    (cfg.shelf_dir / ".staging" / "link").symlink_to(outside)
    reconcile(store, cfg)
    assert not (cfg.shelf_dir / ".staging" / "link").is_symlink()
    assert (outside / "keep").is_file()


# ── copying 입양 · 되돌림 (A44 · A45 · A66 · A131 · A132) ─────────────────────


def test_a44_a_copying_row_with_a_valid_folder_is_adopted(env):
    store, cfg = env
    make_shelf(cfg)
    j = job_with_bundle(store)
    shelf_row(store, j, "copying")
    d = folder(cfg, j, {"out/a.png": b"png!", "run.log": b"ok"})
    manifest_size = (d / "manifest.json").stat().st_size
    reconcile(store, cfg)
    r = row(store, j)
    assert r["state"] == "active"
    assert r["shelved_at"] == r["last_used_at"] == NOW.timestamp()
    assert r["file_count"] == 2
    assert r["total_bytes"] == 6 + manifest_size  # 원본 + 펼침 + manifest(§4.2-5)
    assert r["job_state"] == "succeeded"


def test_a45_a_copying_row_without_a_folder_goes_back_to_pending(env):
    store, cfg = env
    make_shelf(cfg)
    j = job_with_bundle(store)
    shelf_row(store, j, "copying")
    shelf_row(store, j + 100, "copying")  # 묶음 행 없음
    reconcile(store, cfg)
    assert row(store, j)["state"] == "pending"
    r = row(store, j + 100)
    assert r["state"] == "failed" and r["reason_code"] == "bundle_gone"


@pytest.mark.parametrize("state", ["purged", "expired"])
def test_a66_a_copying_row_whose_bundle_is_gone(env, state):
    store, cfg = env
    make_shelf(cfg)
    j = job_with_bundle(store, state=state)
    shelf_row(store, j, "copying")
    reconcile(store, cfg)
    r = row(store, j)
    assert r["state"] == "failed" and r["reason_code"] == "bundle_gone"


@pytest.mark.parametrize(
    "manifest",
    [{"job": 999, "files": [], "expand_skipped": []}, "{not json", {"job": 1}, None],
)
def test_a131_a132_a_foreign_or_missing_manifest_is_not_adopted(env, manifest):
    """§15.3 — 폴더는 있는데 manifest 가 없거나 무효면 그 폴더는 이 행의 것이다.

    지우고 되돌린다."""
    store, cfg = env
    make_shelf(cfg)
    j = job_with_bundle(store)
    shelf_row(store, j, "copying")
    if isinstance(manifest, dict) and manifest.get("job") == 1:
        manifest = {"job": j}
    d = folder(cfg, j, {"out/a.png": b"png"}, manifest=manifest)
    reconcile(store, cfg)
    assert row(store, j)["state"] == "pending"
    assert not d.exists()


def test_a_listed_file_with_another_size_is_not_adopted(env):
    store, cfg = env
    make_shelf(cfg)
    j = job_with_bundle(store)
    shelf_row(store, j, "copying")
    d = folder(
        cfg,
        j,
        {"out/a.png": b"png"},
        manifest={"job": j, "files": [{"path": "out/a.png", "size": 9}]},
    )
    reconcile(store, cfg)
    assert row(store, j)["state"] == "pending" and not d.exists()


# ── 폴더 없는 항목 · 행 없는 폴더 (A46 · A67 · A136) ──────────────────────────


def test_a67_items_without_a_folder_are_missing(env):
    store, cfg = env
    make_shelf(cfg)
    shelf_row(store, 101, "active", last_used_at=NOW.timestamp())
    shelf_row(store, 102, "stale", last_used_at=NOW.timestamp())
    folder(cfg, 103, {"a.png": b"a"})
    shelf_row(store, 103, "active", last_used_at=NOW.timestamp())
    rec = Recorder()
    reconcile(store, cfg, rec)
    for j in (101, 102):
        r = row(store, j)
        assert r["state"] == "failed" and r["reason_code"] == "missing", j
        assert r["failed_at"] == NOW.timestamp()
    assert row(store, 103)["state"] == "active"
    assert len([m for m in rec.logs if "missing" in m]) == 2


def test_a46_a136_folders_without_rows_are_left_alone(env):
    store, cfg = env
    make_shelf(cfg)
    (cfg.shelf_dir / "999").mkdir()
    (cfg.shelf_dir / "999" / "x").write_text("x")
    (cfg.shelf_dir / "notes").mkdir()
    (cfg.shelf_dir / "tmp-notes").mkdir()
    reconcile(store, cfg)
    assert (cfg.shelf_dir / "999" / "x").is_file()
    assert (cfg.shelf_dir / "notes").is_dir() and (cfg.shelf_dir / "tmp-notes").is_dir()


# ── 표식 (A65 · A133 · §15.3) ────────────────────────────────────────────────


@pytest.mark.parametrize("dir_exists", [False, True])
def test_a65_no_marker_with_items_is_unavailable(env, dir_exists):
    store, cfg = env
    if dir_exists:
        cfg.shelf_dir.mkdir(parents=True)
        (cfg.shelf_dir / ".staging").mkdir()
        (cfg.shelf_dir / ".staging" / "junk").write_text("j")
    shelf_row(store, 101, "active", last_used_at=NOW.timestamp())
    shelf_row(store, 102, "copying")
    before = all_rows(store)
    got = reconcile(store, cfg)
    assert got == janitor_mod.ShelfStartup(available=False, error_code="shelf_dir_missing")
    assert store.shelf_available is False
    assert all_rows(store) == before
    assert cfg.shelf_dir.exists() is dir_exists
    assert not marker(cfg).exists()
    if dir_exists:
        assert (cfg.shelf_dir / ".staging" / "junk").is_file()  # unavailable 이면 손대지 않는다


def test_a133_no_marker_with_only_pending_and_failed_rows_writes_it(env):
    """§15.3 — 표식은 active · stale 행이 0 일 때 쓴다."""
    store, cfg = env
    shelf_row(store, 101, "pending")
    shelf_row(store, 102, "failed", reason_code="shelf_full")
    got = reconcile(store, cfg)
    assert got.available is True and marker(cfg).is_file()


def test_an_unwritable_shelf_dir_is_unavailable(env, tmp_path):
    store, cfg = env
    parent = tmp_path / "ro"
    parent.mkdir()
    cfg.server.shelf_dir = str(parent / "shelf")
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        got = reconcile(store, cfg)
    finally:
        parent.chmod(stat.S_IRWXU)
    assert got == janitor_mod.ShelfStartup(available=False, error_code="shelf_dir_unwritable")
    assert store.shelf_available is False


def test_a134_reconciling_twice_changes_nothing_more(env):
    store, cfg = env
    make_shelf(cfg)
    j = job_with_bundle(store)
    shelf_row(store, j, "copying")
    shelf_row(store, 101, "active", last_used_at=NOW.timestamp())
    k = job_with_bundle(store)
    shelf_row(store, k, "copying")
    folder(cfg, k, {"a.png": b"a"})
    reconcile(store, cfg)
    first = all_rows(store)
    reconcile(store, cfg)
    assert all_rows(store) == first


# ── 부르지 않는 곳 (A68) ─────────────────────────────────────────────────────


def test_a68_offline_gc_store_and_janitor_do_not_reconcile(tmp_path, monkeypatch):
    from remote_ci_monitor.cli import main

    data = tmp_path / "data"
    shelf_dir = tmp_path / "shelf"
    cfg_path = tmp_path / "server.toml"
    cfg_path.write_text(
        f'[server]\ndata_dir = "{data}"\nshelf_dir = "{shelf_dir}"\n'
        '[[presets]]\nname = "gate"\nargv = ["true"]\n'
    )
    store = Store(data / "rcm.sqlite3")
    shelf_row(store, 101, "copying")
    store.close()
    (shelf_dir / ".staging").mkdir(parents=True)
    (shelf_dir / ".staging" / "junk").write_text("j")

    def boom(*a: Any, **kw: Any) -> Any:
        raise AssertionError("reconcile_shelf_on_start must not be called here")

    monkeypatch.setattr(janitor_mod, "reconcile_shelf_on_start", boom)
    assert main(["gc", "--dry-run", "--config", str(cfg_path)]) == 0
    s = Store(data / "rcm.sqlite3")
    try:
        cfg = make_config(tmp_path)
        cfg.server.shelf_dir = str(shelf_dir)
        Janitor(s, cfg)
        assert row(s, 101)["state"] == "copying"
    finally:
        s.close()
    assert (shelf_dir / ".staging" / "junk").is_file()


def test_sqlite_is_new_enough_for_drop_column():
    """도우미 `downgrade_to_v23` 이 `ALTER TABLE … DROP COLUMN` 을 쓴다(3.35+)."""
    assert sqlite3.sqlite_version_info >= (3, 35)


# ── 격리 검증이 찾은 것 (PR #217 · D2 · I3) ──────────────────────────────────


def test_d2_a_staging_that_is_a_link_is_not_followed(env, tmp_path):
    store, cfg = env
    make_shelf(cfg)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("k")
    (cfg.shelf_dir / ".staging").symlink_to(outside)
    reconcile(store, cfg)
    assert (outside / "keep").is_file()
    assert not (cfg.shelf_dir / ".staging").is_symlink()


def test_i3_one_bad_item_does_not_stop_the_start(env, monkeypatch):
    store, cfg = env
    make_shelf(cfg)
    bad = job_with_bundle(store)
    good = job_with_bundle(store)
    shelf_row(store, bad, "copying")
    shelf_row(store, good, "copying")
    real = janitor_mod._valid_manifest

    def picky(folder: Path, job_id: int) -> Any:
        if job_id == bad:
            raise PermissionError(13, "denied")
        return real(folder, job_id)

    monkeypatch.setattr(janitor_mod, "_valid_manifest", picky)
    rec = Recorder()
    got = reconcile(store, cfg, rec)
    assert got.available is True
    rb = row(store, bad)
    assert rb["state"] == "copying" and rb["error_code"] == "reconcile_EACCES"
    assert row(store, good)["state"] == "pending"
    assert any(f"#{bad}" in m and "EACCES" in m for m in rec.logs)
