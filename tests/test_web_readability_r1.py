"""웹 UI 가독성 R1 — Codex 가독성 검토(2026-11-17) 1 · 5 · 6 · 7 · 8번. Chrome 이 없으면 skip.

검토의 결론은 「잘리지 않음」이 아니라 「잘린 정보를 다시 읽을 방법」이었다. 휴대폰에는
hover 가 없고 `<span title>` 은 키보드 포커스도 못 받는다. 그래서 여기서 잠그는 것:

1. **잘린 글은 탭 · 키보드로 연다** — 실제로 잘린 `[data-cut]` 요소는 `tabindex=0` · `role=button` ·
   전문을 담은 `aria-label` 을 얻는다. 누르거나 Enter 면 `#reveal` 이 전문과 복사 단추를 보이고,
   다시 누르거나 Escape 면 닫힌다(Escape 는 포커스를 되돌린다). 여는 일이 행 높이 · 페이지 폭을
   바꾸지 않는다. 행동이 이미 있는 요소(잡 링크 · 펼침 · 버튼) 안에서는 가로채지 않고, 안 잘린
   요소는 탭 순서에 끼지 않는다. `title` 은 그대로 남는다(보조 수단).
5. **워커 띠는 더 있다는 것을 말한다** — 띠는 `tabindex=0` 과 `aria-label` 을 갖고 좌우 화살표로
   민다. 넘치면 오른쪽이 흐려지고(`more-r`) 고정된 `+N` 표지가 화면 밖 필 수를 센다. 화면 밖에
   끊김 · 보류 필이 있으면 표지가 경고색이고, 표지의 title · 펼침이 그 이름을 늘어놓는다.
6. **식별자는 가운데를 줄인다** — 잡 이름 · 머신 이름 · 호스트 라벨 · 워커 필 · 호스트 카드 이름의
   끝(`.mx-t`)이 칸 안에 보인다. 공통 접두사가 긴 이름들이 끝에서 갈린다.
7. **끊긴 워커 띠는 요약이다** — 「워커 2대 연결 끊김 — …」, 이름은 띠 본문에 없고 펼침 뒤에 있다.
8. **대기 200개** — 「모두 보기」 뒤에도 「적게 보기」가 남고, 데스크톱에서는 표 머리가 붙어 있다.

장면은 `test_web_layout_queue_edges`(영역 A) · `test_web_layout_host_edges`(영역 B) 의 것을 그대로
쓴다. `RCM_UI_SHOTS=<폴더>` 면 장면마다 사진을 남긴다(판정과 무관).
"""

from __future__ import annotations

import json
import time

import pytest

import test_web_layout_host_edges as B
import test_web_layout_queue_edges as A
from test_web_browser import Chrome, pytestmark  # noqa: F401  (Chrome 이 없으면 skip)
from test_web_layout import OVERFLOW_JS, describe

PHONE = (390, "ko")
DESK = (1240, "ko")


