# remote_ci_monitor

A local job server for a team that shares **one build machine**. Sessions on any computer submit a
preset (`rcm run gate`); the server queues and runs them one at a time, shows queue position,
ETA, step progress and host load, and hands the result back as an **exit code**.

- No GitHub dependency. Runs on your LAN or Tailscale.
- Runtime dependencies: **zero** (Python 3.11+ standard library only). Same package for server and client.
- Sessions upload their **working tree as it is** (uncommitted changes included), so a green gate
  means *this* tree passed.

Status: **M1** — server, queue, worker, `rcm run` / `rcm wait`, host sampler (CPU · RAM · GPU), live events (SSE) and the `rcm top` / `eta` / `jobs` / `logs` / `presets` session commands. The web UI is M2. The plan lives in `PLAN.md` (Korean).

## 5-minute setup

On the build machine:

```sh
pipx install remote-ci-monitor          # or: pip install -e . in a checkout
cp examples/server.toml ~/.config/rcm/server.toml   # edit presets, bind address
rcm token add alice-laptop              # prints the token ONCE; hand it to that client
rcm serve                               # http://127.0.0.1:8787 by default
```

On each session machine:

```sh
pipx install remote-ci-monitor
export RCM_SERVER=http://macmini:8787 RCM_TOKEN=<token>   # or ~/.config/rcm/client.toml
rcm check                               # server · token · presets · timezone
cd ~/src/app && rcm run gate -f scope=full
echo $?                                 # 0 succeeded · 1 failed · 2 cancelled/timed out · 3 unknown
```

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

## Session commands

| command | what it shows |
|---|---|
| `rcm run PRESET [-f k=v] [--no-wait] [--poll]` | snapshot → submit (joins an identical active job) → upload → wait |
| `rcm wait --job N [--timeout S] [--poll]` | follows the job over the event stream, polls every 2 s if the stream is refused |
| `rcm eta PRESET [-f k=v]` / `rcm eta --job N` | queue position, jobs ahead, wait, expected duration, finish time and the confidence of that estimate |
| `rcm top [--watch N] [--json]` | one screen: queue with reasons and ETAs, recent results, medians, host load (CPU · memory · GPU · top processes) |
| `rcm jobs [--mine] [--state S]` | queued, running and recent jobs; `--mine` needs your token and includes jobs you joined |
| `rcm logs N [--follow]` | the job log (your jobs, jobs you joined, or any job with an admin token) |
| `rcm presets` | presets the server offers and their inputs |
| `rcm cancel N` · `rcm pause` · `rcm resume` | cancel (joiners only leave the join list) · pause/resume the queue (admin) |

Every estimate carries `confidence`: `high` (median of ≥ 5 real runs), `med` (< 5), `low` (preset or default guess), `group wait` (blocked by a concurrency group) or `overdue`. Unknown values print as `—`, never as 0.

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

## Why the numbers can be wrong

- ETA source `default`/`preset` means no measurements yet; `measured n=7` is the median of 7 real runs.
- Step counts marked "so far" come from scripts that did not declare `::rcm::steps::N`.
- Step timestamps are **receive** times (`timing: "as_received"`), so buffered output shifts them.
- A `lost` job died with the server; it is left as `lost`, never silently re-queued or deleted.
- When the queue is paused or every worker lane is down, ETAs are `null` on purpose.
- Host samples are polled (default every 5 s). `stale` means the last sample is older than 3 intervals; `hosts_error` means the sampler itself failed. Memory "used" on macOS is `active + wired + compressed` (what Activity Monitor calls Memory Used), which is smaller than `top`'s PhysMem used. GPU numbers come from `ioreg` (Apple Silicon) or `nvidia-smi`; on other machines the GPU shows `unavailable` with the reason.

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

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
python scripts/mutcheck.py      # proves the tests go red for three known mutations
```

CI runs the same on Ubuntu (3.11, 3.13) and macOS (3.13), plus gitleaks. Contributions follow
`PLAN.md`: comments and docstrings in Korean, identifiers, CLI help and UI strings in English.
