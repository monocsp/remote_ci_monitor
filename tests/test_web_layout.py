"""웹 UI(M5d-3) — 폭·움직임·상태 표기를 진짜 브라우저에서 잠근다. Chrome 이 없으면 전부 skip.

여기서 잠그는 계약 셋(docs/m5d-workplan.md §4.2 · §4.7):

1. **페이지가 옆으로 밀리지 않는다.** 390px(폰)·1240px(데스크톱) × 한국어·영어 네 경우에서
   `documentElement.scrollWidth <= clientWidth + 1`. §4.7 이 이 네 칸을 손으로 재고 「잠그는 시험이
   없다」고 적어 둔 자리다. 실패하면 **무엇이** 넘쳤는지(태그·id·class·`right`) 메시지에 담는다 —
   `app.js` 의 `debugLayout`(`?debug=1`)과 같은 계산이다. `.qwrap` 처럼 자기 안에서 가로 스크롤하는
   칸의 자식은 세지 않는다. 페이지 본문이 밀리지 않는 게 계약이지, 표가 좁아야 하는 게 아니다.
2. **움직임은 끌 수 있다.** `prefers-reduced-motion: reduce` 면 도는 애니메이션이 하나도 없다.
   반대로 `no-preference` 면 도는 잡의 행에 움직이는 요소가 하나 이상 있다 — 움직임도 상태
   채널이다(§4.2, Vercel: 종료 아닌 상태만 움직인다).
3. **상태는 색만으로 구분되지 않는다**(WCAG 1.4.1). 상태 필마다 `aria-hidden="true"` 인 글리프와,
   비어 있지 않은 글자가 **둘 다** 있다.

헤드리스 크롬에는 OS 설정이 없어 `prefers-reduced-motion` 은 CDP `Emulation.setEmulatedMedia` 로만
켤 수 있다. 켜졌는지를 `matchMedia` 로 되물어 확인한다 — 안 켜졌는데 초록이면 시험이 아니라
장식이다.

크롬은 느리다. 넷을 재는 시험은 **한 번만 띄우고** 뷰포트와 주소만 바꿔 가며 잰다.
"""

import json
import time

from test_web_browser import Chrome, scene  # noqa: F401  (scene 은 이 파일이 쓰는 픽스처다)

# 폰·데스크톱 × 한국어·영어. 인접한 두 경우의 주소가 늘 달라 `Page.navigate` 가 실제로 이동한다.
CASES = ((390, "ko"), (390, "en"), (1240, "ko"), (1240, "en"))
PHONE_MAX = 720  # §4 모바일 구간의 경계 — 이 아래가 카드 레이아웃이다

# 요소 하나를 사람이 읽을 이름으로. `debugLayout` 과 같은 모양(`tag#id.class`).
_NAME_JS = """
  const name = (el) => el.tagName.toLowerCase()
    + (el.id ? '#' + el.id : '')
    + (typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\\s+/).join('.') : '');
"""

# 가로 넘침 계산. `app.js` 의 `debugLayout` 과 같은 질문에 답한다.
# 자기 안에서 가로로 스크롤(또는 클립)하는 조상을 가진 요소는 문서를 밀지 못하므로 뺀다 — `.qwrap`.
OVERFLOW_JS = (
    """
(() => {
  const de = document.documentElement;
  const vw = de.clientWidth;
"""
    + _NAME_JS
    + """
  const boxed = (el) => {
    for (let p = el.parentElement; p && p !== de; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') return true;
    }
    return false;
  };
  const wide = [];
  document.querySelectorAll('body *').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1 && !boxed(el)) {
      wide.push({ el: name(el), right: Math.round(r.right), width: Math.round(r.width) });
    }
  });
  wide.sort((a, b) => b.right - a.right);
  return {
    innerWidth: window.innerWidth,
    clientWidth: vw,
    scrollWidth: de.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    wide: wide.slice(0, 8),
  };
})()
"""
)

# `__ROOT__` 아래(자기 자신 포함)에서 **실제로 도는** 애니메이션. 이름이 `none` 이거나 길이가 0 이면
# 움직이지 않는 것으로 본다. 짧은 목록(`animation-duration`)은 CSS 규칙대로 돌려 쓴다.
# 가상 요소(`::before` · `::after`)도 본다 — 맥박을 거기에 붙이는 게 흔하다.
_ANIMATIONS_JS = (
    """
(() => {
  const root = document.querySelector(__ROOT__);
  if (root === null) return null;
"""
    + _NAME_JS
    + """
  const out = [];
  const scan = (el, pseudo) => {
    const cs = getComputedStyle(el, pseudo);
    const names = cs.animationName.split(',').map((s) => s.trim());
    const durs = cs.animationDuration.split(',').map((s) => s.trim());
    names.forEach((n, i) => {
      const d = durs[durs.length ? i % durs.length : 0] || '0s';
      if (n !== 'none' && parseFloat(d) > 0) {
        out.push({ el: name(el) + (pseudo || ''), animation: n, duration: d });
      }
    });
  };
  [root, ...root.querySelectorAll('*')].forEach((el) => {
    scan(el, null); scan(el, '::before'); scan(el, '::after');
  });
  return out;
})()
"""
)

