"use strict";
// 큐가 「지금 도는 것」과 「기다리는 것」으로 갈린다 (§4.6-라, 오너 피드백
// 「지금 뭐가 진행중이고 뭐가 되고있는지 이해하기가 힘들어」).
//
// 표가 한 덩어리라 도는 잡과 기다리는 잡이 같은 무게로 섞여 있었다. 같은 표 안에서 먼저 나눈다.
//
// 잠그는 것:
// - `queueGroups(rows, lang)` 를 내보낸다. 인자는 `sortQueue` 를 통과한 행 배열.
// - **언제나 두 묶음**이고 순서는 running 먼저. 빈 묶음도 배열에 남는다 —
//   화면이 「지금 도는 것 없음」을 그릴 자리가 있어야 한다.
// - `running`·`cancelling` 만 running 묶음, 나머지는 전부 waiting.
// - 행 순서는 입력 순서를 보존한다. 정렬은 `sortQueue` 가 이미 했다.
// - 제목은 두 언어에서 다르고 둘 다 비어 있지 않으며, 개수가 들어간다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { load, fixture } = require("./helpers");
const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));

const rcm = load();
const LANGS = I18N.LANGS;

const ids = (rows) => rows.map((r) => r.id);
const row = (id, state) => ({ id: id, state: state });

describe("queueGroups — 나누는 규칙", () => {
  test("main 픽스처: 도는 것 둘 · 기다리는 것 셋, 순서는 sortQueue 그대로", () => {
    const q = rcm.sortQueue(fixture("main").pools[0].queue);
    const g = rcm.queueGroups(q, "en");
    assert.equal(g.length, 2);
    assert.deepEqual(g.map((x) => x.key), ["running", "waiting"], "도는 것이 먼저다");
    assert.deepEqual(ids(g[0].rows), [412, 409], "레인 순 — sortQueue 의 정렬을 다시 뒤집지 않는다");
    assert.deepEqual(ids(g[1].rows), [413, 414, 415]);
  });

  test("cancelling 은 도는 쪽이다 — 아직 레인을 잡고 있다", () => {
    const g = rcm.queueGroups([row(1, "running"), row(2, "cancelling"), row(3, "queued")], "en");
    assert.deepEqual(ids(g[0].rows), [1, 2]);
    assert.deepEqual(ids(g[1].rows), [3]);
  });

  test("도는 두 상태가 아니면 전부 기다리는 쪽이다 (모르는 상태 포함)", () => {
    const rows = [
      row(1, "queued"), row(2, "uploading"), row(3, "materializing"),
      row(4, "blocked"), row(5, null), { id: 6 }, row(7, "RUNNING"),
    ];
    const g = rcm.queueGroups(rows, "en");
    assert.deepEqual(ids(g[0].rows), [], "모르는 상태를 도는 것으로 올리면 거짓말이 된다");
    assert.deepEqual(ids(g[1].rows), [1, 2, 3, 4, 5, 6, 7]);
  });

  test("행 순서는 입력 순서를 보존한다 — 묶음 안에서도 다시 정렬하지 않는다", () => {
    const rows = [row(9, "running"), row(3, "queued"), row(1, "running"), row(7, "queued"), row(5, "cancelling")];
    const g = rcm.queueGroups(rows, "en");
    assert.deepEqual(ids(g[0].rows), [9, 1, 5], "id 로도 상태로도 다시 정렬하지 않는다");
    assert.deepEqual(ids(g[1].rows), [3, 7]);
  });

  test("행 객체는 그대로 넘어온다 — 그리는 층이 같은 행을 읽는다", () => {
    const rows = [row(1, "running"), row(2, "queued")];
    const g = rcm.queueGroups(rows, "en");
    assert.equal(g[0].rows[0], rows[0]);
    assert.equal(g[1].rows[0], rows[1]);
  });

  test("입력을 건드리지 않는다", () => {
    const rows = [row(2, "queued"), row(1, "running")];
    const before = structuredClone(rows);
    rcm.queueGroups(rows, "en");
    assert.deepEqual(rows, before);
  });
});

