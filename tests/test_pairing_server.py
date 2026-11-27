"""M5t-c 서버: DB v27 · `pair.key` · 짝짓기 창 · `/pair*` 라우트 · 거절 MAC(docs/m5tc-workplan.md
§4.2 · §4.4 · §4.5 · §4.7, PR1).

서버는 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 토큰 · 코드 · `pair_secret` 은
실행 중에 만든다(§8 — 커밋하는 64자 hex 리터럴은 없다).
"""

from __future__ import annotations

import base64
import errno
import gzip
import hashlib
import hmac as _hmac
import http.client
import json
import os
import re
import secrets
import socket
import sqlite3
import stat
import threading
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor import pairing as pairing_mod
from remote_ci_monitor import server as server_mod
from remote_ci_monitor.cli import USAGE_EXIT, main
from remote_ci_monitor.core import pairing as cp
from remote_ci_monitor.pairing import PairKeyError, load_or_create_pair_key
from remote_ci_monitor.server import MAX_DRAIN_BODY, ApiError
from remote_ci_monitor.store import DB_VERSION, Store, StoreError
from test_pairing_wire import ref_k, ref_mac
from test_worker_api import WorkerServer

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


def disk_full_os() -> types.SimpleNamespace:
    """`pairing` 모듈만 보는 `os` — `write` 가 ENOSPC 를 낸다(다른 모듈 · DB 는 진짜 `os`)."""

    def write(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    names = {k: getattr(os, k) for k in dir(os) if not k.startswith("__")}
    names["write"] = write
    return types.SimpleNamespace(**names)


def key_files(data: Path) -> list[str]:
    return sorted(p.name for p in data.iterdir() if p.name.startswith("pair.key"))


def test_pi35_f_g5_a_full_disk_while_writing_the_key_leaves_no_file(tmp_path, monkeypatch):
    """G5: 키 임시 파일에 쓰다 디스크가 가득 차면 임시 파일을 지우고 그 오류를 올린다 —
    `pair.key` 도 임시 파일도 남지 않는다."""
    data = tmp_path / "data"
    s = store_in(data)
    monkeypatch.setattr(pairing_mod, "os", disk_full_os())
    try:
        with pytest.raises(OSError) as e:
            load_or_create_pair_key(data, s)
    finally:
        s.close()
    assert e.value.errno == errno.ENOSPC
    assert key_files(data) == []


def test_pi35_f_g5_rcm_serve_on_a_full_disk_exits_2_and_leaves_no_key(
    tmp_path, capsys, monkeypatch
):
    data = tmp_path / "data"
    store_in(data).close()
    monkeypatch.setattr(pairing_mod, "os", disk_full_os())
    rc = main(["serve", "--config", str(serve_config(tmp_path, data))])
    err = capsys.readouterr().err
    assert rc == USAGE_EXIT, err
    assert "cannot start server: No space left on device" in err and "Traceback" not in err
    assert key_files(data) == []


# ══ HTTP: 짝짓기 창 · /pair* · hello · exchange · 거절 MAC ══════════════════════════════

PUBLIC = "http://pair.example:8790"


class PairServer(WorkerServer):
    """`WorkerServer` + 짝짓기 도우미. 기본 `public_url` 은 창을 열 수 있게 하는 자리 표시다 —
    hello 는 소켓 주소(`127.0.0.1:<port>`)로 한다."""

    def __init__(self, tmp_path: Path, **kw):
        kw.setdefault("public_url", PUBLIC)
        super().__init__(tmp_path, **kw)
        self.logs: list[str] = []
        self.app.log = self.logs.append  # type: ignore[method-assign]
        # 서버가 쓸 키를 시험이 먼저 만들어 읽는다 — App 은 같은 파일을 읽는다(직접 만든 App 은
        # 처음 쓸 때 읽는다). 미리 읽혀 두어 hello 가 Store 를 부르지 않게 한다(PI5)
        self.key = load_or_create_pair_key(self.cfg.data_dir, self.store)
        getattr(self.app, "pair_key", None)

    @property
    def dialed(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def open(self, kind: str = "worker", **body):
        return self.req("POST", "/pair", token="admin", json_body={"kind": kind, **body})

    def opened(self, kind: str = "worker", **body) -> dict:
        status, doc = self.open(kind, **body)
        assert status == 201, (status, doc)
        return doc

    def exchange_body(
        self,
        code: str,
        kind: str = "worker",
        host_name: str = "build-01",
        *,
        body_kind: str | None = None,
        worker_nonce: str | None = None,
    ) -> dict:
        code_key = cp.pair_code_key(cp.normalize_pair_code(code))
        wn = worker_nonce or secrets.token_hex(32)
        return {
            "v": 1,
            "kind": kind if body_kind is None else body_kind,
            "host_name": host_name,
            "worker_nonce": wn,
            "proof": cp.exchange_proof(code_key, kind, host_name, wn),
        }

    def exchange(self, body, raw: bool = False):
        return self.req("POST", "/pair/exchange", json_body=body, raw=raw)

    def paired_by_exchange(self, kind: str = "worker", host_name: str = "build-01", **open_body):
        doc = self.opened(kind, **open_body)
        body = self.exchange_body(doc["code"], kind, host_name)
        status, out = self.exchange(body)
        assert status == 200, (status, out)
        code_key = cp.pair_code_key(cp.normalize_pair_code(doc["code"]))
        assert cp.verify_exchange_response(code_key, kind, host_name, body["worker_nonce"], out)
        return out

    def hello(self, pair_id: str, *, dialed: str | None = None, nonce: str | None = None):
        body = {
            "v": 1,
            "pair_id": pair_id,
            "nonce": nonce or secrets.token_hex(32),
            "dialed": self.dialed if dialed is None else dialed,
        }
        return body, self.req("POST", "/pair/hello", json_body=body)


@pytest.fixture
def ps(tmp_path):
    s = PairServer(tmp_path)
    yield s
    s.close()


def no_window_404(raw) -> tuple:
    status, headers, payload = raw
    return status, tuple(sorted(k.lower() for k in headers if k.lower() != "date")), payload


FIXED_404 = b'{"error":"no open pairing window for that code","code":"no_pairing_window"}'
FIXED_400 = b'{"error":"bad pairing request","code":"bad_pair_request"}'


# ── PI47 관리 라우트 ────────────────────────────────────────────────────────


OPEN_201_KEYS = {
    "id",
    "kind",
    "pool",
    "name",
    "replace",
    "code",
    "urls",
    "opened_at",
    "expires_at",
    "presets_count",
    "warnings",
}


def test_pi47_a_bad_admin_bodies_are_400_and_open_nothing(ps):
    ps.store.add_token("taken", now=T0, kind="client")
    for body in (
        {"kind": "worker", "colour": "red"},
        {"kind": "worker", "ttl_minutes": 0},
        {"kind": "worker", "ttl_minutes": 61},
        {"kind": "worker", "ttl_minutes": True},
        {"kind": "client", "pool": "gpu"},
        {"kind": "worker", "replace": True},
        {"kind": "admin"},
        {"kind": "worker", "pool": "bad pool"},
        {},
    ):
        status, doc = ps.req("POST", "/pair", token="admin", json_body=body)
        assert (status, doc["code"]) == (400, "bad_pair_admin_request"), (body, doc)
    for body in (
        {"kind": "worker", "name": "nobody", "replace": True},
        {"kind": "worker", "name": "taken", "replace": True},
    ):
        status, doc = ps.req("POST", "/pair", token="admin", json_body=body)
        assert (status, doc["code"]) == (400, "replace_needs_same_kind"), (body, doc)
    status, listed = ps.req("GET", "/pair", token="admin")
    assert status == 200 and listed == {"windows": []}


def test_pi47_a_the_201_has_exactly_the_spec_keys_and_the_code_only_there(ps):
    doc = ps.opened("worker", ttl_minutes=5)
    assert set(doc) == OPEN_201_KEYS
    assert re.fullmatch(r"[A-Z2-7]{5}-[A-Z2-7]{5}", doc["code"])
    assert re.fullmatch(r"[0-9a-f]{16}", doc["id"])
    assert doc["pool"] == "default" and doc["urls"] == [PUBLIC] and doc["replace"] is False
    assert doc["opened_at"].endswith("Z") and doc["expires_at"].endswith("Z")
    c = ps.opened("client")
    assert c["pool"] is None and c["presets_count"] is None
    _status, _headers, listed = ps.req("GET", "/pair", token="admin", raw=True)
    assert doc["code"].encode() not in listed
    assert doc["code"].replace("-", "").encode() not in listed


def test_pi47_d_the_code_key_is_made_outside_the_window_lock(ps, monkeypatch):
    """P3 r1: 창 열기의 scrypt 는 창 잠금 밖에서 돈다 — 그동안 교환 · 취소 · 목록이 기다리지
    않는다. 이미 열린 창의 409 는 잠금 안에서 그대로 본다."""
    real = cp.pair_code_key
    held: list[bool] = []

    def spy(code: str) -> bytes:
        held.append(ps.app._pair_windows.lock.locked())
        return real(code)

    monkeypatch.setattr(pairing_mod.cp, "pair_code_key", spy)
    doc = ps.opened("worker")
    status, body = ps.open("worker")
    assert (status, body["code"]) == (409, "pair_window_open")
    assert held == [False, False]
    monkeypatch.undo()
    body = ps.exchange_body(doc["code"], "worker", "build-51")  # 만든 코드 키가 창에 들어갔다
    assert ps.exchange(body)[0] == 200


def test_pi47_admin_routes_need_an_admin_token(ps):
    for method, path in (("POST", "/pair"), ("GET", "/pair"), ("POST", "/pair/cancel")):
        assert ps.req(method, path, json_body={"kind": "worker"})[0] == 401
        assert ps.req(method, path, token="alice", json_body={"kind": "worker"})[0] == 403
        assert ps.req(method, path, token="build-02", json_body={"kind": "worker"})[0] == 403


def test_pi47_a_body_over_1_kib_is_413_and_opens_nothing(ps):
    big = {"kind": "worker", "name": "x" * 2000}
    assert ps.req("POST", "/pair", token="admin", json_body=big)[0] == 413
    assert ps.req("GET", "/pair", token="admin")[1] == {"windows": []}


def test_pi47_b_closed_windows_carry_state_and_reason(ps, monkeypatch):
    # 쓰임
    used = ps.paired_by_exchange("worker", "build-01")
    # 틀린 코드 5번
    doc = ps.opened("client")
    for _ in range(5):
        ps.exchange(ps.exchange_body("AAAAA-AAAAA", "client"))
    # 취소
    w = ps.opened("worker")
    assert ps.req("POST", "/pair/cancel", token="admin", json_body={"kind": "worker"})[0] == 200
    # 만료
    ps.opened("worker", ttl_minutes=1)
    ps.clock.advance(61)
    # 행 쓰기 실패
    f = ps.opened("worker")

    def broken(*a, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ps.store, "add_paired_token", broken)
    status, body = ps.exchange(ps.exchange_body(f["code"], "worker", "build-09"))
    assert (status, body["code"]) == (503, "pair_failed")
    status, listed = ps.req("GET", "/pair", token="admin")
    rows = {(r["kind"], r["state"], r["reason"]): r for r in listed["windows"]}
    assert set(rows) == {
        ("worker", "used", None),
        ("client", "closed", "too_many_wrong_codes"),
        ("worker", "closed", "cancelled"),
        ("worker", "closed", "expired"),
        ("worker", "closed", "pair_failed"),
    }
    used_row = rows[("worker", "used", None)]
    assert used_row["paired"] == {
        "name": used["name"],
        "kind": "worker",
        "pool": "default",
        "host_name": "build-01",
        "addr": "127.0.0.1",
        "at": used_row["closed_at"],
    }
    for key, r in rows.items():
        if key[1] != "used":
            assert r["paired"] is None
        assert r["closed_at"] is not None and r["opened_by"] == "macmini-admin"
        assert set(r) == {
            "id",
            "kind",
            "pool",
            "name",
            "opened_by",
            "opened_at",
            "expires_at",
            "closed_at",
            "state",
            "reason",
            "paired",
        }
    assert rows[("client", "closed", "too_many_wrong_codes")]["id"] == doc["id"]
    assert rows[("worker", "closed", "cancelled")]["id"] == w["id"]


def test_pi47_c_cancel_by_kind_by_id_and_with_nothing_open(ps):
    w = ps.opened("worker")
    other = secrets.token_hex(8)
    status, body = ps.req(
        "POST", "/pair/cancel", token="admin", json_body={"kind": "worker", "id": other}
    )
    assert (status, body["code"]) == (404, "no_open_window")
    assert ps.req("GET", "/pair", token="admin")[1]["windows"][0]["state"] == "open"
    status, body = ps.req(
        "POST", "/pair/cancel", token="admin", json_body={"kind": "worker", "id": w["id"]}
    )
    assert status == 200 and body == {
        "id": w["id"],
        "kind": "worker",
        "state": "closed",
        "reason": "cancelled",
    }
    status, body = ps.req("POST", "/pair/cancel", token="admin", json_body={"kind": "worker"})
    assert (status, body["code"]) == (404, "no_open_window")
    status, body = ps.req("POST", "/pair/cancel", token="admin", json_body={"kind": "admin"})
    assert (status, body["code"]) == (400, "bad_pair_admin_request")


# ── PI50 scrypt 없는 서버 ───────────────────────────────────────────────────


def test_pi50_a_without_scrypt_post_pair_is_501_and_hello_still_works(ps, monkeypatch):
    out = ps.paired_by_exchange("worker", "build-01")
    pair_id, k = cp.parse_pair_secret(out["pair_secret"])
    monkeypatch.delattr(hashlib, "scrypt")
    status, body = ps.open("worker")
    assert status == 501 and body["code"] == "pair_unsupported"
    assert "hashlib.scrypt" in body["error"] and "rcm token add" in body["error"]
    assert ps.req("GET", "/pair", token="admin")[1]["windows"][0]["state"] == "used"
    req, (status, doc) = ps.hello(pair_id)
    assert status == 200 and cp.verify_hello(k, pair_id, req["nonce"], ps.dialed, doc)


# ── PI5 hello 중계 · 재생 · Store 0 ────────────────────────────────────────


def test_pi5_a_any_dialed_string_outside_the_set_is_421_not_a_shape_error(ps):
    """`dialed` 의 꼴은 명세가 정하지 않았다 — 문자열이면 자기 주소 집합과 대조할 뿐이다(§4.2)."""
    for dialed in ("", "http://" + "a" * 400 + ":1", "not a url"):
        _, (status, body) = ps.hello(fresh_pair_id(), dialed=dialed)
        assert (status, body["code"]) == (421, "host_not_this_server"), dialed


def test_pi5_a_a_relayed_hello_naming_another_address_is_421_without_mac(ps):
    pair_id = fresh_pair_id()
    _, (status, body) = ps.hello(pair_id, dialed="http://192.0.2.66:8787")
    assert status == 421 and body["code"] == "host_not_this_server"
    assert "public_url" in body["error"]
    assert "mac" not in body and "challenge" not in body


def test_pi5_b_c_the_mac_binds_the_real_dialed_and_the_nonce(ps):
    out = ps.paired_by_exchange("worker", "build-01")
    pair_id, k = cp.parse_pair_secret(out["pair_secret"])
    req, (status, doc) = ps.hello(pair_id)
    assert status == 200 and set(doc) == {"v", "challenge", "mac"}
    assert cp.verify_hello(k, pair_id, req["nonce"], ps.dialed, doc)
    assert not cp.verify_hello(k, pair_id, req["nonce"], "http://192.0.2.66:8787", doc)
    assert not cp.verify_hello(k, pair_id, secrets.token_hex(32), ps.dialed, doc)
    assert doc["mac"] == ref_mac(
        ref_k(ps.key, pair_id), "rcm-hello-v1", pair_id, req["nonce"], ps.dialed, doc["challenge"]
    )


def test_pi5_c_the_nonce_generator_never_repeats():
    assert len({pairing_mod.new_nonce() for _ in range(1000)}) == 1000
    assert all(cp.HEX64_RE.fullmatch(pairing_mod.new_nonce()) for _ in range(10))


def test_pi5_hello_does_not_touch_the_store(ps, monkeypatch):
    calls: list[str] = []
    real = ps.store._conn

    def spy():
        calls.append("conn")
        return real()

    monkeypatch.setattr(ps.store, "_conn", spy)
    for pid in (fresh_pair_id(), fresh_pair_id()):
        _, (status, _body) = ps.hello(pid)
        assert status == 200
    ps.hello(fresh_pair_id(), dialed="http://192.0.2.66:1")
    assert calls == []


def test_pi18_a_server_with_a_new_key_fails_the_old_machines_hello(ps, tmp_path):
    out = ps.paired_by_exchange("worker", "build-01")
    pair_id, k = cp.parse_pair_secret(out["pair_secret"])
    other = PairServer(tmp_path / "other")  # 다른 data_dir → 다른 pair.key
    try:
        req, (status, doc) = other.hello(pair_id)
        assert status == 200
        assert cp.classify_hello(200, doc, k, pair_id, req["nonce"], other.dialed) == "mismatch"
    finally:
        other.close()


# ── PI22 bind · URL · public_url ────────────────────────────────────────────


def test_pi22_a_a_loopback_bind_without_public_url_cannot_open_a_window(tmp_path):
    s = PairServer(tmp_path, public_url="", bind="127.0.0.1")
    try:
        status, body = s.open("worker")
        assert (status, body["code"]) == (409, "pair_unreachable")
        assert "set bind (and public_url) first" in body["error"]
        assert s.req("GET", "/pair", token="admin")[1] == {"windows": []}
    finally:
        s.close()


def test_pi22_b_c_urls_for_a_lan_bind_and_for_any_address(tmp_path):
    s = PairServer(tmp_path, public_url="", bind="192.0.2.10", advertise=False)
    try:
        assert s.opened("worker")["urls"] == [f"http://192.0.2.10:{s.port}"]
    finally:
        s.close()
    s = PairServer(tmp_path / "b", public_url="", bind="0.0.0.0", advertise=True)
    s.app.pair_ips_fn = lambda: ["192.0.2.10", "192.0.2.7"]
    try:
        host = socket.gethostname().split(".")[0].lower()
        urls = s.opened("worker")["urls"]
        assert urls == [
            f"http://192.0.2.10:{s.port}",
            f"http://192.0.2.7:{s.port}",
            f"http://{host}.local:{s.port}",
        ]
        assert all("0.0.0.0" not in u for u in urls)
        # 광고 이름으로 접속한 hello 도 자기 주소다
        _, (status, _b) = s.hello(fresh_pair_id(), dialed=f"http://{host}.local:{s.port}")
        assert status == 200
    finally:
        s.close()


def test_pi22_f_a_tls_proxy_public_url_matches_https_443(tmp_path):
    s = PairServer(tmp_path, public_url="https://rcm.example.ts.net", bind="127.0.0.1")
    try:
        assert s.opened("worker")["urls"] == ["https://rcm.example.ts.net"]
        _, (status, _b) = s.hello(fresh_pair_id(), dialed="https://rcm.example.ts.net:443")
        assert status == 200
        _, (status, _b) = s.hello(fresh_pair_id(), dialed="http://rcm.example.ts.net:80")
        assert status == 421
        _, (status, b) = s.hello(fresh_pair_id(), dialed="http://other-name.ts.net:443")
        assert status == 421 and "set public_url" in b["error"]
    finally:
        s.close()


# ── PI7 · PI8 · PI9 · PI10 교환 ─────────────────────────────────────────────


def token_rows(ps) -> int:
    return int(ps.store._conn().execute("SELECT COUNT(*) FROM tokens").fetchone()[0])


def test_pi7_a_worker_code_cannot_mint_another_kind(ps):
    doc = ps.opened("worker")
    before = token_rows(ps)
    for body_kind in ("client", "admin", "nonsense"):
        raw = ps.exchange(ps.exchange_body(doc["code"], "worker", body_kind=body_kind), raw=True)
        assert raw[0] == 404 and raw[2] == FIXED_404, (body_kind, raw)
    body = ps.exchange_body(doc["code"], "worker")
    del body["kind"]
    raw = ps.exchange(body, raw=True)
    assert raw[0] == 400 and raw[2] == FIXED_400  # 형식 검사가 먼저다(§4.4, workplan_diff)
    assert token_rows(ps) == before
    out = ps.exchange(ps.exchange_body(doc["code"], "worker"))[1]
    ps.tokens["paired-w"] = out["token"]
    assert ps.req("GET", "/api/machines", token="paired-w")[0] == 403
    status, body = ps.req("POST", "/pause", token="paired-w")
    assert status == 403 and body["error"] == "worker tokens cannot use the client API"


def test_pi8_a_sixteen_concurrent_exchanges_issue_exactly_one_token(ps):
    doc = ps.opened("worker")
    body = ps.exchange_body(doc["code"], "worker")
    before = token_rows(ps)
    results: list[tuple] = []
    lock = threading.Lock()
    go = threading.Barrier(16)

    def one():
        go.wait(10)
        r = ps.exchange(body, raw=True)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=one) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(r[0] for r in results) == [200] + [404] * 15
    assert all(r[2] == FIXED_404 for r in results if r[0] == 404)
    assert token_rows(ps) == before + 1


def test_pi8_b_a_used_window_leaves_a_listed_revocable_token(ps):
    out = ps.paired_by_exchange("worker", "build-01")
    row = ps.store.token_row(out["name"])
    assert (row.paired_host, row.paired_addr, row.kind, row.pool) == (
        "build-01",
        "127.0.0.1",
        "worker",
        "default",
    )
    ps.tokens["p"] = out["token"]
    assert ps.req("GET", "/worker/spec", token="p")[0] == 200
    ps.store.revoke_token(out["name"], T0)
    assert ps.req("GET", "/worker/spec", token="p")[0] == 401


def test_pi8_c_g2_the_answer_is_ready_before_the_token_row_is_written(ps, monkeypatch):
    """G2: 답에 실을 값(`presets_count` 등)을 행을 쓰기 전에 만든다. 그 계산이 실패하면 창은
    열린 채이고 토큰도 생기지 않는다 — 커밋한 뒤에 500 이 나 코드는 쓰였는데 토큰만 남는 일이
    없다."""
    doc = ps.opened("worker")
    before = token_rows(ps)

    def broken(pool: str) -> int:
        raise RuntimeError("presets could not be counted")

    monkeypatch.setattr(ps.app, "pair_presets_count", broken)
    with pytest.raises(RuntimeError):
        ps.app.pair_exchange(ps.exchange_body(doc["code"], "worker", "build-21"), "192.0.2.7")
    assert ps.app.pair_window_state("worker")["state"] == "open"
    assert token_rows(ps) == before
    monkeypatch.undo()
    status, out = ps.exchange(ps.exchange_body(doc["code"], "worker", "build-21"))
    assert (status, out["name"]) == (200, "build-21")
    assert token_rows(ps) == before + 1


class OrderLock:
    """창 잠금 대신 넣는 잠금(G3). 스레드 이름으로 두 가지를 한다: `arrive[name]` 은 그 스레드가
    잠금을 **기다리기 시작할 때** 켜고, `hold[name] = (held, go)` 는 그 스레드가 잠금을 쥔 채
    `go` 까지 멈추게 한다. 기다림의 10초는 결과를 정하지 않는 안전 한도다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.arrive: dict[str, threading.Event] = {}
        self.hold: dict[str, tuple[threading.Event, threading.Event]] = {}

    def __enter__(self) -> OrderLock:
        name = threading.current_thread().name
        if name in self.arrive:
            self.arrive[name].set()
        self._lock.acquire()
        if name in self.hold:
            held, go = self.hold[name]
            held.set()
            go.wait(10)
        return self

    def __exit__(self, *exc) -> None:
        self._lock.release()


def run_named(name: str, fn, results: dict, done: threading.Event) -> threading.Thread:
    """`fn()` 을 그 이름의 스레드로 — 결과는 답 dict 또는 `PairRefusal` 의 (상태, code)."""

    def body() -> None:
        try:
            results[name] = fn()
        except pairing_mod.PairRefusal as e:
            results[name] = (e.status, e.extra.get("code"))
        finally:
            done.set()

    t = threading.Thread(target=body, name=name)
    t.start()
    return t


def test_pi8_d_g3_cancel_and_exchange_are_serialised_by_one_lock(ps, monkeypatch):
    """G3: 열기 · 취소 · 교환은 한 잠금으로 차례를 선다. 취소와 교환이 겹쳐도 결과는 두 순서
    중 하나다 — 교환 200 → 취소 404, 또는 취소 200 → 교환 404. 둘 다 200(취소된 창으로 토큰
    발급)이 없다. 배리어로 두 순서를 하나씩 강제한다."""
    lock = OrderLock()
    monkeypatch.setattr(ps.app._pair_windows, "lock", lock)
    before = token_rows(ps)

    # ① 교환이 잠금 안(증명이 맞은 뒤, 행을 쓰기 전)에서 멈춘 사이 취소가 온다
    doc = ps.opened("worker")
    inside, go = threading.Event(), threading.Event()
    real_count = ps.app.pair_presets_count

    def paused_count(pool: str) -> int:
        inside.set()
        go.wait(10)
        return real_count(pool)

    monkeypatch.setattr(ps.app, "pair_presets_count", paused_count)
    results: dict = {}
    ex_done, cancel_seen = threading.Event(), threading.Event()
    body = ps.exchange_body(doc["code"], "worker", "build-41")
    ex = run_named("exchange-1", lambda: ps.app.pair_exchange(body, "192.0.2.8"), results, ex_done)
    assert inside.wait(10)
    lock.arrive["cancel-1"] = cancel_seen
    cancel = run_named(
        "cancel-1", lambda: ps.app.pair_cancel({"kind": "worker"}), results, cancel_seen
    )
    assert cancel_seen.wait(10)  # 취소가 잠금 앞에 왔다(잠금이 없으면 이미 끝났다)
    go.set()
    ex.join(10)
    cancel.join(10)
    assert results["cancel-1"] == (404, "no_open_window"), results
    assert isinstance(results["exchange-1"], dict) and results["exchange-1"]["name"] == "build-41"
    assert ps.app.pair_window_state("worker")["state"] == "used"
    monkeypatch.setattr(ps.app, "pair_presets_count", real_count)

    # ② 취소가 잠금을 쥔 사이 교환이 온다
    doc = ps.opened("worker")
    held, go2 = threading.Event(), threading.Event()
    lock.hold["cancel-2"] = (held, go2)
    results, c_done, ex_seen = {}, threading.Event(), threading.Event()
    cancel = run_named("cancel-2", lambda: ps.app.pair_cancel({"kind": "worker"}), results, c_done)
    held.wait(10)
    body = ps.exchange_body(doc["code"], "worker", "build-42")
    lock.arrive["exchange-2"] = ex_seen
    ex = run_named("exchange-2", lambda: ps.app.pair_exchange(body, "192.0.2.9"), results, ex_seen)
    assert ex_seen.wait(10)
    go2.set()
    cancel.join(10)
    ex.join(10)
    assert results["exchange-2"] == (404, "no_pairing_window"), results
    assert results["cancel-2"]["reason"] == "cancelled", results
    assert ps.app.pair_window_state("worker") == {
        "id": doc["id"],
        "state": "closed",
        "reason": "cancelled",
        "failures": 0,
    }
    assert token_rows(ps) == before + 1


class CompareSpy:
    def __init__(self):
        self.calls = 0

    def compare_digest(self, a, b):
        self.calls += 1
        return _hmac.compare_digest(a, b)

    def __getattr__(self, name):
        return getattr(_hmac, name)


def test_pi9_five_wrong_proofs_close_the_window_from_any_source(ps, monkeypatch):
    spy = CompareSpy()
    monkeypatch.setattr(pairing_mod, "hmac", spy)
    doc = ps.opened("worker")
    wrong = "AAAAA-AAAAA"
    for i in range(4):
        with pytest.raises(pairing_mod.PairRefusal) as e:  # 다섯 출처에서 한 번씩
            ps.app.pair_exchange(ps.exchange_body(wrong), f"192.0.2.{i + 1}")
        assert e.value.status == 404
    assert ps.req("GET", "/pair", token="admin")[1]["windows"][0]["state"] == "open"
    assert ps.exchange(ps.exchange_body(wrong), raw=True)[2] == FIXED_404  # 다섯 번째
    listed = ps.req("GET", "/pair", token="admin")[1]["windows"][0]
    assert (listed["state"], listed["reason"]) == ("closed", "too_many_wrong_codes")
    assert ps.exchange(ps.exchange_body(doc["code"]), raw=True)[2] == FIXED_404
    assert (
        "pair: worker window closed after 5 wrong codes — open a new one with rcm pair" in ps.logs
    )
    assert spy.calls == 5  # 틀린 증명마다 한 번, 닫힌 창에는 비교하지 않는다


def test_pi10_every_failure_is_the_same_404_and_bad_shapes_the_same_400(ps):
    answers = []
    # 연 적 없는 창 · 그 kind 의 창 없음
    answers.append(ps.exchange(ps.exchange_body("ABCDE-FGHJK"), raw=True))
    doc = ps.opened("worker", ttl_minutes=1)
    answers.append(ps.exchange(ps.exchange_body("AAAAA-AAAAA"), raw=True))  # 틀린 증명
    answers.append(ps.exchange(ps.exchange_body(doc["code"], "client"), raw=True))  # 다른 kind
    bad_open = ps.exchange({"v": 1, "kind": "worker", "host_name": 5}, raw=True)
    ps.exchange(ps.exchange_body(doc["code"]))  # 쓴다
    answers.append(ps.exchange(ps.exchange_body(doc["code"]), raw=True))  # 이미 쓴 코드
    d2 = ps.opened("worker", ttl_minutes=1)
    ps.clock.advance(61)
    answers.append(ps.exchange(ps.exchange_body(d2["code"]), raw=True))  # 만료
    d3 = ps.opened("worker")
    ps.req("POST", "/pair/cancel", token="admin", json_body={"kind": "worker"})
    answers.append(ps.exchange(ps.exchange_body(d3["code"]), raw=True))  # 닫힌 창
    bad_closed = ps.exchange({"v": 1, "kind": "worker", "host_name": 5}, raw=True)
    shapes = {no_window_404(a) for a in answers}
    assert len(answers) == 6 and len(shapes) == 1
    assert next(iter(shapes))[0] == 404 and next(iter(shapes))[2] == FIXED_404
    assert no_window_404(bad_open) == no_window_404(bad_closed)
    assert bad_open[0] == 400 and bad_open[2] == FIXED_400


def test_pi10_read_routes_and_mdns_answers_do_not_show_a_window(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeResponder:
        """소켓을 열지 않는 광고 — 받은 인자만 쥔다(`/api/health` 의 `advertise` 가 읽는 두 칸)."""

        error = None

        def __init__(self, *args, **kwargs):
            captured["args"], captured["kwargs"] = args, kwargs
            self.instance = args[0]

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(server_mod, "Responder", FakeResponder)
    s = PairServer(tmp_path, workers=True, advertise=True)
    try:
        from remote_ci_monitor.mdns import Responder

        kwargs = {**captured["kwargs"], "ips_fn": lambda: ["192.0.2.10"]}
        responder = Responder(*captured["args"], **kwargs)

        closed = responder.response()
        s.opened("worker")
        s.opened("client")
        assert responder.response() == closed
        assert set(kwargs["txt_fn"]()) == {"v", "name", "lanes"}
    finally:
        s.close()


def test_pi10_read_routes_are_the_same_bytes_with_and_without_a_window(ps):
    """같은 시각(주입 시계) · 같은 표본 — 로컬 레인 · 표본 스레드를 띄우지 않은 서버다."""

    def snapshot():
        return [
            ps.req("GET", path, raw=True)[2]
            for path in ("/api/status", "/api/machines", "/api/health")
        ]

    closed = snapshot()
    ps.opened("worker")
    ps.opened("client")
    assert snapshot() == closed
    # SSE /events 의 첫 프레임(hello)도 같은 바이트다. 스트림은 끊은 뒤에도 서버가 잠시 세어
    # (`sse_connections`) 위의 읽기 비교가 끝난 다음에 본다 — 열린 창 둘을 취소해 닫기 전과 뒤
    opened = sse_first_frame(ps)
    for kind in ("worker", "client"):
        assert ps.req("POST", "/pair/cancel", token="admin", json_body={"kind": kind})[0] == 200
    assert sse_first_frame(ps) == opened
    assert opened[0] == 200 and opened[1].startswith(b"event: hello\n")


# ── PI11 시계 · 재시작 · reload · 목록 보존 ───────────────────────────────────


def test_pi11_expiry_follows_the_server_clock(ps):
    doc = ps.opened("worker", ttl_minutes=10)
    body = ps.exchange_body(doc["code"])
    ps.clock.advance(599)
    assert ps.exchange(body)[0] == 200
    d2 = ps.opened("worker", ttl_minutes=10)
    ps.clock.advance(601)
    assert ps.exchange(ps.exchange_body(d2["code"]), raw=True)[2] == FIXED_404


def test_pi11_a_restart_forgets_open_windows(ps):
    """재시작 = 같은 data_dir · 같은 DB 위의 새 App(창은 메모리에만 있다)."""
    doc = ps.opened("worker")
    body = ps.exchange_body(doc["code"])
    restarted = server_mod.App(ps.cfg, ps.store, now_fn=ps.clock, mono_fn=ps.clock.mono)
    with pytest.raises(pairing_mod.PairRefusal) as e:
        restarted.pair_exchange(body, "127.0.0.1")
    assert (e.value.status, e.value.extra) == (404, {"code": "no_pairing_window"})
    assert restarted.pair_list() == {"windows": []}
    assert restarted.pair_key == ps.key  # 키는 파일에서 같은 값을 읽는다


def test_pi11_reload_keeps_the_window_and_its_expiry(ps, monkeypatch, tmp_path):
    import dataclasses

    ps.app.config = dataclasses.replace(ps.app.config, path=tmp_path / "server.toml")
    fresh = dataclasses.replace(ps.app.config)
    monkeypatch.setattr(server_mod, "load_server_config", lambda path: fresh)
    doc = ps.opened("worker")
    status, _ = ps.req("POST", "/reload", token="admin", json_body={})
    assert status == 200
    assert ps.app.config is fresh  # 정말 다시 읽었다
    listed = ps.req("GET", "/pair", token="admin")[1]["windows"]
    assert [(w["id"], w["expires_at"], w["state"]) for w in listed] == [
        (doc["id"], doc["expires_at"], "open")
    ]


def test_pi11_ttl_61_is_refused_by_the_server(ps):
    status, body = ps.open("worker", ttl_minutes=61)
    assert (status, body["code"]) == (400, "bad_pair_admin_request")


def test_pi11_closed_windows_stay_listed_for_an_hour_from_closed_at(ps):
    """V2-10: 60분 창이 59분에 쓰이거나 끝까지 두어 만료된 뒤 30분이 더 지나도 목록에 있다."""
    used = ps.opened("worker", ttl_minutes=60)
    ps.clock.advance(59 * 60)
    ps.exchange(ps.exchange_body(used["code"]))
    expired = ps.opened("client", ttl_minutes=60)
    ps.clock.advance(60 * 60 + 30 * 60)  # client 창은 만료 + 30분, worker 창은 쓰인 뒤 90분
    rows = {w["id"]: w for w in ps.req("GET", "/pair", token="admin")[1]["windows"]}
    assert rows[expired["id"]]["state"] == "closed" and rows[expired["id"]]["reason"] == "expired"
    assert used["id"] not in rows  # 쓰인 지 90분 — 한 시간이 지났다
    ps.clock.advance(31 * 60)
    rows = {w["id"]: w for w in ps.req("GET", "/pair", token="admin")[1]["windows"]}
    assert expired["id"] not in rows


def test_pi11_the_list_keeps_the_newest_twenty_closed_windows(ps):
    ids = []
    for _ in range(25):
        ids.append(ps.opened("worker")["id"])
        ps.req("POST", "/pair/cancel", token="admin", json_body={"kind": "worker"})
        ps.clock.advance(1)
    listed = [w["id"] for w in ps.req("GET", "/pair", token="admin")[1]["windows"]]
    assert sorted(listed) == sorted(ids[-20:])


# ── PI12 창 둘 · 이름 ─────────────────────────────────────────────────────


def test_pi12_a_b_one_window_per_kind_and_codes_do_not_cross_kinds(ps):
    first = ps.opened("worker")
    status, body = ps.open("worker")
    assert (status, body["code"]) == (409, "pair_window_open")
    assert body["expires_at"] == first["expires_at"]
    client = ps.opened("client")
    assert ps.exchange(ps.exchange_body(client["code"], "worker"), raw=True)[2] == FIXED_404
    assert ps.exchange(ps.exchange_body(first["code"], "client"), raw=True)[2] == FIXED_404
    assert ps.exchange(ps.exchange_body(first["code"], "worker"))[0] == 200


def test_pi12_c_names_taken_at_open_and_at_exchange(ps):
    ps.store.add_token("gone", now=T0, kind="worker")
    ps.store.revoke_token("gone", T0)
    status, body = ps.open("worker", name="gone")
    assert (status, body["code"], body["name"]) == (409, "name_taken", "gone")
    doc = ps.opened("worker")
    status, body = ps.exchange(ps.exchange_body(doc["code"], "worker", "build-02"))
    assert (status, body["code"], body["name"]) == (409, "name_taken", "build-02")
    assert "already has a token named 'build-02'" in body["error"]
    assert ps.req("GET", "/pair", token="admin")[1]["windows"][0]["state"] == "open"
    assert ps.exchange(ps.exchange_body(doc["code"], "worker", "build-77"))[0] == 200


def test_pi12_c_g1_a_name_taken_between_the_check_and_the_write_keeps_the_code(ps, monkeypatch):
    """G1: 교환의 이름 확인(`token_row`)과 행 쓰기 사이에 `rcm token add` 가 같은 이름을
    가져가면 Store 가 `TokenNameTaken` 을 낸다 — 409 `name_taken` 이고 창은 열린 채라 같은 코드로
    다른 이름의 교환이 200 이다(503 `pair_failed` 로 창을 닫아 코드를 태우지 않는다)."""
    doc = ps.opened("worker")
    real = ps.store.add_paired_token
    calls: list[str] = []

    def racing(name: str, **kw):
        calls.append(name)
        if len(calls) == 1:
            ps.store.add_token(name, now=T0, kind="worker")  # 그 사이의 rcm token add
        return real(name, **kw)

    monkeypatch.setattr(ps.store, "add_paired_token", racing)
    before = token_rows(ps)
    status, body = ps.exchange(ps.exchange_body(doc["code"], "worker", "build-31"))
    assert (status, body.get("code"), body.get("name")) == (409, "name_taken", "build-31"), body
    assert ps.app.pair_window_state("worker")["state"] == "open"
    assert token_rows(ps) == before + 1  # rcm token add 의 행 하나뿐
    status, out = ps.exchange(ps.exchange_body(doc["code"], "worker", "build-32"))
    assert (status, out["name"]) == (200, "build-32"), out
    assert calls == ["build-31", "build-32"]


@pytest.mark.parametrize(
    ("host_name", "want"),
    [
        ("Dev's MacBook Pro", "Dev-s-MacBook-Pro"),
        ("y" * 64, "y" * 64),
        ("빌드머신", None),
        ("evil\nline\r\x1b[31m", "evil-line--[31m"),
    ],
)
def test_pi12_names_come_from_the_cleaned_host_name(ps, host_name, want):
    out = ps.paired_by_exchange("worker", host_name)
    if want is None:
        assert re.fullmatch(r"worker-[0-9a-f]{6}", out["name"]), out["name"]
    else:
        assert out["name"] == re.sub(r"[^A-Za-z0-9._-]", "-", host_name).lstrip("._-")[:64]
    listed = json.dumps(ps.req("GET", "/pair", token="admin")[1])
    for blob in (listed, "\n".join(ps.logs)):
        assert "\n" not in host_name or "evil\nline" not in blob
        assert "\x1b" not in blob and "\r" not in blob
    assert all("\n" not in line for line in ps.logs)
    row = ps.store.token_row(out["name"])
    assert row.paired_host is None or "\n" not in row.paired_host


# ── PI17 비밀이 어디에도 없다(서버) ─────────────────────────────────────────


def test_pi17_no_secret_in_logs_lists_or_status(ps):
    doc = ps.opened("worker")
    code_key = cp.pair_code_key(cp.normalize_pair_code(doc["code"]))
    ps.exchange(ps.exchange_body("AAAAA-AAAAA"))
    out = ps.exchange(ps.exchange_body(doc["code"]))[1]
    pair_id = cp.parse_pair_secret(out["pair_secret"])[0]
    ps.hello(pair_id)
    secrets_seen = [
        out["token"],
        out["pair_secret"],
        out["pair_secret"].split(":")[1],
        doc["code"],
        doc["code"].replace("-", ""),
        code_key.hex(),
        ps.key.hex(),
    ]
    blobs = [
        "\n".join(ps.logs),
        ps.req("GET", "/pair", token="admin", raw=True)[2].decode(),
        ps.req("GET", "/api/status", raw=True)[2].decode(),
        ps.req("GET", "/api/machines", raw=True)[2].decode(),
    ]
    for blob in blobs:
        for value in secrets_seen:
            assert value not in blob
    assert f"pair: worker '{out['name']}' paired from 127.0.0.1 (host build-01, pool default)" in (
        ps.logs
    )


# ── PI19 b --replace 를 교환으로 ──────────────────────────────────────────


def test_pi19_b_a_replace_window_swaps_the_token(ps):
    first = ps.paired_by_exchange("worker", "build-01")
    ps.tokens["old"] = first["token"]
    second = ps.paired_by_exchange("worker", "elsewhere", name=first["name"], replace=True)
    assert second["name"] == first["name"]
    ps.tokens["new"] = second["token"]
    assert ps.req("GET", "/worker/spec", token="old")[0] == 401
    assert ps.req("GET", "/worker/spec", token="new")[0] == 200
    active = [t for t in ps.store.list_tokens() if t.name == first["name"] and t.revoked_at is None]
    assert len(active) == 1
    assert ps.store.token_pair_id(first["token"]) is None


# ── PI40 익명 입력 ──────────────────────────────────────────────────────────


def malformed_exchanges() -> list[bytes]:
    good = {
        "v": 1,
        "kind": "worker",
        "host_name": "h",
        "worker_nonce": secrets.token_hex(32),
        "proof": secrets.token_hex(32),
    }
    bodies = [
        {**good, "host_name": 7},
        {**good, "host_name": None},
        {**good, "host_name": "z" * 65},
        {**good, "worker_nonce": good["worker_nonce"][:63]},
        {**good, "worker_nonce": good["worker_nonce"].upper()},
        {**good, "proof": 12},
        {**good, "v": True},
        [good],
    ]
    out = [json.dumps(b).encode() for b in bodies]
    out.append(json.dumps(good).replace('"h"', '"\\ud800"').encode())  # 홀로 선 서러게이트
    out.append(b"[" * 1000)  # 1 KiB 안의 깊이 초과(M8)
    out.append(b"\xff\xfe\x00")  # UTF-8 아님
    return out


def test_pi40_malformed_anonymous_bodies_are_fixed_400s_whether_or_not_a_window_is_open(
    ps, monkeypatch
):
    errors: list[str] = []
    monkeypatch.setattr(ps.app, "record_error", lambda msg, **kw: errors.append(msg))
    closed = [ps.req("POST", "/pair/exchange", body=b, raw=True) for b in malformed_exchanges()]
    ps.opened("worker")
    opened = [ps.req("POST", "/pair/exchange", body=b, raw=True) for b in malformed_exchanges()]
    for raw in closed + opened:
        assert raw[0] == 400 and raw[2] == FIXED_400, raw
    hello_bad = [ps.req("POST", "/pair/hello", body=b, raw=True) for b in (b"[" * 1000, b"\xff")]
    for raw in hello_bad:
        assert raw[0] == 400 and json.loads(raw[2])["code"] == "bad_hello", raw
    assert errors == []
    assert all("Traceback" not in line for line in ps.logs)
    win = ps.app.pair_window_state("worker")
    assert win["failures"] == 0


def test_pi40_an_empty_host_name_is_valid_and_gets_a_made_up_name(ps):
    out = ps.paired_by_exchange("worker", "")
    assert re.fullmatch(r"worker-[0-9a-f]{6}", out["name"])
    client = ps.paired_by_exchange("client", "")
    assert re.fullmatch(r"client-[0-9a-f]{6}", client["name"])
    assert client["pool"] is None and client["presets_count"] is None


# ── PI20 익명 라우트 ────────────────────────────────────────────────────────


EXTRA_BRANCHES = [
    "/api/machines",
    "/machines/@local/recheck",
    "/worker/spec",
    "/worker/register",
    "/worker/claim",
    "/worker/heartbeat",
    "/worker/capabilities",
    "/worker/jobs/1/finish",
    "/client/x",
    "/api/shelf",
    "/shelf/1",
    "/shelf/1/restore",
    "/gc",
    "/reload",
    "/pair",
    "/pair/hello",
    "/pair/exchange",
    "/pair/cancel",
    # M5t-a · M5t-b 가 더한 갈래(PR1 이 다시 셌다 — §4.7)
    "/jobs/1/release-hold",
    "/jobs/1/keep",
    "/jobs/1/artifacts/ack",
    "/jobs/1/priority",
    "/jobs/1/artifacts/archive",
    "/jobs/1/tree/manifest",
    "/worker/jobs/1/log",
    "/worker/jobs/1/artifacts",
    "/worker/jobs/1/tree",
    "/api/repos/app/release/versions",
    "/api/repos/app/release/versions/1",
    "/api/repos/app/release/versions/1/listing",
]


def plan_api_paths() -> list[str]:
    """PLAN 「서버 API」 표의 첫 칸에 적힌 경로들(경로 변수는 1 · app 으로 채운다)."""
    plan = (ROOT / "PLAN.md").read_text()
    start = plan.index("## 서버 API")
    table = plan[start : plan.index("\n## ", start + 5)]
    out: list[str] = []
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        cell = line.split("|")[1]
        base = ""
        for seg in re.findall(r"`([^`]*)`", cell):
            seg = re.sub(r"^(GET|POST|PUT|DELETE)\s+", "", seg.strip())
            seg = re.sub(r"\[.*?\]|\?.*$", "", seg).replace("{repo}", "app").replace("{id}", "1")
            seg = seg.replace("{name}", "x").replace("{file}", "x").replace("/*", "/x")
            if seg.startswith("…"):
                first = seg[1:].split("/")[1]
                seg = base[: base.rfind("/" + first)] + seg[1:]
            elif (
                seg.startswith("/")
                and base
                and not seg.startswith(("/api", "/jobs", "/pause", "/resume", "/events"))
            ):
                if seg not in ("/", "/static/x"):
                    seg = base.rsplit("/", 1)[0] + seg
            if seg.startswith("/"):
                out.append(seg)
                base = seg
    return out


ANON_ACCEPTED = {
    "none": {
        ("POST", "/api/eta"),
        ("POST", "/shelf/1/restore"),
        ("POST", "/pair/hello"),
        ("POST", "/pair/exchange"),
    },
    "basic": {("POST", "/pair/hello"), ("POST", "/pair/exchange")},
}


@pytest.mark.parametrize("read_auth", ["none", "basic"])
def test_pi20_the_anonymous_writes_are_exactly_the_spec_table(tmp_path, read_auth):
    s = PairServer(tmp_path, read_auth=read_auth)
    try:
        paths = sorted(set(plan_api_paths()) | set(EXTRA_BRANCHES))
        assert "/api/whoami" in paths and "/api/repos/app/release/start" in paths
        accepted = set()
        for path in paths:
            for method in ("POST", "PUT", "DELETE"):
                status = s.req(
                    method, path, body=b"{}", headers={"Content-Type": "application/json"}, raw=True
                )[0]
                if status not in (401, 403, 405):
                    accepted.add((method, path))
        assert accepted == ANON_ACCEPTED[read_auth]
        for method, path in (
            ("POST", "/pair"),
            ("POST", "/pair/cancel"),
            ("POST", "/worker/claim"),
        ):
            assert s.req(method, path, body=b"{}", raw=True)[0] == 401
    finally:
        s.close()


def test_pi20_new_routes_answer_413_400_and_503(ps):
    big = b'{"v": 1, "pad": "' + b"x" * 1100 + b'"}'
    assert ps.req("POST", "/pair/hello", body=big, raw=True)[0] == 413
    assert ps.req("POST", "/pair/exchange", body=big, raw=True)[0] == 413
    assert ps.req("POST", "/pair/hello", body=b"{nope", raw=True)[0] == 400
    sem = ps.httpd.slots
    held = 0
    while sem.acquire(blocking=False):
        held += 1
    try:
        assert ps.req("POST", "/pair/hello", body=b"{}", raw=True)[0] == 503
        assert ps.req("POST", "/pair/exchange", body=b"{}", raw=True)[0] == 503
    finally:
        for _ in range(held):
            sem.release()


# ── PI27 같은 코드를 지나는 다른 길 ─────────────────────────────────────────


def test_pi27_other_token_paths_keep_their_shape(ps):
    status, body = ps.req("GET", "/api/whoami", token="alice")
    assert status == 200 and set(body) == {"name", "admin", "kind"}
    assert ps.req("GET", "/api/whoami")[0] == 401
    assert ps.req("GET", "/api/machines", token="build-02")[0] == 403
    basic = base64.b64encode(f"alice-laptop:{ps.tokens['alice']}".encode()).decode()
    assert ps.req("POST", "/pause", headers={"Authorization": f"Basic {basic}"})[0] == 401
    assert ps.req("POST", "/pause", token="admin")[0] == 200
    assert ps.req("POST", "/resume", token="admin")[0] == 200


def sse_first_frame(srv, token: str | None = None, headers: dict | None = None):
    """`GET /events` 의 상태와 첫 프레임(200 이면 빈 줄까지, 아니면 본문) — 그다음 끊는다."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=15)
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {srv.tokens.get(token, token)}"
    conn.request("GET", "/events", headers=hdrs)
    resp = conn.getresponse()
    if resp.status != 200:
        payload = resp.read()
        conn.close()
        return resp.status, payload
    frame = b""
    while not frame.endswith(b"\n\n"):
        line = resp.fp.readline()
        if not line:
            break
        frame += line
    conn.close()
    return 200, frame


def test_pi27_g6_events_shelf_and_driver_tokens_keep_their_shape(tmp_path, monkeypatch):
    """G6: `TokenInfo` 를 만드는 `_token_info` 를 모든 토큰 읽기가 함께 쓰게 된 뒤에도 SSE
    `/events`, 보관함, 릴리스 드라이버 토큰 발급 · 폐기의 상태 코드와 키가 그대로다. 읽기 인증이
    켜진(`basic`) 서버라 토큰 검사가 실제로 돈다. 짝지은 토큰(worker · client)도 같은 길을 간다."""
    from remote_ci_monitor.release_driver import KIND_START, DriverRunner, token_name

    s = PairServer(tmp_path, read_auth="basic")
    try:
        s.tokens["gone"] = s.store.add_token("gone-client", now=T0, kind="client")
        s.store.revoke_token("gone-client", T0)
        s.tokens["paired-w"] = s.paired_by_exchange("worker", "build-01")["token"]
        s.tokens["paired-c"] = s.paired_by_exchange("client", "laptop-1")["token"]
        basic = base64.b64encode(f"alice-laptop:{s.tokens['alice']}".encode()).decode()

        # SSE /events
        unauth = b'{"error": "read access requires a token on this server"}'
        for who in (None, "no-such-token", "gone"):
            assert sse_first_frame(s, who) == (401, unauth), who
        for who in ("alice", "admin", "build-02", "paired-w", "paired-c"):
            status, frame = sse_first_frame(s, who)
            assert status == 200 and frame.startswith(b"event: hello\nid: 0\ndata: "), who
            hello = json.loads(frame.split(b"data: ", 1)[1])
            assert set(hello) == {"last_id", "generated_at", "server"}, who
        assert sse_first_frame(s, headers={"Authorization": f"Basic {basic}"})[0] == 200

        # 보관함 — 읽기 목록과 admin 삭제
        for who in (None, "no-such-token", "gone"):
            assert s.req("GET", "/api/shelf", token=who)[0] == 401, who
        for who in ("alice", "admin", "build-02", "paired-w", "paired-c"):
            status, body = s.req("GET", "/api/shelf", token=who)
            assert status == 200 and set(body) == {"items", "storage", "display_timezone"}, who
        worker_refused = "worker tokens cannot use the client API"
        for who, want in (
            (None, (401, "read access requires a token on this server")),
            ("alice", (403, "admin token required")),
            ("paired-c", (403, "admin token required")),
            ("build-02", (403, worker_refused)),
            ("paired-w", (403, worker_refused)),
            ("admin", (404, "not on the shelf")),
        ):
            status, body = s.req("DELETE", "/shelf/1", token=who)
            assert (status, body["error"]) == want, who

        # 릴리스 드라이버의 내부 토큰 — 띄우는 동안 client 토큰, 못 띄우면 곧바로 폐기
        seen: dict = {}
        real_add = s.store.add_token

        def add_and_use(name, **kw):
            secret = real_add(name, **kw)
            seen["secret"] = secret
            seen["while"] = s.req("GET", "/api/whoami", token=secret)
            return secret

        monkeypatch.setattr(s.store, "add_token", add_and_use)
        runner = DriverRunner(s.store, now_fn=s.clock, log=lambda line: None)
        with pytest.raises(OSError):
            runner.spawn(
                data_dir=s.cfg.data_dir,
                repo="app",
                driver=tmp_path / "gone" / "driver.sh",
                checkout=tmp_path / "gone",
                build_name="1.0.1",
                kind=KIND_START,
                started_by="macmini-admin",
                env={},
                server_url="http://127.0.0.1:1",
            )
        name = token_name("app", 1)
        assert seen["while"] == (200, {"name": name, "admin": False, "kind": "client"})
        assert s.req("GET", "/api/whoami", token=seen["secret"])[0] == 401
        row = {t.name: t for t in s.store.list_tokens()}[name]
        assert (row.kind, row.admin, row.revoked_at) == ("client", False, s.clock())
        assert (row.pool, row.pair_id, row.paired_host, row.paired_addr) == (None,) * 4
    finally:
        s.close()


# ── PI46 거절 MAC ───────────────────────────────────────────────────────────


def raw_request(
    ps,
    method: str,
    path: str,
    *,
    token: str | None,
    nonce: str | None,
    body: bytes = b"",
    headers: dict | None = None,
):
    conn = http.client.HTTPConnection("127.0.0.1", ps.port, timeout=15)
    hdrs = dict(headers or {})
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    if nonce is not None:
        hdrs["X-RCM-Pair-Nonce"] = nonce
    if "Transfer-Encoding" not in hdrs:
        hdrs.setdefault("Content-Length", str(len(body)))
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    try:
        doc = json.loads(payload)
    except ValueError:
        doc = None
    return resp.status, dict(resp.getheaders()), doc


def want_reject(ps, pair_id, method, path, status, code, reason, nonce, sent: bytes) -> str:
    k = ref_k(ps.key, pair_id)
    return ref_mac(
        k,
        "rcm-pair-reject-v1",
        method,
        path,
        str(status),
        code,
        reason,
        nonce,
        hashlib.sha256(sent).hexdigest(),
    )


def revoked_paired(ps, name="build-01") -> tuple[str, str]:
    out = ps.paired_by_exchange("worker", name)
    pair_id = cp.parse_pair_secret(out["pair_secret"])[0]
    ps.store.revoke_token(out["name"], T0)
    return out["token"], pair_id


def test_pi46_a_a_revoked_paired_token_gets_a_mac_on_every_401(ps):
    token, pair_id = revoked_paired(ps)
    for method, path, body in (
        ("POST", "/worker/heartbeat", b'{"lane": 1}'),
        ("POST", "/worker/register", b'{"pool": "default"}'),
        ("GET", "/worker/spec", b""),
        ("GET", "/api/whoami", b""),
    ):
        nonce = secrets.token_hex(32)
        status, headers, doc = raw_request(ps, method, path, token=token, nonce=nonce, body=body)
        assert status == 401, (path, doc)
        assert headers.get("WWW-Authenticate", "").startswith(("Bearer", "Basic"))
        assert doc["pair_reject_mac"] == want_reject(
            ps, pair_id, method, path, 401, "", "", nonce, body
        ), path
        k = cp.derive_pair_secret(ps.key, pair_id)
        assert cp.verify_pair_reject(
            k, method, path, 401, doc, nonce, sent_body_sha256=cp.body_sha256(body)
        )
    status, _h, doc = raw_request(
        ps, "POST", "/worker/heartbeat", token=token, nonce=None, body=b"{}"
    )
    assert doc == {"error": "a valid worker token is required"}  # 문장은 오늘 그대로


def test_pi46_b_c_d_f_no_mac_for_rows_without_a_pair_id_or_bad_nonces(ps):
    first = ps.paired_by_exchange("worker", "build-01")
    ps.paired_by_exchange("worker", "else", name=first["name"], replace=True)
    manual = ps.store.add_token("manual-w", now=T0, kind="worker")
    ps.store.revoke_token("manual-w", T0)
    revoked, _ = revoked_paired(ps, "build-05")
    nonce = secrets.token_hex(32)
    for token, n in (
        (first["token"], nonce),  # (b) --replace 로 바뀐 옛 토큰 — 행이 없다
        (manual, nonce),  # (c) pair_id 없는 폐기된 옛 토큰
        ("no-such-token", nonce),  # (d)
        (revoked, nonce[:63]),  # (f) 63자
        (revoked, nonce.upper()),  # (f) 대문자
        (revoked, None),  # (f) 헤더 없음
    ):
        status, _h, doc = raw_request(
            ps, "POST", "/worker/heartbeat", token=token, nonce=n, body=b"{}"
        )
        assert status == 401 and "pair_reject_mac" not in doc, (token[:6], n)


def test_pi46_g_success_answers_carry_no_mac(ps):
    out = ps.paired_by_exchange("worker", "build-01")
    nonce = secrets.token_hex(32)
    status, _h, doc = raw_request(ps, "GET", "/worker/spec", token=out["token"], nonce=nonce)
    assert status == 200 and "pair_reject_mac" not in doc


def test_pi46_h_fenced_and_protocol_409s_of_a_paired_token_carry_a_mac(ps):
    out = ps.paired_by_exchange("worker", "build-01")
    name, pair_id = out["name"], cp.parse_pair_secret(out["pair_secret"])[0]
    ps.tokens[name] = out["token"]
    ps.worker_names.add(name)
    # protocol_required — 세션 없는 heartbeat
    nonce = secrets.token_hex(32)
    body = b'{"lane": 1}'
    status, _h, doc = raw_request(
        ps, "POST", "/worker/heartbeat", token=out["token"], nonce=nonce, body=body
    )
    assert (status, doc["code"]) == (409, "protocol_required"), doc
    assert doc["pair_reject_mac"] == want_reject(
        ps, pair_id, "POST", "/worker/heartbeat", 409, "protocol_required", "", nonce, body
    )
    # session_fenced — 같은 이름의 새 세션이 옛 세션을 펜싱한다
    ps.registered(name)
    old = ps.new_process(name)
    ps.registered(name)
    nonce = secrets.token_hex(32)
    body = json.dumps({"lane": 1, "worker_session_id": old, "seq": 1, "claims_settled": 0}).encode()
    status, _h, doc = raw_request(
        ps, "POST", "/worker/heartbeat", token=out["token"], nonce=nonce, body=body
    )
    assert (status, doc["code"]) == (409, "session_fenced"), doc
    assert doc["pair_reject_mac"] == want_reject(
        ps, pair_id, "POST", "/worker/heartbeat", 409, "session_fenced", "", nonce, body
    )
    # 짝짓지 않은 토큰의 같은 409 에는 MAC 이 없다
    status, _h, doc = raw_request(
        ps,
        "POST",
        "/worker/heartbeat",
        token=ps.tokens["build-03"],
        nonce=nonce,
        body=b'{"lane": 1}',
    )
    assert doc["code"] == "protocol_required" and "pair_reject_mac" not in doc


def test_pi46_i_undrained_chunked_or_encoded_bodies_get_no_mac(ps):
    token, _pair_id = revoked_paired(ps)
    nonce = secrets.token_hex(32)
    # 머리가 상한보다 큰 본문을 말한다 — 서버는 읽지 않고 답한 뒤 연결을 닫는다
    status, _h, doc = raw_request(
        ps,
        "POST",
        "/worker/heartbeat",
        token=token,
        nonce=nonce,
        body=b"{}",
        headers={"Content-Length": str(MAX_DRAIN_BODY + 1)},
    )
    assert status == 401 and "pair_reject_mac" not in doc
    status, _h, doc = raw_request(
        ps,
        "POST",
        "/worker/heartbeat",
        token=token,
        nonce=nonce,
        body=b"2\r\n{}\r\n0\r\n\r\n",
        headers={"Transfer-Encoding": "chunked"},
    )
    assert status == 401 and "pair_reject_mac" not in doc
    status, _h, doc = raw_request(
        ps,
        "POST",
        "/worker/heartbeat",
        token=token,
        nonce=nonce,
        body=gzip.compress(b"{}"),
        headers={"Content-Encoding": "gzip"},
    )
    assert status == 401 and "pair_reject_mac" not in doc


def test_pi46_j_the_raw_path_with_percent_encoding_and_a_trailing_slash_is_bound(ps):
    token, pair_id = revoked_paired(ps)
    nonce = secrets.token_hex(32)
    path = "/worker/heartbeat%2F/"
    status, _h, doc = raw_request(ps, "POST", path, token=token, nonce=nonce, body=b"{}")
    assert status == 401
    assert doc["pair_reject_mac"] == want_reject(
        ps, pair_id, "POST", path, 401, "", "", nonce, b"{}"
    )


def fake_handler(
    ps, *, method: str, path: str, token: str | None, nonce: str | None, body: bytes
) -> types.SimpleNamespace:
    """`_send_error` 하나를 소켓 없이 부른다.

    PR2 · M5t-b 가 낼 403 · 409 를 바로 넣는다(PI46 e)."""
    import email.message
    import io

    h = server_mod.Handler.__new__(server_mod.Handler)
    msg = email.message.Message()
    if token:
        msg["Authorization"] = f"Bearer {token}"
    if nonce:
        msg["X-RCM-Pair-Nonce"] = nonce
    msg["Content-Length"] = str(len(body))
    h.headers = msg
    h.command, h.path = method, path
    h.request_version, h.requestline = "HTTP/1.1", f"{method} {path} HTTP/1.1"
    h.client_address = ("127.0.0.1", 1)
    h.close_connection = False
    h.app = ps.app
    h.server = ps.httpd
    h.wfile = io.BytesIO()
    h.body = server_mod._BodyReader(io.BytesIO(body), len(body))
    return h


def sent_error(ps, err: ApiError, **kw) -> dict:
    h = fake_handler(ps, **kw)
    h._send_error(err)
    raw = h.wfile.getvalue()
    return json.loads(raw.split(b"\r\n\r\n", 1)[1])


def test_pi46_e_register_rejects_get_a_mac_only_for_the_listed_codes(ps):
    out = ps.paired_by_exchange("worker", "build-01")
    token, pair_id = out["token"], cp.parse_pair_secret(out["pair_secret"])[0]
    body = b'{"pool": "default", "pair_proof": {}}'
    cases = [
        (ApiError(403, "x", code="pair_proof_required"), 403, "pair_proof_required", "", True),
        (
            ApiError(403, "x", code="pair_proof_invalid", reason="bad_mac"),
            403,
            "pair_proof_invalid",
            "bad_mac",
            True,
        ),
        (
            ApiError(409, "x", code="pool_mismatch", bound_pool="gpu", reason="gpu"),
            409,
            "pool_mismatch",
            "gpu",
            True,
        ),
        (ApiError(403, "x", code="pair_proof_invalid", reason="expired"), 403, "", "", False),
        (ApiError(403, "x", code="pair_proof_invalid", reason="reused"), 403, "", "", False),
        (ApiError(409, "x", code="name_taken"), 409, "", "", False),
        (ApiError(400, "x", code="bad"), 400, "", "", False),
        (ApiError(500, "internal error"), 500, "", "", False),
    ]
    for err, status, code, reason, wants in cases:
        nonce = secrets.token_hex(32)
        doc = sent_error(
            ps, err, method="POST", path="/worker/register", token=token, nonce=nonce, body=body
        )
        if wants:
            assert doc["pair_reject_mac"] == want_reject(
                ps, pair_id, "POST", "/worker/register", status, code, reason, nonce, body
            ), err.extra
        else:
            assert "pair_reject_mac" not in doc, err.extra
    # 짝짓지 않은 토큰 · nonce 없음
    doc = sent_error(
        ps,
        ApiError(403, "x", code="pair_proof_required"),
        method="POST",
        path="/worker/register",
        token=ps.tokens["build-02"],
        nonce=secrets.token_hex(32),
        body=body,
    )
    assert "pair_reject_mac" not in doc
    doc = sent_error(
        ps,
        ApiError(403, "x", code="pair_proof_required"),
        method="POST",
        path="/worker/register",
        token=token,
        nonce=None,
        body=body,
    )
    assert "pair_reject_mac" not in doc


def test_pi46_k_g4_a_reject_mac_that_cannot_be_computed_is_logged_by_exception_name(
    ps, monkeypatch
):
    """G4: 거절 MAC 계산이 예외(DB busy 등)로 실패하면 거절은 MAC 없이 그대로 나가고, 서버
    로그에 예외 이름만 실은 한 줄이 남는다 — 토큰 · nonce · 예외 문장(값이 섞일 수 있다)은
    싣지 않는다."""
    out = ps.paired_by_exchange("worker", "build-01")
    token = out["token"]
    nonce = secrets.token_hex(32)

    def busy(**kw):
        raise sqlite3.OperationalError(f"database is locked ({token})")

    monkeypatch.setattr(ps.app, "pair_reject_mac_for", busy)
    ps.logs.clear()
    doc = sent_error(
        ps,
        ApiError(401, "a valid worker token is required"),
        method="POST",
        path="/worker/heartbeat",
        token=token,
        nonce=nonce,
        body=b"{}",
    )
    assert doc == {"error": "a valid worker token is required"}
    assert ps.logs == ["pair: no pair_reject_mac on a 401 answer (OperationalError)"]
    for value in (token, nonce, "database is locked"):
        assert value not in "\n".join(ps.logs)


# ── 등록 증명 검사(check_register_proof — PR2 가 부른다) ───────────────────


def test_check_register_proof_follows_the_313_order_and_burns_only_passing_challenges(ps):
    out = ps.paired_by_exchange("worker", "build-01")
    pair_id, k = cp.parse_pair_secret(out["pair_secret"])
    req, (status, doc) = ps.hello(pair_id)
    c = doc["challenge"]
    sid = secrets.token_hex(16)
    now = ps.clock()
    good = cp.register_proof_mac(k, pair_id, c, sid)
    assert ps.app.check_register_proof(pair_id, c, "z" * 64, sid, now) == "bad_mac"
    assert ps.app.check_register_proof(pair_id, c, good.upper(), sid, now) == "bad_mac"
    wrong = cp.register_proof_mac(k, pair_id, c, secrets.token_hex(16))
    assert ps.app.check_register_proof(pair_id, c, wrong, sid, now) == "bad_mac"
    # 틀린 MAC 은 챌린지를 태우지 않는다(V2-6)
    assert ps.app.check_register_proof(pair_id, c, good, sid, now) is None
    assert ps.app.check_register_proof(pair_id, c, good, sid, now) == "reused"
    other = fresh_pair_id()
    assert ps.app.check_register_proof(other, c, good, sid, now) == "bad_mac"
    later = datetime.fromtimestamp(cp.challenge_time(c) + 121, UTC)
    _, (_s, d2) = ps.hello(pair_id)
    good2 = cp.register_proof_mac(k, pair_id, d2["challenge"], sid)
    assert ps.app.check_register_proof(pair_id, d2["challenge"], good2, sid, later) == "expired"
