---
name: qa-defect-verify
description: B5 of one fixed finding — isolated verifiers who did not make the fix run its scenarios on their own test servers and read the diff, round by round, until the canon's exit condition; the main session records each round on the PR and posts `verified:`. Use it after B4's PR is green in `rcm/ci`. Not for reviewing planned work (`dev-loop` P3) and never by the session or agent that wrote the fix.
---

# QA defect verify (B5)

The question is not "does the code look right" but "is the reported symptom gone, and is nothing next to it
broken". Severity, rounds, quorum, round-3 analysis, round-4 report and the final triage are the canon in
[isolated-verifier.md](../../agents/isolated-verifier.md). This file says how a QA round is set up.

## Who verifies

- A finding observed by running rcm (a command, the web page, an API answer) is verified by
  **`live-verifier`s** ([agent](../../agents/live-verifier.md)): they run the scenarios and judge from what
  they observe. Every round after a fix is a run round too — a fix checked only by reading is not
  verified (lesson from another project, 2026-09-23).
- A finding that only reading shows (a wrong condition with no reachable runtime symptom yet) is verified by
  **`isolated-verifier`s** reading the diff against the scenarios.
- Never the session or the lead that made the fix, and never a verifier that saw another's verdict.

## Before each round

- **Freeze the head.** `rcm/ci` green on it. Nothing is pushed to that branch until the round has ended; a fix
  made during a round voids that round, and the next one is counted after it.
- **Fresh test server per verifier.** Pick a free port and a new data folder for each `live-verifier` by
  [test-server.md](../_shared/test-server.md) and put both in its prompt — state left by the previous
  verifier (tokens, jobs, blobs) changes what the next one sees first. Verifiers of one round run **one
  after another**; independence comes from separate contexts, not from separate machines.
- The build is the fix worktree's own `.venv` at the frozen head, used read-only.

## The prompt

Fill it in; do not summarise the finding and do not add your own opinion of the fix.

```
You are an isolated verifier for one finding in remote_ci_monitor, round <N>, head <40-char sha>.

The finding, exactly as reported:
<verbatim>

Expected behaviour and its source:
<statement> — <user | plan | workplan | doc | code>: <ref>

A fix for this was just made. You did not make it. Look for what is still wrong.
If you cannot confirm something, it is FAIL. You will not see other verifiers' results; do not look for them.

Read:
- the scenario table: <abs path>
- the diff: <abs path of `git diff <base>...<head>` output> (files: <list>)
- the B2 recipe and the control: <abs paths>
- .claude/agents/isolated-verifier.md — severity, output shape

Run (live-verifier only): your test server per .claude/skills/_shared/test-server.md, port <port>,
data_dir <abs data_dir>, built from <abs worktree>/.venv at <head>. Run the B2 recipe first, then every
scenario row; keep each command, exit code and output as files inside your data_dir.

Do:
1. Core: is the reported symptom gone? Judge from what you ran or read, never from "the code looks fixed".
2. Every scenario row: action → observed → PASS / FAIL, with evidence.
3. Non-regression: the other paths that use the changed code still behave as before.
4. The regression test: would it go red if the defect came back? A test that stays green when its target is
   removed is FAIL.
5. New defects in the diff on the three review axes of isolated-verifier.md.

Output: env-before and env-after as your agent file says (live-verifier), the table
| # | row | action | observed | evidence (file:line, command, exit code) | severity | PASS/FAIL/UNKNOWN |,
`summary: high=<n> medium=<n> low=<n> unknown=<n>`, then one line `core: yes | no | unsure`, then the
path of your data_dir. No raw logs: excerpts only, tokens cut to 4 characters, home paths as <HOME>.
```

Unsure on the core line counts as no; an `UNKNOWN` row is not a pass. Collect each verifier's evidence from
its data folder into the round folder before that folder is removed.

## After each round

- Count per the canon: a finding raised by either verifier counts; a tie is a defect; `core` must be `yes`
  from both for the round to pass.
- MED and LOW are carried, not fixed during rounds. A MED or LOW from an earlier round that nobody fixed is
  counted again in this round's numbers; it leaves the count only when it is resolved with a reason (fixed —
  commit or test; withdrawn — why; moved to a new finding — its id). Writing it as 0 makes it vanish.
- Post the round on the PR, exactly as the canon writes it:
  `verify-round: <40-char sha> r<N> verifiers=<n> high=<n> medium=<n> low=<n>`.
- HIGH: back to [qa-defect-fix](../qa-defect-fix/SKILL.md) B4 with the findings verbatim in a new lead round;
  new head, `rcm/ci`, next round.
- Round 3 still HIGH: the canon's cause analysis by one fresh verifier, `verify-analysis:` comment, ⛔ if it is
  a policy conflict. Round 4 still HIGH: report to the owner, no round 5.
- Passed: the canon's triage of the last round's MED and LOW (fix the 3–4 point ones, `rcm/ci` green again,
  list the rest in the PR body's `## Remaining findings`), then
  `gh pr comment <N> --body "verified: <40-char head>"`. A push after it makes it stale.

Keep every verifier's full answer as `verify-r<N>-<id>.md` in the round folder; the report quotes the counts
and the core lines from them.

## Never

- Tell a verifier where to look ("just check the exit code"): one verifier per axis is no majority.
- Show a verifier another verifier's table, or your summary of it.
- Give a verifier your conclusion, the lead's `notes` or the cause as a fact; the cause goes in only as the
  scenario rows already state it.
- Run destructive steps on anything but the verifier's own test server.
