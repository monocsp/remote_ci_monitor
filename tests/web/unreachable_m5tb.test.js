"use strict";
// M5t-b PR3 — 끊김 유예 · 그룹 홀드 · 로그 완전성의 웹 화면(docs/m5tb-workplan.md §4.8 · §4.12).
//
// 잠그는 것:
// - RL28: 사유 `worker_unreachable_grace` 는 사람 말로 그린다(원문 코드 없음), 행동 가능, ETA 는 「—」,
//   「확인이 필요한 작업」에서 `worker_down` 바로 뒤에 선다.
// - RL45: 열린 홀드마다 「확인이 필요한 작업」에 한 줄(그 그룹에 대기 잡이 없어도), 홀드에 막힌 대기 잡의
//   줄도 원문 코드 없이, 홀드 잡의 최근 행에 칩 하나. 홀드 목록을 못 읽었으면(null) 「없음」이라 하지
//   않는다(fail-open 금지). 키가 없는 옛 서버는 홀드 줄이 없다.
// - RL20: 로그 서랍의 머리 줄은 `log_complete === false` 일 때만 「로그가 불완전함」을 붙인다.
// - RL29: 연락 두절의 다섯 끝이 두 언어로 번역된 문장이다(번역이 없으면 코드가 그대로 나와 빨개진다).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();
const DASH = "—";
const GEN = "2026-11-27T09:00:00Z";
const NOW = Date.parse(GEN);

function running(id, patch) {
  return Object.assign({
    id: id, state: "running", key: "ship", preset: "ship", lane: 1, ran_on: "mac2",
    reason: "worker_unreachable_grace",
    requester: { name: "alice", label: "alice@mbp" }, joiners: [],
    estimate: { elapsed_seconds: 240, remaining_seconds: 60, finish_at: null, source: "preset", sample_count: 0 },
    action: null
  }, patch || {});
}
function waiting(id, patch) {
  return Object.assign({
    id: id, state: "queued", key: "ship", preset: "ship", position: 1,
    reason: "needs_action",
    requester: { name: "alice", label: "alice@mbp" }, joiners: [],
    estimate: { waited_seconds: 30, finish_at: null, wait_seconds: null, source: "preset", sample_count: 0 },
    blocked_by: { job_id: 812, group: "irreversible:ship", remaining_seconds: null },
    action: { code: "irreversible_hold", note: "group held — run rcm release-hold 812", at: "2026-11-27T08:50:00Z" }
  }, patch || {});
}
const HOLD_A = { job_id: 812, preset: "ship", group: "irreversible:ship", scope: "global", worker: "build-02", cause: "worker_unreachable", since: "2026-11-27T08:50:00Z" };
const HOLD_B = { job_id: 815, preset: "deploy", group: "devices", scope: "pool", worker: null, cause: "worker_restarted_without_job", since: "2026-11-27T08:55:00Z" };

function status(queue, extra) {
  return Object.assign({
    schema_version: 1, generated_at: GEN, server: { lanes: 1, workers: [] },
    pools: [{ name: "default", queue: queue, recent: [] }]
  }, extra || {});
}

const CODES = ["worker_unreachable_grace", "irreversible_hold", "worker_restarted_without_job"];
function noCode(text) {
  CODES.forEach((c) => assert.ok(text.indexOf(c) === -1, "raw code " + c + " in: " + text));
}

describe("RL28 — 유예 중인 잡의 사유", () => {
  test("영어: 머신 이름과 함께 사람 말로, 행동 가능", () => {
    const r = rcm.reasonText(running(901), NOW, status([]), "en");
    assert.equal(r.text, "lost contact with mac2 — the job goes on if the worker is back within the grace period");
    assert.equal(r.actionable, true);
    noCode(r.text);
  });
  test("한국어: 같은 뜻, 원문 코드 없음", () => {
    const r = rcm.reasonText(running(901), NOW, status([]), "ko");
    assert.equal(r.text, "워커 mac2와 연락이 끊김 · 유예 시간 안에 돌아오면 이어서 진행");
    noCode(r.text);
  });
  test("머신 이름을 모르면 「워커」라고만 한다", () => {
    assert.equal(rcm.reasonText(running(901, { ran_on: null }), NOW, status([]), "en").text,
      "lost contact with the worker — the job goes on if the worker is back within the grace period");
    assert.equal(rcm.reasonText(running(901, { ran_on: null }), NOW, status([]), "ko").text,
      "워커와 연락이 끊김 · 유예 시간 안에 돌아오면 이어서 진행");
  });
  test("ETA 는 —", () => {
    assert.equal(rcm.etaText(running(901), "UTC", NOW, "en").clock, DASH);
  });
  test("ACTIONABLE 에서 worker_down 바로 뒤", () => {
    const i = rcm.ACTIONABLE.indexOf("worker_unreachable_grace");
    assert.equal(i, rcm.ACTIONABLE.indexOf("worker_down") + 1);
  });
  test("「확인이 필요한 작업」에 오르고 stuck 보다 앞이다", () => {
    const stuck = running(902, { reason: "stuck", ran_on: "build-02", estimate: { elapsed_seconds: 900, expected_seconds: 100, finish_at: null } });
    const r = rcm.notMoving(status([stuck, running(901)]), null, "en");
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [901, 902]);
    noCode(r.lines[0].text);
  });
});

