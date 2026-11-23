"""StatusModel → `/api/status` 스키마 v1 JSON. `rcm top --json` 과 같은 모양.

규칙(PLAN.md 「/api/status 스키마 v1」): 시각은 UTC ISO-8601 `Z`. 조회 실패 섹션은 `null` +
`*_error`. 모르는 숫자는 `null`. `position` 은 대기 잡만. `log_tail` 은 호출자가 토큰을 확인해
넘긴 잡에만 싣는다(여기서는 `log_tails` 에 있는 잡만 값이 된다).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from remote_ci_monitor import SCHEMA_VERSION
from remote_ci_monitor.core import artifacts as _art
from remote_ci_monitor.core import shelf as _shelf
from remote_ci_monitor.core.hostparse import stale as _stale
from remote_ci_monitor.core.model import (
    REASON_BLOCKED_BY_GROUP,
    Action,
    Estimate,
    HostSample,
    Job,
    Pool,
    Preset,
    Progress,
    QueueRow,
    ServerInfo,
    Source,
    StatusModel,
)
from remote_ci_monitor.core.queue import confidence
from remote_ci_monitor.core.supersede import successor_of


def iso(dt: datetime | None) -> str | None:
    """UTC · 초 단위 · `Z`. naive 는 UTC 로 본다."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _num(v: float | None) -> float | int | None:
    if v is None:
        return None
    r = round(v)
    return int(r) if abs(v - r) < 1e-9 else round(v, 1)


def job_url(base_url: str | None, job_id: int) -> str | None:
    return f"{base_url.rstrip('/')}/#/jobs/{job_id}" if base_url else None


def source_json(s: Source) -> dict[str, Any]:
    if s.mode == "git_ref":
        return {"mode": s.mode, "repo": s.repo, "ref": s.ref, "sha": s.sha}
    return {
        "mode": s.mode,
        "repo": s.repo,
        "branch": s.branch,  # 옛 잡은 null — 클라이언트가 안 보내던 때가 있다 (M5h)
        "base_sha": s.base_sha,
        "dirty": s.dirty,
        "tree_hash": s.tree_hash,
        "bytes": s.bytes,
        "received_bytes": s.received_bytes,
        "last_received_at": iso(s.last_received_at),
        "uploaded_bytes": s.uploaded_bytes,
        "cached_bytes": s.cached_bytes,
    }


def estimate_json(e: Estimate, *, confidence: str | None = None) -> dict[str, Any]:
    return {
        "confidence": confidence,
        # 같은 풀의 다른 잡과 머신을 나눠 쓰는 중인가 — 화면이 배지를 스스로 그릴 재료 (M5f)
        "shared": e.shared,
        "expected_seconds": _num(e.expected_seconds),
        "source": e.source,
        "sample_count": e.sample_count,
        "elapsed_seconds": _num(e.elapsed_seconds),
        "waited_seconds": _num(e.waited_seconds),
        "remaining_seconds": _num(e.remaining_seconds),
        "wait_seconds": _num(e.wait_seconds),
        "overdue": e.overdue,
        "stuck": e.stuck,
        # 출력이 조용하다 — 경보가 아니다. `stuck` 과 동시에 참이 되지 않는다.
        "quiet": e.quiet,
        # `stuck` 의 근거: "over_step" · "over_elapsed" · "no_output". 아니면 null.
        "stuck_code": e.stuck_code,
        # 현재 단계가 평소 걸리는 시간(초). 실측이 없으면 null — 지어내지 않는다.
        "step_expected_seconds": _num(e.step_expected_seconds),
        "finish_at": iso(e.finish_at),
    }


def progress_json(p: Progress | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "timing": p.timing,
        "phase": p.phase,
        "last_output_at": iso(p.last_output_at),
        "steps_total": p.steps_total,
        "steps_total_partial": p.steps_total_partial,
        "steps_done": p.steps_done,
        "current_index": p.current_index,
        "current_name": p.current_name,
        "current_seconds": _num(p.current_seconds),
        "job_seconds": _num(p.job_seconds),
        # 초를 세는 기준점(M5d-2 §4.6-다) — 화면이 폴링 사이에도 부드럽게 올린다
        "job_started_at": iso(p.started_at),
        "failed_step": p.failed_step,
        "last_step": p.last_step,
        "steps": [
            {
                "index": s.index,
                "name": s.name,
                "state": s.state,
                "ok": s.ok,
                "seconds": _num(s.seconds),
                "started_at": iso(s.started_at),
                "ended_at": iso(s.ended_at),
            }
            for s in p.steps
        ],
        # 현재 스텝 안의 세부 진행(`::rcm::progress::`). 키만 더했다 — schema_version 은 그대로.
        "sub": None
        if p.sub is None
        else {
            "done": p.sub.done,
            "total": p.sub.total,
            "unit": p.sub.unit,
            "state": p.sub.state,
            "note": p.sub.note,
            "at": iso(p.sub.at),
        },
        "units": [
            {"unit": u.unit, "state": u.state, "note": u.note, "at": iso(u.at)} for u in p.units
        ],
        "units_truncated": p.units_truncated,
    }


