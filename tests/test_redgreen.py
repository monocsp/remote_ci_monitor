"""`tools/redgreen.py` — 고친 커밋의 시험이 「고치기 전 빨강, 고친 뒤 초록」인지 영수증으로 남긴다.

`<sha>^` 트리에 `<sha>` 의 시험 파일만 얹어 돌리면 실패해야 하고(시험 안에서 — 수집 · import 실패는
`--import-red-ok` 일 때만 빨강으로 친다), `<sha>` 트리에서는 통과해야 한다. 임시 워크트리 둘은 레포
밖에 만들고 끝나면 자기가 만든 것만 치운다.
종료 코드: 0 빨강→초록 · 1 아님 · 2 사용법 · 3 판정 못 함.
레포는 tmp 안의 작은 git 레포다 — 계산 함수 하나와 그 시험.
"""

from __future__ import annotations

import contextlib
import getpass
import importlib.util
import json
import os
import shutil
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
    import os, pathlib, subprocess, sys, time
    # 시험이 띄운 손자 — 프로세스 그룹째 죽어야 남지 않는다(L1)
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pathlib.Path(os.environ["RG_STARTED"] + ".kid").write_text(str(kid.pid))
    pathlib.Path(os.environ["RG_STARTED"]).write_text(str(os.getpid()))
    time.sleep(30)
