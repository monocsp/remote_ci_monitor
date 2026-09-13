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
    """글자 `flock` 이 아니라 **락의 뜻**을 잠근다 — macOS 에도 있는 락(`fcntl.flock` 또는
    `lockf`)이 예시에 있고, 그 락을 못 잡으면 스크립트가 **멈춘다**(`|| exit`). `flock(1)` 만 덜렁
    있으면 macOS 에서 `command not found` 뒤 락 없이 heavy 구간이 돈다(리뷰 #106 B-1)."""
    assert has(r"fcntl\.flock|\blockf\b"), section()  # flock(1) 은 macOS 에 없다
    assert has(r"(fcntl\.flock|lockf|flock)[^\n]*\|\| exit \d"), section()  # 못 잡으면 중단
    assert not has(r"^flock 9\s*(#.*)?$"), section()  # 폴백 없는 flock(1) 한 줄
    assert has(r"machine-wide lock"), section()
    assert has(r"only if the heavy phase is serialised"), section()
    assert has(r"must not overlap"), section()  # 락이 걸렸는지 보는 법


def test_the_changelog_names_the_section():
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[Unreleased\]\n(.*?)^## \[0\.", text, re.M | re.S)
    assert m, "no [Unreleased] section above the first release"
    assert re.search(r"Two lanes for a gate.*?pull/106", m.group(1), re.S), m.group(1)


def test_the_overlap_is_light_with_light():
    assert has(r"light phases run together"), section()


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
    assert has(r"GET /jobs/<id>"), section()  # 어디서 읽는지도 말한다(#108 이 노출한다)
    # 값은 자기 포함(`store.concurrent_at_start` — 혼자 돌면 1). 「돌던 잡 수」만 적으면 0 으로
    # 읽는다(M5l S12.2 실측: 첫 잡 1 · 둘째 2).
    assert has(r"itself included"), section()
    assert has(r"ran alone\s+reads `1`"), section()
    assert has(r"step_timeline"), section()


def test_config_has_no_memory_admission_key():
    """결정 42 — 설정에 메모리 게이트 키가 없다."""
    config_py = (ROOT / "src" / "remote_ci_monitor" / "config.py").read_text(encoding="utf-8")
    assert not re.search(r"memory_(max_percent|min_free)", config_py)
