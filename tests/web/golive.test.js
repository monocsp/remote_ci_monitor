"use strict";
// 출시 예약(M5s §4.5 · 결정 215 · 216 · 218) — 버전 페이지의 「출시 예약」 영역이 그리는 판단.
// 잠그는 것: status 가 그 플랫폼을 approved 라고 말할 때만 열린다 · both 는 둘 다 승인됐을 때만 ·
// 보내기는 admin · 신선한 plan · 고른 플랫폼 승인 · 사람이 친 번호가 승인 빌드와 같을 때만 ·
// 살아 있는 사슬이 있으면 닫힌다 · 시도하지 않은 플랫폼은 실패가 아니라 「시도 안 함」.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();
const S = rcm.store;

const VERSION = { id: 7, ios_version: "1.2.0", android_version: "1.2.0", build_name: "1.2.0", state: "submitted" };
const PROFILE = { presets: { golive: "release-golive" }, plan_max_age_minutes: 30 };

function status(ios, android) {
  return { job_id: 90, state: "succeeded", stale: false,
    doc: { schema: 1, checked_at: "2026-11-23T01:00:00Z",
      ios: ios && { version: "1.2.0", build: 185, state: ios, raw_state: "X" },
      android: android && { version: "1.2.0", version_code: 185, state: android, raw_state: "Y" } } };
}
function plan(ios, android, extra) {
  return Object.assign({ job_id: 91, state: "succeeded", stale: false, build_name: "1.2.0",
    doc: { schema: 1, build_name: "1.2.0", plan_verdict: "blocked", reasons: [],
      platforms: { ios: ios, android: android } } }, extra || {});
}
const OK185 = { approved: true, approved_build: 185, raw_state: "PENDING_DEVELOPER_RELEASE" };
const NOT = { approved: false, approved_build: null, raw_state: "IN_REVIEW" };

function model(release, form, extra) {
  return S.goliveModel(Object.assign({ version: VERSION, release: release, profile: PROFILE,
    form: Object.assign({ open: true, when: "now", typed: "" }, form || {}), admin: true, token: true, maxAge: 30 }, extra || {}), "en");
}

describe("opening — only on a store's approved word for this version", () => {
  test("no golive role, no status, nothing approved → closed with a reason", () => {
    assert.match(model({}, {}, { profile: { presets: {} } }).openWhy, /golive role/);
    assert.match(model({}).openWhy, /No store status yet/);
    const m = model({ status: status("in_review", "in_review") });
    assert.equal(m.canOpen, false);
    assert.match(m.openWhy, /approved/);
  });
  test("one approved platform opens, and only that platform can be chosen; both needs both", () => {
    const m = model({ status: status("approved", "in_review") });
    assert.equal(m.canOpen, true);
    assert.deepEqual(m.choices, [{ value: "ios", enabled: true }, { value: "android", enabled: false }, { value: "both", enabled: false }]);
    assert.equal(m.platform, "ios");
    const both = model({ status: status("approved", "approved") });
    assert.deepEqual(both.choices.map((c) => c.enabled), [true, true, true]);
    // 고를 수 없는 것을 골라 두었으면 고를 수 있는 첫 것으로 돌아간다
    assert.equal(model({ status: status("approved", "in_review") }, { platform: "both" }).platform, "ios");
  });
  test("a status for another version name does not open it", () => {
    const st = status("approved", "approved");
    st.doc.ios.version = "1.1.9"; st.doc.android.version = "1.1.9";
    assert.equal(model({ status: st }).canOpen, false);
  });
  test("the plan it runs first reads every platform the version has", () => {
    assert.deepEqual(model({ status: status("approved", "in_review") }).planBody, { version_id: 7, mode: "plan", platform: "both" });
  });
});

