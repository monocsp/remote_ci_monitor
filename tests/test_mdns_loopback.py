"""루프백 멀티캐스트 왕복(M5c-2) — 진짜 UDP 소켓으로 응답기(`Responder`)와 질의기(`discover`).

포트 15353(시스템 mDNS 데몬의 5353 과 절대 겹치지 않게) · 인터페이스 127.0.0.1 ·
`IP_MULTICAST_LOOP=1`. 소켓은 `sock_factory` 로 끼운다. 응답기·질의기·감시 소켓이 한 프로세스에서
같은 포트를 `SO_REUSEADDR`(+`SO_REUSEPORT`) 로 나눠 듣는다 — macOS 의 mDNSResponder 와 5353 을
공존하는 것과 같은 모양(§2). 멀티캐스트를 받으려면 소켓은 `0.0.0.0:<port>` 에 묶고(127.0.0.1 에
묶으면 그룹 주소로 온 패킷이 걸러진다) 가입·송신 인터페이스만 127.0.0.1 로 준다. 이 호스트에서
루프백 멀티캐스트가 안 되면(그룹 가입·바인드·루프 전달 실패) 파일 전체를 이유와 함께 skip 한다.

잠근 규칙: 응답기 하나 → `discover` 가 그 인스턴스 하나(포트·IP·버전·lanes)를 찾는다 — 그룹에
가입한 질의기(멀티캐스트 응답)와 임의 포트 질의기(legacy unicast 응답, RFC 6762 §6.7) 둘 다 ·
이름이 다른 둘 → 둘 다(이름순) · 응답기 없음 → 타임아웃 안에 빈 목록 · `start()` 는 True 를
곧 돌려주고 announce(QR=1, TTL>0) 를 보낸다 · start 직후의 질의에도 답한다(announce 사이에
귀를 닫지 않는다) · `stop()` 은 goodbye(TTL 0) 를 보내고 1.5초 안에 돌아오며 두 번 불러도
조용하다. goodbye 뒤 목록에서 사라지는 것은 v1 범위 밖(§5). sleep 은 없다 — 기다림은 전부
소켓 타임아웃(≤ 1.5초)이다.
"""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Callable, Iterator

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor.core.mdns import (
    SERVICE,
    TYPE_PTR,
    Found,
    MdnsError,
    Record,
    decode,
    encode_query,
    instance_name,
)
from remote_ci_monitor.mdns import Responder, discover

GROUP = "224.0.0.251"
IFACE = "127.0.0.1"
PORT = 15353  # mDNS 데몬(5353)과 겹치지 않는 테스트 전용 포트
IPS = ("192.0.2.10", "192.0.2.11")  # TEST-NET-1 — A 레코드가 그대로 실려 오는지 본다(해석이 아니라)
INSTANCE = instance_name("macmini")


def _loopback_only(s: socket.socket) -> None:
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(IFACE))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)


def member_socket() -> socket.socket:
    """그룹에 가입한 UDP 소켓(0.0.0.0:15353, 루프백 인터페이스) — 응답기 · 멀티캐스트 질의기 ·
    감시용. 실패하면 OSError."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("0.0.0.0", PORT))
        mreq = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton(IFACE))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        _loopback_only(s)
    except OSError:
        s.close()
        raise
    return s


def ephemeral_socket() -> socket.socket:
    """임의 포트 질의기(127.0.0.1:0) — 그룹에 가입하지 않으니 답은 unicast 로만 온다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.bind((IFACE, 0))
        _loopback_only(s)
    except OSError:
        s.close()
        raise
    return s


def _loopback_multicast_reason() -> str | None:
    """루프백 멀티캐스트가 안 되는 이유 문구(되면 None). 수집 시점에 0.5초 안에 끝난다."""
    try:
        s = member_socket()
    except OSError as e:
        return f"loopback multicast unavailable (bind {PORT} / join {GROUP} on {IFACE}): {e}"
    try:
        s.settimeout(0.5)
        s.sendto(b"rcm-probe", (GROUP, PORT))
        data, _ = s.recvfrom(64)
    except TimeoutError:
        return "loopback multicast is not delivered on this host (IP_MULTICAST_LOOP)"
    except OSError as e:
        return f"loopback multicast send/recv failed: {e}"
    finally:
        s.close()
    return None if data == b"rcm-probe" else "loopback multicast delivered unexpected data"


SKIP = _loopback_multicast_reason()
pytestmark = pytest.mark.skipif(SKIP is not None, reason=SKIP or "")


@pytest.fixture
def logs() -> list[str]:
    return []


@pytest.fixture
def responders(logs: list[str]) -> Iterator[list[Responder]]:
    started: list[Responder] = []
    try:
        yield started
    finally:
        for r in started:
            r.stop()
        if logs:
            print("\n".join(f"[responder] {line}" for line in logs))


def start(
    responders: list[Responder],
    logs: list[str],
    name: str,
    port: int,
    *,
    ips: tuple[str, ...] = IPS,
    lanes: int = 2,
) -> Responder:
    """`Responder(instance, host, port, *, ips_fn, txt_fn, log, mdns_port, sock_factory)` 를
    만들고 start — True 여야 한다(소켓을 열었다)."""
    r = Responder(
        instance_name(name),
        f"{name}.local.",
        port,
        ips_fn=lambda: list(ips),
        txt_fn=lambda: {"v": __version__, "name": name, "lanes": str(lanes)},
        log=logs.append,
        mdns_port=PORT,
        sock_factory=member_socket,
    )
    assert r.start() is True, logs
    responders.append(r)
    return r


