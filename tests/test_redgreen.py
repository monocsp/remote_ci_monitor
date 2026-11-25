"""`tools/redgreen.py` — 고친 커밋의 시험이 「고치기 전 빨강, 고친 뒤 초록」인지 영수증으로 남긴다.

`<sha>^` 트리에 `<sha>` 의 시험 파일만 얹어 돌리면 실패해야 하고(시험 안에서 — 수집 · import 실패는
`--import-red-ok` 일 때만 빨강으로 친다), `<sha>` 트리에서는 통과해야 한다. 임시 워크트리 둘은 레포
밖에 만들고 끝나면 자기가 만든 것만 치운다.
종료 코드: 0 빨강→초록 · 1 아님 · 2 사용법 · 3 판정 못 함.
레포는 tmp 안의 작은 git 레포다 — 계산 함수 하나와 그 시험.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
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
    def __init__(self, root: Path, src: bool = False) -> None:
        self.path = root / "repo"
        (self.path / "tests").mkdir(parents=True)
        git("init", "-q", cwd=self.path)
        ini = '[tool.pytest.ini_options]\npythonpath = ["."]\n'
        files = {"pyproject.toml": ini, "calc.py": BUGGY, "tests/test_calc.py": OLD_TESTS}
        if src:  # src/ 배치 — pythonpath 설정 없이 redgreen 이 `src` 를 앞에 둔다
            files = {"pyproject.toml": "", "src/calc.py": BUGGY, "tests/test_calc.py": OLD_TESTS}
        self.parent = self.commit("parent", files)

    def commit(self, message: str, files: dict[str, str]) -> str:
        for rel, text in files.items():
            (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.path / rel).write_text(text)
        git("add", "-A", cwd=self.path)
        git("commit", "-q", "-m", message, cwd=self.path)
        return git("rev-parse", "HEAD", cwd=self.path)

    def run(self, capsys, sha: str, *tests: str, extra=()) -> tuple[int, dict | str]:
        argv = ["--fix", sha, "--repo", str(self.path)]
        for t in tests:
            argv += ["--test", t]
        code = rg.main([*argv, *extra])
        out, self.err = capsys.readouterr()
        return code, (json.loads(out) if "--json" in extra else out)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    isolate_git_env(tmp_path, monkeypatch)
    # 임시 워크트리를 이 시험의 폴더 안에 만든다 — 끝난 뒤 비었는지 볼 수 있게
    (tmp_path / "scratch").mkdir()
    monkeypatch.setattr(rg.tempfile, "tempdir", str(tmp_path / "scratch"))
    return Repo(tmp_path)


def _worktrees(repo: Repo) -> int:
    return git("worktree", "list", "--porcelain", cwd=repo.path).count("worktree ")


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
    assert list((repo.path.parent / "scratch").iterdir()) == []  # 임시 폴더도 치웠다


def test_the_receipt_names_no_temporary_or_home_path(repo, capsys):
    tests = NEW_TESTS + "\n\ndef test_where():\n    assert __file__ == ''\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, ADD, extra=["--json"])
    assert code == 0, receipt
    assert (receipt["red"]["tree"], receipt["green"]["tree"]) == ("<red>", "<green>")
    node = "tests/test_calc.py::test_where"
    code, receipt = repo.run(capsys, fix, node, extra=["--json"])
    assert "<red>/tests/test_calc.py" in "\n".join(receipt["red"]["tail"])
    text = json.dumps(receipt)
    scratch = str(repo.path.parent / "scratch")
    assert scratch not in text and os.path.realpath(scratch) not in text
    assert os.path.dirname(sys.executable) not in text
    assert receipt["interpreter"] in (".venv/bin/python", Path(sys.executable).name)
    assert receipt["red"]["cmd"].startswith(receipt["interpreter"] + " -m pytest")


def test_an_interpreter_without_pytest_is_a_usage_error_at_once(repo, capsys, monkeypatch):
    fake = repo.path.parent / "bin" / "python"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(rg.sys, "executable", str(fake))
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, _ = repo.run(capsys, fix, ADD)
    assert code == 2
    assert "./.venv/bin/python tools/redgreen.py" in repo.err
    assert _worktrees(repo) == 1


def test_a_fixture_error_counts_as_red_only_when_asked(repo, capsys):
    tests = "import calc\nimport pytest\n\n\n@pytest.fixture\ndef mul():\n    return calc.mul\n"
    tests += "\n\ndef test_mul(mul):\n    assert mul(2, 3) == 6\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_mul.py": tests})
    node = "tests/test_mul.py::test_mul"
    code, receipt = repo.run(capsys, fix, node, extra=["--json"])
    assert code == 1 and receipt["verdict"] == "not red"
    assert receipt["red"]["kinds"] == {node: "error"}
    code, receipt = repo.run(capsys, fix, node, extra=["--json", "--import-red-ok"])
    assert code == 0 and receipt["verdict"] == "red-then-green"


def test_a_src_layout_repository_runs_against_its_own_tree(tmp_path, monkeypatch, capsys):
    isolate_git_env(tmp_path, monkeypatch)
    repo = Repo(tmp_path, src=True)
    fix = repo.commit("fix", {"src/calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, receipt = repo.run(capsys, fix, ADD, extra=["--json"])
    assert code == 0 and receipt["verdict"] == "red-then-green", receipt


def test_a_node_that_expands_to_tests_passing_before_the_fix_is_mixed(repo, capsys):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py", extra=["--json"])
    assert code == 1 and receipt["verdict"] == "not red"
    assert receipt["red"]["kinds"] == {"tests/test_calc.py": "mixed"}
    assert "tests/test_calc.py::test_zero" in receipt["note"]
    assert "test_add" not in receipt["note"]


def test_worktrees_are_cleaned_even_when_interrupted(repo, capsys, monkeypatch):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg._git

    def interrupted(root, *args, **kw):
        out = real(root, *args, **kw)
        if args[:2] == ("worktree", "add") and args[-1] == fix:
            raise KeyboardInterrupt  # 두 번째 워크트리를 만든 바로 뒤
        return out

    monkeypatch.setattr(rg, "_git", interrupted)
    with pytest.raises(KeyboardInterrupt):
        repo.run(capsys, fix, ADD)
    assert _worktrees(repo) == 1
    assert list((repo.path.parent / "scratch").iterdir()) == []


def test_a_worktree_that_will_not_go_is_reported_and_pruned(repo, capsys, monkeypatch):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg._git

    def stuck(root, *args, **kw):
        if args[:2] == ("worktree", "remove"):
            return rg.subprocess.CompletedProcess(args, 1, "", "fatal: busy")
        return real(root, *args, **kw)

    monkeypatch.setattr(rg, "_git", stuck)
    code = rg.main(["--fix", fix, "--repo", str(repo.path), "--test", ADD])
    err = capsys.readouterr().err
    assert code == 0 and "busy" in err and "<red>" in err
    assert _worktrees(repo) == 1


def test_a_test_path_reaching_outside_the_red_tree_is_never_written(repo, capsys, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    git("rm", "-rq", "tests", cwd=repo.path)
    (repo.path / "tests").symlink_to(outside)
    parent = repo.commit("tests is a link", {})
    (repo.path / "tests").unlink()
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    assert git("rev-parse", f"{fix}^", cwd=repo.path) == parent
    code, _ = repo.run(capsys, fix, ADD)
    assert code == 3 and list(outside.iterdir()) == []
    assert "outside the red tree" in repo.err
    assert _worktrees(repo) == 1


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


@pytest.mark.parametrize(
    "case", ["no-test", "bad-sha", "root", "missing-file", "relative-escape", "directory"]
)
def test_usage_errors_exit_2(repo, capsys, case):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    tests = {"missing-file": ["tests/test_none.py::test_x"], "no-test": [],
             "relative-escape": ["../outside.py::test_x"],
             "directory": ["tests"]}.get(case, [ADD])  # fmt: skip
    sha = {"bad-sha": "f" * 40, "root": repo.parent}.get(case, fix)
    code, _ = repo.run(capsys, sha, *tests)
    assert code == 2
    assert git("worktree", "list", "--porcelain", cwd=repo.path).count("worktree ") == 1


def test_the_receipt_hides_a_path_under_the_home_folder(repo, capsys, monkeypatch):
    # 시험 출력에 집 폴더 아래 경로가 찍혀도 영수증에는 `~` 로 남는다(2라운드 F13)
    probe = Path(os.environ["HOME"]) / "private-notes"
    monkeypatch.setenv("RG_PROBE", str(probe))
    tests = NEW_TESTS + "\n\ndef test_probe():\n    import os\n"
    tests += "    assert os.environ['RG_PROBE'] == add(1, 2)\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py::test_probe", extra=["--json"])
    text = json.dumps(receipt)
    assert "~/private-notes" in text and str(probe) not in text and os.environ["HOME"] not in text


def test_a_resolved_temp_path_never_leaves_a_prefix_behind(repo, capsys, monkeypatch):
    # 풀린 경로가 안 풀린 경로를 품으면(`/private/var` ⊃ `/var`) 짧은 것을 먼저 바꿔
    # `/private<red>` 가 남았다(2라운드 F7). 그 꼴을 심볼릭 링크로 만든다
    base = repo.path.parent
    link = base / "s"
    real = base / "p" / str(link).lstrip("/")
    real.mkdir(parents=True)
    link.symlink_to(real)
    monkeypatch.setattr(rg.tempfile, "tempdir", str(link))
    tests = NEW_TESTS + "\n\ndef test_where():\n    assert __file__ == ''\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py::test_where", extra=["--json"])
    tail = "\n".join(receipt["red"]["tail"])
    assert "<red>/tests/test_calc.py" in tail
    assert str(base / "p") not in json.dumps(receipt) and "/private<" not in tail
    assert "p<red>" not in tail


def test_a_node_id_with_a_leading_dot_slash_is_the_same_test(repo, capsys):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, receipt = repo.run(capsys, fix, "./" + ADD, extra=["--json"])
    assert code == 0 and receipt["verdict"] == "red-then-green", receipt
    assert receipt["tests"] == [ADD] and receipt["files"] == ["tests/test_calc.py"]


def test_a_test_file_stored_as_a_symlink_is_a_usage_error(repo, capsys):
    # 링크(모드 120000)는 blob 이지만 그 내용은 경로 문자열이다 — 얹으면 시험이 아니다(2라운드 F8)
    (repo.path / "tests" / "test_link.py").symlink_to("test_calc.py")
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, _ = repo.run(capsys, fix, "tests/test_link.py::test_add")
    assert code == 2 and "symbolic link" in repo.err
    assert _worktrees(repo) == 1


SLOW_TEST = """

def test_slow():
    import os, pathlib, time
    pathlib.Path(os.environ["RG_STARTED"]).write_text(str(os.getpid()))
    time.sleep(30)
"""
SLOW = NEW_TESTS + SLOW_TEST


def test_sigterm_removes_the_worktrees_and_stops_pytest(repo, tmp_path):
    # SIGTERM 에도 finally 의 정리가 돌고 pytest 프로세스 그룹이 죽는다(2라운드 F3)
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": SLOW})
    started = tmp_path / "started"
    env = {**os.environ, "TMPDIR": str(tmp_path / "scratch"), "RG_STARTED": str(started)}
    cmd = [sys.executable, str(ROOT / "tools" / "redgreen.py"), "--fix", fix,
           "--repo", str(repo.path), "--test", "tests/test_calc.py::test_slow"]  # fmt: skip
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 15
    while not started.exists() and time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    assert started.exists(), proc.communicate()
    child = int(started.read_text())
    try:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
        assert proc.returncode == 143  # 128 + SIGTERM
        assert _worktrees(repo) == 1
        assert list((tmp_path / "scratch").iterdir()) == []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"pytest {child} is still running")
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)
        git("worktree", "prune", cwd=repo.path)