describe("going live — the server's gate, drawn before sending", () => {
  const rel = (p, live) => ({ status: status("approved", "approved"), golive: { plan: p, result: null, live: live || [] }, jobs: [] });
  test("ready: admin, fresh plan, approved, the typed number equals the approved build", () => {
    const m = model(rel(plan(OK185, NOT)), { platform: "ios", typed: "185" });
    assert.equal(m.why, null);
    assert.deepEqual(m.body, { version_id: 7, mode: "release", platform: "ios", confirm_build_number: "185" });
    const at = model(rel(plan(OK185, NOT)), { platform: "ios", typed: "185", when: "at", at: "2026-11-23T15:00" });
    assert.equal(at.body.start_at, "2026-11-23T15:00");
    assert.equal(at.goLabel, "Book go-live");
  });
  test("each gate closes the button with its own reason; the number is never filled in", () => {
    assert.match(model(rel(plan(OK185, NOT)), { platform: "ios", typed: "185" }, { admin: false }).why, /admin/);
    assert.match(model(rel(plan(OK185, NOT)), { platform: "ios", typed: "" }).why, /Type the approved build number/);
    assert.match(model(rel(plan(OK185, NOT)), { platform: "ios", typed: "auto" }).why, /Type the approved build number/);
    assert.match(model(rel(plan(OK185, NOT)), { platform: "ios", typed: "184" }).why, /not the approved build/);
    assert.match(model(rel(plan(OK185, NOT)), { platform: "both", typed: "185" }).why, /Android is not approved/);
    assert.match(model(rel(plan(OK185, NOT, { stale: true })), { platform: "ios", typed: "185" }).why, /stale/);
    assert.match(model(rel(plan(OK185, Object.assign({}, OK185, { approved_build: 186 }))), { platform: "both", typed: "185" }).why, /different builds/);
    assert.match(model(rel(plan(OK185, NOT)), { platform: "ios", typed: "185", when: "at", at: "" }).why, /Pick a time/);
    assert.equal(model(rel(plan(OK185, NOT)), { platform: "ios", typed: "185" }).body.confirm_build_number, "185");
  });
  test("a booked or running go-live for this version closes it; another version's does not", () => {
    const live = [{ job_id: 12, state: "scheduled", build_name: "1.2.0", platform: "ios", not_before: "2026-11-23T06:00:00Z" }];
    const m = model(rel(plan(OK185, NOT), live), { platform: "ios", typed: "185" });
    assert.match(m.why, /#12 is already booked/);
    assert.equal(m.live.length, 1);
    const other = model(rel(plan(OK185, NOT), [Object.assign({}, live[0], { build_name: "1.1.9" })]), { platform: "ios", typed: "185" });
    assert.equal(other.why, null);
    assert.deepEqual(other.live, []);
  });
  test("a plan still being read, and one that ended without a file", () => {
    const r = rel(plan(OK185, NOT, { job_id: 91 }));
    const running = model(r, { platform: "ios", typed: "185", planJob: 95 });
    assert.equal(running.plan.state, "running");
    assert.match(running.why, /Wait for the plan/);
    r.jobs = [{ id: 95, state: "failed" }];
    assert.equal(model(r, { planJob: 95 }).plan.state, "failed");
  });
});

describe("the last result — not_attempted is not a failure", () => {
  test("golive.json words become tones; reason not_attempted is drawn as «not attempted»", () => {
    const result = { job_id: 30, state: "failed",
      doc: { schema: 1, build_name: "1.2.0", platforms: {
        ios: { result: "failed", raw_state: null, reason: "not_attempted" },
        android: { result: "failed", raw_state: "IN_REVIEW", reason: "not_approved" } } } };
    const m = model({ status: status("approved", "in_review"), golive: { plan: null, result: result, live: [] } });
    assert.deepEqual(m.result.rows.map((r) => [r.platform, r.word, r.tone, r.text]), [
      ["ios", "not_attempted", "na", "iOS not attempted"],
      ["android", "failed", "bad", "Android failed"],
    ]);
    assert.equal(m.result.login, false);
  });
  test("needs_login raises the sign-in note; another version's result is not shown", () => {
    const result = { job_id: 31, state: "failed",
      doc: { schema: 1, build_name: "1.2.0", platforms: { android: { result: "needs_login", raw_state: "X" } } } };
    const m = model({ status: status("approved", "approved"), golive: { plan: null, result: result, live: [] } });
    assert.equal(m.result.login, true);
    assert.equal(m.result.rows[0].tone, "stale");
    result.doc.build_name = "1.1.9";
    assert.equal(model({ status: status("approved", "approved"), golive: { plan: null, result: result, live: [] } }).result, null);
  });
});
