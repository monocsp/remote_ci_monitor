"""`tools/guard_naming.py` — 브랜치 · 커밋 제목 · PR 제목 규약의 판정(순수 함수만).

막아야 할 것: 무슨 일인지 안 보이는 이름(`fix/cli-ux` · 세션 id · 마일스톤 코드만), 타입이 없는
제목, 마침표로 끝나는 제목. 막으면 안 되는 것: git 이 스스로 만드는 제목(Merge · Revert),
파일에서 읽는 `-F`, 목록·삭제 같은 「만들지 않는」 git 명령, 훅이 못 읽는 모양.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "tools" / "guard_naming.py"
    spec = importlib.util.spec_from_file_location("guard_naming", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()


def bash(command: str):
    return guard.decide("Bash", {"command": command})


# ── 브랜치 이름 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "fix/web-host-card-undefined-local",
        "feat/gc-offline-dry-run-on-copy",
        "docs/m5i-repro-fixes-workplan",
        "chore/naming-convention-skills-guard",
        "test/janitor-shared-bytes-floor",
        "refactor/store-open-modes",
        "perf/status-samples-index",
        "ci/ubuntu-chrome-required",
        "release/v0.2.6",
        "dev",
        "main",
    ],
)
def test_good_branch_names(name):
    assert guard.check_branch(name) is None


@pytest.mark.parametrize(
    "name, why",
    [
        ("fix/cli-ux", "where"),  # 2026-09-10 까지 실제로 있던 이름 — 어디인지만 말한다
        ("worktree-agent-a8b49041eb08c77fe", "hash"),  # 세션이 만든 이름
        ("feat/m5g", "type"),  # 낱말 하나 — 형식부터 안 맞는다
        ("feat/m5g-misc", "where"),  # 코드 + 아무 말도 안 하는 낱말
        ("feature/add-thing", "type"),  # feature 는 타입이 아니다(feat)
        ("Fix/Web-Card", "type"),  # 대문자
        ("fix/web_card_local", "type"),  # 밑줄
        ("fix/web-card-" + "-".join(["word"] * 8), "type"),  # 낱말 8개
        ("fix/extraordinarily-long-descriptive-branch-name-here", "chars"),  # 6 낱말 · 53자
        ("hotfix/web-card", "type"),
        ("revert/web-card-local", "type"),  # revert 는 커밋 타입이지 브랜치 타입이 아니다
    ],
)
def test_bad_branch_names(name, why):
    reason = guard.check_branch(name)
    assert reason is not None, name
    assert why in reason, (name, reason)


# ── 커밋 제목 · PR 제목 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "subject",
    [
        "fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것",
        "feat(gc): 오프라인 dry-run 은 임시 사본 위에서 돈다",
        "docs(m5i): 실배치 재현이 찾은 수정·개선 계획서",
        "chore: 브랜치·커밋·PR 이름 규약과 스킬",
        "feat(store)!: 스키마 v16 — 옛 빌드가 쓴 추론 라벨을 last_step 으로",
        "release: v0.2.6",
        "revert: feat(gc): 오프라인 dry-run 은 임시 사본 위에서 돈다",
        "Merge origin/dev — M5g 와 합친다",
        "Merge pull request #79 from monocsp/fix/cli-ux",
        'Revert "feat(gc): …"',
        "fixup! fix(web): 호스트 카드",
    ],
)
def test_good_subjects(subject):
    assert guard.check_subject(subject) is None


@pytest.mark.parametrize(
    "subject, why",
    [
        ("호스트 카드 고침", "not `<type>"),
        ("fix web card", "not `<type>"),
        ("fix(web):no space", "not `<type>"),
        ("Fix(web): 대문자 타입", "not `<type>"),
        ("fix(Web): 대문자 스코프", "not `<type>"),
        ("fix(web): 마침표로 끝난다.", "period"),
        ("feature(web): feature 는 타입이 아니다", "type 'feature'"),
        ("fix(web): " + "x" * 80, "chars"),
        ("fix(web): 커밋 a8b49041eb08c77fe 을 되돌린다", "hash"),
    ],
)
def test_bad_subjects(subject, why):
    reason = guard.check_subject(subject)
    assert reason is not None, subject
    assert why in reason, (subject, reason)


def test_pr_title_uses_the_same_rule_with_its_own_label():
    reason = guard.check_subject("update stuff", what="PR title")
    assert reason is not None and reason.startswith("PR title")


# ── 명령 읽기 ────────────────────────────────────────────────────────────────


def test_worktree_add_with_bad_name_is_denied():
    v = bash(
        "git fetch origin && git worktree add -b fix/cli-ux ../remote_ci_monitor-cli-ux origin/dev"
    )
    assert v is not None and v.decision == "deny"
    assert "fix/cli-ux" in v.reason and "CONTRIBUTING" in v.reason


def test_worktree_add_with_good_name_passes():
    assert bash("git worktree add -b fix/web-host-card-undefined-local ../x origin/dev") is None


@pytest.mark.parametrize(
    "command",
    [
        "git checkout -b feature/thing",
        "git switch -c feature/thing",
        "git switch --create feature/thing",
        "git branch feature/thing",
        "git branch -m docs/m5i-workplan feature/thing",
        "git -C ../other checkout -b feature/thing",
    ],
)
def test_every_way_of_creating_a_branch_is_checked(command):
    v = bash(command)
    assert v is not None and v.decision == "deny", command


@pytest.mark.parametrize(
    "command",
    [
        "git branch",
        "git branch -a",
        "git branch -vv",
        "git branch -d fix/cli-ux",
        "git branch -D fix/cli-ux",
        "git branch --list 'fix/*'",
        "git checkout dev",
        "git switch dev",
        "git worktree list",
        "git worktree remove ../remote_ci_monitor-cli-ux",
        "git checkout -b --help",
        "git worktree add --help",
    ],
)
def test_commands_that_do_not_create_a_branch_pass(command):
    assert bash(command) is None, command


def test_commit_subject_from_dash_m():
    assert bash('git commit -m "fix(web): 호스트 카드" -m "본문"') is None
    v = bash('git commit -m "호스트 카드 고침"')
    assert v is not None and v.decision == "deny"


def test_commit_subject_from_heredoc():
    command = (
        "git commit -m \"$(cat <<'EOF'\n"
        "fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것\n\n"
        "본문 줄.\n"
        "EOF\n"
        ')"'
    )
    assert bash(command) is None
    bad = command.replace("fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것", "호스트 카드 고침")
    v = bash(bad)
    assert v is not None and v.decision == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git commit -F /tmp/msg.txt",
        "git commit --amend --no-edit",
        "git commit",
        "git commit --help",
        'git merge origin/dev -m "Merge origin/dev — 합친다"',
        'git commit -m "$(some_unknown_command)"',
    ],
)
def test_commits_the_hook_cannot_or_should_not_judge_pass(command):
    assert bash(command) is None, command


def test_pr_create_title_is_checked():
    v = bash('GH_TOKEN=x gh pr create --base dev --title "update stuff" --body "b"')
    assert v is not None and v.decision == "deny" and "PR title" in v.reason
    assert bash('gh pr create --base dev --title "docs(m5i): 계획서" --body "b"') is None
    assert bash('gh pr create --base dev -t "docs(m5i): 계획서" -b "b"') is None


def test_pr_commands_without_a_title_pass():
    assert bash("gh pr create --base dev --fill") is None
    assert bash("gh pr view 80") is None
    assert bash("gh pr list") is None


def test_other_tools_and_empty_commands_pass():
    assert guard.decide("Edit", {"file_path": "x"}) is None
    assert bash("") is None
    assert bash("ls -la") is None


def test_hook_emits_deny_json(capsys, monkeypatch):
    import io
    import json

    payload = {"tool_name": "Bash", "tool_input": {"command": "git checkout -b fix/cli-ux"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["guard_naming.py"])
    assert guard.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_cli_returns_1_with_a_reason(capsys):
    assert guard._cli(["branch", "fix/cli-ux"]) == 1
    assert "no —" in capsys.readouterr().out
    assert guard._cli(["commit", "fix(web): 카드"]) == 0
    assert guard._cli(["pr", "docs(m5i): 계획서"]) == 0
    assert guard._cli(["nope"]) == 2
