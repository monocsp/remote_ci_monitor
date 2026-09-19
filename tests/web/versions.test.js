"use strict";
// 버전 목록 · 새 버전 대화상자 · 상태 띠의 순수 함수 (docs/version-page-workplan.md §3.1 · §11 · §12).
//
// 잠그는 것:
// - `parseRoute` 가 화면 셋을 가른다 — 목록 · 버전 하나 · 상태(AC-C1).
// - `nextVersionHint` 가 힌트를 **지어내지 않는다**: 끝이 정수가 아니면 없는 것이다(`1.0.0-rc1`).
//   서버의 `release_state.next_version_hint` 와 같은 답을 내야 한다 — 대화상자가 채우는 값과
//   서버가 받아들이는 값이 갈라지면 사람이 고친 적 없는 400 을 본다.
// - `versionNameCheck` 의 세 이유(`empty` · `pattern` · `not_greater`)가 서버의 400 코드와 같다.
// - `versionListModel` 이 §14-5 의 만료 플래그와 §15 의 «진행 중» 규칙을 행에 그린다.
// - `statusStripModel` 이 §12 의 칩 넷을 그대로 만든다 — 하나라도 빨가면 띠가 빨갛다.

const test = require("node:test");
const { describe } = require("node:test");
const assert = require("node:assert/strict");

const { load, NOW, TZ } = require("./helpers");
const S = load().store;

/** 계획 문서 — 프로젝트 스크립트가 쓰는 `plan.json` 의 모양(계약 §2). */
function planDoc(patch) {
  return Object.assign({
    schema: 1, build_name: "1.1.1", n: 181,
    store: { asc_live: "1.1.0", asc_live_build: 180, play: { production_name: "1.0.0", production: 180 } },
    blockers: [], warnings: [],
  }, patch || {});
}

/** 드래프트 행 — 서버 `_version_json` 이 내는 공개 모양. */
function draft(patch) {
  return Object.assign({
    id: 7, repo: "app", ios_version: "1.1.1", android_version: "1.0.1", build_name: "1.1.1",
    state: "editing", created_by: "pcs", created_at: new Date(NOW - 2400 * 1000).toISOString(),
    last_edit_at: null, expires_at: new Date(NOW + 3600 * 1000).toISOString(),
    expired: false, expiry_warned: 0, asc_version_id: "abc123", error: null,
    create_job_id: 641, delete_job_id: null, release_id: null, review_job_id: null,
    upload_job_id: null, has_prefill: true, has_edits: false, changed: 0,
  }, patch || {});
}

function versionsDoc(patch) {
  return Object.assign({
    live: { ios: "1.1.0", android: "1.0.0", from_plan_job: 641 },
    hints: { ios: "1.1.1", android: "1.0.1" },
    ttl_hours: 24, drafts: [draft()], history: [],
  }, patch || {});
}

describe("module contract", () => {
  test("rcm.store exposes the new pure functions", () => {
    ["parseRoute", "storeHash", "bumpLastNumber", "liveVersions", "nextVersionHint", "versionNameCheck",
      "versionTitle", "versionRowModel", "versionListModel", "statusStripModel"].forEach((k) => {
      assert.equal(typeof S[k], "function", k);
    });
  });
});

describe("parseRoute — 스토어 탭의 화면 셋 (AC-C1)", () => {
  test("목록 · 버전 하나 · 상태", () => {
    assert.deepEqual(S.parseRoute("#/store/app"), { view: "store", repo: "app", sub: "versions", id: null });
    assert.deepEqual(S.parseRoute("#/store/app/v/7"), { view: "store", repo: "app", sub: "version", id: 7 });
    assert.deepEqual(S.parseRoute("#/store/app/v/7/"), { view: "store", repo: "app", sub: "version", id: 7 });
    assert.deepEqual(S.parseRoute("#/store/app/status"), { view: "store", repo: "app", sub: "status", id: null });
  });
  test("버전 번호는 숫자뿐이다 — 그 밖의 꼬리는 스토어 주소가 아니다", () => {
    ["#/store/app/v/abc", "#/store/app/v/", "#/store/app/v/7/edit", "#/store/app/statuses"].forEach((h) => {
      assert.equal(S.parseRoute(h).view, "queue", h);
    });
  });
  test("storeHash 는 parseRoute 의 역이다", () => {
    ["versions", "status"].forEach((sub) => {
      assert.deepEqual(S.parseRoute(S.storeHash("my app", sub)), { view: "store", repo: "my app", sub: sub, id: null });
    });
    assert.deepEqual(S.parseRoute(S.storeHash("app", "version", 12)), { view: "store", repo: "app", sub: "version", id: 12 });
  });
});

