"use strict";
// 멈춤 · 조용함의 화면 쪽 (시나리오 C-45 ~ C-48 · C-70).
//
// 서버는 `estimate.stuck` 이 참일 때 그 **근거**를 `estimate.stuck_code` 로 함께 보낸다
// (`over_step` · `over_elapsed` · `no_output`). 화면은 근거마다 다른 문장을 쓴다.
//
// 잠그는 것:
// - 「지난 실행보다 n배 오래」는 **경과 초과**의 말이다. 단계가 오래 걸리는 것이 근거인데 그 말을
//   같이 그리면 두 사실이 섞여 하나도 안 읽힌다.
// - **「1배」가 절대 안 나온다.** 2026-09-15 화면에 「예상의 1배」를 찍은 것이 `floor(1044/1020)`
//   이다. 1배는 사실이지만 아무것도 말하지 않는다.
// - `quiet` 은 경보가 아니다 — `actionable` 이 false 이고 `ACTIONABLE` 에도 없다.
// - 서버가 `stuck` 과 `quiet` 을 같이 보내면 **stuck 이 이긴다**(경보를 먹는 쪽은 fail-open 이다).
// - 세 키(`quiet`·`stuck_code`·`step_expected_seconds`)를 **안 보내는 옛 서버 문서**도 그대로 읽는다.

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { load, fixture, job, fromNow, NOW } = require("./helpers");

const rcm = load();
const status = fixture("main");

function variant(id, patch) {
  const base = structuredClone(job(status, id));
  return Object.assign(base, patch);
}

/** 도는 잡 하나 — `estimate` 와 `progress` 를 시나리오가 덮어쓴다. */
function stalled(estimate, progress) {
  const v = variant(412, { reason: "stuck", state: "running" });
  Object.assign(v.estimate, { confidence: "overdue", overdue: true, stuck: true, finish_at: null }, estimate);
  if (progress) Object.assign(v.progress, progress);
  v.progress.last_output_at = fromNow(-473);
  return v;
}

