"""문서 잠금 — M5o C: `include` 와 `rcm reload --dry-run`(명세 §4 · §7 완료 기준 3).

기능이 문서 없이 들어가면 빨갛다. 특히 §4.1 의 경고 문단 — 들인 파일을 바꿀 수 있는 사람이 다음
reload 때 서버가 돌릴 argv 를 정한다 — 은 빠지면 안 되는 문장이라 글자로 잠근다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def flat(text: str) -> str:
    """줄바꿈 · 연속 공백을 하나로 — 문단 안의 줄 나눔에 시험이 매이지 않게."""
    return re.sub(r"\s+", " ", text)


def test_configuration_documents_include_and_its_rules():
    text = flat(read("docs/configuration.md"))
    assert "### Presets in their own file: `include`" in read("docs/configuration.md")
    assert 'include = ["presets.dolomood.toml"]' in text
    assert "may only hold `[[presets]]`" in text
    assert "chmod go-w" in text
    assert "before the first `[section]`" in text
    assert "is defined in both server.toml and presets.dolomood.toml" in text
    assert "local presets 24 (server.toml 2 · presets.dolomood.toml 22)" in text


def test_configuration_carries_the_warning_paragraph():
    text = flat(read("docs/configuration.md"))
    assert "rcm does not fetch the file" in text
    assert (
        "whoever can change an included file decides the argv the server runs on the next reload"
        in text
    )
    assert "push to that repository" in text


def test_operating_documents_the_dry_run():
    text = flat(read("docs/operating.md"))
    assert "rcm reload --dry-run" in text
    assert "nothing applied (--dry-run)" in text
    assert "never their values" in text
    assert "presets_changed_keys" in text


def test_usage_guides_mention_the_dry_run_in_both_languages():
    for rel in ("docs/usage.md", "docs/usage.ko.md"):
        text = read(rel)
        assert "rcm reload --dry-run" in text, rel
        assert "include" in text, rel


def test_readme_command_tables_list_the_flag():
    for rel in ("README.md", "README.ko.md"):
        assert "`rcm reload [--dry-run]`" in read(rel), rel


def test_example_server_toml_shows_include_commented_out():
    text = read("examples/server.toml")
    assert '# include = ["presets.myproject.toml"]' in text


def test_changelog_unreleased_has_both():
    # `[Unreleased]` 부터 M5o 이전 릴리스 `[0.5.1]` 앞까지 — 릴리스 뒤에도 읽는다
    text = read("CHANGELOG.md")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [0.5.1]", 1)[0]
    assert "include = [" in unreleased
    assert "rcm reload --dry-run" in unreleased
