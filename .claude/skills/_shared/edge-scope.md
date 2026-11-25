# Edge scope — which edge cases enter a plan

An edge enters a workplan's test table, a scenario author's table or an edge audit by **what has to
come together for it to happen**, not by a guess at how often it happens (nobody can measure that).
Why a rule at all: without one, every audit adds every edge it can think of, every confirmed gap must
be closed, and the loop drowns in low-value gaps (provisional, measured elsewhere; the owner
confirms).

## Common or rare

**Common** — one ordinary action or event produces it. For rcm: the server restarts or is reloaded;
a worker restarts, loses the network or comes back; the session loses the server mid-`rcm wait`;
ctrl-C during `rcm run`; `--no-wait` and a later `rcm wait`; two sessions submit the same tree; a
slow or large upload; a full or nearly full disk; an empty queue, one job, many jobs; a very long log
or many steps; Korean text in a narrow card; an older server or client than this build (mixed
versions are normal: missing keys, missing features); a job that never prints a marker; clock and
time-zone differences between machines.

**Rare** — at least one of:

1. **Two unrelated conditions at once.** Example: the server restarts during an upload while the
   disk fills up.
2. **A timing no one produces.** A window that neither a person nor rcm's own loops (worker polling,
   heartbeat, janitor, scheduler) hit in normal running. Races *between* rcm's own loops are not
   rare: those loops run thousands of times a day, so a claim racing a heartbeat is common.
3. **A dependency breaking its contract.** SQLite, git, the OS or GitHub answering outside what they
   document. Not rare when that interface has already drifted once in rcm's history.

Rare still goes in when:

- **The result cannot be undone:** a job lost or left in a wrong state, a database corrupted or
  migrated by a different build, artifacts, shelf files or blobs deleted while still needed, a token
  or secret exposed, production touched, the wrong sha merged, a wrong exit code that makes a gate
  pass. Every place that applies this page uses this list.
- **It has been reported before:** search `CHANGELOG.md`, `docs/acceptance/reports/`, PLAN.md and
  `git log --grep=<keyword>` first. Reported once means it is common.

**Unsure means common**, so a real defect does not slip out as "rare".

## Writing an exclusion

A rare, reversible edge is not a row. It becomes one line under the table:

```
Excluded (rare): server restart during an upload while the disk fills · ① · reports searched, 0 hits
```

A line without its condition number (①②③) is not an exclusion; a verifier puts it back as a row.
The history search is run by whoever has a shell: `plan-lead` searches itself. For the edge audit the
main session runs it before the spawn and passes the result as a file (`search-<n>.txt`,
`.claude/skills/_shared/stage-lead.md`): the auditors have no `git`. A scenario author, who must not
read the case's cause and fix, writes the condition only; the parent that copies the table searches
and returns any match to the author as a row to write.

## Where it applies

| place | what it does |
|---|---|
| P1 workplan (`plan-lead`) | filters edges when writing the test table; exclusion lines under it |
| P5 edge audit (`isolated-verifier`) | a rare, reversible edge is `RARE`, not `GAP`, with its condition; `RARE` has no severity, is not counted, and is not fixed by P6 or triage; how votes on it merge is the canon's «Quorum» |
| scenario author (`scenario-author`) | filters before a row goes in; "do not pick only what will pass" still holds: the filter is reach, not outcome |
| QA fix (`qa-defect-fix`) | the same filter for the scenario rows of a defect |

## What does not change

Severity, rounds and what is fixed during them stay as `.claude/agents/isolated-verifier.md` says. This
page decides only whether an edge is written and whether P5 confirms it as a gap. Something the owner
actually reported is a defect whatever its reach.
