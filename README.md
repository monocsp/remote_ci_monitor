# remote_ci_monitor

A local job server for a team that shares **one build machine**. Sessions on any computer submit a
preset (`rcm run gate`); the server queues and runs them one at a time, shows queue position,
ETA, step progress and host load, and hands the result back as an **exit code**.

- No GitHub dependency. Runs on your LAN or Tailscale.
- Runtime dependencies: **zero** (Python 3.11+ standard library only). Same package for server and client.
- Build machines: macOS (Apple Silicon and Intel) and Linux. Windows is out of scope (sessions on Windows can still submit through WSL).
- Sessions upload their **working tree as it is** (uncommitted changes included), so a green gate
  means *this* tree passed.

Status: **M0–M4 done (v0.1.0)** — server, queue, worker, live events, web UI, `git_ref` deploys,
retention, service files, packaging and release. M5 (several build machines, GitHub backend) is
next. The plan lives in `PLAN.md` (Korean); changes in `CHANGELOG.md`.

## Install

Needs Python **3.11.4+** (the safe `tarfile` filter) on both machines. One package, no runtime
dependencies:

```sh
pipx install remote-ci-monitor                      # from PyPI
uvx --from remote-ci-monitor rcm version            # or run it through uv without installing
pipx install git+https://github.com/monocsp/remote_ci_monitor   # from git (main); add @dev for the dev branch — use this before the first PyPI release
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

The generated config ships a harmless `ok` preset so you can prove the path end to end before
writing your own presets. To accept sessions from other computers set `bind` to the machine's
Tailscale/LAN address (or `0.0.0.0`) and, on macOS, allow Python through the firewall prompt.
Check from another computer with `curl http://<build-machine>:8787/api/health`. For a service that
survives logins and reboots see [Run as a service](#run-as-a-service).

## Session machine (3 commands)

<!-- smoke:begin -->
```sh
rcm init client --server http://<build-machine>:8787   # ~/.config/rcm/client.toml (mode 600)
export RCM_TOKEN=<token from rcm token add>            # or put it in that file as token = "…"
rcm check                  # python · server · token · presets · timezone must all say ok
rcm run ok                 # first job: exit 0 and one JSON line means everything works
rcm top                    # queue, ETAs, recent results, host load
```
<!-- smoke:end -->

Then run real work from a project directory: `cd ~/src/app && rcm run gate -f scope=full`, and
branch on `$?` — 0 succeeded · 1 failed · 2 cancelled/timed out · 3 unknown.

`rcm run` snapshots the current directory (git-tracked + untracked-but-not-ignored files, minus
`.rcmignore`), uploads it over the same HTTP connection, waits, and prints one JSON line on stdout.
Progress goes to stderr. Ctrl-C detaches — the job keeps running; resume with `rcm wait --job N`,
stop it with `rcm cancel N`.

## Presets and step markers

The server only runs **presets** from its config. A session sends a preset name and inputs; inputs
arrive as `RCM_INPUT_<NAME>` environment variables (never spliced into the command line).

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]      # runs from the uploaded workspace root
timeout_seconds = 1200
expected_seconds = 480                  # used until enough real samples exist
duration_key_inputs = ["scope"]
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
```

Child processes buffer stdout, so markers may arrive late. Use `PYTHONUNBUFFERED=1`, `stdbuf -oL`,
or `flutter --no-color` style flags in your scripts when timing matters. Job elapsed time is always exact.

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

## Session commands

| command | what it shows |
|---|---|
| `rcm run PRESET [-f k=v] [--ref REF] [--no-wait] [--poll]` | snapshot → submit (joins an identical active job) → upload → wait. `--ref` for `git_ref` presets: no snapshot, the server fetches the ref |
| `rcm wait --job N [--timeout S] [--poll]` | follows the job over the event stream, polls every 2 s if the stream is refused |
| `rcm eta PRESET [-f k=v]` / `rcm eta --job N` | queue position, jobs ahead, wait, expected duration, finish time and the confidence of that estimate; a job that is already running shows its state and elapsed time instead of a wait |
| `rcm top [--watch N] [--json]` | one screen: queue with reasons and ETAs, recent results, medians, host load (CPU · memory · GPU · top processes) |
| `rcm jobs [--mine] [--state S]` | queued, running and recent jobs; `--mine` needs your token and includes jobs you joined |
| `rcm logs N [--follow]` | the job log (your jobs, jobs you joined, or any job with an admin token) |
| `rcm presets` | presets the server offers and their inputs |
| `rcm cancel N` · `rcm pause` · `rcm resume` | cancel (joiners only leave the join list) · pause/resume the queue (admin) |

Every estimate carries `confidence`: `high` (median of ≥ 5 real runs), `med` (< 5), `low` (preset or default guess), `group wait` (blocked by a concurrency group) or `overdue`. Unknown values print as `—`, never as 0.

## Web UI

Open `http://<build-machine>:8787/` in a browser (phone included). No build step, no third-party
assets — three static files served by `rcm serve`. The first screen answers three questions in
one glance: **Your jobs** (needs your token), **Not moving** (only actionable causes, worst first:
worker down → likely stuck → upload stalled → not scheduled → blocked by a concurrency group →
overdue → paused) and **Host pressure**. Below that: the queue with a *Reason* column and ETA
confidence badges, the host card (CPU · memory · GPU · 5-minute sparklines), recent results and how
estimates are computed.

