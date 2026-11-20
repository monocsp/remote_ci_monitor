"""`tools/merge_pr.py` — 머지 관문 (사건 2 · 6c).

머지는 이 도구로만 한다(`tools/guard_merge.py` 가 다른 길을 막는다). 순서: PR head sha 를 고정 →
그 sha 의 check-runs · statuses 가 전부 끝났고 실패가 없고, base 브랜치 룰셋의 필수 체크
(`rules/branches/{base}` 의 required_status_checks)가 check-run 이든 commit status 든 성공이다 →
PR 댓글에 같은 sha 의 `verified: <sha>` 표식이 있다(`--no-verify "<이유>"` 는 이유를 댓글로
남기고 지나간다) → 충돌이 없다 → `PUT pulls/N/merge` 에 그 sha · `merge_method=merge` ·
`commit_title="<제목> (#N)"` → 머지됐는지 다시 읽는다 → `--delete-branch` 일 때만 원격
브랜치를 지운다.

종료 코드: 0 머지(또는 `--dry-run` 이 머지할 수 있다고 본 것) · 1 거부 · 2 사용법 · 3 네트워크/모름.

GitHub 은 가짜(`FakeGitHub`)다. 마지막 두 시험만 진짜 `gh api` 호출 모양을 PATH 의 가짜 `gh` 로
본다.
"""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "tools" / "merge_pr.py"
    spec = importlib.util.spec_from_file_location("merge_pr", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mp = _load()

REPO = "o/r"
N = 7
SHA = "a" * 40
OTHER = "b" * 40
TITLE = "feat(guard): 머지 관문"


def run_(name="unit (3.11, ubuntu-latest)", status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


GREEN = [run_(), run_("smoke (macos-latest)"), run_("secrets"), run_("test")]
RCM_OK = {"context": "rcm/ci", "state": "success"}


def verified(sha=SHA):
    return {"body": f"격리 검증 끝.\n\nverified: {sha}\n", "user": {"login": "monocsp"}}


class FakeGitHub:
    """GitHub REST 의 이 도구가 쓰는 자리만. `runs` 는 check-runs 를 부를 때마다 하나씩 넘긴다."""

    def __init__(
        self,
        *,
        runs=None,
        statuses=None,
        required=("test",),
        comments=None,
        pr=None,
        heads=None,
        fail=None,
        merge_response=None,
        merged_after_put=True,
    ):
        self.pr = {
            "number": N,
            "title": TITLE,
            "state": "open",
            "merged": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"sha": SHA, "ref": "feat/guard-merge", "repo": {"full_name": REPO}},
            "base": {"ref": "dev", "repo": {"full_name": REPO}},
        }
        self.pr.update(pr or {})
        self.runs = list(runs if runs is not None else [GREEN])
        self.statuses = statuses or []  # 최신이 앞(GitHub 의 commits/{sha}/statuses 순서)
        self.required = list(required) if required is not None else None
        self.comments = comments if comments is not None else [verified()]
        self.heads = list(heads or [])
        self.fail = fail or {}
        self.merge_response = merge_response
        self.merged_after_put = merged_after_put
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, path, fields=None):
        self.calls.append((method, path, fields))
        for (m, prefix), error in self.fail.items():
            if m == method and path.startswith(prefix):
                raise error
        base = f"repos/{REPO}/"
        assert path.startswith(base), path
        rest = path[len(base) :]
        if method == "GET" and rest == f"pulls/{N}":
            if self.heads:
                self.pr["head"]["sha"] = self.heads.pop(0)
            return copy.deepcopy(self.pr)
        if method == "GET" and rest.startswith("commits/") and "/check-runs" in rest:
            runs = self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
            page = int(rest.rsplit("page=", 1)[1])
            chunk = runs[(page - 1) * 100 : page * 100]
            return {"total_count": len(runs), "check_runs": chunk}
        if method == "GET" and rest.startswith("commits/") and "/statuses" in rest:
            page = int(rest.rsplit("page=", 1)[1])
            return self.statuses[(page - 1) * 100 : page * 100]
        if method == "GET" and rest.startswith("rules/branches/"):
            base = rest.split("rules/branches/", 1)[1].split("?", 1)[0]
            assert base == self.pr["base"]["ref"], (base, self.pr["base"])
            if int(rest.rsplit("page=", 1)[1]) > 1 or self.required is None:
                return []
            return [
                {"type": "deletion", "parameters": None},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": False,
                        "required_status_checks": [{"context": c} for c in self.required],
                    },
                },
            ]
        if method == "GET" and rest.startswith(f"issues/{N}/comments"):
            page = int(rest.rsplit("page=", 1)[1])
            return self.comments[(page - 1) * 100 : page * 100]
        if method == "POST" and rest == f"issues/{N}/comments":
            self.comments.append({"body": fields["body"]})
            return {"id": 1}
        if method == "PUT" and rest == f"pulls/{N}/merge":
            if self.merge_response is not None:
                return self.merge_response
            if self.merged_after_put:
                self.pr.update(merged=True, state="closed", merge_commit_sha="c" * 40)
            return {"merged": True, "sha": "c" * 40, "message": "Pull Request successfully merged"}
        if method == "DELETE" and rest.startswith("git/refs/heads/"):
            return None
        raise AssertionError(f"unexpected call {method} {path}")

    def did(self, method, fragment=""):
        return [c for c in self.calls if c[0] == method and fragment in c[1]]


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def merge(api, *args, clock=None):
    clock = clock or Clock()
    return mp.main([str(N), "--repo", REPO, *args], api=api, now=clock.now, sleep=clock.sleep)


