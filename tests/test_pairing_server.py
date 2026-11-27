"""M5t-c 서버: DB v27 · `pair.key` · 짝짓기 창 · `/pair*` 라우트 · 거절 MAC(docs/m5tc-workplan.md
§4.2 · §4.4 · §4.5 · §4.7, PR1).

서버는 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 토큰 · 코드 · `pair_secret` 은
실행 중에 만든다(§8 — 커밋하는 64자 hex 리터럴은 없다).
"""

from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor import pairing as pairing_mod
from remote_ci_monitor.cli import USAGE_EXIT, main
from remote_ci_monitor.pairing import PairKeyError, load_or_create_pair_key
from remote_ci_monitor.store import DB_VERSION, Store, StoreError

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 27, 9, 0, 0, tzinfo=UTC)
NEW_TOKEN_COLUMNS = ["pool", "pair_id", "paired_host", "paired_addr"]


def columns(path: Path, table: str) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    finally:
        c.close()


def fresh_pair_id() -> str:
    return os.urandom(16).hex()


def paired(store: Store, name: str, *, kind: str = "worker", pool: str | None = "default") -> str:
    """짝지은 토큰 한 행(교환이 쓰는 것과 같은 Store 메서드)."""
    return store.add_paired_token(
        name,
        kind=kind,
        pool=pool if kind == "worker" else None,
        pair_id=fresh_pair_id(),
        paired_host=name,
        paired_addr="127.0.0.1",
        now=T0,
    )


# ── PI28 마이그레이션 · 옛 빌드 ─────────────────────────────────────────────


def downgrade_tokens_to_v26(db: Path, version: int = 26) -> None:
    """v27 의 열 넷과 색인을 지워 v26 사본을 만든다(옛 행은 그대로)."""
    c = sqlite3.connect(db)
    c.execute("DROP INDEX IF EXISTS tokens_pair_id")
    for col in NEW_TOKEN_COLUMNS:
        c.execute(f"ALTER TABLE tokens DROP COLUMN {col}")
    c.execute(f"PRAGMA user_version={version}")
    c.commit()
    c.close()


@pytest.mark.parametrize("old", [25, 26])
def test_pi28_an_old_copy_migrates_to_v27_and_keeps_tokens_and_workers(tmp_path, old):
    db = tmp_path / "data" / "rcm.sqlite3"
    s = Store(db)
    client = s.add_token("alice-laptop", now=T0)
    worker = s.add_token("build-02", now=T0, kind="worker")
    gone = s.add_token("old-desk", now=T0)
    s.revoke_token("old-desk", T0)
    driver = s.add_token("store-driver:app:1", now=T0)
    s.register_worker("build-02", pool="default", lanes=1, host_name="h", version="0", now=T0)
    workers_before = [tuple(r) for r in s._conn().execute("SELECT * FROM workers")]
    s.close()
    downgrade_tokens_to_v26(db, old)
    assert not set(NEW_TOKEN_COLUMNS) & set(columns(db, "tokens"))
    s = Store(db)
    try:
        assert s.user_version() == DB_VERSION == 27
        assert columns(db, "tokens")[-4:] == NEW_TOKEN_COLUMNS
        info = s.verify_token(client)
        assert (info.name, info.kind, info.pool, info.pair_id) == (
            "alice-laptop",
            "client",
            None,
            None,
        )
        assert s.verify_token(worker).kind == "worker"
        assert s.verify_token(gone) is None
        assert s.verify_token(driver).name == "store-driver:app:1"
        assert [t.revoked_at is not None for t in s.list_tokens()] == [False, False, True, False]
        assert all(t.pool is None and t.pair_id is None for t in s.list_tokens())
        assert [tuple(r) for r in s._conn().execute("SELECT * FROM workers")] == workers_before
        idx = {r[1] for r in s._conn().execute("PRAGMA index_list(tokens)")}
        assert "tokens_pair_id" in idx
    finally:
        s.close()