- Updates arrive over the event stream; if it drops, the page polls every 10 s and reconnects with
  backoff. After 30 s without a successful response a **Lost connection** banner appears and the
  ages keep counting — the page never pretends to be current.
- Paste your token with the 🔑 button to highlight your jobs, see log tails, open full logs and
  cancel. Only a 401/403 clears a saved token; network errors keep it.
- `#/jobs/N` deep-links to a job. `?poll=1` disables the event stream (polling only), `?debug=1`
  prints layout diagnostics in the footer — both are for troubleshooting.
- Dark/light follow the system. Below 720 px the queue turns into cards.

## Exit codes

| `rcm wait` exit | meaning |
|---|---|
| 0 | job succeeded |
| 1 | job failed (see `failed_step` and `summary` in the JSON) |
| 2 | cancelled or timed out |
| 3 | **unknown**: lost after a server restart, server unreachable, or `--timeout` elapsed. Never treated as a failure. |

Usage errors and validation failures that never reach the server exit with 2 as well.

## Security notes

- Every write (submit, upload, cancel) needs a bearer token. The server stores only a SHA-256 of it.
- Only configured presets run. No shell interpolation. Uploads are extracted with Python's
  `tarfile` data filter (no absolute paths, no `..`, no links outside the workspace).
- The server binds to `127.0.0.1` unless you set `bind`. It does not do TLS — put it behind
  Tailscale or a TLS proxy. Reads (`/api/status`) are open by default on the assumption of a
  private network; job logs always need the job's token or an admin token.
- Run the server as a dedicated OS user without sudo. Keep build secrets in files on the build
  machine that your preset scripts read; never send them in a job.
- The web UI keeps your client token in the browser's `localStorage` (never in the URL). Do not
  paste it into a shared or public browser; a cross-site-scripting bug would expose it, which is why
  the page ships with a strict Content-Security-Policy and loads nothing from third parties.

### `read_auth = "basic"` — password-protect reads

By default anyone who can reach the port can read the queue (`/`, `/api/status`, `/events`).
Set `read_auth = "basic"` to require credentials for reads too. There is no separate user
database: the browser prompt takes the **token name as the username and the token as the
password** (`rcm token add alice` → user `alice`). API clients keep sending `Authorization: Bearer`.

- Basic is sent in clear text — use it **only behind TLS** (Tailscale HTTPS, Caddy, nginx).
- Writes (`POST /jobs`, uploads, cancel, pause) accept **Bearer only**. Browsers attach Basic
  credentials automatically, so allowing them on writes would let any page on your intranet submit
  or cancel jobs (CSRF).