describe("stuck_code 세 갈래 (C-45)", () => {
  // 하한(`max(배수 × 단계중앙값, no_output_seconds)`) 때문에 서버는 배수가 2 에 못 미치는
  // `over_step` 을 보낼 수 있다. 그때 「평소보다 1배 오래」가 나오면 2026-09-15 의
  // 「예상의 1배」가 자리만 옮겨 되살아난다 — 두 갈래가 같은 가드를 쓴다.
  test("over_step 의 배수가 2 에 못 미치면 배수를 아예 안 말한다", () => {
    for (const [stepSeconds, usual] of [[700, 540], [540, 540], [100, 540], [0.5, 540]]) {
      const v = stalled(
        { stuck_code: "over_step", elapsed_seconds: 3000, expected_seconds: 1080,
          step_expected_seconds: usual },
        { current_name: "build web", current_seconds: stepSeconds }
      );
      for (const lang of ["en", "ko"]) {
        const txt = rcm.reasonText(v, NOW, lang === "en" ? status : { ...status, lang: "ko" }).text;
        assert.ok(!/ 1× /.test(txt) && !/ 0× /.test(txt), `${lang} ${stepSeconds}s: ${txt}`);
        assert.ok(!/평소보다 [01]배/.test(txt), `${lang} ${stepSeconds}s: ${txt}`);
      }
      const en = rcm.reasonText(v, NOW, status);
      assert.ok(en.text.includes("step build web is past its usual time"), en.text);
      assert.equal(en.cls, "stuck");
    }
  });

  test("over_step → 단계 문장만. 「지난 실행보다 n배」는 안 붙는다", () => {
    const v = stalled(
      { stuck_code: "over_step", elapsed_seconds: 3000, expected_seconds: 1080, step_expected_seconds: 540 },
      { current_name: "build web", current_seconds: 2000 }
    );
    const en = rcm.reasonText(v, NOW, status);
    assert.equal(en.cls, "stuck");
    assert.equal(en.actionable, true);
    // Math.floor(2000 / 540) === 3
    assert.ok(en.text.includes("step build web is 3× its usual time"), en.text);
    assert.ok(!/longer than usual/.test(en.text), en.text);
    assert.ok(!/⚠/.test(en.text), en.text);

    const ko = rcm.reasonText(v, status, NOW, "ko").text;
    assert.ok(ko.includes("build web 단계가 평소보다 3배 오래"), ko);
    assert.ok(!ko.includes("지난 실행보다"), ko);
  });

  test("over_elapsed → 「지난 실행보다 n배 오래」만. 단계 문장은 안 붙는다", () => {
    const v = stalled(
      { stuck_code: "over_elapsed", elapsed_seconds: 4000, expected_seconds: 1080 },
      { current_name: "build web", current_seconds: 2000 }
    );
    const en = rcm.reasonText(v, NOW, status);
    // Math.floor(4000 / 1080) === 3
    assert.ok(en.text.includes("3× longer than usual"), en.text);
    assert.ok(!/its usual time/.test(en.text), en.text);
    assert.ok(!/⚠/.test(en.text), en.text);
  });

  test("no_output → 침묵만. 배수도 단계도 안 붙는다 (600/1800 은 0 배다)", () => {
    const v = stalled(
      { stuck_code: "no_output", elapsed_seconds: 600, expected_seconds: 1800 },
      { current_name: "build web", current_seconds: 400 }
    );
    const en = rcm.reasonText(v, NOW, status);
    assert.equal(en.text, "Not responding · no output for 7m");
    assert.ok(!/longer than usual|its usual time|⚠/.test(en.text), en.text);
  });

  test("over_step 인데 단계 정보가 모자라면 조각 자체가 없다 — 지어내지 않는다", () => {
    const noName = stalled(
      { stuck_code: "over_step", elapsed_seconds: 3000, expected_seconds: 1080, step_expected_seconds: 540 },
      { current_name: null, current_seconds: 2000 }
    );
    assert.equal(rcm.reasonText(noName, NOW, status).text, "Not responding · no output for 7m");

    const zero = stalled(
      { stuck_code: "over_step", elapsed_seconds: 3000, expected_seconds: 1080, step_expected_seconds: 0 },
      { current_name: "build web", current_seconds: 2000 }
    );
    const t = rcm.reasonText(zero, NOW, status).text;
    assert.equal(t, "Not responding · no output for 7m");
    assert.ok(!/Infinity|NaN/.test(t), t);
  });
});

describe("stuck_code 없는 옛 문서 (C-46)", () => {
  test("㉮ floor 가 1 이면 「1배」가 나오지 않는다 — 2026-09-15 의 그 자리다", () => {
    const v = stalled({ elapsed_seconds: 1044, expected_seconds: 1020 }, null);
    delete v.estimate.stuck_code;
    const en = rcm.reasonText(v, NOW, status);
    assert.equal(en.cls, "stuck");
    assert.ok(!/1×|1\s*배/.test(en.text), en.text);
    assert.equal(en.text, "Not responding · no output for 7m");
    const ko = rcm.reasonText(v, status, NOW, "ko").text;
    assert.ok(!/1배/.test(ko), ko);
  });

  test("㉯ floor 가 2 이면 붙는다", () => {
    const v = stalled({ elapsed_seconds: 3700, expected_seconds: 1800 }, null);
    delete v.estimate.stuck_code;
    assert.ok(rcm.reasonText(v, NOW, status).text.includes("2× longer than usual"));
  });

  test("㉰ expected 가 0 이면 조각이 없다 — Infinity · NaN 이 화면에 없다", () => {
    const v = stalled({ elapsed_seconds: 100, expected_seconds: 0 }, null);
    delete v.estimate.stuck_code;
    const r = rcm.reasonText(v, NOW, status);
    assert.equal(r.cls, "stuck");
    assert.ok(!/Infinity|NaN/.test(r.text), r.text);
    assert.equal(r.text, "Not responding · no output for 7m");
  });
});

