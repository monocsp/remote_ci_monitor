"""웹 UI(M2) — 진짜 서버(in-process · 워커 on · 가짜 샘플러) 를 headless Chrome 으로 열어 DOM 계약
(docs/m2-workplan.md §4) 을 확인한다. Chrome 이 없으면 전부 skip — 단 `RCM_CHROME` 이 설정된
곳(CI 의 ubuntu 잡)에서는 Chrome 이 **필수**라 못 찾으면 skip 이 아니라 실패다.

이 모듈은 렌더 경로의 스모크이기도 하다(결정 79 · docs/gate-replay-fixes-workplan.md §3 I5):
표본에 `disk`, 상태 문서에 `server.job_storage` 가 있는 **실배치 모양**으로 그리고, 페이지
스크립트보다 먼저 설치한 `error`·`unhandledrejection` 수집기가 0건임을 단언한다. 2026-09-10 실배치
재현에서 웹을 통째로 죽인 ReferenceError(B1)는 표본에 `disk` 가 없어 그 줄이 실행되지 않은 탓에
초록이었다.

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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import parse_preset
from test_server import PRESETS, Server, sh
from test_server_m1 import StubSampler, host_sample, status_until

CHROME_PATHS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",)
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
OTHER_TREE = "ab" * 32


def find_chrome() -> str | None:
    """`RCM_CHROME`(파일 경로 또는 PATH 의 이름) → macOS 앱 번들 → PATH 의 이름들. 없으면 None.

    `RCM_CHROME` 이 설정돼 있으면 그 값만 본다 — 안 풀리면 다른 후보로 넘어가지 않는다. 설정한
    쪽은 「여기엔 Chrome 이 있어야 한다」고 말한 것이고, 그 약속이 깨진 것을 조용히 skip 으로
    덮으면 이 모듈이 지키는 것이 없어진다.
    """
    env = os.environ.get("RCM_CHROME")
    if env:
        return env if Path(env).is_file() else shutil.which(env)
    for p in CHROME_PATHS:
        if Path(p).is_file():
            return p
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


CHROME = find_chrome()
CHROME_REQUIRED = bool(os.environ.get("RCM_CHROME"))
pytestmark = pytest.mark.skipif(
    CHROME is None and not CHROME_REQUIRED, reason="no Chrome binary found (set RCM_CHROME)"
)


@pytest.fixture(autouse=True)
def chrome_is_required_where_rcm_chrome_is_set() -> None:
    """`RCM_CHROME` 이 있는데 Chrome 이 없으면 **실패**다(skip 이 아니다). CI 의 ubuntu 잡이 이
    길이다 — 러너 이미지가 바뀌어 Chrome 이 사라지면 초록이 아니라 빨강이어야 한다."""
    if CHROME is None and CHROME_REQUIRED:
        pytest.fail(
            f"RCM_CHROME={os.environ['RCM_CHROME']!r} is neither a file nor a name on PATH — "
            "Chrome is required where RCM_CHROME is set, so this is a failure, not a skip"
        )


# ── Chrome (CDP over pipe) ───────────────────────────────────────────────────


# 페이지 스크립트보다 먼저 설치되는 예외 수집기(`Page.addScriptToEvaluateOnNewDocument`).
# 메시지만 모은다 — 스택은 실패 메시지에 필요 없고, 원인 한 줄이면 충분하다.
PAGE_ERROR_COLLECTOR_JS = """
window.__rcmErrors = [];
window.addEventListener('error', function (e) {
  var stack = e.error && e.error.stack ? e.error.stack.split('\\n', 2).join(' @ ') : null;
  window.__rcmErrors.push(String(stack || e.message || e));
});
window.addEventListener('unhandledrejection', function (e) {
  var r = e.reason;
  window.__rcmErrors.push('unhandledrejection: ' + String(r && r.message ? r.message : r));
});
"""


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
        # Chrome 은 fd 3·4 를 고정으로 쓴다. 자식에서 dup2 로 맞춘다 — sh 리다이렉션은 dash
        # (ubuntu 의 sh)가 두 자리 fd(`3<&10`)를 못 받아 "Bad fd number" 로 죽는다. preexec_fn 은
        # close_fds 보다 먼저 돌므로 3·4 도 pass_fds 에 넣어야 닫히지 않는다.
        self.stderr = open(profile / "chrome.err", "wb")

        def _wire_pipe_fds() -> None:  # pragma: no cover — 자식 프로세스에서만 돈다
            os.dup2(child_r, 3)
            os.dup2(child_w, 4)

        self.proc = subprocess.Popen(
            cmd,
            pass_fds=(child_r, child_w, 3, 4),
            preexec_fn=_wire_pipe_fds,
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
        # 페이지 스크립트보다 **먼저** 도는 수집기. 터진 render() 는 타임아웃이 아니라 이 목록으로
        # 드러난다 — 「page not ready within 15s」보다 「ReferenceError: local is not defined」가
        # 원인이다. `page_errors()` 로 읽고, 테스트는 0건을 단언한다.
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source": PAGE_ERROR_COLLECTOR_JS})

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

    def page_errors(self) -> list[str]:
        """페이지가 열린 뒤 지금까지의 `error`·`unhandledrejection` 메시지. 이동 중이면 빈 목록."""
        try:
            found = self.eval("Array.isArray(window.__rcmErrors) ? window.__rcmErrors : []")
        except RuntimeError:  # 실행 컨텍스트가 아직 없다
            return []
        return list(found) if isinstance(found, list) else []

    def eval(self, expression: str) -> Any:
        """JS 식 하나를 값으로. 예외면 AssertionError."""
        r = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in r:
            raise AssertionError(f"JS threw in {expression!r}: {r['exceptionDetails'].get('text')}")
        return r["result"].get("value")

    def viewport(self, width: int, height: int = 844, *, mobile: bool = False) -> None:
        """뷰포트를 CDP 로 덮어쓴다 — `--window-size` 만으로는 폰 폭이 안 나온다.

        macOS 의 크롬 창은 500px 밑으로 줄지 않아 `--window-size=390,844` 로 띄워도 실제 뷰포트는
        500 이다(docs/m5d-workplan.md §4.7). `tools/screenshots/capture.py` 의 폰 사진과 같은
        방법으로 덮어쓴다. 덮어쓴 값은 같은 세션의 다음 이동에도 남는다.
        """
        self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile},
        )

    def emulate_media(self, features: dict[str, str]) -> None:
        """미디어 특성을 흉내낸다(예: `{"prefers-reduced-motion": "reduce"}`).

        헤드리스 크롬에는 OS 설정이 없어 이 방법 말고는 `prefers-reduced-motion` 을 켤 수 없다.
        """
        params = {"features": [{"name": n, "value": v} for n, v in features.items()]}
        self.call("Emulation.setEmulatedMedia", params)

    def open(self, url: str, *, ready_js: str, timeout: float = 15.0) -> str:
        """url 로 가서 `ready_js` 가 true 가 될 때까지 기다린 뒤 outerHTML 을 돌려준다.

        기다리는 동안 페이지 예외가 하나라도 잡히면 **바로** 실패한다 — 마감을 채우지 않는다.
        """
        self.call("Page.navigate", {"url": url})
        deadline = time.monotonic() + timeout
        last: Any = None
        while True:
            try:
                last = self.eval(ready_js)
            except RuntimeError:  # 이동 중이라 실행 컨텍스트가 아직 없다
                last = None
            errors = self.page_errors()
            if errors:
                raise AssertionError(f"page raised while loading {url}: {errors}")
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


# 실배치의 표본에는 `disk` 가 있다(`hostsample.py` 가 데이터 디렉터리의 파일 시스템을 잰다). 그
# 칸이 있어야 `hostCardHtml` 의 디스크 막대와 「rcm 데이터 …」 줄이 실행된다 — B1 이 산 자리다.
DISK = {
    "used_bytes": 610_000_000_000,
    "free_bytes": 384_000_000_000,
    "total_bytes": 994_000_000_000,
    "path": "/Users/ci/.local/share/rcm",
}


class FreshStubSampler(StubSampler):
    """부를 때마다 2초 전 표본을 새로 만든다 — 캡처가 늦어져도 `stale` 로 바뀌지 않는다.
    표본은 실배치 모양이다: `disk` 가 있다."""

    def __init__(self) -> None:
        super().__init__([])

    def latest(self):
        return [replace(host_sample(datetime.now(UTC), age_seconds=2), disk=DISK)], None


@dataclass
class Scene:
    srv: Server
    running: int  # alice · slow · lane 1
    queued: int  # bob · slow · 1st in line

    @property
    def url(self) -> str:
        # `?poll=1`: SSE 대신 10초 폴링. 열린 EventSource 는 headless Chrome 의 종료를 막는다.
        # `lang=en`: 화면 기본은 한국어다(결정 38). 아래 단언은 영어 문면을 잠그므로 명시한다.
        return f"http://127.0.0.1:{self.srv.port}/?poll=1&lang=en"

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
    assert re.search(r"<title[^>]*>[^<]+</title>", dom)  # M5d-1: data-i18n 속성이 붙는다
    assert "undefined" not in visible_text and "NaN" not in visible_text, visible_text[:600]


# 큐 행의 키·요청자 칸이 폰에서도 **글자와 폭을 둘 다** 가지는가. `max-width: 0` 이 표 아닌
# 레이아웃에 새면 칸이 통째로 사라지는데(§4.7 회귀) 글자만 보면 안 걸린다 — 폭도 같이 잰다.
ROW_CELLS_JS = """
(() => Object.fromEntries([...document.querySelectorAll('#queue tr[data-job]')]
  .filter(tr => !tr.classList.contains('expanded'))
  .map(tr => [tr.dataset.job, Object.fromEntries(['key', 'requester'].map(name => {
    const td = tr.querySelector('td.' + name);
    return [name, td === null ? null : {
      text: td.textContent.replace(/\\s+/g, ' ').trim(),
      width: Math.round(td.getBoundingClientRect().width),
      height: Math.round(td.getBoundingClientRect().height),
    }];
  }))])))()
