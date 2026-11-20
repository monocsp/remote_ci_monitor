"""PR 머지를 `tools/merge_pr.py` 한 길로 모으는 PreToolUse 훅.

`.claude/settings.json` 이 Bash 와 Write · Edit · NotebookEdit 앞에서 이 파일을 부른다. 훅 입력
JSON 을 stdin 으로 받고, 막을 때는 **종료 코드 2** 와 stderr 의 이유로 답한다(Claude Code 는 그
stderr 를 모델에 돌려준다). 볼 것이 없으면 아무것도 찍지 않고 0 이다.

왜 있나(2026-11-20): 백그라운드 감시 스크립트가 30분을 기다린 끝에 아직 pending 인 체크가 남아
있는데 `gh api -X PUT …/pulls/N/merge` 로 머지를 시도했고, 두 번은 실제로 머지됐다 — 룰셋은 필수
체크(`test`)만 보장한다(사건 6c). 격리 재검증 없이 머지된 PR 도 셋 있었다(#213 · #216 · dolomood
#992, 사건 2). `tools/merge_pr.py` 는 head sha 를 고정하고 그 sha 의 체크가 **전부** 끝났는지,
`verified: <sha>` 표식이 있는지 본 뒤 그 sha 로만 머지한다. 이 훅은 그 길을 돌아가는 명령을 막는다.

막는 것 — 명령 글자 또는 쓰려는 파일 내용에:
  `gh pr merge` · `gh api … PUT … pulls/<N>/merge`(옵션 순서 무관, `-X PUT` · `-XPUT` ·
  `--method PUT`) · `curl … -X PUT …/pulls/<N>/merge`(`--request PUT` 도) · GraphQL 의
  `mergePullRequest` · `enablePullRequestAutoMerge`.
지나가는 것:
  `tools/merge_pr.py` 를 부르는 조각, `git …`(커밋 메시지는 머지 이야기를 한다), 본문을 싣는
  `gh pr create|edit|comment|review` · `gh issue …`, 읽기 · 검색(`grep` · `rg` · `echo` …),
  문서 파일(.md · .txt · .rst)과 관문 자신 · 그 시험, 셸 · 파이썬 주석 줄.

알려진 한계(실수 방지용이지 적대자 방어가 아니다): 변수로 조립한 명령(`$CMD`), 다른 줄에 나뉜
파이썬 인자, `gh` 를 거치지 않는 HTTP 라이브러리 호출은 못 본다. MCP 의 GitHub 머지 도구는 이 훅의
매처 밖이다.

판정 `decide()` 는 순수하다 — 파일도 네트워크도 안 본다. 훅이 터지면 막지 않고 경고만 남긴다.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from dataclasses import dataclass

#: `gh pr merge` — `gh -R o/r pr merge` 처럼 사이에 낀 저장소 옵션도.
GH_PR_MERGE_RE = re.compile(r"\bgh\s+(?:(?:-R|--repo)[\s=]+\S+\s+)?pr\s+merge\b")
GH_API_RE = re.compile(r"\bgh\s+api\b")
CURL_RE = re.compile(r"\bcurl\b")
#: `-X PUT` · `-XPUT` · `--method PUT` · `--method=PUT` · `--request PUT`.
PUT_RE = re.compile(r"(?:-X\s*|--request[\s=]+|--method[\s=]+)PUT\b", re.I)
#: `pulls/<N>/merge` — N 은 숫자든 `$N` · `${PR}` · `{n}` 이든.
MERGE_URL_RE = re.compile(r"pulls/([^/\s]+)/merge\b")
#: GraphQL 로 머지하거나 자동 머지를 거는 뮤테이션.
GRAPHQL_MERGE_RE = re.compile(r"\b(?:mergePullRequest|enablePullRequestAutoMerge)\b")

MERGE_TOOL = "tools/merge_pr.py"
DOCS = ".claude/skills/pr/SKILL.md (Merge)"

#: 읽기만 하는 명령 — 인자에 머지 명령이 글자로 있어도 실행하지 않는다.
READ_ONLY = frozenset(
    {
        "grep", "egrep", "fgrep", "rg", "ag", "ack", "echo", "printf", "cat", "less", "more",
        "head", "tail", "sed", "awk", "jq", "wc", "sort", "uniq", "diff", "man", "which", "type",
        "ls", "find", "test", "[",
    }
)  # fmt: skip
#: 앞에서 떼는 감싸기 — 뒤의 명령을 그대로 실행한다.
WRAPPERS = frozenset({"sudo", "env", "nohup", "time", "command", "exec", "builtin"})
#: `gh` 의 본문 · 조회 하위 명령. 본문은 머지 이야기를 한다.
GH_TEXT = frozenset(
    {"create", "edit", "comment", "review", "view", "list", "checks", "diff", "status", "close"}
)
#: 문서 — 머지를 이야기하지 실행하지 않는다.
DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt")
#: 관문 자신과 그 시험 · 변이 목록. 머지 명령을 글자로 품는 것이 일이다.
OWN_FILES = (
    "tools/merge_pr.py",
    "tools/guard_merge.py",
    "tests/test_guard_merge.py",
    "tests/test_merge_pr.py",
    "scripts/mutcheck.py",
)

ASSIGNMENT_RE = re.compile(
    r"""^[A-Za-z_][A-Za-z0-9_]*=(?:\$\([^)]*\)|"(?:[^"\\]|\\.)*"|'[^']*'|\S*)\s+"""
)
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
#: heredoc 이 들어가는 파일 — `cat > x.md <<EOF` · `tee -a notes.txt <<EOF`.
REDIRECT_RE = re.compile(r"(?:>>?|\btee\s+(?:-a\s+)?)\s*(\S+)")


