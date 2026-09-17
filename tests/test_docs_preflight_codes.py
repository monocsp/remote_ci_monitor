"""문서 문면 잠금(F2b) — 「시작조차 못 한 작업」의 코드와 그 보안 약속.

**무효 잠금을 만들지 않는 것이 이 파일의 요점이다.** 문서 전체에서 낱말을 찾으면, 그 낱말이 딴
절에도 있는 순간 주장을 통째로 지워도 초록이다. 그래서 여기서는 **주장을 담은 절/항목만 떼어**
그 안에 묻는다. 떼어 낼 머리말이 사라지면 그 자리에서 빨개진다(`block()` 이 단언한다).

그리고 문서와 구현을 **서로** 묶는다: 문서가 말하는 코드는 `core/outcome.PREFLIGHT_CODES` 에
있어야 하고, 그 코드는 전부 설정 문서에 이름이 있어야 한다. 한쪽만 늘면 빨개진다 — 손으로 적은
목록이 생산자를 못 보는 문제를 여기서 닫는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from remote_ci_monitor.core.outcome import PREFLIGHT_CODES

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
USAGE_EN = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"


def block(path: Path, head: str, tail: str) -> str:
    """`head` 로 시작해 `tail` 앞에서 끝나는 토막. 둘 중 하나라도 없으면 그 자리에서 실패다."""
    text = path.read_text(encoding="utf-8")
    start = text.find(head)
    assert start >= 0, f"{path.name}: `{head[:40]}…` 절이 사라졌다"
    rest = text[start:]
    end = rest.find(tail)
    assert end > 0, f"{path.name}: `{tail[:40]}…` 가 사라졌다 — 잠글 범위를 못 정한다"
    return rest[:end]


def unreleased_entry(head: str) -> str:
    """항목 하나 — 릴리스 뒤에는 `[Unreleased]` 가 아니라 그 버전의 절(0.3.0)에 있으므로, 다른
    문서 잠금(tests/test_docs_m5.py `unreleased()`)처럼 「0.1.0 이후 전부」에서 찾는다."""
    from test_docs_m5 import unreleased as since_first_release

    section = since_first_release(CHANGELOG.read_text(encoding="utf-8"))
    start = section.find(head)
    assert start >= 0, f"CHANGELOG [Unreleased] 에 `{head[:40]}…` 항목이 없다"
    after = section[start + len(head) :]
    nxt_item = re.search(r"^(- |### |## )", after, re.M)
    return head + (after[: nxt_item.start()] if nxt_item else after)


def codes_in(text: str) -> set[str]:
    return {c for c in PREFLIGHT_CODES if f"`{c}`" in text}


def flat(text: str) -> str:
    """줄바꿈을 지운 한 줄. 문서는 100자에서 접히므로 문구가 줄에 걸쳐 있다."""
    return re.sub(r"\s+", " ", text)


# ── CHANGELOG ────────────────────────────────────────────────────────────────


def test_the_changelog_entry_names_the_codes_and_the_null_exit_code():
    entry = unreleased_entry("- **A job that never started now says so.**")
    named = codes_in(entry)
    assert len(named) >= 10, f"코드를 {len(named)}개만 말한다: {sorted(named)}"
    assert "`exit_code: null`" in entry
    # 기계가 읽을 사실을 말한다는 것이 이 항목의 요점이다
    assert re.search(r"without reading the sentence", entry), entry


def test_the_changelog_does_not_claim_more_than_the_code_does_about_remote_workers():
    """「원격도 같은 코드」는 F2b 1차에서 **거짓**이었다(원격은 프리셋 소멸을 시작 실패로 보고했다).
    이제 참이고, 참인 이유(같은 표)를 말한다. 주장을 다시 세게 쓰면 이 시험이 잡는다."""
    entry = unreleased_entry("- **A job that never started now says so.**")
    said = flat(entry)
    assert "same table" in said, entry
    assert "`preset_missing`" in entry and "which lane picked the job up" in said
    # 「원격도 같은 코드」가 참인 **범위**를 같이 적는다: 버전 검사는 등록 때뿐이라, 재기동 전의
    # 구 워커는 여전히 코드 없이 보고한다(검토 3). 이 예외를 지우면 주장이 다시 구현보다 세진다.
    assert "only checked when it registers" in said, entry


def test_the_changelog_says_the_cancel_race_is_closed_on_the_remote_path_too():
    entry = unreleased_entry(
        "- **Cancelling a job while its workspace is being prepared now wins.**"
    )
    assert "remote worker" in flat(entry), entry


def test_the_security_entry_promises_no_free_text_and_a_token_for_the_original():
    """「절대 경로가 안 나간다」는 씻기로는 못 지키는 약속이었다 — 상대 경로·`~/`·UNC·토큰 모양이
    그대로 나갔다. 지금 약속은 「자유 문구를 아예 안 싣는다」이고, 그건 구현이 지킬 수 있다."""
    entry = unreleased_entry(
        "- **The job document no longer carries free text from a failure before the job starts.**"
    )
    said = flat(entry)
    assert "closed list" in said and "declared shape" in said
    assert "job log" in said and "token" in said
    assert "last segment" in said  # 멤버 이름은 남기되 이름만


# ── docs/configuration.md ────────────────────────────────────────────────────


def test_configuration_documents_every_preflight_code():
    """설정 문서가 정본이다 — 코드를 더하면서 문서를 안 고치면 빨개진다."""
    sec = block(CONFIGURATION, "**When a job never starts.**", "**The launchd trap.**")
    missing = sorted(set(PREFLIGHT_CODES) - codes_in(sec))
    assert not missing, f"configuration.md 가 말하지 않는 코드: {missing}"


def test_configuration_says_what_is_in_the_summary_and_what_needs_a_token():
    sec = flat(block(CONFIGURATION, "**When a job never starts.**", "**The launchd trap.**"))
    assert "`exit_code: null`" in sec
    assert "no free text" in sec.replace("**", "")
    assert "needs a token" in sec and "`log_unavailable`" in sec
    assert "`escape.txt`" in sec  # 어느 멤버였는지는 남는다
    assert "lose to a cancel" in sec and "remote worker" in sec
    assert "not on every report" in sec  # 주장의 범위 — 구 워커는 예외다(검토 3)


# ── 두 거울(usage.md · usage.ko.md) ──────────────────────────────────────────


def test_both_usage_mirrors_name_the_same_codes():
    en = block(USAGE_EN, "5. **A job that never started says so.**", "\n\nA cancelled job")
    ko = block(USAGE_KO, "5. **시작조차 못 한 작업은 그렇다고 말한다.**", "\n\n취소한 작업에는")
    assert codes_in(en) == codes_in(ko), "두 거울이 다른 코드를 말한다"
    assert len(codes_in(en)) >= 10


def test_the_english_usage_mirror_keeps_the_two_promises():
    en = flat(block(USAGE_EN, "5. **A job that never started says so.**", "\n\nA cancelled job"))
    assert "`exit_code: null`" in en and "`summary_code`" in en
    assert "no free text" in en and "needs a token" in en


def test_the_korean_usage_mirror_keeps_the_two_promises():
    ko = flat(
        block(USAGE_KO, "5. **시작조차 못 한 작업은 그렇다고 말한다.**", "\n\n취소한 작업에는")
    )
    assert "`exit_code` 는 `null`" in ko and "`summary_code`" in ko
    assert "자유 문구가 **하나도**" in ko and "토큰이 있어야" in ko
