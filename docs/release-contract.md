# Release contract — connecting a project to the Store tab

> This is what the Store tab reads, as it is built. The screen it describes is drawn in
> `docs/wireframes/web-store.html`. The first project on this contract is a Flutter app whose
> scripts already emit every file below — its names appear only as examples.

The Store tab turns a project's own release scripts into buttons: fetch, plan, gate, QA, upload,
submit for review. rcm **draws files and runs presets**. It does not compute build numbers, judge
QA, decide what is releasable, or release anything after store approval. Every verdict on the page
comes from a JSON file your script wrote; every action is a preset you declared.

The quickest way to provide all of it is the skills rcm ships: `rcm skills install --into <project>`
copies them into the project's `.claude/skills/`, and `/rcm-connect` there asks for the project and
runs the rest (§8). The contract below is what those skills produce; a project can also write it by hand.

A project connects by giving rcm four things:

1. a **release profile** in `server.toml` — which presets play which role, which secrets exist;
2. **presets** for those roles whose scripts write the **artifact files** below;
3. **step markers** on stdout (the ones every rcm job may print) plus one optional progress marker;
4. optionally, **listing commands** that render the store copy and screenshots the tab previews.

Anything missing degrades the page, never the server: a role without a preset shows
`not configured — profile.presets.qa is empty`, and the other cards keep working.

---

## 1. The release profile

One sub-table per repository, next to the `[[repos]]` entry that already lets `git_ref` presets
fetch it:

```toml
[[repos]]
name = "app"
url  = "git@github.com:org/app.git"

[repos.app.release]                       # absent = no Store tab for this repo
default_branch       = "main"             # where releases are cut from (default "main")
tag                  = "prod/{version}-{build}"   # "release exists" is this tag, exactly (default)
build_number_policy  = "auto"             # "auto" (store max + 1, computed by YOUR script) | "manual"
plan_max_age_minutes = 30                 # older plans get a stale badge; Submit stays closed
version_ttl_hours    = 24                 # a new-version draft nobody edited is discarded after this (integer ≥ 1)
driver               = "scripts/release/product_release.sh"   # optional: the V S0–S8 round (§5)
secrets_dir_env      = "APP_SECRETS"      # jobs get the secrets folder path in this variable

[repos.app.release.presets]               # names are yours; rcm only needs the role
plan   = "release-plan"                   # required — read-only store snapshot → plan.json
upload = "release-upload"                 # required — store upload → upload.json
review = "release-review"                 # required — submit for review → review-plan.json / review.json
gate   = "gate-smoke"                     # optional — your CI gate on the release ref
qa     = "scenario-qa"                    # optional — device / scenario QA → report.json
version = "release-version"               # optional — «new version» from the web: prefill / create / delete
# dev  = "deploy-dev"                     # optional — dev distribution card (phase 2)

# Secrets the Settings screen asks for. rcm keeps them under <config_dir>/secrets/<repo>/
# (0700, files 0600) and never shows a value again. `verify` names a read-only check (§4).
[[repos.app.release.secrets]]
name = "ASC_KEY_ID"
kind = "value"                            # "value" | "file" | "dir"
verify = "asc"

[[repos.app.release.secrets]]
name = "AuthKey.p8"
kind = "file"
verify = "asc"
max_kb = 16

[[repos.app.release.secrets]]
name = "play-service-account.json"
kind = "file"
verify = "play"
max_kb = 64

[[repos.app.release.secrets]]
name = "upload-keystore.jks"
kind = "file"
verify = "keystore"

[[repos.app.release.secrets]]
name = "GH_TOKEN"
kind = "value"
verify = "github"

[[repos.app.release.secrets]]
name = "TEAMS_WEBHOOK"
kind = "value"
optional = true

[[repos.app.release.secrets]]
name = "review_information"               # reviewer contact + demo account, one file each
kind = "dir"
files = ["first_name.txt", "last_name.txt", "phone_number.txt", "email_address.txt",
         "demo_user.txt", "demo_password.txt", "notes.txt"]

# Optional: commands the Listing preview runs on a checkout of listing.ref (default_branch) — seconds, not a job.
# `preview` lines are read as fields when they look like `key: value` (`ios.key`, `[play] key` name the
# store) or, under a section header such as `iOS (ko)` / `Android (ko-KR)`, `key   12자   value`
# (two or more spaces, an optional character count). Everything else is shown as-is.
[repos.app.release.listing]
preview       = ["python3", "scripts/release/store_listing.py", "preview"]
diff          = ["python3", "scripts/release/store_listing.py", "diff", "--live"]
validate      = ["python3", "scripts/release/store_listing.py", "validate", "--build-name", "{version}", "--version-code", "{build}"]
ref           = "dev"      # optional — read the copy from this branch (a dev → main project ships what dev has)
screenshots   = ["store/screenshots/**/*.png"]
release_notes = "store/release_notes/{version}/*.txt"
```

