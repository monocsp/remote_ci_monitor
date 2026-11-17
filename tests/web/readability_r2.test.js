"use strict";
// 가독성 R2 — Codex 가독성 검토(2026-11-17) 2 · 3 · 9번과 3절(한국어 문구)의 순수 함수 쪽.
// 구현보다 먼저 썼다(test-first). DOM 쪽(펼친 블록 높이 · 폰 라벨)은
// tests/test_web_readability_r2.py 가 Chrome 으로 잠근다.
//
// 잠그는 것:
// - 2번 placementDetailModel: 판정별 집계를 먼저(`도구 없음 12대 · 점검 실패 9대 · 꺼짐 3대`),
//   그 아래 문제 머신을 최대 PLACE_SHOWN 개, 나머지는 「나머지 N대 보기」 뒤에.
// - 3번 recentDetailModel: 전이 · 단계 · 이름(최대 8 + 「+N」) · 서버 요약을 따로 준다.
// - 9번 machineRows().checkHead: 첫 줄은 결론 · 대상 · 잡 번호, 나머지 점검은 아래 진단.
// - 3절: 어색하거나 모호하던 한국어 문구.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { load, TZ } = require("./helpers");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const rcm = load();
const MACHINES = path.join(__dirname, "fixtures", "machines-m5n.json");
const machinesDoc = () => JSON.parse(fs.readFileSync(MACHINES, "utf8"));

function note(worker, state, code, detail) {
  return { worker: worker, display_name: worker, state: state, code: code, detail: detail || {} };
}

// 머신 31대 — 도구 없음 12 · 점검 실패 9 · 꺼짐 3 · 보고 낡음 6 · 가능 1(서버 차례로 섞여 온다)
function crowd() {
  const out = [];
  for (let i = 0; i < 30; i++) {
    if (i % 10 === 9) out.push(note("down-" + i, "down", "worker_down"));
    else if (i % 5 === 0 || i % 5 === 1 || i % 5 === 3) {
      out.push(i < 20 ? note("tool-" + i, "ineligible", "tool_missing", { tools: ["fvm"] })
        : note("stale-" + i, "unknown", "report_stale"));
    } else out.push(note("chk-" + i, "ineligible", "check_failed", { check: "sim-check", job_id: 3600 + i }));
  }
  out.splice(4, 0, note("ok-mini", "eligible", "ok"));
  return out;
}

