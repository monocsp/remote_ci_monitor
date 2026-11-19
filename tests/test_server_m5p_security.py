"""보관함(M5p) 보안 — 경로 디코딩 · 헤더 · 인라인 허용 목록 · Host 421 · 되살리기 출처 · 이스케이프.

명세는 docs/m5p-workplan.md §4.6 · §4.7 · §15.4 · §15.5, 시나리오 docs/m5p-test-scenarios-b.md 의
`sec` 줄이다. 결정 139 · 141 · 145 · 148.

- 한 항목에 이상한 이름을 다 담아 두고(모듈 하나에 서버 하나) 읽기만 하는 시험이 같이 쓴다.
- 상태를 바꾸는 시험(되살리기 · 시계)은 시험마다 서버를 띄운다.
"""

from __future__ import annotations

import io
import json
import os
import socket
import tarfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from shelfkit import (
    PNG_1PX,
    advance_days,
    finished_linux_job,
    fixtures,
    item_dir,
    last_used,
    make_server,
    manifest_of,
    publish_payload,
    run_local,
    shelf_presets,
    shelve_bundle,
    tar_bytes,
    wait_shelf,
)
from test_server import sh

FILE_CSP = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
EVIL_HTML = b'<script>localStorage.getItem("rcm.token")</script>'
WEIRD = {
    "out/a.png": PNG_1PX,
    "out/evil.html": EVIL_HTML,
    "out/x.svg": b"<svg onload=alert(1)>",
    "out/x.pdf": b"%PDF-1.4",
    "out/x.js": b"alert(1)",
    "out/fake.png": b"<html><script>alert(1)</script>",
    "out/note.txt": b"<script>alert(1)</script>",
    "out/data.json": b'{"x":"<script>"}',
    "out/결과 화면.png": PNG_1PX,
    'out/q"uote.txt': b"q",
    "out/100%.txt": b"p",
    "out/<img src=x onerror=alert(1)>.png": PNG_1PX,
    "out/a?b.png": PNG_1PX,
    "out/c#d.png": PNG_1PX,
    "out/a+b.txt": b"plus",
    "out/x y.txt": b"space",
    "out/한.png": PNG_1PX,
    "manifest.json": b'{"mine":true}',
}


@pytest.fixture(scope="module")
def weird(tmp_path_factory):
    """(서버, 잡 번호) — 이상한 이름을 다 담은 보관 항목 하나."""
    # 멤버가 열여덟이다 — 건너뛸 것까지 `max_artifact_files` 에 센다(격리 검증 D1)
    s = make_server(tmp_path_factory.mktemp("weird"), max_artifact_files=32)
    s.cfg.presets = shelf_presets(
        [
            sh(
                "linin",
                "true",
                keep=True,
                artifacts=["out/**"],
                pool="linux",
                inputs=[{"name": "note", "type": "string"}],
            )
        ]
    )
    jid = shelve_bundle(
        s,
        WEIRD,
        preset="linin",
        branch="<b>dev</b>",
        label='"><script>alert(1)</script>',
        inputs={"note": "'; x=\""},
    )
    yield s, jid
    s.close()


@pytest.fixture
def srv(tmp_path):
    s = make_server(tmp_path)
    yield s
    s.close()


def get(srv, path: str, **kw: Any):
    return srv.req("GET", path, raw=True, **kw)


def raw_request(port: int, text: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
        sock.sendall(text)
        out = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
    return out


# ══ 경로 (§4.6 · §15.4) ══════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "tail",
    [
        "%2e%2e/%2e%2e/%2e%2e/rcm.sqlite3",  # E1
        "..%2f..%2f..%2frcm.sqlite3",  # E2
        "%2fetc%2fpasswd",  # E3
        "out/a.png%00.txt",  # E4
        "out/a%0d%0aX-Evil:%201.png",  # E5
        "..%5c..%5crcm.sqlite3",  # E6
        ".git/config",  # E7
        "%2egit/config",  # E7
        "OUT/.GIT/config",  # .git 은 대소문자 없이
    ],
)
def test_e1_to_e7_unsafe_paths_after_decoding_are_400(weird, tail):
    srv, jid = weird
    status, headers, body = get(srv, f"/shelf/{jid}/f/{tail}")
    assert status == 400, (tail, status, body[:200])
    assert b"SQLite format 3" not in body
    assert "X-Evil" not in headers


