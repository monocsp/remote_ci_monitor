"use strict";
// 스토어 탭 — 릴리스 드라이버 스테퍼(S0~S8) · Start/Confirm N/Abort/Retry 활성 규칙 · 예행 본문 ·
// GitHub 카드의 순수 함수 (docs/wireframes/web-store.html 항목 25 · 26 · 28 · 35 · 37~39,
// STORE-TAB-API-2 「Driver」·「github」). 잠그는 것: 단계 읽기 규칙 · 종료 코드별 색과 글리프 ·
// S2 의 친 N 은 글자 그대로 · exit 3 에는 Retry 가 없다 · mode=upload 버튼은 없다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW, TZ } = require("./helpers");

const rcm = load();
const S = rcm.store;
const I18N = require("../../src/remote_ci_monitor/web/i18n.js");
const iso = (sec) => new Date(NOW - sec * 1000).toISOString();
const CTX = { status: 200, lang: "en", tzName: TZ, nowMs: NOW };

function driver(patch) {
  return Object.assign({ running: false, build_name: "1.0.1", started_at: iso(2280), started_by: "pcs", pid: null, exit_code: null,
    log_tail: [], plan_n: null, status: [] }, patch);
}
const ADMIN = { token: "t", admin: true, busy: null, version: "1.0.1", typedN: "", nMode: "typed" };
const release = { plan: { job_id: 641, state: "succeeded", build_name: "1.0.1", doc: { n: 181, build_name: "1.0.1" } } };
const states = (m) => m.items.map((i) => i.state).join(" ");

describe("driverStage — 단계 읽기 규칙", () => {
  test("`--status` 의 `stage S<n>` 마지막 줄이 이긴다", () => {
    const r = S.driverStage(["release 1.0.1: stage S1 plan", "release 1.0.1: stage S5 scenario QA · job #643"], null, ["stage S2"]);
    assert.deepEqual(r, { stage: "S5", last: "S5", source: "status" });
  });
  test("`--status` 가 비면 로그 꼬리에서 읽고, `다음 단계 S<n>` 도 단계다", () => {
    assert.equal(S.driverStage([], null, ["plan: N = 181", "다음 단계 S3"]).stage, "S3");
    assert.equal(S.driverStage([], null, ["stage S4 gate-smoke"]).source, "log");
  });
  test("아무 줄도 없으면 null — 지어내지 않는다", () => {
    assert.deepEqual(S.driverStage([], null, []), { stage: null, last: null, source: null });
    assert.equal(S.driverStage(null, 1, undefined).stage, null);
  });
  test("종료 코드 0 은 DONE, 2 는 S2 — 줄이 무어라 하든", () => {
    assert.equal(S.driverStage(["stage S8 tag"], 0, []).stage, "DONE");
    assert.equal(S.driverStage(["stage S1 plan"], 2, []).stage, "S2");
  });
  test("DONE · BLOCKED_PR_CLOSED 도 읽고, `last` 는 마지막 S 단계다", () => {
    const r = S.driverStage(["stage S4 gate", "stage BLOCKED_PR_CLOSED"], null, []);
    assert.equal(r.stage, "BLOCKED_PR_CLOSED");
    assert.equal(r.last, "S4");
    assert.equal(S.driverStage(["stage DONE"], null, []).stage, "DONE");
  });
  test("`stage` 낱말이 없는 줄은 단계가 아니다 (S12 같은 것도 아니다)", () => {
    assert.equal(S.driverStage(["S5 is next", "stages: many"], null, []).stage, null);
    assert.equal(S.driverStage(["stage S12"], null, []).stage, null);
  });
});

