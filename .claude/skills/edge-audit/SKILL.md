---
name: edge-audit
description: P5 of the development loop for remote_ci_monitor — two isolated verifiers, in parallel, read the code of a PR's head against the workplan's edge list and judge each edge HANDLED, GAP, INTENDED, NEEDS_RUN or RARE with `file:line` evidence and a root-cause theme, in JSON; the main session merges the verdicts and hands confirmed GAPs to `build-pr` gap-fix mode. Use it on a full-lane PR after `rcm/ci` is green and P3 has closed. Not for fixing anything (this stage only reads), and not as a replacement for a live run (`live-verify`).
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
2. A `git archive` snapshot of the head (`snap-<n>/`) and the diff
   (`git -C <wt> diff origin/dev...<head> > diff-<n>.patch`).
3. The history search for the edges' keywords (`git log --grep`, `CHANGELOG.md`,
   `docs/acceptance/reports/`), which edge-scope asks for, as `search-<n>.txt`.
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
- NEEDS_RUN is not a resting state: it is settled by a test in P6 or a row in
  [`live-verify`](../live-verify/SKILL.md) before the PR closes.

Save the merged array as `edge-audit-r<N>.json` in the handoff folder
([`_shared/stage-lead.md`](../_shared/stage-lead.md); not committed). Post the round record the
canon gives, and put the confirmed GAP table (ID, verdict, `file:line`, theme, severity) in the PR
body under `## Edge audit`.

## What happens next

- Confirmed GAPs go to [`build-pr`](../build-pr/SKILL.md) gap-fix mode whatever their severity
  (RARE excepted), grouped by `root_cause_theme`. A theme that already appeared in an earlier round is
  marked as a redesign target in the in-file.
- Rounds, closing, escalation: [`isolated-verifier`](../../agents/isolated-verifier.md). MED/LOW that
  are not confirmed GAPs go to triage.
- Every `harness_feedback` other than `none` goes into the loop summary as a follow-up.
