"use strict";
// 잡 산출물 한 줄(M5e §13) — 모르는 수는 —, `0` 은 「모았는데 없었다」일 때만.
// 파일 이름은 공개 문서에 없으므로 화면에도 없다(§10) — 대신 받아 가는 명령을 준다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW } = require("./helpers");

const rcm = load();
const DASH = "—";
const NOW_MS = NOW; // helpers 의 NOW 는 epoch ms 다

function job(artifacts, id) {
  return { id: id || 412, state: "succeeded", artifacts: artifacts };
}

function ready(over) {
  return Object.assign(
    {
      state: "ready",
      file_count: 64,
      total_bytes: 12812345,
      bundle_bytes: 12820480,
      skipped_count: 0,
      ready_at: new Date(NOW_MS).toISOString().replace(/\.\d+Z$/, "Z"),
      expires_at: new Date(NOW_MS + 23 * 3600 * 1000).toISOString().replace(/\.\d+Z$/, "Z"),
      purged_at: null,
      reason_code: null,
      reason_args: null
    },
    over || {}
  );
}

describe("artifactsLine", () => {
  test("산출물이 없는 잡에는 줄이 없다", () => {
    assert.equal(rcm.artifactsLine({ id: 1 }, "ko", NOW_MS), null);
    assert.equal(rcm.artifactsLine(job(null), "ko", NOW_MS), null);
  });

  test("disabled·pending 은 그리지 않는다 — 아직 할 말이 없다", () => {
    assert.equal(rcm.artifactsLine(job({ state: "disabled" }), "ko", NOW_MS), null);
    assert.equal(rcm.artifactsLine(job({ state: "pending" }), "ko", NOW_MS), null);
  });

  test("ready 는 개수·크기·남은 시간과 받아 가는 명령을 준다", () => {
    const line = rcm.artifactsLine(job(ready()), "en", NOW_MS);
    assert.ok(line.text.includes("64 files"), line.text);
    assert.ok(line.text.includes("13 MB"), line.text);
    assert.ok(/left/.test(line.text), line.text);
    assert.equal(line.cls, "ready");
    assert.equal(line.command, "rcm artifacts 412 --fetch --output .");
  });

  test("모르는 수는 — 로 그린다. 0 은 empty 일 때만이다(§10)", () => {
    const unknown = rcm.artifactsLine(job({ state: "unknown" }), "en", NOW_MS);
    assert.ok(unknown.text.includes("unknown"), unknown.text);
    assert.ok(!/\b0\b/.test(unknown.text), unknown.text);
    assert.equal(unknown.command, null);

    const empty = rcm.artifactsLine(
      job({ state: "empty", file_count: 0, bundle_bytes: 0 }),
      "en",
      NOW_MS
    );
    assert.ok(empty.text.includes("0 files"), empty.text);
    assert.notEqual(empty.text, unknown.text);
  });

  test("수가 null 이면 — 다 (ready 인데 서버가 못 센 경우)", () => {
    const line = rcm.artifactsLine(
      job(ready({ file_count: null, bundle_bytes: null })),
      "en",
      NOW_MS
    );
    assert.ok(line.text.includes(DASH), line.text);
  });

  test("버린 이유는 코드가 아니라 문장으로 나온다(결정 37)", () => {
    const line = rcm.artifactsLine(
      job({ state: "dropped", reason_code: "over_bytes" }),
      "en",
      NOW_MS
    );
    assert.ok(line.text.includes("over the size limit"), line.text);
    assert.ok(!line.text.includes("over_bytes"), line.text);
    assert.equal(line.cls, "dropped");
  });

  test("두 언어 다 문장이 있고 서로 다르다", () => {
    const ko = rcm.artifactsLine(job(ready()), "ko", NOW_MS);
    const en = rcm.artifactsLine(job(ready()), "en", NOW_MS);
    assert.ok(ko.text.includes("산출물"), ko.text);
    assert.ok(en.text.includes("artifacts"), en.text);
    assert.notEqual(ko.text, en.text);
    assert.equal(ko.command, en.command); // 명령은 번역하지 않는다
  });

  test("파일 이름은 어디에도 없다 — 공개 문서에 경로가 없기 때문이다(§10)", () => {
    const line = rcm.artifactsLine(job(ready()), "ko", NOW_MS);
    assert.ok(!line.text.includes("/"), line.text);
  });
});