describe("stepperModel — 아홉 칸", () => {
  test("404 → «not available in this build», configured:false → 프로파일에 드라이버 없음", () => {
    const na = S.stepperModel(null, Object.assign({}, CTX, { status: 404 }));
    assert.equal(na.available, false);
    assert.equal(na.head, "release driver: not available in this build");
    assert.equal(na.items.length, 0);
    const nc = S.stepperModel({ configured: false }, CTX);
    assert.equal(nc.configured, false);
    assert.match(nc.head, /no driver in the profile/);
    assert.equal(S.stepperModel(null, CTX).loading, true);
  });
  test("회차 없음 — 아홉 칸 전부 회색 · 머리는 «start one below»", () => {
    const m = S.stepperModel(driver(), CTX);
    assert.equal(m.idle, true);
    assert.equal(m.items.length, 9);
    assert.equal(states(m), "todo todo todo todo todo todo todo todo todo");
    assert.equal(m.head, "no round — start one below");
    assert.equal(m.dialog, false);
  });
  test("도는 중 S5 — 앞 ✓ 초록, 지금 ▶ 파랑, 뒤 · 회색, S6·S7 은 irreversible", () => {
    const m = S.stepperModel(driver({ running: true, pid: 4242, plan_n: 181, status: ["release 1.0.1: stage S5 scenario QA"] }), CTX);
    assert.equal(m.running, true);
    assert.equal(m.stage, "S5");
    assert.equal(states(m), "done done done done done current todo todo todo");
    assert.deepEqual(m.items.map((i) => i.glyph), ["✓", "✓", "✓", "✓", "✓", "▶", "·", "·", "·"]);
    assert.equal(m.items[5].label, "scenario QA");
    assert.match(m.items[6].text, /irreversible/);
    assert.match(m.items[7].text, /irreversible/);
    assert.match(m.items[1].text, /N=181/);
    assert.match(m.items[2].text, /typed 181/);
    assert.equal(m.tone, "running");
    assert.match(m.head, /^1\.0\.1 · stage S5 scenario QA · 38m 00s · started by pcs$/);
  });
  test("도는 중인데 아직 줄이 없으면 S0 이 지금이다", () => {
    const m = S.stepperModel(driver({ running: true }), CTX);
    assert.equal(m.stage, "S0");
    assert.equal(m.items[0].state, "current");
  });
  test("exit 2 + plan_n — S2 는 사람 단계(황토), 대화상자가 뜬다", () => {
    const m = S.stepperModel(driver({ exit_code: 2, plan_n: 181, log_tail: ["stage S1 plan", "plan: N = 181"] }), CTX);
    assert.equal(m.stage, "S2");
    assert.equal(states(m), "done done human todo todo todo todo todo todo");
    assert.equal(m.dialog, true);
    assert.equal(m.tone, "human");
    assert.equal(m.head, "1.0.1 · waiting for the typed build number 181");
    assert.match(m.items[2].text, /type the build number/);
  });
  test("plan_n 이 없으면 로그의 `plan: N = <n>` 이 대신 온다; 그것도 없으면 대화상자 없음", () => {
    const m = S.stepperModel(driver({ exit_code: 2, log_tail: ["plan: N = 182"] }), CTX);
    assert.equal(m.planN, 182);
    assert.equal(m.dialog, true);
    const none = S.stepperModel(driver({ exit_code: 2 }), CTX);
    assert.equal(none.planN, null);
    assert.equal(none.dialog, false);
    assert.match(none.head, /plan has none/);
  });
  test("확인한 N 이 이미 있으면(confirmed_n) 대화상자는 다시 뜨지 않는다", () => {
    assert.equal(S.stepperModel(driver({ exit_code: 2, plan_n: 181, confirmed_n: 181 }), CTX).dialog, false);
  });
  test("exit 1 — 실패한 단계 ✗ 빨강, «main untouched»", () => {
    const m = S.stepperModel(driver({ exit_code: 1, plan_n: 181, status: ["stage S5 scenario QA FAIL"] }), CTX);
    assert.equal(states(m), "done done done done done failed todo todo todo");
    assert.equal(m.items[5].glyph, "✗");
    assert.equal(m.tone, "bad");
    assert.equal(m.head, "1.0.1 · failed at S5 scenario QA — main untouched");
    assert.match(S.stepperModel(driver({ exit_code: 1 }), CTX).head, /^1\.0\.1 · failed — main untouched$/);
  });
  test("exit 3 — 결과 모름 ? 보라(lost), 다시 올리지 말라", () => {
    const m = S.stepperModel(driver({ exit_code: 3, status: ["stage S7 upload"] }), CTX);
    assert.equal(m.items[7].state, "unknown");
    assert.equal(m.items[7].glyph, "?");
    assert.equal(m.tone, "lost");
    assert.match(m.head, /result unknown — do not resubmit/);
  });
  test("exit 4 — 스토어 드리프트 ! 황토", () => {
    const m = S.stepperModel(driver({ exit_code: 4, status: ["stage S1 plan"] }), CTX);
    assert.equal(m.items[1].state, "drift");
    assert.equal(m.items[1].glyph, "!");
    assert.equal(m.tone, "warn");
    assert.match(m.head, /store drift/);
  });
  test("exit 0 — 전부 ✓, 머리는 «done»", () => {
    const m = S.stepperModel(driver({ exit_code: 0, status: ["stage S8 tag"] }), CTX);
    assert.equal(m.done, true);
    assert.equal(states(m), "done done done done done done done done done");
    assert.equal(m.head, "1.0.1 done");
    assert.equal(m.tone, "ok");
  });
  test("BLOCKED_PR_CLOSED — 마지막 S 단계에 ! 황토, 앞은 ✓", () => {
    const m = S.stepperModel(driver({ exit_code: 1, status: ["stage S4 gate", "stage BLOCKED_PR_CLOSED"] }), CTX);
    assert.equal(m.blocked, true);
    assert.equal(states(m), "done done done done blocked todo todo todo todo");
    assert.equal(m.tone, "warn");
    assert.match(m.head, /PR was closed/);
  });
  test("모르는 종료 코드는 빨강 «exit N»; status_error 는 그대로 전한다", () => {
    const m = S.stepperModel(driver({ exit_code: 9, status_error: "timeout after 10s" }), CTX);
    assert.equal(m.tone, "bad");
    assert.match(m.head, /exit 9/);
    assert.equal(m.statusError, "timeout after 10s");
  });
  test("도는 중이면 exit_code 는 무시한다(옛 회차의 값)", () => {
    const m = S.stepperModel(driver({ running: true, exit_code: 1, status: ["stage S3 draft PR"] }), CTX);
    assert.equal(m.exit, null);
    assert.equal(m.items[3].state, "current");
  });
  test("한국어 — 단계 이름과 머리가 카탈로그에서 온다", () => {
    const m = S.stepperModel(driver({ running: true, status: ["stage S5"] }), Object.assign({}, CTX, { lang: "ko" }));
    assert.equal(m.items[5].label, "시나리오 QA");
    assert.match(m.head, /단계 S5 시나리오 QA/);
    assert.ok(!/undefined|NaN/.test(m.head + m.items.map((i) => i.text).join("")));
  });
});

