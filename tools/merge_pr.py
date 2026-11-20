#!/usr/bin/env python3
"""PR 하나를 머지하는 관문 — 이 레포에서 머지는 이 도구로만 한다.

  python3 tools/merge_pr.py <N> [--repo O/R] [--wait] [--timeout S] [--no-verify "<reason>"]
                                [--dry-run] [--delete-branch]

왜 있나(2026-11-20): 백그라운드 감시 스크립트가 아직 pending 인 체크가 남았는데 REST 로 머지했고
두 번은 실제로 머지됐다 — 룰셋은 필수 체크(`test`)만 본다(사건 6c). 격리 재검증 없이 머지된 PR 도
셋이었다(#213 · #216 · dolomood #992, 사건 2). `tools/guard_merge.py` 훅이 `gh pr merge` 와 REST
머지를 막고 이 도구로 보낸다.

순서:
  1. PR 을 읽어 head sha 를 **고정**한다. 열려 있지 않으면 거부.
  2. base 브랜치 룰셋의 필수 상태 체크(`GET rules/branches/{base}` 의 required_status_checks)를
     읽는다 — 이름을 박지 않는다(필수 체크가 `test` 에서 `rcm/ci` 로 바뀔 수 있다). 그 sha 의
     check-runs 와 commit statuses(같은 context 는 최신 것만)를 모은다. 하나라도 failure ·
     cancelled · timed_out · action_required(· startup_failure · stale · error) 면 거부. 하나라도
     끝나지 않았거나 필수 context 가 check-run 으로도 status 로도 아직 없으면 `--wait` 는
     `--timeout` 까지 기다리고(기다리는 동안 head 가 움직이면 거부), 아니면 거부. 룰셋에 필수
     체크가 없으면 끝난 체크가 하나라도 있어야 한다.
  3. PR 댓글에서 `verified: <40자 head sha>` 줄(격리 검증을 마친 리드가 다는 한 줄 — 형식 고정)을
     찾는다. 없거나 다른 sha 면 거부. `--no-verify "<reason>"` 은 그 이유를 PR 댓글로 남기고
     지나간다(문서만 · 시험만 PR 용).
  4. 충돌(`mergeable` 이 false 이거나 `mergeable_state` 가 dirty)이면 거부.
  5. `PUT pulls/N/merge` 에 `sha=<head sha>` · `merge_method=merge` · `commit_title="<제목> (#N)"`.
     sha 를 주므로 그 사이 head 가 움직였으면 GitHub 이 409 로 거절한다.
  6. PR 을 다시 읽어 merged 인지 확인한다. `--delete-branch` 일 때만 원격 브랜치를 지운다.

종료 코드: 0 머지됨(`--dry-run` 은 머지할 수 있음) · 1 거부 · 2 사용법 · 3 네트워크 · 모름.

GitHub 은 `gh api` 를 subprocess 로 부른다(`GhApi`). 토큰은 환경의 `GH_TOKEN`, 없으면 gh 의 활성
계정이다 — 이 레포는 `GH_TOKEN=$(gh auth token --user monocsp)` 로 고정한다. 시험은 `main()` 에
가짜 `api` · 시계 · `sleep` 을 넣는다.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

EXIT_MERGED = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_UNKNOWN = 3

#: 격리 검증 표식 — 줄 하나 전체가 이 모양이어야 한다(인용 · 짧은 sha · 대문자는 아니다).
VERIFIED_RE = re.compile(r"^verified: ([0-9a-f]{40})[ \t]*$", re.M)
FAILED_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
)
FAILED_STATES = frozenset({"failure", "error"})
PAGE = 100
DEFAULT_TIMEOUT = 3600.0
POLL_SECONDS = 30.0
#: `--delete-branch` 가 절대 지우지 않는 브랜치 — `dev` → `main` PR 의 head 는 `dev` 다.
LONG_LIVED = frozenset({"main", "dev", "master"})
REMOTE_RE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class ApiError(Exception):
    """GitHub 이 답하지 않았거나(status None · 5xx — 모름) 거절했다(4xx)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def unknown(self) -> bool:
        return self.status is None or self.status >= 500


class Api(Protocol):
    def request(self, method: str, path: str, fields: dict[str, str] | None = None) -> Any: ...


