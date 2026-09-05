"use strict";
// 요약 세 칸(항목 23·24·25) · 큐 헤더(5) · 정렬(6) · 워커 필(1) · 머리 띠(2).
// 긍정 문구(ok · fine)는 조회가 성공했고 값이 전부 있을 때만 — fail-open 금지.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, fromNow, NOW } = require("./helpers");

const rcm = load();
const DASH = "—";

// 픽스처의 행을 복제해 id·reason 을 바꾼 변형 행.
function rowLike(status, id, patch) {
  return Object.assign(structuredClone(job(status, id)), patch);
}

describe("notMoving", () => {
  test("main fixture → one line: #413 blocked by #409 with the evidence numbers", () => {
    const r = rcm.notMoving(fixture("main"), "alice-laptop");
    assert.equal(r.kind, "list");
    assert.equal(r.lines.length, 1);
    const line = r.lines[0];
    assert.equal(line.jobId, 413);
    assert.equal(line.reason, "blocked_by_group");
    assert.ok(line.text.includes("blocked by #409"), line.text);
    assert.ok(line.text.includes("frees in 2m 40s"), line.text);
    assert.ok(!line.text.startsWith("#"), "job id is separate from text");
  });

  test("orders worker_down → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused", () => {
    const s = fixture("main");
    const stuck = rowLike(s, 412, { id: 422, reason: "stuck" });
    Object.assign(stuck.estimate, { confidence: "overdue", elapsed_seconds: 1150, overdue: true, stuck: true, finish_at: null });
    stuck.progress.last_output_at = fromNow(-250);
    const stalled = rowLike(s, 415, { id: 423, position: 6, reason: "upload_stalled" });
    stalled.source.last_received_at = fromNow(-120);
    const overdue = rowLike(s, 412, { id: 425, reason: "overdue" });
    Object.assign(overdue.estimate, { confidence: "overdue", elapsed_seconds: 580, overdue: true, finish_at: null });
    // 우선순위의 역순으로 넣는다 — 입력 순서가 아니라 규칙이 순서를 정해야 한다
    s.pools[0].queue = [
      rowLike(s, 414, { id: 426, position: 7, reason: "paused" }),
      overdue,
      job(s, 413),
      rowLike(s, 414, { id: 424, position: 5, reason: "not_scheduled" }),
      stalled,
      stuck,
      rowLike(s, 414, { id: 421, position: 4, reason: "worker_down" }),
      job(s, 412), job(s, 409), job(s, 414),
    ];
    const r = rcm.notMoving(s, null);
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.reason),
      ["worker_down", "stuck", "upload_stalled", "not_scheduled", "blocked_by_group", "overdue", "paused"]);
    assert.deepEqual(r.lines.map((l) => l.jobId), [421, 422, 423, 424, 413, 425, 426]);
    const text = Object.fromEntries(r.lines.map((l) => [l.reason, l.text]));
    assert.ok(text.worker_down.includes("no worker"), text.worker_down);
    assert.ok(text.stuck.includes("3× expected"), text.stuck);
    assert.ok(text.upload_stalled.includes("upload stalled 2m"), text.upload_stalled);
    assert.ok(text.not_scheduled.includes("not scheduled"), text.not_scheduled);
    assert.ok(text.overdue.includes("over by 3m 31s"), text.overdue);
    assert.ok(text.paused.includes("paused"), text.paused);
  });

  test("two rows with the same reason are both listed, in queue order", () => {
    const s = fixture("main");
    const second = rowLike(s, 413, { id: 427, position: 4 });
    s.pools[0].queue.push(second);
    const r = rcm.notMoving(s, null);
    assert.deepEqual(r.lines.map((l) => l.jobId), [413, 427]);
  });

  test("nothing actionable → ok with no lines", () => {
    const s = fixture("main");
    Object.assign(job(s, 413), { reason: "waiting_for_lane", ahead_job_id: 409, blocked_by: null });
    const r = rcm.notMoving(s, "alice-laptop");
    assert.equal(r.kind, "ok");
    assert.ok(!r.lines || r.lines.length === 0);
  });

  test("empty queue → ok", () => {
    assert.equal(rcm.notMoving(fixture("empty"), null).kind, "ok");
  });

  test("queue null (query failed) → unknown, never ok", () => {
    assert.equal(rcm.notMoving(fixture("errors"), "alice-laptop").kind, "unknown");
  });

  test("old server without a reason field on any row → unknown", () => {
    const s = fixture("main");
    for (const r of s.pools[0].queue) delete r.reason;
    assert.equal(rcm.notMoving(s, null).kind, "unknown");
  });

  test("one row missing its reason → not ok", () => {
    const s = fixture("main");
    Object.assign(job(s, 413), { reason: "waiting_for_lane", ahead_job_id: 409, blocked_by: null });
    delete job(s, 414).reason;
    assert.notEqual(rcm.notMoving(s, null).kind, "ok");
  });

  test("a running job with a failed step that keeps running is not listed", () => {
    const s = fixture("main");
    Object.assign(job(s, 413), { reason: "waiting_for_lane", ahead_job_id: 409, blocked_by: null });
    const p = job(s, 412).progress;
    p.failed_step = "format";
    p.steps[1].ok = false;
    assert.equal(rcm.notMoving(s, "alice-laptop").kind, "ok");
  });

  test("me does not change what is listed", () => {
    const a = rcm.notMoving(fixture("main"), "alice-laptop");
    const b = rcm.notMoving(fixture("main"), null);
    assert.equal(a.kind, b.kind);
    assert.deepEqual(a.lines.map((l) => l.jobId), b.lines.map((l) => l.jobId));
  });
});

