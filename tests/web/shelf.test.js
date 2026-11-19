"use strict";
// 보관함(M5p P3) — 최근 행의 보관함 줄 · 지우기 버튼 · 머리 nav. docs/m5p-workplan.md §4.7 · §15.6,
// docs/m5p-test-scenarios-c.md 의 web 줄(CB14~16 · CE19 · CE20 · CG29~33).
// **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**
//
// 잠그는 것:
// - 링크는 경로만(`/shelf/412/`) — 행의 `shelf.url` 은 public_url 로 만든 절대 주소라 브라우저가 연
//   호스트와 다를 수 있다(CG30). SPA 가 영어면 `?lang=en` 을 붙인다(§15.6).
// - 지우기 버튼은 토큰이 있고 항목이 보관 중 · 정리 대기일 때(§15.6 — admin 이 아니면 서버의 403 을
//   그대로 알린다).
// - nav 는 릴리스 저장소가 없어도 보관함이 켜져 있거나 항목이 있으면 보인다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();

function job(shelf, over) {
  return Object.assign({ id: 412, state: "succeeded", keep: true, shelf: shelf }, over || {});
}
function sh(state, over) {
  return Object.assign({ state: state, url: "http://mac.local:8787/shelf/412/", reason_code: null }, over || {});
}

describe("shelfLine", () => {
  test("CB14 보관된 잡은 경로만 쓰는 링크 줄", () => {
    assert.deepEqual(rcm.shelfLine(job(sh("active")), "ko"), { text: "보관함 ↗", href: "/shelf/412/", cls: "active" });
    assert.deepEqual(rcm.shelfLine(job(sh("active")), "en"), { text: "shelf ↗", href: "/shelf/412/?lang=en", cls: "active" });
  });

  test("CG30 shelf.url 의 호스트를 그대로 쓰지 않는다", () => {
    const l = rcm.shelfLine(job(sh("active", { url: "http://other.example:9999/shelf/412/" })), "ko");
    assert.equal(l.href, "/shelf/412/");
  });

  test("CG29 정리 대기는 링크와 함께 말한다", () => {
    assert.deepEqual(rcm.shelfLine(job(sh("stale")), "ko"), { text: "보관함 ↗ · 정리 대기", href: "/shelf/412/", cls: "stale" });
    assert.equal(rcm.shelfLine(job(sh("stale")), "en").text, "shelf ↗ · cleanup pending");
  });

  test("CG29 보관하는 중 · 기다리는 중에는 링크가 없다", () => {
    ["pending", "copying", "waiting"].forEach((s) => {
      const l = rcm.shelfLine(job(sh(s)), "ko");
      assert.equal(l.href, null, s);
      assert.equal(l.text, "보관하는 중…", s);
      assert.equal(l.cls, "pending", s);
    });
    assert.equal(rcm.shelfLine(job(sh("copying")), "en").text, "copying to the shelf…");
  });

  test("CE20 보관이 실패하면 이유를 그 언어로, 링크 없이", () => {
    const f = job(sh("failed", { reason_code: "shelf_full" }));
    assert.deepEqual(rcm.shelfLine(f, "ko"), { text: "보관 못 함 · 보관함이 가득 참", href: null, cls: "failed" });
    assert.equal(rcm.shelfLine(f, "en").text, "not kept · the shelf is full");
  });

  test("모르는 이유 코드는 코드 그대로 — 빈칸이 되지 않는다", () => {
    const l = rcm.shelfLine(job(sh("failed", { reason_code: "brand_new" })), "ko");
    assert.equal(l.text, "보관 못 함 · brand_new");
  });

  test("CG29 지운 항목 · 보관할 것이 없던 잡", () => {
    assert.deepEqual(rcm.shelfLine(job(sh("deleted")), "ko"), { text: "보관함에서 지움", href: null, cls: "deleted" });
    const none = rcm.shelfLine(job(sh("none", { reason_code: "not_collected_on_success" })), "ko");
    assert.equal(none.href, null);
    assert.equal(none.cls, "none");
    assert.match(none.text, /^보관할 파일 없음 · /);
    assert.equal(rcm.shelfLine(job(sh("none")), "en").text, "nothing to keep");
  });

  test("CG29 keep 이 아니거나 옛 서버(키 없음)면 줄이 없다", () => {
    assert.equal(rcm.shelfLine(job(null, { keep: false }), "ko"), null);
    assert.equal(rcm.shelfLine({ id: 1, state: "succeeded" }, "ko"), null);
    assert.equal(rcm.shelfLine(job(null), "ko"), null);
    assert.equal(rcm.shelfLine(null, "ko"), null);
  });
});

