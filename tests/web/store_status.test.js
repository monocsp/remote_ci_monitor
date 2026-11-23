"use strict";
// 스토어 탭 — 검토 패널의 「스토어 상태」(M5s PR E · docs/m5s-workplan.md §4.4 · §7 S15).
// 잠그는 것: 두 스토어의 상태 8값 · 목록에 없는 값은 unknown · raw_state 는 보조로 · checked_at 의
// 나이와 stale · 역할이 없으면 절이 없다 · status 잡은 「지금 하는 일」 막대에 오르지 않는다 ·
// 「새로 조회」 호출은 `POST …/release/status`.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW, TZ } = require("./helpers");

const rcm = load();
const S = rcm.store;
const I18N = require("../../src/remote_ci_monitor/web/i18n.js");
const DASH = "—";

const STATES = ["preparing", "waiting_for_review", "in_review", "approved", "rejected", "releasing", "released", "unknown"];
const PROFILE = { presets: { plan: "release-plan", status: "release-status" }, plan_max_age_minutes: 30 };

function statusDoc(patch) {
  return Object.assign({
    schema: 1, checked_at: "2026-09-04T00:50:12Z",
    ios: { version: "1.2.0", build: 185, state: "approved", raw_state: "PENDING_DEVELOPER_RELEASE", release_type: "MANUAL", phased: null },
    android: { version: "1.2.0", version_code: 185, state: "in_review", raw_state: "RELEASE_LIFECYCLE_STATE_IN_REVIEW", user_fraction: null },
  }, patch);
}
function release(entry, jobs) {
  return { status: entry, jobs: jobs || [] };
}
function entry(doc, patch) {
  return Object.assign({ job_id: 70, state: "succeeded", measured_at: "2026-09-04T00:50:12Z", age_seconds: 120, stale: false, doc: doc }, patch);
}