describe("yourJobs", () => {
  test("requester → both active jobs, running first, with ETA and joiner count / position", () => {
    const r = rcm.yourJobs(fixture("main"), "alice-laptop");
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [412, 414]);
    assert.equal(r.lines[0].text, "running · ETA 09:57 · in 5m 10s · +1 joined");
    assert.equal(r.lines[1].text, "2nd in line · ETA 09:58 · waiting for lane");
    assert.equal(r.more, 0);
  });

  test("joiner counts as mine", () => {
    const r = rcm.yourJobs(fixture("main"), "eve-ci");
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [412]);
    assert.equal(r.more, 0);
  });

  test("no token → no_token", () => {
    assert.equal(rcm.yourJobs(fixture("main"), null).kind, "no_token");
    assert.equal(rcm.yourJobs(fixture("main"), undefined).kind, "no_token");
    assert.equal(rcm.yourJobs(fixture("main"), "").kind, "no_token");
  });

  test("token with no jobs → none", () => {
    assert.equal(rcm.yourJobs(fixture("main"), "nobody").kind, "none");
    assert.equal(rcm.yourJobs(fixture("empty"), "alice-laptop").kind, "none");
  });

  test("matches requester.name, not the label", () => {
    assert.equal(rcm.yourJobs(fixture("main"), "alice@laptop").kind, "none");
  });

  test("queue null → unknown", () => {
    assert.equal(rcm.yourJobs(fixture("errors"), "alice-laptop").kind, "unknown");
  });

  test("more than two → first two plus a count; uploading counts as active", () => {
    const s = fixture("main");
    const extra = rowLike(s, 415, { id: 416, position: 4, requester: { name: "alice-laptop", label: "alice@laptop" } });
    s.pools[0].queue.push(extra);
    const r = rcm.yourJobs(s, "alice-laptop");
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [412, 414]);
    assert.equal(r.more, 1);
  });

  test("order follows the display sort, not the input order", () => {
    const s = fixture("main");
    const q = s.pools[0].queue;
    s.pools[0].queue = [q[3], q[4], q[2], q[1], q[0]];
    const r = rcm.yourJobs(s, "alice-laptop");
    assert.deepEqual(r.lines.map((l) => l.jobId), [412, 414]);
  });
});

