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
import subprocess
import sys
import time
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
     ".claude/", "tools/", "", "DOCS", "Src/Remote_CI_Monitor",
     # 3라운드: 역슬래시는 git 경로에서 구분자가 아니다 — 겹침 검사를 비켜 간다. 제어 · 보이지
     # 않는 글자는 출력에서 다른 경로처럼 보인다
     "docs\\a.md", "docs/a\tb.md", "docs/\u202eevil.md"],
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

    def __init__(self, heads, prs=(), statuses=None, comments=None, down=False, broken=None):
        self.heads, self.prs = heads, list(prs)
        self.statuses, self.comments = statuses or {}, comments or {}
        self.down = down
        #: 자리 하나(branches · pulls · issues · statuses · check-runs)의 답을 망가뜨린다 —
        #: 예외면 던지고, 아니면 그 값을 답으로 준다(None · 목록 아닌 것 · 모르는 상태 값)
        self.broken = broken or {}

    def request(self, method, path, fields=None):
        assert method == "GET" and path.startswith("repos/o/r/"), (method, path)
        if self.down:
            raise ApiError("HTTP 502", 502)
        rest = path[len("repos/o/r/") :]
        kind = next((k for k in ("branches/", "pulls?", "issues/") if rest.startswith(k)), None)
        kind = kind or rest.split("?", 1)[0].rsplit("/", 1)[-1]  # statuses · check-runs
        if kind in self.broken:
            if isinstance(self.broken[kind], Exception):
                raise self.broken[kind]
            return self.broken[kind]
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


@pytest.mark.parametrize("name", ["@src-config.py", "\ndocs/a.md", "docs/\u202ea.md"])
def test_scope_sees_a_committed_path_that_looks_like_a_log_header(batch, capsys, name):
    # `git log --format=@%H` 를 나누던 때는 `@` 로 시작하는 경로를 커밋 머리로, 앞의 줄바꿈을
    # 구분자로 읽어 범위 밖 경로를 놓쳤다(2라운드 F1)
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, "docs/a.md") == 0
    sha = _commit(wt, name)
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 1
    out = capsys.readouterr().out
    # 제어 · 보이지 않는 글자가 든 경로는 repr 로 찍는다 — 줄바꿈이 가짜 줄을 만들지 않게(L8)
    shown = name if name.isprintable() else repr(name)
    assert f"{sha[:12]}  {shown}  (outside" in out and "1 commits since" in out


def _corrupt(batch, **fields) -> None:
    state = batch.read()
    if "_B_after" in fields:
        state["bundles"]["B"]["after"] = fields.pop("_B_after")
    state["bundles"]["A"].update(fields)
    batch.state.write_text(json.dumps(state))


@pytest.mark.parametrize(
    "fields",
    [{"owns": "docs/a.md"}, {"shared": "CHANGELOG.md"}, {"after": "B"}, {"owns": [1]},
     {"after": ["Z"]}, {"merge_order": "1"}, {"branch": None},
     # 3라운드: merged 는 None 이나 40자리 sha 뿐이다 — "no" 가 참으로 읽혀 「done」이 됐다.
     # 자기 · 서로를 가리키는 after 는 영원히 「wait for」다
     {"merged": "no"}, {"merged": "yes"}, {"merged": "c" * 39}, {"merged": 1},
     {"after": ["A"]}, {"after": ["B"], "_B_after": ["A"]}],
)  # fmt: skip
@pytest.mark.parametrize("cmd", [["status"], ["scope", "--bundle", "A"]])
def test_a_state_file_with_a_wrong_shape_is_corrupt(batch, capsys, fields, cmd):
    # 꼴 검사가 없으면 문자열 owns 는 글자마다 경로가 되고, 없는 묶음을 가리키는 after 는
    # status 에서 KeyError 트레이스백이 된다(2라운드 F2 · F12)
    a = batch.worktree("wt-a", A_BRANCH)
    b = batch.worktree("wt-b", B_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    assert batch.add("B", B_BRANCH, b, "docs/b.md") == 0
    head = _commit(a, "docs/a.md")
    _corrupt(batch, **fields)
    _, api = _green_api({A_BRANCH: head}, 41, A_BRANCH, head)
    capsys.readouterr()
    assert batch.run(*cmd, api=api) == 3
    assert "corrupt" in capsys.readouterr().err


# ── 3라운드: 읽기 전에 아는 값을 두지 않는다 · 「못 봤다」를 「없다」로 세지 않는다 ──


def _one_green_bundle(batch) -> tuple[str, dict]:
    a = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, a, "docs/a.md") == 0
    head = _commit(a, "docs/a.md")
    pr, _ = _green_api({A_BRANCH: head}, 41, A_BRANCH, head)
    return head, pr


