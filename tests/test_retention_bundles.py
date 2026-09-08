"""M5e 번들 TTL — `core/retention.py::bundles_to_expire`.
명세는 docs/m5e-workplan.md §8(보존 · 청소 · M3 와의 경계) · §18.

`BundleInfo`·`bundles_to_expire` 는 아직 없다 — **구현 전이라 빨간 것이 정상이다.** 모듈 자체는
있으므로 파일 맨 위가 아니라 **시험 안에서 늦게** 불러 이름이 붙었는지 본다(`retention()`).
위에서 import 하면 파일 하나가 통째로 수집 오류가 되어 나머지 스위트까지 안 돈다.

번들은 **자기 시계로만** 지운다. `retention_days_success` 는 0 이 될 수 있고 0 은 「다음 sweep 에
바로」다(`core/retention.py::due_for_purge`) — M3 청소에 번들을 얹으면 합류된 잡이 몇 분 만에
산출물을 잃는다. 그래서 **만료 시각이 없는 행은 절대 대상이 아니다.**
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

import pytest

from jobfactory import NOW

SECOND = timedelta(seconds=1)
HOUR = timedelta(hours=1)

#: 상태 문자열은 `tests/test_artifacts_rules.py` 가 상수와 같은지 따로 잠근다.
GONE = ["purged", "expired"]


# ── 아직 없는 이름 (§18) ─────────────────────────────────────────────────────


def artifacts_core() -> Any:
    """`remote_ci_monitor.core.artifacts` — 상태 상수(§18). 구현 전에는 여기서 빨개진다."""
    try:
        import remote_ci_monitor.core.artifacts as mod
    except ImportError as e:
        pytest.fail(
            f"remote_ci_monitor.core.artifacts is not implemented yet "
            f"(docs/m5e-workplan.md §18): {e}",
            pytrace=False,
        )
    return mod


def retention() -> Any:
    """`core/retention.py` 의 M5e 추가분. 모듈은 있고 이름이 아직 없다."""
    import remote_ci_monitor.core.retention as mod

    missing = [n for n in ("BundleInfo", "bundles_to_expire") if not hasattr(mod, n)]
    if missing:
        pytest.fail(
            f"remote_ci_monitor.core.retention has no {' · '.join(missing)} yet "
            f"(docs/m5e-workplan.md §18)",
            pytrace=False,
        )
    return mod


def bundle(
    ret: Any,
    job_id: int,
    *,
    state: str = "ready",
    due: timedelta | None = HOUR,
    size: int = 1024,
) -> Any:
    """`due` 만큼 **전에** 만료된 번들. 음수면 아직 남았다는 뜻이고, `None` 이면 만료가 없다."""
    return ret.BundleInfo(job_id, state, None if due is None else NOW - due, size)


def ids(rows: list[Any]) -> list[int]:
    return [b.job_id for b in rows]


# ── 만료 시각이 없는 행 ─────────────────────────────────────────────────────


def test_rows_without_an_expiry_are_never_returned() -> None:
    ret, core = retention(), artifacts_core()
    rows = [
        bundle(ret, 1, due=None),
        bundle(ret, 2, state=core.UPLOADING, due=None),
        bundle(ret, 3, state=core.READY, due=None, size=0),
    ]
    assert ret.bundles_to_expire(rows, NOW) == []


def test_rows_without_an_expiry_stay_out_even_a_decade_later() -> None:
    """만료 없는 행은 시각을 아무리 밀어도 대상이 아니다 — 24시간 약속의 유일한 보루다."""
    ret, core = retention(), artifacts_core()
    rows = [bundle(ret, 1, due=None), bundle(ret, 2, due=None, state=core.UPLOADING)]
    assert ret.bundles_to_expire(rows, NOW + timedelta(days=3650)) == []


def test_an_expiring_row_next_to_a_row_without_one() -> None:
    ret = retention()
    rows = [bundle(ret, 1, due=None), bundle(ret, 2, due=HOUR)]
    assert ids(ret.bundles_to_expire(rows, NOW)) == [2]


# ── 경계는 `<=` ─────────────────────────────────────────────────────────────


def test_a_ready_row_past_its_expiry_is_returned() -> None:
    ret, core = retention(), artifacts_core()
    row = bundle(ret, 1, state=core.READY, due=HOUR)
    got = ret.bundles_to_expire([row], NOW)
    assert got == [row]
    assert got[0] is row  # 같은 객체를 돌려준다(복사·재생성 없음)


def test_exactly_at_the_expiry_is_due() -> None:
    ret = retention()
    assert ids(ret.bundles_to_expire([bundle(ret, 1, due=timedelta(0))], NOW)) == [1]


def test_one_second_past_the_expiry_is_due() -> None:
    ret = retention()
    assert ids(ret.bundles_to_expire([bundle(ret, 1, due=SECOND)], NOW)) == [1]


def test_one_second_before_the_expiry_is_not_due() -> None:
    ret = retention()
    assert ret.bundles_to_expire([bundle(ret, 1, due=-SECOND)], NOW) == []


def test_a_bundle_inside_its_ttl_is_not_due() -> None:
    ret = retention()
    assert ret.bundles_to_expire([bundle(ret, 1, due=-timedelta(hours=23))], NOW) == []


# ── 이미 사라진 행은 다시 가져가지 않는다 ───────────────────────────────────


@pytest.mark.parametrize("state", GONE)
def test_already_gone_rows_are_not_returned_again(state: str) -> None:
    ret = retention()
    assert ret.bundles_to_expire([bundle(ret, 1, state=state, due=timedelta(days=9))], NOW) == []


def test_gone_rows_are_skipped_while_their_neighbours_are_taken() -> None:
    ret, core = retention(), artifacts_core()
    rows = [
        bundle(ret, 1, state=core.PURGED),
        bundle(ret, 2, state=core.READY),
        bundle(ret, 3, state=core.EXPIRED),
        bundle(ret, 4, state=core.UPLOADING),
    ]
    assert ids(ret.bundles_to_expire(rows, NOW)) == [2, 4]


def test_an_upload_receipt_without_a_finish_is_swept() -> None:
    """§9 ⑤ — 영수증 시각 + TTL 이 이미 박혀 있어 sweep 이 가져간다(만료 없는 행을 안 만든다)."""
    ret, core = retention(), artifacts_core()
    assert ids(ret.bundles_to_expire([bundle(ret, 9, state=core.UPLOADING)], NOW)) == [9]


# ── 모양 · 정렬 ─────────────────────────────────────────────────────────────


def test_output_is_sorted_by_job_id() -> None:
    ret = retention()
    rows = [bundle(ret, 7), bundle(ret, 2), bundle(ret, 30), bundle(ret, 1)]
    assert ids(ret.bundles_to_expire(rows, NOW)) == [1, 2, 7, 30]


def test_output_is_sorted_by_job_id_not_by_expiry() -> None:
    ret = retention()
    rows = [bundle(ret, 5, due=timedelta(days=9)), bundle(ret, 2, due=SECOND)]
    assert ids(ret.bundles_to_expire(rows, NOW)) == [2, 5]


def test_now_is_the_reference_not_the_wall_clock() -> None:
    ret = retention()
    row = bundle(ret, 1, due=-timedelta(hours=2))
    assert ret.bundles_to_expire([row], NOW) == []
    assert ids(ret.bundles_to_expire([row], NOW + timedelta(hours=2))) == [1]
    assert ids(ret.bundles_to_expire([row], NOW + timedelta(days=1))) == [1]


def test_empty_input_gives_an_empty_list() -> None:
    assert retention().bundles_to_expire([], NOW) == []


def test_any_iterable_is_accepted() -> None:
    ret = retention()
    rows = (bundle(ret, i) for i in (3, 1, 2))
    assert ids(ret.bundles_to_expire(rows, NOW)) == [1, 2, 3]


# ── BundleInfo ──────────────────────────────────────────────────────────────


def test_bundle_info_fields_order_and_frozen() -> None:
    ret, core = retention(), artifacts_core()
    b = ret.BundleInfo(4, core.READY, NOW, 99)
    assert (b.job_id, b.state, b.expires_at, b.bytes) == (4, core.READY, NOW, 99)
    assert b == ret.BundleInfo(job_id=4, state=core.READY, expires_at=NOW, bytes=99)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.bytes = 0


def test_bundle_info_expiry_may_be_none() -> None:
    ret, core = retention(), artifacts_core()
    assert ret.BundleInfo(1, core.READY, None, 0).expires_at is None
