"use strict";
// W3 버전 페이지 본문 — 이전 버전 값으로 채워진 편집 칸 (docs/version-page-workplan.md §3.1 · §3.2).
//
// 잠그는 것:
// - 칸 하나의 **값과 출처**: 편집본 → 이전 버전 → 파일 → 빈 값. 화면이 셋을 구별해 말한다(R6).
// - «바뀜» 판정이 서버의 정규화와 같다 — 양끝 공백과 CRLF 는 고친 것이 아니다. 이것이 갈라지면
//   화면은 «바뀜» 이라는데 서버 diff 는 비어 있고, 심사에 `listing_json` 이 안 실린다(E10).
// - 자동 저장이 **고친 키만** 보낸다(AC-C6). 통째로 보내면 다른 브라우저의 편집을 덮어쓴다.
// - 상한을 넘겨도 값은 그대로 가고 카운터만 빨개진다(E9) — 스토어가 최종 판정이다.
// - 두 브라우저가 같은 드래프트를 고치면 폴링이 «다른 곳에서 바뀜» 을 센다(E8).
//
// 키와 차례는 서버 `release_state.PREFILL_KEYS` 와 같아야 하고, 그 잠금은 파이썬 쪽이다
// (tests/test_web.py::test_the_web_listing_keys_match_the_server_contract).

const test = require("node:test");
const { describe } = require("node:test");
const assert = require("node:assert/strict");

const { load, NOW } = require("./helpers");
const S = load().store;

/** `prefill.json` — 이전(라이브) 버전의 문안. 키는 review 계약의 listing 필드와 같다. */
function prefill(patch) {
  return Object.assign(
    {
      schema: 1,
      source: "asc_live:1.1.0 · play_listing",
      locale: "ko",
      ios: {
        subtitle: "Daily notes",
        promotional_text: "",
        description: "A calm journal for every day.",
        keywords: "journal,mood,notes",
        support_url: "https://example.invalid/support",
        marketing_url: "",
        whats_new: "Bug fixes.",
      },
      android: {
        title: "Journal",
        short_description: "A calm journal",
        full_description: "A calm journal for every day.",
        whats_new: "Bug fixes.",
      },
    },
    patch || {}
  );
}

/** `GET …/release/versions/<id>` 의 상세 — 행 + prefill + edited + diff + release(§15). */
function vdoc(patch) {
  return Object.assign(
    {
      id: 9,
      repo: "app",
      ios_version: "1.1.1",
      android_version: "1.0.1",
      build_name: "1.1.1",
      state: "editing",
      created_by: "pcs",
      prefill: prefill(),
      edited: null,
      diff: { fields: [], screenshots: { ios: "same", android: "same" } },
      release: { build_name: "1.1.1" },
    },
    patch || {}
  );
}

function ctx(patch) {
  return Object.assign({ version: vdoc(), file: null, typed: null, remote: {}, admin: true }, patch || {});
}

/** 모델 안에서 칸 하나를 찾는다 — 화면이 그리는 차례 그대로. */
function fieldOf(model, id) {
  let found = null;
  model.sections.forEach((s) =>
    s.groups.forEach((g) => g.fields.forEach((f) => { if (f.platform + "." + f.key === id) found = f; }))
  );
  return found;
}

describe("module contract", () => {
  test("rcm.store 가 C2 순수 함수를 내놓는다", () => {
    ["fieldLimit", "listingNorm", "listingDiff", "fieldLabelKey", "versionFieldModel",
      "versionPageModel", "listingPutBody", "remoteListingEdits", "saveBadge"].forEach((k) => {
      assert.equal(typeof S[k], "function", k);
    });
    assert.equal(S.VERSION_SAVE_DEBOUNCE_MS, 800, "§3.2 — 800 ms 디바운스");
  });
});

