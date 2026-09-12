"""문서 문면 잠금(M5j G5) — 서버 키 `cancel_requires_submission_token`, `rcm cancel --cancel-token`,
상태 파일 `submissions.json`, 옛 클라이언트(0.2.x)에 대한 안내, `examples/server.toml` 의 주석 키,
CHANGELOG `[Unreleased]` 항목.

test_docs_m5 처럼 정규식 스캔만 한다. 구현보다 먼저 썼다(test-first) — 문서가 없으면 빨갛다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
SERVER_TOML = ROOT / "examples" / "server.toml"
CONFIGURATION = ROOT / "docs" / "configuration.md"
OPERATING = ROOT / "docs" / "operating.md"
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
KEY = "cancel_requires_submission_token"


def read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(ROOT)}"
    return path.read_text()


def has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.M) is not None


def unreleased() -> str:
    text = read(CHANGELOG)
    m = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[)", text, re.S | re.M)
    assert m, "CHANGELOG has no [Unreleased] section"
    return m.group(1)


@pytest.mark.parametrize(
    "pattern",
    [
        rf"`{KEY}`",
        r"`--cancel-token`",
        r"submissions\.json",
        r"0\.2\.x",  # 켜면 옛 클라이언트의 취소가 어떻게 되는지
        r"cancel_min_client_version",
        r"admin",
    ],
)
def test_configuration_documents_the_key_and_what_turning_it_on_means(pattern: str):
    text = read(CONFIGURATION)
    section = text[text.index(KEY) - 2000 : text.index(KEY) + 4000]
    assert has(section, pattern), f"docs/configuration.md lacks /{pattern}/ near {KEY}"


def test_configuration_says_the_default_is_off_and_ctrl_c_still_detaches():
    text = read(CONFIGURATION)
    assert has(text, rf"{KEY} = false"), "the default (false) is not shown as a toml line"
    assert has(text, r"Ctrl-C"), "the Ctrl-C contract (detach, not cancel) is not restated"


@pytest.mark.parametrize("path", [USAGE, USAGE_KO])
def test_usage_guides_explain_rcm_cancel_the_state_file_and_the_flag(path: Path):
    text = read(path)
    for pattern in (r"`rcm cancel", r"submissions\.json", r"`--cancel-token", r"0600"):
        assert has(text, pattern), f"{path.name} lacks /{pattern}/"


def test_operating_security_notes_treat_the_cancel_token_as_a_secret():
    text = read(OPERATING)
    assert has(text, r"cancel token"), "operating.md security notes do not mention the cancel token"
    assert has(text, r"SHA-256"), "operating.md does not say only a hash is stored"


def test_example_server_toml_carries_the_commented_key():
    text = read(SERVER_TOML)
    assert has(text, rf"^# ?{KEY} = false"), "examples/server.toml lacks the commented key"


def test_changelog_unreleased_has_an_added_entry_with_a_pull_request_link():
    added = unreleased()
    assert "### Added" in added
    entries = [p for p in added.split("\n- ") if KEY in p or "cancel token" in p]
    assert entries, "no [Unreleased] entry about the cancel capability"
    assert any(
        re.search(r"\(\[#\d+\]\(https://github\.com/monocsp/remote_ci_monitor/pull/\d+\)\)", e)
        for e in entries
    ), "the entry has no pull request link"
    assert any("v17" in e or "17" in e for e in entries), "the entry does not name the DB version"
