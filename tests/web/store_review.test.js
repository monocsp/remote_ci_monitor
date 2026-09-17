"use strict";
// 스토어 탭 — 심사 패널 본체 · Store 행 · Build·upload 행의 순수 함수 (docs/wireframes/web-store.html
// 항목 5~24 · 28 · 36 · 40 · 42 · 46~49, docs/release-contract.md §2·§6). 서버 계약은 STORE-TAB-API-2.
// 잠그는 것: Submit 활성 규칙 · N 은 글자 그대로 · 관리형 게시는 이번 것만 · 띠 판정 · 행 색 · 막대 근거.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW, TZ } = require("./helpers");

const rcm = load();
const S = rcm.store;
const I18N = require("../../src/remote_ci_monitor/web/i18n.js");
const DASH = "—";
const iso = (sec) => new Date(NOW - sec * 1000).toISOString();

function planDoc(patch) {
  return Object.assign({ schema: 1, build_name: "1.0.1", n: 181, first_release: false,
    store: { asc_live: "1.0.0", asc_live_build: 180, asc_editing: "1.0.1", play: { production: 180, internal: 178 } },
    blockers: [], warnings: [], measured_at: iso(240) }, patch);
}
function reviewPlanDoc(patch) {
  return Object.assign({ schema: 2, build_name: "1.0.1", n: 181, plan_verdict: "ok", ios: "ready", android: "ready",
    observed: { ios: "PREPARE_FOR_SUBMISSION", android: "completed", auto_release: false }, listing: { preview: [], diff: [] } }, patch);
}
function release(patch) {
  return Object.assign({
    setup: { required: 3, present: 3, verified: 3, complete: true },
    plan: { job_id: 641, state: "succeeded", measured_at: iso(240), age_seconds: 240, stale: false, build_name: "1.0.1", doc: planDoc() },
    review: { plan: { job_id: 651, state: "succeeded", age_seconds: 200, stale: false, doc: reviewPlanDoc() }, result: null },
    upload: { job_id: 650, state: "succeeded", finished_at: iso(3000), doc: { schema: 1, n: 181, status: "success", mode: "upload", platforms: ["ios", "android"], tag: "prod/1.0.1-181" } },
    jobs: [{ id: 651, preset: "release-review", role: "review", state: "succeeded", started_at: iso(400), finished_at: iso(300) },
      { id: 650, preset: "release-upload", role: "upload", state: "succeeded", started_at: iso(4000), finished_at: iso(3000) },
      { id: 641, preset: "release-plan", role: "plan", state: "succeeded", started_at: iso(5000), finished_at: iso(4900) }]
  }, patch);
}
function withReviewPlan(patch, entryPatch) {
  const r = release();
  r.review.plan = Object.assign({ job_id: 651, state: "succeeded", age_seconds: 200, stale: false, doc: reviewPlanDoc(patch) }, entryPatch || {});
  return r;
}
const PROFILE = { default_branch: "main", plan_max_age_minutes: 30, presets: { plan: "release-plan", upload: "release-upload", review: "release-review" } };
const OK_CTX = () => ({ release: release(), typedN: "181", platforms: { ios: true, android: true }, managed: true, admin: true, token: "t", busy: null });