"""


def test_mobile_viewport_keeps_queue_content(scene, tmp_path):
    # `--window-size` 는 macOS 에서 500px 로 걸린다 — 실제 폰 폭은 `Chrome.viewport()` 가 만든다
    with Chrome(tmp_path / "chrome-mobile", window="900,844") as c:
        c.viewport(390, 844, mobile=True)
        dom = c.open(scene.url, ready_js=scene.ready_js())
        width = c.eval("window.innerWidth")
        visible_text = c.eval("document.body.innerText")
        summary_text = c.eval("document.getElementById('summary').textContent")
        cells = c.eval(ROW_CELLS_JS)
    scene.assert_still_running()
    # 720px 미만이 카드 레이아웃 구간(§4 모바일). 진짜 390 인지 못 박는다 —
    # `<= 720` 은 macOS 가 만들어 주는 500 도 통과시킨다
    assert width == 390, f"viewport is {width}px wide — the device metrics override did not take"
    assert f'data-job="{scene.running}"' in dom
    assert f'data-job="{scene.queued}"' in dom
    assert "1st in line" in visible_text
    assert "21%" in visible_text
    # 라벨은 textContent 로 — innerText 는 CSS `text-transform: uppercase` 를 반영한다
    for label in ("Your jobs", "Not moving", "Host pressure"):
        assert label in summary_text, (label, summary_text)
    assert "lost connection" not in visible_text.lower()
    # 폰에서도 키(프리셋 이름)와 요청자 칸이 남아 있다 — 글자도, 자리도
    for job_id in (scene.running, scene.queued):
        row = cells.get(str(job_id))
        assert row, (job_id, cells)
        for name in ("key", "requester"):
            cell = row[name]
            assert cell is not None, f"job {job_id}: td.{name} is missing at 390px"
            assert cell["text"] and cell["text"] != "—", f"job {job_id}: td.{name} is empty: {cell}"
            assert cell["width"] > 0 and cell["height"] > 0, (
                f"job {job_id}: td.{name} has no box at 390px ({cell}) — a `max-width: 0` "
                "meant for the table layout leaked into the phone cards"
            )
    assert "slow" in cells[str(scene.running)]["key"]["text"], cells
    assert "alice" in cells[str(scene.running)]["requester"]["text"], cells
    assert "bob" in cells[str(scene.queued)]["requester"]["text"], cells


def test_korean_is_the_default_and_the_switch_flips_the_page(scene, tmp_path):
    """결정 36·38 — 주소에 `lang` 이 없으면 한국어로 뜨고, 오른쪽 위 버튼이 영어로 바꾼다.

    다시 그려지지 않는 정적 문자열(요약 라벨 · 섹션 제목)까지 바뀌는지가 핵심이다 —
    화면 본문은 `render()` 가 새로 만들지만 그 라벨들은 아무도 다시 쓰지 않는다.
    """
    url = f"http://127.0.0.1:{scene.srv.port}/?poll=1"  # lang 없음 = 기본
    with Chrome(tmp_path / "chrome-lang", window="1280,900") as c:
        c.open(url, ready_js=scene.ready_js(), timeout=30)
        lang = c.eval("document.documentElement.lang")
        summary = c.eval("document.getElementById('summary').textContent")
        queue_head = c.eval("document.querySelector('#queue .s-h .t').textContent")
        body = c.eval("document.body.innerText")
        assert lang == "ko", "기본 언어가 한국어가 아니다"
        assert "내 잡" in summary and "안 움직이는 것" in summary, summary
        assert queue_head == "큐", queue_head
        assert "실행 중" in body, body[:400]
        assert "undefined" not in body and "NaN" not in body
        # 전환 — 정적 라벨까지 따라와야 한다
        c.eval("document.getElementById('lang-btn').click()")
        after_lang = c.eval("document.documentElement.lang")
        after_summary = c.eval("document.getElementById('summary').textContent")
        after_queue = c.eval("document.querySelector('#queue .s-h .t').textContent")
        after_body = c.eval("document.body.innerText")
        stored = c.eval("localStorage.getItem('rcm.lang')")
        assert after_lang == "en"
        assert "Your jobs" in after_summary and "Not moving" in after_summary, after_summary
        assert after_queue == "Queue", after_queue
        assert "running" in after_body
        assert "내 잡" not in after_summary, "한국어 라벨이 남았다"
        assert stored == "en", "고른 언어가 브라우저에 남지 않는다"
        assert "undefined" not in after_body and "NaN" not in after_body


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


def test_recent_shows_another_pools_finished_job_when_default_has_none(tmp_path):
    """기본 풀에 완료 잡이 없어도 다른 풀의 완료 잡은 Recent 에 보인다 — 첫 풀의 recent 만 보고
    「No completed jobs yet」으로 끝내던 회귀(M5b-1 격리 검증). `pool linux` 칩도 같이 본다."""
    srv = Server(tmp_path, workers=False)
    try:
        srv.cfg.presets = tuple(
            parse_preset(p) for p in [*PRESETS, sh("lin", "echo lin", pool="linux")]
        )
        jid = srv.submit(preset="lin")[1]["job_id"]
        assert srv.upload(jid)[0] == 200
        assert srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})[0] < 300
        doc = status_until(
            srv,
            lambda d: (
                len(d["pools"]) == 2 and any(r["id"] == jid for r in d["pools"][1]["recent"] or [])
            ),
            timeout=5.0,
        )
        assert doc["pools"][0]["recent"] == [] and doc["pools"][1]["name"] == "linux", doc["pools"]
        with Chrome(tmp_path / "chrome-recent", window="1240,900") as c:
            c.open(
                f"http://127.0.0.1:{srv.port}/?poll=1&lang=en",
                ready_js=f"document.querySelector('#recent [data-job=\"{jid}\"]') !== null",
            )
            row_text = c.eval(f"document.querySelector('#recent [data-job=\"{jid}\"]').textContent")
            recent_text = c.eval("document.querySelector('[data-recent-body]').textContent")
    finally:
        srv.close()
    assert "cancelled" in row_text and "pool linux" in row_text, row_text
    assert "No completed jobs yet" not in recent_text


def job_storage_of(doc: dict) -> dict:
    """상태 문서의 `server.job_storage`. 없으면 빈 dict — 단언은 부르는 쪽이 한다."""
    return (doc.get("server") or {}).get("job_storage") or {}


def test_local_host_card_draws_the_disk_and_the_rcm_data_line_without_page_errors(tmp_path):
    """I5(결정 79): 실배치 모양 — 로컬 표본에 `disk`, 상태 문서에 `server.job_storage`(청소기가
    첫 sweep 에서 잰 값) — 으로 열면 서버 카드에 디스크 막대와 「rcm data …」/「rcm 데이터 …」 줄이
    그려지고, 그 아래 Recent 절까지 render() 가 끝나며, 페이지 예외는 **0건**이다.

    2026-09-10 실배치 재현(B1): `hostCardHtml` 이 정의 안 된 `local` 을 읽어 `disk` 가 있는 모든
    로컬 배치에서 render() 가 ReferenceError 로 끊겼다 — 큐 표까지만 그려지고 Recent 가 비고
    갱신 루프가 죽어 30초 뒤 「연결이 끊겼습니다」 띠가 떴다. 이 테스트는 그 자리를 그대로
    지난다: `local` 을 되돌리면 수집기가 그 ReferenceError 를 잡아 빨개진다."""
    srv = Server(tmp_path, workers=True)
    try:
        srv.app.sampler = FreshStubSampler()

        def settled(d: dict) -> bool:
            hosts = d["pools"][0]["hosts"]
            return (
                bool(hosts)
                and hosts[0].get("disk") is not None
                and isinstance(job_storage_of(d).get("volume_bytes"), int)
                and job_storage_of(d).get("next_sweep_at") is not None
            )

        doc = status_until(srv, settled, timeout=5.0)
        assert settled(doc), (doc["pools"][0]["hosts"], job_storage_of(doc))
        (host,) = doc["pools"][0]["hosts"]
        assert host["source"] == "local" and host["disk"]["total_bytes"] == DISK["total_bytes"]
        seen: dict[str, dict[str, Any]] = {}
        with Chrome(tmp_path / "chrome-storage", window="1240,1400") as c:
            for lang in ("en", "ko"):
                c.open(
                    f"http://127.0.0.1:{srv.port}/?poll=1&lang={lang}",
                    ready_js="document.querySelector('#host .hostcard .substat') !== null",
                )
                seen[lang] = {
                    "disk_meters": c.eval(
                        "document.querySelectorAll('#host .hostcard .meter[data-metric=\"disk\"]')"
                        ".length"
                    ),
                    "substats": c.eval(
                        "[...document.querySelectorAll('#host .hostcard .substat')]"
                        ".map(e => e.textContent)"
                    ),
                    "recent": c.eval("document.querySelector('[data-recent-body]').textContent"),
                    "body": c.eval("document.body.innerText"),
                    "errors": c.page_errors(),
                }
    finally:
        srv.close()

    phrase = {"en": ("rcm data ", " · next sweep "), "ko": ("rcm 데이터 ", " · 다음 청소 ")}
    for lang, got in seen.items():
        assert got["errors"] == [], (lang, got["errors"])
        assert got["disk_meters"] == 1, (lang, got["disk_meters"])
        (line,) = got["substats"]
        head, sweep = phrase[lang]
        assert line.startswith(head) and sweep in line, (lang, line)
        assert "undefined" not in line and "NaN" not in line and "—" not in line, (lang, line)
        # Recent 절까지 그려졌다 — B1 은 정확히 여기서 끊겼다(잡이 없으니 「없음」 문구다)
        assert got["recent"].strip(), (lang, got["recent"])
        assert "undefined" not in got["body"] and "NaN" not in got["body"], got["body"][:600]


def test_remote_worker_sample_is_a_host_card_and_recent_has_no_pool_host_header(tmp_path):
    """M5b-4 §2: 원격 워커의 호스트 표본은 **Host 절**의 카드(제목 `build-02 · pool linux`)로
    보이고, Recent 절 밑에 붙던 풀별 host 블록(`POOL LINUX[ · NO WORKERS][ · NO HOST SAMPLE]`
    헤더)은 없다.
    기본 풀의 로컬 카드는 오늘 그대로(제목 = 호스트 이름). 배치: 로컬 표본(스텁 샘플러) + linux
    풀 워커 `build-02` 가 heartbeat 로 표본을 보낸 상태 — 잡은 없다."""
    # 이 테스트가 덧붙여진 M5b-4 에서만 쓰는 이름이라 여기서 들여온다
    from remote_ci_monitor import __version__
    from test_worker_api import SAMPLE

    # workers=True: 청소기가 돌아야 `server.job_storage` 가 재어진다 — 그래야 「rcm data …」 줄이
    # 서버 카드에만 붙는 것을 볼 수 있다. 잡은 없으니 워커는 놀 뿐이다.
    srv = Server(tmp_path, workers=True)
    try:
        srv.app.sampler = FreshStubSampler()
        srv.tokens["build-02"] = srv.store.add_token(
            "build-02", admin=False, now=datetime.now(UTC), kind="worker"
        )
        reg = {"pool": "linux", "lanes": 1, "host_name": "build-02.local", "version": __version__}
        status, body = srv.req("POST", "/worker/register", token="build-02", json_body=reg)
        assert status == 200, body
        # 워커 표본에도 `disk` — 디스크 막대는 두 카드 모두에, 「rcm 데이터 …」 줄은 서버 자신의
        # 카드에만 있어야 한다(데이터 디렉터리의 부피는 서버의 사실이다).
        worker_sample = {**SAMPLE, "disk": {**DISK, "path": "/var/lib/rcm"}}
        status, body = srv.req(
            "POST",
            "/worker/heartbeat",
            token="build-02",
            json_body={"host_sample": worker_sample},
        )
        assert status == 200, body

        def settled(d: dict) -> bool:
            return (
                len(d["pools"]) == 2
                and bool(d["pools"][0]["hosts"])
                and bool(d["pools"][1]["hosts"])
                and isinstance(job_storage_of(d).get("volume_bytes"), int)
            )

        doc = status_until(srv, settled, timeout=5.0)
        assert settled(doc), doc["pools"]
        local, linux = doc["pools"]
        assert local["hosts"][0]["name"] == "macmini" and local["hosts"][0]["source"] == "local"
        assert linux["name"] == "linux" and linux["queue"] == [] and linux["lanes"] == 1
        (sample,) = linux["hosts"]
        assert sample["name"] == "build-02" and sample["source"] == "worker", sample
        assert sample["disk"]["total_bytes"] == DISK["total_bytes"], sample["disk"]
        with Chrome(tmp_path / "chrome-hosts", window="1240,1400") as c:
            c.open(
                f"http://127.0.0.1:{srv.port}/?poll=1&lang=en",
                ready_js="document.querySelector('#host .meter[data-metric=\"cpu\"]') !== null",
            )
            disk_meters = c.eval(
                "document.querySelectorAll('#host .hostcard .meter[data-metric=\"disk\"]').length"
            )
            # 「rcm data …」 줄이 어느 카드에 있나 — 카드 제목(`.hn` 의 첫 글자 마디)으로 답한다
            substat_cards = c.eval(
                "[...document.querySelectorAll('#host .hostcard')]"
                ".filter(card => card.querySelector('.substat'))"
                ".map(card => card.querySelector('.hn').firstChild.textContent.trim())"
            )
            page_errors = c.page_errors()
            # 카드 제목 = `.hn` 에서 나이·OS 부제(`.age`)를 뺀 글자
            card_titles = c.eval(
                "[...document.querySelectorAll('#host .hostcard')].map(card => { "
                "const hn = card.querySelector('.hn'); if (!hn) return null; "
                "const k = hn.cloneNode(true); "
                "k.querySelectorAll('.age').forEach(a => a.remove()); "
                "return k.textContent.replace(/\\s+/g, ' ').trim(); })"
            )
            host_text = c.eval("document.getElementById('host').textContent")
            recent_inner = c.eval("document.getElementById('recent').innerText")
            recent_text = c.eval("document.getElementById('recent').textContent")
            pool_heads_outside_queue = c.eval(
                "[...document.querySelectorAll('.pool-h')].filter(e => !e.closest('#queue')).length"
            )
            visible_text = c.eval("document.body.innerText")
    finally:
        srv.close()

    # Host 절: 로컬 카드는 오늘 그대로, 원격 표본은 `build-02 · pool linux` 카드로
    assert "macmini" in card_titles, card_titles
    assert "build-02 · pool linux" in card_titles, card_titles
    assert card_titles.index("macmini") < card_titles.index("build-02 · pool linux"), card_titles
    assert "12%" in host_text or "13%" in host_text, host_text[:600]  # SAMPLE 의 CPU busy 12.5
    # 디스크 막대는 카드마다, 「rcm data …」 줄은 서버 자신의 카드(로컬 표본)에만
    assert disk_meters == 2, disk_meters
    assert substat_cards == ["macmini"], substat_cards
    assert page_errors == [], page_errors
    # Recent 절 밑의 풀별 host 헤더는 없다(innerText 는 CSS uppercase 를 따른다 → `POOL LINUX`)
    assert pool_heads_outside_queue == 0
    assert "POOL LINUX" not in recent_inner, recent_inner
    assert "pool linux" not in recent_text.lower(), recent_text
    assert "no host sample" not in recent_text.lower(), recent_text
    assert "NO HOST SAMPLE" not in visible_text and "NO WORKERS" not in visible_text
    assert "undefined" not in visible_text and "NaN" not in visible_text, visible_text[:600]


# ── 접힌 행 · 전체 진행 막대 · 최근 완료의 잡 번호 (2026-09-09 오너 요청) ──────────────

# 도는 행 하나에 대해: 펼침 상태 · 막대 · 이유 칸의 지금 스텝 · 저장된 펼침 목록.
FOLD_JS = """
(id => {
  const row = document.querySelector('#queue tr[data-job="' + id + '"]');
  const bar = document.querySelector('#queue tr.qbar[data-bar="' + id + '"]');
  const pbar = bar ? bar.querySelector('.pbar[role="progressbar"]') : null;
  const btn = document.querySelector('[data-toggle="' + id + '"]');
  return {
    expanded_rows: document.querySelectorAll('#queue tr.expanded').length,
    aria_expanded: btn ? btn.getAttribute('aria-expanded') : null,
    steps_blocks: document.querySelectorAll('#queue .steps').length,
    bars: document.querySelectorAll('#queue tr.qbar').length,
    bar: pbar === null ? null : {
      basis: pbar.getAttribute('data-basis'),
      cond: pbar.getAttribute('data-cond'),
      label: bar.querySelector('.plab').textContent.trim(),
      valuetext: pbar.getAttribute('aria-valuetext'),
      width: pbar.querySelector('i').getAttribute('style'),
      tick: bar.querySelector('.pwrap').getAttribute('data-tick'),
      expected: bar.querySelector('.pwrap').getAttribute('data-expected'),
      aria: pbar.outerHTML.slice(0, 400),
    },
    reason: row ? row.querySelector('td.reason').textContent.replace(/\\s+/g, ' ').trim() : null,
    stored: localStorage.getItem('rcm.expanded'),
  };
})(%d)
"""


def test_running_row_is_folded_and_carries_an_overall_bar(scene, tmp_path):
    """오너 요청(2026-09-09): 상세는 기본으로 접히고, 도는 잡은 전체 진행 막대로 보인다.

    접힌 채로도 「어디까지 왔나」(막대 + 퍼센트)와 「지금 뭘 하나」(이유 칸의 스텝)가 읽혀야
    한다 — 접기가 정보를 감추는 것이 아니라 자리만 줄이는 것이어야 접어 둘 수 있다.
    """
    with Chrome(tmp_path / "chrome-fold", window="1240,900") as c:
        c.open(scene.url, ready_js=scene.ready_js())
        folded = c.eval(FOLD_JS % scene.running)
        c.eval(f"document.querySelector('[data-toggle=\"{scene.running}\"]').click()")
        opened = c.eval(FOLD_JS % scene.running)
        c.eval(f"document.querySelector('[data-toggle=\"{scene.running}\"]').click()")
        closed = c.eval(FOLD_JS % scene.running)
        visible_text = c.eval("document.body.innerText")
    scene.assert_still_running()

    # 처음엔 접혀 있다 — 펼친 행도, 스텝 목록도 없다
    assert folded["expanded_rows"] == 0, "도는 행이 기본으로 펼쳐져 있다"
    assert folded["steps_blocks"] == 0, "접힌 행에 스텝 목록이 남아 있다"
    assert folded["aria_expanded"] == "false"
    assert folded["stored"] in (None, "[]"), folded["stored"]

    # 접혀 있어도 막대와 지금 스텝은 보인다. 막대는 도는 잡 하나에만 붙는다(대기 잡엔 없다)
    assert folded["bars"] == 1, folded["bars"]
    bar = folded["bar"]
    assert bar is not None, "도는 행에 전체 진행 막대가 없다"
    assert re.search(r"\d+%|—", bar["label"]), bar
    assert bar["valuetext"] == bar["label"], bar  # 보조기기가 읽는 값과 눈에 보이는 값이 같다
    assert re.search(r"width:\s*\d+(\.\d+)?%", bar["width"] or ""), bar
    assert "step" in folded["reason"].lower(), folded["reason"]
    # `slow` 은 총 스텝 수를 안 알리고, 이 서버엔 표본도 프리셋 추정도 없다(설치 기본값 600초뿐).
    # 그러면 막대는 **눈금을 주지 않는다** — 기본값을 70% 로 그리면
    # 자신있는 거짓말이다(Codex 리뷰 1).
    assert bar["label"] == "progress —", bar
    assert (bar["basis"], bar["cond"]) == ("none", "normal"), bar
    assert bar["tick"] is None, bar
    assert "aria-valuenow" not in (bar["aria"] or ""), bar

    # ▸ 를 누르면 펼쳐지고 그 선택이 남는다
    assert opened["expanded_rows"] == 1, "▸ 를 눌러도 펼쳐지지 않는다"
    assert opened["steps_blocks"] == 1
    assert opened["aria_expanded"] == "true"
    assert json.loads(opened["stored"]) == [scene.running], opened["stored"]
    assert opened["bars"] == 1, "펼쳐도 막대는 그대로 하나다"

    # 다시 누르면 접히고 저장된 목록에서도 빠진다
    assert closed["expanded_rows"] == 0
    assert closed["aria_expanded"] == "false"
    assert json.loads(closed["stored"]) == [], closed["stored"]

    assert "undefined" not in visible_text and "NaN" not in visible_text, visible_text[:600]


def test_recent_rows_show_the_job_id(tmp_path):
    """오너 요청(2026-09-09): 큐 행에 있는 `#412` 가 최근 완료 행에도 있어야 한다 —
    로그·재실행·문의가 전부 잡 번호로 이뤄지는데 끝난 잡에서만 번호가 사라졌다."""
    srv = Server(tmp_path, workers=False)
    try:
        jid = srv.submit(preset="ok")[1]["job_id"]
        assert srv.upload(jid)[0] == 200
        assert srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})[0] < 300
        status_until(
            srv,
            lambda d: any(r["id"] == jid for r in d["pools"][0]["recent"] or []),
            timeout=5.0,
        )
        with Chrome(tmp_path / "chrome-recent-id", window="1240,900") as c:
            c.open(
                f"http://127.0.0.1:{srv.port}/?poll=1&lang=en",
                ready_js=f"document.querySelector('#recent [data-job=\"{jid}\"]') !== null",
            )
            row_id = c.eval(
                f"document.querySelector('#recent [data-job=\"{jid}\"] .id').textContent"
            )
            row_text = c.eval(f"document.querySelector('#recent [data-job=\"{jid}\"]').textContent")
    finally:
        srv.close()
    assert row_id.strip() == f"#{jid}", row_id
    assert f"#{jid}" in row_text, row_text


# 막대가 **무엇으로 셌는지**를 화면에서 확인한다. 순수 함수 시험(tests/web/progress_overall)이
# 규칙을 잠그고, 여기서는 진짜 서버가 만든 상태 문서로 같은 규칙이 그려지는지를 본다.
BARS_JS = """
(ids => Object.fromEntries(ids.map(id => {
  const bar = document.querySelector('#queue tr.qbar[data-bar="' + id + '"]');
  const row = document.querySelector('#queue tr[data-job="' + id + '"]');
  const pbar = bar ? bar.querySelector('.pbar') : null;
  const fill = pbar ? pbar.querySelector('i') : null;
  const track = pbar ? pbar.getBoundingClientRect().width : 0;
  return [id, {
    basis: pbar ? pbar.getAttribute('data-basis') : null,
    cond: pbar ? pbar.getAttribute('data-cond') : null,
    // 그려진 길이 — 자동 레이아웃 표 안에서 퍼센트 폭이 틀어지던 회귀를 여기서 잡는다
    drawn: fill && track ? Math.round(fill.getBoundingClientRect().width / track * 100) : null,
    valuenow: pbar ? pbar.getAttribute('aria-valuenow') : null,
    label: bar ? bar.querySelector('.plab').textContent.trim() : null,
    valuetext: pbar ? pbar.getAttribute('aria-valuetext') : null,
    source: bar ? bar.querySelector('.pwrap').getAttribute('data-source') : null,
    tick: bar ? bar.querySelector('.pwrap').getAttribute('data-tick') : null,
    bars: document.querySelectorAll('[role="progressbar"]').length,
    cancel_in_row: row ? row.querySelectorAll('td.reason [data-cancel]').length : null,
    toggle: row && row.querySelector('[data-toggle]')
      ? row.querySelector('[data-toggle]').getAttribute('aria-label') : null,
  }];
})))(%s)
"""


def test_progress_bar_names_what_it_measured_and_keeps_cancel_one_tap_away(tmp_path):
    """Codex 리뷰(2026-09-09) 1·4·5 — 막대는 눈금의 근거를 밝히고, 접힌 도는 행에서도 내 잡을
    한 번에 취소할 수 있고, 한 잡에 `role="progressbar"` 는 하나다."""
    srv = Server(tmp_path, workers=False, lanes=2, admission="always")
    srv.cfg.presets = tuple(
        parse_preset(p)
        for p in [
            *PRESETS,
            # 총계를 선언한다 → 스텝 눈금(a 가 끝나고 b 가 도는 순간 1/4)
            sh(
                "steps4",
                "echo '::rcm::steps::4'; echo '::rcm::step::a'; echo '::rcm::step-end::ok'; "
                "echo '::rcm::step::b'; sleep 20",
            ),
            # 총계는 없고 프리셋 추정만 있다 → 시간 눈금, 라벨이 「프리셋」이라고 밝힌다
            sh("timed", "echo '::rcm::step::run'; sleep 20", expected_seconds=120),
        ]
    )
    jobs: list[int] = []
    try:
        a = srv.submit(preset="steps4")[1]["job_id"]
        assert srv.upload(a)[0] == 200
        b = srv.submit(preset="timed", tree_hash=OTHER_TREE)[1]["job_id"]
        assert srv.upload(b)[0] == 200
        jobs = [a, b]
        srv.app.start()

        def running_with_markers(doc: dict) -> bool:
            rows = {r["id"]: r for r in (doc["pools"][0]["queue"] or [])}
            ra, rb = rows.get(a) or {}, rows.get(b) or {}
            return (
                ra.get("state") == "running"
                and (ra.get("progress") or {}).get("steps_done") == 1
                and rb.get("state") == "running"
                and (rb.get("estimate") or {}).get("source") == "preset"
            )

        status_until(srv, running_with_markers, timeout=20.0)
        url = f"http://127.0.0.1:{srv.port}/?poll=1&lang=en"
        ready = (
            f"[{a}, {b}].every(id => "
            "document.querySelector('#queue tr.qbar[data-bar=\"' + id + '\"]') !== null)"
        )
        with Chrome(tmp_path / "chrome-bars", window="1240,900") as c:
            c.open(url, ready_js=ready)
            # 토큰이 있어야 내 잡의 취소 버튼이 열린다(읽기는 토큰 없이)
            c.eval(f"localStorage.setItem('rcm.token', {json.dumps(srv.tokens['alice'])})")
            c.open(
                url,
                ready_js=ready + " && document.querySelector('#tok-btn').textContent"
                ".indexOf('alice') >= 0 && document.querySelector('td.reason [data-cancel]')"
                " !== null",
            )
            time.sleep(0.3)  # 표 레이아웃이 끝난 뒤에 잰다 — 막대 길이는 레이아웃의 결과다
            bars = c.eval(BARS_JS % json.dumps([a, b]))
            expanded_before = c.eval("document.querySelectorAll('#queue tr.expanded').length")
            c.eval(f"document.querySelector('[data-toggle=\"{a}\"]').click()")
            open_bars = c.eval(
                "({expanded: document.querySelectorAll('#queue tr.expanded').length,"
                " progressbars: document.querySelectorAll('[role=\"progressbar\"]').length,"
                f' cancel_in_row: document.querySelectorAll(\'tr[data-job="{a}"] td.reason'
                " [data-cancel]').length,"
                f' actions_cancel: document.querySelectorAll(\'tr.expanded[data-job="{a}"]'
                " .actions .cancel:not([disabled])').length})"
            )
            visible_text = c.eval("document.body.innerText")
    finally:
        for jid in jobs:
            srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
        srv.close()

    # 스텝 총계를 선언한 잡: 스텝 눈금
    assert (bars[str(a)]["basis"], bars[str(a)]["cond"]) == ("steps", "normal"), bars[str(a)]
    assert bars[str(a)]["label"] == "25% · 1/4 steps", bars[str(a)]
    assert bars[str(a)]["tick"] is None, "스텝 눈금은 저절로 자라지 않는다"
    # 총계가 없고 프리셋 추정만 있는 잡: 시간 눈금 + 출처를 밝히는 라벨 + 1초 틱 기준점
    assert (bars[str(b)]["basis"], bars[str(b)]["cond"]) == ("time", "normal"), bars[str(b)]
    assert re.fullmatch(r"\d+% · by preset estimate", bars[str(b)]["label"]), bars[str(b)]
    assert bars[str(b)]["source"] == "preset" and bars[str(b)]["tick"] == "progress", bars[str(b)]
    # 보조기기가 읽는 값과 눈에 보이는 값이 같다
    for jid in (a, b):
        assert bars[str(jid)]["valuetext"] == bars[str(jid)]["label"], bars[str(jid)]
    # **막대의 길이가 그 숫자와 같다.** 자동 레이아웃 표 안에서 `width: 25%` 가 중간 폭으로 굳어
    # 11% 로 그려지던 적이 있다 — 길이가 거짓말을 하면 라벨이 정직해도 소용없다.
    for jid in (a, b):
        drawn, valuenow = bars[str(jid)]["drawn"], bars[str(jid)]["valuenow"]
        assert valuenow is not None, bars[str(jid)]
        assert abs(drawn - int(valuenow)) <= 1, (jid, bars[str(jid)])
    # 접힌 도는 행에서 취소가 한 번에 닿는다. ▸ 의 접근 이름에는 잡 번호가 있다
    assert expanded_before == 0
    assert bars[str(a)]["cancel_in_row"] == 1, bars[str(a)]
    assert bars[str(b)]["cancel_in_row"] == 1, bars[str(b)]
    assert f"#{a}" in (bars[str(a)]["toggle"] or ""), bars[str(a)]
    # 도는 잡 둘 → progressbar 둘. 하나를 펼쳐도 늘지 않는다(스텝 띠는 장식이다)
    assert bars[str(a)]["bars"] == 2, bars[str(a)]
    assert open_bars["expanded"] == 1 and open_bars["progressbars"] == 2, open_bars
    # 펼친 행에서는 액션 블록이 취소를 맡는다 — 같은 행에 취소 버튼이 둘이 되지 않는다
    assert open_bars["cancel_in_row"] == 0 and open_bars["actions_cancel"] == 1, open_bars
    assert "undefined" not in visible_text and "NaN" not in visible_text, visible_text[:600]