describe("RL45 — 그룹 홀드의 사람용 화면", () => {
  test("홀드마다 한 줄, 대기 잡이 없는 홀드도", () => {
    const r = rcm.notMoving(status([], { irreversible_holds: [HOLD_A, HOLD_B] }), null, "en");
    assert.equal(r.kind, "list", JSON.stringify(r));
    assert.deepEqual(r.lines.map((l) => l.jobId), [812, 815]);
    assert.equal(r.lines[0].text,
      "Group irreversible:ship is held: job #812 (ship) on build-02 closed without a stop confirmation. "
      + "Check build-02 for a leftover process, then run rcm release-hold 812.");
    // 로컬 레인 잡(worker null)은 서버의 로컬 레인이다
    assert.equal(r.lines[1].text,
      "Group devices is held: job #815 (deploy) on the server's local lane closed without a stop confirmation. "
      + "Check the server for a leftover process, then run rcm release-hold 815.");
    r.lines.forEach((l) => noCode(l.text));
  });
  test("한국어 줄", () => {
    const r = rcm.notMoving(status([], { irreversible_holds: [HOLD_A, HOLD_B] }), null, "ko");
    assert.equal(r.lines.length, 2, JSON.stringify(r));
    assert.equal(r.lines[0].text,
      "그룹 irreversible:ship가 막혀 있음: 작업 #812(ship)가 build-02에서 멈춤 확인 없이 닫힘. "
      + "build-02에 남은 프로세스가 있는지 확인한 뒤 rcm release-hold 812를 실행하세요.");
    assert.equal(r.lines[1].text,
      "그룹 devices가 막혀 있음: 작업 #815(deploy)가 서버의 로컬 레인에서 멈춤 확인 없이 닫힘. "
      + "서버에 남은 프로세스가 있는지 확인한 뒤 rcm release-hold 815를 실행하세요.");
    r.lines.forEach((l) => noCode(l.text));
  });
  test("홀드는 다른 행동 가능한 줄보다 앞이다(needs_action 자리)", () => {
    const down = waiting(820, { reason: "worker_down", blocked_by: null, action: null });
    const r = rcm.notMoving(status([down], { irreversible_holds: [HOLD_A] }), null, "en");
    assert.deepEqual(r.lines.map((l) => l.jobId), [812, 820]);
  });
  test("홀드에 막힌 대기 잡의 줄: 원문 코드 없이 그 홀드 잡으로 가는 링크", () => {
    const row = waiting(821);
    const en = rcm.reasonText(row, NOW, status([row]), "en");
    assert.equal(en.text, "needs action · group irreversible:ship is held by #812 — rcm release-hold 812 after checking the machine");
    assert.deepEqual(en.links, [{ jobId: 812 }]);
    assert.equal(en.actionable, true);
    noCode(en.text);
    const ko = rcm.reasonText(row, NOW, status([row]), "ko");
    assert.equal(ko.text, "사람이 할 일 · 그룹 irreversible:ship가 #812 때문에 막혀 있음 — 머신을 확인한 뒤 rcm release-hold 812");
    noCode(ko.text);
  });
  test("잡이 직접 찍은 표식(다른 code)은 오늘 그대로다", () => {
    const row = running(822, { reason: "needs_action", action: { code: "login", note: "Sign in", at: GEN } });
    assert.equal(rcm.reasonText(row, NOW, status([row]), "en").text, "needs action · login · Sign in");
  });
  test("홀드 목록을 못 읽었으면(null) 「없음」이 아니라 「모름」", () => {
    const r = rcm.notMoving(status([], { irreversible_holds: null }), null, "en");
    assert.equal(r.kind, "unknown");
    assert.equal(r.why, "holds");
    const r2 = rcm.notMoving(status([running(901)], { irreversible_holds: null }), null, "en");
    assert.equal(r2.kind, "list");
    assert.equal(r2.partial, true);
  });
  test("읽을 수 없는 홀드 항목이 하나라도 있으면 목록 전체가 「모름」(P3 r1, rcm check · doctor 와 같은 규칙)", () => {
    const bad = [{ job_id: "812", group: "irreversible:ship" }, { job_id: 813, group: "" }, { job_id: 814 }, null, "x"];
    bad.forEach((b) => {
      const r = rcm.notMoving(status([], { irreversible_holds: [HOLD_A, b] }), null, "en");
      assert.equal(r.kind, "unknown", JSON.stringify(b) + " " + JSON.stringify(r));
      assert.equal(r.why, "holds");
      const r2 = rcm.notMoving(status([running(901)], { irreversible_holds: [HOLD_A, b] }), null, "en");
      assert.equal(r2.kind, "list");
      assert.equal(r2.partial, true, JSON.stringify(b));
      assert.equal(r2.why, "holds");
    });
  });
  test("키가 없는 옛 서버 · 빈 목록은 홀드 줄이 없다", () => {
    assert.equal(rcm.notMoving(status([]), null, "en").kind, "ok");
    assert.equal(rcm.notMoving(status([], { irreversible_holds: [] }), null, "en").kind, "ok");
  });
  test("홀드 잡의 최근 행에 칩 하나, 다른 잡에는 없다", () => {
    assert.equal(typeof rcm.holdChip, "function");
    const st = status([], { irreversible_holds: [HOLD_A] });
    const chip = rcm.holdChip(st, 812, "en");
    assert.equal(chip.text, "group held");
    assert.equal(chip.title,
      "Group irreversible:ship is held: job #812 (ship) on build-02 closed without a stop confirmation. "
      + "Check build-02 for a leftover process, then run rcm release-hold 812.");
    assert.equal(rcm.holdChip(st, 813, "en"), null);
    assert.equal(rcm.holdChip(st, 812, "ko").text, "그룹 막힘");
    assert.equal(rcm.holdChip(status([]), 812, "en"), null);
    assert.equal(rcm.holdChip(status([], { irreversible_holds: null }), 812, "en"), null);
  });
});

