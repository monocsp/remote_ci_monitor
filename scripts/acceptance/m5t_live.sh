#!/usr/bin/env bash
# M5t-a 실기 — docs/m5t-workplan.md §7 의 실기 행(TR16 · TD24 · TD25 · TL12 · TN8)과 결정 352 의
# 한 줄(setup 이 운영 설정 자리를 거절한다)을 진짜 프로세스로 돌린다. pytest 가 부르지 않는다
# (순수 검사 함수만 tests/test_acceptance_m5t_live.py 가 source 해서 부른다).
#
#   bash scripts/acceptance/m5t_live.sh                  # 포트 8794 · 8795, Docker 없이 약 5초
#                                                        # (2026-11-26 Apple Silicon Mac, real 5.16)
#   M5T_PORT=8796 M5T_PORT2=8797 bash scripts/acceptance/m5t_live.sh
#   RCM_LIVE_PROD_CONFIG=<운영 server.toml 절대 경로> bash scripts/acceptance/m5t_live.sh
#                                                        # 결정 352 줄도 돈다(없으면 UNKNOWN)
#
# 지키는 것(.claude/skills/_shared/test-server.md · .claude/skills/live-verify/SKILL.md §1):
#   - 이 체크아웃의 .venv/bin/rcm 만 쓴다. 운영(8787 · ~/.config/rcm · ~/.local/share/rcm ·
#     launchd com.remote-ci-monitor.* · 서비스 venv)은 건드리지 않는다.
#   - 시험 폴더는 새로 만든다: ~/.local/share/rcm-devtest/m5t-live-<무작위>. 이미 있는 폴더는
#     쓰지 않고 지우지도 않는다(rm -rf 없음). M5T_BASE 로 줄 때도 아직 없는 폴더만 받는다.
#   - rcm 명령은 모두 HOME=<시험>/home 과 XDG 네 폴더(<시험>/xdg/…)로, 셸의 RCM_* 는 모두 지우고
#     돈다. 서버는 advertise = false, 클라이언트와 워커는 URL 을 명시한다.
#   - launchd 라벨은 com.example.rcm-m5t-live-<무작위> 하나뿐이다. bootstrap 전에 server.toml 의
#     port · advertise · data_dir · bind 와 plist 의 Label · --config · HOME · 로그 · 작업 폴더가
#     시험 값인지 본다. 하나라도 아니면 bootstrap 하지 않는다. plist 는 ~/Library/LaunchAgents 가
#     아니라 시험 폴더(<시험>/home/Library/LaunchAgents)에 두고 그 경로로 bootstrap 한다.
#   - bootout 은 그 시험 라벨에만 한다. trap 이 실패 때도 내리고 띄운 프로세스를 멈춘다.
#   - 운영 server.toml(RCM_LIVE_PROD_CONFIG)은 내용을 읽지 않고 mtime 만 앞뒤로 잰다.
#   - 프리셋은 sh -c 로 표식과 종료 코드만 내는 가짜다. 알림 훅은 두지 않는다. TN8 의 osascript 는
#     종료 코드를 적는 감싸개를 거쳐 진짜 /usr/bin/osascript 를 부른다.
#
# 결과: <시험>/out/summary.txt(행마다 PASS · FAIL · UNKNOWN 한 줄) · jobs.json(잡의 마지막 행) ·
# 명령마다 출력. 종료 코드: FAIL 이 하나라도 있으면 1, 시작 전 검사에서 멈추면 2, 아니면 0.
set -euo pipefail

M5T_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RCM="$M5T_ROOT/.venv/bin/rcm"
PY=${M5T_PY:-$M5T_ROOT/.venv/bin/python}
PROD_LABEL_PREFIX="com.remote-ci-monitor"
TEST_LABEL_PREFIX="com.example.rcm-m5t-live-"

die() {
  echo "m5t_live: $*" >&2
  exit 2
}

# ── 순수 검사 함수(시험이 source 해서 부른다) ──────────────────────────────────

# 시험 포트인가: 8790~8899 의 숫자. 8787(운영) · 8788(다른 세션의 dev 서버)은 그 밖이다
is_test_port() { # is_test_port <port>
  case "${1:-}" in "" | *[!0-9]*) return 1 ;; esac
  [ "${#1}" -le 5 ] || return 1
  [ "$1" -ge 8790 ] && [ "$1" -le 8899 ]
}

# 시험 라벨인가: com.example.rcm-m5t-live-<소문자 · 숫자 4~32자>. 운영 접두사는 대소문자와 상관없이
# 먼저 거절한다
is_test_label() { # is_test_label <label>
  local l=${1:-} lower
  lower=$(printf '%s' "$l" | tr '[:upper:]' '[:lower:]')
  case "$lower" in "$PROD_LABEL_PREFIX" | "$PROD_LABEL_PREFIX".*) return 1 ;; esac
  case "$l" in "$TEST_LABEL_PREFIX"*) ;; *) return 1 ;; esac
  local rest=${l#"$TEST_LABEL_PREFIX"}
  case "$rest" in "" | *[!a-z0-9]*) return 1 ;; esac
  [ "${#rest}" -ge 4 ] && [ "${#rest}" -le 32 ]
}

