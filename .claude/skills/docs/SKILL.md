---
name: docs
description: Write or update the documentation of remote_ci_monitor — README (English and Korean), CHANGELOG, usage guides, configuration and operating docs, and the annotated screenshots. Use it for any user-visible change, before a release, and whenever a screenshot no longer matches the UI.
---

# Documentation

`docs/documentation.md` holds the rules. Read it before writing. This skill is the working
checklist.

## Where the text goes

| subject | file |
|---|---|
| what it is, install, first commands, links | `README.md` + mirror in `README.ko.md` |
| doing it for the first time, with screenshots | `docs/usage.md` + `docs/usage.ko.md` |
| a `server.toml` key, a preset feature | `docs/configuration.md` |
| services, Docker, upgrades, security, checks | `docs/operating.md` |
| what changed for a user | `CHANGELOG.md`, under `[Unreleased]` |
| how to develop, branch, release | `CONTRIBUTING.md` |

English is canonical. `README.ko.md` and `docs/usage.ko.md` are mirrors and change in the same
pull request as their original.

## Checklist for a user-visible change

1. Add the `[Unreleased]` entry with its pull request link:
   `- Sentence about what a user now sees. ([#N](https://github.com/monocsp/remote_ci_monitor/pull/N))`
   under `### Added` / `### Changed` / `### Fixed` / `### Removed` / `### Deprecated` /
   `### Security`, or `### Breaking changes` first when it breaks something.
2. Update the document that owns the subject, and its Korean mirror if it has one.
3. Add the key to `examples/server.toml` (commented is fine) when it is a config key.
4. Run `pytest tests/test_docs_m5.py tests/test_docs_m5b.py tests/test_docs_m5c.py tests/test_examples.py`.
   These lock the wording; they read README plus `docs/configuration.md` and `docs/operating.md`,
   so text may move between them.
5. `ruff check .` — Korean lines count as double width, so wrap them well before 100 columns.

## Releasing

Turn `[Unreleased]` into `## [x.y.z] - YYYY-MM-DD`, add the compare link at the bottom, bump
`__version__`, and leave a fresh empty `[Unreleased]`. The GitHub release body is that section
verbatim; `release.yml` copies it. Full steps in `CONTRIBUTING.md`.

## Screenshots

```sh
pip install -e ".[docs]"                     # Pillow; Chrome must be installed
python tools/screenshots/capture.py all      # throwaway server + jobs → raw screenshots
python tools/screenshots/build.py            # red boxes and numbers → docs/images/ui/
```

The numbers drawn by `build.py` are the ①②③ of both usage guides: change one and change both.
`capture.py` neutralises the host name, paths, ports, LAN addresses and tokens, but look at the
images before committing them. `RCM_SHOT_ADVERTISE=1` captures a real `rcm discover`, and only
works where no other rcm server advertises.

## Style

Say what a user sees. Short sentences, one idea each. No marketing. State a limit where it exists
(`rcm wait` exit 3 is *unknown*, never a failure). Prefer a table when the reader is comparing
things, prose when following an argument.
