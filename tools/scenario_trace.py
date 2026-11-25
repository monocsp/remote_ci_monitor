#!/usr/bin/env python3
"""명세의 시험 표(시나리오 ID)와 pytest 시험이 서로 어긋나지 않는지 본다.

  python3 tools/scenario_trace.py <workplan.md>... [--tests DIR] [--json]

왜 있나(2026-11-25): 명세의 시험 행과 실제 시험을 사람이 눈으로 맞췄다. 행이 빠지거나 시험이 없는
행을 가리켜도 아무것도 빨개지지 않는다. 표지 하나와 이름 규칙 하나로 기계가 대조하게 한다.

명세 쪽: 시험 표 **바로 위 줄**에 `<!-- scenario-prefix: X -->`(대문자 접두어). 표의 첫 칸이 ID
(`X1`, `X2` …). 행에 `(live)` 가 있으면 pytest 가 아니라 실기(live run)로 증명하는 행이라 누락으로
세지 않는다. 표지가 없는 문서는 건드리지 않는다(옛 명세).
시험 쪽: 이름 `test_x3_…`(앞에서부터 `test_x1_x2_…` 처럼 여럿) 또는 시험 함수 **안**의 주석 `# X3`.

문제의 종류: format(표지 · 표 · ID 꼴) · empty(ID 가 하나도 없는 표 — 검사한 것이 없다) ·
duplicate · missing(시험이 없는 행) · orphan(명세에 없는 ID 를 단 시험) · clash(두 문서가 같은
접두어) · nothing(넘긴 문서 어디에도 표지가 없다 — 검사하지 않은 것을 통과로 세지 않는다) ·
unreadable(시험 파일을 못 읽음).

종료 코드: 0 문제 없음 · 1 문제 있음 · 2 사용법(파일 · 폴더 없음).
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_USAGE = 2

MARKER_RE = re.compile(r"<!--\s*scenario-prefix:\s*(\S*?)\s*-->")
PREFIX_RE = re.compile(r"^[A-Z]+$")
SEPARATOR_RE = re.compile(r"^\|[\s:|-]+$")
NAME_TOKEN_RE = re.compile(r"^([a-z]+)([1-9]\d*)$")


def _problem(kind: str, where: str, message: str, ident: str = "") -> dict[str, str]:
    return {"kind": kind, "where": where, "id": ident, "message": message}


def parse_doc(path: Path, problems: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """문서 하나의 표들 → {접두어: {"ids": {ID: 줄}, "live": set, "line": 표지 줄}}."""
    lines = path.read_text(encoding="utf-8").splitlines()
    groups: dict[str, dict[str, Any]] = {}
    for index, line in enumerate(lines):
        marker = MARKER_RE.search(line)
        if marker is None:
            continue
        where = f"{path}:{index + 1}"
        prefix = marker.group(1)
        if not PREFIX_RE.match(prefix):
            problems.append(_problem("format", where, f"prefix {prefix!r} is not A-Z letters"))
            continue
        group = groups.setdefault(prefix, {"ids": {}, "live": set(), "line": index + 1})
        table: list[tuple[int, str]] = []
        for number in range(index + 1, len(lines)):
            if not lines[number].lstrip().startswith("|"):
                break
            table.append((number + 1, lines[number].strip()))
        if len(table) < 2 or not SEPARATOR_RE.match(table[1][1]):
            problems.append(_problem("format", where, "the marker is not right above a table"))
            continue
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
                if "(live)" in row:
                    group["live"].add(cell)
    for prefix, group in groups.items():
        if not group["ids"]:
            where = f"{path}:{group['line']}"
            problems.append(_problem("empty", where, f"prefix {prefix}: no scenario IDs"))
    return groups


def _comments(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
    return out


def scan_tests(
    tests_dir: Path, prefixes: set[str], problems: list[dict[str, str]]
) -> dict[str, list[str]]:
    """시험 폴더의 `*.py` → {ID: [「파일:줄 함수」 …]}. 주석은 시험 함수 안의 것만 센다."""
    tags: dict[str, list[str]] = {}
    if not prefixes:
        return tags
    alternation = "|".join(sorted(prefixes, key=len, reverse=True))
    comment_re = re.compile(rf"(?<![A-Za-z0-9_])({alternation})([1-9]\d*)(?![A-Za-z0-9_])")
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            comments = _comments(text)
        except (OSError, UnicodeDecodeError, SyntaxError, tokenize.TokenError) as error:
            problems.append(_problem("unreadable", str(path), f"cannot read: {error}"))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith("test_"):
                continue
            found: set[str] = set()
            for token in node.name[5:].split("_"):
                m = NAME_TOKEN_RE.match(token)
                if m is None or m.group(1).upper() not in prefixes:
                    break
                found.add(f"{m.group(1).upper()}{m.group(2)}")
            end = node.end_lineno or node.lineno
            for line, comment in comments:
                if node.lineno <= line <= end:
                    found.update(a + b for a, b in comment_re.findall(comment))
            for ident in found:
                tags.setdefault(ident, []).append(f"{path}:{node.lineno} {node.name}")
    return tags


def check(docs: list[Path], tests_dir: Path) -> dict[str, Any]:
    """문서들과 시험 폴더를 대조한 보고서(dict). `ok` 는 문제가 하나도 없을 때만 참이다."""
    problems: list[dict[str, str]] = []
    owners: dict[str, Path] = {}
    parsed: list[tuple[Path, str, dict[str, Any]]] = []
    for doc in docs:
        for prefix, group in parse_doc(doc, problems).items():
            if prefix in owners and owners[prefix] != doc:
                where = f"{doc}:{group['line']}"
                message = f"prefix {prefix} is also used by {owners[prefix]} — pick another"
                problems.append(_problem("clash", where, message))
            owners.setdefault(prefix, doc)
            parsed.append((doc, prefix, group))
    if not parsed:
        message = "no document carries a <!-- scenario-prefix: X --> marker — nothing was checked"
        problems.append(_problem("nothing", ", ".join(map(str, docs)), message))
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scenario_trace.py",
        description="Check a workplan's scenario table (<!-- scenario-prefix: X --> above it, "
        "IDs X1, X2 … in the first column) against the tests that tag its rows "
        "(test_x3_… or a # X3 comment inside a test).",
        epilog="exit codes: 0 ok · 1 problems (including a table with no IDs and no marked "
        "document at all) · 2 usage",
    )
    ap.add_argument("docs", nargs="+", type=Path, help="workplan / scenario markdown files")
    ap.add_argument("--tests", type=Path, default=Path("tests"), help="test folder (tests/)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    try:
        args = ap.parse_args(argv)
    except SystemExit as stop:
        return EXIT_USAGE if stop.code else EXIT_OK
    missing = [str(d) for d in args.docs if not d.is_file()]
    if missing:
        print(f"no such file: {', '.join(missing)}", file=sys.stderr)
        return EXIT_USAGE
    if not args.tests.is_dir():
        print(f"no such test folder: {args.tests}", file=sys.stderr)
        return EXIT_USAGE
    report = check(args.docs, args.tests)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return EXIT_OK if report["ok"] else EXIT_PROBLEMS


if __name__ == "__main__":
    raise SystemExit(main())
