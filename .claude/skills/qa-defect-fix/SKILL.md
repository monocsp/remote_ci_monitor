---
name: qa-defect-fix
description: B3 and B4 of one reproduced finding — prove the root cause (flip the suspected line, symptom on and off), have the scenarios written before any diff, then fix test-first with a red-then-green receipt from `tools/redgreen.py`, a mutcheck mutant and the smallest change. Use it from `qa-defect-loop` or `qa-batch` once B2 has reproduced the finding. Not for a finding that has not reproduced (B2 first), not for planned work (`build-pr`).
---

# QA defect fix (B3–B4)

Entered with a finding that reproduced twice against a control (B2 of [qa-defect-loop](../qa-defect-loop/SKILL.md)).
Four hands, kept apart on purpose: the main session traces and dispatches; `scenario-author` writes what
must be true without seeing a fix; `regression-test-author` writes the red test and later shakes it; a
`build-lead` in `qa-fix` mode proves the cause and fixes ([stage-lead.md](../_shared/stage-lead.md)).
Nobody here judges whether the fix works — that is B5.

## 0 Scenarios first — before anyone looks for the cause

Right after B2, spawn a `scenario-author` ([agent](../../agents/scenario-author.md); by `subagent_type`,
a general agent would have Edit and Write). Give it the finding as received, the B1 observable statements,
the expected behaviour with its source, the B2 recipe, the row prefix (B4.1), and as read root a snapshot
of the base commit at `.verify/handoff/<branch with / as ->/snap-<n>/` of the fix worktree
([stage-lead.md](../_shared/stage-lead.md) «What a read-only agent gets»). **Not** the cause, **not** a
fix plan, **not** a diff: a cause or a plan is the diff in prose, and a scenario written from it checks the
fix instead of the behaviour.

It returns a table — what must be observable once fixed and how; neighbouring paths that use the same code
and must still work; the edge axes worth a row per [edge-scope.md](../_shared/edge-scope.md). The main
session writes it into the round folder next to `F<n>.md`; B5 runs it.

## B3 Root cause — a hypothesis until it is flipped

1. **Trace** (main session, or a read-only agent): from the symptom back one hop at a time — CLI → handler
   → module → store — to the line that makes it wrong. A symptom is not a cause: "`rcm wait` prints 1" is
   the symptom; "a lost connection is mapped to the failed branch at `cli.py:<line>`" is a cause.
2. **Why it was like this until now**: when the line came in (`git log -L <a>,<b>:<file>`) and which test,
   mutant or review should have caught it. This goes into `F<n>.md` and feeds the meta loop.
3. **Proof** is the lead's first step, before any fix: **flip** — change the suspected line, the B2 symptom
   goes; restore it, it is back — or **inject** — create the suspected condition (a fake response, a state
   in the test database, a forced exception) and the same symptom appears. Every temporary change is
   reverted and noted. A cause that cannot be shown this way (a race the recipe cannot hit at will) stays
   **hypothesis** in `notes`, in the PR and in the report, and B5 is told.
4. The same cause in another place is listed, not fixed: a new finding, unless it is the same function.

A decision the fix needs — two contracts collide, documented behaviour would change, the owner's words
point elsewhere, an existing test locks the opposite — is a ⛔ before dispatch, or the lead's POLICY
question and a `blocked` round. It is never decided in code.

## B4 Fix — test first, smallest change

**1 Expected-behaviour rows.** The main session commits to the fix branch a short table in the
[scenario-trace.md](../_shared/scenario-trace.md) format at `docs/acceptance/qa/<date>-<topic>-<what>.md`:
its own prefix of two or more capital letters that `./.venv/bin/python tools/scenario_trace.py
--prefix-usage <P>` reports unused, one row per observable expected result, the source in each row.
Tests tag these IDs; the trace at the end of B4 reads them.

**2 The red test, by another hand.** Spawn `regression-test-author` stage `red`
([agent](../../agents/regression-test-author.md)) with the worktree, the finding, the cause hypothesis and
the row IDs. It writes a test that fails **on the assertion, for the defect's reason** — not on an import,
a collection error or a missing fixture — and commits nothing. The main session commits that test as it is
(the tree must be clean before a lead).

**3 The fix.** One `build-lead`, mode `qa-fix`, in file `qa-fix-<n>-in.md` with: the finding verbatim, the
recipe and observed output, the expected behaviour and its source, the cause hypothesis (`file:line`), the
red test's node id. The lead proves the cause (B3 step 3), confirms the test is red and keeps it unchanged,
makes the **smallest change** that turns it green — no refactor, no neighbouring cleanup, no second finding
— updates docs through the `docs` skill when users can see the change, commits with the `commit` skill and
returns `out.json` with the proof in `notes` and `red` from before the fix.

**Never weaken a test** to make the change pass (AGENTS.md "Tests first"). A test that locks an existing
contract and stands in the way is a POLICY question.

**4 Shake the green.** After the lead's commit, spawn `regression-test-author` stage `prove` on the fix's
40-character sha: an equivalent rewrite stays green, the defect put back into that rewritten form goes red,
deleting what is checked goes red — "found nothing" and "found it fine" must not look the same: a test that
matches only the old shape of the code lets the defect back in a new shape. Any test change `prove` left
uncommitted is committed by the main session as it is, before the next lead (as in step 2). Its proposed
permanent mutant for `scripts/mutcheck.py` goes to a short follow-up `qa-fix` round (`n+1`); the main
session does not edit code, and `rcm-ci` runs the full mutcheck, not the lead.

**5 The receipt.** The one test run the main session makes itself, never the lead (it adds worktrees
outside the lead's): `qa-fix-<n>-redgreen.json`, made and read as
[stage-lead.md](../_shared/stage-lead.md) «The red-then-green receipt» says. It puts the fix commit's
test files onto its parent and runs them there (must fail; it says whether they failed in the test or
while being collected), then at the fix commit (must pass). Exit 1 means B4 is not done; 3 is "could
not decide" — not a pass, find out why.

**6 Plan-to-test.** The machine trace over the expected-behaviour table with `--tests tests/`, then one fresh
`isolated-verifier` that sees the table and the tagged tests, never `src/`
([scenario-trace.md](../_shared/scenario-trace.md)). `UNSOUND` sends the tests back to a new lead round;
three in a row go to the owner.

**7 PR.** The `pr` skill; the body names the finding, quotes the source of the expected behaviour, says
whether the cause is proven or a hypothesis and links the receipt. `rcm/ci` as in `dev-batch` §3. Green,
traced, receipt 0: B5, [qa-defect-verify](../qa-defect-verify/SKILL.md).

## Never

- Start B4 with an open ⛔ for this finding.
- Give the scenario author the cause, a plan or a diff.
- Call an unproven cause a cause.
- Fix during the trace, or fix something the finding did not report.
- Judge your own fix.
