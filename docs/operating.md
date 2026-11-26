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
- **The log says what is being turned on.** Starting is ten steps — the port, the database, the
  presets, then the seven services the server brings up — and each prints as it finishes, with the
  seconds it took:

  ```
  [rcm] start 1/10 port · http://0.0.0.0:8787 (0.00s)
  [rcm] start 2/10 database · schema v17 (0.31s)
  [rcm] start 3/10 presets · 10 · gate, gate-fast, gate-commit … (0.00s)
  …
  [rcm] start 10/10 discovery · macmini._rcm._tcp.local. (0.02s)
  [rcm] rcm 0.2.8 listening on http://0.0.0.0:8787 · lanes 1 · presets … · data …
  ```

  The `listening on …` banner still closes the sequence and still lists every preset. The steps
  are what to read after a restart that added presets, and they are where a slow start shows
  itself — before this the log stayed empty until everything was up, so a long migration or an
  mDNS responder waiting on Local Network permission looked the same as a hang.
- The `PATH` in the unit is what presets inherit (`env_passthrough`) — add Homebrew and your
  toolchains there. Keep the machine awake (`pmset -a sleep 0` on macOS).
- **A worker on another machine** — `rcm init worker --service-file PATH` writes the launchd plist
  or systemd user unit for `rcm worker` (the same shape as
  `examples/launchd/com.remote-ci-monitor.worker.plist` and `examples/systemd/rcm-worker.service`)
  and prints the command that starts it; see
  [Second build machine](configuration.md#second-build-machine-remote-worker).

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
- **One `gh` account is active per machine.** If git's credential helper is `gh auth
  git-credential`, every github.com fetch uses the active account. Running `gh auth switch` for one
  project cuts the server off from a private repository only the other account can read — the
  queue looks healthy while every `git_ref` submission is refused with `Repository not found`.
  Give such a repository its own `token_file` (configuration.md, "Credentials per repository")
  so it no longer depends on which account is active.
- **Hooks have no keychain.** A `[[notify]]` command that calls `gh`, `aws` or similar must read
  its token from a file (mode 600) via an environment variable; the interactive keyring is not
  available and the call blocks until the hook times out.
- **A failed hook is not sent again.** `rcm check` shows `warn  notify  N hook deliveries
  failed…` while the server's failure count is above 0. The count is kept in memory: restarting
  the server (a deploy, a reboot) sets it to 0 and the row goes away, so look in the server log
  (`notify <rule> #<job>: …`) for failures from before the restart.
- **macOS blocks local-network traffic for services you have not allowed.** On macOS 15 and later
  a process without Local Network permission cannot send to the LAN at all — multicast *and*
  ordinary unicast fail with `EHOSTUNREACH` — and launchd services are denied by default. Incoming
  HTTP still works, so the queue looks healthy while `rcm discover` finds nothing and the server log
  says `mdns: send failed: EHOSTUNREACH`. Grant it once in System Settings ▸ Privacy & Security ▸
  Local Network (the entry is the interpreter or binary named in the service file) and restart the
  service. Until then sessions reach the server by address — `server = "http://<host>.local:8787"`
  in `client.toml` works on the same Wi-Fi without any permission.

## Set a machine up: `rcm setup`

`rcm setup` writes the config file and the service file for one of four roles, from the same
templates `rcm init` uses:

| role | what the machine does | what setup writes |
|---|---|---|
| `client` | sends jobs and reads their results | `client.toml` (mode 600) |
| `worker` | runs the jobs a coordinator hands it | `worker.toml` (mode 600) and a worker service file |
| `coordinator` | queues jobs and hands them to other machines; runs none itself | `server.toml` with [`lanes = 0`](configuration.md#a-server-that-only-hands-out-jobs-lanes--0) and a server service file |
| `coordinator-worker` | a coordinator that also runs jobs here (today's `rcm serve`) | `server.toml` with `lanes = 1` and a server service file |

The role is not stored anywhere: rcm reads it back from the config files that exist, the same way
`rcm doctor` does. In a terminal `rcm setup` asks for the role and for each value, with the
template's defaults. `rcm setup --role ROLE --yes` asks nothing, and neither does a run without a
terminal. Then these flags must be given, or it stops with exit 2 before writing anything
(options are taken by their whole name only: `--data-dir`, never `--data`; an empty or blank
`--config`, `--service-file`, `--data-dir` or `--label` is a usage error too):

| role | needed with `--yes` | optional |
|---|---|---|
| `client` | `--server URL` | `--by LABEL`, `--config PATH` |
| `worker` | `--server URL`, `--pool NAME`, `--lanes N` | `--label`, `--service-file PATH`, `--config PATH` |
| `coordinator`, `coordinator-worker` | `--bind ADDRESS` | `--port`, `--data-dir`, `--label`, `--advertise` / `--no-advertise`, `--service-file`, `--config` |

```sh
rcm setup                                                   # asks
rcm setup --role worker --server http://macmini:8787 --pool linux --lanes 2 --yes
rcm setup --role coordinator --bind 100.101.102.103 --yes   # a Tailscale address
```

**`rcm` with no arguments.** In a terminal (stdin and stderr both), on a machine with no config at
all, `rcm` asks `No rcm config on this machine yet. Set it up now?` and runs `rcm setup` on yes.
Everywhere else it prints the usage and exits 2, as before: without a terminal, after a no, and
when any config is there. A config is `$RCM_CONFIG`, `./rcm.toml`, `server.toml` or `client.toml`
under `$XDG_CONFIG_HOME/rcm` or `~/.config/rcm`, `worker.toml` in `$XDG_CONFIG_HOME/rcm` (or
`~/.config/rcm` without `XDG_CONFIG_HOME`), or only environment variables: `RCM_SERVER` or
`RCM_TOKEN` for a client, `RCM_WORKER_TOKEN` for a worker. An `$RCM_CONFIG` that names a missing
file is a usage error, and nothing is asked.

**Where it writes.** Config files go to `$XDG_CONFIG_HOME/rcm` (or `~/.config/rcm`), or to
`--config PATH`. The service file goes next to them, in `<config dir>/service/<label>.plist` on
macOS or `<config dir>/service/<unit>` on Linux, or to `--service-file PATH`. It does not go to
`~/Library/LaunchAgents`: launchd would start anything there at the next login. A `--service-file`
or `--config` inside a folder the service manager reads (`~/Library/LaunchAgents`, `/Library/LaunchAgents`,
`/Library/LaunchDaemons`, `~/.config/systemd/user`, `/etc/systemd/user`, `/etc/systemd/system`)
is refused with exit 2; on macOS the match ignores case, as the disk does
(`~/library/launchagents` is the same folder). The label is
`com.remote-ci-monitor.server` or `com.remote-ci-monitor.worker` unless you pass `--label`; the
systemd unit of a standard label is `rcm-server.service` or `rcm-worker.service`, and of any
other label `<label>.service`. The server service file has the file-descriptor limit of 4096,
`HOME`, `WorkingDirectory`, a `PATH` with `/usr/sbin` and without the venv's `bin`, and a log
named from the label: `server.log` and `worker.log` for the standard labels, `<label>.log`
otherwise, under `~/Library/Logs/rcm` (launchd) or `~/.local/state/rcm` (systemd); the worker's
systemd unit gets the same `HOME` and label log. It runs the `rcm` that ran setup by its absolute
path; setup warns when it could only find an `rcm` on `PATH`.
In a systemd unit a path with a space, `%` or `$` is written the way systemd reads it (quoted,
`%%`, `$$`); a path with a line break or another control character is refused with exit 2.

**It registers and starts nothing.** It prints the lines to run yourself, with absolute paths:

```sh
mkdir -p <log folder> <LaunchAgents folder>
cp -n <config dir>/service/<label>.plist <LaunchAgents folder>/<label>.plist
cmp -s <config dir>/service/<label>.plist <LaunchAgents folder>/<label>.plist && launchctl bootstrap gui/$(id -u) <LaunchAgents folder>/<label>.plist || echo "not started: the installed file differs from the one setup wrote, or the start failed; to replace an installed one: launchctl bootout, cp without -n, launchctl bootstrap"
```

On Linux the start is `systemctl --user daemon-reload && systemctl --user enable --now
<unit>`, and before it setup prints `sudo loginctl enable-linger $USER`: on a machine nobody logs
in to there is no user manager to start the service otherwise, and without it the service stops
when you log out. `cp -n` never replaces a file already installed, and the start runs only when the
installed file is the one setup just wrote. When a service with the same label is already loaded
(setup only asks: `launchctl print`, or `systemctl --user is-active` and `is-enabled`), it says so
and leaves it alone. Then, and when `--force service` rewrote a service file that was there, it
also prints the order that replaces the installed one: `launchctl bootout gui/$(id -u)/<label>`,
`cp` without `-n`, `launchctl bootstrap gui/$(id -u) <file>`, or `systemctl --user disable --now
<unit>`, `cp`, `systemctl --user daemon-reload`, `systemctl --user enable --now <unit>`. Only
launchd's `Could not find service` and systemd's `is-active` exit 3 with `inactive` or `unknown`
on its output count as not loaded (`activating` or `reloading` does not, and `failed` means the
unit is there, so it counts as loaded); any other
answer (no user session, `Failed to connect to bus`) is "could not tell", with a warning that the
printed start may fail on that machine. Once the file is installed, `rcm doctor` reads the
installed copy, `--label` or not.

**To remove it.** Unload the service, then delete the installed file and the files setup wrote.
On macOS: `launchctl bootout gui/$(id -u)/<label>` and `rm -f ~/Library/LaunchAgents/<label>.plist`;
on Linux: `systemctl --user disable --now <unit>`, `rm -f ~/.config/systemd/user/<unit>` and
`systemctl --user daemon-reload`. The config file and `<config dir>/service/<file>` go the same
way; a `data_dir` and its database stay until you delete them. setup prints this as one
`to remove: …` line with its own paths, just before its last line. In that line the service is
unloaded and its installed file deleted only when `cmp -s` finds the installed file is the one
setup wrote (`cmp -s <staged> <installed> && { launchctl bootout …; rm -f <installed>; }`), and
deleted even when the unload fails (a service already stopped); a file someone else installed
under that name is left alone. When a service with the label was already
loaded before setup ran, the line adds that it may not be one setup wrote.

**What it refuses or warns about.**

- An existing config or service file is kept: the run stops with exit 1 and writes nothing.
  `--force config`, `--force service` or `--force` (both) replace them. When a write fails part
  way, what was already written is undone. An existing `server.toml` that is not TOML or not
  UTF-8 does not stop setup: it warns `could not read <path>` and goes on, and only
  `--force config` replaces that file.
- A `data_dir` that already holds `rcm.sqlite3` is refused with exit 1. `--reuse-data-dir` keeps
  using it; setup then reads the schema version from the file's header, without opening the
  database, and warns when it differs from this build's (the service migrates an older one when it
  starts). `--data-dir` is written as an absolute path.
- A port something already listens on gets a warning. setup binds the port once to check and
  connects to nothing.
- `--bind` must be an address of this machine or `0.0.0.0`, written as an address: a name
  (`localhost`, `macmini.local`) is refused with exit 2, `give an address, not a name`, because
  setup looks no name up. An IPv6 address goes without brackets (`[::1]` is refused with exit 2
  and that hint) and is written in its short form, so `0:0:0:0:0:0:0:0` is `::`, every address.
  A Tailscale address is the
  recommended choice: setup writes `advertise = false` (mDNS would hand LAN workers an address
  they cannot reach, so give workers the URL) and warns that everyone on the tailnet can read the
  queue. A LAN address or `0.0.0.0` gets the warning that anyone on the network can read the queue,
  the shelf and the machine list, and a LAN address also the advice to reserve it in your router.
  `127.0.0.1` gets a warning that no other machine can connect.
- A `coordinator-worker` on a machine that already has `worker.toml` gets a warning: the two run
  two sets of lanes on one host.
- A flag the role does not use (`--lanes` for a coordinator, `--pool` or `--server` for a
  coordinator, `--advertise` for a client) gets one warning line naming them.
- When setup cannot tell whether the port is free, or whether a service with the label is already
  loaded, it says so instead of staying quiet.
- When a file cannot be put back after a failed write, the error names it (`could not restore
  <path>`).

**Tokens.** setup creates none, opens no database and prints no token. A coordinator prints the
two commands to run once its service is up, with the same `rcm` the service runs, so that no other
build opens (and migrates) its database: `<rcm> token --config <server.toml> add CLIENT_NAME` (a
client token, never `--admin`) and `<rcm> token --config <server.toml> add WORKER_NAME --worker`.
It prints them on a machine without launchd or systemd too. A client is told to get a token from
the coordinator and set `RCM_TOKEN`; a worker to put the worker token in `worker.toml` as
`token = "…"`, because a service does not see the `RCM_WORKER_TOKEN` of your shell, and it gets
that line before the lines that start its service: a worker without a token is refused and
restarted over and over.

**At the end** it runs the checks that need no server (Python, SQLite, the open-files limit of the
service file it wrote, whether the config file reads, and the host) as advice that does not change
the exit code, then the `to remove: …` line, and last says to start the service and add the
token (a worker: the token first) and run `<rcm> doctor --role <role>`, with the `rcm` the service
runs and any `--config` or `--service-file` you gave.
Exit codes: 0 written (also when the closing report fails or is interrupted after every file is
written: the last line is `files written; the report failed: …` or `files written; report
interrupted`), 1 not written (an unexpected error is one line, without a traceback, that says what
is on disk), 2 usage, 130 interrupted before every file is written. `rcm setup` does not follow
the server's version.

## Check a machine before you use it: `rcm doctor`

`rcm doctor` says what this machine can build and test, and what to install first. It measures the
way the service will run, not the way your shell does: it starts from an empty environment, adds
what the service manager sets (launchd: `HOME` `USER` `LOGNAME` `SHELL` `TMPDIR` `PATH`; systemd
user units: `HOME` `USER` `LOGNAME` `SHELL` `XDG_RUNTIME_DIR` `PATH`) and the variables in the
service file, and runs the server's probes and `requires` checks there. A `JAVA_HOME` exported in
your shell does not count. For a system service (`/etc/systemd/system`, `/Library/LaunchDaemons`)
`HOME`, `USER`, `LOGNAME` and `SHELL` are those of the user in `User=` or `UserName` (root
without one), read from this machine's user database; when this machine does not know that user,
the report says so, does not look for that service's config in your home folder, and exits 3.

```sh
rcm doctor                                    # the role the config files on this machine name
rcm doctor --role worker --service-file ~/Library/LaunchAgents/com.remote-ci-monitor.worker.plist
rcm doctor --role coordinator-worker --json   # the server's own service measurements
```

| role | what it reads | what decides the exit code |
|---|---|---|
| `worker` | `worker.toml` (or `RCM_WORKER_TOKEN` and `--server`), the service file, and the server's `/worker/spec` | a tool missing for a `pool` preset of this machine's pool, rcm's own needs, or no preset that would come to this machine |
| `coordinator-worker` | the local `server.toml` (never the database) and the server's `/api/health` and `/api/machines`, read with a client token | the same rules, for the server's own lane as its service measured it — for each preset only when the server measured the definition the `server.toml` on disk has |
| `coordinator` | the same files and routes | rcm's own needs and `capacity` from `/api/health`: 1 when no preset has a machine to run on, a warning naming the presets that have none; a `pool` preset that a capable worker in another pool could take is named with that worker (changing it to `any-capable` is yours to decide; rcm changes no preset). With `lanes = 0` and a server that does not report `capacity`, exit 3 |
| `client` | `client.toml` or `RCM_SERVER` / `RCM_TOKEN` | the server answers and the token works |

Without `--config`, `rcm doctor` reads the config the service reads. When a service file is found,
its command line is read with rcm's own parser: `--config` there names the file (`--config=X` and
shortened options too), and its `--server`, `--pool` and `--lanes` (worker) or `--bind` and
`--port` (server) apply as they do for the service. A server service started without `--config`
reads `RCM_CONFIG` from the service file's environment, then `rcm.toml` in the service's working
directory (`WorkingDirectory`; without it launchd starts the service in `/` and a systemd user
unit in the home folder), then `server.toml` in the service's config folder; a worker service
started without `--config` reads `worker.toml` from the service's config folder (the service's
`XDG_CONFIG_HOME`, else `~/.config/rcm`). `RCM_SERVER_*` count only from the service file's
environment. Your shell's `RCM_CONFIG`, `RCM_SERVER_*` and `XDG_CONFIG_HOME`, and the folder you
run `rcm doctor` in, never decide what the service reads. When rcm cannot tell the working
directory (a systemd unit outside `~/.config/systemd/user/` without `WorkingDirectory=`) and an
`rcm.toml` may be there, the report exits 3. Without a service file, the places `rcm init` writes
are read (`~/.config/rcm/worker.toml`, `~/.config/rcm/server.toml`); pass `--config` for a file
anywhere else. `--config` and `--server` given to `rcm doctor` win, with a warning when the
service starts with another `--config` (or a relative one, which depends on the service's working
directory and is not compared). A `coordinator` or `coordinator-worker` report
with no server config anywhere is a usage error (exit 2): it does not ask whatever answers on port
8787 with the built-in defaults. When the service file cannot be read and no `--config` is given,
or the config the service names cannot be read, rcm does not know what the service runs with: the
report names it, asks the server nothing, and exits 3.