describe("storeValueText — plan.json 의 값이 객체여도 정직하게", () => {
  test("숫자·글자는 그대로, 없으면 —", () => {
    assert.equal(S.storeValueText(180), "180");
    assert.equal(S.storeValueText("1.0.1"), "1.0.1");
    assert.equal(S.storeValueText(null), DASH);
    assert.equal(S.storeValueText(""), DASH);
  });
  test("객체는 codes → name/status → 짧은 JSON 순이고 [object Object] 는 절대 없다", () => {
    assert.equal(S.storeValueText({ name: "production", status: "completed", codes: [180, 181] }), "180, 181");
    assert.equal(S.storeValueText({ name: "production", status: "inProgress" }), "production · inProgress");
    assert.equal(S.storeValueText({ status: "draft" }), "draft");
    assert.equal(S.storeValueText({ foo: 1 }), '{"foo":1}');
    assert.equal(S.storeValueText({ nested: { a: 1 } }), '{"nested":{"a":1}}');
    assert.equal(S.storeValueText([1, { name: "x" }]), "1, x");
    assert.equal(S.storeValueText([]), DASH);
    const long = S.storeValueText({ k: "v".repeat(100) });
    assert.equal(long.length, 60);
    assert.ok(!/\[object/.test(long));
  });
  test("Store 행 — Play 트랙과 App Store 값이 객체인 plan.json 을 그대로 그린다", () => {
    const r = release();
    r.plan.doc = planDoc({ store: { asc_live: { version: "1.0.0" }, asc_live_build: { build: 180 }, asc_editing: { version: "1.0.1", status: "PREPARE_FOR_SUBMISSION" },
      play: { production: { name: "production", status: "completed", codes: [180] }, internal: { status: "draft" } } } });
    const m = S.storeRowModel(r, 200, { presets: { plan: "release-plan" } }, "en", TZ, NOW);
    const text = m.head.join(" · ") + " " + m.body.builds.join(" ") + " " + m.body.tracks.join(" ");
    assert.ok(!/\[object/.test(text), text);
    assert.ok(m.head.includes("App Store live 1.0.0 (180)"), m.head);
    assert.ok(m.head.includes("editing 1.0.1 · PREPARE_FOR_SUBMISSION"), m.head);
    assert.ok(m.head.includes("Play production 180"), m.head);
    assert.deepEqual(m.body.tracks, ["Play production 180", "Play internal draft"]);
    assert.deepEqual(m.body.builds, ["App Store live 1.0.0 (180)", "App Store editing 1.0.1 · PREPARE_FOR_SUBMISSION"]);
  });
  test("Store 행 — play 가 배열이거나 값이 비면 — 로, 지어내지 않는다", () => {
    const r = release();
    r.plan.doc = planDoc({ store: { asc_live: null, play: [1, 2] } });
    const m = S.storeRowModel(r, 200, { presets: { plan: "release-plan" } }, "en", TZ, NOW);
    assert.ok(m.head.includes(`App Store live ${DASH} (${DASH})`), m.head);
    assert.ok(m.head.includes(`Play ${DASH} ${DASH}`), m.head);
    assert.deepEqual(m.body.tracks, []);
  });
});

describe("module contract", () => {
  test("rcm.store exposes the review-panel pure functions", () => {
    ["verdictTone", "bannerDecision", "reviewPlanVerdict", "nMatches", "platformParam", "submitDecision", "reviewBody",
      "resultIsCurrent", "panelPill", "panelOpen", "fieldCounter", "listingFields", "screenshotGroups", "reviewInfoModel",
      "versionStrip", "resultModel", "refusalText", "storeRowModel", "rowsById", "basisText", "buildLayers", "buildRowModel"]
      .forEach((k) => assert.equal(typeof S[k], "function", k));
  });
  test("the new api calls hit the contract's paths and never a release/publish route", async () => {
    const log = [];
    const api = S.makeStoreApi((path, opts) => { log.push([opts.method, path, opts.body]); return Promise.resolve({ ok: true, status: 202, text: () => Promise.resolve('{"job_id":1}') }); }, () => "tok");
    await api.release("app");
    await api.plan("app", { build_name: "1.0.1", ref: "main" });
    await api.review("app", { mode: "plan" });
    await api.upload("app", { mode: "rehearsal" });
    await api.listing("app");
    await api.validateListing("app", { build_name: "1.0.1", build: "181" });
    await api.github("app");
    await api.driver("app");
    assert.deepEqual(log.map((c) => [c[0], c[1]]), [
      ["GET", "/api/repos/app/release"], ["POST", "/api/repos/app/release/plan"], ["POST", "/api/repos/app/release/review"],
      ["POST", "/api/repos/app/release/upload"], ["GET", "/api/repos/app/release/listing"], ["POST", "/api/repos/app/release/listing/validate"],
      ["GET", "/api/repos/app/release/github"], ["GET", "/api/repos/app/release/driver"],
    ]);
    assert.equal(log[1][2], JSON.stringify({ build_name: "1.0.1", ref: "main" }));
    assert.equal(api.listingFileUrl("my app", "store/a b.png"), "/api/repos/my%20app/release/listing/file?path=store%2Fa%20b.png");
    Object.keys(api).forEach((k) => assert.ok(!/release$|publish|rollout/i.test(k) || k === "release", k + " — no release/publish/rollout call"));
  });
});

describe("verdictTone — the few words rcm colours (contract §2)", () => {
  test("known words", () => {
    assert.equal(S.verdictTone("ready"), "ok");
    assert.equal(S.verdictTone("already_submitted"), "na");
    assert.equal(S.verdictTone("processing"), "running");
    assert.equal(S.verdictTone("store_unreadable"), "stale");
    assert.equal(S.verdictTone("unsafe_release_type"), "bad");
  });
  test("anything else is red, nothing is not judged", () => {
    assert.equal(S.verdictTone("missing_build"), "bad");
    assert.equal(S.verdictTone("version_conflict"), "bad");
    assert.equal(S.verdictTone(null), "none");
    assert.equal(S.verdictTone(""), "none");
  });
});

describe("bannerDecision — the banner that cannot be dismissed (item 36)", () => {
  test("no banner on a clean plan", () => {
    assert.deepEqual(S.bannerDecision(release()), { show: false, reason: null, version: "1.0.1" });
    assert.equal(S.bannerDecision(null).show, false);
  });
  test("unsafe_release_type on either platform", () => {
    assert.equal(S.bannerDecision(withReviewPlan({ ios: "unsafe_release_type" })).reason, "unsafe_release_type");
    assert.equal(S.bannerDecision(withReviewPlan({ android: "unsafe_release_type" })).reason, "unsafe_release_type");
  });
  test("observed.auto_release === true raises it too — only boolean true", () => {
    assert.equal(S.bannerDecision(withReviewPlan({ observed: { auto_release: true } })).reason, "auto_release");
    assert.equal(S.bannerDecision(withReviewPlan({ observed: { auto_release: "true" } })).show, false);
    const r = release();
    r.review.result = { job_id: 660, state: "succeeded", doc: { overall_status: "submitted", build_name: "1.0.1", observed: { auto_release: true } } };
    assert.equal(S.bannerDecision(r).reason, "auto_release");
  });
});

describe("reviewPlanVerdict — ok only when succeeded, fresh, same build, plan_verdict ok", () => {
  test("ok", () => assert.equal(S.reviewPlanVerdict(release().review.plan, "1.0.1"), "ok"));
  test("missing · not succeeded · other build → plan_required", () => {
    assert.equal(S.reviewPlanVerdict(null, "1.0.1"), "plan_required");
    assert.equal(S.reviewPlanVerdict(withReviewPlan({}, { state: "running" }).review.plan, "1.0.1"), "plan_required");
    assert.equal(S.reviewPlanVerdict(withReviewPlan({ build_name: "1.0.2" }).review.plan, "1.0.1"), "plan_required");
  });
  test("stale beats blocked; blocked when plan_verdict is not ok", () => {
    assert.equal(S.reviewPlanVerdict(withReviewPlan({ plan_verdict: "blocked" }, { stale: true }).review.plan, "1.0.1"), "plan_stale");
    assert.equal(S.reviewPlanVerdict(withReviewPlan({ plan_verdict: "blocked" }).review.plan, "1.0.1"), "plan_blocked");
    assert.equal(S.reviewPlanVerdict(withReviewPlan({ plan_verdict: undefined }).review.plan, "1.0.1"), "plan_blocked");
  });
});

describe("nMatches — the human types the number, as a string, exactly (item 28)", () => {
  test("only the exact digits match", () => {
    assert.equal(S.nMatches("181", 181), true);
    assert.equal(S.nMatches("180", 181), false);
    assert.equal(S.nMatches("0181", 181), false);
    assert.equal(S.nMatches(" 181", 181), false);
    assert.equal(S.nMatches("", 181), false);
  });
  test("no plan number → never", () => {
    assert.equal(S.nMatches("181", null), false);
    assert.equal(S.nMatches("181", "181"), false, "n must be a number from plan.json");
    assert.equal(S.nMatches(181, 181), false, "typed must be a string from the input");
    assert.equal(S.nMatches("1.5", 1.5), false);
  });
});

describe("platformParam", () => {
  test("both · ios · android · null", () => {
    assert.equal(S.platformParam({ ios: true, android: true }), "both");
    assert.equal(S.platformParam({ ios: true, android: false }), "ios");
    assert.equal(S.platformParam({ ios: false, android: true }), "android");
    assert.equal(S.platformParam({ ios: false, android: false }), null);
    assert.equal(S.platformParam(null), null);
  });
});

describe("submitDecision — (plan ok ∧ fresh) ∧ (typed N = plan.n) ∧ (no Android ∨ managed ticked now)", () => {
  test("everything right → enabled, no reasons", () => {
    assert.deepEqual(S.submitDecision(OK_CTX()), { enabled: true, reasons: [] });
  });
  test("wrong N stays closed", () => {
    const d = S.submitDecision(Object.assign(OK_CTX(), { typedN: "180" }));
    assert.deepEqual(d, { enabled: false, reasons: ["n_mismatch"] });
  });
  test("Android selected without the managed box → closed; iOS only → open without it", () => {
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { managed: false })).reasons, ["managed_unconfirmed"]);
    assert.equal(S.submitDecision(Object.assign(OK_CTX(), { managed: false, platforms: { ios: true, android: false } })).enabled, true);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { managed: "true" })).reasons, ["managed_unconfirmed"], "only boolean true counts");
  });
  test("plan required · stale · blocked", () => {
    const r = release(); r.review.plan = null;
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { release: r })).reasons, ["plan_required"]);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { release: withReviewPlan({}, { stale: true }) })).reasons, ["plan_stale"]);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { release: withReviewPlan({ plan_verdict: "blocked" }) })).reasons, ["plan_blocked"]);
  });
  test("no token · non-admin · no platform · busy · unsafe", () => {
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { token: null })).reasons, ["no_token"]);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { admin: false })).reasons, ["admin"]);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { platforms: { ios: false, android: false } })).reasons, ["no_platform"]);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { busy: "submit" })).reasons, ["busy"]);
    assert.deepEqual(S.submitDecision(Object.assign(OK_CTX(), { release: withReviewPlan({ ios: "unsafe_release_type" }) })).reasons, ["unsafe"]);
  });
  test("no release plan at all → no_plan + plan_required + n_mismatch, all listed", () => {
    const d = S.submitDecision(Object.assign(OK_CTX(), { release: { plan: null, review: { plan: null } } }));
    assert.equal(d.enabled, false);
    assert.deepEqual(d.reasons, ["no_plan", "plan_required", "n_mismatch"]);
    assert.equal(S.submitDecision({}).enabled, false);
  });
  test("every reason is a catalogue key in both languages", () => {
    ["unsafe", "no_token", "admin", "no_platform", "no_plan", "plan_required", "plan_stale", "plan_blocked", "n_mismatch", "managed_unconfirmed", "busy"]
      .forEach((k) => { assert.ok(I18N.has("review.reason." + k), k); assert.notEqual(I18N.t("ko", "review.reason." + k), I18N.t("en", "review.reason." + k), k); });
  });
});

