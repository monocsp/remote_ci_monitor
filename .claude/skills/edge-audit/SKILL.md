---
name: edge-audit
description: P5 of the development loop for remote_ci_monitor — two isolated verifiers, in parallel, read the code of a PR's head against the workplan's edge list and judge each edge HANDLED, GAP, INTENDED, NEEDS_RUN or RARE with `file:line` evidence and a root-cause theme, in JSON, then live-verifiers run the few common, high-risk, reproducible edges on test servers (the live lane); the main session merges the verdicts and hands confirmed GAPs to `build-pr` gap-fix mode. Use it on a full-lane PR after `rcm/ci` is green and P3 has closed. Not for fixing anything (this stage only reads), and not as a replacement for a live run (`live-verify`).
---

# Edge audit (P5)

Tests and live runs check the paths someone thought of and can reach on demand. Restarts at the wrong
moment, two lanes claiming at once, a worker that comes back after it was declared lost — those are
read for in the code. This stage reads; it changes nothing.

## Input

The main session assembles, for the PR's head sha, as files in the handoff folder (the verifiers
have no `git`; [`_shared/stage-lead.md`](../_shared/stage-lead.md) «What a read-only agent gets»):

1. The edge list: the workplan's edge rows for this PR (§7 IDs), its §9 risks, and the irreversible
   results that [`_shared/edge-scope.md`](../_shared/edge-scope.md) always keeps.
2. A `git archive` snapshot of the head (`<h>/snap-<n>/`) and the diff
   (`git -C <wt> diff origin/dev...<head> > <h>/diff-<n>.patch`), `<h>` the absolute handoff folder.
3. The history search for the edges' keywords (`git log --grep`, `CHANGELOG.md`,
   `docs/acceptance/reports/`), which edge-scope asks for, as `<h>/search-<n>.txt`.
4. What P3 and P4 already settled on this head, marked as such, so it is not judged twice.

## Run

The canon's P5 quorum of [`isolated-verifier`](../../agents/isolated-verifier.md)s, **in parallel**
(one message — they only read, so they cannot disturb each other), each with the prompt below and
nothing else: not your expectation, not the other's output. Every path in it is absolute, inside the
snapshot or the handoff folder ([`_shared/stage-lead.md`](../_shared/stage-lead.md)).

```
You audit edge cases of remote_ci_monitor at <40-char head sha>. Read only (Read, Grep, Glob) the
snapshot <abs snap-<n>/>; change nothing. Rules of the house: <abs snap-<n>/>AGENTS.md and
<abs snap-<n>/>PLAN.md 「fail-open 금지」.
This is the edge audit (P5) of your agent file: its verdicts and its JSON shape.
Workplan: <abs snap-<n>/>docs/<code>-workplan.md (§7 rows, §9 risks). Diff: <abs diff-<n>.patch>.
History search already run for you: <abs search-<n>.txt>.
Edges (id · Given/When/Then):
<list>
Causes that recur in this code base; look for them first:
<the rows of the table below that the diff is near, pasted>.
Also add edges this code misses that are not in the list, filtered by
<abs snap-<n>/>.claude/skills/_shared/edge-scope.md.
Evidence is file:line you opened. No "probably". Unsure means GAP.
For a GAP, `note` gives the failure path: input → wrong result.
`root_cause_theme`: a short name for the cause, the same name for the same cause.
Add one key per item, `harness_feedback`: none|test|mutcheck|doc-lock|guard-hook|skill — what
could have stopped it before a person did.
Output the JSON array only.
```

Do not read the agents' raw transcripts; take only the JSON.

## Causes that recur in rcm — name them in the prompt when the diff is near one