describe("shelfDeletable (CB15 · CG31)", () => {
  const ctx = (o) => Object.assign({ token: "t", tokenBad: false, admin: true }, o || {});
  test("토큰이 있고 보관 중 · 정리 대기일 때만", () => {
    assert.equal(rcm.shelfDeletable(job(sh("active")), ctx()), true);
    assert.equal(rcm.shelfDeletable(job(sh("stale")), ctx()), true);
    assert.equal(rcm.shelfDeletable(job(sh("active")), ctx({ admin: false })), true, "§15.6: 토큰이면 보이고 403 은 서버가 말한다");
    assert.equal(rcm.shelfDeletable(job(sh("active")), ctx({ token: null })), false);
    assert.equal(rcm.shelfDeletable(job(sh("active")), ctx({ tokenBad: true })), false);
    ["pending", "copying", "waiting", "failed", "deleted", "none"].forEach((s) => {
      assert.equal(rcm.shelfDeletable(job(sh(s)), ctx()), false, s);
    });
    assert.equal(rcm.shelfDeletable(job(null), ctx()), false);
  });
});

describe("shelfDeleteOutcome (CB15 · CE19)", () => {
  test("200 은 지웠다 · 다시 읽는다", () => {
    const o = rcm.shelfDeleteOutcome(200, 412, "ko");
    assert.equal(o.reload, true);
    assert.equal(o.text, "#412를 보관함에서 지웠습니다");
  });
  test("403 은 거절 문구 · 그대로 둔다", () => {
    const o = rcm.shelfDeleteOutcome(403, 412, "en");
    assert.equal(o.reload, false);
    assert.equal(o.text, "only an admin token can remove a shelf item");
  });
  test("409 는 바쁘다", () => {
    const o = rcm.shelfDeleteOutcome(409, 412, "ko");
    assert.equal(o.reload, false);
    assert.match(o.text, /받는 중/);
  });
  test("410 · 404 는 오류 없이 다시 읽는다", () => {
    [404, 410].forEach((s) => {
      const o = rcm.shelfDeleteOutcome(s, 412, "ko");
      assert.equal(o.reload, true, String(s));
      assert.equal(o.text, null, String(s));
    });
  });
  test("그 밖은 상태 코드를 말한다", () => {
    const o = rcm.shelfDeleteOutcome(500, 412, "en");
    assert.equal(o.reload, false);
    assert.match(o.text, /500/);
  });
});

describe("navModel (CB16 · CG32)", () => {
  const on = { server: { shelf_storage: { enabled: true, items: { active: 0, stale: 0 } } } };
  const off = { server: { shelf_storage: { enabled: false, items: { active: 0, stale: 0 } } } };
  const offKept = { server: { shelf_storage: { enabled: false, items: { active: 2, stale: 1 } } } };

  test("CB16 저장소가 없어도 보관함이 켜져 있으면 nav 가 보인다", () => {
    const m = rcm.navModel({ status: on, repos: [], view: "queue", lang: "ko" });
    assert.equal(m.hidden, false);
    assert.deepEqual(m.links.map((l) => l.key), ["queue", "shelf"]);
    const shelf = m.links[1];
    assert.equal(shelf.href, "/shelf/");
    assert.equal(shelf.text, "보관함");
    assert.equal(shelf.external, true);
  });

  test("CG32 영어면 ?lang=en", () => {
    const m = rcm.navModel({ status: on, repos: [], view: "queue", lang: "en" });
    assert.equal(m.links[1].href, "/shelf/?lang=en");
    assert.equal(m.links[1].text, "Shelf");
  });

  test("CG32 꺼졌고 항목도 없고 저장소도 없으면 nav 가 숨는다", () => {
    assert.equal(rcm.navModel({ status: off, repos: [], view: "queue", lang: "ko" }).hidden, true);
    assert.equal(rcm.navModel({ status: {}, repos: [], view: "queue", lang: "ko" }).hidden, true);
    assert.equal(rcm.navModel({ status: null, repos: [], view: "queue", lang: "ko" }).hidden, true);
  });

  test("꺼졌어도 남은 항목이 있으면 링크가 있다(§15.6)", () => {
    const m = rcm.navModel({ status: offKept, repos: [], view: "queue", lang: "ko" });
    assert.deepEqual(m.links.map((l) => l.key), ["queue", "shelf"]);
  });

  test("저장소가 있으면 오늘의 Queue · Store 에 보관함이 붙는다", () => {
    const repos = [{ name: "app" }];
    const m = rcm.navModel({ status: on, repos: repos, view: "store", repo: "app", lang: "ko" });
    assert.deepEqual(m.links.map((l) => l.key), ["queue", "store", "shelf"]);
    assert.equal(m.links[1].href, "#/store/app");
    assert.equal(m.links[1].current, true);
    assert.equal(m.links[0].current, false);
    const plain = rcm.navModel({ status: off, repos: repos, view: "queue", lang: "ko" });
    assert.deepEqual(plain.links.map((l) => l.key), ["queue", "store"]);
    assert.equal(plain.links[0].current, true);
  });
});

describe("SSE (P2 의 shelf_changed)", () => {
  test("보관 상태가 바뀌면 다시 읽는다 — 보관함 줄이 바로 바뀐다", () => {
    assert.ok(rcm.SSE_REFETCH_KINDS.includes("shelf_changed"));
    ["job_changed", "job_finished", "reset", "lag"].forEach((k) => assert.ok(rcm.SSE_REFETCH_KINDS.includes(k), k));
  });
});