The server's own lane is judged from the server's measurement (`preset_env` in `/api/health`),
but the presets come from the `server.toml` on disk. Each `preset_env` entry carries a `digest`
of the definition the server measured (`requires`, `env_passthrough`, the names in the preset's
`env` and its `PATH`). When it differs from the definition on disk, the server has not reloaded
that config yet: the preset is `unknown` with code `not_reloaded` (run `rcm reload`), and the
report exits 3. A server that sends no `digest` cannot say which definition it measured, so its
presets are `unknown` too.

Each preset shows one state: `ready` (it starts taking jobs once the service reports), `needs
check` (its tools are there and rcm runs its check presets once after the worker registers — not
"ok" yet), `missing` (with the install hint and docs link from the coordinator's `[[tools]]` when
there is one), or `unknown`. An `any-capable` preset that misses a tool is "not coming to this
machine" — a warning, not a failure. A preset for another pool is listed and left out. On a remote
worker, a preset whose `checks` name a `local_only` check is "not coming (check is local-only)",
not "needs check": that check never runs there. The server names those checks for each preset in
`GET /worker/spec` (`unreachable_checks`), and `rcm doctor` reads only that key; it does not guess
from which presets the spec leaves out. Against a server that does not send the key, a preset with
`checks` is `unknown` (code `check_unknown`) and the report exits 3. `rcm doctor --role
coordinator` lists every `local_only` preset as "needs a local lane", judged by the same rule the
server places jobs with. A preset the server does not place — an older server that sends no
`placement`, a placement rcm does not know, or no `pool`, `pools` or `checks` where the judgement
needs them — is `unknown` with `applies: unknown`: it is never read as a `pool` preset, so a tool
it misses does not exit 1, and "nothing would come to this machine" is not claimed while it might.
The coordinator's `[[tools]]` hints show for the server's own lane too.

