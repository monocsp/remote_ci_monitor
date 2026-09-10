"""보존 정리의 순수 규칙 — 무엇을 지울 때가 됐는가.

I/O 가 없다. 실제 삭제는 `janitor.py`. 활성 잡은 어떤 시각이 찍혀 있어도 **절대** 대상이
아니다 — 실행 중 워크스페이스를 지우는 것이 이 기능의 가장 큰 사고라서 여기서 먼저 거르고
janitor 가 한 번 더 확인한다(이중 안전).

두 갈래가 있다(M5g, 명세 `docs/m5g-workplan.md` §4).

- **증거**(`jobs/<id>/log.txt` · 잡 행) — `due_for_purge` 가 날짜로만 정한다. 오늘 그대로다.
- **부피**(`workspaces/<id>/` + 그 잡의 `tree.tar.gz`) — `workspaces_to_purge` 가 날짜(나이)에
  더해 **바이트 예산**과 **여유 공간 바닥**으로 회수한다. 부피는 증거보다 1만 배 크고
  (로그 50 KB vs 워크스페이스 720 MB) 훨씬 짧게 산다.

부피 쪽의 안전 성질 둘:

- **못 재면 압박 삭제(예산·바닥)만 멈춘다.** 나이 규칙은 크기를 안 보므로 그대로 돈다. 둘을
  같은 문장으로 묶어 잠그면 디스크가 차는 동안 나이 규칙까지 멈춘다.
- **못 이룰 목표를 위해 증거를 태우지 않는다.** 지울 수 없는 바이트(도는 잡 + 고아)만으로 이미
  예산을 넘으면 예산 규칙은 아무것도 안 고른다(`budget_unreachable`). 바닥은 응급이라 그래도 돈다.

눈금이 둘이다(M5i 결정 76 · 명세 `docs/gate-replay-fixes-workplan.md` §3 B4):

- **charged**(`bytes`) — 링크마다 센다. 항목 표시값과 **예산** 규칙의 눈금. 예산이 보수적으로 돈다.
- **estimated_reclaimable**(`bytes − shared_bytes`) — 하드링크된 블록은 지워도 여유가 안 는다(미러가
  아직 쥐고 있다). **바닥** 규칙 · dry-run 의 「would free」 · 무진전 latch 의 분모가 이것이다.
  보수적 하한이다 — 같은 삭제 단위 안에 링크가 다 있어도 못 센다. 그래서 이름이 `estimated` 다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from remote_ci_monitor.core.artifacts import GONE_STATES
from remote_ci_monitor.core.model import SUCCEEDED, TERMINAL_STATES, Job

DAY_SECONDS = 86_400.0


@dataclass(frozen=True)
class BlobInfo:
    """스냅샷 캐시 blob 하나(M5). `sha256` 은 캐시 키(token 범위면 `<token>/<sha>`)."""

    sha256: str
    size: int
    last_used_at: datetime


def blobs_to_purge(
    blobs: Iterable[BlobInfo],
    referenced: set[str],
    now: datetime,
    *,
    days: int,
    max_bytes: int,
) -> list[BlobInfo]:
    """지울 blob. 활성 잡이 참조하는 것은 **절대** 안 지운다.

    ① `days` 이상 안 쓰인 것(경계 `>=`) ② 그래도 합계가 `max_bytes` 를 넘으면 오래된 것부터
    (참조 안 된 것만) 넘지 않을 때까지. 출력은 `last_used_at` 오름차순.
    """
    items = sorted(blobs, key=lambda b: (b.last_used_at, b.sha256))
    keep = days * DAY_SECONDS
    purge: list[BlobInfo] = []
    remaining: list[BlobInfo] = []
    for b in items:
        if b.sha256 in referenced:
            remaining.append(b)
            continue
        if (now - b.last_used_at).total_seconds() >= keep:
            purge.append(b)
        else:
            remaining.append(b)
    total = sum(b.size for b in remaining)
    for b in remaining:  # 오래된 것부터 — 참조된 것은 건너뛴다
        if total <= max_bytes:
            break
        if b.sha256 in referenced:
            continue
        purge.append(b)
        total -= b.size
    purge.sort(key=lambda b: (b.last_used_at, b.sha256))
    return purge


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


@dataclass(frozen=True)
class BundleInfo:
    """잡 산출물 묶음 하나(M5e). `bytes` 는 디스크가 실제로 쥔 **아카이브** 바이트다."""

    job_id: int
    state: str
    expires_at: datetime | None
    bytes: int = 0


def bundles_to_expire(bundles: Iterable[BundleInfo], now: datetime) -> list[BundleInfo]:
    """TTL 이 지난 묶음. 만료 시각이 **없는 행은 절대 대상이 아니다**(명세 §8).

    번들은 자기 시계로만 지운다 — `retention_days_*` 는 0 이 될 수 있어서(`config.py`) M3 청소에
    얹으면 합류된 잡이 몇 분 만에 산출물을 잃는다. 이미 사라진 것(`purged`·`expired`)은 다시
    가져가지 않는다. 경계는 `<=`, 출력은 `job_id` 오름차순.
    """
    due = [
        b
        for b in bundles
        if b.state not in GONE_STATES and b.expires_at is not None and b.expires_at <= now
    ]
    return sorted(due, key=lambda b: b.job_id)


# ── 부피 회수 (M5g) ──────────────────────────────────────────────────────────

#: 왜 골랐나(결정 37 — 서버는 문장이 아니라 코드를 내려보낸다).
REASON_AGE = "age"
REASON_BUDGET = "budget"
REASON_FREE = "free"


@dataclass(frozen=True)
class VolumeItem:
    """부피 하나 = 한 잡의 워크스페이스 + 그 잡의 스냅샷 tar.

    **인벤토리 전체**가 들어온다 — 종료 잡뿐 아니라 활성 잡과 고아(잡 행이 없는 디렉터리)도.
    총량은 디스크가 실제로 쥔 양이라 지울 수 없는 것도 세어야 한다.

    바이트는 janitor 가 실제로 잰 값이다. 그 자리에 아무것도 없으면 `0`, **못 쟀으면 `None`**
    이다 — 둘은 다른 사실이라 섞지 않는다.
    """

    job_id: int
    state: str | None  # 잡 행이 없으면 None — 고아다
    finished_at: datetime | None
    created_at: datetime | None  # 종료 잡인데 finished_at 이 없을 때의 대체 기준
    workspace_bytes: int | None
    snapshot_bytes: int | None
    #: charged 안에서 **일반 파일의 `nlink > 1` 블록**이 차지하는 몫(워크스페이스 + tar). 지워도
    #: 여유가 안 느는 바이트다. 못 쟀으면 None — 그러면 회수 예상도 모른다.
    shared_bytes: int | None = 0

    @property
    def bytes(self) -> int | None:
        """둘의 합(charged — 링크마다 센다). 하나라도 모르면 모른다."""
        if self.workspace_bytes is None or self.snapshot_bytes is None:
            return None
        return self.workspace_bytes + self.snapshot_bytes

    @property
    def estimated_reclaimable_bytes(self) -> int | None:
        """지우면 여유가 이만큼은 는다(하한). charged 나 shared 를 모르면 모른다."""
        return _reclaimable(self.bytes, self.shared_bytes)

    @property
    def evictable(self) -> bool:
        """지울 수 있나. 활성 잡도 고아도 아니다."""
        return self.state in TERMINAL_STATES

    def ended_at(self) -> datetime | None:
        """나이를 재는 기준 시각. 종료 잡인데 `finished_at` 이 없으면 `created_at`."""
        return self.finished_at or self.created_at


@dataclass(frozen=True)
class WorkspaceBudget:
    """`[server]` 의 세 키. 순수 계층은 `config.py` 를 모른다(`QueueConfig` 와 같은 방식)."""

    days: int  # workspace_retention_days
    max_bytes: int  # workspace_storage_max_bytes. 0 = 무제한
    min_free_bytes: int  # min_free_bytes. 0 = 안 본다


@dataclass(frozen=True)
class PurgeItem:
    """지울 부피 하나와 **첫** 규칙. 바이트는 charged 이고 `shared_bytes` 가 그 안의 공유 몫이다."""

    job_id: int
    workspace_bytes: int | None
    snapshot_bytes: int | None
    reason: str
    shared_bytes: int | None = 0

    @property
    def bytes(self) -> int | None:
        if self.workspace_bytes is None or self.snapshot_bytes is None:
            return None
        return self.workspace_bytes + self.snapshot_bytes

    @property
    def estimated_reclaimable_bytes(self) -> int | None:
        return _reclaimable(self.bytes, self.shared_bytes)


@dataclass(frozen=True)
class PurgePlan:
    """계획 하나. 모르는 숫자는 **0 이 아니라 None** 이다(PLAN 「fail-open 금지」)."""

    items: tuple[PurgeItem, ...] = ()  # 오래된 것부터
    #: 잰 것만 더한 값과, 크기를 모르는 채 지울 항목 수. 하나로 합치면 부분합이 전체 합인 척한다.
    known_freed_bytes: int = 0  # 고른 항목의 charged 합
    unknown_freed_count: int = 0
    #: 고른 항목의 예상 회수량 합(아는 것만) — dry-run 의 「would free」. charged 가 아니다.
    estimated_reclaimable_bytes: int = 0
    volume_bytes: int | None = 0  # 인벤토리 전체(활성·고아 포함) — charged
    evictable_bytes: int | None = 0  # 종료 잡 몫 — charged
    non_evictable_bytes: int | None = 0  # 활성 + 고아 몫 — charged
    shared_bytes: int | None = 0  # 인벤토리 전체의 공유 몫
    evictable_reclaimable_bytes: int | None = 0  # 지울 수 있는 것을 다 지우면 이만큼은 는다
    over_budget_bytes: int | None = 0  # 계획을 다 지워도 남는 초과분
    projected_short_free_bytes: int | None = 0  # 계획대로 지웠을 때 **예상** 부족분(실측 아님)
    budget_unreachable: bool = False  # 지울 수 없는 바이트만으로 이미 예산을 넘었다
    #: 여유를 알았고 바닥이 켜져 있고 계획 시작 시 바닥 아래였다 — 무진전 latch 의 판정 조건.
    #: 「`reason == "free"` 인 항목이 있었나」가 아니다: 나이·예산으로 먼저 뽑힌 항목이 부족분을
    #: 채우면 그 회차에 free 항목이 하나도 없는데도 바닥 아래에서 지운 것이다.
    floor_attempted: bool = False
    inventory_error: str | None = None  # 스캔·DB·측정 실패 코드


def _sum_or_none(values: Iterable[int | None]) -> int | None:
    """하나라도 모르면 모른다."""
    total = 0
    for v in values:
        if v is None:
            return None
        total += v
    return total


def _reclaimable(charged: int | None, shared: int | None) -> int | None:
    """charged − shared. 하나라도 모르면 모른다. 음수는 0(공유 몫이 charged 를 넘을 수는 없다)."""
    if charged is None or shared is None:
        return None
    return max(0, charged - shared)


def workspaces_to_purge(
    items: Iterable[VolumeItem],
    now: datetime,
    budget: WorkspaceBudget,
    *,
    free_bytes: int | None,
    inventory_error: str | None = None,
) -> PurgePlan:
    """부피 회수 계획. 나이 → 예산 → 바닥, 셋 다 오래된 것부터.

    활성 잡과 고아는 **어떤 규칙으로도** 고르지 않는다(총량에는 남는다 — 디스크가 쥐고 있다).
    나이는 크기를 안 보고, 예산·바닥은 총량을 모르면 아무것도 안 고른다. 예산은 charged 로,
    바닥은 예상 회수량으로 잰다(모듈 docstring 「눈금이 둘이다」).
    """
    inventory = list(items)
    volume = None if inventory_error else _sum_or_none(i.bytes for i in inventory)
    evictable_total = (
        None if inventory_error else _sum_or_none(i.bytes for i in inventory if i.evictable)
    )
    non_evictable = (
        None if inventory_error else _sum_or_none(i.bytes for i in inventory if not i.evictable)
    )
    shared_total = None if inventory_error else _sum_or_none(i.shared_bytes for i in inventory)
    evictable_reclaimable = (
        None
        if inventory_error
        else _sum_or_none(i.estimated_reclaimable_bytes for i in inventory if i.evictable)
    )

    # 후보는 종료 잡뿐. 오래된 것부터 — 같은 시각이면 id 순.
    far_past = datetime.min.replace(tzinfo=now.tzinfo)
    candidates = sorted(
        (i for i in inventory if i.evictable),
        key=lambda i: (i.ended_at() or far_past, i.job_id),
    )

    chosen: dict[int, str] = {}

    # ① 나이 — 크기를 안 본다. 측정이 실패해도 그대로 돈다.
    keep = budget.days * DAY_SECONDS
    for c in candidates:
        ended = c.ended_at()
        if ended is not None and (now - ended).total_seconds() >= keep:
            chosen[c.job_id] = REASON_AGE

    def taken_bytes() -> int:
        return sum(c.bytes or 0 for c in candidates if c.job_id in chosen)

    # ② 예산 — 총량을 알 때만. 지울 수 없는 바이트만으로 이미 넘었으면 손대지 않는다.
    unreachable = (
        budget.max_bytes > 0 and non_evictable is not None and non_evictable > budget.max_bytes
    )
    over: int | None = None
    if volume is not None and budget.max_bytes > 0:
        remaining = volume - taken_bytes()
        if not unreachable:
            for c in candidates:
                if remaining <= budget.max_bytes:
                    break
                if c.job_id in chosen:
                    continue
                chosen[c.job_id] = REASON_BUDGET
                remaining -= c.bytes or 0
        over = max(0, remaining - budget.max_bytes)

    # ③ 바닥 — 여유를 알 때만. 예산이 못 이룰 목표여도 여기는 돈다(응급이고 한 걸음이 이득이다).
    # 눈금은 **예상 회수량**이다 — 공유 블록은 지워도 여유가 안 는다. 모르는 회수량은 0 으로
    # 본다(하한이라 더 고르는 쪽으로 틀린다 — 「지워도 안 는다」는 거짓보다 낫다).
    floor_attempted = (
        free_bytes is not None and budget.min_free_bytes > 0 and free_bytes < budget.min_free_bytes
    )
    short: int | None = None
    if free_bytes is not None and volume is not None and budget.min_free_bytes > 0:
        projected = free_bytes + sum(
            c.estimated_reclaimable_bytes or 0 for c in candidates if c.job_id in chosen
        )
        for c in candidates:
            if projected >= budget.min_free_bytes:
                break
            if c.job_id in chosen:
                continue
            chosen[c.job_id] = REASON_FREE
            projected += c.estimated_reclaimable_bytes or 0
        short = max(0, budget.min_free_bytes - projected)

    picked = [c for c in candidates if c.job_id in chosen]
    return PurgePlan(
        items=tuple(
            PurgeItem(
                job_id=c.job_id,
                workspace_bytes=c.workspace_bytes,
                snapshot_bytes=c.snapshot_bytes,
                reason=chosen[c.job_id],
                shared_bytes=c.shared_bytes,
            )
            for c in picked
        ),
        known_freed_bytes=sum(c.bytes for c in picked if c.bytes is not None),
        unknown_freed_count=sum(1 for c in picked if c.bytes is None),
        estimated_reclaimable_bytes=sum(
            r for c in picked if (r := c.estimated_reclaimable_bytes) is not None
        ),
        volume_bytes=volume,
        evictable_bytes=evictable_total,
        non_evictable_bytes=non_evictable,
        shared_bytes=shared_total,
        evictable_reclaimable_bytes=evictable_reclaimable,
        over_budget_bytes=over,
        projected_short_free_bytes=short,
        budget_unreachable=unreachable,
        floor_attempted=floor_attempted,
        inventory_error=inventory_error,
    )
