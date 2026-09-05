"""테스트 공용 팩토리 — 합성 잡·프리셋·워커. 시각은 고정 NOW 기준."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from remote_ci_monitor.core.model import (
    QUEUED,
    RUNNING,
    WORKER_BUSY,
    WORKER_IDLE,
    InputSpec,
    Job,
    Median,
    Preset,
    Requester,
    Source,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import QueueConfig

NOW = datetime(2026, 9, 4, 0, 52, 12, tzinfo=UTC)
CFG = QueueConfig()

GATE = Preset(
    name="gate",
    argv=("bash", "scripts/gate.sh"),
    expected_seconds=480,
    duration_key_inputs=("scope",),
    inputs=(
        InputSpec(name="scope", type="choice", choices=("full", "commit", "fast"), default="full"),
    ),
)
DEPLOY = Preset(name="deploy-dev", argv=("bash", "deploy.sh"), expected_seconds=900)
QA = Preset(name="qa", argv=("bash", "qa.sh"), concurrency_group="devices", expected_seconds=540)
PRESETS = {p.name: p for p in (GATE, DEPLOY, QA)}
MEDIANS = {
    "gate:full": Median(seconds=400.0, wait_seconds=80.0, sample_count=7),
    "deploy-dev": Median(seconds=600.0, wait_seconds=10.0, sample_count=3),
}


def ago(minutes: float = 0, seconds: float = 0) -> datetime:
    return NOW - timedelta(minutes=minutes, seconds=seconds)


def workers(*states: str, since: datetime | None = None) -> list[WorkerInfo]:
    """`workers("busy:412", "idle", "down:ENOSPC")` 처럼 쓴다."""
    out = []
    for lane, spec in enumerate(states, start=1):
        kind, _, extra = spec.partition(":")
        out.append(
            WorkerInfo(
                lane=lane,
                state=kind,
                job_id=int(extra) if kind == WORKER_BUSY and extra else None,
                error=extra if kind == "down" and extra else None,
                since=since or ago(minutes=30),
            )
        )
    return out


def job(
    id: int,
    key: str = "gate:full",
    state: str = QUEUED,
    *,
    created_min: float = 0,
    queued_min: float | None = None,
    started_min: float | None = None,
    finished_min: float | None = None,
    lane: int | None = None,
    group: str | None = None,
    preset: str | None = None,
    inputs: dict[str, Any] | None = None,
    requester: str = "alice-laptop",
    tree_hash: str | None = "9f8e",
    **kw: Any,
) -> Job:
    preset_name = preset or key.split(":")[0]
    if state == RUNNING and lane is None:
        lane = 1
    if state in (RUNNING, "cancelling") and "last_output_at" not in kw:
        kw["last_output_at"] = ago(
            seconds=1
        )  # 방금 출력이 있었다 — stuck 테스트는 명시적으로 넘긴다
    return Job(
        id=id,
        preset=preset_name,
        inputs=inputs
        if inputs is not None
        else ({"scope": key.split(":")[1]} if ":" in key else {}),
        key=key,
        concurrency_group=group,
        source=Source(
            mode="tree",
            repo="org/app",
            base_sha="abc123f",
            dirty=True,
            tree_hash=tree_hash,
            bytes=100,
        ),
        requester=Requester(
            name=requester, label=f"{requester.split('-')[0]}@{requester.split('-')[-1]}"
        ),
        state=state,
        created_at=ago(minutes=created_min),
        queued_at=ago(minutes=queued_min if queued_min is not None else created_min),
        started_at=None if started_min is None else ago(minutes=started_min),
        finished_at=None if finished_min is None else ago(minutes=finished_min),
        lane=lane,
        **kw,
    )


def default_workers(busy_ids: list[int] | None = None, lanes: int = 1) -> list[WorkerInfo]:
    busy_ids = busy_ids or []
    out = []
    for lane in range(1, lanes + 1):
        if lane <= len(busy_ids):
            out.append(
                WorkerInfo(
                    lane=lane, state=WORKER_BUSY, job_id=busy_ids[lane - 1], since=ago(minutes=2)
                )
            )
        else:
            out.append(WorkerInfo(lane=lane, state=WORKER_IDLE, since=ago(minutes=30)))
    return out
