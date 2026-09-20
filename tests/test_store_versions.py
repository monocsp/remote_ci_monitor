"""저장소 v20 · v21 — `versions` 표(스토어 버전 드래프트 대장). 새 DB · v19 → 최신 마이그레이션
(백업 포함) · v20 → v21 의 `upload_job_id` · 옛 빌드의 거절 · CRUD · 만료 조회 · `update_version` 이
모르는 열을 거절 · 청소기가 이 표를 건드리지 않는다.

명세: docs/version-page-workplan.md §2.1 · §14-2 · §14-3 · AC-B1 · 결정 74(마이그레이션 전 백업).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.model import SUCCEEDED
from remote_ci_monitor.janitor import Janitor
from remote_ci_monitor.store import DB_VERSION, Store, StoreError
from test_store import enqueue

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
TTL = timedelta(hours=24)
COLUMNS = [
    "id",
    "repo",
    "ios_version",
    "android_version",
    "state",
    "created_by",
    "created_at",
    "last_edit_at",
    "expires_at",
    "asc_version_id",
    "prefill_json",
    "edited_json",
    "error",
    "create_job_id",
    "delete_job_id",
    "release_id",
    "review_job_id",
    "expiry_warned",
    "upload_job_id",
]


def columns(path: Path, table: str) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    finally:
        c.close()


def tables(path: Path) -> set[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def add_version(
    store: Store,
    ios: str | None = "1.1.1",
    android: str | None = "1.0.1",
    *,
    state: str = "creating",
    now: datetime = NOW,
    by: str = "macmini-admin",
) -> int:
    return store.create_version(
        repo="app",
        ios_version=ios,
        android_version=android,
        state=state,
        created_by=by,
        now=now,
        expires_at=now + TTL,
    )


def test_this_build_is_database_version_21():
    assert DB_VERSION == 21


def test_a_fresh_database_has_the_versions_table_and_index(tmp_path):
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert columns(path, "versions") == COLUMNS
        names = {
            r[0]
            for r in sqlite3.connect(path).execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='versions'"
            )
        }
        assert "versions_repo" in names
        assert s.count_versions() == 0
    finally:
        s.close()


def test_a_v19_database_gets_the_table_a_backup_and_the_newest_version(tmp_path):
    """v19 DB(표가 없다)를 이 빌드로 열면 `backup/rcm.sqlite3.v19.bak` 을 먼저 남기고 표를 만든다.
    잡 · 릴리스 행은 그대로다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    enqueue(s, tree="aaaa", now=NOW)
    rid = s.create_release(
        repo="app", build_name="1.0.1", kind="start", started_by="admin", now=NOW, log_path="/x"
    )
    s.close()
    c = sqlite3.connect(path)
    c.execute("DROP INDEX versions_repo")
    c.execute("DROP TABLE versions")
    c.execute("PRAGMA user_version=19")
    c.commit()
    c.close()
    assert "versions" not in tables(path)
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert columns(path, "versions") == COLUMNS
        assert s.get_job(1) is not None and s.get_release(rid)["build_name"] == "1.0.1"
        vid = add_version(s)
        assert s.get_version(vid)["state"] == "creating"
    finally:
        s.close()
    bak = path.parent / "backup" / "rcm.sqlite3.v19.bak"
    assert bak.exists()
    assert "versions" not in tables(bak)


