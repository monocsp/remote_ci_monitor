# Scenario trace — did the tests carry over every row of the plan?

A workplan's test table lists what must be true; the tests are supposed to lock each row. Two kinds
of slip get through review unless something looks for them: a row with no test at all, and a test
that carries the row's name but does not assert its result. Two devices catch them (adopted from
another project, 2026-11-20: its owner asked how anyone knew the tests matched the plan, and only the
implementer's own mapping existed, which nothing else read):

| device | catches | who |
|---|---|---|
| machine trace | a row with no test, a test tagged with an ID the table does not have (typo, deleted row), a malformed ID or tag, a duplicate, two workplans with the same prefix | `tools/scenario_trace.py`, also run by `rcm/ci` through `tests/test_scenario_trace.py` |
| plan-to-test verifier | a tagged test that does not assert the row's result; a `(live)` row pytest could have locked | one `isolated-verifier` that never sees the implementation |

Only workplans that carry the marker are traced; older ones without it are left alone.

## Format

```markdown
<!-- scenario-prefix: T -->
| ID | scenario | result to check | where | PR |
|---|---|---|---|---|
| T1 | a job submitted while the server restarts | it ends `lost`, never `queued` again | e2e | A |
| T2 | `rcm wait` loses the server | exit 3, stderr says unknown | unit | A |
| T3 | the queue card after a restart | the job shows as lost (live) | browser | B |
```

- The marker sits on the line directly above the table; the first column is the ID: the prefix, then
  a number (`T1`, `T2`, … no leading zeros). The prefix is capital letters, one per workplan, chosen
  from the milestone so two workplans never share it. A QA round uses its own prefix the same way.
- `(live)` in a row means a live run proves it (`.claude/skills/live-verify/SKILL.md`), not pytest.
  Use it only for what needs a running server, worker or browser. A table where every row is `(live)`
  locks nothing; the plan verifier raises it.
- Tests tag a row by name, `def test_t3_queue_card_shows_lost(...)` (several from the front:
  `test_t3_t4_…`), or by a comment `# T3` inside the test function. A row may be covered by tests in
  several files.
- Deleting or renumbering a row means fixing its tags in the same commit; an orphan tag fails the
  trace.

```sh
python3 tools/scenario_trace.py docs/m5x-workplan.md [more.md …] [--tests tests/] [--json]
# problems: format · empty (a table with no IDs) · duplicate · missing (a row with no test) ·
#   orphan (a tag the table lacks) · clash (two documents, one prefix) · nothing (no marker at all)
# exit 0 ok · 1 problems (listed) · 2 usage
```

## When

- **P1** writes the table with the marker and runs the trace before asking for approval. With no
  tests yet, only `missing` may be reported; every other problem is fixed in the plan. The spec PR's
  merge freezes the rows; changing a row later is a re-approval.
- **P2** names tests after the rows, records RED, then runs the trace (exit 0) before handing
  back. Then the main session spawns the plan-to-test verifier below. `SOUND` is a condition for P2
  being done; `WEAK` and `MISSING` are fixed in P2 regardless of severity.
- **QA** uses the expected-behaviour IDs of the defect the same way at the end of B4.

## The plan-to-test verifier

One `isolated-verifier`, spawned by the main session with `effort: "xhigh"` on the Agent call (in
another project its verdicts matched `max` in about a fifth of the time; provisional here). It sees
the workplan and the tagged tests in a snapshot, never `src/`: with the implementation in view it
reads "the code does this, so this test is enough".

```sh
cd <abs worktree> && S=.verify/handoff/<branch-dashes>/snap-trace-<n> && mkdir -p "$S" \
  && git archive <40-char sha> -- docs/m5x-workplan.md <tagged test files> <tests/ helpers they import> \
  | tar -x -C "$S"
```

Helpers (fakes, fixtures, `tests/*.py` the tests import) go in too: when the assertion lives in a
helper, the verdict needs it. Prompt:

```
You are the plan-to-test verifier for <workplan> at <40-char sha>. Read only <abs snapshot path>.
For every row with prefix <X>, check that a tagged test sets up the row's situation, triggers it,
and asserts the result in the row. Use the plan-to-test verdicts of .claude/agents/isolated-verifier.md.
Start your answer with `reviewed: <40-char sha>`. End with `verdict: SOUND` or `verdict: UNSOUND`.
```

`UNSOUND`: fix the tests, commit, trace and ask again on the new sha. Three `UNSOUND` in a row go to
the owner: the row is probably too vague to test, which is a workplan question. Keep each answer as
`.verify/handoff/<branch-dashes>/trace-review-<n>.txt`, `n` going up.

## What the machine does not see

- How strongly a test asserts its row. That is the verifier's job; a tagged test weakened after the
  review is invisible to both, so a big change to a tagged test asks for the review again.
- Text of a row beyond its line. Write each row as one line.
- Whether a `(live)` row was actually run. `live-verify` lists them; a live verifier raises a missing one.
- A tag in a skipped test or in a comment of the wrong test; the verifier calls that `WEAK`.
- A row that was never written. That is plan verification's and the owner's job.
