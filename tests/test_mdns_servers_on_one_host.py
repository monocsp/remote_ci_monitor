"""같은 호스트의 rcm 서버 여럿을 `rcm discover` 가 모두 본다(qa-1, 2026-11-26).

관찰: 운영 서버(광고 켬)가 도는 Mac 에서 시험 서버 둘을 더 띄우면 `dns-sd -B` 는 셋을 보는데
`rcm discover` 는 운영 서버 하나만 봤다. 원인: 그 Mac 에서 터미널로 띄운 서버 프로세스는 LAN
인터페이스(en0)로 들어오는 멀티캐스트를 받지 못한다 — 같은 프로세스가 보낸 질의의 루프 사본조차
오지 않는다. 루프백 인터페이스(lo0)의 멀티캐스트는 온다. 질의기는 기본 인터페이스로만 묻고 응답기는
LAN 쪽에만 가입해 있어서, 같은 호스트에서도 그 서버들은 질의를 한 번도 듣지 못했다(answered 0).
announce 는 나가니 `dns-sd` 의 캐시에는 남는다.

고침: 질의기는 같은 질의를 루프백 인터페이스로 한 번 더 보내고, 응답기는 루프백에도 가입한다.

시험은 두 갈래다.
- 진짜 소켓: 포트는 OS 가 고른 빈 포트(고정 포트를 나눠 듣지 않는다 — 병렬 변이가 서로의 응답을
  세지 않게). 「LAN 을 못 듣는 서버」는 루프백에만 가입한 소켓으로 흉내 낸다. 질의기의 TTL 은 0 이라
  패킷이 이 호스트 밖으로 나가지 않는다. 이 호스트에서 루프백 멀티캐스트가 안 되면 skip.
- 가짜 소켓: 다른 호스트의 응답 · 깨진 응답 · 루프백을 못 쓸 때 · 인터페이스 되돌림.
"""

from __future__ import annotations

import errno
import socket
import struct
from collections.abc import Iterator

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor import mdns as mdns_mod
from remote_ci_monitor.core.mdns import (
    MDNS_GROUP,
    SERVICE,
    TYPE_PTR,
    Found,
    encode_query,
    encode_response,
    instance_name,
)
from remote_ci_monitor.mdns import Responder, discover

LO = "127.0.0.1"
LO_PACKED = socket.inet_aton(LO)
ANY_PACKED = socket.inet_aton("0.0.0.0")
IPS = ("192.0.2.10",)  # TEST-NET-1 — 응답에 그대로 실려 오는지만 본다


# ── 진짜 소켓 도우미 ─────────────────────────────────────────────────────────


def _reusable() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    return s


def port_holder() -> socket.socket:
    """OS 가 고른 빈 포트를 잡아 두는 소켓(그룹 미가입). 응답기들이 같은 포트를 나눠 듣는다."""
    s = _reusable()
    s.bind(("0.0.0.0", 0))
    return s


def loopback_member(port: int) -> socket.socket:
    """루프백에만 가입한 그룹 소켓 — LAN 인터페이스로 오는 멀티캐스트를 받지 못하는 서버의 모양."""
    s = _reusable()
    try:
        s.bind(("0.0.0.0", port))
        mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), LO_PACKED)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, LO_PACKED)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
    except OSError:
        s.close()
        raise
    return s


