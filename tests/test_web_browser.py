"""웹 UI(M2) — 진짜 서버(in-process · 워커 on · 가짜 샘플러) 를 headless Chrome 으로 열어 DOM 계약
(docs/m2-workplan.md §4) 을 확인한다. Chrome 이 없으면 전부 skip.

Chrome 은 `--remote-debugging-pipe`(CDP, fd 3 읽기 · fd 4 쓰기) 로 몬다 — 표준 라이브러리만 쓴다.
`--dump-dom` 을 안 쓰는 이유(2026-09-05 macOS · Chrome 152 실측 — 표는
docs/m2-test-scenarios-python.md §2): `--dump-dom` 은 `load` 시점, 즉 첫 `/api/status` 응답이 오기
**전**의 DOM 을 찍고,
`--virtual-time-budget` · `--timeout` · `--screenshot` 은 `?poll=1`(SSE 없음) 인 진짜 앱에서도
60초 넘게 끝나지 않았다. CDP 로는 「큐 행이 그려질 때까지」를 마감 안에서 기다린 뒤 outerHTML 을
읽고, 같은 세션에서 `querySelector` 로 구조를 묻고 스크린샷도 찍는다. 페이지는 `?poll=1` 로
연다(열린 `EventSource` 는 headless Chrome 의 종료를 막는다 — 코디네이터 확인).

잡 배치: `slow`(20초) 잡 하나 running(lane 1) + 다른 트리의 `slow` 잡 하나 queued(1st in line).
캡처는 몇 초면 끝나므로 20초 안에 든다 — 캡처 뒤 잡이 아직 running 인지 다시 확인해 타이밍 실패를
명확한 메시지로 만든다. 테스트가 끝나면 두 잡을 취소해 teardown 을 빠르게 한다.
"""

import base64
import json
import os
import re
import select
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from test_server import Server
from test_server_m1 import StubSampler, host_sample, status_until

CHROME_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",)
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
OTHER_TREE = "ab" * 32


def find_chrome() -> str | None:
    """`RCM_CHROME` → macOS 앱 번들 → PATH 의 이름들. 없으면 None."""
    env = os.environ.get("RCM_CHROME")
    for p in ((env,) if env else ()) + CHROME_PATHS:
        if p and Path(p).is_file():
            return p
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


CHROME = find_chrome()
pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome binary found (set RCM_CHROME)")


# ── Chrome (CDP over pipe) ───────────────────────────────────────────────────