describe("상한과 정규화 (§3.1)", () => {
  test("whats_new 만 스토어마다 상한이 다르다 — App Store 4000 · Play 500", () => {
    assert.equal(S.fieldLimit("ios", "whats_new"), 4000);
    assert.equal(S.fieldLimit("android", "whats_new"), 500);
    assert.equal(S.fieldLimit("ios", "subtitle"), 30);
    assert.equal(S.fieldLimit("android", "short_description"), 80);
    assert.equal(S.fieldLimit("ios", "support_url"), 255);
    assert.equal(S.fieldLimit("ios", "nope"), null, "모르는 칸에 상한을 지어내지 않는다");
  });
  test("정규화는 서버 `_norm` 과 같다 — 양끝 공백 · CRLF → LF", () => {
    assert.equal(S.listingNorm("  a\r\nb  "), "a\nb");
    assert.equal(S.listingNorm("a\nb"), "a\nb");
    assert.equal(S.listingNorm(null), "");
    assert.equal(S.listingNorm(12), "", "문자열이 아니면 빈 값");
  });
});

describe("listingDiff — 서버 `release_state.listing_diff` 의 짝", () => {
  test("편집본에 있는 키만 보고, 줄끝·양끝 공백 차이는 같은 것이다", () => {
    const d = S.listingDiff(prefill(), { ios: { subtitle: "Daily notes\r\n", keywords: "AI,tarot" } });
    assert.deepEqual(d.fields, [{ platform: "ios", key: "keywords", old: "journal,mood,notes", new: "AI,tarot" }]);
    assert.equal(d.changed, 1);
    assert.equal(d.unchanged, 1, "공백만 다른 subtitle 은 안 센다");
  });
  test("E10 — 되돌려 이전 값과 같아지면 diff 가 빈다", () => {
    const d = S.listingDiff(prefill(), { ios: { keywords: "journal,mood,notes" } });
    assert.deepEqual(d.fields, []);
    assert.equal(d.changed, 0);
  });
  test("이전 문안이 모르는 스토어의 스크린샷은 `n/a` 다 — 지어내지 않는다", () => {
    const only = S.listingDiff({ ios: { subtitle: "x" } }, null);
    assert.deepEqual(only.screenshots, { ios: "same", android: "n/a" });
    assert.deepEqual(S.listingDiff(null, null).screenshots, { ios: "n/a", android: "n/a" });
  });
  test("prefill 이 모르는 키를 고치면 old 는 null 이다", () => {
    const d = S.listingDiff({ ios: {} }, { ios: { subtitle: "new" } });
    assert.deepEqual(d.fields, [{ platform: "ios", key: "subtitle", old: null, new: "new" }]);
  });
});

