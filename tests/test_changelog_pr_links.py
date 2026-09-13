"""CHANGELOG 의 PR 링크는 글자와 주소가 같은 PR 을 가리켜야 한다(M5i 검증 V7.2).

2026-09-10 검증에서 회계 항목 둘이 `[#84](…/pull/88)` 이었다 — 글자는 PR 1b(호스트 표본), 주소는
PR 3(회계). 항목마다 「PR 링크와 함께」가 약속인데, 글자가 다른 PR 을 말하면 읽는 쪽은 잘못된 PR 로
간다. 링크 하나하나를 잠근다 — 글자의 번호와 `pull/<N>` 의 번호가 같아야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
PR_LINK = re.compile(r"\[#(\d+)\]\(https://github\.com/monocsp/remote_ci_monitor/pull/(\d+)\)")


def test_every_pr_link_names_the_pr_it_points_at() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    links = list(PR_LINK.finditer(text))
    assert links, "CHANGELOG 에 PR 링크가 하나도 없다 — 정규식이 문서 형식과 어긋났나"
    wrong = [
        f"line {text[: m.start()].count(chr(10)) + 1}: [#{m.group(1)}] → pull/{m.group(2)}"
        for m in links
        if m.group(1) != m.group(2)
    ]
    assert wrong == [], "링크 글자와 주소의 PR 번호가 다르다:\n" + "\n".join(wrong)