def step_timeline_json(p: Progress | None) -> dict[str, Any]:
    """종료 잡의 `step_timeline` — 저장된 마커로 다시 낸 스텝별 시간(M5j G1 · 결정 84).

    `progress_json` 의 스텝 칸과 같은 값이되 **시간과 `ok` 만** 싣는다. 재구성한 `failed_step`·
    `last_step` 은 넣지 않는다 — 잡 행에 저장된 것이 정본이다(M5h 결정 63·64, v16 불변식).
    `p` 가 None(시작 전에 끝난 잡)이거나 마커가 없으면 **조회에 성공한 빈 타임라인**이다 —
    `steps: []`, `steps_total: null`. 「못 읽었다」는 여기로 오지 않는다(호출자가 `null` 을 싣는다).
    """
    if p is None:
        return {
            "timing": "as_received",
            "steps_total": None,
            "steps_total_partial": True,
            "steps": [],
        }
    return {
        "timing": p.timing,
        "steps_total": p.steps_total,
        "steps_total_partial": p.steps_total_partial,
        "steps": [
            {
                "index": s.index,
                "name": s.name,
                "started_at": iso(s.started_at),
                "ended_at": iso(s.ended_at),
                "seconds": _num(s.seconds),
                "ok": s.ok,
            }
            for s in p.steps
        ],
    }


def action_json(a: Action | None) -> dict[str, Any] | None:
    """「사람이 할 일」 표식 `{code, note, at}` 또는 null(M5s 결정 213). 마지막 표식 하나다."""
    if a is None:
        return None
    return {"code": a.code, "note": a.note, "at": iso(a.at)}


def _requester(job: Job) -> dict[str, str]:
    return {"name": job.requester.name, "label": job.requester.label}


def _link(job: Job) -> dict[str, Any] | None:
    """호출자가 붙인 링크(v22) — `{url, label}` 또는 null. `label` 이 null 이면 화면이
    주소를 줄인다. 키만 더했다 — schema_version 은 그대로다(키를 빼거나 뜻을 바꿀 때만 올린다)."""
    if job.link is None:
        return None
    return {"url": job.link.url, "label": job.link.label}


def _joiners(job: Job) -> list[dict[str, Any]]:
    return [{"name": j.name, "label": j.label, "joined_at": iso(j.joined_at)} for j in job.joiners]


def _cancel(job: Job) -> dict[str, Any] | None:
    if job.cancel is None:
        return None
    return {
        "requested_at": iso(job.cancel.requested_at),
        "by": job.cancel.by,
        "kill_at": iso(job.cancel.kill_at),
    }


def chain_json(job: Job) -> dict[str, Any]:
    """반복 사슬의 칸(M5s 결정 211 · 228) — 잡 행에 **더한다**. 사슬이 없으면 `chain_id` 가
    null 이다.

    `retried_as` 는 `retried` 로 끝난 시도의 다음 시도다 — `rcm wait` 가 이 키를 따라간다.
    `max_attempts` 는 제출 때 얼린 상한이다(반복을 안 켠 잡은 null)."""
    return {
        "chain_id": job.chain_id,
        "attempt": job.attempt,
        "retry_of": job.retry_of,
        "retried_as": job.retried_as,
        "max_attempts": (
            job.retry_max_attempts
            if job.retry_on_exit is not None and not job.retry_disabled
            else None
        ),
    }


