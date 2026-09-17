<h1 align="center">remote_ci_monitor</h1>

<p align="center">
  <b>Your team already owns a build machine. This turns it into a CI queue.</b><br>
  Submit a preset from any computer, watch the queue, get an exit code back.
</p>

<p align="center">
  <a href="https://github.com/monocsp/remote_ci_monitor/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/monocsp/remote_ci_monitor/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/monocsp/remote_ci_monitor/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/monocsp/remote_ci_monitor?sort=semver"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="Runtime dependencies: none" src="https://img.shields.io/badge/runtime%20deps-0-success">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/github/license/monocsp/remote_ci_monitor"></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#build-machine-3-commands">Quick start</a> ·
  <a href="docs/usage.md">Usage guide</a> ·
  <a href="docs/configuration.md">Configuration</a> ·
  <a href="CHANGELOG.md">Changelog</a><br>
  <b>English</b> · <a href="README.ko.md">한국어</a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/monocsp/remote_ci_monitor/main/docs/images/ui/hero-queue.png" alt="The web queue: one job running with its steps, one waiting with an ETA, and the build machine's CPU, memory, disk and GPU below" width="820">
</p>

A team that shares **one build machine** ends up queueing by hand: who is running what, is it stuck
or just slow, is the machine on fire, did it pass? rcm answers that in one screen and one exit code.

- **Nothing else to run.** One package, zero runtime dependencies, Python 3.11+ standard library
  only — the same package is the server and the client. No GitHub, no broker, no database server.
- **Your tree, not your last push.** `rcm run` uploads the working directory as it is, uncommitted
  changes included, so a green result means *this* tree passed. Deploy presets run a pushed ref
  instead.
- **It says when it does not know.** Exit code 3 is *unknown* (server restarted, unreachable,
  timed out) and is never dressed up as a failure. ETAs carry their confidence; missing numbers
  print as `—`, not `0`.
- **The files come back.** A preset can declare what it produces
  (`artifacts = ["test/**/goldens/*.png"]`); `rcm run --fetch-artifacts` writes those files into the
  same paths in your tree. A file you edited while waiting is never overwritten.
- **Finds itself on the LAN.** Sessions on the same network need no address at all: the server
  advertises `_rcm._tcp` over mDNS/DNS-SD. Elsewhere any route works (Tailscale, a tunnel).
- **A web page for the people watching.** Static, no build step, no third-party assets, readable on
  a phone.

Build machines run macOS (Apple Silicon or Intel) and Linux. Windows is out of scope; sessions on
Windows submit through WSL.

Status: **M0–M5 done (v0.2.3)**. There is no GitHub backend and none is planned. `PLAN.md` (Korean)
is the plan of record; `CHANGELOG.md` is what changed.

## Install

Needs Python **3.11.4+** (the safe `tarfile` filter) on both machines. One package, no runtime
dependencies:

```sh
pipx install git+https://github.com/monocsp/remote_ci_monitor   # from git (main); add @dev for the dev branch
pipx install remote-ci-monitor                      # from PyPI (not published yet — see CONTRIBUTING.md)
uvx --from remote-ci-monitor rcm version            # or run it through uv without installing
```

No `pipx` yet? `python3 -m pip install --user pipx && python3 -m pipx ensurepath`, then open a new
shell. If `rcm` is "command not found" right after installing, `~/.local/bin` is not on your `PATH`
yet — run `pipx ensurepath` and open a new shell. A release wheel from the GitHub Releases page
also installs with `pipx install <wheel-url>`.

## Build machine (3 commands)

<!-- every `rcm …` line between smoke:begin and smoke:end is executed by scripts/smoke_install.sh — keep them in sync -->
<!-- smoke:begin -->
```sh
rcm init server            # writes ~/.config/rcm/server.toml — edit presets and the bind address
rcm token add laptop       # prints the token ONCE; hand it to that session machine
rcm serve                  # http://127.0.0.1:8787 · Ctrl-C or SIGTERM stops it cleanly
```
<!-- smoke:end -->

