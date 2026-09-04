"""프리셋 입력 검증 — 타입 · choices · 정규식 · 길이. 검증된 값만 env 로 넘긴다.

세션이 보낸 `inputs` 는 문자열일 수도, JSON 타입(bool/int)일 수도 있다. 여기서 프리셋
스키마대로 정규화해 `dict[str, str | bool | int]` 로 돌려준다. 실패는 `InputError` 이고
메시지에 **프리셋 이름과 입력 이름**이 들어간다(어디가 틀렸는지 바로 알 수 있게).
"""

from __future__ import annotations

import re
from typing import Any

from remote_ci_monitor.core.model import MAX_INPUT_LENGTH, InputSpec, Preset

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class InputError(ValueError):
    """입력이 스키마에 맞지 않는다. 메시지는 사용자에게 그대로 보여도 된다."""


def _coerce(preset: str, spec: InputSpec, raw: Any) -> str | bool | int:
    where = f"preset '{preset}' input '{spec.name}'"
    if spec.type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in _TRUE:
                return True
            if low in _FALSE:
                return False
        raise InputError(f"{where}: expected a bool (true/false), got {raw!r}")
    if spec.type == "int":
        if isinstance(raw, bool):
            raise InputError(f"{where}: expected an int, got {raw!r}")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            try:
                return int(raw.strip())
            except ValueError:
                pass
        raise InputError(f"{where}: expected an int, got {raw!r}")
    # string · choice
    if isinstance(raw, bool) or not isinstance(raw, str | int):
        raise InputError(f"{where}: expected a string, got {type(raw).__name__}")
    value = str(raw)
    if len(value) > MAX_INPUT_LENGTH:
        raise InputError(f"{where}: value longer than {MAX_INPUT_LENGTH} characters")
    if "\0" in value or "\n" in value:
        raise InputError(f"{where}: value must not contain newlines or NUL")
    if spec.type == "choice":
        if value not in spec.choices:
            allowed = ", ".join(spec.choices)
            raise InputError(f"{where}: {value!r} is not one of [{allowed}]")
    elif spec.pattern is not None and re.fullmatch(spec.pattern, value) is None:
        raise InputError(f"{where}: {value!r} does not match pattern {spec.pattern!r}")
    return value


def validate_inputs(preset: Preset, raw: dict[str, Any] | None) -> dict[str, str | bool | int]:
    """스키마대로 검증·정규화한 입력을 돌려준다. 모르는 이름·빠진 필수 입력은 오류."""
    raw = dict(raw or {})
    known = {spec.name for spec in preset.inputs}
    unknown = sorted(set(raw) - known)
    if unknown:
        names = ", ".join(unknown)
        raise InputError(f"preset '{preset.name}': unknown input(s): {names}")
    out: dict[str, str | bool | int] = {}
    for spec in preset.inputs:
        if spec.name in raw:
            out[spec.name] = _coerce(preset.name, spec, raw[spec.name])
        elif spec.default is not None:
            out[spec.name] = _coerce(preset.name, spec, spec.default)
        else:
            raise InputError(f"preset '{preset.name}' input '{spec.name}': required (no default)")
    return out


def duration_key(preset: Preset, inputs: dict[str, Any]) -> str:
    """소요시간 버킷 키. `preset` + `duration_key_inputs` 의 값(예 `gate:full`)."""
    parts = [preset.name]
    for name in preset.duration_key_inputs:
        value = inputs.get(name)
        parts.append("" if value is None else _env_text(value))
    return ":".join(parts)


def _env_text(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def env_for_inputs(inputs: dict[str, Any]) -> dict[str, str]:
    """`RCM_INPUT_<NAME>` 환경변수. argv 에 끼워 넣지 않고 env 로만 넘긴다(셸 보간 금지)."""
    env: dict[str, str] = {}
    for name, value in inputs.items():
        env["RCM_INPUT_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()] = _env_text(value)
    return env


def parse_kv(pairs: list[str]) -> dict[str, str]:
    """CLI 의 `-f k=v` 목록을 dict 로. `=` 이 없으면 InputError."""
    out: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise InputError(f"input must look like name=value, got {pair!r}")
        out[key] = value
    return out