def host_only_querier() -> socket.socket:
    """`rcm discover` 의 질의기 모양(임의 포트 · 그룹 미가입 · 기본 인터페이스) — TTL 0 이라
    질의가 이 호스트 밖으로 나가지 않는다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 0)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.bind(("0.0.0.0", 0))
    return s


def _loopback_multicast_reason() -> str | None:
    """루프백 멀티캐스트가 안 되는 이유(되면 None). 0.5초 안에 끝난다."""
    holder = port_holder()
    port = holder.getsockname()[1]
    try:
        try:
            s = loopback_member(port)
        except OSError as e:
            return f"cannot join {MDNS_GROUP} on {LO}: {e}"
        try:
            s.settimeout(0.5)
            s.sendto(b"rcm-probe", (MDNS_GROUP, port))
            data, _ = s.recvfrom(64)
        except TimeoutError:
            return "loopback multicast is not delivered on this host"
        except OSError as e:
            return f"loopback multicast send/recv failed: {e}"
        finally:
            s.close()
        return None if data == b"rcm-probe" else "loopback multicast delivered unexpected data"
    finally:
        holder.close()


SKIP_REAL = _loopback_multicast_reason()
real_sockets = pytest.mark.skipif(SKIP_REAL is not None, reason=SKIP_REAL or "")


@pytest.fixture
def shared_port() -> Iterator[int]:
    holder = port_holder()
    try:
        yield holder.getsockname()[1]
    finally:
        holder.close()


@pytest.fixture
def responders() -> Iterator[list[Responder]]:
    started: list[Responder] = []
    try:
        yield started
    finally:
        for r in started:
            r.stop()


def start_on_loopback(
    responders: list[Responder], port: int, name: str, server_port: int
) -> Responder:
    r = Responder(
        instance_name(name),
        "samehost.local.",  # 셋 모두 같은 호스트 이름(관찰 그대로)
        server_port,
        ips_fn=lambda: list(IPS),
        txt_fn=lambda: {"v": __version__, "name": name, "lanes": "1"},
        mdns_port=port,
        sock_factory=lambda: loopback_member(port),
        route_fn=lambda _ip: None,
    )
    assert r.start() is True, r.error
    responders.append(r)
    return r


# ── 진짜 소켓 ────────────────────────────────────────────────────────────────


@real_sockets
def test_md1_three_servers_on_one_host_are_all_found(
    responders: list[Responder], shared_port: int
) -> None:
    """MD1: 같은 호스트의 서버 셋(LAN 멀티캐스트를 못 듣는다 — 루프백에만 가입) → `discover` 가
    기본 인터페이스의 질의기로도 셋을 모두 본다(이름순)."""
    for name, port in (("rcm-md-a", 8794), ("rcm-md-b", 8795), ("rcm-md-c", 8796)):
        start_on_loopback(responders, shared_port, name, port)
    q = host_only_querier()
    found = discover(timeout=1.0, mdns_port=shared_port, sock_factory=lambda: q)
    assert [(f.name, f.port) for f in found] == [
        ("rcm-md-a", 8794),
        ("rcm-md-b", 8795),
        ("rcm-md-c", 8796),
    ], [r.answered for r in responders]


@real_sockets
def test_md2_the_responder_socket_also_listens_on_the_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MD2: 응답기가 실제로 여는 소켓(`_multicast_socket`)은 루프백 인터페이스로 온 질의도
    듣는다 — LAN 쪽을 못 듣는 프로세스라도 같은 호스트의 질의기에는 답한다.

    macOS 에서 빨강 — Linux 는 가입 없이도 받을 수 있어 잠그지 못한다(`IP_MULTICAST_ALL` 기본값이
    켜져 있어 다른 소켓의 가입만으로도 그룹 패킷이 온다). rcm/ci 는 macOS 에서 돈다."""
    monkeypatch.setattr(mdns_mod, "local_ipv4s", lambda: [])  # 호스트 이름 해석 없이
    m = mdns_mod._multicast_socket(0)
    port = m.getsockname()[1]
    sender = loopback_member(port)
    try:
        m.settimeout(0.5)
        query = encode_query([(SERVICE, TYPE_PTR)])
        sender.sendto(query, (MDNS_GROUP, port))
        heard = []
        try:
            while True:
                data, _ = m.recvfrom(2048)
                heard.append(data)
                if data == query:
                    break
        except TimeoutError:
            pass
        assert query in heard
    finally:
        sender.close()
        m.close()


