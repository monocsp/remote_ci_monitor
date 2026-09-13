"use strict";
// 전체 진행 막대(2026-09-09 오너 요청, 같은 날 Codex 리뷰 반영) — `rcm.overallProgress` ·
// `rcm.progressBarHtml`.
//
// 잠그는 것:
// - 눈금의 **근거**(`basis`)와 잡의 **형편**(`condition`)은 다른 축이다. 하나로 뭉치면 「stuck 인데
//   왜 파란 막대가 절반 차 있나」 같은 규칙을 사람이 외워야 한다.
// - 눈금을 주지 않는 자리: 「지금까지 본」 스텝 총계 · 설치 기본값 추정(`source: "default"`) ·
//   stuck 의 시간 눈금 · 준비 중. 0% 로 그리지 않는다(fail-open 금지).
// - **도는 잡은 100% 가 되지 않는다.** 예측은 99% 가 상한이고(반올림도 안 쓴다), 선언한 스텝을 다
//   끝냈으면 퍼센트 대신 「마무리 중」이다 — 꽉 찬 막대는 「끝났다」로 읽힌다.
// - 경계는 `>=` 다: 경과가 추정과 **같으면** 이미 초과다(1초 틱과 첫 렌더가 같은 자리에서 넘어간다).
// - 보조기기가 읽는 값(`aria-valuetext`)과 눈에 보이는 글자(`.plab`)가 같다.
// - 채움 폭은 HTML 에 `data-fill` 로만 싣는다(실제 폭은 DOM 에 넣은 뒤 화면 층이 준다 —
//   자동 레이아웃 표 안에서 파싱된 퍼센트 폭이 100% 로 굳는 크롬 동작 때문. 그린 길이가 숫자와
//   같은지는 tests/test_web_browser.py 가 진짜 브라우저에서 잰다).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job } = require("./helpers");

const rcm = load();

const status = fixture("main");
const row = (id) => structuredClone(job(status, id));
// 마커 없는 도는 잡: 시간 눈금만 남는다
const timeRow = (over = {}) => {
  const r = row(412);
  r.progress = Object.assign(r.progress, {
    steps: [], steps_total: null, steps_total_partial: false, steps_done: 0,
    current_index: null, current_name: null, current_seconds: null
  });
  Object.assign(r.estimate, over);
  return r;
};

describe("overallProgress — 근거(basis)", () => {
  test("스텝 총계를 선언한 잡 → 끝난 스텝 비율", () => {
    assert.deepEqual(rcm.overallProgress(row(412)), {
      basis: "steps", condition: "normal", pct: 50, done: 4, total: 8,
      expected: null, startedAt: null, source: null
    });
  });

  test("총계가 「지금까지」면 스텝으로 세지 않고 시간으로 센다", () => {
    // #409 는 steps_total 3 이지만 partial — 경과 380 / 추정 540
    const p = rcm.overallProgress(row(409));
    assert.equal(p.basis, "time");
    assert.equal(p.pct, 70);
    assert.equal(p.expected, 540);
    assert.equal(p.source, "measured");
    assert.equal(p.startedAt, row(409).started_at);
    assert.equal(p.done, null);
  });

  test("마커가 없어도 추정이 있으면 시간으로 센다 — 내림이다(반올림이 아니다)", () => {
    const p = rcm.overallProgress(timeRow());
    assert.equal(p.basis, "time");
    assert.equal(p.pct, 15);  // 59 / 369 = 15.98… → 15
  });

  test("설치 기본값 추정(source: default)은 눈금의 근거가 아니다", () => {
    // 표본도 프리셋 값도 없는 600초 기본값이 70% 로 그려지면 자신있는 거짓말이다
    const p = rcm.overallProgress(timeRow({ source: "default", expected_seconds: 600, elapsed_seconds: 420 }));
    assert.equal(p.basis, "none");
    assert.equal(p.condition, "normal");
    assert.equal(p.pct, null);
  });

  test("프리셋 추정은 눈금을 주되 라벨이 출처를 밝힌다", () => {
    const p = rcm.overallProgress(timeRow({ source: "preset" }));
    assert.equal(p.basis, "time");
    assert.equal(p.source, "preset");
  });

  test("스텝 총계 0 은 나눗셈 근거가 아니다", () => {
    const r = row(412);
    r.progress.steps_total = 0;
    r.progress.steps_done = 0;
    assert.equal(rcm.overallProgress(r).basis, "time");
  });

  test("근거가 하나도 없으면 눈금이 없다 — 0% 가 아니다", () => {
    const r = timeRow();
    r.estimate = {};
    assert.deepEqual(rcm.overallProgress(r), {
      basis: "none", condition: "normal", pct: null, done: null, total: null,
      expected: null, startedAt: null, source: null
    });
  });
});

