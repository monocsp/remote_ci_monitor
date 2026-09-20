---
name: rcm-release-driver
description: Give a project an end-to-end "normal release round" driver (S0 branch → S1 plan → S2 typed build number → S3 draft PR → S4 gate ∥ S5 QA → S6 fast-forward merge → S7 upload → S8 tag + back-merge PR) that the rcm Store tab runs non-interactively and a laptop session can run detached. Use it after `rcm-store-connect` (plan/upload/review presets exist) when the owner wants the S0–S8 stepper instead of single buttons; optional tier — a project without it still gets every card.
---

# rcm-release-driver

The Store tab does not orchestrate presets itself. For a normal round it runs **your** driver as a
separate process, the way a session machine would, and reads the stage back from remote truth
(release contract §5). This skill installs that driver as a skeleton: the flow, the exit codes,
the re-entrancy and the typed-build-number checks are real; the store, PR and chat specifics are
`# TODO(project)` blocks.

## When to use

- The project already has `[repos.<name>.release]` with `plan` and `upload` presets
  (`rcm-store-connect`), and ideally `gate` (`rcm-gate-connect`); `qa` is optional.
- The owner wants one command that takes a version from "cut the branch" to "tag exists",
  survives a server restart or a closed laptop, and refuses to do anything irreversible without
  a human typing the build number.
- Not for a project whose release is one push to a store: the single buttons already cover that.

## Inputs it asks for

Almost nothing — `rcm-store-connect` always runs first and leaves the answers on disk. The skill
**reads** the repo name, the preset names (plan / upload / review / gate / qa), the tag pattern
and the `server.toml` path from `<project>/docs/rcm-connect.md` and
`<project>/scripts/rcm/profile.release.toml`. The presets file is whatever the
`presets_file:` header line of `docs/rcm-connect.md` names (the store skill may have reused an
existing one). If `scripts/rcm/profile.release.toml`, that presets file or
`scripts/rcm/rcm_candidate.py` is missing, it says **"run /rcm-store-connect first"** and stops.

| Question | Default |
|---|---|
| GitHub slug `org/app` (for `gh --repo`) | read from `git remote get-url origin` |
| Default branch · dev branch (`""` when there is none) | the profile's `default_branch` · `dev` if `origin/dev` exists |
| Where the driver lives | `scripts/release/release_driver.sh` |

Commit status contexts (`ci/gate`, `ci/qa`), the `release-blocked` label and `TAG_OWNER` are
constants at the top of the script; change them there.

## What it creates

- `<project>/scripts/release/release_driver.sh` — the bash driver: one function per stage,
  `rcm run … --no-wait` / `rcm wait` / `rcm artifacts` / `gh` calls, no judgement of its own.
  Copied from `templates/release_driver.sh`.
