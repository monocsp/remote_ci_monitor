"""문서 문면 잠금(M5j G1) — 종료 잡의 `step_timeline` 이 사용 안내 두 거울 · 설정 문서 · PLAN 의
서버 API 행 · CHANGELOG `[Unreleased]` 에 있어야 한다. 명세는 `docs/gate-optimization-workplan.md`
§2 G1 · 결정 84 — `rcm wait` JSON 에 실리는 것이 **계약**이므로 문서가 곧 기능의 절반이다.

`test_docs_m5` 처럼 정규식 스캔만 한다. 코드 테스트(`tests/test_step_timeline.py`)와 떼어 둔 것은
`scripts/mutcheck.py` 의 사본에 `docs/` 가 없기 때문이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_docs_m5 import CHANGELOG, CONFIGURATION, has, read, unreleased

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
PLAN = ROOT / "PLAN.md"


@pytest.mark.parametrize("path", [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")])
def test_both_usage_guides_name_the_timeline_in_the_json(path: Path):
    """래퍼가 읽는 키다 — 두 거울이 같이 말해야 한다."""
    assert has(read(path), r"`step_timeline`"), f"{path.name} never names step_timeline"


@pytest.mark.parametrize("path", [USAGE, USAGE_KO], ids=["en", "ko"])
def test_both_usage_guides_say_concurrent_at_start_counts_itself(path: Path):
    """`store.concurrent_at_start` 는 자기를 센다(혼자 돌면 1 · 모르면 None). 「돌던 잡 수」만
    적으면 0 으로 읽는다 — M5l S12.2 실측: 첫 잡 1 · 둘째 2. 두 거울과 CHANGELOG 가 같이 말한다."""
    text = read(path)
    assert has(text, r"`concurrent_at_start`"), f"{path.name} never names concurrent_at_start"
    assert has(text, r"itself included|자기 포함"), f"{path.name} does not say it counts itself"
    assert has(text, r"ran alone[^\n]*`1`|혼자 돌았으면 `1`"), f"{path.name}: alone reads 1"
    assert has(unreleased(read(CHANGELOG)), r"itself included"), "CHANGELOG: itself included"


def test_configuration_says_a_finished_job_keeps_its_step_times():
    text = read(CONFIGURATION)
    assert has(text, r"`step_timeline`"), "configuration.md never names step_timeline"
    assert has(text, r"step_timeline_error_code"), "configuration.md lacks the error code"
    # `ok` 의 마지막 스텝 규칙은 코드 폭 그대로 — 스크립트가 `::rcm::fail::` 로 선언한 스텝은
    # `false` 다(`progress.py` `fail_seen`). 「실패·취소면 null」만 적으면 그 가지를 숨긴다(D-3).
    assert has(text, r"`null` unless the script declared it failed"), (
        "configuration.md's `ok` sentence hides the declared-failure branch"
    )


def test_the_plan_names_the_key_on_the_job_route():
    assert has(read(PLAN), r"step_timeline"), "PLAN.md 「서버 API」 never names step_timeline"


def test_the_changelog_has_an_unreleased_entry_with_a_pr_link():
    text = unreleased(read(CHANGELOG))
    assert has(text, r"`step_timeline`"), "CHANGELOG [Unreleased] lacks step_timeline"
    m = has(
        text,
        r"`step_timeline`[^\n]*(\n[^\n]+)*?\(\[#\d+\]\(https://github\.com/monocsp/"
        r"remote_ci_monitor/pull/\d+\)\)",
    )
    assert m, "the step_timeline entry has no PR link"


def test_the_changelog_entry_sits_above_the_first_release_heading():
    """`unreleased()` 는 `[0.1.0]` 앞까지 전부를 돌려주므로 항목이 `[0.2.6]` 절에 미끄러져도
    초록이었다(리뷰 #108 C-1 · D-1 — 0.2.6 릴리스 커밋 위로 리베이스하면서 실제로 그렇게 됐다).
    항목은 첫 `## [0.` 제목보다 **위**, 즉 `[Unreleased]` 안에 있어야 한다."""
    text = read(CHANGELOG)
    entry = re.search(r"A finished job keeps its step times", text)
    first_release = re.search(r"^## \[0\.", text, re.M)
    assert entry, "CHANGELOG has no step_timeline entry"
    assert first_release, "CHANGELOG has no released section"
    assert entry.start() < first_release.start(), (
        "the step_timeline entry sits inside a released section, not in [Unreleased]"
    )
