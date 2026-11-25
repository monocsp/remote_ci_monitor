// M5t-a PR3 — 분배 전용 코디네이터 `lanes = 0`(결정 243 · docs/m5t-workplan.md §4.2 · §7 TL11).
//
// 잠그는 것: 큐 머리줄과 대기 사유가 `lanes 0/0 busy` 대신 「로컬 레인 없음」을 말한다 · 서버
// 자신의 호스트 카드(`source: "local"`)에 「잡을 돌리지 않는 머신」 · 머신 탭의 `@local` 은
// 「꺼짐」이 아니라 잡을 돌리지 않는 머신이다 · 레인이 있는 서버는 오늘 그대로다(비교 행).

const test = require("node:test");
const assert = require("node:assert");
const { load } = require("./helpers.js");

const rcm = load();
const { queueHeader, reasonText, hostRunsNoJobs, hostFacts, machineRows } = rcm;

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
    // 무엇을 말하나도 본다 — 비어 있어도 「0/0 없음」은 초록이다
    assert.equal(out.text, lang === "en" ? "waiting for lane" : "대기 중", lang);
  }
  // 비교 행: 레인이 있는 서버는 오늘처럼 `0/2` 를 단다
  const two = status(2);
  assert.ok(/0\/2/.test(reasonText(two.pools[0].queue[0], two, NOW, "en").text));
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

test("TL11 the host card's facts line renders runs no jobs in both languages", () => {
  // 카드가 실제로 그리는 줄(`hostCardHtml` 의 facts) — 술어만이 아니라 문구까지
  assert.equal(typeof hostFacts, "function");
  const local = { name: "coord", source: "local", os: "macOS 26", cores: 8, load: [0.5] };
  const worker = { name: "build-02", source: "worker", os: "macOS 26", cores: 8, load: [0.5] };
  assert.equal(hostFacts(status(0), local, "en"), "macOS 26 · 8 cores · load 0.5 · runs no jobs");
  assert.equal(hostFacts(status(0), local, "ko"), "macOS 26 · 시스템 부하 0.5 · 코어 8개 · 작업을 돌리지 않는 머신");
  // 비교 행: 원격 워커의 카드 · 레인이 있는 서버의 카드에는 붙지 않는다
  assert.equal(hostFacts(status(0), worker, "en"), "macOS 26 · 8 cores · load 0.5");
  assert.equal(hostFacts(status(1), local, "ko"), "macOS 26 · 시스템 부하 0.5 · 코어 8개");
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

test("TL7 the machines tab draws no Recheck button on @local without lanes", () => {
  // 누르면 409 「this server has no local lanes」다 — 누를 수 없는 버튼은 그리지 않는다
  const checks = [{ preset: "simcheck", state: "passed", job_id: 3 }];
  const doc = {
    machines: [
      { id: "@local", display_name: "coord", kind: "local", pool: "default", lanes: 0, alive: false, presets: {}, checks: checks },
      { id: "build-02", display_name: "build-02", kind: "remote", pool: "default", lanes: 1, alive: true, presets: {}, checks: checks },
      { id: "@local", display_name: "mini", kind: "local", pool: "default", lanes: 2, alive: true, presets: {}, checks: checks },
    ],
  };
  const rows = machineRows(doc, true, "en");
  assert.equal(rows[0].recheck, false);
  assert.equal(rows[1].recheck, true); // 비교 행: 원격 워커는 그대로
  assert.equal(rows[2].recheck, true); // 비교 행: 레인이 있는 서버의 @local 도 그대로
});

test("TL15 a job with nowhere to go says no local lanes, not unknown", () => {
  // 분배 전용 서버에서 `@local` 을 지정한 잡 · `local_only` 잡 — 서버가 싣는 메모(code no_local_lanes)
  const s = status(0);
  const row = {
    id: 9, state: "queued", reason: "no_capable_machine", estimate: {},
    placement_detail: [{ worker: "@local", display_name: "coord", state: "ineligible", code: "no_local_lanes", detail: {} }],
  };
  const en = reasonText(row, s, NOW, "en").text;
  const ko = reasonText(row, s, NOW, "ko").text;
  assert.equal(en, "no capable machine · coord: no local lanes (lanes = 0)");
  assert.equal(ko, "실행 가능한 머신 없음 · coord: 로컬 레인 없음(lanes = 0)");
});
