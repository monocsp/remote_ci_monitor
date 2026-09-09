/* rcm queue — 문자열 카탈로그. 빌드 도구 없음(app.js 앞에 실린다).

   오너 결정 36·38: 화면은 **한국어가 기본**이고 오른쪽 위에서 영어를 고른다. 브라우저 언어와
   무관하다. 결정 37: 서버가 보내는 것은 코드이고, 문장은 여기서 만든다.

   번역하지 않는 것: 프리셋 이름 · 잡 키 · 요청자 라벨 · 커밋 해시 · ref · 저장소 주소 ·
   동시성 그룹 이름 · 스텝 이름(잡이 찍은 것) · 로그 · `rcm run …` 명령 · 상태 enum 값 자체.

   소요 시간은 두 언어 모두 `5m 10s` 꼴로 둔다. 한국어로 「5분 10초」라고 쓰면 표 칸이 넓어져
   숫자 열이 흔들린다(조사: 숫자 칸은 tabular-nums 로 폭을 고정한다).

   값은 문자열이거나 `(args) -> 문자열` 함수다. 함수로 두면 어순을 언어마다 자유롭게 짤 수 있다 —
   영어의 「조각을 · 로 이어 붙이기」는 한국어에서 그대로 쓸 수 없다. */
(function (root) {
  "use strict";

  var LANGS = ["ko", "en"];
  var DEFAULT_LANG = "ko"; // 결정 38 — 브라우저가 영어여도 기본은 한국어

  var EN = {
    // ── 화면 층(app.js) ─────────────────────────────────────────────────────
    "token.checking": "checking…",
    "token.rejected": "Token rejected",
    "token.rejected_hint": "Token rejected — paste a new one",
    "token.ok": function (a) { return "ok · " + a.name + (a.admin ? " (admin)" : ""); },
    "token.unverified": "🔑 token (unverified)",
    "token.read_auth": "🔑 Read auth required",
    "token.bad_button": "🔑 Token rejected",
    "token.named": function (a) { return "🔑 " + a.name; },
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
    "summary.nothing_stuck": "Nothing is stuck",
    "summary.nothing_else_stuck": "nothing else is stuck",
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
    "queue.more": function (a) { return "and " + a.n + " more ▾"; },
    "queue.group_running": function (a) { return "Running now (" + a.n + ")"; },
    "queue.group_waiting": function (a) { return "Waiting (" + a.n + ")"; },
    "queue.group_none_running": "Nothing is running right now.",
    "queue.group_none_waiting": "Nothing is waiting.",
    "queue.col_job": "Job",
    "queue.col_key": "Key",
    "queue.col_requester": "Requester",
    "queue.col_reason": "Reason",
    "queue.col_elapsed": "Elapsed",
    "queue.col_eta": "ETA",
    "queue.col_source": "Source",
    "queue.pool_jobs": function (a) { return a.head + " · " + a.n + " job" + (a.n === 1 ? "" : "s"); },
    "row.uploading": "↑ uploading",
    "row.cancelling": "■ cancelling…",
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
    "est.to_preset": function (a) { return "n=" + a.n + " → preset"; },
    "est.row": function (a) { return "wait " + a.wait + " · n=" + a.n + " · " + a.conf; },
    "est.how": "measured n≥5 → high, n<5 → med, preset/default → low",
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
    "token.add": "🔑 add token",
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
    "summary.stuck": "Not moving",
    "summary.host": "Host pressure",
    "est.title": "Estimates",
    // ── 상태 ────────────────────────────────────────────────────────────────
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
    "reason.blocked_by": function (a) { return "⛓ blocked by " + a.job + " · " + a.group; },
    "reason.uploading": function (a) { return "uploading · " + a.bytes; },
    "reason.upload_stalled": function (a) { return "upload stalled " + a.since + " · " + a.bytes; },
    "reason.preparing": "preparing workspace",
    "reason.fetching": function (a) { return "fetching " + a.ref; },
    "reason.unpacking": function (a) { return "unpacking " + a.bytes; },
    "reason.over_by": function (a) { return "over by " + a.over + " · expected " + a.expected; },
    "reason.stuck": "⚠ likely stuck",
    "reason.times_expected": function (a) { return a.n + "× expected"; },
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
    "pbar.stuck": "likely stuck",
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
    "recent.none": "No completed jobs yet",
    "recent.unavailable": function (a) { return "Recent unavailable — " + a.error; },
    "recent.count": function (a) { return "last " + a.shown + " of " + a.total; },
    "recent.show_more": function (a) { return "show " + a.n + " more ▾"; },
    "recent.show_fewer": "show fewer ▴",

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
    "outcome.snapshot_rejected": function (a) { return "snapshot rejected: " + a.kind; },
    "outcome.snapshot_blobs_missing": function (a) {
      return "snapshot rejected: " + a.count + " blob(s) missing in upload";
    },
    "outcome.workspace_failed": function (a) { return "workspace failed: " + a.detail; },
    "outcome.exit_code": function (a) { return "exit " + a.code; },
    "outcome.timed_out": function (a) { return a.limit; },
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
    "gpu.sampler_failed": "GPU sampler failed"
  };

  var KO = {
    // ── 화면 층(app.js) ─────────────────────────────────────────────────────
    "token.checking": "확인 중…",
    "token.rejected": "토큰이 거부됐습니다",
    "token.rejected_hint": "토큰이 거부됐습니다 — 새로 붙여 넣으세요",
    "token.ok": function (a) { return "ok · " + a.name + (a.admin ? " (관리자)" : ""); },
    "token.unverified": "🔑 토큰 (미확인)",
    "token.read_auth": "🔑 읽기 인증 필요",
    "token.bad_button": "🔑 토큰 거부됨",
    "token.named": function (a) { return "🔑 " + a.name; },
    "token.kept": "확인하지 못했습니다 — 그대로 둡니다",
    "conn.live": function (a) { return "실시간 · " + a.age + " 갱신"; },
    "conn.polling": function (a) { return "폴링 · " + a.age + " 갱신"; },
    "conn.lost": function (a) { return "끊김 · 마지막 갱신 " + a.age; },
    "conn.paused": "멈춤 · 다시 시작",
    "conn.lost_banner": function (a) {
      return a.host + " 와 연결이 끊겼습니다 · 마지막 갱신 " + a.age + " · 마지막으로 알던 상태를 보여 줍니다";
    },
    "conn.reconnecting": function (a) { return a.countdown + " 뒤 재연결…"; },
    "conn.polling_every": "10초마다 확인 중…",
    "header.lanes_busy": function (a) { return "레인 " + a.busy + "/" + a.lanes + " 사용 중"; },
    "header.error": function (a) { return "오류 · " + a.detail; },
    "header.clock_unknown": "시계 알 수 없음",
    "header.clock_skew": function (a) { return "시계 " + a.delta; },
    "header.paused": function (a) {
      return a.by + " 이(가) " + a.clock + " 에 큐를 멈췄습니다 — 돌던 잡은 끝나고 새 잡은 시작하지 않습니다 · ";
    },
    "header.worker_stopped": function (a) {
      return "레인 " + a.lanes + " 의 워커가 멈췄습니다: " + a.error + " · ";
    },
    "header.lanes_left": function (a) { return "대기 잡은 레인 " + a.lanes + " 만 씁니다"; },
    "header.nothing_can_start": "아무것도 시작할 수 없습니다",
    "header.worker_unreachable": function (a) {
      return "워커 " + a.names + " 가 응답하지 않습니다 — heartbeat 없음 · 그 워커의 실행 중 잡은 유실로 표시됩니다";
    },
    "header.not_scheduled": function (a) {
      return "레인이 비어 있는데 #" + a.id + " 가 " + a.dur + " 동안 시작되지 않았습니다 — 서버 로그를 보세요";
    },
    "footer.server": function (a) {
      return "rcm " + a.version + " · 가동 " + a.uptime + " · 스키마 v" + a.schema;
    },
    "summary.add_token": "토큰을 넣으면 내 잡이 표시됩니다",
    "summary.queue_unknown": "알 수 없음 — 큐를 읽지 못했습니다",
    "summary.no_jobs": "큐에 내 잡이 없습니다",
    "summary.and_more": function (a) { return a.n + "개 더"; },
    "summary.last_known": "마지막으로 알던 값: ",
    "summary.nothing_stuck": "막힌 것 없음",
    "summary.nothing_else_stuck": "다른 막힌 것은 없습니다",
    "summary.host_unavailable": "호스트: 읽지 못함",
    "summary.host_no_sample": "호스트: 아직 표본 없음",
    "summary.host_sampled": function (a) { return "호스트 부하 · " + a.age + " 표본"; },
    "summary.verdict_fine": "여유",
    "summary.verdict_busy": "바쁨",
    "summary.verdict_partial": "일부만 앎",
    "summary.verdict_unknown": "알 수 없음",
    "summary.pressure": function (a) {
      return "CPU " + a.cpu + " · 메모리 " + a.mem + " · 디스크 " + a.disk + " · GPU " + a.gpu;
    },
    "summary.disk_free": function (a) { return a.free + " 남음"; },
    "summary.load": function (a) { return "load " + a.load; },
    "queue.no_status": "큐를 읽지 못했습니다 — 아직 상태가 없습니다",
    "queue.unavailable": function (a) { return "큐를 읽지 못했습니다 — " + a.error; },
    "queue.empty_other_pools": "이 풀의 큐는 비었습니다 — 다른 풀에 잡이 기다립니다.",
    "queue.empty_paused": "큐가 비었지만 멈춰 있습니다 — 아무것도 시작하지 않습니다.",
    "queue.empty": "큐가 비었습니다 — ",
    "queue.empty_hint": " 하면 바로 시작합니다.",
    "queue.presets": function (a) { return "프리셋: " + a.names; },
    "queue.more": function (a) { return a.n + "개 더 보기 ▾"; },
    "queue.group_running": function (a) { return "지금 도는 것 (" + a.n + ")"; },
    "queue.group_waiting": function (a) { return "기다리는 것 (" + a.n + ")"; },
    "queue.group_none_running": "지금 도는 잡이 없습니다.",
    "queue.group_none_waiting": "기다리는 잡이 없습니다.",
    "queue.col_job": "잡",
    "queue.col_key": "키",
    "queue.col_requester": "요청자",
    "queue.col_reason": "이유",
    "queue.col_elapsed": "경과",
    "queue.col_eta": "종료 예상",
    "queue.col_source": "소스",
    "queue.pool_jobs": function (a) { return a.head + " · 잡 " + a.n; },
    "row.uploading": "↑ 업로드 중",
    "row.cancelling": "■ 취소 중…",
    "row.group": function (a) { return "그룹 " + a.name; },
    "row.you": "나",
    "row.you_joined": "합류함",
    "row.also_waiting": function (a) { return "함께 기다리는 사람: " + a.labels; },
    "row.token_of": function (a) { return "토큰: " + a.name; },
    "row.stalled_note": "계속 멈춰 있으면 서버가 취소합니다",
    "row.cancel_requested": "취소 요청함…",
    "row.cancel": "취소",
    "row.log": "로그",
    "row.add_token_for_log": "토큰을 넣으면 로그가 보입니다",
    "row.not_your_job": "내 잡이 아닙니다",
    "row.others_waiting": function (a) { return "다른 세션 " + a.n + "개가 이 잡을 기다립니다"; },
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
    "host.no_sample": "호스트: 아직 표본 없음",
    "host.digest": function (a) { return a.names + " · " + a.state; },
    "host.window": "5분",
    "host.sampled": function (a) { return a.age + " 표본"; },
    "host.cores_load": function (a) { return a.cores + " 코어 · load " + a.load; },
    "host.stale": function (a) { return a.dur + " 지남"; },
    "host.cpu": function (a) { return "CPU " + a.pct; },
    "host.cpu_detail": function (a) { return "user " + a.user + " · sys " + a.sys; },
    "host.memory": function (a) { return "메모리 " + a.used + " / " + a.total; },
    "host.disk": function (a) { return "디스크 " + a.used + " / " + a.total; },
    "host.disk_free": function (a) { return a.free + " 남음"; },
    "host.compressed": function (a) { return "압축 " + a.size; },
    "host.gpu": function (a) { return "GPU " + a.pct + " 사용"; },
    "host.gpu_used": function (a) { return a.size + " 사용 중"; },
    "host.gpu_none": function (a) { return "GPU — " + a.note; },
    "host.last_known": "마지막으로 알던 값",
    "host.top": "무거운 프로세스: ",
    "est.unavailable": function (a) { return "추정 · " + a.error; },
    "est.medians_unavailable": "중앙값을 읽지 못했습니다",
    "est.no_samples": "추정 · 아직 표본이 없습니다 — 키마다 성공한 잡이 2개 모일 때까지 프리셋·기본값을 씁니다",
    "est.keys": function (a) { return "추정 · 키 " + a.n + "개 · ETA 계산 방법"; },
    "est.preset": function (a) { return "프리셋 " + a.dur; },
    "est.default": "기본값",
    "est.no_presets": "프리셋 없음",
    "est.to_preset": function (a) { return "n=" + a.n + " → 프리셋"; },
    "est.row": function (a) { return "대기 " + a.wait + " · n=" + a.n + " · " + a.conf; },
    "est.how": "실측 n≥5 → 높음, n<5 → 보통, 프리셋·기본값 → 낮음",
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
    "cancel.confirm_title": function (a) { return "#" + a.id + " " + a.key + " (" + a.who + ") 를 취소할까요?"; },
    "cancel.joiner_body": function (a) {
      return "이 잡에 합류했습니다. 합류만 빠지고, 잡은 " + a.who + " 를 위해 계속 돕니다.";
    },
    "cancel.running_body": "지금 SIGTERM, 유예 시간 뒤 SIGKILL.",
    "cancel.queued_body": "큐에서 뺍니다. 기다리던 세션은 종료 코드 2를 받습니다. 다시 내려면 rcm run 을 다시 실행하세요.",
    "cancel.cannot_undo": "되돌릴 수 없습니다.",
    "cancel.others_waiting": function (a) { return "다른 세션 " + a.n + "개가 이 잡을 기다립니다."; },
    "cancel.leave": "합류 빠지기",
    // ── 정적 화면(index.html 의 data-i18n) ───────────────────────────────────
    "page.title": "rcm 큐",
    "conn.lost_short": "연결 끊김",
    "conn.pause_hint": "갱신 멈추기",
    "conn.connecting": "연결 중…",
    "lang.hint": "언어 바꾸기",
    "lang.button": "EN",
    "token.add": "🔑 토큰 넣기",
    "token.title": "클라이언트 토큰",
    "token.help": "<code>rcm token add</code> 로 받은 토큰을 붙여 넣으세요. 이 브라우저에만 남고, 내 잡 표시 · 로그 꼬리 · 전체 로그 · 취소가 열립니다.",
    "token.field": "토큰",
    "token.forget": "지우기",
    "dialog.close": "닫기",
    "dialog.save": "저장",
    "cancel.title": "잡을 취소할까요?",
    "cancel.keep": "그대로 두기",
    "cancel.go": "취소하기",
    "drawer.title": "로그",
    "drawer.job": function (a) { return "#" + a.id + (a.key ? " " + a.key : "") + " · 로그"; },
    "drawer.close": "로그 닫기",
    "section.summary": "요약",
    "section.queue": "큐",
    "section.host": "호스트",
    "section.recent": "최근",
    "summary.yours": "내 잡",
    "summary.stuck": "안 움직이는 것",
    "summary.host": "호스트 부하",
    "est.title": "추정",
    "state.running": "실행 중",
    "state.queued": "대기",
    "state.uploading": "업로드 중",
    "state.cancelling": "취소 중",
    "state.succeeded": "성공",
    "state.failed": "실패",
    "state.timed_out": "시간 초과",
    "state.cancelled": "취소됨",
    "state.lost": "유실",
    "state.unknown": "알 수 없음",
    "state.busy": "실행 중",
    "state.idle": "대기",
    "state.down": "끊김",
    "state.held": "부하로 대기",
    "reason.held_by_load": "부하로 대기 — 머신이 바쁘다",
    "hold.cpu_busy": function (a) { return "CPU " + a.cpu + "%"; },
    "hold.no_sample": "표본 없음",
    "hold.cooldown": "잠깐 쉬는 중",
    "time.ago": function (a) { return a.dur + " 전"; },
    "time.in": function (a) { return a.dur + " 뒤"; },
    "time.now": "지금",

    "reason.unknown": "알 수 없음",
    "reason.running": "실행 중",
    "reason.running_lane": function (a) { return "실행 중 · 레인 " + a.lane; },
    "reason.waiting_for_lane": "레인 기다리는 중",
    "reason.lanes_busy": function (a) { return a.busy + "/" + a.lanes + " 사용 중"; },
    "reason.behind": function (a) { return "#" + a.id + " 다음"; },
    "reason.frees_in": function (a) { return a.dur + " 뒤 빔"; },
    "reason.blocked_by": function (a) { return "⛓ " + a.job + " 이(가) 막는 중 · " + a.group; },
    "reason.uploading": function (a) { return "업로드 중 · " + a.bytes; },
    "reason.upload_stalled": function (a) { return "업로드 멈춤 " + a.since + " · " + a.bytes; },
    "reason.preparing": "작업 공간 준비 중",
    "reason.fetching": function (a) { return a.ref + " 받는 중"; },
    "reason.unpacking": function (a) { return a.bytes + " 푸는 중"; },
    "reason.over_by": function (a) { return a.over + " 초과 · 예상 " + a.expected; },
    "reason.stuck": "⚠ 멈춘 듯",
    "reason.times_expected": function (a) { return "예상의 " + a.n + "배"; },
    "reason.no_output_for": function (a) { return a.since + " 동안 출력 없음"; },
    "reason.sigterm": function (a) { return a.by + " 이(가) SIGTERM · " + a.kill + " 뒤 강제 종료"; },
    "reason.paused": "일시정지",
    "reason.not_scheduled": "배정 안 됨",
    "reason.no_worker": "워커 없음",

    "conf.high": "높음",
    "conf.med": "보통",
    "conf.low": "낮음",
    "conf.overdue": "예상 초과",
    "conf.group_wait": "낮음 · 그룹 대기",
    "conf.measured": function (a) { return a.conf + " · 실측 n=" + a.n; },
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
      return "잡 " + a.total + " · 실행 " + a.running + " · 대기 " + a.waiting;
    },
    "queue.oldest_waiting": function (a) { return "가장 오래 기다린 " + a.dur; },
    "queue.lanes_busy": function (a) { return "레인 " + a.busy + "/" + a.lanes + " 사용 중"; },
    "your.eta": function (a) { return a.state + " · 종료 " + a.clock; },
    "your.in_line": function (a) { return a.ordinal + " · 종료 " + a.clock; },
    "your.joined": function (a) { return "합류 +" + a.n; },

    "worker.state": function (a) { return "워커 " + a.state; },
    "worker.lane_state": function (a) { return "레인 " + a.lane + " · " + a.state; },
    "worker.paused": "일시정지",
    "pool.name": function (a) { return "풀 " + a.name; },
    "pool.no_workers": "워커 없음",
    "host.pool": function (a) { return a.host + " · 풀 " + a.pool; },

    "note.stale_ui": "화면이 옛 버전입니다 — 새로고침",
    "note.restarted": function (a) {
      return a.clock + " 에 서버가 재시작했습니다 — 그때 돌던 잡은 유실로 표시됩니다";
    },

    "progress.no_markers": function (a) { return "스텝 마커 없음 · 잡 " + a.dur; },
    "progress.step": function (a) {
      return "스텝 " + a.cur + "/" + a.total + (a.soFar ? " (지금까지)" : "");
    },
    "progress.job": function (a) { return "잡 " + a.dur; },
    "progress.steps_failed": function (a) { return "스텝 " + a.n + "개 실패"; },
    "progress.timing_note": "스텝 시각은 서버가 받은 시각입니다 (as_received)",
    "progress.now": function (a) { return "스텝 " + a.cur + "/" + a.total + " " + a.step; },
    "pbar.steps": function (a) { return a.percent + "% · 스텝 " + a.done + "/" + a.total; },
    "pbar.steps_all": function (a) { return "스텝 " + a.done + "/" + a.total; },
    "pbar.time": function (a) { return a.percent + "% · 예상 시간 기준"; },
    "pbar.time_measured": function (a) { return a.percent + "% · 측정 소요 기준"; },
    "pbar.time_preset": function (a) { return a.percent + "% · 프리셋 예상 기준"; },
    "pbar.over": "예상 시간 초과",
    "pbar.stuck": "멈춘 듯",
    "pbar.preparing": "준비 중",
    "pbar.finalizing": "마무리 중",
    "pbar.none": "진행률 —",
    "pbar.aria": function (a) { return "#" + a.id + " 진행"; },

    "recent.succeeded": "성공",
    "recent.failed": "실패",
    "recent.failed_exit": function (a) { return "실패 · 종료 코드 " + a.code; },
    "recent.cancelled": "취소됨 · 종료 코드 2",
    "recent.timed_out": "시간 초과 · 종료 코드 2",
    "recent.lost": "유실 · 종료 코드 3",
    "recent.before_start": "시작 전",
    "recent.by": function (a) { return a.who + " 이(가)"; },
    "recent.step": function (a) { return "스텝 " + a.step; },

    // ── 잡 산출물(M5e) ────────────────────────────────────────────────────
    "art.label": "산출물",
    "art.state.ready": "받을 수 있음",
    "art.state.empty": "모은 것 없음",
    "art.state.dropped": "버림",
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
    "art.reason.not_run": "잡이 시작되지 못함",
    "art.reason.interrupted": "중단됨",
    "art.reason.acked": "받아 감",
    "art.reason.expired": "기간 지남",
    "art.copy": "받아 가는 명령 복사",
    "recent.failed_step": "실패한 스텝: ",
    "recent.none": "아직 끝난 잡이 없습니다",
    "recent.unavailable": function (a) { return "최근 결과를 읽지 못했습니다 — " + a.error; },
    "recent.count": function (a) { return a.total + "개 중 " + a.shown + "개"; },
    "recent.show_more": function (a) { return a.n + "개 더 보기 ▾"; },
    "recent.show_fewer": "접기 ▴",

    "priority.high": "높음",
    "priority.low": "낮음",
    "cache.text": function (a) { return "캐시 " + a.blobs + "개 · " + a.size; },
    "transitions.waited": function (a) { return "(" + a.dur + " 기다림)"; },
    "transitions.exit": function (a) { return "종료 코드 " + a.code; },

    "outcome.cancelled_before_start": "시작 전에 취소됨",
    "outcome.cancelled_by": function (a) { return a.by + " 이(가) 취소함"; },
    "outcome.server_restarted": function (a) { return a.at + " 서버 재시작"; },
    "outcome.server_restarted_during_upload": "업로드 중 서버 재시작",
    "outcome.server_stopped_while_running": "실행 중 서버가 멈춤",
    "outcome.upload_abandoned": function (a) { return a.seconds + " 동안 업로드 없음 — 버림"; },
    "outcome.upload_interrupted": function (a) { return a.bytes + " 에서 업로드 끊김"; },
    "outcome.snapshot_too_big": function (a) {
      return "스냅샷 " + a.bytes + " 가 상한 " + a.limit + " 을 넘음";
    },
    "outcome.snapshot_rejected": function (a) { return "스냅샷 거부: " + a.kind; },
    "outcome.snapshot_blobs_missing": function (a) {
      return "스냅샷 거부: 업로드에 파일 " + a.count + "개가 빠짐";
    },
    "outcome.workspace_failed": function (a) { return "작업 공간 실패: " + a.detail; },
    "outcome.exit_code": function (a) { return "종료 코드 " + a.code; },
    "outcome.timed_out": function (a) { return a.limit; },
    "outcome.worker_error": function (a) { return "워커 오류: " + a.detail; },
    "outcome.worker_failed": "워커에서 실패",
    "outcome.worker_stopped_while_running": "실행 중 워커가 멈춤",
    "outcome.worker_restarted_without_job": function (a) {
      return "워커 " + a.name + " 이(가) 그 잡 없이 재시작함";
    },
    "outcome.worker_unreachable": function (a) {
      return "워커 " + a.name + " 이(가) " + a.seconds + "초 동안 응답 없음";
    },
    "outcome.cancel_unconfirmed": "워커가 취소를 확인하지 않음",

    "error.internal_error": "내부 오류",
    "error.database_unavailable": "데이터베이스를 쓸 수 없음",
    "error.sampler_failed": "표본 수집 실패",
    "gpu.no_sampler": "GPU 수집기 없음",
    "gpu.no_gpu": "GPU 없음",
    "gpu.sampler_failed": "GPU 수집 실패"
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
    t: t, has: has, normalize: normalize, ordinalSuffix: ordinalSuffix
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof globalThis !== "undefined") globalThis.rcmI18n = api;
  else if (root) root.rcmI18n = api;
})(this);
