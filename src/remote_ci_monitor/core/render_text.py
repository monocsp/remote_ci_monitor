"""스키마 v1 JSON(dict) → 터미널 문자열(`rcm top`). 웹 UI 와 같은 표기 규칙을 쓴다.

표기(목업 4절): 소요·잔여·대기는 `12s` · `5m 10s` · `1h 02m`, 시각은 `HH:MM`, 모름은 `—`.
빈 큐와 조회 실패는 다른 모양이다. 긍정 문구는 조회 성공 + 값 완전일 때만.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    FAILED,
    LOST,
    MODE_GIT_REF,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    UPLOADING,
)
from remote_ci_monitor.core.queue import confidence
from remote_ci_monitor.core.status import parse_iso

MAX_REMOTE_PILLS = 5  # 머리줄의 원격 워커 필 상한(M5b-4). down 은 세지 않는다
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


def _fmt_bytes(n: int) -> str:
    """산출물 크기 — 사람이 읽는 짧은 문면. 0 은 0 으로 찍는다(모르는 것과 다르다)."""
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _artifacts_line(art: Mapping[str, Any] | None) -> str:
    """최근 결과 한 줄 아래에 붙는 산출물 줄(명세 §13).

    모르는 수는 `—` 다. **`0` 은 「모았는데 없었다」일 때만** — `unknown` 과 `empty` 는 화면에서도
    구분된다. 파일 이름은 여기 없다(공개 문서에 경로가 없다, §10).
    """
    if not art:
        return ""
    state = art.get("state")
    if state in (None, "disabled", "pending"):
        return ""
    count = art.get("file_count")
    size = art.get("bundle_bytes")
    files = DASH if count is None else f"{count} files"
    bytes_txt = DASH if size is None else _fmt_bytes(size)
    reason = art.get("reason_code")
    tail = f" · {reason}" if reason else ""
    return f"artifacts {state} · {files} · {bytes_txt}{tail}"


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


def _disk(d: dict[str, Any] | None) -> str:
    """「120 / 460 GB (340 GB free)」.

    디스크는 십진 GB 로 센다 — Finder·`df -H` 와 같은 눈금이다(메모리는 GiB 라 `_gb` 와 다르다).
    """
    if not d:
        return DASH
    used, total, free = d.get("used_bytes"), d.get("total_bytes"), d.get("free_bytes")
    if used is None or total is None:
        return DASH
    tail = f" ({free / 10**9:.0f} GB free)" if free is not None else ""
    return f"{used / 10**9:.0f} / {total / 10**9:.0f} GB{tail}"


def _bytes(b: int | None) -> str:
    """디스크 바이트 — 십진 눈금(Finder·`df -H` 와 같다)이되 **크기에 맞는 단위**로.

    GB 로만 그리면 50 KB 짜리 스냅샷이 `0.0 GB` 가 되어 「없다」로 읽힌다. 회수 목록은 큰 것과
    작은 것이 한 표에 섞이는 자리다. **모르는 값은 대시다** — 0 으로 그리면 「지키고 있다」는
    거짓말이 된다.
    """
    if b is None:
        return DASH
    if b < 10**3:
        return f"{b} B"  # 0 은 「없다」는 사실이다 — `0.0 KB` 는 눈을 미끄러뜨린다
    if b < 10**6:
        return f"{b / 10**3:.1f} KB"
    if b < 10**9:
        return f"{b / 10**6:.1f} MB"
    return f"{b / 10**9:.1f} GB"


def _next_sweep(doc: dict[str, Any], now: str | None) -> str:
    """「next sweep in 42m」. 시각을 모르거나 `now` 가 없으면 빈 문자열."""
    at = doc.get("next_sweep_at")
    if not at or not now:
        return ""
    try:
        left = (parse_iso(at) - parse_iso(now)).total_seconds()
    except (TypeError, ValueError):
        return ""
    return f" · next sweep in {fmt_duration(max(0.0, left))}"


def _coarse(seconds: float) -> str:
    """`12s` · `57m` · `3h` — 웹의 `fmtCoarse` 와 같은 눈금(분·시는 내림). 나이에 초는 소음이다."""
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def _measured_ago(doc: dict[str, Any], now: str | None) -> str:
    """「 · measured 57m ago」 — 회계의 나이(I6). 합계에 기여한 측정 중 가장 오래된 것의 나이라
    캐시가 섞인 값을 「방금 쟀다」처럼 그리지 않는다. 시각을 모르거나 `now` 가 없으면 빈 문자열."""
    at = doc.get("measured_at")
    if not at or not now:
        return ""
    try:
        age = (parse_iso(now) - parse_iso(at)).total_seconds()
    except (TypeError, ValueError):
        return ""
    return f" · measured {_coarse(max(0.0, age))} ago"


def storage_row(doc: dict[str, Any], *, now: str | None) -> tuple[str, bool | None, str] | None:
    """`rcm check` 의 `storage` 행 — (이름, ok, 설명). 영어다(CLI 규칙).

    등급은 **다음 sweep 이 고칠 수 있나**로 가른다. 고칠 수 있으면 warn, 사람이 와야 하면 FAIL.
    삭제가 효과 없었던 상황을 warn 으로 숨기지 않는다.

    **아직 한 번도 안 잰 서버는 행이 없다**(None). 「못 쟀다」와 「아직 안 쟀다」는 다른 사실이고,
    막 뜬 서버를 경고로 그리면 첫 화면이 늘 노랗다. 청소기가 영영 안 도는 것은 `/api/health` 가
    503 으로 잡고 그건 `server` 행에 나온다.
    """
    if doc.get("measured_at") is None and not doc.get("error_code"):
        return None
    volume, free = doc.get("volume_bytes"), doc.get("free_bytes")
    limit, floor = doc.get("limit_bytes"), doc.get("min_free_bytes")
    under_floor = floor is not None and free is not None and free < floor

    if doc.get("no_progress"):
        return (
            "storage",
            False,
            f"{_bytes(free)} free: deleting stopped helping — free space did not move, "
            "so the floor rule is paused until `rcm gc`",
        )
    if doc.get("budget_unreachable"):
        held = doc.get("non_evictable_bytes")
        return (
            "storage",
            False,
            f"{_bytes(volume)} over the {_bytes(limit)} budget, and {_bytes(held)} of it is held "
            "by running jobs and orphan directories — nothing the sweep may delete brings it under",
        )
    if under_floor and not doc.get("evictable_bytes"):
        return (
            "storage",
            False,
            f"{_bytes(free)} free, under the {_bytes(floor)} floor, and nothing left to delete",
        )
    if doc.get("error_code") or volume is None:
        return (
            "storage",
            None,
            "a size could not be measured — the byte rules are not enforced this sweep "
            f"({doc.get('error_code') or 'unknown'})",
        )
    if limit is not None and volume > limit:
        return (
            "storage",
            None,
            f"rcm data {_bytes(volume)} is over the {_bytes(limit)} budget — "
            f"the next sweep will trim it{_measured_ago(doc, now)}",
        )
    if under_floor:
        return (
            "storage",
            None,
            f"{_bytes(free)} free, under the {_bytes(floor)} floor — the next sweep will reclaim",
        )
    of_limit = f" of {_bytes(limit)}" if limit is not None else ""
    tail = _measured_ago(doc, now) + _next_sweep(doc, now)
    return "storage", True, f"rcm data {_bytes(volume)}{of_limit} · {_bytes(free)} free{tail}"


def _planned_totals(items: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """계획 항목들의 (charged 합, 공유 몫 합, 예상 회수량 합, 크기를 모르는 항목 수).

    아는 것만 더한다 — 모르는 항목은 0 으로 세지 않고 **개수로** 따로 말한다. `shared_bytes` 가 없는
    옛 서버의 항목은 공유 몫을 모르는 것이 아니라 그 서버가 안 갈랐던 것이라 charged 를 회수량으로
    쓴다(그 서버의 dry-run 이 그렇게 말했다).
    """
    charged = shared = reclaim = unknown = 0
    for i in items:
        ws, tar = i.get("workspace_bytes"), i.get("snapshot_bytes")
        known = (ws or 0) + (tar or 0)
        charged += known
        if ws is None or tar is None:
            unknown += 1
        if "shared_bytes" not in i:
            reclaim += known
            continue
        s, r = i.get("shared_bytes"), i.get("estimated_reclaimable_bytes")
        if s is None or r is None:
            if ws is not None and tar is not None:
                unknown += 1
            continue
        shared += s
        reclaim += r
    return charged, shared, reclaim, unknown


def render_gc(body: dict[str, Any]) -> str:
    """`rcm gc` 사람용 표. **계획과 결과를 가른다** — 계획을 회수처럼 쓰면 거짓이다.

    「would free」는 **예상 회수량**(charged − 하드링크 공유 몫)이다(결정 76). 표의 항목 값과
    「would remain」은 charged 다 — 인벤토리가 링크마다 세기 때문이다.
    """
    planned = body.get("planned") or []
    lines = ["job     workspace   snapshot   reason"]
    for item in planned:
        lines.append(
            f"#{item.get('job_id'):<6} {_bytes(item.get('workspace_bytes')):>9}  "
            f"{_bytes(item.get('snapshot_bytes')):>9}   {item.get('reason', DASH)}"
        )
    if not planned:
        lines.append("(nothing to reclaim)")
    before = body.get("storage_before") or {}
    after = body.get("storage_after") or {}
    charged, shared, reclaim, unknown = _planned_totals(planned)
    if body.get("dry_run"):
        # dry-run 의 「남는다」는 **예측**이다. 지금 총량을 「남을 양」이라고 쓰면 거짓이 된다.
        line = f"would free {_bytes(reclaim)} from {len(planned)} jobs"
        if shared:
            line += f" ({_bytes(charged)} charged · {_bytes(shared)} shared by hard links)"
        if unknown:
            line += f" · {unknown} of unknown size"
        held = before.get("volume_bytes")
        rest = DASH if held is None else _bytes(max(0, held - charged))
        lines.append(f"{line} · {rest} would remain")
        return "\n".join(lines)
    failed = body.get("failed") or []
    gone = len(body.get("deleted") or [])
    freed = body.get("freed_bytes")
    line = f"freed {_bytes(freed)} from {gone} jobs"
    est = body.get("estimated_reclaimable_bytes")
    if est is not None and est != freed:
        line += f" · est. {_bytes(est)} reclaimable"
    fb, fa = body.get("free_bytes_before"), body.get("free_bytes_after")
    if fb is not None and fa is not None:
        line += f" · free {_bytes(fb)} → {_bytes(fa)}"
    line += f" · {_bytes(after.get('volume_bytes'))} left"  # 지운 뒤 다시 잰 값
    if failed:
        codes = ", ".join(sorted({f.get("error_code", "?") for f in failed}))
        line += f" · {len(failed)} failed ({codes})"
    lines.append(line)
    return "\n".join(lines)


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


#: 보류 사유를 사람 말로. 서버는 코드만 보낸다(결정 37).
_HOLD_WORD = {"cpu_busy": "cpu", "no_sample": "no sample", "cooldown": "cooling down"}


def held_summary(workers: list[dict[str, Any]] | None) -> tuple[int, str | None]:
    """보류 레인 수와 사람이 읽을 이유. 숫자는 `cpu_busy` 로 막힌 레인의 **최댓값**이다.

    `hold_detail` 은 `server.workers[]` 에만 있고 큐 행에는 없다 — 그래서 행 키를 늘리지 않고
    렌더러가 이미 받는 워커 목록에서 읽는다(M5f §5.4).
    """
    held = [w for w in (workers or []) if w.get("state") == "held"]
    if not held:
        return 0, None
    busy = [
        v
        for w in held
        if w.get("hold_code") == "cpu_busy"
        and (v := (w.get("hold_detail") or {}).get("cpu_busy")) is not None
    ]
    if busy:
        return len(held), f"cpu {max(busy):.0f}%"
    codes = [w.get("hold_code") for w in held if w.get("hold_code")]
    return len(held), _HOLD_WORD.get(codes[0], codes[0]) if codes else None


def _reason_text(row: dict[str, Any], workers: list[dict[str, Any]] | None = None) -> str:
    reason = row.get("reason")
    est = row.get("estimate") or {}
    blocked = row.get("blocked_by")
    if reason == "blocked_by_group" and blocked:
        rem = fmt_duration(blocked.get("remaining_seconds"))
        return f"⛓ blocked by #{blocked['job_id']} · {blocked['group']} · frees in {rem}"
    if reason == "waiting_for_lane":
        ahead = row.get("ahead_job_id")
        return f"waiting for lane · behind #{ahead}" if ahead else "waiting for lane"
    if reason == "held_by_load":
        _n, why = held_summary(workers)
        return f"held by load · {why}" if why else "held by load"
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
        return "worker down"
    if reason == "not_scheduled":
        return "not scheduled"
    if reason == "running":
        lane = row.get("lane")
        return f"running · lane {lane}" if lane else "running"
    return reason or "unknown"


def _mb(b: int | None) -> str:
    return DASH if b is None else f"{int(round(b / 1e6))} MB"


#: 목록 한 칸의 폭. 브랜치 이름은 길다 — 자르되 잘렸다고 말한다.
MAX_IDENT = 32

#: 실패 보고의 문구(M5h §2.5). 물음표는 계약이다 — 판정이 아니라 제안이다.
_HISTORY = {
    "persistent": "every one of the last {window} {key}runs",
    "intermittent": "{seen} of the last {window} {key}runs · intermittent?",
    "first_seen": "first time in the last {window} {key}runs",
    "unknown": "{seen} of {window} {key}runs so far — too few to judge",
}


#: 최근 줄의 스텝 라벨 상한. 실제 게이트의 스텝 이름은 60자가 넘는다 — `rcm top` 은 한 화면이다.
MAX_STEP_LABEL = 40


def _short_key(key: Any) -> str:
    text = str(key or "?")
    return text if len(text) <= 16 else text[:15] + "…"


def _short_step(name: Any) -> str:
    text = str(name or "")
    return text if len(text) <= MAX_STEP_LABEL else text[: MAX_STEP_LABEL - 1] + "…"


def _short_sha(sha: Any) -> str:
    return str(sha or "")[:7]


def _repo_piece(repo: Any) -> str:
    """저장소 주소의 마지막 조각. `git@github.com:org/app.git` → `app`(칸이 좁다)."""
    text = str(repo or "").rstrip("/")
    if not text:
        return ""
    piece = text.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return piece[:-4] if piece.endswith(".git") else piece


def source_ident(src: dict[str, Any] | None) -> str:
    """목록 한 칸용 짧은 코드 신원 — `<ref|branch> @<짧은 sha>`(M5h §4.1).

    큐 행의 `_source_text()` 와 다른 함수다: 저 쪽은 넓고 이 쪽은 좁다. 못 채우면 조각만
    내고, 아무것도 없으면 `—` 다(빈 문자열이면 그 다음 칸이 앞으로 밀린다).
    """
    src = src or {}
    if src.get("mode") == MODE_GIT_REF:
        sha = _short_sha(src.get("sha"))
        parts = [str(src.get("ref") or ""), f"@{sha}" if sha else ""]
    else:
        name = str(src.get("branch") or "") or _repo_piece(src.get("repo"))
        sha = _short_sha(src.get("base_sha"))
        tail = f"@{sha}{'+' if src.get('dirty') else ''}" if sha else ""
        parts = [name, tail]
    text = " ".join(p for p in parts if p)
    if not text:
        return DASH
    if len(text) <= MAX_IDENT:
        return text
    # 자를 때 **뒤(sha)를 남긴다** — 신고자가 자기 잡을 찾은 것은 브랜치가 아니라 sha 였다.
    name, sep, tail = text.rpartition(" @")
    if sep and len(tail) + 3 < MAX_IDENT:
        keep = MAX_IDENT - len(tail) - 3  # "…" + " @"
        return f"{name[:keep]}… @{tail}"
    return text[: MAX_IDENT - 1] + "…"


def failure_lines(
    job: dict[str, Any], *, job_id: int, url: str | None, limit: int = 3
) -> list[str]:
    """실패한 잡의 끝줄 — 로그로 가는 길과 이름별 최근 이력(M5h §2.5).

    성공한 잡에는 아무것도 안 붙인다. `failures` 키가 없는 것(못 읽었다)과 빈 배열(이름을 안
    남겼다)은 뜻이 다르지만 화면은 같다 — 둘 다 로그 줄만 나온다.
    """
    if job.get("state") == SUCCEEDED:
        return []
    log = f"log: rcm logs {job_id}" + (f" · {url}" if url else "")
    out = [log]
    # 서버 문서는 우리가 만든 게 아니다 — 이름이 없거나 모양이 이상한 항목 하나 때문에
    # **이미 끝난 잡의 종료 코드와 JSON 을 잃으면** 안 된다(`_wait` 은 이 뒤에 JSON 을 찍는다).
    items = [i for i in (job.get("failures") or []) if isinstance(i, dict) and i.get("name")]
    key = str(job.get("key") or "")
    shown = items[: max(0, limit)]
    for item in shown:
        out.append(f"failed: {item['name']}" + _history_tail(item, key))
    if len(items) > len(shown):
        # 이름이 상한에서 잘렸으면 「N개 더」는 **최소값**이다 — 아는 척하지 않는다
        at_least = "at least " if job.get("failures_truncated") else ""
        out.append(f"… and {at_least}{len(items) - len(shown)} more (rcm logs {job_id})")
    # 분모의 품질은 어느 줄에 실려 와도 읽는다 — 서버는 줄마다 같은 값을 싣는다(결정 68)
    unnamed = max((_int(i.get("window_unnamed")) for i in items), default=0)
    if unnamed:
        window = max((_int(i.get("window")) for i in items), default=0)
        out.append(f"note: {unnamed} of those {window} runs failed without naming anything")
    return out


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _history_tail(item: dict[str, Any], key: str) -> str:
    """` — 최근 이력` 조각. 셀 수 없는 항목에는 **아무 말도 안 붙인다**(`None` 을 문장에 넣느니)."""
    template = _HISTORY.get(str(item.get("verdict")))
    window, seen = item.get("window"), item.get("seen")
    if template is None or not isinstance(window, int) or isinstance(window, bool):
        return ""
    if "{seen}" in template and (not isinstance(seen, int) or isinstance(seen, bool)):
        return ""
    # key 에 공백이나 가운뎃점이 있으면 문장이 어디서 끊기는지 알 수 없다 — 그 자리를 뺀다
    # (key 는 바로 위 줄의 잡 행이 이미 말한다).
    safe = key if key and not any(c in key for c in " ·\t") else ""
    return " — " + template.format(seen=seen, window=window, key=f"{safe} " if safe else "")


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
    # 도는 tree 잡도 브랜치를 말한다 — 큐 행에만 sha 가 있고 브랜치가 없으면 「내 잡」을
    # 알아보는 화면이 하나도 없다(M5h 검증 3)
    branch = src.get("branch") or ""
    ref = f" branch {branch}" if branch else ""
    return f"{repo} @{sha or DASH}{dirty}{ref}".strip()


def render_queue_row(
    row: dict[str, Any],
    tz: tzinfo | None,
    now: datetime | None,
    workers: list[dict[str, Any]] | None = None,
) -> list[str]:
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
        shared=bool(est.get("shared")),
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
        f"        {_reason_text(row, workers)}",
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
                head += f" · ✘ {prog['failed_step']}"  # 도는 잡의 라벨은 선언된 것뿐이다
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
    all_workers = server.get("workers") or []
    # 로컬 레인은 오늘 그대로, 원격 워커(M5b-2)는 `<name>/<lane> <state>[ #job]` 로 뒤에 붙인다
    workers = [w for w in all_workers if not w.get("worker")]
    remote = [w for w in all_workers if w.get("worker")]
    busy = sum(1 for w in workers if w.get("state") == "busy")
    down = [w for w in workers if w.get("state") == "down"]
    lanes = server.get("lanes") or len(workers) or 0
    if lanes == 1 and workers:
        w = workers[0]
        wtxt = f"worker {w.get('state')}" + (f" #{w['job_id']}" if w.get("job_id") else "")
    else:
        wtxt = f"lanes {busy}/{lanes} busy"
        # 보류 레인은 busy 도 down 도 아니라 오늘은 **아예 안 보인다** — idle 과 글자 하나까지
        # 같아서 레인이 왜 노는지 알 수 없다(M5f §5.4).
        n_held, why = held_summary(workers)
        if n_held:
            wtxt += f" · {n_held} held" + (f" ({why})" if why else "")
    if down:
        wtxt += f" · DOWN: lane {', '.join(str(w['lane']) for w in down)}"
    # 원격 필은 5개까지, 넘치면 `+N workers` 로 접는다. down 은 접지 않는다(항상 보여야 한다)
    shown_live = 0
    folded = 0
    for w in remote:
        is_down = w.get("state") == "down"
        if not is_down:
            if shown_live >= MAX_REMOTE_PILLS:
                folded += 1
                continue
            shown_live += 1
        label = w.get("display_name") or f"{w.get('worker')}/{w.get('lane')}"
        wtxt += f" · {label} {w.get('state') or DASH}"
        if w.get("job_id"):
            wtxt += f" #{w['job_id']}"
    if folded:
        wtxt += f" · +{folded} workers"
    if server.get("paused"):
        wtxt += f" · PAUSED by {server['paused'].get('by')}"
    cache = server.get("snapshot_cache")
    if isinstance(cache, dict):  # 캐시가 켜져 있으면 모르는 숫자는 — 로(0 이 아니다)
        blobs = cache.get("blobs")
        wtxt += f" · cache {DASH if blobs is None else blobs} blobs · {_mb(cache.get('bytes'))}"
    if len(pools) > 1:  # 풀이 둘 이상이면 머리줄에 전체 집계(풀 하나면 오늘 그대로)
        running = waiting = 0
        for pl in pools:
            for r in pl.get("queue") or []:
                if r.get("state") in (RUNNING, CANCELLING):
                    running += 1
                else:
                    waiting += 1
        wtxt += f" · pools {len(pools)} · {running} running · {waiting} waiting"
    clock = fmt_clock(status.get("generated_at"), tz)
    tzname = status.get("display_timezone") or "local"
    out = [f"━━━ rcm · {name} · {clock} {tzname} · {wtxt}"]
    if server.get("notify_failures"):
        out.append(f"  notify failures {server['notify_failures']} · see the server log")
    if server.get("last_error"):
        out.append(f"  error · {str(server['last_error'])[:60]}")
    for pl in pools or [{}]:  # pools 가 비면 조회 실패 모양(오늘과 같다)
        out.extend(render_pool(pl, tz=tz, now=now, server=server, workers=workers))
    return "\n".join(out) + "\n"


def render_pool(
    pool: dict[str, Any],
    *,
    tz: tzinfo | None = None,
    now: datetime | None = None,
    server: dict[str, Any] | None = None,
    workers: list[dict[str, Any]] | None = None,
) -> list[str]:
    """풀 하나(큐 · recent · medians · host)를 줄 목록으로. 기본 풀은 M1 모양 그대로."""
    server = server or {}
    workers = workers if workers is not None else (server.get("workers") or [])
    out: list[str] = []
    hosts = pool.get("hosts")
    remote = pool.get("name") not in (None, "default")
    lanes = pool.get("lanes")
    no_workers = remote and (lanes == 0 or (not lanes and not hosts))
    label = ""
    if remote:
        label = f" (pool {pool.get('name')}{' · no workers' if no_workers else ''})"
    queue = pool.get("queue")
    if queue is None:
        out.append(f"queue — unavailable: {pool.get('queue_error') or 'unknown error'}{label}")
    elif not queue:
        if remote:  # 원격 풀 헤더는 언제나 풀 이름을 단다(M5b-4). 정지는 뒤에 붙인다
            out.append(f"queue — empty{label}{' · paused' if server.get('paused') else ''}")
        elif server.get("paused") or (workers and all(w.get("state") == "down" for w in workers)):
            out.append("queue — empty but paused/no worker — nothing will start")
        else:
            out.append("queue — empty (rcm run <preset> starts immediately)")
    else:
        running = sum(1 for r in queue if r["state"] in (RUNNING, CANCELLING))
        waiting = len(queue) - running
        if remote:  # 다른 풀은 짧게 — 「queue — N (pool linux · no workers)」
            out.append(f"queue — {len(queue)}{label}")
        else:
            out.append(f"queue — {len(queue)} jobs · {running} running · {waiting} waiting")
        for row in queue:
            out.extend(render_queue_row(row, tz, now, workers))

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
            # 선언된 스텝만 「step」이다. 아니면 「어디였나」만 말한다 — 인과는 주장하지 않는다
            # (M5h · 결정 63). 취소·유실 잡에는 라벨이 없다(결정 64) — **읽는 쪽에서도** 막는다:
            # M5h 이전에 쓰인 행에는 취소된 잡에도 `failed_step` 이 남아 있다(운영 잡 #176).
            if r.get("state") not in (CANCELLED, LOST):
                if r.get("failed_step"):
                    tail += f" (step {_short_step(r['failed_step'])})"
                elif r.get("last_step"):
                    tail += f" (last step {_short_step(r['last_step'])})"
            when = fmt_clock(r.get("finished_at"), tz, now=now)
            dur = fmt_duration(r.get("job_seconds"))
            out.append(
                f"  {glyph} {_state_word(r['state'])}{exit_txt} {_short_key(r.get('key')):<16} "
                f"← {req:<18} {source_ident(r.get('source')):<{MAX_IDENT}} "
                f"{dur:>8}  {when}  {tail}".rstrip()
            )
            art = _artifacts_line(r.get("artifacts"))
            if art:
                out.append(f"      {art}")

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
            if h.get("disk"):
                out.append(f"      disk: {_disk(h.get('disk'))}")
            top = h.get("top") or []
            if top:
                out.append(
                    "      top: "
                    + " · ".join(
                        f"{t.get('comm')} {_pct(t.get('cpu'))} {t.get('rss_mb')}MB" for t in top
                    )
                )
    return out
