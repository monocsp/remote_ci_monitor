"""오프라인 `rcm gc --dry-run --config` — **살아 있는 DB 를 바꾸지 않는다**(결정 73).

2026-09-10 사고(docs/gate-replay-fixes-workplan.md §3 B2): 「설정과 데이터 디렉터리만 읽는다」던
오프라인 dry-run 이 `Store(...)` 를 열어 운영 DB 를 7→15 로 마이그레이션했고, 옛 빌드는 재시작하면
못 뜨게 됐다. 이제 원본은 `mode=ro` 로만 열고, `Connection.backup()` 으로 뜬 **임시 사본** 위에서
실제 마이그레이션과 실제 계획을 돌린 뒤 사본을 지운다. 어느 단계든 불완전하면 exit 3 — 빈 계획
성공은 없다.

픽스처는 **v7 DB 에 WAL 파일이 실제로 있는 상태**다: 서버가 도는 것처럼 연결 하나가 WAL 에만 쓴 행을
들고 있다. 사본이 WAL 을 못 봤다면 그 잡은 계획에 없다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.core.model import SUCCEEDED
from remote_ci_monitor.store import DB_VERSION, Store
from test_store import enqueue

OLD = 7  # 2026-09-10 운영 DB 의 스키마 — v0.2.5 가 아는 마지막 버전
NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)


def downgrade_to_v7(path: Path) -> None:
    """v16 으로 만든 DB 를 v7 모양으로 되돌린다(tests/test_store.py 의 방식 그대로)."""
    c = sqlite3.connect(path)
    try:
        c.execute("DROP INDEX IF EXISTS jobs_claim")  # v8
        c.execute("DROP INDEX IF EXISTS jobs_recent")  # v9
        c.execute("DROP INDEX IF EXISTS job_failures_name")  # v13
        c.execute("DROP INDEX IF EXISTS jobs_key_finished")
        c.execute("DROP TABLE IF EXISTS job_failures")
        c.execute("ALTER TABLE jobs DROP COLUMN fail_truncated")
        c.execute("ALTER TABLE jobs DROP COLUMN last_step")  # v12
        c.execute("ALTER TABLE jobs DROP COLUMN concurrent_at_start")  # v10
        c.execute("ALTER TABLE jobs DROP COLUMN failed_step_guessed")  # v11
        c.execute(f"PRAGMA user_version={OLD}")
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()


def write_config(tmp_path: Path, data: Path) -> Path:
    cfg_path = tmp_path / "server.toml"
    cfg_path.write_text(
        f'[server]\ndata_dir = "{data}"\nworkspace_retention_days = 0\n'
        '[[presets]]\nname = "gate"\nargv = ["true"]\n'
    )
    return cfg_path


@pytest.fixture
def old_db(tmp_path):
    """v7 DB + 워크스페이스 둘. 잡 1 은 체크포인트된 종료 잡, 잡 2 는 **WAL 에만** 종료가 적혀 있다.
    돌려주는 dict 의 `conn` 이 그 WAL 을 쥔 연결이다 — 테스트가 끝날 때 닫는다."""
    data = tmp_path / "data"
    db = data / "rcm.sqlite3"
    store = Store(db)
    j1 = enqueue(store, tree="1111", now=NOW - timedelta(days=3))
    store.claim(1, NOW - timedelta(days=3))
    assert store.finish(j1.id, SUCCEEDED, now=NOW - timedelta(days=2), exit_code=0)
    j2 = enqueue(store, tree="2222", now=NOW - timedelta(days=1))
    store.claim(1, NOW - timedelta(days=1))  # running 인 채 체크포인트된다
    store.close()
    downgrade_to_v7(db)
    for jid in (j1.id, j2.id):
        (data / "workspaces" / str(jid)).mkdir(parents=True)
        (data / "workspaces" / str(jid) / "big").write_bytes(b"x" * 8192)
    # 서버가 도는 것처럼: 연결 하나가 WAL 모드로 잡 2 의 종료를 쓰고 **열린 채** 있다
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "UPDATE jobs SET state=?, finished_at=?, exit_code=0 WHERE id=?",
        (SUCCEEDED, (NOW - timedelta(hours=12)).timestamp(), j2.id),
    )
    assert (db.parent / "rcm.sqlite3-wal").exists()
    before = {
        "bytes": db.read_bytes(),
        "mtime_ns": db.stat().st_mtime_ns,
        "wal": (db.parent / "rcm.sqlite3-wal").read_bytes(),
    }
    yield {
        "cfg": write_config(tmp_path, data),
        "db": db,
        "data": data,
        "conn": conn,
        "before": before,
        "checkpointed": j1.id,
        "wal_only": j2.id,
    }
    conn.close()


def scratch_copies() -> list[str]:
    """임시 디렉터리에 남은 dry-run 사본."""
    return sorted(n for n in os.listdir(tempfile.gettempdir()) if n.startswith("rcm-gc-dryrun-"))


def raw_version(path: Path) -> int:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(c.execute("PRAGMA user_version").fetchone()[0])
    finally:
        c.close()


# ── 원본은 그대로, 계획은 사본에서 ──────────────────────────────────────────


def test_the_plan_comes_from_a_migrated_copy_and_the_original_is_untouched(old_db, capsys):
    """사고의 재현: v7 DB 로 dry-run → 계획은 나오고(사본을 v16 으로 올려서), 원본은 바이트·mtime·
    `user_version`·WAL 까지 그대로다. mutcheck: 사본 대신 원본을 열면 여기가 빨개진다."""
    stale = scratch_copies()
    rc = main(["gc", "--dry-run", "--config", str(old_db["cfg"]), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0, doc
    planned = {i["job_id"] for i in doc["planned"]}
    assert old_db["checkpointed"] in planned, doc["planned"]
    # WAL 에만 있던 종료가 사본에 실렸다 — `cp` 였다면 잡 2 는 아직 running 이라 계획에 없다
    assert old_db["wal_only"] in planned, doc["planned"]
    assert doc["offline"] == {
        "database": str(old_db["db"]),
        "schema_version": OLD,
        "planned_with_schema": DB_VERSION,
        "copy": True,
    }
    db = old_db["db"]
    assert raw_version(db) == OLD
    assert db.read_bytes() == old_db["before"]["bytes"]
    assert db.stat().st_mtime_ns == old_db["before"]["mtime_ns"]
    assert (db.parent / "rcm.sqlite3-wal").read_bytes() == old_db["before"]["wal"]
    assert not (db.parent / "rcm.sqlite3-journal").exists()
    assert not (old_db["data"] / "backup").exists()  # 사본의 자동 백업은 임시 디렉터리 안이다
    assert scratch_copies() == stale  # 사본·-wal·-shm 이 남지 않는다


def test_the_text_output_says_it_planned_on_a_copy(old_db, capsys):
    rc = main(["gc", "--dry-run", "--config", str(old_db["cfg"])])
    out = capsys.readouterr().out
    assert rc == 0
    assert "would free" in out
    assert f"schema v{OLD}" in out and f"v{DB_VERSION}" in out, out
    assert "copy" in out and "not changed" in out, out


def test_a_database_already_at_this_version_is_also_planned_on_a_copy(tmp_path, capsys):
    """옛 DB 만이 아니다 — 같은 버전이어도 원본은 `mode=ro` 로만 연다(한 코드 경로)."""
    data = tmp_path / "data"
    store = Store(data / "rcm.sqlite3")
    store.close()
    cfg = write_config(tmp_path, data)
    (data / "workspaces" / "7").mkdir(parents=True)
    (data / "workspaces" / "7" / "big").write_bytes(b"x" * 4096)
    mtime = (data / "rcm.sqlite3").stat().st_mtime_ns
    rc = main(["gc", "--dry-run", "--config", str(cfg), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0 and doc["offline"]["schema_version"] == DB_VERSION
    assert (data / "workspaces" / "7").exists()
    assert (data / "rcm.sqlite3").stat().st_mtime_ns == mtime


# ── 불완전하면 exit 3 ─────────────────────────────────────────────────────────


def test_no_database_means_unknown_and_creates_nothing(tmp_path, capsys):
    """DB 가 없는데 빈 계획으로 성공하면 「지울 게 없다」는 거짓이 된다 — 그리고 예전 코드는 여기서
    운영 데이터 디렉터리에 새 DB 를 **만들었다**."""
    data = tmp_path / "data"
    data.mkdir()
    cfg = write_config(tmp_path, data)
    rc = main(["gc", "--dry-run", "--config", str(cfg)])
    err = capsys.readouterr().err
    assert rc == 3
    assert "no database" in err and str(data / "rcm.sqlite3") in err, err
    assert sorted(p.name for p in data.iterdir()) == []


def test_a_copy_that_outruns_the_deadline_is_unknown_not_a_plan(old_db, capsys):
    """페이지 단위 복사에 전체 마감이 있다 — 못 끝내면 3. 마감 0 이면 첫 페이지에서 걸린다."""
    stale = scratch_copies()
    rc = main(["gc", "--dry-run", "--config", str(old_db["cfg"]), "--timeout", "0"])
    err = capsys.readouterr().err
    assert rc == 3 and "copy" in err and "deadline" in err, err
    assert raw_version(old_db["db"]) == OLD
    assert scratch_copies() == stale


def test_a_migration_that_fails_on_the_copy_is_unknown_and_names_the_restart_risk(
    old_db, capsys, monkeypatch
):
    """사본의 마이그레이션이 실패하면 새 서버도 그 DB 로 못 뜬다 — 그걸 재시작 **전에** 본다."""
    from remote_ci_monitor import store as store_mod

    def boom(self):
        raise store_mod.StoreError("simulated: ALTER TABLE failed")

    monkeypatch.setattr(store_mod.Store, "migrate", boom)
    rc = main(["gc", "--dry-run", "--config", str(old_db["cfg"])])
    err = capsys.readouterr().err
    assert rc == 3 and "simulated: ALTER TABLE failed" in err and "restart" in err, err
    assert raw_version(old_db["db"]) == OLD


def test_a_database_newer_than_this_build_is_unknown_with_the_builds_own_message(tmp_path, capsys):
    data = tmp_path / "data"
    Store(data / "rcm.sqlite3").close()
    c = sqlite3.connect(data / "rcm.sqlite3")
    c.execute("PRAGMA user_version=99")
    c.commit()
    c.close()
    rc = main(["gc", "--dry-run", "--config", str(write_config(tmp_path, data))])
    err = capsys.readouterr().err
    assert rc == 3 and "newer than this build" in err, err
    assert raw_version(data / "rcm.sqlite3") == 99


def test_an_inventory_error_is_unknown_not_an_empty_plan(old_db, capsys, monkeypatch):
    """§4-1: 스캔·DB 실패가 `inventory_error` 로 삼켜진 뒤 빈 계획 + exit 0 이던 것(fail-open)."""
    from remote_ci_monitor import janitor as janitor_mod

    def broken(self, now):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(janitor_mod.Janitor, "inventory", broken)
    rc = main(["gc", "--dry-run", "--config", str(old_db["cfg"])])
    err = capsys.readouterr().err
    assert rc == 3 and "scan_EACCES" in err, err
    assert raw_version(old_db["db"]) == OLD
