# Test server — the only place for its rules

Any `rcm serve`, `rcm worker` or `rcm token` that a worktree build runs for live verification,
reproduction, screenshots or an acceptance script follows this page; others link here. The same
mistakes kept coming back: a worktree build migrated a database it should not have opened (AGENTS.md:
a verifier's `rcm token add` turned a v7 dev database into v24), and a test client found production
by discovery (M5n).

## Where it runs

| what | rule | why |
|---|---|---|
| port | a free one in 8790–8899, checked first: `lsof -iTCP:<port> -sTCP:LISTEN` prints nothing. Never 8787 (production) or 8788 (other sessions' dev servers) | two servers on one port answer for each other |
| `data_dir` | `~/.local/share/rcm-devtest-<topic>`, created fresh for this run. If it exists, pick a new name (`-<topic>-2`); never `rm -rf` an existing one, by hand or in a script | a reused folder carries an old database and old blobs into the result, and a deleted one may be another run's evidence |
| not `/tmp` | never under `/tmp` or `/private/tmp` | the system may clear it mid-run and some jobs refuse to run there, so evidence vanishes or jobs fail for reasons that are not the change |
| config | its own files inside `data_dir` (`server.toml`, `worker.toml`), never the production config and never a copy of it | `tools/guard_production.py` refuses a production `data_dir` reached through either |
| binary | the worktree's `./.venv/bin/rcm` (and `./.venv/bin/python`) by absolute path | the `rcm` on `PATH` may be the service's |

**Never `~` in a test config:** every `rcm` command runs with `HOME=$D/home` (below), so `~` would be
`$D/home/…`. `data_dir` is absolute. For a home of `/home/example`, a complete `<data_dir>/server.toml`:

```toml
[server]
bind = "127.0.0.1"
port = 8790
data_dir = "/home/example/.local/share/rcm-devtest-example"
advertise = false   # otherwise discovery mixes this server up with production
```

Write it, and the worker's, with the shell, so `$D` and `$P` are expanded before the file exists
(an unquoted `EOF`); change only the port, the folder name and the presets:

```sh
D=$HOME/.local/share/rcm-devtest-<topic>; P=<port>
mkdir -p "$(dirname "$D")" && mkdir "$D" && mkdir "$D/home" || exit 1   # exists: pick a new name
cat > "$D/server.toml" <<EOF
[server]
bind = "127.0.0.1"
port = $P
data_dir = "$D"
advertise = false

[[presets]]
name = "ok"
description = "Fake preset: prints one step marker and exits 0"
argv = ["sh", "-c", "echo ::rcm::step::hello; echo ok"]
timeout_seconds = 60

[[presets]]
name = "slow"   # a client waiting on it is still waiting when its signal lands («Signals»)
description = "Fake preset: prints one step marker, sleeps 30 seconds and exits 0"
argv = ["sh", "-c", "echo ::rcm::step::hello; sleep 30; echo ok"]
timeout_seconds = 60

[[presets]]
name = "ok-remote"   # its pool is served only by the test worker
argv = ["sh", "-c", "echo ::rcm::step::hello; echo ok"]
pool = "devtest-remote"
timeout_seconds = 60
EOF
printf 'server = "http://127.0.0.1:%s"\npool = "devtest-remote"\ndata_dir = "%s/worker"\n' "$P" "$D" \
  > "$D/worker.toml"
```

`ok` and `slow` run on the server's own local lane (`"worker": null` in the job's JSON): a server ↔ worker
scenario on it passes with no worker at all. A worker scenario submits `ok-remote`, and its PASS needs
the job's `worker` (`GET /jobs/<N>`) to name the test worker; `null` is UNKNOWN, whatever the exit code.

No `[[notify]]` hook unless notification is tested (then a fake that appends what it got to a file
in `data_dir`). Presets are fakes (`sh -c 'echo …; exit <code>'`); nothing pushes anywhere.

## The environment of every command

`D` is set again in every Bash call (shell state does not carry over):

```sh
D=$HOME/.local/share/rcm-devtest-<topic>
cd <abs worktree> && HOME=$D/home ./.venv/bin/rcm serve --config $D/server.toml
cd <abs worktree> && HOME=$D/home ./.venv/bin/rcm token --config $D/server.toml add verifier > $D/client.token
cd <abs worktree> && HOME=$D/home ./.venv/bin/rcm token --config $D/server.toml add --worker w1 > $D/worker.token
cd <abs worktree> && HOME=$D/home RCM_WORKER_TOKEN=$(cat $D/worker.token) ./.venv/bin/rcm worker --config $D/worker.toml
cd <abs worktree> && HOME=$D/home RCM_SERVER=http://127.0.0.1:<port> RCM_TOKEN=$(cat $D/client.token) ./.venv/bin/rcm run ok --by acceptance --no-wait
```

The last line is the client's shape: every client command gets the same prefix, and every `rcm run`
gets `--by acceptance` so the requester names no person or host.

- Tokens: `rcm token … add` prints the secret once; it goes straight into a file in `$D` and reaches
  commands only as `RCM_TOKEN` or `RCM_WORKER_TOKEN`. Never into a log, evidence, a report or the chat.
  `rcm run --no-wait` prints a `cancel_token` (in `submission`): cut it from any evidence file.
- `--config` is always explicit. Without it `rcm serve` reads `$RCM_CONFIG` or a `./rcm.toml` in the
  worktree, which is how a worktree build once migrated a v7 dev database to v24 (AGENTS.md).
- `HOME=<data_dir>/home` on every **rcm** command, so the client never reads the production
  `client.toml`. Not for the browser: the CDP `Chrome` helper keeps your real `HOME` (headless Chrome on
  macOS cannot open an http page from a different one, `tools/screenshots/README.md`).
- `RCM_SERVER=http://127.0.0.1:<port>` on every client command; without it the client may discover a
  server on the network, and the first one it finds can be production.
- Never set `RCM_SERVER_DATA_DIR` to production, and never pass `--config`/`--data-dir` that resolve to
  it. Do not route around `tools/guard_production.py`; a refusal means stop.
- `RCM_ALLOW_MIGRATE=1` only when migrating that database is the point of the test.
- Check before starting: `env | grep -E '^(RCM_|XDG_)'` shows nothing you did not set.

## Driving it

- **CLI:** `--json` output and exit codes. `rcm wait` exit 3 is unknown, never a failure.
- **Web:** the CDP `Chrome` helper in `tests/test_web_browser.py`, page opened with `?poll=1`, and
  `page_errors()` must return `[]` (a missing collector raises: `UNKNOWN`, never an empty list).
- **Two processes:** pytest uses `E2EServer`, `WorkerProc`, `Stack` of `tests/test_e2e_worker.py` (own free
  port, pytest's `tmp_path`), not new ones; a live run uses this file's shell blocks, port and `data_dir`.
- **Repeatable runs:** `scripts/acceptance/<topic>_live.sh`: an allow-listed base folder, fake presets and
  hooks, every server stopped on exit (failure too), verdicts in `out/summary.txt`, exit 1 on any FAIL.
  `scripts/acceptance/m5s_live.sh` has that shape but its opening `rm -rf`; a new script picks a new name.
- **Screenshots:** `Chrome.screenshot()` from the same CDP helper, saved under this server's `data_dir`
  (or `.verify/` of the worktree), never `/tmp`. Nothing redacts them: crop or retake the port, paths,
  host name and any token before an image leaves `data_dir`. `tools/screenshots` only runs its own demo.

## Parallel servers and cleanup

At most two servers at once (the build machine also runs `rcm-ci`), each with its own port and
`data_dir`. Leave `data_dir` until the report has its evidence, then remove it by its exact path,
never with a glob.

## Signals

**Signals go only to a pid you recorded, never by name.** Start each test `rcm serve` and `rcm worker`,
and any client you will signal (an `rcm run` for a ctrl-C), in the background, and in the same call
write its pid and then its start time into `data_dir`: `<name>.pid` and `<name>.start`, with the
names `server`, `worker` and `client`. The pid and that start time together are the process's
identity. Every `ps` here runs under `LC_ALL=C TZ=UTC`, as the worker's own reader does
(`ps_start` in `src/remote_ci_monitor/remote_worker.py`), so the start time written and the one read
back are the same text. The server and the worker:

```sh
D=$HOME/.local/share/rcm-devtest-<topic>
cd <abs worktree> && { HOME=$D/home ./.venv/bin/rcm serve --config $D/server.toml > $D/server.log 2>&1 & echo $! > $D/server.pid; } && LC_ALL=C TZ=UTC ps -p $(cat $D/server.pid) -o lstart= > $D/server.start
cd <abs worktree> && { HOME=$D/home RCM_WORKER_TOKEN=$(cat $D/worker.token) ./.venv/bin/rcm worker --config $D/worker.toml > $D/worker.log 2>&1 & echo $! > $D/worker.pid; } && LC_ALL=C TZ=UTC ps -p $(cat $D/worker.pid) -o lstart= > $D/worker.start
```

A client that will get SIGINT starts under `sh -c 'set -m; …'` and submits the `slow` preset without
`--no-wait`, so it is still waiting when the signal lands (`ok` ends before it). A background job of
a shell without job control starts with SIGINT ignored, Python then sets no ctrl-C handler, and the
signal does nothing (`signal.getsignal(SIGINT)` is `SIG_IGN` there); `set -m` turns job control on.
zsh refuses `set -m` outside a terminal, so it is `sh`:

```sh
D=$HOME/.local/share/rcm-devtest-<topic>
cd <abs worktree> && D=$D sh -c 'set -m; HOME=$D/home RCM_SERVER=http://127.0.0.1:<port> RCM_TOKEN=$(cat $D/client.token) ./.venv/bin/rcm run slow --by acceptance > $D/client.log 2>&1 & echo $! > $D/client.pid' && LC_ALL=C TZ=UTC ps -p $(cat $D/client.pid) -o lstart= > $D/client.start
```

Every stop, SIGTERM, `kill -9` or SIGINT, the live lane's included, goes through this block, with
`N` set to `server`, `worker` or `client` and `SIG` to `TERM`, `KILL` or `INT`. It reads both files
again and signals only when `ps` still prints the recorded start time and, for `rcm serve` and
`rcm worker`, the command line still holds this `data_dir`'s `--config $D/…`;
a client has no `--config` (its `data_dir` reaches it through `HOME` and `RCM_SERVER`), so for a
client the start time alone decides. Otherwise it sends nothing, prints both start times and the
command line, one per line, and ends with `BLOCKED`: the process you started is gone, the pid is
no longer yours, and the row is BLOCKED with those lines as its reason.

```sh
D=$HOME/.local/share/rcm-devtest-<topic>; N=worker; SIG=TERM; P=$(cat $D/$N.pid)
CMD=$(LC_ALL=C TZ=UTC ps -p $P -o command=)
MINE=no; [ $N = client ] && MINE=yes; printf '%s\n' "$CMD" | grep -qF -- "--config $D/" && MINE=yes
if [ -s $D/$N.start ] && [ "$(LC_ALL=C TZ=UTC ps -p $P -o lstart=)" = "$(cat $D/$N.start)" ] && [ $MINE = yes ]; then
  kill -$SIG $P
else
  echo "recorded start: $(cat $D/$N.start)"; echo "current start: $(LC_ALL=C TZ=UTC ps -p $P -o lstart=)"
  echo "command: $CMD"; echo BLOCKED
fi
```

Never by name: `pkill rcm`, `killall` or a `pgrep` pattern also hit the production launchd server
and worker on this build machine.

## Production is read-only, only with the owner's OK, and only by the main session

Only when the owner says so, only the main session: copy the database, read the copy with
`sqlite3 -readonly`, and hand the numbers on as a file (a lead's in file, a path in a verifier's
prompt). A lead or a verifier never reads production itself, whatever it is told. No worktree `rcm`
against the production config, `data_dir`, port or service venv; `docs/operating.md` is the owner's.
