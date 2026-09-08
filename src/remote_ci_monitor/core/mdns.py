"""mDNS/DNS-SD 순수 규칙(M5c) — 패킷 인코딩·디코딩 · 「답해야 하나」 · 응답 조립 · 속도 제한.

소켓은 `remote_ci_monitor.mdns` 가 든다. 여기는 바이트와 dataclass 만.

- 서비스 `_rcm._tcp.local.` (DNS-SD). 인스턴스 `<name>._rcm._tcp.local.` 이 PTR 의 대상이고,
  SRV(포트 · 호스트) · TXT(`v=` · `name=` · `lanes=`) · A(LAN IPv4) 가 따라간다.
- 파싱은 바깥에서 오는 바이트다: 길이 검사 · 압축 포인터 루프/범위 검사 · 512 바이트 상한. 어긋나면
  `MdnsError`. 응답에는 비밀이 없다(이름 · 포트 · 버전 · 레인 수 · IP).
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

SERVICE = "_rcm._tcp.local."
MAX_PACKET = 512
MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_SRV = 33
TYPE_ANY = 255
CLASS_IN = 1
CACHE_FLUSH = 0x8000
TTL_PTR = 4500
TTL_OTHER = 120
MAX_ANSWERS_PER_SECOND = 20


class MdnsError(ValueError):
    """패킷이 규칙에 어긋난다(잘림 · 포인터 루프 · 크기 초과 · 잘못된 라벨)."""


@dataclass(frozen=True)
class Question:
    name: str
    qtype: int
    unicast: bool = False


@dataclass(frozen=True)
class Record:
    name: str
    rtype: int
    ttl: int
    # A → "1.2.3.4" · PTR → "name." · SRV → (priority, weight, port, target) · TXT → tuple[str]
    # 그 외 → bytes
    data: Any


@dataclass(frozen=True)
class Found:
    """발견된 서버 하나."""

    name: str
    host: str
    port: int
    ips: tuple[str, ...]
    version: str
    lanes: int | None = None

    @property
    def address(self) -> str:
        target = self.ips[0] if self.ips else self.host.rstrip(".")
        return f"http://{target}:{self.port}"


# ── 이름 ─────────────────────────────────────────────────────────────────────


def _norm(name: str) -> str:
    n = name.strip()
    if not n.endswith("."):
        n += "."
    return n.lower()


def _labels(name: str) -> list[bytes]:
    out: list[bytes] = []
    for part in _norm(name).rstrip(".").split("."):
        if not part:
            continue
        raw = part.encode("utf-8")
        if len(raw) > 63:
            raise MdnsError("label longer than 63 bytes")
        out.append(raw)
    return out


def encode_name(name: str) -> bytes:
    return b"".join(bytes([len(lab)]) + lab for lab in _labels(name)) + b"\x00"


def decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    """라벨 시퀀스(압축 포인터 포함)를 읽어 (이름, 다음 offset). 포인터를 따라간 뒤에는 원래 자리의
    다음 위치를 돌려준다. 루프·범위 초과는 `MdnsError`."""
    labels: list[str] = []
    jumped = False
    end = offset
    hops = 0
    pos = offset
    while True:
        if pos >= len(packet):
            raise MdnsError("name runs past the end of the packet")
        length = packet[pos]
        if length == 0:
            pos += 1
            if not jumped:
                end = pos
            break
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(packet):
                raise MdnsError("truncated compression pointer")
            pointer = ((length & 0x3F) << 8) | packet[pos + 1]
            if pointer >= pos:  # 앞으로만 가리킬 수 있다 — 루프·자기 참조 차단
                raise MdnsError("compression pointer does not point backwards")
            hops += 1
            if hops > 32:
                raise MdnsError("too many compression pointers")
            if not jumped:
                end = pos + 2
            jumped = True
            pos = pointer
            continue
        if length & 0xC0:
            raise MdnsError("unsupported label type")
        pos += 1
        if pos + length > len(packet):
            raise MdnsError("label runs past the end of the packet")
        labels.append(packet[pos : pos + length].decode("utf-8", errors="replace"))
        pos += length
        if len(labels) > 64:
            raise MdnsError("too many labels")
    return ".".join(labels).lower() + ".", end


# ── 인코딩 ───────────────────────────────────────────────────────────────────


def _header(qr: bool, qdcount: int, ancount: int, *, query_id: int = 0) -> bytes:
    flags = 0x8400 if qr else 0x0000  # QR + AA
    return struct.pack("!HHHHHH", query_id & 0xFFFF, flags, qdcount, ancount, 0, 0)


def query_id(packet: bytes) -> int:
    return struct.unpack("!H", packet[:2])[0] if len(packet) >= 2 else 0


def encode_query(questions: list[tuple[str, int]]) -> bytes:
    """질의 패킷. id 0 · QR 0 · 질문마다 (이름, 타입). QU 비트는 세우지 않는다(멀티캐스트 응답)."""
    if not questions:
        raise MdnsError("a query needs at least one question")
    out = [_header(False, len(questions), 0)]
    for name, qtype in questions:
        out.append(encode_name(name) + struct.pack("!HH", qtype, CLASS_IN))
    packet = b"".join(out)
    if len(packet) > MAX_PACKET:
        raise MdnsError("query larger than 512 bytes")
    return packet


def _rr(name: str, rtype: int, ttl: int, rdata: bytes, *, flush: bool) -> bytes:
    klass = CLASS_IN | (CACHE_FLUSH if flush else 0)
    return encode_name(name) + struct.pack("!HHIH", rtype, klass, ttl, len(rdata)) + rdata


def txt_rdata(txt: dict[str, str]) -> bytes:
    out = b""
    for k, v in txt.items():
        item = f"{k}={v}".encode()
        if len(item) > 255:
            raise MdnsError("TXT item longer than 255 bytes")
        out += bytes([len(item)]) + item
    return out or b"\x00"


def instance_name(name: str) -> str:
    return f"{name}.{SERVICE}"


def encode_response(
    instance: str,
    host: str,
    port: int,
    ips: list[str],
    txt: dict[str, str],
    *,
    ttl_ptr: int = TTL_PTR,
    ttl_other: int = TTL_OTHER,
    query_id: int = 0,
) -> bytes:
    """응답 패킷: PTR(서비스 → 인스턴스) · SRV · TXT · A(IP 마다). SRV/TXT/A 는 cache-flush.
    `query_id` 는 legacy unicast 응답(5353 이 아닌 포트에서 온 질의)에 되돌려 준다."""
    inst = _norm(instance)
    hostn = _norm(host)
    answers = [
        _rr(SERVICE, TYPE_PTR, ttl_ptr, encode_name(inst), flush=False),
        _rr(
            inst,
            TYPE_SRV,
            ttl_other,
            struct.pack("!HHH", 0, 0, int(port)) + encode_name(hostn),
            flush=True,
        ),
        _rr(inst, TYPE_TXT, ttl_other, txt_rdata(txt), flush=True),
    ]
    for ip in ips:
        try:
            packed = bytes(int(p) for p in ip.split("."))
            if len(packed) != 4:
                raise ValueError
        except ValueError as e:
            raise MdnsError(f"not an IPv4 address: {ip!r}") from e
        answers.append(_rr(hostn, TYPE_A, ttl_other, packed, flush=True))
    packet = _header(True, 0, len(answers), query_id=query_id) + b"".join(answers)
    if len(packet) > MAX_PACKET:
        raise MdnsError("response larger than 512 bytes")
    return packet


def encode_goodbye(instance: str, host: str, port: int) -> bytes:
    """떠날 때: 같은 레코드를 TTL 0 으로."""
    return encode_response(instance, host, port, [], {}, ttl_ptr=0, ttl_other=0)


# ── 디코딩 ───────────────────────────────────────────────────────────────────


def _parse_rdata(packet: bytes, rtype: int, start: int, length: int) -> Any:
    raw = packet[start : start + length]
    if rtype == TYPE_A:
        if length != 4:
            raise MdnsError("A record is not 4 bytes")
        return ".".join(str(b) for b in raw)
    # rdata 안 이름(PTR 대상 · SRV target)은 rdlength 안에서 끝나야 한다 — 패킷 안이라도 레코드
    # 밖 바이트를 이름으로 읽지 않는다(압축 포인터로 앞을 가리키는 것은 그대로 허용)
    if rtype == TYPE_PTR:
        name, end = decode_name(packet, start)
        if end > start + length:
            raise MdnsError("PTR name runs past the record")
        return name
    if rtype == TYPE_SRV:
        if length < 7:
            raise MdnsError("SRV record too short")
        prio, weight, port = struct.unpack("!HHH", raw[:6])
        target, end = decode_name(packet, start + 6)
        if end > start + length:
            raise MdnsError("SRV target runs past the record")
        return (prio, weight, port, target)
    if rtype == TYPE_TXT:
        items: list[str] = []
        pos = 0
        while pos < length:
            n = raw[pos]
            pos += 1
            if pos + n > length:
                raise MdnsError("TXT item runs past the record")
            if n:
                items.append(raw[pos : pos + n].decode("utf-8", errors="replace"))
            pos += n
        return tuple(items)
    return bytes(raw)


def decode(packet: bytes) -> tuple[list[Question], list[Record]]:
    """헤더 · 질문 · (답 + 권한 + 추가) 레코드. 모르는 타입은 raw bytes 로."""
    if len(packet) > MAX_PACKET:
        raise MdnsError("packet larger than 512 bytes")
    if len(packet) < 12:
        raise MdnsError("packet shorter than a DNS header")
    _id, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", packet[:12])
    pos = 12
    questions: list[Question] = []
    for _ in range(qd):
        name, pos = decode_name(packet, pos)
        if pos + 4 > len(packet):
            raise MdnsError("question runs past the end of the packet")
        qtype, qclass = struct.unpack("!HH", packet[pos : pos + 4])
        pos += 4
        questions.append(Question(name=name, qtype=qtype, unicast=bool(qclass & CACHE_FLUSH)))
    records: list[Record] = []
    for _ in range(an + ns + ar):
        name, pos = decode_name(packet, pos)
        if pos + 10 > len(packet):
            raise MdnsError("record runs past the end of the packet")
        rtype, _klass, ttl, rdlen = struct.unpack("!HHIH", packet[pos : pos + 10])
        pos += 10
        if pos + rdlen > len(packet):
            raise MdnsError("record data runs past the end of the packet")
        records.append(
            Record(name=name, rtype=rtype, ttl=ttl, data=_parse_rdata(packet, rtype, pos, rdlen))
        )
        pos += rdlen
    return questions, records


def is_query(packet: bytes) -> bool:
    if len(packet) < 12:
        return False
    flags = struct.unpack("!H", packet[2:4])[0]
    return not flags & 0x8000


# ── 규칙 ─────────────────────────────────────────────────────────────────────


def should_answer(questions: list[Question], instance: str, host: str) -> bool:
    """서비스 PTR/ANY · 우리 인스턴스의 SRV/TXT/ANY · 우리 호스트의 A/ANY 에만 답한다.
    이름은 대소문자를 가리지 않는다."""
    inst = _norm(instance)
    hostn = _norm(host)
    for q in questions:
        name = _norm(q.name)
        if name == SERVICE and q.qtype in (TYPE_PTR, TYPE_ANY):
            return True
        if name == inst and q.qtype in (TYPE_SRV, TYPE_TXT, TYPE_ANY):
            return True
        if name == hostn and q.qtype in (TYPE_A, TYPE_ANY):
            return True
    return False


def found_from_records(records: list[Record]) -> list[Found]:
    """PTR + SRV 가 있는 인스턴스만. IP 는 SRV 대상 이름의 A 레코드, 버전·레인은 TXT."""
    instances: list[str] = []
    for r in records:
        if r.rtype == TYPE_PTR and _norm(r.name) == SERVICE and isinstance(r.data, str):
            if r.ttl == 0:  # goodbye — 떠나는 서버는 목록에 넣지 않는다
                continue
            inst = _norm(r.data)
            if inst not in instances:
                instances.append(inst)
    srv = {
        _norm(r.name): r.data for r in records if r.rtype == TYPE_SRV and isinstance(r.data, tuple)
    }
    txt = {
        _norm(r.name): r.data for r in records if r.rtype == TYPE_TXT and isinstance(r.data, tuple)
    }
    addrs: dict[str, list[str]] = {}
    for r in records:
        if r.rtype == TYPE_A and isinstance(r.data, str):
            addrs.setdefault(_norm(r.name), [])
            if r.data not in addrs[_norm(r.name)]:
                addrs[_norm(r.name)].append(r.data)
    out: list[Found] = []
    for inst in instances:
        s = srv.get(inst)
        if s is None:
            continue
        _prio, _weight, port, target = s
        kv: dict[str, str] = {}
        for item in txt.get(inst, ()):
            k, _, v = item.partition("=")
            kv[k] = v
        lanes: int | None = None
        if kv.get("lanes", "").isdigit():
            lanes = int(kv["lanes"])
        label = inst[: -len(SERVICE) - 1] if inst.endswith("." + SERVICE) else inst.rstrip(".")
        out.append(
            Found(
                name=kv.get("name") or label,
                host=_norm(target),
                port=int(port),
                ips=tuple(addrs.get(_norm(target), [])),
                version=kv.get("v", ""),
                lanes=lanes,
            )
        )
    return out


@dataclass
class RateLimiter:
    """초당 응답 상한 — 질의 폭주에 멀티캐스트로 되받아치지 않게."""

    max_per_second: int = MAX_ANSWERS_PER_SECOND
    _window: float = field(default=-1.0, repr=False)
    _count: int = field(default=0, repr=False)

    def allow(self, now: float) -> bool:
        second = float(int(now))
        if second != self._window:
            self._window = second
            self._count = 0
        if self._count >= self.max_per_second:
            return False
        self._count += 1
        return True


# ── A 레코드 순서 ─────────────────────────────────────────────────────────────


def _octets(ip: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in ip.split("."))
    except ValueError:
        return (999,)


def ipv4_rank(ip: str) -> int:
    """A 레코드 등급 — 0 LAN · 1 CGNAT(100.64/10, Tailscale) · 2 링크로컬(169.254/16) · 3 모름.
    같은 Wi-Fi 의 노트북이 닿는 LAN 주소가 먼저다."""
    parts = _octets(ip)
    if len(parts) != 4:
        return 3
    a, b = parts[0], parts[1]
    if a == 169 and b == 254:
        return 2
    if a == 100 and 64 <= b <= 127:
        return 1
    return 0


def order_ipv4s(candidates: Iterable[str], *, first: str | None = None) -> list[str]:
    """발견 응답에 실을 IPv4 순서 — 첫 A 가 곧 `Found.address` 다. `first`(기본 경로 인터페이스,
    또는 질의자에게 가는 인터페이스의 주소)가 있으면 맨 앞, 나머지는 등급(LAN → CGNAT → 링크로컬)
    다음 숫자 순. `getaddrinfo` 의 순서는 호출마다 바뀌므로(실측) 여기서 고정한다.
    루프백은 빼고 중복은 없앤다."""
    seen: list[str] = []
    for ip in ([first] if first else []) + list(candidates):
        if not ip or ip.startswith("127.") or ip in seen:
            continue
        seen.append(ip)
    head = seen[:1] if first and seen and seen[0] == first else []
    rest = sorted(seen[len(head) :], key=lambda ip: (ipv4_rank(ip), _octets(ip)))
    return head + rest


__all__ = [
    "CACHE_FLUSH",
    "CLASS_IN",
    "MAX_ANSWERS_PER_SECOND",
    "MAX_PACKET",
    "MDNS_GROUP",
    "MDNS_PORT",
    "SERVICE",
    "TTL_OTHER",
    "TTL_PTR",
    "TYPE_A",
    "TYPE_ANY",
    "TYPE_PTR",
    "TYPE_SRV",
    "TYPE_TXT",
    "Found",
    "MdnsError",
    "Question",
    "RateLimiter",
    "Record",
    "decode",
    "decode_name",
    "encode_goodbye",
    "encode_name",
    "encode_query",
    "encode_response",
    "found_from_records",
    "instance_name",
    "ipv4_rank",
    "is_query",
    "order_ipv4s",
    "query_id",
    "should_answer",
    "txt_rdata",
]
