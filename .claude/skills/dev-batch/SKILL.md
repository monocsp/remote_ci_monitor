---
name: dev-batch
description: Run several PRs of one milestone as one batch — one build-lead per PR in its own worktree, cut from one pinned base, pushed, verified and merged in order by the main session. Use it when a merged spec has a PR split table with two or more PRs to build at once. Not for a single PR (`dev-loop` or `dev-lite`), not for writing the spec itself (`workplan`), not for defects found after the fact (`qa-batch`).
---

# Dev batch

A milestone spec splits its work into PRs and waves (`docs/m5s-workplan.md` §5 is the shape). This skill
runs one wave of that table at once: a bundle per PR, a lead per bundle, and one main session that owns
everything shared — the batch state, pushes, `rcm/ci`, verification and merges. The owner's instruction is
the reason it exists: 「병렬로 할 수 있는 일은 반드시 병렬로」 (`docs/m5s-workplan.md` §5). M5s merged nine
PRs this way by hand; the tool below adds what that run had to check by eye.

## When

- The spec PR is merged (merged spec = approved plan, frozen). Its split table is the input: PR, branch,
  content, start condition, wave, files shared with other PRs and the merge order.
- Two or more PRs of the same wave can start now. One PR alone goes through `dev-loop` (or `dev-lite`).
- If the table names no paths per PR, write them from the spec's design section first: every bundle needs
  `owns` (files only it changes) and, for registries several PRs append to (`CHANGELOG.md`,
  `scripts/mutcheck.py`, the milestone's doc-lock test), `shared`.

## Who does what

| who | does | never |
|---|---|---|
| main session | batch state, worktrees, in files, dispatch, `scope`, push, PR, `rcm/ci`, spawning verifiers, `verify-round:` / `verified:` comments, merges, re-syncs, the report | implements a bundle or fixes a lead's scope by hand |
| `build-lead` in `batch` mode ([agent](../../agents/build-lead.md)) | red tests from the spec's scenario rows, implementation, docs, commits on its own branch, `out.json` | everything in the lead rules of [stage-lead.md](../_shared/stage-lead.md) (push, `gh pr`, `rcm run`, merges, other worktrees) |
| `isolated-verifier`, `live-verifier` | one verdict each on a frozen head ([isolated-verifier.md](../../agents/isolated-verifier.md)) | edit, see another verifier's verdict |
| owner | answers the one decision page; approves anything that changes the spec | — |

Judgement-free hand work (captures, collecting files) goes to a general agent with `model: "sonnet"`,
`effort: "high"` on the Agent call; without them it inherits the main session's model and effort.

## Invariants

- One bundle = one PR = one branch = one worktree (`../remote_ci_monitor-<what-it-does>`, its own `.venv`).
  A merged bundle is never dispatched again; a follow-up is a new branch.
- Every bundle of a wave is cut from the same pinned 40-character base sha, fetched once: fetching before
  each cut gives bundles different bases.
- `owns` never overlap between unmerged bundles; `tools/dev_batch.py add` refuses it. Shared registries take
  only additions of the bundle's own entries.
- A lead's `complete` is a claim. Done is `rcm/ci` green on that head plus isolated verification passed.
- Full 40-character shas everywhere — in files, comments, commands.
- The head is frozen while it is verified. Fixes wait until the round ends; a fix landed mid-round leaves
  the round verifying a stale head.
- Results come back as files, never as a lead's chat text, which a terminal read can cut short.

## 0 Set up

```sh
git fetch origin                                     # once for this wave
export RCM_DEV_BATCH=~/.local/state/rcm-dev-batch/<batch-id>.json
./.venv/bin/python tools/dev_batch.py init --id <batch-id> --base origin/dev   # prints the base sha
```

The state file lives outside every worktree: a scratchpad dies on restart and a file in a worktree dies with
it. The tool reads its path only from `--batch` or `RCM_DEV_BATCH` — export it in every Bash call, shell
state does not carry over. Run the tool from the main session's own worktree and its `.venv`.

Per bundle: name the branch with the `branch` skill, then cut it from the pinned sha (not `origin/dev`, which
may have moved) and give it its own `.venv`:

```sh
git worktree add -b <type>/<scope>-<what> ../remote_ci_monitor-<what> <base sha>
cd ../remote_ci_monitor-<what> && python3.11 -m venv .venv && ./.venv/bin/python -m pip install -e '.[dev]'
./.venv/bin/python tools/dev_batch.py add --bundle <A> --branch <type>/<scope>-<what> \
  --worktree <absolute path> --owns <path>... [--shared <path>...] [--after <bundle>...] --merge-order <n>
```

`add` refuses an overlap, a glob, an absolute or `..` path and a bare top-level root (`src/`, `tests/`,
`docs/`, `tools/`, `scripts/`, `.claude/`, `src/remote_ci_monitor/`). An overlap means the split is wrong:
fix the table (merge the two bundles or move the file to one of them), never widen `shared` to get past it.

A later wave starts when every PR it depends on has merged: `git fetch origin` once for that wave, cut its
worktrees from the new `origin/dev` sha, and `add` them to the same batch so overlaps with bundles still
open from the earlier wave are refused.

## 1 Dispatch

Write each bundle's in file (`batch-<n>-in.md`) with the Write tool, where and as
[stage-lead.md](../_shared/stage-lead.md) says. For a bundle it also names the bundle, the spec sections and
the scenario IDs this PR proves ([scenario-trace.md](../_shared/scenario-trace.md)) and what is out of scope.

Spawn the leads of one wave in **one message**: one Agent call per bundle, `subagent_type: "build-lead"`,
the prompt's first lines as stage-lead.md gives them (`stage lead · stage: batch · mode: batch`, then the
in file's absolute path). While a lead works, leave its worktree's git alone. The completion notice is the
signal — do not poll, do not wake on a timer: every wake re-reads the main session's whole context.

At most **three implementing leads at once** (provisional, never measured in rcm). This machine is also the
build machine: `rcm-ci` runs on the same CPUs, and a loaded machine turns time-threshold tests red
(2026-11-20). Test servers of live verifiers keep to the parallel limit of
[test-server.md](../_shared/test-server.md); read-only verifiers do not count.

## 2 Take a result

`batch-<n>-out.json` is canon; the lead's final message is not. Accept it only when `stage` and `n` match the
in file, then check it against the tree:

```sh
git -C <wt> rev-parse HEAD            # equals out.json "head"
git -C <wt> status --porcelain        # empty
./.venv/bin/python tools/dev_batch.py scope --bundle <A>   # every touched path inside owns ∪ shared
```

`scope` lists each path a commit since the base (or since the last merge of `origin/dev`) touched outside
`owns ∪ shared`, with the commit sha. Any path it lists is a scope violation (see "When things go wrong").

| status | next |
|---|---|
| `complete` (needs a non-empty `red`) | step 3 |
| `partial` | a new round `n+1` with what is left, the finished commits named |
| `blocked` | park the bundle; collect its `questions` |

Questions from all bundles go to the owner **once**, on one page ([decision-page.md](../_shared/decision-page.md)):
EXTERNAL and POLICY with the lead's default and why; RESOLVED ones are only listed. No answer is not
approval. Answers go into the next round's in file verbatim, with their decision numbers. Other bundles keep
going meanwhile. A `scope_corrections.owns_add` is a question too: the first version of `dev_batch.py` cannot
widen `owns`, so an accepted correction is listed in the report and its `scope` lines are read against it.

## 3 Push, PR, wait for rcm/ci

Only a `complete` head is pushed — each `rcm-ci` run holds the build machine's lane for about 16 minutes, and
a new push to the same branch supersedes the old job.

```sh
cd <wt> && ./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
```

Then push with the pinned account and open the PR exactly as the `pr` skill says (title via `commit`-style
naming, Korean via `korean-writing`, body file `pr-body-<branch with / as ->.md` — parallel sessions
overwrote a shared name, #220 and #233). The body carries the `## Differs from the spec` section of
[workplan-diff.md](../_shared/workplan-diff.md) from `out.json`'s `workplan_diff`.

The watcher posts `rcm/ci` `queued · #<N>` within a minute. Wait for job `N` with `rcm wait --job <N>` as a
background Bash call. No status after two minutes: submit by hand,
`rcm run rcm-ci --source git_ref --ref <branch> --no-wait`, and say so in the PR. `rcm wait` exit 3 is
unknown, not red — look at the commit status again before deciding anything.

Red: read `rcm logs <N>`, paste the failing excerpt (not the whole log) into round `n+1`'s in file and spawn a
fresh `build-lead` on the same worktree. The main session does not patch it.

## 4 Isolated verification

Per PR, the P3 review rounds of [dev-loop](../dev-loop/SKILL.md) on the diff, plus `live-verify` and
`edge-audit` when the PR's lane in the spec (full or short, [workplan](../workplan/SKILL.md) «Lane»)
calls for them. Severity, rounds, convergence, escalation and triage are
[isolated-verifier.md](../../agents/isolated-verifier.md); this skill adds only:

- Freeze the head: no new round for that bundle until the verification round has ended.
- Give each verifier the spec section, the PR's diff as a file (`diff-<n>.patch`) and a `git archive`
  snapshot of the head (`snap-<n>/`), both under `.verify/handoff/<branch with / as ->/` of that worktree
  ([stage-lead.md](../_shared/stage-lead.md) «What a read-only agent gets») — never a moving worktree and
  never your own conclusion.
- Post one `verify-round:` comment per round and `verified: <40-char head>` when it passes, in the formats
  of isolated-verifier.md. A HIGH goes back to the lead as a new round with the finding verbatim; the new
  head needs `rcm/ci` and the next round.
- Triage fixes (what the canon's triage fixes now) are a new lead round `n+1` of the same bundle, never a hand fix;
  the moved head then gets `rcm/ci` and the one-verifier look of isolated-verifier.md «Rounds» 7 before
  `verified:`.

## 5 Merge in order

```sh
./.venv/bin/python tools/dev_batch.py status   # local and remote head, PR, rcm/ci on that head, verified, merged
export GH_TOKEN=...                        # as the pr skill pins it
python3 tools/merge_pr.py <N> --wait --delete-branch
```

Merge by `merge-order`, a bundle only when `status` shows `rcm/ci` green and `verified:` on the same head.
`status` prints `—` and exits 3 when it cannot learn something; that is not a green light.

After each merge:

- Watch `rcm/ci` on the new `dev` head (the watcher submits it). The `dev` ruleset does not require an
  up-to-date branch, so two green PRs can make a red `dev`. **Red `dev` stops all merging** until a fix PR
  from `dev` is merged.
- Re-sync only the open bundles that share files with what just merged, and only while no lead runs there:
  `git -C <wt> fetch origin && git -C <wt> merge --no-edit origin/dev`. Never rebase, never force-push —
  the ruleset refuses it and every `verified:` would point at a sha that no longer exists.
- A conflict confined to registry lines (both sides appended) is resolved by keeping both sides. A conflict
  in behaviour is not resolved by the main session: `git -C <wt> merge --abort`, then a lead round or the
  owner: an auto-resolved conflict becomes approved text nobody checked.
- A re-synced bundle has a new head: `rcm/ci` and verification run again before it merges.

## 6 Clean up and report

Before removing anything, copy each bundle's handoff folder next to the state file
(`~/.local/state/rcm-dev-batch/<batch-id>/`); it lives in the worktree and dies with it. Then, per merged
bundle: `git worktree remove ../remote_ci_monitor-<what>`, and once at the end `git fetch --prune`.

Report to the owner in Korean (`korean-writing`), one block per bundle: PR and merge commit, `rcm-ci` job
numbers, verification rounds with their counts, what differs from the spec, remaining findings and accepted
scope corrections. Then three numbers for the first batches: share of wall time spent waiting for `rcm/ci`,
extra `rcm-ci` runs caused by re-syncs, scope violations. A number you did not measure is `—`.

## When things go wrong

- **A lead stopped without `out.json`** (died, cut off, session restarted). Before anything else, keep its
  uncommitted work as `batch-<n>-stopped.patch` and `batch-<n>-untracked.tar` in its handoff folder, with
  the commands of [stage-lead.md](../_shared/stage-lead.md) "When a lead stops". Then a new lead, round
  `n+1`, gets the commits that exist and both paths.
- **Scope violation.** A new round for the same lead to revert or move the change; never hand-fix it, and
  never re-cut the branch. If the path really belongs to the bundle, it is a scope correction (step 2).
- **No `rcm/ci` after two minutes.** The watcher missed it: submit by hand (step 3); if it happens on every
  push, its log is named in the `pr` skill.
- **`merge_pr.py` exit 3.** GitHub did not answer; the merge may have happened. Look first —
  `gh pr view <N> --json state,mergeCommit,headRefOid` — and retry only if it is still open.
  Exit 1 is a refusal: read the reason (pending check, `verified:` for another sha, conflict).
- **The same failure three times** in one bundle, or more than three verify→fix cycles that moved the head
  since the owner last looked: stop that bundle and ask ([isolated-verifier.md](../../agents/isolated-verifier.md)).

## What this does not check

`scope` sees paths per commit, not who wrote them: bundle A editing a file in B's `shared` passes. It does
not see another worktree's contents, a change made and reverted between two commits, ignored files, or what
a lead read outside its worktree. It does not check that `shared` registries only gained entries. Two bundles
with disjoint files can still conflict in behaviour — only `rcm/ci` on `dev` after each merge catches that.