describe("makeStoreApi — 버전 네 라우트의 주소와 메서드", () => {
  // 화면은 스텁 API 로 시험한다(CDP). 그래서 **진짜 주소를 만드는 자리**는 여기서 잠근다 —
  // 서버의 `_VERSIONS_RE` 와 글자 그대로 맞아야 하고, 아니면 아무 화면 테스트도 안 빨개진다.
  test("주소 · 메서드 · 본문", async () => {
    const log = [];
    const api = S.makeStoreApi((path, opts) => {
      log.push({ path: path, method: opts.method, body: opts.body });
      return Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve("{}") });
    }, () => "tok-1");
    await api.versions("my app");
    await api.versionCreate("app", { ios_version: "1.1.1" });
    await api.version("app", 7);
    await api.versionDiscard("app", 7);
    assert.deepEqual(log.map((c) => [c.method, c.path]), [
      ["GET", "/api/repos/my%20app/release/versions"],
      ["POST", "/api/repos/app/release/versions"],
      ["GET", "/api/repos/app/release/versions/7"],
      ["DELETE", "/api/repos/app/release/versions/7"],
    ]);
    assert.equal(log[1].body, '{"ios_version":"1.1.1"}');
    assert.equal(log[0].body, undefined);
  });
});

describe("nextVersionHint — 마지막 정수 +1, 모르면 없다 (AC-C1 · E1)", () => {
  test("여섯 가지", () => {
    // ① 문서가 힌트를 주면 그대로 ② iOS 는 asc_live ③ Android 는 play.production_name
    assert.deepEqual(S.nextVersionHint(planDoc({ next_version_hint: { ios: "2.0.0", android: "2.0.0" } })),
      { ios: "2.0.0", android: "2.0.0" });
    assert.deepEqual(S.nextVersionHint(planDoc()), { ios: "1.1.1", android: "1.0.1" });
    // ④ 자리 수가 둘이어도 마지막 정수만 올린다 ⑤ 정수로 안 끝나면 지어내지 않는다
    assert.equal(S.bumpLastNumber("2.0"), "2.1");
    assert.equal(S.bumpLastNumber("1.0.0-rc1"), null);
    assert.deepEqual(S.nextVersionHint(planDoc({ store: { asc_live: "1.0.0-rc1", play: {} } })), { ios: null, android: null });
    // ⑥ 플랜이 아예 없으면 둘 다 모른다(E1)
    assert.deepEqual(S.nextVersionHint(null), { ios: null, android: null });
  });
  test("빈 힌트는 힌트가 아니다 — 라이브로 떨어진다", () => {
    assert.deepEqual(S.nextVersionHint(planDoc({ next_version_hint: { ios: "  ", android: null } })),
      { ios: "1.1.1", android: "1.0.1" });
  });
  test("liveVersions 는 문서가 말한 것만 말한다", () => {
    assert.deepEqual(S.liveVersions(planDoc()), { ios: "1.1.0", android: "1.0.0" });
    assert.deepEqual(S.liveVersions({ store: {} }), { ios: null, android: null });
    assert.deepEqual(S.liveVersions(undefined), { ios: null, android: null });
  });
});

describe("versionNameCheck — 꼴과 크기 (AC-C1)", () => {
  test("다섯 가지", () => {
    assert.deepEqual(S.versionNameCheck("1.1.1", "1.1.0"), { ok: true, reason: null });
    assert.deepEqual(S.versionNameCheck("", "1.1.0"), { ok: false, reason: "empty" });
    assert.deepEqual(S.versionNameCheck("1.0", "1.1.0"), { ok: false, reason: "pattern" });
    assert.deepEqual(S.versionNameCheck("1.0.9", "1.1.0"), { ok: false, reason: "not_greater" });
    assert.deepEqual(S.versionNameCheck("1.1.0", "1.1.0"), { ok: false, reason: "not_greater" });
  });
  test("라이브를 모르면 꼴만 본다 (E1)", () => {
    assert.deepEqual(S.versionNameCheck("0.0.1", null), { ok: true, reason: null });
    assert.deepEqual(S.versionNameCheck("0.0.1", ""), { ok: true, reason: null });
  });
  test("라이브가 숫자 꼴이 아니면 크기는 안 본다", () => {
    assert.deepEqual(S.versionNameCheck("1.0.0", "1.0.0-rc1"), { ok: true, reason: null });
  });
  test("자리 수가 두 자리여도 정수로 비교한다 — 글자 비교가 아니다", () => {
    assert.deepEqual(S.versionNameCheck("1.10.0", "1.9.0"), { ok: true, reason: null });
    assert.deepEqual(S.versionNameCheck("1.9.0", "1.10.0"), { ok: false, reason: "not_greater" });
  });
  test("문자열이 아닌 것도 안 던진다", () => {
    [null, undefined, 111, {}].forEach((v) => assert.deepEqual(S.versionNameCheck(v, "1.0.0"), { ok: false, reason: "empty" }));
  });
});

