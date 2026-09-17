"""워크스페이스 자재화 — tree(tar 안전 추출) · git_ref(미러 fetch · 체크아웃).

`tarfile.extractall(filter="data")`(3.11.4+)로 절대 경로 · `..` · 바깥을 가리키는 링크 · 장치 파일을
거부한다. 실패는 **문구가 아니라 코드**로 돌려준다(`MaterializeError`) — 공개 요약으로 가는 값은
닫힌 코드와 경계가 정해진 인자뿐이고, 원문은 보호된 잡 로그로만 간다.
"""

from __future__ import annotations

import shutil
import tarfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.gitref import is_full_sha, short_sha
from remote_ci_monitor.core.manifest import ManifestError, assemble_plan, validate_manifest
from remote_ci_monitor.core.model import Job
from remote_ci_monitor.gitops import GitError, checkout, ensure_mirror, fetch_ref, has_commit


class MaterializeError(Exception):
    """워크스페이스를 만들 수 없다.

    **문구가 아니라 코드**를 든다(`outcome.PREFLIGHT_CODES`). 자유 문구를 들고 다니면 그 문구가
    잡 요약으로, 요약은 토큰 없이 읽히는 `/api/status` 로 간다 — 목적지 절대 경로도 git stderr 도
    그 길로 샜다. 인자는 `outcome.PREFLIGHT_ARGS` 가 정한 모양만 지난다.

    `log` 는 **보호된 잡 로그** 전용 원문이다. 진단에 필요한 경로·stderr 는 버리지 않고 토큰
    뒤로 옮긴다 — 너무 씻어서 고칠 수 없게 만드는 것도 실패다.
    """

    def __init__(self, code: str, /, *, log: str = "", key: str = "", **args: Any) -> None:
        self.code = code
        self.args_public = args
        self.log = log
        #: `blob_missing` 의 전체 blob 키 — 공개 요약엔 안 실리고(짧은 sha 만) 워커가 그 행을 지우는
        #: 데 쓴다.
        self.key = key
        super().__init__(outcome.render(code, args) or code)


def _reject_kind(e: BaseException) -> str:
    """tarfile 예외 → `outcome.REJECT_KINDS` 의 닫힌 키. 예외 **문구**는 절대 옮기지 않는다 —
    거기엔 추출 목적지의 절대 경로가 들어간다."""
    return {
        "AbsolutePathError": "absolute_path",
        "OutsideDestinationError": "escapes_workspace",
        "LinkOutsideDestinationError": "link_outside",
        "AbsoluteLinkError": "absolute_link",
        "SpecialFileError": "special_file",
        "ReadError": "not_a_tarball",
        "CompressionError": "unsupported_compression",
        "EOFError": "truncated",
    }.get(type(e).__name__, "unreadable")


def _rejected(e: BaseException) -> MaterializeError:
    """거절된 아카이브 → 코드 · 까닭 · 문제의 멤버 이름. 멤버는 클라이언트가 보낸 이름이라
    `clean_args` 가 이름만 남긴다(경로도 보이지 않는 문자도 지운다)."""
    member = getattr(getattr(e, "tarinfo", None), "name", None)
    args: dict[str, Any] = {"kind": _reject_kind(e)}
    if isinstance(member, str) and member:
        args["member"] = member
    return MaterializeError("snapshot_rejected", log=f"{type(e).__name__}: {e}", **args)


def extract_tree(tar_path: Path, workspace: Path) -> int:
    """tar.gz 를 워크스페이스에 안전하게 푼다. 멤버 수를 돌려준다."""
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    if not hasattr(tarfile, "data_filter"):  # pragma: no cover — 3.11.4 미만
        raise MaterializeError("workspace_failed", error="NoTarfileDataFilter")
    count = 0
    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            for member in tf:
                count += 1
                tf.extract(member, path=workspace, filter="data")
    except MaterializeError:
        raise
    except (tarfile.FilterError, tarfile.TarError, EOFError, OSError) as e:
        shutil.rmtree(workspace, ignore_errors=True)
        raise _rejected(e) from e
    return count


def blob_path(blobs_dir: Path, key: str) -> Path:
    """blob 키 → 파일 경로. 키는 `<sha>` 또는 `<token>/<sha>`(token 범위) — 둘 다 `aa/` 로 분산."""
    prefix, _, sha = key.rpartition("/")
    base = blobs_dir / prefix if prefix else blobs_dir
    return base / sha[:2] / sha


def stale_blob_keys(blobs_dir: Path, keys: Iterable[str]) -> list[str]:
    """표에는 있는데 **파일이 없는** blob 키. 입력 순서를 지킨다.

    표와 디스크는 갈라질 수 있다 — 2026-09-13~14 사이 blob 파일이 통째로 사라졌는데 `blobs` 행
    6,579개가 남아, 협상(`have_blobs`)이 «있다» 고 답하는 바람에 세션이 그 파일을 안 올렸고
    자재화가 `blob_missing` 으로 죽었다(gate-fast 7회 연속). 그래서 «있다» 는 표가 아니라 파일로
    확인한다.
    """
    return [k for k in keys if not blob_path(blobs_dir, k).is_file()]


