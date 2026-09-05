"""웹 UI 정적 서빙(M2) — `GET /` · `/static/app.js` · `/static/style.css` · ETag/304 · 404/400 ·
`read_auth` · HEAD · 405 · wheel 패키징 · `importlib.resources`. 명세는 docs/m2-workplan.md §1.

구현보다 먼저 썼다(test-first). 서버는 `test_server.Server`(in-process, 워커 off) 를 그대로 쓴다.
"""

import hashlib
import importlib.resources
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from test_server import Server

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_FILES = ("index.html", "app.js", "style.css")
# ETag 는 sha256 앞 16자. 따옴표는 있어도 없어도 되고, If-None-Match 는 받은 값 그대로 보낸다.
ETAG_RE = re.compile(r'^"?([0-9a-f]{16})"?$')
# `pip wheel .` 은 격리된 빌드 환경에 hatchling 을 내려받는다 — 오프라인이면 실패가 아니라 skip.
NO_NETWORK_MARKERS = ("Could not", "No matching", "No module named pip")


def web_file(name: str) -> bytes:
    """소스 트리(pytest 는 `src` 를 pythonpath 에 둔다)의 `web/<name>` 바이트."""
    return (importlib.resources.files("remote_ci_monitor") / "web" / name).read_bytes()


def cache_tokens(headers: dict[str, str]) -> set[str]:
    return {t.strip().lower() for t in headers.get("Cache-Control", "").split(",") if t.strip()}


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


# ── GET / ────────────────────────────────────────────────────────────────────


def test_index_html_is_served_with_no_cache_and_nosniff(srv):
    status, headers, body = srv.req("GET", "/", raw=True)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "no-cache" in cache_tokens(headers) and "no-store" not in cache_tokens(headers)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert int(headers["Content-Length"]) == len(body)
    assert body == web_file("index.html")  # 패키지 안의 파일 그대로
    html = body.decode("utf-8")
    assert "<title>" in html
    for needle in (
        'id="hdr"',
        'id="queue"',
        'id="banner-lost"',
        '<script src="/static/app.js"',
        '<link rel="stylesheet" href="/static/style.css"',
    ):
        assert needle in html, needle


# ── /static/* ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path,ctype",
    [("/static/app.js", "application/javascript"), ("/static/style.css", "text/css")],
)
def test_static_asset_content_type_etag_and_304(srv, path, ctype):
    name = path.rsplit("/", 1)[1]
    status, headers, body = srv.req("GET", path, raw=True)
    assert status == 200
    assert headers["Content-Type"].startswith(ctype)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert body == web_file(name) and len(body) > 0
    assert int(headers["Content-Length"]) == len(body)
    etag = headers["ETag"]
    m = ETAG_RE.match(etag)
    assert m, f"ETag is not 16 hex of sha256: {etag!r}"
    assert m.group(1) == hashlib.sha256(body).hexdigest()[:16]
    # 같은 ETag → 304, 본문 없음, ETag 는 그대로 실린다
    status, headers2, body2 = srv.req("GET", path, headers={"If-None-Match": etag}, raw=True)
    assert status == 304 and body2 == b""
    assert headers2.get("ETag") == etag
    # 다른 ETag → 200 + 본문
    stale = '"0000000000000000"'
    status, _, body3 = srv.req("GET", path, headers={"If-None-Match": stale}, raw=True)
    assert status == 200 and body3 == body


def test_other_static_paths_are_404_json(srv):
    for path in (
        "/static/nope.js",
        "/static/",
        "/static",
        "/static/index.html",  # index 는 `/` 로만 준다
        "/static/app.js/extra",
        "/static/app.js.map",
    ):
        status, body = srv.req("GET", path)
        assert status == 404, (path, status, body)
        assert isinstance(body, dict) and isinstance(body.get("error"), str), (path, body)


def test_static_path_traversal_is_400(srv):
    for path in (
        "/static/../pyproject.toml",
        "/static/../../etc/passwd",
        "/../static/app.js",
        "/static//app.js",
        "/static/\\app.js",
    ):
        status, body = srv.req("GET", path)
        assert status == 400, (path, status, body)
        assert isinstance(body, dict) and "error" in body, (path, body)


# ── HEAD · 405 ───────────────────────────────────────────────────────────────


def test_head_matches_get_headers_without_body(srv):
    for path in ("/", "/static/app.js", "/static/style.css"):
        g_status, g_headers, g_body = srv.req("GET", path, raw=True)
        h_status, h_headers, h_body = srv.req("HEAD", path, raw=True)
        assert g_status == 200, path
        assert (h_status, h_body) == (200, b""), path
        assert h_headers["Content-Length"] == g_headers["Content-Length"] == str(len(g_body))
        assert h_headers["Content-Type"] == g_headers["Content-Type"], path
        assert h_headers.get("ETag") == g_headers.get("ETag"), path


def test_write_methods_on_ui_routes_are_405(srv):
    status, body = srv.req("POST", "/", json_body={})
    assert status == 405 and "error" in body
    assert srv.req("PUT", "/static/app.js", body=b"x")[0] == 405
    assert srv.req("POST", "/static/style.css", json_body={})[0] == 405


# ── read_auth = basic ────────────────────────────────────────────────────────


def test_read_auth_basic_requires_token_for_ui_and_assets(tmp_path):
    s = Server(tmp_path, workers=False, read_auth="basic")
    try:
        for path in ("/", "/static/app.js", "/static/style.css"):
            status, headers, body = s.req("GET", path, raw=True)
            assert status == 401, path
            assert headers.get("WWW-Authenticate", "").startswith("Bearer")
            assert isinstance(json.loads(body).get("error"), str)  # JSON 한 줄, 로그인 페이지 아님
            assert s.req("HEAD", path, raw=True)[0] == 401, path
            assert s.req("GET", path, token="alice")[0] == 200, path
        assert s.req("GET", "/api/health")[0] == 200  # health 는 항상 열려 있다
    finally:
        s.close()


# ── 패키징 ───────────────────────────────────────────────────────────────────


def test_web_assets_are_package_resources():
    web = importlib.resources.files("remote_ci_monitor") / "web"
    for name in WEB_FILES:
        f = web / name
        assert f.is_file(), f"missing {f}"
        assert len(f.read_bytes()) > 0, f"empty {f}"


def test_wheel_ships_web_assets(tmp_path):
    dist = tmp_path / "dist"
    cmd = [sys.executable, "-m", "pip", "wheel", ".", "-w", str(dist), "--no-deps", "-q"]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 and any(m in proc.stderr for m in NO_NETWORK_MARKERS):
        pytest.skip(f"pip wheel could not build (offline?): {proc.stderr.strip()[-300:]}")
    assert proc.returncode == 0, proc.stderr
    wheels = sorted(dist.glob("remote_ci_monitor-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
        shipped = sorted(n for n in names if "/web/" in n)
        for name in WEB_FILES:
            member = f"remote_ci_monitor/web/{name}"
            assert member in names, f"{member} not in wheel; web/ members: {shipped}"
            assert zf.read(member) == web_file(name), f"{member} differs from the source tree"
