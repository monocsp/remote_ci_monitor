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
| live lane (P1 mark, P5 `edge-audit`) | a rare edge is never picked, and the "cannot be undone" list above is the risk test; «Live lane» below |

## Live lane

Reading the code covers many edges and runs none. The live lane runs a few edges for real in P5, on a
test server built from the PR's head, by `live-verifier`s. Each run costs a server, a worker and
minutes, so only an edge that is both reachable and costly when unhandled earns one; running an edge
nobody reaches proves nothing a reader needs. This section is the canon for which edges go in; [`edge-audit`](../edge-audit/SKILL.md) «Live lane»
is the procedure.

An edge goes in only when all three hold:

1. **Not rare.** None of ①②③ above applies. An edge reported before (the history search) is
   common, so it qualifies.
2. **High risk.** Left unhandled, its result is on the "cannot be undone" list above, or is HIGH 1 or
   2 of [`isolated-verifier`](../../agents/isolated-verifier.md) «Severity» (a traceback, a dead
   server or worker, a job silently lost or left in the wrong state, a wrong exit code). No other
   test of risk.
3. **Made the same way every time with the repository's own means** — the table below. An edge that
   needs a moment no command sets (a claim landing between two heartbeats, a cancel in the middle of
   a retry) stays with the code reading and with unit tests that drive an injected clock. A condition
   a command holds in place (the server stopped while a worker runs) is not timing, and qualifies.

The live lane takes at most five rows per PR, rows the P5 round adds included. Past five, keep the
riskiest and cut the rest; the cut rows stay with the code reading and the tests. The lane runs only
on test servers: a step that could only run against production is not a lane row.

| condition | means | described in |
|---|---|---|
| the server restarts while a job runs, waits or uploads | SIGTERM to the test server's recorded pid, then start it again | `_shared/test-server.md` «Parallel servers and cleanup» |
| a worker is killed, stopped or restarted | `kill -9` or SIGTERM to the test worker's recorded pid, then start it again | `_shared/test-server.md` «Parallel servers and cleanup»; `tests/test_e2e_worker.py` (`WorkerProc`, `Stack`) |
| a worker or a client loses the server | stop the test server while the worker runs or while `rcm wait --job <N> --timeout <seconds>` waits; start it again | `_shared/test-server.md` |
| ctrl-C during `rcm run` | SIGINT to the client's recorded pid | `_shared/test-server.md` «Parallel servers and cleanup» |
| `--no-wait`, then `rcm wait` later | the two commands one after the other | — |
| two sessions submit the same tree | two `rcm run … --no-wait` one after the other | — |
| a job's exit code, marker or very long output | a fake preset: `sh -c` printing a marker and an exit code | `live-verify` §1 |
| what a notify hook receives | a fake hook that writes its environment to a file | `live-verify` §1 |
| the web page at phone width, both languages | the CDP helper of `tests/test_web_browser.py`, 390 px measured inside the page | `live-verify` §2 |

An edge that needs a means not in this table fails condition 3. The lane does not build a tool for it.

Where it is written:

- **P1** ([`workplan`](../workplan/SKILL.md)). An edge row that meets all three gets, in its `where`
  cell, `live lane · <means> (live)` — for example `live lane · kill -9 the worker (live)`. P4 does
  not run such a row; P5's lane does, on the same head. Rows cut by the limit are one line under the
  table: `Live lane cut (limit): <ID> · <why it is less risky than the rows kept>`.
- **P5** ([`edge-audit`](../edge-audit/SKILL.md) «Live lane»). It starts from the P1 rows and may add
  an edge the round found or left `NEEDS_RUN` that meets all three, within the same limit. A
  workplan without marks gets its rows picked by P5 by the same rule.

## What does not change

Severity, rounds and what is fixed during them stay as `.claude/agents/isolated-verifier.md` says. This
page decides only whether an edge is written and whether P5 confirms it as a gap. Something the owner
actually reported is a defect whatever its reach.
