"""mDNS/DNS-SD 순수 함수(M5c-1) + `Responder.handle` · 가짜 소켓 `discover`(M5c-2).

`core/mdns.py` 에는 소켓이 없다 — 질의·응답 인코딩, 디코딩(압축 포인터 · 루프 · 잘림 · 512 초과
거부), `should_answer` 규칙, `found_from_records` 조립, goodbye(TTL 0), 응답 상한 `RateLimiter`.
`remote_ci_monitor/mdns.py` 의 `Responder.handle(packet, addr)` 와 `discover(sock_factory=…)` 는
소켓 없이(가짜 소켓으로) 부른다. 실제 루프백 왕복은 `test_mdns_loopback.py`.

잠근 표기: 이름은 언제나 끝에 `.` 을 붙인 FQDN(`macmini._rcm._tcp.local.` · `macmini.local.`).
디코더는 이름을 소문자로 정규화해 돌려주고, 비교는 대소문자를 가리지 않는다. 인코더는 이름을
압축하지 않는다(디코더는 압축을 읽는다). `Found.host` 는 SRV target 그대로(끝 `.` 포함).
M5c-2 모듈(`remote_ci_monitor.mdns`)은 함수 안에서 들여온다 — M5c-1(core 만) 단계에서 core
테스트가 살아 있게.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import socket
import struct
from typing import Any

import pytest

from remote_ci_monitor.core.mdns import (
    CACHE_FLUSH,
    CLASS_IN,
    MAX_ANSWERS_PER_SECOND,
    MAX_PACKET,
    MDNS_GROUP,
    MDNS_PORT,
    SERVICE,
    TTL_OTHER,
    TTL_PTR,
    TYPE_A,
    TYPE_ANY,
    TYPE_PTR,
    TYPE_SRV,
    TYPE_TXT,
    Found,
    MdnsError,
    Question,
    RateLimiter,
    Record,
    decode,
    encode_goodbye,
    encode_query,
    encode_response,
    found_from_records,
    instance_name,
    is_query,
    should_answer,
)

INSTANCE = "macmini._rcm._tcp.local."
HOST = "macmini.local."
PORT = 8787
IPS = ["192.0.2.10", "192.0.2.11"]  # TEST-NET-1 — 실제 인터페이스와 무관하다
TXT = {"v": "0.2.1", "name": "macmini", "lanes": "2"}
ADDR = ("192.0.2.50", 5353)
LEGACY_ADDR = ("192.0.2.50", 40000)  # 5353 이 아닌 포트 — legacy unicast 질의(RFC 6762 §6.7)
MACMINI = Found("macmini", HOST, PORT, tuple(IPS), "0.2.1", 2)
QR_AA = 0x8400
EMPTY_HEADER = struct.pack("!6H", 0, 0, 0, 0, 0, 0)


def host_name(name: str) -> str:
    return f"{name}.local."


def header(pkt: bytes) -> tuple[int, ...]:
    """DNS 헤더 6개 필드 — id · flags · qdcount · ancount · nscount · arcount."""
    return struct.unpack("!6H", pkt[:12])


def labels(name: str) -> bytes:
    """압축 없는 라벨 열. `"a.local."` → `\\x01a\\x05local\\x00`."""
    out = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split("."))
    return out + b"\x00"


def ptr(offset: int) -> bytes:
    """압축 포인터 2바이트(상위 2비트 11)."""
    return struct.pack("!H", 0xC000 | offset)


def rr(name: bytes, rtype: int, rclass: int, ttl: int, rdata: bytes) -> bytes:
    return name + struct.pack("!HHIH", rtype, rclass, ttl, len(rdata)) + rdata


def rr_head(name: str, rtype: int, rclass: int, ttl: int) -> bytes:
    """압축 없는 레코드의 앞부분(이름 · 타입 · 클래스 · TTL) — 클래스의 cache-flush 비트를 본다."""
    return labels(name) + struct.pack("!HHI", rtype, rclass, ttl)


def query_packet(name_wire: bytes, qtype: int = TYPE_PTR, qclass: int = CLASS_IN) -> bytes:
    """질문 하나짜리 손 패킷 — 이름 부분만 바꿔 가며 디코더를 찌른다."""
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + name_wire + struct.pack("!HH", qtype, qclass)


def with_id(pkt: bytes, ident: int) -> bytes:
    return struct.pack("!H", ident) + pkt[2:]


def response(**kw: Any) -> bytes:
    args: dict[str, Any] = {
        "instance": INSTANCE,
        "host": HOST,
        "port": PORT,
        "ips": IPS,
        "txt": TXT,
    }
    args.update(kw)
    return encode_response(**args)


def records(**kw: Any) -> list[Record]:
    return decode(response(**kw))[1]


def only(rs: list[Record], rtype: int) -> Record:
    hits = [r for r in rs if r.rtype == rtype]
    assert len(hits) == 1, hits
    return hits[0]


def padded_response(total: int) -> bytes:
    """TXT 채움 문자열로 정확히 `total` 바이트짜리 응답을 만든다(문자열 하나 ≤ 255 바이트)."""
    txt: dict[str, str] = {"v": "0.2.1"}
    room = total - len(response(txt=txt))
    assert room >= 4, room
    n = 0
    while room > 0:
        take = min(room, 200)
        if 0 < room - take < 4:
            take = room - 4
        key = f"p{n}"  # 길이 바이트 1 + key + "=" + value == take
        txt[key] = "x" * (take - len(key) - 2)
        room -= take
        n += 1
    pkt = response(txt=txt)
    assert len(pkt) == total, len(pkt)
    return pkt


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ── 상수 · 모양 ──────────────────────────────────────────────────────────────


def test_constants_are_the_spec_values() -> None:
    """§1 서비스 이름 · §2 그룹/포트/상한/TTL · DNS 타입 번호 · 오류 클래스는 ValueError."""
    assert SERVICE == "_rcm._tcp.local."
    assert MAX_PACKET == 512
    assert (MDNS_GROUP, MDNS_PORT) == ("224.0.0.251", 5353)
    assert (TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV, TYPE_ANY) == (1, 12, 16, 33, 255)
    assert (CLASS_IN, CACHE_FLUSH) == (1, 0x8000)
    assert (TTL_PTR, TTL_OTHER) == (4500, 120)
    assert MAX_ANSWERS_PER_SECOND == 20
    assert issubclass(MdnsError, ValueError)


def test_instance_name_helper() -> None:
    """`instance_name("macmini")` → `macmini._rcm._tcp.local.`(§1)."""
    assert instance_name("macmini") == INSTANCE


def test_dataclasses_are_frozen_with_the_locked_fields() -> None:
    """Question(name, qtype, unicast=False) · Record(name, rtype, ttl, data) ·
    Found(name, host, port, ips, version, lanes=None) — 모두 frozen."""
    assert [f.name for f in dataclasses.fields(Question)] == ["name", "qtype", "unicast"]
    assert [f.name for f in dataclasses.fields(Record)] == ["name", "rtype", "ttl", "data"]
    assert [f.name for f in dataclasses.fields(Found)] == [
        "name",
        "host",
        "port",
        "ips",
        "version",
        "lanes",
    ]
    assert Question(SERVICE, TYPE_PTR).unicast is False
    assert Found("a", "a.local.", 1, (), "").lanes is None
    for obj, field in (
        (Question(SERVICE, TYPE_PTR), "name"),
        (Record(HOST, TYPE_A, 120, "192.0.2.1"), "ttl"),
        (MACMINI, "port"),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, field, 1)


def test_found_address_prefers_the_first_ip_and_falls_back_to_the_host_name() -> None:
    """§1·§3: 클라이언트는 IP 를 쓴다 — `address` 는 `http://<첫 A>:<port>`, A 가 없으면
    `.local` 이름(끝 `.` 뗀 것)."""
    assert MACMINI.address == "http://192.0.2.10:8787"
    assert dataclasses.replace(MACMINI, ips=()).address == "http://macmini.local:8787"


# ── encode_query ─────────────────────────────────────────────────────────────


def test_encode_query_is_a_plain_multicast_question() -> None:
    """id 0 · QR 0 · 질문 1 · 라벨 인코딩 · QCLASS IN(QU 비트 없음) — 되읽으면 같은 질문."""
    pkt = encode_query([(SERVICE, TYPE_PTR)])
    assert header(pkt) == (0, 0, 1, 0, 0, 0)
    assert pkt[12:] == labels(SERVICE) + struct.pack("!HH", TYPE_PTR, CLASS_IN)
    assert decode(pkt) == ([Question(SERVICE, TYPE_PTR, False)], [])


def test_encode_query_keeps_question_order() -> None:
    pkt = encode_query([(SERVICE, TYPE_PTR), (INSTANCE, TYPE_SRV), (HOST, TYPE_A)])
    assert header(pkt)[2] == 3
    assert decode(pkt)[0] == [
        Question(SERVICE, TYPE_PTR, False),
        Question(INSTANCE, TYPE_SRV, False),
        Question(HOST, TYPE_A, False),
    ]


def test_encode_query_rejects_no_questions_and_a_label_over_63_bytes() -> None:
    with pytest.raises(MdnsError):
        encode_query([])
    with pytest.raises(MdnsError):
        encode_query([("a" * 64 + ".local.", TYPE_PTR)])


def test_is_query_reads_the_qr_bit_and_tolerates_short_input() -> None:
    assert is_query(encode_query([(SERVICE, TYPE_PTR)])) is True
    assert is_query(response()) is False
    assert is_query(b"") is False and is_query(b"\x00" * 11) is False


# ── encode_response ──────────────────────────────────────────────────────────


def test_response_header_is_an_authoritative_answer_with_all_records_in_the_answer_section() -> (
    None
):
    """id 0 · QR=1 · AA=1 · RCODE 0 · 질문 0 · answer = PTR+SRV+TXT+A×n · authority/additional 0."""
    ident, flags, qd, an, ns, ar = header(response())
    assert ident == 0
    assert flags & 0x8000 and flags & 0x0400
    assert flags & 0x000F == 0
    assert (qd, an, ns, ar) == (0, 3 + len(IPS), 0, 0)


def test_response_record_order_is_ptr_srv_txt_then_one_a_per_ip() -> None:
    assert [r.rtype for r in records()] == [TYPE_PTR, TYPE_SRV, TYPE_TXT, TYPE_A, TYPE_A]


def test_response_ptr_is_service_to_instance_with_ttl_4500() -> None:
    assert only(records(), TYPE_PTR) == Record(SERVICE, TYPE_PTR, TTL_PTR, INSTANCE)


def test_response_srv_carries_priority_weight_port_and_host() -> None:
    """SRV data 는 `(priority, weight, port, target)` 튜플 — priority·weight 0."""
    assert only(records(), TYPE_SRV) == Record(INSTANCE, TYPE_SRV, TTL_OTHER, (0, 0, PORT, HOST))


def test_response_txt_is_key_equals_value_in_dict_order() -> None:
    assert only(records(), TYPE_TXT) == Record(
        INSTANCE, TYPE_TXT, TTL_OTHER, ("v=0.2.1", "name=macmini", "lanes=2")
    )


def test_response_one_a_record_per_ip_in_the_given_order() -> None:
    a = [r for r in records() if r.rtype == TYPE_A]
    assert a == [Record(HOST, TYPE_A, TTL_OTHER, ip) for ip in IPS]


def test_response_cache_flush_bit_is_on_srv_txt_a_but_not_ptr() -> None:
    """클래스 최상위 비트(cache-flush): SRV/TXT/A 는 켜고(우리만의 레코드), PTR 은 끈다
    (RFC 6762 §10.2 — 서비스 PTR 은 여러 서버가 나눠 갖는 공유 레코드)."""
    pkt = response()
    assert rr_head(SERVICE, TYPE_PTR, CLASS_IN, TTL_PTR) in pkt
    assert rr_head(INSTANCE, TYPE_SRV, CLASS_IN | CACHE_FLUSH, TTL_OTHER) in pkt
    assert rr_head(INSTANCE, TYPE_TXT, CLASS_IN | CACHE_FLUSH, TTL_OTHER) in pkt
    assert pkt.count(rr_head(HOST, TYPE_A, CLASS_IN | CACHE_FLUSH, TTL_OTHER)) == len(IPS)
    assert rr_head(SERVICE, TYPE_PTR, CLASS_IN | CACHE_FLUSH, TTL_PTR) not in pkt


def test_response_wire_rdata_is_uncompressed() -> None:
    """인코더는 이름을 압축하지 않는다 — SRV rdata(우선순위·가중치·포트 + target 라벨) ·
    A rdata 4바이트 · TXT 문자열이 패킷에 그대로 들어 있다."""
    pkt = response(ips=["192.0.2.10"], txt={"v": "1"})
    assert struct.pack("!HHH", 0, 0, PORT) + labels(HOST) in pkt
    assert labels(SERVICE) in pkt
    assert pkt.count(labels(INSTANCE)) == 3  # PTR rdata · SRV 소유자 · TXT 소유자
    assert socket.inet_aton("192.0.2.10") in pkt
    assert b"\x03v=1" in pkt


def test_response_empty_txt_decodes_to_no_strings() -> None:
    """빈 TXT 는 RFC 6763 §6.1 대로 0 바이트 문자열 하나로 나가고, 디코더는 빈 문자열을
    버린다 → `()`."""
    pkt = response(txt={})
    assert rr_head(INSTANCE, TYPE_TXT, CLASS_IN | CACHE_FLUSH, TTL_OTHER) + b"\x00\x01\x00" in pkt
    assert only(decode(pkt)[1], TYPE_TXT).data == ()


def test_response_without_ips_has_no_a_records() -> None:
    assert [r.rtype for r in records(ips=[])] == [TYPE_PTR, TYPE_SRV, TYPE_TXT]


def test_response_ttl_parameters_are_honoured() -> None:
    ttls = {r.rtype: r.ttl for r in records(ttl_ptr=10, ttl_other=5)}
    assert ttls == {TYPE_PTR: 10, TYPE_SRV: 5, TYPE_TXT: 5, TYPE_A: 5}


def test_response_normalises_instance_and_host_case() -> None:
    """인코더도 이름을 소문자 FQDN 으로 — 끝 `.` 이 없어도, 대문자여도 같은 패킷."""
    assert response(instance="MacMini._rcm._tcp.local", host="MACMINI.local") == response()


def test_response_over_max_packet_is_refused_by_the_encoder() -> None:
    """인코더는 디코더가 버릴 패킷을 만들지 않는다 — 512 를 넘으면 MdnsError."""
    with pytest.raises(MdnsError):
        response(ips=[f"10.0.{i // 256}.{i % 256}" for i in range(40)])


@pytest.mark.parametrize(
    "kw",
    [
        {"ips": ["not-an-ip"]},
        {"ips": ["::1"]},
        {"ips": ["192.0.2"]},
        {"ips": ["999.0.0.1"]},
        {"txt": {"v": "x" * 260}},
    ],
    ids=["ip-text", "ipv6", "ip-short", "ip-octet-over-255", "txt-over-255"],
)
def test_response_rejects_bad_ip_and_long_txt(kw: dict[str, Any]) -> None:
    with pytest.raises(MdnsError):
        response(**kw)


def test_instance_label_with_a_dot_round_trips() -> None:
    """`advertise_name` 은 `.` 을 허용한다(`_NAME_RE`) — `mac.mini` 가 인코딩→디코딩→조립을
    지나 그대로다."""
    inst, host = instance_name("mac.mini"), host_name("mac.mini")
    rs = decode(response(instance=inst, host=host, txt={"v": "1"}))[1]
    assert only(rs, TYPE_PTR).data == inst
    assert found_from_records(rs) == [Found("mac.mini", host, PORT, tuple(IPS), "1", None)]


# ── decode ───────────────────────────────────────────────────────────────────


def test_decode_round_trips_the_encoder_output_exactly() -> None:
    qs, rs = decode(response())
    assert qs == []
    assert rs == [
        Record(SERVICE, TYPE_PTR, TTL_PTR, INSTANCE),
        Record(INSTANCE, TYPE_SRV, TTL_OTHER, (0, 0, PORT, HOST)),
        Record(INSTANCE, TYPE_TXT, TTL_OTHER, ("v=0.2.1", "name=macmini", "lanes=2")),
        Record(HOST, TYPE_A, TTL_OTHER, "192.0.2.10"),
        Record(HOST, TYPE_A, TTL_OTHER, "192.0.2.11"),
    ]


def test_decode_empty_header_only_packet_is_empty() -> None:
    assert decode(EMPTY_HEADER) == ([], [])


def test_decode_follows_compression_pointers_in_owner_names_and_rdata() -> None:
    """압축 포인터(0xC0|offset)를 소유자 이름 · PTR 대상 · SRV target 모두에서 따라간다.
    결과는 FQDN."""
    hdr = struct.pack("!6H", 0, QR_AA, 0, 2, 0, 0)
    service = labels(SERVICE)  # 오프셋 12: \x04_rcm \x04_tcp \x05local \x00
    off_service = 12
    off_local = off_service + 5 + 5  # `\x05local\x00` 의 시작 = 22
    rdata1 = b"\x07macmini" + ptr(off_service)  # macmini._rcm._tcp.local.
    rec1 = rr(service, TYPE_PTR, CLASS_IN, 4500, rdata1)
    off_instance = off_service + len(service) + 10  # rec1 의 rdata 시작 = 39
    rdata2 = struct.pack("!HHH", 0, 0, PORT) + b"\x07macmini" + ptr(off_local)  # macmini.local.
    rec2 = rr(ptr(off_instance), TYPE_SRV, CLASS_IN | CACHE_FLUSH, 120, rdata2)
    pkt = hdr + rec1 + rec2
    assert len(pkt) <= MAX_PACKET
    assert decode(pkt) == (
        [],
        [
            Record(SERVICE, TYPE_PTR, 4500, INSTANCE),
            Record(INSTANCE, TYPE_SRV, 120, (0, 0, PORT, HOST)),
        ],
    )


def test_decode_reads_answer_authority_and_additional_sections_in_order() -> None:
    """세 섹션의 레코드가 한 목록에 순서대로 온다. 모르는 타입(NSEC 47)은 rdata 를 bytes 그대로.
    TXT 안의 빈 문자열은 버린다. cache-flush 비트는 데이터에 영향이 없다."""
    hdr = struct.pack("!6H", 7, QR_AA, 0, 1, 1, 1)
    a = rr(labels("a.local."), TYPE_A, CLASS_IN | CACHE_FLUSH, 120, socket.inet_aton("192.0.2.1"))
    n = rr(labels("n.local."), 47, CLASS_IN, 120, b"\x01\x02\x03")
    x = rr(labels("x.local."), TYPE_TXT, CLASS_IN, 120, b"\x03v=1\x00\x03k=v")
    assert decode(hdr + a + n + x) == (
        [],
        [
            Record("a.local.", TYPE_A, 120, "192.0.2.1"),
            Record("n.local.", 47, 120, b"\x01\x02\x03"),
            Record("x.local.", TYPE_TXT, 120, ("v=1", "k=v")),
        ],
    )


def test_decode_lowercases_names_and_reads_the_qu_bit() -> None:
    """이름은 소문자 FQDN 으로 정규화(RFC 6762 §16 — 비교는 대소문자 무시).
    QCLASS 최상위 비트(QU)는 `Question.unicast`."""
    hdr = struct.pack("!6H", 0, 0, 2, 0, 0, 0)
    q1 = labels("MacMini._RCM._tcp.local.") + struct.pack("!HH", TYPE_SRV, CLASS_IN | 0x8000)
    q2 = labels(SERVICE) + struct.pack("!HH", TYPE_PTR, CLASS_IN)
    assert decode(hdr + q1 + q2) == (
        [Question(INSTANCE, TYPE_SRV, True), Question(SERVICE, TYPE_PTR, False)],
        [],
    )


@pytest.mark.parametrize(
    "name_wire",
    [
        ptr(12),
        ptr(14) + ptr(12),
        b"\x03abc" + ptr(12),
        ptr(200),
        ptr(18),
        b"\x40abc\x00",
        b"\x80abc\x00",
    ],
    ids=[
        "self-loop",
        "two-hop-loop",
        "label-then-loop",
        "pointer-past-end",
        "pointer-at-end",
        "reserved-01",
        "reserved-10",
    ],
)
def test_decode_rejects_pointer_loops_out_of_range_pointers_and_reserved_labels(
    name_wire: bytes,
) -> None:
    """포인터 루프 · 패킷 밖 포인터 · 예약 라벨 타입(01/10)은 MdnsError — 무한 루프도
    IndexError 도 아니다."""
    with pytest.raises(MdnsError):
        decode(query_packet(name_wire))


def test_decode_rejects_every_proper_prefix_of_a_valid_response() -> None:
    """잘린 패킷(헤더 미만 · 질문/레코드 중간 · rdlength 너머)은 모두 MdnsError."""
    pkt = response()
    for n in range(len(pkt)):
        with pytest.raises(MdnsError):
            decode(pkt[:n])


def test_decode_rejects_a_header_whose_counts_exceed_the_body() -> None:
    with pytest.raises(MdnsError):
        decode(struct.pack("!6H", 0, 0, 1, 0, 0, 0))
    with pytest.raises(MdnsError):
        decode(struct.pack("!6H", 0, QR_AA, 0, 1, 0, 0) + labels(HOST))


def test_decode_accepts_exactly_max_packet_and_refuses_one_more_byte() -> None:
    pkt = padded_response(MAX_PACKET)
    _, rs = decode(pkt)
    assert only(rs, TYPE_TXT).data[0] == "v=0.2.1"
    with pytest.raises(MdnsError):
        decode(pkt + b"\x00")
    with pytest.raises(MdnsError):
        decode(b"\x00" * (MAX_PACKET + 1))


# ── should_answer ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "qtype", "want"),
    [
        (SERVICE, TYPE_PTR, True),
        (SERVICE, TYPE_ANY, True),
        ("_RCM._TCP.LOCAL.", TYPE_PTR, True),
        (SERVICE, TYPE_SRV, False),
        (SERVICE, TYPE_TXT, False),
        (SERVICE, TYPE_A, False),
        (INSTANCE, TYPE_SRV, True),
        (INSTANCE, TYPE_TXT, True),
        (INSTANCE, TYPE_ANY, True),
        ("MACMINI._rcm._tcp.LOCAL.", TYPE_SRV, True),
        (INSTANCE, TYPE_PTR, False),
        (INSTANCE, TYPE_A, False),
        (HOST, TYPE_A, True),
        (HOST, TYPE_ANY, True),
        ("MacMini.LOCAL.", TYPE_A, True),
        (HOST, TYPE_SRV, False),
        (HOST, TYPE_TXT, False),
        (HOST, TYPE_PTR, False),
        (HOST, 28, False),  # AAAA — v1 은 IPv4 만(결정 35)
        ("_http._tcp.local.", TYPE_PTR, False),
        ("_http._tcp.local.", TYPE_ANY, False),
        ("other._rcm._tcp.local.", TYPE_SRV, False),
        ("other._rcm._tcp.local.", TYPE_ANY, False),
        ("other.local.", TYPE_A, False),
        ("macmini._rcp._tcp.local.", TYPE_SRV, False),
        ("local.", TYPE_ANY, False),
    ],
)
def test_should_answer_matrix(name: str, qtype: int, want: bool) -> None:
    """§2: PTR/ANY `_rcm._tcp.local.` · SRV/TXT/ANY 인스턴스 · A/ANY 호스트 — 대소문자 무시,
    그 외는 무시."""
    assert should_answer([Question(name, qtype)], INSTANCE, HOST) is want


def test_should_answer_needs_only_one_matching_question() -> None:
    unrelated = Question("_http._tcp.local.", TYPE_PTR)
    assert should_answer([], INSTANCE, HOST) is False
    assert should_answer([unrelated, unrelated], INSTANCE, HOST) is False
    assert should_answer([unrelated, Question(SERVICE, TYPE_PTR)], INSTANCE, HOST) is True


def test_should_answer_ignores_the_qu_bit() -> None:
    assert should_answer([Question(SERVICE, TYPE_PTR, unicast=True)], INSTANCE, HOST) is True


# ── found_from_records ───────────────────────────────────────────────────────


def test_found_assembles_name_host_port_ips_version_and_lanes() -> None:
    """PTR+SRV+TXT+A → Found 하나. name = TXT `name=`(없으면 PTR 대상에서 서비스 접미 뗀 라벨) ·
    host = SRV target · port = SRV · ips = target 과 이름이 같은 A 들(튜플, 순서대로) ·
    version = TXT `v=` · lanes = TXT `lanes=`."""
    found = found_from_records(records())
    assert found == [MACMINI]
    assert isinstance(found[0].ips, tuple) and isinstance(found[0].lanes, int)


def test_found_name_comes_from_txt_name_before_the_instance_label() -> None:
    """광고한 이름의 대소문자는 TXT `name=` 이 지킨다(DNS 이름은 소문자로 정규화되니까)."""
    assert found_from_records(records(txt={"name": "MacMini", "v": "1"}))[0].name == "MacMini"
    assert found_from_records(records(txt={"v": "1"}))[0].name == "macmini"


def test_found_without_a_records_has_empty_ips() -> None:
    assert found_from_records(records(ips=[])) == [dataclasses.replace(MACMINI, ips=())]


def test_found_without_txt_has_empty_version_and_no_lanes() -> None:
    rs = [r for r in records() if r.rtype != TYPE_TXT]
    assert found_from_records(rs) == [dataclasses.replace(MACMINI, version="", lanes=None)]


@pytest.mark.parametrize(
    ("txt", "version", "lanes"),
    [
        ({"v": "0.2.1", "lanes": "2"}, "0.2.1", 2),
        ({"v": "0.2.1", "lanes": "0"}, "0.2.1", 0),
        ({"v": "0.2.1", "lanes": "abc"}, "0.2.1", None),
        ({"v": "0.2.1", "lanes": ""}, "0.2.1", None),
        ({"v": "0.2.1"}, "0.2.1", None),
        ({"lanes": "3"}, "", 3),
        ({"name": "macmini", "extra": "ignored"}, "", None),
        ({"v": "1.0=beta"}, "1.0=beta", None),
    ],
)
def test_found_reads_version_and_lanes_from_txt(
    txt: dict[str, str], version: str, lanes: int | None
) -> None:
    """`v=` 는 첫 `=` 뒤 전부 · `lanes=` 는 int 아니면 None · 모르는 키는 무시."""
    found = found_from_records(records(txt=txt))
    assert (found[0].version, found[0].lanes) == (version, lanes)


def test_found_ignores_txt_strings_without_an_equals_sign() -> None:
    rs = [r for r in records() if r.rtype != TYPE_TXT]
    rs.append(Record(INSTANCE, TYPE_TXT, 120, ("junk", "v=9", "=nokey")))
    assert found_from_records(rs)[0].version == "9"


def test_found_needs_both_ptr_and_srv() -> None:
    rs = records()
    assert found_from_records([r for r in rs if r.rtype != TYPE_SRV]) == []  # PTR 만
    assert found_from_records([r for r in rs if r.rtype != TYPE_PTR]) == []  # SRV/TXT/A 만
    assert found_from_records([]) == []


def test_found_ignores_ptr_for_another_service() -> None:
    other = decode(response(instance="x._http._tcp.local.", host="x.local."))[1]
    ptr_other = Record("_http._tcp.local.", TYPE_PTR, 4500, "x._http._tcp.local.")
    assert found_from_records([ptr_other] + [r for r in other if r.rtype != TYPE_PTR]) == []


def test_found_ignores_a_records_for_other_hosts_and_unknown_types() -> None:
    rs = records() + [
        Record("other.local.", TYPE_A, 120, "192.0.2.77"),
        Record(INSTANCE, 47, 120, b"\x00"),
        Record(HOST, 28, 120, b"\x00" * 16),
    ]
    assert found_from_records(rs) == [MACMINI]


def test_found_collapses_duplicate_responses_and_duplicate_ips() -> None:
    """announce 두 번 + 응답 한 번을 다 받아도 인스턴스 하나 · 같은 IP 는 한 번."""
    assert found_from_records(records() * 3) == [MACMINI]
    rs = records(ips=["192.0.2.10", "192.0.2.10", "192.0.2.11"])
    assert found_from_records(rs) == [MACMINI]


def test_found_lists_instances_in_first_ptr_order() -> None:
    bb = records(
        instance="buildbox._rcm._tcp.local.",
        host="buildbox.local.",
        port=8788,
        ips=["192.0.2.20"],
        txt={"v": "0.3.0", "lanes": "4"},
    )
    found = found_from_records(records() + bb)
    assert found == [
        MACMINI,
        Found("buildbox", "buildbox.local.", 8788, ("192.0.2.20",), "0.3.0", 4),
    ]
    assert found_from_records(bb + records()) == found[::-1]


def test_found_matches_names_case_insensitively() -> None:
    """A 이름 ↔ SRV target · PTR 대상 ↔ SRV/TXT 소유자 는 대소문자를 가리지 않는다
    (RFC 6762 §16). 손으로 만든 레코드라 정규화되지 않은 이름이 섞여 있다."""
    rs = [
        Record(SERVICE, TYPE_PTR, 4500, "MacMini._RCM._TCP.LOCAL."),
        Record(INSTANCE, TYPE_SRV, 120, (0, 0, PORT, "MACMINI.local.")),
        Record("macmini._RCM._tcp.local.", TYPE_TXT, 120, ("v=0.2.1", "name=MacMini", "lanes=2")),
        Record("macmini.LOCAL.", TYPE_A, 120, "192.0.2.10"),
        Record("MacMini.local.", TYPE_A, 120, "192.0.2.11"),
    ]
    assert found_from_records(rs) == [
        Found("MacMini", HOST, PORT, ("192.0.2.10", "192.0.2.11"), "0.2.1", 2)
    ]


def test_found_skips_a_ptr_with_ttl_zero() -> None:
    """goodbye(TTL 0) 는 발견이 아니다 — 질의 창 안에 떠나는 서버를 목록에 올리지 않는다."""
    assert found_from_records(records(ttl_ptr=0, ttl_other=0)) == []
    assert found_from_records(records(ttl_ptr=0)) == []
    assert found_from_records(decode(encode_goodbye(INSTANCE, HOST, PORT))[1]) == []


# ── encode_goodbye ───────────────────────────────────────────────────────────


def test_goodbye_is_an_answer_whose_records_all_have_ttl_zero() -> None:
    """QR=1 · AA=1 · PTR 이 먼저, SRV 포함 · 모든 TTL 0."""
    pkt = encode_goodbye(INSTANCE, HOST, PORT)
    _, flags, qd, an, ns, ar = header(pkt)
    assert flags & 0x8000 and flags & 0x0400 and qd == 0 and an + ns + ar >= 2
    _, rs = decode(pkt)
    assert rs and all(r.ttl == 0 for r in rs)
    assert rs[0].rtype == TYPE_PTR and rs[0].data == INSTANCE
    assert {r.rtype for r in rs} <= {TYPE_PTR, TYPE_SRV, TYPE_TXT, TYPE_A}
    assert only(rs, TYPE_SRV).data == (0, 0, PORT, HOST)


def test_goodbye_fits_in_a_packet_and_is_not_the_live_response() -> None:
    pkt = encode_goodbye(INSTANCE, HOST, PORT)
    assert len(pkt) <= MAX_PACKET
    assert pkt != response()


# ── RateLimiter ──────────────────────────────────────────────────────────────


def test_rate_limiter_allows_max_per_second_then_refuses_until_the_next_second() -> None:
    """같은 초 안에서 max_per_second 까지만 — 0초에 20개 허용 · 21번째 거부 · 0.999초까지 거부 ·
    1.0초에 다시 허용. 시각은 `allow(now)` 로 넣는다(단조 시계 초)."""
    rl = RateLimiter(20)
    assert [rl.allow(0.0) for _ in range(20)] == [True] * 20
    assert rl.allow(0.0) is False
    assert rl.allow(0.5) is False
    assert rl.allow(0.999) is False
    assert rl.allow(1.0) is True


def test_rate_limiter_with_max_one_is_one_per_second() -> None:
    rl = RateLimiter(1)
    assert rl.allow(0.0) is True
    assert rl.allow(0.9) is False
    assert rl.allow(1.0) is True
    assert rl.allow(1.5) is False
    assert rl.allow(2.0) is True


def test_rate_limiter_default_is_twenty_per_second() -> None:
    rl = RateLimiter()
    assert rl.max_per_second == MAX_ANSWERS_PER_SECOND == 20
    assert all(rl.allow(5.0) for _ in range(20)) and rl.allow(5.0) is False


def test_rate_limiter_spreads_a_slow_trickle_without_refusing() -> None:
    rl = RateLimiter(20)
    assert all(rl.allow(i * 0.1) for i in range(100))  # 초당 10개 — 절대 20 을 넘지 않는다


# ── Responder.handle (소켓 없이) ─────────────────────────────────────────────


def make_responder(
    *,
    clock: FakeClock | None = None,
    ips: list[str] | None = None,
    txt: dict[str, str] | None = None,
    log: Any = None,
    sock_factory: Any = None,
) -> Any:
    """`Responder(instance, host, port, *, ips_fn, txt_fn, log, mdns_port, sock_factory, clock)` —
    ips_fn/txt_fn 은 응답을 만들 때마다 불린다."""
    from remote_ci_monitor.mdns import Responder

    ips_list = list(IPS) if ips is None else ips
    txt_dict = dict(TXT) if txt is None else txt
    return Responder(
        INSTANCE,
        HOST,
        PORT,
        ips_fn=lambda: list(ips_list),
        txt_fn=lambda: dict(txt_dict),
        log=log or (lambda _msg: None),
        sock_factory=sock_factory,
        clock=clock or FakeClock(),
    )


def test_socket_api_shape_and_mdns_port_default() -> None:
    """`Responder(instance, host, port, *, …, mdns_port=5353, sock_factory=None, clock)` ·
    `discover(timeout=1.5, *, sock_factory=None, clock, mdns_port=5353)` — 테스트가 포트 15353 과
    가짜 소켓을 끼울 자리."""
    from remote_ci_monitor.mdns import Responder, discover

    r = inspect.signature(Responder).parameters
    assert list(r)[:3] == ["instance", "host", "port"]
    assert all(r[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(r)[3:])
    assert {"ips_fn", "txt_fn", "log", "mdns_port", "sock_factory", "clock"} <= set(r)
    assert r["mdns_port"].default == MDNS_PORT == 5353
    assert r["sock_factory"].default is None
    d = inspect.signature(discover).parameters
    assert list(d)[0] == "timeout" and d["timeout"].default == 1.5
    assert all(d[k].kind is inspect.Parameter.KEYWORD_ONLY for k in list(d)[1:])
    assert d["mdns_port"].default == 5353 and d["sock_factory"].default is None


def test_handle_answers_a_ptr_query_with_the_full_response() -> None:
    """PTR 질의 → `encode_response(instance, host, port, ips_fn(), txt_fn())` 바이트 그대로."""
    out = make_responder().handle(encode_query([(SERVICE, TYPE_PTR)]), ADDR)
    assert out == response()
    assert found_from_records(decode(out)[1]) == [MACMINI]


@pytest.mark.parametrize(
    ("qname", "qtype"),
    [(INSTANCE, TYPE_SRV), (INSTANCE, TYPE_TXT), (HOST, TYPE_A), (SERVICE, TYPE_ANY)],
)
def test_handle_answers_instance_and_host_questions_with_the_same_response(
    qname: str, qtype: int
) -> None:
    assert make_responder().handle(encode_query([(qname, qtype)]), ADDR) == response()


@pytest.mark.parametrize(
    "packet",
    [
        response(),
        encode_goodbye(INSTANCE, HOST, PORT),
        encode_query([("_http._tcp.local.", TYPE_PTR)]),
        encode_query([("other._rcm._tcp.local.", TYPE_SRV)]),
        EMPTY_HEADER,
        b"",
        b"garbage",
        b"\x00" * (MAX_PACKET + 1),
        query_packet(ptr(12)),
    ],
    ids=[
        "our-own-response",
        "goodbye",
        "other-service",
        "other-instance",
        "empty-header",
        "empty",
        "garbage",
        "oversize",
        "pointer-loop",
    ],
)
def test_handle_ignores_responses_unrelated_questions_and_garbage(packet: bytes) -> None:
    """QR=1 · 무관한 질문 · 못 읽는 패킷은 None — 예외 없이."""
    assert make_responder().handle(packet, ADDR) is None


def test_handle_copies_the_query_id_only_for_legacy_unicast_queries() -> None:
    """RFC 6762 §6.7: 5353 이 아닌 포트에서 온 질의(한 번 묻고 마는 질의기)에는 질의 id 를 되돌려
    준다. 5353 에서 온 질의(멀티캐스트 응답)는 id 0."""
    q = with_id(encode_query([(SERVICE, TYPE_PTR)]), 0x1234)
    legacy = make_responder().handle(q, LEGACY_ADDR)
    assert legacy is not None and header(legacy)[0] == 0x1234 and legacy[2:] == response()[2:]
    assert make_responder().handle(q, ADDR) == response()


def test_handle_reads_ips_and_txt_on_every_call() -> None:
    ips = ["192.0.2.10"]
    txt = {"v": "0.2.1"}
    r = make_responder(ips=ips, txt=txt)
    q = encode_query([(SERVICE, TYPE_PTR)])
    assert r.handle(q, ADDR) == response(ips=["192.0.2.10"], txt={"v": "0.2.1"})
    ips.append("192.0.2.11")
    txt["lanes"] = "3"
    assert r.handle(q, ADDR) == response(
        ips=["192.0.2.10", "192.0.2.11"], txt={"v": "0.2.1", "lanes": "3"}
    )


def test_handle_rate_limits_to_twenty_answers_per_second() -> None:
    clock = FakeClock()
    r = make_responder(clock=clock)
    q = encode_query([(SERVICE, TYPE_PTR)])
    assert all(r.handle(q, ADDR) is not None for _ in range(MAX_ANSWERS_PER_SECOND))
    assert r.handle(q, ADDR) is None
    clock.t = 0.5
    assert r.handle(q, ADDR) is None
    clock.t = 1.0
    assert r.handle(q, ADDR) is not None


def test_handle_ignored_packets_do_not_consume_the_answer_budget() -> None:
    r = make_responder()
    for _ in range(MAX_ANSWERS_PER_SECOND):
        assert r.handle(encode_query([("_http._tcp.local.", TYPE_PTR)]), ADDR) is None
        assert r.handle(b"garbage", ADDR) is None
    assert r.handle(encode_query([(SERVICE, TYPE_PTR)]), ADDR) is not None


def test_handle_returns_none_instead_of_raising_when_the_response_cannot_be_encoded() -> None:
    logs: list[str] = []
    r = make_responder(ips=[f"10.0.{i // 256}.{i % 256}" for i in range(40)], log=logs.append)
    assert r.handle(encode_query([(SERVICE, TYPE_PTR)]), ADDR) is None
    assert len(logs) == 1


def test_start_without_a_socket_is_a_logged_no_and_stop_is_quiet() -> None:
    """§2: 소켓을 못 열면(멀티캐스트 불가 등) 로그 한 줄 + `error` — 서버는 계속(발견은 부가 기능).
    그 뒤 `stop()` 은 조용하다."""
    logs: list[str] = []

    def no_socket() -> Any:
        raise OSError(49, "Can't assign requested address")

    r = make_responder(log=logs.append, sock_factory=no_socket)
    assert r.start() is False
    assert r.error and "Can't assign requested address" in r.error
    assert len(logs) == 1 and "advertise off" in logs[0]
    r.stop()
    r.stop()


def test_local_ipv4s_never_lists_loopback() -> None:
    """§1: A 레코드는 LAN IPv4 — 127.x 는 절대 없다(네트워크가 없으면 빈 목록)."""
    from remote_ci_monitor.mdns import local_ipv4s

    ips = local_ipv4s()
    assert isinstance(ips, list) and len(ips) == len(set(ips))
    for ip in ips:
        assert re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip) and not ip.startswith("127.")


# ── discover (가짜 소켓) ─────────────────────────────────────────────────────


class FakeSock:
    """`discover` 가 쓰는 소켓 표면 — settimeout · sendto · recvfrom · close.
    `recvfrom` 은 준비된 패킷을 하나씩 주고(시계 +0.1초), 다 떨어지면 시계를 크게 돌리고
    TimeoutError(= socket.timeout) 를 낸다."""

    def __init__(
        self, packets: list[bytes], clock: FakeClock, *, send_error: OSError | None = None
    ) -> None:
        self.packets = list(packets)
        self.clock = clock
        self.send_error = send_error
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, t: float | None) -> None:
        self.timeout = t

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((bytes(data), addr))
        return len(data)

    def recvfrom(self, bufsize: int) -> tuple[bytes, tuple[str, int]]:
        assert bufsize >= MAX_PACKET
        if self.packets:
            self.clock.t += 0.1
            return self.packets.pop(0), ("192.0.2.10", MDNS_PORT)
        self.clock.t += 10.0
        raise TimeoutError("timed out")

    def close(self) -> None:
        self.closed = True


def run_discover(packets: list[bytes], *, timeout: float = 1.5, **kw: Any) -> Any:
    from remote_ci_monitor.mdns import discover

    clock = FakeClock()
    sock = FakeSock(packets, clock)
    found = discover(timeout=timeout, sock_factory=lambda: sock, clock=clock, **kw)
    return found, sock


def test_discover_sends_the_ptr_query_to_the_group_and_collects_the_response() -> None:
    """질의 = `encode_query([(SERVICE, PTR)])` 를 (224.0.0.251, mdns_port) 로(재전송은 같은
    패킷, 최대 3회) · 응답 하나 → Found 하나 · 소켓 닫힘."""
    found, sock = run_discover([response()])
    assert found == [MACMINI]
    assert 1 <= len(sock.sent) <= 3
    assert {dest for _, dest in sock.sent} == {(MDNS_GROUP, MDNS_PORT)}
    assert {
        decode(data) == ([Question(SERVICE, TYPE_PTR, False)], []) for data, _ in sock.sent
    } == {True}
    assert sock.closed


def test_discover_honours_mdns_port() -> None:
    found, sock = run_discover([], mdns_port=15353)
    assert found == []
    assert sock.sent[0][1] == (MDNS_GROUP, 15353)


def test_discover_ignores_queries_garbage_oversize_other_services_and_duplicates() -> None:
    packets = [
        encode_query([(SERVICE, TYPE_PTR)]),  # 자기 질의의 루프백 사본
        b"garbage",
        b"",
        b"\x00" * 600,
        query_packet(ptr(12)),
        response(),
        response(),
        response(instance="x._http._tcp.local.", host="x.local."),
    ]
    found, _ = run_discover(packets)
    assert found == [MACMINI]


def test_discover_sorts_instances_by_name() -> None:
    """`found_from_records` 는 도착 순, `discover` 는 이름순 — 표와 문구가 안정되게."""
    bb = response(
        instance="buildbox._rcm._tcp.local.",
        host="buildbox.local.",
        port=8788,
        ips=["192.0.2.20"],
        txt={"v": "0.3.0", "lanes": "4"},
    )
    found, _ = run_discover([response(), bb])
    assert found == [
        Found("buildbox", "buildbox.local.", 8788, ("192.0.2.20",), "0.3.0", 4),
        MACMINI,
    ]


def test_discover_keeps_the_copy_that_has_ips_when_responses_disagree() -> None:
    """같은 서버의 응답 중 A 가 있는 쪽을 남긴다(A 없는 announce 가 먼저 와도)."""
    found, _ = run_discover([response(ips=[]), response()])
    assert found == [MACMINI]
    found, _ = run_discover([response(), response(ips=[])])
    assert found == [MACMINI]


def test_discover_returns_empty_when_no_socket_or_no_route() -> None:
    """발견은 부가 기능(§2) — 소켓을 못 만들거나 보낼 수 없으면 예외 대신 빈 목록."""
    from remote_ci_monitor.mdns import discover

    def no_socket() -> Any:
        raise OSError(1, "Operation not permitted")

    clock = FakeClock()
    assert discover(timeout=0.2, sock_factory=no_socket, clock=clock) == []
    sock = FakeSock([], clock, send_error=OSError(65, "No route to host"))
    assert discover(timeout=0.2, sock_factory=lambda: sock, clock=clock) == []
    assert sock.closed


def test_discover_stops_at_the_deadline_without_packets() -> None:
    found, sock = run_discover([])
    assert found == [] and sock.closed
