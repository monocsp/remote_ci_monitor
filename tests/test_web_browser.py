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

잡 배치: `slow`(`SCENE_SLOW_SECONDS` 초 — Chrome 의 찬 기동 마감보다 길다) 잡 하나 running(lane 1)
+ 다른 트리의 `slow` 잡 하나 queued(1st in line). 캡처 뒤 잡이 아직 running 인지 다시 확인해 타이밍
실패를 명확한 메시지로 만든다. 테스트가 끝나면 두 잡을 취소해 teardown 을 빠르게 한다.
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
#: Chrome 이 뜨고 붙기까지의 마감(초) — `Chrome.__init__` 이 부르는 **모든** CDP 호출
#: (`Target.getTargets` `Target.attachToTarget` `Page.enable`
#: `Page.addScriptToEvaluateOnNewDocument` 와 about:blank 의 수집기 심기)이 이 값을 받는다.
#: 붙은 뒤의 호출은 `Chrome.call` 의 기본 15초.
COLD_START_SECONDS = 60.0
#: 장면의 `slow` 잡이 자는 초. Chrome 은 장면이 선 **뒤에** 뜨므로 찬 기동이 길어질수록 잡의 남은
#: 시간이 줄어든다 — `test_server.PRESETS` 의 20초짜리 `slow` 로는 마감 60초가 헛것이었다(M5l L7.1
#: 실측). 찬 기동 마감 + `open()` 15초 + 자리잡기 여유보다 길게. teardown 은 취소로 끝낸다.
SCENE_SLOW_SECONDS = 120
SCENE_PRESETS = [
    p
    if p["name"] != "slow"
    else sh(
        "slow",
        f"echo '::rcm::step::wait'; echo line1; echo line2; sleep {SCENE_SLOW_SECONDS}",
        timeout_seconds=SCENE_SLOW_SECONDS + 60,
    )
    for p in PRESETS
]
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
        # 붙기까지의 호출은 전부 찬 기동 마감을 받는다: CI 러너(ubuntu 3.11·3.13)에서 Chrome 의 찬
        # 기동이 15초를 넘겨 「CDP: no reply before the deadline」로 빨갰다(2026-09-10 · #85 #87
        # #92 ×2). #98 은 `Page.enable`·`addScript…` 에만 60초를 줬고 정작 첫 응답을 기다리는
        # `_attach_first_page` 안의 `Target.getTargets` 는 15초 그대로라 머지 뒤 세 번 더 같은
        # 프레임으로 빨갰다(docs/reviews/2026-09-13-impl-reviews/pr-98.md P1 · M5l L7). 붙은 뒤의
        # 호출은 15초 그대로다 — 늘어진 페이지를 숨기지 않는다.
        self.session = self._attach_first_page(timeout=COLD_START_SECONDS)
        self.call("Page.enable", timeout=COLD_START_SECONDS)
        # 페이지 스크립트보다 **먼저** 도는 수집기. 터진 render() 는 타임아웃이 아니라 이 목록으로
        # 드러난다 — 「page not ready within 15s」보다 「ReferenceError: local is not defined」가
        # 원인이다. `page_errors()` 로 읽고, 테스트는 0건을 단언한다. 이 호출을 지우면
        # `page_errors()` 가 「수집기 누락」으로 실패한다(mutcheck ㉗
        # `web-page-error-collector-removed`).
        self.call(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": PAGE_ERROR_COLLECTOR_JS},
            timeout=COLD_START_SECONDS,
        )
        # 이미 떠 있는 about:blank 에는 위 스크립트가 안 들어간다(새 문서부터 돈다). 첫 `navigate`
        # 가 커밋되기 전에 `page_errors()` 가 그 문서를 읽으면 수집기 누락으로 오판하므로 거기에도
        # 같은 수집기를 심는다 — 그 뒤로는 모든 문서에 수집기가 있고, 없으면 진짜 누락이다.
        seeded = self.call(
            "Runtime.evaluate", {"expression": PAGE_ERROR_COLLECTOR_JS}, timeout=COLD_START_SECONDS
        )
        assert "exceptionDetails" not in seeded, seeded

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
        """첫 페이지 타깃에 붙어 sessionId 를 돌려준다. 안의 CDP 호출마다 **남은** 마감을 넘긴다.

        Chrome 의 찬 기동을 맞는 자리가 여기다 — 첫 `Target.getTargets` 응답이 오기까지가 제일
        길다. `call` 의 기본 마감을 쓰면 `timeout` 은 「페이지 타깃이 생길 때까지 되묻는 루프」만
        덮고 응답 하나하나는 15초에서 끊긴다(pr-98 리뷰 P1). 마감이 거의 다 됐어도 응답 한 번은
        1초 이상 기다린다.
        """
        deadline = time.monotonic() + timeout

        def remaining() -> float:
            return max(1.0, deadline - time.monotonic())

        while True:
            targets = self.call("Target.getTargets", timeout=remaining())["targetInfos"]
            pages = [t for t in targets if t["type"] == "page"]
            if pages:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("CDP: Chrome never created a page target")
            time.sleep(0.05)
        params = {"targetId": pages[0]["targetId"], "flatten": True}
        return self.call("Target.attachToTarget", params, timeout=remaining())["sessionId"]

    def _stderr_tail(self) -> str:
        try:
            self.stderr.flush()
            data = Path(self.stderr.name).read_bytes()
        except OSError:
            return "(no stderr)"
        return "chrome stderr: " + data[-600:].decode("utf-8", "replace")

    # 페이지 -------------------------------------------------------------------

    def page_errors(self) -> list[str]:
        """페이지가 열린 뒤 지금까지의 `error`·`unhandledrejection` 메시지. 이동 중이면 빈 목록.

        수집기가 없으면(`window.__rcmErrors` 가 배열이 아니면) **실패**다. 빈 목록으로 덮으면 I5 의
        「0건 단언」이 수집기 없이도 초록이라 아무것도 지키지 않는다 — CDP 메서드 이름 오타든
        누가 정리하며 지웠든 조용히 통과했다(pr-82 리뷰 P2). 실행 컨텍스트가 아직 없는 이동 중
        (`RuntimeError`)만 빈 목록이다.
        """
        try:
            found = self.eval("Array.isArray(window.__rcmErrors) ? window.__rcmErrors : null")
        except RuntimeError:  # 실행 컨텍스트가 아직 없다
            return []
        if not isinstance(found, list):
            raise AssertionError("page error collector missing")
        return list(found)

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
            f"(capture took longer than the {SCENE_SLOW_SECONDS} s `slow` preset)"
        )


