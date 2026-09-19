"use strict";
// 스토어 탭(docs/wireframes/web-store.html) — 순수 함수. 라우터 · 관문 띠 · 비밀 표 행 · 지문 규칙 ·
// 드롭존 수용 · 행 색 · 머리 문구 · 서버 호출 층(fetch 스텁). 값(비밀)은 어디에도 나오면 안 된다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW } = require("./helpers");

const rcm = load();
const S = rcm.store;
const DASH = "—";

function item(patch) {
  return Object.assign(
    { name: "GH_TOKEN", kind: "value", optional: false, verify: "github", present: true, size: null,
      fingerprint: "ghp_…", verified_at: null, verify_error: null },
    patch,
  );
}
function fileItem(patch) {
  return item(Object.assign({ name: "AuthKey.p8", kind: "file", verify: "asc", size: 2112, fingerprint: "9f1c2a3b4c5d", max_kb: 64 }, patch));
}
function dirItem(patch) {
  return item(Object.assign({ name: "review_information", kind: "dir", verify: "none", fingerprint: "1/2 files",
    files: [{ name: "demo_user.txt", present: true, size: 12, fingerprint: "aabbccdd" }, { name: "demo_password.txt", present: false }] }, patch));
}
function doc(patch) {
  return Object.assign({
    name: "app",
    profile: { default_branch: "main", tag: "prod/{version}-{build}", build_number_policy: "auto",
      presets: { plan: "release-plan", upload: "release-upload", review: "release-review", gate: null, qa: null, dev: null } },
    setup: { required: 6, present: 4, verified: 3, complete: false, missing: ["GH_TOKEN"] },
    mirror: { path: "/srv/mirrors/app", fetched_at: new Date(NOW - 180 * 1000).toISOString(), age_seconds: 180 },
    branches: { main: "9e1c4d2abcdef0123456789", dev: "7a03b9f0000000000000000", main_in_dev: true },
  }, patch);
}

describe("module contract", () => {
  test("rcm.store exposes the pure functions", () => {
    ["parseRoute", "releaseRepos", "setupSummary", "fingerprintText", "secretRowModel", "dropAccept",
      "rowState", "rowOpen", "setupHead", "sourceHead", "naRowText", "makeStoreApi", "mirrorAge"].forEach((k) => {
      assert.equal(typeof S[k], "function", k);
    });
  });
});

describe("parseRoute — #/ is the queue, #/store/<repo> the Store", () => {
  test("queue hashes", () => {
    ["", "#", "#/", "#/jobs/12", "#/jobs/12/log", "#/store", "#/store/", "#/nope/app"].forEach((h) => {
      assert.deepEqual(S.parseRoute(h), { view: "queue", repo: null, sub: null, id: null }, h);
    });
  });
  test("store hashes carry the repo, decoded", () => {
    assert.deepEqual(S.parseRoute("#/store/app"), { view: "store", repo: "app", sub: "versions", id: null });
    assert.deepEqual(S.parseRoute("#/store/app/"), { view: "store", repo: "app", sub: "versions", id: null });
    assert.deepEqual(S.parseRoute("#/store/my%20app"), { view: "store", repo: "my app", sub: "versions", id: null });
  });
  test("a repo with an unknown second segment is not a store route", () => {
    assert.equal(S.parseRoute("#/store/a/b").view, "queue");
  });
});

describe("releaseRepos — the tab exists only for repos with release: true", () => {
  test("filters and tolerates a bad document", () => {
    const list = S.releaseRepos({ repos: [{ name: "app", release: true }, { name: "lib", release: false }, { name: "", release: true }, null] });
    assert.deepEqual(list.map((r) => r.name), ["app"]);
    assert.deepEqual(S.releaseRepos(null), []);
    assert.deepEqual(S.releaseRepos({ error: "not found" }), []);
  });
});

describe("setupSummary — the gate banner (item 29)", () => {
  test("incomplete is red with n of m · k verified", () => {
    const s = S.setupSummary({ required: 6, present: 4, verified: 3, complete: false }, "en");
    assert.equal(s.complete, false);
    assert.equal(s.tone, "bad");
    assert.equal(s.text, "4 of 6 secrets set · 3 verified");
    const ko = S.setupSummary({ required: 6, present: 4, verified: 3, complete: false }, "ko");
    assert.equal(ko.text, "비밀 6개 중 4개 설정 · 3개 검증됨");
  });
  test("complete is green", () => {
    const s = S.setupSummary({ required: 6, present: 6, verified: 6, complete: true }, "en");
    assert.equal(s.complete, true);
    assert.equal(s.tone, "ok");
  });
  test("missing setup is not complete and the numbers are dashes, never 0", () => {
    const s = S.setupSummary(null, "en");
    assert.equal(s.complete, false);
    assert.equal(s.text, `${DASH} of ${DASH} secrets set · ${DASH} verified`);
    assert.equal(S.setupSummary({ complete: "true" }, "en").complete, false, "only boolean true completes");
  });
});

