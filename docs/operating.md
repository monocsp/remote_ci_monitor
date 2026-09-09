# Operating the build machine

Running rcm as a service, upgrading it, what it exposes, and how to check the numbers it shows.
For what goes *in* the config file see [Configuration](configuration.md); for a first job see the
[usage guide](usage.md).

## Run as a service

Keep `rcm serve` alive across logins and reboots with the example units in `examples/`:

- **macOS (launchd)** — `examples/launchd/com.remote-ci-monitor.server.plist`. Create the
  dedicated user in System Settings → Users & Groups (Standard, no admin). Replace every
  `/Users/rcm` in the file (7 places: the `rcm` binary, `server.toml`, WorkingDirectory, the two
  log paths, `PATH`, `HOME`), then as that user `mkdir -p ~/Library/Logs/rcm ~/Library/LaunchAgents`,
  copy the file there and run
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.remote-ci-monitor.server.plist`
  (`launchctl bootout gui/$(id -u)/com.remote-ci-monitor.server` to stop). launchd does not create
  the log directory (a missing one makes the job exit silently) and does not expand `~`, so every
  path in the file is absolute. Logs go to `~/Library/Logs/rcm/server.log`.
- **Linux (systemd)** — `examples/systemd/rcm-server.service`. Copy to `/etc/systemd/system/`,
  then `sudo systemctl daemon-reload && sudo systemctl enable --now rcm-server`;
  `journalctl -u rcm-server -f` shows the log.
- Both raise the **file-descriptor limit to 4096** (`SoftResourceLimits`/`NumberOfFiles` in the
  plist, `LimitNOFILE` in the unit). The server holds descriptors per request thread, per open
  event stream and per SQLite connection — the database, its `-wal` and its `-shm` — and a launchd
  session defaults to `maxfiles 256`, which is not enough. When they run out, `sqlite3` cannot open
  the database and *every* request fails with a database error until the service is restarted; the
  queue looks alive and answers nothing. If you wrote your own service file, set this.
- Both send **SIGTERM** on stop: the server shuts down cleanly and jobs that were running are
  marked `lost` (exit 3 for waiting sessions); queued jobs survive and start after the restart.
- The `PATH` in the unit is what presets inherit (`env_passthrough`) — add Homebrew and your
  toolchains there. Keep the machine awake (`pmset -a sleep 0` on macOS).

Three things learned from a real deployment (a Flutter monorepo gate on a Mac mini):

- **The service `PATH` is what your presets run with** (`env_passthrough` hands it over). Put the
  toolchains first and never the interpreter of the rcm install itself: if the venv that holds
  `rcm` comes first, a preset's `python3` silently becomes that venv's Python (no packages) —
  reference the `rcm` binary by absolute path in the service file instead. On macOS include
  `/usr/sbin` (`sysctl`, `ioreg` feed the host card).
- **macOS privacy (TCC) applies to launchd services.** A service cannot read `~/Documents`,
  `~/Desktop` or `~/Downloads` unless the user grants it, and the failure looks like a hang or
  `Operation not permitted`. Keep `data_dir`, presets, `[[repos]]` mirrors and every script a
  notification hook runs under `~/.local/share` or `~/.config`.
- **Hooks have no keychain.** A `[[notify]]` command that calls `gh`, `aws` or similar must read
  its token from a file (mode 600) via an environment variable; the interactive keyring is not
  available and the call blocks until the hook times out.
- **macOS blocks local-network traffic for services you have not allowed.** On macOS 15 and later
  a process without Local Network permission cannot send to the LAN at all — multicast *and*
  ordinary unicast fail with `EHOSTUNREACH` — and launchd services are denied by default. Incoming
  HTTP still works, so the queue looks healthy while `rcm discover` finds nothing and the server log
  says `mdns: send failed: EHOSTUNREACH`. Grant it once in System Settings ▸ Privacy & Security ▸
  Local Network (the entry is the interpreter or binary named in the service file) and restart the
  service. Until then sessions reach the server by address — `server = "http://<host>.local:8787"`
  in `client.toml` works on the same Wi-Fi without any permission.

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

A container inherits its file-descriptor limit from the Docker daemon rather than from the image,
so check it (`docker exec rcm sh -c 'ulimit -n'`) and pass `--ulimit nofile=4096` if it is lower —
the same reason as the service files above.

## Upgrade

`pipx upgrade remote-ci-monitor` (or `pipx install --force <wheel>`), then restart `rcm serve`
(`launchctl kickstart -k gui/$(id -u)/com.remote-ci-monitor.server` · `systemctl restart rcm-server`).
The database migrates on start; queued jobs and a paused queue survive, jobs that were running
become `lost` (exit 3 for waiting sessions). Sessions may run a different patch version —
`rcm check` shows both versions.

A migration is one way, so copy the database before an upgrade that carries one (the changelog says
which). With the server running, use SQLite's own backup — `cp` misses the write-ahead log:

```sh
sqlite3 ~/.local/share/rcm/rcm.sqlite3 ".backup ~/rcm-before-upgrade.sqlite3"
```

### From a git checkout

The service's virtual environment can point at a working copy instead of a released wheel:

```sh
~/.local/share/rcm-venv/bin/pip install -e /path/to/remote_ci_monitor
```

The build machine then runs whatever that folder has checked out, and an upgrade becomes `git pull
--ff-only` in it plus the same restart. Keep it on `main`: `main` only takes pull requests from
`dev`, so it is the branch CI has already passed on.

| rule | why |
|---|---|
| Nothing is edited in that folder | its files *are* the running server, and the next `git pull` conflicts with local changes |
| Development happens in a `git worktree` with its own `.venv` (`pip install -e ".[dev]"`) | the code you are changing is never the code the machine is running |
| A test server gets its own config file, `port` and `data_dir` | sharing `data_dir` means two servers writing one SQLite database |

A test server is one file away: the config search order is `--config`, `$RCM_CONFIG`, `./rcm.toml`,
then `~/.config/rcm/server.toml`, so an `rcm.toml` in the worktree is found before the production
one — and that name is already in `.gitignore`. Give it `port = 8788`, its own `data_dir` and
`advertise = false`, so discovery keeps pointing sessions at the real server.

Pull and restart together, with the queue empty. Between the two the running process still holds
the old modules, so a job that starts in that window can load a mix of both.

Claude Code sessions have this wired as a `PreToolUse` hook: `.claude/settings.json` runs
`tools/guard_production.py`, which finds the production checkout from the machine's own editable
install, refuses edits to it and to the server's config and data, and asks before a deploy. A
machine with no such install sees nothing.

## Security notes

- Discovery answers (`_rcm._tcp`) carry only the server name, port, version, lane count and LAN
  IPs — never tokens, presets or paths. Anyone on the LAN can learn that a build server exists;
  the read API is open on the LAN unless `read_auth = "basic"`.
- Every write (submit, upload, cancel) needs a bearer token. The server stores only a SHA-256 of it.
  Tokens have a kind: `client` (sessions), `admin` (cancel any job, pause, bump) and `worker`
  (remote workers — `/worker/*` only). A worker can report only on jobs it claimed itself; the
  log bytes and host samples it sends are treated as data, never parsed as commands.
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

The server deletes job logs after `retention_days_success` (default 14) /
`retention_days_failure` (30) days, and the job records themselves after
`metadata_retention_days` (180, must be ≥ `estimate.sample_days`). A sweep runs at start and then
every `retention_sweep_interval_seconds` (3600). Running jobs are never touched; `rcm logs N` on a
purged job answers `log expired`. Git mirrors are never pruned. If the sweeper thread dies,
`/api/health` turns 503 — nothing here fails silently.

**Workspaces keep a shorter clock than logs.** A failed job leaves a 50 KB log and a 720 MB
workspace, so they are not worth the same number of days: the workspace and the snapshot it was
unpacked from go after `workspace_retention_days` (1), and two byte rules —
`workspace_storage_max_bytes` (100 GiB) and `min_free_bytes` (10 GiB) — take the oldest finished
jobs' bulk before any date arrives. Evidence is never given up to make room, and nothing is deleted
on a guess: a size that cannot be measured skips the byte rules for that sweep and says so. The
full table is in [Configuration](configuration.md#retention-what-is-kept-and-for-how-long).

**Upgrading to a release that adds these:** the first sweep after the restart applies the new
defaults, so look first. This needs no server and deletes nothing:

```sh
git -C ~/Documents/GitHub/remote_ci_monitor pull --ff-only   # the service still runs the old code
rcm gc --dry-run --config ~/.config/rcm/server.toml          # what the new rules would remove
launchctl kickstart -k gui/$(id -u)/com.remote-ci-monitor.server
```

To keep the old behaviour instead, set `workspace_retention_days = 30` (or your
`retention_days_failure`) before restarting. `rcm gc` with an admin token runs the same plan for
real on a running server; `rcm gc --dry-run` there shows it first.

**Job artifacts keep their own clock.** A bundle a session can fetch back
([Configuration](configuration.md#getting-files-back-out-of-a-job)) lives for
`artifact_retention_hours` (24) from the moment it is ready, and the retention days above do not
shorten that — with `retention_days_success = 0` the logs go on the next sweep and the bundle still
has its full day. It goes earlier only when the submitting session says it has written every file
and nobody else joined that job. Deleting is reported only after the files are actually gone: a
failed unlink leaves the bundle counted and tries again on the next sweep, and a download already
in flight finishes even if the bundle expires mid-transfer.

`/api/status` carries `server.artifact_storage` — `stored_bytes`, `reserved_bytes` and
`limit_bytes` (`artifact_storage_max_bytes`, 10 GiB). When the server is full it refuses **new**
bundles rather than evicting bundles somebody is still waiting for; the affected jobs say
`storage_full` and still report their own success or failure.

## Why the numbers can be wrong

- ETA source `default`/`preset` means no measurements yet; `measured n=7` is the median of 7 real runs.
- A job sharing the machine with another job takes longer than a median measured from runs that
  had it alone, so the confidence badge drops one step while that is true. The estimate itself is
  not padded — a guessed slowdown factor would be a number nobody measured.
- Step counts marked "so far" come from scripts that did not declare `::rcm::steps::N`.
- Step timestamps are **receive** times (`timing: "as_received"`), so buffered output shifts them.
- A `lost` job died with the server; it is left as `lost`, never silently re-queued or deleted.
- Medians are recomputed when a job finishes, not on every request. A status document served
  seconds after a job ends already includes it; nothing else moves a 45-day median.
- A worker the server has not heard from in a week is forgotten, and its lanes stop appearing as
  `down`. A worker with a job still running is never forgotten, however long it has been silent.
- When the queue is paused or every worker lane is down, ETAs are `null` on purpose.
- Host samples are polled (default every 5 s). `stale` means the last sample is older than 3 intervals; `hosts_error` means the sampler itself failed. Memory "used" on macOS is `active + wired + compressed` (what Activity Monitor calls Memory Used), which is smaller than `top`'s PhysMem used. Memory is shown in GiB under the `GB` label, like Activity Monitor and `free -h`, so a 24 GB machine reads `24.0 GB`. GPU numbers come from `ioreg` (Apple Silicon) or `nvidia-smi`; on other machines the GPU shows `unavailable` with the reason. Disk is the filesystem holding the **data directory** — where snapshots, mirrors and workspaces land — not the root partition, and a remote worker reports its own. It is counted in decimal GB, like Finder. The card warns at 85% used **or** under 10 GiB free, whichever comes first: a large disk at 90% still has room, a small one at 80% cannot unpack a snapshot.

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

11. M5b items: on a second computer (or the same one with another data dir) run
    `rcm worker --server http://<build-machine>:8787 --pool mac2 --lanes 1` with a worker token,
    give a preset `pool = "mac2"`, and `rcm run` it — the job must run there, its steps and host
    card must show under **pool mac2** in `rcm top` and the web page, and `rcm logs` must return
    the whole log. `kill -9` the worker while a job runs: within `worker_timeout_seconds` the job
    must be `lost` with `worker … unreachable`, the header must show `<token name>/1 down`, and a
    restarted worker must pick up the next job.