def wait(c: Chrome, js: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while c.eval(js) is not True:
        if time.monotonic() >= deadline:
            raise AssertionError(f"not true within {timeout}s: {js}")
        time.sleep(0.05)


def no_overflow(c: Chrome, where: str) -> None:
    m = c.eval(OVERFLOW_JS)
    assert m["scrollWidth"] <= m["clientWidth"] + 1 and not m["wide"], f"{where}: {describe(m)}"


# ── 1 · 6: 잘린 잡 이름 — 가운데 말줄임 + 탭해서 전문 ─────────────────────────

KEY_SEL = '#queue tr[data-job="1234567"] td.key [data-cut]'

CUT_JS = """
(() => {
  const el = document.querySelector(%(sel)s);
  if (!el) return {missing: true};
  const td = el.closest('td') || el.parentElement;
  const t = el.querySelector('.mx-t'), h = el.querySelector('.mx-h');
  const tr = t && t.getBoundingClientRect(), cr = td.getBoundingClientRect();
  const er = el.getBoundingClientRect();
  return {
    tabindex: el.getAttribute('tabindex'), role: el.getAttribute('role'),
    label: el.getAttribute('aria-label') || '', title: el.getAttribute('title') || '',
    tail: t ? t.textContent : null,
    tailInside: !!tr && tr.width > 0 && tr.right <= Math.min(cr.right, er.right) + 1
      && tr.left >= cr.left - 1,
    headCut: !!h && h.scrollWidth > h.clientWidth + 1,
    rowH: Math.round(el.closest('tr').getBoundingClientRect().height),
  };
})()
"""

REVEAL_JS = """
(() => {
  const r = document.getElementById('reveal');
  if (!r || r.hidden) return {open: false};
  const b = r.getBoundingClientRect();
  const copy = r.querySelector('[data-copy]');
  return {open: true, text: (r.querySelector('.rv-text') || r).textContent,
          copy: copy ? copy.getAttribute('data-copy') : null,
          left: b.left, right: b.right, vw: document.documentElement.clientWidth,
          focus: document.activeElement ? document.activeElement.getAttribute('data-cut') : null};
})()
"""


def key_event(c: Chrome, sel: str, key: str) -> None:
    c.eval(
        f"(() => {{ const el = {sel}; el.dispatchEvent(new KeyboardEvent('keydown',"
        f" {{key: {json.dumps(key)}, bubbles: true, cancelable: true}})); return true; }})()"
    )


def test_cut_names_open_on_tap_and_keyboard_without_moving_the_row(tmp_path):
    served = A.Served("long-text")
    try:
        with Chrome(tmp_path / "chrome-r1-cut", window="1280,900") as c:
            for width, lang in (PHONE, DESK):
                where = f"{lang}@{width}"
                A.open_scene(c, served, width, lang)
                m = c.eval(CUT_JS % {"sel": json.dumps(KEY_SEL)})
                assert not m.get("missing"), where
                # 6: 가운데 말줄임 — 끝 토막이 칸 안에 보이고 앞 토막이 잘렸다
                assert m["tail"] and A.LONG_KEY.endswith(m["tail"]), f"{where}: {m}"
                assert m["tailInside"] and m["headCut"], f"{where}: 끝이 안 보인다 {m}"
                # 1: 잘린 이름은 탭 순서에 들고 전문을 말한다 — title 은 보조로 남는다
                assert m["tabindex"] == "0" and m["role"] == "button", f"{where}: {m}"
                assert A.LONG_KEY in m["label"] and m["title"] == A.LONG_KEY, f"{where}: {m}"
                row_h = m["rowH"]

                c.eval(f"document.querySelector({json.dumps(KEY_SEL)}).click(); true")
                r = c.eval(REVEAL_JS)
                assert r["open"] and A.LONG_KEY in r["text"], f"{where}: {r}"
                assert r["copy"] == A.LONG_KEY, f"{where}: 복사 단추 {r}"
                assert r["left"] >= -1 and r["right"] <= r["vw"] + 1, f"{where}: 펼침이 화면 밖 {r}"
                no_overflow(c, where + " (펼침)")
                now = c.eval(CUT_JS % {"sel": json.dumps(KEY_SEL)})
                assert abs(now["rowH"] - row_h) <= 1, f"{where}: 펼침이 행을 늘렸다 {row_h}→{now}"
                # 다시 누르면 닫힌다
                c.eval(f"document.querySelector({json.dumps(KEY_SEL)}).click(); true")
                assert c.eval(REVEAL_JS)["open"] is False, where

                # 키보드: 포커스 → Enter 로 열고 Escape 로 닫으면 포커스가 그 이름으로 돌아온다
                c.eval(f"document.querySelector({json.dumps(KEY_SEL)}).focus(); true")
                key_event(c, "document.activeElement", "Enter")
                assert c.eval(REVEAL_JS)["open"] is True, f"{where}: Enter 가 안 연다"
                key_event(c, "document.activeElement", "Escape")
                r = c.eval(REVEAL_JS)
                assert r["open"] is False, f"{where}: Escape 가 안 닫는다"
                assert c.eval("document.activeElement.getAttribute('data-cut')") == "key", where

                # 행동이 있는 것은 가로채지 않는다 — 버튼 · 링크 안에 role=button 이 없고,
                # 펼침 손잡이는 여전히 펼친다
                nested = c.eval(
                    "document.querySelectorAll('button [role=button], a [role=button],"
                    " [role=button] [role=button]').length"
                )
                assert nested == 0, f"{where}: 손잡이 안에 손잡이 {nested}"
                c.eval("document.querySelector('[data-toggle=\"1234567\"]').click(); true")
                wait(c, "!!document.querySelector('#queue tr.expanded[data-job=\"1234567\"]')")
                assert c.eval(REVEAL_JS)["open"] is False, f"{where}: 펼침이 전문을 열었다"
                c.eval("document.querySelector('[data-toggle=\"1234567\"]').click(); true")

                # 다른 곳을 누르면 닫힌다
                c.eval(f"document.querySelector({json.dumps(KEY_SEL)}).click(); true")
                c.eval("document.querySelector('#recent .s-h').click(); true")
                assert c.eval(REVEAL_JS)["open"] is False, f"{where}: 바깥을 눌러도 안 닫힌다"
                assert c.eval("window.innerWidth") == width, where
                assert c.page_errors() == []
    finally:
        served.close()


def test_focus_on_a_cut_name_survives_a_refetch(tmp_path):
    served = A.Served("long-text")
    try:
        with Chrome(tmp_path / "chrome-r1-focus", window="1280,900") as c:
            A.open_scene(c, served, *DESK)
            c.eval(f"document.querySelector({json.dumps(KEY_SEL)}).focus(); true")
            A.refetch(c, "true")
            got = c.eval(
                "(() => { const a = document.activeElement;"
                " return {cut: a && a.getAttribute('data-cut'),"
                " job: a && a.closest('[data-job]')"
                " && a.closest('[data-job]').getAttribute('data-job')}; })()"
            )
            assert got == {"cut": "key", "job": "1234567"}, got
    finally:
        served.close()


def test_names_that_fit_stay_out_of_the_tab_order(tmp_path):
    """crowd 장면의 `gate:full` 은 1240px 에서 다 보인다 — 탭 순서에 끼면 소음이다."""
    served = A.Served("crowd")
    try:
        with Chrome(tmp_path / "chrome-r1-fit", window="1280,900") as c:
            A.open_scene(c, served, *DESK)
            got = c.eval(
                """
                (() => {
                  const out = {fit: 0, fitFocusable: 0, cut: 0, cutFocusable: 0};
                  document.querySelectorAll('#queue td.key [data-cut]').forEach((el) => {
                    const cut = el.scrollWidth > el.clientWidth + 1
                      || [...el.querySelectorAll('*')]
                        .some((k) => k.scrollWidth > k.clientWidth + 1);
                    const f = el.getAttribute('tabindex') === '0';
                    if (cut) { out.cut++; if (f) out.cutFocusable++; }
                    else { out.fit++; if (f) out.fitFocusable++; }
                  });
                  return out;
                })()
                """
            )
            assert got["fit"] > 0 and got["cut"] > 0, got
            assert got["fitFocusable"] == 0, f"다 보이는 이름이 탭 순서에 든다 {got}"
            assert got["cutFocusable"] == got["cut"], f"잘린 이름이 탭 순서에 없다 {got}"
    finally:
        served.close()


# ── 8: 대기 200개 — 적게 보기 · 붙는 표 머리 ─────────────────────────────────────


def test_show_all_can_be_undone_and_the_head_sticks_on_desktop(tmp_path):
    served = A.Served("crowd")
    try:
        with Chrome(tmp_path / "chrome-r1-crowd", window="1280,900") as c:
            for width, lang in (PHONE, DESK, (1240, "en")):
                where = f"{lang}@{width}"
                A.open_scene(c, served, width, lang)  # 장면이 「모두 보기」를 누른다
                n_all = c.eval("document.querySelectorAll('#queue tr[data-job]').length")
                assert n_all >= 202, where
                btn = c.eval(
                    "(() => { const b = document.querySelector('[data-more-queue]');"
                    " return b ? b.textContent.trim() : null; })()"
                )
                want = "적게 보기" if lang == "ko" else "show fewer"
                assert btn and want in btn, f"{where}: 모두 보기 뒤에 되돌릴 단추가 없다 ({btn!r})"
                if width > A.PHONE_MAX:
                    # 표 한가운데로 내려가도 열 머리가 화면 맨 위에 붙어 있다
                    c.eval(
                        "document.querySelector('#queue tr[data-job=\"3000100\"]')"
                        ".scrollIntoView({block: 'center'}); true"
                    )
                    time.sleep(0.15)
                    head = c.eval(
                        """
                        (() => { const th = document.querySelector('#queue table.q thead th');
                          const r = th.getBoundingClientRect();
                          return {pos: getComputedStyle(th).position, top: r.top, h: r.height,
                                  hit: document.elementFromPoint(r.left + 4, r.top + r.height / 2)
                                    === th || th.contains(document.elementFromPoint(
                                      r.left + 4, r.top + r.height / 2))}; })()
                        """
                    )
                    assert head["pos"] == "sticky", f"{where}: {head}"
                    stuck = -1 <= head["top"] <= 2 and head["hit"]
                    assert stuck, f"{where}: 머리가 안 붙는다 {head}"
                    A.shoot(c, "crowd", width, lang, extra="-sticky")
                no_overflow(c, where)
                c.eval("document.querySelector('[data-more-queue]').click(); true")
                wait(c, "document.querySelectorAll('#queue tr[data-job]').length < 40")
                n_few = c.eval("document.querySelectorAll('#queue tr[data-job]').length")
                assert n_few == A.SCENES["crowd"]["rows"], f"{where}: 접은 뒤 {n_few}행"
                more = c.eval("document.querySelector('[data-more-queue]').textContent")
                assert str(n_all - n_few) in more, f"{where}: 「모두 보기」가 아니다 ({more!r})"
                # 접은 뒤 단추는 화면 안에 있다 — 맨 아래에 홀로 남지 않는다
                time.sleep(0.2)  # 접은 뒤 rAF 두 번 — 배치가 정해진 다음에 데려온다
                rect = c.eval(
                    "(() => { const r = document.querySelector('[data-more-queue]')"
                    ".getBoundingClientRect(); return {top: r.top, bottom: r.bottom,"
                    " ih: innerHeight, y: scrollY, h: document.documentElement.scrollHeight}; })()"
                )
                # 1px 여유 — `scrollIntoView` 가 아래끝을 맞출 때 소수점(900.03 > 900)이 남는다
                vis = rect["top"] >= -1 and rect["bottom"] <= rect["ih"] + 1
                assert vis is True, f"{where}: 접은 뒤 단추가 화면 밖이다 {rect}"
    finally:
        served.close()


# ── 5 · 6 · 7: 워커 띠 · 머신 표 · 호스트 카드 · 끊긴 워커 띠 ─────────────────────

BAND_JS = """
(() => {
  const s = document.querySelector('[data-workers]');
  const m = document.querySelector('.wk-more');
  const sr = s.getBoundingClientRect();
  const items = [...s.querySelectorAll('.wk, .envchip')];
  const hidden = items.filter((el) => { const r = el.getBoundingClientRect();
    return r.left < sr.left - 1 || r.right > sr.right + 1; });
  return {
    tabindex: s.getAttribute('tabindex'), label: s.getAttribute('aria-label') || '',
    cls: s.className, scrollLeft: s.scrollLeft,
    overflow: s.scrollWidth > s.clientWidth + 1,
    hidden: hidden.length, hiddenDown: hidden.filter((el) => el.matches('.down, .held')).length,
    marker: m && !m.hidden ? {text: m.textContent.trim(), warn: m.classList.contains('warn'),
      title: m.getAttribute('title') || '', w: Math.round(m.getBoundingClientRect().width),
      inHdr: document.getElementById('hdr').contains(m)} : null,
    hdrH: Math.round(document.getElementById('hdr').getBoundingClientRect().height),
  };
})()
"""


def test_worker_band_says_there_is_more_and_scrolls_by_keyboard(tmp_path):
    scene = B.Scene()
    try:
        with Chrome(tmp_path / "chrome-r1-band", window="1280,900") as c:
            for width, lang in (PHONE, DESK):
                where = f"{lang}@{width}"
                B.open_scene(c, scene, width, lang)
                m = c.eval(BAND_JS)
                assert m["tabindex"] == "0" and m["label"], f"{where}: 띠에 포커스가 없다 {m}"
                assert m["overflow"], f"{where}: 장면이 넘치지 않는다 — 시험이 무의미하다 {m}"
                assert "more-r" in m["cls"], f"{where}: 오른쪽이 흐려지지 않는다 {m}"
                assert m["marker"] and m["marker"]["inHdr"], f"{where}: +N 표지가 없다 {m}"
                assert m["marker"]["text"] == f"+{m['hidden']}", f"{where}: {m}"
                # 끊김 · 보류 필이 화면 밖이면 표지가 경고색이고 그 이름을 늘어놓는다
                assert m["hiddenDown"] > 0, f"{where}: 장면에 숨은 끊김 필이 없다 {m}"
                assert m["marker"]["warn"], f"{where}: {m}"
                assert "mbp" in m["marker"]["title"] or "mac2-worker" in m["marker"]["title"], m
                hdr_h = m["hdrH"]
                # 좌우 화살표
                c.eval("document.querySelector('[data-workers]').focus(); true")
                key_event(c, "document.querySelector('[data-workers]')", "ArrowRight")
                wait(c, "document.querySelector('[data-workers]').scrollLeft > 0")
                time.sleep(0.2)
                after = c.eval(BAND_JS)
                assert after["marker"]["text"] == f"+{after['hidden']}", f"{where}: {after}"
                assert "more-l" in after["cls"], f"{where}: 왼쪽에 숨은 것이 안 보인다 {after}"
                assert after["hdrH"] == hdr_h, f"{where}: 밀자 머리줄이 {hdr_h}→{after['hdrH']}"
                assert after["marker"]["w"] == m["marker"]["w"], f"{where}: 표지 폭이 바뀐다"
                key_event(c, "document.querySelector('[data-workers]')", "ArrowLeft")
                wait(c, "document.querySelector('[data-workers]').scrollLeft === 0")
                # 표지를 누르면 숨은 필 목록이 펼쳐진다
                c.eval("document.querySelector('.wk-more').click(); true")
                r = c.eval(REVEAL_JS)
                assert r["open"] and ("mac2-worker" in r["text"] or "mbp" in r["text"]), r
                assert r["right"] <= r["vw"] + 1, r
                key_event(c, "document.activeElement || document.body", "Escape")
                B_shoot(c, "band", width, lang)
                no_overflow(c, where)
    finally:
        scene.srv.close()


def B_shoot(c: Chrome, scene: str, width: int, lang: str) -> None:
    c.eval("window.scrollTo(0, 0); true")
    A.shoot(c, "r1-" + scene, width, lang)


MID_JS = """
(() => {
  const tail = (el) => { if (!el) return null;
    const t = el.querySelector('.mx-t'); if (!t) return {noSplit: true, text: el.textContent};
    const box = el.getBoundingClientRect(), r = t.getBoundingClientRect();
    const titled = el.closest('[title]');
    return {tail: t.textContent, full: titled ? titled.getAttribute('title') : null,
            inside: r.width > 0 && r.right <= box.right + 1 && r.left >= box.left - 1,
            cutAttr: el.closest('[data-cut]') !== null}; };
  return {
    mname: tail(document.querySelector('#machines tr[data-machine="@local"] td.mname .mn')),
    mhost: tail(document.querySelector('#machines tr[data-machine="@local"] td.mhost [data-cut]')),
    hname: tail(document.querySelector('#host .hostcard .hn .hname')),
    // 잡이 있는 필은 버튼이다(누르면 그 잡으로 간다) — 전문 손잡이는 버튼이 아닌 필에만
    pill: tail([...document.querySelectorAll('#hdr span.wk .wn')]
      .find((e) => e.textContent.length > 40)),
  };
})()
"""


def test_identifiers_keep_their_tail_and_open_in_full(tmp_path):
    scene = B.Scene()
    try:
        with Chrome(tmp_path / "chrome-r1-mid", window="1280,900") as c:
            for width, lang in (PHONE, DESK):
                where = f"{lang}@{width}"
                B.open_scene(c, scene, width, lang)
                m = c.eval(MID_JS)
                for k, v in m.items():
                    assert v and not v.get("noSplit"), f"{where}: {k} 가 가운데 말줄임이 아니다 {v}"
                    assert v["full"] and v["full"].find(v["tail"]) >= 0, f"{where}: {k} {v}"
                    assert v["inside"], f"{where}: {k} 의 끝이 칸 밖이다 {v}"
                    assert v["cutAttr"], f"{where}: {k} 는 전문을 열 수 없다 {v}"
                # 머신 호스트 칸을 누르면 전문이 열린다
                sel = '#machines tr[data-machine="@local"] td.mhost [data-cut]'
                c.eval(f"document.querySelector({json.dumps(sel)}).click(); true")
                r = c.eval(REVEAL_JS)
                assert r["open"] and B.LONG_HOST in r["text"], f"{where}: {r}"
                key_event(c, "document.activeElement || document.body", "Escape")
                # 다시 점검 같은 버튼은 표지가 없다 — 버튼 안에 전문 손잡이가 없다
                nested = c.eval("document.querySelectorAll('button [data-cut][role]').length")
                assert nested == 0, where
                B_shoot(c, "machines", width, lang)
    finally:
        scene.srv.close()


# 진짜 dev 서버(2026-11-17 최종 검증)의 1240px 큐에서 `androi… name-that-go…` — 앞 토막은 59px 로
# 아직 줄 여지가 있는데 뒤 토막이 0.1px 모자라 끝에 말줄임표를 달았다. 줄임 몫 1000:1 은 뒤 토막에도
# 언제나 몇 분의 1px 를 떠넘긴다. 앞이 최소 폭(2ch)보다 넓은 동안 뒤 토막은 제 폭을 다 가져야 한다.
TAIL_WHOLE_JS = """
(() => {
  const bad = [];
  let n = 0;
  document.querySelectorAll('.mx').forEach((m) => {
    const h = m.querySelector('.mx-h'), t = m.querySelector('.mx-t');
    if (!h || !t || !m.getClientRects().length) return;
    const hw = h.getBoundingClientRect().width, tw = t.getBoundingClientRect().width;
    // 제자리에서 뒤 토막만 줄지 않게 하고 잰 폭이 그 글의 온 폭이다(같은 글꼴 · 같은 맥락)
    const keep = t.getAttribute('style');
    t.style.flexShrink = '0'; t.style.maxWidth = 'none';
    const natural = t.getBoundingClientRect().width;
    if (keep === null) t.removeAttribute('style'); else t.setAttribute('style', keep);
    const hMin = parseFloat(getComputedStyle(h).minWidth) || 0;
    n++;
    if (hw > hMin + 1 && tw < natural - 0.01)
      bad.push({tail: t.textContent, tw: tw, natural: natural, hw: hw,
                where: (m.parentElement.className || m.parentElement.tagName)});
  });
  return {n: n, bad: bad};
})()
"""


# 같은 병의 일반형 — 한 줄 flex 안에서 줄임 몫으로 나눠 가지면 「다 들어가야 할」 글이 1px 도 안 되게
# 모자라 말줄임표를 단다. 진짜 dev 서버의 머신 표에서 `macmini` 가 `macmi…` 였다(48.38px / 48.47px —
# 옆의 긴 머신 id 가 줄임 몫 100:1 로 거의 다 가져갔지만 이름에도 0.1px 가 남았다). 1px 도 안 되게
# 잘린 말줄임은 어디서든 버그다 — 그 몇 분의 1px 를 주면 글 전체가 보인다.
SLIVER_JS = """
(() => {
  const bad = [];
  let n = 0;
  document.querySelectorAll('body *').forEach((el) => {
    const cs = getComputedStyle(el);
    if (cs.textOverflow !== 'ellipsis' || !el.getClientRects().length) return;
    const up = el.parentElement;
    if (!up || !/flex/.test(getComputedStyle(up).display)) return;
    n++;
    const w = el.getBoundingClientRect().width;
    const keep = el.getAttribute('style');
    el.style.flexShrink = '0'; el.style.maxWidth = 'none'; el.style.minWidth = 'max-content';
    const natural = el.getBoundingClientRect().width;
    if (keep === null) el.removeAttribute('style'); else el.setAttribute('style', keep);
    if (w < natural - 0.001 && w > natural - 1)
      bad.push({text: el.textContent.slice(0, 40), w: w, natural: natural,
                cls: el.className, up: up.className});
  });
  return {n: n, bad: bad};
})()
"""


def _scenes(c: Chrome):
    """큐 장면 셋 + 호스트 장면을 네 크기(390 · 1240 × ko · en)로 열며 이름을 내준다."""
    for scene_name in ("long-text", "crowd", "placement"):
        served = A.Served(scene_name)
        try:
            for width, lang in A.CASES:
                A.open_scene(c, served, width, lang)
                yield (scene_name, width, lang)
        finally:
            served.close()
    scene = B.Scene()
    try:
        for width, lang in A.CASES:
            B.open_scene(c, scene, width, lang)
            yield ("hosts", width, lang)
    finally:
        scene.srv.close()


def test_the_tail_stays_whole_while_the_head_can_still_give(tmp_path):
    found = {}
    with Chrome(tmp_path / "chrome-r1-tail", window="1280,900") as c:
        for where in _scenes(c):
            got = c.eval(TAIL_WHOLE_JS)
            if where[0] == "hosts":
                assert got["n"] > 0
            if got["bad"]:
                found[where] = got["bad"][:4]
    assert not found, "앞 토막이 줄 수 있는데 뒤 토막이 잘렸다:\n" + "\n".join(
        f"  {k}: {v}" for k, v in found.items()
    )


def test_no_text_is_cut_by_less_than_a_pixel(tmp_path):
    found = {}
    with Chrome(tmp_path / "chrome-r1-sliver", window="1280,900") as c:
        for where in _scenes(c):
            c.eval("document.getElementById('machines') && (document.getElementById('machines')"
                   ".open = true); true")
            time.sleep(0.2)
            got = c.eval(SLIVER_JS)
            assert got["n"] > 0, where
            if got["bad"]:
                found[where] = got["bad"][:4]
    assert not found, "1px 도 안 되게 잘려 말줄임표를 단 글:\n" + "\n".join(
        f"  {k}: {v}" for k, v in found.items()
    )


# 머리줄은 내용 폭만큼만 넓다 — 앞 토막의 최대 폭(칸 − 3em)이 이름 몫 3em 을 비워 두는데, 이름이
# 3em 보다 짧으면(`build`) 그 몫이 모자라 앞 토막이 `step 2/…` 로 잘렸다(문서 스크린샷 재생성 때 발견).
HEAD_A_JS = """
(() => {
  const a = document.querySelector('#queue tr.expanded .prog .head .ph-a');
  const n = document.querySelector('#queue tr.expanded .prog .head .ph-name');
  if (!a) return {missing: true};
  return {text: a.textContent, name: n && n.textContent, cut: a.scrollWidth > a.clientWidth + 0};
})()
"""


def test_a_short_step_name_leaves_the_step_counter_whole(tmp_path):
    served = A.Served("long-text")
    try:
        with Chrome(tmp_path / "chrome-r1-short-step", window="1280,900") as c:
            for width, lang in A.CASES:
                served.swap(A.long_text_doc())
                A.open_scene(c, served, width, lang)
                served.swap(A.updated_doc(served.doc))  # 1234568 의 단계 이름이 「짧음」이 된다
                A.refetch(c, "document.querySelector('#queue tr.expanded .ph-name') !== null"
                          " && document.querySelector('#queue tr.expanded .ph-name')"
                          ".textContent === '짧음'")
                m = c.eval(HEAD_A_JS)
                assert not m.get("missing") and not m["cut"], f"{lang}@{width}: {m}"
    finally:
        served.close()


BANNER_JS = """
(() => {
  const n = document.getElementById('banner-note');
  const b = n.querySelector('[data-reveal]');
  return {hidden: n.hidden, text: n.textContent, h: Math.round(n.getBoundingClientRect().height),
          btn: b ? b.getAttribute('data-reveal') : null, cls: n.className};
})()
"""


def test_worker_unreachable_banner_is_a_short_summary(tmp_path):
    scene = B.Scene()
    try:
        with Chrome(tmp_path / "chrome-r1-banner", window="1280,900") as c:
            for width, lang in (PHONE, DESK, (390, "en")):
                where = f"{lang}@{width}"
                B.open_scene(c, scene, width, lang)
                m = c.eval(BANNER_JS)
                assert not m["hidden"] and "warn" in m["cls"], f"{where}: {m}"
                assert B.LONG_NAME not in m["text"], f"{where}: 띠 본문에 긴 이름이 있다 {m}"
                assert "2" in m["text"], f"{where}: 몇 대인지 말하지 않는다 {m}"
                if lang == "ko":
                    assert m["text"].startswith("워커 2대 연결 끊김"), m
                # 이름 둘이 다 실리던 때는 390px 에서 다섯 줄이었다 — 이제 글 길이가 이름과 무관하다
                assert m["h"] <= 80, f"{where}: 띠가 {m['h']}px 다"
                assert m["btn"] and B.LONG_NAME in m["btn"] and "mac2-worker" in m["btn"], m
                c.eval("document.querySelector('#banner-note [data-reveal]').click(); true")
                r = c.eval(REVEAL_JS)
                assert r["open"] and B.LONG_NAME in r["text"], f"{where}: {r}"
                assert r["right"] <= r["vw"] + 1, r
                no_overflow(c, where)
                B_shoot(c, "banner", width, lang)
                key_event(c, "document.activeElement || document.body", "Escape")
    finally:
        scene.srv.close()


@pytest.mark.parametrize("scene_name", ["long-text", "placement"])
def test_queue_scenes_shoot(scene_name, tmp_path):
    """사진만 — `RCM_UI_SHOTS` 가 없으면 장면이 열리는지만 본다."""
    served = A.Served(scene_name)
    try:
        with Chrome(tmp_path / f"chrome-r1-shot-{scene_name}", window="1280,900") as c:
            for width, lang in (PHONE, DESK):
                A.open_scene(c, served, width, lang)
                A.shoot(c, "r1-" + scene_name, width, lang)
                assert c.page_errors() == []
    finally:
        served.close()
