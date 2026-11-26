# Configuration

Everything the build machine can be told to do lives in one file, `~/.config/rcm/server.toml`
(`rcm init server` writes a starter). `examples/server.toml` is the same file with every key and a
comment on each. Sessions keep a much smaller `~/.config/rcm/client.toml`, and a remote worker
keeps `worker.toml`.

`rcm check --config server.toml` validates a server file (and its data dir and git) without
starting anything.

The server file is found in this order: `--config`, `$RCM_CONFIG`, `./rcm.toml`,
`$XDG_CONFIG_HOME/rcm/server.toml`, `~/.config/rcm/server.toml`. Any key of the `[server]`,
`[estimate]`, `[host]` and `[display]` sections can be overridden by an environment variable
named `RCM_<SECTION>_<KEY>` — `RCM_SERVER_DATA_DIR=/srv/rcm rcm serve` — which wins over the
file and loses to the matching flag (`--data-dir`, `--port`). A variable that is set but empty
is applied as the empty string, not ignored: `RCM_SERVER_DATA_DIR=` means `data_dir = ""`, the
current directory, and creates `rcm.sqlite3` there.

| file | who reads it | how to create it |
|---|---|---|
| `server.toml` | the build machine (`rcm serve`) | `rcm init server` |
| `client.toml` | every session (`rcm run`, `rcm top`, …) | `rcm init client [--server URL]` |
| `worker.toml` | a second build machine (`rcm worker`) | `rcm init worker [--server URL\|auto]` |

New to rcm? Read the [usage guide](usage.md) first — it walks through a first job with
screenshots. This page is the reference.

## Presets and step markers

The server only runs **presets** from its config. A session sends a preset name and inputs; inputs
arrive as `RCM_INPUT_<NAME>` environment variables (never spliced into the command line).

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]      # runs from the uploaded workspace root
pool = "default"                        # worker pool for this preset's jobs (default "default")
pools = []                              # extra pools a session may choose with --pool
timeout_seconds = 1200
expected_seconds = 480                  # used until enough real samples exist
duration_key_inputs = ["scope"]
artifacts = ["test/**/goldens/*.png"]   # files the job produces that sessions may fetch back
artifacts_on = "always"                 # "always" | "failure" — collect only when the job fails
requires = ["fvm", "gitleaks"]          # tools the job needs — looked up before it starts
[[presets.inputs]]
name = "scope"
type = "choice"
choices = ["full", "commit", "fast"]
default = "full"
```

Your script can report progress by printing markers at the start of a line:

```
::rcm::steps::3            # optional: total step count
::rcm::step::analyze       # a new step starts (the previous one ends)
::rcm::step-end::ok        # optional: "ok" or "fail"
::rcm::summary::all green  # optional: one-line result shown in the queue
::rcm::fail::flaky_test    # optional: names something that failed (a step, a test, a file)
::rcm::progress::41/68::inquiry_photo/android::run   # optional: progress inside the current step
::rcm::action::login::Sign in on the build machine   # optional: a person has to act
```

### Progress inside a step

`::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]` reports progress **inside the
current step** — a 40-minute chunk loop, a parallel set of child processes, a device-lock wait.
The line is ignored unless it fits the grammar: `done` and `total` are integers with
`total ≥ 1` and `0 ≤ done ≤ total`, `unit` is the one thing the line is about (`chunk/android`,
`test`, `lock devices`; up to 120 characters, control characters removed), `state` is one of
`run · ok · fail · skip · env · review · blocked · wait`, and `note` is optional free text (up to
200 characters). Print only a denominator the script actually knows — if you do not know it, do
not print it: the queue falls back to declared steps, then to time, and says so. A new
`::rcm::step::` clears it; the line before the first step counts too.

In `progress` (queue rows and `GET /jobs/<id>`) the last line is `sub`
(`{done, total, unit, state, note, at}`, `null` when the step printed none) and `units[]` keeps the
**last** state per distinct unit in the order they were first seen, so a grid of chunks or
children draws itself. A step is limited to 500 distinct units: past that `done/total` still
counts but no cells are added and `units_truncated` is `true`. The web queue prefers `sub` for
the bar (`60% · 41/68 · inquiry_photo/android`), shows `now: <unit> · <state>[ · <note>]` in the
progress line, and draws the unit grid under the step list when there are at least two units.
These are added keys; `schema_version` stays 1. Judges must never parse this marker — it is display
only.

Child processes buffer stdout, so markers may arrive late. Use `PYTHONUNBUFFERED=1`, `stdbuf -oL`,
or `flutter --no-color` style flags in your scripts when timing matters. Job elapsed time is always exact.

A finished job keeps its step times. `GET /jobs/<id>` (and so the JSON `rcm run` and `rcm wait`
print) carries `step_timeline`: `timing` (`as_received`, as above), `steps_total`,
`steps_total_partial` and `steps[]` with `index`, `name`, `started_at`, `ended_at`, `seconds` and
`ok` — the same values the queue showed while the job ran, recomputed from the markers the server
stored. A job that printed no step markers has `steps: []` and `steps_total: null`; a job that
failed before it started has the same empty timeline. When the server cannot read the markers the
key is `null` and `step_timeline_error_code` says why — an empty list is never used to cover a
read that failed. `ok` is what the markers said: a step closed by the next `::rcm::step::` is
`true`, the last step of a job that exited 0 is `true`, and the last step of a failed or cancelled
job is `null` unless the script declared it failed (`::rcm::fail::<name>` or
`::rcm::step-end::fail`), in which case it is `false` — a failure is never inferred, and the stored
`failed_step` and `last_step` are not changed by the timeline. Running jobs keep `progress`;
`/api/status` rows never carry `step_timeline`.

### When a person has to act

`::rcm::action::<code>::<note>` tells rcm that the job cannot go on without a person — a store
console asks for a sign-in again, a device needs unlocking. `code` is `[a-z0-9_-]{1,32}`
(anything else makes the line not a marker) and is what a skill branches on; `note` is optional,
up to 200 characters, with control, bidirectional and zero-width characters removed (the same
rule as the failure names hooks get). The note is readable without a token, goes into
hook environments and is printed in terminals: **never put an account name, an e-mail address or
any other personal detail in it.**

The last line becomes the job's `action`: `{code, note, at}` (`at` is when the server received
it), `null` when the job printed none. It is stored like the other markers, so it stays after the
job ends — on `GET /jobs/<id>`, the JSON `rcm run` and `rcm wait` print, and the queue and recent
rows of `/api/status` (an added key; `schema_version` stays 1). Lines from a remote worker are
parsed by the server like every other marker.

- A **running** job with an action has the reason `needs_action`. It is first in the actionable
  reasons («Needs a look»): every other reason is something rcm inferred, this one the job said
  itself, with what to do. It outranks `stuck` and `overdue` — a job waiting for a person is
  usually quiet — and a job being cancelled stays `cancelling`. The action stays until the job
  ends; there is no marker that clears it. The web page's status column and «Needs a look» read
  `needs action · <code> · <note>` (`사람이 할 일 · …` in Korean); the code and the note are the
  job's own words and are not translated.
- **`rcm wait`** prints `#<id> needs action · <code> · <note>` on stderr once for each action it
  sees. With **`--return-on-action`** it prints the JSON (with `action`) and exits **4** as soon as
  the job has an action — also one that was already there when the wait started, since a session
  that attaches later must not miss it. The job keeps running. Exit 4 only ever comes with that
  flag. After the person has acted, wait again **without** the flag (`rcm wait --job N`): the
  action is still the job's last one, so the flag would return at once. A job that is being
  cancelled, or was cancelled, prints no such line and never returns 4: it will not run on.
  Neither does a job that ended any other way (succeeded, failed, timed out, lost, expired); its
  action is still in the final JSON.
- **Hooks** get `RCM_ACTION_CODE` and `RCM_ACTION_NOTE` (both empty without an action); a `url`
  rule finds `action` in `job`. rcm sends nothing in the middle of a job — a script that wants a
  chat message at that moment sends it itself.

The usual pattern is a script that checks the sign-in first, prints the action and exits 75, with
a preset that runs it again a few minutes later.

### Saying what failed

`failed_step` is only ever a step your script **declared** as failed, with `::rcm::step-end::fail`
or `::rcm::fail::<step name>`. Without a declaration the field is `null` and the job carries
`last_step` instead — where it was when it ended, with no claim about the cause. This matters for
scripts that run several things at once and replay their logs afterwards: the last
`::rcm::step::` heading is not the failure, and rcm will not pretend it is.

`::rcm::fail::<name>` takes any name, not only step names — a test file, a case, a check. The
server never parses your output; it only counts the names you print. It keeps them per job (120
characters each, 100 per job) and, when a job fails, tells you how often each name was red in the
recent runs of the same key:

```
failed: test — every one of the last 8 gate runs
failed: just_audio_screen_music_port_test.dart — 2 of the last 8 gate runs · intermittent?
```

The window is `failure_window_jobs` (20) finished jobs of that key — cancelled and lost jobs say
nothing, so they are left out, and so is a job that ended `tool_missing` before its script could
start — and nothing is judged until there are `failure_min_jobs` (3) of them. Runs that failed
without naming anything stay in the denominator and are reported separately, so the count can
understate a flaky test but never overstate it.

The names also go into the job's one-line summary — the line `rcm run`, `rcm wait`, the queue and
the notification hooks show. Instead of `exit 1` it says what failed:

| what the job declared | summary |
|---|---|
| a failed step and unit names (`::rcm::fail::test/feature/home/x_test.dart`, …) | `test: x_test.dart +1 more (exit 1)` |
| only a failed step | `analyze failed (exit 1)` |
| unit names but no step | `x_test.dart (exit 1)` |
| nothing | `exit 1` (as before) |
| more names than the ledger keeps | `test: u0_test.dart +99+ more (exit 1)` — the `+` says the count is a floor |
| a timeout, with a step open when it ended | `limit 40m (in test)` — where it was, not a claim about the cause |

The first name is shortened to its last path segment and 80 characters; the full list is in
`failures[]`. Step names never count as units — a declared failed step is the `step` part. A line
your script printed with `::rcm::summary::` still wins: it is your team's sentence. The summary code
stays `exit_code` (`timed_out` for a timeout), so a script that filters on `summary_code` keeps
working; `summary_args` gains `step`, `first`, `more` and `more_truncated` (`last_step` for a
timeout). A job a remote worker ran gets the same sentence as one a local lane ran.

### Deploy presets: run a remote ref instead of an upload

Gates run the session's working tree. Deploys and releases should run a **committed, pushed** ref,
so the server fetches it itself (`source_modes = ["git_ref"]`). Declare the repository once and
point the preset at it:

```toml
[[repos]]
name = "app"
url = "git@github.com:org/app.git"      # any git hosting; uses the build machine's git credentials

[[presets]]
name = "deploy"
argv = ["bash", "scripts/deploy.sh"]
source_modes = ["git_ref"]
repo = "app"                            # optional when exactly one [[repos]] is configured
concurrency_group = "deploy"
```

```bash
rcm run deploy --ref v1.2.3             # branch, tag or full commit sha; nothing is uploaded
```

- The server resolves the ref to a commit sha **at submit time** (`git ls-remote`, 20 s limit) and
  that sha is what runs, even if the branch moves later. Two sessions submitting the same commit
  join the same job. The sha is in the JSON output and in the queue (`app @a1b2c3d · ref main`).
- Fetches go into a local mirror under `<data_dir>/mirrors/<name>/`; the workspace is a detached
  checkout with `.git` kept, so `git describe` works. `git submodule` is **not** initialised — run
  `git submodule update --init` in your script if you need it.
- Refs are validated (no leading `-`, no `..`, no control characters) before they reach git, and
  repository URLs must be `https://`, `ssh://`, `git://`, `file://`, `user@host:path` or an absolute
  path. Extra env for the script: `RCM_REF`; `RCM_BASE_SHA` is the pinned commit, `RCM_DIRTY=0`.
- A preset with `source_modes = ["git_ref"]` rejects tree uploads (400), and `--ref` on a tree
  preset is a usage error.

### Credentials per repository

By default git authenticates with whatever the build machine's git is set up to use. On a Mac that
is often `gh auth git-credential`, which hands out the token of the **active** `gh` account — and
there is one active account per machine. If two repositories need two GitHub accounts, switching
the active account for one of them silently breaks the other: GitHub answers a private repository
it cannot see with `Repository not found`, `git ls-remote` exits 128, and every `git_ref`
submission for that repository is refused before it reaches the queue.

Give that repository its own token instead:

```toml
[[repos]]
name = "app"
url = "https://github.com/org/app.git"
token_file = "~/.config/rcm/tokens/app.token"   # one line: a token that can read the repository
```

- Every git call that goes to the network for this repository — resolving the ref at submit time,
  fetching into the mirror, the Store tab's fetch — takes its credential from that file and from
  nothing else: the machine's credential helpers are cleared for those calls only. Other
  repositories keep the machine's credentials.
- The token never passes through rcm. Only the path goes into git's environment; git reads the
  file when it asks for a password. Keep the file `chmod 600`.
- `token_file` needs an `https://` URL (a token is an HTTPS credential; git ignores it for ssh), and
  `~` is expanded on the machine that reads the config. A remote worker reads its own
  `worker.toml`, so give it its own `token_file` there — the token is never sent over the wire.
- If the file is missing or unreadable, that repository's jobs fail with *the credential file for
  this repository is not readable* instead of a git error. The server still starts, so a token can
  be replaced while it runs.
- Needs git 2.31 or later.

### Presets in their own file: `include`

A project's presets can live in their own file next to `server.toml`, instead of being pasted into
it. List the files at the very top of `server.toml`, before the first `[section]` (TOML would
otherwise read `include` as a key of that section, and rcm says so):

```toml
# ~/.config/rcm/server.toml
include = ["presets.dolomood.toml"]   # relative to this file's folder; an absolute path or ~ works too

[server]
...
```

```toml
# ~/.config/rcm/presets.dolomood.toml — presets only
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
```

- **An included file may only hold `[[presets]]`.** `[server]`, `[[repos]]`, `[[notify]]`,
  `[[probes]]` and another `include` are refused with the file's name: repository tokens, secrets
  and the bind address stay in `server.toml`. Includes are one level deep.
- **The file must be owned by the user the server runs as, and not writable by group or others**
  (`chmod go-w presets.dolomood.toml`). A preset is the argv the server runs; a file anyone can
  write would decide that argv. `server.toml` itself is not held to this rule.
- Presets keep their order: the ones in `server.toml` first, then each included file in the order
  `include` lists them. Everything that checks presets — `checks`, a `[[notify]]` rule's
  `presets`, duplicate names — runs on the merged list, so an included preset can name one in
  `server.toml` and the other way round.
- A name defined in two files is refused: `preset 'gate' is defined in both server.toml and
  presets.dolomood.toml`. Listing `server.toml` itself, or the same file twice, is refused too.
- A missing or broken included file stops `rcm serve` like a broken `server.toml` does. `rcm
  reload` re-reads the included files as well; if one is missing or broken, nothing changes.
- `rcm check --config server.toml` counts the presets per file:
  `local presets 24 (server.toml 2 · presets.dolomood.toml 22)`.
- The connect skills' candidate helper (`scripts/rcm/rcm_candidate.py`, installed by
  `rcm-store-connect`) follows `include`: when the project's presets file has the same name as an
  included file (or `--into <entry>` names one), it merges the presets into a copy of that file
  instead of `server.toml` and writes both to a candidate folder for `rcm check`. Copy the two
  files into place, then `rcm reload --dry-run` and `rcm reload`.

**rcm does not fetch the file.** There is no git pull and no URL — only a local path. To use the
file a repository keeps, point `include` at that repository's checkout on the build machine, and
decide who moves that checkout and when. Be clear about what that means: **whoever can change an
included file decides the argv the server runs on the next reload.** If the file is in a
repository's checkout, everyone who can push to that repository and get the checkout updated has
that power. Preview what a reload would apply with `rcm reload --dry-run` before you run it.

## Release profile

A repository can carry a **release profile**: which presets play which release role, which
secrets the Settings screen asks for, and how the store copy is previewed. It is the contract the
Store tab reads (`docs/release-contract.md`); rcm itself computes no build number, and nothing goes
live on approval by itself: a release runs only when a person asks for it with the version, the
build number and a time (`golive`, below). A repository without a profile has no Store tab and
everything else works as before.

The profile is a sub-table right after its `[[repos]]` entry (TOML puts `[repos.app.release]`
inside the **last** `[[repos]]` element, so keep the two together):

```toml
[[repos]]
name = "app"
url  = "git@github.com:org/app.git"

[repos.app.release]
default_branch       = "main"                    # default "main"
tag                  = "prod/{version}-{build}"  # default; must contain {version} and {build}
build_number_policy  = "auto"                    # "auto" (default) | "manual" — the Store tab's «Build number» toggle default
plan_max_age_minutes = 30                        # default 30; integer > 0
version_ttl_hours    = 24                        # default 24; integer > 0 — an unedited new-version draft (or one whose create job failed) is discarded after this
driver               = "scripts/release/product_release.sh"   # optional; relative to the repository
secrets_dir_env      = "APP_SECRETS"             # required when secrets are listed; ^[A-Z][A-Z0-9_]*$

[repos.app.release.presets]                      # values are [[presets]] names
plan   = "release-plan"                          # required
upload = "release-upload"                        # required
review = "release-review"                        # required
gate   = "gate-smoke"                            # optional
qa     = "scenario-qa"                           # optional
dev    = "deploy-dev"                            # optional
version = "release-version"                      # optional — «new version» from the web (prefill / create / delete)
status = "release-status"                        # optional — both stores' review state (read-only)
golive = "release-golive"                        # optional — a release a person asked for (plan / release)

[[repos.app.release.secrets]]                    # zero or more — names and shapes, never values
name     = "AuthKey.p8"                          # file name or env name; unique in the profile
kind     = "file"                                # "value" (default) | "file" | "dir"
optional = false                                 # default false
verify   = "asc"                                 # "asc" | "play" | "github" | "keystore" | "none" (default)
max_kb   = 64                                    # kind = "file" only; default 512

[[repos.app.release.secrets]]
name  = "review_information"
kind  = "dir"
files = ["demo_user.txt", "demo_password.txt"]   # kind = "dir" only: file names that must exist inside

[repos.app.release.listing]                      # optional
preview       = ["python3", "scripts/release/store_listing.py", "preview"]
diff          = ["python3", "scripts/release/store_listing.py", "diff", "--live"]
validate      = ["python3", "scripts/release/store_listing.py", "validate", "--build-name", "{version}", "--version-code", "{build}"]
screenshots   = ["store/screenshots/**/*.png"]
release_notes = "store/release_notes/{version}/*.txt"
ref           = "dev"                            # optional; the branch the copy is read from (default: default_branch)
```

