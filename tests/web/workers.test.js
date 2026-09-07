"use strict";
// M5b-2 웹 — 머리의 워커 필(rcm.workerPills)에 원격 워커. 명세 docs/m5b2-workplan.md §4 · §6 「상태 JSON」:
// server.workers[] 항목에 worker(null|이름) · display_name(로컬 null · 원격 "<이름>/<레인>") 이 붙고,
// 로컬 레인이 먼저 · 원격은 워커 이름순. server.lanes 는 로컬 레인 수 그대로.
//
// 잠그는 모양(구현 전 — test-first). 대상은 이미 노출된 순수 함수 rcm.workerPills(server) 와
// rcm.queueHeader / rcm.reasonText(busyCount · laneCount 를 쓰는 곳):
//   - 로컬 항목(worker 키가 없거나 null)은 오늘과 같다: 로컬 레인 하나면 "worker busy #412", 여럿이면
//     "#412" / "lane 2 · idle" / "lane 1 · down". 「레인 하나」판정은 server.lanes 와 **로컬** 항목 수로 한다 —
//     원격 워커가 붙어도 로컬 필 모양이 바뀌면 안 된다.
//   - 원격 항목은 하나에 필 하나: text = display_name + " " + state + (busy 면 " #" + job_id) →
//     "build-02/1 busy #511" · "build-02/1 idle" · "build-02/1 down". cls 는 state 그대로(로컬과 같은
//     클래스 — down 이면 "down" 이라 같은 스타일), jobId 는 busy 면 job_id 아니면 null, lane 은 항목의 lane.
//     text 는 평문이다(HTML 아님 — DOM 층이 esc 한다).
//   - 순서: 로컬(배열 순) → 원격(배열 순) → paused.
//   - busyCount/laneCount(queueHeader · reasonText 의 "lanes 2/2 busy" · "2/2 busy") 는 로컬만 센다 — 원격
//     busy 가 붙어도 문구가 그대로다(server.lanes 가 로컬 수라 "3/2" 같은 게 나오면 안 된다).
//   - /api/health 배너 · DOM 렌더는 여기서 안 본다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, NOW } = require("./helpers");

const rcm = load();
const SINCE = "2026-09-04T00:40:00Z";

// 필 객체에서 계약으로 잠근 네 키만 — 구현이 키를 더 붙여도(예: worker) 여기서는 안 본다.
function pick(p) {
  return { text: p.text, cls: p.cls, jobId: p.jobId, lane: p.lane };
}
function pills(server) {
  return rcm.workerPills(server).map(pick);
}
// 원격 워커 항목.
function remote(name, lane, state, jobId) {
  return { lane: lane, state: state, job_id: jobId == null ? null : jobId, error: null, since: SINCE,
    worker: name, display_name: name + "/" + lane };
}
// 로컬 항목에 M5b-2 키(null)를 더한다.
function withNullKeys(server) {
  server.workers.forEach(function (w) { w.worker = null; w.display_name = null; });
  return server;
}
function withRemote(server) {
  for (var i = 1; i < arguments.length; i++) server.workers.push(arguments[i]);
  return server;
}

describe("module contract", () => {
  test("workerPills is exported on rcm", () => {
    assert.equal(typeof rcm.workerPills, "function", "rcm.workerPills must be a function");
  });
});

describe("local lanes render as today", () => {
  test("single-lane fixture → one 'worker busy #412' pill", () => {
    assert.deepEqual(pills(fixture("single-lane").server), [{ text: "worker busy #412", cls: "busy", jobId: 412, lane: 1 }]);
  });

  test("main fixture (2 lanes) → '#412' · '#409'", () => {
    assert.deepEqual(pills(fixture("main").server), [
      { text: "#412", cls: "busy", jobId: 412, lane: 1 },
      { text: "#409", cls: "busy", jobId: 409, lane: 2 },
    ]);
  });

  test("paused-down fixture → 'lane 1 · down' · '#409' · 'paused'", () => {
    assert.deepEqual(pills(fixture("paused-down").server), [
      { text: "lane 1 · down", cls: "down", jobId: null, lane: 1 },
      { text: "#409", cls: "busy", jobId: 409, lane: 2 },
      { text: "paused", cls: "paused", jobId: null, lane: null },
    ]);
  });

  test("worker: null / display_name: null on local entries change nothing", () => {
    for (const name of ["single-lane", "main", "paused-down", "empty"]) {
      const before = pills(fixture(name).server);
      assert.deepEqual(pills(withNullKeys(fixture(name).server)), before, name);
    }
  });
});

