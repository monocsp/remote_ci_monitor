"use strict";
// M5b-1 웹 — `pools[]` 순회(명세 docs/m5-workplan.md 「M5b」 `pools[]` 항목: app.js 의 pool0()/pools[0] 를
// 순회 구조로 · 풀 하나일 때 화면은 그대로). 순수 함수만 node --test 로.
//
// 대상 함수(가정 — app.js 에 아직 없다. 구현자가 추가할 순수 함수):
//   rcm.poolSummary(pools) → {running, waiting, pools}. 풀 전체의 running(+cancelling)·waiting 수와 풀 수.
//                            어느 풀이든 queue 가 null(조회 실패)이면 running/waiting 은 null — 0 이 아니다
//                            (fail-open 금지). pools 가 배열이 아니면 {null, null, 0}.
//   rcm.poolHeader(pool)   → 큐 표 위 풀 헤더 문구(문자열, HTML 아님 — DOM 층이 esc 한다). 기본 풀은 ""
//                            (풀 하나일 때 화면은 그대로), 다른 풀은 "pool <name>", lanes 가 0 이고 hosts 가
//                            비면 "pool <name> · no workers". lanes 를 모르면 「no workers」를 주장하지 않는다.
// 요약 세 칸의 빌더(notMoving · yourJobs)는 모든 풀의 행을 본다. 기존 함수는 pools[0] 로 그대로(회귀).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, fromNow, NOW } = require("./helpers");

const rcm = load();

// 픽스처의 행을 복제해 바꾼 변형 행.
function rowLike(status, id, patch) {
  return Object.assign(structuredClone(job(status, id)), patch);
}

// 워커 없는 풀의 대기 행: #414(waiting_for_lane) 을 복사해 worker_down 으로.
function downRow(status, id, position, patch) {
  const r = rowLike(status, 414, Object.assign({ id: id, position: position, reason: "worker_down", ahead_job_id: null }, patch || {}));
  Object.assign(r.estimate, { finish_at: null, wait_seconds: null, waited_seconds: 300 });
  return r;
}

// status 에 linux 풀(기본 풀 복사 · lanes 0 · hosts []) 을 붙인다. queue 를 주면 그걸 쓴다.
function withLinux(status, queue, patch) {
  const linux = structuredClone(status.pools[0]);
  Object.assign(linux, { name: "linux", lanes: 0, hosts: [], queue: queue }, patch || {});
  status.pools.push(linux);
  return status;
}

describe("module contract", () => {
  test("poolSummary is exported on rcm", () => {
    assert.equal(typeof rcm.poolSummary, "function", "rcm.poolSummary must be a function");
  });

  test("poolHeader is exported on rcm", () => {
    assert.equal(typeof rcm.poolHeader, "function", "rcm.poolHeader must be a function");
  });
});

describe("poolSummary", () => {
  test("main fixture (one pool) → 2 running · 3 waiting · 1 pool", () => {
    assert.deepEqual(rcm.poolSummary(fixture("main").pools), { running: 2, waiting: 3, pools: 1 });
  });

  test("two pools → counts across both", () => {
    const s = withLinux(fixture("main"), [downRow(fixture("main"), 521, 1)]);
    assert.deepEqual(rcm.poolSummary(s.pools), { running: 2, waiting: 4, pools: 2 });
  });

  test("cancelling counts as running; uploading counts as waiting", () => {
    const s = fixture("main");
    Object.assign(job(s, 412), { state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "alice-laptop", kill_at: fromNow(8) } });
    assert.deepEqual(rcm.poolSummary(s.pools), { running: 2, waiting: 3, pools: 1 });
  });

  test("empty queue → real zeros", () => {
    assert.deepEqual(rcm.poolSummary(fixture("empty").pools), { running: 0, waiting: 0, pools: 1 });
  });

  test("a pool whose queue is null (query failed) → running/waiting null, never a count", () => {
    const r = rcm.poolSummary(fixture("errors").pools);
    assert.equal(r.running, null);
    assert.equal(r.waiting, null);
    assert.equal(r.pools, 1);
    // 첫 풀은 멀쩡하고 둘째 풀만 실패해도 합계는 모른다
    const s = withLinux(fixture("main"), null, { queue_error: "database locked" });
    const r2 = rcm.poolSummary(s.pools);
    assert.equal(r2.running, null);
    assert.equal(r2.waiting, null);
    assert.equal(r2.pools, 2);
  });

  test("pools missing → {null, null, 0}, no throw", () => {
    assert.deepEqual(rcm.poolSummary(null), { running: null, waiting: null, pools: 0 });
    assert.deepEqual(rcm.poolSummary(undefined), { running: null, waiting: null, pools: 0 });
  });

  test("does not mutate its input", () => {
    const pools = fixture("main").pools;
    const before = JSON.stringify(pools);
    rcm.poolSummary(pools);
    assert.equal(JSON.stringify(pools), before);
  });
});

describe("poolHeader", () => {
  test("default pool → '' (one pool keeps today's screen)", () => {
    assert.equal(rcm.poolHeader(fixture("main").pools[0]), "");
    assert.equal(rcm.poolHeader(fixture("single-lane").pools[0]), "");
  });

  test("another pool with lanes → 'pool linux'", () => {
    const p = structuredClone(fixture("main").pools[0]);
    p.name = "linux";
    assert.equal(rcm.poolHeader(p), "pool linux");
  });

  test("lanes 0 and no hosts → 'pool linux · no workers'", () => {
    assert.equal(rcm.poolHeader({ name: "linux", lanes: 0, hosts: [], queue: [] }), "pool linux · no workers");
  });

  test("lanes unknown → no 'no workers' claim", () => {
    const h = rcm.poolHeader({ name: "linux", queue: [] });
    assert.ok(h.indexOf("pool linux") === 0, h);
    assert.ok(!/no workers/.test(h), h);
  });

  test("returns plain text, not HTML — the name is not escaped here", () => {
    const h = rcm.poolHeader({ name: "a<b", lanes: 1, hosts: [] });
    assert.equal(h, "pool a<b");
  });

  test("pool null / undefined → '', no throw", () => {
    assert.equal(rcm.poolHeader(null), "");
    assert.equal(rcm.poolHeader(undefined), "");
  });
});