def find(timeout: float = 1.0, sock: socket.socket | None = None) -> list[Found]:
    """`discover(timeout, mdns_port=15353, sock_factory=…)`. 소켓을 `start()` 전에 만들어 넘기면
    첫 announce 부터 그 소켓에 쌓여 있어 결과가 결정적이다(질의→응답 경로와 무관하게)."""
    s = sock or member_socket()
    return discover(timeout=timeout, mdns_port=PORT, sock_factory=lambda: s)


def responses(sock: socket.socket, pred: Callable[[list[Record]], bool]) -> list[list[Record]]:
    """소켓 타임아웃까지 QR=1 패킷의 레코드 목록을 모은다. pred 가 참이 되는 첫 패킷에서 멈춘다."""
    seen: list[list[Record]] = []
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(2048)
        except TimeoutError:
            break
        try:
            qs, rs = decode(data)
        except MdnsError:
            continue
        if qs or not rs:
            continue
        seen.append(rs)
        if pred(rs):
            break
    return seen


def our_ptr(rs: list[Record]) -> bool:
    return any(r.rtype == TYPE_PTR and r.name == SERVICE and r.data == INSTANCE for r in rs)


Any = object  # 타입 힌트용 — `find` 의 반환은 list[Found]


def test_one_responder_is_found_by_a_multicast_querier(
    responders: list[Responder], logs: list[str]
) -> None:
    """§3 `discover` — 그룹에 가입한 질의기가 응답기 하나의 이름·포트·A 레코드 IP·TXT 버전·lanes 를
    그대로 받는다(멀티캐스트 응답 경로)."""
    querier = member_socket()  # start 전에 — 첫 announce 부터 이 소켓에 쌓인다
    start(responders, logs, "macmini", 8787)
    assert find(sock=querier) == [Found("macmini", "macmini.local.", 8787, IPS, __version__, 2)]


def test_one_responder_is_found_by_an_ephemeral_port_querier(
    responders: list[Responder], logs: list[str]
) -> None:
    """RFC 6762 §6.7: 임의 포트에서 물은 질의기(그룹 미가입 — 실제 `rcm` 클라이언트의 모양)에게는
    unicast 로 답한다. start 직후의 질의라도 답이 와야 한다(announce 사이에 귀를 닫지 않는다)."""
    start(responders, logs, "macmini", 8787)
    found = find(sock=ephemeral_socket())
    assert found == [Found("macmini", "macmini.local.", 8787, IPS, __version__, 2)], logs


def test_two_responders_with_different_names_are_both_found(
    responders: list[Responder], logs: list[str]
) -> None:
    """§5: 이름이 다른 서버 둘 → 둘 다, 이름순."""
    querier = member_socket()
    start(responders, logs, "macmini", 8787)
    start(responders, logs, "buildbox", 8788, ips=("192.0.2.20",), lanes=4)
    found = find(sock=querier)
    assert [(f.name, f.host, f.port, f.ips, f.version, f.lanes) for f in found] == [
        ("buildbox", "buildbox.local.", 8788, ("192.0.2.20",), __version__, 4),
        ("macmini", "macmini.local.", 8787, IPS, __version__, 2),
    ]


def test_no_responder_yields_an_empty_list_within_the_timeout() -> None:
    """완료 기준 ③: 광고가 꺼져 있으면(응답기 없음) 질의기는 타임아웃 안에 빈 목록을 돌려준다."""
    t0 = time.monotonic()
    assert find(timeout=0.5) == []
    assert time.monotonic() - t0 < 1.5


def test_responder_answers_hand_sent_ptr_queries_by_multicast(
    responders: list[Responder], logs: list[str]
) -> None:
    """§2: PTR `_rcm._tcp.local.` 질의를 5353(여기선 15353)에서 그룹으로 보내면 그룹으로 답이 온다.
    질의 3개 → 응답 ≥ 3개(announce 는 최대 2개라, 답을 안 하면 3개가 될 수 없다). 질의는 start
    직후에 보낸다 — announce 사이에도 답해야 한다."""
    with member_socket() as sniff:
        sniff.settimeout(0.4)
        start(responders, logs, "macmini", 8787)
        query = encode_query([(SERVICE, TYPE_PTR)])
        for _ in range(3):
            sniff.sendto(query, (GROUP, PORT))
        got = responses(sniff, lambda _rs: False)
        ours = [rs for rs in got if our_ptr(rs)]
        assert len(ours) >= 3, f"{len(ours)} responses; logs={logs}"
        assert all(all(r.ttl > 0 for r in rs) for rs in ours)


def test_start_announces_and_stop_says_goodbye_promptly(
    responders: list[Responder], logs: list[str]
) -> None:
    """§2: start 는 announce(2회는 스레드에서 — start 자체는 곧 돌아온다), stop 은 goodbye(TTL 0)
    1회. stop 은 announce 간격(1초)을 기다리지 않고 1.5초 안에 돌아오며, 두 번 불러도 조용하다."""
    with member_socket() as sniff:
        sniff.settimeout(1.0)
        t0 = time.monotonic()
        r = start(responders, logs, "macmini", 8787)
        assert time.monotonic() - t0 < 0.5
        live = [rs for rs in responses(sniff, our_ptr) if our_ptr(rs)]
        assert live, f"no announce seen; logs={logs}"
        assert all(rec.ttl > 0 for rec in live[0])
        t0 = time.monotonic()
        r.stop()
        assert time.monotonic() - t0 < 1.5
        bye = responses(sniff, lambda rs: our_ptr(rs) and all(rec.ttl == 0 for rec in rs))
        goodbye = [rs for rs in bye if our_ptr(rs) and all(rec.ttl == 0 for rec in rs)]
        assert goodbye, f"no goodbye seen; saw {bye}; logs={logs}"
        r.stop()
