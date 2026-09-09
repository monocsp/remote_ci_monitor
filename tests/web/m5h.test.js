"use strict";
// 웹 UI(M5h) — 최근 행의 「실패한 스텝」과 「마지막 스텝」 갈림(명세 §1.5) ·
// 이름별 실패 배지의 문구(§2.6). 구현보다 먼저 썼다(test-first).
//
// 잠그는 것:
// - 요약 줄은 **선언된** `failed_step` 이면 「스텝 X」, 없고 `last_step` 이면 「마지막 스텝 X」,
//   둘 다 없으면 요약뿐이다. 두 언어 모두에서 같은 갈래를 탄다.
// - 스텝 이름은 잡이 찍은 식별자다 — 어느 언어에서도 번역하지 않는다.
// - `failures.*` 여섯 키가 두 언어에 있고, 영어의 `intermittent?` 물음표는 계약이다.
// - 최근 **상세**(`rdetail`)는 DOM 층이라 여기서 부를 수 없다(브라우저 시험은
//   `tests/test_web_browser.py` 의 몫이다). 그래서 갈래가 실제로 배선됐는지는 app.js 원문에서
//   확인한다 — 카탈로그에만 키를 넣고 화면이 안 읽는 상태를 잡는 잠금이다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { load, APP_PATH, TZ, NOW } = require("./helpers");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const rcm = load();
const LANGS = I18N.LANGS;

// 최근 행 하나(#162 를 본뜬 것). 스텝 칸만 시나리오마다 바꾼다.
function recent(patch) {
  return Object.assign({
    id: 162, state: "failed", exit_code: 1, key: "gate", summary: "exit 1",
    job_seconds: 660, finished_at: "2026-09-04T00:50:00Z",
    requester: { name: "macbook", label: "macbook@PCS" },
    failed_step: null, last_step: null
  }, patch || {});
}

const STEP = { en: "step test", ko: "스텝 test" };
const LAST = { en: "last step build web", ko: "마지막 스텝 build web" };

describe("recentLine — 선언된 스텝과 마지막 스텝 (§1.5)", () => {
  test("선언된 failed_step 은 오늘 그대로 「스텝 X」", () => {
    LANGS.forEach((lang) => {
      const l = rcm.recentLine(recent({ failed_step: "test" }), TZ, NOW, lang);
      assert.equal(l.summary, "exit 1 · " + STEP[lang], lang);
    });
  });

  test("last_step 뿐이면 「마지막 스텝 X」 — 인과를 주장하지 않는다", () => {
    LANGS.forEach((lang) => {
      const l = rcm.recentLine(recent({ last_step: "build web" }), TZ, NOW, lang);
      assert.equal(l.summary, "exit 1 · " + LAST[lang], lang);
      assert.ok(!l.summary.includes(STEP[lang]), lang + ": 「스텝」이라고 부르면 안 된다");
    });
  });

  test("둘 다 없으면 요약뿐이다 — 괄호도 라벨도 없다", () => {
    LANGS.forEach((lang) => {
      const l = rcm.recentLine(recent(), TZ, NOW, lang);
      assert.equal(l.summary, "exit 1", lang);
    });
  });

  test("둘 다 있으면 선언이 이긴다", () => {
    LANGS.forEach((lang) => {
      const j = recent({ failed_step: "test", last_step: "build web" });
      const l = rcm.recentLine(j, TZ, NOW, lang);
      assert.equal(l.summary, "exit 1 · " + STEP[lang], lang);
      assert.ok(!l.summary.includes(LAST[lang]), lang);
    });
  });

  test("취소된 잡은 두 칸이 다 비어 있어 스텝 라벨이 없다 (#176 · 결정 64)", () => {
    const j = recent({ state: "cancelled", exit_code: null, summary: "cancelled by macbook",
      started_at: "2026-09-04T00:39:00Z" });
    LANGS.forEach((lang) => {
      const l = rcm.recentLine(j, TZ, NOW, lang);
      assert.equal(l.summary, "cancelled by macbook", lang);
    });
  });
});

