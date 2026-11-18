"use strict";
// 웹 UI(M5o B) — 실패 요약이 이름을 말한다 (`docs/m5o-workplan.md` §3.1 · 결정 117·118).
// 시나리오는 `docs/m5o-test-scenarios-b.md` 의 W1~W4.
//
// 잠그는 것:
// - 영어 화면은 서버가 저장한 문장과 **글자까지 같다**(결정 37) — 인자는 서버가 보낸 원시 값.
// - 한국어 화면은 같은 인자로 자기 말을 만든다. 이름(파일 · 스텝)은 번역하지 않는다.
// - `timed_out` 은 서버가 보내는 `seconds` 로 그린다. 옛 화면은 서버가 안 보내는 `a.limit` 을 읽어
//   `undefined` 를 찍었다.
// - 최근 행은 요약이 이미 스텝을 말하면 ` · step test` 를 다시 붙이지 않는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { load, TZ, NOW } = require("./helpers");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const rcm = load();
const LANGS = I18N.LANGS;

// 서버 `core/outcome.py` 가 같은 인자로 저장하는 문장(tests/test_failure_summaries_m5o.py 와 짝)
const EXIT_EN = [
  [{ code: 1 }, "exit 1"],
  [{ code: 1, step: "analyze" }, "analyze failed (exit 1)"],
  [{ code: 1, step: "test", first: "x_test.dart" }, "test: x_test.dart (exit 1)"],
  [{ code: 1, step: "test", first: "x_test.dart", more: 1 }, "test: x_test.dart +1 more (exit 1)"],
  [{ code: 1, first: "x_test.dart", more: 1 }, "x_test.dart +1 more (exit 1)"],
  [{ code: 1, step: "test", first: "u0_test.dart", more: 99, more_truncated: true },
    "test: u0_test.dart +99+ more (exit 1)"]
];

const EXIT_KO = [
  [{ code: 1 }, "종료 코드 1"],
  [{ code: 1, step: "analyze" }, "analyze 실패 (종료 코드 1)"],
  [{ code: 1, step: "test", first: "x_test.dart", more: 1 }, "test: x_test.dart 외 1개 (종료 코드 1)"],
  [{ code: 1, step: "test", first: "u0_test.dart", more: 99, more_truncated: true },
    "test: u0_test.dart 외 99개 이상 (종료 코드 1)"]
];

describe("outcome.exit_code — 이름을 먼저 (W1 · W2)", () => {
  test("영어는 서버가 저장한 문장과 글자까지 같다", () => {
    EXIT_EN.forEach(([args, want]) => assert.equal(I18N.t("en", "outcome.exit_code", args), want));
  });

  test("한국어는 자기 말로, 이름은 그대로", () => {
    EXIT_KO.forEach(([args, want]) => assert.equal(I18N.t("ko", "outcome.exit_code", args), want));
  });
});

describe("outcome.timed_out — 서버가 보내는 seconds 로 (W3)", () => {
  test("영어는 서버 문장과 같다", () => {
    assert.equal(I18N.t("en", "outcome.timed_out", { seconds: 2400 }), "limit 40m");
    assert.equal(I18N.t("en", "outcome.timed_out", { seconds: 60, last_step: "test" }),
      "limit 1m (in test)");
    assert.equal(I18N.t("en", "outcome.timed_out", { seconds: 3600 }), "limit 1h");
    assert.equal(I18N.t("en", "outcome.timed_out", { seconds: 90 }), "limit 90s");
    assert.equal(I18N.t("en", "outcome.timed_out", {}), "limit");
  });

  test("두 언어 모두 undefined 를 찍지 않는다", () => {
    LANGS.forEach((lang) => {
      const out = I18N.t(lang, "outcome.timed_out", { seconds: 2400, last_step: "test" });
      assert.ok(!/undefined|NaN/.test(out), lang + ": " + out);
      assert.ok(out.includes("test") && out.includes("40m"), lang + ": " + out);
    });
  });
});

