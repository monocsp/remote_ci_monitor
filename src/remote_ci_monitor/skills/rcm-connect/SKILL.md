---
name: rcm-connect
description: Connect this project to an rcm build machine's Store tab in one go — ask for the project path, the repo name and the platforms, then run rcm-store-connect (required) and the optional rcm-gate-connect · rcm-qa-connect · rcm-release-driver in order, verify with `rcm check`, and leave a report in docs/rcm-connect.md. Use it when someone says "connect this project to rcm", "set up the store tab", or names a project and asks for release/gate/QA wiring.
---

# rcm-connect — one entry point

The owner should have to know one thing: the project. Everything else is asked once, written down
in `docs/rcm-connect.md`, and reused by the skills this one calls. The four worker skills are
installed next to this file by `rcm skills install --into <project>`; this skill never copies or
edits them.

Tiers, so expectations are right from the first sentence:

| tier | roles | skill | without it |
|---|---|---|---|
| **required** — the Store tab opens | secrets · `plan` · `upload` · `review` | `rcm-store-connect` | no Store tab for this repo |
| optional | `gate` | `rcm-gate-connect` | grey row "gate — not configured" |
| optional | `qa` | `rcm-qa-connect` | grey row "qa — not configured" |
| optional | `driver` (S0–S8 round) | `rcm-release-driver` | plan / upload / review stay single buttons, no stepper |
| optional | `dev` (dev distribution) | none — detected preset, profile line by `rcm-store-connect` | no dev card |

## When to use

- A project has never been connected, or a connection is half done (a `docs/rcm-connect.md` exists
  with open items).
- The owner asks for "the whole setup" rather than one piece. For one piece, call that skill directly.

## Inputs it asks for (all at once, one question block)

1. **Project path** — default: the current working directory if it is a git repository.
2. **Repo name** for `[[repos]]` in `server.toml` — default: the directory basename, lowercase.
3. **Platforms** — `ios,android` (default) · `ios` · `android`.
4. **Optional tiers to wire now** — multi-select: gate · qa · release driver · dev distribution.
   Default: none. Say plainly that the Store tab works without them and that they can be added
   later by name. `dev` has no skill of its own: it is offered only when detection finds a
   preset whose name matches `deploy-*`/`*-dev` with a safe default mode, and it is wired by the
   store skill writing `dev = "<preset>"` into the profile.
5. **Server config path** — the build machine's `server.toml` used for the candidate check.
   Default: `~/.config/rcm/server.toml` if it exists, else `skip` (the check then runs on a
   minimal candidate).
6. **Secrets env var name** — the variable jobs receive the secrets folder in. Default:
   `<REPO>_SECRETS` upper-cased.
7. **Existing pieces** — do not ask; detect (§Step 1) and show the owner the detection table before
   creating anything.

Nothing else. Run every snippet below in **bash** (zsh aborts a line on an unmatched glob). Secrets are never asked for here — they are entered in the Settings screen of the
web UI after the profile exists (`rcm-store-connect` lists which ones).

## What it creates

- `docs/rcm-connect.md` — the running report: answers, detection table, one section per skill run
  (Created · You must fill in · Verified), and a final checklist. Created on the first run, appended
  afterwards. This file is the memory between runs; its header block holds the answers so no
  worker skill asks again. The header is exactly:

  ```
  # rcm connect — <repo>

  repo: <name>
  platforms: <ios,android|ios|android>
  server_toml: <path or skip>
  secrets_env: <VAR>
  presets_file: <scripts/rcm/presets.release.toml or the existing presets file the store skill reused>
  tiers: <gate,qa,driver or none>
  ```
  `presets_file` is written by `rcm-store-connect` (it decides whether an existing presets file
  plays that role); leave the line as `presets_file: pending` until then.
- Everything else is created by the worker skills, each in its own section of that report. Two
  project-side files are shared by all of them and never edited by hand while a skill runs:
  - `scripts/rcm/presets.release.toml` — every preset the project offers rcm, one file;
  - `scripts/rcm/profile.release.toml` — the whole `[repos.<name>.release]` block; optional skills
    add their one line (`gate = …`, `qa = …`, `driver = …`) to it.
  The live `server.toml` on the build machine is **never** edited by a skill. Verification always
  runs on a candidate: `python3 scripts/rcm/rcm_candidate.py --server <server.toml> --repo <name>
  --check` (installed by `rcm-store-connect`) merges the two fragments into a temporary copy and runs
  `rcm check` on it.

## What the human still fills in

- The `# TODO(project):` blocks in generated scripts (store API calls, signing, PR/notification
  specifics). Each worker skill lists its own; this skill repeats the union in the final checklist.
- The server side: pasting the profile block into the build machine's `server.toml`, restarting
  `rcm serve`, entering secrets in the Settings screen, running the first read-only `plan`.

## Steps

