# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.
The `/api/status` document carries its own `schema_version` — removing or changing the meaning
of a key bumps that number and is listed here.

## [Unreleased]

## [0.2.4] - 2026-09-08

The page speaks Korean, the host card says how much disk is left, and a running step's seconds
count up instead of standing still and then jumping.

### Added
- **How much disk is left, next to CPU and memory** (M5d-2). A host sample now carries `disk`
  (`used_bytes`, `free_bytes`, `total_bytes`, `path`) for the filesystem the jobs actually write
  to — the server's data directory, or a remote worker's own — not the root partition. The host
  card gains a meter reading `Disk 120 GB / 460 GB` with the remaining amount spelled out beside
  it, and Host pressure counts it. Two thresholds decide "full", because one gets it wrong: 85%
  used, **or** under 10 GiB free. A 4 TB disk at 90% is fine; a 60 GB disk at 80% cannot unpack a
  snapshot. API `schema_version` stays 1 — the key is added. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))
- **Step timers count up instead of jumping** (M5d-2). A running step used to show whatever second
  the server put in the last status document and then leap ten or twenty seconds when the next one
  arrived. Progress now ships `job_started_at` and per-step `started_at`/`ended_at`, and the page
  counts from those anchors once a second, so `analyze 9s` becomes `10s`, not `26s`. Finished jobs
  keep the server's fixed number, and a browser whose clock offset is unknown keeps showing the
  server's value rather than quietly counting on its own clock. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))
- **The queue says what is running** (M5d-2). Rows are split under two headings, "Running now" and
  "Waiting", each with a count, and an empty group says so instead of vanishing. A running row now
  carries what it is doing right now — `step 2/4 analyze 12s` — without being expanded. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))

- **The web page speaks Korean, and you can switch it** (M5d-1). It opens in Korean regardless of
  the browser's language and a button at the top right flips it to English; the choice is kept in
  that browser and follows into other tabs. Everything the page writes comes from one catalogue
  (`web/i18n.js`), including the server's outcome codes from M5d-0, so a job the server closed
  reads in the viewer's language while a summary the *job* printed stays exactly as the team wrote
  it. Identifiers never move: preset names, job keys, requester labels, commit shas, refs,
  repository URLs, step names, logs and the `rcm run …` command are the same in both languages.
- **The server says what happened in codes, not sentences** (M5d-0). Every summary the *server*
  writes now carries `summary_code` and `summary_args` beside the English `summary`, so a client
  can render the same fact in its own language: `cancelled_before_start`, `server_restarted`,
  `upload_abandoned`, `snapshot_too_big`, `worker_unreachable` and thirteen more, collected in one
  table (`core/outcome.py`). Arguments are raw values (bytes, seconds, exit codes, names) — the
  side that shows them decides how to format. A summary the *job* printed with `::rcm::summary::`
  has no code; it is the team's own sentence and travels unchanged. The four `*_error` fields, the
  server's `last_error` and a host's `gpu_note` gained `*_code` companions the same way. Database
  schema 5 → 6 (migrates on start); API `schema_version` stays 1 — keys are only added.

