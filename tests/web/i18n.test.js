"use strict";
// 문자열 카탈로그(M5d-1) — 오너 결정 36·37·38.
//
// 잠그는 것:
// - 두 언어의 키 집합이 같다. 한쪽에만 있는 키는 화면에서 언어를 바꾸는 순간 빈 칸이 된다.
// - 없는 키는 **던진다**. 조용히 `undefined` 를 그리면 브라우저 테스트에서야 걸린다.
// - 매개변수를 받는 값은 그 인자를 실제로 쓴다(자리표시자를 흘리지 않는다).
// - `#<id>` 토큰이 두 언어 모두에 남는다 — 이유 칸이 그 토큰을 문자열 치환으로 잡 링크로 바꾸기
//   때문에, 번역이 그것을 흘리면 링크가 **조용히** 사라진다(명세 §2 함정 1).
// - 순수 함수의 기본 언어는 영어다 — 오늘의 단언 300개가 그래서 그대로 산다.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const I18N = require(path.join(__dirname, "..", "..", "src", "remote_ci_monitor", "web", "i18n.js"));
const { load } = require("./helpers");
const rcm = load();

const LANGS = I18N.LANGS;

test("두 언어가 있고 기본은 한국어다 (결정 38)", () => {
  assert.deepEqual(LANGS.slice().sort(), ["en", "ko"]);
  assert.equal(I18N.DEFAULT_LANG, "ko");
});

test("키 집합이 두 언어에서 같다", () => {
  const en = Object.keys(I18N.MESSAGES.en).sort();
  const ko = Object.keys(I18N.MESSAGES.ko).sort();
  assert.deepEqual(ko, en, "한쪽에만 있는 키는 그 언어에서 빈 칸이 된다");
  assert.ok(en.length > 100, "카탈로그가 비어 있다");
});

test("같은 키는 두 언어에서 같은 종류다 (문자열이면 둘 다 문자열)", () => {
  Object.keys(I18N.MESSAGES.en).forEach((k) => {
    assert.equal(
      typeof I18N.MESSAGES.ko[k],
      typeof I18N.MESSAGES.en[k],
      `${k}: 한쪽만 함수면 인자를 받는 자리가 달라진다`
    );
  });
});

test("없는 키는 던진다 — 화면에 undefined 를 그리지 않는다", () => {
  assert.throws(() => I18N.t("ko", "nope.not.here"), /unknown key/);
  assert.throws(() => I18N.t("en", ""), /unknown key/);
});

test("모르는 언어는 기본 언어로 떨어진다", () => {
  assert.equal(I18N.normalize("fr"), "ko");
  assert.equal(I18N.normalize(null), "ko");
  assert.equal(I18N.normalize("en"), "en");
  assert.equal(I18N.t("fr", "state.running"), I18N.t("ko", "state.running"));
});

