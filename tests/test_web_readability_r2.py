"""가독성 R2 — Codex 가독성 검토(2026-11-17) 2 · 3 · 4 · 9번을 실제 화면으로 잠근다. Chrome 이
없으면 skip(`test_web_browser` 의 규칙 그대로).

장면은 `test_web_layout_queue_edges`(배치 메모 30개 · 긴 글) 와 `test_web_layout_host_edges`
(머신 50대 · 긴 점검)의 것을 그대로 빌린다.

1. **2번** 펼친 배치 블록은 판정별 집계 한 줄 + 머신 다섯 + 「나머지 N대 보기」다. 누르면 전부,
   다시 열어도(새로고침) 그대로. 접힌 행의 높이는 어느 쪽에서도 그대로다.
2. **3번** 펼친 최근 실패 상세는 이름 여덟(한 줄씩 · 말줄임 · title) + 「+N」, 서버 요약은
   네 줄에서 자르고 「전체 보기」 · 「로그 열기」가 있다. 접힌 행에서 다 보이는 짧은 요약은
   상세에 다시 쓰지 않는다.
3. **4번** 폰(≤720px)의 큐 카드는 상태 · 실행한 사람 · 완료 예상에만 작은 라벨이 붙는다. 모든 칸이
   번역된 `data-label` 을 갖는다. 데스크톱에는 라벨이 없다. 실행한 사람 줄은 값이 길어도 한 줄이다.
4. **9번** 머신 표의 점검 칸 첫 줄은 `실패 · <점검> · #<잡>` 이고 잡 번호는 그 잡의 로그를 여는
   버튼이다. 진단은 그 아래 두 줄에서 자른다(칸 전체는 오늘처럼 세 줄 자리).
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

import test_web_layout_host_edges as host_edges
import test_web_layout_queue_edges as queue_edges
from fake_rcm_http import FakeRcm
from test_web_browser import Chrome, pytestmark  # noqa: F401  (Chrome 이 없으면 skip)
from test_web_layout_m5n import shifted

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "remote_ci_monitor" / "web"


def _wait(c: Chrome, js: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while c.eval(js) is not True:
        if time.monotonic() >= deadline:
            raise AssertionError(f"not true within {timeout}s: {js}")
        time.sleep(0.05)


# ── 2번 — 배치 판정 ───────────────────────────────────────────────────────────

PLACE_JS = """
(() => {
  const out = {};
  for (const id of ['2000001', '2000002']) {
    const main = document.querySelector('#queue tr[data-job="' + id + '"]:not(.expanded)');
    const exp = document.querySelector('#queue tr.expanded.place[data-job="' + id + '"]');
    if (!exp) { out[id] = null; continue; }
    const sum = exp.querySelector('.place-sum');
    const btn = exp.querySelector('[data-place-more]');
    const lis = [...exp.querySelectorAll('.place-list li')];
    out[id] = {
      main: Math.round(main.getBoundingClientRect().height),
      block: Math.round(exp.getBoundingClientRect().height),
      sum: sum ? sum.textContent : null,
      btn: btn ? btn.textContent.trim() : null,
      expanded: btn ? btn.getAttribute('aria-expanded') : null,
      lis: lis.length,
      lineH: lis.length ? parseFloat(getComputedStyle(lis[0]).lineHeight) : 0,
      tall: lis.filter((li) => li.getBoundingClientRect().height
        > parseFloat(getComputedStyle(li).lineHeight) * 1.5).length,
      untitled: lis.filter((li) => !li.getAttribute('title')).length,
    };
  }
  return out;
})()
"""


@pytest.mark.parametrize("width", [390, 1240])
def test_placement_detail_groups_first_and_shows_five(tmp_path, width):
    served = queue_edges.Served("placement")
    try:
        with Chrome(tmp_path / "chrome-place", window="1280,900") as c:
            queue_edges.open_scene(c, served, width, "ko")
            c.eval("localStorage.removeItem('rcm.place_all'); true")
            first = c.eval(PLACE_JS)
            for jid in ("2000001", "2000002"):
                m = first[jid]
                assert m is not None, first
                # 30 대: 판정 다섯 갈래 + 꺼짐 3 + 가능 1
                assert m["sum"] and "꺼짐 3대" in m["sum"] and m["sum"].endswith("가능 1대"), m
                assert m["lis"] == 5, m
                assert m["btn"] and m["btn"].startswith("나머지 26대 보기"), m
                assert m["expanded"] == "false", m
                assert m["tall"] == 0 and m["untitled"] == 0, m
                # 집계 + 다섯 줄 + 토글 — 30줄이 큐를 삼키지 않는다
                assert m["block"] <= m["lineH"] * 12 + 40, m
            c.eval("document.querySelector('[data-place-more=\"2000001\"]').click(); true")
            _wait(
                c,
                'document.querySelectorAll(\'#queue tr.expanded.place[data-job="2000001"]'
                " .place-list li').length === 31",
            )
            opened = c.eval(PLACE_JS)
            assert opened["2000001"]["btn"].startswith("접기"), opened
            assert opened["2000001"]["expanded"] == "true", opened
            assert opened["2000002"]["lis"] == 5, "다른 행은 접힌 채다"
            # 접힌 행(본문 줄)은 펼친 블록이 늘어도 그대로다
            assert opened["2000001"]["main"] == first["2000001"]["main"], (first, opened)
            # 다시 열어도 기억한다(펼침처럼 그 브라우저에)
            c.open(served.url("ko"), ready_js=queue_edges.ready_js(served.spec, "ko"))
            again = c.eval(PLACE_JS)
            assert again["2000001"]["lis"] == 31, again
            assert again["2000001"]["main"] == first["2000001"]["main"]
            c.eval("document.querySelector('[data-place-more=\"2000001\"]').click(); true")
            _wait(
                c,
                'document.querySelectorAll(\'#queue tr.expanded.place[data-job="2000001"]'
                " .place-list li').length === 5",
            )
            assert c.page_errors() == []
    finally:
        served.close()


# ── 3번 — 최근 실패 상세 ──────────────────────────────────────────────────────

RDETAIL_JS = """
((id) => {
  const row = document.querySelector('.rrow[data-job="' + id + '"]');
  const d = row && row.querySelector('.rdetail');
  if (!d) return null;
  const vis = (el) => !!el && el.getClientRects().length > 0
    && getComputedStyle(el).display !== 'none';
  const lis = [...d.querySelectorAll('.rd-fail li')];
  const sum = d.querySelector('.rd-sumt');
  const full = d.querySelector('[data-rfull]');
  const lh = (el) => parseFloat(getComputedStyle(el).lineHeight);
  return {
    text: d.innerText,
    lis: lis.length,
    tall: lis.filter((li) => li.getBoundingClientRect().height > lh(li) * 1.5).length,
    untitled: lis.filter((li) => !li.getAttribute('title')).length,
    more: (d.querySelector('.rd-more') || {textContent: null}).textContent,
    step: d.querySelector('.rd-step') ? d.querySelector('.rd-step').getBoundingClientRect().height
      / lh(d.querySelector('.rd-step')) : null,
    sum: vis(sum) ? {h: sum.getBoundingClientRect().height, lh: lh(sum),
                     title: sum.getAttribute('title')} : null,
    full: vis(full) ? full.textContent.trim() : null,
    log: !!d.querySelector('[data-log="' + id + '"]'),
    height: d.getBoundingClientRect().height,
  };
})
"""


@pytest.mark.parametrize("width", [390, 1240])
def test_recent_failure_detail_is_structured_and_bounded(tmp_path, width):
    served = queue_edges.Served("long-text")
    try:
        with Chrome(tmp_path / "chrome-recent", window="1280,900") as c:
            queue_edges.open_scene(c, served, width, "ko")
            m = c.eval(RDETAIL_JS + "(1234560)")
            assert m is not None
            assert m["lis"] == 8 and m["tall"] == 0 and m["untitled"] == 0, m
            assert m["more"] and m["more"].startswith("+56개 더"), m
            assert m["step"] is not None and m["step"] <= 1.5, m
            # 300자 요약은 접힌 행에서 잘렸다 — 상세가 그 전문의 자리다. 네 줄까지, 넘치면
            # 「전체 보기」(1240px 에서는 두 줄이면 다 들어가 단추가 없다)
            assert m["sum"] is not None, m
            assert m["sum"]["h"] <= m["sum"]["lh"] * 4 + 1, m
            assert m["sum"]["title"] == queue_edges.SUMMARY_300, m
            assert m["log"], m
            if width > 720:
                assert m["full"] is None, m
            else:
                assert m["full"] == "전체 보기", m
                c.eval(
                    "document.querySelector('.rrow[data-job=\"1234560\"] [data-rfull]')"
                    ".click(); true"
                )
                _wait(
                    c,
                    "document.querySelector('.rrow[data-job=\"1234560\"] [data-rfull]')"
                    ".textContent.trim() === '접기'",
                )
                opened = c.eval(RDETAIL_JS + "(1234560)")
                assert opened is not None and opened["sum"]["h"] > m["sum"]["h"], (m, opened)
                # 상세 안의 단추는 행을 접지 않는다
                assert opened["lis"] == 8
            # 로그 열기 → 서랍
            c.eval(
                "document.querySelector('.rrow[data-job=\"1234560\"] .rdetail [data-log]')"
                ".click(); true"
            )
            _wait(c, "!document.querySelector('#drawer').hidden")
            assert c.page_errors() == []
    finally:
        served.close()


def test_short_summary_is_not_repeated_in_the_detail(tmp_path):
    """접힌 행에서 다 읽히는 요약(`2 tests failed`)은 상세에 다시 쓰지 않는다."""
    doc = queue_edges.base_doc()
    srv = FakeRcm(static_dir=WEB)
    from datetime import UTC, datetime

    delta = datetime.now(UTC).replace(microsecond=0) - queue_edges.FIXTURE_NOW
    srv.route("GET", "/api/status", shifted(doc, delta))
    srv.route("GET", "/api/whoami", {"name": "alice-laptop", "admin": False})
    srv.route("GET", "/api/machines", {"error": "not found"}, status=404)
    srv.route("GET", "/jobs/411", {"id": 411, "failures": [], "failures_truncated": False})
    try:
        with Chrome(tmp_path / "chrome-short", window="1280,900") as c:
            for width in (390, 1240):
                c.viewport(width, 900, mobile=width <= 720)
                c.open(srv.url + "/static/style.css", ready_js="document.readyState === 'complete'")
                c.eval("localStorage.setItem('rcm.token', 'tok'); true")
                c.open(
                    srv.url + "/?poll=1&lang=ko",
                    ready_js="document.querySelector('.rrow[data-job=\"411\"]') !== null",
                )
                c.eval("document.querySelector('[data-rtoggle=\"411\"]').click(); true")
                _wait(c, "document.querySelector('.rrow[data-job=\"411\"] .rdetail') !== null")
                m = c.eval(RDETAIL_JS + "(411)")
                assert m["sum"] is None, (width, m)
                assert m["full"] is None, (width, m)
                assert m["text"].count("2 tests failed") == 0, (width, m["text"])
                assert m["log"], m
    finally:
        srv.close()


# ── 4번 — 폰 큐 카드의 라벨 ───────────────────────────────────────────────────

LABELS_JS = """
(() => {
  const before = (el) => getComputedStyle(el, '::before').content;
  const out = {rows: []};
  document.querySelectorAll('#queue table.q tr[data-job]:not(.expanded)').forEach((tr) => {
    const tds = [...tr.children];
    const req = tr.querySelector('td.requester');
    const lh = parseFloat(getComputedStyle(req).lineHeight) || 16;
    out.rows.push({
      id: tr.getAttribute('data-job'),
      labels: tds.map((td) => td.getAttribute('data-label')),
      shown: tds.map((td) => {
        const own = before(td);
        const inner = td.querySelector(':scope > .reason');
        const c = own && own !== 'none' && own !== 'normal'
          ? own : (inner ? before(inner) : 'none');
        return c && c !== 'none' && c !== 'normal' ? c.replace(/^"|"$/g, '').trim() : null;
      }),
      reqH: req.getBoundingClientRect().height / lh,
    });
  });
  return out;
})()
"""


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_phone_queue_cards_label_three_fields(tmp_path, lang):
    served = queue_edges.Served("long-text")
    want = {
        "ko": ["작업", "작업 이름", "실행한 사람", "상태", "진행 시간", "완료 예상", "코드 출처"],
        "en": ["Job", "Job name", "Requested by", "Status", "Elapsed", "Finishes", "Source"],
    }[lang]
    try:
        with Chrome(tmp_path / "chrome-labels", window="1280,900") as c:
            queue_edges.open_scene(c, served, 390, lang)
            phone = c.eval(LABELS_JS)
            queue_edges.open_scene(c, served, 1240, lang)
            desk = c.eval(LABELS_JS)
    finally:
        served.close()
    assert phone["rows"], phone
    for r in phone["rows"]:
        assert r["labels"] == want, r
        assert r["shown"] == [None, None, want[2], want[3], None, want[5], None], r
        # 실행한 사람 줄은 라벨이 붙어도 한 줄 — 값 길이로 카드 높이가 바뀌지 않는다
        assert r["reqH"] <= 1.6, r
    for r in desk["rows"]:
        assert r["labels"] == want, r
        assert r["shown"] == [None] * 7, r


def test_phone_card_height_does_not_follow_the_requester_length(tmp_path):
    """같은 행의 요청자 라벨이 짧은 이름 ↔ 긴 이름으로 바뀌어도 폰 카드 높이가 같다."""
    served = queue_edges.Served("long-text")
    try:
        with Chrome(tmp_path / "chrome-card", window="1280,900") as c:
            queue_edges.open_scene(c, served, 390, "ko")
            js = (
                "Math.round(document.querySelector('#queue tr[data-job=\"1234569\"]')"
                ".getBoundingClientRect().height)"
            )
            before = c.eval(js)
            doc = copy.deepcopy(served.doc)
            for r in doc["pools"][0]["queue"]:
                if r["id"] == 1234569:
                    # 이름은 그대로(내 잡이라 취소 단추가 남는다) — 라벨 길이만 바꾼다
                    r["requester"] = {**r["requester"], "label": "z"}
            served.swap(doc)
            queue_edges.refetch(
                c,
                "document.querySelector('#queue tr[data-job=\"1234569\"] td.requester .rq')"
                ".textContent === 'z'",
            )
            assert c.eval(js) == before
    finally:
        served.close()


# ── 9번 — 머신 표의 점검 칸 ───────────────────────────────────────────────────

MCHK_JS = """
(() => {
  const td = document.querySelector('#machines tr[data-machine="@local"] td.mchk');
  const head = td.querySelector('.mck-h');
  const rest = td.querySelector('.clamp');
  const btn = head && head.querySelector('button[data-log]');
  const lh = parseFloat(getComputedStyle(td).lineHeight);
  return {
    head: head ? head.textContent : null,
    headTitle: head ? head.getAttribute('title') : null,
    headLines: head ? head.getBoundingClientRect().height / lh : null,
    btn: btn ? btn.getAttribute('data-log') : null,
    btnText: btn ? btn.textContent : null,
    btnVisible: btn ? btn.getBoundingClientRect().width > 0
      && btn.getBoundingClientRect().right <= td.getBoundingClientRect().right + 1 : false,
    restLines: rest ? rest.getBoundingClientRect().height / lh : null,
    restTitle: rest ? rest.getAttribute('title') : null,
    cellLines: (td.clientHeight - parseFloat(getComputedStyle(td).paddingTop)
      - parseFloat(getComputedStyle(td).paddingBottom)) / lh,
  };
})()
"""


@pytest.mark.parametrize("width", [390, 1240])
def test_machine_check_cell_leads_with_the_verdict_and_the_job(tmp_path, width):
    scene = host_edges.Scene()
    try:
        with Chrome(tmp_path / "chrome-mchk", window="1280,900") as c:
            host_edges.open_scene(c, scene, width, "ko")
            m = c.eval(MCHK_JS)
            preset = host_edges.LONG_PRESET + "-check-0"
            assert m["head"] == f"실패 · {preset} · #3600", m
            assert m["headTitle"] == m["head"], m
            assert m["headLines"] <= 1.5, m
            assert m["btn"] == "3600" and m["btnText"] == "#3600" and m["btnVisible"], m
            # 진단은 두 줄에서 자른다 — 칸은 오늘처럼 세 줄 자리
            assert m["restLines"] is not None and m["restLines"] <= 2.05, m
            assert m["restTitle"] and preset in m["restTitle"], m
            if width > host_edges.PHONE_MAX:
                assert m["cellLines"] <= 3.3, m
            c.eval(
                'document.querySelector(\'#machines tr[data-machine="@local"] td.mchk'
                " button[data-log]').click(); true"
            )
            _wait(
                c,
                "!document.querySelector('#drawer').hidden && location.hash === '#/jobs/3600/log'",
            )
            assert c.page_errors() == []
    finally:
        scene.srv.close()


def test_scene_payloads_still_load():
    """빌려 쓰는 장면이 이 파일의 가정(배치 31대 · 실패 이름 64개)을 지킨다."""
    doc = queue_edges.placement_doc()
    rows = [r for r in doc["pools"][0]["queue"] if r["id"] == 2000001]
    assert len(rows[0]["placement_detail"]) == 31
    assert len(queue_edges.failures_doc(1)["failures"]) == 64
    rich = host_edges.machine(0, queue_edges.FIXTURE_NOW, rich=True)
    assert "3600" in json.dumps(rich["checks"][0])
