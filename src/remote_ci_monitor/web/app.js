/* rcm queue — app.js. 빌드 도구 없음. 정본: docs/wireframes/web-queue.html · docs/m2-workplan.md.
   앞부분은 순수 함수(window.rcm / module.exports — node --test 로 검사), 뒷부분은 DOM.
   규칙: 모르는 값은 "—", 절대 0·빈칸·긍정 문구로 그리지 않는다(fail-open 금지). */
(function (root) {
  "use strict";

  // 문자열 카탈로그(i18n.js). 브라우저는 앞서 실린 전역, node 테스트는 require.
  var I18N = (typeof require !== "undefined" && typeof module !== "undefined" && module.exports)
    ? require("./i18n.js")
    : ((typeof globalThis !== "undefined" && globalThis.rcmI18n) || (root && root.rcmI18n));
  var EN = "en";
  /** 키를 문장으로. `lang` 이 없으면 영어 — 순수 함수의 기존 계약을 지킨다. */
  function T(lang, key, args) { return I18N.t(lang || EN, key, args); }

  var DASH = "—";
  var ACTIONABLE = ["worker_down", "stuck", "upload_stalled", "not_scheduled", "blocked_by_group", "overdue", "paused"];
  var TERMINAL = { succeeded: 1, failed: 1, timed_out: 1, cancelled: 1, lost: 1 };
  // 상태마다 모양이 하나씩. 채운 것은 도는 상태, 빈 것은 멈춘 상태다(§4.2) — `취소 중`(채운 사각)과
  // `취소됨`(빈 사각)이 색으로만 갈리면 색을 못 보는 사람에게 같은 것이 된다(WCAG 1.4.1).
  // `core/render_text._GLYPH` 와는 **일부러 다르다**: 터미널에는 색이 없어 상태 글자가 곧 채널이고,
  // 거기는 `✅`·`❌` 처럼 원래 다른 모양을 쓴다. 이 표는 색을 쓰는 화면의 것이다.
  var GLYPH = { running: "▶", queued: "○", uploading: "↑", cancelling: "■", succeeded: "✓",
    failed: "✗", timed_out: "⏱", cancelled: "□", lost: "?" };
  /** 인라인 SVG 아이콘. 색은 `currentColor`, 크기는 1em — 문장 흐름에 붙어 산다.
      i18n 문자열에는 태그를 넣지 않는다(카탈로그는 글자만 담는다 — `token.help` 가 유일한 예외다).
      **위의 `GLYPH` 와는 다른 것이다**: 글리프는 상태 필의 모양 채널(WCAG 1.4.1)이라 글자로
      남아야 하고, 여기 아이콘은 장식이라 `aria-hidden` 으로 붙는다. */
  var ICON = {
    key: '<svg viewBox="0 0 16 16" width="1em" height="1em" fill="none" stroke="currentColor"'
      + ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
      + '<circle cx="5.5" cy="10.5" r="3.2"/><path d="M7.9 8.1 13.5 2.5"/>'
      + '<path d="M11 5l1.6 1.6"/></svg>',
    chain: '<svg viewBox="0 0 16 16" width="1em" height="1em" fill="none" stroke="currentColor"'
      + ' stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
      + '<path d="M6.4 9.6a2.6 2.6 0 0 1 0-3.7l2-2a2.6 2.6 0 0 1 3.7 3.7l-1 1"/>'
      + '<path d="M9.6 6.4a2.6 2.6 0 0 1 0 3.7l-2 2a2.6 2.6 0 0 1-3.7-3.7l1-1"/></svg>',
    chevronDown: '<svg viewBox="0 0 16 16" width="1em" height="1em" fill="none" stroke="currentColor"'
      + ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6 8 10.5 12.5 6"/></svg>',
    chevronUp: '<svg viewBox="0 0 16 16" width="1em" height="1em" fill="none" stroke="currentColor"'
      + ' stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 10 8 5.5 12.5 10"/></svg>'
  };
  function icon(name) { return '<span class="ic" aria-hidden="true">' + ICON[name] + "</span>"; }
  var BACKOFF = [2, 4, 8, 16, 30];
  var LOST_AFTER_MS = 30000;
  var POLL_MS = 10000;
  var TICK_MS = 1000;
  var REFETCH_COALESCE_MS = 300;
  var HIDDEN_PAUSE_MS = 60000;
  // 남은 저장 공간이 이 밑이면 사용률과 무관하게 경고한다 — 스냅샷 하나가 못 풀린다(§4.6-가)
  var DISK_LOW_FREE = 10 * 1024 * 1024 * 1024;
  // 「바쁨」은 85 에서 켜지고 **80 아래로 내려와야** 꺼진다. 빌드 머신의 CPU 는 일하는 동안
  // 85 를 계속 스쳐서, 경계가 하나면 판정이 표본마다 뒤집힌다. 푸는 값 80 은 새 숫자가 아니다 —
  // M5f 의 `cpu_max_percent` 기본값과 같은 「이 아래면 여유」다.
  var BUSY_ON = 85, BUSY_OFF = 80;

  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  // ── 표기 (목업 4절 · core/render_text 와 같은 규칙) ──
  function fmtDuration(seconds) {
    if (!isNum(seconds)) return DASH;
    var s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60); s = s % 60;
    if (m < 60) return m + "m " + pad2(s) + "s";
    var h = Math.floor(m / 60); m = m % 60;
    return h + "h " + pad2(m) + "m";
  }
  function parseIso(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    return isNaN(t) ? null : t;
  }
  function partsIn(ms, tz) {
    var opts = { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false };
    if (tz) opts.timeZone = tz;
    var out = {};
    try {
      new Intl.DateTimeFormat("en-US", opts).formatToParts(new Date(ms)).forEach(function (p) { out[p.type] = p.value; });
    } catch (e) {
      var d = new Date(ms);
      out = { year: String(d.getFullYear()), month: d.toLocaleString("en-US", { month: "short" }), day: String(d.getDate()),
        hour: pad2(d.getHours()), minute: pad2(d.getMinutes()), second: pad2(d.getSeconds()) };
    }
    if (out.hour === "24") out.hour = "00";
    return out;
  }
  function fmtClock(iso, tz, nowMs) {
    var t = parseIso(iso);
    if (t == null) return DASH;
    var p = partsIn(t, tz);
    var hm = p.hour + ":" + p.minute;
    if (isNum(nowMs)) {
      var q = partsIn(nowMs, tz);
      if (q.year !== p.year || q.month !== p.month || q.day !== p.day) return p.month + " " + p.day + " · " + hm;
    }
    return hm;
  }
  function fmtClockSeconds(iso, tz) {
    var t = parseIso(iso);
    if (t == null) return DASH;
    var p = partsIn(t, tz);
    return p.hour + ":" + p.minute + ":" + p.second;
  }
  // 나이·멈춘 시간은 거칠게(목업 4절): 60초 미만은 초, 그 위는 분·시를 내림. fmtAgo 와 Reason 의
  // 「upload stalled 2m」·「no output for 4m」이 같은 눈금을 쓴다.
  function fmtCoarse(seconds) {
    if (!isNum(seconds)) return DASH;
    var s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    return Math.floor(s / 3600) + "h";
  }
  function fmtAgo(seconds, lang) { return isNum(seconds) ? T(lang, "time.ago", { dur: fmtCoarse(seconds) }) : DASH; }
  function fmtCountdown(seconds, lang) {
    if (!isNum(seconds)) return DASH;
    if (seconds <= 0) return T(lang, "time.now");
    return T(lang, "time.in", { dur: fmtDuration(seconds) });
  }
  function fmtBytes(n) {
    if (!isNum(n)) return DASH;
    if (n >= 5e8) return (n / 1e9).toFixed(1) + " GB";  // 0.6 GB 부터 GB 한 자리(목업 「0.6 GB in use」)
    if (n >= 1e6) return Math.round(n / 1e6) + " MB";
    if (n >= 1e3) return Math.round(n / 1e3) + " KB";
    return Math.round(n) + " B";
  }
  function fmtMb(n) { return isNum(n) ? Math.round(n) + " MB" : DASH; }
  // 「30 / 48 MB」 — 단위는 한 번만(목업 11·31). 눈금은 total(없으면 received) 기준, 모르는 쪽은 —
  function fmtBytesPair(received, total) {
    var ref = isNum(total) ? total : received;
    if (!isNum(ref)) return DASH + " / " + DASH;
    var div = ref >= 5e8 ? 1e9 : ref >= 1e6 ? 1e6 : ref >= 1e3 ? 1e3 : 1;
    var unit = div === 1e9 ? "GB" : div === 1e6 ? "MB" : div === 1e3 ? "KB" : "B";
    var one = function (n) { return !isNum(n) ? DASH : div === 1e9 ? (n / div).toFixed(1) : String(Math.round(n / div)); };
    return one(received) + " / " + one(total) + " " + unit;
  }
  // 메모리·GPU 메모리는 GiB 로 세고 라벨은 GB (Activity Monitor · rcm top 과 같다). 업로드 크기는 fmtBytes(십진).
  function fmtMemory(n) {
    if (!isNum(n)) return DASH;
    var gib = n / 1073741824;
    if (gib >= 0.95) return gib.toFixed(1) + " GB";
    return Math.round(n / 1048576) + " MB";
  }
  // 디스크는 십진 GB 정수 — Finder·`df -H` 와 같은 눈금이고, 소수점을 빼야 미터 한 칸에 들어간다
  function fmtDisk(n) {
    if (!isNum(n)) return DASH;
    if (n >= 1e9) return Math.round(n / 1e9) + " GB";
    if (n >= 1e6) return Math.round(n / 1e6) + " MB";
    return Math.round(n / 1e3) + " KB";
  }
  function fmtPct(v) { return isNum(v) ? Math.round(v) + "%" : DASH; }
  function ordinal(n, lang) {
    if (!isNum(n)) return DASH;
    return T(lang, "ordinal", { n: n });
  }
  function truncate(label, n) {
    if (label == null) return DASH;
    n = n || 40;
    var s = String(label);
    return s.length <= n ? s : s.slice(0, n - 1) + "…";
  }
  function stateWord(state, lang) {
    if (!state) return T(lang, "state.unknown");
    var key = "state." + state;
    return I18N.has(key) ? T(lang, key) : state;
  }
  function stateGlyph(state) { return GLYPH[state] || "·"; }

  // 로컬 레인만 센다 — server.lanes 가 로컬 수라 원격(worker 키) 항목을 섞으면 "3/2" 가 된다 (M5b-2)
  function busyCount(status) {
    var w = status && status.server && status.server.workers;
    if (!Array.isArray(w)) return null;
    return w.filter(function (x) { return !x.worker && x.state === "busy"; }).length;
  }
  function laneCount(status) {
    var s = status && status.server;
    if (!s) return null;
    if (isNum(s.lanes)) return s.lanes;
    return Array.isArray(s.workers) ? s.workers.filter(function (x) { return !x.worker; }).length : null;
  }
  function secondsSince(iso, nowMs) {
    var t = parseIso(iso);
    return t == null || !isNum(nowMs) ? null : (nowMs - t) / 1000;
  }
  function secondsUntil(iso, nowMs) {
    var t = parseIso(iso);
    return t == null || !isNum(nowMs) ? null : (t - nowMs) / 1000;
  }

  // 서버는 cancel.by · cancelled_by 에 토큰 이름을 쓴다. 요청자·합류자와 같으면 그 라벨을 보인다
  // (목업 30 「SIGTERM sent by alice@laptop」), 아니면 이름 그대로(macmini-admin · server).
  function personLabel(row, name) {
    if (!name) return DASH;
    var req = row && row.requester;
    if (req && req.name === name) return req.label || name;
    var js = row && Array.isArray(row.joiners) ? row.joiners : [];
    for (var i = 0; i < js.length; i++) if (js[i] && js[i].name === name) return js[i].label || name;
    return name;
  }

  // ── Reason 열 (항목 11) ──
  function reasonText(row, a, b, lang) {
    var nowMs = isNum(a) ? a : (isNum(b) ? b : null);
    var status = (a && typeof a === "object") ? a : ((b && typeof b === "object") ? b : null);
    var reason = row && row.reason;
    var est = (row && row.estimate) || {};
    var src = (row && row.source) || {};
    var links = [];
    var out = { text: T(lang, "reason.unknown"), actionable: false, links: links, cls: "" };
    if (!row || !reason) return out;
    // 조각을 모아 ` · ` 로 잇는다. 조각의 문구와 안쪽 어순은 언어마다 다르고, 잇는 방식은 같다.
    var parts = [];
    switch (reason) {
      case "running":
        out.text = isNum(row.lane)
          ? T(lang, "reason.running_lane", { lane: row.lane })
          : T(lang, "reason.running");
        break;
      case "waiting_for_lane": {
        parts.push(T(lang, "reason.waiting_for_lane"));
        var busy = busyCount(status), lanes = laneCount(status);
        if (isNum(busy) && isNum(lanes)) parts.push(T(lang, "reason.lanes_busy", { busy: busy, lanes: lanes }));
        if (isNum(row.ahead_job_id)) { parts.push(T(lang, "reason.behind", { id: row.ahead_job_id })); links.push({ jobId: row.ahead_job_id }); }
        if (isNum(est.wait_seconds)) parts.push(T(lang, "reason.frees_in", { dur: fmtDuration(est.wait_seconds) }));
        out.text = parts.join(" · "); break;
      }
      case "held_by_load": {
        // 숫자는 행이 아니라 `server.workers[]` 의 `hold_detail` 에서 온다 — 행 키를 안 늘린다
        parts.push(T(lang, "reason.held_by_load"));
        var why = holdWord(status, lang);
        if (why) parts.push(why);
        out.text = parts.join(" · "); break;
      }
      case "blocked_by_group": {
        var bb = row.blocked_by || {};
        // 조각은 남기고 모르는 숫자만 —(「frees in —」): 막는 잡이 있는 한 「언제 풀리나」는 늘 묻는 질문이다
        out.text = T(lang, "reason.blocked_by", {
          job: isNum(bb.job_id) ? "#" + bb.job_id : DASH,
          group: bb.group || row.concurrency_group || DASH
        }) + " · " + T(lang, "reason.frees_in", { dur: fmtDuration(bb.remaining_seconds) });
        if (isNum(bb.job_id)) links.push({ jobId: bb.job_id });
        out.actionable = true; out.cls = "blocked"; break;
      }
      case "uploading":
        out.text = T(lang, "reason.uploading", { bytes: fmtBytesPair(src.received_bytes, src.bytes) }); break;
      case "upload_stalled": {
        var since = secondsSince(src.last_received_at, nowMs);
        out.text = T(lang, "reason.upload_stalled", {
          since: fmtCoarse(since), bytes: fmtBytesPair(src.received_bytes, src.bytes)
        });
        out.actionable = true; out.cls = "stalled"; break;
      }
      case "materializing":
        // 목업 33: tree 는 「unpacking 48 MB」, git_ref 는 「fetching dev」, 둘 다 모르면 조각 없이
        parts.push(T(lang, "reason.preparing"));
        if (src.mode === "git_ref") { if (src.ref) parts.push(T(lang, "reason.fetching", { ref: src.ref })); }
        else if (isNum(src.bytes)) parts.push(T(lang, "reason.unpacking", { bytes: fmtBytes(src.bytes) }));
        out.text = parts.join(" · ");
        break;
      case "overdue": {
        var over = isNum(est.elapsed_seconds) && isNum(est.expected_seconds) ? est.elapsed_seconds - est.expected_seconds : null;
        out.text = T(lang, "reason.over_by", {
          over: fmtDuration(over), expected: fmtDuration(est.expected_seconds)
        });
        out.actionable = true; out.cls = "over"; break;
      }
      case "stuck": {
        // 근거(`estimate.stuck_code`)마다 다른 문장을 쓴다. 근거가 「현재 단계가 평소보다 오래」인데
        // 「지난 실행보다 n배 오래」를 함께 그리면 두 사실이 섞여 하나도 안 읽힌다.
        parts.push(T(lang, "reason.stuck"));
        var code = est.stuck_code;
        var prog = row.progress || null;
        if (code === "over_step") {
          var stepName = prog && prog.current_name;
          var stepSeconds = prog && isNum(prog.current_seconds) ? prog.current_seconds : null;
          var usual = isNum(est.step_expected_seconds) ? est.step_expected_seconds : null;
          // 배수 가드는 `over_elapsed` 쪽과 **같은 규칙**이다(아래 주석). 서버가 하한
          // (`max(배수 × 단계중앙값, no_output_seconds)`) 때문에 배수가 2 에 못 미치는
          // `over_step` 을 보낼 수 있고, 그러면 「평소보다 1배 오래」가 또 나온다.
          if (stepName && isNum(stepSeconds) && usual !== null && usual > 0) {
            var stepTimes = Math.floor(stepSeconds / usual);
            if (stepTimes >= 2) parts.push(T(lang, "reason.step_over", { step: stepName, n: stepTimes }));
            else parts.push(T(lang, "reason.step_slow", { step: stepName }));
          }
        } else if (code !== "no_output") {
          // `over_elapsed`, 그리고 `stuck_code` 를 안 보내는 **옛 서버 문서**가 여기로 온다.
          // 배수가 2 이상일 때만 붙인다 — 2026-09-15 화면의 「예상의 1배」가 바로 이 자리에서
          // 나왔다(`floor(1044 / 1020) === 1`). 1배는 사실이지만 아무것도 말하지 않는다.
          if (isNum(est.elapsed_seconds) && isNum(est.expected_seconds) && est.expected_seconds > 0) {
            var times = Math.floor(est.elapsed_seconds / est.expected_seconds);
            if (times >= 2) parts.push(T(lang, "reason.times_expected", { n: times }));
          }
        }
        var lo = row.progress && row.progress.last_output_at;
        var silentFor = secondsSince(lo, nowMs);
        if (isNum(silentFor)) parts.push(T(lang, "reason.no_output_for", { since: fmtCoarse(silentFor) }));
        out.text = parts.join(" · "); out.actionable = true; out.cls = "stuck"; break;
      }
      case "quiet": {
        // 조용함은 **관측**이지 경보가 아니다 — `actionable` 은 false 로 두고(`ACTIONABLE` 에도
        // 없다) 색은 회색이다. 이것을 「확인이 필요한 작업」에 올리면 오늘의 빨간 소음이 이름만
        // 바꿔 남는다.
        parts.push(T(lang, "reason.quiet"));
        var quietLo = row.progress && row.progress.last_output_at;
        var quietFor = secondsSince(quietLo, nowMs);
        if (isNum(quietFor)) parts.push(T(lang, "reason.no_output_for", { since: fmtCoarse(quietFor) }));
        out.text = parts.join(" · "); out.cls = "quiet"; break;
      }
      case "cancelling": {
        var c = row.cancel || {};
        var kill = secondsUntil(c.kill_at, nowMs);
        out.text = T(lang, "reason.sigterm", { by: personLabel(row, c.by), kill: fmtCountdown(kill, lang) }); break;
      }
      case "paused": out.text = T(lang, "reason.paused"); out.actionable = true; break;
      case "not_scheduled": out.text = T(lang, "reason.not_scheduled"); out.actionable = true; break;
      case "worker_down": out.text = T(lang, "reason.no_worker"); out.actionable = true; break;
      default: out.text = T(lang, "reason.unknown");
    }
    return out;
  }

  function confidenceBadge(est, lang) {
    est = est || {};
    var n = isNum(est.sample_count) ? est.sample_count : null;
    var c = est.confidence;
    if (!c) {
      // 서버가 안 보냈을 때의 대체 계산 — 서버(core/queue.confidence)와 규칙이 같아야 한다.
      // 같이 도는 중이면 한 칸 내린다: 중앙값은 혼자 잰 것이다 (M5f).
      if (est.source === "measured") {
        var high = n != null && n >= 5;
        c = est.shared ? (high ? "med" : "low") : (high ? "high" : "med");
      }
      else if (est.source) c = "low";
      else return { cls: "low", text: T(lang, "conf.low_dash", { dash: DASH }) };
    }
    if (c === "overdue") return { cls: "over", text: T(lang, "conf.overdue") };
    // `group wait` 는 서버가 보내는 문구다(core/queue.py) — 화면에서만 자기 말로 바꾼다
    if (c === "group wait") return { cls: "low", text: T(lang, "conf.group_wait") };
    if (c === "high" || c === "med") {
      var word = T(lang, "conf." + c);
      return { cls: c, text: n != null ? T(lang, "conf.measured", { conf: word, n: n }) : word + " · measured" };
    }
    return { cls: "low", text: T(lang, "conf.source", { source: est.source || DASH }) };
  }

  function etaText(row, tz, nowMs, lang) {
    var est = (row && row.estimate) || {};
    if (!est.finish_at) return { clock: DASH, rel: null };
    var busy = row.state === "running" || row.state === "cancelling";
    var total = busy ? est.remaining_seconds : (isNum(est.wait_seconds) && isNum(est.remaining_seconds) ? est.wait_seconds + est.remaining_seconds : null);
    if (row.reason === "blocked_by_group" && row.blocked_by && isNum(row.blocked_by.job_id)) {
      return { clock: T(lang, "eta.after", { id: row.blocked_by.job_id }), rel: "~" + fmtClock(est.finish_at, tz, nowMs) };
    }
    return { clock: fmtClock(est.finish_at, tz, nowMs), rel: isNum(total) ? T(lang, "eta.in", { dur: fmtDuration(total) }) : null };
  }

  function elapsedText(row, nowMs, lang) {
    var est = (row && row.estimate) || {};
    var st = row && row.state;
    if (st === "running" || st === "cancelling") {
      var sub = isNum(est.waited_seconds) && est.waited_seconds > 0 ? T(lang, "elapsed.waited", { dur: fmtDuration(est.waited_seconds) }) : null;
      return { main: fmtDuration(est.elapsed_seconds), sub: sub };
    }
    if (st === "queued") return { main: T(lang, "elapsed.waiting", { dur: fmtDuration(est.waited_seconds) }), sub: null };
    return { main: DASH, sub: null };
  }

  function pool0(status) { return status && Array.isArray(status.pools) && status.pools.length ? status.pools[0] : null; }
  function poolsOf(status) { return status && Array.isArray(status.pools) ? status.pools : []; }
  // 모든 풀의 큐를 이어 붙인다. 어느 풀이든 queue 가 null(조회 실패)이면 undefined — unknown 이지 ok 가 아니다
  function queueOf(status) {
    var pools = poolsOf(status);
    if (!pools.length) return undefined;
    var all = [];
    for (var i = 0; i < pools.length; i++) {
      if (!Array.isArray(pools[i].queue)) return undefined;
      all = all.concat(pools[i].queue);
    }
    return all;
  }
  // 모든 풀의 완료 잡을 끝난 시각 내림차순으로. 기본 풀 밖의 잡은 `_pool` 을 달아 Recent 가 칩을 그린다.
  // 어느 풀이든 recent 가 null(조회 실패)이면 undefined — 「완료 잡 없음」이 아니라 unknown 이다
  function recentOf(status) {
    var pools = poolsOf(status);
    if (!pools.length) return undefined;
    var all = [];
    for (var i = 0; i < pools.length; i++) {
      var r = pools[i] && pools[i].recent;
      if (!Array.isArray(r)) return undefined;
      for (var j = 0; j < r.length; j++) all.push(i === 0 ? r[j] : Object.assign({}, r[j], { _pool: pools[i].name }));
    }
    all.sort(function (a, b) { return (Date.parse(b.finished_at) || 0) - (Date.parse(a.finished_at) || 0); });
    return all;
  }
  // ── 풀 (M5b) ──
  // Host 절의 카드 목록(M5b-4): 기본 풀의 표본이 먼저(제목 = 이름, 오늘 그대로), 그 뒤 다른 풀의
  // 워커 표본(제목 `<이름> · pool <풀>`). 표본이 없거나 null 인 원격 풀은 카드를 만들지 않는다.
  function hostCards(status, lang) {
    var pools = poolsOf(status);
    var out = [];
    pools.forEach(function (pl, i) {
      if (!pl || !Array.isArray(pl.hosts)) return;
      pl.hosts.forEach(function (h) {
        if (!h) return;
        var title = i === 0 ? (h.name || DASH) : T(lang, "host.pool", { host: h.name || DASH, pool: pl.name || DASH });
        out.push({ title: title, pool: pl.name || "default", host: h });
      });
    });
    return out;
  }

  /**
   * 큐를 「지금 도는 것」과 「기다리는 것」으로 나눈다(§4.6-라). 빈 묶음도 남긴다 — 화면이
   * 「지금 도는 것 없음」을 그릴 수 있어야 한다. 행 순서는 `sortQueue` 가 정한 그대로.
   */
  function queueGroups(rows, lang) {
    var list = Array.isArray(rows) ? rows : [];
    var running = list.filter(function (r) { return r && (r.state === "running" || r.state === "cancelling"); });
    var waiting = list.filter(function (r) { return !r || (r.state !== "running" && r.state !== "cancelling"); });
    return [
      { key: "running", title: T(lang, "queue.group_running", { n: running.length }), rows: running },
      { key: "waiting", title: T(lang, "queue.group_waiting", { n: waiting.length }), rows: waiting }
    ];
  }

  function poolHeader(pool, lang) {
    if (!pool || pool.name === "default" || !pool.name) return "";
    var noWorkers = isNum(pool.lanes) && pool.lanes === 0;
    return T(lang, "pool.name", { name: pool.name }) + (noWorkers ? " · " + T(lang, "pool.no_workers") : "");
  }
  function poolSummary(pools) {
    if (!Array.isArray(pools)) return { running: null, waiting: null, pools: 0 };
    var running = 0, waiting = 0;
    for (var i = 0; i < pools.length; i++) {
      var q = pools[i] && pools[i].queue;
      if (!Array.isArray(q)) return { running: null, waiting: null, pools: pools.length };
      for (var j = 0; j < q.length; j++) {
        if (q[j].state === "running" || q[j].state === "cancelling") running++; else waiting++;
      }
    }
    return { running: running, waiting: waiting, pools: pools.length };
  }

  // ── 요약 (항목 23 · 24 · 25) ──
  function notMoving(status, me, lang) {
    var q = queueOf(status);
    if (!Array.isArray(q)) return { kind: "unknown", lines: [] };
    var lines = [];
    var unknown = false;
    q.forEach(function (row) {
      if (!("reason" in row)) { unknown = true; return; }
      if (row.reason === "running" || row.reason === "waiting_for_lane" || row.reason === "uploading" || row.reason === "materializing" || row.reason === "cancelling") return;
      if (ACTIONABLE.indexOf(row.reason) === -1) return;
      var r = reasonText(row, status, Date.parse(status.generated_at), lang);
      lines.push({ jobId: row.id, reason: row.reason, text: r.text, rank: ACTIONABLE.indexOf(row.reason) });
    });
    if (unknown && !lines.length) return { kind: "unknown", lines: [] };
    lines.sort(function (a, b) { return a.rank - b.rank || (a.jobId || 0) - (b.jobId || 0); });
    return lines.length ? { kind: "list", lines: lines } : { kind: "ok", lines: [] };
  }

  function isMine(row, me) {
    if (!me || !row) return false;
    if (row.requester && row.requester.name === me) return true;
    return Array.isArray(row.joiners) && row.joiners.some(function (j) { return j && j.name === me; });
  }

  // 취소 버튼이 잠기는가(M5j G5). 서버가 취소에 제출 capability 를 요구하면(`server.cancel_requires_submission_token`)
  // 페이지는 그 비밀을 가질 수 없다 — 비밀은 `rcm run` 을 돌린 세션의 상태 파일에만 있다. admin 토큰만 연다.
  // 키가 없는 옛 서버·꺼진 서버는 오늘 그대로 열려 있다. `me` 는 `/api/whoami` 의 `{name, admin}` 또는 null.
  function cancelLocked(server, me) {
    if (!server || server.cancel_requires_submission_token !== true) return false;
    return !(me && me.admin === true);
  }

  // 시각은 status.display_timezone · generated_at 기준(§2 시그니처가 (status, me) 라 다른 데서 올 수 없다).
  // text 에 잡 id 는 넣지 않는다 — id 는 렌더 층이 버튼으로 따로 그린다(목업 23 「<b>#412</b> running …」).
  function yourJobs(status, me, lang) {
    if (!me) return { kind: "no_token", lines: [], more: 0 };
    var q = queueOf(status);
    if (!Array.isArray(q)) return { kind: "unknown", lines: [], more: 0 };
    var tz = status.display_timezone || undefined;
    var nowMs = parseIso(status.generated_at);
    var mine = sortQueue(q.filter(function (r) { return isMine(r, me); }));
    if (!mine.length) return { kind: "none", lines: [], more: 0 };
    var lines = mine.slice(0, 2).map(function (row) {
      var busy = row.state === "running" || row.state === "cancelling";
      var eta = etaText(row, tz, nowMs, lang);
      var t;
      if (busy) t = T(lang, "your.eta", { state: stateWord(row.state, lang), clock: eta.clock }) + (eta.rel ? " · " + eta.rel : "");
      else {
        // 대기 줄은 짧게: 순번 · ETA · 이유의 첫 조각(「2nd in line · ETA 09:58 · waiting for lane」). 그룹 대기는 ~시각
        // `after #N` 은 카탈로그가 만든 문구라 언어마다 다르다 — 조각 비교 대신 blocked 여부로 고른다
        var blocked = row.reason === "blocked_by_group";
        var clock = blocked && eta.rel ? eta.rel : eta.clock;
        var head = isNum(row.position)
          ? T(lang, "ordinal.in_line", { ordinal: ordinal(row.position, lang) })
          : stateWord(row.state, lang);
        t = T(lang, "your.in_line", { ordinal: head, clock: clock });
        // 이유의 첫 조각. 「알 수 없음」이면 붙이지 않는다(언어와 무관하게 같은 판정)
        var reason = reasonText(row, nowMs, status, lang);
        var brief = reason.text.replace(/^⛓ /, "").split(" · ")[0];
        if (brief && row.reason && row.reason !== "unknown" && reason.text !== T(lang, "reason.unknown")) t += " · " + brief;
      }
      var joined = Array.isArray(row.joiners) ? row.joiners.length : 0;
      if (joined) t += " · " + T(lang, "your.joined", { n: joined });
      return { jobId: row.id, text: t, state: row.state };
    });
    return { kind: "list", lines: lines, more: Math.max(0, mine.length - 2) };
  }

  // {cpu, mem, gpu, disk, diskFree, load, verdict} — 퍼센트는 정수(목업 4절), load 는 「3.5 / 10」 문자열(§2).
  // 85% 이상이면 busy(아는 값이 이미 바쁘다고 말하므로 partial 보다 우선), 하나라도 모르면 partial, 넷 다 모르면 unknown.
  // 디스크는 기준이 둘이다(§4.6-가): 사용률 85% 이상, 또는 남은 공간 10 GiB 미만. 큰 디스크는 90%
  // 라도 넉넉하고 작은 디스크는 80% 라도 빌드가 안 돈다 — 하나만 보면 틀린다.
  // load·cores 는 판정에 안 들어간다 — 텍스트만 —.
  // `prev` 는 이 호스트의 **직전 판정**이다. 주면 이력이 붙는다: 한 번 busy 가 된 호스트는
  // 전부 80 아래로 내려와야 busy 를 놓는다(85 를 스칠 때마다 「바쁨↔여유」가 깜빡이지 않게).
  // 안 주면 오늘 규칙 그대로다 — 첫 렌더와 순수 호출부가 안 바뀐다.
  function hostPressure(host, prev) {
    if (!host) return { cpu: null, mem: null, gpu: null, disk: null, diskFree: null, load: DASH, verdict: "no_sample" };
    var pct = function (v) { return isNum(v) ? Math.round(v) : null; };
    var cpu = pct(host.cpu && host.cpu.busy);
    var mem = host.memory && isNum(host.memory.used_bytes) && isNum(host.memory.total_bytes) && host.memory.total_bytes > 0
      ? pct(host.memory.used_bytes / host.memory.total_bytes * 100) : null;
    var gpu = pct(host.gpu && host.gpu.util_pct);
    var d = host.disk || null;
    var disk = d && isNum(d.used_bytes) && isNum(d.total_bytes) && d.total_bytes > 0
      ? pct(d.used_bytes / d.total_bytes * 100) : null;
    var diskFree = d && isNum(d.free_bytes) ? d.free_bytes : null;
    var lowDisk = isNum(diskFree) && diskFree < DISK_LOW_FREE;
    var load1 = Array.isArray(host.load) && isNum(host.load[0]) ? host.load[0] : null;
    var load = isNum(load1) ? load1.toFixed(1) + " / " + (isNum(host.cores) ? host.cores : DASH) : DASH;
    var vals = [cpu, mem, gpu, disk];
    var known = vals.filter(isNum);
    var limit = prev === "busy" ? BUSY_OFF : BUSY_ON;
    var verdict;
    if (lowDisk) verdict = "busy";
    else if (!known.length) verdict = "unknown";
    else if (known.some(function (v) { return v >= limit; })) verdict = "busy";
    else if (known.length < vals.length) verdict = "partial";
    else verdict = "fine";
    return { cpu: cpu, mem: mem, gpu: gpu, disk: disk, diskFree: diskFree, load: load, verdict: verdict };
  }

  function queueHeader(status, nowMs, lang) {
    var q = queueOf(status);
    if (!Array.isArray(q)) return T(lang, "queue.unknown");
    var running = 0, waiting = 0, oldest = null;
    q.forEach(function (r) {
      if (r.state === "running" || r.state === "cancelling") running++;
      else {
        waiting++;
        var w = r.estimate && isNum(r.estimate.waited_seconds) ? r.estimate.waited_seconds : secondsSince(r.created_at, nowMs);
        if (isNum(w) && (oldest == null || w > oldest)) oldest = w;
      }
    });
    var t = T(lang, "queue.counts", { total: q.length, running: running, waiting: waiting });
    if (waiting) t += " · " + T(lang, "queue.oldest_waiting", { dur: fmtDuration(oldest) });
    var busy = busyCount(status), lanes = laneCount(status);
    if (isNum(busy) && isNum(lanes)) t += " · " + T(lang, "queue.lanes_busy", { busy: busy, lanes: lanes });
    return t;
  }

  // running → cancelling → 대기. 실행 중은 레인 순(목업 표·머리 필이 lane 1 → 2), 대기는 position 순, 동률은 id.
  function sortQueue(rows) {
    if (!Array.isArray(rows)) return [];
    function rank(r) { return r.state === "running" ? 0 : r.state === "cancelling" ? 1 : 2; }
    return rows.slice().sort(function (a, b) {
      var d = rank(a) - rank(b);
      if (d) return d;
      var ka = rank(a) < 2 ? a.lane : a.position, kb = rank(b) < 2 ? b.lane : b.position;
      var pa = isNum(ka) ? ka : 1e9, pb = isNum(kb) ? kb : 1e9;
      if (pa !== pb) return pa - pb;
      return (a.id || 0) - (b.id || 0);
    });
  }

  // 원격 워커(M5b-2)의 표시 이름: 서버가 준 display_name, 없으면 `<worker>/<lane>`
  function workerName(w) {
    if (!w || !w.worker) return null;
    return w.display_name || (w.worker + "/" + w.lane);
  }

  /** 워커 상태 낱말. enum 값은 그대로 두고 표시만 바꾼다(CSS 클래스·정렬이 값을 쓴다). */
  /** 그 풀에서 막고 있는 이유를 사람 말로. cpu 로 막힌 레인이 있으면 **최댓값**을 쓴다 —
      창이 [85,70,70] 인데 「cpu 70%」라고 쓰면 거짓말이다. 이유를 모르면 null. */
  function holdWord(status, lang) {
    var workers = ((status || {}).server || {}).workers || [];
    var held = workers.filter(function (w) { return w && w.state === "held"; });
    if (!held.length) return null;
    var busy = [];
    held.forEach(function (w) {
      var v = w.hold_code === "cpu_busy" && w.hold_detail ? w.hold_detail.cpu_busy : null;
      if (isNum(v)) busy.push(v);
    });
    if (busy.length) return T(lang, "hold.cpu_busy", { cpu: Math.round(Math.max.apply(null, busy)) });
    var code = null;
    held.forEach(function (w) { if (!code && w.hold_code) code = w.hold_code; });
    return code && I18N.has("hold." + code) ? T(lang, "hold." + code) : null;
  }

  function workerState(state, lang) {
    if (!state) return DASH;
    var key = "state." + state;
    return I18N.has(key) ? T(lang, key) : state;
  }

  /** 서버가 코드로 말한 요약(결정 37)을 그 언어의 문장으로. 코드가 없으면 저장된 문장 그대로 —
      잡이 `::rcm::summary::` 로 찍은 것은 팀이 쓴 문장이라 번역하지 않는다. */
  function outcomeText(job, lang) {
    var code = job && job.summary_code;
    if (!code) return (job && job.summary) || "";
    var key = "outcome." + code;
    if (!I18N.has(key)) return (job && job.summary) || "";
    try { return T(lang, key, job.summary_args || {}); }
    catch (e) { return (job && job.summary) || ""; }
  }

  function workerPills(server, lang) {
    server = server || {};
    var all = Array.isArray(server.workers) ? server.workers : [];
    var workers = all.filter(function (w) { return !w.worker; });   // 로컬 레인은 오늘 그대로
    var remote = all.filter(function (w) { return !!w.worker; });   // 원격은 이름 필로 뒤에
    var lanes = isNum(server.lanes) ? server.lanes : workers.length;
    var pills = [];
    if (lanes === 1 && workers.length === 1) {
      var w = workers[0];
      pills.push({ text: T(lang, "worker.state", { state: workerState(w.state, lang) }) + (isNum(w.job_id) ? " #" + w.job_id : ""), cls: w.state || "", jobId: isNum(w.job_id) ? w.job_id : null, lane: w.lane });
    } else {
      workers.forEach(function (w) {
        if (w.state === "busy" && isNum(w.job_id)) pills.push({ text: "#" + w.job_id, cls: "busy", jobId: w.job_id, lane: w.lane });
        else pills.push({ text: T(lang, "worker.lane_state", { lane: w.lane, state: workerState(w.state, lang) }), cls: w.state || "", jobId: null, lane: w.lane });
      });
    }
    remote.forEach(function (w) {
      var name = workerName(w);
      var busy = w.state === "busy" && isNum(w.job_id);
      pills.push({ text: name + " " + workerState(w.state, lang) + (busy ? " #" + w.job_id : ""), cls: w.state || "", jobId: busy ? w.job_id : null, lane: w.lane, worker: w.worker });
    });
    if (server.paused) pills.push({ text: T(lang, "worker.paused"), cls: "paused", jobId: null, lane: null });
    return pills;
  }

  // {kind: "reload"|"restart", text} | null — DOM 은 kind 로 띠 색을 고르고, 공개 headerNote 는 §2 대로 문자열만 준다.
  // 버전·스키마 변화가 재시작보다 우선. uptime 이 어느 쪽이든 null 이면 재시작을 주장하지 않는다. prev 없으면(첫 조회) null.
  function headerNoteKind(status, nowMs, prev, lang) {
    if (!status || !prev || typeof prev !== "object") return null;
    if (status.schema_version !== prev.schema_version || (status.server && prev.server && status.server.version !== prev.server.version)) {
      return { kind: "reload", text: T(lang, "note.stale_ui") };
    }
    if (status.server && prev.server && isNum(status.server.uptime_seconds) && isNum(prev.server.uptime_seconds) && status.server.uptime_seconds < prev.server.uptime_seconds) {
      var startMs = parseIso(status.generated_at);
      var tz = status.display_timezone || undefined;
      var at = startMs != null ? fmtClock(new Date(startMs - status.server.uptime_seconds * 1000).toISOString(), tz, nowMs) : DASH;
      return { kind: "restart", text: T(lang, "note.restarted", { clock: at }) };
    }
    return null;
  }
  function headerNote(status, nowMs, prev, lang) {
    var note = headerNoteKind(status, nowMs, prev, lang);
    return note ? note.text : null;
  }

  // ── 진행 (항목 12) · 최근 (항목 14) ──
  function failedStepCount(prog) {
    return Array.isArray(prog && prog.steps) ? prog.steps.filter(function (s) { return s.ok === false; }).length : 0;
  }
  /**
   * 현재 스텝 안의 세부 진행(`::rcm::progress::` · `progress.sub`). 서버가 검증해 보내지만 화면도
   * 분모를 다시 본다 — `total ≥ 1`, `0 ≤ done ≤ total` 이 아니면 **없는 것**이다(분모를 지어내지
   * 않는다). 없으면 null.
   */
  function subProgress(prog) {
    var s = prog && prog.sub;
    if (!s || typeof s !== "object") return null;
    if (!isNum(s.done) || !isNum(s.total) || s.total < 1 || s.done < 0 || s.done > s.total) return null;
    if (typeof s.unit !== "string" || !s.unit) return null;
    return s;
  }
  /** 머리줄 꼬리 「now: <unit> · <state>[ · <note>]」 — 마지막 progress 마커 그대로. 없으면 null. */
  function subNowText(prog, lang) {
    var s = subProgress(prog);
    if (!s) return null;
    var t = T(lang, "progress.sub_now", { unit: s.unit, state: String(s.state || "") });
    if (typeof s.note === "string" && s.note) t += " · " + s.note;
    return t;
  }
  function progressHead(prog, lang) {
    if (!prog || prog.phase === "materializing") return null;
    var steps = Array.isArray(prog.steps) ? prog.steps : [];
    var now = subNowText(prog, lang);
    if (!steps.length) {
      var none = T(lang, "progress.no_markers", { dur: fmtDuration(prog.job_seconds) });
      return now ? none + " · " + now : none;
    }
    var cur = isNum(prog.current_index) ? prog.current_index : prog.steps_done;
    var total = isNum(prog.steps_total) ? prog.steps_total : "?";
    var t = T(lang, "progress.step", { cur: cur, total: total, soFar: !!prog.steps_total_partial });
    if (prog.current_name) t += " · " + prog.current_name + " · " + fmtDuration(prog.current_seconds);
    t += " · " + T(lang, "progress.job", { dur: fmtDuration(prog.job_seconds) });
    var f = failedStepCount(prog);
    if (f) t += " · " + T(lang, "progress.steps_failed", { n: f });
    if (now) t += " · " + now;
    return t;
  }
  /** 도는 스텝(끝나지 않은 마지막 스텝). 없으면 null — 끝난 잡의 초는 올라가면 안 된다. */
  function runningStep(prog) {
    var steps = Array.isArray(prog && prog.steps) ? prog.steps : [];
    var last = steps.length ? steps[steps.length - 1] : null;
    return last && last.state === "running" ? last : null;
  }
  /**
   * 진행 머리줄을 HTML 로. **태그를 벗기면 `progressHead(prog, lang)` 와 글자가 같다**(§4.6-다).
   *
   * `live` 는 「이 잡은 아직 도는 중이다」는 뜻이다(DOM 층이 `state` 로 판단해 넘긴다). 그러면 잡
   * 초가 기준점에서 스스로 오른다. 스텝 초는 **도는 스텝이 있을 때만** — 마지막 스텝이 끝나고
   * 잡이 정리 중일 때 끝난 스텝의 초가 계속 오르면 거짓말이 된다.
   * 서버는 상태 문서를 만든 순간의 초를 보내므로, 그것만 그리면 폴링 간격만큼 숫자가 튄다.
   * 시계 차이를 모르면 DOM 층이 `live=false` 로 부른다 — 조용히 브라우저 시계로 넘어가지 않는다.
   */
  function progressHeadHtml(prog, lang, live) {
    if (!prog || prog.phase === "materializing") return null;
    var steps = Array.isArray(prog.steps) ? prog.steps : [];
    var cur = live ? runningStep(prog) : null;
    // 기준점이 있는 자리만 틱으로 감싼다. 없으면(옛 서버) 서버가 준 숫자를 그대로 둔다.
    var span = function (from, dur) {
      return from ? '<span data-tick="elapsed" data-from="' + esc(from) + '">' + esc(dur) + "</span>" : esc(dur);
    };
    // 「job 1m 2s」의 소요만 틱으로 — 라벨은 언어마다 앞뒤가 달라 자리표시자로 갈아 끼운다
    var JOB = "\u0000job\u0000";
    var jobSpan = function (key) {
      return esc(T(lang, key, { dur: JOB })).replace(JOB, span(live && prog.job_started_at, fmtDuration(prog.job_seconds)));
    };
    // 스텝 안의 세부 진행은 마커가 올 때만 바뀐다 — 틱 없이 글자 그대로
    var now = subNowText(prog, lang);
    var nowHtml = now ? '<span class="sub-now">' + esc(now) + "</span>" : "";
    // 마커가 없는 잡은 머리줄에 잡 초 하나뿐이다 — 그것마저 얼면 도는 잡이 통째로 멈춰 보인다
    if (!steps.length) return jobSpan("progress.no_markers") + (now ? " · " + nowHtml : "");
    var i = isNum(prog.current_index) ? prog.current_index : prog.steps_done;
    var total = isNum(prog.steps_total) ? prog.steps_total : "?";
    var h = esc(T(lang, "progress.step", { cur: i, total: total, soFar: !!prog.steps_total_partial }));
    if (prog.current_name) {
      h += " · " + esc(prog.current_name) + " · " + span(cur && cur.started_at, fmtDuration(prog.current_seconds));
    }
    h += " · " + jobSpan("progress.job");
    var f = failedStepCount(prog);
    if (f) h += " · " + esc(T(lang, "progress.steps_failed", { n: f }));
    if (now) h += " · " + nowHtml;
    return h;
  }
  /** 격자 칸의 글자 — 색만으로 말하지 않는다(§4.2). 모르는 state 는 빈 칸. */
  var UNIT_GLYPH = { ok: "✓", fail: "✗", run: "▶", skip: "—", wait: "…", blocked: "!", env: "!", review: "?" };
  var UNIT_STATES = ["run", "ok", "fail", "skip", "env", "review", "blocked", "wait"];
  /**
   * 현재 스텝 안의 단위 격자(`progress.units[]`) — 단위 하나가 칸 하나, 마지막 상태가 색·글자.
   * 단위가 둘 미만이면 격자가 아니라 빈 문자열이다(하나는 머리줄의 `now:` 가 이미 말했다).
   * 이름은 `title` 과 `aria-label` 에 있다 — 칸은 12px 라 글자를 못 담는다.
   */
  function unitsGridHtml(prog, lang) {
    var units = Array.isArray(prog && prog.units) ? prog.units.filter(function (u) {
      return u && typeof u.unit === "string" && u.unit;
    }) : [];
    if (units.length < 2) return "";
    var h = '<div class="ugrid" role="list" aria-label="' + esc(T(lang, "progress.units_aria", { n: units.length })) + '">';
    units.forEach(function (u) {
      var st = UNIT_STATES.indexOf(u.state) >= 0 ? u.state : "unknown";
      var label = u.unit + " · " + (st === "unknown" ? String(u.state || "?") : st) + (typeof u.note === "string" && u.note ? " · " + u.note : "");
      h += '<span class="u ' + st + '" role="listitem" title="' + esc(label) + '" aria-label="' + esc(label) + '">'
        + '<i aria-hidden="true">' + (UNIT_GLYPH[st] || "") + "</i></span>";
    });
    h += "</div>";
    if (prog.units_truncated) h += '<div class="sub ugrid-more">' + esc(T(lang, "progress.units_more")) + "</div>";
    return h;
  }
  function stepMark(step) {
    if (!step) return "·";
    if (step.state === "running") return "▶";
    if (step.ok === false) return "✘";
    if (step.state === "done") return "✔";
    return "·";
  }

  // ── 전체 진행 막대 (2026-09-09 오너 요청 · 같은 날 Codex 리뷰 반영) ──
  function pctOf(part, whole) { return Math.max(0, Math.min(100, Math.round(part / whole * 100))); }
  // 예측은 99% 를 넘지 않는다 — 100% 는 「끝났다」의 자리다. 반올림도 안 쓴다(59.7/60 이 100% 가 된다).
  function timePct(elapsed, expected) { return Math.max(0, Math.min(99, Math.floor(elapsed / expected * 100))); }
  /**
   * 도는 잡이 어디까지 왔나. **축이 둘이다**(Codex 리뷰 3 — 하나로 뭉치면 「stuck 인데 왜 파란
   * 막대가 절반 차 있나」 같은 규칙을 사람이 외워야 한다):
   *
   * - `basis` — 눈금의 근거. `steps`(잡이 `::rcm::steps::N` 으로 선언한 총계) · `time`(추정 소요
   *   대비 경과 — ETA 칸이 쓰는 그 추정) · `none`(눈금 없음).
   * - `condition` — 잡의 형편. `normal` · `preparing`(워크스페이스 준비) · `stuck` · `over`(추정
   *   초과) · `finalizing`(선언한 스텝을 다 끝냈는데 아직 안 끝남).
   *
   * 눈금을 주지 않는 자리(fail-open 금지):
   * - 「지금까지 본」 총계(`steps_total_partial`)는 분모가 아니다 — 그 8 로 62% 를 쓰면 거짓말이다.
   * - `estimate.source === "default"` 는 표본도 프리셋 값도 없는 설치 기본값(600초)이다. ETA 는
   *   `low · default` 로 스스로를 밝히지만 막대는 길이로만 말하므로, 아예 눈금을 안 준다.
   * - stuck 은 시간 눈금을 잃는다(도는 중인지부터 모른다). 끝난 스텝 수는 stuck 이어도 사실이라
   *   스텝 눈금은 지키되 형편을 라벨과 빗금으로 함께 말한다.
   * - 스텝을 다 끝냈거나 추정을 넘겼으면 퍼센트가 없다 — 파랗게 꽉 찬 막대는 「끝났다」로 읽힌다.
   *
   * 도는 잡이 아니면 null — 막대 자체를 그리지 않는다.
   */
  function overallProgress(row) {
    if (!row || (row.state !== "running" && row.state !== "cancelling")) return null;
    var out = { basis: "none", condition: "normal", pct: null, done: null, total: null,
      expected: null, startedAt: null, source: null };
    var prog = row.progress || null;
    var est = row.estimate || {};
    var stuck = !!est.stuck;
    // 형편의 우선순위: `preparing > stuck > over > finalizing > quiet > normal`.
    // `quiet`(조용함)은 관측이라 가장 약하다 — 「추정을 넘겼다」·「선언한 단계를 다 끝냈다」가
    // 더 행동 가능한 사실이다. 서버가 `stuck` 과 `quiet` 을 같이 보내면 **stuck 이 이긴다**:
    // 경보를 먹는 쪽(quiet 이 이기는 것)은 fail-open 이라 금지다.
    var quiet = !!est.quiet && !stuck;
    if (prog && prog.phase === "materializing") { out.condition = "preparing"; return out; }
    // 스텝 안의 세부 진행이 있으면 그것이 눈금이다 — 스크립트가 **아는** 분모라 선언 스텝보다 곱다.
    // `done == total` 이어도 잡은 아직 돈다(그 스텝의 단위가 다 끝난 것뿐) — 99% 상한이 여기도
    // 적용된다. 100% 는 「끝났다」의 자리다.
    var sub = subProgress(prog);
    if (sub) {
      out.basis = "sub"; out.total = sub.total; out.done = sub.done; out.unit = sub.unit;
      out.pct = Math.min(99, pctOf(sub.done, sub.total));
      if (stuck) out.condition = "stuck";
      else if (quiet) out.condition = "quiet";
      return out;
    }
    var total = prog && isNum(prog.steps_total) ? prog.steps_total : null;
    var done = prog && isNum(prog.steps_done) ? prog.steps_done : null;
    if (isNum(total) && total > 0 && isNum(done) && !(prog && prog.steps_total_partial)) {
      out.basis = "steps"; out.total = total; out.done = Math.min(done, total);
      if (done >= total) { out.condition = stuck ? "stuck" : "finalizing"; return out; }
      out.pct = pctOf(done, total);
      if (stuck) out.condition = "stuck";
      else if (quiet) out.condition = "quiet";
      return out;
    }
    var expected = isNum(est.expected_seconds) && est.expected_seconds > 0 && est.source !== "default"
      ? est.expected_seconds : null;
    var elapsed = isNum(est.elapsed_seconds) ? est.elapsed_seconds : null;
    if (expected == null || elapsed == null) {
      if (stuck) out.condition = "stuck";
      else if (quiet) out.condition = "quiet";
      return out;
    }
    out.expected = expected; out.source = est.source || null;
    if (stuck) { out.condition = "stuck"; return out; }
    if (est.overdue || elapsed >= expected) { out.condition = "over"; return out; }
    out.basis = "time"; out.pct = timePct(elapsed, expected); out.startedAt = row.started_at || null;
    // 조용한 것과 진행률을 모르는 것은 다른 일이다 — 눈금(`basis`·`pct`)은 그대로 둔다.
    if (quiet) out.condition = "quiet";
    return out;
  }
  /** 시간 눈금의 라벨 키 — 추정이 어디서 왔는지 문구가 밝힌다(Codex 리뷰 1). */
  function timeKey(source) {
    return source === "measured" ? "pbar.time_measured" : source === "preset" ? "pbar.time_preset" : "pbar.time";
  }
  /**
   * 행 아래에 붙는 전체 진행 막대. 태그를 벗기면 눈금과 근거가 글자로 남는다 — 길이와 색만으로
   * 말하지 않는다(§4.2). `live` 는 「시계 차이를 알고 이 잡은 아직 돈다」는 뜻이고, **평상시 시간
   * 눈금일 때만** 1초 틱의 기준점을 단다(스텝 눈금은 마커가 올 때 움직이지 저절로 자라지 않는다).
   */
  function progressBarHtml(row, lang, live) {
    var p = overallProgress(row);
    if (!p) return "";
    var head = null;
    if (p.basis === "sub") {
      head = T(lang, "pbar.sub", { percent: p.pct, done: p.done, total: p.total, unit: p.unit });
    } else if (p.basis === "steps") {
      head = isNum(p.pct)
        ? T(lang, "pbar.steps", { percent: p.pct, done: p.done, total: p.total })
        : T(lang, "pbar.steps_all", { done: p.done, total: p.total });
    } else if (p.basis === "time") {
      head = T(lang, timeKey(p.source), { percent: p.pct });
    }
    // 한 작업의 이상은 **한 곳에서만** 말한다. 형편(`조용함`·`응답 없음`…)은 이미 같은 행의 상태
    // 칩과 상태 칸이 두 번 말했다 — 라벨이 세 번째로 말하면서 84px 칸 안에서 두 줄로 접혀 행 높이를
    // 이웃보다 30% 키웠다(2026-09-15 실측: 응답 없음 행 78px vs 이웃 60px, 게다가 어구 한가운데서
    // 끊겼다). 그래서 **화면 글자는 눈금과 근거만** 말하고, 형편은 막대의 색·빗금(`data-cond`)이
    // 말한다. 뺀 것은 픽셀이지 사실이 아니다 — `aria-valuetext` 와 `title` 에는 그대로 남는다.
    var cond = p.condition === "normal" ? null : T(lang, "pbar." + p.condition);
    var none = T(lang, "pbar.none");
    var spoken = [head, cond].filter(Boolean).join(" · ") || none;
    var label = head || none;
    var full = p.condition === "over" || p.condition === "finalizing";
    var width = isNum(p.pct) ? p.pct : (full ? 100 : 0);
    // 근거·형편은 **data 속성**으로 싣는다. class 로 두면 `steps`·`stuck`·`over` 가 화면의 다른
    // 규칙(스텝 목록 격자 · 이유 칸 칩)에 걸려 막대가 엉뚱한 폭으로 그려진다 — 실제로 그랬다.
    // 조용해도 시간 눈금은 계속 자란다 — 「출력이 없다」와 「시계가 멈췄다」는 다른 말이다.
    var tick = live && p.basis === "time" && (p.condition === "normal" || p.condition === "quiet") && p.startedAt
      ? ' data-tick="progress" data-from="' + esc(p.startedAt) + '" data-expected="' + p.expected
        + '" data-source="' + esc(p.source || "") + '"'
      : "";
    return '<div class="pwrap"' + tick + '><div class="pbar" data-basis="' + p.basis + '" data-cond="'
      + p.condition + '" role="progressbar"'
      + ' aria-valuemin="0" aria-valuemax="100"' + (isNum(p.pct) ? ' aria-valuenow="' + p.pct + '"' : "")
      + ' aria-valuetext="' + esc(spoken) + '" aria-label="' + esc(T(lang, "pbar.aria", { id: row.id })) + '">'
      + '<i data-fill="' + width + '"></i></div><span class="plab" title="' + esc(spoken) + '">'
      + esc(label) + "</span></div>";
  }
  // 산출물 한 줄(M5e §13). 모르는 수는 —, `0` 은 「모았는데 없었다」일 때만이다. 파일 이름은
  // 공개 문서에 없으므로 여기서도 없다 — 받아 가는 명령만 준다.
  function artifactsLine(job, lang, nowMs) {
    var a = (job || {}).artifacts;
    if (!a || !a.state) return null;
    if (a.state === "disabled" || a.state === "pending") return null;
    var parts = [T(lang, "art.label"), T(lang, "art.state." + a.state) || a.state];
    if (a.state === "ready" || a.state === "empty") {
      parts.push(isNum(a.file_count) ? T(lang, "art.files", { n: a.file_count }) : DASH);
      parts.push(isNum(a.bundle_bytes) ? fmtBytes(a.bundle_bytes) : DASH);
    }
    if (a.reason_code) parts.push(T(lang, "art.reason." + a.reason_code) || a.reason_code);
    var left = parseIso(a.expires_at);
    if (a.state === "ready" && left !== null && isNum(nowMs)) {
      parts.push(T(lang, "art.left", { dur: fmtDuration(Math.max(0, (left - nowMs) / 1000)) }));
    }
    return {
      text: parts.join(" · "),
      cls: a.state,
      command: a.state === "ready" ? "rcm artifacts " + job.id + " --fetch --output ." : null
    };
  }

  function recentLine(job, tz, nowMs, lang) {
    job = job || {};
    var state = job.state;
    var pill;
    if (state === "succeeded") pill = T(lang, "recent.succeeded");
    else if (state === "failed") pill = isNum(job.exit_code) ? T(lang, "recent.failed_exit", { code: job.exit_code }) : T(lang, "recent.failed");
    else if (state === "cancelled") pill = T(lang, "recent.cancelled");
    else if (state === "timed_out") pill = T(lang, "recent.timed_out");
    else if (state === "lost") pill = T(lang, "recent.lost");
    else pill = stateWord(state, lang);
    var summary = job.summary || "";
    if (state === "cancelled" && !job.started_at) {
      var who = job.cancelled_by ? personLabel(job, job.cancelled_by) : null;
      summary = T(lang, "recent.before_start") + (who ? " · " + T(lang, "recent.by", { who: who }) : "");
    } else if (state === "lost") {
      summary = outcomeText(job, lang) || T(lang, "state.lost");
    } else if (job.failed_step || job.last_step) {
      // 선언된 스텝만 「스텝」이다. 아니면 「마지막 스텝」 — 인과를 주장하지 않는다(M5h 결정 63).
      var stepKey = job.failed_step ? "recent.step" : "recent.last_step";
      var stepName = job.failed_step || job.last_step;
      summary = (outcomeText(job, lang) ? outcomeText(job, lang) + " · " : "") + T(lang, stepKey, { step: stepName });
    } else {
      summary = outcomeText(job, lang) || "";
    }
    return {
      pill: pill, glyph: stateGlyph(state), cls: state || "",
      duration: fmtDuration(job.job_seconds),
      when: fmtClock(job.finished_at, tz, nowMs),
      summary: summary,
      rerun: (state === "failed" || state === "timed_out") ? rerunCommand(job) : null
    };
  }
  // 셸에 붙여 넣는 값이라 안전한 문자만 그대로, 나머지는 작은따옴표로 감싼다(다른 사용자의 입력값·ref 다)
  function shellQuote(v) {
    var s = String(v);
    if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(s)) return s;
    return "'" + s.replace(/'/g, "'\\''") + "'";
  }
  // ── 우선순위 칩 · 캐시 요약 (M5) — 우선순위는 이유가 아니다, 칩만 ──
  function priorityChip(row, lang) {
    var p = row && row.priority;
    if (typeof p !== "number" || p === 0) return "";
    if (p > 0) return '<span class="chip prio high">' + esc(T(lang, "priority.high")) + '</span>';
    return '<span class="chip prio low">' + esc(T(lang, "priority.low")) + '</span>';
  }
  function cacheText(server, lang) {
    var c = server && server.snapshot_cache;
    if (!c || typeof c !== "object") return null;
    var blobs = isNum(c.blobs) ? String(c.blobs) : DASH;
    var mb = isNum(c.bytes) ? fmtBytes(c.bytes) : DASH;
    return T(lang, "cache.text", { blobs: blobs, size: mb });
  }
  function rerunCommand(job) {
    if (!job || !job.preset) return DASH;  // 빈 명령을 복사하게 두지 않는다
    var cmd = "rcm run " + job.preset;
    var inputs = job.inputs || {};
    Object.keys(inputs).forEach(function (k) {
      var v = inputs[k];
      cmd += " -f " + k + "=" + shellQuote(typeof v === "boolean" ? (v ? "true" : "false") : String(v));
    });
    var src = job.source || {};
    if (src.mode === "git_ref" && src.ref) cmd += " --ref " + shellQuote(src.ref);  // git_ref 잡은 --ref 없이는 usage 오류
    return cmd;
  }
  //: 상세에 그리는 이름 줄 수. 넘치면 `failures.more` 한 줄로 접는다(CLI 는 3줄이다).
  var RECENT_FAILURES_SHOWN = 8;

  /* 최근 행 상세의 줄들 — 순수(§2.6). 스텝 라벨은 **선언된 것만** 「실패한 스텝」이고,
     아니면 「마지막 스텝」이다(M5h 결정 63). 이름별 최근 이력은 서버가 코드로 준 판정을
     문장으로만 바꾼다(결정 37) — 여기서 다시 계산하지 않는다. */
  function recentDetail(job, lang) {
    job = job || {};
    var out = [];
    if (job.failed_step) out.push(T(lang, "recent.failed_step") + job.failed_step);
    else if (job.last_step) out.push(T(lang, "recent.last_step_label") + job.last_step);
    var items = Array.isArray(job.failures) ? job.failures.filter(function (f) {
      return f && typeof f === "object" && f.name;
    }) : [];
    if (items.length) {
      out.push(T(lang, "failures.title"));
      // 이름은 100개까지 올 수 있다(§4.2) — 상세가 스크롤 한 화면을 먹지 않게 자른다
      var shown = items.slice(0, RECENT_FAILURES_SHOWN);
      shown.forEach(function (f) {
        var key = "failures." + f.verdict;
        var known = I18N.has(key) && isNum(f.window) && (f.verdict !== "intermittent" || isNum(f.seen));
        var text = known ? T(lang, key, { seen: f.seen, window: f.window }) : "";
        // 스텝인지 단위(테스트·파일)인지는 **여기서** 구분한다 — CLI 는 한 모양이다(§2.5)
        var name = f.step ? f.name + " (" + T(lang, "failures.step_kind") + ")" : f.name;
        out.push(text ? name + " — " + text : name);
      });
      if (items.length > shown.length) out.push(T(lang, "failures.more", { n: items.length - shown.length }));
      // 분모의 품질은 어느 줄에 실려 와도 읽는다 — 서버는 줄마다 같은 값을 싣는다(결정 68)
      var unnamed = 0;
      items.forEach(function (f) { if (isNum(f.window_unnamed) && f.window_unnamed > unnamed) unnamed = f.window_unnamed; });
      if (unnamed) out.push(T(lang, "failures.unnamed", { n: unnamed }));
    }
    return out;
  }

  function transitionsLine(job, tz, lang) {
    var tr = Array.isArray(job && job.transitions) ? job.transitions : [];
    if (!tr.length) return DASH;
    var parts = [];
    for (var i = 0; i < tr.length; i++) {
      var t = tr[i];
      var seg = stateWord(t.state, lang);
      if (t.state === "queued") {
        var next = tr[i + 1];
        if (next && next.state === "running") {
          var waited = secondsSince(t.at, parseIso(next.at));
          if (isNum(waited)) seg += " " + T(lang, "transitions.waited", { dur: fmtDuration(waited) });
        } else seg += " " + fmtClockSeconds(t.at, tz);
      } else seg += " " + fmtClockSeconds(t.at, tz);
      parts.push(seg);
    }
    var line = parts.join(" → ");
    if (isNum(job.exit_code)) line += " · exit " + job.exit_code;
    return line;
  }

  // ── 갱신 상태기계 (3절) ──
  function nextBackoff(attempt) { return BACKOFF[Math.min(Math.max(0, attempt | 0), BACKOFF.length - 1)]; }
  function connection(prev, event, nowMs) {
    var s = prev ? {
      mode: prev.mode, attempt: prev.attempt || 0, lastOkAt: prev.lastOkAt == null ? null : prev.lastOkAt,
      sseOpen: !!prev.sseOpen, before: prev.before || null, retryIn: prev.retryIn == null ? null : prev.retryIn
    } : { mode: "polling", attempt: 0, lastOkAt: null, sseOpen: false, before: null, retryIn: null };
    switch (event) {
      case "status_ok":
        s.lastOkAt = nowMs;
        if (s.mode === "lost") s.mode = s.sseOpen ? "live" : "polling";
        break;
      case "sse_open":
        s.sseOpen = true; s.attempt = 0; s.retryIn = null;
        if (s.mode !== "paused") s.mode = "live";
        break;
      case "sse_error":
        s.sseOpen = false;
        s.retryIn = nextBackoff(s.attempt);
        s.attempt += 1;
        if (s.mode === "live") s.mode = "polling";
        break;
      case "tick":
        if (s.mode !== "paused" && isNum(s.lastOkAt) && isNum(nowMs) && nowMs - s.lastOkAt > LOST_AFTER_MS) s.mode = "lost";
        break;
      case "manual_pause":
      case "hidden_60s":
        if (s.mode !== "paused") { s.before = s.mode; s.mode = "paused"; }
        break;
      case "manual_resume":
      case "visible":
        if (s.mode === "paused") { s.mode = s.before || (s.sseOpen ? "live" : "polling"); s.before = null; }
        break;
      default: break;
    }
    return s;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 스토어 탭 — 순수 함수 (docs/wireframes/web-store.html 항목 1~6 · 25 · 29~35 · 42, 7절).
  // 서버 계약은 STORE-TAB-API(`/api/repos` · `/api/repos/<name>` · `…/secrets` · `…/verify` ·
  // `…/fetch`). 값(비밀)은 어디에도 오지 않는다 — 여기 오는 것은 present · fingerprint · verified_at 뿐.
  // ═══════════════════════════════════════════════════════════════════════════
  var ROW_GLYPH = { ok: "✓", bad: "✗", running: "▶", stale: "⏱", na: "·" };
  var STORE_STALE_SECONDS = 30 * 60;   // 미러 나이 30분 — 목업 항목 4·42 (황토, 빨강이 아니다)
  var VALUE_FP_CHARS = 4;              // 값 비밀의 지문은 앞 4자 + … 를 넘지 않는다(항목 31)

  /** 해시 → 화면. 스토어 탭은 셋으로 갈린다(워크플랜 §3.1):
      `#/store/<repo>` 버전 목록 · `#/store/<repo>/v/<id>` 버전 하나 · `#/store/<repo>/status` 상태.
      나머지(`#/` · `#/jobs/N` · 빈 값)는 큐다. `sub` 는 스토어일 때 언제나 있고 `id` 는 버전일 때만. */
  function parseRoute(hash) {
    var m = /^#\/store\/([^\/?#]+)(?:\/(?:(status)|v\/(\d+)))?\/?$/.exec(hash || "");
    if (!m) return { view: "queue", repo: null, sub: null, id: null };
    var repo;
    try { repo = decodeURIComponent(m[1]); } catch (e) { repo = m[1]; }
    if (m[2]) return { view: "store", repo: repo, sub: "status", id: null };
    if (m[3]) return { view: "store", repo: repo, sub: "version", id: parseInt(m[3], 10) };
    return { view: "store", repo: repo, sub: "versions", id: null };
  }
  /** 스토어 화면의 해시를 만드는 유일한 자리 — `parseRoute` 와 짝이다. */
  function storeHash(repo, sub, id) {
    var base = "#/store/" + encodeURIComponent(repo);
    if (sub === "status") return base + "/status";
    if (sub === "version") return base + "/v/" + encodeURIComponent(String(id));
    return base;
  }
  /** `GET /api/repos` 문서에서 릴리스 프로파일이 있는 저장소만. 문서가 이상하면 빈 배열(탭 없음). */
  function releaseRepos(doc) {
    var list = doc && Array.isArray(doc.repos) ? doc.repos : [];
    return list.filter(function (r) { return r && r.release === true && typeof r.name === "string" && r.name; });
  }
  /** 관문 띠(항목 29). `setup` 이 없으면 완료가 아니고 숫자는 — 다(모르는 값을 0 으로 안 그린다). */
  function setupSummary(setup, lang) {
    var s = setup || {};
    var complete = s.complete === true;
    var n = isNum(s.present) ? s.present : DASH, total = isNum(s.required) ? s.required : DASH, done = isNum(s.verified) ? s.verified : DASH;
    return { complete: complete, tone: complete ? "ok" : "bad", text: T(lang, "store.gate.counts", { n: n, total: total, done: done }) };
  }
  /** 지문 표시 규칙(항목 30·31): 없으면 —. 값은 앞 4자 + …(서버가 더 보내도 더 안 보인다).
      파일은 sha256 앞 8자 + 크기. 폴더는 「n of m files」. */
  function fingerprintText(item, lang) {
    if (!item) return DASH;
    // 폴더의 「n of m files」는 값이 아니라 개수다 — 다 안 찼어도(present=false) 어디까지 찼는지 보인다
    if (item.kind === "dir") {
      var c = dirFileCounts(item);
      return c.total > 0 ? T(lang, "secrets.files_count", { n: c.present, total: c.total }) : DASH;
    }
    if (item.present !== true) return DASH;
    var fp = typeof item.fingerprint === "string" ? item.fingerprint.replace(/…$/, "") : "";
    if (!fp) return DASH;
    if (item.kind === "value") return fp.slice(0, VALUE_FP_CHARS) + "…";
    var text = fp.slice(0, 8);
    if (isNum(item.size)) text += " · " + fmtBytes(item.size);
    return text;
  }
  /** 폴더 비밀의 파일 목록. 서버는 `files: [{name, present, size?, fingerprint?}]` 를 준다 — 문자열
      배열이 오면 이름만 알고 있음/없음은 모르는 것이라 present=false 로 둔다(fail-open 금지). */
  function dirFiles(item) {
    var list = item && Array.isArray(item.files) ? item.files : [];
    return list.map(function (f) {
      if (typeof f === "string") return { name: f, present: false, size: null, fingerprint: null };
      return { name: String(f && f.name || ""), present: !!(f && f.present === true), size: isNum(f && f.size) ? f.size : null, fingerprint: f && typeof f.fingerprint === "string" ? f.fingerprint : null };
    }).filter(function (f) { return f.name; });
  }
  function dirFileCounts(item) {
    var files = dirFiles(item);
    return { present: files.filter(function (f) { return f.present; }).length, total: files.length };
  }
  /** 검증 칸(항목 32): 검증 없음 / 아직 / ✓ 시각 / ✗ 이유. 없는 비밀은 검증할 것도 없다(—). */
  function verifiedCell(item, lang, tzName, nowMs) {
    if (!item || item.present !== true) return { tone: "none", text: DASH };
    if (item.verify === "none" || item.verify == null) return { tone: "none", text: T(lang, "secrets.no_verify") };
    if (item.verify_error) return { tone: "bad", text: T(lang, "secrets.verify_error", { detail: String(item.verify_error) }) };
    if (item.verified_at) return { tone: "ok", text: T(lang, "secrets.verified_at", { clock: fmtClock(item.verified_at, tzName, nowMs) }) };
    return { tone: "pending", text: T(lang, "secrets.not_verified") };
  }
  /** 비밀 표 한 행(항목 31). 값은 절대 안 들어온다 — 들어와도 여기서 흘리지 않는다. */
  function secretRowModel(item, lang, tzName, nowMs) {
    var kind = item && ["value", "file", "dir"].indexOf(item.kind) >= 0 ? item.kind : "value";
    var present = !!(item && item.present === true);
    var files = kind === "dir" ? dirFiles(item).map(function (f) {
      var sub = { kind: "file", present: f.present, fingerprint: f.fingerprint, size: f.size };
      return { name: f.name, present: f.present, fingerprint: fingerprintText(sub, lang) };
    }) : [];
    return {
      name: String(item && item.name || ""), kind: kind, kindWord: T(lang, "secrets.kind." + kind),
      optional: !!(item && item.optional === true), present: present,
      presentWord: T(lang, present ? "secrets.present" : "secrets.missing"), presentGlyph: present ? "✓" : "✗",
      fingerprint: fingerprintText(item, lang), verified: verifiedCell(item, lang, tzName, nowMs),
      // 값은 대화상자, 파일은 드롭존, 폴더는 파일마다 드롭존(자기 행에는 입력이 없다)
      input: kind, action: present ? "replace" : "add", files: files
    };
  }
  /** 드롭존 수용 규칙(항목 30): 하나만 · 비어 있지 않게 · `max_kb` 안. 이름은 자리가 정하므로 안 본다. */
  function dropAccept(item, files, isAdmin) {
    var list = files ? Array.prototype.slice.call(files) : [];
    if (isAdmin === false) return { ok: false, reason: "secrets.reject.admin", args: {} };
    if (list.length === 0) return { ok: false, reason: "secrets.reject.none", args: {} };
    if (list.length > 1) return { ok: false, reason: "secrets.reject.many", args: {} };
    var f = list[0];
    if (!isNum(f.size) || f.size <= 0) return { ok: false, reason: "secrets.reject.empty", args: {} };
    var maxKb = item && isNum(item.max_kb) ? item.max_kb : null;
    if (maxKb != null && f.size > maxKb * 1024) return { ok: false, reason: "secrets.reject.big", args: { limit: fmtBytes(maxKb * 1024) } };
    return { ok: true, reason: null, args: {} };
  }
  /** 미러 나이(초). `fetched_at` 이 있으면 지금 시각으로 세고, 없으면 서버의 `age_seconds`, 둘 다 없으면 null. */
  function mirrorAge(mirror, nowMs) {
    var m = mirror || {};
    var t = parseIso(m.fetched_at);
    if (t != null && isNum(nowMs)) return Math.max(0, (nowMs - t) / 1000);
    if (isNum(m.age_seconds)) return m.age_seconds;
    return null;
  }
  /** 행 색(7절): ok 초록 접힘 · bad 빨강 펼침 · running 파랑 펼침 · stale 황토 펼침 · na 회색 접힘.
      Source(항목 4·25·42): fetch 실패 > main ⊄ dev > 안 가져옴 > 30분 넘음 > 브랜치 모름 > ok.
      Build·Store 행은 이 빌드에 자료가 없어 늘 na 다(다음 PR). */
  function rowState(kind, ctx) {
    ctx = ctx || {};
    if (kind === "setup") return ctx.setup && ctx.setup.complete === true ? "ok" : "bad";
    if (kind === "source") {
      var doc = ctx.doc || {}, br = doc.branches || {};
      if (ctx.fetchError) return "bad";
      if (br.main_in_dev === false) return "bad";
      var age = mirrorAge(doc.mirror, ctx.nowMs);
      if (age == null) return "stale";
      if (age > STORE_STALE_SECONDS) return "stale";
      if (br.main_in_dev !== true) return "na";
      return "ok";
    }
    return "na";
  }
  /** 펼침: 사람이 여닫은 기억(`"open"`/`"closed"`)이 이기고, 없으면 색이 정한다. */
  function rowOpen(state, remembered) {
    if (remembered === "open") return true;
    if (remembered === "closed") return false;
    return state === "bad" || state === "running" || state === "stale";
  }
  /** Setup 행 머리(항목 3): n/m 있음 · 마지막 검증 시각 · 빌드번호 정책. */
  /** 검증 시각 — ISO 글자나 epoch 숫자(초·밀리초) 어느 쪽이든. 모르는 꼴은 null. */
  function verifiedMs(v) {
    if (isNum(v)) return v > 1e12 ? v : v * 1000;
    return parseIso(v);
  }
  /** 마지막 검증 시각 — 종류(value · file · dir)를 가리지 않고, 폴더는 파일마다의 시각도 본다. */
  function latestVerified(items) {
    var latest = null;
    var see = function (v) { var t = verifiedMs(v); if (t != null && (latest == null || t > latest)) latest = t; };
    (items || []).forEach(function (it) {
      if (!it) return;
      see(it.verified_at);
      if (it.verify_detail && typeof it.verify_detail === "object") see(it.verify_detail.verified_at);
      (Array.isArray(it.files) ? it.files : []).forEach(function (f) { if (f && typeof f === "object") see(f.verified_at); });
    });
    return latest;
  }
  /** «검사 안 함» 개수 — setup.not_checked 나 항목의 verify_detail.not_checked(숫자) 를 더한다. 없으면 0. */
  function notCheckedCount(setup, items) {
    var n = 0;
    if (setup && isNum(setup.not_checked)) n += setup.not_checked;
    (items || []).forEach(function (it) {
      var d = it && it.verify_detail;
      if (d && typeof d === "object" && isNum(d.not_checked)) n += d.not_checked;
    });
    return n;
  }
  function setupHead(setup, items, profile, lang, tzName, nowMs) {
    var s = setup || {};
    var latest = latestVerified(items);
    var head = T(lang, "row.setup_head", {
      n: isNum(s.present) ? s.present : DASH, total: isNum(s.required) ? s.required : DASH,
      clock: latest != null ? fmtClock(new Date(latest).toISOString(), tzName, nowMs) : DASH,
      kind: profile && profile.build_number_policy ? String(profile.build_number_policy) : DASH
    });
    var nc = notCheckedCount(setup, items);
    return nc > 0 ? head + " · " + T(lang, "row.setup_not_checked", { n: nc }) : head;
  }
  /** Source 행 머리(항목 4·25): main sha7 · main ⊂ dev · 미러 나이(· fetch 실패). 조각 배열로 준다. */
  function sourceHead(doc, ctx, lang) {
    ctx = ctx || {};
    var d = doc || {}, br = d.branches || {};
    var parts = [];
    parts.push("main " + (typeof br.main === "string" && br.main ? br.main.slice(0, 7) : DASH));
    if (br.main_in_dev === true) parts.push(T(lang, "source.main_in_dev"));
    else if (br.main_in_dev === false) parts.push(T(lang, "source.main_not_in_dev"));
    else parts.push(T(lang, "source.branches_unknown"));
    var age = mirrorAge(d.mirror, ctx.nowMs);
    if (age == null) parts.push(T(lang, "source.never_fetched"));
    else if (age > STORE_STALE_SECONDS) parts.push(T(lang, "source.stale", { age: fmtAgo(age, lang) }));
    else parts.push(T(lang, "source.fetched", { age: fmtAgo(age, lang) }));
    if (ctx.fetchError) parts.push(T(lang, "source.fetch_failed", { detail: String(ctx.fetchError).slice(0, 60) }));
    return parts;
  }
  /** Build·Store 행 머리: 프로파일에 역할 프리셋이 비었으면 「not configured — …」, 아니면 이 빌드에 없음. */
  function naRowText(kind, profile, lang) {
    var presets = profile && profile.presets ? profile.presets : {};
    var role = kind === "build" ? "upload" : "plan";
    if (!presets[role]) return T(lang, "row.not_configured", { name: "presets." + role });
    return T(lang, kind === "build" ? "row.na.build" : "row.na.store");
  }
  // ── 버전 목록 · 새 버전 · 상태 띠 (docs/version-page-workplan.md §3.1 · §11 · §12 · §15) ──
  // rcm 은 스토어를 모른다. 라이브 이름도 힌트도 막힘도 **프로젝트 스크립트가 쓴 `plan.json`** 에서
  // 오고, 여기서는 그 값을 그리기만 한다. 서버의 `release_state.py` 에 같은 규칙이 파이썬으로 한 벌
  // 더 있다(대화상자가 보내기 전에, 서버가 받고 나서 — 둘 다 같은 답을 내야 한다).
  var VERSION_POLL_MS = 5000;          // 버전 상세만, «만드는 중» 일 때만 (§15)
  var VERSION_NAME_RE = /^\d+\.\d+\.\d+$/;
  //: 행 상태 → 필 색. `new` 는 편집 중(청록), `run` 은 만드는 중·진행 중.
  var VERSION_TONE = { creating: "running", editing: "new", running: "running",
    submitted: "ok", discarded: "na", failed: "bad" };

  /** `1.1.0` → `1.1.1` · `2.0` → `2.1`. 끝이 정수가 아니면(`1.0.0-rc1`) null — 지어내지 않는다. */
  function bumpLastNumber(name) {
    if (typeof name !== "string") return null;
    var m = /^((?:\d+\.)*)(\d+)$/.exec(name.trim());
    return m ? m[1] + (parseInt(m[2], 10) + 1) : null;
  }
  /** 계획 문서가 말하는 라이브 이름 — iOS 는 `store.asc_live`, Android 는 `store.play.production_name`. */
  function liveVersions(planDoc) {
    var store = planDoc && typeof planDoc.store === "object" && planDoc.store ? planDoc.store : {};
    var play = store.play && typeof store.play === "object" ? store.play : {};
    var text = function (v) { return typeof v === "string" && v.trim() ? v : null; };
    return { ios: text(store.asc_live), android: text(play.production_name) };
  }
  /** 다음 버전 이름의 힌트. 서버가 이미 계산해 주지만(`GET …/release/versions` 의 `hints`) 화면도
      플랜 문서 하나로 같은 답을 낼 수 있어야 한다 — 문서의 `next_version_hint` 가 있으면 그대로,
      없으면 라이브 이름의 마지막 정수 +1. 모르면 null 이다. */
  function nextVersionHint(planDoc) {
    var given = planDoc && typeof planDoc.next_version_hint === "object" && planDoc.next_version_hint ? planDoc.next_version_hint : {};
    var live = liveVersions(planDoc);
    var pick = function (p) {
      var hint = typeof given[p] === "string" && given[p].trim() ? given[p] : null;
      return hint != null ? hint : bumpLastNumber(live[p]);
    };
    return { ios: pick("ios"), android: pick("android") };
  }
  /** 새 버전 이름 검사 — `{ok, reason}`. `empty` · `pattern`(major.minor.patch 정수 셋) ·
      `not_greater`(라이브를 아는 스토어에서만). 라이브가 숫자 꼴이 아니면 크기는 안 본다. */
  function versionNameCheck(name, live) {
    if (typeof name !== "string" || !name.trim()) return { ok: false, reason: "empty" };
    var value = name.trim();
    if (!VERSION_NAME_RE.test(value)) return { ok: false, reason: "pattern" };
    var parts = function (v) {
      var out = String(v).trim().split(".").map(function (p) { return /^\d+$/.test(p) ? parseInt(p, 10) : null; });
      return out.indexOf(null) >= 0 ? null : out;
    };
    var mine = parts(value), theirs = typeof live === "string" && live ? parts(live) : null;
    if (mine && theirs) {
      for (var i = 0; i < Math.max(mine.length, theirs.length); i++) {
        var a = mine[i] || 0, b = theirs[i] || 0;
        if (a > b) return { ok: true, reason: null };
        if (a < b) return { ok: false, reason: "not_greater" };
      }
      return { ok: false, reason: "not_greater" };
    }
    return { ok: true, reason: null };
  }
  /** 두 스토어 이름을 한 줄로(§11): 언제나 둘 다, 같으면 하나. 스토어 이름은 식별자라 번역하지 않는다. */
  function versionTitle(ios, android) {
    var a = typeof ios === "string" && ios ? ios : null;
    var b = typeof android === "string" && android ? android : null;
    if (a && b) return a === b ? a : "iOS " + a + " · Android " + b;
    if (a) return "iOS " + a;
    if (b) return "Android " + b;
    return DASH;
  }
  /** 이 버전을 붙잡고 있는 것들(§15) — 살아 있는 회차 · 올리는 작업 · 심사 작업. 행이 실어 준
      id 만 말한다: 무엇이 도는지는 **글자로** 보여야 한다(hover 의 title 뿐이면 키보드·스크린
      리더 쓰는 사람은 이유를 못 본다 — C 단계 격리 검증 1). 없으면 빈 배열이다. */
  function versionHolders(row, lang) {
    var r = row || {}, out = [];
    if (r.release_id != null) out.push(T(lang, "version.hold.round", { id: r.release_id }));
    if (r.upload_job_id != null) out.push(T(lang, "version.hold.upload", { id: r.upload_job_id }));
    if (r.review_job_id != null) out.push(T(lang, "version.hold.review", { id: r.review_job_id }));
    return out;
  }
  /** 드래프트 행 하나 — 필 · 만든 지 · 바뀐 칸 수 · 빌드 유무 · 만료(§14-5 · E13) · 실패 사유.
      버튼은 서버가 실제로 받아 주는 것만 연다: 도는 중이면 버리기가 409 `version_running` 이고
      (`discard_version`), 만들기가 실패한 행은 이름을 안 붙잡으므로 같은 이름으로 다시 만든다(§15).
      «지우는 중» 은 상태 열에 없다 — 서버는 `delete_job_id` 만 채우고 잡이 0 으로 끝나야 행을
      `discarded` 로 닫는다. 그 사이 화면이 «편집 중 · 열기 · 버리기» 로 남아 있으면 누른 사람은
      아무 일도 안 일어난 줄 안다(C 단계 격리 검증 2). 잡이 끝나면 저절로 풀린다: 성공하면 행이
      닫혀 목록에서 빠지고, 실패하면 `error` 가 채워져 그 줄이 사유를 말한다. */
  function versionRowModel(row, lang, nowMs) {
    var r = row || {};
    var st = typeof r.state === "string" ? r.state : "editing";
    var created = secondsSince(r.created_at, nowMs);
    var changed = isNum(r.changed) ? r.changed : 0;
    var hasBuild = r.upload_job_id != null || r.release_id != null;
    var expired = r.expired === true || r.expiry_warned === true || r.expiry_warned === 1;
    var deleting = r.delete_job_id != null && !r.error && st !== "submitted" && st !== "discarded";
    var notes = [];
    if (deleting) notes.push(T(lang, "version.row.deleting", { id: r.delete_job_id }));
    if (st === "creating") notes.push(T(lang, "version.row.creating", { id: r.create_job_id != null ? r.create_job_id : DASH }));
    // 최상단 막대가 사라졌으니(결정 Q6) 도는 회차는 **이 행**에서 보여야 한다. 무엇이 붙잡고
    // 있는지도 말한다 — 회차 · 올리는 작업 · 심사 작업 셋 중 마지막 하나가 끝나야 «편집 중» 이다.
    if (st === "running") {
      var holders = versionHolders(r, lang);
      notes.push(holders.length ? T(lang, "version.row.running", { what: holders.join(" · ") })
        : T(lang, "version.row.running_plain"));
    }
    if (created != null) notes.push(T(lang, "version.row.created", { age: fmtCoarse(created) }));
    notes.push(changed > 0 ? T(lang, "version.row.changed", { n: changed }) : T(lang, "version.row.unchanged"));
    notes.push(T(lang, hasBuild ? "version.row.build" : "version.row.no_build"));
    if (expired) notes.push(T(lang, "version.row.expired"));
    if (r.error) notes.push(T(lang, "version.row.error", { detail: String(r.error).slice(0, 120) }));
    return {
      id: r.id, state: st, deleting: deleting, title: versionTitle(r.ios_version, r.android_version),
      ios: r.ios_version || null, android: r.android_version || null,
      tone: deleting ? "running" : VERSION_TONE[st] || "na",
      pill: deleting ? T(lang, "version.state.deleting") : T(lang, "version.state." + (VERSION_TONE[st] ? st : "editing")),
      ageSeconds: created, changed: changed, hasBuild: hasBuild, expired: expired,
      error: r.error || null, createJobId: r.create_job_id != null ? r.create_job_id : null,
      deleteJobId: r.delete_job_id != null ? r.delete_job_id : null,
      holders: versionHolders(r, lang), notes: notes,
      // 왜 못 누르는지는 `notes` 가 이미 글자로 말한다 — 비활성 버튼의 title 에만 두지 않는다
      canDiscard: !deleting && st !== "submitted" && st !== "discarded" && st !== "running" && st !== "creating",
      discardWhy: deleting ? T(lang, "version.row.deleting", { id: r.delete_job_id })
        : st === "running" || st === "creating" ? T(lang, "version.row.busy") : null,
      canRetry: st === "failed"
    };
  }
  /** `GET …/release/versions` → W1 의 행들. 드래프트 · 라이브 · 지난 것, 그리고 «+ 새 버전 만들기»
      를 잠그는 `creating`. 시간은 `nowMs` 를 준 만큼만 말한다(안 주면 나이를 안 그린다). */
  function versionListModel(doc, lang, nowMs) {
    var d = doc && typeof doc === "object" ? doc : {};
    var live = d.live && typeof d.live === "object" ? d.live : {};
    var hints = d.hints && typeof d.hints === "object" ? d.hints : {};
    var drafts = (Array.isArray(d.drafts) ? d.drafts : []).map(function (r) { return versionRowModel(r, lang, nowMs); });
    var history = (Array.isArray(d.history) ? d.history : []).map(function (h) {
      var e = h || {};
      return { id: e.id, title: versionTitle(e.ios, e.android), submittedAt: e.submitted_at || null,
        reviewJobId: e.review_job_id != null ? e.review_job_id : null };
    });
    return {
      ttlHours: isNum(d.ttl_hours) ? d.ttl_hours : null,
      live: { ios: live.ios || null, android: live.android || null, title: versionTitle(live.ios, live.android),
        known: !!(live.ios || live.android), fromPlanJob: live.from_plan_job != null ? live.from_plan_job : null },
      hints: { ios: hints.ios || null, android: hints.android || null },
      // 대화상자에 칸을 둘 스토어(E2): 플랜이 이름이나 힌트를 아는 스토어만. 둘 다 모르면(E1)
      // 둘 다 둔다 — rcm 은 프로젝트가 어느 스토어에 내는지 모르고, 플랜만이 말해 준다.
      platforms: (function () {
        var known = ["ios", "android"].filter(function (p) { return !!(live[p] || hints[p]); });
        return known.length ? known : ["ios", "android"];
      })(),
      creating: drafts.some(function (r) { return r.state === "creating"; }),
      drafts: drafts, history: history
    };
  }
  /** 요약 띠(§12) — 칩 넷, 하나라도 빨가면 띠가 빨갛다. 버전을 고르기 전에 «이 앱을 지금 올릴 수
      있는가»만 답한다. 막힘·경고는 프로젝트가 플랜에 적어 보낸 것 그대로다(rcm 은 목록을 안 만든다).
      `ctx` = `{doc, items, release, profile, fetchError, nowMs, tzName}`. */
  function statusStripModel(ctx, lang) {
    var c = ctx || {}, doc = c.doc || {}, profile = c.profile || {}, setup = doc.setup || {};
    var chips = [];
    // 자격 증명 — 비밀이 다 있고 다 검증됐을 때만 초록
    var latest = latestVerified(c.items);
    var required = isNum(setup.required) ? setup.required : null, verified = isNum(setup.verified) ? setup.verified : null;
    chips.push({
      code: "credentials", label: T(lang, "vstrip.label.credentials"),
      tone: setup.complete === true && required != null && verified != null && verified >= required && latest != null ? "ok" : "bad",
      text: T(lang, "vstrip.creds", {
        n: isNum(setup.present) ? setup.present : DASH, total: required != null ? required : DASH,
        clock: latest != null ? fmtClock(new Date(latest).toISOString(), c.tzName, c.nowMs) : DASH
      })
    });
    // 소스 — 행 넷의 판정을 그대로 쓴다(ok 가 아니면 빨강: 모르는 것도 초록은 아니다)
    var srcCtx = { nowMs: c.nowMs, fetchError: c.fetchError };
    chips.push({
      code: "source", label: T(lang, "vstrip.label.source"),
      tone: rowState("source", { doc: doc, nowMs: c.nowMs, fetchError: c.fetchError }) === "ok" ? "ok" : "bad",
      text: sourceHead(doc, srcCtx, lang).join(" · ")
    });
    // 스토어 — 최신 플랜이 읽은 두 스토어와 다음 빌드 번호. 플랜이 없거나 낡으면 빨강
    var release = c.release || {}, plan = release.plan || null, pdoc = planEntryDoc(plan);
    var parts = [], storeTone = "bad";
    if (plan == null || pdoc == null) parts.push(T(lang, "vstrip.store_none"));
    else {
      var store = pdoc.store && typeof pdoc.store === "object" ? pdoc.store : {};
      var play = store.play && typeof store.play === "object" ? store.play : {};
      parts.push(T(lang, "vstrip.store_ios", { name: store.asc_live || DASH, build: isNum(store.asc_live_build) ? store.asc_live_build : DASH }));
      parts.push(T(lang, "vstrip.store_play", { name: play.production_name || DASH, build: isNum(play.production) ? play.production : DASH }));
      if (isNum(pdoc.n)) parts.push(T(lang, "vstrip.store_next", { n: pdoc.n }));
      var maxAge = isNum(profile.plan_max_age_minutes) ? profile.plan_max_age_minutes * 60 : null;
      var stale = plan.stale === true || (maxAge != null && isNum(plan.age_seconds) && plan.age_seconds > maxAge);
      if (stale) parts.push(T(lang, "vstrip.store_stale", { age: fmtAgo(plan.age_seconds, lang) }));
      else storeTone = "ok";
    }
    chips.push({ code: "store", label: T(lang, "vstrip.label.store"), tone: storeTone, text: parts.join(" · ") });
    // 막힘 — 막힘도 경고도 없으면 칩 자체가 없다
    var blockers = pdoc && Array.isArray(pdoc.blockers) ? pdoc.blockers.length : 0;
    var warnings = pdoc && Array.isArray(pdoc.warnings) ? pdoc.warnings.length : 0;
    if (blockers > 0 || warnings > 0) {
      chips.push({ code: "blockers", label: "", tone: blockers > 0 ? "bad" : "ok",
        text: T(lang, "vstrip.blockers", { n: blockers, count: warnings }) });
    }
    var bad = chips.filter(function (chip) { return chip.tone === "bad"; }).length;
    return { tone: bad > 0 ? "bad" : "ok", bad: bad, chips: chips };
  }

  // ── W3 버전 페이지 본문 — 이전 버전 값으로 채워진 편집 칸 (워크플랜 §3.1 · §3.2 · R6) ──
  // 키와 차례는 서버 `release_state.PREFILL_KEYS` 와 **같아야 한다** — 하나라도 어긋나면 자동
  // 저장이 400 `listing_key` 를 받는다. 스크린샷·그래픽은 이번 범위에서 보기만이다(결정 Q8).
  var PREFILL_KEYS = {
    ios: ["subtitle", "promotional_text", "description", "keywords", "support_url", "marketing_url", "whats_new"],
    android: ["title", "short_description", "full_description", "whats_new"]
  };
  // 절 안의 그룹과 그 차례 — 심사 패널과 같다(항목 15). 읽기 전용 그룹 둘은 같은 코드를 쓴다.
  var VERSION_GROUPS = {
    ios: [{ key: "version_info", fields: ["subtitle", "promotional_text", "description", "keywords", "support_url", "marketing_url"] },
      { key: "whats_new", fields: ["whats_new"] }],
    android: [{ key: "store_listing", fields: ["title", "short_description", "full_description"] },
      { key: "release_notes", fields: ["whats_new"] }]
  };
  var LONG_FIELDS = { description: 1, full_description: 1, whats_new: 1, promotional_text: 1, keywords: 1 };
  var VERSION_SAVE_DEBOUNCE_MS = 800;   // 한 칸을 고치고 800 ms 조용하면 그 키만 보낸다(§3.2)

  /** 이 칸의 스토어 상한. `whats_new` 만 스토어마다 다르다(App Store 4000 · Play 500 — §3.1). */
  function fieldLimit(platform, key) {
    if (key === "whats_new") return NOTES_LIMIT[platform] || null;
    return FIELD_LIMITS[key] || null;
  }
  /** 비교용 정규화 — 서버 `release_state._norm` 과 **같은 규칙**이다: 양끝 공백 · CRLF → LF.
      이것이 어긋나면 화면의 «바뀜» 과 서버의 diff 가 갈라진다(줄끝만 달라도 고친 것이 된다). */
  function listingNorm(text) {
    return typeof text === "string" ? text.replace(/\r\n/g, "\n").trim() : "";
  }
  function listingPart(doc, platform) {
    var part = doc && typeof doc === "object" ? doc[platform] : null;
    return part && typeof part === "object" ? part : {};
  }
  /** 서버 `release_state.listing_diff` 의 웹 쪽 짝. 편집본에 **있는** 키만 보고, 정규화해서 같으면
      안 센다 — 그래서 prefill 값으로 되돌리면 diff 가 빈다(E10). `screenshots` 는 이전 문안이 그
      스토어를 알면 `same`, 모르면 `n/a`(웹 업로드는 범위 밖이라 편집본에 스크린샷이 없다). */
  function listingDiff(prefill, edited) {
    var fields = [], shots = {}, unchanged = 0;
    Object.keys(PREFILL_KEYS).forEach(function (platform) {
      var before = listingPart(prefill, platform), after = listingPart(edited, platform);
      PREFILL_KEYS[platform].forEach(function (key) {
        if (!Object.prototype.hasOwnProperty.call(after, key)) return;
        var old = typeof before[key] === "string" ? before[key] : null;
        var fresh = typeof after[key] === "string" ? after[key] : null;
        if (listingNorm(old) === listingNorm(fresh)) { unchanged++; return; }
        fields.push({ platform: platform, key: key, old: old, new: fresh });
      });
      shots[platform] = Object.keys(before).length ? "same" : "n/a";
    });
    return { fields: fields, screenshots: shots, changed: fields.length, unchanged: unchanged };
  }
  /** 칸 이름 — 문안 키는 심사 패널과 같은 이름이고, 릴리스 노트만 스토어마다 부르는 말이 다르다. */
  function fieldLabelKey(platform, key) {
    if (key === "whats_new") return platform === "ios" ? "version.field.whats_new" : "version.field.release_notes";
    return "review.field." + key;
  }
  /**
   * 칸 하나의 값과 **출처**(R6). 값은 편집본 → 이전 버전(prefill) → 파일(소개 자료 미리보기) →
   * 빈 값 차례로 고르고, 화면은 그중 무엇을 보이는지 말한다. `changed` 는 이전 버전 값과
   * **정규화해서** 다를 때만이다 — 되돌리면 칩도 diff 도 사라진다(E10).
   * `ctx` = `{prefill, edited, typed, file, remote}` — `typed` 는 이 브라우저가 친 값(저장에
   * 실패해도 화면에 남는다 · E7), `file` 은 `listingFields(listing)` 의 플랫폼별 값이다.
   */
  function versionFieldModel(platform, key, ctx, lang) {
    var c = ctx || {};
    var prefill = listingPart(c.prefill, platform), edited = listingPart(c.edited, platform);
    var typed = listingPart(c.typed, platform), file = listingPart(c.file, platform);
    var has = function (o, k) { return typeof o[k] === "string"; };
    var base = has(prefill, key) ? prefill[key] : null;
    var value, origin;
    if (has(typed, key)) { value = typed[key]; origin = "edited"; }
    else if (has(edited, key)) { value = edited[key]; origin = "edited"; }
    else if (base != null) { value = base; origin = "prefill"; }
    else if (has(file, key)) { value = file[key]; origin = "file"; }
    else { value = ""; origin = "empty"; }
    var changed = origin === "edited" && listingNorm(value) !== listingNorm(base);
    // 고친 값이 이전 버전과 같아졌으면 그것은 «이전 버전 그대로» 다 — 이름이 사실을 따라간다
    var source = changed ? "edited"
      : base != null ? "prefill"
        : has(file, key) && listingNorm(value) === listingNorm(file[key]) ? "file"
          : listingNorm(value) ? "edited" : "empty";
    var limit = fieldLimit(platform, key);
    return {
      platform: platform, key: key, id: "f-" + platform + "-" + key,
      label: T(lang, fieldLabelKey(platform, key)),
      value: value, prefill: base, source: source, sourceText: T(lang, "version.edit.source." + source),
      changed: changed, canRevert: changed, remote: !!(c.remote || {})[platform + "." + key],
      limit: limit, counter: fieldCounter(value, limit), multiline: !!LONG_FIELDS[key]
    };
  }
  /**
   * W3 본문 하나 — 절 둘(App Store · Google Play, 심사 패널과 같은 그룹 차례) · 칸마다 값과 출처 ·
   * 바뀐 칸 수 · 읽기 전용 이유. `ctx` = `{version, file, typed, remote, admin}`.
   * 절은 이 버전이 **이름을 가진 스토어**만 둔다(§11 · E25) — 한쪽만 만든 드래프트에 남의 칸을
   * 그리지 않는다. 이름을 둘 다 모르면(있을 수 없지만) 둘 다 둔다.
   */
  function versionPageModel(ctx, lang) {
    var c = ctx || {}, v = c.version && typeof c.version === "object" ? c.version : {};
    var closed = v.state === "submitted" || v.state === "discarded";
    var fctx = { prefill: v.prefill, edited: v.edited, typed: c.typed, file: c.file, remote: c.remote || {} };
    var platforms = Object.keys(PREFILL_KEYS).filter(function (p) {
      return typeof v[p + "_version"] === "string" && v[p + "_version"];
    });
    if (!platforms.length) platforms = Object.keys(PREFILL_KEYS);
    var shots = v.diff && typeof v.diff === "object" && v.diff.screenshots && typeof v.diff.screenshots === "object" ? v.diff.screenshots : {};
    var changed = 0, remote = 0;
    var sections = platforms.map(function (platform) {
      var groups = VERSION_GROUPS[platform].map(function (g) {
        return {
          key: g.key, label: T(lang, "review.group." + g.key),
          fields: g.fields.map(function (key) {
            var f = versionFieldModel(platform, key, fctx, lang);
            if (f.changed) changed++;
            if (f.remote) remote++;
            return f;
          })
        };
      });
      var same = shots[platform] === "same";
      return {
        platform: platform, label: T(lang, "review.section." + platform),
        version: v[platform + "_version"] || null, groups: groups,
        screenshots: { state: same ? "same" : "unknown", text: T(lang, same ? "version.edit.screenshots.same" : "version.edit.screenshots.unknown") }
      };
    });
    var readOnly = c.admin !== true ? "admin" : closed ? "closed" : null;
    return {
      platforms: platforms, sections: sections, changed: changed, remote: remote,
      editable: readOnly == null, readOnly: readOnly,
      readOnlyText: readOnly == null ? null
        : readOnly === "admin" ? T(lang, "version.edit.readonly.admin")
          : T(lang, "version.edit.readonly.closed", { state: T(lang, "version.state." + v.state) }),
      hasPrefill: !!(v.prefill && typeof v.prefill === "object"),
      prefillSource: v.prefill && typeof v.prefill === "object" && typeof v.prefill.source === "string" && v.prefill.source ? v.prefill.source : null,
      headText: T(lang, changed > 0 ? "version.edit.head_changed" : "version.edit.head", { n: changed })
    };
  }
  /** 자동 저장이 보내는 본문 — **바뀐 키만** 싣는다(§3.2 · AC-C6). `dirty` 는 `"<플랫폼>.<키>"`
      집합이고 값은 이 브라우저가 친 것이다. 보낼 것이 없으면 null 이라 부르지도 않는다. */
  function listingPutBody(typed, dirty) {
    var body = {}, any = false;
    Object.keys(dirty || {}).forEach(function (id) {
      if (!dirty[id]) return;
      var at = id.indexOf("."), platform = id.slice(0, at), key = id.slice(at + 1);
      if (!PREFILL_KEYS[platform] || PREFILL_KEYS[platform].indexOf(key) < 0) return;
      var value = listingPart(typed, platform)[key];
      if (typeof value !== "string") return;
      if (!body[platform]) body[platform] = {};
      body[platform][key] = value;
      any = true;
    });
    return any ? body : null;
  }
  /** 두 브라우저가 같은 드래프트를 고칠 때(E8) — 5초 폴링이 가져온 편집본이 우리가 마지막으로 본
      것과 다르면 그 칸이 «다른 곳에서 바뀜» 이다. 화면 값을 조용히 덮어쓰지 않기 위한 표시이고,
      우리가 방금 보낸 값이 그대로 돌아온 것은 남의 편집이 아니다. */
  function remoteListingEdits(seen, incoming, typed) {
    var out = {};
    Object.keys(PREFILL_KEYS).forEach(function (platform) {
      var was = listingPart(seen, platform), got = listingPart(incoming, platform), mine = listingPart(typed, platform);
      PREFILL_KEYS[platform].forEach(function (key) {
        var a = typeof was[key] === "string" ? was[key] : null;
        var b = typeof got[key] === "string" ? got[key] : null;
        if (listingNorm(a) === listingNorm(b)) return;
        if (typeof mine[key] === "string" && listingNorm(mine[key]) === listingNorm(b)) return;
        out[platform + "." + key] = true;
      });
    });
    return out;
  }
  /** 자동 저장 배지 — «저장하는 중» · «자동 저장 · 12s 전» · «저장 실패: …»(+ 다시 저장).
      시간은 준 만큼만 말한다(`at` 이 없거나 `nowMs` 를 모르면 나이를 안 그린다). */
  function saveBadge(save, lang, nowMs) {
    var s = save && typeof save === "object" ? save : {};
    if (s.state === "failed") {
      return { state: "failed", tone: "bad", retry: true, at: null,
        text: T(lang, "version.edit.save.failed", { detail: s.detail || DASH }) };
    }
    if (s.state === "saving") return { state: "saving", tone: "run", retry: false, at: null, text: T(lang, "version.edit.save.saving") };
    if (s.state === "saved") {
      var age = secondsSince(s.at, nowMs);
      return { state: "saved", tone: "ok", retry: false, at: s.at || null,
        text: age == null ? T(lang, "version.edit.save.saved_now") : T(lang, "version.edit.save.saved", { age: fmtCoarse(age) }) };
    }
    return { state: "idle", tone: "none", retry: false, at: null, text: T(lang, "version.edit.save.idle") };
  }

  // ── 심사 패널 본체 · Store 행 · Build·upload 행 (항목 5~24 · 28 · 36 · 40 · 42 · 46~49) ──
  // 서버 계약은 STORE-TAB-API-2(`GET …/release` · `POST …/release/plan|review|upload` ·
  // `GET …/release/listing` · `POST …/release/listing/validate`). 웹은 판정하지 않는다 — N · 판정 ·
  // 위반은 소비 레포 스크립트의 JSON 그대로다. rcm 코드에 특정 앱의 이름·경로·필드는 없다.
  var RELEASE_STATUS_TONE = { submitted: "ok", partial: "stale", noop: "na", failed: "bad" };
  var IOS_FIELDS = ["promotional_text", "description", "keywords", "support_url", "marketing_url", "subtitle"];
  var PLAY_FIELDS = ["title", "short_description", "full_description", "keywords", "support_url", "marketing_url", "subtitle"];
  // 스토어가 정한 글자 상한 — 프로젝트가 아니라 App Store Connect · Play Console 의 것이다
  var FIELD_LIMITS = { promotional_text: 170, description: 4000, keywords: 100, support_url: 255, marketing_url: 255,
    subtitle: 30, title: 30, short_description: 80, full_description: 4000 };
  var NOTES_LIMIT = { ios: 4000, android: 500 };
  var PLAY_ONLY = { title: 1, short_description: 1, full_description: 1 };
  var IOS_ONLY = { promotional_text: 1, keywords: 1, support_url: 1, marketing_url: 1, subtitle: 1 };
  var FIELD_ALIAS = { promotionaltext: "promotional_text", promo: "promotional_text", supporturl: "support_url", marketingurl: "marketing_url",
    name: "title", app_name: "title", appname: "title", shortdescription: "short_description", short: "short_description",
    fulldescription: "full_description", full: "full_description", whatsnew: "whats_new", whats_new: "whats_new",
    release_notes: "whats_new", releasenotes: "whats_new", changelog: "whats_new" };

  /** 판정 낱말 → 색(계약 §2). rcm 이 아는 몇 개만 색이고 나머지는 빨강 — 낱말은 그대로 보인다. */
  function verdictTone(word) {
    if (word == null || word === "") return "none";
    var w = String(word);
    if (w === "ready") return "ok";
    if (w === "already_submitted") return "na";
    if (w === "processing") return "running";
    if (/_unreadable$/.test(w)) return "stale";
    return "bad";
  }
  function planEntryDoc(entry) { return entry && entry.doc && typeof entry.doc === "object" ? entry.doc : null; }
  /** 닫을 수 없는 빨간 띠(항목 36): 어느 판정이든 `unsafe_release_type`, 또는 관측 `auto_release === true`. */
  function bannerDecision(release) {
    var r = release || {}, rv = r.review || {};
    var docs = [planEntryDoc(rv.plan), planEntryDoc(rv.result)];
    var version = r.plan && r.plan.build_name ? String(r.plan.build_name) : null;
    for (var i = 0; i < docs.length; i++) {
      var d = docs[i];
      if (!d) continue;
      if (d.build_name && !version) version = String(d.build_name);
      if (d.ios === "unsafe_release_type" || d.android === "unsafe_release_type") return { show: true, reason: "unsafe_release_type", version: version || DASH };
      if ((d.observed && d.observed.auto_release === true) || d.auto_release === true) return { show: true, reason: "auto_release", version: version || DASH };
    }
    return { show: false, reason: null, version: version || DASH };
  }
  /** 심사 플랜 항목의 형편: `ok`(성공 · 안 낡음 · plan_verdict ok) 아니면 이유 키. */
  function reviewPlanVerdict(entry, buildName) {
    var d = planEntryDoc(entry);
    if (!entry || entry.state !== "succeeded" || !d) return "plan_required";
    if (buildName != null && d.build_name != null && String(d.build_name) !== String(buildName)) return "plan_required";
    if (entry.stale === true) return "plan_stale";
    if (d.plan_verdict !== "ok") return "plan_blocked";
    return "ok";
  }
  /** 사람이 친 N 과 플랜의 N. 숫자 그대로의 글자여야 한다 — `"0181"` 도 `" 181"` 도 아니다(항목 28). */
  function nMatches(typed, n) {
    if (!isNum(n) || Math.floor(n) !== n) return false;
    return typeof typed === "string" && typed === String(n);
  }
  /** 빌드 번호 모드 — «자동»(플랜이 스토어에서 읽은 다음 번호를 그대로) 또는 «직접 입력». 기본은
   * 프로파일의 build_number_policy (manual → typed, 그 밖은 auto); 사람이 토글로 이번 화면만 바꾼다. */
  function nModeDefault(profile) { return profile && profile.build_number_policy === "manual" ? "typed" : "auto"; }
  function nModeOf(mode, profile) { return mode === "auto" || mode === "typed" ? mode : nModeDefault(profile); }
  /** 자동이면 플랜의 n 이 있을 때 통과, 직접이면 친 값이 n 과 글자 그대로 같아야 한다. 이유: n_unknown · n_mismatch. */
  function nReason(mode, typed, n) {
    if (!isNum(n) || Math.floor(n) !== n) return "n_unknown";
    if (mode === "auto") return null;
    return nMatches(typed, n) ? null : "n_mismatch";
  }
  /** 서버로 보내는 확인 값 — 자동은 "auto"(서버가 플랜의 n 을 채운다), 직접은 친 그대로. */
  function nSendValue(mode, typed) { return mode === "auto" ? "auto" : (typeof typed === "string" ? typed : ""); }
  function platformParam(platforms) {
    var p = platforms || {};
    if (p.ios && p.android) return "both";
    if (p.ios) return "ios";
    if (p.android) return "android";
    return null;
  }
  /**
   * Submit 활성 조건(항목 22·23·28 · 계약 §6). 이유는 전부 모아 준다(비활성 버튼은 이유를 쓴다).
   * ctx: {release, typedN, platforms:{ios,android}, managed, admin, token, busy}
   * 관리형 게시 체크는 이 렌더의 것만 본다 — 저장하지 않는다(결정 3).
   */
  function submitDecision(ctx) {
    ctx = ctx || {};
    var r = ctx.release || {}, reasons = [];
    if (bannerDecision(r).show) reasons.push("unsafe");
    if (!ctx.token) reasons.push("no_token");
    else if (!ctx.admin) reasons.push("admin");
    if (!platformParam(ctx.platforms)) reasons.push("no_platform");
    var plan = r.plan || null, planDoc = planEntryDoc(plan);
    if (!planDoc) reasons.push("no_plan");
    var pv = reviewPlanVerdict(r.review && r.review.plan, plan && plan.build_name);
    if (pv !== "ok") reasons.push(pv);
    var nr = nReason(nModeOf(ctx.nMode, ctx.profile), ctx.typedN, planDoc ? planDoc.n : null);
    if (nr) reasons.push(nr);
    if (ctx.platforms && ctx.platforms.android && ctx.managed !== true) reasons.push("managed_unconfirmed");
    if (ctx.busy) reasons.push("busy");
    return { enabled: reasons.length === 0, reasons: reasons };
  }
  /** `POST …/release/review` 본문. `confirmed-on` 은 이번 제출에서 체크했고 Play 가 들어갈 때만이다. */
  function reviewBody(mode, ctx) {
    ctx = ctx || {};
    var r = ctx.release || {}, plan = r.plan || {}, doc = planEntryDoc(plan) || {}, profile = ctx.profile || {};
    var submit = mode === "submit";
    var platform = platformParam(ctx.platforms) || "both";
    var managed = submit && ctx.managed === true && (platform === "both" || platform === "android");
    return {
      build_name: plan.build_name != null ? String(plan.build_name) : (doc.build_name != null ? String(doc.build_name) : ""),
      ref: profile.default_branch || "main",
      mode: submit ? "submit" : "plan",
      platform: platform,
      confirm_build_number: submit ? nSendValue(nModeOf(ctx.nMode, profile), ctx.typedN) : "",
      play_managed_publishing: managed ? "confirmed-on" : "not-checked",
      listing: ctx.listingFull ? "full" : "notes-only",
      phased: ctx.phased === false ? "0" : "1"
    };
  }
  /** 제출 결과(항목 40)가 지금 회차의 것인가 — 문서에 build_name 이 있으면 그것으로, 없으면 잡 순서로. */
  function resultIsCurrent(release) {
    var r = release || {}, rv = r.review || {}, res = rv.result, d = planEntryDoc(res);
    if (!res || !d) return false;
    var plan = r.plan || null;
    if (d.build_name != null && plan && plan.build_name != null) return String(d.build_name) === String(plan.build_name);
    if (plan && isNum(plan.job_id) && isNum(res.job_id)) return res.job_id > plan.job_id;
    return true;
  }
  /** 본체 머리의 필(항목 7·27·40): 왜 못 여는지, 또는 결과. `rows` 는 {setup, source, store, build} 의 색. */
  function panelPill(release, rows, releaseStatus) {
    rows = rows || {};
    if (releaseStatus === 404) return { code: "not_available", tone: "na" };
    if (bannerDecision(release).show) return { code: "unsafe_release_type", tone: "bad" };
    var r = release || {};
    if (resultIsCurrent(r)) {
      var st = String(planEntryDoc(r.review.result).overall_status || "");
      if (RELEASE_STATUS_TONE[st]) return { code: st, tone: RELEASE_STATUS_TONE[st] };
      return { code: "failed", tone: "bad" };
    }
    if (rows.setup === "bad") return { code: "secrets_expired", tone: "bad" };
    if (rows.source === "bad") return { code: "source_not_ready", tone: "bad" };
    if (rows.store === "bad") return { code: "plan_blocked", tone: "bad" };
    var up = planEntryDoc(r.upload);
    if (!up || up.status !== "success") return { code: "waiting_upload", tone: "na" };
    return { code: "not_submitted", tone: "none" };
  }
  /** 본체 펼침: 기억이 이기고, 아니면 네 행이 초록이고 결과가 없을 때만 펼친다(항목 7). */
  function panelOpen(pill, remembered, hasError) {
    if (hasError) return true;   // 심사 호출이 거절되면 접힌 본체 안에 숨지 않는다 — 그 렌더는 연다
    if (remembered === "open") return true;
    if (remembered === "closed") return false;
    return !!pill && pill.code === "not_submitted";
  }
  /** 현재/상한 세기(항목 11): 90% 넘으면 황토, 넘치면 빨강. 글자 수는 코드포인트로 센다. */
  function fieldCounter(text, limit) {
    var n = typeof text === "string" ? Array.from(text).length : 0;
    if (!isNum(limit) || limit <= 0) return { n: n, limit: null, tone: "none", text: String(n) };
    var tone = n > limit ? "bad" : n >= limit * 0.9 ? "warn" : "ok";
    return { n: n, limit: limit, tone: tone, text: n + "/" + limit };
  }
  function normField(key) {
    var k = String(key || "").toLowerCase().replace(/-/g, "_");
    var last = k.split(/[./]/).pop();
    return FIELD_ALIAS[last] || last;
  }
  function isKnownField(key) {
    var k = normField(key);
    return !!(FIELD_LIMITS[k] || PLAY_ONLY[k] || IOS_ONLY[k] || k === "whats_new" || k === "privacy_url");
  }
  function platformOfKey(key, bracket) {
    var s = String(bracket || key || "").toLowerCase();
    if (/(^|[\[./_-])(android|play)([\]./_-]|$)/.test(s)) return "android";
    if (/(^|[\[./_-])(ios|asc|apple|iphone|ipad)([\]./_-]|$)/.test(s)) return "ios";
    return null;
  }
  /**
   * 소개 자료 미리보기(항목 10·11·16·17·21) — `listing.preview` 는 프로젝트 스크립트의 줄이다.
   * `key: value` 꼴만 필드로 읽고(키의 마지막 조각이 이름, `ios.`·`android.`·`[play]` 가 플랫폼),
   * 플랫폼 없는 키는 그 스토어에만 있는 필드면 그쪽으로, 아니면 양쪽으로. 나머지 줄은 `other` 다.
   * diff 줄에 필드 이름이 보이면 `changed`. 아무것도 지어내지 않는다 — 없는 필드는 —.
   */
  function listingFields(listing) {
    var out = { ios: {}, android: {}, other: [], changed: {}, screenshotsChanged: false };
    var lines = listing && Array.isArray(listing.preview) ? listing.preview : [];
    var last = null, section = null;   // section: 「iOS (ko)」·「Android (ko-KR)」 같은 절 머리가 정한 플랫폼
    lines.forEach(function (raw) {
      var line = String(raw == null ? "" : raw);
      var head = /^\s*(iOS|Android|App Store|Google Play|Play)\b/i.exec(line);
      if (head && !/[:=]/.test(line)) { section = platformOfKey(head[1].replace(/\s+/g, "_")); last = null; return; }
      var m = /^\s*(?:\[([A-Za-z]+)\]\s*)?([A-Za-z][A-Za-z0-9_.\/-]*)\s*[:=]\s?(.*)$/.exec(line);
      if (!m) {
        // 「key   12자   value」 · 「key   value」 — 두 칸 이상 띄운 표 꼴. 아는 필드 이름일 때만.
        var t = /^\s*([A-Za-z][A-Za-z0-9_.\/-]*)\s{2,}(?:\d+\s*(?:자|chars?)\s+)?(.*)$/.exec(line);
        if (t && isKnownField(t[1])) m = [t[0], null, t[1], t[2].replace(/^\((?:비어 있음|empty|none)\)$/i, "")];
        else if (t) { out.other.push(line.trim()); last = null; return; }   // 표의 다른 행 — 앞 필드의 이어짐이 아니다
      }
      if (!m) {
        if (last && /^\s+\S/.test(line)) { last.target.forEach(function (t) { out[t][last.key] += "\n" + line.trim(); }); return; }
        if (line.trim()) out.other.push(line.trim());
        return;
      }
      var key = normField(m[2]), plat = platformOfKey(m[2], m[1]) || section;
      var targets = plat ? [plat] : PLAY_ONLY[key] ? ["android"] : IOS_ONLY[key] ? ["ios"] : ["ios", "android"];
      targets.forEach(function (t) { out[t][key] = m[3]; });
      last = { key: key, target: targets };
    });
    var diff = listing && Array.isArray(listing.diff) ? listing.diff : [];
    diff.forEach(function (raw) {
      var line = String(raw == null ? "" : raw).toLowerCase();
      Object.keys(FIELD_LIMITS).forEach(function (k) {
        // 낱말 단위다 — `subtitle` 이 `title` 을 바꾼 것으로 읽히면 안 된다
        var re = new RegExp("(^|[^a-z0-9])(" + k + "|" + k.replace(/_/g, "") + ")([^a-z0-9]|$)");
        if (re.test(line)) out.changed[k] = true;
      });
      if (/screenshot|image|graphic/.test(line)) out.screenshotsChanged = true;
    });
    return out;
  }
  /** 스크린샷을 경로로 가른다 — 못 가리면 `other`(양쪽 절이 «n files not sorted» 로 말한다). */
  function screenshotGroups(listing) {
    var out = { ios: [], android: [], other: [] };
    var list = listing && Array.isArray(listing.screenshots) ? listing.screenshots : [];
    list.forEach(function (s) {
      if (!s || typeof s.path !== "string" || !s.path) return;
      var p = platformOfKey(s.path);
      out[p || "other"].push(s);
    });
    return out;
  }
  /** 리뷰 정보 폴더(항목 14): 프로파일이 `kind: dir` 로 선언한 비밀의 파일마다 있음/없음. 값은 없다. */
  function reviewInfoModel(items) {
    var dirs = (items || []).filter(function (it) { return it && it.kind === "dir"; });
    if (!dirs.length) return null;
    var files = [];
    dirs.forEach(function (d) { dirFiles(d).forEach(function (f) { files.push({ name: f.name, present: f.present }); }); });
    var missing = files.filter(function (f) { return !f.present; }).length;
    return { name: dirs.map(function (d) { return d.name; }).join(" · "), files: files, complete: files.length > 0 && missing === 0, missing: missing };
  }
  /** 버전 띠(항목 8). 값은 마지막 plan.json · upload.json · listing 에서만 온다. */
  function versionStrip(release, listing, profile, lang, tzName, nowMs) {
    var r = release || {}, plan = r.plan || null, doc = planEntryDoc(plan) || {}, up = planEntryDoc(r.upload);
    var version = plan && plan.build_name != null ? String(plan.build_name) : doc.build_name != null ? String(doc.build_name) : DASH;
    var n = isNum(doc.n) ? doc.n : DASH;
    var notes = listing && listing.release_notes && typeof listing.release_notes === "object" ? listing.release_notes : null;
    var notesText = notes && typeof notes.text === "string" ? notes.text : null;
    return {
      version: version, build: n,
      buildNote: up && isNum(up.n) ? T(lang, "review.build.by_job", { id: isNum(r.upload.job_id) ? r.upload.job_id : DASH }) : T(lang, "review.build.not_uploaded"),
      listing: T(lang, "review.strip.listing_from", { ref: (profile && profile.listing && profile.listing.ref) || (profile && profile.default_branch) || "main", sha: listing && typeof listing.sha === "string" && listing.sha ? listing.sha.slice(0, 7) : DASH }),
      notesPath: notes && notes.path ? String(notes.path) : null,
      notesCounter: notesText != null ? fieldCounter(notesText, NOTES_LIMIT.android) : null,
      notesText: notesText,
      tag: up && up.tag ? String(up.tag) : null
    };
  }
  /** 심사 결과(항목 40) — 색 + 낱말 + 플랫폼별 status/reason + observed. 모르는 status 는 빨강. */
  function resultModel(entry, lang) {
    var d = planEntryDoc(entry);
    if (!d) return null;
    var status = String(d.overall_status || DASH);
    var plats = d.platforms && typeof d.platforms === "object" ? d.platforms : {};
    var observed = d.observed && typeof d.observed === "object" ? d.observed : {};
    var lines = Object.keys(plats).map(function (name) {
      var p = plats[name] && typeof plats[name] === "object" ? plats[name] : { status: String(plats[name]) };
      var text = T(lang, "review.result.platform", { name: name, status: String(p.status || DASH) });
      if (p.reason) text += " · " + T(lang, "review.result.reason", { reason: String(p.reason) });
      if (observed[name] != null) text += " · " + T(lang, "review.observed", { value: String(observed[name]) });
      return { name: name, status: String(p.status || DASH), text: text };
    });
    var extras = [];
    if (d.phased_release === true) extras.push(T(lang, "review.result.phased"));
    if (d.play_managed_publishing === "confirmed-on" || d.managed_publishing_confirmed === true) extras.push(T(lang, "review.result.managed_confirmed"));
    return { status: status, tone: RELEASE_STATUS_TONE[status] || "bad", platforms: lines, extras: extras, autoRelease: observed.auto_release === true || d.auto_release === true };
  }
  /** 409 등 서버의 거부 — 코드를 그대로 버튼 옆에 쓴다. 클라이언트가 돌아가지 않는다. */
  function refusalText(res, lang) {
    var b = res && res.body && typeof res.body === "object" ? res.body : {};
    var code = b.code || b.error_code;
    if (code) return T(lang, "review.server_code", { code: String(code) });
    if (b.error) return String(b.error);
    return "http " + (res ? res.status : "?");
  }
  /**
   * plan.json 의 스토어 값을 글자로 — 숫자·글자는 그대로, 객체(`{name, status, codes:[…]}` 같은 Play 트랙)는
   * `codes` 를 잇고, 없으면 name/status, 그것도 없으면 짧은 JSON. 절대 `[object Object]` 가 아니다. 없으면 —.
   */
  function storeValueText(v) {
    if (v == null || v === "") return DASH;
    if (typeof v !== "object") return String(v);
    if (Array.isArray(v)) return v.length ? v.map(storeValueText).join(", ") : DASH;
    if (Array.isArray(v.codes) && v.codes.length) return v.codes.map(storeValueText).join(", ");
    var bits = [v.version, v.name, v.build, v.status].filter(function (x) { return x != null && x !== "" && typeof x !== "object"; }).map(String);
    if (bits.length) return bits.join(" · ");
    var json;
    try { json = JSON.stringify(v); } catch (e) { json = "{…}"; }
    return json.length > 60 ? json.slice(0, 59) + "…" : json;
  }
  /**
   * Store 행(항목 6·42) — 색과 머리 조각과 본문. plan.json 은 서버가 읽어 `plan.doc` 으로 준다.
   * `planError`(«Refresh (release-plan)» 가 거절된 글자)가 있으면 그 렌더는 `bad` 고 머리의 마지막
   * 조각이 그 오류다(`error` 에도 따로 준다) — 행이 접혀 있어도(플랜이 없으면 회색으로 접힌다) 보인다.
   */
  function storeRowModel(release, releaseStatus, profile, lang, tzName, nowMs, planError) {
    var m = storeRowBase(release, releaseStatus, profile, lang, tzName, nowMs);
    if (planError != null && planError !== "") {
      m.error = T(lang, "store.plan_failed", { detail: String(planError) });
      m.head = m.head.concat([m.error]);
      m.state = "bad";
    }
    return m;
  }
  function storeRowBase(release, releaseStatus, profile, lang, tzName, nowMs) {
    var presets = profile && profile.presets ? profile.presets : {};
    if (!presets.plan) return { state: "na", head: [naRowText("store", profile, lang)], body: null };
    if (releaseStatus === 404 || !release) return { state: "na", head: [T(lang, "row.na.store")], body: null };
    var plan = release.plan;
    if (!plan) return { state: "na", head: [T(lang, "store.no_plan")], body: null };
    var head = [], st = plan.state;
    if (st === "running" || st === "queued" || st === "uploading" || st === "cancelling") return { state: "running", head: [T(lang, "store.head.plan_job", { id: plan.job_id, state: stateWord(st, lang) })], body: null };
    var doc = planEntryDoc(plan);
    if (!doc) {
      var why = plan.doc_error ? T(lang, "store.head.plan_unreadable", { id: plan.job_id }) : T(lang, "store.head.plan_job", { id: plan.job_id, state: stateWord(st, lang) });
      return { state: "bad", head: [why], body: null };
    }
    var store = doc.store && typeof doc.store === "object" ? doc.store : {};
    var sv = storeValueText;
    head.push(T(lang, "store.head.live", { version: sv(store.asc_live), build: sv(store.asc_live_build) }));
    if (store.asc_editing != null) head.push(T(lang, "store.head.editing", { version: sv(store.asc_editing) }));
    var play = store.play && typeof store.play === "object" && !Array.isArray(store.play) ? store.play : {};
    var tracks = Object.keys(play);
    head.push(tracks.length ? T(lang, "store.head.play", { track: tracks[0], build: sv(play[tracks[0]]) }) : T(lang, "store.head.play", { track: DASH, build: DASH }));
    head.push(T(lang, "store.head.next", { n: isNum(doc.n) ? doc.n : DASH }));
    var blockers = Array.isArray(doc.blockers) ? doc.blockers : [], warnings = Array.isArray(doc.warnings) ? doc.warnings : [];
    var age = isNum(plan.age_seconds) ? plan.age_seconds : (parseIso(plan.measured_at) != null && isNum(nowMs) ? Math.max(0, (nowMs - parseIso(plan.measured_at)) / 1000) : null);
    var state = "ok";
    if (st !== "succeeded" && st !== "failed") state = "bad";
    if (blockers.length) state = "bad";
    else if (plan.stale === true) state = "stale";
    if (doc.first_release === true) { head.push(T(lang, "store.head.first_release")); if (state === "ok") state = "stale"; }
    if (blockers.length) head.push(T(lang, "store.head.blockers", { n: blockers.length }));
    if (warnings.length) head.push(T(lang, "store.head.warnings", { n: warnings.length }));
    head.push(plan.stale === true ? T(lang, "store.stale_badge", { age: fmtAgo(age, lang) }) : T(lang, "store.head.plan_age", { age: fmtAgo(age, lang) }));
    var item = function (x) { return x && typeof x === "object" ? [x.code, x.text].filter(Boolean).join(" · ") : String(x); };
    return {
      state: state, head: head,
      body: {
        builds: [store.asc_live != null ? "App Store live " + sv(store.asc_live) + " (" + sv(store.asc_live_build) + ")" : null,
          store.asc_editing != null ? "App Store editing " + sv(store.asc_editing) : null].filter(Boolean),
        tracks: tracks.map(function (t) { return "Play " + t + " " + sv(play[t]); }),
        next: isNum(doc.n) ? String(doc.n) : DASH,
        measured: doc.measured_at ? fmtClock(String(doc.measured_at), tzName, nowMs) : DASH,
        blockers: blockers.map(item), warnings: warnings.map(item), firstRelease: doc.first_release === true, jobId: plan.job_id
      }
    };
  }
  /** 큐·최근 행을 id 로 — `/api/status` 의 모든 풀에서. 진행·추정은 여기서만 온다. */
  function rowsById(status) {
    var map = {};
    var pools = status && Array.isArray(status.pools) ? status.pools : [];
    pools.forEach(function (p) {
      ["queue", "recent"].forEach(function (k) { (Array.isArray(p[k]) ? p[k] : []).forEach(function (r) { if (r && isNum(r.id)) map[r.id] = r; }); });
    });
    return map;
  }
  /** 막대의 근거를 글자로(항목 46 · 계약 §6): 선언 단위 · 선언 스텝 · 시간(중앙값) · 없음. */
  function basisText(p, lang) {
    if (!p || p.basis === "none") return T(lang, "build.basis.none");
    if (p.basis === "sub") return T(lang, "build.basis.sub", { unit: p.unit });
    if (p.basis === "steps") return T(lang, "build.basis.steps");
    return T(lang, p.source === "preset" ? "build.basis.time_preset" : "build.basis.time", { dur: fmtDuration(p.expected) });
  }
  function barHeadText(p, lang) {
    if (!p || p.basis === "none" || !isNum(p.pct)) return T(lang, "pbar.none");
    if (p.basis === "sub") return T(lang, "pbar.sub", { percent: p.pct, done: p.done, total: p.total, unit: p.unit });
    if (p.basis === "steps") return T(lang, "pbar.steps", { percent: p.pct, done: p.done, total: p.total });
    return T(lang, timeKey(p.source), { percent: p.pct });
  }
  var JOB_MARK = { succeeded: { mark: "✓", tone: "ok" }, running: { mark: "▶", tone: "running" }, cancelling: { mark: "▶", tone: "running" },
    queued: { mark: "○", tone: "wait" }, uploading: { mark: "○", tone: "wait" }, failed: { mark: "✗", tone: "bad" }, timed_out: { mark: "✗", tone: "bad" },
    lost: { mark: "?", tone: "bad" }, cancelled: { mark: "□", tone: "na" } };
  /**
   * Build·upload 행의 세 층(항목 46~48): 긴 막대(근거 글자와 예상 완료) → «지금 하는 일» 한 줄 →
   * 항목 목록(✓ 초록 · ▶ 파랑 · 회색 waiting) + 도는 잡의 카드. 분모는 도는 잡의 것을 그대로
   * 쓴다(progress.sub → steps → 시간 → 빗금). `rows` 는 `rowsById(status)`.
   */
  function buildLayers(jobs, rows, lang, tzName, nowMs) {
    rows = rows || {};
    var list = (Array.isArray(jobs) ? jobs : []).filter(function (j) { return j && isNum(j.id); }).slice().sort(function (a, b) { return a.id - b.id; });
    var running = list.filter(function (j) { return j.state === "running" || j.state === "cancelling"; });
    var cur = running.length ? running[running.length - 1] : null;
    var row = cur ? rows[cur.id] || null : null;
    var p = row ? overallProgress(row) : null;
    var bar = null;
    if (cur) {
      var est = row && row.estimate ? row.estimate : {};
      bar = { jobId: cur.id, role: cur.role || null, preset: cur.preset || DASH, progress: p, head: barHeadText(p, lang), basis: basisText(p, lang),
        startedAt: (row && row.started_at) || cur.started_at || null,
        finishes: est.finish_at && !est.overdue && !est.stuck ? T(lang, "build.finishes", { clock: fmtClock(est.finish_at, tzName, nowMs) }) : null };
    }
    var nowLine = null;
    if (cur) {
      var ph = row ? progressHead(row.progress, lang) : null;
      nowLine = T(lang, "build.now", { id: cur.id, preset: cur.preset || DASH }) + (ph ? " · " + ph : "");
    }
    var items = list.map(function (j) {
      var mk = JOB_MARK[j.state] || { mark: "·", tone: "na" };
      var r = rows[j.id] || null;
      var dur = null;
      var s = parseIso(j.started_at), e = parseIso(j.finished_at);
      if (s != null && e != null) dur = fmtDuration((e - s) / 1000);
      else if (s != null && (j.state === "running" || j.state === "cancelling") && isNum(nowMs)) dur = fmtDuration(Math.max(0, (nowMs - s) / 1000));
      var parts = ["#" + j.id + " " + (j.preset || DASH)];
      if (j.role && I18N.has("review.role." + j.role)) parts.push(T(lang, "review.role." + j.role));
      parts.push(mk.tone === "wait" ? T(lang, "build.item.waiting") : stateWord(j.state, lang));
      if (dur) parts.push(dur);
      return { id: j.id, preset: j.preset || DASH, role: j.role || null, state: j.state, mark: mk.mark, tone: mk.tone, text: parts.join(" · "), row: r, running: mk.tone === "running" };
    });
    // 이번 회차 = 마지막 플랜 잡부터. 그 앞은 「이전 작업」으로 접는다(50개가 다 펼쳐지면 소음이다).
    var cut = 0;
    items.forEach(function (it, i) { if (it.role === "plan") cut = i; });
    return { bar: bar, now: nowLine, items: items.slice(cut), older: items.slice(0, cut), current: cur };
  }
  /** Build·upload 행(항목 5 · 26 · 38 · 49) — 색 · 머리 조각 · 세 층. */
  function buildRowModel(release, releaseStatus, profile, rows, lang, tzName, nowMs) {
    var presets = profile && profile.presets ? profile.presets : {};
    if (!presets.upload) return { state: "na", head: [naRowText("build", profile, lang)], layers: null, note: null };
    if (releaseStatus === 404 || !release) return { state: "na", head: [T(lang, "row.na.build")], layers: null, note: null };
    var plan = release.plan || null, doc = planEntryDoc(plan) || {};
    var version = plan && plan.build_name != null ? String(plan.build_name) : DASH;
    var n = isNum(doc.n) ? doc.n : DASH;
    var layers = buildLayers(release.jobs, rows, lang, tzName, nowMs);
    var head = [];
    if (layers.current) {
      head.push(T(lang, "build.head.version", { version: version, build: n }));
      head.push(T(lang, "build.head.stage", { role: layers.current.role && I18N.has("review.role." + layers.current.role) ? T(lang, "review.role." + layers.current.role) : layers.current.preset || DASH, id: layers.current.id }));
      if (layers.bar && layers.bar.progress && isNum(layers.bar.progress.pct)) {
        var pr = layers.bar.progress;
        head.push(isNum(pr.done) && isNum(pr.total) ? pr.done + "/" + pr.total : pr.pct + "%");
      }
      if (layers.bar && layers.bar.finishes) head.push(layers.bar.finishes);
      return { state: "running", head: head, layers: layers, note: null };
    }
    var up = release.upload || null, upDoc = planEntryDoc(up);
    if (up && (up.state === "lost")) return { state: "bad", head: [T(lang, "build.status.lost", { id: up.job_id, n: upDoc && isNum(upDoc.n) ? upDoc.n : n })], layers: layers, note: "lost" };
    if (up && (up.state === "failed" || up.state === "timed_out" || up.state === "cancelled")) return { state: "bad", head: [T(lang, "build.status.failed", { id: up.job_id, state: stateWord(up.state, lang) })], layers: layers, note: null };
    if (upDoc) {
      var upN = isNum(upDoc.n) ? upDoc.n : n;
      head.push(T(lang, "build.head.version", { version: version, build: upN }));
      var status = String(upDoc.status || "");
      head.push(I18N.has("build.status." + status) ? T(lang, "build.status." + status) : status || DASH);
      if (Array.isArray(upDoc.platforms) && upDoc.platforms.length) head.push(T(lang, "build.item.platforms", { names: upDoc.platforms.join(" + ") }));
      var fin = parseIso(up.finished_at);
      if (fin != null) head.push(T(lang, "build.head.uploaded", { clock: fmtClock(up.finished_at, tzName, nowMs) }));
      head.push("#" + up.job_id);
      if (upDoc.tag) head.push(String(upDoc.tag));
      var st = status === "success" ? "ok" : status === "partial" ? "bad" : "na";
      return { state: st, head: head, layers: layers, note: null };
    }
    head.push(T(lang, "build.head.none"));
    if (upDoc && upDoc.tag) head.push(T(lang, "build.head.last", { tag: upDoc.tag }));
    return { state: "na", head: head, layers: layers, note: null };
  }
  // ── 릴리스 드라이버 스테퍼 · GitHub 카드 (항목 25 · 26 · 28 · 37~39, STORE-TAB-API-2 「Driver」) ──
  //
  // 웹은 판정하지 않는다(규칙 4): 단계는 드라이버가 찍은 줄에서 읽고, 종료 코드는 드라이버의 것 그대로다.
  // S2 는 사람 단계 — 서버가 `plan_n` 을 주고 종료 코드가 2 면 사람이 그 숫자를 다시 타이핑해야
  // `POST …/release/confirm` 이 나간다(항목 28). `mode=upload` 버튼은 이 PR 에 **일부러 없다** —
  // 드라이버의 S7 이 업로드 길이고, 따로 두는 업로드 버튼은 자기 N 대화상자가 필요한 별개 결정이다.
  var DRIVER_STAGES = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"];
  var STAGE_LINE = /stage\s+(S\d|DONE|BLOCKED_PR_CLOSED)\b/;
  var NEXT_STAGE_LINE = /다음 단계 (S\d)\b/;
  var PLAN_N_LINE = /plan:\s*N\s*=\s*(\d+)/;
  var VERSION_RE = /^\d+\.\d+\.\d+$/;
  /**
   * 드라이버가 지금 어느 단계인가. 규칙: 종료 코드 0 → DONE · 2 → S2(사람 단계) · 그 밖에는
   * `--status` 줄에서 `stage S<n>|DONE|BLOCKED_PR_CLOSED` 또는 `다음 단계 S<n>` 에 맞는 **마지막** 줄,
   * 없으면 로그 꼬리에서 같은 규칙. `last` 는 마지막으로 보인 S 단계(BLOCKED 가 어디서 막혔는지).
   */
  function driverStage(status, exit, log) {
    function scan(lines) {
      var found = null, lastS = null;
      (Array.isArray(lines) ? lines : []).forEach(function (raw) {
        var line = String(raw == null ? "" : raw);
        var m = STAGE_LINE.exec(line) || NEXT_STAGE_LINE.exec(line);
        if (!m) return;
        found = m[1];
        if (/^S\d$/.test(m[1])) lastS = m[1];
      });
      return { stage: found, last: lastS };
    }
    var s = scan(status), l = scan(log);
    var last = s.last || l.last;
    if (exit === 0) return { stage: "DONE", last: last, source: "exit" };
    if (exit === 2) return { stage: "S2", last: last, source: "exit" };
    if (s.stage) return { stage: s.stage, last: last, source: "status" };
    if (l.stage) return { stage: l.stage, last: last, source: "log" };
    return { stage: null, last: last, source: null };
  }
  var EXIT_TONE = { 1: "bad", 2: "human", 3: "lost", 4: "warn" };
  /**
   * 스테퍼 아홉 칸(항목 26). `driver` 는 `GET …/release/driver` 본문(404 면 ctx.status), ctx 는
   * `{status, lang, tzName, nowMs}`. 앞 단계 ✓ 초록 · 지금 ▶ 파랑 · 뒤 · 회색; 사람 단계는 황토 점선,
   * 실패는 ✗ 빨강, exit 3 은 ? 보라(재시도 없음), exit 4 는 ! 황토, PR 닫힘은 ! 황토.
   */
  function stepperModel(driver, ctx) {
    ctx = ctx || {};
    var lang = ctx.lang, nowMs = ctx.nowMs;
    var base = { available: true, configured: false, running: false, exit: null, stage: null, planN: null, version: DASH, items: [], head: "", headParts: [], tone: "na", dialog: false, log: [], idle: true, done: false, blocked: false, statusError: null, knowsV: false };
    if (ctx.status === 404) return Object.assign(base, { available: false, head: T(lang, "driver.na") });
    if (!driver || typeof driver !== "object") return Object.assign(base, { loading: true, head: T(lang, "driver.loading") });
    if (driver.configured === false) return Object.assign(base, { head: T(lang, "driver.not_configured") });
    var running = driver.running === true;
    var exit = !running && isNum(driver.exit_code) ? driver.exit_code : null;
    var log = (Array.isArray(driver.log_tail) ? driver.log_tail : []).map(function (l) { return String(l == null ? "" : l); });
    var planN = isNum(driver.plan_n) && Math.floor(driver.plan_n) === driver.plan_n ? driver.plan_n : null;
    if (planN == null) log.forEach(function (l) { var m = PLAN_N_LINE.exec(l); if (m) planN = parseInt(m[1], 10); });
    var parsed = driverStage(driver.status, exit, log);
    var stage = parsed.stage;
    if (!stage && running) stage = "S0";
    var version = driver.build_name != null && driver.build_name !== "" ? String(driver.build_name) : DASH;
    var idle = !running && exit == null;
    var done = stage === "DONE", blocked = stage === "BLOCKED_PR_CLOSED";
    var cur = DRIVER_STAGES.indexOf(blocked ? parsed.last : stage);
    var tone = running ? "running" : idle ? "na" : done ? "ok" : blocked ? "warn" : EXIT_TONE[exit] || "bad";
    var items = DRIVER_STAGES.map(function (id, i) {
      var label = T(lang, "driver.stage." + id), st, glyph, note = [];
      if (idle) { st = "todo"; glyph = "·"; }
      else if (done || (cur >= 0 && i < cur)) { st = "done"; glyph = "✓"; }
      else if (cur === i) {
        if (running) { st = "current"; glyph = "▶"; note.push(T(lang, "driver.item.current")); }
        else if (blocked) { st = "blocked"; glyph = "!"; note.push(T(lang, "driver.item.blocked")); }
        else if (exit === 2) { st = "human"; glyph = "▶"; note.push(T(lang, "driver.item.human")); }
        else if (exit === 3) { st = "unknown"; glyph = "?"; note.push(T(lang, "driver.item.unknown")); }
        else if (exit === 4) { st = "drift"; glyph = "!"; note.push(T(lang, "driver.item.drift")); }
        else { st = "failed"; glyph = "✗"; note.push(T(lang, "driver.item.failed")); }
      } else { st = "todo"; glyph = "·"; note.push(T(lang, "driver.item.waiting")); }
      if (id === "S1" && planN != null && st !== "todo") note.push(T(lang, "driver.item.n", { n: planN }));
      if (id === "S2" && st === "done" && planN != null) note.push(T(lang, "driver.item.n_confirmed", { n: planN }));
      if (id === "S2" && st === "todo") note.push(T(lang, "driver.item.human_ahead"));
      if ((id === "S6" || id === "S7") && st === "todo") note.push(T(lang, "driver.item.irreversible"));
      return { id: id, label: label, state: st, glyph: glyph, text: note.join(" · ") };
    });
    var stageLabel = cur >= 0 ? DRIVER_STAGES[cur] + " " + T(lang, "driver.stage." + DRIVER_STAGES[cur]) : null;
    var parts = [];
    if (idle) parts.push(T(lang, "driver.head.none"));
    else if (running) {
      parts.push(version);
      parts.push(T(lang, "driver.head.stage", { stage: stageLabel || DASH }));
      var started = parseIso(driver.started_at);
      if (started != null && isNum(nowMs)) parts.push(fmtDuration(Math.max(0, (nowMs - started) / 1000)));
      if (driver.started_by) parts.push(T(lang, "driver.head.by", { name: String(driver.started_by) }));
    } else if (done) parts.push(T(lang, "driver.head.done", { version: version }));
    else if (blocked) { parts.push(version); parts.push(T(lang, "driver.blocked")); }
    else if (exit === 1) { parts.push(version); parts.push(stageLabel ? T(lang, "driver.exit.1", { stage: stageLabel }) : T(lang, "driver.exit.1_nostage")); }
    else if (exit === 2) { parts.push(version); parts.push(planN != null ? T(lang, "driver.exit.2", { n: planN }) : T(lang, "driver.exit.2_unknown_n")); }
    else if (exit === 3) { parts.push(version); parts.push(T(lang, "driver.exit.3")); }
    else if (exit === 4) { parts.push(version); parts.push(T(lang, "driver.exit.4")); }
    else { parts.push(version); parts.push(T(lang, "driver.exit.other", { code: exit })); }
    var confirmed = driver.confirmed_n != null;
    var autoN = driver.auto_n === true;
    if (exit === 2 && autoN && planN != null) { parts.length = 0; parts.push(version); parts.push(T(lang, "driver.exit.2_auto", { n: planN })); }
    return {
      autoN: autoN,
      available: true, configured: true, loading: false, running: running, exit: exit, stage: stage, stageSource: parsed.source, planN: planN, version: version,
      items: items, head: parts.join(" · "), headParts: parts, tone: tone, idle: idle, done: done, blocked: blocked, log: log,
      // S2 의 N 대화상자(항목 28): 종료 코드 2 · plan_n 있음 · 아직 확인한 N 없음
      // «자동» 회차는 서버가 스스로 confirm 을 띄우므로 대화상자를 열지 않는다
      dialog: !running && exit === 2 && planN != null && !confirmed && !autoN,
      statusError: driver.status_error ? String(driver.status_error) : null,
      // 이 드라이버가 `V` 단계를 아는가(계약 §5 · E16). 서버가 `--status` 의 `stages:` 줄로만
      // 판단해 실어 준다 — 모르면 회차는 옛 인자로 돌고 시트가 «스킬을 다시 돌려라» 고 말한다.
      knowsV: driver.knows_version_stage === true,
      startedBy: driver.started_by ? String(driver.started_by) : null, startedAt: driver.started_at || null
    };
  }
  /** Build·upload 행의 색과 머리를 드라이버가 가져가는가 — 도는 중이거나 실패·대기·모름·드리프트일 때만. 끝난 회차(exit 0)는 upload.json 이 말한다. */
  function driverRow(model) {
    if (!model || !model.available || !model.configured || model.idle || model.done) return null;
    var state = model.running ? "running" : model.exit === 2 || model.exit === 4 || model.blocked ? "stale" : "bad";
    return { state: state, head: model.headParts.slice() };
  }
  /**
   * 어느 버튼이 열리는가(항목 35 · 계약 「Driver」) — 이유는 전부 모아 준다. Start · Confirm · Abort ·
   * Retry 는 admin, Rehearsal 은 클라이언트 토큰. ctx: {token, admin, busy, version, typedN, release,
   * releaseStatus, uploadPreset}. Retry 는 exit 1 뿐이다 — exit 3 은 결과를 모르니 다시 올리지 않는다.
   */
  function driverActions(model, ctx) {
    model = model || {}; ctx = ctx || {};
    var live = model.available === true && model.configured === true;
    function gate(admin) {
      var r = [];
      if (!ctx.token) r.push("no_token"); else if (admin && !ctx.admin) r.push("admin");
      if (ctx.busy) r.push("busy");
      return r;
    }
    var r = gate(true);
    if (!model.available) r.push("na"); else if (!model.configured) r.push("not_configured");
    else if (model.running) r.push("running");
    else if (model.exit === 2) r.push("awaiting_n");
    else if (model.exit === 3) r.push("result_unknown");
    if (!VERSION_RE.test(String(ctx.version == null ? "" : ctx.version))) r.push("version_pattern");
    var start = { enabled: !r.length, reasons: r, show: live && !model.running && model.exit !== 2 };
    r = gate(true);
    if (!model.dialog) r.push("no_dialog");
    else { var nr = nReason(nModeOf(ctx.nMode, ctx.profile), ctx.typedN, model.planN); if (nr) r.push(nr); }
    var confirm = { enabled: !r.length, reasons: r, show: model.dialog === true };
    r = gate(true);
    if (!model.running) r.push("not_running");
    var abort = { enabled: !r.length, reasons: r, show: live && model.running === true };
    r = gate(true);
    if (model.running) r.push("running"); else if (model.exit === 3) r.push("result_unknown"); else if (model.exit !== 1) r.push("not_failed");
    var retry = { enabled: !r.length, reasons: r, show: live && !model.running && model.exit === 1 };
    // 예행(업로드 없음) — 플랜의 N 이 confirm_build_number 로 간다. mode=upload 버튼은 없다.
    r = gate(false);
    var doc = planEntryDoc(ctx.release && ctx.release.plan);
    if (!doc || !isNum(doc.n)) r.push("no_plan");
    if (model.running) r.push("running");
    var rehearsal = { enabled: !r.length, reasons: r, show: !!ctx.uploadPreset && ctx.releaseStatus !== 404 && !!ctx.release };
    return { start: start, confirm: confirm, abort: abort, retry: retry, rehearsal: rehearsal };
  }
  /** `POST …/release/upload` 예행 본문 — 플랜의 build_name 과 N 그대로. */
  function rehearsalBody(release) {
    var plan = release && release.plan ? release.plan : {}, doc = planEntryDoc(plan) || {};
    return { mode: "rehearsal", build_name: plan.build_name != null ? String(plan.build_name) : (doc.build_name != null ? String(doc.build_name) : ""),
      confirm_build_number: isNum(doc.n) ? String(doc.n) : "" };
  }
  // ── 바텀시트 — 진행 · 남은 것 · 심사 제출 (워크플랜 §4 · 기획 R8~R12) ────────────────────
  //
  // 0.3.3 의 «최상단 큰 막대» 는 사라지고 그 이야기가 여기로 들어왔다(결정 Q6). 시트는 **버전
  // 페이지에만** 있고(R10), 접힌 머리 한 줄이 «심사 전에 남은 것» 을 말하며(R11), 회차·빌드·제출이
  // 도는 동안에는 그 한 줄에도 막대와 진행 정도가 보인다(R8). 펼치면 단계 · 남은 것 · 이전 버전과
  // 달라진 것 · 제출 조건이다(R12).
  //
  // **판정을 새로 만들지 않는다**: 제출 조건은 `submitDecision`, 보내는 본문은 `reviewBody`,
  // 단계는 `stepperModel`, 잡 층은 `buildLayers`, 문안 차이는 `listingDiff`(또는 서버가 준
  // `version.diff`) 그대로다. 두 벌이 되면 버튼과 이유가 갈라진다.
  var SHEET_STAGES = ["V"].concat(DRIVER_STAGES);   // V S0…S8 — 분모 10 (§4.1)
  // «남은 것» 한 줄의 무게. `bad` 와 `run` 은 제출을 닫고, `todo` 는 사람이 이번 제출에서 해야
  // 하는 것(관리형 게시)이며, `warn` 은 알리기만 한다(Play 그래픽 없음 · 옛 드라이버).
  var SHEET_BLOCKING = { bad: 1, run: 1 };
  // `submitDecision` 의 이유 열쇠 → 같은 뜻의 «남은 것» 코드. 두 이름이 한 줄을 두 번 쓰지 않게 한다.
  var SHEET_REASON_ALIAS = { no_plan: "plan_missing", plan_required: "plan_missing" };

  /** 이 버전이 «진행 중» 인가(§15) — 살아 있는 심사 작업 · 살아 있는 업로드 작업 · 이 버전의
      드라이버 회차. 셋 중 마지막 하나가 끝나야 «편집 중» 으로 내려온다. */
  function versionRunning(version, dm, layers) {
    var v = version || {};
    if (v.state === "running" || v.state === "creating") return true;
    if (layers && layers.current) return true;
    return !!(dm && dm.running === true && v.release_id != null);
  }
  /** 시트가 그리는 단계 칩. 드라이버를 쓰는 저장소는 `V S0…S8` 열 칸(V 는 버전 행이 만들어진
      순간 끝난 것이다 — 행이 곧 그 단계의 산출물이다). 드라이버가 없으면 이번 회차의 잡이
      곧 단계다(`buildLayers.items`) — 없는 단계를 지어내지 않는다. */
  function sheetStages(dm, version, layers, lang) {
    var v = version || {};
    var driverKnown = dm && dm.available !== false && dm.configured === true;
    if (driverKnown) {
      var vDone = v.state != null && v.state !== "creating";
      var out = [{ id: "V", label: T(lang, "sheet.stage.V"), state: vDone ? "done" : "todo", text: "" }];
      (dm.items || []).forEach(function (it) {
        out.push({ id: it.id, label: it.label, state: it.state, text: it.text || "" });
      });
      return out;
    }
    return (layers && Array.isArray(layers.items) ? layers.items : []).map(function (it) {
      var state = it.tone === "ok" ? "done" : it.tone === "running" ? "current" : it.tone === "bad" ? "failed" : "todo";
      return { id: "#" + it.id, label: it.preset, state: state, text: it.text };
    });
  }
  /**
   * «심사 전에 남은 것» — 차례가 곧 중요도다(§4.1): 빌드 → 도는 중 → 플랜 → 빌드 번호 →
   * 관리형 게시 → 문안 → 스킬/드라이버 → 안전. 첫 항목이 접힌 머리에 나온다. 각 항목은
   * **고치는 곳**을 들고 있다(`fix.anchor` 는 이 페이지의 칸, `fix.route` 는 다른 화면).
   */
  function sheetRemaining(ctx, dm, layers, lang) {
    var version = ctx.version || {}, release = ctx.release || {}, choices = ctx.choices || {};
    var platforms = choices.platforms || {}, out = [];
    var toStatus = ctx.repo ? { route: storeHash(ctx.repo, "status"), anchor: null } : null;
    var planLink = ctx.repo ? { route: storeHash(ctx.repo, "status"), anchor: "[data-plan-review]" } : null;
    var add = function (code, severity, text, fix) { out.push({ code: code, severity: severity, text: text, fix: fix || null }); };
    var running = versionRunning(version, dm, layers);
    // 1 빌드 — 스토어에 올라간 빌드가 있어야 심사에 보낼 것이 있다
    var up = release.upload || null, upDoc = planEntryDoc(up);
    if (up && up.state === "lost") add("upload_lost", "bad", T(lang, "sheet.left.upload_lost", { id: up.job_id }), toStatus);
    else if (up && (up.state === "failed" || up.state === "timed_out" || up.state === "cancelled")) {
      add("upload_failed", "bad", T(lang, "sheet.left.upload_failed", { id: up.job_id, state: stateWord(up.state, lang) }), toStatus);
    } else if (!upDoc || upDoc.status !== "success") {
      // 2 도는 중이면 그것이 곧 «빌드가 아직 없는 이유» 다(W4) — 두 줄로 나누지 않는다
      var stage = dm && dm.stage ? dm.stage : null;
      add("build_missing", running ? "run" : "bad",
        running ? T(lang, "sheet.left.build_running", { stage: stage || DASH }) : T(lang, "sheet.left.build_missing"),
        running ? { route: null, anchor: "#sheet-stages" } : toStatus);
    } else if (running) {
      // 무엇이 돌고 있는지 이름을 댄다 — 도는 잡을 알면 그 잡, 아니면 행에 붙은 것들(§15)
      var cur = layers && layers.current ? layers.current : null;
      var what = cur ? "#" + cur.id + " " + (cur.preset || DASH) : versionHolders(version, lang).join(" · ");
      add("round_running", "run", what
        ? T(lang, "sheet.left.round_running", { what: what })
        : T(lang, "sheet.left.round_running_plain"), { route: null, anchor: "#sheet-stages" });
    }
    // 3 심사 플랜 — 있어야 하고, 안 낡았어야 하고, 스스로 ok 라고 해야 한다(E17)
    var plan = release.plan || null, planDoc = planEntryDoc(plan);
    var buildName = version.build_name != null ? String(version.build_name) : (plan && plan.build_name != null ? String(plan.build_name) : null);
    var pv = reviewPlanVerdict(release.review && release.review.plan, buildName);
    if (!planDoc || pv === "plan_required") add("plan_missing", "bad", T(lang, "sheet.left.plan_missing"), planLink);
    else if (pv === "plan_stale") add("plan_stale", "bad", T(lang, "sheet.left.plan_stale", { age: fmtAgo(release.review.plan.age_seconds, lang) }), planLink);
    else if (pv === "plan_blocked") add("plan_blocked", "bad", T(lang, "sheet.left.plan_blocked"), planLink);
    // 4 빌드 번호 — 플랜의 n 뿐이다. 자동이면 플랜이 알아야 하고, 직접이면 글자 그대로 맞아야 한다(E21)
    var nr = nReason(nModeOf(choices.nMode, ctx.profile), choices.typedN, planDoc ? planDoc.n : null);
    if (nr === "n_unknown") add("n_unknown", "bad", T(lang, "sheet.left.n_unknown"), planLink);
    else if (nr === "n_mismatch") add("n_mismatch", "bad", T(lang, "sheet.left.n_mismatch", { n: planDoc.n }), { route: null, anchor: "#sheet-n" });
    // 5 관리형 게시 — 제출마다 사람이(E18: Play 를 끄면 이 줄이 사라진다)
    if (platforms.android && choices.managed !== true) {
      add("managed_unconfirmed", "todo", T(lang, "sheet.left.managed"), { route: null, anchor: "#sheet-managed" });
    }
    // 6 문안 — 상한을 넘긴 칸마다 한 줄, 그 칸으로 간다(E9 · AC-D9)
    (ctx.page && Array.isArray(ctx.page.sections) ? ctx.page.sections : []).forEach(function (s) {
      s.groups.forEach(function (g) {
        g.fields.forEach(function (f) {
          if (!f.counter || f.counter.tone !== "bad") return;
          add("listing_bad", "bad", T(lang, "sheet.left.listing_bad", { field: f.label, count: f.counter.text }), { route: null, anchor: "#" + f.id });
        });
      });
    });
    // 7 Play 그래픽이 하나도 없다 — 경고만이다(스토어가 최종 판정이고 rcm 은 규격을 모른다)
    var graphics = ctx.graphics || {};
    if (platforms.android && graphics.android === 0) {
      add("graphics_missing", "warn", T(lang, "sheet.left.graphics"), { route: null, anchor: '[data-group="graphics"]' });
    }
    // 8 스킬이 모르는 입력 · V 단계를 모르는 드라이버 — 서버가 거절한 코드를 그대로 되풀이한다(E11 · E16)
    if (ctx.refusal === "listing_json_unsupported") add("listing_unsupported", "bad", T(lang, "sheet.left.listing_unsupported"), null);
    if (ctx.refusal === "split_version_unsupported") add("split_unsupported", "bad", T(lang, "sheet.left.split_unsupported"), null);
    if (dm && dm.configured === true && dm.knowsV === false) add("driver_no_v", "warn", T(lang, "sheet.left.driver_no_v"), null);
    // 9 안전 — 빨간 띠는 무엇으로도 못 넘는다(E19)
    if (bannerDecision(release).show) add("unsafe", "bad", T(lang, "sheet.left.unsafe"), toStatus);
    return out;
  }
  /**
   * 바텀시트 하나(§4.1). ctx:
   * `{version, release, driver(stepperModel), layers(buildLayers), profile, page(versionPageModel),
   *   graphics:{ios,android}, choices:{platforms, managed, listingFull, phased, nMode, typedN},
   *   token, admin, busy, refusal, repo, lang, nowMs, tzName}`.
   *
   * 되돌려 주는 것은 접힌 머리 한 줄 · 막대와 그 근거 · 단계 칩 · 남은 것 · 이전 버전과 달라진 것 ·
   * 제출 가능 여부와 이유 · 보낼 본문이다. 출시·게시·롤아웃은 어떤 상태에서도 없다.
   */
  function sheetModel(ctx) {
    ctx = ctx || {};
    var lang = ctx.lang, nowMs = ctx.nowMs;
    var version = ctx.version && typeof ctx.version === "object" ? ctx.version : {};
    var release = ctx.release && typeof ctx.release === "object" ? ctx.release : {};
    var dm = ctx.driver || {}, layers = ctx.layers || {}, choices = ctx.choices || {};
    var bar = layers.bar || null, jobP = bar && bar.progress ? bar.progress : null;
    var plan = release.plan || null, planDoc = planEntryDoc(plan) || {};
    var closed = version.state === "submitted" || version.state === "discarded";
    var running = versionRunning(version, dm, layers);
    var remaining = sheetRemaining(ctx, dm, layers, lang);
    var blocking = remaining.filter(function (r) { return SHEET_BLOCKING[r.severity]; });
    var decision = submitDecision({
      release: release, profile: ctx.profile, typedN: choices.typedN, nMode: choices.nMode,
      platforms: choices.platforms, managed: choices.managed, admin: ctx.admin, token: ctx.token, busy: ctx.busy
    });
    var canSubmit = !closed && decision.enabled && blocking.length === 0;
    // ── 단계와 막대 ────────────────────────────────────────────────────────
    var stages = sheetStages(dm, version, layers, lang);
    var live = dm.configured === true && !dm.idle && !dm.done && (dm.running || dm.exit != null || dm.blocked);
    var total = stages.length, done = 0, curIdx = -1;
    stages.forEach(function (it, i) { if (it.state === "done") done++; else if (it.state !== "todo" && curIdx < 0) curIdx = i; });
    var curStage = curIdx >= 0 ? stages[curIdx] : null;
    var stageLabel = curStage ? curStage.id + " " + curStage.label : null;
    var frac = jobP && isNum(jobP.pct) ? jobP.pct / 100 : 0;
    var upDoc = planEntryDoc(release.upload);
    var pct, basis;
    if (live && total) {
      // 0.3.3 의 막대와 같은 수식 — 분모만 9 에서 10(V 포함)으로 늘었다(§4.1)
      pct = Math.min(99, Math.round((done + (curIdx >= 0 ? frac : 0)) / total * 100));
      basis = T(lang, jobP && isNum(jobP.pct) ? "sheet.basis.stages_job" : "sheet.basis.stages", { total: total });
    } else if (version.state === "submitted") { pct = 100; basis = T(lang, "sheet.basis.submitted"); }
    else if (dm.done === true) { pct = 100; basis = T(lang, "sheet.basis.round_done"); }
    else if (layers.current) { pct = jobP && isNum(jobP.pct) ? jobP.pct : null; basis = bar ? bar.basis : T(lang, "build.basis.none"); }
    else if (upDoc && upDoc.status === "success") { pct = 99; basis = T(lang, "sheet.basis.uploaded"); }
    else if (total && done) { pct = Math.min(99, Math.round(done / total * 100)); basis = T(lang, "sheet.basis.stages", { total: total }); }
    else { pct = null; basis = T(lang, "build.basis.none"); }
    var nowLine = layers.now || (live && !dm.running ? dm.headParts.slice(1).join(" · ") : null) || null;
    var started = parseIso(live ? dm.startedAt : (bar && bar.startedAt) || null);
    var elapsed = isNum(started) && isNum(nowMs) ? T(lang, "sheet.elapsed", { dur: fmtDuration(Math.max(0, (nowMs - started) / 1000)) }) : null;
    var finishes = bar && bar.finishes ? bar.finishes : null;
    // ── 색 ─────────────────────────────────────────────────────────────────
    var expired = version.expired === true || version.expiry_warned === true || version.expiry_warned === 1;
    var result = resultIsCurrent(release) ? resultModel(release.review.result, lang) : null;
    var tone;
    if (version.state === "submitted") tone = "done";
    else if (version.state === "discarded") tone = "expired";
    else if (bannerDecision(release).show) tone = "bad";
    else if (release.upload && release.upload.state === "lost") tone = "lost";
    else if (live) tone = dm.running ? "running" : dm.exit === 2 ? (dm.autoN ? "running" : "human") : dm.blocked || dm.exit === 4 ? "warn" : dm.exit === 3 ? "lost" : "bad";
    else if (running) tone = "running";
    else if (version.state === "failed") tone = "bad";
    else if (expired) tone = "expired";
    else if (upDoc && upDoc.status === "success" && blocking.length === 0) tone = "ok";
    else tone = "new";
    // ── 접힌 머리 한 줄 ────────────────────────────────────────────────────
    var first = remaining.length ? remaining[0] : null;
    var submittedAt = _sheetSubmittedAt(release, version);
    var statusLine;
    if (tone === "done") statusLine = T(lang, "sheet.head.done", { clock: submittedAt ? fmtClock(submittedAt, ctx.tzName, nowMs) : DASH });
    else if (tone === "expired") statusLine = T(lang, version.state === "discarded" ? "sheet.head.discarded" : "sheet.head.expired");
    else if (tone === "running" || tone === "human" || tone === "warn" || tone === "lost" || (tone === "bad" && live)) {
      statusLine = [stageLabel ? T(lang, "sheet.stage_of", { stage: stageLabel, done: done, total: total, percent: pct != null ? pct : DASH }) : null,
        nowLine, elapsed, finishes].filter(Boolean).join(" · ");
      if (!statusLine) statusLine = first ? first.text : T(lang, "sheet.head.running");
    } else if (tone === "bad") statusLine = first ? first.text : T(lang, "sheet.head.bad");
    else if (tone === "ok") {
      statusLine = [T(lang, "sheet.head.ready"),
        release.review && release.review.plan ? T(lang, "sheet.head.plan_age", { age: fmtAgo(release.review.plan.age_seconds, lang) }) : null,
        isNum(planDoc.n) ? T(lang, "sheet.head.n", { n: planDoc.n }) : null].filter(Boolean).join(" · ");
    } else statusLine = T(lang, "sheet.head.remaining", { n: remaining.length, first: first ? first.text : DASH });
    // ── 이전 버전과 달라진 것 ───────────────────────────────────────────────
    var raw = version.diff && typeof version.diff === "object" && Array.isArray(version.diff.fields)
      ? version.diff : listingDiff(version.prefill, version.edited);
    var shots = raw.screenshots && typeof raw.screenshots === "object" ? raw.screenshots : {};
    var diff = {
      changed: raw.fields.length,
      fields: raw.fields.map(function (f) {
        return { platform: f.platform, key: f.key, label: T(lang, fieldLabelKey(f.platform, f.key)),
          old: f.old, new: f["new"], id: "f-" + f.platform + "-" + f.key,
          oldText: "− " + f.key + ": " + truncate(_sheetOneLine(f.old), 70),
          newText: "+ " + f.key + ": " + truncate(_sheetOneLine(f["new"]), 70) };
      }),
      screenshots: Object.keys(shots).map(function (p) {
        return T(lang, shots[p] === "same" ? "sheet.diff.shots_same" : "sheet.diff.shots_unknown", { store: T(lang, "review.section." + p) });
      }),
      noneText: T(lang, "sheet.diff.none")
    };
    // ── 보내는 것 ──────────────────────────────────────────────────────────
    var body = reviewBody("submit", {
      release: release, profile: ctx.profile, typedN: choices.typedN, nMode: choices.nMode,
      platforms: choices.platforms, managed: choices.managed, listingFull: choices.listingFull, phased: choices.phased
    });
    // 이름은 버전 행의 것이다 — 서버가 행의 이름과 다른 본문을 400 `build_name_mismatch` 로 거절한다
    if (version.build_name != null) body.build_name = String(version.build_name);
    var submitBody = Object.assign({ version_id: version.id != null ? version.id : null }, body);
    var sending = [
      isNum(planDoc.n) ? T(lang, "sheet.send.build", { n: planDoc.n }) : T(lang, "sheet.send.build_unknown"),
      T(lang, "review.targets." + (platformParam(choices.platforms) || "both")),
      T(lang, choices.listingFull ? "review.payload.full" : "review.payload.notes_only"),
      diff.changed ? T(lang, "sheet.send.edits", { n: diff.changed }) : T(lang, "sheet.send.no_edits"),
      T(lang, choices.phased === false ? "sheet.send.phased_off" : "sheet.send.phased_on")
    ];
    // 닫힌 이유는 **한 번씩만** 말한다. 남은 것에 이미 그 줄이 있으면 그 문장을 쓰고(«고치는
    // 곳» 까지 말하므로 더 낫다), 없는 것만 `submitDecision` 의 짧은 이유로 채운다.
    var reasons = [], texts = [], seen = {};
    blocking.forEach(function (r) { if (seen[r.code]) return; seen[r.code] = 1; reasons.push(r.code); texts.push(r.text); });
    decision.reasons.forEach(function (k) {
      var alias = SHEET_REASON_ALIAS[k] || k;
      if (seen[k] || seen[alias]) return;
      seen[k] = 1; reasons.push(k); texts.push(T(lang, "review.reason." + k));
    });
    return {
      tone: tone, closed: closed, running: running, expired: expired,
      head: {
        ver: versionTitle(version.ios_version, version.android_version),
        pill: T(lang, "sheet.pill." + tone), statusLine: statusLine,
        remainingCount: remaining.length, firstRemaining: first ? first.text : null,
        // 회차·빌드·제출이 도는 동안에는 접힌 한 줄에도 막대가 보인다(R8 · 결정 Q6)
        live: running || live || tone === "running", pct: pct
      },
      pct: pct, basis: basis, stages: stages, now: nowLine, elapsed: elapsed, finishes: finishes,
      remaining: remaining, diff: diff, sending: sending, result: result,
      canSubmit: canSubmit, reasons: reasons, reasonText: texts.join(" · "),
      managedShown: !!choices.platforms && choices.platforms.android === true,
      planN: isNum(planDoc.n) ? planDoc.n : null, nMode: nModeOf(choices.nMode, ctx.profile),
      submitBody: submitBody, policy: T(lang, "review.policy")
    };
  }
  /** diff 한 줄은 한 줄이다 — 「이 버전의 새로운 기능」은 여러 줄이라 그대로 두면 열이 무너진다. */
  function _sheetOneLine(v) { return v == null ? "" : String(v).replace(/\s+/g, " ").trim(); }
  /** 제출 시각 — 심사 잡이 끝난 때. 모르면 null 이고 화면은 — 를 그린다(지어내지 않는다). */
  function _sheetSubmittedAt(release, version) {
    var rv = release && release.review ? release.review : {};
    if (rv.result && rv.result.finished_at) return rv.result.finished_at;
    return version && version.last_edit_at ? version.last_edit_at : null;
  }
  /** 친 N 과 드라이버의 plan_n — 글자 그대로 같아야 한다(nMatches). state: empty · ok · mismatch · unknown. */
  function confirmNDecision(typed, planN, mode) {
    if (!isNum(planN) || Math.floor(planN) !== planN) return { enabled: false, state: "unknown" };
    if (mode === "auto") return { enabled: true, state: "auto" };
    if (typeof typed !== "string" || typed === "") return { enabled: false, state: "empty" };
    return nMatches(typed, planN) ? { enabled: true, state: "ok" } : { enabled: false, state: "mismatch" };
  }
  /**
   * 플랜 대화상자의 버전 초깃값 — 첫 사용엔 플랜이 없어 build_name 을 물어야 한다(서버는 빈 build_name 을
   * 400 으로 거절한다). 순서: GitHub 카드의 최신 릴리스 태그(`prod/1.0.0-180` → `1.0.0`; 날짜가 가장 늦은
   * 것, 날짜가 없으면 첫 것) → 지난 플랜의 build_name(항목 그대로, 없으면 문서의 것) → "". 지어내지 않는다.
   */
  function planVersionGuess(release, github) {
    var tags = github && Array.isArray(github.tags) ? github.tags.filter(function (t) { return t && typeof t === "object" && t.name; }) : [];
    var best = null;
    tags.forEach(function (t) { var ms = parseIso(t.at); if (!best || (ms != null && (best.ms == null || ms > best.ms))) best = { name: String(t.name), ms: ms }; });
    if (best) { var m = /(\d+\.\d+\.\d+)/.exec(best.name); if (m) return m[1]; }
    var plan = release && release.plan ? release.plan : null, doc = planEntryDoc(plan);
    if (plan && plan.build_name != null && plan.build_name !== "") return String(plan.build_name);
    if (doc && doc.build_name != null && doc.build_name !== "") return String(doc.build_name);
    return "";
  }
  var PLAN_VERSION_RE = /^\d+\.\d+\.\d+$/;
  /** Source 행의 GitHub 카드(항목 25): 미러의 최근 다섯 커밋 · 태그(최신 표시) · PR 은 null 이면 «다음». */
  function githubCardModel(gh, status, ref, lang, tzName, nowMs) {
    if (status === 404) return { state: "na", text: T(lang, "github.na"), log: [], tags: [], prs: null };
    if (!gh || typeof gh !== "object") return { state: "loading", text: T(lang, "github.loading"), log: [], tags: [], prs: null };
    var log = (Array.isArray(gh.log) ? gh.log : []).filter(function (c) { return c && typeof c === "object"; }).slice(0, 5).map(function (c) {
      return { sha: String(c.sha || "").slice(0, 7), subject: String(c.subject || ""), author: String(c.author || ""), at: c.at ? fmtClock(String(c.at), tzName, nowMs) : DASH };
    });
    var tags = (Array.isArray(gh.tags) ? gh.tags : []).filter(function (t) { return t && typeof t === "object" && t.name; }).map(function (t) {
      return { name: String(t.name), at: t.at ? fmtClock(String(t.at), tzName, nowMs) : DASH, ms: parseIso(t.at), latest: false };
    });
    var best = -1;
    tags.forEach(function (t, i) { if (t.ms != null && (best < 0 || t.ms > tags[best].ms)) best = i; });
    if (best < 0 && tags.length) best = 0;
    tags.forEach(function (t, i) { t.latest = i === best; delete t.ms; });
    var prs = gh.prs == null ? null : (Array.isArray(gh.prs) ? gh.prs : []).filter(function (p) { return p && typeof p === "object"; }).map(function (p) {
      return { number: p.number != null ? p.number : DASH, title: String(p.title || ""), state: String(p.state || "") };
    });
    return { state: "ok", ref: ref || "main", log: log, tags: tags, prs: prs,
      prsText: prs == null ? T(lang, "github.prs_next") : prs.length ? null : T(lang, "github.none") };
  }
  /**
   * 서버 호출 한 곳. `fetchFn` 은 window.fetch 모양, `tokenFn` 은 지금 토큰(없으면 null)을 준다.
   * 모든 메서드가 `{ok, status, body}` 로 풀린다 — 404 도 던지지 않는다(프로파일 PR 이 없는 서버는
   * `/api/repos` 가 404 고, 그러면 탭이 없을 뿐이다). node 테스트는 fetchFn 을 스텁으로 준다.
   */
  function makeStoreApi(fetchFn, tokenFn) {
    function call(method, path, body, contentType) {
      var headers = {};
      var tok = tokenFn ? tokenFn() : null;
      if (tok) headers.Authorization = "Bearer " + tok;
      if (contentType) headers["Content-Type"] = contentType;
      return fetchFn(path, { method: method, headers: headers, body: body, cache: "no-store" }).then(function (r) {
        return r.text().then(function (text) {
          var parsed = null;
          try { parsed = text ? JSON.parse(text) : null; } catch (e) { parsed = { error: text }; }
          return { ok: !!r.ok, status: r.status, body: parsed };
        });
      });
    }
    var enc = encodeURIComponent;
    function base(repo) { return "/api/repos/" + enc(repo); }
    return {
      repos: function () { return call("GET", "/api/repos"); },
      repo: function (name) { return call("GET", base(name)); },
      secrets: function (name) { return call("GET", base(name) + "/secrets"); },
      // 값은 text/plain, 파일은 octet-stream, 폴더 안 파일은 `/secrets/<secret>/<file>`
      putSecret: function (name, secret, body, contentType, fileName) {
        var path = base(name) + "/secrets/" + enc(secret) + (fileName ? "/" + enc(fileName) : "");
        return call("PUT", path, body, contentType || "application/octet-stream");
      },
      verify: function (name, names) {
        return call("POST", base(name) + "/verify", JSON.stringify(names && names.length ? { names: names } : {}), "application/json");
      },
      fetchRemote: function (name) { return call("POST", base(name) + "/fetch", "{}", "application/json"); },
      // ── STORE-TAB-API-2 — 릴리스 상태 · 플랜 · 심사 · 업로드 · 소개 자료 · 드라이버 ──
      release: function (name) { return call("GET", base(name) + "/release"); },
      plan: function (name, body) { return call("POST", base(name) + "/release/plan", JSON.stringify(body || {}), "application/json"); },
      review: function (name, body) { return call("POST", base(name) + "/release/review", JSON.stringify(body || {}), "application/json"); },
      upload: function (name, body) { return call("POST", base(name) + "/release/upload", JSON.stringify(body || {}), "application/json"); },
      listing: function (name) { return call("GET", base(name) + "/release/listing"); },
      listingFileUrl: function (name, path) { return base(name) + "/release/listing/file?path=" + enc(path); },
      validateListing: function (name, body) { return call("POST", base(name) + "/release/listing/validate", JSON.stringify(body || {}), "application/json"); },
      github: function (name) { return call("GET", base(name) + "/release/github"); },
      driver: function (name) { return call("GET", base(name) + "/release/driver"); },
      driverStart: function (name, body) { return call("POST", base(name) + "/release/start", JSON.stringify(body || {}), "application/json"); },
      driverConfirm: function (name, body) { return call("POST", base(name) + "/release/confirm", JSON.stringify(body || {}), "application/json"); },
      driverAbort: function (name, body) { return call("POST", base(name) + "/release/abort", JSON.stringify(body || {}), "application/json"); },
      driverRetry: function (name, body) { return call("POST", base(name) + "/release/retry", JSON.stringify(body || {}), "application/json"); },
      // ── 버전 드래프트 (워크플랜 §2.2). 상세만 5초로 폴링한다 — 하위 프로세스가 없는 넷이다 ──
      versions: function (name) { return call("GET", base(name) + "/release/versions"); },
      versionCreate: function (name, body) { return call("POST", base(name) + "/release/versions", JSON.stringify(body || {}), "application/json"); },
      version: function (name, id) { return call("GET", base(name) + "/release/versions/" + enc(String(id))); },
      // 자동 저장 — 고친 키만 싣는다. 응답이 `edited` · `diff` · `last_edit_at` 을 도로 준다.
      versionListing: function (name, id, body) {
        return call("PUT", base(name) + "/release/versions/" + enc(String(id)) + "/listing", JSON.stringify(body || {}), "application/json");
      },
      versionDiscard: function (name, id) { return call("DELETE", base(name) + "/release/versions/" + enc(String(id))); }
    };
  }
  var storePure = {
    parseRoute: parseRoute, releaseRepos: releaseRepos, setupSummary: setupSummary, fingerprintText: fingerprintText,
    dirFiles: dirFiles, verifiedCell: verifiedCell, secretRowModel: secretRowModel, dropAccept: dropAccept,
    mirrorAge: mirrorAge, rowState: rowState, rowOpen: rowOpen, setupHead: setupHead, sourceHead: sourceHead,
    naRowText: naRowText, makeStoreApi: makeStoreApi, ROW_GLYPH: ROW_GLYPH, STORE_STALE_SECONDS: STORE_STALE_SECONDS,
    // 심사 패널 본체 · Store 행 · Build·upload 행
    verdictTone: verdictTone, bannerDecision: bannerDecision, reviewPlanVerdict: reviewPlanVerdict, nMatches: nMatches,
    platformParam: platformParam, submitDecision: submitDecision, reviewBody: reviewBody, resultIsCurrent: resultIsCurrent,
    panelPill: panelPill, panelOpen: panelOpen, fieldCounter: fieldCounter, listingFields: listingFields,
    screenshotGroups: screenshotGroups, reviewInfoModel: reviewInfoModel, versionStrip: versionStrip, resultModel: resultModel,
    refusalText: refusalText, storeRowModel: storeRowModel, rowsById: rowsById, basisText: basisText, buildLayers: buildLayers,
    buildRowModel: buildRowModel, FIELD_LIMITS: FIELD_LIMITS, NOTES_LIMIT: NOTES_LIMIT, IOS_FIELDS: IOS_FIELDS, PLAY_FIELDS: PLAY_FIELDS,
    // 릴리스 드라이버 스테퍼 · GitHub 카드
    driverStage: driverStage, stepperModel: stepperModel, driverRow: driverRow, driverActions: driverActions, rehearsalBody: rehearsalBody,
    confirmNDecision: confirmNDecision, githubCardModel: githubCardModel, DRIVER_STAGES: DRIVER_STAGES,
    nModeDefault: nModeDefault, nModeOf: nModeOf, nReason: nReason, nSendValue: nSendValue,
    planVersionGuess: planVersionGuess, PLAN_VERSION_RE: PLAN_VERSION_RE,
    // 바텀시트 (§4.1) — 최상단 막대(`releaseBarModel`)가 여기로 들어왔다(결정 Q6)
    sheetModel: sheetModel, sheetStages: sheetStages, sheetRemaining: sheetRemaining,
    versionRunning: versionRunning, SHEET_STAGES: SHEET_STAGES,
    storeValueText: storeValueText, latestVerified: latestVerified, notCheckedCount: notCheckedCount,
    // 버전 목록 · 새 버전 대화상자 · 상태 띠 (워크플랜 §3.1)
    storeHash: storeHash, bumpLastNumber: bumpLastNumber, liveVersions: liveVersions, nextVersionHint: nextVersionHint,
    versionNameCheck: versionNameCheck, versionTitle: versionTitle, versionRowModel: versionRowModel,
    versionListModel: versionListModel, statusStripModel: statusStripModel, VERSION_POLL_MS: VERSION_POLL_MS,
    // W3 버전 페이지 본문 — 값·출처 · diff · 자동 저장 본문 · 남의 편집 · 저장 배지 (§3.1)
    PREFILL_KEYS: PREFILL_KEYS, VERSION_GROUPS: VERSION_GROUPS, VERSION_SAVE_DEBOUNCE_MS: VERSION_SAVE_DEBOUNCE_MS,
    fieldLimit: fieldLimit, listingNorm: listingNorm, listingDiff: listingDiff, fieldLabelKey: fieldLabelKey,
    versionFieldModel: versionFieldModel, versionPageModel: versionPageModel, listingPutBody: listingPutBody,
    remoteListingEdits: remoteListingEdits, saveBadge: saveBadge
  };

  var rcm = {
    DASH: DASH, esc: esc, fmtDuration: fmtDuration, fmtClock: fmtClock, fmtClockSeconds: fmtClockSeconds, fmtAgo: fmtAgo,
    fmtCoarse: fmtCoarse, fmtCountdown: fmtCountdown, fmtBytes: fmtBytes, fmtBytesPair: fmtBytesPair, fmtMemory: fmtMemory, fmtDisk: fmtDisk, fmtMb: fmtMb, fmtPct: fmtPct,
    ordinal: ordinal, truncate: truncate, stateWord: stateWord, stateGlyph: stateGlyph, personLabel: personLabel,
    reasonText: reasonText, confidenceBadge: confidenceBadge, etaText: etaText,
    elapsedText: elapsedText, notMoving: notMoving, yourJobs: yourJobs, isMine: isMine, cancelLocked: cancelLocked, hostPressure: hostPressure,
    jobStorageLine: jobStorageLine,
    queueHeader: queueHeader, sortQueue: sortQueue, workerPills: workerPills, workerName: workerName, hostCards: hostCards, headerNote: headerNote, progressHead: progressHead, progressHeadHtml: progressHeadHtml, queueGroups: queueGroups, runningStep: runningStep,
    stepMark: stepMark, overallProgress: overallProgress, progressBarHtml: progressBarHtml, subProgress: subProgress, unitsGridHtml: unitsGridHtml, timePct: timePct, recentLine: recentLine, recentDetail: recentDetail, artifactsLine: artifactsLine, outcomeText: outcomeText, workerState: workerState, rerunCommand: rerunCommand, shellQuote: shellQuote, transitionsLine: transitionsLine,
    sourceHtml: sourceHtml, priorityChip: priorityChip, cacheText: cacheText,
    poolHeader: poolHeader, poolSummary: poolSummary, poolsOf: poolsOf, recentOf: recentOf,
    connection: connection, nextBackoff: nextBackoff, ACTIONABLE: ACTIONABLE, TERMINAL: TERMINAL,
    LOST_AFTER_MS: LOST_AFTER_MS, POLL_MS: POLL_MS,
    store: storePure
  };
  if (typeof module !== "undefined" && module.exports) module.exports = rcm;
  if (typeof globalThis !== "undefined") globalThis.rcm = rcm;
  else if (root) root.rcm = rcm;
  if (typeof document === "undefined") return;

  // ═══════════════════════════════════════════════════════════════════════════
  // DOM — 여기서부터는 브라우저에서만 돈다.
  // ═══════════════════════════════════════════════════════════════════════════
  var $ = function (sel, el) { return (el || document).querySelector(sel); };
  var $$ = function (sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); };
  var state = {
    status: null, prev: null, skewMs: 0, conn: connection(null, "init", Date.now()),
    token: null, me: null, admin: false, tokenBad: false, readAuth: false, skewUnknown: false, lastTrigger: null,
    lang: I18N.DEFAULT_LANG,
    expanded: {}, expandedRecent: {}, showAllRecent: false, showAllQueue: false,
    // 이름별 실패 이력은 `/api/status` 에 없다(결정 67) — 행을 펼칠 때 그 잡만 한 번 받는다
    recentFailures: {},
    // 호스트 이름 → 직전 압력 판정. 「바쁨」에 이력을 주는 기억이고 이 페이지에만 산다.
    hostVerdicts: {},
    es: null, retryTimer: null, pollTimer: null, refetchTimer: null, hiddenSince: null, lostShownAt: null,
    drawer: { jobId: null, offset: 0, timer: null, lines: 0 }, cancelTarget: null, hl: null, tz: null,
    // 스토어 탭. `repos` 는 릴리스 프로파일이 있는 저장소(null = 아직 안 물어봄). 비밀 값은 여기 없다.
    view: "queue", store: { api: null, repos: null, reposStatus: null, repo: null, doc: null, secrets: null, screen: null,
      error: null, fetchError: null, fetching: false, verifying: false, loadedAt: null, timer: null, seq: 0, dialogSecret: null,
      // 릴리스 상태(`GET …/release`) · 소개 자료(`GET …/release/listing`) · 사람의 선택. 선택은 이 페이지에만 산다 —
      // 관리형 게시 체크와 친 N 은 새 플랜이 오면 지워지고 localStorage 에 가지 않는다(결정 3 · 항목 28).
      release: null, releaseStatus: null, listing: null, listingStatus: null, busy: null, reviewError: null, reviewCode: null, planError: null, validate: null,
      review: { platforms: { ios: true, android: true }, managed: false, listingFull: false, phased: true, typedN: "", planId: null, buildName: null },
      // 릴리스 드라이버(`GET …/release/driver`) · GitHub 카드(`GET …/release/github`). Start 폼의 값과 S2 의 친 N 도
      // 이 페이지에만 산다 — 회차(build_name · plan_n)가 바뀌면 지워진다. `driverDialogKey` 는 S2 대화상자를 회차마다 한 번만 저절로 연다.
      driver: null, driverStatus: null, github: null, githubStatus: null, driverError: null, driverN: "", driverDialogKey: null,
      nMode: null,   // 빌드 번호 모드 — null 이면 프로파일 기본값(nModeDefault)
      driverForm: { version: null, track: "", dryRun: false },
      // 버전 목록 · 새 버전 · 버전 하나(워크플랜 §3). `sub` 는 해시가 정한다(versions · version · status).
      // `versionTimer` 는 «만드는 중» 일 때만 도는 5초 폴링이고 상세 하나만 부른다 — 드라이버와
      // 소개 자료는 서버에서 프로세스를 돌리므로 이 화면에서 절대 폴링하지 않는다(§15).
      sub: "versions", versionId: null, versions: null, versionsStatus: null,
      version: null, versionStatus: null, versionTimer: null, versionError: null,
      newVersion: null, createError: null, creating: false, discardTarget: null, discarding: false,
      // W3 문안 편집(§3.2). `typed` 는 이 브라우저가 친 값이라 저장이 실패해도 화면에 남고(E7),
      // `seen` 은 서버의 편집본을 마지막으로 본 모습이다 — 폴링이 그것과 달라지면 «다른 곳에서
      // 바뀜»(E8). 값은 state 에만 산다 — localStorage 에 문안을 남기지 않는다.
      vedit: null,
      // 바텀시트(§4) — 접힘/펼침은 저장소마다 기억하고(`rcm.sheet.<repo>`), 드라이버는 상세
      // 폴링(5초)보다 **느리게** 따로 받는다: 그 라우트는 빌드 머신에서 `--status` 를 돌린다.
      driverTimer: null, draftTimer: null, fixAnchor: null,
      gateReturn: null }
  };
  function now() { return state.skewUnknown ? NaN : Date.now() + state.skewMs; }
  /** 지금을 ISO 로 — 시계 차이를 모르면 null(나이를 지어내지 않는다). */
  function nowIso() { var t = now(); return isNum(t) ? new Date(t).toISOString() : null; }
  function tz() { return state.tz || undefined; }

  // ── 저장소 ──
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* 비공개 탭 등 */ } }
  // 접힘이 아니라 **펼침**을 기억한다(2026-09-09 오너 결정 13 개정): 모든 행은 접힌 채로 뜨고,
  // 사람이 편 행만 그 브라우저에 남는다. 옛 키(`rcm.collapsed`)는 읽지 않는다 — 뜻이 뒤집혔다.
  function loadExpanded() { try { (JSON.parse(lsGet("rcm.expanded") || "[]") || []).forEach(function (id) { state.expanded[id] = true; }); } catch (e) { state.expanded = {}; } }
  function saveExpanded() { lsSet("rcm.expanded", JSON.stringify(Object.keys(state.expanded).map(Number))); }

  // ── 언어 (결정 36·38) ──
  // 기본은 한국어다. 브라우저 언어를 보지 않는다 — 고르면 그 브라우저에 남는다.
  function L() { return state.lang; }
  function tr(key, args) { return T(state.lang, key, args); }
  function loadLang() {
    var m = /[?&]lang=(ko|en)(&|$)/.exec(location.search);
    if (m) { state.lang = m[1]; lsSet("rcm.lang", m[1]); return; }
    state.lang = I18N.normalize(lsGet("rcm.lang"));
  }
  function setLang(lang) {
    var next = I18N.normalize(lang);
    if (next === state.lang) return;
    state.lang = next;
    lsSet("rcm.lang", next);
    applyLang();
  }
  /** 정적 노드(`data-i18n*`)와 문서 언어를 지금 언어로. 다시 그려지지 않는 것들이라 직접 훑는다. */
  function applyStatic() {
    document.documentElement.lang = state.lang;
    $$("[data-i18n]").forEach(function (el) { el.textContent = tr(el.getAttribute("data-i18n")); });
    $$("[data-i18n-html]").forEach(function (el) { el.innerHTML = tr(el.getAttribute("data-i18n-html")); });
    $$("[data-i18n-attr]").forEach(function (el) {
      el.getAttribute("data-i18n-attr").split(",").forEach(function (pair) {
        var bits = pair.split(":");
        if (bits.length === 2) el.setAttribute(bits[0].trim(), tr(bits[1].trim()));
      });
    });
    var title = $("[data-i18n='page.title']");
    if (title) document.title = title.textContent;
    var btn = $("#lang-btn");
    if (btn) { btn.textContent = tr("lang.button"); btn.setAttribute("aria-pressed", state.lang === "en" ? "true" : "false"); }
  }
  /** 언어를 바꾼 뒤 화면 전체를 다시 그린다. 열려 있는 대화상자·서랍도 다시 쓴다. */
  function applyLang() {
    applyStatic();
    render();
    renderTokenButton();
    renderNav(); renderStore();
    if (state.cancelTarget) openCancel(state.cancelTarget);
    if (state.drawer.jobId != null) {
      var d = $("[data-drawer-title]");
      if (d) d.textContent = tr("drawer.job", { id: state.drawer.jobId, key: state.drawer.key || "" });
    }
  }

  // ── HTTP ──
  function api(path, opts) {
    opts = opts || {};
    var headers = opts.headers || {};
    if (state.token) headers.Authorization = "Bearer " + state.token;
    return fetch(path, { method: opts.method || "GET", headers: headers, body: opts.body, cache: "no-store" });
  }

  // ── 갱신 ──
  function scheduleRefetch() {
    if (state.refetchTimer) return;
    state.refetchTimer = setTimeout(function () { state.refetchTimer = null; fetchStatus(); }, REFETCH_COALESCE_MS);
  }
  function fetchStatus() {
    if (state.conn.mode === "paused") return Promise.resolve();
    return api("/api/status").then(function (r) {
      if (r.status === 401 || r.status === 403) {
        // 토큰이 있는데 거부 → Token rejected. 토큰이 없는 401 → 이 서버는 읽기에도 토큰이 필요하다(read_auth)
        if (state.token) tokenRejected(); else { state.readAuth = true; renderTokenButton(); }
        return null;
      }
      state.readAuth = false;
      if (!r.ok) throw new Error("status " + r.status);
      return r.json();
    }).then(function (doc) {
      if (!doc) return;
      var arrived = Date.now();
      var gen = parseIso(doc.generated_at);
      state.skewUnknown = gen == null;  // generated_at 을 못 읽으면 상대 시간은 전부 — (조용히 브라우저 시계로 안 간다)
      if (gen != null) state.skewMs = gen - arrived;
      state.prev = state.status; state.status = doc;
      state.conn = connection(state.conn, "status_ok", now());
      state.lostShownAt = null;
      render();
    }).catch(function () {
      /* 실패는 conn.tick 이 30초 뒤 lost 로 만든다 — 마지막 상태를 그대로 둔다 */
      renderHeaderConn();
    });
  }
  function openSse() {
    // `?poll=1` 은 SSE 를 열지 않고 10초 폴링만 한다(headless 렌더·디버그용 — 열린 스트림은 load/네트워크 idle 을 막는다)
    if (state.noSse || state.es || state.conn.mode === "paused" || typeof EventSource === "undefined") return;
    var es;
    try { es = new EventSource("/events"); } catch (e) { onSseError(); return; }
    state.es = es;
    es.onopen = function () { state.conn = connection(state.conn, "sse_open", now()); renderHeaderConn(); stopPolling(); };
    es.onerror = function () { onSseError(); };
    ["job_changed", "job_finished", "marker", "host_sample", "server", "reset", "lag"].forEach(function (kind) {
      es.addEventListener(kind, function () { scheduleRefetch(); });
    });
    es.addEventListener("hello", function () { /* 연결 확인 — 상태는 onopen 에서 */ });
  }
  function closeSse() { if (state.es) { try { state.es.close(); } catch (e) { /* 무시 */ } state.es = null; } }
  function onSseError() {
    closeSse();
    state.conn = connection(state.conn, "sse_error", now());
    renderHeaderConn();
    startPolling();
    clearTimeout(state.retryTimer);
    state.retryTimer = setTimeout(function () { state.retryTimer = null; openSse(); }, state.conn.retryIn * 1000);
  }
  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(function () { if (state.conn.mode !== "paused") fetchStatus(); }, POLL_MS);
  }
  function stopPolling() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }
  function pauseUpdates(kind) {
    state.conn = connection(state.conn, kind, now());
    closeSse(); stopPolling(); clearTimeout(state.retryTimer); state.retryTimer = null;
    renderHeaderConn();
  }
  function resumeUpdates(kind) {
    state.conn = connection(state.conn, kind, now());
    renderHeaderConn();
    fetchStatus().then(function () { openSse(); });
  }
  function tick() {
    var before = state.conn.mode;
    state.conn = connection(state.conn, "tick", now());
    if (state.conn.mode !== before) render(); else { tickTexts(); renderLostBanner(); }
  }

  // ── 토큰 (항목 4 · 29) ──
  function tokenRejected() {
    state.tokenBad = true; state.me = null; state.admin = false; state.token = null; lsSet("rcm.token", null);
    renderTokenButton(); render(); if (state.view === "store") renderStore();
  }
  function verifyToken(tok, silent) {
    var status = $("[data-tok-status]");
    if (status && !silent) status.textContent = tr("token.checking");
    var headers = tok ? { Authorization: "Bearer " + tok } : {};
    return fetch("/api/whoami", { headers: headers, cache: "no-store" }).then(function (r) {
      if (r.status === 401 || r.status === 403) return { bad: true };
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    }).then(function (me) {
      if (me && me.bad) { state.token = null; state.me = null; state.tokenBad = true; lsSet("rcm.token", null); if (status) status.textContent = tr("token.rejected"); return false; }
      state.token = tok; state.me = me && me.name ? me.name : null; state.admin = !!(me && me.admin === true); state.tokenBad = false; lsSet("rcm.token", tok);
      if (status) status.textContent = "ok · " + (state.me || "") + (me && me.admin ? " (admin)" : "");
      return true;
    }).catch(function () {
      // 네트워크 오류 — 저장값은 지키고 검증만 못 한 것
      state.token = tok; state.tokenBad = false;
      if (status) status.textContent = tr("token.kept");
      return null;
    }).then(function (ok) { renderTokenButton(); if (ok !== false) fetchStatus(); storeAuthChanged(); return ok; });
  }
  function renderTokenButton() {
    var b = $("#tok-btn");
    if (!b) return;
    b.classList.toggle("bad", !!state.tokenBad);
    // 열쇠는 **여기서** 붙인다 — 카탈로그는 글자만 담는다(§2.4). `index.html` 의 `data-i18n` 을
    // 뗀 것도 그래서다: `applyStatic` 이 textContent 로 덮으면 아이콘이 사라진다.
    var label = state.tokenBad ? tr("token.bad_button")
      : (state.me ? tr("token.named", { name: state.me })
        : (state.token ? tr("token.unverified")
          : (state.readAuth ? tr("token.read_auth") : tr("token.add"))));
    b.innerHTML = icon("key") + " " + esc(label);
  }
  function wireTokenDialog() {
    var dlg = $("#tok-dialog"), input = $("#tok-input");
    if (!dlg) return;
    $("#tok-btn").addEventListener("click", function () {
      $("[data-tok-status]").textContent = state.tokenBad ? tr("token.rejected_hint") : "";
      input.value = "";
      if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
      input.focus();
    });
    $("[data-tok-cancel]").addEventListener("click", function () { dlg.close(); });
    $("[data-tok-forget]").addEventListener("click", function () {
      state.token = null; state.me = null; state.admin = false; state.tokenBad = false; lsSet("rcm.token", null); renderTokenButton(); render(); storeAuthChanged(); dlg.close();
    });
    dlg.querySelector("form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var tok = input.value.trim();
      if (!tok) return;
      verifyToken(tok).then(function (ok) { if (ok) dlg.close(); });
    });
    window.addEventListener("storage", function (ev) {
      if (ev.key === "rcm.token") { state.token = ev.newValue; state.tokenBad = false; state.me = null; state.admin = false; if (state.token) verifyToken(state.token, true); else { renderTokenButton(); render(); storeAuthChanged(); } }
      if (ev.key === "rcm.expanded") { state.expanded = {}; loadExpanded(); renderQueue(); }
      // 다른 탭에서 언어를 바꾸면 이 탭도 따라온다
      if (ev.key === "rcm.lang") { state.lang = I18N.normalize(ev.newValue); applyLang(); }
    });
  }

  // ── 렌더: 머리 ──
  function hostName() {
    var p = pool0(state.status);
    var h = p && Array.isArray(p.hosts) && p.hosts[0];
    if (h && h.name) return h.name;
    return location.hostname || "server";
  }
  function renderHeaderConn() {
    var b = $("#live-btn");
    if (!b) return;
    var c = state.conn;
    var age = isNum(c.lastOkAt) ? fmtAgo((now() - c.lastOkAt) / 1000, L()) : DASH;
    var text;
    b.className = "livebtn " + c.mode;
    b.setAttribute("aria-pressed", c.mode === "paused" ? "true" : "false");
    if (c.mode === "live") text = tr("conn.live", { age: age });
    else if (c.mode === "polling") text = tr("conn.polling", { age: age });
    else if (c.mode === "lost") text = tr("conn.lost", { age: age });
    else text = tr("conn.paused");
    $("[data-live-text]").textContent = text;
    renderLostBanner();
  }
  function renderLostBanner() {
    var el = $("#banner-lost");
    if (!el) return;
    var c = state.conn;
    if (c.mode !== "lost") { el.hidden = true; return; }
    el.hidden = false;
    var age = isNum(c.lastOkAt) ? fmtAgo((now() - c.lastOkAt) / 1000, L()) : DASH;
    $("[data-lost-text]").textContent = tr("conn.lost_banner", { host: hostName(), age: age });
    var retry = state.retryTimer ? tr("conn.reconnecting", { countdown: fmtCountdown(c.retryIn, L()) }) : (state.pollTimer ? tr("conn.polling_every") : "");
    $("[data-lost-sub]").textContent = retry;
  }
  function renderHeader() {
    var st = state.status || {};
    var server = st.server || {};
    $("[data-host-name]").textContent = hostName();
    var busy = busyCount(st), lanes = laneCount(st);
    $("[data-lanes-text]").textContent = isNum(busy) && isNum(lanes) && lanes > 1 ? tr("header.lanes_busy", { busy: busy, lanes: lanes }) : "";
    var pills = workerPills(server, L()).map(function (p) {
      var tag = p.jobId != null ? "button" : "span";
      return "<" + tag + ' class="wk ' + esc(p.cls) + '"' + (p.jobId != null ? ' data-goto="' + p.jobId + '" type="button"' : "") + '><i aria-hidden="true"></i>' + esc(p.text) + "</" + tag + ">";
    }).join("");
    $("[data-workers]").innerHTML = pills;
    var err = $("[data-errchip]");
    if (server.last_error) { err.hidden = false; err.textContent = "error · " + truncate(server.last_error, 60); err.title = server.last_error; }
    else err.hidden = true;
    var skew = $("[data-skew]");
    if (state.skewUnknown) { skew.hidden = false; skew.textContent = "clock unknown"; }
    else if (Math.abs(state.skewMs) > 30000) { skew.hidden = false; skew.textContent = "clock " + (state.skewMs > 0 ? "+" : "-") + fmtDuration(Math.abs(state.skewMs) / 1000); }
    else skew.hidden = true;
    var note = headerNoteKind(state.status, now(), state.prev, L());
    var noteEl = $("#banner-note");
    if (note && note.kind === "reload") {
      if (!sessionStorage.getItem("rcm.reloaded")) { sessionStorage.setItem("rcm.reloaded", "1"); location.reload(); return; }
      noteEl.hidden = false; noteEl.textContent = note.text; noteEl.className = "banner bad";
    } else if (note && note.kind === "restart") {
      noteEl.hidden = false; noteEl.textContent = note.text; noteEl.className = "banner warn";
    } else if (server.paused) {
      noteEl.hidden = false; noteEl.className = "banner warn";
      noteEl.innerHTML = esc(tr("header.paused", { by: server.paused.by || DASH, clock: fmtClock(server.paused.at, tz(), now()) })) + '<span class="mono">rcm resume</span>';
    } else {
      var localWorkers = (server.workers || []).filter(function (w) { return !w.worker; });
      var downs = localWorkers.filter(function (w) { return w.state === "down"; });
      var remoteDown = (server.workers || []).filter(function (w) { return w.worker && w.state === "down"; });
      if (downs.length) {
        var live = localWorkers.filter(function (w) { return w.state !== "down"; }).map(function (w) { return w.lane; });
        noteEl.hidden = false; noteEl.className = "banner bad";
        noteEl.textContent = tr("header.worker_stopped", {
          lanes: downs.map(function (w) { return w.lane; }).join(", "),
          error: errorText(downs[0].error, downs[0].error_code)
        }) + (live.length ? tr("header.lanes_left", { lanes: live.join(", ") }) : tr("header.nothing_can_start"));
      } else if (remoteDown.length) {
        // 원격 워커(M5b-2)가 heartbeat 을 멈췄다 — 그 풀의 잡은 서버가 lost 로 남긴다
        var names = []; remoteDown.forEach(function (w) { if (names.indexOf(w.worker) < 0) names.push(w.worker); });
        noteEl.hidden = false; noteEl.className = "banner warn";
        noteEl.textContent = tr("header.worker_unreachable", { names: names.join(", ") });
      } else {
        var stalled = notScheduledRow();
        if (stalled) { noteEl.hidden = false; noteEl.className = "banner warn"; noteEl.textContent = tr("header.not_scheduled", { id: stalled.id, dur: fmtDuration(stalled.estimate && stalled.estimate.waited_seconds) }); }
        else noteEl.hidden = true;
      }
    }
    renderHeaderConn();
    state.footBase = tr("footer.server", { version: server.version || DASH, uptime: fmtDuration(server.uptime_seconds), schema: st.schema_version || DASH });
    $("[data-foot-server]").textContent = state.footBase;
    if (state.debug) setTimeout(debugLayout, 0);
  }
  // `?debug=1`: 레이아웃 진단 — 뷰포트보다 넓은 요소를 푸터에 적는다(모바일 넘침 추적용)
  function debugLayout() {
    var vw = document.documentElement.clientWidth;
    var wide = [];
    $$("body *").forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > vw + 1) wide.push({ el: el, right: Math.round(r.right) });
    });
    wide.sort(function (a, b) { return b.right - a.right; });
    var desc = wide.slice(0, 8).map(function (w) {
      var e = w.el;
      return (e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + (e.className && typeof e.className === "string" ? "." + e.className.trim().split(/\s+/).join(".") : "")) + "@" + w.right;
    });
    $("[data-foot-server]").textContent = state.footBase + " · debug vw=" + vw + " scroll=" + document.documentElement.scrollWidth + " wide=" + desc.join(" | ");
  }
  function notScheduledRow() {
    var q = queueOf(state.status);
    if (!Array.isArray(q)) return null;
    for (var i = 0; i < q.length; i++) if (q[i].reason === "not_scheduled") return q[i];
    return null;
  }

  // ── 렌더: 요약 ──
  /**
   * 호스트 하나의 압력 — **직전 판정을 기억해서** 넘긴다(이력). 요약 한 줄과 호스트 절이 같은
   * 기억을 쓰므로 둘이 갈라질 수 없다. 같은 표본을 다시 먹여도 결과가 같아(고정점) 한 렌더에서
   * 몇 번을 불러도 안전하다.
   */
  function pressureOf(host) {
    if (!host) return hostPressure(host);
    var key = host.name || "";
    var hp = hostPressure(host, state.hostVerdicts[key]);
    state.hostVerdicts[key] = hp.verdict;
    return hp;
  }
  function jl(id, text) { return '<button type="button" class="jlink" data-goto="' + id + '">' + esc(text) + "</button>"; }
  function renderSummary() {
    var st = state.status;
    var p = pool0(st);
    var lost = state.conn.mode === "lost";
    // 23
    var yj = yourJobs(st, state.me, L());
    var y;
    if (yj.kind === "no_token") y = '<span class="muted">' + esc(tr("summary.add_token")) + "</span>";
    else if (yj.kind === "unknown") y = '<span class="muted">' + esc(tr("summary.queue_unknown")) + "</span>";
    else if (yj.kind === "none") y = '<span class="muted">' + esc(tr("summary.no_jobs")) + "</span>";
    else y = yj.lines.map(function (l) { return jl(l.jobId, "#" + l.jobId) + " " + esc(l.text); }).join("<br>") + (yj.more ? '<br><span class="muted">' + esc(tr("summary.and_more", { n: yj.more })) + "</span>" : "");
    $("[data-yours]").innerHTML = (lost ? '<span class="muted">' + esc(tr("summary.last_known")) + "</span>" : "") + y;
    // 24
    var nm = notMoving(st, state.me, L());
    var s;
    if (nm.kind === "unknown") s = '<span class="muted">' + esc(tr("summary.queue_unknown")) + "</span>";
    else if (nm.kind === "ok") s = lost
      ? '<span class="muted">' + esc(tr("summary.last_known") + tr("summary.nothing_stuck")) + "</span>"
      : '<span class="ok">' + esc(tr("summary.nothing_stuck")) + "</span>";
    else s = nm.lines.slice(0, 3).map(function (l) { return jl(l.jobId, "#" + l.jobId) + ' <span class="warn">' + esc(l.text) + "</span>"; }).join("<br>") + '<br><span class="muted">' + esc(tr("summary.nothing_else_stuck")) + "</span>";
    $("[data-stuck]").innerHTML = (lost && nm.kind === "list" ? '<span class="muted">' + esc(tr("summary.last_known")) + "</span><br>" : "") + s;
    // 25
    var host = p && Array.isArray(p.hosts) ? p.hosts[0] : null;
    var hp = pressureOf(host);
    var lab = $("[data-host-lab]");
    var h;
    if (p && p.hosts === null) {
      h = '<span class="bad">' + esc(tr("summary.host_unavailable")) + "</span>"
        + (p.hosts_error ? ' <span class="muted">' + esc(truncate(errorText(p.hosts_error, p.hosts_error_code), 60)) + "</span>" : "");
      lab.textContent = tr("summary.host");
    } else if (hp.verdict === "no_sample") { h = '<span class="muted">' + esc(tr("summary.host_no_sample")) + "</span>"; lab.textContent = tr("summary.host"); }
    else {
      var age = secondsSince(host.sampled_at, now());
      var stale = host.stale || (isNum(age) && isNum(host.interval_seconds) && age > 3 * host.interval_seconds);
      lab.textContent = tr("summary.host_sampled", { age: fmtAgo(age, L()) });
      var mark = function (v) { return isNum(v) ? (v >= 85 ? '<b class="warn">' + fmtPct(v) + "</b>" : "<b>" + fmtPct(v) + "</b>") : "<b>" + DASH + "</b>"; };
      var vcls = hp.verdict === "fine" ? "ok" : hp.verdict === "busy" ? "warn" : "muted";
      var vkey = "summary.verdict_" + (hp.verdict === "fine" || hp.verdict === "busy" || hp.verdict === "partial" ? hp.verdict : "unknown");
      var verdict = '<span class="' + vcls + '">· ' + esc(tr(vkey)) + "</span>";
      // 남은 공간은 디스크 숫자에 **붙여서** 그린다 — 문장 끝에 매달면 바로 앞의 GPU 것처럼 읽힌다
      var diskLow = (isNum(hp.disk) && hp.disk >= 85) || (isNum(hp.diskFree) && hp.diskFree < DISK_LOW_FREE);
      var diskMark = (diskLow ? '<b class="warn">' : "<b>") + fmtPct(hp.disk) + "</b>"
        + (isNum(hp.diskFree) ? ' <span class="' + (diskLow ? "warn" : "muted") + '">(' + esc(tr("summary.disk_free", { free: fmtDisk(hp.diskFree) })) + ")</span>" : "");
      h = esc(tr("summary.pressure", { cpu: "\u0000cpu\u0000", mem: "\u0000mem\u0000", gpu: "\u0000gpu\u0000", disk: "\u0000disk\u0000" }))
        .replace("\u0000cpu\u0000", mark(hp.cpu)).replace("\u0000mem\u0000", mark(hp.mem)).replace("\u0000gpu\u0000", mark(hp.gpu))
        .replace("\u0000disk\u0000", diskMark)
        + "<br>" + esc(tr("summary.load", { load: hp.load })) + " " + verdict
        + (stale ? ' <span class="stale-badge">' + esc(tr("host.stale", { dur: fmtDuration(age) })) + "</span>" : "");
    }
    $("[data-pressure]").innerHTML = h;
  }

  /**
   * 막대 채움의 폭은 **DOM 에 넣은 뒤에** 스타일로 준다. 자동 레이아웃 표의 `colspan` 칸 안에서는
   * 파싱 시점의 퍼센트 폭이 「폭을 모름 → auto(=100%)」로 굳어 다시 계산되지 않는다(Chrome 실측:
   * 616px 막대 안의 `width:25%` 가 616px 로 그려졌다 — 25% 라고 적힌 막대가 가득 차 보인다).
   * 넣은 뒤에 주면 그때는 칸 폭이 정해져 있어 제대로 풀린다. HTML 에는 값만 `data-fill` 로 싣는다.
   */
  function applyBarFills(root) {
    $$("[data-fill]", root).forEach(function (el) { el.style.width = el.getAttribute("data-fill") + "%"; });
  }
  /** 큐 본문을 갈아 끼운다 — 채움 폭 적용을 한자리에 묶어 둔다(빠뜨리면 막대가 거짓말을 한다). */
  function setQueueHtml(body, html) {
    body.innerHTML = html;
    applyBarFills(body);
  }

  // ── 렌더: 큐 ──
  function expandedGc() {
    var q = queueOf(state.status) || [];
    var ids = {};
    q.forEach(function (r) { ids[r.id] = true; });
    var changed = false;
    Object.keys(state.expanded).forEach(function (k) { if (!ids[k]) { delete state.expanded[k]; changed = true; } });
    if (changed) saveExpanded();
  }
  /** 큐 표의 머리줄. 두 곳(기본 풀·다른 풀)이 같은 것을 쓴다 — 갈라지지 않게 한 함수로. */
  function queueHeadHtml(tzName) {
    return "<thead><tr><th>" + esc(tr("queue.col_job")) + "</th><th>" + esc(tr("queue.col_key"))
      + "</th><th>" + esc(tr("queue.col_requester")) + "</th><th>" + esc(tr("queue.col_reason"))
      + "</th><th>" + esc(tr("queue.col_elapsed")) + "</th><th>" + esc(tr("queue.col_eta"))
      + ' <span class="tzh">· ' + esc(tzName) + '</span></th><th class="source">'
      + esc(tr("queue.col_source")) + "</th></tr></thead>";
  }

  function renderQueue() {
    var st = state.status;
    var p = pool0(st);
    var body = $("[data-queue-body]");
    $("[data-queue-header]").textContent = queueHeader(st, now(), L());
    if (!p) { body.innerHTML = '<div class="banner bad" role="alert" data-error="queue">' + esc(tr("queue.no_status")) + "</div>"; return; }
    if (p.queue === null || p.queue === undefined) {
      body.innerHTML = '<div class="banner bad" role="alert" data-error="queue">' + esc(tr("queue.unavailable", { error: errorText(p.queue_error, p.queue_error_code) })) + "</div>";
      return;
    }
    var server = st.server || {};
    var extra = !p.queue.length ? extraPoolsQueueHtml(st) : "";
    if (extra) {
      setQueueHtml(body, '<div class="empty">' + esc(tr("queue.empty_other_pools")) + "</div>" + extra);
      return;
    }
    if (!p.queue.length) {
      var localOnly = Array.isArray(server.workers) ? server.workers.filter(function (w) { return !w.worker; }) : [];
      var allDown = localOnly.length && localOnly.every(function (w) { return w.state === "down"; });
      var presets = Array.isArray(st.presets) ? st.presets.map(function (x) { return x.name; }).join(" · ") : "";
      var emptyHtml = server.paused || allDown
        ? esc(tr("queue.empty_paused"))
        : esc(tr("queue.empty")) + "<code>rcm run &lt;preset&gt;</code>" + esc(tr("queue.empty_hint"));
      body.innerHTML = '<div class="empty">' + emptyHtml + (presets ? '<br><span class="sub">' + esc(tr("queue.presets", { names: presets })) + "</span>" : "") + "</div>";
      return;
    }
    expandedGc();
    var rows = sortQueue(p.queue);
    var nm = notMoving(st, state.me, L());
    var stuckIds = {};
    nm.lines.forEach(function (l) { stuckIds[l.jobId] = true; });
    var waiting = rows.filter(function (r) { return r.state !== "running" && r.state !== "cancelling"; });
    var hiddenCount = 0;
    if (!state.showAllQueue && waiting.length > 20) {
      var keep = {};
      waiting.slice(0, 20).forEach(function (r) { keep[r.id] = true; });
      rows = rows.filter(function (r) { var w = r.state !== "running" && r.state !== "cancelling"; if (!w) return true; if (keep[r.id] || isMine(r, state.me) || stuckIds[r.id] || r.id === state.hl) return true; hiddenCount++; return false; });
    }
    var tzName = st.display_timezone || "local";
    // 도는 것과 기다리는 것을 눈으로 갈라 놓는다 — 한 덩어리면 뭐가 도는지 안 읽힌다(§4.6-라)
    var html = '<div class="qwrap"><table class="q">' + queueHeadHtml(tzName) + "<tbody>";
    queueGroups(rows, L()).forEach(function (g) {
      html += '<tr class="qgroup ' + g.key + '"><th colspan="7" scope="colgroup">' + esc(g.title) + "</th></tr>";
      if (!g.rows.length) {
        html += '<tr class="qgroup-empty"><td colspan="7">' + esc(tr(g.key === "running" ? "queue.group_none_running" : "queue.group_none_waiting")) + "</td></tr>";
        return;
      }
      g.rows.forEach(function (row) { html += queueRowHtml(row, st); });
    });
    html += "</tbody></table></div>";
    if (hiddenCount) html += '<button type="button" class="more" data-more-queue>' + esc(tr("queue.more", { n: hiddenCount })) + " " + icon("chevronDown") + "</button>";
    html += extraPoolsQueueHtml(st);
    setQueueHtml(body, html);
  }
  // 기본 풀 밖의 풀(M5b): 풀 헤더 + 같은 표. 워커가 없으면 그 대기 행은 서버가 worker_down 으로 준다
  function extraPoolsQueueHtml(st) {
    var pools = poolsOf(st).slice(1);
    var html = "";
    var tzName = st.display_timezone || "local";
    pools.forEach(function (p) {
      var head = poolHeader(p, L());
      if (p.queue === null || p.queue === undefined) {
        html += '<div class="pool-h">' + esc(head) + '</div><div class="banner bad" role="alert" data-error="queue">' + esc(tr("queue.unavailable", { error: errorText(p.queue_error, p.queue_error_code) })) + "</div>";
        return;
      }
      if (!p.queue.length) return;  // 잡 없는 풀은 자리를 차지하지 않는다
      html += '<div class="pool-h" data-pool="' + esc(p.name || "") + '">' + esc(tr("queue.pool_jobs", { head: head, n: p.queue.length })) + "</div>";
      html += '<div class="qwrap"><table class="q">' + queueHeadHtml(tzName) + "<tbody>";
      sortQueue(p.queue).forEach(function (row) { html += queueRowHtml(row, st); });
      html += "</tbody></table></div>";
    });
    return html;
  }
  function queueRowHtml(row, st) {
    var est = row.estimate || {};
    var busy = row.state === "running" || row.state === "cancelling";
    var expanded = busy && !!state.expanded[row.id];
    // 전체 진행 막대는 접힘과 무관하게 도는 행에 늘 붙는다 — 접기가 「어디까지 왔나」를 감추면
    // 접어 둘 수 없다. 단계 목록·로그 tail·액션만 ▸ 뒤에 있다.
    // 막대는 **진행 시간 칸 안**에 산다(별도의 막대 행이 아니다) — 한 작업이 한 줄이다.
    var bar = progressBarHtml(row, L(), canTick());
    var mine = isMine(row, state.me);
    var cls = [];
    if (mine) cls.push("mine");
    // 왼쪽 레인 하나가 행의 형편을 말한다. 우선순위 `stuck > overdue > quiet > running` —
    // 한 행에 채운 칩은 상태 필 하나뿐이고, 이상은 레인에서 한 번만 빨강으로 말한다.
    if (est.stuck) cls.push("stuck");
    else if (est.overdue) cls.push("overdue");
    else if (est.quiet) cls.push("quiet");
    else if (busy) cls.push("run");
    if (expanded) cls.push("exp");
    if (state.hl === row.id) cls.push("hl");
    if (row._dim) cls.push("dim");
    var pos = isNum(row.position) ? '<span class="pos">' + esc(tr("ordinal.in_line", { ordinal: ordinal(row.position, L()) })) + "</span>" : "";
    var pill;
    if (row.state === "uploading") {
      var src = row.source || {};
      var pct = isNum(src.received_bytes) && isNum(src.bytes) && src.bytes > 0 ? Math.min(100, Math.round(src.received_bytes / src.bytes * 100)) : 0;
      pill = '<span class="pill uploading"><span class="g" aria-hidden="true">↑</span> ' + esc(stateWord("uploading", L())) + ' <span class="ub"><i style="width:' + pct + '%"></i></span></span>';
    } else if (row.state === "cancelling") pill = '<span class="pill cancelling"><span class="g" aria-hidden="true">■</span> ' + esc(stateWord("cancelling", L())) + "</span>";
    else pill = '<span class="pill ' + esc(row.state) + '"><span class="g" aria-hidden="true">' + stateGlyph(row.state) + "</span> " + esc(stateWord(row.state, L())) + "</span>";
    var expBtn = busy ? '<button type="button" class="exp-btn" data-toggle="' + row.id + '" aria-expanded="' + (expanded ? "true" : "false") + '" aria-controls="exp-' + row.id + '" aria-label="' + esc(tr(expanded ? "row.collapse_job" : "row.expand_job", { id: row.id })) + '" title="' + esc(tr(expanded ? "row.collapse" : "row.expand")) + '">' + (expanded ? "▾" : "▸") + "</button>" : "";
    var chips = "";
    var inputs = row.inputs || {};
    Object.keys(inputs).forEach(function (k) { chips += '<button type="button" class="chip" data-inputs="' + row.id + '" title="' + esc(JSON.stringify(inputs)) + '">' + esc(k + "=" + inputs[k]) + "</button>"; });
    if (row.concurrency_group) chips += '<span class="chip">' + esc(tr("row.group", { name: row.concurrency_group })) + "</span>";
    chips += priorityChip(row, L());
    var req = row.requester || {};
    var joiners = Array.isArray(row.joiners) ? row.joiners : [];
    var requester = '<span title="' + esc(tr("row.token_of", { name: req.name || "" })) + '">' + esc(truncate(req.label || req.name || DASH, 40)) + "</span>"
      + (mine && req.name === state.me ? '<span class="you">' + esc(tr("row.you")) + "</span>" : "")
      + (joiners.length ? '<button type="button" class="joiners" title="' + esc(tr("row.also_waiting", { labels: joiners.map(function (j) { return j.label || j.name; }).join(", ") })) + '">+' + joiners.length + "</button>" : "");
    if (mine && req.name !== state.me) requester += '<span class="you">' + esc(tr("row.you_joined")) + "</span>";
    var r = reasonText(row, st, now(), L());
    // 이유 문구에는 서버가 준 문자열(ref · group · label)이 들어간다 — escape 한 뒤 잡 링크만 버튼으로 바꾼다
    var reasonHtml = esc(r.text);
    r.links.forEach(function (l) { var id = l.jobId; reasonHtml = reasonHtml.replace("#" + id, '<button type="button" class="jlink" data-goto="' + id + '">#' + id + "</button>"); });
    // 한 갈래다 — 형편은 class 하나로만 말한다(칩 세 갈래를 따로 조립하던 자리).
    var reasonCell = '<span class="reason' + (r.actionable || busy ? " act" : "") + (r.cls ? " " + r.cls : "")
      + '">' + (r.cls === "blocked" ? icon("chain") + " " : "") + reasonHtml + "</span>";
    // 펼치지 않아도 지금 무엇을 하는지 읽혀야 한다(§4.6-라). 스텝 초는 기준점으로 스스로 센다.
    var nowStep = busy && !expanded ? stepNowHtml(row.progress) : "";
    if (nowStep) reasonCell += '<div class="sub step-now">' + nowStep + "</div>";
    if (row.state === "uploading" && row.reason === "upload_stalled") reasonCell += '<div class="sub">' + esc(tr("row.stalled_note")) + "</div>";
    if (row._cancelRequested) reasonCell += '<div class="sub">' + esc(tr("row.cancel_requested")) + "</div>";
    // 접힌 행이면 내 잡을 여기서 바로 세울 수 있어야 한다 — 폰에서 유일한 취소 경로이고, 도는
    // 잡을 멈추는 일이 스텝 목록을 구경하는 일보다 급하다(사용자 검사 U3.6 · Codex 리뷰 4).
    // 서버가 취소에 제출 capability 를 요구하면(M5j G5) 페이지는 그것을 못 가진다 — admin 이 아니면
    // 버튼 대신 이유 한 줄. 내 잡에만(남의 잡은 오늘도 버튼이 없다).
    var locked = cancelLocked(st && st.server, state.me ? { name: state.me, admin: state.admin } : null);
    var canActRow = !!state.token && !state.tokenBad && (mine || state.me === null) && !locked;
    if (!expanded && canActRow && !row._cancelRequested && row.state !== "cancelling") reasonCell += '<div class="sub"><button type="button" class="btn danger cancel" data-cancel="' + row.id + '">' + esc(tr("row.cancel")) + "</button></div>";
    else if (!expanded && locked && mine && !!state.token && !state.tokenBad && row.state !== "cancelling") reasonCell += '<div class="sub cancel-locked">' + esc(tr("row.cancel_locked")) + "</div>";
    var el = elapsedText(row, now(), L());
    var elapsedCell = busy && isNum(est.elapsed_seconds)
      ? '<span data-tick="elapsed" data-from="' + esc(row.started_at || "") + '">' + esc(el.main) + "</span>" + (el.sub ? '<div class="sub">' + esc(el.sub) + "</div>" : "")
      : (row.state === "queued" ? '<span data-tick="waiting" data-from="' + esc(row.created_at || "") + '">' + esc(el.main) + "</span>" : esc(el.main));
    if (bar) elapsedCell += bar;
    var eta = etaText(row, tz(), now(), L());
    var conf = confidenceBadge(est, L());
    var etaCell = '<span class="eta">' + esc(eta.clock) + (eta.rel ? ' <span class="in">· ' + esc(eta.rel) + "</span>" : "") + '</span><br><span class="conf ' + esc(conf.cls) + '">' + esc(conf.text) + "</span>";
    if (est.overdue && !est.stuck) etaCell = '<span class="eta">' + DASH + '</span><br><span class="conf over">' + esc(tr("eta.overdue")) + "</span>";
    var source = sourceHtml(row, L(), false);
    var h = '<tr class="' + cls.join(" ") + '" data-job="' + row.id + '" id="job-' + row.id + '">' +
      '<td class="job">' + expBtn + '<span class="id">#' + row.id + "</span> " + pill + pos + "</td>" +
      '<td class="key"><span class="key">' + esc(row.key || row.preset || DASH) + "</span>" + chips + "</td>" +
      '<td class="requester">' + requester + "</td>" +
      '<td class="reason">' + reasonCell + "</td>" +
      '<td class="elapsed">' + elapsedCell + "</td>" +
      '<td class="eta">' + etaCell + "</td>" +
      '<td class="source">' + source + "</td></tr>";
    if (expanded) h += '<tr class="expanded" data-job="' + row.id + '"><td colspan="7" class="prog" id="exp-' + row.id + '">' + progressHtml(row) + '<div class="src-block sub">' + sourceHtml(row, L(), true) + "</div>" + tailHtml(row) + "</td></tr>";
    return h;
  }
  /**
   * 소스 칸. `full` 이 false 면 커밋 해시(와 uncommitted 표지)만 — 저장소 주소는 행마다 같은 값이
   * 되풀이돼 표를 옆으로 늘린다(§4.1). 펼친 블록이 `full` 로 한 번 보여 준다.
   * 기본은 true 라 이 함수를 직접 부르는 쪽(그리고 오늘의 단언)은 그대로다.
   */
  function sourceHtml(row, lang, full) {
    var s = row.source || {};
    var showAll = full !== false;
    if (s.mode === "git_ref") {
      var refLine = '<div class="sub">' + esc(s.repo || "") + " · " + esc(T(lang, "row.ref", { ref: s.ref || DASH })) + "</div>";
      return '<button type="button" class="sha" data-src="' + row.id + '">' + esc((s.sha || "").slice(0, 7) || DASH) + "</button>"
        + (showAll ? refLine : '<div class="sub">' + esc(T(lang, "row.ref", { ref: s.ref || DASH })) + "</div>");
    }
    if (row.state === "uploading" && !s.base_sha) return '<span class="sub">' + esc(T(lang, "row.not_received")) + "</span>";
    var sha = (s.base_sha || "").slice(0, 7);
    return '<button type="button" class="sha" data-src="' + row.id + '" title="' + esc(T(lang, "row.tree", { hash: s.tree_hash || DASH })) + '">' + esc(sha || DASH) + "</button>"
      + (s.dirty ? '<span class="uncommitted">' + esc(T(lang, "row.uncommitted")) + "</span>" : "")
      + (showAll ? '<div class="sub">' + esc(s.repo || "") + "</div>" : "");
  }
  /** 시계 차이를 아는가 — 모르면 기준점으로 세지 않는다(조용히 브라우저 시계로 넘어가지 않는다). */
  function canTick() { return !state.skewUnknown; }

  /** 도는 행 한 줄: 「스텝 2/4 analyze 12s」. 초는 1초 틱이 스스로 센다. */
  function stepNowHtml(prog) {
    if (!prog || prog.phase === "materializing" || !prog.current_name) return "";
    var cur = runningStep(prog);
    if (!cur) return "";
    var total = isNum(prog.steps_total) ? prog.steps_total : "?";
    var i = isNum(prog.current_index) ? prog.current_index : prog.steps_done;
    var dur = fmtDuration(prog.current_seconds);
    var secs = canTick() && cur.started_at
      ? '<span data-tick="elapsed" data-from="' + esc(cur.started_at) + '">' + esc(dur) + "</span>"
      : esc(dur);
    return esc(tr("progress.now", { cur: i, total: total, step: prog.current_name })) + " " + secs;
  }

  function progressHtml(row) {
    var prog = row.progress;
    if (!prog || prog.phase === "materializing") return "";
    var busyRow = row.state === "running" || row.state === "cancelling";
    var head = progressHeadHtml(prog, L(), busyRow && canTick());
    var steps = Array.isArray(prog.steps) ? prog.steps : [];
    var total = isNum(prog.steps_total) ? prog.steps_total : steps.length;
    var failed = failedStepCount(prog);
    var h = '<div class="head"><b>' + (head || "") + "</b>" + (failed ? ' <span class="fail">' + esc(tr("progress.steps_failed", { n: failed })) + "</span>" : "") + ' <span class="note" title="' + esc(tr("progress.timing_note")) + '">ⓘ</span></div>';
    if (steps.length) {
      var segs = "";
      var pending = Math.max(0, total - steps.length);
      // 칸 폭은 CSS 의 flex 가 똑같이 나눈다 — 퍼센트는 표 안에서 틀어진다(style.css `.pbar i` 주석)
      steps.forEach(function (s) { segs += '<i class="' + (s.state === "running" ? "run" : s.ok === false ? "fail" : "") + '"></i>'; });
      for (var i = 0; i < pending; i++) segs += '<i class="pend"></i>';
      // 스텝 띠는 아래 목록을 눈으로 요약할 뿐이다. 한 잡에 progressbar 가 둘이면 보조기기가
      // 같은 잡의 서로 다른 두 값을 읽는다 — 의미는 행 아래 전체 막대 하나가 진다.
      h += '<div class="minibar" aria-hidden="true">' + segs + "</div>";
      h += '<div class="steps">';
      var live = busyRow && canTick();
      steps.forEach(function (s) {
        var c = s.state === "running" ? "run" : s.ok === false ? "fail" : "";
        var tick = live && s.state === "running" && s.started_at ? ' data-tick="elapsed" data-from="' + esc(s.started_at) + '"' : "";
        h += '<div class="step ' + c + '"><span class="g" aria-hidden="true">' + stepMark(s) + "</span><span>" + esc(s.name) + '</span><span class="s"' + tick + ">" + esc(fmtDuration(s.seconds)) + "</span></div>";
      });
      for (var j = 0; j < pending; j++) h += '<div class="step pend"><span class="g" aria-hidden="true">·</span><span>…</span><span class="s">' + DASH + "</span></div>";
      h += "</div>";
    }
    // 스텝 안의 단위 격자(`::rcm::progress::`) — 스텝 목록 아래, 둘 이상일 때만
    h += unitsGridHtml(prog, L());
    return h;
  }
  function tailHtml(row) {
    var busy = row.state === "running" || row.state === "cancelling";
    var mine = isMine(row, state.me);
    var h = "";
    if (Array.isArray(row.log_tail) && row.log_tail.length) h += '<div class="tail">' + esc(row.log_tail.slice(-5).join("\n")) + "</div>";
    else if (!state.token) h += '<div class="sub" style="margin-top:8px">' + esc(tr("row.add_token_for_log")) + "</div>";
    var canAct = !!state.token && !state.tokenBad && (mine || state.me === null);
    // 취소만 잠긴다(M5j G5) — 로그는 그 잡의 토큰으로 오늘처럼 읽는다
    var locked = cancelLocked(state.status && state.status.server, state.me ? { name: state.me, admin: state.admin } : null);
    var joiners = Array.isArray(row.joiners) ? row.joiners.length : 0;
    var note = !state.token ? "" : (!mine ? '<span class="sub">' + esc(tr("row.not_your_job")) + "</span>"
      : (locked ? '<span class="sub cancel-locked">' + esc(tr("row.cancel_locked")) + "</span>"
      : (joiners ? '<span class="sub">' + esc(tr("row.others_waiting", { n: joiners })) + "</span>" : "")));
    h += '<div class="actions"><button type="button" class="btn log" data-log="' + row.id + '"' + (canAct ? "" : " disabled") + ">" + esc(tr("row.log")) + "</button>" +
      '<button type="button" class="btn danger cancel" data-cancel="' + row.id + '"' + (canAct && !locked && busy && row.state !== "cancelling" ? "" : " disabled") + ">" + esc(tr("row.cancel")) + "</button>" +
      note + "</div>";
    return h;
  }

  // ── 렌더: 호스트 (항목 16 · 19) ──
  function sparkline(history, key) {
    if (!Array.isArray(history) || history.length < 2) return "";
    var vals = history.map(function (h) { return isNum(h[key]) ? h[key] : null; });
    var max = Math.max.apply(null, vals.filter(isNum).concat([1]));
    var W = 90, H = 18, n = vals.length;
    var segs = [], cur = [];
    var gaps = [];
    for (var i = 0; i < n; i++) {
      var x = (i / (n - 1)) * W;
      if (isNum(vals[i])) { cur.push(x.toFixed(1) + "," + (H - 2 - (vals[i] / max) * (H - 4)).toFixed(1)); }
      else { if (cur.length) { segs.push(cur); cur = []; } gaps.push(i); }
    }
    if (cur.length) segs.push(cur);
    var svg = '<svg viewBox="0 0 90 18" aria-hidden="true">';
    for (var s = 0; s < segs.length; s++) {
      svg += '<polyline points="' + segs[s].join(" ") + '"></polyline>';
      if (s + 1 < segs.length) svg += '<polyline class="gap" points="' + segs[s][segs[s].length - 1] + " " + segs[s + 1][0] + '"></polyline>';
    }
    return svg + "</svg>";
  }
  /**
   * 접힌 호스트 절의 한 줄. 「세 질문」에 호스트는 없다(§4.1) — 평소엔 접어 둔다.
   *
   * `warn` 은 그 한 줄을 물들이고, `alert` 는 절을 **한 번** 펼친다. 둘이 다른 이유: 부하가
   * 높은 것은 빌드 머신이 **일하는 중**이라는 뜻이지 사람이 손댈 일이 아니다. 펼치는 것은
   * 사람이 봐야 하는 것 — 표본이 안 온다 · 호스트를 못 읽는다 · 디스크가 바닥이다 — 뿐이다.
   */
  function hostDigest() {
    var p = pool0(state.status);
    if (!p) return { text: tr("host.no_sample"), warn: false, alert: false };
    if (p.hosts === null || p.hosts === undefined) {
      return { text: tr("host.unavailable", { error: errorText(p.hosts_error, p.hosts_error_code) }), warn: true, alert: true };
    }
    var cards = hostCards(state.status, L());
    if (!cards.length) return { text: tr("host.no_sample"), warn: false, alert: false };
    var worst = null, stale = false, lowDisk = false;
    cards.forEach(function (c) {
      var hp = pressureOf(c.host);
      var age = secondsSince(c.host.sampled_at, now());
      if (c.host.stale || (isNum(age) && isNum(c.host.interval_seconds) && age > 3 * c.host.interval_seconds)) stale = true;
      if (isNum(hp.diskFree) && hp.diskFree < DISK_LOW_FREE) lowDisk = true;
      if (!worst || (hp.verdict === "busy" && worst.verdict !== "busy")) worst = hp;
    });
    var names = cards.map(function (c) { return c.host.name || DASH; }).join(" · ");
    var vkey = "summary.verdict_" + (worst.verdict === "fine" || worst.verdict === "busy" || worst.verdict === "partial" ? worst.verdict : "unknown");
    return {
      text: tr("host.digest", { names: names, n: cards.length, state: tr(vkey) }),
      warn: worst.verdict === "busy" || stale,
      alert: stale || lowDisk
    };
  }

  // 호스트 카드의 「rcm 데이터」 한 줄 (M5g §5.4) — 이 서버가 쥔 부피와 천장.
  // 경고는 셋 중 하나면 켠다: 예산 초과 · 바닥 아래 · 무진전 latch.
  // **모르는 값은 0 이 아니다** — 못 잰 회계를 0 GB 로 그리면 「지키고 있다」는 거짓말이 된다.
  function jobStorageLine(doc, now, lang) {
    if (!doc || !isNum(doc.volume_bytes) && !doc.error_code) return null;
    var limit = isNum(doc.limit_bytes) ? doc.limit_bytes : null;
    var free = isNum(doc.free_bytes) ? doc.free_bytes : null;
    var floor = isNum(doc.min_free_bytes) ? doc.min_free_bytes : null;
    var warn = Boolean(
      doc.no_progress ||
      doc.budget_unreachable ||
      (limit !== null && isNum(doc.volume_bytes) && doc.volume_bytes > limit) ||
      (floor !== null && free !== null && free < floor)
    );
    if (!isNum(doc.volume_bytes)) return { text: T(lang, "host.job_storage_unknown"), warn: true };
    var left = null;
    if (doc.next_sweep_at && now) {
      var secs = (Date.parse(doc.next_sweep_at) - Date.parse(now)) / 1000;
      if (isNum(secs)) left = fmtDuration(Math.max(0, secs));
    }
    // 회계의 나이(I6) — measured_at 은 합계에 기여한 측정 중 가장 오래된 것이라 「방금 쟀다」가 아니다.
    var measured = "";
    if (doc.measured_at && now) {
      var age = (Date.parse(now) - Date.parse(doc.measured_at)) / 1000;
      if (isNum(age)) measured = fmtAgo(Math.max(0, age), lang);
    }
    return {
      text: T(lang, "host.job_storage", {
        used: fmtDisk(doc.volume_bytes),
        limit: limit === null ? "" : fmtDisk(limit),
        next: left || "",
        measured: measured
      }),
      warn: warn
    };
  }

  function renderHostDigest() {
    var d = hostDigest();
    var el = $("[data-host-digest]");
    if (el) { el.textContent = d.text; el.className = "n" + (d.warn ? " warn" : ""); }
    var det = $("#host-details");
    if (!det) return;
    // 사람이 직접 여닫은 적이 있으면 그 선택이 이긴다. 없으면 `alert` 일 때 **한 번** 펼치고,
    // **저절로 닫지는 않는다**(`|| det.open`) — 화면은 읽는 사람 발밑을 빼지 않는다. 닫는 것은
    // 사람의 일이다. 판정으로 열림 상태를 매 렌더 다시 쓰면 85 를 스칠 때마다 절이 여닫히며
    // 그 아래가 통째로 뛴다(2026-09-14 실측: 2분에 3번 · 315px · CLS 0.245).
    var choice = lsGet("rcm.host");
    var want = choice === "open" ? true : choice === "closed" ? false : (d.alert || det.open);
    if (det.open !== want) { det.dataset.byRender = "1"; det.open = want; }
  }

  function renderHost() {
    var p = pool0(state.status);
    var body = $("[data-host-body]");
    renderHostDigest();
    if (!p) { body.innerHTML = '<div class="empty">host: no sample yet</div>'; return; }
    if (p.hosts === null || p.hosts === undefined) { body.innerHTML = '<div class="banner bad" role="alert" data-error="hosts">Host unavailable — ' + esc(p.hosts_error || "unknown error") + "</div>"; return; }
    var cards = hostCards(state.status, L());
    if (!cards.length) { body.innerHTML = '<div class="empty">' + esc(tr("host.no_sample")) + "</div>"; return; }
    body.innerHTML = cards.map(function (c) { return hostCardHtml(c.host, c.title); }).join("");
  }

  /** GPU 표본이 없는 이유(결정 37). 코드가 있으면 그 언어로, 없으면 서버가 준 문구. */
  function gpuNote(h) {
    var code = h && h.gpu_note_code;
    if (code && I18N.has("gpu." + code)) return tr("gpu." + code);
    return (h && h.gpu_note) || DASH;
  }

  function hostCardHtml(h, title) {
    var age = secondsSince(h.sampled_at, now());
    var stale = h.stale || (isNum(age) && isNum(h.interval_seconds) && age > 3 * h.interval_seconds);
    var cpu = h.cpu || {}, mem = h.memory || {}, gpu = h.gpu;
    var memPct = isNum(mem.used_bytes) && isNum(mem.total_bytes) && mem.total_bytes > 0 ? mem.used_bytes / mem.total_bytes * 100 : null;
    var compPct = isNum(mem.compressed_bytes) && isNum(mem.total_bytes) && mem.total_bytes > 0 ? mem.compressed_bytes / mem.total_bytes * 100 : 0;
    var meter = function (metric, label, right, pct, pct2, warn, spark) {
      var known = isNum(pct);
      return '<div class="meter' + (warn ? " warn" : "") + (stale ? " stale" : "") + '" data-metric="' + metric + '"><div class="lab"><span>' + esc(label) + "</span><span>" + esc(right) + "</span></div>" +
        '<meter min="0" max="100" value="' + (known ? Math.round(pct) : 0) + '" aria-label="' + esc(label) + '"></meter>' +
        '<div class="bar"><i style="width:' + (known ? Math.max(0, Math.min(100, pct - (pct2 || 0))) : 0) + '%"></i>' + (pct2 ? '<i class="b" style="width:' + Math.min(100, pct2) + '%"></i>' : "") + "</div>" +
        (spark ? '<div class="spark">' + spark + "<span>" + esc(tr("host.window")) + "</span></div>" : "") + "</div>";
    };
    var html = '<div class="hostcard' + (h.disk ? " m4" : "") + (stale ? " dim" : "") + '"><div class="hn">' + esc(title || h.name || DASH) + '<span class="age">' + (stale ? '<span class="stale-badge">' + esc(tr("host.stale", { dur: fmtDuration(age) })) + "</span> · " : '<span data-tick="age" data-from="' + esc(h.sampled_at || "") + '">' + esc(tr("host.sampled", { age: fmtAgo(age, L()) })) + "</span> · ") + esc(h.os || DASH) + " · " + esc(tr("host.cores_load", { cores: isNum(h.cores) ? h.cores : DASH, load: Array.isArray(h.load) && isNum(h.load[0]) ? h.load[0].toFixed(1) : DASH })) + "</span></div>";
    html += meter("cpu", tr("host.cpu", { pct: fmtPct(cpu.busy) }), isNum(cpu.user) && isNum(cpu.sys) ? tr("host.cpu_detail", { user: Math.round(cpu.user), sys: Math.round(cpu.sys) }) : (stale ? tr("host.last_known") : DASH), cpu.busy, isNum(cpu.sys) ? cpu.sys : 0, isNum(cpu.busy) && cpu.busy >= 85, sparkline(h.history, "cpu_busy"));
    html += meter("mem", tr("host.memory", { used: fmtMemory(mem.used_bytes), total: fmtMemory(mem.total_bytes) }), (isNum(memPct) ? fmtPct(memPct) : DASH) + (isNum(mem.compressed_bytes) ? " · " + tr("host.compressed", { size: fmtMemory(mem.compressed_bytes) }) : ""), memPct, compPct, isNum(memPct) && memPct >= 85, sparkline(h.history, "mem_used_bytes"));
    var disk = h.disk || null;
    if (disk) {
      var diskPct = isNum(disk.used_bytes) && isNum(disk.total_bytes) && disk.total_bytes > 0 ? disk.used_bytes / disk.total_bytes * 100 : null;
      var lowFree = isNum(disk.free_bytes) && disk.free_bytes < DISK_LOW_FREE;
      // 남은 양은 오른쪽에 글자로 — 막대만으로는 「얼마 남았나」를 못 읽는다. 경로는 그리지 않는다.
      var right = (isNum(diskPct) ? fmtPct(diskPct) : DASH) + (isNum(disk.free_bytes) ? " · " + tr("host.disk_free", { free: fmtDisk(disk.free_bytes) }) : "");
      html += meter("disk", tr("host.disk", { used: fmtDisk(disk.used_bytes), total: fmtDisk(disk.total_bytes) }), right, diskPct, 0, lowFree || (isNum(diskPct) && diskPct >= 85), "");
      // 데이터 디렉터리가 쥔 부피는 서버의 사실이라 **서버 자신의 카드에만** 그린다 — 로컬 표본은
      // `source: "local"`, 원격 워커의 표본은 `source: "worker"` 다.
      var js = h.source === "local" ? jobStorageLine((state.status && state.status.server || {}).job_storage, state.status && state.status.generated_at, L()) : null;
      if (js) html += '<div class="substat' + (js.warn ? " warn" : "") + '">' + esc(js.text) + "</div>";
    }
    if (gpu) html += meter("gpu", tr("host.gpu", { pct: fmtPct(gpu.util_pct) }), isNum(gpu.mem_used_bytes) ? tr("host.gpu_used", { size: fmtMemory(gpu.mem_used_bytes) }) : DASH, gpu.util_pct, 0, isNum(gpu.util_pct) && gpu.util_pct >= 85, sparkline(h.history, "gpu_util_pct"));
    else html += '<div class="meter" data-metric="gpu"><div class="lab"><span>' + esc(tr("host.gpu_none", { note: gpuNote(h) })) + "</span><span></span></div></div>";
    if (Array.isArray(h.top) && h.top.length) html += '<div class="top">' + esc(tr("host.top")) + h.top.map(function (t) { return "<b>" + esc(t.comm || DASH) + "</b> " + fmtPct(t.cpu) + " " + fmtMb(t.rss_mb); }).join(" · ") + "</div>";
    return html + "</div>";
  }

  // ── 렌더: 최근 (항목 14 · 15 · 32) ──
  function renderRecent() {
    var p = pool0(state.status);
    var body = $("[data-recent-body]");
    var head = $("[data-recent-header]");
    if (!p) { body.innerHTML = '<div class="empty">' + esc(tr("recent.none")) + "</div>"; head.textContent = ""; renderEstimates(p); return; }
    // 모든 풀의 완료 잡을 모은 뒤에 「없음」을 판단한다 — 기본 풀이 비어도 다른 풀의 완료 잡은 보여야 한다
    var all = recentOf(state.status);
    if (all === undefined) {
      var bad = poolsOf(state.status).filter(function (pl) { return !Array.isArray(pl.recent); })[0] || p;
      body.innerHTML = '<div class="banner bad" role="alert" data-error="recent">' + esc(tr("recent.unavailable", { error: errorText(bad.recent_error, bad.recent_error_code) })) + "</div>"; head.textContent = ""; renderEstimates(p); return;
    }
    if (!all.length) { body.innerHTML = '<div class="empty">' + esc(tr("recent.none")) + "</div>"; head.textContent = ""; renderEstimates(p); return; }
    var shown = state.showAllRecent ? all : all.slice(0, 5);
    head.textContent = tr("recent.count", { shown: shown.length, total: all.length });
    var html = '<div class="recent">';
    shown.forEach(function (job) {
      var l = recentLine(job, tz(), now(), L());
      var open = !!state.expandedRecent[job.id];
      var failedish = job.state === "failed" || job.state === "timed_out";
      // 결과마다 왼쪽 3px 레인 — 큐 표와 같은 채널이다(성공 · 실패 · 취소 · 결과를 잃음)
      html += '<div class="rrow' + (l.cls ? " " + esc(l.cls) : "") + (failedish ? " clickable" : "") + (state.hl === job.id ? " hl" : "") + '" data-job="' + job.id + '"' + (failedish ? ' data-rtoggle="' + job.id + '" role="button" tabindex="0" aria-expanded="' + (open ? "true" : "false") + '"' : "") + ">" +
        '<span class="id">#' + job.id + "</span>" +
        '<span class="pill ' + esc(l.cls) + '"><span class="g" aria-hidden="true">' + esc(l.glyph) + "</span> " + esc(l.pill) + "</span>" +
        '<span class="k">' + esc(job.key || DASH) + (job._pool ? ' <span class="chip">' + esc(tr("pool.name", { name: job._pool })) + "</span>" : "") + "</span>" +
        '<span class="s">' + esc(truncate((job.requester || {}).label || DASH, 40)) + "</span>" +
        '<span class="d">' + esc(l.duration) + "</span>" +
        '<span class="t">' + esc(l.when) + "</span>" +
        '<span class="s">' + (l.summary ? "<b>" + esc(l.summary.split(" · ")[0]) + "</b>" + esc(l.summary.indexOf(" · ") > 0 ? l.summary.slice(l.summary.indexOf(" · ")) : "") : "") +
        (l.rerun ? ' · <button type="button" class="rerun" data-copy="' + esc(l.rerun) + '" title="' + esc(tr("row.copy")) + '">⧉ ' + esc(l.rerun) + "</button>" : "") + "</span>";
      var art = artifactsLine(job, L(), now());
      if (art) html += '<div class="art ' + esc(art.cls) + '">' + esc(art.text) +
        (art.command ? ' <button type="button" class="rerun" data-copy="' + esc(art.command) + '" title="' + esc(tr("art.copy")) + '">⧉ ' + esc(art.command) + "</button>" : "") + "</div>";
      if (open) {
        var detail = [esc(transitionsLine(job, tz(), L()))];
        var extra = state.recentFailures[job.id];
        var full = extra && extra.failures ? Object.assign({}, job, extra) : job;
        recentDetail(full, L()).forEach(function (line) { detail.push(esc(line)); });
        if (outcomeText(job, L())) detail.push(esc(outcomeText(job, L())));
        html += '<div class="rdetail">' + detail.join("<br>") + "</div>";
      }
      html += "</div>";
    });
    html += "</div>";
    if (all.length > 5) {
      html += '<button type="button" class="more" data-more-recent>'
        + esc(state.showAllRecent ? tr("recent.show_fewer") : tr("recent.show_more", { n: all.length - 5 }))
        + " " + icon(state.showAllRecent ? "chevronUp" : "chevronDown") + "</button>";
    }
    body.innerHTML = html;  // 원격 풀의 host 는 Host 절 카드로(M5b-4) — 여기엔 풀 헤더를 두지 않는다
    renderEstimates(p);
  }
  function renderEstimates(p) {
    var sum = $("[data-est-summary]"), body = $("[data-est-body]"), det = $("#estimates");
    if (!p || p.medians === null || p.medians === undefined) {
      sum.textContent = tr("est.unavailable", { error: tr("est.medians_unavailable") });
      body.innerHTML = '<span class="bad">' + esc(p && p.medians_error ? errorText(p.medians_error, p.medians_error_code) : tr("est.medians_unavailable")) + "</span>";
      return;
    }
    var keys = Object.keys(p.medians);
    var presets = {};
    (state.status.presets || []).forEach(function (x) { presets[x.name] = x; });
    if (!keys.length) {
      sum.textContent = tr("est.no_samples");
      det.open = true;
      body.innerHTML = (state.status.presets || []).map(function (x) {
        var src = isNum(x.expected_seconds) ? tr("est.preset", { dur: fmtDuration(x.expected_seconds) }) : tr("est.default");
        return "<span><b>" + esc(x.name) + "</b> " + esc(src) + " · " + esc(tr("conf.low")) + "</span>";
      }).join("") || "<span>" + esc(tr("est.no_presets")) + "</span>";
      return;
    }
    sum.textContent = tr("est.keys", { n: keys.length });
    body.innerHTML = keys.map(function (k) {
      var m = p.medians[k] || {};
      var n = isNum(m.sample_count) ? m.sample_count : 0;
      var conf = tr("conf." + (n >= 5 ? "high" : n >= 2 ? "med" : "low"));
      var main = n >= 2 ? fmtDuration(m.seconds) : tr("est.to_preset", { n: n });
      var tail = tr("est.row", { wait: isNum(m.wait_seconds) ? fmtDuration(m.wait_seconds) : DASH, n: n, conf: conf });
      return "<span><b>" + esc(k) + "</b> " + esc(main) + " · " + esc(tail) + "</span>";
    }).join("");
    body.title = tr("est.how");
  }

  // ── 1초 틱 ──
  function tickTexts() {
    var n = now();
    $$("[data-tick]").forEach(function (el) {
      var kind = el.getAttribute("data-tick"), from = parseIso(el.getAttribute("data-from"));
      if (from == null) return;
      var s = (n - from) / 1000;
      if (kind === "elapsed") el.textContent = fmtDuration(s);
      else if (kind === "progress") tickProgress(el, s);
      // 1초마다 다시 쓰는 자리 — 렌더 시점이 아니라 **지금** 언어를 읽는다
      else if (kind === "waiting") el.textContent = tr("elapsed.waiting", { dur: fmtDuration(s) });
      else if (kind === "age") el.textContent = tr("host.sampled", { age: fmtAgo(s, L()) });
      else if (kind === "updated") el.textContent = tr("store.updated", { age: fmtAgo(s, L()) });
      else if (kind === "saved") el.textContent = tr("version.edit.save.saved", { age: fmtCoarse(s) });
    });
    renderHeaderConn();
  }

  /**
   * 시간 눈금 막대를 1초마다 민다 — 서버가 준 숫자만 그리면 폴링 간격마다 툭툭 튄다.
   * 경계·상한은 `overallProgress` 와 **같은 규칙**이다(99% 상한, `>=` 면 초과). 추정을 넘기는
   * 순간 그 자리에서 「예상 시간 초과」로 바꾼다(다음 조회가 서버 값으로 덮는다).
   * 갱신이 멈춘 동안(사람이 정지 · 연결 끊김)에는 예측도 멈춘다 — 안 받고 있는 데이터로 새 숫자를
   * 만들어 내면 「마지막으로 안 상태」가 아니라 지어낸 상태가 된다(Codex 리뷰 9).
   */
  function tickProgress(wrap, seconds) {
    if (state.conn.mode === "paused" || state.conn.mode === "lost") return;
    var expected = parseFloat(wrap.getAttribute("data-expected"));
    var bar = wrap.querySelector(".pbar"), fill = bar && bar.querySelector("i"), lab = wrap.querySelector(".plab");
    if (!isNum(expected) || expected <= 0 || !bar || !fill || !lab) return;
    var over = seconds >= expected;
    // 「조용함」은 1초 틱이 지울 사실이 아니다 — 지금 그려진 형편에서 읽어 그대로 들고 간다.
    var quiet = bar.getAttribute("data-cond") === "quiet";
    var pct = over ? null : timePct(seconds, expected);
    // 첫 렌더와 같은 갈래다: 화면 글자는 눈금과 근거만, 형편은 막대와 `aria-valuetext`·`title` 이.
    var head = over ? null : tr(timeKey(wrap.getAttribute("data-source")), { percent: pct });
    var spoken = over ? tr("pbar.over") : head + (quiet ? " · " + tr("pbar.quiet") : "");
    fill.style.width = (over ? 100 : pct) + "%";
    bar.setAttribute("data-basis", over ? "none" : "time");
    bar.setAttribute("data-cond", over ? "over" : (quiet ? "quiet" : "normal"));
    bar.setAttribute("aria-valuetext", spoken);
    if (over) bar.removeAttribute("aria-valuenow"); else bar.setAttribute("aria-valuenow", String(pct));
    lab.textContent = head || tr("pbar.none");
    lab.title = spoken;
  }

  // innerHTML 교체 전후로 포커스를 지킨다(Codex M2 리뷰 2): 같은 data 속성·id 를 가진 요소로 되돌린다
  function focusKey(el) {
    if (!el || el === document.body) return null;
    if (el.id) return "#" + el.id;
    var attrs = ["data-goto", "data-toggle", "data-log", "data-cancel", "data-rtoggle", "data-copy", "data-inputs", "data-src", "data-more-queue", "data-more-recent"];
    for (var i = 0; i < attrs.length; i++) if (el.hasAttribute(attrs[i])) return "[" + attrs[i] + '="' + el.getAttribute(attrs[i]).replace(/"/g, '\\"') + '"]';
    return null;
  }
  function withFocus(fn) {
    var was = document.activeElement;
    var key = focusKey(was);
    // 글을 치던 칸이면 커서 자리도 지킨다 — 버전 페이지는 5초마다 다시 그려지는데 그때마다
    // 커서가 끝으로 튀면 문안을 고칠 수가 없다(워크플랜 §3.2 W3).
    var sel = null;
    try { if (key && was && typeof was.selectionStart === "number") sel = [was.selectionStart, was.selectionEnd]; } catch (e0) { sel = null; }
    fn();
    if (!key) return;
    var el = null;
    try { el = $(key); } catch (e) { el = null; }
    if (!el) return;
    if (el !== document.activeElement) { try { el.focus({ preventScroll: true }); } catch (e2) { el.focus(); } }
    if (sel) { try { el.setSelectionRange(sel[0], sel[1]); } catch (e3) { /* 선택을 못 받는 칸 */ } }
  }
  function render() {
    if (!state.status) { renderHeaderConn(); return; }
    withFocus(function () { renderHeader(); renderSummary(); renderQueue(); renderHost(); renderRecent(); });
  }

  // ── 상호작용 ──
  function gotoJob(id) {
    state.hl = id;
    // 「그 잡을 보러 간다」는 뜻이다 — 도는 행이면 펴서 보여 준다(목업 4절 딥링크 규칙)
    var q = findRow(id);
    if (q && (q.state === "running" || q.state === "cancelling") && !state.expanded[id]) {
      state.expanded[id] = true; saveExpanded();
    }
    var row = $('tr[data-job="' + id + '"]') || $('.rrow[data-job="' + id + '"]');
    if (row) {
      renderQueue(); renderRecent();
      var el = $('[data-job="' + id + '"]');
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(function () { if (state.hl === id) { state.hl = null; renderQueue(); renderRecent(); } }, 2500);
      return;
    }
    api("/jobs/" + id).then(function (r) { return r.ok ? r.json() : null; }).then(function (job) {
      if (!job) toast(tr("toast.not_found", { id: id }));
      else toast(job.finished_at
        ? tr("toast.finished", { id: id, state: stateWord(job.state, L()), clock: fmtClock(job.finished_at, tz(), now()) })
        : "#" + id + " " + stateWord(job.state, L()));
    }).catch(function () { toast(tr("toast.lookup_failed", { id: id })); });
  }
  /* 펼친 최근 행의 이름별 이력. `/api/status` 에는 없고(결정 67) 이 잡의 문서에만 있다 —
     펼칠 때 한 번만 받고 캐시한다. 실패하면 **아무것도 안 그린다**(없는 것과 못 받은 것을
     같게 보이지 않으려면 빈 배열을 지어내지 않는 쪽이 맞다). */
  function loadRecentFailures(id) {
    if (state.recentFailures[id] !== undefined) return;
    state.recentFailures[id] = null;  // 재요청 방지
    api("/jobs/" + id).then(function (r) { return r.ok ? r.json() : null; }).then(function (job) {
      if (!job || !Array.isArray(job.failures)) return;
      state.recentFailures[id] = { failures: job.failures, failures_truncated: !!job.failures_truncated };
      renderRecent();
    }).catch(function () { /* 조용히 — 상세의 나머지는 그대로 보인다 */ });
  }

  function restoreTrigger() {
    var t = state.lastTrigger; state.lastTrigger = null;
    if (t && document.contains(t)) { try { t.focus({ preventScroll: true }); } catch (e) { t.focus(); } }
  }
  function toast(text) {
    var t = $("#toast");
    t.textContent = text; t.hidden = false;
    clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.hidden = true; }, 4000);
  }
  function findRow(id) { var q = queueOf(state.status) || []; for (var i = 0; i < q.length; i++) if (q[i].id === id) return q[i]; return null; }

  // 로그 서랍 (항목 13)
  function openDrawer(id) {
    var d = $("#drawer");
    var row = findRow(id);
    state.drawer.jobId = id; state.drawer.offset = 0; state.drawer.lines = 0;
    state.drawer.key = row ? (row.key || "") : "";
    $("[data-drawer-title]").textContent = tr("drawer.job", { id: id, key: state.drawer.key });
    $("[data-drawer-sub]").textContent = "";
    $("[data-drawer-log]").textContent = "";
    d.hidden = false;
    if (location.hash !== "#/jobs/" + id + "/log") history.pushState(null, "", "#/jobs/" + id + "/log");
    pollLog();
    $("[data-drawer-close]").focus();
  }
  function closeDrawer(fromHash) {
    var d = $("#drawer");
    if (d.hidden) return;
    d.hidden = true; clearTimeout(state.drawer.timer); state.drawer.timer = null; state.drawer.jobId = null;
    if (!fromHash && /\/log$/.test(location.hash)) history.pushState(null, "", "#");
    restoreTrigger();
  }
  function pollLog() {
    var id = state.drawer.jobId;
    if (id == null) return;
    api("/jobs/" + id + "/log?offset=" + state.drawer.offset).then(function (r) {
      if (r.status === 401 || r.status === 403) { $("[data-drawer-sub]").textContent = r.status === 401 ? tr("row.add_token_for_log") : tr("row.not_your_job"); return null; }
      if (!r.ok) throw new Error("http " + r.status);
      var more = r.headers.get("X-RCM-More") === "1";
      var next = parseInt(r.headers.get("X-RCM-Next-Offset") || "0", 10);
      return r.text().then(function (text) { return { text: text, more: more, next: next }; });
    }).then(function (res) {
      if (!res) return;
      var pre = $("[data-drawer-log]");
      if (res.text) {
        var atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
        var frag = document.createDocumentFragment();
        res.text.split("\n").forEach(function (line, i, arr) {
          if (i === arr.length - 1 && line === "") return;
          var span = document.createElement("span");
          if (/^::rcm::step-end::fail/.test(line)) span.className = "mark";
          span.textContent = line + "\n";
          frag.appendChild(span);
        });
        pre.appendChild(frag);
        var mark = pre.querySelector(".mark");
        if (mark && state.drawer.lines === 0) mark.scrollIntoView({ block: "center" });
        else if (atBottom) pre.scrollTop = pre.scrollHeight;
        state.drawer.lines += 1;
      }
      state.drawer.offset = res.next;
      $("[data-drawer-sub]").textContent = res.more ? tr("drawer.live", { bytes: fmtBytes(res.next) }) : tr("drawer.finished", { bytes: fmtBytes(res.next) });
      if (res.more) state.drawer.timer = setTimeout(pollLog, 2000);
    }).catch(function () { $("[data-drawer-sub]").textContent = tr("drawer.load_failed"); state.drawer.timer = setTimeout(pollLog, 5000); });
  }

  // 취소 (항목 13 · 30)
  function openCancel(id) {
    var row = findRow(id);
    if (!row) return;
    state.cancelTarget = id;
    var req = row.requester || {};
    var joiners = Array.isArray(row.joiners) ? row.joiners.length : 0;
    var isJoiner = state.me && req.name !== state.me;
    $("[data-cancel-title]").textContent = tr("cancel.confirm_title", { id: id, key: row.key || "", who: req.label || req.name || DASH });
    var body;
    var others = joiners ? " " + tr("cancel.others_waiting", { n: joiners }) : "";
    if (isJoiner) body = tr("cancel.joiner_body", { who: req.label || req.name || DASH });
    else if (row.state === "running") body = tr("cancel.running_body") + others + " " + tr("cancel.cannot_undo");
    else body = tr("cancel.queued_body") + others;
    $("[data-cancel-body]").textContent = body;
    $("[data-cancel-go]").textContent = isJoiner ? tr("cancel.leave") : tr("cancel.go");
    var dlg = $("#cancel-dialog");
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }
  function doCancel() {
    var id = state.cancelTarget;
    if (id == null) return;
    var row = findRow(id);
    if (row) { row._cancelRequested = true; row._dim = true; renderQueue(); }
    api("/jobs/" + id + "/cancel", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    }).then(function (res) {
      if (!res.ok) { toast(tr("toast.cancel_failed", { detail: (res.body && res.body.error) || res.status })); if (row) { row._cancelRequested = false; row._dim = false; } if (res.status === 401 || res.status === 403) tokenRejected(); fetchStatus(); return; }
      if (res.body && res.body.left) toast(tr("toast.left_join", { id: id }));
      else toast("#" + id + " " + (res.body && res.body.state ? stateWord(res.body.state, L()) : tr("toast.cancel_requested")));
      setTimeout(function () { fetchStatus(); }, 5000);
      scheduleRefetch();
    }).catch(function () { toast(tr("toast.cancel_network")); if (row) { row._cancelRequested = false; row._dim = false; renderQueue(); } });
  }

  /** 서버가 준 오류 — 코드가 있으면 그 언어로, 없으면 원문 그대로(결정 37). */
  function errorText(text, code) {
    if (code && I18N.has("error." + code)) return tr("error." + code);
    return text || tr("error.internal_error");
  }

  function wireHostDetails() {
    var det = $("#host-details");
    if (!det) return;
    det.addEventListener("toggle", function () {
      // 렌더가 다시 쓰는 값이 아니라 **사람의 선택**만 저장한다
      if (det.dataset.byRender === "1") { det.dataset.byRender = ""; return; }
      lsSet("rcm.host", det.open ? "open" : "closed");
    });
  }

  function wireLang() {
    var btn = $("#lang-btn");
    if (btn) btn.addEventListener("click", function () { setLang(state.lang === "ko" ? "en" : "ko"); });
  }

  function wireClicks() {
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-goto],[data-toggle],[data-log],[data-cancel],[data-more-queue],[data-more-recent],[data-rtoggle],[data-copy],[data-inputs],[data-src]");
      if (!t) return;
      if (t.hasAttribute("data-goto")) { gotoJob(parseInt(t.getAttribute("data-goto"), 10)); return; }
      if (t.hasAttribute("data-toggle")) {
        var id = parseInt(t.getAttribute("data-toggle"), 10);
        if (state.expanded[id]) delete state.expanded[id]; else state.expanded[id] = true;
        saveExpanded(); withFocus(renderQueue); return;
      }
      if (t.hasAttribute("data-log")) { state.lastTrigger = t; openDrawer(parseInt(t.getAttribute("data-log"), 10)); return; }
      if (t.hasAttribute("data-cancel")) { state.lastTrigger = t; openCancel(parseInt(t.getAttribute("data-cancel"), 10)); return; }
      if (t.hasAttribute("data-more-queue")) { state.showAllQueue = true; renderQueue(); return; }
      if (t.hasAttribute("data-more-recent")) { state.showAllRecent = !state.showAllRecent; renderRecent(); return; }
      if (t.hasAttribute("data-rtoggle")) { var rid = parseInt(t.getAttribute("data-rtoggle"), 10); if (ev.target.closest("[data-copy]")) return; state.expandedRecent[rid] = !state.expandedRecent[rid]; if (state.expandedRecent[rid]) loadRecentFailures(rid); renderRecent(); return; }
      if (t.hasAttribute("data-copy")) { var text = t.getAttribute("data-copy"); if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { toast(tr("toast.copied", { text: text })); }, function () { toast(text); }); else toast(text); return; }
      if (t.hasAttribute("data-inputs")) { var r = findRow(parseInt(t.getAttribute("data-inputs"), 10)); if (r) toast(tr("toast.inputs", { id: r.id, json: JSON.stringify(r.inputs || {}) })); return; }
      if (t.hasAttribute("data-src")) { var rs = findRow(parseInt(t.getAttribute("data-src"), 10)); if (rs && rs.source) toast("#" + rs.id + " " + (rs.source.mode === "git_ref" ? (rs.source.sha || DASH) + " · ref " + (rs.source.ref || DASH) : (rs.source.base_sha || DASH) + (rs.source.dirty ? " · tree differs from base sha" : "") + (rs.source.tree_hash ? " · tree " + rs.source.tree_hash : ""))); return; }
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { closeDrawer(false); }
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.hasAttribute && ev.target.hasAttribute("data-rtoggle")) { ev.preventDefault(); ev.target.click(); }
    });
    $("#live-btn").addEventListener("click", function () { if (state.conn.mode === "paused") resumeUpdates("manual_resume"); else pauseUpdates("manual_pause"); });
    $("[data-drawer-close]").addEventListener("click", function () { closeDrawer(false); });
    $("[data-cancel-keep]").addEventListener("click", function () { $("#cancel-dialog").close(); });
    $("#cancel-dialog").addEventListener("close", restoreTrigger);
    $("#tok-dialog").addEventListener("close", function () { var b = $("#tok-btn"); if (b) b.focus(); });
    $("#cancel-dialog form").addEventListener("submit", function (ev) { ev.preventDefault(); $("#cancel-dialog").close(); doCancel(); });
    window.addEventListener("hashchange", applyHash);
    window.addEventListener("popstate", applyHash);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { state.hiddenSince = Date.now(); setTimeout(function () { if (document.hidden && state.hiddenSince && Date.now() - state.hiddenSince >= HIDDEN_PAUSE_MS && state.conn.mode !== "paused") pauseUpdates("hidden_60s"); }, HIDDEN_PAUSE_MS + 50); }
      else { state.hiddenSince = null; if (state.conn.mode === "paused" && state.conn.before) resumeUpdates("visible"); }
    });
  }
  // ═══════════════════════════════════════════════════════════════════════════
  // 스토어 탭 — DOM (docs/wireframes/web-store.html). 큐의 SSE·폴링은 이 화면에서도 그대로 산다:
  // 큐 절은 `hidden` 일 뿐 `render()` 는 계속 그린다. 스토어 자료는 SSE 가 없어 들어올 때 받고,
  // 행동(넣기·검증·가져오기) 뒤에 다시 받고, 화면이 보이는 동안 30초마다 받는다.
  // 비밀 값은 대화상자 입력칸에만 잠깐 있고, 보내면 비운다 — localStorage 에도 state 에도 없다.
  // ═══════════════════════════════════════════════════════════════════════════
  var STORE_REFRESH_MS = 30000;
  var STORE_ROWS_KEY = "rcm.store.rows";  // 사람이 여닫은 행 — `<repo>/<row>` → open|closed (큐 화면 규칙)
  // 시험은 `window.rcmStoreApi` 로 서버 호출을 통째로 바꿔 끼운다(tests/test_web_browser.py) —
  // 그것 말고는 진짜 fetch 다. 게으르게 고르는 이유: 시험의 스텁은 페이지 스크립트보다 먼저 심긴다.
  function storeApi() {
    if (root && root.rcmStoreApi) return root.rcmStoreApi;
    if (!state.store.api) state.store.api = makeStoreApi(function (p, o) { return fetch(p, o); }, function () { return state.token; });
    return state.store.api;
  }
  function storeErrorDetail(res) {
    var b = res && res.body;
    if (b && typeof b === "object" && b.error) return errorText(String(b.error), b.code);
    return "http " + (res ? res.status : "?");
  }
  function loadRowMemory() { try { return JSON.parse(lsGet(STORE_ROWS_KEY) || "{}") || {}; } catch (e) { return {}; } }
  function rememberRow(row, open) {
    var mem = loadRowMemory();
    mem[state.store.repo + "/" + row] = open ? "open" : "closed";
    lsSet(STORE_ROWS_KEY, JSON.stringify(mem));
  }

  // ── 화면 전환 (항목 1) ──
  function showView(view) {
    state.view = view;
    ["#summary", "#queue", "#host", "#recent"].forEach(function (sel) { var el = $(sel); if (el) el.hidden = view !== "queue"; });
    var st = $("#store");
    if (st) st.hidden = view !== "store";
    renderNav();
    if (view !== "store") {
      clearInterval(state.store.timer); state.store.timer = null;
      stopVersionPoll();
    }
  }
  /** 머리의 `Queue | Store` — 릴리스 프로파일이 있는 저장소가 있을 때만 있다. 여럿이면 고르는 칸. */
  function renderNav() {
    var nav = $("#view-nav");
    if (!nav) return;
    var repos = state.store.repos || [];
    if (!repos.length) { nav.hidden = true; return; }
    nav.hidden = false;
    var current = state.store.repo && repos.some(function (r) { return r.name === state.store.repo; }) ? state.store.repo : repos[0].name;
    var h = '<a href="#/" data-nav="queue"' + (state.view !== "store" ? ' aria-current="page"' : "") + ">" + esc(tr("nav.queue")) + "</a>"
      + '<a href="#/store/' + encodeURIComponent(current) + '" data-nav="store"' + (state.view === "store" ? ' aria-current="page"' : "") + ">" + esc(tr("nav.store")) + "</a>";
    if (repos.length > 1) {
      h += '<select data-repo-select aria-label="' + esc(tr("nav.repo")) + '">' + repos.map(function (r) {
        return '<option value="' + esc(r.name) + '"' + (r.name === current ? " selected" : "") + ">" + esc(r.name) + "</option>";
      }).join("") + "</select>";
    } else h += '<span class="mono sub">' + esc(current) + "</span>";
    nav.innerHTML = h;
  }
  /** `GET /api/repos` — 404(프로파일 PR 이 없는 서버)·오류면 탭이 없을 뿐이다. */
  function loadRepos() {
    return storeApi().repos().then(function (res) {
      state.store.repos = res.ok ? releaseRepos(res.body) : [];
      state.store.reposStatus = res.status;
      renderNav();
    }).catch(function () { state.store.repos = state.store.repos || []; renderNav(); });
  }

  // ── 들어오기 · 받기 ──
  function enterStore(repo, route) {
    if (state.store.repo !== repo) {
      state.store.repo = repo; state.store.doc = null; state.store.secrets = null; state.store.screen = null;
      state.store.fetchError = null; state.store.error = null; state.store.loadedAt = null;
      state.store.release = null; state.store.releaseStatus = null; state.store.listing = null; state.store.listingStatus = null;
      state.store.busy = null; state.store.reviewError = null; state.store.reviewCode = null; state.store.planError = null; state.store.validate = null;
      state.store.review = { platforms: { ios: true, android: true }, managed: false, listingFull: false, phased: true, typedN: "", planId: null, buildName: null };
      state.store.driver = null; state.store.driverStatus = null; state.store.github = null; state.store.githubStatus = null;
      state.store.driverError = null; state.store.driverN = ""; state.store.driverDialogKey = null;
      state.store.driverForm = { version: null, track: "", dryRun: false }; state.store.nMode = null;
      state.store.versions = null; state.store.versionsStatus = null; state.store.version = null;
      state.store.versionStatus = null; state.store.versionError = null; state.store.createError = null;
      state.store.gateReturn = null; discardVersionEdits();
    }
    // 해시가 화면을 정한다 — 버전 목록(기본) · 버전 하나 · 상태. 다른 버전으로 가면 옛 상세를 버린다.
    var sub = route && route.sub ? route.sub : "versions";
    var id = route && route.id != null ? route.id : null;
    if (state.store.sub !== sub || state.store.versionId !== id) {
      state.store.sub = sub; state.store.versionId = id;
      state.store.version = null; state.store.versionStatus = null; state.store.versionError = null;
      discardVersionEdits();   // 다른 버전(또는 다른 화면)으로 가면 친 값도 저장 상태도 버린다
      // 주소로 화면을 골랐으면 그 화면을 보여 준다 — 관문이 **열려 있을 때만**이다(닫혀 있으면 설정이 맞다)
      var open = !!(state.store.doc && state.store.doc.setup && state.store.doc.setup.complete === true);
      if (state.store.screen === "settings" && open) state.store.screen = "store";
    }
    showView("store");
    renderStore();
    loadStore();
    clearInterval(state.store.timer);
    state.store.timer = setInterval(function () {
      if (state.view === "store" && state.conn.mode !== "paused" && !document.hidden) loadStore();
    }, STORE_REFRESH_MS);
  }
  function loadStore(opts) {
    var repo = state.store.repo;
    if (!repo) return Promise.resolve();
    var api = storeApi();
    var seq = (state.store.seq = (state.store.seq || 0) + 1);
    // 소개 자료는 서버에서 명령을 돌리는 것이라 30초 타이머로는 안 받는다 — 처음과 새로고침·검사 뒤에만.
    // 드라이버 `--status` 도 같아서 **상태 화면에서만** 부른다. 버전 페이지는 소개 자료를 한 번만
    // 받는다(프리필이 없는 칸의 «파일에서» 폴백 · AC-C5): 5초 폴링은 상세 하나뿐이다(워크플랜 §15).
    var onStatus = state.store.sub === "status";
    var onVersion = state.store.sub === "version";
    var wantListing = (onStatus || onVersion)
      && ((opts && opts.listing) || (state.store.listing == null && state.store.listingStatus == null));
    return Promise.all([api.repos(), api.repo(repo), api.secrets(repo), loadRelease(),
      wantListing ? loadListing() : null, onStatus ? loadDriver() : null, onStatus ? loadGithub() : null,
      state.store.sub === "version" ? loadVersion() : state.store.sub === "versions" ? loadVersions() : null]).then(function (rs) {
      if (seq !== state.store.seq || state.store.repo !== repo) return;  // 그 사이 다른 저장소로 갔다
      state.store.repos = rs[0].ok ? releaseRepos(rs[0].body) : [];
      state.store.reposStatus = rs[0].status;
      if (rs[1].ok && rs[1].body && typeof rs[1].body === "object") { state.store.doc = rs[1].body; state.store.error = null; }
      else { state.store.doc = null; state.store.error = rs[1]; }
      state.store.secrets = rs[2].ok && rs[2].body && Array.isArray(rs[2].body.items) ? rs[2].body : null;
      state.store.loadedAt = now();
      // 관문(항목 29·34): 완료가 아니면 설정 화면이다 — 사람이 스토어를 골랐어도 도로 관문이다.
      // 버전 주소로 바로 들어왔다면 가려던 곳을 적어 두고(E22), 관문이 열리면 거기로 보낸다.
      var complete = !!(state.store.doc && state.store.doc.setup && state.store.doc.setup.complete === true);
      if (!complete) {
        state.store.screen = "settings";
        if (state.store.sub !== "versions" && !state.store.gateReturn) state.store.gateReturn = location.hash;
      } else {
        // 관문에 막혀 설정으로 보냈던 사람은 관문이 열리는 순간 가려던 곳으로 간다 — 다시 «Enter
        // Store» 를 누르게 하지 않는다. 그 밖에는 설정 화면에 그대로 둔다(사람이 고른 화면이다).
        if (state.store.gateReturn) { state.store.screen = "store"; goBackToGateReturn(); return; }
        if (!state.store.screen) state.store.screen = "store";
      }
      renderNav(); renderStore(); syncVersionPoll();
    }).catch(function () {
      if (seq !== state.store.seq) return;
      state.store.error = { status: 0, body: null }; renderStore();
    });
  }
  function storeAuthChanged() { if (state.store.repo && state.view === "store") loadStore(); }

  // ── 렌더 ──
  function renderStore() {
    var body = $("[data-store-body]");
    if (!body || state.view !== "store") return;
    var repo = state.store.repo;
    var head = $("[data-store-title]");
    if (head) head.textContent = tr("store.heading", { repo: repo || DASH });
    if (state.store.repos && !state.store.repos.length) { body.innerHTML = '<p class="empty">' + esc(tr("store.none")) + "</p>"; return; }
    if (state.store.error) {
      var msg = state.store.error.status === 404 ? tr("store.unknown_repo", { repo: repo }) : tr("store.load_failed", { repo: repo, detail: storeErrorDetail(state.store.error) });
      body.innerHTML = '<p class="empty">' + esc(msg) + "</p>"; return;
    }
    if (!state.store.doc) { body.innerHTML = '<p class="empty">' + esc(tr("store.loading")) + "</p>"; return; }
    withFocus(function () {
      body.style.paddingBottom = "";   // 시트가 있는 화면에서만 아래 여백을 둔다(afterVersionRender)
      body.innerHTML = state.store.screen === "store" ? storeBodyHtml() : settingsHtml();
      // 렌더가 정한 열림은 기억이 아니다 — 사람이 바꾼 것만 `toggle` 에서 남긴다(호스트 절과 같은 규칙)
      $$("details.srow", body).forEach(function (d) { d.dataset.renderedOpen = d.open ? "1" : "0"; });
      if (state.store.screen === "store" && state.store.sub === "status") afterStoreRender();
      if (state.store.screen === "store" && state.store.sub === "version") afterVersionRender();
    });
  }
  function secretsItems() { return state.store.secrets ? state.store.secrets.items : []; }
  /** 관문 띠 + 저장된 비밀 표 (항목 29~33). */
  function settingsHtml() {
    var doc = state.store.doc, repo = state.store.repo;
    var sum = setupSummary(doc.setup, L());
    var h = '<div class="banner gate ' + sum.tone + '" role="status" data-gate>'
      + '<span class="g" aria-hidden="true">' + ROW_GLYPH[sum.tone] + "</span>"
      + "<b>" + esc(tr(sum.complete ? "store.gate.complete" : "store.gate.incomplete", { repo: repo })) + "</b>"
      + '<span data-gate-counts>' + esc(sum.text) + "</span>"
      // 버전 주소로 바로 들어왔다가 관문에 막힌 사람에게, 설정을 마치면 어디로 돌아가는지 말한다(E22)
      + (state.store.gateReturn ? '<span class="sub" data-gate-return="' + esc(state.store.gateReturn) + '">' + esc(tr("store.gate.return", { text: state.store.gateReturn })) + "</span>" : "")
      + '<span class="spacer"></span>'
      + '<button type="button" class="btn primary" data-enter-store' + (sum.complete ? "" : ' disabled title="' + esc(tr("store.gate.enter_hint")) + '"') + ">" + esc(tr("store.gate.enter")) + "</button>"
      + '<span class="sub gate-help">' + esc(tr("store.gate.help")) + "</span>"
      + "</div>";
    h += secretsTableHtml();
    return h;
  }
  function secretsTableHtml() {
    var doc = state.store.doc, repo = state.store.repo, items = secretsItems();
    var canVerify = !!state.token;
    var h = '<section class="secrets" aria-label="' + esc(tr("secrets.title", { repo: repo })) + '">'
      + '<div class="s-h"><span class="t">' + esc(tr("secrets.title", { repo: repo })) + "</span>"
      + '<span class="n">' + esc(tr("secrets.profile_line", { repo: repo, name: (state.store.secrets && state.store.secrets.dir_env) || (doc.profile && doc.profile.secrets_dir_env) || DASH })) + "</span>"
      + '<span class="spacer"></span>'
      + '<button type="button" class="btn" data-verify-all' + (canVerify && !state.store.verifying ? "" : " disabled") + (canVerify ? "" : ' title="' + esc(tr("store.gate.no_token")) + '"') + ">" + esc(tr(state.store.verifying ? "secrets.verifying" : "secrets.verify_all")) + "</button></div>";
    if (!state.admin) h += '<p class="sub readonly" data-readonly-reason>' + esc(tr("store.gate.admin_only", { repo: repo })) + "</p>";
    if (!state.store.secrets) h += '<p class="empty">' + esc(tr("store.load_failed", { repo: repo, detail: "secrets" })) + "</p>";
    else {
      h += '<div class="secwrap"><table class="sec"><thead><tr><th>' + esc(tr("secrets.col.secret")) + "</th><th>" + esc(tr("secrets.col.kind")) + "</th><th>" + esc(tr("secrets.col.state")) + "</th><th>" + esc(tr("secrets.col.fingerprint")) + "</th><th>" + esc(tr("secrets.col.verified")) + "</th><th></th></tr></thead><tbody>";
      items.forEach(function (it) { h += secretRowHtml(it); });
      h += "</tbody></table></div>";
    }
    h += '<p class="sub">' + esc(tr("secrets.never_shown")) + "</p></section>";
    return h;
  }
  function presentPill(present, word) {
    return '<span class="pill ' + (present ? "present" : "missing") + '"><span class="g" aria-hidden="true">' + (present ? "✓" : "✗") + "</span>" + esc(word) + "</span>";
  }
  /** 드롭존(항목 30). 파일 선택도 같은 자리. 「빌드 머신의 경로」 입력은 이 PR 에 없다 —
      서버 계약(STORE-TAB-API)에 경로를 받는 길이 없어서다. 드롭 파일은 메모리에서 바로 PUT. */
  function dropzoneHtml(secret, fileName, present) {
    var disabled = !state.admin;
    var text = tr(present ? "secrets.drop_replace" : "secrets.drop");
    return '<label class="drop' + (disabled ? " disabled" : "") + '" data-drop="' + esc(secret) + '"' + (fileName ? ' data-drop-file="' + esc(fileName) + '"' : "")
      + (disabled ? ' title="' + esc(tr("secrets.reject.admin")) + '"' : "") + '>'
      + '<input type="file" hidden' + (disabled ? " disabled" : "") + ' aria-label="' + esc(text) + '">'
      + '<span class="g" aria-hidden="true">⤓</span><span>' + esc(text) + "</span></label>";
  }
  function secretRowHtml(it) {
    var m = secretRowModel(it, L(), tz(), now());
    var action = "";
    if (m.input === "value") action = '<button type="button" class="btn" data-set-value="' + esc(m.name) + '"' + (state.admin ? "" : ' disabled title="' + esc(tr("secrets.reject.admin")) + '"') + ">" + esc(tr(m.action === "replace" ? "secrets.replace" : "secrets.add")) + "</button>";
    else if (m.input === "file") action = dropzoneHtml(m.name, null, m.present);
    var h = '<tr data-secret="' + esc(m.name) + '" class="' + (m.present ? "" : "missing") + '">'
      + '<td class="name"><span class="key">' + esc(m.name) + "</span>" + (m.optional ? ' <span class="chip">' + esc(tr("secrets.optional")) + "</span>" : "") + "</td>"
      + '<td class="kind">' + esc(m.kindWord) + "</td>"
      + '<td class="state">' + presentPill(m.present, m.presentWord) + "</td>"
      + '<td class="fp mono">' + esc(m.fingerprint) + "</td>"
      + '<td class="ver ' + m.verified.tone + '">' + esc(m.verified.text) + "</td>"
      + '<td class="act">' + action + "</td></tr>";
    m.files.forEach(function (f) {
      h += '<tr class="subfile' + (f.present ? "" : " missing") + '" data-secret="' + esc(m.name) + '" data-file="' + esc(f.name) + '">'
        + '<td class="name"><span class="key">' + esc(f.name) + "</span></td><td class=\"kind\">" + esc(tr("secrets.kind.file")) + "</td>"
        + '<td class="state">' + presentPill(f.present, tr(f.present ? "secrets.present" : "secrets.missing")) + "</td>"
        + '<td class="fp mono">' + esc(f.fingerprint) + '</td><td class="ver"></td>'
        + '<td class="act">' + dropzoneHtml(m.name, f.name, f.present) + "</td></tr>";
    });
    return h;
  }
  /** 접히는 행 하나 (7절). 색 = 상태, 글리프 + 글자로 한 번 더. 머리의 버튼은 여닫지 않는다. */
  function srowHtml(key, st, head, extra, bodyHtml, forceOpen) {
    var open = forceOpen || rowOpen(st, loadRowMemory()[state.store.repo + "/" + key]);
    return '<details class="srow" data-row="' + key + '" data-state="' + st + '"' + (open ? " open" : "") + ">"
      + '<summary><span class="g" aria-hidden="true">' + ROW_GLYPH[st] + '</span><span class="t">' + esc(tr("row." + key)) + "</span>"
      + '<span class="sr-state">' + esc(tr("row.state." + st)) + "</span>"
      + '<span class="n">' + head + "</span>" + (extra ? '<span class="spacer"></span>' + extra : "") + "</summary>"
      + '<div class="srow-body">' + bodyHtml + "</div></details>";
  }
  /** 관문을 지난 뒤의 본문 — 해시가 고른 화면 하나(워크플랜 §3.2). */
  function storeBodyHtml() {
    if (state.store.sub === "status") return storeScreenHtml();
    if (state.store.sub === "version") return versionPageHtml();
    return versionListHtml();
  }
  /** 화면 머리 한 줄 — 언제 받았는지 · 새로고침. `extra` 는 그 화면만의 단추다. */
  function storeHeadHtml(extra) {
    return '<div class="s-h store-head"><span class="sub" data-tick="updated" data-from="' + esc(state.store.loadedAt != null ? new Date(state.store.loadedAt).toISOString() : "") + '"></span>'
      + '<span class="spacer"></span>' + (extra || "")
      + '<button type="button" class="btn" data-store-refresh>' + esc(tr("store.refresh")) + "</button></div>";
  }
  /** 요약 띠(§12) — 칩 넷, 누르면 상태 화면으로. 빨간 것이 있으면 띠가 빨갛다. */
  function statusStripHtml() {
    var m = statusStripModel({
      doc: state.store.doc, items: secretsItems(), release: releaseDoc(), profile: currentProfile(),
      fetchError: state.store.fetchError, nowMs: now(), tzName: tz()
    }, L());
    var chips = m.chips.map(function (chip) {
      return '<span class="vchip ' + chip.tone + '" data-chip="' + esc(chip.code) + '">'
        + '<span class="g" aria-hidden="true">' + ROW_GLYPH[chip.tone] + "</span>"
        + (chip.label ? "<b>" + esc(chip.label) + "</b> " : "") + esc(chip.text) + "</span>";
    }).join("");
    return '<a class="vstrip ' + m.tone + '" href="' + esc(storeHash(state.store.repo, "status")) + '" data-strip="' + esc(m.tone) + '">'
      + chips + '<span class="spacer"></span><span class="btn" data-strip-detail>' + esc(tr("vstrip.detail")) + "</span></a>";
  }
  /** W1 버전 목록 — 스토어 탭의 첫 화면(§3.2). 행 넷은 상태 화면으로 옮겼다. */
  function versionListHtml() {
    var lang = L(), n = now();
    var m = versionListModel(state.store.versions, lang, n);
    var h = storeHeadHtml("");
    h += statusStripHtml();
    var live = m.live.known ? tr("version.list.live_line", { ios: m.live.ios || DASH, android: m.live.android || DASH }) : tr("version.list.live_unknown");
    var can = state.admin && !m.creating && state.store.versionsStatus !== 404;
    var why = !state.admin ? tr("version.list.new_hint.admin") : m.creating ? tr("version.list.new_hint.creating") : "";
    h += '<div class="s-h vhead"><span class="t">' + esc(tr("version.list.title")) + '</span><span class="n">' + esc(live) + "</span>"
      + '<span class="spacer"></span><button type="button" class="btn primary" data-version-new' + (can ? "" : ' disabled title="' + esc(why) + '"') + ">+ " + esc(tr("version.list.new")) + "</button></div>";
    if (state.store.versionsStatus === 404) return h + '<p class="empty">' + esc(tr("version.list.na")) + "</p>";
    if (state.store.versions == null) return h + '<p class="empty">' + esc(tr("store.loading")) + "</p>";
    h += '<div class="vrows">';
    m.drafts.forEach(function (row) { h += versionRowHtml(row); });
    if (!m.drafts.length) h += '<p class="empty" data-no-drafts>' + esc(tr("version.list.none")) + "</p>";
    if (m.live.known) {
      h += '<div class="vrow live" data-vrow="live"><span class="g" aria-hidden="true">' + ROW_GLYPH.ok + "</span>"
        + '<span class="vt"><b>' + esc(m.live.title) + '</b> <span class="pill v-ok">' + esc(tr("version.list.live")) + "</span></span>"
        + '<span class="vn">' + esc(m.live.fromPlanJob != null ? tr("version.list.from_plan", { id: m.live.fromPlanJob }) : "") + "</span></div>";
    }
    m.history.forEach(function (row) {
      h += '<div class="vrow old" data-vrow="' + esc(String(row.id)) + '"><span class="g" aria-hidden="true">·</span>'
        + '<span class="vt"><b>' + esc(row.title) + "</b></span>"
        + '<span class="vn">' + esc([row.submittedAt ? tr("version.row.submitted", { clock: fmtClock(row.submittedAt, tz(), n) }) : "",
          row.reviewJobId != null ? tr("version.row.review_job", { id: row.reviewJobId }) : ""].filter(Boolean).join(" · ")) + "</span></div>";
    });
    h += "</div>";
    if (m.ttlHours != null) h += '<p class="sub">' + esc(tr("version.list.ttl", { n: m.ttlHours })) + "</p>";
    h += '<p class="sub policy">' + esc(tr("store.policy")) + "</p>";
    return h;
  }
  function versionRowHtml(row) {
    var open = '<a class="btn" href="' + esc(storeHash(state.store.repo, "version", row.id)) + '" data-version-open="' + esc(String(row.id)) + '">' + esc(tr("version.row.open")) + "</a>";
    var retry = row.canRetry ? '<button type="button" class="btn" data-version-retry="' + esc(String(row.id)) + '"' + (state.admin ? "" : " disabled") + ">" + esc(tr("version.row.retry")) + "</button>" : "";
    // 못 누르는 이유는 `notes` 가 이미 글자로 말한다 — title 은 마우스에게만 있는 설명이라 거기에만
    // 두면 키보드·스크린 리더 쓰는 사람은 이유를 못 본다(C 단계 격리 검증 1).
    var why = row.discardWhy || (state.admin ? null : tr("version.list.new_hint.admin"));
    var discard = '<button type="button" class="btn danger" data-version-discard="' + esc(String(row.id)) + '"'
      + (state.admin && row.canDiscard ? "" : ' disabled' + (why ? ' title="' + esc(why) + '"' : "")) + ">" + esc(tr("version.row.discard")) + "</button>";
    return '<div class="vrow draft' + (row.expired ? " expired" : "") + '" data-vrow="' + esc(String(row.id)) + '" data-state="' + esc(row.state) + '"'
      + (row.deleting ? ' data-deleting="1"' : "") + ">"
      + '<span class="g" aria-hidden="true">' + (row.deleting ? "⌫" : row.state === "failed" ? "✗" : "✎") + "</span>"
      + '<span class="vt"><b>' + esc(row.title) + '</b> <span class="pill v-' + row.tone + '">' + esc(row.pill) + "</span></span>"
      + '<span class="vn">' + esc(row.notes.join(" · ")) + "</span>"
      + '<span class="va">' + open + retry + discard + "</span></div>";
  }
  /** W3 버전 페이지 — 이름 · 상태 · **이전 버전 값으로 채워진 편집 칸** 두 절(§3.2 · R6) ·
      본문 끝의 바텀시트(§4 · R10). 출시 · 게시 · 롤아웃 버튼은 여기에도 없다. */
  function versionPageHtml() {
    var v = state.store.version, lang = L(), n = now();
    var back = '<a class="btn" href="' + esc(storeHash(state.store.repo, "versions")) + '" data-version-back>' + esc(tr("version.page.back")) + "</a>";
    var h = storeHeadHtml(back);
    if (v == null) {
      return h + '<p class="empty">' + esc(state.store.versionStatus == null ? tr("store.loading")
        : tr("version.page.not_found", { id: state.store.versionId, detail: state.store.versionError || DASH })) + "</p>";
    }
    var row = versionRowModel(v, lang, n);
    var m = versionPageModel(versionEditCtx(), lang);
    h += '<div class="s-h vhead"><span class="t">' + esc(row.title) + '</span><span class="pill v-' + row.tone + '">' + esc(row.pill) + "</span>"
      + '<span class="n" data-version-notes>' + esc(row.notes.join(" · ")) + "</span></div>";
    if (row.state === "creating") h += '<p class="banner info" data-version-creating>' + esc(tr("version.row.creating", { id: row.createJobId != null ? row.createJobId : DASH })) + "</p>";
    if (row.error) h += '<p class="banner bad" data-version-error>' + esc(tr("version.row.error", { detail: String(row.error).slice(0, 200) })) + "</p>";
    h += versionEditHeadHtml(m, n);
    h += '<div class="stores" data-version-edit>'
      + m.sections.map(function (s) { return versionSectionHtml(s, m, versionReadOnlyCtx(v, s.platform)); }).join("")
      + "</div>";
    h += '<dl class="kv" data-version-kv>'
      + "<dt>" + esc(tr("version.page.state")) + "</dt><dd>" + esc(row.pill) + "</dd>"
      + "<dt>" + esc(tr("version.page.by")) + "</dt><dd>" + esc(v.created_by || DASH) + "</dd>"
      + "<dt>" + esc(tr("version.page.created")) + "</dt><dd>" + esc(v.created_at ? fmtClock(v.created_at, tz(), n) : DASH) + "</dd>"
      + "<dt>" + esc(tr("version.page.expires")) + "</dt><dd>" + esc(v.expires_at ? fmtClock(v.expires_at, tz(), n) : DASH) + "</dd>"
      + "</dl>";
    h += '<p class="sub policy">' + esc(tr("store.policy")) + "</p>";
    // 바텀시트는 본문의 **마지막 자식**이다 — sticky 의 담는 상자가 본문 전체여야 스크롤 내내
    // 화면 아래에 붙어 있는다(§4.2). 감싸는 div 를 두면 그 div 안에서만 붙는다.
    h += sheetHtml(m);
    return h;
  }
  /** 파일 폴백 — 프리필이 그 칸을 모를 때 쓰는 `store/` 값(«파일에서» · AC-C5). 릴리스 노트는
      미리보기 줄이 아니라 파일 하나라, 두 스토어에 같은 원문을 넣는다(항목 12·18). */
  function versionFileFields() {
    var listing = state.store.listing, f = listingFields(listing);
    var notes = listing && listing.release_notes && typeof listing.release_notes === "object" ? listing.release_notes : null;
    var text = notes && typeof notes.text === "string" ? notes.text : null;
    if (text != null) {
      Object.keys(PREFILL_KEYS).forEach(function (p) { if (typeof f[p].whats_new !== "string") f[p].whats_new = text; });
    }
    return f;
  }
  /** 이 브라우저의 편집 상태 — 버전이 바뀌면 새로 만든다. 문안은 state 에만 산다. */
  function versionEdits() {
    var ve = state.store.vedit;
    if (!ve || ve.id !== state.store.versionId) {
      ve = state.store.vedit = { id: state.store.versionId, typed: {}, dirty: {}, sending: {},
        sendingBody: null, remote: {}, save: { state: "idle", at: null, detail: null }, timer: null };
    }
    return ve;
  }
  function discardVersionEdits() {
    var ve = state.store.vedit;
    if (ve && ve.timer) clearTimeout(ve.timer);
    state.store.vedit = null;
  }
  function versionEditCtx() {
    var ve = versionEdits();
    return { version: state.store.version, file: versionFileFields(), typed: ve.typed, remote: ve.remote, admin: state.admin };
  }
  /** 읽기 전용 두 그룹(빌드·출시 설정 / 심사 정보·앱 콘텐츠)이 쓰는 형편. 값은 이 버전의 행과
      상세가 실어 준 릴리스 보기에서만 온다 — 이 화면은 빌드 번호를 지어내지 않는다. */
  function versionReadOnlyCtx(v, platform) {
    var lang = L(), n = now();
    var rel = v.release && typeof v.release === "object" ? v.release : {};
    var rpDoc = planEntryDoc(rel.review && rel.review.plan) || {};
    var observed = rpDoc.observed && typeof rpDoc.observed === "object" ? rpDoc.observed : {};
    var strip = versionStrip(rel, state.store.listing, currentProfile(), lang, tz(), n);
    return {
      strip: Object.assign({}, strip, { version: v[platform + "_version"] || strip.version }),
      observed: observed,
      autoRelease: typeof observed.auto_release === "boolean" ? observed.auto_release : null,
      reviewInfo: reviewInfoModel(secretsItems())
    };
  }
  /** 머리 한 줄 — «이전 버전 값으로 채워져 있습니다» · 프리필 출처 · 자동 저장 배지 · 읽기 전용
      이유 · 다른 곳에서 바뀐 칸 알림(E8). */
  function versionEditHeadHtml(m, nowMs) {
    var badge = saveBadge(versionEdits().save, L(), nowMs);
    var h = '<div class="s-h vedit-head"><span class="t" data-edit-head>' + esc(m.headText) + "</span>";
    h += '<span class="n" data-prefill-source>' + esc(m.prefillSource ? tr("version.edit.prefill_from", { source: m.prefillSource })
      : m.hasPrefill ? tr("version.edit.prefill_unknown") : tr("version.edit.no_prefill")) + "</span>";
    h += '<span class="spacer"></span><span data-save-slot>' + versionSaveBadgeHtml(badge) + "</span></div>";
    if (m.readOnly) h += '<p class="banner info" data-version-readonly="' + esc(m.readOnly) + '">' + esc(m.readOnlyText) + "</p>";
    h += '<p class="banner warn" data-version-remote' + (m.remote > 0 ? "" : " hidden") + ">"
      + esc(tr("version.edit.remote_notice", { n: m.remote })) + "</p>";
    return h;
  }
  function versionSaveBadgeHtml(badge) {
    var tick = badge.at ? ' data-tick="saved" data-from="' + esc(badge.at) + '"' : "";
    return '<span class="savebadge ' + badge.state + '" data-save-state="' + badge.state + '"><span class="txt"' + tick + ">" + esc(badge.text) + "</span>"
      + (badge.retry ? ' <button type="button" class="btn" data-listing-retry>' + esc(tr("version.edit.save.retry")) + "</button>" : "") + "</span>";
  }
  function versionChipsHtml(f) {
    return (f.changed ? '<span class="chip changed" data-field-changed>' + esc(tr("version.edit.changed")) + "</span>" : "")
      + (f.remote ? '<span class="chip remote" data-field-remote>' + esc(tr("version.edit.remote")) + "</span>" : "");
  }
  /** 문안 칸 하나 — 이름 · 칩 · 입력칸 · 출처 · 카운터 · 되돌리기. id 는 `f-<플랫폼>-<키>` 라
      바텀시트의 «남은 것» 이 나중에 그대로 가리킬 수 있다(§4.1). */
  function versionFieldHtml(f, m) {
    var ro = !m.editable;
    var common = ' id="' + esc(f.id) + '" data-listing-field="' + esc(f.platform + "." + f.key) + '" spellcheck="false" autocomplete="off"' + (ro ? " readonly" : "");
    var input = f.multiline
      ? "<textarea" + common + ' rows="' + (f.key === "description" || f.key === "full_description" ? 6 : 3) + '">' + esc(f.value) + "</textarea>"
      : '<input type="text"' + common + ' value="' + esc(f.value) + '">';
    return '<div class="fld ed" data-field="' + esc(f.platform + "." + f.key) + '" data-source="' + esc(f.source) + '"' + (f.changed ? ' data-changed="1"' : "") + ">"
      + '<span class="fl"><label for="' + esc(f.id) + '">' + esc(f.label) + '</label><span class="fchips" data-field-chips>' + versionChipsHtml(f) + "</span></span>"
      + '<span class="val">' + input + '<span class="src" data-field-source>' + esc(f.sourceText) + "</span></span>"
      + '<span class="fa">' + counterHtml(f.counter)
      + '<button type="button" class="btn link" data-field-revert="' + esc(f.platform + "." + f.key) + '"' + (ro || !f.canRevert ? " disabled" : "") + ">"
      + esc(tr("version.edit.revert")) + "</button></span></div>";
  }
  /** 스토어 절 하나 — 심사 패널과 **같은 그룹 차례**(항목 15)인데 문안만 편집 칸이다.
      스크린샷 · 그래픽은 보기만이고(Q8) 빌드 · 심사 정보 두 그룹은 같은 코드를 쓴다. */
  function versionSectionHtml(section, m, roCtx) {
    var platform = section.platform, ios = platform === "ios";
    var group = function (key, inner) {
      return '<div class="grp" data-group="' + key + '"><h4>' + esc(tr("review.group." + key)) + "</h4>" + inner + "</div>";
    };
    var h = '<section class="ssec" data-platform="' + platform + '" aria-label="' + esc(section.label) + '">'
      + '<h3><span>' + esc(section.label) + "</span>"
      + (section.version ? '<span class="pill v-new" data-section-version>' + esc(section.version) + "</span>" : "") + "</h3>";
    var shots = screenshotGroups(state.store.listing)[platform];
    h += group(ios ? "screenshots" : "graphics",
      '<p class="sub">' + esc(tr("review.screenshots.count", { n: shots.length }))
      + ' <span class="chip ' + (section.screenshots.state === "same" ? "same" : "unknown") + '" data-shots-mark="' + section.screenshots.state + '">' + esc(section.screenshots.text) + "</span></p>"
      + screenshotsHtml(shots, []) + '<p class="sub">' + esc(tr("version.edit.screenshots.readonly")) + "</p>");
    section.groups.forEach(function (g) {
      h += group(g.key, g.fields.map(function (f) { return versionFieldHtml(f, m); }).join(""));
    });
    h += group(ios ? "build" : "release", buildGroupInnerHtml(platform, roCtx));
    h += group(ios ? "review_info" : "app_content", infoGroupInnerHtml(platform, roCtx));
    return h + "</section>";
  }
  /**
   * 치는 동안에는 innerHTML 을 갈아 끼우지 않는다 — 칩 · 출처 · 카운터 · 되돌리기 · 저장 배지만
   * 제자리에서 고친다. 입력칸의 값은 손대지 않는다(커서가 튀지 않는다).
   */
  function renderVersionEditState() {
    var body = $("[data-store-body]");
    if (!body || state.view !== "store" || state.store.sub !== "version" || state.store.screen !== "store") return;
    var lang = L(), n = now();
    var m = versionPageModel(versionEditCtx(), lang);
    var fields = {};
    m.sections.forEach(function (s) {
      s.groups.forEach(function (g) { g.fields.forEach(function (f) { fields[f.platform + "." + f.key] = f; }); });
    });
    $$("[data-version-edit] [data-field]", body).forEach(function (el) {
      var f = fields[el.getAttribute("data-field")];
      if (!f) return;
      el.setAttribute("data-source", f.source);
      if (f.changed) el.setAttribute("data-changed", "1"); else el.removeAttribute("data-changed");
      var chips = el.querySelector("[data-field-chips]");
      if (chips) chips.innerHTML = versionChipsHtml(f);
      var src = el.querySelector("[data-field-source]");
      if (src) src.textContent = f.sourceText;
      var cnt = el.querySelector(".counter");
      if (cnt) { cnt.textContent = f.counter.text; cnt.className = "counter " + f.counter.tone; cnt.title = f.counter.text; }
      var rev = el.querySelector("[data-field-revert]");
      if (rev) rev.disabled = !m.editable || !f.canRevert;
      var box = el.querySelector("[data-listing-field]");
      if (box) box.readOnly = !m.editable;
    });
    var head = body.querySelector("[data-edit-head]");
    if (head) head.textContent = m.headText;
    var warn = body.querySelector("[data-version-remote]");
    if (warn) { warn.hidden = m.remote === 0; warn.textContent = tr("version.edit.remote_notice", { n: m.remote }); }
    var slot = body.querySelector("[data-save-slot]");
    if (slot) slot.innerHTML = versionSaveBadgeHtml(saveBadge(versionEdits().save, lang, n));
    renderSheetState();   // 상한을 넘긴 칸은 시트의 «남은 것» 이기도 하다(E9)
  }
  /** 칸 하나를 쳤다 — 값은 state 에 남기고(저장이 실패해도 화면에 남는다 · E7) 800 ms 뒤에 그
      키만 보낸다. 이어서 다른 칸을 고치면 한 번에 묶여 나간다. */
  function listingFieldTyped(fieldId, value) {
    var at = fieldId.indexOf("."), platform = fieldId.slice(0, at), key = fieldId.slice(at + 1);
    if (!PREFILL_KEYS[platform] || PREFILL_KEYS[platform].indexOf(key) < 0) return;
    var ve = versionEdits();
    if (!ve.typed[platform]) ve.typed[platform] = {};
    ve.typed[platform][key] = value;
    ve.dirty[fieldId] = true;
    delete ve.remote[fieldId];   // 내가 이 칸을 고쳤다 — 마지막 저장이 이긴다(E8)
    renderVersionEditState();
    scheduleListingSave();
  }
  function scheduleListingSave() {
    var ve = versionEdits();
    if (ve.timer) clearTimeout(ve.timer);
    ve.timer = setTimeout(function () { ve.timer = null; flushListingSave(); }, VERSION_SAVE_DEBOUNCE_MS);
  }
  /**
   * 자동 저장 — **바뀐 키만** `PUT …/release/versions/<id>/listing` 으로. 앞의 저장이 아직
   * 안 끝났으면 기다렸다가 남은 것을 다시 보낸다. 실패하면 그 키들은 «보낼 것» 으로 남아
   * «다시 저장» 이 같은 몸통을 다시 보내고, 친 글은 화면에 그대로 있다(E7).
   */
  function flushListingSave() {
    var ve = versionEdits(), repo = state.store.repo, id = state.store.versionId;
    if (ve.sendingBody || repo == null || id == null) return;
    var body = listingPutBody(ve.typed, ve.dirty);
    if (body == null) return;
    ve.sendingBody = body; ve.sending = ve.dirty; ve.dirty = {};
    ve.save = { state: "saving", at: null, detail: null };
    renderVersionEditState();
    storeApi().versionListing(repo, id, body).then(function (res) {
      if (state.store.versionId !== id || state.store.vedit !== ve) return;
      ve.sendingBody = null;
      if (res.ok) {
        applySavedListing(ve, res.body);
        ve.sending = {};
        ve.save = { state: "saved", at: nowIso(), detail: null };
      } else {
        Object.keys(ve.sending).forEach(function (k) { ve.dirty[k] = true; });
        ve.sending = {};
        ve.save = { state: "failed", at: null, detail: refusalText(res, L()) };
        if (res.status === 401 || res.status === 403) tokenRejected();
      }
      renderVersionEditState();
      if (Object.keys(ve.dirty).length && res.ok) scheduleListingSave();
    }).catch(function () {
      if (state.store.vedit !== ve) return;
      ve.sendingBody = null;
      Object.keys(ve.sending).forEach(function (k) { ve.dirty[k] = true; });
      ve.sending = {};
      ve.save = { state: "failed", at: null, detail: tr("version.edit.save.network") };
      renderVersionEditState();
    });
  }
  /** 서버가 되돌려 준 편집본 · diff 로 상세를 갱신한다 — 다음 폴링까지 기다리지 않는다. */
  function applySavedListing(ve, out) {
    var v = state.store.version;
    var doc = out && typeof out === "object" ? out : {};
    var edited = doc.edited && typeof doc.edited === "object" ? doc.edited : null;
    ve.seen = edited;
    Object.keys(ve.remote).forEach(function (k) { delete ve.remote[k]; });
    if (!v) return;
    v.edited = edited;
    v.has_edits = edited != null;
    if (doc.diff && typeof doc.diff === "object") {
      v.diff = doc.diff;
      v.changed = Array.isArray(doc.diff.fields) ? doc.diff.fields.length : 0;
    }
    if (doc.last_edit_at !== undefined) v.last_edit_at = doc.last_edit_at;
    if (typeof doc.state === "string") v.state = doc.state;
  }
  /** «되돌리기» — 이전 버전 값(없으면 빈 값)으로 되돌리고 곧바로 저장한다. 저장이 끝나면 그 칸은
      이전 버전과 같아져 diff 에서 빠진다(E10). */
  function revertListingField(fieldId) {
    var at = fieldId.indexOf("."), platform = fieldId.slice(0, at), key = fieldId.slice(at + 1);
    if (!PREFILL_KEYS[platform] || PREFILL_KEYS[platform].indexOf(key) < 0) return;
    var ve = versionEdits(), v = state.store.version || {};
    var base = listingPart(v.prefill, platform)[key];
    var value = typeof base === "string" ? base : "";
    if (!ve.typed[platform]) ve.typed[platform] = {};
    ve.typed[platform][key] = value;
    ve.dirty[fieldId] = true;
    delete ve.remote[fieldId];
    var box = $("#f-" + platform + "-" + key);
    if (box) box.value = value;
    if (ve.timer) { clearTimeout(ve.timer); ve.timer = null; }
    renderVersionEditState();
    flushListingSave();
  }
  // ── 바텀시트 — 진행 · 남은 것 · 심사 제출 (워크플랜 §4.2 · R8~R12) ──────────────────────
  //
  // 버전 페이지의 **마지막 자식**이라 스크롤해도 화면 아래에 붙어 있다(`position: sticky`).
  // 접힘/펼침은 저장소마다 기억한다. 그 밖의 선택(관리형 게시 · 친 N)은 절대 기억하지 않는다 —
  // 한 번의 확인이 다음 제출까지 넘어가면 안 된다(결정 3 · 항목 28).
  var SHEET_KEY = "rcm.sheet.";
  var SHEET_DRIVER_POLL_MS = 15000;   // 상세 5초보다 **느리게** — 이 라우트는 `--status` 를 돌린다
  var SHEET_LEFT_GLYPH = { bad: "✗", run: "▶", todo: "☐", warn: "!" };

  function sheetOpen() {
    var v = lsGet(SHEET_KEY + state.store.repo);
    return v == null ? true : v === "1";   // 처음엔 펼쳐 둔다 — 남은 것을 숨기지 않는다
  }
  function setSheetOpen(open) { lsSet(SHEET_KEY + state.store.repo, open ? "1" : "0"); }
  /** 소개 자료가 말하는 스토어별 그래픽 수. 아직 못 받았으면 null 이고 시트는 말하지 않는다. */
  function versionGraphics() {
    var listing = state.store.listing;
    if (!listing || state.store.listingStatus === 404) return { ios: null, android: null };
    var g = screenshotGroups(listing);
    return { ios: g.ios.length, android: g.android.length };
  }
  /** 시트가 보는 모든 것. 릴리스 보기는 **버전 상세가 실어 준 것**이다(따로 안 부른다 · §15). */
  function sheetCtx(page) {
    var v = state.store.version || {};
    var release = v.release && typeof v.release === "object" ? v.release : releaseDoc();
    var rv = state.store.review;
    return {
      version: v, release: release, repo: state.store.repo, profile: currentProfile(),
      driver: driverModel(), layers: release ? buildLayers(release.jobs, rowsById(state.status), L(), tz(), now()) : null,
      page: page || versionPageModel(versionEditCtx(), L()), graphics: versionGraphics(),
      choices: { platforms: rv.platforms, managed: rv.managed, listingFull: rv.listingFull, phased: rv.phased, nMode: state.store.nMode, typedN: rv.typedN },
      token: state.token, admin: state.admin, busy: state.store.busy, refusal: state.store.reviewCode,
      lang: L(), nowMs: now(), tzName: tz()
    };
  }
  function sheetNow(page) { return sheetModel(sheetCtx(page)); }
  function sheetBarHtml(m, cls) {
    var pct = isNum(m.pct) ? m.pct : null;
    return '<div class="pbar ' + cls + '" role="progressbar" aria-valuemin="0" aria-valuemax="100"'
      + (pct != null ? ' aria-valuenow="' + pct + '"' : ' data-basis="none"')
      + ' aria-valuetext="' + esc(m.head.statusLine) + '"><i data-fill="' + (pct != null ? pct : 0) + '"></i></div>';
  }
  function sheetLeftHtml(m) {
    if (!m.remaining.length) return '<p class="sub" data-sheet-none>' + esc(tr("sheet.nothing_left")) + "</p>";
    return m.remaining.map(function (r) {
      var fix = r.fix || {};
      return '<button type="button" class="ml ' + esc(r.severity) + '" data-sheet-fix="' + esc(r.code) + '"'
        + ' data-fix-anchor="' + esc(fix.anchor || "") + '" data-fix-route="' + esc(fix.route || "") + '"'
        + (fix.anchor || fix.route ? ' title="' + esc(tr("sheet.fix")) + '"' : " disabled")
        + '><span class="g" aria-hidden="true">' + (SHEET_LEFT_GLYPH[r.severity] || "·") + "</span><span>" + esc(r.text) + "</span></button>";
    }).join("");
  }
  function sheetDiffHtml(m) {
    var h = "";
    if (!m.diff.changed) h += '<p class="sub" data-sheet-diff-none>' + esc(m.diff.noneText) + "</p>";
    m.diff.fields.forEach(function (f) {
      h += '<div class="dl del" data-diff-old="' + esc(f.platform + "." + f.key) + '">' + esc(f.oldText) + "</div>"
        + '<div class="dl add" data-diff-new="' + esc(f.platform + "." + f.key) + '">' + esc(f.newText) + "</div>";
    });
    m.diff.screenshots.forEach(function (line) { h += '<div class="dl">' + esc(line) + "</div>"; });
    return h;
  }
  /** 제출 조건 — 체크박스 넷 · 관리형 게시 · 빌드 번호 토글과 칸. 심사 패널에서 옮겨 온 그대로다. */
  function sheetCondHtml(m) {
    var rv = state.store.review;
    var cb = function (name, key, checked, cls) {
      return '<label class="ck' + (cls ? " " + cls : "") + '"><input type="checkbox" data-review-check="' + name + '"' + (checked ? " checked" : "") + "> " + esc(tr(key)) + "</label>";
    };
    var h = '<div class="checks">'
      + cb("ios", "review.check.ios", rv.platforms.ios) + cb("android", "review.check.android", rv.platforms.android)
      + cb("full", "review.check.full", rv.listingFull) + cb("phased", "review.check.phased", rv.phased)
      + '<span id="sheet-managed" tabindex="-1">' + cb("managed", "review.check.managed", rv.managed === true, "managed" + (m.managedShown ? "" : " off")) + "</span>"
      + "</div>";
    h += nModeToggleHtml();
    if (m.nMode === "auto") {
      h += '<div class="nbox auto" id="sheet-n" tabindex="-1" data-n-auto><span class="lab">' + esc(tr("n.mode.label")) + '</span><span class="nval mono">' + esc(m.planN != null ? String(m.planN) : DASH) + "</span>"
        + '<span class="sub nfull">' + esc(m.planN != null ? tr("sheet.n_auto", { n: m.planN }) : tr("sheet.n_auto_unknown")) + "</span></div>";
    } else {
      h += '<div class="nbox" data-n-typed><label for="sheet-n">' + esc(tr("review.n_label")) + '</label><span class="sub">' + esc(m.planN != null ? tr("review.n_hint", { n: m.planN }) : tr("review.n_unknown")) + "</span>"
        + '<input id="sheet-n" type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="off" value="' + esc(rv.typedN) + '" aria-describedby="sheet-n-state"><span id="sheet-n-state" class="nstate" data-n-state></span></div>';
    }
    return h;
  }
  function sheetResultHtml(m) {
    if (!m.result) return "";
    return '<div class="result ' + m.result.tone + '" data-sheet-result="' + esc(m.result.status) + '"><b>' + esc(tr("sheet.title.result")) + " · " + esc(m.result.status) + "</b>"
      + '<ul class="plain">' + m.result.platforms.map(function (p) { return "<li>" + esc(p.text) + "</li>"; }).join("") + "</ul>"
      + (m.result.extras.length ? '<p class="sub">' + esc(m.result.extras.join(" · ")) + "</p>" : "") + "</div>";
  }
  /** 시트 하나(W3 머리 · W4 · W5). 출시 · 게시 · 롤아웃 버튼은 어떤 상태에도 없다. */
  function sheetHtml(page) {
    var m = sheetNow(page), open = sheetOpen();
    var busy = state.store.busy;
    var h = '<section class="sheet" id="release-sheet" data-sheet data-tone="' + esc(m.tone) + '" data-open="' + (open ? "1" : "0") + '"'
      + ' role="group" aria-label="' + esc(tr("sheet.aria")) + '">';
    h += '<div class="sh-head"><span class="grab" aria-hidden="true"></span>'
      + '<span class="ver" data-sheet-ver>' + esc(m.head.ver) + ' <span class="pill v-' + esc(m.tone) + '" data-sheet-pill>' + esc(m.head.pill) + "</span></span>"
      + '<span class="st" data-sheet-status>' + esc(m.head.statusLine) + "</span>";
    // R8 — 도는 동안에는 접힌 한 줄에도 막대와 진행 정도가 보인다(줄여서라도)
    h += '<span class="mini" data-sheet-mini' + (m.head.live ? "" : " hidden") + ">" + sheetBarHtml(m, "thin")
      + '<span class="pct" data-sheet-pct>' + esc(isNum(m.pct) ? m.pct + "%" : DASH) + "</span></span>";
    if (!m.closed) {
      h += '<button type="button" class="btn primary" data-sheet-submit disabled>' + esc(tr(busy === "submit" ? "sheet.submitting" : "sheet.submit")) + "</button>";
    }
    h += '<button type="button" class="btn" data-sheet-toggle aria-expanded="' + (open ? "true" : "false") + '" aria-controls="sheet-body">'
      + esc(tr(open ? "sheet.collapse" : "sheet.expand")) + "</button></div>";
    h += '<div class="sh-body" id="sheet-body" data-sheet-body' + (open ? "" : " hidden") + ">";
    h += sheetBarHtml(m, "wide");
    h += '<div class="stages" id="sheet-stages" tabindex="-1" data-sheet-stages>'
      + m.stages.map(function (s) {
        return '<span class="chip-st ' + esc(s.state) + '" data-stage="' + esc(s.id) + '">' + esc(s.id) + " " + esc(s.label) + "</span>";
      }).join("") + "</div>";
    h += '<p class="sub" data-sheet-basis>' + esc([m.basis, m.now, m.elapsed, m.finishes].filter(Boolean).join(" · ")) + "</p>";
    h += '<div class="two"><div><div class="lab">' + esc(tr("sheet.title.left")) + '</div><div class="miss" data-sheet-left>' + sheetLeftHtml(m) + "</div></div>"
      + '<div><div class="lab">' + esc(tr("sheet.title.diff")) + '</div><div class="diffs" data-sheet-diff>' + sheetDiffHtml(m) + "</div></div></div>";
    if (!m.closed) {
      h += '<div class="lab">' + esc(tr("sheet.title.conditions")) + "</div>" + sheetCondHtml(m)
        + '<div class="actions"><span class="sub" data-sheet-reason></span>'
        + (state.store.reviewError ? '<span class="bad" data-sheet-error>' + esc(state.store.reviewError) + "</span>" : "") + "</div>";
    }
    h += '<p class="sub" data-sheet-sending><b>' + esc(tr("sheet.title.sending")) + "</b> " + esc(m.sending.join(" · ")) + "</p>";
    h += sheetResultHtml(m);
    h += '<p class="sub policy" data-sheet-policy>' + esc(m.policy) + "</p>";
    return h + "</div></section>";
  }
  /** 제출 버튼 · 이유 · N 상태만 제자리에서 — 타이핑마다 시트를 다시 그리지 않는다. */
  function renderSheetState() {
    var sheet = $("#release-sheet");
    if (!sheet) return;
    var m = sheetNow();
    var btn = sheet.querySelector("[data-sheet-submit]"), reason = sheet.querySelector("[data-sheet-reason]");
    if (btn) { btn.disabled = !m.canSubmit; btn.title = m.canSubmit ? "" : m.reasonText; }
    if (reason) reason.textContent = m.canSubmit ? "" : m.reasonText;
    var left = sheet.querySelector("[data-sheet-left]");
    if (left) left.innerHTML = sheetLeftHtml(m);
    var status = sheet.querySelector("[data-sheet-status]");
    if (status) status.textContent = m.head.statusLine;
    var nState = sheet.querySelector("[data-n-state]");
    if (nState) {
      var typed = state.store.review.typedN;
      if (!typed) { nState.textContent = ""; nState.className = "nstate"; }
      else if (nMatches(typed, m.planN)) { nState.textContent = tr("review.n_ok", { n: m.planN }); nState.className = "nstate ok"; }
      else { nState.textContent = m.planN != null ? tr("review.n_mismatch", { n: m.planN }) : tr("review.n_unknown"); nState.className = "nstate bad"; }
    }
    var managed = sheet.querySelector("label.ck.managed");
    if (managed) managed.classList.toggle("off", !m.managedShown);
  }
  /** 남은 것 한 줄을 눌렀다 — 고치는 칸으로 스크롤하고 포커스를 준다(AC-D9). 다른 화면이면
      그리로 가고, 그 화면이 그려진 뒤에 같은 일을 한다. */
  function sheetGoFix(anchor, route) {
    if (route && location.hash !== route) {
      state.store.fixAnchor = anchor || null;
      location.hash = route;
      return;
    }
    focusAnchor(anchor);
  }
  function focusAnchor(anchor) {
    if (!anchor) return;
    var el;
    try { el = $(anchor, $("#store")); } catch (e) { el = null; }
    if (!el) return;
    if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center" });
    if (typeof el.focus === "function") el.focus({ preventScroll: true });
  }
  /**
   * 드라이버 상태를 **느리게** 받는다(§15 · 이 PR 의 결정): 상세 폴링은 5초인데 이 라우트는
   * 빌드 머신에서 `--status` 를 한 번 돌린다. 프로파일이 드라이버를 선언한 저장소에서만, 그리고
   * 버전 페이지가 열려 있는 동안만 15초에 한 번이다. 선언이 없으면 한 번도 부르지 않는다.
   */
  function stopDriverPoll() {
    if (state.store.driverTimer) { clearInterval(state.store.driverTimer); state.store.driverTimer = null; }
  }
  function syncDriverPoll() {
    var want = state.view === "store" && state.store.screen === "store"
      && state.store.sub === "version" && !!currentProfile().driver;
    if (!want) { stopDriverPoll(); return; }
    if (state.store.driverTimer) return;
    if (state.store.driver == null && state.store.driverStatus == null) loadDriver().then(renderStore);
    state.store.driverTimer = setInterval(function () {
      if (state.view !== "store" || state.store.sub !== "version" || state.store.screen !== "store") return;
      if (document.hidden || state.conn.mode === "paused") return;
      loadDriver().then(function () { renderStore(); });
    }, SHEET_DRIVER_POLL_MS);
  }
  /** 상태 화면 `#/store/<repo>/status` (항목 1~6 · 27): 행 넷 + 접힌 본체 머리 + 고정 문장. */
  function storeScreenHtml() {
    var doc = state.store.doc, repo = state.store.repo, items = secretsItems(), profile = doc.profile || {};
    var n = now();
    var h = storeHeadHtml('<a class="btn" href="' + esc(storeHash(repo, "versions")) + '" data-version-back>' + esc(tr("version.page.back")) + "</a>");
    // 최상단 큰 막대는 없다(결정 Q6) — 진행은 버전 페이지의 바텀시트와 목록 행이 말한다.
    // Setup — 프로파일이 선언한 비밀 표(읽기 전용 + Replace · Verify all)
    var setupState = rowState("setup", { setup: doc.setup });
    h += srowHtml("setup", setupState, esc(setupHead(doc.setup, items, profile, L(), tz(), n)),
      '<button type="button" class="btn" data-goto-settings>' + esc(tr("row.settings")) + "</button>", secretsTableHtml());
    // Source — 미러 · main/dev · main ⊂ dev · Fetch remote (항목 4 · 25 · 42)
    var srcCtx = { nowMs: n, fetchError: state.store.fetchError };
    var srcState = rowState("source", { doc: doc, nowMs: n, fetchError: state.store.fetchError });
    var br = doc.branches || {}, mirror = doc.mirror || {};
    var canFetch = !!state.token && !state.store.fetching;
    var fetchBtn = '<button type="button" class="btn" data-fetch-remote' + (canFetch ? "" : " disabled") + (state.token ? "" : ' title="' + esc(tr("source.no_fetch_token")) + '"') + ">" + esc(tr(state.store.fetching ? "source.fetching" : "source.fetch")) + "</button>";
    var srcBody = '<dl class="kv">'
      + "<dt>main</dt><dd class=\"mono\">" + esc(br.main || DASH) + "</dd>"
      + "<dt>dev</dt><dd class=\"mono\">" + esc(br.dev || DASH) + "</dd>"
      + "<dt>" + esc(tr("row.source")) + "</dt><dd>" + esc(br.main_in_dev === true ? tr("source.main_in_dev") : br.main_in_dev === false ? tr("source.main_not_in_dev") : tr("source.branches_unknown")) + "</dd>"
      + "<dt>mirror</dt><dd>" + esc(mirror.fetched_at ? tr("source.mirror_at", { clock: fmtClock(mirror.fetched_at, tz(), n) }) : tr("source.never_fetched")) + (mirror.path ? ' <span class="mono sub">' + esc(mirror.path) + "</span>" : "") + "</dd>"
      + "<dt>profile</dt><dd>" + esc(tr("source.profile_line", { ref: profile.default_branch || DASH, text: profile.tag || DASH })) + "</dd>"
      + (state.store.fetchError ? '<dt class="bad">fetch</dt><dd class="bad">' + esc(tr("source.fetch_failed", { detail: String(state.store.fetchError).slice(0, 60) })) + "</dd>" : "")
      + "</dl>";
    // GitHub 카드(항목 25): 미러의 최근 다섯 커밋 · 태그 · PR 은 «다음»
    srcBody += githubCardHtml(githubCardModel(state.store.github, state.store.githubStatus, profile.default_branch || "main", L(), tz(), n));
    h += srowHtml("source", srcState, esc(sourceHead(doc, srcCtx, L()).join(" · ")), fetchBtn, srcBody);
    // Build·upload(항목 5 · 46~49) · Store(항목 6 · 42) — `GET …/release` 가 404 면 «not available in this build»
    var release = releaseDoc(), rstatus = state.store.releaseStatus;
    var buildModel = buildRowModel(release, rstatus, profile, rowsById(state.status), L(), tz(), n);
    var storeModel = storeRowModel(release, rstatus, profile, L(), tz(), n, state.store.planError);
    h += buildRowHtml(buildModel);
    h += storeRowHtml(storeModel);
    // 본체(항목 7~24) — 필이 왜 못 여는지 말하고, 네 행이 초록이면 펼쳐진다
    var pill = panelPill(release, { setup: setupState, source: srcState, build: buildModel.state, store: storeModel.state }, rstatus);
    h += reviewPanelHtml(pill, { setup: setupState, source: srcState, build: buildModel.state, store: storeModel.state });
    h += '<p class="sub policy">' + esc(tr("store.policy")) + "</p>";
    return unsafeBannerHtml() + h;
  }
  /** 렌더 뒤 상태 화면의 드라이버 폼을 채운다. 제출 버튼은 이제 시트에만 있다(§4.2). */
  function afterStoreRender() { renderDriverState(); applyBarFills($("[data-store-body]")); maybeOpenConfirmN(); }
  /** 렌더 뒤 시트를 채운다 — 제출 버튼은 HTML 에서 늘 비활성으로 나오고 여기서만 열린다. */
  function afterVersionRender() {
    renderSheetState();
    applyBarFills($("[data-store-body]"));
    // 붙어 있는 시트가 본문의 끝을 가리지 않도록 그 높이만큼 아래 여백을 준다(E23). 담는 상자가
    // 커지므로 마지막 칸까지 시트 위로 스크롤된다.
    var body = $("[data-store-body]"), sheet = $("#release-sheet");
    if (body && sheet) body.style.paddingBottom = (sheet.offsetHeight + 16) + "px";
    // 다른 화면의 «남은 것» 을 눌러 여기로 왔으면 그 칸으로 간다(AC-D9)
    var anchor = state.store.fixAnchor;
    if (anchor) { state.store.fixAnchor = null; focusAnchor(anchor); }
  }
  // ── 릴리스 상태 · 소개 자료 (STORE-TAB-API-2) ──
  function releaseDoc() { return state.store.release; }
  function currentProfile() { return (state.store.doc && state.store.doc.profile) || {}; }
  /** 새 심사 플랜이 오면 사람의 확인(관리형 게시 체크 · 친 N)은 지워진다 — 기억하지 않는다(결정 3 · 항목 28). */
  function syncReviewChoices() {
    var r = state.store.release, rv = state.store.review;
    var planId = r && r.review && r.review.plan ? r.review.plan.job_id : null;
    var buildName = r && r.plan ? r.plan.build_name : null;
    if (rv.planId !== planId || rv.buildName !== buildName) { rv.planId = planId; rv.buildName = buildName; rv.managed = false; rv.typedN = ""; }
  }
  function loadRelease() {
    var repo = state.store.repo, api = storeApi();
    if (!repo || typeof api.release !== "function") { state.store.release = null; state.store.releaseStatus = 404; return Promise.resolve(); }
    return api.release(repo).then(function (res) {
      if (state.store.repo !== repo) return;
      state.store.releaseStatus = res.status;
      state.store.release = res.ok && res.body && typeof res.body === "object" ? res.body : null;
      syncReviewChoices();
    }).catch(function () { state.store.release = null; state.store.releaseStatus = 0; });
  }
  function loadListing() {
    var repo = state.store.repo, api = storeApi();
    if (!repo || typeof api.listing !== "function") { state.store.listing = null; state.store.listingStatus = 404; return Promise.resolve(); }
    return api.listing(repo).then(function (res) {
      if (state.store.repo !== repo) return;
      state.store.listingStatus = res.status;
      state.store.listing = res.ok && res.body && typeof res.body === "object" ? res.body : null;
    }).catch(function () { state.store.listing = null; state.store.listingStatus = 0; });
  }

  /** 드라이버 상태. 404 = 이 빌드에 없음. 회차(build_name · plan_n)가 바뀌면 친 N 과 Start 폼은 지워진다. */
  function loadDriver() {
    var repo = state.store.repo, api = storeApi();
    if (!repo || typeof api.driver !== "function") { state.store.driver = null; state.store.driverStatus = 404; return Promise.resolve(); }
    return api.driver(repo).then(function (res) {
      if (state.store.repo !== repo) return;
      var prev = state.store.driver;
      state.store.driverStatus = res.status;
      state.store.driver = res.ok && res.body && typeof res.body === "object" ? res.body : null;
      var cur = state.store.driver;
      if (!prev || !cur || prev.build_name !== cur.build_name || prev.plan_n !== cur.plan_n || prev.running !== cur.running) state.store.driverN = "";
    }).catch(function () { state.store.driver = null; state.store.driverStatus = 0; });
  }
  // ── 버전 목록 · 버전 하나 (워크플랜 §3.2). 네 라우트 가운데 **상세만** 5초로 폴링한다 ──
  function loadVersions() {
    var repo = state.store.repo, api = storeApi();
    if (!repo || typeof api.versions !== "function") { state.store.versions = null; state.store.versionsStatus = 404; return Promise.resolve(); }
    return api.versions(repo).then(function (res) {
      if (state.store.repo !== repo) return;
      state.store.versionsStatus = res.status;
      state.store.versions = res.ok && res.body && typeof res.body === "object" ? res.body : null;
    }).catch(function () { state.store.versions = null; state.store.versionsStatus = 0; });
  }
  function loadVersion() {
    var repo = state.store.repo, id = state.store.versionId, api = storeApi();
    if (!repo || id == null || typeof api.version !== "function") { state.store.version = null; state.store.versionStatus = 404; return Promise.resolve(); }
    return api.version(repo, id).then(function (res) {
      if (state.store.repo !== repo || state.store.versionId !== id) return;
      state.store.versionStatus = res.status;
      var doc = res.ok && res.body && typeof res.body === "object" ? res.body : null;
      if (doc) noteRemoteListing(doc);
      state.store.version = doc;
      state.store.versionError = res.ok ? null : storeErrorDetail(res);
    }).catch(function () { state.store.version = null; state.store.versionStatus = 0; });
  }
  /** 폴링이 가져온 편집본이 우리가 마지막으로 본 것과 달라졌으면 그 칸을 «다른 곳에서 바뀜» 으로
      센다(E8). 화면의 값은 그대로 둔다 — 폴링이 사람이 치던 글을 조용히 덮지 않는다. */
  function noteRemoteListing(doc) {
    var ve = versionEdits();
    var incoming = doc.edited && typeof doc.edited === "object" ? doc.edited : null;
    if (ve.seen !== undefined) {
      var found = remoteListingEdits(ve.seen, incoming, ve.typed);
      Object.keys(found).forEach(function (k) { ve.remote[k] = true; });
    }
    ve.seen = incoming;
  }
  function stopVersionPoll() {
    if (state.store.versionTimer) { clearInterval(state.store.versionTimer); state.store.versionTimer = null; }
  }
  /**
   * 버전 페이지가 열려 있는 동안 도는 5초 폴링(§3.2 · §15). 부르는 것은
   * `GET …/release/versions/<id>` **하나뿐**이고 그 라우트는 하위 프로세스를 돌리지 않는다 —
   * 드라이버와 소개 자료는 이 화면에서 절대 폴링하지 않는다(열어 둔 브라우저 하나가 빌드
   * 머신에서 5초마다 프로세스 셋을 돌리게 하지 않으려고 서버가 그렇게 나뉘었다).
   * «만드는 중» 이 끝나도 계속 돈다: 상태(진행 중 · 제출됨)와 **다른 브라우저의 편집**(E8)이
   * 이 폴링으로만 온다. 화면을 떠나면 멈춘다.
   */
  /**
   * 목록 화면의 짧은 폴링 — «만드는 중» 이나 «지우는 중» 인 드래프트가 있을 때만 5초에 한 번
   * `GET …/release/versions` 를 다시 받는다. 그 잡들은 보통 2~3초에 끝나는데 30초 새로고침만
   * 믿으면 누른 사람은 그 사이 «편집 중 · 열기 · 버리기» 를 보고 아무 일도 안 일어난 줄
   * 안다(C 단계 격리 검증 2). 이 라우트는 하위 프로세스를 돌리지 않는다(§14-1).
   */
  function stopDraftPoll() {
    if (state.store.draftTimer) { clearInterval(state.store.draftTimer); state.store.draftTimer = null; }
  }
  function draftsBusy() {
    var doc = state.store.versions;
    return (doc && Array.isArray(doc.drafts) ? doc.drafts : []).some(function (r) {
      return !!r && (r.state === "creating" || versionRowModel(r, L(), now()).deleting);
    });
  }
  function syncDraftPoll() {
    var want = state.view === "store" && state.store.screen === "store"
      && state.store.sub === "versions" && draftsBusy();
    if (!want) { stopDraftPoll(); return; }
    if (state.store.draftTimer) return;
    state.store.draftTimer = setInterval(function () {
      if (state.view !== "store" || state.store.sub !== "versions" || state.store.screen !== "store") return;
      if (document.hidden || state.conn.mode === "paused") return;
      loadVersions().then(function () { renderStore(); syncDraftPoll(); });
    }, VERSION_POLL_MS);
  }
  function syncVersionPoll() {
    syncDriverPoll();   // 시트의 단계와 색은 드라이버가 말한다 — 그것만 15초로 따로 받는다
    syncDraftPoll();    // 목록에서 만들거나 지우는 중인 드래프트가 있으면 그 화면도 5초로 따라간다
    var want = state.view === "store" && state.store.screen === "store"
      && state.store.sub === "version" && state.store.versionId != null;
    if (!want) { stopVersionPoll(); return; }
    if (state.store.versionTimer) return;
    state.store.versionTimer = setInterval(function () {
      if (state.view !== "store" || state.store.sub !== "version" || state.store.screen !== "store") return;
      if (document.hidden || state.conn.mode === "paused") return;
      loadVersion().then(function () { renderStore(); syncVersionPoll(); });
    }, VERSION_POLL_MS);
  }
  /** 관문에 막혀 설정 화면으로 보냈던 주소로 돌아간다(E22). 한 번 쓰면 잊는다. */
  function goBackToGateReturn() {
    var target = state.store.gateReturn;
    state.store.gateReturn = null;
    if (!target || location.hash === target) { renderNav(); renderStore(); syncVersionPoll(); return; }
    location.hash = target;
  }
  function loadGithub() {
    var repo = state.store.repo, api = storeApi();
    if (!repo || typeof api.github !== "function") { state.store.github = null; state.store.githubStatus = 404; return Promise.resolve(); }
    return api.github(repo).then(function (res) {
      if (state.store.repo !== repo) return;
      state.store.githubStatus = res.status;
      state.store.github = res.ok && res.body && typeof res.body === "object" ? res.body : null;
    }).catch(function () { state.store.github = null; state.store.githubStatus = 0; });
  }

  // ── Store 행 (항목 6 · 42) ──
  function storeRowHtml(model) {
    var canPlan = !!state.token && !state.store.busy;
    var presets = currentProfile().presets || {};
    // 이 빌드에 `…/release` 가 없거나(404) plan 프리셋이 없으면 누를 것도 없다
    var btn = "";
    if (state.store.releaseStatus !== 404 && presets.plan) {
      var dis = (canPlan ? "" : " disabled") + (state.token ? "" : ' title="' + esc(tr("store.gate.no_token")) + '"');
      btn = '<button type="button" class="btn" data-plan-refresh' + dis + ">" + esc(tr(state.store.busy === "plan" ? "store.refreshing_plan" : "store.refresh_plan")) + "</button>";
      // 버전을 아는 회차에는 한 번에 가고, 다른 버전은 대화상자로 — 모르면 Refresh 자체가 대화상자다
      if (knownPlanVersion()) btn += '<button type="button" class="btn link" data-plan-other' + dis + ">" + esc(tr("store.plan_other")) + "</button>";
    }
    var b = model.body, body;
    if (!b) body = '<p class="sub">' + esc(model.head.join(" · ")) + "</p>";
    else {
      var list = function (items) { return items.length ? "<ul class=\"plain\">" + items.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : esc(tr("store.body.none")); };
      body = '<dl class="kv">'
        + "<dt>" + esc(tr("store.body.builds")) + "</dt><dd>" + list(b.builds) + "</dd>"
        + "<dt>" + esc(tr("store.body.tracks")) + "</dt><dd>" + list(b.tracks) + "</dd>"
        + "<dt>" + esc(tr("store.body.next")) + "</dt><dd class=\"mono\">" + esc(b.next) + (b.firstRelease ? ' <span class="chip">' + esc(tr("store.head.first_release")) + "</span>" : "") + "</dd>"
        + "<dt>" + esc(tr("store.body.measured")) + "</dt><dd>" + esc(b.measured) + (isNum(b.jobId) ? ' <span class="mono sub">#' + b.jobId + "</span>" : "") + "</dd>"
        + '<dt class="' + (b.blockers.length ? "bad" : "") + '">' + esc(tr("store.body.blockers")) + '</dt><dd class="' + (b.blockers.length ? "bad" : "") + '">' + list(b.blockers) + "</dd>"
        + "<dt>" + esc(tr("store.body.warnings")) + "</dt><dd>" + list(b.warnings) + "</dd>"
        + "</dl>";
    }
    // 거절(400 `build_name is required` · 409 …)은 머리에 빨갛게 — 접힌 행의 본문에 숨으면 아무 반응이 없는 것처럼 보인다
    var heads = model.error ? model.head.slice(0, -1) : model.head;
    var head = esc(heads.join(" · ")) + (model.error ? (heads.length ? " · " : "") + '<span class="bad" data-plan-error>' + esc(model.error) + "</span>" : "");
    return srowHtml("store", model.state, head, btn, body, !!model.error);
  }
  // ── Build·upload 행 (항목 5 · 46~49) ──
  function jobCardHtml(item) {
    var row = item.row;
    if (!row) return "";
    var live = !state.skewUnknown && (row.state === "running" || row.state === "cancelling");
    var h = "";
    if (row.state === "running" || row.state === "cancelling") h += progressBarHtml(row, L(), live);
    var prog = row.progress;
    if (prog) {
      var ph = progressHeadHtml(prog, L(), live);
      if (ph) h += '<div class="sub step-now">' + ph + "</div>";
      h += unitsGridHtml(prog, L());
    }
    return h ? '<div class="jobcard">' + h + "</div>" : "";
  }
  function buildLayersHtml(layers) {
    if (!layers || !layers.items.length) return "";
    var h = "";
    if (layers.bar) {
      var p = layers.bar.progress, pct = p && isNum(p.pct) ? p.pct : null;
      var basis = p ? p.basis : "none", cond = p ? p.condition : "normal";
      h += '<div class="longbar" role="group" aria-label="' + esc(tr("build.layers_aria")) + '">'
        + '<div class="lb-head"><b>' + esc(layers.bar.preset) + "</b> · " + esc(layers.bar.head) + '<span class="spacer"></span><span class="sub">' + esc(layers.bar.basis) + (layers.bar.finishes ? " · " + esc(layers.bar.finishes) : "") + "</span></div>"
        + '<div class="pbar wide" data-basis="' + esc(basis) + '" data-cond="' + esc(cond) + '" role="progressbar" aria-valuemin="0" aria-valuemax="100"' + (pct != null ? ' aria-valuenow="' + pct + '"' : "") + ' aria-valuetext="' + esc(layers.bar.head + " · " + layers.bar.basis) + '"><i data-fill="' + (pct != null ? pct : 0) + '" style="width:' + (pct != null ? pct : 0) + '%"></i></div>'
        + "</div>";
    }
    if (layers.now) h += '<p class="now-line"><span class="g" aria-hidden="true">▶</span>' + esc(layers.now) + "</p>";
    h += '<details class="checklist" open><summary class="sub">' + esc(tr("build.checklist", { n: layers.items.length })) + "</summary><ol class=\"steps-list\">";
    layers.items.forEach(function (it) {
      h += '<li class="ci ' + esc(it.tone) + '" data-job="' + it.id + '"><span class="g" aria-hidden="true">' + esc(it.mark) + "</span><span>" + esc(it.text) + "</span>" + (it.running ? jobCardHtml(it) : "") + "</li>";
    });
    h += "</ol>";
    if (layers.older && layers.older.length) {
      h += '<details class="older"><summary class="sub">' + esc(tr("build.older", { n: layers.older.length })) + '</summary><ol class="steps-list">';
      layers.older.forEach(function (it) { h += '<li class="ci ' + esc(it.tone) + '" data-job="' + it.id + '"><span class="g" aria-hidden="true">' + esc(it.mark) + "</span><span>" + esc(it.text) + "</span></li>"; });
      h += "</ol></details>";
    }
    h += "</details>";
    return h;
  }
  function buildRowHtml(model) {
    var dm = driverModel(), acts = driverActions(dm, driverCtx());
    var over = driverRow(dm);   // 도는 중 · 실패 · N 대기 · 모름 · 드리프트면 행의 색과 머리는 드라이버의 것
    var st = over ? over.state : model.state, head = over ? over.head : model.head;
    var busy = state.store.busy, reasonOf = function (a) { return a.reasons.map(function (k) { return tr("driver.reason." + k); }).join(" · "); };
    var extra = "";
    if (acts.abort.show) extra += '<button type="button" class="btn danger" data-driver-abort' + (acts.abort.enabled ? "" : ' disabled title="' + esc(reasonOf(acts.abort)) + '"') + ">" + esc(tr(busy === "abort" ? "driver.aborting" : "driver.abort")) + "</button>";
    if (acts.retry.show) extra += '<button type="button" class="btn" data-driver-retry' + (acts.retry.enabled ? "" : ' disabled title="' + esc(reasonOf(acts.retry)) + '"') + ">" + esc(tr(busy === "retry" ? "driver.retrying" : "driver.retry")) + "</button>";
    var body = "";
    // 스테퍼는 잡 카드 위에(항목 26). 릴리스 상태도 드라이버도 없는 빌드면 행의 «not available» 한 줄뿐이다.
    if (state.store.driverStatus !== 404 || (state.store.releaseStatus !== 404 && releaseDoc())) body += driverHtml(dm, acts);
    if (model.layers && model.layers.items.length) body += buildLayersHtml(model.layers);
    else if (!over) body += '<p class="sub">' + esc(model.head.join(" · ")) + "</p>";
    if (model.note === "lost") body = '<p class="bad">' + esc(model.head.join(" · ")) + "</p>" + body;
    return srowHtml("build", st, esc(head.join(" · ")), extra, body);
  }
  // ── 릴리스 드라이버 (항목 26 · 28 · 37~39) ──
  function driverModel() { return stepperModel(state.store.driver, { status: state.store.driverStatus, lang: L(), nowMs: now(), tzName: tz() }); }
  function driverFormVersion() {
    var f = state.store.driverForm;
    if (f.version != null) return f.version;
    var plan = (releaseDoc() || {}).plan;   // 아직 안 쳤으면 플랜의 버전이 기본값
    return plan && plan.build_name != null ? String(plan.build_name) : "";
  }
  function nMode() { return nModeOf(state.store.nMode, currentProfile()); }
  /** «자동 · 직접 입력» 토글 — 심사 패널과 릴리스 시작 폼이 같은 상태를 보여 준다. */
  function nModeToggleHtml() {
    var m = nMode();
    return '<div class="seg" role="group" aria-label="' + esc(tr("n.mode.label")) + '" data-n-mode-toggle>'
      + '<span class="seg-l">' + esc(tr("n.mode.label")) + "</span>"
      + '<button type="button" class="seg-b' + (m === "auto" ? " on" : "") + '" data-n-mode="auto" aria-pressed="' + (m === "auto") + '">' + esc(tr("n.mode.auto")) + "</button>"
      + '<button type="button" class="seg-b' + (m === "typed" ? " on" : "") + '" data-n-mode="typed" aria-pressed="' + (m === "typed") + '">' + esc(tr("n.mode.typed")) + "</button></div>";
  }
  function driverCtx() {
    return { token: state.token, admin: state.admin, busy: state.store.busy, version: driverFormVersion(), typedN: state.store.driverN, nMode: state.store.nMode, profile: currentProfile(),
      release: releaseDoc(), releaseStatus: state.store.releaseStatus, uploadPreset: !!(currentProfile().presets || {}).upload };
  }
  function driverReasons(a) { return a.reasons.map(function (k) { return tr("driver.reason." + k); }).join(" · "); }
  var DRIVER_HEAD_GLYPH = { running: "▶", ok: "✓", bad: "✗", human: "▶", lost: "?", warn: "!", na: "·" };
  function driverHtml(m, acts) {
    var busy = state.store.busy;
    var h = '<div class="driver" data-driver data-driver-stage="' + esc(m.stage || "") + '" data-driver-exit="' + (m.exit == null ? "" : m.exit) + '" role="group" aria-label="' + esc(tr("driver.aria")) + '">';
    h += '<p class="drv-head ' + esc(m.tone) + '" data-driver-head><span class="g" aria-hidden="true">' + (DRIVER_HEAD_GLYPH[m.tone] || "·") + "</span><span>" + esc(m.head) + "</span></p>";
    if (m.statusError) h += '<p class="sub bad">' + esc(tr("driver.status_error", { detail: m.statusError.slice(0, 80) })) + "</p>";
    if (!m.available || !m.configured) return h + "</div>";
    if (!m.idle) {
      h += '<ol class="stepper">';
      m.items.forEach(function (it) {
        h += '<li class="st ' + esc(it.state) + '" data-stage="' + it.id + '"><span class="g" aria-hidden="true">' + esc(it.glyph) + "</span><span><b>" + it.id + "</b> " + esc(it.label) + '</span><span class="sub">' + esc(it.text) + "</span></li>";
      });
      h += "</ol>";
    }
    // S2(항목 28): 대화상자는 회차마다 한 번 저절로 뜨고, 이 버튼이 다시 연다
    if (acts.confirm.show) h += '<div class="actions"><button type="button" class="btn primary" data-driver-confirm-open>' + esc(tr("driver.confirm")) + "</button>"
      + nModeToggleHtml() + '<span class="sub" data-driver-n-hint>' + esc(tr(nMode() === "auto" ? "driver.n_auto_hint" : "driver.n_typed_hint")) + "</span></div>";
    if (acts.start.show) {
      var f = state.store.driverForm;
      h += '<form class="drv-start" data-driver-start>'
        + '<label>' + esc(tr("driver.version")) + '<input type="text" data-driver-version autocomplete="off" spellcheck="false" placeholder="' + esc(tr("driver.version_hint")) + '" value="' + esc(driverFormVersion()) + '"></label>'
        + '<label>' + esc(tr("driver.track")) + '<input type="text" data-driver-track autocomplete="off" spellcheck="false" list="driver-tracks" placeholder="' + esc(tr("driver.track_default")) + '" value="' + esc(f.track) + '"></label>'
        + '<datalist id="driver-tracks"><option value="internal"></option><option value="alpha"></option><option value="beta"></option><option value="production"></option></datalist>'
        + '<label class="ck"><input type="checkbox" data-driver-dry' + (f.dryRun ? " checked" : "") + "> " + esc(tr("driver.dry_run")) + "</label>"
        + nModeToggleHtml() + '<span class="sub" data-driver-n-hint>' + esc(tr(nMode() === "auto" ? "driver.n_auto_hint" : "driver.n_typed_hint")) + "</span>"
        + '<button type="submit" class="btn primary" data-driver-start-go disabled>' + esc(tr(busy === "start" ? "driver.starting" : "driver.start")) + "</button>"
        + '<span class="sub" data-driver-reason></span></form>';
    }
    // 예행(업로드 없음). mode=upload 버튼은 일부러 없다 — 드라이버의 S7 이 업로드 길이다.
    if (acts.rehearsal.show) {
      h += '<div class="actions"><button type="button" class="btn" data-upload-rehearsal' + (acts.rehearsal.enabled ? "" : ' disabled title="' + esc(driverReasons(acts.rehearsal)) + '"') + ">" + esc(tr(busy === "rehearsal" ? "driver.rehearsing" : "driver.rehearsal")) + "</button>"
        + '<span class="sub">' + esc(acts.rehearsal.enabled ? tr("driver.rehearsal_hint") : driverReasons(acts.rehearsal)) + "</span></div>";
    }
    if (state.store.driverError) h += '<p class="bad" data-driver-error>' + esc(state.store.driverError) + "</p>";
    if (m.log.length) h += '<details class="drv-log"><summary class="sub">' + esc(tr("driver.log", { n: m.log.length })) + '</summary><pre class="notes">' + esc(m.log.join("\n")) + "</pre></details>";
    return h + "</div>";
  }
  /** Start 버튼과 이유만 제자리에서 — 타이핑마다 본체를 다시 그리지 않는다. */
  function renderDriverState() {
    var go = $("#store [data-driver-start-go]");
    if (!go) return;
    var a = driverActions(driverModel(), driverCtx()).start;
    go.disabled = !a.enabled;
    go.title = a.enabled ? "" : driverReasons(a);
    var reason = $("#store [data-driver-reason]");
    if (reason) reason.textContent = a.enabled ? "" : driverReasons(a);
  }
  function githubCardHtml(g) {
    if (g.state !== "ok") return '<p class="sub ghcard" data-github="' + esc(g.state) + '">' + esc(g.text) + "</p>";
    var h = '<div class="ghcard" data-github="ok"><div class="two"><div><div class="mini-h">' + esc(tr("github.log", { ref: g.ref, n: g.log.length })) + "</div>";
    h += g.log.length ? '<table class="gh"><tbody>' + g.log.map(function (c) {
      return '<tr><td class="sha">' + esc(c.sha) + "</td><td>" + esc(c.subject) + '</td><td class="sub">' + esc(c.author + " · " + c.at) + "</td></tr>";
    }).join("") + "</tbody></table>" : '<span class="sub">' + esc(tr("github.none")) + "</span>";
    h += '</div><div><div class="mini-h">' + esc(tr("github.tags")) + "</div>";
    h += g.tags.length ? '<ul class="plain mono">' + g.tags.map(function (t) {
      return "<li>" + esc(t.name) + (t.latest ? ' <span class="chip latest">' + esc(tr("github.latest")) + "</span>" : "") + ' <span class="sub">' + esc(t.at) + "</span></li>";
    }).join("") + "</ul>" : '<span class="sub">' + esc(tr("github.none")) + "</span>";
    h += '<div class="mini-h">' + esc(tr("github.prs")) + "</div>";
    h += g.prsText ? '<span class="sub" data-github-prs>' + esc(g.prsText) + "</span>" : '<ul class="plain">' + g.prs.map(function (p) { return "<li>#" + esc(String(p.number)) + " " + esc(p.title) + (p.state ? ' <span class="sub">' + esc(p.state) + "</span>" : "") + "</li>"; }).join("") + "</ul>";
    return h + "</div></div></div>";
  }

  // ── 심사 패널 본체 (항목 7~24 · 28 · 36 · 40) ──
  function counterHtml(c) {
    if (!c) return "";
    return '<span class="counter ' + c.tone + '" title="' + esc(c.text) + '">' + esc(c.text) + "</span>";
  }
  function fieldRowHtml(label, value, counter, changed, extra) {
    var v = value == null ? '<span class="dash">' + DASH + (extra ? " " + esc(extra) : "") + "</span>" : '<span class="val">' + esc(value) + "</span>";
    return '<div class="fld"><span class="fl">' + esc(label) + (changed ? ' <span class="chip changed">' + esc(tr("review.changed")) + "</span>" : "") + "</span>" + v + counterHtml(counter) + "</div>";
  }
  function screenshotsHtml(list, groupsOther) {
    var repo = state.store.repo, api = storeApi();
    var h = '<div class="shots">';
    if (!list.length) h += '<span class="sub">' + esc(tr("review.screenshots.none")) + "</span>";
    list.forEach(function (s) {
      var src = typeof api.listingFileUrl === "function" ? api.listingFileUrl(repo, s.path) : "";
      var dim = isNum(s.width) && isNum(s.height) ? s.width + "×" + s.height : (isNum(s.bytes) ? fmtBytes(s.bytes) : "");
      h += '<figure class="shot"><img loading="lazy" alt="' + esc(s.path) + '" src="' + esc(src) + '"><figcaption class="sub">' + esc(s.path.split("/").pop()) + (dim ? " · " + esc(dim) : "") + "</figcaption></figure>";
    });
    h += "</div>";
    if (groupsOther && groupsOther.length) h += '<p class="sub">' + esc(tr("review.screenshots.unsorted", { n: groupsOther.length })) + "</p>";
    return h;
  }
  function verdictPillHtml(word, judgedAge) {
    var tone = verdictTone(word);
    var text = tone === "none" ? tr("review.verdict.not_judged") : String(word);
    var glyph = { ok: "✓", na: "·", running: "▶", stale: "⏱", bad: "✗", none: "·" }[tone];
    return '<span class="pill v-' + tone + '" data-verdict="' + esc(tone === "none" ? "" : String(word)) + '"><span class="g" aria-hidden="true">' + glyph + "</span>" + esc(text) + "</span>"
      + (tone !== "none" && judgedAge != null ? ' <span class="sub">' + esc(tr("review.judged", { age: fmtAgo(judgedAge, L()) })) + "</span>" : "");
  }
  /** 두 절이 같은 그룹 순서·같은 줄 위치(항목 15): 그래픽 → 문구 → 릴리스 노트 → 빌드/릴리스 → 심사 정보/정책. */
  function storeSectionHtml(platform, ctx) {
    var ios = platform === "ios", lang = L();
    var fields = ctx.fields, f = fields[platform], shots = ctx.shots;
    var listingState = ctx.listingState;
    var h = '<section class="ssec" data-platform="' + platform + '" aria-label="' + esc(tr(ios ? "review.section.ios" : "review.section.android")) + '">'
      + '<h3><span>' + esc(tr(ios ? "review.section.ios" : "review.section.android")) + "</span>" + verdictPillHtml(ctx.verdicts[platform], ctx.judgedAge) + "</h3>";
    var group = function (key, inner) { return '<div class="grp" data-group="' + key + '"><h4>' + esc(tr("review.group." + key)) + "</h4>" + inner + "</div>"; };
    // 1 그래픽
    var shotList = shots[platform];
    var shotsInner = listingState ? '<p class="sub">' + esc(listingState) + "</p>"
      : '<p class="sub">' + esc(tr("review.screenshots.count", { n: shotList.length })) + (fields.screenshotsChanged ? ' <span class="chip changed">' + esc(tr("review.changed")) + "</span>" : "") + "</p>" + screenshotsHtml(shotList, shots.other);
    h += group(ios ? "screenshots" : "graphics", shotsInner);
    // 2 문구 — 같은 줄 수. Play 에 없는 필드는 — 와 이유(항목 15)
    var rows = ios ? IOS_FIELDS : PLAY_FIELDS;
    var inner = "";
    rows.forEach(function (key) {
      var absent = ios ? !!PLAY_ONLY[key] : !!IOS_ONLY[key];
      if (absent) inner += fieldRowHtml(tr("review.field." + key), null, null, false, tr(ios ? "review.no_field.ios" : "review.no_field.play"));
      else {
        var v = Object.prototype.hasOwnProperty.call(f, key) ? f[key] : null;
        inner += fieldRowHtml(tr("review.field." + key), v, v != null ? fieldCounter(v, FIELD_LIMITS[key]) : null, !!fields.changed[key]);
      }
    });
    if (listingState) inner += '<p class="sub">' + esc(listingState) + "</p>";
    h += group(ios ? "version_info" : "store_listing", inner);
    // 3 릴리스 노트 — 같은 원문, 상한만 다르다(항목 12·18)
    var strip = ctx.strip, notes = strip.notesText;
    var notesInner;
    if (notes == null) notesInner = '<p class="bad">' + esc(tr("review.notes.missing")) + "</p>";
    else {
      var nc = fieldCounter(notes, NOTES_LIMIT[platform]);
      notesInner = '<pre class="notes">' + esc(notes) + "</pre><p class=\"sub\">" + (strip.notesPath ? esc(tr("review.notes.from", { path: strip.notesPath })) + " · " : "") + counterHtml(nc) + " · " + esc(tr("review.notes.same")) + "</p>";
    }
    h += group(ios ? "whats_new" : "release_notes", notesInner);
    h += group(ios ? "build" : "release", buildGroupInnerHtml(platform, ctx));
    h += group(ios ? "review_info" : "app_content", infoGroupInnerHtml(platform, ctx));
    return h + "</section>";
  }
  /** 그룹 4 «빌드 · 출시 설정» — 읽기 전용이고 고를 것이 없다(항목 13·19). 심사 패널과 버전
      페이지가 **같은 코드를 쓴다**(§3.2: 빌드·출시 설정은 버전 페이지에서도 지금 문구 그대로). */
  function buildGroupInnerHtml(platform, ctx) {
    var ios = platform === "ios", strip = ctx.strip, observed = (ctx.observed || {})[platform];
    var inner = '<div class="fld"><span class="fl">' + esc(tr(ios ? "review.group.build" : "review.group.release")) + '</span><span class="val">'
      + esc(ios ? tr("review.build.line", { version: strip.version, build: strip.build, state: observed != null ? String(observed) : DASH }) : tr("review.release_line", { build: strip.build, version: strip.version }) + (observed != null ? " · " + tr("review.observed", { value: String(observed) }) : ""))
      + " · " + esc(strip.buildNote) + "</span></div>";
    if (ios) {
      inner += '<div class="fld"><span class="fl">' + esc(tr("review.version_release")) + '</span><span class="val">' + esc(tr("review.manual_release")) + ' <span class="chip">' + esc(tr("review.fixed_by_repo")) + "</span>"
        + (ctx.autoRelease != null ? ' <span class="' + (ctx.autoRelease ? "bad" : "sub") + '">' + esc(tr("review.observed", { value: "automatic_release: " + ctx.autoRelease })) + "</span>" : "") + "</span></div>"
        + '<div class="fld"><span class="fl">' + esc(tr("review.phased")) + '</span><span class="val">' + esc(tr(state.store.review.phased ? "review.on" : "review.off")) + "</span></div>";
    } else {
      inner += '<div class="fld"><span class="fl">' + esc(tr("review.rollout")) + '</span><span class="val"><span class="chip">' + esc(tr("review.rollout_fixed")) + "</span></span></div>"
        + '<div class="fld"><span class="fl">' + esc(tr("review.managed")) + '</span><span class="val"><span class="pill na" data-managed-pill><span class="g" aria-hidden="true">?</span>' + esc(tr("review.managed_unknown")) + "</span> " + esc(tr("review.managed_hint")) + "</span></div>"
        + '<div class="fld"><span class="fl">' + esc(tr("review.group.review_info")) + '</span><span class="val">' + esc(tr("review.send_for_review")) + "</span></div>";
    }
    return inner;
  }
  /** 그룹 5 «앱 심사 정보 / 앱 콘텐츠» — 있음/없음만, 값은 없다(항목 14·20). 두 화면이 같이 쓴다. */
  function infoGroupInnerHtml(platform, ctx) {
    if (platform !== "ios") {
      return fieldRowHtml(tr("review.data_safety"), null, null, false, tr("review.console_only"))
        + fieldRowHtml(tr("review.content_rating"), null, null, false, tr("review.console_only"))
        + fieldRowHtml(tr("review.notes_label"), null, null, false, "");
    }
    var ri = ctx.reviewInfo;
    if (!ri) return '<p class="sub">' + DASH + "</p>";
    var inner = ri.files.map(function (f) {
      return '<div class="fld"><span class="fl">' + esc(f.name) + "</span>" + presentPill(f.present, tr(f.present ? "secrets.present" : "secrets.missing")) + "</div>";
    }).join("") + '<p class="sub">' + esc(tr("review.values_never_shown")) + "</p>";
    if (!ri.complete) inner += '<p class="bad">' + esc(tr("review.review_info_missing")) + ' <button type="button" class="btn" data-goto-settings>' + esc(tr("row.settings")) + "</button></p>";
    return inner;
  }
  function reviewChoicesCtx() {
    var rv = state.store.review;
    return { release: releaseDoc(), profile: currentProfile(), typedN: rv.typedN, nMode: state.store.nMode, platforms: rv.platforms, managed: rv.managed,
      listingFull: rv.listingFull, phased: rv.phased, admin: state.admin, token: state.token, busy: state.store.busy };
  }
  function targetsText(platforms) {
    var p = platformParam(platforms);
    return tr("review.targets." + (p || "both"));
  }
  function reviewPanelHtml(pill, rowsState) {
    var r = releaseDoc(), lang = L(), n = now(), profile = currentProfile();
    var rv = state.store.review;
    var remembered = loadRowMemory()[state.store.repo + "/review"];
    var open = panelOpen(pill, remembered, !!state.store.reviewError);
    var strip = versionStrip(r, state.store.listing, profile, lang, tz(), n);
    var head = tr("review.head", { version: strip.version, build: strip.build, targets: tr("review.targets.both") });
    var pillHtml = '<span class="pill p-' + pill.tone + '" data-panel-pill="' + esc(pill.code) + '"><span class="g" aria-hidden="true">' + ({ ok: "✓", bad: "✗", stale: "⏱", na: "·", none: "○" }[pill.tone] || "·") + "</span>" + esc(tr("review.pill." + pill.code)) + "</span>";
    var h = '<details class="srow review" id="review-panel" data-row="review" data-state="' + esc(pill.tone === "none" ? "ok" : pill.tone) + '"' + (open ? " open" : "") + ">"
      + '<summary><span class="t">' + esc(tr("review.title")) + '</span><span class="n">' + esc(head)
      + (state.store.reviewError ? ' · <span class="bad" data-review-error-head>' + esc(state.store.reviewError) + "</span>" : "") + "</span>" + pillHtml + "</summary><div class=\"srow-body\">";
    if (state.store.releaseStatus === 404 || !r) {
      h += '<p class="sub">' + esc(tr("review.na_body")) + "</p>";
      return h + '<p class="sub policy">' + esc(tr("review.policy")) + "</p></div></details>";
    }
    // 결과(항목 40) — 이번 회차의 것이면 맨 위에
    if (resultIsCurrent(r)) {
      var rm = resultModel(r.review.result, lang);
      h += '<div class="result ' + rm.tone + '" data-result="' + esc(rm.status) + '"><b>' + esc(tr("review.result.title", { version: strip.version, build: strip.build, status: rm.status })) + "</b>"
        + "<ul class=\"plain\">" + rm.platforms.map(function (p) { return "<li>" + esc(p.text) + "</li>"; }).join("") + "</ul>"
        + (rm.extras.length ? '<p class="sub">' + esc(rm.extras.join(" · ")) + "</p>" : "") + "</div>";
    }
    // 버전 띠(항목 8)
    h += '<dl class="kv strip">'
      + "<dt>" + esc(tr("review.strip.version")) + "</dt><dd class=\"mono\">" + esc(strip.version) + "</dd>"
      + "<dt>" + esc(tr("review.strip.build")) + "</dt><dd class=\"mono\">" + esc(String(strip.build)) + ' <span class="sub">' + esc(strip.buildNote) + "</span></dd>"
      + "<dt>" + esc(tr("review.strip.listing")) + "</dt><dd>" + esc(strip.listing) + "</dd>"
      + "<dt>" + esc(tr("review.strip.notes")) + "</dt><dd>" + (strip.notesPath ? '<span class="mono">' + esc(strip.notesPath) + "</span> " + counterHtml(strip.notesCounter) : '<span class="bad">' + esc(tr("review.notes.missing")) + "</span>") + "</dd>"
      + "<dt>" + esc(tr("review.strip.payload")) + "</dt><dd>" + esc(tr(rv.listingFull ? "review.payload.full" : "review.payload.notes_only")) + "</dd>"
      + "</dl>";
    // 두 스토어 절 — 같은 그룹 순서
    var listing = state.store.listing, listingState = null;
    if (state.store.listingStatus === 404) listingState = tr("review.listing.na");
    else if (listing && listing.configured === false) listingState = tr("review.listing.not_configured");
    else if (!listing) listingState = tr("review.listing.loading");
    var rp = r.review && r.review.plan, rpDoc = planEntryDoc(rp) || {};
    var ctx = {
      fields: listingFields(listing), shots: screenshotGroups(listing), listingState: listingState, strip: strip,
      verdicts: { ios: rpDoc.ios != null ? rpDoc.ios : null, android: rpDoc.android != null ? rpDoc.android : null },
      judgedAge: rp && isNum(rp.age_seconds) ? rp.age_seconds : null,
      observed: rpDoc.observed && typeof rpDoc.observed === "object" ? rpDoc.observed : {},
      autoRelease: rpDoc.observed && typeof rpDoc.observed.auto_release === "boolean" ? rpDoc.observed.auto_release : null,
      reviewInfo: reviewInfoModel(secretsItems())
    };
    h += '<div class="stores">' + storeSectionHtml("ios", ctx) + storeSectionHtml("android", ctx) + "</div>";
    if (listing && Array.isArray(listing.errors) && listing.errors.length) h += '<p class="sub bad">' + esc(tr("review.listing.errors", { n: listing.errors.length })) + ": " + esc(listing.errors.join(" · ").slice(0, 200)) + "</p>";
    if (ctx.fields.other.length) h += '<details class="other-lines"><summary class="sub">' + esc(tr("review.listing.other")) + "</summary><pre class=\"notes\">" + esc(ctx.fields.other.join("\n")) + "</pre></details>";
    // 스토어와 달라진 것(항목 21) + validate 결과
    var diff = listing && Array.isArray(listing.diff) ? listing.diff : [];
    h += '<div class="changes"><b>' + esc(tr("review.changes")) + "</b> " + (diff.length ? '<pre class="notes">' + esc(diff.join("\n")) + "</pre>" : '<span class="sub">' + esc(tr("review.changes_none")) + "</span>") + "</div>";
    if (state.store.validate) {
      var v = state.store.validate;
      h += '<div class="validate ' + (v.ok ? "ok" : "bad") + '" data-validate><b>' + esc(v.ok ? tr("review.validate_ok") : tr("review.validate_failed", { code: isNum(v.exit) ? v.exit : DASH })) + "</b>"
        + (v.lines && v.lines.length ? '<pre class="notes">' + esc(v.lines.join("\n")) + "</pre>" : "") + "</div>";
    }
    // 제출 조건(체크박스 · 관리형 게시 · 빌드 번호)과 «심사 제출» 은 **바텀시트**로 갔다(§4.2 ·
    // AC-D6). 이 패널은 상태 화면의 읽기 전용 배치로 남는다 — 진단용 두 작업만 여기서 돈다.
    var busy = state.store.busy;
    var canJob = !!state.token && !busy;
    h += '<div class="actions">'
      + '<button type="button" class="btn" data-validate-listing' + (canJob && listing && listing.configured !== false && state.store.listingStatus !== 404 ? "" : " disabled") + ">" + esc(tr(busy === "validate" ? "review.validating" : "review.validate")) + "</button>"
      + '<button type="button" class="btn" data-plan-review' + (canJob ? "" : " disabled") + ">" + esc(tr(busy === "review-plan" ? "review.planning" : "review.plan")) + "</button>"
      + (state.store.reviewError ? '<span class="bad" data-review-error>' + esc(state.store.reviewError) + "</span>" : "")
      + "</div>"
      + '<p class="sub">' + esc(tr("review.submit_hint", { n: isNum(profile.plan_max_age_minutes) ? profile.plan_max_age_minutes : 30 })) + "</p>"
      + (rp ? '<p class="sub" data-review-plan-line>' + esc(tr("review.plan_running", { id: rp.job_id, state: stateWord(rp.state, lang) })) + "</p>" : "");
    h += '<p class="sub policy">' + esc(tr("review.policy")) + "</p></div></details>";
    return h;
  }
  function unsafeBannerHtml() {
    var b = bannerDecision(releaseDoc());
    if (!b.show) return "";
    return '<div class="banner bad unsafe" role="alert" data-unsafe-banner="' + esc(b.reason) + '"><span class="g" aria-hidden="true">✗</span><b>' + esc(tr(b.reason === "auto_release" ? "review.banner.auto_release" : "review.banner.unsafe", { version: b.version })) + "</b></div>";
  }


  // ── 행동 ──
  function verifyAll() {
    if (!state.token || state.store.verifying) return;
    var repo = state.store.repo;
    state.store.verifying = true; renderStore();
    storeApi().verify(repo, []).then(function (res) {
      state.store.verifying = false;
      if (res.ok) toast(tr("secrets.verify_done"));
      else { toast(tr("secrets.verify_call_failed", { detail: storeErrorDetail(res) })); if (res.status === 401 || res.status === 403) tokenRejected(); }
      return loadStore();
    }).catch(function () { state.store.verifying = false; toast(tr("secrets.verify_call_failed", { detail: "network" })); renderStore(); });
  }
  function fetchRemote() {
    if (!state.token || state.store.fetching) return;
    var repo = state.store.repo;
    state.store.fetching = true; renderStore();
    storeApi().fetchRemote(repo).then(function (res) {
      state.store.fetching = false;
      if (res.ok) {
        state.store.fetchError = null;
        if (res.body && state.store.doc) { if (res.body.mirror) state.store.doc.mirror = res.body.mirror; if (res.body.branches) state.store.doc.branches = res.body.branches; }
        toast(tr("source.fetch_done"));
      } else {
        state.store.fetchError = storeErrorDetail(res);
        if (res.status === 401 || res.status === 403) tokenRejected();
      }
      return loadStore();
    }).catch(function () { state.store.fetching = false; state.store.fetchError = "network"; renderStore(); });
  }
  function putSecretFile(secret, fileName, files) {
    var item = secretsItems().filter(function (it) { return it.name === secret; })[0];
    var verdict = dropAccept(item, files, state.admin);
    if (!verdict.ok) { toast(tr(verdict.reason, verdict.args)); return; }
    var file = files[0], repo = state.store.repo, label = fileName ? secret + "/" + fileName : secret;
    toast(tr("secrets.saving"));
    file.arrayBuffer().then(function (buf) {
      return storeApi().putSecret(repo, secret, buf, "application/octet-stream", fileName);
    }).then(function (res) {
      if (res.ok) toast(tr("secrets.saved", { name: label }));
      else { toast(tr("secrets.save_failed", { name: label, detail: storeErrorDetail(res) })); if (res.status === 401 || res.status === 403) tokenRejected(); }
      return loadStore();
    }).catch(function () { toast(tr("secrets.save_failed", { name: label, detail: "network" })); });
  }
  // 값 비밀 대화상자(항목 30). 입력칸은 닫힐 때마다 비운다 — 값은 보내는 순간 말고는 어디에도 없다.
  function openSecretDialog(name) {
    if (!state.admin) { toast(tr("secrets.reject.admin")); return; }
    var dlg = $("#secret-dialog"), input = $("#secret-input");
    if (!dlg) return;
    state.store.dialogSecret = name;
    $("[data-secret-title]").textContent = tr("secrets.dialog_title", { name: name });
    $("[data-secret-status]").textContent = "";
    input.value = "";
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    input.focus();
  }
  function submitSecretDialog() {
    var dlg = $("#secret-dialog"), input = $("#secret-input"), status = $("[data-secret-status]");
    var name = state.store.dialogSecret, value = input.value;
    if (!value || !value.trim()) { status.textContent = tr("secrets.empty_value"); return; }
    status.textContent = tr("secrets.saving");
    storeApi().putSecret(state.store.repo, name, value, "text/plain; charset=utf-8").then(function (res) {
      input.value = "";
      if (res.ok) { dlg.close(); toast(tr("secrets.saved", { name: name })); }
      else { status.textContent = tr("secrets.save_failed", { name: name, detail: storeErrorDetail(res) }); if (res.status === 401 || res.status === 403) tokenRejected(); }
      return loadStore();
    }).catch(function () { input.value = ""; status.textContent = tr("secrets.save_failed", { name: name, detail: "network" }); });
  }
  // ── 행동: plan · validate · review plan · submit (항목 23 · 28) ──
  function jobCall(kind, run, after) {
    if (!state.token || state.store.busy) return;
    var repo = state.store.repo;
    state.store.busy = kind; state.store.reviewError = null; state.store.reviewCode = null; renderStore();
    run(storeApi(), repo).then(function (res) {
      state.store.busy = null;
      if (res.ok) { after(res); }
      else {
        // 409 는 서버의 규칙이다 — 코드를 그대로 버튼 옆에 쓰고, 돌아가지 않는다. 시트의
        // «남은 것» 은 그 **코드**를 다시 쓴다(E11 — 프리셋이 listing_json 을 모른다).
        if (kind === "plan") state.store.planError = refusalText(res, L());
        else {
          state.store.reviewError = refusalText(res, L());
          var b = res.body && typeof res.body === "object" ? res.body : {};
          state.store.reviewCode = b.code || b.error_code || null;
        }
        if (res.status === 401 || res.status === 403) tokenRejected();
      }
      return loadStore({ listing: kind === "validate" });
    }).catch(function () { state.store.busy = null; state.store.reviewError = "network"; state.store.reviewCode = null; renderStore(); });
  }
  /** 이 회차의 build_name — 플랜 항목, 없으면 plan.json 의 것. 첫 사용(플랜 없음)엔 "". */
  function knownPlanVersion() {
    var r = releaseDoc(), plan = r && r.plan, doc = planEntryDoc(plan) || {};
    return plan && plan.build_name != null ? String(plan.build_name) : (doc.build_name != null ? String(doc.build_name) : "");
  }
  /**
   * «Refresh (release-plan)». 버전을 알면 한 번에 보내고, 모르면(첫 사용) 버전을 묻는 대화상자다 —
   * 서버는 빈 build_name 을 400 으로 거절하고, 그 오류는 접힌 회색 행 속에 숨어 아무 반응이 없어 보였다.
   */
  function refreshPlan(buildName) {
    var version = buildName != null ? buildName : knownPlanVersion();
    if (!version) { openPlanDialog(); return; }
    jobCall("plan", function (api, repo) { return api.plan(repo, { build_name: version, ref: currentProfile().default_branch || "main" }); }, function (res) {
      state.store.planError = null; toast(tr("review.job_started", { id: res.body && res.body.job_id != null ? res.body.job_id : DASH }));
    });
  }
  // ── 플랜 버전 대화상자 — «Store snapshot for which version?» ──
  function openPlanDialog() {
    var dlg = $("#plan-dialog"), input = $("#plan-version");
    if (!dlg || !input) return;
    input.value = planVersionGuess(releaseDoc(), state.store.github);
    $("[data-plan-status]").textContent = "";
    renderPlanDialogState();
    if (!dlg.open) { if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", ""); }
    input.focus(); input.select();
  }
  /** Go 는 `major.minor.patch` 꼴일 때만 열린다 — 빈 값도, `1.0` 도 아니다. */
  function renderPlanDialogState() {
    var go = $("#plan-dialog [data-plan-go]"), input = $("#plan-version"), st = $("#plan-dialog [data-plan-version-state]");
    if (!go || !input) return;
    var v = input.value.trim(), ok = PLAN_VERSION_RE.test(v);
    go.disabled = !ok || !state.token || !!state.store.busy;
    go.textContent = state.store.busy === "plan" ? tr("store.refreshing_plan") : tr("store.plan_dialog.go");
    if (st) { st.textContent = v && !ok ? tr("store.plan_dialog.bad") : ""; st.className = "nstate" + (v && !ok ? " bad" : ""); }
  }
  function submitPlanDialog() {
    var dlg = $("#plan-dialog"), input = $("#plan-version");
    if (!dlg || !input) return;
    var v = input.value.trim();
    if (!PLAN_VERSION_RE.test(v) || !state.token || state.store.busy) return;
    dlg.close();   // 거절은 Store 행 머리에 빨갛게 온다
    refreshPlan(v);
  }
  // ── W2 새 버전 대화상자 (§3.2 · R3 · R4) ─────────────────────────────────────
  // 묻는 것은 버전 이름뿐이다(R3). 트랙 · dry-run · 빌드 번호는 여기 없다. 체크를 끄면 그 스토어는
  // 본문에서 빠지고(E2), 이름은 꼴이 맞고 라이브보다 커야 «만들기» 가 열린다.
  function versionListNow() { return versionListModel(state.store.versions, L(), now()); }
  function openVersionDialog(prefill) {
    var dlg = $("#version-dialog");
    if (!dlg) return;
    var m = versionListNow();
    var chosen = {};
    m.platforms.forEach(function (p) {
      var value = prefill && prefill[p] != null ? String(prefill[p]) : (m.hints[p] || "");
      chosen[p] = { on: prefill ? prefill[p] != null : true, name: value };
    });
    state.store.newVersion = chosen;
    state.store.createError = null;
    renderVersionFields();
    renderVersionDialogState();
    if (!dlg.open) { if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", ""); }
    var first = $("#version-dialog input[data-version-name]");
    if (first) { first.focus(); first.select(); }
  }
  /** 칸은 플랜이 아는 스토어만 둔다(E2) — 둘 다 모르면 둘 다. 힌트는 라이브 + patch 다. */
  function renderVersionFields() {
    var wrap = $("#version-dialog [data-version-fields]");
    if (!wrap) return;
    var m = versionListNow(), nv = state.store.newVersion || {};
    var label = { ios: "App Store · iOS", android: "Google Play · Android" };
    wrap.innerHTML = m.platforms.map(function (p) {
      var f = nv[p] || { on: true, name: "" };
      var hint = m.live[p] ? tr("version.dialog.hint", { version: m.live[p] }) : tr("version.dialog.no_hint");
      return '<div class="nbox vfield" data-version-field="' + p + '">'
        + '<label class="ck"><input type="checkbox" data-version-check="' + p + '"' + (f.on ? " checked" : "") + "> " + esc(label[p]) + "</label>"
        + '<input id="version-' + p + '" type="text" inputmode="decimal" autocomplete="off" spellcheck="false" placeholder="1.0.1"'
        + ' data-version-name="' + p + '" value="' + esc(f.name) + '"' + (f.on ? "" : " disabled") + ' aria-describedby="version-' + p + '-state">'
        + '<span class="sub">' + esc(hint) + "</span>"
        + '<span id="version-' + p + '-state" class="nstate" data-version-state="' + p + '"></span></div>';
    }).join("");
  }
  /** «만들기» 가 열리는 조건 — 스토어 하나 이상 · 고른 이름이 전부 꼴에 맞고 라이브보다 큼. */
  function versionDialogDecision() {
    var m = versionListNow(), nv = state.store.newVersion || {};
    var chosen = Object.keys(nv).filter(function (p) { return nv[p].on; });
    var reasons = {}, ok = chosen.length > 0;
    chosen.forEach(function (p) {
      var r = versionNameCheck(nv[p].name, m.live[p]);
      if (!r.ok) { ok = false; reasons[p] = r.reason; }
    });
    return { ok: ok, chosen: chosen, reasons: reasons, live: m.live };
  }
  function versionReasonText(platform, reason, live) {
    if (reason === "not_greater") return tr("version.dialog.reason.not_greater", { version: live[platform] || DASH });
    return tr("version.dialog.reason." + (reason === "pattern" ? "pattern" : "empty"));
  }
  function renderVersionDialogState() {
    var go = $("#version-dialog [data-version-go]"), st = $("#version-dialog [data-version-status]");
    if (!go) return;
    var d = versionDialogDecision();
    go.disabled = !d.ok || !state.admin || !!state.store.creating;
    go.textContent = tr(state.store.creating ? "version.dialog.creating" : "version.dialog.go");
    var first = null;
    Object.keys(state.store.newVersion || {}).forEach(function (p) {
      var el = $('#version-dialog [data-version-state="' + p + '"]');
      if (!el) return;
      var reason = d.reasons[p];
      var text = reason ? versionReasonText(p, reason, d.live) : "";
      el.textContent = text; el.className = "nstate" + (reason ? " bad" : "");
      if (text && !first) first = text;
    });
    if (!st) return;
    var err = state.store.createError;
    if (err) {
      st.className = "dlg-status sub bad";
      st.innerHTML = esc(err.text) + (err.id != null
        ? ' <a class="btn" href="' + esc(storeHash(state.store.repo, "version", err.id)) + '" data-version-exists-open="' + esc(String(err.id)) + '">' + esc(tr("version.row.open")) + "</a>" : "");
      return;
    }
    st.className = "dlg-status sub";
    st.textContent = !state.admin ? tr("version.list.new_hint.admin")
      : d.ok ? "" : (d.chosen.length ? first || "" : tr("version.dialog.reason.no_store"));
  }
  function submitVersionDialog() {
    var d = versionDialogDecision();
    if (!d.ok || !state.admin || state.store.creating) return;
    var nv = state.store.newVersion, body = {}, repo = state.store.repo;
    d.chosen.forEach(function (p) { body[p + "_version"] = String(nv[p].name).trim(); });
    state.store.creating = true; state.store.createError = null;
    renderVersionDialogState();
    storeApi().versionCreate(repo, body).then(function (res) {
      state.store.creating = false;
      if (res.ok) {
        var dlg = $("#version-dialog");
        if (dlg && dlg.open) dlg.close();
        state.store.versions = null;
        var id = res.body && typeof res.body === "object" ? res.body.id : null;
        if (id != null) { location.hash = storeHash(repo, "version", id); return; }
        loadStore();
        return;
      }
      if (res.status === 401 || res.status === 403) tokenRejected();
      var b = res.body && typeof res.body === "object" ? res.body : {};
      var code = b.error_code || b.code || null;
      // 같은 이름의 드래프트가 이미 있으면(E3) 서버가 그 번호를 준다 — 새로 만들지 말고 열게 한다
      state.store.createError = code === "version_exists" && b.id != null
        ? { code: code, id: b.id, text: tr("version.dialog.exists", { id: b.id }) }
        : { code: code, id: null, text: tr("version.dialog.failed", { detail: refusalText(res, L()) }) };
      renderVersionDialogState();
    }).catch(function () {
      state.store.creating = false;
      state.store.createError = { code: null, id: null, text: tr("version.dialog.failed", { detail: "network" }) };
      renderVersionDialogState();
    });
  }
  // ── «버리기» 확인 (§3.2 · AC-C8) ────────────────────────────────────────────
  function openDiscardDialog(id) {
    var dlg = $("#version-discard-dialog");
    if (!dlg) return;
    var m = versionListNow();
    var row = m.drafts.filter(function (r) { return String(r.id) === String(id); })[0];
    var v = state.store.version;
    var title = row ? row.title : v && String(v.id) === String(id) ? versionTitle(v.ios_version, v.android_version) : "#" + id;
    state.store.discardTarget = id;
    var body = $("#version-discard-dialog [data-discard-body]");
    if (body) body.textContent = tr("version.discard.body", { version: title });
    var st = $("#version-discard-dialog [data-discard-status]");
    if (st) { st.textContent = ""; st.className = "dlg-status sub"; }
    if (!dlg.open) { if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", ""); }
  }
  function submitDiscardDialog() {
    var id = state.store.discardTarget, repo = state.store.repo;
    if (id == null || state.store.discarding) return;
    state.store.discarding = true;
    storeApi().versionDiscard(repo, id).then(function (res) {
      state.store.discarding = false;
      var st = $("#version-discard-dialog [data-discard-status]");
      if (res.ok) {
        var dlg = $("#version-discard-dialog");
        if (dlg && dlg.open) dlg.close();
        state.store.discardTarget = null; state.store.versions = null;
        if (state.store.sub === "version" && String(state.store.versionId) === String(id)) { location.hash = storeHash(repo, "versions"); return; }
        loadStore();
        return;
      }
      if (res.status === 401 || res.status === 403) tokenRejected();
      if (st) { st.textContent = tr("version.discard.failed", { detail: refusalText(res, L()) }); st.className = "dlg-status sub bad"; }
    }).catch(function () {
      state.store.discarding = false;
      var st2 = $("#version-discard-dialog [data-discard-status]");
      if (st2) { st2.textContent = tr("version.discard.failed", { detail: "network" }); st2.className = "dlg-status sub bad"; }
    });
  }
  function validateListing() {
    var r = releaseDoc(), plan = r && r.plan, doc = planEntryDoc(plan) || {};
    var body = { build_name: plan && plan.build_name != null ? String(plan.build_name) : "", build: isNum(doc.n) ? String(doc.n) : "" };
    jobCall("validate", function (api, repo) { return api.validateListing(repo, body); }, function (res) {
      var b = res.body && typeof res.body === "object" ? res.body : {};
      state.store.validate = { ok: b.ok === true, lines: Array.isArray(b.lines) ? b.lines : [], exit: isNum(b.exit) ? b.exit : null };
    });
  }
  function planReview() {
    var body = reviewBody("plan", reviewChoicesCtx());
    jobCall("review-plan", function (api, repo) { return api.review(repo, body); }, function (res) {
      toast(tr("review.job_started", { id: res.body && res.body.job_id != null ? res.body.job_id : DASH }));
    });
  }
  /** «심사 제출…» — 시트가 연다. 조건은 `sheetModel.canSubmit` 하나이고, 본문은 그 모델의
      `submitBody` 그대로다(버전 id 와 이 버전의 build_name 이 들어 있다). */
  function openSubmitDialog() {
    if (!sheetNow().canSubmit) return;
    var dlg = $("#submit-dialog");
    if (!dlg) return;
    var m = sheetNow();
    $("[data-submit-title]").textContent = tr("review.dialog_title", { version: m.head.ver, build: m.planN != null ? m.planN : DASH });
    $("[data-submit-body]").textContent = tr("review.dialog_body", { targets: targetsText(state.store.review.platforms) });
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }
  function submitReview() {
    var dlg = $("#submit-dialog");
    if (dlg) dlg.close();
    var m = sheetNow();
    if (!m.canSubmit) return;   // 대화상자가 열린 사이 조건이 바뀌었을 수 있다
    var body = m.submitBody;
    jobCall("submit", function (api, repo) { return api.review(repo, body); }, function (res) {
      // 보낸 뒤 확인은 지운다 — 한 확인이 다음 되돌릴 수 없는 일까지 넘어가지 않는다(항목 28)
      state.store.review.managed = false; state.store.review.typedN = "";
      toast(tr("review.submitted_toast", { id: res.body && res.body.job_id != null ? res.body.job_id : DASH }));
    });
  }
  // ── 행동: 드라이버 Start · Confirm N · Abort · Retry · 예행 (항목 26 · 28 · 35) ──
  function driverCall(kind, run, after, onFail) {
    if (!state.token || state.store.busy) return;
    var repo = state.store.repo;
    state.store.busy = kind; state.store.driverError = null; renderStore(); renderConfirmNState();
    run(storeApi(), repo).then(function (res) {
      state.store.busy = null;
      if (res.ok) after(res);
      else {
        // 409 는 서버의 규칙이다 — 코드를 그대로 쓰고, 돌아가지 않는다
        state.store.driverError = refusalText(res, L());
        if (onFail) onFail(res);
        if (res.status === 401 || res.status === 403) tokenRejected();
      }
      return loadStore();
    }).catch(function () { state.store.busy = null; state.store.driverError = "network"; renderStore(); renderConfirmNState(); });
  }
  function startDriver() {
    var m = driverModel();
    if (!driverActions(m, driverCtx()).start.enabled) return;
    var f = state.store.driverForm, body = { build_name: driverFormVersion() };
    if (f.track) body.android_track = f.track;
    if (f.dryRun) body.dry_run = true;
    if (nMode() === "auto") body.build_number = "auto";   // S2 를 서버가 플랜의 N 으로 이어 준다
    driverCall("start", function (api, repo) { return api.driverStart(repo, body); }, function (res) {
      state.store.driverForm = { version: null, track: "", dryRun: false }; state.store.driverDialogKey = null;
      toast(tr("driver.started_toast", { id: res.body && res.body.release_id != null ? res.body.release_id : DASH }));
    });
  }
  function abortDriver() {
    var m = driverModel();
    if (!driverActions(m, driverCtx()).abort.enabled) return;
    driverCall("abort", function (api, repo) { return api.driverAbort(repo, { build_name: m.version }); }, function () { toast(tr("driver.aborted_toast")); });
  }
  function retryDriver() {
    var m = driverModel();
    if (!driverActions(m, driverCtx()).retry.enabled) return;
    driverCall("retry", function (api, repo) { return api.driverRetry(repo, { build_name: m.version }); }, function () { toast(tr("driver.retried_toast")); });
  }
  function rehearseUpload() {
    if (!driverActions(driverModel(), driverCtx()).rehearsal.enabled) return;
    var body = rehearsalBody(releaseDoc());
    driverCall("rehearsal", function (api, repo) { return api.upload(repo, body); }, function (res) {
      toast(tr("driver.rehearsal_toast", { id: res.body && res.body.job_id != null ? res.body.job_id : DASH }));
    });
  }
  /** S2 의 N 대화상자(항목 28) — 회차마다 한 번 저절로, 그 뒤엔 «Confirm build number…» 가 다시 연다. */
  function maybeOpenConfirmN() {
    var m = driverModel();
    if (!m.dialog) return;
    var key = state.store.repo + "/" + m.version + "/" + m.planN;
    if (state.store.driverDialogKey === key) return;
    state.store.driverDialogKey = key;
    openConfirmN();
  }
  function openConfirmN() {
    var m = driverModel(), dlg = $("#confirm-n-dialog"), input = $("#driver-n");
    if (!m.dialog || !dlg) return;
    var auto = nMode() === "auto";
    $("[data-confirm-n-title]").textContent = tr("driver.confirm.title", { version: m.version });
    $("[data-confirm-n-body]").textContent = tr(auto ? "driver.confirm.body_auto" : "driver.confirm.body", { n: m.planN });
    $("[data-confirm-n-status]").textContent = "";
    var box = dlg.querySelector(".nbox"); if (box) box.hidden = auto;
    input.value = state.store.driverN || "";
    renderConfirmNState();
    if (!dlg.open) { if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", ""); }
    input.focus();
  }
  /** Confirm 버튼 · N 상태만 제자리에서 갱신. 맞는 N 만 연다 — 글자 그대로. */
  function renderConfirmNState() {
    var go = $("#confirm-n-dialog [data-confirm-n-go]"), st = $("#confirm-n-dialog [data-driver-n-state]");
    if (!go) return;
    var m = driverModel(), d = confirmNDecision(state.store.driverN, m.planN, nMode()), a = driverActions(m, driverCtx()).confirm;
    go.disabled = !a.enabled;
    go.title = a.enabled ? "" : driverReasons(a);
    go.textContent = state.store.busy === "confirm" ? tr("driver.confirming") : m.planN != null ? tr("driver.confirm.go", { n: m.planN }) : tr("driver.confirm.go_empty");
    if (st) {
      st.textContent = d.state === "empty" || d.state === "auto" ? "" : d.state === "ok" ? tr("review.n_ok", { n: m.planN }) : d.state === "mismatch" ? tr("review.n_mismatch", { n: m.planN }) : tr("review.n_unknown");
      st.className = "nstate" + (d.state === "ok" ? " ok" : d.state === "empty" || d.state === "auto" ? "" : " bad");
    }
  }
  function confirmDriverN() {
    var m = driverModel();
    if (!driverActions(m, driverCtx()).confirm.enabled) return;   // 대화상자가 열린 사이 회차가 바뀌었을 수 있다
    var typed = state.store.driverN, dlg = $("#confirm-n-dialog"), send = nSendValue(nMode(), typed), shown = send === "auto" ? m.planN : typed;
    driverCall("confirm", function (api, repo) { return api.driverConfirm(repo, { build_name: m.version, build_number: send }); }, function () {
      state.store.driverN = "";   // 한 확인은 한 번만 쓴다(항목 28)
      if (dlg && dlg.open) dlg.close();
      toast(tr("driver.confirmed_toast", { n: shown }));
    }, function (res) { var st = $("[data-confirm-n-status]"); if (st) st.textContent = refusalText(res, L()); });
  }
  function wireStore() {
    var st = $("#store");
    if (!st) return;
    st.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-enter-store],[data-goto-settings],[data-verify-all],[data-fetch-remote],[data-set-value],[data-store-refresh],[data-plan-refresh],[data-plan-other],[data-validate-listing],[data-plan-review],[data-sheet-submit],[data-sheet-toggle],[data-sheet-fix],[data-driver-abort],[data-driver-retry],[data-driver-confirm-open],[data-upload-rehearsal],[data-n-mode],[data-version-new],[data-version-discard],[data-version-retry],[data-field-revert],[data-listing-retry]");
      if (!t) return;
      if (t.closest("summary")) ev.preventDefault();  // 머리의 버튼은 행을 여닫지 않는다
      // ── 바텀시트(§4.2) ──
      if (t.hasAttribute("data-sheet-toggle")) { setSheetOpen(!sheetOpen()); renderStore(); return; }
      if (t.hasAttribute("data-sheet-submit")) { if (!t.disabled) { state.lastTrigger = t; openSubmitDialog(); } return; }
      if (t.hasAttribute("data-sheet-fix")) { if (!t.disabled) sheetGoFix(t.getAttribute("data-fix-anchor"), t.getAttribute("data-fix-route")); return; }
      if (t.hasAttribute("data-field-revert")) { if (!t.disabled) revertListingField(t.getAttribute("data-field-revert")); return; }
      if (t.hasAttribute("data-listing-retry")) { flushListingSave(); return; }
      if (t.hasAttribute("data-version-new")) { if (!t.disabled) { state.lastTrigger = t; openVersionDialog(null); } return; }
      if (t.hasAttribute("data-version-discard")) { if (!t.disabled) { state.lastTrigger = t; openDiscardDialog(parseInt(t.getAttribute("data-version-discard"), 10)); } return; }
      if (t.hasAttribute("data-version-retry")) {
        // 실패한 드래프트는 이름을 안 붙잡는다(§15) — 같은 이름으로 다시 만들 수 있다
        if (t.disabled) return;
        var again = versionListNow().drafts.filter(function (r) { return String(r.id) === t.getAttribute("data-version-retry"); })[0];
        state.lastTrigger = t;
        openVersionDialog(again ? { ios: again.ios, android: again.android } : null);
        return;
      }
      if (t.hasAttribute("data-enter-store")) { if (!t.disabled) { state.store.screen = "store"; if (state.store.gateReturn) { goBackToGateReturn(); return; } renderStore(); } return; }
      if (t.hasAttribute("data-goto-settings")) { state.store.screen = "settings"; renderStore(); return; }
      if (t.hasAttribute("data-verify-all")) { verifyAll(); return; }
      if (t.hasAttribute("data-fetch-remote")) { fetchRemote(); return; }
      if (t.hasAttribute("data-store-refresh")) { loadStore({ listing: true }); return; }
      if (t.hasAttribute("data-plan-refresh")) { if (!t.disabled) { state.lastTrigger = t; refreshPlan(); } return; }
      if (t.hasAttribute("data-plan-other")) { if (!t.disabled) { state.lastTrigger = t; openPlanDialog(); } return; }
      if (t.hasAttribute("data-validate-listing")) { validateListing(); return; }
      if (t.hasAttribute("data-plan-review")) { planReview(); return; }
      if (t.hasAttribute("data-driver-abort")) { if (!t.disabled) abortDriver(); return; }
      if (t.hasAttribute("data-driver-retry")) { if (!t.disabled) retryDriver(); return; }
      if (t.hasAttribute("data-driver-confirm-open")) { state.lastTrigger = t; openConfirmN(); return; }
      if (t.hasAttribute("data-n-mode")) { state.store.nMode = t.getAttribute("data-n-mode"); state.store.review.typedN = ""; state.store.driverN = ""; renderStore(); return; }
      if (t.hasAttribute("data-upload-rehearsal")) { if (!t.disabled) rehearseUpload(); return; }
      if (t.hasAttribute("data-set-value")) { state.lastTrigger = t; openSecretDialog(t.getAttribute("data-set-value")); }
    });
    st.addEventListener("input", function (ev) {
      // 문안 칸 — 800 ms 디바운스로 그 키만 보낸다(§3.2 · AC-C6)
      var fieldId = ev.target.getAttribute && ev.target.getAttribute("data-listing-field");
      if (fieldId) { if (!ev.target.readOnly) listingFieldTyped(fieldId, ev.target.value); return; }
      if (ev.target.hasAttribute && ev.target.hasAttribute("data-driver-version")) { state.store.driverForm.version = ev.target.value; renderDriverState(); return; }
      if (ev.target.hasAttribute && ev.target.hasAttribute("data-driver-track")) { state.store.driverForm.track = ev.target.value; return; }
      if (ev.target.id !== "sheet-n") return;
      state.store.review.typedN = ev.target.value;   // 기억하지 않는다 — state 에만, 새 플랜이 오면 지워진다
      renderSheetState();
    });
    st.addEventListener("submit", function (ev) {
      if (ev.target.hasAttribute && ev.target.hasAttribute("data-driver-start")) { ev.preventDefault(); startDriver(); }
    });
    st.addEventListener("change", function (ev) {
      if (ev.target.hasAttribute && ev.target.hasAttribute("data-driver-dry")) { state.store.driverForm.dryRun = !!ev.target.checked; return; }
      var check = ev.target.getAttribute && ev.target.getAttribute("data-review-check");
      if (check) {
        var rv = state.store.review, on = !!ev.target.checked;
        if (check === "ios") rv.platforms.ios = on;
        else if (check === "android") { rv.platforms.android = on; if (!on) rv.managed = false; }
        else if (check === "managed") rv.managed = on;
        else if (check === "full") rv.listingFull = on;
        else if (check === "phased") rv.phased = on;
        if (check === "android" && !on) { var m = $('#release-sheet [data-review-check="managed"]'); if (m) m.checked = false; }
        renderSheetState();
        return;
      }
      var zone = ev.target.closest("[data-drop]");
      if (!zone || ev.target.type !== "file") return;
      var files = ev.target.files;
      putSecretFile(zone.getAttribute("data-drop"), zone.getAttribute("data-drop-file"), files);
      ev.target.value = "";
    });
    ["dragenter", "dragover"].forEach(function (kind) {
      st.addEventListener(kind, function (ev) {
        var zone = ev.target.closest("[data-drop]");
        if (!zone) return;
        ev.preventDefault();
        if (!zone.classList.contains("disabled")) zone.classList.add("over");
      });
    });
    st.addEventListener("dragleave", function (ev) { var zone = ev.target.closest("[data-drop]"); if (zone) zone.classList.remove("over"); });
    st.addEventListener("drop", function (ev) {
      var zone = ev.target.closest("[data-drop]");
      if (!zone) return;
      ev.preventDefault(); zone.classList.remove("over");
      if (zone.classList.contains("disabled")) { toast(tr("secrets.reject.admin")); return; }
      putSecretFile(zone.getAttribute("data-drop"), zone.getAttribute("data-drop-file"), ev.dataTransfer ? ev.dataTransfer.files : null);
    });
    st.addEventListener("toggle", function (ev) {
      var d = ev.target;
      if (!d || !d.classList || !d.classList.contains("srow") || !d.hasAttribute("data-row")) return;
      var rendered = d.dataset.renderedOpen === "1";
      if (d.open === rendered) return;  // 렌더가 정한 상태 — 사람의 선택이 아니다
      d.dataset.renderedOpen = d.open ? "1" : "0";
      rememberRow(d.getAttribute("data-row"), d.open);
    }, true);
    var nav = $("#view-nav");
    if (nav) nav.addEventListener("change", function (ev) {
      if (ev.target.hasAttribute("data-repo-select")) location.hash = "#/store/" + encodeURIComponent(ev.target.value);
    });
    var sdlg = $("#submit-dialog");
    if (sdlg) {
      sdlg.querySelector("form").addEventListener("submit", function (ev) { ev.preventDefault(); submitReview(); });
      $("[data-submit-cancel]").addEventListener("click", function () { sdlg.close(); });
      sdlg.addEventListener("close", restoreTrigger);
    }
    var pdlg = $("#plan-dialog");
    if (pdlg) {
      pdlg.querySelector("form").addEventListener("submit", function (ev) { ev.preventDefault(); submitPlanDialog(); });
      pdlg.addEventListener("input", function (ev) { if (ev.target.id === "plan-version") renderPlanDialogState(); });
      $("[data-plan-cancel]").addEventListener("click", function () { pdlg.close(); });
      pdlg.addEventListener("close", restoreTrigger);
    }
    var ndlg = $("#confirm-n-dialog");
    if (ndlg) {
      ndlg.querySelector("form").addEventListener("submit", function (ev) { ev.preventDefault(); confirmDriverN(); });
      ndlg.addEventListener("input", function (ev) { if (ev.target.id === "driver-n") { state.store.driverN = ev.target.value; renderConfirmNState(); } });
      $("[data-confirm-n-cancel]").addEventListener("click", function () { ndlg.close(); });
      ndlg.addEventListener("close", restoreTrigger);
    }
    var dlg = $("#secret-dialog");
    if (dlg) {
      dlg.querySelector("form").addEventListener("submit", function (ev) { ev.preventDefault(); submitSecretDialog(); });
      $("[data-secret-cancel]").addEventListener("click", function () { dlg.close(); });
      dlg.addEventListener("close", function () { $("#secret-input").value = ""; state.store.dialogSecret = null; restoreTrigger(); });
    }
    // 새 버전 대화상자 — 체크 하나, 칸 하나. 값은 이 페이지에만 살고 localStorage 에 가지 않는다.
    var vdlg = $("#version-dialog");
    if (vdlg) {
      vdlg.querySelector("form").addEventListener("submit", function (ev) { ev.preventDefault(); submitVersionDialog(); });
      vdlg.addEventListener("input", function (ev) {
        var p = ev.target.getAttribute && ev.target.getAttribute("data-version-name");
        if (!p || !state.store.newVersion || !state.store.newVersion[p]) return;
        state.store.newVersion[p].name = ev.target.value;
        renderVersionDialogState();
      });
      vdlg.addEventListener("change", function (ev) {
        var p = ev.target.getAttribute && ev.target.getAttribute("data-version-check");
        if (!p || !state.store.newVersion || !state.store.newVersion[p]) return;
        state.store.newVersion[p].on = !!ev.target.checked;
        var box = $('#version-dialog input[data-version-name="' + p + '"]');
        if (box) box.disabled = !ev.target.checked;
        renderVersionDialogState();
      });
      vdlg.addEventListener("click", function (ev) {
        var link = ev.target.closest("[data-version-exists-open]");
        if (link) vdlg.close();   // 이미 있는 드래프트를 열러 간다 — 대화상자는 비켜 준다
      });
      $("[data-version-cancel]").addEventListener("click", function () { vdlg.close(); });
      vdlg.addEventListener("close", function () { state.store.createError = null; restoreTrigger(); });
    }
    var ddlg = $("#version-discard-dialog");
    if (ddlg) {
      ddlg.querySelector("form").addEventListener("submit", function (ev) { ev.preventDefault(); submitDiscardDialog(); });
      $("[data-discard-cancel]").addEventListener("click", function () { ddlg.close(); });
      ddlg.addEventListener("close", function () { state.store.discardTarget = null; restoreTrigger(); });
    }
  }

  function applyHash() {
    var route = parseRoute(location.hash);
    if (route.view === "store") { closeDrawer(true); enterStore(route.repo, route); return; }
    if (state.view !== "queue") showView("queue");
    var m = /^#\/jobs\/(\d+)(\/log)?$/.exec(location.hash);
    if (!m) { closeDrawer(true); return; }
    var id = parseInt(m[1], 10);
    if (m[2]) { if (state.token) openDrawer(id); else gotoJob(id); }
    else { closeDrawer(true); gotoJob(id); }
  }

  function afterLoad(fn) {
    if (document.readyState === "complete") fn(); else window.addEventListener("load", fn, { once: true });
  }
  function boot() {
    state.noSse = /[?&]poll=1(&|$)/.test(location.search);
    state.debug = /[?&]debug=1(&|$)/.test(location.search);
    loadLang();
    applyStatic();
    loadExpanded();
    lsSet("rcm.collapsed", null);   // 뜻이 뒤집힌 옛 키 — 남겨 두면 영영 남는다
    state.token = lsGet("rcm.token");
    wireTokenDialog(); wireClicks(); wireLang(); wireHostDetails(); wireStore(); renderTokenButton();
    var first = (state.token ? verifyToken(state.token, true) : Promise.resolve()).then(function () { return fetchStatus(); });
    first.then(function () {
      state.tz = state.status && state.status.display_timezone ? state.status.display_timezone : null;
      render(); startPolling();
      // 탭은 `/api/repos` 가 릴리스 프로파일을 하나라도 주는 서버에만 있다 — 404 면 큐 화면 그대로
      loadRepos().then(applyHash);
      // SSE 는 load 뒤에 연다 — 열린 스트림이 load 를 붙들면 headless 렌더·인쇄가 끝나지 않는다
      afterLoad(function () { setTimeout(openSse, 0); });
    });
    setInterval(tick, TICK_MS);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})(typeof window !== "undefined" ? window : null);
