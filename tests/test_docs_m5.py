"""문서 문면 잠금(M5a) — README 의 `--priority` · `rcm bump` · high 기아 안내 · `snapshot_cache` ·
`--no-cache` · `[[notify]]` + `RCM_STATE`, `examples/server.toml` 의 주석 `[[notify]]` 예시와
`snapshot_cache*` 키, CHANGELOG `[Unreleased]` 의 priority · cache · notify.

test_release_files 처럼 정규식과 문단 스캔만 한다. 구현보다 먼저 썼다(test-first) — 문서가 없으면
빨갛다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
SERVER_TOML = ROOT / "examples" / "server.toml"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def read(path: Path) -> str:
    assert path.is_file(), f"missing: {path.relative_to(ROOT)}"
    return path.read_text()


def has(text: str, pattern: str, flags: int = re.M) -> bool:
    return re.search(pattern, text, flags) is not None


def paragraphs(text: str) -> list[str]:
    """빈 줄로 나눈 문단. 표의 한 행도 한 문단으로 본다(줄마다 나눠 붙인다)."""
    out: list[str] = []
    for block in re.split(r"\n[ \t]*\n", text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("|"):
            out.extend(ln for ln in block.splitlines() if ln.strip())
        else:
            out.append(block)
    return out


def unreleased(text: str) -> str:
    m = re.search(r"^## \[Unreleased\][^\n]*$", text, re.M)
    assert m, "no `## [Unreleased]`"
    rest = text[m.end() :]
    nxt = re.search(r"^## \[", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


# ── README ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "pattern",
    [
        r"--priority",
        r"rcm bump",
        r"snapshot_cache",
        r"--no-cache",
        r"\[\[notify\]\]",
        r"RCM_STATE",
    ],
)
def test_readme_mentions(pattern: str):
    assert has(read(README), pattern), f"README lacks /{pattern}/"


def test_readme_priority_paragraph_names_the_three_levels():
    ps = [p for p in paragraphs(read(README)) if "--priority" in p]
    assert ps, "no paragraph mentions --priority"
    assert any(all(w in p for w in ("high", "normal", "low")) for p in ps), ps


def test_readme_says_high_keeps_normal_waiting():
    """기아 안내(Codex 리뷰 M5 「고치면 좋은 것」 3): high 가 계속 오면 normal 은 기다린다."""
    ps = paragraphs(read(README))
    hits = [
        p
        for p in ps
        if "priority" in p.lower()
        and has(p, r"\bhigh\b")
        and has(p, r"\bnormal\b")
        and has(p, r"\bwait", re.I)
    ]
    assert hits, "README has no paragraph with 'high', 'normal' and 'wait' together"


def test_readme_bump_is_an_admin_command():
    ps = [p for p in paragraphs(read(README)) if "rcm bump" in p]
    assert ps, "no paragraph mentions rcm bump"
    assert any("admin" in p.lower() for p in ps), ps


def test_readme_no_cache_is_next_to_the_cache_explanation():
    ps = [p for p in paragraphs(read(README)) if "--no-cache" in p]
    assert ps, "no paragraph mentions --no-cache"
    detail = r"\bblob|sha256|changed"
    explained = [p for p in ps if has(p, r"\bcache", re.I) and has(p, detail, re.I)]
    assert explained, ps


def test_readme_notify_paragraph_shows_the_env_and_the_two_transports():
    text = read(README)
    ps = [p for p in paragraphs(text) if "[[notify]]" in p or "RCM_STATE" in p]
    assert ps
    joined = "\n".join(ps)
    assert "RCM_JOB_ID" in joined and "RCM_STATE" in joined, joined
    assert has(joined, r"\bargv\b") and has(joined, r"\burl\b"), joined


# ── examples/server.toml ─────────────────────────────────────────────────────


def test_example_server_toml_has_a_commented_notify_block():
    text = read(SERVER_TOML)
    assert has(text, r"^\s*#\s*\[\[notify\]\]"), "no commented [[notify]] example"
    assert has(text, r"^\s*#\s*name\s*="), text
    assert has(text, r"^\s*#\s*(argv|url)\s*="), text
    assert has(text, r"^\s*#\s*on\s*=\s*\["), text
    # 살아 있는(주석 아닌) [[notify]] 는 없다 — 예시 그대로 쓰는 서버가 없는 훅을 부르면 안 된다
    assert not has(text, r"^\s*\[\[notify\]\]")


@pytest.mark.parametrize(
    "key",
    ["snapshot_cache", "snapshot_cache_days", "snapshot_cache_max_bytes", "snapshot_cache_scope"],
)
def test_example_server_toml_shows_snapshot_cache_keys(key: str):
    # 주석이어도 되고 살아 있어도 된다 — 키 이름과 `=` 가 줄 머리에 있어야 한다
    assert has(read(SERVER_TOML), rf"^\s*#?\s*{key}\s*="), f"no {key} in examples/server.toml"


def test_example_server_toml_shows_preset_priority():
    assert has(read(SERVER_TOML), r'^\s*#?\s*priority\s*=\s*"(high|normal|low)"')


def test_example_server_toml_still_loads(tmp_path):
    """예시는 실제로 읽힌다(git 은 없어도 된다 — [[repos]] 예시는 주석)."""
    cfg = load_server_config(SERVER_TOML, environ={}, check_tools=False)
    assert cfg.preset("ok") is not None
    assert cfg.server.snapshot_cache in (True, False)
    assert cfg.notify == ()  # 예시의 [[notify]] 는 주석


def test_example_notify_block_is_valid_when_uncommented(tmp_path):
    """주석을 풀면 그대로 되는 예시여야 한다 — `presets` 가 주석 처리된 프리셋을 가리키면 빨갛다."""
    text = read(SERVER_TOML)
    m = re.search(r"^\s*#\s*\[\[notify\]\][^\n]*\n((?:\s*#[^\n]*\n)*)", text, re.M)
    assert m, "no commented [[notify]] example"
    block = ["[[notify]]"]
    for line in m.group(1).splitlines():
        stripped = re.sub(r"^\s*#\s?", "", line)
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue  # 설명 주석(`# # …`)·빈 줄은 건너뛴다
        if "=" not in stripped:
            break  # 다음 설명 문단이 시작됐다
        block.append(stripped)
    assert len(block) >= 3, block
    p = tmp_path / "server.toml"
    p.write_text(text + "\n" + "\n".join(block) + "\n")
    cfg = load_server_config(p, environ={}, check_tools=False)
    assert len(cfg.notify) == 1
    rule = cfg.notify[0]
    assert rule.name and (rule.argv or rule.url)


# ── CHANGELOG ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", [r"priorit", r"cache", r"notif"])
def test_changelog_unreleased_mentions_m5a(pattern: str):
    sec = unreleased(read(CHANGELOG))
    assert has(sec, pattern, re.M | re.I), f"[Unreleased] lacks /{pattern}/"


def test_changelog_unreleased_has_content_under_added():
    sec = unreleased(read(CHANGELOG))
    assert has(sec, r"^### Added"), sec
    assert has(sec, r"^- "), sec  # 항목이 최소 하나


def test_changelog_says_schema_keys_are_additive():
    """스키마 v1 의 기존 키는 그대로, 추가만(명세 「바꾸지 않는 것」) — 그 사실을 적는다."""
    sec = unreleased(read(CHANGELOG))
    assert has(sec, r"\bschema\b|additive|added key|new key", re.M | re.I), sec