@pytest.mark.parametrize(
    "broken, cell",
    [({"issues/": ApiError("HTTP 502", 502)}, "verified"), ({"issues/": None}, "verified"),
     ({"issues/": {"message": "x"}}, "verified"), ({"issues/": ["not a comment"]}, "verified"),
     ({"pulls?": None}, "pr"), ({"pulls?": {"message": "x"}}, "pr"),
     ({"statuses": None}, "rcm_ci"), ({"statuses": {"state": "success"}}, "rcm_ci"),
     ({"statuses": [{"context": "rcm/ci", "state": "weird"}]}, "rcm_ci"),
     ({"check-runs": None}, "rcm_ci"), ({"check-runs": {"check_runs": None}}, "rcm_ci"),
     ({"branches/": {"commit": {"sha": "nope"}}}, "remote"),
     # 4라운드: 404 가 아닌 브랜치 읽기 실패는 「not pushed」가 아니다 · 끝난 check-run 의 모르는
     # 결론 · 본문 없는 댓글 · context 없는 상태 · 이름 없는 check-run 은 모름이다(M2 · M4)
     ({"branches/": ApiError("HTTP 502", 502)}, "remote"),
     ({"check-runs": {"check_runs": [
         {"name": "rcm/ci", "status": "completed", "conclusion": "weird"}]}}, "rcm_ci"),
     ({"issues/": [{"user": {"login": "x"}}]}, "verified"),
     ({"issues/": [{"body": 5}]}, "verified"),
     ({"statuses": [{"state": "success"}]}, "rcm_ci"),
     ({"check-runs": {"check_runs": [
         {"status": "completed", "conclusion": "success"}]}}, "rcm_ci")],
)  # fmt: skip
def test_a_read_that_did_not_answer_leaves_a_dash_never_a_known_value(batch, capsys, broken, cell):
    # 표식을 읽기 전에 verified 를 "no" 로 두어, 댓글 읽기가 실패해도 「no · verify · 종료 0」
    # 이었다(H2). 빈 답(None) · 목록 아닌 답 · 모르는 상태 값도 「없음」이 아니라 모름이다
    head, pr = _one_green_bundle(batch)
    api = FakeGitHub({A_BRANCH: head}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                     {41: [{"body": f"verified: {head}"}]}, broken=broken)  # fmt: skip
    code, row = _status_row(batch, capsys, api)
    assert code == 3, row
    assert row[cell] == "—" and row["next"] == "—"
    assert batch.read()["bundles"]["A"]["merged"] is None


@pytest.mark.parametrize("drop", ["state", "merged_at", "number"])
def test_a_pr_missing_a_field_is_unknown_not_closed(batch, capsys, drop):
    head, pr = _one_green_bundle(batch)
    del pr[drop]
    api = FakeGitHub({A_BRANCH: head}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                     {41: [{"body": f"verified: {head}"}]})  # fmt: skip
    code, row = _status_row(batch, capsys, api)
    assert code == 3 and row["pr"] == "—" and row["next"] == "—", row


def test_a_merged_pr_without_a_merge_sha_is_unknown_and_not_saved(batch, capsys):
    head, pr = _one_green_bundle(batch)
    pr.update(merged_at="2026-11-25T00:00:00Z", state="closed", merge_commit_sha=None)
    api = FakeGitHub({A_BRANCH: head}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                     {41: [{"body": f"verified: {head}"}]})  # fmt: skip
    code, row = _status_row(batch, capsys, api)
    assert code == 3 and row["merged"] == "—"
    assert batch.read()["bundles"]["A"]["merged"] is None  # "yes" 를 장부에 남기지 않는다


def test_status_local_is_the_bundle_branch_even_if_the_worktree_moved(batch, capsys):
    head, pr = _one_green_bundle(batch)
    wt = batch.tmp / "wt-a"
    git("switch", "-q", "-c", "feat/web-other-thing", cwd=wt)
    _commit(wt, "docs/elsewhere.md")
    api = FakeGitHub({A_BRANCH: head}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                     {41: [{"body": f"verified: {head}"}]})  # fmt: skip
    code, row = _status_row(batch, capsys, api)
    assert code == 0 and row["local"] == head and row["next"] == "merge", row