# ── 가짜 소켓 ────────────────────────────────────────────────────────────────


class FakeClock:
    """부를 때마다 0.05초씩 간다 — 바쁜 대기 없이 마감까지 돈다."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 0.05
        return self.t


class FakeSock:
    """`discover` 가 쓰는 소켓 면만. `sends` 는 (보낼 때의 멀티캐스트 인터페이스, 목적지)."""

    def __init__(
        self,
        replies: list[tuple[bytes, tuple[str, int]]],
        *,
        iface: bytes = ANY_PACKED,
        loopback_fails: bool = False,
        lan_fails: bool = False,
    ) -> None:
        self.replies = list(replies)
        self.iface = iface
        self.loopback_fails = loopback_fails
        self.lan_fails = lan_fails
        self.sends: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def getsockopt(self, level: int, opt: int, buflen: int = 0) -> bytes:
        assert (level, opt) == (socket.IPPROTO_IP, socket.IP_MULTICAST_IF)
        return self.iface

    def setsockopt(self, level: int, opt: int, value: bytes) -> None:
        assert (level, opt) == (socket.IPPROTO_IP, socket.IP_MULTICAST_IF)
        if self.loopback_fails and value == LO_PACKED:
            raise OSError(errno.EADDRNOTAVAIL, "no multicast on the loopback")
        self.iface = value

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        if self.lan_fails and self.iface != LO_PACKED:
            raise OSError(errno.ENETUNREACH, "Network is unreachable")
        self.sends.append((self.iface, addr))
        return len(data)

    def settimeout(self, _t: float) -> None:
        pass

    def recvfrom(self, _n: int) -> tuple[bytes, tuple[str, int]]:
        if self.replies and self.sends:
            return self.replies.pop(0)
        raise TimeoutError

    def close(self) -> None:
        self.closed = True


def answer(name: str, host: str, port: int, ips: list[str]) -> bytes:
    return encode_response(
        instance_name(name), host, port, ips, {"v": __version__, "name": name, "lanes": "2"}
    )


def run(sock: FakeSock) -> list[Found]:
    return discover(timeout=1.0, sock_factory=lambda: sock, clock=FakeClock())


DEST = (MDNS_GROUP, 5353)


def test_md3_a_server_on_another_host_is_still_found_and_the_lan_is_still_asked() -> None:
    """MD3: 다른 호스트의 서버(LAN 으로 온 답)와 같은 호스트의 서버(루프백으로 온 답)가 둘 다
    보이고, 질의는 매번 원래 인터페이스(LAN)로 먼저 나간다 — 루프백만 묻게 바뀌면 다른 컴퓨터의
    서버가 사라진다."""
    sock = FakeSock(
        [
            (answer("buildbox", "buildbox.local.", 8788, ["192.0.2.20"]), ("192.0.2.20", 5353)),
            (answer("macmini", "macmini.local.", 8787, ["192.0.2.10"]), (LO, 5353)),
        ]
    )
    found = run(sock)
    assert [(f.name, f.ips) for f in found] == [
        ("buildbox", ("192.0.2.20",)),
        ("macmini", ("192.0.2.10",)),
    ]
    assert [iface for iface, _ in sock.sends].count(ANY_PACKED) == 2


def test_md4_one_server_on_a_loopback_querier_is_asked_once_per_round_as_before() -> None:
    """MD4: 서버 하나 · 이미 루프백 인터페이스인 질의기(기존 루프백 시험의 모양) → 예전과 같다 —
    한 차례에 질의 하나, 결과는 그 서버 하나."""
    sock = FakeSock(
        [(answer("macmini", "macmini.local.", 8787, ["192.0.2.10"]), (LO, 5353))] * 2,
        iface=LO_PACKED,
    )
    found = run(sock)
    assert found == [
        Found("macmini", "macmini.local.", 8787, ("192.0.2.10",), __version__, 2),
    ]
    assert sock.sends == [(LO_PACKED, DEST), (LO_PACKED, DEST)]


def test_md5_a_broken_reply_is_ignored_and_the_rest_are_kept() -> None:
    """MD5: 잘린 패킷 · 쓰레기 · 남의 질의는 건너뛰고, 그 뒤의 정상 답은 받는다."""
    good = answer("rcm-md-a", "samehost.local.", 8794, ["192.0.2.10"])
    sock = FakeSock(
        [
            (good[:20], (LO, 5353)),
            (b"\xff" * 40, (LO, 5353)),
            (encode_query([(SERVICE, TYPE_PTR)]), ("192.0.2.30", 5353)),
            (good, (LO, 5353)),
        ]
    )
    assert [(f.name, f.port) for f in run(sock)] == [("rcm-md-a", 8794)]


def test_md6_without_a_lan_route_the_loopback_is_still_asked() -> None:
    """MD6: 기본 인터페이스로 못 보낼 때(네트워크 없음 · ENETUNREACH)도 루프백 질의는 나가고 같은
    호스트의 서버가 보인다 — 예전에는 첫 송신 실패에서 바로 빈 목록이었다."""
    sock = FakeSock(
        [(answer("rcm-md-a", "samehost.local.", 8794, ["192.0.2.10"]), (LO, 5353))],
        lan_fails=True,
    )
    assert [(f.name, f.port) for f in run(sock)] == [("rcm-md-a", 8794)]
    assert sock.sends and all(iface == LO_PACKED for iface, _ in sock.sends)


def test_md6_when_the_loopback_cannot_be_used_the_lan_query_goes_on_as_before() -> None:
    """MD6: 루프백 인터페이스를 못 고르면(멀티캐스트 없는 lo) 그 송신만 건너뛴다 — LAN 질의 두 번 ·
    다른 호스트의 서버 · 정상 종료는 예전 그대로."""
    sock = FakeSock(
        [(answer("buildbox", "buildbox.local.", 8788, ["192.0.2.20"]), ("192.0.2.20", 5353))],
        loopback_fails=True,
    )
    assert [f.name for f in run(sock)] == ["buildbox"]
    assert sock.sends == [(ANY_PACKED, DEST), (ANY_PACKED, DEST)]
    assert sock.iface == ANY_PACKED and sock.closed


def test_md7_each_round_asks_the_lan_then_the_loopback_and_restores_the_interface() -> None:
    """MD7: 한 차례에 LAN → 루프백 순서로 하나씩, 끝나면 소켓의 멀티캐스트 인터페이스는 원래 값."""
    sock = FakeSock([])
    assert run(sock) == []
    assert sock.sends == [
        (ANY_PACKED, DEST),
        (LO_PACKED, DEST),
        (ANY_PACKED, DEST),
        (LO_PACKED, DEST),
    ]
    assert sock.iface == ANY_PACKED


# ── 응답기 소켓의 그룹 가입(qa-2, 2026-11-26) ───────────────────────────────


class JoinSock(socket.socket):
    """진짜 소켓이되 그룹 가입(`IP_ADD_MEMBERSHIP`)과 송신은 흉내만 낸다 — 네트워크에 아무것도
    나가지 않는다. `fail` 에 든 인터페이스의 가입은 EADDRNOTAVAIL, 나머지는 성공으로 친다."""

    fail: frozenset[bytes] = frozenset()

    def setsockopt(self, level: int, opt: int, value: object, *rest: object) -> None:  # type: ignore[override]
        if (level, opt) == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP):
            assert isinstance(value, bytes)
            iface = value[4:8]
            JOINS.append(iface)
            if iface in self.fail:
                raise OSError(errno.EADDRNOTAVAIL, "Can't assign requested address")
            return
        super().setsockopt(level, opt, value, *rest)  # type: ignore[arg-type]

    def sendto(self, data: bytes, *args: object) -> int:  # type: ignore[override]
        SENT.append((data, args[-1]))
        return len(data)


JOINS: list[bytes] = []
SENT: list[tuple[bytes, object]] = []
LAN_IP = "192.0.2.10"


class _SocketModule:
    """`mdns` 모듈이 보는 `socket` — `socket.socket` 만 `cls` 로 바꾼다(다른 모듈은 그대로)."""

    def __init__(self, cls: type[socket.socket]) -> None:
        self.socket = cls

    def __getattr__(self, name: str) -> object:
        return getattr(socket, name)


def joins_failing(monkeypatch: pytest.MonkeyPatch, *ifaces: str) -> None:
    fail = frozenset(socket.inet_aton(i) for i in ifaces)
    cls = type("FailingJoinSock", (JoinSock,), {"fail": fail})
    JOINS.clear()
    SENT.clear()
    monkeypatch.setattr(mdns_mod, "socket", _SocketModule(cls))
    monkeypatch.setattr(mdns_mod, "local_ipv4s", lambda: [LAN_IP])


def test_md8_only_the_loopback_joined_means_advertise_is_off(
    monkeypatch: pytest.MonkeyPatch, responders: list[Responder]
) -> None:
    """MD8: 실제 인터페이스 가입이 모두 실패하고(오프라인 Mac · 기본 경로 없음) 루프백만 되면 광고는
    꺼진 것이다 — `start()` 는 False, `error` 에 그 실패가 남는다(루프백 가입 전과 같다).
    루프백 가입을 세면 `mdns: advertising …` 을 찍고 /health 에 아무 신호도 없다
    (PLAN 「fail-open 금지」)."""
    joins_failing(monkeypatch, "0.0.0.0", LAN_IP)
    logs: list[str] = []
    r = Responder(
        instance_name("rcm-md-off"),
        "samehost.local.",
        8797,
        ips_fn=lambda: [LAN_IP],
        log=logs.append,
        mdns_port=0,
        route_fn=lambda _ip: None,
    )
    started = r.start()
    if started:
        responders.append(r)
    assert LO_PACKED in JOINS  # 루프백 가입은 시도했다 — 그것만으로 켜진 것으로 치지 않는다
    assert (started, r.error is not None) == (False, True), logs
    assert "Can't assign requested address" in (r.error or "")
    assert logs and logs[-1].startswith("mdns: advertise off — ")


def test_md9_a_failed_loopback_join_still_opens_the_socket_and_answers(
    monkeypatch: pytest.MonkeyPatch, responders: list[Responder]
) -> None:
    """MD9: 루프백 가입만 실패해도(멀티캐스트 없는 lo) 소켓은 열리고 LAN 쪽 가입은 그대로 —
    `start()` True · `error` 없음, 들어온 질의에 답한다(5353 이 아닌 포트의 질의 → 그 주소로)."""
    joins_failing(monkeypatch, LO)
    r = Responder(
        instance_name("rcm-md-lan"),
        "samehost.local.",
        8798,
        ips_fn=lambda: [LAN_IP],
        mdns_port=5353,
        sock_factory=lambda: mdns_mod._multicast_socket(0, bind_ip=LO),
        route_fn=lambda _ip: None,
    )
    assert r.start() is True, r.error
    responders.append(r)
    assert r.error is None
    assert JOINS == [ANY_PACKED, socket.inet_aton(LAN_IP), LO_PACKED]
    assert r._sock is not None
    port = r._sock.getsockname()[1]
    asker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        asker.bind((LO, 0))
        asker.sendto(encode_query([(SERVICE, TYPE_PTR)]), (LO, port))
        me = asker.getsockname()
        deadline = 50
        while deadline and not any(addr == me for _, addr in SENT):
            deadline -= 1
            r._stop.wait(0.05)
        assert any(addr == me for _, addr in SENT), SENT
        assert r.answered == 1
    finally:
        asker.close()
