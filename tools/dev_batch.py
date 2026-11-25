#!/usr/bin/env python3
"""한 마일스톤의 PR 여럿을 형제 워크트리에서 동시에 만들 때의 장부(`dev-batch` 스킬). `--help` 참고.

왜 있나(2026-11-25): 두 묶음이 같은 파일을 가지면 하나가 머지된 뒤 다른 쪽이 충돌해 게이트(약 16분)
와 격리 검증을 다시 돈다. 겹침은 `add` 가 거부하고, 남의 경로에 쓴 커밋은 `scope` 가 sha 로 적는다.
상태 파일은 `--batch` · `RCM_DEV_BATCH` 로만 받는다(cwd 폴백 없음). GitHub 읽기는 `merge_pr`.

종료 코드: 0 정상 · 1 거부(이름 · 경로 · 겹침 · 범위 밖 경로, 이미 있는 상태 파일) · 2 사용법 ·
3 모름(git · GitHub 이 답하지 않음 — 그 칸은 `—`, 상태 파일이 깨짐).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import posixpath
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import guard_naming  # noqa: E402
import merge_pr  # noqa: E402

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_UNKNOWN = 0, 1, 2, 3
UNKNOWN = "—"
STATE_HINT = "~/.local/state/rcm-dev-batch/{id}.json"
#: 통째로 가질 수 없는 맨 위 뿌리 — 「src/ 전부」는 겹침 검사를 무력하게 만든다.
#: 상태 파일의 꼴 — 손으로 고치다 깨진 파일은 트레이스백이 아니라 종료 3(모름)이다.
KEYS = ("id", "base_ref", "base")
BUNDLE_KEYS = dict(branch=str, worktree=str, owns=list, shared=list, after=list, merge_order=int)
ROOTS = frozenset(". src src/remote_ci_monitor tests docs tools scripts .claude".split()) | {""}


class Stop(Exception):
    """`Stop(종료 코드, 메시지)` — main 이 메시지를 찍고 그 코드로 끝낸다."""


def _git(*args: str, cwd: Path | str = ".", strip: bool = True) -> str:
    """`strip=False` 는 `-z` 출력용 — porcelain 의 첫 칸 공백(` M`)도 자료다."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    run = {"capture_output": True, "text": True, "timeout": 120}
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, env=env, **run)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Stop(EXIT_UNKNOWN, f"git {args[0]}: {error}") from None
    if proc.returncode != 0:
        raise Stop(EXIT_UNKNOWN, f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip() if strip else proc.stdout


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise Stop(EXIT_USAGE, f"no batch at {path} — run init first")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        bundles = state["bundles"]
        shapes = [isinstance(bundles, dict), *(isinstance(state[k], str) for k in KEYS)]
        for b in bundles.values():
            shapes += [isinstance(b[k], t) for k, t in BUNDLE_KEYS.items()]
        if not all(shapes):
            raise TypeError("a field has the wrong type")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise Stop(EXIT_UNKNOWN, f"{path} is corrupt ({type(error).__name__}: {error})") from None
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def cmd_init(path: Path, args: argparse.Namespace) -> int:
    if path.exists():
        raise Stop(EXIT_REFUSED, f"{path} already exists — a batch is never overwritten")
    remote = args.base.split("/", 1)[0]
    if "/" in args.base and remote in _git("remote").split():
        _git("fetch", "--quiet", remote)
    base = _git("rev-parse", "--verify", f"{args.base}^{{commit}}")
    repo = args.repo or merge_pr.repo_from_remote(_git("remote", "get-url", "origin"))
    _save(path, {"id": args.id, "base_ref": args.base, "base": base, "repo": repo, "bundles": {}})
    print(f"batch {args.id}: base {args.base} = {base} · {path}")
    return EXIT_OK


def _norm(raw: str) -> str:
    """경로 하나를 묶음이 가질 수 있는 꼴로. 안 되면 Stop(거부)."""
    p = raw.strip().removeprefix("./")
    p = p[:-1] if p.endswith("/") and p != "/" else p
    parts = p.split("/")
    for bad, why in (
        (any(ch in p for ch in "*?["), "a glob"),
        (p.startswith(("/", "~")), "absolute — paths are relative to the repository root"),
        (".." in parts, "it climbs out with `..`"),
        (p.casefold() in ROOTS, "a whole top-level root — name the files or a narrower folder"),
        ("" in parts or "." in parts, "an empty or `.` segment — write the path plainly"),
    ):
        if bad:
            raise Stop(EXIT_REFUSED, f"path {raw!r} refused: {why}")
    return posixpath.normpath(p)


def _within(path: str, claim: str, fold: bool = False) -> bool:
    """`fold` 는 겹침 검사용 — macOS 기본 파일 시스템에서 `Docs/A.md` 는 `docs/a.md` 다."""
    if fold:
        path, claim = path.casefold(), claim.casefold()
    return path == claim or path.startswith(claim + "/")


def cmd_add(path: Path, args: argparse.Namespace) -> int:
    state = _load(path)
    bundles = state["bundles"]
    if args.bundle in bundles:
        raise Stop(EXIT_REFUSED, f"bundle {args.bundle} already exists")
    if why := guard_naming.check_branch(args.branch):
        raise Stop(EXIT_REFUSED, f"branch name: {why}")
    wt = Path(args.worktree)
    if not wt.is_absolute():
        raise Stop(EXIT_USAGE, f"--worktree must be an absolute path (got {args.worktree})")
    if not wt.is_dir():
        raise Stop(EXIT_REFUSED, f"no worktree at {wt}")
    if (on := _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt)) != args.branch:
        raise Stop(EXIT_REFUSED, f"{wt} is on {on}, not {args.branch}")
    if _git("merge-base", state["base"], "HEAD", cwd=wt) != state["base"]:
        raise Stop(EXIT_REFUSED, f"{args.branch} does not contain the base {state['base']}")
    owns, shared = ([_norm(p) for p in claim] for claim in (args.owns, args.shared))
    if unknown := [name for name in args.after if name not in bundles]:
        raise Stop(EXIT_REFUSED, f"--after {', '.join(unknown)}: no such bundle")
    for name, other in bundles.items():
        if other["branch"] == args.branch:
            raise Stop(EXIT_REFUSED, f"{args.branch} already belongs to {name}")
        if other.get("merged"):
            continue
        if other["merge_order"] == args.merge_order:
            raise Stop(EXIT_REFUSED, f"merge order {args.merge_order} is taken by {name}")
        # owns 는 남의 owns · shared 와, shared 는 남의 owns 와 겹치면 안 된다
        theirs = [(q, "owns") for q in other["owns"]] + [(q, "shared") for q in other["shared"]]
        for p, kind in [(p, "owns") for p in owns] + [(p, "shared") for p in shared]:
            for q, their in theirs:
                if "owns" in (kind, their) and (_within(p, q, True) or _within(q, p, True)):
                    raise Stop(EXIT_REFUSED, f"{kind} {p} overlaps {name}'s {their} {q}")
    bundles[args.bundle] = dict(branch=args.branch, worktree=str(wt), owns=owns, shared=shared)
    bundles[args.bundle].update(after=list(args.after), merge_order=args.merge_order, merged=None)
    _save(path, state)
    print(f"added {args.bundle}: {args.branch} owns {', '.join(owns)}")
    return EXIT_OK