describe("queueGroups — 빈 묶음도 남는다", () => {
  test("도는 것이 없어도 running 묶음이 rows: [] 로 남는다", () => {
    const g = rcm.queueGroups([row(1, "queued"), row(2, "uploading")], "en");
    assert.equal(g.length, 2);
    assert.equal(g[0].key, "running");
    assert.deepEqual(g[0].rows, [], "자리가 없으면 「지금 도는 것 없음」을 그릴 수 없다");
    assert.deepEqual(ids(g[1].rows), [1, 2]);
  });

  test("기다리는 것이 없어도 waiting 묶음이 남는다", () => {
    const g = rcm.queueGroups([row(1, "running")], "en");
    assert.equal(g.length, 2);
    assert.equal(g[1].key, "waiting");
    assert.deepEqual(g[1].rows, []);
  });

  test("빈 큐 → 두 묶음 다 비었다", () => {
    LANGS.forEach((lang) => {
      const g = rcm.queueGroups([], lang);
      assert.deepEqual(g.map((x) => x.key), ["running", "waiting"], lang);
      assert.deepEqual(g[0].rows, [], lang);
      assert.deepEqual(g[1].rows, [], lang);
      g.forEach((x) => assert.ok(x.title.length > 0, lang + "/" + x.key + ": 제목이 비었다"));
    });
  });

  test("배열이 아니면 두 묶음 다 빈 배열 — 던지지 않는다", () => {
    [null, undefined, {}, "queue", 42, true].forEach((bad) => {
      const label = JSON.stringify(bad) || String(bad);
      let g;
      assert.doesNotThrow(() => { g = rcm.queueGroups(bad, "en"); }, label);
      assert.deepEqual(g.map((x) => x.key), ["running", "waiting"], label);
      assert.deepEqual(g[0].rows, [], label);
      assert.deepEqual(g[1].rows, [], label);
    });
  });
});

describe("queueGroups — 제목", () => {
  const rows = [row(1, "running"), row(2, "cancelling"), row(3, "queued"), row(4, "queued"), row(5, "uploading")];

  test("두 언어 모두 비어 있지 않고, 개수가 들어간다", () => {
    LANGS.forEach((lang) => {
      const g = rcm.queueGroups(rows, lang);
      assert.equal(g[0].rows.length, 2);
      assert.equal(g[1].rows.length, 3);
      g.forEach((x) => {
        assert.equal(typeof x.title, "string", lang + "/" + x.key);
        assert.ok(x.title.length > 0, lang + "/" + x.key + ": 제목이 비었다");
        assert.ok(!/undefined|NaN|\[object/.test(x.title), lang + "/" + x.key + ": " + x.title);
        // 개수가 없으면 접힌 묶음이 몇 개를 감추고 있는지 못 읽는다.
        assert.match(x.title, new RegExp("(^|\\D)" + x.rows.length + "(\\D|$)"),
          lang + "/" + x.key + ": 제목에 개수가 없다 — " + x.title);
      });
    });
  });

  test("두 언어의 제목이 다르다 — 한쪽이 번역을 빼먹으면 여기서 걸린다", () => {
    const en = rcm.queueGroups(rows, "en");
    const ko = rcm.queueGroups(rows, "ko");
    assert.notEqual(ko[0].title, en[0].title, "running 제목이 두 언어에서 같다");
    assert.notEqual(ko[1].title, en[1].title, "waiting 제목이 두 언어에서 같다");
  });

  test("언어는 제목만 바꾼다 — 나누는 규칙은 같다", () => {
    const en = rcm.queueGroups(rows, "en");
    const ko = rcm.queueGroups(rows, "ko");
    assert.deepEqual(ko.map((x) => x.key), en.map((x) => x.key));
    assert.deepEqual(ko.map((x) => ids(x.rows)), en.map((x) => ids(x.rows)));
  });

  test("언어를 안 주면 영어다 — 순수 함수의 집안 규칙", () => {
    const def = rcm.queueGroups(rows);
    const en = rcm.queueGroups(rows, "en");
    assert.deepEqual(def.map((x) => x.title), en.map((x) => x.title));
  });
});
