// M5t-a PR3 — 분배 전용 코디네이터 `lanes = 0`(결정 243 · docs/m5t-workplan.md §4.2 · §7 TL11).
//
// 잠그는 것: 큐 머리줄과 대기 사유가 `lanes 0/0 busy` 대신 「로컬 레인 없음」을 말한다 · 서버
// 자신의 호스트 카드(`source: "local"`)에 「잡을 돌리지 않는 머신」 · 머신 탭의 `@local` 은
// 「꺼짐」이 아니라 잡을 돌리지 않는 머신이다 · 레인이 있는 서버는 오늘 그대로다(비교 행).

const test = require("node:test");
const assert = require("node:assert");
const { load } = require("./helpers.js");

const rcm = load();
const { queueHeader, reasonText, hostRunsNoJobs, machineRows } = rcm;

const NOW = Date.parse("2026-11-26T12:00:00Z");

function status(lanes, workers) {
  return {
    generated_at: "2026-11-26T12:00:00Z",
    server: { lanes: lanes, workers: workers || [] },
    pools: [{ name: "default", queue: [{ id: 7, state: "queued", reason: "waiting_for_lane", estimate: {}, created_at: "2026-11-26T11:59:00Z" }] }],
  };
}
const REMOTE = { worker: "build-02", lane: 1, state: "busy", job_id: 6, pool: "default" };

test("TL11 the queue header says no local lanes instead of lanes 0/0 busy", () => {
  for (const lang of ["en", "ko"]) {
    const t = queueHeader(status(0, [REMOTE]), NOW, lang);
    assert.ok(!/0\/0/.test(t), lang + ": " + t);
    assert.ok(t.includes(lang === "en" ? "no local lanes" : "로컬 레인 없음"), lang + ": " + t);
  }
  // 비교 행: 레인이 있으면 오늘 그대로
  assert.ok(queueHeader(status(2), NOW, "en").includes("lanes 0/2 busy"));
});

test("TL11 a waiting row does not count 0/0 local lanes", () => {
  const s = status(0, [REMOTE]);
  for (const lang of ["en", "ko"]) {
    const out = reasonText(s.pools[0].queue[0], s, NOW, lang);
    assert.ok(!/0\/0/.test(out.text), lang + ": " + out.text);
  }
});

test("TL11 the server's own host card says it runs no jobs", () => {
  assert.equal(typeof hostRunsNoJobs, "function");
  const local = { name: "coord", source: "local" };
  const worker = { name: "build-02", source: "worker" };
  assert.equal(hostRunsNoJobs(status(0), local), true);
  assert.equal(hostRunsNoJobs(status(0), worker), false);
  assert.equal(hostRunsNoJobs(status(1), local), false);
  assert.equal(hostRunsNoJobs(null, local), false);
});

test("TL11 the machines tab shows @local without lanes as running no jobs, not down", () => {
  const doc = {
    machines: [
      { id: "@local", display_name: "coord", kind: "local", pool: "default", lanes: 0, alive: false, presets: {} },
      { id: "build-02", display_name: "build-02", kind: "remote", pool: "default", lanes: 1, alive: false, presets: {} },
    ],
  };
  for (const lang of ["en", "ko"]) {
    const rows = machineRows(doc, false, lang);
    assert.equal(rows[0].noJobs, true, lang);
    assert.equal(rows[0].alive, null, lang); // 꺼짐으로 그리지 않는다
    assert.equal(rows[1].noJobs, false, lang);
    assert.equal(rows[1].alive, false, lang); // 비교 행: 원격 워커의 down 은 그대로
  }
});
