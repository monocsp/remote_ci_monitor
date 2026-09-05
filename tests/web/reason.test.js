"use strict";
// 큐 행의 Reason · 신뢰도 배지 · ETA · Elapsed 칸(목업 항목 10·11, workplan §2).
// 서버가 준 reason · estimate.confidence 를 그대로 그린다 — UI 는 다시 계산하지 않는다(결정 G).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, fromNow, NOW, TZ } = require("./helpers");

const rcm = load();
const DASH = "—";

// reasonText 의 세 번째 인자는 status 전체 — busy 수를 status.server.workers 로 센다(§2).
const status = fixture("main");
const row = (id) => job(status, id);

// 픽스처 행을 바탕으로 변형 행을 만든다(원본은 건드리지 않는다).
function variant(id, patch) {
  const base = structuredClone(row(id));
  return Object.assign(base, patch);
}

describe("reasonText — normal (not actionable)", () => {
  test("running → 'running · lane N'", () => {
    const r = rcm.reasonText(row(412), NOW, status);
    assert.equal(r.text, "running · lane 1");
    assert.equal(r.actionable, false);
    assert.deepEqual(r.links, []);
    assert.equal(rcm.reasonText(row(409), NOW, status).text, "running · lane 2");
  });

  test("running with lane unknown → 'running'", () => {
    const r = rcm.reasonText(variant(412, { lane: null }), NOW, status);
    assert.equal(r.text, "running");
  });

  test("waiting_for_lane → busy count from server.workers, ahead job, frees in wait_seconds", () => {
    const r = rcm.reasonText(row(414), NOW, status);
    assert.equal(r.text, "waiting for lane · 2/2 busy · behind #412 · frees in 5m 10s");
    assert.equal(r.actionable, false);
    assert.deepEqual(r.links, [{ jobId: 412 }]);
  });

  test("waiting_for_lane without server → still names the job ahead", () => {
    const r = rcm.reasonText(row(414), NOW);
    assert.ok(r.text.startsWith("waiting for lane"), r.text);
    assert.ok(r.text.includes("behind #412 · frees in 5m 10s"), r.text);
  });

  test("waiting_for_lane with no ahead job and no wait → only the busy count", () => {
    const v = variant(414, { ahead_job_id: null });
    v.estimate.wait_seconds = null;
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.text, "waiting for lane · 2/2 busy");
    assert.deepEqual(r.links, []);
  });

  test("uploading → 'uploading · received / total MB'", () => {
    const r = rcm.reasonText(row(415), NOW, status);
    assert.equal(r.text, "uploading · 30 / 48 MB");
    assert.equal(r.actionable, false);
    assert.deepEqual(r.links, []);
  });

  test("uploading with received_bytes unknown → — in its place", () => {
    const v = variant(415, {});
    v.source.received_bytes = null;
    assert.equal(rcm.reasonText(v, NOW, status).text, "uploading · — / 48 MB");
  });

  test("materializing (tree) → 'preparing workspace · unpacking 48 MB'", () => {
    const v = variant(412, { reason: "materializing" });
    v.progress = { timing: "as_received", phase: "materializing", last_output_at: null, steps_total: null,
      steps_total_partial: false, steps_done: 0, current_index: null, current_name: null,
      current_seconds: null, job_seconds: 4, failed_step: null, steps: [] };
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.text, "preparing workspace · unpacking 48 MB");
    assert.equal(r.actionable, false);
  });

  test("materializing (git_ref) → 'preparing workspace · fetching <ref>'", () => {
    const v = variant(409, { reason: "materializing" });
    v.progress.phase = "materializing";
    v.progress.steps = [];
    assert.equal(rcm.reasonText(v, NOW, status).text, "preparing workspace · fetching dev");
  });

  test("materializing with unknown size → 'preparing workspace'", () => {
    const v = variant(412, { reason: "materializing" });
    v.source.bytes = null;
    assert.equal(rcm.reasonText(v, NOW, status).text, "preparing workspace");
  });

  test("cancelling → SIGTERM sender (label of the requester) and kill countdown", () => {
    const v = variant(412, {
      state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "alice-laptop", kill_at: fromNow(8) },
    });
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.text, "SIGTERM sent by alice@laptop · kill in 8s");
    assert.equal(r.actionable, false);
  });

  test("cancelling by someone who is not the requester → raw token name", () => {
    const v = variant(412, {
      state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "macmini-admin", kill_at: fromNow(8) },
    });
    assert.equal(rcm.reasonText(v, NOW, status).text, "SIGTERM sent by macmini-admin · kill in 8s");
  });

  test("cancelling with kill_at unknown → 'kill —' (the fragment stays, the number is unknown)", () => {
    const v = variant(412, {
      state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "alice-laptop", kill_at: null },
    });
    assert.equal(rcm.reasonText(v, NOW, status).text, "SIGTERM sent by alice@laptop · kill —");
  });
});

