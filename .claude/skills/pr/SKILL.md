---
name: pr
description: Open, check, merge and clean up a pull request for remote_ci_monitor — title in the commit-header style, a body with why / what / checks / docs, the ruleset's required checks green and a `verified:` marker before `tools/merge_pr.py` merges, branch and worktree removed after. Use it for every `gh pr create` and every merge.
---

# Pull request

A PR is read twice: by the reviewer now and by whoever bisects a regression later. Both need the
title to say the change and the body to say why. `tools/guard_naming.py` refuses a title that is
not `<type>(<scope>): <summary>`; `CONTRIBUTING.md` (Names) is the rule.

## Title

The same shape as a commit header (`commit` skill), because the merge commit takes it:
`fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것`. One PR, one concern — if the title needs
"and", it is two PRs (or the second half is a follow-up). A PR that is not ready is opened as a
draft (`--draft`; GitHub refuses to merge one) and marked ready (`gh pr ready <N>`) before the merge.
The title keeps its header shape either way: `tools/guard_naming.py` refuses a prefix such as WIP.

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
- `./.venv/bin/ruff check . && ./.venv/bin/ruff format --check . && ./.venv/bin/python -m pytest` — 3150 passed
- `node --test tests/web/*.test.js` · `./.venv/bin/python scripts/mutcheck.py` (N/N)
- 실기: 무엇을 어디서 돌려 봤나(포트 · data_dir · 잡 번호)

## 문서
CHANGELOG [Unreleased] · docs/… · README 미러 — 또는 「사용자에게 보이는 변화 없음」.

Refs: #80
```

More sections when the development loop produced them (`.claude/skills/dev-loop/SKILL.md`), in this
order right after `## 한 줄`: `## 요청 카드` (`dev-lite`), `## Differs from the spec`
(`.claude/skills/_shared/workplan-diff.md`), `## Edge audit` (the confirmed GAP table, `edge-audit`),
`## Remaining findings` (the MED/LOW the final triage reported rather than fixed,
`.claude/agents/isolated-verifier.md`).

End the body with the attribution block given at session start.

Write a body that goes through a file into a file named after the branch,
`pr-body-<branch with / as ->.md` (`pr-body-feat-guard-merge.md`), never `pr.md`, `pr_body.md` or
`body.md`, kept in `<abs worktree>/.verify/handoff/<branch with / as ->/` (git-ignored, so
`tools/dev_batch.py scope` and `git status --porcelain` stay clean) and passed as
`--body-file <that path>`; never the scratchpad: parallel sessions share it and overwrote each
other's body there (#220 and #233 went up with another PR's body). `tools/guard_naming.py` refuses
`--body-file` with a shared name.

The Korean in the title's summary and in the body is written with the `korean-writing` skill
(`.claude/skills/korean-writing/SKILL.md`). Load it before you write them. The template's headings,
the trailers and the attribution line stay as they are; the skill only governs the sentences.
The body is addressed to people, so it is written in 합니다체 (the skill's §4); commit bodies and
documents that stay in the repository are written in 평어체.

## Steps

1. Before opening: `./.venv/bin/ruff check . && ./.venv/bin/ruff format --check . &&
   ./.venv/bin/python -m pytest`, plus `node --test` and `./.venv/bin/python scripts/mutcheck.py`
   when the change touches what they cover; the `docs` skill for a
   user-visible change. In the development loop (`dev-loop` and the skills it routes to) run only
   the changed files' tests and `./.venv/bin/ruff` on the changed files; the full suite and mutcheck
   are `rcm-ci`'s, and `rcm/ci` decides (`.claude/skills/_shared/stage-lead.md` «Rules for every
   lead» 6: full local runs loaded the build machine on 2026-11-20). Load the `korean-writing` skill and write the title's summary and the body
   by its rules (its §6 check before you paste them). Push with the pinned account:
   ```sh
   export GH_TOKEN=$(gh auth token --user monocsp)
   git push -u origin <branch>
   python3 tools/guard_naming.py pr "<title>"
   mkdir -p <abs worktree>/.verify/handoff/<branch with / as ->   # where the body file lives
   gh pr create --base dev --title "<title>" \
     --body-file <abs worktree>/.verify/handoff/<branch with / as ->/pr-body-<branch with / as ->.md
   ```
   A title naming both `rcm` and `setup` is refused by `tools/guard_production.py` on a machine
   with a production rcm install; write it to a file and pass `--title "$(cat <file>)"` (and
   `python3 tools/guard_naming.py pr "$(cat <file>)"`), whose command text holds neither word.
