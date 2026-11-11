"""설정을 다시 읽은 결과를 한 줄로 — 로그와 `rcm reload` 가 같은 문장을 쓴다(M5m).

「다시 읽었습니다」만으로는 뭐가 들어갔는지 확인하러 또 물어야 한다. 그 왕복을 없애는 게 이
기능의 요점이라, 요약은 **바뀐 것의 이름**을 댄다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: 출력 순서 — 들어온 것, 바뀐 것, 빠진 것. 「없어졌다」가 맨 뒤라 눈에 띈다.
_ORDER: tuple[tuple[str, str], ...] = (
    ("presets_added", "preset added"),
    ("presets_changed", "preset changed"),
    ("presets_removed", "preset removed"),
    ("repos_added", "repo added"),
    ("repos_changed", "repo changed"),
    ("repos_removed", "repo removed"),
)


def reload_summary(changed: Mapping[str, Sequence[str]]) -> str:
    """`{"presets_added": ["ipf-verify"], …}` → `preset added ipf-verify`.

    아무것도 안 바뀌었으면 그렇게 말한다 — 파일을 고쳤다고 믿는 사람에게 그게 답이다(고친
    파일이 서버가 읽는 파일이 아니었거나, 고친 구역이 다시 읽히지 않는 구역이다).
    """
    parts = [
        f"{label} {', '.join(names)}"
        for key, label in _ORDER
        if (names := list(changed.get(key) or []))
    ]
    return " · ".join(parts) if parts else "nothing changed"
