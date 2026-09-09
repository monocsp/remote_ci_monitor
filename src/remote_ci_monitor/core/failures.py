"""실패 이름의 최근 이력 — 순수 판정. `docs/m5h-implementation.md` §2.2.

잡이 `::rcm::fail::<이름>` 으로 지목한 이름이 **같은 key 의 최근 창**에서 몇 번 빨갰는지를
세는 것은 저장소(`store.failure_stats`)의 일이고, 그 숫자를 **판정 코드**로 바꾸는 것이 이
모듈이다. 문장은 만들지 않는다 — 서버는 코드를 내려보내고 화면과 CLI 가 각자의 말로 그린다
(결정 37). 판정을 서버가 하는 이유는 웹과 `rcm` 이 어긋날 수 없게 하려는 것이다(M1 결정 B).

창은 이 잡을 **포함해서** 세므로 잡이 선언한 이름의 `seen` 은 1 이상이다. `seen == 0` 갈래는
방어용이다 — 없는 증거로 「간헐」이라고 말하지 않는다.
"""

from __future__ import annotations

from collections.abc import Container, Sequence
from dataclasses import dataclass
from typing import Any

#: 표본이 얕거나(창 < min_jobs) 창 안에 증거가 없다 — 아무 말도 안 한다.
VERDICT_UNKNOWN = "unknown"
#: 창에서 이 이름을 가진 잡이 이 잡 하나다. 내 변경일 가능성이 크다.
VERDICT_FIRST_SEEN = "first_seen"
#: 왔다 갔다 한다. 「간헐?」은 판정이 아니라 제안이다(결정 68).
VERDICT_INTERMITTENT = "intermittent"
#: 창의 모든 잡에서 봤다. 간헐이 아니라 그냥 깨져 있다.
VERDICT_PERSISTENT = "persistent"


@dataclass(frozen=True)
class FailureRow:
    """한 이름이 창 안에서 몇 번, 어느 잡에서 보였나. 저장소가 채워 준다."""

    name: str
    seen: int
    first_seen_job_id: int | None
    last_seen_job_id: int | None


def verdict(seen: int, window: int, *, min_jobs: int) -> str:
    """창의 크기와 본 횟수로 판정 코드 하나. 순서가 규칙이다 — 위에서부터 먼저 맞는 것."""
    if window < min_jobs:
        return VERDICT_UNKNOWN  # 표본이 얕다. 이 규칙이 다른 셋보다 먼저다
    if seen <= 0:
        return VERDICT_UNKNOWN  # 창 안에 증거가 없다(방어용)
    if seen >= window:
        return VERDICT_PERSISTENT
    if seen == 1:
        return VERDICT_FIRST_SEEN
    return VERDICT_INTERMITTENT


def failures_json(
    rows: Sequence[FailureRow],
    *,
    steps: Container[str | None],
    window: int,
    window_unnamed: int,
    min_jobs: int,
) -> list[dict[str, Any]]:
    """`GET /jobs/{id}` 의 `failures[]`.

    줄 순서는 **잡이 찍은 순서**다(이름 순으로 정렬하지 않는다 — 첫 줄이 대개 진짜 원인이다).
    `steps` 는 그 잡의 스텝 이름이고, 여기 있는 이름은 스텝, 없으면 단위(테스트·파일)다.
    창과 그 품질(`window_unnamed`)은 줄마다 같이 실어 보낸다 — 분모를 숨기지 않는다(결정 68).
    """
    return [
        {
            "name": r.name,
            "step": r.name in steps,
            "seen": r.seen,
            "window": window,
            "window_unnamed": window_unnamed,
            "first_seen_job_id": r.first_seen_job_id,
            "last_seen_job_id": r.last_seen_job_id,
            "verdict": verdict(r.seen, window, min_jobs=min_jobs),
        }
        for r in rows
    ]


__all__ = [
    "VERDICT_UNKNOWN",
    "VERDICT_FIRST_SEEN",
    "VERDICT_INTERMITTENT",
    "VERDICT_PERSISTENT",
    "FailureRow",
    "verdict",
    "failures_json",
]