describe("driverRow — Build·upload 행의 색을 드라이버가 가져가는가", () => {
  test("도는 중 파랑 · exit 1/3 빨강 · exit 2/4 황토 · PR 닫힘 황토", () => {
    const row = (patch) => S.driverRow(S.stepperModel(driver(patch), CTX));
    assert.equal(row({ running: true }).state, "running");
    assert.equal(row({ exit_code: 1 }).state, "bad");
    assert.equal(row({ exit_code: 3 }).state, "bad");
    assert.equal(row({ exit_code: 2, plan_n: 181 }).state, "stale");
    assert.equal(row({ exit_code: 4 }).state, "stale");
    assert.equal(row({ exit_code: 1, status: ["stage S4", "stage BLOCKED_PR_CLOSED"] }).state, "stale");
  });
  test("회차 없음 · 끝난 회차(exit 0) · 404 · 드라이버 없음은 upload.json 이 말한다 — null", () => {
    assert.equal(S.driverRow(S.stepperModel(driver(), CTX)), null);
    assert.equal(S.driverRow(S.stepperModel(driver({ exit_code: 0 }), CTX)), null);
    assert.equal(S.driverRow(S.stepperModel(null, Object.assign({}, CTX, { status: 404 }))), null);
    assert.equal(S.driverRow(S.stepperModel({ configured: false }, CTX)), null);
  });
});