describe("overallProgress — 형편(condition)", () => {
  test("경과가 추정과 같으면 이미 초과다 (경계는 >=)", () => {
    const p = rcm.overallProgress(timeRow({ elapsed_seconds: 369 }));
    assert.equal(p.condition, "over");
    assert.equal(p.pct, null);
    assert.equal(p.expected, 369);
  });

  test("추정 직전은 아직 초과가 아니고, 눈금은 99% 를 넘지 않는다", () => {
    const p = rcm.overallProgress(timeRow({ elapsed_seconds: 368 }));
    assert.equal(p.condition, "normal");
    assert.equal(p.pct, 99);
  });

  test("반올림으로 100% 가 되지 않는다 — 도는 잡에 꽉 찬 막대는 없다", () => {
    // 367.5 / 369 = 99.59% → 반올림이면 100
    const p = rcm.overallProgress(timeRow({ elapsed_seconds: 367.5 }));
    assert.equal(p.pct, 99);
  });

  test("`overdue` 플래그만 서 있어도 초과다", () => {
    const p = rcm.overallProgress(timeRow({ elapsed_seconds: 10, overdue: true }));
    assert.equal(p.condition, "over");
    assert.equal(p.pct, null);
  });

  test("선언한 스텝을 다 끝냈는데 잡이 안 끝났다 → 마무리 중(퍼센트 없음)", () => {
    const r = row(412);
    r.progress.steps_done = 8;
    const p = rcm.overallProgress(r);
    assert.equal(p.basis, "steps");
    assert.equal(p.condition, "finalizing");
    assert.equal(p.pct, null);
    assert.equal(p.done, 8);
    assert.equal(p.total, 8);
  });

  test("서버가 총계보다 큰 done 을 보내도 총계까지만 센다", () => {
    const r = row(412);
    r.progress.steps_done = 12;
    const p = rcm.overallProgress(r);
    assert.equal(p.done, 8);
    assert.equal(p.condition, "finalizing");
  });

  test("stuck 은 시간 눈금을 잃는다 — 도는 중인지부터 모른다", () => {
    const p = rcm.overallProgress(timeRow({ stuck: true }));
    assert.equal(p.basis, "none");
    assert.equal(p.condition, "stuck");
    assert.equal(p.pct, null);
  });

  test("stuck 이어도 끝난 스텝 수는 사실이다 — 눈금은 지키고 형편을 함께 말한다", () => {
    const r = row(412);
    r.estimate.stuck = true;
    r.estimate.overdue = true;
    const p = rcm.overallProgress(r);
    assert.equal(p.basis, "steps");
    assert.equal(p.condition, "stuck");
    assert.equal(p.pct, 50);
  });

  test("근거가 없는 stuck 잡도 형편은 말한다", () => {
    const r = timeRow();
    r.estimate = { stuck: true };
    const p = rcm.overallProgress(r);
    assert.equal(p.basis, "none");
    assert.equal(p.condition, "stuck");
  });

  test("워크스페이스 준비 중은 눈금이 없고 형편이 준비 중이다", () => {
    const r = row(412);
    r.progress = { timing: "as_received", phase: "materializing", steps: [], steps_total: null, steps_done: 0 };
    const p = rcm.overallProgress(r);
    assert.equal(p.basis, "none");
    assert.equal(p.condition, "preparing");
  });

  test("취소 중인 잡도 도는 잡이다 — 막대를 지우지 않는다", () => {
    const r = row(412);
    r.state = "cancelling";
    assert.equal(rcm.overallProgress(r).basis, "steps");
  });

  test("대기·업로드·끝난 잡·없는 행은 막대가 없다(null)", () => {
    assert.equal(rcm.overallProgress(row(413)), null);
    assert.equal(rcm.overallProgress(row(414)), null);
    assert.equal(rcm.overallProgress(row(415)), null);
    assert.equal(rcm.overallProgress(null), null);
    assert.equal(rcm.overallProgress({ state: "succeeded", progress: { steps_total: 8, steps_done: 8 } }), null);
  });
});

describe("timePct — 1초 틱과 첫 렌더가 같은 규칙을 쓴다", () => {
  test("내림 · 0~99 사이", () => {
    assert.equal(rcm.timePct(0, 100), 0);
    assert.equal(rcm.timePct(1, 100), 1);
    assert.equal(rcm.timePct(99.9, 100), 99);
    assert.equal(rcm.timePct(100, 100), 99);   // 경계 자체는 호출하는 쪽이 over 로 가른다
    assert.equal(rcm.timePct(-5, 100), 0);
  });
});