`{version}` in `tag`, `listing.validate` and `listing.release_notes` is the round's version name.
The two stores may get different names (§2 «Two store version names»): the tag joins them with a
`+` then (`prod/1.1.1+1.0.1-181`), and `release_notes` tries the iOS name first, then the Android one.

`rcm check --config server.toml` adds one row per profile, `release <repo>`. It is FAIL when a
required role is missing or names a preset that does not exist, when the `upload` preset's `mode`
defaults to `upload` or the `review` preset's `mode` defaults to `submit`, when a `version` preset's
`mode` does not default to `prefill`, when those presets lack the inputs rcm sends (§2), when
secrets exist without `secrets_dir_env`, or when a secret name repeats. It warns (not FAIL) when the
secrets folder does not exist yet, an optional role is unset, or the `review` / `upload` preset has
no `listing_json` input (the copy edited in the web UI cannot reach the script then) or no
`build_name_android` input (the two stores must then share one version name).

**What the profile must not contain**: secret values, store credentials, or anything the page
would show back. Secrets are entered once in the Settings screen and never displayed; the API
returns only `present · fingerprint · verified_at`.

---

## 2. Roles, inputs and artifacts

rcm submits each role's preset through the normal job path (same queue, same joiners, same
artifact retention). It sets these inputs; declare them on the preset or the submission bounces.

| Role | Tier | Inputs rcm sends | Artifact rcm reads | Exit codes rcm distinguishes | Skill that adds it |
|---|---|---|---|---|---|
| `plan` | **required** | `build_name` | `plan.json` | 0 ok · 1 blocked (plan.json still written) · 2 environment | `rcm-store-connect` |
| `upload` | **required** | `build_name`, `build_name_android`, `confirm_build_number`, `mode = rehearsal\|upload`, `platform`, `android_track`, `listing_json` | `upload.json` | 0 · 1 · 2 env · 3 confirmation mismatch · 4 store drift | `rcm-store-connect` |
| `review` | **required** | `build_name`, `build_name_android`, `confirm_build_number` (empty for plan), `mode = plan\|submit`, `platform`, `play_managed_publishing = not-checked\|confirmed-on`, `listing = notes-only\|full`, `phased = 1\|0`, `listing_json` | `review-plan.json` / `review.json` | 0 · 1 failed · 2 blocked · 3/4 confirmation mismatch · 5 noop · 6 partial | `rcm-store-connect` |
| `version` | optional | `mode = prefill\|create\|delete` (default **prefill**; rcm itself only ever sends `create` and `delete`), `ios_version`, `android_version` (empty = that store is not touched), `asc_version_id` (delete only) | `version.json` (create · delete) / `prefill.json` (prefill · create) | 0 · 1 failed · 2 environment · 3 already exists (an editable App Store version of that name) · 4 not deletable (the version was submitted) | `rcm-store-connect` |
| `gate` | optional | *(none — ref only)* | commit status is yours; rcm shows steps/log | 0 · non-zero | `rcm-gate-connect` |
| `qa` | optional | project-defined (`order`, `android_mode`, …); rcm passes the profile defaults | `report.json` (+ optional `step-captures.html`) | 0 PASS · 1 FAIL · 2 / 10 BLOCKED (environment, shown amber not red) | `rcm-qa-connect` |
| `driver` | optional | `--build-name`, `--version-id`, `--confirm-build-number` (§5) | stage from remote truth | 0 · 1 · 2 · 3 · 4 | `rcm-release-driver` |

