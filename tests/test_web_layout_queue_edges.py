"""웹 UI 큐 화면(영역 A) — 가장자리 입력에서도 표와 목록이 제 모양을 지킨다. Chrome 이 없으면 skip
(`test_web_browser` 의 규칙 그대로 — `RCM_CHROME` 이 있으면 Chrome 이 필수다).

잡는 것(390px · 1240px × 한국어 · 영어):

1. **페이지가 옆으로 밀리지 않는다** — `test_web_layout.OVERFLOW_JS` 그대로.
2. **큐 표가 자기 상자 안에서도 옆으로 스크롤하지 않는다** — `.qwrap` 은 넘침을 가둘 뿐이다.
   1240px 에서 표가 가로 스크롤을 얻으면 이유 · 완료 예상이 화면 밖으로 숨는다.
3. **글자가 자기 칸 밖으로 새지 않는다** — 칸(`td` · 최근 행의 격자 칸 · 요약 칸 · 워커 필 ·
   서랍 머리)마다 글자 조각(`Range` 의 줄 상자)과 자식 상자가 칸 안에 있어야 한다. 넘침을 잘라
   내는 조상(`overflow` ≠ visible)이 있으면 그 조상이 칸 안에 있는지만 본다 — 잘린 글은 새지 않는다.
4. **말줄임한 글은 전문을 `title` 로 준다** — `text-overflow: ellipsis` 로 실제로 잘린 요소, 또는
   줄 수를 못 박은(`-webkit-line-clamp`) 요소가 잘렸으면 자기나 칸 안의 조상에 `title` 이 있다.
5. **갱신에 행이 출렁이지 않는다** — 1초 틱, 같은 문서의 재조회, 단계 이름이 아주 길어짐,
   대기 이유가 바뀜(줄 서 기다림 → 그룹에 막힘) 뒤에 큐 행 · 최근 행의 높이가 그대로다.

장면은 `fake_rcm_http.FakeRcm` 이 **패키지의 web/ 파일**과 손으로 만든 `/api/status` 를 내준다
(`test_web_layout_m5n` 과 같은 방식 — 픽스처 `status-main.json` 을 브라우저의 지금으로 민다).
가장자리 입력: 120자 넘는 띄어쓰기 없는 프리셋 · 키, 긴 요청자 라벨, 두 배 폭 한국어, 300자
요약 · 실패 단계, 40자 링크 라벨과 500자 주소, 긴 이름 · 긴 도구 목록의 배치 메모 30개, 큰
`blocked_by` 숫자, 7자리 id, 0개 · 200개 대기, 풀 6개, `99h 59m`, 120자 단계 이름, 실패 64개.

`RCM_UI_SHOTS=<폴더>` 를 주면 장면마다 `<scene>-<width>-<lang>-<RCM_UI_SHOTS_TAG>.png` 를 남긴다
(가독성 검토용 — 기본 태그 `after`). 시험의 판정에는 영향이 없다.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from test_web_browser import Chrome, pytestmark  # noqa: F401  (Chrome 이 없으면 skip)
from test_web_layout import OVERFLOW_JS, describe
from test_web_layout_m5n import FIXTURE_NOW, shifted

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "remote_ci_monitor" / "web"
FIXTURES = ROOT / "tests" / "web" / "fixtures"
CASES = ((390, "ko"), (390, "en"), (1240, "ko"), (1240, "en"))
PHONE_MAX = 720
ME = "very.long.user.name-laptop"

# ── 가장자리 문자열 ───────────────────────────────────────────────────────────

# 띄어쓰기도 하이픈도 없는 130자 — 줄을 끊을 자리가 하나도 없다(밑줄은 끊는 자리가 아니다)
LONG_KEY = "gate:" + "_".join(
    "release candidate full regression matrix with golden screenshots and integration suite"
    " shard 0001 of 0016 ios android web macos".split()
)
LONG_PRESET = LONG_KEY.split(":", 1)[1]
KO_KEY = "전체게이트회귀검증" * 7  # 63자 두 배 폭, 띄어쓰기 없음
LONG_LABEL = "very.long.user.name@some-extremely-long-hostname.local"
LONG_GROUP = "shared-ios-simulators-and-android-emulators-device-farm-group"
LONG_STEP = "integration_test_" + "x" * 30 + "_" + "notification_settings_golden_" * 2 + "end"
LONG_STEP = (LONG_STEP * 2)[:120]
KO_STEP = "알림설정화면골든스크린샷통합시험" * 5 + "끝"  # 81자
LONG_STEP_2 = ("flutter_drive_target_" + "y" * 40 + "_") * 3
LINK_LABEL = "가나다라마바사아자차카타파하" * 2 + "PR #816 긴 라벨"  # 40자 — 서버의 상한
assert len(LINK_LABEL) == 40
LINK_URL = "https://example.com/org/app/pull/816?" + "q=" + "a" * 461
assert len(LINK_URL) == 500
SUMMARY_300 = ("Golden mismatch in notification_settings_screen_" + "z" * 40 + " ") * 4
SUMMARY_300 = SUMMARY_300[:300]
FAILED_STEP_300 = ("integration_test/" + "very_long_test_file_name_" * 4)[:100] * 3
LONG_MACHINE = "build-farm-mac-studio-m4-ultra-rack-07-in-the-basement-closet"
LONG_TOOLS = [
    "flutter-3.47.8-stable",
    "xcode-27.2-beta",
    "android-sdk-cmdline-tools-latest",
    "cocoapods-1.17.0",
    "fvm",
    "java-temurin-21",
]


def job_url(job_id: int) -> str:
    return f"http://macmini:8787/#/jobs/{job_id}"


def base_doc() -> dict:
    return json.loads((FIXTURES / "status-main.json").read_text())


def _running(base: dict, **over: Any) -> dict:
    row = copy.deepcopy(base)
    row.update(over)
    return row


def long_text_doc() -> dict:
    """긴 글이 다 모인 장면 — 도는 행 둘, 기다리는 행 넷, 최근 넷, 원격 워커 필 셋."""
    doc = base_doc()
    pool = doc["pools"][0]
    q = {r["id"]: r for r in pool["queue"]}
    run1 = _running(
        q[412],
        id=1234567,
        preset=LONG_PRESET,
        key=LONG_KEY,
        inputs={"scope": "full_" + "x" * 70, "suite": "device-smoke"},
        concurrency_group=LONG_GROUP,
        requester={"name": ME, "label": LONG_LABEL},
        joiners=[{"name": f"j{i}", "label": LONG_LABEL, "joined_at": None} for i in range(3)],
        link={"url": LINK_URL, "label": LINK_LABEL},
        url=job_url(1234567),
    )
    run1["started_at"] = "2026-09-01T00:53:12Z"  # 99h 59m 전
    run1["estimate"].update(elapsed_seconds=359940, expected_seconds=360000, remaining_seconds=60)
    run1["estimate"]["finish_at"] = "2026-09-04T00:53:12Z"
    run1["progress"].update(current_name=LONG_STEP, current_seconds=359000, job_seconds=359940)
    run1["progress"]["steps"][-1]["name"] = LONG_STEP
    run1["progress"]["steps"][0]["name"] = LONG_STEP_2
    run1["log_tail"] = ["[test] " + "w" * 400, "[test] 긴 한국어 로그 줄" * 20]
    run2 = _running(
        q[409],
        id=1234568,
        preset="qa",
        key=KO_KEY,
        requester={"name": "kim", "label": "김철수@회의실-맥북-프로-열여섯인치"},
        url=job_url(1234568),
    )
    run2["progress"].update(current_name=KO_STEP)
    run2["progress"]["steps"][-1]["name"] = KO_STEP
    run2["estimate"].update(stuck=True, stuck_code="over_step", step_expected_seconds=20)
    run2["reason"] = "stuck"
    blocked = _running(
        q[413],
        id=1234569,
        key=LONG_KEY,
        preset=LONG_PRESET,
        concurrency_group=LONG_GROUP,
        blocked_by={"job_id": 9999999, "group": LONG_GROUP, "remaining_seconds": 359940},
        requester={"name": ME, "label": LONG_LABEL},
        url=job_url(1234569),
        link={"url": "https://example.com/" + "p" * 200, "label": ""},
    )
    blocked["estimate"].update(wait_seconds=359940, waited_seconds=359940)
    waiting = _running(
        q[414],
        id=1234570,
        key=KO_KEY,
        preset="gate",
        ahead_job_id=9999998,
        # 그룹은 프리셋의 성질이라 처음부터 있다 — 아래 `updated_doc` 은 이유만 바꾼다
        concurrency_group=LONG_GROUP,
        placement="capable",
        position=2,
        requester={"name": "kim", "label": "김철수@회의실-맥북-프로-열여섯인치"},
        url=job_url(1234570),
    )
    waiting["estimate"].update(wait_seconds=359940)
    no_capable = _running(
        q[414],
        id=1234571,
        key="scenario-qa:" + "ipad_pro_13_inch_m4_" * 4,
        position=3,
        reason="no_capable_machine",
        placement="capable",
        placement_detail=[
            {
                "worker": LONG_MACHINE,
                "display_name": LONG_MACHINE,
                "state": "ineligible",
                "code": "tool_missing",
                "detail": {"tools": LONG_TOOLS},
            },
            {
                "worker": "mac2-worker",
                "display_name": "맥미니-두번째-작업기계-회의실-선반-위",
                "state": "down",
                "code": "worker_down",
                "detail": {},
            },
        ],
        url=job_url(1234571),
    )
    upload = _running(
        q[415],
        id=1234572,
        key=LONG_KEY,
        reason="upload_stalled",
        requester={"name": "z", "label": LONG_LABEL},
        url=job_url(1234572),
    )
    pool["queue"] = [run1, run2, blocked, waiting, no_capable, upload]

    recent = pool["recent"]
    failed = copy.deepcopy(recent[0])
    failed.update(
        id=1234560,
        key=LONG_KEY,
        preset=LONG_PRESET,
        summary=SUMMARY_300,
        failed_step=FAILED_STEP_300,
        requester={"name": ME, "label": LONG_LABEL},
        link={"url": LINK_URL, "label": LINK_LABEL},
        exit_code=137,
        job_seconds=359940,
        inputs={"scope": "full_" + "x" * 70},
    )
    failed["artifacts"] = {
        "state": "ready",
        "file_count": 1234,
        "bundle_bytes": 987654321098,
        "expires_at": "2026-09-30T00:00:00Z",
    }
    ko_ok = copy.deepcopy(recent[1])
    ko_ok.update(
        id=1234561,
        key=KO_KEY,
        summary="모든패키지가초록색으로통과했고골든스크린샷도전부일치했습니다" * 4,
        requester={"name": "kim", "label": "김철수@회의실-맥북-프로-열여섯인치"},
    )
    timed = copy.deepcopy(recent[0])
    timed.update(
        id=1234562,
        state="timed_out",
        key=KO_KEY,
        summary="",
        failed_step=KO_STEP,
        job_seconds=359940,
    )
    cancelled = copy.deepcopy(recent[1])
    cancelled.update(
        id=1234563,
        state="cancelled",
        started_at=None,
        key=LONG_KEY,
        cancelled_by=ME,
        requester={"name": ME, "label": LONG_LABEL},
        summary="",
    )
    pool["recent"] = [failed, ko_ok, timed, cancelled] + recent[2:]

    doc["server"]["workers"] = [
        {"lane": 1, "state": "busy", "job_id": 1234567, "error": None, "since": None},
        {"lane": 2, "state": "busy", "job_id": 1234568, "error": None, "since": None},
        {
            "lane": 1,
            "state": "busy",
            "job_id": 9999997,
            "worker": "w-long",
            "display_name": LONG_MACHINE,
            "pool": "default",
        },
        {
            "lane": 2,
            "state": "down",
            "job_id": None,
            "worker": "w-ko",
            "display_name": "맥미니-두번째-작업기계-회의실-선반-위",
            "pool": "default",
        },
        {"lane": 1, "state": "idle", "job_id": None, "worker": "w" * 60, "pool": "default"},
    ]
    return doc


def failures_doc(job_id: int) -> dict:
    """펼친 최근 행이 받는 잡 문서 — 실패한 이름 64개(긴 이름 · 스텝 · 판정 섞어서)."""
    verdicts = ("new", "persistent", "intermittent", "flaky")
    return {
        "id": job_id,
        "failures": [
            {
                "name": f"integration_test/notification_settings_golden_{i:02d}_" + "q" * 60,
                "step": i % 7 == 0,
                "verdict": verdicts[i % 4],
                "seen": 3,
                "window": 10,
            }
            for i in range(64)
        ],
        "failures_truncated": False,
    }


def placement_doc() -> dict:
    """배치 메모 30개 — 맞는 머신 없음 · 점검 실패 · 점검 중 · 펼친 머신별 판정 목록."""
    doc = base_doc()
    pool = doc["pools"][0]
    base = next(r for r in pool["queue"] if r["id"] == 414)
    notes = []
    codes = ("tool_missing", "workspace_tool", "check_failed", "check_running", "report_stale")
    for i in range(30):
        code = codes[i % len(codes)]
        ko_name = f"원격작업기계이름이아주긴맥스튜디오{i:02d}"
        name = f"{LONG_MACHINE}-{i:02d}" if i % 3 else ko_name
        notes.append(
            {
                "worker": f"w{i}",
                "display_name": name,
                "state": "down" if i % 11 == 0 else "ineligible",
                "code": code,
                "detail": {
                    "tools": LONG_TOOLS,
                    "check": "simulator-runtime-check-" + "k" * 30,
                    "job_id": 9000000 + i,
                },
            }
        )
    notes.append({"worker": "ok", "display_name": "Mac mini", "state": "eligible", "code": "ok"})
    rows = []
    for j, reason in enumerate(("no_capable_machine", "check_failed", "check_pending")):
        rows.append(
            {
                **copy.deepcopy(base),
                "id": 2000001 + j,
                "key": LONG_KEY if j != 1 else KO_KEY,
                "position": 2 + j,
                "reason": reason,
                "placement": "capable",
                "placement_detail": notes,
                "url": job_url(2000001 + j),
            }
        )
    pool["queue"] = [r for r in pool["queue"] if r["state"] == "running"] + rows
    return doc


def crowd_doc() -> dict:
    """도는 행 둘 + 기다리는 행 200개."""
    doc = base_doc()
    pool = doc["pools"][0]
    base = next(r for r in pool["queue"] if r["id"] == 414)
    rows = [r for r in pool["queue"] if r["state"] == "running"]
    for i in range(200):
        rows.append(
            {
                **copy.deepcopy(base),
                "id": 3000000 + i,
                "position": i + 1,
                "key": LONG_KEY if i % 5 == 0 else ("gate:full" if i % 2 else KO_KEY),
                "ahead_job_id": 3000000 + i - 1 if i else 412,
                "url": job_url(3000000 + i),
            }
        )
    pool["queue"] = rows
    return doc


def pools_doc() -> dict:
    """풀 여섯 — 기본 풀 + 원격 풀 다섯, 풀마다 도는 행 하나와 기다리는 행 둘."""
    doc = base_doc()
    pool0 = doc["pools"][0]
    q = {r["id"]: r for r in pool0["queue"]}
    pools = [pool0]
    for p in range(1, 6):
        name = f"remote-pool-{p}-" + "farm" * 8
        rows = []
        for k, src in enumerate((412, 413, 414)):
            rid = 4000000 + p * 10 + k
            rows.append(
                {
                    **copy.deepcopy(q[src]),
                    "id": rid,
                    "key": LONG_KEY if k == 0 else KO_KEY,
                    "lane": 1 if k == 0 else None,
                    "url": job_url(rid),
                }
            )
        pools.append(
            {
                **{k: v for k, v in pool0.items() if k not in ("queue", "recent", "hosts")},
                "name": name,
                "lanes": 1,
                "queue": rows,
                "recent": [],
                "hosts": [],
            }
        )
    doc["pools"] = pools
    return doc


def empty_doc() -> dict:
    """잡 0개 — 빈 대기열이 프리셋 이름을 늘어놓는다(긴 이름 30개)."""
    doc = base_doc()
    pool = doc["pools"][0]
    pool["queue"] = []
    pool["recent"] = []
    doc["server"]["workers"] = [
        {"lane": 1, "state": "idle", "job_id": None, "error": None, "since": None},
        {"lane": 2, "state": "idle", "job_id": None, "error": None, "since": None},
    ]
    doc["presets"] = [
        {**doc["presets"][0], "name": (LONG_PRESET if i % 2 else KO_KEY) + f"_{i}"}
        for i in range(30)
    ]
    return doc


SCENES: dict[str, dict[str, Any]] = {
    "long-text": {
        "doc": long_text_doc,
        "expanded": [1234568],
        "recent_open": [1234560],
        "rows": 6,
    },
    "placement": {"doc": placement_doc, "expanded": [2000001, 2000002], "rows": 5},
    "crowd": {"doc": crowd_doc, "expanded": [], "rows": 22, "show_all": True},
    "pools": {"doc": pools_doc, "expanded": [], "rows": 5 + 15},
    "empty": {"doc": empty_doc, "expanded": [], "rows": 0},
}


# ── 서버 · 페이지 ─────────────────────────────────────────────────────────────


class Served:
    """한 장면을 내주는 가짜 rcm. `swap()` 으로 다음 조회의 문서를 바꾼다."""

    def __init__(self, name: str, page_now: datetime | None = None):
        """`page_now` 를 주면 페이지의 「지금」이 그 시각이다 — 페이지는 `generated_at` 으로
        시계 차이를 재므로, 문서의 시각을 그만큼 밀면 브라우저 시계를 안 건드리고 시각을 고른다."""
        self.name = name
        self.spec = SCENES[name]
        at = datetime.now(UTC) if page_now is None else page_now.astimezone(UTC)
        self.delta = at.replace(microsecond=0) - FIXTURE_NOW
        self.srv = FakeRcm(static_dir=WEB)
        self.doc = self.spec["doc"]()
        self.swap(self.doc)
        self.srv.route("GET", "/api/whoami", {"name": ME, "admin": False})
        self.srv.route("GET", "/api/machines", {"error": "not found"}, status=404)
        for job_id in self.spec.get("recent_open", []):
            self.srv.route("GET", f"/jobs/{job_id}", failures_doc(job_id))
        self.srv.route("GET", "/jobs/1234567/log", "\n".join(["[log] " + "v" * 600] * 5))

    def swap(self, doc: dict) -> None:
        self.doc = doc
        self.srv.route("GET", "/api/status", shifted(doc, self.delta))

    def url(self, lang: str) -> str:
        return f"{self.srv.url}/?poll=1&lang={lang}"

    def close(self) -> None:
        self.srv.close()


def ready_js(spec: dict, lang: str) -> str:
    rows = spec["rows"]
    want = (
        f"document.querySelectorAll('#queue tr[data-job]:not(.expanded)').length >= {rows}"
        if rows
        else "document.querySelector('#queue .empty') !== null"
    )
    return f"{want} && document.documentElement.lang === {json.dumps(lang)}"


def open_scene(c: Chrome, served: Served, width: int, lang: str) -> None:
    """뷰포트를 맞추고 장면을 연다 — 토큰 · 펼친 행은 그 origin 의 localStorage 로 심는다."""
    c.viewport(width, 900, mobile=width <= PHONE_MAX)
    spec = served.spec
    c.open(served.srv.url + "/static/style.css", ready_js="document.readyState === 'complete'")
    c.eval(
        "localStorage.setItem('rcm.token', 'tok'); "
        f"localStorage.setItem('rcm.expanded', {json.dumps(json.dumps(spec['expanded']))}); true"
    )
    c.open(served.url(lang), ready_js=ready_js(spec, lang))
    if spec.get("show_all"):
        c.eval("document.querySelector('[data-more-queue]').click(); true")
        _wait(c, "document.querySelectorAll('#queue tr[data-job]').length >= 202")
    for job_id in spec.get("recent_open", []):
        c.eval(f"document.querySelector('[data-rtoggle=\"{job_id}\"]').click(); true")
        _wait(c, f"document.querySelector('.rrow[data-job=\"{job_id}\"] .rdetail') !== null")
        _wait(
            c,
            f"(document.querySelector('.rrow[data-job=\"{job_id}\"] .rdetail')"
            " || {textContent: ''}).textContent.indexOf('notification_settings_golden_00') >= 0",
        )
    time.sleep(0.25)


def _wait(c: Chrome, js: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while c.eval(js) is not True:
        if time.monotonic() >= deadline:
            raise AssertionError(f"not true within {timeout}s: {js}")
        time.sleep(0.05)


def shoot(c: Chrome, scene: str, width: int, lang: str, extra: str = "") -> None:
    """`RCM_UI_SHOTS` 가 있으면 페이지 전체(최대 6000px)를 PNG 로 남긴다."""
    folder = os.environ.get("RCM_UI_SHOTS")
    if not folder:
        return
    tag = os.environ.get("RCM_UI_SHOTS_TAG", "after")
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    height = min(6000, int(c.eval("document.documentElement.scrollHeight")))
    shot = c.call(
        "Page.captureScreenshot",
        {
            "format": "png",
            "captureBeyondViewport": True,
            "clip": {"x": 0, "y": 0, "width": width, "height": height, "scale": 1},
        },
        timeout=60.0,
    )
    name = f"{scene}{extra}-{width}-{lang}-{tag}.png"
    (out / name).write_bytes(base64.b64decode(shot["data"]))


# ── 재기 ──────────────────────────────────────────────────────────────────────

# 칸 — 글이 머물러야 하는 상자. 가장 가까운 것이 그 글의 칸이다.
CELLS = (
    "td, th, .rrow > *, .rdetail, .sum .v, .wk, .envchip, .place-list li, .pool-h,"
    " .s-h, .drawer-head > *, .empty, .banner, button.more"
)
ROOTS = ("#hdr", ".sum-yours", ".sum-stuck", "#queue", "#recent", "#drawer:not([hidden])")

SPILL_JS = (
    (
        """
