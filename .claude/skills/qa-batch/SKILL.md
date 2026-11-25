---
name: qa-batch
description: Take several findings at once — an acceptance round run from a scenario document, or several owner reports — through triage, reproduction, test-first fixes, isolated verification, merge and one report. Use it for two or more findings. One finding goes to `qa-defect-loop`; a new feature or a planned change goes to `dev-loop`; several planned PRs of a milestone go to `dev-batch`.
---

# QA batch

A router. Each finding goes through B1–B6 of [qa-defect-loop](../qa-defect-loop/SKILL.md); this skill decides
the order, groups findings into branches, runs what is shared once (test servers, the decision page, `rcm/ci`,
merges, the report) and keeps the findings table.

## Policy

- **Only the main session records, submits and merges.** It alone writes the findings table, posts PR comments,
  pushes, submits `rcm-ci` and runs `tools/merge_pr.py`. Leads and verifiers return text or files.
- **Production is untouched.** Test servers only, set up by [test-server.md](../_shared/test-server.md).
  Looking at a real job on the build machine needs the owner's OK, is read-only and is the main session's;
  a verifier gets its numbers only as a file (test-server.md, last section).
- **An item the owner asked for explicitly is never closed alone.** Disagreeing with it ("this is intended",
  "not a defect") is a ⛔ on the decision page, never a closing.
- A side defect seen on the way becomes a new finding; it is never folded into the one at hand.

## Entries

| entry | input | first step |
|---|---|---|
| A — scenario document | an acceptance round: `docs/acceptance/*-verification-scenarios.md`, a workplan's `(live)` rows, `docs/acceptance/user-checklist.md` | stage 1 runs the rows; failing rows become findings |
| B — owner reports | several messages saying "this is wrong" | each message becomes a finding as written; stage 1 runs only what is needed to see it |

One finding, either way: `qa-defect-loop`.

## The round folder

`~/.local/state/rcm-qa/<YYYY-MM-DD>-<topic>/`, outside every worktree (a scratchpad is cleared on restart, a
worktree folder dies with the worktree). It holds `findings.md` — one row per finding: id `F<n>`, the words as
received, severity draft, expected-behaviour source, branch, status — and the raw evidence until stage 9
moves the redacted copies into the repository. Only the main session writes it.

## Stages

| # | stage | who | out |
|---|---|---|---|
| 0 | preflight | main session | worktree(s), test-server plan, empty findings table |
| 1 | run | `live-verifier` × k, each on its own test server | per row: pass / fail / unknown, with evidence paths |
| 2 | triage | main session (+ read-only agents) | B1 and B2 of each finding; findings grouped into branches |
| 3 | decision page | main session → owner | every ⛔ on one page; answers verbatim |
| 4 | scenarios | `scenario-author` per finding | scenario table written before any diff exists |
| 5 | fix | `build-lead` in `qa-fix` mode per branch | red-then-green receipt, commits, `out.json` |
| 6 | PR + `rcm/ci` | main session | PR per branch, `rcm/ci` green on its head |
| 7 | verification | `live-verifier` / `isolated-verifier` | rounds per the canon |
| 8 | merge | main session | `verified:` comment, `tools/merge_pr.py` in order |
| 9 | evidence and report | main session | report in `docs/acceptance/reports/`, evidence hashed |
| 10 | clean up | main session | servers down, data removed, worktrees removed |

**0 Preflight.** The build under test is a worktree at a named sha with its own `.venv`
(`git worktree list` first — never borrow a folder another session holds). `git -C <wt> rev-parse HEAD`
equals the pinned sha, and `./.venv/bin/rcm --version` prints the `__version__` of
`src/remote_ci_monitor/__init__.py` at that sha (pyproject takes its version from there); a mismatch is a
stale editable install, so reinstall before any run. `env | grep -E 'RCM_|XDG_'` is empty in the shell
that will start servers. Plan one test server per runner from [test-server.md](../_shared/test-server.md);
nothing here restates its rules.

**1 Run.** Split the rows among `live-verifier`s, as many at once as the parallel limit of
[test-server.md](../_shared/test-server.md) allows (this machine also runs `rcm-ci`). Each gets its rows
verbatim, the sha, its own port and data folder picked by test-server.md, and returns a table:
row · command as run · observed (exit code, output excerpt, state) · evidence file · pass/fail/unknown.
Unknown is not pass. A row that could not be run is unknown with the reason. Entry B: run only what is needed
to see each report once; the real reproduction is B2.

**2 Triage.** For each finding, B1 and B2 of `qa-defect-loop` — observable statements, the expected
behaviour with its source, two reproductions and a control. The reading may be spread over read-only agents,
one per finding; the main session writes the table. Keep the received words untouched. Draft a severity per
[isolated-verifier.md](../../agents/isolated-verifier.md), which also fixes the grade of a missing explicit
owner request. Group: findings whose suspected files overlap share one branch; the rest get one branch
each. With four or more findings over several branches, register the branches with
`./.venv/bin/python tools/dev_batch.py add` as in `dev-batch` §0, so overlapping files are refused and
`status` gives one table.

