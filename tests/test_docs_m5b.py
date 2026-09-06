"""문서 문면 잠금(M5b-1) — README 「Session commands」 표의 `--pool`(run · eta · jobs), 「Presets」
절의 `pool =` / `pools =`(기본 `"default"`), `examples/server.toml` 의 `pool` 예시(주석 허용),
CHANGELOG `[Unreleased]` 의 pools. 명세는 docs/m5-workplan.md 「M5b. 원격 워커」 「모델」.

test_docs_m5 처럼 정규식과 절 스캔만 한다. 구현보다 먼저 썼다(test-first) — 문서가 없으면 빨갛다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_docs_m5 import CHANGELOG, README, SERVER_TOML, has, read, unreleased


def section(text: str, heading: str) -> str:
    """`## <heading>` 부터 다음 `## ` 앞까지."""
    m = re.search(rf"^## {re.escape(heading)}[^\n]*$", text, re.M)
    assert m, f"no `## {heading}`"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def table_row(text: str, command: str) -> str:
    """표에서 첫 칸이 `rcm <command>` 로 시작하는 행."""
    rows = [ln for ln in text.splitlines() if ln.startswith("| `rcm " + command)]
    assert rows, f"no table row for rcm {command}"
    return rows[0]


# ── README ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["run", "eta", "jobs"])
def test_readme_session_commands_table_mentions_pool(command: str):
    row = table_row(section(read(README), "Session commands"), command)
    assert "--pool" in row, row


def test_readme_presets_section_shows_pool_and_pools_keys():
    sec = section(read(README), "Presets and step markers")
    assert has(sec, r"\bpool\s*="), "no `pool =` in the Presets section"
    assert has(sec, r"\bpools\s*=\s*\["), "no `pools = [...]` in the Presets section"
    assert has(sec, r'"default"'), "the default pool name is not stated"


def test_readme_explains_that_a_session_picks_a_pool_the_preset_allows():
    """`--pool` 은 프리셋의 `pools` 안에서만 — 그 관계를 한 문단에서 말한다."""
    ps = [p for p in re.split(r"\n[ \t]*\n", read(README)) if "--pool" in p]
    assert ps, "no paragraph mentions --pool"
    assert any(has(p, r"\bpools\b") and has(p, r"\bpreset", re.I) for p in ps), ps


# ── examples/server.toml ─────────────────────────────────────────────────────


def test_example_server_toml_shows_a_pool_example():
    # 주석이어도 되고 살아 있어도 된다 — 키 이름과 `=` 가 줄 머리에 있어야 한다
    text = read(SERVER_TOML)
    assert has(text, r'^\s*#?\s*pool\s*=\s*"'), 'no `pool = "…"` in examples/server.toml'
    assert has(text, r"^\s*#?\s*pools\s*=\s*\["), "no `pools = [...]` in examples/server.toml"


# ── CHANGELOG ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", [r"\bpools?\b", r"--pool"])
def test_changelog_unreleased_mentions_pools(pattern: str):
    sec = unreleased(read(CHANGELOG))
    assert has(sec, pattern, re.M | re.I), f"[Unreleased] lacks /{pattern}/"


def test_changelog_says_pool_keys_are_additive_to_the_status_document():
    """`queue[].pool` · `presets[].pool/pools` · `pools[]` 다중화는 추가 키 — 스키마 버전 그대로."""
    sec = unreleased(read(CHANGELOG))
    hits = [p for p in re.split(r"\n(?=- )", sec) if has(p, r"\bpool", re.I)]
    assert hits, sec
    assert any(has(p, r"presets\[\]\.pool|queue\[\]\.pool|pools\[\]", re.I) for p in hits), hits


def test_docs_paths_exist():
    for p in (README, CHANGELOG, SERVER_TOML):
        assert Path(p).is_file(), p
