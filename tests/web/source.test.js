"use strict";
// Source 칸(큐 표 마지막 열) — git_ref 행은 sha 앞 7자 버튼 + 「repo · ref <ref>」, tree 행은
// base_sha 앞 7자 + uncommitted 표지(명세 §1.5 「app.js sourceHtml 이미 있음 — 테스트로 잠근다」).
// sourceHtml 은 HTML 문자열을 돌려주므로 ref·repo 는 반드시 esc 를 거쳐야 한다(ref 는 사용자 입력).
// 같은 이유로 진행 문구(reasonText)의 git_ref 분기와 재실행 명령(rerunCommand)도 여기서 잠근다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, NOW } = require("./helpers");

const rcm = load();
const DASH = "—";
const SHA = "0123456789abcdef0123456789abcdef01234567";

// git_ref 큐 행. source 를 덮어쓸 수 있다.
function gitRow(source, patch) {
  return Object.assign(
    { id: 7, state: "queued", source: Object.assign({ mode: "git_ref", repo: "app", ref: "main", sha: SHA }, source) },
    patch,
  );
}

describe("module contract", () => {
  test("DASH is exported and is the dash used everywhere", () => {
    assert.equal(rcm.DASH, DASH);
  });

  test("sourceHtml is exported on rcm (the Source cell must be testable without a DOM)", () => {
    assert.equal(typeof rcm.sourceHtml, "function", "rcm.sourceHtml must be a function");
  });
});

describe("sourceHtml — git_ref", () => {
  test("short sha button + 'repo · ref <ref>'", () => {
    const h = rcm.sourceHtml(gitRow());
    assert.ok(h.includes("0123456"), h);
    assert.ok(!h.includes(SHA), "the full sha is not shown inline");
    assert.ok(h.includes("app"), h);
    assert.ok(h.includes("ref main"), h);
    assert.ok(h.includes('data-src="7"'), h);
    assert.ok(h.includes('class="sha"'), h);
  });

  test("ref and repo are HTML-escaped", () => {
    const h = rcm.sourceHtml(gitRow({ ref: "<b>x</b>", repo: 'a"b' }));
    assert.ok(h.includes("ref &lt;b&gt;x&lt;/b&gt;"), h);
    assert.ok(!h.includes("<b>"), h);
    assert.ok(h.includes("a&quot;b"), h);
  });

  test("sha null → dash in the button, ref still shown", () => {
    const h = rcm.sourceHtml(gitRow({ sha: null }));
    assert.ok(h.includes(">" + DASH + "</button>"), h);
    assert.ok(h.includes("ref main"), h);
  });

  test("sha shorter than 7 (bad server) → shown as is, no throw", () => {
    const h = rcm.sourceHtml(gitRow({ sha: "abc" }));
    assert.ok(h.includes(">abc</button>"), h);
  });

  test("ref null → 'ref —'", () => {
    assert.ok(rcm.sourceHtml(gitRow({ ref: null })).includes("ref " + DASH));
  });

  test("git_ref rows carry no tree-only fragments", () => {
    const h = rcm.sourceHtml(gitRow());
    assert.ok(!h.includes("uncommitted"), h);
    assert.ok(!h.includes("tree "), h);
    assert.ok(!h.includes("not received yet"), h);
  });
});