describe("versionFieldModel — 값과 출처 (R6 · AC-C5)", () => {
  test("아무것도 안 고쳤으면 이전 버전 값 그대로다", () => {
    const f = S.versionFieldModel("ios", "subtitle", { prefill: prefill() }, "en");
    assert.equal(f.value, "Daily notes");
    assert.equal(f.source, "prefill");
    assert.equal(f.sourceText, "same as the previous version");
    assert.equal(f.changed, false);
    assert.deepEqual(f.counter, { n: 11, limit: 30, tone: "ok", text: "11/30" });
    assert.equal(f.id, "f-ios-subtitle");
  });
  test("프리필이 그 칸을 모르면 파일 값 + «파일에서»", () => {
    const f = S.versionFieldModel("android", "title", { prefill: { android: {} }, file: { android: { title: "Journal" } } }, "en");
    assert.equal(f.value, "Journal");
    assert.equal(f.source, "file");
    assert.equal(f.sourceText, "from the file");
    assert.equal(f.changed, false, "파일 값은 내가 고친 것이 아니다");
  });
  test("아무 데도 없으면 빈 칸이고 그렇게 말한다", () => {
    const f = S.versionFieldModel("ios", "marketing_url", { prefill: { ios: {} } }, "en");
    assert.equal(f.value, "");
    assert.equal(f.source, "empty");
    assert.equal(f.counter.text, "0/255");
  });
  test("저장된 편집본이 이전 값을 이기고 «내가 고친 것» 이 된다", () => {
    const f = S.versionFieldModel("ios", "keywords", { prefill: prefill(), edited: { ios: { keywords: "AI,tarot" } } }, "en");
    assert.equal(f.value, "AI,tarot");
    assert.equal(f.source, "edited");
    assert.equal(f.changed, true);
    assert.equal(f.canRevert, true);
    assert.equal(f.prefill, "journal,mood,notes", "되돌릴 값을 들고 있다");
  });
  test("치는 중인 값이 저장된 편집본보다 앞선다 — 저장에 실패해도 화면에 남는다(E7)", () => {
    const f = S.versionFieldModel("ios", "keywords", {
      prefill: prefill(), edited: { ios: { keywords: "AI,tarot" } }, typed: { ios: { keywords: "AI,tarot,cards" } },
    }, "en");
    assert.equal(f.value, "AI,tarot,cards");
    assert.equal(f.source, "edited");
  });
  test("E10 — 이전 값으로 되돌리면 «이전 버전 그대로» 로 돌아가고 되돌리기가 닫힌다", () => {
    const f = S.versionFieldModel("ios", "keywords", {
      prefill: prefill(), edited: { ios: { keywords: "AI,tarot" } }, typed: { ios: { keywords: "journal,mood,notes " } },
    }, "en");
    assert.equal(f.changed, false, "양끝 공백은 고친 것이 아니다");
    assert.equal(f.source, "prefill");
    assert.equal(f.canRevert, false);
  });
  test("E9 — 상한을 넘겨도 값은 그대로이고 카운터만 빨갛다", () => {
    const long = "x".repeat(4001);
    const f = S.versionFieldModel("ios", "description", { prefill: prefill(), typed: { ios: { description: long } } }, "en");
    assert.equal(f.value.length, 4001);
    assert.equal(f.counter.tone, "bad");
    assert.equal(f.counter.text, "4001/4000");
  });
  test("글자 수는 코드포인트로 센다 — 한글도 이모지도 한 자다", () => {
    const f = S.versionFieldModel("ios", "subtitle", { prefill: { ios: { subtitle: "감정일기 🦔" } } }, "ko");
    assert.equal(f.counter.n, 6);
    assert.equal(f.sourceText, "이전 버전 그대로");
  });
  test("«다른 곳에서 바뀜» 은 칸에 붙는다(E8)", () => {
    const f = S.versionFieldModel("ios", "keywords", { prefill: prefill(), remote: { "ios.keywords": true } }, "en");
    assert.equal(f.remote, true);
  });
  test("릴리스 노트만 스토어마다 부르는 말이 다르다", () => {
    assert.equal(S.fieldLabelKey("ios", "whats_new"), "version.field.whats_new");
    assert.equal(S.fieldLabelKey("android", "whats_new"), "version.field.release_notes");
    assert.equal(S.fieldLabelKey("ios", "subtitle"), "review.field.subtitle");
  });
});

