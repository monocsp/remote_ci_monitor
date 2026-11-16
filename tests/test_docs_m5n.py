"""문서 문면 잠금(M5n) — 배치 · 점검 · 프로브 · 머신 명령 · 워커 붙이기가 문서와 예시에 있다.
명세 docs/m5n-workplan.md §4.1(프로브 출력이 읽기 라우트에 보인다) · §8.2 · §9 · §10 · §13.

`test_docs_m5` 처럼 정규식과 문단 스캔만 한다. **구현보다 먼저 썼다(test-first)** — 문서가 기능을
안 따라오면 빨갛다. 실패 메시지가 빠진 문구를 말한다.
"""

from __future__ import annotations

import re

import pytest

from remote_ci_monitor.config import load_server_config
from test_docs_m5 import (
    CHANGELOG,
    CONFIGURATION,
    README,
    README_KO,
    SERVER_TOML,
    docs,
    has,
    paragraphs,
    read,
    unreleased,
)


@pytest.mark.parametrize(
    "pattern",
    [
        r"`placement`",
        r'"any-capable"',
        r"`concurrency_scope`",
        r'"host"',
        r'"global"',
        r"`checks`",
        r"`check_ref`",
        r"\[\[probes\]\]",
        r"`sample`",
        r"`accept_any`",
        r"`local_accept_any`",
        r"`probe_interval_seconds`",
        r"`auto_checks`",
        r"rcm machines",
        r"rcm recheck",
    ],
)
def test_configuration_documents_each_m5n_key_and_command(pattern):
    assert has(read(CONFIGURATION), pattern), f"docs/configuration.md lacks /{pattern}/"


def test_configuration_warns_that_probe_output_is_visible_on_read_routes():
    """§4.1: `read_auth = "none"` 면 같은 망의 누구나 프로브 값을 본다.
    버전만 찍는 명령을 고르라고 문서가 말한다."""
    hits = [
        p
        for p in paragraphs(read(CONFIGURATION))
        if has(p, r"(?i)probe") and has(p, r"read_auth") and has(p, r"/api/machines|/api/status")
    ]
    assert hits, "configuration.md has no paragraph tying probe output to read_auth and the routes"
    assert any(has(p, r"(?i)version") for p in hits), hits


def test_configuration_says_any_capable_is_for_presets_without_secrets():
    """§13: 비밀 파일은 워커로 안 간다 — 배포·업로드 프리셋은 `pool` 배치로 남긴다."""
    hits = [
        p
        for p in paragraphs(read(CONFIGURATION))
        if has(p, r"any-capable") and has(p, r"(?i)secret")
    ]
    assert hits, "configuration.md does not say any-capable presets must not need secrets"


def test_configuration_says_a_check_preset_needs_git_ref():
    hits = [p for p in paragraphs(read(CONFIGURATION)) if has(p, r"`checks`") and has(p, "git_ref")]
    assert hits, "configuration.md does not say a check preset must accept git_ref"


@pytest.mark.parametrize(
    "pattern",
    [r"rcm init worker", r"--service-file", r"server_name", r'server = "auto"'],
)
def test_the_doc_set_covers_worker_onboarding(pattern):
    assert has(docs(), pattern), f"README/configuration/operating lack /{pattern}/"


@pytest.mark.parametrize("path", [README, README_KO], ids=["README", "README.ko"])
def test_readmes_mention_rcm_machines(path):
    assert has(read(path), r"rcm machines"), f"{path.name} lacks `rcm machines`"


# ── examples/server.toml ─────────────────────────────────────────────────────


def commented(text: str) -> str:
    """주석 줄만, `# ` 를 뗀 채로 — 주석 처리된 예시 블록을 읽는다."""
    return "\n".join(
        re.sub(r"^#\s?", "", ln) for ln in text.splitlines() if ln.lstrip().startswith("#")
    )


def test_server_example_has_a_commented_flutter_block_with_probes_and_checks():
    text = read(SERVER_TOML)
    body = commented(text)
    assert has(body, r"^\[\[probes\]\]"), "examples/server.toml lacks a commented [[probes]]"
    assert has(body, r"(?i)flutter"), "examples/server.toml lacks the Flutter example"
    assert has(body, r'^checks = \["'), "examples/server.toml lacks a `checks = [...]` example"
    assert has(body, r'^placement = "any-capable"'), "examples/server.toml lacks any-capable"
    assert has(body, r'^concurrency_scope = "host"'), "examples/server.toml lacks scope host"
    # 코드에 Flutter 를 안 박는다(§10) — 주석이 아닌 줄에는 프로브가 없다
    live = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert not has(live, r"^\[\[probes\]\]"), "the Flutter probes must stay commented out"


def test_server_example_still_loads():
    cfg = load_server_config(SERVER_TOML, check_tools=False)
    assert cfg.presets, "examples/server.toml has no presets"


def test_changelog_unreleased_mentions_the_m5n_surface():
    sec = unreleased(read(CHANGELOG))
    for pattern in (r"rcm machines", r"`placement`", r"`checks`", r"rcm init worker"):
        assert has(sec, pattern), f"CHANGELOG [Unreleased] lacks /{pattern}/"
