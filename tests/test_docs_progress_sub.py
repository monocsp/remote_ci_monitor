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
    # 릴리스 뒤에는 항목이 그 버전의 절(0.3.0)에 있다 — 「0.1.0 이후 전부」를 본다(test_docs_m5)
    from test_docs_m5 import unreleased as since_first_release

    assert "::rcm::progress::" in since_first_release(changelog)
