"""M5t-c 문서 잠금 — 짝짓기 · 서버 신원(docs/m5tc-workplan.md §4.7 · §7 PI20 · PI29 · PI30).

문서가 코드 · 시험과 같은 집합 · 같은 문장을 말하는지 본다. 뒤 PR(PR2~PR5)이 이 파일에 더한다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = (ROOT / "PLAN.md").read_text()
WORKPLAN = (ROOT / "docs" / "m5tc-workplan.md").read_text()
CONFIGURATION = (ROOT / "docs" / "configuration.md").read_text()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text()
PAIRING_SRC = (ROOT / "src" / "remote_ci_monitor" / "pairing.py").read_text()
PAIRING_TESTS = [ROOT / "tests" / "test_pairing_server.py", ROOT / "tests" / "test_pairing_wire.py"]


def section(text: str, heading: str) -> str:
    m = re.search(rf"^#{{2,4}} {re.escape(heading)}\s*$", text, re.M)
    assert m, f"no heading {heading!r}"
    rest = text[m.end() :]
    nxt = re.search(r"^#{2,4} ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def flat(text: str) -> str:
    return " ".join(text.split())


# ── PI20 익명 쓰기 ──────────────────────────────────────────────────────────


def test_pi20_plan_security_names_the_two_anonymous_pair_writes_and_points_at_the_table():
    body = flat(section(PLAN, "보안 (원격에서 명령을 실행시키는 서버다)"))
    assert "M5t-c 가 더한 익명 쓰기는 둘이다" in body
    assert "`POST /pair/hello`" in body and "`POST /pair/exchange`" in body
    assert "`docs/m5tc-workplan.md` §4.7 「익명 쓰기」 표" in body


def anonymous_writes_table() -> dict[str, set[tuple[str, str]]]:
    """§4.7 「익명 쓰기」 표 — `read_auth` 마다 (메서드, 라우트). 경로 변수는 `1` 로 채운다."""
    out: dict[str, set[tuple[str, str]]] = {}
    for line in section(WORKPLAN, "4.7 얼리는 계약").splitlines():
        m = re.match(r"^\| `(none|basic)` \| (.+?) \| [^|]+ \|$", line)
        if not m:
            continue
        routes = re.findall(r"`(POST|PUT|DELETE) ([^`]+)`", m.group(2))
        out[m.group(1)] = {(meth, path.replace("<n>", "1")) for meth, path in routes}
    return out


def test_pi20_the_workplan_table_is_the_set_the_server_test_probes():
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import test_pairing_server as probe
    finally:
        sys.path.remove(str(ROOT / "tests"))
    assert anonymous_writes_table() == probe.ANON_ACCEPTED


# ── PI29 문서 · 공개 레포 ───────────────────────────────────────────────────


def test_pi29_plan_server_api_lists_the_pair_routes_and_the_reject_mac():
    body = section(PLAN, "서버 API (`server.py`)")
    for route in ("`POST /pair/hello`", "`POST /pair/exchange`"):
        assert re.search(rf"^\| {re.escape(route)} \| 없음", body, re.M), route
    assert "| `POST /pair` · `GET /pair` · `POST /pair/cancel` | admin |" in body
    flat_body = flat(body)
    for code in (
        "host_not_this_server",
        "bad_hello",
        "bad_pair_request",
        "no_pairing_window",
        "name_taken",
        "pair_failed",
        "bad_pair_admin_request",
        "replace_needs_same_kind",
        "pair_window_open",
        "pair_unreachable",
        "no_open_window",
        "pair_unsupported",
    ):
        assert f"`{code}`" in flat_body, code
    assert "**거절 MAC**" in flat_body and "`X-RCM-Pair-Nonce`" in flat_body
    assert "`pair_reject_mac" in flat_body and "`_send_error` 한 곳" in flat_body


def test_pi29_plan_storage_has_the_v27_token_columns_and_pair_key():
    body = flat(section(PLAN, "저장소 (`store.py` · SQLite WAL · 표준 `sqlite3`)"))
    assert (
        "`tokens(name, sha256, admin, created_at, revoked_at, kind, pool, pair_id, paired_host, "
        "paired_addr)`" in body
    )
    assert "pair.key # 서버 신원 키(32바이트 · 0600 · DB 밖" in body


def test_pi29_configuration_explains_pair_key_with_the_words_the_server_prints():
    body = flat(section(CONFIGURATION, "The server's identity key: `pair.key`"))
    for words in (
        "pair.key is missing but N paired tokens are still active",
        "restore pair.key from the backup taken with this data_dir, or reset pairing",
        "does not support hard links",
        "(umask 077; head -c 32 /dev/urandom > <data_dir>/pair.key)",
        "chmod 600",
        "421 host_not_this_server",
    ):
        assert words in body, words
    # 서버가 실제로 찍는 문장 — 이어 붙인 문자열 조각을 합쳐 읽는다
    joined = re.sub(r'"\s*\n\s*f?"', "", PAIRING_SRC)
    for words in (
        "is missing but {active} paired tokens are still active — restore pair.key from the "
        "backup taken with this data_dir, or reset pairing",
        "does not support hard links",
        "(umask 077; head -c 32 /dev/urandom > {final})",
        "chmod 600",
    ):
        assert words in joined, words


def test_pi29_changelog_announces_v27_and_the_pairing_routes():
    unreleased = CHANGELOG.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    body = flat(unreleased)
    assert "**Database v27 and the server's identity key.**" in body
    assert "**The server side of pairing.**" in body
    assert "docs/configuration.md#the-servers-identity-key-pairkey" in body
    # P3 r1: 기동 거절의 셋째 원인 — docs/configuration.md 와 같은 말로
    assert "the filesystem of `data_dir` cannot make hard links" in body
    for heading in (
        "**Database v27 and the server's identity key.**",
        "**The server side of pairing.**",
    ):
        entry = body.split(heading, 1)[1].split(" - **", 1)[0]  # 다음 항목 앞까지(flat 본문)
        assert "([#293](https://github.com/monocsp/remote_ci_monitor/pull/293))" in entry, heading


def test_pi29_pairing_tests_hold_no_secret_shaped_literals_and_only_test_addresses():
    """§8: 시험 값은 실행 중에 만든다. 커밋하는 리터럴은 §4.2.1 의 낮은 엔트로피 바이트뿐이다."""
    for path in PAIRING_TESTS:
        text = path.read_text()
        assert not re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", text), path.name
        hex32 = set(re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", text))
        assert hex32 <= {"00112233445566778899aabbccddeeff"}, (path.name, hex32)
        for ip in re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text):
            assert ip == "0.0.0.0" or ip.startswith(("127.0.0.", "192.0.2.")), (path.name, ip)


# ── PI30 변이 ───────────────────────────────────────────────────────────────


def pr1_mutants_in_workplan() -> set[str]:
    body = section(WORKPLAN, "변이")
    names = set()
    for line in body.splitlines():
        m = re.match(r"^\| `([a-z0-9-]+)` \|.*\| (PR[0-9 ·]+) \|$", line)
        if m and "PR1" in m.group(2).split(" · "):
            names.add(m.group(1))
    return names


def test_pi30_every_pr1_mutant_of_the_workplan_is_in_mutcheck():
    spec = importlib.util.spec_from_file_location("mutcheck_m5tc", ROOT / "scripts" / "mutcheck.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mutcheck_m5tc"] = mod
    spec.loader.exec_module(mod)
    names = {m.name for m in mod.MUTANTS}
    wanted = pr1_mutants_in_workplan()
    assert len(wanted) == 27, sorted(wanted)
    assert wanted <= names, sorted(wanted - names)