describe("versionPageModel — 절 둘과 그룹 차례 (§3.2 W3)", () => {
  test("절은 심사 패널과 같은 그룹 차례이고 칸은 서버 키 그대로다", () => {
    const m = S.versionPageModel(ctx(), "en");
    assert.deepEqual(m.platforms, ["ios", "android"]);
    assert.deepEqual(m.sections.map((s) => s.label), ["App Store · iOS", "Google Play · Android"]);
    assert.deepEqual(m.sections[0].groups.map((g) => g.key), ["version_info", "whats_new"]);
    assert.deepEqual(m.sections[1].groups.map((g) => g.key), ["store_listing", "release_notes"]);
    assert.deepEqual(m.sections[0].groups[0].fields.map((f) => f.key),
      ["subtitle", "promotional_text", "description", "keywords", "support_url", "marketing_url"]);
    assert.deepEqual(m.sections[1].groups[0].fields.map((f) => f.key),
      ["title", "short_description", "full_description"]);
  });
  test("E25 — 한쪽 스토어만 만든 드래프트에는 남의 절이 없다", () => {
    const m = S.versionPageModel(ctx({ version: vdoc({ android_version: null }) }), "en");
    assert.deepEqual(m.platforms, ["ios"]);
    assert.equal(m.sections.length, 1);
    assert.equal(m.sections[0].version, "1.1.1");
  });
  test("바뀐 칸을 세고 머리 문구가 그 수를 말한다", () => {
    const none = S.versionPageModel(ctx(), "en");
    assert.equal(none.changed, 0);
    assert.match(none.headText, /Prefilled with the previous version/);
    const some = S.versionPageModel(ctx({
      version: vdoc({ edited: { ios: { keywords: "AI,tarot" }, android: { whats_new: "New look." } } }),
    }), "en");
    assert.equal(some.changed, 2);
    assert.equal(some.headText, "2 fields differ from the previous version");
    assert.equal(fieldOf(some, "ios.keywords").changed, true);
    assert.equal(fieldOf(some, "ios.subtitle").changed, false);
  });
  test("스크린샷 표시는 서버 diff 가 말한 것뿐이다 — 없으면 «모름»", () => {
    const m = S.versionPageModel(ctx(), "ko");
    assert.equal(m.sections[0].screenshots.state, "same");
    assert.equal(m.sections[0].screenshots.text, "이전 버전과 같음");
    const na = S.versionPageModel(ctx({ version: vdoc({ diff: { fields: [], screenshots: { ios: "n/a", android: "n/a" } } }) }), "ko");
    assert.equal(na.sections[0].screenshots.state, "unknown");
    assert.equal(na.sections[0].screenshots.text, "이전 버전 파일을 모름");
  });
  test("AC-C7 — admin 이 아니면 읽기 전용이고 이유를 한 문장으로 말한다", () => {
    const m = S.versionPageModel(ctx({ admin: false }), "en");
    assert.equal(m.editable, false);
    assert.equal(m.readOnly, "admin");
    assert.match(m.readOnlyText, /admin token/);
  });
  test("닫힌 행(제출됨 · 버려짐)도 읽기 전용이다 — 서버가 409 `version_closed` 를 낸다", () => {
    ["submitted", "discarded"].forEach((state) => {
      const m = S.versionPageModel(ctx({ version: vdoc({ state: state }) }), "en");
      assert.equal(m.editable, false, state);
      assert.equal(m.readOnly, "closed", state);
      assert.match(m.readOnlyText, new RegExp(state));
    });
    assert.equal(S.versionPageModel(ctx({ version: vdoc({ state: "running" }) }), "en").editable, true,
      "회차가 도는 중에도 문안은 고칠 수 있다 — 서버가 막는 것은 닫힌 행뿐이다");
  });
  test("프리필이 없으면 그렇게 말하고 파일 값으로 채운다", () => {
    const m = S.versionPageModel(ctx({ version: vdoc({ prefill: null }), file: { ios: { subtitle: "Daily notes" } } }), "en");
    assert.equal(m.hasPrefill, false);
    assert.equal(m.prefillSource, null);
    assert.equal(fieldOf(m, "ios.subtitle").source, "file");
    const known = S.versionPageModel(ctx(), "en");
    assert.equal(known.prefillSource, "asc_live:1.1.0 · play_listing");
  });
  test("아무 문서도 없을 때 안 던진다", () => {
    const m = S.versionPageModel(null, "en");
    assert.deepEqual(m.platforms, ["ios", "android"]);
    assert.equal(m.changed, 0);
    assert.equal(m.editable, false);
  });
});

