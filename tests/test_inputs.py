"""프리셋 입력 검증 — 타입·choices·정규식·길이·env 변환."""

import pytest

from remote_ci_monitor.core.inputs import (
    InputError,
    duration_key,
    env_for_inputs,
    parse_kv,
    validate_inputs,
)
from remote_ci_monitor.core.model import InputSpec, Preset

GATE = Preset(
    name="gate",
    argv=("bash", "scripts/gate.sh"),
    duration_key_inputs=("scope",),
    inputs=(
        InputSpec(name="scope", type="choice", choices=("full", "commit", "fast"), default="full"),
        InputSpec(name="verbose", type="bool", default=False),
        InputSpec(name="shards", type="int", default=1),
        InputSpec(name="branch", type="string", pattern=r"[A-Za-z0-9/_.-]+"),
    ),
)


def test_defaults_fill_missing_inputs():
    out = validate_inputs(GATE, {"branch": "feat/x"})
    assert out == {"scope": "full", "verbose": False, "shards": 1, "branch": "feat/x"}


def test_required_input_without_default_fails_with_names():
    with pytest.raises(InputError) as e:
        validate_inputs(GATE, {})
    assert "preset 'gate'" in str(e.value) and "'branch'" in str(e.value)


def test_unknown_input_rejected():
    with pytest.raises(InputError) as e:
        validate_inputs(GATE, {"branch": "x", "nope": "1"})
    assert "unknown input(s): nope" in str(e.value)


def test_choice_must_be_in_choices():
    with pytest.raises(InputError) as e:
        validate_inputs(GATE, {"branch": "x", "scope": "huge"})
    assert "'huge' is not one of [full, commit, fast]" in str(e.value)


@pytest.mark.parametrize("raw,want", [("true", True), ("0", False), (True, True), ("Yes", True)])
def test_bool_coercion(raw, want):
    assert validate_inputs(GATE, {"branch": "x", "verbose": raw})["verbose"] is want


def test_bool_rejects_garbage():
    with pytest.raises(InputError):
        validate_inputs(GATE, {"branch": "x", "verbose": "maybe"})


def test_int_coercion_and_rejection():
    assert validate_inputs(GATE, {"branch": "x", "shards": "4"})["shards"] == 4
    with pytest.raises(InputError):
        validate_inputs(GATE, {"branch": "x", "shards": "four"})
    with pytest.raises(InputError):
        validate_inputs(GATE, {"branch": "x", "shards": True})


def test_pattern_and_length():
    with pytest.raises(InputError) as e:
        validate_inputs(GATE, {"branch": "bad branch"})
    assert "does not match pattern" in str(e.value)
    with pytest.raises(InputError) as e:
        validate_inputs(GATE, {"branch": "a" * 257})
    assert "longer than 256" in str(e.value)


def test_newline_rejected_in_string():
    with pytest.raises(InputError):
        validate_inputs(GATE, {"branch": "x\ny"})


def test_duration_key_uses_declared_inputs_only():
    inputs = validate_inputs(GATE, {"branch": "x", "scope": "fast", "shards": 9})
    assert duration_key(GATE, inputs) == "gate:fast"
    bare = Preset(name="deploy", argv=("true",))
    assert duration_key(bare, {}) == "deploy"


def test_env_for_inputs_is_uppercased_and_stringified():
    env = env_for_inputs({"scope": "fast", "verbose": True, "shards": 3, "odd-name": "v"})
    assert env == {
        "RCM_INPUT_SCOPE": "fast",
        "RCM_INPUT_VERBOSE": "1",
        "RCM_INPUT_SHARDS": "3",
        "RCM_INPUT_ODD_NAME": "v",
    }


def test_parse_kv():
    assert parse_kv(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}
    with pytest.raises(InputError):
        parse_kv(["novalue"])
