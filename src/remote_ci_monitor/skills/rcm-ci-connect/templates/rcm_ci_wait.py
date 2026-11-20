#!/usr/bin/env python3
"""rcm-ci-connect: GitHub 러너에서 도는 세 단계 — route · wait · verdict.

    python3 rcm_ci_wait.py route   --legs ubuntu,macos [--schedule-ref dev]
    python3 rcm_ci_wait.py wait
    python3 rcm_ci_wait.py verdict

`route` 는 head 커밋의 `.rcm/ci.toml` 로 모드를 정한다. `wait` 는 커밋 상태(기본 `rcm/ci`)를
`poll_seconds` 마다 읽어 pass · fail · fallback 을 낸다. `verdict` 는 앞 잡들의 결과(`NEEDS_JSON`)와
상태를 한 번 더 읽어 필수 체크 `test` 를 판정한다. 입력은 GitHub 이 주는 환경 변수다.
계약은 remote_ci_monitor 의 docs/m5r-workplan.md §2.5 · §5.3 이다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rcm_ci_config as cc  # noqa: E402

REASONS = (
    "no_pickup",
    "queued_too_long",
    "run_too_long",
    "unavailable",
    "superseded",
    "taken_over",
    "rerun",
    "api_error",
)


@dataclass
class Waited:
    result: str  # pass · fail · fallback
    reason: str = ""
    job: int | None = None


# ── 공통 ──────────────────────────────────────────────────────────────────────


def _event(env: Mapping[str, str]) -> dict[str, Any]:
    path = env.get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _head(env: Mapping[str, str]) -> tuple[str, str, bool, str]:
    """(sha, base, fork, event). PR 이면 head sha 와 base 브랜치, push 면 그 브랜치."""
    event = env.get("GITHUB_EVENT_NAME", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    if event.startswith("pull_request"):
        pr = _event(env).get("pull_request") or {}
        head = pr.get("head") or {}
        head_repo = (head.get("repo") or {}).get("full_name") if head.get("repo") else None
        author = (pr.get("user") or {}).get("login")
        base = (pr.get("base") or {}).get("ref", "")
        fork = cc.is_fork(repo, head_repo, author)
        return head.get("sha") or env.get("GITHUB_SHA", ""), base, fork, "pull_request"
    return env.get("GITHUB_SHA", ""), env.get("GITHUB_REF_NAME", ""), False, event


def _write(env: Mapping[str, str], name: str, pairs: Mapping[str, Any]) -> None:
    path = env.get(name)
    lines = "".join(f"{k}={'' if v is None else v}\n" for k, v in pairs.items())
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(lines)
    else:
        sys.stdout.write(lines)


def _summary(env: Mapping[str, str], text: str) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")


def _api(env: Mapping[str, str]) -> cc.GitHubApi:
    return cc.GitHubApi(
        env.get("GITHUB_API_URL") or "https://api.github.com",
        env.get("GITHUB_REPOSITORY", ""),
        env.get("GITHUB_TOKEN", ""),
    )


def _config(path: str) -> tuple[cc.CiConfig | None, str | None]:
    try:
        return cc.load(Path(path), legs=None), None
    except cc.CiConfigError as e:
        return None, str(e)


def _side(cfg: cc.CiConfig | None) -> cc.RcmSide:
    if cfg is not None and cfg.rcm is not None:
        return cfg.rcm
    return cc.RcmSide(preset="-")


# ── route ────────────────────────────────────────────────────────────────────


def cmd_route(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    legs = tuple(x for x in args.legs.split(",") if x)
    sha, base, fork, event = _head(env)
    override = env.get("RCM_CI_MODE", "") or None
    if override is not None and override not in cc.MODES:
        print(
            f"::warning::RCM_CI_MODE={cc.clean(override)!r} is not one of "
            f"{', '.join(cc.MODES)}; ignored"
        )
        override = None
    cfg, error = None, None
    try:
        cfg = cc.load(Path(args.config), legs=legs)
    except cc.CiConfigError as e:
        error = str(e)
    r = cc.resolve(
        cfg, event=event, base=base, fork=fork, override=override, legs=legs, error=error
    )
    if r.why == "bad_file":
        print(f"::warning::{args.config} is not valid, running every leg on GitHub: {r.error}")
    ref = args.schedule_ref if event == "schedule" and args.schedule_ref else sha
    _write(
        env,
        "GITHUB_OUTPUT",
        {
            "mode": r.mode,
            "wait": "true" if r.wait else "false",
            "now": ",".join(r.now),
            "late": ",".join(r.late),
            "context": r.context or "",
            "sha": "" if event == "schedule" else sha,
            "ref": ref,
            "why": r.why,
        },
    )
    print(f"route: mode={r.mode} why={r.why} now={','.join(r.now)} late={','.join(r.late)}")
    return 0


# ── wait ─────────────────────────────────────────────────────────────────────


def wait_for(
    api: cc.GitHubApi,
    sha: str,
    side: cc.RcmSide,
    *,
    attempt: int,
    run_id: str,
    sleep: Callable[[float], None],
) -> Waited:
    """커밋 상태를 기다린다. 경과는 응답의 `Date` 와 상태의 `created_at` 으로 잰다."""
    if attempt > 1:
        return Waited("fallback", "rerun")  # 사람의 탈출구 — API 를 부르지 않는다
    etag: str | None = None
    cached: list[dict[str, Any]] = []
    virt: float | None = None  # GitHub 의 시계로 본 지금
    start: float | None = None
    err_since: float | None = None
    slept = 0.0
    while True:
        reply = api.statuses(sha, etag=etag)
        if reply.date is not None:
            virt = reply.date
        elif virt is not None:
            virt += slept
        else:
            virt = 0.0
        slept = 0.0
        if start is None:
            start = virt
        if reply.status == 304:
            statuses = cached
            err_since = None
        elif reply.ok and isinstance(reply.data, list):
            statuses = cached = reply.data
            etag = reply.headers.get("etag") or None
            err_since = None
        else:
            if err_since is None:
                err_since = virt
            if virt - err_since >= side.pickup_seconds:
                return Waited("fallback", "api_error")
            sleep(side.poll_seconds)
            slept = side.poll_seconds
            continue
        st = cc.latest(statuses, side.context, side.creators)
        if st is None:
            if virt - start >= side.pickup_seconds:
                return Waited("fallback", "no_pickup")
        else:
            word, job = cc.read_word(st.get("description"))
            state = st.get("state")
            if state == "success":
                return Waited("pass", "", job)
            if state == "failure":
                return Waited("fail", "failed", job)
            if state == "error":
                reason = (word or "").replace("-", "_")
                return Waited("fallback", reason if reason in REASONS else "unavailable", job)
            running = word == "running"
            limit = side.run_seconds if running else side.queue_seconds
            since = cc.created_at(st)
            if since is not None and virt - since >= limit:
                api.post_status(
                    sha,
                    "error",
                    side.context,
                    cc.describe("taken-over", None, f"GitHub Actions run {run_id}"),
                )
                return Waited("fallback", "run_too_long" if running else "queued_too_long", job)
        sleep(side.poll_seconds)
        slept = side.poll_seconds


def cmd_wait(args: argparse.Namespace, env: Mapping[str, str], sleep) -> int:
    cfg, _error = _config(args.config)
    side = _side(cfg)
    fallback = cfg.fallback if cfg is not None else "github"
    sha, *_ = _head(env)
    attempt = int(env.get("GITHUB_RUN_ATTEMPT") or "1")
    w = wait_for(
        _api(env), sha, side, attempt=attempt, run_id=env.get("GITHUB_RUN_ID", "?"), sleep=sleep
    )
    if w.result == "fallback" and fallback == "none":
        w = Waited("fail", w.reason, w.job)
    _write(
        env,
        "GITHUB_OUTPUT",
        {"result": w.result, "reason": w.reason, "job": "" if w.job is None else w.job},
    )
    job = f"#{w.job}" if w.job is not None else "—"
    _summary(env, f"rcm wait: `{w.result}` · reason `{w.reason or '—'}` · rcm job {job}")
    print(f"wait: result={w.result} reason={w.reason} job={job}")
    return 1 if w.result == "fail" else 0


# ── verdict ──────────────────────────────────────────────────────────────────


def _legs(text: Any) -> set[str]:
    return {x for x in str(text or "").split(",") if x}


def judge(needs: Mapping[str, Any], latest: dict[str, Any] | None, attempt: int):
    """(통과?, 사유, 빠진 갈래) — 명세 §2.5 의 판정 표. 표에 없는 조합은 실패다."""

    def job(name: str) -> dict[str, Any]:
        j = needs.get(name)
        return j if isinstance(j, dict) else {}

    def outs(name: str) -> dict[str, Any]:
        o = job(name).get("outputs")
        return o if isinstance(o, dict) else {}

    route = job("route")
    if route.get("result") != "success":
        return False, "route did not succeed", set()
    ro = outs("route")
    mode = ro.get("mode", "")
    now, late = _legs(ro.get("now")), _legs(ro.get("late"))
    missing: set[str] = set()

    def ran_ok(name: str, expected: set[str]) -> bool:
        ran = _legs(outs(name).get("ran"))
        if job(name).get("result") == "success" and expected and ran == expected:
            return True
        missing.update(expected - ran if job(name).get("result") == "success" else expected)
        return False

    def now_ok() -> bool:
        return not now or ran_ok("now", now)

    if mode == "github":
        ok = bool(now) and ran_ok("now", now)
        return ok, "github", missing
    rcm = outs("rcm")
    result = rcm.get("result", "")
    jobno = rcm.get("job") or "?"
    if attempt == 1 and latest is not None and latest.get("state") == "failure":
        _, n = cc.read_word(latest.get("description"))
        return False, f"rcm #{n if n is not None else jobno} failed", missing
    if result == "pass":
        return now_ok(), "rcm passed", missing
    if result == "fail":
        return False, f"rcm #{jobno} failed", missing
    if mode == "both":
        if result == "fallback":
            return now_ok(), f"fallback: {rcm.get('reason', '')}", missing
        return False, "rcm wait gave no result", missing
    if result == "fallback":
        ok = bool(late) and ran_ok("late", late) and now_ok()
        return ok, f"fallback: {rcm.get('reason', '')}", missing
    if result == "" and job("rcm").get("result") not in ("success", "skipped"):
        ok = bool(late) and ran_ok("late", late) and now_ok()
        return ok, "waiter crashed", missing
    return False, f"no verdict for rcm result {result!r}", missing


def cmd_verdict(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    try:
        needs = json.loads(env.get("NEEDS_JSON") or "{}")
    except ValueError:
        needs = {}
    attempt = int(env.get("GITHUB_RUN_ATTEMPT") or "1")
    ro = ((needs.get("route") or {}).get("outputs")) or {}
    latest = None
    if ro.get("wait") == "true" and ro.get("sha"):
        cfg, _ = _config(args.config)
        side = _side(cfg)
        context = ro.get("context") or side.context
        reply = _api(env).statuses(ro["sha"])
        if reply.ok:
            # 대기 잡의 taken-over 와 훅의 failed 가 겹친 경우를 막는다 — taken-over 는 건너뛴다
            latest = cc.latest(reply.data, context, side.creators, skip_taken_over=True)
    ok, reason, missing = judge(needs, latest, attempt)
    rows = ["| mode | rcm | now | late | verdict | reason |", "|---|---|---|---|---|---|"]

    def cell(name: str) -> str:
        j = needs.get(name) or {}
        o = j.get("outputs") or {}
        extra = o.get("ran") if name in ("now", "late") else o.get("result")
        return f"{j.get('result', '—')} {extra or ''}".strip()

    rows.append(
        f"| {ro.get('mode', '—')} | {cell('rcm')} | {cell('now')} | {cell('late')} | "
        f"{'pass' if ok else 'FAIL'} | {reason} |"
    )
    if missing:
        rows.append("")
        rows.append(f"Legs that did not run: {', '.join(sorted(missing))}")
    if attempt > 1:
        rows.append("")
        rows.append("Re-run: judged by GitHub, not by rcm.")
    _summary(env, "\n".join(rows))
    print(f"verdict: {'pass' if ok else 'FAIL'} — {reason}")
    return 0 if ok else 1


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    ap = argparse.ArgumentParser(description="rcm-ci-connect: route, wait for rcm, verdict.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("route", help="decide where the checks run")
    r.add_argument("--legs", required=True, help="comma-separated leg names of the workflow")
    r.add_argument("--schedule-ref", default="", help="ref to check out for the schedule event")
    for p in (r, sub.add_parser("wait", help="wait for the rcm commit status"),
              sub.add_parser("verdict", help="judge the required check")):  # fmt: skip
        p.add_argument("--config", default=cc.CONFIG_PATH, help="default: .rcm/ci.toml")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env
    if args.cmd == "route":
        return cmd_route(args, env)
    if args.cmd == "wait":
        return cmd_wait(args, env, sleep or time.sleep)
    return cmd_verdict(args, env)


if __name__ == "__main__":
    sys.exit(main())
