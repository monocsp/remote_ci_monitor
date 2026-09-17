---
name: rcm-qa-connect
description: Connect a project's device / scenario / end-to-end QA to rcm's Store tab as the optional `qa` role — a `qa` preset, a job body that prints step and per-unit progress markers with a denominator known before the run, and a `report.json` whose verdict (PASS · FAIL · BLOCKED) the tab draws as a chunk grid, a real done/total bar and an amber-not-red "environment" state. Use it after `rcm-store-connect`, when the project has (or wants) a QA harness that runs on real or simulated devices; skip it if there is no QA — the Store tab shows a grey "not configured" row and nothing else breaks.
---

# rcm-qa-connect

The Store tab does not judge QA. It runs the preset you name as `qa`, reads the `report.json`
your script wrote, and draws the `::rcm::progress::` lines it printed. This skill gives a project
those three things with a **working contract and empty hooks**: the job body parses inputs, knows
its denominator before it runs, prints the markers, writes `report.json` however the run ends,
exits with the documented codes, and proves all of that with a `--selftest` that never touches a
device. What runs on the device stays the project's — four `# TODO(project):` hooks.

Read first: rcm `docs/release-contract.md` §2 (report.json, exit codes) and §3 (markers). The
words below — unit, platform, verdict — are the contract's; the project's own words (chunk, spec,
scenario, lane) go in the hooks, never in rcm.

## When to use

- The project has a QA harness that runs per **unit** (a chunk / spec file / scenario) on one or
  more **platforms** (ios, android, web, desktop — any lowercase names), and the owner wants the
  Store tab's QA card to show a per-platform grid, a `done/total` bar and a verdict.
- The `rcm-store-connect` skill has already run: `docs/rcm-connect.md` has its header,
  `scripts/rcm/profile.release.toml` and `scripts/rcm/rcm_candidate.py` exist. If any of the
  three is missing, say "run /rcm-store-connect first" and stop. This skill only adds the
  optional `qa` role.
- Re-run it to add a platform, change the unit definition, or after the harness moved.

## Inputs it asks for

Ask only what cannot be detected. Defaults in brackets.

1. **Project path**. The **repo name**, **platforms**, **server.toml path** and the **presets
   file** are read from the header of `<project>/docs/rcm-connect.md` (written by
   `rcm-store-connect`; the presets file is its `presets_file: <path>` line — the store skill may
   have reused an existing presets file; fall back to `scripts/rcm/presets.release.toml` when the
   line is absent). Nothing from the header is re-asked.
2. **Where the harness is** — detected first (step 1); asked only when detection finds nothing.
3. **What one unit is** and **how to list them before running** (step 2) — a manifest file, a glob,
   or a `--list` command. This question is never skipped and never answered by guessing.
4. **Platform order** [the header's platforms in the order listed; a single name otherwise].
   Order matters: a red platform stops the later ones, which become `not_run`.
5. **Typical duration of a full run** in minutes — for `timeout_seconds` (`3 ×` typical, minimum
   1800) and `expected_seconds`.
6. Whether the harness can leave **per-unit screenshots** for `step-captures.html` [no].

## What it creates

| Path (inside the project) | Why |
|---|---|
| `scripts/rcm/qa.sh` | the `qa` preset body — preflight · build · run per platform · collect · cleanup; markers; exit codes; `--selftest`. From `templates/qa_job.sh`. |
| `scripts/rcm/qa_report.py` | stdlib helper that builds and validates `report.json` (`collect` · `validate` · `check-markers` · `--selftest`). From `templates/qa_report.py`. |
| `[[presets]] name = "qa-<harness>"` appended to the header's presets file (`presets_file:`, default `scripts/rcm/presets.release.toml`) | the preset rcm runs for the `qa` role (step 7); a same-name block is replaced. |
| `qa = "qa-<harness>"` under `[repos.<name>.release.presets]` in `scripts/rcm/profile.release.toml` | tells the Store tab which preset plays QA. The live `server.toml` is never edited by a skill. |
| `docs/rcm-connect.md` — section `## rcm-qa-connect — <date>` | what was created, what the human still owes, what was verified; `/rcm-connect` reads it. |

Existing files are never overwritten: if `scripts/rcm/qa.sh` exists and differs from the
template, report the diff and stop; the owner re-runs with the words `--force` in the request to
replace it (back the old file up as `qa.sh.orig` first).

## What the human still fills in

Each is a `# TODO(project):` block in `scripts/rcm/qa.sh`; nothing is silently stubbed — an
unfilled hook makes the job end BLOCKED (exit 2), never green.

- `list_units` — prints one unit id per line, deterministic order, **before** anything is built.
- `build_platform <plat>` — builds the app/bundle for one platform (no-op allowed if the runner builds).
- `check_devices <plat>` — boots / health-checks the device; exit 10 when it cannot be made healthy.
- `run_unit <unit> <plat> <out_dir>` — runs one unit; exits 0 PASS · 1 FAIL · 2 died without
  evidence · 10 device blocked; may print `screenshot=<path>` as its last line.
- Optional: `collect_extra` (renders `step-captures.html`), `cleanup_devices`.
- Preflight checks for tools, tokens and secrets — read from the folder rcm hands over in
  `$<secrets_dir_env>`; each missing thing is `finish_blocked 2 "<what>"`.
- The preset's project-defined inputs (e.g. `order`), and the `artifacts` globs if the output
  folder is not the default `qa-reports/rcm/<sha7>/`.

## Steps

Run the snippets in bash. `$presets_file` is the header's `presets_file:` value (default
`scripts/rcm/presets.release.toml`); `$REPO` and `$SERVER_TOML` come from the same header.

