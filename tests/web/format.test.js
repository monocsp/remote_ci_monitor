"use strict";
// 표기 규칙(목업 「4. 이 화면이 정한 규칙」 · workplan §2).
// fmtDuration · fmtClock 은 core/render_text.py 의 fmt_duration · fmt_clock 과 같은 결과여야 한다
// (같은 상태를 터미널과 웹이 다르게 그리면 안 된다).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW, TZ } = require("./helpers");

const rcm = load();
const DASH = "—";

describe("module contract", () => {
  test("require(app.js) returns rcm with every §2 pure function", () => {
    const names = [
      "fmtDuration", "fmtClock", "fmtAgo", "fmtCountdown", "fmtBytes", "fmtMb", "ordinal", "truncate",
      "stateWord", "stateGlyph", "esc",
      "reasonText", "confidenceBadge", "etaText", "elapsedText",
      "notMoving", "yourJobs", "hostPressure", "queueHeader", "sortQueue",
      "progressHead", "stepMark", "recentLine", "rerunCommand", "transitionsLine",
      "connection", "nextBackoff", "workerPills", "headerNote",
    ];
    for (const name of names) {
      assert.equal(typeof rcm[name], "function", `rcm.${name} must be a function`);
    }
  });
});

describe("fmtDuration", () => {
  test("null and undefined → —", () => {
    assert.equal(rcm.fmtDuration(null), DASH);
    assert.equal(rcm.fmtDuration(undefined), DASH);
  });

  test("0 → 0s (a known zero is not unknown)", () => {
    assert.equal(rcm.fmtDuration(0), "0s");
  });

  test("under a minute → Ns", () => {
    assert.equal(rcm.fmtDuration(12), "12s");
    assert.equal(rcm.fmtDuration(59), "59s");
  });

  test("minutes → Mm SSs with zero-padded seconds", () => {
    assert.equal(rcm.fmtDuration(310), "5m 10s");
    assert.equal(rcm.fmtDuration(60), "1m 00s");
    assert.equal(rcm.fmtDuration(62), "1m 02s");
    assert.equal(rcm.fmtDuration(3599), "59m 59s");
  });

  test("hours → Hh MMm with zero-padded minutes and no seconds", () => {
    assert.equal(rcm.fmtDuration(3720), "1h 02m");
    assert.equal(rcm.fmtDuration(3600), "1h 00m");
    assert.equal(rcm.fmtDuration(7380), "2h 03m");
    assert.equal(rcm.fmtDuration(86400), "24h 00m");
  });

  test("rounds to the nearest second before formatting", () => {
    // 59.6 은 반올림하면 60 → 분 단위로 넘어간다 (render_text: int(round(seconds)))
    assert.equal(rcm.fmtDuration(59.6), "1m 00s");
    assert.equal(rcm.fmtDuration(59.4), "59s");
    assert.equal(rcm.fmtDuration(0.4), "0s");
  });

  test("negative clamps to 0s", () => {
    assert.equal(rcm.fmtDuration(-5), "0s");
  });
});

describe("fmtClock", () => {
  test("today in the display tz → HH:MM, seconds truncated", () => {
    assert.equal(rcm.fmtClock("2026-09-04T00:57:22Z", TZ, NOW), "09:57");
    // 00:54:52Z 는 09:54:52 — 반올림해서 09:55 로 그리지 않는다 (render_text %H:%M)
    assert.equal(rcm.fmtClock("2026-09-04T00:54:52Z", TZ, NOW), "09:54");
  });

  test("another day → 'Mon D · HH:MM'", () => {
    assert.equal(rcm.fmtClock("2026-09-03T14:40:00Z", TZ, NOW), "Sep 3 · 23:40");
  });

  test("'today' is decided in the display tz, not in UTC", () => {
    // UTC 로는 9/3 이지만 서울로는 9/4 00:30 → 오늘
    assert.equal(rcm.fmtClock("2026-09-03T15:30:00Z", TZ, NOW), "00:30");
  });

  test("UTC display tz", () => {
    assert.equal(rcm.fmtClock("2026-09-04T00:57:22Z", "UTC", NOW), "00:57");
  });

  test("nowMs omitted → always HH:MM (no day check)", () => {
    assert.equal(rcm.fmtClock("2026-09-03T14:40:00Z", TZ), "23:40");
  });

  test("null, undefined, empty and unparsable → —", () => {
    assert.equal(rcm.fmtClock(null, TZ, NOW), DASH);
    assert.equal(rcm.fmtClock(undefined, TZ, NOW), DASH);
    assert.equal(rcm.fmtClock("", TZ, NOW), DASH);
    assert.equal(rcm.fmtClock("not a date", TZ, NOW), DASH);
  });
});