**Required** means the Store tab opens only when these exist and the secrets they need are present
and verified. **Optional** roles that are unset show a grey row saying which skill adds them; the
review flow works without them. Without a `version` preset, «new version» in the web UI creates
the draft in rcm only (state `editing` at once) and the listing starts from the repository's
`store/` files instead of a prefill.

**The `version` preset's three modes are not three things rcm does.** rcm sends `create` when
somebody opens a draft — that one run also writes the `prefill.json` the version page fills its
fields from — and `delete` when a draft with an App Store version is discarded, by hand or by the
expiry sweep. `prefill` is the read-only mode, and it must be the preset's **default** so that
running the preset by hand, or by accident, reads the live listing and touches no store; `rcm check`
fails a preset that defaults to anything else, and warns when the `mode` input declares its values
and `create` or `delete` is not among them.

**`listing_json`** (`review` · `upload`; string, default `""`) is the listing copy a human edited on
the version page, as one JSON object `{"ios": {…}, "android": {…}}` whose keys are the
`prefill.json` field names below. rcm sends it only when the edit differs from the prefill; an
empty value means «the `store/` files are the copy». A non-empty value **wins over `store/`** for
everything the script pushes, and `mode=plan` echoes its fields back in
`review-plan.json.listing.preview` («this is what goes up»). Writing the edited copy back into
`store/` and committing it is the project's choice — allowed, never required, and never done by
rcm. A preset without the input still works; `rcm check` warns and the edited copy stays in rcm.

Three invariants rcm checks on the preset definitions, because they are the last line of safety:

- the irreversible mode is never the default (`mode` defaults to `rehearsal` / `plan`);
- the `version` preset's `mode` defaults to `prefill` — `create` makes a store draft and
  `delete` cannot be undone, so both are choices;
- `play_managed_publishing` has exactly one confirming value, and rcm sends it only when the
  human ticked the box **in this submission**.

### Two store version names

One round may ship a **different version name to each store**, because the live names have drifted
apart (App Store 1.1.0 · Play 1.0.0 → 1.1.1 and 1.0.1). The build number is the one thing both
stores share, so it is what identifies the round; the version names are for humans to read.

rcm sends `build_name` as the **representative** name — the iOS name when there is one, the Android
name otherwise — and adds **`build_name_android`** (`review` · `upload`; string, default `""`) only
when the two differ. Equal names are exactly what rcm has always sent: `build_name` alone, with
`build_name_android` empty. The `plan` role never gets it; it reads the stores, and one name is
enough for that.

A script keeps its hook signatures and passes the **per-platform** name: the skeletons ask
`platform_build_name ios|android` and hand the answer to `store_upload` / `store_submit`, so Android
is uploaded as `1.0.1` in the same run that puts `1.1.1` on the App Store.

When the two names differ and the preset does not declare `build_name_android`, rcm refuses the
submission with **409 `split_version_unsupported`** instead of quietly building one name for both
stores. `rcm check` says it first, as a warning, so a project that always ships one name can leave
the input out and never meet the refusal. rcm knows the two names from a version draft, so this
applies to a `review` / `upload` request that carries a `version_id`; a request that types one
`build_name` is unchanged.

**The tag.** `tag = "prod/{version}-{build}"` does not change — `{version}` resolves per round:

| The round | `{version}` | Tag |
|---|---|---|
| both stores get one name | `1.1.1` | `prod/1.1.1-181` |
| the names differ | `1.1.1+1.0.1` (iOS `+` Android) | `prod/1.1.1+1.0.1-181` |

`+` is legal in a git tag name. A round that touches one store only uses that store's name alone.
rcm never creates the tag — the project's driver does (§5); rcm only reads whether it exists.

`listing.release_notes` resolves `{version}` into a path to one file, so it cannot join the two:
with different names it tries the iOS name first and falls back to the Android name.

### Artifact files (minimum fields)

Extra fields are kept and shown raw where the page has room. Missing fields render as `—`.

