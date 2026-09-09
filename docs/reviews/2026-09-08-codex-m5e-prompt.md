You are reviewing a **design plan**, before it becomes a written spec and before any code exists.
Give an adversarial design critique. Do not write code. Do not edit files. You have read-only access
to the repository you are in — **read the actual source before you agree or disagree with anything
below**, and say explicitly where the plan's claims about the code are wrong.

## What the product is

`rcm` (remote_ci_monitor) is a local job server for a small team that shares one build machine.
A developer runs `rcm run gate` from a laptop; the client snapshots the working tree, uploads it,
the server queues the job, a worker materializes the tree into a workspace and runs a **preset**
(a fixed `argv` declared in server config — no shell, inputs only through `RCM_INPUT_<NAME>` env).
Logs stream back. There is a web UI and a CLI.

Hard house rules, already paid for, that a proposal must not break:

- **Zero runtime dependencies.** Python standard library only. `http.server.ThreadingHTTPServer`,
  `sqlite3`, `tarfile`, `hashlib`. No FastAPI, no requests, no aiofiles.
- **API schema is v1 and additive only.** You may add keys; removing one or changing its meaning
  stops the work and goes to the owner.
- **DB schema may be migrated** (currently 6, `store.py` `_MIGRATIONS`).
- **Honesty rules**: an unknown value prints as an em dash, never a zero; failure is never dressed
  up as success; a job that was `lost` on server restart is never silently retried.
- Writes need a token. Reads of a job's log are restricted to that job's requester token, its
  joiners, and admin tokens; a worker token cannot read them.
- Comments and docstrings are Korean; identifiers, CLI help and docs are English.

Files worth reading before you answer (rough sizes): `src/remote_ci_monitor/server.py` (2000 lines,
routes are listed in its module docstring), `worker.py` (local worker, workspace lifecycle around
line 280-385), `remote_worker.py` + `remote_workers.py` (a worker on another machine, claims over
HTTP and streams logs back), `runner.py` (shared process execution), `store.py` (SQLite),
`client.py` (the laptop side: snapshot, upload, follow), `core/snapshot.py` (tree snapshot and
ignore rules), `core/retention.py` + `janitor.py` (what gets deleted and when), `core/model.py`
(`Preset`, `Job`, `Joiner`), `materialize.py` (untar / assemble a workspace safely),
`config.py` (server config and presets), `PLAN.md` (the canonical plan; see its decision table),
`AGENTS.md`, and `docs/m5c-workplan.md` / `docs/m5d-workplan.md` for what a spec looks like here.

## The concrete problem this milestone (M5e) solves

The owner runs Flutter golden tests remotely: `flutter test --update-goldens` regenerates ~64 PNGs
**in the workspace on the build machine**. Today there is no way to get them back. A successful
job's workspace is deleted by the worker (`keep_workspace_on_failure` only keeps failed ones), and
the only thing a session can retrieve from a job is the log.

The owner's words: *"I want the other session that called it to pull the artifacts right away, and
once the pull is confirmed, delete them right away to keep things lean."*

## The plan under review

(The original is Korean, at `PROMPT-m5e.md` in the repo root, uncommitted. Translated below in full.)

**Design judgment: implementing "delete on pull" literally will lose files.** The direction is
right — storage does not pile up, ownership is clear — but seven things must be decided together
or files vanish silently. The spec must answer each one and lock the answer with a test.

1. **Interrupted download?** Deleting on "download started" is wrong. Two phases: download, then
   the client verifies size and hash and sends an **explicit ack**; only then does the server
   delete. If the client dies before the ack, the files stay. Fail toward keeping, not losing.
2. **Joined jobs?** Two sessions that submitted the same tree share one job. If the requester's
   pull deletes, the joiner gets nothing. Is it "until every eligible session has pulled" or "the
   first pull wins"? **Recommendation: delete when all eligible parties (requester + joiners) have
   acked, or when the TTL expires.**
3. **Nobody pulls?** Someone submits with `--no-wait` and closes the laptop. If the only delete
   condition is "pulled", the files stay forever. **A TTL is mandatory** (e.g.
   `artifact_retention_hours`, default 24), swept by the existing retention janitor.
4. **Who may pull?** Same rule as logs — that job's token, its joiners, admin tokens. Not worker
   tokens.
5. **What counts as an artifact?** If the job script may name any path, it can exfiltrate files
   from the build machine. **The preset declares allowed globs** (e.g.
   `artifacts = ["test/**/goldens/*.png"]`). The worker collects only matching files **before** it
   deletes the workspace. Paths outside the workspace, symlinks and absolute paths are refused —
   reuse the rules already used when unpacking an upload.
