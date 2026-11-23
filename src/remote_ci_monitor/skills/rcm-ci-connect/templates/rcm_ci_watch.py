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
   `fallback = "none"` 이면 대신 돌 곳이 없으니 멈춘 잡을 취소하지 않고 기다린다. 잡 스크립트가
   자기 pending 이나 결과를 직접 올리면 그것을 덮지 않는다(명세 §13, 결정 197 · 198).
5. 제출이 `server_unreachable` 이나 시간 초과로 실패하면 세 바퀴까지 다시 내고 그동안 pending
   `queued · retrying submit (n/3)` 을 올린다. 설정 · 인증 실패는 곧바로 error 다.
6. 열린 PR 목록에서 사라진 head(닫힘 · 머지)의 잡이 아직 queued 면 취소한다. 감시가 혼자 낸 잡만
   취소한다. 합류한 잡과 지금 감시하는 브랜치의 head 인 커밋은 둔다(명세 §13.8).

토큰은 파일 대신 기존 자리에서 읽을 수도 있다. rcm 토큰은 `rcm_client_config`(rcm 클라이언트의
client.toml), GitHub 토큰은 `github_token_command`(예: `gh auth token --user <login>`)다. 값은
메모리에만 두고 새 파일에 쓰지 않는다(결정 199).

멈추지 않게 GitHub 요청은 15초, `rcm` 하위 프로세스는 60초, 한 바퀴는 50초에서 끊는다. 겹쳐 뜨면
뒤에 뜬 것은 잠금을 못 얻고 조용히 끝나고, 10분 넘은 잠금은 빼앗는다. 토큰은 로그에 찍지 않는다.
계약은 remote_ci_monitor 의 docs/m5r-workplan.md §2.6 · §5.4 다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import base64
import http.client
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
TOKEN_COMMAND_TIMEOUT: float = 15  # github_token_command
ROUND_SECONDS: float = 50  # 한 바퀴
LOCK_STALE_SECONDS = 600  # 이보다 오래 잡힌 잠금은 앞 프로세스가 멈춘 것이다
OVERRIDE_CACHE_SECONDS = 300
HOOK_GRACE_SECONDS = 120  # 끝난 잡의 상태가 이만큼 pending 이면 훅이 놓친 것이다
STUCK_REASONS = {"worker_down": 120, "paused": 300}
KEEP_SECONDS = 14 * 86400
#: 끝난 잡. `retried`(rcm 0.10.0)는 없다 — 그 시도는 끝났지만 다음 시도(`retried_as`)가 이어
#: 간다. 감시는 그 id 로 옮겨 따라간다(`follow`)
TERMINAL = ("succeeded", "failed", "cancelled", "timed_out", "lost", "expired", "exhausted")
#: 감시가 올리는 pending 낱말. 가장 새 pending 이 이것이 아니면 잡 스크립트가 올린 것이다
OWN_PENDING = ("queued", "running")
#: 다시 내 볼 만한 제출 실패 — 서버 재시작 순간이다. 설정 · 인증 실패는 다시 내도 같다
#: 실행 파일이 없는 것(`rcm_missing`)도 넣는다 — 배포가 venv 를 다시 까는 순간일 수 있다
RETRY_REASONS = ("server_unreachable", "rcm_timeout", "rcm_missing")
SUBMIT_RETRIES = 3
#: 열린 PR 목록의 한 쪽 크기와 읽을 쪽 수. 끝까지 못 읽은 바퀴에는 닫힌 PR 을 가리지 않는다
PULLS_PER_PAGE = 100
PULLS_PAGES = 10
RETRYING = "retrying submit"
#: 운영 서비스의 venv. 배포가 이 venv 가 가리키는 checkout 을 바꾸는 사이 감시의 `rcm` 이 실패할 수
#: 있다 — 감시는 클라이언트 venv 를 부른다(`--check` 가 경고한다)
SERVICE_VENV = "~/.local/share/rcm-venv"
CLIENT_VENV = "~/.local/share/rcm-client-venv"


class GitHubAuth(Exception):
    """GitHub 토큰이 거절됐다(401 · 403). 그 레포의 바퀴를 멈춘다."""


