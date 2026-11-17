"""M5n 설정 — `[[probes]]` · 프리셋 `placement`·`concurrency_scope`·`checks`·`check_ref`·`sample` ·
`[server]` 의 새 키 넷 (명세 §4.1 · §4.4 · §5.1 · §6 · §7.4 · §11.2 · 엣지 10).

집안 규칙: 오류 메시지는 **프리셋(또는 프로브) 이름과 키 이름**을 함께 댄다. 잘못된 설정은 기동
실패다 — 조용히 기본값으로 물러나지 않는다(fail-open 금지).

`[[repos]]` 가 있는 설정은 `check_tools=False` 로 읽는다 — 여기서 시험하는 것은 git 유무가 아니다.
"""

from pathlib import Path

import pytest

from remote_ci_monitor.config import ConfigError, ServerSection, load_server_config
from remote_ci_monitor.core import model

REPO = """
[[repos]]
name = "app"
url = "git@example.com:org/app.git"
"""

SIM_CHECK = """
[[presets]]
name = "sim-check"
source_modes = ["git_ref"]
repo = "app"
argv = ["bash", "scripts/qa/sim_check.sh"]
"""


def load(tmp_path: Path, text: str):
    p = tmp_path / "rcm.toml"
    p.write_text(text)
    return load_server_config(p, environ={}, check_tools=False)


def fails(tmp_path: Path, text: str, *, unknown_key: bool = False) -> str:
    """기동 실패의 메시지. 「모르는 키」로 거절된 것은 이 키를 **검증한** 것이 아니다 — 막는다."""
    with pytest.raises(ConfigError) as e:
        load(tmp_path, text)
    msg = str(e.value)
    if not unknown_key:
        assert "unknown key" not in msg and "unknown section" not in msg, msg
    return msg


def probe(name="xcode", argv='["xcodebuild", "-version"]', extra=""):
    return f'\n[[probes]]\nname = "{name}"\nargv = {argv}\n{extra}\n'


def preset(name="gate", extra=""):
    return f'\n[[presets]]\nname = "{name}"\nargv = ["bash", "gate.sh"]\n{extra}\n'


def probe_rows(cfg):
    return [(p.name, tuple(p.argv), p.timeout_seconds) for p in cfg.probes]


# ── [[probes]] (§4.1) ──────────────────────────────────────────────────────────


def test_no_probes_is_an_empty_tuple(tmp_path):
    assert load(tmp_path, preset()).probes == ()


def test_probes_parse_in_declared_order_with_default_timeout(tmp_path):
    text = probe() + probe("idb", '["idb_companion", "--version"]', "timeout_seconds = 30")
    assert probe_rows(load(tmp_path, text)) == [
        ("xcode", ("xcodebuild", "-version"), 10),
        ("idb", ("idb_companion", "--version"), 30),
    ]


def test_probe_duplicate_name_fails_naming_it(tmp_path):
    msg = fails(tmp_path, probe() + probe(argv='["true"]'))
    assert "[[probes]]" in msg
    assert "xcode" in msg
    assert "duplicate" in msg


def test_probe_empty_argv_fails_naming_probe_and_key(tmp_path):
    msg = fails(tmp_path, probe(argv="[]"))
    assert "probe 'xcode'" in msg
    assert "argv" in msg


def test_probe_argv_must_be_strings(tmp_path):
    msg = fails(tmp_path, probe(argv='["xcodebuild", 1]'))
    assert "probe 'xcode'" in msg
    assert "argv" in msg


def test_probe_without_argv_fails(tmp_path):
    msg = fails(tmp_path, '\n[[probes]]\nname = "xcode"\n')
    assert "probe 'xcode'" in msg
    assert "argv" in msg


def test_sixty_four_probes_are_allowed(tmp_path):
    text = "".join(probe(f"p{i:02d}", '["true"]') for i in range(64))
    assert len(load(tmp_path, text).probes) == 64