# ── 머지되는 길 ──────────────────────────────────────────────────────────────


def test_a_green_verified_pr_is_merged_with_its_pinned_sha_and_title(capsys):
    api = FakeGitHub()
    assert merge(api) == 0
    (put,) = api.did("PUT")
    assert put[1] == f"repos/{REPO}/pulls/{N}/merge"
    assert put[2] == {"sha": SHA, "merge_method": "merge", "commit_title": f"{TITLE} (#{N})"}
    assert api.did("DELETE") == []  # 브랜치는 --delete-branch 일 때만
    out = capsys.readouterr().out
    assert "merged" in out and "c" * 40 in out


def test_delete_branch_removes_the_head_ref_after_the_merge():
    api = FakeGitHub()
    assert merge(api, "--delete-branch") == 0
    (delete,) = api.did("DELETE")
    assert delete[1] == f"repos/{REPO}/git/refs/heads/feat/guard-merge"


def test_a_release_pr_from_dev_never_deletes_dev():
    api = FakeGitHub(pr={"head": {"sha": SHA, "ref": "dev", "repo": {"full_name": REPO}}})
    assert merge(api, "--delete-branch") == 0
    assert api.did("DELETE") == []


def test_a_branch_from_a_fork_is_not_deleted():
    api = FakeGitHub(pr={"head": {"sha": SHA, "ref": "feat/x", "repo": {"full_name": "someone/r"}}})
    assert merge(api, "--delete-branch") == 0
    assert api.did("DELETE") == []


def test_a_required_context_may_come_as_a_status():
    api = FakeGitHub(statuses=[{"context": "ci/other", "state": "success"}], required=["ci/other"])
    assert merge(api) == 0


def test_neutral_and_skipped_runs_are_not_failures():
    runs = [*GREEN, run_("docs", conclusion="skipped"), run_("lint", conclusion="neutral")]
    assert merge(FakeGitHub(runs=[runs])) == 0


def test_more_than_one_page_of_checks_is_read():
    many = [run_(f"shard {i}") for i in range(150)] + [run_("test")]  # test 는 둘째 쪽에 있다
    api = FakeGitHub(runs=[many])
    assert merge(api) == 0
    pages = [c[1] for c in api.did("GET", "/check-runs")]
    assert any("page=2" in p for p in pages), pages


# ── pending: 기다리거나 거부 ─────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["queued", "in_progress", "pending", "waiting", "requested"])
def test_a_pending_required_check_refuses_without_wait(status, capsys):
    runs = [*GREEN[:-1], run_("test", status=status, conclusion=None)]
    api = FakeGitHub(runs=[runs])
    assert merge(api) == 1
    assert api.did("PUT") == []
    err = capsys.readouterr().err
    assert "test" in err and "--wait" in err


