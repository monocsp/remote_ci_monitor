"""시험 서버의 프로세스를 띄우고 적은 신원에만 신호를 보낸다 — 아직 빈 틀(시험 먼저)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Places:
    home: Path
    prod: Any
    tmp_roots: tuple[Path, ...] = (Path("/tmp"), Path("/private/tmp"))


def refusal(directory, argv, places, *, cwd, environ):  # noqa: ANN001, ANN201
    return None


def start(directory, name, argv, *, interactive_sigint=False, places=None, out=sys.stdout):  # noqa: ANN001, ANN201
    return 0


def send(directory, name, sig, *, expect_config=None, out=sys.stdout):  # noqa: ANN001, ANN201
    return 0


def stop_all(directory, *, grace=10.0, out=sys.stdout):  # noqa: ANN001, ANN201
    return 0


def main(argv=None, *, places=None, out=sys.stdout):  # noqa: ANN001, ANN201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
