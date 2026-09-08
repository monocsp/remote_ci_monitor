"""문서 문면 잠금(M5c) — README 두 언어의 `rcm discover` · `advertise` · 「같은 네트워크면
`server` 를 비워도 된다」(Session machine) · Build machine 의 `advertise` · Security notes 의
「발견 응답에 비밀 없음」 · Session commands 표의 `rcm discover` 행, `examples/server.toml` 의
`advertise` 줄(주석 허용), CHANGELOG `[Unreleased]` 의 discovery. 명세는 docs/m5c-workplan.md §4.

test_docs_m5 처럼 정규식과 절 스캔만 한다. 구현보다 먼저 썼다(test-first) — 문서가 없으면 빨갛다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config
from test_docs_m5 import CHANGELOG, README, SERVER_TOML, has, paragraphs, read, unreleased
from test_docs_m5b import section, table_row

README_KO = README.parent / "README.ko.md"
READMES = [pytest.param(README, id="en"), pytest.param(README_KO, id="ko")]

# 「같은 네트워크」 · 「server」 · 「비워도 된다」 가 한 문단에 — 두 언어의 표현을 함께 받는다
SAME_NETWORK = r"same (network|Wi-?Fi|LAN)|같은 (네트워크|Wi-?Fi|LAN|와이파이|내부망)"
MAY_BE_EMPTY = r"\bempty\b|\bleave\b|\bomit|\bblank\b|비워|비우|생략|없어도|적지 않아도"


# ── README (en · ko) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", READMES)
@pytest.mark.parametrize("pattern", [r"rcm discover", r"\badvertise\b"])
def test_readme_mentions(path: Path, pattern: str):
    assert has(read(path), pattern), f"{path.name} lacks /{pattern}/"


@pytest.mark.parametrize("path", READMES)
def test_readme_session_machine_says_server_may_be_left_empty_on_the_same_network(path: Path):
    sec = section(read(path), "Session machine")
    ps = [p for p in paragraphs(sec) if has(p, SAME_NETWORK, re.I)]
    assert ps, f"{path.name}: Session machine never mentions the same network"
    hits = [p for p in ps if has(p, r"\bserver\b") and has(p, MAY_BE_EMPTY, re.I)]
    assert hits, f"{path.name}: no paragraph says `server` may be left empty on the same network"
    # 다른 네트워크는 Tailscale — 같은 절에서 말한다
    assert has(sec, r"Tailscale"), f"{path.name}: Session machine does not mention Tailscale"


@pytest.mark.parametrize("path", READMES)
def test_readme_build_machine_mentions_advertise(path: Path):
    sec = section(read(path), "Build machine")
    assert has(sec, r"\badvertise\b"), f"{path.name}: Build machine lacks `advertise`"


@pytest.mark.parametrize("path", READMES)
def test_readme_security_notes_say_the_discovery_response_carries_no_secret(path: Path):
    sec = section(read(path), "Security notes")
    ps = [p for p in paragraphs(sec) if has(p, r"advertise|discover|mDNS|발견|광고", re.I)]
    assert ps, f"{path.name}: Security notes never mention discovery/advertise"
    assert any(has(p, r"\btoken|\bsecret|토큰|비밀", re.I) for p in ps), ps


@pytest.mark.parametrize("path", READMES)
def test_readme_session_commands_table_has_a_discover_row(path: Path):
    row = table_row(section(read(path), "Session commands"), "discover")
    assert "--json" in row and "--timeout" in row, row


# ── examples/server.toml ─────────────────────────────────────────────────────


def test_example_server_toml_shows_advertise():
    # 주석이어도 되고 살아 있어도 된다 — 키 이름과 `=` 가 줄 머리에 있어야 한다
    text = read(SERVER_TOML)
    assert has(text, r"^\s*#?\s*advertise\s*=\s*(true|false)"), "no `advertise =` in server.toml"


def test_example_server_toml_still_loads():
    """예시는 실제로 읽힌다 — `advertise` 줄을 살려 두었다면 검증도 통과해야 한다."""
    cfg = load_server_config(SERVER_TOML, environ={}, check_tools=False)
    assert cfg.preset("ok") is not None


# ── CHANGELOG ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", [r"rcm discover", r"\badvertise\b", r"discover"])
def test_changelog_unreleased_mentions_discovery(pattern: str):
    sec = unreleased(read(CHANGELOG))
    assert has(sec, pattern, re.M | re.I), f"[Unreleased] lacks /{pattern}/"


def test_docs_paths_exist():
    for p in (README, README_KO, CHANGELOG, SERVER_TOML):
        assert Path(p).is_file(), p
