"""문서 문면 잠금 — 산출물 받기의 **종료 코드**가 두 사용 안내 거울과 CHANGELOG 에 있어야 한다.

`rcm run --fetch-artifacts` 와 `rcm artifacts --fetch` 는 전달 실패에 5 를 낸다. 그런데 사용
안내 어디에도 그 5 가 없었다 — 스크립트가 분기할 계약인데 사람이 읽을 곳이 없었다. 값이 없으니
「0 이 아니면 잡이 실패한 것」으로 읽히고, 그 오독이 바로 이 PR 이 고친 fail-open 을 오래 살렸다.

⚠️ **문서 전체를 훑지 않는다.** §8 은 다른 항목에서도 `--force` 와 `conflicted` 를 말한다.
종료 코드를 말하는 항목만 떼어 내 거기에 묻는다 — 안 그러면 그 항목을 통째로 지워도 초록이다
(같은 실수를 `test_docs_gitref_fetch` 에서 이미 한 번 했다).

`test_docs_m5` 처럼 정규식 스캔만 한다 — `scripts/mutcheck.py` 의 사본에 `docs/` 가 없다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_docs_m5 import CHANGELOG, has, read, unreleased

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"

BOTH = [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")]

#: 「받을 것이 설계상 없었다」쪽 — 이 둘만 0 이다.
BY_DESIGN = ("disabled", "empty")


def items(text: str) -> list[str]:
    """번호 목록의 항목들(이어지는 들여쓴 줄 포함)."""
    return re.findall(r"^\d+\.[^\n]*(?:\n[ \t]+[^\n]*)*", text, re.M)


def item_with(text: str, *needles: str) -> str:
    """조건을 **전부** 만족하는 항목. 낱말 하나로 찾으면 엉뚱한 항목이 걸린다 — §8 항목 ②도
    `--dry-run` 을 말하지만 종료 코드 이야기는 아니다(실측으로 걸렸다)."""
    for item in items(text):
        if all(n in item for n in needles):
            return item
    return ""


@pytest.mark.parametrize("path", BOTH)
def test_both_usage_guides_say_which_states_still_exit_zero(path: Path):
    """0 의 뜻이 「받았다」 하나가 아니라는 것 — `disabled`·`empty` 도 0 이다. 이걸 안 적으면
    「0 인데 파일이 없다」가 버그로 신고된다."""
    item = item_with(read(path), "`disabled`")
    assert item, f"{path.name} §8 has no item about the exit code"
    for state in BY_DESIGN:
        assert has(item, rf"`{state}`"), f"{path.name}: that item never names `{state}` — {item!r}"
    assert has(item, r"\b0\b"), f"{path.name}: that item never says 0 — {item!r}"


@pytest.mark.parametrize("path", BOTH)
def test_both_usage_guides_say_everything_else_is_five(path: Path):
    """5 가 문서에 없으면 스크립트 작성자는 「0 이 아니면 잡이 실패」로 읽는다 — 실행 실패와
    전달 실패를 가르는 값 자체가 안 보인다."""
    item = item_with(read(path), "`disabled`")
    assert item, f"{path.name} §8 has no item about the exit code"
    assert has(item, r"\*\*5\*\*"), f"{path.name}: that item never says 5 — {item!r}"
    assert has(item, r"`pending`"), (
        f"{path.name}: that item does not name pending — 「아직 안 끝났는데 0」이 그 버그였다"
    )


@pytest.mark.parametrize("path", BOTH)
def test_both_usage_guides_settle_the_dry_run_exit_code(path: Path):
    """시나리오 c #13 이 열어 뒀던 자리다. 매듭지었으면 문서가 말해야 한다."""
    item = item_with(read(path), "`--dry-run`", "`conflicted`", "5")
    assert item, f"{path.name} §8 has no item giving --dry-run's exit code"
    assert has(item, r"\b0\b") and has(item, r"\b5\b"), (
        f"{path.name}: the --dry-run item does not give both codes — {item!r}"
    )
    assert has(item, r"`conflicted`"), f"{path.name}: it does not say what makes it 5 — {item!r}"


@pytest.mark.parametrize("path", BOTH)
def test_the_exit_code_list_itself_names_the_delivery_code(path: Path):
    """§8 에만 적으면 종료 코드를 **세러** 온 사람은 영영 못 본다.

    §6 은 1·2·3 을 나란히 세어 준다 — 스크립트를 쓰는 사람이 읽는 곳이 거기다. 5 가 그 목록에
    없으면 「0 이 아니면 잡이 실패」로 읽고, `--fetch-artifacts` 의 전달 실패를 실행 실패로
    보고한다. 잡 결과가 `wait_exit_code` 에 따로 있다는 것도 거기서 말해야 한다."""
    item = item_with(read(path), "`--fetch-artifacts`", "`wait_exit_code`")
    assert item, f"{path.name}: the exit-code list never mentions the delivery code"
    assert has(item, r"\b3\b"), f"{path.name}: that is not the exit-code list — {item!r}"
    assert has(item, r"\b5\b"), f"{path.name}: the exit-code list omits 5 — {item!r}"


def test_the_changelog_says_an_unfinished_job_used_to_exit_zero():
    """고친 것이 「종료 코드 손질」이 아니라 **fail-open** 이었다는 게 이 항목의 값이다."""
    text = unreleased(read(CHANGELOG))
    assert has(text, r"--fetch"), "CHANGELOG [Unreleased] lacks the artifact fetch entry"
    assert has(text, r"`pending`"), "CHANGELOG entry does not name the state that proves it"
    assert has(text, r"exit(ed)? 0|exit code was 0"), (
        "CHANGELOG entry does not say the old code was 0 — that is the point"
    )
