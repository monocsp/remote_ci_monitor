"""문서 문면 잠금(M5j G4) — 프리셋 `requires` · `tool_missing` · launchd PATH 함정 · `local preset
tools` 행. 명세 `docs/gate-optimization-workplan.md` §2 G4.

`test_docs_m5` 처럼 정규식만 본다. 문서가 기능을 안 따라오면 빨갛다.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_docs_m5 import CHANGELOG, CONFIGURATION, SERVER_TOML, has, read, unreleased

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"


def test_configuration_documents_requires_in_the_preset_snippet():
    sec = read(CONFIGURATION)
    assert has(sec, r'^requires = \["'), "configuration.md preset snippet lacks requires"


def test_configuration_has_a_required_tools_section_with_the_launchd_trap():
    text = read(CONFIGURATION)
    assert has(text, r"^### Required tools"), "configuration.md lacks '### Required tools'"
    body = text.split("### Required tools", 1)[1]
    for needle in (r"tool_missing", r"launchd", r"\[presets\.env\]", r"local preset tools"):
        assert has(body, needle), f"'Required tools' section lacks /{needle}/"
    # 정본이 어디인지 — 셸의 rcm check 가 아니라 잡 시작 전 검사다
    assert has(body, r"(?i)before (the job|it) starts|at job start|before starting"), body


def test_configuration_says_the_local_row_fails_and_the_window_skips_tool_missing():
    """검증 G4.10 · G4.4: `local preset tools` 가 FAIL(종료 1)인 것과 `tool_missing` 잡이 대장
    창에서 빠지는 것을 문서가 말한다 — 「FAIL 이면 문서가 그렇게 말한다」."""
    text = read(CONFIGURATION)
    body = text.split("### Required tools", 1)[1].split("\n### ", 1)[0]
    assert has(body, r"FAIL") and has(body, r"exit 1"), body
    window = [p for p in text.split("\n\n") if "failure_window_jobs" in p]
    assert window and has(window[0], r"tool_missing"), window


def test_the_example_config_mentions_requires():
    assert has(read(SERVER_TOML), r"requires"), "examples/server.toml lacks requires"


def test_changelog_unreleased_mentions_requires_and_tool_missing():
    sec = unreleased(read(CHANGELOG))
    assert has(sec, r"`requires`") and has(sec, r"tool_missing"), sec


def test_mutcheck_count_in_agents_matches_contributing():
    """두 문서의 변이 수가 같다(`test_release_files` 가 CONTRIBUTING 과 스크립트를 맞춘다)."""
    a = re.search(r"(\d+) known mutations", read(AGENTS))
    c = re.search(r"(\d+) known mutations", read(CONTRIBUTING))
    assert a and c and a.group(1) == c.group(1), (a, c)
