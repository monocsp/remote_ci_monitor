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


def test_the_repository_plans_with_a_marker_hold_together():
    """레포의 표지 달린 명세가 서로 · 시험과 어긋나지 않는다(rcm/ci 가 막는다). 누락은 보지 않는다 —
    명세 PR 은 시험보다 먼저 머지되고, PR 여럿이 한 명세를 나눠 구현한다(누락은 CLI 로 본다)."""
    docs = [p for p in sorted((ROOT / "docs").rglob("*.md")) if st.MARKER_RE.search(p.read_text())]
    if not docs:
        pytest.skip("no plan in docs/ carries a scenario-prefix marker yet")
    report = st.check(docs, ROOT / "tests")
    assert [p for p in report["problems"] if p["kind"] != "missing"] == []
