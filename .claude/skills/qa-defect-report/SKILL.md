---
name: qa-defect-report
description: B6 — close one finding (or a QA batch) with a report in `docs/acceptance/reports/` that a stranger can follow — the words as reported, reproduction, proven cause, fix commit, red-then-green receipt, verification rounds, what remains — with every evidence file listed by `shasum -a 256`, redacted, and privacy-checked before its PR. Use it after B5 passed or after a closing without a fix. Not for a milestone's live acceptance run without findings (`live-verify` writes that report).
---

# QA defect report (B6)

The reader does not know the case. They need the picture first, then what happened, why, what changed and
how anyone knows it is fixed — or, for a finding closed without a fix, why not and on what evidence.
A closing without a fix writes this report too; "why not" matters to the reporter as much as "how".

## 1 Collect evidence first — while the worktrees still exist

Evidence lives in the fix worktrees (`.verify/handoff/<branch with / as ->/`: receipts, verifier
answers) and in the round folder. **Before any worktree is removed**, copy what the report will cite
into the round folder
(`~/.local/state/rcm-qa/<date>-<topic>/F<n>/`). A file that is gone is written as **missing**, with where
it was expected — never recreated after the fact, never searched for in another session's worktree:
stopping in front of evidence that sits in another session's tree is right.

## 2 Redact, then place in the repository

The repository is public. Copy into `docs/acceptance/reports/evidence/<date>-<topic>/F<n>/` only redacted
copies:

- the home directory → `<HOME>`; user, host and machine names → generic words;
- tokens, capabilities and anything token-shaped → the first 4 characters;
- e-mail addresses, account logins, repository paths of other projects → removed;
- screenshots come from the CDP helper on a test server (`tests/test_web_browser.py`) and nothing redacts
  them for you: crop or retake any image that shows a token, a port, a home path or a host name, and look
  at each one. `tools/screenshots` only runs its own demo and cannot capture a finding;
- a high-entropy literal fails the `secrets` scan even as a placeholder (AGENTS.md): cut it shorter.

Then list them, once:

```sh
cd docs/acceptance/reports/evidence/<date>-<topic> && shasum -a 256 F*/* > SHA256SUMS
```

**A listed file is never written over.** A re-run, a re-capture after another fix round, a new log: a new
name (`-r2`) and a new line (`shasum -a 256 F3/repro-1-r2.txt >> SHA256SUMS`). Before the PR,
`shasum -a 256 -c SHA256SUMS` must print only `OK`: a re-run or a "better" re-capture written over a listed
file erases the evidence the report cites.

## 3 The report

`docs/acceptance/reports/<YYYY-MM-DD>-<topic>.md`, one per batch (a section per finding) or per lone
finding. It extends the existing form (see the newest report in that folder): the header list first —
date, target (`dev` sha, PRs), the source documents, how to run it again (`scripts/acceptance/<topic>_live.sh`
when there is one) — then a result table, then sections.

Written in Korean like the existing reports, with the `korean-writing` skill; long prose goes to the
`korean-writer` agent with the materials below and the reader named. No sentence without evidence:
"개선했다" is replaced by the observed value, the command and the file.

Per finding:

| part | content |
|---|---|
| title | the finding as reported, verbatim; interpretation goes below it, never in its place |
| 한눈에 | one line each: what really happened, the cause (`file:line`), the fix, how it was checked |
| 재현 | the recipe exactly, 2/2 or flaky, the control and what it showed |
| 원인 | `file:line`, the proof (flip or inject, with the command) or "가설", and why it was not caught until now |
| 수정 | fix commit (40 characters) and PR; what changed per file and why; alternatives dropped |
| red-then-green | the `tools/redgreen.py` receipt: both shas, the test ids, exit codes, the failing line on the parent |
| 검증 | per round: verifiers, `high/medium/low`, the core lines — copied from the `verify-round:` comments and the kept answers; the mutant added to `scripts/mutcheck.py` |
| 남은 것 | every MED and LOW still open: detail, how to fix it, impact · difficulty · time; findings moved to new findings with their ids |
| 결정 | each ⛔ with the question, the owner's answer verbatim and the decision page link |
| 추천값으로 정한 것 | boundary questions that went ahead with the recommended default after the owner's answers, so the owner can confirm them |
| 증거 | path · what it shows · sha256 (from `SHA256SUMS`) |

A finding closed without a fix replaces 원인 and 수정 with the closing (`not_a_defect`, `wontfix`,
`cannot_reproduce`, `duplicate`) and its proof from [qa-defect-loop](../qa-defect-loop/SKILL.md).

At the end of a batch: the meta-loop proposals (a file in several findings; a branch red twice for reasons
outside its change), each as one line with where it would be enforced.

## 4 Privacy check before the PR

The same steps as the release check (`docs/m5s-workplan.md` §8), on this branch's range:

```sh
grep -rnIf <patterns file kept outside the repository> <changed files>     # 0 hits
gitleaks git --log-opts="origin/dev..HEAD"                                 # 0 findings
git log --format='%ae %ce' origin/dev..HEAD | sort -u                      # noreply addresses only
```

plus the general patterns of that section (e-mail shape, `/Users/<name>`, token shapes) and every changed
image by eye. The patterns file never enters the repository — a list of private values is itself a leak.
Each step's result is one line under the PR body's checks, without the patterns.

An e-mail-shaped hit that is an image name's scale suffix (`queue@2x.png`, `card@3x.webp`: a word,
`@`, a digit and `x`, then an image extension) is not an address. Pass it only when the whole hit has
that shape; anything else around the `@` is read as an address (other project, 2026-11-26: its
checker flagged `@3x` names, and its first exception was too wide and had to be narrowed to this
shape).

## 5 PR and close

A docs-only branch named with the `branch` skill, the `pr` skill, `rcm/ci` green, then
`python3 tools/merge_pr.py <N> --wait --no-verify "acceptance report and redacted evidence only"`.
After the merge, mark the finding closed in the round folder with the merge commit; only then may the
worktrees and test-server data of this round be removed (`qa-batch` stage 10).

## Never

- Summarise the reported words in the report.
- Put a before and an after side by side that were not taken at the same place with the same steps.
- Hand the owner a report whose `SHA256SUMS` check fails or whose evidence is missing without saying so.
- Close a finding silently: an open ⛔ or a deferred item stays visible in the report.
