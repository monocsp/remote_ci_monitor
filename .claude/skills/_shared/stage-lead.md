# Stage leads — the heavy stages go to a fresh agent, by file

The main session talks to the owner, spawns verifiers, pushes, waits for `rcm/ci`, writes the decision
page and merges. Heavy stages go to **one fresh lead per stage**, which returns a result file and is
closed: every later call re-reads the main session's context (models provisional; a call's `effort` wins).

| stage | agent | mode | model |
|---|---|---|---|
| P1 workplan material | `plan-lead` | — | opus |
| P2 build · P6 gap fix · one dev-batch PR · one QA defect | `build-lead` | `implement` · `gap-fix` · `batch` · `qa-fix` | opus |

## Handoff location and names

Everything handed over lives in `.verify/handoff/<branch with / as ->/` of the worktree (git-ignored),
never `git add -f`, never the scratchpad or a folder outside the worktree: parallel sessions overwrote
each other's scratchpad files (#220, #233). Workplans, code and tests go where they belong.

| file | written by | content |
|---|---|---|
| `<stage>-<n>-in.md` | main session, before the spawn | the brief below |
| `<stage>-<n>-progress.md` | lead | one appended line per RED seen, per commit, per mutant (command · file · sha) |
| `<stage>-<n>-out.json` | lead | the result below, also as its final text (`P1` also writes `P1-<n>-material.md`) |
| `<stage>-<n>-stopped.patch`, `<stage>-<n>-untracked.tar` | main session | work of a lead that stopped |
| `<stage>-<n>-redgreen.json` | main session | the `tools/redgreen.py` receipt (below) |
| `snap-<n>/`, `diff-<n>.patch`, `search-<n>.txt` | main session | what a read-only agent gets (below) |

`<stage>`: `P1 P2 P6 batch qa-fix`; `n` only goes up, nothing is overwritten. The prompt opens with
`stage lead · stage: <stage> · mode: <mode>` and the in file's absolute path (it gets the agent id).

## The in file