describe("fmtAgo", () => {
  test("under a minute → Ns ago", () => {
    assert.equal(rcm.fmtAgo(4), "4s ago");
    assert.equal(rcm.fmtAgo(0), "0s ago");
    assert.equal(rcm.fmtAgo(59), "59s ago");
  });

  test("a minute and over → Nm ago, coarse (floor)", () => {
    assert.equal(rcm.fmtAgo(60), "1m ago");
    assert.equal(rcm.fmtAgo(185), "3m ago");
    // 179s 는 아직 2m — 나이는 내림이라 실제보다 크게 말하지 않는다
    assert.equal(rcm.fmtAgo(179), "2m ago");
  });

  test("an hour and over → Nh ago", () => {
    assert.equal(rcm.fmtAgo(3600), "1h ago");
    assert.equal(rcm.fmtAgo(7199), "1h ago");
  });

  test("null → —", () => {
    assert.equal(rcm.fmtAgo(null), DASH);
    assert.equal(rcm.fmtAgo(undefined), DASH);
  });
});

describe("fmtCountdown", () => {
  test("'in ' + duration", () => {
    assert.equal(rcm.fmtCountdown(8), "in 8s");
    assert.equal(rcm.fmtCountdown(160), "in 2m 40s");
  });

  test("zero and negative → 'now' (the moment has arrived)", () => {
    assert.equal(rcm.fmtCountdown(0), "now");
    assert.equal(rcm.fmtCountdown(-3), "now");
  });

  test("null → —", () => {
    assert.equal(rcm.fmtCountdown(null), DASH);
  });
});

describe("fmtBytes", () => {
  test("§2 examples: 48213344 → 48 MB, 594411520 → 0.6 GB", () => {
    assert.equal(rcm.fmtBytes(48213344), "48 MB");
    assert.equal(rcm.fmtBytes(594411520), "0.6 GB");
  });

  test("decimal units (1e6 / 1e9), GB with one decimal", () => {
    assert.equal(rcm.fmtBytes(25769803776), "25.8 GB");
    assert.equal(rcm.fmtBytes(12000000), "12 MB");
  });

  test("MB/GB boundary is 500 MB", () => {
    assert.equal(rcm.fmtBytes(499000000), "499 MB");
    assert.equal(rcm.fmtBytes(500000000), "0.5 GB");
  });

  test("small sizes: B and KB", () => {
    assert.equal(rcm.fmtBytes(1000), "1 KB");
    assert.equal(rcm.fmtBytes(999), "999 B");
    assert.equal(rcm.fmtBytes(0), "0 B");
  });

  test("null → —", () => {
    assert.equal(rcm.fmtBytes(null), DASH);
    assert.equal(rcm.fmtBytes(undefined), DASH);
  });
});

describe("fmtMb", () => {
  test("megabytes in (top[].rss_mb) → integer MB with a space before the unit", () => {
    assert.equal(rcm.fmtMb(500), "500 MB");
    assert.equal(rcm.fmtMb(169.6), "170 MB");
    assert.equal(rcm.fmtMb(0), "0 MB");
  });

  test("null → —", () => {
    assert.equal(rcm.fmtMb(null), DASH);
  });
});

