"""M5t-c 와이어 바이트 · 순수 함수(docs/m5tc-workplan.md §4.2 · §4.2.1 · §4.4, PR1).

`core/pairing.py` 의 계산을 시험 안의 독립 구현(`struct.pack(">I", …)` + `hmac.new`)과 맞춘다.
값은 실행 중에 만든다 — 커밋하는 리터럴은 §4.2.1 의 낮은 엔트로피 바이트뿐이다(§8 · gitleaks).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct

import pytest

from remote_ci_monitor import config as config_mod
from remote_ci_monitor.core import pairing as cp

KEY = bytes(range(32))  # §4.2.1 의 시험 입력 — 실제 키는 os 난수
PAIR_ID = "00112233445566778899aabbccddeeff"
NOW = 1764115200


def ref_enc(*fields: str) -> bytes:
    out = b""
    for f in fields:
        b = f.encode("utf-8")
        out += struct.pack(">I", len(b)) + b
    return out


def ref_mac(key: bytes, *fields: str) -> str:
    return hmac.new(key, ref_enc(*fields), hashlib.sha256).hexdigest()


def ref_k(key: bytes, pair_id: str) -> bytes:
    return hmac.new(key, ref_enc("rcm-pair-secret-v1", pair_id), hashlib.sha256).digest()


def nonce() -> str:
    return secrets.token_hex(32)


# ── PI33 인코딩 · MAC · 파생 ────────────────────────────────────────────────


def test_pi33_enc_matches_the_22_bytes_of_the_spec():
    want = bytes.fromhex("0000000c72636d2d68656c6c6f2d76310000000261 62".replace(" ", ""))
    assert cp.enc("rcm-hello-v1", "ab") == want
    assert len(want) == 22


def test_pi33_pair_mac_and_derive_match_an_independent_implementation():
    n = nonce()
    assert cp.pair_mac(KEY, "rcm-hello-v1", PAIR_ID, n) == ref_mac(KEY, "rcm-hello-v1", PAIR_ID, n)
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    assert k == ref_k(KEY, PAIR_ID) and len(k) == 32
    secret = cp.format_pair_secret(PAIR_ID, k)
    assert secret == f"{PAIR_ID}:{k.hex()}"
    assert cp.parse_pair_secret(secret) == (PAIR_ID, k)
    # 다른 pair_id 는 다른 K
    assert cp.derive_pair_secret(KEY, "f" * 32) != k


def test_pi33_pair_code_key_is_scrypt_and_not_sha256():
    want = hashlib.scrypt(
        b"ABCDEFGHJK",
        salt=b"rcm-pair-code-v1",
        n=2**15,
        r=8,
        p=1,
        maxmem=64 * 1024 * 1024,
        dklen=32,
    )
    got = cp.pair_code_key("ABCDEFGHJK")
    assert got == want
    assert got != hashlib.sha256(b"ABCDEFGHJK").digest()


def test_pi33_challenge_is_valid_only_inside_its_bounds():
    rand = secrets.token_bytes(8)
    c = cp.make_challenge(KEY, PAIR_ID, NOW, rand)
    t, r, mac = c.split(".")
    assert t == str(NOW) and r == rand.hex()
    assert mac == ref_mac(KEY, "rcm-challenge-v1", PAIR_ID, t, r)
    assert cp.check_challenge(KEY, PAIR_ID, c, NOW)
    assert cp.check_challenge(KEY, PAIR_ID, c, NOW + 120)  # 경계 안
    assert not cp.check_challenge(KEY, PAIR_ID, c, NOW + 121)  # 120초 지남
    assert cp.check_challenge(KEY, PAIR_ID, c, NOW - 5)  # 5초 미래까지는 받는다
    assert not cp.check_challenge(KEY, PAIR_ID, c, NOW - 6)  # 5초 넘게 미래
    assert cp.challenge_verdict(KEY, PAIR_ID, c, NOW + 121) == "expired"
    # 한 글자 바꿈 · 다른 pair_id 의 챌린지
    flipped = c[:-1] + ("0" if c[-1] != "0" else "1")
    assert cp.challenge_verdict(KEY, PAIR_ID, flipped, NOW) == "bad_mac"
    assert cp.challenge_verdict(KEY, "e" * 32, c, NOW) == "bad_mac"


def test_pi33_shape_checks_refuse_uppercase_hex_short_ids_and_short_nonces():
    good = {"v": 1, "pair_id": PAIR_ID, "nonce": nonce(), "dialed": "http://192.0.2.10:8787"}
    assert cp.hello_request_ok(good)
    for bad in (
        {**good, "pair_id": PAIR_ID[:31]},  # 31자
        {**good, "pair_id": PAIR_ID.upper()},
        {**good, "nonce": good["nonce"][:63]},  # 63자
        {**good, "nonce": good["nonce"].upper()},
        {**good, "v": True},
        {**good, "v": 2},
    ):
        assert not cp.hello_request_ok(bad), bad
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    c = cp.make_challenge(KEY, PAIR_ID, NOW, secrets.token_bytes(8))
    n, d = nonce(), "http://192.0.2.10:8787"
    mac = cp.hello_mac(k, PAIR_ID, n, d, c)
    assert cp.verify_hello(k, PAIR_ID, n, d, {"v": 1, "challenge": c, "mac": mac})
    assert not cp.verify_hello(k, PAIR_ID, n, d, {"v": 1, "challenge": c, "mac": mac.upper()})
    assert cp.parse_pair_secret(f"{PAIR_ID}:{k.hex().upper()}") is None


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("http://192.0.2.10", "http://192.0.2.10:80"),
        ("HTTP://Build-01.Local.:8787/x?y", "http://build-01.local:8787"),
        ("https://rcm.example.ts.net", "https://rcm.example.ts.net:443"),
        ("http://[::ffff:192.0.2.10]:8787", "http://192.0.2.10:8787"),
        ("http://[fe80::1%en0]:8787", "http://[fe80::1]:8787"),
        ("http://192.0.2.010:8787", None),
        ("ftp://h", None),
        ("http://u:p@h", None),
        ("http://h:0", None),
    ],
)
def test_pi33_canonical_dialed_table(url, want):
    assert cp.canonical_dialed(url) == want


def test_pi33_hello_mac_covers_the_challenge():
    """V1-13: 중계가 챌린지를 다른 유효 챌린지로 바꾸면 `verify_hello` 가 거짓이다."""
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    n, d = nonce(), "http://192.0.2.10:8787"
    c1 = cp.make_challenge(KEY, PAIR_ID, NOW, secrets.token_bytes(8))
    c2 = cp.make_challenge(KEY, PAIR_ID, NOW, secrets.token_bytes(8))
    mac = cp.hello_mac(k, PAIR_ID, n, d, c1)
    assert mac == ref_mac(k, "rcm-hello-v1", PAIR_ID, n, d, c1)
    assert cp.verify_hello(k, PAIR_ID, n, d, {"v": 1, "challenge": c1, "mac": mac})
    assert not cp.verify_hello(k, PAIR_ID, n, d, {"v": 1, "challenge": c2, "mac": mac})


def test_pi33_client_window_response_has_null_pool_and_count_that_the_mac_reads_as_empty():
    code_key = cp.pair_code_key("ABCDEFGHJK")
    wn, sn = nonce(), nonce()
    token = secrets.token_urlsafe(32)
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    secret = cp.format_pair_secret(PAIR_ID, k)
    mac = cp.exchange_response_mac(
        code_key,
        kind="client",
        host_name="laptop-1",
        worker_nonce=wn,
        server_nonce=sn,
        name="laptop-1",
        pool=None,
        presets_count=None,
        token=token,
        pair_secret=secret,
    )
    fields = ("client", "laptop-1", wn, sn, "laptop-1", "", "", token, secret)
    assert mac == ref_mac(code_key, "rcm-pair-response-v1", *fields)
    body = {
        "v": 1,
        "name": "laptop-1",
        "kind": "client",
        "pool": None,
        "presets_count": None,
        "token": token,
        "pair_secret": secret,
        "server_nonce": sn,
        "mac": mac,
    }
    assert cp.verify_exchange_response(code_key, "client", "laptop-1", wn, body)
    # 키가 빠지거나 칸에 틀린 타입이 오면 응답 검사가 거짓(V2-12)
    for broken in (
        {k2: v for k2, v in body.items() if k2 != "pool"},
        {**body, "presets_count": True},
        {**body, "presets_count": "3"},
        {**body, "v": True},
        {**body, "kind": "admin"},
    ):
        assert not cp.verify_exchange_response(code_key, "client", "laptop-1", wn, broken)


def test_pi33_worker_window_response_rejects_bool_and_string_counts():
    code_key = cp.pair_code_key("ABCDEFGHJK")
    wn, sn = nonce(), nonce()
    token = secrets.token_urlsafe(32)
    secret = cp.format_pair_secret(PAIR_ID, cp.derive_pair_secret(KEY, PAIR_ID))
    args = dict(kind="worker", host_name="build-01", worker_nonce=wn, server_nonce=sn)
    mac = cp.exchange_response_mac(
        code_key,
        **args,
        name="build-01",
        pool="default",
        presets_count=3,
        token=token,
        pair_secret=secret,
    )
    fields = ("worker", "build-01", wn, sn, "build-01", "default", "3", token, secret)
    assert mac == ref_mac(code_key, "rcm-pair-response-v1", *fields)
    body = {
        "v": 1,
        "name": "build-01",
        "kind": "worker",
        "pool": "default",
        "presets_count": 3,
        "token": token,
        "pair_secret": secret,
        "server_nonce": sn,
        "mac": mac,
    }
    assert cp.verify_exchange_response(code_key, "worker", "build-01", wn, body)
    for broken in ({**body, "presets_count": True}, {**body, "presets_count": "3"}):
        assert not cp.verify_exchange_response(code_key, "worker", "build-01", wn, broken)


# ── PI5 hello 중계 · 재생(순수 쪽) ──────────────────────────────────────────


def test_pi5_a_response_computed_for_another_dialed_or_an_old_nonce_does_not_verify():
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    real, fake = "http://192.0.2.10:8787", "http://192.0.2.66:8787"
    n = nonce()
    c = cp.make_challenge(KEY, PAIR_ID, NOW, secrets.token_bytes(8))
    # (b) 진짜 서버 주소로 고쳐 중계한 hello 의 응답 — 머신이 접속한 dialed(위장 주소)로는 거짓
    relayed = {"v": 1, "challenge": c, "mac": cp.hello_mac(k, PAIR_ID, n, real, c)}
    assert not cp.verify_hello(k, PAIR_ID, n, fake, relayed)
    assert cp.verify_hello(k, PAIR_ID, n, real, relayed)
    # (c) 옛 응답의 재생 — 새 nonce 로는 거짓
    assert not cp.verify_hello(k, PAIR_ID, nonce(), real, relayed)
    assert cp.classify_hello(200, relayed, k, PAIR_ID, nonce(), real) == "mismatch"


def test_pi5_classify_hello_sorts_the_answers():
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    n, d = nonce(), "http://192.0.2.10:8787"
    c = cp.make_challenge(KEY, PAIR_ID, NOW, secrets.token_bytes(8))
    good = {"v": 1, "challenge": c, "mac": cp.hello_mac(k, PAIR_ID, n, d, c)}
    assert cp.classify_hello(200, good, k, PAIR_ID, n, d) == "ok"
    assert cp.classify_hello(200, {**good, "v": 2}, k, PAIR_ID, n, d) == "other"
    assert cp.classify_hello(200, None, k, PAIR_ID, n, d) == "other"
    refused = {"code": "host_not_this_server", "error": "x"}
    assert cp.classify_hello(421, refused, k, PAIR_ID, n, d) == "refused_address"
    for status in (0, 429, 500, 503):
        assert cp.classify_hello(status, None, k, PAIR_ID, n, d) == "unavailable"
    for status in (301, 404, 405, 400):
        assert cp.classify_hello(status, None, k, PAIR_ID, n, d) == "other"


# ── PI22 서버의 자기 주소(순수) ──────────────────────────────────────────────


def test_pi22_own_dialed_set_from_socket_public_url_and_advertised_name():
    s = cp.own_dialed_set("127.0.0.1", 8790, None, None)
    assert s == {"http://127.0.0.1:8790", "http://localhost:8790"}
    s = cp.own_dialed_set("192.0.2.10", 8790, None, "Build-01")
    assert s == {"http://192.0.2.10:8790", "http://build-01.local:8790"}
    # (f) 루프백 bind + TLS 프록시의 public_url — https 443 이 그대로 들어간다
    # (1판의 :80 판정이 아니다)
    s = cp.own_dialed_set("127.0.0.1", 8790, "https://rcm.example.ts.net", None)
    assert "https://rcm.example.ts.net:443" in s
    assert "http://rcm.example.ts.net:80" not in s
    # (d) Tailscale 대역 IP + 이름 public_url — 주소는 문서용 192.0.2.x 로 대신한다(§8)
    s = cp.own_dialed_set("192.0.2.7", 8790, "http://rcm.example.ts.net:8790", None)
    assert s == {"http://192.0.2.7:8790", "http://rcm.example.ts.net:8790"}
    # 0.0.0.0 은 소켓 주소로 나오지 않는다 — 받은 연결의 로컬 주소만 든다
    assert all("0.0.0.0" not in x for x in cp.own_dialed_set("192.0.2.10", 1, None, "h"))


# ── PI9 코드 공간 ───────────────────────────────────────────────────────────


def test_pi9_the_online_guess_probability_matches_the_spec_number():
    assert len(cp.PAIR_CODE_ALPHABET) == 32 and cp.PAIR_CODE_LENGTH == 10
    p = cp.PAIR_WRONG_CODE_LIMIT / len(cp.PAIR_CODE_ALPHABET) ** cp.PAIR_CODE_LENGTH
    assert f"{p:.1e}" == "4.4e-15"


def test_pi9_codes_normalize_and_have_a_shape():
    assert cp.normalize_pair_code(" abcde-fghjk ") == "ABCDEFGHJK"
    assert cp.pair_code_shape_ok("ABCDEFGHJK")
    for bad in ("ABCDEFGHJ", "ABCDEFGHJ0", "ABCDEFGHJ1", "ABCDEFGHJ8", "ABCDEFGHJ9", "ABCDEFGHJ!"):
        assert not cp.pair_code_shape_ok(bad), bad
    assert cp.format_pair_code("ABCDEFGHJK") == "ABCDE-FGHJK"


# ── PI12 이름 정리 ─────────────────────────────────────────────────────────


def test_pi12_clean_pair_label_fits_the_name_rule():
    assert cp.clean_pair_label("Dev's MacBook Pro") == "Dev-s-MacBook-Pro"
    assert cp.clean_pair_label("x" * 70) == "x" * 64
    assert cp.clean_pair_label("빌드머신") == ""  # 한글뿐 → 대체 이름은 부르는 쪽
    tricky = cp.clean_pair_label("evil\nline\r\x1b[31m")
    assert "\n" not in tricky and "\r" not in tricky and "\x1b" not in tricky
    for raw in ("Dev's MacBook Pro", "x" * 70, "-.-host", "evil\nline"):
        label = cp.clean_pair_label(raw)
        assert label == "" or config_mod._NAME_RE.fullmatch(label), (raw, label)
    assert cp.PAIR_NAME_RE.pattern == config_mod._NAME_RE.pattern


# ── PI34 교환 증명 · 응답 MAC(순수 쪽) ──────────────────────────────────────


def test_pi34_a_response_from_a_server_that_does_not_know_the_code_fails_verification():
    real = cp.pair_code_key("ABCDEFGHJK")
    other = cp.pair_code_key("KJHGFEDCBA")
    wn, sn = nonce(), nonce()
    token = secrets.token_urlsafe(32)
    secret = cp.format_pair_secret(PAIR_ID, cp.derive_pair_secret(KEY, PAIR_ID))
    fields = dict(kind="worker", host_name="build-01", worker_nonce=wn, server_nonce=sn)
    payload = dict(name="build-01", pool="default", presets_count=3, token=token)
    body = {
        "v": 1,
        **payload,
        "kind": "worker",
        "pair_secret": secret,
        "server_nonce": sn,
        "mac": cp.exchange_response_mac(other, **fields, **payload, pair_secret=secret),
    }
    assert not cp.verify_exchange_response(real, "worker", "build-01", wn, body)
    body["mac"] = cp.exchange_response_mac(real, **fields, **payload, pair_secret=secret)
    assert cp.verify_exchange_response(real, "worker", "build-01", wn, body)
    # (b) kind 를 admin 으로 바꾼 응답
    assert not cp.verify_exchange_response(
        real, "worker", "build-01", wn, {**body, "kind": "admin"}
    )
    # 요청 증명에는 코드가 없다 — 코드 키 MAC 뿐이다
    proof = cp.exchange_proof(real, "worker", "build-01", wn)
    assert proof == ref_mac(real, "rcm-pair-request-v1", "worker", "build-01", wn)
    assert "ABCDEFGHJK" not in proof


def test_pi40_shape_check_refuses_every_malformed_exchange_body():
    good = {"v": 1, "kind": "worker", "host_name": "", "worker_nonce": nonce(), "proof": nonce()}
    assert cp.exchange_request_ok(good)
    for bad in (
        {**good, "host_name": 3},
        {**good, "host_name": None},
        {**good, "host_name": "x" * 65},
        {**good, "host_name": "\ud800"},
        {**good, "worker_nonce": nonce()[:63]},
        {**good, "worker_nonce": nonce().upper()},
        {**good, "proof": 7},
        {**good, "v": True},
        [good],
    ):
        assert not cp.exchange_request_ok(bad), bad
    with pytest.raises(ValueError):
        cp.loads_pair_body(b"[" * 1000)
    with pytest.raises(ValueError):
        cp.loads_pair_body(b"\xff\xfe")


# ── PI46 거절 검증(순수 쪽) ─────────────────────────────────────────────────


def test_pi46_verify_pair_reject_binds_nonce_body_and_key():
    k = cp.derive_pair_secret(KEY, PAIR_ID)
    n = nonce()
    sent = cp.body_sha256(b'{"pool":"default"}')
    mac = ref_mac(
        k, "rcm-pair-reject-v1", "POST", "/worker/register", "409", "pool_mismatch", "gpu", n, sent
    )
    body = {"error": "x", "code": "pool_mismatch", "reason": "gpu", "pair_reject_mac": mac}
    args = (k, "POST", "/worker/register", 409, body)
    assert cp.verify_pair_reject(*args, n, sent_body_sha256=sent)
    assert not cp.verify_pair_reject(*args, nonce(), sent_body_sha256=sent)  # 다른 nonce
    other = cp.body_sha256(b'{"pool":"gpu"}')
    assert not cp.verify_pair_reject(*args, n, sent_body_sha256=other)  # 바뀐 본문(342)
    k2 = cp.derive_pair_secret(KEY, "a" * 32)
    assert not cp.verify_pair_reject(k2, *args[1:], n, sent_body_sha256=sent)  # 다른 K
    no_key = {k3: v for k3, v in body.items() if k3 != "pair_reject_mac"}
    assert not cp.verify_pair_reject(
        k, "POST", "/worker/register", 409, no_key, n, sent_body_sha256=sent
    )
    assert not cp.verify_pair_reject(
        k, "POST", "/worker/register", 409, None, n, sent_body_sha256=sent
    )
    # 401 은 code · reason 이 빈 문자열
    empty = cp.body_sha256(b"")
    mac401 = ref_mac(k, "rcm-pair-reject-v1", "POST", "/worker/heartbeat", "401", "", "", n, empty)
    assert cp.pair_reject_mac(k, "POST", "/worker/heartbeat", 401, "", "", n, empty) == mac401