def test_a_pending_required_status_refuses_without_wait():
    api = FakeGitHub(statuses=[{"context": "rcm/ci", "state": "pending"}], required=["rcm/ci"])
    assert merge(api) == 1 and api.did("PUT") == []


# ── 필수가 아닌 체크는 보지 않는다 (2026-11-20 오너 결정: GitHub CI 는 머지 조건이 아니다) ──


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_a_pending_check_the_base_does_not_require_does_not_hold_the_merge(status, capsys):
    runs = [run_("unit (macos-latest, 3.13)", status=status, conclusion=None), run_("test")]
    api = FakeGitHub(runs=[runs])
    assert merge(api) == 0 and len(api.did("PUT")) == 1
    assert "ignored 1 check the base does not require" in capsys.readouterr().out


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_failed_check_the_base_does_not_require_does_not_block(conclusion, capsys):
    runs = [run_("unit (ubuntu-latest, 3.11)", conclusion=conclusion), run_("smoke"), run_("test")]
    statuses = [{"context": "ci/other", "state": "failure"}]
    api = FakeGitHub(runs=[runs], statuses=statuses)
    assert merge(api) == 0 and len(api.did("PUT")) == 1
    assert "ignored 3 checks the base does not require" in capsys.readouterr().out


def test_with_rcm_ci_required_github_actions_do_not_matter():
    runs = [run_("test", "in_progress", None), run_("unit", conclusion="failure")]
    api = FakeGitHub(runs=[runs], statuses=[RCM_OK], required=["rcm/ci"])
    assert merge(api) == 0


def test_wait_polls_until_every_check_is_done_then_merges():
    pending = [run_("unit", status="in_progress", conclusion=None), run_("test", "queued", None)]
    api = FakeGitHub(runs=[pending, pending, GREEN])
    clock = Clock()
    assert merge(api, "--wait", "--timeout", "600", clock=clock) == 0
    assert len(clock.slept) == 2 and len(api.did("PUT")) == 1


def test_wait_gives_up_at_the_deadline(capsys):
    pending = [*GREEN[:-1], run_("test", "in_progress", None)]
    api = FakeGitHub(runs=[pending])
    clock = Clock()
    assert merge(api, "--wait", "--timeout", "90", clock=clock) == 1
    assert api.did("PUT") == []
    assert clock.t <= 90 + 1e-9
    assert "test" in capsys.readouterr().err


def test_a_head_that_moves_while_waiting_is_refused(capsys):
    pending = [run_("test", "in_progress", None)]
    api = FakeGitHub(runs=[pending, GREEN], heads=[SHA, OTHER])
    assert merge(api, "--wait", clock=Clock()) == 1
    assert api.did("PUT") == []
    err = capsys.readouterr().err
    assert SHA[:12] in err and OTHER[:12] in err


def test_a_missing_test_check_counts_as_pending_while_waiting():
    without = [run_("unit")]
    api = FakeGitHub(runs=[without, GREEN])
    assert merge(api, "--wait", clock=Clock()) == 0


# ── 실패 · 빠진 체크 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "conclusion", ["failure", "cancelled", "timed_out", "action_required", "startup_failure"]
)
def test_a_failed_required_check_refuses_even_with_wait(conclusion, capsys):
    runs = [*GREEN[:-1], run_("test", conclusion=conclusion)]
    api = FakeGitHub(runs=[runs])
    assert merge(api, "--wait", clock=Clock()) == 1
    assert api.did("PUT") == []
    assert f"test ({conclusion})" in capsys.readouterr().err


@pytest.mark.parametrize("state", ["failure", "error"])
def test_a_failed_required_status_refuses(state):
    api = FakeGitHub(statuses=[{"context": "rcm/ci", "state": state}], required=["rcm/ci"])
    assert merge(api) == 1 and api.did("PUT") == []


# ── 필수 체크는 base 브랜치 룰셋이 정한다 ────────────────────────────────────
#
# 오너 결정(2026-11-20): dev · main 의 필수 체크가 `test`(GitHub Actions) 에서 `rcm/ci`(맥미니 rcm
# 잡이 올리는 커밋 상태)로 바뀐다. 관문은 이름을 박지 않고 룰셋 API 에서 읽는다.


