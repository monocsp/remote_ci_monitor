---
name: workplan
description: P1 of the development loop for remote_ci_monitor — a plan-lead writes `docs/<code>-workplan.md` in the house section structure with a scenario table carrying a `scenario-prefix`, isolated verifiers review it in rounds, the owner answers one decision page, and merging the spec PR approves the plan and freezes its contracts. Use it before any code for a milestone or a feature that meets one of the five lane conditions, and to resume a plan waiting for approval. Not for a change small enough for `dev-lite`, and not for implementing an approved plan (`build-pr`).
---

# Workplan (P1)

A workplan is written before code so that whoever builds, reviews and runs it works from the same
sentences. It is a document in `docs/`; the merge of its own PR approves it and freezes its
contracts. Nothing in this stage writes code.

The main session does not write the plan. [`plan-lead`](../../agents/plan-lead.md) gathers its
material (handoff format and lead rules: [`_shared/stage-lead.md`](../_shared/stage-lead.md), stage
name `P1`); [`korean-writer`](../../agents/korean-writer.md) writes the Korean text from it, because a
long Korean document is its job (`korean-writing` §5 rule 6) and a lead cannot spawn it.

## Order (the only statement of it; `plan-lead` and `dev-loop` link here)

1. §0 below; the branch (`branch` skill, `docs/<code>-<what-it-does>-workplan`) and its worktree.
2. The `scenario-author` table (§2), the in file, then `plan-lead`, which returns
   `P1-<n>-material.md` (the section skeleton with facts, decisions, tables, rows) and its out.json.
3. `korean-writer` writes `docs/<code>-workplan.md` from the material and the in file's verbatim owner
   words: tables copied as they are, no fact the material lacks (it answers 「재료에 없음」 instead).
   The main session reads it against `korean-writing` §6 and runs `tools/scenario_trace.py` on it.
4. The main session commits it (`commit` skill), pushes, and opens the spec PR as a draft (`--draft`,
   `pr` skill; the title stays a plain header); `rcm/ci` runs on it like any other.
5. Plan rounds (§4) on that PR's head, each leaving its `verify-round:` comment on it. A HIGH goes to a
   new `plan-lead` (corrected material for the sections it touches) and `korean-writer` (only those
   sections), then a commit, `rcm/ci` and the next round.
6. The decision page (§5) once a round has no HIGH; the answers folded into §11 and wherever they
   change §4–§7; then the last round, on the diff since the last verified head; then the merge (§6).

## 0. Before writing — check what is already decided

```sh
git fetch origin
git log origin/dev --oneline -30
git show origin/dev:CHANGELOG.md | sed -n '/^## \[Unreleased\]/,/^## \[/p'
gh pr list --state open --json number,title,headRefName
```

Read `PLAN.md` 「마일스톤과 완료 기준」 and 「결정 항목」, the newest workplans and the migrations in
`store.py`. A sibling session may already have shipped the opposite answer to the same request
(rcm, 2026-09: two sessions designed against one incident and one had already merged). Take the next
milestone code and the next free decision number from the newest workplan's §11.

The in-file carries the owner's words **verbatim** — a summary loses the clue that decides the
design. A request written by another project's session is quoted only as far as it is free of names,
paths and identifiers of that project (this repository is public); the rest stays in a file outside
the repository and the workplan says so.

## 1. Sections

`docs/<code>-workplan.md` is Korean, in the house sections below, in this order; `docs/m5s-workplan.md`
is the model. `plan-lead`'s material file has the same skeleton.

| section | what goes in |
|---|---|
| header quote | source, the owner's words verbatim, the first decision number, which edition this is |
| §0 한 줄 | one paragraph a stranger can act on |
| §1 출처와 오너 결정 | each wanted thing: what the request says, what this plan decides; decisions already confirmed |
| §2 범위 | who does what — rcm, a downstream project, the owner |
| §3 실측과 코드 제약 | numbered facts with `file:line` at a pinned HEAD sha, measured values with how they were measured, and what each fact does to the design |
| §4 설계 | the design, one subsection per feature, naming the decisions it rests on |
| §5 작업 분할과 병렬 | PR table: PR, branch, content, start condition, wave, runs with, shared files and merge order, **lane** |
| §6 진행 체크리스트 | `#`, task, PR, depends, state — the loop reads this table |
| §7 시험 계획 | the scenario table (below) and the `scripts/mutcheck.py` mutants to add |
| §8 개인정보 점검 | when the milestone ends in a release to `main` |
| §9 위험 | risk → how it is stopped |
| §10 (when asked) | what goes back to a requester's document, from which section, when |
| §11 결정 표 | `#`, item, decided, 오너 확인 (확정 with date · 추천 · ⛔), differs from the request |
| §12 격리 반박에서 바뀐 것 | rebuttal id, grade, first edition, this edition, decision |
| §13 구현하며 바뀐 것 (Changed while implementing) | empty at approval; filled per PR as `docs/m5q-workplan.md` §12 does |

Facts in §3 are measured, never estimated. A number that was not measured is `—` with what would
measure it. A production number comes only from the main session, with the owner's OK, by the
read-only route of [`_shared/test-server.md`](../_shared/test-server.md); `plan-lead` gets it through
its in file and never reads production itself.

After the merge the plan is frozen: a departure goes in §13 only, as [`_shared/workplan-diff.md`](../_shared/workplan-diff.md)
says, never into §4–§7 — except §6's state column, which the main session ticks as PRs merge. Only the
main session writes §13, in the PR that departs; a `build-lead` never edits the workplan.

## 2. Scenario table

§7 holds the scenario table, with the marker on the line directly above it and one prefix for the
whole workplan:

