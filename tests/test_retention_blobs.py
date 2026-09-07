"""blob 보존 정리 순수 규칙(M5a-2) — `snapshot_cache_days` 경계(`>=`) · 활성 참조는 절대 안 지움 ·
`snapshot_cache_max_bytes` 초과분은 오래된 순 · 섞였을 때 · 출력 정렬 · 빈 입력.

I/O 가 없다. 파일·행 삭제와 활성 잡 manifest 참조 수집은 `janitor.py`(B) 의 몫 — 여기서는
참조 집합을 받는다. `test_retention.py` 의 `due_for_purge` 와 같은 결(같은 객체 반환 · `now` 기준 ·
경계 `>=`)을 유지한다.
"""

from __future__ import annotations

import hashlib
import string
from collections.abc import Iterable
from datetime import datetime, timedelta

from jobfactory import NOW
from remote_ci_monitor.core.retention import BlobInfo, blobs_to_purge

DAY = timedelta(days=1)
BIG = 10**12


def h(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def blob(name: str, size: int = 100, *, age: timedelta) -> BlobInfo:
    """`age` 만큼 전에 마지막으로 쓰인 blob."""
    return BlobInfo(sha256=h(name), size=size, last_used_at=NOW - age)


def purge(
    blobs: Iterable[BlobInfo],
    referenced: Iterable[str] = (),
    *,
    days: int = 30,
    max_bytes: int = BIG,
    now: datetime = NOW,
) -> list[BlobInfo]:
    return blobs_to_purge(blobs, set(referenced), now, days=days, max_bytes=max_bytes)


def names(got: list[BlobInfo]) -> list[str]:
    table = {h(n): n for n in string.ascii_lowercase}
    return [table[b.sha256] for b in got]


# ── 모양 ─────────────────────────────────────────────────────────────────────


def test_blobinfo_field_order_is_sha256_size_last_used_at() -> None:
    assert BlobInfo(h("a"), 1, NOW) == BlobInfo(sha256=h("a"), size=1, last_used_at=NOW)


def test_empty_input_gives_empty_list() -> None:
    assert purge([]) == []
    assert purge([], max_bytes=0) == []


def test_accepts_any_iterable_and_returns_the_same_objects() -> None:
    old = blob("a", age=40 * DAY)
    gen = (b for b in [old, blob("b", age=1 * DAY)])
    got = purge(gen)
    assert len(got) == 1 and got[0] is old


# ── days 경계 ────────────────────────────────────────────────────────────────


def test_blob_unused_for_exactly_days_is_purged() -> None:
    assert names(purge([blob("a", age=30 * DAY)])) == ["a"]


def test_blob_one_second_younger_than_days_is_kept() -> None:
    assert purge([blob("a", age=30 * DAY - timedelta(seconds=1))]) == []


def test_days_zero_purges_every_unreferenced_blob() -> None:
    blobs = [blob("a", age=timedelta(0)), blob("b", age=1 * DAY)]
    assert names(purge(blobs, days=0)) == ["b", "a"]


def test_now_is_the_reference_not_wall_clock() -> None:
    b = blob("a", age=30 * DAY)
    assert names(purge([b], now=NOW + DAY)) == ["a"]
    assert purge([b], now=NOW - timedelta(seconds=1)) == []


def test_age_rule_does_not_depend_on_max_bytes() -> None:
    # 합계가 상한 아래여도 오래 안 쓰인 blob 은 지운다
    blobs = [blob("a", 10, age=31 * DAY), blob("b", 10, age=1 * DAY)]
    assert names(purge(blobs, max_bytes=BIG)) == ["a"]


# ── 참조된 blob 은 절대 대상이 아니다 ────────────────────────────────────────


def test_referenced_blob_is_never_purged_even_if_ancient() -> None:
    blobs = [blob("a", age=400 * DAY), blob("b", age=400 * DAY)]
    assert names(purge(blobs, referenced=[h("a")])) == ["b"]
    assert purge(blobs, referenced=[h("a"), h("b")]) == []


def test_referenced_blob_is_never_purged_under_byte_pressure() -> None:
    blobs = [blob("a", 300, age=5 * DAY), blob("b", 100, age=1 * DAY)]
    # 참조된 a 만으로 상한을 넘지만 a 는 못 지운다 — b 만 지우고 멈춘다(못 내려가도 오류 아님)
    assert names(purge(blobs, referenced=[h("a")], max_bytes=250)) == ["b"]


def test_referenced_bytes_still_count_toward_the_total() -> None:
    blobs = [blob("r", 200, age=1 * DAY), blob("x", 100, age=3 * DAY), blob("y", 100, age=2 * DAY)]
    # 합계 400 > 350 → 참조 안 된 것 중 가장 오래된 x 하나면 300 ≤ 350
    assert names(purge(blobs, referenced=[h("r")], max_bytes=350)) == ["x"]


# ── max_bytes: 오래된 순 ─────────────────────────────────────────────────────


def test_over_max_bytes_purges_oldest_first_until_under() -> None:
    blobs = [blob("c", 100, age=1 * DAY), blob("a", 100, age=3 * DAY), blob("b", 100, age=2 * DAY)]
    assert purge(blobs, max_bytes=300) == []  # 정확히 상한 = 안 넘었다
    assert names(purge(blobs, max_bytes=299)) == ["a"]
    assert names(purge(blobs, max_bytes=150)) == ["a", "b"]
    assert names(purge(blobs, max_bytes=0)) == ["a", "b", "c"]


def test_a_big_old_blob_is_enough_to_get_under() -> None:
    blobs = [blob("a", 500, age=3 * DAY), blob("b", 10, age=2 * DAY), blob("c", 10, age=1 * DAY)]
    assert names(purge(blobs, max_bytes=100)) == ["a"]


def test_purges_only_as_many_as_needed_not_all_old_ones() -> None:
    blobs = [blob("a", 100, age=9 * DAY), blob("b", 100, age=8 * DAY), blob("c", 100, age=7 * DAY)]
    assert names(purge(blobs, max_bytes=200)) == ["a"]


# ── 섞였을 때 · 정렬 ─────────────────────────────────────────────────────────


def test_age_purge_happens_first_then_byte_pressure_on_the_rest() -> None:
    blobs = [
        blob("d", 100, age=1 * DAY),
        blob("a", 100, age=40 * DAY),  # days 초과
        blob("c", 100, age=5 * DAY),
        blob("b", 100, age=10 * DAY),
    ]
    # a 를 지운 뒤 300 > 250 → 남은 것 중 가장 오래된 b 하나 더
    assert names(purge(blobs, max_bytes=250)) == ["a", "b"]


def test_a_blob_appears_at_most_once() -> None:
    blobs = [blob("a", 100, age=40 * DAY), blob("b", 100, age=1 * DAY)]
    got = purge(blobs, max_bytes=0)
    assert names(got) == ["a", "b"] and len({b.sha256 for b in got}) == len(got)


def test_result_is_ordered_by_last_used_at_ascending() -> None:
    blobs = [
        blob("c", age=35 * DAY),
        blob("a", age=90 * DAY),
        blob("e", age=1 * DAY),  # 아직
        blob("b", age=60 * DAY),
        blob("d", age=31 * DAY),
    ]
    assert names(purge(blobs)) == ["a", "b", "c", "d"]


def test_days_and_bytes_output_is_one_ascending_list() -> None:
    blobs = [
        blob("b", 100, age=3 * DAY),
        blob("a", 100, age=50 * DAY),
        blob("c", 100, age=2 * DAY),
        blob("d", 100, age=1 * DAY),
    ]
    got = purge(blobs, max_bytes=100)
    assert names(got) == ["a", "b", "c"]
    assert [b.last_used_at for b in got] == sorted(b.last_used_at for b in got)