def test_scope_sees_a_path_a_merge_commit_changed_itself(batch, capsys):
    # 충돌 해결 · 「악한 병합」은 병합 커밋 자신이 경로를 바꾼다 — --no-merges 만 보면 놓쳤다(M1)
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, "docs/a.md") == 0
    _commit(wt, "docs/a.md")
    git("checkout", "-q", "dev", cwd=batch.remote.work)
    git("pull", "-q", "origin", "dev", cwd=batch.remote.work)
    _commit(batch.remote.work, "web/app.js")
    git("push", "-q", "origin", "dev", cwd=batch.remote.work)
    git("fetch", "-q", "origin", cwd=wt)
    git("merge", "-q", "--no-commit", "origin/dev", cwd=wt)
    (wt / "docs" / "sub").mkdir()
    (wt / "docs" / "sub" / "evil.md").write_text("slipped in by the merge\n")
    git("add", "-A", cwd=wt)
    git("commit", "-q", "--no-edit", cwd=wt)
    merge = git("rev-parse", "HEAD", cwd=wt)
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 1
    out = capsys.readouterr().out
    assert f"{merge[:12]}  docs/sub/evil.md  (outside" in out and "web/app.js" not in out


@pytest.mark.parametrize("move", ["switch", "detach"])
def test_scope_refuses_a_worktree_that_is_not_on_the_bundle_branch(batch, capsys, move):
    # scope 는 워크트리의 HEAD 와 파일을 읽는다 — 다른 브랜치면 남의 답이다(M2)
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, "docs/a.md") == 0
    git(*(["switch", "-q", "-c", "feat/web-other-thing"] if move == "switch" else
          ["switch", "-q", "--detach"]), cwd=wt)  # fmt: skip
    _commit(wt, "README.md")
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 3
    err = capsys.readouterr().err
    assert A_BRANCH in err and ("feat/web-other-thing" in err or "detached" in err)


def test_a_state_file_that_cannot_be_written_is_a_usage_error(tmp_path, monkeypatch, capsys):
    Batch(tmp_path, monkeypatch)
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    argv = ["--batch", str(blocker / "batch.json"), "init", "--id", "m5x", "--repo", "o/r"]
    assert db.main(argv) == 2
    assert "cannot write" in capsys.readouterr().err


@pytest.mark.parametrize("base", ["origin/no-such-branch", "no-such-ref"])
def test_init_with_an_unknown_base_is_a_usage_error(tmp_path, monkeypatch, capsys, base):
    b = Batch(tmp_path, monkeypatch)
    assert b.run("init", "--id", "m5x", "--repo", "o/r", "--base", base) == 2
    assert base in capsys.readouterr().err and not b.state.exists()


# ── 4라운드: 버린 쪽도 PR 의 변경이다 · 남의 쓰기를 덮지 않는다 · 옛 head 의 표식을 쓰지 않는다 ──


def test_scope_sees_paths_a_merge_that_kept_only_our_side_carries(batch, capsys):
    # `merge -s ours` 는 dev 의 변경을 버린다 — 병합 커밋은 첫 부모와 같아 `--cc` 에 아무것도 없지만
    # PR 의 diff 는 dev 의 web/app.js 를 되돌린다(H2)
    wt = batch.worktree("wt-a", A_BRANCH)
    assert batch.add("A", A_BRANCH, wt, "docs/a.md") == 0
    _commit(wt, "docs/a.md")
    git("checkout", "-q", "dev", cwd=batch.remote.work)
    git("pull", "-q", "origin", "dev", cwd=batch.remote.work)
    _commit(batch.remote.work, "web/app.js")
    git("push", "-q", "origin", "dev", cwd=batch.remote.work)
    git("fetch", "-q", "origin", cwd=wt)
    git("merge", "-q", "--no-edit", "-s", "ours", "origin/dev", cwd=wt)
    capsys.readouterr()
    assert batch.run("scope", "--bundle", "A") == 1
    out = capsys.readouterr().out
    assert "merge-result  web/app.js  (outside" in out and "docs/a.md" not in out


@pytest.mark.parametrize("learned", [True, False])
def test_status_keeps_a_bundle_added_while_it_was_reading(batch, capsys, learned):
    # status 는 상태 파일을 통째로 다시 써서 그동안의 add 를 지웠다(M1). 새로 안 머지 sha 가 있을
    # 때만, 잠근 채 다시 읽어 그 칸만 고친다
    head, pr = _one_green_bundle(batch)
    if learned:
        pr.update(merged_at="2026-11-25T00:00:00Z", state="closed", merge_commit_sha="d" * 40)
    b = batch.worktree("wt-b", B_BRANCH)

    class Racing(FakeGitHub):
        def request(self, method, path, fields=None):
            if "/branches/" in path and "B" not in batch.read()["bundles"]:
                state = batch.read()  # 다른 세션의 add 가 그 사이에 쓴다
                state["bundles"]["B"] = dict(
                    state["bundles"]["A"],
                    branch=B_BRANCH,
                    worktree=str(b),
                    owns=["docs/b.md"],
                    merge_order=2,
                )
                batch.state.write_text(json.dumps(state))
            return super().request(method, path, fields)

    api = Racing({A_BRANCH: head}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                 {41: [{"body": f"verified: {head}"}]})  # fmt: skip
    capsys.readouterr()
    assert batch.run("status", "--bundle", "A", api=api) == 0
    bundles = batch.read()["bundles"]
    assert set(bundles) == {"A", "B"}
    assert bundles["A"]["merged"] == ("d" * 40 if learned else None)


