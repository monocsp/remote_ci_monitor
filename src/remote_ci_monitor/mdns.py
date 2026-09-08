"""mDNS/DNS-SD 소켓 쪽(M5c) — 서버의 응답기 스레드와 클라이언트의 질의기.

- 응답기: UDP `0.0.0.0:5353`(SO_REUSEADDR/SO_REUSEPORT — macOS mDNSResponder · avahi 와 공존),
  `224.0.0.251` 가입 — 인터페이스마다(멀티홈 서버: 유선 + Wi-Fi 어느 쪽 질의든 듣는다). 우리
  서비스에 대한 질의에만 답한다. 시작 announce 2회, 종료 goodbye. A 레코드는 질의자에게 가는
  인터페이스의 주소를 맨 앞에 — 노트북은 첫 A 로 붙는다.
- 질의기: 임의 포트에서 PTR 질의를 멀티캐스트하고 timeout 동안 응답을 모은다. 표준 라이브러리만.
- 소켓은 `sock_factory` 로 바꿔 끼울 수 있다(테스트 · 다른 포트).
"""

from __future__ import annotations

import errno
import socket
import struct
import sys
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
    order_ipv4s,
    query_id,
    should_answer,
)

RECV_SIZE = 2048
ANNOUNCE_TIMES = 2
#: 로컬 네트워크로 못 나갈 때의 errno. macOS 는 Local Network 권한이 없는 프로세스의
#: LAN 트래픽을 멀티캐스트든 유니캐스트든 EHOSTUNREACH 로 막는다(launchd 서비스는 기본 거부).
BLOCKED_CODES = frozenset({"EHOSTUNREACH", "ENETUNREACH", "EPERM", "EACCES"})
LOCAL_NETWORK_HINT = (
    "macOS needs Local Network permission for this process "
    "(System Settings > Privacy & Security > Local Network); launchd services are denied by default"
)
SEND_ERROR = "cannot send on this network: "
#: 이만큼 연속으로 실패하면 광고가 실제로 안 되는 것으로 보고 health 에 올린다.
SEND_FAILURES_BEFORE_ERROR = 2


def _multicast_socket(port: int, *, bind_ip: str = "0.0.0.0", loop: bool = True) -> socket.socket:
    """그룹에 가입한 UDP 소켓. 같은 포트를 다른 데몬과 나눠 듣는다. 실패하면 소켓을 닫고 OSError."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind((bind_ip, port))
        # 그룹 가입 — 기본 인터페이스(INADDR_ANY)에 더해 주소가 있는 인터페이스마다 한 번씩.
        # INADDR_ANY 만 가입하면 기본 경로 인터페이스에서만 듣는다(실측: 유선 + Wi-Fi 멀티홈
        # 서버가 Wi-Fi 쪽 질의를 못 들었다). 이미 가입한 인터페이스는 EADDRINUSE — 무시.
        joined = 0
        failure: OSError | None = None
        for iface in ("0.0.0.0", *local_ipv4s()):
            mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton(iface))
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                joined += 1
            except OSError as e:
                failure = e
        if not joined:
            raise failure if failure is not None else OSError("cannot join the multicast group")
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1 if loop else 0)
    except OSError:
        s.close()
        raise
    return s


def _source_ipv4_for(peer_ip: str, port: int = MDNS_PORT) -> str | None:
    """`peer_ip` 로 갈 때 커널이 고르는 출발 주소(라우팅 표 그대로 — 패킷은 나가지 않는다).
    멀티홈 서버가 질의자와 같은 네트워크의 주소를 A 레코드 맨 앞에 싣는 근거. 모르면 None."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        probe.connect((peer_ip, port))
        ip = probe.getsockname()[0]
        return ip or None
    except OSError:
        return None
    finally:
        probe.close()


def _hostname_ipv4s() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return [info[4][0] for info in infos]


