# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.
The `/api/status` document carries its own `schema_version` — removing or changing the meaning
of a key bumps that number and is listed here.

## [Unreleased]

### Breaking changes
- **Database schema v20.** A new `versions` table holds the store version drafts (one row per
  «new version»: the two store version names, its state, the prefilled and edited listing, and
  the jobs and driver round it is linked to). A v19 file is backed up (`rcm.sqlite3.v19.bak`) and
  migrated on start, and an older build refuses a v20 file and points at that backup, so upgrade
  the server before the workers. `rcm gc` and the retention sweeps leave the new table alone.
  ([#160](https://github.com/monocsp/remote_ci_monitor/pull/160))

### Added
- **Store version drafts expire on their own, and `rcm release` makes one from a terminal.** The
  retention sweep — on its usual cycle and once at server start — now also looks at the version
  drafts. A draft nobody has touched is discarded once `version_ttl_hours` (default 24) have
  passed, down the very route the «discard» button uses, so the App Store version is deleted
  through a `mode = delete` job when the profile has a `version` preset and the row is simply
  closed when it does not. A draft that *was* edited is never deleted automatically: it is only
  marked as expired, with one line in the server log, and stays until a person discards it.
  Submitted versions, drafts still being created and drafts whose round is running are left alone,
  a delete job is submitted at most once per draft, and `rcm gc` still leaves the table untouched.
  New command `rcm release`: `new` asks only for the version names — enter takes the server's
  hint, `-` skips that store, `--yes` takes both hints — then prints the new draft, its state and
  the address of its version page; `list`, `delete <id>` and `open <id>` are the rest. `rcm check`
  also warns now when the `version` preset's `mode` input cannot take `create` and `delete`, which
  used to surface only as a 400 at submit time.
  ([#162](https://github.com/monocsp/remote_ci_monitor/pull/162))
- **Store version drafts: the server routes.** `GET /api/repos/<repo>/release/versions` answers
  the live version names and the next-version hints read from the latest plan, the open drafts and
  the last 20 submitted ones; `POST` opens a draft (202 with a `mode = create` job when the
  profile has a `version` preset, 201 straight into `editing` when it does not) after checking
  that a name looks like `major.minor.patch`, is greater than the live one and is not already
  taken by an open draft (409 `version_exists`). `GET …/versions/<id>` is everything one version
  page needs in one request — the row, the prefilled and the edited listing, their diff, the
  release view, the driver view and the listing file preview — `PUT …/versions/<id>/listing`
  saves one field at a time (allowed keys only, 16 KB each), `GET …/versions/<id>/diff` lists what
  changed, and `DELETE …/versions/<id>` discards a draft, deleting the App Store version through a
  `mode = delete` job when there is one. A finished job updates its row by itself, and a server
  restart re-applies the hook for a job that finished while it was down.
  ([#160](https://github.com/monocsp/remote_ci_monitor/pull/160))
- **`version_id` on plan, review, upload and start.** These take the draft's number instead of a
  typed `build_name` (a `build_name` that disagrees is 400 `build_name_mismatch`). review and
  upload carry the edited listing as `listing_json` when it differs from the prefill, and the
  Android name as `build_name_android` when the two stores get different names; a preset that
  declares neither input is refused (409 `listing_json_unsupported` / `split_version_unsupported`)
  instead of quietly dropping the edit or shipping one name to both stores. `start` passes
  `--version-id` to the driver and links the round to the draft, so `confirm`, `abort` and `retry`
  stay on the same version. ([#160](https://github.com/monocsp/remote_ci_monitor/pull/160))
- **Release contract: the `version` role.** A project may name a `version` preset
  (`[repos.<name>.release.presets] version = …`) that rcm runs with `mode = prefill|create|delete`,
  `ios_version`, `android_version` and `asc_version_id`; it writes `version.json` (create ·
  delete) and `prefill.json` (prefill · create), exit `3` = already exists, `4` = not deletable.
  `rcm check` fails when the preset lacks those inputs or its `mode` does not default to
  `prefill`, and warns when the `review` / `upload` preset has no `listing_json` input. New
  profile key `version_ttl_hours` (default 24, integer ≥ 1). `plan.json` may carry
  `store.play.production_name` and `next_version_hint`; the driver takes `--version-id <id>`
  (stage `V`). ([#159](https://github.com/monocsp/remote_ci_monitor/pull/159))
- **Skills: `release_version.sh` and `listing_json`.** `rcm-store-connect` ships a fourth
  skeleton (prefill from the live listing or the `store/` files, create / delete an App Store
  version, `--selftest`), the `release-version` preset, the `version` profile line, and
  `rcm_contract.py` kinds `version` / `prefill`. `release_review.sh` / `release_upload.sh` read
  `RCM_INPUT_LISTING_JSON`, write `listing.json`, hand its path to the store hooks and echo its
  fields in the review-plan preview; an adopted script gets only that handling added.
  `rcm-release-driver` resolves the build name from rcm's version row with `--version-id`.
  ([#159](https://github.com/monocsp/remote_ci_monitor/pull/159))
- **Release contract: two store version names.** One round may ship a different version name to
  each store (App Store 1.1.1 · Google Play 1.0.1), because the live names have drifted apart. rcm
  sends `build_name` as the representative name and adds the new optional input
  `build_name_android` to the `review` / `upload` presets only when the two differ; `rcm check`
  warns when a preset does not declare it, and a round with two names is refused (409
  `split_version_unsupported`) rather than built under one. `{version}` in `tag` is the shared
  name, or the two joined with `+` (`prod/1.1.1+1.0.1-181`); in `listing.release_notes` it is the
  iOS name, falling back to the Android one. `release_upload.sh` / `release_review.sh` read
  `RCM_INPUT_BUILD_NAME_ANDROID` and hand each store its own name through `platform_build_name`.
  ([#159](https://github.com/monocsp/remote_ci_monitor/pull/159))

## [0.3.3] - 2026-09-18

### Fixed
- **Store tab: the top release bar was always full.** Its fill width was an inline `style`
  attribute, which the page's Content-Security-Policy (`style-src 'self'`) drops; the fill now
  goes through the same `data-fill` pass as every other bar. While only a release job runs (no
  driver round) the bar's detail no longer talks about «0 of 9 stages» and no longer says
  «elapsed 0s» — it names the job, its own counter and the elapsed time from the queue row.
  ([#156](https://github.com/monocsp/remote_ci_monitor/pull/156))
- **Store tab: listing preview in table form is read.** Besides `key: value`, a preview line
  shaped `key   12자   value` (two or more spaces, an optional character count) under a section
  header such as `iOS (ko)` / `Android (ko-KR)` is a field; `name` is the title, `(비어 있음)`
  is empty. Lines that are not a known field stay under «other preview lines».
  ([#156](https://github.com/monocsp/remote_ci_monitor/pull/156))
- **Store tab: the Build · upload checklist shows this round only.** Jobs before the latest
  plan job fold into «earlier jobs · n». ([#156](https://github.com/monocsp/remote_ci_monitor/pull/156))
- **Store tab: `--status` gets `--build-name`.** The driver view passes the build name of the
  latest run or plan, so a driver that asks «which version?» (exit 2) answers instead.
  ([#156](https://github.com/monocsp/remote_ci_monitor/pull/156))

## [0.3.2] - 2026-09-18

### Added
- **Store tab: a big release bar at the top of the page while a round runs.** Version and build
  number in the title size, the current stage (`S5 scenario QA · stage 6 of 9`), the overall
  percentage (9 declared stages of equal weight, plus the running job's own progress inside its
  stage; capped at 99 until the round is done), the «Now:» line, elapsed time and the expected
  finish. Hovering the bar (or focusing it) shows the whole detail — stages done, current stage,
  the job's own counter, elapsed, finish, and the basis — and the same text is the bar's `title`
  and `aria-valuetext`. Colours follow the rows: blue running, ochre when a person's turn or store
  drift, purple when the result is unknown, red when failed. With no driver in the profile the bar
  belongs to the running release job alone. ([#153](https://github.com/monocsp/remote_ci_monitor/pull/153))
- **`[repos.<name>.release.listing] ref`** — the branch the store copy (preview, diff, release
  notes, screenshots, validate) is read from; default `default_branch`. A project that ships
  `dev → main` keeps the copy it is about to ship on `dev`, so it sets `ref = "dev"` and the
  review panel says «from dev @ sha». ([#153](https://github.com/monocsp/remote_ci_monitor/pull/153))

### Changed
- **Web: one type scale.** Five text styles — title 18/600 (page heading, the release bar's
  version), subtitle 14/600 (row, section and dialog headings), body 13/400, caption 12/400
  (`.sub`, footers, basis lines) and label 11.5/600 (column heads, `dt`, small headings) — as
  `--t-*` tokens on `:root`; the body went from 14 to 13 px and every heading and label now uses a
  token instead of its own size. ([#153](https://github.com/monocsp/remote_ci_monitor/pull/153))
- **Store tab: a «Build number: Auto (from the stores) · Type it myself» toggle.** The review
  panel and the Start form share it; the default comes from the profile's `build_number_policy`
  (`auto` → Auto, `manual` → Type it myself) and a click changes it for this page only. In Auto
  the page shows the plan's next build number (the number the plan read from App Store Connect
  and Google Play) instead of an input, **Submit for review** and the S2 **Confirm** open without
  typing, and the request carries `confirm_build_number: "auto"` (review, upload) or
  `build_number: "auto"` (driver confirm); the server fills in the plan's `n` itself. A round
  started with `build_number: "auto"` continues by itself when the driver stops at S2 with a
  `plan: N` line — the server re-runs it with `--confirm-build-number N` and the ledger row says
  `started_by: "<who> (auto)"`. Auto never invents a number: no plan, a stale plan, a blocked
  plan or a plan without `n` refuse exactly as before (409 `plan_required` / `plan_stale` /
  `build_number_mismatch`). Type it myself is unchanged. Database schema **v19** adds
  `releases.auto_n` (a v18 file is backed up and migrated on start). ([#152](https://github.com/monocsp/remote_ci_monitor/pull/152))

## [0.3.1] - 2026-09-17

### Fixed
- **Store tab, first use: «Refresh (release-plan)» asks for the version instead of failing
  silently.** With no plan yet the page sent `build_name: ""`; the server answered 400
  `build_name is required` and the message sat in the body of the folded grey Store row, so the
  click looked like nothing happened. Now, when no build name is known, the button opens a
  «Store snapshot for which version?» dialog (`major.minor.patch`, prefilled from the newest
  `prod/<version>-<build>` tag on the GitHub card, else the last plan's build name) and sends
  `{build_name, ref}` on Go; when the version is known the one-click refresh stays and a small
  «…for another version» button opens the same dialog. A refused plan call is now written in red
  in the Store row **head** and opens the row for that render, and a refused review call opens the
  review panel and repeats the code in its head.

## [0.3.0] - 2026-09-17

### Added
- **Store tab server API, part 2: the release routes and the driver.** Under
  `/api/repos/<repo>/release/…` the server now turns the profile into actions. `GET …/release`
  is the state of the release view — the latest job per role (`plan`, `review.plan`,
  `review.result`, `upload`) with the artifact document (`plan.json`, `review-plan.json`,
  `review.json`, `upload.json`) read from the job's bundle, its age and whether it is stale, plus
  the role presets' jobs newest first. `POST …/release/plan`, `…/review` and `…/upload` submit the
  role presets through the normal job path with exactly the contract's inputs; the irreversible
  modes (`review mode=submit`, `upload mode=upload`) need an admin token and the server itself
  refuses them without a fresh succeeded plan, a typed build number equal to the plan's `n`, a
  succeeded and unblocked review plan, and — for Android — `play_managed_publishing =
  confirmed-on` in that very request (409 `review_plan_required` / `review_plan_stale` /
  `review_plan_blocked` / `managed_publishing_unconfirmed` / `plan_required` / `plan_stale` /
  `build_number_mismatch`; a preset that declares `automatic_release`, `rollout` or
  `release_status` is refused with `unsafe_preset`). `GET …/release/listing` runs the profile's
  preview and diff commands in a checkout of `default_branch` from the mirror and lists release
  notes and screenshots (`…/listing/file?path=` serves one; `POST …/listing/validate` runs the
  validate command); `GET …/release/github` reads the last commits and release tags from the
  mirror (pull requests are `null` for now). When the profile names a `driver`, `POST
  …/release/start` runs it detached in its own checkout with the job environment, `RCM_SERVER`
  and a client token minted for that run and revoked when it ends; `…/confirm` forwards the build
  number a person typed only when it equals the log's last `plan: N = <n>`, `…/abort` and
  `…/retry` pass the flags, and `GET …/release/driver` shows the run, the log tail, `plan_n` and
  `--status`. Runs are recorded in a new `releases` table — database schema **v18** (a `v17`
  backup is written first, and older builds refuse the file); `rcm gc` leaves that table alone.
  All write routes answer 409 `setup_incomplete` until the secrets gate is open. Documented in
  `docs/configuration.md` («Release routes and the driver»). `schema_version` of `/api/status`
  is unchanged.
- **Web UI: the Store tab and its settings gate.** When `GET /api/repos` lists a repository
  with a release profile the header shows a **Queue | Store** switch (a select when there are
  several) and `#/store/<name>` opens the Store; a server without a profile has no tab and the
  queue keeps updating over the event stream either way. Until every required secret is present
  and verified, the Store route lands on the Settings screen: a banner with `n of m secrets set ·
  k verified` (red, then green) and a disabled **Enter Store**, one row per secret from the
  profile — kind, present, fingerprint, verified time or error — with a password dialog for
  values, a dropzone (drag-and-drop or file picker) for files and per-file dropzones for folders,
  plus **Verify all**. The page never keeps or shows a value; a non-admin token sees the table
  read-only with the reason in one line. Once complete, the Store screen shows four collapsible
  rows: Setup (the same table), Source (mirror age, `main` / `dev` SHAs, whether `main` is in
  `dev`, **Fetch remote**), and Build · upload and Store as grey "not available in this build"
  rows; green rows are collapsed, red and stale rows open, and a row a person opens or closes is
  remembered in the browser.
- **Web UI: the review panel and the Store / Build · upload rows.** With `GET
  /api/repos/<name>/release` the Store row summarises `plan.json` (App Store live and editing
  versions, the Play track, `next N`, blockers and warnings, the plan's age — red on blockers, amber
  when stale) with a **Refresh** that submits the plan preset, and the Build · upload row shows
  `upload.json` (version, build number, platforms, upload time, tag; a lost upload says so and offers
  no resubmit) or, while a release job runs, three layers: one long bar that names its basis
  (progress marker · declared steps · measured time · none), a «Now» line with the job's current
  step and marker, and a checklist of the round's jobs with the running job's bar and unit grid. The
  review panel below is laid out like an App Store Connect version page with the Google Play
  section beside it in the same group order — screenshots from the listing preview, copy fields
  with `current/limit` counters and `changed` chips from the diff, release notes counted against
  both limits, build/release with «fixed by the repo» and a managed-publishing pill that never turns
  green, review information as present/absent only — then the diff, five checkboxes, a build-number
  box and **Validate listing** · **Plan review** · **Submit for review…**. Submit is enabled only
  when the review plan is green and fresh, the typed number equals the plan's `n`, and, when Google
  Play is selected, the managed-publishing box is ticked in this submission (it resets with every new
  plan and after a submit); the confirmation dialog names the stores and says it cannot be undone.
  A `409` from the server shows its code next to the button; `unsafe_release_type` or an observed
  `auto_release: true` raises a red banner that cannot be dismissed; the result (`submitted` ·
  `partial` · `noop` · `failed`) with the observed store state replaces the panel body. There is no
  Release, Publish or Rollout button in any state. A server without the release routes shows the two
  rows and the panel as «not available in this build».
- **Web UI: the release-driver stepper, the GitHub card and the upload rehearsal.** With `GET
  /api/repos/<name>/release/driver` the Build · upload row shows the round as nine steps S0–S8 above
  the job cards, read from the driver's own `--status` lines and log tail (`stage S<n>` /
  `다음 단계 S<n>`; exit 0 is DONE, exit 2 is S2): done steps green ✓, the current one blue ▶, the rest
  grey ·. S2 is the human step — when the driver stopped with exit 2 and a `plan: N = <n>`, a dialog
  asks for the build number typed again and **Confirm N** opens only on an exact match, sending
  `POST …/release/confirm {build_name, build_number}`. With no round the row offers a Start form
  (version `X.Y.Z` · Android track · dry-run → `POST …/release/start`); while it runs the header names
  the stage and **Abort** is live; exit 1 is red with **Retry same version**, exit 3 is purple «result
  unknown — do not resubmit» with no retry, exit 4 is amber store drift, a closed PR is amber
  blocked; a `409` (`release_running`, `build_number_mismatch`, …) shows the server's code. The Source
  row gains a GitHub card from `GET …/release/github`: the mirror's last five commits (sha7 · subject ·
  author · time), its tags with `latest`, and «PR list: next (needs the GH token)». A **Rehearsal (no
  upload)** button posts `{mode: "rehearsal", build_name, confirm_build_number: <plan.n>}` to
  `…/release/upload`; there is deliberately no `mode=upload` button — the driver's S7 is the upload
  path. The Store row renders object values from `plan.json` (a Play track as `{name, status,
  codes}`) as their codes or name, never `[object Object]`, and the Setup head's «verified» time is the
  newest `verified_at` of any secret kind, with «n not checked» when a count is reported. Servers
  without the routes say «not available in this build».

- **A step can report its own progress.** `::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]`
  at the start of a stdout line says how far the **current step** is — a chunk loop, a parallel set,
  a lock wait — with a denominator the script actually knows (`state` is one of `run · ok · fail ·
  skip · env · review · blocked · wait`). `progress` in queue rows and `GET /jobs/<id>` gains `sub`
  (the last line, or `null`), `units[]` (the last state per unit, first-seen order, 500 per step) and
  `units_truncated`. The web queue draws the bar from `sub` before declared steps and time
  (`60% · 41/68 · inquiry_photo/android`), adds `now: <unit> · <state>` to the progress line and,
  with two or more units, a grid of unit cells under the step list. A new `::rcm::step::` clears it;
  malformed lines are plain log lines; markers are stored as before, so there is no migration, and
  `schema_version` stays 1 because keys were only added.
- **The connect skills ship in the package.** `rcm skills list` now names five skills and
  `rcm skills install --into <project>` copies them: `rcm-connect` (one entry point: project ·
  repo name · platforms · optional tiers), `rcm-store-connect` (required tier: profile block,
  secrets list, `plan`/`upload`/`review` presets and script skeletons with `--selftest`, a
  candidate-config checker), `rcm-gate-connect`, `rcm-qa-connect`, `rcm-release-driver`. Skills
  write only into the project, adopt existing real implementations, verify with `rcm check` on a
  candidate copy of `server.toml`, and never run upload or submit. The contract they produce is
  `docs/release-contract.md`; the Store tab wireframe is `docs/wireframes/web-store.html`;
  README gained a «Store tab» section.
- **Release profiles and the connect skills.** `[repos.<name>.release]` in `server.toml` says
  which presets play the `plan` / `upload` / `review` roles (plus optional `gate`, `qa`, `dev`),
  which secrets the Settings screen will ask for, and how the store copy is previewed — the
  contract the Store tab reads (`docs/release-contract.md`). `rcm check --config` prints one
  `release <repo>` row per profile: FAIL when a required role is empty or names a missing preset,
  when a preset lacks an input rcm sends, when the irreversible `mode` (`upload` / `submit`) is a
  preset's default, or when secrets are listed without `secrets_dir_env`; warn when an optional
  role is unset or the secrets folder does not exist yet. `rcm skills list` and
  `rcm skills install --into <project>` copy the packaged `rcm-*-connect` skills into
  `<project>/.claude/skills/`, keeping identical files, refusing to overwrite changed ones
  without `--force`. `docs/configuration.md` gained a "Release profile" section and
  `examples/server.toml` a commented profile block.
- **The Store tab's server side: repositories, secrets and the setup gate.** `GET /api/repos`
  lists every repository with whether it has a release profile and how far its setup is;
  `GET /api/repos/<repo>` returns the profile (never a value), the mirror's age and the `main` /
  `dev` heads with `main_in_dev`, all read from the mirror; `POST /api/repos/<repo>/fetch` updates
  the mirror under the lanes' lock. Secrets live as files under `<config dir>/secrets/<repo>/`
  (folder `0700`, files `0600`, written atomically): `GET …/secrets` shows name, kind, presence,
  size and a fingerprint (first four characters of a value, eight hex digits of a file's SHA-256,
  `n/m files` for a folder) and never the value; admins `PUT` a value (`text/plain`), a file
  (`application/octet-stream`, at most `max_kb`) or a folder's file, and may `DELETE` only optional
  ones; `POST …/verify` runs the read-only checks — `github` and `keystore` are real, `asc` and
  `play` honestly answer `not implemented in this build` — and records the result. Jobs of a
  preset whose `repo` has a profile get the folder as `$<secrets_dir_env>` (when it exists) and
  their stdout masked: every value secret of 8+ characters becomes `****` in the log, for local
  lanes and remote workers alike. `setup.complete` (all required secrets present and verified)
  is the gate the release routes of the next change will enforce; none of these routes 409.
  Adding routes changes no status key, so `schema_version` stays 1.

### Fixed
- **A snapshot blob the server only *thinks* it has no longer kills the job.** The `blobs` table and the
  files on disk can drift apart — on one server every blob file written before 2026-09-13 was gone while
  6,579 rows stayed. The manifest negotiation answered "have it" from the table, the session skipped the
  upload, and materialization failed with `snapshot blob missing` on seven consecutive `gate-fast` runs
  while the same file kept failing. Now the negotiation checks the file, not the row, and drops rows
  without files; the retention sweep drops them too; and a worker that still hits `blob_missing` deletes
  that row so the next submission uploads the file. No migration.

### Changed
- **A job that has gone quiet is no longer called stuck.** A running job was marked
  `likely stuck` — the loudest red on the page — as soon as it went `no_output_seconds`
  (4 minutes) without writing a line. That is a normal shape for real work: the `gate` preset
  spends its last step running nine test shards, gitleaks and a web build in parallel, and those
  tools buffer their output until they finish. So `gate` tripped the alarm on **every healthy
  run**. Observed on 2026-09-15: 17m 24s elapsed, one times its own estimate, step 49/49 still
  advancing, and the screen said the job had stopped.
  Silence alone is now its own state, `quiet` — grey, not an alarm, and deliberately not in
  `ACTIONABLE_REASONS`, so it stays out of the "needs a look" summary. A job is called stuck when
  its **current step** has run past its own measured median (`[estimate] step_stuck_multiplier`,
  default 3), which the server learns from the step markers past successful runs already wrote;
  no migration, and the threshold never drops below `no_output_seconds`, so a step that normally
  takes half a second cannot be declared dead in a second and a half. The old whole-job rule
  (`elapsed > stuck_multiplier * expected`) is unchanged, and silence is still the verdict for a
  job that prints no step markers at all — there the screen has nothing else to go on.
  `estimate` gained three keys (`quiet`, `stuck_code`, `step_expected_seconds`) and `reason`
  gained one value; `schema_version` stays 1, because adding keys is free and the meaning of
  `estimate.stuck` did not change — what changed is the evidence behind it, which
  `no_output_seconds` already moved. `[estimate] step_min_samples` (default 3) is how many past
  runs a step needs before its median is trusted.
- **The reason for calling a job stuck now matches the evidence.** The screen appended
  `n× expected` to every stuck job whether or not the multiple was what tripped it, so a job
  caught by silence read `likely stuck · 1× expected` — a warning next to the evidence that it
  was exactly on schedule. The server now names the trigger (`over_step`, `over_elapsed`,
  `no_output`) and the screen prints only that.
- **The Korean screen says what it means.** 311 strings were read end to end against the
  translationese rules of [im-not-ai](https://github.com/cloudhat/im-not-ai). The classic
  patterns were already absent, but the copy leaned on implementation words — `잡`, `레인`,
  `키`, `소스`, `풀`, `프리셋`, `표본`, `load`, `스텝` — and on `멈춘 듯`, a literal rendering of
  Jenkins' `likely stuck` that is not a form Korean interfaces use. 47 strings changed:
  `잡` is now `작업` throughout, queue groups read `작업 중` and `대기열` while a row's own state
  reads `진행 중`, `멈춘 듯` is `응답 없음`, `레인 1/1 사용 중` is `동시 실행 1/1`, and
  `load 10.3` is `처리 대기 10.3 · 코어 10개 기준`. Three strings had drifted out of the
  catalogue's polite register and were brought back. Seven strings dodged Korean particle
  agreement by printing `이(가)`; a helper now picks the particle from the final consonant of
  whatever name the server sent, including digits and Latin letters read aloud. The emoji and
  box-drawing glyphs in twelve strings are inline SVG in the renderer — the pill glyphs stay,
  because those are the shape channel that carries state without color.
- **The queue table says a job is in trouble once, not three times.** The same fact was painted
  red in three places at once: the summary panel, a filled block inside the reason cell, and a
  hatched progress bar with its own label. A row now carries one 3px status rail on its left and
  one filled chip, the reason cell is plain text, and the progress bar moved inside the elapsed
  column, which drops a whole table row per running job. The label under that bar says only how
  far along the job is and what the number was measured against — `41% · 20/49 steps`, `88% · by
  measured time`. It no longer repeats the condition the chip and the status column have already
  said twice: inside a 168px column `41% · 20/49 steps · not responding` wrapped onto a second
  line and mid-phrase, and that row stood 13px taller than the ones around it. The condition is
  now carried by the bar's own colour and hatching, and it stays word for word in the bar's
  `aria-valuetext` and in the label's tooltip — it left the pixels, not the page.
  The summary cells and the host section
  are cards with a coloured top edge, and recent results carry a rail in their outcome's colour.
  No new colour tokens; the light-theme contrast figures in `style.css` were re-measured against
  the new backgrounds and the note updated.

### Fixed
- **A fetch that got nothing no longer exits 0.** `rcm run --fetch-artifacts` and
  `rcm artifacts --fetch` asked the server for the bundle and, for **every** state other than
  `ready`, returned no verdict at all — both callers read that as success. The comment defended
  two of them (`disabled`, `empty`: the preset declared no globs, or they matched nothing, and
  neither is a delivery failure). The code also covered `pending`, `collecting`, `uploading`,
  `dropped`, `failed`, `skipped`, `purged`, `expired`, `unavailable` and `unknown`. So fetching
  from a job that had not finished left an empty directory and exited 0, and a script read that
  as "I have the files". Now only `disabled` and `empty` are 0; everything else is the delivery
  code 5, and a state added later is 5 until someone decides otherwise. The reason is on the
  `artifact_fetch` JSON object, which now also appears for these states instead of being absent.
- **`--dry-run` no longer reports a clean preview as a failure.** It always returned
  `complete: false`, so a preview with nothing conflicting still exited 5. It now exits with the
  code the real run would have used — 0 for a clean plan, 5 if anything is `conflicted` — which
  makes it a cheap "would this apply cleanly?" check. Its summary line also stopped contradicting
  the table printed directly above it: it said `wrote 0, unchanged 0, conflicted 0` no matter what
  the plan held, and now reads `would write N, unchanged N, conflicted N`.
- **A job that never started now says so.** A job whose workspace could not be built — a snapshot
  the server could not unpack, a blob retention had already deleted, a `git fetch` that timed out,
  a repository no longer in `[[repos]]` — ended `failed` with a sentence and no code, which is
  exactly what a job whose tests failed looks like. Only one of these failures carried a code.
  Every one of them has its own now, and the code says which thing to fix: `snapshot_missing`,
  `snapshot_rejected`, `blob_missing`, `repo_missing`, `commit_missing`, `git_failed` and
  `snapshot_download_failed` for a workspace that could not be built; `launch_executable_missing`,
  `launch_permission_denied`, `launch_failed` and `log_unavailable` for a process that could not
  be started; `preset_missing` for a preset that left the config while the job waited;
  `tool_missing` as before. All of them have `exit_code: null`, so a script can tell "my tests
  failed" from "the job never ran" without reading the sentence. `summary_args` carries the
  arguments, so the web page says it in Korean or English instead of repeating the server's
  English — and `tool_missing`, which had never been added to either locale, is in both now.
  Remote workers report from the same table, so the code on a row does not depend on which lane
  picked the job up — including a preset deleted while the job waited, which the remote worker
  used to report as a launch failure. A worker still running an older build is the exception: the
  version is only checked when it registers, so until it is restarted it keeps reporting the
  sentence it used to and the server stores that with no code. Jobs that ended before this release
  keep their stored sentence and `summary_code: null`; nothing is invented for them.
- **Cancelling a job while its workspace is being prepared now wins.** A cancel accepted during a
  long `git fetch` was overwritten by whatever the fetch failed with, so a job you stopped was
  recorded as a failure. It ends `cancelled`, named after whoever cancelled it, the way a job
  stopped before a missing tool already did. This holds on a remote worker too: the server used
  to decide with a copy of the job state it had read before the cancel landed, and then write the
  failure unconditionally.
- **A snapshot member whose name is not valid UTF-8 no longer takes the lane down.** The rejection
  message carried the raw name into the database, the write raised, and the worker thread died
  `down` instead of failing that one job.

### Security
- **The job document no longer carries free text from a failure before the job starts.** A preset
  whose `argv[0]` could not be launched put that absolute path into the job summary, and
  `/api/status` is readable without a token in the default configuration — the same exposure that
  was closed for `requires` entries in 0.2.6. Scrubbing the text was not enough: a relative path,
  `~/…`, `$HOME/…`, a Windows or UNC path, a host name or a token-shaped string passes any path
  pattern. So these summaries no longer carry text at all. They carry a code from a closed list
  and arguments of a declared shape — an exception class name, a git operation, an exit code, a
  hex sha, a repository or preset name already in the job document. The original text — `argv[0]`,
  the git stderr, the exception — goes to the job log, which always needs a token, from a remote
  worker as well as a local lane. The name of a rejected archive member is kept, because it came
  from the snapshot you sent and it is what you need to fix it, but only its last segment, with
  `\` read as a separator and invisible and direction-changing characters removed. A remote
  worker's report is checked against the same table on the server: the worker is authenticated,
  not trusted.

## [0.2.9] - 2026-09-15

Starting the server says what it is turning on, one line per step.

### Added
- **Starting says what it is turning on.** `rcm serve` used to print nothing until everything was
  up, and then one `listening on …` line; a slow migration or an mDNS responder waiting on Local
  Network permission looked exactly like a hang. Startup is now ten steps — the port, the
  database, the presets, then the seven services — and each prints as it finishes with the seconds
  it took (`start 3/10 presets · 10 · gate, gate-fast, gate-commit …`). The banner still closes
  the sequence and still lists every preset. This is what to read after a restart that added
  presets: the count in the step line is the server's own, so a preset that did not load is
  visible before the first job is submitted.
  ([#128](https://github.com/monocsp/remote_ci_monitor/pull/128))

### Fixed
- **`rcm run --fetch-artifacts` now writes the files for a `git_ref` preset.** It checked the
  usage twice — `--output DIR` is required because there is no local tree, and it cannot be
  combined with `--no-wait` — and then skipped the fetch: the job went green, the exit code was
  0, and nothing was written. An empty hand looked like a pass, with no signal anywhere. On the
  build machine this was not an edge: **every preset that declares `artifacts` is `git_ref`**
  (`release-plan`, `scenario-qa`, `release-upload`, `build-dev`, `deploy-dev`), so the flag
  delivered nothing for any of them and the two-step `rcm artifacts <id> --fetch --output DIR`
  was the only way to get a file. The baseline is empty — there is no submitted tree to compare
  against — so an existing file still needs `--force`, the same rule `rcm artifacts` follows.
  A test now runs a `git_ref` job to the end and counts the files it received; refusing a bad
  usage was locked before, and that is what let the gap through.

## [0.2.8] - 2026-09-15

The page stops moving under your cursor while the build machine works, and the README walks
through a gate from a session.

### Fixed
- **The host section no longer opens and closes itself while you read.** On a build machine that
  is actually building, CPU crosses 85% every few seconds; the page re-applied that verdict to the
  section's open state on every refresh, so the host panel unfolded and folded again and the
  recent-jobs list below it jumped by 315 pixels each time — three times in two minutes on the
  reference machine, a cumulative layout shift of 0.245. Load now only colours the one-line
  summary. The section opens by itself for the things you have to act on — a stale sample, a host
  that cannot be read, a disk with less than 10 GiB free — and never folds itself back up;
  closing it is yours to do, and your own choice still wins over both. The `busy` verdict also
  gained hysteresis: it turns on at 85% and only clears once every value is below 80%, so the word
  beside the heading stops flickering with each sample. The percentages themselves still turn
  amber at 85%. ([#124](https://github.com/monocsp/remote_ci_monitor/pull/124))

### Changed
- **The README walks through a gate from a session.** A numbered section — `rcm check`,
  `rcm eta`, `rcm run` and its inputs, the exit codes and the JSON keys, logs and artifacts when
  it is red, detaching and `rcm wait`, cancel and join, lighter scopes, keeping the client
  current — now sits between the session set-up and the command table, mirrored in
  `README.ko.md`. ([#122](https://github.com/monocsp/remote_ci_monitor/pull/122))

## [0.2.7] - 2026-09-13

### Fixed
- **`rcm serve` takes its port before it opens the database.** Started next to a running
  service with the same config — another build, a copy of the config — it used to migrate the
  database first and only then print `cannot start server: Address already in use`, leaving the
  running service unable to restart. The socket is bound first; if that fails the database is not
  opened, not backed up and not changed. Also, `rcm serve` and `rcm token` now refuse in one
  `rcm:` line (exit 2) for every way the database cannot be opened — a file that is not a
  database, a read-only data directory, a migration statement that fails, a `data_dir` that is a
  file, another process holding the write lock — where before only a failed migration backup and
  a newer schema were one line and the rest was a traceback. (`rcm gc --dry-run` keeps its exit
  3 for the newer-schema case: it answers "unknown", the other two answer "fix the setup".)
  ([#118](https://github.com/monocsp/remote_ci_monitor/pull/118))
- **The client-update wrapper keeps to its own directory, keeps the token out of `ps`, and reads
  the status code instead of trusting it.** `examples/session/update-client.sh` verified the wheel
  in a private file but then moved it to a shared `$TMPDIR/remote_ci_monitor-<version>….whl` to
  install — two wrappers against two `dev` servers of the same version could install each other's
  bytes, and a file of yours at that name was overwritten. It also put `RCM_TOKEN` on curl's
  command line, where any user of the machine could read it with `ps`, and used `curl -f`, which
  died on a 503 that still carried a perfectly good wheel. Now everything lives in one
  `mktemp -d` (0700) deleted on exit, the token travels in a 0600 curl config file (`-K`), and a
  503 from `janitor stale` or a worker down is judged by its body. The same 503 no longer hides the
  compatibility verdict on the client side either: `rcm check` keeps its `client` row (the
  `server` row is `FAIL` with the reason) and `rcm run` still prints its warning, because
  `Client.health()` returns the 503 body rather than raising. And `/client/<name>.whl/` — the
  exact name plus a trailing slash — is a 404 like every other name, not an alias. The `rcm check`
  screenshot in both usage guides now shows the `client` row the text describes.
  ([#119](https://github.com/monocsp/remote_ci_monitor/pull/119) ·
  [Keeping clients on the server's version](docs/operating.md#keeping-clients-on-the-servers-version))
- **`rcm gc --dry-run --config` now exits 3 on every incomplete path, and the old-build refusal
  names a backup that exists.** A migration statement that failed on the temporary copy raised a
  raw `sqlite3.Error` past the command and `main()` — a traceback and exit 1, for exactly the
  failure the preview exists to catch before a restart; it is now one line on stderr and exit 3.
  A temporary directory that could not be created, or a copy that could not be removed afterwards,
  is exit 3 as well — the copy of the live database left in `$TMPDIR` is named on stderr instead
  of being reported as cleaned up. The `newer than this build` refusal used to compute the file to
  restore from the running build's own schema version, so a v15 database upgraded straight to a
  much newer build told a v16 build to restore a `v16.bak` that never existed; it now points at the
  highest `rcm.sqlite3.v<n>.bak` actually present in `backup/`, and says
  `no migration backup found` when there is none. A `backup/` directory that cannot be listed
  while pruning old backups warns instead of silently keeping every file.
  ([#117](https://github.com/monocsp/remote_ci_monitor/pull/117))
- **The production guard knows which virtualenv is the service's.** It used to take the venv of
  whatever `rcm` was first on `PATH`; in a shell with a worktree's `.venv` activated that is the
  worktree's build, so the guard called it "the service's own" and let it open the production
  database — the 2026-09-10 shape. The service venv is now the one whose editable install
  (`direct_url.json`) points at the primary checkout, never a linked worktree. `rcm serve` and
  `rcm worker` are judged by the data directory they would actually open, like `rcm token` — a
  copy of the production config or `RCM_SERVER_DATA_DIR` pointing at production is refused —
  `cd <dir> && rcm …` is judged in `<dir>`, `$XDG_CONFIG_HOME` is searched where the CLI searches
  it, and `env -i`, `env -u NAME`, `exec`, `nohup`, `time` and `( … )` groups no longer hide a
  command. Discovery no longer takes the session's `$XDG_CONFIG_HOME` for the service's config
  directory — a service unit does not inherit a shell's environment, and following it made a
  test config under XDG "production" while the real data directory went unguarded. The
  `RCM_<SECTION>_<KEY>` environment override and what an empty value means are now in
  [Configuration](docs/configuration.md).
  ([#89](https://github.com/monocsp/remote_ci_monitor/pull/89) review,
  [#93](https://github.com/monocsp/remote_ci_monitor/pull/93) review)
- **The `rcm gc` receipt no longer states what it does not know.** A job whose size could not be
  measured can still be deleted by the age rule; the receipt then printed `freed 0 B from 1 jobs`
  as if it had been measured. It now gives a lower bound — `freed ≥ 0 B from 1 jobs (1 of unknown
  size)` — and the JSON carries `unknown_count`. A delete that got only half way (workspace
  removed, snapshot tar not) was counted as a plain failure, so the inventory was not measured
  again and the size cache kept the old figure: `storage_after` equalled `storage_before` and
  `/api/status` went on showing bytes that were gone. The `failed[]` entry now says what went
  (`"removed": ["workspace"]`), the receipt counts it (`1 failed (EACCES · 1 partly deleted)`), the
  cache entry is dropped and the inventory is re-measured. In `rcm check`, a measurement failure
  while free space is under the floor read as `… and nothing left to delete` (FAIL), and one after
  the floor rule had paused itself read as `deleting stopped helping`; it now reads as the
  measurement failure it is (warn, with the error code) ahead of both, and the row for a budget that
  running jobs alone exceed says how old its number is like the other rows. A job directory
  the server cannot read (`jobs/<id>` without permission) used to fail the whole inventory as
  `scan_EACCES` — no age rule ran for anyone and every figure read `—`; now only that job's
  snapshot is unknown (`measure_EACCES`, `1 of unknown size`) and the rest is measured and
  purged as usual. `schema_version` unchanged — keys were only added. (M5l L2, review of #88)
- **A `main` merge that does not bump `__version__` no longer fails the `Tag release` run.** The
  existing `v<X>` tag is a no-op when it points at an ancestor of the merge commit (a docs-only
  merge, a follow-up after a release); only a tag on a commit that is *not* an ancestor — a reused
  version number — still fails. Re-running a release whose tag already exists is documented
  ([Releasing](CONTRIBUTING.md#releasing)): Actions **Re-run**, or delete the tag and push it again.
### Added
- **A cancel token per submission, so a shared client token cannot cancel another session's job.**
  Every `POST /jobs` — a join too — now answers with `submission: {id, cancel_token}`; the
  requester's token cancels the job, a joiner's only leaves the join list, and the server keeps a
  SHA-256 of it (database **v17**, table `submissions`, backed up as `rcm.sqlite3.v16.bak` before
  the upgrade and deleted with the job's metadata). `rcm run` saves the token in
  `~/.local/state/rcm/submissions.json` (mode 0600) and `rcm cancel N` sends it — or takes
  `--cancel-token` from a wrapper that kept the `--no-wait` JSON, which now carries `submission`
  before `url`. Nothing changes by default: `[server] cancel_requires_submission_token = false`
  keeps today's rules, so 0.2.x clients keep cancelling. With the key on, only a cancel token or an
  admin token cancels — there is no non-admin `--force` — and clients before 0.2.7 get 403;
  `/api/health` then raises `min_client_version` to 0.2.7 (with `cancel_min_client_version` saying
  why, so an old client's `rcm check` fails its `client` row), `rcm check` prints a `cancel` row, and
  the web **Cancel** button is disabled for non-admin tokens with the reason in the row. Ctrl-C
  still detaches the requester and only removes a joiner. Two sessions of the same user share the
  state file: `rcm cancel N` sends the newest token that this client token saved for that job —
  never one another token saved — so a session that joined only leaves (`--submission-id` picks
  another); a joiner's token is bound to the token
  name it was issued to and works once; a token that worked is removed from the file, which keeps
  200 entries and above that drops only finished jobs; the file is locked while written, and a
  save that fails is said in one line without a path. `rcm check` fails its `client` row whenever
  the client is below the server's `min_client_version`, even at an equal version number.
  ([Configuration](docs/configuration.md#who-may-cancel-a-job))
  ([#110](https://github.com/monocsp/remote_ci_monitor/pull/110))
- **Two lanes for a gate with a light phase and a heavy phase.** `docs/configuration.md` now has
  a section on the two-lane experiment: `lanes = 2` with the gate preset's `concurrency_group`
  removed is safe only when the script itself serialises its heavy section with a machine-wide
  lock (the example uses Python's `fcntl.flock`, which works on macOS and Linux, and stops the
  script when the lock cannot be taken). CPU admission is decided once, when a lane picks a job
  up — it is not a section lock — and there is no memory-based admission (decision 42). No
  configuration key changed. ([#106](https://github.com/monocsp/remote_ci_monitor/pull/106))
- **A finished job keeps its step times.** While a job ran, the queue showed how long each
  `::rcm::step::` took; once it finished those numbers were gone, and a team measuring its gate
  had to read the log. `GET /jobs/<id>` for a finished job now carries `step_timeline` — one entry
  per step with `started_at`, `ended_at`, `seconds` and `ok`, recomputed from the markers the
  server already stored, with the same `timing: "as_received"` caveat as live progress. `rcm run`
  and `rcm wait` print that document, so their JSON carries the key too — this is now part of the
  contract. A job that printed no step markers has `steps: []`; when the markers could not be
  read the key is `null` with `step_timeline_error_code`, never an empty list. The stored
  `failed_step` and `last_step` are unchanged, and `failures[].step` now recognises every step of
  the timeline, not only those two. The finished document also carries `concurrent_at_start`
  (how many jobs were running when it started, itself included — a job that ran alone reads
  `1`; `null` when unknown), the raw material for the two-lane experiment. `schema_version`
  stays 1 — keys were added.
  ([#108](https://github.com/monocsp/remote_ci_monitor/pull/108))
- **Presets can require tools.** `requires = ["fvm", "gitleaks"]` on a preset names the tools
  (or absolute paths) the job must find; right before the process starts, on the local lane or on
  a remote worker, rcm looks them up in the environment the job actually gets — `env_passthrough`
  then `[presets.env]`, an empty `PATH` if the job has none — and a job that would have silently
  fallen through to the wrong SDK now does not run: it ends `failed` with
  `summary_code: "tool_missing"` and `summary_args: {"tool": "fvm"}`, the name only, no `PATH` and
  no path anywhere — an entry declared as an absolute path is named by its basename in the job
  log too, and an entry without one (`/opt/bin/`) is a config error. A relative `PATH` entry is
  resolved against the job's workspace, where the process starts, not against the server's own
  directory. The job log says `[rcm] required tools: fvm ok · gitleaks ok` or
  `[rcm] required tool fvm: missing`. Such a failure carries no `failed_step`, no `last_step` and no
  failure ledger line, and it is left out of the failure window like a cancelled job — three real
  failures after one `tool_missing` read "every one of the last 3", not "3 of the last 4". A job
  cancelled while its workspace was being prepared ends `cancelled`, never `tool_missing`. Remote
  workers get `requires` in the claim and report the structured code on finish (a path in the
  reported name is cut to the name); a worker build that does not know the keys ignores them.
  `rcm check --config` gains a `local preset tools` row — a
  check in your shell, explicitly not the service's, whose `launchd` `PATH` is the usual reason a
  tool goes missing (`[presets.env] PATH = …` is the fix).
  ([Configuration](docs/configuration.md#required-tools),
  [#109](https://github.com/monocsp/remote_ci_monitor/pull/109))

## [0.2.6] - 2026-09-10

### Changed
- **A failed job's workspace no longer waits thirty days.** The log and the workspace used to share
  one date, but a failed job leaves a 50 KB log and a 720 MB workspace — fifty of those a day needs
  750 GB to reach a thirty-day limit, so the disk filled long before the limit ever applied. The
  workspace, and the snapshot it was unpacked from, now keep their own clock
  (`workspace_retention_days`, default **1 day**) while the log keeps the days it always had. Two
  byte rules catch the bulk before any date does: `workspace_storage_max_bytes` (100 GiB) and
  `min_free_bytes` (10 GiB). **This changes what an upgrade deletes on its first sweep** — see what
  it would take with `rcm gc --dry-run --config ~/.config/rcm/server.toml`, which needs no running
  server, and set `workspace_retention_days = 30` to keep the old behaviour. Evidence is never given
  up to make room, and a size that cannot be measured skips the byte rules for that sweep rather
  than guessing. ([Configuration](docs/configuration.md#retention-what-is-kept-and-for-how-long))
- **`rcm gc --dry-run` says what deleting would actually give back.** A workspace cloned from the
  git mirror shares its pack files with it by hard link; those bytes are charged to the workspace
  but deleting it does not free them — on one real machine that was a third of all workspace
  bytes, and `would free` overstated by as much. `would free` is now the reclaimable estimate, with
  the charged total and the shared part beside it
  (`would free 1.9 GB from 2 jobs (2.5 GB charged · 0.6 GB shared by hard links)`); the free-space
  floor and its no-progress check plan with the same estimate, while the byte budget still counts
  every link. `server.job_storage` gains `shared_bytes` and `estimated_reclaimable_bytes`
  (`schema_version` unchanged — keys were only added).
  ([#88](https://github.com/monocsp/remote_ci_monitor/pull/88))
- **The storage line says how old its number is.** `rcm check` prints
  `rcm data 30.9 GB · measured 57m ago` and the web host card `rcm 데이터 30.9 GB · 57m 전 측정`.
  A finished workspace is measured once and remembered for up to a day, and the age is that of the
  oldest measurement in the total — a cached figure is never shown as fresh. `server.job_storage`
  also carries `inventory_checked_at`, when the directories were last listed.
  ([#88](https://github.com/monocsp/remote_ci_monitor/pull/88))

### Added
- **A verified wrapper for gates that cannot be changed to print markers.** The server still records
  only what a script declares with `::rcm::fail::<name>` and `::rcm::step-end::fail` — it never
  parses output, and a failure that prints no markers has no name in the ledger, by design.
  `examples/preset/name-failures.sh` runs one command, passes its output through, and afterwards
  prints a marker for every line that matched the exact format the script already prints, keeping
  the command's exit code; `tests/test_examples.py` feeds its output to the server's own marker
  reader. The primary way — print the marker from the function that decides something is red — is
  shown next to it in [Naming what failed](docs/configuration.md#naming-what-failed).
  ([#92](https://github.com/monocsp/remote_ci_monitor/pull/92))
- **The server hands out its own client.** `GET /client/remote_ci_monitor-<version>-py3-none-any.whl`
  is the wheel of the code the server is running, assembled at start from the installed package —
  so `pip install http://<build-machine>:8787/client/remote_ci_monitor-<version>-py3-none-any.whl`
  brings a session machine to the server's version whether that version came from a release, a
  `dev` checkout or an offline network. `/api/health` names it (`client_wheel.path`, `.sha256`,
  `.bytes`) and says the oldest client it still accepts (`min_client_version`); `rcm check` gets a
  `client` row (`same as server` · `older — pip install …` · `newer`), red only below that floor,
  and `rcm run` prints one warning line when the client is that old. A wheel that could not be
  assembled is `client_wheel: null` plus `client_wheel_error`, and the URL answers 503 — never an
  old or empty file. [Keeping clients on the server's version](docs/operating.md#keeping-clients-on-the-servers-version)
  has a tested wrapper (`examples/session/update-client.sh`) that verifies the sha256 before it
  installs. ([#90](https://github.com/monocsp/remote_ci_monitor/pull/90))
- **A preset can collect its files only when the job fails** (`artifacts_on = "failure"`; the
  default `"always"` is unchanged). This is what makes the recommended way of leaving evidence
  affordable: put the heavy step's output in the workspace and declare it, print the verdict to
  stdout so it lands in the log, and stop paying for a bundle on every green run. Cancelled and
  timed-out jobs count as failures; a `lost` job is never collected.
  [Making a failure explain itself](docs/configuration.md#making-a-failure-explain-itself) shows
  the gate script side by side with the one that loses its evidence to `TMPDIR`.
- **`rcm gc` reclaims workspace storage now, and `--dry-run` shows what would go.** With an admin
  token it runs the same plan the sweeper does, and reports what it planned, what it deleted and
  what failed separately — a plan is not a receipt. `rcm gc --dry-run --config server.toml` runs
  **without a server at all** — it reads the config and the data directory and plans on a
  temporary copy of the database, so you can see what a new release would remove before you
  restart into it. A run that outruns `--timeout` (600 s) exits 3 (unknown), never failure: the
  server may still be deleting.
- **The screen says what the data directory holds and when the next sweep is.** `/api/status`
  carries `server.job_storage`, `/api/health` carries `storage`, `rcm check` prints one `storage`
  line, and the web host card shows it under the disk meter in both languages. What cannot be
  measured reads `—`, never `0`. (`schema_version` is unchanged — keys were only added.)
- **A job can now name what failed, and rcm remembers.** A preset script prints
  `::rcm::fail::<name>` for anything that broke — a step, a test file, a check — and rcm keeps
  those names per job. When a job fails, `GET /jobs/<id>` and `rcm run`/`rcm wait` report how
  often each name was red in the recent runs of the same key:
  `failed: flaky_test.dart — 2 of the last 8 gate runs · intermittent?`, or
  `first time in the last 8 gate runs` for something new, or `every one of the last 8 gate runs`
  for something simply broken. The question mark is deliberate — the counts are a suggestion, not
  a verdict, and runs that failed while naming nothing stay in the denominator and are reported
  (`note: 2 of those 8 runs failed without naming anything`) rather than quietly improving the
  odds. The window is the last `failure_window_jobs` (20) finished jobs of that key **up to and
  including this one**, so the answer does not drift as newer jobs arrive, and nothing is judged
  below `failure_min_jobs` (3). This history is on the single-job route only; `/api/status` is
  unchanged.
- **Upgrading also cleans up the labels the old inference left behind.** Cancelled and lost jobs
  lose the step label they should never have had, and a failed job written by an older build
  keeps its label but under `last_step` — the field that does not claim a cause — because
  after the fact there is no way to tell an inferred label from a declared one.
- **Every failed, cancelled or unknown wait now says where the log is.** `rcm run` and `rcm wait`
  end with `log: rcm logs 162 · <url>` — including exit 3, where you know least. A 404 from the
  server now carries a `hint`: `/api/jobs/162` answers with `job #162 is GET /jobs/162 · its log
  is GET /jobs/162/log with that job's token (try: rcm logs 162)`.
- **`rcm jobs` and the recent list say which code ran.** Each row carries `<ref|branch> @<short
  sha>`, and `rcm jobs --ref REF` keeps only the jobs whose ref or branch contains `REF` — useful
  when several sessions on one machine share a token, which made `--mine` mean "this machine".
  Tree jobs now send their branch name; it is display only and never changes `tree_hash`, so two
  sessions on different branches with the same tree still join the same job.
- **A progress bar on every running job.** The web page draws one bar under each running row and
  always says what it measured: `50% · 4/8 steps` when the job declares its step count with
  `::rcm::steps::N`, `70% · by measured time` or `by preset estimate` when it does not — so the
  number carries the worth of the estimate behind it. A job past its estimate reads
  `past the estimate`, one that finished every declared step without exiting reads `finalizing`,
  and a job nothing can be said about — likely stuck, preparing its workspace, or with no samples
  at all (the 600-second installation default is not a measurement) — reads `progress —`. **A
  running job never fills the bar**: a full bar means finished, so a forecast stops at 99% and the
  two "we are past what we know" states are hatched instead. The time bar grows every second
  instead of jumping between refreshes, and stops growing while updates are paused or lost.
  ([#70](https://github.com/monocsp/remote_ci_monitor/pull/70),
  [#73](https://github.com/monocsp/remote_ci_monitor/pull/73))
- **Parallel lanes you can actually turn on.** `[server] lanes = 2` was always there, but nothing
  stopped two heavy jobs from bringing the machine to its knees. Lane 2 and above now only pick up
  a job while the host CPU is below `[server] cpu_max_percent` (80), measured over
  `admission_samples` (3) consecutive host samples, with `admission_cooldown_seconds` (30) between
  admissions on one machine. **Lane 1 is never held**, so the queue keeps moving under any load,
  and an unknown or stale CPU reading closes a lane rather than opening it. A held lane shows in
  `rcm top` (`lanes 1/2 busy · 1 held (cpu 92%)`), on the web page, and as the queue reason
  `held_by_load`; `rcm check` reports it without failing, and warns if a lane has been held for
  more than five minutes. `admission = "always"` restores the old behaviour.
  ([#66](https://github.com/monocsp/remote_ci_monitor/pull/66),
  [#67](https://github.com/monocsp/remote_ci_monitor/pull/67))

### Changed
- **Breaking-ish: `failed_step` is now only ever a step your script declared as failed.** It used
  to be inferred — on a non-zero exit the *last started* step got the label — and that inference
  named the wrong step for any script that runs things in parallel and replays their logs
  afterwards: a real gate reported `build web` (which passed) while `test` was what broke, and a
  **cancelled** job carried a step label at all, which read as "this is what went wrong" next to
  its summary. Declare a failure with `::rcm::step-end::fail` or `::rcm::fail::<step name>` and
  nothing changes. Without a declaration `failed_step` is now `null`, and a new `last_step` field
  says where the job was when it ended, with no claim about the cause — displays read
  `exit 1 (last step build web)` instead of `(step build web)`. Cancelled and lost jobs carry
  neither field. Notification hooks get `RCM_LAST_STEP` alongside `RCM_FAILED_STEP`. Keys were
  added, not removed, so `schema_version` stays 1.
- **`rcm run --no-wait` now answers "where am I in the queue?".** It used to print a job id, a URL
  and `"state": "submitted"` — a state name the server never uses — so a session that submits and
  leaves had to run `rcm eta --job N` to learn anything. The JSON now carries the job's real
  `state` plus `position`, `reason`, `ahead_job_id`, `blocked_by` and the whole `estimate`, and the
  line on stderr reads
  `submitted job #155 queued · 3rd in line · wait 4m 12s · eta 16:02 · <url>`. A job that is
  already running has no position and that piece is left out, never printed as `0th`; a session
  that joined an existing job sees that job's own position; and a queue that cannot start at all —
  paused, or a pool with no live worker — reports no finish time, because there is none to give.
  The lookup is for display only: if it fails or the server is slow, the line and the JSON come
  back without those keys and the exit code is still 0 — `--no-wait` exits 0 because the job was
  submitted, not because it was looked up.
  ([#72](https://github.com/monocsp/remote_ci_monitor/pull/72))
- **An ETA says it is less sure while a job shares the machine.** Medians are measured from runs
  that mostly had the machine to themselves, so a job running beside another finishes later than
  the median suggests. The confidence badge now drops one step for as long as that is true — the
  estimate itself is not inflated by a guessed factor. `estimate.shared` carries the fact so the
  page and `rcm top` agree. Jobs also record how many were running when they started, so a future
  release can measure the real effect instead of guessing at it. Database schema 10.
  ([#74](https://github.com/monocsp/remote_ci_monitor/pull/74))
- **Queue rows now arrive folded.** Running rows used to open themselves, so two or three running
  jobs filled the screen with step lists and log tails. The row keeps what answers "how is it
  going" — the progress bar, `step 2/4 build 2s` in the reason column, and **Cancel** for your own
  job — and **▸** opens the step list and the log tail. The page remembers which rows *you* opened
  (`rcm.expanded` in that browser), not which ones you closed.
  ([#70](https://github.com/monocsp/remote_ci_monitor/pull/70),
  [#73](https://github.com/monocsp/remote_ci_monitor/pull/73))
- **Recent results show the job number.** `#412` is how you ask for a log, an artifact or a rerun,
  and it was the one place the page dropped it.
  ([#70](https://github.com/monocsp/remote_ci_monitor/pull/70))
- **`/api/status` gains three keys on `server.workers[]`** — `hold_code`, `hold_detail` and
  `held_since`, all `null` unless the load gate is holding that lane. `state` gains the value
  `held`. `schema_version` is unchanged.
  ([#66](https://github.com/monocsp/remote_ci_monitor/pull/66))
- **Behaviour change for anyone already running two or more lanes**, whether that is
  `[server] lanes` or a worker's own `--lanes`: those lanes now wait for the machine to be quiet.
  The server logs the effective policy at startup. Set `admission = "always"` to keep the old
  behaviour. ([#66](https://github.com/monocsp/remote_ci_monitor/pull/66))
- **The queue's lane accounting counts `(worker, lane)` instead of the lane number.** A pool with
  local lanes and a remote worker's lanes used to collapse them — a four-lane pool was estimated as
  two, and "behind #N" could name the wrong job.
  ([#59](https://github.com/monocsp/remote_ci_monitor/pull/59))

### Fixed
- **`rcm serve` and `rcm token` refuse in one line when the database cannot be opened** — a
  migration backup that could not be written, or a database a newer build has migrated —
  instead of a Python traceback with the sentence at the bottom. The sentence is the recovery
  path ([going back to the old build](docs/operating.md#going-back-to-the-old-build)), and the
  exit code is 2.
- **The offline `rcm gc --dry-run --config` migrated the live database.** A command documented as
  read-only opened the database the way the server does, which upgrades its schema on the spot —
  so running the preview from a newer build, as the upgrade procedure said to, left the *running*
  older build with a database it did not understand: failed jobs lost their step labels, and the
  service could not have been restarted. It now opens the live database read-only, copies it with
  SQLite's online backup into a private temporary directory, migrates and plans on the copy, and
  deletes the copy; if any step is incomplete it exits 3 (unknown) instead of printing an empty
  plan, and a data directory with no database is unknown too rather than a database being created
  there. ([#87](https://github.com/monocsp/remote_ci_monitor/pull/87))
- **Every schema migration now starts with a verified backup of the old database**,
  `<data_dir>/backup/rcm.sqlite3.v<old>.bak` (three kept). If the backup cannot be written the
  migration does not start and the server says so. An older build that meets a newer database
  refuses to start as before, and the message now names that backup and the restore steps;
  [Operating](docs/operating.md#going-back-to-the-old-build) says what a database-only downgrade
  loses. ([#87](https://github.com/monocsp/remote_ci_monitor/pull/87))
- **Step labels an older build wrote after such an upgrade are corrected once on the next start**
  (schema v16): a failed job's label that has no entry in the failure ledger is moved to
  `last_step`, and cancelled or lost jobs lose theirs — those were the old build's guesses, not
  declarations. ([#87](https://github.com/monocsp/remote_ci_monitor/pull/87))
- **`rcm jobs` showed the commit twice for a job started with `--ref <full sha>`** — the ref and
  the sha are the same forty characters, and the row read `092dc5854301a87eab47c0… @092dc58`. When
  the ref *is* the sha it now shows once (`@092dc58`; `dolomood @092dc58` in the queue). A ref that
  merely starts like the sha — a `092dc58` branch or tag — still shows both.
  ([#92](https://github.com/monocsp/remote_ci_monitor/pull/92)) The `submitted job #1 (gate · …)`
  line `rcm run` prints on submit follows the same rule: `(gate · @092dc58)` for a full-sha ref,
  `(gate · main @092dc58)` for a branch.
  ([#100](https://github.com/monocsp/remote_ci_monitor/pull/100))
- **`rcm wait --job 999` on a job that does not exist ended with `log: rcm logs 999`** — a hint
  pointing at nothing. The line is now left out only when the server answered a definite 404; a
  connection that was lost, or a `--timeout` that ran out first, still exits 3 (unknown) and still
  says where the log is, because the job may well exist.
  ([#92](https://github.com/monocsp/remote_ci_monitor/pull/92))
- **`rcm check` labelled the local config's data directory `data dir`, next to the `server` row,**
  as if it were the server's. The server never reports its `data_dir`, so the row is now
  `local data dir` and says which file it came from (`… · from ~/.config/rcm/server.toml`).
  ([#92](https://github.com/monocsp/remote_ci_monitor/pull/92))
- **`rcm gc --dry-run` on a freshly started server said `0 B would remain`.** The summary used the
  server's previous measurement, taken before the plan ran; it now uses the snapshot the plan
  itself measured. A real `rcm gc` also measures again after deleting: `storage_after` and the new
  `free_bytes_before` / `free_bytes_after` are what the disk said afterwards, and the receipt reads
  `freed 2.5 GB from 2 jobs · est. 1.9 GB reclaimable · free 600.0 GB → 601.9 GB · 28.9 GB left`.
  `freed_bytes` keeps its meaning — the planned size of what was deleted, now also as
  `deleted_charged_bytes` — so nothing that read it changes.
  ([#88](https://github.com/monocsp/remote_ci_monitor/pull/88))
- **The no-progress check could miss.** It only armed when the floor rule itself had picked
  something, so a sweep whose age or budget picks already covered the shortfall could delete under
  the floor, gain nothing, and never pause. It now judges every sweep that started under the floor,
  against what was actually deleted rather than what was planned, and not at all when nothing was.
  ([#88](https://github.com/monocsp/remote_ci_monitor/pull/88))
- **A workspace that could not be measured now says why.** `error_code` and the server log carry
  `measure_EACCES` (or whichever error it was) instead of a bare unknown.
  ([#88](https://github.com/monocsp/remote_ci_monitor/pull/88))
- **The web page came up broken on any server with a disk sample** — since the unreleased data
  directory line under the disk meter, the host card referred to a name that did not exist, the
  render stopped there on every refresh, the recent list stayed empty, and after thirty seconds
  the page reported a lost connection while the server was fine. The browser test now renders the
  shape a real deployment sends (a disk sample and `server.job_storage`), checks the line in both
  languages, and fails on any page error; on the Linux CI runner a missing Chrome is a failure,
  not a skip. ([#82](https://github.com/monocsp/remote_ci_monitor/pull/82))
- **The disk meter and the data-directory line were missing when `data_dir` was written with
  `~`** — which is what the example `server.toml` does (`~/.local/share/rcm`). The server handed
  the sampler the unexpanded string, the usage call failed on it, and the host sample carried
  `disk: null`, so neither the web page nor `rcm top` drew the disk. A path written in full
  was never affected. ([#84](https://github.com/monocsp/remote_ci_monitor/pull/84))
- **A remote worker collected no artifacts at all.** The server never put the frozen artifact
  policy in its `/worker/claim` reply, so the worker found no globs and skipped collection
  entirely: a preset with `artifacts` running in a remote pool produced nothing, while the job
  succeeded and nothing on the screen said otherwise. The tests missed it because they built the
  claim payload by hand instead of taking the server's. Local pools were never affected.
- **`server.artifact_storage.last_sweep_at` was always `null`.** It looked for an attribute the
  server does not have, so the time of the last retention sweep never reached `/api/status`.
- **A failed step that rcm only guessed at now says so.** When a job exits non-zero and its script
  never printed `::rcm::step-end::fail`, rcm still names the last step the job reached — but that
  name is a guess, and it was being reported as a fact. A gate that runs its steps in parallel and
  prints the markers afterwards in a fixed order always ends on the same step, so across two days
  of one repository's gate — 145 jobs, 55 of them failing — 16 were blamed on `build web`, which
  the log shows had *passed*; the real failure was `test`. Nothing can recover the true step from
  the markers alone, so rcm no longer pretends: `/api/status` carries `failed_step_guessed`, the
  recent results — on the web page and in `rcm top` — write `(guessed)` next to the step, and
  notification hooks get `RCM_FAILED_STEP_GUESSED`. A running job never carries a guess, because
  it has no exit code yet. A step confirmed by `::rcm::step-end::fail` looks exactly as it did
  before — print that marker and the blame is a fact. A job that was cancelled, timed out or lost
  reports its step as a guess whatever the markers say: it ended because it was killed, not
  because that step failed. Jobs that finished before this release report `null`: unknown, which
  is neither. Database schema v11 (one added column; `/api/status` `schema_version` is unchanged —
  keys were only added).
- **The server log now says what a 500 actually was**, not just the exception class. During the
  2026-09-08 outage it recorded `OperationalError` 314 times, which does not distinguish "database
  is locked" from "unable to open database file" — two different problems with two different
  fixes. The message is now appended to the log line, with paths redacted. `server.last_error` in
  `/api/status` is unchanged and still carries only the class: reads are unauthenticated by
  default, so the detail belongs in the log, which only whoever runs the server can read.
- **The example launchd service now raises the file-descriptor limit**, as the systemd unit
  already did. A launchd session defaults to `maxfiles 256`, and the server holds descriptors per
  request thread, per open event stream and per SQLite connection (the database, its `-wal` and its
  `-shm`). On 2026-09-08 a build machine ran out: `sqlite3` could no longer open the database, so
  every request answered with a database error and notification hooks died with `Too many open
  files`, and it stayed that way for twelve minutes until the service was restarted — the queue
  looked alive and answered nothing. The leak behind that particular outage was fixed in 0.2.2
  (request threads close their connection); this is the headroom that keeps the next one from being
  fatal. Both service files now say 4096, and [operating the build
  machine](docs/operating.md#run-as-a-service) says why. Anyone who wrote their own service file
  should set it too.
- **A waiting `rcm run` no longer holds the snapshot bookkeeping for the whole build.** The file
  list and per-file hashes were kept alive until the job finished, long after the tarball was
  uploaded and deleted: a 20,000-file tree sat at 64 MB for the length of the run instead of the
  a waiting client actually needs. On a machine short on memory, `rcm run --no-wait` plus
  `rcm wait --job N` is now documented as the pattern — if the operating system kills the waiting
  client, the shell sees 137, which is not the job failing.
- **`/api/status` barely notices how many jobs you have kept.** A status poll on a server holding
  50,000 finished jobs took 1.4 seconds and now takes about a millisecond. Two things cost that
  time: the median behind every ETA was rebuilt from 45 days of finished jobs on **every** request
  as full job objects (two extra queries and two JSON parses per row, for six fields it reads),
  and picking the eight most recent jobs sorted every finished job in the database. Samples are
  now read as plain rows and only re-read when a job actually finishes; recent jobs use an index.
  Database schema 9. ([#69](https://github.com/monocsp/remote_ci_monitor/pull/69))
- **Remote worker lanes are read in one query instead of one per worker**, which also cuts the
  work behind every server event — 552 statements down to 3 on a fleet of fifty workers.
  ([#69](https://github.com/monocsp/remote_ci_monitor/pull/69))
- **Retired workers are forgotten after a week.** Nothing ever deleted them, so every worker that
  ever registered kept adding `down` lanes to every status document. A worker with a job still
  running is never forgotten. ([#69](https://github.com/monocsp/remote_ci_monitor/pull/69))
- **A worker's log flush no longer stalls other lanes.** Every step marker in a batch opened its
  own SQLite transaction, so one 256 KB flush pushed another lane's claim from 0.03 ms to 275 ms,
  and a large body could exhaust the busy timeout and hand the worker an HTTP 500. Markers are now
  written in one transaction, and a busy database answers `503` with `Retry-After` instead of a
  server error. ([#59](https://github.com/monocsp/remote_ci_monitor/pull/59))
- **Claiming a job no longer scans every job the pool has ever had.** A dedicated index makes it
  constant-time (8.5 ms → 0.003 ms at 200k retained jobs). Database schema 8.
  ([#59](https://github.com/monocsp/remote_ci_monitor/pull/59))
- **Workers no longer poll in lockstep.** Their retry interval was a fixed one second, so a fleet
  that restarted together stayed synchronised and could fill the server's request slots. It is now
  spread over one to two seconds. ([#59](https://github.com/monocsp/remote_ci_monitor/pull/59))
- **The production guard stopped objecting to a worktree that has its own `rcm.toml`.** It read a
  bare `rcm serve` as "this would take the production config", but the search order stops at
  `./rcm.toml` (and `$RCM_CONFIG`) long before `~/.config/rcm/server.toml`, so a test server in a
  worktree was refused for no reason. Naming the production config or data directory explicitly is
  still refused. [Operating the build machine](docs/operating.md#from-a-git-checkout) now spells
  out the search order and what a test server's config should set.

## [0.2.5] - 2026-09-09

A job can send its files back, the queue page answers "did mine finish?" before anything else, and
the build machine can run from a git checkout.

### Added
- **A job can send its files back** (M5e). A preset that declares `artifacts = ["test/**/goldens/*.png"]`
  has those files collected before its workspace is deleted, and `rcm run goldens --fetch-artifacts`
  writes them into the same paths in the tree you submitted — the reason this exists is
  `flutter test --update-goldens`, whose PNGs previously had no way home. A file you edited while
  waiting is never overwritten: it is counted `conflicted` and left alone unless you pass `--force`.
  The result line separates what was compared from what was written (`wrote 12, unchanged 51,
  conflicted 1`). `rcm artifacts N` shows what is there and `rcm artifacts N --fetch --output DIR`
  fetches it later. Failed jobs deliver their files too — a golden diff is worth more than a passing
  one. ([#56](https://github.com/monocsp/remote_ci_monitor/pull/56) · [#57](https://github.com/monocsp/remote_ci_monitor/pull/57) · [#58](https://github.com/monocsp/remote_ci_monitor/pull/58))
- **Artifacts disappear when they are no longer needed** (M5e). Once your session has written every
  file it acknowledges the bundle; if nobody joined that job the server deletes it immediately, and
  otherwise it stays until `artifact_retention_hours` (24) so the others can fetch it too. That
  clock is the bundle's own: `retention_days_success = 0` still leaves it a full day. Deletion is
  reported only after the files are actually gone, and a download already in flight finishes even
  if the bundle expires mid-transfer. New keys: `artifact_retention_hours`, `max_artifact_bytes`,
  `max_artifact_files`, `artifact_storage_max_bytes`, `artifact_timeout_seconds`,
  `artifact_cancel_timeout_seconds`, `artifact_transfer_timeout_seconds`,
  `max_concurrent_artifact_transfers`. Over any limit the job still succeeds or fails on its own
  merits and only the artifacts are dropped, with the reason on the job.
  ([#56](https://github.com/monocsp/remote_ci_monitor/pull/56) · [#57](https://github.com/monocsp/remote_ci_monitor/pull/57))
- **The queue page and `rcm top` show the artifacts** (M5e). A finished job's row carries one line —
  state, file count, size, how long it has left — plus the command that fetches it, copyable. An
  unknown count prints `—`; `0` appears only when the collection really found nothing. File names
  never reach the public status document, so they are not on that line either.
  ([#57](https://github.com/monocsp/remote_ci_monitor/pull/57) · [#60](https://github.com/monocsp/remote_ci_monitor/pull/60))
- **Motion says what is still moving** (M5d-3). A running, queued, uploading or cancelling job now
  pulses; a finished one is still. That is a second channel beside colour, so a glance at the
  corner of the screen separates "still going" from "done" — and every animation sits behind
  `prefers-reduced-motion: no-preference`, so a viewer who turns motion off gets none of it. A
  test proves both halves: zero animations under `reduce`, and at least one without it (a control
  that would otherwise pass on a page that simply has no motion).

### Changed
- **The answer you came for is the biggest thing on the page** (M5d-3). "Your jobs" — did it
  finish, did it pass — is the question this screen gets most often, and it was set at 13px, one
  step *smaller* than body text. It is 20px now, with the other two summary cells at body size, so
  size carries the priority instead of weight alone.
- **Running the build machine from a git checkout is written down.** The service's virtual
  environment can point at a working copy (`pip install -e`) instead of a released wheel, which
  turns an upgrade into `git pull --ff-only` plus a restart. [Operating the build
  machine](docs/operating.md#from-a-git-checkout) now says what keeps that safe: the checkout stays
  on `main` and nothing is edited in it, development happens in a worktree with its own virtual
  environment, and a test server gets its own config, `port` and `data_dir`. The upgrade section
  also shows how to copy the database first — `sqlite3 ".backup"`, not `cp`, which misses the
  write-ahead log. For sessions using Claude Code the rule is enforced rather than trusted: a
  `PreToolUse` hook (`tools/guard_production.py`, wired in `.claude/settings.json`) finds the
  production checkout from the machine's own editable install, refuses edits to it and to the
  server's config and data, and asks before a deploy. A machine with no such install sees nothing.
  ([#61](https://github.com/monocsp/remote_ci_monitor/pull/61))
- **The page reads like a page, not a terminal** (M5d-2). Sentences, labels, buttons and state
  words are set in the system sans face; the monospace face is now kept for what it is for —
  job ids, keys, commit shas, refs, repository URLs, step names, logs and the `rcm run …` command.
  Numbers line up in columns (`tabular-nums`), section headings carry weight instead of
  `text-transform: uppercase` (which gave Korean no hierarchy at all and mangled the Latin mixed
  into it — `5s ago` became `5S AGO`), and the monospace stack finally names a Korean fallback.
- **The host section is folded** (M5d-2). It is the fourth question the page answers, not the
  first, and it was taking a third of the screen. A one-line summary beside the heading says which
  machines are reporting and how they are doing; the section opens itself when a machine is busy or
  a sample has gone stale, and if you open or close it yourself that choice is kept.
- **The queue stopped repeating itself** (M5d-2). The Source column shows the commit alone — the
  repository URL, identical on every row, moved into the expanded block — and the columns have
  fixed widths so the reason takes the slack instead of leaving a hole in the middle of the table.
  In Host pressure, the free space now reads as part of the disk figure (`Disk 30% (699 GB free)`)
  instead of trailing off the end of the line where it looked like it belonged to the GPU.

### Fixed
- **The phone layout had never actually been tested on a phone** (M5d-3). The mobile test opened
  Chrome at `390,844`, but a macOS Chrome window will not go below 500px, so the viewport was 500
  and the assertion (`<= 720`) passed anyway. It now overrides the viewport through CDP and pins
  it at exactly 390 — and checks that the key and requester cells still have both text *and* size,
  which is what a `max-width: 0` regression breaks while leaving the text in place. The phone
  screenshot in the guides is a real 390px too.
- **Four state pills failed contrast in the light theme** (M5d-3). Measured against their own
  backgrounds, `succeeded` was 3.79:1, `cancelling` 3.72:1, `queued`/`cancelled` 3.83:1 and
  `failed` 4.32:1, where 12px semibold text needs 4.5:1. The hues are unchanged; only their
  lightness moved. Dark already passed everywhere.
- **`queued` and `cancelled` had no shape of their own** (M5d-3). `queued` drew the same faint `·`
  used for "unknown", and `cancelled` drew the same filled square as `cancelling` — so those pairs
  were told apart by colour alone. They are now a hollow circle and a hollow square: filled means
  moving, hollow means stopped. (`rcm top` keeps its own glyphs; a terminal has no colour to fail
  back to.)

## [0.2.4] - 2026-09-08

The page speaks Korean, the host card says how much disk is left, and a running step's seconds
count up instead of standing still and then jumping.

### Added
- **How much disk is left, next to CPU and memory** (M5d-2). A host sample now carries `disk`
  (`used_bytes`, `free_bytes`, `total_bytes`, `path`) for the filesystem the jobs actually write
  to — the server's data directory, or a remote worker's own — not the root partition. The host
  card gains a meter reading `Disk 120 GB / 460 GB` with the remaining amount spelled out beside
  it, and Host pressure counts it. Two thresholds decide "full", because one gets it wrong: 85%
  used, **or** under 10 GiB free. A 4 TB disk at 90% is fine; a 60 GB disk at 80% cannot unpack a
  snapshot. API `schema_version` stays 1 — the key is added. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))
- **Step timers count up instead of jumping** (M5d-2). A running step used to show whatever second
  the server put in the last status document and then leap ten or twenty seconds when the next one
  arrived. Progress now ships `job_started_at` and per-step `started_at`/`ended_at`, and the page
  counts from those anchors once a second, so `analyze 9s` becomes `10s`, not `26s`. Finished jobs
  keep the server's fixed number, and a browser whose clock offset is unknown keeps showing the
  server's value rather than quietly counting on its own clock. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))
- **The queue says what is running** (M5d-2). Rows are split under two headings, "Running now" and
  "Waiting", each with a count, and an empty group says so instead of vanishing. A running row now
  carries what it is doing right now — `step 2/4 analyze 12s` — without being expanded. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))

- **The web page speaks Korean, and you can switch it** (M5d-1). It opens in Korean regardless of
  the browser's language and a button at the top right flips it to English; the choice is kept in
  that browser and follows into other tabs. Everything the page writes comes from one catalogue
  (`web/i18n.js`), including the server's outcome codes from M5d-0, so a job the server closed
  reads in the viewer's language while a summary the *job* printed stays exactly as the team wrote
  it. Identifiers never move: preset names, job keys, requester labels, commit shas, refs,
  repository URLs, step names, logs and the `rcm run …` command are the same in both languages.
- **The server says what happened in codes, not sentences** (M5d-0). Every summary the *server*
  writes now carries `summary_code` and `summary_args` beside the English `summary`, so a client
  can render the same fact in its own language: `cancelled_before_start`, `server_restarted`,
  `upload_abandoned`, `snapshot_too_big`, `worker_unreachable` and thirteen more, collected in one
  table (`core/outcome.py`). Arguments are raw values (bytes, seconds, exit codes, names) — the
  side that shows them decides how to format. A summary the *job* printed with `::rcm::summary::`
  has no code; it is the team's own sentence and travels unchanged. The four `*_error` fields, the
  server's `last_error` and a host's `gpu_note` gained `*_code` companions the same way. Database
  schema 5 → 6 (migrates on start); API `schema_version` stays 1 — keys are only added.

### Fixed
- **A remote worker's host sample was being thrown away whole** (regression from M5d-0). M5d-0 added
  `gpu_note_code` to the host document, but the heartbeat parser rejected any sample containing a
  key it did not know, so an up-to-date worker's CPU, memory and GPU silently disappeared from its
  pool's host card. Unknown top-level keys are now ignored and the sample is kept; a sample with no
  recognised key at all is still rejected, and every value is still validated as strictly as before.
  This is what lets a newer worker talk to an older server without going blank. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))

### Changed
- Documentation: say plainly that **discovery resolves the address, not who you are**. A token is
  only ever created on the build machine (`rcm token add` writes to the server's database; there
  is no API that hands one out), so a session that has just found the server and shows a green
  `server` row next to a red `token` row is in the expected state, with one step left. Leaving
  `server` out of `client.toml` is also now stated to need 0.2.2+ on both machines, since an older
  client has no discovery and does not compare versions the way `rcm worker` does. And `mode 600`
  on a file holding a token is documented as refused, not merely advised.
  ([#44](https://github.com/monocsp/remote_ci_monitor/pull/44))

## [0.2.3] - 2026-09-08

The documentation rewrite, and discovery failures that explain themselves.

### Changed
- **The documentation was split up.** `README.md` is now a front door (what it is, install, a first
  job, where to go next) and the depth moved into `docs/`: a step-by-step
  [usage guide](docs/usage.md) with annotated screenshots in both languages,
  [configuration](docs/configuration.md), [operating the build machine](docs/operating.md), and
  `CONTRIBUTING.md`. `README.ko.md` mirrors the English README section for section.
  ([#41](https://github.com/monocsp/remote_ci_monitor/pull/41))
- The screenshots are generated by `tools/screenshots/` (headless Chrome capture, then the numbered
  boxes), so they can be regenerated when the UI changes. What each document owns and what a pull
  request has to update is written down in `docs/documentation.md` and the `docs` skill.
  ([#41](https://github.com/monocsp/remote_ci_monitor/pull/41))

### Fixed
- Discovery failures now say **why**. A responder that cannot send logs the errno by name
  (`mdns: send failed: EHOSTUNREACH`) instead of `OSError`, once per cause rather than per packet,
  and on macOS the line names the Local Network permission that a launchd service is denied by
  default. Two consecutive failures set `/api/health.advertise.error`, so a server that advertises
  but cannot be reached no longer reports itself as advertising; `rcm check` shows it as a `warn`
  row (`server cannot be discovered: …`) without failing the check.
  ([#40](https://github.com/monocsp/remote_ci_monitor/pull/40))

## [0.2.2] - 2026-09-08

LAN auto-discovery (M5c) and the fixes from the first week of real use.

### Added
- **LAN auto-discovery** (M5c): a server bound to a non-loopback address advertises `_rcm._tcp`
  over mDNS/DNS-SD (stdlib responder, coexists with mDNSResponder/avahi on port 5353; `advertise`,
  `advertise_name`); sessions with no `server` configured (or `server = "auto"`) find it —
  `rcm discover [--json]`, `rcm check` marks `(found on this network)`, `/api/health.advertise`.
  A multi-homed server (wired + Wi-Fi) listens on every interface and answers with the address
  that routes to the querier first; Tailscale (100.64/10) addresses sort last. `advertise = true`
  with a loopback `bind` logs a warning (found, but unreachable from other machines).

### Fixed
- A notification hook that times out is now killed as a whole process group — grandchildren
  (`python`, `gh`) no longer survive as orphans.
- `rcm run`/`rcm wait` treat a transient `500` from the server like `502/503/504` (retry within
  the 60 s budget) instead of giving up with exit 3.
- `rcm run` checks the token (`/api/whoami`) before building the snapshot, so a rejected token
  fails in a second instead of after packing a large tree.
- "cannot reach" messages are no longer prefixed twice.

### Changed
- README (both languages): "Run as a service" now records three deployment lessons — the
  service `PATH` is the presets' `PATH` (never put the rcm venv first), macOS TCC blocks launchd
  services from `~/Documents`, and notification hooks must read tokens from files.

## [0.2.1] - 2026-09-08

First fixes from real use (a Flutter monorepo gate moved onto rcm) plus the Korean guide.

### Fixed
- Snapshots skip nested git checkouts (`.claude/worktrees/<x>/`, submodule-like directories)
  instead of failing with `snapshot failed: Is a directory`.
- Notification hooks now inherit `PATH`, `HOME` and `LANG` from the server (a hook calling
  `date` or `$HOME` used to exit 1).

### Added
- `README.ko.md` — Korean end-user guide with annotated screenshots (`docs/images/ko/`).
- Notification env carries the job source (`RCM_SOURCE_MODE/REF/SHA/BASE_SHA/DIRTY/REPO`) and
  `RCM_INPUTS`, so a hook can post a commit status for the tested commit.

## [0.2.0] - 2026-09-07

M5: priority, content-addressed snapshot cache, notifications, worker pools and remote workers
(`rcm worker`). Database schema 3 → 5 (migrates on start). API schema stays `schema_version 1`
(additive keys only). Upgrade: install the same release on the server and on every worker
(`rcm worker` refuses a version mismatch).

### Added
- **Priority** (M5a): `rcm run --priority low|normal|high`, preset `priority` defaults that non-admin
  sessions cannot exceed, `rcm bump N` (admin), queue order by priority then age, `queue[].priority`
  and `presets[].priority` in the status document.
- **Snapshot cache** (M5a): content-addressed upload — `POST /jobs/{id}/tree/manifest` returns the
  missing hashes and `PUT …/tree` with `X-RCM-Tree: blobs` sends only those files; blobs are
  purged by age/size but never while an active job references them; `snapshot_cache`,
  `snapshot_cache_days`, `snapshot_cache_max_bytes`, `snapshot_cache_scope`; `source.uploaded_bytes`
  and `source.cached_bytes`; `--no-cache`.
- **Notifications** (M5a): `[[notify]]` rules (argv or url) on job completion, exactly once per
  (job, rule) including after restarts; `server.notify_failures`.
- Database schema version 3 (`priority` column, `blobs` and `notifications` tables); migrates on start.
- **Pools** (M5b-1): `pools[]` in the status document now has one entry per worker pool
  (`default` first); jobs carry `pool`, presets `pool`/`pools`, `rcm run --pool`, `rcm eta --pool`,
  `rcm jobs --pool`; a pool without workers shows `worker_down` and no ETA. Schema version 4
  (`pool` column). All additive keys.
- **Remote worker protocol** (M5b-2): worker tokens (`rcm token add NAME --worker`, `kind` column in
  `rcm token list`, `/api/whoami.kind`), `POST /worker/register|claim|heartbeat`,
  `GET /worker/jobs/{id}/tree` (cache jobs are assembled into a tarball on demand),
  `POST /worker/jobs/{id}/phase|log|finish`; a worker only ever touches jobs it claimed; a
  worker silent for `worker_timeout_seconds` is `down` and its running jobs become `lost`;
  re-registering closes the old jobs as lost; unconfirmed cancels are closed by the server.
  `server.workers[]` gains `worker` and `display_name`, `pools[].lanes` counts live remote
  lanes, remote host samples appear in the pool's `hosts[]` with `source = "worker"`,
  `/api/health` reports `pools_without_workers`. Config `worker_timeout_seconds`,
  `worker_heartbeat_seconds`, `worker_claim_wait_seconds`. Schema version 5 (`tokens.kind`,
  `jobs.worker_name`, `workers` table). Server restarts no longer mark remote running jobs lost.
- **`rcm worker`** (M5b-3): a remote worker process for a pool — `rcm worker --server URL --pool
  NAME --lanes N [--config worker.toml] [--data DIR] [--check] [--once]`, token via
  `RCM_WORKER_TOKEN` or `worker.toml`; downloads snapshots (cache jobs are assembled by the
  server), fetches `git_ref` jobs from its own `[[repos]]`, streams the raw log, reports the
  outcome, heartbeats with a host sample; SIGTERM reports running jobs as `lost` (`worker
  stopped`). Job execution (`runner.py`) is shared with the local worker. `examples/worker.toml`.
- **Multi-pool display** (M5b-4): remote pool headers in `rcm top` always name the pool
  (`queue — empty (pool linux)`, `· paused`), more than five remote worker pills fold into
  `+N workers` (down workers never fold), remote worker host samples are cards in the web Host
  section (`build-02 · pool linux`), `rcm check` gains a `pools` row that fails when every worker
  of a pool is down, and `server.workers[]` entries carry `pool`.

## [0.1.0] - 2026-09-06

First release. One package for the build machine (`rcm serve`) and every session (`rcm run`).
Python 3.11+ standard library only — zero runtime dependencies. API schema: `schema_version 1`.

### Added
- **Server, queue, worker** (M0): SQLite (WAL) queue that survives restarts, worker lanes running
  registered presets only (argv arrays, inputs via `RCM_INPUT_*`), working-tree snapshot upload
  with safe `tarfile` extraction, step markers, SIGTERM → grace → SIGKILL, per-client bearer
  tokens (SHA-256 at rest), `rcm run` / `rcm wait` with exit codes 0/1/2/3 (3 = unknown, never a
  fake failure). Jobs running during a restart become `lost`; nothing disappears from the queue.
- **Visibility** (M1): host sampler (CPU · memory · GPU on Apple Silicon and NVIDIA · top
  processes · 5-minute history), median-based ETAs with confidence, join of identical
  submissions, live events over SSE with polling fallback, `rcm top` / `eta` / `jobs` / `logs` /
  `presets` / `cancel` / `pause` / `resume`.
- **Web UI** (M2): static, dependency-free queue page — Your jobs · Not moving · Host pressure,
  queue with reasons and ETA confidence, host card with sparklines, recent results, log drawer,
  cancel, token entry, mobile layout, dark/light, Lost-connection banner, `#/jobs/N` deep links.
- **Operations** (M3): `git_ref` source mode for deploy presets (`rcm run deploy --ref v1.2.3`,
  commit pinned at submit time, local mirror, detached checkout), retention cleanup of logs,
  snapshots, workspaces and old job records, `read_auth = "basic"` (username = token name,
  password = token; writes stay Bearer-only), launchd and systemd unit examples, concurrency-group
  and signal e2e coverage.
- **Packaging** (M4): `rcm init server` / `rcm init client --server URL`, `rcm version --json`,
  `rcm check` Python row (with `--config server.toml` also data-dir and git rows), install smoke script, release workflow (GitHub Release + optional
  PyPI trusted publishing), Linux server `Dockerfile`.

### Known limits
- Windows is out of scope. GPU numbers exist only on Apple Silicon (`ioreg`) and NVIDIA
  (`nvidia-smi`); other machines report `gpu: null` with a note.
- No partial-upload resume: an interrupted snapshot upload ends as `cancelled`; run `rcm run` again.
- Basic auth is clear text — use it only behind TLS (Tailscale HTTPS or a reverse proxy).

[Unreleased]: https://github.com/monocsp/remote_ci_monitor/compare/v0.3.1...HEAD
[0.3.3]: https://github.com/monocsp/remote_ci_monitor/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/monocsp/remote_ci_monitor/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/monocsp/remote_ci_monitor/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.9...v0.3.0
[0.2.9]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.8...v0.2.9
[0.2.8]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/monocsp/remote_ci_monitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/monocsp/remote_ci_monitor/releases/tag/v0.1.0
