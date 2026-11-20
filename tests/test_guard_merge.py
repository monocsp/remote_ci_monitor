"""`tools/guard_merge.py` — PR 머지는 `tools/merge_pr.py` 한 길로만 (사건 2 · 6c).

2026-11-20: 백그라운드 감시 스크립트가 30분 대기 끝에 아직 pending 인 체크가 남았는데
`gh api -X PUT …/pulls/N/merge` 로 머지를 시도했고 두 번은 실제로 머지됐다. 룰셋은 필수 체크만
보장한다. 격리 재검증 없이 머지된 일도 세 번 있었다(#213 · #216 · dolomood #992).

막아야 할 것: 명령 글자나 쓰려는 스크립트 안의 `gh pr merge`, `gh api … PUT … pulls/<N>/merge`
(옵션 순서 · `--method PUT` · `-XPUT` 무관), `curl … -X PUT …/pulls/<N>/merge`.
막으면 안 되는 것: `tools/merge_pr.py` 를 부르는 명령, 머지 이야기를 하는 커밋 · PR 본문, 문서,
검색(`grep`), 머지가 아닌 REST 호출.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "tools" / "guard_merge.py"
    spec = importlib.util.spec_from_file_location("guard_merge", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load()


def bash(command: str):
    return guard.decide("Bash", {"command": command})


# ── 명령: 막는 것 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 233",
        "gh pr merge 233 --merge --delete-branch",
        "GH_TOKEN=$(gh auth token --user monocsp) gh pr merge 233 --squash",
        "gh -R monocsp/remote_ci_monitor pr merge 233",
        "gh api -X PUT repos/monocsp/remote_ci_monitor/pulls/233/merge -f merge_method=merge",
        "gh api repos/monocsp/remote_ci_monitor/pulls/233/merge -X PUT -f merge_method=merge",
        "gh api --method PUT repos/o/r/pulls/233/merge",
        "gh api --method=PUT repos/o/r/pulls/233/merge",
        "gh api -XPUT repos/o/r/pulls/233/merge",
        'gh api -X "PUT" "repos/o/r/pulls/$N/merge"',
        "GH_TOKEN=$TOK gh api -X PUT repos/o/r/pulls/${PR}/merge -f commit_title='x (#5)'",
        "gh api -X PUT repos/o/r/pulls/233/merge \\\n  -f merge_method=merge \\\n  -f sha=abc",
        "gh api \\\n  repos/o/r/pulls/233/merge \\\n  -X PUT",
        "curl -X PUT -H 'Authorization: token x' https://api.github.com/repos/o/r/pulls/233/merge",
        "curl --request PUT https://api.github.com/repos/o/r/pulls/233/merge -d '{}'",
        "curl -sS -XPUT https://api.github.com/repos/o/r/pulls/233/merge",
        "sleep 1800; gh api -X PUT repos/o/r/pulls/233/merge",
        "gh pr checks 233 --watch && gh pr merge 233",
        "bash -c 'gh pr merge 233'",
        'gh api graphql -f query=\'mutation { mergePullRequest(input: {pullRequestId: "x"}) '
        "{ clientMutationId } }'",
        "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { x } }'",
        "nohup sh -c 'gh api -X PUT repos/o/r/pulls/233/merge' &",
        "cat > /tmp/watch.sh <<'EOF'\nwhile true; do sleep 60; done\n"
        "gh api -X PUT repos/o/r/pulls/$1/merge\nEOF",
        "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['gh', 'pr', 'merge', '233'])\nEOF",
    ],
)
def test_a_merge_outside_merge_pr_is_denied(command):
    v = bash(command)
    assert v is not None, command
    assert "tools/merge_pr.py" in v.reason, v.reason


@pytest.mark.parametrize(
    "command",
    [
        "gh pr -R o/r merge 7",
        "gh pr --repo o/r merge 7",
        "gh pr --repo=o/r merge 7 --merge",
        "gh -R o/r pr --repo o/r merge 7",
        "GH_TOKEN=$(gh auth token --user monocsp) gh pr -R monocsp/remote_ci_monitor merge 7",
    ],
)
def test_a_repo_option_after_pr_does_not_hide_the_merge(command):
    """#237 격리 검증: `-R` 이 `pr` 뒤에 오면(`gh pr -R o/r merge 7`) 지나갔다 — gh 는 두 자리를
    다 받는다."""
    v = bash(command)
    assert v is not None, command
    assert "python3 tools/merge_pr.py 7" in v.reason, v.reason


@pytest.mark.parametrize(
    "command",
    [
        "gh pr -R o/r comment 7 --body 'gh pr merge 7 는 막혔습니다'",
        "gh pr --repo o/r view 7",
        "gh pr --repo=o/r edit 7 --body 'gh api -X PUT repos/o/r/pulls/7/merge 를 막는다'",
    ],
)
def test_a_repo_option_after_pr_still_lets_the_text_commands_through(command):
    assert bash(command) is None, command


def test_the_reason_names_the_pr_number_when_it_can():
    v = bash("gh api -X PUT repos/o/r/pulls/233/merge")
    assert v is not None and "python3 tools/merge_pr.py 233" in v.reason
    v = bash('gh pr merge "$N"')
    assert v is not None and "python3 tools/merge_pr.py <N>" in v.reason


# ── 명령: 지나가는 것 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "python3 tools/merge_pr.py 233",
        "GH_TOKEN=$(gh auth token --user monocsp) python3 tools/merge_pr.py 233 --wait",
        "./.venv/bin/python tools/merge_pr.py 233 --no-verify 'docs only — gh pr merge is gone'",
        "python3 /abs/remote_ci_monitor-x/tools/merge_pr.py 233 --dry-run",
        "gh pr view 233",
        "gh pr checks 233 --watch --fail-fast",
        "gh api repos/o/r/pulls/233",
        "gh api repos/o/r/pulls/233/merge",  # GET — 머지됐는지 묻는다
        "gh api -X DELETE repos/o/r/git/refs/heads/feat/x",
        "gh api -X PUT repos/o/r/pulls/233/update-branch",
        "curl https://api.github.com/repos/o/r/pulls/233/merge",
        "grep -rn 'gh pr merge' .claude",
        "rg 'pulls/.*/merge' tools",
        'git commit -m "docs(pr): gh pr merge 대신 merge_pr.py 를 쓴다"',
        "git commit -m \"$(cat <<'EOF'\nfeat(guard): 머지 관문\n\n"
        '`gh api -X PUT …/pulls/N/merge` 를 막는다.\nEOF\n)"',
        "git commit -F - <<'EOF'\nfeat(guard): 머지 관문\n\ngh pr merge 233 은 이제 막힌다.\nEOF",
        "gh pr create --base dev --title 'feat(guard): 머지 관문' --body \"$(cat <<'EOF'\n"
        '이 PR 이 머지된 뒤로는 gh api -X PUT repos/o/r/pulls/N/merge 가 막힙니다.\nEOF\n)"',
        "cat > /tmp/s/pr-body-feat-x.md <<'EOF'\n머지는 gh pr merge 가 아니라 merge_pr.py 로.\nEOF",
        "tee -a notes.txt <<'EOF'\ngh api -X PUT repos/o/r/pulls/7/merge 는 막힌다\nEOF",
        "gh pr comment 233 --body 'verified: 0123456789abcdef0123456789abcdef01234567'",
        "gh pr comment 233 --body 'gh pr merge 를 쓰지 마세요'",
        "echo 'run python3 tools/merge_pr.py 233'",
        "ls -la",
        "",
    ],
)
def test_merge_pr_and_talk_about_merging_pass(command):
    assert bash(command) is None, command


def test_a_merge_after_a_merge_pr_call_is_still_denied():
    v = bash("python3 tools/merge_pr.py 233 --dry-run && gh pr merge 233")
    assert v is not None


# ── 파일: 머지하는 스크립트를 쓰는 것 ────────────────────────────────────────


def write(path: str, content: str):
    return guard.decide("Write", {"file_path": path, "content": content})


def edit(path: str, new: str):
    return guard.decide("Edit", {"file_path": path, "old_string": "x", "new_string": new})


@pytest.mark.parametrize(
    "path, content",
    [
        ("/tmp/s/watch-pr.sh", "#!/bin/sh\nsleep 1800\ngh api -X PUT repos/o/r/pulls/$1/merge\n"),
        ("/tmp/s/watch.sh", "gh pr checks $1 --watch\ngh pr merge $1 --merge\n"),
        (
            "/tmp/s/merge.py",
            'subprocess.run(["gh", "api", "-X", "PUT", f"repos/{r}/pulls/{n}/merge"])\n',
        ),
        ("/tmp/s/merge2.py", "subprocess.run(['gh', 'pr', 'merge', str(n)])\n"),
        ("/tmp/s/curl.sh", "curl -X PUT \\\n  https://api.github.com/repos/o/r/pulls/7/merge\n"),
        ("/tmp/s/noext", "gh api --method PUT repos/o/r/pulls/7/merge\n"),
    ],
)
def test_writing_a_script_that_merges_is_denied(path, content):
    v = write(path, content)
    assert v is not None and "tools/merge_pr.py" in v.reason, (path, content)
    v = edit(path, content)
    assert v is not None, (path, content)


@pytest.mark.parametrize(
    "path, content",
    [
        # 문서는 머지를 이야기한다
        (
            ".claude/skills/pr/SKILL.md",
            "Never `gh pr merge`; `gh api -X PUT …/pulls/N/merge` is blocked.\n",
        ),
        ("/tmp/s/pr-body-feat-x.md", "gh api -X PUT repos/o/r/pulls/233/merge 를 막습니다.\n"),
        ("/tmp/s/notes.txt", "gh pr merge 233\n"),
        # 관문 자신과 그 시험
        (
            "/repo/tools/merge_pr.py",
            'api.request("PUT", f"repos/{repo}/pulls/{n}/merge", fields)\n',
        ),
        ("/repo/tools/guard_merge.py", "# gh pr merge · gh api -X PUT …/pulls/N/merge\n"),
        ("/repo/tests/test_guard_merge.py", '"gh pr merge 233",\n'),
        ("/repo/tests/test_merge_pr.py", '"gh api -X PUT repos/o/r/pulls/7/merge"\n'),
        ("/repo/scripts/mutcheck.py", "old='gh pr merge'\n"),
        # 주석은 실행되지 않는다
        ("/tmp/s/watch.sh", "# do not run gh pr merge here\nsleep 60\n"),
        # 머지하지 않는 스크립트
        ("/tmp/s/watch.sh", "gh pr checks $1 --watch\npython3 tools/merge_pr.py $1 --wait\n"),
    ],
)
def test_documents_the_gate_itself_and_harmless_scripts_pass(path, content):
    assert write(path, content) is None, (path, content)


def test_other_tools_pass():
    assert guard.decide("Read", {"file_path": "/tmp/s/watch.sh"}) is None
    assert guard.decide("Write", {"file_path": "/tmp/s/x.sh"}) is None
    assert guard.decide("Bash", {}) is None


# ── 진짜 훅: JSON stdin → 종료 코드 2 · stderr ───────────────────────────────


def hook(payload: object, raw: str | None = None):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "guard_merge.py")],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_the_real_hook_blocks_with_exit_2_and_says_why_on_stderr():
    proc = hook(
        {"tool_name": "Bash", "tool_input": {"command": "gh api -X PUT repos/o/r/pulls/233/merge"}}
    )
    assert proc.returncode == 2, proc
    assert "python3 tools/merge_pr.py 233" in proc.stderr and proc.stdout == "", proc


def test_the_real_hook_blocks_a_merging_script_write():
    proc = hook(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/s/watch.sh", "content": "gh pr merge 233\n"},
        }
    )
    assert proc.returncode == 2 and "tools/merge_pr.py" in proc.stderr, proc


def test_the_real_hook_is_silent_when_there_is_nothing_to_judge():
    proc = hook({"tool_name": "Bash", "tool_input": {"command": "python3 tools/merge_pr.py 233"}})
    assert proc.returncode == 0 and proc.stdout == "" and proc.stderr == "", proc


def test_a_broken_payload_does_not_block():
    """가드가 터져도 작업을 막지 않는다 — 대신 눈에 보이게 알린다(다른 가드와 같다)."""
    proc = hook(None, raw="{not json")
    assert proc.returncode == 0, proc
    assert "guard_merge.py failed" in json.loads(proc.stdout)["systemMessage"]
