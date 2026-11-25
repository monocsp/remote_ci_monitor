---
name: dev-loop
description: Route one milestone or feature of remote_ci_monitor through the development loop — workplan, build, review, live run, edge audit, gap fix, merge — by reading where it stands (the workplan's §6 checklist, open PRs, `verify-round:` and `verified:` comments, `rcm/ci`) and starting the next stage. Use it for "build this", "what's next", "where are we", "keep going". Not for a single named stage (call that skill), a small change (`dev-lite`), several PRs of one wave at once (`dev-batch`) or a defect found after the fact (`qa-batch`, `qa-defect-loop`).
---

# Development loop (router)

This skill decides **which stage comes next and who does it**. It does not hold any stage's steps —
those live in the stage skills, and every shared rule lives in one canon file (below). Copying a
stage's steps here is how the two drift apart.

The main session runs this skill. It talks to the owner, starts leads and verifiers, pushes, waits
for `rcm/ci`, posts the round records and merges. It does not write the plan or the code of P1, P2
and P6 itself; why is in [`_shared/stage-lead.md`](../_shared/stage-lead.md).

## The loop

| stage | skill | done when |
|---|---|---|
| P1 workplan | [`workplan`](../workplan/SKILL.md) | the spec PR is merged (merge = owner approval, contracts frozen) |
| P2 build | [`build-pr`](../build-pr/SKILL.md) (implement) | lead's out.json `complete`, plan↔test check SOUND, `rcm/ci` green on the head |
| P3 review | this file, «P3 review» | a round with HIGH 0 on the head |
| P4 live | [`live-verify`](../live-verify/SKILL.md) | only when required; a round with HIGH 0 and the report committed |
| P5 edges | [`edge-audit`](../edge-audit/SKILL.md) | a round with HIGH 0; confirmed GAPs handed to P6 |
| P6 gap fix | [`build-pr`](../build-pr/SKILL.md) (gap-fix) | every confirmed GAP closed (HANDLED + a test that locks the path) |
| triage | [`isolated-verifier`](../../agents/isolated-verifier.md) «triage» | last-round MED/LOW scored; 3–4 points fixed, the rest in `## Remaining findings` |
| P7 merge | [`pr`](../pr/SKILL.md) | `verified: <head sha>` posted, `tools/merge_pr.py` merged, worktree removed |

A workplan's §5 gives each PR a lane, **full** or **short**; which stages each lane runs, and when a
PR is full, is defined only in [`workplan`](../workplan/SKILL.md) «Lane», and the owner picks it on the
P1 decision page. A change too small for a workplan goes to [`dev-lite`](../dev-lite/SKILL.md); a wave
of two or more PRs goes to [`dev-batch`](../dev-batch/SKILL.md), which runs this loop once per PR.

## Canon — read these, do not restate them

| subject | file |
|---|---|
| severity, rounds, convergence, escalation, triage scoring, quorum, `dev-lite` counts | [`isolated-verifier`](../../agents/isolated-verifier.md) |
| handoff files (in/out), where they live, the rules for leads, a lead that stops | [`_shared/stage-lead.md`](../_shared/stage-lead.md) |
| test servers (port, data_dir, HOME, discovery, never production) | [`_shared/test-server.md`](../_shared/test-server.md) |
| scenario IDs, test tags, `tools/scenario_trace.py`, plan↔test verifier prompt | [`_shared/scenario-trace.md`](../_shared/scenario-trace.md) |
| which edge cases enter a plan | [`_shared/edge-scope.md`](../_shared/edge-scope.md) |
| asking the owner | [`_shared/decision-page.md`](../_shared/decision-page.md) |
| what differs from the spec | [`_shared/workplan-diff.md`](../_shared/workplan-diff.md) |

The house rules in `AGENTS.md`, `CONTRIBUTING.md` and `PLAN.md` 「fail-open 금지」 win over anything
here: tests first, never weaken a test, never invent a number (unknown prints `—`), one branch one
worktree, names through the `branch`/`commit`/`pr` skills, merge only through `tools/merge_pr.py`.
No Codex (owner, 2026-11-17: reviews are isolated agents, carried through to the merge).

## 1. Find where it stands

There is no ledger file. The state is already in the repository and on GitHub — a milestone is one
workplan and several PRs, its progress table is the workplan's §6, and `tools/merge_pr.py` already
refuses a merge without `rcm/ci` and `verified:` on the pinned head. Read it fresh every time:

```sh
git fetch origin
git show origin/dev:docs/<code>-workplan.md | sed -n '/^## 6\./,/^## 7\./p'   # §6 checklist
gh pr list --state all --search "<code>" --json number,title,state,headRefName,headRefOid
gh pr view <N> --json headRefOid,comments --jq '.comments[].body' | grep -E '^(verify-round|verify-analysis|verified):'
gh api repos/{owner}/{repo}/commits/<head sha>/status --jq '.statuses[] | [.context, .state, .description]'
ls .verify/handoff/<branch with / as ->/        # last <stage>-<n>-out.json of a lead
```

A workplan that is not on `origin/dev` yet is on its spec PR's branch. After a session break, read
the newest out.json in the handoff folder before anything else
([`stage-lead`](../_shared/stage-lead.md) «a lead that stops»).

## 2. Pick the next stage

Take the first row that matches, per PR in §5 order.

| what you see | next |
|---|---|
| no workplan | P1 |
| spec PR open; plan rounds not closed, or a ⛔ row in §11 without an answer | P1 (rounds) or **stop** (owner) |
| spec merged; the PR's §6 row not started | P2, or `dev-batch` when its wave has two PRs or more |
| lead's out.json `blocked` | answer `RESOLVED` questions from the repo; `POLICY` → **stop** (owner) |
| `rcm/ci` pending on the head | wait (§4) |
| `rcm/ci` failed on the head | P2 again with a new lead and the failing log — a red gate is not a stop |
| green; no `verify-round:` for this head | P3 |
| P3 closed; lane full; live required and no report | P4 |
| lane full; no P5 round | P5 |
| confirmed GAPs open | P6 |
| P3–P5 closed; triage not done | triage |
| triage done, `rcm/ci` green on the final head | P7 |
| merged | tick the §6 row (next PR that touches the workplan), next PR |

A head that moves after P3–P5 closed: triage fixes keep the verdicts (gate green again, no new
rounds — canon); any other code change reopens review on the new head as the next round.

## 3. Stop points — the owner decides, never the loop

- Plan approval: the spec PR is merged only after the owner has answered every ⛔ and the lane
  question. No answer is not approval, and the loop never approves its own plan.
- A change to a frozen contract (schema_version, DB version, wire, exit codes, CLI) after approval.
- An explicit owner request missing or contradicted (always ⛔ — canon).
- Round 3 still HIGH with a policy conflict; round 4 still HIGH; more than three verify→fix cycles
  that moved the head since the owner last looked (canon).
- The plan↔test check UNSOUND three times; the same gate failure three times; the same gap not
  closed three times.
- A release to `main` and anything that touches the production install (`AGENTS.md`).

Every question goes on one [decision page](../_shared/decision-page.md). A stop is reported with
what was tried, what is known, and the default you would pick — then the loop waits.

## 4. Who does what

| stage | lead (writes, commits) | verifiers | main session |
|---|---|---|---|
| P1 | [`plan-lead`](../../agents/plan-lead.md) | [`isolated-verifier`](../../agents/isolated-verifier.md) ×2 per round on the plan | [`scenario-author`](../../agents/scenario-author.md)'s table first, in-file with the owner's words verbatim, decision page, push, spec PR, round records, merge after the owner |
| P2 | [`build-lead`](../../agents/build-lead.md), new | `isolated-verifier` ×1, plan↔tests without the code | worktree, in-file, push, PR, wait for `rcm/ci` |
| P3 | — | `isolated-verifier` ×2 per round on the diff | spawn, tally, fix HIGH (small) or a new lead |
| P4 | — | [`live-verifier`](../../agents/live-verifier.md) ×2, each on its own test server | spawn, tally, report |
| P5 | — | `isolated-verifier` ×2 in parallel | merge verdicts, open single-verifier findings |
| P6 | `build-lead`, new (not P2's) | `isolated-verifier` ×2 re-check | in-file with the GAP table, push, `rcm/ci` |
| triage, P7 | — | — | score, fix, PR body, `verified:`, merge, clean up |

A lead is fresh for every stage and every retry. A small follow-up (a comment, a doc line, one more
assertion) goes to a new agent given only the files it needs, not to the previous lead resumed: a
resumed lead carries its whole context into every call.

### Roles and models (provisional — the owner confirms)

| role | model | where it is set |
|---|---|---|
| main session | the session's own | session |
| `plan-lead`, `build-lead`, `regression-test-author` | opus | agent frontmatter |
| `isolated-verifier`, `live-verifier`, `scenario-author` | sonnet | agent frontmatter |
| hand work without judgement (captures, running a script, copying a table) | sonnet | Agent call: `model: "sonnet"`, `effort: "high"` |
| `korean-writer` | its frontmatter | `.claude/agents/korean-writer.md` |

No Haiku — never measured here. An `effort` in an agent's frontmatter (`plan-lead`,
`scenario-author`) is a provisional value measured elsewhere, which the owner confirms; none was
measured in rcm, and the others carry none. An `effort` passed on the Agent call overrides the
definition, and a `general-purpose` agent with no `model` inherits the main session's, so hand work
always names its model.

### The main session does the long waits

Leads and verifiers never wait for `rcm/ci`, never run `rcm run` and never poll. The main session
pushes, then waits once in the background — `rcm wait --job <N>` or a single `Monitor` until the
status leaves `pending` — and acts on the notification. `rcm wait` exit 3 means *unknown*: look at
the job (`rcm jobs`, `rcm logs <N>`), never treat it as red. While a lead works in a worktree, the
main session does not touch that worktree's git state.

## 5. P3 review

On the head that `rcm/ci` passed: two [`isolated-verifier`](../../agents/isolated-verifier.md)s, one
after the other. The main session first writes, in the handoff folder, the diff
(`git -C <wt> diff origin/dev...<head> > diff-<n>.patch`) and a `git archive` snapshot of the head
(`snap-<n>/`, [`_shared/stage-lead.md`](../_shared/stage-lead.md) «What a read-only agent gets»); each
verifier gets those absolute paths, the workplan sections the PR implements and the PR body — never
your conclusion or the other's verdict. Each looks at all three axes the agent file names. After
each round post the record line the canon gives (`verify-round: <sha> r<N> …`). During rounds fix
HIGH only; MED/LOW wait for triage.

## 6. Loop summary

At P7, and whenever the owner asks "how many rounds", post one PR comment and say it in the chat:

```
| stage | rounds | found H/M/L | fixed | left | last head |
|---|---|---|---|---|---|
| P1 plan | 2 | 1/4/3 | 1/0/0 | 7 → §12 | 1a2b3c4 |
| P3 review | 1 | 0/2/1 | 0/1/0 | 2 → Remaining | 5d6e7f8 |
| P5 edges | 2 | 1/3/0 + GAP 2 | 1/0/0 + GAP 2 | 3 → Remaining | 5d6e7f8 |
```

Counts come from the `verify-round:` lines and the triage table, nothing else; a count nobody
recorded is `—`. `left` says where it went (deferred, `## Remaining findings`, §12, a follow-up).

## 7. Meta loop

When a defect class comes back (a second wrong `file:line` in a plan, a second fail-open default, a
second test that locks keys nobody reads), ask what would have stopped it without a person: a test,
a `scripts/mutcheck.py` mutant, a doc-lock test, a guard hook, a line in a skill. Put the answer in
the PR that fixes it, or in the summary as a follow-up for the owner.