# 절대 경로이고 `.` · `..` · 빈 조각이 없는가
plain_abs() { # plain_abs <path>
  case "${1:-}" in /*) ;; *) return 1 ;; esac
  case "$1/" in */../* | */./* | *//*) return 1 ;; esac
  return 0
}

# <path> 가 <root> 이거나 그 안인가 — 글자로 견준다(조각 경계를 지킨다)
is_inside() { # is_inside <path> <root>
  local p=${1%/} r=${2%/}
  [ -n "$r" ] || return 1
  [ "$p" = "$r" ] && return 0
  case "$p" in "$r"/*) return 0 ;; esac
  return 1
}

# 시험 폴더로 받을 수 있는가: <home>/.local/share/rcm-devtest/<이름> 이나
# <home>/.local/share/rcm-devtest-<이름>, 이름은 한 단계다. 그 밖(홈 · 운영 data_dir · /tmp ·
# 상대 경로)은 거절한다
is_test_base() { # is_test_base <base> <real home>
  local b=${1:-} h=${2:-} rest
  plain_abs "$b" || return 1
  plain_abs "$h" || return 1
  [ "$h" != / ] || return 1
  case "$b" in
    "$h/.local/share/rcm-devtest/"?*) rest=${b#"$h/.local/share/rcm-devtest/"} ;;
    "$h/.local/share/rcm-devtest-"?*) rest=${b#"$h/.local/share/rcm-devtest-"} ;;
    *) return 1 ;;
  esac
  case "$rest" in */*) return 1 ;; esac
  return 0
}

# 시험 경로인가: 시험 폴더 안이고, 진짜 홈의 운영 자리(설정 · data_dir · LaunchAgents · 로그) 안이
# 아니다. /tmp 거절은 시험 폴더를 고를 때 main 이 한다(시험의 가짜 홈이 /tmp 아래일 수 있다)
is_test_path() { # is_test_path <path> <base> <real home>
  local p=${1:-} b=${2:-} h=${3:-} prod
  plain_abs "$p" || return 1
  is_inside "$p" "$b" || return 1
  for prod in "$h/.config/rcm" "$h/.local/share/rcm" "$h/Library/LaunchAgents" \
    "$h/Library/Logs/rcm"; do
    is_inside "$p" "$prod" && return 1
  done
  return 0
}

# 한 파일의 키를 `키=값` 줄로 — toml 은 [server], plist 는 위쪽 키와 EnvironmentVariables.HOME.
# 값에 줄바꿈이 있으면 `<newline>` 이다(한 줄 한 값을 지킨다)
_fields() { # _fields toml|plist <file>
  "$PY" -I - "$1" "$2" <<'EOF'
import plistlib, sys, tomllib
kind, path = sys.argv[1:3]
def show(v):
    if v is None:
        return "<missing>"
    s = str(v).lower() if isinstance(v, bool) else str(v)
    return "<newline>" if "\n" in s or "\r" in s else s
if kind == "toml":
    with open(path, "rb") as f:
        d = tomllib.load(f).get("server", {})
    for k in ("port", "advertise", "data_dir", "bind"):
        print(f"{k}={show(d.get(k))}")
else:
    with open(path, "rb") as f:
        d = plistlib.load(f)
    env = d.get("EnvironmentVariables") or {}
    for k in ("Label", "WorkingDirectory", "StandardOutPath", "StandardErrorPath"):
        print(f"{k}={show(d.get(k))}")
    print(f"HOME={show(env.get('HOME'))}")
    args = d.get("ProgramArguments") or []
    print(f"argc={len(args)}")
    for i, a in enumerate(args):
        print(f"arg{i}={show(a)}")
EOF
}

_field() { # _field <key> <fields text>
  printf '%s\n' "$2" | sed -n "s/^$1=//p" | sed -n 1p
}

# bootstrap 전 검사 하나: server.toml 의 port · advertise · data_dir · bind 가 시험 값인가.
# 틀리면 무엇이 틀렸는지 한 줄을 찍고 1
check_server_toml() { # check_server_toml <file> <port> <data_dir> <base> <real home>
  local f=$1 port=$2 data=$3 base=$4 home=$5 got v
  got=$(_fields toml "$f" 2>/dev/null) || {
    echo "cannot read $f as TOML"
    return 1
  }
  v=$(_field port "$got")
  { [ "$v" = "$port" ] && is_test_port "$v"; } || {
    echo "server.toml port is $v, not the test port $port"
    return 1
  }
  v=$(_field advertise "$got")
  [ "$v" = false ] || {
    echo "server.toml advertise is $v, not false"
    return 1
  }
  v=$(_field data_dir "$got")
  { [ "$v" = "$data" ] && is_test_path "$v" "$base" "$home"; } || {
    echo "server.toml data_dir is $v, not the test data_dir $data"
    return 1
  }
  v=$(_field bind "$got")
  [ "$v" = 127.0.0.1 ] || {
    echo "server.toml bind is $v, not 127.0.0.1"
    return 1
  }
  return 0
}

# bootstrap 전 검사 둘: plist 파일 자리 · Label · `<rcm> serve --config <시험 설정>` · HOME · 로그 ·
# 작업 폴더가 시험 값인가
check_plist() { # check_plist <file> <label> <config> <test home> <rcm> <base> <real home>
  local f=$1 label=$2 conf=$3 th=$4 rcm=$5 base=$6 home=$7 got v k
  is_test_path "$f" "$base" "$home" || {
    echo "the plist $f is not inside the test folder"
    return 1
  }
  got=$(_fields plist "$f" 2>/dev/null) || {
    echo "cannot read $f as a plist"
    return 1
  }
  v=$(_field Label "$got")
  { [ "$v" = "$label" ] && is_test_label "$v"; } || {
    echo "plist Label is $v, not the test label $label"
    return 1
  }
  v="$(_field argc "$got") $(_field arg0 "$got") $(_field arg1 "$got") $(_field arg2 "$got") $(_field arg3 "$got")"
  { [ "$v" = "4 $rcm serve --config $conf" ] && is_test_path "$conf" "$base" "$home"; } || {
    echo "plist ProgramArguments is '$v', not '$rcm serve --config $conf'"
    return 1
  }
  v=$(_field HOME "$got")
  { [ "$v" = "$th" ] && is_test_path "$v" "$base" "$home"; } || {
    echo "plist HOME is $v, not the test home $th"
    return 1
  }
  for k in StandardOutPath StandardErrorPath WorkingDirectory; do
    v=$(_field "$k" "$got")
    is_test_path "$v" "$base" "$home" || {
      echo "plist $k is $v, outside the test folder"
      return 1
    }
  done
  return 0
}

