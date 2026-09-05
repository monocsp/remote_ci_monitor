"""보존 정리의 순수 규칙 — 어떤 잡의 산출물(로그·스냅샷·워크스페이스)을 지울 때가 됐는가.

I/O 가 없다. 실제 삭제는 `janitor.py`. 활성 잡은 어떤 시각이 찍혀 있어도 **절대** 대상이
아니다 — 실행 중 워크스페이스를 지우는 것이 이 기능의 가장 큰 사고라서 여기서 먼저 거르고
janitor 가 한 번 더 확인한다(이중 안전).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from remote_ci_monitor.core.model import SUCCEEDED, TERMINAL_STATES, Job

DAY_SECONDS = 86_400.0


@dataclass(frozen=True)
class RetentionPolicy:
    """`[server] retention_days_success` · `retention_days_failure`."""

    success_days: int
    failure_days: int


def retention_seconds(state: str, policy: RetentionPolicy) -> float | None:
    """상태별 보존 기간(초). 활성 상태는 None — 보존 기간이라는 개념이 없다."""
    if state == SUCCEEDED:
        return policy.success_days * DAY_SECONDS
    if state in TERMINAL_STATES:
        return policy.failure_days * DAY_SECONDS
    return None


def due_for_purge(jobs: Iterable[Job], now: datetime, policy: RetentionPolicy) -> list[Job]:
    """보존 기간이 지난 종료 잡. `finished_at` 오름차순.

    `artifacts_purged_at` 이 이미 있으면 제외. 종료 잡인데 `finished_at` 이 없으면(있으면 안
    되지만) `created_at` 기준. 경계는 `>=` — days 0 은 「끝나자마자 다음 sweep 에」.
    """
    due: list[tuple[datetime, int, Job]] = []
    for job in jobs:
        if job.artifacts_purged_at is not None:
            continue
        keep = retention_seconds(job.state, policy)
        if keep is None:
            continue  # 활성 잡 — retention_seconds 가 None 을 준다. 절대 대상이 아니다
        ended = job.finished_at or job.created_at
        if (now - ended).total_seconds() >= keep:
            due.append((ended, job.id, job))
    due.sort(key=lambda t: (t[0], t[1]))
    return [job for _, _, job in due]
