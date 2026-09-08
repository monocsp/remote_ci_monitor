"use strict";
// 상태 표기 — 색에 기대지 않는다 (M5d-3 · docs/m5d-workplan.md §4.2 · WCAG 1.4.1).
//
// 화면의 상태 필은 채널이 셋이다: 글자(`stateWord`) · 모양(`stateGlyph`) · 색(CSS).
// 색은 **마지막** 채널이다 — 색을 못 보거나 회색조로 인쇄해도 상태가 갈려야 한다(완료 기준 5).
// 여기서는 순수 함수 쪽만 잠근다. 글리프가 실제로 DOM 에 `aria-hidden` 으로 붙는지와
// 움직임(`prefers-reduced-motion`)은 브라우저 시험(`tests/test_web_layout.py`)이 본다.
//
// 잠그는 것:
// - 잡 상태 아홉 가지 전부에 **제 모양**이 있다 — `stateGlyph` 가 대체값 `·` 를 주지 않는다.
//   (`·` 는 「모르는 상태」 자리다. 아는 상태가 그 값을 받으면 모양 채널이 통째로 없는 것이다.)
// - 두 언어 모두에서 상태 글자가 비어 있지 않고, 영어 식별자가 한국어 화면에 새지 않는다.
// - 실행 중과 대기가 같은 모양을 쓰지 않는다 — 큐에서 늘 나란히 보이는 한 쌍이다(§4.2 채운 원/빈 원).

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const { load } = require("./helpers");
const rcm = load();

const FALLBACK_GLYPH = "·"; // 모르는 상태의 자리표시자
// 워커 상태와 `unknown` 은 잡 상태가 아니다 — 상태 필을 만들지 않는다.
const NOT_A_JOB_STATE = new Set(["unknown", "busy", "idle", "down"]);
// 카탈로그에서 뽑는다. 상태를 새로 들이면 글리프 없이 지나갈 수 없다.
const JOB_STATES = Object.keys(I18N.MESSAGES.en)
  .filter((k) => k.startsWith("state."))
  .map((k) => k.slice("state.".length))
  .filter((s) => !NOT_A_JOB_STATE.has(s));

test("§4.2 의 잡 상태 아홉 가지가 카탈로그에 있다", () => {
  assert.deepEqual(JOB_STATES.slice().sort(), [
    "cancelled", "cancelling", "failed", "lost", "queued",
    "running", "succeeded", "timed_out", "uploading"
  ]);
});

test("잡 상태마다 제 모양이 있다 — 색 말고 채널이 하나 더 (WCAG 1.4.1)", () => {
  JOB_STATES.forEach((state) => {
    const g = rcm.stateGlyph(state);
    assert.equal(typeof g, "string", state);
    assert.ok(g.length > 0, `${state}: 글리프가 빈 문자열이다`);
    assert.notEqual(
      g,
      FALLBACK_GLYPH,
      `${state}: 모르는 상태의 대체값 \`${FALLBACK_GLYPH}\` 을 그대로 쓴다 — 색을 빼면 구분이 없다`
    );
  });
});

test("실행 중과 대기가 같은 모양을 쓰지 않는다 (§4.2 채운 원 / 빈 원)", () => {
  // 큐에서 늘 나란히 서는 한 쌍이다. 회색조로 인쇄해도 갈려야 한다.
  assert.notEqual(rcm.stateGlyph("running"), rcm.stateGlyph("queued"));
});

test("모르는 상태에는 대체 모양이 있다 — 빈 칸을 그리지 않는다", () => {
  [null, undefined, "", "brand_new_state"].forEach((state) => {
    const g = rcm.stateGlyph(state);
    assert.equal(typeof g, "string", String(state));
    assert.ok(g.length > 0, `${String(state)}: 글리프가 비었다`);
  });
});

test("두 언어에서 상태 글자가 비어 있지 않다", () => {
  I18N.LANGS.forEach((lang) => {
    JOB_STATES.forEach((state) => {
      const w = rcm.stateWord(state, lang);
      assert.equal(typeof w, "string", `${lang}/${state}`);
      assert.ok(w.trim().length > 0, `${lang}/${state}: 글자가 없다 — 색·모양만 남는다`);
      assert.ok(!/undefined|NaN/.test(w), `${lang}/${state}: ${w}`);
      assert.notEqual(w, "—", `${lang}/${state}: 상태를 모른다고 그리면 안 된다`);
    });
    // 상태를 모를 때도 빈 칸이 아니다
    assert.ok(rcm.stateWord(null, lang).trim().length > 0, lang);
  });
});

test("한국어 화면에 영어 식별자가 새지 않는다", () => {
  // `stateWord` 는 카탈로그에 키가 없으면 상태 이름을 그대로 돌려준다 — 그 구멍을 막는다.
  JOB_STATES.forEach((state) => {
    assert.notEqual(
      rcm.stateWord(state, "ko"),
      state,
      `state.${state} 의 한국어가 없어 식별자가 그대로 그려진다`
    );
  });
});

test("모양과 글자는 서로 다른 채널이다 — 하나가 다른 하나를 대신하지 않는다", () => {
  JOB_STATES.forEach((state) => {
    I18N.LANGS.forEach((lang) => {
      assert.notEqual(rcm.stateGlyph(state), rcm.stateWord(state, lang), `${lang}/${state}`);
    });
  });
});
