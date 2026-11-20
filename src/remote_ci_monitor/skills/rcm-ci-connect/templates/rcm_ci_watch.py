#!/usr/bin/env python3
"""rcm-ci-connect: 빌드 머신의 감시 스크립트 — 열린 PR 과 push 브랜치의 head 를 rcm 에 낸다.

    python3 rcm_ci_watch.py --config ~/.config/rcm/ci-watch.toml [--once] [--dry-run] [--check]

launchd 가 60초마다 한 번 부르는 짧은 프로세스다. 한 바퀴에 하는 일은 이렇다.

1. 저장소 변수 `RCM_CI_MODE` 를 읽는다(5분 캐시, 404 면 덮어쓰기 없음).
2. 같은 레포 브랜치의 열린 PR head 와 `branches` 의 head 를 모은다. 포크 · 지워진 포크 ·
   `dependabot[bot]` 의 PR 은 받지 않는다.
3. 처음 보는 sha 는 그 커밋의 `.rcm/ci.toml` 로 모드를 정하고, rcm 을 기다리는 모드면
   `rcm run <preset> --source git_ref --ref <브랜치> --no-wait` 로 내고 `queued` 를 올린다.
4. 낸 잡을 따라가며 `running` 을 올리고, `worker_down`(120초) · `paused`(300초)가 이어지거나
   GitHub 이 넘겨받으면(`taken-over`) 잡을 취소하고, 훅이 놓친 결과를 120초 뒤 대신 올린다.

멈추지 않게 GitHub 요청은 15초, `rcm` 하위 프로세스는 60초, 한 바퀴는 50초에서 끊는다. 겹쳐 뜨면
뒤에 뜬 것은 잠금을 못 얻고 조용히 끝나고, 10분 넘은 잠금은 빼앗는다. 토큰은 로그에 찍지 않는다.
계약은 remote_ci_monitor 의 docs/m5r-workplan.md §2.6 · §5.4 다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import stat
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rcm_ci_config as cc  # noqa: E402

RCM_TIMEOUT: float = 60  # rcm 하위 프로세스
ROUND_SECONDS: float = 50  # 한 바퀴
LOCK_STALE_SECONDS = 600  # 이보다 오래 잡힌 잠금은 앞 프로세스가 멈춘 것이다
OVERRIDE_CACHE_SECONDS = 300
HOOK_GRACE_SECONDS = 120  # 끝난 잡의 상태가 이만큼 pending 이면 훅이 놓친 것이다
STUCK_REASONS = {"worker_down": 120, "paused": 300}
KEEP_SECONDS = 14 * 86400
TERMINAL = ("succeeded", "failed", "cancelled", "timed_out", "lost")


class GitHubAuth(Exception):
    """GitHub 토큰이 거절됐다(401 · 403). 그 레포의 바퀴를 멈춘다."""


class RoundOver(Exception):
    """한 바퀴의 시한이 지났다."""


@dataclass
class Repo:
    github: str
    token_file: Path
    branches: tuple[str, ...] = ()
    legs: tuple[str, ...] | None = None


@dataclass
class WatchConfig:
    rcm: str
    server: str
    rcm_token_file: Path
    state_dir: Path
    log_file: Path | None
    job_url: bool
    github_api: str
    repos: list[Repo] = field(default_factory=list)


def _path(raw: Any) -> Path:
    return Path(str(raw)).expanduser()


def load_config(path: Path) -> WatchConfig:
    raw = tomllib.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    repos = []
    for r in raw.get("repos") or []:
        legs = r.get("legs")
        repos.append(
            Repo(
                github=str(r["github"]),
                token_file=_path(r["github_token_file"]),
                branches=tuple(r.get("branches") or ()),
                legs=tuple(legs) if legs else None,
            )
        )
    log = raw.get("log_file")
    return WatchConfig(
        rcm=str(_path(raw.get("rcm", "rcm"))) if "/" in str(raw.get("rcm", "")) else "rcm",
        server=str(raw.get("server", "http://127.0.0.1:8787")).rstrip("/"),
        rcm_token_file=_path(raw["rcm_token_file"]),
        state_dir=_path(raw.get("state_dir", "~/.local/state/rcm-ci-watch")),
        log_file=_path(log) if log else None,
        job_url=bool(raw.get("job_url", False)),
        github_api=str(raw.get("github_api", "https://api.github.com")),
        repos=repos,
    )


class Log:
    """한 줄에 한 사건. 파일이 없으면 stderr."""

    def __init__(self, path: Path | None, now_fn: Callable[[], float]):
        self.path = path
        self.now_fn = now_fn

    def __call__(self, repo: str, event: str) -> None:
        stamp = datetime.fromtimestamp(self.now_fn(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{stamp} {repo} {cc.clean(event)}\n"
        if self.path is None:
            sys.stderr.write(line)
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            sys.stderr.write(line)


def too_open(path: Path) -> str | None:
    """토큰 파일이 그룹이나 남에게 열려 있으면 그 권한 문자열(`0644`)."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return "missing"
    return f"{mode:04o}" if mode & 0o077 else None