describe("RL20 — 로그 서랍의 불완전 표시", () => {
  test("log_complete false 면 머리 줄에 한 마디", () => {
    assert.equal(typeof rcm.logCompleteNote, "function");
    assert.equal(rcm.logCompleteNote({ log_complete: false }, "en"), "log incomplete — the worker could not keep all of it");
    assert.equal(rcm.logCompleteNote({ log_complete: false }, "ko"), "로그가 불완전함 — 워커가 일부를 남기지 못함");
  });
  test("참 · null · 키 없음 · 잡을 모름이면 아무것도 없다", () => {
    assert.equal(typeof rcm.logCompleteNote, "function");
    assert.equal(rcm.logCompleteNote({ log_complete: true }, "en"), null);
    assert.equal(rcm.logCompleteNote({ log_complete: null }, "en"), null);
    assert.equal(rcm.logCompleteNote({}, "en"), null);
    assert.equal(rcm.logCompleteNote(null, "en"), null);
  });
});

describe("RL29 — 연락 두절의 다섯 끝은 번역된 문장이다", () => {
  const FIVE = [
    ["worker_unreachable", { name: "mac2", seconds: 660 }],
    ["worker_self_stopped", { seconds: 575 }],
    ["worker_replaced", { name: "mac2" }],
    ["worker_restarted_without_job", { name: "mac2" }],
    ["orphan_unverified", { name: "mac2", seconds: 660 }]
  ];
  FIVE.forEach(([code, args]) => {
    test(code, () => {
      ["en", "ko"].forEach((lang) => {
        const line = rcm.recentLine({ id: 9, state: "lost", summary_code: code, summary_args: args, summary: null }, "UTC", NOW, lang);
        assert.ok(line.summary && line.summary.indexOf(code) === -1, lang + ": " + line.summary);
      });
    });
  });
  test("orphan_unverified 는 그 머신에서 아직 돌 수 있다고 말한다", () => {
    const job = { id: 9, state: "lost", summary_code: "orphan_unverified", summary_args: { name: "mac2", seconds: 660 } };
    assert.match(rcm.recentLine(job, "UTC", NOW, "en").summary, /may still be running on that machine/);
    assert.match(rcm.recentLine(job, "UTC", NOW, "ko").summary, /아직 돌 수 있으니/);
  });
});

// ── batch-3: 리드가 찾은 화면 결함 둘 ────────────────────────────────────────────────
const fs = require("node:fs");
const path = require("node:path");
const I18N_PATH = path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js");
const I18N = require(I18N_PATH);