- Browsers cannot "log out" of Basic auth: closing the tab is not enough. Quit the browser, use a
  separate profile, or `rcm token revoke` the token.
- `/api/health` stays open (no secrets in it) for monitoring.

### Retention

The server deletes job logs, snapshots and kept workspaces after `retention_days_success`
(default 14) / `retention_days_failure` (30) days, and the job records themselves after
`metadata_retention_days` (180, must be ≥ `estimate.sample_days`). A sweep runs at start and then
every `retention_sweep_interval_seconds` (3600). Running jobs are never touched; `rcm logs N` on a
purged job answers `log expired`. Git mirrors are never pruned. If the sweeper thread dies,
`/api/health` turns 503 — nothing here fails silently.

## Run as a service

Keep `rcm serve` alive across logins and reboots with the example units in `examples/`:

- **macOS (launchd)** — `examples/launchd/com.remote-ci-monitor.server.plist`. Edit the paths,
  copy it to `~/Library/LaunchAgents/` of the dedicated `rcm` user and run
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.remote-ci-monitor.server.plist`
  (`launchctl bootout gui/$(id -u)/com.remote-ci-monitor.server` to stop). Logs go to
  `~/Library/Logs/rcm/server.log`. launchd does not expand `~`, so every path in the file is absolute.
- **Linux (systemd)** — `examples/systemd/rcm-server.service`. Copy to `/etc/systemd/system/`,
  then `sudo systemctl daemon-reload && sudo systemctl enable --now rcm-server`;
  `journalctl -u rcm-server -f` shows the log.
- Both send **SIGTERM** on stop: the server shuts down cleanly and jobs that were running are
  marked `lost` (exit 3 for waiting sessions); queued jobs survive and start after the restart.
- The `PATH` in the unit is what presets inherit (`env_passthrough`) — add Homebrew and your
  toolchains there. Keep the machine awake (`pmset -a sleep 0` on macOS).

## Docker (Linux build machine)

`Dockerfile` builds a server image (`python:3.12-slim` + git for `git_ref` presets, non-root user
`rcm`, config at `/config/server.toml`, data volume `/data`, port 8787). macOS build machines should
use launchd instead — the toolchains live outside containers there.

```sh
docker build -t rcm .
docker run -d --name rcm -p 127.0.0.1:8787:8787 \
  -v rcm-data:/data -v "$PWD/server.toml:/config/server.toml:ro" rcm
