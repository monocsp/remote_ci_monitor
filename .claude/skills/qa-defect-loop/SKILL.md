---
name: qa-defect-loop
description: Take one finding — something the owner or an acceptance run saw rcm do wrong — from "this is wrong" to a closed report, through triage (B1), reproduction (B2), proven cause and test-first fix (B3–B4), isolated verification (B5) and the report (B6). Use it for a single observed symptom. Several findings at once go to `qa-batch`; something to build that nobody has seen fail goes to `dev-loop`.
---

# QA defect loop

The entry is an observed symptom: a command, a page or an API answer that did something other than expected.
The loop does B1 and B2 itself and hands B3–B6 to the stage skills. It never skips a stage, and a finding
that is not fixed still ends in a report.

| stage | where | without it |
|---|---|---|
| B1 triage | here | the wrong place gets fixed, or a "normal" nobody decided |
| B2 reproduce | here | a guess gets fixed and the symptom stays — or something else is fixed and reported as this |
| B3 cause, B4 fix | [qa-defect-fix](../qa-defect-fix/SKILL.md) | the symptom is patched, not the cause; the test does not catch the defect |
| B5 verify | [qa-defect-verify](../qa-defect-verify/SKILL.md) | the one who fixed it closes it |
| B6 report | [qa-defect-report](../qa-defect-report/SKILL.md) | nobody can tell later what happened or why it was not fixed |

Rules that hold throughout: production is untouched (test servers per
[test-server.md](../_shared/test-server.md)); the finding's words are kept exactly as received; a side
defect becomes a new finding; an item the owner asked for explicitly is never closed alone — disagreement is
a ⛔ on the [decision page](../_shared/decision-page.md).

## Record

One finding has one file, `F<n>.md`, in the round folder of `qa-batch`
(`~/.local/state/rcm-qa/<date>-<topic>/`; a lone finding gets a round folder of its own). It accumulates:
the words as received, B1 statements and source, B2 attempts and recipe, then B3–B6 links. Only the main
session writes it. Evidence files sit next to it under `F<n>/`.

## B1 Triage — read, do not build or fix

1. **Split the report into observable statements.** Drop guesses about the cause ("probably the cache") and
   keep four cells:

   | cell | rcm example |
   |---|---|
   | where | `rcm wait`, the web queue, `GET /api/status`, a preset's job |
   | what was done | the exact command, flags, config keys, the sha or version |
   | what was seen | output lines, exit code, job state, the value on the page |
   | what was expected | in the reporter's words |

   If a cell is empty, ask for that cell only, in one line.

2. **Find where it lives.** Command → `cli.py` handler → the module that computes the value
   (`core/`, `store.py`, `server.py`, `remote_workers.py`, `worker.py`), one hop at a time. Write the
   suspected files; `qa-batch` groups findings by them.

3. **Fix the expected behaviour and its source.** Take the first that exists and write where it is:

   | source | ref |
   |---|---|
   | `user` | the owner's sentence, verbatim |
   | `plan` | `PLAN.md` section and decision number |
   | `workplan` | `docs/m5x-workplan.md` § and decision number |
   | `doc` | `README.md` / `docs/*.md` section — a command the docs promise that does not work as written |
   | `code` | `file:line` of a comment or an existing test that locks the contract |

   Open the file and check the line is there before writing the ref. **No source → ⛔**: ask on the decision
   page with two options and a default, and stop this finding. Fixing toward a "normal" nobody decided brings
   the opposite report next round (lesson from another project).

   The same output through a different path can still be the defect: a number that happens to be right but is
   computed where the contract says it is read, or `0` printed where the value is unknown
   (PLAN.md 「fail-open 금지」).

4. **Hypotheses**, at most three, most likely first: the condition (config, data, timing, server/worker
   version, network) and the steps.

## B2 Reproduce — on a test server, not in your head

- Start a test server per [test-server.md](../_shared/test-server.md) from the build under test.
- Run the recipe and save the evidence: the command exactly as run, its exit code, stdout and stderr to
  files (`F<n>/repro-1.txt`), and for the web a screenshot from the CDP helper.
- **Reproduced = the same command, twice, the same result.** Once only is `flaky` — record that as the result;
  a single sighting is not a cause.
- **A control** shows what normal looks like under the same conditions: the neighbouring path that works,
  the same command on the previous release's wheel, or the sentence the docs promise. Without a control the
  finding is not yet a defect.
- Write every attempt, failed ones too — they are the only count of attempts.
- **Three failed attempts → STOP.** Ask the owner for the conditions (version, config, machine, what ran
  just before, every time or sometimes). Do not move on to a guessed fix.
- Do not change code during B2; a temporary log line is noted and removed before B3.
- Write the recipe so a stranger can follow it — every flag, every config key, the order.

Turn the recipe into `scripts/acceptance/<topic>_live.sh` when it will be run again (verification rounds
will), following the existing script in that folder.

## Then

Reproduced → [qa-defect-fix](../qa-defect-fix/SKILL.md) (B3–B4) → PR and `rcm/ci` (`pr` skill) →
[qa-defect-verify](../qa-defect-verify/SKILL.md) (B5) → `verified:` and `tools/merge_pr.py` →
[qa-defect-report](../qa-defect-report/SKILL.md) (B6).

## Closing without a fix

Common, and never silent: the reporter needs "why not" as much as "how". B1 and B6 are never skipped; B3–B5
are skipped only by these closings, each with its own proof:

| closed as | means | needs |
|---|---|---|
| `not_a_defect` | rcm does what was decided | the source (`plan` / `workplan` / `doc` / `code` with the line) **and** the owner's agreement. Never against an owner's explicit words — that is a ⛔ |
| `wontfix` | could be fixed, the owner decided not to | the owner's answer verbatim from the decision page |
| `cannot_reproduce` | three attempts, no symptom | the three attempts with their commands and outputs, and the condition questions asked |
| `duplicate` | another finding or an issue already covers it | its id or PR number, and why it is the same (same statements, same suspected place) |

Each still writes the B6 report, with "why not fixed" and its proof where the cause and fix would be.

## Meta

After closing, ask whether a test, a guard or a doc-lock could have caught this. Write the proposal in the
report. The same symptom a second time is a harness defect, not a code defect.