2. After every push, wait for the commit status `rcm/ci` — the ruleset's required check on `dev`
   and `main` (`main` also needs `main-from-dev-only`). The build machine's watcher submits the
   `rcm-ci` job within a minute and posts `queued · #<N>` on the head; the job posts its result.
   ```sh
   gh api repos/monocsp/remote_ci_monitor/commits/<sha>/status --jq '.statuses[] | [.context, .state, .description]'
   rcm wait --job <N>
   ```
   No `rcm/ci` after two minutes means the watcher did not see it (its log is
   `~/Library/Logs/rcm/ci-watch.log`): submit it by hand with
   `cd ~ && rcm run rcm-ci --source git_ref --ref <branch> --no-wait` and say so in the PR.
   Read a failure before touching anything: `rcm logs <N>`. GitHub Actions (`ci.yml`) no longer
   runs on pull requests; it is `workflow_dispatch` only, run by hand when the owner asks because
   the build machine is down.
3. Merge with `tools/merge_pr.py`, and only with it. `tools/guard_merge.py` (a `PreToolUse`
   hook) refuses `gh pr merge`, a REST `PUT …/pulls/<N>/merge` through `gh api` or `curl`, and a
   script that would run one; a watcher that merged by REST in 2026-11 merged twice with checks
   still pending, and three PRs merged without re-verification.
   ```sh
   export GH_TOKEN=$(gh auth token --user monocsp)
   python3 tools/merge_pr.py <N> --wait --delete-branch
   ```
   It pins the PR's head sha and then refuses unless every check the base branch's ruleset
   requires (`GET rules/branches/<base>`: `rcm/ci` on `dev`, `main-from-dev-only` and `rcm/ci` on
   `main`) has passed on that sha, as a check run or a commit status, the latest status of a
   context counting (`--wait` waits up to `--timeout` seconds, 3600 by default, and refuses if
   the head moves meanwhile). Checks the ruleset does not require, GitHub Actions included, are
   counted and ignored, pending or failed. It also needs a PR comment carrying the verification
   marker for that sha, and GitHub must report no conflict. Then it
   merges that sha into the base as a merge commit titled `<title> (#<N>)` and reads the PR back.
   `--dry-run` reads everything and changes nothing; `--delete-branch` never deletes `main` or
   `dev`. Exit codes: 0 merged, 1 refused, 2 usage, 3 GitHub did not answer (look before
   retrying).

   **The verification marker.** Whoever ran the isolated verification on the head, and saw it
   pass, posts one line as a PR comment, the full 40-character head sha after `verified: `:
   ```sh
   gh pr comment <N> --body "verified: $(gh pr view <N> --json headRefOid -q .headRefOid)"
   ```
   Each verification round leaves its own line before that, in the record formats of
   `.claude/agents/isolated-verifier.md` «Rounds» (the only copy); `tools/merge_pr.py` reads only
   the `verified:` line. The line stands alone (no quote, no short sha, lowercase). A push after it
   makes it stale: verify the new head and post a new line; what a head moved by triage fixes needs
   first is «Rounds» 7 of the same file. A PR with nothing to verify (documentation only, tests
   only) merges with `--no-verify "<reason>"`; the reason is posted on the PR.

   `main` only takes a PR from `dev` (`gh pr create --base main --head dev`), and the `Releasing`
   section of `CONTRIBUTING.md` says when.
4. Clean up — the branch is done (`--delete-branch` deleted it on GitHub), the worktree is done:
   ```sh
   git worktree remove ../remote_ci_monitor-<what-it-does>
   git fetch --prune
   ```
5. Say in the chat: PR number, merge commit, what CI ran, and what the next branch is.