def test_the_required_context_comes_from_the_base_branch_ruleset():
    api = FakeGitHub(runs=[[run_("unit")]], statuses=[RCM_OK], required=["rcm/ci"])
    assert merge(api) == 0
    assert api.did("GET", f"repos/{REPO}/rules/branches/dev")


def test_a_release_pr_reads_the_ruleset_of_main():
    api = FakeGitHub(
        statuses=[RCM_OK],
        required=["test", "rcm/ci"],
        pr={"base": {"ref": "main", "repo": {"full_name": REPO}}},
    )
    assert merge(api) == 0
    assert api.did("GET", f"repos/{REPO}/rules/branches/main")


@pytest.mark.parametrize(
    "runs, statuses, code",
    [
        # 2026-11-20 룰셋: main 은 `main-from-dev-only`(check-run) + `rcm/ci`(commit status)
        ([run_("main-from-dev-only")], [RCM_OK], 0),
        ([run_("main-from-dev-only")], [], 1),  # rcm/ci 가 아직 없다
        ([], [RCM_OK], 1),  # main-from-dev-only 가 아직 없다
        ([run_("main-from-dev-only", conclusion="failure")], [RCM_OK], 1),
    ],
)
def test_the_main_ruleset_mixes_a_check_run_and_a_commit_status(runs, statuses, code):
    api = FakeGitHub(
        runs=[runs],
        statuses=statuses,
        required=["main-from-dev-only", "rcm/ci"],
        pr={"base": {"ref": "main", "repo": {"full_name": REPO}}},
    )
    assert merge(api) == code
    assert bool(api.did("PUT")) == (code == 0)


def test_a_missing_required_status_refuses_and_names_it(capsys):
    api = FakeGitHub(required=["rcm/ci"])  # GitHub Actions 는 초록이지만 rcm/ci 가 없다
    assert merge(api) == 1 and api.did("PUT") == []
    assert "`rcm/ci`" in capsys.readouterr().err


def test_a_missing_required_status_is_waited_for():
    api = FakeGitHub(required=["rcm/ci"])
    clock = Clock()
    later = [RCM_OK]

    def sleep(seconds):
        clock.sleep(seconds)
        api.statuses = later

    assert mp.main([str(N), "--repo", REPO, "--wait"], api=api, now=clock.now, sleep=sleep) == 0


@pytest.mark.parametrize(
    "history, code",
    [
        # 같은 context 는 최신 것만 본다(목록은 최신이 앞)
        ([RCM_OK, {"context": "rcm/ci", "state": "failure"}], 0),
        ([{"context": "rcm/ci", "state": "failure"}, RCM_OK], 1),
        ([{"context": "rcm/ci", "state": "pending"}, RCM_OK], 1),
        ([{"context": "rcm/ci", "state": "error"}], 1),
    ],
)
def test_only_the_latest_status_of_a_context_counts(history, code):
    api = FakeGitHub(statuses=history, required=["rcm/ci"])
    assert merge(api) == code
    assert bool(api.did("PUT")) == (code == 0)


def test_a_required_check_run_that_was_skipped_or_neutral_counts_as_passed():
    api = FakeGitHub(runs=[[run_("rcm/ci", conclusion="neutral")]], required=["rcm/ci"])
    assert merge(api) == 0


def test_a_base_without_required_checks_is_refused(capsys):
    """필수 체크가 없으면 관문이 볼 것이 없다 — 모르면 머지하지 않는다."""
    api = FakeGitHub(required=None)
    assert merge(api) == 1 and api.did("PUT") == []
    assert "no required status checks" in capsys.readouterr().err


def test_the_ruleset_api_failing_is_unknown():
    api = FakeGitHub(fail={("GET", f"repos/{REPO}/rules"): mp.ApiError("HTTP 502", 502)})
    assert merge(api) == 3 and api.did("PUT") == []


def test_no_test_check_refuses(capsys):
    api = FakeGitHub(runs=[[run_("unit"), run_("smoke")]])
    assert merge(api) == 1 and api.did("PUT") == []
    assert "`test`" in capsys.readouterr().err


