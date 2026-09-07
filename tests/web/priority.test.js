"use strict";
// M5a 웹 — 큐 행의 `high`/`low` 칩(명세 M5a-1 「표시」) · 푸터/Estimates 의 `cache N blobs · X MB`
// (M5a-2 「저장 · 정리」) · 「우선순위는 이유가 아니다」(Not moving · reasonText · sortQueue 불변).
//
// 대상 함수(가정 — app.js 에 아직 없다. 구현자가 추가해야 할 순수 함수):
//   rcm.priorityChip(row)  → HTML 문자열. row.priority 가 1 이면 <span class="chip prio high">high</span>,
//                            -1 이면 …prio low…>low<, 0 · 없음 · null · 숫자 아님이면 "" (빈 문자열).
//                            queueRowHtml 의 chips 자리(inputs 칩 · group 칩 옆)에서 부른다.
//   rcm.cacheText(server)  → "cache 12 blobs · 48 MB" 같은 문자열. server.snapshot_cache 가 없거나 null 이면
//                            null. 숫자를 모르면 — (0 으로 그리지 않는다 — fail-open 금지).
//                            renderHeader 의 footBase 나 renderEstimates 의 summary 에 붙인다.
// 기존 순수 함수 중 행을 HTML 로 그리는 건 sourceHtml 뿐이고 queueRowHtml 은 DOM 쪽(state 의존)이라
// 여기서 부를 수 없다 — 그래서 칩 하나를 만드는 가장 작은 함수를 계약으로 잡는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, NOW } = require("./helpers");

const rcm = load();
const DASH = "—";

// 픽스처의 행을 복제해 바꾼 변형 행.
function rowLike(status, id, patch) {
  return Object.assign(structuredClone(job(status, id)), patch);
}

// class="…" 속성 안에 `prio <word>` 가 있는가.
function hasClass(html, word) {
  return new RegExp('class="[^"]*\\bprio ' + word + '\\b[^"]*"').test(html);
}

describe("module contract", () => {
  test("priorityChip is exported on rcm", () => {
    assert.equal(typeof rcm.priorityChip, "function", "rcm.priorityChip must be a function");
  });

  test("cacheText is exported on rcm", () => {
    assert.equal(typeof rcm.cacheText, "function", "rcm.cacheText must be a function");
  });
});

describe("priorityChip", () => {
  test("priority 1 → chip with class 'prio high' and text 'high'", () => {
    const h = rcm.priorityChip({ id: 1, state: "queued", priority: 1 });
    assert.ok(hasClass(h, "high"), h);
    assert.ok(/>\s*high\s*</.test(h), h);
    assert.ok(!/low/.test(h), h);
  });

  test("priority -1 → chip with class 'prio low' and text 'low'", () => {
    const h = rcm.priorityChip({ id: 1, state: "queued", priority: -1 });
    assert.ok(hasClass(h, "low"), h);
    assert.ok(/>\s*low\s*</.test(h), h);
    assert.ok(!/high/.test(h), h);
  });

  test("priority 0 → nothing (normal is the default and gets no chip)", () => {
    assert.equal(rcm.priorityChip({ id: 1, state: "queued", priority: 0 }), "");
  });

  test("priority missing / null / undefined (old server) → nothing, no throw", () => {
    assert.equal(rcm.priorityChip({ id: 1, state: "queued" }), "");
    assert.equal(rcm.priorityChip({ id: 1, state: "queued", priority: null }), "");
    assert.equal(rcm.priorityChip({ id: 1, state: "queued", priority: undefined }), "");
  });

  test("non-numeric priority → nothing and never echoed into the HTML", () => {
    const h = rcm.priorityChip({ id: 1, priority: "<b>x</b>" });
    assert.equal(h, "");
  });

  test("row itself null → nothing, no throw", () => {
    assert.equal(rcm.priorityChip(null), "");
    assert.equal(rcm.priorityChip(undefined), "");
  });

  test("main fixture rows carry no priority yet → no chip anywhere", () => {
    const s = fixture("main");
    s.pools[0].queue.forEach((row) => assert.equal(rcm.priorityChip(row), ""));
  });
});