describe("자동 저장이 보내는 것 (AC-C6)", () => {
  test("고친 키만 실린다 — 통째로 보내지 않는다", () => {
    const typed = { ios: { keywords: "AI,tarot", subtitle: "Daily notes" }, android: { title: "Journal" } };
    assert.deepEqual(S.listingPutBody(typed, { "ios.keywords": true }), { ios: { keywords: "AI,tarot" } });
    assert.deepEqual(S.listingPutBody(typed, { "ios.keywords": true, "android.title": true }),
      { ios: { keywords: "AI,tarot" }, android: { title: "Journal" } });
  });
  test("보낼 것이 없으면 null 이라 부르지도 않는다", () => {
    assert.equal(S.listingPutBody({ ios: { keywords: "x" } }, {}), null);
    assert.equal(S.listingPutBody({}, { "ios.keywords": true }), null, "친 값이 없으면 안 보낸다");
    assert.equal(S.listingPutBody(null, null), null);
  });
  test("서버가 모르는 키는 아예 싣지 않는다 — 400 `listing_key` 를 자초하지 않는다", () => {
    const typed = { ios: { made_up: "x", keywords: "AI" }, windows: { title: "x" } };
    assert.deepEqual(S.listingPutBody(typed, { "ios.made_up": true, "windows.title": true, "ios.keywords": true }),
      { ios: { keywords: "AI" } });
  });
  test("빈 문자열도 값이다 — 지우는 것은 지워서 보낸다", () => {
    assert.deepEqual(S.listingPutBody({ ios: { promotional_text: "" } }, { "ios.promotional_text": true }),
      { ios: { promotional_text: "" } });
  });
});

describe("E8 — 두 브라우저가 같은 드래프트를 고친다", () => {
  test("서버 값이 우리가 본 것과 달라진 칸을 센다", () => {
    const found = S.remoteListingEdits({ ios: { keywords: "mine" } }, { ios: { keywords: "theirs" } }, {});
    assert.deepEqual(found, { "ios.keywords": true });
  });
  test("내가 방금 보낸 값이 돌아온 것은 남의 편집이 아니다", () => {
    const found = S.remoteListingEdits(null, { ios: { keywords: "mine" } }, { ios: { keywords: "mine" } });
    assert.deepEqual(found, {});
  });
  test("줄끝·양끝 공백만 다른 것도 남의 편집이 아니다", () => {
    assert.deepEqual(S.remoteListingEdits({ ios: { keywords: "a\nb" } }, { ios: { keywords: " a\r\nb " } }, {}), {});
  });
  test("남이 지운 칸도 바뀐 것이다", () => {
    assert.deepEqual(S.remoteListingEdits({ android: { title: "Journal" } }, null, {}), { "android.title": true });
  });
});

describe("saveBadge — 저장 상태 (§3.2)", () => {
  test("네 가지 형편", () => {
    assert.equal(S.saveBadge(null, "ko", NOW).state, "idle");
    assert.equal(S.saveBadge({ state: "saving" }, "ko", NOW).text, "저장하는 중…");
    const saved = S.saveBadge({ state: "saved", at: new Date(NOW - 12000).toISOString() }, "ko", NOW);
    assert.equal(saved.text, "자동 저장 · 12s 전");
    assert.equal(saved.tone, "ok");
    assert.equal(saved.retry, false);
    const failed = S.saveBadge({ state: "failed", detail: "http 401" }, "ko", NOW);
    assert.equal(failed.text, "저장 실패: http 401");
    assert.equal(failed.tone, "bad");
    assert.equal(failed.retry, true, "E7 — 실패에는 언제나 다시 보낼 길이 있다");
  });
  test("시각을 모르면 나이를 지어내지 않는다", () => {
    assert.equal(S.saveBadge({ state: "saved", at: null }, "en", NOW).text, "autosaved");
    assert.equal(S.saveBadge({ state: "saved", at: new Date(NOW).toISOString() }, "en", NaN).text, "autosaved");
  });
});
