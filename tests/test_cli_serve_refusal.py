"""`rcm serve`·`rcm token` 이 DB 를 못 열 때 — 한 줄로 거절하고 트레이스백을 찍지 않는다.

M5i 검증 V2.8(docs/acceptance/m5i-verification-scenarios.md): `backup/` 이 읽기 전용이면 서버는
「migration backup failed … not changed」로 안 떠야 한다. 안 뜨긴 했는데, 그 문장이 파이썬
트레이스백 끝에 묻혀 나왔다 — `cmd_serve` 가 `ConfigError`·`OSError` 만 잡고 `StoreError` 는
안 잡았다. `cmd_token` 도 `Store(...)` 가 `try` 밖이라 같다. 운영자가 launchd 로그에서 읽는
문장이 결정 74 의 복구 경로다(docs/operating.md 「Going back to the old build」) — 한 줄이어야
한다.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import stat
from pathlib import Path

import pytest

from remote_ci_monitor import store as store_mod
from remote_ci_monitor.cli import USAGE_EXIT, main
from remote_ci_monitor.store import DB_VERSION, Store


def free_port() -> int:
    """지금 비어 있는 포트 하나 — `serve` 는 이제 DB 보다 포트를 먼저 잡으므로
    고정 번호는 못 쓴다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def config_for(tmp_path: Path, data: Path, port: int | None = None) -> Path:
    cfg = tmp_path / "server.toml"
    port = free_port() if port is None else port
    cfg.write_text(
        f'[server]\nbind = "127.0.0.1"\nport = {port}\ndata_dir = "{data}"\n'
        '[[presets]]\nname = "ok"\nargv = ["true"]\n'
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


# ── M5l L5 (리뷰 pr-94 B-1 ~ B-3) ──────────────────────────────────────────────


def run_cli(cfg: Path, argv: list[str]) -> int:
    if argv[0] == "serve":
        return main(["serve", "--config", str(cfg)])
    return main(["token", "--config", str(cfg), *argv[1:]])


def one_refusal_line(err: str) -> str:
    """stderr 가 `rcm: …` 한 줄이고 트레이스백이 아니어야 한다 — 그 줄을 돌려준다."""
    assert "Traceback" not in err, err
    lines = err.strip().splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith("rcm: "), err
    return lines[0]


def test_a_taken_port_is_refused_before_the_database_is_opened(tmp_path, capsys):
    """리뷰 pr-94 B-1: 도는 서비스 곁에서 다른 빌드로 `rcm serve` 를 치면 DB 가 **먼저** 올라가고
    그 다음에야 「Address already in use」였다 — 옛 서비스는 다음 재시작에서 못 뜬다. 포트를 못
    잡으면 DB 를 열지도 않아야 한다: `user_version` 그대로 · `backup/` 없음 · `-wal` 없음."""
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = int(taken.getsockname()[1])
        cfg, db = marked(tmp_path, DB_VERSION - 1)
        cfg = config_for(tmp_path, db.parent, port)
        for leftover in (db.with_name("rcm.sqlite3-wal"), db.with_name("rcm.sqlite3-shm")):
            leftover.unlink(missing_ok=True)
        rc = main(["serve", "--config", str(cfg)])
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    line = one_refusal_line(err)
    assert "cannot start server" in line and "Address already in use" in line, err
    # 열린 적이 없다는 증거는 읽기 전에 본다 — 읽기 전용 연결도 WAL DB 를 열면 `-wal` 을 만든다
    assert not db.with_name("rcm.sqlite3-wal").exists()
    assert not (db.parent / "backup").exists()
    assert version_of(db) == DB_VERSION - 1


@pytest.mark.parametrize(
    "argv", [["serve"], ["token", "list"], ["token", "add", "x"], ["token", "revoke", "x"]]
)
def test_a_garbage_database_file_is_one_line(tmp_path, capsys, argv):
    """리뷰 pr-94 B-2 ①: 첫 바이트가 SQLite 헤더가 아닌 파일 → `sqlite3.DatabaseError`
    트레이스백이었다."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "rcm.sqlite3").write_bytes(b"garbage\n")
    cfg = config_for(tmp_path, data)
    rc = run_cli(cfg, argv)
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    assert "file is not a database" in one_refusal_line(err), err


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.parametrize("argv", [["serve"], ["token", "list"]])
def test_a_read_only_data_dir_is_one_line(tmp_path, capsys, argv):
    """리뷰 pr-94 B-2 ②: 현재 버전 DB 가 있는 읽기 전용 `data_dir` → WAL 전환에서
    `attempt to write a readonly database` 트레이스백이었다."""
    cfg, db = marked(tmp_path, DB_VERSION)
    data = db.parent
    for leftover in (db.with_name("rcm.sqlite3-wal"), db.with_name("rcm.sqlite3-shm")):
        leftover.unlink(missing_ok=True)
    data.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        rc = run_cli(cfg, argv)
    finally:
        data.chmod(stat.S_IRWXU)
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    line = one_refusal_line(err)
    assert "readonly database" in line or "ermission denied" in line, err


@pytest.mark.parametrize(
    "argv", [["serve"], ["token", "list"], ["token", "add", "x"], ["token", "revoke", "x"]]
)
def test_a_data_dir_that_is_a_file_is_one_line_for_token_too(tmp_path, capsys, argv):
    """리뷰 pr-94 B-3: `data_dir` 이 파일이면 `serve` 는 한 줄인데 `token` 은 `FileExistsError`
    트레이스백이었다 — 같은 PR 이 「같은 모양으로」 고쳤다고 했으니 `token` 도 `OSError` 를
    잡는다."""
    data = tmp_path / "data"
    data.write_text("")
    cfg = config_for(tmp_path, data)
    rc = run_cli(cfg, argv)
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    line = one_refusal_line(err)
    assert "File exists" in line or "Not a directory" in line, err
