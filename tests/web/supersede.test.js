"use strict";
// M5o A — 새 잡에 밀려 치워진 잡의 요약(`superseded by #N`)에서 `#N` 을 그 잡으로 가는 버튼으로
// 그린다(docs/m5o-workplan.md §2.3 · 시나리오 A35). 버튼은 다른 잡 링크와 같은 `jlink` + `data-goto`
// 라 클릭 처리는 이미 있다. 나머지 글자는 escape 한다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();

function superseded(extra) {
  return Object.assign({
    id: 41, state: "cancelled", summary: "superseded by #57", summary_code: "superseded",
    summary_args: { by: 57 }, cancelled_by: "superseded", started_at: null,
    requester: { name: "alice-laptop", label: "alice@laptop" }, joiners: [], superseded_by: 57
  }, extra || {});
}

describe("superseded summary", () => {
  test("outcome text in both languages names the new job", () => {
    assert.equal(rcm.outcomeText(superseded(), "en"), "superseded by #57");
    const ko = rcm.outcomeText(superseded(), "ko");
    assert.ok(ko.includes("#57"), ko);
  });

  test("a job superseded before it started says so, not 'cancelled before start · by superseded'", () => {
    const l = rcm.recentLine(superseded(), "UTC", Date.now(), "en");
    assert.equal(l.summary, "superseded by #57");
    assert.equal(l.supersededBy, 57);
  });

  test("a running job that was superseded says the same", () => {
    const l = rcm.recentLine(superseded({ started_at: "2026-11-18T03:00:00Z" }), "UTC", Date.now(), "en");
    assert.equal(l.summary, "superseded by #57");
    assert.equal(l.supersededBy, 57);
  });

  test("an ordinary cancel has no successor", () => {
    const l = rcm.recentLine({ id: 3, state: "cancelled", cancelled_by: "alice-laptop", started_at: null,
      requester: { name: "alice-laptop", label: "alice@laptop" } }, "UTC", Date.now(), "en");
    assert.equal(l.supersededBy, null);
  });

  test("#N becomes one jlink button that goes to that job", () => {
    const h = rcm.supersededHtml("superseded by #57", 57);
    assert.equal(h, 'superseded by <button type="button" class="jlink" data-goto="57">#57</button>');
  });

  test("the Korean sentence links the same way", () => {
    const ko = rcm.outcomeText(superseded(), "ko");
    const h = rcm.supersededHtml(ko, 57);
    assert.ok(h.includes('data-goto="57">#57</button>'), h);
    assert.equal((h.match(/<button/g) || []).length, 1, h);
  });

  test("the rest of the text is escaped", () => {
    const h = rcm.supersededHtml("<b>x</b> #57", 57);
    assert.ok(h.startsWith("&lt;b&gt;x&lt;/b&gt; "), h);
  });

  test("no number or a text without #N draws plain escaped text", () => {
    assert.equal(rcm.supersededHtml("a <b>", null), "a &lt;b&gt;");
    assert.equal(rcm.supersededHtml("superseded", 57), "superseded");
    // #570 안의 #57 을 잘라 링크로 만들지 않는다
    assert.equal(rcm.supersededHtml("by #570", 57), "by #570");
  });

  test("a non-integer successor is ignored", () => {
    const l = rcm.recentLine(superseded({ superseded_by: "57" }), "UTC", Date.now(), "en");
    assert.equal(l.supersededBy, null);
  });
});
