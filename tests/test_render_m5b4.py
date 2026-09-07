"""터미널 렌더(M5b-4) — 원격 풀 빈 큐 헤더의 풀 라벨 · paused 문구 · 머리줄 원격 워커 필 접기.
명세는 docs/m5b4-workplan.md §1 (`rcm top`).

잠그는 모양(구현 전 — test-first):
- 기본 풀 밖의 풀 블록 헤더는 **언제나** 풀 이름을 단다. 잡이 없고 lanes ≥ 1 이면
  `queue — empty (pool linux)` — 오늘은 기본 문구(`rcm run <preset> starts immediately`)가 나와
  어느 풀인지 알 수 없다. lanes 0 은 M5b-1 그대로 `queue — empty (pool linux · no workers)`,
  조회 실패는 `queue — unavailable: <error> (pool linux)`.
- 서버가 paused 면 원격 빈 풀은 `queue — empty (pool linux) · paused`. lanes 0 이면 `no workers`
  라벨이 이미 말한다(`(pool linux · no workers)` 는 남고 기본 풀 문구를 빌리지 않는다).
- 로컬 레인이 down 인 것은 원격 풀의 사정이 아니다 — 원격 빈 풀 헤더는 `queue — empty (pool linux)`.
- 기본 풀의 빈 큐·paused·조회 실패 줄은 바이트 단위로 오늘 그대로. 풀 하나면 GOLDEN 그대로.
- 원격 풀 블록 안의 `recent — …` · `medians: …` · `host — …` 줄은 라벨 없이 오늘 그대로.
- 머리줄 원격 워커 필(M5b-2 `build-02/1 idle`)은 5개까지 그대로, 6개부터는 앞 5개 뒤에
  ` · +N workers`(N = 접힌 수). down 워커는 접지 않는다 — 어디에 있든 항상 보인다. 접힌 꼬리는
  `PAUSED by` · `cache` · 풀 집계보다 앞.

순수 함수만 — test_render_m5.doc() 의 스키마 v1 문서에 풀·워커 항목을 얹는다.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest

from remote_ci_monitor.core.render_text import render
from test_render_m5 import doc
from test_render_m5b import GOLDEN, linux_pool, one_pool_doc
from test_render_m5b2 import body, head_line, local_doc, remote, with_workers

PAUSED = {"by": "macmini-admin", "at": "2026-09-04T00:50:00Z"}
DEFAULT_EMPTY = "queue — empty (rcm run <preset> starts immediately)"
DEFAULT_PAUSED = "queue — empty but paused/no worker — nothing will start"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def empty_linux_doc(*, lanes: int = 1, queue: list | None = None, **server_extra: Any) -> dict:
    """풀 둘 — 기본 풀은 doc() 그대로(잡 2), linux 풀은 잡이 없다(queue []). lanes 로 워커 유무."""
    d = local_doc(**server_extra)
    linux = linux_pool(d["pools"][0], lanes=lanes, running=False)
    linux["queue"] = [] if queue is None else queue
    d["pools"].append(linux)
    return d


def empty_default(d: dict) -> dict:
    """기본 풀의 큐도 비운다(빈 큐 문구 회귀용)."""
    d["pools"][0]["queue"] = []
    return d


def local_down(d: dict) -> dict:
    d["server"]["workers"][0].update(state="down", job_id=None, error="ENOSPC")
    return d


def queue_heads(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("queue — ")]


def linux_header(out: str) -> str:
    """linux 풀 블록의 헤더 줄 — 두 번째 `queue — ` 줄(첫 줄은 기본 풀)."""
    heads = queue_heads(out)
    assert len(heads) == 2, out
    return heads[1]


def split_sections(out: str) -> tuple[list[str], list[str]]:
    """(기본 풀 절, linux 풀 절) — 첫 `queue — ` 줄부터 두 번째 `queue — ` 줄 앞까지, 그리고 그 뒤.
    test_render_m5b.default_section 과 달리 풀 라벨(`(pool `)에 기대지 않는다 — 라벨이 없는
    오늘의 출력에서도 절이 제대로 갈린다."""
    lines = out.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.startswith("queue — ")]
    assert len(heads) == 2, out
    return lines[heads[0] : heads[1]], lines[heads[1] :]


def linux_section(out: str) -> list[str]:
    """linux 헤더부터 끝까지의 줄들."""
    return split_sections(out)[1]


def idle_workers(n: int, *, first: int = 2) -> list[dict[str, Any]]:
    """build-02 부터 n 개의 idle 원격 워커(레인 1)."""
    return [remote(f"build-{first + i:02d}", 1, "idle") for i in range(n)]


def pills(head: str) -> list[str]:
    """머리줄의 로컬 요약 뒤 조각들(` · ` 로 나눈다)."""
    return head.split(" · ")


# ── 원격 풀 빈 큐 헤더 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("lanes", [1, 2])
def test_empty_remote_pool_with_workers_names_the_pool(lanes):
    """잡이 없고 워커가 있는 원격 풀 — `queue — empty (pool linux)`. 기본 문구를 빌리지 않는다."""
    out = render(empty_linux_doc(lanes=lanes), tz=UTC)
    assert linux_header(out) == "queue — empty (pool linux)"
    assert "starts immediately" not in linux_header(out)


def test_empty_remote_pool_without_workers_is_unchanged():
    out = render(empty_linux_doc(lanes=0), tz=UTC)
    assert linux_header(out) == "queue — empty (pool linux · no workers)"


def test_unavailable_remote_queue_names_the_pool():
    d = empty_linux_doc(lanes=1)
    d["pools"][1].update(queue=None, queue_error="database locked")
    out = render(d, tz=UTC)
    assert linux_header(out) == "queue — unavailable: database locked (pool linux)"
    # lanes 0 이면 M5b-1 의 라벨 그대로 — 어느 쪽이든 풀 이름은 붙는다
    d["pools"][1]["lanes"] = 0
    assert linux_header(render(d, tz=UTC)).startswith(
        "queue — unavailable: database locked (pool linux"
    )


def test_paused_server_marks_the_empty_remote_pool_with_a_paused_suffix():
    out = render(empty_linux_doc(lanes=1, paused=PAUSED), tz=UTC)
    assert linux_header(out) == "queue — empty (pool linux) · paused"
    assert "PAUSED by macmini-admin" in head_line(out)  # 머리줄은 M5b-2 그대로


def test_paused_server_with_no_workers_keeps_the_no_workers_label():
    """lanes 0 이면 `no workers` 가 이미 말한다 — 기본 풀의 paused 문구를 빌리지 않는다."""
    header = linux_header(render(empty_linux_doc(lanes=0, paused=PAUSED), tz=UTC))
    assert header.startswith("queue — empty (pool linux · no workers)"), header
    assert "nothing will start" not in header


def test_local_lanes_down_do_not_change_the_remote_pool_header():
    """로컬 레인이 전부 down 이어도 원격 풀의 워커는 살아 있다 — 원격 헤더는 그대로."""
    d = local_down(empty_default(empty_linux_doc(lanes=1)))
    out = render(d, tz=UTC)
    heads = queue_heads(out)
    assert heads == [DEFAULT_PAUSED, "queue — empty (pool linux)"], heads


def test_remote_pool_header_is_the_only_line_that_changes():
    """원격 풀 블록의 나머지 줄(recent · medians · host)은 라벨 없이 오늘 그대로."""
    section = linux_section(render(empty_linux_doc(lanes=1), tz=UTC))
    assert section == [
        "queue — empty (pool linux)",
        "recent — no completed jobs yet",
        "medians: gate:full 6m 40s (n=7) · deploy-dev 10m 00s (n=3)",
        "host — no sample yet",
    ], section


def test_remote_pool_with_jobs_keeps_the_m5b1_header():
    """잡이 있으면 M5b-1 의 `queue — N (pool linux)` 그대로 — 여기서 바뀌는 건 빈 큐 줄뿐."""
    d = local_doc()
    d["pools"].append(linux_pool(d["pools"][0], lanes=1, running=True))
    assert linux_header(render(d, tz=UTC)) == "queue — 3 (pool linux)"


# ── 기본 풀: 오늘 그대로 ─────────────────────────────────────────────────────


def test_default_pool_empty_lines_are_byte_identical_to_today():
    empty = empty_default(local_doc())
    assert queue_heads(render(empty, tz=UTC)) == [DEFAULT_EMPTY]
    assert queue_heads(render(empty_default(local_doc(paused=PAUSED)), tz=UTC)) == [DEFAULT_PAUSED]
    assert queue_heads(render(local_down(empty_default(local_doc())), tz=UTC)) == [DEFAULT_PAUSED]
    bad = local_doc()
    bad["pools"][0].update(queue=None, queue_error="database locked")
    assert queue_heads(render(bad, tz=UTC)) == ["queue — unavailable: database locked"]


def test_default_pool_section_is_unchanged_next_to_an_empty_remote_pool():
    for make in (lambda: local_doc(), lambda: empty_default(local_doc())):
        one = render(make(), tz=UTC).splitlines()
        one_section = one[next(i for i, ln in enumerate(one) if ln.startswith("queue — ")) :]
        two_doc = make()
        two_doc["pools"].append(linux_pool(doc()["pools"][0], lanes=1, running=False))
        two_doc["pools"][1]["queue"] = []
        assert split_sections(render(two_doc, tz=UTC))[0] == one_section


def test_one_pool_golden_is_unchanged():
    assert render(one_pool_doc(), tz=UTC) == GOLDEN
    assert render(local_doc(), tz=UTC) == GOLDEN


# ── 머리줄: 원격 워커 필 접기 ────────────────────────────────────────────────


def test_seven_idle_remote_workers_fold_after_the_first_five():
    out = render(with_workers(local_doc(), *idle_workers(7)), tz=UTC)
    head = head_line(out)
    assert head == (
        "━━━ rcm · server · 00:52 local · worker busy #412"
        " · build-02/1 idle · build-03/1 idle · build-04/1 idle · build-05/1 idle"
        " · build-06/1 idle · +2 workers"
    ), head
    assert "build-07" not in head and "build-08" not in head
    assert body(out) == body(GOLDEN)  # 머리줄 말고는 아무것도 안 바뀐다


def test_five_remote_workers_are_not_folded():
    head = head_line(render(with_workers(local_doc(), *idle_workers(5)), tz=UTC))
    for i in range(2, 7):
        assert f"build-{i:02d}/1 idle" in head, head
    assert "workers" not in head and "+" not in head, head


def test_six_remote_workers_fold_exactly_one():
    head = head_line(render(with_workers(local_doc(), *idle_workers(6)), tz=UTC))
    assert head.endswith("build-06/1 idle · +1 workers"), head
    assert "build-07" not in head


def test_busy_pills_inside_the_first_five_keep_their_job_number():
    entries = [remote("build-02", 1, "busy", 511), *idle_workers(6, first=3)]
    head = head_line(render(with_workers(local_doc(), *entries), tz=UTC))
    assert "worker busy #412 · build-02/1 busy #511 · build-03/1 idle" in head, head
    assert head.endswith("build-06/1 idle · +2 workers"), head


def test_down_remote_worker_is_never_folded():
    """7번째가 down 이면 그 필은 보이고, 대신 idle 하나(build-07)가 접힌다 — `+1 workers`."""
    entries = [*idle_workers(6), remote("build-08", 1, "down")]
    head = head_line(render(with_workers(local_doc(), *entries), tz=UTC))
    assert "build-08/1 down" in head, head
    assert "build-07" not in head, head
    assert "+1 workers" in head, head
    for i in range(2, 7):
        assert f"build-{i:02d}/1 idle" in head, head
    assert head.count("build-0") == 6, head  # 5 idle + 1 down — 접힌 것은 문구 하나뿐
    assert "DOWN" not in head, head  # 원격 down 은 `DOWN: lane` 이 아니다(M5b-2)


def test_down_pills_are_all_shown_even_when_several_are_past_the_fifth():
    entries = [*idle_workers(5), remote("build-07", 1, "down"), remote("build-08", 1, "down")]
    head = head_line(render(with_workers(local_doc(), *entries), tz=UTC))
    assert "build-07/1 down" in head and "build-08/1 down" in head, head
    assert "workers" not in head, head  # 접힌 워커가 없으면 꼬리도 없다


def test_folded_tail_comes_before_paused_cache_and_pool_totals():
    d = local_doc(snapshot_cache={"blobs": 12, "bytes": 48_213_344}, paused=PAUSED)
    d["pools"].append(linux_pool(d["pools"][0], lanes=1, running=False))
    d["pools"][1]["queue"] = []
    head = head_line(render(with_workers(d, *idle_workers(7)), tz=UTC))
    tail = pills(head)
    assert "+2 workers" in tail, head
    i = tail.index("+2 workers")
    assert tail[i + 1] == "PAUSED by macmini-admin", tail
    assert tail[i + 2].startswith("cache 12 blobs"), tail
    # `cache 12 blobs · 48 MB` 는 ` · ` 로 나누면 두 칸이다 — pools 는 그 다음 칸
    assert tail[i + 4] == "pools 2", tail