| cause | where to look |
|---|---|
| a state change and its follow-up in separate transactions | `store.py`: the window between them is a lost or doubled job (M5s decision 225: `retried` and the next attempt in one transaction) |
| a clock that is not injected | `time.time()` or `datetime.now()` inside `core/` — pure code takes `now` as an argument |
| restart recovery missing | what `rcm serve` does on start for each new state: `lost` for running local jobs, schedules kept, notify rescan |
| remote vs local asymmetry | a path fixed in `worker.py` but not in `remote_workers.py`/`remote_worker.py`, or the reverse (M5d-0: new host keys dropped by the remote parser, #48) |
| claim, heartbeat, finish and cancel racing | `store.claim`, `worker_finish`, `_owned_active`: two lanes, a late finish, a cancel during a retry, two processes sharing one token |
| a silent no-op on an unknown state | a function returning `None` that the route turns into `200` and the CLI into `0` (M5s §3 #4) |
| fail-open defaults | a missing value shown as `0`, `fine` or success; `rcm wait` turning unknown into 1 |
| reverse DNS or real sleeps in tests | `socket.getfqdn`, `gethostbyaddr`, real timeouts — `AGENTS.md` «Traps» |
| a test that pins keys nobody consumes | a key-set assertion passing while the real consumer never reads the payload — run the real producer into the real consumer |

## Merge the verdicts (main session)

Per edge, by the vote merge row of [`isolated-verifier`](../../agents/isolated-verifier.md)
«Quorum» (ties, RARE, a finding only one raised). What is edge-audit's own:

- GAP against HANDLED: open the `file:line` both cite and confirm which is true before writing it
  down. A finding only one raised (new edges included) that its evidence does not bear out is dropped
  with one line saying why.
- NEEDS_RUN is not a resting state: it is settled by the live lane below, by a test in P6 or by a
  row in [`live-verify`](../live-verify/SKILL.md) before the PR closes.

Save the merged array as `edge-audit-r<N>.json` in the handoff folder
([`_shared/stage-lead.md`](../_shared/stage-lead.md); not committed). Post the round record the
canon gives, and put the confirmed GAP table (ID, verdict, `file:line`, theme, severity) in the PR
body under `## Edge audit`.

## Live lane

The rows: the workplan's `live lane` rows, plus edges this round's merged verdicts left `NEEDS_RUN`
or newly found, when they meet the three conditions of
[`_shared/edge-scope.md`](../_shared/edge-scope.md) «Live lane» (its canon, the limit included). No
row: skip the lane and write `live lane: none` under `## Edge audit` in the PR body.

1. **When.** Once per head, after the reading round's verdicts are merged, on the head P4 ran (P5
   changes no code, so the build is the same). When P6 moves the head, the lane runs again on the new
   head after P4's rerun (if P4 is required), the rows that failed first. A second reading round on
   the same head does not rerun it.
2. **Who.** The canon's quorum of [`live-verifier`](../../agents/live-verifier.md)s for the P5 live
   lane, one after another, each on its own fresh test server from
   [`_shared/test-server.md`](../_shared/test-server.md) with the port and data folder the main
   session picked. None sees another's verdict, the reading verdicts or your expectation.
3. **Merge per row.**
   - PASS needs every verifier's PASS; one FAIL is FAIL. Anything else — PASS with BLOCKED, or all
     BLOCKED — is blocked.
   - FAIL is a confirmed GAP even where the reading said HANDLED or INTENDED: what was seen running
     beats what was read. It goes to P6 with its evidence folder, and P6 locks the same action in a
     pytest (`tests/test_e2e_worker.py` for two processes).
   - PASS is written as `live: passed` and the reading verdict stands. A row the reading confirmed as
     GAP stays a GAP: the run may not have gone down the failing path.
   - Blocked is neither a defect nor HANDLED. A reading GAP stays a GAP; any other row stays
     `NEEDS_RUN`, for P6 to settle with a test. The reason it was blocked is written down.
   - A `NEEDS_RUN` row that passes becomes HANDLED, with `live` and the evidence folder as its
     evidence; one that fails becomes a GAP.
   - When half the rows or more were blocked, say `live lane blocked <n>/<m>` in the PR body and in
     the report to the owner.
   - Each row is counted once, by its final verdict, in the round record and in the GAP table.
4. **Records.** Each row of `edge-audit-r<N>.json` gains `live` (`passed`, `fail`, `blocked` or `—`)
   and `live_evidence` (the folder); each verifier's answer is `edge-live-r<N>-<id>.md` in the handoff
   folder; the round's `verify-round: … stage=P5` counts the lane's FAILs by severity together with the
   reading's.

```
You run the live lane of the edge audit of remote_ci_monitor at <40-char head sha>. You did not write
this code; look for what fails. Judge from what you ran and saw (commands, exit codes, job states from
GET /jobs/<id>, files on disk), never from the code.
Your test server: <abs snap-<n>/>.claude/skills/_shared/test-server.md, port <port>, data_dir
<abs data_dir>, built from <abs worktree>/.venv at <head>. Keep every command, exit code and output as
files in your data_dir.
Rows (id · Given/When/Then · the condition and the means that makes it · what should be observed):
<the rows>
Make each condition only with the means named, and first check that it really happened (the worker
really died, the server really stopped) before you judge the result.
Per row: PASS — the condition happened and the result is as expected (evidence). FAIL — the condition
happened and the result is wrong: a job lost or doubled, a wrong state, a traceback, a wrong exit
code, a silent failure (evidence, and the path action → wrong result). BLOCKED — the condition could
not be made (why). What you could not establish is BLOCKED with the reason, never PASS.
Do not look for other verifiers' results.
Output the JSON array only:
[{"id","result":"PASS|FAIL|BLOCKED","evidence":"path · what was seen","failure_path","severity"}]
severity: high, med or low by <abs snap-<n>/>.claude/agents/isolated-verifier.md «Severity»; null for
PASS and BLOCKED.
```

## What happens next

- Confirmed GAPs go to [`build-pr`](../build-pr/SKILL.md) gap-fix mode whatever their severity
  (RARE excepted), grouped by `root_cause_theme`. A theme that already appeared in an earlier round is
  marked as a redesign target in the in-file.
- Rounds, closing, escalation: [`isolated-verifier`](../../agents/isolated-verifier.md). MED/LOW that
  are not confirmed GAPs go to triage.
- Every `harness_feedback` other than `none` goes into the loop summary as a follow-up.
