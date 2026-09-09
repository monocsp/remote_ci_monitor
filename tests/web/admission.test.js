// M5f — 보류 레인(held)과 사유 held_by_load. 명세는 docs/m5f-workplan.md §5.4.
//
// 잠그는 것: 두 언어에 문구가 있다 · 「Not moving」이 held 를 **정상 대기**로 보고 조용하다
// (ACTIONABLE 에는 안 넣는다 — 의도된·자가 치유되는 상태라 worker_down·stuck 이 묻히면 안 된다) ·
// hold_code 가 없는 옛 서버 문서에서도 안 깨진다.

const test = require("node:test");
const assert = require("node:assert");
const { load } = require("./helpers.js");

const rcm = load();
const { reasonText, workerState, notMoving, ACTIONABLE } = rcm;

function row(extra) {
  return Object.assign({ id: 412, reason: "held_by_load", estimate: {} }, extra || {});
}
function status(workers) {
  return {
    generated_at: "2026-09-08T12:00:00Z",
    server: { lanes: 2, workers: workers || [] },
    pools: [{ queue: [row()] }],
  };
}
function held(lane, code, busy) {
  return {
    lane: lane, state: "held", job_id: null, worker: null,
    hold_code: code || "cpu_busy",
    hold_detail: busy === undefined ? { cpu_busy: 92.4 } : busy,
    held_since: "2026-09-08T12:00:00Z",
  };
}

test("held_by_load has words in both languages", () => {
  for (const lang of ["en", "ko"]) {
    const out = reasonText(row(), status([held(2)]), Date.parse("2026-09-08T12:00:00Z"), lang);
    assert.ok(out.text && out.text.length, lang);
    assert.ok(!/unknown|알 수 없음/.test(out.text), lang + ": " + out.text);
  }
});

test("held_by_load shows the cpu number from the workers, not from the row", () => {
  // hold_detail 은 server.workers[] 에만 있다 — 행 키를 늘리지 않는다
  const out = reasonText(row(), status([held(2)]), Date.parse("2026-09-08T12:00:00Z"), "en");
  assert.match(out.text, /92/);
});

test("held_by_load without a number says only why", () => {
  const s = status([held(2, "no_sample", null)]);
  const out = reasonText(row(), s, Date.parse("2026-09-08T12:00:00Z"), "en");
  assert.ok(out.text.length && !/92/.test(out.text), out.text);
});

test("workerState translates held in both languages", () => {
  for (const lang of ["en", "ko"]) {
    const word = workerState("held", lang);
    assert.ok(word && word !== "held" || lang === "en", lang + ": " + word);
  }
  assert.ok(workerState("held", "ko").length);
});

test("Not moving stays quiet for a held lane", () => {
  // 의도된·자가 치유되는 상태다. 늘 켜져 있으면 사람들이 패널을 무시하게 된다(결정 45).
  assert.strictEqual(ACTIONABLE.indexOf("held_by_load"), -1);
  const out = notMoving(status([held(2)]), null, "en");
  assert.strictEqual(out.kind, "ok", JSON.stringify(out));
});

test("an old status document without hold_code does not break the page", () => {
  const old = { lane: 2, state: "held", job_id: null, worker: null };
  const out = reasonText(row(), status([old]), Date.parse("2026-09-08T12:00:00Z"), "en");
  assert.ok(out.text.length);
  assert.strictEqual(notMoving(status([old]), null, "en").kind, "ok");
});

test("the held worker pill has a shape channel, not only a colour (WCAG 1.4.1)", () => {
  // idle 은 둥근 점, held 는 네모 — 회색조로 인쇄해도 갈려야 한다.
  const fs = require("node:fs");
  const path = require("node:path");
  const css = fs.readFileSync(
    path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "style.css"), "utf8");
  assert.match(css, /\.wk\.held\s*\{/);
  assert.match(css, /\.wk\.held i\s*\{[^}]*border-radius:\s*1px/);
});