# 큐·최근의 상태 필. 글리프(`aria-hidden`)와 글자를 따로 뽑는다 — 글자는 글리프를 뺀 나머지다.
PILLS_JS = """
(() => [...document.querySelectorAll('#queue .pill, #recent .pill')].map((p) => {
  const g = p.querySelector('[aria-hidden="true"]');
  const word = [...p.childNodes]
    .filter((n) => !(n.nodeType === 1 && n.getAttribute('aria-hidden') === 'true'))
    .map((n) => n.textContent).join('').replace(/\\s+/g, ' ').trim();
  return {
    cls: typeof p.className === 'string' ? p.className : '',
    glyph: g === null ? null : g.textContent.trim(),
    word: word,
    text: p.textContent.replace(/\\s+/g, ' ').trim(),
  };
}))()
"""

REDUCE_JS = 'matchMedia("(prefers-reduced-motion: reduce)").matches'


def animations_js(root: str = "body") -> str:
    """`root` 아래에서 도는 애니메이션을 묻는 JS 식."""
    return _ANIMATIONS_JS.replace("__ROOT__", json.dumps(root))


def page_url(scene, lang: str) -> str:  # noqa: F811  (인자 이름은 픽스처와 같아도 된다)
    """`?poll=1`(열린 SSE 는 headless Chrome 의 종료를 막는다) + 언어를 못 박은 주소."""
    return f"http://127.0.0.1:{scene.srv.port}/?poll=1&lang={lang}"


def ready_at(scene, lang: str) -> str:  # noqa: F811
    """큐 두 행 + 호스트 막대 + **언어까지** 자리 잡았는가. 반쯤 그려진 화면을 재지 않는다."""
    return f"{scene.ready_js()} && document.documentElement.lang === {json.dumps(lang)}"


def describe(measured: dict) -> str:
    """실패 메시지 한 덩어리 — 무엇이 얼마나 넘쳤는지. 고치는 사람이 다시 재지 않아도 되게."""
    wide = measured["wide"]
    listed = "\n".join(f"    {w['el']} → right {w['right']}px (width {w['width']}px)" for w in wide)
    return (
        f"scrollWidth {measured['scrollWidth']} > clientWidth {measured['clientWidth']}"
        f" (body {measured['bodyScrollWidth']}, window.innerWidth {measured['innerWidth']})\n"
        f"  넘친 요소 {len(wide)}개 (오른쪽 끝 순):\n{listed or '    (없음 — 표 아닌 곳이 밀었다)'}"
    )


# ── (나) 가로로 넘치지 않는다 ────────────────────────────────────────────────


def test_page_never_scrolls_sideways_at_390_and_1240_in_both_languages(scene, tmp_path):  # noqa: F811
    """§4.7 의 네 칸을 시험으로. 크롬은 한 번만 띄우고 뷰포트·주소만 바꾼다."""
    measured: dict[tuple[int, str], dict] = {}
    with Chrome(tmp_path / "chrome-layout", window="1280,900") as c:
        for width, lang in CASES:
            c.viewport(width, 900, mobile=width <= PHONE_MAX)
            c.open(page_url(scene, lang), ready_js=ready_at(scene, lang))
            time.sleep(0.2)  # 뷰포트를 바꾼 뒤 재배치가 끝나기를 기다린다
            m = c.eval(OVERFLOW_JS)
            assert m["innerWidth"] == width, (
                f"{lang}@{width}: viewport is {m['innerWidth']}px — the override did not take"
            )
            measured[(width, lang)] = m
    scene.assert_still_running()

    bad = {case: m for case, m in measured.items() if m["scrollWidth"] > m["clientWidth"] + 1}
    assert not bad, "가로로 넘친다:\n" + "\n".join(
        f"  {lang} @ {width}px — {describe(m)}" for (width, lang), m in bad.items()
    )
    # 넘침 목록도 비어 있어야 한다 — `?debug=1` 이 아무것도 보고하지 않는 게 완료 기준 6 이다
    noisy = {case: m for case, m in measured.items() if m["wide"]}
    assert not noisy, "본문을 밀지는 않지만 뷰포트 밖으로 나간 요소가 있다:\n" + "\n".join(
        f"  {lang} @ {width}px — {describe(m)}" for (width, lang), m in noisy.items()
    )


