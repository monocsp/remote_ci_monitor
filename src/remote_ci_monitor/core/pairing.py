"""짝짓기와 서버 신원의 순수 계산(M5t-c · docs/m5tc-workplan.md §4.2 · §4.2.1 · §4.4).

입출력도 시계도 보지 않는다(PLAN.md 「core/ ── 순수」, F42). 시각(`now`, 유닉스 초)과 난수
바이트는 인자로 받는다. `secrets` · `time` · 파일 · 창 상태는 `remote_ci_monitor.pairing` 에 있다.

- `enc` · `pair_mac`: 모든 MAC 의 입력 인코딩. 필드마다 UTF-8 바이트 앞에 4바이트 big-endian 길이.
- `derive_pair_secret`: `pair.key` 와 `pair_id` 로 머신마다 다른 K(32바이트)를 만든다.
- 챌린지 · hello · 등록 증명 · 교환 증명 · 교환 응답 MAC · 거절 MAC(340 · 342 · 343).
- `canonical_dialed` · `own_dialed_set`: hello 의 `dialed` 규칙(308).
- `pair_code_key`: 코드를 scrypt 로 늘린 키(319 a). 표준 라이브러리 계산이라 순수다.

값(코드 · 코드 키 · 토큰 · `pair_secret` · `pair.key`)은 어디에도 찍지 않는다 — 부르는 쪽의 일이다.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import struct
from typing import Any
from urllib.parse import urlsplit

#: 용도 라벨 — `enc` 의 첫 필드(§4.2 「인코딩」)
LABEL_SECRET = "rcm-pair-secret-v1"
LABEL_HELLO = "rcm-hello-v1"
LABEL_CHALLENGE = "rcm-challenge-v1"
LABEL_REGISTER = "rcm-register-v1"
LABEL_REQUEST = "rcm-pair-request-v1"
LABEL_RESPONSE = "rcm-pair-response-v1"
LABEL_REJECT = "rcm-pair-reject-v1"

#: 챌린지가 유효한 과거 쪽 초(`now − 120 ≤ t`)와 미래 쪽 여유(`t ≤ now + 5`)
PAIR_CHALLENGE_SECONDS = 120
PAIR_CHALLENGE_FUTURE_SECONDS = 5
#: 교환 요청의 `host_name` 상한(코드 포인트, 319 b)
PAIR_HOST_NAME_MAX = 64
#: 익명 pair 라우트의 본문 상한(§4.2 hello 요청 · §4.4 교환 요청 · 관리 라우트)
PAIR_BODY_MAX = 1024
#: 코드: RFC 4648 base32 알파벳 10자(291 · 307)
PAIR_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
PAIR_CODE_LENGTH = 10
#: 창당 틀린 증명 상한(288). 다섯 번째 실패에 창을 닫는다
PAIR_WRONG_CODE_LIMIT = 5
#: 코드 키 scrypt 매개변수(319 a, M7) — 바꾸면 판이 다른 rcm 끼리 짝짓지 못한다(§4.7 얼린 계약)
PAIR_CODE_SALT = b"rcm-pair-code-v1"
PAIR_CODE_SCRYPT_N = 2**15
PAIR_CODE_SCRYPT_R = 8
PAIR_CODE_SCRYPT_P = 1
PAIR_CODE_SCRYPT_MAXMEM = 64 * 1024 * 1024
PAIR_CODE_KEY_BYTES = 32

PAIR_KINDS = ("worker", "client")

#: 형식 검사(§4.2 「형식 검사」). 대문자 hex 는 틀린 값이다 — 대소문자를 접지 않는다
PAIR_ID_RE = re.compile(r"^[0-9a-f]{32}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
CHALLENGE_RE = re.compile(r"^([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})$")
PAIR_SECRET_RE = re.compile(r"^([0-9a-f]{32}):([0-9a-f]{64})$")
WINDOW_ID_RE = re.compile(r"^[0-9a-f]{16}$")
#: 토큰 · 풀 이름 — `config._NAME_RE` 와 같은 꼴(core 는 config 를 부르지 않는다.
#: 시험이 둘을 맞춘다)
PAIR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_LABEL_BAD = re.compile(r"[^A-Za-z0-9._-]")
_HOSTNAME_RE = re.compile(r"^[a-z0-9._-]+$")
_DOTTED_RE = re.compile(r"^[0-9.]+$")
_LOOPBACK_V4 = ipaddress.IPv4Network("127.0.0.0/8")


# ── 인코딩과 MAC ─────────────────────────────────────────────────────────────


def enc(*fields: str) -> bytes:
    """필드마다 UTF-8 바이트 수(4바이트 big-endian 부호 없는 정수) + 그 바이트를 이어 붙인다.

    필드는 모두 `str` 이다. 정수는 부르는 쪽이 부호 · 앞의 0 없는 10진으로 바꿔 넣고, 값이 없는
    칸(null)은 빈 문자열로 넣는다(§4.2 「인코딩」). 홀로 선 서러게이트는 `UnicodeEncodeError` 다 —
    익명 입력은 그 전에 형식 검사가 거른다(319 b)."""
    out = bytearray()
    for f in fields:
        if not isinstance(f, str):
            raise TypeError(f"enc fields are str, not {type(f).__name__}")
        b = f.encode("utf-8")
        out += struct.pack(">I", len(b))
        out += b
    return bytes(out)


def pair_mac(key: bytes, label: str, *fields: str) -> str:
    """`HMAC-SHA256(key, enc(label, *fields))` 의 소문자 hex 64자."""
    return hmac.new(key, enc(label, *fields), hashlib.sha256).hexdigest()


def same_mac(a: Any, b: str) -> bool:
    """MAC · 증명 비교 — 형식이 맞는 문자열일 때만 `hmac.compare_digest`(상수 시간)."""
    return isinstance(a, str) and HEX64_RE.fullmatch(a) is not None and hmac.compare_digest(a, b)


def derive_pair_secret(key: bytes, pair_id: str) -> bytes:
    """K = `HMAC-SHA256(pair.key, enc("rcm-pair-secret-v1", pair_id))` — 32바이트(§4.2)."""
    return hmac.new(key, enc(LABEL_SECRET, pair_id), hashlib.sha256).digest()


def format_pair_secret(pair_id: str, k: bytes) -> str:
    """머신이 받는 값 `"<pair_id>:<K 의 소문자 hex 64자>"`."""
    return f"{pair_id}:{k.hex()}"


def parse_pair_secret(text: Any) -> tuple[str, bytes] | None:
    """`pair_secret` 을 (`pair_id`, K 바이트)로. 꼴이 틀리면 None.

    MAC 키는 hex 문자열이 아니라 바이트다."""
    if not isinstance(text, str):
        return None
    m = PAIR_SECRET_RE.fullmatch(text)
    if m is None:
        return None
    return m.group(1), bytes.fromhex(m.group(2))


def body_sha256(data: bytes) -> str:
    """거절 MAC 의 마지막 칸 — 본문 바이트의 sha256 소문자 hex. 본문이 없으면 `b""` 의 값(342)."""
    return hashlib.sha256(data).hexdigest()


# ── 챌린지 · hello · 등록 증명 ──────────────────────────────────────────────


def make_challenge(key: bytes, pair_id: str, now: int, rand: bytes) -> str:
    """`"<t>.<r>.<pair_mac(pair.key, "rcm-challenge-v1", pair_id, t, r)>"` — t 는 유닉스 초, r 은
    8바이트 난수의 hex 16자. 서버는 메모리 상태 없이 MAC 으로 검증한다(§4.2 「챌린지」)."""
    if len(rand) != 8:
        raise ValueError("challenge randomness is 8 bytes")
    t, r = str(int(now)), rand.hex()
    return f"{t}.{r}.{pair_mac(key, LABEL_CHALLENGE, pair_id, t, r)}"


def _challenge_parts(c: Any) -> tuple[int, str, str] | None:
    if not isinstance(c, str):
        return None
    m = CHALLENGE_RE.fullmatch(c)
    if m is None:
        return None
    return int(m.group(1)), m.group(2), m.group(3)


def challenge_time(c: Any) -> int | None:
    """형식이 맞는 챌린지의 `t`. 아니면 None."""
    parts = _challenge_parts(c)
    return None if parts is None else parts[0]


def challenge_fresh(t: int, now: int) -> bool:
    """`now − 120 ≤ t ≤ now + 5`."""
    return now - PAIR_CHALLENGE_SECONDS <= t <= now + PAIR_CHALLENGE_FUTURE_SECONDS


def challenge_verdict(key: bytes, pair_id: str, c: Any, now: int) -> str | None:
    """챌린지의 판정. 통과면 None, 형식 · MAC 이 틀리면 `bad_mac`, 시각이 범위 밖이면 `expired`.
    순서가 계약이다: 형식 → MAC → 시각(§4.2 「등록 증명」 ①~③)."""
    parts = _challenge_parts(c)
    if parts is None:
        return "bad_mac"
    t, r, mac = parts
    if not hmac.compare_digest(mac, pair_mac(key, LABEL_CHALLENGE, pair_id, str(t), r)):
        return "bad_mac"
    if not challenge_fresh(t, now):
        return "expired"
    return None


def check_challenge(key: bytes, pair_id: str, c: Any, now: int) -> bool:
    """챌린지가 그 `pair_id` 의 것이고 지금 유효한가."""
    return challenge_verdict(key, pair_id, c, now) is None


def hello_mac(k: bytes, pair_id: str, nonce: str, dialed: str, challenge: str) -> str:
    """hello 200 의 `mac` — MAC 이 챌린지를 덮는다(V1-13)."""
    return pair_mac(k, LABEL_HELLO, pair_id, nonce, dialed, challenge)


def verify_hello(k: bytes, pair_id: str, nonce: str, dialed: str, body: Any) -> bool:
    """머신의 hello 검증 — 본문이 객체 · `v == 1` · `challenge` · `mac` 형식이 맞고
    MAC 이 같을 때만 참."""
    if not isinstance(body, dict) or not _is_one(body.get("v")):
        return False
    c, mac = body.get("challenge"), body.get("mac")
    if _challenge_parts(c) is None or not isinstance(mac, str) or not HEX64_RE.fullmatch(mac):
        return False
    return hmac.compare_digest(mac, hello_mac(k, pair_id, nonce, dialed, c))


def classify_hello(status: int, body: Any, k: bytes, pair_id: str, nonce: str, dialed: str) -> str:
    """hello 응답의 분류(§4.3 판정 표 J1~J5). 이 이름은 이 함수 안에서만 쓴다 — 화면 · JSON · 로그에
    그대로 내보내지 않는다. `status` 0 은 연결 실패 · 시한 초과다."""
    if status == 200:
        if not isinstance(body, dict) or not _is_one(body.get("v")):
            return "other"
        c, mac = body.get("challenge"), body.get("mac")
        if _challenge_parts(c) is None or not isinstance(mac, str) or not HEX64_RE.fullmatch(mac):
            return "other"
        return "ok" if verify_hello(k, pair_id, nonce, dialed, body) else "mismatch"
    if status == 421 and isinstance(body, dict) and body.get("code") == "host_not_this_server":
        return "refused_address"
    if status == 0 or status == 429 or 500 <= status <= 599:
        return "unavailable"
    return "other"


def register_proof_mac(k: bytes, pair_id: str, challenge: str, worker_session_id: str) -> str:
    """등록 본문 `pair_proof.mac`.

    `pair_mac(K, "rcm-register-v1", pair_id, c, worker_session_id)`."""
    return pair_mac(k, LABEL_REGISTER, pair_id, challenge, worker_session_id)


def register_proof_verdict(
    key: bytes, pair_id: str, challenge: Any, mac: Any, worker_session_id: Any, now: int
) -> str | None:
    """등록 증명 ①~④(313): 형식 → 챌린지 MAC → 시각 → 증명 MAC. 통과면 None, 아니면 `bad_mac` ·
    `expired`. K 는 **토큰의** `pair_id` 로 만든다 — 본문의 어떤 값도 `pair_id` 로 쓰지 않는다.
    쓴 챌린지(⑤ · ⑥)는 상태를 쥔 쪽(`pairing.ChallengeLedger`)이 본다."""
    if _challenge_parts(challenge) is None or not isinstance(mac, str):
        return "bad_mac"
    if not HEX64_RE.fullmatch(mac):
        return "bad_mac"
    verdict = challenge_verdict(key, pair_id, challenge, now)
    if verdict is not None:
        return verdict
    session = worker_session_id if isinstance(worker_session_id, str) else ""
    k = derive_pair_secret(key, pair_id)
    if not hmac.compare_digest(mac, register_proof_mac(k, pair_id, challenge, session)):
        return "bad_mac"
    return None


# ── dialed ──────────────────────────────────────────────────────────────────


def canonical_dialed(url: Any) -> str | None:
    """머신이 접속한 URL 의 정규형 `"<scheme>://<host>:<port>"` — 양쪽이 같은 함수를 쓴다(308).

    스킴은 `http` · `https` 만, userinfo 가 있으면 None, 호스트는 소문자 · 끝의 점 제거, IPv6 는
    zone id 를 떼고 IPv4-mapped 는 IPv4 로, IPv4 는 앞의 0 이 있으면 None, 포트가 없으면 80 · 443,
    1~65535 밖이면 None, 경로 · 질의 · 조각은 버린다(§4.2.1 표)."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    netloc = parts.netloc
    if "@" in netloc:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = 80 if scheme == "http" else 443
    if not 1 <= port <= 65535:
        return None
    hostport = netloc
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0:
            return None
        raw = hostport[1:end].split("%", 1)[0]
        try:
            ip6 = ipaddress.IPv6Address(raw)
        except ValueError:
            return None
        host = str(ip6.ipv4_mapped) if ip6.ipv4_mapped else f"[{ip6.compressed}]"
    else:
        host = hostport.rsplit(":", 1)[0] if ":" in hostport else hostport
        host = host.lower().rstrip(".")
        if not host:
            return None
        if _DOTTED_RE.fullmatch(host):
            try:
                host = str(ipaddress.IPv4Address(host))
            except ValueError:
                return None
        elif not _HOSTNAME_RE.fullmatch(host):
            return None
    return f"{scheme}://{host}:{port}"