def test_an_open_pr_behind_the_pushed_head_waits_and_ignores_the_old_marks(batch, capsys):
    # PR 의 head 가 아직 옛 sha 면 그 sha 의 rcm/ci · verified: 는 지금 브랜치의 것이 아니다(M3)
    head, pr = _one_green_bundle(batch)
    new = _commit(batch.tmp / "wt-a", "docs/a.md", "two\n")
    api = FakeGitHub({A_BRANCH: new}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                     {41: [{"body": f"verified: {head}"}]})  # fmt: skip
    code, row = _status_row(batch, capsys, api)
    assert code == 0 and row["next"] == "wait for PR head", row
    assert row["rcm_ci"] != "success" and row["verified"] != "yes"


@pytest.mark.parametrize("merged_at", [0, 1, ["2026-11-25"]])
def test_a_merged_at_that_is_not_a_string_is_unknown(batch, capsys, merged_at):
    head, pr = _one_green_bundle(batch)
    pr.update(merged_at=merged_at, state="closed", merge_commit_sha="d" * 40)
    api = FakeGitHub({A_BRANCH: head}, [pr], {head: [{"context": "rcm/ci", "state": "success"}]},
                     {41: [{"body": f"verified: {head}"}]})  # fmt: skip
    code, row = _status_row(batch, capsys, api)
    assert code == 3 and row["pr"] == "—", row
    assert batch.read()["bundles"]["A"]["merged"] is None


def test_add_refuses_a_folder_inside_the_worktree(batch, capsys):
    # 하위 폴더도 git 은 그 워크트리로 읽지만, scope · status 는 이 경로를 워크트리로 쓴다(L6)
    wt = batch.worktree("wt-a", A_BRANCH)
    (wt / "docs").mkdir()
    assert batch.add("A", A_BRANCH, wt / "docs", "docs/a.md") == 1
    assert "top level" in capsys.readouterr().err and batch.read()["bundles"] == {}


DRIVER = """import importlib.util, pathlib, sys, time
spec = importlib.util.spec_from_file_location("dev_batch", sys.argv[1])
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
real = db._load
gate = pathlib.Path(sys.argv[2])  # 모두 import 를 마친 뒤 한꺼번에 출발한다
(gate / f"ready-{sys.argv[3]}").touch()
while not (gate / "go").exists():
    time.sleep(0.01)


def slow(path):  # 읽고 나서 쓰기 전까지를 늘린다 — 잠금이 없으면 서로의 쓰기를 덮는다
    state = real(path)
    time.sleep(0.3)
    return state


db._load = slow
sys.exit(db.main(sys.argv[4:]))
"""


def test_concurrent_adds_keep_every_bundle(batch, tmp_path):
    # 여러 세션이 동시에 add 해도 묶음이 모두 남는다 — 읽고-고치고-쓰기가 잠금 안이다(5라운드 M4)
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER)
    gate = tmp_path / "gate"
    gate.mkdir()
    names = "abcd"
    worktrees = {n: batch.worktree(f"wt-{n}", f"feat/queue-add-retry-limit-{n}") for n in names}
    procs = []
    for order, n in enumerate(names, 1):
        argv = ["--batch", str(batch.state), "add", "--bundle", n.upper(), "--branch"]
        argv += [f"feat/queue-add-retry-limit-{n}", "--worktree", str(worktrees[n])]
        argv += ["--owns", f"docs/{n}.md", "--merge-order", str(order)]
        cmd = [sys.executable, str(driver), str(ROOT / "tools" / "dev_batch.py"), str(gate), n]
        procs.append(
            subprocess.Popen([*cmd, *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        )
    deadline = time.monotonic() + 30
    while len(list(gate.glob("ready-*"))) < len(names) and time.monotonic() < deadline:
        time.sleep(0.01)
    (gate / "go").touch()
    results = [(p.wait(timeout=60), p.stderr.read().decode()) for p in procs]
    for p in procs:
        p.stdout.close()
        p.stderr.close()
    assert [code for code, _ in results] == [0] * len(names), results
    assert set(batch.read()["bundles"]) == set(names.upper())
