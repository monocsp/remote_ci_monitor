"""터미널 렌더(M5b-1) — `render` 가 `doc["pools"]` 를 순회한다. 풀 하나면 오늘과 바이트 단위로 같고,
둘이면 두 번째 풀은 `queue — N (pool linux[ · no workers])` 헤더와 자기 host 블록을 가진다. 머리줄은
풀 전체를 센다. `render_pool(pool, …)` 이 풀 하나를 그린다. 명세는 docs/m5-workplan.md 「M5b」 의
`pools[]` 항목(첫 풀만 보는 `render_text` 를 순회 구조로) · 「순서」 3 (M5b-1: 풀 하나일 때 화면은
그대로).

순수 함수만 — test_render_m5.doc() 의 스키마 v1 문서에 풀을 하나 더 복사해 넣는다.
구현보다 먼저 썼다(test-first).
"""

from __future__ import annotations

import copy
import inspect
from datetime import UTC
from typing import Any

from jobfactory import NOW
from remote_ci_monitor.core.render_text import render
from test_render_m5 import doc, head_of

# 오늘(M5a 시점) `render(doc(), tz=UTC)` 의 출력 그대로 — 풀 하나면 M5b-1 뒤에도 이와 같아야 한다.
GOLDEN = (
    "━━━ rcm · server · 00:52 local · worker busy #412\n"
    "queue — 2 jobs · 1 running · 1 waiting\n"
    "      ▶ running    #412 gate:full        org/app @abc123f+uncommitted ← alice@laptop"
    "       elapsed 1m 00s · waited 1m 00s eta 00:57 · in 5m 40s  (high · measured n=7)\n"
    "        running · lane 1\n"
    "   1. · queued     #413 gate:full        org/app @abc123f+uncommitted ← alice@laptop"
    "       waiting 1m 00s           eta 01:04 · in 12m 20s  (high · measured n=7)\n"
    "        waiting for lane · behind #412\n"
    "recent — no completed jobs yet\n"
    "medians: gate:full 6m 40s (n=7) · deploy-dev 10m 00s (n=3)\n"
    "host — no sample yet\n"
)


# ── 도우미 ───────────────────────────────────────────────────────────────────


def one_pool_doc() -> dict[str, Any]:
    return doc()


def waiting_row(base: dict[str, Any], job_id: int, position: int) -> dict[str, Any]:
    """queued #413 을 복사해 워커 없는 풀의 대기 행으로: reason worker_down · ETA 없음."""
    row = copy.deepcopy(base)
    row.update(id=job_id, position=position, reason="worker_down", ahead_job_id=None, lane=None)
    row["estimate"].update(finish_at=None, wait_seconds=None, remaining_seconds=None)
    return row


def running_row(base: dict[str, Any], job_id: int) -> dict[str, Any]:
    row = copy.deepcopy(base)
    row.update(id=job_id, lane=1)
    return row


def linux_pool(default_pool: dict[str, Any], *, lanes: int, running: bool) -> dict[str, Any]:
    """기본 풀을 복사해 `linux` 풀로. lanes 0 · hosts [] 면 「워커 없음」.

    running 이면 #511 이 lane 1 을 차지한 채 #512 · #513 이 기다리는 풀이 된다.
    """
    pool = copy.deepcopy(default_pool)
    q = default_pool["queue"]
    running_base = next(r for r in q if r["state"] == "running")
    queued_base = next(r for r in q if r["state"] == "queued")
    queue = [running_row(running_base, 511)] if running else []
    queue += [waiting_row(queued_base, 512, 1), waiting_row(queued_base, 513, 2)]
    pool.update(name="linux", lanes=lanes, hosts=[], queue=queue)
    return pool


def two_pool_doc(*, lanes: int = 0, running: bool = False) -> dict[str, Any]:
    d = one_pool_doc()
    d["pools"].append(linux_pool(d["pools"][0], lanes=lanes, running=running))
    return d


