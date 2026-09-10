"""문서 문면 잠금(M5i · PR 3) — 두 눈금 · 사후 측정 · 회계의 나이.

`test_docs_m5g` 와 같은 방식. **약속은 테스트가 잠근다** — 「would free 가 예상 회수량이다」를
문서가 말하지 않으면 하드링크 pack 이 3분의 1을 과장하던 시절의 문장이 남는다.
"""

from __future__ import annotations

import re

from test_docs_m5 import CHANGELOG, has, read, unreleased
from test_docs_m5b import section
from test_docs_m5g import CONFIG, OPERATING, subsection


def test_configuration_says_would_free_is_the_reclaimable_estimate() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"hard.?link", re.I), "하드링크를 말하지 않는다"
    assert has(sec, r"would free"), sec
    assert has(sec, r"reclaim", re.I), sec


def test_configuration_shows_the_new_dry_run_summary_line() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"would free .* from \d+ jobs .*would remain"), sec
    assert not has(sec, r"from \d+ jobs · [\d.]+ GB left"), "옛 문구가 남았다"


def test_configuration_says_the_accounting_has_an_age() -> None:
    sec = section(read(CONFIG), "Retention")
    assert has(sec, r"measured .*ago|how old", re.I), sec


def test_operating_says_a_real_gc_measures_again_after_deleting() -> None:
    sec = subsection(read(OPERATING), "Retention")
    assert has(sec, r"measured again|re-?measured|after (it )?delet", re.I), sec


def test_changelog_explains_what_would_free_means_now() -> None:
    text = unreleased(read(CHANGELOG))
    assert has(text, r"would free"), text
    assert has(text, r"hard.?link", re.I), text
    assert has(text, r"measured .*ago", re.I), text
