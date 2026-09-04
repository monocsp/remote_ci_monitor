"""세션 쪽 클라이언트 — 스냅샷 tar 만들기 · 제출 · 업로드 · 폴링 wait. `urllib.request` 만 쓴다.

- 스냅샷 규칙(파일 선택·tree_hash)은 순수 계층 `core/snapshot.py`. 여기서는 git 을 부르고
  파일을 읽는다.
- tar.gz 는 **임시 파일에 먼저** 만들어 크기를 확인한 뒤 `Content-Length` 와 함께 PUT 한다
  (Codex 리뷰 B4: 스트리밍 tar 는 Content-Length 를 못 준다).
- `wait` 는 2초 폴링. 종료 코드 0/1/2/3. 서버 연결 실패가 60초 넘게 이어지면 3(「모른다」).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remote_ci_monitor import __version__
from remote_ci_monitor.core.model import (
    EXIT_CODE_BY_STATE,
    EXIT_UNKNOWN,
    TERMINAL_STATES,
    InputSpec,
    Preset,
)
from remote_ci_monitor.core.snapshot import (
    normalize_mode,
    parse_ignore,
    parse_ignore_pattern,
    select_files,
    tree_hash,
)

POLL_SECONDS = 2.0
CONNECTION_GRACE_SECONDS = 60.0
RCMIGNORE = ".rcmignore"


class ClientError(Exception):
    """서버가 거부했거나(status) 닿지 않았다(status 0)."""

    def __init__(self, status: int, message: str, body: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body or {}


# ── 스냅샷 ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Snapshot:
    root: Path
    files: tuple[str, ...]
    tree_hash: str
    base_sha: str | None
    dirty: bool | None
    repo: str | None
    tar_path: Path
    bytes: int


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _git_candidates(root: Path) -> list[str] | None:
    """git 체크아웃이면 추적 + 무시되지 않은 미추적 파일 목록. 아니면 None."""
    if _git(root, "rev-parse", "--is-inside-work-tree") is None:
        return None
    raw = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if raw.returncode != 0:
        return None
    return [p.decode("utf-8", errors="surrogateescape") for p in raw.stdout.split(b"\0") if p]


def _walk_candidates(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            out.append(str(rel_dir / name) if str(rel_dir) != "." else name)
    return out


def _digest(path: Path) -> tuple[int, str]:
    st = path.lstat()
    if os.path.islink(path):
        target = os.readlink(path)
        return normalize_mode(st.st_mode, is_symlink=True), hashlib.sha256(
            target.encode()
        ).hexdigest()
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return normalize_mode(st.st_mode, is_symlink=False), h.hexdigest()


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    # 사용자 이름·uid 는 빌드 머신과 무관하고 개인정보다 — 비운다
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def make_snapshot(
    root: Path,
    *,
    excludes: list[str] | None = None,
    tar_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Snapshot:
    """작업 트리를 tar.gz 로. `.rcmignore` + `--exclude` 적용. tar_path 는 호출자가 지운다."""
    root = root.resolve()
    rules = []
    ignore_file = root / RCMIGNORE
    if ignore_file.is_file():
        rules = parse_ignore(ignore_file.read_text(encoding="utf-8", errors="replace"))
    for pat in excludes or []:
        rule = parse_ignore_pattern(pat)
        if rule is not None:
            rules.append(rule)
    candidates = _git_candidates(root)
    is_git = candidates is not None
    if candidates is None:
        candidates = _walk_candidates(root)
    files = select_files(candidates, rules=rules, present=lambda p: os.path.lexists(root / p))
    if progress:
        progress(f"snapshot: {len(files)} files")
    entries: list[tuple[str, int, str]] = []
    for rel in files:
        mode, digest = _digest(root / rel)
        entries.append((rel, mode, digest))
    th = tree_hash(entries)
    base_sha = dirty = repo = None
    if is_git:
        head = _git(root, "rev-parse", "HEAD")
        base_sha = head.strip() if head else None
        status = _git(root, "status", "--porcelain", "--untracked-files=all")
        dirty = bool(status.strip()) if status is not None else None
        remote = _git(root, "remote", "get-url", "origin")
        repo = remote.strip() if remote else None
    fd, tmp_name = tempfile.mkstemp(prefix="rcm-tree-", suffix=".tar.gz", dir=tar_dir)
    os.close(fd)
    tar_path = Path(tmp_name)
    with tarfile.open(tar_path, "w:gz", compresslevel=6) as tf:
        for rel in files:
            tf.add(root / rel, arcname=rel, recursive=False, filter=_tar_filter)
    size = tar_path.stat().st_size
    if progress:
        progress(f"snapshot: {size / 1e6:.1f} MB · tree {th[:12]}")
    return Snapshot(
        root=root,
        files=tuple(files),
        tree_hash=th,
        base_sha=base_sha,
        dirty=dirty,
        repo=repo,
        tar_path=tar_path,
        bytes=size,
    )


# ── HTTP ─────────────────────────────────────────────────────────────────────


class _Reader:
    """파일을 청크로 읽어 주며 진행률 콜백을 부른다(urllib 이 data 로 받는다)."""

    def __init__(self, fh, total: int, progress: Callable[[int, int], None] | None):
        self.fh = fh
        self.total = total
        self.sent = 0
        self.progress = progress

    def read(self, n: int = -1) -> bytes:
        chunk = self.fh.read(n if n and n > 0 else 1 << 16)
        if chunk:
            self.sent += len(chunk)
            if self.progress:
                self.progress(self.sent, self.total)
        return chunk

    def __len__(self) -> int:
        return self.total


class Client:
    def __init__(self, server: str, token: str | None = None, *, timeout: float = 15.0):
        if not server:
            raise ClientError(0, "no server configured (use --server, RCM_SERVER or client.toml)")
        if "://" not in server:
            server = "http://" + server
        self.server = server.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: Any = None,
        content_length: int | None = None,
        timeout: float | None = None,
        content_type: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        url = self.server + path
        headers = {"User-Agent": f"rcm/{__version__}", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body: Any = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        elif data is not None:
            body = data
            headers["Content-Type"] = content_type or "application/octet-stream"
            if content_length is not None:
                headers["Content-Length"] = str(content_length)
        elif method in ("POST", "PUT"):
            body = b""
            headers["Content-Length"] = "0"
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                parsed = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                parsed = {}
            msg = parsed.get("error") if isinstance(parsed, dict) else None
            raise ClientError(
                e.code, msg or f"HTTP {e.code}", parsed if isinstance(parsed, dict) else {}
            ) from e
        except (TimeoutError, urllib.error.URLError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", None) or e
            raise ClientError(0, f"cannot reach {self.server}: {reason}") from e

    def get_json(self, path: str) -> Any:
        _, _, body = self._request("GET", path)
        return json.loads(body) if body else None

    def post_json(self, path: str, obj: Any = None) -> Any:
        _, _, body = self._request("POST", path, json_body=obj if obj is not None else {})
        return json.loads(body) if body else None

    # ── API ──

    def health(self) -> dict[str, Any]:
        return self.get_json("/api/health")

    def whoami(self) -> dict[str, Any]:
        return self.get_json("/api/whoami")

    def status(self) -> dict[str, Any]:
        return self.get_json("/api/status")

    def presets(self) -> dict[str, Preset]:
        doc = self.status()
        return {p["name"]: preset_from_json(p) for p in doc.get("presets", [])}

    def submit(
        self,
        preset: str,
        inputs: dict[str, Any],
        source: dict[str, Any],
        *,
        requester_label: str | None,
        join: bool = True,
    ) -> dict[str, Any]:
        body = {"preset": preset, "inputs": inputs, "source": source, "join": join}
        if requester_label:
            body["requester_label"] = requester_label
        return self.post_json("/jobs", body)

    def upload(
        self,
        job_id: int,
        tar_path: Path,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        size = tar_path.stat().st_size
        with tar_path.open("rb") as fh:
            reader = _Reader(fh, size, progress)
            _, _, body = self._request(
                "PUT",
                f"/jobs/{job_id}/tree",
                data=reader,
                content_length=size,
                timeout=max(self.timeout, 600),
                content_type="application/gzip",
            )
        return json.loads(body)

    def job(self, job_id: int, *, tail: int = 0) -> dict[str, Any]:
        return self.get_json(f"/jobs/{job_id}?tail={tail}")

    def cancel(self, job_id: int) -> dict[str, Any]:
        return self.post_json(f"/jobs/{job_id}/cancel")

    def pause(self) -> dict[str, Any]:
        return self.post_json("/pause")

    def resume(self) -> dict[str, Any]:
        return self.post_json("/resume")

    def log(self, job_id: int, offset: int = 0) -> tuple[bytes, int, bool]:
        _, headers, body = self._request("GET", f"/jobs/{job_id}/log?offset={offset}")
        return body, int(headers.get("X-RCM-Next-Offset", offset)), headers.get("X-RCM-More") == "1"


def preset_from_json(p: dict[str, Any]) -> Preset:
    """`/api/status.presets[]` 항목 → Preset(입력 스키마 검증용. argv 는 서버에만 있다)."""
    inputs = tuple(
        InputSpec(
            name=i["name"],
            type=i.get("type", "string"),
            choices=tuple(i.get("choices") or ()),
            default=i.get("default"),
            pattern=i.get("pattern"),
            description=i.get("description", ""),
        )
        for i in p.get("inputs", [])
    )
    return Preset(
        name=p["name"],
        argv=("<server>",),
        description=p.get("description", ""),
        timeout_seconds=p.get("timeout_seconds") or 1200,
        source_modes=tuple(p.get("source_modes") or ("tree",)),
        concurrency_group=p.get("concurrency_group"),
        expected_seconds=p.get("expected_seconds"),
        inputs=inputs,
    )


# ── wait ─────────────────────────────────────────────────────────────────────


def exit_code_for(job: dict[str, Any] | None) -> int:
    if not job:
        return EXIT_UNKNOWN
    return EXIT_CODE_BY_STATE.get(job.get("state", ""), EXIT_UNKNOWN)


def wait_for_job(
    client: Client,
    job_id: int,
    *,
    timeout: float | None = None,
    poll_seconds: float = POLL_SECONDS,
    on_update: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """잡이 끝날 때까지 폴링. (종료 코드, 마지막 잡 JSON, 사유). 3 은 「모른다」."""
    started = clock()
    last: dict[str, Any] | None = None
    unreachable_since: float | None = None
    while True:
        try:
            job = client.job(job_id)
            unreachable_since = None
        except ClientError as e:
            if e.status == 404:
                return EXIT_UNKNOWN, last, f"job {job_id} not found on the server"
            if e.status not in (0, 502, 503, 504):
                return EXIT_UNKNOWN, last, f"server error: {e.message}"
            now = clock()
            if unreachable_since is None:
                unreachable_since = now
            if now - unreachable_since > CONNECTION_GRACE_SECONDS:
                return EXIT_UNKNOWN, last, f"lost contact with the server: {e.message}"
            sleep(poll_seconds)
            continue
        last = job
        if on_update:
            on_update(job)
        if job.get("state") in TERMINAL_STATES:
            return exit_code_for(job), job, None
        if timeout is not None and clock() - started > timeout:
            return (
                EXIT_UNKNOWN,
                job,
                f"--timeout {timeout:g}s elapsed; job {job_id} is still {job.get('state')}",
            )
        sleep(poll_seconds)


def default_label(token_name: str | None) -> str:
    host = socket.gethostname().split(".")[0] or "host"
    user = os.environ.get("USER") or os.environ.get("USERNAME") or token_name or "session"
    return f"{user}@{host}"


__all__ = [
    "Client",
    "ClientError",
    "Snapshot",
    "make_snapshot",
    "wait_for_job",
    "exit_code_for",
    "preset_from_json",
    "default_label",
]