def default_section(out: str) -> str:
    """첫 `queue — ` 줄부터 두 번째 풀 헤더(`(pool ` 가 든 줄) 앞까지. 풀 하나면 끝까지."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("queue — "))
    end = next((i for i, ln in enumerate(lines) if "(pool " in ln), len(lines))
    assert start < end, out
    return "\n".join(lines[start:end])


def line_index(out: str, needle: str) -> int:
    for i, ln in enumerate(out.splitlines()):
        if needle in ln:
            return i
    raise AssertionError(f"{needle!r} not in output:\n{out}")


# ── 풀 하나: 오늘과 같다 ─────────────────────────────────────────────────────


def test_one_pool_output_is_byte_identical_to_today():
    out = render(one_pool_doc(), tz=UTC)
    assert out == GOLDEN
    assert "(pool " not in out  # 기본 풀 하나엔 풀 헤더가 없다


def test_no_pools_still_renders_unavailable_not_a_crash():
    """옛 서버·부분 문서 회귀: pools 가 비면 조회 실패 모양(오늘과 같다)."""
    d = one_pool_doc()
    d["pools"] = []
    out = render(d, tz=UTC)
    assert "queue — unavailable" in out
    assert "gate:full" not in out and "(pool " not in out  # 큐 행도 풀 헤더도 없다


# ── render_pool ──────────────────────────────────────────────────────────────


def test_render_pool_exists_and_renders_one_pool():
    from remote_ci_monitor.core.render_text import render_pool

    params = list(inspect.signature(render_pool).parameters)
    assert params and params[0] == "pool", params
    result = render_pool(one_pool_doc()["pools"][0], tz=UTC, now=NOW)
    text = result if isinstance(result, str) else "\n".join(result)
    assert "queue — 2 jobs · 1 running · 1 waiting" in text, text
    assert "#412" in text and "#413" in text
    assert "host — no sample yet" in text  # host 블록도 풀의 일부


# ── 풀 둘: 헤더 · 워커 없음 · 절 불변 · 머리줄 집계 ──────────────────────────


def test_two_pools_are_both_rendered_and_the_second_has_a_pool_header():
    out = render(two_pool_doc(), tz=UTC)
    for job in ("#412", "#413", "#512", "#513"):
        assert job in out, out
    assert "queue — 2 (pool linux · no workers)" in out, out
    heads = [ln for ln in out.splitlines() if ln.startswith("queue — ")]
    assert len(heads) == 2, heads
    # 기본 풀 → linux 풀 순서. linux 잡은 linux 헤더 아래에.
    assert line_index(out, "#413") < line_index(out, "(pool linux") < line_index(out, "#512")


def test_no_workers_pool_rows_say_worker_down_and_have_no_eta():
    out = render(two_pool_doc(lanes=0), tz=UTC)
    lines = out.splitlines()
    for job in ("#512", "#513"):
        i = line_index(out, job)
        assert "eta —" in lines[i], lines[i]
        assert "worker down" in lines[i + 1], lines[i + 1]  # 이유 줄
    assert "queue — 2 (pool linux · no workers)" in out


def test_pool_with_lanes_has_no_no_workers_suffix():
    out = render(two_pool_doc(lanes=1, running=True), tz=UTC)
    assert "queue — 3 (pool linux)" in out, out
    assert "no workers" not in out, out


def test_default_pool_section_is_unchanged_next_to_a_second_pool():
    one = render(one_pool_doc(), tz=UTC)
    two = render(two_pool_doc(), tz=UTC)
    assert default_section(two) == default_section(one)
    assert default_section(one) == GOLDEN.split("\n", 1)[1].rstrip("\n")


def test_head_line_counts_jobs_across_pools():
    two = render(two_pool_doc(lanes=1, running=True), tz=UTC)
    assert "2 running · 3 waiting" in head_of(two), head_of(two)  # 1+1 running · 1+2 waiting
    assert "queue — 2 jobs · 1 running · 1 waiting" in two  # 풀별 집계는 그대로
    down = render(two_pool_doc(lanes=0), tz=UTC)
    assert "1 running · 3 waiting" in head_of(down), head_of(down)
    # 풀 하나면 머리에 전체 집계를 덧붙이지 않는다(GOLDEN 이 잠근다)
    assert "running ·" not in head_of(render(one_pool_doc(), tz=UTC))


def test_each_pool_gets_its_own_host_block():
    out = render(two_pool_doc(), tz=UTC)
    hosts = [i for i, ln in enumerate(out.splitlines()) if ln.startswith("host")]
    assert len(hosts) == 2, out
    assert hosts[0] < line_index(out, "(pool linux") < hosts[1], out
