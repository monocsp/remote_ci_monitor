"""기동 진행 — 단계가 끝날 때마다 한 줄. 순수 · 시계는 받는다.

`rcm serve` 는 포트 → DB → 앱 → 서비스 순으로 뜨고, 그 순서 자체가 안전장치다(2026-09-10 사고,
docs/operating.md 「From a git checkout」). 그런데 지금까지는 **다 끝난 뒤에야** 「listening」
한 줄이 나왔다. 마이그레이션이나 mDNS 가 느리면 그동안 로그가 비어 있어서, 프리셋을 넣고
재시작했을 때 정작 보고 싶은 구간이 안 보인다.

한 줄의 모양(초는 그 단계에만 걸린 시간이다):

    start 1/9 port · http://0.0.0.0:8787 (0.01s)
    start 2/9 database · schema v10 (0.42s)
    start 3/9 presets · 10 · gate, gate-fast, gate-commit … (0.00s)

집안 규칙대로 모르는 값은 0 이 아니라 아예 싣지 않는다 — 초를 모르면 괄호가 통째로 빠진다.
"""

from __future__ import annotations

from collections.abc import Callable

SEP = " · "


def step_line(
    index: int,
    total: int,
    name: str,
    detail: str | None = None,
    seconds: float | None = None,
) -> str:
    """`start 2/5 database · schema v10 (0.42s)`. `detail`·`seconds` 는 없으면 빠진다."""
    line = f"start {index}/{total} {name}"
    if detail:
        line += f"{SEP}{detail}"
    if seconds is not None:
        line += f" ({seconds:.2f}s)"
    return line


class Startup:
    """선언한 개수만큼 단계를 세며 한 줄씩 남긴다.

    총계를 미리 아는 게 스텝 마커(`core/progress.py`)와 다른 점이라 「N/M (so far)」가 없다.
    그래도 선언보다 많은 단계가 오면 총계를 늘린다 — 거기서도 같은 규칙이다.
    """

    def __init__(self, total: int, *, log: Callable[[str], None], now: Callable[[], float]) -> None:
        self.total = total
        self._log = log
        self._now = now
        self._index = 0
        self._mark = now()

    def step(self, name: str, detail: str | None = None, *, seconds: float | None = None) -> None:
        """단계 하나가 끝났다고 알린다. 걸린 시간은 **앞 단계가 끝난 뒤부터** 센다.

        `seconds` 를 주면 그 값을 싣는다 — 일이 끝난 시각과 줄을 찍는 시각이 다를 때가 있다.
        `serve()` 의 port·database 가 그렇다: 포트를 먼저 잡되(안전 순서), 줄은 DB 가 열린 뒤에
        찍는다. DB 를 못 열면 거절은 `rcm:` **한 줄**이어야 하기 때문이다(2026-09-10 사고).
        """
        self._index += 1
        at = self._now()
        if seconds is None:
            seconds = at - self._mark
        self._mark = at
        self._log(step_line(self._index, max(self.total, self._index), name, detail, seconds))


def noop_step(name: str, detail: str | None = None, *, seconds: float | None = None) -> None:
    """진행 표시가 없을 때 쓰는 빈 자리 — 호출부가 `if progress` 로 갈라지지 않게."""
