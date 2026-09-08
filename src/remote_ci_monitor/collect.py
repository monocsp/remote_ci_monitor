"""산출물 수집기(M5e) — 워크스페이스를 지우기 전에 글롭에 맞는 파일을 모아 불변 tar 로 만든다.

로컬 워커와 원격 워커가 **같은 코드**를 쓴다. 순수 규칙은 `core/artifacts.py` 에 있고 여기는 I/O 만
맡는다.

경계는 **워크스페이스**다. 유출 방어 장치가 아니다 — 잡은 워커 사용자 권한으로 돌기 때문에 비밀을
`goldens/ok.png` 안에 복사해 넣는 것은 글롭으로 막을 수 없다. 여기서 막는 것은 **수집기가** 링크
너머를 읽거나 워크스페이스 밖으로 나가는 것이다(명세 §2 · §4).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.artifacts import (
    DROPPED,
    EMPTY,
    READY,
    SKIPPED,
    ArtifactError,
    ArtifactPolicy,
    BundleFile,
    check_path,
    collisions,
    compile_globs,
)

CHUNK = 64 * 1024
BUNDLE_NAME = "bundle.tar"


@dataclass(frozen=True)
class CollectResult:
    """수집 한 번의 결과.

    `reason_args` 는 공개되므로 **수치만** 담는다. 경로는 `detail` 에만 담는다(명세 §3).
    """

    state: str
    files: tuple[BundleFile, ...] = ()
    total_bytes: int = 0
    bundle_bytes: int = 0
    skipped_count: int = 0
    bundle_sha256: str | None = None
    bundle_path: Path | None = None
    reason_code: str | None = None
    reason_args: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def open_anchored(root: Path, relpath: str) -> int:
    """워크스페이스 안의 일반 파일 하나를 **링크를 따르지 않고** 연다. 파일 서술자를 돌려준다.

    `O_NOFOLLOW` 는 마지막 조각만 지킨다 — 검사한 부모 디렉터리가 그사이 심링크로 바뀌면 못 막는다.
    그래서 뿌리 fd 에서 시작해 조각마다 `dir_fd` 로 **닻을 내리며** 내려간다.

    마지막은 `O_NONBLOCK` 으로 연다. 없으면 FIFO 를 열 때 쓰는 쪽이 열릴 때까지 워커가 선다.
    """
    check_path(relpath)  # `..` 은 O_NOFOLLOW 로도 그냥 열린다 — 경로 규칙이 먼저 막는다
    parts = relpath.split("/")
    flags = getattr(os, "O_CLOEXEC", 0)
    fds: list[int] = []
    try:
        fds.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | flags))
        for part in parts[:-1]:
            fds.append(
                os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | flags,
                    dir_fd=fds[-1],
                )
            )
        fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | flags,
            dir_fd=fds[-1],
        )
    except FileNotFoundError:
        raise
    except OSError as e:  # ELOOP(심링크) · ENOTDIR · EACCES … — 경로는 싣지 않는다
        raise ArtifactError(f"cannot open artifact ({os.strerror(e.errno or 0)})") from e
    finally:
        for fd_open in fds:
            with contextlib.suppress(OSError):
                os.close(fd_open)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ArtifactError("artifact is not a regular file")
    return fd


def _write_bundle(
    workspace: Path, rel_paths: list[str], dest: Path
) -> tuple[list[BundleFile], int]:
    """고른 파일을 tar 로 쓴다. 같은 입력이면 **같은 바이트**가 나온다(시각·소유자를 고정한다)."""
    files: list[BundleFile] = []
    with tarfile.open(dest, "w", format=tarfile.GNU_FORMAT) as tar:
        for rel in rel_paths:
            fd = open_anchored(workspace, rel)
            with os.fdopen(fd, "rb") as fh:
                st = os.fstat(fh.fileno())
                mode = 0o755 if st.st_mode & 0o111 else 0o644
                digest = hashlib.sha256()
                remaining = st.st_size
                while remaining > 0:
                    chunk = fh.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    remaining -= len(chunk)
                fh.seek(0)
                info = tarfile.TarInfo(rel)
                info.size = st.st_size
                info.mode = mode
                info.mtime = 0  # 묶음은 불변이다 — 시각이 들어가면 해시가 매번 달라진다
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, fh)
            files.append(
                BundleFile(path=rel, size=st.st_size, sha256=digest.hexdigest(), mode=mode)
            )
    return files, dest.stat().st_size


def collect(
    workspace: Path,
    policy: ArtifactPolicy,
    staging: Path,
    *,
    now_fn: Callable[[], datetime] = _utcnow,
    clock: Callable[[], float] = time.monotonic,
    budget_seconds: int | None = None,
) -> CollectResult:
    """워크스페이스에서 산출물을 모아 `staging` 에 불변 tar 를 만든다(명세 §4 · §5).

    실행이 없었으면 `skipped`, 맞는 것이 없으면 `empty`, 상한·예산·충돌은 `dropped` 다.
    **잡의 성공/실패는 여기서 바꾸지 않는다** — 산출물만 버린다.
    """
    if not policy.enabled():
        return CollectResult(state=EMPTY, reason_code="no_match")
    if not workspace.is_dir():
        return CollectResult(state=SKIPPED, reason_code="not_run")

    budget = policy.timeout_seconds if budget_seconds is None else budget_seconds
    deadline = clock() + budget
    matched, skipped = _budgeted_candidates(workspace, policy, clock, deadline)
    if matched is None:
        return CollectResult(
            state=DROPPED,
            skipped_count=skipped,
            reason_code="timed_out",
            reason_args={"seconds": int(budget)},
        )
    if not matched:
        # 맞긴 맞았는데 전부 일반 파일이 아니었으면 `no_match` 는 거짓이다 — 건너뛴 수가 사정이다.
        return CollectResult(
            state=EMPTY,
            skipped_count=skipped,
            reason_code=None if skipped else "no_match",
        )
    if len(matched) > policy.max_files:
        return CollectResult(
            state=DROPPED,
            skipped_count=skipped,
            reason_code="over_files",
            reason_args={"limit": policy.max_files, "seen": len(matched)},
        )
    seen = 0
    for rel in matched:
        with contextlib.suppress(OSError):
            seen += os.lstat(workspace / rel).st_size
        if seen > policy.max_bytes:
            return CollectResult(
                state=DROPPED,
                skipped_count=skipped,
                reason_code="over_bytes",
                reason_args={"limit": policy.max_bytes, "seen": seen},
            )
    clashes = collisions(matched)
    if clashes:
        # 「충돌 빼고 전부」는 없다 — 하나라도 겹치면 묶음째 버린다. 경로는 detail 에만 담는다.
        return CollectResult(
            state=DROPPED,
            skipped_count=skipped,
            reason_code="path_conflict",
            reason_args={"pairs": len(clashes)},
            detail={"conflicts": [list(pair) for pair in clashes[:20]]},
        )
    dest = staging / BUNDLE_NAME
    try:
        files, bundle_bytes = _write_bundle(workspace, matched, dest)
    except (ArtifactError, OSError) as e:
        dest.unlink(missing_ok=True)
        return CollectResult(
            state=DROPPED,
            skipped_count=skipped,
            reason_code="unsafe_path",
            reason_args={"files": len(matched)},
            detail={"error": type(e).__name__},
        )
    digest = hashlib.sha256()
    with dest.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return CollectResult(
        state=READY,
        files=tuple(files),
        total_bytes=sum(f.size for f in files),
        bundle_bytes=bundle_bytes,
        skipped_count=skipped,
        bundle_sha256=digest.hexdigest(),
        bundle_path=dest,
    )


def _budgeted_candidates(
    workspace: Path,
    policy: ArtifactPolicy,
    clock: Callable[[], float],
    deadline: float,
) -> tuple[list[str] | None, int]:
    """`_candidates` 를 돌면서 예산을 본다. 넘으면 `(None, 지금까지 건너뛴 수)`.

    끝나고 한 번 보는 것으로는 못 막는다 — 워크스페이스가 크면 걷는 동안 이미 예산을 넘긴다.
    """
    patterns = compile_globs(policy.globs)
    if not patterns:
        return [], 0
    matched: list[str] = []
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        rel_dir = os.path.relpath(dirpath, workspace)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/") + "/"
        for name in sorted(filenames):
            rel = prefix + name
            if not any(p.match(rel) for p in patterns):
                continue
            if clock() > deadline:
                return None, skipped
            try:
                st = os.lstat(os.path.join(dirpath, name))
            except OSError:
                skipped += 1
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
                skipped += 1
                continue
            matched.append(rel)
    return sorted(matched), skipped