describe("driverActions — 어느 버튼이 열리는가", () => {
  const model = (patch) => S.stepperModel(driver(patch), CTX);
  const ctx = (patch) => Object.assign({}, ADMIN, { release, releaseStatus: 200, uploadPreset: true }, patch);
  test("회차 없음 + admin + X.Y.Z → Start 만 열린다", () => {
    const a = S.driverActions(model(), ctx());
    assert.equal(a.start.enabled, true);
    assert.equal(a.start.show, true);
    assert.equal(a.abort.show, false);
    assert.equal(a.retry.show, false);
    assert.equal(a.confirm.show, false);
    assert.deepEqual(a.abort.reasons, ["not_running"]);
    assert.deepEqual(a.retry.reasons, ["not_failed"]);
  });
  test("버전이 X.Y.Z 꼴이 아니면 Start 가 닫히고 이유가 있다", () => {
    for (const v of ["", "1.0", "v1.0.1", "1.0.1-rc1", " 1.0.1"]) assert.deepEqual(S.driverActions(model(), ctx({ version: v })).start.reasons, ["version_pattern"], v);
  });
  test("토큰 없음 · 일반 토큰 · 바쁨은 네 버튼 모두의 이유다", () => {
    const m = model({ running: true });
    assert.deepEqual(S.driverActions(m, ctx({ token: null })).abort.reasons, ["no_token"]);
    assert.deepEqual(S.driverActions(m, ctx({ admin: false })).abort.reasons, ["admin"]);
    assert.deepEqual(S.driverActions(m, ctx({ busy: "abort" })).abort.reasons, ["busy"]);
    assert.ok(S.driverActions(model(), ctx({ admin: false })).start.reasons.includes("admin"));
  });
  test("도는 중 — Abort 만 열리고, Start 는 «a round is running»", () => {
    const a = S.driverActions(model({ running: true, status: ["stage S5"] }), ctx());
    assert.equal(a.abort.enabled, true);
    assert.equal(a.abort.show, true);
    assert.equal(a.start.show, false);
    assert.ok(a.start.reasons.includes("running"));
    assert.ok(a.retry.reasons.includes("running"));
    assert.equal(a.rehearsal.enabled, false);
  });
  test("exit 2 — Confirm 은 친 N 이 plan_n 과 글자 그대로 같을 때만", () => {
    const m = model({ exit_code: 2, plan_n: 181 });
    assert.deepEqual(S.driverActions(m, ctx({ typedN: "" })).confirm.reasons, ["n_mismatch"]);
    assert.deepEqual(S.driverActions(m, ctx({ typedN: "180" })).confirm.reasons, ["n_mismatch"]);
    assert.deepEqual(S.driverActions(m, ctx({ typedN: "0181" })).confirm.reasons, ["n_mismatch"]);
    assert.equal(S.driverActions(m, ctx({ typedN: "181" })).confirm.enabled, true);
    assert.equal(S.driverActions(m, ctx({ typedN: "181", admin: false })).confirm.enabled, false);
    const a = S.driverActions(m, ctx({ typedN: "181" }));
    assert.equal(a.start.show, false, "a round waiting for N is a round");
    assert.ok(a.start.reasons.includes("awaiting_n"));
  });
  test("exit 2 + «자동» — Confirm 은 친 N 없이 열린다; 플랜에 n 이 없으면 n_unknown", () => {
    const m = model({ exit_code: 2, plan_n: 181 });
    assert.equal(S.driverActions(m, ctx({ nMode: "auto", typedN: "" })).confirm.enabled, true);
    assert.equal(S.driverActions(m, ctx({ nMode: "auto", typedN: "180" })).confirm.enabled, true, "typed value is ignored in auto");
    assert.equal(S.driverActions(m, ctx({ nMode: null, profile: { build_number_policy: "auto" } })).confirm.enabled, true, "profile default auto");
    assert.deepEqual(S.driverActions(m, ctx({ nMode: null, profile: { build_number_policy: "manual" } })).confirm.reasons, ["n_mismatch"], "profile manual → typed");
    const none = S.stepperModel(driver({ exit_code: 2, plan_n: null, log_tail: [] }), CTX);
    assert.equal(none.dialog, false);
  });
  test("«자동» 회차(auto_n)는 exit 2 에서 대화상자를 열지 않고 머리에 «자동 확인 중» 을 쓴다", () => {
    const m = S.stepperModel(driver({ exit_code: 2, plan_n: 181, auto_n: true }), CTX);
    assert.equal(m.autoN, true);
    assert.equal(m.dialog, false);
    assert.match(m.head, /automatically|자동/);
    const typed = S.stepperModel(driver({ exit_code: 2, plan_n: 181, auto_n: false }), CTX);
    assert.equal(typed.dialog, true);
  });
  test("대화상자가 없으면 Confirm 은 «no_dialog»", () => {
    assert.deepEqual(S.driverActions(model(), ctx({ typedN: "181" })).confirm.reasons, ["no_dialog"]);
  });
  test("exit 1 — Retry same version 이 열린다; Start 도 열린다", () => {
    const a = S.driverActions(model({ exit_code: 1 }), ctx());
    assert.equal(a.retry.enabled, true);
    assert.equal(a.retry.show, true);
    assert.equal(a.start.enabled, true);
  });
  test("exit 3 — Retry 없음, Start 도 «result_unknown»: 다시 올리지 않는다", () => {
    const a = S.driverActions(model({ exit_code: 3 }), ctx());
    assert.equal(a.retry.show, false);
    assert.deepEqual(a.retry.reasons, ["result_unknown"]);
    assert.deepEqual(a.start.reasons, ["result_unknown"]);
  });
  test("exit 4 — Retry 없음(not_failed), Start 는 열린다", () => {
    const a = S.driverActions(model({ exit_code: 4 }), ctx());
    assert.equal(a.retry.show, false);
    assert.equal(a.start.enabled, true);
  });
  test("404 · 드라이버 없음 — Start/Abort/Retry 는 보이지 않고 이유가 있다", () => {
    const na = S.driverActions(S.stepperModel(null, Object.assign({}, CTX, { status: 404 })), ctx());
    assert.equal(na.start.show, false);
    assert.ok(na.start.reasons.includes("na"));
    const nc = S.driverActions(S.stepperModel({ configured: false }, CTX), ctx());
    assert.equal(nc.start.show, false);
    assert.ok(nc.start.reasons.includes("not_configured"));
    assert.equal(nc.rehearsal.show, true, "the rehearsal needs no driver");
  });
  test("예행 — 클라이언트 토큰이면 되고, 플랜에 N 이 있어야 하며, 도는 중엔 닫힌다", () => {
    assert.equal(S.driverActions(model(), ctx({ admin: false })).rehearsal.enabled, true);
    assert.deepEqual(S.driverActions(model(), ctx({ token: null })).rehearsal.reasons, ["no_token"]);
    assert.deepEqual(S.driverActions(model(), ctx({ release: { plan: null } })).rehearsal.reasons, ["no_plan"]);
    assert.equal(S.driverActions(model(), ctx({ uploadPreset: false })).rehearsal.show, false);
    assert.equal(S.driverActions(model(), ctx({ releaseStatus: 404, release: null })).rehearsal.show, false);
  });
  test("모든 이유 키가 두 언어 카탈로그에 있다", () => {
    const m = model({ exit_code: 3 });
    const all = [];
    for (const c of [ctx({ token: null }), ctx({ admin: false, busy: "x", version: "", typedN: "1", release: { plan: null } })]) {
      const a = S.driverActions(m, c);
      Object.keys(a).forEach((k) => a[k].reasons.forEach((r) => all.push(r)));
    }
    for (const r of ["na", "not_configured", "running", "awaiting_n", "n_mismatch", "no_dialog", "not_running"]) all.push(r);
    assert.ok(all.length >= 12);
    all.forEach((r) => assert.ok(I18N.has("driver.reason." + r), r));
  });
});