```jsonc
// plan.json — written however the run ends; n == null requires a non-empty blockers[]
{ "schema": 1, "build_name": "1.0.1", "n": 181, "first_release": false,
  "store": { "asc_live": "1.0.0", "asc_live_build": 180, "asc_editing": "1.0.1",
             "play": { "production": 180, "internal": 178, "production_name": "1.0.0" } },
  "next_version_hint": { "ios": "1.0.1", "android": "1.0.1" },   // optional, see below
  "blockers": [ { "code": "B-PLAYBUSY", "text": "…" } ], "warnings": [ … ],
  "measured_at": "2026-09-17T00:41:00Z" }
// Optional: store.play.production_name (the live Android version name) and next_version_hint {ios, android}.
// Without a hint rcm suggests the last number of asc_live / production_name + 1 (1.1.0 → 1.1.1); a name it
// cannot parse (1.0.0-rc1) gives no hint. The hint prefills the «new version» dialog; a human confirms it.

// version.json — after mode=create · delete, however the run ends. ios/android are null when that store was not
// touched; both null is accepted only with a non-empty error (why nothing happened: exit 3 already exists, …).
{ "schema": 1, "mode": "create",
  "ios": { "version": "1.1.1", "asc_version_id": "abc123", "state": "PREPARE_FOR_SUBMISSION" } | null,
  "android": { "version": "1.0.1" } | null,            // Play has no version object: the name only
  "error": null, "measured_at": "2026-09-18T02:00:00Z" }

// prefill.json — the live version's listing copy the new version starts from. Field keys are the ones the
// version page edits and sends back as listing_json; one locale (Q7), recorded in `locale`, not in the keys.
// `source` says where it came from ("asc_live:1.1.0 · play_listing", or "file:store/" for the repository fallback).
{ "schema": 1, "source": "asc_live:1.1.0 · play_listing", "locale": "ko",
  "ios": { "subtitle": "…", "promotional_text": "", "description": "…", "keywords": "…", "support_url": "…",
           "marketing_url": "…", "whats_new": "…", "screenshots": [ { "path": "store/screenshots/ios/ko/0.png" } ] } | null,
  "android": { "title": "…", "short_description": "…", "full_description": "…", "whats_new": "…", "graphics": [] } | null }

// report.json — QA verdict; verdict == FAIL requires failures[] to be non-empty
{ "schema": 1, "verdict": "PASS|FAIL|BLOCKED",
  "platforms": { "ios": { "verdict": "PASS", "chunks": 34, "failed": 0 }, "android": "not_run" },
  "failures": [ { "chunk_id": "…", "platform": "android", "rc": 1, "step": "…", "screenshot": "…" } ] }

// upload.json — mode == "rehearsal" ⇒ status == "rehearsal"
{ "schema": 1, "n": 181, "status": "success|partial|rehearsal", "mode": "upload",
  "platforms": ["ios", "android"], "tag": "prod/1.0.1-181" }

// review-plan.json — per-platform verdict words are yours; rcm colours a few it knows
{ "schema": 2, "build_name": "1.0.1", "n": 181, "plan_verdict": "ok|blocked",
  "ios": "ready", "android": "ready",
  "observed": { "ios": "PREPARE_FOR_SUBMISSION", "android": "completed", "auto_release": false },
  "listing": { "preview": ["…"], "diff": ["…"] }, "measured_at": "…" }

// review.json — after submit
{ "schema": 2, "overall_status": "submitted|partial|noop|failed",
  "platforms": { "ios": { "status": "submitted", "reason": null },
                 "android": { "status": "skipped", "reason": "managed_publishing_unconfirmed" } },
  "observed": { "ios": "WAITING_FOR_REVIEW", "android": "completed", "auto_release": false },
  "auto_release": false, "phased_release": true }
```

Verdict words rcm colours: `ready` green · `already_submitted` grey · `processing` blue dashed ·
`*_unreadable` amber · **`unsafe_release_type` red with a banner that cannot be dismissed** ·
anything else red with the word shown as-is. `observed.auto_release == true` also raises that banner.

---

## 3. Markers — what a running job tells the page

Any rcm job may print these at the start of a stdout line (see README «Presets and step markers»):

```
::rcm::steps::7                      # total steps — only if the script knows it
::rcm::step::scenarios (default)     # a step begins; the previous one ends
::rcm::step-end::ok|fail
::rcm::summary::PASS ios=PASS android=PASS
::rcm::fail::<name>
```