describe("hostPressure", () => {
  const host = () => fixture("main").pools[0].hosts[0];

  test("main sample → fine with CPU 21, Mem 58, GPU 13, load 3.5 / 10", () => {
    assert.deepEqual(rcm.hostPressure(host()), { cpu: 21, mem: 58, gpu: 13, load: "3.5 / 10", verdict: "fine" });
  });

  test("85% or more on any value → busy", () => {
    const h = host();
    h.cpu.busy = 90;
    let r = rcm.hostPressure(h);
    assert.equal(r.verdict, "busy");
    assert.equal(r.cpu, 90);

    const h2 = host();
    h2.cpu.busy = 85;
    assert.equal(rcm.hostPressure(h2).verdict, "busy");

    const h3 = host();
    h3.cpu.busy = 84;
    assert.equal(rcm.hostPressure(h3).verdict, "fine");

    const h4 = host();
    h4.memory.used_bytes = 23000000000; // 89%
    r = rcm.hostPressure(h4);
    assert.equal(r.verdict, "busy");
    assert.equal(r.mem, 89);

    const h5 = host();
    h5.gpu.util_pct = 85;
    assert.equal(rcm.hostPressure(h5).verdict, "busy");
  });

  test("any value null → partial (never fine)", () => {
    const h = host();
    h.gpu = null;
    let r = rcm.hostPressure(h);
    assert.equal(r.verdict, "partial");
    assert.equal(r.gpu, null);
    assert.equal(r.cpu, 21);

    const h2 = host();
    h2.gpu.util_pct = null;
    assert.equal(rcm.hostPressure(h2).verdict, "partial");

    const h3 = host();
    h3.cpu = null;
    r = rcm.hostPressure(h3);
    assert.equal(r.verdict, "partial");
    assert.equal(r.cpu, null);

    const h4 = host();
    h4.memory = null;
    r = rcm.hostPressure(h4);
    assert.equal(r.verdict, "partial");
    assert.equal(r.mem, null);
  });

  test("busy wins over partial", () => {
    const h = host();
    h.cpu.busy = 90;
    h.gpu = null;
    assert.equal(rcm.hostPressure(h).verdict, "busy");
  });

  test("all of cpu, memory, gpu null → unknown", () => {
    const h = host();
    h.cpu = null;
    h.memory = null;
    h.gpu = null;
    const r = rcm.hostPressure(h);
    assert.equal(r.verdict, "unknown");
    assert.equal(r.cpu, null);
    assert.equal(r.mem, null);
    assert.equal(r.gpu, null);
  });

  test("load or cores unknown → — in the load text; the verdict is about the three percentages only", () => {
    const h = host();
    h.load = null;
    let r = rcm.hostPressure(h);
    assert.equal(r.load, DASH);
    assert.equal(r.verdict, "fine");

    const h2 = host();
    h2.cores = null;
    r = rcm.hostPressure(h2);
    assert.equal(r.load, "3.5 / —");
    assert.equal(r.verdict, "fine");
  });

  test("no sample → no_sample", () => {
    assert.equal(rcm.hostPressure(null).verdict, "no_sample");
    assert.equal(rcm.hostPressure(undefined).verdict, "no_sample");
    assert.equal(rcm.hostPressure(fixture("empty").pools[0].hosts[0]).verdict, "no_sample");
  });
});

describe("queueHeader", () => {
  test("main → counts, oldest waiting, lanes busy", () => {
    assert.equal(rcm.queueHeader(fixture("main"), NOW),
      "5 jobs · 2 running · 3 waiting · oldest waiting 1m 35s · lanes 2/2 busy");
  });

  test("cancelling counts as running", () => {
    const s = fixture("main");
    Object.assign(job(s, 412), { state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "alice-laptop", kill_at: fromNow(8) } });
    assert.equal(rcm.queueHeader(s, NOW),
      "5 jobs · 2 running · 3 waiting · oldest waiting 1m 35s · lanes 2/2 busy");
  });

  test("empty queue → zeros, no 'oldest waiting'", () => {
    assert.equal(rcm.queueHeader(fixture("empty"), NOW), "0 jobs · 0 running · 0 waiting · lanes 0/2 busy");
  });

  test("single lane", () => {
    assert.equal(rcm.queueHeader(fixture("single-lane"), NOW),
      "2 jobs · 1 running · 1 waiting · oldest waiting 40s · lanes 1/1 busy");
  });

  test("queue null → 'unknown' (the banner replaces the header; never a count)", () => {
    assert.equal(rcm.queueHeader(fixture("errors"), NOW), "unknown");
  });
});

describe("sortQueue", () => {
  test("running → cancelling → waiting by position; running by lane", () => {
    const s = fixture("main");
    const q = s.pools[0].queue;
    const shuffled = [q[4], q[3], q[1], q[2], q[0]]; // 415 414 409 413 412
    assert.deepEqual(rcm.sortQueue(shuffled).map((r) => r.id), [412, 409, 413, 414, 415]);
  });

  test("cancelling sits between running and waiting", () => {
    const s = fixture("main");
    const q = s.pools[0].queue;
    Object.assign(job(s, 412), { state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "alice-laptop", kill_at: fromNow(8) } });
    const shuffled = [q[0], q[4], q[3], q[2], q[1]]; // 412(cancelling) 415 414 413 409
    assert.deepEqual(rcm.sortQueue(shuffled).map((r) => r.id), [409, 412, 413, 414, 415]);
  });

  test("does not mutate its input", () => {
    const s = fixture("main");
    const q = s.pools[0].queue;
    const input = [q[4], q[3], q[1], q[2], q[0]];
    const before = input.map((r) => r.id);
    rcm.sortQueue(input);
    assert.deepEqual(input.map((r) => r.id), before);
  });

  test("null → []", () => {
    assert.deepEqual(rcm.sortQueue(null), []);
  });
});