Loading the file checks **shape**: unknown keys anywhere under `release` are an error, `tag`
must contain both placeholders, `build_number_policy`, `kind` and `verify` take only the values
above, `files` is only valid for `kind = "dir"` and `max_kb` only for `kind = "file"`, `driver`
is a relative path, and every error names the section and key
(`[repos.app.release.presets]: unknown key(s): deploy (roles are plan, upload, review, gate, qa,
dev, version, status, golive)`). Whether the presets exist and behave is a `rcm check` matter, so a
half-written profile degrades the Store tab without stopping the server.

`{version}` in `tag` (and in `listing.validate` and `listing.release_notes`) is the round's version
name: the one name when both stores get the same one, and the iOS name, a `+` and the Android name
when they differ — `prod/1.1.1-181` or `prod/1.1.1+1.0.1-181`
([release contract](release-contract.md) §2 «Two store version names»).

`rcm check --config server.toml` prints one row per profile, `release <repo>`:

| verdict | when |
|---|---|
| `FAIL` | a required role (`plan`, `upload`, `review`) is empty or names a preset that is not in `[[presets]]` |
| `FAIL` | the `plan` preset lacks the `build_name` input; `upload` lacks `build_name confirm_build_number mode platform`; `review` lacks `build_name confirm_build_number mode platform play_managed_publishing listing phased` |
| `FAIL` | the `upload` preset's `mode` input defaults to `upload`, the `review` preset's `mode` defaults to `submit`, or the `golive` preset's `mode` defaults to `release` — the irreversible mode is never the default |
| `FAIL` | a `version` preset lacks `mode ios_version android_version asc_version_id`, or its `mode` does not default to `prefill` (`create` makes a store draft, `delete` is irreversible) |
| `FAIL` | a `status` preset lacks the `build_name` input, or a `golive` preset lacks `build_name confirm_build_number platform mode` |
| `FAIL` | secrets are listed but `secrets_dir_env` is not set, or a secret name repeats |
| `warn` | an optional role (`gate`, `qa`, `dev`, `version`, `status`, `golive`) is unset, or the secrets folder `<config dir>/secrets/<repo>/` does not exist yet (the Settings screen creates it) |
| `warn` | the `version` preset's `mode` input lists the values it takes and `create` or `delete` is missing — those are the two rcm sends, so «new version» and «discard» would be refused when the job is submitted |
| `warn` | the `review` or `upload` preset has no `listing_json` input — the listing copy edited in the web UI cannot reach the script (re-run `/rcm-store-connect`, which adds the input) |
| `warn` | the `review`, `upload` or `golive` preset has no `build_name_android` input — the two stores must then share one version name (a round that gives them different names is refused) |

The detail lists each role with its preset, the driver when one is set, and the number of secrets:
`ok   release app   plan=release-plan upload=release-upload review=release-review gate=gate-smoke
· driver=scripts/release/product_release.sh · 2 secret(s)`. The row does not open the repository,
so it cannot say whether `driver` exists there.

Writing all of this by hand is not the intended path. The package ships **connect skills** —
`rcm-store-connect` (required tier), `rcm-gate-connect`, `rcm-qa-connect`, `rcm-release-driver`
and the `rcm-connect` orchestrator — that ask a few questions inside the project and write the
profile block, the presets and script skeletons. `rcm skills list` prints one line per skill;
`rcm skills install --into <project>` copies every skill into `<project>/.claude/skills/<name>/`
and prints one line per file: `written`, `kept` (identical already) or `skipped` (the project's
copy differs — the command exits 1 and leaves it alone; `--force` overwrites). Nothing outside
`.claude/skills/` is touched.

One more skill is not about releases: **`rcm-ci-connect`** lets a repository choose, in
`.rcm/ci.toml`, whether its checks run on GitHub Actions, on this build machine, or both. A
watcher on the build machine (`rcm_ci_watch.py`, run by launchd every minute) submits the head of
each same-repository pull request with `rcm run <preset> --source git_ref --no-wait` and posts a
`rcm/ci` commit status; a `[[notify]]` hook (`rcm_ci_status.py`) posts the result when the job
ends. GitHub's required check waits for that status and runs the checks itself when rcm does not
answer in time. Pull requests from forks, deleted forks and `dependabot[bot]` always run on
GitHub. The server is unchanged — the code that talks to GitHub lives in the skill's templates,
and the two tokens it needs (a fine-grained GitHub token, a non-admin rcm client token) are
created by the owner. `rcm_ci_watch.py --check` prints a table that must be all `OK`.
With `fallback = "none"` the required check is `rcm/ci` itself and there is no waiting workflow:
the watcher alone submits, leaves a job held by `worker_down` or `paused` waiting, and never posts
`pending` over a result the job script posted. Its tokens can then come from where they already
are — `rcm_client_config` (the rcm client's `client.toml`) and `github_token_command` (for example
`gh auth token --user <login>`). This repository runs that way: `.rcm/ci.toml` at its root.

### Secrets on the server

The values the profile names are entered once, in the Store tab's Settings screen (or with
`curl`), and live as files under **`<config dir>/secrets/<repo>/`** — the directory next to the
loaded `server.toml`, mode `0700`, one file per secret, mode `0600`; a `kind = "dir"` secret is a
sub-folder holding its `files`. Nothing is kept in the database or the browser. Every write goes
to a temporary file first and is renamed into place, so a half-written secret is never visible,
and a new value forgets the old value's verification.

What a job gets: when its preset's `repo` has a profile and the folder exists, the worker sets
`$<secrets_dir_env>` (`APP_SECRETS=/…/secrets/app` above) in the job environment and nothing
else — the scripts read the folder the way they already read a local one. The job's stdout is
masked: every `kind = "value"` secret of 8 or more characters is replaced by `****` before the
log is written, for local lanes and for logs a remote worker uploads alike (a value split across
two upload batches is not caught; the runner flushes whole lines, so that is rare).

The API never returns a value. Each item is `{name, kind, optional, verify, present, size,
fingerprint, verified_at, verify_error, verify_detail}` — `fingerprint` is the first four
characters plus `…` for a value, the first eight hex digits of the file's SHA-256 for a file, and
`n/m files` for a dir (which also lists `files[]` with `present` per file).

| route | token | what |
|---|---|---|
| `GET /api/repos` | read rule | every `[[repos]]` entry with `release: true/false` and, for profiles, `setup: {required, present, verified, complete}` |
| `GET /api/repos/<repo>` | read rule | the profile without values, `setup` (plus `missing[]`), the mirror (`path`, `fetched_at`, `age_seconds`) and `branches` (`main` = `default_branch`, `dev`, `main_in_dev`) read from the mirror only |
| `POST /api/repos/<repo>/fetch` | client token | updates the mirror (all heads and tags, pruned, the same lock the lanes use); 502 `fetch_failed` with the last 60 characters of git's stderr, 504 on timeout |
| `GET /api/repos/<repo>/secrets` | any client token | `{dir_env, items[]}` as above — never a value |
| `PUT /api/repos/<repo>/secrets/<name>` | admin | the body is the secret: `Content-Type: text/plain` for a value (one non-empty line, trailing newline dropped), `application/octet-stream` for a file (at most `max_kb`), and `…/secrets/<name>/<file>` for a dir's file. 400 wrong kind or file name, 404 a name that is not in the profile, 413 too large |
| `DELETE /api/repos/<repo>/secrets/<name>` | admin | only for `optional = true` secrets (409 otherwise) — a required one is replaced, never removed |
| `POST /api/repos/<repo>/verify` | client token | `{"names": [...]}` or `{}` for every secret with `verify != "none"`; runs the read-only check and records `verified_at` / `verify_error` in `<config dir>/secrets/<repo>/.verify.json`; returns the secrets document |

`verify` kinds in this build: `github` (`GET https://api.github.com/user` with the token, 5 s,
reports `login: <name>`), `keystore` (`keytool -list -keystore <file>`, plus `-storepass` from a
value secret named `KEYSTORE_PASSWORD` when the profile has one; `keytool missing` when the
build machine has none), and `none`. `asc` and `play` answer `not implemented in this build` as a *detail*, not an error (a JWT signature this build cannot produce must not lock the gate; the page shows the sentence) —
the page shows the cross and the text rather than a green mark nobody earned.

**The setup gate.** `setup.complete` is true when every non-optional secret is present and either
has `verify = "none"` or was verified without error. The release write routes below answer
`409 setup_incomplete` (with the `setup` counts in the body) until then; the routes above and the
four read-only release routes never do, so the Settings screen always works. An unknown
repository, or one without a profile, is 404 on all of them; worker tokens are refused everywhere
here.

### Release routes and the driver

Everything under `/api/repos/<repo>/release/…` turns the profile into actions. Jobs go through
the normal `POST /jobs` path (`git_ref` source, same validation, joiners, priority and pools);
rcm sends exactly the inputs of the contract's §2 table and every other input the preset declares
keeps its default. Writes take a Bearer token only.