@pytest.mark.parametrize("tail", ["out/../a.png", "/etc/passwd"])
def test_e8_the_router_still_refuses_raw_dot_dot_and_double_slash(weird, tail):
    srv, jid = weird
    status, _h, body = get(srv, f"/shelf/{jid}/f/{tail}")
    assert status == 400 and b"bad path" in body


def test_e9_double_encoding_is_decoded_once(weird):
    srv, jid = weird
    assert get(srv, f"/shelf/{jid}/f/%252e%252e/rcm.sqlite3")[0] == 404


def test_e10_broken_utf8_is_not_a_500(weird):
    srv, jid = weird
    assert get(srv, f"/shelf/{jid}/f/out/%ff%fe.png")[0] in (400, 404)


def test_q5_an_encoded_slash_that_decodes_to_a_real_path_is_served(weird):
    srv, jid = weird
    status, _h, body = get(srv, f"/shelf/{jid}/f/out%2fa.png")
    assert status == 200 and body == PNG_1PX


def test_e11_a_file_outside_the_manifest_is_404(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    (item_dir(srv, jid) / "files/out/planted.txt").write_text("planted")
    assert get(srv, f"/shelf/{jid}/f/out/planted.txt")[0] == 404


def test_e12_a_symlinked_file_is_404(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    target = item_dir(srv, jid) / "files/out/a.png"
    target.unlink()
    target.symlink_to(srv.cfg.data_dir / "rcm.sqlite3")
    status, _h, body = get(srv, f"/shelf/{jid}/f/out/a.png")
    assert status == 404 and b"SQLite" not in body


def test_e13_a_symlinked_folder_is_404(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX, "out/rcm.sqlite3": b"x"})
    out = item_dir(srv, jid) / "files/out"
    for f in out.iterdir():
        f.unlink()
    out.rmdir()
    out.symlink_to(srv.cfg.data_dir)
    for tail in ("out/a.png", "out/rcm.sqlite3"):
        status, _h, body = get(srv, f"/shelf/{jid}/f/{tail}")
        assert status == 404 and b"SQLite" not in body, tail


def test_e14_rcms_own_manifest_is_not_served(weird):
    srv, jid = weird
    assert get(srv, f"/shelf/{jid}/manifest.json")[0] == 404
    assert get(srv, f"/shelf/{jid}/f/../manifest.json")[0] == 400


def test_g28_a_job_file_called_manifest_json_is_the_jobs(weird):
    srv, jid = weird
    status, _h, body = get(srv, f"/shelf/{jid}/f/manifest.json")
    assert status == 200 and json.loads(body) == {"mine": True}


def test_g29_an_nfd_request_does_not_match_an_nfc_name(weird):
    srv, jid = weird
    nfd = quote("out/한.png")
    assert get(srv, f"/shelf/{jid}/f/{nfd}")[0] == 404
    assert get(srv, f"/shelf/{jid}/f/{quote('out/한.png')}")[0] == 200


# ══ 헤더 (결정 139) ═══════════════════════════════════════════════════════════


def assert_sandboxed(headers: dict[str, str]) -> None:
    assert headers["Content-Security-Policy"] == FILE_CSP
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize("name", ["evil.html", "x.svg", "x.pdf", "x.js"])
def test_e15_e16_active_documents_are_downloads(weird, name):
    srv, jid = weird
    status, headers, _b = get(srv, f"/shelf/{jid}/f/out/{name}")
    assert status == 200
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"].startswith("attachment; ")
    assert_sandboxed(headers)


def test_e17_html_inside_a_png_stays_a_png(weird):
    srv, jid = weird
    status, headers, _b = get(srv, f"/shelf/{jid}/f/out/fake.png")
    assert status == 200 and headers["Content-Type"] == "image/png"
    assert_sandboxed(headers)


@pytest.mark.parametrize("name", ["note.txt", "data.json"])
def test_e18_text_is_plain_text(weird, name):
    srv, jid = weird
    status, headers, _b = get(srv, f"/shelf/{jid}/f/out/{name}")
    assert status == 200 and headers["Content-Type"] == "text/plain; charset=utf-8"
    assert headers["Content-Disposition"].startswith("inline; ")
    assert_sandboxed(headers)


def raw_headers(srv, path: str) -> bytes:
    req = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nConnection: close\r\n\r\n"
    out = raw_request(srv.port, req.encode())
    return out.split(b"\r\n\r\n", 1)[0]


def test_e19_a_korean_name_is_encoded_not_raw(weird):
    srv, jid = weird
    path = f"/shelf/{jid}/f/{quote('out/결과 화면.png')}"
    head = raw_headers(srv, path)
    assert head.startswith(b"HTTP/1.1 200")
    assert all(b < 0x80 for b in head)
    assert (
        b'Content-Disposition: inline; filename="__ __.png"; '
        b"filename*=UTF-8''%EA%B2%B0%EA%B3%BC%20%ED%99%94%EB%A9%B4.png" in head
    )


def test_e20_quotes_and_percents_in_names(weird):
    srv, jid = weird
    quoted = quote('out/q"uote.txt')
    status, headers, _b = get(srv, f"/shelf/{jid}/f/{quoted}")
    assert status == 200
    cd = headers["Content-Disposition"]
    assert 'filename="q_uote.txt"' in cd and "filename*=UTF-8''q%22uote.txt" in cd
    status, headers, _b = get(srv, f"/shelf/{jid}/f/{quote('out/100%.txt')}")
    assert status == 200 and "filename*=UTF-8''100%25.txt" in headers["Content-Disposition"]


def test_e21_control_characters_from_a_real_job_never_reach_the_shelf(srv):
    srv.cfg.presets = shelf_presets(
        [
            sh(
                "ctlkeep",
                "mkdir -p out && cp fixtures/home.png out/ok.png && "
                "f=$(printf 'out/a\\nb.png') && cp fixtures/home.png \"$f\" && "
                "g=$(printf 'out/x\\r\\nSet-Cookie: rcm=1.txt') && echo hi > \"$g\"",
                keep=True,
                artifacts=["out/*"],
            )
        ]
    )
    jid = run_local(srv, "ctlkeep")
    wait_shelf(srv, jid)
    names = [p.name for p in (item_dir(srv, jid) / "files/out").iterdir()]
    assert names == ["ok.png"], names
    paths = [f["path"] for f in manifest_of(srv, jid)["files"]]
    assert paths == ["out/ok.png"]
    for path in (f"/shelf/{jid}/", "/shelf/"):
        status, headers, body = get(srv, path)
        assert status == 200
        assert b"\r" not in body and "Set-Cookie" not in headers
        assert b"a\nb.png" not in body


# ══ Host 허용 목록 (결정 148 · §15.5) ═════════════════════════════════════════


def shelf_routes(jid: int) -> list[tuple[str, str]]:
    return [
        ("GET", "/shelf/"),
        ("GET", f"/shelf/{jid}/"),
        ("GET", f"/shelf/{jid}/f/out/a.png"),
        ("GET", f"/shelf/{jid}.zip"),
        ("GET", "/shelf/latest/linin"),
        ("GET", "/api/shelf"),
        ("GET", f"/api/shelf/{jid}"),
        ("POST", f"/shelf/{jid}/restore"),
        ("DELETE", f"/shelf/{jid}"),
    ]


def test_e22_a_foreign_host_gets_421_on_every_shelf_route(weird):
    srv, jid = weird
    before = last_used(srv, jid)
    host = f"evil.example:{srv.port}"
    for method, path in shelf_routes(jid):
        hdrs = {"Host": host, "Origin": f"http://{host}"}
        status = srv.req(method, path, headers=hdrs)[0]
        assert status == 421, (method, path, status)
    assert last_used(srv, jid) == before and item_dir(srv, jid).is_dir()
    assert srv.req("GET", "/api/status", headers={"Host": host})[0] == 200


def test_q6_a_valid_bearer_skips_the_host_check(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    host = {"Host": "macmini.tail1234.example.com:8787"}
    assert srv.req("GET", "/api/shelf", headers=host)[0] == 421
    assert srv.req("GET", "/api/shelf", headers=host, token="alice")[0] == 200
    status, body = srv.req("DELETE", f"/shelf/{jid}", headers=host, token="admin")
    assert status == 200, body
    bogus = {**host, "Authorization": "Bearer not-a-token"}
    assert srv.req("GET", "/api/shelf", headers=bogus)[0] == 421


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1.nip.io",
        "macmini.local.evil.com",
        "evil.com:8787@127.0.0.1",
        "172.32.0.1",
        "100.128.0.1",
        "[2001:db8::1]:8787",
        "3232235777",
        "0x7f000001",
        "127.1",
        "",
    ],
)
def test_e23_lookalike_hosts_are_421(weird, host):
    srv, jid = weird
    req = f"GET /shelf/{jid}/ HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    out = raw_request(srv.port, req.encode())
    assert out.startswith(b"HTTP/1.1 421"), (host, out[:80])


