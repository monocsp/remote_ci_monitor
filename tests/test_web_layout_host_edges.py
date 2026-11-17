"""웹 UI(영역 B) — 호스트 · 머신 · 풀 · 머리줄 워커 필이 모서리 입력에서 무너지지 않는다.

Chrome 이 없으면 전부 skip(`test_web_browser` 의 규칙 그대로).

`test_web_layout_m5n` 처럼 `fake_rcm_http.FakeRcm` 이 **패키지의 web/ 파일**과 손으로 쓴 문서를
내준다. 문서는 픽스처를 고치지 않고 여기서 지어낸다 — 시각은 전부 브라우저의 「지금」 기준이다
(`환경 바뀜` 칩은 24시간 창을 브라우저 시계로 잰다).

모서리(장면 `crowd`):

- 워커 12개(로컬 레인 2 + 원격 10 — 꺼짐 · 보류 · 바쁨이 섞였고 이름 하나는 끊김 없는 80자 넘게)
  와 풀 6개(풀마다 호스트 카드 하나, 이름이 길다).
- 호스트 카드 — GPU 있음/없음(없는 이유가 길다) · 디스크 TB · 200자 프로세스 이름 · 5분 sparkline.
- 머신 50대 — 끊김 없는 80자 넘는 이름 · 긴 호스트 라벨 · 프로브 64개(값은 상한 120자) ·
  프리셋 40개(이름이 길다) · 점검 이름이 길고 `error_code`(`auto_check_limit` · `ref_unresolved`) ·
  `recheck_needed` · 바뀐 프로브 10개짜리 `환경 바뀜` 칩.

다른 장면: 긴 `hosts_error`(끊김 없는 한 덩어리), 머신 0대.

잠그는 계약:

1. **페이지가 옆으로 밀리지 않는다** — 390 · 1240 × ko · en (`OVERFLOW_JS` 는 `test_web_layout` 것).
2. **글자가 자기 칸 밖으로 새지 않는다** — 영역 B 의 요소마다 `scrollWidth <= clientWidth` 이거나,
   말줄임(`overflow: hidden` + `text-overflow: ellipsis`)이면 전문이 `title` 에 있다. 데스크톱에서는
   머신 표도 자기 상자(`.mwrap`) 안에서 옆으로 스크롤하지 않는다 — 끊김 없는 이름 하나가 표를
   통째로 밀면 다른 칸이 안 보인다.
3. **갱신이 레이아웃을 흔들지 않는다** — 5초 표본(값 · sparkline 한 점 · 프로세스 이름) ·
   머신 하나가 꺼짐 · 칩 하나가 새로 뜸. 갱신 전후로 호스트 절 · 머신 절 · 최근 절의 `top` 과
   호스트 카드의 높이를 잰다(2026-09-14 의 315px 점프를 다시 들이지 않는다 — PLAN.md
   「웹 UI (M2)」 호스트 절).
"""

from __future__ import annotations

import copy
import json
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

LONG_NAME = "build-farm-mac-studio-m4-ultra-rack3-slot12-" + "x" * 40  # 끊김 없는 84자
LONG_HOST = "epeuem-mac-studio-m4-ultra-192gb-rack3-slot12.corp.example.internal (a1f3c9d2)"
LONG_COMM = "/Applications/Xcode-27.2.app/Contents/Developer/Platforms/" + "Simulator" * 16
PROBE_VALUE = ("Xcode 27.2 · Build version 27B52 · " + "SDK iphonesimulator27.2 " * 6)[:120]
LONG_PRESET = "scenario-qa-device-matrix-with-a-deliberately-long-preset-name"
LONG_MEDIAN = "scenario_qa:" + "device_matrix_" * 6 + "iphone_se_3rd_generation"  # 끊김 없음
LONG_HOSTS_ERROR = "sampler:" + "collector_failed_" * 18 + " (ioreg · vm_stat · df all failed)"


