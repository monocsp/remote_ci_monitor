"""문서 잠금 — M5s(명세 `docs/m5s-workplan.md` §5 · §7). PR 마다 자기 몫의 함수만 더한다.

함수 이름에 PR 주제를 넣는다: `action_marker`(C) · …. 기능이 문서 없이 들어가면 빨갛다.
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


# ── C: 「사람이 할 일」 표식 (§4.3 · 결정 213 · 234) ─────────────────────────


def test_action_marker_contract_section_3_names_the_marker_and_the_privacy_rule():
    text = read("docs/release-contract.md")
    sec = text[text.index("## 3. Markers") : text.index("## 4. Secrets")]
    assert "::rcm::action::<code>::<note>" in sec
    assert "[a-z0-9_-]{1,32}" in sec
    assert "200 characters" in sec
    assert "personal detail" in flat(sec)
    for word in ("needs_action", "--return-on-action", "RCM_ACTION_CODE", "RCM_ACTION_NOTE"):
        assert word in sec, word


def test_action_marker_readme_lists_the_marker_and_exit_4_in_both_languages():
    for rel in ("README.md", "README.ko.md"):
        text = read(rel)
        assert "::rcm::action::login::" in text, rel
        assert "configuration.md#when-a-person-has-to-act" in text, rel
        rows = [ln for ln in text.splitlines() if ln.startswith("| 4 |")]
        assert len(rows) == 1 and "--return-on-action" in rows[0], (rel, rows)


def test_action_marker_configuration_has_the_section_and_the_hook_env():
    text = read("docs/configuration.md")
    assert "### When a person has to act" in text
    sec = text[text.index("### When a person has to act") : text.index("### Saying what failed")]
    body = flat(sec)
    for word in ("{code, note, at}", "needs_action", "--return-on-action", "exit", "**4**"):
        assert word in body, word
    # 이미 있던 표식에서도 돌아오고, 다시 기다릴 때는 플래그 없이다 — 고리를 막는 문장
    assert "already there when the wait started" in body
    assert "**without** the flag" in body
    hooks = flat(text[text.index("- **Notifications**") :])
    assert "`RCM_ACTION_CODE` and `RCM_ACTION_NOTE`" in hooks


def test_action_marker_notify_failures_limit_is_written_down():
    """`rcm check` 의 warn 행과 「재시작하면 0」 한계(명세 §4.7)."""
    config = flat(read("docs/configuration.md"))
    assert "`warn notify` row" in config
    assert "a restart sets it back to 0" in config
    operating = flat(read("docs/operating.md"))
    assert "warn notify N hook deliveries failed" in operating
    assert "sets it to 0" in operating


def test_action_marker_usage_guides_mirror_the_return_on_action_paragraph():
    en = flat(read("docs/usage.md"))
    ko = flat(read("docs/usage.ko.md"))
    for text in (en, ko):
        assert "rcm wait --job N --return-on-action" in text
        assert "rcm wait --return-on-action" in text  # 종료 코드 4 문단
        assert "#412 needs action · login · Sign in on the build machine" in text
    assert "When the job needs a person." in en
    assert "사람이 손대야 할 때." in ko


def test_action_marker_cli_help_names_the_flag():
    from remote_ci_monitor.cli import build_parser

    sub = next(a for a in build_parser()._actions if a.__class__.__name__ == "_SubParsersAction")
    wait_help = sub.choices["wait"].format_help()
    assert "--return-on-action" in wait_help and "exit 4" in wait_help
