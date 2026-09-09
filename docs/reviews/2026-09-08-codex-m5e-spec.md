# Codex 교차 리뷰 — M5e 명세 (2026-09-08, 2차)

> 1차는 설계 초안에 대한 것이다(`2026-09-08-codex-m5e-plan.md`). 이건 그 리뷰와 오너 결정 넷을
> 반영해 쓴 `docs/m5e-workplan.md` 를 다시 건 것이다. 프롬프트는
> `docs/reviews/2026-09-08-codex-m5e-spec-prompt.md`.
>
> 여기 지적된 것 중 코드 사실 주장은 전부 확인했다 — `retention_days_*` 가 0 을 허용한다는 것
> (`config.py:594`), `ACTIVE_STATES` 에 `cancelling` 이 들어 있다는 것(`core/model.py:28`),
> 합류자 이탈이 종료 검사보다 **앞에** 있어 끝난 뒤에도 이탈된다는 것(`server.py:1304`),
> `core/manifest.py` 에 **조각 길이 제한이 없다**는 것, `_glob_to_regex` 가 앵커 없는 조각을
> 돌려주고 `search` 로 쓰인다는 것(`snapshot.py:113`), 새 DB 는 마이그레이션을 건너뛴다는 것
> (`store.py:294`), 취소 마감이 `kill_at + 2 × worker_heartbeat_seconds`(기본 5초)라는 것
> (`remote_workers.py:536`) — 전부 맞았다. 명세를 그에 맞춰 고쳤다.
>
> **한 가지는 받아들이지 않았다**: 결정 42(덮어쓰기)의 「오너 확인 대기」 표시가 낡았다는 지적은
> 내가 리뷰 프롬프트에 그걸 확정된 것처럼 적어서 나온 것이다. 오너는 아직 답하지 않았다 — 표시는
> 그대로 둔다.

**The spec resolves much of the earlier review, but it is not yet precise enough for test-first implementation.** The remaining problems include premature deletion, unsafe restoration edge cases, incomplete remote reporting, and recovery states that can leak storage.

I reviewed the repository read-only. I did not edit files or run tests. The four owner decisions are treated as settled.