describe("reviewBody — the contract's inputs, confirmed-on only for a submit that includes Play", () => {
  test("plan mode never carries N or the confirmation", () => {
    assert.deepEqual(S.reviewBody("plan", Object.assign(OK_CTX(), { profile: PROFILE })), {
      build_name: "1.0.1", ref: "main", mode: "plan", platform: "both", confirm_build_number: "",
      play_managed_publishing: "not-checked", listing: "notes-only", phased: "1" });
  });
  test("submit carries the typed N as typed and confirmed-on", () => {
    const b = S.reviewBody("submit", Object.assign(OK_CTX(), { profile: PROFILE, listingFull: true, phased: false }));
    assert.equal(b.mode, "submit");
    assert.equal(b.confirm_build_number, "181");
    assert.equal(b.play_managed_publishing, "confirmed-on");
    assert.equal(b.listing, "full");
    assert.equal(b.phased, "0");
  });
  test("iOS only → not-checked even if the box was ticked; unticked → not-checked", () => {
    assert.equal(S.reviewBody("submit", Object.assign(OK_CTX(), { platforms: { ios: true, android: false } })).play_managed_publishing, "not-checked");
    assert.equal(S.reviewBody("submit", Object.assign(OK_CTX(), { managed: false })).play_managed_publishing, "not-checked");
    assert.equal(S.reviewBody("submit", Object.assign(OK_CTX(), { platforms: { ios: false, android: true } })).platform, "android");
  });
  test("no input named automatic_release · rollout · release_status, ever", () => {
    ["plan", "submit"].forEach((m) => {
      const keys = Object.keys(S.reviewBody(m, OK_CTX()));
      ["automatic_release", "rollout", "release_status"].forEach((k) => assert.ok(!keys.includes(k), m + " " + k));
    });
  });
});

