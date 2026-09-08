Review a written specification before implementation starts. Read the repository (read-only) and
verify it against the actual code. Do not write code, do not edit files.

**The spec: `docs/m5e-workplan.md`** (Korean). Its background review is
`docs/reviews/2026-09-08-codex-m5e-plan.md` — you wrote that one; this spec is the response to it,
plus four owner decisions taken since. Do not simply repeat that review; check whether the spec
actually resolved what it raised, and find what is new or newly wrong.

Context: `rcm` is a local job server (stdlib only, no runtime dependencies, API schema v1 additive
only, SQLite schema 6). M5e returns files a job produced (Flutter goldens) to the session that
submitted it. Key files: `server.py`, `worker.py`, `remote_worker.py`, `remote_workers.py`,
`store.py`, `client.py`, `cli.py`, `janitor.py`, `core/retention.py`, `core/manifest.py`,
`core/snapshot.py`, `materialize.py`, `config.py`, `PLAN.md`, `CONTRIBUTING.md`.

The owner's decisions, which are settled and not up for debate — review the *design that implements
them*, not the decisions themselves:
- TTL 24 hours.
- Delete on acknowledgement **only when `jobs.join_count == 0`**; a job that anyone joined keeps its
  bundle until the TTL. `join_count` is a new column incremented inside `join_or_bump`, including
  the requester's own re-submission.
- Limits are all config keys: 1 GiB per job, 10 GiB server-wide, 10000 files, 60s collection.
- Fetching is opt-in (`--fetch-artifacts`); files the user edited after submission are never
  overwritten without `--force`.

What I need, ranked by cost of being wrong:

1. **Factual errors.** Every file/line reference and every claim about current behavior. Especially:
   does joining really only happen in active states, so `join_count` is frozen once a job is
   terminal? Is the described insertion point in `worker.py` and `remote_worker.py` correct? Is the
   authentication split (reads via `authenticate_read`, ack Bearer-only) right? Does the migration
   shape match `_MIGRATIONS`?
2. **Holes in the `join_count` rule.** Where can it delete a bundle someone still needed, or keep
   one forever? Consider `--no-join`, `join_duplicates = false`, a joiner that leaves, a job
   re-submitted after it finished (new job, not a join), admin acks, replayed acks, and a client
   that crashes between applying files and sending the ack.
3. **Anything under-specified for a test-first agent.** The agent writes tests from this document
   without touching `src/`. Name the places where two readings of the spec would produce different
   tests.
4. **Contradictions with existing behavior or with the repo's rules** (v1 additive, honesty rules,
   docs in the same PR, Korean comments/English identifiers, zero dependencies).
5. **Anything in §17 "not building this time" that is actually load-bearing** — i.e. the spec
   cannot be correct without it.

Answer in English, Markdown, biggest risks first. Be specific and cite file:line.
