"use strict";
// 웹 호스트 카드의 「rcm 데이터」 한 줄 (M5g §5.4).
//
// 잠그는 것:
// - `jobStorageLine` 이 **쥐고 있는 양과 천장**을 함께 준다. 막대만으로는 「얼마나 남았나」를 못
//   읽는다는 디스크 미터의 요구가 여기에도 그대로 적용된다.
// - 경고는 셋 중 하나면 켠다: 예산 초과 · 바닥 아래 · 무진전 latch.
// - **모르는 값은 0 이 아니다.** 못 잰 회계를 0 GB 로 그리면 「지키고 있다」는 거짓말이 된다.
// - 이 키가 없는 **옛 상태 문서**에서 안 깨진다(그리지 않는다).
// 구현보다 먼저 썼다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { load } = require("./helpers");
const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));

const rcm = load();
const LANGS = I18N.LANGS;
const GB = 1000 * 1000 * 1000;

function doc(over) {
  return Object.assign(
    {
      volume_bytes: 30 * GB,
      workspace_bytes: 29 * GB,
      snapshot_bytes: GB,
      evictable_bytes: 20 * GB,
      non_evictable_bytes: 10 * GB,
      orphan_bytes: 0,
      limit_bytes: 100 * GB,
      min_free_bytes: 10 * GB,
      free_bytes: 600 * GB,
      over_budget_bytes: 0,
      projected_short_free_bytes: 0,
      budget_unreachable: false,
      no_progress: false,
      measured_at: "2026-09-09T05:00:03Z",
      last_sweep_at: "2026-09-09T05:00:03Z",
      next_sweep_at: "2026-09-09T06:00:03Z",
      error_code: null,
    },
    over || {}
  );
}

describe("jobStorageLine", () => {
  test("쥐고 있는 양과 천장을 함께 준다", () => {
    const line = rcm.jobStorageLine(doc(), "2026-09-09T05:18:03Z", "en");
    assert.ok(line, "줄이 없다");
    assert.match(line.text, /30(\.0)? GB/, "쥔 양이 사라졌다");
    assert.match(line.text, /100(\.0)? GB/, "천장이 사라졌다");
    assert.equal(line.warn, false);
  });

  test("예산 초과 · 바닥 아래 · 무진전은 경고다", () => {
    assert.equal(rcm.jobStorageLine(doc({ volume_bytes: 118 * GB }), null, "en").warn, true);
    assert.equal(rcm.jobStorageLine(doc({ free_bytes: 4 * GB }), null, "en").warn, true);
    assert.equal(rcm.jobStorageLine(doc({ no_progress: true }), null, "en").warn, true);
    assert.equal(rcm.jobStorageLine(doc({ budget_unreachable: true }), null, "en").warn, true);
  });

  test("못 잰 회계는 0 으로 안 그린다", () => {
    const line = rcm.jobStorageLine(doc({ volume_bytes: null, error_code: "scan_EACCES" }), null, "en");
    assert.doesNotMatch(line.text, /\b0(\.0)? GB/, "모르는 값을 0 으로 그렸다");
    assert.equal(line.warn, true);
  });

  test("상한이 꺼져 있으면(null) 천장을 말하지 않는다", () => {
    const line = rcm.jobStorageLine(doc({ limit_bytes: null }), null, "en");
    assert.doesNotMatch(line.text, /null|undefined/);
    assert.equal(line.warn, false);
  });

  test("이 키가 없는 옛 상태 문서에서 안 깨진다", () => {
    assert.equal(rcm.jobStorageLine(null, null, "en"), null);
    assert.equal(rcm.jobStorageLine(undefined, null, "en"), null);
    assert.equal(rcm.jobStorageLine({}, null, "en"), null);
  });
});

describe("문자열", () => {
  test("host.job_storage 와 host.job_storage_unknown 이 두 언어에 다 있다", () => {
    assert.ok(I18N.has("host.job_storage"), "카탈로그에 host.job_storage 가 없다");
    assert.ok(I18N.has("host.job_storage_unknown"), "카탈로그에 host.job_storage_unknown 이 없다");
  });

  test("host.job_storage 가 {used, limit} 을 실제로 쓴다", () => {
    LANGS.forEach((lang) => {
      const line = I18N.t(lang, "host.job_storage", { used: "30.9 GB", limit: "107.4 GB" });
      assert.match(line, /30\.9 GB/, lang + ": 쥔 양이 사라졌다");
      assert.match(line, /107\.4 GB/, lang + ": 천장이 사라졌다");
    });
  });
});
