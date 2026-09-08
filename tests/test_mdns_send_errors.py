"""응답기의 송신 실패 진단 — errno 를 이름으로 남기고 health 에 올린다.

실측 배경(2026-09-08, 실배치): macOS 는 Local Network 권한이 없는 프로세스의 LAN 트래픽을
멀티캐스트든 유니캐스트든 `EHOSTUNREACH` 로 막는다. launchd 서비스는 그 권한이 기본으로 없다.
그때 로그에 「mdns: send failed: OSError」만 남으면 무엇이 막았는지 알 수 없고, `/api/health` 는
광고가 켜져 있다고(`on: true`) 말한다 — 실제로는 아무도 서버를 못 찾는데.

잠그는 모양:
- 로그 줄은 errno **이름**(`EHOSTUNREACH`)을 담는다. 같은 errno 는 한 번만 적는다(폭주 방지).
- macOS 에서 막힌 errno 면 그 줄이 Local Network 권한을 지목한다.
- 연속 2회 실패하면 `responder.error` 가 서고, `/api/health.advertise.on` 이 false 가 된다.
- 다시 성공하면 그 오류는 사라진다(일시적인 네트워크 변경).
"""

from __future__ import annotations

import errno
import itertools
import os
import time

import pytest

from remote_ci_monitor.mdns import (
    LOCAL_NETWORK_HINT,
    SEND_ERROR,
    SEND_FAILURES_BEFORE_ERROR,
    Responder,
)

INSTANCE = "macmini._rcm._tcp.local."
HOST = "macmini.local."
PORT = 8787


class Blocked:
    """sendto 가 정해진 errno 로 실패하는 가짜 소켓. `ok_after` 번째부터는 성공한다."""

    def __init__(self, code: int = errno.EHOSTUNREACH, *, ok_after: int | None = None):
        self.code = code
        self.ok_after = ok_after
        self.attempts = 0
        self.sent = 0

    def settimeout(self, _t: float) -> None:
        pass

    def sendto(self, payload: bytes, _addr: tuple[str, int]) -> int:
        self.attempts += 1
        if self.ok_after is not None and self.attempts > self.ok_after:
            self.sent += 1
            return len(payload)
        raise OSError(self.code, os.strerror(self.code))

    def recvfrom(self, _n: int) -> tuple[bytes, tuple[str, int]]:
        raise TimeoutError

    def close(self) -> None:
        pass


def responder(sock: Blocked, logs: list[str], platform: str = "darwin") -> Responder:
    """시계를 빨리 감아 announce 두 번이 곧바로 나가게 한다(테스트가 1초를 기다리지 않게)."""
    ticks = itertools.count(0.0, 0.7)
    return Responder(
        INSTANCE,
        HOST,
        PORT,
        ips_fn=lambda: ["192.168.0.10"],
        txt_fn=lambda: {"v": "0.2.2"},
        log=logs.append,
        sock_factory=lambda: sock,
        clock=lambda: next(ticks),
    )


def run_until(sock: Blocked, attempts: int, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while sock.attempts < attempts and time.monotonic() < deadline:
        time.sleep(0.01)


@pytest.fixture
def darwin(monkeypatch):
    monkeypatch.setattr("remote_ci_monitor.mdns.sys.platform", "darwin")


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr("remote_ci_monitor.mdns.sys.platform", "linux")


def test_send_failure_names_the_errno_instead_of_just_oserror(darwin):
    logs: list[str] = []
    sock = Blocked()
    r = responder(sock, logs)
    assert r.start()
    run_until(sock, 1)
    r.stop()
    failed = [ln for ln in logs if "send failed" in ln]
    assert failed, logs
    assert "EHOSTUNREACH" in failed[0], failed
    assert "OSError" not in failed[0], failed


def test_the_same_errno_is_logged_once_not_once_per_packet(darwin):
    logs: list[str] = []
    sock = Blocked()
    r = responder(sock, logs)
    assert r.start()
    run_until(sock, 5)
    r.stop()
    assert len([ln for ln in logs if "send failed" in ln]) == 1, logs


def test_on_macos_the_line_points_at_the_local_network_permission(darwin):
    logs: list[str] = []
    sock = Blocked()
    r = responder(sock, logs)
    assert r.start()
    run_until(sock, 1)
    r.stop()
    line = next(ln for ln in logs if "send failed" in ln)
    assert LOCAL_NETWORK_HINT in line, line
    assert "Local Network" in line


def test_elsewhere_the_line_is_just_the_errno(linux):
    logs: list[str] = []
    sock = Blocked()
    r = responder(sock, logs)
    assert r.start()
    run_until(sock, 1)
    r.stop()
    line = next(ln for ln in logs if "send failed" in ln)
    assert "EHOSTUNREACH" in line
    assert "Local Network" not in line


def test_repeated_failures_raise_an_error_so_health_stops_claiming_advertise(darwin):
    logs: list[str] = []
    sock = Blocked()
    r = responder(sock, logs)
    assert r.start()
    run_until(sock, SEND_FAILURES_BEFORE_ERROR)
    deadline = time.monotonic() + 2.0
    while r.error is None and time.monotonic() < deadline:
        time.sleep(0.01)
    r.stop()
    assert r.error is not None
    assert r.error.startswith(SEND_ERROR), r.error
    assert "EHOSTUNREACH" in r.error, r.error


def test_one_failure_alone_is_not_an_error(darwin):
    logs: list[str] = []
    sock = Blocked(ok_after=1)  # 1회 실패 뒤 성공
    r = responder(sock, logs)
    r._sock = sock
    r._send(b"x" * 12)
    assert r.send_failures == 1
    assert r.error is None


def test_a_later_success_clears_the_error(darwin):
    logs: list[str] = []
    sock = Blocked(ok_after=SEND_FAILURES_BEFORE_ERROR)
    r = responder(sock, logs)
    r._sock = sock
    for _ in range(SEND_FAILURES_BEFORE_ERROR):
        r._send(b"x" * 12)
    assert r.error is not None
    r._send(b"x" * 12)
    assert sock.sent == 1
    assert r.error is None
    assert r.send_failures == 0


def test_a_permission_denied_errno_is_reported_the_same_way(darwin):
    logs: list[str] = []
    sock = Blocked(code=errno.EPERM)
    r = responder(sock, logs)
    r._sock = sock
    r._send(b"x" * 12)
    r._send(b"x" * 12)
    assert "EPERM" in r.error, r.error
    assert LOCAL_NETWORK_HINT in logs[0], logs
