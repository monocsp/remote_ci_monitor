#!/usr/bin/env python3
"""고친 커밋의 시험이 「고치기 전에 빨강, 고친 뒤에 초록」인지 돌려 보고 영수증을 찍는다.

  python3 tools/redgreen.py --fix <sha> --test <pytest nodeid>... [--repo .] [--json]
                            [--import-red-ok]

왜 있나(2026-11-25): AGENTS.md 「Tests first」 — 고침은 고치기 전에 빨갛던 시험과 함께 온다.
말로만 적은 빨강은 확인할 길이 없다. `<sha>^` 트리에 `<sha>` 의 시험 파일만 얹어 돌리고(실패해야
한다) `<sha>` 트리에서 돌린다(통과해야 한다). 두 트리는 레포 밖 임시 워크트리이고, 끝나면 **자기가
만든 것만** `git worktree remove --force` 로 치운다.

빨강은 시험 **안**의 실패(FAILED)여야 한다. 수집 · import 실패와 픽스처 오류는 시험이 아직 못 도는
것이라 기본은 빨강이 아니다 — 없는 이름을 들여오는 것이 요점이면 `--import-red-ok`. 어느 쪽인지는
`kinds` 에 적는다. 건너뛴(skip) 시험은 통과가 아니다 — 모르는 값을 성공으로 세지 않는다.

종료 코드: 0 빨강→초록 · 1 아님(not red · not green) · 2 사용법(sha · 부모 · 시험 파일 없음) ·
3 판정 못 함(pytest 가 시험을 못 찾음 · 시간 초과 · 워크트리를 못 만듦 · 결과 줄 없음).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

EXIT_RED_GREEN, EXIT_NOT, EXIT_USAGE, EXIT_UNKNOWN = 0, 1, 2, 3
PYTEST_TIMEOUT = 900
TAIL = 15
#: `-rA` 의 짧은 요약 줄 — `FAILED tests/x.py::test_a - assert …`
SUMMARY_RE = re.compile(r"^(PASSED|FAILED|ERROR|XPASS|XFAIL) (\S+)")


class Usage(Exception):
    """사용법 오류 — 종료 2."""


def _git(repo: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    cmd = ["git", "-C", str(repo), *args]
    return subprocess.run(cmd, capture_output=True, text=not binary, env=env, timeout=120)


def resolve(repo: Path, fix: str, tests: list[str]) -> tuple[Path, str, str, list[str]]:
    """(레포 뿌리, fix sha, 부모 sha, 얹을 시험 파일들). 틀리면 Usage."""
    top = _git(repo, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise Usage(f"{repo} is not a git repository")
    root = Path(top.stdout.strip())
    sha = _git(root, "rev-parse", "--verify", "--quiet", f"{fix}^{{commit}}").stdout.strip()
    if not sha:
        raise Usage(f"no commit {fix!r}")
    parent = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^1").stdout.strip()
    if not parent:
        raise Usage(f"{sha} has no parent — a root commit cannot be red before it")
    files = list(dict.fromkeys(node.split("::", 1)[0] for node in tests))
    for rel in files:
        if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            raise Usage(f"{rel}: the test path must be inside the repository")
        if _git(root, "cat-file", "-e", f"{sha}:{rel}").returncode != 0:
            raise Usage(f"{rel} does not exist at {sha[:12]}")
    return root, sha, parent, files


def kinds(output: str, tests: list[str]) -> dict[str, str]:
    """요청한 nodeid 마다 결과 하나: collection · failed · error · passed · none."""
    seen = [m.groups() for m in map(SUMMARY_RE.match, output.splitlines()) if m]
    out: dict[str, str] = {}
    for node in tests:
        file = node.split("::", 1)[0]
        mine = [s for s, nid in seen if nid == node or nid.startswith((node + "::", node + "["))]
        if any(s == "ERROR" and nid == file for s, nid in seen):
            out[node] = "collection"  # 시험 파일 자체가 수집 · import 단계에서 실패
        elif "FAILED" in mine:
            out[node] = "failed"
        elif "ERROR" in mine:
            out[node] = "error"
        elif mine and set(mine) == {"PASSED"}:
            out[node] = "passed"
        else:
            out[node] = "none"  # 건너뜀 · xfail · 못 찾음 — 어느 쪽도 증명하지 않는다
    return out


def run_side(tree: Path, tests: list[str], label: str) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", *tests]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    src = [str(tree / "src")] if (tree / "src").is_dir() else []  # editable 설치의 src 를 이긴다
    env["PYTHONPATH"] = os.pathsep.join([*src, *filter(None, [env.get("PYTHONPATH")])])
    shown = shlex.join(["python", *cmd[1:]])
    try:
        proc = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True,
                              timeout=PYTEST_TIMEOUT)  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"tree": str(tree), "cmd": shown, "exit": None,
                "kinds": dict.fromkeys(tests, "none"), "tail": ["timed out"]}  # fmt: skip
    text = proc.stdout + proc.stderr
    for real in {str(tree), str(tree.resolve())}:
        text = text.replace(real, f"<{label}>")  # 영수증에 개인 경로를 남기지 않는다
    text = text.replace(str(Path.home()), "~")
    tail = text.strip().splitlines()[-TAIL:]
    return {"tree": str(tree), "cmd": shown, "exit": proc.returncode,
            "kinds": kinds(text, tests), "tail": tail}  # fmt: skip


def verdict(red: dict[str, Any], green: dict[str, Any], import_ok: bool) -> tuple[str, int]:
    red_ok = {"failed", "collection", "error"} if import_ok else {"failed"}
    rk, gk = set(red["kinds"].values()), set(green["kinds"].values())
    # pytest 3 내부 오류 · 4 못 찾음 · 5 시험 없음 · 시간 초과. 수집 실패도 nodeid 를 주면 4 다
    if red["exit"] not in (0, 1, 2) and "collection" not in rk:
        return "unknown", EXIT_UNKNOWN
    if red["exit"] == 0 or "passed" in rk or not rk <= red_ok | {"none"}:
        return "not red", EXIT_NOT
    if "none" in rk:
        return "unknown", EXIT_UNKNOWN
    if green["exit"] not in (0, 1, 2) and "collection" not in gk:
        return "unknown", EXIT_UNKNOWN
    if green["exit"] != 0 or gk & {"failed", "error", "collection"}:
        return "not green", EXIT_NOT
    if gk != {"passed"}:
        return "unknown", EXIT_UNKNOWN
    return "red-then-green", EXIT_RED_GREEN


def _print_text(receipt: dict[str, Any]) -> None:
    print(f"redgreen: {receipt['verdict']}")
    print(f"fix     {receipt['fix']}\nparent  {receipt['parent']}")
    print(f"overlay {', '.join(receipt['files'])} (from fix onto parent)")
    for side, what in (("red", "parent + test files"), ("green", "fix")):
        r = receipt[side]
        if r is None:
            continue
        exit_code = "—" if r["exit"] is None else r["exit"]
        found = ", ".join(f"{node} {kind}" for node, kind in r["kinds"].items())
        print(f"{side} ({what}): exit {exit_code} · {found}\n  $ {r['cmd']}")
        for line in r["tail"]:
            print(f"  | {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="redgreen.py",
        description="Prove a fix commit's tests fail on its parent (with the fix's test files "
        "laid on top) and pass on the fix. Prints a receipt.",
        epilog="exit codes: 0 red-then-green · 1 not (not red, or not green) · 2 usage · "
        "3 could not decide (pytest found no such test, timed out, no result line)",
    )
    ap.add_argument("--fix", required=True, help="the fix commit")
    ap.add_argument("--test", action="append", required=True, help="pytest node id (repeat)")
    ap.add_argument("--repo", type=Path, default=Path("."), help="repository (default .)")
    ap.add_argument("--json", action="store_true", help="print the receipt as JSON")
    ap.add_argument("--import-red-ok", action="store_true", help="import/fixture error = red")
    try:
        args = ap.parse_args(argv)
        root, sha, parent, files = resolve(args.repo, args.fix, args.test)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code else EXIT_RED_GREEN
    except Usage as why:
        print(f"usage: {why}", file=sys.stderr)
        return EXIT_USAGE
    tmp = Path(tempfile.mkdtemp(prefix="redgreen-"))
    created: list[Path] = []
    receipt: dict[str, Any] = {"fix": sha, "parent": parent, "tests": args.test, "files": files}
    receipt.update(red=None, green=None, verdict="unknown")
    code = EXIT_UNKNOWN
    try:
        for side, at in (("red", parent), ("green", sha)):
            made = _git(root, "worktree", "add", "--quiet", "--detach", str(tmp / side), at)
            if made.returncode != 0:
                raise OSError(f"git worktree add failed: {made.stderr.strip()}")
            created.append(tmp / side)
        for rel in files:  # 고친 커밋의 시험 파일을 부모 트리 위에 얹는다
            target = tmp / "red" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_git(root, "show", f"{sha}:{rel}", binary=True).stdout)
        receipt["red"] = run_side(tmp / "red", args.test, "red")
        receipt["green"] = run_side(tmp / "green", args.test, "green")
        receipt["verdict"], code = verdict(receipt["red"], receipt["green"], args.import_red_ok)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"unknown: {error}", file=sys.stderr)
    finally:
        for path in created:  # 자기가 만든 워크트리만 치운다
            _git(root, "worktree", "remove", "--force", str(path))
        shutil.rmtree(tmp, ignore_errors=True)
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    else:
        _print_text(receipt)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
