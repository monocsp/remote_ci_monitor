"use strict";
// 진행(항목 12) · 최근 완료(14) · 재실행 명령 · 전이 이력.
// 마커가 없는 잡은 0/0 이 아니라 「no step markers」, 시작 전 취소는 소요 — 로 그린다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, recentJob, NOW, TZ } = require("./helpers");

const rcm = load();
const DASH = "—";

const status = fixture("main");
const prog = (id) => structuredClone(job(status, id).progress);
const recent = (id) => structuredClone(recentJob(status, id));

describe("progressHead", () => {
  test("executing with markers → 'step N/M · name · step time · job time'", () => {
    assert.equal(rcm.progressHead(prog(412)), "step 5/8 · test · 51s · job 59s");
  });

  test("steps_total_partial → '(so far)' after the count", () => {
    const p = prog(412);
    p.steps_total_partial = true;
    assert.equal(rcm.progressHead(p), "step 5/8 (so far) · test · 51s · job 59s");
    assert.equal(rcm.progressHead(prog(409)), "step 3/3 (so far) · boot-simulators · 3m 20s · job 6m 20s");
  });

  test("a failed step while the job keeps running → ' · 1 step failed'", () => {
    const p = prog(412);
    p.failed_step = "format";
    p.steps[1].ok = false;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job 59s · 1 step failed");
  });

  test("two failed steps → ' · 2 steps failed'", () => {
    const p = prog(412);
    p.failed_step = "lint";
    p.steps[1].ok = false;
    p.steps[2].ok = false;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job 59s · 2 steps failed");
  });

  test("no markers → 'no step markers · job N'", () => {
    const p = prog(412);
    Object.assign(p, { steps: [], steps_total: null, steps_done: 0, current_index: null, current_name: null, current_seconds: null });
    assert.equal(rcm.progressHead(p), "no step markers · job 59s");
  });

  test("materializing → null (the Reason column carries it)", () => {
    const p = prog(412);
    Object.assign(p, { phase: "materializing", steps: [], steps_total: null, current_name: null });
    assert.equal(rcm.progressHead(p), null);
  });

  test("progress null (queued/uploading) → null", () => {
    assert.equal(rcm.progressHead(null), null);
    assert.equal(rcm.progressHead(undefined), null);
  });

  test("job_seconds unknown → 'job —'", () => {
    const p = prog(412);
    p.job_seconds = null;
    assert.equal(rcm.progressHead(p), "step 5/8 · test · 51s · job —");
  });
});

describe("stepMark", () => {
  test("done → ✔ (ok true or unknown)", () => {
    assert.equal(rcm.stepMark({ index: 1, name: "analyze", state: "done", ok: true, seconds: 12 }), "✔");
    assert.equal(rcm.stepMark({ index: 1, name: "analyze", state: "done", ok: null, seconds: 12 }), "✔");
  });

  test("running → ▶", () => {
    assert.equal(rcm.stepMark({ index: 5, name: "test", state: "running", ok: null, seconds: 51 }), "▶");
  });

  test("ok === false → ✘", () => {
    assert.equal(rcm.stepMark({ index: 2, name: "format", state: "done", ok: false, seconds: 3 }), "✘");
  });

  test("pending (filled in from the declared total) → ·", () => {
    assert.equal(rcm.stepMark({ state: "pending" }), "·");
    assert.equal(rcm.stepMark(null), "·");
    assert.equal(rcm.stepMark(undefined), "·");
  });
});

