"use strict";
// 가독성 R1 — Codex 가독성 검토(2026-11-17) 1 · 5 · 6 · 7 · 8번의 순수한 부분.
//
// 잠그는 것:
// - 6번: 식별자의 **가운데** 말줄임. `midSplit` 은 이름을 앞 · 뒤 둘로 나누고(이으면 원문 그대로),
//   뒤 토막은 구별되는 끝(번호 · 슬롯 · 시나리오)을 남긴다 — 구분자 바로 뒤에서 자르고 폭은
//   상한이 있다. 짧은 이름은 나누지 않는다. `midHtml` 은 그 둘을 `.mx-h` · `.mx-t` 로 감싼다.
// - 1번: 잘릴 수 있는 칸은 `data-cut` 표지를 단다 — 그려진 뒤 실제로 잘렸으면 DOM 층이 그 요소를
//   탭 · 키보드로 열 수 있게 한다. 표지가 없으면 전문에 닿을 길이 title 뿐이다.
// - 5번: `bandMore` 는 워커 띠에서 화면 밖에 있는 필을 센다 — 끊김 · 보류가 숨었으면 표지가 경고색이다.
// - 7번: `unreachableNote` 는 끊긴 워커 띠를 **요약**한다 — 이름 목록은 본문에 없고 펼쳐서 본다.
//   한 대이고 이름이 짧을 때만 이름을 본문에 쓴다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();

const LONG_MACHINE = "build-farm-mac-studio-m4-ultra-rack-07-in-the-basement-closet";
const LONG_KEY =
  "gate:release_candidate_full_regression_matrix_with_golden_screenshots_and_integration_suite_shard_0001_of_0016_ios_android_web_macos";

function width(s) {
  let w = 0;
  for (const ch of s) w += ch.codePointAt(0) >= 0x1100 ? 2 : 1;
  return w;
}

describe("midSplit — middle ellipsis keeps the distinguishing tail (Codex 6)", () => {
  test("short names are not split", () => {
    assert.deepEqual(rcm.midSplit("gate:full"), ["gate:full", ""]);
    assert.deepEqual(rcm.midSplit("mac2-worker"), ["mac2-worker", ""]);
    assert.deepEqual(rcm.midSplit(""), ["", ""]);
  });

  test("head + tail is always the original text", () => {
    const names = [LONG_MACHINE, LONG_KEY, "w".repeat(60), "맥미니-두번째-작업기계-회의실-선반-위", LONG_MACHINE + "-07",
      "전체게이트회귀검증".repeat(7), "epeuem-mac-studio-m4-ultra-192gb-rack3-slot12.corp.example.internal (a1f3c9d2)"];
    names.forEach((n) => {
      const [h, t] = rcm.midSplit(n);
      assert.equal(h + t, n, n);
      assert.ok(t.length > 0, "long names have a tail: " + n);
      assert.ok(h.length > 0, "and a head: " + n);
    });
  });

  test("the tail starts right after a separator and keeps the slot / number", () => {
    assert.equal(rcm.midSplit(LONG_MACHINE + "-07")[1], "closet-07");
    assert.equal(rcm.midSplit(LONG_MACHINE + "-22")[1], "closet-22");
    assert.equal(rcm.midSplit("맥미니-두번째-작업기계-회의실-선반-위")[1], "회의실-선반-위");
    // 공통 접두사가 긴 이름 둘이 끝에서 갈린다 — 끝 말줄임은 둘을 똑같이 보이게 했다
    const a = rcm.midSplit(LONG_MACHINE + "-01")[1], b = rcm.midSplit(LONG_MACHINE + "-02")[1];
    assert.notEqual(a, b);
  });

  test("the tail is bounded in width — a name with no separator keeps its last few characters", () => {
    [LONG_MACHINE, LONG_KEY, "w".repeat(60), "전체게이트회귀검증".repeat(7)].forEach((n) => {
      const t = rcm.midSplit(n)[1];
      assert.ok(width(t) <= 16, n + " → " + t);
      assert.ok(width(t) >= 6, n + " → " + t);
    });
    assert.equal(rcm.midSplit("w".repeat(60))[1], "w".repeat(10));
  });
});

describe("midHtml — head and tail spans", () => {
  test("a short name is plain escaped text", () => {
    assert.equal(rcm.midHtml("gate:full"), "gate:full");
    assert.equal(rcm.midHtml("<b>"), "&lt;b&gt;");
  });

  test("a long name is two spans inside .mx, both escaped, no whitespace between them", () => {
    const h = rcm.midHtml(LONG_MACHINE + "-<07>");
    const m = /^<span class="mx"><span class="mx-h">(.*)<\/span><span class="mx-t">(.*)<\/span><\/span>$/.exec(h);
    assert.ok(m, h);
    assert.ok(!m[1].includes("<") && !m[2].includes("<"), h);
    assert.ok(m[2].endsWith("&lt;07&gt;"), h);
  });
});

describe("keyWithLinkHtml — the name is marked as possibly cut and split in the middle", () => {
  test("short name: data-cut, full name in title", () => {
    assert.equal(rcm.keyWithLinkHtml("gate:full", null), '<span class="key" title="gate:full" data-cut="key">gate:full</span>');
  });

  test("long name: the middle-ellipsis spans live inside the titled span", () => {
    const h = rcm.keyWithLinkHtml(LONG_KEY, null, "kn");
    assert.ok(h.startsWith('<span class="kn" title="' + LONG_KEY + '" data-cut="kn"><span class="mx">'), h);
    assert.ok(h.includes('<span class="mx-t">' + rcm.midSplit(LONG_KEY)[1] + "</span>"), h);
  });
});

