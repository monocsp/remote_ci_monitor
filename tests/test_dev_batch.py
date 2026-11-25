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

    def add(self, bundle: str, branch: str, wt: Path, *owns: str, extra=(), order=None) -> int:
        order = str(order or len(self.read().get("bundles", {})) + 1)
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


def _green_api(heads: dict, number: int, branch: str, sha: str, **kw) -> tuple[dict, FakeGitHub]:
    """그 브랜치에 PR 하나 · rcm/ci 초록 · `verified:` 표식까지 갖춘 가짜 GitHub."""
    pr = {"number": number, "head": {"ref": branch, "sha": sha}, "merged_at": None}
    pr["state"] = "open"
    statuses = kw.get("statuses", {sha: [{"context": "rcm/ci", "state": "success"}]})
    comments = kw.get("comments", {number: [{"body": f"verified: {sha}"}]})
    return pr, FakeGitHub(heads, [pr], statuses, comments)


def _status_row(batch, capsys, api, bundle="A") -> tuple[int, dict]:
    capsys.readouterr()
    code = batch.run("status", "--json", api=api)
    rows = {r["bundle"]: r for r in json.loads(capsys.readouterr().out)["bundles"]}
    return code, rows[bundle]


@pytest.mark.parametrize(
    "case, want",
    [
        ("unreported", ("not reported", "yes", "wait for rcm/ci")),
        ("pending", ("pending", "yes", "wait for rcm/ci")),
        ("failure", ("failure", "yes", "fix rcm/ci")),
        ("error", ("error", "yes", "fix rcm/ci")),
        ("stale", ("success", "stale", "verify")),
        ("unverified", ("success", "no", "verify")),
    ],
)
def test_status_never_reads_an_unfinished_check_or_an_old_marker_as_done(batch, capsys, case, want):
    a = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    old = _commit(a, "docs/a.md", "one\n")
    head = _commit(a, "docs/a.md", "two\n")
    states = {"unreported": [], "pending": [{"context": "rcm/ci", "state": "pending"}]}
    for word in ("failure", "error"):
        states[word] = [{"context": "rcm/ci", "state": word}]
    ok = [{"context": "rcm/ci", "state": "success"}]
    marks = {"stale": [{"body": f"verified: {old}"}], "unverified": []}
    kw = {"statuses": {head: states.get(case, ok)}}
    kw["comments"] = {41: marks.get(case, [{"body": f"verified: {head}"}])}
    _, api = _green_api({A_BRANCH: head}, 41, A_BRANCH, head, **kw)
    code, row = _status_row(batch, capsys, api)
    assert code == 0, row
    assert (row["rcm_ci"], row["verified"], row["next"]) == want


def test_status_next_step_is_push_then_wait_for_the_bundle_it_follows(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    b = batch.worktree("wt-b", B_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    assert batch.add("B", B_BRANCH, b, "docs/b.md", extra=["--after", "A"]) == 0
    pushed = _commit(b, "docs/b.md", "one\n")
    _, api = _green_api({B_BRANCH: pushed}, 42, B_BRANCH, pushed)
    code, row = _status_row(batch, capsys, api, "B")
    assert code == 0 and row["next"] == "wait for A"
    _commit(b, "docs/b.md", "two\n")  # 로컬이 원격보다 앞선다
    code, row = _status_row(batch, capsys, api, "B")
    assert code == 0 and row["next"] == "push"


def test_a_pr_closed_without_merging_is_not_ready_to_merge(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    head = _commit(a, "docs/a.md")
    pr, api = _green_api({A_BRANCH: head}, 41, A_BRANCH, head)
    pr["state"] = "closed"
    code, row = _status_row(batch, capsys, api)
    assert code == 0 and row["merged"] == "no"
    assert row["next"] == "reopen or open a new PR"


def test_status_says_unknown_when_gh_answers_with_something_that_is_not_json(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0

    def run(cmd, **kw):
        return db.subprocess.CompletedProcess(cmd, 0, "<html>rate limited</html>", "")

    capsys.readouterr()
    assert batch.run("status", "--json", api=db.merge_pr.GhApi(run=run)) == 3
    captured = capsys.readouterr()
    [row] = json.loads(captured.out)["bundles"]
    assert row["remote"] == row["next"] == "—" and "GitHub" in captured.err


@pytest.mark.parametrize("text", ["{not json", "[]", '{"id": "m5x"}'])
@pytest.mark.parametrize("cmd", [["status"], ["scope", "--bundle", "A"], ["add"]])
def test_a_corrupt_state_file_is_unknown_not_a_traceback(batch, capsys, text, cmd):
    batch.state.write_text(text)
    if cmd == ["add"]:
        wt = batch.worktree("wt-a", A_BRANCH)
        cmd = ["add", "--bundle", "A", "--branch", A_BRANCH, "--worktree", str(wt)]
        cmd += ["--owns", "docs/a.md", "--merge-order", "1"]
    capsys.readouterr()
    assert batch.run(*cmd, api=FakeGitHub({})) == 3
    assert "corrupt" in capsys.readouterr().err


def test_scope_reads_the_worktree_status_without_losing_characters(batch, capsys):
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, "docs/a.md", "docs/한 글.md") == 0
    _commit(wt, "docs/a.md")
    (wt / "docs" / "a.md").write_text("unstaged\n")  # porcelain 의 첫 줄이 ` M docs/a.md`
    (wt / "docs" / "한 글.md").write_text("new\n")  # 따옴표로 싸이는 이름
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 0, capsys.readouterr().out
    (wt / "hello.txt").write_text("changed, not committed\n")
    (wt / "notes 1.txt").write_text("untracked\n")
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 1
    out = capsys.readouterr().out
    assert "worktree  hello.txt" in out and "worktree  notes 1.txt" in out
    assert "docs" not in out.split("commits since", 1)[1]


@pytest.mark.parametrize("owns", ["docs/./a.md", "docs//a.md", "./", "docs/."])
def test_add_refuses_empty_and_dot_segments(batch, owns):
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, owns) == 1


def test_overlap_ignores_case_and_shared_may_meet_shared(batch, capsys):
    a = batch.worktree("wt-a", A_BRANCH)
    b = batch.worktree("wt-b", B_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/Sub/a.md", extra=["--shared", "CHANGELOG.md"]) == 0
    assert batch.add("B", B_BRANCH, b, "docs/sub/") == 1
    assert batch.add("B", B_BRANCH, b, "docs/b.md", extra=["--shared", "CHANGELOG.md"]) == 0
    assert batch.read()["bundles"]["A"]["owns"] == ["docs/Sub/a.md"]


def test_add_refuses_a_taken_merge_order_a_taken_branch_and_a_branch_behind_the_base(batch):
    a = batch.worktree("wt-a", A_BRANCH)
    b = batch.worktree("wt-b", B_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    assert batch.add("B", B_BRANCH, b, "docs/b.md", order=1) == 1
    assert batch.add("B", A_BRANCH, a, "docs/b.md") == 1
    old = batch.tmp / "wt-old"
    branch = "feat/web-show-old-badge"
    git("worktree", "add", "-q", "-b", branch, str(old), "origin/main", cwd=batch.clone)
    assert batch.add("C", branch, old, "docs/c.md") == 1
    assert set(batch.read()["bundles"]) == {"A"}
