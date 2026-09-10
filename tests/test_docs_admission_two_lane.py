"""docs/configuration.md 의 「두 레인 실험」(M5j G3 · 결정 86) 문구 잠금.

두 레인은 CPU admission 이 **구간 락이 아니라는 것**을 문서가 말할 때만 안전하다 — 그 문장이 빠지면
누군가 `concurrency_group` 을 빼고 무거운 구간 둘을 동시에 돌린다(fail-open).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "docs" / "configuration.md"


def section() -> str:
    text = CONFIG.read_text(encoding="utf-8")
    m = re.search(r"^### Two lanes for a gate.*$", text, re.M)
    assert m, "no two-lane section"
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def has(pattern: str) -> bool:
    return re.search(pattern, section(), re.I | re.M) is not None


def test_the_experiment_needs_a_lock_in_the_script():
    assert has(r"flock|machine-wide lock"), section()
    assert has(r"only if the heavy phase is serialised"), section()


def test_admission_is_not_a_section_lock():
    assert has(r"decided \*\*once"), section()
    assert has(r"does not stop that"), section()


def test_removing_the_group_is_named_as_losing_the_servers_guarantee():
    assert has(r"Removing `concurrency_group` removes the server"), section()


def test_no_memory_admission_by_decision_42():
    assert has(r"no memory-based admission"), section()
    assert has(r"decision 42"), section()


def test_measurement_hooks_are_named():
    assert has(r"concurrent_at_start"), section()
    assert has(r"step_timeline"), section()


def test_config_has_no_memory_admission_key():
    """결정 42 — 설정에 메모리 게이트 키가 없다."""
    config_py = (ROOT / "src" / "remote_ci_monitor" / "config.py").read_text(encoding="utf-8")
    assert not re.search(r"memory_(max_percent|min_free)", config_py)
