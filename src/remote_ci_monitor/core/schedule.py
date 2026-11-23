"""예약 실행의 시각 규칙(M5s §4.1 · 결정 207 · 208) — 순수 함수다. 벽시계를 보지 않는다.

제출은 `start_at`(문자열) 또는 `start_in_seconds`(정수)로 시각을 준다. 해석은 **서버**가 한다 —
클라이언트는 서버의 시간대를 모를 수 있다. 받는 꼴은 셋이다.

- 오프셋 있는 ISO-8601(`2026-11-23T15:00:00+09:00`, `…Z`) — 그대로.
- 오프셋 없는 `YYYY-MM-DDTHH:MM`(초는 있어도 된다) — 주어진 시간대로 읽는다.
- `HH:MM` — 그 시간대의 **오늘**. 지났으면 내일로 넘기지 않고 거절한다(결정 207).

오프셋 없는 시각이 서머타임 경계에 걸리면(없는 시각 · 두 번 오는 시각) 거절한다. 어느 쪽으로
골라도 사람이 생각한 시각과 한 시간 어긋날 수 있다.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: 거절 코드. 서버는 이 코드를 400 본문의 `code` · `error_code` 로 싣는다.
START_AT_INVALID = "start_at_invalid"
START_AT_PAST = "start_at_past"
START_AT_TOO_FAR = "start_at_too_far"
START_AT_AMBIGUOUS = "start_at_ambiguous"
START_AT_CONFLICT = "start_at_conflict"
START_IN_INVALID = "start_in_invalid"

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_LOCAL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ]([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")
#: 오프셋이 붙은 꼴 — `Z` · `+09:00` · `-0400`. 이 꼴만 `fromisoformat` 에 넘긴다(그 함수는
#: 너무 많은 꼴을 받는다 — `20261123` 같은 것까지)
_OFFSET = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:?\d{2})$"
)


class ScheduleError(ValueError):
    """제출의 시각을 받을 수 없다. `code` 는 위의 거절 코드 하나다."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _local_naive(naive: datetime, tz: tzinfo) -> datetime:
    """오프셋 없는 시각을 `tz` 로 읽어 UTC 로. 서머타임 경계면 `start_at_ambiguous`.

    `fold` 0 과 1 의 오프셋이 다르면 두 번 오는 시각이다. UTC 로 갔다 돌아와 글자가 바뀌면 없는
    시각이다(시계가 그 구간을 건너뛴다). 고정 오프셋 시간대에는 둘 다 일어나지 않는다.
    """
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ScheduleError(
            START_AT_AMBIGUOUS,
            f"start_at {naive:%Y-%m-%dT%H:%M} falls on a daylight saving change in "
            f"{_zone_name(tz)} — give an offset (e.g. +09:00)",
        )
    utc = first.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) != naive:
        raise ScheduleError(
            START_AT_AMBIGUOUS,
            f"start_at {naive:%Y-%m-%dT%H:%M} does not exist in {_zone_name(tz)} "
            "(daylight saving skips it) — give an offset (e.g. +09:00)",
        )
    return utc


def _zone_name(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or str(tz)


def parse_start_at(text: str, *, now: datetime, tz: tzinfo) -> datetime:
    """`start_at` 문자열 → UTC 시각. 지난 시각이면 `start_at_past`(내일로 넘기지 않는다)."""
    if not isinstance(text, str):
        raise ScheduleError(START_AT_INVALID, "start_at must be a string")
    raw = text.strip()
    got: datetime | None = None
    if m := _HHMM.match(raw):
        today = now.astimezone(tz).date()
        naive = datetime(today.year, today.month, today.day, int(m.group(1)), int(m.group(2)))
        got = _local_naive(naive, tz)
    elif m := _LOCAL.match(raw):
        try:
            naive = datetime(*(int(g) for g in m.groups(default="0")))
        except ValueError as e:
            raise ScheduleError(START_AT_INVALID, f"start_at {raw!r}: {e}") from e
        got = _local_naive(naive, tz)
    elif _OFFSET.match(raw):
        try:
            got = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError as e:
            raise ScheduleError(START_AT_INVALID, f"start_at {raw!r}: {e}") from e
    if got is None:
        raise ScheduleError(
            START_AT_INVALID,
            f"start_at {raw!r} is not a time — use HH:MM, YYYY-MM-DDTHH:MM or an ISO-8601 "
            "time with an offset",
        )
    if got <= now:
        raise ScheduleError(
            START_AT_PAST,
            f"start_at {raw} is in the past ({got:%Y-%m-%d %H:%M}Z) — it is not moved to tomorrow",
        )
    return got


def resolve_start(
    start_at: object,
    start_in_seconds: object,
    *,
    now: datetime,
    tz: tzinfo,
    max_ahead_days: int,
) -> datetime | None:
    """제출 본문의 두 키 → 승격할 UTC 시각. 둘 다 없으면 None(즉시 실행)."""
    if start_at is None and start_in_seconds is None:
        return None
    if start_at is not None and start_in_seconds is not None:
        raise ScheduleError(START_AT_CONFLICT, "give start_at or start_in_seconds, not both")
    if start_in_seconds is not None:
        if (
            isinstance(start_in_seconds, bool)
            or not isinstance(start_in_seconds, int)
            or start_in_seconds < 1
        ):
            raise ScheduleError(START_IN_INVALID, "start_in_seconds must be a positive integer")
        if start_in_seconds > max_ahead_days * 86400:
            raise ScheduleError(
                START_AT_TOO_FAR, f"start_in_seconds is more than {max_ahead_days} days ahead"
            )
        return now + timedelta(seconds=start_in_seconds)
    got = parse_start_at(start_at, now=now, tz=tz)  # type: ignore[arg-type]
    if got - now > timedelta(days=max_ahead_days):
        raise ScheduleError(START_AT_TOO_FAR, f"start_at is more than {max_ahead_days} days ahead")
    return got


def server_zone(name: str) -> tuple[tzinfo, str]:
    """오프셋 없는 시각을 읽을 시간대와 그 이름 — `[display] timezone`, 비면 서버 로컬.

    로컬은 IANA 이름을 찾는다(`TZ` → `/etc/localtime` 링크). 이름이 있어야 서머타임을 안다. 못
    찾으면 지금 오프셋으로 고정한다 — 그 시간대의 서머타임 경계는 판정하지 못한다.
    """
    if name:
        return ZoneInfo(name), name
    env = os.environ.get("TZ", "").lstrip(":")
    if env:
        try:
            return ZoneInfo(env), env
        except (ZoneInfoNotFoundError, ValueError):
            pass
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        key = target.split("zoneinfo/", 1)[1]
        try:
            return ZoneInfo(key), key
        except (ZoneInfoNotFoundError, ValueError):
            pass
    fixed = datetime.now().astimezone().tzinfo or UTC
    return fixed, str(fixed)


def effective_deadline_seconds(
    server_default: int, preset_value: int | None, body_value: object
) -> int:
    """시작 마감(초). 프리셋 값이 서버 기본을 덮고, 제출은 그보다 **짧게만** 줄 수 있다
    (결정 208)."""
    allowed = preset_value if preset_value is not None else server_default
    if body_value is None:
        return allowed
    if isinstance(body_value, bool) or not isinstance(body_value, int) or body_value < 1:
        raise ScheduleError("start_deadline_invalid", "start_deadline_seconds must be >= 1")
    if body_value > allowed:
        raise ScheduleError(
            "start_deadline_too_long",
            f"start_deadline_seconds can only shorten the deadline ({allowed}s for this preset)",
        )
    return body_value