describe("rehearsalBody · confirmNDecision", () => {
  test("예행 본문은 플랜의 build_name 과 N 그대로 — mode 는 rehearsal 뿐", () => {
    assert.deepEqual(S.rehearsalBody(release), { mode: "rehearsal", build_name: "1.0.1", confirm_build_number: "181" });
    assert.deepEqual(S.rehearsalBody(null), { mode: "rehearsal", build_name: "", confirm_build_number: "" });
  });
  test("친 N 판정 — empty · ok · mismatch · unknown", () => {
    assert.deepEqual(S.confirmNDecision("", 181), { enabled: false, state: "empty" });
    assert.deepEqual(S.confirmNDecision("181", 181), { enabled: true, state: "ok" });
    assert.deepEqual(S.confirmNDecision("180", 181), { enabled: false, state: "mismatch" });
    assert.deepEqual(S.confirmNDecision(" 181", 181), { enabled: false, state: "mismatch" });
    assert.deepEqual(S.confirmNDecision("181", null), { enabled: false, state: "unknown" });
    assert.deepEqual(S.confirmNDecision("181", 181.5), { enabled: false, state: "unknown" });
  });
});

describe("githubCardModel — Source 행의 카드", () => {
  const gh = {
    log: [1, 2, 3, 4, 5, 6].map((i) => ({ sha: "9e1c4d2f" + "0".repeat(32) + i, subject: "feat(x): " + i, author: "pcs", at: iso(i * 3600) })),
    tags: [{ name: "prod/1.0.0-179", at: iso(9 * 86400) }, { name: "prod/1.0.0-180", at: iso(2 * 86400) }],
    prs: null
  };
  test("404 → «not available in this build»; 아직 없으면 loading", () => {
    assert.equal(S.githubCardModel(null, 404, "main", "en", TZ, NOW).state, "na");
    assert.match(S.githubCardModel(null, 404, "main", "en", TZ, NOW).text, /not available in this build/);
    assert.equal(S.githubCardModel(null, 200, "main", "en", TZ, NOW).state, "loading");
  });
  test("커밋 다섯 — sha7 · 제목 · 사람 · 시각; 여섯째는 버린다", () => {
    const g = S.githubCardModel(gh, 200, "main", "en", TZ, NOW);
    assert.equal(g.log.length, 5);
    assert.equal(g.log[0].sha, "9e1c4d2");
    assert.equal(g.log[0].subject, "feat(x): 1");
    assert.equal(g.log[0].author, "pcs");
    assert.match(g.log[0].at, /\d\d:\d\d/);
    assert.equal(g.ref, "main");
  });
  test("태그 — 가장 새 것에 latest, 시각이 없으면 첫 줄", () => {
    const g = S.githubCardModel(gh, 200, "main", "en", TZ, NOW);
    assert.deepEqual(g.tags.map((t) => [t.name, t.latest]), [["prod/1.0.0-179", false], ["prod/1.0.0-180", true]]);
    const noAt = S.githubCardModel({ log: [], tags: [{ name: "a" }, { name: "b" }] }, 200, "main", "en", TZ, NOW);
    assert.deepEqual(noAt.tags.map((t) => t.latest), [true, false]);
  });
  test("prs null → «PR list: next (needs the GH token)»; [] → none; 목록이면 그대로", () => {
    assert.equal(S.githubCardModel(gh, 200, "main", "en", TZ, NOW).prsText, "PR list: next (needs the GH token)");
    assert.equal(S.githubCardModel(Object.assign({}, gh, { prs: [] }), 200, "main", "en", TZ, NOW).prsText, "none");
    const g = S.githubCardModel(Object.assign({}, gh, { prs: [{ number: 412, title: "release/1.0.1", state: "draft" }] }), 200, "main", "en", TZ, NOW);
    assert.equal(g.prsText, null);
    assert.deepEqual(g.prs, [{ number: 412, title: "release/1.0.1", state: "draft" }]);
  });
  test("깨진 항목은 버리고 undefined 를 그리지 않는다", () => {
    const g = S.githubCardModel({ log: [null, {}, "x"], tags: [null, { at: "z" }] }, 200, null, "ko", TZ, NOW);
    assert.equal(g.log.length, 1);
    assert.equal(g.log[0].sha, "");
    assert.equal(g.tags.length, 0);
    assert.equal(g.ref, "main");
    assert.equal(g.prsText, "PR 목록: 다음 (GH 토큰 필요)");
  });
});

