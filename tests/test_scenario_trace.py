"""`tools/scenario_trace.py` — 명세의 시험 표(시나리오 ID)와 pytest 시험을 잇는 검사.

명세의 시험 표 바로 위 줄에 `<!-- scenario-prefix: X -->` 를 두고 첫 칸에 `X1` · `X2` … 를 쓴다.
시험은 이름(`test_x3_…`)이나 시험 함수 안의 주석(`# X3`)으로 행을 단다. 형식 · 중복 · 누락 ·
고아 · 접두어 충돌 · **ID 가 하나도 없는 표**를 문제로 센다. 표지가 없는 옛 명세는 건드리지 않는다.

종료 코드: 0 문제 없음 · 1 문제 있음(검사할 표가 하나도 없는 것도) · 2 사용법.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "tools" / "scenario_trace.py"
    spec = importlib.util.spec_from_file_location("scenario_trace", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


st = _load()

PLAN = """# 작업 계획

## 시험

<!-- scenario-prefix: X -->
| ID | 상황 | 기대 |
|---|---|---|
| X1 | 잡을 낸다 | 줄에 선다 |
| X2 | 취소한다 | `cancelled` |
| X3 | 서버를 다시 띄운다 (live) | 남는다 |
"""

TESTS = """
def test_x1_a_job_queues():
    assert True


def test_cancel_closes_it():
    # X2
    assert True