| route | token | what |
|---|---|---|
| `GET …/release` | read rule, always | `setup`, then the latest job per role: `plan` (`job_id`, `state`, `measured_at`, `age_seconds`, `stale`, `build_name`, `doc`), `review.plan` (same shape) and `review.result`, `upload` (`job_id`, `state`, `doc`), `status` (`job_id`, `state`, `measured_at`, `age_seconds`, `stale`, `doc` — aged from the document's `checked_at`, no `build_name`), `golive` (`plan` in the shape of `plan`, `result`, and `live[]` — the go-live jobs that are booked or running: `job_id state build_name platform confirm_build_number not_before start_deadline`), and `jobs[]` — the role presets' jobs, newest first, at most 50, each with `id preset role state sha ref started_at finished_at artifacts[]`. `status` jobs run often, so they get at most 3 rows of their own in `jobs[]` and never take one of the 50. Each role preset is read through its own window of its newest 500 jobs (50 for `gate`, `qa` and `dev`, which only have rows), so a `status` job every few minutes does not push the last plan or review out of sight. "Latest per role" is the newest job of that preset whose artifact bundle contains the role's file (`plan.json`, `upload.json`, `review-plan.json` / `review.json`, matched by base name anywhere in the bundle); `doc` is that file parsed (256 KB at most; unreadable or invalid gives `doc: null` plus `doc_error`). `measured_at` is the document's own or, failing that, the job's end; `stale` is `age_seconds > plan_max_age_minutes × 60`, or a `measured_at` later than now (a clock that is wrong never makes a plan fresh). Computed from the database and the artifact store only |
| `POST …/release/plan` | client token | `{build_name, ref?}` (`ref` defaults to `default_branch`) → submits `presets.plan` with `build_name` → `202 {job_id, joined, state, sha, submission}` (an identical running job is joined; `submission` is the cancel secret, as `POST /jobs` gives it) |
| `POST …/release/review` | client token; **admin for `mode = submit`** | `{build_name, ref?, mode: plan\|submit, platform?, confirm_build_number?, play_managed_publishing?, listing?, phased?}` → submits `presets.review`. In `plan` mode `confirm_build_number` is sent empty and `play_managed_publishing` as `not-checked` unless this body says `confirmed-on` |
| `POST …/release/upload` | client token; **admin for `mode = upload`** | `{build_name, ref?, mode: rehearsal\|upload, platform?, confirm_build_number?, android_track?}` → submits `presets.upload` |
| `POST …/release/status` | client token | `{build_name?, ref?}` → submits `presets.status`, with `build_name` only when the body has one (empty: the script reads the newest versions the stores have) → `202 {job_id, joined, state, sha, submission}`. Read-only for the stores, so any client token will do; 409 `role_not_configured` when the profile has no `status` preset. The review panel's **Check again** sends this route the round's version |
| `POST …/release/golive` | client token; **admin for `mode = release`** | `{build_name or version_id, ref?, mode: plan\|release, platform: ios\|android\|both, confirm_build_number?, start_at? \| start_in_seconds?}` → submits `presets.golive`. `plan` (the default) reads the stores now → `202`; a time is 400 `golive_plan_now_only`. `release` runs the go-live gate below and goes now → `202`, or at `start_at` / after `start_in_seconds` (read as for `rcm run --at`) → `201` with `not_before`, `start_deadline` and `timezone`. Every answer carries `submission` |
| `GET …/release/versions` | read rule, always | the store version drafts of this repository: `{live: {ios, android, from_plan_job}, hints: {ios, android}, ttl_hours, drafts[], history[]}`. `live` comes from the latest succeeded plan's `store.asc_live` and `store.play.production_name`; `hints` is that document's `next_version_hint` or, failing that, each live name with its last integer + 1 (unknown stays `null`). `drafts[]` is every open draft, newest first; `history[]` the last 20 submitted ones (`{id, ios, android, submitted_at, review_job_id}`). A draft row is `{id, repo, ios_version, android_version, build_name, state, created_by, created_at, last_edit_at, expires_at, expired, expiry_warned, asc_version_id, error, create_job_id, delete_job_id, release_id, review_job_id, upload_job_id, has_prefill, has_edits, changed}` — `state` is `creating`, `editing`, `running`, `submitted`, `discarded` or `failed`; `build_name` is the iOS name, or the Android one when there is no iOS name; `changed` counts the edited listing fields |
| `POST …/release/versions` | admin | `{ios_version?, android_version?, ref?}` → opens a draft. At least one name, each `major.minor.patch` and greater than that store's live name when the plan knows it. With a `version` preset it submits that preset with `mode = create` → `202 {id, job_id, state: "creating", build_name}`; without one the draft is `editing` at once → `201 {id, job_id: null, state, build_name}`. The job's `version.json` and `prefill.json` then fill `asc_version_id` and the prefilled listing; a failure leaves the row `failed` with an `error` sentence |
| `GET …/release/versions/<id>` | read rule, always | one draft — the row above plus `prefill`, `edited`, `diff` (the shape of `…/diff`) and `release` (the `GET …/release` document with this version's `build_name`). It runs **no** subprocess, because the version page polls it every five seconds: the driver's `--status` and the two listing commands stay behind `GET …/release/driver` and `GET …/release/listing`, which the page calls at its own pace |
| `PUT …/release/versions/<id>/listing` | admin | `{ios?: {…}, android?: {…}}` — only the field names the contract's `prefill.json` uses, strings only, 16 KB each and 256 KB of body. Only the fields in the body change; `null` removes one and a platform set to `null` removes it whole → `{id, state, edited, last_edit_at, diff}`. Character limits are **not** enforced here: the value is saved and the page's counter says so, because the store has the last word |
| `GET …/release/versions/<id>/diff` | read rule, always | `{fields: [{platform, key, old, new}…], screenshots: {ios, android}}` — only the fields whose value differs from the prefill, ignoring surrounding whitespace and `\r\n` against `\n`. `screenshots` is `same` (the prefill knows that platform) or `n/a`; uploading one is not in this build |
| `DELETE …/release/versions/<id>` | admin | discards the draft. With an `asc_version_id` and a `version` preset it submits `mode = delete` → `202 {id, job_id, state}`, and the row becomes `discarded` when that job exits 0; otherwise the row is `discarded` at once → `200 {id, job_id: null, state}` |
| `GET …/release/listing[?build_name=]` | read rule, always | runs `listing.preview` and `listing.diff` in a checkout of `listing.ref` (default `default_branch`) made from the mirror (`<data_dir>/listing/<repo>/checkout`, rebuilt when the branch SHA changes; 20 s and 64 KB of stdout each) → `{configured, sha, preview[], diff[], release_notes: {path, text} \| null, screenshots[]: {path, bytes, width, height}, errors[]}`. `release_notes` substitutes `{version}` with `build_name`, or `*` without one; `width`/`height` are `null` in this build. No `listing` in the profile → `{configured: false}`; no mirror yet → `sha: null` and one line in `errors[]` (never remembered). The answer is kept for 30 s per repository, checkout SHA and `build_name`, so a page that polls does not re-run the commands; a repository's entries are dropped as soon as its mirror moves, and an answer computed under a different `listing` section is never served |
| `GET …/release/listing/file?path=<rel>` | read rule, always | the bytes of one screenshot — the path must match a `listing.screenshots` glob and be an image type; 5 MB at most (413) |
| `POST …/release/listing/validate` | client token | `{build_name, build?}` → runs `listing.validate` with `{version}` and `{build}` substituted → `{ok, lines[], exit}` (plus `error` when it failed to run) |
| `GET …/release/github` | read rule, always | from the mirror only: `log[]` (`sha subject author at` × 5 of `default_branch`), `tags[]` (`name at` × 5 newest whose name starts with the `tag` pattern's literal prefix, e.g. `prod/`), and `prs: null` — next: pull requests via the `GH_TOKEN` secret |
| `POST …/release/start` | admin | `{build_name, android_track?, dry_run?}` → runs the driver (below) → `202 {release_id, pid, build_name}` |
| `POST …/release/confirm` | admin | `{build_name, build_number}` → re-runs the driver with `--confirm-build-number N` **only if** the last `plan: N = <n>` line of that build's log equals what was typed |
| `POST …/release/abort` · `POST …/release/retry` | admin | `{build_name?}` (default: the latest run's) → runs the driver with `--abort` / `--retry` |
| `GET …/release/driver` | read rule, always | `{configured, running, release_id, kind, build_name, started_at, started_by, pid, exit_code, confirmed_n, log_tail[] (last 60 lines, secret values masked), plan_n, status[] (the output of `<driver> --status`, run synchronously with a 10 s limit), status_error, stages[], knows_version_stage}`. `{configured: false}` when the profile has no `driver`. That subprocess is the only costly part, and its answer is kept for 20 s per repository, driver checkout SHA, `build_name`, round id, running flag and exit code — so a page that polls does not multiply `--status` runs on the build machine, and a remembered answer cannot outlive the round it describes. The ledger row, the log tail and `plan_n` are read fresh every time. `stages` is the driver's own `stages: V S0 … S8` line parsed into a list, `null` when it printed none, and `knows_version_stage` says whether `V` is in it (release contract §5). A driver that does not advertise `V` is started **without** `--version-id` — the round still runs under the name rcm read from the version row — and the page says «this driver does not know the V stage — re-run `/rcm-release-driver`» instead of letting an old driver die on an unknown argument |

**`version_id`.** `POST …/release/plan`, `review`, `upload` and `start` take an optional
`version_id` — the number of an open draft — instead of a typed `build_name`. The server then
fills `build_name` from the row (a `build_name` in the same body that disagrees is 400
`build_name_mismatch`), and adds two inputs to `review` and `upload` when there is something to
add: `listing_json`, the edited listing merged over the prefill, whenever the two differ, and
`build_name_android`, the Android name, whenever the two stores get **different** version names.
A preset that does not declare the input it needs is refused (409 `listing_json_unsupported` /
`split_version_unsupported`) rather than run without the edit or under one name for both stores;
re-run `/rcm-store-connect` to add it. `plan` never gets either input — it reads the stores, and
one name is enough for that. `start` passes `--version-id` to the driver and links the round to
the row, so `confirm`, `abort` and `retry` continue on the same version. While a review job, an
`upload` job or a driver round is running for it, the row is `running` and cannot be discarded
under it; a `rehearsal` writes nothing to a store, so it leaves the row open. When more than one
of the three runs at once, the row goes back to `editing` only when the **last** of them ends —
a manual job finishing in the middle of a round leaves the row held. A round whose process is
gone is closed before that judgement is made, so nothing keeps a row `running` for ever, and a
restart makes the same judgement again.

**The 409 codes.** These are the server's own rules, kept whatever the page sends (`code` and
`error_code` carry the same value):

| code | when |
|---|---|
| `setup_incomplete` | any write route while the setup gate is closed; the body carries `setup` |
| `role_not_configured` | the role's preset is empty or names a preset that is not in `[[presets]]` |
| `unsafe_preset` | the role's preset declares an input named `automatic_release`, `rollout` or `release_status` — rcm never sends those, and refuses to run a preset that would take them (`inputs[]` names them) |
| `review_plan_required` | `mode = submit` without a **succeeded** review plan (`review-plan.json`) for the same `build_name` |
| `review_plan_stale` | that review plan is older than `plan_max_age_minutes` (`age_seconds` in the body) |
| `review_plan_blocked` | that review plan's `plan_verdict` is not `ok` |
| `managed_publishing_unconfirmed` | `mode = submit` with `platform` `both` or `android` and `play_managed_publishing` other than `confirmed-on` **in this body** — the value is never remembered |
| `plan_required` | `mode = upload` / `mode = submit` without a succeeded plan (`plan.json`) for the same `build_name` |
| `plan_stale` | that plan is older than `plan_max_age_minutes` |
| `build_number_mismatch` | `confirm_build_number` (review submit, upload) or `build_number` (driver confirm) is empty or does not equal the plan's `n` as an integer — the server compares, it never fills the number in. For `golive` it is compared with each chosen platform's `approved_build`; `auto` is not accepted there, and with `platform = both` the two approved builds must be the same number (when they differ, go live per platform; `approved_build` in the body) |
| `mirror_missing` | the driver or listing needs a checkout and the mirror has no `default_branch` yet — `POST …/fetch` first |
| `no_driver` | a driver route on a profile without `driver` |
| `driver_missing` | `driver` is not an executable file in the checkout |
| `release_running` | a driver process for this repository is still alive (`release_id` in the body) |
| `release_required` | `confirm` / `abort` / `retry` with no earlier run for that build |
| `version_exists` | a new version name that an open draft already carries (`id` and `state` in the body). A draft whose create job **failed** does not count — nothing was made in the store under that name, so the same name can be typed again |
| `version_closed` | a submitted or discarded draft — it cannot be edited, submitted or discarded again. `golive` is the exception: it takes a **submitted** draft (going live comes after the review), never a discarded one |
| `version_running` | discarding a draft whose review job, `mode = upload` job or driver round is still running (`review_job_id`, `upload_job_id` and `release_id` in the body) |
| `listing_json_unsupported` | the draft has an edited listing but the `review` / `upload` preset has no `listing_json` input |
| `split_version_unsupported` | the draft's two store version names differ but that preset has no `build_name_android` input |
| `golive_plan_required` | `golive` `mode = release` without a **succeeded** golive plan (`golive-plan.json`) for the same `build_name`, or one that did not read a chosen platform |
| `golive_plan_stale` | that golive plan is older than `plan_max_age_minutes` (`age_seconds` in the body). Checked when the release is asked for, never again when a booked one runs — the script reads the stores again then |
| `golive_plan_blocked` | a chosen platform's `approved` is not `true` in that plan (`platforms[]` names it). Only the chosen platforms count: iOS alone goes through while Android is still in review |
| `golive_running` | a `golive` `mode = release` job for the same `build_name` is booked (`scheduled`) or alive — now or later, one at a time (`job_id`, `state` in the body). Cancel it first |
| `release_route_required` | `POST /jobs` with the `golive` role's preset and `mode = release`, from any token, admin included: going live takes `POST …/release/golive` (or `rcm release golive`), which runs the gate. `review` and `upload` are not refused there — the driver submits `upload` with `rcm run` |

`mode = submit`, `mode = upload` and the `golive` `mode = release` also need an admin token (403 `admin_required`), as do every
`POST`, `PUT` and `DELETE` under `…/release/versions`. The version routes also answer 404
`version_not_found` (no such draft in this repository — another repository's number included) and
400 `empty` (neither version name), `pattern` (not `major.minor.patch`), `not_greater` (not above
the live name; `platform` and `live` are in the body), `listing_key` (an unknown platform or
field) and `listing_too_large` (one value over 16 KB).

**The driver.** `POST …/release/start` checks out `default_branch` from the mirror into
`<data_dir>/driver/<repo>/checkout` and runs `<driver> --build-name X [--android-track T]
[--dry-run]` there as a **detached** process (its own session, so a server restart does not kill
it). Its environment is the job environment for that repository — `PATH`, `HOME`, `LANG` and
`$<secrets_dir_env>` — plus `RCM_SERVER` (this server's URL) and `RCM_TOKEN`, a **client token
minted for that run** (`store-driver:<repo>:<id>`, not admin) and revoked as soon as the run ends,
so it never outlives the process; the value goes to the child's environment and nowhere else.
stdout and stderr append to `<data_dir>/driver/<repo>/<build_name>.log`, so `confirm`, `abort`
and `retry` continue the same log. Every run is a row in the database's `releases` table
(`id repo build_name kind started_by started_at pid log_path exit_code finished_at confirmed_n
confirmed_by android_track dry_run token_name`); `rcm gc` and the retention sweeps leave that
table, and the `versions` table behind the version routes, alone. The exit code is written by a wrapper to `<data_dir>/driver/<repo>/<id>.exit`, so
after a restart the next `GET …/release/driver` (or the next start) closes runs whose process is
gone, records the code (or `null` when it is unknown) and revokes their tokens. `--status` runs
with the same environment but without a token — it is read-only by contract. Confirm follows the
contract: the driver prints `plan: N = <n>` and exits 2, the page shows the number, a person
types it, and the server forwards it only when the two agree.

### Going live (`golive`)

Nothing goes live on approval by itself. A release runs only when a person asks for it with the
version, the build number and a time. The `golive` role is that request, and it has two modes:

- `plan` (the default, any client token) reads both stores and writes `golive-plan.json`: for each
  platform whether it is `approved` and the `approved_build`. It exits 0 whenever it could read the
  stores, approved or not — the answer is in the file.
- `release` (admin) asks the stores to go live. It takes the approved build number a person typed —
  never `auto` — for now, or for `start_at` / `start_in_seconds` like `rcm run --at` / `--in`. The
  gate is checked once, when the release is asked for: a succeeded golive plan for that
  `build_name` no older than `plan_max_age_minutes`, every chosen platform `approved` in it, the
  typed number equal to their `approved_build`, and no other go-live job of that build booked or
  running. A booked release is not checked again when its time comes; the script reads the stores
  again then, and a platform that is not approved ends the job with exit 2, never 75.

The same gate stands behind the web page — the version page's «Schedule go-live» area, which opens
only once `status.json` says that store approved this version and runs the plan first — and behind
`rcm release golive`:

```sh
rcm release status app                                     # both stores' review state (waits)
rcm release golive app --build-name 1.2.0 --platform both --plan
#   iOS      approved      build 185    PENDING_DEVELOPER_RELEASE
#   Android  not approved  build —      RELEASE_LIFECYCLE_STATE_IN_REVIEW
#   next: rcm release golive app --build-name 1.2.0 --platform ios --confirm-build-number 185 …
rcm release golive app --build-name 1.2.0 --platform ios --confirm-build-number 185 --at 15:00
#   golive #412 scheduled for 15:00 · start by 16:00 · build 1.2.0 · ios 185
#   follow: rcm wait --job 412 · cancel: rcm cancel 412
```

`POST /jobs` (and so `rcm run`) refuses the `golive` preset's `mode = release` from every token
with 409 `release_route_required` — that path would skip the gate. A submitted draft takes
`golive` (going live comes after the review); a discarded one does not. Play has no API to publish
a held release, so the project's go-live script publishes through a browser or asks a person; when
its Play Console sign-in is gone it prints an `action` marker and exits 75, and the next attempt
checks again ([release contract](release-contract.md) §2 and §6).

## Getting files back out of a job

A job that regenerates files — Flutter goldens are the reason this exists — leaves them in the
workspace on the build machine, and the workspace is deleted when the job ends. A preset that
declares `artifacts` gets those files collected **before** the workspace goes, and the session
that submitted the job can write them back into its own tree.

```toml
[[presets]]
name = "goldens"
argv = ["flutter", "test", "--update-goldens"]
artifacts = ["test/**/goldens/*.png", "test/failures/*.png"]
```

```sh
rcm run goldens --fetch-artifacts          # writes the PNGs back into your tree
rcm run goldens --fetch-artifacts --dry-run  # show what would be written, write nothing
rcm artifacts 412 --fetch --output ./out   # fetch later, into a directory you name
```

The result line separates what was compared from what was written:
`artifacts: wrote 12, unchanged 51, conflicted 1`.

**What is collected.** Only paths matching the globs, and only regular files inside the workspace.
Symlinks, hard links, device files and anything named `.git` are skipped and counted. A glob that
would match the whole workspace (`**`, `*`, `**/*`) is a config error — declare what you want back.
This is a guard against collecting the wrong thing, not a security boundary: the job runs as the
worker user and could copy anything it can read into a path the globs allow.

**What is written back.** Files you did not touch are overwritten; files you edited (or deleted)
while waiting are left alone and reported as `conflicted` — `--force` overwrites those too, except
one that changed between the preview and the write. Nothing outside the tree is ever written, and a
local file that is not in the bundle is never deleted.

**When it disappears.** Once your session has written every file, it acknowledges the bundle. If
nobody else joined that job, the server deletes it immediately; if anyone joined, it stays until
the TTL so they can fetch it too. Either way it is gone after `artifact_retention_hours` (24).

| key | default | meaning |
|---|---|---|
| `artifact_retention_hours` | `24` | how long a bundle lives, measured from when it is ready. Reading it does not extend it |
| `max_artifact_bytes` | `1073741824` | per job, the size of the collected files |
| `max_artifact_files` | `10000` | per job, how many files |
| `artifact_storage_max_bytes` | `10737418240` | for the whole server. When it is full, new bundles are dropped — existing ones are never evicted |
| `artifact_timeout_seconds` | `60` | how long collecting may take |
| `artifact_cancel_timeout_seconds` | `5` | the shorter budget after a cancel or timeout. Must be under `2 × worker_heartbeat_seconds` |
| `artifact_transfer_timeout_seconds` | `300` | one upload or download |
| `max_concurrent_artifact_transfers` | `2` | transfers at once. Over that, the server answers 503 immediately rather than making you wait |

Over a limit, **the job still succeeds or fails on its own merits** — only the artifacts are
dropped, and the reason is on the job (`over_bytes`, `over_files`, `timed_out`, `storage_full`).

## Keeping a job's files: the shelf

A bundle is a pickup point: it is gone 24 hours after it is ready, or the moment its owner fetches
it. A job you mark is also kept on the server's **shelf**. The server copies the job's artifacts
into a folder of its own, and anyone on the same network opens them in a browser — a phone, a
laptop, another Mac — at `http://macmini.local:8787/shelf/412/`. Nothing to install, no token.

```toml
[[presets]]
name = "golden-update"
argv = ["bash", "scripts/golden_update.sh"]
artifacts = ["build/.golden-update/goldens.tar.gz", "build/.golden-update/manifest.json"]
keep = true              # always put this preset's jobs on the shelf (default false)
```

```sh
rcm run golden-update --keep   # keep this one job (any preset that declares artifacts)
rcm keep 412                   # keep a job later, while its bundle still exists
rcm shelf                      # what is on the shelf, with the link
rcm shelf rm 412               # remove it now (admin token)
```

`rcm run --keep` ends with the link: `shelf: http://macmini.local:8787/shelf/412/`. It waits up
to a minute for the copy; if the copy is not done by then the line says `(still copying)` and the
link works a moment later. The exit code is always the job's own.

**What goes on it.** A preset with `keep = true`, a `--keep` on `rcm run`, or `rcm keep N` later.
`keep = true` without `artifacts` is a config error (`keep needs artifacts`). With
`artifacts_on = "failure"` only failed jobs are kept, because a green job collects nothing; that
is allowed, and `rcm check --config` says so. The keep is frozen when the job is submitted, so a
reload does not change jobs already in the queue. A submission that joins a running job turns its
keep on (never off), and a job that supersedes an older one takes over its keep. `rcm run
--fetch-artifacts` deletes the bundle as soon as the files are written, so a job fetched that way
cannot be kept afterwards — use `--keep` from the start; with both flags the bundle waits until
the copy is done.

**What you see.** `/shelf/` lists the shelf in two columns, kept and cleanup pending, with the
usage on top (`12.3 GB / 53.7 GB`). `/shelf/412/` shows the job (preset, inputs, branch, commit,
when it finished, who asked), a grid of its images, a file table and *Download as zip*. A `.tar`,
`.tar.gz` or `.tgz` among the artifacts is also unpacked once into `<name>.d/` next to it, so
goldens shipped as a tarball show up as pictures. The metadata macOS `tar` slips into a tarball
(`._intro.png`, `._.`, `.DS_Store`, anything under `__MACOSX/`) is left out of `<name>.d/` and
counted as skipped (`apple_double`); a file the job itself declared is kept whatever its name.
`/shelf/latest/golden-update?branch=dev` goes to
the newest kept job of that preset and branch — bookmark it. The pages are Korean by default and
English with `?lang=en`. In the web UI a kept job's recent row shows `shelf ↗`, and the header
links to the shelf.

**The clock.** Each item has one clock: when it was last used. Opening a kept item's page,
downloading one of its files or the zip, *Restore*, and `rcm keep N` all count as use. After
`shelf_stale_days` without use the item moves to **cleanup pending**: the files stay, its page
shows only the file table, a *Restore* button and the day it will be removed, and opening that
page does not count as use. After `shelf_delete_days` without use it is deleted. *Restore* or a
download moves it back and the clock starts again. A deleted item's link answers 410 with the day
it was removed, for 30 days.

| key | default | meaning |
|---|---|---|
| `shelf_dir` | `"<data_dir>/shelf"` | the shelf folder. An absolute path or `~`; it may be on another disk. It may not be `data_dir`, a folder that holds it, or a folder inside `data_dir/artifacts`, `jobs`, `workspaces`, `blobs` or `mirrors` |
| `shelf_max_bytes` | `53687091200` (50 GiB) | the whole shelf. A keep that would go over it fails with `shelf_full` — nothing already kept is evicted. `0` turns the shelf off: existing items stay readable and follow their clock, new keeps fail with `disabled` |
| `shelf_stale_days` | `182` | days without use before an item is cleanup pending. At least 1 |
| `shelf_delete_days` | `365` | days without use before an item is deleted. Must be greater than `shelf_stale_days` |

These four are read when the server starts; change them and restart. Bytes are checked after a
copy is made, against what it really takes. A keep that would leave less than `min_free_bytes`
free on the disk fails with `disk_low`. The shelf is not part of `workspace_storage_max_bytes`,
but when it shares a disk with `data_dir` the floor rule sees the space it uses, so workspaces are
deleted earlier. Put `shelf_dir` on another disk to keep them apart.

The first time the server makes the folder it writes a `.rcm-shelf` marker in it. If the server
starts with kept items in its database and no marker in the folder — the other disk is not
mounted — the shelf goes unavailable instead of writing into the empty mount point: nothing is
kept or removed, `/shelf/` answers 503 and `rcm check` says `FAIL shelf unavailable
(shelf_dir_missing)`. A folder in the shelf with no row in the database is never deleted;
`rcm check --config` counts it as an orphan.

**Who can open it.** Reading the shelf needs no token, on purpose: it holds only the jobs you
marked, and the point is a link that opens on a phone. Once a job's files are on the shelf,
anyone on that network, and anyone who reaches this machine over Tailscale, can open them. Do not
put secrets in artifacts. `read_auth = "basic"` applies to the shelf too: then it asks for the
same password as the rest of the server. A job's files are served with a sandboxed
Content-Security-Policy and `nosniff`; only images and plain text open in the browser, everything
else (`.html`, `.svg` and `.pdf` included) downloads. Removing an item needs an admin token
(`rcm shelf rm`, or the button in the web UI). *Restore* needs no token, but only from a page of
the same server.

The shelf answers only when the request's `Host` names this machine: `localhost`, a `.local` or
`.ts.net` name, a private address (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`,
`100.64.0.0/10` for Tailscale, `127.0.0.0/8`, `fc00::/7`, `::1`), the host of `public_url`, the
bind address or the server's hostname. Any other `Host` gets 421 — that stops a web page somewhere
else from reading kept files through DNS rebinding. A request with a valid token skips this check.

**Notifications.** A notification for a kept job carries the link: `RCM_SHELF_URL` for an `argv`
rule and `shelf_url` at the top of a webhook's JSON, starting with the same address as `RCM_URL`.
It is there as soon as the job finishes, before the copy is done, so a link sent while the copy is
still pending can still fail afterwards (`shelf_full`, say); `rcm keep N` or the job's page says
what happened. A job that is not kept gets an
empty `RCM_SHELF_URL` and no `shelf_url` key, and so does a kept job whose copy already failed or
that had nothing to keep.

**Upgrading.** The shelf needs the server at v0.7.0 (database v24). Presets refuse keys they do
not know, so put `keep = true` in `server.toml` or an `include` file only after the server runs
v0.7.0 — an older server fails to reload or restart on it. `rcm run --keep` against an older
server stops before it sends anything (`server does not support --keep (rcm ≥ 0.7.0)`), and
`rcm keep` and `rcm shelf` say the same.

### Using the shelf from a script

A script that wants a link to leave in a pull request comment submits with `--keep --no-wait` and
reads two lines on stderr, the job number and the shelf link. Their wording is fixed. A tree
submission prints, in this order:

```console
$ rcm run app-map-shots --keep --no-wait
snapshot: 1834 files
…
submitted job #412 queued · 1st in line · http://macmini.local:8787/#/jobs/412
fetch its artifacts later with `rcm artifacts 412 --fetch --output DIR`
shelf: http://macmini.local:8787/shelf/412/ (after the job finishes)
{"job_id":412,"joined":false,"state":"queued",…,"shelf_url":"http://macmini.local:8787/shelf/412/",…}
```

A `git_ref` preset (`--ref dev`) has no `fetch its artifacts later` line. The last line on
stdout is JSON with `job_id` and `shelf_url`. `rcm wait --job 412` follows the job to the end.

Later, from any computer that reaches the server, `rcm shelf get` downloads the kept files again,
without a token. The paths stay as they were kept: a file kept as `build/app_map/shots.tar.gz`
lands at `<DIR>/build/app_map/shots.tar.gz`. Use `--output .` in the project folder to put the
files back where the job made them:

```sh
rcm shelf get http://macmini.local:8787/shelf/412/ --output .
rcm shelf get 412 --output . --file build/app_map/manifest.json
rcm shelf get --latest app-map-shots --branch dev --output .
```

It checks every file against the size and sha256 the shelf lists and writes nothing unless all of
them match, so a half-finished or tampered download never lands in `DIR`. If it made `DIR`
itself, it removes it again on failure. A link to another server is fine; your token goes only to
the server you configured, and a redirect is not followed. It ends with
`wrote 4 files to . from http://macmini.local:8787/shelf/412/`. Exit 0 means you have the files,
1 means the item is not there (never kept, removed, or its copy failed), 2 a usage error or an
older server, 3 that the server could not be reached or answered 503, the copy is still running,
or what arrived did not match.

Without rcm, the same information is JSON (quote the URL — `?` is a pattern in zsh):

```sh
curl "http://macmini.local:8787/api/shelf/latest/app-map-shots?branch=dev"   # the newest item and its files
curl "http://macmini.local:8787/api/shelf/412"                               # one item
```

Each file in it has `path`, `url`, `sha256`, `size`, `type` and, for a file unpacked from a
tarball, `expanded_from`.

Put `keep = true` on a preset only after the server runs v0.7.0. An older server refuses the key
on reload and on restart, and so does an older dolomood `check_presets.py`; until then pass
`--keep` on the command line.

## Making a failure explain itself

rcm stores everything a job writes to stdout and stderr, uncut. What it cannot do is invent output
the script never printed — and the usual gate script hides exactly the part you need. A run that
sends its heavy step to a temporary directory and prints the last forty lines on failure leaves a
50 KB log with no `Expected:`, no `Actual:` and no stack trace: the forty lines were a progress
bar. The fix is in the preset, and it has two halves.

**Print the verdict, so it lands in the log.** The log is the evidence of record: it lives for
`retention_days_failure` (30 days), it is what `rcm logs` and the web page show, and it is what
somebody reads on a phone. A few lines naming the failing test and its diff are worth more than
forty lines of progress.

**Leave the bulk in the workspace and declare it**, so a session can fetch it. Anything written to
`TMPDIR` is gone the moment the job ends.

```bash
# bad — the evidence disappears with the temporary directory
log=$(mktemp -d)/test.log
flutter test > "$log" 2>&1 || { tail -5 "$log"; exit 1; }

# good — the bulk stays in the workspace, the verdict goes to the log
mkdir -p .rcm/logs
flutter test > .rcm/logs/test.log 2>&1 || {
  echo "::rcm::summary::2 tests failed"
  grep -A3 -m5 -E '^(Expected|Actual|#[0-9])' .rcm/logs/test.log
  exit 1
}
```

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
artifacts = [".rcm/logs/*.log"]
artifacts_on = "failure"      # "always" (default) collects on every run
```

`artifacts_on = "failure"` collects only when the job did not succeed, which is what makes this
pattern affordable: a green run has nothing anyone wants, and paying for it on every build is what
stops people following the advice. Cancelled and timed-out jobs count as failures; a `lost` job is
never collected.

**A bundle is a way to fetch, not a place to keep.** It lives `artifact_retention_hours` (24) —
about as long as a workspace, and far less than a log. Put what you will want next week in the log
and what you will want in the next hour in the bundle.

### Required tools

A gate that cannot find `fvm` does not stop — it falls through to whatever `flutter` is on the
path, builds with the wrong SDK, and comes back green. `requires` names the tools the job must
find, and the job does not start without them:

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
requires = ["fvm", "gitleaks", "/opt/homebrew/bin/gh"]   # names or absolute paths
```

Right before the process starts — on the local lane or on a remote worker alike — rcm looks each
entry up in the **environment the job will actually run in**: the `env_passthrough` allowlist,
then `[presets.env]`. A relative path, an empty entry, a duplicate, or an absolute path that does
not end in a tool name (`/opt/bin/`) is a config error at start; without the key nothing changes.
If the job's environment has no `PATH`, the check uses an empty one, never the server's own. A
relative `PATH` entry (`tools`, `.`, or an empty entry between two colons) means the **job's
workspace**, because that is where the process starts — a tool that only exists next to the
server's own working directory does not count. When everything is found the job log gets one
line, `[rcm] required tools: fvm ok · gitleaks ok`; when something is missing the job does not
run and ends `failed` with `summary_code: "tool_missing"` and `summary_args: {"tool": "fvm"}` —
the name only. No `PATH` and no path appears in the job document, the queue or the log: an entry
declared as `/opt/homebrew/bin/gh` shows up everywhere as `gh`. A `tool_missing` failure carries
no `failed_step`, no `last_step` and no `failed: …` line: the tool was missing before the script
could say anything. A job cancelled while its workspace was still being prepared ends `cancelled`,
not `tool_missing` — the check runs only for a job nobody has stopped.

**When a job never starts.** `tool_missing` is one of a family of failures that happen before the
process exists, and the code says which one — because the thing to fix is different each time.
The workspace could not be built: `snapshot_missing` (the server has no snapshot for this job),
`snapshot_rejected` (the archive was refused; the argument names the reason and the member),
`blob_missing` (retention had already deleted a file the snapshot needs), `repo_missing` (the
repository left `[[repos]]` — `where: "worker"` means it is the worker's config, not the
server's), `commit_missing` (the ref moved or was force-pushed), `git_failed` (a fetch or
checkout timed out or exited non-zero) and `snapshot_download_failed` (a remote worker could not
download it). The process could not be launched: `launch_executable_missing`,
`launch_permission_denied`, `launch_failed` and `log_unavailable`. The config changed while the
job waited: `preset_missing`. And `requires` was not satisfied: `tool_missing`. Anything else
that stopped the workspace from being built — a manifest the server could not read, a disk that
filled — is `workspace_failed`, with the exception class as its argument. All of them end
`failed` with `exit_code: null`, no `failed_step`, no `last_step` and no `failed: …` line, and
all of them lose to a cancel that arrives while the workspace is being prepared — on a remote
worker as well as a local lane. A remote worker reports from the same table; a worker still
running an older build than the server is the exception, because the version is compared when it
registers and not on every report, so its jobs keep arriving with a sentence and no code until it
is restarted.

These summaries carry **no free text**. `summary_args` holds only what the code declares — an
exception class name, a git operation and exit code, a hex sha, a repository or preset name that
is already in the job document — because `/api/status` is readable without a token in the default
configuration. The original text (`argv[0]`, the git stderr, the exception) is written to the job
log, which always needs a token; a remote worker sends it to the same place. The one exception is
`log_unavailable`: the log file is what could not be opened, so there is nowhere to write it, and
the code itself is the diagnosis. A rejected archive member keeps its **name** (`escape.txt` from
`../escape.txt`) — it came from the snapshot you sent and it is what you need to fix it.

**The launchd trap.** A service started by `launchd` (or `systemd`) has a short `PATH` —
`/usr/bin:/bin:/usr/sbin:/sbin` — so `fvm` and `gitleaks` from Homebrew are found in your shell and
not in the job, and `requires` is what makes that visible. The fix is a `PATH` the job owns:

```toml
[presets.env]
PATH = "/Users/build/fvm/default/bin:/opt/homebrew/bin:/usr/bin:/bin"
```

`rcm check --config server.toml` has a `local preset tools` row that runs the same lookup for every
preset that declares `requires` — in **this shell**, with this shell's environment. A tool this
shell cannot find makes the row FAIL and `rcm check` exit 1. It catches a typo and a missing
install; it cannot vouch for the service, whose `PATH` is not yours — an `ok` here says nothing
about the job. The check that counts is the one at job start. The row names each entry the way
the job log does — by its basename, never by the path you declared.

A `placement = "any-capable"` preset is listed in both rows with `any machine` and never fails
them: it goes to a machine that has its tools, so a tool missing here is expected. Whether some
machine can run it is the `placement` row's question.

**The row below it does vouch for the service.** `service preset tools` is the same two questions
asked by the server, in the environment the service actually has, and answered once when the
server starts:

| question | what a FAIL means |
|---|---|
| can the job's `PATH` find every `requires` entry? | the tool is installed for you, not for the service |
| is every `env_passthrough` name present in the service's environment, or set by the preset's own `[presets.env]`? | the job gets nothing for that name, however it looks in your shell |

The second question is the one a shell cannot answer. `env_passthrough = ["JAVA_HOME"]` passes a
variable the service does not have, and nothing says so: the preset is valid, the tool lookup is
green, and the job silently receives an empty value. The row prints `gate (JAVA_HOME unset)` and
the server log says the same at start, next to `preset env`. The fix is a value the job owns — add
it to `[presets.env]`, or put it in the service unit (`EnvironmentVariables` in a launchd plist,
`Environment=` in a systemd unit) and restart. A name the preset sets in `[presets.env]` is
not missing, even if it is also listed in `env_passthrough` and absent from the service: the job
receives the preset's value, and the row says `ok`.

A server too old to answer draws no row at all. An empty value counts as present: a unit that
deliberately sets `JAVA_HOME=""` meant it.

The row carries **names, never values**. `/api/health` is readable without a token in the default
configuration, so it says `JAVA_HOME` and never what `JAVA_HOME` is, and a tool declared as an
absolute path is named by its basename — the same rule the job log and `local preset tools` follow.

### Install hints: `[[tools]]`

`requires` says a tool is missing; `[[tools]]` says how to get it. When `rcm doctor` finds a tool
missing on a machine, it prints the hint for that machine's OS next to it, with the docs link:

```toml
[[tools]]                       # shown, never run
name = "fvm"                    # the basename in requires, or a probe's argv[0]
docs = "https://example.org/fvm/install"
[tools.install]                 # darwin · linux · any (used when the OS has no line)
darwin = "brew install fvm"
linux = "see the docs"
```

The text is shown as written. rcm never runs it and never installs anything with `sudo`; whether
the text is right is up to whoever runs the coordinator. Each string is one line of at most 300
characters without control characters (line and paragraph separators, bidirectional controls
and zero-width characters count too), the install keys are `darwin`, `linux` and `any`, and a
name appears once, has no spaces around it and is at most 64 characters (the length `rcm doctor`
matches names at). Anything else is a config error at start. An older server does not know the
table and refuses to start, so remove it before rolling back below 0.11.0. A name that no preset
`requires` and no probe runs is a warning at start and at `rcm reload`, because its hint would
never show.

Workers get the table with the rest of the spec (`GET /worker/spec`, a worker token). It is not in
`/api/status` or `/api/machines`, which may be readable without a token. A worker strips control
characters once more before it prints a hint.

**A toolchain catalog to start from.** The server template (what `rcm init server` and
`rcm setup` write, and `examples/server.toml`) ends with a commented toolchain catalog:
`[[probes]]` and `[[tools]]` entries for Flutter, iOS (Xcode and CocoaPods), Android (JDK and the
SDK's `sdkmanager`), Node and Python, each with notes on what trips a service up. Lines starting
with one `#` are config: remove the `# ` of the entries you want. Lines starting with `##` are
notes. Uncommented as a whole, the catalog passes the start checks, and every hint in it belongs to
a probe, so none warns. None of these names is built into rcm, and the lines were tried on one
Apple Silicon Mac only: check each on your machines. Version rules ("Xcode 27 or newer") belong in
a [check preset](#proving-a-machine-works-checks), since rcm compares no versions. macOS's
`/usr/bin/java` is a stub that rcm's Command Line Tools rule does not recognise, so give a Java
preset a JDK of its own (`JAVA_HOME` and `PATH` in `[presets.env]`).

### Naming what failed

The `failed: …` lines, `failed_step` and the history above come from one place: the
`::rcm::fail::<name>` and `::rcm::step-end::fail` markers your script prints. rcm never parses the
rest of the output, and there is no pattern list to configure — deliberately. A pattern turns every
mention of `FAIL` into a verdict, and the day the output format changes it records the wrong name
with full confidence. A gate that prints no markers leaves the failure ledger empty, by design:
`last_step` still says where the job was, and `rcm logs` still has everything it printed.

**Print the marker from the function that decides.** The place that knows something is red is the
place to name it, and one helper covers every check in the script:

```bash
status=0
fail() {                   # the one function that decides something is red
  echo "FAIL: $1"
  echo "::rcm::fail::$1"   # the same name, for rcm
  status=1
}

echo "::rcm::step::analyze"
dart analyze || fail "analyze"
echo "::rcm::step::test"
for f in test/*_test.dart; do
  flutter test "$f" > ".rcm/logs/$(basename "$f").log" 2>&1 || fail "$f"
done
exit $status
```

**When the script cannot be changed, wrap it.** [`examples/preset/name-failures.sh`](../examples/preset/name-failures.sh)
runs one command, passes its output through untouched, and once the command has finished prints
`::rcm::fail::<name>` for every line that matched the exact format the script already prints —
`^FAIL: (.+)$` by default, `--pattern` for another — then closes the step with the command's own
exit code, which is also the wrapper's:

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/name-failures.sh", "--step", "test", "--", "bash", "scripts/gate.sh"]
```

It never guesses: a command that fails without printing a matching line names nothing beyond the
step. Read the header of the file before copying it. It is bash only (`PIPESTATUS`); it merges
stderr into stdout, as rcm does; and a program that block-buffers when its stdout is a pipe delivers
its lines late and in bursts (`stdbuf -oL`, `PYTHONUNBUFFERED=1`, or the tool's own flag). Match
the exact line your script prints, anchored at the start of the line — a loose `FAIL` anywhere
turns a mention into a verdict. `tests/test_examples.py` locks the wrapper by feeding its output to
the same code the server reads markers with.

## Estimates, and when a job is called not responding

Every ETA on the page comes from the same place: the median of this preset's own finished runs.
`[estimate]` says how that median is built (`sample_days`, `sample_policy`, `min_job_seconds`,
`min_samples`, `default_seconds`, `floor_remaining_seconds` — each with a comment in
`examples/server.toml`) and when the server stops believing a running job is alive.

Silence is not that moment. A job that has stopped printing has not necessarily stopped working:
a preset whose last step runs nine test shards, gitleaks and a web build in parallel says nothing
for minutes and is perfectly healthy, because those tools buffer their output until they finish.
The server decides in this order, and the screen names only the rule that fired:

1. **The current step against its own history.** For the last successful runs of the same preset
   the server reads the step markers those runs already wrote, takes the median time of the step
   the job is in now, and calls the job not responding once the step has run longer than
   `step_stuck_multiplier` times that median. A step median counts only once `step_min_samples`
   runs have measured it — a step nobody has measured yet says nothing either way.
2. **The whole job against its estimate.** `elapsed > stuck_multiplier × expected`, unchanged.
3. **Silence, and only for a job that prints no step markers at all.** With no steps to look at,
   `no_output_seconds` of silence is the only signal there is, so there it is the verdict.

The step threshold never drops below `no_output_seconds`: it is
`max(step_stuck_multiplier × step median, no_output_seconds)`. Without that floor a step whose
median is half a second would be called dead a second and a half in — a claim the silence rule
could never have made.

A job that is stepping along normally and has merely gone silent is **quiet**: grey on the page,
never red, and deliberately not in the "needs a look" summary, which lists only what a person can
act on. A job that has no history yet is quiet too, not red, until its steps have been measured.

| key | default | what it is |
|---|---|---|
| `stuck_multiplier` | `3` | how many times its estimate the whole job may run before it is called not responding |
| `step_stuck_multiplier` | `3` | how many times its own measured median the current step may run |
| `step_min_samples` | `3` | successful runs a step needs before its median is trusted. Higher than `min_samples` (2) on purpose: step times are noisier than job times |
| `no_output_seconds` | `240` | silence this long makes a job quiet. It is also the floor under the step threshold, and the verdict for a job that declares no steps at all |

`step_stuck_multiplier` must be greater than 1 and `step_min_samples` at least 2; `rcm serve`
refuses to start otherwise. Nothing here changes the status document's `schema_version` — the
three keys `estimate.quiet`, `estimate.stuck_code` (`over_step`, `over_elapsed`, `no_output`) and
`estimate.step_expected_seconds` were added, and adding keys is free.

## Retention: what is kept, and for how long

A finished job leaves two very different things behind, and they are worth different amounts:

| | typical size | what it is | how long it lives |
|---|---|---|---|
| the log (`jobs/<id>/log.txt`) | **50 KB** | the evidence — why it broke | `retention_days_success` (14) / `retention_days_failure` (30) |
| the workspace, and the snapshot it was unpacked from | **720 MB** | the bulk — the folder the job ran in | `workspace_retention_days` (1) |

Giving both the same clock is what fills a disk: fifty failing jobs a day at half a gigabyte each
needs 750 GB to reach a thirty-day limit. So the bulk keeps its own, much shorter clock, and two
byte rules catch it before any date does.

| key | default | meaning |
|---|---|---|
| `retention_days_success` | `14` | logs of succeeded jobs. Their workspace is deleted the moment the job ends |
| `retention_days_failure` | `30` | logs of failed, cancelled, timed-out, lost and expired jobs |
| `workspace_retention_days` | `1` | a kept workspace and the job's uploaded snapshot. Cannot outlive the shorter of the two day counts above — set it higher and the server says so at start-up and uses the lower number |
| `workspace_storage_max_bytes` | `107374182400` (100 GiB) | when the workspaces plus snapshots weigh more than this, the oldest finished jobs give theirs up regardless of age. `0` means no limit |
| `min_free_bytes` | `10737418240` (10 GiB) | when the filesystem holding the data directory drops below this, the same thing happens. `0` turns it off |
| `metadata_retention_days` | `180` | job rows and events, deleted only after the job's files are gone. Must be ≥ `estimate.sample_days` |
| `retention_sweep_interval_seconds` | `3600` | how often the sweep runs. It also runs once at start-up |

Two things this never does. **It does not delete evidence to make room**: under any pressure the
server gives up a 720 MB workspace, never a 50 KB log, a job row, an artifact bundle or a snapshot
blob — those keep their own clocks and budgets. And **it does not delete on a guess**: if a size
cannot be measured, the byte rules are skipped for that sweep and the reason is reported; only the
day rule, which never needed a size, keeps running.

**Store version drafts ride the same timer, under their own rule.** The sweep also looks at the
`versions` table on every cycle and once at start-up, but none of the keys above applies to it:
a draft nobody has edited goes when `version_ttl_hours` (the release profile, default 24) has
passed since it was opened — through the `version` preset's `mode = delete` job when the profile
has one, so the App Store version goes with it, and inside rcm alone when it does not, or when
the draft's create job failed and the store never had anything. A draft that *was* edited is
**never** deleted for you: it is marked expired, the server logs one line, and it waits for a
person to discard it. Submitted drafts, drafts still being created and drafts whose review job,
upload job or driver round is running are left alone, and a store delete job is submitted at most
once per draft. `rcm gc` does not touch the table at all.

`min_free_bytes` is a target, not a guarantee. Deleting does not always give space back — a macOS
local snapshot or an open file can hold the blocks — so if a sweep deletes and free space does not
move, the floor rule pauses itself and `rcm check` says so rather than deleting everything for
nothing.

**Before you upgrade**, see what the new defaults would remove on your machine. Run it from the
new build: it reads the config and the data directory, plans on a temporary copy of the database
(the live one is opened read-only and is not migrated), deletes nothing, and does not need the
server. If the copy cannot be made or migrated it exits 3 — unknown, not "nothing to do":

```sh
rcm gc --dry-run --config ~/.config/rcm/server.toml
```

```
job     workspace   snapshot   reason
#118       1.7 GB          —   age
#131       0.8 GB     0.0 GB   budget
would free 1.9 GB from 2 jobs (2.5 GB charged · 0.6 GB shared by hard links) · 28.9 GB would remain
```

**Hard links are charged but not reclaimable.** A workspace made from a `git_ref` source is a
local clone of the server's mirror, and git hard-links the pack files into it, so the same blocks
are counted once for the mirror and once for every workspace — on one real machine a third of all
workspace bytes. The accounting therefore keeps two numbers. What a workspace is **charged** counts
every link: that is what the table shows and what `workspace_storage_max_bytes` is measured
against, so the budget errs on the safe side. What deleting it would **reclaim** is the charged
bytes minus the blocks that are hard-linked elsewhere: `would free` is that estimate, the
free-space floor plans with it, and so does the no-progress check. It is a lower bound — a link
that lives entirely inside one workspace cannot be told apart — which is why the field is named
`estimated_reclaimable_bytes`. `would remain` is what the inventory would still charge.

`rcm gc` (admin token) runs the same plan for real against a running server, and reports what it
planned, what it deleted and what failed separately — a plan is not a receipt. After deleting it
measures the inventory and the free space again, so its `… left` figure and `free … → …` are what
the disk said afterwards, not the plan. That re-measure also follows a delete that only half
succeeded: a job's workspace is removed first and its snapshot tar second, and when the second
step fails the entry in `failed[]` says what did go (`"removed": ["workspace"]`, empty when nothing
went), the receipt counts it as `1 failed (EACCES · 1 partly deleted)`, and the numbers are read
from the disk as it is now — the next sweep retries the rest. A job whose size could not be
measured can still be deleted by the age rule; the receipt then gives a lower bound instead of a
figure — `freed ≥ 1.2 GB from 3 jobs (1 of unknown size)`, with `unknown_count` in the JSON —
never `freed 0 B` as if it had been measured. A `gc` that outruns its `--timeout` (600 s) exits
**3, unknown**, not failure: the server may still be deleting, so run the dry run again to see
what is left.

The same numbers are on `/api/status` under `server.job_storage`
(`volume_bytes = workspace_bytes + snapshot_bytes = evictable_bytes + non_evictable_bytes`, plus
`shared_bytes` and `estimated_reclaimable_bytes`), as one line in `rcm check`, and under the disk
meter on the web host card; `/api/health` carries the totals and the two flags under `storage`
(`volume_bytes`, `free_bytes`, the limits, `budget_unreachable`, `no_progress`) but not the
shared/reclaimable split or the measurement time. What cannot be measured reads `—`, never `0`,
and a size that could not be measured is reported as exactly that — the `rcm check` line says
`a size could not be measured … (measure_EACCES)` even when free space is under the floor, rather
than "nothing left to delete", and even after the floor rule has paused itself (`no_progress`),
rather than "deleting stopped helping" — those verdicts rest on numbers the sweep did not get. The
line also says how old the number is — `rcm data 30.9 GB ·
measured 57m ago` — in every state that shows a figure, including the one where running jobs alone
exceed the budget: a finished workspace is measured once and remembered for up to a day, and the
age shown is that of the **oldest** measurement in the total, so a cached figure is never presented
as fresh.

Git mirrors are never pruned.

## Priority, snapshot cache and notifications

- **Priority** — three levels, `low` · `normal` · `high`. `rcm run gate --priority high` starts
  before waiting normal jobs (queue order is priority, then age). A preset can set its default
  (`priority = "high"`); without an admin token a session can lower but not raise a job above the
  preset default. Admins reorder waiting jobs with `rcm bump N --priority high`. While `high` jobs
  keep arriving, `normal` jobs wait — the queue shows it; nothing hides it. A joined submission with
  a higher priority raises the existing job.
- **Snapshot cache** — the server keeps uploaded files by content hash (`snapshot_cache = true`,
  default). `rcm run` sends a manifest first and then only the files the server does not have:
  the second upload of a mostly unchanged tree transfers a few percent of it. Blobs unused for
  `snapshot_cache_days` (30) or beyond `snapshot_cache_max_bytes` (4 GiB) are purged; blobs
  referenced by active or scheduled jobs never are. Set `snapshot_cache_scope = "token"` to keep each client's
  blobs separate (by default identical content is shared between clients, which also means a
  client can learn whether a given file already exists on the server). `--no-cache` sends a full
  tarball; combine it with `--no-join` to force a fresh job for an unchanged tree.
- **Pools** — a job runs in a worker pool. The local worker is pool `default`; a preset can
  declare `pool = "linux"` (its default) and `pools = ["default"]` (extra pools a session may pick
  with `--pool`). Jobs of a pool with no workers wait with reason `worker_down` and no ETA —
  nothing pretends they will start.
- **Remote workers** — another machine serves a pool by talking to the server with a worker token
  (`rcm token add build-02 --worker`; worker tokens can only use `/worker/*`, never submit or
  cancel). The worker registers, claims one queued job per lane, downloads the snapshot, streams
  the raw log (the server parses step markers) and reports the outcome; a heartbeat every
  `worker_heartbeat_seconds` (5) keeps it alive. If the server hears nothing for
  `worker_timeout_seconds` (60) the worker shows `down`; its running jobs keep running and become
  `lost` (`worker build-02 unreachable for 601s`) only after the
  [disconnect grace](#a-lost-connection-disconnect_grace_seconds) (600 s by default). A worker that
  comes back inside the grace goes on reporting, and the job ends with its real result. `rcm top`
  and the web header show each
  remote lane as `build-02/1 busy #511`; `pools[].lanes` counts only live workers. The worker
  process itself (`rcm worker`) ships in the next release step (M5b-3).
- **Notifications** — `[[notify]]` rules run a command (`argv`, no shell) or POST JSON to a `url`
  when jobs finish, filtered by state (`on`; without it every final state but `retried`, `expired`
  and `exhausted` included) and
  preset (`presets`). The command gets
  `RCM_JOB_ID`, `RCM_STATE`, `RCM_PRESET`, `RCM_KEY`, `RCM_REQUESTER`, `RCM_SUMMARY`,
  `RCM_FAILED_STEP` (declared failures only) and `RCM_LAST_STEP`, `RCM_EXIT_CODE`,
  `RCM_JOB_SECONDS`, `RCM_URL`, `RCM_NOTIFY` (rule name), the
  source (`RCM_SOURCE_MODE`, `RCM_SOURCE_REF`, `RCM_SOURCE_SHA`, `RCM_SOURCE_BASE_SHA`,
  `RCM_SOURCE_DIRTY`, `RCM_SOURCE_REPO` — enough to post a commit status), `RCM_INPUTS` (JSON),
  `RCM_FAILURES` (the names the job reported with `::rcm::fail::` and failed steps, in the order it
  printed them, one per line, at most 20) and `RCM_FAILURE_COUNT` (how many there are in all —
  `100+` when the job reported more than the ledger keeps; both empty when nothing was named),
  `RCM_SHELF_URL` (a kept job's [shelf](#keeping-a-jobs-files-the-shelf) link, empty otherwise),
  `RCM_ACTION_CODE` and `RCM_ACTION_NOTE` (the job's last
  [action](#when-a-person-has-to-act), empty without one),
  `RCM_MACHINE` (where the job ran: the remote worker's name as it claimed the job, or this
  server's current host name for a local lane; empty for a job that never started or for a local
  lane when this server does not know its own name — the job's `ran_on`),
  `RCM_EVENT` (`job_finished`), and `RCM_HOLD`, `RCM_HOLD_GROUP` and `RCM_HOLD_NOTE` (`1`, the
  group and `group held — run rcm release-hold <job>` when the job's close
  [held its group](#jobs-that-must-not-run-twice-irreversible); `0` and empty otherwise);
  the hook also inherits `PATH`, `HOME` and `LANG`;
  user strings are sanitised and capped at 4 KB (`RCM_FAILURES` drops whole lines to fit). A `url`
  rule POSTs `{"notify": <rule>, "event": "job_finished", "job": <the finished job>, "hold":
  {"held": false, "group": "", "note": ""}, "failures": [...], "failure_count": N,
  "failures_truncated": false}` with the same 20-name cap, plus `"shelf_url"` for a kept job. Each (job, rule) fires exactly once,
  including jobs that finished while the server was down. `on` also takes `irreversible_hold`,
  which is never part of the default: such a rule is called once for each new group hold, with
  `RCM_EVENT=irreversible_hold`, `RCM_HOLD_CAUSE`, `RCM_HOLD_WORKER` (empty for a local lane), a
  one-line `RCM_SUMMARY` and, for a `url`, `"hold"` with `scope`, `cause`, `worker`, `since` and
  `release_command` added. A hold released before the call is not announced. Failures are logged and counted
  (`server.notify_failures`) but never retried, and never mark the queue unhealthy. `rcm check`
  shows a `warn  notify` row with the count while it is above 0, and the web header a
  `notify failures N` badge (hidden at 0). The count lives in the server's
  memory, so a restart sets it back to 0 — the server log keeps each failure
  (`notify <rule> #<job>: exit 1`).
- **Job links** — `RCM_URL`, the `url` in the webhook body and the job URL `rcm run` prints start
  with `[server] public_url` when it is set. Without it the server picks an address another
  computer can open, never `0.0.0.0`:

  | `bind` | link |
  |---|---|
  | a specific address (`100.64.0.5`, `127.0.0.1`) | `http://100.64.0.5:8787` |
  | `0.0.0.0`, `::` or empty, `advertise` on (the default off loopback) | `http://<host>.local:8787` — the name the server answers for over mDNS |
  | `0.0.0.0`, `::` or empty, `advertise = false` | `http://<hostname>:8787` |

  A request that reached the server under another name (`rcm run` against a Tailscale name) still
  gets that name back. `rcm check` shows the link it will use and warns while `public_url` is
  empty: `warn  public url    empty — links use http://macmini.local:8787 (set [server]
  public_url)`. Set `public_url` when other computers reach the server under a name of its own
  (a Tailscale name, a reverse proxy).

## Parallel lanes without overloading the machine

`[server] lanes` is how many jobs the build machine runs at once (`0` makes it a server that only
hands jobs out — see [A server that only hands out jobs](#a-server-that-only-hands-out-jobs-lanes--0)).
Raising it used to be a gamble:
two heavy jobs together and the machine crawls. Now lane 2 and above only pick up a job while the
host CPU is below `cpu_max_percent`.

```toml
[server]
lanes = 2
admission = "load"                 # "always" turns the gate off (the pre-0.2.6 behaviour)
cpu_max_percent = 80               # lanes 2+ wait while CPU is above this
admission_samples = 3              # this many host samples in a row must be under the cap
admission_cooldown_seconds = 30    # after a gated lane starts a job, that machine waits this long
```

**Lane 1 is never held.** Whatever the load, every machine keeps one lane that takes work, so the
queue always moves. That is also why there is no "give up and start anyway" timer: nothing starves.

The gate reads the same CPU number the host card shows — the whole machine, not one core. It needs
`admission_samples` consecutive samples under the cap, and they have to be *consecutive in time*:
after a sampler outage the window is refused rather than trusted. **If the CPU is unknown, the lane
closes.** A missing, stale or broken sample never opens a lane.

`rcm top` counts held lanes in its header (`lanes 1/2 busy · 1 held (cpu 92%)`), the queue row of a
job waiting on one says `held by load`, and `rcm check` reports them without failing — a held lane
is the feature working. It does warn if a lane has been held for more than five minutes, which
usually means something outside rcm is using the machine.

Two things worth knowing:

- **The first 15 seconds after `rcm serve` starts, lanes 2+ are closed.** The sampler has not
  produced `admission_samples` samples yet, and an unknown load closes the lane.
- **Jobs that use the same `[[repos]]` entry serialise while they fetch.** The git mirror is shared
  and one lane fetches at a time, so extra lanes do not speed up the materialize phase for jobs on
  the same repository — only the run itself.

Running `rcm serve` and `rcm worker` on one machine gives that machine **two** ungated lanes, one
per process, and two independent cooldowns. They both read the true CPU and both hold correctly;
what they cannot do is coordinate. Keep it in mind when you set `lanes` on both.

### Two lanes for a gate with a light phase and a heavy phase

A gate often spends its first minutes on checkout, `pub get`, codegen and lints at 20–50 % CPU and
only then hits the heavy part (tests, web build). With `lanes = 1` the next job waits through the
light phase for nothing. A second lane lets the next job's light phase overlap the current job's
heavy phase — **but only if the heavy phase is serialised by the script itself**:

```toml
[server]
lanes = 2
admission = "load"

[[presets]]
name = "gate"
argv = ["/bin/bash", "scripts/local_ci.sh"]
# no concurrency_group — two gate jobs may now run at once
```

```sh
# scripts/local_ci.sh — the heavy section takes a machine-wide lock.
# `flock(1)` is not on macOS; Python's fcntl.flock on an inherited fd works on macOS and Linux
# and the lock stays with the shell's fd 9 until the script exits. If the lock cannot be taken
# the script must stop (`|| exit 3`) — a heavy section that runs unlocked is exactly what this
# section exists to prevent, and `local_ci.sh` without `set -e` would carry on past the error.
exec 9>/tmp/gate-heavy.lock || exit 3
python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX)' || exit 3   # waits while another gate job is in its heavy section
flutter test ...
```

To see that the lock holds, submit two `gate` jobs back to back and compare the heavy steps in
their `step_timeline` (or the timestamps in their logs): the heavy sections must not overlap.

The lock is machine-wide: lanes of `rcm serve` and of an `rcm worker` on the same machine share it,
which is what you want. The fd stays locked in child processes the script starts, so the heavy
section may be a whole sub-script.

What the admission gate does and does not do:

- It is decided **once, when a lane picks a job up**. Two jobs that are already running can both
  enter their heavy section; the CPU cap does not stop that. The lock in the script does.
- The overlap you actually get: while job A is in its light phase, job B is admitted and both
  light phases run together; while A is in its heavy phase (CPU near 100 %), lane 2 stays closed
  and B waits at the gate — so the win is the light phases overlapping, not B's light phase running
  under A's heavy one.
- Removing `concurrency_group` removes the server's own guarantee that two `gate` jobs never
  overlap. Do that only when the script holds a lock like the one above; otherwise keep the group.
- There is no memory-based admission on purpose: macOS and Linux disagree about what "used" means
  (PLAN decision 42), so the gate reads CPU only. If memory pressure is your limit, keep
  `lanes = 1` or narrow the heavy section.
- ETA estimates assume the lanes are independent. While two jobs overlap, the medians drift; the
  `concurrent_at_start` field on a finished job's document (`GET /jobs/<id>`, `rcm wait --json`)
  says how many jobs were running when it started — itself included, so a job that ran alone
  reads `1` and `null` means unknown — so the effect can be measured afterwards, and the same
  document's `step_timeline` shows which step paid.

## Second build machine (remote worker)

A preset can run on another machine, either by naming that machine's pool (`pool = "linux"`) or
by letting any machine that can do the job take it (`placement = "any-capable"`, next section).
That machine runs `rcm worker`, which talks to the server outbound only (it works from behind NAT;
the server never connects to workers). The short way to add one:

```sh
# on the server
rcm token add mbp --worker        # prints the secret once, then the line to type on the worker:
                                  #   RCM_WORKER_TOKEN=<token above> rcm worker --check
# on the new machine (rcm 0.8.0 or newer follows the server's version; older: the same release)
rcm init worker                   # ~/.config/rcm/worker.toml (mode 600) with server = "auto"
export RCM_WORKER_TOKEN=<that token>
rcm worker --check                # server · token kind · pool · fingerprint · presets
rcm init worker --service-file ~/Library/LaunchAgents/com.remote-ci-monitor.worker.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.remote-ci-monitor.worker.plist
```

`rcm init worker [--server URL|auto] [--pool P] [--lanes N] [--name N] [--force]` writes
`worker.toml` from the packaged template (the same file as `examples/worker.toml`). `server`
defaults to `"auto"`, `pool` to the machine's short host name and `lanes` to 1; an existing file
is kept unless you pass `--force`. `--service-file PATH` writes only the service file — a launchd
plist on macOS, a systemd user unit on Linux, the same shape as
`examples/launchd/com.remote-ci-monitor.worker.plist` and `examples/systemd/rcm-worker.service`.
It runs this `rcm` by its absolute path with
`PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` (never the venv's `bin`
first — see [operating](operating.md#run-as-a-service)). It starts nothing: it prints the
`launchctl bootstrap …` or `systemctl --user enable --now …` line to run next. Neither command
writes the token anywhere; carrying it to the worker is still a person's job. A service does not
see the `RCM_WORKER_TOKEN` of your shell, so before you start it put `token = "…"` in
`worker.toml` (the file is mode 600).

`rcm setup --role worker --server URL --pool P --lanes N --yes` does both steps at once: it writes
`worker.toml` with that URL (never `"auto"`) and the service file in `service/` next to it,
prints the copy and start lines instead of running them, and ends with
the checks that need no server. See [Set a machine up](operating.md#set-a-machine-up-rcm-setup).

Without `--config`, `rcm worker` reads the `worker.toml` that `rcm init worker` wrote
(`~/.config/rcm/worker.toml`, or under `$XDG_CONFIG_HOME`) when it exists, and says which file it
read. The long form still works, with flags instead of a file — given `--server`, it reads no file
unless you pass `--config`:

```sh
rcm worker --server http://macmini:8787 --pool linux --lanes 1 --check   # server · token kind · pool
rcm worker --server http://macmini:8787 --pool linux --lanes 1           # Ctrl-C or SIGTERM stops it
```

**Finding the server.** With `server = "auto"` (or an empty `server`) the worker looks for the
server on the network over mDNS, as `rcm discover` does. One server found: that one. Several: the
one whose advertised name equals `server_name` in `worker.toml`. None, or still several, and the
worker prints the names it saw and stops with exit 2 — set `server_name = "<name>"` or a fixed
address. After **5 consecutive connection failures** on register or heartbeat an `auto` worker
looks again, so a server whose address changed (DHCP) is found without restarting the worker. A
fixed `server = "http://…"` or `--server` is never looked up. `rcm worker --check` follows the same
rules, and also prints two rows that never fail the check: `fingerprint` (what the
[probes](#environment-fingerprint-probes) return here) and `presets` (how many presets this
worker can run, and the top reasons for the rest), both measured on the spot in this process's
`PATH`.

The worker registers, measures and reports its environment, then claims queued jobs per lane,
downloads the snapshot (or fetches the `git_ref` from its own `[[repos]]` — keep them in
`worker.toml` and pass `--config`), streams the raw log to the server, which parses the step
markers, and reports the outcome. `rcm top` and the web header show it as `build-02/1 busy #511`;
its host sample appears under that pool. If the server hears no heartbeat for
`worker_timeout_seconds` (60) the worker shows `down`. Its running jobs keep running for the
[disconnect grace](#a-lost-connection-disconnect_grace_seconds) and only then become `lost`
(`worker build-02 unreachable for 601s`) — resubmit then. Each heartbeat lists
the jobs the worker is running; a job the server handed it that is missing becomes `lost`
(`worker build-02 restarted without the job`), but only once the heartbeat says the worker has
seen that job's claim answer. Stopping the worker
reports its running jobs as `lost` (`worker stopped`). `worker.toml` keys: `server` (a URL, or
`"auto"`), `server_name`, `token` (or the env var), `pool`, `lanes`, `name`, `accept_any`,
`data_dir`, `grace_seconds`, `keep_workspace_on_failure`, `[host]` (sampler) and `[[repos]]`. See
`examples/worker.toml`. `rcm worker --once` runs at most one job and exits (cron, tests).

**Following the server's version.** A worker installed from a wheel runs the server's version
of `rcm`, as a session does. When it starts, it asks the server for its version (always afresh)
and, when they differ, fetches the server's wheel and starts that version in the same process.
When the server refuses its registration for the version, it does the same once more; when that
fails it stops with exit 2 and says why. A running worker that sees another version in a
heartbeat finishes its jobs and restarts the same way, without losing one. `RCM_CLIENT_VERSION`, the `client-version` file and
`rcm worker --no-follow` pin the version or turn this off, and an editable install does not follow.
`rcm worker --check` never follows. Details, including what a service manager sees, are in
[remote workers](operating.md#remote-workers).

**One process per worker token.** Each `rcm worker` process opens its own session when it
registers. When a second process starts with the same token, it becomes the worker: the first
one's next request is refused, it stops its running jobs, reports them `lost`
(`worker build-02 was replaced by a newer process`) and exits 0, so a service manager does not
start it again. If the first process cannot be reached when it is replaced (a network split), its
jobs keep their lane and group until the [disconnect grace](#a-lost-connection-disconnect_grace_seconds)
runs out, and the queue still shows them `running`: the worker name has a live session, so its
lanes are not `down`. An older worker is refused on its version first and follows the server or stops
with exit 2. A new worker that meets a server of its own version without worker protocol 2 stops
before it registers with
`the server speaks worker protocol 1 — upgrade rcm on the server (this worker needs protocol 2)`
and exit 3 ([remote workers](operating.md#remote-workers)).

### A lost connection: `disconnect_grace_seconds`

A remote job is not closed when its worker goes quiet. The server waits
`[server] disconnect_grace_seconds` after the last time it heard that worker process; only then
does the job become `lost` (`worker_unreachable`, exit 3 from `rcm wait`). A plain job keeps
running on the worker the whole time: if the connection comes back after the job was closed, the
worker finishes it and the server does not change the result. A worker that is restarted on the
same machine and data folder checks the jobs its last process left: a job whose process group is
gone is closed at once, and an `irreversible` job whose process it cannot verify is closed as
`orphan_unverified` (`the same work may still be running`) — check that machine.

| key | default | range |
|---|---|---|
| `disconnect_grace_seconds` | `max(600, L + 35)` | `L + 35` to `86400` |

`L` is `max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 50)`, so the floor is 95 with
the default 60 and 5. The value is written on the job when a worker claims it; a later edit or
`rcm reload` does not change jobs that already run. Time the server was stopped does not count:
after a restart the grace starts again from the moment the server came up, so a restart never
closes every remote job at once. A server with `lanes = 0` uses the same key.

### Jobs that must not run twice: `irreversible`

`irreversible = true` on a preset marks work that must not run twice at once, such as a store
upload or a deploy. rcm then does three things for its jobs.

- **The worker stops the job itself** when it cannot reach the server for most of the grace
  (`G − m`, where `m` is 15 s plus the preset's `grace_seconds` plus 10 s). The job ends `lost`
  (`worker_self_stopped`), before the server would close it. A plain preset is never stopped this
  way.
- **A supervisor runs the command.** The worker, or a local lane, starts the command under a small
  supervisor process. If the worker is killed (`kill -9`), the supervisor ends the command's
  process group within about a quarter of a second. Do not start rcm's supervisor yourself inside
  an irreversible preset: the job already runs under one.
- **An unconfirmed close holds the group.** When such a job closes without a confirmation that
  its processes are gone (the grace ran out, a cancel never reached the worker, the server was
  killed while a local job ran), the server holds the job's `concurrency_group`. No job of that
  group starts until the worker confirms or an admin runs `rcm release-hold N` after checking the
  machine ([releasing a hold](operating.md#when-a-group-is-held-rcm-release-hold)). The hold
  does not expire.

A preset with `irreversible = true` and no `concurrency_group` gets the group
`irreversible:<preset name>` with `concurrency_scope = "global"`; the server logs
`preset X: irreversible without concurrency_group — using group irreversible:X (global)` when it
starts, and `rcm presets` shows that group.

An irreversible preset cannot have [`[presets.retry]`](#trying-again-later-presetsretry): a retry
runs the command again, so the server refuses to load such a preset (`irreversible presets cannot
have [retry]`).

This key is not the release checks' irreversible mode (an input value such as `mode = upload` that
a [release profile](#release-profile) never makes the default). `irreversible = true` marks the
whole preset, so its job stops itself on a lost connection even when its input is a rehearsal.

**Keep the worker's clock from jumping.** The worker counts the time since the server last
answered on the wall clock too, so the time a sleeping machine spends asleep counts. A step
correction (NTP stepping the clock, or the date set by hand) that moves the clock forward by
`G − m` or more makes the worker stop an irreversible job early: the job ends `lost`
(`worker_self_stopped`, exit 3 from `rcm wait`). On a worker that runs irreversible presets, let
NTP slew the clock instead of stepping it.

The command must not leave daemons or other processes outside its session behind (`setsid`, a
double-fork daemon): rcm cannot see them, so for those processes this protection is fail-open.
They keep running after the job ends, and a later job of the same group can start beside them.

A held job's waiting neighbours show reason `needs_action` with `action.code =
"irreversible_hold"` in `/api/status`, whose top-level `irreversible_holds` lists every open hold
(`/api/health` carries the same list, for information only; both say `null` when the server could
not read them, never an empty list). `GET /jobs/N` shows the job's `hold`
and its history. A `[[notify]]` rule can ask for one call per new hold with
`on = ["irreversible_hold"]` ([notifications](#priority-snapshot-cache-and-notifications)).

### A server that only hands out jobs: `lanes = 0`

```toml
[server]
lanes = 0        # no local lanes: queue jobs and hand them to remote workers only
```

With `lanes = 0` the server runs no job itself. It takes submissions, keeps the queue and hands
each job to a worker that can take it. What changes:

- `@local` is not alive and is never a candidate: no placement, no `eligible_now` entry in the
  submission answer, no automatic check on it. The server does not run its own `[[probes]]`.
- A submission with `--machine @local` is refused with 409 (`this server has no local lanes`).
  `rcm recheck @local` gets the same 409, and the web machines table draws no Recheck button on
  that row. A [`local_only`](#keeping-a-preset-on-the-server-local_only) preset is refused the
  same way, since only the server's lanes may run it. Jobs already waiting when the server
  restarts with `lanes = 0`, or when a reload marks their preset `local_only`, wait with
  `no capable machine` and no ETA, and the reason names `no local lanes (lanes = 0)` on `@local`;
  they are not failed. Cancelling one cancels it at once. The next attempt of a retried job
  pinned to `@local` waits the same way, and a newer submission of the same branch does not
  supersede a job pinned to a machine.
- Without a worker a job waits (`worker down`, ETA `—`); a worker that attaches to its pool takes
  it at once, and the queue survives a restart.
- `/api/health` keeps answering 200 and carries `capacity`: `total` presets, how many have a live
  machine they can go to now (`reachable`) and the names of the rest (`unreachable`). A machine
  counts when the preset's candidates include it and it is alive; a `pool` preset without `checks`
  counts every live lane of its pool whatever the verdict, as placement does. `rcm check` draws a
  `capacity` row: FAIL when no preset has a machine, warn with the names when some have none
  (a deploy preset no machine runs can be normal), no row without presets. When the server
  could not count (`capacity: null`, the reason is in the server log once, not on every
  health call) or sends counts that do not add up, the row says
  `server could not count capacity`: FAIL on a `lanes = 0` server, warn on one with lanes. An
  older server that does not send the key gets no row.
- `pools_without_workers` in `/api/health` now lists the default pool too when all its workers
  are down, as the server has no lane of its own there.
- `rcm check` shows `local preset tools` and `service preset tools` as `this machine runs no
  jobs`, not as FAIL, and its `machines` row does not count `@local`. `rcm top` says
  `no local lanes` instead of `lanes 0/0 busy`, the host line of the server says `runs no jobs`
  (the host sampler keeps running), and `rcm machines` and the web machines table show `@local`
  as `runs no jobs` / `no lanes` instead of `down`.
- The mDNS record says `lanes=0`.

`rcm doctor --role coordinator` (the role a `lanes = 0` config names) ends on `capacity`
instead of «no preset would come here»: see [rcm doctor](operating.md#check-a-machine-before-you-use-it-rcm-doctor).
`rcm setup --role coordinator --bind ADDRESS --yes` writes such a `server.toml` with its server
service file: see [Set a machine up](operating.md#set-a-machine-up-rcm-setup).

## Which machine runs a job

Every worker — the server's own lanes (shown as `@local`) and each `rcm worker` — measures its own
environment and reports it: for every preset, which `requires` tools its job `PATH` can reach, and
the output of the `[[probes]]` commands, its **fingerprint**. From that report the server decides,
per worker and preset, whether the worker can run the preset: `eligible`, `ineligible` or
`unknown`, with a reason. A worker that has not shown it can do a job is not handed that job on the
hope that it will work.

A **worker** is a process that claims jobs; a **host** is the physical computer. Two workers on one
Mac (`rcm serve`'s lanes and an `rcm worker` beside it) share its simulators, disk and CPU, and rcm
knows they are one host: it keys hosts by a hash of the hardware UUID (`IOPlatformUUID` on macOS,
`/etc/machine-id` on Linux) and never stores or shows the UUID itself.

### Environment fingerprint: `[[probes]]`

```toml
[[probes]]
name = "xcode"                       # lowercase letters, digits, - and _; 32 characters at most
argv = ["xcodebuild", "-version"]    # no shell
timeout_seconds = 10                 # default 10, at most 60
```

Each worker runs every probe in its own service environment — the same `PATH` rules its jobs get —
right after it registers, after a server start or `rcm reload`, and every
`probe_interval_seconds` (default 600, at least 60). stdout and stderr together are reduced to the
first two non-empty lines joined with ` · `, control characters removed, cut at 120 characters.
Each probe ends in one state:

| state | when | value |
|---|---|---|
| `ok` | exit 0 | the reduced output — `Xcode 27.2 · Build version 27B52` |
| `error` | non-zero exit | `exit N` |
| `missing` | the program is not found | none |
| `timeout` | still running after `timeout_seconds` | the previous value, marked stale |

The fingerprint hash covers the `ok`, `error` and `missing` probes. A `timeout` does not change
it: the previous value carries over, marked stale, so one slow run is not a new environment. A
timeout on the very first measurement leaves the fingerprint `unknown`. When the fingerprint
changes rcm keeps the previous values, across restarts too, and shows what moved:
`xcode 26.5 → 27.2`. It compares values only — there is no version arithmetic like
`xcode >= 27`; whether a new version still works is what a [check](#proving-a-machine-works-checks)
is for. Without any `[[probes]]` the fingerprint is the same every time.

Probe output is readable by anyone who can read the queue: the values travel on the read routes
`/api/machines` and `/api/status`, so with `read_auth = "none"` everyone on the same network sees
them. Pick commands that print only a version (`xcodebuild -version`, `java -version`,
`flutter --version --machine`), never ones that print paths, user names or host names (`which`,
`env`, `xcode-select -p`).

The output must also stay the same while nothing changes. Only the first two non-empty lines
count, but a relative time in them moves the fingerprint by itself: plain `flutter --version`
prints `revision 058e0af2c2 (4 months ago)` on its second line, so the fingerprint changes when
the month turns and every check runs again. `--machine` prints the same JSON for the same
version.

A duplicate name, an empty `argv` or more than 64 probes stops the server at start, naming the
key. `rcm reload` re-reads `[[probes]]`; every worker then fetches the new list and measures again.

### Placement: `placement = "any-capable"`

| `placement` | who may take the job |
|---|---|
| `"pool"` (default) | the lanes of the preset's `pool` — exactly as before |
| `"any-capable"` | any worker, in any pool, whose verdict for this preset is `eligible` |

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
placement = "any-capable"        # instead of a pool: any machine that can run it
requires = ["fvm", "gitleaks"]
concurrency_group = "gate"
concurrency_scope = "host"       # one at a time per computer, in parallel across computers
```

For an `any-capable` preset, `pool` only decides which pool section a *waiting* job is listed
under. The worker that picks the job up stamps its own pool on it, so the finished job, its
notifications, `rcm jobs --pool` and the medians all record where it really ran. `rcm run … --pool
X` pins that one job to pool X — the person's choice wins. A worker missing a `requires` tool never
sees an `any-capable` job. A `pool` job is unchanged: a worker of that pool still picks it up and
it fails at once with `tool_missing`, because you tied it to that pool and a quick red beats a
silent wait.

`any-capable` is for presets that need no secrets. The release profile's secret files live on the
server under `<config dir>/secrets/<repo>/` and are never shipped to a worker, so a deploy, upload
or release preset stays `placement = "pool"` on the machine that holds them. Gates, tests and
checks that read nothing secret are what `any-capable` is for. A `pool` preset in the default pool
is still picked up by any remote worker you register in that pool; mark such a preset
`local_only = true` ([below](#keeping-a-preset-on-the-server-local_only)) to keep it on the
server's own lanes.

A `git_ref` job is fetched by whichever worker takes it, so every worker that may take an
`any-capable` `git_ref` preset — and every worker that runs checks, which are always `git_ref` —
needs the same `[[repos]]` entry in its `worker.toml`. A worker without it fails the job with
`repo_missing`.

A worker can opt out: `accept_any = false` in `worker.toml` (default `true`) keeps it for its own
pool's jobs and for checks addressed to it — a device lab you keep for one purpose. The server's
own lanes use `[server] local_accept_any` (default `true`).

The verdict, worker by worker — the first row that applies is the answer:

| code | state | meaning |
|---|---|---|
| `report_missing` | unknown | the worker has not reported yet |
| `report_stale` | unknown | the report predates the last server start or `rcm reload`, or is older than 3 × `probe_interval_seconds` |
| `tool_missing` | ineligible | a `requires` tool is not found |
| `workspace_tool` | unknown | the tool could only come from a relative `PATH` entry, which exists once a workspace does |
| `check_failed` | ineligible | a check preset failed on this worker with its current fingerprint |
| `check_running` | unknown | that check is queued or running |
| `check_needed` | unknown | no check has passed on this worker with its current fingerprint |
| `fingerprint_unknown` | unknown | the preset has `checks` and the fingerprint is unknown |
| `ok` | eligible | — |

Two more codes come from `local_only` (below). `local_only` (ineligible) is shown first, before
any row of this table, on a remote worker for a `local_only` preset. `check_local_only`
(ineligible, with the check's name) replaces `check_needed` on a remote worker for a preset whose
`checks` names a `local_only` check that has not passed there — that check can never run there, so
the preset is not "pending". It also replaces `check_failed`, and `check_running` while the
check's job is only waiting: no machine can take that job, and a recheck there is refused. A check
job the remote worker has already claimed runs to its end and counts.

`env_passthrough` names the worker's environment lacks are shown but do not block: an empty value
can be intended.

**When nothing can run it.** The job is queued anyway — a machine may be fixed later — and
`rcm run` prints one line on stderr: `gate: no capable machine — mbp: fvm missing ·
mac2-worker: down`. A busy machine is not a problem: if an eligible worker is merely busy, the job
waits as usual and nothing is printed. While no worker can take it, the waiting row's reason is
`no capable machine` with each worker's reason, its ETA is `—`, and the web page lists it under
the jobs that need attention. The `POST /jobs` answer carries
`placement: {mode, eligible_now, blocked}` for wrappers.

### Proving a machine works: `checks`

```toml
[[presets]]
name = "sim-check"                    # the project's own check: boot a simulator, tap once
argv = ["bash", "scripts/qa/sim_check.sh"]
source_modes = ["git_ref"]
repo = "app"
# check_ref = "HEAD"                  # the ref rcm runs it from (default: the default branch)

[[presets]]
name = "scenario-qa"
argv = ["bash", "scripts/qa/scenario.sh"]
checks = ["sim-check"]                # a worker takes this only once sim-check passed there
```

`checks` names *check presets*: before a worker gets the preset, each of them must have succeeded
**on that worker with its current fingerprint**. Every name in `checks` must be a preset that
accepts `git_ref` in its `source_modes`, because rcm submits it by itself from the repository —
`check_ref` on the check preset (default `"HEAD"`, the repository's default branch) is the ref it
resolves, and the check's inputs take their defaults. A check preset cannot have `checks` of its
own. Breaking any of these stops the server at start.

The state of a check comes from the job history: the newest job of that check preset that ran on
that worker with the current fingerprint. Succeeded → passed; queued or running → `check_running`;
failed or timed out → `check_failed`; none, cancelled or lost → `check_needed`. `checks` also
applies to `pool` presets — a check that has not passed makes the job **wait** (`check failed` or
`check pending`), it is not picked up to fail.

**Automatic checks.** With `[server] auto_checks = true` (the default) a sweeper every
`check_sweep_seconds` (30) submits each needed check **pinned to that worker**: requester
`rcm:check`, label `rcm (machine check)`, priority `high`. It does so **once** per worker, check
and fingerprint, so a check that fails, is cancelled or is lost is not submitted again by itself —
no retry loop — and **at most three times a day** per worker and check, however often the
fingerprint changes (a worker describes its own environment; one that keeps reporting a new
fingerprint shows `auto_check_limit` instead of running checks all day). A worker that is down gets
none, and neither does a worker that could never take the preset that needs the check (outside its
pool, or not accepting any-capable jobs). A check that ends without a verdict (cancelled or lost)
proves nothing: a pass or failure already recorded for the same fingerprint still stands. If `check_ref` cannot be resolved the sweep skips
that check and `rcm machines` shows `ref_unresolved`. A check job is an ordinary job: it is in the
queue, keeps its log and fires notifications.

What starts it again is a person — `rcm recheck MACHINE [CHECK…]` (admin) or **Recheck** on the
web page — or the next change of fingerprint. A check that will not come back by itself — the
automatic one was lost or cancelled, or `auto_checks = false` — is not shown as pending: the
waiting job's reason is `check failed` with `sim-check #3621 did not finish — recheck`, and
`rcm check` fails its `machines` row (`1 check needs rcm recheck`). So when Xcode updates itself overnight, the machine's
`scenario-qa` drops to `check_needed`, the sweeper runs `sim-check` there once, and if that goes red
`rcm check`, `rcm machines` and the web page say
`scenario-qa: check_failed (sim-check #3620 · xcode 26.5 → 27.2)` the same day. Fix the machine,
run `rcm recheck @local sim-check`, and it turns green.

A check preset can also warm the machine: put `pub get`, Gradle or CocoaPods in front of the real
check, and the first real job after an environment change finds warm caches.

### Running on one machine: `--machine`

`rcm run PRESET --machine ID` pins a job to one worker (`machine` in the `POST /jobs` body); only
that worker's lanes take it, ignoring pool, placement and verdict, so a check can never block
itself. A token may pin a check preset; pinning any other preset needs an admin token (403
otherwise). An unknown machine is 400, a machine that is down 409 — no job is created and
`rcm run` exits 2. A pinned job whose tool is missing is picked up and fails with `tool_missing`,
which for a check is the failed check. Pinning a `local_only` preset to a remote worker is 409
(next section).

### Keeping a preset on the server: `local_only`

```toml
[[presets]]
name = "release-ios"
argv = ["bash", "scripts/release_ios.sh"]
local_only = true                # only the server's own lanes run it, never a remote worker
```

`local_only = true` (default `false`) gives the preset one machine: `@local`, the server's own
lanes. Mark the presets that need what only the server has — the release profile's secrets, a
signing identity, a store login. Without it, a remote worker registered in the default pool picks
up a default-pool release job and runs it without those secrets, or with its own.

Everything that chooses a machine uses that one list, so a remote worker never sees the preset:

- it is not in the remote workers' `GET /worker/spec`, its `env` included, and no remote claim
  hands it out — pinned, pool or `any-capable`;
- `rcm machines` and `/api/machines` show it as `local_only` on every remote worker, and it is not
  counted in their `eligible` number;
- the waiting row, `rcm eta` and the `POST /jobs` answer (`placement.eligible_now`) count only
  `@local`. When no local lane can take the job, `POST /api/eta` answers `ahead: null` with no
  estimate. `ahead` was always a number before, so a script that reads it must accept `null`.
  Otherwise `ahead` counts only the jobs that compete for the server's lanes.

The server's lanes take the job exactly as before. A `local_only` `pool` preset must use the
default pool — the server's lanes serve no other — or the server does not start. Extra `pools` are
allowed, but a submission to one of them is refused.

**Submissions that cannot run are refused.** `POST /jobs` answers 409 and creates no job when the
server's lanes cannot take it: `--machine` names a remote worker (`… runs only on this server's
local lanes (local_only) — machine mbp cannot take it`), `--pool` names a pool other than
`default`, the preset is `any-capable` while `[server] local_accept_any = false`, or it is
`any-capable` and the server's lanes miss a `requires` tool. The reason code is
`no_capable_machine`, or `tool_missing` for the last case. This check runs before joining an
identical job and before superseding older ones. `rcm run` prints the server's reason and exits 2,
since no job was made. A preset whose check has not passed (or failed)
on `@local` is not refused: the job waits with `check pending` or `check failed`, as any other.
Nor is one submitted while `@local` has no report yet, or a stale one.

**Adding the mark later.** After `rcm reload` or a restart that adds `local_only`, jobs already
waiting are placed again: one pinned to a remote worker, or queued in another pool, shows
`no capable machine` with no ETA. It is not failed — cancel it or move the mark back. A scheduled
job follows the same rule once its time comes. A job already running on a remote worker is not
stopped: it finishes and reports its result as usual. Its retry (`[presets.retry]`) is a new
attempt and follows the mark: one pinned to that worker waits with `no capable machine` and no
ETA, one in the default pool goes to the server's lanes. An older server does not know the key
and refuses to start, so upgrade every server that reads the preset first, `include` files
included. Before rolling back below 0.11.0, remove the presets' `local_only` lines first: an older
server does not know the key and does not start.

**Checks.** A `local_only` check preset runs only on `@local`. On a remote worker, a preset that
needs it shows `check_local_only`, the check's row in `rcm machines` shows `local_only` and asks
for no recheck, the sweeper does not submit it there, and `rcm recheck <remote> <check>` is 409.
Its state follows the same rule as the verdict: unless the check passed there or that machine is
running it, the row shows `needed` with the last job's number, so a check job nobody can take does
not read as running, and a failure there is not counted among that machine's problems.
`rcm recheck <remote>` without names submits the other checks and answers 200; the skipped one is
listed apart from `jobs`, as `skipped: [{"preset": "<check>", "code": "local_only"}]`, and is not a
failure. A check that already passed on a remote worker before the mark was added keeps counting
there until that machine's fingerprint changes; after that it cannot be run there again.

### Concurrency groups across machines: `concurrency_scope`

`concurrency_group` keeps two jobs of one group from running at once. `concurrency_scope` says
where:

| `concurrency_scope` | the group blocks jobs running … | for |
|---|---|---|
| `"pool"` (default) | in the same pool — exactly as before | existing configs |
| `"host"` | on the same physical computer | simulators, emulators, attached devices |
| `"global"` | anywhere | one shared outside resource: a staging database, a store account |

The scope is stamped on the job when it is submitted, like the group — editing the config does not
change queued jobs. `"host"` is the one that matters when `rcm serve` and an `rcm worker` share a
Mac: they are different pools, so `"pool"` cannot stop both from taking the simulator at once;
`"host"` does.

### Estimates per machine: `sample`

Medians are kept per host. A job's expected duration comes from runs on the host the job is
expected to run on; when that host has fewer than `min_samples` runs, the median of the other hosts
is used with confidence `low` (`estimate.source` = `measured_other`); then the preset's
`expected_seconds`, then the default. Step medians — the "not responding" judgement — never borrow
another host's numbers: on a host without enough runs only the quiet rule applies, so a slower
machine is not called stuck. The queue ETA picks, for each waiting job, the open lane where it would
**finish** first among the workers that may take it.

`sample = false` keeps a preset's jobs out of every median. Check presets default to `false` (a
check that also warms caches is not a fair sample); everything else defaults to `true`.

### Seeing it: `rcm machines`, `rcm check`, `rcm recheck`

```
$ rcm machines
machine            host            pool     lanes  fingerprint                   presets
Mac mini (@local)  macmini (a1f3)  default  2      xcode 27.2 · flutter 3.47.8   21/24 ok · 3 need sim-check
mac2-worker        macmini (a1f3)  mac2     1      xcode 27.2 · flutter 3.47.8   21/24 ok
mbp                mbp (7c20)      mbp      1      xcode 26.5 · flutter missing  4/24 ok · 18 fvm missing
```

`rcm machines [--json] [--machine ID]` — one row per worker; two workers on one host show the same
host label. A machine's ID is its worker token name, or `@local` for the server's own lanes; the first column
shows the display name with the ID in brackets when they differ. `--machine`, `rcm run --machine`
and `rcm recheck` take either — the ID first, then the display name; a name that fits several
machines is refused with their IDs. `--machine ID` prints that worker's
verdict for every preset, each probe with its previous value, and its checks. `--json` is the
`GET /api/machines` document as the server sends it.

`rcm check` adds two rows. `machines`: how many machines, which changed environment in the last 24
hours and which check failed — `3 machines · 1 environment changed (Mac mini: xcode 26.5 → 27.2) ·
1 check failed (Mac mini: sim-check #3620)`; FAIL when a check failed. `placement`: every
`any-capable` preset and every preset with `checks`, with how many machines can run it now —
`gate: any machine (2 eligible) · scenario-qa: pool default (check failed)`; warn when one has none.
A third row, `capacity`, counts the presets that have a live machine to run on now
(`9/10 presets have a machine to run on — none for: deploy-android`): FAIL when none has, warn
when some have not, no row on a server without presets or one too old to say.

`rcm recheck MACHINE [CHECK…]` (admin; `POST /machines/{id}/recheck`) submits that worker's checks
again — all of them, or the ones named — whatever the automatic once-per-fingerprint rule and the
daily limit say, and prints each job number. A machine that is down is refused (409), as with
`rcm run --machine`: start `rcm worker` there first. The web page has the same as a **Recheck** button in its machines table,
for admin tokens. `rcm top` and `rcm jobs` mark an `any-capable` waiting job `any machine` and a
pinned one `on <machine>`.

### Keys

| where | key | default |
|---|---|---|
| `[[probes]]` | `name` · `argv` · `timeout_seconds` | — · — · `10` |
| `[server]` | `probe_interval_seconds` · `auto_checks` · `local_accept_any` · `check_sweep_seconds` | `600` · `true` · `true` · `30` |
| `[[presets]]` | `placement` · `concurrency_scope` · `checks` · `check_ref` · `sample` | `"pool"` · `"pool"` · `[]` · `"HEAD"` · `true` (`false` for a check preset) |
| `[[presets]]` | `local_only` | `false` |
| `worker.toml` | `server = "auto"` · `server_name` · `accept_any` | — · `""` · `true` |

`rcm reload` picks up `[[probes]]` and the preset keys. The `[server]` keys need a restart, like
the rest of that section: a reload that sees them changed is refused and names them.
`examples/server.toml` has a commented Flutter block with all of it.

## Linking a job to a page

A job can carry one link to the page it is about — usually its pull request. The web UI shows it
right after the job's name in the queue, in the recent list and in the log drawer's header, as
`PR #816 ↗`, and opens it in a new tab. rcm does not know what the page is: the caller finds the
pull request and passes its address, so rcm still needs no GitHub access.

```sh
rcm run gate -f scope=full --link https://github.com/org/app/pull/816 --link-label "PR #816"
```

The same thing in the `POST /jobs` body is `"link": {"url": "…", "label": "…"}`.

| field | rule |
|---|---|
| `url` | required when `link` is given; starts with `http://` or `https://` and has a host; at most 500 characters; no spaces or control characters |
| `label` | optional; at most 40 characters; no control characters. Without one the chip shows the address's host and the end of its path (`github.com/…/pull/816`) |

Anything else is refused with 400 and a sentence naming the field (`link.url must start with
http:// or https://`); `rcm run` checks the same rules first and stops with exit 2 before it
sends anything, and `--link-label` without `--link` is also exit 2. A submission that joins an
existing job does not change that job's link — the link belongs to the submission that created
it. Jobs carry `link` (`{"url", "label"}`, `label` may be `null`; `null` when there is none) in
`/jobs/<id>`, the queue rows and the recent list of `/api/status`. The web UI checks the address
again and draws nothing for one that is not `http(s)`. The chip stays on one line and is cut with
an ellipsis (the full address is its tooltip), so a long label never makes a queue row taller;
clicking it opens the page and does nothing else to the row.

A wrapper detects support with `rcm run --help | grep -q -- --link-label`. A server from before
this feature (database v21 or older) ignores the field and queues the job without a link, so
adding it never makes a submission fail.

## Who may cancel a job

Every `POST /jobs` — a fresh submission and a join alike — answers with a `submission` object,
`{"id": "…", "cancel_token": "…"}`. The token is a secret that carries exactly one right: the
requester's token cancels the job (`cancel_job`), a joiner's token only removes that joiner from
the join list (`leave_submission`). The server keeps a SHA-256 of it (database v17, table
`submissions`, written in the same transaction as the job or the join and deleted together with
the job's metadata), and two sessions that share one client token still receive two different
secrets — a session cannot cancel a job that another session submitted with the same token unless
it also shares that session's state file. A `leave_submission` token is bound to the token name it
was issued to: sent with another bearer token it answers 403, and it works once — two requests
with the same token at the same time get one 200 and one 403. `rcm run` saves the token in
`$XDG_STATE_HOME/rcm/submissions.json` (or `~/.local/state/rcm/submissions.json`, mode 0600,
one entry per submission, locked while written) and `rcm cancel N` sends the newest one that
this client token saved for that job, never an entry another token saved (`--submission-id`
picks another); `rcm cancel N --cancel-token …` takes it from a wrapper
that kept the `--no-wait` JSON, which carries the same `submission` object. A token that worked
is removed from the file. The file keeps at most 200 entries; above that it drops only entries
whose job the server says has finished (oldest first) — a running or queued job's entry is never
dropped, and when the server cannot be asked nothing is dropped. When the file cannot be written
(`rcm run` prints one `warning: the cancel token was not saved …` line, never the token itself)
the `--no-wait` JSON is the only copy, and `rcm cancel N --cancel-token …` is the way to use it.
Ctrl-C keeps its
meaning: the requester detaches and the job keeps running; a joiner leaves the join list with its
own token. A token whose role the server does not know (`cancel_job` and `leave_submission` are
the only two) is refused with 403 and a server log line.

One server key, `cancel_requires_submission_token`, decides whether the token is optional or
required:

```toml
[server]
cancel_requires_submission_token = false   # the default
```

| value | who may cancel |
|---|---|
| `false` (default) | as before — the token that submitted, a joiner (it only leaves) and an admin token — plus any valid cancel token, honoured by its role. Clients on 0.2.x keep cancelling |
| `true` | a valid cancel token (`rcm cancel N`, or `--cancel-token` when the state file is elsewhere) or an admin token, nothing else. There is no `--force` for a non-admin token: a shared client token does not get the old right back |

Turning it on changes what old clients can do. A 0.2.x client (anything before 0.2.7) never sends
a cancel token, so its `rcm cancel` and a joiner's Ctrl-C answer 403
`not your submission — pass the cancel token from the submission (rcm cancel --cancel-token …)`.
While the key is on, `/api/health` raises `min_client_version` to the effective floor (`0.2.7`
instead of the usual `0.2.0`, so an old client's own `rcm check` fails its `client` row and its
`rcm run` warns — both still submit) and adds `cancel_min_client_version` (`0.2.7`; `null` when the
key is off) to say why; `rcm check` prints a `cancel` row naming it. Keep the key off until every
session has upgraded (`examples/session/update-client.sh`). The web page
never holds a cancel token, so with the key on its **Cancel** button is disabled unless the pasted
token is an admin token, and the row says why. The token appears nowhere else: not in
`/api/status`, not in job documents, logs, errors or URLs.

## Superseding older jobs of the same branch

When a session pushes again, the job for its previous commit usually stops mattering — only the
newest code's gate result is worth a lane. A preset can let the server clear those older jobs
itself, instead of each session finding, cancelling and resubmitting by hand:

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]
supersede = "all"        # "off" (default) | "waiting" | "all"
```

| value | when a new job of this preset enters the queue, an older matching job … |
|---|---|
| `"off"` (default) | is left alone — the behaviour before this key existed |
| `"waiting"` | is cancelled if it is still waiting (`queued`, or still uploading); a running one finishes |
| `"all"` | is cancelled whether it waits or runs. A running one stops the way any cancel does: SIGTERM, then the grace period; a remote worker hears it in its next heartbeat |

The default is `off` on purpose: a deploy, upload or store preset must never lose a job quietly.
Turn it on for presets where only the newest code matters (a gate, a test run). Use `waiting`
when a long job's partial result is still worth seeing.

**Which jobs count as "older jobs of the same branch".** At the moment the new job N enters the
queue — when its upload finishes (tree) or when it is submitted (git_ref) — an older job O is
cleared only if all of these hold:

1. same preset, and that preset's `supersede` is not `off`;
2. same source mode and repository (a tree job and a git_ref job never clear each other). **If
   either side has no repository name** (a tree from a repository without an `origin` remote),
   nothing is cleared — two unrelated local repositories can both be on `main`;
3. same branch — `source.branch` for a tree job, `source.ref` for a git_ref job. **If either side
   has no branch** (a detached tree), nothing is cleared;
4. the same inputs (`rcm run test-narrow -f target=a` and `-f target=b` ask different questions);
5. a different tree or commit (the same one joins instead);
6. the same requester, and every joiner of O is that requester too — your push never cancels a
   job someone else is waiting for;
7. O is older (a smaller id) and in a state the value allows; a job already being cancelled is
   left as it is;
8. neither job was aimed at one machine (`--machine`, check jobs) — choosing a machine is a
   different question, whichever side made the choice — and O waits in the same pool:
   `--pool linux` and the default pool are different questions.

A new job that is still uploading clears nothing, so a failed upload or a server restart in the
middle leaves the old job queued. A reload that turns the key on applies to the next job that
enters the queue; jobs already queued are not cleared retroactively.

**What a cleared job leaves behind.** It ends `cancelled`, with `cancelled_by = "superseded"`,
`summary_code = "superseded"`, `summary_args = {"by": N}` and the summary `superseded by #N`.
The queue row and `GET /jobs/<id>` carry `superseded_by: N` (`null` for every other job); the
web page draws `#N` as a link to the new job. Events and notifications are those of any cancel
(a `[[notify]]` rule with `on = ["cancelled"]` hears it). The new job takes the higher priority
of the two — if the old job had been bumped to `high`, so is the new one; it never goes down.
A cleared job is not an estimate sample, like any cancelled job.

`rcm wait --job 41` (and the wait in `rcm run`) ends with exit code **2**, as for any cancel, and
prints one more line:

```
#41 superseded by #57 — rcm wait --job 57
```

`--json` carries `superseded_by: 57`. rcm does not follow the new job for you: the waiting script
asked for job 41's result, and an exit code that quietly changed meaning would mislead it.

**Servers that require the cancel token.** With `cancel_requires_submission_token = true` (see
[who may cancel a job](#who-may-cancel-a-job)) a session cannot cancel another session's job
without that job's cancel token, even under the same token name. Supersede only knows the token
name, so on such a server it cancels nothing: the new job queues next to the old one, the server
log says `job 57: supersede skipped: cancel_requires_submission_token`, and the start-up log,
`rcm reload` and `rcm check` warn (`supersede  warn  skipped for gate — cancel_requires_submission_token
is on`) without failing. Set the preset back to `supersede = "off"`, or turn the token rule off,
to make the warning go away.

**Joining a job that is being cancelled.** A submission never joins a job in `cancelling` — a job
being stopped cannot give the joiner a result. The same preset, inputs and tree make a new job
instead.

**Opting out for one submission.** `rcm run gate --no-supersede` (API: `"supersede": false` in the
`POST /jobs` body) clears nothing — for running a flaky test twice side by side. There is no way to
turn it on from the submitting side: a preset set to `off` stays off.

## Running a job later: `--at` and `--in`

`rcm run` can hand the server a time instead of running now. The job waits on the server as
`scheduled` and enters the queue at that time:

```sh
rcm run deploy --ref main --at 15:00            # today, 15:00 on the server's clock
rcm run deploy --ref main --at 2026-11-24T09:30
rcm run gate --in 90m                           # 90s · 5m · 2h · 1h30m · 1d
```

The server reads the time, not your machine: `--at` is sent as written and `--in` as a number of
seconds. A time without an offset is read in `[display] timezone`, or in the server's local zone
when that is empty. Three shapes are accepted — `HH:MM` (today), `YYYY-MM-DDTHH:MM`, and ISO-8601
with an offset (`2026-11-24T09:30+09:00`, `…Z`). A time that has passed is refused with 400
`start_at_past`; it is never moved to tomorrow. So is a time more than `schedule_max_ahead_days`
(14) ahead (`start_at_too_far`), and a wall-clock time that a daylight saving change skips or
repeats (`start_at_ambiguous` — give an offset). The submission answer carries the UTC time it
settled on (`not_before`), the start deadline (`start_deadline`) and the zone it used
(`timezone`). Over HTTP the same keys are `start_at` (string) or `start_in_seconds` (integer) on
`POST /jobs`.

An older server ignores body keys it does not know, so it would run the job **now**. `rcm run`
only sends `--at` or `--in` to a server whose `/api/health` says `schedule: true`; anything else
ends with `server does not support scheduling — upgrade it` and exit 2, before a snapshot is made.

**The start deadline.** A scheduled job must be picked up by a lane within
`[server] schedule_start_deadline_seconds` (900) of its time. A preset can set its own
(`start_deadline_seconds = 3600`, applied on `rcm reload`); a submission may only shorten it
(`start_deadline_seconds` in the body, 400 `start_deadline_too_long` otherwise). A job that misses
it — because the lanes were busy, or because the server was down at the time — becomes
`expired`: it did not run, `rcm wait` exits 2 (the same as `cancelled`), and its summary says
`did not start by its start deadline 2026-11-24 09:45Z`. On start-up the server closes the
schedules that passed their deadline while it was down and queues the ones whose time came but
whose deadline did not, all before a lane starts. The deadline clock keeps running while the queue
is paused.

| state | means | `rcm wait` |
|---|---|---|
| `scheduled` | waiting for its time; no lane, no process | keeps waiting, prints `scheduled for 15:00 · start by 15:15` |
| `queued` (from a schedule) | its time came; waits for a lane like any job, with its submission's priority | keeps waiting |
| `expired` | no lane picked it up before the deadline | 2 |

**What a schedule does not do.** It never joins another job, and no job joins it: a schedule
next to a running job with the same preset, inputs and source is a new job, and `rcm run` of the
same thing now does not wait for the schedule. A second schedule with the same preset, inputs and
source (the commit for `git_ref`, the tree for an upload) is refused with 409 `schedule_exists`
and the first job's `job_id` (`rcm run` exits 2: no job was made); cancel it or pick another time. A `git_ref` schedule resolves its
ref when you submit, as every `git_ref` job does; a tree is uploaded when you submit and kept until
the job runs — its snapshot blobs are protected from the cache sweep the same way an active job's
are. [Supersede](#superseding-older-jobs-of-the-same-branch) happens when the schedule enters the
queue, not when it is made, and never cancels a schedule; `--no-supersede` is stored with the job
and survives a restart.

**Seeing and changing it.** `rcm jobs --state scheduled` lists them, `/api/status` has them under
`pools[].scheduled[]` in time order (apart from `queue`, with `scheduled_error` and
`scheduled_error_code` when they cannot be read), and queue and recent rows carry `not_before`
and `start_deadline` (`null` for a job that was never scheduled). `rcm cancel N` cancels a
schedule at once, `rcm bump N` changes its priority, and `rcm eta --job N` answers
`scheduled for 15:00` without inventing a number. The ETA of a new submission does not count
schedules about to enter the queue. Schedules live in the database: `rcm reload` and a restart
keep them; one whose preset was removed fails at claim with `preset_missing`. A `[[notify]]` rule
without `on` also fires for `expired`, and `on = ["expired"]` picks only those. Finished schedules
follow the ordinary [retention](#retention-what-is-kept-and-for-how-long).

**On screen.** The web page lists schedules below the queue under **Scheduled (N)**, in time
order: the preset, its inputs, who asked, the time and the start deadline in the display time
zone, and **Cancel** on your own. A schedule has no ETA; the page does not invent one. When the
server could not read them the section is a red **Scheduled jobs unavailable** band, never an
empty list, and «Needs a look» says `unknown — scheduled jobs unavailable` instead of
`Nothing needs a look` (a schedule waiting for a person may be among them). `rcm top` adds one line per pool, `scheduled — 2 · next 15:00 golive`, or
`scheduled — unavailable: <error>`; with no schedules it adds nothing. A job that missed its
deadline is `expired · exit 2` in the recent list. The queue reason `not_scheduled` (a lane sat
idle and did not pick the job up) reads `idle lane did not pick it up` on both, so it is not
mistaken for this state.

## Trying again later: `[presets.retry]`

Some scripts can only say "not yet": the store has not finished the release, the browser session
needs a person to log in again. A preset can ask rcm to run such a script again later. The script
exits with the agreed code (75, `EX_TEMPFAIL`) and rcm runs it again after an interval, up to a
limit:

```toml
[[presets]]
name = "release-golive"
argv = ["bash", "scripts/release_golive.sh"]
source_modes = ["git_ref"]   # required: each attempt fetches the same commit again
repo = "app"

[presets.retry]
on_exit = 75             # the exit code that means "not yet" (1–255, default 75)
interval_seconds = 600   # wait this long before the next attempt (default 600)
max_attempts = 12        # attempts in all, the first one included (2–100, default 12)
```

**Only a preset that declares it retries.** Without `[presets.retry]` an exit 75 is an ordinary
`failed` job, as before; `rcm-ci-connect` uses 75 for an infrastructure failure, and that must
not loop. A preset that retries takes `git_ref` sources only (`source_modes = ["git_ref"]`, a
configuration error otherwise): every attempt runs the commit the first one resolved. The policy
is stored with the job when it is submitted, so `rcm reload` or removing the preset does not change
a chain already running. A submission can only turn it off for that job: `"retry": false` in the
`POST /jobs` body; such a job is not a chain (no `chain_id`, no `attempts[]`). A submission
only joins a job whose frozen policy is the same (`on_exit`, `interval_seconds`, `max_attempts`
and whether `retry: false` turned it off), so a submission made after a reload changed the limit
starts a job of its own, and a submission that wants retries never joins a
`retry: false` job or one submitted before the preset declared the table, and the other way round.

**Each attempt is a job of its own.** An attempt that exits with `on_exit` ends as `retried` and,
in the same database transaction, the next attempt is created as `scheduled` for
`now + interval_seconds`. It keeps the inputs, the commit, the priority (raised by joins too), the
link, the machine or pool, `keep`, `--no-supersede`, and the joiners. The last attempt that still
exits with `on_exit` ends as `exhausted`: rcm never found out, and a person has to look.

| state | means | `rcm wait` |
|---|---|---|
| `retried` | this attempt said "not yet"; `retried_as` names the next one | keeps waiting, on the next attempt |
| `exhausted` | the last attempt said "not yet" too | 1 |

A retry attempt has **no start deadline**: when the lanes are busy, or a concurrency group is held
(a login preset sharing the group while a person signs in), it waits as `blocked_by_group` without
ending `expired`. The chain ends only at `max_attempts`. A retry attempt is not a new
submission: entering the queue it [supersedes](#superseding-older-jobs-of-the-same-branch)
nothing — it runs the older commit, and a newer job of the same branch must not be cleared for it.

**The chain.** Every attempt carries `chain_id` (the first job's id), `attempt` (1, 2, …),
`retry_of`, `retried_as` (on a `retried` attempt) and `max_attempts`, in `GET /jobs/<id>` and in
`/api/status` rows; `presets[].retry` shows the declared policy. `rcm wait` and `rcm run` follow
`retried_as` to the end of the chain: `--timeout` counts for the whole chain, `--fetch-artifacts`
fetches the last attempt's files, and the final JSON's `job_id` is the last attempt, with
`chain_id` and `attempts[]` (`job_id`, `attempt`, `state`, `exit_code` for each). Each step prints
`#57 exit 75 · attempt 1 of 12 · retrying as #58 — waiting for it`. A `retried` attempt without a
next one would be a server bug; `rcm wait` exits 3 then, never 1.

**A person's task across attempts.** A script that needs a person (a login, say) prints an
`::rcm::action::` marker and exits 75. The marker belongs to the whole chain: the chain shows the
last marker of the most recent attempt that ran, while the next attempt waits as `scheduled`,
waits in the queue, or runs without a marker of its own. An attempt that runs to its end without a
marker clears it. So `/api/status` gives the waiting attempt `reason: "needs_action"` and its
`action` (on `pools[].scheduled[]` rows too, which carry `reason` and `action`, both `null`
without a marker), and `rcm wait --return-on-action` returns 4 between attempts as well.

`rcm cancel` takes any id of the chain and stops the attempt that is still alive; the cancel token
from the first submission works for every attempt. A cancel that lands while an attempt is
exiting 75 wins: the attempt ends `cancelled` and no next attempt is made. Only the last attempt
collects artifacts and goes on the [shelf](#keeping-a-jobs-files-the-shelf); a `retried` attempt
keeps no workspace even with `keep_workspace_on_failure`. `retried` attempts stay out of the recent
list (the chain shows as its last attempt, with its `attempt` number), out of the estimate samples
and out of the default `[[notify]]` set (`on = ["retried"]` asks for them); `exhausted` is in it.

**Older clients.** A client before 0.10.0 does not know `retried` and would wait on it forever. A
server with at least one preset that retries raises `min_client_version` in `/api/health` to
`0.10.0` (and says `retry_min_client_version: "0.10.0"`), so the old `rcm check` shows the client
row as FAIL. Making a script safe to run again is the script's job.

## Client and worker files

`client.toml` (mode 600 — a file that holds a token and is readable by anyone else is
**refused**, not warned about):

```toml
server = "http://macmini.local:8787"   # leave empty (or "auto") to find the server on this network
token = "…"                            # from `rcm token add <name>` on the build machine
label = "alice@laptop"                 # who the queue shows as the requester
```

`RCM_SERVER`, `RCM_TOKEN` and `RCM_LABEL` override the file; `--server`, `--token` and `--by`
override both. `RCM_CLIENT_CONFIG` (or `--client-config`) points at another file.

**Several servers on the network.** With an empty `server` (or `"auto"`) a client uses the one rcm
server discovery finds. When it finds more than one, the client does not pick one: it stops with
exit 2 and `several rcm servers on this network: … — pick one with --server or client.toml`,
listing each server's name and address. Pass `--server URL`, or write the URL as `server` in
`client.toml`. A worker chooses among several by name, with `server_name` in `worker.toml` (below).
`rcm setup --role client` does not search the network: it needs `--server` and never writes
`server = "auto"`; remove the line or set `server = "auto"` by hand to use discovery. A server
answers with its short host name, or with `advertise_name` in `server.toml`; `rcm setup` has no
flag for that key. In WSL2 under the default NAT network, discovery finds nothing; see
[Windows (WSL)](usage.md#windows-wsl).

Which code a session runs is not a `client.toml` key, because an older version would refuse the
key. A session follows the server's version unless `RCM_CLIENT_VERSION` or a one-line file says
otherwise: `follow`, `installed` (run this install) or a version such as `0.8.0`. The file is
`$XDG_CONFIG_HOME/rcm/client-version`, else `~/.config/rcm/client-version`; blank lines and text
after `#` are ignored. `--no-follow` beats `RCM_CLIENT_VERSION`, which beats `client-version`.
With none of them a wheel install follows and an editable install does not. `rcm worker` reads
the same setting (and takes `--no-follow` too). Downloaded clients live in `$XDG_CACHE_HOME/rcm`
(`~/.cache/rcm`). See
[keeping clients on the server's version](operating.md#keeping-clients-on-the-servers-version).

`worker.toml` keys: `server` (a URL, or `"auto"` to find it over mDNS), `server_name` (which
server, when several advertise), `token` (or `RCM_WORKER_TOKEN`), `pool`, `lanes`, `name`,
`accept_any` (take `any-capable` jobs, default `true`), `data_dir`, `grace_seconds`,
`keep_workspace_on_failure`, `[host]` (the sampler) and `[[repos]]` (so the worker can fetch
`git_ref` jobs itself). `rcm init worker` writes one; see `examples/worker.toml` and
[Second build machine](#second-build-machine-remote-worker).