def test_pi28_an_older_build_refuses_a_newer_database_and_does_not_change_it(tmp_path):
    db = tmp_path / "data" / "rcm.sqlite3"
    Store(db).close()
    c = sqlite3.connect(db)
    c.execute(f"PRAGMA user_version={DB_VERSION + 1}")
    c.commit()
    c.close()
    before = db.read_bytes()
    with pytest.raises(StoreError, match="newer than this build"):
        Store(db)
    assert db.read_bytes() == before


def test_pi28_the_guard_still_opens_the_database_only_for_rcm_token():
    text = (ROOT / "tools" / "guard_production.py").read_text()
    assert 'DB_OPENING = frozenset({"token"})' in text


# ── 토큰 칸 · token_pair_id(F2 · F65) ──────────────────────────────────────


def test_pi18_verify_token_carries_the_paired_columns_and_the_db_holds_no_secret(tmp_path):
    db = tmp_path / "data" / "rcm.sqlite3"
    s = Store(db)
    try:
        pair_id = fresh_pair_id()
        secret = s.add_paired_token(
            "build-01",
            kind="worker",
            pool="gpu",
            pair_id=pair_id,
            paired_host="build-01",
            paired_addr="192.0.2.10",
            now=T0,
        )
        info = s.verify_token(secret)
        assert (info.name, info.kind, info.pool, info.pair_id) == (
            "build-01",
            "worker",
            "gpu",
            pair_id,
        )
        assert (info.paired_host, info.paired_addr) == ("build-01", "192.0.2.10")
        listed = {t.name: t for t in s.list_tokens()}
        assert listed["build-01"].pool == "gpu" and listed["build-01"].pair_id == pair_id
    finally:
        s.close()
    # 사본에는 pair_id 와 토큰 sha256 만 — 토큰 값 · K · pair.key 는 없다
    copy = tmp_path / "copy.sqlite3"
    src, dst = sqlite3.connect(db), sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    dst.close()
    raw = copy.read_bytes()
    assert secret.encode() not in raw
    assert hashlib.sha256(secret.encode()).hexdigest().encode() in raw
    assert pair_id.encode() in raw