describe("2번 — placementDetailModel: 판정별 집계 먼저, 머신은 다섯까지", () => {
  test("PLACE_SHOWN 은 5다", () => {
    assert.equal(rcm.PLACE_SHOWN, 5);
  });

  test("집계는 많은 판정부터, 가능은 맨 끝 · 두 언어", () => {
    const row = { placement_detail: crowd() };
    const counts = {};
    row.placement_detail.forEach((n) => { const k = n.state === "down" ? "down" : n.code; counts[k] = (counts[k] || 0) + 1; });
    assert.deepEqual(counts, { tool_missing: 12, check_failed: 9, down: 3, report_stale: 6, ok: 1 });
    const ko = rcm.placementDetailModel(row, "ko", false);
    assert.equal(ko.summary, "도구 없음 12대 · 점검 실패 9대 · 보고 낡음 6대 · 꺼짐 3대 · 가능 1대");
    const en = rcm.placementDetailModel(row, "en", false);
    assert.equal(en.summary, "12 missing tools · 9 check failed · 6 stale report · 3 down · 1 eligible");
  });

  test("접힌 목록은 문제 머신 다섯 — 서버 차례 그대로, 가능은 뒤로", () => {
    const row = { placement_detail: crowd() };
    const m = rcm.placementDetailModel(row, "ko", false);
    assert.equal(m.lines.length, 5);
    assert.ok(m.lines.every((l) => l.cls !== "eligible"), JSON.stringify(m.lines));
    assert.deepEqual(m.lines.map((l) => l.text), [
      "tool-0: fvm 없음", "tool-1: fvm 없음", "chk-2: sim-check 실패 #3602", "tool-3: fvm 없음", "chk-4: sim-check 실패 #3604",
    ]);
    assert.equal(m.hidden, 26);
    assert.equal(m.total, 31);
  });

  test("「나머지 보기」면 전부 — 문제 머신이 먼저, 가능은 맨 끝", () => {
    const row = { placement_detail: crowd() };
    const m = rcm.placementDetailModel(row, "ko", true);
    assert.equal(m.lines.length, 31);
    assert.equal(m.hidden, 0);
    assert.equal(m.lines[30].text, "ok-mini: 가능");
    assert.equal(m.lines[30].cls, "eligible");
    assert.equal(m.lines.filter((l) => l.cls === "down").length, 3);
  });

  test("머신이 다섯 이하면 집계도 토글도 없다 — 줄이 곧 집계다", () => {
    const row = { placement_detail: [note("Mac mini", "eligible", "ok"), note("mbp", "ineligible", "tool_missing", { tools: ["fvm"] }), note("mac2-worker", "down", "worker_down")] };
    const m = rcm.placementDetailModel(row, "ko", false);
    assert.equal(m.summary, "");
    assert.equal(m.hidden, 0);
    // 문제 머신이 먼저 — 가능은 뒤로 간다
    assert.deepEqual(m.lines.map((l) => l.text), ["mbp: fvm 없음", "mac2-worker: 꺼짐", "Mac mini: 가능"]);
  });

  test("판정이 없으면 빈 모형 — 지어내지 않는다", () => {
    for (const row of [{}, { placement_detail: null }, null]) {
      const m = rcm.placementDetailModel(row, "ko", false);
      assert.deepEqual(m, { summary: "", lines: [], hidden: 0, total: 0 });
    }
  });

  test("모르는 코드도 세고 코드 그대로 이름 붙인다", () => {
    const list = [];
    for (let i = 0; i < 7; i++) list.push(note("m" + i, "unknown", "future_code"));
    const m = rcm.placementDetailModel({ placement_detail: list }, "ko", false);
    assert.equal(m.summary, "future_code 7대");
    assert.equal(rcm.placementDetailModel({ placement_detail: list }, "en", false).summary, "7 future_code");
  });

  test("토글 문구 — 나머지 N대 보기 / 접기", () => {
    assert.equal(I18N.t("ko", "place.more", { n: 26 }), "나머지 26대 보기");
    assert.equal(I18N.t("en", "place.more", { n: 26 }), "show 26 more");
    assert.equal(I18N.t("ko", "place.fewer"), "접기");
    assert.equal(I18N.t("en", "place.fewer"), "show fewer");
  });
});

function failedJob(patch) {
  return Object.assign({
    id: 411, state: "failed", exit_code: 1, key: "gate:full", preset: "gate",
    summary: "2 tests failed", summary_code: null, failed_step: "test",
    transitions: [
      { state: "queued", at: "2026-09-04T00:49:39Z" },
      { state: "running", at: "2026-09-04T00:50:12Z" },
      { state: "failed", at: "2026-09-04T00:51:40Z" },
    ],
  }, patch || {});
}
function names(n) {
  const out = [];
  for (let i = 0; i < n; i++) out.push({ name: "integration_test/case_" + i, step: i === 0, verdict: "persistent", seen: 8, window: 8, window_unnamed: 0 });
  return out;
}