- `<project>/scripts/release/release_check.py` — every decision (stdlib only): `stage` from
  remote truth, `validate plan|upload`, `exitcode --rcm-json`, `find-job`, `confirm --typed N`,
  `version-name --json` (the build name from rcm's version row), `--selftest`. Copied from
  `templates/release_check.py`.
- `driver = "scripts/release/release_driver.sh"` in the `[repos.<name>.release]` block of
  `<project>/scripts/rcm/profile.release.toml` — the fragment `rcm-store-connect` created; the
  skill never edits the live `server.toml`. The Store tab reads this line to show the S0–S8
  stepper (missing → single buttons only).
- A section in `<project>/docs/rcm-connect.md` (see Steps 8).

Re-running detects the two scripts and the profile line and reports them instead of overwriting;
say `--force` to replace the scripts (the owner's `TODO(project)` edits are lost — diff first).

## What the human still fills in

- `GH_REPO` at the top of the driver (or `RELEASE_GH_REPO` in the environment).
- `notify()` — Teams/Slack webhook, if any. Read the secret from `$<secrets_dir_env>`, never
  print it.
- `s3_pr` — PR title/body house style, reviewers, labels.
- `s7_upload` — extra upload inputs (`platform`, `android_track`); every default stays the
  reversible one. `fail_red` — which report files from the job's artifacts to link in the comment.
- `TAG_OWNER` (`job` | `driver`): who creates the tag. `job` — the upload job tags and S8 only
  verifies; `driver` — S8 pushes the tag when it is missing (typed N checked first).
- Whether S0 cuts from the default branch or from dev (the default is the default branch, with
  an assertion that default ⊂ dev when a dev branch exists).

## The driver contract (what the template implements)

```
release_driver.sh --build-name X.Y.Z [--version-id <id>] [--confirm-build-number N] [--dry-run]
                  [--skip-qa '<reason>'] [--retry] [--abort] [--status] [--selftest]
```

| Stage | Does | Proof it happened (remote truth) |
|---|---|---|
| V | only with `--version-id <id>` (the Store tab sends it when the round starts from a version page): read rcm's version row `GET /api/repos/<repo>/release/versions/<id>` with the `RCM_SERVER` / `RCM_TOKEN` the job has, take `ios_version` (else `android_version`) as the build name; a `--build-name` given too must match. Not readable → exit 2 (`--status` prints `stage V` instead) | the version row in rcm |
| S0 | push `release/X.Y.Z` from the default branch; assert default ⊂ dev if dev exists | `git ls-remote --heads origin release/X.Y.Z` |
| S1 | submit the `plan` preset with `build_name`; fetch `plan.json`; validate | job of preset+sha with `build_name` |
| S2 | the human types N: `--confirm-build-number N`, or a TTY prompt; **non-interactive without N → prints `plan: N = <n>`, exit 2** (the Store tab shows the dialog and re-runs with the flag). Refuses if the exact tag already exists (exit 1) | state cache only (`confirmed_n`, who, when) |
| S3 | draft PR release → default | PR whose `headRefOid == sha` |
| S4 ∥ S5 | submit gate and qa together, wait gate then qa; gate red → cancel qa. Green posts a commit status; `--skip-qa '<reason>'` skips S5 and leaves the reason as a PR comment | commit statuses `ci/gate`, `ci/qa` |
| S6 | `gh pr ready` + fast-forward push to the default branch (`merge-base --is-ancestor` first; not ff-able → exit 1, nothing pushed) | PR MERGED |
| S7 | `upload` preset with `mode=upload confirm_build_number=N build_name=X.Y.Z`, `--no-join`; fetch `upload.json`; validate n/mode/status/tag | succeeded job of preset+sha with those inputs |
| S8 | the exact tag exists (or the driver pushes it — TODO); back-merge PR default → dev | tag; open PR default → dev |

Failure path: gate or qa red → PR closed, label `release-blocked`, a comment with the reason and
job number, branch kept, default branch untouched, exit 1. `--retry` reopens the PR, drops the
label, refreshes the release branch from the default branch, clears the cache and resumes.
`--abort` closes an open PR, cancels the cached jobs, deletes the cache — never touches the
default branch. `--status` is **read-only**: it never creates a branch, a PR or a job; it prints
`stage <S>` and — always, with `--version-id`, with `--build-name` and with neither — one
`stages:` line naming the stages this driver knows. rcm reads that line to learn whether it may
pass `--version-id`.
`--dry-run` runs S7 as `mode=rehearsal` and skips S6/S8 (nothing merged, nothing tagged).

Exit codes: `0` done · `1` red / contract violation / typed N ≠ plan / already released ·
`2` prerequisites or N not given · `3` result unknown (job lost or timed out — **the upload is
never resubmitted**; reconcile with the store by hand) · `4` store drift (default branch stays;
plan again with the next N).

Stage is derived from remote truth on **every** call — PR whose head is this sha (a merged PR of
the same branch name from an earlier round is ignored), commit statuses, `rcm jobs --json` rows
with the same preset + sha (+ inputs for upload), the exact tag. The local file under
`.git/release-driver/` is only a cache of job numbers and the confirmed N. Re-running resumes; a
job that already exists for this sha is joined with `rcm wait`, not resubmitted.

Every irreversible call — the push to the default branch, the upload, the tag — is preceded in
code by `require_typed_n`: N must have arrived in this call from a flag or a TTY prompt
(`N_SOURCE`), and `release_check.py confirm --typed N --plan plan.json` must agree. There is no
flag the skill or rcm could set to skip it.

## Steps

Run the snippets in bash. Nothing below contacts GitHub or a live rcm server; the driver's
network preflight (`gh auth status`, `rcm check`, presets on the server) runs only on a real
round, after the merged server config is installed.

1. **Detect** — read the presets file name from the `docs/rcm-connect.md` header first, then
   check the three files the store skill leaves behind:
   ```sh
   presets_file=$(grep -m1 '^presets_file:' docs/rcm-connect.md | cut -d: -f2- | xargs)
   presets_file=${presets_file:-scripts/rcm/presets.release.toml}
   ls scripts/rcm/profile.release.toml "$presets_file" scripts/rcm/rcm_candidate.py
   ```
   Any one of those missing → say "run /rcm-store-connect first" and stop. Read
   `[repos.<name>.release]` from the fragment (repo name, `tag`, `presets.*`) and the
   `server.toml` path from `docs/rcm-connect.md`.
   Then `ls scripts/release/release_driver.sh scripts/release/release_check.py`; grep `driver =`
   in the fragment. Anything present is reported, not overwritten (no `--force`).
2. **Copy** `templates/release_driver.sh` and `templates/release_check.py` into
   `scripts/release/`, `chmod +x`. Check: `bash -n` and `python3 -m py_compile` pass.
   1. Set the constants at the top of the driver: `GH_REPO` from `git remote get-url origin`;
      `DEV_BRANCH` — detect with `git ls-remote --heads origin dev` (`""` when it prints
      nothing; offline, leave the default `dev` and note it as a TODO in `docs/rcm-connect.md`);
      `TAG_OWNER`. `PROFILE_FILE` stays `scripts/rcm/profile.release.toml`; `PRESETS_FILE` is
      `$presets_file` from step 1 (set it when it is not the default).
   2. The driver reads `DEFAULT_BRANCH`, `TAG_PATTERN` and `PRESET_PLAN/UPLOAD/REVIEW/GATE/QA`
      from the fragment at runtime (`load_profile` → `release_check.py profile`); nothing is
      copied into the script, and an empty role means "not configured" (S4/S5 are skipped).
      Prove the fragment is usable now:
      ```sh
      presets_file=$(grep -m1 '^presets_file:' docs/rcm-connect.md | cut -d: -f2- | xargs)
      presets_file=${presets_file:-scripts/rcm/presets.release.toml}
      python3 scripts/release/release_check.py profile \
        --file scripts/rcm/profile.release.toml --presets "$presets_file"
      ```
      Prints `REPO_NAME=… PRESET_PLAN=… PRESET_GATE=…` and exits 2 when a referenced preset is
      not in the presets file, `plan`/`upload` is missing, or a value has characters the driver
      would not eval.
3. **Profile fragment** — add `driver = "scripts/release/release_driver.sh"` under
   `[repos.<name>.release]` in `scripts/rcm/profile.release.toml` (never in the live
   `server.toml`). Check: `python3 scripts/rcm/rcm_candidate.py --server <server.toml> --repo
   <name> --presets "$presets_file" --check` — the store skill's helper merges the presets file +
   the fragment into a temporary candidate and runs `rcm check`; the `release <repo>` row is not
   FAIL after the line is added (the row does not print `driver` yet; newer rcm prints
   `driver=<path>` — either is fine).
4. **Selftest** — `scripts/release/release_driver.sh --selftest`: runs `release_check.py
   --selftest` (a PR row whose head sha differs is ignored; DONE only with the exact tag and a
   known N; a rehearsal job is not an upload; qa-only green is not S6; lost/timed_out map to 3;
   `confirm` refuses a wrong or empty N; `version-name` takes iOS first, Android when iOS is
   null, and never invents one) plus a check that `s6_merge` and `s7_upload` call
   `require_typed_n`, and that `--version-id 7` runs stage V against an API shim and sets the
   build name to `1.1.1` while `--build-name` alone takes the old path. Check: "all green",
   exit 0.
5. **Read-only run** — `scripts/release/release_driver.sh --build-name X.Y.Z --status` on a
   version that does not exist: prints `stage S0`, creates nothing (verify with
   `git ls-remote --heads origin release/X.Y.Z` → empty). `--status` runs only the local
   preflight (files, constants, `origin` remote) and, for a branch that does not exist, touches
   nothing but `git ls-remote`. The full preflight (`gh auth status`, `rcm check`, presets on
   the server) belongs to a real round and needs the merged server config installed — do not
   run it while connecting.
6. **Non-interactive contract** — `scripts/release/release_driver.sh --build-name X.Y.Z </dev/null`
   on a throwaway version **only if the owner agrees to a pushed branch and a plan job**;
   expected: S0, S1, then `plan: N = <n>` and exit 2. Abort afterwards with `--abort` and delete
   the branch. Skip this step if the owner does not want a real push during setup.
7. **Detached use from a laptop** — put this in the `docs/rcm-connect.md` section (step 8):
   ```sh
   nohup scripts/release/release_driver.sh --build-name 1.0.1 --confirm-build-number 181 \
     > ~/release-1.0.1.log 2>&1 &
   scripts/release/release_driver.sh --build-name 1.0.1 --status      # any time, any machine
   ```
   The round takes more than an hour (gate ∥ qa); a closed laptop or a killed shell loses
   nothing — re-run the same command and it resumes from remote truth. The Store tab calls the
   same file as `<driver> --build-name X.Y.Z [--version-id <id>] [--confirm-build-number N]
   [--dry-run] [--retry] [--abort] [--status]` (contract §5), reads `plan: N = <n>` on exit 2,
   and passes back only what the human typed in that dialog. A round started from a version page
   carries `--version-id`; a driver from before this skill's V stage is called without it and the
   page says so — re-run this skill to add it.
8. **Record** — append to `<project>/docs/rcm-connect.md`:
   ```
   ## rcm-release-driver — YYYY-MM-DD
   Created: scripts/release/release_driver.sh · scripts/release/release_check.py · `driver` in scripts/rcm/profile.release.toml
   You must fill in: GH_REPO · notify() · s3_pr style · s7_upload inputs · TAG_OWNER
   Verified: bash -n · py_compile · profile fragment readable · --selftest green · --status read-only (stage S0, no branch)
   Detached use: nohup scripts/release/release_driver.sh --build-name X.Y.Z --confirm-build-number N > ~/release-X.Y.Z.log 2>&1 &  ·  --status any time
   ```

## Done means

- `python3 scripts/rcm/rcm_candidate.py --server <server.toml> --repo <name> --presets
  "$presets_file" --check`: the `release <repo>` row is not FAIL with the `driver` line in the
  fragment (newer rcm prints `driver=<path>` on it); the live `server.toml` is unchanged.
- `release_check.py profile --file … --presets …` prints the roles and exits 0.
- `release_driver.sh --selftest` is green.
- `--status` on a fresh version prints `stage S0` and leaves no branch behind.
- `docs/rcm-connect.md` has the section above.

## Never

- Never run S6, S7 or S8 while connecting; verification is `--selftest`, `--status` and at most
  the S0–S2 read-only path with the owner's consent.
- Never let the driver, rcm or a flag supply N: it is typed by a human in the same call
  (`--confirm-build-number` from the dialog, or the TTY prompt) and checked against `plan.json`
  before every irreversible call.
- Never resubmit an upload job that exists in any state — lost or timed out means exit 3 and a
  human reconciles with the store.
- Never make `--status` create anything; never derive the stage from the cache alone.
- Never contact GitHub or a live rcm server while connecting — `--selftest`, `profile` and
  `--status` on a non-existent version are the whole verification.
- Never print a secret: the driver reads `$<secrets_dir_env>` only inside `notify()` and logs
  job numbers, not tokens.
- Never put a project's name, slug, label or context into rcm — they live in this script and
  the profile.
