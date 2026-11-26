#!/usr/bin/env python3
"""한 마일스톤의 PR 여럿을 형제 워크트리에서 동시에 만들 때의 장부(`dev-batch` 스킬). `--help` 참고.

왜 있나(2026-11-25): 두 묶음이 같은 파일을 가지면 하나가 머지된 뒤 다른 쪽이 충돌해 게이트(약 16분)
와 격리 검증을 다시 돈다. 겹침은 `add` 가 거부하고, 남의 경로에 쓴 커밋은 `scope` 가 sha 로 적는다.
상태 파일은 `--batch` · `RCM_DEV_BATCH` 로만 받는다(cwd 폴백 없음). GitHub 읽기는 `merge_pr`.

모르는 것을 아는 값으로 두지 않는다(3라운드). status 는 GitHub 칸을 지역 변수에 모았다가 읽기가 다
끝난 뒤에만 옮긴다 — 하나라도 실패하면 그 행의 GitHub 칸은 모두 `—` 다. 빈 답(None) · 목록이어야 할
자리의 목록 아닌 답 · 모르는 상태 값도 「없음」이 아니라 모름이다(`merge_pr` 의 읽기는 빈 답을 빈
목록으로 세므로 `_Strict` 가 감싸 걸러 낸다). scope 는 병합 커밋이 스스로 바꾼 경로(충돌 해결 ·
악한 병합)도 보고, 워크트리가 묶음의 브랜치에 있지 않으면 답하지 않는다(종료 3).

4라운드. scope 는 PR 이 실제로 바꾸는 것(`git diff <merge-base(base_ref, HEAD)> HEAD`)도
본다 — 한쪽을 버린 병합(`merge -s ours` · `checkout --ours`)은 `--cc` 에 없지만 dev 의 변경을
되돌린다. 커밋에서 못 본 그 경로는 `merge-result` 로 적는다. status 는 새로 안 머지 sha 가 있을
때만, fcntl 잠금 안에서 상태 파일을 다시 읽어 그 칸만 고친다(add · init 도 같은 잠금) — 그동안의
add 를 덮지 않는다. 열린 PR 의 head 가 로컬 head 와 다르면 next 는 「wait for PR head」이고 옛
head 의 rcm/ci · verified: 는 쓰지 않는다(`old head`). 본문 없는 댓글 · context 없는 상태 · 이름
없는 check-run · 문자열이 아닌 merged_at 은 모름이다. `add --worktree` 는 워크트리의 맨 위 폴더여야
한다.

종료 코드: 0 정상 · 1 거부(이름 · 경로 · 겹침 · 범위 밖 경로, 이미 있는 상태 파일) · 2 사용법(없는
`--base`, 상태 파일을 못 씀) · 3 모름(git · GitHub 이 답하지 않음 — 그 칸은 `—`, 상태 파일이 깨짐,
워크트리가 다른 브랜치에 있음).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Iterator
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
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
#: GitHub 이 주는 값 가운데 아는 것 — 이 밖의 값은 모름(`—`)이다. merge_pr 는 모르는 상태 값을
#: 「ok」로 세지만 그 뜻은 바꾸지 않고 여기서 거른다
STATUS_STATES = frozenset({"success", "pending", "failure", "error"})
RUN_STATUSES = frozenset({"queued", "in_progress", "completed", "waiting", "requested", "pending"})
RUN_CONCLUSIONS = frozenset(
    {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"}
    | {"stale", "startup_failure"}
)


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
        shapes.append(bool(SHA_RE.match(str(state["base"]))))
        for b in bundles.values():
            shapes += [isinstance(b[k], t) for k, t in BUNDLE_KEYS.items()]
            # 경로 · 이름 목록의 칸은 문자열이고, after 는 이 장부에 있는 묶음만 가리킨다
            shapes += [isinstance(x, str) for k in ("owns", "shared", "after") for x in b[k]]
            shapes += [a in bundles for a in b["after"]]
            # merged 는 None(아직) 이나 머지 커밋 sha 뿐이다 — "no" 는 참이라 「done」으로 읽혔다
            shapes.append(b["merged"] is None or bool(SHA_RE.match(str(b["merged"]))))
        if not all(shapes):
            raise TypeError("a field has the wrong type or names a bundle that is not there")
        if loop := _after_loop(bundles):
            raise ValueError(f"after goes round in a circle ({' → '.join(loop)}) — never mergeable")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise Stop(EXIT_UNKNOWN, f"{path} is corrupt ({type(error).__name__}: {error})") from None
    return state


def _after_loop(bundles: dict[str, Any]) -> list[str]:
    """after 를 따라가다 제자리로 오는 고리(없으면 []). 자기 자신을 가리키는 것도 고리다 —
    고리 안의 묶음은 영원히 「wait for」라 머지되지 않는다. 묶음은 몇 개뿐이라 그냥 다 걷는다."""

    def walk(name: str, trail: list[str]) -> list[str]:
        if name in trail:
            return [*trail[trail.index(name) :], name]
        for after in bundles[name]["after"]:
            if loop := walk(after, [*trail, name]):
                return loop
        return []

    return next((loop for start in bundles if (loop := walk(start, []))), [])


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise Stop(EXIT_USAGE, f"cannot write {path}: {error}") from None


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """상태 파일의 읽고-고치고-쓰기를 곁 파일(`<이름>.lock`)의 fcntl 잠금 안에서 한다 — 두 세션의
    add · status 가 서로의 쓰기를 덮지 않게."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path.with_name(path.name + ".lock"), "a")
    except OSError as error:
        raise Stop(EXIT_USAGE, f"cannot write {path}: {error}") from None
    with handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def cmd_init(path: Path, args: argparse.Namespace) -> int:
    with _locked(path):
        return _init(path, args)


