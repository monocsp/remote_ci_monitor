# Codex 설계 리뷰 — M5b-2 워커 프로토콜 (2026-09-06)

`codex exec --sandbox read-only`(gpt-5.5) 에 `docs/m5b2-workplan.md` 초안과 server/store/worker 코드를 주고 받은 결과 그대로. 반영은 문서 §7.

**Findings**

- **must-fix:** Server restart semantics contradict current recovery. M5b-2 says remote `running` jobs must survive restart until heartbeat timeout, but `src/remote_ci_monitor/store.py:898` currently turns all `running`/`cancelling` jobs into `lost`. Recommendation: specify `recover_on_start()` must only recover local jobs, e.g. `worker_name IS NULL`; remote jobs keep `running`/`cancelling` and are resolved by `mark_lost_workers()`.

- **must-fix:** Heartbeat lane state is internally inconsistent. `docs/m5b2-workplan.md:33` defines `lanes: [{lane, job_id}]`, `docs/m5b2-workplan.md:42` says status uses heartbeat lane reports, but `docs/m5b2-workplan.md:62` says remote lane state is DB-derived. Recommendation: make DB-derived state the single source of truth; keep heartbeat job reports only for reconciliation, and rename/define one schema (`jobs` or `lanes`) consistently.

- **must-fix:** Claim does not define per-worker lane exclusivity. If the same worker calls `claim` twice for lane 1, DB-derived status can show two jobs on one lane and overcommit capacity. Current `claim()` (`src/remote_ci_monitor/store.py:768`) only filters queued jobs. Recommendation: validate `1 <= lane <= registered.lanes` and atomically refuse claim when that `(worker_name, lane)` already has `running`/`cancelling`; a partial unique index or transaction check is enough.

- **must-fix:** Lost vs late finish needs explicit terminal-state race behavior. `finish()` (`src/remote_ci_monitor/store.py:853`) already returns false once terminal, but the worker API spec only says “two finishes 409.” Recommendation: define all report routes as conditional on `worker_name` match and active state; if janitor already marked `lost`, late `finish`, `phase`, and `log` get `409` with current state and must not mutate logs, markers, or state.

- **must-fix:** Cancel confirmation conflicts with lost handling. `docs/m5b2-workplan.md:36` says unconfirmed cancel becomes `cancelled`; `docs/m5b2-workplan.md:41` says down workers’ `cancelling` jobs become `lost`. Recommendation: choose precedence. Defensive default: if the worker is unreachable, mark `lost`; only mark “worker did not confirm the cancel” as `cancelled` while the worker is still heartbeating past `cancel_kill_at`.

- **should:** Worker restart reconciliation can be bypassed. A restarted worker can `register` and immediately `claim` while old jobs for the same `worker_name` are still active unless heartbeat reconciliation runs first. Recommendation: require `register` or first `claim` to reconcile the worker’s active-job set, or deny claim while the worker has active jobs absent from the heartbeat report.

- **should:** Auth boundaries need a central route rule. The doc allows worker Bearer tokens for `/api/status` and `/events`, but forbids client API including `GET /jobs/{id}/log` `docs/m5b2-workplan.md:11`. Current handlers call generic `require_token()` for writes and read-token logic for logs. Recommendation: add a `require_client_token()`/`require_worker_token()` split and make `/jobs/*`, `/pause`, `/resume`, `/api/eta`, upload, cancel, priority reject `kind=worker`; `/worker/*` rejects non-worker.

- **should:** Long-poll claims can exhaust normal request capacity. `ThreadingHTTPServer` uses a global `max_concurrent_requests` semaphore `src/remote_ci_monitor/server.py:1734`; 20-second worker claims can occupy all slots. Recommendation: cap concurrent long-poll claims separately or make worker claim acquire a small worker-specific semaphore and return `503 Retry-After` when saturated.

- **should:** Resource validation needs sharper limits. The 4 MiB log route cap should be enforced from `Content-Length` before reading, with chunked rejected like `src/remote_ci_monitor/server.py:1394`. Host samples should reject bool-as-number and non-finite JSON values, cap nested dict/string sizes, and use server `sampled_at`.

- **nit:** Status JSON plan is mostly compatible, but name the existing serializer correctly. There is no `worker_json`; `server.workers[]` is emitted inline in `src/remote_ci_monitor/core/status.py:241`. Recommendation: state additive keys are added there, keep `schema_version` and existing keys unchanged, and keep `lane` as int.

- **nit:** Error class naming contradicts code conventions. The doc says `HttpError`, but the implementation uses `ApiError`. Recommendation: use `ApiError(status, message)` in the spec/tests.