1. **Detect the harness** (read-only, generic globs — never an app name). Look, in this order, for
   `integration_test/`, `e2e/`, `cypress/` or `cypress.config.*`, `.detoxrc*` / `detox` in
   `package.json`, `.maestro/` or `maestro/`, `**/qa/**/*.y*ml` chunk files, and any script under
   `scripts/` whose name contains `scenario`, `e2e` or `qa` (`grep -l` for `--platform` or
   `verdict`). Record what was found in one line each: kind, path, how it is invoked. If nothing
   matches, ask input 2 — do not scaffold a harness; this skill connects one, it does not write one.
   *Check:* the list is shown to the owner and confirmed before any file is written.
2. **Define the unit and its denominator.** With the owner, write down: one unit = `<thing> ×
   platform`; the list of `<thing>` comes from `<manifest | glob | --list command>`; it is
   available before the build. Try the command now (read-only) and count the lines: that count ×
   platforms is the `total` the tab will draw. If the count is only knowable after running (a
   runner that discovers tests while executing), say so and set `list_units` to print nothing
   under a comment explaining why — the job then ends BLOCKED, and the page falls back to a
   time-based bar with `basis: by time` — rather than printing an invented `0/1`.
   Unit ids must not contain `/` or `::` — the marker is `<unit>/<plat>` inside a `::`-delimited
   line, and either character would split the grid cell; map such names (e.g. a path) to a slug in
   `list_units`.
   *Check:* the command prints ≥ 1 line, the same lines twice, and none contains `/` or `::`.
3. **Install the job body** — unless the project already has one. **Adopt** an existing body
   when it prints `::rcm::step::` lines and writes a `report.json` that `python3 -B
   templates/qa_report.py validate --file <it>` accepts (or its own selftest proves that): keep it,
   do not copy `qa_job.sh`, record `kept — real implementation` and continue with step 7 using
   the preset that already runs it. Otherwise copy `templates/qa_job.sh` to `scripts/rcm/qa.sh` and
   `templates/qa_report.py` to `scripts/rcm/qa_report.py` (`chmod +x`). Fill `list_units` from
   step 2 and set the default of `RCM_INPUT_ORDER` to input 4. Leave the other hooks as TODOs
   unless the owner dictates them now. What the body already does — do not re-implement it:
   - `::rcm::steps::N` printed **after** `list_units` succeeds, computed as
     `1 (preflight) + 2 × platforms (build, run) + collect + cleanup`;
   - `::rcm::step::preflight` · `build <plat>` · `run <plat>` · `collect` · `cleanup`;
   - `::rcm::progress::<done>/<total>::<unit>/<plat>::run` before each unit and
     `…::ok|fail|env|blocked` after; `total = units × platforms`, fixed for the run, so the
     numerator can never pass it; a `not_run` platform prints nothing (its grid stays grey);
   - `::rcm::summary::<VERDICT> <plat>=<v> … units <done>/<total>` as the **last** marker line.
   *Check:* `bash -n scripts/rcm/qa.sh` and `python3 -m py_compile scripts/rcm/qa_report.py`.