def _init(path: Path, args: argparse.Namespace) -> int:
    if path.exists():
        raise Stop(EXIT_REFUSED, f"{path} already exists — a batch is never overwritten")
    remote = args.base.split("/", 1)[0]
    if "/" in args.base and remote in _git("remote").split():
        _git("fetch", "--quiet", remote)
    try:
        base = _git("rev-parse", "--verify", "--quiet", f"{args.base}^{{commit}}")
    except Stop:
        raise Stop(EXIT_USAGE, f"--base {args.base!r} is not a commit in this repository") from None
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
        (not p.isprintable(), "a control or invisible character"),
        ("\\" in p, "a backslash — git paths use `/`, so it never meets another bundle's path"),
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
    with _locked(path):
        return _add(path, args)


def _add(path: Path, args: argparse.Namespace) -> int:
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
    if Path(_git("rev-parse", "--show-toplevel", cwd=wt)).resolve() != wt.resolve():
        # 하위 폴더도 git 은 그 워크트리로 읽지만 scope · status 는 이 경로를 워크트리로 쓴다
        raise Stop(EXIT_REFUSED, f"{wt} is not the top level of a worktree")
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


def _shown(rel: str) -> str:
    """경로를 찍는 꼴 — 제어 · 보이지 않는 글자가 있으면 repr(줄바꿈이 가짜 줄을 만들지 않게)."""
    return rel if rel.isprintable() else repr(rel)


