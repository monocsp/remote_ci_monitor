---
name: build-pr
description: P2 and P6 of the development loop for remote_ci_monitor — a fresh build-lead turns one PR's scenario rows into red tests named with their IDs, proves them red, implements, checks the IDs with `tools/scenario_trace.py`, adds a permanent mutcheck mutant per defect, updates the docs and commits; then one isolated verifier checks plan against tests without the code, and the main session pushes and waits for `rcm/ci`. Gap-fix mode closes confirmed GAPs from `edge-audit` by root cause. Use it after the workplan is merged, and for every confirmed GAP. Not for planning (`workplan`), not for a small change without a workplan (`dev-lite`), not for several PRs at once (`dev-batch`).
---

# Build a PR (P2 implement · P6 gap fix)

One PR from the workplan's §5, built test-first by a lead who did not write the plan and judged by
people who did not build it. The lead's "done" is never the verdict: `rcm/ci` on the head and the
isolated verifiers decide.

## Who does what

The main session: creates the worktree, writes the in-file, starts
[`build-lead`](../../agents/build-lead.md), reads its out.json, runs the plan↔test check, pushes,
opens the PR, waits for `rcm/ci`. The lead: everything in §2 or §4, committed on its own branch. The
handoff format, location (`.verify/handoff/<branch>/`, stage `P2` or `P6`), the out.json contract and
the rules a lead keeps are [`_shared/stage-lead.md`](../_shared/stage-lead.md). A lead is new for
every stage and every retry — a red gate or an UNSOUND check goes into a new in-file for a new lead.

## 1. Before the lead starts (main session)

1. The workplan is merged on `origin/dev` and this PR's §6 row has its dependencies merged.
2. Branch name with the `branch` skill, then its own worktree and `.venv` (`AGENTS.md` «Branches»).
3. The worktree is clean; the in-file names: the workplan path and the sections this PR implements,
   the scenario rows (IDs) it owns, the files it owns and the shared ones (`CHANGELOG.md`,
   `scripts/mutcheck.py`, `tests/test_docs_<code>.py` — add your own entries only), the frozen
   contracts it must not change, and in P6 the GAP table and the redesign targets.
4. A defect whose red test is hard to pin down (a race, a restart) may first go to
   [`regression-test-author`](../../agents/regression-test-author.md); its test and proposed mutant
   go into the in-file. Leads cannot spawn agents.

## 2. P2 — what the lead does, in order

1. **Rows → red tests.** Each scenario row this PR owns becomes one or more tests named with its ID
   (`test_s3_…`) or tagged `# S3` inside the test — format in
   [`_shared/scenario-trace.md`](../_shared/scenario-trace.md). `(live)` rows are not pytest's; they
   go to [`live-verify`](../live-verify/SKILL.md).
2. **Prove them red** before writing the code. The evidence is the command and the failure it
   printed, and the failure is in the assertion, not in collecting or importing the test file — an
   import error proves only that a name is missing; when that is the point (a new module), say so.
   Once the fix is committed, `python3 tools/redgreen.py --fix <sha> --test <nodeid>…` gives the
   receipt for out.json `red`.
3. **Implement.** Standard library only, Korean comments, English identifiers and output, pure
   computation in `core/` with `now` as an argument. Run the changed tests and `ruff check . &&
   ruff format --check .`; `node --test tests/web/*.test.js` when `web/` changed. The full suite,
   the full `mutcheck` and `smoke_install.sh` are `rcm-ci`'s — not repeated locally.
4. **Trace.** `python3 tools/scenario_trace.py docs/<code>-workplan.md --tests tests/` exits 0:
   no row without a test, no tag without a row.
5. **Mutants.** Every defect this PR fixes, and every check it adds that a silent deletion would
   undo, gets a permanent mutant in `scripts/mutcheck.py` with its source in the docstring. Prove each:
   `python scripts/mutcheck.py --only <name>` must go red. It works on a copy in a temporary folder,
   so the worktree is safe.