# ── (다) 움직임은 끌 수 있어야 한다 (§4.2) ──────────────────────────────────


def test_reduced_motion_stops_every_animation(scene, tmp_path):  # noqa: F811
    """`prefers-reduced-motion: reduce` 면 페이지 어디에도 도는 애니메이션이 없다.

    같은 페이지에서 미디어만 `no-preference` 로 되돌려 대조군을 잰다 — 애초에 움직이는 게 없으면
    이 시험은 아무것도 증명하지 않는다(§4.7 이 잡아낸 「통과하지만 재지 않는 시험」과 같은 함정).
    """
    with Chrome(tmp_path / "chrome-motion-off", window="1240,900") as c:
        c.emulate_media({"prefers-reduced-motion": "reduce"})
        c.open(page_url(scene, "ko"), ready_js=ready_at(scene, "ko"))
        reduced = c.eval(REDUCE_JS)
        with_reduce = c.eval(animations_js())
        c.emulate_media({"prefers-reduced-motion": "no-preference"})  # 대조군 — 같은 DOM
        still_reduced = c.eval(REDUCE_JS)
        without_reduce = c.eval(animations_js())
    scene.assert_still_running()
    assert reduced is True, "CDP 가 prefers-reduced-motion 을 못 켰다 — 이 시험은 아무것도 안 잰다"
    assert still_reduced is False, "미디어를 되돌리지 못했다 — 대조군이 없다"
    assert with_reduce == [], "reduce 인데 도는 애니메이션이 있다:\n" + "\n".join(
        f"  {a['el']}: {a['animation']} {a['duration']}" for a in with_reduce
    )
    assert without_reduce, (
        "대조군에서도 도는 애니메이션이 하나도 없다 — 「reduce 면 안 돈다」가 공허하게 통과했다. "
        "움직임이 아직 구현되지 않은 것이다(§4.2)"
    )


def test_running_job_animates_when_motion_is_allowed(scene, tmp_path):  # noqa: F811
    """반대쪽 — `no-preference` 면 도는 잡의 행에 움직이는 요소가 하나 이상 있다(§4.2).

    어느 선택자에 붙일지는 못 박지 않는다. 「도는 행 안에 애니메이션이 도는 요소가 하나 이상
    있다」가 계약이다.
    """
    with Chrome(tmp_path / "chrome-motion-on", window="1240,900") as c:
        c.emulate_media({"prefers-reduced-motion": "no-preference"})
        c.open(page_url(scene, "ko"), ready_js=ready_at(scene, "ko"))
        reduced = c.eval(REDUCE_JS)
        row = animations_js(f'#queue tr[data-job="{scene.running}"]')
        running_row = c.eval(row)
        page = c.eval(animations_js())
    scene.assert_still_running()
    assert reduced is False, "no-preference 를 흉내내지 못했다 — 이 시험은 아무것도 안 잰다"
    assert running_row is not None, f"job {scene.running} 의 큐 행이 없다"
    assert running_row, (
        f"도는 잡 #{scene.running} 의 행에 움직이는 요소가 하나도 없다 — 움직임이 상태 채널이 "
        f"아니게 된다(§4.2). 페이지 전체의 애니메이션: {page}"
    )


# ── (라) 상태는 색만으로 구분되지 않는다 (WCAG 1.4.1) ───────────────────────


def test_state_pills_carry_a_glyph_and_a_word(scene, tmp_path):  # noqa: F811
    """상태 필마다 `aria-hidden` 글리프와 비어 있지 않은 글자가 둘 다 있다.

    색은 마지막 채널이다 — 회색조로 인쇄해도 상태가 읽혀야 한다(완료 기준 5).
    """
    with Chrome(tmp_path / "chrome-pills", window="1240,900") as c:
        c.open(page_url(scene, "ko"), ready_js=ready_at(scene, "ko"))
        pills_ko = c.eval(PILLS_JS)
        c.open(page_url(scene, "en"), ready_js=ready_at(scene, "en"))
        pills_en = c.eval(PILLS_JS)
    scene.assert_still_running()
    for lang, pills in (("ko", pills_ko), ("en", pills_en)):
        assert len(pills) >= 2, f"{lang}: 상태 필이 {len(pills)}개다 — 큐가 안 그려졌다: {pills}"
        for p in pills:
            assert p["glyph"], f"{lang}: `{p['text']}` 필에 aria-hidden 글리프가 없다: {p}"
            assert p["word"], f"{lang}: `{p['text']}` 필에 글자가 없다 — 색·모양만 남는다: {p}"
            assert "undefined" not in p["word"] and p["word"] != "—", f"{lang}: {p}"
    # 같은 잡이 언어만 바뀌었다 — 글리프는 그대로, 글자는 번역된다
    assert [p["glyph"] for p in pills_ko] == [p["glyph"] for p in pills_en], (pills_ko, pills_en)
    assert [p["word"] for p in pills_ko] != [p["word"] for p in pills_en], (pills_ko, pills_en)