describe("서버가 둘 다 보내면 stuck 이 이긴다 (C-48)", () => {
  test("reasonText · overallProgress 둘 다 stuck 을 고른다", () => {
    const v = stalled(
      { stuck: true, quiet: true, stuck_code: "over_step", elapsed_seconds: 3000,
        expected_seconds: 1080, step_expected_seconds: 540 },
      { current_name: "build web", current_seconds: 2000 }
    );
    assert.equal(rcm.reasonText(v, NOW, status).cls, "stuck");
    assert.equal(rcm.overallProgress(v).condition, "stuck");
    const ko = rcm.reasonText(v, status, NOW, "ko").text;
    assert.ok(!ko.includes("조용"), ko);
  });
});

describe("옛 문서도 그대로 읽는다 (C-70)", () => {
  // 세 키가 **전혀 없는** 상태 문서 — 오늘 서버가 내는 것 그대로.
  const legacy = fixture("main");

  test("모든 행에서 reasonText 가 문자열을 낸다 — 예외도 undefined 도 없다", () => {
    const q = legacy.pools[0].queue;
    assert.ok(q.length > 0);
    q.forEach((row) => {
      ["en", "ko"].forEach((lang) => {
        const r = rcm.reasonText(row, legacy, Date.parse(legacy.generated_at), lang);
        assert.equal(typeof r.text, "string", `${lang}/#${row.id}`);
        assert.ok(r.text.length > 0, `${lang}/#${row.id}`);
        assert.ok(!/undefined|NaN|\[object/.test(r.text), `${lang}/#${row.id}: ${r.text}`);
      });
    });
  });

  test("`est.quiet` 이 없으면 condition 이 quiet 이 되지 않는다", () => {
    legacy.pools[0].queue.forEach((row) => {
      const p = rcm.overallProgress(row);
      if (p) assert.notEqual(p.condition, "quiet", "#" + row.id);
    });
  });

  test("`reason: \"quiet\"` 을 모르는 옛 화면 경로는 unknown 으로 떨어진다 — 던지지 않는다", () => {
    // 반대 방향: 화면이 모르는 reason 값을 받았을 때. `default:` 가지가 그 자리다.
    const r = rcm.reasonText({ reason: "brand_new_reason", estimate: {} }, null, 0);
    assert.equal(r.text, "unknown");
    assert.equal(r.actionable, false);
  });
});

describe("조용한 잡은 「확인이 필요한 작업」에 안 뜬다 (C-47)", () => {
  test("ACTIONABLE 은 오늘과 글자까지 같다", () => {
    assert.deepEqual(rcm.ACTIONABLE,
      ["worker_down", "stuck", "upload_stalled", "not_scheduled", "blocked_by_group", "overdue", "paused"]);
  });

  test("notMoving 이 quiet 행을 빼고 stuck · overdue 만 순서대로 낸다", () => {
    const s = fixture("main");
    const base = job(s, 412);
    const mk = (id, reason, estimate) => Object.assign(structuredClone(base), {
      id: id, reason: reason,
      estimate: Object.assign({}, base.estimate, estimate)
    });
    s.pools[0].queue = [
      mk(401, "quiet", { quiet: true, stuck: false, overdue: false }),
      mk(402, "stuck", { stuck: true, stuck_code: "no_output", overdue: false }),
      mk(403, "overdue", { overdue: true, stuck: false, elapsed_seconds: 2000, expected_seconds: 1800 })
    ];
    const r = rcm.notMoving(s, null);
    assert.equal(r.kind, "list");
    assert.deepEqual(r.lines.map((l) => l.jobId), [402, 403]);
  });

  test("reasonText 의 quiet 가지는 경보가 아니다", () => {
    const r = rcm.reasonText({ reason: "quiet", estimate: { quiet: true } }, null, 0);
    assert.equal(r.actionable, false);
    assert.equal(r.cls, "quiet");
  });
});

describe("overallProgress — quiet 은 가장 약한 형편이다 (C-49)", () => {
  // 우선순위: preparing > stuck > over > finalizing > quiet > normal.
  const running = (estimate, progress) => ({
    id: 412, state: "running", started_at: fromNow(-900),
    estimate: Object.assign({ source: "measured" }, estimate),
    progress: progress || null
  });

  test("㉠ materializing 은 quiet 을 이긴다 → preparing", () => {
    const p = rcm.overallProgress(running({ quiet: true },
      { phase: "materializing", steps: [], steps_total: null, steps_done: 0 }));
    assert.equal(p.condition, "preparing");
  });

  test("㉡ stuck 은 quiet 을 이긴다 → stuck", () => {
    const p = rcm.overallProgress(running({ stuck: true, quiet: true, elapsed_seconds: 900, expected_seconds: 1800 }));
    assert.equal(p.condition, "stuck");
  });

  test("㉢ over 는 quiet 을 이긴다 → over", () => {
    const p = rcm.overallProgress(running({ overdue: true, quiet: true, elapsed_seconds: 2000, expected_seconds: 1800 }));
    assert.equal(p.condition, "over");
  });

  test("㉣ finalizing 은 quiet 을 이긴다 → finalizing", () => {
    const p = rcm.overallProgress(running({ quiet: true },
      { phase: "executing", steps_total: 4, steps_done: 4 }));
    assert.equal(p.condition, "finalizing");
  });

  test("㉤ 단계 눈금 + quiet → quiet. 눈금은 그대로 산다", () => {
    const p = rcm.overallProgress(running({ quiet: true },
      { phase: "executing", steps_total: 4, steps_done: 1 }));
    assert.equal(p.condition, "quiet");
    assert.equal(p.basis, "steps");
    assert.equal(p.pct, 25);
  });

  test("㉥ 시간 눈금 + quiet → quiet. 퍼센트가 null 이 되지 않는다", () => {
    const p = rcm.overallProgress(running({ quiet: true, elapsed_seconds: 900, expected_seconds: 1800 }));
    assert.equal(p.condition, "quiet");
    assert.equal(p.basis, "time");
    assert.equal(p.pct, 50);
  });

  test("막대가 형편을 말하되 화면 글자는 눈금만 말한다 — 두 언어", () => {
    // 「조용함」은 같은 행의 상태 칩과 상태 칸이 이미 말했다. 라벨까지 말하면 84px 칸에서 두 줄로
    // 접혀 그 행만 이웃보다 높아진다 — 화면에서는 막대의 색이, 글자로는 보조기기가 말한다.
    const row = running({ quiet: true, elapsed_seconds: 900, expected_seconds: 1800 });
    const plab = (h) => /class="plab" title="[^"]*">([^<]*)</.exec(h)[1];
    const spoken = (h) => /aria-valuetext="([^"]*)"/.exec(h)[1];

    const en = rcm.progressBarHtml(row, "en", false);
    assert.ok(en.includes('data-cond="quiet"'), en);
    assert.equal(plab(en), "50% · by measured time");
    assert.equal(spoken(en), "50% · by measured time · quiet");

    const ko = rcm.progressBarHtml(row, "ko", false);
    assert.equal(plab(ko), "50% · 지난 실행 기준");
    assert.equal(spoken(ko), "50% · 지난 실행 기준 · 조용함");
  });

  test("㉦ 응답 없음도 같다 — 화면 글자에 「응답 없음」이 없다", () => {
    const row = running({ stuck: true, elapsed_seconds: 900, expected_seconds: 1800 },
      { phase: "executing", steps_total: 49, steps_done: 20 });
    const ko = rcm.progressBarHtml(row, "ko", false);
    assert.ok(ko.includes('data-cond="stuck"'), ko);
    assert.equal(/class="plab" title="[^"]*">([^<]*)</.exec(ko)[1], "41% · 단계 20/49");
    assert.equal(/aria-valuetext="([^"]*)"/.exec(ko)[1], "41% · 단계 20/49 · 응답 없음");
  });
});