def test_e23_an_http10_request_without_host_is_421(weird):
    srv, jid = weird
    out = raw_request(srv.port, f"GET /shelf/{jid}/ HTTP/1.0\r\n\r\n".encode())
    assert out.split(b"\r\n", 1)[0].endswith(b"421 Misdirected Request"), out[:80]


@pytest.mark.parametrize(
    "host",
    [
        "localhost:{port}",
        "macmini.local:{port}",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.10:8787",
        "100.64.0.1",
        "100.127.255.254",
        "[fd12::1]:8787",
        "[::1]:{port}",
        "macmini.tail1234.ts.net",
        "MACMINI.LOCAL.",
    ],
)
def test_e24_local_and_private_hosts_are_allowed(weird, host):
    srv, jid = weird
    h = host.format(port=srv.port)
    assert srv.req("GET", f"/shelf/{jid}/", headers={"Host": h}, raw=True)[0] == 200, h


def test_e24_public_url_and_the_hostname_are_allowed(srv, monkeypatch):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    assert srv.req("GET", f"/api/shelf/{jid}", headers={"Host": "ci.example.com"})[0] == 421
    srv.cfg.server.public_url = "http://ci.example.com"
    assert srv.req("GET", f"/api/shelf/{jid}", headers={"Host": "ci.example.com"})[0] == 200
    monkeypatch.setattr(socket, "gethostname", lambda: "macmini")
    assert srv.req("GET", f"/api/shelf/{jid}", headers={"Host": "macmini:8787"})[0] == 200


