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

The order matters. The database migrates one way on the first start of the new build, and an
editable checkout starts serving new code the moment it is pulled — so the running service must
be stopped **before** the code moves, and the preview must not touch the live database. The
sequence below is the same for a wheel and for a git checkout; only the "move the code" line
differs.

1. **Verify the new build somewhere else first** — a separate venv or worktree with its own
   config, `port` and `data_dir`.
2. **Preview from that build, against the real config**:
   ```sh
   <new build>/bin/rcm gc --dry-run --config ~/.config/rcm/server.toml
   ```
   This opens the live database read-only, copies it with SQLite's online backup into a private
   temporary directory, migrates and plans **on the copy**, and deletes the copy. The database
   itself is not changed. If the copy cannot be made or migrated, the command exits 3 (unknown)
   and says why — that is the migration failing *before* your restart, which is the point.
3. `rcm pause` (admin token), then confirm nothing is `running`, `cancelling` or `uploading`
   (`rcm top`). Pausing stops the queue, not submissions: stop the service right after the check,
   or a job that starts uploading in between is cancelled by the stop.
4. Stop the service (`launchctl bootout gui/$(id -u)/com.remote-ci-monitor.server` ·
   `systemctl stop rcm-server`).
5. Keep your own copy of the database at this moment (see below), so there is one that predates
   the new build regardless of what the build does.
6. Move the code: `pipx upgrade remote-ci-monitor` (or `pipx install --force <wheel>`), or
   `git pull --ff-only` in the checkout.
7. Start the service. The first start migrates the database — and before it changes anything it
   writes `<data_dir>/backup/rcm.sqlite3.v<old>.bak`, a verified copy of the old database (the
   three newest are kept). If that backup cannot be written, the server refuses to start and the
   database stays as it was.
8. `rcm check`, the web page, then `rcm resume`.

Queued jobs and a paused queue survive; jobs that were running when the service stopped become
`lost` (exit 3 for waiting sessions). Sessions may run a different patch version — `rcm check`
shows both versions.

A copy you make yourself, with the server running, must use SQLite's own backup — `cp` misses
the write-ahead log:

```sh
sqlite3 ~/.local/share/rcm/rcm.sqlite3 ".backup ~/rcm-before-upgrade.sqlite3"
```

### Going back to the old build

An old build refuses a database a newer build has migrated:

```
database schema version 16 is newer than this build (15) — stop the service, restore
~/.local/share/rcm/backup/rcm.sqlite3.v15.bak (and remove rcm.sqlite3-wal/-shm), or upgrade
```