describe("3번 — recentDetailModel: 전이 · 단계 · 이름 여덟 · 요약을 나눠 준다", () => {
  test("모든 칸이 따로 온다", () => {
    const m = rcm.recentDetailModel(failedJob({ failures: names(3) }), TZ, "ko");
    assert.equal(m.transitions, rcm.transitionsLine(failedJob(), TZ, "ko"));
    assert.equal(m.step, "실패한 단계: test");
    assert.equal(m.failuresTitle, "이름별 실패");
    assert.equal(m.failures.length, 3);
    assert.equal(m.failures[0].name, "integration_test/case_0 (단계)");
    assert.equal(m.failures[0].verdict, "최근 8회 전부");
    assert.equal(m.failures[0].title, "integration_test/case_0 (단계) — 최근 8회 전부");
    assert.equal(m.more, "");
    assert.equal(m.summary, "2 tests failed");
  });

  test("이름은 여덟까지, 넘치면 「+N」", () => {
    const m = rcm.recentDetailModel(failedJob({ failures: names(64) }), TZ, "ko");
    assert.equal(m.failures.length, 8);
    assert.match(m.more, /^\+56개 더/);
    assert.match(rcm.recentDetailModel(failedJob({ failures: names(64) }), TZ, "en").more, /^\+56 more/);
  });

  test("요약은 번역된 결과 문장 — 코드가 있으면 그 언어로", () => {
    const m = rcm.recentDetailModel(failedJob({ summary: "x", summary_code: "nope_unknown" }), TZ, "ko");
    assert.equal(m.summary, "x");
    assert.equal(rcm.recentDetailModel(failedJob({ summary: "" }), TZ, "ko").summary, "");
  });

  test("마지막 단계 · 이름 없는 실패 · 망가진 항목", () => {
    const m = rcm.recentDetailModel(failedJob({ failed_step: null, last_step: "build web",
      failures: [null, {}, { name: "ok" }, { name: "u", verdict: "first_seen", seen: 1, window: 8, window_unnamed: 3 }] }), TZ, "en");
    assert.equal(m.step, "last step: build web");
    assert.deepEqual(m.failures.map((f) => f.name), ["ok", "u"]);
    assert.equal(m.failures[0].verdict, "");
    assert.equal(m.failures[0].title, "ok");
    assert.match(m.unnamed, /3/);
  });

  test("recentDetail(옛 줄 목록)은 같은 모형에서 나온다 — 두 갈래가 어긋나지 않게", () => {
    const job = failedJob({ failures: names(10) });
    const lines = rcm.recentDetail(job, "ko");
    const m = rcm.recentDetailModel(job, TZ, "ko");
    assert.equal(lines[0], m.step);
    assert.equal(lines[1], m.failuresTitle);
    assert.deepEqual(lines.slice(2, 10), m.failures.map((f) => f.title));
    assert.equal(lines[10], m.more);
  });

  test("상세의 버튼 문구", () => {
    assert.equal(I18N.t("ko", "recent.full"), "전체 보기");
    assert.equal(I18N.t("ko", "recent.less"), "접기");
    assert.equal(I18N.t("ko", "recent.open_log"), "로그 열기");
    assert.equal(I18N.t("en", "recent.full"), "show all");
    assert.equal(I18N.t("en", "recent.less"), "show less");
    assert.equal(I18N.t("en", "recent.open_log"), "open log");
  });
});

describe("9번 — machineRows().checkHead: 결론 · 대상 · 잡 번호가 첫 줄", () => {
  test("픽스처 세 머신 — 실패 · 통과 · 점검 필요", () => {
    const ko = rcm.machineRows(machinesDoc(), false, "ko");
    assert.deepEqual(ko.map((r) => r.checkHead && r.checkHead.text),
      ["실패 · sim-check · #3620", "통과 · sim-check", "점검 필요 · sim-check"]);
    assert.deepEqual(ko[0].checkHead, { text: "실패 · sim-check · #3620", word: "실패", preset: "sim-check", jobId: 3620, cls: "failed" });
    assert.deepEqual(ko.map((r) => r.checkRest), ["", "", ""]);
    const en = rcm.machineRows(machinesDoc(), false, "en");
    assert.deepEqual(en.map((r) => r.checkHead.text), ["failed · sim-check · #3620", "passed · sim-check", "needed · sim-check"]);
    // 칸 전체의 전문(title)은 오늘 그대로
    assert.deepEqual(ko.map((r) => r.checks), ["sim-check 실패 #3620", "sim-check 통과", "sim-check 점검 필요"]);
  });

  test("점검이 여럿이면 실패가 첫 줄로 오르고 나머지는 아래 진단", () => {
    const doc = machinesDoc();
    doc.machines[0].checks = [
      { preset: "lint-check", state: "passed", job_id: 3590 },
      { preset: "boot-check", state: "running", job_id: 3651 },
      { preset: "scenario-qa-check", state: "failed", job_id: 3600 },
      { preset: "sim-check", state: "needed", job_id: 3599, recheck_needed: true },
    ];
    const r = rcm.machineRows(doc, false, "ko")[0];
    assert.equal(r.checkHead.text, "실패 · scenario-qa-check · #3600");
    assert.equal(r.checkHead.jobId, 3600);
    assert.equal(r.checkRest, "sim-check 점검 미완료 — 다시 점검 필요 #3599 · boot-check 점검 중 #3651 · lint-check 통과");
  });

  test("다시 점검 필요 > 점검 중 > 점검 필요 > 통과 순", () => {
    const doc = machinesDoc();
    doc.machines[0].checks = [
      { preset: "a", state: "passed", job_id: 1 },
      { preset: "b", state: "needed", job_id: null },
      { preset: "c", state: "running", job_id: 7 },
    ];
    assert.equal(rcm.machineRows(doc, false, "ko")[0].checkHead.text, "점검 중 · c · #7");
    doc.machines[0].checks.push({ preset: "d", state: "needed", job_id: 9, recheck_needed: true });
    assert.equal(rcm.machineRows(doc, false, "ko")[0].checkHead.text, "다시 점검 필요 · d · #9");
    assert.equal(rcm.machineRows(doc, false, "en")[0].checkHead.text, "recheck needed · d · #9");
  });

  test("점검이 없으면 머리도 진단도 없다", () => {
    const doc = machinesDoc();
    doc.machines[1].checks = [];
    const r = rcm.machineRows(doc, false, "ko")[1];
    assert.equal(r.checkHead, null);
    assert.equal(r.checkRest, "");
    assert.equal(r.checks, "—");
  });
});