describe("workerPills — the remote name carries its middle-ellipsis split", () => {
  test("name and rest stay; the name is split for drawing", () => {
    const pills = rcm.workerPills({ lanes: 0, workers: [{ worker: "w", display_name: LONG_MACHINE + "-07", lane: 1, state: "down" }] }, "en");
    assert.equal(pills.length, 1);
    assert.equal(pills[0].name, LONG_MACHINE + "-07");
    assert.equal(pills[0].text, LONG_MACHINE + "-07 down");
    assert.deepEqual(rcm.midSplit(pills[0].name)[1], "closet-07");
  });
});

describe("machineRows — names and hosts are drawn with middle ellipsis", () => {
  test("rows keep the full strings (the DOM splits them)", () => {
    const rows = rcm.machineRows({ machines: [{ id: "m1", display_name: LONG_MACHINE, host_label: "host-" + "x".repeat(40) + "-slot12" }] }, false, "en");
    assert.equal(rows[0].name, LONG_MACHINE);
    assert.equal(rcm.midSplit(rows[0].host)[1].endsWith("slot12"), true);
  });
});

describe("bandMore — which worker pills are off-screen (Codex 5)", () => {
  const items = [
    { left: 0, right: 80, label: "#412", warn: false },
    { left: 86, right: 200, label: "mac2-worker down", warn: true },
    { left: 206, right: 300, label: "mbp held", warn: true },
    { left: 306, right: 400, label: "mac-studio-2 idle", warn: false }
  ];

  test("everything fits — nothing hidden", () => {
    assert.deepEqual(rcm.bandMore(items, 0, 400), { n: 0, warn: false, labels: [] });
  });

  test("partly visible counts as hidden; warn when a down/held pill is hidden", () => {
    assert.deepEqual(rcm.bandMore(items, 0, 210), { n: 2, warn: true, labels: ["mbp held", "mac-studio-2 idle"] });
    assert.deepEqual(rcm.bandMore(items, 0, 120), { n: 3, warn: true, labels: ["mac2-worker down", "mbp held", "mac-studio-2 idle"] });
  });

  test("scrolled: pills hidden on the left count too", () => {
    const r = rcm.bandMore(items, 200, 400);
    assert.equal(r.n, 2);
    assert.equal(r.warn, true);
    assert.deepEqual(r.labels, ["#412", "mac2-worker down"]);
  });

  test("only idle/busy pills hidden — the marker is not a warning", () => {
    assert.equal(rcm.bandMore(items, 0, 305).warn, false);
    assert.equal(rcm.bandMore(items, 0, 305).n, 1);
  });

  test("garbage in — zero out", () => {
    assert.deepEqual(rcm.bandMore(null, 0, 10), { n: 0, warn: false, labels: [] });
  });
});

describe("unreachableNote — the worker-unreachable banner is a summary (Codex 7)", () => {
  test("two workers: a count, no names in the sentence; the names are the list behind the reveal", () => {
    const names = [LONG_MACHINE, "mac2-worker"];
    ["ko", "en"].forEach((lang) => {
      const n = rcm.unreachableNote(names, lang);
      assert.ok(!n.text.includes(LONG_MACHINE) && !n.text.includes("mac2-worker"), lang + ": " + n.text);
      assert.match(n.text, /2/, lang + ": " + n.text);
      assert.equal(n.list, LONG_MACHINE + "\nmac2-worker");
      assert.equal(n.inline, false);
    });
    assert.ok(rcm.unreachableNote(names, "ko").text.startsWith("워커 2대 연결 끊김"), rcm.unreachableNote(names, "ko").text);
  });

  test("one short name is said inline", () => {
    const n = rcm.unreachableNote(["mac2-worker"], "ko");
    assert.ok(n.text.includes("mac2-worker"), n.text);
    assert.equal(n.inline, true);
  });

  test("one long name is still a count — the banner does not grow with the name", () => {
    const n = rcm.unreachableNote([LONG_MACHINE], "en");
    assert.ok(!n.text.includes(LONG_MACHINE), n.text);
    assert.equal(n.inline, false);
    assert.equal(n.list, LONG_MACHINE);
  });

  test("the sentence length does not depend on the names", () => {
    const a = rcm.unreachableNote(["a".repeat(80), "b".repeat(80), "c"], "ko").text;
    const b = rcm.unreachableNote(["x", "y", "z"], "ko").text;
    assert.equal(a, b);
  });
});

describe("reveal popup buttons read as one pair (final check)", () => {
  // 진짜 dev 서버에서 영어 팝업이 「copy」 「Close」 — 나란한 두 단추의 대소문자가 달랐다.
  // 큐 행의 작은 복사 칩(`row.copy`, 소문자)과 팝업의 단추는 다른 글이다.
  const t = require("path").join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js");
  const I18N = require(t);
  test("English: Copy · Close", () => {
    assert.equal(I18N.t("en", "reveal.copy"), "Copy");
    assert.equal(I18N.t("en", "reveal.close"), "Close");
  });
  test("Korean: 복사 · 닫기", () => {
    assert.equal(I18N.t("ko", "reveal.copy"), "복사");
    assert.equal(I18N.t("ko", "reveal.close"), "닫기");
  });
});