def test_a_v20_database_gains_upload_job_id_and_keeps_its_drafts(tmp_path):
    """v20 → v21(워크플랜 §14-2) — 열 하나를 더할 뿐, 있던 드래프트는 그대로다. `dev` 로 이미 v20
    까지 올라간 데이터베이스가 여기로 온다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    vid = add_version(s, state="editing")
    s.close()
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE versions DROP COLUMN upload_job_id")
    c.execute("PRAGMA user_version=20")
    c.commit()
    c.close()
    assert "upload_job_id" not in columns(path, "versions")
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert columns(path, "versions") == COLUMNS
        row = s.get_version(vid)
        assert row["state"] == "editing" and row["ios_version"] == "1.1.1"
        assert row["upload_job_id"] is None
        assert s.update_version(vid, upload_job_id=9) is True
        assert s.version_for_job(9)["id"] == vid
    finally:
        s.close()
    assert (path.parent / "backup" / "rcm.sqlite3.v20.bak").exists()


def test_an_older_build_refuses_a_newer_database_and_points_at_the_backup(tmp_path):
    """v19 빌드가 v20 DB 를 만나는 것과 같은 경로 — `newer_database_message`."""
    path = tmp_path / "data" / "rcm.sqlite3"
    Store(path).close()
    bdir = path.parent / "backup"
    bdir.mkdir()
    (bdir / f"rcm.sqlite3.v{DB_VERSION}.bak").write_bytes(b"backup")
    c = sqlite3.connect(path)
    c.execute(f"PRAGMA user_version={DB_VERSION + 1}")
    c.commit()
    c.close()
    with pytest.raises(StoreError) as e:
        Store(path)
    msg = str(e.value)
    assert f"schema version {DB_VERSION + 1} is newer than this build ({DB_VERSION})" in msg
    assert f"restore {bdir / f'rcm.sqlite3.v{DB_VERSION}.bak'}" in msg


def test_create_get_list_and_update(tmp_path):
    s = Store(tmp_path / "data" / "rcm.sqlite3")
    try:
        vid = add_version(s)
        row = s.get_version(vid)
        assert row == {
            "id": vid,
            "repo": "app",
            "ios_version": "1.1.1",
            "android_version": "1.0.1",
            "state": "creating",
            "created_by": "macmini-admin",
            "created_at": NOW,
            "last_edit_at": None,
            "expires_at": NOW + TTL,
            "asc_version_id": None,
            "prefill_json": None,
            "edited_json": None,
            "error": None,
            "create_job_id": None,
            "delete_job_id": None,
            "release_id": None,
            "review_job_id": None,
            "upload_job_id": None,
            "expiry_warned": False,
        }
        # Android 만 · 빈 문자열은 NULL 로
        only = s.create_version(
            repo="app",
            ios_version="",
            android_version="1.0.2",
            state="editing",
            created_by="admin",
            now=NOW,
            expires_at=NOW + TTL,
            create_job_id=7,
        )
        assert s.get_version(only)["ios_version"] is None
        assert s.get_version(only)["create_job_id"] == 7
        with pytest.raises(ValueError):
            add_version(s, None, "")
        with pytest.raises(ValueError):
            add_version(s, state="open")
        assert s.get_version(999) is None
        other = s.create_version(
            repo="other",
            ios_version="9.9.9",
            android_version=None,
            state="editing",
            created_by="admin",
            now=NOW,
            expires_at=NOW + TTL,
        )
        assert [r["id"] for r in s.list_versions("app")] == [only, vid]  # 새것부터
        assert [r["id"] for r in s.list_versions()] == [other, only, vid]
        assert s.count_versions() == 3
        # update — 허용 열만, 시각은 datetime, bool 은 정수로
        assert s.update_version(
            vid,
            state="editing",
            asc_version_id="abc123",
            prefill_json='{"schema": 1}',
            last_edit_at=NOW + timedelta(minutes=1),
            expiry_warned=True,
            create_job_id=3,
            review_job_id=4,
            release_id=5,
            delete_job_id=6,
            upload_job_id=8,
            error=None,
        )
        row = s.get_version(vid)
        assert row["state"] == "editing" and row["asc_version_id"] == "abc123"
        assert row["prefill_json"] == '{"schema": 1}'
        assert row["last_edit_at"] == NOW + timedelta(minutes=1)
        assert row["expiry_warned"] is True
        assert (row["create_job_id"], row["review_job_id"], row["release_id"]) == (3, 4, 5)
        assert (row["delete_job_id"], row["upload_job_id"]) == (6, 8)
        assert s.update_version(999, state="editing") is False
        assert s.update_version(vid) is True  # 바꿀 것이 없어도 행은 있다
        with pytest.raises(ValueError, match="unknown column"):
            s.update_version(vid, ios_version="2.0.0")
        with pytest.raises(ValueError, match="unknown column"):
            s.update_version(vid, created_by="x")
        with pytest.raises(ValueError, match="unknown version state"):
            s.update_version(vid, state="open")
        assert s.get_version(vid)["ios_version"] == "1.1.1"
        # closed 둘은 include_closed=False 에서 빠진다
        s.update_version(vid, state="submitted")
        s.update_version(only, state="discarded")
        assert s.list_versions("app", include_closed=False) == []
        assert [r["id"] for r in s.list_versions("app")] == [only, vid]
        s.update_version(only, state="failed")
        assert [r["id"] for r in s.list_versions("app", include_closed=False)] == [only]
    finally:
        s.close()


def test_rows_are_found_by_their_jobs_and_by_a_running_release(tmp_path):
    s = Store(tmp_path / "data" / "rcm.sqlite3")
    try:
        vid = add_version(s)
        s.update_version(vid, create_job_id=11)
        assert s.version_for_job(11)["id"] == vid
        assert s.version_for_job(12) is None
        s.update_version(vid, delete_job_id=12, review_job_id=13, upload_job_id=14)
        assert s.version_for_job(12)["id"] == vid and s.version_for_job(13)["id"] == vid
        assert s.version_for_job(14)["id"] == vid  # §14-2 — 올리기 잡도 행으로 돌아온다
        assert s.version_for_release(5) is None
        s.update_version(vid, release_id=5)
        assert s.version_for_release(5) is None  # 도는 중일 때만
        s.update_version(vid, state="running")
        assert s.version_for_release(5)["id"] == vid
    finally:
        s.close()


def test_expiry_queries(tmp_path):
    """미편집 `editing` · `failed` 는 만료 뒤 `open_versions_expired`(§14-3), 편집 있음은
    `versions_to_warn` 한 번."""
    s = Store(tmp_path / "data" / "rcm.sqlite3")
    try:
        untouched = add_version(s, "1.1.1", None, state="editing")
        edited = add_version(s, "1.1.2", None, state="editing")
        s.update_version(edited, edited_json="{}", last_edit_at=NOW)
        creating = add_version(s, "1.1.3", None, state="creating")
        submitted = add_version(s, "1.1.4", None, state="submitted")
        s.update_version(submitted, edited_json="{}", last_edit_at=NOW)
        # 만들기가 실패한 행 — 이름만 붙잡고 있다가 같이 치워진다
        failed = add_version(s, "1.1.5", None, state="failed")
        before = NOW + TTL - timedelta(seconds=1)
        after = NOW + TTL + timedelta(seconds=1)
        assert s.open_versions_expired(before) == []
        assert [r["id"] for r in s.open_versions_expired(after)] == [untouched, failed]
        assert s.versions_to_warn(before) == []
        assert [r["id"] for r in s.versions_to_warn(after)] == [edited]  # submitted 는 아니다
        s.update_version(edited, expiry_warned=True)
        assert s.versions_to_warn(after) == []
        assert s.get_version(creating)["state"] == "creating"
    finally:
        s.close()


def test_version_rows_survive_retention_and_a_full_gc(tmp_path):
    """청소기는 잡 · 이벤트 · 묶음 · blob 을 지운다. 버전 대장은 잡이 아니다."""
    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.metadata_retention_days = 1
    cfg.server.retention_days_success = 0
    cfg.server.retention_days_failure = 0
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        old = NOW - timedelta(days=40)
        job = enqueue(store, tree="bbbb", now=old)
        assert store.claim(1, old) is not None
        assert store.finish(job.id, SUCCEEDED, now=old, exit_code=0)
        for ios in ("1.0.0", "1.0.1", "1.0.2"):
            add_version(store, ios, None, state="editing", now=old)
        assert store.count_versions() == 3
        janitor = Janitor(store, cfg, now_fn=lambda: NOW, log=lambda _m: None)
        janitor.sweep_once(NOW)
        janitor.sweep_once(NOW)
        assert store.get_job(job.id) is None
        janitor.gc(NOW, dry_run=False)
        assert store.count_versions() == 3
        assert store.list_versions("app")[0]["ios_version"] == "1.0.2"
    finally:
        store.close()