def cmd_scope(path: Path, args: argparse.Namespace) -> int:
    """base 뒤 커밋(병합 커밋이 스스로 바꾼 경로까지)과 워크트리의 바뀐 파일이 건드린 경로가
    owns ∪ shared 안인지."""
    state = _load(path)
    b = _bundle(state, args.bundle)
    wt = b["worktree"]
    allowed = b["owns"] + b["shared"]
    # 워크트리의 HEAD 와 파일을 읽는다 — 다른 브랜치(떨어진 HEAD 포함)면 이 묶음의 답이 아니다
    if (on := _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt)) != b["branch"]:
        where = "a detached HEAD" if on == "HEAD" else on
        raise Stop(EXIT_UNKNOWN, f"{wt} is on {where}, not {b['branch']} — scope reads the "
                   f"worktree's HEAD and files, so it cannot answer for {args.bundle}; switch it "
                   "back first")  # fmt: skip
    # base 브랜치(origin/dev)에 이미 있는 커밋은 뺀다 — 들여온 병합으로 따라온 남의 묶음이다
    tip = _git("rev-parse", "--verify", f"{state['base_ref']}^{{commit}}", cwd=wt)
    since = ("HEAD", f"^{state['base']}", f"^{tip}")
    shas = _git("rev-list", "--no-merges", *since, cwd=wt).split()
    merges = _git("rev-list", "--merges", *since, cwd=wt).split()
    touched: list[tuple[str, str]] = []
    # 커밋마다 따로 읽는다 — log 한 줄기에 머리와 경로를 섞으면 `@` 로 시작하는 경로가 머리로
    # 읽혔다. `-z` 는 경로를 따옴표 · 8진수로 싸지 않는다(공백 · 한글 · 줄바꿈 이름)
    tree = ("diff-tree", "-z", "-r", "--no-commit-id", "--name-only", "--no-renames", "--root")
    # 병합 커밋은 `--cc` — 어느 부모와도 다른 경로만 나온다. 한쪽 부모에서 그대로 온 것(들여온
    # 남의 묶음)은 빠지고 충돌 해결 · 악한 병합이 병합 커밋 안에서 바꾼 것만 남는다
    combined = ("diff-tree", "--cc", "-z", "-r", "--no-commit-id", "--name-only")
    for sha, how in [(sha, tree) for sha in shas] + [(sha, combined) for sha in merges]:
        names = _git(*how, sha, cwd=wt, strip=False).split("\0")
        touched += [(sha[:12], name) for name in names if name]
    commits = len(shas)
    # PR 이 실제로 바꾸는 것 — `merge -s ours` · `checkout --ours` 처럼 한쪽을 버린 병합은 `--cc` 에
    # 아무것도 없지만 diff 는 dev 의 변경을 되돌린다. 커밋에서 못 본 경로는 merge-result 로 적는다
    fork = _git("merge-base", tip, "HEAD", cwd=wt)
    seen = {name for _, name in touched}
    diff = _git("diff", "-z", "--name-only", "--no-renames", fork, "HEAD", cwd=wt, strip=False)
    touched += [("merge-result", name) for name in diff.split("\0") if name and name not in seen]
    status = ("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames")
    for entry in _git(*status, cwd=wt, strip=False).split("\0"):
        if entry:  # `XY path` — 앞 두 칸은 상태(공백일 수 있다), 셋째는 띄어쓰기
            touched.append(("worktree", entry[3:]))
    stray = [(w, rel) for w, rel in touched if not any(_within(rel, p) for p in allowed)]
    merged = f" (+{len(merges)} merges)" if merges else ""
    print(f"{args.bundle}: {commits} commits{merged} since {state['base'][:12]} on {b['branch']}")
    for where, rel in stray:
        print(f"  {where}  {_shown(rel)}  (outside owns/shared)")
    print("scope ok" if not stray else f"{len(stray)} paths outside the bundle's scope")
    return EXIT_REFUSED if stray else EXIT_OK