class Chrome:
    """headless Chrome 한 개. `with` 로 열고 닫는다. 모든 호출에 마감이 있다 — 멈추면 실패다."""

    FLAGS = (
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        "--disable-extensions",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
    )

    def __init__(self, profile: Path, *, window: str):
        profile.mkdir(parents=True, exist_ok=True)
        child_r, self.w_fd = os.pipe()
        self.r_fd, child_w = os.pipe()
        cmd = [
            CHROME,
            *self.FLAGS,
            f"--window-size={window}",
            f"--user-data-dir={profile}",
            "--remote-debugging-pipe",
            "about:blank",
        ]
        # Chrome 은 fd 3·4 를 고정으로 쓴다. preexec_fn 대신 sh 리다이렉션으로 맞춘다(fork-safe).
        wrapper = f'exec "$0" "$@" 3<&{child_r} 4>&{child_w}'
        self.stderr = open(profile / "chrome.err", "wb")
        self.proc = subprocess.Popen(
            ["sh", "-c", wrapper, *cmd],
            pass_fds=(child_r, child_w),
            stdout=subprocess.DEVNULL,
            stderr=self.stderr,
        )
        os.close(child_r)
        os.close(child_w)
        self.buf = b""
        self.next_id = 0
        self.session: str | None = None
        self.session = self._attach_first_page(timeout=15.0)
        self.call("Page.enable")

    def __enter__(self) -> "Chrome":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # 전송 ---------------------------------------------------------------------

    def _read_msg(self, deadline: float) -> dict[str, Any]:
        while b"\0" not in self.buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"CDP: no reply before the deadline; {self._stderr_tail()}")
            ready, _, _ = select.select([self.r_fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self.r_fd, 1 << 16)
            if not chunk:
                raise AssertionError(f"CDP: Chrome closed the pipe; {self._stderr_tail()}")
            self.buf += chunk
        raw, _, self.buf = self.buf.partition(b"\0")
        return json.loads(raw)

    def call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 15.0
    ) -> dict[str, Any]:
        """CDP 메서드 하나. 페이지 세션이 붙어 있으면 그 세션으로 보낸다. 이벤트는 버린다."""
        self.next_id += 1
        msg: dict[str, Any] = {"id": self.next_id, "method": method, "params": params or {}}
        if self.session:
            msg["sessionId"] = self.session
        os.write(self.w_fd, json.dumps(msg).encode() + b"\0")
        deadline = time.monotonic() + timeout
        while True:
            m = self._read_msg(deadline)
            if m.get("id") == self.next_id:
                if "error" in m:
                    raise RuntimeError(f"CDP {method}: {m['error']}")
                return m.get("result", {})

    def _attach_first_page(self, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while True:
            targets = self.call("Target.getTargets")["targetInfos"]
            pages = [t for t in targets if t["type"] == "page"]
            if pages:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("CDP: Chrome never created a page target")
            time.sleep(0.05)
        params = {"targetId": pages[0]["targetId"], "flatten": True}
        return self.call("Target.attachToTarget", params)["sessionId"]

    def _stderr_tail(self) -> str:
        try:
            self.stderr.flush()
            data = Path(self.stderr.name).read_bytes()
        except OSError:
            return "(no stderr)"
        return "chrome stderr: " + data[-600:].decode("utf-8", "replace")

    # 페이지 -------------------------------------------------------------------

    def eval(self, expression: str) -> Any:
        """JS 식 하나를 값으로. 예외면 AssertionError."""
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in r:
            raise AssertionError(f"JS threw in {expression!r}: {r['exceptionDetails'].get('text')}")
        return r["result"].get("value")

    def open(self, url: str, *, ready_js: str, timeout: float = 15.0) -> str:
        """url 로 가서 `ready_js` 가 true 가 될 때까지 기다린 뒤 outerHTML 을 돌려준다."""
        self.call("Page.navigate", {"url": url})
        deadline = time.monotonic() + timeout
        last: Any = None
        while True:
            try:
                last = self.eval(ready_js)
            except RuntimeError:  # 이동 중이라 실행 컨텍스트가 아직 없다
                last = None
            if last is True:
                break
            if time.monotonic() >= deadline:
                body = self.eval("document.body ? document.body.innerText.slice(0, 1200) : ''")
                raise AssertionError(
                    f"page not ready within {timeout}s: {ready_js!r} → {last!r}\n"
                    f"--- body ---\n{body}"
                )
            time.sleep(0.1)
        return self.eval("document.documentElement.outerHTML")

    def screenshot(self, path: Path) -> int:
        """뷰포트 PNG 를 path 에 쓰고 바이트 수를 돌려준다."""
        shot = self.call("Page.captureScreenshot", {"format": "png"}, timeout=30.0)
        data = base64.b64decode(shot["data"])
        path.write_bytes(data)
        return len(data)

    def close(self) -> None:
        try:
            self.session = None
            self.call("Browser.close", timeout=5.0)
        except (AssertionError, RuntimeError, OSError):
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for fd in (self.w_fd, self.r_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        self.stderr.close()


# ── 서버 배치 ────────────────────────────────────────────────────────────────


class FreshStubSampler(StubSampler):
    """부를 때마다 2초 전 표본을 새로 만든다 — 캡처가 늦어져도 `stale` 로 바뀌지 않는다."""

    def __init__(self) -> None:
        super().__init__([])

    def latest(self):
        return [host_sample(datetime.now(UTC), age_seconds=2)], None


@dataclass
class Scene:
    srv: Server
    running: int  # alice · slow · lane 1
    queued: int  # bob · slow · 1st in line

    @property
    def url(self) -> str:
        # `?poll=1`: SSE 대신 10초 폴링. 열린 EventSource 는 headless Chrome 의 종료를 막는다.
        return f"http://127.0.0.1:{self.srv.port}/?poll=1"

    def ready_js(self) -> str:
        """두 잡의 행과 호스트 CPU 막대가 다 그려졌는가."""
        return (
            f"[{self.running}, {self.queued}].every(id => "
            "document.querySelector('#queue [data-job=\"' + id + '\"]') !== null) && "
            "document.querySelector('#host .meter[data-metric=\"cpu\"]') !== null"
        )

    def assert_still_running(self) -> None:
        j = self.srv.store.get_job(self.running)
        assert j.state == "running", (
            f"job {self.running} is {j.state} — it finished before the DOM was captured "
            "(capture took too long for the 20 s `slow` preset)"
        )


@pytest.fixture
def scene(tmp_path):
    srv = Server(tmp_path, workers=True)
    # start() 가 만든 진짜 샘플러를 덮는다. shutdown 은 stop 이벤트로 하므로 스텁이어도 된다.
    srv.app.sampler = FreshStubSampler()
    jobs: list[tuple[int, str]] = []
    try:
        running = srv.submit(preset="slow")[1]["job_id"]
        jobs.append((running, "alice"))
        assert srv.upload(running)[0] == 200
        srv.wait_state(running, "running")
        queued = srv.submit(token="bob", preset="slow", tree_hash=OTHER_TREE)[1]["job_id"]
        jobs.append((queued, "bob"))
        assert srv.upload(queued, token="bob")[0] == 200

        def settled(doc: dict) -> bool:
            rows = {r["id"]: r for r in doc["pools"][0]["queue"] or []}
            return (
                rows.get(running, {}).get("state") == "running"
                and rows.get(queued, {}).get("position") == 1
                and bool(doc["pools"][0]["hosts"])
            )

        doc = status_until(srv, settled, timeout=5.0)
        assert settled(doc), doc["pools"][0]
        rows = {r["id"]: r for r in doc["pools"][0]["queue"]}
        assert rows[running]["lane"] == 1 and rows[running]["reason"] == "running"
        assert rows[queued]["reason"] == "waiting_for_lane"
        assert rows[queued]["estimate"]["confidence"] == "low"
        assert doc["pools"][0]["hosts"][0]["cpu"]["busy"] == 21.0
        yield Scene(srv, running, queued)
    finally:
        for jid, token in jobs:
            srv.req("POST", f"/jobs/{jid}/cancel", token=token, json_body={})
        srv.close()


def tag_with_id(dom: str, element_id: str) -> str:
    m = re.search(rf'<[a-z]+[^>]*\bid="{re.escape(element_id)}"[^>]*>', dom)
    assert m, f'no element with id="{element_id}"'
    return m.group(0)


# ── 테스트 ───────────────────────────────────────────────────────────────────


def test_desktop_dom_shows_running_and_queued_jobs(scene, tmp_path):
    with Chrome(tmp_path / "chrome-desktop", window="1240,900") as c:
        dom = c.open(scene.url, ready_js=scene.ready_js())
        # 구조 질문은 살아 있는 DOM 에 — 직렬화 문자열을 파싱하지 않는다
        conf_texts = c.eval(
            "[...document.querySelectorAll('#queue .conf')].map(e => e.textContent.trim())"
        )
        banner_hidden = c.eval(
            "(() => { const b = document.getElementById('banner-lost'); "
            "return b !== null && (b.hidden || getComputedStyle(b).display === 'none'); })()"
        )
        visible_text = c.eval("document.body.innerText")
        summary_labels = c.eval(
            "['23','24','25'].map(n => { "
            "const s = document.querySelector('#summary [data-c=\"' + n + '\"]'); "
            "return s ? s.textContent : null; })"
        )
        # ?poll=1 이면 /events 를 열지 않는다 — 열린 SSE 는 headless Chrome 을 못 끝나게 한다
        sse_open = scene.srv.req("GET", "/api/status")[1]["server"]["sse_connections"]
    scene.assert_still_running()
    assert sse_open == 0, "?poll=1 page opened an EventSource"

    # 큐 행 (§4 queue)
    assert f'data-job="{scene.running}"' in dom
    assert f'data-job="{scene.queued}"' in dom
    assert re.search(r'<table[^>]*class="[^"]*\bq\b', dom), "table.q missing"
    assert re.search(r'<td[^>]*class="[^"]*\breason\b', dom), "td.reason missing"
    assert "running · lane 1" in dom
    assert "1st in line" in dom
    assert "waiting for lane" in dom
    assert conf_texts and any("low" in t for t in conf_texts), conf_texts  # low · preset|default
    assert not any(re.search(r"\bundefined\b|\bNaN\b|\bnull\b", t) for t in conf_texts), conf_texts

    # 요약 세 칸 (§4 summary · 항목 23·24·25)
    assert all(summary_labels), summary_labels
    assert "Your jobs" in summary_labels[0]
    assert "Not moving" in summary_labels[1]
    assert "Host pressure" in summary_labels[2]

    # 호스트 (§4 host) — 스텁 표본의 CPU busy 21.0 → "21%"
    assert 'data-metric="cpu"' in dom
    assert "21%" in visible_text, visible_text[:600]

    # 오버레이 (§4 overlays) — 있어야 하고, 연결이 살아 있으니 Lost 띠는 숨겨져 있다
    banner = tag_with_id(dom, "banner-lost")
    assert 'role="alert"' in banner, banner
    assert re.search(r'\shidden(=""|="hidden"|\s|>)', banner), banner
    assert banner_hidden is True
    assert "lost connection" not in visible_text.lower()  # innerText 는 CSS 대소문자 변환을 따른다
    for element_id in ("hdr", "summary", "queue", "host", "recent", "drawer", "toast"):
        tag_with_id(dom, element_id)
    assert tag_with_id(dom, "tok-dialog").startswith("<dialog")
    assert tag_with_id(dom, "cancel-dialog").startswith("<dialog")
    assert re.search(r"<title>[^<]+</title>", dom)
    assert "undefined" not in visible_text and "NaN" not in visible_text, visible_text[:600]


def test_mobile_viewport_keeps_queue_content(scene, tmp_path):
    with Chrome(tmp_path / "chrome-mobile", window="390,844") as c:
        dom = c.open(scene.url, ready_js=scene.ready_js())
        width = c.eval("window.innerWidth")
        visible_text = c.eval("document.body.innerText")
        summary_text = c.eval("document.getElementById('summary').textContent")
    scene.assert_still_running()
    # 720px 미만이 카드 레이아웃 구간(§4 모바일)
    assert width <= 720, f"viewport is {width}px wide — not the mobile layout"
    assert f'data-job="{scene.running}"' in dom
    assert f'data-job="{scene.queued}"' in dom
    assert "1st in line" in visible_text
    assert "21%" in visible_text
    # 라벨은 textContent 로 — innerText 는 CSS `text-transform: uppercase` 를 반영한다
    for label in ("Your jobs", "Not moving", "Host pressure"):
        assert label in summary_text, (label, summary_text)
    assert "lost connection" not in visible_text.lower()


def test_screenshot_for_owner_review(scene, tmp_path):
    # TODO(m2): `Lost connection` 띠는 브라우저로 못 본다 — `/api/status` 가 상대 경로라
    # 서버를 내리면 페이지 자체가 안 열리고, file:// 로 열면 API 가 없다. 30초 무응답 → lost
    # 전이는 Node 의 `rcm.connection` 상태기계 테스트(tests/web)가 덮는다. 여기서는
    # 스크린샷만 남긴다(오너 확인용).
    out_dir = Path(os.environ.get("RCM_SHOT_DIR") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "queue.png"
    with Chrome(tmp_path / "chrome-shot", window="1240,1400") as c:
        c.open(scene.url, ready_js=scene.ready_js())
        size = c.screenshot(path)
    scene.assert_still_running()
    print("screenshot:", path)
    assert path.is_file() and path.stat().st_size == size
    assert size > 10_000, f"screenshot is only {size} bytes — blank page?"