describe("summary builders see every pool", () => {
  test("notMoving lists a worker_down row from the second pool, ordered before blocked_by_group", () => {
    const s = withLinux(fixture("main"), [downRow(fixture("main"), 521, 1)]);
    const r = rcm.notMoving(s, "alice-laptop");
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => [l.jobId, l.reason]), [[521, "worker_down"], [413, "blocked_by_group"]]);
    assert.ok(r.lines[0].text.includes("no worker"), r.lines[0].text);
  });

  test("first pool fine, second pool stuck → not ok", () => {
    const s = fixture("main");
    Object.assign(job(s, 413), { reason: "waiting_for_lane", ahead_job_id: 409, blocked_by: null });
    assert.equal(rcm.notMoving(structuredClone(s), null).kind, "ok");
    withLinux(s, [downRow(fixture("main"), 521, 1)]);
    const r = rcm.notMoving(s, null);
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [521]);
  });

  test("second pool queue null → unknown, never ok", () => {
    const s = fixture("main");
    Object.assign(job(s, 413), { reason: "waiting_for_lane", ahead_job_id: 409, blocked_by: null });
    withLinux(s, null, { queue_error: "database locked" });
    assert.equal(rcm.notMoving(s, null).kind, "unknown");
  });

  test("second pool empty → same answer as one pool", () => {
    const base = rcm.notMoving(fixture("main"), null);
    const r = rcm.notMoving(withLinux(fixture("main"), []), null);
    assert.equal(r.kind, base.kind);
    assert.deepEqual(r.lines.map((l) => l.jobId), base.lines.map((l) => l.jobId));
  });

  test("yourJobs counts my job in the second pool", () => {
    const mine = downRow(fixture("main"), 522, 1, { requester: { name: "alice-laptop", label: "alice@laptop" } });
    const s = withLinux(fixture("main"), [mine]);
    const r = rcm.yourJobs(s, "alice-laptop");
    assert.equal(r.kind, "list");
    assert.equal(r.lines.length + r.more, 3);  // 412 · 414 · 522
    // 다른 사람의 linux 잡은 여전히 내 것이 아니다
    // downRow 는 #414(내 잡)를 복사하므로 남의 잡은 요청자를 바꿔야 한다
    const other = withLinux(fixture("main"), [downRow(fixture("main"), 523, 1, { requester: { name: "bob-desk", label: "bob@desk" } })]);
    const r2 = rcm.yourJobs(other, "alice-laptop");
    assert.equal(r2.lines.length + r2.more, 2);
  });

  test("yourJobs: only the second pool holds my job → list, not none", () => {
    const s = fixture("main");
    for (const row of s.pools[0].queue) row.requester = { name: "bob-desk", label: "bob@desk" };
    job(s, 412).joiners = [];
    assert.equal(rcm.yourJobs(structuredClone(s), "alice-laptop").kind, "none");
    withLinux(s, [downRow(fixture("main"), 522, 1, { requester: { name: "alice-laptop", label: "alice@laptop" } })]);
    const r = rcm.yourJobs(s, "alice-laptop");
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [522]);
  });
});

describe("regression: one pool behaves as before", () => {
  test("notMoving / yourJobs / queueHeader / hostPressure on the main fixture", () => {
    const s = fixture("main");
    const nm = rcm.notMoving(s, "alice-laptop");
    assert.equal(nm.kind, "list");
    assert.deepEqual(nm.lines.map((l) => l.jobId), [413]);
    const yj = rcm.yourJobs(s, "alice-laptop");
    assert.deepEqual(yj.lines.map((l) => l.jobId), [412, 414]);
    assert.equal(yj.more, 0);
    assert.equal(rcm.queueHeader(s, NOW), "5 jobs · 2 running · 3 waiting · oldest waiting 1m 35s · lanes 2/2 busy");
    assert.equal(rcm.hostPressure(s.pools[0].hosts[0]).verdict, "fine");
    assert.deepEqual(rcm.sortQueue(s.pools[0].queue).map((r) => r.id), [412, 409, 413, 414, 415]);
  });

  test("errors / empty fixtures still answer unknown / ok", () => {
    assert.equal(rcm.notMoving(fixture("errors"), null).kind, "unknown");
    assert.equal(rcm.yourJobs(fixture("errors"), "alice-laptop").kind, "unknown");
    assert.equal(rcm.notMoving(fixture("empty"), null).kind, "ok");
    assert.equal(rcm.queueHeader(fixture("errors"), NOW), "unknown");
  });

  test("poolSummary agrees with queueHeader's counts on every fixture with one pool", () => {
    for (const name of ["main", "empty", "single-lane", "paused-down"]) {
      const s = fixture(name);
      const sum = rcm.poolSummary(s.pools);
      const head = rcm.queueHeader(s, NOW);
      assert.equal(sum.pools, 1, name);
      assert.ok(head.includes(sum.running + " running · " + sum.waiting + " waiting"), name + ": " + head);
    }
  });
});