describe("fingerprintText — present · fingerprint only, never the value (items 30 · 31)", () => {
  test("missing secret → dash", () => {
    assert.equal(S.fingerprintText(item({ present: false, fingerprint: "ghp_…" }), "en"), DASH);
  });
  test("value: at most 4 chars + …, even when the server sends more", () => {
    assert.equal(S.fingerprintText(item({ fingerprint: "ghp_…" }), "en"), "ghp_…");
    assert.equal(S.fingerprintText(item({ fingerprint: "ghp_abcdefgh1234" }), "en"), "ghp_…");
    assert.equal(S.fingerprintText(item({ fingerprint: null }), "en"), DASH);
  });
  test("file: 8 hex + size", () => {
    assert.equal(S.fingerprintText(fileItem(), "en"), "9f1c2a3b · 2 KB");
    assert.equal(S.fingerprintText(fileItem({ size: null }), "en"), "9f1c2a3b");
  });
  test("dir: n of m files from the file list", () => {
    assert.equal(S.fingerprintText(dirItem(), "en"), "1 of 2 files");
    assert.equal(S.fingerprintText(dirItem({ files: [] }), "en"), DASH);
    // 개수는 값이 아니다 — 폴더가 아직 다 안 찼어도(present=false) 어디까지 찼는지는 보인다
    assert.equal(S.fingerprintText(dirItem({ present: false }), "en"), "1 of 2 files");
  });
});

describe("secretRowModel — one table row (item 31)", () => {
  test("a present value secret: replace, pill ✓, verified pending", () => {
    const m = S.secretRowModel(item(), "en", "Asia/Seoul", NOW);
    assert.equal(m.name, "GH_TOKEN");
    assert.equal(m.kindWord, "value");
    assert.equal(m.action, "replace");
    assert.equal(m.presentGlyph, "✓");
    assert.equal(m.presentWord, "present");
    assert.equal(m.fingerprint, "ghp_…");
    assert.deepEqual(m.verified, { tone: "pending", text: "not verified yet" });
    assert.equal(m.input, "value");
    assert.deepEqual(m.files, []);
  });
  test("a missing one: add, pill ✗, no verification", () => {
    const m = S.secretRowModel(item({ present: false, fingerprint: null }), "en", "Asia/Seoul", NOW);
    assert.equal(m.action, "add");
    assert.equal(m.presentGlyph, "✗");
    assert.equal(m.presentWord, "missing");
    assert.equal(m.fingerprint, DASH);
    assert.equal(m.verified.text, DASH);
  });
  test("verified_at → green clock, verify_error → red text", () => {
    const ok = S.secretRowModel(item({ verified_at: new Date(NOW - 60000).toISOString() }), "en", "Asia/Seoul", NOW);
    assert.equal(ok.verified.tone, "ok");
    assert.match(ok.verified.text, /^verified \d\d:\d\d$/);
    const bad = S.secretRowModel(item({ verified_at: new Date(NOW).toISOString(), verify_error: "not implemented in this build" }), "en", "Asia/Seoul", NOW);
    assert.equal(bad.verified.tone, "bad");
    assert.equal(bad.verified.text, "failed · not implemented in this build");
    const none = S.secretRowModel(fileItem({ verify: "none" }), "en", "Asia/Seoul", NOW);
    assert.equal(none.verified.text, "no check for this kind");
  });
  test("dir: sub rows per file with their own present flag and fingerprint", () => {
    const m = S.secretRowModel(dirItem(), "en", "Asia/Seoul", NOW);
    assert.equal(m.input, "dir");
    assert.equal(m.fingerprint, "1 of 2 files");
    assert.deepEqual(m.files.map((f) => [f.name, f.present, f.fingerprint]), [
      ["demo_user.txt", true, "aabbccdd · 12 B"],
      ["demo_password.txt", false, DASH],
    ]);
  });
  test("a string file list means present is unknown → not present (no fail-open)", () => {
    const m = S.secretRowModel(dirItem({ files: ["a.txt", "b.txt"] }), "en", "Asia/Seoul", NOW);
    assert.deepEqual(m.files.map((f) => f.present), [false, false]);
  });
  test("the model never carries a value field", () => {
    const m = S.secretRowModel(item({ value: "ghp_SECRET_SHOULD_NOT_LEAK" }), "en", "Asia/Seoul", NOW);
    assert.ok(!JSON.stringify(m).includes("SECRET_SHOULD_NOT_LEAK"));
  });
  test("korean words", () => {
    const m = S.secretRowModel(dirItem(), "ko", "Asia/Seoul", NOW);
    assert.equal(m.kindWord, "폴더");
    assert.equal(m.presentWord, "있음");
    assert.equal(m.fingerprint, "파일 2개 중 1개");
  });
});

