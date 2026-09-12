"use strict";
// 웹 UI(M5j G5) — 서버 키 `cancel_requires_submission_token` 이 켜지면 페이지는 capability 를
// 가질 수 없다(비밀은 `rcm run` 을 돌린 세션에만 있다). 그래서 취소 버튼은 admin 토큰이 아닌 한
// 비활성이고 이유 문구가 붙는다. 순수 판정 `cancelLocked(server, me)` 를 잠근다.
// 구현보다 먼저 썼다(test-first).

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { load, APP_PATH } = require("./helpers");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const rcm = load();
const LANGS = I18N.LANGS;

describe("cancelLocked — 키가 켜진 서버에서는 admin 만 취소한다", () => {
  test("켜짐 + admin 아님 → 잠김", () => {
    assert.equal(rcm.cancelLocked({ cancel_requires_submission_token: true }, { name: "alice", admin: false }), true);
    assert.equal(rcm.cancelLocked({ cancel_requires_submission_token: true }, null), true);
  });
  test("켜짐 + admin → 열림", () => {
    assert.equal(rcm.cancelLocked({ cancel_requires_submission_token: true }, { name: "ops", admin: true }), false);
  });
  test("꺼짐 · 키 없는 옛 서버 · server 없음 → 열림(오늘 그대로)", () => {
    assert.equal(rcm.cancelLocked({ cancel_requires_submission_token: false }, { admin: false }), false);
    assert.equal(rcm.cancelLocked({}, { admin: false }), false);
    assert.equal(rcm.cancelLocked(null, { admin: false }), false);
    assert.equal(rcm.cancelLocked(undefined, undefined), false);
  });
});

describe("이유 문구는 두 언어에 있고 화면이 실제로 읽는다", () => {
  test("row.cancel_locked 가 두 언어에 있고 영어는 cancel token 을 말한다", () => {
    assert.ok(I18N.has("row.cancel_locked"));
    LANGS.forEach((lang) => {
      assert.ok(Object.prototype.hasOwnProperty.call(I18N.MESSAGES[lang], "row.cancel_locked"), lang);
      assert.ok(I18N.t(lang, "row.cancel_locked").length > 10, lang);
    });
    assert.match(I18N.t("en", "row.cancel_locked"), /cancel token/);
    assert.match(I18N.t("ko", "row.cancel_locked"), /cancel token/);
  });
  test("app.js 가 행과 액션 블록에서 cancelLocked 를 부르고 문구를 그린다", () => {
    const src = fs.readFileSync(APP_PATH, "utf8");
    assert.ok((src.match(/cancelLocked\(/g) || []).length >= 3, "wired at least twice besides its definition");
    assert.ok(src.indexOf('tr("row.cancel_locked")') >= 0, "the reason is drawn");
  });
});
