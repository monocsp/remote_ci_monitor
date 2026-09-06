"""내용 주소 스냅샷의 순수 규칙 — manifest 검증 · 빠진 blob 계산 · 자재화 계획.

I/O 가 없다. 경로 규칙은 tar 데이터 필터와 같은 강도로 잠근다(절대 경로 · `..` · 빈 조각 ·
백슬래시 · NUL · `.git/` 거부). 링크 target 은 워크스페이스 안 상대 경로만. 오류 문구엔 서버
경로가 없다(클라이언트 400 본문과 잡 summary 에 실린다).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MAX_MANIFEST_FILES = 200_000
MAX_PATH_LEN = 4096
MODE_FILE = 0o644
MODE_EXEC = 0o755
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """manifest 가 잘못됐다. 메시지는 짧고 경로는 클라이언트가 보낸 것만 담는다."""


@dataclass(frozen=True)
class ManifestFile:
    path: str
    mode: int
    size: int
    sha256: str


@dataclass(frozen=True)
class ManifestLink:
    path: str
    target: str


@dataclass(frozen=True)
class Manifest:
    files: tuple[ManifestFile, ...]
    links: tuple[ManifestLink, ...]
    total_bytes: int

    @property
    def unique_hashes(self) -> set[str]:
        return {f.sha256 for f in self.files}


@dataclass(frozen=True)
class Op:
    """자재화 한 단계. kind ∈ mkdir · copy · symlink."""

    kind: str
    path: str
    sha256: str | None = None
    mode: int | None = None
    target: str | None = None


def normalize_mode(mode: int) -> int:
    """실행 비트만 남긴다 — 0o755 아니면 0o644. setuid 같은 건 절대 안 옮긴다."""
    return MODE_EXEC if mode & 0o111 else MODE_FILE


def _check_path(path: Any, what: str) -> str:
    if not isinstance(path, str) or not path:
        raise ManifestError(f"{what}: path must be a non-empty string")
    if len(path) > MAX_PATH_LEN:
        raise ManifestError(f"{what}: path longer than {MAX_PATH_LEN}")
    if "\x00" in path or "\\" in path:
        raise ManifestError(f"{what}: path contains NUL or backslash: {path[:80]!r}")
    if path.startswith("/"):
        raise ManifestError(f"{what}: absolute path: {path[:80]!r}")
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ManifestError(f"{what}: path has empty, '.' or '..' components: {path[:80]!r}")
    if ".git" in parts:
        raise ManifestError(f"{what}: .git/ is never uploaded: {path[:80]!r}")
    return path


def _check_link_target(path: str, target: Any) -> str:
    if not isinstance(target, str) or not target:
        raise ManifestError(f"link {path!r}: target must be a non-empty string")
    if "\x00" in target or "\\" in target:
        raise ManifestError(f"link {path!r}: target contains NUL or backslash")
    if target.startswith("/"):
        raise ManifestError(f"link {path!r}: absolute target")
    # 링크 위치에서 target 을 따라간 결과가 워크스페이스 안이어야 한다
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise ManifestError(f"link {path!r}: target escapes the workspace")
    return target


def validate_manifest(doc: Any, *, max_bytes: int) -> Manifest:
    """클라이언트가 보낸 manifest JSON 을 검증한다. 틀리면 `ManifestError`."""
    if not isinstance(doc, dict):
        raise ManifestError("manifest must be an object")
    raw_files = doc.get("files", [])
    raw_links = doc.get("links", [])
    if not isinstance(raw_files, list) or not isinstance(raw_links, list):
        raise ManifestError("manifest files and links must be arrays")
    if len(raw_files) + len(raw_links) > MAX_MANIFEST_FILES:
        raise ManifestError(f"manifest has more than {MAX_MANIFEST_FILES} entries")
    seen: set[str] = set()
    files: list[ManifestFile] = []
    total = 0
    for i, raw in enumerate(raw_files):
        if not isinstance(raw, dict):
            raise ManifestError(f"file #{i}: must be an object")
        path = _check_path(raw.get("path"), f"file #{i}")
        if path in seen:
            raise ManifestError(f"duplicate path: {path[:80]!r}")
        seen.add(path)
        size = raw.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ManifestError(f"file {path[:80]!r}: size must be a non-negative integer")
        sha = raw.get("sha256")
        # fullmatch: match 는 끝의 개행을 통과시킨다
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise ManifestError(f"file {path[:80]!r}: sha256 must be 64 lowercase hex characters")
        mode = raw.get("mode")
        if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0:
            raise ManifestError(f"file {path[:80]!r}: mode must be an integer")
        total += size
        if total > max_bytes:
            raise ManifestError(f"snapshot exceeds {max_bytes} bytes")
        files.append(ManifestFile(path=path, mode=normalize_mode(mode), size=size, sha256=sha))
    links: list[ManifestLink] = []
    for i, raw in enumerate(raw_links):
        if not isinstance(raw, dict):
            raise ManifestError(f"link #{i}: must be an object")
        path = _check_path(raw.get("path"), f"link #{i}")
        if path in seen:
            raise ManifestError(f"duplicate path: {path[:80]!r}")
        seen.add(path)
        links.append(ManifestLink(path=path, target=_check_link_target(path, raw.get("target"))))
    _check_tree_shape(seen)
    return Manifest(files=tuple(files), links=tuple(links), total_bytes=total)


def _check_tree_shape(paths: set[str]) -> None:
    """어떤 경로가 파일이면서 다른 경로의 디렉터리일 수는 없다."""
    dirs: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for n in range(1, len(parts)):
            dirs.add("/".join(parts[:n]))
    clash = sorted(dirs & paths)
    if clash:
        raise ManifestError(f"path is both a file and a directory: {clash[0][:80]!r}")


def missing_hashes(manifest: Manifest, have: Iterable[str]) -> list[str]:
    """서버에 없는 blob 해시. 정렬·중복 제거."""
    have_set = set(have)
    return sorted(manifest.unique_hashes - have_set)


def assemble_plan(manifest: Manifest) -> list[Op]:
    """자재화 순서: 부모 디렉터리(얕은 것부터, 중복 없이) → 파일 복사 → 링크."""
    paths = [f.path for f in manifest.files] + [link.path for link in manifest.links]
    _check_tree_shape(set(paths))
    dirs: list[str] = []
    seen: set[str] = set()
    for p in paths:
        parts = p.split("/")
        for n in range(1, len(parts)):
            d = "/".join(parts[:n])
            if d not in seen:
                seen.add(d)
                dirs.append(d)
    dirs.sort(key=lambda d: (d.count("/"), d))
    ops: list[Op] = [Op(kind="mkdir", path=d) for d in dirs]
    ops.extend(Op(kind="copy", path=f.path, sha256=f.sha256, mode=f.mode) for f in manifest.files)
    ops.extend(Op(kind="symlink", path=link.path, target=link.target) for link in manifest.links)
    return ops
