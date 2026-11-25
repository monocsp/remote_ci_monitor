"""`tools/scenario_trace.py` — 명세의 시험 표(시나리오 ID)와 pytest 시험을 잇는 검사.

명세의 시험 표 바로 위 줄에 `<!-- scenario-prefix: X -->` 를 두고 첫 칸에 `X1` · `X2` … 를 쓴다.
시험은 이름(`test_x3_…`)이나 시험 함수 안의 주석(`# X3`)으로 행을 단다. 형식 · 중복 · 누락 ·
고아 · 접두어 충돌 · **ID 가 하나도 없는 표**를 문제로 센다. 표지가 없는 옛 명세는 건드리지 않는다.

종료 코드: 0 문제 없음 · 1 문제 있음(검사할 표가 하나도 없는 것도) · 2 사용법.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_documents_without_the_marker_are_ignored_but_nothing_checked_is_not_ok(tmp_path, capsys):
    old = tmp_path / "old.md"
    old.write_text("| S1 | 옛 명세 |\n|---|---|\n", encoding="utf-8")
    code, out = _run(tmp_path, capsys, docs=[str(old)], extra=())
    assert code == 1
    assert out["documents"] == [] and _kinds(out) == [("nothing", "")]
    code, report = _run(tmp_path, capsys, docs=[str(old), str(tmp_path / "plan.md")])
    assert code == 0 and [d["prefix"] for d in report["documents"]] == ["X"]


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