# doctor --json 이 늘 내는 키 — src/remote_ci_monitor/doctor.py 의 document() 사전 그대로다(모르는 값은
# 키를 빼지 않고 null). tests/test_acceptance_m5t_live.py 가 그 사전과 견준다
DOCTOR_KEYS="doctor_version role measured_in host server service shell rcm_requirements presets probes remote_machines summary problems undecided warnings exit"

# TD24: doctor 의 사람용 표와 --json 이 행마다 같은 판정인가, 측정 PATH 를 적었는가, 두 실행의
# 종료 코드가 문서의 exit 와 같은가. doctor 가 늘 내는 키가 빠지거나 요구 · 프리셋 절이 비면
# 견줄 것이 없는 것이므로 실패다(이 하네스의 시험 서버에는 프리셋이 하나 있다). 다르면 어긋난
# 행을 한 줄씩 찍고 1
doctor_same() { # doctor_same <human file> <json file> <human rc> <json rc>
  "$PY" -I - "$@" "$DOCTOR_KEYS" <<'EOF'
import json, re, sys
human, doc_path, human_rc, json_rc, keys = sys.argv[1:6]
lines = open(human, encoding="utf-8").read().splitlines()
doc = json.load(open(doc_path, encoding="utf-8"))
bad = []
missing = [k for k in keys.split() if k not in doc]
if missing:
    bad.append(f"--json lacks keys doctor always writes: {', '.join(missing)}")
for key in ("rcm_requirements", "presets"):
    if not isinstance(doc.get(key), list) or not doc.get(key):
        bad.append(f"--json {key} is empty or not a list — nothing to compare")
if not isinstance(doc.get("service"), dict):
    bad.append("--json service is not an object")
# JSON 의 상태 → 표의 첫 칸(docs/operating.md 의 doctor 표와 같은 낱말)
NEED = {"ok": "ok", "fail": "FAIL", "warn": "warn", "info": "info", "unknown": "?"}
STATE = {"ready": "ready", "needs_check": "needs check", "missing": "missing", "unknown": "unknown"}
APPLIES = {"other_pool": "other pool", "not_coming": "not coming", "local_lane_only": "local lane"}

def section(title):
    rows, on = [], False
    for line in lines:
        if line == title:
            on = True
            continue
        if on:
            if not line.startswith("  "):
                break
            rows.append(re.split(r"\s{2,}", line.strip()))
    return rows

def compare(title, want):
    rows = section(title)
    got = {}
    for cells in rows:
        if len(cells) >= 2:
            got.setdefault(cells[1], []).append(cells[0])
    for name, mark in want.items():
        if got.get(name) != [mark]:
            bad.append(f"{title}: {name} is {mark} in --json, {got.get(name) or 'absent'} in the table")
    extra = sorted(set(got) - set(want))
    if extra:
        bad.append(f"{title}: rows only in the table: {', '.join(extra)}")

needs = {r["name"]: NEED.get(str(r.get("state")), "?") for r in doc.get("rcm_requirements") or []}
if needs:
    compare("rcm's own needs", needs)
presets = {}
for p in doc.get("presets") or []:
    if p.get("applies") in APPLIES:
        presets[p["name"]] = APPLIES[p["applies"]]
    else:
        presets[p["name"]] = STATE.get(p.get("state"), "—") if p.get("state") is not None else "—"
if presets:
    compare("presets", presets)
# 프로브는 서버가 정의할 때만 있다 — 비어 있으면 표에도 그 절이 없어야 한다
probes = {n: str(p.get("state")) for n, p in (doc.get("probes") or {}).items()}
if probes:
    compare("probes", probes)
elif "probes" in lines:
    bad.append("probes: the table has the section, --json has none")
s = doc.get("summary")
if not isinstance(s, dict):
    bad.append("--json summary is missing or not an object")
else:
    want = (
        f"summary  {s.get('ready')} ready · {s.get('needs_check')} needs check · "
        f"{s.get('missing')} missing · {s.get('unknown')} unknown"
    )
    if s.get("applies_unknown"):
        want += f" · {s['applies_unknown']} may come (unknown)"
    if want not in lines:
        bad.append(f"summary: --json says '{want}', the table does not")
for key, mark in (("warnings", "warn  "), ("problems", "FAIL  "), ("undecided", "?     ")):
    for item in doc.get(key) or []:
        if f"{mark}{item}" not in lines:
            bad.append(f"{key}: '{item}' is in --json, not in the table")
path =(doc.get("service") or {}).get("path")
if not path:
    bad.append("--json has no measured PATH (service.path is null)")
elif f"PATH     {path}" not in lines:
    bad.append("the table does not show the measured PATH of --json")
code = doc.get("exit")
if f"exit {code}" not in lines:
    bad.append(f"--json exit is {code}, the table does not end with it")
if human_rc != str(code) or json_rc != str(code):
    bad.append(f"exit codes: table run {human_rc}, --json run {json_rc}, document {code}")
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
EOF
}