"""


def _run(tmp_path, capsys, plan=PLAN, tests=TESTS, *, extra=(), docs=None):
    (tmp_path / "plan.md").write_text(plan, encoding="utf-8")
    tdir = tmp_path / "tests"
    tdir.mkdir(exist_ok=True)
    (tdir / "test_feature.py").write_text(tests, encoding="utf-8")
    paths = docs or [str(tmp_path / "plan.md")]
    code = st.main([*paths, "--tests", str(tdir), "--json", *extra])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip().startswith("{") else out)


def _kinds(report):
    return sorted((p["kind"], p.get("id") or "") for p in report["problems"])


def test_a_plan_whose_rows_are_all_tagged_passes(tmp_path, capsys):
    code, report = _run(tmp_path, capsys)
    assert code == 0, report
    assert report["ok"] is True
    [doc] = report["documents"]
    assert doc["prefix"] == "X" and doc["ids"] == ["X1", "X2", "X3"] and doc["live"] == ["X3"]


def test_a_row_without_a_test_is_missing(tmp_path, capsys):
    code, report = _run(tmp_path, capsys, tests="def test_x1_only():\n    pass\n")
    assert code == 1
    assert _kinds(report) == [("missing", "X2")]


def test_a_test_naming_an_id_the_plan_lacks_is_an_orphan(tmp_path, capsys):
    code, report = _run(tmp_path, capsys, tests=TESTS + "\n\ndef test_x9_ghost():\n    pass\n")
    assert code == 1
    assert _kinds(report) == [("orphan", "X9")]


def test_duplicate_and_malformed_ids_are_reported(tmp_path, capsys):
    plan = (
        PLAN
        + "| X2 | 또 | 같은 ID |\n| 7 | 번호만 | 형식 틀림 |\n| Y4 | 다른 접두어 | 형식 틀림 |\n"
    )
    code, report = _run(tmp_path, capsys, plan=plan)
    assert code == 1
    assert _kinds(report) == [("duplicate", "X2"), ("format", ""), ("format", "")]


def test_a_document_with_zero_ids_does_not_pass(tmp_path, capsys):
    plan = "<!-- scenario-prefix: X -->\n| ID | 상황 |\n|---|---|\n"
    code, report = _run(tmp_path, capsys, plan=plan, tests="def test_nothing():\n    pass\n")
    assert code == 1
    assert _kinds(report) == [("empty", "")]


def test_a_marker_without_a_table_below_it_is_a_format_problem(tmp_path, capsys):
    plan = "<!-- scenario-prefix: X -->\n\n본문만 있다.\n"
    code, report = _run(tmp_path, capsys, plan=plan, tests="def test_nothing():\n    pass\n")
    assert code == 1
    assert ("format", "") in _kinds(report)


def test_the_same_prefix_in_two_documents_clashes(tmp_path, capsys):
    (tmp_path / "other.md").write_text(PLAN, encoding="utf-8")
    docs = [str(tmp_path / "plan.md"), str(tmp_path / "other.md")]
    code, report = _run(tmp_path, capsys, docs=docs)
    assert code == 1
    assert ("clash", "") in _kinds(report)


def test_a_comment_outside_any_test_function_tags_nothing(tmp_path, capsys):
    tests = "# X1 X2\n\n\ndef helper():\n    # X2\n    pass\n\n\ndef test_x1_a():\n    pass\n"
    code, report = _run(tmp_path, capsys, tests=tests)
    assert code == 1
    assert _kinds(report) == [("missing", "X2")]


def test_one_name_may_tag_several_rows_and_a_live_row_may_be_tagged(tmp_path, capsys):
    tests = "class TestThing:\n    def test_x1_x2_x3_together(self):\n        pass\n"
    code, report = _run(tmp_path, capsys, tests=tests)
    assert code == 0, report


def test_a_longer_prefix_is_not_read_as_a_shorter_one(tmp_path, capsys):
    tests = TESTS + "\n\ndef test_xy1_other_plan():\n    # XY2\n    pass\n"
    code, report = _run(tmp_path, capsys, tests=tests)
    assert code == 0, report


def test_a_document_without_the_marker_is_reported_as_nothing_checked(tmp_path, capsys):
    old = tmp_path / "old.md"
    old.write_text("| S1 | 옛 명세 |\n|---|---|\n", encoding="utf-8")
    code, out = _run(tmp_path, capsys, docs=[str(old)], extra=())
    assert code == 1
    assert out["documents"] == [] and _kinds(out) == [("nothing", "")]
    # 여러 문서 가운데 하나만 표지가 없어도 그 문서는 검사하지 않았다 — 문서마다 적는다(3라운드)
    code, report = _run(tmp_path, capsys, docs=[str(old), str(tmp_path / "plan.md")])
    assert code == 1 and [d["prefix"] for d in report["documents"]] == ["X"]
    assert [(p["kind"], p["where"]) for p in report["problems"]] == [("nothing", str(old))]


def test_text_output_names_the_problem_and_the_place(tmp_path, capsys):
    (tmp_path / "plan.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_x1_a():\n    pass\n")
    code = st.main([str(tmp_path / "plan.md"), "--tests", str(tmp_path / "tests")])
    out = capsys.readouterr().out
    assert code == 1
    assert "missing" in out and "X2" in out and "plan.md:" in out


@pytest.mark.parametrize(
    "argv", [[], ["nope.md"], ["{plan}", "--tests", "{tmp}/no-such-dir"], ["--bogus"]]
)
def test_usage_errors_exit_2(tmp_path, capsys, argv):
    (tmp_path / "plan.md").write_text(PLAN, encoding="utf-8")
    argv = [a.format(plan=tmp_path / "plan.md", tmp=tmp_path) for a in argv]
    assert st.main(argv) == 2


SAMPLE = ROOT / "tests" / "fixtures" / "scenario_trace" / "plan.md"


def test_the_repository_plans_with_a_marker_hold_together():
    """레포의 표지 달린 명세가 서로 · 시험과 어긋나지 않는다(rcm/ci 가 막는다). docs/ 의 누락은 보지
    않는다 — 명세 PR 은 시험보다 먼저 머지되고, PR 여럿이 한 명세를 나눠 구현한다(누락은 CLI 로
    본다). 커밋된 표본 명세(`TRC`)를 늘 같이 넣어 이 시험이 아무것도 안 보고 초록이 되지 않게 한다 —
    흔한 접두어(`S` · `A` …)를 고른 새 명세는 기존 시험 이름과 부딪혀 여기서 빨개진다."""
    marked = [
        p for p in sorted((ROOT / "docs").rglob("*.md")) if st.MARKER_RE.search(p.read_text())
    ]
    report = st.check([*marked, SAMPLE], ROOT / "tests")
    problems = [p for p in report["problems"]
                if p["kind"] != "missing" or str(SAMPLE) in p["where"]]  # fmt: skip
    assert problems == []
    [sample] = [d for d in report["documents"] if d["path"] == str(SAMPLE)]
    assert sample["ids"] == sample["tagged"] == ["TRC1", "TRC2", "TRC3", "TRC4"]


def test_trc1_prefix_usage_counts_the_tags_already_taken(tmp_path, capsys):
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "test_old.py").write_text(
        "def test_s1_s2_old():\n    pass\n\n\ndef test_other():\n    # S7\n    pass\n"
    )
    assert st.main(["--prefix-usage", "S", "--tests", str(tdir)]) == 1
    out = capsys.readouterr().out
    assert "3 test tags" in out and "test_s1_s2_old" in out and "test_other" in out
    assert st.main(["--prefix-usage", "Q", "--tests", str(tdir)]) == 0
    assert "free" in capsys.readouterr().out
    assert st.main(["--prefix-usage", "s1", "--tests", str(tdir)]) == 2
    (tdir / "test_bad.py").write_bytes(b"def test_x():\n    '\xff'\n")
    assert st.main(["--prefix-usage", "Q", "--tests", str(tdir)]) == 1  # 못 읽은 것은 0 이 아니다


def test_trc1_the_help_tells_a_new_workplan_to_pick_an_unused_prefix(capsys):
    assert st.main(["--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "--prefix-usage" in out and "zero usage" in out and "TR" in out


def test_trc2_markers_and_tables_inside_fenced_code_are_not_read(tmp_path, capsys):
    fenced = "```markdown\n<!-- scenario-prefix: X -->\n| ID |\n|---|\n| X9 |\n```\n\n"
    code, report = _run(tmp_path, capsys, plan=fenced + PLAN)
    assert code == 0, report
    assert report["documents"][0]["ids"] == ["X1", "X2", "X3"]
    code, report = _run(tmp_path, capsys, plan="~~~\n<!-- scenario-prefix: X -->\n~~~\n")
    assert code == 1 and _kinds(report) == [("nothing", "")]


def test_trc3_a_plan_that_is_not_utf8_is_a_format_problem(tmp_path, capsys):
    (tmp_path / "bad.md").write_bytes(b"<!-- scenario-prefix: X -->\n\xff\xfe\n")
    code, report = _run(tmp_path, capsys, docs=[str(tmp_path / "bad.md")])
    assert code == 1
    assert ("format", "") in _kinds(report)


def test_trc4_a_multi_letter_prefix_is_accepted(tmp_path, capsys):
    plan = "<!-- scenario-prefix: TR -->\n| ID | 상황 |\n|---|---|\n| TR1 | a |\n| TR2 | b |\n"
    tests = "def test_tr1_a():\n    pass\n\n\ndef test_b():\n    # TR2\n    pass\n"
    tests += "\n\ndef test_t1_not_tr():\n    # T3 TRX1\n    pass\n"
    code, report = _run(tmp_path, capsys, plan=plan, tests=tests)
    assert code == 0, report
    assert report["documents"][0]["tagged"] == ["TR1", "TR2"]


def test_only_the_leading_tokens_of_a_test_name_tag_rows(tmp_path, capsys):
    tests = TESTS + "\n\ndef test_cancel_x3_x9_later():\n    pass\n"
    code, report = _run(tmp_path, capsys, tests=tests.replace("test_x1_a_job", "test_a_x1_job"))
    assert code == 1
    assert _kinds(report) == [("missing", "X1")]  # x3 · x9 는 이름 중간이라 세지 않는다


def test_a_table_without_a_separator_row_is_a_format_problem(tmp_path, capsys):
    plan = "<!-- scenario-prefix: X -->\n| ID | 상황 |\n| X1 | a |\n| X2 | b |\n"
    code, report = _run(tmp_path, capsys, plan=plan, tests="def test_a():\n    pass\n")
    assert code == 1 and _kinds(report) == [("empty", ""), ("format", "")]


@pytest.mark.parametrize("body", [b"def test_x1(:\n", b"def test_x1():\n    '\xff'\n"])
def test_an_unreadable_test_file_is_a_problem(tmp_path, capsys, body):
    code, report = _run(tmp_path, capsys)
    assert code == 0, report
    (tmp_path / "tests" / "test_broken.py").write_bytes(body)
    code = st.main([str(tmp_path / "plan.md"), "--tests", str(tmp_path / "tests"), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1 and _kinds(report) == [("unreadable", "")]


@pytest.mark.parametrize(
    "row, live",
    [("| X3 | 서버를 다시 띄운다 (live) | 남는다 |", True),
     ("| X3 | 서버를 다시 띄운다 | 남는다 | (live) |", True),
     ("| X3 | `(live)` 표시를 고친다 | 남는다 |", False),
     ("| X3 | 서버를 (live) 로 띄운다 | 남는다 |", False),
     ("| X3 | 서버를 다시 띄운다(live) | 남는다 |", False)],
)  # fmt: skip
def test_only_a_cell_ending_with_live_marks_a_live_row(tmp_path, capsys, row, live):
    # 설명 가운데의 `(live)` 는 실기 행 표시가 아니다 — 시험 없는 행이 누락에서 빠졌다(2라운드 F4)
    plan = PLAN.replace("| X3 | 서버를 다시 띄운다 (live) | 남는다 |", row)
    code, report = _run(tmp_path, capsys, plan=plan)
    assert report["documents"][0]["live"] == (["X3"] if live else [])
    assert (code, _kinds(report)) == ((0, []) if live else (1, [("missing", "X3")]))


def test_the_help_says_how_a_live_row_is_marked(capsys):
    assert st.main(["--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "ends with (live)" in out


def test_a_fence_closes_only_on_a_bare_fence_line(tmp_path, capsys):
    # 정보 문자열이 붙은 줄(```text)은 닫는 줄이 아니다 — 그 뒤의 예시 표지를 읽었다(2라운드 F9)
    fenced = "```markdown\n```text\n<!-- scenario-prefix: X -->\n| ID |\n|---|\n| X9 |\n```\n\n"
    code, report = _run(tmp_path, capsys, plan=fenced + PLAN)
    assert code == 0, report
    assert report["documents"][0]["ids"] == ["X1", "X2", "X3"]


def test_a_marker_counts_only_on_a_line_of_its_own(tmp_path, capsys):
    # 본문 속에 인용한 표지는 표지가 아니다(2라운드 F10)
    plan = "표마다 `<!-- scenario-prefix: Y -->` 를 단다.\n\n" + PLAN
    code, report = _run(tmp_path, capsys, plan=plan)
    assert code == 0, report
    assert [d["prefix"] for d in report["documents"]] == ["X"]


def test_the_same_document_given_twice_is_read_once(tmp_path, capsys):
    (tmp_path / "sub").mkdir()
    docs = [str(tmp_path / "plan.md"), str(tmp_path / "sub" / ".." / "plan.md")]
    code, report = _run(tmp_path, capsys, docs=docs)
    assert code == 0, report
    assert len(report["documents"]) == 1


# ── 3라운드: 「못 본 것」을 「없는 것」으로 세지 않는다 ──


def test_an_unclosed_fence_is_a_format_problem_at_the_line_it_opened(tmp_path, capsys):
    # 닫히지 않은 펜스는 문서 끝까지 코드다 — 그 뒤의 표지를 조용히 못 읽고 통과했다(H3)
    plan = PLAN + "\n```text\n<!-- scenario-prefix: Y -->\n| ID |\n|---|\n| Y1 |\n"
    code, report = _run(tmp_path, capsys, plan=plan)
    assert code == 1 and _kinds(report) == [("format", "")]
    opened = PLAN.count("\n") + 2
    [problem] = report["problems"]
    assert problem["where"].endswith(f"plan.md:{opened}") and "never closes" in problem["message"]


def test_a_backtick_fence_whose_info_string_has_a_backtick_is_not_a_fence(tmp_path, capsys):
    # CommonMark: 백틱 펜스의 정보 문자열에는 백틱이 올 수 없다 — 그 줄은 펜스가 아니라 본문이다(H3)
    code, report = _run(tmp_path, capsys, plan="``` a`b\n\n" + PLAN)
    assert code == 0, report
    assert report["documents"][0]["ids"] == ["X1", "X2", "X3"]
    tilde = "~~~ a`b\n<!-- scenario-prefix: Y -->\n| ID |\n|---|\n| Y1 |\n~~~\n\n"
    code, report = _run(tmp_path, capsys, plan=tilde + PLAN)  # 물결 펜스는 백틱이 와도 펜스다
    assert code == 0 and [d["prefix"] for d in report["documents"]] == ["X"], report


@pytest.mark.parametrize(
    "line",
    ["<!-- scenario_prefix: Y -->", "<!-- scenario-prefix Y -->", "<!--scenario-prefix: Y -->",
     "<!-- scenario-prefix:Y -->", "<!-- Scenario-Prefix: Y -->", "<!-- scenario-prefix: Y ->",
     "<!-- scenario prefix: Y -->"],
)  # fmt: skip
def test_a_line_that_looks_like_a_marker_but_is_not_exact_is_a_format_problem(
    tmp_path, capsys, line
):
    # 표지를 잘못 적으면 그 표는 대조되지 않는다 — 조용히 건너뛰지 않고 format 으로 적는다
    plan = PLAN + f"\n{line}\n| ID |\n|---|\n| Y1 |\n"
    code, report = _run(tmp_path, capsys, plan=plan)
    assert code == 1 and _kinds(report) == [("format", "")], report
    assert report["problems"][0]["where"].endswith(f"plan.md:{PLAN.count(chr(10)) + 2}")


@pytest.mark.parametrize("gap", ["\n", "\n메모 한 줄\n", "\n```\n| X9 |\n```\n"])
def test_a_table_broken_by_a_gap_is_a_format_problem_not_silently_short(tmp_path, capsys, gap):
    # 빈 줄 · 본문 줄이 표를 끊으면 뒤의 행을 안 읽었다 — 줄은 있는데 대조하지 않은 행이다
    plan = "<!-- scenario-prefix: X -->\n| ID | a |\n|---|---|\n| X1 | a |\n" + gap + "| X2 | b |\n"
    tests = "def test_x1_a():\n    pass\n"
    code, report = _run(tmp_path, capsys, plan=plan, tests=tests)
    assert code == 1 and _kinds(report) == [("format", "")], report
    assert "interrupted" in report["problems"][0]["message"]
    assert report["problems"][0]["where"].endswith(f"plan.md:{plan.count(chr(10))}")


def test_a_table_after_the_next_heading_belongs_to_no_marker(tmp_path, capsys):
    code, report = _run(tmp_path, capsys, plan=PLAN + "\n## 다음 절\n\n| a | b |\n|---|---|\n")
    assert code == 0, report


def test_a_tag_comment_in_a_helper_nested_in_a_test_tags_nothing(tmp_path, capsys):
    # 주석의 주인은 그 줄을 품은 가장 안쪽 함수다 — 시험 안의 도우미 함수는 시험이 아니다
    tests = "def test_x1_a():\n    def helper():\n        # X2\n        pass\n    helper()\n"
    code, report = _run(tmp_path, capsys, tests=tests)
    assert code == 1 and _kinds(report) == [("missing", "X2")]


def test_prefix_usage_counts_a_tag_anywhere_in_the_tests(tmp_path, capsys):
    # 모듈 주석 · 도우미 안의 주석도 그 접두어를 쓴 것이다 — 「free」라고 하면 새 명세가 부딪힌다
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "test_old.py").write_text(
        "# Q5 is about retries\n\n\ndef helper():\n    # Q6\n    pass\n"
    )
    assert st.main(["--prefix-usage", "Q", "--tests", str(tdir), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["count"] == 2 and "free" not in json.dumps(report)
    assert any("test_old.py:1" in p and "(Q5)" in p for p in report["places"])


def test_a_byte_order_mark_does_not_hide_the_first_marker(tmp_path, capsys):
    code, report = _run(tmp_path, capsys, plan="\ufeff" + PLAN.split("\n\n", 2)[2])
    assert code == 0, report
    assert report["documents"][0]["ids"] == ["X1", "X2", "X3"]


def test_an_indented_code_block_is_not_read(tmp_path, capsys):
    sample = "예시:\n\n    <!-- scenario-prefix: Y -->\n    | ID |\n    |---|\n    | Y1 |\n\n"
    code, report = _run(tmp_path, capsys, plan=sample + PLAN)
    assert code == 0 and [d["prefix"] for d in report["documents"]] == ["X"], report


def test_prefix_usage_with_documents_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "plan.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    argv = [str(tmp_path / "plan.md"), "--prefix-usage", "Q", "--tests", str(tmp_path / "tests")]
    assert st.main(argv) == 2
    assert "--prefix-usage" in capsys.readouterr().err


# ── 4라운드: GFM 이 행으로 그리는 줄 · pytest 가 모으지 않는 시험 · 못 연 폴더 ──


@pytest.mark.parametrize("row", ["X2 | b |", "X2 | b", "`X2` | b |"])
def test_a_row_without_a_leading_pipe_right_after_the_table_is_a_format_problem(
    tmp_path, capsys, row
):
    # GFM 은 앞 `|` 없는 줄도 표의 행으로 그린다 — 표가 거기서 끝난 줄 알고 그 행을 안 읽었다(H1)
    plan = "<!-- scenario-prefix: X -->\n| ID | a |\n|---|---|\n| X1 | a |\n" + row + "\n"
    code, report = _run(tmp_path, capsys, plan=plan, tests="def test_x1_a():\n    pass\n")
    assert code == 1 and _kinds(report) == [("format", "")], report
    assert report["problems"][0]["where"].endswith("plan.md:5")


def test_only_tests_pytest_would_collect_tag_rows(tmp_path, capsys):
    # 도우미 안 · 시험 안의 `test_*` 함수, Test 가 아닌 클래스의 메서드, 시험 파일이 아닌 모듈은
    # pytest 가 돌리지 않는다 — 그 이름 · 주석을 태그로 세면 안 도는 시험이 행을 채운다(M5)
    tests = (
        "def helper():\n    def test_x1_nested():\n        pass\n\n\n"
        "class Helper:\n    def test_x2_method(self):\n        # X1\n        pass\n\n\n"
        "def test_outer():\n    def test_x2_inner():\n        # X1\n        pass\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "helpers.py").write_text("def test_x1_a():\n    # X2\n    pass\n")
    code, report = _run(tmp_path, capsys, tests=tests)
    assert code == 1 and _kinds(report) == [("missing", "X1"), ("missing", "X2")], report
    nested = "class TestOuter:\n    class TestInner:\n        def test_x1_a(self):\n"
    nested += "            pass\n\n    def test_b(self):\n        # X2\n        pass\n"
    code, report = _run(tmp_path, capsys, tests=nested)
    assert code == 0, report  # 시험 클래스 안의 시험 클래스는 pytest 가 모은다
    (tmp_path / "tests" / "feature_test.py").write_text("def test_x9_a():\n    pass\n")
    code, report = _run(tmp_path, capsys, tests=nested)
    assert code == 1 and _kinds(report) == [("orphan", "X9")]  # `*_test.py` 도 시험 파일이다


@pytest.mark.parametrize(
    "tests",
    ["import pytest\n\n\n@pytest.mark.skip(reason='later')\ndef test_x1_a():\n    pass\n",
     "import pytest\n\n\n@pytest.mark.skipif(True, reason='x')\ndef test_x1_a():\n    pass\n",
     "import pytest\n\n\n@pytest.mark.skip\nclass TestA:\n    def test_x1_a(self):\n        pass\n",
     "import pytest\n\npytestmark = pytest.mark.skip\n\n\ndef test_x1_a():\n    pass\n",
     "import pytest\n\npytestmark = [pytest.mark.skip('x')]\n\n\ndef test_x1_a():\n    pass\n"],
)  # fmt: skip
def test_a_tag_on_a_skipped_test_is_a_problem(tmp_path, capsys, tests):
    # 건너뛰는 시험은 그 행을 증명하지 않는다 — 태그가 있어도 「시험 있음」이 아니다(M5)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text("def test_x2_b():\n    pass\n")
    code, report = _run(tmp_path, capsys, tests=tests)
    assert code == 1 and _kinds(report) == [("skipped", "X1")], report


def test_prefix_usage_still_counts_tags_pytest_would_not_collect(tmp_path, capsys):
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "helpers.py").write_text("def helper():\n    def test_q1_nested():\n        pass\n")
    assert st.main(["--prefix-usage", "Q", "--tests", str(tdir), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["count"] == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
@pytest.mark.parametrize("usage", [True, False])
def test_a_test_folder_that_cannot_be_listed_is_unreadable_not_empty(tmp_path, capsys, usage):
    # rglob 은 못 연 폴더를 조용히 건너뛴다 — 그 안의 태그를 「없다」고 셌다
    tdir = tmp_path / "tests"
    (tdir / "locked").mkdir(parents=True)
    (tdir / "locked" / "test_q.py").write_text("def test_q1_a():\n    pass\n")
    (tdir / "locked").chmod(0)
    try:
        if usage:
            code = st.main(["--prefix-usage", "Q", "--tests", str(tdir), "--json"])
            report = json.loads(capsys.readouterr().out)
            assert code == 1 and [p["kind"] for p in report["problems"]] == ["unreadable"]
        else:
            (tmp_path / "plan.md").write_text(PLAN, encoding="utf-8")
            (tdir / "test_feature.py").write_text(TESTS, encoding="utf-8")
            code = st.main([str(tmp_path / "plan.md"), "--tests", str(tdir), "--json"])
            report = json.loads(capsys.readouterr().out)
            assert code == 1 and _kinds(report) == [("unreadable", "")]
    finally:
        (tdir / "locked").chmod(0o755)
