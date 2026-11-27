# Stage leads — the heavy stages go to a fresh agent, by file

The main session talks to the owner, spawns verifiers, pushes, waits for `rcm/ci`, writes the decision
page and merges. Heavy stages go to **one fresh lead per stage**, which returns a result file and is
closed: every later call re-reads the main session's context. `plan-lead` writes P1's material;
`build-lead`'s modes are `implement` (P2), `gap-fix` (P6), `batch` (a dev-batch PR), `qa-fix` (a defect).

## Handoff location and names

Everything handed over lives in `.verify/handoff/<branch with / as ->/` of the worktree (git-ignored,
never `git add -f`), not the scratchpad: parallel sessions overwrote each other's files there (#220, #233).

| file | written by | content |
|---|---|---|
| `<stage>-<n>-in.md` | main session, before the spawn | the brief below |
| `<stage>-<n>-progress.md` | lead | one appended line per RED seen, per commit, per mutant (command · file · sha) |
| `<stage>-<n>-out.json` | lead | the result below, also as its final text (`P1` also writes `P1-<n>-material.md`) |
| `<stage>-<n>-stopped.patch`, `-untracked.tar`, `-untracked/` | main session | work of a lead that stopped (below) |
| `<stage>-<n>-redgreen.json`, `<stage>-<n>-prove.md` | main session | the receipt (below); `regression-test-author`'s `prove` answer |
| `snap-<n>/`, `diff-<n>.patch`, `search-<n>.txt` | main session | what a read-only agent gets (below) |
| `trace-review-<n>.txt`, `edge-audit-r<N>.json` | main session | the plan-to-test verdict ([`scenario-trace.md`](scenario-trace.md)); P5's merged array (`edge-audit`) |

`<stage>`: `P1 P2 P6 batch qa-fix`; `n` only goes up, nothing is overwritten. The prompt opens with
`stage lead · stage: <stage> · mode: <mode>` and the in file's absolute path (it gets the agent id).

## The in file

1. **Owner's words, verbatim and dated**, carried forward (only here do they survive compaction).
2. **Previous outputs** — paths: workplan, decision answers, the last `out.json`, verifier findings.
3. **Scope** — `owns` and `shared` paths, and what the lead must not do.
4. **State** — absolute worktree path, branch, base, start commit (40 chars). The tree is clean: the
   main session commits its own changes and any test `regression-test-author` left, as they are, and
   takes a stopped lead's work out first (below), naming its patch and tar here.
5. **Already verified** — tests and mutants already run, so they are not run again.
6. **`gap-fix` only** — the confirmed GAP table (`file:line`) and the redesign target, or "none".
7. **Attribution** — the commit trailer lines given at the main session's start, verbatim.

## What a read-only agent gets

`isolated-verifier` and `scenario-author` have Read, Grep and Glob only, no `git`: the main session
prepares these in the handoff folder `<h>` and names them by absolute path, rules included
(`<h>/snap-<n>/AGENTS.md`) — a relative one resolves wherever the agent started. The snapshot of the
pinned sha: `mkdir -p <h>/snap-<n> && git -C <wt> archive <40-char sha> [paths…] | tar -x -C <h>/snap-<n>`;
the diff: `git -C <wt> diff <base>...<head> > <h>/diff-<n>.patch`; a history search
(`git log --grep=<word>`, a `CHANGELOG.md` or report grep): `<h>/search-<n>.txt`. `n` only goes up.

## The out.json

```json
{"stage": "P2", "n": 1, "status": "complete|partial|blocked", "branch": "", "base": "", "head": "",
 "commits": [], "files": [], "red": [{"test": "", "cmd": "", "observed": ""}],
 "tests": [{"cmd": "", "result": ""}], "docs": [],
 "questions": [{"kind": "EXTERNAL|POLICY|RESOLVED", "q": "", "default": "", "why": ""}],
 "scope_corrections": {"owns_add": [], "why": ""}, "workplan_diff": [], "notes": ""}
```

- `complete` needs a non-empty `red` for P2, P6, batch and qa-fix. A POLICY question, or a blocking
  EXTERNAL one, means `blocked`; RESOLVED is not asked, its `why` carries the answering `file:line`.
- Shas are 40 characters; unknown is `—`. `workplan_diff` uses the columns of `workplan-diff.md`.
- "Done" is `rcm/ci` and isolated verification, never the lead. The main session reads this JSON and
  its products, checks `git status --porcelain` is empty, and reruns no test but the receipt below.

## The red-then-green receipt — main session only

`tools/redgreen.py` adds worktrees outside the lead's (rule 4), so the main session runs it, after the fix:

```sh
cd <abs worktree> && ./.venv/bin/python tools/redgreen.py --fix <40-char fix sha> --json \
  --test <node id> [--test <node id> …] > .verify/handoff/<branch with / as ->/<stage>-<n>-redgreen.json
```

