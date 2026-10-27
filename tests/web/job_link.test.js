"use strict";
// 잡 링크 칩 — 호출자가 잡에 붙인 `link: {url, label}` 을 잡 이름 옆의 <a> 로 그린다.
// 서버가 이미 검증하지만(core/joblink.py) 화면도 다시 본다(심층 방어): http(s) 가 아니면 링크를
// 안 만들고, 글과 주소는 escape 하고, 새 탭으로 열되 opener 를 넘기지 않는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load } = require("./helpers");

const rcm = load();
const PR = "https://github.com/fmmc-tech/dolomood-app-renew/pull/816";

describe("jobLinkHtml", () => {
  test("is exported (the chip must be testable without a DOM)", () => {
    assert.equal(typeof rcm.jobLinkHtml, "function");
    assert.equal(typeof rcm.jobLinkLabel, "function");
  });

  test("an http(s) link becomes one <a> that opens a new tab without an opener", () => {
    const h = rcm.jobLinkHtml({ url: PR, label: "PR #816" });
    assert.match(h, /^<a class="job-link" /);
    assert.ok(h.includes(`href="${PR}"`), h);
    assert.ok(h.includes('target="_blank"'), h);
    assert.ok(h.includes('rel="noopener noreferrer"'), h);
    assert.ok(h.includes(">PR #816<"), h);
    assert.ok(h.includes("↗"), h);
    // 라벨이 잘려도 전체 주소는 툴팁으로 읽힌다
    assert.ok(h.includes(`title="${PR}"`), h);
    assert.equal((h.match(/<a /g) || []).length, 1, h);
  });

  test("http:// works too", () => {
    const h = rcm.jobLinkHtml({ url: "http://ci.local:8080/build/7", label: "build 7" });
    assert.ok(h.includes('href="http://ci.local:8080/build/7"'), h);
  });

  test("no link, a null link or a link without a url draws nothing", () => {
    assert.equal(rcm.jobLinkHtml(null), "");
    assert.equal(rcm.jobLinkHtml(undefined), "");
    assert.equal(rcm.jobLinkHtml({}), "");
    assert.equal(rcm.jobLinkHtml({ url: "" }), "");
    assert.equal(rcm.jobLinkHtml({ url: 42 }), "");
    assert.equal(rcm.jobLinkHtml("https://example.com"), "");
  });

  for (const url of [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "java\tscript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "//evil.example/x",
    "/relative/path",
    "ftp://example.com/a",
    "https://",
    "https://exa mple.com/",
    "https://example.com/\u0000",
    "https://example.com/" + "a".repeat(500),
  ]) {
    test(`a non-http(s) or malformed url is not a link: ${JSON.stringify(url).slice(0, 40)}`, () => {
      assert.equal(rcm.jobLinkHtml({ url, label: "PR #816" }), "");
      assert.equal(rcm.jobLinkLabel({ url, label: "PR #816" }), null);
    });
  }

  test("the label and the url are escaped", () => {
    const h = rcm.jobLinkHtml({
      url: 'https://example.com/a"onmouseover="alert(1)',
      label: '<img src=x onerror=alert(1)> & "q"',
    });
    assert.ok(!h.includes("<img"), h);
    assert.ok(h.includes("&lt;img src=x onerror=alert(1)&gt; &amp; &quot;q&quot;"), h);
    assert.ok(!/href="[^"]*"onmouseover=/.test(h), h);
    assert.ok(h.includes("&quot;onmouseover=&quot;"), h);
  });

  test("without a label the chip shows the host and the end of the path", () => {
    assert.equal(rcm.jobLinkLabel({ url: PR }), "github.com/…/pull/816");
    assert.equal(rcm.jobLinkLabel({ url: PR, label: "" }), "github.com/…/pull/816");
    assert.equal(rcm.jobLinkLabel({ url: "https://ci.local:8080/" }), "ci.local:8080");
    assert.equal(rcm.jobLinkLabel({ url: "https://ci.local/build" }), "ci.local/build");
    assert.equal(rcm.jobLinkLabel({ url: "https://ci.local/a/b" }), "ci.local/a/b");
    const h = rcm.jobLinkHtml({ url: PR, label: null });
    assert.ok(h.includes(">github.com/…/pull/816<"), h);
  });

  test("a label longer than 40 characters is cut with an ellipsis (the server caps it anyway)", () => {
    const label = rcm.jobLinkLabel({ url: PR, label: "x".repeat(60) });
    assert.equal(label.length, 40);
    assert.ok(label.endsWith("…"));
    const derived = rcm.jobLinkLabel({ url: "https://a-very-long-host-name.example.internal/" + "seg/".repeat(5) + "x".repeat(50) });
    assert.ok(derived.length <= 40, derived);
  });
});

describe("keyWithLinkHtml — the job-name cell", () => {
  test("without a link it is today's plain name span (rows without links do not change)", () => {
    assert.equal(rcm.keyWithLinkHtml("gate:full", null), '<span class="key">gate:full</span>');
    assert.equal(rcm.keyWithLinkHtml("gate:full", { url: "javascript:x" }), '<span class="key">gate:full</span>');
  });

  test("with a link the name and the chip share one line box, the chip right after the name", () => {
    const h = rcm.keyWithLinkHtml("gate:full", { url: PR, label: "PR #816" });
    assert.match(h, /^<span class="key-line"><span class="key">gate:full<\/span><a class="job-link" /);
    assert.ok(h.endsWith("</a></span>"), h);
  });

  test("the name is escaped", () => {
    const h = rcm.keyWithLinkHtml("<b>x</b>", { url: PR, label: "PR #816" }, "kn");
    assert.ok(h.includes('<span class="kn">&lt;b&gt;x&lt;/b&gt;</span>'), h);
  });
});