describe("progressBarHtml", () => {
  const html = (r, lang, live) => rcm.progressBarHtml(r, lang, live);
  // 태그를 벗긴 라벨 = 보조기기가 읽는 값
  const label = (h) => /class="plab">([^<]*)</.exec(h)[1];

  test("스텝 눈금 — 폭·퍼센트·근거가 한 줄에 다 있다", () => {
    const h = html(row(412), "en", false);
    assert.match(h, /data-basis="steps" data-cond="normal"/);
    assert.match(h, /data-fill="50"/);
    assert.equal(label(h), "50% · 4/8 steps");
    assert.match(h, /role="progressbar"/);
    assert.match(h, /aria-valuenow="50"/);
    assert.match(h, /aria-label="#412 progress"/);
  });

  test("보이는 글자와 읽히는 글자가 같다", () => {
    [row(412), timeRow(), timeRow({ stuck: true }), timeRow({ elapsed_seconds: 400 })].forEach((r) => {
      const h = html(r, "ko", false);
      assert.equal(label(h), /aria-valuetext="([^"]*)"/.exec(h)[1]);
    });
  });

  test("한국어도 같은 눈금 — 숫자는 그대로, 문구만 그 언어", () => {
    const h = html(row(412), "ko", false);
    assert.equal(label(h), "50% · 스텝 4/8");
    assert.match(h, /aria-label="#412 진행"/);
  });

  test("시간 눈금은 추정의 출처를 밝힌다", () => {
    assert.equal(label(html(row(409), "en", false)), "70% · by measured time");
    assert.equal(label(html(timeRow({ source: "preset" }), "en", false)), "15% · by preset estimate");
    assert.equal(label(html(timeRow({ source: null }), "en", false)), "15% · by expected time");
    assert.equal(label(html(row(409), "ko", false)), "70% · 측정 소요 기준");
  });

  test("살아 있으면 1초 틱의 기준점과 출처를 단다", () => {
    const h = html(row(409), "en", true);
    assert.match(h, /data-tick="progress"/);
    assert.match(h, new RegExp('data-from="' + row(409).started_at + '"'));
    assert.match(h, /data-expected="540"/);
    assert.match(h, /data-source="measured"/);
  });

  test("시계 차이를 모르면 기준점을 달지 않는다 — 브라우저 시계로 넘어가지 않는다", () => {
    assert.doesNotMatch(html(row(409), "en", false), /data-tick/);
  });

  test("스텝 눈금은 틱을 달지 않는다 — 스텝은 마커가 올 때만 움직인다", () => {
    assert.doesNotMatch(html(row(412), "en", true), /data-tick/);
  });

  test("추정을 넘긴 잡 — 가득 찬 빗금, 퍼센트도 틱도 없다", () => {
    const h = html(timeRow({ elapsed_seconds: 400 }), "en", true);
    assert.match(h, /data-basis="none" data-cond="over"/);
    assert.equal(label(h), "past the estimate");
    assert.match(h, /data-fill="100"/);
    assert.doesNotMatch(h, /aria-valuenow/);
    assert.doesNotMatch(h, /data-tick/);
  });

  test("스텝을 다 끝낸 잡 — 100% 라고 쓰지 않고 「마무리 중」이라고 쓴다", () => {
    const r = row(412);
    r.progress.steps_done = 8;
    const h = html(r, "en", true);
    assert.match(h, /data-basis="steps" data-cond="finalizing"/);
    assert.equal(label(h), "8/8 steps · finalizing");
    assert.doesNotMatch(h, /aria-valuenow/);
    assert.doesNotMatch(label(h), /100%/);
  });

  test("stuck — 스텝 눈금은 남기고 형편을 함께 말한다", () => {
    const r = row(412);
    r.estimate.stuck = true;
    const h = html(r, "en", false);
    assert.match(h, /data-basis="steps" data-cond="stuck"/);
    assert.equal(label(h), "50% · 4/8 steps · likely stuck");
    assert.match(h, /aria-valuenow="50"/);
  });

  test("준비 중 · 근거 없음은 눈금 없는 막대다", () => {
    const r = row(412);
    r.progress = { timing: "as_received", phase: "materializing", steps: [], steps_total: null, steps_done: 0 };
    assert.equal(label(html(r, "en", true)), "preparing workspace");
    assert.match(html(r, "en", true), /data-basis="none" data-cond="preparing"/);

    const none = timeRow({ source: "default" });
    assert.equal(label(html(none, "en", true)), "progress —");
    assert.match(html(none, "en", true), /data-basis="none" data-cond="normal"/);
    assert.doesNotMatch(html(none, "en", true), /aria-valuenow/);
  });

  test("도는 잡이 아니면 빈 문자열 — 행 아래에 아무것도 안 붙는다", () => {
    assert.equal(html(row(413), "en", true), "");
    assert.equal(html(null, "en", true), "");
  });
});
