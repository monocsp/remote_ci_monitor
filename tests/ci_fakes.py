"""M5r `rcm-ci-connect` 시험의 가짜들 — 가짜 GitHub · 주입 시계 · 가짜 `rcm` 실행 파일.

명세는 docs/m5r-workplan.md §6 이다.

- `FakeClock`: 시험이 흐르게 하는 시계. `sleep(s)` 는 진짜로 기다리지 않고 시계만 민다. 가짜
  GitHub 의 `Date` 헤더와 상태의 `created_at` 이 이 시계를 따른다. 그래서 대기 잡의 `poll_seconds`
  와 시한이 진짜 시간 없이 흐른다(#233 의 교훈).
- `FakeGitHub`: `127.0.0.1` 에만 묶은 `ThreadingHTTPServer`. 요청 로그를 찍을 때 역방향 DNS 를
  묻지 않는다(#235 와 같은 이유 — macOS 러너에서 첫 조회가 30초 넘게 멈춘다). 상태 목록 · 상태
  쓰기 · PR 목록 · contents · 저장소 변수(없으면 404) · 브랜치 · ETag 와 304 를 흉내 낸다.
- `fake_rcm(...)`: `rcm` 을 대신하는 작은 파이썬 실행 파일. 받은 argv 를 적고 정해 둔 종료 코드와
  출력을 낸다.
"""

from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import socketserver
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "src" / "remote_ci_monitor" / "skills" / "rcm-ci-connect" / "templates"

#: 2026-11-20 00:00:00 UTC — 시험의 기준 시각
T0 = 1_763_596_800.0


def load_templates():
    """템플릿 세 스크립트와 설정 모듈을 import 해 돌려준다(같은 폴더의 `rcm_ci_config` 를 쓴다)."""
    path = str(TEMPLATES)
    if path not in sys.path:
        sys.path.insert(0, path)
    import rcm_ci_config  # noqa: PLC0415
    import rcm_ci_status  # noqa: PLC0415
    import rcm_ci_wait  # noqa: PLC0415
    import rcm_ci_watch  # noqa: PLC0415

    return rcm_ci_config, rcm_ci_wait, rcm_ci_watch, rcm_ci_status


class FakeClock:
    """주입 시계. `now` 는 epoch 초다. `sleep` 은 기다리지 않고 민다."""

    def __init__(self, start: float = T0):
        self.now = start
        self.slept: list[float] = []
        self._lock = threading.Lock()

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.slept.append(seconds)
            self.now += seconds

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class Status:
    state: str
    description: str
    context: str = "rcm/ci"
    creator: str = "monocsp"
    at: float = T0  # 보이기 시작하는 시각 = created_at
    target_url: str | None = None

    def doc(self, sha: str, ident: int) -> dict[str, Any]:
        return {
            "id": ident,
            "url": f"https://api.github.test/repos/statuses/{sha}",
            "state": self.state,
            "description": self.description,
            "context": self.context,
            "target_url": self.target_url,
            "created_at": iso(self.at),
            "updated_at": iso(self.at),
            "creator": {"login": self.creator},
        }


@dataclass
class Seen:
    method: str
    path: str  # 쿼리까지
    headers: dict[str, str]
    body: Any
    status: int = 0


def pr(
    number: int,
    sha: str,
    branch: str,
    *,
    base: str = "dev",
    repo: str = "monocsp/remote_ci_monitor",
    head_repo: str | None = "same",
    author: str = "monocsp",
) -> dict[str, Any]:
    """`GET /pulls` 의 항목 하나(필요한 칸만)."""
    head_full = repo if head_repo == "same" else head_repo
    return {
        "number": number,
        "html_url": f"https://github.test/{repo}/pull/{number}",
        "user": {"login": author},
        "head": {
            "sha": sha,
            "ref": branch,
            "repo": None if head_full is None else {"full_name": head_full},
        },
        "base": {"ref": base, "repo": {"full_name": repo}},
    }


