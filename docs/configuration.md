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
artifacts = ["test/**/goldens/*.png"]   # files the job produces that sessions may fetch back
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
| `retention_days_failure` | `30` | logs of failed, cancelled, timed-out and lost jobs |
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

`min_free_bytes` is a target, not a guarantee. Deleting does not always give space back — a macOS
local snapshot or an open file can hold the blocks — so if a sweep deletes and free space does not
move, the floor rule pauses itself and `rcm check` says so rather than deleting everything for
nothing.

**Before you upgrade**, see what the new defaults would remove on your machine. This reads the
config and the data directory and deletes nothing, and it does not need the server:

```sh
rcm gc --dry-run --config ~/.config/rcm/server.toml
```

`rcm gc` (admin token) runs the same plan for real against a running server. `/api/status` carries
the same numbers under `server.job_storage`, `rcm check` prints one line, and the web host card
shows what the data directory holds and when the next sweep is.

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

## Parallel lanes without overloading the machine

`[server] lanes` is how many jobs the build machine runs at once. Raising it used to be a gamble:
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

`client.toml` (mode 600 — a file that holds a token and is readable by anyone else is
**refused**, not warned about):

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