### Fixed
- **A remote worker's host sample was being thrown away whole** (regression from M5d-0). M5d-0 added
  `gpu_note_code` to the host document, but the heartbeat parser rejected any sample containing a
  key it did not know, so an up-to-date worker's CPU, memory and GPU silently disappeared from its
  pool's host card. Unknown top-level keys are now ignored and the sample is kept; a sample with no
  recognised key at all is still rejected, and every value is still validated as strictly as before.
  This is what lets a newer worker talk to an older server without going blank. ([#48](https://github.com/monocsp/remote_ci_monitor/pull/48))

### Changed
- Documentation: say plainly that **discovery resolves the address, not who you are**. A token is
  only ever created on the build machine (`rcm token add` writes to the server's database; there
  is no API that hands one out), so a session that has just found the server and shows a green
  `server` row next to a red `token` row is in the expected state, with one step left. Leaving
  `server` out of `client.toml` is also now stated to need 0.2.2+ on both machines, since an older
  client has no discovery and does not compare versions the way `rcm worker` does. And `mode 600`
  on a file holding a token is documented as refused, not merely advised.
  ([#44](https://github.com/monocsp/remote_ci_monitor/pull/44))

## [0.2.3] - 2026-09-08

The documentation rewrite, and discovery failures that explain themselves.

### Changed
- **The documentation was split up.** `README.md` is now a front door (what it is, install, a first
  job, where to go next) and the depth moved into `docs/`: a step-by-step
  [usage guide](docs/usage.md) with annotated screenshots in both languages,
  [configuration](docs/configuration.md), [operating the build machine](docs/operating.md), and
  `CONTRIBUTING.md`. `README.ko.md` mirrors the English README section for section.
  ([#41](https://github.com/monocsp/remote_ci_monitor/pull/41))
- The screenshots are generated by `tools/screenshots/` (headless Chrome capture, then the numbered
  boxes), so they can be regenerated when the UI changes. What each document owns and what a pull
  request has to update is written down in `docs/documentation.md` and the `docs` skill.
  ([#41](https://github.com/monocsp/remote_ci_monitor/pull/41))

### Fixed
- Discovery failures now say **why**. A responder that cannot send logs the errno by name
  (`mdns: send failed: EHOSTUNREACH`) instead of `OSError`, once per cause rather than per packet,
  and on macOS the line names the Local Network permission that a launchd service is denied by
  default. Two consecutive failures set `/api/health.advertise.error`, so a server that advertises
  but cannot be reached no longer reports itself as advertising; `rcm check` shows it as a `warn`
  row (`server cannot be discovered: …`) without failing the check.
  ([#40](https://github.com/monocsp/remote_ci_monitor/pull/40))

## [0.2.2] - 2026-09-08

LAN auto-discovery (M5c) and the fixes from the first week of real use.

### Added
- **LAN auto-discovery** (M5c): a server bound to a non-loopback address advertises `_rcm._tcp`
  over mDNS/DNS-SD (stdlib responder, coexists with mDNSResponder/avahi on port 5353; `advertise`,
  `advertise_name`); sessions with no `server` configured (or `server = "auto"`) find it —
  `rcm discover [--json]`, `rcm check` marks `(found on this network)`, `/api/health.advertise`.
  A multi-homed server (wired + Wi-Fi) listens on every interface and answers with the address
  that routes to the querier first; Tailscale (100.64/10) addresses sort last. `advertise = true`
  with a loopback `bind` logs a warning (found, but unreachable from other machines).

### Fixed
- A notification hook that times out is now killed as a whole process group — grandchildren
  (`python`, `gh`) no longer survive as orphans.
- `rcm run`/`rcm wait` treat a transient `500` from the server like `502/503/504` (retry within
  the 60 s budget) instead of giving up with exit 3.
- `rcm run` checks the token (`/api/whoami`) before building the snapshot, so a rejected token
  fails in a second instead of after packing a large tree.
- "cannot reach" messages are no longer prefixed twice.

### Changed
- README (both languages): "Run as a service" now records three deployment lessons — the
  service `PATH` is the presets' `PATH` (never put the rcm venv first), macOS TCC blocks launchd
  services from `~/Documents`, and notification hooks must read tokens from files.

## [0.2.1] - 2026-09-08

First fixes from real use (a Flutter monorepo gate moved onto rcm) plus the Korean guide.

### Fixed
- Snapshots skip nested git checkouts (`.claude/worktrees/<x>/`, submodule-like directories)
  instead of failing with `snapshot failed: Is a directory`.
- Notification hooks now inherit `PATH`, `HOME` and `LANG` from the server (a hook calling
  `date` or `$HOME` used to exit 1).

### Added
- `README.ko.md` — Korean end-user guide with annotated screenshots (`docs/images/ko/`).
- Notification env carries the job source (`RCM_SOURCE_MODE/REF/SHA/BASE_SHA/DIRTY/REPO`) and
  `RCM_INPUTS`, so a hook can post a commit status for the tested commit.

## [0.2.0] - 2026-09-07

M5: priority, content-addressed snapshot cache, notifications, worker pools and remote workers
(`rcm worker`). Database schema 3 → 5 (migrates on start). API schema stays `schema_version 1`
(additive keys only). Upgrade: install the same release on the server and on every worker
(`rcm worker` refuses a version mismatch).

### Added
- **Priority** (M5a): `rcm run --priority low|normal|high`, preset `priority` defaults that non-admin
  sessions cannot exceed, `rcm bump N` (admin), queue order by priority then age, `queue[].priority`
  and `presets[].priority` in the status document.
- **Snapshot cache** (M5a): content-addressed upload — `POST /jobs/{id}/tree/manifest` returns the
  missing hashes and `PUT …/tree` with `X-RCM-Tree: blobs` sends only those files; blobs are
  purged by age/size but never while an active job references them; `snapshot_cache`,
  `snapshot_cache_days`, `snapshot_cache_max_bytes`, `snapshot_cache_scope`; `source.uploaded_bytes`
  and `source.cached_bytes`; `--no-cache`.
- **Notifications** (M5a): `[[notify]]` rules (argv or url) on job completion, exactly once per
  (job, rule) including after restarts; `server.notify_failures`.
- Database schema version 3 (`priority` column, `blobs` and `notifications` tables); migrates on start.
- **Pools** (M5b-1): `pools[]` in the status document now has one entry per worker pool
  (`default` first); jobs carry `pool`, presets `pool`/`pools`, `rcm run --pool`, `rcm eta --pool`,
  `rcm jobs --pool`; a pool without workers shows `worker_down` and no ETA. Schema version 4
  (`pool` column). All additive keys.
- **Remote worker protocol** (M5b-2): worker tokens (`rcm token add NAME --worker`, `kind` column in
  `rcm token list`, `/api/whoami.kind`), `POST /worker/register|claim|heartbeat`,
  `GET /worker/jobs/{id}/tree` (cache jobs are assembled into a tarball on demand),
  `POST /worker/jobs/{id}/phase|log|finish`; a worker only ever touches jobs it claimed; a
  worker silent for `worker_timeout_seconds` is `down` and its running jobs become `lost`;
  re-registering closes the old jobs as lost; unconfirmed cancels are closed by the server.
  `server.workers[]` gains `worker` and `display_name`, `pools[].lanes` counts live remote
  lanes, remote host samples appear in the pool's `hosts[]` with `source = "worker"`,
  `/api/health` reports `pools_without_workers`. Config `worker_timeout_seconds`,
  `worker_heartbeat_seconds`, `worker_claim_wait_seconds`. Schema version 5 (`tokens.kind`,
  `jobs.worker_name`, `workers` table). Server restarts no longer mark remote running jobs lost.
- **`rcm worker`** (M5b-3): a remote worker process for a pool — `rcm worker --server URL --pool
  NAME --lanes N [--config worker.toml] [--data DIR] [--check] [--once]`, token via
  `RCM_WORKER_TOKEN` or `worker.toml`; downloads snapshots (cache jobs are assembled by the
  server), fetches `git_ref` jobs from its own `[[repos]]`, streams the raw log, reports the
  outcome, heartbeats with a host sample; SIGTERM reports running jobs as `lost` (`worker
  stopped`). Job execution (`runner.py`) is shared with the local worker. `examples/worker.toml`.
- **Multi-pool display** (M5b-4): remote pool headers in `rcm top` always name the pool
  (`queue — empty (pool linux)`, `· paused`), more than five remote worker pills fold into
  `+N workers` (down workers never fold), remote worker host samples are cards in the web Host
  section (`build-02 · pool linux`), `rcm check` gains a `pools` row that fails when every worker
  of a pool is down, and `server.workers[]` entries carry `pool`.

## [0.1.0] - 2026-09-06

First release. One package for the build machine (`rcm serve`) and every session (`rcm run`).
Python 3.11+ standard library only — zero runtime dependencies. API schema: `schema_version 1`.

### Added
- **Server, queue, worker** (M0): SQLite (WAL) queue that survives restarts, worker lanes running
  registered presets only (argv arrays, inputs via `RCM_INPUT_*`), working-tree snapshot upload
  with safe `tarfile` extraction, step markers, SIGTERM → grace → SIGKILL, per-client bearer
  tokens (SHA-256 at rest), `rcm run` / `rcm wait` with exit codes 0/1/2/3 (3 = unknown, never a
  fake failure). Jobs running during a restart become `lost`; nothing disappears from the queue.
- **Visibility** (M1): host sampler (CPU · memory · GPU on Apple Silicon and NVIDIA · top
  processes · 5-minute history), median-based ETAs with confidence, join of identical
  submissions, live events over SSE with polling fallback, `rcm top` / `eta` / `jobs` / `logs` /
  `presets` / `cancel` / `pause` / `resume`.
- **Web UI** (M2): static, dependency-free queue page — Your jobs · Not moving · Host pressure,
  queue with reasons and ETA confidence, host card with sparklines, recent results, log drawer,
  cancel, token entry, mobile layout, dark/light, Lost-connection banner, `#/jobs/N` deep links.
- **Operations** (M3): `git_ref` source mode for deploy presets (`rcm run deploy --ref v1.2.3`,
  commit pinned at submit time, local mirror, detached checkout), retention cleanup of logs,
  snapshots, workspaces and old job records, `read_auth = "basic"` (username = token name,
  password = token; writes stay Bearer-only), launchd and systemd unit examples, concurrency-group
  and signal e2e coverage.
- **Packaging** (M4): `rcm init server` / `rcm init client --server URL`, `rcm version --json`,
  `rcm check` Python row (with `--config server.toml` also data-dir and git rows), install smoke script, release workflow (GitHub Release + optional
  PyPI trusted publishing), Linux server `Dockerfile`.

### Known limits
- Windows is out of scope. GPU numbers exist only on Apple Silicon (`ioreg`) and NVIDIA
  (`nvidia-smi`); other machines report `gpu: null` with a note.
- No partial-upload resume: an interrupted snapshot upload ends as `cancelled`; run `rcm run` again.
- Basic auth is clear text — use it only behind TLS (Tailscale HTTPS or a reverse proxy).

[Unreleased]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.4...HEAD
[0.2.4]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/monocsp/remote_ci_monitor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/monocsp/remote_ci_monitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/monocsp/remote_ci_monitor/releases/tag/v0.1.0
