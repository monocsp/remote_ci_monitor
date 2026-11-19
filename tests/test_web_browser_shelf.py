"""보관함(M5p) — 진짜 Chrome(CDP)으로 되살리기 버튼 · 교차 출처 form · sandbox 문서를 본다.

시나리오 docs/m5p-test-scenarios-b.md 의 `web` 줄(B19 · E32 · G44)이다. 결정 139 · 145. 브라우저가
실제로 싣는 `Origin` · `Referer` 를 믿을 수 있는지는 손으로 만든 헤더로는 모른다(검토 P0-6). Chrome
이 없으면 skip, `RCM_CHROME` 이 있으면 실패(`test_web_browser.pytestmark`).
"""

from __future__ import annotations

import http.server
import threading
import time
from typing import Any

import pytest

from shelfkit import PNG_1PX, advance_days, make_server, shelve_bundle
from test_web_browser import Chrome, pytestmark  # noqa: F401  (Chrome 이 없으면 skip)


@pytest.fixture
def stale(tmp_path):
    s = make_server(tmp_path)
    jid = shelve_bundle(s, {"out/a.png": PNG_1PX, "out/note.txt": b"hello"})
    advance_days(s, 183)
    s.app.retention.sweep_once(s.clock.now)
    assert s.store.shelf_item(jid).state == "stale"
    yield s, jid
    s.close()


def wait_js(c: Chrome, expr: str, timeout: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = c.eval(expr)
        except (AssertionError, RuntimeError):
            last = None
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"{expr!r} never became truthy (last {last!r})")


def test_b19_the_restore_button_in_a_real_browser_redirects_back_with_pictures(stale, tmp_path):
    srv, jid = stale
    base = f"http://127.0.0.1:{srv.port}"
    with Chrome(tmp_path / "chrome", window="1200,900") as c:
        c.open(f"{base}/shelf/{jid}/", ready_js="!!document.querySelector('form button')")
        assert c.eval("document.images.length") == 0
        c.eval("document.querySelector('form button').click()")
        wait_js(c, "document.images.length > 0 && document.images[0].naturalWidth > 0")
        assert c.eval("location.pathname") == f"/shelf/{jid}/"
        nav = c.eval("JSON.stringify(performance.getEntriesByType('navigation')[0].redirectCount)")
        assert nav == "1"
    item = srv.store.shelf_item(jid)
    assert item.state == "active" and item.last_used_at == srv.clock.now.timestamp()


class _Attacker(http.server.BaseHTTPRequestHandler):
    target = ""

    def do_GET(self) -> None:  # noqa: N802
        body = (
            f'<form method="post" action="{self.target}"><button>x</button></form>'
            "<script>document.forms[0].submit()</script>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: Any) -> None:
        pass


def test_e32_cross_origin_forms_in_a_real_browser_are_403(stale, tmp_path):
    srv, jid = stale
    target = f"http://127.0.0.1:{srv.port}/shelf/{jid}/restore"
    _Attacker.target = target
    evil = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Attacker)
    t = threading.Thread(target=evil.serve_forever, daemon=True)
    t.start()
    try:
        with Chrome(tmp_path / "chrome", window="1000,800") as c:
            c.call("Page.navigate", {"url": f"http://127.0.0.1:{evil.server_address[1]}/"})
            wait_js(c, f"location.port === '{srv.port}'")
            wait_js(c, "document.body && document.body.innerText.length > 0")
            assert srv.store.shelf_item(jid).state == "stale"
            c.call("Page.navigate", {"url": "about:blank"})
            time.sleep(0.3)
            c.eval(
                "(() => { const f = document.createElement('form'); f.method = 'post';"
                f" f.action = '{target}'; document.body.appendChild(f); f.submit(); }})()"
            )
            wait_js(c, f"location.port === '{srv.port}'")
            time.sleep(0.3)
        assert srv.store.shelf_item(jid).state == "stale"
    finally:
        evil.shutdown()
        evil.server_close()


def test_g44_a_sandboxed_file_cannot_read_the_token(stale, tmp_path):
    srv, jid = stale
    base = f"http://127.0.0.1:{srv.port}"
    # 다시 보관 중으로(잡 행은 청소가 이미 지웠다 — 항목이 잡 행보다 오래 산다)
    assert srv.req("POST", f"/shelf/{jid}/restore", headers={"Origin": base})[0] == 303
    with Chrome(tmp_path / "chrome", window="1000,800") as c:
        c.open(
            f"{base}/shelf/{jid}/",
            ready_js="document.images.length > 0 && document.images[0].complete",
        )
        assert c.eval("document.images[0].naturalWidth") > 0
        c.eval("localStorage.setItem('rcm.token', 'secret')")
        c.call("Page.navigate", {"url": f"{base}/shelf/{jid}/f/out/note.txt"})
        wait_js(c, "document.body && document.body.innerText.includes('hello')")
        assert c.eval("window.origin") == "null"
        verdict = c.eval(
            "(() => { try { return localStorage.getItem('rcm.token') || 'empty'; }"
            " catch (e) { return 'threw'; } })()"
        )
        assert verdict == "threw"
