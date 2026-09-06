# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.
The `/api/status` document carries its own `schema_version` — removing or changing the meaning
of a key bumps that number and is listed here.

## [Unreleased]

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

[Unreleased]: https://github.com/monocsp/remote_ci_monitor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/monocsp/remote_ci_monitor/releases/tag/v0.1.0
