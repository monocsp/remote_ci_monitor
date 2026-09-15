"use strict";
// 조사 헬퍼 (§2.3 · 시나리오 C-27 ~ C-35).
//
// 화면에서 「이(가)」 같은 괄호 표기를 없애는 것이 목적이다. 괄호를 없애려면 **틀린 조사를 쓰지
// 않는다**가 아니라 **늘 하나를 고른다**가 규칙이어야 한다 — 모르는 글자는 받침 없는 쪽으로
// 떨어지고, 절대 괄호로 되돌아가지 않는다.
//
// 잠그는 것:
// - 한글 · 숫자 열 개 · 라틴 52자 전부의 판정. 라틴에서 받침 있는 것은 `l m n r` **넷뿐**이다.
// - 네 쌍(이/가 · 을/를 · 은/는 · 와/과)이 **같은 판정**을 쓴다.
// - 끝의 부호는 건너뛰고, 건너뛰기 집합 **밖의** 글자에서는 멈춘다(`@`).
// - `null` · `undefined` · `""` 가 던지지 않고, 화면에 「null이」를 찍지 않는다.
// - 모르는 쌍은 **던진다** — 조용히 빈 문자열을 주면 문장에서 조사가 사라진 채로 배포된다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const { hasFinalConsonant, josa, withJosa } = I18N;

// 받침 있음 → 이/을/은/과, 없음 → 가/를/는/와.
const WITH = { "이/가": "이", "을/를": "을", "은/는": "은", "와/과": "과" };
const WITHOUT = { "이/가": "가", "을/를": "를", "은/는": "는", "와/과": "와" };
const PAIRS = Object.keys(WITH);

/** 네 쌍을 모두 돌려 `final`(받침 유무)이 고른 글자가 붙는지 본다. */
function assertAllPairs(word, final, label) {
  const table = final ? WITH : WITHOUT;
  PAIRS.forEach((pair) => {
    assert.equal(withJosa(word, pair), String(word) + table[pair], `${label} / ${pair}`);
    assert.equal(josa(word, pair), table[pair], `${label} / ${pair}`);
  });
}

describe("한글 음절 (C-27)", () => {
  const CASES = [
    ["가", false],   // U+AC00, (code - 0xAC00) % 28 === 0
    ["각", true],    // U+AC01
    ["갈", true],    // U+AC08 — ㄹ받침도 이 네 쌍에서는 다른 받침과 똑같다
    ["사람", true],
    ["워커", false],
    ["단계", false],
    ["힣", true]     // U+D7A3 — 범위의 끝
  ];
  CASES.forEach(([word, final]) => {
    test(`${word} → 받침 ${final ? "있음" : "없음"}`, () => {
      assert.equal(hasFinalConsonant(word), final, word);
      assertAllPairs(word, final, word);
    });
  });

  test("한글 범위 밖의 이웃 코드포인트는 한글로 읽지 않는다", () => {
    assert.equal(hasFinalConsonant(String.fromCharCode(0xABFF)), null);
    assert.equal(hasFinalConsonant(String.fromCharCode(0xD7A4)), null);
  });
});

describe("숫자 열 개 (C-28)", () => {
  // 읽는 소리로 판정한다: 0 영 · 1 일 · 3 삼 · 6 육 · 7 칠 · 8 팔 은 받침이 있고
  // 2 이 · 4 사 · 5 오 · 9 구 는 없다.
  const FINAL = { 0: true, 1: true, 2: false, 3: true, 4: false, 5: false, 6: true, 7: true, 8: true, 9: false };
  Object.keys(FINAL).forEach((d) => {
    test(`"${d}" → 받침 ${FINAL[d] ? "있음" : "없음"}`, () => {
      assert.equal(hasFinalConsonant(d), FINAL[d], d);
      assertAllPairs(d, FINAL[d], d);
    });
  });

  test("숫자로 끝나는 실제 값", () => {
    assert.equal(withJosa("build-12", "이/가"), "build-12가");
    assert.equal(withJosa("build-13", "이/가"), "build-13이");
  });
});

describe("라틴 52자 (C-29)", () => {
  // 글자 이름의 읽는 소리로 판정한다. 받침이 있는 것은 `l`(엘) · `m`(엠) · `n`(엔) · `r`(알)
  // **넷뿐**이다. `f` 는 「에프」의 끝 글자 `프` 에 받침이 없어 **받침 없는 쪽**이다.
  const FINAL = new Set(["l", "m", "n", "r"]);

  test("받침 있는 것은 정확히 네 글자다", () => {
    const found = [];
    for (let i = 0; i < 26; i++) {
      const ch = String.fromCharCode(97 + i);
      if (hasFinalConsonant(ch)) found.push(ch);
    }
    assert.deepEqual(found, ["l", "m", "n", "r"]);
  });

  test("26자 × 대소문자 = 52개가 같은 판정을 쓴다", () => {
    for (let i = 0; i < 26; i++) {
      const low = String.fromCharCode(97 + i);
      const up = String.fromCharCode(65 + i);
      const final = FINAL.has(low);
      assert.equal(hasFinalConsonant(low), final, low);
      assert.equal(hasFinalConsonant(up), final, up);
      assertAllPairs(low, final, low);
      assertAllPairs(up, final, up);
    }
  });

  test("`f` 는 받침 없는 쪽이다 — 「에프」의 끝 글자 `프` 에 받침이 없다", () => {
    assert.equal(hasFinalConsonant("f"), false);
    assert.equal(withJosa("perf", "이/가"), "perf가");
    assert.equal(withJosa("mail", "이/가"), "mail이");
  });
});

