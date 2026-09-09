"""터미널 렌더(M5f) — 보류 레인이 **보여야** 한다. 명세는 docs/m5f-workplan.md §5.4.

잠그는 모양:
- 오늘 `lanes >= 2` 머리줄은 `busy` 수와 `down` 목록만 찍는다 — 보류 레인이 **아예 안 보이고**
  idle 일 때와 글자 하나까지 같다. 세는 자리를 새로 넣는다: `lanes 1/2 busy · 1 held (cpu 92%)`.
- `lanes == 1` 이면 오늘처럼 필 하나로 접는다(결정 12). 레인 1 은 게이트를 안 지나므로 `held` 가
  나올 수 없다 — 그 줄은 바이트 단위로 그대로다.
- 원격 필은 상태 문자열을 그대로 찍으므로 `build-02/2 held` 가 저절로 나온다.
- 큐 행 사유는 `held by load · cpu 92%`. 숫자는 **행이 아니라 워커**에서 온다(`hold_detail` 은
  `server.workers[]` 에 있다) — 행 키를 늘리지 않고 렌더러가 이미 받는 workers 를 쓴다.
"""

from __future__ import annotations

from typing import Any

from remote_ci_monitor.core.render_text import render, render_pool
from test_render_m5 import doc


def held_lane(lane: int, *, worker: str | None = None, code: str = "cpu_busy", busy: float = 92.4):
    return {
        "lane": lane,
        "state": "held",
        "job_id": None,
        "error": None,
        "since": "2026-09-08T12:00:00Z",
        "worker": worker,
        "display_name": f"{worker}/{lane}" if worker else None,
        "pool": "default",
        "hold_code": code,
        "hold_detail": {"cpu_busy": busy} if code == "cpu_busy" else None,
        "held_since": "2026-09-08T12:00:00Z",
    }


def lane(lane: int, state: str, job_id: int | None = None):
    return {
        "lane": lane,
        "state": state,
        "job_id": job_id,
        "error": None,
        "since": "2026-09-08T12:00:00Z",
        "worker": None,
        "display_name": None,
        "pool": "default",
        "hold_code": None,
        "hold_detail": None,
        "held_since": None,
    }


def with_workers(lanes: int, workers: list[dict[str, Any]]) -> dict[str, Any]:
    d = doc()
    d["server"]["lanes"] = lanes
    d["server"]["workers"] = workers
    return d


def header(status: dict[str, Any]) -> str:
    return render(status).splitlines()[0]


# ── 머리줄 ──────────────────────────────────────────────────────────────────


def test_a_held_local_lane_is_counted_in_the_header():
    """오늘은 idle 일 때와 글자 하나까지 같다 — 레인이 왜 노는지 알 수 없다."""
    line = header(with_workers(2, [lane(1, "busy", 412), held_lane(2)]))
    assert "lanes 1/2 busy" in line and "1 held (cpu 92%)" in line


def test_a_held_lane_without_a_number_says_only_why():
    line = header(with_workers(2, [lane(1, "busy", 412), held_lane(2, code="no_sample")]))
    assert "1 held (no sample)" in line


def test_two_held_lanes_show_the_highest_number():
    workers = [lane(1, "busy", 412), held_lane(2, busy=70.0), held_lane(3, busy=95.5)]
    assert "2 held (cpu 96%)" in header(with_workers(3, workers))


def test_a_held_lane_is_not_counted_as_busy_or_down():
    line = header(with_workers(2, [lane(1, "idle"), held_lane(2)]))
    assert "lanes 0/2 busy" in line and "DOWN" not in line


def test_one_lane_collapses_to_a_single_pill_as_before():
    """레인 1 은 게이트를 안 지나므로 held 가 나올 수 없다 — 이 줄은 오늘 그대로다."""
    assert "worker busy #412" in header(with_workers(1, [lane(1, "busy", 412)]))


def test_a_remote_held_lane_prints_its_state_verbatim():
    workers = [lane(1, "busy", 412), held_lane(2, worker="build-02")]
    assert "build-02/2 held" in header(with_workers(2, workers))


def test_a_header_without_held_lanes_is_unchanged():
    before = header(with_workers(2, [lane(1, "busy", 412), lane(2, "idle")]))
    assert before.endswith("lanes 1/2 busy")


# ── 큐 행 사유 ──────────────────────────────────────────────────────────────


def queue_row(reason: str = "held_by_load") -> dict[str, Any]:
    d = doc()
    row = dict(d["pools"][0]["queue"][0])
    row.update({"reason": reason, "position": 1, "lane": None, "state": "queued"})
    d["pools"][0]["queue"] = [row]
    return d


def test_the_queue_row_says_held_by_load_with_the_number_from_the_workers():
    """`hold_detail` 은 `server.workers[]` 에 있다 — 행 키를 늘리지 않고 workers 를 쓴다."""
    d = queue_row()
    workers = [lane(1, "busy", 412), held_lane(2)]
    lines = render_pool(d["pools"][0], server={"workers": workers}, workers=workers)
    assert any("held by load · cpu 92%" in line for line in lines), lines


def test_the_queue_row_without_a_cpu_number_says_only_the_code():
    d = queue_row()
    workers = [lane(1, "busy", 412), held_lane(2, code="no_sample")]
    lines = render_pool(d["pools"][0], server={"workers": workers}, workers=workers)
    assert any("held by load · no sample" in line for line in lines), lines


def test_the_queue_row_falls_back_when_no_lane_reports_a_hold():
    """워커 목록이 안 왔거나 이미 열렸으면 코드만 — 숫자를 지어내지 않는다."""
    d = queue_row()
    lines = render_pool(d["pools"][0], server={}, workers=[])
    assert any("held by load" in line for line in lines), lines


# ── rcm check ──────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self, dead: list[str] | None = None):
        self._dead = dead or []

    def health(self) -> dict[str, Any]:
        return {"pools_without_workers": self._dead}


def pools_row(workers: list[dict[str, Any]], lanes: int = 2):
    from remote_ci_monitor.cli import _pools_row

    doc = {
        "generated_at": "2026-09-08T12:00:00Z",
        "server": {"lanes": lanes, "workers": workers},
    }
    return _pools_row(doc, FakeClient())


def test_a_held_lane_is_shown_and_is_not_a_failure():
    """의도된 동작이다 — FAIL 이면 사람이 놀란다."""
    name, ok, text = pools_row([lane(1, "busy", 412), held_lane(2)])
    assert name == "pools" and ok is True
    assert "2 lanes · 1 held" in text


def test_a_lane_held_for_a_long_time_is_a_warning_not_a_failure():
    """「게이트가 제 일을 하는 중」과 「누가 두 시간째 잡고 있음」을 가른다."""
    old = dict(held_lane(2), held_since="2026-09-08T11:00:00Z")  # 한 시간 전
    _name, ok, text = pools_row([lane(1, "busy", 412), old])
    assert ok is None  # warn — FAIL 이 아니다
    assert "held for" in text


def test_a_short_hold_is_not_a_warning():
    _name, ok, _text = pools_row([lane(1, "busy", 412), held_lane(2)])
    assert ok is True