describe("storeCheckModel — the two stores' review state, read only (§4.4)", () => {
  test("the eight states are the contract's, in order", () => {
    assert.deepEqual(S.STORE_CHECK_STATES, STATES);
  });

  test("no status role, no section", () => {
    assert.equal(S.storeCheckModel(release(entry(statusDoc())), { presets: { plan: "p" } }, "en"), null);
    assert.equal(S.storeCheckModel(release(entry(statusDoc())), null, "en"), null);
  });

  test("both stores with state, raw state, version and build", () => {
    const m = S.storeCheckModel(release(entry(statusDoc())), PROFILE, "en");
    assert.equal(m.present, true);
    assert.equal(m.jobId, 70);
    assert.equal(m.stale, false);
    assert.equal(m.age, "2m ago");
    assert.deepEqual(m.platforms.map((p) => [p.platform, p.state, p.raw, p.version, p.build]), [
      ["ios", "approved", "PENDING_DEVELOPER_RELEASE", "1.2.0", "185"],
      ["android", "in_review", "RELEASE_LIFECYCLE_STATE_IN_REVIEW", "1.2.0", "185"],
    ]);
    assert.equal(m.platforms[0].text, I18N.t("en", "store_check.state.approved"));
    assert.equal(m.platforms[1].text, I18N.t("en", "store_check.state.in_review"));
  });

  test("a state outside the list is drawn as unknown — the raw word stays as a hint", () => {
    const doc = statusDoc({ ios: { state: "APPROVED", raw_state: "PENDING_DEVELOPER_RELEASE" }, android: { state: 3 } });
    const m = S.storeCheckModel(release(entry(doc)), PROFILE, "en");
    assert.deepEqual(m.platforms.map((p) => p.state), ["unknown", "unknown"]);
    assert.equal(m.platforms[0].text, I18N.t("en", "store_check.state.unknown"));
    assert.equal(m.platforms[0].raw, "PENDING_DEVELOPER_RELEASE");
    assert.equal(m.platforms[1].raw, null);
    // 숫자를 지어내지 않는다
    assert.equal(m.platforms[1].version, DASH);
    assert.equal(m.platforms[1].build, DASH);
  });

  test("a missing platform block is unknown, not approved", () => {
    const doc = statusDoc({ android: null });
    const m = S.storeCheckModel(release(entry(doc)), PROFILE, "en");
    assert.equal(m.platforms[1].state, "unknown");
    assert.equal(m.platforms[1].version, DASH);
  });

  test("every state has en and ko text", () => {
    STATES.forEach((s) => {
      ["en", "ko"].forEach((lang) => {
        const text = I18N.t(lang, "store_check.state." + s);
        assert.ok(text && text !== "store_check.state." + s, lang + " " + s);
      });
    });
  });

  test("stale and age come from the server entry; an unknown age is a dash and stale", () => {
    const old = S.storeCheckModel(release(entry(statusDoc(), { age_seconds: 45 * 60, stale: true })), PROFILE, "en");
    assert.equal(old.stale, true);
    assert.equal(old.age, "45m ago");
    const noAge = S.storeCheckModel(release(entry(statusDoc(), { age_seconds: null, stale: true })), PROFILE, "en");
    assert.equal(noAge.age, DASH);
    assert.equal(noAge.stale, true);
    const noFlag = S.storeCheckModel(release(entry(statusDoc(), { stale: undefined })), PROFILE, "en");
    assert.equal(noFlag.stale, true, "missing stale is not fresh");
  });

  test("no document yet: present false, both stores unknown", () => {
    const m = S.storeCheckModel(release(null), PROFILE, "en");
    assert.equal(m.present, false);
    assert.equal(m.jobId, null);
    assert.deepEqual(m.platforms.map((p) => p.state), ["unknown", "unknown"]);
    assert.equal(m.age, DASH);
  });

  test("an unreadable document says why", () => {
    const m = S.storeCheckModel(release(entry(null, { doc_error: "not valid JSON: JSONDecodeError" })), PROFILE, "en");
    assert.equal(m.present, true);
    assert.equal(m.docError, "not valid JSON: JSONDecodeError");
    assert.deepEqual(m.platforms.map((p) => p.state), ["unknown", "unknown"]);
  });

  test("the newest status job still queued or running is the one being checked", () => {
    const jobs = [
      { id: 72, preset: "release-status", role: "status", state: "running" },
      { id: 71, preset: "release-plan", role: "plan", state: "running" },
      { id: 70, preset: "release-status", role: "status", state: "succeeded" },
    ];
    const m = S.storeCheckModel(release(entry(statusDoc()), jobs), PROFILE, "en");
    assert.deepEqual(m.checking, { id: 72, state: "running" });
    const idle = S.storeCheckModel(release(entry(statusDoc()), jobs.slice(1)), PROFILE, "en");
    assert.equal(idle.checking, null);
  });
});

