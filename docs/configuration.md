# Configuration

Everything the build machine can be told to do lives in one file, `~/.config/rcm/server.toml`
(`rcm init server` writes a starter). `examples/server.toml` is the same file with every key and a
comment on each. Sessions keep a much smaller `~/.config/rcm/client.toml`, and a remote worker
keeps `worker.toml`.

`rcm check --config server.toml` validates a server file (and its data dir and git) without
starting anything.

| file | who reads it | how to create it |
|---|---|---|
| `server.toml` | the build machine (`rcm serve`) | `rcm init server` |
| `client.toml` | every session (`rcm run`, `rcm top`, …) | `rcm init client [--server URL]` |
| `worker.toml` | a second build machine (`rcm worker`) | copy `examples/worker.toml` |

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
  referenced by active jobs never are. Set `snapshot_cache_scope = "token"` to keep each client's
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
  `worker_timeout_seconds` (60) the worker shows `down`, its running jobs become `lost`
  (`worker build-02 unreachable for 61s`) and are not resumed — resubmit. A worker that restarts
  re-registers and its old jobs are closed as lost too. `rcm top` and the web header show each
  remote lane as `build-02/1 busy #511`; `pools[].lanes` counts only live workers. The worker
  process itself (`rcm worker`) ships in the next release step (M5b-3).
- **Notifications** — `[[notify]]` rules run a command (`argv`, no shell) or POST JSON to a `url`
  when jobs finish, filtered by state (`on`) and preset (`presets`). The command gets
  `RCM_JOB_ID`, `RCM_STATE`, `RCM_PRESET`, `RCM_KEY`, `RCM_REQUESTER`, `RCM_SUMMARY`,
  `RCM_FAILED_STEP`, `RCM_EXIT_CODE`, `RCM_JOB_SECONDS`, `RCM_URL`, `RCM_NOTIFY` (rule name), the
  source (`RCM_SOURCE_MODE`, `RCM_SOURCE_REF`, `RCM_SOURCE_SHA`, `RCM_SOURCE_BASE_SHA`,
  `RCM_SOURCE_DIRTY`, `RCM_SOURCE_REPO` — enough to post a commit status) and `RCM_INPUTS` (JSON);
  the hook also inherits `PATH`, `HOME` and `LANG`;
  user strings are sanitised and capped at 4 KB. Each (job, rule) fires exactly once, including
  jobs that finished while the server was down. Failures are logged and counted
  (`server.notify_failures`) but never retried, and never mark the queue unhealthy.

## Second build machine (remote worker)

A preset can run on another machine by naming a pool (`pool = "linux"`). That machine runs
`rcm worker`, which talks to the server outbound only (it works from behind NAT; the server never
connects to workers):

```sh
# on the server
rcm token add build-02 --worker             # printed once; a worker token only speaks /worker/*
# on the second machine (same rcm release as the server)
export RCM_WORKER_TOKEN=<that token>
rcm worker --server http://macmini:8787 --pool linux --lanes 1 --check   # server · token kind · pool
rcm worker --server http://macmini:8787 --pool linux --lanes 1           # Ctrl-C or SIGTERM stops it
```

The worker registers, claims one queued job of its pool per lane, downloads the snapshot (or
fetches the `git_ref` from its own `[[repos]]` — put a `worker.toml` next to it and pass
`--config`), streams the raw log to the server, which parses the step markers, and reports the
outcome. `rcm top` and the web header show it as `build-02/1 busy #511`; its host sample appears
under that pool. If the server hears no heartbeat for `worker_timeout_seconds` (60) the worker
shows `down`, its running jobs become `lost` (`worker build-02 unreachable for 61s`) and are not
resumed — resubmit. Stopping the worker reports its running jobs as `lost` (`worker stopped`).
`worker.toml` keys: `server`, `token` (or the env var), `pool`, `lanes`, `name`, `data_dir`,
`grace_seconds`, `keep_workspace_on_failure`, `[host]` (sampler) and `[[repos]]`. See
`examples/worker.toml`. `rcm worker --once` runs at most one job and exits (cron, tests).

## Client and worker files

`client.toml` (mode 600 — it holds a token):

```toml
server = "http://macmini.local:8787"   # leave empty (or "auto") to find the server on this network
token = "…"                            # from `rcm token add <name>` on the build machine
label = "alice@laptop"                 # who the queue shows as the requester
```

`RCM_SERVER`, `RCM_TOKEN` and `RCM_LABEL` override the file; `--server`, `--token` and `--by`
override both. `RCM_CLIENT_CONFIG` (or `--client-config`) points at another file.

`worker.toml` keys: `server`, `token` (or `RCM_WORKER_TOKEN`), `pool`, `lanes`, `name`,
`data_dir`, `grace_seconds`, `keep_workspace_on_failure`, `[host]` (the sampler) and `[[repos]]`
(so the worker can fetch `git_ref` jobs itself). See `examples/worker.toml`.
