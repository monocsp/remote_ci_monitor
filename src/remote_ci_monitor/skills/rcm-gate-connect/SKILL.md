---
name: rcm-gate-connect
description: Connect a project's existing CI gate script to rcm so the Store tab and the queue show a step-by-step, evidence-based progress bar. Adds `::rcm::step::` markers to the gate (or wraps it when it must not change), finds silent stretches of five minutes or more and gives them a `::rcm::progress::` line with a real denominator, declares the `gate` preset and the `gate = "<preset>"` profile line, and proves it with a dry run and `rcm check`. Optional tier — use it after `rcm-store-connect`, whenever the gate card says "not configured" or the gate runs as one hatched bar with no steps.
---

# rcm-gate-connect

The gate is the project's own CI script. rcm does not run tests; it runs **your** script on the
release ref and draws what the script says on stdout. A gate with no markers is one grey bar
labelled "by time"; a gate with markers is a list of named steps, each with its own clock, and
a failure list that names what broke. This skill gets a project from the first to the second
without inventing anything: every number the page shows must come from something the script
actually counted.

The marker grammar rcm parses today (`core/progress.py`, contract §3):

```
::rcm::steps::N                                       # total — ONLY when computed at run time
::rcm::step::<name>                                   # a step begins; the previous one ends
::rcm::step-end::ok|fail                              # optional explicit end
::rcm::fail::<name>                                   # names what broke (a step, a test, a file)
::rcm::summary::<one line>                            # the last one wins; shown in the queue
::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]   # inside a long step (Store tab)
```

Rules that follow from the parser, not from taste: markers start at column 0; a step name is
one line, 120 characters max, control characters stripped; `::rcm::fail::` colours a step red
**only when the name equals a step name** — a child called `web` under a step called `build web`
leaves the failure list populated but `failed_step` empty, so print the step's name. A failed
step is one the script *declared*; rcm never guesses one from the exit code.

## When to use

