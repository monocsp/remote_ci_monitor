"""`rcm doctor` 의 순수 판정(M5t-a 결정 246) — 뼈대."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

READY, NEEDS_CHECK, MISSING, UNKNOWN = "ready", "needs_check", "missing", "unknown"
APPLIES_POOL = "pool"
APPLIES_CAPABLE = "any-capable"
APPLIES_NOT_COMING = "not_coming"
APPLIES_OTHER_POOL = "other_pool"
DEFAULT_PATHS = {"launchd": "", "systemd": ""}


def decide_exit(problems: Sequence[str], undecided: Sequence[str]) -> int:
    return 0


def clean_env(manager: str, base: Mapping[str, str], service_env: Mapping[str, str]) -> dict:
    return dict(base)


def mask_home(text: str, home: str) -> str:
    return text


def clean_text(value: Any, limit: int = 300) -> str:
    return str(value)


@dataclass
class PresetRow:
    name: str
    applies: str = ""
    state: str | None = None
    missing: list[dict[str, Any]] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    code: str | None = None
    warn: str | None = None
    counts: bool = True
    problem: bool = False


def classify_preset(entry, facts, *, pool, accept_any, hints, os_name) -> PresetRow:
    return PresetRow(name=str(entry.get("name")))
