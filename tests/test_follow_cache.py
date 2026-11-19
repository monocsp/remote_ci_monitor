"""따라가기(M5q PR A)의 기록 · 받기 · 풀기 · 캐시 청소 — 실제 HTTP 와 실제 파일로.

`server_info` · `load_record` · `save_record` · `fetch` · `scan_cache` · `prune` ·
`check_cache_safe` 를 `FakeServer` · `VersionedServer` 에 대고 돌린다. 시각은 `now=` 로 넣고
sleep 은 시간 제한이 주제인 시험에서만 쓴다. 명세는 `docs/m5q-workplan.md` §2.4 · §2.5 ·
§2.8 · §14.4~§14.6 이다.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import struct
import threading
import time
import zipfile
from pathlib import Path

import pytest

from follow_helpers import (
    INSTALLED,
    NEW,
    OLD,
    SHA_A,
    SHA_B,
    FakeServer,
    M,
    SilentListener,
    T,
    VersionedServer,
    entries,
    fake_wheel,
    leftovers,
    raw_get,
    sha,
)
from remote_ci_monitor import follow

REG = (stat.S_IFREG | 0o644) << 16


@pytest.fixture
def root(tmp_path) -> Path:
    return tmp_path / "cache" / "rcm"


@pytest.fixture
def servers():
    made: list = []

    def make(*a, **k):
        s = FakeServer(*a, **k)
        made.append(s)
        return s

    yield make
    for s in made:
        if s.client_hold is not None:
            s.client_hold.set()
        s.close()


def header(seen, name: str) -> str | None:
    for k, v in seen.headers.items():
        if k.lower() == name.lower():
            return v
    return None


def info_of(srv) -> follow.ServerInfo:
    return follow.parse_health(srv.key, srv.health, now=T)


def do_fetch(srv, root: Path, token: str | None = None, inf=None) -> follow.CacheEntry:
    follow.check_cache_safe(root)
    return follow.fetch(srv.key, token, inf or info_of(srv), root=root)


def fetch_fails(srv, root: Path, code: str | tuple[str, ...], token=None, inf=None):
    with pytest.raises(follow.FollowError) as e:
        do_fetch(srv, root, token, inf)
    codes = (code,) if isinstance(code, str) else code
    assert e.value.code in codes, (e.value.code, e.value.detail)
    clients = root / "clients"
    assert entries(clients) == [], "a failed fetch must not leave a target folder"
    assert leftovers(clients) == [], "a failed fetch must not leave .tmp-*"
    return e.value


def make_entry(root: Path, version: str, digest: str, mtime: float, *, name=None) -> Path:
    """손으로 만든 캐시 항목 — 표지와 mtime 만 있으면 된다."""
    clients = root / "clients"
    clients.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    clients.chmod(0o700)
    d = clients / (name or digest[:16])
    (d / "remote_ci_monitor").mkdir(parents=True)
    d.chmod(0o700)
    marker = d / ".rcm-client.json"
    marker.write_text(
        json.dumps(
            {"version": version, "sha256": digest, "server": "http://x:1", "fetched_at": mtime}
        )
    )
    os.utime(marker, (mtime, mtime))
    return d


def digest_of(i: int) -> str:
    return hashlib.sha256(str(i).encode()).hexdigest()


# ── 기록 ─────────────────────────────────────────────────────────────────────


def test_record_is_fresh_for_ten_minutes(servers, root):  # A1 · A27 · A86 · 결정 153
    srv = servers(NEW)
    got = follow.server_info(srv.key, None, root=root, now=T, ttl=follow.CHECK_TTL_SECONDS)
    assert got.version == NEW and srv.health_count == 1
    got = follow.server_info(srv.key, None, root=root, now=T + 9 * M, ttl=600)
    assert got.version == NEW and srv.health_count == 1
    follow.server_info(srv.key, None, root=root, now=T + 599, ttl=600)
    assert srv.health_count == 1
    follow.server_info(srv.key, None, root=root, now=T + 600, ttl=600)
    assert srv.health_count == 2
    doc = json.loads((root / "follow.json").read_text())
    assert list(doc) == [srv.key]
    rec = doc[srv.key]
    assert rec["version"] == NEW and rec["failed_at"] is None and rec["wheel_error"] is None
    assert rec["checked_at"] == T + 600
    assert rec["wheel"] == {
        "path": srv.health["client_wheel"]["path"],
        "sha256": srv.health["client_wheel"]["sha256"],
        "bytes": srv.health["client_wheel"]["bytes"],
    }
    assert {p.name for p in root.iterdir()} <= {"follow.json", "clients"}


def test_ttl_zero_always_asks(servers, root):  # A16 · A47 · §14 A질문13
    srv = servers(NEW)
    follow.server_info(srv.key, None, root=root, now=T, ttl=600)
    follow.server_info(srv.key, None, root=root, now=T + 1, ttl=0)
    assert srv.health_count == 2


def test_future_record_is_not_fresh(servers, root):  # A131 · §14 A질문21
    srv = servers(NEW)
    follow.server_info(srv.key, None, root=root, now=T + 3600, ttl=600)
    follow.server_info(srv.key, None, root=root, now=T, ttl=600)
    assert srv.health_count == 2


def test_failure_is_remembered_for_sixty_seconds(servers, root):  # A81 · A86 · R12
    srv = servers(health=b"", health_status=500)
    assert follow.server_info(srv.key, None, root=root, now=T, ttl=600) is None
    assert srv.health_count == 1
    for now in (T + 30, T + 59):
        assert follow.server_info(srv.key, None, root=root, now=now, ttl=600) is None
    assert srv.health_count == 1
    assert follow.server_info(srv.key, None, root=root, now=T + 60, ttl=600) is None
    assert srv.health_count == 2
    _, failed = follow.load_record(root, srv.key)
    assert failed == T + 60
    # fresh(ttl=0) 는 실패 기억도 무시한다
    follow.server_info(srv.key, None, root=root, now=T + 61, ttl=0)
    assert srv.health_count == 3


def test_failure_keeps_the_last_version(servers, root):  # A36 · A91 · §14 A질문22
    srv = servers(health=b"", health_status=500)
    rec = follow.ServerInfo(
        srv.key,
        NEW,
        "/client/remote_ci_monitor-99.0.1-py3-none-any.whl",
        SHA_A,
        100,
        None,
        T,
    )
    follow.save_record(root, rec, server=srv.key, failed_at=None)
    assert follow.server_info(srv.key, None, root=root, now=T + 11 * M, ttl=600) is None
    got, failed = follow.load_record(root, srv.key)
    assert got is not None and got.version == NEW and got.sha256 == SHA_A
    assert got.checked_at == T and failed == T + 11 * M


def test_record_round_trip_and_missing(root):
    assert follow.load_record(root, "http://h:1") == (None, None)
    follow.save_record(root, None, server="http://h:1", failed_at=T)
    assert follow.load_record(root, "http://h:1") == (None, T)


def test_503_with_a_body_is_used_500_is_not(servers, root):  # A37 · A38 · F16 · F17
    ok = servers(NEW, health_status=503)
    assert follow.server_info(ok.key, None, root=root, now=T, ttl=600).version == NEW
    dead = servers(health=b"", health_status=500)
    assert follow.server_info(dead.key, None, root=root, now=T, ttl=600) is None


def test_real_server_health(tmp_path, root):  # A22 · CB7
    w = fake_wheel(NEW)
    srv = VersionedServer(tmp_path, NEW, w)
    try:
        got = follow.server_info(srv.key, None, root=root, now=T, ttl=600)
        assert got.version == NEW and got.sha256 == sha(w) and got.bytes == len(w)
        follow.check_cache_safe(root)
        entry = follow.fetch(srv.key, None, got, root=root)
        assert entry.version == NEW and srv.client_count == 1
    finally:
        srv.close()


def test_malformed_health_is_not_recorded(servers, root):  # A51 · §14 A질문7
    srv = servers(NEW)
    srv.health = {**srv.health, "version": "../x"}
    with pytest.raises(follow.FollowError) as e:
        follow.server_info(srv.key, None, root=root, now=T, ttl=600)
    assert e.value.code == "bad_health"
    assert follow.load_record(root, srv.key) == (None, None)
    # 실패 기억에도 들지 않는다 — 다음 호출은 다시 묻는다
    with pytest.raises(follow.FollowError):
        follow.server_info(srv.key, None, root=root, now=T + 1, ttl=600)
    assert srv.health_count == 2


def test_html_health_is_bad_health(servers, root):  # A103
    srv = servers(health=b"<html>hi</html>")
    with pytest.raises(follow.FollowError) as e:
        follow.server_info(srv.key, None, root=root, now=T, ttl=600)
    assert e.value.code == "bad_health"


@pytest.mark.parametrize("content", [b"{", b"[]", b'{"http://h:1": 7}', None])
def test_broken_record_file_is_ignored(servers, root, content):  # A111 · §14 A질문22
    srv = servers(NEW)
    root.mkdir(parents=True)
    path = root / "follow.json"
    path.write_bytes(content if content is not None else b"{}")
    if content is None:
        path.chmod(0)
    try:
        got = follow.server_info(srv.key, None, root=root, now=T, ttl=600)
        assert got.version == NEW and srv.health_count == 1
    finally:
        path.chmod(0o600)


def test_unwritable_record_is_silent(servers, root):  # A111
    srv = servers(NEW)
    root.mkdir(parents=True)
    root.chmod(0o500)
    try:
        got = follow.server_info(srv.key, None, root=root, now=T, ttl=600)
        assert got.version == NEW
    finally:
        root.chmod(0o700)


def test_concurrent_record_writes_stay_json(root):  # A137
    root.mkdir(parents=True)

    def save(i):
        rec = follow.ServerInfo(
            f"http://h{i}:1",
            NEW,
            "/client/remote_ci_monitor-99.0.1-py3-none-any.whl",
            SHA_A,
            100,
            None,
            T,
        )
        for _ in range(5):
            follow.save_record(root, rec, server=f"http://h{i}:1", failed_at=None)

    threads = [threading.Thread(target=save, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    doc = json.loads((root / "follow.json").read_text())
    assert isinstance(doc, dict) and doc
    assert {p.name for p in root.iterdir()} == {"follow.json"}


# ── 리다이렉트 · 토큰 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_health_redirect_is_unreachable(servers, root, status):  # A52 · R ③
    other = servers(NEW)
    srv = servers(NEW, redirect=(status, other.url + "/api/health"))
    assert follow.server_info(srv.key, "tok", root=root, now=T, ttl=600) is None
    assert other.requests == []


def test_client_redirect_is_unreachable(servers, root):  # A52 · R ③
    other = servers(NEW)
    srv = servers(NEW, client_redirect=(302, other.url + other.wheel_path))
    fetch_fails(srv, root, "unreachable", token="tok")
    assert other.requests == []


def test_token_is_always_bearer(servers, root):  # A85 · F52 · §2.4
    srv = servers(NEW)
    got = follow.server_info(srv.key, "t", root=root, now=T, ttl=600)
    follow.check_cache_safe(root)
    follow.fetch(srv.key, "t", got, root=root)
    assert [header(r, "Authorization") for r in srv.requests] == ["Bearer t", "Bearer t"]
    assert all(header(r, "User-Agent") == f"rcm/{INSTALLED}" for r in srv.requests)


def test_no_token_no_header(servers, root):
    srv = servers(NEW)
    do_fetch(srv, root)
    assert all(header(r, "Authorization") is None for r in srv.requests)


@pytest.fixture
def basic_server(tmp_path):
    w = fake_wheel(NEW)
    srv = VersionedServer(tmp_path, NEW, w, read_auth="basic")
    yield srv
    srv.close()


def basic_info(srv):
    status, body = raw_get(srv.url + "/api/health")
    return follow.parse_health(srv.key, json.loads(body), now=T)


def test_basic_server_with_a_token(basic_server, root):  # A44 · F23
    entry = do_fetch(basic_server, root, basic_server.tokens["alice"], basic_info(basic_server))
    assert entry.version == NEW


def test_basic_server_without_a_token(basic_server, root):  # A45 · F24 · §14 A질문6
    e = fetch_fails(basic_server, root, "unauthorized", None, basic_info(basic_server))
    assert follow.reason_text(e.code, e.detail) == "the server needs a token to hand out its client"


def test_basic_server_with_a_wrong_token(basic_server, root):  # A105 · §14 A질문6
    e = fetch_fails(basic_server, root, "unauthorized", "nope", basic_info(basic_server))
    assert follow.reason_text(e.code, e.detail) == (
        "the server refused the token for its client (HTTP 401)"
    )


@pytest.mark.parametrize(
    "status, code, detail",
    [(404, "no_wheel", "HTTP 404"), (503, "no_wheel", "HTTP 503"), (403, "unauthorized", None)],
)
def test_client_statuses(servers, root, status, code, detail):  # A104 · §14 A질문6
    srv = servers(NEW, client_status=status)
    e = fetch_fails(srv, root, code, token="t")
    if detail:
        assert e.detail == detail
    else:
        assert "403" in follow.reason_text(e.code, e.detail)


def test_real_server_404_after_an_upgrade(tmp_path, root):  # A104 — 그 사이 서버가 올라갔다
    srv = VersionedServer(tmp_path, NEW, fake_wheel(NEW))
    try:
        stale = follow.ServerInfo(
            srv.key, OLD, "/client/remote_ci_monitor-0.0.9-py3-none-any.whl", SHA_A, 10, None, T
        )
        follow.check_cache_safe(root)
        with pytest.raises(follow.FollowError) as e:
            follow.fetch(srv.key, None, stale, root=root)
        assert (e.value.code, e.value.detail) == ("no_wheel", "HTTP 404")
    finally:
        srv.close()


# ── 받기와 풀기 ──────────────────────────────────────────────────────────────


def test_fetch_layout(servers, root):  # A26 · A2 · 결정 151 · 164 · §14 A질문15
    srv = servers(NEW)
    entry = do_fetch(srv, root)
    digest = sha(srv.wheel)
    folder = root / "clients" / digest[:16]
    assert entry.path == folder and entry.version == NEW and entry.sha256 == digest
    assert (folder / "remote_ci_monitor" / "__init__.py").is_file()
    assert (folder / f"remote_ci_monitor-{NEW}.dist-info" / "METADATA").is_file()
    marker = json.loads((folder / ".rcm-client.json").read_text())
    assert set(marker) == {"version", "sha256", "server", "fetched_at"}
    assert marker["version"] == NEW and marker["sha256"] == digest and marker["server"] == srv.key
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "clients").stat().st_mode) == 0o700
    assert leftovers(root / "clients") == []
    assert not [p for p in (root / "clients").rglob("*.whl")]
    assert srv.client_count == 1
    scanned = follow.scan_cache(root)
    assert [(e.version, e.sha256, e.path) for e in scanned] == [(NEW, digest, folder)]


def test_fetch_keeps_executable_bits(servers, root):  # §14 A질문17 실행 비트
    exe = (stat.S_IFREG | 0o755) << 16
    w = fake_wheel(NEW, extra=(("remote_ci_monitor/tool.sh", b"#!/bin/sh\n", exe),))
    srv = servers(NEW, w)
    entry = do_fetch(srv, root)
    tool = entry.path / "remote_ci_monitor" / "tool.sh"
    assert tool.stat().st_mode & stat.S_IXUSR
    assert not (entry.path / "remote_ci_monitor" / "cli.py").stat().st_mode & stat.S_IXUSR


def test_sha_mismatch_leaves_nothing(servers, root):  # A41 · F20 · 결정 157
    srv = servers(NEW)
    srv.health = {**srv.health, "client_wheel": {**srv.health["client_wheel"], "sha256": SHA_A}}
    fetch_fails(srv, root, "sha_mismatch")
    assert srv.client_count == 1


def test_longer_than_declared(servers, root):  # A42 · F21
    srv = servers(NEW, extra_bytes=1000)
    fetch_fails(srv, root, ("sha_mismatch", "unpack"))


def test_disk_full_while_unpacking(servers, root, monkeypatch):  # A43 · F22
    srv = servers(NEW)

    def full(self, *a, **k):
        raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))

    monkeypatch.setattr(zipfile.ZipExtFile, "read", full)
    e = fetch_fails(srv, root, "unpack")
    assert follow.reason_text(e.code, e.detail) == "cannot unpack: No space left on device"


@pytest.mark.parametrize("req", [">=3.99", "~=3.11", ">=3.11,<4"])
def test_requires_python(servers, root, req):  # A46 · A47 · F25 · F26
    srv = servers(NEW, fake_wheel(NEW, requires_python=req))
    e = fetch_fails(srv, root, "python")
    if req == ">=3.99":
        text = follow.reason_text(e.code, e.detail)
        assert text == f"needs Python >=3.99, this is {platform.python_version()}"


def test_requires_python_absent_or_spaced(servers, root):  # A24 · §14 A질문17
    assert do_fetch(servers(NEW, fake_wheel(NEW, requires_python=None)), root).version == NEW
    srv = servers(NEW, fake_wheel(NEW, requires_python=">= 3.11"))
    assert do_fetch(srv, root).version == NEW


def test_two_requires_python_lines(servers, root):  # §14 A질문17
    w = fake_wheel(NEW, requires_python=">=3.11\nRequires-Python: >=3.11")
    fetch_fails(servers(NEW, w), root, "python")


def test_inner_version_differs(servers, root):  # A50 · F29 · 결정 161
    srv = servers(NEW, fake_wheel(NEW, init_version=INSTALLED))
    e = fetch_fails(srv, root, "version_mismatch")
    assert follow.reason_text(e.code, e.detail) == f"the wheel says v{INSTALLED}"


def test_missing_init_and_quotes(servers, root):  # A108 · §14 A질문17
    fetch_fails(servers(NEW, fake_wheel(NEW, drop=("__init__.py",))), root, "unpack")
    fetch_fails(servers(NEW, fake_wheel(NEW, init_text="x = 1\n")), root, "version_mismatch")
    single = fake_wheel(NEW, init_text=f"__version__ = '{NEW}'\nSCHEMA_VERSION = 1\n")
    assert do_fetch(servers(NEW, single), root).version == NEW


LINK = (stat.S_IFLNK | 0o777) << 16


@pytest.mark.parametrize(
    "name, data, attr",
    [
        ("../evil.py", b"x", REG),
        ("/tmp/rcm-follow-evil.py", b"x", REG),
        ("remote_ci_monitor/evil-link", b"/etc/passwd", LINK),
        ("other_pkg/x.py", b"x", REG),
        ("remote_ci_monitor\\x.py", b"x", REG),
        ("remote_ci_monitor/../../evil.py", b"x", REG),
        ("remote_ci_monitorX/evil.py", b"x", REG),  # A59
        ("remote_ci_monitor-9.9.9.dist-info/METADATA", b"x", REG),  # A59
        ("Remote_CI_Monitor/x.py", b"x", REG),  # A59
        ("remote_ci_monitor/./x.py", b"x", REG),  # A107
        ("remote_ci_monitor//x.py", b"x", REG),  # A107
        ("remote_ci_monitor/cli.py", b"dup", REG),  # A107 같은 이름 두 번
        ("remote_ci_monitor/fifo", b"", (stat.S_IFIFO | 0o644) << 16),  # A107
        ("remote_ci_monitor/dev", b"", (stat.S_IFCHR | 0o644) << 16),  # A107
    ],
)
def test_bad_entries_are_refused(servers, root, tmp_path, name, data, attr):  # A49 · A59 · A107
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile 의 「Duplicate name」 경고
        w = fake_wheel(NEW, extra=((name, data, attr),))
    before = set(tmp_path.parent.rglob("*evil*"))
    fetch_fails(servers(NEW, w), root, "unpack")
    assert set(tmp_path.parent.rglob("*evil*")) == before
    assert not Path("/tmp/rcm-follow-evil.py").exists()


def test_unpacked_size_limit(servers, root, monkeypatch):  # A90 · 결정 165
    w = fake_wheel(NEW)
    total = sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(w)).infolist())
    monkeypatch.setattr(follow, "MAX_UNPACKED_BYTES", total - 1)
    fetch_fails(servers(NEW, w), root, "unpack")
    monkeypatch.setattr(follow, "MAX_UNPACKED_BYTES", total)
    assert do_fetch(servers(NEW, w), root).version == NEW


def _member_data_offset(data: bytes, name: str) -> tuple[int, zipfile.ZipInfo]:
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo(name)
    off = info.header_offset
    n_len, e_len = struct.unpack("<HH", data[off + 26 : off + 30])
    return off + 30 + n_len + e_len, info


def corrupt(w: bytes) -> bytes:
    """`cli.py` 의 압축 데이터 한가운데 한 바이트를 뒤집는다 — CRC 나 zlib 오류."""
    start, info = _member_data_offset(w, "remote_ci_monitor/cli.py")
    pos = start + info.compress_size // 2
    return w[:pos] + bytes([w[pos] ^ 0xFF]) + w[pos + 1 :]


def unknown_method(w: bytes) -> bytes:
    """`__init__.py` 의 압축 방식을 모르는 값(99)으로 — 로컬 헤더와 중앙 디렉터리 둘 다."""
    buf = bytearray(w)
    zf = zipfile.ZipFile(io.BytesIO(w))
    info = zf.getinfo("remote_ci_monitor/__init__.py")
    struct.pack_into("<H", buf, info.header_offset + 8, 99)
    pos = zf.start_dir  # 중앙 디렉터리 시작
    while True:
        pos = w.index(b"PK\x01\x02", pos)
        n_len = struct.unpack("<H", w[pos + 28 : pos + 30])[0]
        if w[pos + 46 : pos + 46 + n_len] == b"remote_ci_monitor/__init__.py":
            struct.pack_into("<H", buf, pos + 10, 99)
            break
        pos += 4
    return bytes(buf)


@pytest.mark.parametrize("make", [lambda w: b"not a zip at all", corrupt, unknown_method])
def test_broken_zips_are_unpack(servers, root, make):  # A106
    data = make(fake_wheel(NEW))
    fetch_fails(servers(NEW, data), root, "unpack")


def test_interrupt_while_downloading(servers, root, monkeypatch):  # A53 · R ④ · 결정 157
    # 받는 중의 Ctrl-C 를 결정적으로 넣는다 — 실제 신호는 test_follow_exec 의 하위 프로세스가 본다.
    # 이 프로세스에 SIGINT 를 쏘면 막힌 호출 밖에 떨어질 때 pytest 세션 전체가 멈춘다
    srv = servers(NEW)
    inf = info_of(srv)
    follow.check_cache_safe(root)
    seen: list[int] = []

    class Boom:
        def update(self, chunk: bytes) -> None:
            seen.append(len(chunk))
            raise KeyboardInterrupt

    monkeypatch.setattr(follow.hashlib, "sha256", lambda *a: Boom())
    with pytest.raises(KeyboardInterrupt):
        follow.fetch(srv.key, None, inf, root=root)
    assert seen and srv.client_count == 1  # 정말로 받는 중이었다
    assert entries(root / "clients") == [] and leftovers(root / "clients") == []


def test_health_three_second_cap(root):  # A60 · 결정 153
    lis = SilentListener()
    try:
        started = time.monotonic()
        got = follow.server_info(lis.url, None, root=root, now=T, ttl=600)
        took = time.monotonic() - started
    finally:
        lis.close()
    assert got is None
    assert 2.5 <= took < 5.5, took


def test_health_total_cap_on_a_dripping_server(servers, root):  # A118 · §14 A질문18
    srv = servers(NEW, drip=2.0)
    started = time.monotonic()
    got = follow.server_info(srv.key, None, root=root, now=T, ttl=600)
    took = time.monotonic() - started
    assert got is None
    assert took < 6.0, took


def test_two_fetches_race_for_one_folder(servers, root, monkeypatch):  # A61 · A88 · F30
    srv = servers(NEW, client_barrier=threading.Barrier(2))
    for i in range(5):
        make_entry(root, OLD, digest_of(i), T + i)
    calls: list = []
    real_prune = follow.prune

    def counting(*a, **k):
        calls.append(a)
        return real_prune(*a, **k)

    monkeypatch.setattr(follow, "prune", counting)
    inf = info_of(srv)
    follow.check_cache_safe(root)
    out: list = []
    errors: list = []

    def go():
        try:
            out.append(follow.fetch(srv.key, None, inf, root=root))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == [] and len(out) == 2
    assert out[0].path == out[1].path
    assert srv.client_count == 2
    folders = [p for p in entries(root / "clients") if (p / "remote_ci_monitor").is_dir()]
    assert sum(1 for p in folders if p.name == sha(srv.wheel)[:16]) == 1
    assert leftovers(root / "clients") == []
    assert len(calls) == 1  # 청소는 rename 에 이긴 쪽만


def test_two_servers_two_folders(servers, root):  # A64 · A65 · F33 · F34
    old, new = servers(OLD), servers(NEW)
    variant = servers(NEW, fake_wheel(NEW, extra=(("remote_ci_monitor/_which.txt", b"b", REG),)))
    for s in (old, new, variant):
        do_fetch(s, root)
        assert follow.server_info(s.key, None, root=root, now=T, ttl=600).version == s.version
    names = sorted(p.name for p in entries(root / "clients"))
    assert names == sorted(sha(s.wheel)[:16] for s in (old, new, variant))
    doc = json.loads((root / "follow.json").read_text())
    assert set(doc) == {old.key, new.key, variant.key}


def test_same_prefix_other_sha_is_refused(servers, root):  # A124 · §14 A질문15
    srv = servers(NEW)
    digest = sha(srv.wheel)
    other = digest[:16] + ("0" if digest[16] != "0" else "1") + digest[17:]
    make_entry(root, NEW, other, T, name=digest[:16])
    with pytest.raises(follow.FollowError) as e:
        do_fetch(srv, root)
    assert e.value.code == "unpack" and "holds another client" in e.value.detail
    assert leftovers(root / "clients") == []


def test_target_without_a_marker(servers, root):  # A125
    srv = servers(NEW)
    clients = root / "clients"
    clients.mkdir(parents=True)
    root.chmod(0o700)
    clients.chmod(0o700)
    target = clients / sha(srv.wheel)[:16]
    (target / "junk").mkdir(parents=True)
    with pytest.raises(follow.FollowError) as e:
        do_fetch(srv, root)
    assert e.value.code == "unpack"
    shutil.rmtree(target)
    target.mkdir()  # 빈 폴더면 rename 이 덮는다
    assert do_fetch(srv, root).path == target


# ── 캐시 권한 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o720])
def test_clients_writable_by_others(root, mode):  # A48 · F27 · 결정 164
    follow.check_cache_safe(root)
    (root / "clients").chmod(mode)
    try:
        with pytest.raises(follow.FollowError) as e:
            follow.check_cache_safe(root)
        assert e.value.code == "cache_unsafe" and str(root / "clients") in e.value.detail
    finally:
        (root / "clients").chmod(0o700)


def test_root_writable_by_others(root):  # A48
    follow.check_cache_safe(root)
    root.chmod(0o777)
    try:
        with pytest.raises(follow.FollowError) as e:
            follow.check_cache_safe(root)
        assert e.value.code == "cache_unsafe" and str(root) in e.value.detail
    finally:
        root.chmod(0o700)


def test_owned_by_someone_else(root, monkeypatch):  # A48
    follow.check_cache_safe(root)
    real = os.geteuid()
    monkeypatch.setattr(follow.os, "geteuid", lambda: real + 1)
    with pytest.raises(follow.FollowError) as e:
        follow.check_cache_safe(root)
    assert e.value.code == "cache_unsafe" and str(root) in e.value.detail


def test_symlink_to_a_foreign_folder(tmp_path, monkeypatch):  # A55 · F51 · 결정 164
    other = tmp_path / "foreign"
    other.mkdir(mode=0o700)
    (tmp_path / "xdg").mkdir()
    (tmp_path / "xdg" / "rcm").symlink_to(other)
    root = follow.cache_root({"XDG_CACHE_HOME": str(tmp_path / "xdg"), "HOME": str(tmp_path)})
    assert root == Path(os.path.realpath(other))
    real = os.geteuid()
    monkeypatch.setattr(follow.os, "geteuid", lambda: real + 1)
    with pytest.raises(follow.FollowError) as e:
        follow.check_cache_safe(root)
    assert e.value.code == "cache_unsafe"
    assert str(Path(os.path.realpath(other))) in e.value.detail


def test_cache_made_unsafe_after_a_fetch(servers, root):  # A92
    do_fetch(servers(NEW), root)
    (root / "clients").chmod(0o777)
    try:
        with pytest.raises(follow.FollowError) as e:
            follow.check_cache_safe(root)
        assert e.value.code == "cache_unsafe"
    finally:
        (root / "clients").chmod(0o700)


# ── 캐시 읽기 · 청소 ─────────────────────────────────────────────────────────


def test_scan_cache_order_and_bad(root):  # A33 · §14 B질문14
    a = make_entry(root, OLD, SHA_A, T)
    b = make_entry(root, OLD, SHA_B, T + M)
    got = follow.scan_cache(root)
    assert [e.path for e in got] == [b, a]
    assert got[0].last_used == T + M and got[0].sha256 == SHA_B and got[0].version == OLD
    (a / ".bad").write_text("")
    assert [e.path for e in follow.scan_cache(root)] == [b]
    with_bad = follow.scan_cache(root, include_bad=True)
    assert {(e.path, e.bad) for e in with_bad} == {(a, True), (b, False)}


def test_scan_cache_reads_the_marker_not_the_name(root):  # A123 · §14 A질문15
    make_entry(root, "0.8.0-rc1", SHA_A, T, name="0.8.0-rc1-aaaaaaaa")
    got = follow.scan_cache(root)
    assert [e.version for e in got] == ["0.8.0-rc1"]


def test_scan_cache_skips_strangers(root, tmp_path):  # A133 · A134 · A132
    good = make_entry(root, NEW, SHA_A, T)
    loose = make_entry(root, NEW, SHA_B, T + 1)
    loose.chmod(0o777)
    (root / "clients" / "notes").mkdir()
    (root / "clients" / "readme.txt").write_text("hi")
    (root / "clients" / ".tmp-x").mkdir()
    outside = tmp_path / "outside"
    (outside / "remote_ci_monitor").mkdir(parents=True)
    (outside / ".rcm-client.json").write_text(
        json.dumps({"version": NEW, "sha256": digest_of(9), "server": "x", "fetched_at": T})
    )
    (root / "clients" / "link").symlink_to(outside)
    try:
        assert [e.path for e in follow.scan_cache(root)] == [good]
    finally:
        loose.chmod(0o700)


def recording_rmtree(monkeypatch) -> list[str]:
    seen: list[str] = []
    real = shutil.rmtree

    def rec(path, *a, **k):
        seen.append(Path(path).name)
        return real(path, *a, **k)

    monkeypatch.setattr(shutil, "rmtree", rec)
    return seen


def test_prune_keeps_three_recent(root, monkeypatch):  # A30 · F35 · §14 A질문16
    old = [make_entry(root, OLD, digest_of(i), T + i * 60) for i in range(5)]
    new = make_entry(root, NEW, SHA_A, T + 3600)
    seen = recording_rmtree(monkeypatch)
    removed = follow.prune(root, keep=3, protect=new)
    left = {p.name for p in entries(root / "clients")}
    assert left == {new.name, old[4].name, old[3].name}
    assert sorted(removed) == sorted(old[:3])
    assert seen and all(n.startswith(".trash-") for n in seen), seen
    assert leftovers(root / "clients") == []


def test_prune_keeps_locked_and_protected(root):  # A66 · F35
    old = [make_entry(root, OLD, digest_of(i), T + i * 60) for i in range(5)]
    protect = old[0]  # 지금 목표는 가장 오래됐어도 남는다
    locked = old[1]
    fh = open(locked / ".lock", "a")  # noqa: SIM115
    try:
        fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
        new = make_entry(root, NEW, SHA_A, T + 3600)
        follow.prune(root, keep=3, protect=protect)
    finally:
        fh.close()
    left = {p.name for p in entries(root / "clients")}
    assert left == {new.name, old[4].name, old[3].name, locked.name, protect.name}


def test_prune_leaves_unknown_things(root, tmp_path):  # A133 · A134 · A132
    for i in range(4):
        make_entry(root, OLD, digest_of(i), T + 1000 + i)
    clients = root / "clients"
    (clients / "notes").mkdir()
    (clients / "readme.txt").write_text("hi")
    loose = make_entry(root, OLD, digest_of(50), T)
    loose.chmod(0o777)
    outside = tmp_path / "outside"
    (outside / "remote_ci_monitor").mkdir(parents=True)
    (outside / "remote_ci_monitor" / "x.py").write_text("x = 1\n")
    (outside / ".rcm-client.json").write_text(
        json.dumps({"version": OLD, "sha256": digest_of(60), "server": "x", "fetched_at": T})
    )
    os.utime(outside / ".rcm-client.json", (T - 100, T - 100))
    (clients / "link").symlink_to(outside)
    protect = make_entry(root, NEW, SHA_A, T + 5000)
    try:
        follow.prune(root, keep=3, protect=protect)
    finally:
        loose.chmod(0o700)
    assert (clients / "notes").is_dir() and (clients / "readme.txt").is_file()
    assert loose.is_dir()  # 남이 쓸 수 있는 항목은 고르지도 지우지도 않는다
    assert not (clients / "link").exists() and not (clients / "link").is_symlink()
    assert (outside / "remote_ci_monitor" / "x.py").is_file()  # 링크가 가리킨 곳은 그대로


def test_prune_clears_old_leftovers(root):  # A87 · §2.8
    clients = root / "clients"
    clients.mkdir(parents=True)
    now = time.time()
    ages = {".tmp-old": 61, ".tmp-new": 59, ".trash-old": 61, ".trash-new": 59}
    for name, minutes in ages.items():
        (clients / name).mkdir()
        (clients / name / "f").write_text("x")
        os.utime(clients / name, (now - minutes * 60, now - minutes * 60))
    protect = make_entry(root, NEW, SHA_A, now)
    follow.prune(root, keep=3, protect=protect)
    assert sorted(p.name for p in clients.iterdir() if p.name.startswith(".")) == [
        ".tmp-new",
        ".trash-new",
    ]


def test_fetch_prunes_after_a_win(servers, root):  # A30 · A88 · 결정 163
    for i in range(5):
        make_entry(root, OLD, digest_of(i), T + i)
    srv = servers(NEW)
    entry = do_fetch(srv, root)
    left = {p.name for p in entries(root / "clients")}
    assert entry.path.name in left and len(left) == 3