describe("reasonText — actionable", () => {
  test("blocked_by_group → ⛓ blocker, group, frees in remaining", () => {
    const r = rcm.reasonText(row(413), NOW, status);
    assert.equal(r.text, "⛓ blocked by #409 · devices · frees in 2m 40s");
    assert.equal(r.actionable, true);
    assert.deepEqual(r.links, [{ jobId: 409 }]);
  });

  test("blocked_by_group with remaining unknown → 'frees in —'", () => {
    const v = variant(413, {});
    v.blocked_by.remaining_seconds = null;
    assert.equal(rcm.reasonText(v, NOW, status).text, "⛓ blocked by #409 · devices · frees in —");
  });

  test("upload_stalled → coarse time since last_received_at + received / total", () => {
    const v = variant(415, { reason: "upload_stalled" });
    v.source.last_received_at = fromNow(-120);
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.text, "upload stalled 2m · 30 / 48 MB");
    assert.equal(r.actionable, true);
    assert.deepEqual(r.links, []);
  });

  test("upload_stalled under a minute → seconds", () => {
    const v = variant(415, { reason: "upload_stalled" });
    v.source.last_received_at = fromNow(-45);
    assert.equal(rcm.reasonText(v, NOW, status).text, "upload stalled 45s · 30 / 48 MB");
  });

  test("upload_stalled with last_received_at unknown → 'upload stalled —'", () => {
    const v = variant(415, { reason: "upload_stalled" });
    v.source.last_received_at = null;
    assert.equal(rcm.reasonText(v, NOW, status).text, "upload stalled — · 30 / 48 MB");
  });

  test("overdue → 'over by (elapsed − expected) · expected N'", () => {
    const v = variant(412, { reason: "overdue" });
    Object.assign(v.estimate, { confidence: "overdue", elapsed_seconds: 580, remaining_seconds: 30,
      overdue: true, finish_at: null });
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.text, "over by 3m 31s · expected 6m 09s");
    assert.equal(r.actionable, true);
    assert.deepEqual(r.links, []);
  });

  test("stuck → ⚠ multiplier (floor of elapsed/expected) and coarse silence since last_output_at", () => {
    const v = variant(412, { reason: "stuck" });
    Object.assign(v.estimate, { confidence: "overdue", elapsed_seconds: 1150, remaining_seconds: 30,
      overdue: true, stuck: true, finish_at: null });
    v.progress.last_output_at = fromNow(-250);
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.text, "⚠ likely stuck · 3× expected · no output for 4m");
    assert.equal(r.actionable, true);
  });

  test("stuck without last_output_at → multiplier only", () => {
    const v = variant(412, { reason: "stuck" });
    Object.assign(v.estimate, { confidence: "overdue", elapsed_seconds: 1150, stuck: true, overdue: true, finish_at: null });
    v.progress.last_output_at = null;
    assert.equal(rcm.reasonText(v, NOW, status).text, "⚠ likely stuck · 3× expected");
    v.progress = null;
    assert.equal(rcm.reasonText(v, NOW, status).text, "⚠ likely stuck · 3× expected");
  });

  test("paused → 'paused'", () => {
    const paused = fixture("paused-down");
    const r = rcm.reasonText(job(paused, 413), NOW, paused);
    assert.equal(r.text, "paused");
    assert.equal(r.actionable, true);
    assert.deepEqual(r.links, []);
  });

  test("not_scheduled → 'not scheduled'", () => {
    const r = rcm.reasonText(variant(414, { reason: "not_scheduled" }), NOW, status);
    assert.equal(r.text, "not scheduled");
    assert.equal(r.actionable, true);
  });

  test("worker_down → 'no worker'", () => {
    const r = rcm.reasonText(variant(414, { reason: "worker_down" }), NOW, status);
    assert.equal(r.text, "no worker");
    assert.equal(r.actionable, true);
  });
});

