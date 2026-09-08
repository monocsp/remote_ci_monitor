"use strict";
// 초가 부드럽게 올라간다 (§4.6-다, 오너 피드백 「9초에서 갑자기 26초」).
//
// 서버가 보내는 초는 상태 문서를 만든 순간의 값이다. 그것만 정적으로 그리면 다음 문서가 올 때까지
// 얼어 있다가 한 번에 뛴다. 고침은 「도는 잡의 초는 시각을 기준점으로 삼아 화면이 스스로 센다」 —
// 1초 틱이 다시 쓰는 자리에 `data-tick="elapsed" data-from="<ISO>"` 를 단다.
//
// 잠그는 것:
// - `progressHeadHtml(prog, lang, live)` 를 내보낸다.
// - **태그를 벗기면 `progressHead(prog, lang)` 와 글자가 똑같다** — `live` 가 무엇이든.
//   두 함수가 조용히 갈라지는 것을 막는 핵심 시험이다.
// - 이스케이프가 살아 있다 — 스텝 이름은 팀이 쓴 값이지 신뢰할 마크업이 아니다.
// - 잡 초는 `live`(=아직 도는 중) 이면 센다. 스텝 초는 **도는 스텝이 있을 때만** —
//   끝난 스텝의 초가 계속 올라가면 거짓말이 된다.
// - 기준점이 없는 옛 서버 문서에는 그 자리만 그냥 숫자다 — 던지지 않는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { load, fixture, job, fromNow } = require("./helpers");
const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));

const rcm = load();
const LANGS = I18N.LANGS;
const LIVE = [true, false];

const status = fixture("main");
const prog = (id) => structuredClone(job(status, id).progress);

// 픽스처의 진행 문서에 M5d-2 가 더한 기준점을 얹는다(tests/ 픽스처가 아직 안 실어도 시험이 산다).
// 412: 5/8, test 51s 도는 중, 잡 59s.
function running412() {
  const p = prog(412);
  p.job_started_at = fromNow(-59);
  p.steps[p.steps.length - 1].started_at = fromNow(-51);
  return p;
}

const JOB_FROM = fromNow(-59);
const STEP_FROM = fromNow(-51);

// 실체 참조를 되돌린다. `&amp;` 를 **마지막**에 풀어야 `&amp;lt;` 가 `<` 로 무너지지 않는다.
function unesc(s) {
  return String(s)
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&");
}

// 태그를 벗기고 실체 참조를 되돌린 글자.
function strip(html) {
  return unesc(String(html).replace(/<[^>]*>/g, ""));
}

// `data-tick` 이 붙은 여는 태그를 문서 순서대로. text 는 태그 바로 뒤의 글자(다음 `<` 까지).
function ticks(html) {
  const out = [];
  const re = /<[a-zA-Z][^\s>]*([^>]*\bdata-tick="([^"]*)"[^>]*)>([^<]*)/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const from = /\bdata-from="([^"]*)"/.exec(m[1]);
    out.push({ tick: m[2], from: from ? unesc(from[1]) : null, text: unesc(m[3]) });
  }
  return out;
}

