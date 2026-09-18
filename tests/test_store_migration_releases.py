"""저장소 v18 — `releases` 표(릴리스 드라이버 실행 대장). 새 DB · v17 → v18 마이그레이션(백업
포함) · 옛 빌드의 거절 · 청소기(`rcm gc` · 보존 정리)가 이 표를 건드리지 않는다.

명세: 스토어 탭 API 계약 v2 「Driver」 · PLAN.md 「저장소」 · 결정 74(마이그레이션 전 백업).
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

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
COLUMNS = [
    "id",
    "repo",
    "build_name",
    "kind",
    "started_by",
    "started_at",
    "pid",
    "log_path",
    "exit_code",
    "finished_at",
    "confirmed_n",
    "confirmed_by",
    "android_track",
    "dry_run",
    "token_name",
    "auto_n",
]


def columns(path: Path, table: str) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    finally:
        c.close()


def set_version(path: Path, version: int) -> None:
    c = sqlite3.connect(path)
    try:
        c.execute(f"PRAGMA user_version={version}")
        c.commit()
    finally:
        c.close()


def add_release(store: Store, build: str = "1.0.1", *, started: datetime = NOW) -> int:
    rid = store.create_release(
        repo="app",
        build_name=build,
        kind="start",
        started_by="admin",
        now=started,
        log_path="/tmp/x.log",
    )
    store.set_release_started(rid, pid=1, token_name=f"store-driver:app:{rid}")
    store.finish_release(rid, 2, started)
    return rid


def test_a_fresh_database_is_version_18_with_the_releases_table():
    assert DB_VERSION == 20  # v20: versions 표(tests/test_store_versions.py)


def test_a_fresh_database_has_the_releases_table_and_index(tmp_path):
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert columns(path, "releases") == COLUMNS
        names = {
            r[0]
            for r in sqlite3.connect(path).execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='releases'"
            )
        }
        assert "releases_repo" in names
    finally:
        s.close()


def test_a_v17_database_gets_the_table_a_backup_and_version_18(tmp_path):
    """v17 DB(표가 없다)를 이 빌드로 열면 `backup/rcm.sqlite3.v17.bak` 을 먼저 남기고 표를 만든다.
    잡 행은 그대로다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    enqueue(s, tree="aaaa", now=NOW)
    s.close()
    c = sqlite3.connect(path)
    c.execute("DROP INDEX releases_repo")
    c.execute("DROP TABLE releases")
    c.execute("PRAGMA user_version=17")
    c.commit()
    c.close()
    assert "releases" not in {
        r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")
    }
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert columns(path, "releases") == COLUMNS
        assert s.get_job(1) is not None
        rid = add_release(s)
        assert s.get_release(rid)["exit_code"] == 2
    finally:
        s.close()
    bak = path.parent / "backup" / "rcm.sqlite3.v17.bak"
    assert bak.exists()
    assert "releases" not in {
        r[0]
        for r in sqlite3.connect(f"file:{bak}?mode=ro", uri=True).execute(
            "SELECT name FROM sqlite_master"
        )
    }


def test_an_older_build_refuses_a_v18_database_and_points_at_the_backup(tmp_path):
    """이 빌드가 `DB_VERSION` 보다 새 DB 를 만나면 뜨지 않는다 — v17 빌드가 v18 DB 를 만나는 것과
    같은 경로(`newer_database_message`)다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    Store(path).close()
    bdir = path.parent / "backup"
    bdir.mkdir()
    (bdir / f"rcm.sqlite3.v{DB_VERSION}.bak").write_bytes(b"backup")
    set_version(path, DB_VERSION + 1)
    with pytest.raises(StoreError) as e:
        Store(path)
    msg = str(e.value)
    assert f"schema version {DB_VERSION + 1} is newer than this build ({DB_VERSION})" in msg
    assert f"restore {bdir / f'rcm.sqlite3.v{DB_VERSION}.bak'}" in msg


def test_release_rows_survive_retention_and_a_full_gc(tmp_path):
    """청소기는 잡 · 이벤트 · 묶음 · blob 을 지운다. 릴리스 대장은 잡이 아니다 — 사람이 지운다."""
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
        for build in ("1.0.0", "1.0.1", "1.0.2"):
            add_release(store, build, started=old)
        assert store.count_releases() == 3
        janitor = Janitor(store, cfg, now_fn=lambda: NOW, log=lambda _m: None)
        janitor.sweep_once(NOW)
        janitor.sweep_once(NOW)  # 두 번째 sweep 이 산출물이 지워진 잡의 행을 지운다
        assert store.get_job(job.id) is None
        janitor.gc(NOW, dry_run=False)
        assert store.count_releases() == 3
        assert store.latest_release("app")["build_name"] == "1.0.2"
        assert [r["build_name"] for r in store.list_open_releases()] == []
    finally:
        store.close()


def test_a_v18_database_gets_the_auto_n_column_and_version_19(tmp_path):
    """v18 DB(`auto_n` 열이 없다)를 이 빌드로 열면 열을 더하고 19 가 된다. 옛 행은 auto_n False."""
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    rid = add_release(s)
    s.close()
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE releases DROP COLUMN auto_n")
    c.execute("PRAGMA user_version=18")
    c.commit()
    c.close()
    assert "auto_n" not in columns(path, "releases")
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert columns(path, "releases") == COLUMNS
        assert s.get_release(rid)["auto_n"] is False
        auto = s.create_release(
            repo="app",
            build_name="1.0.2",
            kind="start",
            started_by="admin",
            now=NOW,
            log_path="/tmp/y.log",
            auto_n=True,
        )
        assert s.get_release(auto)["auto_n"] is True
    finally:
        s.close()
    assert (path.parent / "backup" / "rcm.sqlite3.v18.bak").exists()