The Store tab adds one more, for progress **inside** a long step — a 40-minute chunk loop, a test
run redirected to a file, a device-lock wait:

```
::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]
```

- `done/total` — integers. Print only a denominator the script actually knows (declared chunks ×
  platforms, number of child processes, `0/1` → `1/1` for a lock). If you do not know it, do not
  print it: the page will fall back to a time-based bar and say so.
- `unit` — the one thing this line is about: `inquiry_photo/android`, `test`, `lock devices`.
- `state` — `run · ok · fail · skip · env · review · blocked · wait`.
- `note` — free text: `+1284 ~2 -0`, `rank 2 · 12m to start`.

The page keeps the last line per `unit`, so a grid of units (chunks per platform, children of a
parallel set) draws itself; the bar uses the newest `done/total`. Older rcm servers ignore the
line. Judges must never parse it — it is display only.

How the page builds a bar, in order of preference: `progress` marker → declared `steps::N` →
measured median duration for this preset (`by time · median 21m`) → hatched, no percentage.

---

## 4. Secrets

- Stored as files under `<config_dir>/secrets/<repo>/` (directory 0700, files 0600). Values
  typed in the Settings screen become one file each; uploaded files keep the profile's name.
- Jobs receive the folder path in `$<secrets_dir_env>` and nothing else. Your scripts read it the
  way they already read a local secrets folder — the point is zero script changes.
- The API returns `{name, kind, present, fingerprint, size, verified_at, verify_error}`. No value
  ever leaves the server; nothing is kept in the browser.
- `verify` runs read-only checks: `asc` (issue a token, read the app), `play` (read tracks),
  `github` (read the repo, report the login), `keystore` (SHA-1 fingerprint), or a preset you name.
- Job stdout is masked: any registered secret value of 8+ characters is replaced by `****`.
- The Store screen is gated: until every non-optional secret is present **and** verified, the
  route redirects to Settings and the API answers `409 setup incomplete`.

---

## 5. The driver

For a normal release round the page does not orchestrate presets itself; it runs your driver
non-interactively, the way a session machine would, and reads the stage back from remote truth:

```
<driver> --build-name 1.0.1 [--version-id 7] [--confirm-build-number 181] [--dry-run] [--retry] [--abort] [--status]
```

Expectations on the driver:

- `--version-id <id>` (optional, sent when the round starts from a version page): stage **`V`**
  reads the version row `GET /api/repos/<repo>/release/versions/<id>` with the `RCM_SERVER` /
  `RCM_TOKEN` the job receives and uses its `ios_version` (else `android_version`) as the build
  name — the name comes from the row, never from the driver; a `--build-name` given as well must
  be the same. `--status` prints `stage V …` while the row is not readable. A driver that does not
  know the flag is called the old way (`--build-name` only) and the page says the driver has no
  `V` stage — re-run `/rcm-release-driver`;
- **`--status` always prints one `stages:` line naming the stages that driver knows**, in order,
  alongside whatever else it prints — with `--version-id`, with `--build-name`, and with neither:

  ```
  stages: V S0 S1 S2 S3 S4 S5 S6 S7 S8
  ```

  That line is how rcm learns whether the driver knows the version work, and the only way: handing
  `--version-id` to a driver that does not know it dies with `unknown argument` and exit `2`, and
  exit `2` already means «needs the build number» and «environment blocked», so the exit code
  cannot tell the cases apart. rcm asks first — `--status` is read-only — and a driver whose list
  has no `V`, or that prints no such line at all (every driver written before this), is called the
  old way with `--build-name` only. The round still runs: rcm resolved the name from the version
  row before it started. `GET …/release/driver` carries the answer as `stages` (the parsed list,
  `null` when there is no line) and `knows_version_stage`, and the page turns a `false` into
  «this driver does not know the V stage — re-run `/rcm-release-driver`»;
- exit `2` and print `plan: N = <n>` when it needs the build number — rcm shows the dialog, the
  human **types** the number, rcm re-runs with `--confirm-build-number`; with the page's
  **Build number: Auto** toggle (default when `build_number_policy = "auto"`) rcm re-runs with
  that same `<n>` by itself, so the line is the only source of the number either way;
