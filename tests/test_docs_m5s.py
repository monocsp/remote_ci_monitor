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
        "2 blocked (a chosen platform is not approved; never 75) · also environment / usage",
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


# ── PR E: status 라우트 · 역할별 창 · 검토 패널 (§4.4 · §3 #11) ──────────────


def test_release_status_panel_configuration_names_the_route_and_the_window():
    text = CONFIGURATION.read_text()
    for needle in (
        "| `POST …/release/status` | client token | `{build_name?, ref?}` → submits "
        "`presets.status`",
        "409 `role_not_configured` when the profile has no `status` preset",
        "`status` (`job_id`, `state`, `measured_at`, `age_seconds`, `stale`, `doc` — aged from the "
        "document's `checked_at`, no `build_name`)",
        "its own window of its newest 500 jobs",
        "at most 3 rows of their own in `jobs[]`",
    ):
        assert needle in text, f"docs/configuration.md lacks {needle!r}"


def test_release_status_panel_readmes_and_changelog_name_the_panel():
    en = re.sub(r"\s+", " ", (ROOT / "README.md").read_text())
    ko = re.sub(r"\s+", " ", (ROOT / "README.ko.md").read_text())
    assert "With a `status` preset the panel also shows each store's review state" in en, (
        "README.md lacks the review panel's store state"
    )
    assert "**Check again** runs the preset once more" in en, "README.md lacks **Check again**"
    assert "`status` 프리셋이 있으면 패널에 두 스토어의 심사 상태도 나온다" in ko, (
        "README.ko.md lacks the review panel's store state"
    )
    assert "**새로 조회**는 그 프리셋을 한 번 더 돌린다" in ko, "README.ko.md lacks **새로 조회**"
    text = re.sub(r"\s+", " ", unreleased_only(CHANGELOG.read_text()))
    for needle in (
        "`POST /api/repos/<repo>/release/status`",
        "**Check again**",
        "own window of 500 jobs",
        "`unknown` for any other",
    ):
        assert needle in text, f"CHANGELOG.md [Unreleased] lacks {needle!r}"