def test_sixty_five_probes_fail(tmp_path):
    text = "".join(probe(f"p{i:02d}", '["true"]') for i in range(65))
    msg = fails(tmp_path, text)
    assert "[[probes]]" in msg
    assert "64" in msg


@pytest.mark.parametrize("timeout", [1, 60])
def test_probe_timeout_bounds_inclusive(tmp_path, timeout):
    cfg = load(tmp_path, probe(extra=f"timeout_seconds = {timeout}"))
    assert cfg.probes[0].timeout_seconds == timeout


@pytest.mark.parametrize("timeout", ["0", "61", "-5", "true", '"10"', "1.5"])
def test_probe_timeout_out_of_bounds_fails(tmp_path, timeout):
    msg = fails(tmp_path, probe(extra=f"timeout_seconds = {timeout}"))
    assert "probe 'xcode'" in msg
    assert "timeout_seconds" in msg


@pytest.mark.parametrize("name", ["Xcode", "x.code", "x code", "", "-x", "a" * 33])
def test_probe_name_rule(tmp_path, name):
    """소문자 · 숫자 · `-` · `_` 로 32자까지(§4.1)."""
    msg = fails(tmp_path, probe(name=name))
    assert "[[probes]]" in msg
    assert "name" in msg


def test_probe_name_of_32_characters_is_allowed(tmp_path):
    name = "a" * 32
    assert load(tmp_path, probe(name=name)).probes[0].name == name


def test_probe_unknown_key_fails(tmp_path):
    msg = fails(tmp_path, probe(extra='shell = "bash"'), unknown_key=True)
    assert "probe 'xcode'" in msg
    assert "shell" in msg


def test_probes_must_be_an_array_of_tables(tmp_path):
    msg = fails(tmp_path, 'probes = "xcodebuild -version"\n' + preset())
    assert "[[probes]]" in msg


# ── 프리셋 `placement` (§5.1 · 결정 97) ─────────────────────────────────────────


def test_placement_defaults_to_pool(tmp_path):
    assert load(tmp_path, preset()).preset("gate").placement == model.PLACEMENT_POOL


def test_placement_any_capable(tmp_path):
    cfg = load(tmp_path, preset(extra='placement = "any-capable"\npool = "mac2"'))
    gate = cfg.preset("gate")
    assert gate.placement == model.PLACEMENT_CAPABLE
    assert gate.pool == "mac2"  # 대기 표시 위치로 남는다


def test_placement_explicit_pool(tmp_path):
    assert load(tmp_path, preset(extra='placement = "pool"')).preset("gate").placement == "pool"


@pytest.mark.parametrize("value", ['"capable"', '"anywhere"', '"Any-Capable"', "1", "true"])
def test_placement_bad_value_fails_naming_preset_and_key(tmp_path, value):
    """설정 문자열은 `"any-capable"` 이다 — DB 값 `"capable"` 을 설정에 쓰면 오타로 본다."""
    msg = fails(tmp_path, preset(extra=f"placement = {value}"))
    assert "preset 'gate'" in msg
    assert "placement" in msg


# ── 프리셋 `concurrency_scope` (§6 · 결정 100) ─────────────────────────────────


def test_concurrency_scope_defaults_to_pool(tmp_path):
    cfg = load(tmp_path, preset(extra='concurrency_group = "sim"'))
    assert cfg.preset("gate").concurrency_scope == "pool"


@pytest.mark.parametrize("scope", ["pool", "host", "global"])
def test_concurrency_scope_values(tmp_path, scope):
    text = preset(extra=f'concurrency_group = "sim"\nconcurrency_scope = "{scope}"')
    assert load(tmp_path, text).preset("gate").concurrency_scope == scope


@pytest.mark.parametrize("scope", ['"machine"', '"Host"', '""', "1"])
def test_concurrency_scope_bad_value_fails(tmp_path, scope):
    text = preset(extra=f'concurrency_group = "sim"\nconcurrency_scope = {scope}')
    msg = fails(tmp_path, text)
    assert "preset 'gate'" in msg
    assert "concurrency_scope" in msg