describe("cacheText", () => {
  test("{blobs: 12, bytes: 48213344} → 'cache 12 blobs · 48 MB'", () => {
    const t = rcm.cacheText({ version: "0.2.0", snapshot_cache: { blobs: 12, bytes: 48213344 } });
    assert.equal(typeof t, "string");
    assert.ok(/cache 12 blobs · 48 MB/.test(t), t);
  });

  test("zero blobs is a real zero — 'cache 0 blobs'", () => {
    const t = rcm.cacheText({ snapshot_cache: { blobs: 0, bytes: 0 } });
    assert.ok(/cache 0 blobs/.test(t), t);
  });

  test("snapshot_cache absent or null (old server / cache off) → null", () => {
    assert.equal(rcm.cacheText({ version: "0.1.0" }), null);
    assert.equal(rcm.cacheText({ snapshot_cache: null }), null);
    assert.equal(rcm.cacheText(null), null);
    assert.equal(rcm.cacheText(undefined), null);
  });

  test("unknown numbers → dashes, never 0", () => {
    const t = rcm.cacheText({ snapshot_cache: { blobs: null, bytes: null } });
    assert.ok(typeof t === "string" && t.indexOf("cache") === 0, t);
    assert.ok(t.indexOf(DASH) !== -1, t);
    assert.ok(!/\b0 blobs\b/.test(t) && !/\b0 (MB|KB|B)\b/.test(t), t);
  });

  test("a 4 GiB cache reads as GB or as whole MB — never raw bytes", () => {
    const t = rcm.cacheText({ snapshot_cache: { blobs: 900, bytes: 4294967296 } });
    assert.ok(/4\.3 GB|4295 MB/.test(t), t);
    assert.ok(!/4294967296/.test(t), t);
  });
});

describe("priority is not a reason", () => {
  test("notMoving lists the same jobs with or without priority on the rows", () => {
    const base = rcm.notMoving(fixture("main"), "alice-laptop");
    const s = fixture("main");
    s.pools[0].queue.forEach((row, i) => { row.priority = [1, 0, -1, 1, 0][i]; });
    const r = rcm.notMoving(s, "alice-laptop");
    assert.equal(r.kind, base.kind);
    assert.deepEqual(r.lines.map((l) => [l.jobId, l.reason]), base.lines.map((l) => [l.jobId, l.reason]));
  });

  test("a low-priority job waiting for a lane behind high ones is not 'not moving'", () => {
    const s = fixture("main");
    Object.assign(job(s, 413), { reason: "waiting_for_lane", ahead_job_id: 409, blocked_by: null, priority: -1 });
    Object.assign(job(s, 414), { priority: 1 });
    assert.equal(rcm.notMoving(s, null).kind, "ok");
  });

  test("a high-priority job that is blocked by a group is still listed with the same text", () => {
    const s = fixture("main");
    const before = rcm.notMoving(fixture("main"), null).lines[0].text;
    job(s, 413).priority = 1;
    const r = rcm.notMoving(s, null);
    assert.equal(r.lines.length, 1);
    assert.equal(r.lines[0].text, before);
    assert.ok(!/high|priority/.test(r.lines[0].text), r.lines[0].text);
  });

  test("reasonText ignores priority", () => {
    const s = fixture("main");
    const plain = rcm.reasonText(job(s, 414), NOW, s).text;
    const high = rcm.reasonText(rowLike(s, 414, { priority: 1 }), NOW, s).text;
    const low = rcm.reasonText(rowLike(s, 414, { priority: -1 }), NOW, s).text;
    assert.equal(high, plain);
    assert.equal(low, plain);
  });

  test("sortQueue follows the server's position, not the priority value", () => {
    // 서버가 position 을 (-priority, id) 로 매긴다 — UI 는 그 순번을 믿고 다시 정렬하지 않는다
    const s = fixture("main");
    const a = rowLike(s, 414, { id: 420, position: 1, priority: 0 });
    const b = rowLike(s, 414, { id: 421, position: 2, priority: 1 });
    const c = rowLike(s, 414, { id: 422, position: 3, priority: -1 });
    assert.deepEqual(rcm.sortQueue([c, b, a]).map((r) => r.id), [420, 421, 422]);
  });

  test("yourJobs text is unchanged by priority", () => {
    const base = rcm.yourJobs(fixture("main"), "alice-laptop");
    const s = fixture("main");
    job(s, 412).priority = 1;
    job(s, 414).priority = -1;
    const r = rcm.yourJobs(s, "alice-laptop");
    assert.deepEqual(r.lines.map((l) => l.text), base.lines.map((l) => l.text));
  });

  test("ACTIONABLE has no priority-flavoured reason", () => {
    assert.ok(!rcm.ACTIONABLE.some((x) => /prio|starv|high|low/.test(x)), rcm.ACTIONABLE.join(","));
  });
});
