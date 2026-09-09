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
  var BACKOFF = [2, 4, 8, 16, 30];
  var LOST_AFTER_MS = 30000;
  var POLL_MS = 10000;
  var TICK_MS = 1000;
  var REFETCH_COALESCE_MS = 300;
  var HIDDEN_PAUSE_MS = 60000;
  // 남은 저장 공간이 이 밑이면 사용률과 무관하게 경고한다 — 스냅샷 하나가 못 풀린다(§4.6-가)
  var DISK_LOW_FREE = 10 * 1024 * 1024 * 1024;

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
        parts.push(T(lang, "reason.stuck"));
        if (isNum(est.elapsed_seconds) && isNum(est.expected_seconds) && est.expected_seconds > 0) parts.push(T(lang, "reason.times_expected", { n: Math.floor(est.elapsed_seconds / est.expected_seconds) }));
        var lo = row.progress && row.progress.last_output_at;
        var quiet = secondsSince(lo, nowMs);
        if (isNum(quiet)) parts.push(T(lang, "reason.no_output_for", { since: fmtCoarse(quiet) }));
        out.text = parts.join(" · "); out.actionable = true; out.cls = "stuck"; break;
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
  function hostPressure(host) {
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
    var verdict;
    if (lowDisk) verdict = "busy";
    else if (!known.length) verdict = "unknown";
    else if (known.some(function (v) { return v >= 85; })) verdict = "busy";
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
  function progressHead(prog, lang) {
    if (!prog || prog.phase === "materializing") return null;
    var steps = Array.isArray(prog.steps) ? prog.steps : [];
    if (!steps.length) return T(lang, "progress.no_markers", { dur: fmtDuration(prog.job_seconds) });
    var cur = isNum(prog.current_index) ? prog.current_index : prog.steps_done;
    var total = isNum(prog.steps_total) ? prog.steps_total : "?";
    var t = T(lang, "progress.step", { cur: cur, total: total, soFar: !!prog.steps_total_partial });
    if (prog.current_name) t += " · " + prog.current_name + " · " + fmtDuration(prog.current_seconds);
    t += " · " + T(lang, "progress.job", { dur: fmtDuration(prog.job_seconds) });
    var f = failedStepCount(prog);
    if (f) t += " · " + T(lang, "progress.steps_failed", { n: f });
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
    // 마커가 없는 잡은 머리줄에 잡 초 하나뿐이다 — 그것마저 얼면 도는 잡이 통째로 멈춰 보인다
    if (!steps.length) return jobSpan("progress.no_markers");
    var i = isNum(prog.current_index) ? prog.current_index : prog.steps_done;
    var total = isNum(prog.steps_total) ? prog.steps_total : "?";
    var h = esc(T(lang, "progress.step", { cur: i, total: total, soFar: !!prog.steps_total_partial }));
    if (prog.current_name) {
      h += " · " + esc(prog.current_name) + " · " + span(cur && cur.started_at, fmtDuration(prog.current_seconds));
    }
    h += " · " + jobSpan("progress.job");
    var f = failedStepCount(prog);
    if (f) h += " · " + esc(T(lang, "progress.steps_failed", { n: f }));
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
    if (prog && prog.phase === "materializing") { out.condition = "preparing"; return out; }
    var total = prog && isNum(prog.steps_total) ? prog.steps_total : null;
    var done = prog && isNum(prog.steps_done) ? prog.steps_done : null;
    if (isNum(total) && total > 0 && isNum(done) && !(prog && prog.steps_total_partial)) {
      out.basis = "steps"; out.total = total; out.done = Math.min(done, total);
      if (done >= total) { out.condition = stuck ? "stuck" : "finalizing"; return out; }
      out.pct = pctOf(done, total);
      if (stuck) out.condition = "stuck";
      return out;
    }
    var expected = isNum(est.expected_seconds) && est.expected_seconds > 0 && est.source !== "default"
      ? est.expected_seconds : null;
    var elapsed = isNum(est.elapsed_seconds) ? est.elapsed_seconds : null;
    if (expected == null || elapsed == null) {
      if (stuck) out.condition = "stuck";
      return out;
    }
    out.expected = expected; out.source = est.source || null;
    if (stuck) { out.condition = "stuck"; return out; }
    if (est.overdue || elapsed >= expected) { out.condition = "over"; return out; }
    out.basis = "time"; out.pct = timePct(elapsed, expected); out.startedAt = row.started_at || null;
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
    if (p.basis === "steps") {
      head = isNum(p.pct)
        ? T(lang, "pbar.steps", { percent: p.pct, done: p.done, total: p.total })
        : T(lang, "pbar.steps_all", { done: p.done, total: p.total });
    } else if (p.basis === "time") {
      head = T(lang, timeKey(p.source), { percent: p.pct });
    }
    var cond = p.condition === "normal" ? null : T(lang, "pbar." + p.condition);
    var label = [head, cond].filter(Boolean).join(" · ") || T(lang, "pbar.none");
    var full = p.condition === "over" || p.condition === "finalizing";
    var width = isNum(p.pct) ? p.pct : (full ? 100 : 0);
    // 근거·형편은 **data 속성**으로 싣는다. class 로 두면 `steps`·`stuck`·`over` 가 화면의 다른
    // 규칙(스텝 목록 격자 · 이유 칸 칩)에 걸려 막대가 엉뚱한 폭으로 그려진다 — 실제로 그랬다.
    var tick = live && p.basis === "time" && p.condition === "normal" && p.startedAt
      ? ' data-tick="progress" data-from="' + esc(p.startedAt) + '" data-expected="' + p.expected
        + '" data-source="' + esc(p.source || "") + '"'
      : "";
    return '<div class="pwrap"' + tick + '><div class="pbar" data-basis="' + p.basis + '" data-cond="'
      + p.condition + '" role="progressbar"'
      + ' aria-valuemin="0" aria-valuemax="100"' + (isNum(p.pct) ? ' aria-valuenow="' + p.pct + '"' : "")
      + ' aria-valuetext="' + esc(label) + '" aria-label="' + esc(T(lang, "pbar.aria", { id: row.id })) + '">'
      + '<i data-fill="' + width + '"></i></div><span class="plab">' + esc(label) + "</span></div>";
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
    } else if (job.failed_step) {
      var stepKey = job.failed_step_guessed === true ? "recent.step_guessed" : "recent.step";
      summary = (outcomeText(job, lang) ? outcomeText(job, lang) + " · " : "") + T(lang, stepKey, { step: job.failed_step });
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

  var rcm = {
    DASH: DASH, esc: esc, fmtDuration: fmtDuration, fmtClock: fmtClock, fmtClockSeconds: fmtClockSeconds, fmtAgo: fmtAgo,
    fmtCoarse: fmtCoarse, fmtCountdown: fmtCountdown, fmtBytes: fmtBytes, fmtBytesPair: fmtBytesPair, fmtMemory: fmtMemory, fmtDisk: fmtDisk, fmtMb: fmtMb, fmtPct: fmtPct,
    ordinal: ordinal, truncate: truncate, stateWord: stateWord, stateGlyph: stateGlyph, personLabel: personLabel,
    reasonText: reasonText, confidenceBadge: confidenceBadge, etaText: etaText,
    elapsedText: elapsedText, notMoving: notMoving, yourJobs: yourJobs, isMine: isMine, hostPressure: hostPressure,
    jobStorageLine: jobStorageLine,
    queueHeader: queueHeader, sortQueue: sortQueue, workerPills: workerPills, workerName: workerName, hostCards: hostCards, headerNote: headerNote, progressHead: progressHead, progressHeadHtml: progressHeadHtml, queueGroups: queueGroups, runningStep: runningStep,
    stepMark: stepMark, overallProgress: overallProgress, progressBarHtml: progressBarHtml, timePct: timePct, recentLine: recentLine, artifactsLine: artifactsLine, outcomeText: outcomeText, workerState: workerState, rerunCommand: rerunCommand, shellQuote: shellQuote, transitionsLine: transitionsLine,
    sourceHtml: sourceHtml, priorityChip: priorityChip, cacheText: cacheText,
    poolHeader: poolHeader, poolSummary: poolSummary, poolsOf: poolsOf, recentOf: recentOf,
    connection: connection, nextBackoff: nextBackoff, ACTIONABLE: ACTIONABLE, TERMINAL: TERMINAL,
    LOST_AFTER_MS: LOST_AFTER_MS, POLL_MS: POLL_MS
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
    token: null, me: null, tokenBad: false, readAuth: false, skewUnknown: false, lastTrigger: null,
    lang: I18N.DEFAULT_LANG,
    expanded: {}, expandedRecent: {}, showAllRecent: false, showAllQueue: false,
    es: null, retryTimer: null, pollTimer: null, refetchTimer: null, hiddenSince: null, lostShownAt: null,
    drawer: { jobId: null, offset: 0, timer: null, lines: 0 }, cancelTarget: null, hl: null, tz: null
  };
  function now() { return state.skewUnknown ? NaN : Date.now() + state.skewMs; }
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
    state.tokenBad = true; state.me = null; state.token = null; lsSet("rcm.token", null);
    renderTokenButton(); render();
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
      state.token = tok; state.me = me && me.name ? me.name : null; state.tokenBad = false; lsSet("rcm.token", tok);
      if (status) status.textContent = "ok · " + (state.me || "") + (me && me.admin ? " (admin)" : "");
      return true;
    }).catch(function () {
      // 네트워크 오류 — 저장값은 지키고 검증만 못 한 것
      state.token = tok; state.tokenBad = false;
      if (status) status.textContent = tr("token.kept");
      return null;
    }).then(function (ok) { renderTokenButton(); if (ok !== false) fetchStatus(); return ok; });
  }
  function renderTokenButton() {
    var b = $("#tok-btn");
    if (!b) return;
    b.classList.toggle("bad", !!state.tokenBad);
    b.textContent = state.tokenBad ? tr("token.bad_button")
      : (state.me ? tr("token.named", { name: state.me })
        : (state.token ? tr("token.unverified")
          : (state.readAuth ? tr("token.read_auth") : tr("token.add"))));
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
      state.token = null; state.me = null; state.tokenBad = false; lsSet("rcm.token", null); renderTokenButton(); render(); dlg.close();
    });
    dlg.querySelector("form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var tok = input.value.trim();
      if (!tok) return;
      verifyToken(tok).then(function (ok) { if (ok) dlg.close(); });
    });
    window.addEventListener("storage", function (ev) {
      if (ev.key === "rcm.token") { state.token = ev.newValue; state.tokenBad = false; state.me = null; if (state.token) verifyToken(state.token, true); else { renderTokenButton(); render(); } }
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
    var hp = hostPressure(host);
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
    if (hiddenCount) html += '<button type="button" class="more" data-more-queue>' + esc(tr("queue.more", { n: hiddenCount })) + "</button>";
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
    // 접어 둘 수 없다. 스텝 목록·로그 tail·액션만 ▸ 뒤에 있다.
    var bar = progressBarHtml(row, L(), canTick());
    var mine = isMine(row, state.me);
    var cls = [];
    if (mine) cls.push("mine");
    if (est.overdue || est.stuck) cls.push("overdue");
    if (expanded) cls.push("exp");
    if (state.hl === row.id) cls.push("hl");
    if (row._dim) cls.push("dim");
    // 막대 줄이 붙는 행은 아래 선을 지운다 — 막대까지가 한 행으로 보이게
    if (bar) cls.push("hasbar");
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
    var reasonCell = r.cls === "blocked" ? '<span class="blocked">' + reasonHtml + "</span>" : r.cls === "stalled" ? '<span class="stalled">' + reasonHtml + "</span>" : r.cls === "stuck" ? '<span class="stuck">' + reasonHtml + "</span>" : '<span class="reason' + (r.actionable || busy ? " act" : "") + '">' + reasonHtml + "</span>";
    // 펼치지 않아도 지금 무엇을 하는지 읽혀야 한다(§4.6-라). 스텝 초는 기준점으로 스스로 센다.
    var nowStep = busy && !expanded ? stepNowHtml(row.progress) : "";
    if (nowStep) reasonCell += '<div class="sub step-now">' + nowStep + "</div>";
    if (row.state === "uploading" && row.reason === "upload_stalled") reasonCell += '<div class="sub">' + esc(tr("row.stalled_note")) + "</div>";
    if (row._cancelRequested) reasonCell += '<div class="sub">' + esc(tr("row.cancel_requested")) + "</div>";
    // 접힌 행이면 내 잡을 여기서 바로 세울 수 있어야 한다 — 폰에서 유일한 취소 경로이고, 도는
    // 잡을 멈추는 일이 스텝 목록을 구경하는 일보다 급하다(사용자 검사 U3.6 · Codex 리뷰 4).
    var canActRow = !!state.token && !state.tokenBad && (mine || state.me === null);
    if (!expanded && canActRow && !row._cancelRequested && row.state !== "cancelling") reasonCell += '<div class="sub"><button type="button" class="btn danger cancel" data-cancel="' + row.id + '">' + esc(tr("row.cancel")) + "</button></div>";
    var el = elapsedText(row, now(), L());
    var elapsedCell = busy && isNum(est.elapsed_seconds)
      ? '<span data-tick="elapsed" data-from="' + esc(row.started_at || "") + '">' + esc(el.main) + "</span>" + (el.sub ? '<div class="sub">' + esc(el.sub) + "</div>" : "")
      : (row.state === "queued" ? '<span data-tick="waiting" data-from="' + esc(row.created_at || "") + '">' + esc(el.main) + "</span>" : esc(el.main));
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
    if (bar) h += '<tr class="qbar" data-bar="' + row.id + '"><td colspan="7">' + bar + "</td></tr>";
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
    return h;
  }
  function tailHtml(row) {
    var busy = row.state === "running" || row.state === "cancelling";
    var mine = isMine(row, state.me);
    var h = "";
    if (Array.isArray(row.log_tail) && row.log_tail.length) h += '<div class="tail">' + esc(row.log_tail.slice(-5).join("\n")) + "</div>";
    else if (!state.token) h += '<div class="sub" style="margin-top:8px">' + esc(tr("row.add_token_for_log")) + "</div>";
    var canAct = !!state.token && !state.tokenBad && (mine || state.me === null);
    var joiners = Array.isArray(row.joiners) ? row.joiners.length : 0;
    h += '<div class="actions"><button type="button" class="btn log" data-log="' + row.id + '"' + (canAct ? "" : " disabled") + ">" + esc(tr("row.log")) + "</button>" +
      '<button type="button" class="btn danger cancel" data-cancel="' + row.id + '"' + (canAct && busy && row.state !== "cancelling" ? "" : " disabled") + ">" + esc(tr("row.cancel")) + "</button>" +
      (!state.token ? "" : (!mine ? '<span class="sub">' + esc(tr("row.not_your_job")) + "</span>" : (joiners ? '<span class="sub">' + esc(tr("row.others_waiting", { n: joiners })) + "</span>" : ""))) + "</div>";
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
   * 접힌 호스트 절의 한 줄. 「세 질문」에 호스트는 없다(§4.1) — 평소엔 접어 두고, 뭔가 잘못됐을
   * 때만 저절로 펼친다. 사람이 직접 여닫으면 그 선택이 이긴다(`rcm.host` 에 저장).
   */
  function hostDigest() {
    var p = pool0(state.status);
    if (!p) return { text: tr("host.no_sample"), warn: false };
    if (p.hosts === null || p.hosts === undefined) {
      return { text: tr("host.unavailable", { error: errorText(p.hosts_error, p.hosts_error_code) }), warn: true };
    }
    var cards = hostCards(state.status, L());
    if (!cards.length) return { text: tr("host.no_sample"), warn: false };
    var worst = null, stale = false;
    cards.forEach(function (c) {
      var hp = hostPressure(c.host);
      var age = secondsSince(c.host.sampled_at, now());
      if (c.host.stale || (isNum(age) && isNum(c.host.interval_seconds) && age > 3 * c.host.interval_seconds)) stale = true;
      if (!worst || (hp.verdict === "busy" && worst.verdict !== "busy")) worst = hp;
    });
    var names = cards.map(function (c) { return c.host.name || DASH; }).join(" · ");
    var vkey = "summary.verdict_" + (worst.verdict === "fine" || worst.verdict === "busy" || worst.verdict === "partial" ? worst.verdict : "unknown");
    return {
      text: tr("host.digest", { names: names, n: cards.length, state: tr(vkey) }),
      warn: worst.verdict === "busy" || stale
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
    return {
      text: T(lang, "host.job_storage", {
        used: fmtDisk(doc.volume_bytes),
        limit: limit === null ? "" : fmtDisk(limit),
        next: left || ""
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
    // 사람이 직접 연 적이 있으면 그 선택이 이긴다. 없으면 경고일 때만 펼친다.
    var choice = lsGet("rcm.host");
    var want = choice === "open" ? true : choice === "closed" ? false : d.warn;
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
      // 데이터 디렉터리가 쥔 부피는 서버의 사실이라 **서버 자신의 카드에만** 그린다.
      var js = local ? jobStorageLine((state.status && state.status.server || {}).job_storage, state.status && state.status.generated_at, L()) : null;
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
      html += '<div class="rrow' + (failedish ? " clickable" : "") + (state.hl === job.id ? " hl" : "") + '" data-job="' + job.id + '"' + (failedish ? ' data-rtoggle="' + job.id + '" role="button" tabindex="0" aria-expanded="' + (open ? "true" : "false") + '"' : "") + ">" +
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
      if (open) html += '<div class="rdetail">' + esc(transitionsLine(job, tz(), L())) + (job.failed_step ? "<br>" + esc(tr(job.failed_step_guessed === true ? "recent.failed_step_guessed" : "recent.failed_step")) + "<b>" + esc(job.failed_step) + "</b>" : "") + (outcomeText(job, L()) ? "<br>" + esc(outcomeText(job, L())) : "") + "</div>";
      html += "</div>";
    });
    html += "</div>";
    if (all.length > 5) html += '<button type="button" class="more" data-more-recent>' + (state.showAllRecent ? tr("recent.show_fewer") : tr("recent.show_more", { n: all.length - 5 })) + "</button>";
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
    var pct = over ? null : timePct(seconds, expected);
    var text = over ? tr("pbar.over") : tr(timeKey(wrap.getAttribute("data-source")), { percent: pct });
    fill.style.width = (over ? 100 : pct) + "%";
    bar.setAttribute("data-basis", over ? "none" : "time");
    bar.setAttribute("data-cond", over ? "over" : "normal");
    bar.setAttribute("aria-valuetext", text);
    if (over) bar.removeAttribute("aria-valuenow"); else bar.setAttribute("aria-valuenow", String(pct));
    lab.textContent = text;
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
    var key = focusKey(document.activeElement);
    fn();
    if (!key) return;
    var el = null;
    try { el = $(key); } catch (e) { el = null; }
    if (el && el !== document.activeElement) { try { el.focus({ preventScroll: true }); } catch (e2) { el.focus(); } }
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
      if (t.hasAttribute("data-rtoggle")) { var rid = parseInt(t.getAttribute("data-rtoggle"), 10); if (ev.target.closest("[data-copy]")) return; state.expandedRecent[rid] = !state.expandedRecent[rid]; renderRecent(); return; }
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
  function applyHash() {
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
    wireTokenDialog(); wireClicks(); wireLang(); wireHostDetails(); renderTokenButton();
    var first = (state.token ? verifyToken(state.token, true) : Promise.resolve()).then(function () { return fetchStatus(); });
    first.then(function () {
      state.tz = state.status && state.status.display_timezone ? state.status.display_timezone : null;
      render(); startPolling(); applyHash();
      // SSE 는 load 뒤에 연다 — 열린 스트림이 load 를 붙들면 headless 렌더·인쇄가 끝나지 않는다
      afterLoad(function () { setTimeout(openSse, 0); });
    });
    setInterval(tick, TICK_MS);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})(typeof window !== "undefined" ? window : null);