```
<!-- scenario-prefix: XS -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| XS1 | … | an observable result, not "works" | 단위 · e2e · 브라우저 · (live) | A |
```

1. **Prefix.** Existing tests already carry short tags (`test_s1_…`, `test_a10_…`, `# P2`), and a
   workplan whose prefix matches them turns the repository's trace test red. Pick two or more
   capital letters and check each candidate first; take one that reports zero usage:
   `cd <abs worktree> && ./.venv/bin/python tools/scenario_trace.py --prefix-usage <P>`.
2. **Scenarios before any design is read as code.** The main session spawns a
   [`scenario-author`](../../agents/scenario-author.md) (by `subagent_type`) with the owner's words,
   their sources, the prefix, the axes to cover and a snapshot of the base commit
   ([`_shared/stage-lead.md`](../_shared/stage-lead.md) «What a read-only agent gets»). Its table goes
   into the P1 in file; `plan-lead` builds §7 from it, and a row it drops is listed in §12 with why.
3. **Rows.** The ID is the first column. `(live)` marks a row that only a live run proves
   ([`live-verify`](../live-verify/SKILL.md)). The format, test tagging and the tool are in
   [`_shared/scenario-trace.md`](../_shared/scenario-trace.md). Before review, check the table alone:
   `cd <abs worktree> && ./.venv/bin/python tools/scenario_trace.py docs/<code>-workplan.md`.

Edge rows are chosen by reach condition, and irreversible results always get a row —
[`_shared/edge-scope.md`](../_shared/edge-scope.md) decides which. A table of every conceivable edge
is not thoroughness: it buries the few rows that matter.

## 3. Lane

This section is the only definition of the lanes; other skills link here. For every PR in §5,
`plan-lead` recommends one:

- **full** — P2 build → P3 review → P4 live run (when [`live-verify`](../live-verify/SKILL.md) says it
  is required) → P5 edge audit → P6 gap fix → triage → P7 merge;
- **short** — P2 build → P3 review → triage → P7 merge; no live run, no edge audit, no gap fix.

Recommend full when any of these applies to that PR:

| condition | what the full lane catches that short misses |
|---|---|
| DB schema or migration (`DB_VERSION` moves) | a build that migrates a database it should not open, data loss on upgrade (`AGENTS.md`: a worktree build sent a v7 database to v24) |
| wire format: `/api/status`, job JSON, worker protocol, health feature keys | the other side of the wire left on the old shape (M5d-0 added host keys and the remote parser silently dropped every new worker's samples, #48) |
| concurrency, restart or signals: claim, heartbeat, janitor, `lost`, SIGTERM/KILL | races and recovery paths no unit test orders by itself (M5f: without a lock a cooldown let 20 of 20 bursts through) |
| web UI | layout and wording only a browser shows (M5d: the phone test had been running at 500 px, #52) |
| security: tokens, auth, tar or path handling, guard hooks, anything shown to an unauthenticated reader | the attack path, which only an edge audit reads for |

None applies and at most about five files under `src/` change → recommend short. The five-file
figure is provisional: it was never measured in rcm. When in doubt, recommend full. The lane is the
owner's answer, recorded as a §11 row; the plan never picks it alone.

## 4. Plan review

Two [`isolated-verifier`](../../agents/isolated-verifier.md)s per round, one after the other. The
main session prepares a `git archive` snapshot of the pinned sha with the workplan in it
([`_shared/stage-lead.md`](../_shared/stage-lead.md) «What a read-only agent gets»); each verifier gets
that absolute path — not the chat, not the in-file, not the other's verdict. Ask each to check:

- every `file:line` in §3 against the code, and every new identifier (state, error code, config key,
  CLI flag) with `grep`, so a name already used elsewhere is caught (M5s §3 #7: `expired` already
  meant something in two other places);
- §4 against `PLAN.md` and earlier decision numbers — a silent reversal is a defect;
- each §7 row: observable, placed in the right PR, reachable by the stated test kind;
- §5: overlapping files without a merge order, a wave that depends on an unmerged PR;
- the owner's verbatim request: every item covered, none quietly narrowed.

Severity, rounds, escalation and the PR comment records are the canon's. Fix HIGH between rounds as
«Order» 5 says. Each finding and what changed goes into §12; MED/LOW not fixed are listed there too.

## 5. Ask the owner — one page

All open questions go on one [decision page](../_shared/decision-page.md): each with its options, the
result of each, one default. The lane question is always on it, even when nothing else is open.
Answers go into §11 verbatim with their decision numbers; an answer that differs from the default is
marked. Before asking, look for an answer that already exists in `PLAN.md`, a merged workplan or the
code — asking what is already decided is not neutral, it costs the owner's time.

No answer is not approval. A ⛔ row without an answer blocks the merge. 추천 rows are approved as
written by the merge, so a row the owner should look at is ⛔, not 추천.

## 6. Merge = approval

Once the owner has said to proceed and «Order» 6's last round passed: mark the draft ready
(`gh pr ready <N>`), post `verified: <head sha>` for that head, and merge with
`python3 tools/merge_pr.py <N> --wait`.

From the merge, these are frozen: `schema_version` of `/api/status`, the DB version and its columns,
the wire (request and response bodies, worker protocol, health feature keys), exit codes, and the CLI
(commands, flags, documented output). Changing one later is a new spec PR the owner approves; a
`build-lead` that needs it stops with a `POLICY` question.

## 7. Meta loop

A plan round that finds the same kind of mistake as a previous plan (a stale `file:line`, an unchecked
state set, a missing irreversible edge) adds the check to §4 or to the verifier's prompt.