def test_no_checks_at_all_refuses():
    api = FakeGitHub(runs=[[]])
    assert merge(api) == 1 and api.did("PUT") == []


# ── 격리 검증 표식 ───────────────────────────────────────────────────────────


def test_no_verified_marker_refuses(capsys):
    api = FakeGitHub(comments=[{"body": "LGTM"}])
    assert merge(api) == 1 and api.did("PUT") == []
    err = capsys.readouterr().err
    assert f"verified: {SHA}" in err and "--no-verify" in err


def test_a_marker_for_an_older_head_refuses(capsys):
    api = FakeGitHub(comments=[verified(OTHER)])
    assert merge(api) == 1 and api.did("PUT") == []
    err = capsys.readouterr().err
    assert OTHER[:12] in err and SHA[:12] in err


@pytest.mark.parametrize(
    "body",
    [
        f"verified: {SHA[:12]}",  # 짧은 sha 는 형식이 아니다
        f"Verified: {SHA}",
        f"verified {SHA}",
        f"> verified: {SHA}",  # 인용은 표식이 아니다
        f"not verified: {SHA}",
    ],
)
def test_the_marker_format_is_fixed(body):
    api = FakeGitHub(comments=[{"body": body}])
    assert merge(api) == 1 and api.did("PUT") == []


def test_the_marker_may_sit_on_any_line_of_any_comment():
    api = FakeGitHub(comments=[{"body": "첫 댓글"}] * 120 + [verified()])
    assert merge(api) == 0


def test_no_verify_leaves_its_reason_on_the_pr_and_merges():
    api = FakeGitHub(comments=[])
    assert merge(api, "--no-verify", "docs only — no code changed") == 0
    (post,) = api.did("POST")
    assert "docs only — no code changed" in post[2]["body"] and SHA in post[2]["body"]
    assert len(api.did("PUT")) == 1
    order = [c[0] for c in api.calls]
    assert order.index("POST") < order.index("PUT")


@pytest.mark.parametrize("reason", ["", "   "])
def test_no_verify_needs_a_reason(reason):
    api = FakeGitHub(comments=[])
    assert merge(api, "--no-verify", reason) == 2
    assert api.calls == []


# ── 충돌 · 상태 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fields", [{"mergeable": False, "mergeable_state": "dirty"}, {"mergeable_state": "dirty"}]
)
def test_a_conflicting_pr_is_refused(fields, capsys):
    api = FakeGitHub(pr=fields)
    assert merge(api) == 1 and api.did("PUT") == []
    assert "conflict" in capsys.readouterr().err


def test_an_already_merged_or_closed_pr_is_refused():
    assert merge(FakeGitHub(pr={"merged": True, "state": "closed"})) == 1
    assert merge(FakeGitHub(pr={"state": "closed"})) == 1


def test_github_refusing_the_merge_is_a_refusal(capsys):
    api = FakeGitHub(
        fail={
            ("PUT", f"repos/{REPO}/pulls/{N}/merge"): mp.ApiError("Head branch was modified", 409)
        }
    )
    assert merge(api) == 1
    assert "409" in capsys.readouterr().err


def test_a_merge_that_does_not_read_back_as_merged_is_unknown():
    api = FakeGitHub(merged_after_put=False)
    assert merge(api) == 3


# ── dry-run ──────────────────────────────────────────────────────────────────


def test_dry_run_checks_everything_and_changes_nothing(capsys):
    api = FakeGitHub(comments=[])
    assert merge(api, "--dry-run", "--no-verify", "tests only", "--delete-branch") == 0
    assert [c for c in api.calls if c[0] != "GET"] == []
    assert "would merge" in capsys.readouterr().out


def test_dry_run_still_refuses_what_a_merge_would_refuse():
    api = FakeGitHub(runs=[[run_("test", "queued", None)]])
    assert merge(api, "--dry-run") == 1


# ── 네트워크 · 사용법 ────────────────────────────────────────────────────────


def test_a_network_failure_is_exit_3(capsys):
    api = FakeGitHub(fail={("GET", f"repos/{REPO}/pulls"): mp.ApiError("connection refused")})
    assert merge(api) == 3
    assert "connection refused" in capsys.readouterr().err


