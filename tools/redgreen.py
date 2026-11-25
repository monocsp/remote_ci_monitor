#!/usr/bin/env python3
"""고친 커밋의 시험이 「고치기 전에 빨강, 고친 뒤에 초록」인지 돌려 보고 영수증을 찍는다.

  ./.venv/bin/python tools/redgreen.py --fix <sha> --test <pytest nodeid>... [--repo .]
                                       [--json] [--import-red-ok]

pytest 는 이 도구를 돌린 인터프리터(`sys.executable`)로 돈다 — 그 인터프리터가 pytest 를 못 들이면
곧바로 종료 2 다. 워크트리의 `.venv` python 으로 돌린다.

왜 있나(2026-11-25): AGENTS.md 「Tests first」 — 고침은 고치기 전에 빨갛던 시험과 함께 온다.
말로만 적은 빨강은 확인할 길이 없다. `<sha>^` 트리에 `<sha>` 의 시험 파일만 얹어 돌리고(실패해야
한다) `<sha>` 트리에서 돌린다(통과해야 한다). 두 트리는 레포 밖 임시 워크트리이고, 끝나면 **자기가
만든 것만** `git worktree remove --force` 로 치운다.

빨강은 시험 **안**의 실패(FAILED)여야 한다. 수집 · import 실패와 픽스처 오류는 시험이 아직 못 도는
것이라 기본은 빨강이 아니다 — 없는 이름을 들여오는 것이 요점이면 `--import-red-ok`. 어느 쪽인지는
`kinds` 에 적는다. 건너뛴(skip) 시험은 통과가 아니다 — 모르는 값을 성공으로 세지 않는다. 파일
하나처럼 여러 시험으로 펼쳐지는 nodeid 에서 고치기 전에 통과한 것이 섞이면 `mixed` 이고 빨강이
아니다(어느 것이 통과했는지 `note` 에 적는다). 영수증에는 임시 경로 대신 `<red>` · `<green>` 을,
pytest 의 `--basetemp`(임시 트리 곁의 `<red-tmp>` · `<green-tmp>` — 기본 `pytest-of-<사용자>` 를
쓰지 않는다) · 임시 폴더는 `<tmp>`, 집은 `~`, 사용자 이름은 `<user>` 로 적고, 인터프리터는
이름(`python3`)이나 `.venv/bin/python` 만 적는다. 경로 치환은 긴 것부터 한다(풀린 경로
`/private/var/…` 가 안 풀린 `/var/…` 를 품는다). `--test` 의 파일 부분은 앞 `./` · 빈 마디(`//`) ·
`.` 마디를 떼고 읽는다(pytest 가 찍는 nodeid 와 같은 꼴).

SIGTERM · SIGINT 를 받아도 임시 워크트리와 임시 폴더를 치우고 끝난다(종료 143 · 130). 임시 폴더를
만드는 동안과 `git worktree add` 동안은 두 신호를 미뤘다가(pthread_sigmask) 끝난 뒤 받는다 — add
가 도중에 끊기면 잠긴(initializing) 등록이 남는다. 치울 때는 `git worktree remove -f -f`(잠긴 것도)
뒤에 남은 등록을 prune 하고, 그래도 남으면 stderr 에 적고 종료 4 다. 치우는 동안의 신호는 무시한다.
pytest 는 자기 세션에서 돌고, 끊기면 그 프로세스 그룹째 죽인다 — 시험이 띄운 자식이 남지 않게.

종료 코드: 0 빨강→초록 · 1 아님(not red · not green · mixed) · 2 사용법(sha · 부모 · 시험 파일
없음, 폴더 · 심볼릭 링크를 줌, pytest 없는 인터프리터) · 3 판정 못 함(pytest 가 시험을 못 찾음 ·
시간 초과 · 워크트리를 못 만듦 · 결과 줄 없음 · 시험 파일 경로가 임시 트리 밖으로 풀림 · 고친
커밋에서 시험 파일을 못 꺼냄) · 4 정리 실패(임시 워크트리 등록이나 임시 폴더가 남았다 — 판정은
그대로 찍는다) · 130 SIGINT · 143 SIGTERM.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

EXIT_RED_GREEN, EXIT_NOT, EXIT_USAGE, EXIT_UNKNOWN, EXIT_CLEANUP = 0, 1, 2, 3, 4
#: 끊기면 치우고 끝내는 신호 — 만드는 동안(mkdtemp · worktree add)은 미룬다
HELD = (signal.SIGTERM, signal.SIGINT)
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


def node_id(raw: str) -> str:
    """`./tests//x.py::t` → `tests/x.py::t` — pytest 는 빈 · `.` 마디 없이 nodeid 를 찍는다.
    절대 경로와 `..` 는 그대로 둔다(resolve 가 사용법 오류로 막는다)."""
    file, sep, rest = raw.partition("::")
    if file.startswith("/") or ".." in file.split("/"):
        return raw
    return "/".join(part for part in file.split("/") if part not in ("", ".")) + sep + rest


@contextlib.contextmanager
def _held() -> Any:
    """SIGTERM · SIGINT 를 잠시 미룬다 — 그 사이에 온 신호는 블록이 끝나면 받는다(처리기가
    SystemExit 를 던져 main 의 finally 가 치운다). 자식(git)도 이 마스크를 물려받는다."""
    if not hasattr(signal, "pthread_sigmask"):
        yield
        return
    old = signal.pthread_sigmask(signal.SIG_BLOCK, HELD)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


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
        # 모드까지 본다 — 심볼릭 링크(120000)도 blob 이지만 내용은 경로 문자열이다
        listed = _git(root, "--literal-pathspecs", "ls-tree", "-z", "--full-tree", sha, "--", rel)
        entries = [e.split("\t", 1) for e in listed.stdout.split("\0") if "\t" in e]
        if listed.returncode != 0 or not entries:
            raise Usage(f"{rel} does not exist at {sha[:12]}")
        mode, kind = next((head.split()[:2] for head, path in entries if path == rel), ("", "tree"))
        if kind == "tree":
            raise Usage(f"{rel} is a directory at {sha[:12]} — name a test file or node id")
        if mode == "120000":
            raise Usage(f"{rel} is a symbolic link at {sha[:12]} — name the file it points to")
        if kind != "blob":
            raise Usage(f"{rel} is not a file at {sha[:12]} ({kind})")
    return root, sha, parent, files


def interpreter() -> str:
    """영수증에 적을 인터프리터 — 집 경로를 남기지 않게 `.venv/bin/python` 이나 이름만."""
    exe = Path(sys.executable)
    if ".venv" in exe.parts:
        return "/".join(exe.parts[exe.parts.index(".venv") :])
    return exe.name or "python"


def has_pytest() -> bool:
    probe = "import importlib.util, sys; sys.exit(importlib.util.find_spec('pytest') is None)"
    try:
        return subprocess.run([sys.executable, "-c", probe], timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def kinds(output: str, tests: list[str]) -> dict[str, str]:
    """요청한 nodeid 마다 결과 하나: collection · mixed · failed · error · passed · none."""
    seen = [m.groups() for m in map(SUMMARY_RE.match, output.splitlines()) if m]
    out: dict[str, str] = {}
    for node in tests:
        file = node.split("::", 1)[0]
        mine = [s for s, nid in seen if nid == node or nid.startswith((node + "::", node + "["))]
        if any(s == "ERROR" and nid == file for s, nid in seen):
            out[node] = "collection"  # 시험 파일 자체가 수집 · import 단계에서 실패
        elif "PASSED" in mine and {"FAILED", "ERROR"} & set(mine):
            out[node] = "mixed"  # 펼쳐진 시험 가운데 통과한 것이 섞였다
        elif "FAILED" in mine:
            out[node] = "failed"
        elif "ERROR" in mine:
            out[node] = "error"
        elif mine and set(mine) == {"PASSED"}:
            out[node] = "passed"
        else:
            out[node] = "none"  # 건너뜀 · xfail · 못 찾음 — 어느 쪽도 증명하지 않는다
    return out


def _temp_dirs() -> set[str]:
    """시험 출력에 찍힐 수 있는 임시 폴더 — 이 프로세스의 것과 TMPDIR(자식이 쓴다)."""
    raw = {tempfile.gettempdir(), os.environ.get("TMPDIR", "")}
    return {p.rstrip("/") for p in raw} - {""}


def redact(text: str, tree: Path, label: str) -> str:
    """영수증 글에서 사람 · 기계가 드러나는 것을 지운다 — 임시 트리 · basetemp · 임시 폴더 · 집 ·
    사용자 이름. 긴 경로부터 바꾼다(풀린 경로가 안 풀린 경로를 품는다)."""
    swaps: dict[str, str] = {}
    places = [(tree.parent / f"{label}-tmp", f"<{label}-tmp>"), (tree, f"<{label}>")]
    places += [(Path(p), "<tmp>") for p in _temp_dirs()] + [(Path.home(), "~")]
    for place, word in places:
        for spelled in (str(place), str(place.resolve())):
            if len(spelled) > 1:  # `/` 하나를 바꾸면 글 전체가 깨진다
                swaps.setdefault(spelled, word)
    for old in sorted(swaps, key=len, reverse=True):
        text = text.replace(old, swaps[old])
    with contextlib.suppress(Exception):  # 사용자 이름을 모르는 환경 — 바꿀 것도 없다
        user = getpass.getuser()
        if user:
            text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(user)}(?![A-Za-z0-9_])", "<user>", text)
    return text


def run_side(tree: Path, tests: list[str], label: str) -> dict[str, Any]:
    # basetemp 를 임시 트리 곁에 둔다 — 기본 `<임시>/pytest-of-<사용자>` 는 이름을 영수증에 남긴다
    basetemp = tree.parent / f"{label}-tmp"
    flags = ["-q", "-rA", "-p", "no:cacheprovider"]
    cmd = [sys.executable, "-m", "pytest", *flags, f"--basetemp={basetemp}", *tests]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": tempfile.gettempdir()}
    src = [str(tree / "src")] if (tree / "src").is_dir() else []  # editable 설치의 src 를 이긴다
    env["PYTHONPATH"] = os.pathsep.join([*src, *filter(None, [env.get("PYTHONPATH")])])
    shown = shlex.join([interpreter(), "-m", "pytest", *flags, f"--basetemp=<{label}-tmp>", *tests])
    # 자기 세션 — 끊기면(시간 초과 · SIGTERM) 시험이 띄운 자식까지 그룹째 죽인다
    proc = subprocess.Popen(cmd, cwd=tree, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True)  # fmt: skip
    try:
        out, err = proc.communicate(timeout=PYTEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"tree": f"<{label}>", "cmd": shown, "exit": None, "passed": [],
                "kinds": dict.fromkeys(tests, "none"), "tail": ["timed out"]}  # fmt: skip
    finally:
        if proc.returncode is None:  # 다 돌지 못했다 — 거두기 전이라 그룹 번호가 아직 그의 것
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
    text = redact(out + err, tree, label)  # 영수증에 개인 경로 · 이름을 남기지 않는다
    tail = text.strip().splitlines()[-TAIL:]
    passed = [nid for s, nid in (m.groups() for m in map(SUMMARY_RE.match, text.splitlines()) if m)
              if s == "PASSED"]  # fmt: skip
    return {"tree": f"<{label}>", "cmd": shown, "exit": proc.returncode, "passed": passed,
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
    if green["exit"] != 0 or gk & {"failed", "error", "collection", "mixed"}:
        return "not green", EXIT_NOT
    if gk != {"passed"}:
        return "unknown", EXIT_UNKNOWN
    return "red-then-green", EXIT_RED_GREEN


def _print_text(receipt: dict[str, Any]) -> None:
    print(f"redgreen: {receipt['verdict']}")
    if receipt.get("note"):
        print(f"note    {receipt['note']}")
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


def _registered(root: Path) -> set[str] | None:
    """등록된 워크트리 경로들(못 읽으면 None)."""
    listed = _git(root, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        return None
    return {
        line[len("worktree ") :]
        for line in listed.stdout.splitlines()
        if line.startswith("worktree ")
    }


def _cleanup(root: Path, tmp: Path | None, created: list[Path]) -> bool:
    """자기가 만든(만들려던) 워크트리와 임시 폴더만 치운다. 다 치웠으면 True — 못 치운 것은 stderr.

    `remove -f -f` 는 끊긴 add 가 남긴 잠긴 등록도 지운다(`--force` 하나는 못 지운다). 그래도 남은
    등록은 prune 하고(폴더 없는 등록만 지운다 — 잠긴 것은 못 지운다) 다시 세어 본다."""
    names = {path: {str(path), str(path.resolve())} for path in created}
    hide = [str(tmp.resolve()), str(tmp)] if tmp is not None else []

    def tell(what: str, why: str) -> None:
        for spelled in hide:
            why = why.replace(spelled, "")
        print(f"cleanup: {what}: {why}", file=sys.stderr)

    listed = _registered(root) or set()
    for path in created:
        if not path.exists() and not names[path] & listed:
            continue  # add 가 폴더도 등록도 만들기 전에 끊겼다
        gone = _git(root, "worktree", "remove", "-f", "-f", str(path))
        if gone.returncode != 0:
            tell(f"could not remove worktree <{path.name}>", gone.stderr.strip())
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = tmp is None or not tmp.exists()
    if not ok:
        tell("temporary folder left", "<tmp>/" + tmp.name)
    if not created:
        return ok
    listed = _registered(root)
    if listed is not None and any(names[p] & listed for p in created):
        pruned = _git(root, "worktree", "prune")  # 폴더는 지웠는데 등록이 남은 것
        if pruned.returncode != 0:
            tell("git worktree prune failed", pruned.stderr.strip())
        listed = _registered(root)
    if listed is None:
        tell("could not list worktrees", "git worktree list failed — cannot tell what is left")
        return False
    if left := [p for p in created if names[p] & listed]:
        tell("cleanup failed", f"still registered: {', '.join(f'<{p.name}>' for p in left)}")
        return False
    return ok


def _terminated(signum: int, _frame: object) -> None:
    """SIGTERM · SIGINT 를 예외로 바꾼다 — main 의 finally 가 치우고 128 + 신호 번호로 끝난다."""
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="redgreen.py",
        description="Prove a fix commit's tests fail on its parent (with the fix's test files "
        "laid on top) and pass on the fix. Prints a receipt.",
        epilog="exit codes: 0 red-then-green · 1 not (not red, or not green) · 2 usage · "
        "3 could not decide (pytest found no such test, timed out, no result line) · "
        "4 cleanup failed (a temporary worktree or folder is left; the verdict still prints) · "
        "130 SIGINT · 143 SIGTERM (temporary worktrees removed first)",
    )
    ap.add_argument("--fix", required=True, help="the fix commit")
    ap.add_argument("--test", action="append", required=True, help="pytest node id (repeat)")
    ap.add_argument("--repo", type=Path, default=Path("."), help="repository (default .)")
    ap.add_argument("--json", action="store_true", help="print the receipt as JSON")
    ap.add_argument("--import-red-ok", action="store_true", help="import/fixture error = red")
    try:
        args = ap.parse_args(argv)
        args.test = [node_id(t) for t in args.test]
        root, sha, parent, files = resolve(args.repo, args.fix, args.test)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code else EXIT_RED_GREEN
    except Usage as why:
        print(f"usage: {why}", file=sys.stderr)
        return EXIT_USAGE
    if not has_pytest():
        print(f"usage: {sys.executable} cannot import pytest — run it with the worktree's .venv "
              "python (./.venv/bin/python tools/redgreen.py …)", file=sys.stderr)  # fmt: skip
        return EXIT_USAGE
    previous = {signum: signal.signal(signum, _terminated) for signum in HELD}
    tmp: Path | None = None
    created: list[Path] = []
    receipt: dict[str, Any] = {"fix": sha, "parent": parent, "tests": args.test, "files": files}
    receipt.update(interpreter=interpreter(), red=None, green=None, verdict="unknown")
    code, cleaned = EXIT_UNKNOWN, False
    try:
        with _held():  # 만든 바로 뒤에 끊겨도 tmp 를 안다 — finally 가 치운다
            tmp = Path(tempfile.mkdtemp(prefix="redgreen-"))
        for side, at in (("red", parent), ("green", sha)):
            with _held():  # add 도중에 끊기면 잠긴 등록이 남는다 — 끝난 뒤 신호를 받는다
                created.append(tmp / side)  # add 보다 먼저 적는다 — 중간에 끊겨도 치운다
                made = _git(root, "worktree", "add", "--quiet", "--detach", str(tmp / side), at)
            if made.returncode != 0:
                raise OSError(f"git worktree add failed: {made.stderr.strip()}")
        red_root = (tmp / "red").resolve()
        for rel in files:  # 고친 커밋의 시험 파일을 부모 트리 위에 얹는다
            target = tmp / "red" / rel
            if not target.resolve().is_relative_to(red_root):  # 부모 트리의 심볼릭 링크
                raise OSError(f"{rel} resolves outside the red tree on the parent — not written")
            shown = _git(root, "show", f"{sha}:{rel}", binary=True)
            if shown.returncode != 0:  # 빈 파일을 얹으면 「시험 없음」이 빨강처럼 보인다
                why = shown.stderr.decode(errors="replace").strip()
                raise OSError(f"git show {sha[:12]}:{rel} failed — not written: {why}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(shown.stdout)
        receipt["red"] = run_side(tmp / "red", args.test, "red")
        receipt["green"] = run_side(tmp / "green", args.test, "green")
        receipt["verdict"], code = verdict(receipt["red"], receipt["green"], args.import_red_ok)
        if mixed := [n for n, k in receipt["red"]["kinds"].items() if k == "mixed"]:
            passed = ", ".join(receipt["red"]["passed"])
            receipt["note"] = f"{', '.join(mixed)} expands to tests that passed before: {passed}"
    except (OSError, subprocess.SubprocessError) as error:
        print(f"unknown: {error}", file=sys.stderr)
    finally:
        for signum in HELD:  # 치우는 도중에 또 끊기지 않게
            signal.signal(signum, signal.SIG_IGN)
        try:
            cleaned = _cleanup(root, tmp, created)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, signal.SIG_DFL if handler is None else handler)
    if not cleaned:
        code = EXIT_CLEANUP
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    else:
        _print_text(receipt)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