@dataclass(frozen=True)
class Verdict:
    """막는다. reason 은 무엇을 봤고 대신 무엇을 하는지까지 말한다."""

    reason: str


# ── 글자에서 머지 찾기 ───────────────────────────────────────────────────────


def merge_hit(text: str) -> str | None:
    """머지 명령이 글자로 있으면 그 PR 번호(모르면 「<N>」), 없으면 None.

    따옴표 · 쉼표 · 대괄호는 지운다 — `["gh", "pr", "merge"]` 같은 파이썬 인자 목록도 같은
    글자로 읽으려고."""
    flat = re.sub(r"[\"',\[\]]", " ", text)
    if GH_PR_MERGE_RE.search(flat):
        after = flat[GH_PR_MERGE_RE.search(flat).end() :].split()  # type: ignore[union-attr]
        number = next((word for word in after if not word.startswith("-")), "")
        return number if number.isdigit() else "<N>"
    url = MERGE_URL_RE.search(flat)
    rest = GH_API_RE.search(flat) or CURL_RE.search(flat)
    if rest and PUT_RE.search(flat) and url:
        return url.group(1) if url.group(1).isdigit() else "<N>"
    if rest and GRAPHQL_MERGE_RE.search(flat):
        return "<N>"
    return None


def _reason(number: str) -> str:
    return (
        "This merges a pull request outside the merge gate (`gh pr merge`, or a REST "
        f"`PUT …/pulls/{number}/merge`). Merge with `python3 {MERGE_TOOL} {number}` instead: it "
        "pins the head sha, refuses while a check the base branch requires is pending or failed "
        "on that sha (`--wait` waits), needs a `verified: <head sha>` comment from the isolated "
        'verification (or `--no-verify "<reason>"`), and merges that exact sha. In 2026-11 a '
        "watcher merged with checks still pending, and three PRs merged without "
        f"re-verification. See {DOCS}."
    )


# ── 명령 읽기 ────────────────────────────────────────────────────────────────


def _split_segments(command: str) -> list[str]:
    """`;` `&&` `||` `|` `&` 줄바꿈으로 나눈다. 따옴표 안은 안 가른다."""
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
        if command.startswith(("&&", "||"), i):
            out.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in ";\n|&":
            out.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))
    return [s for s in out if s.strip()]


def _words(segment: str) -> list[str]:
    """조각의 낱말. 앞의 `VAR=value`(값이 `$(…)` 여도)와 감싸기는 뗀다."""
    text = segment.strip().lstrip("({").strip()
    while True:
        m = ASSIGNMENT_RE.match(text)
        if m is None:
            break
        text = text[m.end() :]
    try:
        words = shlex.split(text, comments=True)
    except ValueError:
        words = text.split()
    while words and (words[0] in WRAPPERS or (words[0].startswith("-") and len(words) > 1)):
        words = words[1:]
    return words