describe("storeCheckModel — a failed check and a document from a failed job say so (#254 review P2)", () => {
  const row = (id, state) => ({ id, preset: "release-status", role: "status", state });
  test("the newest status job failed after the document: last check #N failed", () => {
    ["failed", "timed_out", "lost"].forEach((st) => {
      const m = S.storeCheckModel(release(entry(statusDoc()), [row(72, st), row(70, "succeeded")]), PROFILE, "en");
      assert.deepEqual(m.lastFailed, { id: 72, state: st, text: rcm.stateWord(st, "en") }, st);
      assert.equal(m.checking, null);
    });
  });
  test("only the newest status job counts, and only when it is newer than the document", () => {
    const newerOk = S.storeCheckModel(release(entry(statusDoc()), [row(73, "succeeded"), row(72, "failed")]), PROFILE, "en");
    assert.equal(newerOk.lastFailed, null);
    const older = S.storeCheckModel(release(entry(statusDoc(), { job_id: 75 }), [row(72, "failed")]), PROFILE, "en");
    assert.equal(older.lastFailed, null);
    const running = S.storeCheckModel(release(entry(statusDoc()), [row(74, "running"), row(72, "failed")]), PROFILE, "en");
    assert.equal(running.lastFailed, null);
    assert.deepEqual(running.checking, { id: 74, state: "running" });
  });
  // #254 검증 P3(G 몫) — 예약된 조회나 반복 사슬의 중간 시도는 결과가 아니다. 실패한 조회 뒤에
  // 그런 잡이 있어도 실패 줄이 사라지지 않는다
  test("a scheduled or retried status job after a failed check does not hide the failure", () => {
    ["scheduled", "retried"].forEach((st) => {
      const m = S.storeCheckModel(release(entry(statusDoc()), [row(74, st), row(72, "failed"), row(70, "succeeded")]), PROFILE, "en");
      assert.deepEqual(m.lastFailed, { id: 72, state: "failed", text: rcm.stateWord("failed", "en") }, st);
      assert.equal(m.checking, null, st);
    });
  });
  test("the failed-check line reads naturally for every failed state", () => {
    assert.equal(I18N.t("en", "store_check.last_failed", { id: 690, state: rcm.stateWord("lost", "en") }),
      "Last check #690 did not succeed (lost).");
    assert.equal(I18N.t("en", "store_check.last_failed", { id: 690, state: rcm.stateWord("timed_out", "en"), older: true }),
      "Last check #690 did not succeed (timed out). What follows is from an earlier check.");
  });
  test("no document yet and the only check was lost: it says so", () => {
    const m = S.storeCheckModel(release(null, [row(9, "lost")]), PROFILE, "en");
    assert.deepEqual(m.lastFailed, { id: 9, state: "lost", text: rcm.stateWord("lost", "en") });
  });
  test("a document from a job that did not succeed carries the job's state word", () => {
    const bad = S.storeCheckModel(release(entry(statusDoc(), { state: "failed" })), PROFILE, "ko");
    assert.equal(bad.jobStateOk, false);
    assert.equal(bad.jobStateText, rcm.stateWord("failed", "ko"));
    const good = S.storeCheckModel(release(entry(statusDoc())), PROFILE, "en");
    assert.equal(good.jobStateOk, true);
    assert.equal(good.jobStateText, null);
  });
  test("the failed-check line has en and ko text", () => {
    ["en", "ko"].forEach((lang) => {
      const text = I18N.t(lang, "store_check.last_failed", { id: 72, state: rcm.stateWord("failed", lang) });
      assert.ok(text.indexOf("#72") >= 0 && text.indexOf(rcm.stateWord("failed", lang)) >= 0, lang + " " + text);
    });
  });
});

describe("buildLayers — a status job is not «what runs now» (§3 #11 carry-over)", () => {
  test("a running status job does not take the bar and is not an item", () => {
    const jobs = [
      { id: 80, preset: "release-status", role: "status", state: "running", started_at: "2026-09-04T00:50:00Z" },
      { id: 79, preset: "release-upload", role: "upload", state: "running", started_at: "2026-09-04T00:40:00Z" },
      { id: 78, preset: "release-plan", role: "plan", state: "succeeded" },
    ];
    const L = S.buildLayers(jobs, {}, "en", TZ, NOW);
    assert.equal(L.current.id, 79);
    assert.equal(L.bar.jobId, 79);
    assert.ok(!L.items.concat(L.older).some((it) => it.id === 80), "status rows are not listed");
  });
  test("only a status job running: nothing runs now", () => {
    const L = S.buildLayers([{ id: 80, preset: "release-status", role: "status", state: "running" }], {}, "en", TZ, NOW);
    assert.equal(L.current, null);
    assert.equal(L.bar, null);
    assert.deepEqual(L.items, []);
  });
});

describe("makeStoreApi.checkStatus — POST …/release/status", () => {
  test("posts the body as JSON with the token", async () => {
    const log = [];
    const fetchFn = (path, opts) => {
      log.push([opts.method, path, opts.body, opts.headers]);
      return Promise.resolve({ ok: true, status: 202, text: () => Promise.resolve('{"job_id":5}') });
    };
    const api = S.makeStoreApi(fetchFn, () => "tok");
    const res = await api.checkStatus("my app", { build_name: "1.2.0" });
    assert.deepEqual(res, { ok: true, status: 202, body: { job_id: 5 } });
    assert.equal(log[0][0], "POST");
    assert.equal(log[0][1], "/api/repos/my%20app/release/status");
    assert.equal(log[0][2], JSON.stringify({ build_name: "1.2.0" }));
    assert.equal(log[0][3].Authorization, "Bearer tok");
    await api.checkStatus("app");
    assert.equal(log[1][2], "{}");
  });
});