describe("panelPill · panelOpen — why the panel is closed, or the result (items 7 · 27 · 40)", () => {
  const green = { setup: "ok", source: "ok", build: "ok", store: "ok" };
  test("all green, uploaded, no result → not submitted, open", () => {
    const p = S.panelPill(release(), green, 200);
    assert.deepEqual(p, { code: "not_submitted", tone: "none" });
    assert.equal(S.panelOpen(p), true);
  });
  test("404 → not available, closed", () => {
    const p = S.panelPill(null, green, 404);
    assert.equal(p.code, "not_available");
    assert.equal(S.panelOpen(p), false);
  });
  test("the reason order: unsafe > result > secrets > source > plan blocked > waiting for upload", () => {
    assert.equal(S.panelPill(withReviewPlan({ ios: "unsafe_release_type" }), green, 200).code, "unsafe_release_type");
    assert.equal(S.panelPill(release(), Object.assign({}, green, { setup: "bad", source: "bad" }), 200).code, "secrets_expired");
    assert.equal(S.panelPill(release(), Object.assign({}, green, { source: "bad", store: "bad" }), 200).code, "source_not_ready");
    assert.equal(S.panelPill(release(), Object.assign({}, green, { store: "bad" }), 200).code, "plan_blocked");
    assert.equal(S.panelPill(release({ upload: null }), green, 200).code, "waiting_upload");
    assert.equal(S.panelPill(release({ upload: { job_id: 1, state: "succeeded", doc: { status: "rehearsal" } } }), green, 200).code, "waiting_upload");
  });
  test("a result for this build: submitted green · partial amber · noop grey · failed red, and the panel closes", () => {
    const withResult = (status) => { const r = release(); r.review.result = { job_id: 660, state: "succeeded", doc: { overall_status: status, build_name: "1.0.1" } }; return r; };
    assert.deepEqual(S.panelPill(withResult("submitted"), green, 200), { code: "submitted", tone: "ok" });
    assert.deepEqual(S.panelPill(withResult("partial"), green, 200), { code: "partial", tone: "stale" });
    assert.deepEqual(S.panelPill(withResult("noop"), green, 200), { code: "noop", tone: "na" });
    assert.deepEqual(S.panelPill(withResult("failed"), green, 200), { code: "failed", tone: "bad" });
    assert.deepEqual(S.panelPill(withResult("weird"), green, 200), { code: "failed", tone: "bad" }, "an unknown status is not green");
    assert.equal(S.panelOpen(S.panelPill(withResult("submitted"), green, 200)), false);
  });
  test("a result from an older round is ignored", () => {
    const r = release(); r.review.result = { job_id: 600, state: "succeeded", doc: { overall_status: "submitted", build_name: "1.0.0" } };
    assert.equal(S.resultIsCurrent(r), false);
    assert.equal(S.panelPill(r, green, 200).code, "not_submitted");
    const r2 = release(); r2.review.result = { job_id: 600, state: "succeeded", doc: { overall_status: "submitted" } };
    assert.equal(S.resultIsCurrent(r2), false, "no build_name: older job id than the plan");
    r2.review.result.job_id = 700;
    assert.equal(S.resultIsCurrent(r2), true);
  });
  test("remembered choice wins", () => {
    const p = { code: "not_submitted", tone: "none" };
    assert.equal(S.panelOpen(p, "closed"), false);
    assert.equal(S.panelOpen({ code: "submitted", tone: "ok" }, "open"), true);
  });
  test("every pill code has words in both languages", () => {
    ["not_submitted", "waiting_upload", "plan_blocked", "source_not_ready", "secrets_expired", "submitted", "partial", "noop", "failed", "unsafe_release_type", "not_available"]
      .forEach((k) => assert.ok(I18N.has("review.pill." + k), k));
  });
});