- `--status` is read-only and never creates branches or touches the store;
- every irreversible step (merge, upload, submit) is preceded by a confirmation of the plan's
  number — typed by a human, or (Auto) the plan's own `n` — never a number rcm computed; rcm
  passes only what the plan said or what a human typed in this session;
- stage is derivable from remote facts (PR for this sha, commit statuses, jobs for this sha, the
  exact tag) so a restarted server can pick the round up.

Projects without such a driver still get plan, gate, QA, upload, listing and review as single
buttons; only the `V S0`–`S8` stepper is missing, and the bottom sheet's bar then counts the jobs
that have run instead of a declared list of stages — so it shows no percentage (§6).

---

## 6. Rules the page enforces regardless of project

- **No release after approval.** There is no Release, Publish or Rollout button in any state.
  The page says so in a fixed sentence: on the version list, on the version page, beside the
  submit button in the bottom sheet, and under the review panel.
- **Submitting for review is one button in one place** — the bottom sheet of a version page. The
  review panel shows the two stores read-only and runs the two read-only commands
  («Validate listing», «Plan review»); it has no submit button, no checkboxes and no
  build-number box.
- **The build number is confirmed before every irreversible step** — the driver's merge, the
  upload, the review submission — and rcm never computes it. A human types the plan's `n`, or,
  with the page's **Build number: Auto** toggle, the server takes that same `n` from the plan and
  compares it. A checkbox never counts, and no plan means no number.
- **Android review needs a human statement** that managed publishing is on in the console,
  ticked per submission, never remembered. The page says the API cannot verify it.
- **Stale plans do not open irreversible buttons** (`plan_max_age_minutes`).
- **Bars say their basis, and show a percentage only against a denominator declared in advance**
  — the driver's `stages:` line. Without a driver the stage list grows as jobs run, so the bar is
  hatched and says so in words instead of counting.
- **A draft is held while work is in flight.** A version whose review job, `mode = upload` job or
  driver round is alive cannot be discarded (409 `version_running`), and the row says which one
  holds it.
- **Nothing project-specific in rcm.** Names, paths and field names live in the profile; the
  example profile ships in `examples/server.toml`.

---

## 7. Checklist for a new project

1. Scripts write `plan.json` / `upload.json` / `review-plan.json` / `review.json` (and
   `report.json` if there is QA) with the fields in §2, whatever else they contain.
2. Presets exist for each role and accept the inputs in §2; irreversible modes are not defaults.
3. Scripts print `::rcm::step::` markers; long steps print `::rcm::progress::` where a real
   denominator exists.
4. Secrets are read from one folder whose path arrives in an environment variable.
5. `[repos.<name>.release]` is written; `rcm check` is green.
6. Open the Store tab: Settings first (drop the files, verify), then Enter Store.
7. Run the read-only paths (plan, listing preview, review plan) before the first real round.

---

## 8. Skills — letting a session do it

`rcm skills list` names the skills the installed rcm ships; `rcm skills install --into <project>`
copies them into `<project>/.claude/skills/` (it refuses to overwrite a file the project changed
unless `--force`). In that project a session then runs:

| skill | tier | produces |
|---|---|---|
| `/rcm-connect` | entry point | asks project · repo name · platforms · which optional tiers; runs the rest in order; writes `docs/rcm-connect.md` |
| `/rcm-store-connect` | required | profile block, secrets list, presets `plan`/`upload`/`review` (+ the optional `version`), four script skeletons (`release_version.sh` for prefill / create / delete) + a JSON helper, each with `--selftest`; `listing_json` on review / upload |
| `/rcm-gate-connect` | optional | `gate` preset, step markers in the existing CI script, progress markers in its silent stretches |
| `/rcm-qa-connect` | optional | `qa` preset, `report.json` writer, per-unit progress markers, optional capture table |
| `/rcm-release-driver` | optional | `V S0`–`S8` driver skeleton with typed-N gates, `--status` (including its `stages:` line), retry/abort, and its judge with `--selftest` |

Skills generate skeletons with a working contract (inputs, markers, JSON, exit codes, selftests)
and leave the store calls as `TODO(project)` blocks; they never run upload or submit, and they never
write a project name into rcm.

