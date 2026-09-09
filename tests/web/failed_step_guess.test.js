"use strict";
// 추측한 실패 스텝은 화면에서도 추측으로 보인다 (2026-09-08 운영 사고 후속).
//
// 게이트가 스텝을 병렬로 돌린 뒤 마커를 몰아 찍고 `::rcm::step-end::fail` 은 안 찍어서, 서버의
// 「종료 코드가 0 이 아니면 마지막 스텝」 폴백이 **성공한** 스텝(`build web`)을 범인으로 지목했다.
// 서버가 `failed_step_guessed` 로 그게 짐작임을 말해 주므로, 화면은 확정된 실패와 다르게 쓴다.
//
// 이 값이 실제로 보이는 곳은 **최근 완료**다 — 도는 잡은 종료 코드가 없어 추측이 생기지 않고,
// 끝난 잡은 큐 행(스텝 타임라인)이 아니라 recent 행으로 내려온다. 그래서 여기서 잠그는 것도
// recent 줄과 그 서랍뿐이다.
//
// 잠그는 것:
// - 확정된 실패의 표시는 **지금 그대로**다(회귀 금지).
// - 추측이면 이름은 대되 추측이라고 쓴다 — 두 언어 모두.
// - 키가 없거나 null 이면(옛 서버 · v9 앞에 끝난 잡) 아무 말도 덧붙이지 않는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();

function recent(guessed) {
  return {
    id: 143, state: "failed", exit_code: 1, job_seconds: 300,
    summary: "exit 1", summary_code: "exit_code", summary_args: { code: 1 },
    failed_step: guessed ? "build web" : "test",
    failed_step_guessed: guessed,
    finished_at: "2026-09-08T01:02:03Z"
  };
}

describe("recentLine", () => {
  test("a certain failure reads exactly as it does today", () => {
    ["en", "ko"].forEach((lang) => {
      const line = rcm.recentLine(recent(false), "UTC", Date.now(), lang);
      assert.ok(line.summary.includes("test"), line.summary);
      assert.ok(!/guessed|추측/.test(line.summary), lang + ": " + line.summary);
    });
  });

  test("a guessed failure names the step and says it is a guess", () => {
    const en = rcm.recentLine(recent(true), "UTC", Date.now(), "en").summary;
    assert.ok(en.includes("build web"), en);
    assert.ok(en.includes("guessed"), en);
    const ko = rcm.recentLine(recent(true), "UTC", Date.now(), "ko").summary;
    assert.ok(ko.includes("build web"), ko);
    assert.ok(ko.includes("추측"), ko);
  });

  test("an old job that never said stays silent — not certain, not a guess", () => {
    const old = recent(true);
    delete old.failed_step_guessed;
    const line = rcm.recentLine(old, "UTC", Date.now(), "en").summary;
    assert.ok(line.includes("build web"), line);
    assert.ok(!line.includes("guessed"), line);
    const nulled = Object.assign(recent(true), { failed_step_guessed: null });
    assert.ok(!rcm.recentLine(nulled, "UTC", Date.now(), "en").summary.includes("guessed"));
  });
});