The service file is found in this order: `--service-file`; the standard places
(`~/Library/LaunchAgents/com.remote-ci-monitor.*.plist`, `~/.config/systemd/user/rcm-*.service`,
and the system places `/Library/LaunchDaemons/com.remote-ci-monitor.*.plist` and
`/etc/systemd/system/rcm-*.service`, where the Linux server install above puts
`rcm-server.service`); `~/.config/rcm/service/`. Only files that start the role's command (`rcm worker` or `rcm serve`)
count; a file in those places that looks like rcm's but whose command rcm cannot resolve to one of
its own (`env rcm worker`, `sh -c "exec rcm worker"`, arguments rcm would refuse, a label ending in
`worker` or `server` that starts something else) is named as unreadable, since it may be this
role's service. A file there that plainly starts something else — no `rcm` executable or
`remote_ci_monitor` module in its command, and a label that does not end in `worker` or `server`,
like the `rcm-ci-connect` watcher `com.remote-ci-monitor.ci-watch.plist` — is not a candidate. A
file given with `--service-file` is used as given; if it starts another role's command the report
warns, and if rcm cannot resolve its command, the `PATH` and config of what it starts are unknown
(exit 3). Two in the same place need `--service-file` (exit 2 without a terminal). With none, a
worker is measured with the worker's default service `PATH` (the one `rcm init worker` writes) and
the report says so; a `coordinator` or `coordinator-worker` report does not borrow it — the
server's `PATH` is unknown, `measured_in` is `none` unless the server's own measurement was read,
and the report says "no service file for the rcm server was found" and exits 3. A system unit
without `WorkingDirectory=` starts in `/`. A launchd plist without `PATH`
gets launchd's default, `/usr/bin:/bin:/usr/sbin:/sbin`. A systemd unit without
`Environment=PATH=…` is not guessed — the default differs between distributions — so the report
shows no `PATH`, measures no tool and exits 3 until the unit sets one. A unit line rcm does not
follow (`EnvironmentFile=`, `PassEnvironment=`, `UnsetEnvironment=`) and an `Environment=` or
`ExecStart=` value with a systemd specifier rcm does not expand are named by line and key, and the
report exits 3. `%h` in a user unit (`~/.config/systemd/user/`, `~/.config/rcm/service/`) is your
home folder and is expanded; any other specifier, and `%h` in a unit given with `--service-file`
from another folder (it may be a system unit, where `%h` is `/root`), is not. When the line
can change `PATH` (`EnvironmentFile=` always can), the service `PATH` is unknown and no tool is
measured. Write `PATH` out in full in an `Environment=` line. A `$` is kept as written: systemd does
not expand variables in `Environment=`. A service file rcm cannot
read (XML cut short, a plist whose top level is not a dictionary, an argument or environment value
that is not a string, a unit line it cannot parse) is named with the reason, never its values, and
the report exits 3. When one in a standard place cannot be read, that whole place is unknown: rcm
does not measure with another file next to it, since the broken one may be this role's service,
and it does not go on to `~/.config/rcm/service/`, since the service manager reads the installed
file, not that copy. The service `PATH` is then unknown and no tool is measured, as it is for a
file given with `--service-file` that cannot be read. The worker's default `PATH` is used
only when no candidate file was found at all. `--shell-path` measures
once more in your shell and marks the presets whose result differs.

