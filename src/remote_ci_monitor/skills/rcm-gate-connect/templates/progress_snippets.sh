#!/usr/bin/env bash
# scripts/rcm/progress_snippets.sh — `::rcm::progress::` helpers for long, silent gate steps.
#
# Installed by the rcm-gate-connect skill. Source it (`. scripts/rcm/progress_snippets.sh`);
# do not execute it. Bash 3.2 compatible: no `wait -n`, no associative arrays, no `mapfile`.
#
# Grammar (docs/release-contract.md §3):
#   ::rcm::progress::<done>/<total>::<unit>::<state>[::<note>]
#   state ∈ run · ok · fail · skip · env · review · blocked · wait
# The page keeps the last line per <unit> and moves the bar with the newest done/total.
# Both helpers are DISPLAY ONLY — the gate's verdict must come from exit codes and .rc files,
# never from what these functions print. Print a denominator only when the script counted it.

# ── Pattern A: N children, each writing <name>.rc when it finishes ─────────────────────────
#
# Launch each child as:
#     ( some_command; echo $? > "${dir}/${name}.rc" ) &
# then call:
#     rcm_progress_children "${dir}" "<unit>" name1 name2 ...
# The denominator is $# — the number of names you passed, i.e. the children you launched.
# One line per child (`<unit>/<name>`, ok|fail with rc) and one bare `<unit>` line for the bar.
# Returns 0 when every .rc file exists. `wait` your pids afterwards for the real verdict.
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

# ── Pattern B: a lock / device wait heartbeat, 0/1 → 1/1 ────────────────────────────────────
#
#     rcm_progress_lock "<unit>" <try-command ...>
# Runs <try-command> until it exits 0 (a NON-blocking attempt: `mkdir "$lockdir"`,
# `flock -n 9`, a queue script's `--try`). Prints `0/1 … wait::<elapsed>s` when the wait starts
# and every RCM_PROGRESS_HEARTBEAT seconds (default 30) so the queue never calls the job quiet,
# then `1/1 … ok::<elapsed>s` when the lock is held. Returns 0 once acquired; the caller keeps
# whatever timeout it already had around the wait (RCM_PROGRESS_LOCK_MAX seconds → returns 5).
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

# ── Self-check: `bash progress_snippets.sh --selftest` proves both patterns on tiny inputs ───
# 실행됐을 때만 돈다. `. progress_snippets.sh` 로 source 하면 부모의 $1 이 넘어오므로 여기서 걸러야 한다.
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ "${1:-}" = "--selftest" ]; then
  d="$(mktemp -d -t rcm-progress)"
  RCM_PROGRESS_POLL=0.2
  ( sleep 0.3; echo 0 > "${d}/a.rc" ) &
  ( sleep 0.6; echo 7 > "${d}/b.rc" ) &
  out="$(rcm_progress_children "${d}" test a b)"
  wait
  echo "${out}"
  echo "${out}" | grep -q '^::rcm::progress::2/2::test::fail::1 of 2 failed$' || { echo "SELFTEST FAIL: children bar"; exit 1; }
  echo "${out}" | grep -q '^::rcm::progress::[12]/2::test/b::fail::rc=7$' || { echo "SELFTEST FAIL: child b"; exit 1; }
  n=0
  RCM_PROGRESS_HEARTBEAT=0
  _try() { n=$((n + 1)); [ "${n}" -ge 3 ]; }
  out="$(rcm_progress_lock devices _try)"
  echo "${out}"
  echo "${out}" | grep -q '^::rcm::progress::0/1::devices::wait::' || { echo "SELFTEST FAIL: lock wait"; exit 1; }
  echo "${out}" | tail -1 | grep -q '^::rcm::progress::1/1::devices::ok::' || { echo "SELFTEST FAIL: lock ok"; exit 1; }
  rm -rf "${d}"
  echo "selftest ok"
fi