describe("ordinal", () => {
  test("1st 2nd 3rd 4th", () => {
    assert.equal(rcm.ordinal(1), "1st");
    assert.equal(rcm.ordinal(2), "2nd");
    assert.equal(rcm.ordinal(3), "3rd");
    assert.equal(rcm.ordinal(4), "4th");
  });

  test("teens are all th", () => {
    assert.equal(rcm.ordinal(11), "11th");
    assert.equal(rcm.ordinal(12), "12th");
    assert.equal(rcm.ordinal(13), "13th");
    assert.equal(rcm.ordinal(111), "111th");
    assert.equal(rcm.ordinal(112), "112th");
    assert.equal(rcm.ordinal(113), "113th");
  });

  test("21st 22nd 23rd 101st", () => {
    assert.equal(rcm.ordinal(21), "21st");
    assert.equal(rcm.ordinal(22), "22nd");
    assert.equal(rcm.ordinal(23), "23rd");
    assert.equal(rcm.ordinal(101), "101st");
  });

  test("null → —", () => {
    assert.equal(rcm.ordinal(null), DASH);
  });
});

describe("truncate", () => {
  test("40 chars stay unchanged", () => {
    const label = "a".repeat(40);
    assert.equal(rcm.truncate(label, 40), label);
  });

  test("41 chars → 40 chars total, ending with …", () => {
    const out = rcm.truncate("a".repeat(41), 40);
    assert.equal(out.length, 40);
    assert.equal(out, "a".repeat(39) + "…");
  });

  test("default limit is 40", () => {
    assert.equal(rcm.truncate("b".repeat(40)), "b".repeat(40));
    assert.equal(rcm.truncate("b".repeat(41)), "b".repeat(39) + "…");
  });

  test("custom limit", () => {
    assert.equal(rcm.truncate("alice@laptop", 8), "alice@l…");
    assert.equal(rcm.truncate("alice", 8), "alice");
  });

  test("null → —", () => {
    assert.equal(rcm.truncate(null, 40), DASH);
  });
});

describe("stateWord", () => {
  test("timed_out → 'timed out', every other state unchanged", () => {
    assert.equal(rcm.stateWord("timed_out"), "timed out");
    for (const s of ["running", "queued", "uploading", "cancelling", "succeeded", "failed", "cancelled", "lost"]) {
      assert.equal(rcm.stateWord(s), s);
    }
  });

  test("null → 'unknown' (a state we were not told)", () => {
    assert.equal(rcm.stateWord(null), "unknown");
  });
});

describe("stateGlyph", () => {
  test("one glyph per state (§2)", () => {
    assert.equal(rcm.stateGlyph("running"), "▶");
    assert.equal(rcm.stateGlyph("queued"), "·");
    assert.equal(rcm.stateGlyph("uploading"), "↑");
    assert.equal(rcm.stateGlyph("cancelling"), "■");
    assert.equal(rcm.stateGlyph("succeeded"), "✓");
    assert.equal(rcm.stateGlyph("failed"), "✗");
    assert.equal(rcm.stateGlyph("timed_out"), "⏱");
    assert.equal(rcm.stateGlyph("cancelled"), "■");
    assert.equal(rcm.stateGlyph("lost"), "?");
  });

  test("null → · (same default as render_text's queue rows; the pill word carries the meaning)", () => {
    assert.equal(rcm.stateGlyph(null), "·");
  });
});

describe("esc", () => {
  test("escapes < > &", () => {
    assert.equal(rcm.esc("<b>"), "&lt;b&gt;");
    assert.equal(rcm.esc("a & b"), "a &amp; b");
  });

  test("escapes double and single quotes", () => {
    assert.equal(rcm.esc('"'), "&quot;");
    assert.match(rcm.esc("'"), /^&#(39|x27);$/);
  });

  test("no raw special character survives", () => {
    const out = rcm.esc(`a<b>&"c'd`);
    assert.doesNotMatch(out, /[<>"']/);
    assert.doesNotMatch(out, /&(?![a-z]+;|#\d+;|#x[0-9a-f]+;)/i);
  });

  test("does not unescape existing entities (ampersand first)", () => {
    assert.equal(rcm.esc("&amp;"), "&amp;amp;");
  });

  test("null/undefined → empty string, numbers stringified", () => {
    assert.equal(rcm.esc(null), "");
    assert.equal(rcm.esc(undefined), "");
    assert.equal(rcm.esc(412), "412");
  });
});
