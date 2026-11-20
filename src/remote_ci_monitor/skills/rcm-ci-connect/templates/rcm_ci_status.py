#!/usr/bin/env python3
"""rcm-ci-connect: rcm 서버의 `[[notify]]` 훅 — 끝난 잡의 결과를 커밋 상태로 올린다.

    [[notify]]
    name = "rcm-ci-status"
    presets = ["rcm-ci"]
    argv = ["/opt/homebrew/bin/python3", "<home>/.config/rcm/scripts/rcm_ci_status.py",
            "--github", "owner/repo", "--context", "rcm/ci",
            "--token-file", "<home>/.config/rcm/tokens/gh-ci.token",
            "--server", "http://127.0.0.1:8787"]

잡의 사실은 서버가 주는 환경 변수(`RCM_STATE` · `RCM_EXIT_CODE` · `RCM_SOURCE_SHA` …)에서 읽는다.
대응표는 `rcm_ci_config.terminal_status()` 하나이고 감시 스크립트도 같은 표를 쓴다. GitHub 이
이미 넘겨받은 커밋(`taken-over`)은 덮지 않는다. 못 올리면 종료 1 — 서버의 `notify_failures` 에
잡히고 감시 스크립트가 120초 뒤 같은 일을 다시 한다. 토큰은 요청 헤더에만 싣는다.
계약은 remote_ci_monitor 의 docs/m5r-workplan.md §2.7 · §5.5 다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rcm_ci_config as cc  # noqa: E402


def say(text: str) -> None:
    print(f"rcm_ci_status: {text}", file=sys.stderr)


def _int(raw: str | None) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def job_doc(server: str, job: int) -> dict[str, Any] | None:
    """`GET /jobs/{id}` — 취소 사유(`cancelled_by` · `superseded_by`)를 본다. 토큰이 필요 없다."""
    url = f"{server.rstrip('/')}/jobs/{job}"
    try:
        with urllib.request.urlopen(url, timeout=cc.HTTP_TIMEOUT) as resp:
            doc = json.loads(resp.read())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return doc if isinstance(doc, dict) else None


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="rcm [[notify]] hook: post the rcm/ci commit status.")
    ap.add_argument("--github", required=True, metavar="OWNER/REPO")
    ap.add_argument("--context", default=cc.DEFAULT_CONTEXT)
    ap.add_argument("--token-file", required=True, help="GitHub token file (mode 0600)")
    ap.add_argument("--server", required=True, help="rcm server URL (for GET /jobs/{id})")
    ap.add_argument("--api-url", default="https://api.github.com")
    ap.add_argument("--job-url", action="store_true", help="put RCM_URL in target_url")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env

    sha = env.get("RCM_SOURCE_SHA", "")
    if env.get("RCM_SOURCE_MODE") != "git_ref" or not sha:
        say("not a git_ref job with a commit sha — nothing to post")
        return 0
    job = _int(env.get("RCM_JOB_ID"))
    state = env.get("RCM_STATE", "")
    if job is None:
        say("RCM_JOB_ID is missing — nothing to post")
        return 0
    try:
        token = cc.read_token(args.token_file)
    except (OSError, ValueError) as e:
        say(f"cannot read the GitHub token file: {type(e).__name__}")
        return 1
    api = cc.GitHubApi(args.api_url, args.github, token)

    listed = api.statuses(sha)
    if not listed.ok:
        say(f"cannot read the statuses of {sha[:12]}: {listed.error or listed.status}")
        return 1
    top = cc.latest(listed.data, args.context)
    if top is not None and cc.read_word(top.get("description"))[0] == "taken-over":
        say(f"skip taken-over: GitHub already took {sha[:12]} over")
        return 0

    cancelled_by = successor = None
    if state == "cancelled":
        doc = job_doc(args.server, job) or {}
        cancelled_by = doc.get("cancelled_by")
        successor = doc.get("superseded_by") if isinstance(doc.get("superseded_by"), int) else None
    step = env.get("RCM_FAILED_STEP") or env.get("RCM_LAST_STEP") or None
    mapped = cc.terminal_status(
        state,
        _int(env.get("RCM_EXIT_CODE")),
        cancelled_by,
        job=job,
        summary=env.get("RCM_SUMMARY", ""),
        step=step,
        successor=successor,
    )
    if mapped is None:
        say(f"job #{job} is {state!r}, not a terminal state — nothing to post")
        return 0
    gh_state, description = mapped
    target = env.get("RCM_URL") if args.job_url else None
    reply = api.post_status(sha, gh_state, args.context, description, target_url=target or None)
    if not reply.ok:
        say(f"could not post {gh_state} on {sha[:12]}: {reply.error or reply.status}")
        return 1
    say(f"posted {gh_state} on {sha[:12]}: {description}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