"""
SLOW = NEW_TESTS + SLOW_TEST


def _gone(pid: int, seconds: float = 5) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("signum, code", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signal_removes_the_worktrees_and_stops_the_whole_pytest_group(
    repo, tmp_path, signum, code
):
    # SIGTERM 에도 finally 의 정리가 돌고 pytest 프로세스 그룹이 죽는다(2라운드 F3). 손자까지
    # 죽는지 보고(L1), SIGINT 도 같은 길로 130 이다(L3)
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
    kid = int(Path(str(started) + ".kid").read_text())
    try:
        proc.send_signal(signum)
        proc.communicate(timeout=10)
        assert proc.returncode == code  # 128 + 신호 번호
        assert _worktrees(repo) == 1
        assert list((tmp_path / "scratch").iterdir()) == []
        assert _gone(child), f"pytest {child} is still running"
        assert _gone(kid), f"the test's child {kid} is still running"
    finally:
        for pid in (child, kid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        git("worktree", "prune", cwd=repo.path)


# ── 3라운드: 끊긴 자리에서도 남기지 않는다 · 영수증에 사람이 드러나지 않는다 ──


def test_signals_are_held_while_a_worktree_is_being_added(repo, capsys, monkeypatch):
    # `git worktree add` 도중에 끊기면 잠긴(initializing) 등록이 남는다 — 그동안은 신호를 미뤘다가
    # add 가 끝난 뒤 받는다(M3)
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real, masks = rg._git, []

    def spy(root, *args, **kw):
        if args[:2] == ("worktree", "add"):
            masks.append(signal.pthread_sigmask(signal.SIG_BLOCK, []))
        return real(root, *args, **kw)

    monkeypatch.setattr(rg, "_git", spy)
    code, _ = repo.run(capsys, fix, ADD)
    assert code == 0 and len(masks) == 2
    assert all({signal.SIGTERM, signal.SIGINT} <= mask for mask in masks), masks
    assert {signal.SIGTERM, signal.SIGINT}.isdisjoint(signal.pthread_sigmask(signal.SIG_BLOCK, []))


def test_a_locked_worktree_left_by_a_cut_add_is_removed(repo, capsys, monkeypatch):
    # 끊긴 add 는 잠긴 등록을 남긴다 — `remove --force` 한 번은 잠긴 것을 못 지우고, 폴더를 지운
    # 뒤의 prune 도 잠긴 등록은 남긴다. `remove -f -f` 가 지운다(M3)
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg._git

    def cut(root, *args, **kw):
        out = real(root, *args, **kw)
        if args[:2] == ("worktree", "add"):
            git("worktree", "lock", "--reason", "initializing", args[-2], cwd=repo.path)
            raise SystemExit(143)
        return out

    monkeypatch.setattr(rg, "_git", cut)
    try:
        with pytest.raises(SystemExit):
            repo.run(capsys, fix, ADD)
        assert _worktrees(repo) == 1
        assert list((repo.path.parent / "scratch").iterdir()) == []
        # remove 자체가 지워야 한다 — 남은 등록 지우기(_unregister)는 마지막 그물이다
        assert "could not remove" not in capsys.readouterr().err
    finally:
        listed = git("worktree", "list", "--porcelain", cwd=repo.path)
        for line in listed.splitlines()[1:]:
            if line.startswith("worktree "):
                git("worktree", "remove", "-f", "-f", line[len("worktree ") :], cwd=repo.path)


def test_a_cleanup_that_fails_is_a_non_zero_exit(repo, capsys, monkeypatch):
    # 못 치운 것을 stderr 에만 적고 0 으로 끝나면 다음 실행 · 다른 세션이 남은 등록을 밟는다
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg._git

    admin = repo.path / ".git" / "worktrees"

    def stuck(root, *args, **kw):
        if args[:2] == ("worktree", "remove"):
            admin.chmod(0o555)  # 남은 등록(관리 폴더)도 못 지운다
            return rg.subprocess.CompletedProcess(args, 1, "", "fatal: busy")
        return real(root, *args, **kw)

    monkeypatch.setattr(rg, "_git", stuck)
    try:
        code = rg.main(["--fix", fix, "--repo", str(repo.path), "--test", ADD, "--json"])
        out, err = capsys.readouterr()
        assert code == 4, err
        assert json.loads(out)["verdict"] == "red-then-green"  # 판정은 그대로 찍는다
        assert "cleanup failed" in err and "<red>" in err and "<green>" in err
    finally:
        admin.chmod(0o755)
        git("worktree", "prune", cwd=repo.path)


def test_a_signal_right_after_mkdtemp_still_removes_the_folder(repo, capsys, monkeypatch):
    # mkdtemp 과 try 사이에 끊기면 임시 폴더가 남았다 — 만드는 동안 신호를 미룬다
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg.tempfile.mkdtemp

    def made_then_cut(*args, **kw):
        out = real(*args, **kw)
        os.kill(os.getpid(), signal.SIGTERM)
        return out

    monkeypatch.setattr(rg.tempfile, "mkdtemp", made_then_cut)
    with pytest.raises(SystemExit) as stop:
        repo.run(capsys, fix, ADD)
    assert stop.value.code == 143
    assert list((repo.path.parent / "scratch").iterdir()) == []
    assert _worktrees(repo) == 1


def test_the_receipt_names_neither_the_user_nor_the_temp_folder(repo, capsys, monkeypatch):
    # pytest 의 tmp_path 는 `<임시>/pytest-of-<사용자>/…` 다 — 영수증(PR 본문)에 사용자 이름과
    # 임시 폴더가 남았다(M4). `--basetemp` 를 임시 트리 곁에 두고 `<tmp>` · `<user>` 로 바꾼다
    tests = NEW_TESTS + (
        "\n\ndef test_where(tmp_path):\n    import getpass, tempfile\n"
        "    assert add(1, 2) == 0, f'{tmp_path} | {getpass.getuser()} | {tempfile.gettempdir()}'\n"
    )
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_calc.py::test_where", extra=["--json"])
    text = json.dumps(receipt)
    tail = "\n".join(receipt["red"]["tail"])
    assert "<red-tmp>/test_where0 | <user> | <tmp>" in tail, tail
    assert getpass.getuser() not in text and "pytest-of-" not in text
    for temp in {rg.tempfile.gettempdir(), os.environ.get("TMPDIR", "").rstrip("/")} - {""}:
        assert temp not in text and os.path.realpath(temp) not in text
    assert "--basetemp=<red-tmp>" in receipt["red"]["cmd"]


def test_a_failed_git_show_never_writes_an_empty_test_file(repo, capsys, monkeypatch):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg._git

    def broken(root, *args, **kw):
        if args[:1] == ("show",):
            return rg.subprocess.CompletedProcess(args, 128, b"", b"fatal: bad object")
        return real(root, *args, **kw)

    monkeypatch.setattr(rg, "_git", broken)
    code, receipt = repo.run(capsys, fix, ADD, extra=["--json"])
    assert code == 3 and receipt["red"] is None and receipt["verdict"] == "unknown"
    assert "git show" in repo.err and "bad object" in repo.err
    assert _worktrees(repo) == 1


@pytest.mark.parametrize("raw", ["tests//test_calc.py::test_add", "tests/./test_calc.py::test_add"])
def test_a_test_path_with_empty_or_dot_segments_is_normalised(repo, capsys, raw):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    code, receipt = repo.run(capsys, fix, raw, extra=["--json"])
    assert code == 0 and receipt["verdict"] == "red-then-green", (receipt, repo.err)
    assert receipt["tests"] == [ADD] and receipt["files"] == ["tests/test_calc.py"]


# ── 4라운드: 건너뛴 시험 · 남의 등록 · 띄어쓴 매개변수 ──

MORE = """import pytest
from calc import add


