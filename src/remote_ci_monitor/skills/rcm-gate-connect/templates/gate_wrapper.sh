#!/usr/bin/env bash
# scripts/rcm/gate.sh — rcm marker wrapper around a gate script that must not change.
#
# Installed by the rcm-gate-connect skill. Bash 3.2 compatible (macOS /bin/bash).
#
# What it does: runs the project's existing gate and prints the `::rcm::` markers rcm reads
# (see docs/release-contract.md §3 of remote_ci_monitor). The gate itself is untouched; the
# verdict is the gate's exit code, never anything this wrapper computes.
#
# Two modes, pick one by filling the block below:
#   MODE=phases   — the gate exposes its phases as separate invocations (make targets, npm
#                   scripts, sub-commands). The wrapper runs them one by one with a marker
#                   before each and can therefore print a computed `::rcm::steps::N`.
#   MODE=tail     — the gate is one command. The wrapper reads its stdout line by line and turns
#                   lines matching STEP_PATTERN into `::rcm::step::` lines. It cannot know the
#                   total, so it prints no `::rcm::steps::`.
set -u

# ── fill in ─────────────────────────────────────────────────────────────────────────────────
MODE="phases"                                  # "phases" | "tail"
GATE="make"                                    # TODO(project): the gate entry from step 1
PHASES="analyze test build"                    # MODE=phases: space-separated targets/sub-commands,
                                               #   run as: $GATE <phase>. Names are what the page shows.
STEP_PATTERN='^==> '                           # MODE=tail: ERE; the matched line minus the prefix
                                               #   becomes the step name
SNIPPETS="$(dirname "$0")/progress_snippets.sh"   # optional: progress helpers (step 4)
# ────────────────────────────────────────────────────────────────────────────────────────────

# --selftest 는 이 파일이 자기 자신을 가짜 게이트로 다시 부를 때 아래 셋을 덮어쓴다. 실제 잡은 안 건드린다.
if [ -n "${RCM_GATE_SELFTEST_GATE:-}" ]; then
  GATE="${RCM_GATE_SELFTEST_GATE}"; MODE="${RCM_GATE_SELFTEST_MODE:-phases}"
  PHASES="${RCM_GATE_SELFTEST_PHASES:-analyze test}"; STEP_PATTERN='^==> '
fi

[ -f "${SNIPPETS}" ] && . "${SNIPPETS}"

_RCM_STEP=""
_RCM_FINISHED=0
_RCM_STEP_FILE="$(mktemp -t rcm-gate-step)"     # tail 모드의 서브셸이 마지막 스텝 이름을 여기 적는다
step() { _RCM_STEP="$1"; printf '%s' "$1" > "${_RCM_STEP_FILE}"; echo "::rcm::step::$1"; }

_on_exit() {
  local rc="$1"
  if [ -s "${_RCM_STEP_FILE}" ]; then _RCM_STEP="$(cat "${_RCM_STEP_FILE}")"; fi
  rm -f "${_RCM_STEP_FILE}"
  # 신호로 죽었으면 어느 스텝의 잘못도 아니다 — rcm 의 취소·타임아웃이 SIGTERM 이다.
  case "${rc}" in 129|130|131|143) return 0 ;; esac
  if [ "${rc}" = "0" ] && [ "${_RCM_FINISHED}" != "1" ]; then
    # bash 3.2 는 `set -u` 로 죽을 때 EXIT 트랩에 $?=0 을 넘긴다. 완주 표시 없는 0 은 초록이 아니다.
    echo "::rcm::fail::${_RCM_STEP:-wrapper}"
    echo "::rcm::summary::gate ended without finishing (rc=0 but no completion flag)"
    exit 1
  fi
  if [ "${rc}" != "0" ]; then
    [ -n "${_RCM_STEP}" ] && echo "::rcm::fail::${_RCM_STEP}"
    echo "::rcm::summary::failed at ${_RCM_STEP:-start} (rc=${rc})"
  fi
  return 0
}
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 131' QUIT
trap 'exit 143' TERM
trap '_on_exit $?' EXIT

run_phases() {
  # 총 스텝 수는 이 목록의 길이에서 **계산**된다 — 손으로 적은 숫자가 아니다.
  set -- ${PHASES}
  echo "::rcm::steps::$#"
  local phase rc
  for phase in "$@"; do
    step "${phase}"
    "${GATE}" "${phase}"
    rc=$?
    if [ "${rc}" != "0" ]; then
      echo "::rcm::step-end::fail"
      return "${rc}"
    fi
    echo "::rcm::step-end::ok"
  done
  return 0
}