def queue_row_json(
    row: QueueRow, *, base_url: str | None = None, log_tail: list[str] | None = None
) -> dict[str, Any]:
    job = row.job
    return {
        "id": job.id,
        "position": row.position,
        "priority": job.priority,
        "pool": job.pool,
        "preset": job.preset,
        "key": job.key,
        "inputs": dict(job.inputs),
        "concurrency_group": job.concurrency_group,
        "requester": _requester(job),
        "joiners": _joiners(job),
        "link": _link(job),
        "state": job.state,
        "reason": row.reason,
        "lane": row.lane,
        "ahead_job_id": row.ahead_job_id,
        "blocked_by": (
            {
                "job_id": row.blocked_by.job_id,
                "group": row.blocked_by.group,
                "remaining_seconds": _num(row.blocked_by.remaining_seconds),
            }
            if row.blocked_by
            else None
        ),
        "cancel": _cancel(job),
        # 새 잡에 밀려 취소 중이면 그 새 잡(M5o). 키만 더한다 — schema_version 그대로
        "superseded_by": successor_of(job),
        "source": source_json(job.source),
        "created_at": iso(job.created_at),
        "queued_at": iso(job.queued_at),
        "started_at": iso(job.started_at),
        "estimate": estimate_json(
            row.estimate,
            confidence=confidence(
                row.estimate.source,
                row.estimate.sample_count,
                group_wait=row.reason == REASON_BLOCKED_BY_GROUP,
                overdue=row.estimate.overdue or row.estimate.stuck,
                shared=row.estimate.shared,
            ),
        ),
        "progress": progress_json(row.progress),
        "log_tail": log_tail,
        "url": job_url(base_url, job.id),
        # M5n — 어디서 돌 수 있나(대기)와 어디서 도나(실행). 키만 더한다(스키마 v1)
        "placement": job.placement,
        "display_pool": (job.display_pool or job.pool) if job.is_waiting else job.display_pool,
        "target_worker": job.target_worker,
        "concurrency_scope": job.concurrency_scope,
        "host_key": job.host_key,
        "placement_detail": [placement_note_json(n) for n in row.placement_detail],
        # M5p — 제출 때 얼린 keep 과 보관 상태. `shelf` 는 서버가 채운다(DB 를 봐야 한다).
        # 키 추가만 — schema_version 그대로
        "keep": job.keep,
        "shelf": None,
        # M5s — 잡이 찍은 마지막 「사람이 할 일」 표식(`::rcm::action::`). 키 추가만
        "action": action_json(row.action),
        # M5s — 예약에서 온 잡이면 그 시각과 시작 마감. 즉시 실행이면 둘 다 null. 키 추가만
        "not_before": iso(job.not_before),
        "start_deadline": iso(job.start_deadline),
        # M5s — 반복 사슬(결정 211). 키 추가만
        **chain_json(job),
    }


def placement_note_json(n: Any) -> dict[str, Any]:
    """대기 잡 하나에서 본 워커 하나의 판정(M5n §7.3)."""
    return {
        "worker": n.worker,
        "display_name": n.display_name,
        "state": n.state,
        "code": n.code,
        "detail": dict(n.detail),
    }


def recent_json(
    job: Job, *, base_url: str | None = None, action: Action | None = None
) -> dict[str, Any]:
    """끝난 잡 한 행. `action` 은 순수 층이 모른다 — 마커를 읽은 호출자(서버 · 알림)가 넘긴다."""
    started = job.started_at
    finished = job.finished_at
    job_seconds = (finished - started).total_seconds() if started and finished else None
    waited = (started - job.wait_origin).total_seconds() if started else None
    return {
        "pool": job.pool,
        "id": job.id,
        "preset": job.preset,
        "key": job.key,
        "inputs": dict(job.inputs),
        "requester": _requester(job),
        "joiners": _joiners(job),
        "link": _link(job),
        "state": job.state,
        "exit_code": job.exit_code,
        "job_seconds": _num(job_seconds),
        "waited_seconds": _num(waited),
        "created_at": iso(job.created_at),
        "started_at": iso(started),
        "finished_at": iso(finished),
        "summary": job.summary,
        # 서버가 만든 요약이면 코드와 원시 인자가 함께 온다(결정 37) — 화면이 자기 말로 그린다.
        # 잡이 `::rcm::summary::` 로 찍은 문장에는 코드가 없다.
        "summary_code": job.summary_code,
        "summary_args": dict(job.summary_args) if job.summary_args else None,
        "failed_step": job.failed_step,
        "last_step": job.last_step,
        "cancelled_by": job.cancelled_by,
        # 같은 브랜치의 새 잡에 밀려 취소됐으면 그 잡의 id(M5o 결정 114). 아니면 null
        "superseded_by": successor_of(job),
        "timeout_seconds": job.timeout_seconds,
        "source": source_json(job.source),
        "transitions": [{"state": t.state, "at": iso(t.at)} for t in job.transitions],
        "url": job_url(base_url, job.id),
        "placement": job.placement,
        "display_pool": job.display_pool,
        "target_worker": job.target_worker,
        "host_key": job.host_key,
        "worker": job.worker_name,
        # M5p — 제출 때 얼린 keep 과 보관 상태(서버가 채운다). 키 추가만
        "keep": job.keep,
        "shelf": None,
        # M5s — 마지막 「사람이 할 일」 표식. 잡이 끝나도 남는다(이벤트로 저장). 키 추가만
        "action": action_json(action),
        # M5s — 예약에서 온 잡이면 그 시각과 시작 마감(`expired` 는 언제까지였나). 키 추가만
        "not_before": iso(job.not_before),
        "start_deadline": iso(job.start_deadline),
        # M5s — 반복 사슬(결정 211). 사슬은 마지막 시도 한 행으로 보이고 `attempt` 가 시도 수다
        **chain_json(job),
    }