def _copy_blob(src: Path, dst: Path) -> None:
    """blob → 워크스페이스 파일. 복사다(하드링크 금지 — 잡이 파일을 고치면 blob 이 깨진다)."""
    shutil.copyfile(src, dst)


def assemble_from_manifest(manifest_path: Path, blobs_dir: Path, workspace: Path) -> int:
    """`jobs/<id>/manifest.json` 과 blob 저장소로 워크스페이스를 만든다. 만든 항목 수.

    manifest 는 받을 때 검증했지만 여기서 한 번 더 한다(파일이 바뀌었을 수 있다). blob 이 없으면
    `blob_missing` — 보존 정리가 지웠거나 손상(--no-cache 재제출).
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
        raise MaterializeError(
            "workspace_failed", error=type(e).__name__, log=f"manifest unreadable: {e}"
        ) from e
    prefix = doc.get("blob_prefix") or ""
    count = 0
    try:
        for op in assemble_plan(manifest):
            target = workspace / op.path
            if op.kind == "mkdir":
                target.mkdir(exist_ok=True)
            elif op.kind == "copy":
                key = prefix + (op.sha256 or "")
                src = blob_path(blobs_dir, key)
                if not src.is_file():
                    raise MaterializeError("blob_missing", sha=short_sha(op.sha256), key=key)
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
        raise MaterializeError(
            "workspace_failed", error=type(e).__name__, log=f"cannot assemble workspace: {e}"
        ) from e
    return count


def assemble_tar_from_manifest(manifest_path: Path, blobs_dir: Path, out_path: Path) -> int:
    """캐시 잡의 manifest + blob 으로 원격 워커에게 줄 tar.gz 를 만든다(M5b-2). 항목 수.

    `.part` 에 쓰고 마지막에 바꿔 넣는다 — 반쯤 만든 파일을 다른 요청이 읽지 않게. 디렉터리는
    파일 경로에서 자연히 생기므로 넣지 않는다(`extract_tree` 가 만든다).
    """
    import json

    try:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = validate_manifest(doc, max_bytes=1 << 62)
    except (OSError, ValueError, ManifestError) as e:
        raise MaterializeError(
            "workspace_failed", error=type(e).__name__, log=f"manifest unreadable: {e}"
        ) from e
    prefix = doc.get("blob_prefix") or ""
    part = out_path.with_name(out_path.name + ".part")
    count = 0
    try:
        with tarfile.open(part, "w:gz") as tar:
            for op in assemble_plan(manifest):
                if op.kind == "copy":
                    key = prefix + (op.sha256 or "")
                    src = blob_path(blobs_dir, key)
                    if not src.is_file():
                        raise MaterializeError("blob_missing", sha=short_sha(op.sha256), key=key)
                    info = tar.gettarinfo(str(src), arcname=op.path)
                    info.mode = op.mode or 0o644
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with src.open("rb") as fh:
                        tar.addfile(info, fh)
                elif op.kind == "symlink":
                    info = tarfile.TarInfo(op.path)
                    info.type = tarfile.SYMTYPE
                    info.linkname = op.target or ""
                    tar.addfile(info)
                else:
                    continue
                count += 1
        part.replace(out_path)
    except MaterializeError:
        part.unlink(missing_ok=True)
        raise
    except (OSError, tarfile.TarError) as e:
        part.unlink(missing_ok=True)
        raise MaterializeError(
            "workspace_failed", error=type(e).__name__, log=f"cannot assemble snapshot: {e}"
        ) from e
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
        raise MaterializeError("workspace_failed", error="NoCommitSha")
    if workspace.exists():
        shutil.rmtree(workspace)
    log(f"[rcm] fetching {ref or short_sha(sha)} from {repo_name}")
    try:
        ensure_mirror(mirror, repo_url, timeout=timeout, log=log)
        if not (is_full_sha(ref) and has_commit(mirror, sha)):
            fetch_ref(mirror, repo_url, ref, timeout=timeout, log=log, want_sha=sha)
        if not has_commit(mirror, sha):
            raise MaterializeError("commit_missing", sha=short_sha(sha))
        checkout(mirror, workspace, sha, timeout=timeout, log=log)
    except GitError as e:
        shutil.rmtree(workspace, ignore_errors=True)
        # git 의 stderr 에는 URL·자격 증명·경로가 들어간다 — 구조만 요약으로, 원문은 잡 로그로.
        raise MaterializeError(
            "git_failed", log=f"{e}\n{e.stderr}".strip(), **e.outcome_args()
        ) from e
    log(f"[rcm] checked out {short_sha(sha)}")
