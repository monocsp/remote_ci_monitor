"""git_ref 소스 모드의 순수 규칙 — ref 검증 · `ls-remote` 출력에서 sha 고르기.

I/O 가 없다. git 을 실제로 부르는 쪽은 `gitops.py`. 여기서 거른 값만 argv 에 들어가므로
검증은 `git check-ref-format` 규칙의 부분집합을 **보수적으로** 적용한다 — 애매하면 거부.
"""

from __future__ import annotations

import re

MAX_REF_LEN = 200
DASH = "—"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
#: 한 글자라도 있으면 거부하는 문자. `git check-ref-format` 의 금지 목록 + 옵션·경로 주입 방지.
_FORBIDDEN_CHARS = frozenset(" \t\n\r\\^:?*[~")


def is_full_sha(ref: str) -> bool:
    """40 자리 hex 인가(대소문자 무관). `validate_ref` 는 소문자로 정규화한다."""
    return bool(_SHA_RE.match(ref))


def validate_ref(ref: str) -> str:
    """세션이 보낸 ref 를 검사해 정규화한 값을 돌려준다. 틀리면 `ValueError`(사유 한 줄).

    40 hex 는 소문자로 정규화해 그대로 통과. 그 외는 브랜치·태그·완전한 refname 만
    받는다. `-` 로 시작하는 값은 git 이 옵션으로 읽을 수 있어 항상 거부한다.
    """
    if not isinstance(ref, str) or not ref:
        raise ValueError("ref must be a non-empty string")
    if len(ref) > MAX_REF_LEN:
        raise ValueError(f"ref is longer than {MAX_REF_LEN} characters")
    if is_full_sha(ref):
        return ref.lower()
    if ref.startswith("-"):
        raise ValueError("ref must not start with '-'")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in ref):
        raise ValueError("ref contains a control character")
    bad = sorted({ch for ch in ref if ch in _FORBIDDEN_CHARS})
    if bad:
        shown = " ".join(repr(ch) for ch in bad)
        raise ValueError(f"ref contains forbidden character(s): {shown}")
    if ".." in ref or "@{" in ref:
        raise ValueError("ref must not contain '..' or '@{'")
    if ref.startswith("/") or ref.endswith("/") or "//" in ref:
        raise ValueError("ref must not start or end with '/' or contain '//'")
    if ref.endswith(".lock") or ref.endswith("."):
        raise ValueError("ref must not end with '.lock' or '.'")
    if ref == "@":
        raise ValueError("ref must not be '@'")
    for part in ref.split("/"):
        if part.startswith(".") or part.endswith(".lock"):
            raise ValueError("ref components must not start with '.' or end with '.lock'")
    return ref


def _parse_ls_remote(output: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        sha, _, name = line.strip().partition("\t")
        if _SHA_RE.match(sha) and name:
            rows.append((sha.lower(), name))
    return rows


def pick_sha(ls_remote_output: str, ref: str) -> str | None:
    """`git ls-remote -- <url> <ref>` 출력에서 ref 가 가리키는 **커밋** sha 를 고른다.

    우선순위: ① `refs/heads/<ref>` ② `refs/tags/<ref>^{}`(annotated 태그가 가리키는
    커밋) ③ `refs/tags/<ref>` ④ refname 이 `<ref>` 와 정확히 같은 줄(`^{}` 변형이 있으면
    그것). ref 가 40 hex 면 출력과 무관하게 그 값. 없으면 None.
    """
    if is_full_sha(ref):
        return ref.lower()
    rows = dict(reversed([(name, sha) for sha, name in _parse_ls_remote(ls_remote_output)]))
    for candidate in (
        f"refs/heads/{ref}",
        f"refs/tags/{ref}^{{}}",
        f"refs/tags/{ref}",
        f"{ref}^{{}}",
        ref,
    ):
        sha = rows.get(candidate)
        if sha is not None:
            return sha
    return None


_SCP_LIKE_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s]+$")
_URL_SCHEMES = ("https://", "ssh://", "git://", "file://")


def validate_repo_url(url: str) -> str | None:
    """`[[repos]].url` 이 안전한 모양인가. 문제가 있으면 사유, 없으면 None.

    허용: `https://` · `ssh://` · `git://` · `file://` · scp 형(`git@host:path`) · 절대 로컬
    경로(테스트·사내 미러). 그 외(`ext::` 같은 원격 헬퍼, `-` 로 시작, 공백·제어문자)는 거부.
    """
    if not isinstance(url, str) or not url.strip():
        return "url must not be empty"
    if url != url.strip():
        return "url must not have leading or trailing whitespace"
    if url.startswith("-"):
        return "url must not start with '-'"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in url):
        return "url contains whitespace or a control character"
    if url.startswith(_URL_SCHEMES) or url.startswith("/") or _SCP_LIKE_RE.match(url):
        return None
    return "url must be https://, ssh://, git://, file://, user@host:path or an absolute path"


def short_sha(sha: str | None) -> str:
    """표시용 앞 7자. 없으면 「—」."""
    return sha[:7] if sha else DASH
