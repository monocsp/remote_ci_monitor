"""웹 카탈로그가 보관함 이유 코드를 두 언어에서 하나씩 **정의**했는가(M5p P3 · CG34).

`core/shelf.SHELF_REASON_CODES` 를 직접 읽고 `web/i18n.js` 의 en · ko 영역에서 `shelf.reason.<code>`
정의를 각각 센다. 파일 어딘가에 문자열이 있는지만 보면 주석에 남긴 글자도 통과한다 —
`tests/test_outcome.py::_catalogue` 와 같은 방식이다. **구현보다 먼저 썼다(test-first).**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remote_ci_monitor.core.shelf import SHELF_REASON_CODES

_PROP_RE = re.compile(r'^ {4}"shelf\.reason\.([a-z_]+)"\s*:', re.M)


def _catalogue(locale: str) -> list[str]:
    src = (Path(__file__).parents[1] / "src/remote_ci_monitor/web/i18n.js").read_text("utf-8")
    en, mark, ko = src.partition("  var KO = {")
    assert mark and "  var EN = {" in en, "i18n.js 의 구조가 바뀌었다 — 이 시험을 고쳐야 한다"
    return _PROP_RE.findall(en if locale == "en" else ko)


@pytest.mark.parametrize("locale", ["en", "ko"])
def test_every_shelf_reason_code_is_defined_once_in_each_locale(locale):
    found = _catalogue(locale)
    assert len(found) == len(set(found)), f"{locale}: 같은 코드를 두 번 정의했다"
    assert set(found) == set(SHELF_REASON_CODES), (
        f"{locale}: 없는 것 {sorted(set(SHELF_REASON_CODES) - set(found))}, "
        f"남는 것 {sorted(set(found) - set(SHELF_REASON_CODES))}"
    )


def test_the_lock_ignores_a_code_left_in_a_comment():
    assert _PROP_RE.findall('    "shelf.reason.shelf_full": "x",') == ["shelf_full"]
    assert _PROP_RE.findall('    // "shelf.reason.shelf_full": 옛 코드') == []
