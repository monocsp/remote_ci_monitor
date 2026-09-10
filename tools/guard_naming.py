"""브랜치 이름 · 커밋 제목 · PR 제목이 규약에 맞는지 보는 PreToolUse 훅 — 그리고 같은 검사의 CLI.

규약의 정본은 `CONTRIBUTING.md` 「Names」 절이고, 일하는 절차는 `.claude/skills/branch` ·
`commit` · `pr` 스킬이다. 이 파일은 그 규약을 **기계가 읽는 모양**으로 한 번 더 적은 것이다 —
글로만 있는 규칙은 세션이 바뀌면 흐려진다(2026-09-10, `fix/cli-ux` · `worktree-agent-a8b4…` 같은
이름이 그렇게 생겼다).

훅 입력 JSON 을 stdin 으로 받아 판정 JSON 을 stdout 으로 낸다(`.claude/settings.json` 의 `Bash`
매처). 보는 것은 셋뿐이다 — 브랜치를 **만드는** git 명령, `git commit -m` 의 제목, `gh pr create`
의 제목. 그 밖의 명령은 건드리지 않는다. 훅이 못 읽는 모양(파일에서 읽는 `-F`, 알 수 없는 치환)은
막지 않는다 — 검사기가 파서를 이기면 안 된다.

CLI:
  python3 tools/guard_naming.py branch <name>
  python3 tools/guard_naming.py commit "<subject>"
  python3 tools/guard_naming.py pr "<title>"
맞으면 0, 아니면 이유를 찍고 1.

판정 함수(`check_branch` · `check_subject` · `decide`)는 순수하다 — 파일도 시계도 안 본다.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import unicodedata
from dataclasses import dataclass

#: 브랜치와 커밋이 공유하는 타입. Conventional Commits 의 둘(feat · fix)에 Angular 의 여섯을
#: 더했고, 이 레포가 이미 쓰는 chore · release · revert 를 남겼다.
TYPES = (
    "feat",
    "fix",
    "docs",
    "test",
    "refactor",
    "perf",
    "ci",
    "build",
    "chore",
    "release",
    "revert",
)

#: 브랜치는 `<type>/<scope>-<what-it-does>` — 소문자 · 숫자 · 하이픈, 낱말 2~7개.
BRANCH_RE = re.compile(r"^(?P<type>[a-z]+)/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+){1,6})$")
RELEASE_BRANCH_RE = re.compile(r"^release/v\d+\.\d+\.\d+$")
MAX_BRANCH = 48

#: 커밋 제목 · PR 제목: `<type>(<scope>)!: <summary>` — 스코프와 `!` 는 선택.
SUBJECT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[a-z0-9][a-z0-9-]*)\))?(?P<bang>!)?: (?P<summary>\S.*)$"
)
MAX_SUBJECT = 72

#: git 이 스스로 만드는 제목 — 규약 밖이다.
GIT_OWN_PREFIXES = ("Merge ", "Revert ", "fixup! ", "squash! ")

#: 세션 id · sha 조각. 이름에 들어오면 사람이 못 읽는다.
HEX_RUN_RE = re.compile(r"[0-9a-f]{12,}")
#: 마일스톤 코드(`m5g`)는 낱말 하나로 치지 않는다 — 코드만으로는 무슨 일인지 모른다.
MILESTONE_RE = re.compile(r"^m\d+[a-z]?$")

#: 「어디」만 말하는 낱말 — 이것만 남으면 「무엇을」이 없다(`fix/cli-ux`).
SCOPE_WORDS = frozenset(
    {
        "cli",
        "web",
        "ui",
        "ux",
        "server",
        "store",
        "db",
        "worker",
        "janitor",
        "gc",
        "guard",
        "plan",
        "docs",
        "doc",
        "config",
        "progress",
        "queue",
        "retention",
        "notify",
        "artifacts",
        "client",
        "materialize",
        "examples",
        "ci",
        "release",
        "api",
        "status",
        "hook",
        "hooks",
        "skill",
        "skills",
        "test",
        "tests",
    }
)
#: 아무 말도 안 하는 낱말.
GENERIC_WORDS = frozenset(
    {
        "misc",
        "stuff",
        "wip",
        "tmp",
        "temp",
        "update",
        "updates",
        "change",
        "changes",
        "fix",
        "fixes",
        "improve",
        "improvement",
        "improvements",
        "cleanup",
        "work",
        "dev",
        "new",
        "various",
        "minor",
        "patch",
        "issue",
        "bug",
        "bugs",
        "and",
        "or",
        "the",
        "a",
        "of",
        "for",
        "to",
        "v1",
        "v2",
    }
)

DOCS = "CONTRIBUTING.md (Names) · skills: branch, commit, pr"


@dataclass(frozen=True)
class Verdict:
    """`deny` 는 막는다. reason 은 무엇이 왜 틀렸고 어떻게 고치는지까지 말한다."""

    decision: str
    reason: str


# ── 규칙 (순수) ──────────────────────────────────────────────────────────────


def _width(text: str) -> int:
    """터미널 폭 — 한글은 두 칸이다(ruff E501 과 같은 눈금)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def check_branch(name: str) -> str | None:
    """브랜치 이름이 규약에 맞으면 None, 아니면 이유 한 줄."""
    if name in ("main", "dev"):
        return None
    if RELEASE_BRANCH_RE.match(name):
        return None
    if HEX_RUN_RE.search(name):
        return f"'{name}' carries a hash or session id — a person cannot read it; say what changes"
    m = BRANCH_RE.match(name)
    if m is None:
        return (
            f"'{name}' is not <type>/<scope>-<what-it-does> (lowercase, digits, hyphens; "
            f"2-7 words; type one of {', '.join(TYPES[:-1])})"
        )
    if m.group("type") not in TYPES or m.group("type") == "revert":
        return f"'{name}': type '{m.group('type')}' is not one of {', '.join(TYPES[:-1])}"
    if len(name) > MAX_BRANCH:
        return f"'{name}' is {len(name)} chars — keep a branch name at {MAX_BRANCH} or fewer"
    words = m.group("slug").split("-")
    telling = [
        w
        for w in words
        if w not in SCOPE_WORDS and w not in GENERIC_WORDS and not MILESTONE_RE.match(w)
    ]
    if not telling:
        return (
            f"'{name}' says where or which milestone, not what changes — add the change itself "
            f"(e.g. fix/cli-run-no-wait-eta, not fix/cli-ux)"
        )
    return None