# ── 프리셋 `checks` · `check_ref` (§4.4 · 결정 94 · 엣지 10) ───────────────────────


def test_checks_name_a_git_ref_preset(tmp_path):
    text = REPO + SIM_CHECK + preset("scenario-qa", 'checks = ["sim-check"]')
    cfg = load(tmp_path, text)
    assert cfg.preset("scenario-qa").checks == ("sim-check",)
    assert cfg.preset("sim-check").checks == ()


def test_checks_default_is_empty(tmp_path):
    assert load(tmp_path, preset()).preset("gate").checks == ()


def test_checks_unknown_preset_fails_naming_both(tmp_path):
    text = REPO + SIM_CHECK + preset("scenario-qa", 'checks = ["sim-chek"]')
    msg = fails(tmp_path, text)
    assert "preset 'scenario-qa'" in msg
    assert "checks" in msg
    assert "sim-chek" in msg


def test_check_preset_must_accept_git_ref(tmp_path):
    """rcm 이 스스로 걸 때 쓸 소스가 필요하다 — tree 만 받는 프리셋은 점검이 될 수 없다."""
    text = preset("tree-check") + preset("scenario-qa", 'checks = ["tree-check"]')
    msg = fails(tmp_path, text)
    assert "preset 'scenario-qa'" in msg
    assert "tree-check" in msg
    assert "git_ref" in msg


def test_check_preset_accepting_both_modes_is_fine(tmp_path):
    both = """
[[presets]]
name = "sim-check"
source_modes = ["tree", "git_ref"]
repo = "app"
argv = ["bash", "sim_check.sh"]
"""
    text = REPO + both + preset("scenario-qa", 'checks = ["sim-check"]')
    assert load(tmp_path, text).preset("scenario-qa").checks == ("sim-check",)


def test_check_preset_with_its_own_checks_fails(tmp_path):
    """순환 금지 — 점검의 점검은 없다(A→B→A 뿐 아니라 A→B→C 도 막는다)."""
    deep = """
[[presets]]
name = "boot-check"
source_modes = ["git_ref"]
repo = "app"
argv = ["true"]
"""
    sim = SIM_CHECK.rstrip() + '\nchecks = ["boot-check"]\n'
    text = REPO + deep + sim + preset("scenario-qa", 'checks = ["sim-check"]')
    msg = fails(tmp_path, text)
    assert "preset 'sim-check'" in msg
    assert "checks" in msg


def test_preset_cannot_check_itself(tmp_path):
    selfish = SIM_CHECK.rstrip() + '\nchecks = ["sim-check"]\n'
    msg = fails(tmp_path, REPO + selfish)
    assert "preset 'sim-check'" in msg
    assert "checks" in msg


def test_checks_duplicate_entry_fails(tmp_path):
    text = REPO + SIM_CHECK + preset("scenario-qa", 'checks = ["sim-check", "sim-check"]')
    msg = fails(tmp_path, text)
    assert "preset 'scenario-qa'" in msg
    assert "duplicate" in msg


def test_checks_must_be_a_list_of_strings(tmp_path):
    text = REPO + SIM_CHECK + preset("scenario-qa", 'checks = "sim-check"')
    msg = fails(tmp_path, text)
    assert "preset 'scenario-qa'" in msg
    assert "checks" in msg


def test_check_ref_defaults_to_head(tmp_path):
    cfg = load(tmp_path, REPO + SIM_CHECK + preset("scenario-qa", 'checks = ["sim-check"]'))
    assert cfg.preset("sim-check").check_ref == "HEAD"


def test_check_ref_custom_value(tmp_path):
    sim = SIM_CHECK.rstrip() + '\ncheck_ref = "release/qa"\n'
    cfg = load(tmp_path, REPO + sim + preset("scenario-qa", 'checks = ["sim-check"]'))
    assert cfg.preset("sim-check").check_ref == "release/qa"