class _Strict:
    """status 의 GitHub 읽기를 감싼다. `merge_pr._pages` 는 빈 답(None)을 빈 목록으로 세고
    `read_checks` 는 모르는 상태 값을 「ok」로 센다 — merge_pr 의 뜻은 그대로 두고, 여기서 꼴이
    다른 답을 ValueError 로 바꿔 그 행의 칸을 `—` 로 남긴다."""

    def __init__(self, api: merge_pr.Api, repo: str) -> None:
        self.api, self.repo = api, repo

    def request(self, method: str, path: str, fields: dict[str, str] | None = None) -> Any:
        body = self.api.request(method, path, fields)
        rest = path.removeprefix(f"repos/{self.repo}/")
        if rest.startswith("branches/"):
            sha = body.get("commit", {}).get("sha") if isinstance(body, dict) else None
            _need(isinstance(sha, str) and SHA_RE.match(sha), path, "no 40-hex commit sha")
            return body
        if re.match(r"commits/[0-9a-f]{40}/check-runs\?", rest):
            runs = body.get("check_runs") if isinstance(body, dict) else None
            _need(_dicts(runs), path, "check_runs is not a list of objects")
            for run in runs:
                status, end = run.get("status"), run.get("conclusion")
                _need(isinstance(run.get("name"), str), path, "a check-run has no name")
                _need(status in RUN_STATUSES, path, f"unknown check-run status {status!r}")
                done = status != "completed" or end in RUN_CONCLUSIONS
                _need(done, path, f"unknown check-run conclusion {end!r}")
            return body
        known = ("pulls?", "issues/", "commits/")
        _need(rest.startswith(known), path, "not a read status makes")
        _need(_dicts(body), path, "not a list of objects")
        if re.match(r"commits/[0-9a-f]{40}/statuses\?", rest):
            for status in body:
                state = status.get("state")
                _need(state in STATUS_STATES, path, f"unknown commit status state {state!r}")
                _need(isinstance(status.get("context"), str), path, "a status has no context")
        if rest.startswith("issues/"):  # 본문 없는 댓글을 빈 글로 읽으면 「표식 없음」이 된다
            _need(all(isinstance(c.get("body"), str) for c in body), path, "a comment has no body")
        return body


def _dicts(body: Any) -> bool:
    return isinstance(body, list) and all(isinstance(x, dict) for x in body)


def _need(ok: Any, path: str, why: str) -> None:
    if not ok:
        raise ValueError(f"{path.split('?', 1)[0]}: {why}")


def _pr_ok(pr: Any) -> dict[str, Any]:
    """PR 하나의 꼴 — 빠진 칸(state · merged_at …)은 「닫힘 · 안 머지됨」이 아니라 모름이다."""
    _need(isinstance(pr, dict), "pulls", "a PR is not an object")
    _need(type(pr.get("number")) is int, "pulls", "a PR has no number")
    _need(pr.get("state") in ("open", "closed"), "pulls", f"PR state {pr.get('state')!r}")
    merged_at = pr.get("merged_at", 0)  # 빠진 칸도 None 이 아니다
    _need(merged_at is None or isinstance(merged_at, str), "pulls", f"merged_at {merged_at!r}")
    sha = (pr.get("head") or {}).get("sha")
    _need(isinstance(sha, str) and SHA_RE.match(sha), "pulls", "a PR has no 40-hex head sha")
    return pr