4. **report.json.** The body writes `$QA_OUT_DIR/report.json` exactly once in `collect` via
   `qa_report.py collect`, and an EXIT trap writes a BLOCKED one if the script dies earlier — so the
   file exists however the run ends. Minimum fields (contract §2): `schema: 1`,
   `verdict: PASS|FAIL|BLOCKED`, `platforms: {<plat>: {verdict, chunks, failed} | "not_run"}`,
   `failures: [{chunk_id, platform, rc, step, screenshot}]` — non-empty whenever `verdict == FAIL`
   (an empty one is folded to BLOCKED, never left as FAIL). Extra fields the body adds and the
   page shows raw: `blockers[]`, `coverage {declared, executed{}}` — a platform that ran fewer
   units than declared is BLOCKED even when every unit it ran passed.
   *Check:* `python3 scripts/rcm/qa_report.py validate <a report.json>` exits 0.
5. **Exit codes and the amber rule.** `0` PASS · `1` FAIL (a unit or a build failed — code is
   wrong, the release is blocked) · `2` BLOCKED-environment (a tool, token, unit list or evidence
   was missing; a unit died without a verdict) · `10` BLOCKED-device (the device could not be made
   healthy). The tab colours 2 and 10 **amber, "environment"**, not red: the code was not shown to
   be wrong, so the PR stays open and the round can be retried without a new version. Keep that
   distinction in the hooks — never map a missing tool to exit 1, never map a failing assertion
   to exit 2.
   *Check:* the selftest in step 8 covers all four codes.
6. **Optional `step-captures.html`.** If input 6 is yes, fill `collect_extra` to render one HTML
   table — rows are units, cells are the step screenshots — under `$QA_OUT_DIR/step-captures.html`,
   and add it to the preset's `artifacts`. It is informational: its failure is logged and never
   changes the verdict. The live film during a run (wireframe item 45) is a later rcm phase;
   this file is what the finished card links to.
   *Check:* the file appears in `rcm artifacts <job>` after a run, or the line is skipped.
7. **The `qa` preset and the profile line** — in the two fragment files, never in the live
   `server.toml`. Append this block to `$presets_file` (if a `[[presets]]` block with the same
   `name` already exists there, replace that block, from its `[[presets]]` line to the line before
   the next `[[presets]]`), adapting names and inputs. Name it `qa-<harness>` after what step 1
   found (`qa-scenarios`, `qa-e2e`, `qa-maestro`) — or keep the project's existing name when the
   body was adopted. Every check below greps for **the name the profile line points at**, never
   for a `qa-` prefix: `qa_name="$(sed -nE 's/^qa *= *"([^"]+)".*/\1/p' scripts/rcm/profile.release.toml)"`:
   ```toml
   [[presets]]
   name = "qa-<harness>"                         # e.g. qa-scenarios; the profile refers to it
   description = "Device / scenario QA on the release ref — writes report.json"
   argv = ["bash", "scripts/rcm/qa.sh"]
   source_modes = ["git_ref"]
   repo = "app"                                  # the [[repos]] name from the header
   timeout_seconds = 5400                        # 3 × a typical full run, never less than 1800
   expected_seconds = 1800
   concurrency_group = "devices"                 # one job at a time on the shared simulators/emulators
   artifacts = ["qa-reports/rcm/**/report.json", "qa-reports/rcm/**/*.log",
                "qa-reports/rcm/**/declared_units.txt", "qa-reports/rcm/**/results.tsv",
                "qa-reports/rcm/**/step-captures.html"]
   artifacts_on = "always"                       # the page needs report.json on red runs most of all
   env_passthrough = ["PATH", "HOME", "LANG"]
   [[presets.inputs]]                            # project-defined; rcm passes the profile defaults
   name = "order"
   type = "choice"
   choices = ["ios,android", "android,ios", "ios", "android"]
   default = "ios,android"
   description = "Platform order; a red platform stops the later ones"
   ```
   Then in `scripts/rcm/profile.release.toml`, under `[repos.<name>.release.presets]`, set
   `qa = "qa-<harness>"` (replace the line if `qa =` is already there, commented or not).
   The preset carries **no** irreversible mode and no store credentials; the only secrets it may
   read are the profile's, via `$<secrets_dir_env>`.
   *Check:*
   ```sh
   grep -cE '^name *= *"qa-' "$presets_file"                 # exactly 1 — the block is there once
   grep -cE '^qa *=' scripts/rcm/profile.release.toml        # exactly 1
   python3 scripts/rcm/rcm_candidate.py --server "$SERVER_TOML" --repo "$REPO" --presets "$presets_file" --check
   ```
   The helper (installed by the store skill) merges the profile fragment and the presets file
   into a temporary candidate and runs `rcm check` on it — the `release <repo>` row is
   `ok`/`warn` and no longer warns that `qa` is not configured. Never point `rcm check` at the
   live config with edits in it.