class GhApi:
    """`gh api -X <METHOD> <path> [-f k=v …]` — 인증과 호스트는 gh 가 안다."""

    def __init__(self, gh: str = "gh", run: Callable[..., Any] = subprocess.run) -> None:
        self.gh = gh
        self.run = run

    def request(self, method: str, path: str, fields: dict[str, str] | None = None) -> Any:
        cmd = [self.gh, "api", "-X", method, path]
        for key, value in (fields or {}).items():
            cmd += ["-f", f"{key}={value}"]
        try:
            proc = self.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise ApiError(f"`{self.gh}` is not installed") from None
        except subprocess.TimeoutExpired:
            raise ApiError(f"`gh api {method} {path}` timed out") from None
        if proc.returncode != 0:
            text = (proc.stderr or proc.stdout or "").strip()
            m = re.search(r"HTTP (\d{3})", text)
            raise ApiError(text or f"gh exited {proc.returncode}", int(m.group(1)) if m else None)
        out = (proc.stdout or "").strip()
        return json.loads(out) if out else None


class Refused(Exception):
    """관문이 거부한다 — 메시지는 무엇이 모자라고 무엇을 하면 되는지까지 말한다."""


@dataclass(frozen=True)
class Checks:
    """head sha 하나의 체크 상태. 이름은 사람이 읽는 체크 이름(statuses 는 context)."""

    pending: tuple[str, ...]
    failed: tuple[str, ...]
    #: base 룰셋의 필수 context 가운데 head 에 아직 아무 보고도 없는 것.
    missing: tuple[str, ...]
    #: head 에 보고된 체크가 하나라도 있나.
    seen: bool


@dataclass(frozen=True)
class Options:
    number: int
    repo: str
    wait: bool
    timeout: float
    no_verify: str | None
    dry_run: bool
    delete_branch: bool


# ── GitHub 읽기 ──────────────────────────────────────────────────────────────


def _pages(api: Api, path: str, key: str | None = None) -> list[Any]:
    """`per_page=100` 으로 끝까지 읽는다. `key` 가 있으면 응답 객체의 그 목록을 모은다."""
    items: list[Any] = []
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        body = api.request("GET", f"{path}{sep}per_page={PAGE}&page={page}")
        chunk = (body or {}).get(key, []) if key else (body or [])
        items.extend(chunk)
        if len(chunk) < PAGE:
            return items
        page += 1


def required_contexts(api: Api, repo: str, base: str) -> tuple[str, ...]:
    """base 브랜치에 걸린 룰셋들의 필수 상태 체크 context. 이름을 박지 않는다 — 오너가 필수 체크를
    `test`(GitHub Actions) 에서 `rcm/ci`(맥미니 rcm 잡이 올리는 커밋 상태)로 바꿀 수 있다."""
    out: list[str] = []
    for rule in _pages(api, f"repos/{repo}/rules/branches/{base}"):
        if rule.get("type") != "required_status_checks":
            continue
        for check in (rule.get("parameters") or {}).get("required_status_checks") or []:
            context = str(check.get("context") or "")
            if context and context not in out:
                out.append(context)
    return tuple(out)


def read_checks(api: Api, repo: str, sha: str, required: tuple[str, ...] = ()) -> Checks:
    """그 sha 의 check-runs(같은 이름은 GitHub 이 최신 것만 준다)와 commit statuses(목록은 최신이
    앞이라 context 마다 처음 본 것만 센다). 필수 context 는 둘 중 어느 쪽으로 와도 된다."""
    pending: list[str] = []
    failed: list[str] = []
    names: set[str] = set()
    for run in _pages(api, f"repos/{repo}/commits/{sha}/check-runs", "check_runs"):
        name = str(run.get("name", "?"))
        names.add(name)
        if run.get("status") != "completed":
            pending.append(name)
        elif run.get("conclusion") in FAILED_CONCLUSIONS:
            failed.append(f"{name} ({run.get('conclusion')})")
    latest: set[str] = set()
    for status in _pages(api, f"repos/{repo}/commits/{sha}/statuses"):
        name = str(status.get("context", "?"))
        if name in latest:
            continue  # 같은 context 의 옛 상태
        latest.add(name)
        names.add(name)
        if status.get("state") == "pending":
            pending.append(name)
        elif status.get("state") in FAILED_STATES:
            failed.append(f"{name} ({status.get('state')})")
    missing = tuple(context for context in required if context not in names)
    return Checks(tuple(pending), tuple(failed), missing, bool(names))


