"""`rcm serve`·`rcm token` 이 DB 를 못 열 때 — 한 줄로 거절하고 트레이스백을 찍지 않는다.

M5i 검증 V2.8(docs/acceptance/m5i-verification-scenarios.md): `backup/` 이 읽기 전용이면 서버는
「migration backup failed … not changed」로 안 떠야 한다. 안 뜨긴 했는데, 그 문장이 파이썬
트레이스백 끝에 묻혀 나왔다 — `cmd_serve` 가 `ConfigError`·`OSError` 만 잡고 `StoreError` 는
안 잡았다. `cmd_token` 도 `Store(...)` 가 `try` 밖이라 같다. 운영자가 launchd 로그에서 읽는
문장이 결정 74 의 복구 경로다(docs/operating.md 「Going back to the old build」) — 한 줄이어야
한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from remote_ci_monitor import store as store_mod
from remote_ci_monitor.cli import USAGE_EXIT, main
from remote_ci_monitor.store import DB_VERSION, Store


def config_for(tmp_path: Path, data: Path) -> Path:
    cfg = tmp_path / "server.toml"
    cfg.write_text(
        f'[server]\nport = 8799\ndata_dir = "{data}"\n[[presets]]\nname = "ok"\nargv = ["true"]\n'
    )
    return cfg


def marked(tmp_path: Path, version: int) -> tuple[Path, Path]:
    data = tmp_path / "data"
    db = data / "rcm.sqlite3"
    Store(db).close()
    c = sqlite3.connect(db)
    c.execute(f"PRAGMA user_version={version}")
    c.commit()
    c.close()
    return config_for(tmp_path, data), db


def version_of(db: Path) -> int:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return int(c.execute("PRAGMA user_version").fetchone()[0])
    finally:
        c.close()


@pytest.mark.parametrize("argv", [["serve"], ["token", "list"]])
def test_a_failed_migration_backup_is_one_line_and_the_database_is_untouched(
    tmp_path, monkeypatch, capsys, argv
):
    cfg, db = marked(tmp_path, DB_VERSION - 1)

    def refuse(src, dst, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store_mod, "_copy_database", refuse)
    if argv[0] == "serve":
        rc = main(["serve", "--config", str(cfg)])
    else:
        rc = main(["token", "--config", str(cfg), "list"])
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    assert "migration backup failed" in err and "not changed" in err, err
    assert "Traceback" not in err, err
    assert len(err.strip().splitlines()) == 1, err
    assert version_of(db) == DB_VERSION - 1


@pytest.mark.parametrize("argv", [["serve"], ["token", "list"]])
def test_a_newer_database_is_refused_in_one_line_with_the_restore_hint(tmp_path, capsys, argv):
    cfg, db = marked(tmp_path, DB_VERSION + 1)
    if argv[0] == "serve":
        rc = main(["serve", "--config", str(cfg)])
    else:
        rc = main(["token", "--config", str(cfg), "list"])
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    assert f"newer than this build ({DB_VERSION})" in err, err
    assert str(db.parent / "backup" / f"rcm.sqlite3.v{DB_VERSION}.bak") in err, err
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1, err
    assert version_of(db) == DB_VERSION + 1