@pytest.mark.parametrize("value", ['""', "1", '["main"]'])
def test_check_ref_must_be_a_nonempty_string(tmp_path, value):
    sim = SIM_CHECK.rstrip() + f"\ncheck_ref = {value}\n"
    msg = fails(tmp_path, REPO + sim + preset("scenario-qa", 'checks = ["sim-check"]'))
    assert "preset 'sim-check'" in msg
    assert "check_ref" in msg


# ── 프리셋 `sample` (§7.4 · 결정 103) ──────────────────────────────────────────


def test_sample_defaults_true_for_ordinary_presets(tmp_path):
    cfg = load(tmp_path, REPO + SIM_CHECK + preset("scenario-qa", 'checks = ["sim-check"]'))
    assert cfg.preset("scenario-qa").sample is True


def test_sample_defaults_false_for_check_presets(tmp_path):
    cfg = load(tmp_path, REPO + SIM_CHECK + preset("scenario-qa", 'checks = ["sim-check"]'))
    assert cfg.preset("sim-check").sample is False


def test_a_git_ref_preset_nobody_checks_is_still_a_sample(tmp_path):
    """점검 프리셋은 「다른 프리셋의 `checks` 에 이름이 오른」 것이다(§3).

    git_ref 를 받는다는 것만으로는 점검 프리셋이 아니다.
    """
    assert load(tmp_path, REPO + SIM_CHECK).preset("sim-check").sample is True


def test_sample_explicit_true_on_a_check_preset_wins(tmp_path):
    sim = SIM_CHECK.rstrip() + "\nsample = true\n"
    cfg = load(tmp_path, REPO + sim + preset("scenario-qa", 'checks = ["sim-check"]'))
    assert cfg.preset("sim-check").sample is True


def test_sample_explicit_false_on_an_ordinary_preset(tmp_path):
    assert load(tmp_path, preset(extra="sample = false")).preset("gate").sample is False


def test_sample_must_be_a_bool(tmp_path):
    msg = fails(tmp_path, preset(extra='sample = "no"'))
    assert "preset 'gate'" in msg
    assert "sample" in msg


# ── [server] 새 키 (§11.2) ─────────────────────────────────────────────────────


def test_server_defaults():
    s = ServerSection()
    assert s.probe_interval_seconds == 600
    assert s.auto_checks is True
    assert s.local_accept_any is True
    assert s.check_sweep_seconds == 30


def test_server_keys_from_file(tmp_path):
    text = (
        "[server]\nprobe_interval_seconds = 60\nauto_checks = false\n"
        "local_accept_any = false\ncheck_sweep_seconds = 5\n" + preset()
    )
    s = load(tmp_path, text).server
    assert (s.probe_interval_seconds, s.auto_checks, s.local_accept_any, s.check_sweep_seconds) == (
        60,
        False,
        False,
        5,
    )


def test_probe_interval_below_60_fails(tmp_path):
    msg = fails(tmp_path, "[server]\nprobe_interval_seconds = 59\n" + preset())
    assert "[server] probe_interval_seconds" in msg
    assert "60" in msg


@pytest.mark.parametrize("value", [0, -1])
def test_check_sweep_seconds_must_be_positive(tmp_path, value):
    msg = fails(tmp_path, f"[server]\ncheck_sweep_seconds = {value}\n" + preset())
    assert "[server] check_sweep_seconds" in msg


def test_auto_checks_must_be_a_bool(tmp_path):
    """다른 `[server]` 불리언처럼 `"yes"`·`"off"` 는 받는다(`_coerce_scalar`) — 그 밖은 오류다."""
    msg = fails(tmp_path, '[server]\nauto_checks = "maybe"\n' + preset())
    assert "auto_checks" in msg


def test_probe_interval_env_override(tmp_path):
    p = tmp_path / "rcm.toml"
    p.write_text(preset())
    cfg = load_server_config(
        p, environ={"RCM_SERVER_PROBE_INTERVAL_SECONDS": "120"}, check_tools=False
    )
    assert cfg.server.probe_interval_seconds == 120