describe("dropAccept — one non-empty file within max_kb, admin only (item 30)", () => {
  const f = (size, name) => ({ name: name || "x.p8", size });
  test("accepts one file", () => {
    assert.deepEqual(S.dropAccept(fileItem(), [f(100)], true), { ok: true, reason: null, args: {} });
  });
  test("rejects nothing · many · empty · too big · non-admin", () => {
    assert.equal(S.dropAccept(fileItem(), [], true).reason, "secrets.reject.none");
    assert.equal(S.dropAccept(fileItem(), null, true).reason, "secrets.reject.none");
    assert.equal(S.dropAccept(fileItem(), [f(1), f(2)], true).reason, "secrets.reject.many");
    assert.equal(S.dropAccept(fileItem(), [f(0)], true).reason, "secrets.reject.empty");
    const big = S.dropAccept(fileItem({ max_kb: 1 }), [f(1025)], true);
    assert.equal(big.reason, "secrets.reject.big");
    assert.equal(big.args.limit, "1 KB");
    assert.equal(S.dropAccept(fileItem(), [f(100)], false).reason, "secrets.reject.admin");
  });
  test("no max_kb → any size; unknown item → still checks the file", () => {
    assert.equal(S.dropAccept(fileItem({ max_kb: undefined }), [f(10 * 1024 * 1024)], true).ok, true);
    assert.equal(S.dropAccept(undefined, [f(5)], true).ok, true);
  });
  test("every reject reason is a catalogue key in both languages", () => {
    const I18N = require("../../src/remote_ci_monitor/web/i18n.js");
    ["none", "many", "empty", "big", "admin"].forEach((r) => assert.ok(I18N.has("secrets.reject." + r), r));
  });
});

describe("rowState — colour from data (section 7)", () => {
  test("setup: complete → ok, else bad", () => {
    assert.equal(S.rowState("setup", { setup: { complete: true } }), "ok");
    assert.equal(S.rowState("setup", { setup: { complete: false } }), "bad");
    assert.equal(S.rowState("setup", {}), "bad");
  });
  test("source: fetch error > main not in dev > never fetched > 30 min > unknown branch > ok", () => {
    const d = doc();
    assert.equal(S.rowState("source", { doc: d, nowMs: NOW }), "ok");
    assert.equal(S.rowState("source", { doc: d, nowMs: NOW, fetchError: "boom" }), "bad");
    assert.equal(S.rowState("source", { doc: doc({ branches: { main: "a", dev: "b", main_in_dev: false } }), nowMs: NOW }), "bad");
    assert.equal(S.rowState("source", { doc: doc({ mirror: { path: "p", fetched_at: null, age_seconds: null } }), nowMs: NOW }), "stale");
    const old = doc({ mirror: { path: "p", fetched_at: new Date(NOW - 31 * 60 * 1000).toISOString(), age_seconds: 31 * 60 } });
    assert.equal(S.rowState("source", { doc: old, nowMs: NOW }), "stale");
    assert.equal(S.rowState("source", { doc: doc({ branches: { main: "a", dev: null, main_in_dev: null } }), nowMs: NOW }), "na");
    // 실패는 낡음보다 먼저다 — 오래된 미러라도 fetch 가 깨졌으면 빨강
    assert.equal(S.rowState("source", { doc: old, nowMs: NOW, fetchError: "x" }), "bad");
  });
  test("build and store rows are n/a in this build", () => {
    assert.equal(S.rowState("build", { doc: doc() }), "na");
    assert.equal(S.rowState("store", { doc: doc() }), "na");
  });
  test("exactly 30 minutes is not stale yet", () => {
    const edge = doc({ mirror: { fetched_at: new Date(NOW - S.STORE_STALE_SECONDS * 1000).toISOString() } });
    assert.equal(S.rowState("source", { doc: edge, nowMs: NOW }), "ok");
  });
});

