#!/usr/bin/env bash
# M5t-b 실기 — docs/m5tb-workplan.md §5 PR4 행 · §7.1 의 실기 확인 대상(RL1 · RL2 · RL5 · RL8 · RL10 ·
# RL15 · RL18 · RL28 · RL29 · RL34 · RL40 · RL42 · RL43 · RL45 · RL50 · RL51)과 감독자 값(F78)을 진짜
# `rcm serve` · `rcm worker` 프로세스, 표준 라이브러리 TCP 중계(tests/tcp_relay.py), 가짜 `sh -c` 프리셋으로
# 돌린다. pytest 가 부르지 않는다(순수 함수와 판정은 tests/test_acceptance_m5tb_live.py 가 source 해서 부른다).
#
#   bash scripts/acceptance/m5tb_live.sh --list         # 단계 표(§7.1 의 행 ID 전부 → 확인 방법)
#   bash scripts/acceptance/m5tb_live.sh --self-test    # 잡 없이 가드 · 서버 · 중계 · 워커 · 정리만(약 15초)
#   bash scripts/acceptance/m5tb_live.sh                # 전부(약 25~30분 — G 만료를 실제로 기다린다)
#   bash scripts/acceptance/m5tb_live.sh --only RL43,RL45
#   M5TB_PORT=8797 bash scripts/acceptance/m5tb_live.sh
#
# 지키는 것(.claude/skills/_shared/test-server.md · .claude/skills/live-verify/SKILL.md §1):
#   - 이 체크아웃의 .venv/bin/rcm 만 쓴다. 운영(8787 · ~/.config/rcm · ~/.local/share/rcm ·
#     launchd com.remote-ci-monitor.* · 서비스 venv)은 건드리지 않는다. launchd 는 아예 쓰지 않는다.
#   - 시험 폴더는 새로 만든다: ~/.local/share/rcm-devtest-m5tb/live-<무작위>. 이미 있는 폴더는 쓰지도
#     지우지도 않는다(rm -rf 없음). /tmp 아래는 거절한다.
#   - rcm 명령은 모두 HOME=<시험>/home 과 XDG 네 폴더(<시험>/xdg/…)로, 셸의 RCM_* 는 모두 지우고 돈다.
#     서버는 bind 127.0.0.1 · advertise = false, 클라이언트와 워커는 URL 을 명시한다.
#   - 신호는 이 하네스가 띄운 프로세스에만 보낸다: 명령줄에 시험 폴더가 들어 있고 시작 시각이 띄울 때
#     적어 둔 것과 같을 때만(서버 · 워커 · 중계). 잡 프로세스는 표지 파일의 pid 와 그 시작 시각이 같을 때만.
#   - 프리셋은 표지 · 종료 코드만 내는 가짜다. 알림 훅은 받은 환경을 시험 폴더의 파일에 적는 가짜 하나
#     (RL29)뿐이고 --self-test 에는 없다. 비밀(RL18)은 실행마다 새로 만든 값이다.
#   - 토큰은 600 파일에만 있고 명령의 argv 에 싣지 않는다(환경 변수 · 설정 파일로만 간다).
#
# 판정 줄(out/summary.txt): PASS · FAIL · UNKNOWN · SKIP. 모르면 PASS 가 아니라 UNKNOWN 이다(PLAN.md
# 「fail-open 금지」). SKIP 은 이 하네스가 일부러 돌리지 않는 몫(단위 시험 몫 · Linux 머신 없음)이다.
# 종료 코드: FAIL 이 하나라도 있으면 1, 시작 전 검사에서 멈추면 2, 아니면 0.
set -euo pipefail

M5TB_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RCM="$M5TB_ROOT/.venv/bin/rcm"
PY=${M5TB_PY:-$M5TB_ROOT/.venv/bin/python}
PROD_LABEL_PREFIX="com.remote-ci-monitor"
TEST_DIR_NAME="rcm-devtest-m5tb"
POOL="m5tb"
# 시험 서버 값(§7.5 · e2e 와 같다): G = disconnect_grace_seconds, T = worker_timeout_seconds,
# HB = worker_heartbeat_seconds. 하한 max(T, 2·HB + 50) + 35 = 87 바로 위다
G=90
T=10
HB=1
CLAIM_WAIT=2
GRACE_J=3   # 워커 grace_seconds — 표식 잡의 grace_J(상한 G − L − 15 − 10 = 13 안쪽)
BIG_GRACE=30 # RL34 「워커 grace 큰 값」 — 워커가 13 으로 줄인다고 한 줄 남긴다
RECENT_COUNT=100 # 웹 최근 목록 — 전체 실기가 끝내는 잡(서른 남짓)을 다 담는다(RL29-web)
LATE_SECONDS=210 # RL5: 일반 잡이 G 를 넘긴 분단 · 서버 재시작 · 복귀 뒤에 끝나도록

die() {
  echo "m5tb_live: $*" >&2
  exit 2
}

# ── 단계 표 — docs/m5tb-workplan.md §7.1 의 행 ID 전부 ────────────────────────────
# 칸: ID|종류|돌리는 함수(+ 로 여럿)|확인 방법. 종류 live = 이 하네스가 돌린다, e2e = pytest e2e 몫이라 되풀이하지
# 않는다, unit = 단위 시험 몫(주입 시계 · 주입 훅)이라 실기로 볼 수 없다. 시험이 §7.1 표와 견준다
steps_table() {
  cat <<'EOF'
RL1|live|run_partition_past_g|표식 잡 + 분단(중계 pause)이 G 를 넘김: G 전에 pid 소멸 · 그때 서버는 running · lost 한 번 · 종료 3 · step 없음 · worker_unreachable 이면 홀드 → 복귀 finish 가 품
RL2|live|run_server_stop_past_g|서버 정지가 G 를 넘김: 꺼진 동안 표식 pid 소멸 · 기동 직후 일반 잡 running · 표식 잡 lost 한 번(state 사건 1) · 종료 3
RL3|e2e|-|tests/test_e2e_worker_grace_m5tb.py test_rl1_rl5_rl42_rl3_a_partition_past_g
RL4|unit|-|주입 시계 경계 반복(tests/test_*_m5tb.py)
RL5|live|run_partition_past_g|일반 잡 + 분단 G 초과: lost(worker_unreachable) · 종료 3 · pid 생존 · 워커 로컬 log 증가 · 홀드 0 · 복귀 뒤 생존 · 끝난 뒤 late_result 한 번 · 상태 lost 그대로 · rcm wait 의 late result 줄
RL6|e2e|-|tests/test_e2e_worker_grace_m5tb.py test_rl6_* · tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL7|e2e|-|tests/test_e2e_worker_grace_m5tb.py test_rl7_*
RL8|live|run_cancel_in_partition|분단 중 rcm cancel 후 G 경과(b): cancelling · 표식 G 전 소멸 · 둘 다 lost(worker_unreachable) · 종료 3 · 복귀 뒤 일반 pid 소멸 · 표식 홀드 → 복귀가 품 · late_result 많아야 하나 · 상태 lost 그대로
RL9|unit|-|tests/test_*_m5tb.py(claim 응답 유실 흉내) — 이 하네스는 RL29 의 worker_restarted_without_job 을 만들 때만 중계로 흉내 낸다
RL10|live|run_fence|같은 토큰 둘째 프로세스: 첫 프로세스 종료 0 · 잡 pid 소멸 · lost(worker_replaced) · 종료 3 · 홀드 0 · 그 이름 등록 ≤ 2
RL11|unit|-|단위(HTTP) 응답 코드
RL12|e2e|-|tests/test_e2e_worker_grace_m5tb.py · tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL13|unit|-|단위(HTTP)
RL14|e2e|-|tests/test_e2e_worker.py kill -9 재시작 · tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL15|live|run_orphan|워커 kill -9 → 같은 폴더로 다시 시작: 일반 잡 고아를 claim 전에 끝냄 · 로그 줄 · 표식 잡은 감독자가 끝내 홀드 0 · 리더가 죽은 표식 그룹은 손대지 않고 홀드(닫힌 쪽) · 신원은 macOS ps / Linux /proc
RL16|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL17|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL18|live|run_secret|릴리스 비밀: 조각 경계 · 재전송(중계 끊기)에도 log.txt · rcm logs · SSE · 웹 로그 · 잡 문서에 원문 0 · log.txt = 가린 출력 바이트 그대로 · 종료 0
RL19|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL20|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2) · 웹 node 시험(PR3)
RL21|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL22|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL23|e2e|-|tests/test_e2e_worker_outbox_m5tb.py(PR2)
RL24|unit|-|v25 고정 DB
RL25|unit|-|가짜 옛 서버
RL26|unit|-|워커 시계 주입
RL27|unit|-|서버 now_fn · mono_fn
RL28|live|run_grace_view|유예 중(T~G): 사유 worker_unreachable_grace · finish_at null · running · rcm top · rcm eta --job 문장(원문 코드 없음) · /api/health 고정 · rcm wait --timeout 3 · 끊김/돌아옴 줄 지연 · 웹 띠 390px 80px 이하 · 두 언어 · 콘솔 오류 0 · 끊긴 행은 시간을 안 셈 · 회색 레인 · 막대에 시간 눈금 없음(듣는 행이 대조군) — RL28-cancel: 유예 중 취소한 잡은 top · eta · wait 가 「돌아오면 취소」, 기다리던 wait 은 「cancel requested」로 바로잡음, 돌아온 뒤 cancelled
RL29|live|run_claim_lost+run_lost_ends|다섯 lost 끝(worker_unreachable · worker_self_stopped · worker_replaced · worker_restarted_without_job · orphan_unverified): 종료 3 · rcm wait 의 lost 문장(core/outcome.py 가 그린 것) · 알림 훅 잡당 1회 · 웹 문장 · 콘솔 오류 0
RL30|unit|-|설정 로드 · reload
RL31|unit|-|ps · /proc 실패 주입 — 리더만 죽은 그룹은 RL15 가 실기로 본다
RL32|unit|-|가짜 서버
RL33|unit|-|두 세션 · 주입 시계
RL34|live|run_self_stop_timing|(a) heartbeat 가 매번 시한까지 매달림(중계 pause): 워커 grace 기본 · 큰 값 각각, 멈춤 ≤ 문턱 + grace_J + 3 · 멈춤 < G − 5 · lost(worker_self_stopped) · 종료 3 · 홀드 0 · 큰 grace 는 줄였다는 줄 — (b)~(f) 는 단위
RL35|e2e|-|tests/test_e2e_worker_grace_m5tb.py
RL36|unit|-|DB 바쁨 · OSError 주입
RL37|unit|-|파일 자르기 주입
RL38|unit|-|두 설정 · 한 데이터 폴더
RL39|unit|-|가짜 서버 응답 순서
RL40|live|run_finish_offline|연결 실패(중계 stop) 동안 잡이 끝남: 그동안 running · 복귀 뒤 succeeded · 종료 0 · 서버 log.txt sha256 = 워커 로컬 · log_complete true · 홀드 0 — 503 · 5xx 300초 몫은 단위
RL41|e2e|-|tests/test_e2e_worker_grace_m5tb.py test_rl41_*
RL42|live|run_partition_past_g|표식 잡 유예 만료: 홀드 행 1(cause worker_unreachable) · hold 사건 1 · 같은 그룹 대기 잡 queued/needs_action · claim 0 · 서버 재시작 뒤에도 · 다른 그룹 잡은 돎 · 일반 잡 홀드 0 · 복귀가 푼 뒤 대기 잡 시작 — 보존 기간 정리는 단위
RL43|live|run_supervisor_only+run_kill_worker|워커 kill -9: (a) SIGTERM 을 따르는 명령 ≤ 0.31초 (b) 무시하는 명령 ≤ 0.31 + grace_J (d) 감독자만 kill -9 → failed −9 · 홀드 0 · 취소 무변 (e) 일반 잡 생존 · G 뒤 lost + 홀드 · 종료 코드 7 · SIGKILL −9 · rcm release-hold 로 풀리고 대기 잡 시작 — (c) 는 단위
RL44|unit|-|단위(HTTP) — (a) 는 RL15 가 실기로 본다
RL45|live|run_kill_worker|열린 홀드 둘(대기 잡 있음 · 없음): needs_action · action.code · blocked_by · null ETA · /api/status · /api/health 2 항목 · ok 불변 · rcm wait 줄 · --return-on-action 4 · rcm check 경고 · doctor 줄 · --json 키 · 종료 코드 불변 · 홀드를 만든 잡의 rcm wait 줄 · 막힌 잡의 rcm eta 사유(N ahead 없음) · 웹 「확인이 필요한 작업」 4줄 이상 → 세 줄 + and N more(nothing else 없음) · client 403(2) · admin 0 · 홀드 없음 1 — RL45-empty: 빈 대기열 + 열린 홀드에서 rcm top · 웹이 「바로 시작」을 약속하지 않음
RL46|unit|-|주입 시계
RL47|unit|-|v25 고정 DB
RL48|unit|-|설정 로드 · store claim
RL49|e2e|-|tests/test_e2e_worker_grace_m5tb.py test_rl49_*
RL50|live|run_hb_order|claim 응답 전에 찍은 heartbeat 를 중계가 붙잡았다 늦게 놓음: X running · pid 생존 · 홀드 0 · 같은 그룹 둘째 잡 queued
RL51|live|run_sweep|SIGTERM 을 무시하는 자식이 남음: 워커 산 채 취소 · 워커 kill -9 · 명령이 grace 를 거의 다 쓰고 끝남, 각각 자식 소멸 시간 — (a) 는 단위(주입 훅)
RL52|unit|-|감독자 모듈
RL53|unit|-|설정 reload
RL54|unit|-|가짜 훅
RL55|unit|-|멈춤 주입
RL56|unit|-|트랜잭션 멈춤 주입
F78|live|run_supervisor_only+run_kill_worker|감독자 값 재측정: 부모 SIGKILL → SIGTERM 명령 소멸 · 무시 명령 소멸 − grace_J · 30초 잡 감독자 CPU · python -I 파일 경로 기동 · ps -A -o pid= -o pgid= 한 번 · 감독 아래 /usr/bin/true 의 끝 지연
EOF
}

# ── 순수 검사 함수(시험이 source 해서 부른다) ──────────────────────────────────

# 시험 포트인가: 8790~8899 의 숫자. 8787(운영) · 8788(다른 세션의 dev 서버)은 그 밖이다
is_test_port() { # is_test_port <port>
  case "${1:-}" in "" | *[!0-9]*) return 1 ;; esac
  [ "${#1}" -le 5 ] || return 1
  [ "$1" -ge 8790 ] && [ "$1" -le 8899 ]
}

# 운영 launchd 라벨인가(대소문자와 상관없이 com.remote-ci-monitor 이거나 그 아래)
is_prod_label() { # is_prod_label <label>
  local lower
  lower=$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')
  case "$lower" in "$PROD_LABEL_PREFIX" | "$PROD_LABEL_PREFIX".*) return 0 ;; esac
  return 1
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

# 시험 폴더로 받을 수 있는가: <home>/.local/share/rcm-devtest-m5tb/<이름 한 단계>. 그 밖(홈 · 운영
# data_dir · 설정 · /tmp · 상대 경로 · 두 단계)은 거절한다
is_test_base() { # is_test_base <base> <real home>
  local b=${1:-} h=${2:-} rest
  plain_abs "$b" || return 1
  plain_abs "$h" || return 1
  [ "$h" != / ] || return 1
  case "$b" in
    "$h/.local/share/$TEST_DIR_NAME/"?*) rest=${b#"$h/.local/share/$TEST_DIR_NAME/"} ;;
    *) return 1 ;;
  esac
  case "$rest" in */*) return 1 ;; esac
  return 0
}

# 시험 경로인가: 시험 폴더 안이고, 진짜 홈의 운영 자리(설정 · data_dir · LaunchAgents · 로그) 안이 아니다
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

# server.toml 의 [server] 칸과 알림 훅마다 argv 의 마지막 칸을 `키=값` 줄로
_toml_fields() { # _toml_fields <file>
  "$PY" -I - "$1" <<'EOF'
import sys, tomllib
with open(sys.argv[1], "rb") as fh:
    d = tomllib.load(fh)
s = d.get("server", {})
def show(v):
    if v is None:
        return "<missing>"
    return str(v).lower() if isinstance(v, bool) else str(v)
for k in ("port", "advertise", "data_dir", "bind"):
    print(f"{k}={show(s.get(k))}")
for rule in d.get("notify", []) or []:
    argv = rule.get("argv") or ["<missing>"]
    print(f"notify={argv[-1]}")
EOF
}

# 서버를 띄우기 전 검사: server.toml 의 port · advertise · data_dir · bind 가 시험 값이고, 알림 훅은
# 시험 폴더 안의 스크립트만 부른다. 틀리면 무엇이 틀렸는지 한 줄을 찍고 1
check_server_toml() { # check_server_toml <file> <port> <data_dir> <base> <real home>
  local f=$1 port=$2 data=$3 base=$4 home=$5 got v
  got=$(_toml_fields "$f" 2>/dev/null) || {
    echo "cannot read $f as TOML"
    return 1
  }
  v=$(printf '%s\n' "$got" | sed -n 's/^port=//p')
  { [ "$v" = "$port" ] && is_test_port "$v"; } || {
    echo "server.toml port is $v, not the test port $port"
    return 1
  }
  v=$(printf '%s\n' "$got" | sed -n 's/^advertise=//p')
  [ "$v" = false ] || {
    echo "server.toml advertise is $v, not false"
    return 1
  }
  v=$(printf '%s\n' "$got" | sed -n 's/^data_dir=//p')
  { [ "$v" = "$data" ] && is_test_path "$v" "$base" "$home"; } || {
    echo "server.toml data_dir is $v, not the test data_dir $data"
    return 1
  }
  v=$(printf '%s\n' "$got" | sed -n 's/^bind=//p')
  [ "$v" = 127.0.0.1 ] || {
    echo "server.toml bind is $v, not 127.0.0.1"
    return 1
  }
  for v in $(printf '%s\n' "$got" | sed -n 's/^notify=//p'); do
    is_test_path "$v" "$base" "$home" || {
      echo "server.toml notify hook $v is outside the test folder"
      return 1
    }
  done
  return 0
}

# 프로세스 신원 — 시작 시각(LC_ALL=C TZ=UTC 로 고정한 ps 출력). 없으면 빈 글
proc_start() { # proc_start <pid>
  LC_ALL=C TZ=UTC ps -o lstart= -p "${1:-0}" 2>/dev/null | sed 's/^ *//;s/ *$//' || true
}

# 이 하네스가 띄운 프로세스인가: 명령줄에 시험 폴더가 들어 있고 시작 시각이 적어 둔 것과 같다
is_our_proc() { # is_our_proc <pid> <base> <recorded start>
  local pid=${1:-} base=${2:-} rec=${3:-} cmd
  case "$pid" in "" | *[!0-9]*) return 1 ;; esac
  [ "$pid" -gt 1 ] || return 1
  [ "$pid" != "$$" ] || return 1
  [ -n "$base" ] && [ -n "$rec" ] || return 1
  cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
  case "$cmd" in *"$base"*) ;; *) return 1 ;; esac
  [ "$(proc_start "$pid")" = "$rec" ]
}

# <pid> 의 부모가 <parent> 인가(ps -o ppid=). 감독자는 워커가 직접 띄운 자식이다(runner.py Popen)
is_child_of() { # is_child_of <pid> <parent pid>
  local pid=${1:-} parent=${2:-} got
  case "$pid" in "" | *[!0-9]*) return 1 ;; esac
  case "$parent" in "" | *[!0-9]*) return 1 ;; esac
  [ "$pid" -gt 1 ] && [ "$parent" -gt 1 ] || return 1
  got=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  [ "$got" = "$parent" ]
}

# 잡 프로세스(표지 파일의 pid)인가: 시작 시각이 처음 읽을 때 적어 둔 것과 같다(pid 재사용을 거른다)
is_our_job_pid() { # is_our_job_pid <pid> <recorded start>
  local pid=${1:-} rec=${2:-}
  case "$pid" in "" | *[!0-9]*) return 1 ;; esac
  [ "$pid" -gt 1 ] && [ "$pid" != "$$" ] && [ -n "$rec" ] || return 1
  [ "$(proc_start "$pid")" = "$rec" ]
}

