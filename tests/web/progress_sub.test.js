"use strict";
// 스텝 안의 세부 진행(`::rcm::progress::` · `progress.sub` · `progress.units[]`) —
// `rcm.overallProgress` · `rcm.progressBarHtml` · `rcm.progressHead(Html)` · `rcm.unitsGridHtml`.
//
// 잠그는 것:
// - 눈금의 근거는 `sub` 가 먼저다: 스크립트가 **아는** 분모라 선언 스텝·시간보다 앞선다. 화면은
//   분모를 다시 본다 — `total < 1`, `done > total`, 빈 unit 이면 **없는 것**이다(지어내지 않는다).
// - 도는 잡은 100% 가 되지 않는다: `done == total` 이어도 99% 다(그 스텝의 단위가 다 끝난 것뿐).
// - 형편(`stuck`·`quiet`)은 sub 눈금에서도 그대로 말한다.
// - 머리줄은 `now: <unit> · <state>[ · <note>]` 를 끝에 단다 — HTML 을 벗기면 글자가 같다.
// - 격자는 단위가 둘 이상일 때만, 칸의 class 가 state 이고 이름은 title/aria-label 에 있다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job } = require("./helpers");

const rcm = load();
const status = fixture("main");
const row = (id) => structuredClone(job(status, id));
const strip = (html) => html.replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");

const SUB = { done: 41, total: 68, unit: "inquiry_photo/android", state: "run", note: "+1284 ~2 -0", at: "2026-09-04T00:52:10Z" };
const withSub = (sub = SUB, over = {}) => {
  const r = row(412);
  r.progress.sub = sub;
  r.progress.units = [];
  r.progress.units_truncated = false;
  Object.assign(r.estimate, over);
  return r;
};

describe("overallProgress — sub 가 먼저다", () => {
  test("sub 가 있으면 basis=sub, done/total 그대로, 퍼센트는 그 비율", () => {
    const p = rcm.overallProgress(withSub());
    assert.equal(p.basis, "sub");
    assert.equal(p.done, 41);
    assert.equal(p.total, 68);
    assert.equal(p.unit, "inquiry_photo/android");
    assert.equal(p.pct, 60);
    assert.equal(p.condition, "normal");
  });

  test("선언한 스텝 총계가 있어도 sub 가 이긴다 (412 는 steps 8 개 선언)", () => {
    const r = withSub();
    assert.equal(r.progress.steps_total_partial, false);
    assert.equal(rcm.overallProgress(r).basis, "sub");
  });

  test("sub 가 null 이면 오늘 규칙대로 steps 눈금", () => {
    assert.equal(rcm.overallProgress(withSub(null)).basis, "steps");
  });

  test("「지금까지 본」 스텝 총계는 분모가 아니지만 sub 는 분모다", () => {
    const r = withSub();
    r.progress.steps_total_partial = true;
    assert.equal(rcm.overallProgress(r).basis, "sub");
    r.progress.sub = null;
    assert.equal(rcm.overallProgress(r).basis, "time");
  });

  test("done == total 이어도 도는 잡은 99% 다 — 100% 는 끝났다의 자리", () => {
    const p = rcm.overallProgress(withSub({ ...SUB, done: 68, total: 68 }));
    assert.equal(p.basis, "sub");
    assert.equal(p.pct, 99);
    assert.equal(p.condition, "normal");
  });

  test("0/68 은 0% 이되 basis 는 sub 다 (빗금이 아니라 빈 막대 — 분모를 안다)", () => {
    const p = rcm.overallProgress(withSub({ ...SUB, done: 0 }));
    assert.equal(p.basis, "sub");
    assert.equal(p.pct, 0);
  });

  test("분모가 거짓이면 sub 는 없는 것이다 — total 0 · done > total · 음수 · 빈 unit · 문자열", () => {
    const bad = [
      { ...SUB, total: 0 }, { ...SUB, done: 70 }, { ...SUB, done: -1 }, { ...SUB, unit: "" },
      { ...SUB, done: "41" }, { ...SUB, total: null }, "41/68", 7
    ];
    bad.forEach((sub, i) => {
      const p = rcm.overallProgress(withSub(sub));
      assert.equal(p.basis, "steps", `case ${i}: ${JSON.stringify(sub)}`);
      assert.equal(rcm.subProgress({ sub }), null, `case ${i}`);
    });
  });

  test("stuck · quiet 은 sub 눈금 위에서도 형편으로 남는다", () => {
    assert.deepEqual(
      [rcm.overallProgress(withSub(SUB, { stuck: true })).condition, rcm.overallProgress(withSub(SUB, { quiet: true })).condition],
      ["stuck", "quiet"]
    );
    assert.equal(rcm.overallProgress(withSub(SUB, { stuck: true })).pct, 60, "끝난 단위 수는 stuck 이어도 사실이다");
    assert.equal(rcm.overallProgress(withSub(SUB, { stuck: true, quiet: true })).condition, "stuck", "stuck 이 quiet 을 이긴다");
  });

  test("준비 중이면 sub 가 있어도 눈금이 없다", () => {
    const r = withSub();
    r.progress.phase = "materializing";
    assert.equal(rcm.overallProgress(r).condition, "preparing");
    assert.equal(rcm.overallProgress(r).basis, "none");
  });

  test("도는 잡이 아니면 null", () => {
    const r = withSub();
    r.state = "queued";
    assert.equal(rcm.overallProgress(r), null);
  });
});