describe("fieldCounter — current/limit, amber at 90%, red over (item 11)", () => {
  test("tones", () => {
    assert.deepEqual(S.fieldCounter("Daily notes", 30), { n: 11, limit: 30, tone: "ok", text: "11/30" });
    assert.equal(S.fieldCounter("a".repeat(27), 30).tone, "warn");
    assert.equal(S.fieldCounter("a".repeat(31), 30).tone, "bad");
    assert.equal(S.fieldCounter("", 30).text, "0/30");
  });
  test("code points, not UTF-16 units; no limit → just the count", () => {
    assert.equal(S.fieldCounter("😀😀", 10).n, 2);
    assert.deepEqual(S.fieldCounter("abc", null), { n: 3, limit: null, tone: "none", text: "3" });
    assert.equal(S.fieldCounter(null, 10).n, 0);
  });
  test("store limits are the stores', not a project's", () => {
    assert.deepEqual(S.FIELD_LIMITS, { promotional_text: 170, description: 4000, keywords: 100, support_url: 255, marketing_url: 255, subtitle: 30, title: 30, short_description: 80, full_description: 4000 });
    assert.deepEqual(S.NOTES_LIMIT, { ios: 4000, android: 500 });
  });
});

describe("listingFields — key: value lines from the project's preview, nothing invented (items 10 · 11 · 17 · 21)", () => {
  const listing = { preview: [
    "ios.promotional_text: Short daily notes", "description: A calm journal.", "  with a second line",
    "ios.keywords: a,b,c", "[play] title = Journal", "android/short_description: Short", "whats_new: - one",
    "generated by store_listing.py", "", "subtitle: Daily notes",
  ], diff: ["ios/ko/subtitle: «Daily» → «Daily notes»", "screenshots ios +1"] };
  test("platform from the key, shared keys go to both, continuation lines append", () => {
    const f = S.listingFields(listing);
    assert.equal(f.ios.promotional_text, "Short daily notes");
    assert.equal(f.ios.description, "A calm journal.\nwith a second line");
    assert.equal(f.android.description, "A calm journal.\nwith a second line");
    assert.equal(f.android.title, "Journal");
    assert.equal(f.android.short_description, "Short");
    assert.equal(f.ios.whats_new, "- one");
    assert.equal(f.ios.subtitle, "Daily notes", "an iOS-only key without a platform lands on iOS");
    assert.equal(f.android.subtitle, undefined);
    assert.equal(f.android.promotional_text, undefined);
    assert.deepEqual(f.other, ["generated by store_listing.py"]);
  });
  test("diff lines mark changed fields and screenshots", () => {
    const f = S.listingFields(listing);
    assert.deepEqual(f.changed, { subtitle: true });
    assert.equal(f.screenshotsChanged, true);
    assert.deepEqual(S.listingFields({ preview: [], diff: [] }).changed, {});
  });
  test("aliases: camelCase and known synonyms; garbage is tolerated", () => {
    const f = S.listingFields({ preview: ["promotionalText: x", "ios.supportUrl: https://e", "android.app_name: N", "release_notes: r"] });
    assert.equal(f.ios.promotional_text, "x");
    assert.equal(f.ios.support_url, "https://e");
    assert.equal(f.android.title, "N");
    assert.equal(f.android.whats_new, "r");
    assert.deepEqual(S.listingFields(null), { ios: {}, android: {}, other: [], changed: {}, screenshotsChanged: false });
    assert.deepEqual(S.listingFields({ preview: [null, 42] }).other, ["42"]);
  });
  test("the field lists keep the same length on both sides so the rows line up (item 15)", () => {
    assert.equal(S.IOS_FIELDS.length, 6);
    assert.equal(S.PLAY_FIELDS.length, 7);
    S.IOS_FIELDS.concat(S.PLAY_FIELDS).forEach((k) => assert.ok(I18N.has("review.field." + k), k));
  });
});

describe("screenshotGroups — by path, unsorted stays visible as unsorted", () => {
  test("ios · android · other", () => {
    const g = S.screenshotGroups({ screenshots: [
      { path: "store/screenshots/ios/ko/01.png" }, { path: "metadata/android/ko-KR/images/phoneScreenshots/1.png" },
      { path: "store/misc/banner.png" }, { path: "" }, null, { path: "fastlane/screenshots/en-US/iPhone 6.5/01.png" }] });
    assert.deepEqual(g.ios.map((s) => s.path), ["store/screenshots/ios/ko/01.png"]);
    assert.deepEqual(g.android.map((s) => s.path), ["metadata/android/ko-KR/images/phoneScreenshots/1.png"]);
    assert.deepEqual(g.other.map((s) => s.path), ["store/misc/banner.png", "fastlane/screenshots/en-US/iPhone 6.5/01.png"]);
    assert.deepEqual(S.screenshotGroups(null), { ios: [], android: [], other: [] });
  });
});

describe("reviewInfoModel — present/absent per file, never a value (item 14)", () => {
  test("from the dir secrets", () => {
    const m = S.reviewInfoModel([{ name: "GH_TOKEN", kind: "value", present: true }, { name: "review_information", kind: "dir", present: false,
      files: [{ name: "demo_user.txt", present: true }, { name: "demo_password.txt", present: false, value: "LEAK" }] }]);
    assert.equal(m.name, "review_information");
    assert.deepEqual(m.files, [{ name: "demo_user.txt", present: true }, { name: "demo_password.txt", present: false }]);
    assert.equal(m.complete, false);
    assert.equal(m.missing, 1);
    assert.ok(!JSON.stringify(m).includes("LEAK"));
    assert.equal(S.reviewInfoModel([{ name: "x", kind: "value" }]), null);
  });
});