def scheduled_json(job: Job, *, base_url: str | None = None) -> dict[str, Any]:
    """예약 행 하나(M5s §4.1 · `pools[].scheduled[]` · 예약 잡의 `GET /jobs/<id>`).

    큐 행이 아니다 — 순번 · 이유 · ETA 가 없다. 승격되기 전의 잡에 그 숫자를 지어내지 않는다.
    """
    return {
        "id": job.id,
        "pool": job.pool,
        "preset": job.preset,
        "key": job.key,
        "inputs": dict(job.inputs),
        "priority": job.priority,
        "concurrency_group": job.concurrency_group,
        "requester": _requester(job),
        "joiners": _joiners(job),
        "link": _link(job),
        "state": job.state,
        "source": source_json(job.source),
        "created_at": iso(job.created_at),
        "not_before": iso(job.not_before),
        "start_deadline": iso(job.start_deadline),
        "started_at": None,
        "finished_at": None,
        "url": job_url(base_url, job.id),
        "placement": job.placement,
        "display_pool": job.display_pool or job.pool,
        "target_worker": job.target_worker,
        "keep": job.keep,
        "shelf": None,
        # M5s — 반복 사슬(결정 211). 다음 시도는 시작 마감 없이 이 행으로 기다린다(결정 231)
        **chain_json(job),
        # M5s — 사슬의 「사람이 할 일」 표식과 그 이유(결정 233). 순수 층은 마커를 모른다 — 서버가
        # 채운다. 표식이 있으면 `reason` 은 `needs_action`, 없으면 둘 다 null
        "reason": None,
        "action": None,
    }


def preset_json(p: Preset) -> dict[str, Any]:
    return {
        "name": p.name,
        "description": p.description,
        "source_modes": list(p.source_modes),
        "repo": p.repo or None,
        "priority": p.priority,
        "pool": p.pool,
        "pools": list(p.pools),
        "concurrency_group": p.concurrency_group,
        "expected_seconds": p.expected_seconds,
        "timeout_seconds": p.timeout_seconds,
        # M5n — 설정 문자열 그대로(`"any-capable"`). 잡 행의 `placement` 는 DB 값(`"capable"`)이다
        "placement": "any-capable" if p.placement == "capable" else "pool",
        "checks": list(p.checks),
        "concurrency_scope": p.concurrency_scope,
        "sample": p.sample is not False,
        # M5p — `--keep` 을 걸 수 있나(글롭이 있나)와 늘 보관하나(§15.6). CLI 가 제출 전에 거른다
        "has_artifacts": bool(p.artifacts),
        "keep": bool(p.keep),
        # M5s — `[presets.retry]`(결정 210). 선언이 없으면 null 이다. 키 추가만
        "retry": (
            {
                "on_exit": p.retry.on_exit,
                "interval_seconds": p.retry.interval_seconds,
                "max_attempts": p.retry.max_attempts,
            }
            if p.retry is not None
            else None
        ),
        "inputs": [
            {
                "name": i.name,
                "type": i.type,
                "choices": list(i.choices) if i.type == "choice" else None,
                "default": i.default,
                "pattern": i.pattern,
                "description": i.description,
            }
            for i in p.inputs
        ],
    }