class _NoLookupHTTPServer(ThreadingHTTPServer):
    """`server_bind` 의 `socket.getfqdn()` 역방향 조회를 건너뛴다(#235)."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)


class FakeGitHub:
    """GitHub REST 의 작은 흉내. 시험은 속성을 바꿔 상황을 만든다.

    - `statuses[sha]`: `Status` 목록. 시계가 `at` 에 닿아야 보인다. 응답은 새것부터다.
    - `pulls` · `branches` · `files[(ref, path)]` · `variables[name]`.
    - `users[token] = login`: 쓴 상태의 `creator`. 모르는 토큰은 401 이다.
    - `fail[prefix] = [500, 500]`: 그 경로로 시작하는 요청에 차례로 낸다(다 쓰면 정상).
    - `fail_always[prefix] = 500` · `hang`: 경로 접두어 집합 — `release` 가 설 때까지 답하지 않는다.
    - `auth_fail = True`: 모든 요청에 401.
    """

    def __init__(self, clock: FakeClock, repo: str = "monocsp/remote_ci_monitor"):
        self.clock = clock
        self.repo = repo
        self.statuses: dict[str, list[Status]] = {}
        self.pulls: list[dict[str, Any]] = []
        self.branches: dict[str, str] = {}
        self.files: dict[tuple[str | None, str], str] = {}
        self.variables: dict[str, str] = {}
        self.users: dict[str, str] = {
            "gh-watch-token-value": "monocsp",
            "gh-actions-token-value": "github-actions[bot]",
        }
        self.fail: dict[str, list[int]] = {}
        self.fail_always: dict[str, int] = {}
        self.hang: set[str] = set()
        self.release = threading.Event()
        self.auth_fail = False
        self.requests: list[Seen] = []
        self.not_modified = 0
        self._ids = 0
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def date_time_string(self, timestamp: float | None = None) -> str:  # noqa: ARG002
                # Date 헤더는 주입 시계를 따른다 — 대기 잡이 경과를 이것으로 잰다(W15)
                return email.utils.formatdate(outer.clock.now, usegmt=True)

            def _send(self, status: int, payload: Any, headers: dict[str, str] | None = None):
                data = b"" if status == 304 else json.dumps(payload).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if status != 304:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def _handle(self, method: str) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw else None
                seen = Seen(
                    method, self.path, {k.lower(): v for k, v in self.headers.items()}, body
                )
                with outer._lock:
                    outer.requests.append(seen)
                path = urllib.parse.urlsplit(self.path).path
                # 접두어는 쿼리까지 볼 수 있다(`…/contents/.rcm/ci.toml?ref=<sha>`)
                if any(self.path.startswith(p) or path.startswith(p) for p in outer.hang):
                    outer.release.wait(30)
                    return
                status, payload, headers = outer._answer(method, self.path, seen)
                seen.status = status
                self._send(status, payload, headers)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self.httpd = _NoLookupHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    # ── 시험이 쓰는 것 ──

    def add_status(self, sha: str, status: Status) -> None:
        self.statuses.setdefault(sha, []).append(status)

    def visible(self, sha: str) -> list[Status]:
        return [s for s in self.statuses.get(sha, []) if s.at <= self.clock.now]

    def posted(self, sha: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        """POST 로 받은 상태 — (sha, 본문)."""
        out = []
        for r in self.requests:
            if r.method == "POST" and "/statuses/" in r.path and r.status == 201:
                s = r.path.rsplit("/", 1)[1]
                if sha is None or s == sha:
                    out.append((s, r.body))
        return out

    def calls(self, method: str, contains: str) -> list[Seen]:
        return [r for r in self.requests if r.method == method and contains in r.path]

    def close(self) -> None:
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    # ── 답 ──

    def _answer(self, method: str, raw_path: str, seen: Seen):
        split = urllib.parse.urlsplit(raw_path)
        path, query = split.path, urllib.parse.parse_qs(split.query)
        if self.auth_fail:
            return 401, {"message": "Bad credentials"}, {}
        auth = seen.headers.get("authorization", "")
        token = auth.split(" ", 1)[1] if " " in auth else ""
        login = self.users.get(token)
        if login is None:
            return 401, {"message": "Bad credentials"}, {}
        for prefix, codes in self.fail.items():
            if path.startswith(prefix) and codes:
                return codes.pop(0), {"message": "boom"}, {}
        for prefix, code in self.fail_always.items():
            if path.startswith(prefix):
                return code, {"message": "boom"}, {}
        base = f"/repos/{self.repo}"
        if not path.startswith(base):
            return 404, {"message": "Not Found"}, {}
        rest = path[len(base) :]
        if method == "GET" and rest.startswith("/commits/") and rest.endswith("/statuses"):
            sha = rest.split("/")[2]
            items = list(reversed(self.visible(sha)))
            docs = [s.doc(sha, i) for i, s in enumerate(items)]
            return self._etagged(docs, seen)
        if method == "POST" and rest.startswith("/statuses/"):
            sha = rest.split("/")[2]
            body = seen.body or {}
            self.add_status(
                sha,
                Status(
                    state=body.get("state", ""),
                    description=body.get("description", ""),
                    context=body.get("context", "default"),
                    creator=login,
                    at=self.clock.now,
                    target_url=body.get("target_url"),
                ),
            )
            return 201, {"state": body.get("state")}, {}
        if method == "GET" and rest == "/pulls":
            return self._etagged(self.pulls, seen)
        if method == "GET" and rest.startswith("/branches/"):
            name = urllib.parse.unquote(rest[len("/branches/") :])
            if name not in self.branches:
                return 404, {"message": "Branch not found"}, {}
            return 200, {"name": name, "commit": {"sha": self.branches[name]}}, {}
        if method == "GET" and rest.startswith("/contents/"):
            rel = urllib.parse.unquote(rest[len("/contents/") :])
            ref = (query.get("ref") or [None])[0]
            text = self.files.get((ref, rel))
            if text is None:
                return 404, {"message": "Not Found"}, {}
            enc = base64.b64encode(text.encode()).decode()
            return 200, {"type": "file", "encoding": "base64", "content": enc}, {}
        if method == "GET" and rest.startswith("/actions/variables/"):
            name = rest.rsplit("/", 1)[1]
            if name not in self.variables:
                return 404, {"message": "Not Found"}, {}
            return 200, {"name": name, "value": self.variables[name]}, {}
        if method == "GET" and rest == "":
            return 200, {"full_name": self.repo, "default_branch": "main"}, {}
        return 404, {"message": "Not Found"}, {}

    def _etagged(self, docs: Any, seen: Seen):
        tag = '"' + hashlib.sha1(json.dumps(docs, sort_keys=True).encode()).hexdigest() + '"'
        if seen.headers.get("if-none-match") == tag:
            with self._lock:
                self.not_modified += 1
            return 304, None, {"ETag": tag}
        return 200, docs, {"ETag": tag}


# ── 가짜 rcm 실행 파일 ──────────────────────────────────────────────────────────

_FAKE_RCM = '''#!{python}
"""시험용 가짜 rcm. 같은 이름의 .json 이 할 일을 정한다."""
import json, os, sys, time
from pathlib import Path
here = Path(__file__)
plan = json.loads(here.with_suffix(".json").read_text())
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
with open(here.with_suffix(".calls"), "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "server": os.environ.get("RCM_SERVER"),
                        "has_token": bool(os.environ.get("RCM_TOKEN"))}}) + "\\n")
step = plan.get(cmd) or {{}}
if isinstance(step, list):
    n = sum(1 for line in open(here.with_suffix(".calls")) if json.loads(line)["argv"][:1] == [cmd])
    step = step[min(n, len(step)) - 1]
time.sleep(step.get("sleep", 0))
if step.get("stderr"):
    sys.stderr.write(step["stderr"] + "\\n")
out = step.get("stdout")
if out is not None:
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out) + "\\n")
sys.exit(step.get("rc", 0))
'''


@dataclass
class FakeRcmExe:
    path: Path
    plan: dict[str, Any] = field(default_factory=dict)

    def set(self, **plan: Any) -> None:
        self.plan.update(plan)
        self.path.with_suffix(".json").write_text(json.dumps(self.plan))

    def calls(self) -> list[dict[str, Any]]:
        p = self.path.with_suffix(".calls")
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def argvs(self, cmd: str) -> list[list[str]]:
        return [c["argv"] for c in self.calls() if c["argv"][:1] == [cmd]]


def fake_rcm(folder: Path) -> FakeRcmExe:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "rcm"
    exe.write_text(_FAKE_RCM.format(python=sys.executable))
    exe.chmod(0o755)
    fake = FakeRcmExe(exe)
    fake.set()
    return fake


def submitted(job_id: int, sha: str, *, joined: bool = False, position: int | None = 1) -> dict:
    """`rcm run --no-wait` 의 JSON 한 줄(git_ref)."""
    body: dict[str, Any] = {"job_id": job_id, "joined": joined, "state": "queued"}
    if position is not None:
        body.update(
            {
                "position": position,
                "reason": None,
                "ahead_job_id": None,
                "blocked_by": None,
                "estimate": {"wait_seconds": 240},
            }
        )
    body.update({"url": f"http://build.local:8787/jobs/{job_id}", "ref": "x", "sha": sha})
    return body


# ── GitHub Actions 의 환경 흉내 ────────────────────────────────────────────────

REPO = "monocsp/remote_ci_monitor"
HEAD_SHA = "a" * 40
MERGE_SHA = "m" * 40


def pr_event(base: str = "dev", *, head_repo: str | None = REPO, author: str = "monocsp") -> dict:
    """`pull_request` 이벤트 본문에서 대기 스크립트가 읽는 칸."""
    return {
        "pull_request": {
            "number": 7,
            "user": {"login": author},
            "head": {
                "sha": HEAD_SHA,
                "ref": "feat/x",
                "repo": None if head_repo is None else {"full_name": head_repo},
            },
            "base": {"ref": base, "repo": {"full_name": REPO}},
        }
    }


def actions_env(
    tmp: Path,
    event: str = "pull_request",
    payload: dict | None = None,
    *,
    api: str = "http://127.0.0.1:9",
    ref_name: str = "7/merge",
    attempt: int = 1,
    mode: str | None = None,
    **extra: str,
) -> dict[str, str]:
    """GitHub 이 주는 환경 변수. 출력 파일과 요약 파일은 tmp 아래다."""
    tmp.mkdir(parents=True, exist_ok=True)
    ev = tmp / "event.json"
    ev.write_text(json.dumps(payload if payload is not None else pr_event()))
    out = tmp / "github_output"
    out.write_text("")
    summary = tmp / "step_summary"
    summary.write_text("")
    env = {
        "GITHUB_EVENT_NAME": event,
        "GITHUB_EVENT_PATH": str(ev),
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SHA": MERGE_SHA,
        "GITHUB_REF_NAME": ref_name,
        "GITHUB_RUN_ATTEMPT": str(attempt),
        "GITHUB_RUN_ID": "4242",
        "GITHUB_TOKEN": "gh-actions-token-value",
        "GITHUB_API_URL": api,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    if mode is not None:
        env["RCM_CI_MODE"] = mode
    env.update(extra)
    return env


def outputs(env: dict[str, str]) -> dict[str, str]:
    """`GITHUB_OUTPUT` 파일의 `key=value` 줄."""
    out: dict[str, str] = {}
    for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


#: 명세 §3.1 의 rcm 레포 설정
RCM_REPO_CI_TOML = """\
version = 1
mode = "rcm"
fallback = "github"

[rcm]
preset = "rcm-ci"
context = "rcm/ci"
priority = "low"
push = ["dev"]
creators = ["monocsp"]
pickup_seconds = 300
queue_seconds = 1800
run_seconds = 3600
poll_seconds = 30

[platforms]
ubuntu = "rcm"
macos = "rcm-only"
"""