describe("reasonText — unknown and null", () => {
  test("unrecognised reason → 'unknown', not actionable", () => {
    const r = rcm.reasonText(variant(414, { reason: "frobnicate" }), NOW, status);
    assert.equal(r.text, "unknown");
    assert.equal(r.actionable, false);
    assert.deepEqual(r.links, []);
  });

  test("reason null or missing (old server) → 'unknown'", () => {
    assert.equal(rcm.reasonText(variant(414, { reason: null }), NOW, status).text, "unknown");
    const v = variant(414, {});
    delete v.reason;
    assert.equal(rcm.reasonText(v, NOW, status).text, "unknown");
  });

  test("row null → 'unknown'", () => {
    const r = rcm.reasonText(null, NOW, status);
    assert.equal(r.text, "unknown");
    assert.equal(r.actionable, false);
    assert.deepEqual(r.links, []);
  });

  test("actionable is exactly the Not-moving set; links is always an array", () => {
    const expect = {
      worker_down: true, stuck: true, upload_stalled: true, not_scheduled: true,
      blocked_by_group: true, overdue: true, paused: true,
      running: false, waiting_for_lane: false, uploading: false, materializing: false, cancelling: false,
    };
    for (const [reason, actionable] of Object.entries(expect)) {
      const r = rcm.reasonText(variant(414, { reason }), NOW, status);
      assert.equal(r.actionable, actionable, reason);
      assert.ok(Array.isArray(r.links), `${reason} links`);
    }
  });
});

describe("confidenceBadge", () => {
  test("high · measured n=7", () => {
    assert.deepEqual(rcm.confidenceBadge(row(412).estimate), { cls: "high", text: "high · measured n=7" });
  });

  test("med · measured n=3", () => {
    assert.deepEqual(rcm.confidenceBadge(row(409).estimate), { cls: "med", text: "med · measured n=3" });
  });

  test("low · preset", () => {
    const est = job(fixture("paused-down"), 413).estimate;
    assert.deepEqual(rcm.confidenceBadge(est), { cls: "low", text: "low · preset" });
  });

  test("low · default", () => {
    const est = { confidence: "low", expected_seconds: 600, source: "default", sample_count: 0,
      elapsed_seconds: null, waited_seconds: 5, remaining_seconds: 600, wait_seconds: 0,
      overdue: false, stuck: false, finish_at: fromNow(600) };
    assert.deepEqual(rcm.confidenceBadge(est), { cls: "low", text: "low · default" });
  });

  test("group wait → cls low", () => {
    assert.deepEqual(rcm.confidenceBadge(row(413).estimate), { cls: "low", text: "low · group wait" });
  });

  test("overdue (also for stuck) → cls over, text 'overdue'", () => {
    const est = structuredClone(row(412).estimate);
    Object.assign(est, { confidence: "overdue", elapsed_seconds: 580, overdue: true, finish_at: null });
    assert.deepEqual(rcm.confidenceBadge(est), { cls: "over", text: "overdue" });
    Object.assign(est, { elapsed_seconds: 1150, stuck: true });
    assert.deepEqual(rcm.confidenceBadge(est), { cls: "over", text: "overdue" });
  });

  test("estimate null → cls low, text 'low · —'", () => {
    assert.deepEqual(rcm.confidenceBadge(null), { cls: "low", text: `low · ${DASH}` });
  });
});

