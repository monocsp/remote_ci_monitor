"use strict";
// 바텀시트의 순수 함수 `sheetModel` (docs/version-page-workplan.md §4.1 · §4.3 · 기획 R8~R12).
//
// 잠그는 것:
// - 색과 접힌 머리 한 줄이 §4.3 의 아홉 가지 형편을 **서로 다르게** 말한다(편집 중 · 도는 중 ·
//   사람 차례 · 실패 · 결과 모름 · 드리프트 · 제출 준비됨 · 제출됨 · 만료).
// - «남은 것» 의 **차례**가 §4.1 그대로다 — 첫 항목이 접힌 머리에 나오므로 차례가 곧 중요도다.
//   각 항목은 고치는 곳(이 페이지의 칸 또는 다른 화면)을 들고 있다.
// - `canSubmit` 은 새 판정이 아니다: 기존 `submitDecision` ∧ 막는 남은 것이 없음.
// - `pct` 는 0.3.3 의 막대와 **같은 수식**이고 분모만 10(V 포함)이다.
// - 보내는 본문에 `version_id` 와 **버전 행의** build_name 이 들어간다(서버는 다른 이름을 400 으로
//   거절한다). 빌드 번호는 플랜의 n 뿐이다 — 자동이면 "auto", 직접이면 친 글자 그대로.
// - 엣지케이스 E11 · E17 · E18 · E19 · E20 · E21 이 각각 한 줄로 드러난다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, NOW, TZ } = require("./helpers");

const S = load().store;
const iso = (sec) => new Date(NOW - sec * 1000).toISOString();
const DCTX = { status: 200, lang: "en", tzName: TZ, nowMs: NOW };

/** 드라이버 원본(`GET …/release/driver`) → `stepperModel`. */
function driverDoc(patch) {
  return Object.assign({
    configured: true, running: false, build_name: "1.1.1", started_at: iso(2280),
    started_by: "pcs", pid: null, exit_code: null, log_tail: [], plan_n: 181, status: [],
    knows_version_stage: true,
  }, patch || {});
}
const dm = (patch) => S.stepperModel(driverDoc(patch), DCTX);
const noDriver = () => S.stepperModel({ configured: false }, DCTX);

/** `GET …/release` 보기 — 버전 상세가 `release` 로 실어 준다(§15). */
function release(patch) {
  return Object.assign({
    build_name: "1.1.1",
    plan: {
      job_id: 641, state: "succeeded", build_name: "1.1.1", age_seconds: 200, stale: false,
      doc: { schema: 1, build_name: "1.1.1", n: 181, store: { asc_live: "1.1.0" }, blockers: [], warnings: [] },
    },
    review: {
      plan: {
        job_id: 651, state: "succeeded", age_seconds: 200, stale: false,
        doc: { schema: 2, build_name: "1.1.1", n: 181, plan_verdict: "ok", ios: "ready", android: "ready" },
      },
      result: null,
    },
    upload: {
      job_id: 650, state: "succeeded", finished_at: iso(3000),
      doc: { schema: 1, n: 181, status: "success", platforms: ["ios", "android"], tag: "prod/1.1.1-181" },
    },
    jobs: [],
  }, patch || {});
}
/** 버전 상세 한 행. `release` 는 그 안에 들어 있다 — 시트는 따로 부르지 않는다. */
function version(patch) {
  return Object.assign({
    id: 7, repo: "app", ios_version: "1.1.1", android_version: "1.0.1", build_name: "1.1.1",
    state: "editing", created_by: "pcs", created_at: iso(2400), last_edit_at: iso(300),
    expires_at: new Date(NOW + 3600 * 1000).toISOString(), expired: false, expiry_warned: 0,
    release_id: null, review_job_id: null, upload_job_id: null, create_job_id: 700,
    prefill: { ios: { keywords: "a,b,c", whats_new: "Bug fixes." }, android: { whats_new: "Bug fixes." } },
    edited: null, diff: { fields: [], screenshots: { ios: "same", android: "same" } },
    release: release(),
  }, patch || {});
}
/** `versionPageModel` 의 모양 가운데 시트가 보는 것만 — 카운터가 빨간 칸이 «남은 것» 이다. */
function page(fields) {
  return { sections: [{ platform: "ios", groups: [{ key: "version_info", fields: fields || [] }] }] };
}
const okField = { platform: "ios", key: "keywords", id: "f-ios-keywords", label: "Keywords", counter: { tone: "ok", text: "5/100" } };
const badField = { platform: "ios", key: "keywords", id: "f-ios-keywords", label: "Keywords", counter: { tone: "bad", text: "101/100" } };

