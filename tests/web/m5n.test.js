"use strict";
// M5n 역할 C — 웹의 순수 함수(docs/m5n-workplan.md §7.3 · §8.3 · §12, 시나리오 문서 N 절).
// 구현보다 먼저 썼다(test-first) — 함수가 없어 빨간 것이 정상이다.
//
// 잠그는 이름(가정 C20): reasonText(기존) · placementNoteText · placementLabel ·
// placementDetailLines · envChangedChip · machineRows · probeSummary · ACTIONABLE · notMoving(기존).
// 모두 window.rcm / module.exports 의 키다. 문장은 카탈로그(i18n.js)에서 오고, 한국어가 기본이다.
// 서버가 보낸 이름(머신 · 프리셋 · 도구 · 프로브 값)은 두 언어 모두 그대로다(AGENTS).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { load, fixture, NOW } = require("./helpers");

const rcm = load();
const MACHINES = path.join(__dirname, "fixtures", "machines-m5n.json");
const machinesDoc = () => JSON.parse(fs.readFileSync(MACHINES, "utf8"));
const machine = (id) => machinesDoc().machines.find((m) => m.id === id);
const H = 3600 * 1000;

function note(worker, state, code, detail, name) {
  return { worker: worker, display_name: name || worker, state: state, code: code, detail: detail || {} };
}

const S4_DETAIL = [
  note("mbp", "ineligible", "tool_missing", { tools: ["fvm"] }),
  note("mac2-worker", "down", "worker_down"),
];

function queued(patch) {
  const status = fixture("main");
  const base = structuredClone(status.pools[0].queue.find((r) => r.id === 414));
  return { status: status, row: Object.assign(base, patch) };
}

describe("ACTIONABLE — §7.3 order", () => {
  test("no_capable_machine and check_failed come right after worker_down", () => {
    assert.deepEqual(rcm.ACTIONABLE, [
      "needs_action", "worker_down", "worker_unreachable_grace", "no_capable_machine", "check_failed", "stuck", "upload_stalled",
      "not_scheduled", "blocked_by_group", "overdue", "paused",
    ]);
  });

  test("check_pending is not actionable (it resolves itself)", () => {
    assert.equal(rcm.ACTIONABLE.indexOf("check_pending"), -1);
  });
});

describe("placementNoteText — one machine's short reason", () => {
  const cases = [
    [note("mbp", "ineligible", "tool_missing", { tools: ["fvm"] }), "mbp: fvm 없음", "mbp: fvm missing"],
    [note("mbp", "ineligible", "tool_missing", { tools: ["fvm", "gitleaks"] }),
      "mbp: fvm, gitleaks 없음", "mbp: fvm, gitleaks missing"],
    [note("mbp", "unknown", "workspace_tool", { tools: ["melos"] }),
      "mbp: melos는 작업 공간에만 있음", "mbp: melos workspace only"],
    [note("@local", "ineligible", "check_failed", { check: "sim-check", job_id: 3620 }, "Mac mini"),
      "Mac mini: sim-check 실패 #3620", "Mac mini: sim-check failed #3620"],
    [note("@local", "unknown", "check_running", { check: "sim-check", job_id: 3650 }, "Mac mini"),
      "Mac mini: sim-check 점검 중", "Mac mini: sim-check running"],
    [note("mbp", "unknown", "check_needed", { check: "sim-check" }),
      "mbp: sim-check 점검 필요", "mbp: need sim-check"],
    [note("mbp", "unknown", "report_missing"), "mbp: 환경 보고 없음", "mbp: no report"],
    [note("mbp", "unknown", "report_stale"), "mbp: 환경 보고 낡음", "mbp: stale report"],
    [note("mbp", "unknown", "fingerprint_unknown"), "mbp: 지문 모름", "mbp: fingerprint unknown"],
    [note("mac2-worker", "down", "worker_down"), "mac2-worker: 꺼짐", "mac2-worker: down"],
    [note("mac2-worker", "down", "report_stale"), "mac2-worker: 꺼짐", "mac2-worker: down"],
    [note("@local", "eligible", "ok", {}, "Mac mini"), "Mac mini: 가능", "Mac mini: eligible"],
    [note("mbp", "unknown", "some_future_code"), "mbp: some_future_code", "mbp: some_future_code"],
  ];
  for (const [n, ko, en] of cases) {
    test(n.code + "/" + n.state + " → " + en, () => {
      assert.equal(rcm.placementNoteText(n, "ko"), ko);
      assert.equal(rcm.placementNoteText(n, "en"), en);
    });
  }

  test("display_name missing → the worker id, never 'undefined'", () => {
    const n = { worker: "mbp", state: "down", code: "worker_down", detail: {} };
    assert.equal(rcm.placementNoteText(n, "en"), "mbp: down");
  });

  test("no lang → English (the pure-function contract)", () => {
    assert.equal(rcm.placementNoteText(note("mac2-worker", "down", "worker_down")), "mac2-worker: down");
  });
});