describe("recentLine — 요약이 이미 스텝을 말하면 다시 안 붙인다 (W4)", () => {
  function recent(patch) {
    return Object.assign({
      id: 3760, state: "failed", exit_code: 1, key: "gate",
      summary: "test: x_test.dart +1 more (exit 1)", summary_code: "exit_code",
      summary_args: { code: 1, step: "test", first: "x_test.dart", more: 1 },
      job_seconds: 660, finished_at: "2026-11-18T00:50:00Z",
      requester: { name: "macbook", label: "macbook@PCS" },
      failed_step: "test", last_step: "test"
    }, patch || {});
  }

  test("스텝이 요약에 있으면 꼬리 라벨이 없다", () => {
    assert.equal(rcm.recentLine(recent(), TZ, NOW, "en").summary, "test: x_test.dart +1 more (exit 1)");
    assert.equal(rcm.recentLine(recent(), TZ, NOW, "ko").summary,
      "test: x_test.dart 외 1개 (종료 코드 1)");
  });

  test("옛 잡(인자에 스텝 없음)은 오늘처럼 꼬리를 붙인다", () => {
    const old = recent({ summary: "exit 1", summary_args: { code: 1 } });
    assert.equal(rcm.recentLine(old, TZ, NOW, "en").summary, "exit 1 · step test");
  });

  test("타임아웃이 마지막 스텝을 말하면 「마지막 단계」를 다시 안 붙인다", () => {
    const j = recent({ state: "timed_out", exit_code: -9, failed_step: null, last_step: "test",
      summary: "limit 1m (in test)", summary_code: "timed_out",
      summary_args: { seconds: 60, last_step: "test" } });
    assert.equal(rcm.recentLine(j, TZ, NOW, "en").summary, "limit 1m (in test)");
  });

  test("타임아웃인데 선언된 실패 스텝이 따로 있으면 그것은 붙인다", () => {
    const j = recent({ state: "timed_out", exit_code: -9, failed_step: "test", last_step: "build web",
      summary: "limit 1m (in build web)", summary_code: "timed_out",
      summary_args: { seconds: 60, last_step: "build web" } });
    assert.equal(rcm.recentLine(j, TZ, NOW, "en").summary, "limit 1m (in build web) · step test");
  });
});

describe("recentLine — 꼬리 비교는 씻은 이름끼리 (검증 라운드 1)", () => {
  // 서버 인자 `step` 은 씻어서 80자로 자른 값이다(core/outcome._clean_label). 행의 failed_step 은
  // 원문(120자)이라 그대로 견주면 같은 스텝이 두 번 찍힌다.
  [
    ["s".repeat(120), "s".repeat(80)],
    ["te​st", "test"],
    ["te\u0085st", "test"],
    ["  pad  ", "pad"]
  ].forEach(([raw, cleaned]) => {
    test(JSON.stringify(raw).slice(0, 30), () => {
      const j = {
        id: 1, state: "failed", exit_code: 1, key: "gate", failed_step: raw, last_step: raw,
        summary: cleaned + " failed (exit 1)", summary_code: "exit_code",
        summary_args: { code: 1, step: cleaned },
        job_seconds: 1, finished_at: "2026-11-18T00:50:00Z", requester: { name: "m", label: "m" }
      };
      assert.equal(rcm.recentLine(j, TZ, NOW, "en").summary, cleaned + " failed (exit 1)");
    });
  });

  test("80자 자르기는 코드 포인트 단위다 — 서버(파이썬)와 같다", () => {
    const raw = "😀".repeat(90);
    const j = {
      id: 1, state: "failed", exit_code: 1, key: "gate", failed_step: raw,
      summary: "x", summary_code: "exit_code",
      summary_args: { code: 1, step: "😀".repeat(80) },
      job_seconds: 1, finished_at: "2026-11-18T00:50:00Z", requester: { name: "m", label: "m" }
    };
    assert.ok(!rcm.recentLine(j, TZ, NOW, "en").summary.includes("step "));
  });
});
