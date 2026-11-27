# Scenario trace — did the tests carry over every row of the plan?

A workplan's test table lists what must be true; the tests are supposed to lock each row. Two kinds
of slip get through review unless something looks for them: a row with no test at all, and a test
that carries the row's name but does not assert its result. Without a check, the only mapping from
rows to tests is the implementer's own, which nothing else reads. Two devices catch them:

| device | catches | who |
|---|---|---|
| machine trace | a row with no test, a test tagged with an ID the table does not have (typo, deleted row), a malformed ID or tag, a duplicate, two workplans with the same prefix | `tools/scenario_trace.py`, also run by `rcm/ci` through `tests/test_scenario_trace.py` |
| plan-to-test verifier | a tagged test that does not assert the row's result; a `(live)` row pytest could have locked | one `isolated-verifier` that never sees the implementation |

Only workplans that carry the marker are traced. A document passed to the tool without any marker is reported as `nothing`, so name only marked workplans.

## Format

```markdown
<!-- scenario-prefix: XT -->
| ID | scenario | result to check | where | PR |
|---|---|---|---|---|
| XT1 | a job submitted while the server restarts | it ends `lost`, never `queued` again | e2e | A |
| XT2 | `rcm wait` loses the server | exit 3, stderr says unknown | unit | A |
| XT3 | the queue card after a restart | the job shows as lost (live) | browser | B |
```

- The marker sits on the line directly above the table; the first column is the ID: the prefix, then
  a number (`XT1`, `XT2`, … no leading zeros). `XT` is only an example.
- **The prefix** is two or more capital letters, one per workplan, QA table or dev-lite card, and used
  by no test yet: existing tests already carry short tags (`test_s1_…`, `test_a10_…`, `# P2`), and a
  prefix that matches them turns the repository's trace test red. Check each candidate and take one
  with zero usage: `cd <abs worktree> && ./.venv/bin/python tools/scenario_trace.py --prefix-usage <P>`.
- `(live)` in a row means a live run proves it (`.claude/skills/live-verify/SKILL.md`), not pytest.
  Use it only for what needs a running server, worker or browser. A table where every row is `(live)`
  locks nothing; the plan verifier raises it.
- Tests tag a row by name, `def test_xt3_queue_card_shows_lost(...)` (several from the front:
  `test_xt3_xt4_…`), or by a comment `# XT3` inside the test function. A row may be covered by tests
  in several files.
- Deleting or renumbering a row means fixing its tags in the same commit; an orphan tag fails the
  trace.
