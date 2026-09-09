"""M5h §2.2 — `core/failures.py` 의 판정 4종과 `GET /jobs/{id}` 의 `failures[]` (test-first).

시나리오 표는 `docs/m5h-test-scenarios-a.md`. 판정은 **서버가 코드로** 내려보내고 문장은
보는 쪽이 만든다(결정 37) — 그래서 여기서 잠그는 것은 문구가 아니라 코드와 JSON 키다.
창을 세는 일(질의)은 `store.failure_stats` 의 몫이라 이 모듈은 시계도 DB 도 안 본다.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any

import pytest

# test-first — `core/failures.py` 는 아직 없다. 이 import 가 빨간 것이 이 파일의 정상 상태다.
from remote_ci_monitor.core.failures import (
    VERDICT_FIRST_SEEN,
    VERDICT_INTERMITTENT,
    VERDICT_PERSISTENT,
    VERDICT_UNKNOWN,
    FailureRow,
    failures_json,
    verdict,
)

WINDOW = 8  # 완료 기준 2 의 「같은 key 8회」
MIN_JOBS = 3
TEST_DART = "just_audio_screen_music_port_test.dart"


def row(name: str, seen: int, first: int | None = 141, last: int | None = 162) -> FailureRow:
    return FailureRow(name=name, seen=seen, first_seen_job_id=first, last_seen_job_id=last)


def js(rows: list[FailureRow], **kw: Any) -> list[dict[str, Any]]:
    return failures_json(
        rows,
        steps=kw.pop("steps", ()),
        window=kw.pop("window", WINDOW),
        window_unnamed=kw.pop("unnamed", 0),
        min_jobs=kw.pop("min_jobs", MIN_JOBS),
    )


# ── A. `verdict` 의 경계 (§2.2 판정 표 · 결정 66) ────────────────────────────


def test_a_window_shallower_than_min_jobs_is_unknown():
    """표본이 없으면 아무 말도 안 한다 — 이 규칙이 다른 셋보다 **먼저**다."""
    assert verdict(1, 2, min_jobs=MIN_JOBS) == VERDICT_UNKNOWN
    assert verdict(2, 2, min_jobs=MIN_JOBS) == VERDICT_UNKNOWN  # seen == window 인데도
    assert verdict(5, 2, min_jobs=MIN_JOBS) == VERDICT_UNKNOWN
    assert verdict(0, 0, min_jobs=1) == VERDICT_UNKNOWN  # 창이 비었다


def test_a_window_exactly_at_min_jobs_is_judged():
    """`<` 를 `<=` 로 쓰면 창이 딱 찬 순간이 영영 `unknown` 이다."""
    assert verdict(1, 3, min_jobs=MIN_JOBS) == VERDICT_FIRST_SEEN
    assert verdict(2, 3, min_jobs=MIN_JOBS) == VERDICT_INTERMITTENT
    assert verdict(3, 3, min_jobs=MIN_JOBS) == VERDICT_PERSISTENT


def test_seen_equal_to_the_window_is_persistent():
    """창의 모든 잡에서 봤다 = 계속 빨갛다. 「간헐?」이라고 물으면 안 된다."""
    assert verdict(8, 8, min_jobs=MIN_JOBS) == VERDICT_PERSISTENT
    assert verdict(20, 20, min_jobs=MIN_JOBS) == VERDICT_PERSISTENT


def test_seen_once_is_first_seen():
    assert verdict(1, 8, min_jobs=MIN_JOBS) == VERDICT_FIRST_SEEN
    assert verdict(1, 20, min_jobs=MIN_JOBS) == VERDICT_FIRST_SEEN


def test_between_one_and_the_window_is_intermittent():
    assert verdict(2, 8, min_jobs=MIN_JOBS) == VERDICT_INTERMITTENT
    assert verdict(7, 8, min_jobs=MIN_JOBS) == VERDICT_INTERMITTENT
    assert verdict(3, 20, min_jobs=MIN_JOBS) == VERDICT_INTERMITTENT


def test_a_single_run_window_is_persistent_not_first_seen():
    """`seen >= window` 가 `seen == 1` 보다 앞이라는 순서 그 자체(창 1 · 1회 실패)."""
    assert verdict(1, 1, min_jobs=1) == VERDICT_PERSISTENT


def test_more_seen_than_the_window_is_still_persistent():
    """방어적 경계 — 창 밖의 잡이 섞여 분자가 커져도 `>=` 라 판정은 흔들리지 않는다."""
    assert verdict(9, 8, min_jobs=MIN_JOBS) == VERDICT_PERSISTENT
    assert verdict(2, 1, min_jobs=1) == VERDICT_PERSISTENT


def test_the_verdict_codes_are_locked():
    codes = (VERDICT_UNKNOWN, VERDICT_FIRST_SEEN, VERDICT_INTERMITTENT, VERDICT_PERSISTENT)
    assert codes == ("unknown", "first_seen", "intermittent", "persistent")
    assert len(set(codes)) == 4
    for seen in range(0, 11):
        for window in range(0, 11):
            assert verdict(seen, window, min_jobs=MIN_JOBS) in codes, (seen, window)


def test_min_jobs_is_keyword_only():
    """`seen` · `window` · `min_jobs` 셋 다 int 다 — 자리를 바꿔 넣어도 조용히 돌면 안 된다."""
    kinds = [p.kind for p in inspect.signature(verdict).parameters.values()]
    assert kinds == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ]
    with pytest.raises(TypeError):
        verdict(1, 8, 3)  # type: ignore[misc]


# ── B. `failures_json` — 모양 · 순서 · 분모 (§2.2 · §2.3) ────────────────────


def test_a_row_becomes_exactly_the_documented_object():
    """명세 §2.2 의 예시 그대로. 키가 더 붙어도 빠져도 빨개진다(웹·CLI 가 이 키를 읽는다)."""
    got = js([row("test", 8)], steps={"test"})
    assert got == [
        {
            "name": "test",
            "step": True,
            "seen": 8,
            "window": 8,
            "window_unnamed": 0,
            "first_seen_job_id": 141,
            "last_seen_job_id": 162,
            "verdict": "persistent",
        }
    ]


def test_a_name_that_is_not_a_step_is_a_unit():
    """workplan §4.3 의 두 번째 항목 — 테스트 파일 이름은 스텝이 아니라 **단위**다."""
    got = js([row(TEST_DART, 1, first=162)], steps={"test"})
    assert got == [
        {
            "name": TEST_DART,
            "step": False,
            "seen": 1,
            "window": 8,
            "window_unnamed": 0,
            "first_seen_job_id": 162,
            "last_seen_job_id": 162,
            "verdict": "first_seen",
        }
    ]


def test_the_rows_keep_the_jobs_own_order():
    """이름 순으로 정렬하면 잡이 찍은 순서(`seq`)가 사라진다 — 첫 줄이 대개 진짜 원인이다."""
    rows = [row("zulu", 2), row("alpha", 1), row("mike", 8)]
    assert [f["name"] for f in js(rows)] == ["zulu", "alpha", "mike"]


def test_step_is_true_only_for_names_that_are_steps():
    rows = [row("test", 8), row(TEST_DART, 1), row("build web", 3)]
    got = js(rows, steps={"test", "build web"})
    assert [f["step"] for f in got] == [True, False, True]


def test_the_steps_container_may_be_any_container():
    """서버는 종료 잡의 `failed_step` · `last_step` **두 칸**만 넘긴다(§2.3) — None 이 섞인다."""
    rows = [row("test", 1)]
    for steps in (("test",), ["test"], frozenset({"test"}), {None, "test"}):
        assert js(rows, steps=steps)[0]["step"] is True, steps
    for steps in ((), [], frozenset(), {None}):
        assert js(rows, steps=steps)[0]["step"] is False, steps


def test_the_window_and_its_quality_ride_on_every_row():
    """분모의 품질을 숨기지 않는다(결정 68) — 줄마다 같은 창과 같은 `window_unnamed`."""
    got = js([row("a", 1), row("b", 2), row("c", 3)], window=8, unnamed=2)
    assert [f["window"] for f in got] == [8, 8, 8]
    assert [f["window_unnamed"] for f in got] == [2, 2, 2]


def test_each_row_gets_its_own_verdict():
    got = js([row("a", 8), row("b", 1), row("c", 4)], window=8)
    assert [f["verdict"] for f in got] == ["persistent", "first_seen", "intermittent"]


def test_a_shallow_window_makes_every_verdict_unknown():
    got = js([row("a", 2), row("b", 1)], window=2, min_jobs=3)
    assert [f["verdict"] for f in got] == ["unknown", "unknown"]
    assert [f["window"] for f in got] == [2, 2]


def test_no_rows_is_an_empty_list():
    """빈 배열은 「이름을 하나도 안 남겼다」이고, 키가 없는 것은 「못 읽었다」이다(§2.3)."""
    assert js([]) == []


def test_unknown_job_ids_stay_null():
    """모르는 값은 `null` 이지 0 이 아니다(집안 규칙)."""
    got = js([row("a", 1, first=None, last=None)])
    assert got[0]["first_seen_job_id"] is None and got[0]["last_seen_job_id"] is None


def test_the_result_is_plain_json():
    """서버가 그대로 직렬화한다 — dataclass 나 set 이 새어 나오면 안 된다."""
    got = js([row("test", 8), row(TEST_DART, 1)], steps={"test"})
    assert json.loads(json.dumps(got)) == got
    for item in got:
        assert isinstance(item, dict)
        assert all(isinstance(k, str) for k in item)


def test_failures_json_takes_its_options_by_keyword():
    kinds = [p.kind for p in inspect.signature(failures_json).parameters.values()]
    assert kinds[0] == inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert set(kinds[1:]) == {inspect.Parameter.KEYWORD_ONLY}
    assert [p.name for p in inspect.signature(failures_json).parameters.values()] == [
        "rows",
        "steps",
        "window",
        "window_unnamed",
        "min_jobs",
    ]


def test_failures_json_does_not_mutate_its_rows():
    rows = [row("test", 8), row(TEST_DART, 1)]
    before = list(rows)
    js(rows, steps={"test"})
    assert rows == before


# ── C. `FailureRow` 와 모듈 경계 (순수 계층) ────────────────────────────────


def test_failure_row_is_a_frozen_dataclass_with_four_fields():
    r = row("test", 8)
    assert [f.name for f in fields(FailureRow)] == [
        "name",
        "seen",
        "first_seen_job_id",
        "last_seen_job_id",
    ]
    assert r == FailureRow(name="test", seen=8, first_seen_job_id=141, last_seen_job_id=162)
    with pytest.raises(FrozenInstanceError):
        r.seen = 9  # type: ignore[misc]


def test_the_failures_module_never_reads_the_clock_or_the_filesystem():
    """`core/` 는 시계도 I/O 도 안 본다 — 창은 인자로 들어온다."""
    import remote_ci_monitor.core.failures as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    for banned in ("datetime.now", "utcnow", "time.time", "time.monotonic", "open(", "random."):
        assert banned not in text, banned


def test_the_failures_module_does_not_import_the_store_or_the_config():
    """대장을 읽는 질의는 `store` 의 몫이다. 순수 모듈이 그쪽을 알면 방향이 뒤집힌다."""
    import remote_ci_monitor.core.failures as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    for banned in ("import sqlite3", "remote_ci_monitor.store", "remote_ci_monitor.config"):
        assert banned not in text, banned