- `rcm-store-connect` is done and the Store tab's gate card reads `not configured —
  profile.presets.gate is empty`.
- The gate already runs under rcm but shows as one bar with no steps, or its bar sits at the
  same place for 20 minutes because one child process writes to a file.
- The gate's exit code is red but the queue says `failed (no step named)`.

Do not use it to write a gate. If the project has no CI script yet, that is a project task; come
back when `scripts/*ci*.sh`, a `Makefile` target or a `package.json` script exists.

## Inputs it asks for

`rcm-store-connect` always runs first and leaves what this skill relies on: the profile
fragment `scripts/rcm/profile.release.toml`, the helper `scripts/rcm/rcm_candidate.py`, a
presets file (its own `scripts/rcm/presets.release.toml` or an existing presets file it chose to
reuse), and a header at the top of `docs/rcm-connect.md` (under the `# rcm connect` title) with
the answers already given — repo name, platforms, `server.toml` path and `presets_file: <path>`.
**Stop with "run /rcm-store-connect first" when the header, `scripts/rcm/profile.release.toml`
or `scripts/rcm/rcm_candidate.py` is missing.** Read the answers from the header and do not ask
again; `presets_file` falls back to `scripts/rcm/presets.release.toml` when the header has no
such line. Below, `$presets_file` means that path.

| Question | Default | Why it is asked |
|---|---|---|
| Project path | current directory | where to look for the script |
| Repo name (`[[repos]] name`) | from the `docs/rcm-connect.md` header; else the only `[[repos]]` entry | the preset's `repo =` and the profile table name |
| `server.toml` path | from the header | only for the candidate check in step 7 — never edited |
| Gate entry script | detected (step 1) — asked only if 0 or 2+ candidates | the file the markers go into |
| May the script be edited? | yes | no → wrapper mode (step 2b) |
| Preset name | `gate-<script basename without extension>` for a shell script; `gate-<target>` for a make target or npm script — these two rules win even in wrapper mode (a wrapper over `make ci` is `gate-ci`); `gate-<repo>` only when the entry is neither a script nor a named target | the name the profile's `gate =` line points at |
| `expected_seconds` / `timeout_seconds` | asked; suggest the last real duration if the owner knows it, else 900 / 3600 | rcm uses `expected_seconds` until it has a median |
| `concurrency_group` | none | set when the gate needs a device, a simulator or a shared lock |

Nothing else. Everything the page will show is derived from what the script prints.

## What it creates

| Path (in the project) | Why |
|---|---|
| `scripts/rcm/gate.sh` — **wrapper mode only** | thin wrapper that calls the untouched gate and prints markers around its known phases (from `templates/gate_wrapper.sh`) |
| `scripts/rcm/progress_snippets.sh` — only when step 4 found a silent stretch | the two polling helpers (copied from this skill's `templates/progress_snippets.sh` next to the wrapper), sourced by the gate or the wrapper; run the snippets in bash, not sh |
| the gate script itself — **edit mode**, in place | `::rcm::step::` at each stage, `::rcm::fail::` + `::rcm::summary::` at the end, `::rcm::progress::` in the long steps |
| `$presets_file` — one `[[presets]]` block appended (a same-name block is replaced) | the `gate` preset; the live `server.toml` is never edited by a skill |
| `scripts/rcm/profile.release.toml` — the line `gate = "<preset>"` under `[repos.<name>.release.presets]` | the `gate` role in the profile fragment |
| `docs/rcm-connect.md` — a `## rcm-gate-connect — <date>` section | `/rcm-connect` reads it to know this tier is done |

Re-running the skill on a project that already has these files **reports** them (`kept:` per
path with the reason) and changes nothing; `--force` re-applies the templates and rewrites the
preset block and the `gate =` line, never the gate's own logic. The fragment files must already
exist (the store skill creates them) — this skill appends to them and never creates them. Only
bash and TOML are written, so there is no `__pycache__` or build output to ignore.

## What the human still fills in

- **Step names**, when the script's stages are not obvious from function names. The skill
  proposes them from the script; the owner confirms the words the page will show.
- **Which phases the wrapper wraps.** In wrapper mode the wrapper can only mark phases it can
  see from outside (separate commands, separate log files, separate exit points). If the gate is
  one opaque `make ci`, the wrapper prints one step and says so.
- **The denominator of every `progress` line.** The skill points at the loop or the parallel
  set; the owner confirms that the count is known before the loop starts. If it is not, the
  line is not added.
- `expected_seconds` — an honest guess. rcm replaces it with a median after a few runs.
- Any `# TODO(project):` the wrapper leaves where it could not see a phase boundary.

## Steps

Each step names the file it touches and the check that proves it.

### 1. Find the gate entry script

Detection, in this order; stop at the first that yields exactly one candidate, ask when it
yields none or several:

```sh
ls scripts/*ci*.sh scripts/*gate*.sh ci/*.sh 2>/dev/null                    # shell gates
grep -nE '^(ci|gate|check|test-all|verify)[a-z_-]*:' Makefile 2>/dev/null   # make targets
python3 -c 'import json;d=json.load(open("package.json"))["scripts"];print(*[k for k in d if any(w in k for w in ("ci","gate","check","verify"))])' 2>/dev/null
grep -rlE '^::rcm::step::' scripts 2>/dev/null                              # already marked?
```

A Makefile target that only depends on another (`gate: ci`) is the same phase, not a second
candidate — take the target that has the recipe. A script that already prints `::rcm::step::` is *connected*; the skill jumps to step 4 (silent
stretches) and step 6 (preset) and reports the markers it found instead of adding more — this is
an **adopted** gate: record `kept — real implementation`, and in step 6 keep the preset that
already runs it (the `gate *=` line of `scripts/rcm/profile.release.toml` if present, else the
existing `[[presets]]` whose `argv` names the script) instead of writing a new `gate-<name>` block.

Record the choice as `gate_entry=<path or make target or npm script>`; it goes into the preset's
`argv`. **Check:** the entry runs from the repository root with no arguments and exits 0 on a
clean tree (or the owner names the arguments the gate needs — they become fixed `argv`
entries, never inputs, because the `gate` role receives no inputs from rcm).

### 2. Add `::rcm::step::<name>` at each stage

**2a. Edit mode (default).** Most gates already have a "stage" helper — a function that prints
a banner such as `━━━ analyze` or `==> test`. Add the marker to *that* function so every stage
gets it in one edit:

```sh
step() { _RCM_STEP="$1"; echo; echo "━━━ $1"; echo "::rcm::step::$1"; }
```

If there is no such helper, add one at the top and call it once at the head of each stage
(`step analyze`, `step "unit tests"`, `step "build ios"`). Names are what the page shows: short,
lowercase, no exit codes or timestamps, stable across runs (the median per step depends on it).

**2b. Wrapper mode** — the gate is shared with another system, generated, or the owner said it
must not change. Copy `templates/gate_wrapper.sh` to `scripts/rcm/gate.sh`, set `GATE=` to the
entry from step 1 and fill the `phases` list with the phases the wrapper can see from outside.
The wrapper marks phases in one of two ways, and the template shows both:

- the gate exposes phases as separate invocations (`make analyze && make test`) — the wrapper
  runs them one by one with a marker before each;
- the gate is one command — the wrapper tails its stdout and turns lines matching a pattern the
  owner supplies (`^==> ` for example) into `::rcm::step::` lines. The pattern is the wrapper's
  only knowledge; if no line matches, the run is one step called `gate` and the page says so.

**Check:** `bash <entry> 2>&1 | grep -c '^::rcm::step::'` equals the number of stages the owner
named, and each marker is at column 0 (a leading space or a `\r` from a progress spinner makes
rcm ignore it — `sed 's/\r//'` before the echo if the gate's children emit carriage returns).

### 3. `::rcm::steps::N` — only when the script computes N

Print the total **only** if the script can compute it at run time from the same table that
drives the stages — a `phases=(…)` array whose length is `${#phases[@]}`, a `case` over a scope
flag that fills that array, a list of test suites globbed from disk. Then, and only then:

```sh
echo "::rcm::steps::${#phases[@]}"
```

A hand-written `::rcm::steps::7` is refused because it goes stale the first time someone adds a
stage, and a stale total is worse than none: rcm shows `7 of 7 done` while the eighth stage runs,
or draws the bar at 6/7 for a stage that no longer exists. Without a declared total rcm still
counts steps as they arrive and marks the total as partial; the page falls back to the measured
median, which is honest. **Check:** the number printed equals `grep -c '^::rcm::step::'` on the
same run, for each scope the gate supports (`full`, `smoke`, …).

### 4. Find silent stretches of five minutes or more and add `::rcm::progress::`

A step is silent when nothing reaches stdout for minutes: rcm's "not responding" heuristic and
the Store bar both go flat. Look for the three shapes that cause it:

| Shape | How to spot it | Denominator that is real |
|---|---|---|
| a child redirected to a file | `> "$log" 2>&1`, `tee`-less `flutter test`, `xcodebuild … > build.log` | none for one child; pair it with a heartbeat only if the child writes something countable (test files it has finished, targets it has built) |
| a parallel set | `&` followed by `wait`, `xargs -P`, several `pid=$!` | the number of children launched — `N children writing .rc files`, pattern A below |
| a lock or device wait | `flock`, a `while ! mkdir lockdir`, a queue script that polls | exactly `0/1` while waiting, `1/1` when acquired — pattern B below |

Add nothing for a stretch whose count you cannot name. The contract says it plainly: *if you do
not know it, do not print it; the page will fall back to a time-based bar and say so.*

Copy `templates/progress_snippets.sh` to `scripts/rcm/progress_snippets.sh` and source it from
the gate (edit mode) or the wrapper. It ships two functions, bash 3.2 compatible (no `wait -n`,
no associative arrays, no `mapfile`), and both are shown in full below because they are the
part people get wrong.

**Pattern A — N children, each writes `<name>.rc` when it finishes.** Every child is launched
as `( cmd; echo $? > "$dir/$name.rc" ) &`. The poller counts `.rc` files, so the denominator is
the number of children the script itself launched, and each child's state comes from its own
exit code:

```sh
# rcm_progress_children <dir> <unit> <name>...   → one line per change; returns 0 when all .rc files exist
rcm_progress_children() {
  local dir="$1" unit="$2"; shift 2
  local total=$# done=0 last=-1 failed=0 name rc
  [ "${total}" -gt 0 ] || return 0                       # 자식이 없으면 분모도 없다 — 아무것도 안 찍는다
  while :; do
    done=0
    for name in "$@"; do
      if [ -f "${dir}/${name}.rc" ]; then
        done=$((done + 1))
        if [ ! -f "${dir}/${name}.reported" ]; then
          rc="$(cat "${dir}/${name}.rc" 2>/dev/null || echo "?")"
          if [ "${rc}" = "0" ]; then
            echo "::rcm::progress::${done}/${total}::${unit}/${name}::ok"
          else
            failed=$((failed + 1))
            echo "::rcm::progress::${done}/${total}::${unit}/${name}::fail::rc=${rc}"
          fi
          : > "${dir}/${name}.reported"
        fi
      fi
    done
    if [ "${done}" != "${last}" ]; then
      if [ "${done}" -ge "${total}" ] && [ "${failed}" -gt 0 ]; then
        echo "::rcm::progress::${done}/${total}::${unit}::fail::${failed} of ${total} failed"
      elif [ "${done}" -ge "${total}" ]; then
        echo "::rcm::progress::${done}/${total}::${unit}::ok"
      else
        echo "::rcm::progress::${done}/${total}::${unit}::run"
      fi
      last="${done}"
    fi
    [ "${done}" -ge "${total}" ] && return 0
    sleep "${RCM_PROGRESS_POLL:-5}"
  done
}
```

The page keeps the last line per `unit`, so `${unit}/${name}` draws one cell per child and the
bare `${unit}` line moves the bar. After the loop, `wait` the pids as before and read the `.rc`
files for the verdict — the poller is display only; the gate's pass/fail must not depend on it.

**Pattern B — a lock wait heartbeat `0/1 → 1/1`.** Print `0/1 … wait` when the wait starts,
repeat it with the elapsed time as the note every 30 s so the queue does not call the job quiet,
and `1/1 … ok` the moment the lock is held:

```sh
# rcm_progress_lock <unit> <try-command...>   → runs try-command until it succeeds, heartbeat while waiting
rcm_progress_lock() {
  local unit="$1"; shift
  local started waited=0 next=0 max="${RCM_PROGRESS_LOCK_MAX:-0}"
  started="$(date +%s)"
  until "$@"; do
    waited=$(( $(date +%s) - started ))
    if [ "${waited}" -ge "${next}" ]; then
      echo "::rcm::progress::0/1::${unit}::wait::${waited}s"
      next=$((waited + ${RCM_PROGRESS_HEARTBEAT:-30}))
    fi
    if [ "${max}" -gt 0 ] && [ "${waited}" -ge "${max}" ]; then
      echo "::rcm::progress::0/1::${unit}::blocked::timed out after ${waited}s"
      return 5
    fi
    sleep "${RCM_PROGRESS_POLL:-5}"
  done
  echo "::rcm::progress::1/1::${unit}::ok::${waited}s"
  return 0
}
```

`try-command` is whatever the gate already uses to take the lock without blocking (`mkdir
"$lockdir"`, `flock -n 9`, a queue script with a `--try` flag). If the lock script prints rank
and ETA, pass them through as the note (`rank 2 · 12m to start`); rcm does not parse the note.

**Check:** run the gate with the stretch made artificially small (two children of `sleep 1`, a
lock that is free) and confirm the `progress` lines appear with the right `done/total`, that
`done` never exceeds `total`, and that the last line per unit is `ok` or `fail`, never `run`.

### 5. `::rcm::summary::` and `::rcm::fail::<name>` — the failure list

At the end of the gate, whatever the exit path:

- `::rcm::fail::<name>` once per thing that broke, with the **step's name** when the thing is a
  step (see the note at the top about names that do not match). Tests may be named individually
  (`::rcm::fail::test/login_test.dart`) — rcm keeps the first 100 distinct names and marks the
  list truncated after that, so do not print one per assertion line.
- `::rcm::summary::<one line>` last: `all green` or `2 failed: analyze, unit tests`. The queue
  shows it; keep it under 200 characters.

The reliable place is an `EXIT` trap that knows the current step **and whether the script
reached its end**. The second part is not optional: on bash 3.2 a `set -u` death hands the
`EXIT` trap `$?=0`, so without a finished flag the gate dies silently, exits 0, and rcm records
a green run with no summary.

```sh
_RCM_STEP=""; _RCM_DONE=0
_on_exit() {
  local rc="$1"
  case "${rc}" in 129|130|131|143) return 0 ;; esac    # a signal — not a step's fault
  if [ "${rc}" = "0" ] && [ "${_RCM_DONE}" != "1" ]; then   # died before the end: not green
    echo "::rcm::fail::${_RCM_STEP:-start}"
    echo "::rcm::summary::ended without finishing (rc=0, no completion flag)"
    exit 1
  fi
  [ "${rc}" = "0" ] && return 0
  [ -n "${_RCM_STEP}" ] && echo "::rcm::fail::${_RCM_STEP}"
  echo "::rcm::summary::failed at ${_RCM_STEP:-start} (rc=${rc})"
}
trap 'exit 143' TERM; trap 'exit 130' INT; trap '_on_exit $?' EXIT
# … stages …
_RCM_DONE=1
echo "::rcm::summary::all green"        # the last line of the script, explicitly
```

Do not name a step on `TERM`/`INT`: rcm cancels and times out with SIGTERM, and a cancelled job
must not count as that step's failure in the 20-job window. **Check, three runs:** poison one
stage (`false` inserted after the marker) → exactly one `::rcm::fail::` with that step's name
and a non-zero exit; remove the poison → the last line is `::rcm::summary::all green` and exit
0; insert `echo "$UNSET_VAR"` under `set -u` inside a stage → exit 1 with `::rcm::fail::<that
step>` and no `all green`.

### 6. The `gate` preset and the profile line

Skills never edit the live `server.toml`. The preset goes into `$presets_file` (from the
header; default `scripts/rcm/presets.release.toml`) — append it, or replace the block whose
`name` is the same — and the role line goes into `scripts/rcm/profile.release.toml`. The server owner merges the
fragments; step 7 checks the merged result on a throwaway copy. The block (the skill prints it
and asks before writing):

```toml
[[presets]]
name = "gate-<basename>"                     # gate-<target> for make/npm (also when wrapped); gate-<repo> only if neither applies
description = "CI gate on the release ref"
argv = ["bash", "<gate_entry>"]              # or ["bash", "scripts/rcm/gate.sh"] in wrapper mode
source_modes = ["git_ref"]                   # the Store tab runs a ref, not an uploaded tree
repo = "<repo name>"
concurrency_group = "<group>"                # only if the gate needs a device or a shared lock
expected_seconds = 900                       # replaced by the median after a few runs
timeout_seconds = 3600
env_passthrough = ["PATH", "HOME", "LANG"]
[presets.env]
CI = "1"
```

and in `scripts/rcm/profile.release.toml`, under the existing table (add the line; do not add a
second `[repos.<name>.release.presets]` header):

```toml
[repos.<name>.release.presets]
gate = "gate-<basename>"
```

No `[[presets.inputs]]`: the `gate` role receives none. `artifacts` stays optional and unset
here: the gate's verdict is its exit code and its markers, so there is no JSON for rcm to read —
add `artifacts = [...]` only if the gate writes a report the owner wants back.
**Check** (with `gate_name` = the value of the `gate *=` line, whatever it is — `gate-<basename>`
for a new block, the project's own name for an adopted gate):
`gate_name="$(sed -nE 's/^gate *= *"([^"]+)".*/\1/p' scripts/rcm/profile.release.toml)"`;
`grep -cE "^name *= *\"$gate_name\"" "$presets_file"` is 1 and
`grep -cE '^gate *=' scripts/rcm/profile.release.toml` is 1 (the profile template aligns keys,
hence the `*`).