describe("progressBarHtml — sub 눈금의 라벨", () => {
  test('data-basis="sub", 라벨은 「60% · 41/68 · <unit>」, aria-valuenow 60', () => {
    const html = rcm.progressBarHtml(withSub(), "en", false);
    assert.match(html, /data-basis="sub"/);
    assert.match(html, /aria-valuenow="60"/);
    assert.match(html, /60% · 41\/68 · inquiry_photo\/android/);
    assert.match(html, /<i data-fill="60"><\/i>/);
  });

  test("한국어도 같은 숫자와 단위 — unit 은 번역하지 않는다", () => {
    const html = rcm.progressBarHtml(withSub(), "ko", false);
    assert.match(html, /60% · 41\/68 · inquiry_photo\/android/);
  });

  test("unit 의 HTML 은 escape 된다", () => {
    const html = rcm.progressBarHtml(withSub({ ...SUB, unit: "<b>x</b>" }), "en", false);
    assert.ok(!html.includes("<b>x</b>"));
    assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
  });

  test("sub 눈금에는 1초 틱이 없다 — 마커가 올 때만 움직인다", () => {
    assert.ok(!rcm.progressBarHtml(withSub(), "en", true).includes('data-tick="progress"'));
  });

  test("stuck 이면 aria-valuetext 에 형편이 남고 화면 라벨은 눈금만", () => {
    const html = rcm.progressBarHtml(withSub(SUB, { stuck: true }), "en", false);
    assert.match(html, /aria-valuetext="60% · 41\/68 · inquiry_photo\/android · not responding"/);
    assert.match(html, /<span class="plab" title="[^"]*">60% · 41\/68 · inquiry_photo\/android<\/span>/);
  });
});

describe("progressHead — now: <unit> · <state>[ · <note>]", () => {
  test("sub 가 있으면 끝에 now 를 단다", () => {
    const p = withSub().progress;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job 59s · now: inquiry_photo/android · run · +1284 ~2 -0");
  });

  test("note 가 없으면 unit · state 까지", () => {
    const p = withSub({ ...SUB, note: null }).progress;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job 59s · now: inquiry_photo/android · run");
  });

  test("실패 스텝 수 뒤에 온다", () => {
    const p = withSub({ ...SUB, note: null }).progress;
    p.steps[1].ok = false;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job 59s · 1 step failed · now: inquiry_photo/android · run");
  });

  test("스텝 마커 없이 progress 만 찍은 잡도 now 를 단다", () => {
    const p = withSub({ ...SUB, note: null }).progress;
    Object.assign(p, { steps: [], steps_total: null, steps_done: 0, current_index: null, current_name: null, current_seconds: null });
    assert.equal(rcm.progressHead(p), "no step markers · job 59s · now: inquiry_photo/android · run");
  });

  test("한국어는 「지금:」", () => {
    const p = withSub({ ...SUB, note: null }).progress;
    assert.match(rcm.progressHead(p, "ko"), / · 지금: inquiry_photo\/android · run$/);
  });

  test("거짓 분모의 sub 는 머리줄에도 없다", () => {
    const p = withSub({ ...SUB, done: 99 }).progress;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job 59s");
  });

  test("progressHeadHtml 을 벗기면 progressHead 와 같다 — 두 언어 · live 양쪽", () => {
    [true, false].forEach((live) => ["en", "ko"].forEach((lang) => {
      [withSub().progress, withSub({ ...SUB, unit: "a<b>&c" }).progress].forEach((p) => {
        assert.equal(strip(rcm.progressHeadHtml(structuredClone(p), lang, live)), rcm.progressHead(structuredClone(p), lang), `${lang} live=${live}`);
      });
    }));
    assert.match(rcm.progressHeadHtml(withSub().progress, "en", false), /<span class="sub-now">now: /);
  });
});