def server_json(s: ServerInfo) -> dict[str, Any]:
    return {
        "version": s.version,
        "uptime_seconds": _num(s.uptime_seconds),
        "lanes": s.lanes,
        "paused": {"by": s.paused.by, "at": iso(s.paused.at)} if s.paused else None,
        "last_error": s.last_error,
        "last_error_code": s.last_error_code,
        "sse_connections": s.sse_connections,
        "snapshot_cache": (
            {"blobs": s.snapshot_cache_blobs, "bytes": s.snapshot_cache_bytes}
            if s.snapshot_cache_blobs is not None
            else None
        ),
        "notify_failures": s.notify_failures,
        # M5n — 능력 보고의 세대와 머신별 요약(전부는 `GET /api/machines`)
        "generation": s.generation,
        "machines": list(s.machines),
        "workers": [
            {
                "lane": w.lane,
                "state": w.state,
                "job_id": w.job_id,
                "error": w.error,
                "since": iso(w.since),
                "worker": w.worker,  # 원격 워커 이름 · 로컬 레인은 null (M5b-2)
                "display_name": w.display_name,
                "pool": w.pool,
                "host_key": w.host_key,
                # 부하 게이트가 막고 있으면 **왜**와 **언제부터**(M5f). 아니면 셋 다 null.
                "hold_code": w.hold_code,
                "hold_detail": dict(w.hold_detail) if w.hold_detail else None,
                "held_since": iso(w.held_since),
            }
            for w in s.workers
        ],
    }


def host_json(h: HostSample, *, now: datetime) -> dict[str, Any]:
    age = (now - h.sampled_at).total_seconds()
    return {
        "name": h.name,
        "source": h.source,
        "sampled_at": iso(h.sampled_at),
        "age_seconds": _num(age),
        "stale": _stale(h.sampled_at, now, h.interval_seconds),
        "interval_seconds": h.interval_seconds,
        "os": h.os,
        "cores": h.cores,
        "load": list(h.load) if h.load else None,
        "cpu": dict(h.cpu) if h.cpu else None,
        "memory": dict(h.memory) if h.memory else None,
        "gpu": dict(h.gpu) if h.gpu else None,
        # 잡이 쓰는 디스크 — 「얼마 남았나」를 화면이 보여 준다(M5d-2)
        "disk": dict(h.disk) if h.disk else None,
        "gpu_note": h.gpu_note,
        "gpu_note_code": h.gpu_note_code,
        "top": [dict(t) for t in h.top],
        "history": [dict(x) for x in h.history],
    }


def pool_json(
    pool: Pool,
    *,
    now: datetime,
    base_url: str | None,
    log_tails: Mapping[int, list[str]] | None,
) -> dict[str, Any]:
    tails = log_tails or {}
    return {
        "name": pool.name,
        "lanes": pool.lanes,
        "queue": (
            [queue_row_json(r, base_url=base_url, log_tail=tails.get(r.job.id)) for r in pool.queue]
            if pool.queue is not None
            else None
        ),
        "queue_error": pool.queue_error,
        "queue_error_code": pool.queue_error_code,
        # M5s — 예약(시각순). 큐 표와 따로 간다. 조회 실패는 null + `scheduled_error*`(관례)
        "scheduled": (
            [scheduled_json(j, base_url=base_url) for j in pool.scheduled]
            if pool.scheduled is not None
            else None
        ),
        "scheduled_error": pool.scheduled_error,
        "scheduled_error_code": pool.scheduled_error_code,
        "recent": (
            [recent_json(j, base_url=base_url) for j in pool.recent]
            if pool.recent is not None
            else None
        ),
        "recent_count": pool.recent_count,
        "recent_error": pool.recent_error,
        "recent_error_code": pool.recent_error_code,
        "medians": (
            {
                k: {
                    "seconds": _num(m.seconds),
                    "wait_seconds": _num(m.wait_seconds),
                    "sample_count": m.sample_count,
                }
                for k, m in pool.medians.items()
            }
            if pool.medians is not None
            else None
        ),
        "medians_error": pool.medians_error,
        "medians_error_code": pool.medians_error_code,
        "hosts": [host_json(h, now=now) for h in pool.hosts] if pool.hosts is not None else None,
        "hosts_error": pool.hosts_error,
        "hosts_error_code": pool.hosts_error_code,
    }


