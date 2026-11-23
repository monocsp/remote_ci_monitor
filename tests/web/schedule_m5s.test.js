"use strict";
// M5s G — 웹 큐의 예약 절 · 새 상태 넷 · 「사람이 할 일」 표식 · 알림 실패 배지
// (docs/m5s-workplan.md §4.1 「표시」 · §4.3 「어디에 보이나」 · §4.7 · 결정 238).
//
// 잠그는 것:
// - 예약은 큐 표와 따로 간다. 시각순이고, 읽지 못했으면(`scheduled` null 또는 `scheduled_error`)
//   빈 목록과 **다른 것**(`error`)이다 — fail-open 금지. 키가 없는 옛 서버는 절을 그리지 않는다.
// - 예약 행에는 ETA 가 없다. 승격되기 전의 잡에 끝나는 시각을 지어내지 않는다.
// - `needs_action` 은 「Needs a look」의 맨 앞이고, 이유 칸은 「사람이 할 일 · <code> · <note>」다.
//   note 는 잡이 찍은 문장이라 번역하지 않는다.
// - `not_scheduled` 의 화면 문구는 새 상태 `scheduled` 와 헷갈리지 않는다(결정 238).
// - 알림 실패 배지는 0 이면 그리지 않는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, fromNow, NOW, TZ } = require("./helpers");

const rcm = load();

function sched(id, patch) {
  return Object.assign({
    id: id, pool: "default", preset: "golive", key: "golive:1.2.0",
    inputs: { build_name: "1.2.0", platform: "ios" }, priority: "high", concurrency_group: null,
    requester: { name: "alice-laptop", label: "alice@laptop" }, joiners: [], link: null,
    state: "scheduled", source: { mode: "git_ref", repo: "app", ref: "main", sha: "1234567890abcdef" },
    created_at: fromNow(-600), not_before: fromNow(3600), start_deadline: fromNow(3600 + 900),
    started_at: null, finished_at: null, url: null, placement: "pool", display_pool: "default",
    target_worker: null, keep: false, shelf: null
  }, patch || {});
}

function withScheduled(rows, patch) {
  const s = fixture("main");
  Object.assign(s.pools[0], { scheduled: rows, scheduled_error: null, scheduled_error_code: null }, patch || {});
  return s;
}

describe("새 상태 넷 — scheduled · expired · retried · exhausted", () => {
  const NEW = ["scheduled", "expired", "retried", "exhausted"];

  test("모양이 저마다 있고 서로 다르며 기존 아홉과도 겹치지 않는다", () => {
    const old = ["running", "queued", "uploading", "cancelling", "succeeded", "failed", "timed_out", "cancelled", "lost"]
      .map((s) => rcm.stateGlyph(s));
    const glyphs = NEW.map((s) => rcm.stateGlyph(s));
    glyphs.forEach((g, i) => {
      assert.notEqual(g, "·", NEW[i]);
      assert.ok(old.indexOf(g) === -1, `${NEW[i]}: ${g} 는 이미 다른 상태의 모양이다`);
    });
    assert.equal(new Set(glyphs).size, glyphs.length, glyphs.join(" "));
  });

  test("두 언어의 낱말", () => {
    assert.deepEqual(NEW.map((s) => rcm.stateWord(s, "en")),
      ["scheduled", "expired", "retried", "out of retries"]);
    assert.deepEqual(NEW.map((s) => rcm.stateWord(s, "ko")),
      ["예약됨", "만료됨", "다시 시도함", "시도를 다 씀"]);
  });

  test("최근 줄 — expired 는 종료 코드 2, exhausted 는 1 이다 (결정 212)", () => {
    const expired = rcm.recentLine({ id: 1, state: "expired", finished_at: fromNow(-60) }, TZ, NOW, "en");
    assert.equal(expired.pill, "expired · exit 2");
    assert.equal(expired.cls, "expired");
    assert.equal(expired.glyph, rcm.stateGlyph("expired"));
    assert.equal(rcm.recentLine({ id: 1, state: "expired" }, TZ, NOW, "ko").pill, "만료됨 · 종료 코드 2");
    const ex = rcm.recentLine({ id: 2, state: "exhausted", exit_code: 75 }, TZ, NOW, "en");
    assert.equal(ex.pill, "out of retries · exit 1");
    assert.equal(rcm.recentLine({ id: 2, state: "exhausted" }, TZ, NOW, "ko").pill, "시도를 다 씀 · 종료 코드 1");
  });

  test("retried 행이 와도 깨지지 않는다 — D 의 규칙으로는 오지 않지만 화면이 버틴다", () => {
    const r = rcm.recentLine({ id: 3, state: "retried", exit_code: 75, retried_as: 4 }, TZ, NOW, "en");
    assert.equal(r.pill, "retried");
    assert.equal(r.cls, "retried");
    assert.ok(!/undefined|NaN/.test(JSON.stringify(r)), JSON.stringify(r));
    assert.equal(rcm.recentLine({ id: 3, state: "retried" }, TZ, NOW, "ko").pill, "다시 시도함");
  });
});

