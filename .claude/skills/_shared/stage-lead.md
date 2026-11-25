# Stage leads — the heavy stages go to a fresh agent, by file

The main session runs the loop: it talks to the owner, spawns verifiers, pushes, waits for `rcm/ci`,
writes the decision page and merges. The heavy stages are done by **one fresh lead per stage**, which
returns a result file and is then closed. Why: every later call re-reads the main session's context,
and in another project two thirds of the stage cost was the main session re-reading what it had read
for planning and building (measured there, 2026-11-23). One lead for several stages only moves the
load; a fresh lead per stage, and a fresh one for a small follow-up, keeps each context small.

| stage | agent | mode | model |
|---|---|---|---|
| P1 workplan | `plan-lead` | — | opus |
| P2 build | `build-lead` | `implement` | opus |
| P6 gap fix | `build-lead` | `gap-fix` | opus |
| one PR of a dev batch | `build-lead` | `batch` | opus |
| one QA defect | `build-lead` | `qa-fix` | opus |

Models are provisional until the owner confirms them; the `effort` given on the Agent call beats the
definition's (measured in another project, 2026-11-22).

## Handoff location and names

Everything handed over lives in `.verify/handoff/<branch with / as ->/` of the worktree, which
`.gitignore` already excludes. Never `git add -f` it, and never use the session scratchpad: parallel
sessions share it and have overwritten each other's files there (#220 and #233 went up with another
PR's body). Workplans, code and tests are products, not handoff files; they go where they belong.

| file | written by | content |
|---|---|---|
| `<stage>-<n>-in.md` | main session, before the spawn | the brief below |
| `<stage>-<n>-progress.md` | lead | one appended line per RED seen, per commit, per mutant (command · file · sha) |
| `<stage>-<n>-out.json` | lead | the result below, also as its final text |
| `<stage>-<n>-stopped.patch`, `<stage>-<n>-untracked.tar` | main session | work of a lead that stopped |

`<stage>` is one of `P1 P2 P6 batch qa-fix`; `n` only goes up, a new lead gets a new `n`, nothing is
overwritten. The prompt's first line is `stage lead · stage: <stage> · mode: <mode>`, the second the
absolute path of the in file; the main session appends the lead's agent id to the in file.

## The in file

1. **Owner's words, verbatim and dated** — copied forward from the previous in file, new ones added.
   They survive the main session's compaction only here.
2. **Previous outputs** — paths: workplan, decision answers, the last `out.json`, verifier findings.
3. **Scope** — `owns` and `shared` paths, and what the lead must not do.
4. **State** — absolute worktree path, branch, base, start commit (40 chars), `git status --short` if
   a previous lead left the tree dirty (the new lead cleans up; the main session does not).
5. **Already verified** — tests and mutants already run, so they are not run again.
6. **`gap-fix` only** — the confirmed GAP table (`file:line`) and the redesign target (cause name and
   its GAPs, or "none").

Before a `build-lead`, the tree is clean: the main session commits its own changes first.

## The out.json

```json
{"stage": "P2", "n": 1, "status": "complete|partial|blocked", "branch": "", "base": "", "head": "",
 "commits": [], "files": [], "red": [{"test": "", "cmd": "", "observed": ""}],
 "tests": [{"cmd": "", "result": ""}], "docs": [],
 "questions": [{"kind": "EXTERNAL|POLICY|RESOLVED", "q": "", "default": "", "why": ""}],
 "scope_corrections": {"owns_add": [], "why": ""}, "workplan_diff": [], "notes": ""}
```

- `complete` needs a non-empty `red` for P2, P6 and qa-fix. A POLICY question, or an EXTERNAL one that
  blocks, means `blocked`. RESOLVED is not asked: its `why` carries the `file:line` that answers it.
- `head` and every sha are 40 characters (another project, 2026-10-13: a 9-character sha in a record
  cost twelve re-reviews). Unknown is `—`, never a guess.
- `workplan_diff` lines use the six columns of `.claude/skills/_shared/workplan-diff.md`; the main
  session repeats each in the chat at once.
- "Done" is decided by `rcm/ci` and isolated verification, never by the lead. The main session reads
  this JSON and the products it names, checks `git status --porcelain` is empty, and does not rerun
  the lead's tests — the one exception is the `tools/redgreen.py` receipt a QA fix needs, which the
  main session produces itself.

## Rules for every lead

1. **Leads never spawn verifiers** — no agent judges work it made or commissioned. They have no
   `Agent` tool.
2. **Long waits are the main session's** — no push, no `gh pr`, no `tools/merge_pr.py`, no `rcm run`,
   no `rcm wait` (a subagent that ends its reply while waiting counts as finished; another project,
   2026-11-05).
3. **No owner decisions** — questions go into `questions`; the decision page is the main session's.
4. **Nothing outside the worktree** — never production (`tools/guard_production.py`), never another
   worktree, not even to read. A live run follows `.claude/skills/_shared/test-server.md`.
5. **Git stays simple** — `git add <paths>` and `git commit` on its own branch only (`plan-lead` does
   not commit). Never switch, rebase, reset, merge, cherry-pick, stash, clean, `commit --amend`;
   `git checkout -- <file>` only to undo its own mutant.
6. **No heavy commands** — changed tests and `ruff` on changed files; web changes add `node --test` on
   the changed file; one `python scripts/mutcheck.py --only <name>` for a mutant the lead added. The
   full `pytest`, `scripts/mutcheck.py` and `scripts/smoke_install.sh` are `rcm-ci`'s (2026-11-20: a
   load average of 23–25 from repeated local runs turned timing tests red).
7. **One call, one place** — every command is `cd <abs worktree> && …` in one Bash call (the cwd
   resets between calls; an empty `cd ""` once ran a gate in the wrong tree for 18 minutes in another
   project). Check `git -C <wt> rev-parse --show-toplevel` first. No zsh-only syntax, no `rm -rf`
   (background agents stall at the permission prompt).
8. **While a lead works, the main session leaves the worktree's git alone.**

## When a lead stops or is needed again

| situation | next |
|---|---|
| `blocked` or questions | decision page; then `SendMessage` to the same lead with the verbatim answer, or a new in file and a new lead if it cannot be resumed |
| the answer or a verifier changes the frozen workplan | a new `plan-lead`, plan verification and approval again, then a new lead; the blocked one is not resumed |
| `partial` | its remaining items go into a new in file for a new lead |
| plan HIGH, plan-to-test UNSOUND, `rcm/ci` red, re-verification left a GAP | the findings (HIGH only for a plan) go into a new in file for a new lead |
| a lead stopped, crashed or the session lost it | before anything else, keep its work: `git -C <wt> diff HEAD > <stage>-<n>-stopped.patch` and `cd <wt> && git ls-files -z --others --exclude-standard \| tar --null -T - -cf <handoff>/<stage>-<n>-untracked.tar`; then a new lead with both paths in its in file |
| session compacted | read the highest-`n` files; `out.json` complete means the main session's step is next |

Keep the work before collecting it (another project, 2026-10-05: an operating-system block emptied a
lead's scratch folder before anyone looked).
