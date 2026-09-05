"""워크스페이스 자재화 — tree(tar 안전 추출) · git_ref(M3).

`tarfile.extractall(filter="data")`(3.11.4+)로 절대 경로 · `..` · 바깥을 가리키는 링크 · 장치 파일을
거부한다. 거부 사유는 짧은 문구로만 돌려준다(서버 경로를 싣지 않는다).
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path


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


def prepare_git_ref(*args: object, **kwargs: object) -> None:
    """git_ref 소스 모드는 M3. 지금은 명확히 거부한다."""
    raise MaterializeError("git_ref source mode is not implemented yet (planned for M3)")
