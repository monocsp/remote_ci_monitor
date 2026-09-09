"use strict";
// 전체 진행 막대(2026-09-09 오너 요청) — `rcm.overallProgress` · `rcm.progressBarHtml`.
//
// 잠그는 것:
// - 눈금의 **근거**를 함께 돌려준다. 스텝 총계를 잡이 선언했으면 스텝으로, 아니면 추정 소요
//   대비 경과로, 둘 다 없으면 `unknown` — 0% 로 그리지 않는다(fail-open 금지).
// - `steps_total_partial`(총계를 모르고 세는 중)은 스텝 눈금의 근거가 아니다. 「5/8 (so far)」의
//   8 은 지금까지 본 수라 62% 라고 쓰면 자신있는 거짓말이 된다.
// - 추정을 넘긴 잡·stuck 잡에는 퍼센트를 주지 않는다. ETA 칸이 `—` 인 것과 같은 이유다.
// - 도는 잡이 아니면 막대 자체가 없다(queued 에 0/0 금지와 같은 규칙).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job } = require("./helpers");

const rcm = load();

const status = fixture("main");
const row = (id) => structuredClone(job(status, id));

describe("overallProgress", () => {
  test("스텝 총계를 선언한 잡 → 끝난 스텝 비율", () => {
    // #412 는 steps_total 8 · steps_done 4 · partial 아님
    assert.deepEqual(rcm.overallProgress(row(412)), {
      kind: "steps", pct: 50, done: 4, total: 8, expected: null, startedAt: null
    });
  });

  test("총계가 「지금까지」면 스텝으로 세지 않고 시간으로 센다", () => {
    // #409 는 steps_total 3 이지만 partial — 경과 380 / 추정 540 = 70%
    const p = rcm.overallProgress(row(409));
    assert.equal(p.kind, "time");
    assert.equal(p.pct, 70);
    assert.equal(p.expected, 540);
    assert.equal(p.startedAt, row(409).started_at);
    assert.equal(p.done, null);
  });

  test("마커가 없어도 추정이 있으면 시간으로 센다", () => {
    const r = row(412);
    r.progress = Object.assign(r.progress, {
      steps: [], steps_total: null, steps_total_partial: false, steps_done: 0,
      current_index: null, current_name: null, current_seconds: null
    });
    const p = rcm.overallProgress(r);
    assert.equal(p.kind, "time");
    assert.equal(p.pct, 16);  // 59 / 369
  });

  test("추정을 넘긴 잡은 퍼센트를 주지 않는다 — 남은 양을 모른다", () => {
    const r = row(412);
    r.progress.steps_total = null;
    r.estimate.elapsed_seconds = 500;
    r.estimate.overdue = true;
    assert.deepEqual(rcm.overallProgress(r), {
      kind: "over", pct: null, done: null, total: null, expected: 369, startedAt: null
    });
  });

  test("`overdue` 플래그가 없어도 경과가 추정을 넘겼으면 over 다", () => {
    const r = row(412);
    r.progress.steps_total = null;
    r.estimate.elapsed_seconds = 400;
    assert.equal(rcm.overallProgress(r).kind, "over");
  });

  test("stuck 잡은 시간 눈금을 주지 않는다 — 도는 중인지부터 모른다", () => {
    const r = row(412);
    r.progress.steps_total = null;
    r.estimate.stuck = true;
    assert.equal(rcm.overallProgress(r).kind, "unknown");
  });

  test("스텝을 선언한 잡은 stuck 이어도 스텝 눈금을 지킨다 — 끝난 스텝은 사실이다", () => {
    const r = row(412);
    r.estimate.stuck = true;
    r.estimate.overdue = true;
    const p = rcm.overallProgress(r);
    assert.equal(p.kind, "steps");
    assert.equal(p.pct, 50);
  });

  test("워크스페이스 준비 중이면 눈금이 없다", () => {
    const r = row(412);
    r.progress = { timing: "as_received", phase: "materializing", steps: [], steps_total: null, steps_done: 0 };
    assert.equal(rcm.overallProgress(r).kind, "unknown");
  });

  test("근거가 하나도 없으면 unknown — 0% 가 아니다", () => {
    const r = row(412);
    r.progress = null;
    r.estimate = { expected_seconds: null, elapsed_seconds: null };
    assert.deepEqual(rcm.overallProgress(r), {
      kind: "unknown", pct: null, done: null, total: null, expected: null, startedAt: null
    });
  });

  test("스텝 총계 0 은 나눗셈 근거가 아니다", () => {
    const r = row(412);
    r.progress.steps_total = 0;
    r.progress.steps_done = 0;
    assert.equal(rcm.overallProgress(r).kind, "time");
  });

  test("서버가 총계보다 큰 done 을 보내도 100% 를 넘지 않는다", () => {
    const r = row(412);
    r.progress.steps_done = 12;
    assert.equal(rcm.overallProgress(r).pct, 100);
  });

  test("취소 중인 잡도 도는 잡이다 — 막대를 지우지 않는다", () => {
    const r = row(412);
    r.state = "cancelling";
    assert.equal(rcm.overallProgress(r).kind, "steps");
  });

  test("대기·업로드·없는 행은 막대가 없다(null)", () => {
    assert.equal(rcm.overallProgress(row(413)), null);
    assert.equal(rcm.overallProgress(row(414)), null);
    assert.equal(rcm.overallProgress(row(415)), null);
    assert.equal(rcm.overallProgress(null), null);
    assert.equal(rcm.overallProgress({ state: "succeeded", progress: { steps_total: 8, steps_done: 8 } }), null);
  });
});