def check_subject(subject: str, *, what: str = "commit subject") -> str | None:
    """커밋 제목 · PR 제목이 규약에 맞으면 None, 아니면 이유 한 줄. PR 제목도 같은 규칙이다."""
    if subject.startswith(GIT_OWN_PREFIXES):
        return None
    m = SUBJECT_RE.match(subject)
    if m is None:
        return (
            f"{what} '{_shorten(subject)}' is not `<type>(<scope>): <summary>` "
            f"(type one of {', '.join(TYPES)}; scope optional, lowercase; "
            "one space after the colon)"
        )
    if m.group("type") not in TYPES:
        return f"{what}: type '{m.group('type')}' is not one of {', '.join(TYPES)}"
    summary = m.group("summary")
    if summary.rstrip().endswith("."):
        return f"{what} ends with a period — drop it"
    if _width(subject) > MAX_SUBJECT * 2 or len(subject) > MAX_SUBJECT:
        return (
            f"{what} is {len(subject)} chars — keep the first line at {MAX_SUBJECT} or fewer and "
            f"put the rest in the body"
        )
    if HEX_RUN_RE.search(summary) and not re.search(r"\b[0-9a-f]{7,12}\b", summary):
        return f"{what} carries a long hash — name the change, cite the sha in the body"
    return None


