"""문서 문면 잠금 — `git_ref` 잡의 산출물 회수가 두 사용 안내 거울과 CHANGELOG `[Unreleased]` 에
있어야 한다.

이 기능이 **조용히 죽어 있었던 이유가 문서였다.** CLI 는 `git_ref` + `--fetch-artifacts` 에
`--output DIR` 을 요구하고 `--no-wait` 와의 조합을 거절했다 — 거절 두 갈래는 시험으로 잠겨 있었다.
그런데 정작 받는 쪽은 없었고, 잡은 초록이고 종료 코드는 0 이고 받은 파일은 0 이었다. 「거절은
잠갔는데 성공 경로는 안 잠갔다」가 그 틈이다(같은 교훈: `locked-key-sets-hide-bugs`).

⚠️ **문서 전체를 훑어서는 안 된다.** 첫 판은 `has(text, "`--force`")` 로 문서를 통째로 봤는데,
§8 항목 ②가 이미 `conflicted`·`--force` 를 말하고 있어서 **`git_ref` 항목에서 그 말을 지워도
5 passed** 였다(실측). 그래서 `git_ref` 를 말하는 목록 항목 **하나만** 떼어 내 거기에 묻는다.

`test_docs_m5` 처럼 정규식 스캔만 한다 — `scripts/mutcheck.py` 의 사본에 `docs/` 가 없어서
코드 테스트와 떼어 둔다. 코드 쪽 짝은 `tests/test_cli_m5e.py` 의 e2e 다(실제로 잡을 끝까지
돌려 받은 파일을 센다).
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


def item_naming_git_ref(text: str) -> str:
    """번호 목록에서 `git_ref` 를 말하는 항목 하나(이어지는 들여쓴 줄 포함)를 떼어 낸다."""
    for item in re.findall(r"^\d+\.[^\n]*(?:\n[ \t]+[^\n]*)*", text, re.M):
        if "`git_ref`" in item:
            return item
    return ""


@pytest.mark.parametrize("path", BOTH)
def test_both_usage_guides_tell_a_git_ref_job_where_to_write(path: Path):
    """제출한 트리가 없으니 어디에 쓸지는 사람이 말해야 한다. 그 말이 없으면 독자는 §8 을
    tree 모드 이야기로만 읽고 `git_ref` 프리셋에서는 못 쓰는 줄 안다."""
    item = item_naming_git_ref(read(path))
    assert item, f"{path.name} §8 has no list item about git_ref"
    assert has(item, r"`--output DIR`"), (
        f"{path.name}: the git_ref item does not say --output DIR — {item!r}"
    )


@pytest.mark.parametrize("path", BOTH)
def test_both_usage_guides_say_an_existing_file_needs_force_in_that_same_item(path: Path):
    """기준(baseline)이 비어 있어 `classify` 가 `conflicted` 를 낸다(`core/artifacts.py`:
    `baseline is None` + 내용이 다름). 이걸 안 적으면 「받았는데 안 써졌다」가 버그로 신고된다 —
    규칙이지 버그가 아니다. **같은 항목 안에** 있어야 한다: 다른 항목의 `--force` 는 tree 모드
    이야기라, 문서 전체를 보면 지워도 초록이 된다."""
    item = item_naming_git_ref(read(path))
    assert item, f"{path.name} §8 has no list item about git_ref"
    assert has(item, r"baseline is empty|기준이\s*\n?\s*비어 있어"), (
        f"{path.name}: the git_ref item never says the baseline is empty — {item!r}"
    )
    assert has(item, r"`conflicted`"), (
        f"{path.name}: the git_ref item does not name the conflicted verdict — {item!r}"
    )
    assert has(item, r"`--force`"), (
        f"{path.name}: the git_ref item does not say --force is what overwrites — {item!r}"
    )


def test_the_changelog_records_that_an_empty_hand_used_to_look_like_a_pass():
    """고친 것이 「빠진 기능」이 아니라 **fail-open** 이었다는 게 이 항목의 값이다."""
    text = unreleased(read(CHANGELOG))
    assert has(text, r"`git_ref`"), "CHANGELOG [Unreleased] lacks the git_ref fetch entry"
    assert has(text, r"--fetch-artifacts"), "CHANGELOG entry never names the flag"
    assert has(text, r"empty hand|nothing was written"), (
        "CHANGELOG entry does not say the failure was silent — that is the point"
    )