class RateLimited(Exception):
    """GitHub 이 요청 한도를 말했다(429, 또는 남은 한도 0 인 403). 그 레포는 `until` 까지 쉰다."""

    def __init__(self, until: float, detail: str):
        super().__init__(detail)
        self.until = until


class RoundOver(Exception):
    """한 바퀴의 시한이 지났다."""


@dataclass
class Repo:
    github: str
    token_file: Path | None
    branches: tuple[str, ...] = ()
    legs: tuple[str, ...] | None = None
    token_command: tuple[str, ...] | None = None


@dataclass
class WatchConfig:
    rcm: str
    server: str
    rcm_token_file: Path | None
    state_dir: Path
    log_file: Path | None
    job_url: bool
    github_api: str
    repos: list[Repo] = field(default_factory=list)
    rcm_client_config: Path | None = None

    def secret_files(self) -> list[Path]:
        """권한(0600)을 볼 파일 — 토큰 파일과 rcm 클라이언트 설정."""
        files = [self.rcm_token_file or self.rcm_client_config]
        files += [r.token_file for r in self.repos]
        return [f for f in files if f is not None]


def _path(raw: Any) -> Path:
    return Path(str(raw)).expanduser()


def _one_of(table: str, raw: dict[str, Any], a: str, b: str) -> None:
    """토큰은 출처 하나에서만 읽는다 — 둘 다 있거나 둘 다 없으면 설정 오류다."""
    if (a in raw) == (b in raw):
        raise ValueError(f"{table}: set exactly one of {a} or {b}")


