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

```sh
git switch dev && git pull
git switch -c <type>/<topic>
# work
gh pr create --base dev
```

`main` only takes a pull request from `dev`. The workflow job names `test` (`ci.yml`) and
`main-from-dev-only` (`pr-policy.yml`) are wired into the ruleset: renaming one means changing the
ruleset too.

## Checks before a pull request

```sh
ruff check . && ruff format --check . && pytest
node --test tests/web/*.test.js      # web UI pure functions
python scripts/mutcheck.py           # the tests must go red for 8 known mutations
scripts/smoke_install.sh             # the README's own commands on a fresh venv
```

## Documentation

Every user-visible change updates the docs in the same pull request. The rules are in
[`docs/documentation.md`](docs/documentation.md); the working checklist is the `docs` skill
(`.claude/skills/docs/SKILL.md`), which also covers regenerating the annotated screenshots.

## Traps that have already cost a session

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
