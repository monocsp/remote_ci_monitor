"""터미널 렌더(M5b-2) — `rcm top` 머리줄의 원격 워커 필. `server.workers[]` 항목에 `worker`
(null|이름) · `display_name`(로컬 null · 원격 `"<이름>/<레인>"`) 이 붙는다(명세
docs/m5b2-workplan.md §4 · §6 「상태 JSON」; `server.lanes` 는 로컬 레인 수 그대로 · 로컬 레인이
먼저, 원격은 워커 이름순).

잠그는 모양(구현 전 — test-first):
- 로컬 레인은 오늘과 같다: 레인 하나면 `worker busy #412`, 여럿이면 `lanes 1/2 busy` — **로컬
  레인만** 센다(`server.lanes` 가 로컬 수라 원격 busy 를 섞으면 `2/2` 같은 거짓말이 된다). `worker`
  키가 아예 없는 옛 항목도 로컬이다. 로컬 down 은 오늘처럼 `DOWN: lane N`.
- 원격 워커는 항목마다 필 하나: `<display_name> <state>[ #job_id]` — `build-02/1 busy #511` ·
  `build-02/1 idle` · `build-02/1 down`. 로컬 요약 뒤에 ` · ` 로 잇고 배열 순서를 지킨다. 원격
  down 은 필에만 보이고 `DOWN: lane …` 에는 안 들어간다(그 숫자는 로컬 레인 번호다).
- 머리줄 순서: 로컬 요약(+ `DOWN: lane`) → 원격 필 → `PAUSED by` → `cache` → 풀 집계.
- 풀 헤더는 M5b-1 그대로: lanes 0 → `(pool linux · no workers)`, lanes ≥ 1 → `(pool linux)` —
  레인 수 형태(`· 2 lanes`)는 만들지 않는다.
- 풀 하나 + 로컬 항목에 `worker: null` · `display_name: null` 만 더한 문서는 GOLDEN 과 바이트
  단위로 같다.

순수 함수만 — test_render_m5.doc() 의 스키마 v1 문서에 항목을 얹는다.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from remote_ci_monitor.core.render_text import render
from test_render_m5 import doc, head_of
from test_render_m5b import GOLDEN, two_pool_doc

SINCE = "2026-09-04T00:40:00Z"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def local_doc(**server_extra: Any) -> dict[str, Any]:
    """doc() 의 로컬 워커 항목에 M5b-2 키(`worker: None` · `display_name: None`)를 더한 문서."""
    d = doc(**server_extra)
    for w in d["server"]["workers"]:
        w.update(worker=None, display_name=None)
    return d


def local_entry(lane: int, state: str, job_id: int | None = None) -> dict[str, Any]:
    return {
        "lane": lane,
        "state": state,
        "job_id": job_id,
        "error": None,
        "since": SINCE,
        "worker": None,
        "display_name": None,
    }


def remote(name: str, lane: int, state: str, job_id: int | None = None) -> dict[str, Any]:
    """원격 워커 항목 — `worker` 는 이름, `display_name` 은 `<이름>/<레인>`, `lane` 은 int."""
    return {
        "lane": lane,
        "state": state,
        "job_id": job_id,
        "error": None,
        "since": SINCE,
        "worker": name,
        "display_name": f"{name}/{lane}",
    }


def with_workers(d: dict[str, Any], *entries: dict[str, Any]) -> dict[str, Any]:
    d["server"]["workers"].extend(entries)
    return d


def head_line(out: str) -> str:
    line = out.splitlines()[0]
    assert line.startswith("━━━ rcm ·"), line
    return line


def body(out: str) -> str:
    """머리줄을 뺀 나머지(queue 절부터)."""
    return out.split("\n", 1)[1]


# ── 풀 하나 · 로컬만: 오늘과 같다 ────────────────────────────────────────────


def test_one_pool_golden_is_unchanged_with_null_worker_keys_on_local_entries():
    assert render(local_doc(), tz=UTC) == GOLDEN


def test_local_entries_without_the_new_keys_still_render_as_today():
    """옛 서버(M5b-1)의 항목엔 `worker` 키가 없다 — 없으면 로컬이다."""
    assert render(doc(), tz=UTC) == GOLDEN


# ── 원격 워커 필 ─────────────────────────────────────────────────────────────


def test_remote_busy_worker_gets_its_own_pill_after_the_local_one():
    out = render(with_workers(local_doc(), remote("build-02", 1, "busy", 511)), tz=UTC)
    head = head_line(out)
    assert "worker busy #412 · build-02/1 busy #511" in head, head
    assert body(out) == body(GOLDEN)  # 머리줄 말고는 아무것도 안 바뀐다


def test_remote_idle_worker_pill_has_no_job_number():
    out = render(with_workers(local_doc(), remote("build-02", 1, "idle")), tz=UTC)
    head = head_line(out)
    assert "worker busy #412 · build-02/1 idle" in head, head
    assert "build-02/1 idle #" not in head and "None" not in head, head


def test_remote_down_worker_pill_says_down_and_is_not_a_local_DOWN_lane():
    out = render(with_workers(local_doc(), remote("build-02", 1, "down")), tz=UTC)
    head = head_line(out)
    assert "worker busy #412 · build-02/1 down" in head, head
    assert "DOWN" not in head, head  # `DOWN: lane 1` 은 로컬 레인 1 얘기다 — 원격엔 안 쓴다


def test_local_down_keeps_todays_DOWN_lane_suffix_before_the_remote_pills():
    d = local_doc()
    d["server"]["workers"][0].update(state="down", job_id=None, error="ENOSPC")
    out = render(with_workers(d, remote("build-02", 1, "idle")), tz=UTC)
    head = head_line(out)
    assert "worker down · DOWN: lane 1 · build-02/1 idle" in head, head


def test_remote_pills_keep_the_array_order():
    entries = [
        remote("build-02", 1, "busy", 511),
        remote("build-02", 2, "idle"),
        remote("build-03", 1, "down"),
    ]
    out = render(with_workers(local_doc(), *entries), tz=UTC)
    head = head_line(out)
    assert "build-02/1 busy #511 · build-02/2 idle · build-03/1 down" in head, head
    assert head.count("build-0") == 3, head  # 필은 항목당 하나


def test_local_lane_summary_counts_local_lanes_only():
    """로컬 2레인(1 busy) + 원격 busy → `lanes 1/2 busy` — 원격 busy 를 로컬 레인에 섞지 않는다."""
    d = local_doc()
    d["server"]["lanes"] = 2
    with_workers(d, local_entry(2, "idle"), remote("build-02", 1, "busy", 511))
    head = head_line(render(d, tz=UTC))
    assert "lanes 1/2 busy · build-02/1 busy #511" in head, head
    assert "2/2" not in head and "3/2" not in head, head


def test_remote_pill_is_used_even_when_the_local_lane_count_is_one():
    """레인 하나 판정은 `server.lanes`(로컬)로 — 원격이 붙어도 로컬 필은 `worker busy #412`."""
    d = with_workers(local_doc(), remote("build-02", 1, "idle"), remote("build-03", 1, "idle"))
    head = head_line(render(d, tz=UTC))
    assert head.startswith("━━━ rcm · server · 00:52 local · worker busy #412 · build-02/1 idle"), (
        head
    )
    assert "lanes " not in head, head  # `lanes 1/3 busy` 처럼 원격을 로컬 레인으로 세면 안 된다


def test_remote_pills_come_before_paused_and_cache():
    d = local_doc(snapshot_cache={"blobs": 12, "bytes": 48_213_344})
    d["server"]["paused"] = {"by": "macmini-admin", "at": "2026-09-04T00:50:00Z"}
    head = head_line(render(with_workers(d, remote("build-02", 1, "idle")), tz=UTC))
    assert (
        "worker busy #412 · build-02/1 idle · PAUSED by macmini-admin · cache 12 blobs" in head
    ), head


def test_remote_workers_do_not_change_the_default_pool_section():
    """원격 워커가 있어도 큐·recent·medians·host 절은 오늘 그대로(잡 행은 풀별 queue 가 준다)."""
    d = with_workers(local_doc(), remote("build-02", 1, "busy", 511), remote("build-03", 1, "down"))
    assert body(render(d, tz=UTC)) == body(GOLDEN)


# ── 풀 헤더: 레인 수는 안 쓴다 ───────────────────────────────────────────────


def test_pool_header_with_two_lanes_is_still_just_pool_name():
    out = render(two_pool_doc(lanes=2, running=True), tz=UTC)
    header = next(ln for ln in out.splitlines() if "(pool linux" in ln)
    assert header == "queue — 3 (pool linux)", header
    assert "lanes" not in header and "no workers" not in out, out


def test_pool_header_no_workers_is_unchanged_with_remote_workers_elsewhere():
    d = two_pool_doc(lanes=0)
    for w in d["server"]["workers"]:
        w.update(worker=None, display_name=None)
    with_workers(d, remote("build-02", 1, "down"))  # linux 풀의 워커가 죽어 lanes 0
    out = render(d, tz=UTC)
    assert "queue — 2 (pool linux · no workers)" in out, out
    assert "build-02/1 down" in head_of(out), head_of(out)