**3 Decision page.** Every ⛔ of the batch — no expected-behaviour source, a policy conflict, disagreement with
an owner's explicit words — on one page per [decision-page.md](../_shared/decision-page.md). Search for an
existing answer first (PLAN.md decisions, the workplan, docs); asking what is already decided is not
neutral. No answer is not approval. A branch with an open ⛔ stops; the others continue. Boundary questions
that come up after the owner answered go ahead with the recommended default and are listed under
"decided by recommended default" in the report.

**4 Scenarios.** Before any fix exists, a `scenario-author` per finding gets the finding as received, the
observable statements, the expected behaviour with its source, the repro recipe and a base snapshot as
`qa-defect-fix` §0 sets it up — not the cause, not the plan (`qa-defect-fix` says why). The main
session writes the returned table to the round folder. Scenarios written after the fix are a
checklist of what was done.

**5 Fix.** One `build-lead` per branch, mode `qa-fix`, in file per [stage-lead.md](../_shared/stage-lead.md),
spawned together in one message (three at most). The procedure is `qa-defect-fix`. A branch moves on only
when the main session's own `tools/redgreen.py` receipt exits 0 for each of its findings (the lead never
runs it; [stage-lead.md](../_shared/stage-lead.md) «The red-then-green receipt»).

**6 PR and `rcm/ci`.** As `dev-batch` §3: ruff before push, the `pr` skill, wait for `rcm/ci` in the
background, hand submission after two minutes, `rcm logs <N>` into the next lead round when red.

**7 Verification.** `qa-defect-verify`, per branch, on a frozen head.

**8 Merge.** `verified: <40-char head>` posted by the main session, then
`python3 tools/merge_pr.py <N> --wait --delete-branch`, in `merge-order` if the branches are registered with
`dev_batch.py`, otherwise highest severity first. Skip the `pr` skill's step 4 (worktree removal) here: the
worktrees stay until step 10, after step 9 has collected their evidence. After each merge, branches sharing
files re-sync and re-verify as in `dev-batch` §5; a red `dev` stops merging.

**9 Evidence and report.** `qa-defect-report`: collect evidence from the worktrees into the round folder
**before** any worktree is removed, redact, hash, write the report, privacy check, report PR (docs only:
`--no-verify` with the reason).

**10 Clean up.** Stop the test servers and remove the data directories this round created — only those.
Remove the worktrees this round created, `git fetch --prune`. Report to the owner in Korean.

## Invariants

- No fix without two reproductions and a control (B2); no cause called proven without a flip or an
  injection (B3); no fix without a test that was red before it (B4); no closing by the one who fixed it (B5).
- The received words are never summarised in the table or the report: a summary drops the clue that
  decides the cause first.
- Evidence files are never overwritten once their hash is listed; a re-run writes a new name.
- What is fixed during verification, and when MED and LOW are triaged: the canon's «Rounds» and «Triage»
  ([isolated-verifier.md](../../agents/isolated-verifier.md)).

## STOP

| what happened | do |
|---|---|
| A finding did not reproduce in three attempts | Stop that finding. Ask the owner for the conditions: version, config, machine, what ran just before, every time or sometimes. Closing as `cannot_reproduce` needs the three attempts in the report |
| An unresolved ⛔ | That branch waits; no B4 or later for its findings |
| Round 3 or 4 still HIGH, or too many verify→fix cycles | The canon's escalation ([isolated-verifier.md](../../agents/isolated-verifier.md) «Rounds» 3 and 4) |
| `rcm wait` exit 3 | Unknown, not a failure. Read the commit status and `rcm jobs` again; never record it as red |
| Evidence missing at stage 9 | Write "missing" with where it was expected. Never recreate it after the fact, never search another session's worktree |

## Meta loop

When the batch is closed, ask whether these findings could have been prevented. One file named by several
findings is a design problem in that file. A branch that went red twice in `rcm/ci` for reasons outside
its change is a harness problem. Write the proposal in the report; a lesson that recurs becomes a test or a
guard, not another paragraph here.

## False PASS — shapes that have already fooled a run

Check for them in every result you accept.

- **An empty path.** `cd ""` succeeds and stays where it was; `git -C ""` runs in the current repository.
  Check a path variable is non-empty and absolute before using it, or write the literal path.
- **A pipe without `pipefail`.** `cmd | tee log` exits with `tee`'s 0. Use `set -o pipefail` or read
  `PIPESTATUS`.
- **A command that died silently.** Exit 127 (not found) prints nothing on stdout; "no output" is not "no
  problems". Read the exit code.
- **A summary line read as a verdict.** "All steps done" means the steps ran, not that the screen was right;
  a job that prints `FAIL:` on purpose can be green. The verdict is the exit code and the evidence itself.
- **Counting by key presence.** A key that exists with an empty or null value is counted as present and
  inflates the result. Count values.
- **"Unknown" folded into "none".** An empty list (nothing) and a missing key (did not look) are different;
  rcm prints the second as `—` (PLAN.md 「fail-open 금지」).
- **An earlier-round LOW written as 0.** A finding not fixed is counted again next round until it is resolved
  with a reason.
- **Verdicts shared between verifiers.** The second one copies the first and the pair becomes one opinion.
- **Scenarios written after the fix.** They describe the fix, not the expected behaviour.