def artifacts_json(row: Mapping[str, Any] | None, state: str) -> dict[str, Any]:
    """잡 하나의 산출물 처분 — **공개 문서용**(명세 §10).

    파일 목록·경로·해시는 여기 없다. 그건 보호 라우트에만 있다. `reason_args` 도 수치만 담는다.
    모르는 수는 `null` 이고 `0` 은 「모았는데 없었다」일 때만이다.
    """
    if row is None:
        return {
            "state": state,
            "file_count": None,
            "total_bytes": None,
            "bundle_bytes": None,
            "skipped_count": None,
            "ready_at": None,
            "expires_at": None,
            "purged_at": None,
            "reason_code": None,
            "reason_args": None,
        }
    return {
        "state": state,
        "file_count": row["file_count"],
        "total_bytes": row["total_bytes"],
        "bundle_bytes": row["bundle_bytes"],
        "skipped_count": row["skipped_count"],
        "ready_at": iso(row["ready_at"]),
        "expires_at": iso(row["expires_at"]),
        "purged_at": iso(row["purged_at"]),
        "reason_code": row["reason_code"],
        "reason_args": row["reason_args"] or None,
    }


def shelf_stage_state(
    state: str,
    last_used_at: float | None,
    requested_at: float,
    *,
    now: float,
    stale_days: int,
    delete_days: int,
) -> str:
    """DB 열 → 화면에 보일 상태. 보관 중 · 정리 대기는 열이 아니라 `stage()` 로 다시 잰다(§15.5).

    청소 회차 사이의 어긋남을 없앤다 — 열은 `active` 인데 182일이 지났으면 `stale` 로 보인다.
    삭제 단계가 지났는데 아직 안 지운 항목도 `stale` 이다(쓰면 다시 산다).
    """
    if state not in _shelf.SHELVED_STATES:
        return state
    used = last_used_at if last_used_at is not None else requested_at
    return "active" if _shelf.stage(used, now, stale_days, delete_days) == "active" else "stale"


def shelf_json(
    *,
    keep: bool,
    item: Any | None,
    artifact_state: str,
    url: str | None,
    now: float,
    terminal: bool = False,
    stale_days: int,
    delete_days: int,
    none_reason: str | None = None,
) -> dict[str, Any] | None:
    """잡 행의 `shelf` — `{state, url, reason_code}` 또는 null(keep 이 아닌 잡, §4.8 · §15.1).

    `state` 는 `PUBLIC_SHELF_STATES` 의 하나다. 보관 행이 없으면 묶음으로 정한다 — 아직 오는 중이면
    `waiting`, 종결인데 ready 가 아니면 `none` 과 그 이유(묶음 상태 이름, 또는 정책으로 계산한
    `none_reason`). 묶음도 행도 없는 keep 잡은 `none(forgotten)` 이다(묘비까지 지워졌다).
    """
    if not keep:
        return None
    if item is not None:
        state = shelf_stage_state(
            item.state,
            item.last_used_at,
            item.requested_at,
            now=now,
            stale_days=stale_days,
            delete_days=delete_days,
        )
        reason = item.reason_code if state == _shelf.STATE_FAILED else None
        return {"state": state, "url": url, "reason_code": reason}
    if artifact_state == _art.DISABLED:
        return {"state": "none", "url": url, "reason_code": "no_artifacts"}
    if artifact_state in _art.PROGRESS_STATES:
        if terminal:
            # 끝난 잡의 묶음은 더 오지 않는다 — 업로드 도중 끊겼다(P2 격리 검증 D2)
            return {"state": "none", "url": url, "reason_code": "interrupted"}
        return {"state": "waiting", "url": url, "reason_code": None}
    if artifact_state == _art.UNKNOWN:
        return {"state": "none", "url": url, "reason_code": none_reason or _art.UNKNOWN}
    if artifact_state in (_art.READY, _art.UNAVAILABLE) or artifact_state in _art.GONE_STATES:
        return {"state": "none", "url": url, "reason_code": "forgotten"}
    return {"state": "none", "url": url, "reason_code": artifact_state}


def status_json(
    model: StatusModel, *, log_tails: Mapping[int, list[str]] | None = None
) -> dict[str, Any]:
    """전체 StatusModel → 스키마 v1 dict. `json.dumps` 가능해야 한다(테스트로 잠근다)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(model.generated_at),
        "display_timezone": model.display_timezone,
        "server": server_json(model.server),
        "presets": [preset_json(p) for p in model.presets],
        "pools": [
            pool_json(p, now=model.generated_at, base_url=model.base_url, log_tails=log_tails)
            for p in model.pools
        ],
    }