def _carries_text(words: list[str]) -> bool:
    """머지 이야기를 글로 싣거나 읽기만 하는 조각 — 판정하지 않는다."""
    if not words:
        return True
    if any(word.endswith("merge_pr.py") for word in words):
        return True
    return words[0].rsplit("/", 1)[-1] in READ_ONLY or _writes_a_message(words)


def _writes_a_message(words: list[str]) -> bool:
    """커밋 메시지나 PR · 이슈 본문을 싣는 명령. heredoc 몸통이 이것의 것이면 글이지 명령이 아니다
    (`cat > watch.sh <<EOF` 의 몸통은 스크립트라 읽기 명령이어도 둔다)."""
    if not words:
        return False
    head = words[0].rsplit("/", 1)[-1]
    if head == "git":
        return True
    if head == "gh":
        sub = [word for word in words[1:] if not word.startswith("-")]
        if sub[:1] == ["issue"]:
            return True
        if sub[:1] == ["pr"] and len(sub) > 1 and sub[1] in GH_TEXT:
            return True
    return False


def _into_a_document(line: str) -> bool:
    """heredoc 이 문서 파일(.md · .txt …)로 들어가나 — 그 몸통은 글이다."""
    target = REDIRECT_RE.search(line)
    return bool(target) and target.group(1).strip("'\"").lower().endswith(DOC_SUFFIXES)


def _drop_text_heredocs(command: str) -> str:
    """본문을 싣는 명령(`git commit -F -` · `gh pr create --body "$(cat <<EOF …`)의 heredoc 몸통은
    지운다. 문서로 들어가는 heredoc(`cat > notes.md <<EOF`)도 글이다. 스크립트를 만드는
    heredoc(`cat > watch.sh <<EOF`, `python3 - <<EOF`)은 둔다."""
    lines = command.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = HEREDOC_RE.search(line)
        if m is None:
            i += 1
            continue
        tag = m.group(2)
        end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == tag), None)
        owner = (_split_segments(line[: m.start()]) or [""])[-1]
        if end is not None and (_writes_a_message(_words(owner)) or _into_a_document(line)):
            out.append(lines[end])
            i = end + 1
            continue
        i += 1
    return "\n".join(out)


def _bash_verdict(command: str) -> Verdict | None:
    text = _drop_text_heredocs(command.replace("\\\n", " "))
    for segment in _split_segments(text):
        if _carries_text(_words(segment)):
            continue
        number = merge_hit(segment)
        if number is not None:
            return Verdict(_reason(number))
    return None


def _file_verdict(path: str, content: str) -> Verdict | None:
    """머지하는 스크립트를 쓰는 것. 문서 · 관문 자신 · 주석 줄은 본다 해도 실행되지 않는다."""
    normalized = path.replace("\\", "/")
    if normalized.lower().endswith(DOC_SUFFIXES):
        return None
    if any(normalized == own or normalized.endswith("/" + own) for own in OWN_FILES):
        return None
    for line in content.replace("\\\n", " ").splitlines():
        if line.lstrip().startswith(("#", "//")):
            continue
        number = merge_hit(line)
        if number is not None:
            return Verdict(_reason(number))
    return None


def decide(tool_name: str, tool_input: dict) -> Verdict | None:
    """막을 이유가 있으면 Verdict, 없으면 None."""
    if tool_name == "Bash":
        command = tool_input.get("command")
        return _bash_verdict(command) if isinstance(command, str) else None
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        content = (
            tool_input.get("content")
            or tool_input.get("new_string")
            or tool_input.get("new_source")
        )
        if isinstance(path, str) and isinstance(content, str):
            return _file_verdict(path, content)
    return None


# ── 진입점 ────────────────────────────────────────────────────────────────────


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        verdict = decide(str(payload.get("tool_name", "")), payload.get("tool_input") or {})
    except Exception as error:  # 가드가 터져도 작업은 막지 않는다 — 대신 눈에 보이게 알린다
        print(json.dumps({"systemMessage": f"guard_merge.py failed: {error!r}"}))
        return 0
    if verdict is None:
        return 0
    print(verdict.reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