describe("makeStoreApi — 드라이버 · GitHub 경로와 본문", () => {
  function api() {
    const calls = [];
    const fetchFn = (path, opts) => { calls.push([opts.method, path, opts.body || null, opts.headers.Authorization || null]); return Promise.resolve({ ok: true, status: 202, text: () => Promise.resolve('{"release_id":7}') }); };
    return { calls, api: S.makeStoreApi(fetchFn, () => "tok") };
  }
  test("start · confirm · abort · retry 는 JSON 본문으로, 토큰을 달고 간다", async () => {
    const { calls, api: a } = api();
    await a.driverStart("app", { build_name: "1.0.1", android_track: "internal", dry_run: true });
    await a.driverConfirm("app", { build_name: "1.0.1", build_number: "181" });
    await a.driverAbort("app", { build_name: "1.0.1" });
    await a.driverRetry("app", { build_name: "1.0.1" });
    await a.driver("app");
    await a.github("app");
    assert.deepEqual(calls.map((c) => [c[0], c[1]]), [
      ["POST", "/api/repos/app/release/start"], ["POST", "/api/repos/app/release/confirm"], ["POST", "/api/repos/app/release/abort"],
      ["POST", "/api/repos/app/release/retry"], ["GET", "/api/repos/app/release/driver"], ["GET", "/api/repos/app/release/github"]]);
    assert.deepEqual(JSON.parse(calls[1][2]), { build_name: "1.0.1", build_number: "181" });
    assert.deepEqual(JSON.parse(calls[2][2]), { build_name: "1.0.1" });
    assert.ok(calls.every((c) => c[3] === "Bearer tok"));
  });
  test("upload 은 하나의 메서드 — 화면은 rehearsal 본문만 보낸다(mode=upload 버튼 없음)", async () => {
    const { calls, api: a } = api();
    await a.upload("app", S.rehearsalBody(release));
    assert.deepEqual(JSON.parse(calls[0][2]), { mode: "rehearsal", build_name: "1.0.1", confirm_build_number: "181" });
  });
});

