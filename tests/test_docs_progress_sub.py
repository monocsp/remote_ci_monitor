"""문서 문면 잠금 — `::rcm::progress::`(스텝 안의 세부 진행)가 README(영·한) · 설정 문서 ·
CHANGELOG 에 있다."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_docs_show_the_progress_marker():
    for rel in ("README.md", "README.ko.md", "docs/configuration.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "::rcm::progress::" in text, f"{rel} does not show the progress marker"
    conf = (ROOT / "docs/configuration.md").read_text(encoding="utf-8")
    for word in (
        "sub",
        "units_truncated",
        "run · ok · fail · skip · env · review · blocked · wait",
    ):
        assert word in conf, f"docs/configuration.md is missing {word!r}"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert "::rcm::progress::" in unreleased