def _shorten(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


# ── 명령 읽기 ────────────────────────────────────────────────────────────────


def _split_segments(command: str) -> list[str]:
    """`;` `&&` `||` `|` 로 나눈다. 따옴표 안은 안 가른다."""
    out: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in ";\n" or command.startswith(("&&", "||"), i) or ch == "|":
            out.append("".join(buf))
            buf = []
            i += 2 if command.startswith(("&&", "||"), i) else 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


HEREDOC_RE = re.compile(r"<<-?\s*'?(?P<tag>[A-Za-z_]+)'?\n(?P<body>.*?)\n\s*(?P=tag)\b", re.S)


def _heredoc_first_line(command: str) -> str | None:
    """`-m "$(cat <<'EOF' … EOF)"` 꼴에서 제목(첫 비어 있지 않은 줄)을 꺼낸다."""
    m = HEREDOC_RE.search(command)
    if m is None:
        return None
    for line in m.group("body").splitlines():
        if line.strip():
            return line.strip()
    return None


def _argv(segment: str) -> list[str]:
    try:
        tokens = shlex.split(segment, comments=True)
    except ValueError:
        return []
    while tokens:
        head = tokens[0]
        if head in ("sudo", "env", "nohup"):
            tokens = tokens[1:]
            continue
        name, sep, _ = head.partition("=")
        if sep and not head.startswith("-") and "/" not in name:
            tokens = tokens[1:]
            continue
        break
    return tokens


def _option_value(argv: list[str], names: tuple[str, ...]) -> str | None:
    for i, tok in enumerate(argv):
        if tok in names:
            return argv[i + 1] if i + 1 < len(argv) else None
        for n in names:
            if n.startswith("--") and tok.startswith(n + "="):
                return tok.split("=", 1)[1]
    return None


def _git_words(argv: list[str]) -> list[str]:
    """`git -C dir -c k=v <sub> …` 에서 앞의 전역 옵션을 뗀 나머지."""
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    return argv[i:]


def _new_branch_name(words: list[str]) -> str | None:
    """브랜치를 **만드는** 명령이면 그 이름. `git branch` 로 목록만 보는 것은 None."""
    if not words:
        return None
    sub = words[0]
    if sub == "worktree" and words[1:2] == ["add"]:
        return _option_value(words, ("-b", "-B"))
    if sub == "checkout":
        return _option_value(words, ("-b", "-B"))
    if sub == "switch":
        return _option_value(words, ("-c", "-C", "--create", "--force-create"))
    if sub == "branch":
        rest = [w for w in words[1:] if not w.startswith("-")]
        flags = [w for w in words[1:] if w.startswith("-")]
        if any(f in ("-m", "-M", "--move", "-c", "-C", "--copy") for f in flags):
            return rest[-1] if rest else None
        if any(
            f in ("-d", "-D", "--delete", "-l", "--list", "-a", "-r", "-v", "-vv") for f in flags
        ):
            return None
        return rest[0] if rest else None
    return None


def _commit_subject(words: list[str], segment: str) -> str | None:
    """`git commit -m` 의 제목. `-F`·`--amend`(제목 없음)·못 읽는 치환은 None."""
    if not words or words[0] != "commit":
        return None
    if "--help" in words or "-h" in words:
        return None
    msg = _option_value(words, ("-m", "--message"))
    if msg is None:
        return None
    if msg.startswith("$("):
        return _heredoc_first_line(segment)
    return msg.splitlines()[0] if msg else None


def _pr_title(argv: list[str], segment: str) -> str | None:
    if argv[:3] != ["gh", "pr", "create"]:
        return None
    title = _option_value(argv, ("--title", "-t"))
    if title is None:
        return None
    if title.startswith("$("):
        return _heredoc_first_line(segment)
    return title


def decide(tool_name: str, tool_input: dict) -> Verdict | None:
    """Bash 명령 하나에 대한 판정. 볼 것이 없으면 None."""
    if tool_name != "Bash":
        return None
    command = str(tool_input.get("command") or "")
    if not command.strip():
        return None
    for segment in _split_segments(command):
        argv = _argv(segment)
        if not argv:
            continue
        head = argv[0].rsplit("/", 1)[-1]
        if head == "git":
            words = _git_words(argv)
            if "--help" in words or "-h" in words:
                continue
            name = _new_branch_name(words)
            if name is not None:
                reason = check_branch(name)
                if reason:
                    return Verdict("deny", f"branch name: {reason}. See {DOCS}.")
            subject = _commit_subject(words, segment)
            if subject is not None:
                reason = check_subject(subject)
                if reason:
                    return Verdict("deny", f"{reason}. See {DOCS}.")
        elif head == "gh":
            title = _pr_title(argv, segment)
            if title is not None:
                reason = check_subject(title, what="PR title")
                if reason:
                    return Verdict("deny", f"{reason}. See {DOCS}.")
    return None


# ── 진입점 ────────────────────────────────────────────────────────────────────


def _cli(args: list[str]) -> int:
    if len(args) != 2 or args[0] not in ("branch", "commit", "pr"):
        print(__doc__.strip().splitlines()[0])
        print("usage: guard_naming.py branch <name> | commit <subject> | pr <title>")
        return 2
    kind, text = args
    reason = (
        check_branch(text)
        if kind == "branch"
        else check_subject(text, what="commit subject" if kind == "commit" else "PR title")
    )
    if reason:
        print(f"no — {reason}")
        return 1
    print("ok")
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        return _cli(sys.argv[1:])
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        verdict = decide(str(payload.get("tool_name", "")), payload.get("tool_input") or {})
    except Exception as error:  # 가드가 터져도 작업은 막지 않는다 — 대신 눈에 보이게 알린다
        print(json.dumps({"systemMessage": f"guard_naming.py failed: {error!r}"}))
        return 0
    if verdict is None:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": verdict.decision,
                    "permissionDecisionReason": verdict.reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