def _bracketed(ip: str) -> str:
    return f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip


def own_dialed_set(
    sock_ip: str, sock_port: int, public_url: str | None, advertise_host: str | None
) -> frozenset[str]:
    """서버가 자기 주소로 받는 `dialed` 집합(§4.2 「서버의 자기 주소」).

    ① 이 요청이 도착한 소켓의 로컬 주소 ② 그것이 127.0.0.0/8 이면 `localhost` 도 ③ `public_url`
    (스킴 그대로) ④ 광고가 켜져 있으면 `<짧은 호스트 이름>.local`. 보관함 Host 허용 목록과
    `local_ipv4s()` 는 쓰지 않는다(F14 · F15) — 같은 망의 다른 주소를 받으면 중계를 못 막는다."""
    out: set[str] = set()
    sock = canonical_dialed(f"http://{_bracketed(sock_ip)}:{sock_port}")
    if sock is not None:
        out.add(sock)
        host = sock.split("://", 1)[1].rsplit(":", 1)[0]
        try:
            if ipaddress.IPv4Address(host) in _LOOPBACK_V4:
                out.add(f"http://localhost:{sock_port}")
        except ValueError:
            pass
    if public_url:
        pub = canonical_dialed(public_url)
        if pub is not None:
            out.add(pub)
    if advertise_host:
        adv = canonical_dialed(f"http://{advertise_host.lower()}.local:{sock_port}")
        if adv is not None:
            out.add(adv)
    return frozenset(out)


