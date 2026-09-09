"""문서 문면 잠금(M5h) — 새 마커 `::rcm::fail::` · 「선언된 것만」 규칙 · 이름별 최근 이력 ·
메모리가 빠듯한 기계의 패턴 · 설정 키 둘. 명세는 `docs/m5h-implementation.md`.

`test_docs_m5` 처럼 정규식과 절 스캔만 한다. **문서가 기능을 안 따라오면 빨갛다** — 이
마일스톤은 프리셋 스크립트가 마커를 찍어 줘야 값이 생기므로, 문서가 곧 기능의 절반이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remote_ci_monitor.config import load_server_config
from test_docs_m5 import CHANGELOG, SERVER_TOML, has, read, unreleased

ROOT = Path(__file__).resolve().parent.parent
USAGE = ROOT / "docs" / "usage.md"
USAGE_KO = ROOT / "docs" / "usage.ko.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"
GATE_SH = ROOT / "examples" / "session" / "ci-gate.sh"
USAGES = [pytest.param(USAGE, id="en"), pytest.param(USAGE_KO, id="ko")]

#: 새 마커는 문서에 이 모양 그대로 나온다 — 스크립트 작성자가 복사한다
FAIL_MARKER = r"::rcm::fail::"


@pytest.mark.parametrize("path", [CONFIGURATION, *USAGES])
def test_the_new_marker_is_documented(path: Path):
    assert has(read(path), FAIL_MARKER), f"{path.name} lacks {FAIL_MARKER}"


def test_configuration_explains_that_a_failed_step_must_be_declared(path: Path = CONFIGURATION):
    """결정 63 — 「선언 없으면 빈칸」이 문서에 없으면 사람들은 버그로 읽는다."""
    text = read(path)
    assert has(text, r"declared"), "configuration.md never says the step must be declared"
    assert has(text, r"\blast_step\b"), "configuration.md never mentions last_step"


def test_configuration_documents_the_history_window_and_its_two_keys():
    text = read(CONFIGURATION)
    for key in ("failure_window_jobs", "failure_min_jobs"):
        assert has(text, key), f"configuration.md lacks {key}"
    assert has(text, r"intermittent\?"), "configuration.md lacks the intermittent? wording"


@pytest.mark.parametrize("key", ["failure_window_jobs", "failure_min_jobs"])
def test_the_example_server_config_shows_the_keys_with_their_defaults(key: str):
    """결정 60 의 결 — 동작을 바꾸는 값은 설치할 때 파일에서 보여야 한다."""
    assert has(read(SERVER_TOML), rf"^\s*#?\s*{key}\s*="), f"no {key} in examples/server.toml"


def test_the_example_server_config_still_loads(tmp_path: Path):
    cfg = load_server_config(SERVER_TOML)
    assert cfg.server.failure_window_jobs >= cfg.server.failure_min_jobs >= 1


@pytest.mark.parametrize("path", USAGES)
def test_both_usage_guides_point_at_the_log_command(path: Path):
    """신고 2 — 로그로 가는 길. 두 거울이 같이 말해야 한다."""
    assert has(read(path), r"rcm logs"), f"{path.name} never names `rcm logs`"


@pytest.mark.parametrize("path", USAGES)
def test_both_usage_guides_describe_the_memory_tight_pattern(path: Path):
    """신고 4 — `--no-wait` + `rcm wait`. 137 이 잡의 실패가 아니라는 것도 같이 적는다."""
    text = read(path)
    assert has(text, r"--no-wait"), f"{path.name} lacks --no-wait"
    assert has(text, r"\b137\b"), f"{path.name} never explains a killed waiting client"


def test_the_example_session_script_reads_the_new_fields():
    """래퍼가 복사해 가는 자리다 — `failed_step` 이 비었을 때를 예시가 먼저 보여 준다."""
    text = read(GATE_SH)
    assert has(text, r"last_step"), "ci-gate.sh does not fall back to last_step"
    assert has(text, r"\.failures"), "ci-gate.sh never reads the failure history"
    assert has(text, r"rcm logs"), "ci-gate.sh never points at the log"


def test_the_changelog_entry_calls_the_step_label_a_behaviour_change():
    """결정 63 은 **동작 변경**이다 — 오늘 라벨이 붙던 잡의 상당수가 빈칸이 된다."""
    text = unreleased(read(CHANGELOG))
    assert has(text, FAIL_MARKER), "CHANGELOG [Unreleased] never mentions the new marker"
    assert has(text, r"failed_step", re.M | re.I), "CHANGELOG never mentions failed_step"
    assert has(text, r"### Changed"), "the step-label change is not under ### Changed"
