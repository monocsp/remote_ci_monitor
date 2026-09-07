"use strict";
// M5b-4 웹 — Host 절의 카드 목록(rcm.hostCards). 명세 docs/m5b4-workplan.md §2: 원격 워커의 호스트 표본은
// Host 절에 카드로, 제목 `build-02 · pool linux`. 기본 풀 로컬 카드는 오늘 그대로(제목 = 호스트 이름).
// Recent 절 밑의 풀별 host 블록은 없앤다 — 표본이 없는 원격 풀은 카드를 만들지 않는다.
//
// 대상 함수(가정 — app.js 에 아직 없다. 구현자가 추가할 순수 함수):
//   rcm.hostCards(status) → [{title, pool, host}] 순서대로. 기본 풀(pools[0] · name "default")의 hosts[] 가
//     먼저 — title 은 오늘 카드 제목 그대로 host.name(source 가 worker 여도 기본 풀이면 붙이는 게 없다) —
//     그 뒤 나머지 풀의 hosts[] 가 풀 순서·배열 순서대로, title "<host.name> · pool <pool.name>".
//     hosts 가 [] · null · 없음인 풀은 카드를 내지 않고 예외도 없다(기본 풀의 hosts null 은 DOM 층이 오늘처럼
//     `Host unavailable` 띠로 — 여기서는 카드 없음). title 은 평문(HTML 아님 — DOM 층이 esc 한다).
//     status/pools 가 없으면 []. 입력은 바꾸지 않는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture } = require("./helpers");

const rcm = load();

// 원격 워커의 호스트 표본 — main 픽스처의 로컬 표본을 복사해 이름과 source 만 바꾼다(heartbeat 가 주는 모양).
function workerHost(status, name) {
  const h = structuredClone(status.pools[0].hosts[0]);
  h.name = name;
  h.source = "worker";
  h.os = "linux";
  return h;
}

// status 에 풀 하나를 붙인다(기본 풀 복사 · lanes 1 · 잡 없음). hosts 는 patch 로.
function withPool(status, name, patch) {
  const p = structuredClone(status.pools[0]);
  Object.assign(p, { name: name, lanes: 1, hosts: [], queue: [], recent: [] }, patch || {});
  status.pools.push(p);
  return status;
}

function titles(status) {
  return rcm.hostCards(status).map((c) => c.title);
}

describe("module contract", () => {
  test("hostCards is exported on rcm", () => {
    assert.equal(typeof rcm.hostCards, "function", "rcm.hostCards must be a function");
  });
});

describe("default pool: today's card, unchanged", () => {
  test("main fixture → one card titled exactly the host name, pool default, the host itself", () => {
    const s = fixture("main");
    const cards = rcm.hostCards(s);
    assert.equal(cards.length, 1);
    assert.equal(cards[0].title, "macmini");  // 오늘 카드 제목 = h.name 그대로
    assert.equal(cards[0].pool, "default");
    assert.deepEqual(cards[0].host, s.pools[0].hosts[0]);
  });

  test("no ' · pool default' suffix, even for a worker sample that landed in the default pool", () => {
    const s = fixture("main");
    s.pools[0].hosts.push(workerHost(s, "build-01"));
    assert.deepEqual(titles(s), ["macmini", "build-01"]);
    assert.deepEqual(rcm.hostCards(s).map((c) => c.pool), ["default", "default"]);
  });

  test("hosts [] → no card (fixtures empty · single-lane · paused-down)", () => {
    for (const name of ["empty", "single-lane", "paused-down"]) {
      assert.deepEqual(rcm.hostCards(fixture(name)), [], name);
    }
  });

  test("hosts null (errors fixture) → no card and no throw", () => {
    assert.deepEqual(rcm.hostCards(fixture("errors")), []);
  });
});

describe("remote pools: one card per host sample, titled '<name> · pool <pool>'", () => {
  test("linux worker sample → a second card after the local one", () => {
    const s = fixture("main");
    withPool(s, "linux", { hosts: [workerHost(s, "build-02")] });
    const cards = rcm.hostCards(s);
    assert.deepEqual(cards.map((c) => [c.title, c.pool]), [["macmini", "default"], ["build-02 · pool linux", "linux"]]);
    assert.deepEqual(cards[1].host, s.pools[1].hosts[0]);
    assert.equal(cards[1].host.source, "worker");
  });

  test("two samples in one pool → two cards in array order", () => {
    const s = fixture("main");
    withPool(s, "linux", { hosts: [workerHost(s, "build-02"), workerHost(s, "build-03")] });
    assert.deepEqual(titles(s), ["macmini", "build-02 · pool linux", "build-03 · pool linux"]);
  });

  test("two remote pools → cards follow the pools order, local first", () => {
    const s = fixture("main");
    withPool(s, "windows", { hosts: [workerHost(s, "win-01")] });
    withPool(s, "linux", { hosts: [workerHost(s, "build-02")] });
    assert.deepEqual(titles(s), ["macmini", "win-01 · pool windows", "build-02 · pool linux"]);
  });

  test("a remote pool with hosts [] contributes no card", () => {
    const s = withPool(fixture("main"), "linux", { hosts: [] });
    assert.deepEqual(titles(s), ["macmini"]);
  });

  test("a remote pool with hosts null (or no hosts key) contributes no card and no error", () => {
    const s = withPool(fixture("main"), "linux", { hosts: null, hosts_error: null });
    assert.deepEqual(titles(s), ["macmini"]);
    const t = withPool(fixture("main"), "linux");
    delete t.pools[1].hosts;
    assert.deepEqual(titles(t), ["macmini"]);
  });

  test("default pool hosts null + a linux sample → only the linux card, no throw", () => {
    const s = fixture("errors");
    withPool(s, "linux", { hosts: [workerHost(fixture("main"), "build-02")] });
    assert.deepEqual(titles(s), ["build-02 · pool linux"]);
  });

  test("default pool has no sample but linux does → the linux card alone", () => {
    const s = fixture("empty");
    withPool(s, "linux", { hosts: [workerHost(fixture("main"), "build-02")] });
    assert.deepEqual(titles(s), ["build-02 · pool linux"]);
  });

  test("the sample decides, not lanes — lanes 0 with a sample still makes a card", () => {
    const s = withPool(fixture("main"), "linux", { lanes: 0, hosts: [workerHost(fixture("main"), "build-02")] });
    assert.deepEqual(titles(s), ["macmini", "build-02 · pool linux"]);
  });

  test("title is plain text — the pool name is not escaped here", () => {
    const s = withPool(fixture("main"), "a<b", { hosts: [workerHost(fixture("main"), "build-02")] });
    assert.equal(titles(s)[1], "build-02 · pool a<b");
  });
});

describe("robustness", () => {
  test("no status / no pools → [] without throwing", () => {
    assert.deepEqual(rcm.hostCards(null), []);
    assert.deepEqual(rcm.hostCards(undefined), []);
    assert.deepEqual(rcm.hostCards({}), []);
    assert.deepEqual(rcm.hostCards({ pools: [] }), []);
    assert.deepEqual(rcm.hostCards({ pools: null }), []);
  });

  test("does not mutate its input", () => {
    const s = fixture("main");
    withPool(s, "linux", { hosts: [workerHost(s, "build-02")] });
    withPool(s, "windows", { hosts: null });
    const before = JSON.stringify(s);
    rcm.hostCards(s);
    assert.equal(JSON.stringify(s), before);
  });
});
