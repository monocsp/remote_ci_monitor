---
name: live-verify
description: P4 of the development loop for remote_ci_monitor — two live-verifiers each start their own test server from the worktree's build and run the PR's `(live)` scenario rows through a reproducible `scripts/acceptance/<topic>_live.sh`, the web through the CDP harness at 390 px, two-process e2e for workers, judging the result where it arrives; the main session writes the report under `docs/acceptance/reports/`. Use it when a change shows output to a user, crosses processes, restarts or signals, or installs. Not for pure computation or docs-only PRs (pytest covers those), and never against the production server.
---

# Live verification (P4)

`rcm/ci` proves that the tests pass. It does not prove that a person running `rcm` sees the right
thing, that a second process gets what the first one sent, or that a restart leaves the queue right.
In rcm, a throwaway server run found display bugs that a fully green suite had missed (2026-09), and
the M5s live run found an exit code that disagreed with its documentation (report F1,
`docs/acceptance/reports/2026-11-23-m5s-acceptance.md`). This stage runs the real build.

## When it is required

Required when the PR's lane is full and any of these holds; the workplan's `(live)` rows are the
list to run:

- output a user reads: CLI text or `--json`, the web page, notify hook environment, log lines;
- two processes: client ↔ server, server ↔ `rcm worker`, a hook the server starts;
- restart, signal or time: `rcm serve` stopped and started, SIGTERM/KILL, a schedule that waits;
- install or upgrade: a wheel, `scripts/smoke_install.sh`, a client following the server's version.

Not required: pure functions in `core/`, docs-only and tests-only PRs, a refactor with no output
change. A `(live)` row that is not run is `UNKNOWN` with the reason, never PASS.

## 1. The script

Before the run, the PR has `scripts/acceptance/<topic>_live.sh` — written and committed by the P2
build-lead ([`build-pr`](../build-pr/SKILL.md) §2), not by the main session; reviewed like code; run
by a verifier only on its own test server, never edited by one. `scripts/acceptance/m5s_live.sh` is
the model for its shape, except that it starts by deleting its base folder; a new script never
`rm -rf`s an existing folder and picks a new name instead ([`_shared/test-server.md`](../_shared/test-server.md)):

- it uses only the worktree's `.venv/bin/rcm` and builds its server exactly as
  [`_shared/test-server.md`](../_shared/test-server.md) says (the only place for those rules — port,
  data folder, HOME, discovery, never production);
- presets are fakes (`sh -c` printing a marker and an exit code), notify hooks are fakes that write
  the environment they received to a file;
- each scenario ID prints one `PASS`/`FAIL` line to `out/summary.txt`; the last rows of every job go
  to `out/jobs.json`; it exits 1 if any line fails and stops every process it started, on failure too;
- pytest does not call it.

## 2. Run — two live-verifiers

Two [`live-verifier`](../../agents/live-verifier.md)s, one after the other (the build machine also runs
`rcm-ci`), each on its **own** test server with its own data folder — never one shared server, whose
state the second would inherit. Neither sees the other's verdict or your expectation; each gets the
worktree path and head sha, the port and data folder the main session picked for it (checked free as
the test-server rules say), the workplan's rows, the script, and the owner's explicit requests for
this PR. The prompt asks each to:

1. Write down the environment before (`env | grep -E '^(RCM_|XDG_)'`, `which rcm`, the server's
   `rcm version`) and after, so a "restored" setting can be compared with the original.
2. Run the script, then open every result it claims — a summary saying `PASS` is the script's
   opinion, not the verdict.
3. **Judge where it arrives.** The job's final state from `GET /jobs/<id>` and the events, the hook's
   file, `rcm wait`'s exit code, the artifact on disk, the page after the update — not the `200` of
   the submission or the transition in a log: a transition can succeed and land on a broken result.
4. For the web: the CDP `Chrome` helper in `tests/test_web_browser.py`, the page's error collector at
   0, the phone width at **390 px measured inside the page** (the phone test once ran at 500 px
   because of Chrome's minimum window, M5d §4.7) and a desktop width, both languages when wording
   changed. Screenshots with the same helper's `Chrome.screenshot()`, saved in the verifier's
   `data_dir`, redacted by hand before they leave it (`tools/screenshots` runs only its own demo
   server and cannot capture this one).
5. For workers: the two-process helpers in `tests/test_e2e_worker.py` — a real `rcm worker` process,
   `kill -9`, SIGTERM, a server restart while a job runs; submitted to the worker-only preset, as
   [`_shared/test-server.md`](../_shared/test-server.md) says, or the local lane answers instead.
6. Report per ID: command, job number, observed result, verdict (FAIL only with evidence that the
   result is wrong; what could not be established is `UNKNOWN`, never PASS), severity — severity as
   [`isolated-verifier`](../../agents/isolated-verifier.md) defines it. A difference in environment
   (machine, timing that the scenario allows) is not a defect; an explicit owner request that is not
   met always is.

Tokens appear as their first four characters, home paths as `<HOME>`, the requester as
`--by acceptance`. Production jobs on the build machine are looked at only by the main session,
read-only, with the owner's OK; a verifier gets the numbers as a file
([`_shared/test-server.md`](../_shared/test-server.md)).

## 3. Rounds and fixes

Rounds, escalation and the `verify-round:` record are the canon's. A HIGH is fixed (small: the main
session, red test first and a receipt as [`_shared/stage-lead.md`](../_shared/stage-lead.md) «The
red-then-green receipt» says; otherwise a new lead through [`build-pr`](../build-pr/SKILL.md)),
`rcm/ci` goes green on the new head, and the next round **starts the script from the beginning** on
fresh servers for both verifiers — the fix changed the code the earlier steps ran on. MED/LOW wait
for triage.

## 4. Report

The report is `docs/acceptance/reports/<YYYY-MM-DD>-<topic>.md` (Korean), with images in
`docs/acceptance/reports/shots/`, committed in the PR. A report over about 800 characters is written by
[`korean-writer`](../../agents/korean-writer.md) from the verifiers' tables (`korean-writing` §5 rule 6);
the main session gives it the material and reads the result against §6. It holds:

- date, target branch and sha, the PRs it contains and the ones it does not yet;
- the workplan rows it covers and how to run it again (script, variables, duration);
- setup: build, server settings, fake presets and hooks, and that production was not touched;
- a verdict table: ID, what was done, command, job, state transitions with times, expected vs
  observed, verdict;
- findings `F1…` with severity and what happened to each, and what was not checked and why.

Look at every image before committing it: nothing redacted it but you (tokens, ports, home paths,
host names). The `docs` skill covers the generated images under `docs/images/ui/`, not these.