def _slug(repo: str) -> str:
    return repo.replace("/", "__")


# ── 잠금과 상태 파일 ──────────────────────────────────────────────────────────


def acquire_lock(path: Path, now: float, log: Log, repo: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"pid": os.getpid(), "at": now})
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            held = json.loads(path.read_text()).get("at", 0)
        except (OSError, ValueError, AttributeError):
            held = 0
        if now - float(held or 0) <= LOCK_STALE_SECONDS:
            return False
        log(repo, f"LOCK STALE: lock stale for {int(now - float(held or 0))}s, taking it over")
        tmp = path.with_suffix(f".lock.{os.getpid()}")
        tmp.write_text(body)
        os.replace(tmp, path)
        return True
    with os.fdopen(fd, "w") as f:
        f.write(body)
    return True


def release_lock(path: Path) -> None:
    try:
        if json.loads(path.read_text()).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def load_state(path: Path, now: float, log: Log, repo: str) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("shas"), dict):
            raise ValueError("bad shape")
        return state
    except FileNotFoundError:
        return {"shas": {}}
    except (OSError, ValueError):
        aside = path.with_name(f"{path.name}.corrupt-{int(now)}")
        try:
            os.replace(path, aside)
        except OSError:
            pass
        log(repo, f"state file unreadable, moved aside to {aside.name}")
        return {"shas": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ── rcm 하위 프로세스 ─────────────────────────────────────────────────────────


def submit_reason(rc: int, stderr: str) -> str:
    """`rcm run` 실패의 사유 코드 — 종료 코드와 stderr 첫 줄에서 고른다."""
    low = stderr.lower()
    if "bearer token" in low or "unauthorized" in low or " 401" in low or "invalid token" in low:
        return "rcm_auth"
    if "unknown preset" in low:
        return "unknown_preset"
    if "cannot resolve" in low or "unknown ref" in low or "no such ref" in low:
        return "ref_not_found"
    if "cannot reach" in low or rc == 3:
        return "server_unreachable"
    return f"submit_failed rc={rc}"


@dataclass
class Round:
    cfg: WatchConfig
    repo: Repo
    api: cc.GitHubApi
    state: dict[str, Any]
    log: Log
    now: float
    deadline: float
    dry_run: bool
    rcm_token: str

    # ── 시간 ──

    def left(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise RoundOver
        return left

    def gh(self, method: str, *args: Any, **kw: Any) -> cc.Reply:
        kw["timeout"] = self.left()
        reply = getattr(self.api, method)(*args, **kw)
        if reply.status in (401, 403):
            raise GitHubAuth(reply.error or str(reply.status))
        return reply

    def rcm(self, *argv: str) -> subprocess.CompletedProcess[str] | None:
        env = {**os.environ, "RCM_SERVER": self.cfg.server, "RCM_TOKEN": self.rcm_token}
        try:
            return subprocess.run(
                [self.cfg.rcm, *argv],
                env=env,
                capture_output=True,
                text=True,
                timeout=min(RCM_TIMEOUT, self.left()),
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.log(self.repo.github, f"rcm {argv[0]} timeout after {RCM_TIMEOUT:g}s, skipped")
            return None
        except OSError as e:
            self.log(self.repo.github, f"rcm {argv[0]} could not start: {type(e).__name__}")
            return None

    def job(self, job_id: int) -> dict[str, Any] | None:
        url = f"{self.cfg.server}/jobs/{job_id}"
        try:
            limit = min(cc.HTTP_TIMEOUT, self.left())
            with urllib.request.urlopen(url, timeout=max(limit, 0.05)) as resp:
                doc = json.loads(resp.read())
        except (OSError, ValueError, urllib.error.URLError):
            return None
        return doc if isinstance(doc, dict) else None

    # ── 쓰기 ──

    def post(self, sha: str, state: str, context: str, description: str) -> bool:
        if self.dry_run:
            print(f"would post {state} {context} on {sha[:12]}: {description}")
            return True
        reply = self.gh("post_status", sha, state, context, description, target_url=None)
        word = description.split(" · ")[0]
        if not reply.ok:
            self.log(self.repo.github, f"post {word} on {sha[:12]} failed: {reply.error}")
            return False
        self.log(self.repo.github, f"post {word} on {sha[:12]}: {description}")
        return True

    def cancel(self, job_id: int, why: str) -> None:
        if self.dry_run:
            print(f"would cancel #{job_id} ({why})")
            return
        self.rcm("cancel", str(job_id))
        self.log(self.repo.github, f"cancel #{job_id} {why}")

    # ── 한 바퀴 ──

    def override(self) -> str | None:
        st = self.state
        if self.now - float(st.get("override_at", 0)) < OVERRIDE_CACHE_SECONDS:
            return st.get("override")
        reply = self.gh("variable", "RCM_CI_MODE")
        if reply.status == 404:
            value = None
        elif reply.ok and isinstance(reply.data, dict):
            value = reply.data.get("value") or None
        else:
            self.log(self.repo.github, f"RCM_CI_MODE unreadable ({reply.error}), using the cache")
            return st.get("override")
        st["override"], st["override_at"] = value, self.now
        return value

    def candidates(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        reply = self.gh("request", "GET", "/pulls?state=open&per_page=100",
                        etag=self.state.get("pulls_etag"))  # fmt: skip
        if reply.status == 304:
            pulls = self.state.get("pulls") or []
        elif reply.ok and isinstance(reply.data, list):
            pulls = [
                {
                    "number": p.get("number"),
                    "url": p.get("html_url"),
                    "author": (p.get("user") or {}).get("login"),
                    "sha": (p.get("head") or {}).get("sha"),
                    "branch": (p.get("head") or {}).get("ref"),
                    "head_repo": ((p.get("head") or {}).get("repo") or {}).get("full_name"),
                    "base": (p.get("base") or {}).get("ref"),
                }
                for p in reply.data
                if isinstance(p, dict)
            ]
            self.state["pulls"], self.state["pulls_etag"] = pulls, reply.headers.get("etag")
        else:
            self.log(self.repo.github, f"pull list unreadable: {reply.error}")
            pulls = []
        for p in pulls:
            if cc.is_fork(self.repo.github, p.get("head_repo"), p.get("author")):
                continue  # 남의 코드는 빌드 머신에서 돌리지 않는다(명세 §2.9)
            if p.get("sha") and p.get("branch"):
                out.append({**p, "event": "pull_request"})
        for branch in self.repo.branches:
            reply = self.gh("request", "GET", f"/branches/{branch}")
            sha = ((reply.data or {}).get("commit") or {}).get("sha") if reply.ok else None
            if sha:
                out.append({"event": "push", "sha": sha, "branch": branch, "base": branch})
            else:
                self.log(self.repo.github, f"branch {branch} unreadable: {reply.error}")
        return out

    def handle_new(self, cand: dict[str, Any], override: str | None) -> None:
        sha, shas = cand["sha"], self.state["shas"]
        if sha in shas:
            label = f"{cand['event']}:{cand['branch']}"
            seen = shas[sha].setdefault("seen_as", [])
            if label not in seen:
                seen.append(label)
                self.log(self.repo.github, f"skip known {sha[:12]} ({label})")
            return
        reply = self.gh("contents", cc.CONFIG_PATH, ref=sha)
        cfg, error = None, None
        if reply.status == 404:
            pass
        elif reply.ok and isinstance(reply.data, dict):
            try:
                text = base64.b64decode(reply.data.get("content") or "").decode("utf-8")
                cfg = cc.parse(text, legs=self.repo.legs)
            except (ValueError, UnicodeDecodeError) as e:
                error = str(e) if isinstance(e, cc.CiConfigError) else "undecodable"
        else:
            if reply.status == 0:
                self.log(self.repo.github, f"config of {sha[:12]} timed out, retry next round")
                return
            # 파일 오류가 아니라 API 오류 — GitHub 이 곧바로 대신 돌게 한다
            context = cc.DEFAULT_CONTEXT
            if self.post(sha, "error", context, cc.describe("unavailable", None, "watcher_config")):
                shas[sha] = self.record(cand, word="unavailable", context=context)
            return
        r = cc.resolve(
            cfg,
            event=cand["event"],
            base=cand.get("base") or "",
            fork=False,
            override=override,
            legs=self.repo.legs or (),
            error=error,
        )
        if not r.wait:
            shas[sha] = self.record(cand, word=None, mode=r.mode, why=r.why)
            self.log(self.repo.github, f"skip {sha[:12]} mode={r.mode} why={r.why}")
            return
        context = r.context or cc.DEFAULT_CONTEXT
        side = cfg.rcm if cfg is not None and cfg.rcm is not None else None
        if side is None:
            self.log(self.repo.github, f"skip {sha[:12]}: no [rcm] preset to submit")
            shas[sha] = self.record(cand, word=None, mode=r.mode, why="no_preset")
            return
        listed = self.gh("statuses", sha)
        if not listed.ok:
            self.log(self.repo.github, f"statuses of {sha[:12]} unreadable, retry next round")
            return
        if cc.latest(listed.data, context) is not None:
            shas[sha] = self.record(cand, word="known", context=context)
            self.log(self.repo.github, f"skip {sha[:12]}: {context} already has a status")
            return
        self.submit(cand, side, context)

    def record(self, cand: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return {
            "first_seen": self.now,
            "branch": cand.get("branch"),
            "event": cand.get("event"),
            "job": None,
            **kw,
        }

    def submit(self, cand: dict[str, Any], side: cc.RcmSide, context: str) -> None:
        sha, shas = cand["sha"], self.state["shas"]
        argv = ["run", side.preset, "--source", "git_ref", "--ref", cand["branch"], "--no-wait",
                "--by", f"ci-watch@{socket.gethostname()}"]  # fmt: skip
        if side.priority:
            argv += ["--priority", side.priority]
        if cand["event"] == "pull_request" and cand.get("url"):
            argv += ["--link", cand["url"], "--link-label", f"PR #{cand['number']}"]
        if self.dry_run:
            print(f"would run: rcm {' '.join(argv)}")
            return
        proc = self.rcm(*argv)
        if proc is None:
            return  # 끊겼다 — 다음 바퀴가 다시 낸다(같은 sha 면 서버가 합류시킨다)
        if proc.returncode != 0:
            first = (proc.stderr.strip().splitlines() or [""])[0]
            reason = submit_reason(proc.returncode, first)
            self.log(self.repo.github, f"submit failed rc={proc.returncode} {reason}")
            if self.post(sha, "error", context, cc.describe("unavailable", None, reason)):
                shas[sha] = self.record(cand, word="unavailable", context=context)
            return
        doc = None
        for line in reversed(proc.stdout.strip().splitlines()):
            try:
                doc = json.loads(line)
                break
            except ValueError:
                continue
        if not isinstance(doc, dict) or not isinstance(doc.get("job_id"), int):
            self.log(self.repo.github, "submit printed no job JSON")
            return
        job = doc["job_id"]
        target = doc.get("sha") or sha
        joined = bool(doc.get("joined"))
        self.log(
            self.repo.github,
            f"submit {'joined ' if joined else ''}#{job} {cand['branch']} {target[:12]}",
        )
        entry = self.record(cand, word=None, context=context, job=job, creators=list(side.creators))
        if target != sha:
            # 그사이에 새 커밋이 왔다 — 상태는 서버가 정한 sha 에 올린다
            shas[sha] = self.record(cand, word="moved", context=context, job=job, moved_to=target)
        shas[target] = entry
        parts = []
        if joined:
            parts.append("joined")
        if isinstance(doc.get("position"), int):
            parts.append(f"position {doc['position']}")
        wait_s = (doc.get("estimate") or {}).get("wait_seconds") if doc.get("estimate") else None
        if isinstance(wait_s, int | float) and wait_s > 0:
            parts.append(f"wait ~{max(1, round(wait_s / 60))} min")
        if self.post(target, "pending", context, cc.describe("queued", job, " · ".join(parts))):
            entry["word"], entry["posted_at"] = "queued", self.now

    def follow(self, sha: str, entry: dict[str, Any]) -> None:
        job, context = entry.get("job"), entry.get("context") or cc.DEFAULT_CONTEXT
        if not isinstance(job, int) or entry.get("word") not in (None, "queued", "running"):
            return
        doc = self.job(job)
        if doc is None:
            return
        listed = self.gh("statuses", sha)
        if not listed.ok:
            return
        top = cc.latest(listed.data, context, entry.get("creators") or ())
        word = cc.read_word(top.get("description"))[0] if top else None
        state = doc.get("state")
        if word == "taken-over":
            if state not in TERMINAL:
                self.cancel(job, "taken-over")
            entry["word"] = "taken-over"
            return
        if state in TERMINAL:
            if top is not None and top.get("state") != "pending":
                entry["word"] = word
                return
            ended = cc.created_at({"created_at": doc.get("finished_at")})
            if ended is None:
                ended = entry.setdefault("terminal_seen", self.now)
            if self.now - ended <= HOOK_GRACE_SECONDS:
                return
            step = doc.get("failed_step") or doc.get("last_step")
            mapped = cc.terminal_status(
                state,
                doc.get("exit_code"),
                doc.get("cancelled_by"),
                job=job,
                summary=doc.get("summary") or "",
                step=step,
                successor=doc.get("superseded_by"),
            )
            if mapped and self.post(sha, mapped[0], context, mapped[1]):
                entry["word"] = cc.read_word(mapped[1])[0]
                self.log(self.repo.github, f"hook missed #{job}, posted it")
            return
        if entry.get("word") is None:
            if self.post(sha, "pending", context, cc.describe("queued", job, "")):
                entry["word"] = "queued"
        if state == "running" and entry.get("word") != "running":
            if self.post(sha, "pending", context, cc.describe("running", job, "")):
                entry["word"] = "running"
        reason = doc.get("reason")
        if reason in STUCK_REASONS:
            if entry.get("reason") != reason:
                entry["reason"], entry["reason_since"] = reason, self.now
            elif self.now - float(entry.get("reason_since", self.now)) > STUCK_REASONS[reason]:
                text = cc.describe("unavailable", job, reason)
                if self.post(sha, "error", context, text):
                    entry["word"] = "unavailable"
                    self.cancel(job, reason)
        else:
            entry.pop("reason", None)
            entry.pop("reason_since", None)

    def run(self) -> None:
        override = self.override()
        if override not in (None, *cc.MODES):
            override = None
        for cand in self.candidates():
            self.handle_new(cand, override)
        for sha, entry in list(self.state["shas"].items()):
            self.follow(sha, entry)

    def purge(self) -> None:
        shas = self.state["shas"]
        for sha in [s for s, e in shas.items() if self.now - e.get("first_seen", 0) > KEEP_SECONDS]:
            del shas[sha]


def run_repo(cfg: WatchConfig, repo: Repo, *, now: float, deadline: float, log: Log,
             dry_run: bool, rcm_token: str) -> None:  # fmt: skip
    lock = cfg.state_dir / f"{_slug(repo.github)}.lock"
    if not acquire_lock(lock, now, log, repo.github):
        return  # 다른 프로세스가 돌고 있다 — 조용히 끝난다
    try:
        path = cfg.state_dir / f"{_slug(repo.github)}.json"
        state = load_state(path, now, log, repo.github)
        token = cc.read_token(repo.token_file)
        api = cc.GitHubApi(cfg.github_api, repo.github, token)
        rnd = Round(cfg, repo, api, state, log, now, deadline, dry_run, rcm_token)
        try:
            rnd.run()
        except GitHubAuth as e:
            log(repo.github, f"github_auth: GitHub refused the token ({e}); nothing submitted")
        except RoundOver:
            log(repo.github, f"round deadline {ROUND_SECONDS:g}s reached, the next round goes on")
        rnd.purge()
        if not dry_run:
            save_state(path, state)
    finally:
        release_lock(lock)


# ── --check ──────────────────────────────────────────────────────────────────


def check(cfg: WatchConfig) -> int:
    rows: list[tuple[str, str, str]] = []

    def row(ok: bool | None, name: str, detail: str) -> None:
        rows.append(("OK" if ok else "WARN" if ok is None else "FAIL", name, detail))

    for name, path in [("rcm token", cfg.rcm_token_file)] + [
        (f"github token {r.github}", r.token_file) for r in cfg.repos
    ]:
        bad = too_open(path)
        if bad == "missing":
            row(False, name, f"{path} does not exist")
        elif bad:
            row(False, name, f"{path} is mode {bad}; chmod 600 it")
        else:
            row(True, name, f"{path} is mode 0600")
    exe = cfg.rcm
    found = exe if "/" in exe else None
    if found is None:
        import shutil  # noqa: PLC0415

        found = shutil.which(exe)
    row(bool(found and os.access(found, os.X_OK)), "rcm client", found or f"{exe} not found")
    try:
        with urllib.request.urlopen(f"{cfg.server}/api/health", timeout=cc.HTTP_TIMEOUT) as r:
            health = json.loads(r.read())
        row(True, "rcm server", f"{cfg.server} answers (version {health.get('version', '?')})")
    except (OSError, ValueError, urllib.error.URLError) as e:
        row(False, "rcm server", f"{cfg.server} unreachable: {type(e).__name__}")
    try:
        req = urllib.request.Request(f"{cfg.server}/api/whoami")
        req.add_header("Authorization", f"Bearer {cc.read_token(cfg.rcm_token_file)}")
        with urllib.request.urlopen(req, timeout=cc.HTTP_TIMEOUT) as r:
            me = json.loads(r.read())
        admin = bool(me.get("admin"))
        row(not admin, "rcm token", f"name {me.get('name')}, admin {str(admin).lower()}"
            + (" — use a non-admin client token" if admin else ""))  # fmt: skip
    except (OSError, ValueError, urllib.error.URLError) as e:
        row(False, "rcm token", f"/api/whoami refused or unreachable: {type(e).__name__}")
    for repo in cfg.repos:
        try:
            api = cc.GitHubApi(cfg.github_api, repo.github, cc.read_token(repo.token_file))
        except (OSError, ValueError) as e:
            row(False, f"github {repo.github}", f"token unreadable: {type(e).__name__}")
            continue
        probes = [
            ("metadata", api.request("GET", "")),
            ("pull requests", api.request("GET", "/pulls?state=open&per_page=1")),
            ("variables", api.variable("RCM_CI_MODE")),
            ("contents", api.contents(cc.CONFIG_PATH, ref=None)),
        ]
        bad = [(n, r) for n, r in probes if not (r.ok or r.status == 404 and n != "metadata")]
        if bad:
            row(False, f"github {repo.github}",
                "; ".join(f"{n}: {r.error or r.status}" for n, r in bad))  # fmt: skip
            continue
        row(True, f"github {repo.github}", "metadata, pull requests, variables, contents readable")
        file = probes[3][1]
        if file.status == 404:
            row(None, f"config {repo.github}", f"no {cc.CONFIG_PATH} on the default branch")
            continue
        try:
            text = base64.b64decode(file.data.get("content") or "").decode("utf-8")
            ci = cc.parse(text, legs=repo.legs)
        except (ValueError, UnicodeDecodeError, AttributeError) as e:
            row(False, f"config {repo.github}", f"{cc.CONFIG_PATH}: {e}")
            continue
        missing = [b for b in (ci.rcm.push if ci.rcm else ()) if b not in repo.branches]
        if missing:
            text = f"push lists {', '.join(missing)} but branches does not; add it to watch it"
            row(None, f"config {repo.github}", text)
        else:
            row(True, f"config {repo.github}", f"mode {ci.mode}, fallback {ci.fallback}")
    for status, name, detail in rows:
        print(f"{status:<5} {name:<40} {detail}")
    return 1 if any(s == "FAIL" for s, _, _ in rows) else 0


def main(argv: list[str] | None = None, *, now_fn: Callable[[], float] | None = None) -> int:
    ap = argparse.ArgumentParser(description="rcm-ci-connect watcher: submit PR heads to rcm.")
    ap.add_argument("--config", default="~/.config/rcm/ci-watch.toml")
    ap.add_argument("--once", action="store_true", help="one round (launchd runs it every 60 s)")
    ap.add_argument(
        "--dry-run", action="store_true", help="print what would be done, write nothing"
    )
    ap.add_argument("--check", action="store_true", help="check the setup and print a table")
    args = ap.parse_args(argv)
    now_fn = now_fn or time.time
    try:
        cfg = load_config(Path(args.config))
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"rcm_ci_watch: cannot read {args.config}: {type(e).__name__} {e}", file=sys.stderr)
        return 2
    if args.check:
        return check(cfg)
    log = Log(cfg.log_file, now_fn)
    opened = [(p, too_open(p)) for p in [cfg.rcm_token_file, *(r.token_file for r in cfg.repos)]]
    opened = [(p, m) for p, m in opened if m]
    if opened:
        for p, m in opened:
            log("-", f"token file {p} is {'missing' if m == 'missing' else f'mode {m}'}; "
                "it must be 0600 — nothing done")  # fmt: skip
        return 2
    rcm_token = cc.read_token(cfg.rcm_token_file)
    deadline = time.monotonic() + ROUND_SECONDS
    for repo in cfg.repos:
        if time.monotonic() >= deadline:
            log(repo.github, "round deadline reached before this repository")
            break
        run_repo(cfg, repo, now=now_fn(), deadline=deadline, log=log,
                 dry_run=args.dry_run, rcm_token=rcm_token)  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
