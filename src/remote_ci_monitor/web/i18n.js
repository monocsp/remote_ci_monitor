/* rcm queue — 문자열 카탈로그. 빌드 도구 없음(app.js 앞에 실린다).

   오너 결정 36·38: 화면은 **한국어가 기본**이고 오른쪽 위에서 영어를 고른다. 브라우저 언어와
   무관하다. 결정 37: 서버가 보내는 것은 코드이고, 문장은 여기서 만든다.

   번역하지 않는 것: 프리셋 이름 · 작업 키 · 요청자 라벨 · 커밋 해시 · ref · 저장소 주소 ·
   동시성 그룹 이름 · 단계 이름(작업이 찍은 것) · 로그 · `rcm run …` 명령 · 상태 enum 값 자체.

   소요 시간은 두 언어 모두 `5m 10s` 꼴로 둔다. 한국어로 「5분 10초」라고 쓰면 표 칸이 넓어져
   숫자 열이 흔들린다(조사: 숫자 칸은 tabular-nums 로 폭을 고정한다).

   값은 문자열이거나 `(args) -> 문자열` 함수다. 함수로 두면 어순을 언어마다 자유롭게 짤 수 있다 —
   영어의 「조각을 · 로 이어 붙이기」는 한국어에서 그대로 쓸 수 없다.

   **카탈로그는 글자만 담는다.** 태그도 이모지도 여기 없다 — 모양은 `app.js`/`index.html` 의
   렌더 쪽이 붙인다(`token.help` 의 `<code>` 하나가 유일한 예외다). 그래야 문구를 고치는 일과
   모양을 고치는 일이 서로 발밑을 안 뺀다.

   **한국어 문체는 합쇼체다.** 종결어미로 끝나는 문장은 `…니다` 로 끝난다. 명사형 조각
   (「진행 중」 · 「대기」 · 「응답 없음」 · 「머신이 바빠 대기 중」)은 종결어미가 없으므로
   이 규칙 밖이다 — 이유 칸에 들어가는 조각은 짧아야 해서 명사형으로 끝낸다. */