8. **Selftest.** Run `bash scripts/rcm/qa.sh --selftest`. It swaps the hooks for a fake unit list
   and a fake runner (`QA_HOOK_*` environment variables) and proves, without a device: all-pass →
   exit 0, `steps::N` equals the number of `step` lines, every progress numerator ≤ denominator
   and the denominator never changes, the summary is the last marker; one FAIL → exit 1, the
   later platform is `not_run`, `failures[]` carries the unit, platform and screenshot; device
   blocked → exit 10 with zero units run; a unit dying without evidence → exit 2 while later
   units still run; an empty unit list → exit 2 before the build, no progress line, `report.json`
   still written; a poisoned report (FAIL with empty `failures[]`) is rejected by `validate`;
   argv and a repeated platform are refused before any step.
   *Check:* both `[qa] selftest PASS` and `selftest PASS` (the python helper) print.
9. **Verify and record.** The candidate check of step 7 green; the two selftests green (for an
   adopted body: a grep for the marker kinds plus the body's own selftest, when the host's guards
   refuse to run the harness locally); if a
   device is attached and the owner agrees, one real run through rcm (`rcm run qa-<harness> --ref
   <branch>`, once the server owner has pasted the fragments into `server.toml`) and read the
   card — the skill itself never starts devices. Append to `docs/rcm-connect.md` (keep its header;
   the section goes last):
   ```
   ## rcm-qa-connect — <YYYY-MM-DD>
   Created: scripts/rcm/qa.sh · scripts/rcm/qa_report.py · preset qa-<harness> in <presets_file> · qa = "qa-<harness>" in scripts/rcm/profile.release.toml
   You must fill in: <the TODO(project) hooks still open, one per line>
   Verified: rcm_candidate.py --check (row release <repo>: ok|warn) · qa.sh --selftest PASS · qa_report.py --selftest PASS · [real run #<id>: <verdict> | pending]
   ```

## Done means

- `python3 scripts/rcm/rcm_candidate.py --server "$SERVER_TOML" --repo "$REPO" --presets
  "$presets_file" --check` shows the `release <repo>` row `ok`/`warn` with `qa` set; the server
  owner has the two fragments to paste.
- `bash scripts/rcm/qa.sh --selftest` and `python3 scripts/rcm/qa_report.py --selftest` both PASS.
- `docs/rcm-connect.md` has the section above with an honest "You must fill in" list.
- Optionally, one real job whose Store card shows the per-platform grid, `done/total` with
  `basis: declared units × platforms`, and the verdict from `report.json`.

## Never

- Never invent a denominator: no `::rcm::progress::` line without a list produced before the run;
  no `0/1` placeholders. An unknown count is BLOCKED plus a time-based bar, not a guess.
- Never let the job go green with fewer units than declared, or with `verdict == FAIL` and an
  empty `failures[]`; never map environment trouble to exit 1 or a failing assertion to exit 2.
- Never run real devices, simulators or emulators from this skill — verification is `--selftest`,
  `rcm check`, and a real run only when the owner starts it.
- Never put secrets in the preset, the script, the log or `report.json`; read them from
  `$<secrets_dir_env>` and print nothing but their presence.
- Never write a project name, app id, bundle path or team name into rcm — those live in the
  project's `scripts/rcm/qa.sh`, presets file and profile.
- Never overwrite an existing `scripts/rcm/qa.sh` without `--force` and a backup; never edit the
  live `server.toml` — only the two fragment files, checked through a temporary candidate.
- Never touch the irreversible roles (`upload`, `review`); this skill adds `qa` only.