docker exec rcm rcm token add laptop --data-dir /data
```

Publish the port on `127.0.0.1` or a Tailscale IP only. Inside a container `ps` and `/proc` see
just the container, so **Host pressure** is less accurate than with a native service, and GPU
numbers need an NVIDIA base image plus `--gpus all`. Your presets' toolchains must be in the image.

## Why the numbers can be wrong

- ETA source `default`/`preset` means no measurements yet; `measured n=7` is the median of 7 real runs.
- Step counts marked "so far" come from scripts that did not declare `::rcm::steps::N`.
- Step timestamps are **receive** times (`timing: "as_received"`), so buffered output shifts them.
- A `lost` job died with the server; it is left as `lost`, never silently re-queued or deleted.
- When the queue is paused or every worker lane is down, ETAs are `null` on purpose.
- Host samples are polled (default every 5 s). `stale` means the last sample is older than 3 intervals; `hosts_error` means the sampler itself failed. Memory "used" on macOS is `active + wired + compressed` (what Activity Monitor calls Memory Used), which is smaller than `top`'s PhysMem used. Memory is shown in GiB under the `GB` label, like Activity Monitor and `free -h`, so a 24 GB machine reads `24.0 GB`. GPU numbers come from `ioreg` (Apple Silicon) or `nvidia-smi`; on other machines the GPU shows `unavailable` with the reason.

## Verify on the real build machine

The loopback e2e test proves the flow on one machine. Checking the M1 goal ("another computer submits over Tailscale and sees position, ETA, steps and GPU") is done by hand:

1. On the build machine: `rcm serve --bind <tailscale-ip>` (or `bind = "0.0.0.0"` in `server.toml`). The server warns when it binds off-loopback with open reads — that is expected inside Tailscale.
2. `rcm token add <laptop-name>` on the build machine; copy the token to the laptop as `RCM_TOKEN`.
3. On the laptop: `RCM_SERVER=http://<tailscale-ip>:8787 rcm check` — server, token, presets and timezone must all say `ok`.
4. `rcm run gate --no-wait` from a project checkout on the laptop, then `rcm top` in another terminal: the job must show its position or `running`, an ETA with a confidence badge, and the current step once the script prints markers.
5. `rcm top` host line: CPU, memory and load must be numbers, `sampled Ns ago` must stay small. GPU shows a percentage on Apple Silicon or NVIDIA machines; elsewhere it must say `unavailable` with a note (that still passes).
6. `rcm wait --job N` from a third terminal must update on the event stream (no 2 s polling gaps) and exit with the job's code.
7. From a second session on the same tree, `rcm run gate` must print `joined job #N`, and `rcm jobs --mine` with that session's token must list the job.
8. Kill the server with the job running, restart it: `rcm wait` must exit 3 with `lost`, and a job that was queued must run afterwards.
9. Web UI: open `http://<tailscale-ip>:8787/` on the phone. The queue, the running job's steps and the host card must be readable in one column; paste the laptop token via 🔑 and confirm **Your jobs** lists the job and **Log** opens. Stop the server: within ~30 s the **Lost connection** banner must appear at the top; start it again and the banner must go away by itself. Then pause only the sampler's view of the world — `kill -STOP <server pid>` for 20 s and `kill -CONT` — and the host card must show a `stale` badge briefly (the queue keeps working). (`kill -9` cannot signal the job's process group, so the script itself keeps running as an orphan until it ends on its own; a normal stop — SIGTERM or Ctrl-C — terminates it.)

10. M3 items: a `git_ref` preset against your real remote — `rcm run deploy --ref <branch>` must
    print the pinned sha and the job log must show `[rcm] fetching … from <repo>` (ssh keys or a
    credential helper must be set up for the server's OS user). With `read_auth = "basic"` behind
    your TLS proxy, the browser must prompt and accept token name + token. With
    `retention_days_success = 0` a finished job's log must answer `log expired` after the next
    sweep while the job stays in **Recent**.

## Releasing

Releases come from `main`, and `main` only takes PRs from `dev`:

1. Bump `__version__` in `src/remote_ci_monitor/__init__.py` and add the section to
   `CHANGELOG.md` (on a feature branch → PR to `dev`).
2. `gh pr create --base main --head dev`, wait for `test` and `main-from-dev-only`, merge.
3. `git tag v0.1.0 <main-sha> && git push origin v0.1.0`.

The `Release` workflow then checks the tag is on `main` and equals `__version__`, builds sdist +
wheel, runs the install smoke on Ubuntu and macOS, and creates the GitHub Release with the files
and the CHANGELOG section. PyPI publishing (trusted publishing, no API token) runs only when the
repository variable `PYPI_PUBLISH` is `true`: register the publisher on PyPI first (owner
`monocsp`, repository `remote_ci_monitor`, workflow `release.yml`, environment `pypi` — the
environment name must match exactly), create that environment in the repository settings, then
set the variable.

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
node --test tests/web/*.test.js  # web UI pure functions
python scripts/mutcheck.py      # proves the tests go red for 8 known mutations
scripts/smoke_install.sh        # README setup on a fresh venv (builds the wheel first)
```

CI runs the same on Ubuntu (3.11, 3.13) and macOS (3.13), plus the install smoke and gitleaks. Contributions follow
`PLAN.md`: comments and docstrings in Korean, identifiers, CLI help and UI strings in English.
