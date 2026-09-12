"""문서 문면 잠금(M5j G1) — 종료 잡의 `step_timeline` 이 사용 안내 두 거울 · 설정 문서 · PLAN 의
서버 API 행 · CHANGELOG `[Unreleased]` 에 있어야 한다. 명세는 `docs/gate-optimization-workplan.md`
§2 G1 · 결정 84 — `rcm wait` JSON 에 실리는 것이 **계약**이므로 문서가 곧 기능의 절반이다.

`test_docs_m5` 처럼 정규식 스캔만 한다. 코드 테스트(`tests/test_step_timeline.py`)와 떼어 둔 것은
`scripts/mutcheck.py` 의 사본에 `docs/` 가 없기 때문이다.
"""

from __future__ import annotations

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


def test_configuration_says_a_finished_job_keeps_its_step_times():
    text = read(CONFIGURATION)
    assert has(text, r"`step_timeline`"), "configuration.md never names step_timeline"
    assert has(text, r"step_timeline_error_code"), "configuration.md lacks the error code"


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
