# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.
The `/api/status` document carries its own `schema_version` — removing or changing the meaning
of a key bumps that number and is listed here.

## [Unreleased]

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
  ([#84](https://github.com/monocsp/remote_ci_monitor/pull/88))
- **The storage line says how old its number is.** `rcm check` prints
  `rcm data 30.9 GB · measured 57m ago` and the web host card `rcm 데이터 30.9 GB · 57m 전 측정`.
  A finished workspace is measured once and remembered for up to a day, and the age is that of the
  oldest measurement in the total — a cached figure is never shown as fresh. `server.job_storage`
  also carries `inventory_checked_at`, when the directories were last listed.
  ([#84](https://github.com/monocsp/remote_ci_monitor/pull/88))

### Added
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

[Unreleased]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.5...HEAD
[0.2.5]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/monocsp/remote_ci_monitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/monocsp/remote_ci_monitor/releases/tag/v0.1.0
