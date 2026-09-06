"""스키마 v1 JSON(dict) → 터미널 문자열(`rcm top`). 웹 UI 와 같은 표기 규칙을 쓴다.

표기(목업 4절): 소요·잔여·대기는 `12s` · `5m 10s` · `1h 02m`, 시각은 `HH:MM`, 모름은 `—`.
빈 큐와 조회 실패는 다른 모양이다. 긍정 문구는 조회 성공 + 값 완전일 때만.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    FAILED,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    UPLOADING,
)
from remote_ci_monitor.core.queue import confidence
from remote_ci_monitor.core.status import parse_iso

DASH = "—"
_GLYPH = {
    RUNNING: "▶",
    CANCELLING: "■",
    QUEUED: "·",
    UPLOADING: "↑",
    SUCCEEDED: "✅",
    FAILED: "❌",
    TIMED_OUT: "⏱",
    CANCELLED: "■",
    LOST: "?",
}
_STATE_WORD = {TIMED_OUT: "timed out"}


def fmt_duration(seconds: float | None) -> str:
    """`12s` · `5m 10s` · `1h 02m`. 모름은 `—`."""
    if seconds is None:
        return DASH
    s = int(round(seconds))
    if s < 0:
        s = 0
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def fmt_clock(iso_text: str | None, tz: tzinfo | None, *, now: datetime | None = None) -> str:
    """`HH:MM`. 오늘이 아니면 `Sep 3 · 23:40`. 모름은 `—`."""
    dt = parse_iso(iso_text)
    if dt is None:
        return DASH
    local = dt.astimezone(tz) if tz else dt
    if now is not None:
        today = (now.astimezone(tz) if tz else now).date()
        if local.date() != today:
            return f"{local.strftime('%b')} {local.day} · {local.strftime('%H:%M')}"
    return local.strftime("%H:%M")


def _pct(v: float | None) -> str:
    return DASH if v is None else f"{int(round(v))}%"


def _gb(b: int | None) -> str:
    # GiB 로 나눈다 — Activity Monitor·`free -h` 와 같은 눈금이라 24 GB 기계가 24 GB 로 보인다
    return DASH if b is None else f"{b / 2**30:.1f} GB"


def _load(v: float | None) -> str:
    # os.getloadavg() 는 이진 소수(6.60693359375)라 두 자리로 자른다
    return DASH if v is None else f"{v:.2f}"


def _state_word(state: str) -> str:
    return _STATE_WORD.get(state, state)


def _tz_from(status: dict[str, Any], tz: tzinfo | None) -> tzinfo | None:
    if tz is not None:
        return tz
    name = status.get("display_timezone")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001 — 이름이 틀려도 렌더는 멈추지 않는다(UTC 로)
            return None
    return None


def _reason_text(row: dict[str, Any]) -> str:
    reason = row.get("reason")
    est = row.get("estimate") or {}
    blocked = row.get("blocked_by")
    if reason == "blocked_by_group" and blocked:
        rem = fmt_duration(blocked.get("remaining_seconds"))
        return f"⛓ blocked by #{blocked['job_id']} · {blocked['group']} · frees in {rem}"
    if reason == "waiting_for_lane":
        ahead = row.get("ahead_job_id")
        return f"waiting for lane · behind #{ahead}" if ahead else "waiting for lane"
    if reason == "overdue":
        over = (est.get("elapsed_seconds") or 0) - (est.get("expected_seconds") or 0)
        return (
            f"over by {fmt_duration(over)} · expected {fmt_duration(est.get('expected_seconds'))}"
        )
    if reason == "stuck":
        return "⚠ likely stuck"
    if reason == "upload_stalled":
        src = row.get("source") or {}
        return f"upload stalled · {_mb(src.get('received_bytes'))} / {_mb(src.get('bytes'))}"
    if reason == "uploading":
        src = row.get("source") or {}
        return f"uploading · {_mb(src.get('received_bytes'))} / {_mb(src.get('bytes'))}"
    if reason == "materializing":
        return "preparing workspace"
    if reason == "cancelling":
        c = row.get("cancel") or {}
        return f"cancelling · by {c.get('by', '?')}"
    if reason == "paused":
        return "paused"
    if reason == "worker_down":
        return "no worker"
    if reason == "not_scheduled":
        return "not scheduled"
    if reason == "running":
        lane = row.get("lane")
        return f"running · lane {lane}" if lane else "running"
    return reason or "unknown"


def _mb(b: int | None) -> str:
    return DASH if b is None else f"{int(round(b / 1e6))} MB"


def _source_text(src: dict[str, Any]) -> str:
    if not src:
        return DASH
    if src.get("mode") == "git_ref":
        sha = (src.get("sha") or "")[:7] or DASH
        return f"{src.get('repo') or ''} @{sha} ref {src.get('ref')}".strip()
    sha = (src.get("base_sha") or "")[:7]
    if not sha and src.get("received_bytes") is None:
        return "not received yet"
    dirty = "+uncommitted" if src.get("dirty") else ""
    repo = src.get("repo") or ""
    return f"{repo} @{sha or DASH}{dirty}".strip()


def render_queue_row(row: dict[str, Any], tz: tzinfo | None, now: datetime | None) -> list[str]:
    est = row.get("estimate") or {}
    state = row["state"]
    glyph = _GLYPH.get(state, "·")
    pos = f"{row['position']}." if row.get("position") else "  "
    req = (row.get("requester") or {}).get("label") or "?"
    joiners = row.get("joiners") or []
    if joiners:
        req += f" +{len(joiners)}"
    src = _source_text(row.get("source") or {})
    if state in (RUNNING, CANCELLING):
        timing = f"elapsed {fmt_duration(est.get('elapsed_seconds'))}"
        if est.get("waited_seconds"):
            timing += f" · waited {fmt_duration(est.get('waited_seconds'))}"
    elif state == UPLOADING:
        timing = "elapsed —"
    else:
        timing = f"waiting {fmt_duration(est.get('waited_seconds'))}"
    finish = est.get("finish_at")
    conf = confidence(
        est.get("source") or "default",
        est.get("sample_count") or 0,
        group_wait=row.get("reason") == "blocked_by_group",
        overdue=bool(est.get("overdue")) or bool(est.get("stuck")),
    )
    if finish:
        eta = f"eta {fmt_clock(finish, tz, now=now)}"
        if est.get("wait_seconds") is not None and state not in (RUNNING, CANCELLING):
            total = (est.get("wait_seconds") or 0) + (est.get("remaining_seconds") or 0)
            eta += f" · in {fmt_duration(total)}"
        elif est.get("remaining_seconds") is not None:
            eta += f" · in {fmt_duration(est.get('remaining_seconds'))}"
    else:
        eta = f"eta {DASH}"
    n = est.get("sample_count") or 0
    conf_text = f"{conf} · {est.get('source')}" + (
        f" n={n}" if est.get("source") == "measured" else ""
    )
    prio = row.get("priority") or 0
    arrow = "↑" if prio > 0 else ("↓" if prio < 0 else "")  # 우선순위는 이유가 아니다 — 표시만
    lines = [
        f"  {pos:>3} {glyph} {_state_word(state):<10} {arrow}#{row['id']} "
        f"{row.get('key', '?'):<16} {src:<28} ← {req:<18} {timing:<24} {eta}  ({conf_text})",
        f"        {_reason_text(row)}",
    ]
    prog = row.get("progress")
    if prog and prog.get("phase") == "executing":
        total = prog.get("steps_total")
        cur = prog.get("current_index")
        if prog.get("steps"):
            head = f"step {cur or prog.get('steps_done')}/{total if total is not None else '?'}"
            if prog.get("steps_total_partial"):
                head += " (so far)"
            if prog.get("current_name"):
                head += f" · {prog['current_name']} · {fmt_duration(prog.get('current_seconds'))}"
            head += f" · job {fmt_duration(prog.get('job_seconds'))}"
            if prog.get("failed_step"):
                head += f" · ✘ {prog['failed_step']}"
            lines.append("        " + head)
            parts = []
            for s in prog["steps"]:
                mark = "▶" if s["state"] == "running" else ("✘" if s.get("ok") is False else "✔")
                parts.append(f"{mark} {s['name']} {fmt_duration(s.get('seconds'))}")
            lines.append("        " + "  ".join(parts))
        else:
            lines.append(f"        no step markers · job {fmt_duration(prog.get('job_seconds'))}")
    for line in row.get("log_tail") or []:
        lines.append("        " + line)
    return lines


def render(
    status: dict[str, Any], *, tz: tzinfo | None = None, host_name: str | None = None
) -> str:
    """StatusModel JSON → 사람용 텍스트. 섹션마다 실패는 실패로, 빈 값은 빈 값으로 그린다."""
    tz = _tz_from(status, tz)
    now = parse_iso(status.get("generated_at"))
    server = status.get("server") or {}
    pools = status.get("pools") or []
    pool = pools[0] if pools else {}
    hosts = pool.get("hosts")
    name = host_name or ((hosts or [{}])[0].get("name") if hosts else None) or "server"
    workers = server.get("workers") or []
    busy = sum(1 for w in workers if w.get("state") == "busy")
    down = [w for w in workers if w.get("state") == "down"]
    lanes = server.get("lanes") or len(workers) or 0
    if lanes == 1 and workers:
        w = workers[0]
        wtxt = f"worker {w.get('state')}" + (f" #{w['job_id']}" if w.get("job_id") else "")
    else:
        wtxt = f"lanes {busy}/{lanes} busy"
    if down:
        wtxt += f" · DOWN: lane {', '.join(str(w['lane']) for w in down)}"
    if server.get("paused"):
        wtxt += f" · PAUSED by {server['paused'].get('by')}"
    cache = server.get("snapshot_cache")
    if isinstance(cache, dict):  # 캐시가 켜져 있으면 모르는 숫자는 — 로(0 이 아니다)
        blobs = cache.get("blobs")
        wtxt += f" · cache {DASH if blobs is None else blobs} blobs · {_mb(cache.get('bytes'))}"
    clock = fmt_clock(status.get("generated_at"), tz)
    tzname = status.get("display_timezone") or "local"
    out = [f"━━━ rcm · {name} · {clock} {tzname} · {wtxt}"]
    if server.get("notify_failures"):
        out.append(f"  notify failures {server['notify_failures']} · see the server log")
    if server.get("last_error"):
        out.append(f"  error · {str(server['last_error'])[:60]}")

    queue = pool.get("queue")
    if queue is None:
        out.append(f"queue — unavailable: {pool.get('queue_error') or 'unknown error'}")
    elif not queue:
        if server.get("paused") or (workers and all(w.get("state") == "down" for w in workers)):
            out.append("queue — empty but paused/no worker — nothing will start")
        else:
            out.append("queue — empty (rcm run <preset> starts immediately)")
    else:
        running = sum(1 for r in queue if r["state"] in (RUNNING, CANCELLING))
        waiting = len(queue) - running
        out.append(f"queue — {len(queue)} jobs · {running} running · {waiting} waiting")
        for row in queue:
            out.extend(render_queue_row(row, tz, now))

    recent = pool.get("recent")
    if recent is None:
        out.append(f"recent — unavailable: {pool.get('recent_error') or 'unknown error'}")
    elif not recent:
        out.append("recent — no completed jobs yet")
    else:
        out.append("recent")
        for r in recent:
            glyph = _GLYPH.get(r["state"], "?")
            # 웹과 같은 규칙: 프로세스 종료 코드는 failed 에만(취소·타임아웃의 -15/-9 는 신호일 뿐)
            show_exit = r.get("state") == "failed" and r.get("exit_code") is not None
            exit_txt = f" · exit {r['exit_code']}" if show_exit else ""
            req = (r.get("requester") or {}).get("label") or "?"
            tail = r.get("summary") or ""
            if r.get("failed_step"):
                tail += f" (step {r['failed_step']})"
            when = fmt_clock(r.get("finished_at"), tz, now=now)
            dur = fmt_duration(r.get("job_seconds"))
            out.append(
                f"  {glyph} {_state_word(r['state'])}{exit_txt} {r.get('key', '?'):<16} "
                f"← {req:<18} {dur:>8}  {when}  {tail}".rstrip()
            )

    medians = pool.get("medians")
    if medians is None:
        out.append(f"medians — unavailable: {pool.get('medians_error') or 'unknown error'}")
    elif medians:
        parts = [
            f"{k} {fmt_duration(m.get('seconds'))} (n={m.get('sample_count')})"
            for k, m in medians.items()
        ]
        out.append("medians: " + " · ".join(parts))
    else:
        out.append("medians: no samples yet — using preset/default")

    if hosts is None:
        out.append(f"host — unavailable: {pool.get('hosts_error') or 'unknown error'}")
    elif not hosts:
        out.append("host — no sample yet")
    else:
        for h in hosts:
            age = fmt_duration(h.get("age_seconds"))
            stale = " · STALE" if h.get("stale") else ""
            cpu = (h.get("cpu") or {}).get("busy")
            mem = h.get("memory") or {}
            gpu = h.get("gpu") or {}
            load = h.get("load") or [None]
            cores = h.get("cores") if h.get("cores") is not None else DASH
            out.append(
                f"host  {h.get('name')} ({age} ago{stale})  load {_load(load[0])}"
                f" / {cores} cores · CPU {_pct(cpu)}"
                f" · mem {_gb(mem.get('used_bytes'))} / {_gb(mem.get('total_bytes'))}"
                f" · GPU {_pct(gpu.get('util_pct')) if gpu else DASH}"
            )
            top = h.get("top") or []
            if top:
                out.append(
                    "      top: "
                    + " · ".join(
                        f"{t.get('comm')} {_pct(t.get('cpu'))} {t.get('rss_mb')}MB" for t in top
                    )
                )
    return "\n".join(out) + "\n"