# TD24 의 PASS 줄: 무엇을 쟀는지 말한다 — 서비스 파일이 없으면 기본 워커 PATH 를 잰 것이고, 명세
# 행의 「실제 워커 서비스 환경」을 잰 것이 아니다
td24_pass_text() { # td24_pass_text <json file>
  "$PY" -I - "$1" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
s = d.get("service") or {}
where = (
    f"service file {s['file']} measured"
    if s.get("file")
    else "no service file — default worker PATH measured"
)
print(
    f"PASS ({where}) table and --json agree row by row, exit {d.get('exit')}, "
    f"measured PATH {s.get('path')}"
)
EOF
}

# 결정 352 의 판정: 종료 1, 거절 문장, 운영 파일 mtime 그대로, 시험 쪽에도 쓴 파일 없음
d352_verdict() { # d352_verdict <rc> <stderr file> <mtime before> <mtime after> <files left>
  local rc=$1 err=$2 before=$3 after=$4 left=$5
  if [ "$rc" != 1 ]; then
    echo "FAIL setup exited $rc on the production config, not 1"
  elif ! grep -q 'refusing (nothing was written)' "$err"; then
    echo "FAIL setup exited 1 without the production refusal: $(head -1 "$err")"
  elif [ "$before" != "$after" ]; then
    echo "FAIL the production config's mtime changed ($before -> $after)"
  elif [ "$left" != 0 ]; then
    echo "FAIL setup left $left file(s) in the test folder"
  else
    echo "PASS exit 1, refused as production, config mtime unchanged, nothing written"
  fi
}

# TN8 의 판정: 기계는 배너를 볼 수 없다. osascript 가 0 으로 끝났고 경고 줄이 없으면 UNKNOWN
# (사람이 봐야 한다), 경고 · 0 아닌 종료 · 불리지 않음 · wait 실패는 FAIL
tn8_verdict() { # tn8_verdict <wait rc> <wait stderr file> <osascript log>
  local rc=$1 err=$2 log=$3 os
  os=$(sed -n 's/^exit //p' "$log" 2>/dev/null | tail -1)
  if [ "$rc" != 0 ]; then
    echo "FAIL rcm wait --notify exited $rc"
  elif grep -q 'warning: --notify' "$err"; then
    echo "FAIL $(grep 'warning: --notify' "$err" | sed -n 1p)"
  elif [ -z "$os" ]; then
    echo "FAIL osascript was not called"
  elif [ "$os" != 0 ]; then
    echo "FAIL osascript exited $os"
  else
    echo "UNKNOWN banner needs a person (rcm wait 0, osascript exit 0, no warning; banner not observed)"
  fi
}

# ── 실행 ───────────────────────────────────────────────────────────────────

# 운영과 떼어 놓는다: 셸의 RCM_* 를 모두 지우고 HOME 과 XDG 네 폴더를 시험 폴더 안으로 둔다.
# 이 뒤의 rcm 명령은 모두 이 환경을 물려받는다(시험이 source 뒤 불러 확인한다)
isolate_env() { # isolate_env <base>
  local b=$1 v
  for v in $(env | sed -n 's/^\(RCM_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done
  export HOME="$b/home"
  export XDG_CONFIG_HOME="$b/xdg/config" XDG_DATA_HOME="$b/xdg/data"
  export XDG_STATE_HOME="$b/xdg/state" XDG_CACHE_HOME="$b/xdg/cache"
}

main() {
  local real_home=${HOME:-} v
  case "$real_home" in /?*) ;; *) die "HOME must be an absolute path other than / ($real_home)" ;; esac

  # 운영 설정 경로는 이 변수로만 받는다(launchd 에서 찾지 않는다) — isolate_env 가 RCM_* 를 지우기 전에
  PROD_CONFIG=${RCM_LIVE_PROD_CONFIG:-}

  # 포트와 시험 폴더 검사가 도구 검사보다 먼저다 — 어느 쪽이든 아무것도 만들기 전에 멈춘다
  P1=${M5T_PORT:-8794}
  P2=${M5T_PORT2:-$((P1 + 1))}
  is_test_port "$P1" || die "M5T_PORT $P1 is not a test port (8790-8899)"
  is_test_port "$P2" || die "M5T_PORT2 $P2 is not a test port (8790-8899)"
  [ "$P1" != "$P2" ] || die "the two ports must differ"
  for v in "$P1" "$P2"; do
    if lsof -nP -iTCP:"$v" -sTCP:LISTEN >/dev/null 2>&1; then die "port $v is in use"; fi
  done

  [ -x "$PY" ] || die "no $PY"
  RID=$("$PY" -I -c 'import secrets; print(secrets.token_hex(4))')
  if [ -n "${M5T_BASE:-}" ]; then
    B=$M5T_BASE
  else
    B="$real_home/.local/share/rcm-devtest/m5t-live-$RID"
  fi
  is_test_base "$B" "$real_home" || die "base $B is not ~/.local/share/rcm-devtest/<name> or ~/.local/share/rcm-devtest-<name>"
  if is_inside "$B" /tmp || is_inside "$B" /private/tmp; then die "base $B is under /tmp"; fi
  [ ! -e "$B" ] || die "$B exists — pick a new name (this harness never reuses or deletes a folder)"
  [ -x "$RCM" ] || die "no $RCM — make the worktree's .venv first"
  [ "$(uname -s)" = Darwin ] || die "this harness drives launchd: macOS only"
  command -v launchctl >/dev/null || die "no launchctl"
  mkdir -p "$(dirname "$B")"
  mkdir "$B" || die "cannot create $B"
  REAL_HOME=$real_home
  LABEL="$TEST_LABEL_PREFIX$RID"
  is_test_label "$LABEL" || die "made a label that is not a test label ($LABEL)"
  U=$(id -u)
  OUT="$B/out"
  mkdir -p "$OUT" "$B/home" "$B/xdg/config" "$B/xdg/data" "$B/xdg/state" "$B/xdg/cache" "$B/bin"
  : >"$OUT/summary.txt"
  FAILS=0
  BOOTED=""
  BG_PIDS=()

  isolate_env "$B"
  cd "$B/home"
  env | grep -E '^(RCM_|XDG_|HOME=)' | sed "s|$REAL_HOME|<HOME>|g" | sort >"$OUT/env.txt" || true
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  log "base <HOME>${B#"$REAL_HOME"} · label $LABEL · ports $P1 $P2"

  run_d352
  run_tr16
  run_tl12
  run_td24
  run_tn8
  run_td25
  dump_jobs

  cleanup
  trap - EXIT
  log "done · $FAILS failed · results in $OUT"
  [ "$FAILS" = 0 ]
}

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$OUT/timeline.txt" >&2; }