function ctx(patch) {
  return Object.assign({
    version: version(), release: release(), repo: "app",
    profile: { default_branch: "main", presets: { review: "release-review" } },
    driver: noDriver(), layers: { bar: null, now: null, items: [], current: null },
    page: page([okField]), graphics: { ios: 4, android: 2 },
    choices: { platforms: { ios: true, android: true }, managed: true, listingFull: false, phased: true, nMode: "auto", typedN: "" },
    token: "t", admin: true, busy: null, refusal: null, lang: "en", nowMs: NOW, tzName: TZ,
  }, patch || {});
}
/** 도는 잡이 있는 `buildLayers` 결과(분모는 그 잡의 것). */
function layersWith(pct) {
  return {
    bar: {
      jobId: 643, preset: "scenario-qa", progress: { pct: pct, basis: "sub", done: 17, total: 30 },
      head: pct + "% · 17/30 · chunks", basis: "basis: declared chunks",
      startedAt: iso(600), finishes: "finishes 21:40",
    },
    now: "Now: #643 scenario-qa", items: [],
    current: { id: 643, preset: "scenario-qa", state: "running", started_at: iso(600) },
  };
}
const codes = (m) => m.remaining.map((r) => r.code);

describe("module contract", () => {
  test("rcm.store 가 시트의 순수 함수를 내보낸다", () => {
    ["sheetModel", "sheetStages", "sheetRemaining", "versionRunning"].forEach((k) => {
      assert.equal(typeof S[k], "function", k);
    });
    assert.deepEqual(S.SHEET_STAGES, ["V", "S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]);
  });
  test("최상단 막대는 사라졌다 — 그 이야기는 시트가 한다 (결정 Q6)", () => {
    assert.equal(S.releaseBarModel, undefined);
  });
});

describe("§4.3 편집 중 · 빌드 없음 (AC-D1 · AC-D2)", () => {
  const m = () => S.sheetModel(ctx({
    version: version({ release: release({ upload: null }) }),
    release: release({ upload: null }),
    choices: { platforms: { ios: true, android: true }, managed: false, listingFull: false, phased: true, nMode: "auto", typedN: "" },
  }));
  test("색은 new, 첫 남은 것은 build_missing, 제출은 닫힌다", () => {
    const s = m();
    assert.equal(s.tone, "new");
    assert.equal(s.remaining[0].code, "build_missing");
    assert.equal(s.canSubmit, false);
    assert.ok(s.reasons.includes("build_missing"), s.reasons);
  });
  test("접힌 머리 한 줄이 «남은 것 n · 첫 항목» 이고 그 수가 목록과 같다 (R11)", () => {
    const s = m();
    assert.equal(s.head.remainingCount, s.remaining.length);
    assert.equal(s.head.firstRemaining, s.remaining[0].text);
    assert.match(s.head.statusLine, /^2 left before review · No build yet/);
    assert.equal(s.head.live, false, "도는 것이 없으면 접힌 줄에 막대가 없다");
  });
  test("빌드가 없으면 막대는 빗금 — 퍼센트를 지어내지 않는다", () => {
    assert.equal(m().pct, null);
  });
  test("«남은 것» 의 차례가 §4.1 그대로다", () => {
    const s = S.sheetModel(ctx({
      version: version({ release: release({ upload: null, plan: null, review: { plan: null, result: null } }) }),
      release: release({ upload: null, plan: null, review: { plan: null, result: null } }),
      page: page([badField]),
      graphics: { ios: 4, android: 0 },
      choices: { platforms: { ios: true, android: true }, managed: false, listingFull: false, phased: true, nMode: "typed", typedN: "" },
    }));
    assert.deepEqual(codes(s), [
      "build_missing", "plan_missing", "n_unknown", "managed_unconfirmed", "listing_bad", "graphics_missing",
    ]);
  });
});

describe("§4.3 도는 중 (AC-D1 · AC-D4 · R8)", () => {
  const running = (pct) => S.sheetModel(ctx({
    version: version({ state: "running", release_id: 12, release: release({ upload: null }) }),
    release: release({ upload: null }),
    driver: dm({ running: true, pid: 4242, status: ["release 1.1.1: stage S5 scenario QA · job #643"] }),
    layers: layersWith(pct),
  }));
  test("색은 running 이고 단계 칩은 V 를 포함해 열 칸이다", () => {
    const s = running(57);
    assert.equal(s.tone, "running");
    assert.equal(s.stages.length, 10);
    assert.deepEqual(s.stages.map((x) => x.id), S.SHEET_STAGES);
    assert.equal(s.stages[0].id, "V");
    assert.equal(s.stages[0].state, "done", "행이 있으면 V 는 끝난 단계다");
    assert.deepEqual(s.stages.map((x) => x.state), ["done", "done", "done", "done", "done", "done", "current", "todo", "todo", "todo"]);
  });
  test("pct 는 0.3.3 과 같은 수식, 분모만 10 — 6 단계 끝 + 잡 0.57", () => {
    assert.equal(running(57).pct, Math.round((6 + 0.57) / 10 * 100));
    assert.equal(running(null).pct, 60, "잡 진행이 없으면 단계만으로");
    assert.match(running(57).basis, /declared stages \+ the running job/);
    assert.match(running(null).basis, /10 declared stages, each worth the same/);
  });
  test("접힌 한 줄에도 막대와 진행 정도가 보인다 (R8 · 소유자가 못 박은 것)", () => {
    const s = running(57);
    assert.equal(s.head.live, true);
    assert.equal(s.head.pct, s.pct);
    assert.match(s.head.statusLine, /S5 scenario QA · stage 7 of 10 · 66%/);
    assert.match(s.head.statusLine, /Now: #643 scenario-qa/);
    assert.match(s.head.statusLine, /elapsed 38m/, "경과는 회차가 시작한 때부터다 — 도는 잡이 아니라");
    assert.match(s.head.statusLine, /finishes 21:40/);
  });
  test("도는 중이면 «빌드 없음» 이 아니라 «지금 도는 중» 한 줄이고 제출은 닫힌다", () => {
    const s = running(57);
    assert.deepEqual(codes(s), ["build_missing"]);
    assert.equal(s.remaining[0].severity, "run");
    assert.match(s.remaining[0].text, /running now \(S5\)/);
    assert.deepEqual(s.remaining[0].fix, { route: null, anchor: "#sheet-stages" });
    assert.equal(s.canSubmit, false);
  });
  test("업로드가 끝났는데 심사 작업이 아직 돌면 round_running 이고 **이름을 댄다** (§15)", () => {
    const s = S.sheetModel(ctx({
      version: version({ state: "running", review_job_id: 660 }),
      layers: layersWith(20),
    }));
    assert.deepEqual(codes(s), ["round_running"]);
    assert.match(s.remaining[0].text, /#643 scenario-qa is still running/);
    assert.equal(s.canSubmit, false);
    // 도는 잡을 모르면 행에 붙은 것들의 이름을 댄다 — 상태만 말하지 않는다
    const noJob = S.sheetModel(ctx({
      version: version({ state: "running", review_job_id: 660, release_id: 12 }),
      layers: { bar: null, now: null, items: [], current: null },
    }));
    assert.match(noJob.remaining[0].text, /release round #12 · review job #660 is still running/);
  });
  test("드라이버가 없으면 단계는 이번 회차의 작업이다 (E25)", () => {
    const s = S.sheetModel(ctx({
      driver: noDriver(),
      layers: { bar: null, now: null, current: null, items: [
        { id: 641, preset: "release-plan", tone: "ok", text: "#641 release-plan" },
        { id: 650, preset: "release-upload", tone: "running", text: "#650 release-upload" },
      ] },
    }));
    assert.deepEqual(s.stages.map((x) => x.id), ["#641", "#650"]);
    assert.deepEqual(s.stages.map((x) => x.state), ["done", "current"]);
  });
});

describe("§4.3 사람 차례 · 실패 · 결과 모름 · 드리프트", () => {
  const withDriver = (patch, extra) => S.sheetModel(ctx(Object.assign({
    version: version({ state: "running", release_id: 12, release: release({ upload: null }) }),
    release: release({ upload: null }),
    driver: dm(patch),
  }, extra || {})));
  test("exit 2 는 사람 차례(황토), auto 회차는 그냥 진행 중이다", () => {
    assert.equal(withDriver({ exit_code: 2, status: ["release 1.1.1: stage S2 waiting"] }).tone, "human");
    assert.equal(withDriver({ exit_code: 2, auto_n: true }).tone, "running");
  });
  test("exit 1 은 빨강 · exit 3 은 보라 · exit 4 는 황토", () => {
    assert.equal(withDriver({ exit_code: 1 }).tone, "bad");
    assert.equal(withDriver({ exit_code: 3 }).tone, "lost");
    assert.equal(withDriver({ exit_code: 4 }).tone, "warn");
  });
  test("E20 — 업로드가 lost 면 색은 lost 고 «다시 올리지 말라» 고 말한다", () => {
    const lost = release({ upload: { job_id: 650, state: "lost", doc: null } });
    const s = S.sheetModel(ctx({ version: version({ release: lost }), release: lost }));
    assert.equal(s.tone, "lost");
    assert.equal(s.remaining[0].code, "upload_lost");
    assert.match(s.remaining[0].text, /do not resubmit/);
    assert.equal(s.canSubmit, false);
  });
  test("E19 — unsafe_release_type 은 색이 bad 고 제출이 닫힌다", () => {
    const unsafe = release({ review: { plan: { job_id: 651, state: "succeeded", age_seconds: 200, stale: false,
      doc: { schema: 2, build_name: "1.1.1", n: 181, plan_verdict: "ok", ios: "unsafe_release_type", android: "ready" } }, result: null } });
    const s = S.sheetModel(ctx({ version: version({ release: unsafe }), release: unsafe }));
    assert.equal(s.tone, "bad");
    assert.ok(codes(s).includes("unsafe"), codes(s));
    assert.equal(codes(s)[codes(s).length - 1], "unsafe", "안전은 목록의 마지막이다(§4.1)");
    assert.equal(s.canSubmit, false);
  });
  test("E16 — V 단계를 모르는 드라이버는 경고 한 줄이고 제출은 막지 않는다", () => {
    const s = S.sheetModel(ctx({ driver: dm({ knows_version_stage: false }) }));
    const row = s.remaining.filter((r) => r.code === "driver_no_v")[0];
    assert.ok(row, codes(s));
    assert.equal(row.severity, "warn");
    assert.equal(s.canSubmit, true, "경고는 제출을 닫지 않는다");
  });
});

describe("§4.3 제출 준비됨 (AC-D1 · AC-D5 · W5)", () => {
  test("남은 것이 없고 제출이 열린다", () => {
    const s = S.sheetModel(ctx());
    assert.equal(s.tone, "ok");
    assert.deepEqual(s.remaining, []);
    assert.equal(s.canSubmit, true);
    assert.deepEqual(s.reasons, []);
    assert.equal(s.pct, 99);
    assert.match(s.head.statusLine, /ready to submit · review plan .* · build 181/);
  });
  test("관리형 게시를 체크하기 전에는 닫혀 있다 — 제출마다 사람이", () => {
    const s = S.sheetModel(ctx({ choices: { platforms: { ios: true, android: true }, managed: false, listingFull: false, phased: true, nMode: "auto", typedN: "" } }));
    assert.equal(s.canSubmit, false);
    assert.deepEqual(codes(s), ["managed_unconfirmed"]);
    assert.equal(s.remaining[0].severity, "todo");
    assert.deepEqual(s.remaining[0].fix, { route: null, anchor: "#sheet-managed" });
    assert.equal(s.tone, "ok", "사람이 눌러야 하는 체크 하나는 «준비됨» 을 깨지 않는다(W5)");
  });
  test("E18 — Google Play 를 끄면 관리형 게시 줄이 사라진다", () => {
    const s = S.sheetModel(ctx({ choices: { platforms: { ios: true, android: false }, managed: false, listingFull: false, phased: true, nMode: "auto", typedN: "" } }));
    assert.deepEqual(codes(s), []);
    assert.equal(s.managedShown, false);
    assert.equal(s.canSubmit, true);
    assert.equal(s.submitBody.platform, "ios");
  });
  test("E17 — 플랜이 낡으면 닫히고 «플랜을 다시» 가 상태 화면을 가리킨다", () => {
    const stale = release({ review: { plan: { job_id: 651, state: "succeeded", age_seconds: 2400, stale: true,
      doc: { schema: 2, build_name: "1.1.1", n: 181, plan_verdict: "ok", ios: "ready", android: "ready" } }, result: null } });
    const s = S.sheetModel(ctx({ version: version({ release: stale }), release: stale }));
    assert.deepEqual(codes(s), ["plan_stale"]);
    assert.match(s.remaining[0].text, /stale/);
    assert.deepEqual(s.remaining[0].fix, { route: "#/store/app/status", anchor: "[data-plan-review]" });
    assert.equal(s.canSubmit, false);
  });
  test("E21 — 빌드 번호를 직접 입력하는데 틀리면 닫히고 그 칸을 가리킨다", () => {
    const typed = (v) => S.sheetModel(ctx({ choices: { platforms: { ios: true, android: true }, managed: true, listingFull: false, phased: true, nMode: "typed", typedN: v } }));
    assert.deepEqual(codes(typed("180")), ["n_mismatch"]);
    assert.match(typed("180").remaining[0].text, /181/);
    assert.deepEqual(typed("180").remaining[0].fix, { route: null, anchor: "#sheet-n" });
    assert.equal(typed("180").canSubmit, false);
    assert.equal(typed("181").canSubmit, true);
    assert.equal(typed("181").submitBody.confirm_build_number, "181");
  });
  test("E9 — 상한을 넘긴 칸은 그 칸을 가리키고 제출을 닫는다 (AC-D9)", () => {
    const s = S.sheetModel(ctx({ page: page([badField]) }));
    assert.deepEqual(codes(s), ["listing_bad"]);
    assert.deepEqual(s.remaining[0].fix, { route: null, anchor: "#f-ios-keywords" });
    assert.match(s.remaining[0].text, /Keywords .*101\/100/);
    assert.equal(s.canSubmit, false);
  });
  test("E11 — 프리셋이 listing_json 을 모르면 그 코드를 남은 것으로 되풀이한다", () => {
    const s = S.sheetModel(ctx({ refusal: "listing_json_unsupported" }));
    assert.deepEqual(codes(s), ["listing_unsupported"]);
    assert.match(s.remaining[0].text, /re-run the store-connect skill/);
    assert.equal(s.remaining[0].fix, null, "스킬을 다시 돌리는 일은 화면 안에 고치는 곳이 없다");
    assert.equal(s.canSubmit, false);
  });
  test("보내는 본문 — version_id · 버전 행의 이름 · 자동 빌드 번호 · 관리형 게시 확인", () => {
    const s = S.sheetModel(ctx());
    assert.deepEqual(s.submitBody, {
      version_id: 7, build_name: "1.1.1", ref: "main", mode: "submit", platform: "both",
      confirm_build_number: "auto", play_managed_publishing: "confirmed-on",
      listing: "notes-only", phased: "1",
    });
  });
  test("플랜의 이름이 달라도 본문의 이름은 **버전 행**의 것이다", () => {
    const other = release({
      plan: { job_id: 641, state: "succeeded", build_name: "9.9.9", age_seconds: 200, stale: false,
        doc: { schema: 1, build_name: "9.9.9", n: 181 } },
      review: { plan: { job_id: 651, state: "succeeded", age_seconds: 200, stale: false,
        doc: { schema: 2, build_name: "9.9.9", n: 181, plan_verdict: "ok", ios: "ready", android: "ready" } }, result: null },
    });
    const s = S.sheetModel(ctx({ version: version({ release: other }), release: other }));
    assert.equal(s.submitBody.build_name, "1.1.1");
    assert.ok(codes(s).includes("plan_missing"), "그 플랜은 이 버전의 것이 아니다");
  });
});

describe("§4.3 제출됨 · 만료", () => {
  test("제출되면 done · 막대는 100 · 제출 조건은 닫힌다", () => {
    const done = release({ review: { plan: release().review.plan, result: { job_id: 660, state: "succeeded", finished_at: iso(60),
      doc: { schema: 2, build_name: "1.1.1", overall_status: "submitted", platforms: { ios: { status: "submitted" } } } } } });
    const s = S.sheetModel(ctx({ version: version({ state: "submitted", release: done }), release: done }));
    assert.equal(s.tone, "done");
    assert.equal(s.closed, true);
    assert.equal(s.canSubmit, false);
    assert.equal(s.pct, 100);
    assert.match(s.head.statusLine, /^submitted .* · waiting for review$/);
    assert.equal(s.result.status, "submitted");
  });
  test("만료 경고가 붙은 드래프트는 회색이고 사람이 정리한다 (E13)", () => {
    const noBuild = release({ upload: null });
    const s = S.sheetModel(ctx({ version: version({ expired: true, expiry_warned: 1, release: noBuild }), release: noBuild }));
    assert.equal(s.tone, "expired");
    assert.equal(s.expired, true);
    assert.match(s.head.statusLine, /expired/);
  });
  test("폐기된 드래프트도 회색이고 제출 버튼이 없다", () => {
    const s = S.sheetModel(ctx({ version: version({ state: "discarded" }) }));
    assert.equal(s.tone, "expired");
    assert.equal(s.closed, true);
    assert.equal(s.canSubmit, false);
  });
});

describe("이전 버전과 달라진 것 (R12)", () => {
  test("서버가 준 diff 를 그대로 두 줄씩 그린다 · 여러 줄은 한 줄로 접는다", () => {
    const diff = { fields: [{ platform: "ios", key: "whats_new", old: "Bug fixes.", new: "New home.\nNew chat." }],
      screenshots: { ios: "same", android: "n/a" } };
    const s = S.sheetModel(ctx({ version: version({ diff: diff }) }));
    assert.equal(s.diff.changed, 1);
    assert.equal(s.diff.fields[0].oldText, "− whats_new: Bug fixes.");
    assert.equal(s.diff.fields[0].newText, "+ whats_new: New home. New chat.");
    assert.equal(s.diff.fields[0].id, "f-ios-whats_new");
    assert.deepEqual(s.diff.screenshots, [
      "App Store · iOS screenshots: same as the previous version",
      "Google Play · Android screenshots: the previous version's files are unknown",
    ]);
  });
  test("서버가 diff 를 안 줬으면 `listingDiff` 로 직접 센다 (두 벌을 만들지 않는다)", () => {
    const v = version({ diff: null, edited: { ios: { keywords: "a,b,z" } } });
    const s = S.sheetModel(ctx({ version: v }));
    assert.equal(s.diff.changed, 1);
    assert.deepEqual(S.listingDiff(v.prefill, v.edited).fields.map((f) => f.key), ["keywords"]);
  });
  test("되돌려 놓으면 «달라진 것이 없다» 고 말한다 (E10)", () => {
    const s = S.sheetModel(ctx());
    assert.equal(s.diff.changed, 0);
    assert.match(s.diff.noneText, /Nothing differs/);
    assert.ok(s.sending.includes("the previous version's copy, unchanged"), s.sending);
  });
});

describe("토큰 · 권한 · 요청 중", () => {
  test("토큰이 없거나 admin 이 아니면 닫히고 이유가 그대로 붙는다", () => {
    assert.deepEqual(S.sheetModel(ctx({ token: null })).reasons, ["no_token"]);
    assert.deepEqual(S.sheetModel(ctx({ admin: false })).reasons, ["admin"]);
    assert.equal(S.sheetModel(ctx({ busy: "submit" })).canSubmit, false);
    assert.ok(S.sheetModel(ctx({ token: null })).reasonText.length > 0);
  });
});

describe("versionRunning — 진행 중의 세 가지 (§15)", () => {
  test("행의 running · 도는 작업 · 이 버전의 회차", () => {
    assert.equal(S.versionRunning(version({ state: "running" }), noDriver(), null), true);
    assert.equal(S.versionRunning(version(), noDriver(), layersWith(10)), true);
    assert.equal(S.versionRunning(version({ release_id: 12 }), dm({ running: true }), null), true);
    assert.equal(S.versionRunning(version({ release_id: null }), dm({ running: true }), null), false);
    assert.equal(S.versionRunning(version(), noDriver(), { items: [], current: null }), false);
  });
});