6. **Docs** with the `docs` skill for anything a user sees; the doc-lock test names missing wording.
7. **Commits** with the `commit` skill (Korean by `korean-writing`), small, on the branch. No push.
8. **out.json** `complete` only with a non-empty `red`. A departure from the workplan goes in
   `workplan_diff` ([`_shared/workplan-diff.md`](../_shared/workplan-diff.md)) and is never silent;
   a frozen contract that has to change is a `POLICY` question and status `blocked`.

Never weaken, skip or `xfail` a test, and never `# noqa` a rule, to get green.

## 3. After the lead (main session)

1. `git -C <worktree> status --porcelain` is empty and its HEAD is out.json `head`. Tell the owner
   each `workplan_diff` row in one chat line as it comes in.
2. **Plan↔test check, without the code.** Snapshot what the verifier may see — the workplan and
   the tests, not `src/`:
   ```sh
   S=<abs handoff folder>/snap-P2-<n>; mkdir -p "$S"     # folder: _shared/stage-lead.md
   git -C <worktree> archive <head> docs/<code>-workplan.md tests/ | tar -x -C "$S"
   ```
   One [`isolated-verifier`](../../agents/isolated-verifier.md) gets only that folder and the prompt
   in [`_shared/scenario-trace.md`](../_shared/scenario-trace.md). SOUND → push. UNSOUND → a new lead
   with the findings. UNSOUND a third time → stop and ask the owner: the plan or the tests may be
   wrong, not the code.
3. Push and open the PR with the `pr` skill. In `## 검사` keep two lists apart: the lead's feedback
   checks (which tests, ruff, node — run in the worktree) and the authoritative result, `rcm/ci` on
   the head sha with its job number. A feedback pass is never written as the gate.
4. Wait for `rcm/ci` ([`dev-loop`](../dev-loop/SKILL.md) «long waits»). Red → read `rcm logs <N>`,
   then a new lead with the log. **The same gate failure three times → stop and ask**: a test, the
   spec or the harness may disagree, and a fourth patch to the code will not settle it.
5. Green → back to [`dev-loop`](../dev-loop/SKILL.md) for P3.

## 4. P6 — gap-fix mode

Input: the confirmed GAPs from [`edge-audit`](../edge-audit/SKILL.md) (its merged JSON) and the
redesign targets. The lead, a new one — not the P2 lead:

1. **Group by root cause**, not by symptom (`root_cause_theme`). One cause seen in three places is
   fixed once at the cause, not three times where it showed.
2. **Order:** a lost or wrongly finished job, data loss, DB damage, secret exposure and touching
   production first; then fail-open (unknown shown as a number or success, a wrong exit code); the
   rest after.
3. **A cause seen in a second round is redesigned, not patched again.** Write the cause as one
   sentence; first write a test that varies the order of the events behind it (claim, heartbeat,
   finish, cancel, restart in shuffled interleavings, a fake clock) and see it red; then change the
   code where the cause is. A new LOW from a redesigned cause is deferred (lesson from another project,
   2026-10-13: five rounds of local fixes to one cause ended in a HIGH regression).
4. Each GAP: a test that was red (named with the edge's ID), the fix, a mutant as in §2.5, a
   `redgreen.py` receipt.
5. out.json as in §2.8; the GAPs it closed and how, in `notes`.

The main session then pushes, waits for `rcm/ci`, and runs the re-check: two isolated verifiers
judge each GAP again with the [`edge-audit`](../edge-audit/SKILL.md) prompt, plus "does the test
reproduce this path and fail without the fix?". **Closed = HANDLED by the re-check and a test that
locks the path.** A self-assessment never closes a gap. New HIGH from the re-check is fixed here; new
MED/LOW are deferred to the PR body. The same gap not closed three times → stop and ask the owner.

## 5. Meta loop

For each defect, the lead writes in `notes` what could have caught it before a person did: a test
shape, a mutant, a doc-lock test, a guard hook, a line in a skill. The main session carries the
answer into the PR or the loop summary.