def test_add():
    assert add(1, 2) == 3


@pytest.mark.skip(reason="later")
def test_skipped():
    pass


class TestK:
    def test_add(self):
        assert add(2, 2) == 4

    @pytest.mark.skip(reason="later")
    def test_skipped(self):
        pass


@pytest.mark.parametrize("v", [1, 2])
def test_p(v):
    if v == 2:
        pytest.skip("later")
    assert add(v, 1) == v + 1
"""


@pytest.mark.parametrize(
    "node, skipped",
    [("tests/test_more.py", "tests/test_more.py::test_skipped"),
     ("tests/test_more.py::TestK", "tests/test_more.py::TestK::test_skipped"),
     ("tests/test_more.py::test_p", "tests/test_more.py::test_p[2]")],
)  # fmt: skip
def test_a_skipped_test_inside_a_requested_node_is_undecided(repo, capsys, node, skipped):
    # `-rA` 는 건너뛴 시험을 `SKIPPED [1] 파일:줄` 로 접어 찍어 nodeid 가 없다 — 안 읽혀서 파일 ·
    # 클래스 · 매개변수 nodeid 의 건너뛴 시험이 안 보였고 영수증은 red-then-green 이었다(H3)
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_more.py": MORE})
    code, receipt = repo.run(capsys, fix, node, extra=["--json"])
    assert code == 3 and receipt["verdict"] == "unknown", receipt
    assert receipt["red"]["kinds"] == receipt["green"]["kinds"] == {node: "none"}
    assert skipped in receipt["note"] and "skipped" in receipt["note"]


def test_an_xfail_next_to_a_passing_test_is_not_passed(repo, capsys):
    # 통과 하나와 xfail 하나가 섞인 파일은 「통과」가 아니다 — xfail 은 기대대로 실패한 것이다(M4)
    tests = "import pytest\nfrom calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    tests += "\n\n@pytest.mark.xfail(strict=False)\ndef test_later():\n    assert False\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_more.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_more.py", extra=["--json"])
    assert code == 3 and receipt["green"]["kinds"] == {"tests/test_more.py": "none"}, receipt
    assert "tests/test_more.py::test_later" in receipt["note"]


def test_a_test_skipped_on_the_red_side_alone_is_undecided(repo, capsys):
    # 빨강 쪽이 모두 건너뛰면 pytest 는 0 으로 끝난다 — 「not red」가 아니라 모름이다(L2)
    tests = "import pytest\nfrom calc import add\n\n\n@pytest.mark.skipif(add(1, 2) != 3, "
    tests += "reason='x')\ndef test_add():\n    assert add(1, 2) == 3\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_more.py": tests})
    code, receipt = repo.run(capsys, fix, "tests/test_more.py::test_add", extra=["--json"])
    assert code == 3 and receipt["verdict"] == "unknown", receipt


def test_cleanup_never_prunes_a_worktree_it_did_not_make(repo, capsys, monkeypatch):
    # 전역 `git worktree prune` 은 남의 (폴더가 잠시 없는) 등록까지 지운다(L1)
    other = repo.path.parent / "someone-elses"
    git("worktree", "add", "-q", "--detach", str(other), "HEAD", cwd=repo.path)
    shutil.rmtree(other)  # 다른 세션이 외장 디스크를 뺐다 — 등록은 그 세션의 것이다
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real = rg._git

    def stuck(root, *args, **kw):
        if args[:2] == ("worktree", "remove"):
            return rg.subprocess.CompletedProcess(args, 1, "", "fatal: busy")
        assert args[:2] != ("worktree", "prune"), args
        return real(root, *args, **kw)

    monkeypatch.setattr(rg, "_git", stuck)
    code = rg.main(["--fix", fix, "--repo", str(repo.path), "--test", ADD])
    capsys.readouterr()
    listed = git("worktree", "list", "--porcelain", cwd=repo.path)
    assert code == 0 and str(other) in listed and listed.count("worktree ") == 2, listed


def test_a_failed_worktree_add_does_not_print_the_temporary_path(repo, capsys, monkeypatch):
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": NEW_TESTS})
    real, seen = rg._git, []

    def refused(root, *args, **kw):
        if args[:2] == ("worktree", "add"):
            seen.append(args[-2])
            why = f"fatal: could not create leading directories of '{args[-2]}/.git'"
            return rg.subprocess.CompletedProcess(args, 128, "", why)
        return real(root, *args, **kw)

    monkeypatch.setattr(rg, "_git", refused)
    code, _ = repo.run(capsys, fix, ADD)
    assert code == 3 and "worktree add failed" in repo.err and "<red>/.git" in repo.err
    assert seen and seen[0] not in repo.err and str(Path(seen[0]).parent) not in repo.err  # L3


def test_a_parameter_id_with_spaces_is_read_whole(repo, capsys):
    # `FAILED tests/x.py::test_p[one two] - …` 를 첫 빈칸에서 잘라 그 nodeid 를 못 찾았다(L4)
    tests = NEW_TESTS.replace("from calc import add", "import pytest\nfrom calc import add")
    tests += "\n\n@pytest.mark.parametrize('v', [1], ids=['one - two'])\ndef test_p(v):\n"
    tests += "    assert add(v, 1) == 2\n"
    fix = repo.commit("fix", {"calc.py": FIXED, "tests/test_calc.py": tests})
    node = "tests/test_calc.py::test_p[one - two]"
    code, receipt = repo.run(capsys, fix, node, extra=["--json"])
    assert code == 0 and receipt["verdict"] == "red-then-green", receipt
    assert receipt["green"]["passed"] == [node]


@pytest.mark.parametrize(
    ("body", "ok"),
    [("version_tuple = (8, 2, 9)\n", False), ("version_tuple = (8, 3, 0)\n", True),
     ("version_tuple = (9, 0, 0, 'dev')\n", True), ("raise ImportError('broken pytest')\n", False)],
)  # fmt: skip
def test_the_pytest_version_gate_reads_the_interpreter_and_stays_quiet(
    tmp_path, monkeypatch, capfd, body, ok
):
    # 8.3 앞의 pytest 는 `--no-fold-skipped` 가 없다 — 가짜 pytest 로 판정을 본다. 못 여는 pytest 의
    # 트레이스백은 사용법 안내 앞에 찍히지 않는다(5라운드 L3)
    fake = tmp_path / "fake" / "pytest"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(body)
    monkeypatch.setenv("PYTHONPATH", str(fake.parent))
    assert rg.has_pytest() is ok
    out, err = capfd.readouterr()
    assert (out, err) == ("", "")