1. **Owner's words, verbatim and dated** — copied forward from the previous in file, new ones added
   (they survive the main session's compaction only here).
2. **Previous outputs** — paths: workplan, decision answers, the last `out.json`, verifier findings.
3. **Scope** — `owns` and `shared` paths, and what the lead must not do.
4. **State** — absolute worktree path, branch, base, start commit (40 chars), `git status --short` if
   a previous lead left the tree dirty (the new lead cleans up). Otherwise the tree is clean: the main
   session first commits its own changes and, as it is, any test `regression-test-author` left.
5. **Already verified** — tests and mutants already run, so they are not run again.
6. **`gap-fix` only** — the confirmed GAP table (`file:line`) and the redesign target, or "none".
7. **Attribution** — the commit trailer lines given at the main session's start, verbatim.

## What a read-only agent gets

`isolated-verifier` and `scenario-author` have Read, Grep and Glob only, no `git`. The main session
prepares these in the handoff folder and names them (never "run `git diff`"). Every path in the prompt
is absolute, rules to read included (`<h>/snap-<n>/AGENTS.md`): a relative one resolves wherever the
agent started, which may be another checkout.

- a snapshot of the pinned sha, `n` going up: `mkdir -p <h>/snap-<n> && git -C <wt> archive <40-char
  sha> [paths…] | tar -x -C <h>/snap-<n>`, `<h>` = `.verify/handoff/<branch with / as ->` (absolute);
- the diff, `git -C <wt> diff <base>...<head> > <h>/diff-<n>.patch`, and any history search
  (`git log --grep=<word>`, a `CHANGELOG.md` or report grep) as `<h>/search-<n>.txt`.

## The out.json

```json
{"stage": "P2", "n": 1, "status": "complete|partial|blocked", "branch": "", "base": "", "head": "",
 "commits": [], "files": [], "red": [{"test": "", "cmd": "", "observed": ""}],
 "tests": [{"cmd": "", "result": ""}], "docs": [],
 "questions": [{"kind": "EXTERNAL|POLICY|RESOLVED", "q": "", "default": "", "why": ""}],
 "scope_corrections": {"owns_add": [], "why": ""}, "workplan_diff": [], "notes": ""}
```

- `complete` needs a non-empty `red` for P2, P6, batch and qa-fix. A POLICY question, or an EXTERNAL
  one that blocks, means `blocked`. RESOLVED is not asked: its `why` carries the `file:line` that answers it.
- Every sha is 40 characters; unknown is `—`, never a guess. `workplan_diff` lines use the six columns
  of `workplan-diff.md`; the main session says each in chat.
- "Done" is `rcm/ci` and isolated verification, never the lead. The main session reads this JSON and
  its products, checks `git status --porcelain` is empty, and reruns no test but the receipt below.

## The red-then-green receipt — main session only

`tools/redgreen.py` adds temporary git worktrees outside the lead's worktree, which rule 4 forbids a
lead; the main session runs it after the fix is committed, in every flow that cites a receipt:

```sh
cd <abs worktree> && ./.venv/bin/python tools/redgreen.py --fix <40-char fix sha> --json \
  --test <node id> [--test <node id> …] > .verify/handoff/<branch with / as ->/<stage>-<n>-redgreen.json
```

`--fix` is the first commit on which the red tests pass: the fix right after its test commit, or one
commit holding both (the tool lays the fix's test files on its parent). Nothing is squashed (rule 5):
a lead keeps each fix in one commit after its test; a fix that still spans commits gets one receipt
per row, against the commit that makes that row pass. Exit 0 is red-then-green, 1 is not (the stage
is not done), 3 is "could not decide" (not a pass), 4 is a verdict whose cleanup failed (remove the
leftover worktree it names). PRs and reports cite the receipt, not `red`.

## Rules for every lead

1. **Leads never spawn verifiers** — no agent judges work it made or commissioned (no `Agent` tool).
2. **Long waits are the main session's** — no push, no `gh pr`, no `tools/merge_pr.py`, no `rcm run`,
   no `rcm wait` (a subagent that ends its reply while waiting counts as finished). While a lead
   works, the main session leaves the worktree's git alone.
3. **No owner decisions** — questions go into `questions`; the decision page is the main session's.
4. **Nothing outside the worktree** — never production (`tools/guard_production.py`), never another
   worktree, not even to read. A live run follows `.claude/skills/_shared/test-server.md`.
5. **Git stays simple** — `git add <paths>` and `git commit` on its own branch only (`plan-lead` does
   not commit). Never switch, rebase, reset, merge, cherry-pick, stash, clean, `commit --amend`;
   `git checkout -- <file>` only to undo its own mutant. A lead never edits the frozen workplan.
6. **No heavy commands** — changed tests and `./.venv/bin/ruff` on changed files; `node --test` on a
   changed web file; one `./.venv/bin/python scripts/mutcheck.py --only <name>` per mutant it added.
   The full `pytest`, mutcheck and `scripts/smoke_install.sh` are `rcm-ci`'s (2026-11-20: repeated
   local runs loaded the machine and turned timing tests red).
7. **One call, one place** — `cd <abs worktree> && …` in one Bash call (the cwd resets; `cd ""` stays
   put), `git -C <wt> rev-parse --show-toplevel` first; no zsh-only syntax, no `rm -rf`.

## When a lead stops or is needed again

| situation | next |
|---|---|
| `blocked` or questions | decision page; then `SendMessage` to the same lead with the verbatim answer, or a new in file and a new lead if it cannot be resumed |
| the answer or a verifier changes the frozen workplan | a new `plan-lead`, plan verification and approval again, then a new lead; the blocked one is not resumed |
| `partial`, plan HIGH, plan-to-test UNSOUND, `rcm/ci` red, a GAP left by re-verification | what remains, or the findings (HIGH only for a plan), go into a new in file for a new lead |
| a lead stopped, crashed or the session lost it | before anything else, keep its work with the two commands below; then a new lead with both paths in its in file |
| session compacted | read the highest-`n` files; `out.json` complete means the main session's step is next |

```sh
git -C <wt> diff HEAD > <h>/<stage>-<n>-stopped.patch
cd <wt> && git ls-files -z --others --exclude-standard | tar --null -T - -cf <h>/<stage>-<n>-untracked.tar
```