describe("unitsGridHtml — 단위 격자", () => {
  const units = [
    { unit: "a/ios", state: "ok", note: "+12", at: null },
    { unit: "a/android", state: "fail", note: null, at: null },
    { unit: "b/ios", state: "run", note: null, at: null },
    { unit: "b/android", state: "skip", note: null, at: null },
    { unit: "c/ios", state: "wait", note: null, at: null },
    { unit: "c/android", state: "blocked", note: null, at: null },
    { unit: "d/ios", state: "env", note: null, at: null },
    { unit: "d/android", state: "review", note: null, at: null }
  ];

  test("칸마다 class 가 state 이고 이름·상태·note 가 title 과 aria-label 에 있다", () => {
    const html = rcm.unitsGridHtml({ units, units_truncated: false }, "en");
    assert.match(html, /^<div class="ugrid" role="list" aria-label="8 units in this step">/);
    const cells = html.match(/<span class="u [a-z]+" role="listitem"/g);
    assert.deepEqual(cells.map((c) => c.match(/class="u ([a-z]+)"/)[1]), ["ok", "fail", "run", "skip", "wait", "blocked", "env", "review"]);
    assert.match(html, /title="a\/ios · ok · \+12" aria-label="a\/ios · ok · \+12"/);
    assert.match(html, /title="a\/android · fail" aria-label="a\/android · fail"/);
    // 색만으로 말하지 않는다 — 글자가 있다
    assert.match(html, /class="u ok"[^>]*><i aria-hidden="true">✓<\/i>/);
    assert.match(html, /class="u fail"[^>]*><i aria-hidden="true">✗<\/i>/);
    assert.match(html, /class="u run"[^>]*><i aria-hidden="true">▶<\/i>/);
    assert.ok(!html.includes("ugrid-more"));
  });

  test("단위가 둘 미만이면 격자가 없다", () => {
    assert.equal(rcm.unitsGridHtml({ units: [units[0]] }, "en"), "");
    assert.equal(rcm.unitsGridHtml({ units: [] }, "en"), "");
    assert.equal(rcm.unitsGridHtml({}, "en"), "");
    assert.equal(rcm.unitsGridHtml(null, "en"), "");
  });

  test("모르는 state 는 class unknown 이고 그 말은 title 에 그대로", () => {
    const html = rcm.unitsGridHtml({ units: [units[0], { unit: "z", state: "weird" }] }, "en");
    assert.match(html, /class="u unknown" role="listitem" title="z · weird"/);
  });

  test("이름·note 의 HTML 은 escape 된다", () => {
    const html = rcm.unitsGridHtml({ units: [{ unit: '<img src=x onerror="1">', state: "ok" }, units[1]] }, "en");
    assert.ok(!html.includes("<img"));
    assert.match(html, /&lt;img src=x onerror=&quot;1&quot;&gt;/);
  });

  test("units_truncated 면 「더 있다」 한 줄이 붙는다 — 두 언어", () => {
    assert.match(rcm.unitsGridHtml({ units, units_truncated: true }, "en"), /ugrid-more">more units than the grid shows \(500 per step\)<\/div>$/);
    assert.match(rcm.unitsGridHtml({ units, units_truncated: true }, "ko"), /ugrid-more">격자에 못 올린 단위가 더 있습니다/);
  });
});