### 7. Verify without a build machine

A stub run proves the marker stream; `rcm check` proves the config. Neither needs a worker.
When the host refuses to run the gate locally (a project guard/hook such as a load guard that
redirects heavy commands to the build machine), the accepted verification is instead: grep the
script for the four marker kinds (`::rcm::step::`, `::rcm::fail::`, `::rcm::summary::`,
`::rcm::progress::` where a silent stretch exists) plus the gate's own `--selftest` if it has one;
say which of the two you did in the report.

```sh
# 1. dry run: every command the gate would call is replaced by a stub that prints its name
mkdir -p /tmp/rcm-stub && for t in flutter xcodebuild gradle npm make pytest; do
  printf '#!/bin/sh\necho "[stub] %s $*"\n' "$t" > /tmp/rcm-stub/$t; chmod +x /tmp/rcm-stub/$t
done
PATH=/tmp/rcm-stub:$PATH bash <gate_entry> 2>&1 | tee /tmp/gate-dry.log
grep -E '^::rcm::(steps|step|step-end|fail|summary|progress)::' /tmp/gate-dry.log

# 2. config — merges the two fragments into a temporary candidate and runs `rcm check` on it;
#    the live server.toml and the running server are not touched (helper installed by rcm-store-connect)
python3 scripts/rcm/rcm_candidate.py --server <server.toml from the header> --repo <name> --presets "$presets_file" --check
```

