"""터미널 렌더(M5a) — 큐 행 앞의 `↑`(high) / `↓`(low) · 헤더의 `cache N blobs · X MB` ·
`notify failures N`(0 이면 숨김). 명세는 docs/m5-workplan.md M5a-1 「표시」 · M5a-2 「저장 · 정리」
· M5a-3 「실패」. 순수 함수만 — 스키마 v1 dict 에 추가 키를 얹어 `render`/`render_queue_row` 에
넣는다.

구현보다 먼저 썼다(test-first). 추가 키가 없는 옛 문서(M4 서버)는 지금과 똑같이 그려져야 한다.
"""

from __future__ import annotations

import re
from datetime import UTC
from typing import Any

import pytest

from jobfactory import NOW
from remote_ci_monitor.core.model import RUNNING
from remote_ci_monitor.core.render_text import render, render_queue_row
from remote_ci_monitor.core.status import status_json
from test_render_text import rows
from test_status_schema import job, model


def doc(queue=None, **server_extra: Any) -> dict[str, Any]:
    """스키마 v1 문서. `server_extra` 는 `server` 에 얹는 추가 키(snapshot_cache 등)."""
    if queue is None:
        queue = rows(
            [job(412, state=RUNNING, created_min=2, started_min=1), job(413, created_min=1)],
            busy=[412],
        )
    d = status_json(model(queue=queue))
    d["server"].update(server_extra)
    return d


def queued_row(priority: int | None = None) -> dict[str, Any]:
    """queued 인 #413 행 dict. priority 가 None 이면 키 자체를 넣지 않는다(옛 서버)."""
    row = doc()["pools"][0]["queue"][1]
    assert row["state"] == "queued" and row["id"] == 413
    if priority is not None:
        row["priority"] = priority
    return row


def first_line(row: dict[str, Any]) -> str:
    return render_queue_row(row, UTC, NOW)[0]


def head_of(out: str) -> str:
    """`queue` 절 앞부분 — 헤더 줄(들)."""
    return out.split("queue", 1)[0]


# ── 큐 행: ↑ / ↓ ─────────────────────────────────────────────────────────────


def test_high_row_has_an_up_arrow_before_the_job_id():
    line = first_line(queued_row(1))
    assert "↑" in line, line
    assert line.index("↑") < line.index("#413"), line
    assert line.count("↑") == 1 and "↓" not in line, line  # queued 라 업로드 글리프는 없다


def test_low_row_has_a_down_arrow_before_the_job_id():
    line = first_line(queued_row(-1))
    assert "↓" in line, line
    assert line.index("↓") < line.index("#413"), line
    assert line.count("↓") == 1 and "↑" not in line, line


@pytest.mark.parametrize("priority", [0, None])
def test_normal_row_has_no_arrow(priority):
    line = first_line(queued_row(priority))
    assert "↑" not in line and "↓" not in line, line


def test_arrow_does_not_disturb_the_rest_of_the_row():
    """화살표는 접두일 뿐 — 나머지 조각(상태·key·ETA·신뢰도)은 normal 행과 같다."""
    normal = first_line(queued_row(0))
    high = first_line(queued_row(1))
    assert high.replace("↑", "").split() == normal.split(), (high, normal)


def test_running_row_can_be_high_too():
    row = doc()["pools"][0]["queue"][0]
    assert row["state"] == "running"
    row["priority"] = 1
    line = first_line(row)
    assert "↑" in line and line.index("↑") < line.index("#412"), line
    assert "▶" in line  # 상태 글리프는 그대로


def test_render_marks_priority_rows_in_the_queue_section():
    d = doc()
    q = d["pools"][0]["queue"]
    q[0]["priority"] = -1  # running · low
    q[1]["priority"] = 1  # queued · high
    out = render(d, tz=UTC)
    # 머리줄의 「worker busy #412」(결정 12)는 행이 아니다 — 큐 절에서 찾는다
    body = out.split("queue", 1)[1]
    running = next(ln for ln in body.splitlines() if "#412" in ln)
    queued = next(ln for ln in body.splitlines() if "#413" in ln)
    assert "↓" in running and running.index("↓") < running.index("#412"), running
    assert "↑" in queued and queued.index("↑") < queued.index("#413"), queued
    assert "1 running · 1 waiting" in out  # 머리 집계는 우선순위와 무관


# ── 헤더: cache N blobs · X MB ───────────────────────────────────────────────


def test_header_shows_cache_blobs_and_mb_when_present():
    out = render(doc(snapshot_cache={"blobs": 12, "bytes": 48_213_344}), tz=UTC)
    head = head_of(out)
    assert re.search(r"cache 12 blobs · 48(\.\d+)? MB", head), head


def test_header_cache_zero_is_shown_as_zero_not_hidden():
    # 0 blob 은 「캐시가 비었다」 — 모른다(키 없음)와 다르다
    out = render(doc(snapshot_cache={"blobs": 0, "bytes": 0}), tz=UTC)
    assert re.search(r"cache 0 blobs · 0(\.0+)? MB", head_of(out)), head_of(out)


def test_header_omits_cache_when_the_key_is_absent_or_null():
    assert "cache" not in head_of(render(doc(), tz=UTC))
    assert "cache" not in head_of(render(doc(snapshot_cache=None), tz=UTC))


def test_header_cache_with_unknown_numbers_uses_dashes_not_zero():
    out = render(doc(snapshot_cache={"blobs": None, "bytes": None}), tz=UTC)
    head = head_of(out)
    assert "cache" in head, head
    assert re.search(r"cache — blobs · — MB|cache —", head), head
    assert "cache 0 blobs" not in head, head


# ── notify failures N ────────────────────────────────────────────────────────


def test_notify_failures_shown_only_when_positive():
    out = render(doc(notify_failures=3), tz=UTC)
    assert "notify failures 3" in out, out
    assert "notify failures" not in render(doc(notify_failures=0), tz=UTC)
    assert "notify failures" not in render(doc(), tz=UTC)


def test_notify_failures_is_on_the_header_or_host_line_not_in_the_queue():
    out = render(doc(notify_failures=2), tz=UTC)
    lines = [ln for ln in out.splitlines() if "notify failures 2" in ln]
    assert len(lines) == 1, out
    line = lines[0]
    assert line.startswith("host") or line in head_of(out).splitlines(), line
    assert "#412" not in line and "#413" not in line


def test_notify_failures_do_not_turn_into_last_error():
    """알림 실패는 큐가 아픈 게 아니다 — `error ·` 줄이 생기면 안 된다."""
    out = render(doc(notify_failures=5), tz=UTC)
    assert "  error ·" not in out