describe("문자열 카탈로그 (§1.5 · §2.6)", () => {
  test("스텝 두 갈래의 문구가 두 언어에 있다", () => {
    assert.equal(I18N.t("en", "recent.step", { step: "test" }), "step test");
    assert.equal(I18N.t("ko", "recent.step", { step: "test" }), "스텝 test");
    assert.equal(I18N.t("en", "recent.last_step", { step: "build web" }), "last step build web");
    assert.equal(I18N.t("ko", "recent.last_step", { step: "build web" }), "마지막 스텝 build web");
    assert.equal(I18N.t("en", "recent.failed_step"), "failed step: ");
    assert.equal(I18N.t("ko", "recent.failed_step"), "실패한 스텝: ");
    assert.equal(I18N.t("en", "recent.last_step_label"), "last step: ");
    assert.equal(I18N.t("ko", "recent.last_step_label"), "마지막 스텝: ");
  });

  test("이름별 실패 배지의 여섯 문구가 두 언어에 있다", () => {
    assert.equal(I18N.t("en", "failures.title"), "failed by name");
    assert.equal(I18N.t("ko", "failures.title"), "이름별 실패");
    assert.equal(I18N.t("en", "failures.persistent", { window: 8 }),
      "every one of the last 8 runs");
    assert.equal(I18N.t("ko", "failures.persistent", { window: 8 }), "최근 8회 전부");
    assert.equal(I18N.t("en", "failures.intermittent", { seen: 3, window: 8 }),
      "3 of the last 8 runs · intermittent?");
    assert.equal(I18N.t("ko", "failures.intermittent", { seen: 3, window: 8 }),
      "최근 8회 중 3회 · 간헐?");
    assert.equal(I18N.t("en", "failures.first_seen", { window: 8 }),
      "first time in the last 8 runs");
    assert.equal(I18N.t("ko", "failures.first_seen", { window: 8 }), "최근 8회 중 처음");
    assert.equal(I18N.t("en", "failures.unknown", { seen: 1, window: 2 }), "1 of 2 runs so far");
    assert.equal(I18N.t("ko", "failures.unknown", { seen: 1, window: 2 }), "아직 2회 중 1회");
    assert.equal(I18N.t("en", "failures.unnamed", { n: 2 }),
      "2 of those runs failed without naming anything");
    assert.equal(I18N.t("ko", "failures.unnamed", { n: 2 }), "그 중 2회는 이름 없이 실패했다");
  });

  test("판정 이름(verdict)은 번역하지 않는다 — 문구만 언어를 탄다", () => {
    // 서버가 보내는 것은 코드다(결정 37). 카탈로그의 키가 그 코드와 1:1 이어야 한다.
    ["persistent", "intermittent", "first_seen", "unknown"].forEach((verdict) => {
      LANGS.forEach((lang) => {
        const out = I18N.t(lang, "failures." + verdict, { seen: 1, window: 8 });
        assert.equal(typeof out, "string");
        assert.ok(out.length > 0 && !/undefined|NaN/.test(out), lang + "/" + verdict + ": " + out);
      });
    });
  });
});

describe("최근 상세의 배선 (§1.5 · app.js 원문)", () => {
  // assert.match 은 실패할 때 app.js 전문을 쏟는다 — 원문은 크므로 boolean 으로만 본다.
  test("상세가 「마지막 스텝」 갈래를 실제로 읽는다", () => {
    const src = fs.readFileSync(APP_PATH, "utf8");
    assert.ok(src.includes("recent.last_step_label"), "상세에 「마지막 스텝」 라벨이 없다");
    assert.ok(/job\.last_step\b/.test(src), "상세가 job.last_step 을 안 읽는다");
    // 두 갈래가 한자리에 붙어 있어야 한다 — 라벨만 카탈로그에 넣고 안 쓰는 상태를 잡는다
    const together =
      /last_step_label[\s\S]{0,120}job\.last_step|job\.last_step[\s\S]{0,120}last_step_label/;
    assert.ok(together.test(src), "「마지막 스텝」 라벨과 그 값이 같은 갈래에 있지 않다");
  });
});
