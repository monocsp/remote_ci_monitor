"""기동 진행 — 단계가 끝날 때마다 한 줄(`core/startup.py`). 순수 함수와 가짜 시계만 쓴다.

`rcm serve` 는 포트 → DB → 앱 → 서비스 순으로 뜨고 그 순서가 곧 안전장치인데(2026-09-10 사고),
지금까지는 **다 끝난 뒤에야** 「listening」 한 줄이 나왔다. 마이그레이션이나 mDNS 가 느리면
그동안 로그가 비어 있어서, 프리셋을 넣고 재시작했을 때 정작 보고 싶은 구간이 안 보인다.
"""

from __future__ import annotations

from remote_ci_monitor.core.startup import Startup, step_line

# ── step_line — 순수 포맷 ─────────────────────────────────────────────────────


def test_step_line_carries_the_position_and_the_name():
    assert step_line(1, 5, "port") == "start 1/5 port"


def test_step_line_carries_the_detail_after_a_separator():
    assert step_line(2, 5, "database", "schema v10") == "start 2/5 database · schema v10"


def test_step_line_carries_the_seconds_it_took():
    assert step_line(2, 5, "database", "schema v10", 0.42) == (
        "start 2/5 database · schema v10 (0.42s)"
    )


def test_step_line_without_a_detail_still_carries_the_seconds():
    assert step_line(3, 5, "presets", None, 1.5) == "start 3/5 presets (1.50s)"


def test_unknown_seconds_are_left_out_rather_than_printed_as_zero():
    # 집안 규칙: 모르는 값은 0 이 아니라 아예 싣지 않는다
    assert step_line(1, 5, "port") == "start 1/5 port"
    assert "(" not in step_line(1, 5, "port", "http://0.0.0.0:8787")


def test_an_empty_detail_is_the_same_as_no_detail():
    assert step_line(1, 5, "port", "") == step_line(1, 5, "port", None)


# ── Startup — 단계마다 한 줄, 시계는 주입 ──────────────────────────────────────


class _Clock:
    """가짜 단조 시계 — `tick()` 한 번이 1초."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def tick(self, seconds: float = 1.0) -> None:
        self.t += seconds


def _startup(total: int) -> tuple[Startup, list[str], _Clock]:
    lines: list[str] = []
    clock = _Clock()
    return Startup(total, log=lines.append, now=clock), lines, clock


def test_one_line_per_step_numbered_from_one():
    st, lines, _ = _startup(3)
    st.step("port")
    st.step("database")
    st.step("presets")
    assert [line.split(" ")[1] for line in lines] == ["1/3", "2/3", "3/3"]


def test_each_step_is_timed_from_the_end_of_the_one_before():
    st, lines, clock = _startup(2)
    clock.tick(0.5)
    st.step("port")
    clock.tick(2.0)
    st.step("database")
    assert lines[0].endswith("(0.50s)")
    assert lines[1].endswith("(2.00s)")  # 앞 단계의 0.5초가 두 번 세어지지 않는다


def test_a_given_seconds_wins_over_the_clock():
    """일이 끝난 시각과 줄을 찍는 시각이 다를 때가 있다 — `serve()` 의 port·database."""
    st, lines, clock = _startup(2)
    clock.tick(9.0)  # 그 사이 DB 를 열었다
    st.step("port", seconds=0.25)
    assert lines[0].endswith("(0.25s)")


def test_a_given_seconds_still_moves_the_mark_for_the_next_step():
    st, lines, clock = _startup(2)
    clock.tick(9.0)
    st.step("port", seconds=0.25)  # 잰 값은 0.25 지만 시계는 9.0 에 와 있다
    clock.tick(1.0)
    st.step("database")
    assert lines[1].endswith("(1.00s)")  # 앞의 9초가 다음 단계로 새지 않는다


def test_the_total_grows_when_more_steps_arrive_than_were_declared():
    # `core/progress.py` 가 스텝 마커에 쓰는 규칙과 같다 — 선언보다 많으면 총계가 는다
    st, lines, _ = _startup(1)
    st.step("port")
    st.step("database")
    assert lines[0].startswith("start 1/1 ")
    assert lines[1].startswith("start 2/2 ")


def test_a_detail_reaches_the_line():
    st, lines, _ = _startup(1)
    st.step("presets", "10 · gate, gate-fast")
    assert "presets · 10 · gate, gate-fast" in lines[0]


def test_nothing_is_logged_before_the_first_step():
    _, lines, _ = _startup(4)
    assert lines == []


# ── 선언한 개수 ↔ 실제로 켜는 것 ──────────────────────────────────────────────


def test_the_declared_total_matches_what_the_server_actually_turns_on(tmp_path):
    """`STARTUP_STEPS` 가 실제 단계 수와 어긋나면 빨개진다.

    서비스를 하나 더 켜면서 상수를 안 올리면 줄마다 총계가 달라진다(`… /10` 뒤에 `11/11`).
    사람이 로그를 보고 「9/10 에서 멈췄나?」를 판단할 수 있으려면 그 둘이 같아야 한다.
    """
    from remote_ci_monitor.config import ServerConfig
    from remote_ci_monitor.server import STARTUP_STEPS, App
    from remote_ci_monitor.store import Store

    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    store = Store(cfg.data_dir / "rcm.sqlite3")
    lines: list[str] = []
    progress = Startup(STARTUP_STEPS, log=lines.append, now=lambda: 0.0)
    # `serve()` 가 App 보다 먼저 찍는 셋
    progress.step("port")
    progress.step("database")
    progress.step("presets")
    app = App(cfg, store)
    try:
        app.start(progress=progress)
    finally:
        app.shutdown()
        store.close()

    assert len(lines) == STARTUP_STEPS, [line.split(" ")[2] for line in lines]
    totals = {line.split(" ")[1].split("/")[1] for line in lines}
    assert totals == {str(STARTUP_STEPS)}  # 줄마다 총계가 같다 — 늘어난 줄이 없다
    assert lines[-1].startswith(f"start {STARTUP_STEPS}/{STARTUP_STEPS} ")


def test_start_without_a_progress_object_stays_quiet(tmp_path):
    # 테스트와 안에서 서버를 띄우는 곳은 진행 표시를 안 넘긴다 — 그래도 떠야 한다
    from remote_ci_monitor.config import ServerConfig
    from remote_ci_monitor.server import App
    from remote_ci_monitor.store import Store

    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    store = Store(cfg.data_dir / "rcm.sqlite3")
    app = App(cfg, store)
    try:
        app.start()  # 예외 없이 끝나면 된다
    finally:
        app.shutdown()
        store.close()
