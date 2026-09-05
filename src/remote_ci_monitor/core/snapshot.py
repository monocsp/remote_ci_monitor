"""작업 트리 스냅샷 규칙 — 파일 선택 · `.rcmignore`(gitignore 문법) · `tree_hash`.

순수 함수만 있다. 파일 목록과 내용 해시는 I/O 쪽(`client.py`)이 만들어 넘긴다.

- 후보: git 이면 `git ls-files -z --cached --others --exclude-standard`(추적 + 무시되지 않은
  미추적), 아니면 전체 walk. 작업 트리에 없는 것(삭제)은 빼고 `.git/` 은 항상 제외.
- `.rcmignore` 와 `--exclude` 를 더한다. 마지막에 맞은 규칙이 이긴다(`!` 는 되살림).
  무시된 디렉터리 안은 되살릴 수 없다(git 과 같다).
- `tree_hash = sha256(정렬된 (경로, 모드, 내용 sha256) 목록)`. 합류 판정과 감사에 쓴다.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

ALWAYS_EXCLUDED_DIRS = (".git",)


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negate: bool
    dir_only: bool
    anchored: bool
    regex: re.Pattern[str]


def _glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                # `**/` · `/**` · `**`
                if pattern.startswith("**/", i):
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 1
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def parse_ignore_pattern(raw: str) -> IgnoreRule | None:
    """gitignore 한 줄 → 규칙. 빈 줄·주석은 None."""
    line = raw.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    # 뒤쪽 공백은 무시(이스케이프된 공백은 유지)
    while line.endswith(" ") and not line.endswith("\\ "):
        line = line[:-1]
    negate = False
    if line.startswith("!"):
        negate = True
        line = line[1:]
    elif line.startswith("\\!") or line.startswith("\\#"):
        line = line[1:]
    dir_only = False
    if line.endswith("/") and not line.endswith("\\/"):
        dir_only = True
        line = line.rstrip("/")
    if not line:
        return None
    anchored = "/" in line
    if line.startswith("/"):
        line = line.lstrip("/")
    if not line:
        return None
    body = _glob_to_regex(line)
    regex = re.compile("^" + body + "$" if anchored else "(?:^|/)" + body + "$")
    return IgnoreRule(
        pattern=raw.rstrip("\n"), negate=negate, dir_only=dir_only, anchored=anchored, regex=regex
    )


def parse_ignore(text: str) -> list[IgnoreRule]:
    rules: list[IgnoreRule] = []
    for raw in text.splitlines():
        rule = parse_ignore_pattern(raw)
        if rule is not None:
            rules.append(rule)
    return rules


def _matches(rule: IgnoreRule, path: str, is_dir: bool) -> bool:
    if rule.dir_only and not is_dir:
        return False
    return rule.regex.search(path) is not None


def is_ignored(path: str, rules: Sequence[IgnoreRule], *, is_dir: bool = False) -> bool:
    """경로가 무시되는가. 상위 디렉터리가 무시되면 안쪽은 되살릴 수 없다."""
    parts = path.strip("/").split("/")
    for depth in range(1, len(parts) + 1):
        prefix = "/".join(parts[:depth])
        prefix_is_dir = is_dir if depth == len(parts) else True
        decision: bool | None = None
        for rule in rules:
            if _matches(rule, prefix, prefix_is_dir):
                decision = not rule.negate
        if decision:
            return True
    return False


def select_files(
    candidates: Iterable[str],
    *,
    rules: Sequence[IgnoreRule] = (),
    present: Callable[[str], bool] | None = None,
) -> list[str]:
    """후보 경로에서 삭제된 것 · `.git/` · 무시 규칙에 걸린 것을 빼고 정렬해 돌려준다."""
    out: set[str] = set()
    for raw in candidates:
        path = raw.strip("/")
        if not path:
            continue
        parts = path.split("/")
        if parts[0] in ALWAYS_EXCLUDED_DIRS or any(p == ".git" for p in parts):
            continue
        if present is not None and not present(path):
            continue
        if rules and is_ignored(path, rules):
            continue
        out.add(path)
    return sorted(out)


def tree_hash(entries: Iterable[tuple[str, int, str]]) -> str:
    """(경로, 모드, 내용 sha256) 를 경로순으로 정렬해 sha256. 모드·내용 하나라도 다르면 다르다."""
    h = hashlib.sha256()
    for path, mode, digest in sorted(entries, key=lambda e: e[0]):
        h.update(f"{path}\0{mode:o}\0{digest}\n".encode())
    return h.hexdigest()


def normalize_mode(st_mode: int, *, is_symlink: bool) -> int:
    """git 과 같은 세 가지 모드로 접는다: 100644 · 100755 · 120000."""
    if is_symlink:
        return 0o120000
    return 0o100755 if st_mode & 0o111 else 0o100644