def _pr(api: Api, opts: Options) -> dict[str, Any]:
    return api.request("GET", f"repos/{opts.repo}/pulls/{opts.number}") or {}


def _open_head(pr: dict[str, Any], number: int) -> str:
    if pr.get("merged"):
        raise Refused(f"#{number} is already merged")
    if pr.get("state") != "open":
        raise Refused(f"#{number} is {pr.get('state')}, not open")
    return str(pr["head"]["sha"])


# ── 관문 ─────────────────────────────────────────────────────────────────────


def _short(sha: str) -> str:
    return sha[:12]


def _wait_for_checks(
    api: Api,
    opts: Options,
    sha: str,
    required: tuple[str, ...],
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """체크가 전부 끝나고 실패가 없고 필수 context 가 다 보고됐을 때 돌아온다. 아니면 Refused."""
    deadline = now() + opts.timeout
    while True:
        checks = read_checks(api, opts.repo, sha, required)
        if checks.failed:
            raise Refused(
                f"checks failed on {_short(sha)}: {', '.join(checks.failed)} — fix them and push; "
                "a rerun that turns green counts"
            )
        waiting = list(checks.pending)
        waiting += [f"`{context}` (not reported yet)" for context in checks.missing]
        if not required and not checks.seen:
            waiting.append("any check (the base has no required checks and none reported yet)")
        if not waiting:
            return
        if not opts.wait:
            if checks.pending:
                raise Refused(
                    f"checks still running on {_short(sha)}: {', '.join(waiting)} — pass --wait "
                    "to wait for them"
                )
            raise Refused(
                f"required checks not reported on {_short(sha)}: {', '.join(waiting)} — CI has "
                "not run on this head (pass --wait if it is starting)"
            )
        left = deadline - now()
        if left <= 0:
            raise Refused(
                f"gave up after {opts.timeout:.0f}s: still waiting on {_short(sha)} for "
                f"{', '.join(waiting)}"
            )
        print(f"waiting on {', '.join(waiting)} …", file=sys.stderr)
        sleep(min(POLL_SECONDS, left))
        moved = _open_head(_pr(api, opts), opts.number)
        if moved != sha:
            raise Refused(
                f"the head moved from {_short(sha)} to {_short(moved)} while waiting — verify the "
                f"new head and post `verified: {moved}`, then merge again"
            )


def _check_verified(api: Api, opts: Options, sha: str) -> None:
    if opts.no_verify is not None:
        return
    comments = _pages(api, f"repos/{opts.repo}/issues/{opts.number}/comments")
    marks = [m for c in comments for m in VERIFIED_RE.findall(str(c.get("body") or ""))]
    if sha in marks:
        return
    if marks:
        raise Refused(
            f"the verified marker is for {', '.join(_short(m) for m in marks)}, but the head is "
            f"{_short(sha)} — the isolated verification has to run on this head and post "
            f"`verified: {sha}`"
        )
    raise Refused(
        f"no `verified: {sha}` comment on #{opts.number} — the isolated verification posts that "
        'line when it passes; a docs-only or tests-only PR may pass --no-verify "<reason>"'
    )


def gate(
    api: Api,
    opts: Options,
    *,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    pr = _pr(api, opts)
    sha = _open_head(pr, opts.number)
    title = str(pr.get("title") or "")
    base = str((pr.get("base") or {}).get("ref") or "")
    required = required_contexts(api, opts.repo, base)
    print(
        f"#{opts.number} {title}\nhead {sha} (pinned) · required on {base}: "
        f"{', '.join(required) or '(none)'}"
    )
    _wait_for_checks(api, opts, sha, required, now, sleep)
    _check_verified(api, opts, sha)
    pr = _pr(api, opts)
    head = _open_head(pr, opts.number)
    if head != sha:
        raise Refused(f"the head moved from {_short(sha)} to {_short(head)} — verify it again")
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        raise Refused(f"#{opts.number} has conflicts with its base — merge the base in and push")
    commit_title = f"{title} (#{opts.number})"
    if opts.dry_run:
        print(f"would merge #{opts.number} at {sha} as {commit_title!r} (dry run)")
        return EXIT_MERGED
    if opts.no_verify is not None:
        api.request(
            "POST",
            f"repos/{opts.repo}/issues/{opts.number}/comments",
            {
                "body": f"no-verify: {opts.no_verify.strip()}\n\nhead {sha} — merged by "
                "`tools/merge_pr.py` without an isolated-verification marker."
            },
        )
    api.request(
        "PUT",
        f"repos/{opts.repo}/pulls/{opts.number}/merge",
        {"sha": sha, "merge_method": "merge", "commit_title": commit_title},
    )
    after = _pr(api, opts)
    if not after.get("merged"):
        print(
            f"GitHub accepted the merge of #{opts.number} but it does not read as merged yet — "
            f"look at it before retrying: gh pr view {opts.number}",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN
    print(f"merged #{opts.number} as {after.get('merge_commit_sha')}")
    if opts.delete_branch:
        _delete_branch(api, opts, after)
    return EXIT_MERGED


def _delete_branch(api: Api, opts: Options, pr: dict[str, Any]) -> None:
    head = pr.get("head") or {}
    if (head.get("repo") or {}).get("full_name") != opts.repo:
        print("the head branch lives in another repository — left as it is", file=sys.stderr)
        return
    ref = str(head.get("ref"))
    if ref in LONG_LIVED:
        print(f"{ref} is a long-lived branch — not deleted", file=sys.stderr)
        return
    try:
        api.request("DELETE", f"repos/{opts.repo}/git/refs/heads/{ref}")
    except ApiError as error:
        print(f"merged, but deleting {ref} failed: {error}", file=sys.stderr)
        return
    print(f"deleted {ref}")


# ── 진입점 ────────────────────────────────────────────────────────────────────


def repo_from_remote(url: str) -> str | None:
    m = REMOTE_RE.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _origin_repo() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return repo_from_remote(proc.stdout) if proc.returncode == 0 else None


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="merge_pr.py",
        description="Merge a pull request only when every check on its pinned head sha is done "
        "and green, the base branch's required checks (read from its rulesets) passed as a check "
        "run or a commit status, and the head carries a `verified: <sha>` comment.",
        epilog="exit codes: 0 merged (or would merge, with --dry-run) · 1 refused · 2 usage · "
        "3 network or unknown. Token: $GH_TOKEN, else gh's active account "
        "(GH_TOKEN=$(gh auth token --user monocsp) in this repository).",
    )
    ap.add_argument("number", help="pull request number")
    ap.add_argument("--repo", help="OWNER/REPO (default: the origin remote)")
    ap.add_argument("--wait", action="store_true", help="wait for pending checks until --timeout")
    ap.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds --wait waits (default {DEFAULT_TIMEOUT:.0f})",
    )
    ap.add_argument(
        "--no-verify",
        metavar="REASON",
        help="merge without a `verified:` comment (docs- or tests-only PR); the reason is posted "
        "on the PR",
    )
    ap.add_argument("--dry-run", action="store_true", help="check everything, change nothing")
    ap.add_argument(
        "--delete-branch", action="store_true", help="delete the head branch after the merge"
    )
    return ap


def parse(argv: list[str]) -> Options:
    """사용법이 틀리면 SystemExit(2)."""
    ap = _parser()
    args = ap.parse_args(argv)
    if not args.number.isdigit() or int(args.number) <= 0:
        ap.error(f"not a pull request number: {args.number!r}")
    if args.timeout < 0:
        ap.error("--timeout must be zero or more seconds")
    if args.no_verify is not None and not args.no_verify.strip():
        ap.error('--no-verify needs a reason: --no-verify "docs only"')
    repo = args.repo or _origin_repo()
    if not repo or not REPO_RE.match(repo):
        ap.error(f"--repo must be OWNER/REPO (got {repo!r})")
    return Options(
        number=int(args.number),
        repo=repo,
        wait=args.wait,
        timeout=args.timeout,
        no_verify=args.no_verify,
        dry_run=args.dry_run,
        delete_branch=args.delete_branch,
    )


def main(
    argv: list[str] | None = None,
    *,
    api: Api | None = None,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    try:
        opts = parse(sys.argv[1:] if argv is None else argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code else EXIT_MERGED  # --help 는 0
    try:
        return gate(api or GhApi(), opts, now=now, sleep=sleep)
    except Refused as why:
        print(f"refused: {why}", file=sys.stderr)
        return EXIT_REFUSED
    except ApiError as error:
        if error.unknown:
            print(f"unknown: GitHub did not answer — {error}", file=sys.stderr)
            return EXIT_UNKNOWN
        print(f"refused: GitHub said {error.status}: {error}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
