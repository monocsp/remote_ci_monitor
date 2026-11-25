"""`rcm setup` 의 순수한 판단 — 뼈대(시험을 먼저 빨갛게 보려는 자리)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from remote_ci_monitor.core import doctor as dc

LAUNCHD, SYSTEMD = dc.LAUNCHD, dc.SYSTEMD
SERVER_ROLES = (dc.ROLE_COORDINATOR, dc.ROLE_COORDINATOR_WORKER)
STANDARD_LABELS = {"server": "", "worker": ""}
NOFILE = 0


def service_kind(role: str) -> str | None:
    return None


def default_label(role: str) -> str | None:
    return None


def unit_name(label: str) -> str:
    return ""


def service_file_name(label: str, manager: str) -> str:
    return ""


def log_file_name(label: str) -> str:
    return ""


def label_problem(label: str) -> str | None:
    return None


def missing_for_yes(role: str, given: Mapping[str, Any]) -> list[str]:
    return []


def bind_kind(address: str) -> str:
    return ""


def bind_choices(addresses: Sequence[str]) -> list[tuple[str, str]]:
    return []


def bind_warnings(kind: str) -> list[str]:
    return []


def server_toml(
    template: str, *, bind: str, port: int, data_dir: str, lanes: int, advertise: bool | None
) -> str:
    return template


def server_plist(*, rcm: Path, config: Path, home: Path, label: str, log: Path, path: str) -> bytes:
    return b""


def server_unit(*, rcm: Path, config: Path, home: Path, label: str, log: Path, path: str) -> bytes:
    return b""


def install_commands(
    manager: str, *, staged: Path, installed: Path, label: str, log: Path
) -> list[str]:
    return []


def token_commands(rcm: Path, config: Path) -> list[str]:
    return []


def doctor_command(role: str) -> str:
    return ""