describe("progressHeadHtml — 있고, progressHead 와 갈라지지 않는다", () => {
  test("module.exports 로 나온다", () => {
    assert.equal(typeof rcm.progressHeadHtml, "function");
  });

  // 같은 문서를 두 함수에 넣고, 태그를 벗긴 글자가 같은지 본다. live 가 켜지든 꺼지든 같아야 한다.
  const cases = {
    "도는 중 · 기준점 있음": running412(),
    "도는 중 · 기준점 없음 (옛 서버)": prog(412),
    "so far · 3/3": prog(409),
    "실패한 스텝이 있는 채로 계속 도는 중": (() => {
      const p = running412();
      p.failed_step = "format";
      p.steps[1].ok = false;
      p.steps[2].ok = false;
      return p;
    })(),
    "마커 없음": (() => {
      const p = running412();
      Object.assign(p, {
        steps: [], steps_total: null, steps_done: 0,
        current_index: null, current_name: null, current_seconds: null,
      });
      return p;
    })(),
    "전부 done — 도는 스텝 없음": (() => {
      const p = running412();
      const last = p.steps[p.steps.length - 1];
      last.state = "done";
      last.ok = true;
      return p;
    })(),
    "잡 초를 모름": (() => {
      const p = running412();
      p.job_seconds = null;
      return p;
    })(),
  };

  Object.keys(cases).forEach((name) => {
    test(`태그를 벗기면 progressHead 와 같다 — ${name}`, () => {
      LANGS.forEach((lang) => {
        const plain = rcm.progressHead(structuredClone(cases[name]), lang);
        LIVE.forEach((live) => {
          const html = rcm.progressHeadHtml(structuredClone(cases[name]), lang, live);
          assert.equal(strip(html), plain, `${lang} / live=${live} / ${name}`);
        });
      });
    });
  });

  test("스텝 이름의 `<` 는 이스케이프된 채로 살아 있다 — 글자는 같고 마크업은 안 샌다", () => {
    const p = running412();
    p.current_name = 'test <b>"x"</b> & \'y\'';
    LANGS.forEach((lang) => {
      const plain = rcm.progressHead(structuredClone(p), lang);
      assert.match(plain, /<b>/, "평문 쪽은 원문 그대로다");
      LIVE.forEach((live) => {
        const html = rcm.progressHeadHtml(structuredClone(p), lang, live);
        assert.ok(!/<b>/.test(html), `${lang} / live=${live}: 스텝 이름의 태그가 날것으로 샜다`);
        assert.match(html, /&lt;b&gt;/, `${lang} / live=${live}: 이스케이프가 없다`);
        assert.equal(strip(html), plain, `${lang} / live=${live}`);
      });
    });
  });

  test("progress 가 없거나 materializing 이면 progressHead 와 같이 null", () => {
    const mat = running412();
    Object.assign(mat, { phase: "materializing", steps: [], steps_total: null, current_name: null });
    LANGS.forEach((lang) => {
      LIVE.forEach((live) => {
        assert.equal(rcm.progressHeadHtml(null, lang, live), null);
        assert.equal(rcm.progressHeadHtml(undefined, lang, live), null);
        assert.equal(rcm.progressHeadHtml(structuredClone(mat), lang, live), null);
      });
    });
  });
});

