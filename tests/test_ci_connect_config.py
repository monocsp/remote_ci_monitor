"""M5r C: `.rcm/ci.toml` 파서와 모드 결정(`resolve`) — docs/m5r-workplan.md §2.2 · §2.3 · §6 C.

대기 잡의 `route` 와 감시 스크립트가 같은 `resolve()` 를 쓴다. 여기서는 `route` 를 실제로 불러
`GITHUB_OUTPUT` 을 보고(C1~C5 · C8 · C11 · C14), 파서와 상태 문장 함수는 직접 부른다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci_fakes import (
    HEAD_SHA,
    RCM_REPO_CI_TOML,
    actions_env,
    load_templates,
    outputs,
    pr_event,
)

cfgmod, waitmod, _watch, _status = load_templates()
LEGS = ("ubuntu", "macos")


def route(tmp_path: Path, *, toml: str | None = RCM_REPO_CI_TOML, capsys=None, **kw) -> dict:
    """`rcm_ci_wait.py route --legs ubuntu,macos` 를 부르고 출력 칸을 돌려준다."""
    cfg = tmp_path / "repo" / ".rcm" / "ci.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    if toml is not None:
        cfg.write_text(toml)
    env = actions_env(tmp_path / "gh", **kw)
    argv = ["route", "--legs", ",".join(LEGS), "--config", str(cfg)]
    if kw.get("event") == "schedule":
        argv += ["--schedule-ref", "dev"]
    rc = waitmod.main(argv, env=env)
    assert rc == 0
    return outputs(env)


# ── C1 · C2 · C3 · C4 · C5 ──


@pytest.mark.parametrize("base", ["dev", "main"])
def test_c1_the_rcm_repo_file_waits_for_rcm_and_falls_back_to_ubuntu_only(tmp_path, base):
    out = route(tmp_path, payload=pr_event(base))
    assert out["mode"] == "rcm"
    assert out["wait"] == "true"
    assert out["now"] == ""
    assert out["late"] == "ubuntu"  # macos 는 rcm-only — 대체 때도 GitHub 에서 돌지 않는다
    assert out["why"] == "default"
    assert out["context"] == "rcm/ci"
    assert out["sha"] == HEAD_SHA  # merge 커밋이 아니라 head


@pytest.mark.parametrize("override", [None, "rcm"])
def test_c2_schedule_runs_every_leg_on_github_against_dev(tmp_path, override):
    out = route(tmp_path, event="schedule", payload={}, mode=override, ref_name="main")
    assert out["mode"] == "github"
    assert out["wait"] == "false"
    assert out["now"] == "ubuntu,macos"
    assert out["late"] == ""
    assert out["ref"] == "dev"
    assert out["why"] == "schedule"


def test_c3_a_fork_pr_goes_to_github(tmp_path):
    out = route(tmp_path, payload=pr_event(head_repo="mallory/remote_ci_monitor"))
    assert (out["mode"], out["why"], out["wait"]) == ("github", "fork", "false")
    assert out["now"] == "ubuntu,macos"


def test_c4_the_repository_variable_overrides_the_file(tmp_path):
    out = route(tmp_path, mode="github")
    assert (out["mode"], out["why"]) == ("github", "override")
    assert out["now"] == "ubuntu,macos"  # rcm-only 갈래도 GitHub 에서 돈다


def test_c4_an_unknown_variable_value_is_ignored_with_a_warning(tmp_path, capsys):
    out = route(tmp_path, mode="lates")
    assert (out["mode"], out["why"]) == ("rcm", "default")
    printed = capsys.readouterr().out
    assert "::warning::" in printed and "RCM_CI_MODE" in printed and "lates" in printed


def test_c5_no_file_means_github(tmp_path):
    out = route(tmp_path, toml=None)
    assert (out["mode"], out["why"], out["now"]) == ("github", "no_file", "ubuntu,macos")


# ── C6 · C7: 틀린 파일은 github, 문장에 표와 키 ──

BAD = {
    "unknown_key": (
        RCM_REPO_CI_TOML.replace("[rcm]\n", "[rcm]\ncolour = 1\n"),
        ("[rcm]", "colour"),
    ),
    "version_2": (RCM_REPO_CI_TOML.replace("version = 1", "version = 2"), ("version", "newer")),
    "mode_local": (RCM_REPO_CI_TOML.replace('mode = "rcm"', 'mode = "local"'), ("mode", "local")),
    "pickup_5": (
        RCM_REPO_CI_TOML.replace("pickup_seconds = 300", "pickup_seconds = 5"),
        ("[rcm]", "pickup_seconds"),
    ),
    "platform_windows": (RCM_REPO_CI_TOML + 'windows = "rcm"\n', ("[platforms]", "windows")),
    "left_branches": (
        RCM_REPO_CI_TOML.replace(
            "[rcm]\n", '[[branches]]\nbase = "main"\nmode = "both"\n\n[rcm]\n'
        ),
        ("branches",),
    ),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_c6_a_broken_file_runs_on_github_and_names_the_table_and_key(tmp_path, case, capsys):
    text, needles = BAD[case]
    with pytest.raises(cfgmod.CiConfigError) as err:
        cfgmod.parse(text, legs=LEGS)
    for n in needles:
        assert n in str(err.value), (case, str(err.value))
    r = cfgmod.resolve(
        None,
        event="pull_request",
        base="dev",
        fork=False,
        override=None,
        legs=LEGS,
        error=str(err.value),
    )
    assert (r.mode, r.why, r.wait) == ("github", "bad_file", False)
    assert r.now == LEGS and r.late == ()
    assert r.error == str(err.value)
    out = route(tmp_path, toml=text)
    assert (out["mode"], out["why"]) == ("github", "bad_file")
    assert "::warning::" in capsys.readouterr().out


def test_c7_rcm_mode_without_an_rcm_table_is_a_broken_file():
    with pytest.raises(cfgmod.CiConfigError) as err:
        cfgmod.parse('version = 1\nmode = "rcm"\n', legs=LEGS)
    assert "[rcm]" in str(err.value)


def test_c7_github_fallback_with_only_rcm_only_legs_says_fallback_none():
    text = RCM_REPO_CI_TOML.replace('ubuntu = "rcm"', 'ubuntu = "rcm-only"')
    with pytest.raises(cfgmod.CiConfigError) as err:
        cfgmod.parse(text, legs=LEGS)
    assert 'fallback = "none"' in str(err.value)


# ── C8 · C9 · C10 · C11 · C14 ──


def test_c8_a_push_to_a_branch_outside_the_push_list_runs_on_github(tmp_path):
    out = route(tmp_path, event="push", payload={}, ref_name="main")
    assert (out["mode"], out["why"]) == ("github", "push_not_listed")
    out = route(tmp_path / "dev", event="push", payload={}, ref_name="dev")
    assert (out["mode"], out["why"], out["late"]) == ("rcm", "default", "ubuntu")


def test_c9_a_github_leg_runs_now_and_an_rcm_leg_falls_back_late():
    text = RCM_REPO_CI_TOML.replace('ubuntu = "rcm"', 'ubuntu = "github"').replace(
        'macos = "rcm-only"', 'macos = "rcm"'
    )
    cfg = cfgmod.parse(text, legs=LEGS)
    r = cfgmod.resolve(cfg, event="pull_request", base="dev", fork=False, override=None, legs=LEGS)
    assert (r.now, r.late, r.wait) == (("ubuntu",), ("macos",), True)


def test_c10_fallback_none_leaves_late_empty():
    text = RCM_REPO_CI_TOML.replace('fallback = "github"', 'fallback = "none"')
    cfg = cfgmod.parse(text, legs=LEGS)
    r = cfgmod.resolve(cfg, event="pull_request", base="dev", fork=False, override=None, legs=LEGS)
    assert (r.mode, r.late, r.fallback) == ("rcm", (), "none")


@pytest.mark.parametrize(
    "payload",
    [pr_event(head_repo=None), pr_event(author="dependabot[bot]")],
    ids=["deleted_fork", "dependabot"],
)
def test_c11_a_null_head_repo_or_dependabot_is_treated_as_a_fork(tmp_path, payload):
    out = route(tmp_path, payload=payload)
    assert (out["mode"], out["why"]) == ("github", "fork")


def test_c11_is_fork_directly():
    base = "monocsp/remote_ci_monitor"
    assert cfgmod.is_fork(base, None, "monocsp")
    assert cfgmod.is_fork(base, "mallory/remote_ci_monitor", "mallory")
    assert cfgmod.is_fork(base, base, "dependabot[bot]")
    assert not cfgmod.is_fork(base, base, "monocsp")


def test_c14_an_empty_repository_variable_is_no_override(tmp_path, capsys):
    out = route(tmp_path, mode="")
    assert (out["mode"], out["why"]) == ("rcm", "default")
    assert "::warning::" not in capsys.readouterr().out


# ── C12: 상태 문장 ──


def test_c12_describe_cuts_at_140_and_read_word_reads_it_back():
    d = cfgmod.describe("queued", 12, "x" * 300)
    assert len(d) == cfgmod.DESCRIPTION_LIMIT == 140
    assert d.startswith("queued · #12 · x")
    assert cfgmod.read_word(d) == ("queued", 12)
    bare = cfgmod.describe("unavailable", None, "server_unreachable")
    assert bare == "unavailable · server_unreachable"
    assert cfgmod.read_word(bare) == ("unavailable", None)
    assert cfgmod.describe("passed", 3, "") == "passed · #3"
    dirty = cfgmod.describe("failed", 4, "a\x00b\nc\x1b[31m")
    assert "\x00" not in dirty and "\n" not in dirty and "\x1b" not in dirty
    assert cfgmod.read_word(dirty) == ("failed", 4)
    assert cfgmod.read_word(None) == (None, None)
    assert cfgmod.read_word("") == (None, None)


# ── C13: 종료 대응표(§2.7 의 열 줄) ──

TABLE = [
    # (state, exit, cancelled_by, step, successor) → (state, description)
    (("succeeded", 0, None, None, None, "12 passed"), ("success", "passed · #9 · 12 passed")),
    (("failed", 75, None, "venv", None, ""), ("error", "unavailable · #9 · infra at venv")),
    (("failed", 1, None, "test", None, "1 failed"), ("failure", "failed · #9 · test")),
    (("failed", None, None, None, None, ""), ("error", "unavailable · #9 · could not prepare")),
    (("timed_out", None, None, "test", None, ""), ("error", "unavailable · #9 · timed out")),
    (("lost", None, None, None, None, ""), ("error", "unavailable · #9 · lost")),
    (("cancelled", None, "superseded", None, 11, ""), ("error", "superseded · #9 · by #11")),
    (("cancelled", None, "owner", None, None, ""), ("error", "unavailable · #9 · cancelled")),
]


@pytest.mark.parametrize("row", TABLE, ids=lambda r: f"{r[0][0]}-{r[0][1]}-{r[0][2]}")
def test_c13_terminal_status_follows_the_table(row):
    (state, code, by, step, succ, summary), want = row
    got = cfgmod.terminal_status(state, code, by, job=9, summary=summary, step=step, successor=succ)
    assert got == want


def test_c13_exit_75_is_never_a_failure_and_names_the_step():
    state, desc = cfgmod.terminal_status(
        "failed", cfgmod.EX_TEMPFAIL, None, job=1, summary="", step="isolate", successor=None
    )
    assert state == "error" and desc.startswith("unavailable") and "isolate" in desc


def test_c13_a_non_terminal_state_maps_to_nothing():
    for s in ("queued", "running", "uploading"):
        assert (
            cfgmod.terminal_status(s, None, None, job=1, summary="", step=None, successor=None)
            is None
        )
