# Contributing

Thanks for looking. This is a small, dependency-free tool; the bar for a change is that it keeps
being small and honest about what it knows.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
node --test tests/web/*.test.js  # web UI pure functions
python scripts/mutcheck.py      # proves the tests go red for 22 known mutations
scripts/smoke_install.sh        # the README setup on a fresh venv (builds the wheel first)
```

CI runs the same on Ubuntu (3.11, 3.13) and macOS (3.13), plus the install smoke and gitleaks.

If this machine also *runs* a build server, keep the two apart. The service has its own checkout,
which stays on `main` and is never edited; your work happens in a `git worktree` with the `.venv`
above, and a test server gets its own config, `port` and `data_dir`. See [running from a git
checkout](docs/operating.md#from-a-git-checkout).

## House rules

- **Runtime dependencies stay at zero.** The standard library only, on the server and the client.
  Development and test dependencies are fine.
- **Comments and docstrings in Korean; identifiers, CLI help and documentation in English.**
  The web UI is Korean-first with an English switch; identifiers, preset names, commands, SHAs and
  repository URLs are never translated. `PLAN.md` (Korean) is the source of truth for scope.
- **Tests come first.** A change that fixes a bug carries the test that was red before it.
- **The status document is versioned.** Adding a key is free; removing one or changing what it
  means bumps `schema_version` and is listed in the changelog.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

## Branches

`main` and `dev` are protected: no direct pushes, no exceptions.

1. Branch from `dev` **into its own worktree** — do not switch an existing one, someone may be
   working in it: `git fetch origin && git worktree add -b <type>/<topic>
   ../remote_ci_monitor-<topic> origin/dev`, then give it its own `.venv`.
2. Open the pull request against `dev` and let CI (`test`) pass.
3. `main` only ever takes a pull request from `dev`, which additionally runs `main-from-dev-only`.

## Documentation

Every user-visible change updates the docs in the same pull request. The rules — which file
gets what, how the changelog entry is written, how the annotated screenshots are regenerated —
are in [`docs/documentation.md`](docs/documentation.md).

## Releasing

Releases come from `main`, and `main` only takes pull requests from `dev`:

1. Bump `__version__` in `src/remote_ci_monitor/__init__.py` and turn the changelog's
   `[Unreleased]` section into `## [x.y.z] - YYYY-MM-DD` with a compare link at the bottom
   (feature branch → pull request to `dev`).
2. `gh pr create --base main --head dev`, wait for `test` and `main-from-dev-only`, merge.
3. `git tag v0.1.0 <main-sha> && git push origin v0.1.0`.

The `Release` workflow then checks that the tag is on `main` and equals `__version__`, builds the
sdist and wheel, runs the install smoke on Ubuntu and macOS, and creates the GitHub Release with
the files and the changelog section as its body. PyPI publishing (trusted publishing, no API
token) runs only when the repository variable `PYPI_PUBLISH` is `true`: register the publisher on
PyPI first (owner `monocsp`, repository `remote_ci_monitor`, workflow `release.yml`, environment
`pypi` — the environment name must match exactly), create that environment in the repository
settings, then set the variable.