Read the grep output as the page would: a `steps::N` (if any) equal to the count of `step::`
lines; each `progress` unit ending in `ok`/`fail`; a single `summary`. Then the first real run
goes through the Store tab's gate card on a branch, not through this skill.

Finally append to `docs/rcm-connect.md`:

```
## rcm-gate-connect — <date>
Created: <paths from "What it creates" that were actually written — name $presets_file as written>
You must fill in: <the items from "What the human still fills in" still open>
Verified: dry run <N> steps · progress units <list or "none needed"> · rcm_candidate --check release <repo>: <ok|warn (<warnings>)|not verified>
```

Do not commit; tell the owner which files to commit (the gate or wrapper, the snippets, the two
fragments, `docs/rcm-connect.md`) and that the fragments still have to be merged into the
server's `server.toml` by whoever operates it.

## Done means

- `python3 scripts/rcm/rcm_candidate.py --server <server.toml> --repo <name> --presets "$presets_file" --check`: the
  `release <repo>` row is `ok`, or `warn` only about roles this skill does not own; the live
  `server.toml` is unchanged (`git diff` on it is empty if it is in a repo).
- `$presets_file` holds exactly one `gate-…` block and `scripts/rcm/profile.release.toml`
  exactly one `gate =` line.
- In wrapper mode `bash scripts/rcm/gate.sh --selftest` prints `selftest ok`; in edit mode the
  three runs of step 5 pass.
