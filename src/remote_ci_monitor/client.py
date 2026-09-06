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
from collections.abc import Callable, Iterable, Iterator
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
SSE_IDLE_TIMEOUT_SECONDS = 30.0
SSE_TICK_SECONDS = 5.0
REFETCH_MIN_SECONDS = 1.0
SSE_WAKE_KINDS = frozenset({"hello", "job_changed", "job_finished", "marker", "reset", "lag"})


@dataclass(frozen=True)
class SseEvent:
    kind: str
    id: int | None
    data: dict[str, Any]


class ClientError(Exception):
    """서버가 거부했거나(status) 닿지 않았다(status 0)."""

    def __init__(self, status: int, message: str, body: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body or {}


# ── 스냅샷 ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Entry:
    """스냅샷 파일 하나 — manifest · 전체 tar · 부분 tar 의 **단일 출처**(M5)."""

    path: str
    mode: int
    size: int
    sha256: str
    kind: str  # "file" | "link" (실행 비트는 mode)
    target: str | None = None


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
    entries: tuple[Entry, ...] = ()

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries if e.kind != "link")

    def manifest(self) -> dict[str, Any]:
        """서버 `POST /jobs/{id}/tree/manifest` 본문."""
        return {
            "files": [
                {"path": e.path, "mode": e.mode, "size": e.size, "sha256": e.sha256}
                for e in self.entries
                if e.kind != "link"
            ],
            "links": [
                {"path": e.path, "target": e.target} for e in self.entries if e.kind == "link"
            ],
        }


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