describe("releaseBarModel — 최상단 릴리스 막대", () => {
  const model = (patch) => S.stepperModel(driver(patch), CTX);
  const running = () => model({ running: true, pid: 4242, status: ["release 1.0.1: stage S5 scenario QA · job #643"] });
  const layersWith = (pct, extra) => Object.assign({ bar: { jobId: 643, preset: "scenario-qa", progress: { pct: pct, basis: "sub", done: 17, total: 30 }, head: pct + "% · 17/30 · chunks", basis: "basis: declared chunks", finishes: "finishes 21:40" },
    now: "Now: #643 scenario-qa", items: [], current: { id: 643, preset: "scenario-qa", state: "running", started_at: iso(600) } }, extra);
  test("회차 없음 · 도는 잡 없음 → null; 끝난 회차(exit 0)도 null", () => {
    assert.equal(S.releaseBarModel(model(), { bar: null, now: null, items: [], current: null }, release, CTX), null);
    assert.equal(S.releaseBarModel(model({ exit_code: 0, status: ["release 1.0.1: done"] }), { current: null }, release, CTX), null);
  });
  test("S5 진행 중 + 잡 57% → 5 단계 끝 + 0.57 → 62%, 파랑, 버전 (N), 지금 줄, 근거", () => {
    const m = S.releaseBarModel(running(), layersWith(57), release, CTX);
    assert.equal(m.tone, "running");
    assert.equal(m.pct, Math.round((5 + 0.57) / 9 * 100));
    assert.equal(m.head, "1.0.1 (181)");
    assert.match(m.stage, /S5 scenario QA · stage 6 of 9/);
    assert.equal(m.now, "Now: #643 scenario-qa");
    assert.match(m.detail, /5\/9 stages done · 62% overall/);
    assert.match(m.detail, /now S5 scenario QA/);
    assert.match(m.detail, /57% · 17\/30 · chunks/);
    assert.match(m.detail, /elapsed 38m/);
    assert.match(m.detail, /finishes 21:40/);
    assert.match(m.basis, /declared stages \+ the running job/);
  });
  test("잡 진행이 없으면 단계만으로 — 5/9 = 56%, 근거는 «선언 단계»", () => {
    const m = S.releaseBarModel(running(), { bar: null, now: null, items: [], current: null }, release, CTX);
    assert.equal(m.pct, 56);
    assert.match(m.basis, /9 declared stages, each worth the same/);
    assert.equal(m.now, null);
  });
  test("99% 상한 — 8 단계 끝 + 잡 99% 여도 99", () => {
    const m8 = model({ running: true, pid: 1, status: ["release 1.0.1: stage S8 tag"] });
    assert.equal(S.releaseBarModel(m8, layersWith(99), release, CTX).pct, 99);
  });
  test("exit 2 사람 차례 → 황토, 머리 문구가 지금 줄; auto_n 회차는 파랑", () => {
    const m = S.releaseBarModel(model({ exit_code: 2, plan_n: 181 }), { current: null }, release, CTX);
    assert.equal(m.tone, "human");
    assert.match(m.now, /waiting for the typed build number 181/);
    const a = S.releaseBarModel(model({ exit_code: 2, plan_n: 181, auto_n: true }), { current: null }, release, CTX);
    assert.equal(a.tone, "running");
  });
  test("exit 3 보라 · exit 1 빨강 · exit 4 황토", () => {
    assert.equal(S.releaseBarModel(model({ exit_code: 3 }), { current: null }, release, CTX).tone, "lost");
    assert.equal(S.releaseBarModel(model({ exit_code: 1 }), { current: null }, release, CTX).tone, "bad");
    assert.equal(S.releaseBarModel(model({ exit_code: 4 }), { current: null }, release, CTX).tone, "warn");
  });
  test("드라이버 없이 릴리스 잡만 돌면 그 잡의 막대 — 버전은 플랜에서, 근거는 잡의 것, 단계 이야기는 없다", () => {
    const none = S.stepperModel({ configured: false }, CTX);
    const m = S.releaseBarModel(none, layersWith(40, { bar: Object.assign(layersWith(40).bar, { startedAt: iso(600) }) }), release, CTX);
    assert.equal(m.pct, 40);
    assert.equal(m.head, "1.0.1 (181)");
    assert.equal(m.stage, "#643 scenario-qa");
    assert.equal(m.basis, "basis: declared chunks");
    assert.equal(m.live, false);
    assert.doesNotMatch(m.detail, /stages/);
    assert.match(m.detail, /^#643 scenario-qa · Now: #643 scenario-qa · 40% · 17\/30 · chunks · elapsed 10m/);
    const noStart = S.releaseBarModel(none, layersWith(null, { bar: Object.assign(layersWith(40).bar, { progress: null, head: "progress —", startedAt: null }) }), release, CTX);
    assert.equal(noStart.elapsed, null, "no started_at → no «elapsed 0s»");
    assert.equal(noStart.pct, null);
  });
});
