"use strict";
// 갱신 상태기계(workplan §3). 순수: connection(prev, event, nowMs) → 새 상태.
// 상태 = {mode: live|polling|lost|paused, attempt, lastOkAt, sseOpen}.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW } = require("./helpers");

const rcm = load();

const LIVE = Object.freeze({ mode: "live", attempt: 0, lastOkAt: NOW, sseOpen: true });
const at = (sec) => NOW + sec * 1000;
const step = (state, event, sec = 0) => rcm.connection(state, event, at(sec));

describe("nextBackoff", () => {
  test("2, 4, 8, 16, then capped at 30", () => {
    assert.deepEqual([0, 1, 2, 3, 4, 5, 6].map(rcm.nextBackoff), [2, 4, 8, 16, 30, 30, 30]);
  });
});

describe("connection — sse_error and reconnect backoff", () => {
  test("live → sse_error → polling, attempt 1, sseOpen false, lastOkAt kept", () => {
    const s = step(LIVE, "sse_error", 1);
    assert.equal(s.mode, "polling");
    assert.equal(s.attempt, 1);
    assert.equal(s.sseOpen, false);
    assert.equal(s.lastOkAt, NOW);
    assert.equal(rcm.nextBackoff(LIVE.attempt), 2);
  });

  test("repeated sse_error → attempts 1..6 with backoffs 2, 4, 8, 16, 30, 30", () => {
    let s = LIVE;
    const backoffs = [];
    for (let i = 1; i <= 6; i++) {
      backoffs.push(rcm.nextBackoff(s.attempt));
      s = step(s, "sse_error", i);
      assert.equal(s.attempt, i);
      assert.equal(s.mode, "polling");
    }
    assert.deepEqual(backoffs, [2, 4, 8, 16, 30, 30]);
  });

  test("sse_open → live, attempt reset, sseOpen true", () => {
    const polling = { mode: "polling", attempt: 3, lastOkAt: NOW, sseOpen: false };
    const s = step(polling, "sse_open", 5);
    assert.equal(s.mode, "live");
    assert.equal(s.attempt, 0);
    assert.equal(s.sseOpen, true);
    assert.equal(s.lastOkAt, NOW);
  });
});

describe("connection — lost and recovery", () => {
  test("tick at exactly 30s since lastOkAt → not lost yet", () => {
    const polling = step(LIVE, "sse_error", 1);
    const s = step(polling, "tick", 30);
    assert.equal(s.mode, "polling");
  });

  test("tick at 31s since lastOkAt → lost, lastOkAt unchanged", () => {
    const polling = step(LIVE, "sse_error", 1);
    const s = step(polling, "tick", 31);
    assert.equal(s.mode, "lost");
    assert.equal(s.lastOkAt, NOW);
  });

  test("live without a successful response for 31s → lost too", () => {
    assert.equal(step(LIVE, "tick", 31).mode, "lost");
  });

  test("status_ok from lost with SSE closed → polling, lastOkAt = now", () => {
    const lost = step(step(LIVE, "sse_error", 1), "tick", 31);
    const s = step(lost, "status_ok", 40);
    assert.equal(s.mode, "polling");
    assert.equal(s.lastOkAt, at(40));
  });

  test("status_ok from lost with SSE open → live", () => {
    const lost = step(LIVE, "tick", 31);
    const s = step(lost, "status_ok", 40);
    assert.equal(s.mode, "live");
    assert.equal(s.lastOkAt, at(40));
  });

  test("status_ok while polling → stays polling, lastOkAt refreshed", () => {
    const polling = step(LIVE, "sse_error", 1);
    const s = step(polling, "status_ok", 12);
    assert.equal(s.mode, "polling");
    assert.equal(s.attempt, 1);
    assert.equal(s.lastOkAt, at(12));
  });

  test("full cycle: sse_error → polling → 30s → lost → status_ok → sse_open → live", () => {
    let s = step(LIVE, "sse_error", 1);
    assert.equal(s.mode, "polling");
    s = step(s, "tick", 31);
    assert.equal(s.mode, "lost");
    s = step(s, "status_ok", 35);
    assert.equal(s.mode, "polling");
    s = step(s, "sse_open", 36);
    assert.equal(s.mode, "live");
    assert.equal(s.attempt, 0);
  });
});

describe("connection — pause and resume", () => {
  test("manual_pause from live → paused; manual_resume → live", () => {
    const paused = step(LIVE, "manual_pause", 1);
    assert.equal(paused.mode, "paused");
    assert.equal(step(paused, "manual_resume", 2).mode, "live");
  });

  test("manual_pause from polling → paused; manual_resume → polling (SSE still closed)", () => {
    const polling = step(LIVE, "sse_error", 1);
    const paused = step(polling, "manual_pause", 2);
    assert.equal(paused.mode, "paused");
    assert.equal(paused.sseOpen, false);
    assert.equal(step(paused, "manual_resume", 3).mode, "polling");
  });

  test("tick while paused never turns into lost", () => {
    const paused = step(LIVE, "manual_pause", 1);
    assert.equal(step(paused, "tick", 100).mode, "paused");
  });

  test("status_ok while paused → lastOkAt updated, still paused", () => {
    const paused = step(LIVE, "manual_pause", 1);
    const s = step(paused, "status_ok", 5);
    assert.equal(s.mode, "paused");
    assert.equal(s.lastOkAt, at(5));
  });

  test("hidden_60s → paused; visible → back to live or polling by sseOpen", () => {
    const hiddenLive = step(LIVE, "hidden_60s", 60);
    assert.equal(hiddenLive.mode, "paused");
    assert.equal(step(hiddenLive, "visible", 90).mode, "live");

    const polling = step(LIVE, "sse_error", 1);
    const hiddenPolling = step(polling, "hidden_60s", 61);
    assert.equal(hiddenPolling.mode, "paused");
    assert.equal(step(hiddenPolling, "visible", 91).mode, "polling");
  });
});

describe("connection — purity and edge cases", () => {
  test("unknown event → the four contract fields are unchanged", () => {
    const s = step(LIVE, "frobnicate", 1);
    const pick = ({ mode, attempt, lastOkAt, sseOpen }) => ({ mode, attempt, lastOkAt, sseOpen });
    assert.deepEqual(pick(s), pick(LIVE));
  });

  test("returns a new object and does not mutate prev", () => {
    const prev = { mode: "live", attempt: 0, lastOkAt: NOW, sseOpen: true };
    const next = rcm.connection(prev, "sse_error", at(1));
    assert.notEqual(next, prev);
    assert.deepEqual(prev, { mode: "live", attempt: 0, lastOkAt: NOW, sseOpen: true });
  });

  test("prev null + status_ok → initial polling state with lastOkAt = now", () => {
    const s = rcm.connection(null, "status_ok", NOW);
    assert.equal(s.mode, "polling");
    assert.equal(s.attempt, 0);
    assert.equal(s.sseOpen, false);
    assert.equal(s.lastOkAt, NOW);
  });
});