describe("versionStrip (item 8)", () => {
  test("version · N · build note · listing sha · notes counter against the Play limit", () => {
    const s = S.versionStrip(release(), { sha: "9e1c4d2abcdef", release_notes: { path: "store/release_notes/1.0.1/ko.txt", text: "• one\n• two" } }, PROFILE, "en", TZ, NOW);
    assert.equal(s.version, "1.0.1");
    assert.equal(s.build, 181);
    assert.equal(s.buildNote, "uploaded by rcm #650");
    assert.equal(s.listing, "from main @ 9e1c4d2");
    assert.equal(s.notesPath, "store/release_notes/1.0.1/ko.txt");
    assert.equal(s.notesCounter.text, "11/500");
    assert.equal(s.tag, "prod/1.0.1-181");
  });
  test("nothing known → dashes, not zeros", () => {
    const s = S.versionStrip(null, null, null, "en", TZ, NOW);
    assert.equal(s.version, DASH);
    assert.equal(s.build, DASH);
    assert.equal(s.buildNote, "not uploaded yet");
    assert.equal(s.listing, `from main @ ${DASH}`);
    assert.equal(s.notesPath, null);
    assert.equal(s.notesCounter, null);
  });
});

describe("resultModel — four outcomes with observed state (item 40)", () => {
  test("submitted with observed and reason", () => {
    const m = S.resultModel({ job_id: 660, state: "succeeded", doc: { overall_status: "submitted",
      platforms: { ios: { status: "submitted", reason: null }, android: { status: "skipped", reason: "managed_publishing_unconfirmed" } },
      observed: { ios: "WAITING_FOR_REVIEW", android: "completed", auto_release: false }, phased_release: true } }, "en");
    assert.equal(m.tone, "ok");
    assert.deepEqual(m.platforms.map((p) => p.text), ["ios submitted · observed WAITING_FOR_REVIEW", "android skipped · reason managed_publishing_unconfirmed · observed completed"]);
    assert.deepEqual(m.extras, ["phased"]);
    assert.equal(m.autoRelease, false);
  });
  test("partial amber · noop grey · failed red · unknown red; no doc → null", () => {
    const of = (s) => S.resultModel({ doc: { overall_status: s } }, "en").tone;
    assert.equal(of("partial"), "stale");
    assert.equal(of("noop"), "na");
    assert.equal(of("failed"), "bad");
    assert.equal(of("???"), "bad");
    assert.equal(S.resultModel({ doc: null }, "en"), null);
    assert.equal(S.resultModel({ doc: { overall_status: "submitted", auto_release: true } }, "en").autoRelease, true);
  });
});

describe("refusalText — the server's code, verbatim, next to the button", () => {
  test("409 code · plain error · nothing", () => {
    assert.equal(S.refusalText({ status: 409, body: { error: "x", code: "managed_publishing_unconfirmed" } }, "en"), "server refused: managed_publishing_unconfirmed");
    assert.equal(S.refusalText({ status: 409, body: { error_code: "build_number_mismatch" } }, "ko"), "서버가 거부함: build_number_mismatch");
    assert.equal(S.refusalText({ status: 500, body: { error: "boom" } }, "en"), "boom");
    assert.equal(S.refusalText({ status: 502, body: null }, "en"), "http 502");
  });
});

describe("storeRowModel — plan.json summary, colour from blockers/stale (items 6 · 42)", () => {
  test("green head: live · editing · Play track · next N · plan age", () => {
    const m = S.storeRowModel(release(), 200, PROFILE, "en", TZ, NOW);
    assert.equal(m.state, "ok");
    assert.deepEqual(m.head, ["App Store live 1.0.0 (180)", "editing 1.0.1", "Play production 180", "next N 181", "plan 4m ago"]);
    assert.deepEqual(m.body.builds, ["App Store live 1.0.0 (180)", "App Store editing 1.0.1"]);
    assert.deepEqual(m.body.tracks, ["Play production 180", "Play internal 178"]);
    assert.equal(m.body.next, "181");
    assert.equal(m.body.jobId, 641);
  });
  test("blockers → red with the count; stale → amber with the age; first release → amber", () => {
    const blocked = release(); blocked.plan.doc = planDoc({ n: null, blockers: [{ code: "B-PLAYBUSY", text: "Play has a draft" }] });
    const m = S.storeRowModel(blocked, 200, PROFILE, "en", TZ, NOW);
    assert.equal(m.state, "bad");
    assert.ok(m.head.includes("1 blockers") && m.head.includes(`next N ${DASH}`), m.head);
    assert.deepEqual(m.body.blockers, ["B-PLAYBUSY · Play has a draft"]);
    const stale = release(); stale.plan.stale = true; stale.plan.age_seconds = 47 * 60;
    const s = S.storeRowModel(stale, 200, PROFILE, "en", TZ, NOW);
    assert.equal(s.state, "stale");
    assert.equal(s.head[s.head.length - 1], "stale · 47m ago");
    const first = release(); first.plan.doc = planDoc({ first_release: true });
    assert.equal(S.storeRowModel(first, 200, PROFILE, "en", TZ, NOW).state, "stale");
  });
  test("running plan → blue; failed or unreadable plan → red; no plan → grey; 404 → grey «not available»", () => {
    const run = release(); run.plan = { job_id: 642, state: "running", doc: null };
    assert.equal(S.storeRowModel(run, 200, PROFILE, "en", TZ, NOW).state, "running");
    const bad = release(); bad.plan = { job_id: 642, state: "failed", doc: null };
    assert.equal(S.storeRowModel(bad, 200, PROFILE, "en", TZ, NOW).state, "bad");
    const unreadable = release(); unreadable.plan = { job_id: 642, state: "succeeded", doc: null, doc_error: "bad json" };
    assert.deepEqual(S.storeRowModel(unreadable, 200, PROFILE, "en", TZ, NOW).head, ["plan #642 unreadable"]);
    assert.deepEqual(S.storeRowModel(release({ plan: null }), 200, PROFILE, "en", TZ, NOW), { state: "na", head: ["no plan yet — press Refresh"], body: null });
    const na = S.storeRowModel(null, 404, PROFILE, "en", TZ, NOW);
    assert.equal(na.state, "na");
    assert.equal(na.head[0], "Store status is not available in this build");
    assert.equal(S.storeRowModel(release(), 200, { presets: {} }, "en", TZ, NOW).head[0], "not configured — presets.plan is empty");
  });
});

