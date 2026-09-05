"use strict";
// 웹 UI 순수 함수 테스트의 공용 헬퍼.
// app.js 는 브라우저에서 window.rcm 에 순수 함수를 노출하고, module.exports 가 있으면
// module.exports = rcm 을 한다(workplan §0 결정 B). 여기서는 require 로 그 객체를 받는다.

const fs = require("node:fs");
const path = require("node:path");

const APP_PATH = path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "app.js");
const FIXTURE_DIR = path.join(__dirname, "fixtures");

// 모든 픽스처의 generated_at. 상대 시간(경과·대기·나이·카운트다운)은 전부 이 시각 기준이다.
// Asia/Seoul 로는 2026-09-04 09:52:12.
const NOW = Date.UTC(2026, 8, 4, 0, 52, 12);
const TZ = "Asia/Seoul";

// app.js 를 require 한다. 브라우저 전용 전역(window)이 없어도 파일이 평가되도록 최소 shim 을 둔다.
// document 는 정의하지 않는다 — boot() 는 typeof document !== "undefined" 일 때만 돌아야 한다.
function load() {
  if (typeof globalThis.window === "undefined") {
    globalThis.window = globalThis;
  }
  return require(APP_PATH);
}

// fixtures/status-<name>.json 을 읽어 매번 새 객체로 준다(테스트가 마음껏 고쳐도 서로 안 샌다).
function fixture(name) {
  const file = path.join(FIXTURE_DIR, `status-${name}.json`);
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

// pools[0].queue 에서 id 로 행 하나. 없으면 undefined.
function job(status, id) {
  const queue = status.pools[0].queue;
  return queue ? queue.find((row) => row.id === id) : undefined;
}

// pools[0].recent 에서 id 로 하나.
function recentJob(status, id) {
  const recent = status.pools[0].recent;
  return recent ? recent.find((row) => row.id === id) : undefined;
}

// ms → 스키마와 같은 UTC ISO(초 단위, Z).
function iso(ms) {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

// NOW 에서 sec 초 뒤(음수면 앞)의 ISO.
function fromNow(sec) {
  return iso(NOW + sec * 1000);
}

module.exports = { APP_PATH, NOW, TZ, load, fixture, job, recentJob, iso, fromNow };
