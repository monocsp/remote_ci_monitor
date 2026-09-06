"""워크스페이스 자재화 — tree(tar 안전 추출) · git_ref(미러 fetch · 체크아웃).

`tarfile.extractall(filter="data")`(3.11.4+)로 절대 경로 · `..` · 바깥을 가리키는 링크 · 장치 파일을
거부한다. 거부 사유는 짧은 문구로만 돌려준다(서버 경로를 싣지 않는다).
"""

from __future__ import annotations

import shutil
import tarfile
from collections.abc import Callable
from pathlib import Path

from remote_ci_monitor.core.gitref import is_full_sha, short_sha
from remote_ci_monitor.core.manifest import ManifestError, assemble_plan, validate_manifest
from remote_ci_monitor.core.model import Job
from remote_ci_monitor.gitops import GitError, checkout, ensure_mirror, fetch_ref, has_commit


class MaterializeError(Exception):
    """워크스페이스를 만들 수 없다. 메시지는 잡 summary 에 그대로 실린다(경로 없음)."""


def _reject_reason(e: BaseException) -> str:
    # tarfile 의 필터 예외는 메시지에 목적지 절대 경로가 들어갈 수 있어 종류만 옮긴다
    name = type(e).__name__
    mapping = {
        "AbsolutePathError": "absolute path in archive",
        "OutsideDestinationError": "member escapes the workspace",
        "LinkOutsideDestinationError": "link points outside the workspace",
        "AbsoluteLinkError": "absolute link target in archive",
        "SpecialFileError": "device or special file in archive",
        "ReadError": "not a valid tar.gz",
        "CompressionError": "unsupported compression",
        "EOFError": "truncated archive",
    }
    reason = mapping.get(name, name)
    member = getattr(getattr(e, "tarinfo", None), "name", None)
    if isinstance(member, str) and member:
        reason += f": {member[:120]}"  # 클라이언트가 보낸 상대 경로 — 서버 경로가 아니다
    return reason


def extract_tree(tar_path: Path, workspace: Path) -> int:
    """tar.gz 를 워크스페이스에 안전하게 푼다. 멤버 수를 돌려준다."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    if not hasattr(tarfile, "data_filter"):  # pragma: no cover — 3.11.4 미만
        raise MaterializeError("python without tarfile data filter (need 3.11.4+)")
    count = 0
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf:
                count += 1
                tf.extract(member, path=workspace, filter="data")
    except MaterializeError:
        raise
    except tarfile.FilterError as e:
        shutil.rmtree(workspace, ignore_errors=True)
        raise MaterializeError(f"snapshot rejected: {_reject_reason(e)}") from e
    except (tarfile.TarError, EOFError, OSError) as e:
        shutil.rmtree(workspace, ignore_errors=True)
        raise MaterializeError(f"snapshot rejected: {_reject_reason(e)}") from e
    return count


def blob_path(blobs_dir: Path, key: str) -> Path:
    """blob 키 → 파일 경로. 키는 `<sha>` 또는 `<token>/<sha>`(token 범위) — 둘 다 `aa/` 로 분산."""
    prefix, _, sha = key.rpartition("/")
    base = blobs_dir / prefix if prefix else blobs_dir
    return base / sha[:2] / sha


def _copy_blob(src: Path, dst: Path) -> None:
    """blob → 워크스페이스 파일. 복사다(하드링크 금지 — 잡이 파일을 고치면 blob 이 깨진다)."""
    shutil.copyfile(src, dst)


def assemble_from_manifest(manifest_path: Path, blobs_dir: Path, workspace: Path) -> int:
    """`jobs/<id>/manifest.json` 과 blob 저장소로 워크스페이스를 만든다. 만든 항목 수.

    manifest 는 받을 때 검증했지만 여기서 한 번 더 한다(파일이 바뀌었을 수 있다). blob 이 없으면
    blob 이 없으면 `snapshot blob missing <sha7>` — 보존 정리가 지웠거나 손상(--no-cache 재제출).
    """
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    try:
        import json

        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = validate_manifest(doc, max_bytes=1 << 62)
    except (OSError, ValueError, ManifestError) as e:
        shutil.rmtree(workspace, ignore_errors=True)
        raise MaterializeError(f"snapshot manifest unreadable: {type(e).__name__}") from e
    prefix = doc.get("blob_prefix") or ""
    count = 0
    try:
        for op in assemble_plan(manifest):
            target = workspace / op.path
            if op.kind == "mkdir":
                target.mkdir(exist_ok=True)
            elif op.kind == "copy":
                src = blob_path(blobs_dir, prefix + (op.sha256 or ""))
                if not src.is_file():
                    raise MaterializeError(f"snapshot blob missing {short_sha(op.sha256)}")
                _copy_blob(src, target)
                target.chmod(op.mode or 0o644)
            elif op.kind == "symlink":
                target.symlink_to(op.target or "")
            count += 1
    except MaterializeError:
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    except OSError as e:
        shutil.rmtree(workspace, ignore_errors=True)
        raise MaterializeError(f"cannot assemble workspace: {type(e).__name__}") from e
    return count


def prepare_git_ref(
    job: Job,
    workspace: Path,
    *,
    repo_name: str,
    repo_url: str,
    mirror: Path,
    timeout: float,
    log: Callable[[str], None],
) -> None:
    """미러를 원격과 맞추고 제출 때 확정한 sha 를 워크스페이스에 체크아웃한다.

    ref 가 그 사이 옮겨갔어도 **제출 시점의 sha** 를 돈다(재현성). 미러에 그 커밋이 없으면
    강제 push 로 사라진 것이니 실패로 남긴다. 오류 문구에는 URL·경로가 없다.
    """
    sha = job.source.sha
    ref = job.source.ref or ""
    if not sha or not is_full_sha(sha):
        raise MaterializeError("git_ref job has no commit sha")
    if workspace.exists():
        shutil.rmtree(workspace)
    log(f"[rcm] fetching {ref or short_sha(sha)} from {repo_name}")
    try:
        ensure_mirror(mirror, repo_url, timeout=timeout, log=log)
        if not (is_full_sha(ref) and has_commit(mirror, sha)):
            fetch_ref(mirror, repo_url, ref, timeout=timeout, log=log, want_sha=sha)
        if not has_commit(mirror, sha):
            raise MaterializeError(
                f"commit {short_sha(sha)} not found after fetch — ref moved or was force-pushed?"
            )
        checkout(mirror, workspace, sha, timeout=timeout, log=log)
    except GitError as e:
        shutil.rmtree(workspace, ignore_errors=True)
        raise MaterializeError(str(e)) from e
    log(f"[rcm] checked out {short_sha(sha)}")