// 두 자리의 조건이 다르다: 잡 초는 `live` + `job_started_at`, 스텝 초는 거기에 「도는 스텝」까지.
describe("progressHeadHtml — live 일 때만 틱이 붙는다", () => {
  test("도는 잡 · live → 스텝 초와 잡 초 두 자리에 data-tick", () => {
    LANGS.forEach((lang) => {
      const t = ticks(rcm.progressHeadHtml(running412(), lang, true));
      assert.equal(t.length, 2, lang + ": 틱은 스텝 초·잡 초 두 자리다");
      assert.deepEqual(t.map((x) => x.tick), ["elapsed", "elapsed"], lang);
      // 머리줄은 「step 5/8 · test · 51s · job 59s」 — 스텝 초가 잡 초보다 앞이다.
      assert.deepEqual(t.map((x) => x.from), [STEP_FROM, JOB_FROM], lang + ": 기준점이 뒤바뀌었다");
      // 1초 틱은 이 요소의 글자를 소요 시간으로 **통째로** 갈아 끼운다(tickTexts).
      // 라벨까지 감싸면 다음 틱에 「job」이 지워진다.
      assert.deepEqual(t.map((x) => x.text), ["51s", "59s"], lang + ": 틱이 소요 시간만 감싸야 한다");
    });
  });

  test("live=false → data-tick 이 하나도 없다", () => {
    LANGS.forEach((lang) => {
      const html = rcm.progressHeadHtml(running412(), lang, false);
      assert.ok(!/data-tick/.test(html), lang);
      assert.ok(!/data-from/.test(html), lang);
    });
  });

  test("live 를 안 주면 틱이 아니다 — 스스로 세는 것은 켜서 쓰는 것이다", () => {
    LANGS.forEach((lang) => {
      assert.ok(!/data-tick/.test(rcm.progressHeadHtml(running412(), lang)), lang);
    });
  });

  test("도는 스텝이 없으면 스텝 초에는 안 붙지만, 잡 초는 계속 센다", () => {
    // 마지막 스텝이 끝나고도 잡은 아직 도는 구간(정리·종료 대기)이 있다. 그때 잡 초까지 얼면
    // 오너가 지적한 「숫자가 튄다」가 그 자리에서 그대로 재발한다. 끝난 **스텝**의 초만 멈춘다.
    const p = running412();
    const last = p.steps[p.steps.length - 1];
    last.state = "done";
    last.ok = true;
    LANGS.forEach((lang) => {
      const html = rcm.progressHeadHtml(structuredClone(p), lang, true);
      const froms = ticks(html).map((t) => t.from);
      assert.deepEqual(froms, [p.job_started_at], lang + ": 잡 기준점 하나만 남는다");
      assert.ok(!froms.includes(last.started_at), lang + ": 끝난 스텝의 초가 오르면 거짓말이다");
    });
  });

  test("마커가 없는 잡도 잡 초는 센다 — 화면에서 움직이는 숫자가 그것뿐이다", () => {
    // 스텝 마커가 없으면 머리줄에는 잡 초 하나뿐이다. 그것마저 얼면 도는 잡이 멈춰 보인다.
    const p = running412();
    Object.assign(p, {
      steps: [], steps_total: null, steps_done: 0,
      current_index: null, current_name: null, current_seconds: null,
    });
    LANGS.forEach((lang) => {
      const html = rcm.progressHeadHtml(structuredClone(p), lang, true);
      const t = ticks(html);
      assert.equal(t.length, 1, lang + ": 틱은 잡 초 한 자리다");
      assert.equal(t[0].from, JOB_FROM, lang);
      assert.equal(t[0].text, "59s", lang + ": 틱이 소요 시간만 감싸야 한다");
      assert.equal(strip(html), rcm.progressHead(structuredClone(p), lang), lang);
      assert.ok(!/data-tick/.test(rcm.progressHeadHtml(structuredClone(p), lang, false)),
        lang + ": live=false 면 여전히 정적이다");
    });
  });
});

describe("progressHeadHtml — 기준점이 없는 옛 서버 문서", () => {
  test("둘 다 없으면 그냥 숫자다 — 던지지 않는다", () => {
    const p = prog(412);   // job_started_at·started_at 없음
    LANGS.forEach((lang) => {
      let html;
      assert.doesNotThrow(() => { html = rcm.progressHeadHtml(structuredClone(p), lang, true); }, lang);
      assert.ok(!/data-tick/.test(html), lang);
      assert.equal(strip(html), rcm.progressHead(structuredClone(p), lang), lang);
    });
  });

  test("스텝 기준점만 있으면 스텝 초에만 붙는다", () => {
    const p = running412();
    delete p.job_started_at;
    LANGS.forEach((lang) => {
      const t = ticks(rcm.progressHeadHtml(structuredClone(p), lang, true));
      assert.equal(t.length, 1, lang);
      assert.equal(t[0].from, STEP_FROM, lang);
      assert.equal(t[0].text, "51s", lang);
    });
  });

  test("잡 기준점만 있으면 잡 초에만 붙는다", () => {
    const p = running412();
    delete p.steps[p.steps.length - 1].started_at;
    LANGS.forEach((lang) => {
      const t = ticks(rcm.progressHeadHtml(structuredClone(p), lang, true));
      assert.equal(t.length, 1, lang);
      assert.equal(t[0].from, JOB_FROM, lang);
      assert.equal(t[0].text, "59s", lang);
    });
  });

  test("기준점이 null 이어도(키만 있고 값이 없음) 던지지 않고 그냥 숫자다", () => {
    const p = running412();
    p.job_started_at = null;
    p.steps[p.steps.length - 1].started_at = null;
    LANGS.forEach((lang) => {
      let html;
      assert.doesNotThrow(() => { html = rcm.progressHeadHtml(structuredClone(p), lang, true); }, lang);
      assert.ok(!/data-tick/.test(html), lang);
      assert.equal(strip(html), rcm.progressHead(structuredClone(p), lang), lang);
    });
  });
});
