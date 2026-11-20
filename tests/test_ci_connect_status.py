"""M5r N: 알림 훅 `rcm_ci_status.py` — 종료 상태를 커밋 상태로 올린다.

docs/m5r-workplan.md §2.7 · §5.5 · §6 N. 가짜 GitHub 이 받은 상태를 본다. `cancelled` 의 사유는
가짜 rcm 서버(`fake_rcm_http.FakeRcm`)의 `GET /jobs/{id}` 가 말한다.
"""

from __future__ import annotations

import time

import pytest

from ci_fakes import REPO, T0, FakeClock, FakeGitHub, Status, load_templates
from fake_rcm_http import FakeRcm

cfgmod, _wait, _watch, statusmod = load_templates()
SHA = "b" * 40
TOKEN = "gh-watch-token-value"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def gh(clock):
    g = FakeGitHub(clock)
    yield g
    g.close()


@pytest.fixture
def rcm():
    r = FakeRcm()
    yield r
    r.close()


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "gh.token"
    p.write_text(TOKEN + "\n")
    p.chmod(0o600)
    return p


def hook_env(**kw: str) -> dict[str, str]:
    env = {
        "RCM_JOB_ID": "9",
        "RCM_STATE": "succeeded",
        "RCM_EXIT_CODE": "0",
        "RCM_SOURCE_MODE": "git_ref",
        "RCM_SOURCE_SHA": SHA,
        "RCM_SUMMARY": "",
        "RCM_FAILED_STEP": "",
        "RCM_LAST_STEP": "secrets",
        "RCM_URL": "http://build.local:8787/jobs/9",
    }
    env.update(kw)
    return env


def run_hook(gh, rcm, token_file, capsys, *, job_url: bool = False, **env):
    argv = [
        "--github", REPO,
        "--context", "rcm/ci",
        "--token-file", str(token_file),
        "--server", rcm.url,
        "--api-url", gh.url,
    ]  # fmt: skip
    if job_url:
        argv.append("--job-url")
    rc = statusmod.main(argv, env=hook_env(**env))
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def only_post(gh) -> dict:
    posted = gh.posted(SHA)
    assert len(posted) == 1, posted
    return posted[0][1]


# ── N1 · N2: 할 일이 없다 ──


@pytest.mark.parametrize("env", [{"RCM_SOURCE_MODE": "tree"}, {"RCM_SOURCE_SHA": ""}])
def test_n1_a_tree_job_or_a_missing_sha_makes_no_request(gh, rcm, token_file, capsys, env):
    rc, out = run_hook(gh, rcm, token_file, capsys, **env)
    assert rc == 0
    assert gh.requests == []
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 1


def test_n2_a_taken_over_commit_is_left_alone(gh, rcm, token_file, capsys):
    gh.add_status(SHA, Status("pending", "queued · #9", at=T0 - 2000))
    gh.add_status(
        SHA,
        Status(
            "error", "taken-over · GitHub Actions run 1", creator="github-actions[bot]", at=T0 - 100
        ),
    )
    rc, out = run_hook(gh, rcm, token_file, capsys, RCM_STATE="failed", RCM_EXIT_CODE="1")
    assert rc == 0
    assert len(gh.requests) == 1 and gh.requests[0].method == "GET"
    assert "taken-over" in out


# ── N3 ~ N10: §2.7 의 표 ──


def test_n3_succeeded_posts_passed(gh, rcm, token_file, capsys):
    rc, _ = run_hook(gh, rcm, token_file, capsys)
    assert rc == 0
    body = only_post(gh)
    assert (body["state"], body["context"]) == ("success", "rcm/ci")
    assert body["description"].startswith("passed · #9")


def test_n4_exit_75_is_unavailable_infra_at_the_last_step(gh, rcm, token_file, capsys):
    run_hook(
        gh, rcm, token_file, capsys,
        RCM_STATE="failed", RCM_EXIT_CODE="75", RCM_FAILED_STEP="", RCM_LAST_STEP="venv",
    )  # fmt: skip
    body = only_post(gh)
    assert (body["state"], body["description"]) == ("error", "unavailable · #9 · infra at venv")