describe("progressBarHtml", () => {
  const html = (r, lang, live) => rcm.progressBarHtml(r, lang, live);

  test("스텝 눈금 — 폭·퍼센트·근거가 한 줄에 다 있다", () => {
    const h = html(row(412), "en", false);
    assert.match(h, /class="pbar steps"/);
    assert.match(h, /style="width:50%"/);
    assert.match(h, /50% · 4\/8 steps/);
    assert.match(h, /role="progressbar"/);
    assert.match(h, /aria-valuenow="50"/);
    assert.match(h, /aria-valuemax="100"/);
    assert.match(h, /aria-label="#412 overall progress"/);
  });

  test("한국어도 같은 눈금 — 숫자는 그대로, 문구만 그 언어", () => {
    const h = html(row(412), "ko", false);
    assert.match(h, /50% · 스텝 4\/8/);
    assert.match(h, /aria-label="#412 전체 진행률"/);
  });

  test("시간 눈금이고 살아 있으면 1초 틱의 기준점을 단다", () => {
    const h = html(row(409), "en", true);
    assert.match(h, /data-tick="progress"/);
    assert.match(h, new RegExp('data-from="' + row(409).started_at + '"'));
    assert.match(h, /data-expected="540"/);
    assert.match(h, /70% · by expected time/);
  });

  test("시계 차이를 모르면 기준점을 달지 않는다 — 브라우저 시계로 넘어가지 않는다", () => {
    assert.doesNotMatch(html(row(409), "en", false), /data-tick/);
  });

  test("스텝 눈금은 틱을 달지 않는다 — 스텝은 마커가 올 때만 움직인다", () => {
    assert.doesNotMatch(html(row(412), "en", true), /data-tick/);
  });

  test("추정을 넘긴 잡 — 막대는 가득이지만 퍼센트는 없다", () => {
    const r = row(412);
    r.progress.steps_total = null;
    r.estimate.overdue = true;
    const h = html(r, "en", true);
    assert.match(h, /class="pbar over"/);
    assert.match(h, /past the estimate/);
    assert.doesNotMatch(h, /aria-valuenow/);
    assert.doesNotMatch(h, /data-tick/);
  });

  test("근거가 없으면 눈금 없는 막대와 「—」 — 0% 로 그리지 않는다", () => {
    const r = row(412);
    r.progress = null;
    r.estimate = {};
    const h = html(r, "en", true);
    assert.match(h, /class="pbar unknown"/);
    assert.match(h, /progress —/);
    assert.doesNotMatch(h, /aria-valuenow/);
  });

  test("도는 잡이 아니면 빈 문자열 — 행 아래에 아무것도 안 붙는다", () => {
    assert.equal(html(row(413), "en", true), "");
    assert.equal(html(null, "en", true), "");
  });

  test("잡이 찍은 스텝 이름·근거 문구를 escape 한다", () => {
    const r = row(412);
    r.id = 412;
    r.progress.steps_total = 8;
    const h = html(r, "en", false);
    assert.doesNotMatch(h, /<script/);
  });
});
