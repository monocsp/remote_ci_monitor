#!/usr/bin/env python3
"""명세의 시험 표(시나리오 ID)와 pytest 시험이 서로 어긋나지 않는지 본다.

  python3 tools/scenario_trace.py <workplan.md>... [--tests DIR] [--json]
  python3 tools/scenario_trace.py --prefix-usage <P> [--tests DIR]   # 새 명세의 접두어 고르기

왜 있나(2026-11-25): 명세의 시험 행과 실제 시험을 사람이 눈으로 맞췄다. 행이 빠지거나 시험이 없는
행을 가리켜도 아무것도 빨개지지 않는다. 표지 하나와 이름 규칙 하나로 기계가 대조하게 한다.

명세 쪽: 시험 표 **바로 위 줄**에 정확히 `<!-- scenario-prefix: X -->`(대문자 접두어) — 그 줄에
표지만 있어야 한다(본문 속에 인용한 표지는 세지 않는다). 표지처럼 보이는데 꼴이 다른 줄
(`scenario_prefix` · 쌍점이나 띄어쓰기 빠짐 · 대문자)은 format 이다 — 건너뛰면 그 표는 대조되지
않는다. 표의 첫 칸이 ID(`X1`, `X2` …). 표는 빈 줄 · 본문 줄에서 끝난다. 다음 제목 · 표지 전에 같은
표지 아래 `|` 줄이 또 나오면 끊긴 표라서 format 이다(뒤의 행을 읽지 않았다). 표 바로 다음 줄이
빈 줄 · 제목 · 가로줄 · 목록 · 인용 · 펜스 · 표지가 아니면(앞 `|` 가 없거나 `|` 가 아예 없어도
GFM 은 행으로 그린다) 그것도 format 이다. 어느 칸이
`(live)` 이거나 ` (live)` 로 끝나면(시나리오 칸 끝에 붙이는 꼴, `docs/m5t-workplan.md`) pytest 가
아니라 실기(live run)로 증명하는 행이라 누락으로 세지 않는다. 설명 가운데의 `(live)` 는 아니다.
펜스 코드 블록(```` ``` ```` · `~~~`)과 4칸 들여쓴 코드 블록 안의 표지와 표는 예시라서 읽지 않는다.
펜스는 펜스 글자만 있는 줄(정보 문자열 없이)에서만 닫힌다. 백틱 펜스의 정보 문자열에 백틱이 있으면
그 줄은 펜스가 아니다(CommonMark). 문서 끝까지 안 닫힌 펜스는 연 줄을 적은 format 이다. 맨 앞의
BOM 은 떼고 읽는다. 같은 문서를 두 번 주면(다른 철자라도) 한 번만 읽는다.
시험 쪽: 이름 `test_x3_…`(앞에서부터 `test_x1_x2_…` 처럼 여럿) 또는 시험 함수 **안**의 주석 `# X3`.
pytest 가 모으는 시험만 센다 — `test_*.py` · `*_test.py` 파일의 모듈 맨 위 `test_*` 함수와 `Test*`
클래스(`__init__` 없고 `__test__ = False` 가 아닌 것, 안의 `Test*` 클래스까지)의 `test_*` 메서드.
함수 안 · 다른 클래스 · 시험 파일이 아닌 모듈 · pytest 가 안 들어가는 폴더(기본 `norecursedirs`:
`.*` · `build` · `venv` …)의 것은 아니다. 주석의 주인은 그 줄을 품은 가장 안쪽 함수다 — 시험 안에
둔 도우미 함수의 주석은 태그가 아니다. `skip*` · `xfail` 을 단 시험(함수 · 클래스 데코레이터, 모듈 ·
클래스의 `pytestmark`)의 태그는 `skipped` 문제다 — 돌지 않거나 실패해도 초록인 시험은 행을 증명하지
않는다. 조건부는 조건이 없거나 참 상수일 때만이다 — 식이 조건이면 어딘가에서는 돈다.

문제의 종류: format(표지 · 표 · ID 꼴, 안 닫힌 펜스, 끊긴 표) · empty(ID 가 하나도 없는 표 — 검사한
것이 없다) · duplicate · missing(시험이 없는 행) · orphan(명세에 없는 ID 를 단 시험) · clash(두
문서가 같은 접두어) · nothing(표지가 하나도 없는 문서 — 문서마다 적는다. 검사하지 않은 것을 통과로
세지 않는다) · skipped(건너뛰거나 xfail 인 시험의 태그) · unreadable(시험 파일 · 하위 폴더를 못
읽음).
UTF-8 이 아닌 명세는 format 이다.

접두어는 새 명세마다 **아무 시험도 쓰지 않는 것**을 고른다(`--prefix-usage`). 기존 시험이 이미
`test_s1_` · `test_a10_` · `# P2` 꼴을 쓰므로 흔한 한 글자는 고아로 쏟아진다. `TR` 같은 여러 글자도
된다. `--prefix-usage` 는 대조보다 넓게 센다 — 시험 파일이 아닌 모듈까지 모든 `*.py` 에서
시험 밖(모듈 · 도우미)의 `# P2` 주석과 어디에 있든 `test_p2_…` 이름도 쓴 것이다. 쓰는 접두어를
「free」라고 하지 않으려고.

종료 코드: 0 문제 없음 · 1 문제 있음 · 2 사용법(파일 · 폴더 없음, `--prefix-usage` 에 문서를 줌).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import os
import re
import sys
import tokenize
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2

#: 표지의 꼴은 하나다 — 띄어쓰기까지 이대로. 다르게 적은 줄은 LOOKALIKE_RE 가 format 으로 잡는다
MARKER_RE = re.compile(r"<!-- scenario-prefix: (\S*) -->")
LOOKALIKE_RE = re.compile(r"^<!.*scenario[\W_]*prefix|scenario[\W_]*prefix.*>$", re.I)
PREFIX_RE = re.compile(r"^[A-Z]+$")
SEPARATOR_RE = re.compile(r"^\|[\s:|-]+$")
NAME_TOKEN_RE = re.compile(r"^([a-z]+)([1-9]\d*)$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")  # 닫는 펜스 — 정보 문자열이 없다
#: 표 아래에서 표지의 영역을 끝내는 줄 — ATX 제목, setext 밑줄 · 가로줄
SECTION_RE = re.compile(r"^ {0,3}(#{1,6}(\s|$)|=+[ \t]*$|-{3,}[ \t]*$)")
#: 표를 끝내는 블록의 첫 줄 — 목록 · 인용 · 펜스(제목 · 가로줄은 SECTION_RE, 표지는 MARKER_RE)
BLOCK_RE = re.compile(r"^ {0,3}([-+*]|\d{1,9}[.)])([ \t]|$)|^ {0,3}(>|`{3,}|~{3,})")
#: pytest 의 기본 norecursedirs — 이 아래의 시험은 모이지 않는다
NORECURSE = (".*", "build", "dist", "venv", "*.egg", "node_modules", "_darcs", "CVS", "{arch}")
LIVE = "(live)"
SHOWN = 5  # --prefix-usage 가 보여 주는 자리 수


def _problem(kind: str, where: str, message: str, ident: str = "") -> dict[str, str]:
    return {"kind": kind, "where": where, "id": ident, "message": message}


def _code_lines(lines: list[str], path: Path, problems: list[dict[str, str]]) -> set[int]:
    """코드 블록(펜스 · 4칸 들여쓰기) 안의 줄 번호(0부터). 안 닫힌 펜스는 연 줄을 적은 format."""
    code: set[int] = set()
    fence, opened, indented, after_blank = "", 0, False, True
    for index, line in enumerate(lines):
        if fence:  # 같은 글자로 같거나 더 길게, 그 글자만 있는 줄이 닫는다
            code.add(index)
            close = CLOSE_RE.match(line)
            fence = "" if close and close.group(1).startswith(fence) else fence
            continue
        blank = not line.strip()
        wide = line.expandtabs(4)
        # 들여쓴 코드 블록은 문단을 끊지 못한다 — 빈 줄 뒤(또는 문서 첫 줄)에서만 시작한다
        if not blank and (indented or after_blank) and len(wide) - len(wide.lstrip()) >= 4:
            code.add(index)
            indented, after_blank = True, False
            continue
        indented = indented and blank  # 빈 줄은 들여쓴 블록을 끝내지 않는다
        opener = FENCE_RE.match(line)
        if opener and not (opener.group(1)[0] == "`" and "`" in opener.group(2)):
            code.add(index)
            fence, opened, after_blank = opener.group(1), index + 1, False
            continue
        after_blank = blank
    if fence:
        message = f"the {fence} fence opened here never closes — nothing after it was read"
        problems.append(_problem("format", f"{path}:{opened}", message))
    return code


def parse_doc(path: Path, problems: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """문서 하나의 표들 → {접두어: {"ids": {ID: 줄}, "live": set, "line": 표지 줄}}.

    표지가 하나도 없으면(꼴이 틀린 표지도 없으면) `nothing` 을 그 문서 이름으로 적는다."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()  # -sig: 맨 앞 BOM 을 뗀다
    except (OSError, UnicodeDecodeError) as error:
        problems.append(_problem("format", str(path), f"cannot read as UTF-8: {error}"))
        return {}
    code = _code_lines(lines, path, problems)
    groups: dict[str, dict[str, Any]] = {}
    seen = False
    for index, line in enumerate(lines):
        if index in code:
            continue
        where = f"{path}:{index + 1}"
        if (marker := MARKER_RE.fullmatch(line.strip())) is None:
            if LOOKALIKE_RE.search(line.strip()):
                seen = True
                message = "looks like a marker but is not exactly <!-- scenario-prefix: X -->"
                problems.append(_problem("format", where, message))
            continue
        seen = True
        prefix = marker.group(1)
        if not PREFIX_RE.match(prefix):
            problems.append(_problem("format", where, f"prefix {prefix!r} is not A-Z letters"))
            continue
        group = groups.setdefault(prefix, {"ids": {}, "live": set(), "line": index + 1})
        table: list[tuple[int, str]] = []
        for number in range(index + 1, len(lines)):
            if number in code or not lines[number].lstrip().startswith("|"):
                break
            table.append((number + 1, lines[number].strip()))
        if len(table) < 2 or not SEPARATOR_RE.match(table[1][1]):
            problems.append(_problem("format", where, "the marker is not right above a table"))
            continue
        after = index + 1 + len(table)
        if after < len(lines) and after not in code and _row_like(lines[after]):
            # GFM 은 표 바로 뒤의 줄을 빈 줄 · 다른 블록이 아니면 행으로 그린다(앞 `|` 가 없어도,
            # `|` 가 아예 없어도) — 여기서 끝난 줄 알면 그 행을 안 읽는다
            message = "a line right after the table that GFM shows as a row — start it with | or "
            message += "leave a blank line"
            problems.append(_problem("format", f"{path}:{after + 1}", message))
        _interrupted(lines, code, after, index + 1, path, problems)
        id_re = re.compile(rf"^{prefix}([1-9]\d*)$")
        for number, row in table[2:]:
            cell = row.strip("|").split("|", 1)[0].strip().strip("*`")
            at = f"{path}:{number}"
            if not id_re.match(cell):
                problems.append(_problem("format", at, f"first cell {cell!r} is not {prefix}<n>"))
            elif cell in group["ids"]:
                line_no = group["ids"][cell]
                problems.append(_problem("duplicate", at, f"{cell} also on line {line_no}", cell))
            else:
                group["ids"][cell] = number
                cells = [c.strip() for c in row.strip("|").split("|")]
                if any(c == LIVE or c.endswith(" " + LIVE) for c in cells):
                    group["live"].add(cell)
    for prefix, group in groups.items():
        if not group["ids"]:
            where = f"{path}:{group['line']}"
            problems.append(_problem("empty", where, f"prefix {prefix}: no scenario IDs"))
    if not seen:
        message = (
            "no <!-- scenario-prefix: X --> marker in this document — nothing in it was checked"
        )
        problems.append(_problem("nothing", str(path), message))
    return groups