1. **High — M3 cleanup can violate the promised 24-hour retention.**

   [Spec:229](../../docs/m5e-workplan.md#L229) explicitly adds `artifacts/<id>/` to ordinary job cleanup. However, `retention_days_success = 0` and `retention_days_failure = 0` are valid configurations: [config.py:594](../../src/remote_ci_monitor/config.py#L594). Zero means eligible at the next sweep: [core/retention.py:82](../../src/remote_ci_monitor/core/retention.py#L82).

   Consequently, a joined job can lose its bundle minutes after completion, without acknowledgement. This contradicts decision 40 even though the default M3 retention periods happen to be longer.

   Keep bundle deletion governed by its own acknowledgement/expiry eligibility. Likewise, metadata deletion must wait until **both** M3 resources and bundle files/reservations are gone. Merely adding deletion of `job_artifacts` rows to `delete_old_jobs` is insufficient: its current eligibility checks only `jobs.artifacts_purged_at`, at [store.py:475](../../src/remote_ci_monitor/store.py#L475). Otherwise failed bundle deletion can lose its ownership, accounting, and retry record.

2. **High — The restoration table permits restoring a locally deleted file and leaves existing files without a baseline undefined.**

   At [spec:291](../../docs/m5e-workplan.md#L291), every locally absent file is `new` and written. But if a file existed at submission and the user deleted it while waiting, that deletion is a local change. The specified classification restores it without `--force`.

   Two further cases have no defined classification:

   - An output path was excluded from the submitted snapshot, but a differing local file already exists.
   - Standalone `rcm artifacts … --fetch --output DIR` encounters a differing existing file; there is explicitly no submission baseline.

   Define classification using **baseline presence/content, current presence/content, and incoming content**. Missing baseline must not imply overwrite permission. Specify that local deletion is a conflict, and that standalone differing files require `--force`.

   The baseline must also account for every submission path. Full-tar submission rereads files after hashing ([client.py:345](../../src/remote_ci_monitor/client.py#L345), [client.py:367](../../src/remote_ci_monitor/client.py#L367)); cached submission sends the manifest and rereads missing blobs ([client.py:540](../../src/remote_ci_monitor/client.py#L540), [client.py:562](../../src/remote_ci_monitor/client.py#L562)); joining sends no tree at all ([cli.py:314](../../src/remote_ci_monitor/cli.py#L314)). “Actually sent representation” at spec:297 needs an explicit rule for each.

3. **High — The prescribed filesystem operations do not establish the promised race protection.**

   [Spec:118](../../docs/m5e-workplan.md#L118) prescribes `os.walk`, `lstat`, and `open(O_NOFOLLOW)` followed by `fstat`. `O_NOFOLLOW` protects the final path component; it does not prevent a checked parent directory from being replaced with a symlink before opening the file. A regular-file `fstat` does not reveal that escape.

   On the client, [spec:286](../../docs/m5e-workplan.md#L286) checks destinations once, then later replaces files. A user edit between comparison and replacement is overwritten without `--force`. The test list mentions this race, but the algorithm does not say what should happen when it occurs.

   Require anchored directory traversal and destination revalidation immediately before replacement, and define conflict/abort behavior. Also protect staging creation itself: the predictable `<dest>/.rcm-artifacts-<job_id>/` is used **before** destination validation. Existing symlinks, concurrent fetches, and different servers reusing the same job ID need defined handling.

4. **High — Remote workers cannot report most of the new artifact outcomes.**

   [Spec:181](../../docs/m5e-workplan.md#L181) adds only optional `bundle_sha256` to `finish`. That cannot communicate:

   - Successful empty collection.
   - Collection timeout or unsafe-path rejection.
   - Skipped execution.
   - Upload failure.
   - `skipped_count` or collection error details.

   The current finish handler reads execution outcome, exit code, and summary; it has no artifact report: [remote_workers.py:433](../../src/remote_ci_monitor/remote_workers.py#L433).

   Define an optional structured artifact disposition, its validation, and its relationship to an uploaded receipt. **Absent reporting from an older worker must remain distinguishable from a confirmed empty collection.** The earlier review explicitly required this distinction; the response lost the necessary protocol field.

5. **High — Moving collection before completion leaves cancellation semantics unresolved.**

   The proposed insertion points are correct, but they create a potentially long interval after process exit and before terminal commit. During that interval cancellation still changes the job to `cancelling`: [store.py:927](../../src/remote_ci_monitor/store.py#L927).

   Neither the current `outcome_for` nor `Store.finish` automatically makes an accepted cancellation win: [worker.py:135](../../src/remote_ci_monitor/worker.py#L135), [store.py:970](../../src/remote_ci_monitor/store.py#L970). Following the spec literally can accept cancellation during collection and then commit the previously computed `succeeded`.

   Remote cancellation has another constraint: the server closes an unconfirmed cancellation at `kill_at + 2 × heartbeat`, even while heartbeats continue ([remote_workers.py:535](../../src/remote_ci_monitor/remote_workers.py#L535)). The proposed 60-second collection window can outlast that deadline.

   Specify the transaction ordering for cancellation versus completion, whether collection stops on newly accepted cancellation, and how upload publication rechecks terminal state. “Best-effort” and “short deadline” are not sufficient expected results for tests.

6. **High — Acknowledgement replay, administrator acknowledgements, and partial application need a concrete state machine.**

   Three ambiguities can produce incompatible implementations:

   - **Replay:** [spec:178](../../docs/m5e-workplan.md#L178) says a non-`ready` ack returns 409. After successful early deletion, the state is `purged`, so a client that lost the successful response receives 409 on retry. Yet spec:353 requires safe ack retries. Preserve the bundle digest and acknowledgement tombstone, and specify the duplicate response explicitly.
   - **Administrator ack:** the algorithm first records `acked_at` ([spec:194](../../docs/m5e-workplan.md#L194)), while spec:207 says an administrator’s ack must not delete another person’s bundle. Recording that same deletion-eligible timestamp can cause a later sweep to delete it anyway. Define whether such an ack is a no-op, and separately define an admin acknowledging a job they submitted themselves.
   - **Conflicts/dry-run:** spec:301 says to ack after application, but does not say whether “finished applying all non-conflicting files” qualifies. A bundle with one unresolved conflict must not be deleted as fully applied. Dry-run must not acknowledge either.

   A crash after applying files but before ack should leave the server bundle until retry or TTL. The client needs a durable record identifying server, job, digest, destination, baseline, and application status, plus a documented recovery invocation. “Run again” at spec:300 is ambiguous: repeating `rcm run` after completion submits a **new job**, not a recovery request.

7. **High — Recovery does not cover installed or abandoned bundles, and can retain storage indefinitely.**

   Local publication renames files before the DB commit ([spec:135](../../docs/m5e-workplan.md#L135)). A crash between those operations leaves an installed `artifacts/<id>/` directory without a committed `ready` record. Startup reconciliation at [spec:257](../../docs/m5e-workplan.md#L257) only mentions orphan **staging**, interrupted rows, and missing ready files.

   Similarly, a verified remote upload whose worker never finishes can retain a reservation without any specified `expires_at`. The stated expiry sweep cannot collect a row with no expiry. Changing its state to `failed` does not delete its files.

   Define cleanup and reservation release for every interrupted/publication state, including orphan final directories and terminal jobs with staged uploads. Preserve records when deletion fails.

   Remote restart recovery also needs an explicit branch: current server recovery deliberately preserves remote active jobs ([store.py:998](../../src/remote_ci_monitor/store.py#L998)). If startup discards their uploaded receipt, specify what a still-running worker learns from the recovery GET and whether it may retry. That GET must work after terminal completion; reusing `_owned_active` would reject precisely the ambiguous-finish query it exists to answer ([remote_workers.py:364](../../src/remote_ci_monitor/remote_workers.py#L364)).

8. **High — The archive format and admission accounting are not specified sufficiently to enforce the limits.**

   [Spec:69](../../docs/m5e-workplan.md#L69) gives a schematic manifest, but the upload route does not define where that manifest travels, the accepted tar dialect/metadata, or canonical archive construction.

   In particular, `max_artifact_bytes` limits **original file bytes**, while [spec:221](../../docs/m5e-workplan.md#L221) proposes admission from `Content-Length`. Tar headers, padding, and extended metadata mean those are different quantities. Comparing the content length directly to the raw-file limit rejects valid boundary-sized bundles; not bounding archive overhead permits excessive storage consumption.

   Define raw-byte, archive-byte, metadata, and staging accounting separately, including reservation conversion at publication. Specify regular-file-only archive validation, sparse entries, duplicate/unlisted members, prefix conflicts, size/hash mismatches, and deterministic retries.

   Also, unlinking an archive with an active download does not immediately release its storage. The spec recognizes this at line 188, but needs accounting until the last reader closes—not merely until unlink succeeds.

9. **High — Public `reason_args` contradicts the protected-path rule.**

   [Spec:95](../../docs/m5e-workplan.md#L95) defines `path_conflict({path})` and `unsafe_path({path, kind})`. Section 10 then includes `reason_args` in every public job artifact object while explicitly prohibiting public paths ([spec:264](../../docs/m5e-workplan.md#L264)).

   A rejected filename would therefore expose the tree structure through status, even when the manifest endpoint is protected. Specify separate public arguments and protected diagnostic details; test serialization of rejection cases, not just successful manifests.

10. **Medium — Migration 7 is plausible, but incomplete for this repository’s migration machinery.**

    `_MIGRATIONS` is a mapping from target versions to **tuples of individual SQL statements** ([store.py:173](../../src/remote_ci_monitor/store.py#L173)). The proposed SQL fits that convention when split accordingly.

    However, new databases **skip migrations** and execute `_SCHEMA_V1` directly before setting the latest version ([store.py:294](../../src/remote_ci_monitor/store.py#L294)). The spec must require updating `DB_VERSION`, `_SCHEMA_V1`, and `_MIGRATIONS[7]`, with fresh-database and reopen tests—not only 6→7 migration.

    Explicitly include pre-M5e **active** jobs in the legacy rule. `DEFAULT 0` cannot reconstruct historical joins, especially requester self-resubmissions or departed joiners. Keeping those jobs artifact-`unknown` and ineligible for new collection is consistent with the document; enabling collection for them would require conservative treatment of their unknown join history.

**The `join_count` premise is valid for production submissions, with important qualifications.**

The actual production path is `join_or_bump`, not the cited `find_joinable`. It selects an active job under `BEGIN IMMEDIATE`; terminal completion uses the same transaction serialization ([store.py:606](../../src/remote_ci_monitor/store.py#L606), [store.py:968](../../src/remote_ci_monitor/store.py#L968)). Therefore an increment inside that transaction freezes at **terminal commit**, not at process exit.

| Case | Verified behavior and required test |
|---|---|
| Active states | Includes `uploading`, `queued`, `running`, **and `cancelling`**: [core/model.py:25](../../src/remote_ci_monitor/core/model.py#L25). Test joining during collection and cancellation. |
| Requester resubmission | No joiner row is inserted; increment must sit outside that conditional: [store.py:619](../../src/remote_ci_monitor/store.py#L619). |
| Repeated submission by the same joiner | `INSERT OR IGNORE` collapses the row. Every successful joined submission must still increment the counter, regardless of insertion rowcount. |
| `--no-join` | Bypasses joining **for that request**. Its newly created job still receives a `join_key`, so a later normal submission can join it: [server.py:838](../../src/remote_ci_monitor/server.py#L838), [server.py:858](../../src/remote_ci_monitor/server.py#L858). It is not a permanent “unjoinable job” flag. |
| `join_duplicates = false` | Bypasses joining for both tree and git-ref submissions. Each new job starts independently: [server.py:1179](../../src/remote_ci_monitor/server.py#L1179). |
| Departing joiner | Removes ownership, including after completion, because the leave branch precedes the terminal check: [server.py:1304](../../src/remote_ci_monitor/server.py#L1304). Never decrement historical `join_count`; retain until TTL. |
| Resubmit after completion | Cannot join that terminal job. Creates another job, or joins another matching active job. No output cache is implied. |
| Direct `Store.add_joiner` | Has **no state check**: [store.py:782](../../src/remote_ci_monitor/store.py#L782). Current callers outside its definition are tests, so this is not a production terminal-join bug. Nevertheless, specify its relationship to the new invariant so fixtures cannot manufacture a joiner with `join_count == 0`. |

The counter itself does not create indefinite retention: departures, repeated submissions, or missing acknowledgements all remain bounded by TTL. The indefinite-retention risks are the missing cleanup/accounting transitions described above.

**Other factual checks and corrections:**

| Claim | Assessment |
|---|---|
| Local insertion between `run_job` and `store.finish` | Correct. `worker.py:346` starts the call; it returns before line 353. Completion is line 368 and cleanup line 381. |
| Remote insertion after final flush, before `_finish` | Correct for normal completion: [remote_worker.py:439](../../src/remote_ci_monitor/remote_worker.py#L439). Error exits at lines 432–438 also need artifact disposition handling. |
| `_finish` removes heartbeat membership before reporting | Correct. Line 455 is the definition; the removal is line 459 and reporting line 463. |
| Successful workspaces “are deleted” | More precisely, cleanup is **attempted**: both paths use `ignore_errors=True`. Do not infer reclaimed bytes from that operation. |
| Reads via `authenticate_read`, ack Bearer-only | Correct: [server.py:447](../../src/remote_ci_monitor/server.py#L447). Protected reads must require a token even with `read_auth=none`; `_read_only_ok` alone does not. Explicit worker rejection is necessary because `can_read_log` checks ownership/admin, not token kind. |
| Existing materializers delete their destination | Correct. Exact deletion lines are `materialize.py:47` and **87**; line 80 is the second function’s definition. |
| Reuse manifest’s “255-character components” | **Incorrect.** [core/manifest.py:68](../../src/remote_ci_monitor/core/manifest.py#L68) enforces total Python-string length 4096, but no component-length limit. Specify the new component limit and whether lengths mean characters or encoded bytes. |
| APFS does not distinguish case | Overbroad. Treat NFC/casefold rejection as a deliberate portability policy, not a universal property of APFS. |
| `_publish_job` would repeat `job_finished` | Correct. The new artifact event is appropriate, but its publication must also invalidate the cached status: [server.py:367](../../src/remote_ci_monitor/server.py#L367). |
| Expiry is physically delayed “at most one hour” | Only a default-operation expectation. The sweep interval is configurable, and deletion errors can extend it. State the configured interval and error qualification. |
| Decisions 39–42 still need adding to PLAN | Already present at [PLAN.md:591](../../PLAN.md#L591). The “owner confirmation pending” label for `--force` is stale under your supplied decisions. |

**Additional places where independent test authors would reasonably disagree:**

| Location | Missing contract |
|---|---|
| [Spec:113](../../docs/m5e-workplan.md#L113) | `_glob_to_regex` returns an unanchored fragment. Define root-relative matching, malformed character classes, wildcard access to `.git`, and whether skipped counts include unmatched entries. |
| [Spec:123](../../docs/m5e-workplan.md#L123) | Collision checks must include normalized **directory prefixes**, not just complete file paths—for example, file `A` versus `a/b.png`. Define collisions against existing destination entries too. |
| [Spec:175](../../docs/m5e-workplan.md#L175) | Complete response schemas/status mappings are absent for `disabled`, `empty`, `failed`, `unknown`, `unavailable`, malformed upload/ack, and slot exhaustion. |
| [Spec:185](../../docs/m5e-workplan.md#L185) | Transfer-slot acquisition must not block while holding all general request slots. The current handler acquires a general slot before routing: [server.py:1502](../../src/remote_ci_monitor/server.py#L1502). Define rejection versus waiting and transfer deadlines. |
| [Spec:214](../../docs/m5e-workplan.md#L214) | What starts TTL: collection, receipt, publication, or terminal commit? What happens to GET and ack exactly at expiry, before physical sweep? |
| [Spec:218](../../docs/m5e-workplan.md#L218) | Does 60 seconds cover collection alone, upload, retries, and commit? Define the shorter cancelled/timed-out budget and configuration validation. |
| [Spec:255](../../docs/m5e-workplan.md#L255) | Which settings are frozen per job? Global admission capacity and transfer concurrency should not accidentally become historical per-job limits. |
| [Spec:298](../../docs/m5e-workplan.md#L298) | Define journal persistence, recovery command, mode handling, staging cleanup, and whether dry-run may create staging. |
| [Spec:321](../../docs/m5e-workplan.md#L321) | Choose the actual artifact-delivery exit code, JSON fields, and precedence when execution and delivery both fail. Existing output includes `wait_exit_code`: [cli.py:446](../../src/remote_ci_monitor/cli.py#L446). |
| [Spec:353](../../docs/m5e-workplan.md#L353), spec:373 | “Never delete before ack” contradicts TTL deletion. Explicitly say “unless TTL expires,” including interrupted-download tests. |

There is also a timing compatibility question: local completion currently stores `result.finished`, whereas remote completion stores server receipt time ([worker.py:371](../../src/remote_ci_monitor/worker.py#L371), [remote_workers.py:448](../../src/remote_ci_monitor/remote_workers.py#L448)). Adding collection/upload makes that difference substantial; existing `job_seconds` derives from those timestamps ([core/status.py:185](../../src/remote_ci_monitor/core/status.py#L185)). Specify timing without silently redefining v1 fields.

The earlier review’s destructive-extraction, trusted-job boundary, failed-job collection, separate outcomes, and same-PR documentation concerns are substantially resolved. The documentation checklist should additionally name CHANGELOG, `examples/server.toml`, and its matching packaged template, as required by [docs/documentation.md:75](../../docs/documentation.md#L75). I found no proposed runtime dependency or language-policy violation.

**Nothing explicitly excluded by §17 is inherently required.** Full-download retry does not require HTTP range support; an application journal does not require resumable downloads; deterministic replay of one bundle does not require multiple versions or caching. Those recovery mechanisms are nevertheless load-bearing and must remain explicitly in scope. Late uploads to `lost` jobs, browser restoration, arbitrary mappings, and deletion of local files absent from the manifest can all stay excluded.