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
from remote_ci_monitor.core.hostparse import stale as _stale
from remote_ci_monitor.core.model import (
    REASON_BLOCKED_BY_GROUP,
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
        # 위 이름이 확정인가 추측인가(false/true). 화면·CLI 가 추측을 추측으로 그린다.
        "failed_step_guessed": p.failed_step_guessed,
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
    }


def _requester(job: Job) -> dict[str, str]:
    return {"name": job.requester.name, "label": job.requester.label}


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
    }


def recent_json(job: Job, *, base_url: str | None = None) -> dict[str, Any]:
    started = job.started_at
    finished = job.finished_at
    job_seconds = (finished - started).total_seconds() if started and finished else None
    waited = (started - job.created_at).total_seconds() if started else None
    return {
        "pool": job.pool,
        "id": job.id,
        "preset": job.preset,
        "key": job.key,
        "inputs": dict(job.inputs),
        "requester": _requester(job),
        "joiners": _joiners(job),
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
        # v9 마이그레이션 전에 끝난 잡은 `null` — 「추측이었는지 모른다」이지 「확정」이 아니다.
        "failed_step_guessed": job.failed_step_guessed,
        "cancelled_by": job.cancelled_by,
        "timeout_seconds": job.timeout_seconds,
        "source": source_json(job.source),
        "transitions": [{"state": t.state, "at": iso(t.at)} for t in job.transitions],
        "url": job_url(base_url, job.id),
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
