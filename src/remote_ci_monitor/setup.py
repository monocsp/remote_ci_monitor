"""`rcm setup` — 뼈대(시험을 먼저 빨갛게 보려는 자리)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from remote_ci_monitor.core import doctor as dc


class SetupUsage(Exception):
    pass


class SetupFailed(Exception):
    pass


def find_rcm() -> tuple[Path | None, bool]:
    return None, False


def addresses() -> list[str]:
    return []


def is_local_address(address: str) -> bool:
    return False


def port_in_use(host: str, port: int) -> bool | None:
    return None


def service_loaded(manager: str, label: str) -> bool | None:
    return None


def host_facts() -> dict[str, Any]:
    return {}


@dataclass
class World:
    platform: str = sys.platform
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    interactive: Callable[[], bool] = lambda: False
    addresses: Callable[[], list[str]] = addresses
    is_local_address: Callable[[str], bool] = is_local_address
    port_in_use: Callable[[str, int], bool | None] = port_in_use
    service_loaded: Callable[[str, str], bool | None] = service_loaded
    host_facts: Callable[[], dict[str, Any]] = host_facts
    find_rcm: Callable[[], tuple[Path | None, bool]] = find_rcm


def _add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--role", choices=dc.ROLES)
    p.add_argument("--yes", "-y", action="store_true")
    p.add_argument("--server")
    p.add_argument("--by")
    p.add_argument("--config")
    p.add_argument("--pool")
    p.add_argument("--lanes", type=int)
    p.add_argument("--label")
    p.add_argument("--service-file", dest="service_file")
    p.add_argument("--port", type=int)
    p.add_argument("--data-dir", dest="data_dir")
    p.add_argument("--advertise", dest="advertise", action="store_true", default=None)
    p.add_argument("--no-advertise", dest="advertise", action="store_false")
    p.add_argument("--bind")
    p.add_argument(
        "--force", action="append", nargs="?", const="all", choices=("config", "service", "all")
    )
    p.add_argument("--reuse-data-dir", dest="reuse_data_dir", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rcm setup")
    _add_arguments(p)
    return p


def register(subparsers: Any) -> argparse.ArgumentParser:
    p = subparsers.add_parser("setup")
    _add_arguments(p)
    p.set_defaults(func=cmd_setup)
    return p


def existing_roles(environ: Mapping[str, str]) -> list[str]:
    return []


def db_header_version(db: Path) -> int | None:
    return None


def run(args: argparse.Namespace, world: World) -> int:
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    return run(args, World())


def main(argv: list[str] | None = None) -> int:
    return cmd_setup(build_parser().parse_args(argv))