describe("reasonText — new reasons", () => {
  test("no_capable_machine lists every machine (S4) and is actionable", () => {
    const { status, row } = queued({ reason: "no_capable_machine", placement: "capable", placement_detail: S4_DETAIL });
    const ko = rcm.reasonText(row, NOW, status, "ko");
    assert.equal(ko.text, "실행 가능한 머신 없음 · mbp: fvm 없음 · mac2-worker: 꺼짐");
    assert.equal(ko.actionable, true);
    assert.equal(rcm.reasonText(row, NOW, status, "en").text,
      "no capable machine · mbp: fvm missing · mac2-worker: down");
  });

  test("no_capable_machine without detail says unknown, never invents a reason (§12)", () => {
    for (const detail of [[], undefined]) {
      const { status, row } = queued({ reason: "no_capable_machine", placement: "capable", placement_detail: detail });
      assert.equal(rcm.reasonText(row, NOW, status, "ko").text, "실행 가능한 머신 없음 · 알 수 없음");
      assert.equal(rcm.reasonText(row, NOW, status, "en").text, "no capable machine · unknown");
    }
  });

  test("check_failed names the machine, the check and the job, and is actionable", () => {
    const detail = [note("@local", "ineligible", "check_failed", { check: "sim-check", job_id: 3620 }, "Mac mini")];
    const { status, row } = queued({ key: "scenario-qa", reason: "check_failed", placement_detail: detail });
    const ko = rcm.reasonText(row, NOW, status, "ko");
    assert.equal(ko.text, "머신 점검 실패 · Mac mini: sim-check 실패 #3620");
    assert.equal(ko.actionable, true);
    assert.equal(rcm.reasonText(row, NOW, status, "en").text, "check failed · Mac mini: sim-check failed #3620");
  });

  test("check_failed links to the failed check job", () => {
    const detail = [note("@local", "ineligible", "check_failed", { check: "sim-check", job_id: 3620 }, "Mac mini")];
    const { status, row } = queued({ reason: "check_failed", placement_detail: detail });
    assert.deepEqual(rcm.reasonText(row, NOW, status, "en").links, [{ jobId: 3620 }]);
  });

  test("check_pending → 점검 중 (sim-check), not actionable", () => {
    const detail = [
      note("@local", "unknown", "check_running", { check: "sim-check", job_id: 3650 }, "Mac mini"),
      note("mbp", "unknown", "check_needed", { check: "sim-check" }),
    ];
    const { status, row } = queued({ key: "scenario-qa", reason: "check_pending", placement_detail: detail });
    const ko = rcm.reasonText(row, NOW, status, "ko");
    assert.equal(ko.text, "점검 중 (sim-check)");
    assert.equal(ko.actionable, false);
    assert.equal(rcm.reasonText(row, NOW, status, "en").text, "checking (sim-check)");
  });

  test("check_pending with two different checks names both once, in order", () => {
    const detail = [
      note("@local", "unknown", "check_running", { check: "sim-check" }, "Mac mini"),
      note("mbp", "unknown", "check_needed", { check: "emu-check" }),
      note("mac2-worker", "unknown", "check_needed", { check: "sim-check" }),
    ];
    const { status, row } = queued({ reason: "check_pending", placement_detail: detail });
    assert.equal(rcm.reasonText(row, NOW, status, "ko").text, "점검 중 (sim-check, emu-check)");
  });

  test("check_pending without names → 점검 중", () => {
    const { status, row } = queued({ reason: "check_pending", placement_detail: [] });
    assert.equal(rcm.reasonText(row, NOW, status, "ko").text, "점검 중");
    assert.equal(rcm.reasonText(row, NOW, status, "en").text, "checking");
  });

  test("an any-capable row waiting for a lane ends with 어느 머신이든", () => {
    const { status, row } = queued({ placement: "capable" });
    const ko = rcm.reasonText(row, NOW, status, "ko").text;
    const en = rcm.reasonText(row, NOW, status, "en").text;
    assert.ok(ko.endsWith(" · 어느 머신이든"), ko);
    assert.ok(en.endsWith(" · any machine"), en);
    assert.ok(en.startsWith("waiting for lane"), en);
  });

  test("a pool row waiting for a lane is unchanged", () => {
    const { status, row } = queued({ placement: "pool" });
    assert.equal(rcm.reasonText(row, NOW, status, "en").text,
      "waiting for lane · 2/2 busy · behind #412 · frees in 5m 10s");
  });
});