describe("rowOpen — colour decides, a remembered choice wins", () => {
  test("defaults", () => {
    assert.equal(S.rowOpen("ok"), false);
    assert.equal(S.rowOpen("na"), false);
    assert.equal(S.rowOpen("bad"), true);
    assert.equal(S.rowOpen("running"), true);
    assert.equal(S.rowOpen("stale"), true);
  });
  test("memory", () => {
    assert.equal(S.rowOpen("ok", "open"), true);
    assert.equal(S.rowOpen("bad", "closed"), false);
    assert.equal(S.rowOpen("bad", "garbage"), true);
  });
});

describe("mirrorAge", () => {
  test("fetched_at counts from now, else age_seconds, else null", () => {
    assert.equal(S.mirrorAge({ fetched_at: new Date(NOW - 5000).toISOString(), age_seconds: 999 }, NOW), 5);
    assert.equal(S.mirrorAge({ fetched_at: null, age_seconds: 42 }, NOW), 42);
    assert.equal(S.mirrorAge({ fetched_at: "not a date" }, NOW), null);
    assert.equal(S.mirrorAge(null, NOW), null);
  });
});

describe("row heads", () => {
  test("setupHead: n/m · last verified clock · build number policy", () => {
    const items = [item({ verified_at: new Date(NOW - 3600 * 1000).toISOString() }), fileItem({ verified_at: new Date(NOW - 60 * 1000).toISOString() })];
    const h = S.setupHead(doc().setup, items, doc().profile, "en", "Asia/Seoul", NOW);
    assert.match(h, /^4\/6 secrets present · verified 09:51 · build number auto$/, h);
    const none = S.setupHead(null, [], null, "en", "Asia/Seoul", NOW);
    assert.equal(none, `${DASH}/${DASH} secrets present · verified ${DASH} · build number ${DASH}`);
  });
  test("setupHead: the newest verified_at of any kind counts — value · file · dir · a dir's files · epoch numbers", () => {
    const t = (sec) => new Date(NOW - sec * 1000).toISOString();
    const items = [
      Object.assign(item({ verified_at: t(3600) }), { kind: "value" }),
      fileItem({ verified_at: null }),
      { name: "review_information", kind: "dir", present: true, verified_at: t(600), files: [{ name: "a", present: true, verified_at: t(60) }] },
    ];
    assert.match(S.setupHead(doc().setup, items, doc().profile, "en", "Asia/Seoul", NOW), /verified 09:51 ·/);
    assert.equal(S.latestVerified(items), NOW - 60 * 1000);
    assert.equal(S.latestVerified([{ verified_at: Math.floor(NOW / 1000) }]), Math.floor(NOW / 1000) * 1000, "epoch seconds");
    assert.equal(S.latestVerified([{ verified_at: NOW }]), NOW, "epoch milliseconds");
    assert.equal(S.latestVerified([{ verified_at: "soon" }, null, {}]), null);
    assert.equal(S.latestVerified([{ verify_detail: { verified_at: t(5) } }]), NOW - 5000);
  });
  test("setupHead: «n not checked» only when a count is present", () => {
    const items = [item({ verify_detail: { not_checked: 2 } }), fileItem({ verify_detail: "ok" })];
    assert.match(S.setupHead(doc().setup, items, doc().profile, "en", "Asia/Seoul", NOW), / · 2 not checked$/);
    assert.match(S.setupHead(Object.assign({}, doc().setup, { not_checked: 1 }), items, doc().profile, "ko", "Asia/Seoul", NOW), /검사 안 함 3$/);
    assert.equal(S.notCheckedCount(doc().setup, [item()]), 0);
    assert.doesNotMatch(S.setupHead(doc().setup, [item()], doc().profile, "en", "Asia/Seoul", NOW), /not checked/);
  });
  test("sourceHead: sha7 · main in dev · fetched age", () => {
    assert.deepEqual(S.sourceHead(doc(), { nowMs: NOW }, "en"), ["main 9e1c4d2", "main in dev", "fetched 3m ago"]);
    const bad = S.sourceHead(doc({ branches: { main: null, dev: "x", main_in_dev: false } }), { nowMs: NOW, fetchError: "a".repeat(100) }, "en");
    assert.equal(bad[0], `main ${DASH}`);
    assert.match(bad[1], /not in origin\/dev/);
    assert.equal(bad[3], "fetch failed: " + "a".repeat(60), "error text is cut at 60 chars (item 42)");
    const stale = S.sourceHead(doc({ mirror: { fetched_at: null } }), { nowMs: NOW }, "en");
    assert.equal(stale[2], "not fetched yet");
    const old = S.sourceHead(doc({ mirror: { fetched_at: new Date(NOW - 47 * 60 * 1000).toISOString() } }), { nowMs: NOW }, "en");
    assert.equal(old[2], "stale · fetched 47m ago");
  });
  test("naRowText: not configured when the role preset is empty, else not in this build", () => {
    assert.equal(S.naRowText("build", doc().profile, "en"), "No release running · not available in this build");
    assert.equal(S.naRowText("store", doc().profile, "en"), "Store status is not available in this build");
    assert.equal(S.naRowText("build", { presets: { upload: null } }, "en"), "not configured — presets.upload is empty");
    assert.equal(S.naRowText("store", { presets: {} }, "en"), "not configured — presets.plan is empty");
    assert.equal(S.naRowText("store", null, "en"), "not configured — presets.plan is empty");
  });
});

