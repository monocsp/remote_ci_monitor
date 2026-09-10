# Agent guide

`PLAN.md` (Korean) is the plan of record: read it before changing anything. `CONTRIBUTING.md` has
the same rules for humans.

## Rules that are not negotiable

- **Zero runtime dependencies.** Standard library only, server and client. Dev and docs extras are
  fine.
- **Comments and docstrings in Korean. Identifiers, CLI help and documentation in English.**
- **The web UI ships Korean first**, with an English switch in the page. Identifiers, preset names,
  commands, SHAs and repository URLs stay untranslated in both languages, and text the *server*
  sends is the server's own — the page does not translate it.
- **Tests first.** A fix ships with the test that was red before it. Never weaken a test to make a
  change pass.
- **Never invent a number.** Unknown prints as `—`; `rcm wait` exit 3 means unknown and is never
  reported as a failure.
- **No secrets in logs, errors or screenshots.** Tokens are stored as SHA-256 and printed once.

## Branches

`main` and `dev` are protected by a ruleset; direct pushes are rejected, admins included.

**One branch, one worktree.** This repository is worked on through sibling worktrees
(`remote_ci_monitor-<topic>`), several checked out at once. Never `git switch` an existing worktree
onto another branch: another session may be working in that folder, and the switch pulls the floor
out from under it. `remote_ci_monitor-dev` is where `dev` lives. New work means a new worktree.
What is forbidden is *changing* which branch a worktree is on; bringing one up to date with
`git fetch` or `git pull` on the branch it already has is always fine.

```sh
git fetch origin
git worktree add -b <type>/<topic> ../remote_ci_monitor-<topic> origin/dev
cd ../remote_ci_monitor-<topic>
python3.11 -m venv .venv && ./.venv/bin/python -m pip install -e '.[dev]'
# work
gh pr create --base dev
git worktree remove ../remote_ci_monitor-<topic>   # once it is merged
```

**Names are part of the work.** Before creating a branch invoke the `branch` skill, before
committing the `commit` skill, before opening or merging a PR the `pr` skill — they hold the
shape (`<type>/<scope>-<what-it-does>`, `<type>(<scope>): <summary>`) and the steps.
`tools/guard_naming.py` (a `PreToolUse` hook) refuses a branch name, commit subject or PR
title that does not fit; the rule is in `CONTRIBUTING.md` (Names).

`main` only takes a pull request from `dev`. The workflow job names `test` (`ci.yml`) and
`main-from-dev-only` (`pr-policy.yml`) are wired into the ruleset: renaming one means changing the
ruleset too.

## The build machine's own install

A machine that runs `rcm serve` may have its service virtual environment pointing at a checkout of
this repository (`pip install -e`). That checkout is production: it stays on `main`, nothing is
edited in it, and it moves only by `git pull --ff-only` plus a service restart with the queue
empty. Develop in a `git worktree` with its own `.venv`, and give a test server its own config,
`port` and `data_dir` — never the production ones.

`tools/guard_production.py` enforces this as a `PreToolUse` hook (`.claude/settings.json`). It
finds the production checkout from the machine's own editable install, refuses edits to it and to
the server's config and data, refuses opening the production database with a build other than
the service's own (`rcm token …` against the production config or data — a different build
migrates the database on open; `rcm gc --dry-run --config` is allowed, it plans on a copy), and
asks before a deploy. On a machine with no such install it does nothing. The procedure is in
[operating a build machine](docs/operating.md#from-a-git-checkout).

## Checks before a pull request

```sh
ruff check . && ruff format --check . && pytest
node --test tests/web/*.test.js      # web UI pure functions
python scripts/mutcheck.py           # the tests must go red for 24 known mutations
scripts/smoke_install.sh             # the README's own commands on a fresh venv
```

## Documentation

Every user-visible change updates the docs in the same pull request. The rules are in
[`docs/documentation.md`](docs/documentation.md); the working checklist is the `docs` skill
(`.claude/skills/docs/SKILL.md`), which also covers regenerating the annotated screenshots.

## Traps that have already cost a session

- `git switch` inside an existing worktree took a folder another session was working in. A clean
  `git status` does not mean the folder is free — read `git worktree list` and add your own.
- Korean text is double width: a line under 100 characters can still fail `ruff` E501.
- The doc-lock tests (`tests/test_docs_m5*.py`, `tests/test_examples.py`) fail when a feature has
  no documentation. Read the failure — it names the missing wording.
- `rcm token` takes `--config` **before** the subcommand: `rcm token --config X list`.
- A high-entropy string literal fails the `secrets` job (gitleaks) even when it is a placeholder,
  and rewriting it later does not help: the job scans the branch's commits. Split it or drop
  it before pushing.
- macOS blocks local-network traffic for processes without Local Network permission, launchd
  services included. Discovery silently sends nothing; the server log says
  `mdns: send failed: EHOSTUNREACH`.