describe("예약 절 — scheduledOf", () => {
  test("모든 풀의 예약을 시각순으로, 같은 시각이면 번호순으로", () => {
    const s = withScheduled([
      sched(31, { not_before: fromNow(7200) }),
      sched(30, { not_before: fromNow(600) }),
      sched(29, { not_before: fromNow(7200) }),
    ]);
    s.pools.push({ name: "linux", lanes: 1, queue: [], recent: [], scheduled: [sched(40, { pool: "linux", not_before: fromNow(60) })],
      scheduled_error: null, scheduled_error_code: null });
    const r = rcm.scheduledOf(s);
    assert.equal(r.kind, "list");
    assert.deepEqual(r.rows.map((x) => x.id), [40, 30, 29, 31]);
    assert.equal(r.pools[40], "linux");
    assert.equal(r.pools[30], undefined, "기본 풀은 풀 이름을 달지 않는다");
  });

  test("읽지 못했으면 error — 빈 목록과 다르다 (fail-open 금지)", () => {
    const failed = rcm.scheduledOf(withScheduled(null, { scheduled_error: "database is locked", scheduled_error_code: "db_locked" }));
    assert.equal(failed.kind, "error");
    assert.equal(failed.error, "database is locked");
    assert.equal(failed.code, "db_locked");
    assert.deepEqual(failed.rows, []);
    assert.equal(rcm.scheduledOf(withScheduled([])).kind, "empty");
    // 목록이 있어도 오류가 함께 오면 믿지 않는다
    assert.equal(rcm.scheduledOf(withScheduled([sched(1)], { scheduled_error: "partial" })).kind, "error");
    // 다른 풀 하나만 실패해도 절 전체가 「모른다」다
    const s = withScheduled([sched(1)]);
    s.pools.push({ name: "linux", queue: [], recent: [], scheduled: null, scheduled_error: "boom" });
    assert.equal(rcm.scheduledOf(s).kind, "error");
  });

  test("키가 없는 옛 서버는 absent — 절을 그리지 않는다", () => {
    assert.equal(rcm.scheduledOf(fixture("main")).kind, "absent");
    assert.equal(rcm.scheduledOf(null).kind, "absent");
  });
});

describe("예약 행 — scheduledRowModel", () => {
  test("프리셋 · 입력 · 요청자 · 시각(표시 시간대) · 시작 마감, ETA 는 없다", () => {
    const row = sched(30, { not_before: "2026-09-04T06:00:00Z", start_deadline: "2026-09-04T06:15:00Z" });
    const m = rcm.scheduledRowModel(row, TZ, NOW, "en", "bob-desk");
    assert.equal(m.id, 30);
    assert.equal(m.preset, "golive");
    assert.deepEqual(m.inputs, ["build_name=1.2.0", "platform=ios"]);
    assert.equal(m.requester, "alice@laptop");
    assert.equal(m.when, "at 15:00");  // 서울 15:00
    assert.equal(m.deadline, "start by 15:15");
    assert.equal(m.mine, false);
    assert.equal(m.reason, null);
    assert.equal(m.attempt, null);
    assert.ok(!("eta" in m), "예약 행에 ETA 를 지어내지 않는다");
    const ko = rcm.scheduledRowModel(row, TZ, NOW, "ko", "alice-laptop");
    assert.equal(ko.when, "15:00 시작");
    assert.equal(ko.deadline, "시작 마감 15:15");
    assert.equal(ko.mine, true);
  });

  test("모르는 시각은 — 로 (0 이나 빈칸이 아니다)", () => {
    const m = rcm.scheduledRowModel(sched(1, { not_before: null, start_deadline: null }), TZ, NOW, "en", null);
    assert.equal(m.when, "at —");
    assert.equal(m.deadline, "start by —");
  });

  test("반복 사슬의 다음 시도면 몇 번째인지 적는다", () => {
    const m = rcm.scheduledRowModel(sched(5, { attempt: 3, max_attempts: 12, chain_id: 2 }), TZ, NOW, "en", null);
    assert.equal(m.attempt, "attempt 3/12");
    assert.equal(rcm.scheduledRowModel(sched(5, { attempt: 3, max_attempts: 12 }), TZ, NOW, "ko", null).attempt,
      "12번 중 3번째 시도");
    assert.equal(rcm.scheduledRowModel(sched(5, { attempt: 1, max_attempts: 12 }), TZ, NOW, "en", null).attempt, null);
  });

  test("사슬의 표식이 있으면 이유 칸에 「사람이 할 일」을 그린다 (결정 233)", () => {
    const row = sched(5, { reason: "needs_action", action: { code: "login", note: "Play Console 로그인 필요", at: fromNow(-30) } });
    const m = rcm.scheduledRowModel(row, TZ, NOW, "ko", null);
    assert.equal(m.reason.text, "사람이 할 일 · login · Play Console 로그인 필요");
    assert.equal(m.reason.actionable, true);
  });
});