verdict() { # verdict <id> <PASS|FAIL|UNKNOWN> <text>
  printf '%-5s %-7s %s\n' "$1" "$2" "$3" | sed "s|$REAL_HOME|<HOME>|g" | tee -a "$OUT/summary.txt"
  [ "$2" != FAIL ] || FAILS=$((FAILS + 1))
}

# `PASS 글` 꼴의 한 줄을 verdict 로
verdict_line() { # verdict_line <id> <line>
  verdict "$1" "${2%% *}" "${2#* }"
}

# 시험 라벨만 내린다 — 내리기 전에 라벨을 다시 확인한다
bootout_test_label() {
  [ -n "$BOOTED" ] || return 0
  if is_test_label "$LABEL"; then
    launchctl bootout "gui/$U/$LABEL" >>"$OUT/launchctl.txt" 2>&1 || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      launchctl print "gui/$U/$LABEL" >/dev/null 2>&1 || break
      sleep 0.5
    done
    if is_test_path "$INSTALLED" "$B" "$REAL_HOME"; then rm -f "$INSTALLED"; fi
  fi
  BOOTED=""
}

stop_pid() { # stop_pid <pid>
  local p=$1
  kill -TERM "$p" 2>/dev/null || return 0
  for _ in $(seq 1 50); do
    kill -0 "$p" 2>/dev/null || break
    sleep 0.2
  done
  kill -KILL "$p" 2>/dev/null || true
  wait "$p" 2>/dev/null || true
}

cleanup() {
  local p
  for p in ${BG_PIDS[@]+"${BG_PIDS[@]}"}; do stop_pid "$p"; done
  BG_PIDS=()
  bootout_test_label
}

wait_http() { # wait_http <url> <seconds>
  local i code
  for i in $(seq 1 $(($2 * 5))); do
    code=$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' "$1/api/health" 2>/dev/null || true)
    case "$code" in 2?? | 5??) return 0 ;; esac
    sleep 0.2
  done
  return 1
}