The generated config ships a harmless `ok` preset, so you can prove the path end to end before
writing your own. To accept sessions from other computers set `bind` to the machine's LAN or
Tailscale address (or `0.0.0.0`) and, on macOS, allow Python through the firewall prompt; check
from another computer with `curl http://<build-machine>:8787/api/health`. A server bound off
loopback also **advertises itself** on the network (`_rcm._tcp`, mDNS/DNS-SD — `advertise = false`
turns it off, `advertise_name` renames it), so sessions there need no address at all.

Tokens: `rcm token add ops --admin` makes an admin token (pause/resume, cancel any job, read any
log); `rcm token list` never shows secrets; `rcm token revoke NAME`. Another port:
`rcm serve --port 8790` (or `port = …` in the config). Set `public_url` when you bind to `0.0.0.0`
so the job URLs printed by `rcm run` open from other computers.

Keep it running across logins and reboots with the launchd and systemd examples in
[Operating the build machine](docs/operating.md#run-as-a-service).

## Session machine (3 commands)

A session needs two things: **where** the server is, and **who you are**. Only the first one can
be automatic.

On the same Wi-Fi or LAN as the build machine, leave `server` empty (or `server = "auto"`) and rcm
finds it, which `rcm discover` shows. That needs rcm **0.2.2 or newer on both sides** — an older
client has no discovery and stops with `no server configured`, and unlike `rcm worker` it never
compares its version with the server's. From another network you need a route of your own —
Tailscale, or a tunnel — and then that address goes in `client.toml`.

The token is the other half, and it is deliberately manual. `rcm token add` runs **on the build
machine** and writes straight to the server's database; there is no API that hands a token out, and
there will not be one. So on a machine that has just found the server, `rcm check` showing a green
**server** row and a red **token** row is not a bug — copying the token over is the one step left.

<!-- smoke:begin -->
```sh
rcm init client --server http://<build-machine>:8787   # ~/.config/rcm/client.toml (mode 600)
export RCM_TOKEN=<token from rcm token add>            # or put it in that file as token = "…"
rcm check                  # python · server · token · presets · timezone must all say ok
cd ~/src/app               # any project directory — rcm run uploads the *current directory*
rcm run ok                 # first job: exit 0 and one JSON line means everything works
rcm top                    # queue, ETAs, recent results, host load
```
<!-- smoke:end -->

Then run real work from a project directory — `rcm run gate -f scope=full` — and branch on `$?`:
0 succeeded · 1 failed · 2 cancelled or timed out · 3 unknown. `examples/session/ci-gate.sh` is a
ready-made wrapper (needs `jq`).

`rcm run` snapshots the **current directory** (git-tracked plus untracked-but-not-ignored files,
minus `.rcmignore` patterns), uploads only what the server does not already have, waits, and prints
one JSON line on stdout. Progress goes to stderr. Ctrl-C detaches: the job keeps running,
`rcm wait --job N` picks it back up and `rcm cancel N` stops it. The workspace on the build machine
has **no `.git`** — scripts that need history belong in a `git_ref` preset.

**New here? The [usage guide](docs/usage.md) walks through all of this with annotated
screenshots.**

## Running a gate from a session

A **gate** is the preset your build machine runs before a merge — analysis, tests and lint in one
script, usually with a `scope` input for a full or a quick pass. This is the whole loop from a
project directory on your machine; every command works the same from a terminal, a CI wrapper or
an agent session.

1. **Check once.** `rcm check` — `python`, `server`, `token`, `presets`, `timezone`, `client` and
   `cancel` must all say `ok`. If `presets` does not list `gate`, the server owner has not defined
   it yet ([Configuration](docs/configuration.md)); `rcm presets` shows every preset with its
   inputs.
2. **Look before you queue.** `rcm eta gate -f scope=full` prints your position, the jobs ahead,
   the wait, the expected duration, the finish time and the `confidence` of that estimate —
   without submitting anything.
3. **Submit from the repository root.** `rcm run gate -f scope=full --by "$(whoami)@$(hostname -s)"`
   snapshots the current directory (tracked files plus untracked files that are not ignored, minus
   `.rcmignore`), joins an identical job that is already queued or running, uploads only the files
   the server has not seen, and waits. Progress — one line per `::rcm::step::` — goes to stderr;
   the result is one JSON line on stdout. Inputs are `-f name=value`; `--by` is the name the queue
   shows (default `user@host`). A gate that needs git history runs from a pushed ref instead:
   `rcm run gate --source git_ref --ref my-branch` uploads nothing, so push first.
4. **Branch on the exit code.** 0 succeeded · 1 failed · 2 cancelled or timed out · 3 unknown,
   which is never a failure ([Exit codes](#exit-codes)). The JSON carries `job_id`, `url`, `state`,
   `exit_code`, `summary`, `failed_step` (only when the script declared it), `last_step`,
   `failures` (each name the script reported, with how often it was red in the recent runs of this
   preset) and `step_timeline`. `examples/session/ci-gate.sh` is a complete wrapper (needs `jq`).
5. **When it is red.** `rcm logs N` shows the log (`--follow` while it runs); the verdict line
   names the failed step when your script declared one and `last step …` otherwise;
   `rcm artifacts N --fetch` brings back the reports the preset published. Exit 3 means rcm does
   not know — the server restarted or you lost the route — so look at `rcm jobs` before you retry.
6. **Detach and come back.** `--no-wait` prints the job number, its position and its ETA and
   returns; `rcm wait --job N` follows it later. Ctrl-C during `rcm run` detaches too — the job
   keeps running.
7. **Cancel.** `rcm cancel N` stops your own job (`rcm run` saved the submission's cancel token in
   `~/.local/state/rcm/submissions.json`); if you only joined someone else's job it removes you
   from the list and the job goes on. Admin tokens cancel anything.
8. **Two sessions, one commit.** A second `rcm run` with the same preset, inputs and tree joins
   the first job and gets the same result; `--no-join` forces a separate run.
9. **Faster passes.** If the preset defines a lighter scope (`-f scope=fast` · `commit`), use it
   while iterating and keep `full` for the merge; `rcm eta` says what each costs right now.
10. **Keep the client current.** `rcm check` fails its `client` row when the server requires a
    newer client; `examples/session/update-client.sh` installs the server's own wheel.

The [usage guide](docs/usage.md) shows each of these with annotated screenshots.

## Session commands

| command | what it shows |
|---|---|
| `rcm run PRESET [-f k=v] [--ref REF] [--priority P] [--pool NAME] [--no-cache] [--by LABEL] [--no-join] [--no-wait] [--exclude PATTERN] [--dir DIR] [--timeout S] [--poll]` | snapshot → submit (joins an identical active job) → upload (only changed files when the server caches) → wait. `--ref` for `git_ref` presets: no snapshot, the server fetches the ref. `--priority low\|normal\|high`; `--no-cache` uploads a full tarball; `--no-join` never joins an identical job; `--no-wait` returns as soon as the job is queued and prints its position and ETA; `--exclude` adds an `.rcmignore` pattern; `--dir` snapshots another directory |
| `rcm wait --job N [--timeout S] [--poll]` | follows the job over the event stream, polls every 2 s if the stream is refused |
| `rcm eta PRESET [-f k=v] [--priority P] [--pool NAME] [--json]` / `rcm eta --job N` | queue position, jobs ahead, wait, expected duration, finish time and the confidence of that estimate; a job that is already running shows its state and elapsed time instead of a wait |
| `rcm top [--watch N] [--json]` | one screen: queue with reasons and ETAs, recent results, medians, host load (CPU · memory · disk · GPU · top processes) |
| `rcm jobs [--mine] [--state S] [--pool NAME] [--json]` | queued, running and recent jobs; `--mine` needs your token and includes jobs you joined |
| `rcm logs N [--follow]` | the job log (your jobs, jobs you joined, or any job with an admin token) |
| `rcm artifacts N [--fetch --output DIR] [--force] [--resume]` | what the job produced, and fetching it. `rcm run --fetch-artifacts` does it in one step, into the tree you submitted |
| `rcm presets [--json]` | presets the server offers and their inputs |
| `rcm discover [--json] [--timeout S]` | rcm servers on this network (mDNS); `rcm check` says `(found on this network)` when it used one |
| `rcm cancel N` · `rcm pause` · `rcm resume` | cancel (joiners only leave the join list) · pause/resume the queue (admin) |
| `rcm bump N [--priority high]` | change a waiting job's priority (admin) |

Every estimate carries a `confidence`: `high` (median of ≥ 5 real runs), `med` (fewer), `low`
(a preset or default guess), `group wait` (blocked by a concurrency group) or `overdue`. Unknown
values print as `—`, never as `0`.

## Presets and step markers

The server only runs **presets** from its config. A session sends a preset name and inputs; inputs
arrive as `RCM_INPUT_<NAME>` environment variables, never spliced into a command line.

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]      # runs from the uploaded workspace root
pool = "default"                        # worker pool for this preset's jobs (default "default")
pools = []                              # extra pools a session may choose with --pool
timeout_seconds = 1200
expected_seconds = 480                  # used until enough real runs exist
[[presets.inputs]]
name = "scope"
type = "choice"
choices = ["full", "commit", "fast"]
default = "full"
```

Your script reports progress by printing markers at the start of a line:

```
::rcm::steps::3            # optional: total step count
::rcm::step::analyze       # a new step starts (the previous one ends)
::rcm::step-end::ok        # optional: "ok" or "fail"
::rcm::summary::all green  # optional: one-line result shown in the queue
::rcm::progress::41/68::inquiry_photo/android::run   # optional: progress inside the current step
```

`::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]` is for a long step — a chunk loop, a
parallel set, a lock wait. Print only a denominator the script knows; the queue draws the bar from
it and a grid of the units it has seen. Details in [Configuration](docs/configuration.md#progress-inside-a-step).

A session can send a job to another pool with `--pool`, but only to a pool the preset lists in
`pools`. Everything else the config can do — deploy presets that fetch a pushed ref, priorities,
the snapshot cache, remote workers, notifications, retention — is in
[Configuration](docs/configuration.md).

## Web UI

Open `http://<build-machine>:8787/` in any browser, phone included: three static files served by
`rcm serve`, no build step and no third-party assets. The first screen answers three questions at
a glance — **Your jobs** (paste your token with the key button), **Needs a look** (only actionable
causes, worst first) and **Host pressure** — and below them sit the queue with its status column
and ETA confidence, the host card, recent results and how the estimates were computed. A job that
has merely gone quiet is not listed as needing a look; see
[Configuration](docs/configuration.md#estimates-and-when-a-job-is-called-not-responding).

Updates arrive over the event stream; if it drops, the page polls every 10 s, and after 30 s
without a successful response a **Lost connection** banner appears while the ages keep counting.
The page never pretends to be current. `#/jobs/N` deep-links to a job.

When a repository in `server.toml` has a `[repos.<name>.release]` profile, the header gains a
**Queue | Store** switch and `#/store/<name>` opens the Store tab; without a profile there is no
tab. The Store is behind a **settings gate**: until every required secret is present and verified
you land on the Settings screen — a red banner with `n of m secrets set · k verified`, one row per
secret from the profile (drop a file, or type a value once in a password box), **Verify all**, and
a disabled **Enter Store** — and the browser never sees a value, only `present`, a fingerprint and
the verification time. Once complete, the Store shows four collapsible rows — Setup, Source (mirror
age, `main` / `dev`, whether `main` is in `dev`, **Fetch remote**), Build · upload and Store — green
when fine and collapsed, red and open when something needs a hand, grey when this build has nothing
to say. Below them the review panel lays out the App Store and Google Play sections in the same group
order (screenshots · copy with `current/limit` counters · release notes · build/release · review
information, `—` where a store has no such field), and **Submit for review** opens only after a
green review plan, the build number typed again, and — when Google Play is selected — the
managed-publishing box ticked for this submission; there is no Release, Publish or Rollout button in
any state, and an `unsafe_release_type` verdict raises a red banner that cannot be dismissed. When the
profile names a driver, the Build · upload row carries the round's stepper S0–S8 read from the
driver's own status lines (a Start form with version · Android track · dry-run when nothing runs,
**Abort** while it runs, **Retry same version** after exit 1, «result unknown — do not resubmit»
after exit 3, store drift after exit 4), the S2 dialog asks for the build number typed again before
`confirm` goes out, a **Rehearsal (no upload)** button runs the upload preset in rehearsal mode —
the real upload is the driver's S7 and there is no upload button — and the Source row shows the
mirror's last five commits, its tags and «PR list: next».

### Store tab — connecting a project's release flow

Beyond the queue, a repository can get a **Store tab**: fetch, store snapshot, gate, QA, upload,
submit for review, each a button over the project's *own* release scripts. rcm draws the JSON
those scripts write and runs the presets they declare; it never computes a build number, judges
QA, or releases anything after store approval. What a project must provide is written down in
[docs/release-contract.md](docs/release-contract.md); the fastest way to provide it is the skills
rcm ships for a Claude Code session in that project:

```sh
rcm skills list                                   # the skills packaged with this rcm
rcm skills install --into ~/src/app               # copies them to ~/src/app/.claude/skills/
# in that project, in Claude Code:
/rcm-connect                                      # asks project · repo name · platforms · optional tiers
```

`/rcm-connect` runs `rcm-store-connect` (required: profile block, secrets list, `plan` / `upload`
/ `review` presets and script skeletons, each with a `--selftest`) and, if chosen, the optional
`rcm-gate-connect`, `rcm-qa-connect` and `rcm-release-driver`. Skills write only into the
project (`scripts/rcm/presets.release.toml`, `scripts/rcm/profile.release.toml`, scripts, a report
in `docs/rcm-connect.md`) and verify with `rcm check` on a candidate copy of `server.toml`; the
live server file, secrets and the stores are never touched. The profile keys are in
[Configuration](docs/configuration.md#release-profile).

## Exit codes

| `rcm wait` exit | meaning |
|---|---|
| 0 | job succeeded |
| 1 | job failed (see `summary`, plus `failed_step` when the script declared one and `last_step` otherwise) |
| 2 | cancelled or timed out |
| 3 | **unknown**: lost after a server restart, server unreachable, or `--timeout` elapsed. Never treated as a failure. |

Usage errors and validation failures that never reach the server also exit 2, including `rcm run`
when the server cannot be reached before submit. `rcm wait` gives an unreachable server 60 s to
come back (printing `reconnecting…`) before exiting 3; `rcm eta`, `rcm jobs` and `rcm top` fail
fast with `cannot reach <url>` and exit 3.

## Security notes

- Every write (submit, upload, cancel) needs a bearer token, stored as a SHA-256 hash. Tokens have
  a kind: `client`, `admin` (cancel any job, pause, bump) and `worker` (`/worker/*` only).
- Only configured presets run. No shell interpolation. Uploads are extracted with Python's
  `tarfile` data filter — no absolute paths, no `..`, no links outside the workspace.
- Discovery answers carry only the server name, port, version, lane count and LAN addresses —
  never tokens, presets or paths. Anyone on the LAN can learn that a build server exists.
- The server binds to `127.0.0.1` unless you say otherwise, and it does **no TLS**: put it behind
  Tailscale or a TLS proxy. Reads are open on that network by default (`read_auth = "basic"`
  password-protects them), and job logs always need the job's token or an admin token.
- Run it as a dedicated OS user without sudo, and keep build secrets in files on the build machine
  that presets read — never send them in a job.

The full list, including what the web UI keeps in `localStorage`, is in
[Operating the build machine](docs/operating.md#security-notes).

## Documentation

| document | what is in it |
|---|---|
| [Usage guide](docs/usage.md) · [한국어](docs/usage.ko.md) | a first job, step by step, with annotated screenshots |
| [Release contract](docs/release-contract.md) | what a project provides for the Store tab: profile, presets by role, artifact files, markers, secrets, the driver, and the skills that generate them |
| [Configuration](docs/configuration.md) | presets, inputs, markers, deploy presets, priority, snapshot cache, pools and remote workers, notifications, the client and worker files |
| [Operating the build machine](docs/operating.md) | running it as a service, Docker, upgrades, security, why a number can be wrong, the manual check on a real machine |
| [CHANGELOG.md](CHANGELOG.md) | every user-visible change, newest first |
| [CONTRIBUTING.md](CONTRIBUTING.md) | development, house rules, branches, releasing |
| `PLAN.md` (Korean) | the plan of record: scope, decisions, milestones |

## Contributing

Bug reports and pull requests are welcome. `main` and `dev` are protected: branch from `dev` and
open the pull request against `dev`. Run `ruff check . && pytest` before you push, keep runtime
dependencies at zero, and update the documentation in the same pull request. Details in
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