def test_e25_421_comes_before_the_basic_challenge(tmp_path):
    s = make_server(tmp_path, read_auth="basic")
    try:
        status, headers, _b = s.req("GET", "/shelf/", headers={"Host": "evil.example"}, raw=True)
        assert status == 421 and "WWW-Authenticate" not in headers
        status, headers, _b = s.req("GET", "/shelf/", raw=True)
        assert status == 401 and headers["WWW-Authenticate"].startswith("Basic")
    finally:
        s.close()


# ══ 되살리기 (결정 145) ═══════════════════════════════════════════════════════


@pytest.fixture
def stale(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    advance_days(srv, 183)
    srv.app.retention.sweep_once(srv.clock.now)
    assert srv.store.shelf_item(jid).state == "stale"
    return srv, jid


def restore(srv, jid: int, headers: dict[str, str]) -> int:
    return srv.req("POST", f"/shelf/{jid}/restore", headers=headers)[0]


def still_stale(srv, jid: int) -> bool:
    return srv.store.shelf_item(jid).state == "stale"


def test_e26_another_origin_is_403(stale):
    srv, jid = stale
    assert restore(srv, jid, {"Origin": "http://evil.example"}) == 403
    assert still_stale(srv, jid)


def test_e27_no_origin_and_no_referer_is_403(stale):
    srv, jid = stale
    assert restore(srv, jid, {}) == 403 and still_stale(srv, jid)


def test_e28_a_null_origin_falls_back_to_the_referer(stale):
    srv, jid = stale
    me = f"http://127.0.0.1:{srv.port}"
    assert restore(srv, jid, {"Origin": "null", "Referer": "http://evil.example/"}) == 403
    assert still_stale(srv, jid)
    assert restore(srv, jid, {"Origin": "null", "Referer": f"{me}/shelf/{jid}/"}) == 303
    assert not still_stale(srv, jid)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://127.0.0.1:{port}"},
        {"Origin": "http://localhost:{port}"},
        {"Referer": "http://127.0.0.1:{port}.evil.com/shelf/1/"},
        {"Referer": "http://127.0.0.1:{port}@evil.com/"},
        {"Origin": "http://evil.example", "Referer": "http://127.0.0.1:{port}/shelf/1/"},
    ],
    ids=["https", "other-host", "suffix", "userinfo", "origin-wins"],
)
def test_e29_e30_e31_lookalike_origins_are_403(stale, headers):
    srv, jid = stale
    h = {k: v.format(port=srv.port) for k, v in headers.items()}
    assert restore(srv, jid, h) == 403 and still_stale(srv, jid)