# 잡 하나의 칸: jf <url> <token file> <id> <key>...
# 토큰은 curl 의 argv 에 두지 않는다(ps 에 보인다). 토큰 파일 옆의 600 헤더 파일을 `-H @파일` 로 준다 —
# printf 는 셸 내장이라 프로세스 목록에 토큰이 나오지 않는다
jf() {
  local url=$1 tok=$2 id=$3 hdr="$2.header"
  shift 3
  if [ ! -s "$hdr" ]; then
    (umask 077 && printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' <"$tok")" >"$hdr")
  fi
  curl --noproxy '*' -fs -H @"$hdr" "$url/jobs/$id" | "$PY" -I -c '
import json, sys
d = json.load(sys.stdin)
print(" ".join(str(d.get(k)) for k in sys.argv[1:]))' "$@"
}

# rcm run --no-wait 의 stdout JSON 에서 잡 번호를 꺼내고 cancel_token 을 뺀 사본을 남긴다
submitted() { # submitted <raw json> <redacted copy>
  printf '%s' "$1" | "$PY" -I -c '
import json, sys
d = json.loads(sys.stdin.read())
d.pop("submission", None)
json.dump(d, open(sys.argv[1], "w"), indent=1)
print(d["job_id"])' "$2"
}

new_token() { # new_token <server.toml> <file> <name> [--worker]
  local conf=$1 file=$2
  shift 2
  (umask 077 && "$RCM" token --config "$conf" add "$@" 2>>"$OUT/token.err" | tail -1 >"$file")
  [ -s "$file" ]
}

# ── 결정 352: setup 이 운영 설정 자리를 거절한다 ─────────────────────────────

run_d352() {
  if [ -z "$PROD_CONFIG" ]; then
    verdict D352 UNKNOWN "RCM_LIVE_PROD_CONFIG not set (the harness does not look the production config up)"
    return 0
  fi
  if ! plain_abs "$PROD_CONFIG" || [ ! -f "$PROD_CONFIG" ] || is_inside "$PROD_CONFIG" "$B"; then
    verdict D352 UNKNOWN "RCM_LIVE_PROD_CONFIG is not an absolute path to an existing file outside the test folder"
    return 0
  fi
  local d="$B/d352" before after rc=0 left
  mkdir -p "$d"
  before=$(stat -f %m "$PROD_CONFIG")
  "$RCM" setup --role coordinator-worker --config "$PROD_CONFIG" --port "$P1" \
    --data-dir "$d/data" --label "${LABEL}p" --service-file "$d/svc/${LABEL}p.plist" \
    --bind 127.0.0.1 --no-advertise --yes >"$OUT/d352.out" 2>"$OUT/d352.err" || rc=$?
  after=$(stat -f %m "$PROD_CONFIG")
  left=$(find "$d" -type f | wc -l | tr -d ' ')
  log "D352 rc $rc · mtime $before -> $after · files left $left"
  verdict_line D352 "$(d352_verdict "$rc" "$OUT/d352.err" "$before" "$after" "$left")"
}

# ── TR16: setup → 시험 값 확인 → 출력된 명령으로 bootstrap → 토큰 → rcm check ──

run_tr16() {
  local conf="$XDG_CONFIG_HOME/rcm/server.toml" staged="$XDG_CONFIG_HOME/rcm/service/$LABEL.plist"
  local url="http://127.0.0.1:$P1" rc=0 why logp
  INSTALLED="$HOME/Library/LaunchAgents/$LABEL.plist"
  logp="$HOME/Library/Logs/rcm/$LABEL.log"
  log "TR16 rcm setup --role coordinator-worker --port $P1 --label $LABEL"
  "$RCM" setup --role coordinator-worker --port "$P1" --data-dir "$B/data" --label "$LABEL" \
    --bind 127.0.0.1 --no-advertise --yes >"$OUT/tr16-setup.out" 2>"$OUT/tr16-setup.err" || rc=$?
  if [ "$rc" != 0 ]; then
    verdict TR16 FAIL "setup exited $rc: $(grep -v '^warning' "$OUT/tr16-setup.err" | sed -n 1p)"
    return 0
  fi
  # bootstrap 전 검사 — 하나라도 아니면 띄우지 않는다
  why=$(check_server_toml "$conf" "$P1" "$B/data" "$B" "$REAL_HOME") || {
    verdict TR16 FAIL "not started: $why"
    return 0
  }
  why=$(check_plist "$staged" "$LABEL" "$conf" "$HOME" "$RCM" "$B" "$REAL_HOME") || {
    verdict TR16 FAIL "not started: $why"
    return 0
  }
  is_test_path "$INSTALLED" "$B" "$REAL_HOME" || {
    verdict TR16 FAIL "not started: install place $INSTALLED is not in the test folder"
    return 0
  }
  [ "$(_field StandardOutPath "$(_fields plist "$staged")")" = "$logp" ] || {
    verdict TR16 FAIL "not started: the plist log is not $logp"
    return 0
  }
  # setup 이 찍은 명령이 이 값들로 만든 명령과 글자까지 같은가 — 같을 때만 그 명령을 그대로 돌린다
  local l1="mkdir -p $(dirname "$logp") $(dirname "$INSTALLED")"
  local l2="cp -n $staged $INSTALLED"
  local l3="cmp -s $staged $INSTALLED && launchctl bootstrap gui/\$(id -u) $INSTALLED || echo \"not started:"
  local t1="$RCM token --config $conf add CLIENT_NAME"
  local need
  for need in "  $l1" "  $l2" "  $t1"; do
    grep -qxF -- "$need" "$OUT/tr16-setup.err" || {
      verdict TR16 FAIL "setup did not print: ${need# }"
      return 0
    }
  done
  grep -qF -- "  $l3" "$OUT/tr16-setup.err" || {
    verdict TR16 FAIL "setup did not print the start line for $INSTALLED"
    return 0
  }
  if launchctl print "gui/$U/$LABEL" >/dev/null 2>&1; then
    verdict TR16 FAIL "not started: label $LABEL is already loaded"
    return 0
  fi
  log "TR16 checks passed; running the printed install lines"
  mkdir -p "$(dirname "$logp")" "$(dirname "$INSTALLED")"
  cp -n "$staged" "$INSTALLED" || true
  BOOTED=1
  if ! { cmp -s "$staged" "$INSTALLED" && launchctl bootstrap "gui/$U" "$INSTALLED" >>"$OUT/launchctl.txt" 2>&1; }; then
    verdict TR16 FAIL "launchctl bootstrap failed: $(tail -1 "$OUT/launchctl.txt")"
    bootout_test_label
    return 0
  fi
  if ! wait_http "$url" 30; then
    cp "$logp" "$OUT/tr16-service.log" 2>/dev/null || true
    verdict TR16 FAIL "bootstrapped, but nothing answers on $url (service log in out/tr16-service.log)"
    bootout_test_label
    return 0
  fi
  launchctl print "gui/$U/$LABEL" 2>/dev/null | grep -E '^[[:space:]]*(state|pid|path) =' >"$OUT/tr16-launchd.txt" || true
  sed "s|$REAL_HOME|<HOME>|g" "$OUT/tr16-launchd.txt" >"$OUT/tr16-launchd.redacted.txt"
  mv "$OUT/tr16-launchd.redacted.txt" "$OUT/tr16-launchd.txt"
  log "TR16 server up under launchd ($(tr '\n' ' ' <"$OUT/tr16-launchd.txt"))"
  # 출력된 토큰 명령 — 같은 시험 빌드로, 이름만 채운다
  if ! new_token "$conf" "$B/tr16-client.token" m5t-live-client; then
    verdict TR16 FAIL "the printed token command failed: $(tail -1 "$OUT/token.err")"
    bootout_test_label
    return 0
  fi
  rc=0
  RCM_TOKEN=$(cat "$B/tr16-client.token") "$RCM" --no-follow check --server "$url" \
    >"$OUT/tr16-check.txt" 2>&1 || rc=$?
  local fails
  fails=$(grep -c '^FAIL' "$OUT/tr16-check.txt" || true)
  bootout_test_label
  local still=no
  if launchctl print "gui/$U/$LABEL" >/dev/null 2>&1; then still=yes; fi
  log "TR16 rcm check exit $rc · FAIL lines $fails · label still loaded: $still"
  if [ "$rc" = 0 ] && [ "$fails" = 0 ] && [ "$still" = no ]; then
    verdict TR16 PASS "test values checked before bootstrap, served on $url by $LABEL, rcm check 0 with no FAIL, only $LABEL booted out"
  else
    verdict TR16 FAIL "rcm check exit $rc, FAIL lines $fails, label still loaded: $still (out/tr16-check.txt)"
  fi
}

# ── TL12: lanes = 0 서버 + 일회용 워커, 기본 풀 프리셋을 끝까지 ─────────────────

run_tl12() {
  T="$B/tl12"
  URL2="http://127.0.0.1:$P2"
  mkdir -p "$T/data" "$T/worker-data" "$T/src"
  cat >"$T/server.toml" <<EOF
[server]
bind = "127.0.0.1"
port = $P2
data_dir = "$T/data"
lanes = 0
advertise = false

[[presets]]
name = "m5t-echo"
description = "Fake preset: one step marker, exit 0"
argv = ["sh", "-c", "echo ::rcm::step::echo; echo m5t-echo ran; exit 0"]
timeout_seconds = 60
EOF
  local why
  why=$(check_server_toml "$T/server.toml" "$P2" "$T/data" "$B" "$REAL_HOME") || die "TL12 config: $why"
  "$RCM" serve --config "$T/server.toml" >>"$OUT/tl12-serve.log" 2>&1 &
  BG_PIDS+=($!)
  if ! wait_http "$URL2" 20; then
    verdict TL12 FAIL "the lanes = 0 server did not come up (out/tl12-serve.log)"
    TL12_JOB=""
    return 0
  fi
  log "TL12 lanes = 0 server up on $URL2"
  new_token "$T/server.toml" "$T/client.token" m5t-tl12-client || die "TL12 client token failed"
  new_token "$T/server.toml" "$T/worker.token" m5t-tl12-worker --worker || die "TL12 worker token failed"
  # 사람이 옮긴 토큰: 코디네이터에서 만든 워커 토큰을 worker.toml 의 token 에 적는다
  (
    umask 077
    printf 'server = "%s"\npool = "default"\nlanes = 1\ndata_dir = "%s"\ntoken = "%s"\n' \
      "$URL2" "$T/worker-data" "$(cat "$T/worker.token")" >"$T/worker.toml"
  )
  "$RCM" --no-follow worker --config "$T/worker.toml" >>"$OUT/tl12-worker.log" 2>&1 &
  BG_PIDS+=($!)
  (cd "$T/src" && git init -q -b main && echo m5t >README &&
    git -c user.name=acceptance -c user.email=acceptance@example.invalid add README &&
    git -c user.name=acceptance -c user.email=acceptance@example.invalid commit -qm init) ||
    die "TL12 git repo failed"
  local raw rc=0
  raw=$(cd "$T/src" && RCM_TOKEN=$(cat "$T/client.token") "$RCM" --no-follow run m5t-echo \
    --server "$URL2" --by acceptance --no-wait 2>"$OUT/tl12-submit.err") || rc=$?
  if [ "$rc" != 0 ]; then
    verdict TL12 FAIL "rcm run exited $rc: $(tail -1 "$OUT/tl12-submit.err")"
    TL12_JOB=""
    return 0
  fi
  TL12_JOB=$(submitted "$raw" "$OUT/tl12-submit.json")
  log "TL12 job #$TL12_JOB submitted"
  rc=0
  RCM_TOKEN=$(cat "$T/client.token") "$RCM" --no-follow wait --job "$TL12_JOB" --server "$URL2" \
    --timeout 120 >"$OUT/tl12-wait.json" 2>"$OUT/tl12-wait.err" || rc=$?
  local row end
  row=$(jf "$URL2" "$T/client.token" "$TL12_JOB" state worker ran_on)
  # 끝 줄은 `#N <state> on <machine> · <걸린 시간>` 이다(앞의 `#N queued …` · `#N succeeded` 줄이 아니다)
  end=$(grep -E "^#$TL12_JOB [a-z_]+ on " "$OUT/tl12-wait.err" | tail -1 || true)
  log "TL12 wait exit $rc · job $row · end line: $end"
  local end_ok=0
  case "$end" in "#$TL12_JOB succeeded on m5t-tl12-worker"*) end_ok=1 ;; esac
  if [ "$rc" = 0 ] && [ "$row" = "succeeded m5t-tl12-worker m5t-tl12-worker" ] && [ "$end_ok" = 1 ]; then
    verdict TL12 PASS "#$TL12_JOB on a lanes = 0 server ran on m5t-tl12-worker (worker = ran_on), rcm wait 0, end line: $end"
  else
    verdict TL12 FAIL "#$TL12_JOB state/worker/ran_on = $row, rcm wait $rc, end line: ${end:-none}"
  fi
}

