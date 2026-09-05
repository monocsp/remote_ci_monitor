# remote_ci_monitor

A local job server for a team that shares **one build machine**. Sessions on any computer submit a
preset (`rcm run gate`); the server queues and runs them one at a time, shows queue position,
ETA, step progress and host load, and hands the result back as an **exit code**.

- No GitHub dependency. Runs on your LAN or Tailscale.
- Runtime dependencies: **zero** (Python 3.11+ standard library only). Same package for server and client.
- Sessions upload their **working tree as it is** (uncommitted changes included), so a green gate
  means *this* tree passed.

Status: **M0** — server, queue, worker, `rcm run` / `rcm wait`. Web UI (M2), host sampler and `rcm top` (M1) are next. The plan lives in `PLAN.md` (Korean).

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

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
python scripts/mutcheck.py      # proves the tests go red for three known mutations
```

CI runs the same on Ubuntu (3.11, 3.13) and macOS (3.13), plus gitleaks. Contributions follow
`PLAN.md`: comments and docstrings in Korean, identifiers, CLI help and UI strings in English.