def local_ipv4s() -> list[str]:
    """이 머신의 비루프백 IPv4 들(발견 응답의 A 레코드). 순서가 곧 `Found.address` 다 — 기본 경로
    인터페이스(멀티캐스트 그룹으로 나가는 쪽)의 주소가 먼저, 그다음 LAN → Tailscale(100.64/10) →
    링크로컬(`order_ipv4s`). 실패하면 빈 목록."""
    return order_ipv4s(_hostname_ipv4s(), first=_source_ipv4_for(MDNS_GROUP))


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
        route_fn: Callable[[str], str | None] = _source_ipv4_for,
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
        self.route_fn = route_fn  # 질의자 IP → 그쪽으로 나가는 우리 주소(멀티홈: 첫 A 로)
        self.limiter = RateLimiter()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error: str | None = None
        self.answered = 0
        self.send_failures = 0  # 연속 송신 실패 수(성공하면 0)
        self._last_send_code: str | None = None  # 같은 errno 를 로그에 반복하지 않으려고

    # ── 규칙 ────────────────────────────────────────────────────────────────

    def response(self, query_id: int = 0, *, prefer: str | None = None) -> bytes:
        """응답 바이트. `prefer` 가 우리 주소 중 하나면 그것을 첫 A 로(질의자가 닿는 주소)."""
        ips = list(self.ips_fn())
        if prefer and prefer in ips:
            ips = [prefer, *(ip for ip in ips if ip != prefer)]
        return encode_response(
            self.instance, self.host, self.port, ips, self.txt_fn(), query_id=query_id
        )

    def handle(self, packet: bytes, addr: Any = None) -> bytes | None:
        """질의(QR=0)이고 우리 이름을 묻고 속도 상한 안이면 응답 바이트, 아니면 None. 예외는 나가지
        않는다 — 응답기 스레드는 어떤 패킷·콜백에도 죽지 않는다."""
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
        prefer: str | None = None
        if addr:
            try:
                prefer = self.route_fn(addr[0])
            except Exception:  # noqa: BLE001 — 라우팅을 못 알아내면 기본 순서
                prefer = None
        try:
            return self.response(query_id=query_id(packet) if legacy else 0, prefer=prefer)
        except MdnsError as e:
            self.log(f"mdns: cannot build response: {e}")
            return None
        except Exception as e:  # noqa: BLE001 — ips_fn/txt_fn 이 무엇을 던져도 응답기는 산다
            self.log(f"mdns: cannot build response: {type(e).__name__}: {e}")
            return None

    # ── 스레드 ──────────────────────────────────────────────────────────────

    def _send(self, payload: bytes, addr: tuple[str, int] | None = None) -> None:
        """멀티캐스트(기본)나 질의자에게 그대로. 실패는 errno 이름으로 남긴다 — 「OSError」만으로는
        무엇이 막았는지 알 수 없다(실측: macOS launchd 서비스의 EHOSTUNREACH)."""
        if self._sock is None:
            return
        try:
            self._sock.sendto(payload, addr or (MDNS_GROUP, self.mdns_port))
        except OSError as e:
            self._send_failed(e)
            return
        self.send_failures = 0
        self._last_send_code = None
        if self.error is not None and self.error.startswith(SEND_ERROR):
            self.error = None

    def _send_failed(self, e: OSError) -> None:
        code = errno.errorcode.get(e.errno or 0, str(e.errno))
        self.send_failures += 1
        if code != self._last_send_code:
            hint = ""
            if code in BLOCKED_CODES and sys.platform == "darwin":
                hint = f" — {LOCAL_NETWORK_HINT}"
            self.log(f"mdns: send failed: {code}{hint}")
            self._last_send_code = code
        if self.send_failures >= SEND_FAILURES_BEFORE_ERROR:
            self.error = f"{SEND_ERROR}{code}"

    def _loop(self) -> None:
        try:
            self._serve()
        except Exception as e:  # noqa: BLE001 — 스레드가 조용히 죽지 않게: 로그 + error(health 에)
            self.error = f"{type(e).__name__}: {e}"
            self.log(f"mdns: responder stopped — {self.error}")

    def _serve(self) -> None:
        assert self._sock is not None
        self._sock.settimeout(0.2)
        # announce 는 recv 루프 안에서 시각으로 — 시작 직후에도 질의에 바로 답한다(실측: 2초 귀먹음)
        announces_left = ANNOUNCE_TIMES
        next_announce = self.clock()
        while not self._stop.is_set():
            if announces_left and self.clock() >= next_announce:
                self._send(self.response())
                announces_left -= 1
                next_announce = self.clock() + 1.0
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
                self._send(out, addr)
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
