"""`tools/redgreen.py` — 고친 커밋의 시험이 「고치기 전 빨강, 고친 뒤 초록」인지 영수증으로 남긴다.

`<sha>^` 트리에 `<sha>` 의 시험 파일만 얹어 돌리면 실패해야 하고(시험 안에서 — 수집 · import 실패는
`--import-red-ok` 일 때만 빨강으로 친다), `<sha>` 트리에서는 통과해야 한다. 임시 워크트리 둘은 레포
밖에 만들고 끝나면 자기가 만든 것만 치운다.
종료 코드: 0 빨강→초록 · 1 아님 · 2 사용법 · 3 판정 못 함.
레포는 tmp 안의 작은 git 레포다 — 계산 함수 하나와 그 시험.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gitrepo import git, isolate_git_env

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "tools" / "redgreen.py"
    spec = importlib.util.spec_from_file_location("redgreen", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rg = _load()

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
OLD_TESTS = "from calc import add\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
NEW_TESTS = OLD_TESTS + "\n\ndef test_add():\n    assert add(1, 2) == 3\n"


class Repo:
    def __init__(self, root: Path) -> None:
        self.path = root / "repo"
        (self.path / "tests").mkdir(parents=True)
        git("init", "-q", cwd=self.path)
        ini = '[tool.pytest.ini_options]\npythonpath = ["."]\n'
        self.parent = self.commit("parent", {"pyproject.toml": ini, "calc.py": BUGGY,
                                             "tests/test_calc.py": OLD_TESTS})  # fmt: skip

    def commit(self, message: str, files: dict[str, str]) -> str:
        for rel, text in files.items():
            (self.path / rel).write_text(text)
        git("add", "-A", cwd=self.path)
        git("commit", "-q", "-m", message, cwd=self.path)
        return git("rev-parse", "HEAD", cwd=self.path)

    def run(self, capsys, sha: str, *tests: str, extra=()) -> tuple[int, dict | str]:
        argv = ["--fix", sha, "--repo", str(self.path)]
        for t in tests:
            argv += ["--test", t]
        code = rg.main([*argv, *extra])
        out = capsys.readouterr().out
        return code, (json.loads(out) if "--json" in extra else out)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    isolate_git_env(tmp_path, monkeypatch)
    return Repo(tmp_path)


ADD = "tests/test_calc.py::test_add"


def test_a_real_fix_is_red_then_green_and_leaves_no_worktree(repo, capsys):
    keep = repo.path.parent / "someone-elses-worktree"
    git("worktree", "add", "-q", "--detach", str(keep), "HEAD", cwd=repo.path)
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, receipt = repo.run(capsys, fix, ADD, extra=["--json"])
    assert code == 0, receipt
    assert receipt["verdict"] == "red-then-green"
    assert (receipt["fix"], receipt["parent"]) == (fix, repo.parent)
    assert receipt["red"]["exit"] == 1 and receipt["red"]["kinds"] == {ADD: "failed"}
    assert receipt["green"]["exit"] == 0 and receipt["green"]["kinds"] == {ADD: "passed"}
    assert "assert -1 == 3" in "\n".join(receipt["red"]["tail"])
    assert receipt["files"] == ["tests/test_calc.py"]
    listed = git("worktree", "list", "--porcelain", cwd=repo.path)
    assert listed.count("worktree ") == 2 and str(keep) in listed
    for side in ("red", "green"):
        assert not Path(receipt[side]["tree"]).exists()


def test_the_text_receipt_names_both_shas_and_the_commands(repo, capsys):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, out = repo.run(capsys, fix, ADD)
    assert code == 0
    assert fix in out and repo.parent in out and "pytest" in out and "red-then-green" in out


def test_a_test_that_already_passes_before_the_fix_is_not_red(repo, capsys):
    tests = OLD_TESTS + "\n\ndef test_add_zero():\n    assert add(5, 0) == 5\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py::test_add_zero", extra=["--json"])
    assert code == 1 and receipt["verdict"] == "not red"


def test_a_test_that_still_fails_after_the_fix_is_not_green(repo, capsys):
    tests = NEW_TESTS + "\n\ndef test_wrong():\n    assert add(1, 1) == 3\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py::test_wrong", extra=["--json"])
    assert code == 1 and receipt["verdict"] == "not green"


def test_an_import_failure_counts_as_red_only_when_asked(repo, capsys):
    tests = "from calc import mul\n\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_mul.py": tests})
    node = "tests/test_mul.py::test_mul"
    code, receipt = repo.run(capsys, fix, node, extra=["--json"])
    assert code == 1 and receipt["verdict"] == "not red"
    assert receipt["red"]["kinds"] == {node: "collection"}
    code, receipt = repo.run(capsys, fix, node, extra=["--json", "--import-red-ok"])
    assert code == 0 and receipt["verdict"] == "red-then-green"


def test_a_test_pytest_cannot_find_is_undecided(repo, capsys):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py::test_nope", extra=["--json"])
    assert code == 3 and receipt["verdict"] == "unknown"


def test_a_skipped_test_is_never_green(repo, capsys):
    tests = NEW_TESTS.replace("def test_add", "import pytest\n\n\n@pytest.mark.skipif(\n"
                              "    add(1, 2) == 3, reason='x')\ndef test_add")  # fmt: skip
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, ADD, extra=["--json"])
    assert code == 3 and receipt["green"]["kinds"] == {ADD: "none"}


def test_a_test_skipped_before_the_fix_is_not_proven_red(repo, capsys):
    extra = "\n\n@pytest.mark.skipif(add(1, 2) != 3, reason='x')\ndef test_later():\n    pass\n"
    tests = "import pytest\n\n" + NEW_TESTS + extra
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, ADD, "tests/test_calc.py::test_later", extra=["--json"])
    assert receipt["red"]["kinds"]["tests/test_calc.py::test_later"] == "none"
    assert code == 3 and receipt["verdict"] == "unknown"


@pytest.mark.parametrize("case", ["no-test", "bad-sha", "root", "missing-file", "relative-escape"])
def test_usage_errors_exit_2(repo, capsys, case):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    tests = {"missing-file": ["tests/test_none.py::test_x"], "no-test": [],
             "relative-escape": ["../outside.py::test_x"]}.get(case, [ADD])  # fmt: skip
    sha = {"bad-sha": "f" * 40, "root": repo.parent}.get(case, fix)
    code, _ = repo.run(capsys, sha, *tests)
    assert code == 2
    assert git("worktree", "list", "--porcelain", cwd=repo.path).count("worktree ") == 1