(function (root) {
  "use strict";

  var LANGS = ["ko", "en"];
  var DEFAULT_LANG = "ko"; // 결정 38 — 브라우저가 영어여도 기본은 한국어

  /* 서버가 보내는 **닫힌** 인자 값 → 그 언어의 낱말(core/outcome.py 의 REJECT_KINDS · git kind).
     서버가 문장을 만들어 보내면 화면이 다시 쓸 수 없어서, 코드도 인자도 열쇠로만 온다. 모르는
     열쇠는 그대로 찍는다 — 새 서버 + 옛 화면에서 빈칸이 되는 것보다 낫다. */
  var REJECT = {
    en: {
      absolute_path: "absolute path in archive",
      escapes_workspace: "member escapes the workspace",
      link_outside: "link points outside the workspace",
      absolute_link: "absolute link target in archive",
      special_file: "device or special file in archive",
      not_a_tarball: "not a valid tar.gz",
      unsupported_compression: "unsupported compression",
      truncated: "truncated archive",
      unreadable: "unreadable archive"
    },
    ko: {
      absolute_path: "아카이브에 절대 경로",
      escapes_workspace: "작업 공간 밖을 가리키는 항목",
      link_outside: "작업 공간 밖을 가리키는 링크",
      absolute_link: "아카이브에 절대 경로 링크",
      special_file: "아카이브에 장치·특수 파일",
      not_a_tarball: "tar.gz 가 아님",
      unsupported_compression: "지원하지 않는 압축",
      truncated: "잘린 아카이브",
      unreadable: "읽을 수 없는 아카이브"
    }
  };

  function rejected(lang, a) {
    var kind = REJECT[lang][a.kind] || a.kind;
    return a.member ? kind + ": " + a.member : kind;
  }

  /* 초 → `1s`·`5m`·`1h`. 서버의 `core/outcome._dur` 와 **글자까지 같아야** 한다: 서버가 저장한
     문장과 화면이 코드로 다시 그린 문장이 다르면 결정 37 의 불변식이 깨진다(CLI 와 브라우저가
     같은 잡을 다르게 말한다). 서버는 인자를 **원시 값**으로 보낸다 — 여기서 꼴을 만든다. */
  function dur(n) {
    var v = parseInt(n, 10);
    if (isNaN(v)) return "?";
    if (v >= 3600 && v % 3600 === 0) return (v / 3600) + "h";
    if (v >= 60 && v % 60 === 0) return (v / 60) + "m";
    return v + "s";
  }

  function gitFailed(lang, a) {
    var op = a.op || "git";
    if (a.kind === "no_git") {
      return lang === "ko" ? "빌드 머신에 git 이 없음" : "git is not installed on the build machine";
    }
    if (a.kind === "timeout") {
      return lang === "ko"
        ? op + " 가 " + dur(a.seconds) + " 만에 시간 초과"
        : op + " timed out after " + dur(a.seconds);
    }
    if (a.kind === "spawn") {
      return lang === "ko" ? op + " 를 띄우지 못함: " + a.error : op + " could not start git: " + a.error;
    }
    if (a.kind === "exit") {
      return lang === "ko"
        ? op + " 실패(종료 코드 " + a.code + ") — 잡 로그를 보라"
        : op + " failed (exit " + a.code + "), see the job log";
    }
    return lang === "ko" ? op + " 실패 — 작업 로그를 보라" : op + " failed, see the job log";
  }

  // ── 조사 ────────────────────────────────────────────────────────────────────
  // 「이(가)」 같은 괄호 표기를 화면에서 없앤다. 뒤에서부터 **판정할 수 있는 글자**를 찾아
  // 받침 유무를 정하고, 그 한 글자를 이름 뒤에 붙인다.
  //
  //  1. 아래 `JOSA_SKIP` 집합(공백 · 문장 부호 · 괄호 · 따옴표 · 하이픈)은 건너뛴다.
  //     이 집합 **밖의** 글자를 만나면 거기서 멈춘다 — `@` 는 집합 밖이라 `web@` 는 `@` 에서
  //     멈춰 규칙 5 로 떨어진다(`build-f@` 가 `f` 냐 `@` 냐로 갈리지 않게 못 박는다).
  //  2. 한글 음절(U+AC00–U+D7A3): `(code - 0xAC00) % 28 !== 0` 이면 받침 있음.
  //  3. 숫자: 읽는 소리로. 받침 **있음** `0 영 · 1 일 · 3 삼 · 6 육 · 7 칠 · 8 팔`,
  //     **없음** `2 이 · 4 사 · 5 오 · 9 구`.
  //  4. 라틴 글자(대소문자 무시): 글자 이름의 읽는 소리로. 받침 **있는 것은 `l`(엘) · `m`(엠) ·
  //     `n`(엔) · `r`(알) 넷뿐**이고 나머지 22개는 없다. `f` 는 「에프」의 끝 글자 `프` 에
  //     받침이 없어 **받침 없는 쪽**이다(`perf가` · `mail이`).
  //  5. 그 밖(한자 · 이모지 · 빈 문자열 · 전부 건너뛴 경우): `null` → 받침 없는 쪽(가/를/는/와).
  //     「이(가)」로 되돌아가지 않는다 — 괄호를 없애는 것이 이 규칙의 목적이다.
  var JOSA_SKIP = /[\s.,!?;:'"`’”\)\]\}\(\[\{…·\-_]/;
  var JOSA_DIGIT = { "0": true, "1": true, "2": false, "3": true, "4": false,
    "5": false, "6": true, "7": true, "8": true, "9": false };
  var JOSA_LATIN = { l: true, m: true, n: true, r: true };
  // 각 쌍은 [받침 있는 쪽, 없는 쪽]. 「와/과」만 글자 순서가 뒤집혀 있다(받침 있으면 「과」).
  var JOSA_PAIRS = {
    "이/가": ["이", "가"],
    "을/를": ["을", "를"],
    "은/는": ["은", "는"],
    "와/과": ["과", "와"]
  };

  /** 마지막 글자에 받침이 있는가. 모르면 `null`(호출자가 받침 없는 쪽으로 읽는다). 안 던진다. */
  function hasFinalConsonant(word) {
    if (word === null || word === undefined) return null;
    var s = String(word);
    for (var i = s.length - 1; i >= 0; i--) {
      var ch = s.charAt(i);
      if (JOSA_SKIP.test(ch)) continue;
      var code = s.charCodeAt(i);
      if (code >= 0xAC00 && code <= 0xD7A3) return (code - 0xAC00) % 28 !== 0;
      if (ch >= "0" && ch <= "9") return JOSA_DIGIT[ch];
      var low = ch.toLowerCase();
      if (low >= "a" && low <= "z") return JOSA_LATIN[low] === true;
      return null;   // 판정할 수 없는 글자 — 건너뛰지 않고 여기서 멈춘다(규칙 5)
    }
    return null;     // 전부 건너뛰었다
  }

  /** 이름 뒤에 붙일 조사 하나. `pair` 는 "이/가" · "을/를" · "은/는" · "와/과". */
  function josa(word, pair) {
    var p = Object.prototype.hasOwnProperty.call(JOSA_PAIRS, pair) ? JOSA_PAIRS[pair] : null;
    if (!p) throw new Error("josa: unknown pair " + pair);
    return hasFinalConsonant(word) ? p[0] : p[1];
  }

  /** 이름 + 조사. 문장 쪽은 이것만 쓴다.
   *
   * 모르는 값은 **이름 부분을 빈 문자열로** 둔다 — `String(null)` 을 타면 끝 글자 `l` 이
   * 받침 있음이라 화면에 「null이」가 찍힌다. 화면에 넣을 대체값(`—`)은 호출자가 이미 고른다. */
  function withJosa(word, pair) {
    var name = (word === null || word === undefined || word === "") ? "" : String(word);
    return name + josa(word, pair);
  }

  var EN = {
    // ── 화면 층(app.js) ─────────────────────────────────────────────────────
    "token.checking": "checking…",
    "token.rejected": "Token rejected",
    "token.rejected_hint": "Token rejected — paste a new one",
    "token.ok": function (a) { return "ok · " + a.name + (a.admin ? " (admin)" : ""); },
    "token.unverified": "token (unverified)",
    "token.read_auth": "Read auth required",
    "token.bad_button": "Token rejected",
    "token.named": function (a) { return a.name; },
    "token.kept": "couldn’t verify — kept",
    "conn.live": function (a) { return "live · updated " + a.age; },
    "conn.polling": function (a) { return "polling · updated " + a.age; },
    "conn.lost": function (a) { return "lost · last update " + a.age; },
    "conn.paused": "paused · resume",
    "conn.lost_banner": function (a) {
      return "Lost connection to " + a.host + " · last update " + a.age + " · showing last known state";
    },
    "conn.reconnecting": function (a) { return "reconnecting " + a.countdown + "…"; },
    "conn.polling_every": "polling every 10s…",
    "header.lanes_busy": function (a) { return "lanes " + a.busy + "/" + a.lanes + " busy"; },
    "header.error": function (a) { return "error · " + a.detail; },
    "header.clock_unknown": "clock unknown",
    "header.clock_skew": function (a) { return "clock " + a.delta; },
    "header.paused": function (a) {
      return "Queue paused by " + a.by + " at " + a.clock
        + " — running jobs finish, nothing new starts · ";
    },
    "header.worker_stopped": function (a) {
      return "Worker on lane " + a.lanes + " stopped: " + a.error + " · ";
    },
    "header.lanes_left": function (a) { return "waiting jobs use lane " + a.lanes + " only"; },
    "header.nothing_can_start": "nothing can start",
    "header.worker_unreachable": function (a) {
      return "Worker " + a.names + " unreachable — no heartbeat · its running jobs are marked lost";
    },
    "header.not_scheduled": function (a) {
      return "Lane is idle but #" + a.id + " has not started for " + a.dur + " — check the server log";
    },
    "footer.server": function (a) {
      return "rcm " + a.version + " · up " + a.uptime + " · schema v" + a.schema;
    },
    "summary.add_token": "Add a token to highlight your jobs",
    "summary.queue_unknown": "unknown — queue unavailable",
    "summary.no_jobs": "No jobs of yours in the queue",
    "summary.and_more": function (a) { return "and " + a.n + " more"; },
    "summary.last_known": "last known: ",
    "summary.nothing_stuck": "Everything is fine",
    "summary.nothing_else_stuck": "everything else is fine",
    "summary.host_unavailable": "host: unavailable",
    "summary.host_no_sample": "host: no sample yet",
    "summary.host_sampled": function (a) { return "Host pressure · sampled " + a.age; },
    "summary.verdict_fine": "fine",
    "summary.verdict_busy": "busy",
    "summary.verdict_partial": "partial",
    "summary.verdict_unknown": "unknown",
    "summary.pressure": function (a) {
      return "CPU " + a.cpu + " · Mem " + a.mem + " · Disk " + a.disk + " · GPU " + a.gpu;
    },
    "summary.disk_free": function (a) { return a.free + " free"; },
    "summary.load": function (a) { return "load " + a.load; },
    "queue.no_status": "Queue unavailable — no status yet",
    "queue.unavailable": function (a) { return "Queue unavailable — " + a.error; },
    "queue.empty_other_pools": "Queue is empty here — jobs wait in other pools.",
    "queue.empty_paused": "Queue is empty but paused — nothing will start.",
    "queue.empty": "Queue is empty — ",
    "queue.empty_hint": " starts immediately.",
    "queue.presets": function (a) { return "presets: " + a.names; },
    "queue.more": function (a) { return "and " + a.n + " more"; },
    "queue.group_running": function (a) { return "Running now (" + a.n + ")"; },
    "queue.group_waiting": function (a) { return "Waiting (" + a.n + ")"; },
    "queue.group_none_running": "Nothing is running right now.",
    "queue.group_none_waiting": "Nothing is waiting.",
    "queue.col_job": "Job",
    "queue.col_key": "Job name",
    "queue.col_requester": "Requested by",
    "queue.col_reason": "Status",
    "queue.col_elapsed": "Elapsed",
    "queue.col_eta": "Finishes",
    "queue.col_source": "Source",
    "queue.pool_jobs": function (a) { return a.head + " · " + a.n + " job" + (a.n === 1 ? "" : "s"); },
    "row.group": function (a) { return "group " + a.name; },
    "row.you": "you",
    "row.you_joined": "you joined",
    "row.also_waiting": function (a) { return "also waiting: " + a.labels; },
    "row.token_of": function (a) { return "token: " + a.name; },
    "row.stalled_note": "will be cancelled by the server if it stays stalled",
    "row.cancel_requested": "cancel requested…",
    "row.cancel": "Cancel",
    "row.log": "Log",
    "row.add_token_for_log": "Add a token to see the log",
    "row.not_your_job": "not your job",
    "row.cancel_locked": "cancelling needs the submission’s cancel token — run rcm cancel in the session that submitted, or use an admin token",
    "row.others_waiting": function (a) {
      return a.n + " other session" + (a.n > 1 ? "s are" : " is") + " waiting on this job";
    },
    "row.ref": function (a) { return "ref " + a.ref; },
    "row.not_received": "not received yet",
    "row.uncommitted": "uncommitted",
    "row.tree": function (a) { return "tree " + a.hash; },
    "row.collapse": "collapse",
    "row.collapse_job": function (a) { return "collapse details for #" + a.id; },
    "row.expand_job": function (a) { return "expand details for #" + a.id; },
    "row.copy": "copy",
    "row.expand": "expand",
    "host.unavailable": function (a) { return "Host unavailable — " + a.error; },
    "host.no_sample": "host: no sample yet",
    "host.digest": function (a) { return a.names + " · " + a.state; },
    "host.window": "5 min",
    "host.sampled": function (a) { return "sampled " + a.age; },
    "host.cores_load": function (a) { return a.cores + " cores · load " + a.load; },
    "host.stale": function (a) { return "stale " + a.dur; },
    "host.cpu": function (a) { return "CPU " + a.pct; },
    "host.cpu_detail": function (a) { return "user " + a.user + " · sys " + a.sys; },
    "host.memory": function (a) { return "Memory " + a.used + " / " + a.total; },
    "host.disk": function (a) { return "Disk " + a.used + " / " + a.total; },
    "host.disk_free": function (a) { return a.free + " free"; },
    "host.job_storage": function (a) { return "rcm data " + a.used + (a.limit ? " of " + a.limit : "") + (a.measured ? " · measured " + a.measured : "") + (a.next ? " · next sweep " + a.next : ""); },
    "host.job_storage_unknown": function () { return "rcm data: size could not be measured"; },
    "host.compressed": function (a) { return "comp " + a.size; },
    "host.gpu": function (a) { return "GPU " + a.pct + " busy"; },
    "host.gpu_used": function (a) { return a.size + " in use"; },
    "host.gpu_none": function (a) { return "GPU — " + a.note; },
    "host.last_known": "last known",
    "host.top": "top: ",
    "est.unavailable": function (a) { return "Estimates · " + a.error; },
    "est.medians_unavailable": "medians unavailable",
    "est.no_samples": "Estimates · no samples yet — using preset/default until 2 successful jobs per key",
    "est.keys": function (a) { return "Estimates · " + a.n + " key" + (a.n > 1 ? "s" : "") + " · how ETA is computed"; },
    "est.preset": function (a) { return "preset " + a.dur; },
    "est.default": "default",
    "est.no_presets": "no presets",
    "est.to_preset": function (a) { return "n=" + a.n + " · using preset"; },
    "est.row": function (a) { return "wait " + a.wait + " · n=" + a.n + " · " + a.conf; },
    "est.how": "measured n≥5: high · n<5: med · preset or default: low",
    "toast.not_found": function (a) { return "#" + a.id + " not found"; },
    "toast.finished": function (a) { return "#" + a.id + " " + a.state + " · finished " + a.clock; },
    "toast.lookup_failed": function (a) { return "#" + a.id + " — could not look up"; },
    "toast.copied": function (a) { return "copied: " + a.text; },
    "toast.inputs": function (a) { return "#" + a.id + " inputs: " + a.json; },
    "toast.left_join": function (a) { return "left the join list of #" + a.id; },
    "toast.cancel_failed": function (a) { return "cancel failed: " + a.detail; },
    "toast.cancel_network": "cancel failed — network",
    "toast.cancel_requested": "cancel requested",
    "drawer.live": function (a) { return "live · " + a.bytes; },
    "drawer.finished": function (a) { return "finished · " + a.bytes; },
    "drawer.load_failed": "couldn’t load — retrying",
    "cancel.confirm_title": function (a) { return "Cancel #" + a.id + " " + a.key + " (" + a.who + ")?"; },
    "cancel.joiner_body": function (a) {
      return "You joined this job. You will leave the join list; the job keeps running for " + a.who + ".";
    },
    "cancel.running_body": "SIGTERM now, SIGKILL after the grace period.",
    "cancel.queued_body": "Removed from the queue; sessions waiting on it get exit 2. Run rcm run again to resubmit.",
    "cancel.cannot_undo": "Cannot be undone.",
    "cancel.others_waiting": function (a) {
      return a.n + " other session" + (a.n > 1 ? "s are" : " is") + " waiting on it.";
    },
    "cancel.leave": "Leave",
    // ── 정적 화면(index.html 의 data-i18n) ───────────────────────────────────
    "page.title": "rcm queue",
    "conn.lost_short": "Lost connection",
    "conn.pause_hint": "Click to pause updates",
    "conn.connecting": "connecting…",
    "lang.hint": "Change language",
    "lang.button": "한국어",
    "token.add": "add token",
    "token.title": "Client token",
    "token.help": "Paste the token from <code>rcm token add</code>. It stays in this browser only and unlocks your jobs, log tails, logs and cancel.",
    "token.field": "token",
    "token.forget": "Forget",
    "dialog.close": "Close",
    "dialog.save": "Save",
    "cancel.title": "Cancel job?",
    "cancel.keep": "Keep",
    "cancel.go": "Cancel job",
    "drawer.title": "Log",
    "drawer.job": function (a) { return "#" + a.id + (a.key ? " " + a.key : "") + " · log"; },
    "drawer.close": "Close log",
    "section.summary": "Summary",
    "section.queue": "Queue",
    "section.host": "Host",
    "section.recent": "Recent",
    "summary.yours": "Your jobs",
    "summary.stuck": "Needs a look",
    "summary.host": "Host pressure",
    "est.title": "Estimates",
    // ── 상태 ────────────────────────────────────────────────────────────────
    // 회귀 방지: **`state.quiet` 키를 만들지 않는다.** `quiet`(조용함)은 잡 상태가 아니라
    // 표시 사유이고, 키는 `reason.quiet` · `pbar.quiet` 두 개뿐이다.
    // `tests/web/state_marks.test.js` 가 `state.*` 에서 워커 상태를 뺀 집합을 잡 상태 아홉 개와
    // `deepEqual` 로 비교한다 — 여기에 상태 아닌 것을 하나라도 더하면 즉사한다.
    "state.running": "running",
    "state.queued": "queued",
    "state.uploading": "uploading",
    "state.cancelling": "cancelling",
    "state.succeeded": "succeeded",
    "state.failed": "failed",
    "state.timed_out": "timed out",
    "state.cancelled": "cancelled",
    "state.lost": "lost",
    "state.unknown": "unknown",
    "state.busy": "busy",
    "state.idle": "idle",
    "state.down": "down",
    "state.held": "held",
    "reason.held_by_load": "held — the machine is busy",
    "hold.cpu_busy": function (a) { return "cpu " + a.cpu + "%"; },
    "hold.no_sample": "no host sample",
    "hold.cooldown": "cooling down",
    "time.ago": function (a) { return a.dur + " ago"; },
    "time.in": function (a) { return "in " + a.dur; },
    "time.now": "now",

    // ── 이유 칸 ─────────────────────────────────────────────────────────────
    "reason.unknown": "unknown",
    "reason.running": "running",
    "reason.running_lane": function (a) { return "running · lane " + a.lane; },
    "reason.waiting_for_lane": "waiting for lane",
    "reason.lanes_busy": function (a) { return a.busy + "/" + a.lanes + " busy"; },
    "reason.behind": function (a) { return "behind #" + a.id; },
    "reason.frees_in": function (a) { return "frees in " + a.dur; },
    "reason.blocked_by": function (a) { return "blocked by " + a.job + " · " + a.group; },
    "reason.uploading": function (a) { return "uploading · " + a.bytes; },
    "reason.upload_stalled": function (a) { return "upload stalled " + a.since + " · " + a.bytes; },
    "reason.preparing": "preparing workspace",
    "reason.fetching": function (a) { return "fetching " + a.ref; },
    "reason.unpacking": function (a) { return "unpacking " + a.bytes; },
    "reason.over_by": function (a) { return "over by " + a.over + " · expected " + a.expected; },
    "reason.stuck": "Not responding",
    "reason.quiet": "output has gone quiet",
    "reason.step_over": function (a) { return "step " + a.step + " is " + a.n + "× its usual time"; },
    "reason.step_slow": function (a) { return "step " + a.step + " is past its usual time"; },
    "reason.times_expected": function (a) { return a.n + "× longer than usual"; },
    "reason.no_output_for": function (a) { return "no output for " + a.since; },
    "reason.sigterm": function (a) { return "SIGTERM sent by " + a.by + " · kill " + a.kill; },
    "reason.paused": "paused",
    "reason.not_scheduled": "not scheduled",
    "reason.no_worker": "no worker",

    // ── 추정 ────────────────────────────────────────────────────────────────
    "conf.high": "high",
    "conf.med": "med",
    "conf.low": "low",
    "conf.overdue": "overdue",
    "conf.group_wait": "low · group wait",
    "conf.measured": function (a) { return a.conf + " · measured n=" + a.n; },
    "conf.source": function (a) { return "low · " + a.source; },
    "conf.low_dash": function (a) { return "low · " + a.dash; },
    "eta.after": function (a) { return "after #" + a.id; },
    "eta.in": function (a) { return "in " + a.dur; },
    "eta.overdue": "overdue",
    "elapsed.waited": function (a) { return "waited " + a.dur; },
    "elapsed.waiting": function (a) { return "waiting " + a.dur; },
    "ordinal": function (a) { return a.n + ordinalSuffix(a.n); },
    "ordinal.in_line": function (a) { return a.ordinal + " in line"; },

    // ── 큐 머리줄 · 요약 ─────────────────────────────────────────────────────
    "queue.unknown": "unknown",
    "queue.counts": function (a) {
      return a.total + " jobs · " + a.running + " running · " + a.waiting + " waiting";
    },
    "queue.oldest_waiting": function (a) { return "oldest waiting " + a.dur; },
    "queue.lanes_busy": function (a) { return "lanes " + a.busy + "/" + a.lanes + " busy"; },
    "your.eta": function (a) { return a.state + " · ETA " + a.clock; },
    "your.in_line": function (a) { return a.ordinal + " · ETA " + a.clock; },
    "your.joined": function (a) { return "+" + a.n + " joined"; },

    // ── 워커 · 풀 ───────────────────────────────────────────────────────────
    "worker.state": function (a) { return "worker " + a.state; },
    "worker.lane_state": function (a) { return "lane " + a.lane + " · " + a.state; },
    "worker.paused": "paused",
    "pool.name": function (a) { return "pool " + a.name; },
    "pool.no_workers": "no workers",
    "host.pool": function (a) { return a.host + " · pool " + a.pool; },

    // ── 머리줄 알림 ─────────────────────────────────────────────────────────
    "note.stale_ui": "UI out of date — reload",
    "note.restarted": function (a) {
      return "Server restarted at " + a.clock + " — running jobs were marked lost";
    },

    // ── 진행 ────────────────────────────────────────────────────────────────
    "progress.no_markers": function (a) { return "no step markers · job " + a.dur; },
    "progress.step": function (a) {
      return "step " + a.cur + "/" + a.total + (a.soFar ? " (so far)" : "");
    },
    "progress.job": function (a) { return "job " + a.dur; },
    "progress.steps_failed": function (a) {
      return a.n + " step" + (a.n > 1 ? "s" : "") + " failed";
    },
    "progress.timing_note": "step times are server receive times (as_received)",
    "progress.now": function (a) { return "step " + a.cur + "/" + a.total + " " + a.step; },
    // 전체 진행 막대 — 퍼센트 옆에 **무엇으로 셌는지**를 늘 붙인다. 시간 눈금은 그 추정이
    // 어디서 왔는지까지 밝힌다(측정인가 프리셋인가) — 막대는 길이로만 말하기 때문이다.
    "pbar.steps": function (a) { return a.percent + "% · " + a.done + "/" + a.total + " steps"; },
    "pbar.steps_all": function (a) { return a.done + "/" + a.total + " steps"; },
    "pbar.time": function (a) { return a.percent + "% · by expected time"; },
    "pbar.time_measured": function (a) { return a.percent + "% · by measured time"; },
    "pbar.time_preset": function (a) { return a.percent + "% · by preset estimate"; },
    "pbar.over": "past the estimate",
    "pbar.stuck": "not responding",
    "pbar.quiet": "quiet",
    "pbar.preparing": "preparing workspace",
    "pbar.finalizing": "finalizing",
    "pbar.none": "progress —",
    "pbar.aria": function (a) { return "#" + a.id + " progress"; },

    // ── 최근 결과 ───────────────────────────────────────────────────────────
    "recent.succeeded": "succeeded",
    "recent.failed": "failed",
    "recent.failed_exit": function (a) { return "failed · exit " + a.code; },
    "recent.cancelled": "cancelled · exit 2",
    "recent.timed_out": "timed out · exit 2",
    "recent.lost": "lost · exit 3",
    "recent.before_start": "before start",
    "recent.by": function (a) { return "by " + a.who; },
    "recent.step": function (a) { return "step " + a.step; },
    "recent.last_step": function (a) { return "last step " + a.step; },
    "failures.title": "failed by name",
    "failures.persistent": function (a) { return "every one of the last " + a.window + " runs"; },
    "failures.intermittent": function (a) { return a.seen + " of the last " + a.window + " runs · intermittent?"; },
    "failures.first_seen": function (a) { return "first time in the last " + a.window + " runs"; },
    "failures.unknown": function (a) { return a.seen + " of " + a.window + " runs so far"; },
    "failures.unnamed": function (a) { return a.n + " of those runs failed without naming anything"; },
    "failures.more": function (a) { return "… and " + a.n + " more (see the log)"; },
    "failures.step_kind": "step",


    // ── 잡 산출물(M5e) — 모르는 수는 —, 0 은 「모았는데 없었다」일 때만 ────────
    "art.label": "artifacts",
    "art.state.ready": "ready",
    "art.state.empty": "nothing collected",
    "art.state.dropped": "dropped",
    "art.state.failed": "collection failed",
    "art.state.skipped": "not collected",
    "art.state.purged": "fetched and removed",
    "art.state.expired": "expired",
    "art.state.unavailable": "unavailable",
    "art.state.unknown": "unknown",
    "art.state.collecting": "collecting…",
    "art.state.uploading": "uploading…",
    "art.files": function (a) { return a.n + (a.n === 1 ? " file" : " files"); },
    "art.left": function (a) { return a.dur + " left"; },
    "art.reason.over_bytes": "over the size limit",
    "art.reason.over_files": "over the file limit",
    "art.reason.no_match": "nothing matched",
    "art.reason.path_conflict": "colliding paths",
    "art.reason.unsafe_path": "unsafe path",
    "art.reason.collect_failed": "collection error",
    "art.reason.upload_failed": "upload error",
    "art.reason.storage_full": "server storage full",
    "art.reason.timed_out": "collection timed out",
    "art.reason.cancelled": "cancelled",
    "art.reason.not_run": "the job never started",
    "art.reason.interrupted": "interrupted",
    "art.reason.acked": "fetched",
    "art.reason.expired": "expired",
    "art.copy": "copy the fetch command",
    "recent.failed_step": "failed step: ",
    "recent.last_step_label": "last step: ",
    "recent.none": "No completed jobs yet",
    "recent.unavailable": function (a) { return "Recent unavailable — " + a.error; },
    "recent.count": function (a) { return "last " + a.shown + " of " + a.total; },
    "recent.show_more": function (a) { return "show " + a.n + " more"; },
    "recent.show_fewer": "show fewer",

    // ── 우선순위 · 캐시 · 전이 ───────────────────────────────────────────────
    "priority.high": "high",
    "priority.low": "low",
    "cache.text": function (a) { return "cache " + a.blobs + " blobs · " + a.size; },
    "transitions.waited": function (a) { return "(waited " + a.dur + ")"; },
    "transitions.exit": function (a) { return "exit " + a.code; },

    // ── 서버가 만든 요약(결정 37) ────────────────────────────────────────────
    "outcome.cancelled_before_start": "cancelled before start",
    "outcome.cancelled_by": function (a) { return "cancelled by " + a.by; },
    "outcome.server_restarted": function (a) { return "server restarted " + a.at; },
    "outcome.server_restarted_during_upload": "server restarted during upload",
    "outcome.server_stopped_while_running": "server stopped while running",
    "outcome.upload_abandoned": function (a) { return "upload abandoned after " + a.seconds; },
    "outcome.upload_interrupted": function (a) { return "upload interrupted after " + a.bytes; },
    "outcome.snapshot_too_big": function (a) {
      return "snapshot " + a.bytes + " exceeds " + a.limit;
    },
    "outcome.snapshot_rejected": function (a) { return "snapshot rejected: " + rejected("en", a); },
    "outcome.snapshot_blobs_missing": function (a) {
      return "snapshot rejected: " + a.count + " blob(s) missing in upload";
    },
    "outcome.exit_code": function (a) { return "exit " + a.code; },
    "outcome.timed_out": function (a) { return a.limit; },
    // ── 프로세스가 뜨기 전에 끝난 잡 (core/outcome.PREFLIGHT_CODES) ─────────
    "outcome.preset_missing": function (a) {
      return "preset '" + a.preset + "' is no longer configured";
    },
    "outcome.tool_missing": function (a) { return "required tool " + a.tool + " is missing"; },
    "outcome.snapshot_missing": "snapshot file is missing",
    "outcome.blob_missing": function (a) { return "snapshot blob missing " + a.sha; },
    "outcome.repo_missing": function (a) {
      return a.where === "worker"
        ? "repo '" + a.repo + "' is not configured on this worker"
        : "repo '" + a.repo + "' is no longer configured";
    },
    "outcome.commit_missing": function (a) {
      return "commit " + a.sha + " not found after fetch (ref moved or was force-pushed?)";
    },
    "outcome.git_failed": function (a) { return gitFailed("en", a); },
    "outcome.snapshot_download_failed": function (a) {
      return "cannot download the snapshot from the server" + (a.status ? " (HTTP " + a.status + ")" : "");
    },
    "outcome.launch_executable_missing": "the preset's command was not found",
    "outcome.launch_permission_denied": "the preset's command is not executable",
    "outcome.launch_failed": function (a) {
      return "the preset's command could not be started (" + a.error + ")";
    },
    "outcome.log_unavailable": function (a) {
      return "the job log file could not be opened (" + a.error + ")";
    },
    "outcome.workspace_failed": function (a) {
      return "the workspace could not be prepared" + (a.error ? " (" + a.error + ")" : "");
    },
    "outcome.worker_error": function (a) { return "worker error: " + a.detail; },
    "outcome.worker_failed": "failed on the worker",
    "outcome.worker_stopped_while_running": "worker stopped while running",
    "outcome.worker_restarted_without_job": function (a) {
      return "worker " + a.name + " restarted without the job";
    },
    "outcome.worker_unreachable": function (a) {
      return "worker " + a.name + " unreachable for " + a.seconds + "s";
    },
    "outcome.cancel_unconfirmed": "worker did not confirm the cancel",

    // ── 오류 종류(결정 37) ──────────────────────────────────────────────────
    "error.internal_error": "internal error",
    "error.database_unavailable": "database unavailable",
    "error.sampler_failed": "sampler failed",
    "gpu.no_sampler": "no GPU sampler",
    "gpu.no_gpu": "no GPU",
    "gpu.sampler_failed": "GPU sampler failed",

    /* ── 스토어 탭 (docs/wireframes/web-store.html) — 머리 탭 · 설정 관문 · 접히는 행 넷 ── */
    "nav.queue": "Queue",
    "nav.store": "Store",
    "nav.repo": "repository",
    "store.heading": function (a) { return "Store · " + a.repo; },
    "store.loading": "Loading…",
    "store.none": "No repository on this server has a release profile.",
    "store.unknown_repo": function (a) { return a.repo + " has no release profile on this server."; },
    "store.load_failed": function (a) { return "Could not load " + a.repo + ": " + a.detail; },
    "store.updated": function (a) { return "updated " + a.age; },
    "store.refresh": "Refresh",
    "store.policy": "Approval does not release. This page has no release, publish or rollout button.",
    "store.gate.incomplete": function (a) { return "Set up " + a.repo + " before entering Store"; },
    "store.gate.complete": function (a) { return a.repo + " is set up"; },
    "store.gate.counts": function (a) { return a.n + " of " + a.total + " secrets set · " + a.done + " verified"; },
    "store.gate.help": "Store opens when every required secret is present and verified. Nothing you drop here leaves this machine.",
    "store.gate.enter": "Enter Store",
    "store.gate.enter_hint": "Opens when every required secret is present and verified",
    "store.gate.admin_only": function (a) { return "Only an admin token can set up " + a.repo + ". Your token sees this table read-only."; },
    "store.gate.no_token": "Add a token to verify or fetch",
    "secrets.title": function (a) { return "Saved secrets · " + a.repo; },
    "secrets.profile_line": function (a) { return "profile from server.toml [repos." + a.repo + ".release] · passed to jobs as " + a.name; },
    "secrets.never_shown": "Values are written once and never shown again. The API answers only present · fingerprint · verified at. Nothing is kept in this browser.",
    "secrets.verify_all": "Verify all (read-only)",
    "secrets.verifying": "Verifying…",
    "secrets.verify_done": "Verification finished",
    "secrets.verify_call_failed": function (a) { return "Verify failed: " + a.detail; },
    "secrets.col.secret": "Secret",
    "secrets.col.kind": "Kind",
    "secrets.col.state": "State",
    "secrets.col.fingerprint": "Fingerprint",
    "secrets.col.verified": "Verified",
    "secrets.kind.value": "value",
    "secrets.kind.file": "file",
    "secrets.kind.dir": "folder",
    "secrets.optional": "optional",
    "secrets.present": "present",
    "secrets.missing": "missing",
    "secrets.files_count": function (a) { return a.n + " of " + a.total + " files"; },
    "secrets.verified_at": function (a) { return "verified " + a.clock; },
    "secrets.verify_error": function (a) { return "failed · " + a.detail; },
    "secrets.not_verified": "not verified yet",
    "secrets.no_verify": "no check for this kind",
    "secrets.replace": "Replace…",
    "secrets.add": "Add…",
    "secrets.drop": "Drop a file or choose",
    "secrets.drop_replace": "Drop a file to replace",
    "secrets.dialog_title": function (a) { return "Set " + a.name; },
    "secrets.dialog_help": "Typed once, sent to the build machine, never shown again.",
    "secrets.field": "value",
    "secrets.empty_value": "Type a value first",
    "secrets.saving": "Saving…",
    "secrets.saved": function (a) { return a.name + " saved"; },
    "secrets.save_failed": function (a) { return "Could not save " + a.name + ": " + a.detail; },
    "secrets.reject.none": "Nothing was dropped",
    "secrets.reject.many": "Drop one file at a time",
    "secrets.reject.empty": "The file is empty",
    "secrets.reject.big": function (a) { return "Larger than " + a.limit; },
    "secrets.reject.admin": "Only an admin token can set secrets",
    "row.setup": "Setup",
    "row.source": "Source",
    "row.build": "Build · upload",
    "row.store": "Store",
    "row.settings": "Settings",
    "row.setup_head": function (a) { return a.n + "/" + a.total + " secrets present · verified " + a.clock + " · build number " + a.kind; },
    "row.state.ok": "ready",
    "row.state.bad": "needs attention",
    "row.state.running": "running",
    "row.state.stale": "stale",
    "row.state.na": "not available",
    "row.not_configured": function (a) { return "not configured — " + a.name + " is empty"; },
    "row.na.build": "No release running · not available in this build",
    "row.na.store": "Store status is not available in this build",
    "source.main_in_dev": "main in dev",
    "source.main_not_in_dev": "origin/main is not in origin/dev — back-merge main into dev first",
    "source.branches_unknown": "branch state unknown — a branch is missing in the mirror",
    "source.fetched": function (a) { return "fetched " + a.age; },
    "source.never_fetched": "not fetched yet",
    "source.stale": function (a) { return "stale · fetched " + a.age; },
    "source.fetch": "Fetch remote",
    "source.fetching": "Fetching…",
    "source.fetch_failed": function (a) { return "fetch failed: " + a.detail; },
    "source.fetch_done": "Mirror updated",
    "source.no_fetch_token": "Add a token to fetch",
    "source.profile_line": function (a) { return "default branch " + a.ref + " · tag " + a.text; },
    "source.mirror_at": function (a) { return "mirror fetched " + a.clock; },
    "review.title": "Submit for review",
    "review.na": "not available yet",
    "review.na_body": "The review panel comes in a later build. Nothing here submits or releases anything."
  };

  var KO = {
    // ── 화면 층(app.js) ─────────────────────────────────────────────────────
    "token.checking": "확인 중…",
    "token.rejected": "토큰이 거부됐습니다",
    "token.rejected_hint": "토큰이 거부됐습니다 — 새로 붙여 넣으세요",
    "token.ok": function (a) { return "ok · " + a.name + (a.admin ? " (관리자)" : ""); },
    "token.unverified": "토큰 (미확인)",
    "token.read_auth": "읽기 인증 필요",
    "token.bad_button": "토큰 거부됨",
    "token.named": function (a) { return a.name; },
    "token.kept": "확인하지 못했습니다 — 그대로 둡니다",
    "conn.live": function (a) { return "실시간 · " + a.age + " 갱신"; },
    "conn.polling": function (a) { return "주기 확인 · " + a.age + " 갱신"; },
    "conn.lost": function (a) { return "끊김 · 마지막 갱신 " + a.age; },
    "conn.paused": "멈춤 · 다시 시작",
    "conn.lost_banner": function (a) {
      return a.host + " 와 연결이 끊겼습니다 · 마지막 갱신 " + a.age + " · 마지막으로 알던 상태를 보여 줍니다";
    },
    "conn.reconnecting": function (a) { return a.countdown + " 뒤 재연결…"; },
    "conn.polling_every": "10초마다 확인 중…",
    "header.lanes_busy": function (a) { return "동시 실행 " + a.busy + "/" + a.lanes; },
    "header.error": function (a) { return "오류 · " + a.detail; },
    "header.clock_unknown": "시계 알 수 없음",
    "header.clock_skew": function (a) { return "시계 " + a.delta; },
    "header.paused": function (a) {
      return withJosa(a.by, "이/가") + " " + a.clock
        + " 에 큐를 멈췄습니다 — 진행 중인 작업은 끝까지 실행하고, 새 작업은 시작하지 않습니다 · ";
    },
    "header.worker_stopped": function (a) {
      return "레인 " + a.lanes + " 의 워커가 멈췄습니다: " + a.error + " · ";
    },
    "header.lanes_left": function (a) { return "대기 중인 작업은 레인 " + a.lanes + " 만 씁니다"; },
    "header.nothing_can_start": "아무것도 시작할 수 없습니다",
    "header.worker_unreachable": function (a) {
      return "워커 " + a.names + " 의 신호가 끊겼습니다 — 그 워커에서 진행 중이던 작업은 결과를 잃음으로 표시됩니다";
    },
    "header.not_scheduled": function (a) {
      return "레인이 비어 있는데 #" + a.id + " 가 " + a.dur + " 동안 시작되지 않았습니다 — 서버 로그를 보세요";
    },
    "footer.server": function (a) {
      return "rcm " + a.version + " · 가동 " + a.uptime + " · 스키마 v" + a.schema;
    },
    "summary.add_token": "토큰을 넣으면 내 작업이 표시됩니다",
    "summary.queue_unknown": "알 수 없음 — 큐를 읽지 못했습니다",
    "summary.no_jobs": "큐에 내 작업이 없습니다",
    "summary.and_more": function (a) { return a.n + "개 더"; },
    "summary.last_known": "마지막으로 알던 값: ",
    "summary.nothing_stuck": "모두 정상입니다",
    "summary.nothing_else_stuck": "그 밖에는 모두 정상입니다",
    "summary.host_unavailable": "호스트: 읽지 못함",
    "summary.host_no_sample": "호스트: 아직 측정값이 없습니다",
    "summary.host_sampled": function (a) { return "호스트 부하 · " + a.age + " 측정"; },
    "summary.verdict_fine": "여유",
    "summary.verdict_busy": "바쁨",
    "summary.verdict_partial": "일부만 확인됨",
    "summary.verdict_unknown": "알 수 없음",
    "summary.pressure": function (a) {
      return "CPU " + a.cpu + " · 메모리 " + a.mem + " · 디스크 " + a.disk + " · GPU " + a.gpu;
    },
    "summary.disk_free": function (a) { return a.free + " 남음"; },
    "summary.load": function (a) { return "처리 대기 " + a.load; },
    "queue.no_status": "큐를 읽지 못했습니다 — 아직 상태가 없습니다",
    "queue.unavailable": function (a) { return "큐를 읽지 못했습니다 — " + a.error; },
    "queue.empty_other_pools": "이 실행 그룹의 큐는 비었습니다 — 다른 그룹에서 작업이 기다립니다.",
    "queue.empty_paused": "큐가 비었지만 멈춰 있습니다 — 아무것도 시작하지 않습니다.",
    "queue.empty": "큐가 비었습니다 — ",
    "queue.empty_hint": " 하면 바로 시작합니다.",
    "queue.presets": function (a) { return "작업 유형: " + a.names; },
    "queue.more": function (a) { return a.n + "개 더 보기"; },
    "queue.group_running": function (a) { return "작업 중 (" + a.n + ")"; },
    "queue.group_waiting": function (a) { return "대기열 (" + a.n + ")"; },
    "queue.group_none_running": "진행 중인 작업이 없습니다",
    "queue.group_none_waiting": "대기 중인 작업이 없습니다",
    "queue.col_job": "작업",
    "queue.col_key": "작업 이름",
    "queue.col_requester": "실행한 사람",
    "queue.col_reason": "상태",
    "queue.col_elapsed": "진행 시간",
    "queue.col_eta": "완료 예상",
    "queue.col_source": "코드 출처",
    "queue.pool_jobs": function (a) { return a.head + " · 작업 " + a.n + "개"; },
    "row.group": function (a) { return "그룹 " + a.name; },
    "row.you": "나",
    "row.you_joined": "같은 작업에 묶임",
    "row.also_waiting": function (a) { return "함께 기다리는 사람: " + a.labels; },
    "row.token_of": function (a) { return "토큰: " + a.name; },
    "row.stalled_note": "응답이 계속 없으면 서버가 자동으로 취소합니다",
    "row.cancel_requested": "취소 요청함…",
    "row.cancel": "취소",
    "row.log": "로그",
    "row.add_token_for_log": "토큰을 넣으면 로그가 보입니다",
    "row.not_your_job": "내 작업이 아닙니다",
    "row.cancel_locked": "취소에는 제출할 때 받은 cancel token 이 필요합니다 — 제출한 세션에서 rcm cancel 을 쓰거나 관리자 토큰으로",
    "row.others_waiting": function (a) { return "다른 세션 " + a.n + "개가 이 작업을 기다립니다"; },
    "row.ref": function (a) { return "ref " + a.ref; },
    "row.not_received": "아직 받지 못함",
    "row.uncommitted": "미커밋",
    "row.tree": function (a) { return "tree " + a.hash; },
    "row.collapse": "접기",
    "row.collapse_job": function (a) { return "#" + a.id + " 상세 접기"; },
    "row.expand_job": function (a) { return "#" + a.id + " 상세 펴기"; },
    "row.copy": "복사",
    "row.expand": "펼치기",
    "host.unavailable": function (a) { return "호스트를 읽지 못했습니다 — " + a.error; },
    "host.no_sample": "호스트: 아직 측정값이 없습니다",
    "host.digest": function (a) { return a.names + " · " + a.state; },
    "host.window": "5분",
    "host.sampled": function (a) { return a.age + " 측정"; },
    "host.cores_load": function (a) { return "처리 대기 " + a.load + " · 코어 " + a.cores + "개 기준"; },
    "host.stale": function (a) { return a.dur + " 지남"; },
    "host.cpu": function (a) { return "CPU " + a.pct; },
    "host.cpu_detail": function (a) { return "user " + a.user + " · sys " + a.sys; },
    "host.memory": function (a) { return "메모리 " + a.used + " / " + a.total; },
    "host.disk": function (a) { return "디스크 " + a.used + " / " + a.total; },
    "host.disk_free": function (a) { return a.free + " 남음"; },
    "host.job_storage": function (a) { return "rcm 데이터 " + a.used + (a.limit ? " / " + a.limit : "") + (a.measured ? " · " + a.measured + " 측정" : "") + (a.next ? " · 다음 청소 " + a.next : ""); },
    "host.job_storage_unknown": function () { return "rcm 데이터: 용량을 재지 못했습니다"; },
    "host.compressed": function (a) { return "압축 " + a.size; },
    "host.gpu": function (a) { return "GPU " + a.pct + " 사용"; },
    "host.gpu_used": function (a) { return a.size + " 사용 중"; },
    "host.gpu_none": function (a) { return "GPU — " + a.note; },
    "host.last_known": "마지막으로 알던 값",
    "host.top": "무거운 프로세스: ",
    "est.unavailable": function (a) { return "예상 시간 · " + a.error; },
    "est.medians_unavailable": "중앙값을 읽지 못했습니다",
    "est.no_samples": "아직 기록이 없습니다. 같은 작업이 2번 성공하면 그때부터 실제 걸린 시간으로 예상합니다.",
    "est.keys": function (a) { return "키 " + a.n + "개 · 예상 시간 계산 방법"; },
    "est.preset": function (a) { return "설정값 " + a.dur; },
    "est.default": "기본값",
    "est.no_presets": "작업 유형 없음",
    "est.to_preset": function (a) { return "기록 " + a.n + "회 · 설정값 사용"; },
    "est.row": function (a) { return "대기 " + a.wait + " · 기록 " + a.n + "회 · " + a.conf; },
    "est.how": "최근 5회 이상 기록이 있으면 높음, 그보다 적으면 보통, 기록이 없어 설정값을 쓰면 낮음",
    "toast.not_found": function (a) { return "#" + a.id + " 를 찾지 못했습니다"; },
    "toast.finished": function (a) { return "#" + a.id + " " + a.state + " · " + a.clock + " 종료"; },
    "toast.lookup_failed": function (a) { return "#" + a.id + " — 조회하지 못했습니다"; },
    "toast.copied": function (a) { return "복사함: " + a.text; },
    "toast.inputs": function (a) { return "#" + a.id + " 입력: " + a.json; },
    "toast.left_join": function (a) { return "#" + a.id + " 합류에서 빠졌습니다"; },
    "toast.cancel_failed": function (a) { return "취소 실패: " + a.detail; },
    "toast.cancel_network": "취소 실패 — 네트워크",
    "toast.cancel_requested": "취소 요청함",
    "drawer.live": function (a) { return "실시간 · " + a.bytes; },
    "drawer.finished": function (a) { return "종료 · " + a.bytes; },
    "drawer.load_failed": "불러오지 못했습니다 — 다시 시도합니다",
    "cancel.confirm_title": function (a) {
      return withJosa("#" + a.id + " " + a.key + " (" + a.who + ")", "을/를") + " 취소할까요?";
    },
    "cancel.joiner_body": function (a) {
      return "이 작업에 함께 기다리고 있습니다. 나만 빠지고, 작업은 "
        + withJosa(a.who, "을/를") + " 위해 계속 진행합니다.";
    },
    "cancel.running_body": "정상 종료를 먼저 요청하고, 그래도 끝나지 않으면 강제로 종료합니다.",
    "cancel.queued_body": "큐에서 뺍니다. 기다리던 세션은 종료 코드 2를 받습니다. 다시 내려면 rcm run 을 다시 실행하세요.",
    "cancel.cannot_undo": "되돌릴 수 없습니다.",
    "cancel.others_waiting": function (a) { return "다른 세션 " + a.n + "개가 이 작업을 기다립니다."; },
    "cancel.leave": "합류 빠지기",
    // ── 정적 화면(index.html 의 data-i18n) ───────────────────────────────────
    "page.title": "rcm 큐",
    "conn.lost_short": "연결 끊김",
    "conn.pause_hint": "갱신 멈추기",
    "conn.connecting": "연결 중…",
    "lang.hint": "언어 바꾸기",
    "lang.button": "EN",
    "token.add": "토큰 넣기",
    "token.title": "클라이언트 토큰",
    "token.help": "<code>rcm token add</code> 로 받은 토큰을 붙여 넣으세요. 이 브라우저에만 남고, 내 작업 표시 · 로그 꼬리 · 전체 로그 · 취소가 열립니다.",
    "token.field": "토큰",
    "token.forget": "지우기",
    "dialog.close": "닫기",
    "dialog.save": "저장",
    "cancel.title": "작업을 취소할까요?",
    "cancel.keep": "그대로 두기",
    "cancel.go": "취소하기",
    "drawer.title": "로그",
    "drawer.job": function (a) { return "#" + a.id + (a.key ? " " + a.key : "") + " · 로그"; },
    "drawer.close": "로그 닫기",
    "section.summary": "요약",
    "section.queue": "큐",
    "section.host": "호스트",
    "section.recent": "최근",
    "summary.yours": "내 작업",
    "summary.stuck": "확인이 필요한 작업",
    "summary.host": "호스트 부하",
    "est.title": "예상 시간 계산 근거",
    // ── 상태 ────────────────────────────────────────────────────────────────
    // 회귀 방지: **`state.quiet` 키를 만들지 않는다.** 위 EN 블록의 주석과 같은 이유다 —
    // `quiet` 은 잡 상태가 아니라 표시 사유이고, `tests/web/state_marks.test.js` 가
    // `state.*` 집합을 잡 상태 아홉 개와 `deepEqual` 로 비교한다.
    // 결정 A3: 묶음 머리는 「작업 중」, 개별 행의 상태는 「진행 중」이다.
    "state.running": "진행 중",
    "state.queued": "대기",
    "state.uploading": "업로드 중",
    "state.cancelling": "취소 중",
    "state.succeeded": "성공",
    "state.failed": "실패",
    "state.timed_out": "시간 초과",
    "state.cancelled": "취소됨",
    "state.lost": "결과를 잃음",
    "state.unknown": "알 수 없음",
    "state.busy": "진행 중",
    "state.idle": "대기",
    "state.down": "끊김",
    "state.held": "부하로 대기",
    "reason.held_by_load": "머신이 바빠 대기 중",
    "hold.cpu_busy": function (a) { return "CPU " + a.cpu + "%"; },
    "hold.no_sample": "측정값 없음",
    "hold.cooldown": "잠깐 쉬는 중",
    "time.ago": function (a) { return a.dur + " 전"; },
    "time.in": function (a) { return a.dur + " 뒤"; },
    "time.now": "지금",

    "reason.unknown": "알 수 없음",
    "reason.running": "진행 중",
    "reason.running_lane": function (a) { return "진행 중 · 레인 " + a.lane; },
    "reason.waiting_for_lane": "대기 중",
    "reason.lanes_busy": function (a) { return "동시 실행 " + a.busy + "/" + a.lanes; },
    "reason.behind": function (a) { return "#" + a.id + " 다음"; },
    "reason.frees_in": function (a) { return a.dur + " 뒤 빔"; },
    "reason.blocked_by": function (a) { return withJosa(a.job, "이/가") + " 먼저 끝나야 함 · " + a.group; },
    "reason.uploading": function (a) { return "업로드 중 · " + a.bytes; },
    "reason.upload_stalled": function (a) { return "업로드 멈춤 " + a.since + " · " + a.bytes; },
    "reason.preparing": "작업 공간 준비 중",
    "reason.fetching": function (a) { return a.ref + " 받는 중"; },
    "reason.unpacking": function (a) { return a.bytes + " 푸는 중"; },
    "reason.over_by": function (a) { return a.over + " 초과 · 예상 " + a.expected; },
    "reason.stuck": "응답 없음",
    "reason.quiet": "출력이 조용합니다",
    "reason.step_over": function (a) { return a.step + " 단계가 평소보다 " + a.n + "배 오래"; },
    "reason.step_slow": function (a) { return a.step + " 단계가 평소보다 오래 걸립니다"; },
    "reason.times_expected": function (a) { return "지난 실행보다 " + a.n + "배 오래"; },
    "reason.no_output_for": function (a) { return a.since + " 동안 출력 없음"; },
    "reason.sigterm": function (a) { return withJosa(a.by, "이/가") + " 종료를 요청함 · " + a.kill + " 뒤 강제 종료"; },
    "reason.paused": "일시정지",
    "reason.not_scheduled": "시작되지 않음",
    "reason.no_worker": "워커 없음",

    "conf.high": "높음",
    "conf.med": "보통",
    "conf.low": "낮음",
    "conf.overdue": "예상 초과",
    "conf.group_wait": "낮음 · 그룹 대기",
    "conf.measured": function (a) { return a.conf + " · 최근 " + a.n + "회 기준"; },
    "conf.source": function (a) { return "낮음 · " + a.source; },
    "conf.low_dash": function (a) { return "낮음 · " + a.dash; },
    "eta.after": function (a) { return "#" + a.id + " 다음"; },
    "eta.in": function (a) { return a.dur + " 뒤"; },
    "eta.overdue": "예상 초과",
    "elapsed.waited": function (a) { return a.dur + " 기다림"; },
    "elapsed.waiting": function (a) { return a.dur + " 기다리는 중"; },
    "ordinal": function (a) { return a.n + "번째"; },
    "ordinal.in_line": function (a) { return "대기 " + a.ordinal; },

    "queue.unknown": "알 수 없음",
    "queue.counts": function (a) {
      return "작업 " + a.total + "개 · 진행 중 " + a.running + " · 대기 " + a.waiting;
    },
    "queue.oldest_waiting": function (a) { return "가장 오래 기다린 " + a.dur; },
    "queue.lanes_busy": function (a) { return "동시 실행 " + a.busy + "/" + a.lanes; },
    "your.eta": function (a) { return a.state + " · 종료 " + a.clock; },
    "your.in_line": function (a) { return a.ordinal + " · 종료 " + a.clock; },
    "your.joined": function (a) { return "함께 기다리는 사람 " + a.n + "명"; },

    "worker.state": function (a) { return "워커 " + a.state; },
    "worker.lane_state": function (a) { return "레인 " + a.lane + " · " + a.state; },
    "worker.paused": "일시정지",
    "pool.name": function (a) { return "실행 그룹 " + a.name; },
    "pool.no_workers": "워커 없음",
    "host.pool": function (a) { return a.host + " · 실행 그룹 " + a.pool; },

    "note.stale_ui": "화면이 옛 버전입니다 — 새로고침",
    "note.restarted": function (a) {
      return a.clock + " 에 서버가 재시작했습니다 — 그때 진행 중이던 작업은 결과를 잃음으로 표시됩니다";
    },

    "progress.no_markers": function (a) { return "단계 마커 없음 · 작업 " + a.dur; },
    "progress.step": function (a) {
      return "단계 " + a.cur + "/" + a.total + (a.soFar ? " (지금까지)" : "");
    },
    "progress.job": function (a) { return "작업 " + a.dur; },
    "progress.steps_failed": function (a) { return "단계 " + a.n + "개 실패"; },
    "progress.timing_note": "단계 시각은 서버가 받은 시각입니다 (as_received)",
    "progress.now": function (a) { return "단계 " + a.cur + "/" + a.total + " " + a.step; },
    "pbar.steps": function (a) { return a.percent + "% · 단계 " + a.done + "/" + a.total; },
    "pbar.steps_all": function (a) { return "단계 " + a.done + "/" + a.total; },
    "pbar.time": function (a) { return a.percent + "% · 예상 시간 기준"; },
    "pbar.time_measured": function (a) { return a.percent + "% · 지난 실행 기준"; },
    "pbar.time_preset": function (a) { return a.percent + "% · 설정한 예상 시간 기준"; },
    "pbar.over": "예상 시간 초과",
    "pbar.stuck": "응답 없음",
    "pbar.quiet": "조용함",
    "pbar.preparing": "준비 중",
    "pbar.finalizing": "마무리 중",
    "pbar.none": "진행률을 알 수 없음",
    "pbar.aria": function (a) { return "#" + a.id + " 진행"; },

    "recent.succeeded": "성공",
    "recent.failed": "실패",
    "recent.failed_exit": function (a) { return "실패 · 종료 코드 " + a.code; },
    "recent.cancelled": "취소됨 · 종료 코드 2",
    "recent.timed_out": "시간 초과 · 종료 코드 2",
    "recent.lost": "결과를 잃음 · 종료 코드 3",
    "recent.before_start": "시작 전",
    "recent.by": function (a) { return withJosa(a.who, "이/가") + " 취소"; },
    "recent.step": function (a) { return "단계 " + a.step; },
    "recent.last_step": function (a) { return "마지막 단계 " + a.step; },
    "failures.title": "이름별 실패",
    "failures.persistent": function (a) { return "최근 " + a.window + "회 전부"; },
    "failures.intermittent": function (a) { return "최근 " + a.window + "회 중 " + a.seen + "회 실패 · 가끔 실패합니다"; },
    "failures.first_seen": function (a) { return "최근 " + a.window + "회 중 처음"; },
    "failures.unknown": function (a) { return "아직 " + a.window + "회 중 " + a.seen + "회"; },
    "failures.unnamed": function (a) { return "그 중 " + a.n + "회는 이름 없이 실패했습니다"; },
    "failures.more": function (a) { return "… 그리고 " + a.n + "개 더 (로그에서 확인하세요)"; },
    "failures.step_kind": "단계",


    // ── 작업 결과 파일(M5e) ────────────────────────────────────────────────
    "art.label": "결과 파일",
    "art.state.ready": "받을 수 있음",
    "art.state.empty": "가져온 파일 없음",
    "art.state.dropped": "저장하지 않음",
    "art.state.failed": "수집 실패",
    "art.state.skipped": "수집 안 함",
    "art.state.purged": "받아 가서 지움",
    "art.state.expired": "기간 지나 지움",
    "art.state.unavailable": "파일 없음",
    "art.state.unknown": "모름",
    "art.state.collecting": "모으는 중…",
    "art.state.uploading": "올리는 중…",
    "art.files": function (a) { return a.n + "개"; },
    "art.left": function (a) { return a.dur + " 남음"; },
    "art.reason.over_bytes": "크기 상한 초과",
    "art.reason.over_files": "파일 수 상한 초과",
    "art.reason.no_match": "맞는 파일 없음",
    "art.reason.path_conflict": "경로가 겹침",
    "art.reason.unsafe_path": "안전하지 않은 경로",
    "art.reason.collect_failed": "수집 오류",
    "art.reason.upload_failed": "업로드 오류",
    "art.reason.storage_full": "서버 저장 공간 참",
    "art.reason.timed_out": "수집 시간 초과",
    "art.reason.cancelled": "취소됨",
    "art.reason.not_run": "작업이 시작되지 못함",
    "art.reason.interrupted": "중단됨",
    "art.reason.acked": "받아 감",
    "art.reason.expired": "기간 지남",
    "art.copy": "받아 가는 명령 복사",
    "recent.failed_step": "실패한 단계: ",
    "recent.last_step_label": "마지막 단계: ",
    "recent.none": "아직 끝난 작업이 없습니다",
    "recent.unavailable": function (a) { return "최근 결과를 읽지 못했습니다 — " + a.error; },
    "recent.count": function (a) { return a.total + "개 중 " + a.shown + "개"; },
    "recent.show_more": function (a) { return a.n + "개 더 보기"; },
    "recent.show_fewer": "접기",

    "priority.high": "높음",
    "priority.low": "낮음",
    "cache.text": function (a) { return "보관 중인 파일 " + a.blobs + "개 · " + a.size; },
    "transitions.waited": function (a) { return "(" + a.dur + " 기다림)"; },
    "transitions.exit": function (a) { return "종료 코드 " + a.code; },

    "outcome.cancelled_before_start": "시작 전에 취소됨",
    "outcome.cancelled_by": function (a) { return withJosa(a.by, "이/가") + " 취소함"; },
    "outcome.server_restarted": function (a) { return a.at + " 서버 재시작"; },
    "outcome.server_restarted_during_upload": "업로드 중 서버 재시작",
    "outcome.server_stopped_while_running": "실행 중 서버가 멈춤",
    "outcome.upload_abandoned": function (a) { return a.seconds + " 동안 업로드 없음 — 버림"; },
    "outcome.upload_interrupted": function (a) { return a.bytes + " 에서 업로드 끊김"; },
    "outcome.snapshot_too_big": function (a) {
      return "스냅샷 " + a.bytes + " 가 상한 " + a.limit + " 을 넘음";
    },
    "outcome.snapshot_rejected": function (a) { return "스냅샷 거부: " + rejected("ko", a); },
    "outcome.snapshot_blobs_missing": function (a) {
      return "스냅샷 거부: 업로드에 파일 " + a.count + "개가 빠짐";
    },
    "outcome.exit_code": function (a) { return "종료 코드 " + a.code; },
    "outcome.timed_out": function (a) { return a.limit; },
    "outcome.preset_missing": function (a) {
      return "프리셋 '" + a.preset + "'" + josa(a.preset, "이/가") + " 설정에 없음";
    },
    "outcome.tool_missing": function (a) { return "필요한 도구 " + a.tool + " 없음"; },
    "outcome.snapshot_missing": "스냅샷 파일이 없음",
    "outcome.blob_missing": function (a) { return "스냅샷 파일 " + withJosa(a.sha, "이/가") + " 없음"; },
    "outcome.repo_missing": function (a) {
      return a.where === "worker"
        ? "이 워커에 레포 '" + a.repo + "' 설정이 없음"
        : "레포 '" + a.repo + "'" + josa(a.repo, "이/가") + " 설정에 없음";
    },
    "outcome.commit_missing": function (a) {
      return "fetch 뒤에도 커밋 " + a.sha + " 없음(ref 가 옮겨갔거나 강제 push?)";
    },
    "outcome.git_failed": function (a) { return gitFailed("ko", a); },
    "outcome.snapshot_download_failed": function (a) {
      return "서버에서 스냅샷을 받지 못함" + (a.status ? " (HTTP " + a.status + ")" : "");
    },
    "outcome.launch_executable_missing": "프리셋의 명령을 찾지 못함",
    "outcome.launch_permission_denied": "프리셋의 명령을 실행할 수 없음(권한)",
    "outcome.launch_failed": function (a) {
      return "프리셋의 명령을 띄우지 못함 (" + a.error + ")";
    },
    "outcome.log_unavailable": function (a) {
      return "작업 로그 파일을 열지 못함 (" + a.error + ")";
    },
    "outcome.workspace_failed": function (a) {
      return "작업 공간을 준비하지 못함" + (a.error ? " (" + a.error + ")" : "");
    },
    "outcome.worker_error": function (a) { return "워커 오류: " + a.detail; },
    "outcome.worker_failed": "워커에서 실패",
    "outcome.worker_stopped_while_running": "실행 중 워커가 멈춤",
    "outcome.worker_restarted_without_job": function (a) {
      return "워커 " + withJosa(a.name, "이/가") + " 그 작업 없이 재시작함";
    },
    "outcome.worker_unreachable": function (a) {
      return "워커 " + withJosa(a.name, "이/가") + " " + a.seconds + "초 동안 응답 없음";
    },
    "outcome.cancel_unconfirmed": "워커가 취소를 확인하지 않음",

    "error.internal_error": "내부 오류",
    "error.database_unavailable": "데이터베이스를 쓸 수 없음",
    "error.sampler_failed": "표본 수집 실패",
    "gpu.no_sampler": "GPU 수집기 없음",
    "gpu.no_gpu": "GPU 없음",
    "gpu.sampler_failed": "GPU 수집 실패",

    /* ── 스토어 탭 ── */
    "nav.queue": "큐",
    "nav.store": "스토어",
    "nav.repo": "저장소",
    "store.heading": function (a) { return "스토어 · " + a.repo; },
    "store.loading": "불러오는 중…",
    "store.none": "이 서버에는 릴리스 프로파일을 가진 저장소가 없습니다.",
    "store.unknown_repo": function (a) { return a.repo + " 에는 이 서버에 릴리스 프로파일이 없습니다."; },
    "store.load_failed": function (a) { return a.repo + " 정보를 받지 못했습니다: " + a.detail; },
    "store.updated": function (a) { return a.age + " 갱신"; },
    "store.refresh": "새로고침",
    "store.policy": "승인은 출시가 아닙니다. 이 화면에는 release · publish · rollout 버튼이 없습니다.",
    "store.gate.incomplete": function (a) { return a.repo + " 설정을 마쳐야 스토어에 들어갑니다"; },
    "store.gate.complete": function (a) { return a.repo + " 설정이 끝났습니다"; },
    "store.gate.counts": function (a) { return "비밀 " + a.total + "개 중 " + a.n + "개 설정 · " + a.done + "개 검증됨"; },
    "store.gate.help": "필수 비밀이 전부 있고 검증되어야 스토어가 열립니다. 여기 넣은 것은 이 머신을 떠나지 않습니다.",
    "store.gate.enter": "스토어 들어가기",
    "store.gate.enter_hint": "필수 비밀이 전부 있고 검증되면 열립니다",
    "store.gate.admin_only": function (a) { return a.repo + " 설정은 admin 토큰만 할 수 있습니다. 이 토큰으로는 표를 보기만 합니다."; },
    "store.gate.no_token": "검증·가져오기에는 토큰이 필요합니다",
    "secrets.title": function (a) { return "저장된 비밀 · " + a.repo; },
    "secrets.profile_line": function (a) { return "server.toml [repos." + a.repo + ".release] 의 프로파일 · 작업에는 " + a.name + " 으로 전달"; },
    "secrets.never_shown": "값은 한 번 쓰고 다시 보여 주지 않습니다. API 는 있음 · 지문 · 검증 시각만 답합니다. 이 브라우저에는 아무것도 남지 않습니다.",
    "secrets.verify_all": "전부 검증 (읽기 전용)",
    "secrets.verifying": "검증 중…",
    "secrets.verify_done": "검증을 마쳤습니다",
    "secrets.verify_call_failed": function (a) { return "검증 요청 실패: " + a.detail; },
    "secrets.col.secret": "비밀",
    "secrets.col.kind": "종류",
    "secrets.col.state": "상태",
    "secrets.col.fingerprint": "지문",
    "secrets.col.verified": "검증",
    "secrets.kind.value": "값",
    "secrets.kind.file": "파일",
    "secrets.kind.dir": "폴더",
    "secrets.optional": "선택",
    "secrets.present": "있음",
    "secrets.missing": "없음",
    "secrets.files_count": function (a) { return "파일 " + a.total + "개 중 " + a.n + "개"; },
    "secrets.verified_at": function (a) { return a.clock + " 검증됨"; },
    "secrets.verify_error": function (a) { return "실패 · " + a.detail; },
    "secrets.not_verified": "아직 검증 안 함",
    "secrets.no_verify": "이 종류는 검증 없음",
    "secrets.replace": "교체…",
    "secrets.add": "넣기…",
    "secrets.drop": "파일을 끌어다 놓거나 고르기",
    "secrets.drop_replace": "파일을 끌어다 놓아 교체",
    "secrets.dialog_title": function (a) { return a.name + " 넣기"; },
    "secrets.dialog_help": "한 번 입력하면 빌드 머신으로 보내고 다시 보여 주지 않습니다.",
    "secrets.field": "값",
    "secrets.empty_value": "값을 먼저 입력해야 합니다",
    "secrets.saving": "저장 중…",
    "secrets.saved": function (a) { return a.name + " 저장됨"; },
    "secrets.save_failed": function (a) { return a.name + " 저장 실패: " + a.detail; },
    "secrets.reject.none": "놓인 파일이 없습니다",
    "secrets.reject.many": "파일은 한 번에 하나만 놓습니다",
    "secrets.reject.empty": "빈 파일입니다",
    "secrets.reject.big": function (a) { return a.limit + " 보다 큽니다"; },
    "secrets.reject.admin": "비밀은 admin 토큰만 넣을 수 있습니다",
    "row.setup": "설정",
    "row.source": "소스",
    "row.build": "빌드 · 업로드",
    "row.store": "스토어",
    "row.settings": "설정",
    "row.setup_head": function (a) { return "비밀 " + a.n + "/" + a.total + " 있음 · " + a.clock + " 검증 · 빌드 번호 " + a.kind; },
    "row.state.ok": "준비됨",
    "row.state.bad": "손봐야 함",
    "row.state.running": "진행 중",
    "row.state.stale": "낡음",
    "row.state.na": "해당 없음",
    "row.not_configured": function (a) { return "설정 안 됨 — " + a.name + " 이 비어 있음"; },
    "row.na.build": "도는 릴리스 없음 · 이 빌드에는 아직 없습니다",
    "row.na.store": "스토어 상태는 이 빌드에 아직 없습니다",
    "source.main_in_dev": "main 이 dev 에 있음",
    "source.main_not_in_dev": "origin/main 이 origin/dev 에 없습니다 — main 을 dev 로 먼저 되돌려 합칩니다",
    "source.branches_unknown": "브랜치 상태를 모릅니다 — 미러에 브랜치 하나가 없습니다",
    "source.fetched": function (a) { return a.age + " 가져옴"; },
    "source.never_fetched": "아직 가져오지 않음",
    "source.stale": function (a) { return "낡음 · " + a.age + " 가져옴"; },
    "source.fetch": "원격 가져오기",
    "source.fetching": "가져오는 중…",
    "source.fetch_failed": function (a) { return "가져오기 실패: " + a.detail; },
    "source.fetch_done": "미러를 갱신했습니다",
    "source.no_fetch_token": "가져오기에는 토큰이 필요합니다",
    "source.profile_line": function (a) { return "기본 브랜치 " + a.ref + " · 태그 " + a.text; },
    "source.mirror_at": function (a) { return "미러 " + a.clock + " 가져옴"; },
    "review.title": "심사 제출",
    "review.na": "아직 없음",
    "review.na_body": "심사 화면은 다음 빌드에 들어옵니다. 여기서는 아무것도 제출하거나 출시하지 않습니다."
  };

  var MESSAGES = { en: EN, ko: KO };

  function ordinalSuffix(n) {
    var v = Number(n);
    if (!isFinite(v)) return "th";
    var mod100 = v % 100;
    if (mod100 >= 11 && mod100 <= 13) return "th";
    return { 1: "st", 2: "nd", 3: "rd" }[v % 10] || "th";
  }

  /** 키를 그 언어의 문장으로. 없는 키는 던진다 — 화면에 `undefined` 를 그리지 않기 위해서다. */
  function t(lang, key, args) {
    var table = MESSAGES[lang] || MESSAGES[DEFAULT_LANG];
    var v = table[key];
    if (v === undefined) v = MESSAGES.en[key];
    if (v === undefined) throw new Error("i18n: unknown key " + key);
    return typeof v === "function" ? v(args || {}) : v;
  }

  /** 그 키가 두 언어에 다 있는가. 테스트와 개발용. */
  function has(key) {
    return Object.prototype.hasOwnProperty.call(EN, key);
  }

  function normalize(lang) {
    return LANGS.indexOf(lang) >= 0 ? lang : DEFAULT_LANG;
  }

  var api = {
    MESSAGES: MESSAGES, LANGS: LANGS, DEFAULT_LANG: DEFAULT_LANG,
    t: t, has: has, normalize: normalize, ordinalSuffix: ordinalSuffix,
    hasFinalConsonant: hasFinalConsonant, josa: josa, withJosa: withJosa
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof globalThis !== "undefined") globalThis.rcmI18n = api;
  else if (root) root.rcmI18n = api;
})(this);
