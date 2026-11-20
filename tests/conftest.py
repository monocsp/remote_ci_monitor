"""모든 시험의 공통 격리 (M5q §6 · 시나리오 A101).

따라가기는 `$XDG_CACHE_HOME/rcm` 에 받고 `$XDG_CONFIG_HOME/rcm/client-version` 을 읽는다. 시험이
사용자의 진짜 캐시와 한 줄 파일을 건드리지 않게 두 변수를 `tmp_path` 아래로 돌리고, 개발자 셸에
남은 `RCM_CLIENT_VERSION` 과 따라가기 변수, 워커 재실행 시각을 지운다. 따라간 자식의
표지(`_follow_boot`)와 이 프로세스의 따라가기 결과도 시험마다 비운다.

시험 위생 둘(2026-11-20 사건 4 — macOS 의 역방향 DNS 가 변이마다 36초, 진짜 시간 제한을 기다린
M5q 시험이 CI 를 25분 늘렸다):

- 역방향 DNS 를 막는다. `socket.getfqdn(name)` 은 묻지 않고 `name`(빈 값이면 `gethostname()`)을,
  `socket.gethostbyaddr` 는 `socket.herror` 를 돌려준다. 진짜 조회가 필요한 시험은
  `@pytest.mark.allow_reverse_dns`. 세션 범위 픽스처가 서버를 띄우기 전에 걸려야 해서 픽스처가
  아니라 `pytest_configure` 에서 바꾼다.
- `@pytest.mark.slow` 없이 setup · call · teardown 합이 20초(`RCM_TEST_SLOW_SECONDS`)를 넘는 시험이
  있으면 세션 끝에 실패(exit 1)로 만든다. 판정은 `pytest_runtest_logreport` 로 모으므로 xdist
  (`-n`)에서도 워커가 아니라 컨트롤러가 한 번 한다.
"""

from __future__ import annotations

import os
import socket

import pytest

import remote_ci_monitor

#: 표시 없이 넘으면 안 되는 한 시험의 시간(초). CI 의 느린 러너는 env 로 늘릴 수 있다.
SLOW_SECONDS = float(os.environ.get("RCM_TEST_SLOW_SECONDS") or 20)

_REAL_GETFQDN = socket.getfqdn
_REAL_GETHOSTBYADDR = socket.gethostbyaddr
#: 지금 도는 시험이 `allow_reverse_dns` 인가 — 함수 범위 픽스처가 켜고 끈다.
_DNS_ALLOWED = [False]
_DURATIONS: dict[str, float] = {}
_MARKED_SLOW: set[str] = set()


def _getfqdn(name: str = "") -> str:
    if _DNS_ALLOWED[0]:
        return _REAL_GETFQDN(name)
    return name or socket.gethostname()


def _gethostbyaddr(address: str):
    if _DNS_ALLOWED[0]:
        return _REAL_GETHOSTBYADDR(address)
    raise socket.herror(
        1, f"reverse DNS for {address!r} is stubbed in tests — mark the test allow_reverse_dns"
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", f"slow: this test may take more than {SLOW_SECONDS:g}s (tests/conftest.py)"
    )
    config.addinivalue_line(
        "markers", "allow_reverse_dns: real socket.getfqdn / gethostbyaddr (tests/conftest.py)"
    )
    socket.getfqdn = _getfqdn
    socket.gethostbyaddr = _gethostbyaddr


def pytest_unconfigure(config):
    socket.getfqdn = _REAL_GETFQDN
    socket.gethostbyaddr = _REAL_GETHOSTBYADDR


@pytest.fixture(autouse=True)
def _reverse_dns_marker(request):
    _DNS_ALLOWED[0] = request.node.get_closest_marker("allow_reverse_dns") is not None
    yield
    _DNS_ALLOWED[0] = False


def pytest_runtest_logreport(report):
    if report.when not in ("setup", "call", "teardown"):
        return
    _DURATIONS[report.nodeid] = _DURATIONS.get(report.nodeid, 0.0) + report.duration
    if "slow" in report.keywords:
        _MARKED_SLOW.add(report.nodeid)


def _too_slow() -> list[tuple[float, str]]:
    return sorted(
        ((took, nodeid) for nodeid, took in _DURATIONS.items() if took > SLOW_SECONDS),
        reverse=True,
    )


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):  # xdist 워커 — 컨트롤러가 판정한다
        return
    unmarked = [(took, nodeid) for took, nodeid in _too_slow() if nodeid not in _MARKED_SLOW]
    session.config.stash[_UNMARKED] = unmarked
    if unmarked and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


_UNMARKED = pytest.StashKey[list]()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    unmarked = config.stash.get(_UNMARKED, [])
    if not unmarked:
        return
    terminalreporter.section(
        f"tests over {SLOW_SECONDS:g}s without @pytest.mark.slow", sep="=", red=True, bold=True
    )
    for took, nodeid in unmarked:
        terminalreporter.write_line(f"{took:7.1f}s  {nodeid}")
    terminalreporter.write_line(
        "Make the test quicker (fake the clock, stub the network) or mark it @pytest.mark.slow."
    )


FOLLOW_ENV = (
    "RCM_CLIENT_VERSION",
    "RCM_FOLLOWED",
    "RCM_FOLLOWED_PATH",
    "RCM_FOLLOWED_FROM",
    "RCM_FOLLOW_FROM",
    "RCM_FOLLOW_FOUND",
    "RCM_WORKER_RESTARTED_AT",  # 드레인 재실행의 고리 막기(PR B2)
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
    # 워커가 exec 직전에 프로브를 닫는다(M5q B2). 한 시험이 닫은 것이 다른 시험의 프로브를 막지
    # 않게 시험마다 되돌린다
    from remote_ci_monitor import hostsample

    if hasattr(hostsample, "_PROBES_CLOSED"):
        monkeypatch.setattr(hostsample, "_PROBES_CLOSED", False)
    yield
