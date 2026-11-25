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
| `data_dir` | `~/.local/share/rcm-devtest-<topic>`, created fresh for this run. If it exists, pick a new name (`-<topic>-2`) instead of deleting one | a reused folder carries an old database and old blobs into the result |
| not `/tmp` | never under `/tmp` or `/private/tmp` | a downstream project's selftest rejects repositories under `/private/tmp`, so jobs fail for reasons that are not the change |
| config | its own file inside `data_dir`, never the production config and never a copy of it | `tools/guard_production.py` refuses a production `data_dir` reached through either |
| binary | the worktree's `./.venv/bin/rcm` (and `./.venv/bin/python`) by absolute path | the `rcm` on `PATH` may be the service's |

The config holds at least:

```toml
bind = "127.0.0.1"
port = <port>
data_dir = "<HOME>/.local/share/rcm-devtest-<topic>"
advertise = false        # otherwise discovery mixes this server up with production
```

No `[[notify]]` hook, unless notification is what is being tested; then a fake hook that appends what
it received to a file inside `data_dir`. Presets are fakes too: `sh -c 'echo …; exit <code>'` that
print the markers the scenario needs. Nothing in a test server pushes to GitHub, a store or a phone.

## The environment of every command

```sh
D=$HOME/.local/share/rcm-devtest-<topic>
cd <abs worktree> && HOME=$D/home RCM_SERVER=http://127.0.0.1:<port> ./.venv/bin/rcm …
```

- `HOME=<data_dir>/home` so the client never reads the production `client.toml`.
- `RCM_SERVER=http://127.0.0.1:<port>` on every client command; without it the client may discover a
  server on the network, and the first one it finds can be production.
- `rcm token` takes `--config` **before** the subcommand: `rcm token --config $D/server.toml add …`.
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
- **Repeatable runs:** a script at `scripts/acceptance/<topic>_live.sh`, modelled on `m5s_live.sh`: an
  allow-listed base folder, fake presets and hooks, every server stopped on exit (even on failure),
  verdict lines in `out/summary.txt`, exit 1 if any line is FAIL.
- **Screenshots:** `tools/screenshots` neutralises host, paths, ports and tokens; look at the image anyway.

## Parallel servers and cleanup

Two verifiers may each run a server at the same time as long as each has its own port and `data_dir`;
the build machine also runs `rcm-ci`, so keep it to two. Record the server's pid when you start it and
stop that pid when you are done. Leave the `data_dir` in place until the report has collected its
evidence; remove it afterwards by its exact path, never with a glob.

## Production is read-only, and only with the owner's OK

Production jobs on the build machine may be looked at only when the owner says so: copy the database
and read the copy with `sqlite3 -readonly`. No `rcm` command from a worktree build against the
production config, `data_dir`, port or service venv; the procedure for production lives in
`docs/operating.md` and is the owner's.