describe("workerPills", () => {
  // text 와 cls 만 계약이다 — 구현이 jobId·lane 같은 필드를 더 실어도 된다.
  const pills = (server) => rcm.workerPills(server).map(({ text, cls }) => ({ text, cls }));

  test("single lane busy → one pill 'worker busy #412'", () => {
    assert.deepEqual(pills(fixture("single-lane").server), [{ text: "worker busy #412", cls: "busy" }]);
  });

  test("single lane idle → 'worker idle'", () => {
    const server = fixture("single-lane").server;
    server.workers[0] = { lane: 1, state: "idle", job_id: null, error: null, since: fromNow(-100) };
    assert.deepEqual(pills(server), [{ text: "worker idle", cls: "idle" }]);
  });

  test("two lanes busy → a pill per lane with the job id", () => {
    assert.deepEqual(pills(fixture("main").server),
      [{ text: "#412", cls: "busy" }, { text: "#409", cls: "busy" }]);
  });

  test("two lanes idle → 'lane N · idle'", () => {
    assert.deepEqual(pills(fixture("empty").server),
      [{ text: "lane 1 · idle", cls: "idle" }, { text: "lane 2 · idle", cls: "idle" }]);
  });

  test("down lane and paused server → down pill first, paused pill last", () => {
    assert.deepEqual(pills(fixture("paused-down").server), [
      { text: "lane 1 · down", cls: "down" },
      { text: "#409", cls: "busy" },
      { text: "paused", cls: "paused" },
    ]);
  });

  test("single lane and paused → worker pill plus paused pill", () => {
    const server = fixture("single-lane").server;
    server.paused = { by: "macmini-admin", at: fromNow(-132) };
    assert.deepEqual(pills(server), [
      { text: "worker busy #412", cls: "busy" },
      { text: "paused", cls: "paused" },
    ]);
  });
});

describe("headerNote", () => {
  test("first load (no prev) → null", () => {
    assert.equal(rcm.headerNote(fixture("main"), NOW, null), null);
    assert.equal(rcm.headerNote(fixture("main"), NOW, undefined), null);
  });

  test("same version and growing uptime → null", () => {
    const prev = fixture("main");
    const cur = fixture("main");
    cur.server.uptime_seconds = prev.server.uptime_seconds + 5;
    assert.equal(rcm.headerNote(cur, NOW, prev), null);
    assert.equal(rcm.headerNote(fixture("main"), NOW, fixture("main")), null);
  });

  test("uptime decreased → restart notice with the restart clock (generated_at − uptime)", () => {
    const prev = fixture("main");
    const cur = fixture("main");
    cur.server.uptime_seconds = 30; // 00:52:12Z − 30s = 09:51:42 KST
    assert.equal(rcm.headerNote(cur, NOW, prev), "Server restarted at 09:51 — running jobs were marked lost");
  });

  test("schema_version changed → out of date", () => {
    const prev = fixture("main");
    const cur = fixture("main");
    cur.schema_version = 2;
    assert.equal(rcm.headerNote(cur, NOW, prev), "UI out of date — reload");
  });

  test("server.version changed → out of date", () => {
    const prev = fixture("main");
    const cur = fixture("main");
    cur.server.version = "0.2.0";
    assert.equal(rcm.headerNote(cur, NOW, prev), "UI out of date — reload");
  });

  test("version change wins over a restart", () => {
    const prev = fixture("main");
    const cur = fixture("main");
    cur.server.version = "0.2.0";
    cur.server.uptime_seconds = 30;
    assert.equal(rcm.headerNote(cur, NOW, prev), "UI out of date — reload");
  });

  test("uptime unknown on either side → no restart claim", () => {
    const prev = fixture("main");
    const cur = fixture("main");
    cur.server.uptime_seconds = null;
    assert.equal(rcm.headerNote(cur, NOW, prev), null);
    prev.server.uptime_seconds = null;
    cur.server.uptime_seconds = 30;
    assert.equal(rcm.headerNote(cur, NOW, prev), null);
  });
});