describe("placementLabel", () => {
  test("any-capable → 어느 머신이든 / any machine", () => {
    assert.equal(rcm.placementLabel({ placement: "capable" }, "ko"), "어느 머신이든");
    assert.equal(rcm.placementLabel({ placement: "capable" }, "en"), "any machine");
  });

  test("a targeted job names its machine", () => {
    assert.equal(rcm.placementLabel({ placement: "pool", target_worker: "mbp" }, "ko"), "mbp 지정");
    assert.equal(rcm.placementLabel({ placement: "pool", target_worker: "mbp" }, "en"), "on mbp");
  });

  test("pool or an old server's row → empty", () => {
    assert.equal(rcm.placementLabel({ placement: "pool" }, "ko"), "");
    assert.equal(rcm.placementLabel({}, "ko"), "");
  });
});

describe("placementDetailLines — the expanded row", () => {
  test("one line per machine in server order", () => {
    const row = { placement_detail: [note("@local", "eligible", "ok", {}, "Mac mini"), ...S4_DETAIL] };
    assert.deepEqual(rcm.placementDetailLines(row, "ko"),
      ["Mac mini: 가능", "mbp: fvm 없음", "mac2-worker: 꺼짐"]);
    assert.deepEqual(rcm.placementDetailLines(row, "en"),
      ["Mac mini: eligible", "mbp: fvm missing", "mac2-worker: down"]);
  });

  test("no detail → no lines (the caller shows nothing, not a guess)", () => {
    assert.deepEqual(rcm.placementDetailLines({}, "ko"), []);
    assert.deepEqual(rcm.placementDetailLines({ placement_detail: null }, "ko"), []);
  });
});

describe("envChangedChip — 환경 바뀜", () => {
  test("changed an hour ago with a failed check → red chip with old → new", () => {
    const chip = rcm.envChangedChip(machine("@local"), NOW, "ko");
    assert.equal(chip.text, "환경 바뀜");
    assert.equal(chip.red, true);
    assert.deepEqual(chip.lines, ["xcode: Xcode 26.5 · Build version 26F75 → Xcode 27.2 · Build version 27B52"]);
    assert.equal(rcm.envChangedChip(machine("@local"), NOW, "en").text, "env changed");
  });

  test("changed with checks passing → not red", () => {
    const m = machine("@local");
    m.checks[0].state = "passed";
    assert.equal(rcm.envChangedChip(m, NOW, "ko").red, false);
  });

  test("24h window: 23h59m shows, 24h01m does not", () => {
    const m = machine("@local");
    m.fingerprint.changed_at = new Date(NOW - 24 * H + 60 * 1000).toISOString();
    assert.ok(rcm.envChangedChip(m, NOW, "ko"));
    m.fingerprint.changed_at = new Date(NOW - 24 * H - 60 * 1000).toISOString();
    assert.equal(rcm.envChangedChip(m, NOW, "ko"), null);
  });

  test("never changed, five days ago, or an unreadable time → no chip", () => {
    assert.equal(rcm.envChangedChip(machine("mbp"), NOW, "ko"), null);
    assert.equal(rcm.envChangedChip(machine("mac2-worker"), NOW, "ko"), null);
    const m = machine("@local");
    m.fingerprint.changed_at = "yesterday";
    assert.equal(rcm.envChangedChip(m, NOW, "ko"), null);
    assert.equal(rcm.envChangedChip({ id: "x" }, NOW, "ko"), null);
  });

  test("a probe that went missing reads 없음 / missing, probes are listed by name", () => {
    const m = machine("@local");
    m.fingerprint.probes.idb = { state: "missing", value: null, previous: "idb_companion 1.1.8" };
    assert.deepEqual(rcm.envChangedChip(m, NOW, "ko").lines, [
      "idb: idb_companion 1.1.8 → 없음",
      "xcode: Xcode 26.5 · Build version 26F75 → Xcode 27.2 · Build version 27B52",
    ]);
    assert.equal(rcm.envChangedChip(m, NOW, "en").lines[0], "idb: idb_companion 1.1.8 → missing");
  });
});