# 운영과 떼어 놓는다: 셸의 RCM_* 를 모두 지우고 HOME 과 XDG 네 폴더를 시험 폴더 안으로 둔다
isolate_env() { # isolate_env <base>
  local b=$1 v
  for v in $(env | sed -n 's/^\(RCM_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$v"; done
  export HOME="$b/home"
  export XDG_CONFIG_HOME="$b/xdg/config" XDG_DATA_HOME="$b/xdg/data"
  export XDG_STATE_HOME="$b/xdg/state" XDG_CACHE_HOME="$b/xdg/cache"
}

# 하네스가 기대하는 키 — 시험이 진짜 생산자(코드)와 견준다. PR2 · PR3 의 키는 따로 둔다
# 끝난 잡 문서(core/status.py recent_json)
JOB_KEYS="state exit_code summary_code summary_args failed_step last_step started_at finished_at worker ran_on"
# 대기 · 도는 잡 문서(core/status.py queue_row_json)
QUEUE_KEYS="state reason blocked_by estimate started_at ran_on action"
# 잡 문서의 hold(core/status.py hold_doc_json) · /api/status 와 /api/health 의 irreversible_holds 항목(hold_item_json)
HOLD_DOC_KEYS="cause released_at release_kind released_by release_reason"
HOLD_ITEM_KEYS="job_id preset group scope worker cause"
# 다섯 lost 끝(core/outcome.py CODES)
LOST_CODES="worker_unreachable worker_self_stopped worker_replaced worker_restarted_without_job orphan_unverified"
# 시험 DB 의 칸(store.py 스키마) — 읽기 전용으로만 연다
DB_COLUMNS="jobs.state jobs.summary_code jobs.finished_at irreversible_holds.job_id irreversible_holds.cause irreversible_holds.released_at events.job_id events.kind events.payload worker_sessions.worker_name"
# 알림 훅이 적는 환경(core/notify.py)
NOTIFY_ENV="RCM_JOB_ID RCM_STATE RCM_EVENT RCM_HOLD"
# 워커 로그 줄(remote_worker.py) — 판정이 찾는 글
WORKER_LINES="no answer from the server for|lowered to|orphan #|stopped process group|leader gone but its group is alive|claimed #"
# PR2(#286) · PR3(#285) 의 키와 줄 — 그 PR 이 머지되기 전 dev 에는 없다. 시험은 있을 때만 견준다
PR2_JOB_KEYS="log_complete late_result"
PR2_LATE_KEYS="outcome exit_code finished_at received_at log_complete"
PR3_REASON="worker_unreachable_grace"
# PR3 의 사람 줄 — 실기에서 이 글로 grep 한다. 값은 여기서만 정하고, 시험이 진짜 생산자를 불러 만든
# 글에 이 값이 들어 있는지 본다(rcm wait 의 _GraceWatch · action_line · hold_owner_line ·
# late_result_line, rcm top 의 render, rcm eta 의 _fmt_eta_row, rcm check 의 _hold_rows,
# rcm doctor 의 hold_line)
LINE_LOST_CONTACT="lost contact with"
LINE_GRACE_GOES_ON="the job goes on if the worker is back"
LINE_GRACE_CANCEL="the job is cancelled when the worker is back"
LINE_CANCEL_REQUESTED="cancel requested — "
LINE_BACK_GOES_ON="is back — the job goes on"
LINE_BACK_CANCEL="is back — the job is being cancelled"
LINE_LATE="late result:"
LINE_HELD_WAIT="needs action · group"
LINE_HOLD_OWNER="is held — check the machine for a leftover process"
LINE_CHECK_HOLD="irreversible hold"
LINE_DOCTOR_HOLD="closed without a stop confirmation"
LINE_ETA_HELD="is held by #"
LINE_ETA_AHEAD=" ahead"
LINE_TOP_EMPTY_HELD="a held group does not start until it is released"
# PR3 의 웹 — 영어 화면의 글(i18n.js) · 레인 class · 「확인이 필요한 작업」 띠의 줄 수와 꼬리(app.js
# stuckBand). 시험이 node 로 app.js 를 불러 견준다
WEB_GRACE_GOES_ON="the job goes on if the worker is back within the grace period"
WEB_GRACE_CANCEL="the job is cancelled when the worker is back, or ends as lost"
WEB_STUCK_MAX="3"
WEB_AND_MORE_PRE="and "
WEB_AND_MORE_POST=" more"
WEB_NOTHING_ELSE="nothing else needs a look"
WEB_EMPTY_HELD="Queue is empty — a held group does not start until it is released."
WEB_EMPTY_HINT=" starts immediately."
WEB_LANE_UNHEARD="quiet"
WEB_LANE_LIVE="run"
# 하네스가 부르는 rcm 하위 명령과 그 옵션(시험이 cli.build_parser() 와 견준다). --no-follow 는 맨 앞
CLI_USES="wait:--job,--timeout,--return-on-action release-hold:--reason,--json doctor:--role,--config,--json run:--by,--no-wait,--no-join cancel: top: logs: check: eta:--job worker:--config serve:--config token:--config"
# 중계 명령(도우미 apply_relay) — tests/tcp_relay.py 의 TcpRelay 메서드로 간다
RELAY_OPS="pause resume stop hold release unrefuse slow fast quit"

# ── 도우미(파이썬) — 실행 때 시험 폴더에 쓴다 ───────────────────────────────────

write_helper() { # write_helper <dir> → <dir>/m5tb_helper.py
  mkdir -p "$1"
  cat >"$1/m5tb_helper.py" <<'PYEOF'
"""m5tb_live.sh 의 도우미. 하네스가 시험 폴더에 써서 `python -I` 로 부른다.

표준 라이브러리 + 이 체크아웃(TCP 중계 tests/tcp_relay.py · CDP 도우미
tests/test_web_browser.py · remote_ci_monitor 의 감독자 · 요약 문장). 판정(`judge`)은 순수
함수다 — 시험이 이 파일을 import 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("M5TB_ROOT") or Path(__file__).resolve().parents[3])
TERMINAL = ("succeeded", "failed", "timed_out", "cancelled", "lost", "expired")
ABSENT = "<absent>"

# ── 판정 경계의 재료 ─────────────────────────────────────────────────────────
# 제품의 값 — 시험이 remote_worker.py · job_supervisor.py 의 상수와 같은지 견준다. 판정은 부를
# 때마다 이 이름을 읽는다(모듈을 import 하지 않아 판정이 순수 함수로 남는다)
HEARTBEAT_DEADLINE = 15  # remote_worker.HEARTBEAT_DEADLINE_SECONDS(T_req)
SELF_STOP_SLACK = 10  # remote_worker.SELF_STOP_SLACK_SECONDS
WATCHDOG_TICK = 1  # remote_worker.WATCHDOG_TICK_SECONDS
SUPERVISOR_POLL = 0.25  # job_supervisor.SUPERVISOR_POLL_SECONDS
# 명세의 값 — docs/m5tb-workplan.md
DETECT_EPS = 0.06  # §4.1 「감독자가 t_d + 0.25 + ε 에 알아챈다(F78 실측 ε ≤ 0.06초)」
GRACE_OVERSHOOT = 0.05  # F78 「종료 대기 = grace_J(측정 넘침 ≤ 폴링 + 0.05초)」
RL43_D_AFTER_GRACE = 2.0  # RL43 (d) 「리더 끝 + grace_J + 2초 안에 사라진다」
RL51_AFTER_GRACE = 0.5  # RL51 (b)(c) 「첫 SIGTERM + grace_J + 0.5초 안에 사라진다」
# 하네스가 재는 자리의 몫 — 신호를 보낸 뒤 5ms 마다 kill(0) · 좀비면 ps 한 번(F84 최대 0.015초)
MEASURE_SLACK = 0.04
# RL51 (b)(c) 는 첫 SIGTERM 을 볼 수 없어 `rcm cancel` 이 돌아온 때부터 잰다. 취소는 다음
# heartbeat 응답(≤ HB)이 워커에 싣고, 그 왕복과 러너가 그룹에 SIGTERM 을 보내기까지를 0.5초로 둔다
CANCEL_PATH = 0.5


def detect_max() -> float:
    """부모 SIGKILL → 감독자가 알아채 SIGTERM 을 보내기까지의 상한(F78 0.07~0.31초)."""
    return SUPERVISOR_POLL + DETECT_EPS


# ── HTTP ──────────────────────────────────────────────────────────────────


def _token(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip() if path and path != "-" else ""


def get(url: str, tok: str, path: str, timeout: float = 5.0) -> tuple[int, object]:
    req = urllib.request.Request(url + path)
    t = _token(tok)
    if t:
        req.add_header("Authorization", f"Bearer {t}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else {})
        except ValueError:
            return e.code, {"raw": raw.decode(errors="replace")}


def show(v: object) -> str:
    """한 칸의 글 — null 은 null, 참 · 거짓은 true · false, 키가 없으면 <absent>(null 과 다르다)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return str(v).replace("\n", " ")


def dotted(doc: object, key: str) -> object:
    cur = doc
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return ABSENT
        cur = cur[part]
    return cur


def cmd_job(url: str, tok: str, jid: str, *keys: str) -> int:
    try:
        status, doc = get(url, tok, f"/jobs/{jid}")
    except OSError:
        return 1
    if status != 200:
        return 1
    for k in keys:
        v = dotted(doc, k)
        print(ABSENT if v is ABSENT else show(v))
    return 0


def cmd_jobdoc(url: str, tok: str, jid: str, dest: str) -> int:
    """잡 문서를 dest 에 쓰고 HTTP 상태를 찍는다(닿지 않으면 0). 오류 본문도 쓰이므로 판정은
    그 파일을 상태 200 일 때만 「읽었다」고 본다."""
    try:
        status, doc = get(url, tok, f"/jobs/{jid}")
    except OSError:
        print(0)
        return 1
    Path(dest).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(status)
    return 0 if status == 200 else 1


def cmd_waitstate(url: str, tok: str, jid: str, timeout: str, states: str) -> int:
    want = TERMINAL if states == "terminal" else tuple(states.split(","))
    deadline = time.monotonic() + float(timeout)
    last = "unknown"
    while True:
        try:
            status, doc = get(url, tok, f"/jobs/{jid}")
            if status == 200 and isinstance(doc, dict):
                last = str(doc.get("state"))
        except OSError:
            pass
        if last in want:
            print(last)
            return 0
        if time.monotonic() >= deadline:
            print(last)
            return 1
        time.sleep(0.1)


def cmd_status(url: str, tok: str, dest: str) -> int:
    status, doc = get(url, tok, "/api/status")
    Path(dest).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if status == 200 else 1


def cmd_holds(url: str, tok: str) -> int:
    """열린 홀드의 잡 번호(공백으로). 못 읽으면 unknown — 빈 목록과 다르다."""
    try:
        status, doc = get(url, tok, "/api/status")
    except OSError:
        print("unknown")
        return 0
    holds = doc.get("irreversible_holds", ABSENT) if isinstance(doc, dict) else ABSENT
    if status != 200 or not isinstance(holds, list):
        print("unknown")
        return 0
    print(" ".join(str(h.get("job_id")) for h in holds if isinstance(h, dict)))
    return 0


def open_job_ids(doc: object) -> list[int] | None:
    """`/api/status` 의 풀마다 대기열(queued · uploading · running · cancelling)과 예약의 잡 번호.

    큐를 못 읽은 풀(`queue` 가 null)이 하나라도 있거나 문서가 이상하면 None — 빈 목록과 다르다."""
    pools = doc.get("pools") if isinstance(doc, dict) else None
    if not isinstance(pools, list):
        return None
    ids: set[int] = set()
    for pool in pools:
        if not isinstance(pool, dict) or not isinstance(pool.get("queue"), list):
            return None
        for row in [*pool["queue"], *(pool.get("scheduled") or [])]:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                ids.add(row["id"])
    return sorted(ids)


def cmd_openjobs(url: str, tok: str) -> int:
    """남은(끝나지 않은) 잡 번호(공백으로). 못 읽으면 unknown."""
    try:
        status, doc = get(url, tok, "/api/status")
    except OSError:
        print("unknown")
        return 0
    ids = open_job_ids(doc) if status == 200 else None
    print("unknown" if ids is None else " ".join(str(i) for i in ids))
    return 0


def why_not_started(
    jobs: list[dict[str, object]], status: object, workers: list[str]
) -> tuple[str, str]:
    """시작하지 않은 잡마다 「어디서 돌았나 · 대기 사유」와 그 단계 워커 레인의 막힘을 한 줄로.

    하네스 탓이 보이면 FAIL 이고 글은 `harness: ` 로 시작한다 — 앞 단계의 남은 잡이 이 단계 워커를
    가져갔거나, 하네스가 끈 부하 게이트가 이 단계 워커의 레인을 막고 있다. 모르면 UNKNOWN 이다."""
    if not isinstance(status, dict):
        ids = " ".join(f"#{j.get('id')}" for j in jobs)
        return "UNKNOWN", f"jobs did not start ({ids}); status unreadable"
    rows: dict[object, dict[str, object]] = {}
    for pool in status.get("pools") or []:
        for row in (pool.get("queue") or []) if isinstance(pool, dict) else []:
            if isinstance(row, dict):
                rows[row.get("id")] = row
    lanes = [
        w
        for w in ((status.get("server") or {}).get("workers") or [])
        if isinstance(w, dict) and w.get("worker") in workers
    ]
    harness = False
    parts: list[str] = []
    for j in jobs:
        jid, preset, state = j.get("id"), j.get("preset") or "?", j.get("state") or "?"
        text = f"#{jid} {preset} {state}"
        ran = j.get("ran_on")
        if isinstance(ran, str) and ran and ran not in workers:
            harness = True
            text += f" on {ran}, not on {','.join(workers)}"
        reason = (rows.get(jid) or {}).get("reason")
        if reason:
            text += f" ({reason})"
        parts.append(text)
    for name in workers:
        mine = [w for w in lanes if w.get("worker") == name]
        if not mine:
            parts.append(f"{name}: not registered")
            continue
        for w in mine:
            if w.get("hold_code"):
                harness = True
                parts.append(f"{name} lane {w.get('lane')} held ({w.get('hold_code')})")
            elif w.get("state") not in ("idle", "busy"):
                parts.append(f"{name} lane {w.get('lane')} {w.get('state')}")
    text = "jobs did not start: " + "; ".join(parts)
    return ("FAIL", "harness: " + text) if harness else ("UNKNOWN", text)


def cmd_whynot(url: str, tok: str, workers: str, *jids: str) -> int:
    """`LEVEL 글` 한 줄 — `why_not_started` 를 진짜 잡 문서와 `/api/status` 로."""
    jobs: list[dict[str, object]] = []
    for jid in jids:
        try:
            status, doc = get(url, tok, f"/jobs/{jid}")
        except OSError:
            status, doc = 0, {}
        jobs.append(doc if status == 200 and isinstance(doc, dict) else {"id": jid})
    try:
        status, sdoc = get(url, tok, "/api/status")
    except OSError:
        status, sdoc = 0, None
    level, text = why_not_started(jobs, sdoc if status == 200 else None, workers.split(","))
    print(f"{level} {text}")
    return 0


def cmd_health(url: str, tok: str) -> int:
    try:
        status, doc = get(url, tok, "/api/health")
    except OSError:
        print("unreachable")
        return 0
    d = doc if isinstance(doc, dict) else {}
    holds = d.get("irreversible_holds", ABSENT)
    n = len(holds) if isinstance(holds, list) else ("null" if holds is None else ABSENT)
    print(f"{status}|{show(d.get('ok'))}|{show(d.get('workers_down'))}|{n}|{show(d.get('error'))}")
    return 0


def cmd_getraw(url: str, tok: str, path: str, dest: str) -> int:
    req = urllib.request.Request(url + path)
    t = _token(tok)
    if t:
        req.add_header("Authorization", f"Bearer {t}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            Path(dest).write_bytes(r.read())
            print(r.status)
    except urllib.error.HTTPError as e:
        Path(dest).write_bytes(e.read())
        print(e.code)
    except OSError:  # 닿지 않음 — 200 이 아니므로 판정은 「읽지 못함」으로 본다
        print(0)
    return 0


def cmd_sse(url: str, tok: str, seconds: str, dest: str) -> int:
    """`/events` 를 정한 초만큼 받아 적는다. 받은 바이트 수를 찍는다."""
    req = urllib.request.Request(url + "/events")
    t = _token(tok)
    if t:
        req.add_header("Authorization", f"Bearer {t}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    end = time.monotonic() + float(seconds)
    got = 0
    with open(dest, "wb") as out:
        try:
            with opener.open(req, timeout=5) as r:
                while time.monotonic() < end:
                    try:
                        chunk = r.read1(65536)
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    out.write(chunk)
                    out.flush()
                    got += len(chunk)
        except (OSError, urllib.error.URLError):
            pass
    print(got)
    return 0


# ── 프로세스 ────────────────────────────────────────────────────────────────


def is_dead(pid: int) -> bool:
    """없거나(ESRCH) 좀비면 죽은 것이다."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    return out == "" or out.startswith("Z")


def _wait_dead(pid: int, t0: float, timeout: float) -> float | None:
    while True:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if not alive or is_dead(pid):
            return time.monotonic() - t0
        if time.monotonic() - t0 >= timeout:
            return None
        time.sleep(0.005)


def cmd_alive(pid: str) -> int:
    return 1 if is_dead(int(pid)) else 0


def cmd_waitdead(pid: str, timeout: str) -> int:
    got = _wait_dead(int(pid), time.monotonic(), float(timeout))
    print("timeout" if got is None else f"{got:.3f}")
    return 0 if got is not None else 1


def cmd_killtime(sig: str, targets: str, watch: str, timeout: str) -> int:
    """targets 에 신호를 한꺼번에 보내고 watch 의 pid 마다 사라진 시간(초)을 잰다.

    pid 는 2 이상의 수만 받는다. 0 은 자기 프로세스 그룹, 음수는 그룹, 1 은 init 이다 — 하나라도
    있으면 아무 신호도 보내지 않고 `refused=…` 를 찍고 2 로 끝난다."""
    signum = getattr(signal, f"SIG{sig}")
    pids = [p for p in (*targets.split(","), *watch.split(",")) if p]
    bad = [p for p in pids if not p.isdigit() or int(p) <= 1 or int(p) == os.getpid()]
    if bad:
        print(f"refused={','.join(bad)}")
        return 2
    t0 = time.monotonic()
    print(f"t0={time.time():.3f}")
    for p in targets.split(","):
        if p:
            try:
                os.kill(int(p), signum)
            except ProcessLookupError:
                pass
    results: dict[str, float | None] = {}

    def one(p: str) -> None:
        results[p] = _wait_dead(int(p), t0, float(timeout))

    threads = [threading.Thread(target=one, args=(p,)) for p in watch.split(",") if p]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    for p in watch.split(","):
        if p:
            v = results.get(p)
            print(f"{p}={'timeout' if v is None else f'{v:.3f}'}")
    return 0


def cmd_cputime(pid: str) -> int:
    """`ps -o time=` 를 초로. 못 읽으면 빈 줄."""
    out = subprocess.run(
        ["ps", "-o", "time=", "-p", pid], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    if not out:
        print("")
        return 0
    days = 0
    if "-" in out:
        d, out = out.split("-", 1)
        days = int(d)
    parts = [float(x) for x in out.split(":")]
    secs = 0.0
    for x in parts:
        secs = secs * 60 + x
    print(f"{secs + days * 86400:.3f}")
    return 0


def cmd_spawn(log: str, pidfile: str, *argv: str) -> int:
    """argv 를 새 세션으로 띄우고 출력 줄마다 벽시계 시각을 붙여 log 에 쓴다.

    끝나면 종료 코드 한 줄을 더한다(`[harness] exit N`)."""
    p = subprocess.Popen(
        list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True
    )
    tmp = pidfile + ".tmp"
    Path(tmp).write_text(str(p.pid), encoding="utf-8")
    os.replace(tmp, pidfile)
    assert p.stdout is not None
    with open(log, "ab") as f:
        for line in iter(p.stdout.readline, b""):
            f.write(f"{time.time():.3f} ".encode() + line)
            f.flush()
        rc = p.wait()
        f.write(f"{time.time():.3f} [harness] exit {rc}\n".encode())
    return 0


def cmd_run(out: str, err: str, *argv: str) -> int:
    """argv 를 끝까지 돌린다. stdout 은 그대로, stderr 는 줄마다 벽시계 시각을 붙인다.

    종료 코드는 argv 의 것이다."""
    with open(out, "wb") as fo, open(err, "ab") as fe:
        p = subprocess.Popen(list(argv), stdout=fo, stderr=subprocess.PIPE)
        assert p.stderr is not None
        for line in iter(p.stderr.readline, b""):
            fe.write(f"{time.time():.3f} ".encode() + line)
            fe.flush()
        return p.wait()


def cmd_firstline(path: str, needle: str) -> int:
    """시각이 붙은 파일에서 needle 이 든 첫 줄의 시각. 없으면 빈 줄."""
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            if needle in line:
                print(line.split(" ", 1)[0])
                return 0
    except OSError:
        pass
    print("")
    return 0


def lines_in_order(path: str, first: str, second: str) -> str:
    """first 가 든 첫 줄이 second 가 든 첫 줄보다 앞이면 1, 뒤면 0, 둘 중 하나라도 없으면 <absent>.

    워커 로그는 stderr 한 줄기이고 줄마다 flush 한다(remote_worker.py) — 줄 순서가 쓴 순서다."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ABSENT
    a = next((i for i, x in enumerate(lines) if first in x), None)
    b = next((i for i, x in enumerate(lines) if second in x), None)
    if a is None or b is None:
        return ABSENT
    return "1" if a < b else "0"


def cmd_inorder(path: str, first: str, second: str) -> int:
    print(lines_in_order(path, first, second))
    return 0


def cmd_count(path: str, needle: str) -> int:
    try:
        print(Path(path).read_bytes().count(needle.encode()))
    except OSError:
        print(ABSENT)
    return 0


def cmd_sha(path: str) -> int:
    try:
        print(hashlib.sha256(Path(path).read_bytes()).hexdigest())
    except OSError:
        print(ABSENT)
    return 0


def cmd_epoch(iso: str) -> int:
    if iso in ("", "null", ABSENT):
        print("")
        return 0
    print(f"{datetime.fromisoformat(iso.replace('Z', '+00:00')).timestamp():.3f}")
    return 0


def cmd_now() -> int:
    print(f"{time.time():.3f}")
    return 0


def cmd_sub(a: str, b: str) -> int:
    """a − b(초). 하나라도 비면 빈 줄."""
    try:
        print(f"{float(a) - float(b):.3f}")
    except ValueError:
        print("")
    return 0


def cmd_sql(db: str, query: str, *params: str) -> int:
    """시험 DB 를 읽기 전용으로 연다(mode=ro). 행마다 탭으로."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        for row in conn.execute(query, params):
            print("\t".join(show(v) for v in row))
    finally:
        conn.close()
    return 0


def cmd_render(code: str, args_json: str) -> int:
    """잡의 요약 코드를 rcm 이 그리는 영어 문장으로(core/outcome.py — 진짜 생산자)."""
    from remote_ci_monitor.core.outcome import render

    try:
        args = json.loads(args_json) if args_json not in ("", "null", ABSENT) else None
    except ValueError:
        args = None
    text = render(code, args if isinstance(args, dict) else None)
    print("" if text is None else text)
    return 0


#: 웹이 lost 끝을 그리는 생산자(app.js outcomeText → i18n.js `outcome.<코드>`)를 node 로 부른다
_I18N_JS = """
globalThis.window = globalThis;
const I = require(process.argv[1]);
const [lang, code, args] = [process.argv[2], process.argv[3], JSON.parse(process.argv[4])];
const key = "outcome." + code;
process.stdout.write(I.has(key) ? String(I.t(lang, key, args)) : "");
"""


def web_sentence(lang: str, code: str, args: dict[str, object] | None) -> str:
    """그 언어 화면이 그 요약 코드에 그리는 문장. node 가 없거나 모르는 코드면 빈 글(모름)."""
    import shutil

    import remote_ci_monitor  # 서버가 내주는 바로 그 i18n.js(이 빌드의 web/)

    node = shutil.which("node")
    i18n = Path(remote_ci_monitor.__file__).parent / "web" / "i18n.js"
    if node is None or not i18n.is_file():
        return ""
    r = subprocess.run(
        [node, "-e", _I18N_JS, str(i18n), lang, code, json.dumps(args or {})],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def cmd_needle(lang: str, code: str, args_json: str) -> int:
    """웹 화면에서 찾을 글 — web_sentence 의 앞 40자(긴 문장은 줄이 바뀌어도 앞은 그대로다)."""
    try:
        args = json.loads(args_json) if args_json not in ("", "null", ABSENT) else None
    except ValueError:
        args = None
    print(web_sentence(lang, code, args if isinstance(args, dict) else None)[:40])
    return 0


def cmd_putsecret(config: str, repo: str, name: str, value_file: str) -> int:
    """시험 설정 옆의 비밀 폴더에 값을 둔다(서버가 가릴 값)."""
    from remote_ci_monitor.config import parse_release_profile
    from remote_ci_monitor.release_secrets import SecretStore, secrets_dir

    profile = parse_release_profile(
        repo,
        {
            "secrets_dir_env": "APP_SECRETS",
            "presets": {"plan": "rl18-plan", "upload": "rl18-upload", "review": "rl18-review"},
            "secrets": [{"name": name, "kind": "value"}],
        },
    )
    store = SecretStore(secrets_dir(Path(config), repo), profile)
    store.put(name, Path(value_file).read_bytes(), content_type="text/plain")
    return 0


def masked_output(secret: str, lines: int) -> bytes:
    """RL18 생성기의 출력에서 비밀을 `****` 로 바꾼 바이트 — 서버 log.txt 가 이것과 같아야 한다."""
    out = []
    for i in range(lines):
        line = f"k{i}=**** end" if i % 3 == 0 else f"pad-{i:05d}-" + "x" * (i % 37)
        out.append(line + "\n")
    return "".join(out).encode()


def cmd_masked_sha(secret_file: str, lines: str) -> int:
    secret = Path(secret_file).read_text(encoding="utf-8")
    print(hashlib.sha256(masked_output(secret, int(lines))).hexdigest())
    return 0


# ── TCP 중계(tests/tcp_relay.py) — 명령 파일로 몬다 ─────────────────────────────


def apply_relay(r: object, words: list[str]) -> str:
    op = words[0] if words else ""
    if op == "pause":
        r.pause()
    elif op == "resume":
        r.resume()
    elif op == "stop":
        r.stop()
    elif op == "hold":
        r.hold_next(words[1], refuse_rest=len(words) > 2 and words[2] == "refuse")
    elif op == "release":
        r.release_held()
    elif op == "unrefuse":
        r.stop_refusing()
    elif op == "slow":
        r.slow_next(words[1], every=float(words[2]) if len(words) > 2 else 1.0)
    elif op == "fast":
        r.release_slow()
    elif op == "quit":
        r.close()
    else:
        return f"error unknown {op}"
    return "ok"


def cmd_relay(ctl: str, upstream: str) -> int:
    sys.path.insert(0, str(ROOT / "tests"))
    from tcp_relay import TcpRelay

    r = TcpRelay(int(upstream))
    d = Path(ctl)
    (d / "port.tmp").write_text(str(r.port), encoding="utf-8")
    os.replace(d / "port.tmp", d / "port")
    seen = 0
    while True:
        cmds = sorted(
            (p for p in d.glob("cmd.*") if p.suffix[1:].isdigit()), key=lambda p: int(p.suffix[1:])
        )
        for c in cmds:
            n = int(c.suffix[1:])
            if n <= seen:
                continue
            words = c.read_text(encoding="utf-8").split()
            ack = apply_relay(r, words)
            seen = n
            (d / f"ack.{n}").write_text(ack, encoding="utf-8")
            if words[:1] == ["quit"]:
                return 0
        state = (
            f"held={r.held_count} refused={r.refused} slow={r.slow_count} accepted={r.accepted}\n"
        )
        (d / "state.tmp").write_text(state, encoding="utf-8")
        os.replace(d / "state.tmp", d / "state")
        time.sleep(0.05)


# ── 웹(CDP, tests/test_web_browser.py) ─────────────────────────────────────

_BAND = "document.getElementById('banner-note')"
WEB_JS = {
    "width": "window.innerWidth",
    "band_hidden": f"(() => {{ const e = {_BAND}; return e ? e.hidden : null; }})()",
    # 띠가 숨었으면 0 — 「띠 없음」과 「높이 0」을 가르려고 band_hidden 을 따로 둔다
    "band_h": (
        f"(() => {{ const e = {_BAND}; "
        # 이 조각은 f-string 이 아니다 — 닫는 괄호는 하나다
        "return e && !e.hidden ? Math.round(e.getBoundingClientRect().height) : 0; })()"
    ),
    "band_text": f"(() => {{ const e = {_BAND}; return e && !e.hidden ? e.textContent : ''; }})()",
    "stuck_n": "document.querySelectorAll('[data-stuck] .sl:not(.muted)').length",
    "stuck_text": (
        "(() => { const e = document.querySelector('[data-stuck]'); "
        "return e ? e.textContent : ''; })()"
    ),
    "chips": "document.querySelectorAll('[data-hold-chip]').length",
    # 띠의 꼬리 한 줄 — 「and N more」 · 「nothing else needs a look」 · 「unknown — …」
    "stuck_tail": (
        "(() => { const e = document.querySelectorAll('[data-stuck] .sl.muted'); "
        "return e.length ? e[e.length - 1].textContent : ''; })()"
    ),
}
#: 큐 행 하나에서 재는 것(RL28-web) — 끊긴 행은 시간을 세지 않고(틱 0 · 경과 글 그대로) 막대는 있되
#: 시간 눈금이 없다. 듣는 행(대조군)은 센다
ROW_KEYS = ("found", "cls", "reason", "ticks", "bars", "bar_ticks", "scaled", "el", "el_same")


def row_js(jid: str) -> str:
    """`tr#job-<jid>` 의 사실을 JSON 글로(행이 없으면 null)."""
    if not jid.isdigit():
        raise ValueError(f"job id {jid!r}")
    return (
        f"(() => {{ const r = document.getElementById('job-{jid}'); if (!r) return null; "
        "const p = r.querySelector('.pbar'); const e = r.querySelector('td.elapsed'); "
        "const why = r.querySelector('.reason'); "
        "return JSON.stringify({cls: r.className, "
        "reason: why ? (why.getAttribute('title') || why.textContent) : '', "
        "ticks: r.querySelectorAll('[data-tick]').length, "
        "bars: r.querySelectorAll('.pwrap').length, "
        "bar_ticks: r.querySelectorAll('.pwrap[data-tick]').length, "
        "scaled: p ? (p.getAttribute('data-basis') === 'time' ? 1 : 0) : 0, "
        "el: e ? e.textContent : ''}); })()"
    )


def _rows(c: object, roles: list[tuple[str, str]], lang: str) -> None:
    """역할마다 행 사실을 찍는다. 경과 글은 2.5초 뒤 한 번 더 읽어 그대로인지 본다."""
    first: dict[str, dict[str, object] | None] = {}
    for role, jid in roles:
        raw = c.eval(row_js(jid))  # type: ignore[attr-defined]
        first[role] = json.loads(raw) if isinstance(raw, str) else None
    time.sleep(2.5)
    for role, jid in roles:
        got = first[role]
        if got is None:
            print(f"{lang}_{role}_found=0")
            continue
        raw = c.eval(row_js(jid))  # type: ignore[attr-defined]
        again = json.loads(raw) if isinstance(raw, str) else None
        print(f"{lang}_{role}_found=1")
        for k in ROW_KEYS[1:-1]:
            print(f"{lang}_{role}_{k}={show(got.get(k))}")
        same = ABSENT if again is None else ("1" if again.get("el") == got.get("el") else "0")
        print(f"{lang}_{role}_el_same={same}")


#: 첫 상태 문서가 그려진 뒤 — 「확인이 필요한 작업」 칸은 그리기 전에 `—` 다(index.html)
READY_JS = (
    "(() => { const e = document.querySelector('[data-stuck]'); "
    "const t = e ? e.textContent.trim() : ''; "
    "return document.readyState === 'complete' && t.length > 0 && t !== '—'; })()"
)


#: 「show N more」를 눌러 최근 목록을 다 편다(app.js data-more-recent). 이미 폈거나 단추가 없으면
#: 그대로 둔다 — 단추 글이 「show fewer」면 이미 편 것이다. 누른 뒤 다시 그려질 때까지 기다린다
EXPAND_RECENT_JS = (
    "(() => { const b = document.querySelector('[data-more-recent]'); "
    "if (!b || !/more/i.test(b.textContent) && !/더/.test(b.textContent)) return 0; "
    "b.click(); return 1; })()"
)


def cmd_web(url: str, outdir: str, tag: str, width: str, rows: str = "") -> int:
    """390px 와 두 언어로 첫 화면을 연다. 사실을 `키=값` 줄로, 스크린샷 · 본문 글을 outdir 에.

    `rows` 는 `역할=잡번호,…` — 그 큐 행마다 ROW_KEYS 를 `<lang>_<역할>_<키>` 로 찍는다."""
    roles = [tuple(x.split("=", 1)) for x in rows.split(",") if "=" in x]
    sys.path.insert(0, str(ROOT / "tests"))
    try:
        import test_web_browser as wb
    except Exception as e:  # noqa: BLE001 — 도우미가 없으면 모른다(UNKNOWN)
        print(f"web=unavailable {type(e).__name__}")
        return 0
    if wb.CHROME is None:
        print("web=no-chrome")
        return 0
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        with wb.Chrome(out / f"chrome-{tag}", window="900,844") as c:
            c.viewport(int(width), 844, mobile=True)
            for lang in ("en", "ko"):
                c.open(f"{url}/?poll=1&lang={lang}", ready_js=READY_JS, timeout=30)
                c.frames(3)
                time.sleep(1.5)
                c.frames(2)
                for k, js in WEB_JS.items():
                    print(f"{lang}_{k}={show(c.eval(js))}")
                if roles:
                    _rows(c, roles, lang)  # type: ignore[arg-type]
                c.screenshot(out / f"{tag}-{lang}.png")
                # 화면 글은 최근 목록을 다 편 뒤에 읽는다 — 앞 단계의 끝난 잡도 거기 있다(RL29-web)
                if c.eval(EXPAND_RECENT_JS) == 1:
                    c.frames(2)
                    time.sleep(0.5)
                print(f"{lang}_errors={len(c.page_errors())}")
                body = c.eval("document.body.innerText") or ""
                (out / f"{tag}-{lang}.txt").write_text(body, encoding="utf-8")
        print("web=ok")
    except Exception as e:  # noqa: BLE001
        print(f"web=failed {type(e).__name__}: {str(e)[:200]}")
    return 0


# ── F78 · F83 · F84: 이 머신에서 감독자 값 ──────────────────────────────────


def cmd_f78() -> int:
    """감독자 -I 파일 경로 기동 · ps 한 번 · 감독 아래 /usr/bin/true 의 끝 지연(각 중앙값).

    기동은 부모가 틀린 감독자(시작 확인에서 125 로 끝남)로 잰다 — `-I` 파일 경로 기동과 표준
    라이브러리 import 와 시작 확인까지다(F83). 감독 아래 true 에서 그 기동과 직접 true 를 빼면
    자식 끝을 알아채는 지연이다(F78: waitpid 스레드라 0 에 가깝다)."""
    import remote_ci_monitor

    sup = str(Path(remote_ci_monitor.__file__).parent / "job_supervisor.py")
    starts, wraps, direct, pss = [], [], [], []
    for _ in range(5):
        t0 = time.monotonic()
        wrong_parent = [sys.executable, "-I", sup, "--parent", "1", "--grace", "1", "--", "true"]
        subprocess.run(wrong_parent, check=False, capture_output=True, start_new_session=True)
        starts.append(time.monotonic() - t0)
    for _ in range(5):
        t0 = time.monotonic()
        subprocess.run(["/usr/bin/true"], check=False)
        direct.append(time.monotonic() - t0)
        argv = [sys.executable, "-I", sup, "--parent", str(os.getpid()), "--grace", "1"]
        t0 = time.monotonic()
        subprocess.run([*argv, "--", "/usr/bin/true"], check=False, start_new_session=True)
        wraps.append(time.monotonic() - t0)
    env = {**os.environ, "LC_ALL": "C"}
    for _ in range(20):
        t0 = time.monotonic()
        subprocess.run(["ps", "-A", "-o", "pid=", "-o", "pgid="], capture_output=True, env=env)
        pss.append(time.monotonic() - t0)
    print(f"python_I_start={statistics.median(starts):.4f}")
    print(f"true_direct={statistics.median(direct):.4f}")
    print(f"true_supervised={statistics.median(wraps):.4f}")
    print(f"ps_cost={statistics.median(pss):.4f}")
    print(f"ps_cost_max={max(pss):.4f}")
    return 0


# ── 판정(순수) ───────────────────────────────────────────────────────────────


def read_facts(path: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return facts
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            facts[k.strip()] = v.strip()
    return facts


class Judge:
    """사실 몇 개를 기대와 견준다. 없는 사실은 UNKNOWN 이지 PASS 가 아니다."""

    def __init__(self, facts: dict[str, str]):
        self.f = facts
        self.bad: list[str] = []
        self.unk: list[str] = []

    def v(self, k: str) -> str | None:
        x = self.f.get(k)
        return None if x is None or x in ("", ABSENT, "unknown", "timeout") else x

    def num(self, k: str) -> float | None:
        x = self.v(k)
        if x is None:
            return None
        try:
            return float(x)
        except ValueError:
            return None

    def gate(self, flag: str, what: str) -> bool:
        if self.f.get(flag) == "1":
            return True
        pr = {"pr2": "PR2 #286", "pr3": "PR3 #285"}.get(flag, flag)
        self.unk.append(f"{what}: this build has no {pr}")
        return False

    def eq(self, k: str, want: object, what: str) -> None:
        x = self.v(k)
        if x is None:
            self.unk.append(f"{what}: not observed")
        elif x != str(want):
            self.bad.append(f"{what}: {x}, want {want}")

    def among(self, k: str, wants: tuple[str, ...], what: str) -> None:
        x = self.v(k)
        if x is None:
            self.unk.append(f"{what}: not observed")
        elif x not in wants:
            self.bad.append(f"{what}: {x}, want one of {', '.join(wants)}")

    def le(self, k: str, bound: float, what: str) -> None:
        x = self.num(k)
        if x is None:
            self.unk.append(f"{what}: not measured")
        elif x > bound:
            self.bad.append(f"{what}: {x:g}s > {bound:g}s")

    def lt(self, k: str, bound: float, what: str) -> None:
        x = self.num(k)
        if x is None:
            self.unk.append(f"{what}: not measured")
        elif not x < bound:
            self.bad.append(f"{what}: {x:g} not < {bound:g}")

    def ge(self, k: str, bound: float, what: str) -> None:
        x = self.num(k)
        if x is None:
            self.unk.append(f"{what}: not measured")
        elif x < bound:
            self.bad.append(f"{what}: {x:g} < {bound:g}")

    def nulls(self, k: str, what: str) -> None:
        x = self.f.get(k)
        if x is None or x in ("", ABSENT):
            self.unk.append(f"{what}: not observed")
        elif x != "null":
            self.bad.append(f"{what}: {x}, want null")

    def result(self, pass_text: str) -> str:
        if self.bad:
            tail = f" (not observed: {'; '.join(self.unk)})" if self.unk else ""
            return "FAIL " + "; ".join(self.bad) + tail
        if self.unk:
            return "UNKNOWN " + "; ".join(self.unk)
        return "PASS " + pass_text


def _g(f: dict[str, str]) -> float:
    try:
        return float(f.get("G", ""))
    except ValueError:
        return 90.0


def judge_rl1(f: dict[str, str]) -> str:
    j = Judge(f)
    g = _g(f)
    j.eq("state_at_stop", "running", "server state when the command died")
    j.lt("self_stop_after", g - 5, "partition → command gone")
    # 명세: 멈춘 시각 ≤ DB finished_at(차이 ≥ 0). finished_epoch 는 DB 의 REAL 칸이다 — 잡 문서의
    # finished_at 은 초 단위로 잘린다(core/status.py iso). death_epoch 는 하네스가 소멸을 본 뒤의
    # 시각이라 실제 소멸보다 늦다(엄한 쪽)
    death, fin = j.num("death_epoch"), j.num("finished_epoch")
    if death is None or fin is None:
        j.unk.append("command death vs DB finished_at: not observed")
    elif death > fin:
        j.bad.append(f"command died {death - fin:.3f}s after the server closed the job")
    j.eq("final_state", "lost", "final state")
    j.eq("lost_events", 1, "state→lost events")
    j.among("summary_code", ("worker_unreachable", "worker_self_stopped"), "summary_code")
    j.nulls("failed_step", "failed_step")
    j.nulls("last_step", "last_step")
    j.eq("wait_rc", 3, "rcm wait exit")
    if j.v("summary_code") == "worker_unreachable":
        j.eq("hold_at_close", 1, "hold on the server's close")
        j.eq("hold_released_after_return", 1, "returning worker's finish releases the hold")
    elif j.v("summary_code") == "worker_self_stopped":
        j.eq("hold_at_close", 0, "hold after the worker's own report")
    return j.result(
        f"command gone {f.get('self_stop_after')}s after the cut (server still running), "
        f"lost {f.get('summary_code')} once, rcm wait 3, hold {f.get('hold_at_close')}"
    )


def judge_rl5(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("final_state", "lost", "final state")
    j.eq("summary_code", "worker_unreachable", "summary_code")
    j.eq("wait_rc", 3, "rcm wait exit")
    j.eq("alive_after_close", 1, "process alive after the close")
    j.eq("local_log_grew", 1, "worker's local log.txt grows")
    j.eq("hold", 0, "hold on a plain job")
    j.eq("alive_after_return", 1, "process alive after the 409 job_closed")
    if j.gate("pr2", "late_result"):
        j.eq("late_count", 1, "late_result events")
        j.among("late_outcome", TERMINAL, "late_result.outcome")
        j.eq("state_after_late", "lost", "state after the late finish")
        j.eq("wait_rc_after_late", 3, "rcm wait exit after the late finish")
    if j.gate("pr3", "rcm wait late result line"):
        j.eq("late_line", 1, "rcm wait prints the late result line")
    return j.result(
        "lost worker_unreachable, exit 3, process kept running and logging, no hold, "
        f"one late_result ({f.get('late_outcome')}), state stays lost"
    )


def judge_rl42(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("hold_rows", 1, "irreversible_holds rows for the job")
    j.eq("hold_cause", "worker_unreachable", "hold cause")
    j.eq("hold_events", 1, "hold events")
    j.eq("same_group_state", "queued", "same-group job")
    j.eq("same_group_reason", "needs_action", "same-group reason")
    j.nulls("same_group_started", "same-group started_at (claims)")
    j.eq("after_restart_hold", 1, "hold after a server restart")
    j.eq("after_restart_same_group_state", "queued", "same-group job after the restart")
    j.eq("other_group_ran", 1, "a job of another group runs")
    j.eq("plain_hold_rows", 0, "hold rows of the plain job")
    j.eq("released_after_return", 1, "returning worker releases the hold")
    j.eq("same_group_started_after_release", 1, "same-group job starts after the release")
    return j.result(
        "one hold row (worker_unreachable) and one hold event; the same group waited through a "
        "server restart with no claim while another group ran; the plain job held nothing"
    )


def judge_rl2(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("x_dead_while_down", 1, "irreversible command gone while the server was down")
    j.eq("y_alive_while_down", 1, "plain process alive while the server was down")
    j.eq("y_state_after_start", "running", "plain job right after the start")
    j.eq("x_final", "lost", "irreversible job state")
    j.among("x_code", ("worker_self_stopped", "worker_unreachable"), "summary_code")
    j.eq("x_lost_events", 1, "state→lost events")
    j.eq("x_wait_rc", 3, "rcm wait exit")
    j.nulls("x_failed_step", "failed_step")
    if j.v("x_code") == "worker_unreachable":
        j.eq("x_hold_released", 1, "returning worker releases the hold")
    else:
        j.eq("x_hold", 0, "hold after the worker's own report")
    j.eq("y_alive_end", 1, "plain process alive at the end")
    return j.result(
        f"self-stop while the server was down, lost {f.get('x_code')} once, rcm wait 3, "
        "the start-up tick did not close the plain job"
    )


def judge_rl8(f: dict[str, str]) -> str:
    j = Judge(f)
    g = _g(f)
    j.eq("cancel_rc_x", 0, "rcm cancel (irreversible)")
    j.eq("cancel_rc_y", 0, "rcm cancel (plain)")
    j.eq("x_cancelling", "cancelling", "irreversible job after the cancel")
    j.eq("y_cancelling", "cancelling", "plain job after the cancel")
    j.lt("x_dead_after", g - 5, "irreversible command gone before G")
    for k in ("x", "y"):
        j.eq(f"{k}_final", "lost", f"{k} state")
        j.eq(f"{k}_code", "worker_unreachable", f"{k} summary_code")
        j.eq(f"{k}_wait_rc", 3, f"{k} rcm wait exit")
    j.eq("y_dead_after_return", 1, "plain process stopped after the return (had_cancel)")
    j.eq("x_hold_at_close", 1, "hold on the irreversible job's close")
    j.eq("x_hold_released", 1, "returning worker releases the hold")
    if j.gate("pr2", "late_result"):
        late = j.num("y_late_count")
        if late is None:
            j.unk.append("late_result events: not observed")
        elif late > 1:
            j.bad.append(f"late_result events: {late:g}, want at most 1")
        j.eq("y_state_end", "lost", "plain job state at the end")
        j.eq("x_state_end", "lost", "irreversible job state at the end")
    return j.result(
        "both cancelling, both lost worker_unreachable (exit 3), not cancelled or "
        "cancel_unconfirmed; the returning worker stopped the plain process and released the hold"
    )


def judge_rl10(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("a_exit", 0, "first process exit")
    j.eq("x_dead", 1, "job process gone")
    j.eq("x_final", "lost", "job state")
    j.eq("x_code", "worker_replaced", "summary_code")
    j.eq("x_wait_rc", 3, "rcm wait exit")
    j.eq("x_hold", 0, "hold")
    j.eq("b_registered", 1, "second process registered")
    j.le("registers", 2, "sessions of that name")
    return j.result(
        "the first process stopped its job, reported lost (worker_replaced) and exited 0 "
        f"(no restart by a supervisor), {f.get('registers')} sessions, no hold"
    )


def judge_rl15(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("orphan_alive_after_kill", 1, "plain job orphan alive after kill -9")
    j.eq("orphan_dead_after_restart", 1, "orphan stopped by the restarted worker")
    j.eq("orphan_line", 1, "worker log 'orphan #N: stopped process group'")
    j.eq("z_after_orphan", 1, "next job started after the orphan was gone")
    j.eq("plain_final", "lost", "orphan job state")
    j.eq("ship_cmd_dead", 1, "irreversible command ended by the supervisor")
    j.eq("ship_final", "lost", "irreversible job state")
    j.eq("ship_hold", 0, "irreversible job hold after the restart (group confirmed gone)")
    j.eq("leader_alive", 1, "member alive after worker and supervisor kill -9")
    j.eq("leader_line", 1, "worker log 'leader gone but its group is alive'")
    j.eq("leader_final", "lost", "leaderless job state")
    j.eq("leader_hold", 1, "leaderless group is held (not confirmed)")
    return j.result(
        f"identity by {f.get('identity')}: the orphan was stopped before the next claim; the "
        "supervised group was confirmed gone; a leaderless group was left alone and held"
    )


def judge_rl18(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("wait_rc", 0, "rcm wait exit")
    # 원문 0 번은 그곳을 정말 읽었을 때만 「비밀 없음」이다. 증거: 명령 종료 0 · HTTP 200 과, 그
    # 글에 있어야 할 것(가린 값 `****` · 잡의 프리셋 이름 · SSE 의 그 잡 사건)이 하나 이상
    for k, what, proofs in (
        ("in_log", "server log.txt", (("masked_seen", None),)),
        ("in_rcm_logs", "rcm logs", (("rcm_logs_rc", "0"), ("rcm_logs_seen", None))),
        ("in_web_log", "web log route", (("web_log_status", "200"), ("web_log_seen", None))),
        ("in_job_doc", "job document", (("job_doc_status", "200"), ("job_doc_seen", None))),
        ("in_sse", "SSE /events", (("sse_seen", None),)),
    ):
        found = j.num(k)
        if found is not None and found > 0:
            j.bad.append(f"secret in {what}: {found:g}")  # 읽은 증거가 없어도 보였으면 보인 것이다
            continue
        missing = [
            f"{p} {f.get(p, 'not recorded')}"
            for p, want in proofs
            if (j.v(p) != want if want is not None else (j.num(p) or 0) < 1)
        ]
        if missing:
            j.unk.append(f"{what}: no sign it was read ({', '.join(missing)})")
        else:
            j.eq(k, 0, f"secret in {what}")
    if j.gate("pr2", "byte-exact log and resend"):
        j.eq("log_equals_masked", 1, "log.txt equals the masked output")
        j.eq("resend_seen", 1, "the worker resent a piece (relay cut)")
        j.eq("log_complete", "true", "log_complete")
    return j.result("no secret in log.txt, rcm logs, SSE, the web log route or the job document")


def judge_rl28(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("state_during", "running", "job state in the grace")
    j.nulls("finish_at", "estimate.finish_at")
    j.eq("health_stable", 1, "/api/health stays one value in the grace")
    j.eq("wait_timeout_rc", 3, "rcm wait --timeout exit")
    secs, tmo = j.num("wait_timeout_secs"), j.num("wait_timeout")
    if secs is None or tmo is None:
        j.unk.append("rcm wait --timeout duration: not measured")
    elif abs(secs - tmo) > 5:
        j.bad.append(f"rcm wait --timeout {tmo:g} took {secs:g}s")
    if j.gate("pr3", "worker_unreachable_grace on screens"):
        j.eq("reason", "worker_unreachable_grace", "queue reason")
        j.eq("top_text", 1, "rcm top says contact was lost")
        j.eq("top_raw", 0, "raw code in rcm top")
        t = float(f.get("T", "10") or 10)
        j.le("lost_line_delay", t + 15, "rcm wait 'lost contact' line delay")
        j.le("back_line_delay", 15, "rcm wait 'is back' line delay")
        j.eq("eta_text", 1, "rcm eta --job says contact was lost")
    return j.result(
        f"running with reason {f.get('reason')}, no ETA, health fixed, rcm wait lines after "
        f"{f.get('lost_line_delay')}s / {f.get('back_line_delay')}s, --timeout exit 3"
    )


def judge_rl28_web(f: dict[str, str]) -> str:
    """끊긴 행(유예 중 · 취소 중)은 시간을 세지 않고 회색 레인이고 막대에 시간 눈금이 없다. 같은
    화면의 듣는 행(대조군)이 세지 않으면 「안 센다」는 아무것도 말하지 않는다 — UNKNOWN."""
    base = judge_web(f, band_max=80, needles=("grace_said", "cancel_said"))
    if not base.startswith("PASS"):
        return base
    j = Judge(f)
    for lang in ("en", "ko"):
        live = f"{lang}_live"
        j.eq(f"{live}_found", 1, f"{lang} the control row (a worker that answers) is on the page")
        if j.v(f"{live}_found") == "1":
            for k, want, what in (
                ("lane", "1", "lane"),
                ("scaled", "1", "time scale on the bar"),
                ("el_same", "0", "elapsed text changes"),
            ):
                if j.v(f"{live}_{k}") != want:
                    j.unk.append(f"{lang} control row {what}: {f.get(f'{live}_{k}')}, want {want}")
            for k, what in (
                ("ticks", "ticking elements"),
                ("bars", "a progress bar"),
                ("bar_ticks", "a ticking bar"),
            ):
                n = j.num(f"{live}_{k}")
                if n is None or n < 1:
                    j.unk.append(f"{lang} control row has no {what} ({f.get(f'{live}_{k}')})")
        for role in ("grace", "cancel"):
            r = f"{lang}_{role}"
            j.eq(f"{r}_found", 1, f"{lang} {role} row is on the page")
            j.eq(f"{r}_lane", 1, f"{lang} {role} row lane is grey (not running)")
            j.eq(f"{r}_ticks", 0, f"{lang} {role} row ticking elements")
            j.ge(f"{r}_bars", 1, f"{lang} {role} row progress bar")
            j.eq(f"{r}_bar_ticks", 0, f"{lang} {role} row bar ticks")
            j.eq(f"{r}_scaled", 0, f"{lang} {role} row bar time scale")
            j.eq(f"{r}_el_same", 1, f"{lang} {role} row elapsed text stays")
    return j.result(
        f"{base[5:]}; unreachable rows grey, not counting, bar without a time scale, while the "
        "control row counts"
    )


def judge_rl28_cancel(f: dict[str, str]) -> str:
    """끊긴 동안 취소한 잡: 화면마다 「돌아오면 취소된다」고 말하고 「이어진다」고 말하지 않는다
    (결정 322). 돌아온 뒤 줄은 rcm wait 이 그 순간을 봤을 때만 나온다 — 봤는지만 적는다."""
    j = Judge(f)
    if not j.gate("pr3", "cancelling job lines in the grace"):
        return j.result("")
    j.eq("cancel_rc", 0, "rcm cancel in the grace")
    j.eq("state_after_cancel", "cancelling", "state after the cancel")
    j.eq("reason_after_cancel", "worker_unreachable_grace", "queue reason after the cancel")
    j.eq("top_cancel", 1, "rcm top says it is cancelled when the worker is back")
    j.eq("eta_cancel", 1, "rcm eta --job says it is cancelled when the worker is back")
    j.eq("fresh_line", 1, "a new rcm wait says it is cancelled when the worker is back")
    j.eq("fresh_goes_on", 0, "a new rcm wait says the job goes on")
    j.eq("bg_cancel_line", 1, "the running rcm wait corrects itself (cancel requested)")
    j.eq("bg_back_goes_on", 0, "the running rcm wait says the cancelled job goes on")
    j.among("bg_back_cancel", ("0", "1"), "the running rcm wait's back line")
    j.eq("final", "cancelled", "state after the worker came back")
    seen = "seen" if f.get("bg_back_cancel") == "1" else "not seen (the job ended first)"
    return j.result(
        "top, eta and wait promise a cancel on the return, never that the job goes on; "
        f"cancelled after the return; the running wait's back line {seen}"
    )


def judge_web(f: dict[str, str], *, band_max: float | None, needles: tuple[str, ...]) -> str:
    j = Judge(f)
    web = f.get("web", "")
    if web != "ok":
        return f"UNKNOWN web not observed ({web or 'not run'})"
    for lang in ("en", "ko"):
        j.eq(f"{lang}_width", 390, f"{lang} width")
        j.eq(f"{lang}_errors", 0, f"{lang} page errors")
        if band_max is not None:
            j.le(f"{lang}_band_h", band_max, f"{lang} banner height (px)")
        j.eq(f"{lang}_raw", 0, f"{lang} raw codes on the page")
    for n in needles:
        j.eq(f"found_{n}", 1, f"page shows {n}")
    return j.result(
        f"390px, en and ko, 0 page errors, banner {f.get('en_band_h')}px / {f.get('ko_band_h')}px"
    )


def judge_rl29(f: dict[str, str]) -> str:
    j = Judge(f)
    codes = (f.get("codes") or "").split()
    lines = j.gate("pr3", "rcm wait lost lines")
    for code in codes:
        if f.get(f"{code}_job") in (None, "", ABSENT):
            j.unk.append(f"{code}: no job ended so in this run")
            continue
        j.eq(f"{code}_rc", 3, f"{code} rcm wait exit")
        j.nulls(f"{code}_failed_step", f"{code} failed_step")
        j.eq(f"{code}_notify", 1, f"{code} notify hook calls")
        if lines:
            j.eq(f"{code}_line", 1, f"{code} rcm wait lost line")
    if not codes:
        j.unk.append("no codes listed")
    return j.result(f"{len(codes)} lost endings: exit 3, their sentence, one notify call each")


def judge_rl29_web(f: dict[str, str]) -> str:
    """다섯 lost 끝마다 두 언어 화면에 그 언어의 문장(i18n.js 가 그린 앞 40자)이 있다.

    찾을 끝이 없거나(`needles` 에 없는 코드) 찾을 글이 비면 모른다 — 빈 글은 아무 화면에나
    맞는다."""
    j = Judge(f)
    codes = (f.get("codes") or "").split()
    needles = set((f.get("needles") or "").split())
    found: list[str] = []
    if not codes:
        j.unk.append("no codes listed")
    for code in codes:
        if code not in needles:
            j.unk.append(f"{code}: no job ended so — nothing to look for on the page")
            continue
        for lang in ("en", "ko"):
            if j.v(f"needle_{lang}_{code}") is None:
                j.unk.append(f"{lang} {code}: no sentence to look for")
            else:
                found.append(f"{lang}_{code}")
    base = judge_web(f, band_max=None, needles=tuple(found))
    if base.startswith("FAIL") or not j.unk:
        return base
    rest = "" if base.startswith("PASS") else f"; {base.split(' ', 1)[1]}"
    return "UNKNOWN " + "; ".join(j.unk) + rest


def remote_threshold(g: float, grace_j: float) -> float:
    """자기 멈춤 문턱 G − m_J, m_J = T_req + grace_J + 여유(remote_worker.self_stop_threshold)."""
    return g - (HEARTBEAT_DEADLINE + grace_j + SELF_STOP_SLACK)


def judge_rl34(f: dict[str, str]) -> str:
    j = Judge(f)
    g = _g(f)
    for m, what in (("d", "default grace"), ("b", "big grace")):
        j.eq(f"{m}_state_at_stop", "running", f"{what}: server state at the stop")
        gj = j.num(f"{m}_grace_j")
        thr = j.num(f"{m}_threshold")
        if gj is None or thr is None:
            j.unk.append(f"{what}: threshold line or grace_J not observed")
        else:
            # 워커가 적는 값은 int(wgap) 이다: 감시 틱(WATCHDOG_TICK)마다 wgap ≥ 문턱을 보므로
            # 문턱 이상, 문턱 + 틱 이하다(remote_worker.py 의 watchdog)
            want = remote_threshold(g, gj)
            if thr > want + WATCHDOG_TICK:
                j.bad.append(f"{what}: stopped at {thr:g}s without the server, threshold {want:g}")
            elif thr < want:
                j.bad.append(f"{what}: stopped at {thr:g}s, before its threshold {want:g}")
            # 멈춘 뒤 러너가 SIGTERM → grace_J → SIGKILL 하므로 grace_J, 그리고 감시 틱 1 + 측정 2
            j.le(f"{m}_stop_after", thr + gj + 3, f"{what}: cut → command gone")
        j.lt(f"{m}_stop_after", g - 5, f"{what}: command gone before heard + G")
        j.eq(f"{m}_code", "worker_self_stopped", f"{what}: summary_code")
        j.eq(f"{m}_wait_rc", 3, f"{what}: rcm wait exit")
        j.eq(f"{m}_hold", 0, f"{what}: hold")
    j.eq("b_lowered", 1, "big grace: the worker says it lowered grace_seconds")
    return j.result(
        f"default grace: gone {f.get('d_stop_after')}s after the cut (threshold "
        f"{f.get('d_threshold')}s); big grace: {f.get('b_stop_after')}s "
        f"(grace_J {f.get('b_grace_j')})"
    )


def judge_rl40(f: dict[str, str]) -> str:
    j = Judge(f)
    j.ge("refused_secs", float(f.get("T", "10") or 10) + 1, "connection refused for longer than T")
    j.eq("state_while_refused", "running", "job state while refused")
    j.eq("wait_rc", 0, "rcm wait exit")
    j.eq("final_state", "succeeded", "final state")
    j.eq("log_sha_match", 1, "server log.txt sha256 = worker local log")
    j.eq("hold", 0, "hold")
    if j.gate("pr2", "log_complete"):
        j.eq("log_complete", "true", "log_complete")
    return j.result(
        "finished while refused, reported after the return: succeeded, exit 0, same log"
    )


def judge_rl43(f: dict[str, str]) -> str:
    j = Judge(f)
    gj = j.num("grace_j")
    # (a) 감독자 감지(F78 ≤ 0.31) 뒤 SIGTERM 에 곧 끝난다 (b) 그 뒤 grace_J 를 다 기다려 SIGKILL
    # (d) 리더 끝 + grace_J + 2초(명세) — 감독자를 SIGKILL 한 순간이 리더 끝이다
    j.le(
        "a_detect",
        detect_max() + MEASURE_SLACK,
        "(a) worker kill -9 → SIGTERM-following command gone",
    )
    if gj is None:
        j.unk.append("(b) grace_J: not known")
    else:
        j.le("b_detect", gj + detect_max() + MEASURE_SLACK, "(b) SIGTERM-ignoring command gone")
        j.ge("b_detect", gj - 0.5, "(b) SIGTERM-ignoring command outlived its grace")
        j.le(
            "d_dead",
            gj + RL43_D_AFTER_GRACE + MEASURE_SLACK,
            "(d) supervisor kill -9 → command gone",
        )
    j.eq("e_alive", 1, "(e) plain job alive after the kill")
    j.eq("a_final", "lost", "(a) state after G")
    j.eq("a_code", "worker_unreachable", "(a) summary_code")
    j.eq("a_hold", 1, "(a) hold")
    j.eq("b_hold", 1, "(b) hold")
    j.eq("e_hold", 0, "(e) plain job hold")
    j.eq("d_state", "failed", "(d) state")
    j.eq("d_exit", -9, "(d) exit code")
    j.eq("d_hold", 0, "(d) hold")
    j.eq("d_state_after_cancel", "failed", "(d) state after rcm cancel")
    j.eq("x7_exit", 7, "exit 7 under the supervisor")
    j.eq("x7_wait_rc", 1, "exit 7: rcm wait exit")
    j.eq("k_exit", -9, "command SIGKILL: exit code")
    j.eq("release_rc", 0, "rcm release-hold exit")
    j.eq("z_started", 1, "the held group's waiting job starts after the release")
    return j.result(
        f"(a) {f.get('a_detect')}s (b) {f.get('b_detect')}s (grace_J {f.get('grace_j')}) "
        f"(d) {f.get('d_dead')}s, plain job alive, holds after G, released by rcm release-hold"
    )


def judge_rl45(f: dict[str, str]) -> str:
    j = Judge(f)
    j.eq("z_state", "queued", "held job state")
    j.eq("z_reason", "needs_action", "held job reason")
    j.eq("z_action", "irreversible_hold", "action.code")
    j.eq("z_blocked_by_ok", 1, "blocked_by.job_id is the hold's job")
    j.nulls("z_remaining", "blocked_by.remaining_seconds")
    j.nulls("z_finish_at", "estimate.finish_at")
    j.eq("status_holds_ok", 1, "/api/status irreversible_holds has both")
    j.eq("health_holds_ok", 1, "/api/health irreversible_holds has both")
    j.eq("health_ok_same", 1, "/api/health ok unchanged by the holds")
    j.eq("wait_action_rc", 4, "rcm wait --return-on-action exit")
    j.eq("wait_timeout_rc", 3, "rcm wait --timeout exit")
    j.eq("check_rc_same", 1, "rcm check exit unchanged")
    j.eq("doctor_rc_same", 1, "rcm doctor exit unchanged")
    j.eq("client_release_rc", 2, "client token release-hold exit")
    j.eq("admin_release_rc", 0, "admin release-hold exit")
    j.eq("released_by", f.get("admin_name", "?"), "hold.released_by")
    j.eq("release_reason_ok", 1, "hold.release_reason")
    j.eq("nohold_release_rc", 1, "release-hold of a job with no hold")
    if j.gate("pr3", "human hold lines"):
        j.eq("wait_line", 1, "rcm wait names the group and the job to release")
        j.ge("check_lines", 2, "rcm check group hold warnings")
        j.ge("doctor_lines", 1, "rcm doctor needs action line")
        j.ge("doctor_json_holds", 1, "doctor --json irreversible_holds")
        j.eq("owner_line", 1, "rcm wait of the hold's own job says its group is held")
        j.eq("eta_held", 1, "rcm eta --job of the held job names the hold")
        j.eq("eta_no_ahead", 1, "rcm eta --job of the held job counts no jobs ahead")
        j.eq("top_empty_held", 1, "rcm top with an empty queue and a hold makes no start promise")
    return j.result(
        "held job needs_action with the hold's job and no ETA; both holds in status and health; "
        "--return-on-action 4; check · doctor exits unchanged; client 2, admin 0, no hold 1"
    )


def judge_rl45_web(f: dict[str, str]) -> str:
    """「확인이 필요한 작업」: 띠는 `stuck_max` 줄까지 그리고 넘치면 「and N more」, 「nothing else
    needs a look」은 다 그렸을 때만이다(PR3 P6). `stuck_min` 은 하네스가 만든 줄(홀드 + 막힌 대기
    잡) 수 — 띠가 센 줄(보인 것 + N)이 그보다 적으면 줄을 잃은 것이다."""
    base = judge_web(f, band_max=None, needles=("hold_a", "hold_b"))
    if not base.startswith("PASS"):
        return base
    j = Judge(f)
    j.ge("en_chips", 1, "held job chip")
    need, cap = j.num("stuck_min"), j.num("stuck_max")
    shown, more, done = j.num("en_stuck_n"), j.num("en_more_n"), j.num("en_nothing_else")
    text = (f.get("en_stuck_text") or "")[:300]
    if None in (need, cap, shown, more, done):
        j.unk.append("Needs a look lines: not observed")
    elif need <= cap:  # type: ignore[operator]
        j.unk.append(f"Needs a look had {need:g} lines, the case needs more than {cap:g}")
    else:
        if shown != cap:
            j.bad.append(
                f"Needs a look shows {shown:g} lines, want {cap:g} when more wait ('{text}')"
            )
        if shown + more < need:  # type: ignore[operator]
            j.bad.append(
                f"Needs a look accounts for {shown:g} + {more:g} of at least {need:g} lines — "
                f"hidden lines are not counted ('{text}')"
            )
        if done != 0:
            j.bad.append(f"Needs a look says all is shown while lines are hidden ('{text}')")
    return j.result(
        f"both holds shown, {f.get('en_stuck_n')} lines and 'and {f.get('en_more_n')} more' "
        f"for at least {f.get('stuck_min')}"
    )


def judge_rl45_empty(f: dict[str, str]) -> str:
    """대기열이 비었고 그룹 홀드가 열려 있으면 웹은 「바로 시작」을 약속하지 않는다(PR3 P6-2)."""
    j0 = Judge(f)
    if not j0.gate("pr3", "empty queue with a held group"):
        return j0.result("")
    base = judge_web(f, band_max=None, needles=("empty_held",))
    j = Judge(f)
    if j.v("queue_empty") != "1" or j.v("hold_open") != "1":
        return (
            f"UNKNOWN the case was not set up: queue empty {f.get('queue_empty')}, "
            f"hold open {f.get('hold_open')}"
        )
    if not base.startswith("PASS"):
        return base
    j.eq("en_empty_hint", 0, "the page still promises an immediate start")
    return j.result("empty queue with an open hold: the page says the held group does not start")


def judge_rl50(f: dict[str, str]) -> str:
    j = Judge(f)
    j.ge("held", 1, "relay held a heartbeat")
    j.ge("refused", 1, "relay refused a later heartbeat")
    j.eq("x_state", "running", "job state after the late heartbeat")
    j.eq("x_alive", 1, "job process alive")
    j.eq("x_hold", 0, "hold")
    j.eq("second_state", "queued", "same-group second job")
    return j.result("the late heartbeat closed nothing; the second same-group job waited")


def judge_rl51(f: dict[str, str]) -> str:
    j = Judge(f)
    gj = j.num("grace_j")
    hb = float(f.get("HB", "1") or 1)
    if gj is None:
        j.unk.append("grace_J: not known")
    else:
        # 명세: 남은 자식이 첫 SIGTERM + grace_J + 0.5초 안에 사라진다. 워커가 죽은 경우는 그
        # SIGTERM 이 감독자 감지(F78) 뒤라 kill -9 에서 잰다. 산 경우는 첫 SIGTERM 을 볼 수 없어
        # `rcm cancel` 이 돌아온 때부터 재므로 다음 heartbeat(≤ HB)와 그 왕복(CANCEL_PATH)이
        # 앞에 붙는다
        after_cancel = gj + hb + CANCEL_PATH + RL51_AFTER_GRACE
        j.le("b1_gone", after_cancel, "(b) worker alive, cancel → ignoring child gone")
        after_kill = gj + detect_max() + RL51_AFTER_GRACE + MEASURE_SLACK
        j.le("b2_gone", after_kill, "(b) worker kill -9 → ignoring child gone")
        j.le("c_gone", after_cancel, "(c) command used most of its grace → child gone")
    j.eq("b1_state", "cancelled", "(b) state after the cancel")
    j.eq("c_state", "cancelled", "(c) state after the cancel")
    return j.result(
        f"ignoring child gone: alive worker {f.get('b1_gone')}s, killed worker "
        f"{f.get('b2_gone')}s, slow exit {f.get('c_gone')}s (grace_J {f.get('grace_j')})"
    )


def judge_f78(f: dict[str, str]) -> str:
    j = Judge(f)
    gj = j.num("grace_j")
    j.le("a_detect", detect_max() + MEASURE_SLACK, "parent SIGKILL → SIGTERM command gone")
    b = j.num("b_detect")
    if gj is None or b is None:
        j.unk.append("ignoring command: not measured")
    elif b - gj > detect_max() + GRACE_OVERSHOOT:
        j.bad.append(f"ignoring command gone {b - gj:.3f}s after grace_J")
    j.le("sup_cpu_30s", 0.1, "supervisor CPU over 30 s")
    j.le("python_I_start", 0.1, "python -I start")
    j.le("ps_cost", 0.05, "ps -A -o pid= -o pgid= once")
    d, s, st = j.num("true_direct"), j.num("true_supervised"), j.num("python_I_start")
    if d is None or s is None or st is None:
        j.unk.append("supervised /usr/bin/true: not measured")
    elif s - d - st > 0.1:
        j.bad.append(f"supervised /usr/bin/true ends {s - d - st:.3f}s late (start cost taken off)")
    return j.result(
        f"detect {f.get('a_detect')}s · ignoring {f.get('b_detect')}s − grace_J "
        f"{f.get('grace_j')} · "
        f"CPU {f.get('sup_cpu_30s')}s/30s · -I start {f.get('python_I_start')}s · "
        f"ps {f.get('ps_cost')}s · true {f.get('true_direct')}s → {f.get('true_supervised')}s"
    )


JUDGES = {
    "RL1": judge_rl1,
    "RL2": judge_rl2,
    "RL5": judge_rl5,
    "RL8": judge_rl8,
    "RL10": judge_rl10,
    "RL15": judge_rl15,
    "RL18": judge_rl18,
    "RL28": judge_rl28,
    "RL28-web": judge_rl28_web,
    "RL28-cancel": judge_rl28_cancel,
    "RL29": judge_rl29,
    "RL29-web": judge_rl29_web,
    "RL34": judge_rl34,
    "RL40": judge_rl40,
    "RL42": judge_rl42,
    "RL43": judge_rl43,
    "RL45": judge_rl45,
    "RL45-web": judge_rl45_web,
    "RL45-empty": judge_rl45_empty,
    "RL50": judge_rl50,
    "RL51": judge_rl51,
    "F78": judge_f78,
}


def judge(step: str, facts: dict[str, str]) -> str:
    fn = JUDGES.get(step)
    if fn is None:
        return f"UNKNOWN no judge for {step}"
    return fn(facts)


def cmd_judge(step: str, path: str) -> int:
    """판정 한 줄. 판정 함수가 예외로 끝나면 빈 출력이 아니라 UNKNOWN 이다."""
    try:
        line = judge(step, read_facts(path))
    except Exception as e:  # noqa: BLE001 — 판정이 깨진 것은 모르는 것이다
        line = f"UNKNOWN the judge crashed: {type(e).__name__}: {str(e)[:200]}"
    print(line)
    return 0


COMMANDS = {
    "job": cmd_job,
    "jobdoc": cmd_jobdoc,
    "waitstate": cmd_waitstate,
    "status": cmd_status,
    "holds": cmd_holds,
    "openjobs": cmd_openjobs,
    "whynot": cmd_whynot,
    "health": cmd_health,
    "getraw": cmd_getraw,
    "sse": cmd_sse,
    "alive": cmd_alive,
    "waitdead": cmd_waitdead,
    "killtime": cmd_killtime,
    "cputime": cmd_cputime,
    "spawn": cmd_spawn,
    "run": cmd_run,
    "firstline": cmd_firstline,
    "inorder": cmd_inorder,
    "count": cmd_count,
    "sha": cmd_sha,
    "epoch": cmd_epoch,
    "now": cmd_now,
    "sub": cmd_sub,
    "sql": cmd_sql,
    "render": cmd_render,
    "needle": cmd_needle,
    "putsecret": cmd_putsecret,
    "maskedsha": cmd_masked_sha,
    "relay": cmd_relay,
    "web": cmd_web,
    "f78": cmd_f78,
    "judge": cmd_judge,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: m5tb_helper.py {{{','.join(COMMANDS)}}} …", file=sys.stderr)
        return 2
    args = [a for a in argv[1:] if a != "--"] if argv[0] not in ("spawn", "run") else _argv(argv)
    return COMMANDS[argv[0]](*args)


def _argv(argv: list[str]) -> list[str]:
    """spawn · run: `<a> <b> -- <명령…>` — 앞의 둘과 명령을 가른다."""
    cut = argv.index("--")
    return [*argv[1:cut], *argv[cut + 1 :]]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PYEOF
}

# ── 실행: 기록 · 판정 ───────────────────────────────────────────────────────

log() {
  printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | sed "s|${REAL_HOME:-/nonexistent}|<HOME>|g" |
    tee -a "$OUT/timeline.txt" >&2
}

h() { "$PY" -I "$H" "$@"; }

verdict() { # verdict <id> <PASS|FAIL|UNKNOWN|SKIP> <text>
  printf '%-9s %-7s %s\n' "$1" "$2" "$3" | sed "s|$REAL_HOME|<HOME>|g" | tee -a "$OUT/summary.txt"
  [ "$2" != FAIL ] || FAILS=$((FAILS + 1))
}

verdict_line() { # verdict_line <id> <「PASS 글」 한 줄>
  verdict "$1" "${2%% *}" "${2#* }"
}

fact() { # fact <id> <key> <value…> — 사실 한 줄(값의 줄바꿈은 공백으로)
  local id=$1 key=$2
  shift 2
  printf '%s=%s\n' "$key" "$(printf '%s' "$*" | tr '\n' ' ')" >>"$OUT/facts/$id.txt"
}

judge() { # judge <id> — 그 단계의 사실을 판정해 한 줄로
  verdict_line "$1" "$(h judge "$1" "$OUT/facts/$1.txt")"
}

common_facts() { # common_facts <id> — 시험 값과 이 빌드에 PR2 · PR3 가 있는지
  fact "$1" G "$G"
  fact "$1" T "$T"
  fact "$1" HB "$HB"
  fact "$1" grace_j "$GRACE_J"
  fact "$1" pr2 "$HAS_PR2"
  fact "$1" pr3 "$HAS_PR3"
}

# ── 실행: 프로세스 ──────────────────────────────────────────────────────────

wait_file() { # wait_file <path> <seconds>
  local i
  for i in $(seq 1 $(($2 * 10))); do
    [ -s "$1" ] && return 0
    sleep 0.1
  done
  return 1
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

# spawn <이름> <로그> <명령…> — 도우미 아래 새 세션으로 띄우고 그 pid · 시작 시각을 적는다. SPAWNED=pid
spawn() {
  local name=$1 logf=$2 pidf="$B/run/$1.pid"
  shift 2
  rm -f "$pidf" # 다시 띄우는 서버의 옛 pid 를 읽지 않게
  "$PY" -I "$H" spawn "$logf" "$pidf" -- "$@" &
  LAUNCHERS+=($!)
  echo "$!" >>"$B/run/launchers.txt"
  wait_file "$pidf" 20 || return 1
  SPAWNED=$(cat "$pidf")
  printf '%s\t%s\t%s\n' "$name" "$SPAWNED" "$(proc_start "$SPAWNED")" >>"$B/run/procs.tsv"
}

recorded_start() { # recorded_start <pid> — procs.tsv 에 적은 시작 시각
  awk -F '\t' -v p="$1" '$2 == p { s = $3 } END { print s }' "$B/run/procs.tsv" 2>/dev/null || true
}

# 우리가 띄운 프로세스에만 신호(명령줄 · 시작 시각 확인). 그룹째 보낼 때는 group
signal_ours() { # signal_ours <SIG> <pid> [group]
  local sig=$1 pid=$2
  is_our_proc "$pid" "$B" "$(recorded_start "$pid")" || {
    log "not signalling $pid: not a process this harness started"
    return 1
  }
  if [ "${3:-}" = group ]; then kill "-$sig" -- "-$pid" 2>/dev/null || true; fi
  kill "-$sig" "$pid" 2>/dev/null || true
}

stop_ours() { # stop_ours <pid> — TERM, 15초 안에 안 끝나면 그룹째 KILL
  local pid=${1:-} i
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 0
  signal_ours TERM "$pid" || return 0
  for i in $(seq 1 75); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.2
  done
  signal_ours KILL "$pid" group || true
}

# 잡 pid(표지 파일) — 처음 읽을 때 시작 시각을 적는다. pid_of <잡> [초]
pid_of() {
  local f="$MARKS/$1.pid" p
  wait_file "$f" "${2:-30}" || {
    echo ""
    return 0
  }
  p=$(tr -d ' \n' <"$f")
  [ -s "$MARKS/$1.start" ] || proc_start "$p" >"$MARKS/$1.start"
  echo "$p"
}

kill_job_group() { # kill_job_group <pid> <start file> — 잡 그룹을 KILL(시작 시각이 같을 때만)
  local pid=$1 rec
  rec=$(cat "$2" 2>/dev/null || true)
  is_our_job_pid "$pid" "$rec" || return 0
  kill -KILL -- "-$pid" 2>/dev/null || true
  kill -KILL "$pid" 2>/dev/null || true
}

alive() { if h alive "$1"; then echo 1; else echo 0; fi; }

server_start() {
  spawn server "$OUT/server.log" "$RCM" serve --config "$CONF" || return 1
  S_PID=$SPAWNED
  wait_http "$URL" 30
}

server_stop() { stop_ours "${S_PID:-}"; }

relay_start() {
  mkdir -p "$RCTL"
  spawn relay "$OUT/relay.log" "$PY" -I "$H" relay "$RCTL" "$P" || return 1
  R_PID=$SPAWNED
  wait_file "$RCTL/port" 10 || return 1
  RURL="http://127.0.0.1:$(cat "$RCTL/port")"
  RSEQ=0
}

relay_do() { # relay_do <명령…> — 중계에 명령 하나, 응답(ack)까지 기다린다
  local i
  [ -n "${R_PID:-}" ] || return 1
  RSEQ=$((RSEQ + 1))
  printf '%s\n' "$*" >"$RCTL/tmp.$RSEQ"
  mv "$RCTL/tmp.$RSEQ" "$RCTL/cmd.$RSEQ"
  for i in $(seq 1 100); do
    [ -s "$RCTL/ack.$RSEQ" ] && return 0
    sleep 0.1
  done
  log "relay did not answer '$*'"
  return 1
}

relay_state() { # relay_state <held|refused|slow|accepted>
  sed -n "s/.*$1=\([0-9]*\).*/\1/p" "$RCTL/state" 2>/dev/null || echo 0
}

wait_relay() { # wait_relay <key> <at least> <seconds>
  local i
  for i in $(seq 1 $(($3 * 10))); do
    [ "$(relay_state "$1")" -ge "$2" ] 2>/dev/null && return 0
    sleep 0.1
  done
  return 1
}

new_token() { # new_token <file> <name> [--worker|--admin]
  local file=$1
  shift
  [ -s "$file" ] && return 0
  (umask 077 && "$RCM" token --config "$CONF" add "$@" 2>>"$OUT/token.err" | tail -1 >"$file") || true
  [ -s "$file" ]
}

# 워커 하나: worker_start <이름> <relay|direct> [grace] [데이터 폴더] → W_PID W_CONF W_DATA W_LOG
worker_start() {
  local name=$1 via=$2 grace=${3:-$GRACE_J} url
  WN=$((WN + 1))
  W_DATA=${4:-$B/w/$name-$WN}
  case "$via" in relay) url=$RURL ;; *) url=$URL ;; esac
  new_token "$B/tok/w-$name" "$name" --worker || {
    log "worker token $name failed"
    return 1
  }
  mkdir -p "$W_DATA"
  W_CONF="$B/w/$name-$WN.toml"
  W_LOG="$OUT/worker-$name-$WN.log"
  (
    umask 077
    printf 'server = "%s"\npool = "%s"\nlanes = %s\ndata_dir = "%s"\ngrace_seconds = %s\ntoken = "%s"\n' \
      "$url" "$POOL" "${W_LANES:-1}" "$W_DATA" "$grace" "$(cat "$B/tok/w-$name")" >"$W_CONF"
  )
  spawn "worker-$name-$WN" "$W_LOG" "$RCM" --no-follow worker --config "$W_CONF" || return 1
  W_PID=$SPAWNED
  printf '%s\t%s\t%s\t%s\n' "$name" "$W_PID" "$W_CONF" "$W_DATA" >>"$B/run/workers.tsv"
  WORKER_PIDS+=("$W_PID")
  wait_registered "$name" 30 || {
    log "worker $name did not register (out/$(basename "$W_LOG"))"
    return 1
  }
}

wait_registered() { # wait_registered <이름> <초>
  local i
  for i in $(seq 1 $(($2 * 5))); do
    h status "$URL" "$CTOK" "$B/run/status.json" >/dev/null 2>&1 || true
    if "$PY" -I - "$B/run/status.json" "$1" <<'EOF'; then return 0; fi
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except ValueError:
    sys.exit(1)
ws = (d.get("server") or {}).get("workers") or []
sys.exit(0 if any(w.get("worker") == sys.argv[2] and w.get("state") in ("idle", "busy") for w in ws) else 1)
EOF
    sleep 0.2
  done
  return 1
}

conf_of() { # conf_of <워커 이름> — 그 이름의 마지막 설정 파일
  awk -F '\t' -v n="$1" '$1 == n { c = $3 } END { print c }' "$B/run/workers.tsv"
}

client() { # client <rcm 인자…> — 시험 클라이언트 토큰으로
  (
    export RCM_SERVER="$URL" RCM_TOKEN
    RCM_TOKEN=$(cat "$CTOK")
    cd "$SRC" && "$RCM" --no-follow "$@"
  )
}

admin() { # admin <rcm 인자…> — 시험 admin 토큰으로
  (
    export RCM_SERVER="$URL" RCM_TOKEN
    RCM_TOKEN=$(cat "$ATOK")
    cd "$SRC" && "$RCM" --no-follow "$@"
  )
}

submit() { # submit <프리셋> → 잡 번호
  local raw rc=0
  raw=$(client run "$1" --by acceptance --no-wait --no-join 2>>"$OUT/submit.err") || rc=$?
  if [ "$rc" != 0 ]; then
    log "rcm run $1 exited $rc: $(tail -1 "$OUT/submit.err")"
    echo ""
    return 0
  fi
  printf '%s' "$raw" | "$PY" -I -c 'import json, sys; print(json.loads(sys.stdin.read())["job_id"])' ||
    echo ""
}

web() { # web <태그> [역할=잡,…] — 390px · 두 언어. CDP Chrome 은 진짜 HOME 이 있어야 http 페이지를 연다(test-server.md)
  HOME="$REAL_HOME" "$PY" -I "$H" web "$URL" "$OUT/web" "$1" 390 "${2:-}" 2>>"$OUT/web.err" || true
}

has_line() { # has_line <파일> <글> → 1 · 0 — 그 글이 그대로(고정 문자열) 들어 있나
  if grep -qF -- "$2" "$1" 2>/dev/null; then echo 1; else echo 0; fi
}

has_line_in() { # has_line_in <글> <찾을 글> → 1 · 0 — 고정 문자열이 들어 있나
  if [[ "$1" == *"$2"* ]]; then echo 1; else echo 0; fi
}

jv() { # jv <잡> <키> — 잡 문서의 칸 하나(점으로 깊이)
  h job "$URL" "$CTOK" "$1" "$2" 2>/dev/null || echo "<absent>"
}

waitstate() { # waitstate <잡> <초> <terminal|상태,상태>
  h waitstate "$URL" "$CTOK" "$1" "$2" "$3" 2>/dev/null || true
}

has_hold() { # has_hold <잡> → 1 · 0 · <absent>(못 읽음)
  local got
  got=$(h holds "$URL" "$CTOK")
  [ "$got" = unknown ] && {
    echo "<absent>"
    return 0
  }
  case " $got " in *" $1 "*) echo 1 ;; *) echo 0 ;; esac
}

wait_unheld() { # wait_unheld <잡> <초> → 1 이면 풀림
  local i
  for i in $(seq 1 $(($2 * 2))); do
    [ "$(has_hold "$1")" = 0 ] && {
      echo 1
      return 0
    }
    sleep 0.5
  done
  echo 0
}

rwait() { # rwait <잡> <초> <이름> → RW_RC, out/<이름>.err(줄마다 시각)
  RW_RC=0
  (
    export RCM_SERVER="$URL" RCM_TOKEN
    RCM_TOKEN=$(cat "$CTOK")
    h run "$OUT/$3.json" "$OUT/$3.err" -- "$RCM" --no-follow wait --job "$1" --timeout "$2" "${@:4}"
  ) || RW_RC=$?
}

now() { h now; }

db() { h sql "$DB" "$@"; }

hold_rows() { db "SELECT count(*) FROM irreversible_holds WHERE job_id=?" "$1"; }

record_lost() { # record_lost <잡> — 끝난 lost 잡의 요약 코드를 RL29 몫으로 적는다
  local st code
  st=$(jv "$1" state)
  [ "$st" = lost ] || return 0
  code=$(jv "$1" summary_code)
  printf '%s %s\n' "$code" "$1" >>"$OUT/lost.tsv"
}

reset_world() { # 단계 사이: 중계를 풀고 워커를 멈추고 남은 잡 그룹 · 홀드를 치운다
  local p f ids
  relay_do resume >/dev/null 2>&1 || true
  relay_do unrefuse >/dev/null 2>&1 || true
  relay_do fast >/dev/null 2>&1 || true
  relay_do release >/dev/null 2>&1 || true
  for p in ${WORKER_PIDS[@]+"${WORKER_PIDS[@]}"}; do stop_ours "$p"; done
  WORKER_PIDS=()
  for f in "$MARKS"/*.pid "$MARKS"/*.child; do
    [ -s "$f" ] || continue
    p=$(tr -d ' \n' <"$f")
    kill_job_group "$p" "${f%.*}.start"
  done
  # 워커를 멈춘 뒤 남은 잡(대기 · 도는 중 · 예약)을 취소한다 — 남겨 두면 다음 단계의 첫 워커가
  # 집어 그 뒤 단계마다 잡이 하나씩 밀린다(2026-11-27 첫 실기: #19 rl15-ship 이 rl1a 로)
  ids=$(h openjobs "$URL" "$CTOK")
  if [ "$ids" = unknown ]; then
    log "reset: the queue could not be read — leftover jobs may reach the next step"
  else
    for p in $ids; do
      printf 'reset: cancel leftover #%s %s %s\n' "$p" "$(jv "$p" preset)" "$(jv "$p" state)" >>"$OUT/reset.log"
      client cancel "$p" >>"$OUT/reset.log" 2>&1 || admin cancel "$p" >>"$OUT/reset.log" 2>&1 || true
      waitstate "$p" 20 terminal >/dev/null
    done
  fi
  ids=$(h holds "$URL" "$CTOK")
  if [ "$ids" != unknown ]; then
    for p in $ids; do
      admin release-hold "$p" --reason "acceptance reset" >>"$OUT/reset.log" 2>&1 || true
    done
  fi
}

not_started() { # not_started <ID> <워커,워커> <잡…> — 시작하지 않은 잡의 이유를 판정 한 줄로
  local id=$1 workers=$2
  shift 2
  verdict_line "$id" "$(h whynot "$URL" "$CTOK" "$workers" "$@" || echo "UNKNOWN jobs did not start: $*")"
}

# ── 단계 ────────────────────────────────────────────────────────────────────

# F78 의 이 머신 값(서버 없이)
run_f78_local() {
  log "F78 local measurements"
  common_facts F78
  h f78 >>"$OUT/facts/F78.txt" 2>>"$OUT/f78.err" || log "F78 local measurement failed"
}

# RL50 — claim 응답 전에 찍은 heartbeat 이 늦게 닿는다
run_hb_order() {
  local x px y
  log "RL50 start"
  common_facts RL50
  worker_start rl50 relay || return 0
  relay_do hold /worker/heartbeat refuse
  wait_relay held 1 10 || log "RL50 no heartbeat was held"
  x=$(submit rl50-ship)
  px=$(pid_of "$x" 30)
  wait_relay refused 1 30 || log "RL50 no later heartbeat was refused"
  relay_do release
  sleep 2
  relay_do unrefuse
  sleep 3
  fact RL50 held "$(relay_state held)"
  fact RL50 refused "$(relay_state refused)"
  fact RL50 x_state "$(jv "$x" state)"
  [ -n "$px" ] && fact RL50 x_alive "$(alive "$px")"
  fact RL50 x_hold "$(has_hold "$x")"
  y=$(submit rl50-ship)
  sleep 3
  fact RL50 second_state "$(jv "$y" state)"
  judge RL50
  client cancel "$x" >/dev/null 2>&1 || true
  client cancel "$y" >/dev/null 2>&1 || true
  reset_world
}

# RL10 — 같은 토큰의 둘째 프로세스가 첫 프로세스를 펜싱한다
run_fence() {
  local x px a a_log out
  log "RL10 start"
  common_facts RL10
  worker_start rl10 direct || return 0
  a=$W_PID
  a_log=$W_LOG
  x=$(submit rl10-ship)
  px=$(pid_of "$x" 30)
  worker_start rl10 direct || log "RL10 second process did not register"
  fact RL10 b_registered "$(if kill -0 "$W_PID" 2>/dev/null; then echo 1; else echo 0; fi)"
  out=$(h waitdead "$a" 30 || true)
  log "RL10 first process gone after $out s"
  fact RL10 a_exit "$(sed -n 's/.*\[harness\] exit //p' "$a_log" | tail -1)"
  [ -n "$px" ] && fact RL10 x_dead "$(if h waitdead "$px" 10 >/dev/null; then echo 1; else echo 0; fi)"
  fact RL10 x_final "$(waitstate "$x" 30 terminal)"
  fact RL10 x_code "$(jv "$x" summary_code)"
  rwait "$x" 10 rl10-wait
  fact RL10 x_wait_rc "$RW_RC"
  fact RL10 x_hold "$(has_hold "$x")"
  record_lost "$x"
  sleep 45 # 감독자 핑퐁이 있다면 1분 안에 등록이 늘어난다
  fact RL10 registers "$(db "SELECT count(*) FROM worker_sessions WHERE worker_name=?" rl10)"
  judge RL10
  reset_world
}

# RL43 (d) · 종료 코드 7 · 명령 SIGKILL · F78 의 감독자 CPU
run_supervisor_only() {
  local x7 k pk d pd sup out
  log "RL43 (d) start"
  worker_start rl43d direct || return 0
  x7=$(submit rl43d-exit7)
  rwait "$x7" 60 rl43-x7
  fact RL43 x7_wait_rc "$RW_RC"
  fact RL43 x7_exit "$(jv "$x7" exit_code)"
  k=$(submit rl43d-ship)
  pk=$(pid_of "$k" 30)
  [ -n "$pk" ] && kill_job_group "$pk" "$MARKS/$k.start"
  waitstate "$k" 30 terminal >/dev/null
  fact RL43 k_exit "$(jv "$k" exit_code)"
  d=$(submit rl43d-ship)
  pd=$(pid_of "$d" 30)
  if [ -n "$pd" ]; then
    # 감독자는 명령 그룹의 리더다(pgid). 그 pid 가 이 단계 워커의 자식일 때만 재고 죽인다 —
    # `ps` 가 빈 줄을 주면 0 이 되어 하네스 자신의 그룹을 죽였다(격리 확인 1라운드 MED 3)
    sup=$(ps -o pgid= -p "$pd" 2>/dev/null | tr -d ' ' || true)
    if [ -n "$sup" ] && [ "$sup" != "$pd" ] && is_child_of "$sup" "$W_PID" &&
      is_our_proc "$W_PID" "$B" "$(recorded_start "$W_PID")"; then
      sleep 30
      fact F78 sup_cpu_30s "$(h cputime "$sup")"
      out=$(h killtime KILL "$sup" "$pd" $((GRACE_J + 10)) || true)
      fact RL43 d_dead "$(printf '%s\n' "$out" | sed -n "s/^$pd=//p")"
    else
      log "RL43 (d) #$d: '$sup' is not a supervisor under worker $W_PID — not signalling it"
    fi
  fi
  fact RL43 d_state "$(waitstate "$d" 30 terminal)"
  fact RL43 d_exit "$(jv "$d" exit_code)"
  fact RL43 d_hold "$(has_hold "$d")"
  client cancel "$d" >>"$OUT/rl43-cancel.log" 2>&1 || true
  sleep 2
  fact RL43 d_state_after_cancel "$(jv "$d" state)"
  reset_world
}

# RL51 — SIGTERM 을 무시하는 자식이 남는 세 경우
run_sweep() {
  local j pj child out data
  log "RL51 start"
  common_facts RL51
  worker_start rl51 direct || return 0
  data=$W_DATA
  j=$(submit rl51-child)
  pj=$(pid_of "$j" 30)
  wait_file "$MARKS/$j.child" 10 && child=$(tr -d ' \n' <"$MARKS/$j.child")
  if [ -n "${child:-}" ]; then
    proc_start "$child" >"$MARKS/$j.child.start"
    client cancel "$j" >>"$OUT/rl51.log" 2>&1 || true
    fact RL51 b1_gone "$(h waitdead "$child" $((GRACE_J + 20)) || true)"
  fi
  fact RL51 b1_state "$(waitstate "$j" 30 terminal)"
  child=""
  j=$(submit rl51-slowexit)
  pj=$(pid_of "$j" 30)
  wait_file "$MARKS/$j.child" 10 && child=$(tr -d ' \n' <"$MARKS/$j.child")
  if [ -n "${child:-}" ]; then
    proc_start "$child" >"$MARKS/$j.child.start"
    client cancel "$j" >>"$OUT/rl51.log" 2>&1 || true
    fact RL51 c_gone "$(h waitdead "$child" $((GRACE_J + 20)) || true)"
  fi
  fact RL51 c_state "$(waitstate "$j" 30 terminal)"
  child=""
  j=$(submit rl51-child)
  pj=$(pid_of "$j" 30)
  wait_file "$MARKS/$j.child" 10 && child=$(tr -d ' \n' <"$MARKS/$j.child")
  if [ -n "${child:-}" ] && is_our_proc "$W_PID" "$B" "$(recorded_start "$W_PID")"; then
    proc_start "$child" >"$MARKS/$j.child.start"
    out=$(h killtime KILL "$W_PID" "$child" $((GRACE_J + 10)))
    fact RL51 b2_gone "$(printf '%s\n' "$out" | sed -n "s/^$child=//p")"
    # 같은 폴더로 다시 띄워 죽은 세션의 그룹이 없음을 확인시킨다 — G 뒤에 홀드가 남지 않게
    worker_start rl51 direct "$GRACE_J" "$data" || true
    waitstate "$j" 60 terminal >/dev/null
  fi
  judge RL51
  reset_world
}

# RL18 — 릴리스 비밀이 어디에도 원문으로 남지 않는다
run_secret() {
  local secret j n sse_pid i rc
  log "RL18 start"
  common_facts RL18
  secret="rl18-$("$PY" -I -c 'import secrets; print(secrets.token_hex(16))')"
  (umask 077 && printf '%s' "$secret" >"$B/secret.txt")
  if ! h putsecret "$CONF" app GH_TOKEN "$B/secret.txt" 2>>"$OUT/rl18.err"; then
    verdict RL18 UNKNOWN "could not store the test secret (out/rl18.err)"
    return 0
  fi
  worker_start rl18 relay || return 0
  "$PY" -I "$H" sse "$URL" "$CTOK" 150 "$OUT/rl18-sse.bin" >/dev/null &
  sse_pid=$!
  sleep 1
  j=$(submit rl18-plan)
  waitstate "$j" 30 running >/dev/null
  for i in 1 2; do # 서버가 받은 조각의 답을 끊는다 — 워커는 같은 범위를 다시 보낸다
    n=$(relay_state slow)
    relay_do slow "/worker/jobs/$j/log" 1.0
    wait_relay slow $((n + 1)) 20 || log "RL18 no log piece was slowed"
    sleep 0.7
    relay_do stop
    sleep 0.3
    relay_do fast
    relay_do resume
    sleep 0.5
  done
  rwait "$j" 120 rl18-wait
  fact RL18 wait_rc "$RW_RC"
  # 다섯 곳마다 「읽었다」는 증거를 같이 적는다 — 실패한 명령 · 오류 본문에서 원문 0 번은
  # 「비밀 없음」이 아니다(judge_rl18)
  rc=0
  client logs "$j" >"$OUT/rl18-logs.txt" 2>"$OUT/rl18-logs.err" || rc=$?
  fact RL18 rcm_logs_rc "$rc"
  fact RL18 web_log_status "$(h getraw "$URL" "$CTOK" "/jobs/$j/log?offset=0" "$OUT/rl18-weblog.bin" 2>>"$OUT/rl18.err")"
  fact RL18 job_doc_status "$(h jobdoc "$URL" "$CTOK" "$j" "$OUT/rl18-job.json" 2>>"$OUT/rl18.err")"
  sleep 2 # 끝 사건까지 SSE 에 실리게
  kill "$sse_pid" 2>/dev/null || true
  wait "$sse_pid" 2>/dev/null || true
  fact RL18 in_log "$(h count "$DATA/jobs/$j/log.txt" "$secret")"
  fact RL18 in_rcm_logs "$(h count "$OUT/rl18-logs.txt" "$secret")"
  fact RL18 in_web_log "$(h count "$OUT/rl18-weblog.bin" "$secret")"
  fact RL18 in_job_doc "$(h count "$OUT/rl18-job.json" "$secret")"
  fact RL18 in_sse "$(h count "$OUT/rl18-sse.bin" "$secret")"
  fact RL18 sse_bytes "$(wc -c <"$OUT/rl18-sse.bin" 2>/dev/null | tr -d ' ' || echo 0)"
  # 그 글에 있어야 할 것: log.txt · rcm logs · 웹 로그는 가린 값, 잡 문서는 프리셋 이름, SSE 는 그 잡의
  # 사건(`"job_id":N,` — core 의 job_changed 사건)
  fact RL18 masked_seen "$(h count "$DATA/jobs/$j/log.txt" "****")"
  fact RL18 rcm_logs_seen "$(h count "$OUT/rl18-logs.txt" "****")"
  fact RL18 web_log_seen "$(h count "$OUT/rl18-weblog.bin" "****")"
  fact RL18 job_doc_seen "$(h count "$OUT/rl18-job.json" rl18-plan)"
  fact RL18 sse_seen "$(h count "$OUT/rl18-sse.bin" "\"job_id\":$j,")"
  fact RL18 log_equals_masked "$(if [ "$(h sha "$DATA/jobs/$j/log.txt")" = "$(h maskedsha "$B/secret.txt" 600)" ]; then echo 1; else echo 0; fi)"
  fact RL18 resend_seen "$(if grep -q retrying "$W_LOG"; then echo 1; else echo 0; fi)"
  fact RL18 log_complete "$(jv "$j" log_complete)"
  judge RL18
  reset_world
}

# RL28 — 유예 중에 요청자가 보는 것. 잡 셋: y(유예 중 · 이어 감), y2(유예 중에 취소 · 결정 322),
# y3(중계를 거치지 않는 워커 — 웹 행의 대조군: 시간을 센다)
run_grace_view() {
  local y py y2 py2 y3 py3 wpid wpid2 t_pause t_resume a b i samples lang role cls grey run ok
  log "RL28 start"
  common_facts RL28
  common_facts RL28-cancel
  worker_start rl28 relay || return 0
  worker_start rl28b relay || return 0
  y=$(submit rl28-plain)
  py=$(pid_of "$y" 30)
  y2=$(submit rl28-plain)
  py2=$(pid_of "$y2" 30)
  # 대조군 워커는 두 잡이 중계 워커에 붙은 뒤에 띄운다 — 먼저 띄우면 y · y2 를 집을 수 있다
  worker_start rl28c direct || true
  y3=$(submit rl28-plain)
  py3=$(pid_of "$y3" 30)
  for i in "$y" "$y2"; do
    (
      export RCM_SERVER="$URL" RCM_TOKEN
      RCM_TOKEN=$(cat "$CTOK")
      h run "$OUT/rl28-wait-$i.json" "$OUT/rl28-wait-$i.err" -- "$RCM" --no-follow wait --job "$i" --timeout 300
    ) &
    if [ "$i" = "$y" ]; then wpid=$!; else wpid2=$!; fi
  done
  sleep 2
  t_pause=$(now)
  relay_do pause
  sleep $((T + 5))
  h status "$URL" "$CTOK" "$OUT/rl28-status.json" || true
  fact RL28 reason "$(jv "$y" reason)"
  fact RL28 finish_at "$(jv "$y" estimate.finish_at)"
  fact RL28 state_during "$(jv "$y" state)"
  client top >"$OUT/rl28-top.txt" 2>&1 || true
  fact RL28 top_text "$(has_line "$OUT/rl28-top.txt" "$LINE_LOST_CONTACT")"
  fact RL28 top_raw "$(has_line "$OUT/rl28-top.txt" "$PR3_REASON")"
  client eta --job "$y" >"$OUT/rl28-eta.txt" 2>&1 || true
  fact RL28 eta_text "$(has_line "$OUT/rl28-eta.txt" "$LINE_LOST_CONTACT")"
  samples=""
  for i in 1 2 3 4 5; do
    samples="$samples$(h health "$URL" "$CTOK")
"
    sleep 1
  done
  printf '%s' "$samples" >"$OUT/rl28-health.txt"
  fact RL28 health_stable "$(if [ "$(printf '%s' "$samples" | sort -u | grep -c .)" = 1 ]; then echo 1; else echo 0; fi)"
  a=$(now)
  rwait "$y" 8 rl28-wait-timeout
  b=$(now)
  fact RL28 wait_timeout_rc "$RW_RC"
  fact RL28 wait_timeout 8
  fact RL28 wait_timeout_secs "$(h sub "$b" "$a")"
  # 유예 중에 y2 를 취소한다 — 워커가 돌아오면 멈추고, 그 전에는 「이어진다」고 말하지 않는다
  client cancel "$y2" >>"$OUT/rl28-cancel.log" 2>&1 && fact RL28-cancel cancel_rc 0 || fact RL28-cancel cancel_rc $?
  sleep 2
  fact RL28-cancel state_after_cancel "$(jv "$y2" state)"
  fact RL28-cancel reason_after_cancel "$(jv "$y2" reason)"
  client top >"$OUT/rl28-top-cancel.txt" 2>&1 || true
  fact RL28-cancel top_cancel "$(has_line "$OUT/rl28-top-cancel.txt" "$LINE_GRACE_CANCEL")"
  client eta --job "$y2" >"$OUT/rl28-eta-cancel.txt" 2>&1 || true
  fact RL28-cancel eta_cancel "$(has_line "$OUT/rl28-eta-cancel.txt" "$LINE_GRACE_CANCEL")"
  rwait "$y2" 3 rl28-wait-cancel-fresh
  fact RL28-cancel fresh_line "$(has_line "$OUT/rl28-wait-cancel-fresh.err" "$LINE_GRACE_CANCEL")"
  fact RL28-cancel fresh_goes_on "$(has_line "$OUT/rl28-wait-cancel-fresh.err" "$LINE_GRACE_GOES_ON")"
  web rl28 "grace=$y,cancel=$y2,live=$y3" >"$OUT/facts/RL28-web.txt"
  for lang in en ko; do
    fact RL28-web "${lang}_raw" "$(if grep -q worker_unreachable "$OUT/web/rl28-$lang.txt" 2>/dev/null; then echo 1; else echo 0; fi)"
    # 레인 class — 끊긴 행은 회색(WEB_LANE_UNHEARD)이고 도는 색(WEB_LANE_LIVE)이 아니다. 대조군은 도는 색
    for role in grace cancel live; do
      cls=" $(sed -n "s/^${lang}_${role}_cls=//p" "$OUT/facts/RL28-web.txt" | tail -1) "
      case "$cls" in *" $WEB_LANE_UNHEARD "*) grey=1 ;; *) grey=0 ;; esac
      case "$cls" in *" $WEB_LANE_LIVE "*) run=1 ;; *) run=0 ;; esac
      if [ "$role" = live ]; then ok=$run; elif [ "$grey$run" = 10 ]; then ok=1; else ok=0; fi
      fact RL28-web "${lang}_${role}_lane" "$ok"
    done
  done
  # 이유 칸의 글(title 의 전문) — 영어 화면이 i18n 의 문장을 그렸나
  fact RL28-web found_grace_said "$(sed -n 's/^en_grace_reason=//p' "$OUT/facts/RL28-web.txt" | tail -1 | grep -cF -- "$WEB_GRACE_GOES_ON" || true)"
  fact RL28-web found_cancel_said "$(sed -n 's/^en_cancel_reason=//p' "$OUT/facts/RL28-web.txt" | tail -1 | grep -cF -- "$WEB_GRACE_CANCEL" || true)"
  # 분단은 적어도 40초 — 일반 잡이라 자기 멈춤은 없고, G 보다 한참 짧다
  sleep "$(h sub "$(now)" "$t_pause" | awk -v w=40 '{ d = w - $1; print (d > 0 ? int(d) : 0) }')"
  t_resume=$(now)
  relay_do resume
  for i in $(seq 1 60); do
    grep -qF -- "$LINE_BACK_GOES_ON" "$OUT/rl28-wait-$y.err" 2>/dev/null && break
    sleep 0.5
  done
  fact RL28 lost_line_delay "$(h sub "$(h firstline "$OUT/rl28-wait-$y.err" "$LINE_LOST_CONTACT")" "$t_pause")"
  fact RL28 back_line_delay "$(h sub "$(h firstline "$OUT/rl28-wait-$y.err" "$LINE_BACK_GOES_ON")" "$t_resume")"
  # y2 — 다시 붙은 워커가 취소 요청을 보고 멈춘다. 기다리던 rcm wait 은 그 사이 말을 바로잡았다
  fact RL28-cancel final "$(waitstate "$y2" 30 terminal)"
  wait "$wpid2" 2>/dev/null || true
  fact RL28-cancel bg_cancel_line "$(has_line "$OUT/rl28-wait-$y2.err" "$LINE_CANCEL_REQUESTED")"
  fact RL28-cancel bg_back_goes_on "$(has_line "$OUT/rl28-wait-$y2.err" "$LINE_BACK_GOES_ON")"
  fact RL28-cancel bg_back_cancel "$(has_line "$OUT/rl28-wait-$y2.err" "$LINE_BACK_CANCEL")"
  judge RL28
  judge RL28-web
  judge RL28-cancel
  client cancel "$y" >/dev/null 2>&1 || true
  client cancel "$y3" >/dev/null 2>&1 || true
  wait "$wpid" 2>/dev/null || true
  for i in "$py" "$py2" "$py3"; do
    [ -n "$i" ] && h waitdead "$i" 20 >/dev/null || true
  done
  reset_world
}

# RL34 (a) — heartbeat 가 매번 시한까지 매달린다(워커 grace 기본 · 큰 값)
run_self_stop_timing() {
  local m grace x px t
  log "RL34 start"
  common_facts RL34
  for m in d b; do
    if [ "$m" = d ]; then grace=$GRACE_J; else grace=$BIG_GRACE; fi
    worker_start "rl34$m" relay "$grace" || continue
    x=$(submit rl34-ship)
    px=$(pid_of "$x" 30)
    [ -n "$px" ] || continue
    sleep 2
    relay_do pause
    t=$(h waitdead "$px" $((G + 20)) || true)
    fact RL34 "${m}_stop_after" "$t"
    fact RL34 "${m}_state_at_stop" "$(jv "$x" state)"
    fact RL34 "${m}_threshold" "$(sed -n "s/.*#$x: no answer from the server for \([0-9]*\)s.*/\1/p" "$W_LOG" | head -1)"
    t=$(sed -n "s/.*#$x: grace_seconds [0-9]* lowered to \([0-9]*\).*/\1/p" "$W_LOG" | head -1 || true)
    fact RL34 "${m}_grace_j" "${t:-$grace}"
    [ "$m" = b ] && fact RL34 b_lowered "$(if [ -n "$t" ]; then echo 1; else echo 0; fi)"
    relay_do resume
    waitstate "$x" 60 terminal >/dev/null
    fact RL34 "${m}_code" "$(jv "$x" summary_code)"
    rwait "$x" 10 "rl34-$m-wait"
    fact RL34 "${m}_wait_rc" "$RW_RC"
    fact RL34 "${m}_hold" "$(has_hold "$x")"
    record_lost "$x"
    reset_world
  done
  judge RL34
  verdict RL34-bf SKIP "(b)~(f) need injected clocks and clients — unit tests (tests/test_worker_selfstop_m5tb.py)"
}

# RL40 — 서버가 아예 안 닿는 동안 잡이 끝난다
run_finish_offline() {
  local j pj t0 local_log
  log "RL40 start"
  common_facts RL40
  worker_start rl40 relay || return 0
  j=$(submit rl40-fin)
  pj=$(pid_of "$j" 30)
  sleep 5
  t0=$(now)
  relay_do stop
  [ -n "$pj" ] && h waitdead "$pj" 60 >/dev/null || true
  sleep 3
  fact RL40 state_while_refused "$(jv "$j" state)"
  fact RL40 refused_secs "$(h sub "$(now)" "$t0")"
  relay_do resume
  rwait "$j" 120 rl40-wait
  fact RL40 wait_rc "$RW_RC"
  fact RL40 final_state "$(jv "$j" state)"
  local_log=$(find "$W_DATA" -path "*/jobs/$j/log.txt" 2>/dev/null | head -1 || true)
  # 워커 쪽 사본이 없으면 견줄 것이 없다 — 0(다름)이 아니라 모른다
  if [ -z "$local_log" ]; then
    fact RL40 log_sha_match "<absent>"
  else
    fact RL40 log_sha_match "$(if [ "$(h sha "$DATA/jobs/$j/log.txt")" = "$(h sha "$local_log")" ]; then echo 1; else echo 0; fi)"
  fi
  fact RL40 log_complete "$(jv "$j" log_complete)"
  fact RL40 hold "$(has_hold "$j")"
  judge RL40
  verdict RL40-5xx SKIP "503 log_write_failed · 5xx for 300 s need server fault injection — unit tests"
  reset_world
}

# RL29 의 worker_restarted_without_job — claim 응답을 중계가 끊어 워커가 그 잡을 모른다(RL9 흉내)
run_claim_lost() {
  local j n attempt st
  log "claim-lost start"
  worker_start rl9 relay || return 0
  for attempt in 1 2 3; do
    n=$(relay_state slow)
    relay_do slow /worker/claim 1.0
    wait_relay slow $((n + 1)) 20 || continue
    j=$(submit rl9-plain)
    st=$(waitstate "$j" 10 running)
    sleep 1.5
    relay_do stop
    sleep 0.5
    relay_do fast
    relay_do resume
    st=$(waitstate "$j" 60 terminal)
    log "claim-lost attempt $attempt: #$j $st $(jv "$j" summary_code), pid file: $([ -s "$MARKS/$j.pid" ] && echo yes || echo no)"
    record_lost "$j"
    [ "$(jv "$j" summary_code)" = worker_restarted_without_job ] && break
    client cancel "$j" >/dev/null 2>&1 || true
  done
  reset_world
}

# RL15 — 워커 kill -9 뒤 같은 폴더로 다시 시작
run_orphan() {
  local data wp y py x px z pz t_dead x3 p3 sup out
  log "RL15 start"
  common_facts RL15
  fact RL15 identity "$(if [ "$(uname -s)" = Linux ] && [ -r /proc/self/stat ]; then echo /proc; else echo ps; fi)"
  W_LANES=2 worker_start rl15 direct || return 0
  data=$W_DATA
  wp=$W_PID
  y=$(submit rl15-plain)
  py=$(pid_of "$y" 30)
  x=$(submit rl15-ship)
  px=$(pid_of "$x" 30)
  [ -n "$py" ] && [ -n "$px" ] || {
    not_started RL15 rl15 "$y" "$x"
    reset_world
    return 0
  }
  signal_ours KILL "$wp" || true
  sleep 3
  fact RL15 orphan_alive_after_kill "$(alive "$py")"
  fact RL15 ship_cmd_dead "$(if [ "$(alive "$px")" = 0 ]; then echo 1; else echo 0; fi)"
  z=$(submit rl15-plain)
  W_LANES=2 worker_start rl15 direct "$GRACE_J" "$data" || true
  if h waitdead "$py" 30 >/dev/null; then
    # 고아가 끝난 시각은 새 워커가 그 줄을 적은 시각이다. 하네스가 알아챈 시각은 worker_start 가
    # 등록을 확인한 뒤라 다음 잡의 시작보다 늦다(2026-11-27 재실기: 줄 641.949 · 시작 641.992)
    t_dead=$(h firstline "$W_LOG" "orphan #$y: stopped process group")
    [ -n "$t_dead" ] || t_dead=$(now)
    fact RL15 orphan_dead_after_restart 1
  else
    fact RL15 orphan_dead_after_restart 0
  fi
  fact RL15 orphan_line "$(if grep -q "orphan #$y: stopped process group" "$W_LOG"; then echo 1; else echo 0; fi)"
  pz=$(pid_of "$z" 60)
  # 「claim 전에 끝냄」은 시계가 아니라 같은 워커 로그의 줄 순서로 본다 — 고아 줄 다음에 z 를 집은
  # 줄이 있어야 한다. 잡 문서의 started_at 은 초 단위로 잘려(core/status.py iso) 견줄 수 없다
  [ -n "$pz" ] && fact RL15 z_after_orphan "$(h inorder "$W_LOG" "orphan #$y: stopped process group" "claimed #$z ")"
  [ -n "${t_dead:-}" ] && fact RL15 orphan_end_epoch "$t_dead"
  fact RL15 plain_final "$(waitstate "$y" 30 terminal)"
  fact RL15 ship_final "$(waitstate "$x" 30 terminal)"
  fact RL15 ship_hold "$(has_hold "$x")"
  record_lost "$y"
  record_lost "$x"
  client cancel "$z" >/dev/null 2>&1 || true
  # 리더(감독자)까지 죽인다 — 남은 멤버는 손대지 않고 그룹을 잡아 둔다(W13, 닫힌 쪽)
  x3=$(submit rl15-ship)
  p3=$(pid_of "$x3" 30)
  if [ -n "$p3" ]; then
    sup=$(ps -o pgid= -p "$p3" 2>/dev/null | tr -d ' ' || true)
    # 감독자가 이 단계 워커의 자식일 때만 둘을 함께 죽인다(run_supervisor_only 와 같은 확인)
    if is_our_proc "$W_PID" "$B" "$(recorded_start "$W_PID")" && [ -n "$sup" ] &&
      [ "$sup" != "$p3" ] && is_child_of "$sup" "$W_PID"; then
      out=$(h killtime KILL "$W_PID,$sup" "$sup" 5 || true)
      sleep 2
      fact RL15 leader_alive "$(alive "$p3")"
      W_LANES=2 worker_start rl15 direct "$GRACE_J" "$data" || true
      sleep 5
      fact RL15 leader_line "$(if grep -q "orphan #$x3: leader gone but its group is alive" "$W_LOG"; then echo 1; else echo 0; fi)"
      fact RL15 leader_final "$(waitstate "$x3" $((G + 40)) terminal)"
      fact RL15 leader_hold "$(has_hold "$x3")"
      record_lost "$x3"
    fi
    kill_job_group "$p3" "$MARKS/$x3.start"
  fi
  judge RL15
  if [ "$(uname -s)" = Linux ]; then
    verdict_line RL15-proc "$(h judge RL15 "$OUT/facts/RL15.txt")"
  else
    verdict RL15-proc SKIP "not a Linux machine — /proc identity not exercised (ps -o lstart= was)"
  fi
  verdict RL15-reuse SKIP "a reused pid needs an injected identity — unit tests"
  reset_world
}

# RL1 · RL5 · RL42 — 분단이 G 를 넘는다(서버 재시작 포함)
run_partition_past_g() {
  local x px y py z o t_pause t lf s1 s2
  log "RL1/RL5/RL42 start"
  common_facts RL1
  common_facts RL5
  common_facts RL42
  worker_start rl1a relay || return 0
  worker_start rl1b relay || return 0
  x=$(submit rl1-ship)
  px=$(pid_of "$x" 30)
  y=$(submit rl1-late)
  py=$(pid_of "$y" 30)
  [ -n "$px" ] && [ -n "$py" ] || {
    not_started RL1 rl1a,rl1b "$x" "$y"
    reset_world
    return 0
  }
  t_pause=$(now)
  relay_do pause
  t=$(h waitdead "$px" $((G + 20)) || true)
  fact RL1 self_stop_after "$t"
  fact RL1 death_epoch "$(now)"
  fact RL1 state_at_stop "$(jv "$x" state)"
  fact RL1 final_state "$(waitstate "$x" $((G + 40)) terminal)"
  fact RL5 final_state "$(waitstate "$y" 40 terminal)"
  fact RL1 summary_code "$(jv "$x" summary_code)"
  fact RL1 failed_step "$(jv "$x" failed_step)"
  fact RL1 last_step "$(jv "$x" last_step)"
  # DB 의 REAL 칸 — 잡 문서의 finished_at 은 초 단위로 잘린다(core/status.py iso)
  fact RL1 finished_epoch "$(db "SELECT finished_at FROM jobs WHERE id=?" "$x")"
  fact RL5 summary_code "$(jv "$y" summary_code)"
  rwait "$x" 10 rl1-wait
  fact RL1 wait_rc "$RW_RC"
  rwait "$y" 10 rl5-wait
  fact RL5 wait_rc "$RW_RC"
  fact RL5 alive_after_close "$(alive "$py")"
  lf=$(find "$B/w" -path "*/jobs/$y/log.txt" 2>/dev/null | head -1 || true)
  s1=$(wc -c <"${lf:-/dev/null}" | tr -d ' ')
  sleep 3
  s2=$(wc -c <"${lf:-/dev/null}" | tr -d ' ')
  fact RL5 local_log_grew "$(if [ -n "$lf" ] && [ "$s2" -gt "$s1" ]; then echo 1; else echo 0; fi)"
  fact RL1 hold_at_close "$(has_hold "$x")"
  fact RL5 hold "$(has_hold "$y")"
  fact RL42 hold_rows "$(hold_rows "$x")"
  fact RL42 hold_cause "$(db "SELECT cause FROM irreversible_holds WHERE job_id=?" "$x")"
  fact RL42 hold_events "$(db "SELECT count(*) FROM events WHERE job_id=? AND kind='hold'" "$x")"
  fact RL42 plain_hold_rows "$(hold_rows "$y")"
  record_lost "$x"
  record_lost "$y"
  z=$(submit rl1-ship)
  worker_start rl1c direct || true
  o=$(submit rl1-short)
  rwait "$o" 60 rl42-other
  fact RL42 other_group_ran "$(if [ "$(jv "$o" state)" = succeeded ]; then echo 1; else echo 0; fi)"
  fact RL42 same_group_state "$(jv "$z" state)"
  fact RL42 same_group_reason "$(jv "$z" reason)"
  fact RL42 same_group_started "$(jv "$z" started_at)"
  server_stop
  server_start || log "RL42 the server did not come back"
  sleep 6
  fact RL42 after_restart_hold "$(has_hold "$x")"
  fact RL42 after_restart_same_group_state "$(jv "$z" state)"
  relay_do resume
  t=$(wait_unheld "$x" 60)
  fact RL42 released_after_return "$t"
  fact RL1 hold_released_after_return "$t"
  fact RL42 same_group_started_after_release "$(if [ -n "$(pid_of "$z" 60)" ]; then echo 1; else echo 0; fi)"
  fact RL1 state_end "$(jv "$x" state)"
  # 「lost 한 번」 — 돌아온 워커의 finish 가 풀어 준 뒤에 센다(RL2 의 x_lost_events 와 같은 질의)
  fact RL1 lost_events "$(db "SELECT count(*) FROM events WHERE job_id=? AND kind='state' AND payload LIKE '%\"lost\"%'" "$x")"
  sleep 3
  fact RL5 alive_after_return "$(alive "$py")"
  client cancel "$z" >/dev/null 2>&1 || true
  # 일반 잡이 제 손으로 끝나면 늦은 finish — late_result 한 번, 상태는 lost 그대로
  h waitdead "$py" $((LATE_SECONDS + 30)) >/dev/null || log "RL5 the plain job did not end"
  sleep 10
  fact RL5 late_count "$(db "SELECT count(*) FROM events WHERE job_id=? AND kind='late_result'" "$y")"
  fact RL5 late_outcome "$(jv "$y" late_result.outcome)"
  fact RL5 state_after_late "$(jv "$y" state)"
  rwait "$y" 10 rl5-wait-late
  fact RL5 wait_rc_after_late "$RW_RC"
  fact RL5 late_line "$(has_line "$OUT/rl5-wait-late.err" "$LINE_LATE")"
  judge RL1
  judge RL5
  judge RL42
  verdict RL42-gc SKIP "the metadata_retention_days sweep needs an aged store — unit tests (store clock)"
  reset_world
}

# RL8 (b) — 분단 중 취소, 복귀 없이 G 경과
run_cancel_in_partition() {
  local x px y py t
  log "RL8 start"
  common_facts RL8
  worker_start rl8a relay || return 0
  worker_start rl8b relay || return 0
  x=$(submit rl8-ship)
  px=$(pid_of "$x" 30)
  y=$(submit rl8-tick)
  py=$(pid_of "$y" 30)
  [ -n "$px" ] && [ -n "$py" ] || {
    not_started RL8 rl8a,rl8b "$x" "$y"
    reset_world
    return 0
  }
  relay_do pause
  sleep 14
  client cancel "$x" >>"$OUT/rl8-cancel.log" 2>&1 && fact RL8 cancel_rc_x 0 || fact RL8 cancel_rc_x $?
  client cancel "$y" >>"$OUT/rl8-cancel.log" 2>&1 && fact RL8 cancel_rc_y 0 || fact RL8 cancel_rc_y $?
  sleep 2
  fact RL8 x_cancelling "$(jv "$x" state)"
  fact RL8 y_cancelling "$(jv "$y" state)"
  t=$(h waitdead "$px" "$G" || true)
  fact RL8 x_dead_after "$(h sub "$t" -16)"
  fact RL8 x_final "$(waitstate "$x" $((G + 40)) terminal)"
  fact RL8 y_final "$(waitstate "$y" 40 terminal)"
  fact RL8 x_code "$(jv "$x" summary_code)"
  fact RL8 y_code "$(jv "$y" summary_code)"
  fact RL8 x_hold_at_close "$(has_hold "$x")"
  rwait "$x" 10 rl8-x-wait
  fact RL8 x_wait_rc "$RW_RC"
  rwait "$y" 10 rl8-y-wait
  fact RL8 y_wait_rc "$RW_RC"
  record_lost "$x"
  record_lost "$y"
  relay_do resume
  fact RL8 y_dead_after_return "$(if h waitdead "$py" 20 >/dev/null; then echo 1; else echo 0; fi)"
  fact RL8 x_hold_released "$(wait_unheld "$x" 60)"
  sleep 5
  fact RL8 y_late_count "$(db "SELECT count(*) FROM events WHERE job_id=? AND kind='late_result'" "$y")"
  fact RL8 y_state_end "$(jv "$y" state)"
  fact RL8 x_state_end "$(jv "$x" state)"
  judge RL8
  verdict RL8-a SKIP "(a) return inside G — pytest e2e test_rl8_a_* (not repeated)"
  reset_world
}

# RL2 — 서버 정지가 G 를 넘는다
run_server_stop_past_g() {
  local x px y py t_down t
  log "RL2 start"
  common_facts RL2
  worker_start rl2a direct || return 0
  worker_start rl2b direct || return 0
  x=$(submit rl2-ship)
  px=$(pid_of "$x" 30)
  y=$(submit rl2-plain)
  py=$(pid_of "$y" 30)
  [ -n "$px" ] && [ -n "$py" ] || {
    not_started RL2 rl2a,rl2b "$x" "$y"
    reset_world
    return 0
  }
  server_stop
  t_down=$(now)
  fact RL2 x_dead_while_down "$(if h waitdead "$px" "$G" >/dev/null; then echo 1; else echo 0; fi)"
  fact RL2 y_alive_while_down "$(alive "$py")"
  t=$(h sub "$(now)" "$t_down" | awk -v g="$G" '{ d = g + 3 - $1; print (d > 0 ? int(d) + 1 : 0) }')
  sleep "$t"
  server_start || log "RL2 the server did not come back"
  sleep 7
  fact RL2 y_state_after_start "$(jv "$y" state)"
  fact RL2 x_final "$(waitstate "$x" 60 terminal)"
  fact RL2 x_code "$(jv "$x" summary_code)"
  fact RL2 x_failed_step "$(jv "$x" failed_step)"
  fact RL2 x_lost_events "$(db "SELECT count(*) FROM events WHERE job_id=? AND kind='state' AND payload LIKE '%\"lost\"%'" "$x")"
  rwait "$x" 10 rl2-wait
  fact RL2 x_wait_rc "$RW_RC"
  fact RL2 x_hold "$(has_hold "$x")"
  fact RL2 x_hold_released "$(wait_unheld "$x" 30)"
  fact RL2 y_alive_end "$(alive "$py")"
  record_lost "$x"
  judge RL2
  reset_world
}

# RL43 (a)(b)(e) · RL45 · 감독자 값(F78)
run_kill_worker() {
  local xa pa xb pb ye pe out wa wb we z z2 c1 c2 d1 d2 h1 h2 name conf hold_a hold_b stuck p n more tail_text
  log "RL43/RL45 start"
  common_facts RL43
  common_facts RL45
  fact RL45 admin_name acceptance-admin
  worker_start rl43a direct || return 0
  wa=$W_PID
  worker_start rl43b direct || return 0
  wb=$W_PID
  worker_start rl43e direct || return 0
  we=$W_PID
  xa=$(submit rl43-ship)
  pa=$(pid_of "$xa" 30)
  xb=$(submit rl43-ignore)
  pb=$(pid_of "$xb" 30)
  ye=$(submit rl43-plain)
  pe=$(pid_of "$ye" 30)
  [ -n "$pa" ] && [ -n "$pb" ] && [ -n "$pe" ] || {
    not_started RL43 rl43a,rl43b,rl43e "$xa" "$xb" "$ye"
    reset_world
    return 0
  }
  for p in "$wa" "$wb" "$we"; do
    is_our_proc "$p" "$B" "$(recorded_start "$p")" || {
      verdict RL43 UNKNOWN "worker $p is not a process this harness started — not killing it"
      reset_world
      return 0
    }
  done
  out=$(h killtime KILL "$wa,$wb,$we" "$pa,$pb" $((GRACE_J + 10)))
  printf '%s\n' "$out" >"$OUT/rl43-killtime.txt"
  fact RL43 a_detect "$(printf '%s\n' "$out" | sed -n "s/^$pa=//p")"
  fact RL43 b_detect "$(printf '%s\n' "$out" | sed -n "s/^$pb=//p")"
  fact F78 a_detect "$(printf '%s\n' "$out" | sed -n "s/^$pa=//p")"
  fact F78 b_detect "$(printf '%s\n' "$out" | sed -n "s/^$pb=//p")"
  sleep 3
  fact RL43 e_alive "$(alive "$pe")"
  fact RL43 a_final "$(waitstate "$xa" $((G + 40)) terminal)"
  waitstate "$xb" 30 terminal >/dev/null
  waitstate "$ye" 30 terminal >/dev/null
  fact RL43 a_code "$(jv "$xa" summary_code)"
  fact RL43 a_hold "$(has_hold "$xa")"
  fact RL43 b_hold "$(has_hold "$xb")"
  fact RL43 e_hold "$(has_hold "$ye")"
  record_lost "$xa"
  record_lost "$ye"
  # RL45 — 홀드 둘. 하나는 대기 잡 둘, 하나는 하나(「확인이 필요한 작업」 4줄 이상)
  z=$(submit rl43-ship)
  z2=$(submit rl43-ignore)
  worker_start rl43f direct || true
  sleep 4
  fact RL45 z_state "$(jv "$z" state)"
  fact RL45 z_reason "$(jv "$z" reason)"
  fact RL45 z_action "$(jv "$z" action.code)"
  fact RL45 z_blocked_by_ok "$(if [ "$(jv "$z" blocked_by.job_id)" = "$xa" ]; then echo 1; else echo 0; fi)"
  fact RL45 z_remaining "$(jv "$z" blocked_by.remaining_seconds)"
  fact RL45 z_finish_at "$(jv "$z" estimate.finish_at)"
  hold_a=$(has_hold "$xa")
  hold_b=$(has_hold "$xb")
  fact RL45 status_holds_ok "$(if [ "$hold_a$hold_b" = 11 ]; then echo 1; else echo 0; fi)"
  h health "$URL" "$CTOK" >"$OUT/rl45-health.txt"
  fact RL45 health_holds_ok "$(if [ "$(cut -d'|' -f4 "$OUT/rl45-health.txt")" -ge 2 ] 2>/dev/null; then echo 1; else echo 0; fi)"
  h1=$(cut -d'|' -f2 "$OUT/rl45-health.txt")
  rwait "$z" 10 rl45-wait
  fact RL45 wait_timeout_rc "$RW_RC"
  fact RL45 wait_line "$(has_line "$OUT/rl45-wait.err" "$LINE_HELD_WAIT")"
  rwait "$z" 30 rl45-wait-action --return-on-action
  fact RL45 wait_action_rc "$RW_RC"
  # 홀드를 만든 잡(xa)의 rcm wait — 끝 줄에 그룹이 막혔다는 말과 푸는 명령
  rwait "$xa" 10 rl45-wait-owner
  fact RL45 owner_line "$(if [ "$(has_line "$OUT/rl45-wait-owner.err" "$LINE_HOLD_OWNER")$(has_line "$OUT/rl45-wait-owner.err" "rcm release-hold $xa")" = 11 ]; then echo 1; else echo 0; fi)"
  # 막힌 대기 잡의 rcm eta --job — 홀드를 말하고, 오지 않을 순번(N ahead)을 세지 않는다
  client eta --job "$z" >"$OUT/rl45-eta.txt" 2>&1 || true
  fact RL45 eta_held "$(if [ "$(has_line "$OUT/rl45-eta.txt" "$LINE_ETA_HELD$xa")" = 1 ]; then echo 1; else echo 0; fi)"
  fact RL45 eta_no_ahead "$(if [ "$(has_line "$OUT/rl45-eta.txt" "$LINE_ETA_AHEAD")" = 0 ] && [ -s "$OUT/rl45-eta.txt" ]; then echo 1; else echo 0; fi)"
  c1=0
  client check >"$OUT/rl45-check.txt" 2>&1 || c1=$?
  fact RL45 check_lines "$(grep -cF -- "$LINE_CHECK_HOLD" "$OUT/rl45-check.txt" || true)"
  name=$(jv "$xa" ran_on)
  conf=$(conf_of "$name")
  [ -n "$conf" ] || conf=$(conf_of rl43a)
  d1=0
  "$RCM" --no-follow doctor --role worker --config "$conf" >"$OUT/rl45-doctor.txt" 2>&1 || d1=$?
  "$RCM" --no-follow doctor --role worker --config "$conf" --json >"$OUT/rl45-doctor.json" 2>"$OUT/rl45-doctor.err" || true
  fact RL45 doctor_lines "$(grep -cF -- "$LINE_DOCTOR_HOLD" "$OUT/rl45-doctor.txt" || true)"
  fact RL45 doctor_json_holds "$("$PY" -I -c 'import json, sys; d = json.load(open(sys.argv[1])); h = d.get("irreversible_holds"); print(len(h) if isinstance(h, list) else "<absent>")' "$OUT/rl45-doctor.json" 2>/dev/null || echo "<absent>")"
  # 웹 — 홀드 둘 다 · 칩 · 줄 수. 하네스가 만든 줄 = 열린 홀드 + 그 홀드에 막힌 대기 잡(4 를 기대한다)
  n=0
  for p in "$hold_a" "$hold_b"; do
    if [ "$p" = 1 ]; then n=$((n + 1)); fi
  done
  for p in "$z" "$z2"; do
    if [ "$(jv "$p" reason)" = needs_action ]; then n=$((n + 1)); fi
  done
  web rl45 >"$OUT/facts/RL45-web.txt"
  fact RL45-web stuck_min "$n"
  fact RL45-web stuck_max "$WEB_STUCK_MAX"
  for p in en ko; do
    fact RL45-web "${p}_raw" "$(if grep -q 'irreversible_hold\|worker_unreachable' "$OUT/web/rl45-$p.txt" 2>/dev/null; then echo 1; else echo 0; fi)"
  done
  # 「확인이 필요한 작업」 칸의 글(en_stuck_text)에서 찾는다 — 큐 행의 번호와 섞지 않는다
  stuck=$(sed -n 's/^en_stuck_text=//p' "$OUT/facts/RL45-web.txt" | tail -1)
  # 명령 치환 안에 case 를 두지 않는다 — macOS 의 bash 3.2 는 패턴의 `)` 를 치환의 끝으로 읽는다
  fact RL45-web found_hold_a "$(if [[ "$stuck" == *"#$xa "* ]]; then echo 1; else echo 0; fi)"
  fact RL45-web found_hold_b "$(if [[ "$stuck" == *"#$xb "* ]]; then echo 1; else echo 0; fi)"
  # 꼬리 한 줄 — 「and N more」의 N(없으면 0)과 「nothing else needs a look」이 있는지
  tail_text=$(sed -n 's/^en_stuck_tail=//p' "$OUT/facts/RL45-web.txt" | tail -1)
  more=$(printf '%s' "$tail_text" | sed -n "s/.*${WEB_AND_MORE_PRE}\([0-9][0-9]*\)${WEB_AND_MORE_POST}.*/\1/p" | head -1)
  if grep -q '^web=ok' "$OUT/facts/RL45-web.txt"; then
    fact RL45-web en_more_n "${more:-0}"
    fact RL45-web en_nothing_else "$(has_line_in "$tail_text" "$WEB_NOTHING_ELSE")"
  fi
  # 풀기 — client 토큰 2, admin 0, 홀드 없는 잡 1
  client release-hold "$xa" --reason "client try" >"$OUT/rl45-release-client.txt" 2>&1 && fact RL45 client_release_rc 0 || fact RL45 client_release_rc $?
  admin release-hold "$xa" --reason "checked by acceptance" --json >"$OUT/rl45-release.json" 2>&1 && fact RL45 admin_release_rc 0 || fact RL45 admin_release_rc $?
  fact RL43 release_rc "$(sed -n 's/^admin_release_rc=//p' "$OUT/facts/RL45.txt" | tail -1)"
  fact RL45 released_by "$(jv "$xa" hold.released_by)"
  fact RL45 release_reason_ok "$(if [ "$(jv "$xa" hold.release_reason)" = "checked by acceptance" ]; then echo 1; else echo 0; fi)"
  fact RL43 z_started "$(if [ -n "$(pid_of "$z" 60)" ]; then echo 1; else echo 0; fi)"
  admin release-hold "$ye" --reason "no hold" >"$OUT/rl45-release-none.txt" 2>&1 && fact RL45 nohold_release_rc 0 || fact RL45 nohold_release_rc $?
  # RL45-empty — 대기열을 비우고(z · z2 취소) xb 의 홀드만 열어 둔다: rcm top 과 웹이 「바로
  # 시작」을 약속하지 않는다
  common_facts RL45-empty
  client cancel "$z" >/dev/null 2>&1 || true
  client cancel "$z2" >/dev/null 2>&1 || true
  waitstate "$z" 30 terminal >/dev/null
  waitstate "$z2" 30 terminal >/dev/null
  h status "$URL" "$CTOK" "$OUT/rl45-status-empty.json" || true
  fact RL45-empty queue_empty "$("$PY" -I -c 'import json, sys; d = json.load(open(sys.argv[1])); ps = d.get("pools") or []; ok = ps and all(isinstance(p.get("queue"), list) for p in ps); print(1 if ok and not any(p["queue"] for p in ps) else 0)' "$OUT/rl45-status-empty.json" 2>/dev/null || echo "<absent>")"
  fact RL45-empty hold_open "$(has_hold "$xb")"
  client top >"$OUT/rl45-top-empty.txt" 2>&1 || true
  fact RL45 top_empty_held "$(has_line "$OUT/rl45-top-empty.txt" "$LINE_TOP_EMPTY_HELD")"
  web rl45-empty >>"$OUT/facts/RL45-empty.txt"
  for p in en ko; do
    fact RL45-empty "${p}_raw" "$(if grep -q 'irreversible_hold\|worker_unreachable' "$OUT/web/rl45-empty-$p.txt" 2>/dev/null; then echo 1; else echo 0; fi)"
  done
  if grep -q '^web=ok' "$OUT/facts/RL45-empty.txt"; then
    fact RL45-empty found_empty_held "$(has_line "$OUT/web/rl45-empty-en.txt" "$WEB_EMPTY_HELD")"
    fact RL45-empty en_empty_hint "$(has_line "$OUT/web/rl45-empty-en.txt" "$WEB_EMPTY_HINT")"
  fi
  admin release-hold "$xb" --reason "acceptance cleanup" >>"$OUT/reset.log" 2>&1 || true
  # 홀드가 모두 풀린 뒤 같은 머신 · 같은 워커 상태로 다시 잰다 — 종료 코드 · ok 가 홀드로 바뀌지 않았나
  sleep 2
  c2=0
  client check >"$OUT/rl45-check-after.txt" 2>&1 || c2=$?
  d2=0
  "$RCM" --no-follow doctor --role worker --config "$conf" >"$OUT/rl45-doctor-after.txt" 2>&1 || d2=$?
  h2=$(h health "$URL" "$CTOK" | cut -d'|' -f2)
  fact RL45 check_rc_same "$(if [ "$c1" = "$c2" ]; then echo 1; else echo 0; fi)"
  fact RL45 doctor_rc_same "$(if [ "$d1" = "$d2" ]; then echo 1; else echo 0; fi)"
  fact RL45 health_ok_same "$(if [ "$h1" = "$h2" ]; then echo 1; else echo 0; fi)"
  kill_job_group "$pe" "$MARKS/$ye.start"
  judge RL43
  verdict RL43-c SKIP "(c) death before the supervisor's parent check needs an injected start delay — unit tests"
  judge RL45
  judge RL45-web
  judge RL45-empty
  judge F78
  reset_world
}

# RL29 — 다섯 lost 끝을 사람 · 훅 · 웹으로
run_lost_ends() {
  local code jid args text lang needles=""
  log "RL29 start"
  common_facts RL29
  fact RL29 codes "$LOST_CODES"
  for code in $LOST_CODES; do
    jid=$(awk -v c="$code" '$1 == c { print $2; exit }' "$OUT/lost.tsv" 2>/dev/null || true)
    [ -n "$jid" ] || continue
    fact RL29 "${code}_job" "$jid"
    rwait "$jid" 10 "rl29-$code"
    fact RL29 "${code}_rc" "$RW_RC"
    fact RL29 "${code}_failed_step" "$(jv "$jid" failed_step)"
    args=$(jv "$jid" summary_args)
    text=$(h render "$code" "$args" || true)
    fact RL29 "${code}_text" "$text"
    fact RL29 "${code}_line" "$(if [ -n "$text" ] && grep -qF "#$jid lost: $text" "$OUT/rl29-$code.err"; then echo 1; else echo 0; fi)"
    fact RL29 "${code}_notify" "$(grep -c "^job=$jid state=lost " "$B/notify.log" 2>/dev/null || true)"
    needles="$needles $code"
    printf '%s\n' "$args" >"$OUT/rl29-args-$code.txt"
  done
  judge RL29
  web rl29 >"$OUT/facts/RL29-web.txt"
  fact RL29-web codes "$LOST_CODES"
  fact RL29-web needles "$needles"
  # 끝마다 그 언어 화면이 그리는 문장(i18n.js)의 앞 40자를 그 언어 화면 글에서 찾는다. 찾을 글이
  # 비면 찾지 않는다 — 빈 글은 아무 화면에나 맞는다(judge_rl29_web 이 UNKNOWN 으로 본다)
  for code in $needles; do
    for lang in en ko; do
      text=$(h needle "$lang" "$code" "$(cat "$OUT/rl29-args-$code.txt")" 2>>"$OUT/web.err" || true)
      fact RL29-web "needle_${lang}_$code" "$text"
      [ -n "$text" ] || continue
      fact RL29-web "found_${lang}_$code" "$(has_line "$OUT/web/rl29-$lang.txt" "$text")"
    done
  done
  for code in en ko; do
    fact RL29-web "${code}_raw" "$(if grep -qE "$(printf '%s' "$LOST_CODES" | tr ' ' '|')" "$OUT/web/rl29-$code.txt" 2>/dev/null; then echo 1; else echo 0; fi)"
  done
  judge RL29-web
}

dump_jobs() {
  "$PY" -I - "$URL" "$CTOK" "$OUT/jobs.json" <<'EOF' || echo "[]" >"$OUT/jobs.json"
import json, sys, urllib.request
url, token_file, dest = sys.argv[1:4]
token = open(token_file).read().strip()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
rows = []
for n in range(1, 200):
    req = urllib.request.Request(f"{url}/jobs/{n}", headers={"Authorization": f"Bearer {token}"})
    try:
        rows.append(json.load(opener.open(req, timeout=5)))
    except Exception:
        break
keys = ("id", "preset", "state", "created_at", "started_at", "finished_at", "exit_code",
        "summary_code", "summary", "ran_on", "hold", "late_result", "log_complete")
json.dump([{k: r.get(k) for k in keys} for r in rows], open(dest, "w"), indent=1)
EOF
}

# ── 설정 ────────────────────────────────────────────────────────────────────

# 표지 줄: 잡 프로세스의 pid(표식 잡이면 감독자 아래 명령의 pid)를 적는다. CHILD_LINE 은 SIGTERM 을
# 무시하는 자식 하나를 남기고 그 pid 를 적는다(RL51)
PID_LINE='echo $$ > "$RCM_MARK_DIR/$RCM_JOB_ID.pid"'
CHILD_LINE='(trap "" TERM; exec sleep 400) & echo $! > "$RCM_MARK_DIR/$RCM_JOB_ID.child"'

preset() { # preset <이름> <irreversible 0|1> <timeout> <sh 스크립트> [expected_seconds] — TOML 한 덩이
  printf '\n[[presets]]\nname = "%s"\npool = "%s"\n' "$1" "$POOL"
  [ "$2" = 1 ] && printf 'irreversible = true\n'
  # 예상 시간이 있으면 웹의 진행 막대가 시간 눈금을 단다(RL28-web 의 대조군 행)
  [ -z "${5:-}" ] || printf 'expected_seconds = %s\n' "$5"
  printf "argv = [\"sh\", \"-c\", '%s']\ntimeout_seconds = %s\n" "$4" "$3"
  printf '[presets.env]\nRCM_MARK_DIR = "%s"\n' "$MARKS"
}

write_server_toml() { # write_server_toml <self-test 0|1>
  local long tick late ignore child slowexit fin
  long="$PID_LINE; exec sleep 400"
  tick="$PID_LINE; while :; do echo tick; sleep 1; done"
  late="$PID_LINE; i=0; while [ \$i -lt $LATE_SECONDS ]; do echo tick \$i; i=\$((i+1)); sleep 1; done; exit 0"
  ignore="$PID_LINE; trap \"\" TERM; exec sleep 400"
  child="$PID_LINE; $CHILD_LINE; trap \"exit 0\" TERM; while :; do sleep 0.2; done"
  slowexit="$PID_LINE; $CHILD_LINE; trap \"sleep $((GRACE_J - 1)).5; exit 0\" TERM; while :; do sleep 0.2; done"
  fin="$PID_LINE; i=0; while [ \$i -lt 40 ]; do echo line \$i; i=\$((i+1)); sleep 1; done; exit 0"
  {
    printf '[server]\nbind = "127.0.0.1"\nport = %s\ndata_dir = "%s"\nadvertise = false\nlanes = 0\n' "$P" "$DATA"
    printf 'grace_seconds = %s\nworker_timeout_seconds = %s\nworker_heartbeat_seconds = %s\n' "$GRACE_J" "$T" "$HB"
    printf 'worker_claim_wait_seconds = %s\ndisconnect_grace_seconds = %s\n' "$CLAIM_WAIT" "$G"
    # 부하 게이트(server.py admit)는 시험하지 않는다 — 켜 두면 레인 둘인 워커(RL15)의 둘째 레인이
    # 머신이 바쁠 때 쿨다운 · CPU 창에 막혀 잡이 안 시작한다. 웹 최근 목록(RL29-web)은 실기가 끝낸
    # 잡을 다 담는다(기본 8개)
    printf 'admission = "always"\nrecent_count = %s\n' "$RECENT_COUNT"
    preset rl50-ship 1 900 "$long"
    preset rl10-ship 1 900 "$long"
    preset rl43d-exit7 1 60 "$PID_LINE; sleep 1; exit 7"
    preset rl43d-ship 1 900 "$long"
    preset rl51-child 1 900 "$child"
    preset rl51-slowexit 1 900 "$slowexit"
    preset rl28-plain 0 900 "$tick" 600
    preset rl34-ship 1 900 "$long"
    preset rl40-fin 0 300 "$fin"
    preset rl9-plain 0 900 "$long"
    preset rl15-plain 0 900 "$long"
    preset rl15-ship 1 900 "$long"
    preset rl1-ship 1 900 "$long"
    preset rl1-late 0 900 "$late"
    preset rl1-short 0 60 "$PID_LINE; sleep 3; exit 0"
    preset rl8-ship 1 900 "$long"
    preset rl8-tick 0 900 "$tick"
    preset rl2-ship 1 900 "$long"
    preset rl2-plain 0 900 "$long"
    preset rl43-ship 1 900 "$long"
    preset rl43-ignore 1 900 "$ignore"
    preset rl43-plain 0 900 "$long"
    # RL18 — 서버가 아는 릴리스 비밀(시험마다 새 값). 저장소는 없어도 된다(tree 모드)
    printf '\n[[presets]]\nname = "rl18-plan"\npool = "%s"\nrepo = "app"\nsource_modes = ["tree", "git_ref"]\n' "$POOL"
    printf 'argv = ["%s", "%s", "%s", "600"]\ntimeout_seconds = 300\n' "$PY" "$B/bin/gen.py" "$B/secret.txt"
    printf '\n[[presets]]\nname = "rl18-upload"\npool = "%s"\nrepo = "app"\nsource_modes = ["git_ref"]\nargv = ["true"]\ntimeout_seconds = 60\n' "$POOL"
    printf '\n[[presets]]\nname = "rl18-review"\npool = "%s"\nrepo = "app"\nsource_modes = ["git_ref"]\nargv = ["true"]\ntimeout_seconds = 60\n' "$POOL"
    printf '\n[[repos]]\nname = "app"\nurl = "%s"\n\n[repos.app.release]\nsecrets_dir_env = "APP_SECRETS"\n\n' "$B/no-such-repo.git"
    printf '[repos.app.release.presets]\nplan = "rl18-plan"\nupload = "rl18-upload"\nreview = "rl18-review"\n\n'
    printf '[[repos.app.release.secrets]]\nname = "GH_TOKEN"\nkind = "value"\n'
    if [ "$1" = 0 ]; then
      # RL29 — 받은 환경을 시험 폴더의 파일에 적는 가짜 알림 훅
      printf '\n[[notify]]\nname = "acceptance"\non = ["lost"]\nargv = ["sh", "%s"]\ntimeout_seconds = 10\n' "$B/bin/notify.sh"
    fi
  } >"$CONF"
  cat >"$B/bin/gen.py" <<'EOF'
import sys
import time

secret = open(sys.argv[1]).read()
for i in range(int(sys.argv[2])):
    line = f"k{i}={secret} end" if i % 3 == 0 else f"pad-{i:05d}-" + "x" * (i % 37)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    time.sleep(0.02)
EOF
  cat >"$B/bin/notify.sh" <<EOF
#!/bin/sh
echo "job=\$RCM_JOB_ID state=\$RCM_STATE event=\$RCM_EVENT hold=\$RCM_HOLD" >> "$B/notify.log"
EOF
  chmod 755 "$B/bin/notify.sh"
}

# ── 정리 ────────────────────────────────────────────────────────────────────

cleanup() {
  local p f
  for p in ${WORKER_PIDS[@]+"${WORKER_PIDS[@]}"}; do stop_ours "$p"; done
  WORKER_PIDS=()
  if [ -s "$B/run/procs.tsv" ]; then
    for p in $(cut -f2 "$B/run/procs.tsv"); do stop_ours "$p"; done
  fi
  for f in "$MARKS"/*.pid "$MARKS"/*.child; do
    [ -s "$f" ] || continue
    p=$(tr -d ' \n' <"$f")
    kill_job_group "$p" "${f%.*}.start"
  done
  for p in ${LAUNCHERS[@]+"${LAUNCHERS[@]}"}; do
    kill -TERM "$p" 2>/dev/null || true
    wait "$p" 2>/dev/null || true
  done
  LAUNCHERS=()
}

# 남은 프로세스가 없는가(자기 점검) — 띄운 모든 pid 가 사라졌다
leftovers() {
  local n=0 p
  for p in $(cut -f2 "$B/run/procs.tsv" 2>/dev/null) $(cat "$B/run/launchers.txt" 2>/dev/null); do
    if h alive "$p"; then n=$((n + 1)); fi
  done
  echo "$n"
}

# ── 자기 점검: 잡 없이 가드 · 서버 · 중계 · 워커 · 정리 ─────────────────────────

self_test() {
  local st left
  log "self-test: server $URL"
  if ! server_start; then
    verdict SELF-server FAIL "the test server did not answer (out/server.log)"
    return 0
  fi
  verdict SELF-server PASS "test server up on $URL from $CONF"
  new_token "$CTOK" acceptance || die "client token failed"
  relay_start || {
    verdict SELF-relay FAIL "the relay did not start (out/relay.log)"
    return 0
  }
  if worker_start self relay; then
    verdict SELF-worker PASS "worker registered through the relay $RURL"
  else
    verdict SELF-worker FAIL "worker did not register through the relay (out/worker-self-*.log)"
  fi
  relay_do pause && relay_do resume && relay_do hold /worker/heartbeat && relay_do release &&
    relay_do slow /worker/claim 1.0 && relay_do fast && relay_do stop && relay_do resume &&
    verdict SELF-relay PASS "pause, resume, hold, release, slow, fast, stop answered" ||
    verdict SELF-relay FAIL "a relay command got no answer"
  st=$(h holds "$URL" "$CTOK")
  case "$st" in unknown) verdict SELF-status FAIL "/api/status has no irreversible_holds list" ;;
  *) verdict SELF-status PASS "/api/status irreversible_holds is a list ('$st')" ;; esac
  relay_do quit || true
  cleanup
  sleep 1
  left=$(leftovers)
  if [ "$left" = 0 ]; then
    verdict SELF-cleanup PASS "every process this harness started is gone"
  else
    verdict SELF-cleanup FAIL "$left process(es) still running"
  fi
}

# ── main ────────────────────────────────────────────────────────────────────

usage() {
  echo "usage: m5tb_live.sh [--list | --self-test | --only ID[,ID…]]" >&2
  exit 2
}

# --only 의 ID 들 → 돌릴 함수들(단계 표의 셋째 칸). 순서는 ALL_RUNNERS 의 순서다
runners_for() { # runners_for <ID,ID…>
  local id r want="" x
  for id in $(printf '%s' "$1" | tr ',' ' '); do
    r=$(steps_table | awk -F '|' -v i="$id" '$1 == i && $2 == "live" { print $3 }')
    [ -n "$r" ] || die "$id is not a live step (see --list)"
    want="$want $(printf '%s' "$r" | tr '+' ' ')"
  done
  for x in $ALL_RUNNERS; do
    case " $want " in *" $x "*) echo "$x" ;; esac
  done
}

has_runner() { # has_runner <함수 목록(공백 · 줄바꿈)> <이름> — 목록에 그 이름이 있나
  local r
  for r in $1; do [ "$r" = "$2" ] && return 0; done
  return 1
}

ALL_RUNNERS="run_hb_order run_fence run_supervisor_only run_sweep run_secret run_grace_view run_self_stop_timing run_finish_offline run_claim_lost run_orphan run_partition_past_g run_cancel_in_partition run_server_stop_past_g run_kill_worker run_lost_ends"

main() {
  local real_home=${HOME:-} mode=all only="" runners r
  case "${1:-}" in
    "") ;;
    --list)
      steps_table
      return 0
      ;;
    --self-test) mode=self ;;
    --only)
      [ -n "${2:-}" ] || usage
      only=$2
      ;;
    *) usage ;;
  esac
  case "$real_home" in /?*) ;; *) die "HOME must be an absolute path other than / ($real_home)" ;; esac
  CHROME_FOR_WEB=${RCM_CHROME:-}

  # 포트와 시험 폴더 검사가 먼저다 — 아무것도 만들기 전에 멈춘다
  P=${M5TB_PORT:-8797}
  is_test_port "$P" || die "M5TB_PORT $P is not a test port (8790-8899)"
  if lsof -nP -iTCP:"$P" -sTCP:LISTEN >/dev/null 2>&1; then die "port $P is in use"; fi
  if [ -n "$only" ]; then runners=$(runners_for "$only"); else runners=$ALL_RUNNERS; fi

  [ -x "$PY" ] || die "no $PY"
  RID=$("$PY" -I -c 'import secrets; print(secrets.token_hex(4))')
  B=${M5TB_BASE:-$real_home/.local/share/$TEST_DIR_NAME/live-$RID}
  is_test_base "$B" "$real_home" || die "base $B is not ~/.local/share/$TEST_DIR_NAME/<name>"
  [ ! -e "$B" ] || die "$B exists — pick a new name (this harness never reuses or deletes a folder)"
  if is_inside "$B" /tmp || is_inside "$B" /private/tmp; then die "base $B is under /tmp"; fi
  [ -x "$RCM" ] || die "no $RCM — make the worktree's .venv first"
  mkdir -p "$(dirname "$B")"
  mkdir "$B" || die "cannot create $B"
  REAL_HOME=$real_home
  OUT="$B/out"
  DATA="$B/data"
  CONF="$B/server.toml"
  MARKS="$B/marks"
  SRC="$B/src"
  RCTL="$B/relay"
  URL="http://127.0.0.1:$P"
  CTOK="$B/tok/client"
  ATOK="$B/tok/admin"
  DB="$DATA/rcm.sqlite3"
  H="$B/bin/m5tb_helper.py"
  mkdir -p "$OUT/facts" "$B/home" "$B/xdg/config" "$B/xdg/data" "$B/xdg/state" "$B/xdg/cache" \
    "$B/bin" "$B/run" "$B/w" "$MARKS" "$SRC" "$DATA"
  (umask 077 && mkdir -p "$B/tok")
  : >"$OUT/summary.txt"
  : >"$OUT/lost.tsv"
  FAILS=0
  WN=0
  LAUNCHERS=()
  WORKER_PIDS=()
  R_PID=""
  write_helper "$B/bin"
  export M5TB_ROOT
  [ -z "$CHROME_FOR_WEB" ] || export M5TB_CHROME="$CHROME_FOR_WEB"
  if [ "$mode" = self ]; then write_server_toml 1; else write_server_toml 0; fi
  why=$(check_server_toml "$CONF" "$P" "$DATA" "$B" "$REAL_HOME") || die "server.toml: $why"

  isolate_env "$B"
  [ -z "${M5TB_CHROME:-}" ] || export RCM_CHROME="$M5TB_CHROME"
  cd "$B/home"
  env | grep -E '^(RCM_|XDG_|HOME=)' | sed "s|$REAL_HOME|<HOME>|g" | sort >"$OUT/env.txt" || true
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  HAS_PR2=$("$PY" -I -c 'from remote_ci_monitor.store import Store; print(int(hasattr(Store, "record_late_result")))' 2>/dev/null || echo 0)
  HAS_PR3=$("$PY" -I -c 'from remote_ci_monitor.core import render_text as r; print(int(hasattr(r, "held_group")))' 2>/dev/null || echo 0)
  log "base <HOME>${B#"$REAL_HOME"} · port $P · PR2 $HAS_PR2 · PR3 $HAS_PR3 · mode $mode"

  if [ "$mode" = self ]; then
    self_test
    cleanup
    trap - EXIT
    log "self-test done · $FAILS failed · results in $OUT"
    [ "$FAILS" = 0 ]
    return
  fi

  (cd "$SRC" && git init -q -b main && echo m5tb >README &&
    git -c user.name=acceptance -c user.email=acceptance@example.invalid add README &&
    git -c user.name=acceptance -c user.email=acceptance@example.invalid commit -qm init) ||
    die "git repo failed"
  server_start || die "the test server did not come up (out/server.log)"
  new_token "$CTOK" acceptance || die "client token failed"
  new_token "$ATOK" acceptance-admin --admin || die "admin token failed"
  relay_start || die "the relay did not start (out/relay.log)"
  has_runner "$runners" run_kill_worker && run_f78_local
  for r in $runners; do
    "$r" || log "$r stopped early"
  done
  relay_do quit || true
  dump_jobs
  steps_table | awk -F '|' '$2 != "live" { printf "%-9s %-7s %s — %s\n", $1, "SKIP", $2 " row, not live", $4 }' >>"$OUT/summary.txt"
  cleanup
  trap - EXIT
  log "done · $FAILS failed · results in $OUT"
  [ "$FAILS" = 0 ]
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
