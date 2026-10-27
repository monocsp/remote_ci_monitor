"""잡 링크 검증 — 서버(`POST /jobs` 의 `link`)와 CLI(`rcm run --link`)가 같은 규칙을 쓴다.

rcm 은 GitHub 을 모른다(PLAN 결정 30). 호출자가 PR 같은 페이지의 주소를 알고, rcm 은 그 주소를
**링크 하나**로 저장해 돌려줄 뿐이다. 그래서 여기서 보는 것은 「브라우저에 `<a href>` 로 넣어도
안전한가」 하나다: `http://`·`https://` 절대 주소만, 500자 이하, 공백·제어문자 없음. 라벨은 40자
이하이고 제어문자가 없다. 웹은 받은 것을 다시 검사한다(심층 방어 — `app.js` `jobLinkHtml`).

메시지는 사용자에게 그대로 보여도 되는 영어 한 문장이고 어느 칸인지(`link.url` · `link.label`)를
말한다. 서버는 400 으로, CLI 는 `--link` 이름을 앞에 붙여 사용 오류(exit 2)로 낸다.
"""

from __future__ import annotations

import unicodedata
import urllib.parse
from typing import Any

from remote_ci_monitor.core.model import JobLink

LINK_URL_MAX = 500
LINK_LABEL_MAX = 40
_SCHEMES = ("http://", "https://")
#: 「제어문자」로 보는 유니코드 범주 — C0·C1·DEL(Cc), 보이지 않는 서식 문자(Cf: 방향 바꾸기
#: U+202E 처럼 글을 뒤집어 보이게 하는 것), 줄·문단 구분자(Zl·Zp).
_CONTROL = frozenset({"Cc", "Cf", "Zl", "Zp"})


class LinkError(ValueError):
    """링크가 계약에 맞지 않는다. 메시지는 사용자에게 그대로 보여도 된다."""


def _has_control(text: str) -> bool:
    return any(unicodedata.category(ch) in _CONTROL for ch in text)


def parse_link(raw: Any) -> JobLink | None:
    """`{"url": …, "label": …}` 를 검증해 `JobLink` 로. None 이면 링크가 없는 것이다."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LinkError('link must be an object: {"url": "https://…", "label": "…"}')
    url = raw.get("url")
    if url is None:
        raise LinkError("link.url is required when link is given")
    if not isinstance(url, str):
        raise LinkError("link.url must be a string")
    if len(url) > LINK_URL_MAX:
        raise LinkError(f"link.url must be at most {LINK_URL_MAX} characters")
    if any(ch.isspace() for ch in url) or _has_control(url):
        raise LinkError("link.url must not contain spaces or control characters")
    if not url.startswith(_SCHEMES):
        raise LinkError("link.url must start with http:// or https://")
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        host = None
    if not host:
        raise LinkError("link.url must be an absolute http(s) URL with a host")
    label = raw.get("label")
    if label is not None and not isinstance(label, str):
        raise LinkError("link.label must be a string")
    if label:
        if len(label) > LINK_LABEL_MAX:
            raise LinkError(f"link.label must be at most {LINK_LABEL_MAX} characters")
        if _has_control(label):
            raise LinkError("link.label must not contain control characters")
    return JobLink(url=url, label=label or None)