describe("versionTitle — 두 스토어 이름을 옮기는 규칙 (§11)", () => {
  test("언제나 둘 다, 같으면 하나, 한쪽만이면 그 이름", () => {
    assert.equal(S.versionTitle("1.1.1", "1.0.1"), "iOS 1.1.1 · Android 1.0.1");
    assert.equal(S.versionTitle("1.1.1", "1.1.1"), "1.1.1");
    assert.equal(S.versionTitle("1.1.1", null), "iOS 1.1.1");
    assert.equal(S.versionTitle(null, "1.0.1"), "Android 1.0.1");
    assert.equal(S.versionTitle(null, null), "—");
  });
});

describe("versionListModel — W1 의 행들", () => {
  test("드래프트 · 라이브 · 지난 것이 각각 온다", () => {
    const m = S.versionListModel(versionsDoc({
      history: [{ id: 3, ios: "1.1.0", android: "1.0.0", submitted_at: new Date(NOW - 86400 * 1000).toISOString(), review_job_id: 612 }],
    }), "en", NOW);
    assert.equal(m.ttlHours, 24);
    assert.equal(m.live.title, "iOS 1.1.0 · Android 1.0.0");
    assert.equal(m.live.known, true);
    assert.equal(m.live.fromPlanJob, 641);
    assert.equal(m.drafts.length, 1);
    assert.equal(m.drafts[0].title, "iOS 1.1.1 · Android 1.0.1");
    assert.equal(m.drafts[0].pill, "editing");
    assert.equal(m.drafts[0].tone, "new");
    assert.equal(m.history.length, 1);
    assert.equal(m.history[0].title, "iOS 1.1.0 · Android 1.0.0");
    assert.equal(m.history[0].reviewJobId, 612);
  });
  test("행 문구 — 만든 지 · 바뀐 칸 · 빌드 유무", () => {
    const m = S.versionListModel(versionsDoc(), "en", NOW);
    assert.deepEqual(m.drafts[0].notes, ["created 40m ago", "no edits yet", "no build yet"]);
    const edited = S.versionListModel(versionsDoc({ drafts: [draft({ changed: 3, upload_job_id: 650 })] }), "en", NOW);
    assert.deepEqual(edited.drafts[0].notes, ["created 40m ago", "3 fields changed", "build uploaded"]);
  });
  test("만드는 중이면 작업 번호가 행에 있고 «새 버전 만들기» 가 잠긴다", () => {
    const m = S.versionListModel(versionsDoc({ drafts: [draft({ state: "creating" })] }), "en", NOW);
    assert.equal(m.creating, true);
    assert.equal(m.drafts[0].tone, "running");
    assert.equal(m.drafts[0].notes[0], "creating · job #641");
    // 만드는 중에는 버리기가 서버에서 409 다 — 버튼도 닫혀 있어야 한다
    assert.equal(m.drafts[0].canDiscard, false);
    assert.equal(S.versionListModel(versionsDoc(), "en", NOW).creating, false);
  });
  test("진행 중(회차 · 심사 · 올리기)이면 버리기가 닫힌다 (§15)", () => {
    const m = S.versionListModel(versionsDoc({ drafts: [draft({ state: "running", release_id: 12 })] }), "en", NOW);
    assert.equal(m.drafts[0].canDiscard, false);
    assert.equal(m.drafts[0].pill, "running");
  });
  test("TTL 이 지났는데 편집이 있으면 경고만 — 사람이 정리한다 (E13 · §14-5)", () => {
    const m = S.versionListModel(versionsDoc({
      drafts: [draft({ changed: 2, expired: true, expiry_warned: 1, last_edit_at: new Date(NOW - 100 * 1000).toISOString() })],
    }), "en", NOW);
    assert.equal(m.drafts[0].expired, true);
    assert.ok(m.drafts[0].notes.includes("expired — a person has to clear it"), m.drafts[0].notes.join(" · "));
    assert.equal(m.drafts[0].canDiscard, true, "버리기는 사람에게 남는다");
  });
  test("실패한 드래프트는 사유를 보이고 같은 이름으로 다시 만들 수 있다 (§15)", () => {
    const m = S.versionListModel(versionsDoc({ drafts: [draft({ state: "failed", error: "already exists" })] }), "en", NOW);
    assert.equal(m.drafts[0].tone, "bad");
    assert.equal(m.drafts[0].canRetry, true);
    assert.equal(m.drafts[0].canDiscard, true);
    assert.ok(m.drafts[0].notes.some((t) => t.indexOf("already exists") >= 0));
  });
  test("드래프트 40개도 한 모델이다 (E24)", () => {
    const rows = [];
    for (let i = 0; i < 40; i++) rows.push(draft({ id: i + 1, ios_version: "1.1." + (i + 1), android_version: null }));
    const history = [];
    for (let i = 0; i < 20; i++) history.push({ id: 100 + i, ios: "1.0." + i, android: null, submitted_at: null, review_job_id: null });
    const m = S.versionListModel(versionsDoc({ drafts: rows, history: history }), "en", NOW);
    assert.equal(m.drafts.length, 40);
    assert.equal(m.history.length, 20);
    assert.equal(m.drafts[0].title, "iOS 1.1.1");
  });
  test("칸을 둘 스토어 — 플랜이 아는 것만, 둘 다 모르면 둘 다 (E1 · E2)", () => {
    assert.deepEqual(S.versionListModel(versionsDoc(), "en", NOW).platforms, ["ios", "android"]);
    const iosOnly = versionsDoc({ live: { ios: "1.1.0", android: null, from_plan_job: 641 }, hints: { ios: "1.1.1", android: null } });
    assert.deepEqual(S.versionListModel(iosOnly, "en", NOW).platforms, ["ios"]);
    const noPlan = versionsDoc({ live: { ios: null, android: null, from_plan_job: null }, hints: { ios: null, android: null } });
    const m = S.versionListModel(noPlan, "en", NOW);
    assert.deepEqual(m.platforms, ["ios", "android"]);
    assert.equal(m.live.known, false);
  });
  test("문서가 없어도 안 던진다", () => {
    const m = S.versionListModel(null, "en", NOW);
    assert.deepEqual(m.drafts, []);
    assert.deepEqual(m.history, []);
    assert.equal(m.creating, false);
    assert.equal(m.live.known, false);
    assert.equal(m.ttlHours, null);
  });
  test("한국어 문면", () => {
    const m = S.versionListModel(versionsDoc({ drafts: [draft({ changed: 3 })] }), "ko", NOW);
    assert.equal(m.drafts[0].pill, "편집 중");
    assert.deepEqual(m.drafts[0].notes, ["만든 지 40m", "필드 3개 바뀜", "빌드 아직 없음"]);
  });
});

