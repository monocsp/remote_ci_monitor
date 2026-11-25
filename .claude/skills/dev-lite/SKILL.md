---
name: dev-lite
description: Build a small change to remote_ci_monitor without a workplan — a request card in the PR body, a red test first, the fix, `rcm/ci`, one isolated verifier (two when core server code changes), `verified:` and `tools/merge_pr.py`. Use it for "just fix this", "a quick change", when none of the five lane conditions applies and about five files or fewer under `src/` change. Not when the change touches the database, the wire, concurrency or restart, the web UI or security, or changes a frozen contract — that is `dev-loop`.
---

# Small change (dev-lite)

The full loop costs a workplan, review rounds, a live run and an edge audit. A wording fix, a
missing `—`, one flag's default, one wrong branch in one function do not earn that. This skill takes
them from request to merge with a test that was red and one isolated look — and moves them to the full
loop the moment they stop being small.

## 0. Is it small?

Both must hold; write the answer on the card.

1. None of the five lane conditions in [`workplan`](../workplan/SKILL.md) «Lane» applies. Go through
   that table row by row; do not copy it here.
2. About five files or fewer change under `src/` (provisional — never measured in rcm).

Also not lite: a change to a frozen contract (schema_version, DB version, wire, exit codes, CLI) or a
question the owner has not answered. Either one fails → say which, and go to
[`dev-loop`](../dev-loop/SKILL.md). When in doubt, the full loop.

Before asking the owner anything, look for the answer in `PLAN.md`, the workplans and the code. More
than one question, or a question about a screen, goes on a [decision page](../_shared/decision-page.md).

## 1. The request card

Kept as a working note, then pasted into the PR body under `## 요청 카드`, right after `## 한 줄`.
It lives **only in the PR body**: a commit would move the head and send `rcm/ci` round again.

- the owner's request and decisions, verbatim;
- 3–6 acceptance lines (Given / When / Then), each with an ID the tests carry, under a prefix of two
  or more capital letters unique to this PR (`XL1`, `XL2`… — `XL` is only an example). Short tags such
  as `L1` already exist in tests; take a prefix that
  `./.venv/bin/python tools/scenario_trace.py --prefix-usage <P>` reports unused;
- the files that will change, and the §0 answer;
- each item of the request: done · needs-decision · ⛔;
- after verification: verified head, verifier count, findings, `rcm/ci` job number.

## 2. Build

1. Branch with the `branch` skill; its own worktree and `.venv`.
2. A test per acceptance line first, named with its ID (`test_xl1_…`); run it and keep the failure
   it printed. It must fail in the assertion, not on import.
3. The fix. Changed tests, and on the changed files only `./.venv/bin/ruff check <files> &&
   ./.venv/bin/ruff format --check <files>`; `node --test` on a changed web file.
4. A fix to a defect gets a permanent mutant in `scripts/mutcheck.py`, proven with
   `./.venv/bin/python scripts/mutcheck.py --only <name>`. A plain addition (a new message, a flag)
   does not need one.
5. User-visible → the `docs` skill (`CHANGELOG.md` `[Unreleased]` and the owning document).
6. Commit with the `commit` skill. When output changed, run the command once from the worktree's
   `.venv` and paste what it printed under `## 검사`.

## 3. PR and verification

1. Push and open the PR with the `pr` skill, card included. Wait for `rcm/ci` on the head
   ([`dev-loop`](../dev-loop/SKILL.md) «long waits»).
2. Isolated verification on the green head, with the count, the HIGH retry and the MED/LOW handling
   of the `dev-lite` row in [`isolated-verifier`](../../agents/isolated-verifier.md) «Quorum». Each
   verifier gets, as absolute paths, the diff file and a snapshot of the head
   ([`_shared/stage-lead.md`](../_shared/stage-lead.md) «What a read-only agent gets»), the card and
   the claims to check (each acceptance line, plus "the test fails without the fix", with the
   `tools/redgreen.py` receipt made as that file says), and marks every finding with a severity.
3. A HIGH that is still there after the canon's one retry means it was not small: stop and tell the
   owner. MED/LOW go into the PR body under `## Remaining findings`.
4. Post `verify-round:` (`stage=P3`) and `verified: <head sha>` as the canon gives, merge with
   `python3 tools/merge_pr.py <N> --wait --delete-branch`, remove the worktree (`pr` skill).

## Switching to the full loop

Stop as soon as the work meets a lane condition (it needs a migration, a new key in `/api/status`, a
second process) or grows past about five `src/` files. Tell the owner which condition, then start
[`workplan`](../workplan/SKILL.md) with the card and the commits as material. The open PR becomes a
draft with `WIP:` and stays on its branch; no more code until the workplan is merged.

## Left out, compared with the full loop

The workplan and its decision table, plan review, P4 live run, P5 edge audit, P6 gap fix, review
rounds beyond the one retry, and MED/LOW triage.
