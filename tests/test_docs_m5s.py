"""M5s 문서 잠금 — 스토어 심사 확인 · 승인 뒤 출시 · 예약 실행.

명세는 `docs/m5s-workplan.md` 다. PR 마다 자기 몫의 함수만 더한다(함수 이름에 PR 주제를 넣는다).
mutcheck 의 복사본(`src` · `tests` · `pyproject.toml`)에는 문서가 없으므로 코드 시험과 나눈다.
실패 문구는 빠진 낱말과 파일을 말한다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "release-contract.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
CHANGELOG = ROOT / "CHANGELOG.md"


def unreleased_only(text: str) -> str:
    """`## [Unreleased]` 부터 다음 `## [` 절 앞까지."""
    m = re.search(r"^## \[Unreleased\][^\n]*$", text, re.M)
    assert m, "no `## [Unreleased]`"
    rest = text[m.end() :]
    nxt = re.search(r"^## \[", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def contract_roles_table() -> str:
    text = CONTRACT.read_text()
    start = text.index("## 2. Roles, inputs and artifacts")
    return text[start : text.index("\n\n**Required**", start)]


# ── PR B: status · golive 역할 등록 (§4.4 · §4.6) ────────────────────────────


def test_release_roles_contract_table_has_the_status_and_golive_rows():
    table = contract_roles_table()
    status = re.search(
        r"^\| `status` \| optional \| `build_name`.*\| `status\.json` \|", table, re.M
    )
    assert status, "docs/release-contract.md §2 lacks the `status` row"
    golive = re.search(r"^\| `golive` \| optional \|.*$", table, re.M)
    assert golive, "docs/release-contract.md §2 lacks the `golive` row"
    row = golive.group(0)
    for needle in (
        "`build_name_android`",
        "`confirm_build_number`",
        "`mode = plan\\|release` (default **plan**)",
        "`golive-plan.json` (plan) / `golive.json` (release)",
        "0 read (approved or not — that is in the file)",
        "2 blocked (a chosen platform is not approved; never 75)",
        "3 build number mismatch",
    ):
        assert needle in row, f"the `golive` row lacks {needle!r}"


def test_release_roles_contract_lists_the_status_and_golive_minimum_fields():
    text = CONTRACT.read_text()
    for needle in (
        "// status.json",
        "preparing · waiting_for_review · in_review · approved · rejected · releasing ·",
        "// golive-plan.json",
        "plan_verdict ok requires every listed platform approved",
        "// golive.json",
        '"result": "published|pending|needs_login|failed"',
    ):
        assert needle in text, f"docs/release-contract.md lacks {needle!r}"


def test_release_roles_configuration_names_the_check_rules():
    text = CONFIGURATION.read_text()
    for needle in (
        'status = "release-status"',
        'golive = "release-golive"',
        "or the `golive` preset's `mode` defaults to `release`",
        "a `golive` preset lacks `build_name confirm_build_number platform mode`",
        "(`gate`, `qa`, `dev`, `version`, `status`, `golive`) is unset",
        "the `review`, `upload` or `golive` preset has no `build_name_android` input",
    ):
        assert needle in text, f"docs/configuration.md lacks {needle!r}"


def test_release_roles_changelog_has_the_entry():
    text = re.sub(r"\s+", " ", unreleased_only(CHANGELOG.read_text()))
    for needle in (
        "`status` and `golive`",
        "`release_status.sh`",
        "`release_golive.sh`",
        "`golive-plan`",
        "`rcm check`",
    ):
        assert needle in text, f"CHANGELOG.md [Unreleased] lacks {needle!r}"