- The dry run prints one `::rcm::step::` per stage, `::rcm::fail::` naming the poisoned step and
  nothing on a clean run, one `::rcm::summary::`, and `::rcm::progress::` lines only where a
  counted denominator exists.
- The first real gate job from the Store tab shows named steps with their own durations; after a
  few runs the queue's ETA says `median` for this preset instead of `expected`.

## Never

- Never print a total the script did not compute (`::rcm::steps::` by hand) or a progress
  denominator it did not count. A hatched bar is honest; a wrong percentage is not.
- Never let a display marker change the verdict: the gate's exit code and `.rc` files decide,
  the pollers only print.
- Never name a step as failed on cancel or timeout (SIGTERM/SIGINT), and never let a `set -u`
  death exit 0 — an `EXIT` trap that sees `$?=0` without a "finished" flag must exit 1.
- Never give the `gate` preset inputs or an irreversible action; it runs a ref read-only.
- Never edit the live `server.toml` or restart the server; write the fragments and check a
  candidate copy.
- Never write secrets, tokens or store credentials into the gate, the preset, the log or
  `docs/rcm-connect.md`.
- Never put a project's name, paths or step names into rcm itself; they live in the project's
  script and `server.toml`.
- Never `git switch` the owner's worktree or commit on their behalf; the skill edits files and
  reports, the owner commits.
