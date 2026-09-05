/* rcm queue — app.js. 빌드 도구 없음. 정본: docs/wireframes/web-queue.html · docs/m2-workplan.md.
   앞부분은 순수 함수(window.rcm / module.exports — node --test 로 검사), 뒷부분은 DOM.
   규칙: 모르는 값은 "—", 절대 0·빈칸·긍정 문구로 그리지 않는다(fail-open 금지). */
(function (root) {
  "use strict";

  var DASH = "—";
  var ACTIONABLE = ["worker_down", "stuck", "upload_stalled", "not_scheduled", "blocked_by_group", "overdue", "paused"];
  var TERMINAL = { succeeded: 1, failed: 1, timed_out: 1, cancelled: 1, lost: 1 };
  var GLYPH = { running: "▶", queued: "·", uploading: "↑", cancelling: "■", succeeded: "✓",
    failed: "✗", timed_out: "⏱", cancelled: "■", lost: "?" };
  var BACKOFF = [2, 4, 8, 16, 30];
  var LOST_AFTER_MS = 30000;
  var POLL_MS = 10000;
  var TICK_MS = 1000;
  var REFETCH_COALESCE_MS = 300;
  var HIDDEN_PAUSE_MS = 60000;

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
  function fmtAgo(seconds) { return isNum(seconds) ? fmtCoarse(seconds) + " ago" : DASH; }
  function fmtCountdown(seconds) {
    if (!isNum(seconds)) return DASH;
    if (seconds <= 0) return "now";
    return "in " + fmtDuration(seconds);
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
  function fmtPct(v) { return isNum(v) ? Math.round(v) + "%" : DASH; }
  function ordinal(n) {
    if (!isNum(n)) return DASH;
    var r100 = n % 100, r10 = n % 10, suf = "th";
    if (r100 < 11 || r100 > 13) suf = r10 === 1 ? "st" : r10 === 2 ? "nd" : r10 === 3 ? "rd" : "th";
    return n + suf;
  }
  function truncate(label, n) {
    if (label == null) return DASH;
    n = n || 40;
    var s = String(label);
    return s.length <= n ? s : s.slice(0, n - 1) + "…";
  }
  function stateWord(state) { return state === "timed_out" ? "timed out" : (state || "unknown"); }
  function stateGlyph(state) { return GLYPH[state] || "·"; }

  function busyCount(status) {
    var w = status && status.server && status.server.workers;
    if (!Array.isArray(w)) return null;
    return w.filter(function (x) { return x.state === "busy"; }).length;
  }
  function laneCount(status) {
    var s = status && status.server;
    if (!s) return null;
    if (isNum(s.lanes)) return s.lanes;
    return Array.isArray(s.workers) ? s.workers.length : null;
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
  function reasonText(row, a, b) {
    var nowMs = isNum(a) ? a : (isNum(b) ? b : null);
    var status = (a && typeof a === "object") ? a : ((b && typeof b === "object") ? b : null);
    var reason = row && row.reason;
    var est = (row && row.estimate) || {};
    var src = (row && row.source) || {};
    var links = [];
    var out = { text: "unknown", actionable: false, links: links, cls: "" };
    if (!row || !reason) return out;
    switch (reason) {
      case "running":
        out.text = isNum(row.lane) ? "running · lane " + row.lane : "running"; break;
      case "waiting_for_lane": {
        var t = "waiting for lane";
        var busy = busyCount(status), lanes = laneCount(status);
        if (isNum(busy) && isNum(lanes)) t += " · " + busy + "/" + lanes + " busy";
        if (isNum(row.ahead_job_id)) { t += " · behind #" + row.ahead_job_id; links.push({ jobId: row.ahead_job_id }); }
        if (isNum(est.wait_seconds)) t += " · frees in " + fmtDuration(est.wait_seconds);
        out.text = t; break;
      }
      case "blocked_by_group": {
        var bb = row.blocked_by || {};
        // 조각은 남기고 모르는 숫자만 —(「frees in —」): 막는 잡이 있는 한 「언제 풀리나」는 늘 묻는 질문이다
        var t2 = "⛓ blocked by " + (isNum(bb.job_id) ? "#" + bb.job_id : DASH) + " · " + (bb.group || row.concurrency_group || DASH) + " · frees in " + fmtDuration(bb.remaining_seconds);
        if (isNum(bb.job_id)) links.push({ jobId: bb.job_id });
        out.text = t2; out.actionable = true; out.cls = "blocked"; break;
      }
      case "uploading":
        out.text = "uploading · " + fmtBytesPair(src.received_bytes, src.bytes); break;
      case "upload_stalled": {
        var since = secondsSince(src.last_received_at, nowMs);
        out.text = "upload stalled " + fmtCoarse(since) + " · " + fmtBytesPair(src.received_bytes, src.bytes);
        out.actionable = true; out.cls = "stalled"; break;
      }
      case "materializing":
        // 목업 33: tree 는 「unpacking 48 MB」, git_ref 는 「fetching dev」, 둘 다 모르면 조각 없이
        out.text = "preparing workspace";
        if (src.mode === "git_ref") { if (src.ref) out.text += " · fetching " + src.ref; }
        else if (isNum(src.bytes)) out.text += " · unpacking " + fmtBytes(src.bytes);
        break;
      case "overdue": {
        var over = isNum(est.elapsed_seconds) && isNum(est.expected_seconds) ? est.elapsed_seconds - est.expected_seconds : null;
        out.text = "over by " + fmtDuration(over) + " · expected " + fmtDuration(est.expected_seconds);
        out.actionable = true; out.cls = "over"; break;
      }
      case "stuck": {
        var t3 = "⚠ likely stuck";
        if (isNum(est.elapsed_seconds) && isNum(est.expected_seconds) && est.expected_seconds > 0) t3 += " · " + Math.floor(est.elapsed_seconds / est.expected_seconds) + "× expected";
        var lo = row.progress && row.progress.last_output_at;
        var quiet = secondsSince(lo, nowMs);
        if (isNum(quiet)) t3 += " · no output for " + fmtCoarse(quiet);
        out.text = t3; out.actionable = true; out.cls = "stuck"; break;
      }
      case "cancelling": {
        var c = row.cancel || {};
        var kill = secondsUntil(c.kill_at, nowMs);
        out.text = "SIGTERM sent by " + personLabel(row, c.by) + " · kill " + fmtCountdown(kill); break;
      }
      case "paused": out.text = "paused"; out.actionable = true; break;
      case "not_scheduled": out.text = "not scheduled"; out.actionable = true; break;
      case "worker_down": out.text = "no worker"; out.actionable = true; break;
      default: out.text = "unknown";
    }
    return out;
  }

  function confidenceBadge(est) {
    est = est || {};
    var n = isNum(est.sample_count) ? est.sample_count : null;
    var c = est.confidence;
    if (!c) {
      if (est.source === "measured") c = (n != null && n >= 5) ? "high" : "med";
      else if (est.source) c = "low";
      else return { cls: "low", text: "low · " + DASH };
    }
    if (c === "overdue") return { cls: "over", text: "overdue" };
    if (c === "group wait") return { cls: "low", text: "low · group wait" };
    if (c === "high" || c === "med") return { cls: c, text: c + " · measured" + (n != null ? " n=" + n : "") };
    return { cls: "low", text: "low · " + (est.source || DASH) };
  }

  function etaText(row, tz, nowMs) {
    var est = (row && row.estimate) || {};
    if (!est.finish_at) return { clock: DASH, rel: null };
    var busy = row.state === "running" || row.state === "cancelling";
    var total = busy ? est.remaining_seconds : (isNum(est.wait_seconds) && isNum(est.remaining_seconds) ? est.wait_seconds + est.remaining_seconds : null);
    if (row.reason === "blocked_by_group" && row.blocked_by && isNum(row.blocked_by.job_id)) {
      return { clock: "after #" + row.blocked_by.job_id, rel: "~" + fmtClock(est.finish_at, tz, nowMs) };
    }
    return { clock: fmtClock(est.finish_at, tz, nowMs), rel: isNum(total) ? "in " + fmtDuration(total) : null };
  }

  function elapsedText(row, nowMs) {
    var est = (row && row.estimate) || {};
    var st = row && row.state;
    if (st === "running" || st === "cancelling") {
      var sub = isNum(est.waited_seconds) && est.waited_seconds > 0 ? "waited " + fmtDuration(est.waited_seconds) : null;
      return { main: fmtDuration(est.elapsed_seconds), sub: sub };
    }
    if (st === "queued") return { main: "waiting " + fmtDuration(est.waited_seconds), sub: null };
    return { main: DASH, sub: null };
  }

  function pool0(status) { return status && Array.isArray(status.pools) && status.pools.length ? status.pools[0] : null; }
  function queueOf(status) { var p = pool0(status); return p ? p.queue : undefined; }

  // ── 요약 (항목 23 · 24 · 25) ──
  function notMoving(status, me) {
    var q = queueOf(status);
    if (!Array.isArray(q)) return { kind: "unknown", lines: [] };
    var lines = [];
    var unknown = false;
    q.forEach(function (row) {
      if (!("reason" in row)) { unknown = true; return; }
      if (row.reason === "running" || row.reason === "waiting_for_lane" || row.reason === "uploading" || row.reason === "materializing" || row.reason === "cancelling") return;
      if (ACTIONABLE.indexOf(row.reason) === -1) return;
      var r = reasonText(row, status, Date.parse(status.generated_at));
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
  function yourJobs(status, me) {
    if (!me) return { kind: "no_token", lines: [], more: 0 };
    var q = queueOf(status);
    if (!Array.isArray(q)) return { kind: "unknown", lines: [], more: 0 };
    var tz = status.display_timezone || undefined;
    var nowMs = parseIso(status.generated_at);
    var mine = sortQueue(q.filter(function (r) { return isMine(r, me); }));
    if (!mine.length) return { kind: "none", lines: [], more: 0 };
    var lines = mine.slice(0, 2).map(function (row) {
      var busy = row.state === "running" || row.state === "cancelling";
      var eta = etaText(row, tz, nowMs);
      var t;
      if (busy) t = stateWord(row.state) + " · ETA " + eta.clock + (eta.rel ? " · " + eta.rel : "");
      else {
        // 대기 줄은 짧게: 순번 · ETA · 이유의 첫 조각(「2nd in line · ETA 09:58 · waiting for lane」). 그룹 대기는 ~시각
        t = (isNum(row.position) ? ordinal(row.position) + " in line" : stateWord(row.state)) + " · ETA " + (eta.rel && /^after /.test(eta.clock) ? eta.rel : eta.clock);
        var brief = reasonText(row, nowMs, status).text.replace(/^⛓ /, "").split(" · ")[0];
        if (brief && brief !== "unknown") t += " · " + brief;
      }
      var joined = Array.isArray(row.joiners) ? row.joiners.length : 0;
      if (joined) t += " · +" + joined + " joined";
      return { jobId: row.id, text: t, state: row.state };
    });
    return { kind: "list", lines: lines, more: Math.max(0, mine.length - 2) };
  }

  // {cpu, mem, gpu, load, verdict} — 퍼센트는 정수(목업 4절), load 는 「3.5 / 10」 문자열(§2).
  // 85% 이상이면 busy(아는 값이 이미 바쁘다고 말하므로 partial 보다 우선), 하나라도 모르면 partial, 셋 다 모르면 unknown.
  // load·cores 는 판정에 안 들어간다 — 텍스트만 —.
  function hostPressure(host) {
    if (!host) return { cpu: null, mem: null, gpu: null, load: DASH, verdict: "no_sample" };
    var pct = function (v) { return isNum(v) ? Math.round(v) : null; };
    var cpu = pct(host.cpu && host.cpu.busy);
    var mem = host.memory && isNum(host.memory.used_bytes) && isNum(host.memory.total_bytes) && host.memory.total_bytes > 0
      ? pct(host.memory.used_bytes / host.memory.total_bytes * 100) : null;
    var gpu = pct(host.gpu && host.gpu.util_pct);
    var load1 = Array.isArray(host.load) && isNum(host.load[0]) ? host.load[0] : null;
    var load = isNum(load1) ? load1.toFixed(1) + " / " + (isNum(host.cores) ? host.cores : DASH) : DASH;
    var vals = [cpu, mem, gpu];
    var known = vals.filter(isNum);
    var verdict;
    if (!known.length) verdict = "unknown";
    else if (known.some(function (v) { return v >= 85; })) verdict = "busy";
    else if (known.length < vals.length) verdict = "partial";
    else verdict = "fine";
    return { cpu: cpu, mem: mem, gpu: gpu, load: load, verdict: verdict };
  }

  function queueHeader(status, nowMs) {
    var q = queueOf(status);
    if (!Array.isArray(q)) return "unknown";
    var running = 0, waiting = 0, oldest = null;
    q.forEach(function (r) {
      if (r.state === "running" || r.state === "cancelling") running++;
      else {
        waiting++;
        var w = r.estimate && isNum(r.estimate.waited_seconds) ? r.estimate.waited_seconds : secondsSince(r.created_at, nowMs);
        if (isNum(w) && (oldest == null || w > oldest)) oldest = w;
      }
    });
    var t = q.length + " jobs · " + running + " running · " + waiting + " waiting";
    if (waiting) t += " · oldest waiting " + fmtDuration(oldest);
    var busy = busyCount(status), lanes = laneCount(status);
    if (isNum(busy) && isNum(lanes)) t += " · lanes " + busy + "/" + lanes + " busy";
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

  function workerPills(server) {
    server = server || {};
    var workers = Array.isArray(server.workers) ? server.workers : [];
    var lanes = isNum(server.lanes) ? server.lanes : workers.length;
    var pills = [];
    if (lanes === 1 && workers.length === 1) {
      var w = workers[0];
      pills.push({ text: "worker " + (w.state || DASH) + (isNum(w.job_id) ? " #" + w.job_id : ""), cls: w.state || "", jobId: isNum(w.job_id) ? w.job_id : null, lane: w.lane });
    } else {
      workers.forEach(function (w) {
        if (w.state === "busy" && isNum(w.job_id)) pills.push({ text: "#" + w.job_id, cls: "busy", jobId: w.job_id, lane: w.lane });
        else if (w.state === "down") pills.push({ text: "lane " + w.lane + " · down", cls: "down", jobId: null, lane: w.lane });
        else pills.push({ text: "lane " + w.lane + " · " + (w.state || DASH), cls: w.state || "", jobId: null, lane: w.lane });
      });
    }
    if (server.paused) pills.push({ text: "paused", cls: "paused", jobId: null, lane: null });
    return pills;
  }

  // {kind: "reload"|"restart", text} | null — DOM 은 kind 로 띠 색을 고르고, 공개 headerNote 는 §2 대로 문자열만 준다.
  // 버전·스키마 변화가 재시작보다 우선. uptime 이 어느 쪽이든 null 이면 재시작을 주장하지 않는다. prev 없으면(첫 조회) null.
  function headerNoteKind(status, nowMs, prev) {
    if (!status || !prev || typeof prev !== "object") return null;
    if (status.schema_version !== prev.schema_version || (status.server && prev.server && status.server.version !== prev.server.version)) {
      return { kind: "reload", text: "UI out of date — reload" };
    }
    if (status.server && prev.server && isNum(status.server.uptime_seconds) && isNum(prev.server.uptime_seconds) && status.server.uptime_seconds < prev.server.uptime_seconds) {
      var startMs = parseIso(status.generated_at);
      var tz = status.display_timezone || undefined;
      var at = startMs != null ? fmtClock(new Date(startMs - status.server.uptime_seconds * 1000).toISOString(), tz, nowMs) : DASH;
      return { kind: "restart", text: "Server restarted at " + at + " — running jobs were marked lost" };
    }
    return null;
  }
  function headerNote(status, nowMs, prev) {
    var note = headerNoteKind(status, nowMs, prev);
    return note ? note.text : null;
  }

  // ── 진행 (항목 12) · 최근 (항목 14) ──
  function failedStepCount(prog) {
    return Array.isArray(prog && prog.steps) ? prog.steps.filter(function (s) { return s.ok === false; }).length : 0;
  }
  function progressHead(prog) {
    if (!prog || prog.phase === "materializing") return null;
    var steps = Array.isArray(prog.steps) ? prog.steps : [];
    if (!steps.length) return "no step markers · job " + fmtDuration(prog.job_seconds);
    var cur = isNum(prog.current_index) ? prog.current_index : prog.steps_done;
    var total = isNum(prog.steps_total) ? prog.steps_total : "?";
    var t = "step " + cur + "/" + total + (prog.steps_total_partial ? " (so far)" : "");
    if (prog.current_name) t += " · " + prog.current_name + " · " + fmtDuration(prog.current_seconds);
    t += " · job " + fmtDuration(prog.job_seconds);
    var f = failedStepCount(prog);
    if (f) t += " · " + f + " step" + (f > 1 ? "s" : "") + " failed";
    return t;
  }
  function stepMark(step) {
    if (!step) return "·";
    if (step.state === "running") return "▶";
    if (step.ok === false) return "✘";
    if (step.state === "done") return "✔";
    return "·";
  }
  function recentLine(job, tz, nowMs) {
    job = job || {};
    var state = job.state;
    var pill;
    if (state === "succeeded") pill = "succeeded";
    else if (state === "failed") pill = isNum(job.exit_code) ? "failed · exit " + job.exit_code : "failed";
    else if (state === "cancelled") pill = "cancelled · exit 2";
    else if (state === "timed_out") pill = "timed out · exit 2";
    else if (state === "lost") pill = "lost · exit 3";
    else pill = stateWord(state);
    var summary = job.summary || "";
    if (state === "cancelled" && !job.started_at) {
      var who = job.cancelled_by ? personLabel(job, job.cancelled_by) : null;
      summary = "before start" + (who ? " · by " + who : "");
    } else if (state === "lost") {
      summary = job.summary || "lost";
    } else if (job.failed_step) {
      summary = (summary ? summary + " · " : "") + "step " + job.failed_step;
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
  function transitionsLine(job, tz) {
    var tr = Array.isArray(job && job.transitions) ? job.transitions : [];
    if (!tr.length) return DASH;
    var parts = [];
    for (var i = 0; i < tr.length; i++) {
      var t = tr[i];
      var seg = stateWord(t.state);
      if (t.state === "queued") {
        var next = tr[i + 1];
        if (next && next.state === "running") {
          var waited = secondsSince(t.at, parseIso(next.at));
          if (isNum(waited)) seg += " (waited " + fmtDuration(waited) + ")";
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
    fmtCoarse: fmtCoarse, fmtCountdown: fmtCountdown, fmtBytes: fmtBytes, fmtBytesPair: fmtBytesPair, fmtMemory: fmtMemory, fmtMb: fmtMb, fmtPct: fmtPct,
    ordinal: ordinal, truncate: truncate, stateWord: stateWord, stateGlyph: stateGlyph, personLabel: personLabel,
    reasonText: reasonText, confidenceBadge: confidenceBadge, etaText: etaText,
    elapsedText: elapsedText, notMoving: notMoving, yourJobs: yourJobs, isMine: isMine, hostPressure: hostPressure,
    queueHeader: queueHeader, sortQueue: sortQueue, workerPills: workerPills, headerNote: headerNote, progressHead: progressHead,
    stepMark: stepMark, recentLine: recentLine, rerunCommand: rerunCommand, shellQuote: shellQuote, transitionsLine: transitionsLine,
    sourceHtml: sourceHtml,
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
    collapsed: {}, expandedRecent: {}, showAllRecent: false, showAllQueue: false,
    es: null, retryTimer: null, pollTimer: null, refetchTimer: null, hiddenSince: null, lostShownAt: null,
    drawer: { jobId: null, offset: 0, timer: null, lines: 0 }, cancelTarget: null, hl: null, tz: null
  };
  function now() { return state.skewUnknown ? NaN : Date.now() + state.skewMs; }
  function tz() { return state.tz || undefined; }

  // ── 저장소 ──
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* 비공개 탭 등 */ } }
  function loadCollapsed() { try { (JSON.parse(lsGet("rcm.collapsed") || "[]") || []).forEach(function (id) { state.collapsed[id] = true; }); } catch (e) { state.collapsed = {}; } }
  function saveCollapsed() { lsSet("rcm.collapsed", JSON.stringify(Object.keys(state.collapsed).map(Number))); }

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
    if (status && !silent) status.textContent = "checking…";
    var headers = tok ? { Authorization: "Bearer " + tok } : {};
    return fetch("/api/whoami", { headers: headers, cache: "no-store" }).then(function (r) {
      if (r.status === 401 || r.status === 403) return { bad: true };
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    }).then(function (me) {
      if (me && me.bad) { state.token = null; state.me = null; state.tokenBad = true; lsSet("rcm.token", null); if (status) status.textContent = "Token rejected"; return false; }
      state.token = tok; state.me = me && me.name ? me.name : null; state.tokenBad = false; lsSet("rcm.token", tok);
      if (status) status.textContent = "ok · " + (state.me || "") + (me && me.admin ? " (admin)" : "");
      return true;
    }).catch(function () {
      // 네트워크 오류 — 저장값은 지키고 검증만 못 한 것
      state.token = tok; state.tokenBad = false;
      if (status) status.textContent = "couldn’t verify — kept";
      return null;
    }).then(function (ok) { renderTokenButton(); if (ok !== false) fetchStatus(); return ok; });
  }
  function renderTokenButton() {
    var b = $("#tok-btn");
    if (!b) return;
    b.classList.toggle("bad", !!state.tokenBad);
    b.textContent = state.tokenBad ? "🔑 Token rejected" : (state.me ? "🔑 " + state.me : (state.token ? "🔑 token (unverified)" : (state.readAuth ? "🔑 Read auth required" : "🔑 add token")));
  }
  function wireTokenDialog() {
    var dlg = $("#tok-dialog"), input = $("#tok-input");
    if (!dlg) return;
    $("#tok-btn").addEventListener("click", function () {
      $("[data-tok-status]").textContent = state.tokenBad ? "Token rejected — paste a new one" : "";
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
      if (ev.key === "rcm.collapsed") { state.collapsed = {}; loadCollapsed(); renderQueue(); }
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
    var age = isNum(c.lastOkAt) ? fmtAgo((now() - c.lastOkAt) / 1000) : DASH;
    var text;
    b.className = "livebtn " + c.mode;
    b.setAttribute("aria-pressed", c.mode === "paused" ? "true" : "false");
    if (c.mode === "live") text = "live · updated " + age;
    else if (c.mode === "polling") text = "polling · updated " + age;
    else if (c.mode === "lost") text = "lost · last update " + age;
    else text = "paused · resume";
    $("[data-live-text]").textContent = text;
    renderLostBanner();
  }
  function renderLostBanner() {
    var el = $("#banner-lost");
    if (!el) return;
    var c = state.conn;
    if (c.mode !== "lost") { el.hidden = true; return; }
    el.hidden = false;
    var age = isNum(c.lastOkAt) ? fmtAgo((now() - c.lastOkAt) / 1000) : DASH;
    $("[data-lost-text]").textContent = "Lost connection to " + hostName() + " · last update " + age + " · showing last known state";
    var retry = state.retryTimer ? "reconnecting " + fmtCountdown(c.retryIn) + "…" : (state.pollTimer ? "polling every 10s…" : "");
    $("[data-lost-sub]").textContent = retry;
  }
  function renderHeader() {
    var st = state.status || {};
    var server = st.server || {};
    $("[data-host-name]").textContent = hostName();
    var busy = busyCount(st), lanes = laneCount(st);
    $("[data-lanes-text]").textContent = isNum(busy) && isNum(lanes) && lanes > 1 ? "lanes " + busy + "/" + lanes + " busy" : "";
    var pills = workerPills(server).map(function (p) {
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
    var note = headerNoteKind(state.status, now(), state.prev);
    var noteEl = $("#banner-note");
    if (note && note.kind === "reload") {
      if (!sessionStorage.getItem("rcm.reloaded")) { sessionStorage.setItem("rcm.reloaded", "1"); location.reload(); return; }
      noteEl.hidden = false; noteEl.textContent = note.text; noteEl.className = "banner bad";
    } else if (note && note.kind === "restart") {
      noteEl.hidden = false; noteEl.textContent = note.text; noteEl.className = "banner warn";
    } else if (server.paused) {
      noteEl.hidden = false; noteEl.className = "banner warn";
      noteEl.innerHTML = "Queue paused by " + esc(server.paused.by || DASH) + " at " + esc(fmtClock(server.paused.at, tz(), now())) + " — running jobs finish, nothing new starts · <span class=\"mono\">rcm resume</span>";
    } else {
      var downs = (server.workers || []).filter(function (w) { return w.state === "down"; });
      if (downs.length) {
        var live = (server.workers || []).filter(function (w) { return w.state !== "down"; }).map(function (w) { return w.lane; });
        noteEl.hidden = false; noteEl.className = "banner bad";
        noteEl.textContent = "Worker on lane " + downs.map(function (w) { return w.lane; }).join(", ") + " stopped: " + (downs[0].error || "unknown error") + (live.length ? " · waiting jobs use lane " + live.join(", ") + " only" : " · nothing can start");
      } else {
        var stalled = notScheduledRow();
        if (stalled) { noteEl.hidden = false; noteEl.className = "banner warn"; noteEl.textContent = "Lane is idle but #" + stalled.id + " has not started for " + fmtDuration(stalled.estimate && stalled.estimate.waited_seconds) + " — check the server log"; }
        else noteEl.hidden = true;
      }
    }
    renderHeaderConn();
    state.footBase = "rcm " + (server.version || DASH) + " · up " + fmtDuration(server.uptime_seconds) + " · schema v" + (st.schema_version || DASH);
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
    var yj = yourJobs(st, state.me);
    var y;
    if (yj.kind === "no_token") y = '<span class="muted">Add a token to highlight your jobs</span>';
    else if (yj.kind === "unknown") y = '<span class="muted">unknown — queue unavailable</span>';
    else if (yj.kind === "none") y = '<span class="muted">No jobs of yours in the queue</span>';
    else y = yj.lines.map(function (l) { return jl(l.jobId, "#" + l.jobId) + " " + esc(l.text); }).join("<br>") + (yj.more ? '<br><span class="muted">and ' + yj.more + " more</span>" : "");
    $("[data-yours]").innerHTML = (lost ? '<span class="muted">last known: </span>' : "") + y;
    // 24
    var nm = notMoving(st, state.me);
    var s;
    if (nm.kind === "unknown") s = '<span class="muted">unknown — queue unavailable</span>';
    else if (nm.kind === "ok") s = lost ? '<span class="muted">last known: nothing stuck</span>' : '<span class="ok">Nothing is stuck</span>';
    else s = nm.lines.slice(0, 3).map(function (l) { return jl(l.jobId, "#" + l.jobId) + ' <span class="warn">' + esc(l.text) + "</span>"; }).join("<br>") + '<br><span class="muted">nothing else is stuck</span>';
    $("[data-stuck]").innerHTML = (lost && nm.kind === "list" ? '<span class="muted">last known:</span><br>' : "") + s;
    // 25
    var host = p && Array.isArray(p.hosts) ? p.hosts[0] : null;
    var hp = hostPressure(host);
    var lab = $("[data-host-lab]");
    var h;
    if (p && p.hosts === null) { h = '<span class="bad">host: unavailable</span>' + (p.hosts_error ? ' <span class="muted">' + esc(truncate(p.hosts_error, 60)) + "</span>" : ""); lab.textContent = "Host pressure"; }
    else if (hp.verdict === "no_sample") { h = '<span class="muted">host: no sample yet</span>'; lab.textContent = "Host pressure"; }
    else {
      var age = secondsSince(host.sampled_at, now());
      var stale = host.stale || (isNum(age) && isNum(host.interval_seconds) && age > 3 * host.interval_seconds);
      lab.textContent = "Host pressure · sampled " + fmtAgo(age);
      var mark = function (v) { return isNum(v) ? (v >= 85 ? '<b class="warn">' + fmtPct(v) + "</b>" : "<b>" + fmtPct(v) + "</b>") : "<b>" + DASH + "</b>"; };
      var verdict = hp.verdict === "fine" ? '<span class="ok">· fine</span>' : hp.verdict === "busy" ? '<span class="warn">· busy</span>' : hp.verdict === "partial" ? '<span class="muted">· partial</span>' : '<span class="muted">· unknown</span>';
      h = "CPU " + mark(hp.cpu) + " · Mem " + mark(hp.mem) + " · GPU " + mark(hp.gpu) + "<br>load " + esc(hp.load) + " " + verdict + (stale ? ' <span class="stale-badge">stale ' + fmtDuration(age) + "</span>" : "");
    }
    $("[data-pressure]").innerHTML = h;
  }

  // ── 렌더: 큐 ──
  function collapsedGc() {
    var q = queueOf(state.status) || [];
    var ids = {};
    q.forEach(function (r) { ids[r.id] = true; });
    var changed = false;
    Object.keys(state.collapsed).forEach(function (k) { if (!ids[k]) { delete state.collapsed[k]; changed = true; } });
    if (changed) saveCollapsed();
  }
  function renderQueue() {
    var st = state.status;
    var p = pool0(st);
    var body = $("[data-queue-body]");
    $("[data-queue-header]").textContent = queueHeader(st, now());
    if (!p) { body.innerHTML = '<div class="banner bad" role="alert" data-error="queue">Queue unavailable — no status yet</div>'; return; }
    if (p.queue === null || p.queue === undefined) {
      body.innerHTML = '<div class="banner bad" role="alert" data-error="queue">Queue unavailable — ' + esc(p.queue_error || "unknown error") + "</div>";
      return;
    }
    var server = st.server || {};
    if (!p.queue.length) {
      var allDown = Array.isArray(server.workers) && server.workers.length && server.workers.every(function (w) { return w.state === "down"; });
      var presets = Array.isArray(st.presets) ? st.presets.map(function (x) { return x.name; }).join(" · ") : "";
      body.innerHTML = '<div class="empty">' + (server.paused || allDown ? "Queue is empty but paused — nothing will start." : "Queue is empty — <code>rcm run &lt;preset&gt;</code> starts immediately.") + (presets ? '<br><span class="sub">presets: ' + esc(presets) + "</span>" : "") + "</div>";
      return;
    }
    collapsedGc();
    var rows = sortQueue(p.queue);
    var nm = notMoving(st, state.me);
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
    var html = '<div class="qwrap"><table class="q"><thead><tr><th>Job</th><th>Key</th><th>Requester</th><th>Reason</th><th>Elapsed</th><th>ETA <span class="tzh">· ' + esc(tzName) + '</span></th><th class="source">Source</th></tr></thead><tbody>';
    rows.forEach(function (row) { html += queueRowHtml(row, st); });
    html += "</tbody></table></div>";
    if (hiddenCount) html += '<button type="button" class="more" data-more-queue>and ' + hiddenCount + " more ▾</button>";
    body.innerHTML = html;
  }
  function queueRowHtml(row, st) {
    var est = row.estimate || {};
    var busy = row.state === "running" || row.state === "cancelling";
    var expanded = busy && !state.collapsed[row.id];
    var mine = isMine(row, state.me);
    var cls = [];
    if (mine) cls.push("mine");
    if (est.overdue || est.stuck) cls.push("overdue");
    if (expanded) cls.push("exp");
    if (state.hl === row.id) cls.push("hl");
    if (row._dim) cls.push("dim");
    var pos = isNum(row.position) ? '<span class="pos">' + esc(ordinal(row.position)) + " in line</span>" : "";
    var pill;
    if (row.state === "uploading") {
      var src = row.source || {};
      var pct = isNum(src.received_bytes) && isNum(src.bytes) && src.bytes > 0 ? Math.min(100, Math.round(src.received_bytes / src.bytes * 100)) : 0;
      pill = '<span class="pill uploading"><span aria-hidden="true">↑</span> uploading <span class="ub"><i style="width:' + pct + '%"></i></span></span>';
    } else if (row.state === "cancelling") pill = '<span class="pill cancelling"><span aria-hidden="true">■</span> cancelling…</span>';
    else pill = '<span class="pill ' + esc(row.state) + '"><span aria-hidden="true">' + stateGlyph(row.state) + "</span> " + esc(stateWord(row.state)) + "</span>";
    var expBtn = busy ? '<button type="button" class="exp-btn" data-toggle="' + row.id + '" aria-expanded="' + (expanded ? "true" : "false") + '" aria-controls="exp-' + row.id + '" title="' + (expanded ? "collapse" : "expand") + '">' + (expanded ? "▾" : "▸") + "</button>" : "";
    var chips = "";
    var inputs = row.inputs || {};
    Object.keys(inputs).forEach(function (k) { chips += '<button type="button" class="chip" data-inputs="' + row.id + '" title="' + esc(JSON.stringify(inputs)) + '">' + esc(k + "=" + inputs[k]) + "</button>"; });
    if (row.concurrency_group) chips += '<span class="chip">group ' + esc(row.concurrency_group) + "</span>";
    var req = row.requester || {};
    var joiners = Array.isArray(row.joiners) ? row.joiners : [];
    var requester = '<span title="token: ' + esc(req.name || "") + '">' + esc(truncate(req.label || req.name || DASH, 40)) + "</span>" + (mine && req.name === state.me ? '<span class="you">you</span>' : "") +
      (joiners.length ? '<button type="button" class="joiners" title="also waiting: ' + esc(joiners.map(function (j) { return j.label || j.name; }).join(", ")) + '">+' + joiners.length + "</button>" : "");
    if (mine && req.name !== state.me) requester += '<span class="you">you joined</span>';
    var r = reasonText(row, st, now());
    // 이유 문구에는 서버가 준 문자열(ref · group · label)이 들어간다 — escape 한 뒤 잡 링크만 버튼으로 바꾼다
    var reasonHtml = esc(r.text);
    r.links.forEach(function (l) { var id = l.jobId; reasonHtml = reasonHtml.replace("#" + id, '<button type="button" class="jlink" data-goto="' + id + '">#' + id + "</button>"); });
    var reasonCell = r.cls === "blocked" ? '<span class="blocked">' + reasonHtml + "</span>" : r.cls === "stalled" ? '<span class="stalled">' + reasonHtml + "</span>" : r.cls === "stuck" ? '<span class="stuck">' + reasonHtml + "</span>" : '<span class="reason' + (r.actionable || busy ? " act" : "") + '">' + reasonHtml + "</span>";
    if (row.state === "uploading" && row.reason === "upload_stalled") reasonCell += '<div class="sub">will be cancelled by the server if it stays stalled</div>';
    if (row._cancelRequested) reasonCell += '<div class="sub">cancel requested…</div>';
    var el = elapsedText(row, now());
    var elapsedCell = busy && isNum(est.elapsed_seconds)
      ? '<span data-tick="elapsed" data-from="' + esc(row.started_at || "") + '">' + esc(el.main) + "</span>" + (el.sub ? '<div class="sub">' + esc(el.sub) + "</div>" : "")
      : (row.state === "queued" ? '<span data-tick="waiting" data-from="' + esc(row.created_at || "") + '">' + esc(el.main) + "</span>" : esc(el.main));
    var eta = etaText(row, tz(), now());
    var conf = confidenceBadge(est);
    var etaCell = '<span class="eta">' + esc(eta.clock) + (eta.rel ? ' <span class="in">· ' + esc(eta.rel) + "</span>" : "") + '</span><br><span class="conf ' + esc(conf.cls) + '">' + esc(conf.text) + "</span>";
    if (est.overdue && !est.stuck) etaCell = '<span class="eta">' + DASH + '</span><br><span class="conf over">overdue</span>';
    var source = sourceHtml(row);
    var h = '<tr class="' + cls.join(" ") + '" data-job="' + row.id + '" id="job-' + row.id + '">' +
      '<td class="job">' + expBtn + '<span class="id">#' + row.id + "</span> " + pill + pos + "</td>" +
      '<td class="key"><span class="key">' + esc(row.key || row.preset || DASH) + "</span>" + chips + "</td>" +
      '<td class="requester">' + requester + "</td>" +
      '<td class="reason">' + reasonCell + "</td>" +
      '<td class="elapsed">' + elapsedCell + "</td>" +
      '<td class="eta">' + etaCell + "</td>" +
      '<td class="source">' + source + "</td></tr>";
    if (expanded) h += '<tr class="expanded" data-job="' + row.id + '"><td colspan="7" class="prog" id="exp-' + row.id + '">' + progressHtml(row) + '<div class="src-block sub">' + source + "</div>" + tailHtml(row) + "</td></tr>";
    return h;
  }
  function sourceHtml(row) {
    var s = row.source || {};
    if (s.mode === "git_ref") return '<button type="button" class="sha" data-src="' + row.id + '">' + esc((s.sha || "").slice(0, 7) || DASH) + "</button>" + '<div class="sub">' + esc(s.repo || "") + " · ref " + esc(s.ref || DASH) + "</div>";
    if (row.state === "uploading" && !s.base_sha) return '<span class="sub">not received yet</span>';
    var sha = (s.base_sha || "").slice(0, 7);
    return '<button type="button" class="sha" data-src="' + row.id + '" title="' + esc("tree " + (s.tree_hash || DASH)) + '">' + esc(sha || DASH) + "</button>" + (s.dirty ? '<span class="uncommitted">uncommitted</span>' : "") + '<div class="sub">' + esc(s.repo || "") + "</div>";
  }
  function progressHtml(row) {
    var prog = row.progress;
    if (!prog || prog.phase === "materializing") return "";
    var head = progressHead(prog);
    var steps = Array.isArray(prog.steps) ? prog.steps : [];
    var total = isNum(prog.steps_total) ? prog.steps_total : steps.length;
    var failed = failedStepCount(prog);
    var h = '<div class="head"><b>' + esc(head || "") + "</b>" + (failed ? ' <span class="fail">' + failed + " step failed</span>" : "") + ' <span class="note" title="step times are server receive times (as_received)">ⓘ</span></div>';
    if (steps.length) {
      var segs = "";
      var pending = Math.max(0, total - steps.length);
      var n = steps.length + pending;
      steps.forEach(function (s) { segs += '<i class="' + (s.state === "running" ? "run" : s.ok === false ? "fail" : "") + '" style="width:' + (100 / n) + '%"></i>'; });
      for (var i = 0; i < pending; i++) segs += '<i class="pend" style="width:' + (100 / n) + '%"></i>';
      var vt = "step " + (isNum(prog.current_index) ? prog.current_index : prog.steps_done) + " of " + (prog.steps_total_partial ? "at least " : "") + total;
      h += '<div class="minibar" role="progressbar" aria-valuemin="0" aria-valuemax="' + total + '" aria-valuenow="' + prog.steps_done + '" aria-valuetext="' + esc(vt) + '">' + segs + "</div>";
      h += '<div class="steps">';
      steps.forEach(function (s) {
        var c = s.state === "running" ? "run" : s.ok === false ? "fail" : "";
        h += '<div class="step ' + c + '"><span class="g" aria-hidden="true">' + stepMark(s) + "</span><span>" + esc(s.name) + '</span><span class="s">' + esc(fmtDuration(s.seconds)) + "</span></div>";
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
    else if (!state.token) h += '<div class="sub" style="margin-top:8px">Add a token to see the log</div>';
    var canAct = !!state.token && !state.tokenBad && (mine || state.me === null);
    var joiners = Array.isArray(row.joiners) ? row.joiners.length : 0;
    h += '<div class="actions"><button type="button" class="btn log" data-log="' + row.id + '"' + (canAct ? "" : " disabled") + ">Log</button>" +
      '<button type="button" class="btn danger cancel" data-cancel="' + row.id + '"' + (canAct && busy && row.state !== "cancelling" ? "" : " disabled") + ">Cancel</button>" +
      (!state.token ? "" : (!mine ? '<span class="sub">not your job</span>' : (joiners ? '<span class="sub">' + joiners + " other session" + (joiners > 1 ? "s are" : " is") + " waiting on this job</span>" : ""))) + "</div>";
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
  function renderHost() {
    var p = pool0(state.status);
    var body = $("[data-host-body]");
    if (!p) { body.innerHTML = '<div class="empty">host: no sample yet</div>'; return; }
    if (p.hosts === null || p.hosts === undefined) { body.innerHTML = '<div class="banner bad" role="alert" data-error="hosts">Host unavailable — ' + esc(p.hosts_error || "unknown error") + "</div>"; return; }
    if (!p.hosts.length) { body.innerHTML = '<div class="empty">host: no sample yet</div>'; return; }
    var h = p.hosts[0];
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
        (spark ? '<div class="spark">' + spark + "<span>5 min</span></div>" : "") + "</div>";
    };
    var html = '<div class="hostcard' + (stale ? " dim" : "") + '"><div class="hn">' + esc(h.name || DASH) + '<span class="age">' + (stale ? '<span class="stale-badge">stale ' + fmtDuration(age) + "</span> · " : "sampled <span data-tick=\"age\" data-from=\"" + esc(h.sampled_at || "") + "\">" + esc(fmtAgo(age)) + "</span> · ") + esc(h.os || DASH) + " · " + (isNum(h.cores) ? h.cores + " cores" : DASH) + " · load " + (Array.isArray(h.load) && isNum(h.load[0]) ? h.load[0].toFixed(1) : DASH) + "</span></div>";
    html += meter("cpu", "CPU " + fmtPct(cpu.busy), isNum(cpu.user) && isNum(cpu.sys) ? "user " + Math.round(cpu.user) + " · sys " + Math.round(cpu.sys) : (stale ? "last known" : DASH), cpu.busy, isNum(cpu.sys) ? cpu.sys : 0, isNum(cpu.busy) && cpu.busy >= 85, sparkline(h.history, "cpu_busy"));
    html += meter("mem", "Memory " + fmtMemory(mem.used_bytes) + " / " + fmtMemory(mem.total_bytes), (isNum(memPct) ? fmtPct(memPct) : DASH) + (isNum(mem.compressed_bytes) ? " · comp " + fmtMemory(mem.compressed_bytes) : ""), memPct, compPct, isNum(memPct) && memPct >= 85, sparkline(h.history, "mem_used_bytes"));
    if (gpu) html += meter("gpu", "GPU " + fmtPct(gpu.util_pct) + " busy", isNum(gpu.mem_used_bytes) ? fmtMemory(gpu.mem_used_bytes) + " in use" : DASH, gpu.util_pct, 0, isNum(gpu.util_pct) && gpu.util_pct >= 85, sparkline(h.history, "gpu_util_pct"));
    else html += '<div class="meter" data-metric="gpu"><div class="lab"><span>GPU — ' + esc(h.gpu_note || "unavailable") + "</span><span></span></div></div>";
    if (Array.isArray(h.top) && h.top.length) html += '<div class="top">top: ' + h.top.map(function (t) { return "<b>" + esc(t.comm || DASH) + "</b> " + fmtPct(t.cpu) + " " + fmtMb(t.rss_mb); }).join(" · ") + "</div>";
    body.innerHTML = html + "</div>";
  }

  // ── 렌더: 최근 (항목 14 · 15 · 32) ──
  function renderRecent() {
    var p = pool0(state.status);
    var body = $("[data-recent-body]");
    var head = $("[data-recent-header]");
    if (!p) { body.innerHTML = '<div class="empty">No completed jobs yet</div>'; head.textContent = ""; renderEstimates(p); return; }
    if (p.recent === null || p.recent === undefined) { body.innerHTML = '<div class="banner bad" role="alert" data-error="recent">Recent unavailable — ' + esc(p.recent_error || "unknown error") + "</div>"; head.textContent = ""; renderEstimates(p); return; }
    if (!p.recent.length) { body.innerHTML = '<div class="empty">No completed jobs yet</div>'; head.textContent = ""; renderEstimates(p); return; }
    var all = p.recent;
    var shown = state.showAllRecent ? all : all.slice(0, 5);
    head.textContent = "last " + shown.length + " of " + all.length;
    var html = '<div class="recent">';
    shown.forEach(function (job) {
      var l = recentLine(job, tz(), now());
      var open = !!state.expandedRecent[job.id];
      var failedish = job.state === "failed" || job.state === "timed_out";
      html += '<div class="rrow' + (failedish ? " clickable" : "") + (state.hl === job.id ? " hl" : "") + '" data-job="' + job.id + '"' + (failedish ? ' data-rtoggle="' + job.id + '" role="button" tabindex="0" aria-expanded="' + (open ? "true" : "false") + '"' : "") + ">" +
        '<span class="pill ' + esc(l.cls) + '"><span aria-hidden="true">' + esc(l.glyph) + "</span> " + esc(l.pill) + "</span>" +
        '<span class="k">' + esc(job.key || DASH) + "</span>" +
        '<span class="s">' + esc(truncate((job.requester || {}).label || DASH, 40)) + "</span>" +
        '<span class="d">' + esc(l.duration) + "</span>" +
        '<span class="t">' + esc(l.when) + "</span>" +
        '<span class="s">' + (l.summary ? "<b>" + esc(l.summary.split(" · ")[0]) + "</b>" + esc(l.summary.indexOf(" · ") > 0 ? l.summary.slice(l.summary.indexOf(" · ")) : "") : "") +
        (l.rerun ? ' · <button type="button" class="rerun" data-copy="' + esc(l.rerun) + '" title="copy">⧉ ' + esc(l.rerun) + "</button>" : "") + "</span>";
      if (open) html += '<div class="rdetail">' + esc(transitionsLine(job, tz())) + (job.failed_step ? "<br>failed step: <b>" + esc(job.failed_step) + "</b>" : "") + (job.summary ? "<br>" + esc(job.summary) : "") + "</div>";
      html += "</div>";
    });
    html += "</div>";
    if (all.length > 5) html += '<button type="button" class="more" data-more-recent>' + (state.showAllRecent ? "show fewer ▴" : "show " + (all.length - 5) + " more ▾") + "</button>";
    body.innerHTML = html;
    renderEstimates(p);
  }
  function renderEstimates(p) {
    var sum = $("[data-est-summary]"), body = $("[data-est-body]"), det = $("#estimates");
    if (!p || p.medians === null || p.medians === undefined) { sum.textContent = "Estimates · unavailable"; body.innerHTML = '<span class="bad">' + esc((p && p.medians_error) || "medians unavailable") + "</span>"; return; }
    var keys = Object.keys(p.medians);
    var presets = {};
    (state.status.presets || []).forEach(function (x) { presets[x.name] = x; });
    if (!keys.length) {
      sum.textContent = "Estimates · no samples yet — using preset/default until 2 successful jobs per key";
      det.open = true;
      body.innerHTML = (state.status.presets || []).map(function (x) { return "<span><b>" + esc(x.name) + "</b> " + (isNum(x.expected_seconds) ? "preset " + fmtDuration(x.expected_seconds) : "default") + " · low</span>"; }).join("") || "<span>no presets</span>";
      return;
    }
    sum.textContent = "Estimates · " + keys.length + " key" + (keys.length > 1 ? "s" : "") + " · how ETA is computed";
    body.innerHTML = keys.map(function (k) {
      var m = p.medians[k] || {};
      var n = isNum(m.sample_count) ? m.sample_count : 0;
      var conf = n >= 5 ? "high" : n >= 2 ? "med" : "low";
      var main = n >= 2 ? fmtDuration(m.seconds) : "n=" + n + " → preset";
      return "<span><b>" + esc(k) + "</b> " + esc(main) + (isNum(m.wait_seconds) ? " · wait " + fmtDuration(m.wait_seconds) : "") + " · n=" + n + " · " + conf + "</span>";
    }).join("");
    body.title = "measured n≥5 → high, n<5 → med, preset/default → low";
  }

  // ── 1초 틱 ──
  function tickTexts() {
    var n = now();
    $$("[data-tick]").forEach(function (el) {
      var kind = el.getAttribute("data-tick"), from = parseIso(el.getAttribute("data-from"));
      if (from == null) return;
      var s = (n - from) / 1000;
      if (kind === "elapsed") el.textContent = fmtDuration(s);
      else if (kind === "waiting") el.textContent = "waiting " + fmtDuration(s);
      else if (kind === "age") el.textContent = fmtAgo(s);
    });
    renderHeaderConn();
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
    var row = $('tr[data-job="' + id + '"]') || $('.rrow[data-job="' + id + '"]');
    if (row) {
      renderQueue(); renderRecent();
      var el = $('[data-job="' + id + '"]');
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(function () { if (state.hl === id) { state.hl = null; renderQueue(); renderRecent(); } }, 2500);
      return;
    }
    api("/jobs/" + id).then(function (r) { return r.ok ? r.json() : null; }).then(function (job) {
      if (!job) toast("#" + id + " not found"); else toast("#" + id + " " + stateWord(job.state) + (job.finished_at ? " · finished " + fmtClock(job.finished_at, tz(), now()) : ""));
    }).catch(function () { toast("#" + id + " — could not look up"); });
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
    $("[data-drawer-title]").textContent = "#" + id + (row ? " " + (row.key || "") : "") + " · log";
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
      if (r.status === 401 || r.status === 403) { $("[data-drawer-sub]").textContent = r.status === 401 ? "Add a token to see the log" : "not your job"; return null; }
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
      $("[data-drawer-sub]").textContent = res.more ? "live · " + fmtBytes(res.next) : "finished · " + fmtBytes(res.next);
      if (res.more) state.drawer.timer = setTimeout(pollLog, 2000);
    }).catch(function () { $("[data-drawer-sub]").textContent = "couldn’t load — retrying"; state.drawer.timer = setTimeout(pollLog, 5000); });
  }

  // 취소 (항목 13 · 30)
  function openCancel(id) {
    var row = findRow(id);
    if (!row) return;
    state.cancelTarget = id;
    var req = row.requester || {};
    var joiners = Array.isArray(row.joiners) ? row.joiners.length : 0;
    var isJoiner = state.me && req.name !== state.me;
    $("[data-cancel-title]").textContent = "Cancel #" + id + " " + (row.key || "") + " (" + (req.label || req.name || DASH) + ")?";
    var body;
    if (isJoiner) body = "You joined this job. You will leave the join list; the job keeps running for " + (req.label || req.name || "its requester") + ".";
    else if (row.state === "running") body = "SIGTERM now, SIGKILL after the grace period." + (joiners ? " " + joiners + " other session" + (joiners > 1 ? "s are" : " is") + " waiting on it." : "") + " Cannot be undone.";
    else body = "Removed from the queue. Run rcm run again to resubmit." + (joiners ? " " + joiners + " other session" + (joiners > 1 ? "s are" : " is") + " waiting on it." : "");
    $("[data-cancel-body]").textContent = body;
    $("[data-cancel-go]").textContent = isJoiner ? "Leave" : "Cancel job";
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
      if (!res.ok) { toast("cancel failed: " + ((res.body && res.body.error) || res.status)); if (row) { row._cancelRequested = false; row._dim = false; } if (res.status === 401 || res.status === 403) tokenRejected(); fetchStatus(); return; }
      if (res.body && res.body.left) toast("left the join list of #" + id);
      else toast("#" + id + " " + (res.body && res.body.state ? res.body.state : "cancel requested"));
      setTimeout(function () { fetchStatus(); }, 5000);
      scheduleRefetch();
    }).catch(function () { toast("cancel failed — network"); if (row) { row._cancelRequested = false; row._dim = false; renderQueue(); } });
  }

  function wireClicks() {
    document.addEventListener("click", function (ev) {
      var t = ev.target.closest("[data-goto],[data-toggle],[data-log],[data-cancel],[data-more-queue],[data-more-recent],[data-rtoggle],[data-copy],[data-inputs],[data-src]");
      if (!t) return;
      if (t.hasAttribute("data-goto")) { gotoJob(parseInt(t.getAttribute("data-goto"), 10)); return; }
      if (t.hasAttribute("data-toggle")) {
        var id = parseInt(t.getAttribute("data-toggle"), 10);
        if (state.collapsed[id]) delete state.collapsed[id]; else state.collapsed[id] = true;
        saveCollapsed(); withFocus(renderQueue); return;
      }
      if (t.hasAttribute("data-log")) { state.lastTrigger = t; openDrawer(parseInt(t.getAttribute("data-log"), 10)); return; }
      if (t.hasAttribute("data-cancel")) { state.lastTrigger = t; openCancel(parseInt(t.getAttribute("data-cancel"), 10)); return; }
      if (t.hasAttribute("data-more-queue")) { state.showAllQueue = true; renderQueue(); return; }
      if (t.hasAttribute("data-more-recent")) { state.showAllRecent = !state.showAllRecent; renderRecent(); return; }
      if (t.hasAttribute("data-rtoggle")) { var rid = parseInt(t.getAttribute("data-rtoggle"), 10); if (ev.target.closest("[data-copy]")) return; state.expandedRecent[rid] = !state.expandedRecent[rid]; renderRecent(); return; }
      if (t.hasAttribute("data-copy")) { var text = t.getAttribute("data-copy"); if (navigator.clipboard) navigator.clipboard.writeText(text).then(function () { toast("copied: " + text); }, function () { toast(text); }); else toast(text); return; }
      if (t.hasAttribute("data-inputs")) { var r = findRow(parseInt(t.getAttribute("data-inputs"), 10)); if (r) toast("#" + r.id + " inputs: " + JSON.stringify(r.inputs || {})); return; }
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
    loadCollapsed();
    state.token = lsGet("rcm.token");
    wireTokenDialog(); wireClicks(); renderTokenButton();
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