run_tail() {
  # 게이트의 stdout 을 한 줄씩 읽어 단계 배너를 마커로 바꾼다. 원본 줄도 그대로 흘려보낸다.
  # `\r` 은 지운다 — 스피너가 남긴 캐리지 리턴이 마커를 줄 중간으로 밀면 rcm 이 무시한다.
  local line name rc
  "${GATE}" 2>&1 | while IFS= read -r line; do
    line="${line//$'\r'/}"
    if printf '%s\n' "${line}" | grep -qE "${STEP_PATTERN}"; then
      name="$(printf '%s\n' "${line}" | sed -E "s/${STEP_PATTERN}//")"
      step "${name}"
    fi
    printf '%s\n' "${line}"
  done
  # 파이프의 종료코드는 게이트의 것이어야 한다 — bash 3.2 에 `set -o pipefail` 이 있다.
  rc="${PIPESTATUS[0]}"
  return "${rc}"
}

main() {
  local rc=0
  case "${MODE}" in
    phases) run_phases || rc=$? ;;
    tail)
      # 패턴에 맞는 줄이 하나도 없으면 실행 전체가 스텝 `gate` 하나다 — 그래서 먼저 연다.
      # 서브셸에서 찍은 스텝 이름은 _RCM_STEP_FILE 을 거쳐 EXIT 트랩까지 온다.
      step "gate"
      set -o pipefail
      run_tail || rc=$?
      ;;
    *) echo "gate.sh: MODE must be phases or tail" >&2; exit 2 ;;
  esac
  if [ "${rc}" = "0" ]; then
    echo "::rcm::summary::all green"
  fi
  _RCM_FINISHED=1
  return "${rc}"
}

# ── --selftest: proves the wrapper's contract on a stub gate in a temp dir. `--selftest` is never a phase.
_selftest() {
  local d self out rc fails=0
  self="$0"; d="$(mktemp -d -t rcm-gate-selftest)"
  printf '#!/bin/sh\necho "[stub] $1"\n[ "$1" = "${POISON:-}" ] && exit 3\nexit 0\n' > "${d}/gate"; chmod +x "${d}/gate"
  printf '#!/bin/sh\necho "==> analyze"; printf "==> test\\r\\n"; exit ${TAILRC:-0}\n' > "${d}/tailgate"; chmod +x "${d}/tailgate"
  _expect() {   # $1=설명 $2=기대 rc $3=stdout 에 있어야 할 ERE $4=없어야 할 ERE ; 나머지=환경
    local what="$1" want="$2" must="$3" mustnot="$4"; shift 4
    out="$(env "$@" bash "${self}" 2>/dev/null)"; rc=$?
    if [ "${rc}" != "${want}" ]; then echo "SELFTEST FAIL: ${what} — rc ${rc}, want ${want}"; fails=1; fi
    if ! printf '%s\n' "${out}" | grep -qE "${must}"; then echo "SELFTEST FAIL: ${what} — missing /${must}/"; fails=1; fi
    if [ -n "${mustnot}" ] && printf '%s\n' "${out}" | grep -qE "${mustnot}"; then echo "SELFTEST FAIL: ${what} — has /${mustnot}/"; fails=1; fi
  }
  _expect "phases green: computed steps + summary" 0 '^::rcm::steps::2$' '::rcm::fail::' RCM_GATE_SELFTEST_GATE="${d}/gate"
  _expect "phases green: last line is all green" 0 '^::rcm::summary::all green$' '' RCM_GATE_SELFTEST_GATE="${d}/gate"
  _expect "phases poisoned: step-end::fail + fail::<step>" 3 '^::rcm::step-end::fail$' '::rcm::summary::all green' RCM_GATE_SELFTEST_GATE="${d}/gate" POISON=test
  _expect "phases poisoned: names the step" 3 '^::rcm::fail::test$' '' RCM_GATE_SELFTEST_GATE="${d}/gate" POISON=test
  _expect "tail: banner → step, CR stripped" 0 '^::rcm::step::test$' $'\r' RCM_GATE_SELFTEST_GATE="${d}/tailgate" RCM_GATE_SELFTEST_MODE=tail
  _expect "tail red: names the tailed step" 4 '^::rcm::fail::test$' '' RCM_GATE_SELFTEST_GATE="${d}/tailgate" RCM_GATE_SELFTEST_MODE=tail TAILRC=4
  _expect "set -u death is not green" 1 '^::rcm::fail::' '::rcm::summary::all green' RCM_GATE_SELFTEST_GATE="${d}/gate" RCM_GATE_SELFTEST_UNBOUND=1
  rm -rf "${d}"
  [ "${fails}" = "0" ] && echo "selftest ok"
  return "${fails}"
}

case "${1:-}" in
  --selftest) _RCM_DONE_SELFTEST=1; _selftest; rc=$?; _RCM_FINISHED=1; exit "${rc}" ;;
esac
if [ -n "${RCM_GATE_SELFTEST_UNBOUND:-}" ]; then
  # 자기검증 전용: 스텝 안에서 `set -u` 로 죽는 경우를 재현한다.
  step "analyze"; echo "${_RCM_SELFTEST_UNSET_VAR}"
fi
main "$@"