def test_q7_the_host_part_is_compared_without_case(stale):
    srv, jid = stale
    h = {"Host": f"LOCALHOST:{srv.port}", "Origin": f"http://localhost:{srv.port}"}
    status, headers, _b = srv.req("POST", f"/shelf/{jid}/restore", headers=h, raw=True)
    assert status == 303 and headers["Location"] == f"/shelf/{jid}/"


def test_restore_keeps_the_language(stale):
    srv, jid = stale
    me = f"http://127.0.0.1:{srv.port}"
    status, headers, _b = srv.req(
        "POST", f"/shelf/{jid}/restore?lang=en", headers={"Origin": me}, raw=True
    )
    assert status == 303 and headers["Location"] == f"/shelf/{jid}/?lang=en"


def test_e33_restore_is_post_only(stale):
    srv, jid = stale
    assert srv.req("GET", f"/shelf/{jid}/restore")[0] == 405


# ══ 묶음 · 펼침 안의 나쁜 멤버 (§15.4 · E49 · E50) ════════════════════════════


def bad_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:

        def add(info: tarfile.TarInfo, data: bytes | None = None) -> None:
            if data is not None:
                info.size = len(data)
            tf.addfile(info, io.BytesIO(data) if data is not None else None)

        add(tarfile.TarInfo("/etc/passwd"), b"root")
        add(tarfile.TarInfo("../../escape.txt"), b"out")
        link = tarfile.TarInfo("out")
        link.type, link.linkname = tarfile.SYMTYPE, "/"
        add(link)
        hard = tarfile.TarInfo("hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, "/etc/passwd"
        add(hard)
        chr_ = tarfile.TarInfo("dev")
        chr_.type, chr_.devmajor, chr_.devminor = tarfile.CHRTYPE, 1, 3
        add(chr_)
        fifo = tarfile.TarInfo("fifo")
        fifo.type = tarfile.FIFOTYPE
        add(fifo)
        add(tarfile.TarInfo(".git/config"), b"[core]")
        add(tarfile.TarInfo("a\nb.png"), PNG_1PX)
        add(tarfile.TarInfo("good.png"), PNG_1PX)
    return buf.getvalue()


def walk_specials(root: Path) -> list[str]:
    bad = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            st = p.lstat()
            import stat as st_mod

            if not (st_mod.S_ISREG(st.st_mode) or st_mod.S_ISDIR(st.st_mode)):
                bad.append(str(p))
    return bad


def test_e49_bad_members_inside_an_expanded_tar_are_skipped_one_by_one(tmp_path):
    # 건너뛸 멤버도 `max_artifact_files` 에 센다(격리 검증 D1) — 나쁜 멤버 여덟에 좋은 하나라 16
    srv = make_server(tmp_path / "s", max_artifact_files=16)
    request_close = srv.close
    outside_before = set(os.listdir(tmp_path))
    jid = run_local(srv, "copyfix", fixtures({"fixtures/bad.tar.gz": bad_tar()}))
    wait_shelf(srv, jid)
    assert walk_specials(srv.cfg.shelf_dir) == []
    assert set(os.listdir(tmp_path)) == outside_before
    assert not Path("/etc/passwd.d").exists()
    paths = {f["path"] for f in manifest_of(srv, jid)["files"]}
    expanded = {p for p in paths if p.startswith("out/bad.tar.gz.d/")}
    assert expanded == {"out/bad.tar.gz.d/good.png"}, expanded
    skipped = manifest_of(srv, jid)["skipped"]
    assert len(skipped) == 8 and all(s["expanded_from"] == "out/bad.tar.gz" for s in skipped)
    request_close()


def sparse_tar(declared: int) -> bytes:
    """GNU sparse 멤버 하나 — 실제 데이터 512 바이트, 선언한 크기 `declared`."""
    info = tarfile.TarInfo("sparse.bin")
    info.type = tarfile.GNUTYPE_SPARSE
    info.size = 512
    head = bytearray(info.tobuf(tarfile.GNU_FORMAT))
    head[386:398] = tarfile.itn(declared - 512, 12, tarfile.GNU_FORMAT)
    head[398:410] = tarfile.itn(512, 12, tarfile.GNU_FORMAT)
    head[483:495] = tarfile.itn(declared, 12, tarfile.GNU_FORMAT)
    head[148:156] = b"        "
    chksum = tarfile.calc_chksums(bytes(head))[0]
    head[148:155] = b"%06o\0" % chksum
    raw = bytes(head) + b"x" * 512 + bytes(1024)
    return raw


def test_e50_a_sparse_member_is_refused(srv):
    import gzip
    import time

    payload = gzip.compress(sparse_tar(1 << 40))
    began = time.monotonic()
    jid = run_local(srv, "copyfix", fixtures({"fixtures/sparse.tar.gz": payload}))
    wait_shelf(srv, jid)
    assert time.monotonic() - began < 30
    assert not (item_dir(srv, jid) / "files/out/sparse.tar.gz.d/sparse.bin").exists()
    total = sum(f.stat().st_size for f in srv.cfg.shelf_dir.rglob("*") if f.is_file())
    assert total < 10 * 1024 * 1024


def test_e50_a_sparse_member_in_the_outer_bundle_is_refused(srv):
    jid = finished_linux_job(srv)
    publish_payload(srv, jid, sparse_tar(1 << 40)[:-1024] + tar_bytes([("out/ok.txt", b"ok")]))
    srv.app._on_job_change(jid)
    doc = wait_shelf(srv, jid, ("active", "failed"))
    if doc["state"] == "active":
        assert {f["path"] for f in doc["files"]} == {"out/ok.txt"}
    assert not (item_dir(srv, jid) / "files/sparse.bin").exists()


# ══ 번호 · 이스케이프 · 링크 ══════════════════════════════════════════════════


@pytest.mark.parametrize("n", ["999", "abc", "99999999999999999999"])
def test_e60_unknown_numbers_are_404_not_500(weird, n):
    srv, _jid = weird
    for path in (f"/shelf/{n}/", f"/api/shelf/{n}", f"/shelf/{n}.zip"):
        assert get(srv, path)[0] == 404, path


def test_g25_names_and_labels_are_escaped(weird):
    srv, jid = weird
    html = get(srv, f"/shelf/{jid}/")[2].decode()
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<img src=x" not in html and "<script>" not in html and "<b>dev</b>" not in html
    assert "&lt;b&gt;dev&lt;/b&gt;" in html
    assert "&quot;&gt;&lt;script&gt;" in html
    assert "&#x27;; x=&quot;" in html
    href = f"/shelf/{jid}/f/out/%3Cimg%20src%3Dx%20onerror%3Dalert%281%29%3E.png"
    assert f'href="{href}"' in html
    status, headers, _b = get(srv, href)
    assert status == 200 and headers["Content-Type"] == "image/png"


@pytest.mark.parametrize(
    ("name", "encoded"),
    [
        ("out/a?b.png", "out/a%3Fb.png"),
        ("out/c#d.png", "out/c%23d.png"),
        ("out/a+b.txt", "out/a%2Bb.txt"),
        ("out/x y.txt", "out/x%20y.txt"),
    ],
)
def test_g26_special_characters_are_encoded_in_links(weird, name, encoded):
    srv, jid = weird
    html = get(srv, f"/shelf/{jid}/")[2].decode()
    assert f'href="/shelf/{jid}/f/{encoded}"' in html
    assert get(srv, f"/shelf/{jid}/f/{encoded}")[0] == 200


def test_g26_a_raw_plus_is_not_a_space(weird):
    srv, jid = weird
    status, _h, body = get(srv, f"/shelf/{jid}/f/out/a+b.txt")
    assert status == 200 and body == b"plus"


def test_g27_every_link_is_an_absolute_path(weird):
    import re

    srv, jid = weird
    for path in ("/shelf/", f"/shelf/{jid}/"):
        html = get(srv, path, headers={"Host": f"macmini.local:{srv.port}"})[2].decode()
        targets = re.findall(r'(?:href|src|action)="([^"]*)"', html)
        assert targets, path
        assert all(t.startswith(("/shelf/", "/#/jobs/")) for t in targets), targets
        assert "macmini.local" not in html and "http://" not in html
