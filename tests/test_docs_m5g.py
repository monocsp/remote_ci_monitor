"""문서 문면 잠금(M5g) — 보존 키 표 · 업그레이드 절차 · `rcm gc` · CHANGELOG.

`test_docs_m5` 처럼 정규식과 절 스캔만 한다. 구현보다 먼저 썼다 — 문서가 없으면 빨갛다.

여기서 잠그는 것은 **사람이 잃을 수 있는 정보**다: 새 키 셋이 설치할 때 파일에서 보이는가(결정 60) ·
업그레이드가 무엇을 지우는지 미리 보는 법이 적혀 있는가(결정 61) · 되돌리는 법이 있는가.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_docs_m5 import CHANGELOG, SERVER_TOML, has, read, unreleased
from test_docs_m5b import section


def subsection(text: str, heading: str) -> str:
    """`### <heading>` 부터 다음 `## ` 또는 `### ` 앞까지 — operating.md 의 Retention 은 3단이다."""
    m = re.search(rf"^### {re.escape(heading)}[^\n]*$", text, re.M)
    assert m, f"no `### {heading}`"
    rest = text[m.end() :]
    nxt = re.search(r"^#{2,3} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


CONFIG = Path(__file__).resolve().parents[1] / "docs" / "configuration.md"
OPERATING = Path(__file__).resolve().parents[1] / "docs" / "operating.md"
KEYS = ["workspace_retention_days", "workspace_storage_max_bytes", "min_free_bytes"]


# ── examples/server.toml — 결정 60: 주석이 아니라 값으로 있어야 한다 ──────────


@pytest.mark.parametrize("key", KEYS)
def test_the_example_config_sets_the_key_uncommented(key: str) -> None:
    """데이터를 지우는 값은 설치할 때 **파일에서 보여야** 한다 — 주석이면 기본값이 안 보인다."""
    lines = [ln for ln in read(SERVER_TOML).splitlines() if re.match(rf"^\s*{key}\s*=", ln)]
    assert lines, f"examples/server.toml lacks an uncommented `{key} = …`"


def test_the_example_config_explains_why_those_three_are_written_out() -> None:
    text = read(SERVER_TOML)
    i = text.index("workspace_retention_days")
    nearby = text[max(0, i - 400) : i]
    assert has(nearby, r"\bdelete|\bbulk\b|\bworkspace\b"), nearby


# ── docs/configuration.md — 키 표와 두 시계 ──────────────────────────────────


def test_configuration_has_a_retention_section_with_every_key() -> None:
    sec = section(read(CONFIG), "Retention")
    for key in [*KEYS, "retention_days_success", "retention_days_failure"]:
        assert has(sec, rf"`{key}`"), f"configuration.md Retention lacks `{key}`"


def test_configuration_says_the_log_and_the_workspace_are_worth_different_amounts() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"50 KB") and has(sec, r"720 MB"), sec


def test_configuration_promises_evidence_is_never_traded_for_room() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"never|not.{0,20}delete", re.I)
    assert has(sec, r"evidence", re.I)


def test_configuration_says_a_size_it_cannot_measure_is_not_guessed() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"cannot be measured|could not be measured", re.I), sec


def test_configuration_shows_the_offline_dry_run() -> None:
    """업그레이드 게이트 — 서버를 올리기 전에 무엇이 지워질지 보는 법."""
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"rcm gc --dry-run"), sec
    assert has(sec, r"--config"), sec


def test_configuration_says_the_floor_is_not_a_guarantee() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"not a guarantee|target, not", re.I), sec


# ── docs/operating.md — 업그레이드 절차와 되돌리는 법 ────────────────────────


def test_operating_retention_has_the_upgrade_procedure() -> None:
    sec = subsection(read(OPERATING), "Retention")
    assert has(sec, r"rcm gc --dry-run"), sec
    assert has(sec, r"pull --ff-only|upgrad", re.I), sec


def test_operating_says_how_to_keep_the_old_behaviour() -> None:
    """되돌리는 법이 같은 자리에 없으면, 놀란 사람이 검색으로 찾아야 한다."""
    sec = subsection(read(OPERATING), "Retention")
    assert has(sec, r"workspace_retention_days = 30|old behaviour|old behavior", re.I), sec


# ── CHANGELOG ────────────────────────────────────────────────────────────────


def test_changelog_calls_it_a_behaviour_change_and_says_how_to_undo_it() -> None:
    text = unreleased(read(CHANGELOG))
    assert has(text, r"workspace_retention_days"), text
    assert has(text, r"upgrade", re.I), text
    assert has(text, r"rcm gc --dry-run"), text
