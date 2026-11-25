# Test server — the only place for its rules

Any `rcm serve`, `rcm worker` or `rcm token` that a worktree build runs for live verification,
reproduction, screenshots or an acceptance script follows this page. Other skills and agents link
here and do not repeat it. It exists because the same mistakes kept coming back: a worktree build
migrated a database it should not have opened (AGENTS.md: a verifier's `rcm token add` turned a v7
dev database into v24), and a test server's client found the production server by discovery (M5n).

## Where it runs

| what | rule | why |
|---|---|---|
| port | a free one in 8790–8899, checked first: `lsof -iTCP:<port> -sTCP:LISTEN` prints nothing. Never 8787 (production) or 8788 (other sessions' dev servers) | two servers on one port answer for each other |
| `data_dir` | `~/.local/share/rcm-devtest-<topic>`, created fresh for this run. If it exists, pick a new name (`-<topic>-2`); never `rm -rf` an existing one, by hand or in a script | a reused folder carries an old database and old blobs into the result, and a deleted one may be another run's evidence |
| not `/tmp` | never under `/tmp` or `/private/tmp` | a downstream project's selftest rejects repositories under `/private/tmp`, so jobs fail for reasons that are not the change |
| config | its own files inside `data_dir` (`server.toml`, `worker.toml`), never the production config and never a copy of it | `tools/guard_production.py` refuses a production `data_dir` reached through either |
| binary | the worktree's `./.venv/bin/rcm` (and `./.venv/bin/python`) by absolute path | the `rcm` on `PATH` may be the service's |

`<data_dir>/server.toml` is a complete config the loader accepts; change only the port, the folder
name and the presets:

```toml
[server]
bind = "127.0.0.1"
port = 8790
data_dir = "~/.local/share/rcm-devtest-example"
advertise = false   # otherwise discovery mixes this server up with production

[[presets]]
name = "ok"
description = "Fake preset: prints one step marker and exits 0"
argv = ["sh", "-c", "echo ::rcm::step::hello; echo ok"]
timeout_seconds = 60
```

A worker gets `<data_dir>/worker.toml` with its own folder; its token goes in `RCM_WORKER_TOKEN`:

```toml
server = "http://127.0.0.1:8790"
data_dir = "~/.local/share/rcm-devtest-example/worker"
```

No `[[notify]]` hook, unless notification is what is being tested; then a fake hook that appends what
it received to a file inside `data_dir`. Presets are fakes too: `sh -c 'echo …; exit <code>'` that
print the markers the scenario needs. Nothing in a test server pushes to GitHub, a store or a phone.

## The environment of every command

```sh
D=$HOME/.local/share/rcm-devtest-<topic>
cd <abs worktree> && HOME=$D/home ./.venv/bin/rcm serve --config $D/server.toml
cd <abs worktree> && HOME=$D/home ./.venv/bin/rcm worker --config $D/worker.toml
cd <abs worktree> && HOME=$D/home ./.venv/bin/rcm token --config $D/server.toml add …
cd <abs worktree> && HOME=$D/home RCM_SERVER=http://127.0.0.1:<port> ./.venv/bin/rcm …
```

- `--config` is always explicit. Without it `rcm serve` reads `$RCM_CONFIG` or a `./rcm.toml` in the
  worktree, which is how a worktree build once migrated a v7 dev database to v24 (AGENTS.md).
- `HOME=<data_dir>/home` on every **rcm** command (client, server, worker, token), so the client never
  reads the production `client.toml`. It is not for the browser: the CDP `Chrome` helper keeps your
  real `HOME`, because headless Chrome on macOS cannot open an http page from a different one
  (`tools/screenshots/README.md`).
- `RCM_SERVER=http://127.0.0.1:<port>` on every client command; without it the client may discover a
  server on the network, and the first one it finds can be production.
- `rcm token` takes `--config` **before** the subcommand, as above.
- Never set `RCM_SERVER_DATA_DIR` to production, and never pass `--config`/`--data-dir` that resolve to
  it. Do not route around `tools/guard_production.py`; a refusal means stop.
- `RCM_ALLOW_MIGRATE=1` only when migrating that database is the point of the test.
- Check before starting: `env | grep -E '^(RCM_|XDG_)'` shows nothing you did not set.

## Driving it

- **CLI:** `--json` output and exit codes. `rcm wait` exit 3 is unknown, never a failure.
- **Web:** the CDP `Chrome` helper in `tests/test_web_browser.py`, page opened with `?poll=1`, and
  `page_errors()` must return `[]` (a missing collector raises; that is a failure, not an empty list).
- **Two processes:** the server and worker helpers in `tests/test_e2e_worker.py` (`E2EServer`,
  `WorkerProc`, `Stack`, `clean_env`, `free_port`) rather than new ones.
- **Repeatable runs:** a script at `scripts/acceptance/<topic>_live.sh`: an allow-listed base folder,
  fake presets and hooks, every server stopped on exit (even on failure), verdict lines in
  `out/summary.txt`, exit 1 if any line is FAIL. `scripts/acceptance/m5s_live.sh` has that shape, but
  it starts with `rm -rf` of its base folder: that part is not a model; a new script picks a new folder.
- **Screenshots:** `Chrome.screenshot()` from the same CDP helper, saved under this server's `data_dir`
  (or `.verify/` of the worktree), never a `/tmp` folder. Nothing redacts them for you: the port, paths,
  host name and any token on the page are yours to crop or retake before an image leaves `data_dir`.
  `tools/screenshots` is not a live-capture tool — it runs only its own fixed demo on its own server
  for `docs/images/ui/`.

## Parallel servers and cleanup

Two verifiers may each run a server at the same time as long as each has its own port and `data_dir`;
the build machine also runs `rcm-ci`, so keep it to two. Record the server's pid when you start it and
stop that pid when you are done. Leave the `data_dir` in place until the report has collected its
evidence; remove it afterwards by its exact path, never with a glob.

## Production is read-only, only with the owner's OK, and only by the main session

Production jobs on the build machine may be looked at only when the owner says so, and only by the
main session — never by a lead or a verifier: copy the database and read the copy with
`sqlite3 -readonly`, then hand the numbers on as a file (a lead gets them through its in file). No
`rcm` command from a worktree build against the production config, `data_dir`, port or service venv;
the procedure for production lives in `docs/operating.md` and is the owner's.