describe("실제로 화면에 오는 값 (C-30)", () => {
  const CASES = [
    ["mac2", "mac2가"],
    ["PCS-MACBOOK-PRO", "PCS-MACBOOK-PRO가"],
    ["web@studio", "web@studio가"],
    ["ci@mac2", "ci@mac2가"]
  ];
  CASES.forEach(([word, expected]) => {
    test(`${word} → ${expected}`, () => {
      assert.equal(withJosa(word, "이/가"), expected);
      assert.equal(withJosa(word, "은/는"), word + "는");
      assert.equal(withJosa(word, "을/를"), word + "를");
      assert.equal(withJosa(word, "와/과"), word + "와");
    });
  });
});

describe("끝의 부호는 건너뛴다 (C-31)", () => {
  // 건너뛰는 집합은 공백 · 문장 부호 · 괄호 · 따옴표 · 하이픈이다. 그 **밖의** 글자를 만나면
  // 거기서 멈춘다 — `@` 가 그렇다. 결과가 우연히 같더라도 규칙은 적어 둔다.
  const CASES = [
    ["alice (관리자)", "alice (관리자)가", false],
    ["alice)", "alice)가", false],
    ['"mac2"', '"mac2"가', false],
    ["mac2.", "mac2.가", false],
    ["mac2 ", "mac2 가", false],
    ["mac2…", "mac2…가", false],
    ["...", "...가", null],
    ["web@", "web@가", null],
    ["'삼'", "'삼'이", true],
    ["[build-3]", "[build-3]이", true]
  ];
  CASES.forEach(([word, expected, final]) => {
    test(JSON.stringify(word) + " → " + expected, () => {
      assert.equal(hasFinalConsonant(word), final, word);
      assert.equal(withJosa(word, "이/가"), expected);
    });
  });
});

describe("빈 값은 이름을 안 찍는다 (C-32)", () => {
  test("`null` · `undefined` · 빈 문자열은 던지지 않고 `null` 이다", () => {
    [null, undefined, ""].forEach((v) => {
      assert.equal(hasFinalConsonant(v), null, String(v));
    });
  });

  test("`withJosa(null)` 이 「null이」를 찍지 않는다 — 조사만 남는다", () => {
    // `String(null)` 은 "null" 이고 끝 글자 `l` 은 받침 있음이다. 글자 그대로 이었으면
    // 화면에 「null이」가 찍힌다 — 집안 규칙은 모르는 값이 `—` 이지 `null` 문자열이 아니다.
    assert.equal(withJosa(null, "이/가"), "가");
    assert.equal(withJosa(undefined, "이/가"), "가");
    assert.equal(withJosa("", "이/가"), "가");
    assert.equal(josa(null, "이/가"), "가");
    PAIRS.forEach((pair) => {
      assert.equal(withJosa(null, pair), WITHOUT[pair], pair);
    });
  });

  test("조사를 쓰는 일곱 문자열에 `null` · `undefined` 가 새지 않는다", () => {
    const args = { by: null, who: undefined, name: undefined, clock: "09:57", kill: "in 8s",
      job: "#409", group: "devices", seconds: 61, id: 412, key: "gate:full" };
    [
      "header.paused", "reason.blocked_by", "reason.sigterm", "recent.by",
      "outcome.cancelled_by", "outcome.worker_restarted_without_job", "outcome.worker_unreachable"
    // 한국어만 본다 — 조사 헬퍼를 쓰는 것이 이 일곱 자리의 한국어 값이다. 영어는 이름을 그대로
    // 잇고, 화면에 넣을 대체값(`—`)은 호출자(`app.js::personLabel`)가 이미 고른다.
    ].forEach((key) => {
      const out = I18N.t("ko", key, args);
      assert.ok(!/null|undefined/.test(out), `ko/${key}: ${out}`);
    });
  });
});

describe("모르는 글자는 받침 없는 쪽 (C-33)", () => {
  test("숫자 타입은 문자열로 바꿔 판정한다", () => {
    assert.equal(hasFinalConsonant(2), false);
    assert.equal(withJosa(2, "이/가"), "2가");
    assert.equal(hasFinalConsonant(0), true);
    assert.equal(withJosa(0, "이/가"), "0이");
  });

  test("이모지 · 한자 · 기호는 `null` 로 떨어지고 던지지 않는다", () => {
    // 이모지는 JS 에서 서로게이트 쌍이다 — 마지막 코드 단위(U+DE80)가 한글 범위에 안 들어간다.
    assert.equal(hasFinalConsonant("배포🚀"), null);
    assert.equal(withJosa("배포🚀", "이/가"), "배포🚀가");
    assert.equal(hasFinalConsonant("作業"), null);
    assert.equal(withJosa("作業", "이/가"), "作業가");
    assert.equal(hasFinalConsonant("✓"), null);
    assert.equal(withJosa("✓", "이/가"), "✓가");
  });
});

describe("모르는 쌍은 던진다 (C-34)", () => {
  ["이가", "", null, undefined, "으로/로", "가/이"].forEach((pair) => {
    test(JSON.stringify(pair) + " → throw", () => {
      assert.throws(() => josa("mac2", pair), /unknown pair/);
      assert.throws(() => withJosa("mac2", pair), /unknown pair/);
    });
  });
});

describe("api 노출 (C-35)", () => {
  test("세 함수가 카탈로그 api 에 있다", () => {
    assert.equal(typeof I18N.josa, "function");
    assert.equal(typeof I18N.withJosa, "function");
    assert.equal(typeof I18N.hasFinalConsonant, "function");
  });

  test("브라우저 전역에도 같은 것이 실린다", () => {
    assert.equal(globalThis.rcmI18n.withJosa, I18N.withJosa);
    assert.equal(globalThis.rcmI18n.josa, I18N.josa);
    assert.equal(globalThis.rcmI18n.hasFinalConsonant, I18N.hasFinalConsonant);
  });
});
