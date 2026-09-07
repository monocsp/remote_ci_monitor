"""mDNS/DNS-SD 소켓 쪽(M5c) — 서버의 응답기 스레드와 클라이언트의 질의기.

- 응답기: UDP `0.0.0.0:5353`(SO_REUSEADDR/SO_REUSEPORT — macOS mDNSResponder · avahi 와 공존),
  `224.0.0.251` 가입. 우리 서비스에 대한 질의에만 멀티캐스트로 답한다. 시작 announce 2회,
  종료 goodbye.
- 질의기: 임의 포트에서 PTR 질의를 멀티캐스트하고 timeout 동안 응답을 모은다. 표준 라이브러리만.
- 소켓은 `sock_factory` 로 바꿔 끼울 수 있다(테스트 · 다른 포트).
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from remote_ci_monitor.core.mdns import (
    MDNS_GROUP,
    MDNS_PORT,
    SERVICE,
    TYPE_PTR,
    Found,
    MdnsError,
    RateLimiter,
    decode,
    encode_goodbye,
    encode_query,
    encode_response,
    found_from_records,
    is_query,
    query_id,
    should_answer,
)

RECV_SIZE = 2048
ANNOUNCE_TIMES = 2


def _multicast_socket(port: int, *, bind_ip: str = "0.0.0.0", loop: bool = True) -> socket.socket:
    """그룹에 가입한 UDP 소켓. 같은 포트를 다른 데몬과 나눠 듣는다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    s.bind((bind_ip, port))
    mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0"))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if loop else 0)
    return s


def local_ipv4s() -> list[str]:
    """이 머신의 비루프백 IPv4 들(발견 응답의 A 레코드). 실패하면 빈 목록."""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    # 기본 경로의 주소도 하나 더(호스트명이 루프백에만 묶인 머신)
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((MDNS_GROUP, MDNS_PORT))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        finally:
            probe.close()
    except OSError:
        pass
    return ips


class Responder:
    """서버 쪽 광고. `handle` 은 소켓 없이도 시험할 수 있다(패킷 → 응답 바이트 또는 None)."""

    def __init__(
        self,
        instance: str,
        host: str,
        port: int,
        *,
        ips_fn: Callable[[], list[str]] = local_ipv4s,
        txt_fn: Callable[[], dict[str, str]] | None = None,
        log: Callable[[str], None] | None = None,
        mdns_port: int = MDNS_PORT,
        sock_factory: Callable[[], socket.socket] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.instance = instance
        self.host = host
        self.port = port
        self.ips_fn = ips_fn
        self.txt_fn = txt_fn or (lambda: {})
        self.log = log or (lambda msg: None)
        self.mdns_port = mdns_port
        self.sock_factory = sock_factory or (lambda: _multicast_socket(mdns_port))
        self.clock = clock
        self.limiter = RateLimiter()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error: str | None = None
        self.answered = 0

    # ── 규칙 ────────────────────────────────────────────────────────────────

    def response(self, query_id: int = 0) -> bytes:
        return encode_response(
            self.instance, self.host, self.port, self.ips_fn(), self.txt_fn(), query_id=query_id
        )

    def handle(self, packet: bytes, addr: Any = None) -> bytes | None:
        """질의(QR=0)이고 우리 이름을 묻고 속도 상한 안이면 응답 바이트, 아니면 None."""
        if not is_query(packet):
            return None
        try:
            questions, _ = decode(packet)
        except MdnsError:
            return None
        if not should_answer(questions, self.instance, self.host):
            return None
        if not self.limiter.allow(self.clock()):
            return None
        self.answered += 1
        # 5353 이 아닌 포트에서 온 질의는 legacy unicast — 응답에 질의 id 를 되돌려 준다
        # (RFC 6762 §6.7)
        legacy = bool(addr) and addr[1] != self.mdns_port
        try:
            return self.response(query_id=query_id(packet) if legacy else 0)
        except MdnsError as e:
            self.log(f"mdns: cannot build response: {e}")
            return None

    # ── 스레드 ──────────────────────────────────────────────────────────────

    def _send(self, payload: bytes) -> None:
        if self._sock is None:
            return
        try:
            self._sock.sendto(payload, (MDNS_GROUP, self.mdns_port))
        except OSError as e:
            self.log(f"mdns: send failed: {type(e).__name__}")

    def _loop(self) -> None:
        assert self._sock is not None
        self._sock.settimeout(0.5)
        for _ in range(ANNOUNCE_TIMES):
            self._send(self.response())
            if self._stop.wait(1.0):
                break
        while not self._stop.is_set():
            try:
                packet, addr = self._sock.recvfrom(RECV_SIZE)
            except TimeoutError:
                continue
            except OSError:
                break
            out = self.handle(packet, addr)
            if out is None:
                continue
            if addr and addr[1] != self.mdns_port:  # legacy unicast: 물어본 그 주소로
                try:
                    self._sock.sendto(out, addr)
                except OSError as e:
                    self.log(f"mdns: unicast reply failed: {type(e).__name__}")
            else:
                self._send(out)

    def start(self) -> bool:
        """소켓을 열고 스레드를 띄운다. 못 열면 False + `error`(발견은 부가 기능 — 서버는 계속)."""
        try:
            self._sock = self.sock_factory()
        except OSError as e:
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"mdns: advertise off — {self.error}")
            return False
        self._thread = threading.Thread(target=self._loop, name="rcm-mdns", daemon=True)
        self._thread.start()
        self.log(f"mdns: advertising {self.instance} on port {self.port}")
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.sendto(
                    encode_goodbye(self.instance, self.host, self.port),
                    (MDNS_GROUP, self.mdns_port),
                )
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


def discover(
    timeout: float = 1.5,
    *,
    sock_factory: Callable[[], socket.socket] | None = None,
    clock: Callable[[], float] = time.monotonic,
    mdns_port: int = MDNS_PORT,
) -> list[Found]:
    """같은 네트워크의 rcm 서버들. PTR 질의를 두 번(0 · 0.4초) 보내고 timeout 동안 응답을 모은다.
    소켓을 못 열면 빈 목록(발견은 부가 기능)."""
    try:
        sock = sock_factory() if sock_factory else _querier_socket()
    except OSError:
        return []
    found: dict[tuple[str, int], Found] = {}
    query = encode_query([(SERVICE, TYPE_PTR)])
    deadline = clock() + timeout
    next_send = clock()
    sends = 0
    try:
        while True:
            now = clock()
            if sends < 2 and now >= next_send:
                try:
                    sock.sendto(query, (MDNS_GROUP, mdns_port))
                except OSError:
                    break
                sends += 1
                next_send = now + 0.4
            remaining = deadline - now
            if remaining <= 0:
                break
            try:
                sock.settimeout(min(0.2, remaining))
                packet, _addr = sock.recvfrom(RECV_SIZE)
            except TimeoutError:
                continue
            except OSError:
                break
            if not packet or is_query(packet):
                continue
            try:
                _, records = decode(packet)
            except MdnsError:
                continue
            for f in found_from_records(records):
                key = (f.name, f.port)
                if key not in found or (not found[key].ips and f.ips):
                    found[key] = f
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return sorted(found.values(), key=lambda f: (f.name, f.port))


def _querier_socket() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.bind(("0.0.0.0", 0))
    return s


__all__ = ["Responder", "discover", "local_ipv4s"]