describe("sourceHtml — tree (regression)", () => {
  const status = fixture("main");

  test("dirty tree → base_sha[0:7], 'uncommitted' badge, tree hash in the title, repo below", () => {
    const h = rcm.sourceHtml(job(status, 412));
    assert.ok(h.includes("abc123f"), h);
    assert.ok(h.includes("uncommitted"), h);
    assert.ok(h.includes('title="tree 9f8e7d6c5b4a39281706f5e4d3c2b1a0"'), h);
    assert.ok(h.includes("org/app"), h);
    assert.ok(!h.includes("ref "), h);
    assert.ok(h.includes('data-src="412"'), h);
  });

  test("clean tree → no 'uncommitted'", () => {
    const h = rcm.sourceHtml(job(status, 413));
    assert.ok(h.includes("def4567"), h);
    assert.ok(!h.includes("uncommitted"), h);
  });

  test("uploading before the manifest arrived → 'not received yet'", () => {
    const row = structuredClone(job(status, 415));
    row.source.base_sha = null;
    assert.ok(rcm.sourceHtml(row).includes("not received yet"));
  });

  test("uploading after the manifest → the normal cell", () => {
    const h = rcm.sourceHtml(job(status, 415));
    assert.ok(h.includes("77aa88b"), h);
    assert.ok(!h.includes("not received yet"), h);
  });

  test("tree_hash null → title 'tree —'; base_sha null (queued) → dash button", () => {
    const row = structuredClone(job(status, 413));
    row.source.tree_hash = null;
    row.source.base_sha = null;
    const h = rcm.sourceHtml(row);
    assert.ok(h.includes('title="tree ' + DASH + '"'), h);
    assert.ok(h.includes(">" + DASH + "</button>"), h);
  });

  test("source missing → no throw, dash button", () => {
    assert.ok(rcm.sourceHtml({ id: 1, state: "queued" }).includes(DASH));
  });
});

describe("reasonText — materializing a git_ref job", () => {
  const status = fixture("main");
  // 자재화 중인 git_ref 행: 워커가 fetch 하는 동안 phase 가 materializing 이고 steps 는 비어 있다
  function materializing(source) {
    return gitRow(source, {
      state: "running", reason: "materializing", lane: 1, estimate: null,
      progress: { timing: "as_received", phase: "materializing", last_output_at: null, steps_total: null,
        steps_total_partial: false, steps_done: 0, current_index: null, current_name: null,
        current_seconds: null, job_seconds: 1, failed_step: null, steps: [] },
    });
  }

  test("'preparing workspace · fetching <ref>'", () => {
    const r = rcm.reasonText(materializing(), NOW, status);
    assert.equal(r.text, "preparing workspace · fetching main");
    assert.equal(r.actionable, false);
    assert.deepEqual(r.links, []);
  });

  test("ref unknown → 'preparing workspace' only (never 'unpacking' for git_ref)", () => {
    const row = materializing({ ref: null, bytes: 48213344 });
    assert.equal(rcm.reasonText(row, NOW, status).text, "preparing workspace");
  });

  test("reasonText returns text, not HTML — escaping is the renderer's job", () => {
    const row = materializing({ ref: "<b>" });
    assert.equal(rcm.reasonText(row, NOW, status).text, "preparing workspace · fetching <b>");
  });
});

describe("rerunCommand — git_ref", () => {
  // 명세 §1.5: git_ref 프리셋은 --ref 가 없으면 usage 2 다. 최근 실패 행의 「다시 실행」 명령이
  // `rcm run deploy` 로 끝나면 복사해 붙이는 순간 실패한다 — ref 를 같이 실어야 한다.
  test("failed git_ref job → 'rcm run <preset> --ref <ref>'", () => {
    const j = { id: 9, preset: "deploy", key: "deploy", state: "failed", inputs: {},
      source: { mode: "git_ref", repo: "app", ref: "v1.2.3", sha: SHA } };
    assert.equal(rcm.rerunCommand(j), "rcm run deploy --ref v1.2.3");
  });

  test("inputs and --ref together", () => {
    const j = { preset: "deploy", inputs: { env: "prod" }, source: { mode: "git_ref", ref: "main", sha: SHA } };
    const cmd = rcm.rerunCommand(j);
    assert.ok(cmd.startsWith("rcm run deploy"), cmd);
    assert.ok(cmd.includes(" -f env=prod"), cmd);
    assert.ok(cmd.includes(" --ref main"), cmd);
  });

  test("tree job unchanged (no --ref)", () => {
    const cmd = rcm.rerunCommand({ preset: "gate", inputs: { scope: "full" }, source: { mode: "tree" } });
    assert.equal(cmd, "rcm run gate -f scope=full");
  });
});