def _row_like(line: str) -> bool:
    """표 바로 뒤의 이 줄을 GFM 이 표의 행으로 그리는지 — 빈 줄 · 제목 · 가로줄 · 목록 · 인용 ·
    펜스 · 표지가 아니면 그린다."""
    text = line.strip()
    if not text or SECTION_RE.match(line) or BLOCK_RE.match(line):
        return False
    return not (MARKER_RE.fullmatch(text) or LOOKALIKE_RE.search(text))


def _interrupted(
    lines: list[str],
    code: set[int],
    start: int,
    marker_line: int,
    path: Path,
    problems: list[dict[str, str]],
) -> None:
    """표가 끝난 뒤 다음 제목 · 표지 전에 `|` 줄이 또 오면 끊긴 표다 — 뒤의 행을 읽지 않았다."""
    for number in range(start, len(lines)):
        text = lines[number].strip()
        if number in code:
            continue
        if (
            SECTION_RE.match(lines[number])
            or MARKER_RE.fullmatch(text)
            or LOOKALIKE_RE.search(text)
        ):
            return
        if text.startswith("|"):
            message = (
                f"the table under the marker on line {marker_line} is interrupted by a "
                "blank or text line — rows after the break were not read"
            )
            problems.append(_problem("format", f"{path}:{number + 1}", message))
            return