1. **Detect before asking.** In the project path, look for (generic globs only):
   - an rcm presets file (`scripts/rcm/*.toml`, `rcm/*.toml`, `*.rcm.toml`) and which preset names
     it already has;
   - release scripts writing the contract files (`grep -rl 'plan.json\|review.json\|upload.json'
     scripts/ tools/ fastlane/ 2>/dev/null`);
   - a CI gate entry (`scripts/*ci*.sh`, `Makefile` targets named `ci`/`gate`, `package.json`
     scripts named `ci`/`test:all`);
   - a QA harness (`integration_test/`, `e2e/`, `cypress/`, `maestro/`, `scripts/*qa*`);
   - a release driver (`scripts/release/*release_driver*` or `*product_release*` that accepts
     `--status`; the store skill's `release_plan/upload/review.sh` are not a driver);
   - `docs/rcm-connect.md` from an earlier run → **resume**: reuse the header answers, ask only
     the tiers (item 4), append a `## rcm-connect — <date> (resume)` section with the new detection
     table. The header is not rewritten, with exactly two permitted edits: if it lacks the
     `presets_file:` / `tiers:` lines (an older run), append `presets_file: pending` and
     `tiers: none` after `secrets_env:`; and the values of those two lines are updated by the
     skills that own them (`rcm-store-connect` sets `presets_file`, this skill sets `tiers` in
     Step 6). A worker section counts as done only when its `Verified:`
     line names a green candidate check and green selftests — `Verified: none`, `pending` or a
     missing line means "run that skill again"; the skill treats its own earlier files as its own
     (see each skill's resume rule) and does not need `--force` for them.
   Exclude `.claude/`, `.git/`, `node_modules/`, `build/` from every recursive grep — the installed
   skills' own templates would otherwise show up as project pieces.
   A piece that already exists and passes its role's own checks (a script that writes the
   contract file, a gate that prints the markers, a preset that meets the invariants) is
   **adopted**: the worker skill records `kept — real implementation` and never replaces it with
   a skeleton. Show the table `piece · found at · will create / will keep`. Nothing is written yet.
2. **Ask the input block** (§Inputs). Write `docs/rcm-connect.md` with the header above (all six
   lines, `presets_file: pending`), then `## Answers` and `## Detection` sections.
3. **Run `rcm-store-connect`** with the answers. It must end with its section in the report, the two
   fragment files, `scripts/rcm/rcm_candidate.py`, and a `release <repo>` row that is green or only
   warns about the secrets folder (Settings creates it). If it ends red, stop here and show the red
   rows — nothing optional is worth wiring on top of a broken required tier.
4. **Run the chosen optional skills**, in this order: `rcm-gate-connect` → `rcm-qa-connect` →
   `rcm-release-driver`. Each reads the header (`presets_file` included); the driver takes the
   preset names from `scripts/rcm/profile.release.toml`, never from a question.
5. **Verify the whole**: `python3 scripts/rcm/rcm_candidate.py --server <server.toml> --repo <name>
   --presets <presets_file> --check` again. Accepted row: `ok release <repo>` or `warn release
   <repo>` whose warnings are only `secrets dir … does not exist yet` (always true on a candidate)
   and `<role> not configured` for roles the owner did not choose (`dev` is never configured by
   these skills). Any FAIL, or a missing `release <repo>` row, is red. Then every generated
   script's `--selftest` green (`release_plan/upload/review.sh`, `rcm_contract.py`,
   `rcm_candidate.py`, and if chosen `gate.sh`/wrapper, `qa.sh`, `qa_report.py`,
   `release_driver.sh`, `release_check.py`) — green means exit 0 and a last line matching
   `selftest[: ]+(PASS|ok|all green)`; `bash -n` on every shell file and
   `python3 -c 'import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]' <files>` on
   every python file (no `__pycache__` left behind).
6. **Finish the report**: set the header's `tiers:` line to the optional tiers whose sections
   are now Verified (`gate,qa,driver` subset, or `none`); append `## Summary — <date>` with a
   table `role · preset · script · selftest · status`, then `## Next on the build machine` — the five lines the owner runs there
   (append `scripts/rcm/presets.release.toml` and `scripts/rcm/profile.release.toml` to the
   server's `server.toml` — or run `rcm_candidate.py --out` there and diff · restart `rcm serve` ·
   `rcm check` · open the web UI → Settings → drop the secrets · run the first read-only plan from
   the Store tab).
   Print the same summary to the owner. Do not open a PR — the owner reviews the generated files.

## Done means

- the candidate check shows `ok   release <repo>`, or `warn` only for the not-yet-created secrets
  folder and for roles not chosen, with the chosen optional roles set.
- Every generated script passes its `--selftest`.
- `docs/rcm-connect.md` lists every `TODO(project)` still open, by file and line.
- Nothing irreversible ran: no upload, no review submission, no push to the default branch.

## Never

- Never run `upload` with `mode=upload` or `review` with `mode=submit` — verification is
  `--selftest`, `rcm check`, and the read-only `plan` at most.
- Never generate an input that releases after store approval (no `automatic_release`, `rollout`,
  `release_status` inputs); the generated review preset pins manual release and the driver's
  irreversible calls require a build number typed by a human.
- Never ask for or write a secret value; only names and kinds go into the profile.
- Never put the project's name, paths or key names into rcm itself — they live in this project's
  `server.toml` profile and scripts. rcm stays generic.
- Never overwrite a file the owner already has: worker skills report an existing file and stop,
  unless the owner names it in `FORCE_FILES` (per file) or says `FORCE=1` (all). Files the skills
  generated themselves (marked `# generated by rcm-…`) are theirs to rewrite, with a `.bak`.
- Never contact GitHub, a store or the live rcm server while connecting; every check is local
  (`rcm check` on a candidate with a dummy `--server`). When a project's own script needs a
  network identity before it can run (a `gh api` call in its preflight), skip that run and rely
  on its `--selftest`; say so in the report.