describe("statusStripModel — 요약 띠 (§12)", () => {
  const setup = { required: 4, present: 4, verified: 4, complete: true, missing: [] };
  const items = [{ name: "AuthKey.p8", kind: "file", present: true, verified_at: new Date(NOW - 600 * 1000).toISOString() }];
  function ctx(patch) {
    return Object.assign({
      doc: {
        setup: setup,
        branches: { main: "9e1c4d2", dev: "7a03b9f", main_in_dev: true },
        mirror: { fetched_at: new Date(NOW - 120 * 1000).toISOString(), age_seconds: 120 },
      },
      items: items,
      release: { plan: { job_id: 641, state: "succeeded", age_seconds: 240, stale: false, build_name: "1.1.1", doc: planDoc() } },
      profile: { plan_max_age_minutes: 30 },
      fetchError: null, nowMs: NOW, tzName: TZ,
    }, patch || {});
  }
  test("전부 초록이면 띠가 초록이고 칩은 셋이다 — 막힘도 경고도 없으면 그 칩은 없다", () => {
    const m = S.statusStripModel(ctx(), "ko");
    assert.equal(m.tone, "ok");
    assert.equal(m.bad, 0);
    assert.deepEqual(m.chips.map((c) => c.code), ["credentials", "source", "store"]);
    assert.equal(m.chips[0].text, "비밀 4/4 있음 · 검증 09:42");
    assert.equal(m.chips[2].text, "App Store 1.1.0 (180) · Play 1.0.0 (180) · 다음 빌드 181");
  });
  test("비밀이 덜 찼으면 자격 증명 칩이 빨갛고 띠도 빨갛다", () => {
    const m = S.statusStripModel(ctx({ doc: Object.assign({}, ctx().doc, { setup: { required: 4, present: 3, verified: 3, complete: false } }) }), "en");
    assert.equal(m.chips[0].tone, "bad");
    assert.equal(m.tone, "bad");
    assert.equal(m.bad, 1);
  });
  test("검증한 적이 없으면 자격 증명 칩이 빨갛다", () => {
    const m = S.statusStripModel(ctx({ items: [{ name: "AuthKey.p8", present: true, verified_at: null }] }), "en");
    assert.equal(m.chips[0].tone, "bad");
    assert.match(m.chips[0].text, /verified —/);
  });
  test("main 이 dev 에 없거나 미러가 30분을 넘으면 소스 칩이 빨갛다", () => {
    const notIn = ctx();
    notIn.doc = Object.assign({}, notIn.doc, { branches: { main: "9e1c4d2", dev: "7a03b9f", main_in_dev: false } });
    assert.equal(S.statusStripModel(notIn, "en").chips[1].tone, "bad");
    const old = ctx();
    old.doc = Object.assign({}, old.doc, { mirror: { fetched_at: new Date(NOW - 3600 * 1000).toISOString(), age_seconds: 3600 } });
    assert.equal(S.statusStripModel(old, "en").chips[1].tone, "bad");
    const failed = ctx({ fetchError: "boom" });
    assert.equal(S.statusStripModel(failed, "en").chips[1].tone, "bad");
  });
  test("플랜이 없으면 스토어 칩이 빨갛고 숫자를 지어내지 않는다", () => {
    const m = S.statusStripModel(ctx({ release: {} }), "ko");
    assert.equal(m.chips[2].tone, "bad");
    assert.equal(m.chips[2].text, "플랜 없음 — 스토어 상태를 모릅니다");
    assert.equal(m.tone, "bad");
  });
  test("플랜이 낡으면 스토어 칩이 빨갛다 — 서버 판정도 프로파일의 나이도 본다", () => {
    const byServer = ctx();
    byServer.release = { plan: { job_id: 641, state: "succeeded", age_seconds: 60, stale: true, doc: planDoc() } };
    assert.equal(S.statusStripModel(byServer, "en").chips[2].tone, "bad");
    const byAge = ctx();
    byAge.release = { plan: { job_id: 641, state: "succeeded", age_seconds: 4000, stale: false, doc: planDoc() } };
    const m = S.statusStripModel(byAge, "en");
    assert.equal(m.chips[2].tone, "bad");
    assert.match(m.chips[2].text, /stale/);
  });
  test("막힘과 경고는 플랜이 적어 보낸 수 그대로다", () => {
    const withBlockers = ctx();
    withBlockers.release = { plan: { job_id: 641, state: "succeeded", age_seconds: 60, stale: false,
      doc: planDoc({ blockers: [{ code: "B1" }], warnings: [{ code: "W1" }, { code: "W2" }] }) } };
    const m = S.statusStripModel(withBlockers, "ko");
    assert.equal(m.chips.length, 4);
    assert.equal(m.chips[3].code, "blockers");
    assert.equal(m.chips[3].text, "막힘 1 · 경고 2");
    assert.equal(m.chips[3].tone, "bad");
    assert.equal(m.tone, "bad");
  });
  test("경고만 있으면 칩은 있고 빨갛지는 않다", () => {
    const warnOnly = ctx();
    warnOnly.release = { plan: { job_id: 641, state: "succeeded", age_seconds: 60, stale: false,
      doc: planDoc({ warnings: [{ code: "W-TABLET" }] }) } };
    const m = S.statusStripModel(warnOnly, "en");
    assert.equal(m.chips[3].tone, "ok");
    assert.equal(m.tone, "ok");
  });
  test("아무 문서도 없을 때 안 던진다 — 모르는 것은 초록이 아니다", () => {
    const m = S.statusStripModel(null, "en");
    assert.equal(m.tone, "bad");
    assert.equal(m.chips.length, 3);
  });
});