def _bundle(state: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in state["bundles"]:
        raise Stop(EXIT_USAGE, f"no bundle named {name}")
    return state["bundles"][name]


def cmd_scope(path: Path, args: argparse.Namespace) -> int:
    """base 뒤 머지가 아닌 커밋과 워크트리의 바뀐 파일이 건드린 경로가 owns ∪ shared 안인지."""
    state = _load(path)
    b = _bundle(state, args.bundle)
    wt = b["worktree"]
    allowed = b["owns"] + b["shared"]
    # base 브랜치(origin/dev)에 이미 있는 커밋은 뺀다 — 들여온 병합으로 따라온 남의 묶음이다
    tip = _git("rev-parse", "--verify", f"{state['base_ref']}^{{commit}}", cwd=wt)
    # `-z` — 경로를 따옴표 · 8진수로 싸지 않는다(공백 · 한글 이름)
    log = ("log", "-z", "--no-merges", "--no-renames", "--format=@%H", "--name-only", "HEAD")
    out = _git(*log, f"^{state['base']}", f"^{tip}", cwd=wt, strip=False)
    touched: list[tuple[str, str]] = []
    sha, commits = "", 0
    for item in out.split("\0"):
        item = item.removeprefix("\n")
        if item.startswith("@"):
            sha, commits = item[1:], commits + 1
        elif item:
            touched.append((sha[:12], item))
    status = ("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    for entry in _git(*status, cwd=wt, strip=False).split("\0"):
        if entry:  # `XY path` — 앞 두 칸은 상태(공백일 수 있다), 셋째는 띄어쓰기
            touched.append(("worktree", entry[3:]))
    stray = [(w, rel) for w, rel in touched if not any(_within(rel, p) for p in allowed)]
    print(f"{args.bundle}: {commits} commits since {state['base'][:12]} on {b['branch']}")
    for where, rel in stray:
        print(f"  {where}  {rel}  (outside owns/shared)")
    print("scope ok" if not stray else f"{len(stray)} paths outside the bundle's scope")
    return EXIT_REFUSED if stray else EXIT_OK


def _row(name: str, b: dict[str, Any], state: dict[str, Any], api: merge_pr.Api) -> dict[str, Any]:
    row = dict(bundle=name, branch=b["branch"], order=b["merge_order"], local=UNKNOWN)
    row.update(remote=UNKNOWN, pr=UNKNOWN, rcm_ci=UNKNOWN, verified=UNKNOWN)
    row["merged"] = b.get("merged") or UNKNOWN
    with contextlib.suppress(Stop):
        row["local"] = _git("rev-parse", "HEAD", cwd=b["worktree"])
    repo = state.get("repo")
    try:
        if not repo:
            raise merge_pr.ApiError("no OWNER/REPO for this batch")
        try:
            head = api.request("GET", f"repos/{repo}/branches/{b['branch']}")["commit"]["sha"]
            row["remote"] = head
        except merge_pr.ApiError as error:
            if error.status != 404:
                raise
            head, row["remote"] = None, "not pushed"
        owner = repo.split("/", 1)[0]
        query = f"repos/{repo}/pulls?head={owner}:{b['branch']}&state=all&per_page=100"
        prs = sorted(api.request("GET", query) or [], key=lambda p: p["number"])
        pr = prs[-1] if prs else None
        row["pr"] = f"#{pr['number']}" if pr else "none"
        if pr and pr.get("merged_at"):
            row["merged"] = b["merged"] = pr.get("merge_commit_sha") or "yes"
        elif not b.get("merged"):
            row["merged"] = "no"
            if pr and pr.get("state") != "open":
                row["pr"] += " (closed)"  # 머지 없이 닫힌 PR — 머지할 것이 없다
        sha = pr["head"]["sha"] if pr else head
        row["rcm_ci"] = "none" if sha is None else _rcm_ci(api, repo, sha)
        row["verified"] = "no"
        if pr:
            path = f"repos/{repo}/issues/{pr['number']}/comments"
            bodies = (str(c.get("body") or "") for c in merge_pr._pages(api, path))
            marks = [m for body in bodies for m in merge_pr.VERIFIED_RE.findall(body)]
            row["verified"] = "yes" if sha in marks else "stale" if marks else "no"
    except (merge_pr.ApiError, ValueError, KeyError, TypeError, AttributeError) as error:
        # gh 가 JSON 이 아닌 것(HTML 오류 쪽)이나 다른 모양을 주면 모름 — 트레이스백이 아니다
        print(f"{name}: GitHub did not answer — {type(error).__name__}: {error}", file=sys.stderr)
    row["next"] = _next(row, b, state)
    return row


def _rcm_ci(api: merge_pr.Api, repo: str, sha: str) -> str:
    c = merge_pr.read_checks(api, repo, sha, ("rcm/ci",))
    if c.failed:  # `rcm/ci (error)` → `error`
        return c.failed[0].rsplit("(", 1)[-1].rstrip(")")
    found = ((c.pending, "pending"), (c.missing, "not reported"))
    return next((word for hit, word in found if hit), "success")


def _next(row: dict[str, Any], b: dict[str, Any], state: dict[str, Any]) -> str:
    """다음에 할 일 한 마디. 모르는 칸이 있으면 `—`."""
    if b.get("merged"):
        return "done"
    if UNKNOWN in (row["local"], row["remote"], row["pr"], row["rcm_ci"], row["verified"]):
        return UNKNOWN
    if row["remote"] != row["local"]:
        return "push"
    if row["pr"] == "none":
        return "open PR"
    if row["pr"].endswith("(closed)"):
        return "reopen or open a new PR"
    if row["rcm_ci"] in ("pending", "not reported"):
        return "wait for rcm/ci"
    if row["rcm_ci"] != "success":
        return "fix rcm/ci"  # failure · error · 실패한 check-run 의 결론
    if row["verified"] != "yes":
        return "verify"
    waiting = [a for a in b["after"] if not state["bundles"][a].get("merged")]
    return f"wait for {', '.join(waiting)}" if waiting else "merge"


def cmd_status(path: Path, args: argparse.Namespace, api: merge_pr.Api) -> int:
    state = _load(path)
    names = [args.bundle] if args.bundle else list(state["bundles"])
    names.sort(key=lambda n: _bundle(state, n)["merge_order"])  # 없는 이름은 사용법 오류
    rows = [_row(name, state["bundles"][name], state, api) for name in names]
    _save(path, state)  # 새로 안 머지 sha 를 남긴다 — 겹침 검사가 머지된 묶음을 뺀다
    if args.json:
        print(json.dumps({"id": state["id"], "base": state["base"], "bundles": rows}, indent=2))
    else:
        cols = ("bundle", "branch", "local", "remote", "pr", "rcm_ci", "verified", "merged", "next")
        short = {"local", "remote", "merged"}
        cells = [[str(r[c])[: 12 if c in short else None] for c in cols] for r in rows]
        width = [max(len(x) for x in col) for col in zip(cols, *cells, strict=True)]
        for line in [list(cols), *cells]:
            print("  ".join(x.ljust(w) for x, w in zip(line, width, strict=True)).rstrip())
    unknown = any(UNKNOWN in (str(v) for v in r.values()) for r in rows)
    return EXIT_UNKNOWN if unknown else EXIT_OK


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="dev_batch.py",
        description="Ledger for several PRs of one milestone built at once in sibling worktrees.",
        epilog="exit codes: 0 ok · 1 refused (name, path, overlap, out-of-scope path) · 2 usage "
        "· 3 unknown (git or GitHub did not answer; those cells print —)",
    )
    ap.add_argument("--batch", type=Path, help="state file (or $RCM_DEV_BATCH)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="pin the base sha")
    init.add_argument("--id", required=True)
    init.add_argument("--base", default="origin/dev")
    init.add_argument("--repo", help="OWNER/REPO (default: the origin remote)")
    add = sub.add_parser("add", help="add a bundle (one branch, one worktree)")
    for flag in ("--bundle", "--branch", "--worktree"):
        add.add_argument(flag, required=True)
    add.add_argument("--owns", nargs="+", required=True)
    for flag in ("--shared", "--after"):
        add.add_argument(flag, nargs="+", default=[])
    add.add_argument("--merge-order", type=int, required=True)
    scope = sub.add_parser("scope", help="paths the bundle's commits touched outside its claim")
    scope.add_argument("--bundle", required=True)
    status = sub.add_parser("status", help="heads, PR, rcm/ci, verified: and merge per bundle")
    status.add_argument("--bundle")
    status.add_argument("--json", action="store_true")
    return ap


def main(argv: list[str] | None = None, *, api: merge_pr.Api | None = None) -> int:
    try:
        args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code else EXIT_OK
    raw = args.batch or os.environ.get("RCM_DEV_BATCH")
    if not raw:
        hint = STATE_HINT.format(id=getattr(args, "id", None) or "<id>")
        print(f"no state file: pass --batch {hint} or set RCM_DEV_BATCH", file=sys.stderr)
        return EXIT_USAGE
    path = Path(raw).expanduser()
    commands = {"init": cmd_init, "add": cmd_add, "scope": cmd_scope}
    try:
        if args.cmd in commands:
            return commands[args.cmd](path, args)
        return cmd_status(path, args, api or merge_pr.GhApi())
    except Stop as stop:
        code, message = stop.args
        word = {EXIT_UNKNOWN: "unknown", EXIT_USAGE: "usage"}.get(code, "refused")
        print(f"{word}: {message}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