def _command(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or not all(isinstance(a, str) and a for a in raw):
        raise ValueError("github_token_command must be a non-empty list of strings")
    head = str(_path(raw[0])) if "/" in raw[0] else raw[0]  # 이름만이면 PATH 에서 찾는다
    return (head, *raw[1:])


def load_config(path: Path) -> WatchConfig:
    raw = tomllib.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    repos = []
    for r in raw.get("repos") or []:
        legs = r.get("legs")
        _one_of(f"[[repos]] {r.get('github')}", r, "github_token_file", "github_token_command")
        command = r.get("github_token_command")
        repos.append(
            Repo(
                github=str(r["github"]),
                token_file=_path(r["github_token_file"]) if "github_token_file" in r else None,
                branches=tuple(r.get("branches") or ()),
                legs=tuple(legs) if legs else None,
                token_command=_command(command) if command is not None else None,
            )
        )
    log = raw.get("log_file")
    _one_of("the top level", raw, "rcm_token_file", "rcm_client_config")
    client = raw.get("rcm_client_config")
    return WatchConfig(
        rcm=str(_path(raw.get("rcm", "rcm"))) if "/" in str(raw.get("rcm", "")) else "rcm",
        server=str(raw.get("server", "http://127.0.0.1:8787")).rstrip("/"),
        rcm_token_file=_path(raw["rcm_token_file"]) if "rcm_token_file" in raw else None,
        rcm_client_config=_path(client) if client is not None else None,
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


def github_token(repo: Repo) -> str:
    """그 레포의 GitHub 토큰. 명령이면 실행할 때마다 읽는다(키체인의 토큰이 바뀌어도 따라간다).

    실패 문장에는 종료 코드만 싣는다 — 명령의 출력에는 토큰이 섞일 수 있다."""
    if repo.token_command is None:
        assert repo.token_file is not None
        return cc.read_token(repo.token_file)
    try:
        proc = subprocess.run(
            list(repo.token_command),
            capture_output=True,
            text=True,
            timeout=TOKEN_COMMAND_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(
            f"github_token_command timed out after {TOKEN_COMMAND_TIMEOUT:g}s"
        ) from None
    except OSError as e:
        raise ValueError(f"github_token_command could not start: {type(e).__name__}") from None
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines or not lines[0].strip():
        raise ValueError(f"github_token_command failed rc={proc.returncode} (no token printed)")
    return lines[0].strip()


def rcm_token(cfg: WatchConfig) -> str:
    """rcm 클라이언트 토큰. `rcm_client_config` 면 rcm 클라이언트와 같은 규칙으로 읽는다:
    파일의 `token`, 없으면 `token_env`(기본 RCM_TOKEN) 환경 변수."""
    if cfg.rcm_client_config is None:
        assert cfg.rcm_token_file is not None
        return cc.read_token(cfg.rcm_token_file)
    raw = tomllib.loads(cfg.rcm_client_config.read_text(encoding="utf-8"))
    token = raw.get("token") or os.environ.get(str(raw.get("token_env") or "RCM_TOKEN"), "")
    if not isinstance(token, str) or not token.strip():
        raise ValueError(f"{cfg.rcm_client_config} has no token")
    return token.strip()


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


def by_label() -> str:
    """감시가 낸 잡의 요청자 이름표(`rcm run --by`). 닫힌 PR 의 잡이 감시 것인지 이것으로 본다."""
    return f"ci-watch@{socket.gethostname()}"


def submit_reason(rc: int, stderr: str) -> str:
    """`rcm run` 실패의 사유 코드 — 종료 코드와 stderr 첫 줄에서 고른다."""
    low = stderr.lower()
    auth = ("token rejected", "bearer token", "unauthorized", " 401", "invalid token")
    if any(word in low for word in auth):
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
    mono: Callable[[], float] = time.monotonic
    #: 이 바퀴에 열린 PR 목록을 읽었나(304 포함). 못 읽은 바퀴에는 닫힌 PR 을 가리지 않는다
    pulls_read: bool = False
    #: 마지막 `rcm` 이 끝나지 못한 까닭 — `rcm_timeout` · `rcm_missing`
    rcm_failure: str = ""

    # ── 시간 ──

    def left(self) -> float:
        left = self.deadline - self.mono()
        if left <= 0:
            raise RoundOver
        return left

    def gh(self, method: str, *args: Any, **kw: Any) -> cc.Reply:
        kw["timeout"] = self.left()
        reply = getattr(self.api, method)(*args, **kw)
        limited = self.rate_limit(reply)
        if limited is not None:
            raise RateLimited(limited, reply.error or str(reply.status))
        if reply.status in (401, 403):
            raise GitHubAuth(reply.error or str(reply.status))
        return reply

    def rate_limit(self, reply: cc.Reply) -> float | None:
        """한도 응답이면 다시 물어도 될 시각(epoch 초). 아니면 None.

        429 는 언제나, 403 은 `X-RateLimit-Remaining: 0` 이나 `Retry-After` 가 있을 때만 한도다.
        그 밖의 403 은 토큰 권한 문제(`github_auth`)다."""
        h = reply.headers
        if reply.status != 429 and not (
            reply.status == 403 and (h.get("x-ratelimit-remaining") == "0" or h.get("retry-after"))
        ):
            return None
        try:
            if h.get("retry-after"):
                return self.now + max(1.0, float(h["retry-after"]))
            if h.get("x-ratelimit-reset"):
                return max(self.now + 1.0, float(h["x-ratelimit-reset"]))
        except ValueError:
            pass
        return self.now + 60.0

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
            self.rcm_failure = "rcm_timeout"
            return None
        except OSError as e:
            self.log(self.repo.github, f"rcm {argv[0]} could not start: {type(e).__name__}")
            self.rcm_failure = "rcm_missing"
            return None

    def job(self, job_id: int) -> dict[str, Any] | None:
        req = urllib.request.Request(f"{self.cfg.server}/jobs/{job_id}")
        if self.rcm_token:
            req.add_header("Authorization", f"Bearer {self.rcm_token}")  # read_auth 를 건 서버
        try:
            limit = min(cc.HTTP_TIMEOUT, self.left())
            with urllib.request.urlopen(req, timeout=max(limit, 0.05)) as resp:
                doc = json.loads(resp.read())
        except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
            return None
        return doc if isinstance(doc, dict) else None

    # ── 쓰기 ──

    def post(self, sha: str, state: str, context: str, description: str) -> bool:
        if self.dry_run:
            print(f"would post {state} {context} on {sha[:12]}: {description}")
            return True
        # job_url = true 면 설명의 잡 번호로 서버 화면의 잡 링크를 싣는다(서버 job_url() 과 같은
        # `/#/jobs/<N>` — `/jobs/<N>` 은 JSON 이다). 기본은 비운다(명세 §2.4)
        job = cc.read_word(description)[1]
        on = self.cfg.job_url and job is not None
        target = f"{self.cfg.server}/#/jobs/{job}" if on else None
        reply = self.gh("post_status", sha, state, context, description, target_url=target)
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
        proc = self.rcm("cancel", str(job_id))
        left = False
        for line in reversed((proc.stdout if proc is not None else "").strip().splitlines()):
            try:
                left = bool(json.loads(line).get("left"))
                break
            except (ValueError, AttributeError):
                continue
        if left:
            # 세션이 낸 잡에 합류했었다 — 취소는 합류만 떠나고 잡은 계속 돈다
            self.log(self.repo.github, f"left join of #{job_id} (job keeps running) {why}")
        else:
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
        query = f"/pulls?state=open&per_page={PULLS_PER_PAGE}"
        reply = self.gh("request", "GET", query, etag=self.state.get("pulls_etag"))
        self.pulls_read = reply.status == 304 or (reply.ok and isinstance(reply.data, list))
        if reply.status == 304:
            pulls = self.state.get("pulls") or []
        elif reply.ok and isinstance(reply.data, list):
            items, page, etag = list(reply.data), 1, reply.headers.get("etag")
            while 'rel="next"' in reply.headers.get("link", ""):
                page += 1
                if page > PULLS_PAGES:
                    reply = cc.Reply(0, error=f"more than {PULLS_PAGES} pages")
                else:
                    reply = self.gh("request", "GET", f"{query}&page={page}")
                if not (reply.ok and isinstance(reply.data, list)):
                    # 끝까지 못 읽었다 — 읽은 것은 내되 닫힌 PR 은 가리지 않는다
                    self.log(self.repo.github, f"pull list page {page} unreadable: {reply.error}")
                    self.pulls_read = False
                    break
                items += reply.data
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
                for p in items
                if isinstance(p, dict)
            ]
            # 첫 쪽의 ETag 는 뒤쪽이 바뀐 것을 모른다 — 한 쪽으로 끝까지 읽었을 때만 둔다
            whole = page == 1 and self.pulls_read
            self.state["pulls"], self.state["pulls_etag"] = pulls, etag if whole else None
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
        word = (shas.get(sha) or {}).get("word")
        retry = shas[sha] if word == "retrying" else None
        # 닫혀서 잡을 취소한 커밋이 다시 열린 PR(이나 브랜치)의 head 로 왔다 — 새로 낸다
        reopened = shas[sha] if word == "gone" else None
        if retry is not None and retry.get("tried_at") == self.now:
            return  # 이 바퀴에 이미 냈다 — dev → main PR 의 head 는 dev 의 head 와 같은 sha 다
        if sha in shas and retry is None and reopened is None:
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
        if self.repo.legs is None and cfg is not None and cfg.platforms:
            self.log(
                self.repo.github,
                f"{sha[:12]}: ci-watch.toml has no legs for this repository, so [platforms] "
                "was not checked the way the workflow's route checks it",
            )
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
        top = cc.latest(listed.data, context)
        mine = retry is not None and top is not None and RETRYING in (top.get("description") or "")
        if reopened is not None and top is not None:
            # 취소한 그 잡의 상태(감시의 queued, 알림 훅이나 감시가 올린 그 잡의 끝)이거나 취소 전에
            # 올라온 것이면 감시가 남긴 것이다. 다른 잡의 것이 취소 뒤에 왔으면 누가 다룬 것이다
            at = cc.created_at(top)
            same_job = cc.read_word(top.get("description"))[1] == reopened.get("job")
            mine = same_job or at is None or at <= float(reopened.get("gone_at", 0))
        if top is not None and not mine:
            shas[sha] = self.record(cand, word="known", context=context)
            self.log(self.repo.github, f"skip {sha[:12]}: {context} already has a status")
            return
        self.submit(cand, side, context, r.fallback, tries=retry.get("tries", 0) if retry else 0)

    def record(self, cand: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return {
            "first_seen": self.now,
            "branch": cand.get("branch"),
            "event": cand.get("event"),
            "job": None,
            **kw,
        }

    def submit(
        self,
        cand: dict[str, Any],
        side: cc.RcmSide,
        context: str,
        fallback: str = "github",
        *,
        tries: int = 0,
    ) -> None:
        sha, shas = cand["sha"], self.state["shas"]
        argv = ["run", side.preset, "--source", "git_ref", "--ref", cand["branch"], "--no-wait",
                "--by", by_label()]  # fmt: skip
        if side.priority:
            argv += ["--priority", side.priority]
        if cand["event"] == "pull_request" and cand.get("url"):
            argv += ["--link", cand["url"], "--link-label", f"PR #{cand['number']}"]
        if self.dry_run:
            print(f"would run: rcm {' '.join(argv)}")
            return
        proc = self.rcm(*argv)
        if proc is None or proc.returncode != 0:
            if proc is None:
                reason = self.rcm_failure or "rcm_timeout"
            else:
                first = (proc.stderr.strip().splitlines() or [""])[0]
                reason = submit_reason(proc.returncode, first)
                self.log(self.repo.github, f"submit failed rc={proc.returncode} {reason}")
            if reason in RETRY_REASONS and tries < SUBMIT_RETRIES:
                # 서버가 재시작하는 순간이다 — error 를 올리면 다음 push 까지 머지가 막힌다
                tries += 1
                text = cc.describe("queued", None, f"{RETRYING} ({tries}/{SUBMIT_RETRIES})")
                self.log(self.repo.github, f"{sha[:12]} {reason}, retry {tries}/{SUBMIT_RETRIES}")
                self.post(sha, "pending", context, text)
                shas[sha] = self.record(
                    cand, word="retrying", context=context, tries=tries, tried_at=self.now
                )
                return
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
        entry = self.record(
            cand, word=None, context=context, job=job, creators=list(side.creators),
            fallback=fallback,
        )  # fmt: skip
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
        nxt = doc.get("retried_as")
        if state == "retried" and isinstance(nxt, int) and not isinstance(nxt, bool):
            # 반복 프리셋의 한 시도가 끝나고 다음 시도가 걸렸다(rcm 0.10.0) — 그 잡을 따라간다.
            # 결과는 사슬의 마지막 시도가 낸다
            entry["job"] = nxt
            return
        if word == "taken-over":
            if state not in TERMINAL:
                self.cancel(job, "taken-over")
            entry["word"] = "taken-over"
            return
        if top is not None and top.get("state") != "pending":
            # 결과가 이미 올라왔다(훅이나 잡 스크립트). 서버의 잡이 아직 running 이어도 그 위에
            # pending 을 올리지 않는다 — 올리면 결과가 가려져 머지가 막힌다(명세 §13, 결정 198)
            entry["word"] = word
            return
        if state in TERMINAL:
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
        # 가장 새 pending 이 감시의 것이 아니면 잡 스크립트가 제 진행을 올리고 있다 — 덮지 않는다
        ours = top is None or word in OWN_PENDING
        if entry.get("word") is None and ours:
            if self.post(sha, "pending", context, cc.describe("queued", job, "")):
                entry["word"] = "queued"
        if state == "running" and entry.get("word") != "running" and ours:
            if self.post(sha, "pending", context, cc.describe("running", job, "")):
                entry["word"] = "running"
        reason = doc.get("reason")
        if reason in STUCK_REASONS and entry.get("fallback") == "none":
            # 대신 돌 곳이 없다 — 취소하고 error 를 올려도 남는 것이 없다. 배포 때의 큐 정지도
            # 이 경우다. 사유가 바뀔 때 한 줄만 남기고 기다린다(결정 197)
            if entry.get("reason") != reason:
                entry["reason"], entry["reason_since"] = reason, self.now
                self.log(self.repo.github, f"#{job} {reason} but no fallback, waiting")
        elif reason in STUCK_REASONS:
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
        rest = float(self.state.get("rest_until") or 0)
        if self.now < rest:
            return  # GitHub 의 한도가 풀릴 때까지 묻지 않는다
        self.state.pop("rest_until", None)
        override = self.override()
        if override not in (None, *cc.MODES):
            override = None
        cands = self.candidates()
        for cand in cands:
            self.handle_new(cand, override)
        if self.pulls_read:
            heads = {c["sha"] for c in cands}
            # 서버가 `--ref` 를 더 새 커밋으로 풀어 받은 제출(moved)은 그 head 의 것이다 — 그 커밋이
            # 아직 PR 목록에 없어도 닫힌 것이 아니다
            shas = self.state["shas"]
            heads |= {
                shas[h]["moved_to"] for h in list(heads) if (shas.get(h) or {}).get("moved_to")
            }
            self.cancel_gone(heads)
        for sha, entry in list(self.state["shas"].items()):
            self.follow(sha, entry)

    def cancel_gone(self, heads: set[str]) -> None:
        """열린 PR 목록에서 사라진 head(닫힘 · 머지 · 새 push)의 잡을 정리한다(명세 §13.8).

        아직 queued 이고 감시가 혼자 낸 잡만 취소한다. 목적은 레인을 비우는 것이고 도는 잡의 결과는
        그 커밋에만 가니 둔다. 감시의 이름표가 아닌 잡이나 합류자가 있는 잡은 남의 검사가 걸려 있다.
        지금 감시하는 브랜치의 head 이거나 push 로 본 적 있는 커밋도 둔다. dev push 의 제출이 같은
        잡에 합류했을 수 있는데, 같은 토큰의 두 번째 제출은 합류자 행을 남기지 않아 잡만 봐서는
        모른다. 한 커밋에 한 번만 판정한다."""
        for sha, entry in self.state["shas"].items():
            if entry.get("event") != "pull_request" or sha in heads or "gone_at" in entry:
                continue
            job = entry.get("job")
            if not isinstance(job, int) or entry.get("word") not in (None, "queued", "running"):
                continue
            if any(str(label).startswith("push:") for label in entry.get("seen_as") or ()):
                continue
            doc = self.job(job)
            if doc is None:
                continue  # 서버가 답하지 않았다 — 다음 바퀴에 다시 본다
            entry["gone_at"] = self.now
            requester = doc.get("requester") if isinstance(doc.get("requester"), dict) else {}
            alone = requester.get("label") == by_label() and not doc.get("joiners")
            if doc.get("state") == "queued" and alone:
                self.cancel(job, "pr_gone: no open pull request has this head")
                entry["word"] = "gone"

    def purge(self) -> None:
        shas = self.state["shas"]
        for sha in [s for s, e in shas.items() if self.now - e.get("first_seen", 0) > KEEP_SECONDS]:
            del shas[sha]


def run_repo(cfg: WatchConfig, repo: Repo, *, now: float, deadline: float, log: Log,
             dry_run: bool, rcm_token: str,
             mono: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
    lock = cfg.state_dir / f"{_slug(repo.github)}.lock"
    if not acquire_lock(lock, now, log, repo.github):
        return  # 다른 프로세스가 돌고 있다 — 조용히 끝난다
    try:
        path = cfg.state_dir / f"{_slug(repo.github)}.json"
        state = load_state(path, now, log, repo.github)
        token = github_token(repo)
        api = cc.GitHubApi(cfg.github_api, repo.github, token)
        rnd = Round(cfg, repo, api, state, log, now, deadline, dry_run, rcm_token, mono)
        try:
            rnd.run()
        except RateLimited as e:
            state["rest_until"] = e.until
            until = datetime.fromtimestamp(e.until, UTC).strftime("%H:%M:%SZ")
            log(repo.github, f"rate_limited: GitHub asked to wait ({e}); resting until {until}")
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


def in_service_venv(exe: str) -> bool:
    """그 실행 파일이(심볼릭 링크를 따라가서) 운영 서비스의 venv 안에 있나."""
    venv = Path(os.path.realpath(_path(SERVICE_VENV)))
    return Path(os.path.realpath(exe)).is_relative_to(venv)


def check(cfg: WatchConfig) -> int:
    rows: list[tuple[str, str, str]] = []

    def row(ok: bool | None, name: str, detail: str) -> None:
        rows.append(("OK" if ok else "WARN" if ok is None else "FAIL", name, detail))

    files: list[tuple[str, Path | None]] = [
        ("rcm token", cfg.rcm_token_file or cfg.rcm_client_config)
    ]
    files += [(f"github token {r.github}", r.token_file) for r in cfg.repos]
    for name, path in files:
        if path is None:
            continue  # 명령으로 읽는 토큰 — 아래 github 행이 실제로 불러 본다
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
    if found and os.access(found, os.X_OK) and in_service_venv(found):
        text = (
            f"{found} is in the service venv {SERVICE_VENV}: a deploy changes the checkout it "
            f"runs from, so a submit can fail meanwhile; use a client venv ({CLIENT_VENV})"
        )
        row(None, "rcm client", text)
    else:
        row(bool(found and os.access(found, os.X_OK)), "rcm client", found or f"{exe} not found")
    try:
        with urllib.request.urlopen(f"{cfg.server}/api/health", timeout=cc.HTTP_TIMEOUT) as r:
            health = json.loads(r.read())
        row(True, "rcm server", f"{cfg.server} answers (version {health.get('version', '?')})")
    except (OSError, ValueError, urllib.error.URLError) as e:
        row(False, "rcm server", f"{cfg.server} unreachable: {type(e).__name__}")
    try:
        req = urllib.request.Request(f"{cfg.server}/api/whoami")
        req.add_header("Authorization", f"Bearer {rcm_token(cfg)}")
        with urllib.request.urlopen(req, timeout=cc.HTTP_TIMEOUT) as r:
            me = json.loads(r.read())
        admin = bool(me.get("admin"))
        # 전용 토큰 파일이 admin 이면 실수다. 클라이언트 설정을 빌려 쓰면 보통 오너 토큰이라
        # 경고만 한다 — 고른 것이다(결정 199)
        ok = (None if cfg.rcm_client_config is not None else False) if admin else True
        row(ok, "rcm token", f"name {me.get('name')}, admin {str(admin).lower()}"
            + (" — use a non-admin client token" if admin else ""))  # fmt: skip
    except (OSError, ValueError, urllib.error.URLError) as e:
        row(False, "rcm token", f"/api/whoami refused or unreachable: {type(e).__name__}")
    for repo in cfg.repos:
        if repo.legs is None:
            row(None, f"legs {repo.github}",
                "not set: [platforms] cannot be checked against the workflow legs, so the "
                "watcher and route may disagree on a broken file")  # fmt: skip
        try:
            api = cc.GitHubApi(cfg.github_api, repo.github, github_token(repo))
        except (OSError, ValueError) as e:
            row(False, f"github {repo.github}", f"token unreadable: {type(e).__name__}: {e}")
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


def main(
    argv: list[str] | None = None,
    *,
    now_fn: Callable[[], float] | None = None,
    mono_fn: Callable[[], float] | None = None,
) -> int:
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
    opened = [(p, too_open(p)) for p in cfg.secret_files()]
    opened = [(p, m) for p, m in opened if m]
    if opened:
        for p, m in opened:
            log("-", f"token file {p} is {'missing' if m == 'missing' else f'mode {m}'}; "
                "it must be 0600 — nothing done")  # fmt: skip
        return 2
    try:
        token = rcm_token(cfg)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        log("-", f"rcm token unreadable: {type(e).__name__}: {e} — nothing done")
        return 2
    mono = mono_fn or time.monotonic
    deadline = mono() + ROUND_SECONDS
    for repo in cfg.repos:
        if mono() >= deadline:
            log(repo.github, "round deadline reached before this repository")
            break
        try:
            run_repo(cfg, repo, now=now_fn(), deadline=deadline, log=log,
                     dry_run=args.dry_run, rcm_token=token, mono=mono)  # fmt: skip
        except Exception as e:  # noqa: BLE001 — 한 레포의 고장이 다른 레포를 막지 않는다
            log(repo.github, f"repository skipped this round: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
