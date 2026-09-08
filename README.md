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
  <img src="https://raw.githubusercontent.com/monocsp/remote_ci_monitor/main/docs/images/ui/hero-queue.png" alt="The web queue: one job running with its steps, one waiting with an ETA, and the build machine's CPU, memory and GPU below" width="820">
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
- **Finds itself on the LAN.** Sessions on the same network need no address at all: the server
  advertises `_rcm._tcp` over mDNS/DNS-SD. Elsewhere any route works (Tailscale, a tunnel).
- **A web page for the people watching.** Static, no build step, no third-party assets, readable on
  a phone.

Build machines run macOS (Apple Silicon or Intel) and Linux. Windows is out of scope; sessions on
Windows submit through WSL.

Status: **M0–M5 done (v0.2.2)**. There is no GitHub backend and none is planned. `PLAN.md` (Korean)
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

On the same Wi-Fi or LAN as the build machine a session needs no address: leave `server` empty
(or `server = "auto"`) and rcm finds it, which `rcm discover` shows. From another network you need
a route of your own — Tailscale, or a tunnel — and then that address goes in `client.toml`.

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

## Session commands

| command | what it shows |
|---|---|
| `rcm run PRESET [-f k=v] [--ref REF] [--priority P] [--pool NAME] [--no-cache] [--by LABEL] [--no-join] [--no-wait] [--exclude PATTERN] [--dir DIR] [--timeout S] [--poll]` | snapshot → submit (joins an identical active job) → upload (only changed files when the server caches) → wait. `--ref` for `git_ref` presets: no snapshot, the server fetches the ref. `--priority low\|normal\|high`; `--no-cache` uploads a full tarball; `--no-join` never joins an identical job; `--exclude` adds an `.rcmignore` pattern; `--dir` snapshots another directory |
| `rcm wait --job N [--timeout S] [--poll]` | follows the job over the event stream, polls every 2 s if the stream is refused |
| `rcm eta PRESET [-f k=v] [--priority P] [--pool NAME] [--json]` / `rcm eta --job N` | queue position, jobs ahead, wait, expected duration, finish time and the confidence of that estimate; a job that is already running shows its state and elapsed time instead of a wait |
| `rcm top [--watch N] [--json]` | one screen: queue with reasons and ETAs, recent results, medians, host load (CPU · memory · GPU · top processes) |
| `rcm jobs [--mine] [--state S] [--pool NAME] [--json]` | queued, running and recent jobs; `--mine` needs your token and includes jobs you joined |
| `rcm logs N [--follow]` | the job log (your jobs, jobs you joined, or any job with an admin token) |
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
```

A session can send a job to another pool with `--pool`, but only to a pool the preset lists in
`pools`. Everything else the config can do — deploy presets that fetch a pushed ref, priorities,
the snapshot cache, remote workers, notifications, retention — is in
[Configuration](docs/configuration.md).

## Web UI

Open `http://<build-machine>:8787/` in any browser, phone included: three static files served by
`rcm serve`, no build step and no third-party assets. The first screen answers three questions at
a glance — **Your jobs** (paste your token with 🔑), **Not moving** (only actionable causes, worst
first) and **Host pressure** — and below them sit the queue with its reasons and ETA confidence,
the host card, recent results and how the estimates were computed.

Updates arrive over the event stream; if it drops, the page polls every 10 s, and after 30 s
without a successful response a **Lost connection** banner appears while the ages keep counting.
The page never pretends to be current. `#/jobs/N` deep-links to a job.

## Exit codes

| `rcm wait` exit | meaning |
|---|---|
| 0 | job succeeded |
| 1 | job failed (see `failed_step` and `summary` in the JSON) |
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