describe("rowsById · basisText · buildLayers — three layers from jobs + queue progress (items 46~48)", () => {
  const runningRow = { id: 643, state: "running", started_at: iso(2280), estimate: { expected_seconds: 3600, elapsed_seconds: 2280, source: "measured", finish_at: iso(-1320) },
    progress: { phase: "executing", steps_total: 7, steps_done: 2, current_index: 3, current_name: "scenarios (default)", current_seconds: 100, job_seconds: 2280,
      steps: [{ index: 1, name: "a", state: "done", ok: true }, { index: 2, name: "b", state: "done", ok: true }, { index: 3, name: "scenarios (default)", state: "running", started_at: iso(100) }],
      sub: { done: 41, total: 68, unit: "emotion_drive_basic/android", state: "run", note: "1m 40s" },
      units: [{ unit: "a/ios", state: "ok" }, { unit: "b/android", state: "run" }] } };
  const jobs = [{ id: 643, preset: "scenario-qa", role: "qa", state: "running", started_at: iso(2280) },
    { id: 642, preset: "gate-smoke", role: "gate", state: "succeeded", started_at: iso(4000), finished_at: iso(2735) },
    { id: 641, preset: "release-plan", role: "plan", state: "succeeded", started_at: iso(5000), finished_at: iso(4928) },
    { id: 650, preset: "release-upload", role: "upload", state: "queued" }];
  test("rowsById reads every pool's queue and recent", () => {
    const m = S.rowsById({ pools: [{ queue: [runningRow], recent: [{ id: 1 }] }, { queue: null, recent: [{ id: 2 }, null] }] });
    assert.deepEqual(Object.keys(m).sort(), ["1", "2", "643"]);
    assert.deepEqual(S.rowsById(null), {});
  });
  test("basis is said in words: sub marker · declared steps · measured time · none", () => {
    assert.equal(S.basisText({ basis: "sub", unit: "chunk/android" }, "en"), "basis: declared chunk/android (progress marker)");
    assert.equal(S.basisText({ basis: "steps" }, "en"), "basis: declared steps");
    assert.equal(S.basisText({ basis: "time", source: "measured", expected: 1260 }, "en"), "basis: by time · median 21m 00s");
    assert.equal(S.basisText({ basis: "time", source: "preset", expected: 600 }, "en"), "basis: by time · preset estimate 10m 00s");
    assert.equal(S.basisText({ basis: "none" }, "en"), "basis: none — no percentage");
    assert.equal(S.basisText(null, "en"), "basis: none — no percentage");
  });
  test("layer 1: the running job's bar with the sub marker as its basis and the finish clock", () => {
    const L = S.buildLayers(jobs, S.rowsById({ pools: [{ queue: [runningRow] }] }), "en", TZ, NOW);
    assert.equal(L.current.id, 643);
    assert.equal(L.bar.progress.basis, "sub");
    assert.equal(L.bar.head, "60% · 41/68 · emotion_drive_basic/android");
    assert.equal(L.bar.basis, "basis: declared emotion_drive_basic/android (progress marker)");
    assert.match(L.bar.finishes, /^finishes \d\d:\d\d$/);
  });
  test("layer 2: «Now» line = job id · preset · the queue's progress head with the marker", () => {
    const L = S.buildLayers(jobs, S.rowsById({ pools: [{ queue: [runningRow] }] }), "en", TZ, NOW);
    assert.match(L.now, /^Now: #643 scenario-qa · step 3\/7 · scenarios \(default\) · 1m 40s · job 38m 00s · now: emotion_drive_basic\/android · run · 1m 40s$/, L.now);
  });
  test("layer 3: checklist in job order — done ✓ · running ▶ · waiting ○ — with durations", () => {
    const L = S.buildLayers(jobs, S.rowsById({ pools: [{ queue: [runningRow] }] }), "en", TZ, NOW);
    assert.deepEqual(L.items.map((i) => [i.id, i.mark, i.tone, i.running]), [[641, "✓", "ok", false], [642, "✓", "ok", false], [643, "▶", "running", true], [650, "○", "wait", false]]);
    assert.equal(L.items[0].text, "#641 release-plan · plan · succeeded · 1m 12s");
    assert.equal(L.items[1].text, "#642 gate-smoke · gate · succeeded · 21m 05s");
    assert.equal(L.items[2].text, "#643 scenario-qa · QA · running · 38m 00s");
    assert.equal(L.items[3].text, "#650 release-upload · upload · waiting");
    assert.equal(L.items[2].row, runningRow, "the running item carries its queue row for the job card");
  });
  test("a running job the queue no longer shows → bar with no basis, no percentage", () => {
    const L = S.buildLayers(jobs, {}, "en", TZ, NOW);
    assert.equal(L.bar.progress, null);
    assert.equal(L.bar.head, "progress —");
    assert.equal(L.bar.basis, "basis: none — no percentage");
    assert.equal(L.bar.finishes, null);
    assert.equal(L.now, "Now: #643 scenario-qa");
  });
  test("nothing running → no bar, no now line; failed and lost are red", () => {
    const L = S.buildLayers([{ id: 1, preset: "p", role: "plan", state: "failed" }, { id: 2, preset: "p", state: "lost" }, { id: 3, preset: "p", state: "cancelled" }], {}, "en", TZ, NOW);
    assert.equal(L.bar, null);
    assert.equal(L.now, null);
    assert.deepEqual(L.items.map((i) => [i.mark, i.tone]), [["✗", "bad"], ["?", "bad"], ["□", "na"]]);
    assert.deepEqual(S.buildLayers(null, null, "en", TZ, NOW), { bar: null, now: null, items: [], current: null });
  });
});

describe("buildRowModel — colour and head (items 5 · 38 · 49)", () => {
  test("uploaded → green: version (N) · uploaded · platforms · clock · job · tag", () => {
    const m = S.buildRowModel(release(), 200, PROFILE, {}, "en", TZ, NOW);
    assert.equal(m.state, "ok");
    assert.equal(m.head[0], "1.0.1 (181)");
    assert.equal(m.head[1], "uploaded");
    assert.equal(m.head[2], "platforms ios + android");
    assert.match(m.head[3], /^uploaded \d\d:\d\d$/);
    assert.deepEqual(m.head.slice(4), ["#650", "prod/1.0.1-181"]);
  });
  test("running → blue, collapsed head = version (N) · stage · done/total · ~clock (item 49)", () => {
    const r = release({ upload: null });
    r.jobs = [{ id: 643, preset: "scenario-qa", role: "qa", state: "running", started_at: iso(2280) }].concat(r.jobs);
    const rows = { 643: { id: 643, state: "running", started_at: iso(2280), estimate: { expected_seconds: 3600, elapsed_seconds: 2280, source: "measured", finish_at: iso(-1320) },
      progress: { phase: "executing", steps: [], sub: { done: 41, total: 68, unit: "c/android", state: "run" } } } };
    const m = S.buildRowModel(r, 200, PROFILE, rows, "en", TZ, NOW);
    assert.equal(m.state, "running");
    assert.equal(m.head[0], "1.0.1 (181)");
    assert.equal(m.head[1], "QA #643");
    assert.equal(m.head[2], "41/68");
    assert.match(m.head[3], /^finishes \d\d:\d\d$/);
    assert.equal(m.layers.items.length, 4);
  });
  test("lost upload → red with the reconcile sentence and no resubmit; failed → red; partial → red; rehearsal → grey", () => {
    const lost = release({ upload: { job_id: 650, state: "lost", doc: null } });
    const m = S.buildRowModel(lost, 200, PROFILE, {}, "en", TZ, NOW);
    assert.equal(m.state, "bad");
    assert.equal(m.note, "lost");
    assert.match(m.head[0], /^Upload job #650 was lost\. The store may or may not have 181\. Do not resubmit/);
    assert.equal(S.buildRowModel(release({ upload: { job_id: 650, state: "failed", doc: null } }), 200, PROFILE, {}, "en", TZ, NOW).state, "bad");
    assert.equal(S.buildRowModel(release({ upload: { job_id: 650, state: "succeeded", doc: { n: 181, status: "partial", platforms: ["ios"] } } }), 200, PROFILE, {}, "en", TZ, NOW).state, "bad");
    assert.equal(S.buildRowModel(release({ upload: { job_id: 650, state: "succeeded", doc: { n: 181, status: "rehearsal" } } }), 200, PROFILE, {}, "en", TZ, NOW).state, "na");
  });
  test("no upload, nothing running → grey «No release running»; 404 → grey «not available»; no preset → not configured", () => {
    const m = S.buildRowModel(release({ upload: null }), 200, PROFILE, {}, "en", TZ, NOW);
    assert.equal(m.state, "na");
    assert.equal(m.head[0], "No release running");
    assert.deepEqual(S.buildRowModel(null, 404, PROFILE, {}, "en", TZ, NOW).head, ["No release running · not available in this build"]);
    assert.equal(S.buildRowModel(release(), 200, { presets: { plan: "x" } }, {}, "en", TZ, NOW).head[0], "not configured — presets.upload is empty");
  });
});

describe("catalogue — no release/publish/rollout button label, fixed sentences present", () => {
  test("the only strings naming Release/Publish are the fixed policy sentences, never a button label", () => {
    ["review.validate", "review.plan", "review.submit", "review.dialog_go", "store.refresh_plan", "source.fetch", "store.refresh"].forEach((k) => {
      ["en", "ko"].forEach((lang) => assert.ok(!/release this version|publish|rollout/i.test(I18N.t(lang, k)), lang + "/" + k));
    });
    assert.match(I18N.t("en", "review.policy"), /^Approval does not release\./);
    assert.match(I18N.t("ko", "review.policy"), /^승인은 출시가 아닙니다\./);
    assert.match(I18N.t("en", "review.check.managed"), /no API can check this/);
  });
});