# ── (마) 한 행에 채운 칩은 상태 필 하나다 (§3.1 · C-53) ──────────────────────

# 그 행 안에서 **채운 칩**을 센다. 칩은 「글자를 담은 인라인 조각」이다 — 칸(`td`)·막대(`.pbar`)
# 처럼 글자가 없거나 블록인 것은 칩이 아니고, 버튼은 자기 배경이 곧 누를 수 있다는 표시라 뺀다.
# 「채웠다」는 배경색이 투명이 아니거나 배경 이미지(빗금)가 있는 것이다.
FILLED_CHIPS_JS = """
(() => {
  const row = document.querySelector('#queue tr[data-job="__JOB__"]');
  if (!row) return null;
  const painted = (s) => s.backgroundImage !== 'none'
    || !(s.backgroundColor === 'rgba(0, 0, 0, 0)' || s.backgroundColor === 'transparent');
  const chips = [];
  row.querySelectorAll('span, b, i, em, strong').forEach((el) => {
    const s = getComputedStyle(el);
    if (!el.textContent.trim()) return;
    if (!s.display.startsWith('inline')) return;
    if (!painted(s)) return;
    chips.push({
      cls: String(el.className || '').trim().split(/\\s+/).filter(Boolean),
      text: el.textContent.trim().slice(0, 40),
      bg: s.backgroundColor,
      bgi: s.backgroundImage,
    });
  });
  const reason = row.querySelector('td.reason span.reason');  // 칸(`td.reason`)이 아니라 칩이다
  return {
    chips: chips,
    rowClass: row.className,
    reasonClass: reason ? String(reason.className) : null,
    reasonPainted: reason ? painted(getComputedStyle(reason)) : null,
    reasonText: reason ? reason.textContent.trim().slice(0, 40) : null,
  };
})()
"""


def test_a_row_never_shows_two_filled_status_chips(scene, tmp_path):  # noqa: F811
    """행의 **상태**를 말하는 채운 칩은 하나다 — 상태 필.

    §3.1 의 원칙을 2026-09-15 에 한 칸 좁혔다: 신뢰도 배지(`.conf.*`)는 예외다. 그 배지가
    말하는 것은 잡의 상태가 아니라 「완료 예상을 얼마나 믿나」이고, 자리도 「완료 예상」
    칸이다. 그 판단의 근거는 `style.css` 의 `.conf` 위 주석에 있다.

    **비어 있는 시험이 되지 않게** 진짜로 멈춘 행을 만든다. `estimate.default_seconds` 를
    1초로 낮추면 장면의 도는 잡이 `elapsed > 3 × expected` 로 `stuck` 이 되고, 이유 칸에
    `.reason.stuck` 이 실제로 그려진다 — 그 칩에 배경을 도로 넣으면 여기가 빨개진다.
    """
    scene.srv.cfg.estimate.default_seconds = 1  # 요청마다 `queue_config()` 가 새로 읽는다
    job = scene.running
    ready = ready_at(scene, "ko") + (
        f" && document.querySelector('#queue tr[data-job=\"{job}\"].stuck') !== null"
    )
    with Chrome(tmp_path / "chrome-chips", window="1240,900") as c:
        c.open(page_url(scene, "ko"), ready_js=ready, timeout=30)
        m = c.eval(FILLED_CHIPS_JS.replace("__JOB__", str(job)))
    scene.assert_still_running()

    assert m is not None, f"job {job} 의 큐 행이 없다"
    assert "stuck" in m["rowClass"].split(), m
    # 장면 확인 — 이유 칩이 정말로 그려졌다. 아니면 아래 단언은 아무것도 재지 않는다
    assert m["reasonClass"] and "stuck" in m["reasonClass"].split(), m
    assert m["reasonPainted"] is False, f"이유 칩이 배경을 도로 얻었다: {m}"

    status = [c for c in m["chips"] if "conf" not in c["cls"]]
    assert len(status) == 1, f"한 행에 상태를 말하는 채운 칩이 {len(status)}개다: {status}"
    assert "pill" in status[0]["cls"], f"채운 칩 하나는 상태 필이어야 한다: {status[0]}"
    badges = [c for c in m["chips"] if "conf" in c["cls"]]
    assert len(badges) <= 1, f"신뢰도 배지는 행마다 하나뿐이다(유일한 예외): {badges}"