def test_n5_a_real_failure_posts_failure_with_the_step(gh, rcm, token_file, capsys):
    run_hook(
        gh, rcm, token_file, capsys,
        RCM_STATE="failed", RCM_EXIT_CODE="1", RCM_FAILED_STEP="test",
    )  # fmt: skip
    body = only_post(gh)
    assert (body["state"], body["description"]) == ("failure", "failed · #9 · test")


def test_n6_failed_without_an_exit_code_could_not_prepare(gh, rcm, token_file, capsys):
    run_hook(gh, rcm, token_file, capsys, RCM_STATE="failed", RCM_EXIT_CODE="")
    body = only_post(gh)
    assert (body["state"], body["description"]) == ("error", "unavailable · #9 · could not prepare")


@pytest.mark.parametrize(("state", "text"), [("timed_out", "timed out"), ("lost", "lost")])
def test_n7_n8_timed_out_and_lost_are_unavailable(gh, rcm, token_file, capsys, state, text):
    run_hook(gh, rcm, token_file, capsys, RCM_STATE=state, RCM_EXIT_CODE="")
    body = only_post(gh)
    assert (body["state"], body["description"]) == ("error", f"unavailable · #9 · {text}")


def test_n9_a_superseded_job_names_its_successor(gh, rcm, token_file, capsys):
    rcm.route(
        "GET",
        "/jobs/9",
        {"id": 9, "state": "cancelled", "cancelled_by": "superseded", "superseded_by": 11},
    )
    run_hook(gh, rcm, token_file, capsys, RCM_STATE="cancelled", RCM_EXIT_CODE="")
    body = only_post(gh)
    assert (body["state"], body["description"]) == ("error", "superseded · #9 · by #11")


def test_n10_a_cancel_by_someone_is_unavailable(gh, rcm, token_file, capsys):
    rcm.route(
        "GET",
        "/jobs/9",
        {"id": 9, "state": "cancelled", "cancelled_by": "owner", "superseded_by": None},
    )
    run_hook(gh, rcm, token_file, capsys, RCM_STATE="cancelled", RCM_EXIT_CODE="")
    body = only_post(gh)
    assert (body["state"], body["description"]) == ("error", "unavailable · #9 · cancelled")


# ── N11 ~ N15: 실패와 꼴 ──


def test_n11_a_github_error_exits_1(gh, rcm, token_file, capsys):
    gh.fail_always[f"/repos/{REPO}/statuses/"] = 500
    rc, _ = run_hook(gh, rcm, token_file, capsys)
    assert rc == 1


def test_n12_an_expired_token_exits_1_without_printing_it(gh, rcm, token_file, capsys):
    gh.auth_fail = True
    rc, out = run_hook(gh, rcm, token_file, capsys)
    assert rc == 1
    assert "401" in out
    assert TOKEN not in out


def test_n13_without_a_failed_step_the_last_step_is_named(gh, rcm, token_file, capsys):
    run_hook(
        gh, rcm, token_file, capsys,
        RCM_STATE="failed", RCM_EXIT_CODE="1", RCM_FAILED_STEP="", RCM_LAST_STEP="test",
    )  # fmt: skip
    assert "test" in only_post(gh)["description"]


def test_n14_target_url_only_with_job_url(gh, rcm, token_file, capsys):
    run_hook(gh, rcm, token_file, capsys)
    assert not only_post(gh).get("target_url")
    gh.requests.clear()
    gh.statuses.clear()
    run_hook(gh, rcm, token_file, capsys, job_url=True)
    assert only_post(gh)["target_url"] == "http://build.local:8787/jobs/9"


def test_n15_a_silent_github_is_cut_off(gh, rcm, token_file, capsys, monkeypatch):
    assert cfgmod.HTTP_TIMEOUT == 15
    monkeypatch.setattr(cfgmod, "HTTP_TIMEOUT", 0.3)
    gh.hang.add(f"/repos/{REPO}/commits/")
    started = time.monotonic()
    rc, _ = run_hook(gh, rcm, token_file, capsys)
    assert rc == 1
    assert time.monotonic() - started < 3
