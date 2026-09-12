"""저장소(M5i) — 마이그레이션 전 백업(결정 74) · v16 복구(결정 78) · 옛 빌드의 거절 메시지.

2026-09-10 사고(docs/gate-replay-fixes-workplan.md §3 B2): 새 코드가 옛 서버가 도는 DB 를 올렸고,
옛 빌드는 「newer than this build」로 못 뜨게 됐는데 되돌릴 사본이 없었다. 이제 `migrate()` 가
실제로 버전을 올리기 **전에** `<data_dir>/backup/rcm.sqlite3.v<old>.bak` 을 만들고 검증한다 —
못 만들면 마이그레이션도 안 한다(fail-closed). 그리고 사고 뒤 옛 빌드가 실패 잡에 계속 쓴 추론
라벨은 v15 가 다시 돌지 않아 남는다 — v16 이 한 번 걷어낸다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor import store as store_mod
from remote_ci_monitor.core.model import CANCELLED, FAILED, LOST, SUCCEEDED, TIMED_OUT
from remote_ci_monitor.store import DB_VERSION, Store, StoreError
from test_store import enqueue

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
STEP = "test/core/feedback/just_audio_screen_music_port_test.dart"


def set_version(path: Path, version: int) -> None:
    c = sqlite3.connect(path)
    try:
        c.execute(f"PRAGMA user_version={version}")
        c.commit()
    finally:
        c.close()


def raw(path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


def old_database(tmp_path: Path, version: int = 15) -> Path:
    """행이 몇 개 있는 DB 를 `version` 으로 표시만 낮춘다 — 마이그레이션 문장은 `_already_added`
    가 건너뛰므로(스키마는 이미 최신) 백업·v16 의 동작만 본다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    store = Store(path)
    enqueue(store, tree="aaaa", now=NOW)
    store.close()
    set_version(path, version)
    return path


# ── 마이그레이션 전 백업 (결정 74) ───────────────────────────────────────────


def test_a_version_bump_leaves_a_verified_backup_of_the_old_database(tmp_path):
    path = old_database(tmp_path, version=15)
    Store(path).close()
    bak = path.parent / "backup" / "rcm.sqlite3.v15.bak"
    assert bak.is_file(), sorted(p.name for p in (path.parent / "backup").iterdir())
    # 백업은 **올리기 전** 모양이다: 버전 표식이 옛 것이고, 행은 다 있고, 무결하다
    assert raw(bak, "PRAGMA user_version") == [(15,)]
    assert raw(bak, "SELECT count(*) FROM jobs") == [(1,)]
    assert raw(bak, "PRAGMA integrity_check") == [("ok",)]
    assert not (path.parent / "backup" / "rcm.sqlite3.v15.bak.tmp").exists()


