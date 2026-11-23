"""같은 브랜치의 옛 잡을 치우는 규칙 — M5o A (`docs/m5o-workplan.md` §2, 결정 109~116).

새 잡 N 이 `queued` 가 되는 순간, 같은 질문(프리셋 · 레포 · 브랜치 · 입력 · 풀)을 **같은 사람이**
먼저 던져 둔 옛 잡 O 는 답이 쓸모없어졌다. 세션이 손으로 하던 「내 잡 찾기 → 취소 → 다시 내기」를
서버가 맡는다. 순수 함수다 — 시계도 DB 도 안 본다. 치우는 일 자체는 `Store.supersede` 가 한
트랜잭션으로 하고, 그 트랜잭션 **안에서** 이 함수로 다시 판정한다(경쟁 · 합류자 · 상태 변화).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .model import (
    PLACEMENT_CAPABLE,
    RUNNING,
    WAITING_STATES,
    Job,
    Source,
)

SUPERSEDE_OFF = "off"
SUPERSEDE_WAITING = "waiting"
SUPERSEDE_ALL = "all"
#: 설정 값 셋(결정 110). 순서가 「덜 치움 → 더 치움」이다.
SUPERSEDE_MODES = (SUPERSEDE_OFF, SUPERSEDE_WAITING, SUPERSEDE_ALL)

#: 치운 잡의 `cancelled_by` · `cancel.by`, 그리고 요약 코드. 토큰 이름과 겹칠 수 있지만 요약 코드와
#: `{"by": N}` 이 함께 있어야 「밀려났다」로 읽는다(`successor_of`).
SUPERSEDED = "superseded"


def _text(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    return raw or None


def branch_of(source: Source) -> str | None:
    """잡의 브랜치 — tree 는 `source.branch`, git_ref 는 `source.ref`. 비었으면 None(규칙 3)."""
    return _text(source.branch if source.mode != "git_ref" else source.ref)


def repo_of(source: Source) -> str | None:
    """잡의 레포 이름. 비었으면 None — `origin` 이 없는 로컬 레포는 클라이언트가 None 을 보낸다.
    None 끼리를 「같은 레포」로 읽으면 서로 무관한 두 레포의 `main` 이 서로를 치운다(규칙 2)."""
    return _text(source.repo)


def _where(job: Job) -> tuple[str, str]:
    """어디서 묻는 질문인가 — 배치와 **기다린** 풀. `capable` 잡은 claim 때 `pool` 이 집은 워커의
    풀로 바뀌므로 `display_pool` 로 잰다(그것이 제출 때의 풀이다)."""
    if job.placement == PLACEMENT_CAPABLE:
        return job.placement, job.display_pool or job.pool
    return job.placement, job.pool


def _states(mode: str) -> frozenset[str]:
    if mode == SUPERSEDE_ALL:
        return WAITING_STATES | {RUNNING}
    if mode == SUPERSEDE_WAITING:
        return WAITING_STATES
    return frozenset()


def superseded_by(new: Job, active: Iterable[Job], mode: str) -> list[int]:
    """N 이 치울 옛 잡의 id(오름차순). 명세 §2.2 의 규칙 1~8 + 풀 · 빈 레포 · 지정 머신(§10).

    `cancelling` 은 언제나 뺀다 — 이미 누가 멈추는 중이다. 다시 요청하면 `kill_at` 이 밀리고
    요약이 사람의 취소를 덮는다(A18).
    """
    # 반복의 다음 시도(M5s)는 새 제출이 아니다 — 옛 sha 를 물려받고 id 만 새로 받았다. 그것을
    # 「새 잡」으로 보면 같은 브랜치에 그 사이 들어온 더 새 커밋의 잡을 치운다(#256 검증 P1)
    if new.retry_of is not None:
        return []
    states = _states(mode)
    branch = branch_of(new.source)
    repo = repo_of(new.source)
    # 머신을 고른 새 잡(`--machine` · 점검 잡)은 다른 질문이다 — 규칙 8 을 양쪽에 건다(§10)
    if not states or branch is None or repo is None or new.target_worker is not None:
        return []
    owner = new.requester.name
    out: list[int] = []
    for old in active:
        if old.id >= new.id or old.state not in states:
            continue
        if old.preset != new.preset:  # 규칙 1 — 모드는 N 의 프리셋 것
            continue
        if old.source.mode != new.source.mode or repo_of(old.source) != repo:  # 규칙 2
            continue
        if branch_of(old.source) != branch:  # 규칙 3
            continue
        if old.inputs != new.inputs:  # 규칙 4 — 다른 질문(결정 112)
            continue
        if old.source.identity == new.source.identity:  # 규칙 5 — 그건 합류다
            continue
        if old.requester.name != owner or any(j.name != owner for j in old.joiners):  # 규칙 6
            continue
        if old.target_worker is not None:  # 규칙 8 — 사람이 일부러 고른 머신
            continue
        if _where(old) != _where(new):  # 다른 머신에서 묻는 다른 질문(§10)
            continue
        out.append(old.id)
    return sorted(out)


def successor_of(job: Job) -> int | None:
    """이 잡을 치운 새 잡의 id. 밀려난 잡이 아니면 None.

    취소 주체(`cancelled_by` · 취소 중이면 `cancel.by`)가 `superseded` 이고 요약 코드가 같고
    인자의 `by` 가 정수일 때만이다 — 셋 중 하나라도 어긋나면 지어내지 않는다.
    """
    by_whom = job.cancelled_by or (job.cancel.by if job.cancel is not None else None)
    if by_whom != SUPERSEDED or job.summary_code != SUPERSEDED:
        return None
    return _int(job.summary_args.get("by"))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