# ── TD24: 시험 워커 환경의 rcm doctor --role worker, 표와 --json ──────────────

run_td24() {
  local hr=0 jr=0 why
  if [ ! -f "${T:-}/worker.toml" ]; then
    verdict TD24 UNKNOWN "no test worker (TL12 did not set it up)"
    return 0
  fi
  "$RCM" --no-follow doctor --role worker --config "$T/worker.toml" >"$OUT/td24-doctor.txt" 2>&1 || hr=$?
  "$RCM" --no-follow doctor --role worker --config "$T/worker.toml" --json \
    >"$OUT/td24-doctor.json" 2>"$OUT/td24-doctor.err" || jr=$?
  log "TD24 doctor exit: table $hr · --json $jr"
  if why=$(doctor_same "$OUT/td24-doctor.txt" "$OUT/td24-doctor.json" "$hr" "$jr" 2>&1); then
    verdict_line TD24 "$(td24_pass_text "$OUT/td24-doctor.json")"
  else
    verdict TD24 FAIL "$(printf '%s' "$why" | tr '\n' ';' | cut -c1-400)"
  fi
}

# ── TN8: rcm wait --notify ──────────────────────────────────────────────────

run_tn8() {
  if [ -z "${TL12_JOB:-}" ]; then
    verdict TN8 UNKNOWN "no test server with a worker to run a job on (TL12 did not get that far)"
    return 0
  fi
  # 진짜 osascript 를 부르고 종료 코드만 적는 감싸개 — rcm 은 PATH 에서 osascript 를 찾는다
  cat >"$B/bin/osascript" <<EOF
#!/bin/sh
/usr/bin/osascript "\$@"
rc=\$?
echo "exit \$rc" >> "$OUT/tn8-osascript.log"
exit \$rc
EOF
  chmod 755 "$B/bin/osascript"
  : >"$OUT/tn8-osascript.log"
  local raw rc=0 job
  raw=$(cd "$T/src" && RCM_TOKEN=$(cat "$T/client.token") "$RCM" --no-follow run m5t-echo \
    --server "$URL2" --by acceptance --no-wait --no-join 2>"$OUT/tn8-submit.err") || rc=$?
  if [ "$rc" != 0 ]; then
    verdict TN8 FAIL "rcm run exited $rc: $(tail -1 "$OUT/tn8-submit.err")"
    return 0
  fi
  job=$(submitted "$raw" "$OUT/tn8-submit.json")
  rc=0
  PATH="$B/bin:$PATH" RCM_TOKEN=$(cat "$T/client.token") "$RCM" --no-follow wait --job "$job" \
    --server "$URL2" --timeout 120 --notify >"$OUT/tn8-wait.json" 2>"$OUT/tn8-wait.err" || rc=$?
  log "TN8 job #$job · wait --notify exit $rc · osascript: $(tr '\n' ' ' <"$OUT/tn8-osascript.log")"
  verdict_line TN8 "$(tn8_verdict "$rc" "$OUT/tn8-wait.err" "$OUT/tn8-osascript.log")"
}

