#!/usr/bin/env python3
"""뮤테이션 확인 — 테스트가 실제로 빨개지는지 본다(PLAN.md 「테스트·품질」).

`src/` + `tests/` + `pyproject.toml` 을 임시 디렉터리에 복사하고 변이 하나를 넣은 뒤 그 복사본에서
pytest 를 돌린다. **pytest 가 실패해야 통과**다. 원본은 건드리지 않는다. 변이 패턴을 못 찾으면
그 자체로 실패다(코드가 바뀌어 감시가 풀린 것).

변이 6종:
  ① remaining-floor  — 잔여 하한 제거 (`core/queue.py`)
  ② join-key-inputs  — 합류 키에서 inputs 제외 (`core/queue.py`)
  ③ restart-lost     — 재시작 정리에서 running → lost 를 succeeded 로 (`store.py`)
  ④ stale-threshold  — 호스트 표본 stale 판정의 3×interval 을 0 으로 (`core/hostparse.py`, M1)
  ⑤ top-first-sample — macOS top 의 마지막 표본 대신 첫 표본 사용 (`core/hostparse.py`, M1)
  ⑥ web-not-moving-unknown — 웹 UI 「Not moving」이 queue null 을 「Nothing is stuck」으로
     (`web/app.js`, M2, node --test)

사용: python scripts/mutcheck.py [--keep] [--only NAME]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTEST_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]
    runner: str = "pytest"  # "pytest" | "node" (tests 는 node --test 에 넘길 경로)


MUTANTS = (
    Mutant(
        name="remaining-floor",
        path="src/remote_ci_monitor/core/queue.py",
        old="return max(expected - elapsed, float(cfg.floor_remaining_seconds))",
        new="return expected - elapsed",
        tests=("tests/test_queue.py", "tests/test_progress.py"),
    ),
    Mutant(
        name="join-key-inputs",
        path="src/remote_ci_monitor/core/queue.py",
        old="canonical = [preset, dict(sorted(inputs.items())), source_identity]",
        new="canonical = [preset, source_identity]",
        tests=("tests/test_queue.py",),
    ),
    Mutant(
        name="restart-lost",
        path="src/remote_ci_monitor/store.py",
        old="recover_state = LOST",
        new='recover_state = "succeeded"',
        tests=("tests/test_store.py",),
    ),
    Mutant(
        name="stale-threshold",
        path="src/remote_ci_monitor/core/hostparse.py",
        old="return age > STALE_MULTIPLIER * interval_seconds",
        new="return age > 0 * interval_seconds",
        tests=("tests/test_hostparse.py", "tests/test_status_schema.py"),
    ),
    Mutant(
        name="top-first-sample",
        path="src/remote_ci_monitor/core/hostparse.py",
        old="user_s, sys_s, idle_s = matches[-1]",
        new="user_s, sys_s, idle_s = matches[0]",
        tests=("tests/test_hostparse.py",),
    ),
    Mutant(
        name="web-not-moving-unknown",
        path="src/remote_ci_monitor/web/app.js",
        old='if (!Array.isArray(q)) return { kind: "unknown", lines: [] };\n    var lines = [];',
        new='if (!Array.isArray(q)) return { kind: "ok", lines: [] };\n    var lines = [];',
        tests=("tests/web/summary.test.js",),
        runner="node",
    ),
    Mutant(
        name="retention-active-guard",
        path="src/remote_ci_monitor/core/retention.py",
        old=(
            "    if state in TERMINAL_STATES:\n"
            "        return policy.failure_days * DAY_SECONDS\n"
            "    return None\n"
        ),
        new="    return policy.failure_days * DAY_SECONDS\n",
        tests=("tests/test_retention.py",),
    ),
    Mutant(
        name="gitref-leading-dash",
        path="src/remote_ci_monitor/core/gitref.py",
        old=(
            '    if ref.startswith("-"):\n'
            "        raise ValueError(\"ref must not start with '-'\")\n"
        ),
        new="",
        tests=("tests/test_gitref.py",),
    ),
    Mutant(
        name="priority-order",
        path="src/remote_ci_monitor/core/queue.py",
        old="key=lambda j: (-j.priority, j.id)",
        new="key=lambda j: j.id",
        tests=("tests/test_priority.py",),
    ),
    Mutant(
        name="manifest-link-escape",
        path="src/remote_ci_monitor/core/manifest.py",
        old=(
            '    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):\n'
            "        raise ManifestError(f\"link {path!r}: target escapes the workspace\")\n"
        ),
        new="",
        tests=("tests/test_manifest.py",),
    ),
)


def _pytest(cmd: list[str], cwd: Path) -> tuple[int, float, str] | None:
    """(exit code, seconds, output tail) — timeout 이면 None."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=PYTEST_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return None
    took = time.monotonic() - started
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, took, "\n".join(lines[-6:])


def run_mutant(m: Mutant, keep: bool) -> tuple[bool, str]:
    """복사본에 변이를 넣고 pytest 를 돌린다. (감지됨?, 설명)."""
    tmp = Path(tempfile.mkdtemp(prefix=f"mutcheck-{m.name}-"))
    try:
        for name in ("src", "tests", "pyproject.toml"):
            src = ROOT / name
            if src.is_dir():
                shutil.copytree(src, tmp / name, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src, tmp / name)
        target = tmp / m.path
        text = target.read_text(encoding="utf-8")
        if text.count(m.old) != 1:
            return False, f"pattern not found exactly once in {m.path}: {m.old!r}"
        if m.runner == "node":
            node = shutil.which("node")
            if node is None:
                return False, "node is not installed (needed for the web mutant)"
            cmd = [node, "--test", *m.tests]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *m.tests]
        # 대조군: 변이 없이 복사본에서 초록이어야 한다(환경 문제로 빨간 것을 감지로 착각하지 않게)
        control = _pytest(cmd, tmp)
        if control is None:
            return False, f"control run timed out: {' '.join(cmd)}"
        if control[0] != 0:
            return False, f"control run is RED without the mutant (exit {control[0]})\n{control[2]}"
        target.write_text(text.replace(m.old, m.new), encoding="utf-8")
        mutated = _pytest(cmd, tmp)
        if mutated is None:
            return False, f"mutant run timed out: {' '.join(cmd)}"
        code, took, tail = mutated
        if code == 0:
            return False, f"tests stayed GREEN with mutant applied ({took:.1f}s)\n{tail}"
        return True, f"control green, mutant red (exit {code}, {took:.1f}s)"
    finally:
        if keep:
            print(f"  kept copy: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check that tests catch known mutations.")
    ap.add_argument("--keep", action="store_true", help="keep the mutated copies")
    ap.add_argument("--only", help="run one mutant by name")
    args = ap.parse_args(argv)
    mutants = [m for m in MUTANTS if not args.only or m.name == args.only]
    if not mutants:
        print(f"no mutant named {args.only!r}", file=sys.stderr)
        return 2
    failures = 0
    for m in mutants:
        ok, info = run_mutant(m, args.keep)
        mark = "OK " if ok else "FAIL"
        print(f"[{mark}] {m.name}: {info}")
        if not ok:
            failures += 1
    if failures:
        print(f"mutcheck: {failures} of {len(mutants)} mutants NOT caught", file=sys.stderr)
        return 1
    print(f"mutcheck: all {len(mutants)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