rcm's own needs are rows of their own: Python 3.11.4+ (the `python` row is the Python that ran
`rcm doctor`; when the service file starts rcm with another Python — a `python3.x -m
remote_ci_monitor.cli` command, or the `#!` line of its `rcm` script — a `service python` row
measures that one in the service environment, and is `unknown` when it cannot be run; a macOS
Command Line Tools shim such as `/usr/bin/python3` without the tools installed is not run, as it
would open the install dialog, and the row fails with `missing (needs Command Line Tools)`);
git, when a `git_ref` preset comes to this machine (2.31+ with `token_file`); every `[[repos]]`
entry reachable with `git ls-remote` (a FAIL, since `git_ref` jobs cannot run without it); for a worker, the repository of each `git_ref` preset
in its `[[repos]]` (a FAIL naming the repository, else the job fails with `repo_missing`); the host
sampler's commands on the service `PATH`; and a warning when the rcm install's own `bin` folder
is on that `PATH`. A `[[repos]]` entry with `token_file` is checked the way the worker calls git,
with a credential helper that reads only that file. A row rcm cannot measure is `unknown`, not a
FAIL, and the report exits 3: a git version it cannot read, a `token_file` the user running
`rcm doctor` cannot read (run it as the service's user), repositories when the service `PATH` is
unknown, a `git ls-remote` that timed out or could not run. A `git ls-remote` or `git --version`
that runs past its time limit is stopped with its whole process group, so no credential helper or
`git-remote-https` is left behind. On macOS it also reminds you of Local Network permission and
of keeping the machine awake; rcm checks neither. On Linux a systemd user unit gets a `linger`
row (`loginctl enable-linger`); a system unit does not, and when neither `USER` nor `LOGNAME` is
set the row is `unknown`. A `--service-file` outside both the user unit places and
`/etc/systemd/system` may or may not be a user unit, so its `linger` row is `unknown` too.

| exit | meaning |
|---|---|
| 0 | every preset that comes to this machine is `ready` or `needs check`, and rcm's own needs are met; warnings allowed |
| 1 | a tool is missing, rcm's own needs fail (Python 3.11.4+, git 2.31+ for `token_file`, an unreachable or missing repository), or nothing would come to this machine |
| 2 | usage: a bad flag, a config file you gave or that sits in a standard place that does not exist, is not UTF-8 or TOML, or whose `[server]` is not a table, no server config at all for a coordinator role, a server URL that does not start with `http://` or `https://`, several roles or service files to choose from |
| 3 | could not decide: the server is down or older, an answer from it that is not the JSON rcm expects (an HTML page from the wrong port or a proxy, an empty body, a field of the wrong type, a field rcm needs that is missing, `null` or, for a name, empty, such as `ok` and `version` in `/api/health`, `kind` in `/api/whoami`, `presets` and each preset's `name` in `/worker/spec`, `machines` in `/api/machines`), a preset the server does not place, a row rcm cannot measure, a probe timed out, no token or a refused one, a server found on the network that nobody confirmed, a systemd unit without `PATH` or with a line rcm does not follow, a service file it cannot read, a config the service starts with that it cannot read, no home folder |

1 wins over 3, and 3 over 0. `rcm check` keeps its own exit codes. An error rcm did not expect
ends the report with exit 3 and one line, `could not decide: <kind>: <message>` (home folder as
`~`), on stderr and in `undecided`, not with a traceback. Exit 3 also covers a server that has not
reloaded the config on disk, no service file for a server role, a system service whose user rcm
does not know, and a service Python rcm cannot run. The exit code stays the verdict when the
output cannot be written: `rcm doctor | head -1` ends quietly, and on a terminal that is not
UTF-8 the text report replaces the characters it cannot print while `--json` writes them as
`\uXXXX`; a closed stderr drops the usage and could-not-decide lines quietly. Ctrl-C stops the
probes, the `git` and the service Python that are running, with their helpers, and exits 130.

What it never does: it sends the server only read requests (no registration, no capability
report — the server's judgement stays the server's), opens no database, runs no install hint and never uses
`sudo` (as root, as in a container, rcm's own hints drop it; the coordinator's `[[tools]]` text is
shown as written, after control characters, line separators and bidirectional controls are
removed). It prints no home folder (`~` instead),
no preset `env` value and no value from the service file, only the names. A server found by mDNS
gets no request before you confirm it in a terminal; `--server URL` skips the question. The name
and address it shows in that question come from the network and are cleaned to one line first.
`--offline` skips `git ls-remote` for `[[repos]]` and still asks the coordinator.

`--json` prints one document with these keys, always all of them; a value rcm does not know is
`null`, never a missing key. `doctor_version` is `1`; a later version only adds keys, so ignore the
ones you do not know.

| key | what it holds |
|---|---|
| `doctor_version` · `role` · `exit` | `1`, the role measured, the exit code |
| `measured_in` | `clean_service_env` (worker), `server_service_env` (coordinator-worker, when the server's own measurement was read), `none` |
| `host` | `os` `os_release` `os_like` `arch` `machine` `rosetta` `container` `root` `package_manager` `python` `rcm`; `rosetta` and `container` are `null` when rcm could not tell |
| `server` | `url` `found_by` (`flag`, `config`, `mdns`) `state` (`ok`, `not ok` when the server answers `ok: false`, `unreachable`, `token refused`, `unreadable answer`, `not confirmed`, `—`) `generation` `version` (`null` when the server does not say) |
| `service` | `file` `manager` `path` `env_names` (names only); `null` for `client` |
| `shell` | `{"path"}` with `--shell-path`, else `null` |
| `rcm_requirements` | rows of `name` `state` (`ok` `warn` `fail` `info` `unknown`) `detail` `needed_for` `install` |
| `presets` | rows of `name` `applies` (`pool` `any-capable` `not_coming` `other_pool` `local_lane_only` `unknown`) `state` `missing` (`tool` `install` `docs`) `checks` `code` `tools` `warn` `note` `shell_differs` |
| `probes` | probe name → `state` `value` |
| `remote_machines` | coordinator roles: rows of `name` `pool` `alive` `eligible` `presets`, each `null` when the server does not say; else `null` |
| `summary` | counts of `ready` `needs_check` `missing` `unknown` among presets that come to this machine, and `applies_unknown`: presets the server does not say whether they come here |
| `problems` · `undecided` · `warnings` | the lines behind exit 1, exit 3 and the warnings; every `unknown` preset that might come, `unknown` probe and `unknown` row of rcm's own needs has its line in `undecided` |

macOS Command Line Tools shims: `/usr/bin/git`, `make`, `xcrun` and the other tools that only
forward to the Command Line Tools open an install dialog when the tools are not installed. rcm
treats a file directly in `/usr/bin/` that links `libxcselect` as such a shim and, when
`xcode-select -p` fails, reports it as `missing (needs Command Line Tools)` without running it —
in `rcm doctor`, in the job's `requires` check, in the server's tool rows, in worker probes and
in the `git` row of `rcm check` and `rcm worker --check` alike; `git ls-remote` for `[[repos]]` is
then not run. `rcm serve` with `[[repos]]` does not start on such a shim (`git is missing (needs
Command Line Tools)`), and `git_ref` jobs fail as `git is not installed on the build machine (needs
Command Line Tools)` without running it, so a headless service does not open the
dialog for every job. `rcm doctor` runs `git ls-remote` with the git on the service `PATH`, in the service
environment, not your shell's. `/usr/bin/java` and the other Java stubs are not detected this way.

A worker sends the same host facts (OS version, real arch and Rosetta, container, root, package
manager, Python) when it registers and with each capability report. The server keeps them in
memory only, never in the database, so after a server restart they show `—` until the next
report. `rcm machines --machine ID` prints them on its `host` line. `GET /api/machines` carries
them only to a request with a client token, even when `read_auth = "none"` lets anyone read the
rest; a worker token sent with Basic under `read_auth = "basic"` does not get them.

`rcm worker --check` keeps its rows and adds two: `version` (ok when it matches the server; a
warning when registering will follow the server's version; FAIL when this install cannot follow,
such as an editable or pinned one) and a last line with the number of missing tools that points
at `rcm doctor --role worker`. That line is ok only when nothing is missing, and a warning when
something is or when the server sent no spec to count from (`—`); it never fails the check. A
`/worker/spec` answer that is not rcm's (a list, a string, an HTML page, a probe timeout that is
not a number) is a FAIL on the `presets` row, as a health or whoami answer of the wrong shape is
on the `server` and `token` rows, never a traceback. A server version or token name the server
does not send is left out of the line, not printed as `None`.

With `server = "auto"`, `rcm worker --check` now asks before it talks to the server it found on
the network, as `rcm doctor` does. Without a terminal there is nobody to ask, so it exits 3 and
sends nothing; pass `--server URL` in scripts.

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

## Change a preset without a restart

A restart marks every job running in a local lane `lost`. That is the right price for new code;
it is the wrong price for adding a preset. `rcm reload` (admin) makes the server re-read its
config file and swap in the new **presets and repos** — nothing is interrupted, and the next job
to be claimed sees the new definition.

```sh
rcm reload                 # from any machine with an admin token
kill -HUP <pid>            # or on the build machine itself
launchctl kill -HUP gui/$(id -u)/com.remote-ci-monitor.server
```

It prints what moved, by name:

```
preset added ipf-verify, ipf-verify-tree · repo added image_place_follower
config: /Users/build/.config/rcm/server.toml
```

**Only `[[presets]]`, `[[repos]]` and `[[probes]]` reload.** A reload starts a new generation:
every worker fetches the new preset and probe list and measures its environment again, and until
it has, it takes no `any-capable` job. `[server]`, `[estimate]`, `[host]`, `[display]`
and `[[notify]]` are read once at start by threads that keep their own copy — the workers, the
janitor, the notifier, the host sampler — so a live swap would not reach them. Rather than apply
half the file, rcm refuses the whole reload and names the section:

```
config unchanged — [server] changed and only takes effect on a restart
```

A job already running is not touched: it was handed everything it needs when it started. A job
still **queued** is matched when a lane claims it, so it runs under the edited definition.

A reload also re-measures the job environment, so `rcm check`'s `service preset tools` row
answers for the presets you just loaded rather than for the ones the server started with.

A reload re-reads the files `server.toml` brings in with `include` too
([presets in their own file](configuration.md#presets-in-their-own-file-include)). To see what a
reload would apply before applying it:

```
$ rcm reload --dry-run
would reload /Users/build/.config/rcm/server.toml (+ presets.dolomood.toml)
  presets changed: gate (argv, timeout_seconds), test-narrow (env)
  presets added:   game-web-smoke
  nothing applied (--dry-run)
```

The server runs the same load and the same refusals as a real reload and stops before swapping
anything in: no generation is started, no worker re-measures. For a changed preset it names the
**keys** that differ, never their values — `env` and `argv` may hold things that should not leave
the machine. `--json` prints the response (`dry_run: true`, `presets_changed_keys: {"gate":
["argv", "timeout_seconds"]}`, …). An older server would ignore the request body and apply the
reload, so `rcm reload --dry-run` asks the server first and sends nothing to a server that does
not know dry runs.

Removing a preset is the one edit to think about. A queued job whose preset is gone does not
wait — it fails at claim with `preset_missing`, the same way it does after a restart. Reload the
removal while jobs are queued against it and you fail them; check `rcm top` first, as you would
before a restart.

Change one of those and you are doing an upgrade-shaped restart; the section above covers it. The
same refusal protects you from a broken file: if the config does not parse or a preset is
invalid, **nothing changes** and the error says why. The running server is never left in a worse
state than before you typed the command. `nothing changed` means the file rcm read is not the
file you edited, or what you edited was in a section that does not reload.

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
   itself is not changed. If the copy cannot be made or migrated — a `StoreError` or any SQLite
   error from a migration statement alike — the command exits 3 (unknown) and says why in one line:
   that is the migration failing *before* your restart, which is the point. If the copy cannot be
   removed afterwards, the command also exits 3 and names the directory that was left behind.
3. `rcm pause` (admin token), then confirm nothing is `running`, `cancelling` or `uploading`
   (`rcm top`). Pausing stops the queue, not submissions: stop the service right after the check,
   or a job that starts uploading in between is cancelled by the stop. Pausing does not stop the
   clock of a [scheduled job](configuration.md#running-a-job-later---at-and---in): one whose time
   falls inside the pause, the stop and the start, plus its start deadline (15 minutes by
   default), ends `expired` instead of running. Look at `rcm jobs --state scheduled` first and
   pick a window that misses them.
4. Stop the service (`launchctl bootout gui/$(id -u)/com.remote-ci-monitor.server` ·
   `systemctl stop rcm-server`).
5. Keep your own copy of the database at this moment (see below), so there is one that predates
   the new build regardless of what the build does.
6. Move the code: `pipx upgrade remote-ci-monitor` (or `pipx install --force <wheel>`), or
   `git pull --ff-only` in the checkout.
7. Start the service. The first start migrates the database — and before it changes anything it
   writes `<data_dir>/backup/rcm.sqlite3.v<old>.bak`, a verified copy of the old database (the
   three newest are kept). If that backup cannot be written, the server refuses to start and the
   database stays as it was. The server takes its port before it opens the database, so a build
   started by mistake next to the running service stops at `Address already in use` without
   touching the database.
8. `rcm check`, the web page, then `rcm resume`.

Queued jobs and a paused queue survive; jobs that were running when the service stopped become
`lost` (exit 3 for waiting sessions). Sessions may run a different patch version — `rcm check`
shows both versions. From 0.8.0 sessions and remote workers follow the new server by themselves:
the next command fetches the server's client, and a running worker finishes its jobs and starts
again on the new version. `RCM_CLIENT_VERSION`, `~/.config/rcm/client-version` or `--no-follow`
keeps one where it is ([keeping clients on the server's version](#keeping-clients-on-the-servers-version)).

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
silently show wrong facts. The file the message names is the highest `rcm.sqlite3.v<n>.bak` that
actually exists in `backup/` — after an upgrade that skipped versions it is older than the build
you are going back to, and if there is none the message says `no migration backup found` and
leaves you the copy you made in step 5. Restoring the `.bak` is a **database-only downgrade**:
stop the service, copy the file the message names over `rcm.sqlite3`, delete `rcm.sqlite3-wal`
and `rcm.sqlite3-shm`, start the old build. What you lose is everything that happened after the backup was taken — job rows
finished since then are gone from the database while their `jobs/<id>/` logs and
`workspaces/<id>/` directories are still on disk (the sweeper treats those as orphans and counts
them but does not delete them), and a job number handed out after the backup will be reused. Do
this only when the new build cannot run at all; otherwise fix forward.

Before you roll back below 0.11.0, remove `local_only = true` from every preset, `include` files
included, and the top-level `[[tools]]` tables: an older server does not know either key and
refuses to start.

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
such install sees nothing. For `rcm setup` the hook does not try to read arbitrary shell. On a
machine with a production install it lets setup through only as **one plain command**: optional
`NAME=value` words, then `rcm` (any path ending in `/rcm`, or `python3 -m remote_ci_monitor`),
then `setup` and its arguments — no `;` `&` `|` `$` `` ` `` `(` `)` `<` `>`, backslash, glob or
line break anywhere, quotes only around a whole value, and `--config`, `--service-file`,
`--data-dir`, `HOME` and `XDG_CONFIG_HOME` given as absolute or `~` paths (a relative path is
refused wherever the session stands). `rcm setup --help` passes in that shape only. For such a
command it asks setup's own resolver where it would write, and refuses a setup that would write
its config or service file anywhere an edit is refused (the production checkout, the service
virtualenv, the server's config folder or data directory) or into a folder the service manager
reads, use a production label, or point a server at the production `data_dir`; paths are
compared as written and with symbolic links resolved. Anything else that could reach setup — a
command naming rcm (or with a variable where the command goes) that holds the word `setup`
anywhere, or where the word after rcm is something the shell builds (`${S}tup`, `` `…` ``,
`s{e,}tup`) — is refused with "run `rcm setup` as one plain command: no ; & | $ ` ( ) < > or
backslash, absolute or ~ paths only". So are `cd /x && rcm setup …`, `env rcm setup …`, a
command line setup would reject, and a failure of the hook itself. So is any command that holds
both a word that could be rcm (`rcm`, a path ending in `/rcm`, `remote_ci_monitor`, a `$`
variable, a glob such as `r?m`) and, anywhere in it, a launcher that feeds arguments (`xargs`,
`parallel`, `find … -exec`, `fd -x`, `entr`, `rush`, `xe`), whose arguments come from input the
hook cannot read, or one that gives a terminal (`script`, `unbuffer`, `expect`, `socat`, `tmux`,
`screen`, `ssh -t`, Python's `pty`), where the first-run question would start setup. The hook
does not look at where the words stand — `<f xargs rcm` and `2>/dev/null xargs rcm` are refused
like `xargs rcm` — so `git ls-files | xargs grep -n rcm` is refused too; search text with a tool
that is not a launcher. Text that
only mentions it, such as `git commit -m "… rcm setup …"`, is refused too: put the text in a file
(`git commit -F <file>`, `gh pr create --title "$(cat <file>)"`).

The *service virtualenv* is the one whose editable install (`direct_url.json`) points at the
primary checkout — not whatever `rcm` is first on `PATH`, which in a shell with a worktree's
`.venv` activated is the worktree's build. The hook looks at `~/.local/share/rcm-venv`, then at
the `rcm` named by the installed launchd or systemd unit, then at every `rcm` on `PATH`, and
takes the first with an editable source. The first two are what the service runs, so they count
wherever they sit — a service run from a worktree, such as `remote_ci_monitor-service`, or one
that runs the `.venv` inside its checkout. An `rcm` found only on `PATH` counts only when its
virtualenv lies outside its source and the source is not a linked worktree, so your own
worktree's `.venv` is a development build. What counts is the data
directory the command would *actually* open, in the CLI's own order: `--data-dir`, then
`RCM_SERVER_DATA_DIR`, then the `data_dir` of the config it would pick (`--config`,
`$RCM_CONFIG`, `./rcm.toml`, `$XDG_CONFIG_HOME/rcm/server.toml`, `~/.config/rcm/server.toml`).
So a **copy of the production config** that only changes `port` is refused too — its `data_dir`
is still the production one — and so is a test config run with `RCM_SERVER_DATA_DIR` pointing at
production. `cd <dir> && rcm …` is judged in `<dir>`; `env -i`, `env -u NAME`, `env -S '…'`, `exec`,
`nohup`, `time`, `nice`, `timeout <duration>`, `command` and a `( … )` or `{ … }` group do not hide the command. The hook does not look
inside `bash -c '…'`, `xargs`, `uv run` or `$(…)`, and does not resolve symlinks.

## Keeping clients on the server's version

A session machine runs the server's version of `rcm`. Before a command that talks to the server
(`rcm run`, `wait`, `jobs`, `check`, and commands this `rcm` does not know yet), the installed
`rcm` asks the server which version it runs. When the two differ, it downloads the wheel the
server hands out, checks its sha256, unpacks it under `~/.cache/rcm/clients/` and starts itself
again from there. It is the same Python and the same process (`exec`), so the exit code, signals
and the JSON on stdout are the command's own; `ps` shows the followed process as
`python -P -c …` followed by your arguments. Upgrading the server is enough: each session
follows on its next command, upward or downward. The first download prints one line on stderr:

```
rcm: following server v0.8.1 (installed v0.8.0) — fetched 707.7 KB
```

Later commands run straight from the cache and print nothing. The server's version is asked at
most every 10 minutes per server address (`rcm check` always asks). A server that did not answer
is remembered for 60 seconds, so a script that calls `rcm` in a loop does not wait 3 seconds on
every call.

The installed `rcm` is now the bootstrap: the bootloader that decides which code runs. Each
session machine needs `0.8.0` or newer installed once, and `examples/session/update-client.sh`
(below) is the way to put it there. After that, upgrading the server reaches the sessions by
itself.

Some commands never follow, because they are the server or work without one: `rcm serve`,
`rcm token`, `rcm init`, `rcm discover`, `rcm version`, `rcm skills` and `rcm gc --config …`.
`rcm doctor` does not follow either: it measures what this install can do. Nor does `rcm setup`,
which writes this machine's own config.
`rcm worker` follows at its own moments, described in [remote workers](#remote-workers) below. A call with `--help` or `--version` always shows the installed rcm's help
and version. The help of a command that only the server's version has appears once the installed
rcm is upgraded.

An editable install (`pip install -e`) does not follow by default and runs the installed code.
That is the `.venv` of a development checkout, and the service virtual environment of a build
machine that runs from a checkout: code under development is never swapped for the production
version. `RCM_CLIENT_VERSION=follow` makes it follow anyway.

### Pinning a version or turning it off

| setting | where | values |
|---|---|---|
| `--no-follow` | after `rcm` or after the command, for one call | runs the installed rcm |
| `RCM_CLIENT_VERSION` | the environment | `follow`, `installed`, or a version such as `0.8.0` |
| `client-version` | a one-line file: `$XDG_CONFIG_HOME/rcm/client-version`, else `~/.config/rcm/client-version` | the same three values; `#` starts a comment |

The flag wins over the variable, and the variable over the file. With none of them a wheel install
follows and an editable install does not. A wrong value (`lates`, `../x`) stops a following
command with exit 2 and names the variable or the file, so a typo never silently follows or
silently stops; `rcm version` shows it on its second line. Only the installed rcm reads the file.
It is not a `client.toml` key, because an older version refuses keys it does not know.

A pinned version `X` runs the installed rcm when that is `X`, else a cached `X`, else the server's
wheel when the server runs `X` right now. Otherwise the installed rcm runs with a warning. Once
fetched, a pinned version keeps running from the cache after the server has moved on.

Going down to an older server means that version's parser runs. A flag only a newer version knows,
such as `rcm run --no-supersede` against a 0.5.0 server, ends in that version's usage error
(exit 2). The server would not understand it either.

### When it cannot follow

The command goes on with the installed rcm and says why in one line on stderr. Stdout stays the
command's own, so `rcm run --no-wait` still prints its JSON line:

```
rcm: not following server v0.8.1: sha256 mismatch — running installed v0.8.0
rcm: not following http://macmini.local:8787: cannot reach the server — running installed v0.8.0
```

| reason | when |
|---|---|
| `cannot reach the server` | no answer within 3 seconds (5 in all), an error status, or a redirect — redirects are never followed, so a token never travels to another host |
| `the server's health has a malformed …` | `version`, `client_wheel.path`, `.sha256` or `.bytes` in `/api/health` failed its format check |
| `the server has no client wheel (…)` | `client_wheel: null`, or `/client/…` answered 404 or 503 |
| `the server is too old to hand out its client` | a server before 0.2.6 |
| `the server needs a token to hand out its client` | `read_auth = "basic"` and no token; with a wrong token, `the server refused the token for its client (HTTP 401)` |
| `sha256 mismatch` | the downloaded bytes do not hash to `client_wheel.sha256`; nothing is kept |
| `cannot unpack: …` | a full disk, permissions, a broken zip, or an entry outside `remote_ci_monitor/` and its `.dist-info` |
| `the wheel says v…` | the unpacked `__version__` is not the server's |
| `needs Python …` | the wheel's `Requires-Python` is above this interpreter, or not a plain `>=X.Y` |
| `… is writable by others` · `… belongs to another user` | the cache folder is not yours alone, or its parent is writable by others without the sticky bit |
| `v0.8.0 is not installed or cached and the server runs v0.8.1` | a pinned version that cannot be found |
| `the cached client v… did not load before` | that cached copy failed to start once and is not used again; the next line, `rcm: remove <folder> to fetch it again`, names the folder to delete |
| `cannot start the cached client: …` | the `exec` itself failed |
| `not supported on this platform` | not macOS or Linux |

When the server is down but answered before, the cached copy of the version it reported last
(with the same sha256) runs without a warning. An `rcm wait` across a server restart keeps the
server's version, and the command itself reports the lost connection.

### What you see

`rcm version` adds a second line when following is configured, or when the default server has a
record. `rcm --version` is always one line with the installed version:

```
$ rcm version
rcm 0.8.0 (Python 3.12.4, darwin arm64)
follow: on (default) · server http://macmini.local:8787 v0.8.1 checked 3m ago · cached 0.8.1, 0.8.0
```

`rcm version --json` carries the same under `follow`: `mode`, `pin`, `source`, `install`,
`server`, `server_version`, `checked_at`, `cached`, `installed` and `running`. `running` is the
version a following command would run now, and it is what a script should read instead of
`rcm --version`. Nothing in it asks the server.

`rcm check` ends its `client` row with what happened to this call:

```
ok    client        v0.8.1 · same as server · following (installed v0.8.0)
warn  client        v0.8.0 · server v0.8.1 · older · not following: sha256 mismatch — pip install http://macmini.local:8787/client/remote_ci_monitor-0.8.1-py3-none-any.whl
warn  client        v0.8.0 · server v0.8.1 · older · pinned by RCM_CLIENT_VERSION
ok    client        v0.8.1 · same as server · not following (editable install)
```

`same as server` is `ok`; `older`, `newer` and `different build` are `warn` (a heads-up, exit 0).
`older` turns `FAIL` only below `min_client_version`, and then `rcm run` prints one warning line
on stderr before it goes on; the refusal itself, when it comes, is the server's 400. When a
session follows a server down to a version before 0.8.0, that version prints its own older row.

The server logs one line the first time it sees a token's client version on an accepted
`POST /jobs`, for example `client macbook runs v0.8.1`. It keeps no record in the database.

### Remote workers

`rcm worker` reads the same setting: `RCM_CLIENT_VERSION`, the `client-version` file, or
`rcm worker --no-follow`. A wheel install follows by default and an editable install does not. The
worker follows at three moments:

- **When it starts.** Before it registers, the worker asks the server for its version. It always
  asks afresh and never uses the 10-minute record, because a restart is usually the moment the
  server changed. When the versions differ, it fetches the server's wheel and starts that version
  in the same process, exactly as a command does, with the same `rcm: following …` and
  `rcm: not following …` lines. If following fails, the worker registers with the installed
  version. When the server could not be reached and the worker started the version it last saw
  from its cache, a server that moved on in the meantime refuses that registration, and the worker
  restarts through the installed rcm (below) instead of stopping.
- **When the server refuses its registration for the version.** That refusal (a 409) now carries
  `server_version`. A following worker asks the server again and starts that version, logging
  `[rcm worker] register: server runs v0.8.1 — following`. When it cannot, it stops with exit 2
  and puts the reason after the server's sentence:

```
rcm: worker version 0.8.0, server 0.8.1 — install the same release · not following: sha256 mismatch
rcm: worker version 0.8.0, server 0.8.1 — install the same release · pinned by RCM_CLIENT_VERSION
rcm: worker version 0.8.0, server 0.8.1 — install the same release · not following (editable install)
```

- **While it runs.** Every heartbeat answer carries the server's `server_version`. When it differs
  from the version the worker runs, a following worker *drains*: it takes no new jobs, lets the
  jobs it already has run to the end and reports them, and then starts the installed rcm again in
  the same process, which follows from the beginning. Nothing is lost: a job that was running
  ends with its own result, not `lost`. The same happens when a lane finds that the server forgot
  the worker and refuses its new registration for the version. The log reads:

```
[rcm worker] server runs v0.8.1 — draining: no new claims, restarting when running jobs finish
[rcm worker] drained — restarting through the installed rcm for v0.8.1
rcm: following server v0.8.1 (installed v0.8.0) — fetched 707.7 KB
```

If the server goes back to the worker's own version before the jobs finish, the drain is cancelled
(`server runs v0.8.0 again — drain cancelled`) and the lanes take jobs again; if it moves to a
third version, the worker restarts for that one. A job that finishes while the server is away
(for example during the few seconds it is being replaced) is not given up: the worker keeps it in
its heartbeats and reports it again after the next answered heartbeat, before it registers again,
and at the end of a drain, for up to a minute, so the new registration does not close that job as
lost. After that minute it logs `job #<id> still not reported` and gives the report up. The restart leaves `RCM_WORKER_RESTARTED_AT` in its environment: within 5 minutes of
it, a server version change seen in a heartbeat does not drain again
(`server runs v0.8.1 — not draining: restarted 60s ago`), and jobs go on being served. A refused
registration drains at any time, because that lane cannot take jobs. While the server does not
know the worker, its heartbeats say `heartbeat: worker not registered`. A stop signal during a
drain is an ordinary stop: running jobs are reported as `lost` and the worker does not restart. If
the restart itself fails, the worker logs `restart failed: <reason>` and exits 1, and the service
manager starts it again.

A worker that does not follow (pinned, `installed`, `--no-follow` or editable) never drains. It
logs one line the first time a heartbeat shows another version, for example
`[rcm worker] server v0.8.1 — not following (editable install)`, keeps running, and stops with
exit 2 only when a lane's registration is refused, with the same reason after the server's
sentence. A worker older than this release does not read `server_version` and keeps its version
until it restarts.

A worker started from the cache does not follow by itself a second time. If the server moved again
between its start and its registration, it restarts through the installed rcm, which follows the
new version. Within 5 minutes of a drain restart it stops with exit 2 instead
(`· not following: already following v0.8.1`), and the next start begins again from the installed
rcm. A worker pinned to a cached version stops with `· pinned by RCM_CLIENT_VERSION`.
`rcm worker --once` follows when it starts, stops with exit 2 when its registration is refused,
and ignores a server change while it runs; if a lane's registration is refused, it finishes the
running jobs and exits 2 without restarting. `rcm worker --check` never follows: it runs the
installed rcm and shows the server's version in its `server` row. With `server = "auto"`, the
address found over mDNS is handed to the restarted process, which does not look for the server
again. The worker's requests for the health and the wheel carry the worker token.

Under launchd or systemd, starting the server's version and restarting after a drain are an
`exec`: the PID stays the same, the service manager does not count a restart, and the log goes on
in the same file. Before the `exec` the lanes have finished, the heartbeat has stopped and any
capability probe still running is ended. A worker that stops with exit 2 is started again by the
service files that `rcm init worker --service-file` writes (`SuccessfulExit = false` for launchd,
`Restart=on-failure` for systemd), and that start follows from the beginning. A cached copy that
failed to start once is marked and not entered again. The start that finds it broken ends with
`· not following: the cached client did not load`, each later start with
`· not following: the cached client v0.8.1 did not load before`, and both print the line
`rcm: remove <folder> to fetch it again` once, not a traceback. Delete that folder.

If one of its lanes has to register again and the server refuses the version, a worker that does
not follow stops with exit 2, so the service manager starts it again. Before, it stopped with
exit 0 and stayed down. A worker that follows a much older server reads its `worker.toml` with that
version's code, so a key that version does not know (for example `server_name` on a server before
0.5.0) stops it with exit 2.

### The cache

`$XDG_CACHE_HOME/rcm/` (by default `~/.cache/rcm/`) holds `follow.json`, the last version seen per
server address, and `clients/<first 16 characters of the sha256>/`: one unpacked wheel per folder,
completed by a single rename, with `.rcm-client.json` naming its version. Right after a download
it keeps the three most recently used versions and any that a running `rcm` holds, and removes the
rest. Both folders must belong to you and be writable by no one else; they are created with mode
0700, and `rcm` refuses to run code from them otherwise. So does each unpacked folder, and the
parent of the cache folder must not be writable by others unless it has the sticky bit. Deleting the folder is always safe: it is
filled again from the server.

### Security: the session runs the server's code

Following means a session machine runs the code the server hands out, as the user who typed
`rcm`. That code reaches everything this user can read: the GitHub token of `gh`, git credential
helpers, the keys in `~/.ssh`, cloud credentials and every other repository on the disk. This is
new: before, the server only returned results and artifacts to a session.

Plain HTTP on a LAN lets a man-in-the-middle swap the wheel. The sha256 arrives over the same
channel (`/api/health`), so it catches a broken download, not a forged one.

A `.local` name is resolved over mDNS even when you write it out, as in
`server = "http://macmini.local:8787"` instead of `auto`. Anyone on the network who answers that
name first becomes your server, so leaving discovery off does not make a `.local` address safe.

What rcm does guard against: it never follows a redirect, checks every value of the health answer
before it becomes a path, unpacks only `remote_ci_monitor/` and its `.dist-info`, and refuses a
cache folder other users can write to. Those close the paths of a leaked token and a swapped cache,
not a forged server.

On a network you do not trust, connect over the server's Tailscale address: WireGuard encrypts the
channel and authenticates the node, which makes both attacks hard. To turn following off, choose
one of three: `export RCM_CLIENT_VERSION=installed` in your shell profile, a line `installed` in
`~/.config/rcm/client-version`, or `--no-follow` for a single command.

### Serving the wheel

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

Without following (pinned, `installed`, or a client before 0.8.0), `rcm check` prints the
same `client` row next to `server` with the command that installs the server's wheel:

```
ok    server    http://macmini.local:8787 · v0.2.6
warn  client    v0.2.4 · server v0.2.6 · older — pip install http://macmini.local:8787/client/remote_ci_monitor-0.2.6-py3-none-any.whl
```

### Installing or upgrading the bootloader

Following never touches the installed package, and there is no `rcm self-update`: swapping the
package under a running process is different for every install (venv, pipx, uv, editable). Use
`examples/session/update-client.sh` for the first install of the bootloader and when the
bootloader itself needs a fix. It is the wrapper the tests run — copy it as it is. What it does,
and what the inline sketch below keeps:

- **One private directory.** Everything — the health document, the wheel, the curl config — lives
  in its own `mktemp -d` (mode 0700) and is deleted on exit, success or failure. There is no shared
  `$TMPDIR/remote_ci_monitor-<version>….whl`: two wrappers running at once against two servers of
  the same version each install exactly the bytes they verified, and a file of yours at that name
  is never touched.
- **The token is never an argument.** `RCM_TOKEN` is written to a 0600 curl config file inside
  that directory and passed with `-K`; `ps` on a shared machine shows no `Authorization: Bearer`.
- **The status code is read, not trusted.** `curl -f` would stop on a 503 — but `janitor stale`
  or a worker down is a 503 whose body still carries `version` and `client_wheel`. The wrapper
  judges by the body, says `server health is 503 (…) but its client wheel is fine` on stderr and
  installs. A response with no `version` (a 500, a proxy page) is exit 3 with the status and the
  body's `error`; a wheel URL that is not 200 is exit 3 too; a sha256 mismatch is exit 4; a
  version that does not match after the install is exit 5. `pip` itself failing half-way is the
  one step the wrapper cannot undo — its exit code is pip's, and the environment is what pip left.

```sh
server=http://macmini.local:8787
umask 077
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
printf 'header = "Authorization: Bearer %s"\n' "$RCM_TOKEN" >"$tmp/curlrc"   # or leave it empty
status=$(curl -sS -K "$tmp/curlrc" -o "$tmp/health.json" -w '%{http_code}' "$server/api/health")
path=$(jq -r '.client_wheel.path // empty' "$tmp/health.json")
expected=$(jq -r '.client_wheel.sha256 // empty' "$tmp/health.json")
[ -n "$path" ] || { echo "no client wheel ($status)" >&2; exit 3; }
curl -sS -K "$tmp/curlrc" -o "$tmp/$(basename "$path")" "$server$path"
actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$tmp"/*.whl)
[ "$actual" = "$expected" ] || { echo "sha256 mismatch" >&2; exit 4; }
python3 -m pip install --upgrade "$tmp"/*.whl
rcm version
```

Point `RCM_VENV` at the virtual environment that holds `rcm` when it is not the one on `PATH`,
and set `RCM_TOKEN` on a server with `read_auth = "basic"`. `pipx` users run
`pipx install --force "$tmp"/*.whl` in place of the `pip install` line.

## Security notes

- The client wheel (`/client/…whl`) is the server's own installed code, served under the read
  rule above. With `read_auth = "none"` on a LAN anyone can download it; it is the code of a
  public repository, so that exposes nothing new — tokens, presets and data never enter it.
- A session's `rcm` runs the code in that wheel: it follows the server's version and executes
  what the server hands out, as the user who typed `rcm`. Use a trusted server and, on an
  untrusted network, its Tailscale address; `RCM_CLIENT_VERSION=installed` turns this off
  ([keeping clients on the server's version](#keeping-clients-on-the-servers-version)).
- Discovery answers (`_rcm._tcp`) carry only the server name, port, version, lane count and LAN
  IPs — never tokens, presets or paths. Anyone on the LAN can learn that a build server exists;
  the read API is open on the LAN unless `read_auth = "basic"`.
- Every write (submit, upload, cancel) needs a bearer token. The server stores only a SHA-256 of it.
  Tokens have a kind: `client` (sessions), `admin` (cancel any job, pause, bump) and `worker`
  (remote workers — `/worker/*` only). A worker can report only on jobs it claimed itself; the
  log bytes and host samples it sends are treated as data, never parsed as commands.
- Every `POST /jobs` also answers with a **cancel token** for that one submission — a secret with
  a single right: cancel that job (the requester's), or leave its join list (a joiner's). The
  server keeps its SHA-256 only; `rcm run` stores it in a 0600 file under `~/.local/state/rcm`.
  It never appears in `/api/status`, job documents, logs, errors or URLs. With
  `cancel_requires_submission_token = true` it is the only non-admin way to cancel
  ([configuration](configuration.md#who-may-cancel-a-job)).
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
- The shelf (`/shelf/`) is read **without a token**: a kept job's files open in any browser that
  reaches the server, on the LAN and over Tailscale. Keep secrets out of `artifacts` for any job
  that may be kept. `read_auth = "basic"` covers the shelf as well. It answers only a `Host` that
  names this machine (local names, private and Tailscale addresses, `public_url`, the bind
  address, the hostname) and 421 to anything else, so a page elsewhere cannot read it through DNS
  rebinding. A job's files are served sandboxed: only images and plain text open inline, and
  nothing a job wrote can reach the token the web UI keeps. Removing an item takes an admin token
  ([configuration](configuration.md#keeping-a-jobs-files-the-shelf)).

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
its receipt is what the disk said, not the plan, also when a delete got only half way (the entry
in `failed[]` says what went) or the size of what went was never measured (`freed ≥ …`). The full
table is in
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

**The shelf keeps its own clock too.** A kept item stays until nobody has used it for
`shelf_stale_days` (182, then *cleanup pending*) and `shelf_delete_days` (365, then deleted) —
the retention days above never touch it, and it outlives the job row. `rcm gc` does not touch the
shelf; `rcm gc --dry-run` adds one line saying what the next sweep would mark and remove. The first
start of v0.7.0 migrates the database to v24 and leaves `backup/rcm.sqlite3.v23.bak`; add
`keep = true` to presets only after that, because an older build refuses the key. Put
`shelf_dir` on another disk if you do not want kept files to count against the free-space floor
that clears workspaces ([configuration](configuration.md#keeping-a-jobs-files-the-shelf)).

`/api/status` carries `server.artifact_storage` — `stored_bytes`, `reserved_bytes` and
`limit_bytes` (`artifact_storage_max_bytes`, 10 GiB). When the server is full it refuses **new**
bundles rather than evicting bundles somebody is still waiting for; the affected jobs say
`storage_full` and still report their own success or failure.

## Why the numbers can be wrong

- ETA source `default`/`preset` means no measurements yet; `measured n=7` is the median of 7 real runs
  (the web badge says `high · n=7` and spells it out on hover).
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
9. Web UI: open `http://<tailscale-ip>:8787/` on the phone. The queue, the running job's steps and the host card must be readable in one column; paste the laptop token via the key button and confirm **Your jobs** lists the job and **Log** opens. Stop the server: within ~30 s the **Lost connection** banner must appear at the top; start it again and the banner must go away by itself. Then pause only the sampler's view of the world — `kill -STOP <server pid>` for 20 s and `kill -CONT` — and the host card must show a `stale` badge briefly (the queue keeps working). (`kill -9` cannot signal the job's process group, so the script itself keeps running as an orphan until it ends on its own; a normal stop — SIGTERM or Ctrl-C — terminates it.)

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