# ── TD25: Docker 가 있을 때만 Linux 컨테이너에서 rcm doctor --role worker ─────

run_td25() {
  if ! command -v docker >/dev/null 2>&1 ||
    ! perl -e 'alarm 15; exec @ARGV' docker info >/dev/null 2>&1; then
    verdict TD25 UNKNOWN "docker unavailable"
    return 0
  fi
  local image=${M5T_DOCKER_IMAGE:-python:3.11-slim} rc=0
  mkdir -p "$B/wheel"
  if ! "$PY" -m pip wheel --quiet --no-deps --no-build-isolation -w "$B/wheel" "$M5T_ROOT" \
    >"$OUT/td25-wheel.txt" 2>&1; then
    verdict TD25 UNKNOWN "could not build the worktree wheel without the network (out/td25-wheel.txt)"
    return 0
  fi
  docker run --rm -v "$B/wheel:/w:ro" "$image" sh -c \
    'pip install --quiet --no-index /w/*.whl >/dev/null 2>&1 && rcm doctor --role worker --offline --json' \
    >"$OUT/td25-doctor.json" 2>"$OUT/td25-docker.err" || rc=$?
  local got
  if ! got=$("$PY" -I - "$OUT/td25-doctor.json" <<'EOF'
import json, re, sys
d = json.load(open(sys.argv[1]))
host = d.get("host") or {}
text = json.dumps(d)
print(host.get("package_manager"), host.get("root"), int(bool(re.search(r"\bsudo\s", text))))
EOF
  ); then
    verdict TD25 UNKNOWN "the container gave no doctor JSON (exit $rc, out/td25-docker.err)"
    return 0
  fi
  local pm root sudo
  read -r pm root sudo <<<"$got"
  log "TD25 $image · doctor exit $rc · package manager $pm · root $root · sudo in advice $sudo"
  if [ "$pm" != None ] && [ "$root" = True ] && [ "$sudo" = 0 ]; then
    verdict TD25 PASS "$image: package manager $pm, root seen, no sudo in the advice (doctor exit $rc)"
  else
    verdict TD25 FAIL "$image: package manager $pm, root $root, sudo in the advice $sudo"
  fi
}

# ── 잡의 마지막 행 ──────────────────────────────────────────────────────────

dump_jobs() {
  if [ -z "${URL2:-}" ] || [ ! -s "${T:-}/client.token" ]; then
    echo "[]" >"$OUT/jobs.json"
    return 0
  fi
  "$PY" -I - "$URL2" "$T/client.token" "$OUT/jobs.json" <<'EOF'
import json, sys, urllib.request
url, token_file, dest = sys.argv[1:4]
token = open(token_file).read().strip()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
rows = []
for n in range(1, 40):
    req = urllib.request.Request(f"{url}/jobs/{n}", headers={"Authorization": f"Bearer {token}"})
    try:
        rows.append(json.load(opener.open(req, timeout=5)))
    except Exception:
        break
keys = ("id", "preset", "state", "created_at", "started_at", "finished_at", "exit_code",
        "summary", "worker", "ran_on", "requester")
json.dump([{k: r.get(k) for k in keys} for r in rows], open(dest, "w"), indent=1)
EOF
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