describe("etaText", () => {
  test("running → clock + 'in remaining'", () => {
    assert.deepEqual(rcm.etaText(row(412), TZ, NOW), { clock: "09:57", rel: "in 5m 10s" });
  });

  test("queued (waiting for lane) → clock + 'in wait + remaining'", () => {
    assert.deepEqual(rcm.etaText(row(414), TZ, NOW), { clock: "09:58", rel: "in 6m 15s" });
  });

  test("uploading → rel is wait + remaining", () => {
    assert.equal(rcm.etaText(row(415), TZ, NOW).rel, "in 12m 24s");
  });

  test("blocked by group → 'after #409' + '~clock'", () => {
    assert.deepEqual(rcm.etaText(row(413), TZ, NOW), { clock: "after #409", rel: "~10:04" });
  });

  test("blocked by group with finish_at unknown → clock — (finish_at null always wins, §2)", () => {
    const v = variant(413, {});
    v.estimate.finish_at = null;
    assert.deepEqual(rcm.etaText(v, TZ, NOW), { clock: DASH, rel: null });
  });

  test("finish_at null (overdue) → clock —, no rel", () => {
    const v = variant(412, { reason: "overdue" });
    Object.assign(v.estimate, { confidence: "overdue", elapsed_seconds: 580, overdue: true, finish_at: null });
    assert.deepEqual(rcm.etaText(v, TZ, NOW), { clock: DASH, rel: null });
  });

  test("finish_at null (paused) → clock —, no rel", () => {
    const paused = fixture("paused-down");
    assert.deepEqual(rcm.etaText(job(paused, 413), TZ, NOW), { clock: DASH, rel: null });
  });

  test("row null → clock —", () => {
    assert.deepEqual(rcm.etaText(null, TZ, NOW), { clock: DASH, rel: null });
  });
});

describe("elapsedText", () => {
  test("running → main elapsed, sub 'waited N'", () => {
    assert.deepEqual(rcm.elapsedText(row(412), NOW), { main: "59s", sub: "waited 33s" });
    assert.deepEqual(rcm.elapsedText(row(409), NOW), { main: "6m 20s", sub: "waited 8s" });
  });

  test("running that waited 0s → no sub line", () => {
    const v = variant(412, {});
    v.estimate.waited_seconds = 0;
    v.created_at = v.started_at;
    const r = rcm.elapsedText(v, NOW);
    assert.equal(r.main, "59s");
    assert.ok(!r.sub, `sub should be empty, got ${JSON.stringify(r.sub)}`);
  });

  test("cancelling → same as running", () => {
    const v = variant(412, { state: "cancelling", reason: "cancelling",
      cancel: { requested_at: fromNow(-2), by: "alice-laptop", kill_at: fromNow(8) } });
    assert.deepEqual(rcm.elapsedText(v, NOW), { main: "59s", sub: "waited 33s" });
  });

  test("queued → 'waiting N', no sub", () => {
    const r413 = rcm.elapsedText(row(413), NOW);
    assert.equal(r413.main, "waiting 1m 35s");
    assert.ok(!r413.sub);
    assert.equal(rcm.elapsedText(row(414), NOW).main, "waiting 35s");
  });

  test("uploading → —", () => {
    const r = rcm.elapsedText(row(415), NOW);
    assert.equal(r.main, DASH);
    assert.ok(!r.sub);
  });

  test("running with elapsed unknown → —", () => {
    const v = variant(412, { estimate: null, started_at: null });
    assert.equal(rcm.elapsedText(v, NOW).main, DASH);
  });

  test("row null → —", () => {
    assert.equal(rcm.elapsedText(null, NOW).main, DASH);
  });
});
