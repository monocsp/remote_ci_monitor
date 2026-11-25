"""`tools/dev_batch.py` — 한 마일스톤의 PR 여럿을 형제 워크트리에서 동시에 만들 때의 장부.

상태 파일 경로는 `--batch` 나 `RCM_DEV_BATCH` 로만 받는다(현재 폴더로 물러나지 않는다). `add` 는
브랜치 이름(`tools/guard_naming.py`)과 경로(glob · 절대 · `..` · 맨 위 뿌리 통째 · 다른 묶음과
겹침, 머지된 묶음은 빼고)를 본다. `scope` 는 base 뒤 머지가 아닌 커밋이 owns ∪ shared 밖을
건드렸는지 커밋 sha 와 함께 적는다. `status` 는 GitHub(가짜)에서 PR · 원격 head · `rcm/ci` ·
`verified:` · 머지를 읽고, 모르는 값은 `—` 에 종료 3 이다. 원격은 tmp 안의 bare 레포다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gitrepo import build_remote, git, isolate_git_env

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


db = _load("dev_batch")
ApiError = db.merge_pr.ApiError

A_BRANCH = "feat/queue-add-retry-limit"
B_BRANCH = "feat/web-show-retry-badge"


class Batch:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        isolate_git_env(tmp_path, monkeypatch)
        monkeypatch.delenv("RCM_DEV_BATCH", raising=False)
        self.tmp = tmp_path
        self.remote = build_remote(tmp_path)
        self.clone = tmp_path / "clone"
        git("clone", "-q", "--branch", "dev", self.remote.url, str(self.clone))
        monkeypatch.chdir(self.clone)
        self.state = tmp_path / "state" / "batch.json"

    def run(self, *args: str, api=None) -> int:
        return db.main(["--batch", str(self.state), *args], api=api)

    def worktree(self, name: str, branch: str) -> Path:
        path = self.tmp / name
        git("worktree", "add", "-q", "-b", branch, str(path), "origin/dev", cwd=self.clone)
        return path

    def add(self, bundle: str, branch: str, wt: Path, *owns: str, extra=()) -> int:
        order = str(len(self.read().get("bundles", {})) + 1)
        args = ["add", "--bundle", bundle, "--branch", branch, "--worktree", str(wt)]
        return self.run(*args, "--owns", *owns, *extra, "--merge-order", order)

    def read(self) -> dict:
        return json.loads(self.state.read_text()) if self.state.exists() else {}


@pytest.fixture
def batch(tmp_path, monkeypatch):
    b = Batch(tmp_path, monkeypatch)
    assert b.run("init", "--id", "m5x", "--repo", "o/r") == 0
    return b


def _commit(wt: Path, rel: str, text: str = "x\n") -> str:
    (wt / rel).parent.mkdir(parents=True, exist_ok=True)
    (wt / rel).write_text(text)
    git("add", "-A", cwd=wt)
    git("commit", "-q", "-m", f"touch {rel}", cwd=wt)
    return git("rev-parse", "HEAD", cwd=wt)


def test_the_state_path_comes_only_from_the_flag_or_the_environment(tmp_path, monkeypatch, capsys):
    b = Batch(tmp_path, monkeypatch)
    assert db.main(["init", "--id", "m5x", "--repo", "o/r"]) == 2
    assert "~/.local/state/rcm-dev-batch/m5x.json" in capsys.readouterr().err
    assert not list(b.clone.glob("*.json"))
    monkeypatch.setenv("RCM_DEV_BATCH", str(b.state))
    assert db.main(["init", "--id", "m5x", "--repo", "o/r"]) == 0
    assert b.read()["base"] == b.remote.dev


def test_init_pins_the_fetched_base_and_never_overwrites(batch):
    state = batch.read()
    assert state["id"] == "m5x" and state["base_ref"] == "origin/dev"
    assert state["base"] == batch.remote.dev and state["repo"] == "o/r"
    assert batch.run("init", "--id", "m5x", "--repo", "o/r") == 1


@pytest.mark.parametrize(
    "owns",
    ["src/*.py", "/etc/passwd", "docs/../src/x.py", "src/", "src/remote_ci_monitor", "tests",
     ".claude/", "tools/", ""],
)  # fmt: skip
def test_add_refuses_paths_that_are_not_a_narrow_claim(batch, owns):
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, owns) == 1
    assert "A" not in batch.read()["bundles"]


def test_add_checks_the_branch_name_and_the_worktree(batch, capsys):
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", "wip", wt, "docs/a.md") == 1
    assert "wip" in capsys.readouterr().err
    assert batch.add("A", B_BRANCH, wt, "docs/a.md") == 1  # 워크트리는 다른 브랜치에 있다
    rel = ["add", "--bundle", "A", "--branch", A_BRANCH, "--worktree", "wt-a"]
    assert batch.run(*rel, "--owns", "docs/a.md", "--merge-order", "1") == 2
    assert batch.add("A", A_BRANCH, wt, "docs/a.md", extra=["--after", "Z"]) == 1


def test_overlapping_owns_are_refused_until_the_owner_is_merged(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    b = batch.worktree("wt-b", B_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/sub/a.md", "src/remote_ci_monitor/queue.py") == 0
    assert batch.add("B", B_BRANCH, b, "docs/sub/") == 1
    assert "docs/sub/a.md" in capsys.readouterr().err
    assert batch.add("B", B_BRANCH, b, "web/app.js", extra=["--shared", "docs/sub/a.md"]) == 1
    state = batch.read()
    state["bundles"]["A"]["merged"] = "c" * 40
    batch.state.write_text(json.dumps(state))
    assert batch.add("B", B_BRANCH, b, "docs/sub/", extra=["--after", "A"]) == 0
    assert batch.read()["bundles"]["B"]["after"] == ["A"]


def test_scope_names_each_stray_path_with_its_commit(batch, capsys):
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, "src/remote_ci_monitor/queue.py", "tests/test_q.py",
                     extra=["--shared", "CHANGELOG.md"]) == 0  # fmt: skip
    _commit(wt, "src/remote_ci_monitor/queue.py")
    _commit(wt, "tests/test_q.py")
    _commit(wt, "CHANGELOG.md")
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 0
    # 다른 묶음이 dev 에 머지한 것을 들여온 병합은 세지 않는다
    git("checkout", "-q", "dev", cwd=batch.remote.work)
    git("pull", "-q", "origin", "dev", cwd=batch.remote.work)
    _commit(batch.remote.work, "web/app.js")
    git("push", "-q", "origin", "dev", cwd=batch.remote.work)
    git("fetch", "-q", "origin", cwd=wt)
    git("merge", "-q", "--no-edit", "origin/dev", cwd=wt)
    assert batch.run("scope", "--bundle", "A") == 0
    stray = _commit(wt, "README.md")
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 1
    out = capsys.readouterr().out
    assert stray[:12] in out and "README.md" in out and "web/app.js" not in out
    assert batch.run("scope", "--bundle", "Q") == 2


class FakeGitHub:
    """status 가 읽는 자리만: 브랜치 head · PR 목록 · rcm/ci 상태 · 댓글."""

    def __init__(self, heads, prs=(), statuses=None, comments=None, down=False):
        self.heads, self.prs = heads, list(prs)
        self.statuses, self.comments = statuses or {}, comments or {}
        self.down = down

    def request(self, method, path, fields=None):
        assert method == "GET" and path.startswith("repos/o/r/"), (method, path)
        if self.down:
            raise ApiError("HTTP 502", 502)
        rest = path[len("repos/o/r/") :]
        if rest.startswith("branches/"):
            branch = rest[len("branches/") :]
            if branch not in self.heads:
                raise ApiError("HTTP 404: Branch not found", 404)
            return {"commit": {"sha": self.heads[branch]}}
        if rest.startswith("pulls?"):
            head = rest.split("head=o:", 1)[1].split("&", 1)[0]
            return [p for p in self.prs if p["head"]["ref"] == head]
        page = int(rest.rsplit("page=", 1)[1]) if "page=" in rest else 1
        if "/check-runs" in rest:
            return {"check_runs": []}
        if "/statuses" in rest:
            sha = rest.split("/")[1]
            return self.statuses.get(sha, []) if page == 1 else []
        if rest.startswith("issues/"):
            return self.comments.get(int(rest.split("/")[1]), []) if page == 1 else []
        raise AssertionError(path)


def test_status_reads_heads_checks_and_markers(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    b = batch.worktree("wt-b", B_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    assert batch.add("B", B_BRANCH, b, "docs/b.md", extra=["--after", "A"]) == 0
    head = _commit(a, "docs/a.md")
    pr = {"number": 41, "head": {"ref": A_BRANCH, "sha": head}, "merged_at": None, "state": "open"}
    api = FakeGitHub(
        {A_BRANCH: head},
        [pr],
        {head: [{"context": "rcm/ci", "state": "success"}]},
        {41: [{"body": f"verified: {head}"}]},
    )
    capsys.readouterr()
    assert batch.run("status", "--json", api=api) == 0
    rows = {r["bundle"]: r for r in json.loads(capsys.readouterr().out)["bundles"]}
    assert rows["A"]["local"] == head and rows["A"]["remote"] == head
    assert (rows["A"]["pr"], rows["A"]["rcm_ci"], rows["A"]["verified"]) == (
        "#41",
        "success",
        "yes",
    )
    assert rows["A"]["next"] == "merge"
    assert rows["B"]["remote"] == "not pushed" and rows["B"]["pr"] == "none"
    pr["merged_at"], pr["state"] = "2026-11-25T00:00:00Z", "closed"
    pr["merge_commit_sha"] = "d" * 40
    assert batch.run("status", "--bundle", "A", api=api) == 0
    assert batch.read()["bundles"]["A"]["merged"] == "d" * 40


def test_status_prints_a_dash_and_exits_3_when_github_does_not_answer(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    capsys.readouterr()
    assert batch.run("status", "--json", api=FakeGitHub({}, down=True)) == 3
    [row] = json.loads(capsys.readouterr().out)["bundles"]
    assert row["remote"] == row["pr"] == row["rcm_ci"] == row["verified"] == "—"
    assert row["merged"] == "—" and row["next"] == "—"
    assert row["local"] == batch.remote.dev