def test_the_scene_outlives_the_cold_start_deadline(scene):
    """Chrome 은 장면이 선 **뒤에** 뜬다 — 찬 기동이 길어도 `COLD_START_SECONDS` 안이면 붙지만,
    그 사이 장면의 `slow` 잡이 끝나 버리면 마감은 헛것이다(M5l L7.1 실측: `Target.getTargets`
    20초 지연에 「no reply」가 아니라 「page not ready」— 20초 `slow` 가 먼저 끝났다). 장면의
    running 잡은 찬 기동 마감 + `open()` 의 15초 + 자리잡기 여유보다 오래 살아야 한다."""
    slow = next(p for p in scene.srv.cfg.presets if p.name == "slow")
    m = re.search(r"sleep (\d+)", " ".join(slow.argv))
    assert m, slow.argv
    assert int(m.group(1)) >= COLD_START_SECONDS + 15 + 5, (
        f"the scene's slow job sleeps {m.group(1)} s — shorter than the cold-start deadline "
        "it must outlive"
    )


@pytest.fixture
def scene(tmp_path):
    srv = Server(tmp_path, workers=True)
    srv.cfg.presets = tuple(parse_preset(p) for p in SCENE_PRESETS)  # 긴 `slow`
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
    assert "Needs a look" in summary_labels[1]
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
        # 막대는 「진행 시간」 칸 안에 산다 — 칸이 블록이 되는 폰에서도 84px 그대로여야 한다(C-52)
        bar_track = c.eval(
            "(() => { const p = document.querySelector('#queue td.elapsed .pwrap .pbar');"
            " return p === null ? null : Math.round(p.getBoundingClientRect().width); })()"
        )
    scene.assert_still_running()
    # 720px 미만이 카드 레이아웃 구간(§4 모바일). 진짜 390 인지 못 박는다 —
    # `<= 720` 은 macOS 가 만들어 주는 500 도 통과시킨다
    assert width == 390, f"viewport is {width}px wide — the device metrics override did not take"
    assert f'data-job="{scene.running}"' in dom
    assert f'data-job="{scene.queued}"' in dom
    assert "1st in line" in visible_text
    assert "21%" in visible_text
    # 라벨은 textContent 로 — innerText 는 CSS `text-transform: uppercase` 를 반영한다
    for label in ("Your jobs", "Needs a look", "Host pressure"):
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
    assert bar_track is not None, "390px 에서 도는 행의 진행 막대가 사라졌다"
    assert abs(bar_track - 84) <= 1, f"390px 에서 막대가 {bar_track}px 로 눌렸다 — 84px 고정이다"
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
        assert "내 작업" in summary and "확인이 필요한 작업" in summary, summary
        assert queue_head == "큐", queue_head
        assert "진행 중" in body, body[:400]
        assert "undefined" not in body and "NaN" not in body
        # 전환 — 정적 라벨까지 따라와야 한다
        c.eval("document.getElementById('lang-btn').click()")
        after_lang = c.eval("document.documentElement.lang")
        after_summary = c.eval("document.getElementById('summary').textContent")
        after_queue = c.eval("document.querySelector('#queue .s-h .t').textContent")
        after_body = c.eval("document.body.innerText")
        stored = c.eval("localStorage.getItem('rcm.lang')")
        assert after_lang == "en"
        assert "Your jobs" in after_summary and "Needs a look" in after_summary, after_summary
        assert after_queue == "Queue", after_queue
        assert "running" in after_body
        assert "내 작업" not in after_summary, "한국어 라벨이 남았다"
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
  const wrap = row ? row.querySelector('td.elapsed .pwrap') : null;
  const pbar = wrap ? wrap.querySelector('.pbar[role="progressbar"]') : null;
  const btn = document.querySelector('[data-toggle="' + id + '"]');
  return {
    expanded_rows: document.querySelectorAll('#queue tr.expanded').length,
    aria_expanded: btn ? btn.getAttribute('aria-expanded') : null,
    steps_blocks: document.querySelectorAll('#queue .steps').length,
    bars: document.querySelectorAll('#queue td.elapsed .pwrap').length,
    bar: pbar === null ? null : {
      basis: pbar.getAttribute('data-basis'),
      cond: pbar.getAttribute('data-cond'),
      label: wrap.querySelector('.plab').textContent.trim(),
      valuetext: pbar.getAttribute('aria-valuetext'),
      width: pbar.querySelector('i').getAttribute('style'),
      // 막대는 84px 고정이다 — 칸 안에서 눌리면 그려진 길이가 `aria-valuenow` 와 갈린다(C-51)
      track: Math.round(pbar.getBoundingClientRect().width),
      tick: wrap.getAttribute('data-tick'),
      expected: wrap.getAttribute('data-expected'),
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
    # 막대는 「진행 시간」 칸 안의 **84px 고정폭**이다(C-51). 자동 폭으로 되돌아가면 칸이 줄 때
    # 막대가 눌려 `width: 25%` 가 25% 로 안 보인다 — 아래 `drawn` 단언이 통과해도 뜻이 없어진다.
    assert abs(bar["track"] - 84) <= 1, bar
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
  const row = document.querySelector('#queue tr[data-job="' + id + '"]');
  const wrap = row ? row.querySelector('td.elapsed .pwrap') : null;
  const pbar = wrap ? wrap.querySelector('.pbar') : null;
  const fill = pbar ? pbar.querySelector('i') : null;
  const track = pbar ? pbar.getBoundingClientRect().width : 0;
  return [id, {
    basis: pbar ? pbar.getAttribute('data-basis') : null,
    cond: pbar ? pbar.getAttribute('data-cond') : null,
    // 그려진 길이 — 자동 레이아웃 표 안에서 퍼센트 폭이 틀어지던 회귀를 여기서 잡는다
    drawn: fill && track ? Math.round(fill.getBoundingClientRect().width / track * 100) : null,
    // 막대 그릇의 폭. 84 가 아니면 위의 `drawn` 이 통과해도 뜻이 없다(C-51)
    track: Math.round(track),
    valuenow: pbar ? pbar.getAttribute('aria-valuenow') : null,
    label: wrap ? wrap.querySelector('.plab').textContent.trim() : null,
    valuetext: pbar ? pbar.getAttribute('aria-valuetext') : null,
    source: wrap ? wrap.getAttribute('data-source') : null,
    tick: wrap ? wrap.getAttribute('data-tick') : null,
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
            "document.querySelector('#queue tr[data-job=\"' + id + '\"] td.elapsed .pwrap')"
            " !== null)"
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
            # 좁은 화면(720px 미만 — 표 칸이 블록이 되는 자리)에서도 같은 계약이다(C-52).
            # 같은 크롬을 뷰포트만 바꿔 다시 잰다 — 한 번 더 띄우면 그만큼 느려진다.
            c.viewport(680, 900)
            time.sleep(0.3)
            narrow = c.eval(BARS_JS % json.dumps([a, b]))
            narrow_overflow = c.eval(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            c.call("Emulation.clearDeviceMetricsOverride")
            time.sleep(0.3)
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
        # 그릇이 84px 이 아니면 위의 단언은 통과해도 뜻이 없다 — 칸이 눌려 막대가 같이 줄면
        # 「25%」가 그 줄어든 폭의 25% 다(C-51). 폭도 함께 잠근다.
        assert abs(bars[str(jid)]["track"] - 84) <= 1, (jid, bars[str(jid)])
    # 680px: 표 칸이 블록이 되어도 막대는 살아 있고, 폭도 그려진 길이도 그대로다(C-52)
    for jid in (a, b):
        n = narrow[str(jid)]
        assert n["valuenow"] is not None, (jid, n)
        assert abs(n["track"] - 84) <= 1, (jid, n)
        assert abs(n["drawn"] - int(n["valuenow"])) <= 1, (jid, n)
    # 84px 칸 안의 라벨이 `nowrap` 으로 남아 있으면 표가 라벨 폭만큼 옆으로 샌다
    assert narrow_overflow <= 1, (narrow_overflow, narrow)
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


# ── 호스트 절은 저절로 여닫히지 않는다 ───────────────────────────────────────


class LoadSampler(StubSampler):
    """시험이 정한 CPU 와 표본 나이로 매번 새 표본을 만든다 — 85 를 오가게 하려고.

    `FreshStubSampler` 와 같은 실배치 모양(`disk` 있음)이되 부하와 나이를 시험이 쥔다.
    """

    def __init__(self, cpu: float = 21.0, age_seconds: float = 2.0) -> None:
        super().__init__([])
        self.cpu = cpu
        self.age_seconds = age_seconds

    def latest(self):
        s = host_sample(datetime.now(UTC), age_seconds=self.age_seconds)
        cpu = dict(s.cpu, busy=self.cpu, idle=round(100.0 - self.cpu, 1))
        return [replace(s, cpu=cpu, disk=DISK)], None


def host_panel(c: Chrome) -> dict[str, Any]:
    """지금 화면의 호스트 절 — 열림 · 페이지 높이 · 접힌 한 줄 · 카드에 그려진 CPU."""
    return c.eval(
        "({open: document.querySelector('#host-details').open,"
        " height: document.body.scrollHeight,"
        " digest: document.querySelector('[data-host-digest]').textContent,"
        " cpu: (document.querySelector('#host .meter[data-metric=\"cpu\"] .lab span')"
        " || {}).textContent})"
    )


def refetch(c: Chrome, *, until: str, timeout: float = 10.0) -> dict[str, Any]:
    """live 토글을 껐다 켜 **즉시** 다시 받아 그린다 — 10초 폴링을 기다리지 않는다.

    `resumeUpdates` 가 `fetchStatus()` 를 바로 부른다(`app.js` 의 `#live-btn`). `until` 이 참이
    될 때까지 기다린 뒤 호스트 절을 돌려준다.
    """
    for _ in range(2):  # 멈춤 → 재개
        c.eval("document.querySelector('#live-btn').click()")
    deadline = time.monotonic() + timeout
    while True:
        if c.eval(until) is True:
            return host_panel(c)
        if time.monotonic() >= deadline:
            raise AssertionError(f"{until!r} not true within {timeout}s: {host_panel(c)}")
        time.sleep(0.1)


def cpu_drawn(pct: int) -> str:
    """호스트 카드의 CPU 막대에 이 퍼센트가 그려졌는가."""
    return (
        "document.querySelector('#host .meter[data-metric=\"cpu\"] .lab span')"
        f".textContent.includes('CPU {pct}%')"
    )


def test_a_busy_host_does_not_open_the_host_panel_and_flipping_the_verdict_moves_nothing(tmp_path):
    """호스트 절은 **부하로는 저절로 펼쳐지지 않는다.** CPU 가 85 를 오가도 `#host-details` 의
    열림과 페이지 높이가 그대로고, 부하는 접힌 한 줄의 글자(`busy`)로만 말한다.

    2026-09-14 운영 인스턴스(v0.2.7) 실측 회귀: `renderHostDigest()` 가 렌더마다
    `det.open = warn` 을 다시 적용해서, CPU 가 85 를 스칠 때마다 호스트 절이 저절로 펴졌다
    접히며 그 아래 「최근」이 통째로 **315px** 뛰었다(기본 브라우저 2분에 3번 · CLS 0.245 ·
    사람이 「열림」을 고른 대조군은 0번). 빌드 머신의 CPU 85% 는 이상이 아니라 **일하는 중**
    이라는 뜻이라 펼칠 일이 아니다 — 읽는 사람 발밑을 빼는 일만 한다.
    """
    srv = Server(tmp_path, workers=True)
    try:
        sampler = LoadSampler(cpu=90.0)
        srv.app.sampler = sampler
        status_until(srv, lambda d: bool(d["pools"][0]["hosts"]), timeout=5.0)
        seen: list[dict[str, Any]] = []
        with Chrome(tmp_path / "chrome-host-quiet", window="1240,1400") as c:
            c.open(
                f"http://127.0.0.1:{srv.port}/?poll=1&lang=en",
                ready_js="document.querySelector('#host .meter[data-metric=\"cpu\"]') !== null",
            )
            seen.append(dict(host_panel(c), label="busy 90"))
            # 85 아래 → 위 → 사이(80~84). 사람은 아무것도 안 눌렀다.
            for cpu in (40.0, 90.0, 82.0):
                sampler.cpu = cpu
                seen.append(dict(refetch(c, until=cpu_drawn(int(cpu))), label=f"cpu {cpu:.0f}"))
            errors = c.page_errors()
    finally:
        srv.close()

    assert errors == [], errors
    # 저절로 펼쳐지지 않는다 — 네 번의 렌더 내내 닫혀 있다
    assert [s["open"] for s in seen] == [False, False, False, False], seen
    # 그리고 아무것도 안 움직인다 — 페이지 높이가 하나뿐이다
    assert len({s["height"] for s in seen}) == 1, seen
    # 부하를 감추는 것이 아니다: 접힌 한 줄이 「busy」라고 말한다
    assert seen[0]["digest"].endswith("busy"), seen[0]
    assert seen[1]["digest"].endswith("fine"), seen[1]
    # 80~84 는 이력이 잡는다 — 85 를 한 번 넘었으면 80 아래로 내려와야 여유다
    assert seen[3]["digest"].endswith("busy"), seen[3]


def test_a_stale_sample_opens_the_host_panel_once_and_nothing_closes_it_again(tmp_path):
    """표본이 안 오는 것은 **사람이 손대야 하는 일**이라 호스트 절이 저절로 펼쳐진다. 그리고
    표본이 다시 신선해져도 **저절로 닫히지 않는다** — 닫는 것은 사람의 일이다. 읽는 중에
    접히면 그 아래가 통째로 올라온다.
    """
    srv = Server(tmp_path, workers=True)
    try:
        sampler = LoadSampler(cpu=21.0)
        srv.app.sampler = sampler
        status_until(srv, lambda d: bool(d["pools"][0]["hosts"]), timeout=5.0)
        with Chrome(tmp_path / "chrome-host-stale", window="1240,1400") as c:
            c.open(
                f"http://127.0.0.1:{srv.port}/?poll=1&lang=en",
                ready_js="document.querySelector('#host .meter[data-metric=\"cpu\"]') !== null",
            )
            fresh = host_panel(c)
            sampler.age_seconds = 90.0  # 3 × interval(5초)보다 한참 지난 표본
            stale = refetch(c, until="document.querySelector('#host .stale-badge') !== null")
            sampler.age_seconds = 2.0
            again = refetch(c, until="document.querySelector('#host .stale-badge') === null")
            errors = c.page_errors()
    finally:
        srv.close()

    assert errors == [], errors
    assert fresh["open"] is False, fresh  # 멀쩡할 때는 접혀 있다
    assert stale["open"] is True, stale  # 이상이면 한 번 펼친다
    assert again["open"] is True, again  # 그리고 저절로 닫지 않는다


# ── 스토어 탭 (docs/wireframes/web-store.html · 항목 1 · 29~35) ─────────────────
#
# 이 빌드의 서버에는 `/api/repos` 가 없다(프로파일 PR 이 따로다). 그래서 서버 호출 층
# (`window.rcmStoreApi` — app.js `storeApi()`)을 페이지 스크립트보다 먼저 심어 STORE-TAB-API 모양의
# 문서를 준다. `?complete=1` 이면 관문이 열린 저장소, 아니면 비밀 둘이 빠진 저장소다.
STORE_STUB_JS = r"""
(() => {
  const complete = /[?&]complete=1(&|$)/.test(location.search);
  const setup = complete
    ? { required: 3, present: 3, verified: 3, complete: true, missing: [] }
    : { required: 3, present: 1, verified: 1, complete: false,
        missing: ["GH_TOKEN", "review_information/demo_password.txt"] };
  const sha = (p) => p + "0".repeat(40 - p.length);
  const at = "2026-09-17T12:03:00Z";
  const doc = {
    name: "app", url: "git@example.invalid:app.git",
    profile: { default_branch: "main", tag: "prod/{version}-{build}",
      build_number_policy: "auto", plan_max_age_minutes: 30, driver: null, listing: null,
      secrets_dir_env: "APP_SECRETS",
      presets: { plan: "release-plan", upload: "release-upload", review: "release-review",
        gate: null, qa: null, dev: null } },
    setup,
    mirror: { path: "/srv/rcm/mirrors/app", age_seconds: 200,
      fetched_at: new Date(Date.now() - 200000).toISOString() },
    branches: { main: sha("9e1c4d2f"), dev: sha("7a03b9f0"), main_in_dev: true },
  };
  const secrets = { dir_env: "APP_SECRETS", items: [
    { name: "AuthKey.p8", kind: "file", optional: false, verify: "asc", present: true,
      size: 2112, fingerprint: "9f1c2a3b", verified_at: at, verify_error: null, max_kb: 64 },
    { name: "GH_TOKEN", kind: "value", optional: false, verify: "github", present: complete,
      size: null, fingerprint: complete ? "ghp_…" : null, verified_at: complete ? at : null,
      verify_error: null },
    { name: "review_information", kind: "dir", optional: false, verify: "none",
      present: complete, fingerprint: complete ? "2/2 files" : "1/2 files",
      verified_at: null, verify_error: null,
      files: [{ name: "demo_user.txt", present: true, size: 12, fingerprint: "aabbccdd" },
              { name: "demo_password.txt", present: complete, size: complete ? 9 : null,
                fingerprint: complete ? "eeff0011" : null }] },
  ] };
  const ok = (body) => Promise.resolve({ ok: true, status: 200, body });
  const notFound = { ok: false, status: 404, body: { error: "unknown repo", code: "not_found" } };
  window.rcmStoreCalls = [];
  window.rcmStoreApi = {
    repos: () => ok({ repos: [{ name: "app", release: true, setup },
                               { name: "lib", release: false }] }),
    repo: (name) => name === "app" ? ok(doc) : Promise.resolve(notFound),
    secrets: () => ok(secrets),
    putSecret: (...a) => { window.rcmStoreCalls.push(["put", a[0], a[1], a[4] || null]);
                           return ok(secrets.items[1]); },
    verify: () => { window.rcmStoreCalls.push(["verify"]); return ok(secrets); },
    fetchRemote: () => { window.rcmStoreCalls.push(["fetch"]);
                         return ok({ mirror: doc.mirror, branches: doc.branches }); },
  };
})();
"""

STORE_ROWS_JS = """
(() => [...document.querySelectorAll('#store details.srow[data-row]:not(.review)')].map((d) => ({
  row: d.getAttribute('data-row'), state: d.getAttribute('data-state'), open: d.open,
  head: d.querySelector('summary').textContent.replace(/\\s+/g, ' ').trim(),
})))()
"""

SECRET_ROWS_JS = """
[...document.querySelectorAll('#store table.sec tr[data-secret]')].map((r) => [
  r.getAttribute('data-secret'), r.getAttribute('data-file'),
  r.querySelector('.pill').textContent.trim(), r.querySelector('.fp').textContent.trim()])
"""


def _q(selector: str, prop: str = "") -> str:
    return f"document.querySelector({json.dumps(selector)}){prop}"


def test_store_tab_gate_shows_settings_until_every_secret_is_set(tmp_path):
    """항목 1 · 29 · 31 · 34 · 35 — `/api/repos` 가 `release: true` 를 주면 머리에 `Queue | Store`
    가 생기고, `#/store/app` 은 관문(설정 화면)이다: 빨간 띠 «1 of 3 secrets set · 1 verified»,
    비활성 «Enter Store», 프로파일이 선언한 비밀마다 한 행(폴더는 파일마다 한 줄). 토큰이 없으면
    표는 읽기 전용이고 이유가 한 줄 있다. 값은 어디에도 없고, 큐의 폴링은 그대로 산다.
    관문이 열리면(`?complete=1`) 행 넷 + 접힌 본체 머리다 — Setup·Source 초록 접힘, 나머지 회색."""
    srv = Server(tmp_path, workers=False)
    try:
        base = f"http://127.0.0.1:{srv.port}/?poll=1&lang=en"
        with Chrome(tmp_path / "chrome-store", window="1240,900") as c:
            c.call("Page.addScriptToEvaluateOnNewDocument", {"source": STORE_STUB_JS})
            # 큐 화면 — 탭은 있고 스토어 절은 숨겨져 있다
            c.open(base, ready_js=_q('#view-nav a[data-nav="store"]') + " !== null")
            assert c.eval("document.getElementById('view-nav').hidden") is False
            assert c.eval("document.getElementById('store').hidden") is True
            assert c.eval("document.getElementById('queue').hidden") is False
            nav_text = c.eval("document.getElementById('view-nav').textContent")
            assert "Queue" in nav_text and "Store" in nav_text and "app" in nav_text, nav_text
            assert "lib" not in nav_text, "a repo without a profile must not be offered"

            # 관문 — 설정 화면
            gate_ready = _q('#store [data-secret="GH_TOKEN"]') + " !== null"
            c.open(base + "#/store/app", ready_js=gate_ready)
            assert c.eval("document.getElementById('store').hidden") is False
            assert c.eval("document.getElementById('queue').hidden") is True
            assert c.eval("document.getElementById('summary').hidden") is True
            gate_cls = c.eval(_q("#store [data-gate]", ".className"))
            gate_text = c.eval(_q("#store [data-gate]", ".textContent"))
            assert "bad" in gate_cls.split() and "ok" not in gate_cls.split(), gate_cls
            assert "1 of 3 secrets set · 1 verified" in gate_text, gate_text
            assert "Set up app before entering Store" in gate_text, gate_text
            assert c.eval(_q("#store [data-enter-store]", ".disabled")) is True
            n_rows = c.eval("document.querySelectorAll('#store details.srow').length")
            assert n_rows == 0, "no store rows behind the gate"
            rows = c.eval(SECRET_ROWS_JS)
            assert rows == [
                ["AuthKey.p8", None, "✓present", "9f1c2a3b · 2 KB"],
                ["GH_TOKEN", None, "✗missing", "—"],
                ["review_information", None, "✗missing", "1 of 2 files"],
                ["review_information", "demo_user.txt", "✓present", "aabbccdd · 12 B"],
                ["review_information", "demo_password.txt", "✗missing", "—"],
            ], rows
            body = c.eval("document.body.innerText")
            assert "Only an admin token can set up app" in body, body[:600]
            assert re.search(r"verified \d\d:\d\d", body), body[:600]
            assert "undefined" not in body and "NaN" not in body
            # 비활성 + 이유 — 감추지 않는다(항목 35)
            assert c.eval(_q('#store [data-set-value="GH_TOKEN"]', ".disabled")) is True
            assert c.eval(_q("#store [data-verify-all]", ".disabled")) is True
            drops = c.eval(
                "[...document.querySelectorAll('#store label.drop')]"
                ".map(l => l.classList.contains('disabled'))"
            )
            assert drops == [True, True, True], "one dropzone per file secret and per folder file"
            assert c.eval("window.rcmStoreCalls.length") == 0, "nothing was sent without a token"
            # 큐의 갱신은 이 화면에서도 산다 — `?poll=1` 이라 폴링 타이머가 곧 SSE 자리다
            assert c.eval("document.getElementById('live-btn').className").startswith("livebtn")
            assert c.page_errors() == []

            # 폰 폭 — 옆으로 새지 않는다
            c.viewport(390, mobile=True)
            assert c.eval("document.documentElement.scrollWidth") <= 390
            c.viewport(1240)

            # 관문이 열린 저장소 — 행 넷 + 본체 머리
            c.open(
                base + "&complete=1#/store/app",
                ready_js="document.querySelectorAll("
                "'#store details.srow[data-row]:not(.review)').length === 4",
            )
            rows = c.eval(STORE_ROWS_JS)
            by = {r["row"]: r for r in rows}
            assert [r["row"] for r in rows] == ["setup", "source", "build", "store"], rows
            assert by["setup"]["state"] == "ok" and by["setup"]["open"] is False, by["setup"]
            assert "3/3 secrets present" in by["setup"]["head"], by["setup"]
            assert "build number auto" in by["setup"]["head"], by["setup"]
            assert by["source"]["state"] == "ok" and by["source"]["open"] is False, by["source"]
            src_head = by["source"]["head"]
            assert "main 9e1c4d2 · main in dev · fetched 3m ago" in src_head, src_head
            assert by["build"]["state"] == "na" and by["store"]["state"] == "na", rows
            assert "not available in this build" in by["build"]["head"], by["build"]
            review = c.eval(_q("#review-panel summary", ".textContent"))
            assert "Submit for review" in review and "not available in this build" in review, review
            assert c.eval("document.getElementById('review-panel').open") is False
            fetch_disabled = c.eval(_q("#store [data-fetch-remote]", ".disabled"))
            assert fetch_disabled is True, "no token → fetch disabled with a reason"
            assert c.eval(_q("#store [data-gate]")) is None, "no gate banner on the store screen"
            body = c.eval("document.body.innerText")
            assert "Approval does not release" in body, body[:800]
            assert "ghp_" not in body.replace("ghp_…", ""), "a value shows 4 chars + … at most"
            # 사람이 연 행은 기억된다(큐 화면 규칙) — 렌더가 정한 열림은 기억이 아니다
            assert c.eval("localStorage.getItem('rcm.store.rows')") is None
            c.eval(_q('#store details.srow[data-row="setup"] > summary', ".click()"))
            remembered = c.eval("JSON.parse(localStorage.getItem('rcm.store.rows'))")
            assert remembered == {"app/setup": "open"}
            # 한국어로 바꾸면 행 이름이 따라온다
            c.eval("document.getElementById('lang-btn').click()")
            heads = c.eval(
                "[...document.querySelectorAll("
                "'#store details.srow[data-row]:not(.review) > summary .t')]"
                ".map(e => e.textContent)"
            )
            assert heads == ["설정", "소스", "빌드 · 업로드", "스토어"], heads
            assert c.page_errors() == []
    finally:
        srv.close()


# ── 심사 패널 본체 · Store 행 · Build·upload 행 (항목 5~24 · 28 · 36 · 40 · 46~49) ─────────
#
# STORE-TAB-API-2 의 `GET …/release` · `GET …/release/listing` · `POST …/release/review` 를 통째로
# 스텁한다(서버 쪽은 다른 PR). `?unsafe=1` 이면 심사 플랜의 iOS 판정이 `unsafe_release_type` 이고,
# `window.rcmRefuse = "<code>"` 를 두면 review POST 가 그 코드로 409 를 답한다.
RELEASE_STUB_JS = r"""
(() => {
  const unsafe = /[?&]unsafe=1(&|$)/.test(location.search);
  const sha = (p) => p + "0".repeat(40 - p.length);
  const at = "2026-09-17T12:03:00Z";
  const ago = (s) => new Date(Date.now() - s * 1000).toISOString();
  const setup = { required: 3, present: 3, verified: 3, complete: true, missing: [] };
  const presets = { plan: "release-plan", upload: "release-upload", review: "release-review",
    gate: null, qa: null, dev: null };
  const doc = {
    name: "app", url: "git@example.invalid:app.git",
    profile: { default_branch: "main", tag: "prod/{version}-{build}",
      build_number_policy: "auto", plan_max_age_minutes: 30, driver: null,
      listing: { preview: ["x"], diff: ["y"], validate: ["z"] }, secrets_dir_env: "APP_SECRETS",
      presets },
    setup,
    mirror: { path: "/srv/rcm/mirrors/app", age_seconds: 200, fetched_at: ago(200) },
    branches: { main: sha("9e1c4d2f"), dev: sha("7a03b9f0"), main_in_dev: true },
  };
  const secrets = { dir_env: "APP_SECRETS", items: [
    { name: "AuthKey.p8", kind: "file", optional: false, verify: "asc", present: true,
      size: 2112, fingerprint: "9f1c2a3b", verified_at: at, verify_error: null, max_kb: 64 },
    { name: "GH_TOKEN", kind: "value", optional: false, verify: "github", present: true,
      size: null, fingerprint: "ghp_…", verified_at: at, verify_error: null },
    { name: "review_information", kind: "dir", optional: false, verify: "none", present: true,
      fingerprint: "2/2 files", verified_at: null, verify_error: null,
      files: [{ name: "demo_user.txt", present: true, size: 12, fingerprint: "aabbccdd" },
              { name: "demo_password.txt", present: true, size: 9, fingerprint: "eeff0011" }] },
  ] };
  const planDoc = { schema: 1, build_name: "1.0.1", n: 181, first_release: false,
    store: { asc_live: "1.0.0", asc_live_build: 180, asc_editing: "1.0.1",
             play: { production: 180 } },
    blockers: [], warnings: [{ code: "W-TABLET", text: "no tablet screenshots" }],
    measured_at: ago(240) };
  const reviewPlanDoc = { schema: 2, build_name: "1.0.1", n: 181, plan_verdict: "ok",
    ios: unsafe ? "unsafe_release_type" : "ready", android: "ready",
    observed: { ios: "PREPARE_FOR_SUBMISSION", android: "completed", auto_release: false },
    listing: { preview: [], diff: [] }, measured_at: ago(240) };
  const uploadDoc = { schema: 1, n: 181, status: "success", mode: "upload",
    platforms: ["ios", "android"], tag: "prod/1.0.1-181" };
  const job = (id, preset, role) => ({ id, preset, role, state: "succeeded",
    sha: sha("9e1c4d2f"), ref: "main", started_at: ago(4000 - id), finished_at: ago(3000 - id),
    artifacts: [] });
  const release = {
    setup,
    plan: { job_id: 641, state: "succeeded", measured_at: ago(240), age_seconds: 240,
            stale: false, build_name: "1.0.1", doc: planDoc },
    review: { plan: { job_id: 651, state: "succeeded", age_seconds: 240, stale: false,
                      doc: reviewPlanDoc },
              result: null },
    upload: { job_id: 650, state: "succeeded", finished_at: ago(3000), doc: uploadDoc },
    jobs: [job(651, "release-review", "review"), job(650, "release-upload", "upload"),
           job(641, "release-plan", "plan")],
  };
  const listing = { sha: sha("9e1c4d2f"),
    preview: ["ios.promotional_text: Short daily notes",
              "description: A calm journal for every day.",
              "ios.keywords: journal,mood,notes",
              "ios.support_url: https://example.invalid/support",
              "ios.subtitle: Daily notes", "android.title: Journal",
              "android.short_description: A calm journal",
              "android.full_description: A calm journal for every day, with photos.",
              "generated by store_listing.py"],
    diff: ["ios/ko/subtitle: «Daily» → «Daily notes»", "screenshots ios +1"],
    release_notes: { path: "store/release_notes/1.0.1/ko.txt",
                     text: "• photos in inquiries\n• fixes" },
    screenshots: [
      { path: "store/screenshots/ios/ko/01_iphone65_home.png", bytes: 1234,
        width: 1284, height: 2778 },
      { path: "store/screenshots/ios/ko/02_iphone65_write.png", bytes: 1200,
        width: 1284, height: 2778 },
      { path: "store/screenshots/android/ko-KR/01_phone_home.png", bytes: 999,
        width: null, height: null }],
    errors: [] };
  const ok = (body, status) => Promise.resolve({ ok: true, status: status || 200, body });
  window.rcmStoreCalls = [];
  window.rcmRefuse = null;
  window.rcmStoreApi = {
    repos: () => ok({ repos: [{ name: "app", release: true, setup }] }),
    repo: () => ok(doc),
    secrets: () => ok(secrets),
    putSecret: () => ok({}),
    verify: () => ok(secrets),
    fetchRemote: () => ok({ mirror: doc.mirror, branches: doc.branches }),
    release: () => ok(release),
    listing: () => ok(listing),
    listingFileUrl: (name, path) =>
      "/api/repos/" + name + "/release/listing/file?path=" + encodeURIComponent(path),
    plan: (name, body) => { window.rcmStoreCalls.push(["plan", body]);
                            return ok({ job_id: 642 }, 202); },
    review: (name, body) => {
      window.rcmStoreCalls.push(["review", body]);
      if (window.rcmRefuse) return Promise.resolve({ ok: false, status: 409,
        body: { error: "refused", code: window.rcmRefuse, error_code: window.rcmRefuse } });
      return ok({ job_id: 660 }, 202);
    },
    validateListing: (name, body) => { window.rcmStoreCalls.push(["validate", body]);
      return ok({ ok: false, lines: ["ios/ko/keywords: 101 > 100"], exit: 1 }); },
    upload: () => ok({ job_id: 0 }, 202),
    github: () => ok({ log: [], tags: [], prs: null }),
  };
})();
"""

GROUPS_JS = """
(() => {
  const sec = (p) => document.querySelector('#review-panel .ssec[data-platform="' + p + '"]');
  const groups = (p) => [...sec(p).querySelectorAll('.grp')].map(g => g.dataset.group);
  const heads = (p) => [...sec(p).querySelectorAll('.grp h4')].map(h => h.textContent);
  return { ios: groups('ios'), android: groups('android'),
           iosHeads: heads('ios'), androidHeads: heads('android') };
})()
"""

FORBIDDEN_BUTTONS_JS = """
[...document.querySelectorAll('button, [role="button"], input[type="submit"], a.btn')]
  .map(b => (b.textContent || b.value || '').trim())
  .filter(t => /release this version|publish|rollout/i.test(t))
"""

SUBMIT = "#review-panel [data-submit-review]"
MANAGED = '#review-panel [data-review-check="managed"]'
ANDROID = '#review-panel [data-review-check="android"]'


def _type_n(c: Chrome, value: str) -> None:
    c.eval(
        "(() => { const i = document.getElementById('review-n'); i.value = "
        + json.dumps(value)
        + "; i.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
    )


def _wait(c: Chrome, js: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not c.eval(js):
        time.sleep(0.05)
    assert c.eval(js), js


def _submit_reason(c: Chrome) -> str:
    return c.eval(_q("#review-panel [data-submit-reason]", ".textContent"))


def test_store_review_panel_two_stores_typed_n_and_no_release_button(tmp_path):
    """항목 5~24 · 28 · 36 · 46~49 — 릴리스 상태가 있으면 Store 행은 plan.json 의
    요약(라이브 · 편집 중 · Play 트랙 · next N · 플랜 나이)이고 Build·upload 행은
    upload.json 의 결과 + 이 회차의 잡 목록이다. 본체는 App Store 절과 Google Play 절을
    **같은 그룹 순서**로 나란히 그리고, Play 에 없는 필드는 — 와 이유다. Submit 은
    (심사 플랜 ok · 안 낡음) ∧ (친 N = plan.n) ∧ (Android 를 골랐으면 관리형 게시 체크)
    일 때만 열린다 — 틀린 N 은 닫힌 채 «≠ 181», 맞는 N + 체크로 열린다. 보낸 본문은
    서버 계약 그대로고, 409 는 코드가 버튼 옆에 글자로 온다. `unsafe_release_type` 은
    닫을 수 없는 빨간 띠 + 닫힌 Submit. Release · Publish · Rollout 버튼은 어떤 상태에도
    없다."""
    srv = Server(tmp_path, workers=False)
    try:
        base = f"http://127.0.0.1:{srv.port}/?poll=1&lang=en"
        ready = _q(SUBMIT) + " !== null"
        with Chrome(tmp_path / "chrome-review", window="1240,900") as c:
            c.call("Page.addScriptToEvaluateOnNewDocument", {"source": RELEASE_STUB_JS})
            c.open(base, ready_js=_q('#view-nav a[data-nav="store"]') + " !== null")
            c.eval(f"localStorage.setItem('rcm.token', {json.dumps(srv.tokens['admin'])})")
            # 쿼리가 달라야 다시 싣는다 — 해시만 바뀌면 boot() 가 안 돌아 토큰을 안 읽는다
            c.open(
                base + "&admin=1#/store/app",
                ready_js=ready + " && document.querySelector('#tok-btn').textContent"
                ".indexOf('macmini-admin') >= 0",
            )
            rows = c.eval(STORE_ROWS_JS)
            by = {r["row"]: r for r in rows}
            assert [r["row"] for r in rows] == ["setup", "source", "build", "store"], rows
            # Build·upload 행(항목 5): upload.json 의 결과 — 초록 접힘, 버전 (N) · 올림 · 태그
            build = by["build"]
            assert build["state"] == "ok" and build["open"] is False, build
            assert "1.0.1 (181)" in build["head"] and "uploaded" in build["head"], build
            assert "prod/1.0.1-181" in build["head"], build
            items = c.eval(
                "[...document.querySelectorAll("
                "'#store details.srow[data-row=\"build\"] .checklist .ci')]"
                ".map(li => [li.className.replace('ci ', ''), li.textContent.trim().slice(0, 40)])"
            )
            assert [i[0] for i in items] == ["ok", "ok", "ok"], items
            assert items[0][1].startswith("✓#641 release-plan · plan · succeeded"), items
            # Store 행(항목 6): plan.json 의 요약 + Refresh(= release-plan 잡)
            store = by["store"]
            assert store["state"] == "ok" and store["open"] is False, store
            assert "App Store live 1.0.0 (180)" in store["head"], store
            assert "editing 1.0.1" in store["head"] and "Play production 180" in store["head"]
            assert "next N 181" in store["head"] and "1 warnings" in store["head"], store
            assert re.search(r"plan \d+m ago", store["head"]), store
            assert c.eval(_q("#store [data-plan-refresh]", ".disabled")) is False

            # 본체(항목 7): 네 행이 초록이라 펼쳐져 있고, 필은 «not submitted»
            pill = _q("#review-panel [data-panel-pill]", ".getAttribute('data-panel-pill')")
            assert c.eval("document.getElementById('review-panel').open") is True
            assert c.eval(pill) == "not_submitted"
            head = c.eval(_q("#review-panel > summary", ".textContent"))
            assert "1.0.1 · build 181 · Submit for review · App Store + Google Play" in head, head
            # 두 절 — 같은 그룹 순서 · 같은 줄 수 (항목 15)
            groups = c.eval(GROUPS_JS)
            assert groups["ios"] == [
                "screenshots",
                "version_info",
                "whats_new",
                "build",
                "review_info",
            ], groups
            assert groups["android"] == [
                "graphics",
                "store_listing",
                "release_notes",
                "release",
                "app_content",
            ], groups
            assert len(groups["iosHeads"]) == len(groups["androidHeads"]) == 5, groups
            assert groups["iosHeads"][1] == "Version information", groups
            assert groups["androidHeads"][1] == "Store listing", groups
            body = c.eval("document.getElementById('review-panel').innerText")
            assert "Play has no such field" in body, body[:1500]
            assert "Promotional text" in body and "Short description" in body, body[:1500]
            assert "ready" in body and "judged 4m ago" in body, body[:1500]
            assert "Approval does not release" in body
            assert "console-only — not touched by rcm" in body
            assert "unknown to the API" in body, "the managed-publishing pill never turns green"
            assert "undefined" not in body and "NaN" not in body
            # 글자 수 세기(항목 11) · changed 칩(항목 21) · 스크린샷 띠(항목 10)
            counters = c.eval(
                "[...document.querySelectorAll("
                "'#review-panel .ssec[data-platform=\"ios\"] .counter')].map(e => e.textContent)"
            )
            assert "11/30" in counters, counters  # «Daily notes» / subtitle 30
            assert c.eval("document.querySelectorAll('#review-panel .chip.changed').length") >= 1
            shots = c.eval(
                "[...document.querySelectorAll('#review-panel .ssec img')]"
                ".map(i => i.getAttribute('src'))"
            )
            assert len(shots) == 3, shots
            assert all("/release/listing/file?path=" in s for s in shots), shots
            ios_imgs = "document.querySelectorAll('#review-panel .ssec[data-platform=\"ios\"] img')"
            assert c.eval(ios_imgs + ".length") == 2
            # 릴리스 노트 — 같은 원문, 상한만 다르다(4000 · 500)
            assert "/4000" in body and "/500" in body, body[:2000]

            # Submit 활성 규칙(항목 22 · 23 · 28)
            disabled = _q(SUBMIT, ".disabled")
            assert c.eval(disabled) is True
            reason = _submit_reason(c)
            assert "type the build number" in reason and "managed-publishing" in reason, reason
            _type_n(c, "180")
            assert c.eval(disabled) is True, "a wrong N keeps the button closed"
            assert c.eval(_q("#review-panel [data-n-state]", ".textContent")) == "≠ 181"
            _type_n(c, "181")
            assert c.eval(disabled) is True, "the right N alone is not enough with Play selected"
            assert c.eval(_q("#review-panel [data-n-state]", ".textContent")) == "= 181"
            assert "managed-publishing" in _submit_reason(c)
            assert c.eval(_q(MANAGED, ".checked")) is False, "never pre-ticked"
            c.eval(_q(MANAGED, ".click()"))
            assert c.eval(disabled) is False, "right N + managed box → enabled"
            assert _submit_reason(c) == ""
            # 관리형 게시 체크는 Play 를 빼면 무의미해지고, 다시 넣어도 기억되지 않는다
            c.eval(_q(ANDROID, ".click()"))
            assert c.eval(disabled) is False, "iOS only: no managed box needed"
            c.eval(_q(ANDROID, ".click()"))
            assert c.eval(_q(MANAGED, ".checked")) is False
            assert c.eval(disabled) is True
            c.eval(_q(MANAGED, ".click()"))
            assert c.eval(disabled) is False

            # 출시 버튼은 없다 — 어떤 상태에도 (계약 §6)
            assert c.eval(FORBIDDEN_BUTTONS_JS) == []

            # Submit → 대화상자(되돌릴 수 없다 · 플랫폼 이름) → 확인 → 계약 그대로의 본문
            c.eval(_q(SUBMIT, ".click()"))
            assert c.eval("document.getElementById('submit-dialog').open") is True
            dlg = c.eval("document.getElementById('submit-dialog').innerText")
            assert "Submit 1.0.1 (181) for review?" in dlg and "cannot be undone" in dlg, dlg
            assert "App Store + Google Play" in dlg, dlg
            c.eval(_q("#submit-dialog [data-submit-go]", ".click()"))
            _wait(c, "window.rcmStoreCalls.length > 0")
            calls = c.eval("window.rcmStoreCalls")
            assert calls and calls[-1][0] == "review", calls
            assert calls[-1][1] == {
                "build_name": "1.0.1",
                "ref": "main",
                "mode": "submit",
                "platform": "both",
                "confirm_build_number": "181",
                "play_managed_publishing": "confirmed-on",
                "listing": "notes-only",
                "phased": "1",
            }, calls[-1]
            # 보낸 뒤 확인은 지워진다 — 다음 되돌릴 수 없는 일로 넘어가지 않는다
            _wait(c, "document.getElementById('review-n').value === ''")
            assert c.eval(_q(MANAGED, ".checked")) is False
            assert c.eval(disabled) is True

            # 409 — 서버의 코드가 버튼 옆에 그대로, 클라이언트는 돌아가지 않는다
            c.eval("window.rcmRefuse = 'managed_publishing_unconfirmed'")
            _type_n(c, "181")
            c.eval(_q(MANAGED, ".click()"))
            c.eval(_q(SUBMIT, ".click()"))
            c.eval(_q("#submit-dialog [data-submit-go]", ".click()"))
            _wait(c, _q("#review-panel [data-review-error]") + " !== null")
            err = c.eval(_q("#review-panel [data-review-error]", ".textContent"))
            assert err == "server refused: managed_publishing_unconfirmed", err
            assert c.eval(FORBIDDEN_BUTTONS_JS) == []
            assert c.page_errors() == []

            # 폰 폭(항목 41): 두 절이 세로로 쌓이고 옆으로 새지 않는다
            c.viewport(390, mobile=True)
            assert c.eval("document.documentElement.scrollWidth") <= 390
            cols = c.eval(
                "getComputedStyle(document.querySelector('#review-panel .stores'))"
                ".gridTemplateColumns.split(' ').length"
            )
            assert cols == 1, cols
            c.viewport(1240)

            # 항목 36 — unsafe_release_type: 닫을 수 없는 빨간 띠, 필 빨강, Submit 닫힘
            banner = "[data-unsafe-banner]"
            c.open(base + "&unsafe=1#/store/app", ready_js=_q(banner) + " !== null")
            assert c.eval(_q(banner, ".getAttribute('role')")) == "alert"
            kind = c.eval(_q(banner, ".getAttribute('data-unsafe-banner')"))
            assert kind == "unsafe_release_type", kind
            n_buttons = c.eval(f"document.querySelectorAll('{banner} button').length")
            assert n_buttons == 0, "not dismissable"
            assert "not set to manual release" in c.eval(_q(banner, ".textContent"))
            assert c.eval(pill) == "unsafe_release_type"
            assert c.eval("document.getElementById('review-panel').open") is False
            _type_n(c, "181")
            c.eval(_q(MANAGED, ".click()"))
            assert c.eval(disabled) is True, "unsafe → closed even with N and the box"
            assert "fix the release type" in _submit_reason(c)
            assert c.eval(FORBIDDEN_BUTTONS_JS) == []
            # 한국어로 바꿔도 스토어·필드 이름은 카탈로그에서 온다 — 판정 낱말은 서버 것 그대로.
            # 접혀 있어도 textContent 에는 본문이 있다.
            c.eval("document.getElementById('lang-btn').click()")
            ko = c.eval("document.getElementById('review-panel').textContent")
            assert "App Store · iOS" in ko and "버전 정보" in ko, ko[:800]
            assert "unsafe_release_type" in ko, ko[:800]
            assert c.page_errors() == []
    finally:
        srv.close()