def _link_stays_inside(root: Path, rel: str, progress: Callable[[str], None] | None) -> bool:
    """밖을 가리키는 심링크(절대 경로 · `..` 탈출)는 서버가 거부한다 — 미리 이름을 말하고 뺀다."""
    path = root / rel
    if not os.path.islink(path):
        return True
    target = os.readlink(path)
    if os.path.isabs(target):
        reason = "absolute target"
    else:
        joined = os.path.normpath(os.path.join(os.path.dirname(rel), target))
        reason = "points outside the tree" if joined.startswith("..") else ""
    if not reason:
        return True
    if progress:
        progress(f"snapshot: skipping symlink {rel} -> {target} ({reason})")
    return False


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
    files = [rel for rel in files if _link_stays_inside(root, rel, progress)]
    if progress:
        progress(f"snapshot: {len(files)} files")
        if not is_git and len(files) > 200:
            progress(
                f"snapshot: {root} is not a git checkout — everything under it is included; "
                "add a .rcmignore or use --dir"
            )
    entries: list[tuple[str, int, str]] = []
    full: list[Entry] = []
    for rel in files:
        mode, digest = _digest(root / rel)
        entries.append((rel, mode, digest))
        path = root / rel
        if os.path.islink(path):
            full.append(Entry(rel, mode, 0, digest, "link", os.readlink(path)))
        else:  # 실행 비트는 mode 가 말한다 — kind 는 file | link 둘뿐
            full.append(Entry(rel, mode, os.path.getsize(path), digest, "file"))
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
        entries=tuple(full),
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
    cache_supported: bool | None = None  # 마지막 submit 응답의 cache 플래그(모르면 None)

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
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        url = self.server + path
        headers = {"User-Agent": f"rcm/{__version__}", "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
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
        priority: str | int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"preset": preset, "inputs": inputs, "source": source, "join": join}
        if requester_label:
            body["requester_label"] = requester_label
        if priority is not None:
            body["priority"] = priority
        resp = self.post_json("/jobs", body)
        # 서버가 캐시를 지원하는지 기억한다 — upload_cached 가 manifest 를 헛되이 보내지 않게
        if isinstance(resp, dict) and "cache" in resp:
            self.cache_supported = bool(resp.get("cache"))
        return resp

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

    # ── 내용 주소 캐시 업로드 (M5) ──

    def manifest(self, job_id: int, snapshot: Snapshot) -> dict[str, Any]:
        """manifest 를 보내고 `{missing, missing_bytes, state}` 를 받는다. 구버전 서버는 404."""
        return self.post_json(f"/jobs/{job_id}/tree/manifest", snapshot.manifest())

    def upload_blobs(
        self,
        job_id: int,
        snapshot: Snapshot,
        missing: Iterable[str],
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        """빠진 해시의 파일만 tar.gz(멤버 이름 = sha256, 같은 해시는 한 번)로 PUT."""
        want = set(missing)
        by_hash: dict[str, Entry] = {}
        for e in snapshot.entries:
            if e.kind != "link" and e.sha256 in want and e.sha256 not in by_hash:
                by_hash[e.sha256] = e
        fd, tmp_name = tempfile.mkstemp(prefix="rcm-blobs-", suffix=".tar.gz")
        os.close(fd)
        tar_path = Path(tmp_name)
        try:
            with tarfile.open(tar_path, "w:gz", compresslevel=6) as tf:
                for sha in sorted(by_hash):
                    e = by_hash[sha]
                    info = tf.gettarinfo(str(snapshot.root / e.path), arcname=sha)
                    info = _tar_filter(info)
                    with (snapshot.root / e.path).open("rb") as fh:
                        tf.addfile(info, fh)
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
                    extra_headers={"X-RCM-Tree": "blobs"},
                )
            return json.loads(body)
        finally:
            tar_path.unlink(missing_ok=True)

    def set_priority(self, job_id: int, priority: str | int) -> dict[str, Any]:
        return self.post_json(f"/jobs/{job_id}/priority", {"priority": priority})

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

    def log_follow(
        self,
        job_id: int,
        *,
        offset: int = 0,
        poll_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Iterator[bytes]:
        """로그를 증분으로 흘린다. 잡이 끝나고(`X-RCM-More: 0`) 남은 바이트가 없으면 멈춘다."""
        while True:
            data, offset, more = self.log(job_id, offset)
            if data:
                yield data
            if not more and not data:
                return
            if not data:
                sleep(poll_seconds)

    def eta(
        self, preset: str, inputs: dict[str, Any], *, priority: str | int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"preset": preset, "inputs": inputs}
        if priority is not None:
            body["priority"] = priority
        return self.post_json("/api/eta", body)

    def events(
        self,
        path: str = "/events",
        *,
        last_id: int | None = None,
        idle_timeout: float = SSE_IDLE_TIMEOUT_SECONDS,
    ) -> Iterator[SseEvent]:
        """SSE 스트림. 503(상한 초과)·연결 실패·`idle_timeout` 무응답이면 ClientError → 폴링."""
        url = self.server + path
        headers = {"User-Agent": f"rcm/{__version__}", "Accept": "text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if last_id is not None:
            headers["Last-Event-ID"] = str(last_id)
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=idle_timeout)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                parsed = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                parsed = {}
            msg = parsed.get("error") if isinstance(parsed, dict) else None
            raise ClientError(e.code, msg or f"HTTP {e.code}", parsed or {}) from e
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            raise ClientError(0, f"cannot open event stream: {getattr(e, 'reason', e)}") from e
        with resp:
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" not in ctype:
                raise ClientError(0, f"not an event stream ({ctype or 'no content type'})")
            kind = "message"
            event_id: int | None = None
            data_lines: list[str] = []
            try:
                while True:
                    raw = resp.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line == "":
                        if data_lines:
                            text = "\n".join(data_lines)
                            try:
                                data = json.loads(text)
                            except json.JSONDecodeError:
                                data = {"raw": text}
                            yield SseEvent(kind=kind, id=event_id, data=data)
                        kind, event_id, data_lines = "message", None, []
                        continue
                    if line.startswith(":"):
                        continue  # keep-alive
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "event":
                        kind = value
                    elif field == "id":
                        try:
                            event_id = int(value)
                        except ValueError:
                            event_id = None
                    elif field == "data":
                        data_lines.append(value)
            except (TimeoutError, OSError, http.client.HTTPException) as e:
                raise ClientError(
                    0, f"event stream stalled: {type(e).__name__}", {"stalled": True}
                ) from e

    def job_events(
        self,
        job_id: int,
        *,
        last_id: int | None = None,
        idle_timeout: float = SSE_IDLE_TIMEOUT_SECONDS,
    ) -> Iterator[SseEvent]:
        return self.events(f"/jobs/{job_id}/events", last_id=last_id, idle_timeout=idle_timeout)


def upload_cached(
    client: Client,
    job_id: int,
    snapshot: Snapshot,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """manifest → missing → blob tar. **404(구버전 서버)일 때만** 전체 tar 로 간다.

    400/401/403/413/5xx 는 그대로 올린다(조용한 폴백은 왜 느린지 숨긴다 — 잡은 cancelled 로 남는다).
    반환에는 서버 응답 + `cached_bytes` · `uploaded_files` 를 더한다.
    """
    if getattr(client, "cache_supported", None) is False:  # 서버가 캐시 없다고 했다
        return client.upload(job_id, snapshot.tar_path, progress=progress)
    try:
        resp = client.manifest(job_id, snapshot)
    except ClientError as e:
        if e.status == 404:
            return client.upload(job_id, snapshot.tar_path, progress=progress)
        raise
    missing = list(resp.get("missing") or [])
    sizes: dict[str, int] = {}
    for e in snapshot.entries:
        if e.kind != "link":
            sizes.setdefault(e.sha256, e.size)
    missing_bytes = sum(sizes.get(h, 0) for h in missing)
    total = snapshot.total_bytes
    if not missing:
        resp.setdefault("state", "queued")
        resp.setdefault("job_id", job_id)
        resp["cached_bytes"] = total
        resp["uploaded_files"] = 0
        return resp
    out = client.upload_blobs(job_id, snapshot, missing, progress=progress)
    out["cached_bytes"] = max(0, total - missing_bytes)
    out["uploaded_files"] = len(missing)
    return out


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
        repo=p.get("repo") or "",
        priority=int(p.get("priority") or 0),
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
    use_sse: bool = True,
    on_info: Callable[[str], None] | None = None,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """잡이 끝날 때까지 기다린다. SSE 우선, 안 되면 폴링(명세 0-F). 3 은 「모른다」.

    `on_info` 는 잡 JSON 이 아닌 상황 문구(서버 연결 실패 → 재접속 중)를 받는다.

    반환 (종료 코드, 마지막 잡 JSON, 사유).
    """
    started = clock()
    last: dict[str, Any] | None = None

    def check() -> tuple[int, dict[str, Any] | None, str | None] | None:
        nonlocal last
        job = client.job(job_id)
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
        return None

    sse_ok = use_sse
    while sse_ok:
        try:
            done = check()
            if done is not None:
                return done
            remaining = None if timeout is None else max(0.1, timeout - (clock() - started))
            idle = SSE_TICK_SECONDS if remaining is None else min(SSE_TICK_SECONDS, remaining)
            last_check = clock()
            for ev in client.job_events(job_id, idle_timeout=idle):
                if ev.kind not in SSE_WAKE_KINDS:
                    continue
                if ev.kind != "job_finished" and clock() - last_check < REFETCH_MIN_SECONDS:
                    continue  # 마커가 몰려도 재조회는 초당 한 번(리뷰 F)
                last_check = clock()
                done = check()
                if done is not None:
                    return done
            done = check()  # 스트림이 닫혔다 — 마지막으로 한 번 더 본다
            if done is not None:
                return done
        except ClientError as e:
            if e.status == 404:
                return EXIT_UNKNOWN, last, f"job {job_id} not found on the server"
            if e.status not in (0, 503, 502, 504):
                return EXIT_UNKNOWN, last, f"server error: {e.message}"
            if e.status == 0 and e.body.get("stalled"):
                continue  # 조용한 스트림 — 다시 보고(check) 다시 연다
            sse_ok = False  # 상한 초과(503)·연결 실패 → 폴링으로
    return _poll_for_job(
        client,
        job_id,
        timeout=timeout,
        poll_seconds=poll_seconds,
        on_update=on_update,
        sleep=sleep,
        clock=clock,
        started=started,
        last=last,
        on_info=on_info,
    )


def _poll_for_job(
    client: Client,
    job_id: int,
    *,
    timeout: float | None,
    poll_seconds: float,
    on_update: Callable[[dict[str, Any]], None] | None,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    started: float,
    last: dict[str, Any] | None,
    on_info: Callable[[str], None] | None = None,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """2초 폴링. 서버 연결 실패가 60초 넘게 이어지면 3."""
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
                if on_info:  # 60초 동안 아무 말이 없으면 왜 기다리는지 모른다(사용자 검사 U2.9)
                    on_info(
                        f"server unreachable ({e.message}) — reconnecting for up to "
                        f"{CONNECTION_GRACE_SECONDS:.0f}s"
                    )
            if now - unreachable_since > CONNECTION_GRACE_SECONDS:
                return EXIT_UNKNOWN, last, f"lost contact with the server: {e.message}"
            if timeout is not None and now - started > timeout:
                # 서버가 안 보여도 --timeout 은 지킨다
                return EXIT_UNKNOWN, last, f"--timeout {timeout:g}s elapsed; server unreachable"
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
    "SseEvent",
    "exit_code_for",
    "preset_from_json",
    "default_label",
]
