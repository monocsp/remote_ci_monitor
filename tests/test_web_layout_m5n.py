"""웹 UI(M5n 역할 C) — 머신 표 · `환경 바뀜` 칩 · 새 이유가 들어간 화면이 390px · 1240px × 한국어 ·
영어에서 옆으로 밀리지 않는다. Chrome 이 없으면 skip(`test_web_browser` 의 규칙 그대로 —
`RCM_CHROME` 이 있으면 Chrome 이 필수다).

`test_web_layout` 은 진짜 서버의 장면을 쓴다. M5n 의 머신 문서는 역할 B 의 서버 구현이 있어야
나오므로, 여기서는 `fake_rcm_http.FakeRcm` 이 **패키지의 web/ 파일**과 손으로 쓴 `/api/status` ·
`/api/machines` 를 내준다(픽스처: `tests/web/fixtures/status-main.json` + 대기 행 두 개 ·
`machines-m5n.json`). 페이지가 그 문서를 어떻게 그리는지만 잰다.

잠그는 DOM(가정 C21): 머신 표는 `<details id="machines">` 안에 있고 머신마다
`tr[data-machine="<id>"]` 한 행, 다시 점검 버튼은 `button.recheck`. 접힌 채 그려지므로 재기 전에
`open` 을 켠다 — 접힌 표의 넘침은 아무것도 증명하지 않는다.

**구현보다 먼저 썼다(test-first)** — 오늘은 `#machines` 가 없어 「page not ready」로 빨갛다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from test_web_browser import Chrome, pytestmark  # noqa: F401  (Chrome 이 없으면 skip)
from test_web_layout import OVERFLOW_JS, describe

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "remote_ci_monitor" / "web"
FIXTURES = ROOT / "tests" / "web" / "fixtures"
CASES = ((390, "ko"), (390, "en"), (1240, "ko"), (1240, "en"))
PHONE_MAX = 720

NO_CAPABLE_DETAIL = [
    {
        "worker": "mbp",
        "display_name": "mbp",
        "state": "ineligible",
        "code": "tool_missing",
        "detail": {"tools": ["fvm"]},
    },
    {
        "worker": "mac2-worker",
        "display_name": "mac2-worker",
        "state": "down",
        "code": "worker_down",
        "detail": {},
    },
]


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
FIXTURE_NOW = datetime(2026, 9, 4, 0, 52, 12, tzinfo=UTC)


def shifted(value: Any, delta: timedelta) -> Any:
    """문서 안의 UTC 시각을 전부 `delta` 만큼 민다 — 픽스처의 「지금」을 브라우저의 지금으로.

    페이지는 상대 시각(경과 · 24시간 창)을 브라우저 시계로도 잰다. 픽스처 시각(2026-09-04)
    그대로면 「한 시간 전에 바뀜」이 두 달 전이 되어 칩이 안 뜬다.
    """
    if isinstance(value, dict):
        return {k: shifted(v, delta) for k, v in value.items()}
    if isinstance(value, list):
        return [shifted(v, delta) for v in value]
    if isinstance(value, str) and _ISO.match(value):
        at = datetime.fromisoformat(value.replace("Z", "+00:00")) + delta
        return at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def status_doc() -> dict:
    """`status-main` + any-capable 대기 행 둘(맞는 머신 없음 · 점검 실패) + 머신 요약."""
    doc = json.loads((FIXTURES / "status-main.json").read_text())
    queue = doc["pools"][0]["queue"]
    base = next(r for r in queue if r["id"] == 414)
    queue.append(
        {
            **base,
            "id": 430,
            "key": "gate-with-a-rather-long-preset-name",
            "position": 2,
            "reason": "no_capable_machine",
            "placement": "capable",
            "placement_detail": NO_CAPABLE_DETAIL,
        }
    )
    queue.append(
        {
            **base,
            "id": 431,
            "key": "scenario-qa",
            "position": 3,
            "reason": "check_failed",
            "placement": "pool",
            "placement_detail": [
                {
                    "worker": "@local",
                    "display_name": "Mac mini",
                    "state": "ineligible",
                    "code": "check_failed",
                    "detail": {"check": "sim-check", "job_id": 3620},
                }
            ],
        }
    )
    machines = json.loads((FIXTURES / "machines-m5n.json").read_text())["machines"]
    doc["server"]["generation"] = "1731740000:3"
    doc["server"]["machines"] = [
        {
            "id": m["id"],
            "display_name": m["display_name"],
            "kind": m["kind"],
            "pool": m["pool"],
            "host_label": m["host_label"],
            "alive": m["alive"],
            "fingerprint_hash": m["fingerprint"]["hash"],
            "fingerprint_changed_at": m["fingerprint"]["changed_at"],
            "eligible_count": sum(1 for v in m["presets"].values() if v["state"] == "eligible"),
            "preset_count": len(m["presets"]),
            "problems": [c["preset"] for c in m["checks"] if c["state"] == "failed"],
        }
        for m in machines
    ]
    return doc


@pytest.fixture
def page() -> Iterator[FakeRcm]:
    srv = FakeRcm(static_dir=WEB)
    delta = datetime.now(UTC).replace(microsecond=0) - FIXTURE_NOW
    machines = json.loads((FIXTURES / "machines-m5n.json").read_text())
    srv.route("GET", "/api/status", shifted(status_doc(), delta))
    srv.route("GET", "/api/machines", shifted(machines, delta))
    srv.route("GET", "/api/whoami", {"error": "no token"}, status=401)
    yield srv
    srv.close()


def url(srv: FakeRcm, lang: str) -> str:
    return f"{srv.url}/?poll=1&lang={lang}"


def ready(lang: str) -> str:
    return (
        "document.querySelectorAll('#machines tr[data-machine]').length === 3 && "
        "document.querySelector('#queue [data-job=\"430\"]') !== null && "
        f"document.documentElement.lang === {json.dumps(lang)}"
    )


OPEN_JS = (
    "(() => { const d = document.querySelector('#machines'); d.open = true; return d.open; })()"
)


def test_machines_table_and_new_reasons_never_scroll_sideways(page, tmp_path):
    measured: dict[tuple[int, str], dict] = {}
    with Chrome(tmp_path / "chrome-m5n", window="1280,900") as c:
        for width, lang in CASES:
            c.viewport(width, 900, mobile=width <= PHONE_MAX)
            c.open(url(page, lang), ready_js=ready(lang))
            assert c.eval(OPEN_JS) is True
            time.sleep(0.2)
            m = c.eval(OVERFLOW_JS)
            assert m["innerWidth"] == width, f"{lang}@{width}: viewport {m['innerWidth']}px"
            measured[(width, lang)] = m
    bad = {
        k: m for k, m in measured.items() if m["scrollWidth"] > m["clientWidth"] + 1 or m["wide"]
    }
    assert not bad, "가로로 넘친다:\n" + "\n".join(
        f"  {lang} @ {width}px — {describe(m)}" for (width, lang), m in bad.items()
    )


def test_machines_table_is_collapsed_and_has_no_recheck_without_admin(page, tmp_path):
    with Chrome(tmp_path / "chrome-m5n-collapsed", window="1240,900") as c:
        c.open(url(page, "ko"), ready_js=ready("ko"))
        is_open = c.eval("document.querySelector('#machines').open")
        buttons = c.eval("document.querySelectorAll('#machines button.recheck').length")
        rows = c.eval(
            "[...document.querySelectorAll('#machines tr[data-machine]')]"
            ".map(r => r.getAttribute('data-machine'))"
        )
        text = c.eval("document.querySelector('#machines').textContent")
    assert is_open is False, "§8.3: 머신 표는 접힌 채로 시작한다"
    assert buttons == 0, "admin 토큰이 없으면 다시 점검 버튼이 없다"
    assert rows == ["@local", "mac2-worker", "mbp"], rows
    assert "실패 · sim-check · #3620" in text, text


def test_the_env_changed_chip_and_the_no_capable_reason_are_on_the_page(page, tmp_path):
    with Chrome(tmp_path / "chrome-m5n-chip", window="1240,900") as c:
        c.open(url(page, "ko"), ready_js=ready("ko"))
        body = c.eval("document.body.innerText")
    assert "환경 바뀜" in body, body[:1500]
    assert "실행 가능한 머신 없음 · mbp: fvm 없음 · mac2-worker: 꺼짐" in body, body[:1500]
