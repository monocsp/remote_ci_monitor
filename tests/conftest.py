"""모든 시험의 공통 격리 (M5q §6 · 시나리오 A101).

따라가기는 `$XDG_CACHE_HOME/rcm` 에 받고 `$XDG_CONFIG_HOME/rcm/client-version` 을 읽는다. 시험이
사용자의 진짜 캐시와 한 줄 파일을 건드리지 않게 두 변수를 `tmp_path` 아래로 돌리고, 개발자 셸에
남은 `RCM_CLIENT_VERSION` 과 따라가기 변수를 지운다. 따라간 자식의 표지(`_follow_boot`)와 이
프로세스의 따라가기 결과도 시험마다 비운다.
"""

from __future__ import annotations

import pytest

import remote_ci_monitor

FOLLOW_ENV = (
    "RCM_CLIENT_VERSION",
    "RCM_FOLLOWED",
    "RCM_FOLLOWED_PATH",
    "RCM_FOLLOWED_FROM",
    "RCM_FOLLOW_FROM",
    "RCM_FOLLOW_FOUND",
)


@pytest.fixture(autouse=True)
def _follow_isolation(monkeypatch, tmp_path_factory):
    base = tmp_path_factory.mktemp("xdg")
    monkeypatch.setenv("XDG_CACHE_HOME", str(base / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base / "config"))
    for name in FOLLOW_ENV:
        monkeypatch.delenv(name, raising=False)
    if hasattr(remote_ci_monitor, "_follow_boot"):
        monkeypatch.delattr(remote_ci_monitor, "_follow_boot")
    try:
        from remote_ci_monitor import follow
    except ImportError:  # 따라가기 모듈이 없는 판(뮤테이션 사본의 옛 코드)
        follow = None
    if follow is not None and hasattr(follow, "_STATE"):
        monkeypatch.setattr(follow, "_STATE", None)
    yield