def test_a_server_error_is_exit_3():
    api = FakeGitHub(fail={("GET", f"repos/{REPO}/commits"): mp.ApiError("HTTP 502", 502)})
    assert merge(api) == 3


def test_a_missing_pr_is_a_refusal():
    api = FakeGitHub(fail={("GET", f"repos/{REPO}/pulls"): mp.ApiError("Not Found", 404)})
    assert merge(api) == 1


@pytest.mark.parametrize(
    "argv",
    [[], ["seven"], ["0"], ["-3"], ["7", "--repo", "noslash"], ["7", "--timeout", "-1"]],
)
def test_bad_arguments_are_exit_2(argv):
    api = FakeGitHub()
    assert mp.main(argv, api=api) == 2
    assert api.calls == []


def test_the_repo_comes_from_origin_when_not_given():
    assert mp.repo_from_remote("git@github.com:monocsp/remote_ci_monitor.git") == (
        "monocsp/remote_ci_monitor"
    )
    assert mp.repo_from_remote("https://github.com/monocsp/remote_ci_monitor") == (
        "monocsp/remote_ci_monitor"
    )
    assert mp.repo_from_remote("https://x-access-token:t@github.com/o/r.git") == "o/r"
    assert mp.repo_from_remote("/srv/git/r.git") is None


# ── 진짜 `gh api` 호출 모양 (PATH 의 가짜 gh) ────────────────────────────────

FAKE_GH = r"""#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if os.environ.get("FAKE_GH_DOWN"):
    sys.stderr.write("error connecting to api.github.com\n")
    sys.exit(1)
method = args[args.index("-X") + 1]
path = args[args.index("-X") + 2]
sha = "{sha}"
if method == "GET" and path == "repos/o/r/pulls/7":
    merged = os.path.exists(os.environ["FAKE_GH_LOG"] + ".merged")
    print(json.dumps({{"number": 7, "title": "feat(guard): 머지 관문", "state": "open",
        "merged": merged, "merge_commit_sha": "c" * 40, "mergeable": True,
        "mergeable_state": "clean",
        "head": {{"sha": sha, "ref": "feat/x", "repo": {{"full_name": "o/r"}}}}}}))
elif "/check-runs" in path:
    runs = [] if "page=2" in path else [{{"name": "test", "status": "completed",
        "conclusion": "success"}}]
    print(json.dumps({{"total_count": 1, "check_runs": runs}}))
elif "/statuses" in path:
    print(json.dumps([]))
elif "/rules/branches/" in path:
    rules = [{{"type": "required_status_checks", "parameters": {{"required_status_checks":
        [{{"context": "test"}}]}}}}]
    print(json.dumps(rules if path.endswith("page=1") else []))
elif "/comments" in path:
    page = path.rsplit("page=", 1)[1]
    print(json.dumps([{{"body": "verified: " + sha}}] if page == "1" else []))
elif method == "PUT":
    open(os.environ["FAKE_GH_LOG"] + ".merged", "w").close()
    print(json.dumps({{"merged": True, "sha": "c" * 40}}))
else:
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
    sys.exit(1)
"""


def _fake_gh(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable, sha=SHA))
    gh.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_GH_LOG"] = str(tmp_path / "gh.log")
    return env


def test_the_real_tool_calls_gh_api_with_the_pinned_sha(tmp_path):
    env = _fake_gh(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "merge_pr.py"), "7", "--repo", "o/r"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc
    calls = [json.loads(line) for line in (tmp_path / "gh.log").read_text().splitlines()]
    (put,) = [c for c in calls if "PUT" in c]
    assert put[:4] == ["api", "-X", "PUT", "repos/o/r/pulls/7/merge"]
    assert {"sha=" + SHA, "merge_method=merge", "commit_title=feat(guard): 머지 관문 (#7)"} <= set(
        put
    )
    assert "-f" in put


def test_the_real_tool_says_unknown_when_github_cannot_be_reached(tmp_path):
    env = _fake_gh(tmp_path)
    env["FAKE_GH_DOWN"] = "1"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "merge_pr.py"), "7", "--repo", "o/r"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 3, proc
    assert "error connecting" in proc.stderr
