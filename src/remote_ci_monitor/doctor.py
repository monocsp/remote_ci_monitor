"""`rcm doctor` — 뼈대."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

PLATFORM = sys.platform


def is_tty() -> bool:
    return False


def ask(question: str) -> bool:
    return False


def python_version() -> tuple[int, ...]:
    return tuple(sys.version_info[:3])


def python_requirement(version, has_filter, *, platform, package_manager, root=False) -> dict:
    return {"state": "ok"}


def install_hint(what: str, *, platform: str, package_manager: str | None, root: bool) -> str:
    return ""


@dataclass
class ServiceFile:
    manager: str = ""
    command: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    nofile: int | None = None

    @property
    def env_names(self) -> list[str]:
        return []


def read_service_file(path: Any) -> ServiceFile:
    return ServiceFile()


def main(args: Any) -> int:
    return 0