That refusal is correct — a migration can rewrite data, and reading the columns it knows would
silently show wrong facts. Restoring the `.bak` is a **database-only downgrade**: stop the
service, copy the backup over `rcm.sqlite3`, delete `rcm.sqlite3-wal` and `rcm.sqlite3-shm`, start
the old build. What you lose is everything that happened after the backup was taken — job rows
finished since then are gone from the database while their `jobs/<id>/` logs and
`workspaces/<id>/` directories are still on disk (the sweeper treats those as orphans and counts
them but does not delete them), and a job number handed out after the backup will be reused. Do
this only when the new build cannot run at all; otherwise fix forward.

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
install, refuses edits to it and to the server's config and data, refuses a command that would
open the production database with a build other than the service's own (`rcm token …`, `rcm
serve`, `rcm worker` — a different build migrates the database on open), and asks before a
deploy. `rcm gc --dry-run --config` is allowed: it plans on a temporary copy. A machine with no
such install sees nothing.

The *service virtualenv* is the one whose editable install (`direct_url.json`) points at the
primary checkout — not whatever `rcm` is first on `PATH`, which in a shell with a worktree's
`.venv` activated is the worktree's build. The hook looks at `~/.local/share/rcm-venv`, then at
the `rcm` named by the installed launchd or systemd unit, then at every `rcm` on `PATH`, and
takes the first whose editable source is not a linked worktree. What counts is the data
directory the command would *actually* open, in the CLI's own order: `--data-dir`, then
`RCM_SERVER_DATA_DIR`, then the `data_dir` of the config it would pick (`--config`,
`$RCM_CONFIG`, `./rcm.toml`, `$XDG_CONFIG_HOME/rcm/server.toml`, `~/.config/rcm/server.toml`).
So a **copy of the production config** that only changes `port` is refused too — its `data_dir`
is still the production one — and so is a test config run with `RCM_SERVER_DATA_DIR` pointing at
production. `cd <dir> && rcm …` is judged in `<dir>`; `env -i`, `env -u NAME`, `exec`, `nohup`,
`time`, `command` and a `( … )` or `{ … }` group do not hide the command. The hook does not look
inside `bash -c '…'`, `xargs`, `uv run` or `$(…)`, and does not resolve symlinks.

## Keeping clients on the server's version

The server hands out the client for the code it is running. It assembles the wheel once at start,
from its own installed package (no build tools, no GitHub), and serves it at one exact name:

```
GET /client/remote_ci_monitor-<version>-py3-none-any.whl
```

`<version>` must be the server's own `version` — any other name is a 404 whose `hint` says the
right one. The file name sits at the end of the URL so `pip` recognises it as a wheel:

```sh
pip install --upgrade http://<build-machine>:8787/client/remote_ci_monitor-0.2.6-py3-none-any.whl
```

`/api/health` tells a script everything it needs in one call:

| key | meaning |
|---|---|
| `version` | what the server runs |
| `client_wheel.path` · `.sha256` · `.bytes` | where the wheel is and what it must hash to |
| `client_wheel: null` + `client_wheel_error` | the wheel could not be assembled (for instance the installed metadata is missing); the URL answers 503 with the same code — the server never serves an old or empty wheel instead |
| `min_client_version` | the oldest client the server still talks to; it moves only when the wire contract breaks |

The wheel follows the read rule, like `/api/status`: open with the default `read_auth = "none"`,
token or Basic credentials with `read_auth = "basic"`. It is the code of a public repository, so
it is not treated like job artifacts (which always need a token).

On a session machine, `rcm check` prints a `client` row next to `server`:

```
ok    server    http://macmini.local:8787 · v0.2.6
warn  client    v0.2.4 · server v0.2.6 · older — pip install http://macmini.local:8787/client/remote_ci_monitor-0.2.6-py3-none-any.whl
```

`same as server` is `ok`; `older` and `newer` are `warn` (a heads-up, exit 0); `older` turns `FAIL`
only below `min_client_version`, and then `rcm run` prints one warning line on stderr before it
goes on — the refusal itself, when it comes, is the server's 400. `rcm version --json` stays
server-free.

There is no `rcm self-update`: swapping the package under a running process is different for
every install (venv, pipx, uv, editable). The wrapper below is what the tests run instead
(`examples/session/update-client.sh`): read health, download to a temporary file, verify the
sha256, `pip install --upgrade` the file, then check `rcm version`. Any failing step stops it with
a non-zero exit and nothing installed; a hash mismatch installs nothing.

```sh
server=http://macmini.local:8787
health=$(curl -fsS "$server/api/health")
path=$(jq -r .client_wheel.path <<<"$health")
expected=$(jq -r .client_wheel.sha256 <<<"$health")
tmp=$(mktemp -d)
curl -fsS -o "$tmp/$(basename "$path")" "$server$path"
actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$tmp"/*.whl)
[ "$actual" = "$expected" ] || { echo "sha256 mismatch" >&2; exit 4; }
python3 -m pip install --upgrade "$tmp"/*.whl && rm -rf "$tmp"
rcm version
```

Point `RCM_VENV` at the virtual environment that holds `rcm` when it is not the one on `PATH`,
and set `RCM_TOKEN` on a server with `read_auth = "basic"`. `pipx` users run
`pipx install --force "$tmp"/*.whl` in place of the `pip install` line.

## Security notes

- The client wheel (`/client/…whl`) is the server's own installed code, served under the read
  rule above. With `read_auth = "none"` on a LAN anyone can download it; it is the code of a
  public repository, so that exposes nothing new — tokens, presets and data never enter it.
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
on a guess: a size that cannot be measured skips the byte rules for that sweep and says so. Bytes a
workspace shares with the git mirror by hard link are charged to it but do not come back when it
is deleted, so `rcm gc --dry-run` says what it would free (the reclaimable estimate) next to what
is charged, and a real `rcm gc` measures the inventory and the free space again after it deleted —
its receipt is what the disk said, not the plan. The full table is in
[Configuration](configuration.md#retention-what-is-kept-and-for-how-long).

**Upgrading to a release that adds these:** the first sweep after the restart applies the new
defaults, so look first — from the **new** build, before the running checkout moves
([Upgrade](#upgrade) has the full order):

```sh
<new build>/bin/rcm gc --dry-run --config ~/.config/rcm/server.toml   # what the new rules would remove
```

It plans on a temporary copy of the database and deletes nothing; the live database is opened
read-only and is not migrated. To keep the old behaviour instead, set `workspace_retention_days = 30` (or your
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