def test_no_backup_when_nothing_is_migrated(tmp_path):
    """새 DB(0 → 최신) 도, 이미 최신인 DB 도 백업을 만들지 않는다 — 기동마다 파일이 늘면 안 된다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    Store(path).close()
    Store(path).close()
    assert not (path.parent / "backup").exists()


def test_backup_happens_before_any_change_and_its_failure_stops_the_migration(
    tmp_path, monkeypatch
):
    """백업을 못 만들면 마이그레이션을 **안 한다** — 되돌릴 길 없는 일방통행을 시작하지 않는다."""
    path = old_database(tmp_path, version=15)

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store_mod, "_copy_database", refuse)
    with pytest.raises(StoreError, match="backup.*No space left|No space left.*backup"):
        Store(path)
    assert raw(path, "PRAGMA user_version") == [(15,)]
    assert not (path.parent / "backup").exists() or not any(
        p.suffix == ".bak" for p in (path.parent / "backup").iterdir()
    )


def test_a_backup_that_fails_verification_stops_the_migration(tmp_path, monkeypatch):
    path = old_database(tmp_path, version=15)

    def corrupt(src, dst):
        Path(dst).write_bytes(b"not a database")

    monkeypatch.setattr(store_mod, "_copy_database", corrupt)
    with pytest.raises(StoreError, match="backup"):
        Store(path)
    assert raw(path, "PRAGMA user_version") == [(15,)]
    assert not (path.parent / "backup" / "rcm.sqlite3.v15.bak").exists()


def test_only_the_three_newest_backups_are_kept_and_a_cleanup_failure_only_warns(
    tmp_path, monkeypatch
):
    path = old_database(tmp_path, version=15)
    bdir = path.parent / "backup"
    bdir.mkdir()
    for v in (11, 12, 13, 14):
        (bdir / f"rcm.sqlite3.v{v}.bak").write_bytes(b"old")
    (bdir / "rcm.sqlite3.pre-0.2.4.bak").write_bytes(b"hand-made")  # 사람이 만든 것은 안 건드린다
    warnings: list[str] = []
    Store(path, log=warnings.append).close()
    kept = sorted(p.name for p in bdir.iterdir())
    assert kept == [
        "rcm.sqlite3.pre-0.2.4.bak",
        "rcm.sqlite3.v13.bak",
        "rcm.sqlite3.v14.bak",
        "rcm.sqlite3.v15.bak",
    ], kept
    assert warnings == []

    # 정리 실패는 경고 한 줄 — 마이그레이션은 이미 끝났다
    path2 = old_database(tmp_path / "second", version=15)
    bdir2 = path2.parent / "backup"
    bdir2.mkdir()
    for v in (12, 13, 14):
        (bdir2 / f"rcm.sqlite3.v{v}.bak").write_bytes(b"old")

    real_unlink = Path.unlink

    def stubborn(self, *a, **kw):
        if self.name == "rcm.sqlite3.v12.bak":
            raise PermissionError(1, "Operation not permitted")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", stubborn)
    warned: list[str] = []
    s = Store(path2, log=warned.append)
    assert s.user_version() == DB_VERSION
    s.close()
    assert any("backup" in w and "v12" in w for w in warned), warned


# ── 옛 빌드의 거절 메시지 (B2-3) ─────────────────────────────────────────────


def newer_database(tmp_path: Path, *backups: int) -> Path:
    """이 빌드보다 새 DB 하나와, `backup/` 에 **실제로 있는** 자동 백업들(버전 목록)."""
    path = tmp_path / "data" / "rcm.sqlite3"
    Store(path).close()
    bdir = path.parent / "backup"
    for v in backups:
        bdir.mkdir(exist_ok=True)
        (bdir / f"rcm.sqlite3.v{v}.bak").write_bytes(b"backup")
    set_version(path, DB_VERSION + 5)
    return path


def refusal(path: Path) -> str:
    with pytest.raises(StoreError) as e:
        Store(path)
    msg = str(e.value)
    assert f"schema version {DB_VERSION + 5} is newer than this build ({DB_VERSION})" in msg
    assert "stop the service" in msg and "upgrade" in msg
    return msg


def test_the_newer_database_message_points_at_the_backup_that_actually_exists(tmp_path):
    """리뷰 #87 B P1(셋째) · M5l L1: v15 DB 를 v21 빌드로 바로 올리면 백업은 `v15.bak` 하나다 —
    v16 빌드가 「`v16.bak` 을 복원하라」고 하면 없는 파일을 가리킨다. 이 빌드의 `DB_VERSION` 이
    아니라 `backup/` 에 있는 가장 높은 `.bak` 을 가리켜야 한다."""
    path = newer_database(tmp_path, DB_VERSION - 1)
    msg = refusal(path)
    bak = path.parent / "backup" / f"rcm.sqlite3.v{DB_VERSION - 1}.bak"
    assert bak.exists() and f"restore {bak}" in msg, msg
    assert f"v{DB_VERSION}.bak" not in msg, msg
    assert "-wal" in msg and "-shm" in msg


def test_the_newer_database_message_picks_the_highest_backup_and_ignores_hand_made_ones(
    tmp_path,
):
    path = newer_database(tmp_path, DB_VERSION - 2, DB_VERSION - 1)
    bdir = path.parent / "backup"
    (bdir / "rcm.sqlite3.pre-0.2.4.bak").write_bytes(b"hand-made")
    (bdir / f"rcm.sqlite3.v{DB_VERSION + 9}.bak.tmp").write_bytes(b"half")  # 검증 전 임시 파일
    msg = refusal(path)
    assert f"restore {bdir / f'rcm.sqlite3.v{DB_VERSION - 1}.bak'}" in msg, msg
    assert f"v{DB_VERSION - 2}.bak" not in msg and "pre-0.2.4" not in msg, msg
    assert f"v{DB_VERSION + 9}" not in msg, msg


def test_the_newer_database_message_says_so_when_there_is_no_backup(tmp_path):
    """L1.7: 백업이 하나도 없으면 `.bak` 이름을 지어내지 않는다 — 「백업 없음」과 남은 길."""
    path = newer_database(tmp_path)
    msg = refusal(path)
    assert "no migration backup found" in msg, msg
    assert ".bak" not in msg, msg
    assert "-wal" in msg and "-shm" in msg
    # `backup/` 이 있지만 비어 있어도 같다
    (path.parent / "backup").mkdir()
    assert "no migration backup found" in refusal(path)


def test_a_backup_directory_that_cannot_be_listed_only_warns(tmp_path, monkeypatch):
    """리뷰 #87 B P2 · L1.8: `backup/` 에 쓰기·실행 권한만 있으면 새 백업은 만들어지지만
    `iterdir()` 가 실패한다 — 결정 74 가 약속한 경고 없이 조용히 넘어갔고 백업이 계속 쌓였다."""
    path = old_database(tmp_path, version=15)
    bdir = path.parent / "backup"
    real_iterdir = Path.iterdir

    def unreadable(self):
        if self == bdir:
            raise PermissionError(13, "Permission denied", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    warned: list[str] = []
    s = Store(path, log=warned.append)
    assert s.user_version() == DB_VERSION
    s.close()
    assert (bdir / "rcm.sqlite3.v15.bak").exists()
    assert len(warned) == 1 and warned[0].startswith("warning:"), warned
    assert "backup" in warned[0] and "Permission denied" in warned[0], warned
    assert "Traceback" not in warned[0]


# ── v16 복구 (결정 78) ──────────────────────────────────────────────────────


def finished(store: Store, *, state: str, seconds: int, **fields) -> int:
    j = enqueue(store, tree=f"t{seconds}", now=NOW - timedelta(seconds=seconds + 1))
    ok = store.finish(
        j.id,
        state,
        now=NOW - timedelta(seconds=seconds),
        exit_code=0 if state == SUCCEEDED else 1,
        **fields,
    )
    assert ok is True
    return j.id


def label(path: Path, job_id: int) -> tuple:
    (row,) = raw(path, "SELECT failed_step, last_step FROM jobs WHERE id=?", (job_id,))
    return row


def test_v16_moves_labels_that_have_no_ledger_row_and_keeps_declared_ones(tmp_path):
    """옛 빌드(0.2.5)가 사고 뒤에 쓴 실패 잡의 라벨은 대장(`job_failures`) 행이 없다 — 그것만
    `last_step` 으로 옮긴다. 새 코드의 선언 라벨은 대장 행이 있으니 그대로. 취소·유실은 비운다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    store = Store(path)
    declared = finished(
        store, state=FAILED, seconds=10, failed_step="test", last_step="test", fail_names=["test"]
    )
    inferred = finished(store, state=FAILED, seconds=20, last_step="build web")
    inferred_timed_out = finished(store, state=TIMED_OUT, seconds=30)
    cancelled = finished(store, state=CANCELLED, seconds=40)
    lost = finished(store, state=LOST, seconds=50)
    # 상한 경계: 대장에 100개가 실려 **잘렸고**, 라벨은 대장의 어느 이름과도 다르다 — 그래도
    # 선언이다
    many = finished(
        store,
        state=FAILED,
        seconds=60,
        failed_step="the-101st",
        last_step="the-101st",
        fail_names=[f"n{i}" for i in range(100)],
        fail_truncated=True,
    )
    store.close()
    c = sqlite3.connect(path)
    try:  # 옛 빌드처럼: 대장 없이 failed_step 만, last_step 은 옛 빌드가 모르는 열이라 그대로
        c.execute("UPDATE jobs SET failed_step=? WHERE id=?", (STEP, inferred))
        c.execute(
            "UPDATE jobs SET failed_step=?, last_step=NULL WHERE id=?", (STEP, inferred_timed_out)
        )
        c.execute("UPDATE jobs SET failed_step=? WHERE id IN (?,?)", (STEP, cancelled, lost))
        c.execute("PRAGMA user_version=15")
        c.commit()
    finally:
        c.close()

    s = Store(path)
    assert s.user_version() == DB_VERSION >= 16
    s.close()
    assert label(path, declared) == ("test", "test")
    assert label(path, many) == ("the-101st", "the-101st")
    assert label(path, inferred) == (None, "build web")  # 있던 last_step 은 지키고 라벨만 뗀다
    assert label(path, inferred_timed_out) == (None, STEP)
    assert label(path, cancelled) == (None, None)
    assert label(path, lost) == (None, None)


def test_v16_runs_once_and_does_not_touch_labels_written_afterwards(tmp_path):
    """매 기동 보정이 아니다 — v16 뒤 새 코드가 대장 없이 남긴 라벨(있을 수 없지만)은 손대지
    않는다."""
    path = tmp_path / "data" / "rcm.sqlite3"
    store = Store(path)
    assert store.user_version() == DB_VERSION
    jid = finished(store, state=FAILED, seconds=10, failed_step="x", last_step="x")
    store.close()
    Store(path).close()
    assert label(path, jid) == ("x", "x")