describe("「사람이 할 일」 — needs_action", () => {
  const ACT = { code: "login", note: "Sign in to Play Console on the build machine", at: "2026-09-04T00:50:00Z" };

  test("ACTIONABLE 의 맨 앞이다", () => {
    assert.equal(rcm.ACTIONABLE[0], "needs_action");
  });

  test("이유 칸: code 와 note 를 그대로 — note 는 번역하지 않는다", () => {
    const s = fixture("main");
    const row = Object.assign(structuredClone(job(s, 412)), { reason: "needs_action", action: ACT });
    const en = rcm.reasonText(row, s, NOW, "en");
    assert.equal(en.text, "needs action · login · Sign in to Play Console on the build machine");
    assert.equal(en.actionable, true);
    assert.equal(en.cls, "action");
    const ko = rcm.reasonText(row, s, NOW, "ko");
    assert.equal(ko.text, "사람이 할 일 · login · Sign in to Play Console on the build machine");
  });

  test("note 나 code 가 없으면 있는 것만 — undefined 를 그리지 않는다", () => {
    const s = fixture("main");
    const base = structuredClone(job(s, 412));
    const noNote = Object.assign(structuredClone(base), { reason: "needs_action", action: { code: "login", note: "", at: null } });
    assert.equal(rcm.reasonText(noNote, s, NOW, "en").text, "needs action · login");
    const noAction = Object.assign(structuredClone(base), { reason: "needs_action", action: null });
    assert.equal(rcm.reasonText(noAction, s, NOW, "en").text, "needs action");
    assert.equal(rcm.reasonText(noAction, s, NOW, "ko").text, "사람이 할 일");
  });

  test("Needs a look 의 맨 앞에 선다 — stuck · worker_down 보다 먼저", () => {
    const s = fixture("main");
    const stuck = Object.assign(structuredClone(job(s, 412)), { id: 501, reason: "stuck" });
    const down = Object.assign(structuredClone(job(s, 414)), { id: 502, reason: "worker_down" });
    const act = Object.assign(structuredClone(job(s, 412)), { id: 503, reason: "needs_action", action: ACT });
    s.pools[0].queue = [stuck, down, act];
    const r = rcm.notMoving(s, null);
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [503, 502, 501]);
    assert.ok(r.lines[0].text.startsWith("needs action · login"), r.lines[0].text);
  });

  test("시도 사이를 기다리는 예약 행의 표식도 Needs a look 에 남는다 (결정 233)", () => {
    const s = withScheduled([
      sched(600, { reason: "needs_action", action: ACT }),
      sched(601),  // 이유 없는 예약은 아무것도 아니다 — 「모른다」로 만들지 않는다
    ]);
    s.pools[0].queue = [];
    const r = rcm.notMoving(s, null);
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [600]);
    assert.equal(r.lines[0].reason, "needs_action");
  });

  test("예약을 읽지 못해도 큐의 판단은 그대로다 — 큐가 깨끗하면 ok", () => {
    const s = withScheduled(null, { scheduled_error: "boom" });
    s.pools[0].queue = [];
    assert.equal(rcm.notMoving(s, null).kind, "ok");
  });
});

describe("not_scheduled 화면 문구 (결정 238)", () => {
  test("빈 레인이 집지 않았다고 말한다 — 「예약」으로 읽히지 않게", () => {
    const s = fixture("main");
    const row = Object.assign(structuredClone(job(s, 414)), { reason: "not_scheduled" });
    assert.equal(rcm.reasonText(row, s, NOW, "en").text, "idle lane did not pick it up");
    assert.equal(rcm.reasonText(row, s, NOW, "ko").text, "빈 레인이 집지 않음");
    assert.ok(!/schedul|예약/.test(rcm.reasonText(row, s, NOW, "en").text + rcm.reasonText(row, s, NOW, "ko").text));
  });
});

describe("알림 실패 배지 — notifyFailuresBadge", () => {
  test("0 · 없음 · 숫자 아님이면 그리지 않는다", () => {
    [undefined, null, {}, { notify_failures: 0 }, { notify_failures: null }, { notify_failures: "3" }, { notify_failures: -1 }]
      .forEach((server) => assert.equal(rcm.notifyFailuresBadge(server, "en"), null, JSON.stringify(server)));
  });

  test("1 이상이면 수와 서버 로그를 가리키는 설명", () => {
    const en = rcm.notifyFailuresBadge({ notify_failures: 3 }, "en");
    assert.equal(en.text, "notify failures 3");
    assert.ok(en.title.includes("3") && en.title.includes("server log"), en.title);
    assert.ok(en.title.includes("restart"), "재시작하면 0 이 된다는 한계를 말한다: " + en.title);
    const ko = rcm.notifyFailuresBadge({ notify_failures: 3 }, "ko");
    assert.equal(ko.text, "알림 실패 3");
    assert.ok(ko.title.includes("서버 로그"), ko.title);
  });
});
