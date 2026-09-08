# Codex 교차 리뷰 — M5e 잡 산출물 되돌려주기 (2026-09-08)

> 명세를 쓰기 **전**, 설계 초안(`PROMPT-m5e.md`)을 `codex exec --sandbox read-only`(gpt-6-astra,
> reasoning high)에 물었다. 프롬프트는 `docs/reviews/2026-09-08-codex-m5e-prompt.md`.
> 코덱스가 코드에 대해 주장한 사실은 따로 확인했다 — 합류자 기본키가 토큰 이름이라는 것
> (`store.py` `joiners` 테이블), `data` 필터가 절대 경로를 **거부가 아니라 상대화**한다는 것
> (`PLAN.md` 「서버 풀기」), `assemble_from_manifest` 가 심링크를 만든다는 것, 두 자재화 함수가
> 목적지를 **먼저 지운다**는 것, `_finish` 가 `running` 에서 뺀 **뒤에** 완료를 보고한다는 것,
> 문서는 같은 PR 에서 고친다는 것(`CONTRIBUTING.md`) — 전부 맞았다.

## Biggest risks first

**The problem is real, but I would not turn this plan into a spec unchanged.** The most expensive mistakes would be:

1. **Destroying or overwriting the submitting tree.** The suggested extraction helper deletes its destination before extracting. Safe download, safe extraction, and safe replacement of existing files are three separate operations.
2. **Deleting after the wrong “session” acknowledges.** The repository tracks ownership by token name, not by submitting session. Its joiner model cannot implement the proposed all-session acknowledgement rule.
3. **Claiming an exfiltration boundary that does not exist.** Preset globs restrict collection paths; they cannot prevent executed code from copying host secrets into an allowed file.
4. **Losing remote outputs during completion.** Upload, heartbeat membership, cancellation, terminal-state publication, and workspace cleanup must form one coordinated lifecycle.
5. **Hiding the job’s actual result behind an artifact warning.** Artifact delivery needs its own result fields. Replacing the job summary with “artifacts dropped” would discard execution information.

My recommendation is **TTL-only retention for M5e, explicit fetching, explicit overwrite permission, and collection from failed jobs too**. This deliberately postpones immediate deletion and therefore requires an owner decision. If immediate deletion remains mandatory, the spec needs the additional receipt protocol described below.

This is a source review; I did not edit files or run tests.

## 1. What the source actually says

