# Documentation rules

Who reads what, where it goes, and what a pull request has to update. The layout follows what
widely used CLI projects converged on (ripgrep, bat, ruff, uv, httpie: a short front door plus
deeper files) and the changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## The map

| file | audience | rule |
|---|---|---|
| `README.md` | someone deciding whether to use this | the front door: what it is, install, first job, where to go next. Keep it under ~250 lines |
| `README.ko.md` | the same, in Korean | a **mirror** of `README.md`: same sections, same order, same headings |
| `docs/usage.md` · `docs/usage.ko.md` | a new user doing it for the first time | numbered walkthrough with annotated screenshots |
| `docs/configuration.md` | someone writing `server.toml` | every key and preset feature, with examples |
| `docs/operating.md` | whoever runs the build machine | service files, Docker, upgrades, security, the manual check |
| `CHANGELOG.md` | someone upgrading | every user-visible change, newest first |
| `CONTRIBUTING.md` | someone sending a patch | development, house rules, branches, releasing |
| `PLAN.md` (Korean) | the maintainers | scope and decisions. Not a user document |

English is canonical. Korean exists for `README.ko.md` and `docs/usage.ko.md` only, and both are
updated **in the same pull request** as their English original — a stale translation is worse than
none. Everything else stays English, per the house rule in `CONTRIBUTING.md`.

## README

- Header first: name, one-line claim, badges, a nav line, the language switcher, then one
  screenshot. Somebody who reads only the first screen should know what this is and whether it
  fits.
- Install and the first commands come before any explanation of how it works.
- The blocks between `<!-- smoke:begin -->` and `<!-- smoke:end -->` are executed by
  `scripts/smoke_install.sh` in CI. Change a command there and change the script with it.
- Link to the deeper document instead of inlining it. If a section grows past a screen, it belongs
  in `docs/`.
- Images live in `docs/images/ui/` and are referenced from the READMEs by their full
  `raw.githubusercontent.com` URL, so they also render on PyPI. Documents under `docs/` use
  relative paths. Every image gets alt text that says what it shows.

## CHANGELOG

- One `## [Unreleased]` section on top, and every user-visible pull request adds its entry there,
  in that pull request — never assembled from git log at release time.
- Versions are `## [0.2.2] - 2026-09-08` (ISO date), newest first, with a compare link at the
  bottom of the file. The project follows SemVer.
- Group entries under `### Added` · `### Changed` · `### Deprecated` · `### Removed` ·
  `### Fixed` · `### Security`. When a release breaks something, `### Breaking changes` goes first
  and says what to do about it.
- Every entry ends with its pull request link: `([#40](https://github.com/monocsp/remote_ci_monitor/pull/40))`.
- Write what a user sees, not what the diff did. "Discovery failures now say why" beats "refactor
  `_send`".
- The status document has its own `schema_version`: adding a key is free, removing or redefining
  one bumps it and is stated in the entry.
- The GitHub release body is the changelog section, verbatim. `release.yml` does this — do not
  maintain a second text.

## Screenshots

Both usage guides share one set of annotated images in `docs/images/ui/`, so a number in a
screenshot means the same thing in Korean and in English.

```sh
pip install -e ".[docs]"                          # Pillow; Chrome must be installed
python tools/screenshots/capture.py all           # throwaway server + jobs → raw screenshots
python tools/screenshots/build.py                 # red boxes and numbers → docs/images/ui/
```

- `capture.py` fixes the host name to `macmini` and rewrites paths, ports, LAN addresses and
  tokens, so no machine of yours ends up in a public image. Check the result before committing it
  anyway.
- `RCM_SHOT_ADVERTISE=1` makes the throwaway server advertise itself so `rcm discover` can be
  captured for real. Only use it on a network where no other rcm server is running.
- The numbers in `build.py` are the ①②③ of the usage guides. Change a number and change both
  guides.
- Keep each image under 400 KB; `build.py` fails if one is bigger.

## What a pull request updates

1. The changelog entry under `[Unreleased]`, with the pull request link.
2. The document that owns the subject (the map above), and its Korean mirror if it has one.
3. The screenshots, when the UI in them changed.
4. `examples/server.toml` when a config key was added — the doc-lock tests check that it is there,
   commented or live.

`pytest tests/test_docs_m5*.py tests/test_examples.py` locks this wording: it fails when a feature
exists but the documentation does not mention it. Those tests read a **set** of documents, not just
the README, so moving text between them is fine as long as it still exists.