`--fix` is the first commit on which the red tests pass: the fix right after its test commit, or one
commit holding both (the tool lays the fix's test files on its parent). Nothing is squashed (rule 5):
a fix that still spans commits gets one receipt per row, against the commit that makes that row pass.
PRs and reports cite the receipt, not `red`. A small HIGH the main session fixes itself (P3, P4)
follows the same order: red test committed first, the fix next, this receipt. Exit codes:

- 0 red-then-green; 1 not — the only exit that sends the work to a new lead round; 2 usage: fix the
  command and rerun; 3 could not decide, never a pass: find why (a node id typo, a timeout, a skipped
  test) and rerun; 130 / 143 interrupted (its worktrees removed first): no receipt, rerun.
- 4 cleanup failed, and the exit hides any verdict: only a JSON `verdict` of `red-then-green` passes.
  Find the leftover with `git worktree list` (the receipt redacts its path), remove it, rerun.

## Rules for every lead

1. **Leads never spawn verifiers** — no agent judges work it made or commissioned (no `Agent` tool).
2. **Long waits are the main session's** — no push, `gh pr` or `tools/merge_pr.py`; no `rcm run` or
   `rcm wait` against production or for `rcm-ci` (a subagent whose reply ends while waiting is done).
   On a test server of `test-server.md`, `rcm run … --no-wait` and `rcm wait --job <N> --timeout <seconds>`
   are allowed. Until a lead is known stopped (below), the main session leaves its git alone.
3. **No owner decisions** — questions go into `questions`; the decision page is the main session's.
4. **Nothing outside the worktree** — never production (`tools/guard_production.py`), never another
   worktree, not even to read. A live run follows `.claude/skills/_shared/test-server.md`.
5. **Git stays simple** — the whole list: `git add <paths>` and `git commit` on its own branch
   (`plan-lead` does not commit); `git apply --allow-empty <patch>` for a stopped lead's work;
   `git restore <file>`, `git apply -R <patch>` or `git checkout -- <file>` to undo its own change or
   mutant; read-only `status`, `diff`, `log`, `show`, `rev-parse`. Nothing else — never switch,
   rebase, reset, merge, cherry-pick, stash, clean, `commit --amend`. Never an edit to the frozen workplan.
6. **No heavy commands** — changed tests, `./.venv/bin/ruff` and `node --test` on changed files, one
   `./.venv/bin/python scripts/mutcheck.py --only <name>` per mutant added. Full `pytest`, mutcheck and
   `scripts/smoke_install.sh` are `rcm-ci`'s (2026-11-20: local runs turned timing tests red).
7. **One call, one place** — `cd <abs worktree> && …` in one Bash call (the cwd resets; `cd ""` stays
   put), `git -C <wt> rev-parse --show-toplevel` first; no zsh-only syntax, no `rm -rf`. The shell is
   zsh, so an unquoted `$F` stays one word: pass a file list as `set -o pipefail && … -z | xargs -0 -r`.

## When a lead stops or is needed again

| situation | next |
|---|---|
| `blocked` or questions | decision page; then `SendMessage` to the same lead with the verbatim answer, or a new in file and a new lead if it cannot be resumed |
| the answer or a verifier changes the frozen workplan | a new `plan-lead`, plan verification and approval again, then a new lead; the blocked one is not resumed |
| `partial`, plan HIGH, plan-to-test UNSOUND, `rcm/ci` red, a GAP left by re-verification | what remains, or the findings (HIGH only for a plan), go into a new in file for a new lead |
| a lead stopped, crashed or the session lost it | first make sure it is stopped: its completion notification came, or `TaskStop` stopped it, and its progress file no longer grows (same size a minute apart). Then, before anything else, save its work and restore the tree with the block below (untracked files are moved into `<h>/<stage>-<n>-untracked/`, never `rm -rf`); a new lead, both paths in its in file, applies them first: `git apply --allow-empty <patch>` (an empty patch otherwise exits 128), then `tar -xf <tar>` in the worktree |
| session compacted | reopen the `SKILL.md` you were running and the section of the current stage, then read the highest-`n` files; `out.json` complete means the main session's step is next. After compaction only the start of a skill comes back, so the stage's steps are gone until reopened (other project, 2026-11-25) |

```sh
git -C <wt> diff --binary HEAD > <h>/<stage>-<n>-stopped.patch
cd <wt> && git ls-files -z --others --exclude-standard | tar --null -T - -cf <h>/<stage>-<n>-untracked.tar
git -C <wt> restore --staged -- . && git -C <wt> apply -R --allow-empty <h>/<stage>-<n>-stopped.patch
cd <wt> && U=<h>/<stage>-<n>-untracked && tar -tf "$U.tar" | while IFS= read -r f; do
  mkdir -p "$U/$(dirname "$f")" && mv "$f" "$U/$f"; done
```
