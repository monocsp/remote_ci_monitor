---
name: pr
description: Open, check, merge and clean up a pull request for remote_ci_monitor — title in the commit-header style, a body with why / what / checks / docs, CI green before a REST merge, branch and worktree removed after. Use it for every `gh pr create` and every merge.
---

# Pull request

A PR is read twice: by the reviewer now and by whoever bisects a regression later. Both need the
title to say the change and the body to say why. `tools/guard_naming.py` refuses a title that is
not `<type>(<scope>): <summary>`; `CONTRIBUTING.md` (Names) is the rule.

## Title

The same shape as a commit header (`commit` skill), because the merge commit takes it:
`fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것`. One PR, one concern — if the title needs
"and", it is two PRs (or the second half is a follow-up). A PR that is not ready starts with
`WIP: ` and is opened as a draft (`--draft`); remove both before asking for a merge.

## Body

```
## 한 줄
무엇이 어떻게 달라지는가 — 한 문장.

## 왜
문제 · 결정 · 반대급부. 계획서의 절과 결정 번호 (docs/m5i-workplan.md §3 B1 · 결정 79).

## 무엇
- 바뀐 것을 동작 수준으로, 파일이 아니라.
- 스키마·설정·종료 코드가 바뀌면 여기에 **Breaking** 으로.

## 검사
- `ruff check . && ruff format --check . && pytest` — 3150 passed
- `node --test tests/web/*.test.js` · `python scripts/mutcheck.py` (N/N)
- 실기: 무엇을 어디서 돌려 봤나(포트 · data_dir · 잡 번호)

## 문서
CHANGELOG [Unreleased] · docs/… · README 미러 — 또는 「사용자에게 보이는 변화 없음」.

Refs: #80
```

End the body with the attribution block given at session start.

## Steps

1. Before opening: `ruff check . && ruff format --check . && pytest`, plus `node --test` and
   `python scripts/mutcheck.py` when the change touches what they cover; the `docs` skill for a
   user-visible change. Push with the pinned account:
   ```sh
   export GH_TOKEN=$(gh auth token --user monocsp)
   git push -u origin <branch>
   python3 tools/guard_naming.py pr "<title>"
   gh pr create --base dev --title "<title>" --body "$(cat <<'EOF'
   …
   EOF
   )"
   ```
2. Wait for CI (`test` — ubuntu 3.11/3.13, macOS 3.13, smoke, gitleaks), and read a failure
   before touching anything: `gh pr checks <N>` · `gh run view <id> --log-failed`.
3. Merge only when green, by REST (the `gh pr merge` command is blocked here), with the title as
   the merge commit's subject and the PR number appended, into `dev` as a merge commit:
   ```sh
   gh api -X PUT repos/monocsp/remote_ci_monitor/pulls/<N>/merge \
     -f merge_method=merge -f commit_title="<title> (#<N>)"
   ```
   `main` only takes a PR from `dev` (`gh pr create --base main --head dev`), and the `Releasing`
   section of `CONTRIBUTING.md` says when.
4. Clean up — the branch is done, the worktree is done:
   ```sh
   gh api -X DELETE repos/monocsp/remote_ci_monitor/git/refs/heads/<branch>
   git worktree remove ../remote_ci_monitor-<what-it-does>
   git fetch --prune
   ```
5. Say in the chat: PR number, merge commit, what CI ran, and what the next branch is.