def _row(name: str, b: dict[str, Any], state: dict[str, Any], api: merge_pr.Api) -> dict[str, Any]:
    row = dict(bundle=name, branch=b["branch"], order=b["merge_order"], local=UNKNOWN)
    row.update(remote=UNKNOWN, pr=UNKNOWN, pr_head=UNKNOWN, rcm_ci=UNKNOWN, verified=UNKNOWN)
    row["merged"] = b.get("merged") or UNKNOWN
    # 워크트리의 HEAD 가 아니라 묶음 브랜치의 끝 — 워크트리를 다른 브랜치로 옮겨도 이 브랜치다
    with contextlib.suppress(Stop):
        row["local"] = _git("rev-parse", "--verify", f"refs/heads/{b['branch']}^{{commit}}",
                            cwd=b["worktree"])  # fmt: skip
    repo = state.get("repo")
    #: GitHub 에서 읽은 칸 — 읽기가 다 끝난 뒤에만 row 로 옮긴다. 읽기 전에 아는 값을 두면
    #: 실패한 읽기가 그 값으로 남는다(3라운드 H2: verified 가 "no" 로 남아 종료 0)
    cells: dict[str, str] = {}
    try:
        if not repo:
            raise merge_pr.ApiError("no OWNER/REPO for this batch")
        api = _Strict(api, repo)
        try:
            head = api.request("GET", f"repos/{repo}/branches/{b['branch']}")["commit"]["sha"]
            cells["remote"] = head
        except merge_pr.ApiError as error:
            if error.status != 404:
                raise
            head, cells["remote"] = None, "not pushed"
        owner = repo.split("/", 1)[0]
        query = f"repos/{repo}/pulls?head={owner}:{b['branch']}&state=all"
        prs = sorted((_pr_ok(p) for p in merge_pr._pages(api, query)), key=lambda p: p["number"])
        pr = prs[-1] if prs else None
        cells["pr"] = f"#{pr['number']}" if pr else "none"
        cells["pr_head"] = pr["head"]["sha"] if pr else "none"
        merged = None
        if pr and pr["merged_at"]:
            merged = pr.get("merge_commit_sha")
            _need(isinstance(merged, str) and SHA_RE.match(merged), "pulls", "merged, no sha")
            cells["merged"] = merged
        elif not b.get("merged"):
            cells["merged"] = "no"
            if pr and pr["state"] != "open":
                cells["pr"] += " (closed)"  # 머지 없이 닫힌 PR — 머지할 것이 없다
        sha = pr["head"]["sha"] if pr else head
        if pr and pr["state"] == "open" and sha != row["local"]:
            # PR 이 아직 옛 head 다 — 그 sha 의 rcm/ci · verified: 는 지금 브랜치의 것이 아니다
            cells["rcm_ci"] = cells["verified"] = "old head"
        else:
            cells["rcm_ci"] = "none" if sha is None else _rcm_ci(api, repo, sha)
            cells["verified"] = _verified(api, repo, pr, sha)
        row.update(cells)
        if merged:
            b["merged"] = merged  # 새로 안 머지 sha 만 장부에 남긴다
    except (merge_pr.ApiError, ValueError, KeyError, TypeError, AttributeError) as error:
        # gh 가 JSON 이 아닌 것(HTML 오류 쪽)이나 다른 모양을 주면 모름 — 트레이스백이 아니다
        print(f"{name}: GitHub did not answer — {type(error).__name__}: {error}", file=sys.stderr)
    row["next"] = _next(row, b, state)
    return row


def _verified(api: merge_pr.Api, repo: str, pr: dict[str, Any] | None, sha: str | None) -> str:
    """그 head 에 단 `verified:` 표식 — PR 이 없으면 표식도 없다. 댓글을 못 읽으면 예외(모름)."""
    if not pr:
        return "no"
    path = f"repos/{repo}/issues/{pr['number']}/comments"
    bodies = (c["body"] for c in merge_pr._pages(api, path))  # _Strict 가 str 임을 봤다
    marks = [m for body in bodies for m in merge_pr.VERIFIED_RE.findall(body)]
    return "yes" if sha in marks else "stale" if marks else "no"


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
    if UNKNOWN in (row["local"], row["remote"], row["pr"], row["pr_head"], row["rcm_ci"],
                   row["verified"]):  # fmt: skip
        return UNKNOWN
    if row["remote"] != row["local"]:
        return "push"
    if row["pr"] == "none":
        return "open PR"
    if row["pr"].endswith("(closed)"):
        return "reopen or open a new PR"
    if row["pr_head"] != row["local"]:
        return "wait for PR head"  # push 는 됐는데 PR 이 아직 옛 head 를 가리킨다
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
    before = {name: state["bundles"][name]["merged"] for name in names}
    rows = [_row(name, state["bundles"][name], state, api) for name in names]
    learned = {n: state["bundles"][n]["merged"] for n in names if state["bundles"][n]["merged"]}
    learned = {n: sha for n, sha in learned.items() if before[n] != sha}
    if learned:  # 새로 안 머지 sha 만 남긴다 — 겹침 검사가 머지된 묶음을 뺀다
        with _locked(path):  # 읽는 동안 다른 세션이 add 했을 수 있다 — 다시 읽고 그 칸만 고친다
            fresh = _load(path)
            for name, sha in learned.items():
                if name in fresh["bundles"] and fresh["bundles"][name]["merged"] is None:
                    fresh["bundles"][name]["merged"] = sha
            _save(path, fresh)
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