test("모든 키가 두 언어에서 비어 있지 않은 문자열을 만든다", () => {
  // 매개변수 있는 값에는 그럴듯한 인자를 준다. 여기 없는 인자는 자리표시자로 남아 아래에서 걸린다.
  const args = {
    lane: 1, busy: 2, lanes: 2, id: 412, dur: "5m 10s", job: "#409", group: "devices",
    bytes: "48 MB", since: "2m", ref: "main", over: "3m", expected: "6m", n: 3, by: "alice",
    kill: "in 8s", conf: "high", source: "preset", dash: "—", ordinal: "2nd", clock: "09:57",
    total: 5, running: 2, waiting: 3, state: "running", name: "build-02", pool: "linux",
    host: "macmini", cur: 2, soFar: true, code: 1, who: "alice", step: "test", size: "48 MB",
    blobs: 12, at: "2026-09-08 01:02:03Z", seconds: 61, limit: "512 MB", kind: "TarError",
    count: 3, detail: "boom", age: "3s ago", error: "database is locked", names: "build-02",
    labels: "bob@desk", hash: "9f8e", version: "0.2.3", uptime: "2m", schema: 1, cpu: "12%",
    mem: "56%", gpu: "4%", load: "3.5 / 10", shown: 5, json: "{}", text: "rcm run demo",
    key: "gate", wait: "5m", pct: "12%", user: 7, sys: 7, used: "13 GB", note: "no GPU",
    preset: "gate", tool: "fvm", sha: "1234567", repo: "app", member: "escape.txt",
    op: "git fetch", status: 503,  // F2b — 시작 전에 끝난 잡의 인자
    countdown: "in 8s", cores: 10, delta: "+2s", head: "pool linux",
    disk: "26%", free: "340 GB", percent: 62, done: 4,
    window: 8, seen: 3  // M5h — 이름별 실패 이력의 창과 본 횟수
  };
  LANGS.forEach((lang) => {
    Object.keys(I18N.MESSAGES[lang]).forEach((k) => {
      const out = I18N.t(lang, k, args);
      assert.equal(typeof out, "string", `${lang}/${k}`);
      assert.ok(out.length > 0, `${lang}/${k} 이 빈 문자열이다`);
      assert.ok(!/undefined|NaN|\[object/.test(out), `${lang}/${k}: ${out}`);
    });
  });
});

test("잡 링크 토큰 `#<id>` 가 두 언어에 남는다 (명세 §2 함정 1)", () => {
  // 이유 칸은 `#412` 를 문자열 치환으로 버튼으로 바꾼다. 없으면 replace 가 조용히 아무 일도 안 한다.
  ["reason.behind", "eta.after"].forEach((key) => {
    LANGS.forEach((lang) => {
      assert.match(I18N.t(lang, key, { id: 412 }), /#412/, `${lang}/${key}`);
    });
  });
  const row = { reason: "waiting_for_lane", ahead_job_id: 412, estimate: {} };
  LANGS.forEach((lang) => {
    const r = rcm.reasonText(row, null, 0, lang);
    assert.match(r.text, /#412/, lang);
    assert.deepEqual(r.links, [{ jobId: 412 }]);
  });
});

test("막힌 잡의 링크 토큰도 두 언어에 남는다", () => {
  const row = { reason: "blocked_by_group", blocked_by: { job_id: 409, group: "devices" }, estimate: {} };
  LANGS.forEach((lang) => {
    const r = rcm.reasonText(row, null, 0, lang);
    assert.match(r.text, /#409/, lang);
    assert.match(r.text, /devices/, lang + ": 그룹 이름은 번역하지 않는다");
  });
});

test("순수 함수의 기본 언어는 영어다 — 오늘의 단언이 그래서 산다", () => {
  const row = { reason: "running", lane: 1 };
  assert.equal(rcm.reasonText(row, null, 0).text, "running · lane 1");
  assert.equal(rcm.reasonText(row, null, 0, "en").text, "running · lane 1");
  assert.notEqual(rcm.reasonText(row, null, 0, "ko").text, "running · lane 1");
  assert.equal(rcm.stateWord("timed_out"), "timed out");
  assert.equal(rcm.ordinal(2), "2nd");
});

test("한국어 문면 — 상태 · 이유 · 큐 머리줄", () => {
  assert.equal(rcm.stateWord("running", "ko"), "실행 중");
  assert.equal(rcm.stateWord("timed_out", "ko"), "시간 초과");
  assert.equal(rcm.stateWord(null, "ko"), "알 수 없음");
  assert.equal(rcm.ordinal(2, "ko"), "2번째");
  assert.equal(rcm.reasonText({ reason: "worker_down" }, null, 0, "ko").text, "워커 없음");
  assert.equal(rcm.reasonText({ reason: "paused" }, null, 0, "ko").text, "일시정지");
});

test("식별자는 어느 언어에서도 번역하지 않는다", () => {
  const job = {
    state: "failed", exit_code: 1, preset: "gate", key: "gate:full",
    inputs: { scope: "full" }, source: { mode: "git_ref", ref: "main" }
  };
  // 재실행 명령은 셸에 붙여 넣는 것이라 언어와 무관하게 같아야 한다
  assert.equal(rcm.rerunCommand(job), "rcm run gate -f scope=full --ref main");
  LANGS.forEach((lang) => {
    const l = rcm.recentLine(job, "UTC", 0, lang);
    assert.equal(l.rerun, "rcm run gate -f scope=full --ref main", lang);
  });
});

test("서버가 코드로 말한 요약은 그 언어로, 잡이 찍은 문장은 그대로 (결정 37)", () => {
  const fromServer = { state: "lost", summary: "server restarted 2026-09-08 01:02:03Z",
    summary_code: "server_restarted", summary_args: { at: "2026-09-08 01:02:03Z" } };
  assert.equal(rcm.outcomeText(fromServer, "en"), "server restarted 2026-09-08 01:02:03Z");
  assert.equal(rcm.outcomeText(fromServer, "ko"), "2026-09-08 01:02:03Z 서버 재시작");

  const fromJob = { state: "failed", summary: "2 tests failed" };  // 코드 없음 = 팀이 쓴 문장
  LANGS.forEach((lang) => {
    assert.equal(rcm.outcomeText(fromJob, lang), "2 tests failed", lang + ": 팀 문장은 번역하지 않는다");
  });

  const unknownCode = { summary: "something new", summary_code: "not_in_catalogue", summary_args: {} };
  LANGS.forEach((lang) => {
    assert.equal(rcm.outcomeText(unknownCode, lang), "something new", lang + ": 모르는 코드는 원문으로");
  });
});

test("outcome 코드가 두 언어 모두에 있다 — 서버가 보내는 것을 화면이 다 그릴 수 있다", () => {
  // 서버의 core/outcome.CODES 와 짝이다. 하나라도 빠지면 그 요약이 영어로 남는다.
  // 이 목록은 손으로 적은 것이라 생산자를 못 본다 — `tool_missing` 이 두 로케일 모두에 없는 채로
  // M5j 부터 살아 있었고 여기서 안 걸렸다. **정본 잠금은 파이썬 쪽**이다(F2b):
  // tests/test_outcome.py::test_every_outcome_code_is_defined_once_in_each_locale_… 가
  // core/outcome.CODES 를 직접 읽는다. 여기는 그 잠금이 죽었을 때를 위한 두 번째 그물이다.
  const codes = [
    "cancelled_before_start", "cancelled_by", "server_restarted",
    "server_restarted_during_upload", "server_stopped_while_running", "upload_abandoned",
    "upload_interrupted", "snapshot_too_big", "snapshot_rejected", "snapshot_blobs_missing",
    "exit_code", "timed_out", "worker_error", "worker_failed",
    "worker_stopped_while_running", "worker_restarted_without_job", "worker_unreachable",
    "cancel_unconfirmed",
    // 프로세스가 뜨기 전에 끝난 잡(core/outcome.PREFLIGHT_CODES)
    "preset_missing", "tool_missing", "snapshot_missing", "blob_missing", "repo_missing",
    "commit_missing", "git_failed", "snapshot_download_failed", "launch_executable_missing",
    "launch_permission_denied", "launch_failed", "log_unavailable", "workspace_failed"
  ];
  codes.forEach((c) => {
    assert.ok(I18N.has("outcome." + c), "카탈로그에 outcome." + c + " 이 없다");
    // `has()` 는 영어만 본다 — KO 프로퍼티를 지우고 주석에 같은 문자열만 남겨도 통과한다.
    // 그래서 **두 언어에서 실제로 부른다**. 빈 문자열도, 자리표시자가 샌 것도 여기서 걸린다.
    LANGS.forEach((lang) => {
      assert.ok(
        Object.prototype.hasOwnProperty.call(I18N.MESSAGES[lang], "outcome." + c),
        lang + " 카탈로그에 outcome." + c + " 정의가 없다"
      );
    });
  });
});

// 서버가 저장한 영어 문장(core/outcome.py 의 PINNED)과 **글자까지 같아야** 한다: 저장된 문장과
// 화면이 코드로 다시 그린 문장이 다르면 CLI 와 브라우저가 같은 잡을 다르게 말한다(결정 37).
// 인자는 서버가 보내는 **원시 값** 그대로다 — 꼴을 만드는 일은 화면 몫이다.
const PREFLIGHT_EN = [
  ["preset_missing", { preset: "ok" }, "preset 'ok' is no longer configured"],
  ["tool_missing", { tool: "fvm" }, "required tool fvm is missing"],
  ["snapshot_missing", {}, "snapshot file is missing"],
  ["blob_missing", { sha: "1234567" }, "snapshot blob missing 1234567"],
  ["repo_missing", { repo: "app" }, "repo 'app' is no longer configured"],
  ["repo_missing", { repo: "app", where: "worker" },
    "repo 'app' is not configured on this worker"],
  ["commit_missing", { sha: "1234567" },
    "commit 1234567 not found after fetch (ref moved or was force-pushed?)"],
  ["git_failed", { op: "git fetch", kind: "timeout", seconds: 1 },
    "git fetch timed out after 1s"],
  ["git_failed", { op: "git fetch", kind: "timeout", seconds: 3600 },
    "git fetch timed out after 1h"],
  ["git_failed", { op: "git fetch", kind: "exit", code: 128 },
    "git fetch failed (exit 128), see the job log"],
  ["git_failed", { kind: "no_git" }, "git is not installed on the build machine"],
  ["git_failed", { op: "git init", kind: "spawn", error: "OSError" },
    "git init could not start git: OSError"],
  ["snapshot_download_failed", { status: 503 },
    "cannot download the snapshot from the server (HTTP 503)"],
  ["launch_executable_missing", {}, "the preset's command was not found"],
  ["launch_permission_denied", {}, "the preset's command is not executable"],
  ["launch_failed", { error: "OSError" },
    "the preset's command could not be started (OSError)"],
  ["log_unavailable", { error: "PermissionError" },
    "the job log file could not be opened (PermissionError)"],
  ["workspace_failed", { error: "OSError" }, "the workspace could not be prepared (OSError)"],
  ["snapshot_rejected", { kind: "escapes_workspace", member: "escape.txt" },
    "snapshot rejected: member escapes the workspace: escape.txt"],
  ["snapshot_rejected", { kind: "TarError" }, "snapshot rejected: TarError"]
];

test("시작 전에 끝난 잡의 영어 문장이 서버가 저장한 문장과 같다 (결정 37)", () => {
  PREFLIGHT_EN.forEach(([code, args, expected]) => {
    assert.equal(I18N.t("en", "outcome." + code, args), expected, code);
  });
});

test("같은 인자로 두 언어가 각각 자기 말을 만든다 — 한국어가 영어로 물러서지 않는다", () => {
  PREFLIGHT_EN.forEach(([code, args]) => {
    LANGS.forEach((lang) => {
      const out = I18N.t(lang, "outcome." + code, args);
      assert.equal(typeof out, "string", lang + "/" + code);
      assert.ok(out.length > 0, lang + "/" + code + " 이 비었다");
      assert.ok(!/undefined|NaN|\[object/.test(out), lang + "/" + code + ": " + out);
    });
    // 한국어 화면이 영어 문장을 그대로 보여 주면 그건 fallback 이지 번역이 아니다
    const ko = I18N.t("ko", "outcome." + code, args);
    const en = I18N.t("en", "outcome." + code, args);
    assert.notEqual(ko, en, "ko/" + code + " 이 영어와 같다 — KO 프로퍼티가 지워졌는가?");
  });
});

test("워커 상태와 오류 종류도 두 언어에 있다", () => {
  ["busy", "idle", "down"].forEach((s) => assert.ok(I18N.has("state." + s), s));
  ["internal_error", "database_unavailable", "sampler_failed"].forEach((c) =>
    assert.ok(I18N.has("error." + c), c));
  ["no_sampler", "no_gpu", "sampler_failed"].forEach((c) => assert.ok(I18N.has("gpu." + c), c));
});

test("소요 시간은 두 언어에서 같은 꼴이다 — 숫자 칸이 흔들리지 않게", () => {
  assert.equal(rcm.fmtDuration(310), "5m 10s");
  const row = { state: "queued", estimate: { waited_seconds: 310 } };
  assert.match(rcm.elapsedText(row, 0, "ko").main, /5m 10s/);
  assert.match(rcm.elapsedText(row, 0, "en").main, /5m 10s/);
});