describe("3절 — 한국어 문구", () => {
  const ko = (k, a) => I18N.t("ko", k, a);
  const en = (k, a) => I18N.t("en", k, a);

  test("확인할 작업 카드는 시스템 전체가 정상이라고 하지 않는다", () => {
    assert.equal(ko("summary.nothing_stuck"), "확인이 필요한 작업이 없습니다");
    assert.equal(ko("summary.nothing_else_stuck"), "그 밖에 확인할 작업은 없습니다");
    assert.equal(en("summary.nothing_stuck"), "Nothing needs a look");
    assert.equal(en("summary.nothing_else_stuck"), "nothing else needs a look");
  });

  test("레인 · 대기열 · 최장 대기", () => {
    assert.equal(ko("reason.frees_in", { dur: "5m 10s" }), "빈 레인까지 5m 10s");
    assert.equal(ko("ordinal.in_line", { ordinal: "1번째" }), "대기열 1번째");
    assert.equal(ko("queue.oldest_waiting", { dur: "1m 35s" }), "최장 대기 1m 35s");
  });

  test("ETA 는 「완료 예상」 — 단정하지 않는다", () => {
    assert.equal(ko("your.eta", { state: "실행 중", clock: "10:06" }), "실행 중 · 완료 예상 10:06");
    assert.equal(ko("your.in_line", { ordinal: "대기열 2번째", clock: "10:06" }), "대기열 2번째 · 완료 예상 10:06");
  });

  test("머신 이유는 무엇이 안 맞는지 · 무엇의 점검인지 말한다", () => {
    assert.equal(ko("reason.no_capable_machine"), "실행 가능한 머신 없음");
    assert.equal(ko("reason.check_failed"), "머신 점검 실패");
    assert.equal(ko("machines.failed_count", { n: 1 }), "점검 실패 1대");
  });

  test("합류 · 취소 잠김", () => {
    assert.equal(ko("row.you_joined"), "내가 합류함");
    assert.equal(ko("row.cancel_locked"),
      "취소에는 제출할 때 받은 cancel token 이 필요합니다 — 제출한 세션에서 rcm cancel 을 실행하거나 관리자 토큰을 사용하세요");
  });

  test("배치 사유", () => {
    assert.equal(ko("place.workspace_tool", { tool: "melos" }), "melos는 작업 공간에만 있음");
    assert.equal(ko("place.check_recheck", { preset: "sim-check" }), "sim-check 점검 미완료 — 다시 점검 필요");
    assert.equal(ko("place.report_missing"), "환경 보고 없음");
    assert.equal(ko("place.report_stale"), "환경 보고 낡음");
  });

  test("부하는 처리 대기가 아니라 시스템 부하 · 폴링은 몇 초마다인지", () => {
    assert.equal(ko("host.cores_load", { cores: 32, load: "3.5" }), "시스템 부하 3.5 · 코어 32개");
    assert.equal(ko("summary.load", { load: "3.5" }), "시스템 부하 3.5");
    assert.equal(ko("conn.polling", { age: "3초 전" }), "10초마다 확인 · 3초 전 갱신");
  });
});