describe("probeSummary", () => {
  test("name + version token, missing, exit code, first word", () => {
    const fp = { probes: {
      xcode: { state: "error", value: "exit 72" },
      java: { state: "ok", value: 'openjdk version "17.0.12" 2024-07-16' },
      idb: { state: "missing", value: null },
      adb: { state: "ok", value: "Android Debug Bridge" },
    } };
    assert.equal(rcm.probeSummary(fp, "en"), "xcode exit 72 · java 17.0.12 · idb missing · adb Android");
    assert.equal(rcm.probeSummary(fp, "ko"), "xcode exit 72 · java 17.0.12 · idb 없음 · adb Android");
  });

  test("no probes → —", () => {
    assert.equal(rcm.probeSummary({ probes: {} }, "ko"), "—");
    assert.equal(rcm.probeSummary(null, "ko"), "—");
  });
});

describe("machineRows — the machines table", () => {
  test("one row per machine, same host label for two workers on one host", () => {
    const rows = rcm.machineRows(machinesDoc(), false, "ko");
    assert.deepEqual(rows.map((r) => r.id), ["@local", "mac2-worker", "mbp"]);
    assert.deepEqual(rows.map((r) => r.name), ["Mac mini", "mac2-worker", "mbp"]);
    assert.equal(rows[0].host, rows[1].host);
    assert.equal(rows[2].host, "mbp (7c20)");
  });

  test("fingerprint · presets · checks texts", () => {
    const ko = rcm.machineRows(machinesDoc(), false, "ko");
    assert.equal(ko[0].fingerprint, "xcode 27.2 · flutter 3.47.8");
    assert.equal(ko[2].fingerprint, "xcode 26.5 · flutter 없음");
    assert.deepEqual(ko.map((r) => r.presets), ["3/4 가능", "3/4 가능", "1/4 가능"]);
    assert.deepEqual(ko.map((r) => r.checks), ["sim-check 실패 #3620", "sim-check 통과", "sim-check 점검 필요"]);
    const en = rcm.machineRows(machinesDoc(), false, "en");
    assert.deepEqual(en.map((r) => r.presets), ["3/4 ok", "3/4 ok", "1/4 ok"]);
    assert.deepEqual(en.map((r) => r.checks), ["sim-check failed #3620", "sim-check passed", "need sim-check"]);
  });

  test("a running check and a machine without checks", () => {
    const doc = machinesDoc();
    doc.machines[2].checks[0] = { preset: "sim-check", state: "running", job_id: 3651, auto_submitted: true, error_code: null };
    doc.machines[1].checks = [];
    const ko = rcm.machineRows(doc, false, "ko");
    assert.equal(ko[2].checks, "sim-check 점검 중 #3651");
    assert.equal(ko[1].checks, "—");
    assert.equal(rcm.machineRows(doc, false, "en")[2].checks, "sim-check running #3651");
  });

  test("다시 점검 only with an admin token, and only where there is a check", () => {
    const doc = machinesDoc();
    doc.machines[2].checks = [];  // 점검 프리셋이 없는 설정이면 checks[] 가 빈다
    assert.deepEqual(rcm.machineRows(doc, false, "ko").map((r) => r.recheck), [false, false, false]);
    assert.deepEqual(rcm.machineRows(doc, true, "ko").map((r) => r.recheck), [true, true, false]);
  });

  test("a down machine says so; unknown numbers are —", () => {
    const doc = machinesDoc();
    doc.machines[1].alive = false;
    doc.machines[2].presets = {};
    const rows = rcm.machineRows(doc, false, "ko");
    assert.equal(rows[1].alive, false);
    assert.equal(rows[2].presets, "—");
  });

  test("a broken document → no rows, not a throw", () => {
    assert.deepEqual(rcm.machineRows(null, false, "ko"), []);
    assert.deepEqual(rcm.machineRows({ machines: "x" }, false, "ko"), []);
  });
});

describe("notMoving — the new actionable reasons", () => {
  test("no_capable_machine sorts right after worker_down, check_pending stays out", () => {
    const status = fixture("main");
    const q = status.pools[0].queue;
    const base = q.find((r) => r.id === 414);
    q.push(Object.assign(structuredClone(base), { id: 420, reason: "check_pending", placement_detail: [] }));
    q.push(Object.assign(structuredClone(base), { id: 421, reason: "no_capable_machine", placement: "capable", placement_detail: S4_DETAIL }));
    q.push(Object.assign(structuredClone(base), { id: 422, reason: "worker_down" }));
    const out = rcm.notMoving(status, null, "ko");
    assert.equal(out.kind, "list");
    const ids = out.lines.map((l) => l.jobId);
    assert.ok(ids.indexOf(422) < ids.indexOf(421), ids);
    assert.equal(ids.indexOf(420), -1);
    assert.equal(out.lines.find((l) => l.jobId === 421).text, "실행 가능한 머신 없음 · mbp: fvm 없음 · mac2-worker: 꺼짐");
  });
});