describe("makeStoreApi — one place for every call, fetch stubbed", () => {
  function stub(status, body, log) {
    return (path, opts) => {
      log.push({ path, method: opts.method, headers: opts.headers, body: opts.body });
      return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(body == null ? "" : JSON.stringify(body)) });
    };
  }
  test("paths, methods, auth header and content types", async () => {
    const log = [];
    const api = S.makeStoreApi(stub(200, { repos: [] }, log), () => "tok-1");
    await api.repos();
    await api.repo("my app");
    await api.secrets("app");
    await api.putSecret("app", "GH_TOKEN", "ghp_x", "text/plain; charset=utf-8");
    await api.putSecret("app", "AuthKey.p8", new ArrayBuffer(3));
    await api.putSecret("app", "review_information", new ArrayBuffer(3), "application/octet-stream", "demo_password.txt");
    await api.verify("app", ["GH_TOKEN"]);
    await api.verify("app", []);
    await api.fetchRemote("app");
    assert.deepEqual(log.map((c) => [c.method, c.path]), [
      ["GET", "/api/repos"],
      ["GET", "/api/repos/my%20app"],
      ["GET", "/api/repos/app/secrets"],
      ["PUT", "/api/repos/app/secrets/GH_TOKEN"],
      ["PUT", "/api/repos/app/secrets/AuthKey.p8"],
      ["PUT", "/api/repos/app/secrets/review_information/demo_password.txt"],
      ["POST", "/api/repos/app/verify"],
      ["POST", "/api/repos/app/verify"],
      ["POST", "/api/repos/app/fetch"],
    ]);
    log.forEach((c) => assert.equal(c.headers.Authorization, "Bearer tok-1", c.path));
    assert.equal(log[3].headers["Content-Type"], "text/plain; charset=utf-8");
    assert.equal(log[3].body, "ghp_x");
    assert.equal(log[4].headers["Content-Type"], "application/octet-stream");
    assert.equal(log[6].body, JSON.stringify({ names: ["GH_TOKEN"] }));
    assert.equal(log[7].body, "{}");
    assert.equal(log[8].headers["Content-Type"], "application/json");
  });
  test("no token → no Authorization header", async () => {
    const log = [];
    await S.makeStoreApi(stub(200, {}, log), () => null).repos();
    assert.equal(log[0].headers.Authorization, undefined);
  });
  test("404 does not throw — ok false with the body", async () => {
    const res = await S.makeStoreApi(stub(404, { error: "not found", code: "not_found" }, []), () => null).repos();
    assert.deepEqual(res, { ok: false, status: 404, body: { error: "not found", code: "not_found" } });
    assert.deepEqual(S.releaseRepos(res.body), []);
  });
  test("non-JSON body becomes {error: text}", async () => {
    const fetchFn = () => Promise.resolve({ ok: false, status: 502, text: () => Promise.resolve("Bad Gateway") });
    const res = await S.makeStoreApi(fetchFn, () => null).fetchRemote("app");
    assert.deepEqual(res, { ok: false, status: 502, body: { error: "Bad Gateway" } });
  });
});

describe("glyphs — colour never stands alone", () => {
  test("every row state has a glyph and a word in both languages", () => {
    const I18N = require("../../src/remote_ci_monitor/web/i18n.js");
    ["ok", "bad", "running", "stale", "na"].forEach((s) => {
      assert.ok(S.ROW_GLYPH[s], s);
      assert.notEqual(I18N.t("ko", "row.state." + s), I18N.t("en", "row.state." + s), s);
    });
  });
});