describe("remote worker pills", () => {
  test("busy remote → 'build-02/1 busy #511' after the local pill, cls busy, jobId 511", () => {
    const server = withRemote(withNullKeys(fixture("single-lane").server), remote("build-02", 1, "busy", 511));
    assert.deepEqual(pills(server), [
      { text: "worker busy #412", cls: "busy", jobId: 412, lane: 1 },
      { text: "build-02/1 busy #511", cls: "busy", jobId: 511, lane: 1 },
    ]);
  });

  test("idle remote → 'build-02/1 idle', no job", () => {
    const server = withRemote(withNullKeys(fixture("single-lane").server), remote("build-02", 1, "idle"));
    assert.deepEqual(pills(server)[1], { text: "build-02/1 idle", cls: "idle", jobId: null, lane: 1 });
  });

  test("down remote → 'build-02/1 down' with the same class as a local down lane", () => {
    const localDown = pills(fixture("paused-down").server)[0];
    assert.equal(localDown.cls, "down");
    const server = withRemote(withNullKeys(fixture("single-lane").server), remote("build-02", 1, "down"));
    const p = pills(server)[1];
    assert.deepEqual(p, { text: "build-02/1 down", cls: localDown.cls, jobId: null, lane: 1 });
    assert.ok(!/lane 1 · down/.test(p.text), p.text);  // 로컬 down 문구를 빌리지 않는다
  });

  test("the local single-lane pill keeps its shape when remote workers are appended", () => {
    // 오늘의 조건 `lanes === 1 && workers.length === 1` 은 원격이 붙으면 깨진다 — 로컬 항목 수로 세야 한다
    const server = withRemote(withNullKeys(fixture("single-lane").server), remote("build-02", 1, "idle"), remote("build-03", 1, "idle"));
    const out = pills(server);
    assert.equal(out.length, 3);
    assert.equal(out[0].text, "worker busy #412");
    assert.equal(out[1].text, "build-02/1 idle");
    assert.equal(out[2].text, "build-03/1 idle");
  });

  test("remote entries keep the array order and come after every local lane", () => {
    const server = withRemote(withNullKeys(fixture("main").server),
      remote("build-02", 1, "busy", 511), remote("build-02", 2, "idle"), remote("build-03", 1, "down"));
    assert.deepEqual(pills(server).map((p) => p.text), ["#412", "#409", "build-02/1 busy #511", "build-02/2 idle", "build-03/1 down"]);
  });

  test("paused pill stays last, after the remote pills", () => {
    const server = withRemote(withNullKeys(fixture("paused-down").server), remote("build-02", 1, "idle"));
    assert.deepEqual(pills(server).map((p) => p.text), ["lane 1 · down", "#409", "build-02/1 idle", "paused"]);
  });

  test("a local entry without the worker key next to a remote one is still local", () => {
    // 옛 모양(키 없음) + 새 항목이 섞여도 키 없음 = 로컬
    const server = withRemote(fixture("single-lane").server, remote("build-02", 1, "busy", 511));
    assert.deepEqual(pills(server).map((p) => p.text), ["worker busy #412", "build-02/1 busy #511"]);
  });

  test("display_name is plain text — not escaped here", () => {
    const server = withRemote(withNullKeys(fixture("single-lane").server),
      { lane: 1, state: "idle", job_id: null, error: null, since: SINCE, worker: "a<b", display_name: "a<b/1" });
    assert.equal(pills(server)[1].text, "a<b/1 idle");
  });

  test("does not mutate its input", () => {
    const server = withRemote(withNullKeys(fixture("main").server), remote("build-02", 1, "busy", 511));
    const before = JSON.stringify(server);
    rcm.workerPills(server);
    assert.equal(JSON.stringify(server), before);
  });
});

describe("lane counts stay local", () => {
  test("queueHeader's 'lanes 2/2 busy' is unchanged by a busy remote worker", () => {
    const s = fixture("main");
    const before = rcm.queueHeader(s, NOW);
    assert.ok(/lanes 2\/2 busy$/.test(before), before);
    withRemote(withNullKeys(s.server), remote("build-02", 1, "busy", 511), remote("build-03", 1, "busy", 512));
    assert.equal(rcm.queueHeader(s, NOW), before);
  });

  test("reasonText 'waiting for lane · 2/2 busy' is unchanged by a busy remote worker", () => {
    const s = fixture("main");
    const before = rcm.reasonText(job(s, 414), s, NOW).text;
    assert.ok(before.includes("2/2 busy"), before);
    withRemote(withNullKeys(s.server), remote("build-02", 1, "busy", 511));
    assert.equal(rcm.reasonText(job(s, 414), s, NOW).text, before);
  });

  test("an idle remote worker does not lower the busy count either", () => {
    const s = fixture("main");
    const before = rcm.queueHeader(s, NOW);
    withRemote(withNullKeys(s.server), remote("build-02", 1, "idle"));
    assert.equal(rcm.queueHeader(s, NOW), before);
  });
});

describe("pool header does not grow a lane count (M5b-1 shape kept)", () => {
  test("lanes 2 → 'pool linux', lanes 0 → 'pool linux · no workers'", () => {
    assert.equal(rcm.poolHeader({ name: "linux", lanes: 2, hosts: [], queue: [] }), "pool linux");
    assert.equal(rcm.poolHeader({ name: "linux", lanes: 1, hosts: [], queue: [] }), "pool linux");
    assert.equal(rcm.poolHeader({ name: "linux", lanes: 0, hosts: [], queue: [] }), "pool linux · no workers");
  });
});
