"""M5o A — 같은 브랜치의 옛 잡을 치우는 **순수 규칙** · 설정 키 · 요약 코드.

명세 `docs/m5o-workplan.md` §2.2 의 규칙 1~8, 시나리오 `docs/m5o-test-scenarios-a.md`.
시계도 DB 도 안 본다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from jobfactory import NOW, job
from remote_ci_monitor.config import ConfigError, parse_preset
from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    CancelInfo,
    Joiner,
    Source,
)
from remote_ci_monitor.core.supersede import (
    SUPERSEDE_MODES,
    SUPERSEDED,
    branch_of,
    successor_of,
    superseded_by,
)


def tree(branch: str | None = "feat/x", tree_hash: str = "aa", repo: str | None = "org/app"):
    return Source(mode="tree", repo=repo, branch=branch, tree_hash=tree_hash, bytes=100)


def ref(name: str | None = "feat/x", sha: str = "a" * 40, repo: str = "app"):
    return Source(mode="git_ref", repo=repo, ref=name, sha=sha, base_sha=sha)


def _with_source(j, source):
    return replace(j, source=source)


def old(id: int = 1, state: str = QUEUED, *, source: Source | None = None, **kw):
    return _with_source(job(id, "gate:full", state, **kw), source or tree(tree_hash="o" * 4))


def new(id: int = 9, *, source: Source | None = None, **kw):
    return _with_source(job(id, "gate:full", QUEUED, **kw), source or tree(tree_hash="n" * 4))


# ── 값 셋 ────────────────────────────────────────────────────────────────────


def test_modes_are_off_waiting_all():
    assert SUPERSEDE_MODES == ("off", "waiting", "all")
    assert SUPERSEDED == "superseded"


# ── A1 · A3 · A13 · A14: 상태와 모드 ──────────────────────────────────────────


def test_all_takes_waiting_and_running_older_jobs():
    olds = [old(1, QUEUED), old(2, UPLOADING), old(3, RUNNING)]
    assert superseded_by(new(), olds, "all") == [1, 2, 3]


def test_waiting_leaves_running_jobs_alone():
    olds = [old(1, QUEUED), old(2, UPLOADING), old(3, RUNNING)]
    assert superseded_by(new(), olds, "waiting") == [1, 2]


def test_off_takes_nothing():
    assert superseded_by(new(), [old(1)], "off") == []


def test_unknown_mode_takes_nothing():
    assert superseded_by(new(), [old(1)], "yes") == []


def test_result_is_sorted_and_ignores_the_new_job_itself():
    n = new(9)
    assert superseded_by(n, [old(5), n, old(2)], "all") == [2, 5]


# ── A18: 이미 취소 중 · 종료 ─────────────────────────────────────────────────


def test_already_cancelling_job_is_not_taken_again():
    cancelling = old(
        1,
        CANCELLING,
        cancel=CancelInfo(requested_at=NOW, by="alice-laptop", kill_at=NOW),
    )
    assert superseded_by(new(), [cancelling], "all") == []


@pytest.mark.parametrize("state", [CANCELLED, SUCCEEDED])
def test_finished_jobs_are_not_taken(state):
    assert superseded_by(new(), [old(1, state)], "all") == []


# ── A5 · A6 · A7 · A8 · A9 · A16 · A31~A34: 규칙 1~8 ─────────────────────────


def test_different_inputs_are_a_different_question():
    o = old(1, inputs={"scope": "full"})
    n = new(inputs={"scope": "fast"})
    assert superseded_by(n, [o], "all") == []


@pytest.mark.parametrize(
    "o_branch, n_branch", [(None, None), ("", ""), (None, "feat/x"), ("feat/x", None), ("  ", "  ")]
)
def test_empty_branch_on_either_side_takes_nothing(o_branch, n_branch):
    o = old(1, source=tree(o_branch, "o1"))
    n = new(source=tree(n_branch, "n1"))
    assert superseded_by(n, [o], "all") == []


def test_other_branch_is_left():
    o = old(1, source=tree("main", "o1"))
    assert superseded_by(new(), [o], "all") == []


def test_tree_and_git_ref_with_the_same_branch_name_are_different_modes():
    o = old(1, source=tree("main", "o1"))
    n = new(source=ref("main"))
    assert superseded_by(n, [o], "all") == []


def test_git_ref_jobs_match_on_ref():
    o = old(1, source=ref("feat/x", "b" * 40))
    n = new(source=ref("feat/x", "c" * 40))
    assert superseded_by(n, [o], "all") == [1]


def test_same_identity_is_a_join_not_a_supersede():
    o = old(1, source=tree(tree_hash="same"))
    n = new(source=tree(tree_hash="same"))
    assert superseded_by(n, [o], "all") == []


def test_other_repo_is_left():
    o = old(1, source=tree(repo="org/other", tree_hash="o1"))
    assert superseded_by(new(), [o], "all") == []


def test_other_preset_is_left():
    o = replace(job(1, "gate-fast", QUEUED, inputs={"scope": "full"}), source=tree(tree_hash="o1"))
    assert superseded_by(new(), [o], "all") == []


def test_other_requester_is_left():
    o = old(1, requester="bob-desk")
    assert superseded_by(new(), [o], "all") == []


def test_a_joiner_from_someone_else_keeps_the_old_job():
    o = old(1, joiners=(Joiner(name="bob-desk", label="bob@desk", joined_at=NOW),))
    assert superseded_by(new(), [o], "all") == []


def test_a_joiner_with_my_own_name_does_not_protect_it():
    o = old(1, joiners=(Joiner(name="alice-laptop", label="alice@other", joined_at=NOW),))
    assert superseded_by(new(), [o], "all") == [1]


def test_a_targeted_job_is_left():
    o = old(1, target_worker="build-02")
    assert superseded_by(new(), [o], "all") == []


def test_a_newer_job_is_never_taken():
    assert superseded_by(new(5), [old(7)], "all") == []


def test_other_pool_is_a_different_question():
    o = old(1, pool="linux")
    assert superseded_by(new(), [o], "all") == []


def test_capable_job_compares_the_pool_it_waited_in_not_where_it_ran():
    """`capable` 잡은 claim 때 `pool` 이 바뀐다 — 기다린 풀(`display_pool`)로 잰다."""
    o = old(1, RUNNING, placement="capable", pool="linux", display_pool="default")
    n = new(placement="capable", display_pool="default")
    assert superseded_by(n, [o], "all") == [1]


# ── 읽는 쪽 — successor_of ────────────────────────────────────────────────────


def test_successor_of_a_superseded_cancelled_job():
    j = old(
        1,
        CANCELLED,
        cancelled_by=SUPERSEDED,
        summary_code="superseded",
        summary_args={"by": 9},
    )
    assert successor_of(j) == 9


def test_successor_of_a_superseded_cancelling_job():
    j = old(
        1,
        CANCELLING,
        cancel=CancelInfo(requested_at=NOW, by=SUPERSEDED, kill_at=NOW),
        summary_code="superseded",
        summary_args={"by": 9},
    )
    assert successor_of(j) == 9


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"cancelled_by": "alice-laptop", "summary_code": "cancelled_by", "summary_args": {"by": 9}},
        {"cancelled_by": SUPERSEDED, "summary_code": "superseded", "summary_args": {"by": "9"}},
        {"cancelled_by": SUPERSEDED, "summary_code": "superseded", "summary_args": {"by": True}},
        {"cancelled_by": SUPERSEDED, "summary_code": "exit_code", "summary_args": {"code": 1}},
    ],
)
def test_successor_of_anything_else_is_none(patch):
    assert successor_of(replace(old(1, CANCELLED), **patch)) is None


def test_branch_of():
    assert branch_of(tree("feat/x")) == "feat/x"
    assert branch_of(ref("main")) == "main"
    assert branch_of(tree(None)) is None
    assert branch_of(tree("   ")) is None


# ── 요약 코드 ────────────────────────────────────────────────────────────────


def test_superseded_summary_code():
    text, code, args = outcome.summary("superseded", by=42)
    assert (text, code, args) == ("superseded by #42", "superseded", {"by": 42})
    assert outcome.render("superseded", {"by": 7}) == "superseded by #7"


# ── A21: 설정 키 ─────────────────────────────────────────────────────────────


def _preset(**extra):
    return parse_preset({"name": "gate", "argv": ["true"], **extra})


def test_preset_supersede_defaults_to_off():
    assert _preset().supersede == "off"


@pytest.mark.parametrize("value", ["off", "waiting", "all"])
def test_preset_supersede_accepts_the_three_values(value):
    assert _preset(supersede=value).supersede == value


@pytest.mark.parametrize("value", ["yes", "ALL", "", True, 1, ["all"]])
def test_preset_supersede_rejects_anything_else(value):
    with pytest.raises(ConfigError) as e:
        _preset(supersede=value)
    msg = str(e.value)
    assert "supersede" in msg and "off" in msg and "waiting" in msg and "all" in msg


# ── 검증 뒤 더한 규칙(§10) ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "o_repo, n_repo", [(None, None), ("", ""), (None, "org/app"), ("org/app", None)]
)
def test_empty_repo_on_either_side_takes_nothing(o_repo, n_repo):
    """`origin` 이 없는 로컬 레포는 repo=None — 무관한 두 레포의 `main` 이 서로를 치우면 안 된다."""
    o = old(1, source=tree(repo=o_repo, tree_hash="o1"))
    n = new(source=tree(repo=n_repo, tree_hash="n1"))
    assert superseded_by(n, [o], "all") == []


def test_a_new_job_aimed_at_one_machine_takes_nothing():
    """규칙 8 을 양쪽에 — `--machine` · 점검 잡으로 낸 새 잡은 다른 질문이다."""
    assert superseded_by(new(target_worker="build-02"), [old(1)], "all") == []


def test_supersede_warnings_only_when_cancel_needs_the_submission_token():
    from remote_ci_monitor.config import (
        ServerConfig,
        supersede_skipped_presets,
        supersede_warnings,
    )

    cfg = ServerConfig()
    cfg.presets = (_preset(supersede="all"), parse_preset({"name": "off", "argv": ["true"]}))
    assert supersede_skipped_presets(cfg) == [] and supersede_warnings(cfg) == []
    cfg.server.cancel_requires_submission_token = True
    assert supersede_skipped_presets(cfg) == ["gate"]
    (line,) = supersede_warnings(cfg)
    assert line.startswith("warning:") and "gate" in line
    assert "cancel_requires_submission_token" in line and "skipped" in line


def test_rcm_check_supersede_row_is_a_warning_not_a_failure():
    from remote_ci_monitor.cli import _supersede_row

    assert _supersede_row({}) is None
    assert _supersede_row({"supersede_skipped_presets": []}) is None
    name, ok, text = _supersede_row({"supersede_skipped_presets": ["gate", "test-narrow"]})
    assert name == "supersede" and ok is None
    assert "gate, test-narrow" in text and "cancel_requires_submission_token" in text