6. **Size limits.** 64 PNGs are small; build outputs reach gigabytes. Per-job (`max_artifact_bytes`)
   and server-wide caps. On overflow **do not fail the job** — drop the artifacts and record that
   fact in the result (one more summary code in the decision-37 table).
7. **Remote workers.** That job's workspace lives on the worker machine. The worker must upload to
   the server, the way it already streams logs. Both the local-worker path and the remote-worker
   path must work, or `pool` jobs silently come back empty.

**Client-side cautions.** Writing received files into the session's working tree means a remote
machine is changing local files: reuse the snapshot-unpack safety rules (no absolute paths, no
`..`, no symlinks, nothing outside the current directory). Default to showing what will be written
before writing it — do not silently overwrite 64 goldens; require an explicit
`rcm run … --fetch-artifacts`, or at minimum print the file count and bytes first. Count how many
differ from what is already on disk ("12 of 64 changed") — for a golden update that count *is* the
result.

**Order of work.** Spec first (`docs/m5e-workplan.md`, answering the seven questions, nailing down
routes, config keys, DB columns, status-document keys, CLI flags in tables, and how to split the
PRs — suggested: server collect+store → client fetch+write → UI+docs). Then this Codex review.
Then tests written by an isolated agent that may not touch `src/`, locking: no delete before ack ·
joiner rule · TTL · refusal of files outside the globs · size caps · the remote-worker path ·
schema keys added only. Then implementation, then full checks, then an isolated end-to-end run,
then a PR to `dev`, then docs.

**Done means.** (1) Running a golden-making preset writes the files back into the submitting
session's tree at the same paths, and the screen says how many changed. (2) They disappear from the
server only after the ack; an interrupted pull leaves them and can be retried. (3) Two joined
sessions can both pull. (4) If nobody pulls, the TTL sweeper deletes them. (5) Files outside the
globs, outside the workspace, or symlinked are never collected in the first place. (6) Over the
cap, the job still succeeds or fails on its own merits and the result records that artifacts were
dropped. (7) The same thing works on a remote worker pool. (8) All existing tests green, API schema
still v1, still zero runtime dependencies.

**Four questions the plan wants to put to the owner.** Wait for every joiner to ack or just one?
Fetch by default or only behind `--fetch-artifacts`? Allow overwriting artifacts (goldens) by
default, or only new files unless a flag is passed? What TTL default (24h proposed) and what
server-wide cap?

## What I want from you

Rank everything by how much it would cost to get wrong. Be concrete and cite files/lines.

1. **Where is the plan factually wrong about this codebase?** Verify the claims: that successful
   workspaces are deleted, that only logs come back, that remote workers stream logs to the server,
   that `materialize.py`/upload unpacking has reusable safety rules, that the retention janitor
   could carry a TTL, that the joiner/eligibility model is what the plan assumes.
2. **What does the plan miss?** Failure modes, races (worker deleting the workspace while a fetch
   is in flight; job cancelled or timed out mid-collection; server restart between collect and ack;
   two joiners acking concurrently; disk full), security holes (a preset glob like `**` or `../`,
   symlink-to-directory, a hardlink, a huge file count, path length, case-insensitive collisions on
   macOS, zip-bomb-shaped output), and operational holes (what shows in the web UI and status doc).
3. **Where is the plan's judgment wrong?** In particular: is the two-phase ack worth its
   complexity versus a simpler rule (e.g. delete only by TTL, or reference-count by eligible
   party)? Should artifacts be collected on *failed* jobs too? Should a failed golden run return
   the failure diff images? Is per-preset glob declaration the right boundary, or should the job
   script write to a declared output directory (`RCM_ARTIFACT_DIR`) that the worker collects
   wholesale — which is simpler and closes the exfiltration hole differently?
4. **Concrete design, in this codebase's idiom.** Name the routes (verb + path + auth + status
   codes), the new config keys, the DB columns/tables and the migration, the status-document and
   job-JSON keys (additive to v1), the CLI flags, and where the collect step must sit in
   `worker.py` / `remote_worker.py` so it happens before workspace deletion and before the job is
   marked terminal. Say what you would deliberately **not** build in M5e.
5. **Which of the four owner questions are real owner decisions**, and which can be answered from
   the repo's existing conventions? Answer the latter yourself, with the convention you are citing.
6. **How to split the PRs**, and the five or six tests you would insist on before implementation.

Write the review in English, as Markdown, with a short "biggest risks first" section at the top.