- **A row holds one observable result.** "It retries, logs the reason and keeps the job queued" is
  three rows. A row about time or about something appearing states both bounds ("not before the
  first poll, by the second at the latest"): a verifier left to invent the bound asks for a
  different one each round (other project, 2026-11-26: a row holding several conditions passed four
  reviews and went WEAK again on the next fresh verifier).

```sh
./.venv/bin/python tools/scenario_trace.py docs/m5x-workplan.md [more.md …] [--tests tests/] [--json]
# problems: format · empty (a table with no IDs) · duplicate · missing (a row with no test) ·
#   orphan (a tag the table lacks) · clash (two documents, one prefix) · nothing (a document with no marker)
#   skipped (a tagged test carries skip or xfail) · unreadable (a test file or folder it could not read)
#   format also covers a marker that is not exactly `<!-- scenario-prefix: X -->`, an interrupted table, a line GFM reads as a row, an unclosed fence
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
- **dev-lite** gives its card's acceptance lines a prefix of their own per PR, checked as above.

## The plan-to-test verifier

One `isolated-verifier`, spawned by the main session with `effort: "xhigh"` on the Agent call
(provisional, measured elsewhere; the owner confirms). It sees
the workplan and the tagged tests in a snapshot the main session makes, never `src/`: with the
implementation in view it reads "the code does this, so this test is enough".

```sh
cd <abs worktree> && S=.verify/handoff/<branch with / as ->/snap-<n> && mkdir -p "$S" \
  && git archive <40-char sha> -- docs/m5x-workplan.md <tagged test files> <tests/ helpers they import> \
  | tar -x -C "$S"
```

Helpers (fakes, fixtures, `tests/*.py` the tests import) go in too: when the assertion lives in a
helper, the verdict needs it. Prompt:

```
You are the plan-to-test verifier for <workplan> at <40-char sha>. Read only <abs snapshot path>.
For every row with prefix <P>, check that a tagged test sets up the row's situation, triggers it,
and asserts the result in the row. Use the plan-to-test verdicts of your agent file.
Start your answer with `reviewed: <40-char sha>`. End with `verdict: SOUND` or `verdict: UNSOUND`.
```

`UNSOUND`: fix the tests, commit, trace and ask again on the new sha, as «Re-reviews» below says.
When it goes to the owner instead is [`isolated-verifier`](../../agents/isolated-verifier.md)
«Rounds» 6. Keep each answer as `.verify/handoff/<branch with / as ->/trace-review-<n>.txt`, `n`
going up: a later review reads the earlier ones, so none is written over.

## Re-reviews

A fresh verifier that has not seen the last review moves the target. In another project
(2026-11-26, 27 expected-behaviour rows) each new verifier judged from scratch with "unsure is WEAK",
and the review came back `UNSOUND` seven times running while its count of WEAK and MISSING rows went
8, 11, 3, 3, 2, 4, 1; given the previous review, it settled. One of the new verifiers' findings was
still a real hole (the test checked a fake drawn into the page, not the page's own wiring), so the
first review stays independent and only the later ones get the history.

**The first review** is the prompt above, alone: no earlier answer, unsure is WEAK.

**From the second review on** — of the same workplan or the same QA table, after an `UNSOUND`, or
after a P6, triage or B5 fix touched a tagged test — the verifier gets the first review's snapshot
contents plus these files inside it:

```sh
cd <abs worktree> && H=.verify/handoff/<branch with / as ->/ && S="$H"snap-<n> \
  && mkdir -p "$S/prev" && cp "$H"trace-review-*.txt "$S/prev/" \
  && git diff <sha the last review read> <this sha> -- docs/<code>-workplan.md > "$S/rows.diff" \
  && git diff <sha the last review read> <this sha> -- <tagged test files> <helpers they import> \
     > "$S/changed.diff"
```

rcm has no tool that sorts the rows; the verifier sorts each one from the two diffs:

| kind | when | what the verifier does |
|---|---|---|
| re-judge | the row is new, its text changed (`rows.diff`), or a test tagged with it changed | judge it as a first review would; unsure is WEAK |
| carry, read the change | only what its tests stand on changed: a fixture, a helper, an untagged test in the same file | read `changed.diff`; WEAK only with a counterexample (below), otherwise carry |
| carry | nothing it relies on changed | copy the previous verdict and evidence |

Per row, the first line that applies:

1. A row the previous review called WEAK, MISSING or SHOULD_TEST is **closed** (COVERED or LIVE_OK,
   with `file:line`) or **not closed** (WEAK or MISSING, with the assertion still missing). Unsure is
   not closed. This line comes before the kind.
2. A re-judge row is judged from scratch.
3. A carry row keeps its verdict.

**A new weakness needs a counterexample.** On a carried or closed row, a new WEAK is written only
with one sentence of the form "an implementation that breaks the row this way still passes every
tagged test" and the `file:line` that shows why the tests miss it. Without one, it goes under
`## Notes` below the table and outside the verdict: the burden of proof is the verifier's.

**Ambiguous parts stay out of the verdict.** Where a row's text sets no value or bound, so no
assertion can be chosen, the verifier lists that part under `## Ambiguous` below the table and
judges the row on what its text does set. The main session carries each line, with its row ID, into
the P4 in file as a check (full lane) or into the PR body's `## Remaining findings` (short lane); in
QA, into the B5 scenario rows and the B6 report. It is not asked of the owner on its own.

**Shape.** Every row gets a line, carried ones too; the verdict cell holds one word (COVERED, WEAK,
MISSING, LIVE_OK, SHOULD_TEST) and nothing after it. The last column starts with its mark:

| case | verdict | evidence | last column starts with |
|---|---|---|---|
| carried | the previous word | the previous evidence | `carried r<N-1>` |
| carried, change read | the previous word (WEAK with a counterexample) | the previous evidence and the change | `carried r<N-1> · change read` |
| closed | COVERED or LIVE_OK | the new evidence | `closed r<N-1>` |
| not closed | WEAK or MISSING | the evidence now | `not closed r<N-1>`, then the missing assertion |
| re-judged | as judged | the new evidence | `re-judged` |

Append this to the first review's prompt:

```
This is re-review r<N>. Verdict words and output shape stay as above; only this differs.
<abs snapshot>/prev/ holds every earlier answer; the reference is the last one, r<N-1>.
<abs snapshot>/rows.diff and <abs snapshot>/changed.diff are the changes since the sha r<N-1> read:
the workplan rows, and the tagged tests with their helpers.
Sort each row (re-judge · carry, read the change · carry) from those diffs, then take the first that applies:
1. A row r<N-1> called WEAK, MISSING or SHOULD_TEST: closed (COVERED or LIVE_OK, file:line, last
   column "closed r<N-1>") or not closed (WEAK or MISSING, last column "not closed r<N-1>" and the
   missing assertion). Unsure is not closed.
2. A re-judge row: judge it from scratch; unsure is WEAK; last column "re-judged".
3. A carry row: copy r<N-1>'s word and evidence; last column "carried r<N-1>", or
   "carried r<N-1> · change read" after reading changed.diff for it.
One word in the verdict cell. A line for every row, carried ones too.
A new WEAK on a carried or closed row needs a counterexample: one sentence "an implementation that
breaks the row this way still passes every tagged test" and the file:line showing why the tests
miss it. Without one, write it under ## Notes, outside the verdict.
Where a row's text sets no value or bound to assert, list that part under ## Ambiguous and judge
the row on what its text does set.
```

## Words for people

`SOUND` reads like a severity grade to a person (an owner took it for one, other project,
2026-11-26). Writing addressed to people — the chat, a decision page, a blocking question, a PR
body, a report — uses these words. The verifier's answer, the round records, the handoff files and
this page keep the marks as they are.

| mark | words for people |
|---|---|
| `SOUND` · `UNSOUND` | 커버리지 충족 · 커버리지 미충족 |
| `COVERED` · `WEAK` · `MISSING` | 완전 커버 · 부분 커버 · 미커버 |
| `LIVE_OK` · `SHOULD_TEST` | 실기로 확인할 항목 · 부분 커버 |

## What the machine does not see

- How strongly a test asserts its row. That is the verifier's job; a tagged test weakened after the
  review is invisible to both, so a big change to a tagged test asks for the review again.
- Text of a row beyond its line. Write each row as one line.
- Whether a `(live)` row was actually run. `live-verify` lists them; a live verifier raises a missing one.
- A tag in a test that a runtime `pytest.skip()` or `pytest.importorskip()` inside its body skips
  (unconditional and constant-condition marks are seen; a `skipif`/`xfail` with an expression or a
  string, and a mark through an alias, count as proof), or in a comment of the wrong test; the
  verifier calls those `WEAK`.
- A row that was never written. That is plan verification's and the owner's job.
- Whether a re-review kept its order: a WEAK without a counterexample, a carried row quietly judged
  again. A person reads the last column's marks and the counterexample sentences.
- Whether a helper change really weakened a carried row's assertion: the diff shows the change, and
  when the verifier finds no counterexample the row is carried.