describe("RL28 — 끊긴 워커 띠는 지금 동작을 말한다(유예 동안 이어 감)", () => {
  // 옛 띠는 「돌던 잡의 결과를 잃는다」고 했다. M5t-b(결정 253 · 256)부터 원격 잡은 끊김 유예 동안
  // 계속 돌고, 유예가 지나야 lost 로 닫힌다. 유예 값은 /api/status 에 없어서 숫자를 넣지 않는다.
  // 띠 높이(390px 에서 80px 이하, tests/test_web_readability_r1.py)가 글 길이를 묶는다.
  const ONE_EN = "Worker macmini unreachable — its jobs go on if back within the grace period, else end as lost";
  const MANY_EN = "3 workers unreachable — their jobs go on if back within the grace period, else end as lost";
  const ONE_KO = "워커 macmini 연결 끊김 — 유예 시간 안에 돌아오면 작업이 이어지고 넘기면 결과를 잃음";
  const MANY_KO = "워커 3대 연결 끊김 — 유예 시간 안에 돌아오면 작업이 이어지고 넘기면 결과를 잃음";

  test("rl28 banner — 한 대 · 여러 대, 두 언어", () => {
    assert.equal(rcm.unreachableNote(["macmini"], "en").text, ONE_EN);
    assert.equal(rcm.unreachableNote(["macmini", "b", "c"], "en").text, MANY_EN);
    assert.equal(rcm.unreachableNote(["macmini"], "ko").text, ONE_KO);
    assert.equal(rcm.unreachableNote(["macmini", "b", "c"], "ko").text, MANY_KO);
  });

  test("rl28 banner — 유예 값을 지어내지 않는다(띠에 숫자는 대수뿐)", () => {
    ["en", "ko"].forEach((lang) => {
      assert.ok(!/\d/.test(rcm.unreachableNote(["macmini"], lang).text), lang);
      assert.deepEqual(rcm.unreachableNote(["x", "y", "z"], lang).text.match(/\d+/g), ["3"], lang);
    });
  });

  test("rl28 banner — 옛 문장(결과를 잃는다 · 알 수 없다)이 다시 들어오면 빨갛다", () => {
    const OLD = [
      /results of (its|their) running jobs are lost/,
      /진행 중이던 작업의 결과를 알 수 없습니다/
    ];
    ["en", "ko"].forEach((lang) => {
      [["solo"], ["a", "b"]].forEach((names) => {
        const text = rcm.unreachableNote(names, lang).text;
        OLD.forEach((re) => assert.ok(!re.test(text), lang + ": " + text));
      });
    });
    const src = fs.readFileSync(I18N_PATH, "utf8");
    OLD.forEach((re) => assert.ok(!re.test(src), "i18n.js 에 옛 띠 문장이 있다: " + re));
  });
});

describe("한국어 조사 — 라틴 낱말 끝의 「모음 + r」는 받침이 아니다", () => {
  // 글자 이름 읽기(규칙 4)로는 `r`(알)에 받침이 있어 `mac2-worker과` 가 됐다. 한국어로 적을 때
  // 영어 낱말 끝의 「모음 + r」는 받침이 되지 않는다(워커 · 러너 · 서버 · 도커 · 컬러).
  // 두 글자 이하 낱말이나 자음 + r 로 끝나는 약어(`AR` · `HDR`)와 `r` 한 글자는 글자 이름 그대로다.
  const { withJosa } = I18N;

  test("mac2-worker 는 받침 없는 쪽이다", () => {
    assert.equal(withJosa("mac2-worker", "와/과"), "mac2-worker와");
    assert.equal(withJosa("mac2-worker", "이/가"), "mac2-worker가");
    assert.equal(withJosa("mac2-worker", "을/를"), "mac2-worker를");
    assert.equal(withJosa("mac2-worker", "은/는"), "mac2-worker는");
    assert.equal(withJosa("mac2-worker", "으로/로"), "mac2-worker로");
  });

  test("다른 「모음 + r」 낱말도 같다(대소문자 무시)", () => {
    ["runner", "docker", "builder", "color", "USER", "Car"].forEach((w) => {
      assert.equal(withJosa(w, "와/과"), w + "와", w);
      assert.equal(withJosa(w, "이/가"), w + "가", w);
    });
  });

  test("build-02 · macmini · 한글 이름은 그대로", () => {
    assert.equal(withJosa("build-02", "와/과"), "build-02와");
    assert.equal(withJosa("macmini", "와/과"), "macmini와");
    assert.equal(withJosa("맥미니", "와/과"), "맥미니와");
    assert.equal(withJosa("빌드실", "와/과"), "빌드실과");
  });

  test("약어와 한 글자는 글자 이름으로 읽는다", () => {
    assert.equal(withJosa("r", "와/과"), "r과");
    assert.equal(withJosa("AR", "와/과"), "AR과");
    assert.equal(withJosa("HDR", "와/과"), "HDR과");
    assert.equal(withJosa("ocr", "이/가"), "ocr이");
  });

  test("rl28 reason — 유예 중인 잡의 한국어 사유에 「mac2-worker와」", () => {
    const r = rcm.reasonText(running(901, { ran_on: "mac2-worker" }), NOW, status([]), "ko");
    assert.equal(r.text, "워커 mac2-worker와 연락이 끊김 · 유예 시간 안에 돌아오면 이어서 진행");
  });
});