# ── 요청 본문 ───────────────────────────────────────────────────────────────


def loads_pair_body(data: bytes) -> Any:
    """익명 pair 라우트의 본문 파싱 — UTF-8 이 아님 · JSON 이 아님 · 깊이 초과를 모두 `ValueError`
    하나로. `RecursionError` 는 `ValueError` 가 아니라서 따로 잡는다
    (M8 · F63 — 500 을 내지 않는다)."""
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise ValueError("not a JSON body") from e


def _is_one(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v == 1


def _hex64(v: Any) -> bool:
    return isinstance(v, str) and HEX64_RE.fullmatch(v) is not None


def hello_request_ok(body: Any) -> bool:
    """`{"v": 1, "pair_id": <hex32>, "nonce": <hex64>, "dialed": <str>}` 꼴인가."""
    return (
        isinstance(body, dict)
        and _is_one(body.get("v"))
        and isinstance(body.get("pair_id"), str)
        and PAIR_ID_RE.fullmatch(body["pair_id"]) is not None
        and _hex64(body.get("nonce"))
        and isinstance(body.get("dialed"), str)
        and 0 < len(body["dialed"]) <= 300
    )


def host_name_ok(v: Any) -> bool:
    """`host_name`: 문자열 · 0~64 코드 포인트 · 엄격한 UTF-8(홀로 선 서러게이트 없음, 319 b)."""
    if not isinstance(v, str) or len(v) > PAIR_HOST_NAME_MAX:
        return False
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def exchange_request_ok(body: Any) -> bool:
    """교환 요청의 형식(§4.4 「교환 요청」). `kind` 가 아는 값인지는 보지 않는다 — 형식이 맞는
    모르는 kind 는 같은 404 다."""
    return (
        isinstance(body, dict)
        and _is_one(body.get("v"))
        and isinstance(body.get("kind"), str)
        and host_name_ok(body.get("host_name"))
        and _hex64(body.get("worker_nonce"))
        and _hex64(body.get("proof"))
    )


# ── 코드 · 이름 ─────────────────────────────────────────────────────────────


def normalize_pair_code(text: str) -> str:
    """사람이 친 코드 — 대문자로, `-` 와 공백을 뺀다. 0→O 같은 바꿈은 하지 않는다(307)."""
    return "".join(ch for ch in text.upper() if ch != "-" and not ch.isspace())


def pair_code_shape_ok(code: str) -> bool:
    """정규화한 코드가 10자 base32 알파벳인가(307)."""
    return len(code) == PAIR_CODE_LENGTH and all(ch in PAIR_CODE_ALPHABET for ch in code)


def format_pair_code(code: str) -> str:
    """화면 꼴 `ABCDE-FGHJK`."""
    return f"{code[:5]}-{code[5:]}"


def scrypt_available() -> bool:
    """`hashlib.scrypt` 가 있는가(F69 — OpenSSL 1.1+ 로 빌드한 파이썬에만 있다)."""
    return hasattr(hashlib, "scrypt")


def pair_code_key(code: str) -> bytes:
    """코드 키 = `scrypt(코드 ASCII, salt "rcm-pair-code-v1", n 2^15, r 8, p 1, maxmem 64 MiB, 32)`
    (319 a, M7). 서버는 창을 열 때 한 번, 워커는 setup 에서 한 번 부른다 — 익명 교환은 부르지
    않는다. `hashlib.scrypt` 가 없으면 `AttributeError` 다: 부르는 쪽이 `scrypt_available()` 로
    먼저 본다."""
    return hashlib.scrypt(
        code.encode("ascii"),
        salt=PAIR_CODE_SALT,
        n=PAIR_CODE_SCRYPT_N,
        r=PAIR_CODE_SCRYPT_R,
        p=PAIR_CODE_SCRYPT_P,
        maxmem=PAIR_CODE_SCRYPT_MAXMEM,
        dklen=PAIR_CODE_KEY_BYTES,
    )


def clean_pair_label(host_name: str) -> str:
    """`host_name` 을 이름 꼴로 — `_NAME_RE` 밖 글자는 `-`, 앞의 영숫자 아닌 글자는 지우고 64자로
    자른다. 남는 글자가 없으면 빈 문자열(대체 이름은 부르는 쪽이 짓는다, §4.4 「이름」). 로그 ·
    목록 · `tokens.paired_host` 에는 원문이 아니라 이 값만 싣는다(V2-14)."""
    # 바꾼 뒤에는 ASCII 영숫자와 `._-` 만 남는다 — 앞의 `._-` 를 지우면 첫 글자가 영숫자다
    return _LABEL_BAD.sub("-", host_name).lstrip("._-")[:64]


# ── 교환 ────────────────────────────────────────────────────────────────────


def exchange_proof(code_key: bytes, kind: str, host_name: str, worker_nonce: str) -> str:
    """교환 요청의 `proof` — 코드를 보내지 않고 코드 키로 만든 증명(301)."""
    return pair_mac(code_key, LABEL_REQUEST, kind, host_name, worker_nonce)


def _opt_text(v: Any) -> str:
    return "" if v is None else str(v)


def exchange_response_mac(
    code_key: bytes,
    *,
    kind: str,
    host_name: str,
    worker_nonce: str,
    server_nonce: str,
    name: str,
    pool: str | None,
    presets_count: int | None,
    token: str,
    pair_secret: str,
) -> str:
    """교환 200 의 `mac` — 두 nonce 와 전체 페이로드를 덮는다. null 칸은 빈 문자열(§4.2.1)."""
    return pair_mac(
        code_key,
        LABEL_RESPONSE,
        kind,
        host_name,
        worker_nonce,
        server_nonce,
        name,
        _opt_text(pool),
        _opt_text(presets_count),
        token,
        pair_secret,
    )


_EXCHANGE_KEYS = (
    "v",
    "name",
    "kind",
    "pool",
    "presets_count",
    "token",
    "pair_secret",
    "server_nonce",
    "mac",
)


def verify_exchange_response(
    code_key: bytes, kind: str, host_name: str, worker_nonce: str, body: Any
) -> bool:
    """머신이 저장하기 **전에** 보는 교환 응답 검사(§4.2.1 끝 단락). 하나라도 어긋나면 거짓 —
    MAC 이 틀린 것과 같이 다룬다."""
    if not isinstance(body, dict) or any(k not in body for k in _EXCHANGE_KEYS):
        return False
    if not _is_one(body["v"]) or body["kind"] != kind:
        return False
    name, pool, count = body["name"], body["pool"], body["presets_count"]
    if not isinstance(name, str) or not PAIR_NAME_RE.fullmatch(name):
        return False
    if kind == "worker":
        if not isinstance(pool, str) or not PAIR_NAME_RE.fullmatch(pool):
            return False
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return False
    elif pool is not None or count is not None:
        return False
    token, secret = body["token"], body["pair_secret"]
    if not isinstance(token, str) or not token or parse_pair_secret(secret) is None:
        return False
    if not _hex64(body["server_nonce"]) or not _hex64(body["mac"]):
        return False
    want = exchange_response_mac(
        code_key,
        kind=kind,
        host_name=host_name,
        worker_nonce=worker_nonce,
        server_nonce=body["server_nonce"],
        name=name,
        pool=pool,
        presets_count=count,
        token=token,
        pair_secret=secret,
    )
    return hmac.compare_digest(body["mac"], want)


# ── 거절 MAC(340 · 342 · 343) ────────────────────────────────────────────────


def pair_reject_mac(
    k: bytes,
    method: str,
    canonical_path: str,
    status: int,
    code: str,
    reason: str,
    nonce: str,
    body_hash: str,
) -> str:
    """`pair_mac(K, "rcm-pair-reject-v1", method, canonical_path, status, code, reason, nonce,
    <본문의 sha256>)`. 없는 `code` · `reason` 은 빈 문자열(401 은 둘 다 빈 문자열)."""
    return pair_mac(
        k, LABEL_REJECT, method, canonical_path, str(int(status)), code, reason, nonce, body_hash
    )


def verify_pair_reject(
    k: bytes,
    method: str,
    canonical_path: str,
    status: int,
    body: Any,
    nonce: str,
    *,
    sent_body_sha256: str,
) -> bool:
    """머신의 거절 검증(§4.2 「머신의 거절 검증」). 참이 아닌 모든 경우 — 키 없음 · 꼴 틀림 · 다른
    nonce · 다른 본문 · 다른 K · 파싱 실패 — 는 종료 증거가 아니다. `nonce` 는 그 요청에 실어 보낸
    값 하나, `sent_body_sha256` 은 보낸 본문의 sha256 이다."""
    if not isinstance(body, dict):
        return False
    got = body.get("pair_reject_mac")
    if not _hex64(got):
        return False
    code = body.get("code") if isinstance(body.get("code"), str) else ""
    reason = body.get("reason") if isinstance(body.get("reason"), str) else ""
    want = pair_reject_mac(k, method, canonical_path, status, code, reason, nonce, sent_body_sha256)
    return hmac.compare_digest(got, want)


__all__ = [
    "CHALLENGE_RE",
    "HEX64_RE",
    "PAIR_BODY_MAX",
    "PAIR_CHALLENGE_SECONDS",
    "PAIR_CODE_ALPHABET",
    "PAIR_HOST_NAME_MAX",
    "PAIR_ID_RE",
    "PAIR_KINDS",
    "PAIR_NAME_RE",
    "PAIR_SECRET_RE",
    "PAIR_WRONG_CODE_LIMIT",
    "body_sha256",
    "canonical_dialed",
    "challenge_verdict",
    "check_challenge",
    "classify_hello",
    "clean_pair_label",
    "derive_pair_secret",
    "enc",
    "exchange_proof",
    "exchange_request_ok",
    "exchange_response_mac",
    "format_pair_code",
    "format_pair_secret",
    "hello_mac",
    "hello_request_ok",
    "loads_pair_body",
    "make_challenge",
    "normalize_pair_code",
    "own_dialed_set",
    "pair_code_key",
    "pair_code_shape_ok",
    "pair_mac",
    "pair_reject_mac",
    "parse_pair_secret",
    "register_proof_mac",
    "register_proof_verdict",
    "same_mac",
    "scrypt_available",
    "verify_exchange_response",
    "verify_hello",
    "verify_pair_reject",
]
