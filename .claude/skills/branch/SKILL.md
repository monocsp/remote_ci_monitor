---
name: branch
description: Name and create a branch (with its own worktree) for remote_ci_monitor. Use it every time you start work — before `git worktree add`, `git checkout -b` or `git switch -c` — so the name says what the branch changes, not just where or which milestone.
---

# Branch

A branch name is read by someone who did not write it — in `git worktree list`, in a PR title, in
`git log --graph` a month later. It has to answer "what does this change?" on its own.
`tools/guard_naming.py` refuses names that do not (the hook in `.claude/settings.json`), and
`CONTRIBUTING.md` (Names) is the rule this skill applies.

## Shape

```
<type>/<scope>-<what-it-does>
```

| part | rule |
|---|---|
| `type` | `feat` `fix` `docs` `test` `refactor` `perf` `ci` `build` `chore` — the same set as commit types. `release/vX.Y.Z` is the one other shape |
| `scope` | where: a module (`web`, `cli`, `store`, `gc`, `janitor`, `guard`) or a milestone code (`m5i`) |
| `what-it-does` | 1–5 more words that say the change: a verb or a noun phrase a stranger can read |
| characters | lowercase `a-z0-9` and `-`, 2–7 words in all, at most 48 characters |

Never: a session id or a hash (`worktree-agent-a8b4…`), a milestone code alone (`feat/m5g`), a
scope alone (`fix/cli-ux` — *what* about the CLI?), `feature/` or `hotfix/` (not in the type
set), uppercase, underscores.

| bad | why | good |
|---|---|---|
| `fix/cli-ux` | where, not what | `fix/cli-run-no-wait-eta` |
| `feat/m5g-retention` | code + a scope word | `feat/m5g-workspace-byte-budget` |
| `docs/spec-wireframes` | acceptable, but "which spec?" | `docs/web-queue-wireframes` |
| `worktree-agent-a8b49041eb08c77fe` | nobody can read it | (a name from the table above) |

The worktree directory is the branch without its type: `../remote_ci_monitor-<what-it-does>`
(`fix/web-host-card-undefined-local` → `../remote_ci_monitor-web-host-card-undefined-local`).

## Steps

1. Say in one line what the branch will change. If you cannot, the work is not one branch yet.
2. Pick the type from the table, the scope, and 1–5 words for the change. Check it:
   `python3 tools/guard_naming.py branch <name>` (prints `ok` or the reason).
3. Create it in **its own worktree from `origin/dev`** — never switch an existing worktree
   ([one branch, one worktree](../../../AGENTS.md#branches)):
   ```sh
   git fetch origin
   git worktree add -b <type>/<scope>-<what-it-does> ../remote_ci_monitor-<what-it-does> origin/dev
   cd ../remote_ci_monitor-<what-it-does>
   python3.11 -m venv .venv && ./.venv/bin/python -m pip install -e '.[dev]'
   ```
4. One branch, one concern. Unrelated fixes found on the way go to a second branch
   (Kubernetes: "put changes that are unrelated to your feature into a different pull request").
5. When the PR is merged: `git worktree remove ../remote_ci_monitor-<what-it-does>` and delete
   the remote branch (the `pr` skill does both).

## Renaming a branch you already have

`git branch -m <old> <new>` inside that branch's own worktree is a rename, not a switch — allowed.
If it was pushed, push the new name and delete the old one on the remote.