| Plan claim | Verdict and source evidence |
|---|---|
| Successful workspaces are deleted. | **Correct on normal completion.** Local execution calls `store.finish`, then removes the workspace unless the outcome is non-success and `keep_workspace_on_failure` is enabled: [worker.py:368](../../src/remote_ci_monitor/worker.py#L368). Remote execution likewise finishes before cleanup: [remote_worker.py:451](../../src/remote_ci_monitor/remote_worker.py#L451). Deletion uses `ignore_errors=True`, so this is attempted cleanup, not a guarantee of reclaimed space. |
| Only logs come back. | **Correct for produced files, too broad literally.** Clients also retrieve structured status, outcomes, progress and events. There is no session-facing route returning produced workspace files. The worker-only tree download returns the **input snapshot**, not outputs: [server.py:1653](../../src/remote_ci_monitor/server.py#L1653), [remote_workers.py:375](../../src/remote_ci_monitor/remote_workers.py#L375). |
| The module docstring lists every route. | **Wrong.** It omits routes including tree manifests, priority, ETA and events. Inspect the router, not that abbreviated list: [server.py:3](../../src/remote_ci_monitor/server.py#L3), [server.py:1586](../../src/remote_ci_monitor/server.py#L1586). |
| Remote workers stream logs to the server. | **Correct, but this is not a reliable artifact transport template.** Logs are batched, retried, and older buffered bytes can be discarded after the buffer limit. Server ingestion appends bytes without a sequence number or idempotency key. An ambiguous response can therefore produce duplicate log bytes on retry. Artifacts need immutable, verified publication: [remote_worker.py:97](../../src/remote_ci_monitor/remote_worker.py#L97), [remote_workers.py:406](../../src/remote_ci_monitor/remote_workers.py#L406). |
| Upload unpacking already supplies “no absolute paths, no symlinks” rules. | **Wrong.** `extract_tree` uses the tar data filter; contained links are allowed, and leading slashes can be normalized rather than rejected. `PLAN.md` explicitly documents the latter. Manifest assembly also deliberately creates symlinks. Most critically, both materializers delete an existing destination directory: [materialize.py:44](../../src/remote_ci_monitor/materialize.py#L44), [materialize.py:80](../../src/remote_ci_monitor/materialize.py#L80), [PLAN.md:162](../../PLAN.md#L162). |
| The retention janitor can carry an artifact TTL. | **Yes as an extension, not by reusing its current purge operation.** It currently removes entire `jobs/<id>` and `workspaces/<id>` directories. `artifacts_purged_at` already means logs/snapshots/workspace cleanup. Metadata deletion depends on that marker: [janitor.py:95](../../src/remote_ci_monitor/janitor.py#L95), [store.py:467](../../src/remote_ci_monitor/store.py#L467). |
| Requester plus joiners identifies all eligible sessions. | **Wrong.** The joiner primary key is `(job_id, name)`, with `name` being the token name. Labels are descriptive metadata. Repeated submissions using the requester’s token add no joiner; repeated submissions using another token collapse into one joiner. Ownership also compares token names: [store.py:103](../../src/remote_ci_monitor/store.py#L103), [store.py:599](../../src/remote_ci_monitor/store.py#L599), [core/model.py:240](../../src/remote_ci_monitor/core/model.py#L240). |
| Artifact reads should follow log authorization. | **Correct policy.** Use a valid requester/joiner/admin token, with an explicit worker-kind rejection. The existing log predicate itself checks ownership/admin rather than token kind. Reads support Bearer, plus Basic when configured; destructive acknowledgements must be Bearer-only: [server.py:455](../../src/remote_ci_monitor/server.py#L455). |

Two further factual qualifications matter:

- **Joining requires matching preset, inputs and source identity, not merely the same tree.** The current key does not include the requested pool. A repeated submission can join an existing job whose pool differs from the new request. M5e should use the actual job’s policy and pool, not the submitting client’s assumptions: [core/queue.py:88](../../src/remote_ci_monitor/core/queue.py#L88), [server.py:835](../../src/remote_ci_monitor/server.py#L835).
- **Server restart does not mark every running job lost.** Local jobs are recovered to `lost`; remote jobs remain active pending worker/heartbeat reconciliation: [store.py:998](../../src/remote_ci_monitor/store.py#L998).

## 2. Missing failure modes, ranked by cost

### Critical: restoring into a changing working tree

“Show the count before writing” is not overwrite authorization and does not protect local edits.

A developer can submit, edit a golden while waiting, then receive an older replacement. The client must remember the exact snapshot root, preserve a baseline for replacement candidates, and reject conflicting local changes.

The root is currently `--dir` or the invocation directory, not necessarily the Git repository root: [cli.py:281](../../src/remote_ci_monitor/cli.py#L281). Restoration must use that same root.

Required distinctions:

- New file.
- Identical existing file.
- Existing file eligible for explicitly authorized replacement.
- Local conflict.
- Unsafe destination, including a symlink in any parent component.

Validate the entire manifest before changing anything. Download into private staging, verify every file, then apply. Individual atomic replacements do **not** make 64 replacements atomic together. On interruption, retain staging and an application journal; report partial application honestly.

Also, the current snapshot code hashes files and later reads them again to build the tar. Do not assume `Snapshot.entries` necessarily describes the bytes ultimately transmitted if files changed between those operations: [client.py:345](../../src/remote_ci_monitor/client.py#L345). Establish the restore baseline from the actual submitted representation.

### Critical: collection is not sandboxing

The runner executes the preset with the worker’s OS privileges. It sets a working directory and process group; it does not establish filesystem isolation. Presets can invoke interpreters or shell scripts, and `HOME` is passed through by default: [runner.py:93](../../src/remote_ci_monitor/runner.py#L93), [runner.py:152](../../src/remote_ci_monitor/runner.py#L152).

Consequently:

- Rejecting symlinks prevents the **collector** from accidentally reading through links.
- Rejecting hardlinks prevents another easy alias to an outside inode.
- Neither prevents the job from copying secret bytes into `test/example/goldens/result.png`.
- A declared output directory has the same limitation.
- Hashes prove transfer integrity, not that a file was legitimately generated.

The spec should describe a trusted-job execution model and a defensive collector. It must not promise protection against hostile code running as the worker user.

### High: remote completion has an existing heartbeat race

`RemoteWorker._finish` removes the job from `self.running` **before** reporting completion. Heartbeats send that set, and the server marks active jobs omitted from it `lost`: [remote_worker.py:455](../../src/remote_ci_monitor/remote_worker.py#L455), [remote_workers.py:494](../../src/remote_ci_monitor/remote_workers.py#L494).

Artifact transfer must remain inside the reported active lifetime. Otherwise, adding a slow upload makes this race much easier to hit.

There is another deadline: the server can close unconfirmed cancellations independently of the worker. A successful ownership check at the start of upload does not authorize publication at its end: [remote_workers.py:535](../../src/remote_ci_monitor/remote_workers.py#L535).

### High: cleanup has several independent owners

The fetch endpoint must serve an immutable server bundle, never the live workspace. Then workspace deletion cannot race a download.

But deleting that bundle does **not** necessarily remove all copies:

- Failed workspaces are retained by default.
- Remote retained workspaces/job directories are swept on worker startup after their age threshold, not by the server’s retention thread: [remote_worker.py:465](../../src/remote_ci_monitor/remote_worker.py#L465).

The owner’s “keep things lean” goal needs separate accounting for published bundles, upload staging and retained workspaces. Do not silently change `keep_workspace_on_failure`.

### High: disk limits must cover admission, not just completed files

A server-wide cap checked after writing is too late. Concurrent collectors/uploads need reservations that include temporary storage and archive overhead.

Specify behavior for:

- Disk full during collection, upload, rename, SQLite commit and deletion.
- Many zero-byte files exhausting metadata or inodes.
- Sparse files with huge logical sizes.
- Excessive path depth, path length and archive metadata.
- Slow uploads/downloads occupying HTTP threads and delaying heartbeats.
- Staging files left by crashes.

A cap on artifacts is not a cap on the entire server disk: jobs can still fill their workspaces.

### High: missing, empty and incomplete must remain different

These cases cannot all become `files: []`:

- Artifacts not configured.
- Collection completed and matched nothing.
- Job never started.
- Collection exceeded a limit.
- Upload failed.
- Server restarted during finalization.
- Bundle expired.
- Bundle metadata exists but its file is missing.

In particular, one summary code cannot preserve both “tests failed” and “artifact upload failed.” The existing summary also carries arbitrary job-authored text: [worker.py:145](../../src/remote_ci_monitor/worker.py#L145).

### Medium: eligibility changes and acknowledgements are underspecified

A joiner can leave, losing ownership. This happens through cancellation and best-effort Ctrl-C detach: [server.py:1296](../../src/remote_ci_monitor/server.py#L1296), [cli.py:427](../../src/remote_ci_monitor/cli.py#L427).

An acknowledgement design needs answers for revoked tokens, departing joiners, legacy clients, lost submission responses, administrator downloads and multiple sessions sharing one token.

Also, the done criteria contradict themselves: “only after ack” must explicitly say **“after the required acknowledgements, or expiry.”**

## 3. Which judgments I would change

### Retention: TTL first; acknowledgements only for the explicit deletion requirement

| Policy | Assessment |
|---|---|
| Delete when download starts or finishes server-side | Reject. Neither proves successful receipt. |
| Delete after first client ack | Reject while promising both joined sessions can retrieve. |
| Reference-count requester/joiner tokens | Does not count sessions. Still needs durable ack records and membership rules. |
| TTL-only | Recommended M5e scope: bounded retention, repeated retrieval, no consumer-registration protocol. |
| Per-submission receipts plus TTL | Appropriate if immediate deletion is mandatory, but materially larger than the plan implies. |

Two-phase acknowledgement is justified by **early deletion**, not by downloading files safely. TTL-only still requires staging, hashing and safe application.

An ack should mean that the client has durably retained a verified copy—not merely hashed a response buffer. If the command promises restoration into the tree, send it after successful application, or explicitly report that a recoverable staged copy remains.

### Collect failed-job artifacts

Yes. Failure diagnostics are often more valuable than successful outputs. A failed golden comparison should return its diff images **when the preset’s declared patterns include them**. The proposed goldens-only pattern will not magically include diagnostics written elsewhere.

For M5e:

- Attempt collection after ordinary success and failure.
- After cancellation or timeout, allow bounded best-effort collection once execution has stopped; label the outputs as coming from that outcome.
- Do not manufacture “empty” after materialization failure.
- Do not automatically resurrect a lost job to collect or upload outputs.
- Preserve the execution state, exit code, summary and failed step regardless of artifact failure.

### Keep preset globs for this milestone

A dedicated `RCM_ARTIFACT_DIR` is simpler to enumerate and makes output intent explicit. However, it requires wrappers to copy existing tool outputs into that directory and preserve their intended destination paths. It also does not close the malicious-copy exfiltration hole.

For the stated in-place golden workflow, I would keep **preset-declared relative globs**, with one narrowly specified grammar. Do not build both mechanisms in M5e.

Reject absolute patterns, `..`, empty components and a whole-workspace catch-all such as `**`. Exclude `.git` components independently of matching. Walk without following directory links; validate matched files through file descriptors, not a `resolve()` check followed by an unrelated open.

The reusable starting point is the path/tree-shape logic in [core/manifest.py:68](../../src/remote_ci_monitor/core/manifest.py#L68), extended for regular files only and portable destination names. It is not `extract_tree`.

## 4. Concrete proposed M5e contract

The following is my recommended **TTL-only baseline**. The early-deletion extension follows it.

### Storage and lifecycle

Use one immutable bundle per job, under a new top-level directory such as:

`<data_dir>/artifacts/<job_id>/`

Keep it separate from `jobs/<id>` so existing log retention cannot accidentally delete it. Do not put outputs in the shared snapshot blob cache.

Use a restricted **uncompressed tar** plus a manifest of relative paths, sizes and SHA-256 hashes. PNG outputs give little reason to introduce compression/decompression limits in the first milestone. Accept only regular-file entries; reject links, special files, sparse representations, duplicates and unsupported archive metadata.

Both producer and receiver must bound entry counts, actual bytes and metadata—not trust declared sizes alone. Check case-folded and Unicode-normalized collisions, including collisions with existing destination entries. Never recursively extract uploaded content into a server workspace.

**Local placement:** after `run_job` returns at [worker.py:346](../../src/remote_ci_monitor/worker.py#L346), before `store.finish` at line 368:

1. Determine execution outcome.
2. Collect into private staging with a deadline and byte/file limits.
3. Verify and durably install the immutable bundle.
4. Commit artifact disposition and terminal job state together.
5. Publish completion.
6. Apply existing workspace cleanup.

Collection errors must be caught separately; they must not reach the outer worker exception path that marks the job failed and the lane down.

**Remote placement:** after `run_job` and final log flush, before `_finish` at [remote_worker.py:439](../../src/remote_ci_monitor/remote_worker.py#L439):

1. Collect locally.
2. Upload and obtain a verified staging receipt.
3. Report completion referencing that exact bundle.
4. Server commits artifact disposition and job outcome together.
5. Remove heartbeat membership only after confirmed completion or authoritative terminal rejection.
6. Clean up; preserve a bounded local spool if transfer outcome is uncertain.

Keep the job active during finalization. Add artifact progress independently of the existing execution phase. Do not invent log output to suppress “stuck” detection.

Collection has its own deadline. Exceeding it drops artifacts; it must not retroactively turn a completed process into `timed_out`. A cancellation accepted before terminal commit aborts further artifact work and must be resolved consistently by that transaction; cancellation after commit remains `409`.

File installation and SQLite commit cannot be one transaction. Recovery must reconcile staged/orphan files and missing committed files before starting workers. A crash before completion may leave a local job `lost`; a bundle’s existence must never turn it into success.

### Routes

“Reader” below means a valid requester/joiner/admin token, explicitly excluding workers. Support configured Basic authentication on reads; writes remain Bearer-only.

| Route | Auth | Proposed response |
|---|---|---|
| `GET /jobs/{id}/artifacts` | Reader | `200` disposition; include protected manifest when ready. `401`, `403`, `404` for auth/ownership/missing job. |
| `GET /jobs/{id}/artifacts/archive` | Reader | `200` immutable archive; `409` pending/not available; `410` expired or acknowledged-and-purged; `503` if storage is unexpectedly unavailable. |
| `HEAD /jobs/{id}/artifacts/archive` | Reader | Same availability and headers, no body or side effects. |
| `PUT /worker/jobs/{id}/artifacts` | Assigned worker Bearer | `201` verified staging receipt; `200` identical retry while active; `409` terminal job or conflicting bundle; `400` malformed content; `411` missing length; `413` per-job limit; `415` wrong media type; `503` capacity/storage unavailable. |
| `GET /worker/jobs/{id}/artifacts` | Assigned worker Bearer | `200` upload disposition and job state, including after completion; no artifact contents. Resolves ambiguous upload/finish responses. |
| Existing `POST /worker/jobs/{id}/finish` | Assigned active worker | Add optional artifact receipt/disposition fields; preserve current success and terminal-rejection semantics. |

Apply normal `401/403/404/405` handling throughout. New downloads must use streaming I/O: the generic client helper reads the entire response into memory. The existing worker tree downloader is a closer starting point: [client.py:422](../../src/remote_ci_monitor/client.py#L422), [client.py:110](../../src/remote_ci_monitor/client.py#L110).

Require `Content-Length`; use bounded transfer slots and timeouts. A waiting artifact transfer must not consume all request capacity needed for heartbeat/cancel/status. Downloads use `Cache-Control: no-store`, attachment disposition and no token-bearing URLs.

At expiry, reject new downloads. Coordinate open-file acquisition with deletion so an already accepted bounded transfer can finish. Reclaim space after its reader releases the file.

### Configuration

These are proposed policy choices, not measurements of the owner’s PNGs.

| Key | Proposal |
|---|---|
| `presets.artifacts` | Relative glob list; default `[]`, disabled. |
| `server.artifact_retention_hours` | Proposed default `24`; fixed expiry, not extended by reads. |
| `server.max_artifact_bytes` | Per-job logical file-byte cap; require an explicit value when enabling artifacts. |
| `server.artifact_storage_max_bytes` | Global bundle/staging budget; require an explicit operator-selected value. |
| `server.max_artifact_files` | Proposed default `10000`; independently enforced from bytes. |
| `server.artifact_timeout_seconds` | Proposed default `60` for collection/finalization work; validate and document separately from process timeout. |

Reuse `retention_sweep_interval_seconds`; disclose that physical expiry cleanup occurs on a sweep, currently hourly by default. Use indexed expiry queries rather than filtering an arbitrary limited batch of old jobs.

Freeze the effective artifact policy for each job and return it in the worker claim. Upload validation must use that frozen policy. A joiner inherits the existing job’s policy.

Reject admission when capacity is unavailable; do not evict an unexpired bundle merely to admit a newer one. Keep quota reservations until physical cleanup succeeds.

### Database migration

Add migration **7**, following the current migration-6 structure: [store.py:173](../../src/remote_ci_monitor/store.py#L173).

Proposed table:

| Table | Columns |
|---|---|
| `job_artifacts` | `job_id PRIMARY KEY`, `policy_json`, `state`, `manifest_json`, `archive_sha256`, `file_count`, `total_bytes`, `archive_bytes`, `reserved_bytes`, `collection_started_at`, `ready_at`, `expires_at`, `purged_at`, `reason_code`, `reason_args_json` |

Index expiry/state. Artifact metadata is nullable until known.

Use internal staging states; publicly distinguish `disabled`, `pending`, `collecting`, `uploading`, `ready`, `empty`, `dropped`, `unavailable`, `expired`.

Old jobs must not be backfilled as successfully empty. Mark their artifact availability unknown/legacy.

Leave `jobs.artifacts_purged_at` unchanged. Extend metadata deletion so it cannot remove ownership records while a returned bundle or staging reservation still exists. Preserve a disposition tombstone until job metadata expires.

### Additive JSON and UI

Add an `artifacts` object to both queue and recent job serializers, hence also `GET /jobs/{id}`:

- `state`
- `file_count`
- `total_bytes`
- `archive_bytes`
- `ready_at`
- `expires_at`
- `purged_at`
- `reason_code`
- `reason_args`

Unknown counts remain `null`; only completed empty collection reports zero.

Expose file paths, per-file hashes and download details only through the protected artifact endpoint. Public status/events receive aggregate disposition, not filenames or receipts.

Add `server.artifact_storage` with known stored/reserved bytes, configured cap, last sweep time and error code. Keep existing execution `summary`, `summary_code`, `summary_args`, `exit_code` and `wait_exit_code` meanings.

Publish artifact changes and invalidate the status cache. Do not use `_publish_job` blindly for post-completion expiry: it emits `job_finished` for any terminal job. Use a separate artifact-change event and add it to the UI’s refetch listeners: [server.py:367](../../src/remote_ci_monitor/server.py#L367), [web/app.js:812](../../src/remote_ci_monitor/web/app.js#L812).

The UI should show availability, count/bytes, expiry and explicit collection/upload errors alongside the original outcome. Use Korean-first catalog strings. M5e needs a protected manifest view and a copyable CLI command; it does not need an image gallery or browser-based working-tree restoration.

### CLI

Proposed interface:

- `rcm artifacts JOB_ID` — inspect disposition and manifest.
- `rcm artifacts JOB_ID --fetch --output DIR` — retrieve into an explicitly selected directory.
- `rcm run PRESET --fetch-artifacts` — retrieve after completion into the original snapshot root.
- `--force` — explicitly authorize replacement of existing regular files, following the existing CLI spelling.
- `--dry-run` — preview paths and comparisons without application.

For integrated `run`, even `--force` should refuse files changed locally since the submitted baseline. Retain staging and direct the user to an explicit output directory for conflicts. Standalone restoration without a submission baseline requires explicit replacement authorization and destination rechecks.

Reject `--no-wait --fetch-artifacts` before submission. After plain `--no-wait`, print the independent retrieval command. For `git_ref`, require an explicit output directory: there is no corresponding submitted local tree.

Compare bytes, not timestamps or image pixels. Report `new`, `changed`, `unchanged`, `conflicted` and `applied`; “12 changed” must say whether that means compared or actually written.

Preserve normal `rcm wait` exit codes. For the new integrated fetch mode, add a separate `artifact_fetch` result and a documented command exit code for delivery/application failure; retain the original `wait_exit_code`. Never return overall success when explicitly requested delivery failed, and never relabel `lost` as a test failure.

### If immediate deletion is mandatory

Add this extension instead of pretending existing joiners are sessions:

- Every upgraded submission registers a **per-invocation consumer receipt**, including submissions sharing one token.
- Add `artifact_consumers(job_id, consumer_id, token_name, created_at, acked_at, waived_at)` with a unique consumer key.
- Legacy submissions without receipt support pin retention until TTL. Unknown consumers must fail toward retention.
- Freeze the consumer set in the same transaction that marks the job terminal. Joining and finalization must serialize.
- Leaving the join list removes read permission and waives that token’s outstanding consumer obligations. Token revocation likewise cannot retain an impossible acknowledgement requirement indefinitely.
- Administrator reads do not acknowledge on somebody else’s behalf.
- Add `POST /jobs/{id}/artifacts/ack`, Bearer-only, requiring current job ownership plus the caller’s receipt and exact bundle digest. Return `200` for accepted and duplicate acknowledgements, including an already-acked purged bundle; `409` for wrong bundle/not ready; `410` for expiry before acknowledgement.
- Record acknowledgement and determine deletion eligibility transactionally. Two concurrent final acknowledgements must schedule one deletion.
- Failed deletion remains pending, consumes quota, and is retried. Do not report physical deletion before it happens.

Persist the receipt on the client before depending on it. This is a delivery protocol with recovery state, not just one POST after a GET.

**Deliberately exclude from M5e:** output caching/deduplication, multiple artifact versions per job, range/resumable downloads, automatic reruns or late uploads to lost jobs, server-side image comparison, arbitrary output-directory mappings, browser restore and deletion of local files absent from the manifest.

## 5. Which owner questions are real decisions?

| Question | Answer |
|---|---|
| Every joiner or first pull? | **Real owner decision, but the useful choice is TTL-only versus per-submission receipts.** First-pull deletion contradicts the proposed promise that both sessions can retrieve. Existing token ownership does not resolve this. |
| Fetch by default? | **Answer from compatibility conventions: opt-in.** Existing `run` uploads, waits and prints a result; adding unsolicited working-tree writes changes established behavior. `--fetch-artifacts` makes the new side effect explicit: [cli.py:345](../../src/remote_ci_monitor/cli.py#L345). |
| Overwrite by default? | **Answer from the existing write convention: no.** `rcm init` refuses overwrite without `--force`: [cli.py:817](../../src/remote_ci_monitor/cli.py#L817). This is a strong precedent, not a substitute for conflict checks. |
| TTL and global cap? | **Real operational decisions.** The repo’s existing snapshot-cache limits are not evidence of available disk or artifact demand. Keep 24 hours as a proposal; choose the storage budget from the operator’s disk allocation. |

There is one additional owner decision: **does “delete artifacts” mean deleting the published bundle, or also changing failed-workspace retention?** Those are materially different promises.

## 6. PR split and tests before implementation

I would split this into four implementation PRs after the reviewed spec:

1. **Artifact core and persistence:** manifest/path rules, frozen config, migration, quota reservations, immutable storage, recovery and TTL. Include configuration/operations docs.
2. **Worker completion:** local and remote collection, upload protocol, heartbeat/completion ordering, outcome separation and API exposure. Ship both worker paths together.
3. **Client retrieval/application:** streaming verification, destination protection, conflict handling, CLI results and both usage guides.
4. **UI and acceptance:** status presentation, Korean/English strings, protected inspection, screenshots and isolated end-to-end verification.

If early deletion is retained, put receipt persistence/protocol in PRs 1–2 and client acknowledgement in PR 3; keep early deletion disabled until the complete contract works.

**“PR, then docs” is wrong for this repository.** User-visible changes require their docs in the same PR: [CONTRIBUTING.md:39](../../CONTRIBUTING.md#L39). A final UI/docs PR may finish screenshots; it cannot repair undocumented earlier changes.

I would require these six test groups to fail before implementation:

1. **Local data protection.** Traversal, absolute paths, `.git`, directory symlinks, hardlinks, special files, duplicate/prefix conflicts, case/Unicode collisions, and a destination changed between preview and write. Unrelated working-tree files survive; unsafe manifests cause no application.
2. **Delivery interruption and repetition.** Disconnects, truncation, wrong hashes, client disk full, partial application and retry. TTL-only downloads never delete. With receipts: no deletion before durable acknowledgement, wrong digest rejected, lost ack response safely retried.
3. **Real consumer/authorization model.** Two tokens, two sessions sharing one token, departing joiner, revoked token, admin and worker. Both sessions retrieve; filenames never leak through public status/events. With receipts, concurrent final acknowledgements delete once.
4. **Lifecycle across both workers.** Real local and remote processes produce success outputs and failure diagnostics. Cancellation/timeout during collection, slow upload, heartbeat omission, ambiguous finish response and server restart never expose partial bundles or revive a lost job.
5. **Quota, retention and recovery.** Concurrent reservations, byte/file limits, sparse/oversized metadata inputs, disk-full/commit failures, orphan staging, unlink failure and an active download crossing expiry. Existing log/workspace retention remains independent.
6. **Compatibility and honest presentation.** Migration from schema 6; old jobs and absent worker artifact reports remain unknown rather than empty; v1 fields and meanings survive; process failure survives successful artifact retrieval; delivery failure survives process success; CLI/UI show null as `—`.

Then run the repository’s full required checks and an isolated end-to-end scenario. Merely proving that a new `artifacts` key exists would not establish compatibility or prevent the failures above.