describe("recentLine", () => {
  // 계약 필드: pill · duration · when · summary. key·requester 는 렌더 층이 job 에서 직접 읽는다.
  test("failed · exit 1 with summary and failed step", () => {
    const r = rcm.recentLine(recent(411), TZ, NOW);
    assert.equal(r.pill, "failed · exit 1");
    assert.equal(r.duration, "1m 02s");
    assert.equal(r.when, "09:47");
    assert.equal(r.summary, "2 tests failed · step test");
  });

  test("succeeded → no exit code in the pill", () => {
    const r = rcm.recentLine(recent(410), TZ, NOW);
    assert.equal(r.pill, "succeeded");
    assert.equal(r.duration, "5m 50s");
    assert.equal(r.when, "09:40");
    assert.equal(r.summary, "all 9 packages green");
  });

  test("cancelled before start → duration —, 'before start · by <label>'", () => {
    const r = rcm.recentLine(recent(408), TZ, NOW);
    assert.equal(r.pill, "cancelled · exit 2");
    assert.equal(r.duration, DASH);
    assert.equal(r.when, "09:31");
    assert.ok(r.summary.includes("before start"), r.summary);
    assert.ok(r.summary.includes("by carol@mbp"), r.summary);
  });

  test("cancelled before start without a server summary → derived from started_at null", () => {
    const j = recent(408);
    j.summary = null;
    const r = rcm.recentLine(j, TZ, NOW);
    assert.ok(r.summary.includes("before start"), r.summary);
    assert.ok(r.summary.includes("by carol@mbp"), r.summary);
  });

  test("cancelled_by that is not the requester → raw token name", () => {
    const j = recent(408);
    j.cancelled_by = "macmini-admin";
    assert.ok(rcm.recentLine(j, TZ, NOW).summary.includes("by macmini-admin"));
  });

  test("timed out · exit 2 with the server's limit summary and failed step", () => {
    const r = rcm.recentLine(recent(407), TZ, NOW);
    assert.equal(r.pill, "timed out · exit 2");
    assert.equal(r.duration, "20m 00s");
    assert.equal(r.when, "09:22");
    assert.equal(r.summary, "limit 20m · step test");
  });

  test("lost · exit 3 → duration —, summary from the server", () => {
    const r = rcm.recentLine(recent(406), TZ, NOW);
    assert.equal(r.pill, "lost · exit 3");
    assert.equal(r.duration, DASH);
    assert.equal(r.when, "09:02");
    assert.ok(r.summary.includes("server restarted 09:02"), r.summary);
  });

  test("exit_code null (materialize failure) → plain 'failed', duration —, other-day clock", () => {
    const r = rcm.recentLine(recent(403), TZ, NOW);
    assert.equal(r.pill, "failed");
    assert.equal(r.duration, DASH);
    assert.equal(r.when, "Sep 3 · 23:40");
    assert.equal(r.summary, "tar rejected: absolute path in archive");
  });

  test("no summary and no failed step → empty summary", () => {
    const r = rcm.recentLine(recent(405), TZ, NOW);
    assert.equal(r.pill, "succeeded");
    assert.equal(r.when, "08:31");
    assert.equal(r.summary, "");
  });

  test("job null → dashes", () => {
    const r = rcm.recentLine(null, TZ, NOW);
    assert.equal(r.duration, DASH);
    assert.equal(r.when, DASH);
  });
});

describe("rerunCommand", () => {
  test("preset with inputs → -f key=value", () => {
    assert.equal(rcm.rerunCommand(recent(411)), "rcm run gate -f scope=fast");
    assert.equal(rcm.rerunCommand(recent(407)), "rcm run gate -f scope=full");
  });

  test("no inputs → bare preset", () => {
    assert.equal(rcm.rerunCommand({ preset: "gate", inputs: {} }), "rcm run gate");
    assert.equal(rcm.rerunCommand({ preset: "gate", inputs: null }), "rcm run gate");
    assert.equal(rcm.rerunCommand({ preset: "gate" }), "rcm run gate");
  });

  test("bool and int inputs, insertion order", () => {
    assert.equal(rcm.rerunCommand({ preset: "gate", inputs: { verbose: true } }), "rcm run gate -f verbose=true");
    assert.equal(rcm.rerunCommand({ preset: "gate", inputs: { scope: "fast", verbose: true, n: 3 } }),
      "rcm run gate -f scope=fast -f verbose=true -f n=3");
  });

  test("job null or preset unknown → —", () => {
    assert.equal(rcm.rerunCommand(null), DASH);
    assert.equal(rcm.rerunCommand({ inputs: { scope: "fast" } }), DASH);
  });
});

describe("transitionsLine", () => {
  test("fixture failed job → HH:MM:SS clocks, queued shows its wait, exit code at the end", () => {
    assert.equal(rcm.transitionsLine(recent(411), TZ),
      "uploading 09:45:40 → queued (waited 21s) → running 09:46:01 → failed 09:47:03 · exit 1");
  });

  test("§2 literal example", () => {
    const j = {
      id: 999, state: "failed", exit_code: 1, waited_seconds: 21,
      transitions: [
        { state: "uploading", at: "2026-09-04T00:50:40Z" },
        { state: "queued", at: "2026-09-04T00:50:52Z" },
        { state: "running", at: "2026-09-04T00:51:13Z" },
        { state: "failed", at: "2026-09-04T00:52:15Z" },
      ],
    };
    assert.equal(rcm.transitionsLine(j, TZ),
      "uploading 09:50:40 → queued (waited 21s) → running 09:51:13 → failed 09:52:15 · exit 1");
  });

  test("timed_out uses the state word", () => {
    assert.equal(rcm.transitionsLine(recent(407), TZ),
      "uploading 09:02:15 → queued (waited 15s) → running 09:02:30 → timed out 09:22:30 · exit 2");
  });

  test("cancelled before running → queued keeps its clock (no wait to report)", () => {
    assert.equal(rcm.transitionsLine(recent(408), TZ),
      "uploading 09:30:40 → queued 09:30:40 → cancelled 09:31:00 · exit 2");
  });

  test("exit_code null → no exit suffix", () => {
    assert.equal(rcm.transitionsLine(recent(403), TZ), "uploading 23:38:00 → failed 23:40:00");
  });

  test("no transitions → —", () => {
    assert.equal(rcm.transitionsLine({ id: 1, state: "failed", exit_code: 1, transitions: [] }, TZ), DASH);
    assert.equal(rcm.transitionsLine({ id: 1, state: "failed", exit_code: 1, transitions: null }, TZ), DASH);
    assert.equal(rcm.transitionsLine(null, TZ), DASH);
  });
});