(() => {
  const CELLS = __CELLS__, ROOTS = __ROOTS__;
  const name = (el) => el.tagName.toLowerCase()
    + (el.id ? '#' + el.id : '')
    + (typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\\s+/).join('.') : '')
    + (el.closest('[data-job]') ? '@' + el.closest('[data-job]').getAttribute('data-job') : '');
  const clips = (el) => { const s = getComputedStyle(el); return s.overflowX !== 'visible'; };
  // el(포함) 과 cell(포함) 사이에서 넘침을 자르는 가장 가까운 조상
  const clipper = (el, cell) => {
    for (let p = el; p; p = p.parentElement) {
      if (clips(p)) return p;
      if (p === cell) return null;
    }
    return null;
  };
  // 접힌 <details> 의 본문은 상자를 갖고 있어도 안 보인다
  const folded = (el) => {
    const d = el.closest('details:not([open])');
    return !!d && !el.closest('summary');
  };
  const shown = (el) => {
    if (folded(el)) return false;
    const r = el.getBoundingClientRect(); return r.width > 0 || r.height > 0;
  };
  const out = {spill: [], untitled: [], innerScroll: []};
  const seen = new Set();
  const outside = (r, b) => r.right > b.right + 1 || r.left < b.left - 1 || r.bottom > b.bottom + 1;
  ROOTS.forEach((sel) => document.querySelectorAll(sel).forEach((root) => {
    if (!shown(root)) return;
    const cellOf = (el) => {
      const c = el.closest(CELLS); return c && root.contains(c) ? c : root;
    };
    // (1) 글 조각
    const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      if (!n.textContent.trim()) continue;
      const el = n.parentElement;
      if (!el || !shown(el)) continue;
      const cell = cellOf(el);
      if (clipper(el, cell)) continue;   // 잘린 글은 새지 않는다 — 자르는 상자는 (2) 가 본다
      const b = cell.getBoundingClientRect();
      const range = document.createRange(); range.selectNodeContents(n);
      for (const r of range.getClientRects()) {
        if (r.width === 0) continue;
        if (outside(r, b)) {
          const key = name(el) + '|' + n.textContent.slice(0, 30);
          if (seen.has(key)) break; seen.add(key);
          out.spill.push({el: name(el), cell: name(cell), text: n.textContent.trim().slice(0, 50),
                          right: Math.round(r.right), cellRight: Math.round(b.right),
                          bottom: Math.round(r.bottom), cellBottom: Math.round(b.bottom)});
          break;
        }
      }
    }
    // (2) 상자
    root.querySelectorAll('*').forEach((el) => {
      if (!shown(el) || el.matches(CELLS)) return;
      const cell = cellOf(el);
      const up = el.parentElement;
      if (up && up !== cell && clipper(up, cell)) return;
      const r = el.getBoundingClientRect(), b = cell.getBoundingClientRect();
      if (outside(r, b)) {
        const key = name(el);
        if (seen.has(key)) return; seen.add(key);
        out.spill.push({el: name(el), cell: name(cell), text: el.textContent.trim().slice(0, 50),
                        right: Math.round(r.right), cellRight: Math.round(b.right),
                        bottom: Math.round(r.bottom), cellBottom: Math.round(b.bottom)});
      }
    });
    // (3) 잘렸는데 전문이 없다
    root.querySelectorAll('*').forEach((el) => {
      if (!shown(el)) return;
      const s = getComputedStyle(el);
      const cut = (s.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1)
        || (s.webkitLineClamp !== 'none' && el.scrollHeight > el.clientHeight + 1);
      if (!cut) return;
      const cell = cellOf(el);
      let titled = false;
      for (let p = el; p && p !== cell.parentElement; p = p.parentElement) {
        if (p.getAttribute && p.getAttribute('title')) { titled = true; break; }
      }
      if (!titled) out.untitled.push({el: name(el), text: el.textContent.trim().slice(0, 50)});
    });
    // (4) 표가 자기 상자 안에서 옆으로 스크롤한다
    root.querySelectorAll('.qwrap').forEach((w) => {
      if (w.scrollWidth > w.clientWidth + 1) {
        out.innerScroll.push({el: name(w), scrollWidth: w.scrollWidth, clientWidth: w.clientWidth});
      }
    });
  }));
  out.spill = out.spill.slice(0, 12);
  out.untitled = out.untitled.slice(0, 12);
  return out;
})()
"""
    )
    .replace("__CELLS__", json.dumps(CELLS))
    .replace("__ROOTS__", json.dumps(list(ROOTS)))
)


def measure(c: Chrome) -> dict:
    m = c.eval(SPILL_JS)
    m["page"] = c.eval(OVERFLOW_JS)
    return m


def problems(m: dict) -> list[str]:
    out = []
    page = m["page"]
    if page["scrollWidth"] > page["clientWidth"] + 1 or page["wide"]:
        out.append("페이지가 옆으로 밀린다 — " + describe(page))
    for s in m["innerScroll"]:
        out.append(f"표가 상자 안에서 옆으로 스크롤한다 — {s}")
    for s in m["spill"]:
        out.append(f"글이 칸 밖으로 샌다 — {s}")
    for s in m["untitled"]:
        out.append(f"말줄임했는데 title 이 없다 — {s}")
    return out


@pytest.fixture(params=sorted(SCENES))
def served(request) -> Iterator[Served]:
    s = Served(request.param)
    yield s
    s.close()


def test_queue_screen_holds_edge_inputs(served, tmp_path):
    """장면 하나 × 네 경우 — 옆으로 밀림 · 칸 밖으로 샘 · 전문 없는 말줄임이 하나도 없다."""
    found: dict[tuple[int, str], list[str]] = {}
    with Chrome(tmp_path / "chrome-edges", window="1280,900") as c:
        for width, lang in CASES:
            open_scene(c, served, width, lang)
            shoot(c, served.name, width, lang)
            bad = problems(measure(c))
            # 폰 흉내(mobile)에서는 넘친 페이지가 레이아웃 뷰포트를 넓힌다 — 그것도 밀림이다
            inner = c.eval("window.innerWidth")
            if inner != width:
                bad.insert(0, f"뷰포트가 {width}px → {inner}px 로 넓어졌다(페이지가 넘친다)")
            if served.name == "long-text":
                # 서랍 — 긴 잡 이름 + 40자 링크 라벨이 머리를 밀지 않는다
                c.eval("location.hash = '#/jobs/1234567/log'; true")
                _wait(c, "!document.querySelector('#drawer').hidden")
                time.sleep(0.2)
                shoot(c, served.name, width, lang, extra="-drawer")
                bad += [f"(서랍) {p}" for p in problems(measure(c))]
                c.eval("document.querySelector('[data-drawer-close]').click(); true")
            assert c.page_errors() == []
            if bad:
                found[(width, lang)] = bad
    assert not found, f"{served.name}:\n" + "\n".join(
        f"  {lang} @ {width}px\n    " + "\n    ".join(p) for (width, lang), p in found.items()
    )


# ── 갱신에 출렁이지 않는다 ────────────────────────────────────────────────────

# 큐 행 · 최근 행의 높이와 (표 머리 기준) 위치. 레이아웃 이동은 PerformanceObserver 가 모은다.
ROWS_JS = """
(() => {
  const out = {};
  const table = document.querySelector('#queue table.q');
  const top0 = table ? table.getBoundingClientRect().top : 0;
  document.querySelectorAll('#queue tr[data-job]').forEach((tr) => {
    const r = tr.getBoundingClientRect();
    out['q' + tr.getAttribute('data-job') + (tr.classList.contains('expanded') ? 'x' : '')] =
      {h: Math.round(r.height), top: Math.round(r.top - top0)};
  });
  const rec = document.querySelector('#recent .recent');
  const rtop = rec ? rec.getBoundingClientRect().top : 0;
  document.querySelectorAll('#recent .rrow').forEach((el) => {
    const r = el.getBoundingClientRect();
    out['r' + el.getAttribute('data-job')] =
      {h: Math.round(r.height), top: Math.round(r.top - rtop)};
  });
  return out;
})()
"""

CLS_START_JS = """
(() => {
  window.__shifts = [];
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      if (e.hadRecentInput) continue;
      window.__shifts.push({value: e.value, sources: (e.sources || []).map((s) => {
        const n = s.node;
        const nm = n && n.nodeType === 1 ? n.tagName.toLowerCase()
          + (typeof n.className === 'string' && n.className
            ? '.' + n.className.split(' ').join('.') : '')
          + (n.closest && n.closest('[data-job]')
            ? '@' + n.closest('[data-job]').getAttribute('data-job') : '')
          : String(n && n.nodeName);
        return {node: nm, dy: Math.round(s.currentRect.top - s.previousRect.top),
                dh: Math.round(s.currentRect.height - s.previousRect.height)};
      })});
    }
  }).observe({type: 'layout-shift', buffered: false});
  return true;
})()
"""


def refetch(c: Chrome, until: str) -> None:
    """live 단추를 껐다 켜 곧바로 다시 받게 한다(`test_web_browser.refetch` 와 같은 길)."""
    for _ in range(2):
        c.eval("document.querySelector('#live-btn').click(); true")
    _wait(c, until, timeout=10.0)
    time.sleep(0.3)


def updated_doc(doc: dict) -> dict:
    """같은 장면의 다음 순간 — 단계가 아주 긴 이름으로 바뀌고, 대기 이유가 바뀐다."""
    new = copy.deepcopy(doc)
    q = {r["id"]: r for r in new["pools"][0]["queue"]}
    prog = q[1234567]["progress"]
    prog["steps"][-1].update(state="done", ok=True)
    prog["steps"].append(
        {"index": 6, "name": LONG_STEP_2[:120], "state": "running", "ok": None, "seconds": 1}
    )
    prog.update(current_index=6, steps_done=5, current_name=LONG_STEP_2[:120], current_seconds=1)
    q[1234568]["progress"]["current_name"] = "짧음"
    q[1234568]["progress"]["steps"][-1]["name"] = "짧음"
    # 줄 서서 기다림 → 그룹에 막힘(큰 숫자), 그룹에 막힘 → 줄 서서 기다림
    w = q[1234570]
    w.update(
        reason="blocked_by_group",
        blocked_by={"job_id": 9999999, "group": LONG_GROUP, "remaining_seconds": 359940},
    )
    q[1234569].update(reason="waiting_for_lane", ahead_job_id=9999998, blocked_by=None)
    return new


# 픽스처의 표시 시간대(Asia/Seoul)로 자정 10분 전 — 대기 행의 도착 시각이 다음 날로 넘어가
# 날짜를 단다. 고치기 전에는 그 긴 시각이 도착 칸을 152px 너머로 키웠고, 갱신마다 표가 칸 폭을
# 다시 나눠 행이 움직였다(2026-11-18 23:50 KST 의 CI 세 판이 함께 빨갰다 — 그 시각에만 나는 실패).
BEFORE_MIDNIGHT_KST = datetime(2026, 11, 18, 14, 50, tzinfo=UTC)


@pytest.mark.parametrize(
    "page_now", [None, BEFORE_MIDNIGHT_KST], ids=["now", "before-midnight-kst"]
)
def test_rows_keep_their_height_through_ticks_and_updates(tmp_path, page_now):
    served = Served("long-text", page_now)
    moved: dict[tuple[int, str], list[str]] = {}
    try:
        with Chrome(tmp_path / "chrome-shift", window="1280,900") as c:
            for width, lang in CASES:
                served.swap(long_text_doc())
                open_scene(c, served, width, lang)
                assert c.eval(CLS_START_JS) is True
                before = c.eval(ROWS_JS)
                time.sleep(2.2)  # 1초 틱 둘
                ticked = c.eval(ROWS_JS)
                refetch(c, "true")  # 같은 문서를 다시 받는다(SSE 갱신과 같은 길)
                same = c.eval(ROWS_JS)
                served.swap(updated_doc(served.doc))
                refetch(
                    c,
                    "document.querySelector('#queue tr[data-job=\"1234570\"] .reason.blocked')"
                    " !== null",
                )
                after = c.eval(ROWS_JS)
                shifts = c.eval("window.__shifts")
                shoot(c, "long-text", width, lang, extra="-updated")
                bad = []
                for label, snap in (("틱", ticked), ("재조회", same), ("단계·이유 바뀜", after)):
                    for key, was in before.items():
                        now = snap.get(key)
                        if now is None:
                            bad.append(f"{label}: {key} 행이 사라졌다")
                        elif abs(now["h"] - was["h"]) > 1 or abs(now["top"] - was["top"]) > 1:
                            bad.append(f"{label}: {key} {was} → {now}")
                total = sum(s["value"] for s in shifts)
                if total > 0.001:
                    bad.append(f"layout-shift 합 {total:.4f}: {shifts[:6]}")
                if bad:
                    moved[(width, lang)] = bad
    finally:
        served.close()
    assert not moved, "갱신에 행이 움직였다:\n" + "\n".join(
        f"  {lang} @ {width}px\n    " + "\n    ".join(b) for (width, lang), b in moved.items()
    )


# ── 최근 행: 다른 풀의 칩이 이름 칸을 통째로 말줄임하지 않는다 ─────────────────────

# 진짜 dev 서버(2026-11-17 최종 검증)에서 1240px 최근 행이 `gate …` 로 보였다. 이름은 네 글자인데
# 뒤의 「실행 그룹 mac2」 칩이 칸(150px)에 안 들어가 칸 전체의 말줄임표가 칩을 통째로 지웠다 —
# 이름이 잘린 것처럼 읽히고, 펼치는 행 안이라 눌러 열 수도 없다. 칸은 자기 상자를 넘치지 않고,
# 칩이 제 안에서 줄어야 한다.

RECENT_K_JS = """
(() => {
  const out = [];
  document.querySelectorAll('#recent .rrow').forEach((row) => {
    const k = row.querySelector('.k'), chip = k && k.querySelector('.chip');
    if (!chip) return;
    const kr = k.getBoundingClientRect(), cr = chip.getBoundingClientRect();
    const kn = k.querySelector('.kn');
    out.push({job: row.getAttribute('data-job'), kOver: k.scrollWidth - k.clientWidth,
      chipW: Math.round(cr.width), chipIn: cr.left >= kr.left - 1 && cr.right <= kr.right + 1,
      knCut: kn.scrollWidth > kn.clientWidth + 1, key: kn.getAttribute('title'),
      chipCut: chip.scrollWidth > chip.clientWidth + 1});
  });
  return out;
})()
"""


def recent_chip_doc() -> dict:
    """풀 여섯 장면에 다른 풀의 최근 잡 셋 — 짧은 이름 하나, 긴 이름 + 링크 하나, 그리고 문서
    스크린샷의 그 행(`remote-demo` · 풀 `mac2`). 그 행은 1240px 에서 `remote-d… po…` 였다."""
    doc = pools_doc()
    tmpl = base_doc()["pools"][0]["recent"][1]
    short = {**copy.deepcopy(tmpl), "id": 5000001, "key": "mac2-only", "summary": ""}
    long_ = {
        **copy.deepcopy(tmpl),
        "id": 5000002,
        "key": LONG_KEY,
        "summary": "",
        "link": {"url": LINK_URL, "label": LINK_LABEL},
    }
    doc["pools"][1]["recent"] = [short]
    doc["pools"][2]["recent"] = [long_]
    doc["pools"][3]["name"] = "mac2"
    doc["pools"][3]["recent"] = [
        {**copy.deepcopy(tmpl), "id": 5000003, "key": "remote-demo", "summary": ""}
    ]
    return doc


def test_recent_pool_chip_shrinks_inside_the_name_cell(tmp_path, monkeypatch):
    spec = {"doc": recent_chip_doc, "expanded": [], "rows": 5 + 15}
    monkeypatch.setitem(SCENES, "recent-chip", spec)
    served = Served("recent-chip")
    found: dict[tuple[int, str], list] = {}
    try:
        with Chrome(tmp_path / "chrome-recent-chip", window="1280,900") as c:
            for width, lang in CASES:
                open_scene(c, served, width, lang)
                _wait(c, "document.querySelectorAll('#recent .rrow .k .chip').length >= 3")
                rows = c.eval(RECENT_K_JS)
                bad = [r for r in rows if r["kOver"] > 1 or not r["chipIn"] or r["chipW"] < 24]
                bad += [r for r in rows if r["key"] == "mac2-only" and r["knCut"]]
                # 짧은 이름 + 짧은 풀은 둘 다 온전히 읽힌다 — 한 줄에 안 들어가면 칩이 다음 줄로
                bad += [
                    r for r in rows if r["key"] == "remote-demo" and (r["knCut"] or r["chipCut"])
                ]
                if bad:
                    found[(width, lang)] = bad
    finally:
        served.close()
    assert not found, "최근 행의 이름 칸이 칩 때문에 넘친다:\n" + "\n".join(
        f"  {lang} @ {width}px {rows}" for (width, lang), rows in found.items()
    )