def iso(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 문서 짓기 ────────────────────────────────────────────────────────────────


def host_sample(
    name: str,
    now: datetime,
    *,
    gpu: bool,
    tick: int = 0,
    cpu: float = 21.0,
    source: str = "local",
) -> dict[str, Any]:
    """호스트 표본 하나. `tick` 만큼 5초씩 흐른 표본이다 — 값 · sparkline · 프로세스가 바뀐다."""
    history = [
        {
            "at": iso(now - timedelta(seconds=5 * (60 - i))),
            "cpu_busy": (cpu + 37 * ((i + tick) % 3)) % 100,
            "mem_used_bytes": 14_900_000_000 + 1_000_000_000 * ((i + tick) % 5),
            "gpu_util_pct": ((i + tick) * 7) % 100 if gpu else None,
        }
        for i in range(60)
    ]
    top = [
        {"comm": LONG_COMM if tick % 2 == 0 else "dart", "cpu": 180.4 + tick, "rss_mb": 50000},
        {"comm": "flutter_tester" if tick % 2 == 0 else LONG_COMM[:150], "cpu": 95, "rss_mb": 3},
        {"comm": "gen_snapshot", "cpu": 103.2, "rss_mb": 169},
    ]
    return {
        "name": name,
        "source": source,
        "sampled_at": iso(now - timedelta(seconds=2)),
        "age_seconds": 2,
        "stale": False,
        "interval_seconds": 5,
        "os": "darwin 27.2 (arm64)",
        "cores": 32,
        "load": [3.48 + 40 * (tick % 2), 3.1, 2.9],
        "cpu": {"user": cpu - 4, "sys": 4, "idle": 100 - cpu, "busy": cpu},
        "memory": {
            "total_bytes": 206_158_430_208,
            "used_bytes": 15_032_385_536 if tick % 2 == 0 else 150_032_385_536,
            "compressed_bytes": 2_254_857_830 if tick % 2 == 0 else 52_254_857_830,
        },
        "gpu": (
            {
                "util_pct": 13 if tick % 2 == 0 else 100,
                "mem_used_bytes": 594_411_520 if tick % 2 == 0 else 120_594_411_520,
                "mem_total_bytes": None,
                "source": "ioreg",
            }
            if gpu
            else None
        ),
        "gpu_note": None if gpu else "no GPU sampler on this platform (nvidia-smi not found)",
        "disk": {
            "used_bytes": 7_289_995_948_032 if tick % 2 == 0 else 7_989_995_948_032,
            "free_bytes": 704_666_636_288 if tick % 2 == 0 else 4_666_636_288,
            "total_bytes": 7_994_662_584_320,
            "path": "/Users/ci/.local/share/rcm",
        },
        "top": top,
        "history": history,
    }


def workers(now: datetime, *, tick: int = 0) -> list[dict[str, Any]]:
    """로컬 레인 둘 + 원격 열 — 꺼짐 · 보류 · 바쁨 · 쉼이 섞였다."""
    since = iso(now - timedelta(minutes=3))
    out: list[dict[str, Any]] = [
        {"lane": 1, "state": "busy", "job_id": 412, "error": None, "since": since},
        {"lane": 2, "state": "busy", "job_id": 409, "error": None, "since": since},
    ]
    names = [LONG_NAME, "mac2-worker", "mbp", "mac-studio-2", "linux-gpu-box"]
    states = ["busy", "down", "held", "idle", "busy", "down", "idle", "held", "busy", "idle"]
    for i, st in enumerate(states):
        if tick and i == 3:
            st = "down"  # 갱신 뒤 하나가 꺼진다
        name = names[i % len(names)]
        w = {
            "lane": 1 + i // len(names),
            "worker": f"w{i}-{name}",
            "display_name": name,
            "state": st,
            "job_id": 5000 + i if st == "busy" else None,
            "error": None,
            "since": since,
        }
        if st == "held":
            w["hold_code"] = "cpu_busy"
            w["hold_detail"] = {"cpu_busy": 91}
        out.append(w)
    return out


POOL_NAMES = ["default", "mac2", "mbp-pool", "linux-gpu", LONG_NAME[:40], "ios-devices"]


def status_doc(now: datetime, *, tick: int = 0, hosts_error: str | None = None) -> dict:
    """`status-main` + 워커 12 · 풀 6 · 모서리 호스트 카드."""
    doc = json.loads((FIXTURES / "status-main.json").read_text())
    # 픽스처의 잡 시각을 지금으로 민다 — 경과가 두 달이 되면 다른 영역이 흔들린다
    fixture_now = datetime(2026, 9, 4, 0, 52, 12, tzinfo=UTC)
    doc = _shift(doc, now - fixture_now)
    doc["generated_at"] = iso(now)
    base = doc["pools"][0]
    base["hosts"] = [host_sample(LONG_HOST, now, gpu=True, tick=tick, cpu=21 + 70 * (tick % 2))]
    if hosts_error is not None:
        base["hosts"] = None
        base["hosts_error"] = hosts_error
    base["medians"][LONG_MEDIAN] = {"seconds": 4210, "wait_seconds": 30, "sample_count": 3}
    doc["server"]["workers"] = workers(now, tick=tick)
    doc["server"]["lanes"] = 2
    waiting = next(r for r in base["queue"] if r["state"] != "running")
    for i, name in enumerate(POOL_NAMES[1:], start=1):
        pool = copy.deepcopy(base)
        pool["name"] = name
        pool["lanes"] = 0 if i == 5 else 1
        pool["queue"] = [{**copy.deepcopy(waiting), "id": 6000 + i, "position": 1}]
        pool["recent"] = []
        pool["hosts"] = [
            host_sample(f"{name}-host-{LONG_HOST}", now, gpu=i % 2 == 0, tick=tick, source="worker")
        ]
        doc["pools"].append(pool)
    doc["server"]["generation"] = f"1731740000:{3 + tick}"
    # 요약이 바뀌면 페이지가 /api/machines 를 다시 받는다
    doc["server"]["machines"] = [{"tick": tick}]
    return doc


_ISO_LEN = len("2026-09-04T00:52:12Z")


def _shift(value: Any, delta: timedelta) -> Any:
    if isinstance(value, dict):
        return {k: _shift(v, delta) for k, v in value.items()}
    if isinstance(value, list):
        return [_shift(v, delta) for v in value]
    if isinstance(value, str) and len(value) == _ISO_LEN and value.endswith("Z") and "T" in value:
        try:
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return iso(at + delta)
    return value


def machine(i: int, now: datetime, *, tick: int = 0, rich: bool = False) -> dict[str, Any]:
    """머신 하나. `rich` 면 모든 모서리를 한 몸에 — 긴 이름 · 프로브 64 · 프리셋 40 · 긴 점검."""
    name = LONG_NAME if i == 0 else f"mac-{i:02d}" if i % 7 else f"{LONG_NAME[:30]}-{i}"
    probes: dict[str, Any] = {
        "xcode": {"state": "ok", "value": "Xcode 27.2 · Build version 27B52"},
        "flutter": {"state": "ok", "value": "Flutter 3.47.8 • channel stable"},
    }
    if rich:
        for p in range(64):
            probes[f"probe-{p:02d}-{'long' * 3}"] = {
                "state": "ok" if p % 5 else "error",
                "value": PROBE_VALUE if p % 5 else PROBE_VALUE.replace(" ", "_"),
                "previous": (PROBE_VALUE[:100] + " old") if p < 10 else None,
            }
    changed = iso(now - timedelta(hours=1)) if (rich or (tick and i == 2)) else None
    presets: dict[str, Any] = {}
    for p in range(40 if rich else 4):
        key = f"{LONG_PRESET}-{p:02d}" if rich else ["gate", "scenario-qa", "sim-check", "lint"][p]
        presets[key] = (
            {"state": "eligible", "code": "ok", "detail": {}}
            if p % 3
            else {"state": "ineligible", "code": "tool_missing", "detail": {"tools": ["fvm"]}}
        )
    checks: list[dict[str, Any]] = [
        {"preset": "sim-check", "state": "passed", "job_id": 3611, "auto_submitted": True}
    ]
    if rich:
        checks = [
            {
                "preset": f"{LONG_PRESET}-check-{c}",
                "state": ["failed", "needed", "running", "passed"][c % 4],
                "job_id": 3600 + c if c % 4 != 1 else None,
                "auto_submitted": True,
                "error_code": ["auto_check_limit", "ref_unresolved", None, None][c % 4],
                "recheck_needed": c % 4 == 1,
            }
            for c in range(8)
        ]
    return {
        "id": "@local" if i == 0 else f"w{i}-{name}",
        "display_name": name,
        "kind": "local" if i == 0 else "remote",
        "pool": POOL_NAMES[i % len(POOL_NAMES)],
        "lanes": 1,
        "alive": not (i % 9 == 4 or (tick and i == 1)),
        "host_label": LONG_HOST if i % 3 == 0 else f"mac-{i:02d} (7c{i:02d})",
        "host_key": f"{i:016x}",
        "accept_any": True,
        "fingerprint": {
            "hash": f"{i:016x}",
            "measured_at": iso(now - timedelta(minutes=2)),
            "stale": False,
            "changed_at": changed,
            "probes": probes,
        },
        "presets": presets,
        "checks": checks,
    }


def machines_doc(now: datetime, *, n: int = 50, tick: int = 0) -> dict:
    return {
        "generation": f"1731740000:{3 + tick}",
        "machines": [machine(i, now, tick=tick, rich=i == 0) for i in range(n)],
    }


# ── 장면 ─────────────────────────────────────────────────────────────────────


class Scene:
    """`FakeRcm` + 「지금」. `set(tick=…)` 으로 문서를 갈아 끼운다(다음 조회부터 새 문서)."""

    def __init__(self) -> None:
        self.srv = FakeRcm(static_dir=WEB)
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.srv.route("GET", "/api/whoami", {"error": "no token"}, status=401)
        self.set()

    def set(self, *, tick: int = 0, machines: int = 50, hosts_error: str | None = None) -> None:
        now = self.now + timedelta(seconds=5 * tick)
        self.srv.route("GET", "/api/status", status_doc(now, tick=tick, hosts_error=hosts_error))
        self.srv.route("GET", "/api/machines", machines_doc(now, n=machines, tick=tick))

    def url(self, lang: str) -> str:
        return f"{self.srv.url}/?poll=1&lang={lang}"


@pytest.fixture
def crowd() -> Iterator[Scene]:
    s = Scene()
    yield s
    s.srv.close()


def ready(lang: str, machines: int = 50) -> str:
    rows = (
        f"document.querySelectorAll('#machines tr[data-machine]').length === {machines}"
        if machines
        else "document.querySelector('#machines-section').hidden === true"
    )
    return (
        f"{rows} && document.querySelector('#host [data-host-body]').children.length > 0"
        f" && document.documentElement.lang === {json.dumps(lang)}"
    )


# 두 절을 펼친다 — 접힌 절의 넘침은 아무것도 증명하지 않는다. 사람의 선택처럼 저장하지는 않는다.
OPEN_JS = """
(() => {
  for (const id of ['host-details', 'machines', 'estimates']) {
    const d = document.getElementById(id);
    if (d && !d.open) { d.dataset.byRender = '1'; d.open = true; }
  }
  return document.getElementById('host-details').open;
})()
"""

# 영역 B 의 요소 중 **자기 칸 밖으로 글자가 새는** 것. 말줄임이면 전문이 title 에 있어야 한다.
SPILL_JS = """
(() => {
  const roots = ['#host', '#machines-section', '#hdr', '#estimates', '.pool-h'];
  const name = (el) => el.tagName.toLowerCase()
    + (el.id ? '#' + el.id : '')
    + (typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\\s+/).join('.') : '');
  const out = [];
  const seen = new Set();
  for (const sel of roots) {
    for (const root of document.querySelectorAll(sel)) {
      for (const el of [root, ...root.querySelectorAll('*')]) {
        if (seen.has(el)) continue;
        seen.add(el);
        const r = el.getBoundingClientRect();
        if (r.width === 0 || el.closest('svg')) continue;
        const cs = getComputedStyle(el);
        if (cs.display === 'inline' || cs.display === 'contents') continue;
        if (el.matches('.mwrap')) continue;  // 표의 상자는 아래에서 따로 잰다
        const over = el.scrollWidth - el.clientWidth;
        if (over <= 1) continue;
        const clipped = cs.overflowX === 'hidden' || cs.overflowX === 'clip';
        const ellipsis = cs.textOverflow === 'ellipsis';
        let titled = false;
        for (let p = el; p && p !== document.body; p = p.parentElement) {
          if (p.getAttribute('title')) { titled = true; break; }
        }
        if (clipped && ellipsis && titled) continue;
        if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
        out.push({el: name(el), over: over, clipped: clipped, ellipsis: ellipsis, titled: titled,
                  text: el.textContent.slice(0, 60)});
      }
    }
  }
  const boxes = [...document.querySelectorAll('#machines .mwrap')].map((w) => ({
    scrollW: w.scrollWidth, clientW: w.clientWidth}));
  return {spill: out.slice(0, 12), count: out.length, mwrap: boxes};
})()
"""

# 머리줄 — 워커 필 · 칩이 몇 줄을 쓰나, 그리고 칩·필마다 넘침.
HEADER_JS = """
(() => {
  const hdr = document.getElementById('hdr');
  const pills = [...document.querySelectorAll('#hdr .wk, #hdr .envchip')];
  // 줄은 필의 **세로 가운데**로 센다 — 버튼 필(24px)과 칩(20px)은 같은 줄에서도 top 이 2px 다르다
  const tops = new Set(pills.map((p) => {
    const r = p.getBoundingClientRect(); return Math.round((r.top + r.bottom) / 8); }));
  const hs = pills.map((p) => p.getBoundingClientRect().height);
  return {height: hdr.getBoundingClientRect().height, lines: tops.size, pills: pills.length,
          tallest: Math.max(...hs), shortest: Math.min(...hs),
          vw: document.documentElement.clientWidth};
})()
"""

# 갱신 전후로 비교할 자리. 절의 top 이 움직이면 그 위의 무언가가 자랐거나 줄었다.
RECTS_JS = """
(() => {
  const r = (sel) => { const e = document.querySelector(sel);
    if (!e) return null; const b = e.getBoundingClientRect();
    return {top: Math.round(b.top + scrollY), height: Math.round(b.height)}; };
  const cards = [...document.querySelectorAll('#host .hostcard')].map((c) =>
    Math.round(c.getBoundingClientRect().height));
  const rows = [...document.querySelectorAll('#machines tr[data-machine]')].map((c) =>
    Math.round(c.getBoundingClientRect().height));
  return {host: r('#host'), machines: r('#machines-section'), recent: r('#recent'),
          summary: r('#summary'), hdr: r('#hdr'), cards: cards, rows: rows,
          cls: window.__cls || 0, shifts: (window.__shifts || []).slice(0, 8)};
})()
"""

CLS_JS = """
(() => {
  window.__cls = 0; window.__shifts = [];
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      if (e.hadRecentInput) continue;
      window.__cls += e.value;
      for (const s of e.sources || []) {
        const n = s.node; window.__shifts.push({
          node: n ? (n.id || n.className || n.nodeName) : null,
          dy: Math.round(s.currentRect.top - s.previousRect.top),
          dh: Math.round(s.currentRect.height - s.previousRect.height)});
      }
    }
  }).observe({type: 'layout-shift', buffered: false});
  return true;
})()
"""

# 다음 조회를 지금 부른다 — 라이브 버튼을 두 번(정지 → 재개) 누르면 재개가 바로 다시 읽는다.
REFETCH_JS = """
(() => { const b = document.getElementById('live-btn'); b.click(); b.click(); return true; })()
"""


def open_scene(c: Chrome, scene: Scene, width: int, lang: str, machines: int = 50) -> None:
    c.viewport(width, 900, mobile=width <= PHONE_MAX)
    c.open(scene.url(lang), ready_js=ready(lang, machines), timeout=20)
    assert c.eval(OPEN_JS) is True
    time.sleep(0.3)


# ── 1 · 2: 옆으로 밀리지 않고 칸 밖으로 새지 않는다 ─────────────────────────────


def test_crowded_hosts_and_machines_never_scroll_sideways(crowd, tmp_path):
    bad: dict[tuple[int, str], dict] = {}
    with Chrome(tmp_path / "chrome-b-crowd", window="1280,900") as c:
        for width, lang in CASES:
            open_scene(c, crowd, width, lang)
            m = c.eval(OVERFLOW_JS)
            assert m["innerWidth"] == width, f"{lang}@{width}: viewport {m['innerWidth']}px"
            if m["scrollWidth"] > m["clientWidth"] + 1 or m["wide"]:
                bad[(width, lang)] = m
    assert not bad, "가로로 넘친다:\n" + "\n".join(
        f"  {lang} @ {width}px — {describe(m)}" for (width, lang), m in bad.items()
    )


def test_crowded_text_stays_inside_its_box(crowd, tmp_path):
    found: dict[tuple[int, str], dict] = {}
    with Chrome(tmp_path / "chrome-b-spill", window="1280,900") as c:
        for width, lang in CASES:
            open_scene(c, crowd, width, lang)
            found[(width, lang)] = c.eval(SPILL_JS)
    msgs = []
    for (width, lang), m in found.items():
        if m["count"]:
            msgs.append(f"  {lang} @ {width}px — {m['count']}개가 칸 밖으로 샌다:")
            msgs += [f"    {s}" for s in m["spill"]]
        if width > PHONE_MAX:
            for box in m["mwrap"]:
                if box["scrollW"] > box["clientW"] + 1:
                    msgs.append(
                        f"  {lang} @ {width}px — 머신 표가 자기 상자에서 옆으로 스크롤한다 "
                        f"({box['scrollW']} > {box['clientW']}) — 긴 이름이 표를 밀었다"
                    )
    assert not msgs, "\n".join(msgs)


def test_long_titles_are_truncated_with_the_full_text_in_title(crowd, tmp_path):
    """긴 이름은 잘려도 전문이 남는다 — 머신 이름 · 호스트 라벨 · 지문 · 카드 이름 · 프로세스."""
    js = """
    (() => {
      const t = (sel) => [...document.querySelectorAll(sel)].map((e) => {
        let p = e; while (p && !p.getAttribute('title')) p = p.parentElement;
        return p ? p.getAttribute('title') : null; });
      return {
        mname: t('#machines tr[data-machine="@local"] td.mname .mono')[0],
        mhost: t('#machines tr[data-machine="@local"] td.mhost')[0],
        mfp: t('#machines tr[data-machine="@local"] td.mfp .clamp')[0],
        hn: t('#host .hostcard .hn .hname')[0],
        top: t('#host .hostcard .top b')[0],
        wk: t('#hdr .wk')[2],
      };
    })()
    """
    with Chrome(tmp_path / "chrome-b-title", window="1280,900") as c:
        open_scene(c, crowd, 1240, "ko")
        m = c.eval(js)
    assert m["mname"] == LONG_NAME, m
    assert m["mhost"] == LONG_HOST, m
    # 지문 칸은 프로브마다 짧은 값이다 — title 은 64개 전부를 싣는다(마지막 것까지)
    assert m["mfp"] and "probe-63-longlonglong 27.2" in m["mfp"], m
    assert m["hn"] and LONG_HOST in m["hn"], m
    assert m["top"] == LONG_COMM, m
    assert m["wk"] and LONG_NAME in m["wk"], m


MACHINE_ROW_JS = """
(() => {
  const tr = document.querySelector('#machines tr[data-machine="@local"]');
  const other = document.querySelector('#machines tr[data-machine="w1-mac-01"]');
  const cells = [...tr.children].map((td) => ({cls: td.className,
    w: Math.round(td.getBoundingClientRect().width)}));
  const lh = parseFloat(getComputedStyle(tr.querySelector('td.mfp')).lineHeight);
  return {cells: cells, height: tr.getBoundingClientRect().height, lineHeight: lh,
          plain: other.getBoundingClientRect().height};
})()
"""


def test_machine_table_columns_hold_against_one_long_row(crowd, tmp_path):
    """끊김 없는 이름 · 프로브 64개 · 긴 점검이 한 행에 몰려도 칸은 제 폭이고 행은 몇 줄이다."""
    got = {}
    with Chrome(tmp_path / "chrome-b-mtab", window="1280,900") as c:
        for width, lang in CASES:
            open_scene(c, crowd, width, lang)
            got[(width, lang)] = c.eval(MACHINE_ROW_JS)
    for (width, lang), m in got.items():
        where = f"{lang}@{width}"
        if width > PHONE_MAX:
            narrow = [x for x in m["cells"] if x["w"] < 70]
            assert not narrow, f"{where}: 눌린 칸 {narrow} (전체 {m['cells']})"
        # 지문 두 줄 · 점검 세 줄에서 자른다 — 64개 프로브가 행을 수십 줄로 늘리지 않는다
        limit = 4 if width > PHONE_MAX else 9
        assert m["height"] <= m["lineHeight"] * limit + 16, f"{where}: 행 높이 {m}"


def test_many_workers_keep_the_header_short(crowd, tmp_path):
    """워커 12개 + 칩이 있어도 필은 **한 줄**이고 필마다 한 줄이다.

    필이 줄을 바꿔 쌓이면 잡이 하나 시작·끝날 때마다(필 글자 `대기` ↔ `진행 중 #5008`) 줄 수가
    바뀌어 머리줄 아래 전부가 뛴다. 넘치는 필은 그 줄 안에서 옆으로 민다(`.workers` 가 스크롤).
    """
    got = {}
    with Chrome(tmp_path / "chrome-b-header", window="1280,900") as c:
        for width, lang in CASES:
            open_scene(c, crowd, width, lang)
            got[(width, lang)] = c.eval(HEADER_JS)
    for (width, lang), m in got.items():
        assert m["pills"] >= 12, m
        assert m["lines"] == 1, f"{lang}@{width}: 워커 필이 {m['lines']}줄로 쌓인다 ({m})"
        assert m["tallest"] <= m["shortest"] * 1.5, f"{lang}@{width}: 필 하나가 두 줄이다 ({m})"
        assert m["height"] <= (200 if width <= PHONE_MAX else 120), f"{lang}@{width}: 머리줄 {m}"


# ── 다른 장면 ────────────────────────────────────────────────────────────────


def test_long_hosts_error_wraps_inside_the_page(crowd, tmp_path):
    crowd.set(hosts_error=LONG_HOSTS_ERROR)
    bad = {}
    with Chrome(tmp_path / "chrome-b-hosts-error", window="1280,900") as c:
        for width, lang in CASES:
            open_scene(c, crowd, width, lang)
            m = c.eval(OVERFLOW_JS)
            s = c.eval(SPILL_JS)
            text = c.eval("document.querySelector('#host').innerText")
            assert LONG_HOSTS_ERROR in text.replace("\n", ""), text[:400]
            if m["scrollWidth"] > m["clientWidth"] + 1 or m["wide"] or s["count"]:
                bad[(width, lang)] = (m, s)
    assert not bad, "긴 hosts_error 가 넘친다:\n" + "\n".join(
        f"  {lang} @ {w}px — {describe(m)}\n    spill {s['spill']}"
        for (w, lang), (m, s) in bad.items()
    )


def test_zero_machines_hide_the_section_and_the_chip(crowd, tmp_path):
    crowd.set(machines=0)
    with Chrome(tmp_path / "chrome-b-zero", window="1280,900") as c:
        open_scene(c, crowd, 390, "ko", machines=0)
        hidden = c.eval("document.querySelector('#machines-section').hidden")
        chips = c.eval("document.querySelectorAll('#hdr .envchip').length")
        m = c.eval(OVERFLOW_JS)
    assert hidden is True
    assert chips == 0
    assert m["scrollWidth"] <= m["clientWidth"] + 1, describe(m)


# ── 3: 갱신이 레이아웃을 흔들지 않는다 ─────────────────────────────────────────


def wait_for(c: Chrome, js: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if c.eval(js) is True:
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {js!r}")


UPDATED_JS = (
    "document.querySelector('#machines tr[data-machine=\"w1-mac-01\"]')"
    "?.classList.contains('down') === true"
    " && document.querySelectorAll('#hdr .envchip').length === 2"
)


@pytest.mark.parametrize("width", [390, 1240])
def test_an_update_does_not_move_the_sections(crowd, tmp_path, width):
    with Chrome(tmp_path / f"chrome-b-update-{width}", window="1280,900") as c:
        open_scene(c, crowd, width, "ko")
        assert c.eval(CLS_JS) is True
        before = c.eval(RECTS_JS)
        crowd.set(tick=1)
        assert c.eval(REFETCH_JS) is True
        wait_for(c, UPDATED_JS)
        time.sleep(0.5)
        after = c.eval(RECTS_JS)
    moved = {
        k: (before[k], after[k])
        for k in ("summary", "host", "machines", "recent")
        if before[k] and after[k] and before[k]["top"] != after[k]["top"]
    }
    assert not moved, f"@{width}: 갱신이 절을 옮겼다 {moved} · shifts {after['shifts']}"
    assert before["cards"] == after["cards"], (
        f"@{width}: 호스트 카드 높이가 갱신마다 바뀐다 {before['cards']} → {after['cards']}"
    )
    assert before["rows"][:3] == after["rows"][:3] or width <= PHONE_MAX, (
        f"@{width}: 머신 행 높이가 꺼짐 하나에 바뀐다 {before['rows'][:3]} → {after['rows'][:3]}"
    )
    assert after["cls"] < 0.01, f"@{width}: CLS {after['cls']:.3f} — {after['shifts']}"
