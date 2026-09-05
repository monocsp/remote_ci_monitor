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
    return mapping.get(name, name)


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
