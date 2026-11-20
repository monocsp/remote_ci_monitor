#!/usr/bin/env bash
# rcm-ci-connect: 잡 스크립트의 머리 — 마커 함수 · isolate 단계 · venv 단계.
# 프로젝트는 이 파일을 scripts/ci/rcm_ci.sh 로 복사하고 맨 아래의 STEPS 와 run_<단계> 함수를
# 자기 검사로 채운다. 준비 단계(isolate · venv)의 실패는 종료 75(EX_TEMPFAIL)다. 알림 훅이 그것을
# 「코드의 실패」가 아니라 `unavailable · infra` 로 올려 GitHub 이 대신 돈다.
# 계약은 remote_ci_monitor 의 docs/m5r-workplan.md §3.3(결정 189)이다.
set -euo pipefail

EX_TEMPFAIL=75
WORKSPACE="$(pwd)"
STEPS="isolate venv lint test"     # TODO(project): 단계 이름을 차례대로

step() { echo "::rcm::step::$1"; }
fail() { echo "::rcm::fail::$1"; }

run_isolate() {
  # 시험이 빌드 머신 사용자의 설정 · 데이터를 건드리지 못하게 집과 XDG 를 워크스페이스 아래로 돌린다.
  # 이것은 실수를 막는 장치이고 공격을 막는 장치가 아니다.
  export HOME="$WORKSPACE/.rcm-ci/home"
  export XDG_CONFIG_HOME="$HOME/.config" XDG_CACHE_HOME="$HOME/.cache"
  export XDG_STATE_HOME="$HOME/.local/state" XDG_DATA_HOME="$HOME/.local/share"
  mkdir -p "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME" "$XDG_DATA_HOME"
  # macOS 의 유닉스 소켓 경로는 104바이트다 — 워크스페이스 아래가 아니라 짧은 경로를 쓴다
  RCM_CI_TMP="$(mktemp -d /tmp/rcmci.XXXXXX)" || return 1
  export TMPDIR="$RCM_CI_TMP"
  trap 'rm -rf "$RCM_CI_TMP"' EXIT
  local name
  for name in $(env | sed -n 's/^\(RCM_[A-Za-z0-9_]*\)=.*/\1/p'); do
    case "$name" in
      RCM_CHROME|RCM_CI_PYTHON|RCM_CI_STEPS) ;;
      *) unset "$name" ;;
    esac
  done
  local tool
  for tool in "${RCM_CI_PYTHON:-python3}" git; do   # TODO(project): 잡이 부르는 도구
    command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool"; return 1; }
  done
}

run_venv() {
  "${RCM_CI_PYTHON:-python3}" -m venv .venv || return 1
  .venv/bin/python -m pip install -q -e '.[dev]' || return 1
}

run_lint() { .venv/bin/ruff check . && .venv/bin/ruff format --check .; }   # TODO(project)
run_test() { .venv/bin/pytest; }                                             # TODO(project)

wanted() {
  [ -z "${RCM_CI_STEPS:-}" ] && return 0
  case ",$RCM_CI_STEPS," in *",$1,"*) return 0 ;; esac
  return 1
}

set -- $STEPS
echo "::rcm::steps::$#"
for s in $STEPS; do
  wanted "$s" || continue
  step "$s"
  if ! "run_$s"; then
    fail "$s"
    case "$s" in isolate|venv) exit "$EX_TEMPFAIL" ;; *) exit 1 ;; esac
  fi
done