def _comments(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
    return out


def _owner(funcs: list[Any], line: int) -> Any:
    """그 줄을 품은 가장 안쪽 함수(없으면 None) — 안쪽 함수가 늘 더 뒤에서 시작한다."""
    around = [f for f in funcs if f.lineno <= line <= (f.end_lineno or f.lineno)]
    return max(around, key=lambda f: f.lineno, default=None)


def _not_run(node: Any) -> str:
    """돌지 않거나 실패해도 초록인 시험의 표 — `skip` · `xfail`(pytest · unittest 의 `skip*` ·
    `expectedFailure`)을 단 데코레이터나 `pytestmark` 값이면 그 이름, 아니면 "". 조건부
    (`skipif` · `skipIf` · `xfail`)는 조건이 없거나 참 상수일 때만, `skipUnless` 는 거짓 상수일 때만
    — 식이 조건이면 어딘가에서는 돈다."""
    if isinstance(node, ast.List | ast.Tuple):
        return next(filter(None, map(_not_run, node.elts)), "")
    call = node if isinstance(node, ast.Call) else None
    node = node.func if call else node
    name = (node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")).lower()
    kind = "skip" if name.startswith("skip") else ""
    kind = "xfail" if name in ("xfail", "expectedfailure") else kind
    if not kind or call is None or name in ("skip", "skiptest", "expectedfailure"):
        return kind
    cond = call.args[0] if call.args else next(
        (k.value for k in call.keywords if k.arg == "condition"), None)  # fmt: skip
    if cond is None:  # 조건 없는 xfail(reason=…) · skipif() — 늘 그렇다
        return kind
    if not isinstance(cond, ast.Constant) or isinstance(cond.value, str):
        return ""  # 식 · 문자열 조건은 돌리는 곳에서 정해진다
    if name == "xfail" and cond.value is None:
        return kind
    return kind if bool(cond.value) != (name == "skipunless") else ""


def _marks(body: list[Any]) -> list[Any]:
    """`pytestmark = …` · `pytestmark: list = …` 의 값 — 모듈 · 클래스 몸통."""
    out = []
    for n in body:
        targets = n.targets if isinstance(n, ast.Assign) else []
        if isinstance(n, ast.AnnAssign) and n.value is not None:
            targets = [n.target]
        if any(getattr(t, "id", "") == "pytestmark" for t in targets):
            out.append(n.value)
    return out


def _flag(body: list[Any], name: str) -> Any:
    """몸통의 `name = <상수>` 값(없으면 None) — 클래스의 `__test__ = False`."""
    for n in body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == name for t in n.targets):
            return n.value.value if isinstance(n.value, ast.Constant) else None
    return None


def _collected(tree: ast.Module) -> list[tuple[Any, str]]:
    """pytest 가 모으는 시험 함수와 그 돌지 않는 표(`_not_run`) — 모듈 맨 위의 `test_*` 와 `Test*`
    클래스(안의 `Test*` 클래스까지, `__init__` 이 없고 `__test__ = False` 가 아닌 것)의 `test_*`
    메서드. 함수 안 · 다른 클래스의 것은 아니다."""
    out: list[tuple[Any, str]] = []

    def first(*kinds: str) -> str:
        return next(filter(None, kinds), "")

    def walk(body: list[Any], kind: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if node.name.startswith("test_"):
                    out.append((node, first(kind, *map(_not_run, node.decorator_list))))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                if any(getattr(n, "name", "") == "__init__" for n in node.body):
                    continue
                if _flag(node.body, "__test__") is False:
                    continue
                marks = [*node.decorator_list, *_marks(node.body)]
                walk(node.body, first(kind, *map(_not_run, marks)))

    walk(tree.body, first(*map(_not_run, _marks(tree.body))))
    return out


def _test_files(tests_dir: Path, every: bool, problems: list[dict[str, str]]) -> list[Path]:
    """시험 폴더의 `*.py`(`every` 가 아니면 pytest 가 모으는 `test_*.py` · `*_test.py` 만).
    못 연 하위 폴더는 unreadable — rglob 처럼 조용히 건너뛰면 그 안의 태그를 「없다」고 센다."""

    def unreadable(error: OSError) -> None:
        where = str(error.filename or tests_dir)
        problems.append(_problem("unreadable", where, f"cannot list: {error.strerror}"))

    found: list[Path] = []
    for folder, dirs, names in os.walk(tests_dir, onerror=unreadable):
        # pytest 가 들어가지 않는 폴더는 대조에서 뺀다 — --prefix-usage(`every`)는 그래도 본다
        skip = ("__pycache__",) if every else ("__pycache__", *NORECURSE)
        dirs[:] = sorted(d for d in dirs if not any(fnmatch.fnmatchcase(d, p) for p in skip))
        for name in names:
            wanted = name.startswith("test_") or name.endswith("_test.py")
            if name.endswith(".py") and (every or wanted):
                found.append(Path(folder) / name)
    return sorted(found)


def scan_tests(
    tests_dir: Path, prefixes: set[str], problems: list[dict[str, str]], anywhere: bool = False
) -> dict[str, list[str]]:
    """시험 파일 → {ID: [「파일:줄 함수」 …]}. pytest 가 모으는 시험(`_collected`)의 이름과, 그 줄을
    품은 가장 안쪽 함수가 그 시험인 주석만 센다. 건너뛰는 시험의 태그는 `skipped` 문제다.
    `anywhere`(--prefix-usage)는 모든 `*.py` 의 어디에 있는 `test_*` 이름 · 주석이든 센다 —
    「안 쓴다」고 말하기 전에 그 접두어의 흔적을 모두 본다."""
    tags: dict[str, list[str]] = {}
    if not prefixes:
        return tags
    alternation = "|".join(sorted(prefixes, key=len, reverse=True))
    comment_re = re.compile(rf"(?<![A-Za-z0-9_])({alternation})([1-9]\d*)(?![A-Za-z0-9_])")
    for path in _test_files(tests_dir, anywhere, problems):
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            comments = [(line, c) for line, c in _comments(text) if comment_re.search(c)]
        except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as error:
            problems.append(_problem("unreadable", str(path), f"cannot read: {error}"))
            continue
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
        owners = {line: _owner(funcs, line) for line, _ in comments}
        tests = [(n, "") for n in funcs if n.name.startswith("test_")]
        for node, not_run in tests if anywhere else _collected(tree):
            found: set[str] = set()
            for token in node.name[5:].split("_"):
                m = NAME_TOKEN_RE.match(token)
                if m is None or m.group(1).upper() not in prefixes:
                    break
                found.add(f"{m.group(1).upper()}{m.group(2)}")
            for line, comment in comments:
                if owners[line] is node:
                    found.update(a + b for a, b in comment_re.findall(comment))
            for ident in sorted(found):
                place = f"{path}:{node.lineno} {node.name}"
                tags.setdefault(ident, []).append(place)
                if not_run:  # 태그는 있지만 돌지 않거나 실패해도 초록 — 행을 증명하지 않는다
                    why = "it never runs" if not_run == "skip" else "it passes even when it fails"
                    message = f"{ident} is tagged on a test marked {not_run} — {why}"
                    problems.append(_problem("skipped", place, message, ident))
        if anywhere:  # 시험 함수 밖의 주석 — 대조에서는 태그가 아니지만 접두어는 쓴 것이다
            for line, comment in comments:
                if owners[line] is None or not owners[line].name.startswith("test_"):
                    for ident in sorted({a + b for a, b in comment_re.findall(comment)}):
                        tags.setdefault(ident, []).append(f"{path}:{line} comment")
    return tags


def check(docs: list[Path], tests_dir: Path) -> dict[str, Any]:
    """문서들과 시험 폴더를 대조한 보고서(dict). `ok` 는 문제가 하나도 없을 때만 참이다."""
    problems: list[dict[str, str]] = []
    owners: dict[str, Path] = {}
    parsed: list[tuple[Path, str, dict[str, Any]]] = []
    # 같은 파일을 다른 철자로 두 번 주면 제 접두어와 부딪힌다 — 풀린 경로로 한 번만 읽는다
    unique: dict[Path, Path] = {}
    for doc in docs:
        unique.setdefault(doc.resolve(), doc)
    docs = list(unique.values())
    for doc in docs:
        for prefix, group in parse_doc(doc, problems).items():
            if prefix in owners and owners[prefix] != doc:
                where = f"{doc}:{group['line']}"
                message = f"prefix {prefix} is also used by {owners[prefix]} — pick another"
                problems.append(_problem("clash", where, message))
            owners.setdefault(prefix, doc)
            parsed.append((doc, prefix, group))
    tags = scan_tests(tests_dir, {prefix for _, prefix, _ in parsed}, problems)
    known: set[str] = set()
    documents = []
    for doc, prefix, group in parsed:
        known.update(group["ids"])
        for ident, line in group["ids"].items():
            if ident not in tags and ident not in group["live"]:
                message = f"{ident} has no test (test_{ident.lower()}_… or # {ident} inside one)"
                problems.append(_problem("missing", f"{doc}:{line}", message, ident))
        ids = sorted(group["ids"], key=lambda i: (len(i), i))  # X2 가 X10 앞
        live, tagged = [i for i in ids if i in group["live"]], [i for i in ids if i in tags]
        documents.append(dict(path=str(doc), prefix=prefix, ids=ids, live=live, tagged=tagged))
    for ident, places in sorted(tags.items()):
        if ident not in known:
            for place in places:
                message = f"{ident} is not a row of any plan given"
                problems.append(_problem("orphan", place, message, ident))
    return {"ok": not problems, "documents": documents, "problems": problems}


def _print_text(report: dict[str, Any]) -> None:
    for doc in report["documents"]:
        print(
            f"{doc['path']}: prefix {doc['prefix']} — {len(doc['ids'])} IDs "
            f"({len(doc['live'])} live), {len(doc['tagged'])} tagged by tests"
        )
    for p in report["problems"]:
        print(f"{p['where']}: {p['kind']}: {p['message']}")
    count = len(report["problems"])
    print("ok" if report["ok"] else f"{count} problem{'s' if count != 1 else ''}")


def prefix_usage(prefix: str, tests_dir: Path, as_json: bool) -> int:
    """접두어 하나를 이미 쓰는 시험 태그 수. 0 이면 새 명세가 써도 된다(종료 0), 아니면 1."""
    problems: list[dict[str, str]] = []
    tags = scan_tests(tests_dir, {prefix}, problems, anywhere=True)
    places = sorted(f"{place} ({ident})" for ident, found in tags.items() for place in found)
    if as_json:
        print(json.dumps({"prefix": prefix, "count": len(places), "places": places,
                          "problems": problems}, ensure_ascii=False, indent=2))  # fmt: skip
    elif places:
        print(f"prefix {prefix}: {len(places)} test tags already use it — pick another")
        for place in places[:SHOWN]:
            print(f"  {place}")
        if len(places) > SHOWN:
            print(f"  … {len(places) - SHOWN} more")
    elif not problems:
        print(f"prefix {prefix}: no test uses it — free for a new workplan")
    for p in [] if as_json else problems:  # 못 읽은 파일이 있으면 0 이라고 말할 수 없다
        print(f"{p['where']}: {p['kind']}: {p['message']}")  # JSON 에는 problems 로 들었다
    return EXIT_PROBLEMS if places or problems else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scenario_trace.py",
        description="Check a workplan's scenario table (<!-- scenario-prefix: X --> above it, "
        "IDs X1, X2 … in the first column) against the tests that tag its rows "
        "(test_x3_… or a # X3 comment inside a test).",
        epilog="A new workplan must pick a prefix with zero usage: run --prefix-usage first. "
        "Existing tests already carry tags such as test_s1_, test_a10_ and # P2, so a common "
        "one-letter prefix turns them into orphans. Multi-letter prefixes such as TR are "
        "allowed and --prefix-usage counts a tag anywhere under the test folder (module or helper "
        "comments too). The marker must be alone on its line and written exactly as "
        "<!-- scenario-prefix: X -->; a line that looks like one but differs is a format problem, "
        "as are a table broken by a blank or text line, a line right after the table that is not "
        "blank or another block (GFM shows it as a row) and a fence that never closes. Only tests "
        "pytest collects tag rows (top-level test_* functions and test_* methods of Test* classes "
        "in test_*.py or *_test.py, outside pytest's default norecursedirs); a tag on a test "
        "marked skip or xfail without a condition, or with a true constant one, is a skipped "
        "problem. "
        "A row is live (proved by a live run, "
        "never counted missing) when one of its cells is (live) or ends with (live) after a "
        "space, as at the end of the scenario cell; (live) in the middle of a cell does not "
        "count. Markers and tables inside fenced or four-space indented code blocks are not "
        "read; a fence closes only on a line of bare fence characters. Every document given "
        "must carry a marker. "
        "exit codes: 0 ok (--prefix-usage: unused) · 1 problems (including a table with no IDs "
        "and no marked document at all; --prefix-usage: already used) · 2 usage",
    )
    ap.add_argument("docs", nargs="*", type=Path, help="workplan / scenario markdown files")
    ap.add_argument("--tests", type=Path, default=Path("tests"), help="test folder (tests/)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument(
        "--prefix-usage",
        metavar="P",
        help="count the test tags that already use prefix P (A-Z letters) and exit 1 if any",
    )
    try:
        args = ap.parse_args(argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code else EXIT_OK
    if args.prefix_usage is not None and not PREFIX_RE.match(args.prefix_usage):
        print(f"--prefix-usage {args.prefix_usage!r}: A-Z letters only", file=sys.stderr)
        return EXIT_USAGE
    if args.prefix_usage is not None and args.docs:
        print("--prefix-usage takes no workplan — run it alone", file=sys.stderr)
        return EXIT_USAGE
    if args.prefix_usage is None and not args.docs:
        print("give at least one workplan, or --prefix-usage P", file=sys.stderr)
        return EXIT_USAGE
    missing = [str(d) for d in args.docs if not d.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    if not args.tests.is_dir():
        print(f"no such test folder: {args.tests}", file=sys.stderr)
        return EXIT_USAGE
    if args.prefix_usage is not None:
        return prefix_usage(args.prefix_usage, args.tests, args.json)
    report = check(args.docs, args.tests)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return EXIT_OK if report["ok"] else EXIT_PROBLEMS


if __name__ == "__main__":
    raise SystemExit(main())
