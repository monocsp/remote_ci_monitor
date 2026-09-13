"""부하 게이트의 판정 — 순수 함수 하나. I/O 도 시계도 없고 `now` 는 인자다(M5f §4.3).

레인 2 부터는 호스트가 한가할 때만 잡을 집는다. **레인 1 은 게이트를 안 지난다** — 머신마다
레인 하나는 언제나 돌아야 큐가 멈추지 않는다(오너 결정 40).

규칙은 하나로 요약된다: **모르면 닫는다.** 표본이 없거나 낡았거나 창이 끊겼으면 여는 게 아니라
닫는다. 그래서 이 파일에는 「모르는 채로 여는」 분기가 하나도 없다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from remote_ci_monitor.core.hostparse import STALE_MULTIPLIER
from remote_ci_monitor.core.model import HostSample
from remote_ci_monitor.core.status import parse_iso

#: 보류 사유(결정 37 — 서버는 문장이 아니라 코드를 내려보낸다).
HOLD_CPU_BUSY = "cpu_busy"
HOLD_NO_SAMPLE = "no_sample"
HOLD_COOLDOWN = "cooldown"

#: 창의 항목 간격 상한 배수. 이보다 벌어지면 샘플러가 쉰 것이라 창을 못 믿는다.
GAP_MULTIPLIER = 2.0
#: `interval_seconds` 가 0 이나 음수여도 모든 창이 「끊김」이 되지 않게 하는 하한.
MIN_INTERVAL_SECONDS = 1.0

POLICY_LOAD = "load"
POLICY_ALWAYS = "always"


@dataclass(frozen=True)
class AdmissionConfig:
    """계산에 필요한 설정만. 서버 설정(`config.py`)에서 뽑아 만든다 — `core/queue.QueueConfig` 와
    같은 방식이라 순수 계층이 `config.py` 를 모른다."""

    policy: str = POLICY_LOAD
    cpu_max_percent: float = 80.0
    samples: int = 3
    cooldown_seconds: float = 30.0
    #: 표본이 이보다 늙으면 못 믿는다. 서버가 표본 종류에 맞춰 계산해 넣는다(로컬은
    #: `3 × [host] interval_seconds`, 원격은 heartbeat 주기도 함께 본다).
    stale_seconds: float = 15.0


@dataclass(frozen=True)
class Hold:
    """레인이 왜 막혔나. `detail` 은 화면이 숫자를 그릴 재료다(`cpu_busy` 일 때만 있다)."""

    code: str
    detail: dict[str, Any] | None = None


def _number(value: Any) -> float | None:
    """유한한 수만 통과시킨다. bool 은 수가 아니다(`True <= 80` 은 참이라 조용히 샌다)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    f = float(value)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _at_of(item: Any) -> datetime | None:
    """항목의 `at`. 모양이 어긋나면 None — `parse_iso` 는 깨진 문자열에 예외를 던진다.

    통짜 `try/except` 로 `decide` 전체를 감싸지 않는 이유: 그러면 진짜 버그가 「표본 없음」으로
    숨는다. 예외가 날 수 있는 **이 한 줄만** 감싼다.
    """
    if not isinstance(item, Mapping):
        return None
    try:
        return parse_iso(item.get("at"))
    except (ValueError, TypeError):
        return None


def _window_is_broken(window: Sequence[Mapping[str, Any]], interval: float) -> bool:
    """창의 항목이 시간으로도 이어져 있나.

    **항목끼리만 비교한다.** `history[].at` 은 표본을 만든 머신의 시계이고 `sampled_at` 은 서버가
    찍은 시각이라(원격 표본은 받은 시각으로 다시 찍힌다) 둘을 빼면 시계 차가 섞인다.

    개수만 세면 샘플러가 10분 쉰 뒤에도 「오래된 둘 + 새것 하나」로 창이 차서 관측 하나로 열린다.
    """
    gap = GAP_MULTIPLIER * max(interval, MIN_INTERVAL_SECONDS)
    previous: datetime | None = None
    for item in window:
        at = _at_of(item)
        if at is None:
            return True
        if previous is not None and (at - previous).total_seconds() > gap:
            return True
        previous = at
    return False


def decide(
    *,
    lane: int,
    sample: HostSample | None,
    now: datetime,
    last_admit_at: datetime | None,
    cfg: AdmissionConfig,
) -> Hold | None:
    """이 레인이 지금 잡을 집어도 되나. `None` 이면 열림, 아니면 왜 막혔는지.

    순서는 **규범이다** — 화면이 「왜 안 움직이나」를 한 가지로만 말해야 하기 때문에, 낡은 표본은
    상한을 넘었어도 `no_sample` 이고 쿨다운은 창을 걷기 전에 본다.
    """
    if lane <= 1:  # 레인은 1부터지만 방어로 `<=` — 0 이 들어와도 잠기지 않는다
        return None
    if cfg.policy != POLICY_LOAD:
        return None
    if sample is None or (now - sample.sampled_at).total_seconds() > cfg.stale_seconds:
        return Hold(HOLD_NO_SAMPLE)
    if not isinstance(sample.cpu, Mapping) or _number(sample.cpu.get("busy")) is None:
        return Hold(HOLD_NO_SAMPLE)  # 표본은 cpu 없이도 만들어진다(`hostsample.py`)
    if last_admit_at is not None and (now - last_admit_at).total_seconds() < cfg.cooldown_seconds:
        return Hold(HOLD_COOLDOWN)
    history = sample.history or ()
    if len(history) < cfg.samples:
        return Hold(HOLD_NO_SAMPLE)
    window = tuple(history[-cfg.samples :])
    values = [
        _number(item.get("cpu_busy")) if isinstance(item, Mapping) else None for item in window
    ]
    if any(v is None for v in values):
        return Hold(HOLD_NO_SAMPLE)  # 수집이 실패해도 항목은 들어간다 — 그 창은 못 믿는다
    if cfg.samples > 1 and _window_is_broken(window, sample.interval_seconds):
        return Hold(HOLD_NO_SAMPLE)
    highest = max(values)  # type: ignore[type-var]
    if highest > cfg.cpu_max_percent:
        # **최신값이 아니라 최댓값**이다 — 막은 값이 그것이다
        return Hold(HOLD_CPU_BUSY, {"cpu_busy": highest})
    return None


__all__ = [
    "STALE_MULTIPLIER",
    "AdmissionConfig",
    "Hold",
    "decide",
    "HOLD_CPU_BUSY",
    "HOLD_NO_SAMPLE",
    "HOLD_COOLDOWN",
    "POLICY_LOAD",
    "POLICY_ALWAYS",
]