def test_pi46_token_pair_id_finds_revoked_rows_but_verify_token_still_refuses_them(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    try:
        secret = paired(s, "build-01")
        pair_id = s.verify_token(secret).pair_id
        assert s.token_pair_id(secret) == pair_id
        assert s.revoke_token("build-01", T0)
        assert s.verify_token(secret) is None
        assert s.token_pair_id(secret) == pair_id  # 폐기된 행도 찾는다(F65)
        manual = s.add_token("build-02", now=T0, kind="worker")
        assert s.token_pair_id(manual) is None  # pair_id 가 없는 옛 토큰
        assert s.token_pair_id("not-a-token") is None
        assert s.token_pair_id(None) is None
    finally:
        s.close()


def test_pi19_b_replace_rewrites_the_row_in_one_update(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    try:
        old = paired(s, "build-01")
        old_pair = s.verify_token(old).pair_id
        statements: list[str] = []
        s._conn().set_trace_callback(statements.append)
        new_pair = fresh_pair_id()
        new = s.add_paired_token(
            "build-01",
            kind="worker",
            pool="gpu",
            pair_id=new_pair,
            paired_host="build-01b",
            paired_addr="192.0.2.11",
            now=T0,
            replace=True,
        )
        s._conn().set_trace_callback(None)
        writes = [
            q for q in statements if q.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        assert len(writes) == 1 and writes[0].lstrip().upper().startswith("UPDATE"), writes
        assert s.verify_token(old) is None
        info = s.verify_token(new)
        assert (info.pair_id, info.pool, info.paired_host) == (new_pair, "gpu", "build-01b")
        assert s.token_pair_id(old) is None  # 옛 sha256 의 행은 없다 → MAC 없음(340)
        assert old_pair != new_pair
        active = [t for t in s.list_tokens() if t.name == "build-01" and t.revoked_at is None]
        assert len(active) == 1
    finally:
        s.close()


def test_pi19_b_replace_of_a_revoked_row_reactivates_it(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    try:
        paired(s, "build-01")
        s.revoke_token("build-01", T0)
        new = s.add_paired_token(
            "build-01",
            kind="worker",
            pool="default",
            pair_id=fresh_pair_id(),
            paired_host="build-01",
            paired_addr="127.0.0.1",
            now=T0,
            replace=True,
        )
        assert s.verify_token(new).revoked_at is None
        with pytest.raises(StoreError):
            s.add_paired_token(
                "build-01",
                kind="worker",
                pool="default",
                pair_id=fresh_pair_id(),
                paired_host=None,
                paired_addr="127.0.0.1",
                now=T0,
            )
    finally:
        s.close()


# ── PI35 · PI17 · PI18 pair.key 수명 주기 ───────────────────────────────────


def store_in(data: Path) -> Store:
    data.mkdir(parents=True, exist_ok=True)
    return Store(data / "rcm.sqlite3")


def test_pi35_a_missing_key_with_active_paired_tokens_refuses_to_start(tmp_path):
    data = tmp_path / "data"
    s = store_in(data)
    try:
        paired(s, "build-01")
        paired(s, "laptop-1", kind="client")
        with pytest.raises(PairKeyError) as e:
            load_or_create_pair_key(data, s)
        text = str(e.value)
        assert "pair.key is missing but 2 paired tokens are still active" in text
        assert "restore pair.key from the backup taken with this data_dir" in text
        assert "rcm token revoke <name>" in text
        assert not (data / "pair.key").exists()
        assert isinstance(e.value, StoreError)
    finally:
        s.close()


def test_pi35_b_with_every_paired_token_revoked_a_new_key_is_made(tmp_path):
    data = tmp_path / "data"
    s = store_in(data)
    try:
        paired(s, "build-01")
        s.revoke_token("build-01", T0)
        key = load_or_create_pair_key(data, s)
        st = (data / "pair.key").stat()
        assert len(key) == 32 and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size == 32
        assert load_or_create_pair_key(data, s) == key  # 다시 읽어도 같다
        assert [p.name for p in data.iterdir() if p.name.startswith("pair.key")] == ["pair.key"]
    finally:
        s.close()


def test_pi17_the_key_is_0600_from_the_start_even_with_a_loose_umask(tmp_path):
    data = tmp_path / "data"
    s = store_in(data)
    old = os.umask(0)
    try:
        load_or_create_pair_key(data, s)
    finally:
        os.umask(old)
        s.close()
    assert stat.S_IMODE((data / "pair.key").stat().st_mode) == 0o600


def test_pi35_c_concurrent_creation_reads_one_whole_key_and_leaves_no_temp_file(tmp_path):
    data = tmp_path / "data"
    s = store_in(data)
    s.close()
    first_wrote = threading.Event()
    others_done = threading.Event()
    results: list[bytes | BaseException] = []

    def stage(name: str) -> None:
        # 첫 스레드는 임시 파일을 열고(쓰기 전) 멈춘다 — 그 사이 다른 쪽이 만들고 읽는다
        if name == "opened" and not first_wrote.is_set():
            first_wrote.set()
            others_done.wait(10)

    def run(hook) -> None:
        st = Store(data / "rcm.sqlite3")
        try:
            results.append(load_or_create_pair_key(data, st, stage=hook))
        except BaseException as e:  # noqa: BLE001 — 결과로 본다
            results.append(e)
        finally:
            st.close()

    a = threading.Thread(target=run, args=(stage,))
    a.start()
    assert first_wrote.wait(10)
    others = [threading.Thread(target=run, args=(None,)) for _ in range(4)]
    for t in others:
        t.start()
    for t in others:
        t.join(10)
    others_done.set()
    a.join(10)
    assert len(results) == 5 and all(isinstance(r, bytes) for r in results), results
    assert len({r for r in results}) == 1 and len(results[0]) == 32
    assert sorted(p.name for p in data.iterdir() if p.name.startswith("pair.key")) == ["pair.key"]


@pytest.mark.parametrize("shape", ["symlink", "0644", "31 bytes"])
def test_pi35_d_a_wrong_key_file_refuses_without_printing_the_value(tmp_path, shape):
    data = tmp_path / "data"
    s = store_in(data)
    value = os.urandom(32)
    key = data / "pair.key"
    if shape == "symlink":
        real = tmp_path / "elsewhere.key"
        real.write_bytes(value)
        real.chmod(0o600)
        key.symlink_to(real)
    elif shape == "0644":
        key.write_bytes(value)
        key.chmod(0o644)
    else:
        value = value[:31]
        key.write_bytes(value)
        key.chmod(0o600)
    try:
        with pytest.raises(PairKeyError) as e:
            load_or_create_pair_key(data, s)
    finally:
        s.close()
    text = str(e.value)
    assert str(key) in text
    assert value.hex() not in text and repr(value) not in text
    if shape == "0644":
        assert "chmod 600" in text


def test_pi35_e_a_filesystem_without_hard_links_refuses_and_never_renames(tmp_path, monkeypatch):
    data = tmp_path / "data"
    s = store_in(data)
    calls: list[str] = []

    def no_link(src, dst, **kw):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    def spy(name):
        def f(*a, **kw):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return f

    monkeypatch.setattr(pairing_mod.os, "link", no_link)
    monkeypatch.setattr(pairing_mod.os, "replace", spy("replace"))
    monkeypatch.setattr(pairing_mod.os, "rename", spy("rename"))
    try:
        with pytest.raises(PairKeyError, match="does not support hard links") as e:
            load_or_create_pair_key(data, s)
    finally:
        s.close()
    assert "EPERM" in str(e.value)
    assert calls == []
    assert sorted(p.name for p in data.iterdir() if p.name.startswith("pair.key")) == []


def free_port() -> int:
    import socket

    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return int(so.getsockname()[1])


def serve_config(tmp_path: Path, data: Path) -> Path:
    cfg = tmp_path / "server.toml"
    cfg.write_text(
        f'[server]\nbind = "127.0.0.1"\nport = {free_port()}\ndata_dir = "{data}"\n'
        "advertise = false\n"
        '[[presets]]\nname = "ok"\nargv = ["true"]\n'
    )
    return cfg


def test_pi35_a_rcm_serve_exits_2_and_creates_no_key(tmp_path, capsys):
    data = tmp_path / "data"
    s = store_in(data)
    paired(s, "build-01")
    s.close()
    rc = main(["serve", "--config", str(serve_config(tmp_path, data))])
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT
    assert "cannot start server:" in err and "pair.key is missing but 1 paired tokens" in err, err
    assert "Traceback" not in err
    assert not (data / "pair.key").exists()


def test_pi17_rcm_serve_refuses_a_0644_key(tmp_path, capsys):
    data = tmp_path / "data"
    store_in(data).close()
    key = data / "pair.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o644)
    rc = main(["serve", "--config", str(serve_config(tmp_path, data))])
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT and "cannot start server:" in err and "chmod 600" in err, err


def test_pi18_a_data_dir_with_only_the_db_copy_refuses_to_start(tmp_path):
    """DB 만 다른 곳에 복원 — 살아 있는 짝 토큰이 있어 새 키를 만들지 않는다(PI35 와 같은 규칙)."""
    src = tmp_path / "src"
    s = store_in(src)
    load_or_create_pair_key(src, s)  # 키가 먼저 있고, 그 뒤 짝지은 토큰이 생긴 서버
    paired(s, "build-01")
    s.close()
    copy = tmp_path / "copy"
    copy.mkdir()
    c = sqlite3.connect(src / "rcm.sqlite3")
    dst = sqlite3.connect(copy / "rcm.sqlite3")
    c.backup(dst)
    c.close()
    dst.close()
    s2 = Store(copy / "rcm.sqlite3")
    try:
        with pytest.raises(PairKeyError, match="paired tokens are still active"):
            load_or_create_pair_key(copy, s2)
        rows = s2._conn().execute("SELECT * FROM tokens").fetchall()
        assert {k for k in rows[0].keys()} >= {"pair_id", "sha256"}
    finally:
        s2.close()
