#!/usr/bin/env bash
# M5s 실기 — 진짜 `rcm serve` 프로세스로 docs/m5s-workplan.md §7 의 S1~S6(실기 칸)과
# 덧붙인 둘(action 표식 · 웹 `/`)을 돌린다. 테스트 스위트가 아니다(pytest 가 부르지 않는다).
#
#   scripts/acceptance/m5s_live.sh            # 포트 8796, 약 5분 10초(S1 이 진짜 5분을 기다린다)
#   M5S_PORT=8797 scripts/acceptance/m5s_live.sh
#
# 지키는 것:
#   - 이 체크아웃의 .venv/bin/rcm 만 쓴다. 운영(8787 · ~/.config/rcm · ~/.local/share/rcm ·
#     서비스 venv)은 건드리지 않는다. HOME 을 시험 폴더로 바꿔 운영 client.toml 을 안 읽는다.
#   - data_dir 은 ~/.local/share/rcm-devtest-m5s/data(/tmp 아래 금지 — 시작할 때 비운다).
#   - 시작할 때 지우는 폴더(M5S_BASE)는 허용 목록으로만 받는다: ~/.local/share/rcm-devtest-<이름>
#     한 단계. 설정 폴더(M5S_CONF_DIR)는 지우지 않지만 두 파일을 덮어쓰므로 M5S_BASE 안이거나
#     비어 있는(또는 없는) 절대 경로만 받는다.
#   - 프리셋은 sh -c 로 종료코드와 마커만 찍는 가짜, 알림 훅은 받은 env 를 파일에 쓰는 가짜다.
#   - 끝나면(실패해도) 서버와 백그라운드 rcm wait 를 모두 멈춘다.
#
# 결과: $B/out/summary.txt(판정 줄) · jobs.json(모든 잡의 최종 행) · notify.log · serve.log ·
# 시험마다 명령 출력. 종료 코드는 FAIL 이 하나라도 있으면 1.
set -u

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
RCM="$ROOT/.venv/bin/rcm"
PY="$ROOT/.venv/bin/python"
PORT=${M5S_PORT:-8796}
REAL_HOME=${HOME:-}
B=${M5S_BASE-$REAL_HOME/.local/share/rcm-devtest-m5s}
CONF=${M5S_CONF_DIR-$B/conf}
OUT="$B/out"
URL="http://127.0.0.1:$PORT"

die() { echo "m5s_live: $*" >&2; exit 2; }

# 절대 경로이고 `.`·`..` 조각이 없는가 — 아니면 die
plain_abs() { # plain_abs <이름> <경로>
  [ -n "$2" ] || die "$1 is empty"
  case "$2" in /*) ;; *) die "$1 must be an absolute path ($2)" ;; esac
  case "/$2/" in */../* | */./*) die "$1 must not contain . or .. ($2)" ;; esac
}

# 시작할 때 `rm -rf "$B"` 를 하므로 B 는 허용 목록으로만 받는다:
# $HOME/.local/share/rcm-devtest-<이름>, 이름은 한 단계(슬래시 없음)이고 비어 있지 않다.
# 그 밖(HOME, /, 운영 ~/.local/share/rcm, /tmp, 상대 경로)은 모두 거절한다.
case "$REAL_HOME" in /?*) ;; *) die "HOME must be an absolute path other than / ($REAL_HOME)" ;; esac
plain_abs M5S_BASE "$B"
DEVTEST_PREFIX="$REAL_HOME/.local/share/rcm-devtest-"
case "$B" in
  "$DEVTEST_PREFIX"?*) ;;
  *) die "M5S_BASE must be ${DEVTEST_PREFIX}<name> ($B)" ;;
esac
case "${B#"$DEVTEST_PREFIX"}" in
  */*) die "M5S_BASE must be one level: ${DEVTEST_PREFIX}<name> without / ($B)" ;;
esac

# 설정 폴더는 지우지 않지만 server.toml · hook.sh 를 덮어쓴다. 운영 ~/.config/rcm 같은 곳을
# 덮지 않게, B 안이거나 비어 있는(없는) 폴더만 받는다.
plain_abs M5S_CONF_DIR "$CONF"
case "$CONF" in
  "$B" | "$B/"*) ;;
  *)
    if [ -e "$CONF" ] && [ -n "$(ls -A "$CONF" 2>/dev/null)" ]; then
      die "M5S_CONF_DIR must be inside M5S_BASE or an empty directory ($CONF)"
    fi
    ;;
esac

[ -x "$RCM" ] || die "no $RCM — make the worktree's .venv first"
[ "$PORT" != 8787 ] || die "8787 is the production port"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then die "port $PORT is in use"; fi

rm -rf "$B"
mkdir -p "$B/data" "$B/home" "$OUT" "$CONF" || die "cannot create $B"
: > "$OUT/summary.txt"

# ── 가짜 저장소 · 훅 · 설정 ───────────────────────────────────────────────
G="git -c user.name=acceptance -c user.email=acceptance@example.invalid"
(cd "$B" && mkdir src && cd src && git init -q -b main && echo m5s > README && $G add README \
  && $G commit -qm init && git clone -q --bare "$B/src" "$B/app.git") || die "git setup failed"

cat > "$CONF/hook.sh" <<'EOF'
#!/bin/sh
# 가짜 알림 훅 — 받은 env 를 한 줄로 적는다
printf '%s job=%s state=%s preset=%s notify=%s exit=%s action=%s note=%s summary=%s\n' \
  "$(date -u +%H:%M:%S)" "$RCM_JOB_ID" "$RCM_STATE" "$RCM_PRESET" "$RCM_NOTIFY" \
  "$RCM_EXIT_CODE" "$RCM_ACTION_CODE" "$RCM_ACTION_NOTE" "$RCM_SUMMARY" >> "$NOTIFY_LOG"
EOF

RAN="$B/ran.log"
ok_argv() { # 실행되면 ran.log 에 한 줄을 남기고 0
  echo "[\"sh\", \"-c\", \"echo $1 \$RCM_JOB_ID \$(date -u +%H:%M:%S) >> $RAN\"]"
}

cat > "$CONF/server.toml" <<EOF
[server]
bind = "127.0.0.1"
port = $PORT
data_dir = "$B/data"
lanes = 3
admission = "always"
advertise = false

[display]
timezone = "UTC"

[[notify]]
name = "file-hook"
argv = ["sh", "-c", "NOTIFY_LOG=$OUT/notify.log sh $CONF/hook.sh"]
timeout_seconds = 10

[[notify]]
name = "retried-hook"
on = ["retried"]
argv = ["sh", "-c", "NOTIFY_LOG=$OUT/notify.log sh $CONF/hook.sh"]
timeout_seconds = 10

[[repos]]
name = "app"
url = "$B/app.git"

[[presets]]
name = "s1-ok"
argv = $(ok_argv s1-ok)
source_modes = ["git_ref"]
repo = "app"

[[presets]]
name = "s2-ok"
argv = $(ok_argv s2-ok)
source_modes = ["git_ref"]
repo = "app"

[[presets]]
name = "s3-ok"
argv = $(ok_argv s3-ok)
source_modes = ["git_ref"]
repo = "app"

[[presets]]
name = "s4-down"
argv = $(ok_argv s4-down)
source_modes = ["git_ref"]
repo = "app"
start_deadline_seconds = 15

[[presets]]
name = "s4-hold"
argv = ["sh", "-c", "sleep 60"]
source_modes = ["git_ref"]
repo = "app"
concurrency_group = "s4g"

[[presets]]
name = "s4-lane"
argv = $(ok_argv s4-lane)
source_modes = ["git_ref"]
repo = "app"
concurrency_group = "s4g"
start_deadline_seconds = 15

[[presets]]
name = "s5-later"
argv = ["sh", "-c", "echo s5-later \$RCM_JOB_ID \$(date -u +%H:%M:%S) >> $RAN; echo '::rcm::summary::not yet'; exit 75"]
source_modes = ["git_ref"]
repo = "app"
[presets.retry]
on_exit = 75
interval_seconds = 20
max_attempts = 3

[[presets]]
name = "x-login"
argv = ["sh", "-c", "echo '::rcm::action::login::sign in to the store again'; sleep 8; exit 75"]
source_modes = ["git_ref"]
repo = "app"
[presets.retry]
interval_seconds = 30
max_attempts = 2
EOF

# ── 도우미 ────────────────────────────────────────────────────────────────
export HOME="$B/home" RCM_SERVER="$URL"
unset RCM_CONFIG RCM_TOKEN
cd "$B/home" || die "no home"

SERVE_PID=""
BG_PIDS=()
FAILS=0

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$OUT/timeline.txt"; }
verdict() { # verdict <id> <PASS|FAIL> <글>
  printf '%-4s %s  %s\n' "$1" "$2" "$3" | tee -a "$OUT/summary.txt"
  [ "$2" = PASS ] || FAILS=$((FAILS + 1))
}

start_server() {
  "$RCM" serve --config "$CONF/server.toml" --port "$PORT" >> "$OUT/serve.log" 2>&1 &
  SERVE_PID=$!
  for _ in $(seq 1 50); do
    curl -fs "$URL/api/health" >/dev/null 2>&1 && { log "server up (pid $SERVE_PID)"; return 0; }
    sleep 0.2
  done
  die "server did not come up — see $OUT/serve.log"
}

stop_server() {
  [ -n "$SERVE_PID" ] || return 0
  kill -TERM "$SERVE_PID" 2>/dev/null
  for _ in $(seq 1 100); do kill -0 "$SERVE_PID" 2>/dev/null || break; sleep 0.1; done
  kill -0 "$SERVE_PID" 2>/dev/null && kill -KILL "$SERVE_PID" 2>/dev/null
  wait "$SERVE_PID" 2>/dev/null
  log "server stopped (pid $SERVE_PID)"
  SERVE_PID=""
}

cleanup() {
  for p in "${BG_PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
  stop_server
}
trap cleanup EXIT
trap 'exit 130' INT TERM

client() { RCM_TOKEN="$TOKEN" "$RCM" --no-follow "$@"; }
admin() { RCM_TOKEN="$ADMIN" "$RCM" --no-follow "$@"; }
job_of() { grep -o 'job #[0-9]*' | head -1 | tr -dc 0-9; }

# 잡 하나의 칸을 읽는다: jf <id> <key>...
jf() {
  local id=$1; shift
  curl -fs -H "Authorization: Bearer $TOKEN" "$URL/jobs/$id" | "$PY" -I -c '
import json, sys
d = json.load(sys.stdin)
print(" ".join(str(d.get(k)) for k in sys.argv[1:]))' "$@"
}

# 시각(UTC ISO) 까지 잔다 + 여유 초
sleep_until() {
  local target=$1 extra=${2:-0} now t
  t=$("$PY" -I -c 'import sys,datetime as d; print(int(d.datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")).timestamp()))' "$target")
  now=$(date +%s)
  [ $((t + extra - now)) -gt 0 ] && sleep $((t + extra - now))
  return 0
}

# 상태가 바뀌기를 기다린다: wait_state <id> <state> <초>
wait_state() {
  local id=$1 want=$2 limit=$3 i got
  for i in $(seq 1 "$limit"); do
    got=$(jf "$id" state)
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  return 1
}

# ── 0. 서버 · 토큰 ────────────────────────────────────────────────────────
TOKEN=$("$RCM" token --config "$CONF/server.toml" add live-client 2>/dev/null | tail -1)
ADMIN=$("$RCM" token --config "$CONF/server.toml" add live-admin --admin 2>/dev/null | tail -1)
[ -n "$TOKEN" ] && [ -n "$ADMIN" ] || die "token add failed"
start_server
RUN=(run --source git_ref --ref main --by acceptance --no-wait)

# ── S1 · S6: 5분 뒤 예약, 같은 예약 한 번 더 ───────────────────────────────
log "S1 submit s1-ok --in 5m"
client "${RUN[@]}" s1-ok --in 5m > "$OUT/s1-submit.txt" 2>&1
J1=$(job_of < "$OUT/s1-submit.txt")
log "S1 job #$J1 $(jf "$J1" state created_at not_before start_deadline)"

log "S6 submit the same schedule again (CLI)"
client "${RUN[@]}" s1-ok --in 5m > "$OUT/s6-cli.txt" 2>&1
S6_EXIT=$?
log "S6 cli exit $S6_EXIT: $(cat "$OUT/s6-cli.txt")"
curl -s -o "$OUT/s6-http.json" -w '%{http_code}' -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"preset":"s1-ok","inputs":{},"source":{"mode":"git_ref","ref":"main"},"start_in_seconds":300}' \
  "$URL/jobs" > "$OUT/s6-http-code.txt"
S6_HTTP=$(cat "$OUT/s6-http-code.txt")
S6_CODE=$("$PY" -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("code"), d.get("job_id"))' "$OUT/s6-http.json")
log "S6 http $S6_HTTP $S6_CODE"
SCHEDULED_COUNT=$(client jobs --state scheduled --json | "$PY" -I -c 'import json,sys; print(sum(1 for j in json.load(sys.stdin) if j["preset"]=="s1-ok"))')
if [ "$S6_HTTP" = 409 ] && [ "$S6_CODE" = "schedule_exists $J1" ] && [ "$SCHEDULED_COUNT" = 1 ]; then
  verdict S6 PASS "409 schedule_exists job_id=$J1, one s1-ok schedule; rcm run exit $S6_EXIT"
else
  verdict S6 FAIL "http=$S6_HTTP code=$S6_CODE schedules=$SCHEDULED_COUNT cli_exit=$S6_EXIT"
fi

# ── S2: 예약 뒤 취소 ──────────────────────────────────────────────────────
log "S2 submit s2-ok --in 90s, then cancel"
client "${RUN[@]}" s2-ok --in 90s > "$OUT/s2-submit.txt" 2>&1
J2=$(job_of < "$OUT/s2-submit.txt")
client cancel "$J2" > "$OUT/s2-cancel.txt" 2>&1
log "S2 job #$J2 cancel exit $? · $(jf "$J2" state not_before started_at finished_at)"

# ── S3: rcm reload, 재시작 뒤에도 예약이 남는다 ────────────────────────────
S3_AT=$(date -u -v+4M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+4 min' +%Y-%m-%dT%H:%M:%SZ)
log "S3 submit s3-ok --at $S3_AT"
client "${RUN[@]}" s3-ok --at "$S3_AT" > "$OUT/s3-submit.txt" 2>&1
J3=$(job_of < "$OUT/s3-submit.txt")
# reload 이 실제로 무언가를 읽게 프리셋 하나를 더한다
cat >> "$CONF/server.toml" <<EOF

[[presets]]
name = "s3-added-by-reload"
argv = $(ok_argv s3-added)
source_modes = ["git_ref"]
repo = "app"
EOF
admin reload > "$OUT/s3-reload.txt" 2>&1
log "S3 reload exit $?: $(tr '\n' ' ' < "$OUT/s3-reload.txt")"
client jobs --state scheduled > "$OUT/s3-after-reload.txt" 2>&1
AFTER_RELOAD="$(jf "$J1" state) $(jf "$J3" state)"
log "S3 after reload: #$J1 #$J3 = $AFTER_RELOAD"

# ── S4a: 서버를 끈 채 마감이 지난다 ────────────────────────────────────────
log "S4a submit s4-down --in 20s (start deadline 15s)"
client "${RUN[@]}" s4-down --in 20s > "$OUT/s4a-submit.txt" 2>&1
J4=$(job_of < "$OUT/s4a-submit.txt")
J4_DEADLINE=$(jf "$J4" start_deadline)
log "S4a job #$J4 $(jf "$J4" state not_before start_deadline)"
stop_server
log "S4a server down until past $J4_DEADLINE"
sleep_until "$J4_DEADLINE" 5
start_server
sleep 2
grep -E 'recovery' "$OUT/serve.log" | tail -1 > "$OUT/s4a-recovery.txt"
log "S4a $(cat "$OUT/s4a-recovery.txt")"
S4A="$(jf "$J4" state started_at summary)"
log "S4a job #$J4 = $S4A"

AFTER_RESTART="$(jf "$J1" state) $(jf "$J3" state)"
client jobs --state scheduled > "$OUT/s3-after-restart.txt" 2>&1
log "S3 after restart: #$J1 #$J3 = $AFTER_RESTART"

# ── S4b: 레인(그룹)이 막혀 마감이 지난다 ───────────────────────────────────
log "S4b s4-hold now + s4-lane --in 10s in the same concurrency group"
client "${RUN[@]}" s4-hold > "$OUT/s4b-hold.txt" 2>&1
J5=$(job_of < "$OUT/s4b-hold.txt")
client "${RUN[@]}" s4-lane --in 10s > "$OUT/s4b-submit.txt" 2>&1
J6=$(job_of < "$OUT/s4b-submit.txt")
log "S4b hold #$J5 $(jf "$J5" state) · lane #$J6 $(jf "$J6" state not_before start_deadline)"

# ── S5: 75 를 반복하다 상한에서 exhausted ──────────────────────────────────
log "S5 submit s5-later --in 10s (retry every 20s, 3 attempts)"
client "${RUN[@]}" s5-later --in 10s > "$OUT/s5-submit.txt" 2>&1
J7=$(job_of < "$OUT/s5-submit.txt")
(client wait --job "$J7" > "$OUT/s5-wait.json" 2> "$OUT/s5-wait.err"; echo $? > "$OUT/s5-wait.exit") &
BG_PIDS+=($!)

# ── X1: action 표식 + --return-on-action ──────────────────────────────────
log "X1 submit x-login now"
client "${RUN[@]}" x-login > "$OUT/x1-submit.txt" 2>&1
J8=$(job_of < "$OUT/x1-submit.txt")
client wait --job "$J8" --return-on-action --timeout 60 > "$OUT/x1-wait.json" 2> "$OUT/x1-wait.err"
X1_EXIT=$?
log "X1 wait --return-on-action exit $X1_EXIT · #$J8 $(jf "$J8" state)"
curl -fs "$URL/api/status" > "$OUT/x1-status-running.json"
wait_state "$J8" retried 30
sleep 6
curl -fs "$URL/api/status" > "$OUT/x1-status-between.json"
X1_ROWS=$("$PY" -I - "$OUT/x1-status-running.json" "$OUT/x1-status-between.json" "$J8" <<'EOF'
import json, sys
def rows(path):
    d = json.load(open(path))
    for p in d.get("pools", []):
        for where in ("queue", "scheduled", "recent"):
            for r in p.get(where) or []:
                yield where, r
chain = int(sys.argv[3])
for label, path in (("running", sys.argv[1]), ("between", sys.argv[2])):
    for where, r in rows(path):
        rid = r.get("id", r.get("job_id"))
        if rid == chain or r.get("chain_id") == chain:
            a = r.get("action") or {}
            print(f"{label}:{where}#{rid}:{r.get('state')}:{r.get('reason')}:{a.get('code')}")
EOF
)
log "X1 status rows: $(echo $X1_ROWS)"

# ── 웹 · rcm check ────────────────────────────────────────────────────────
WEB=$(curl -s -o "$OUT/web-index.html" -w '%{http_code}' "$URL/")
log "web / -> $WEB"
client check > "$OUT/check.txt" 2>&1
log "rcm check exit $? · $(grep -E '^(ok|FAIL|warn) +client' "$OUT/check.txt")"

# ── S4b 판정 ──────────────────────────────────────────────────────────────
sleep_until "$(jf "$J6" start_deadline)" 8
S4B="$(jf "$J6" state started_at summary) · hold $(jf "$J5" state)"
log "S4b lane #$J6 = $S4B"
client cancel "$J5" > "$OUT/s4b-cancel-hold.txt" 2>&1

# ── S5 끝까지 ─────────────────────────────────────────────────────────────
for _ in $(seq 1 120); do [ -s "$OUT/s5-wait.exit" ] && break; sleep 1; done
S5_EXIT=$(cat "$OUT/s5-wait.exit" 2>/dev/null || echo none)
log "S5 rcm wait exit $S5_EXIT"
wait_state "$J8" exhausted 5 >/dev/null 2>&1
for _ in $(seq 1 60); do
  last=$(jf "$J8" retried_as)
  [ "$last" = None ] && break
  wait_state "$last" exhausted 60 && break
done

# ── S1 · S3: 정한 시각까지 기다린다 ────────────────────────────────────────
log "S1 waiting for #$J1 at $(jf "$J1" not_before)"
client wait --job "$J1" > "$OUT/s1-wait.json" 2> "$OUT/s1-wait.err"
S1_EXIT=$?
client wait --job "$J3" > "$OUT/s3-wait.json" 2> "$OUT/s3-wait.err"
S3_EXIT=$?
log "S1 wait exit $S1_EXIT · S3 wait exit $S3_EXIT"
sleep 3  # 알림 스레드가 훅을 부를 시간

# ── 모든 잡의 최종 행 ─────────────────────────────────────────────────────
client jobs --json > "$OUT/jobs-list.json" 2>&1
"$PY" -I - "$URL" "$TOKEN" "$OUT/jobs.json" <<'EOF'
import json, sys, urllib.request
url, token, dest = sys.argv[1:4]
rows = []
for n in range(1, 40):
    req = urllib.request.Request(f"{url}/jobs/{n}", headers={"Authorization": f"Bearer {token}"})
    try:
        rows.append(json.load(urllib.request.urlopen(req)))
    except Exception:
        break
keys = ("id", "preset", "state", "created_at", "not_before", "start_deadline", "started_at",
        "finished_at", "exit_code", "summary", "chain_id", "attempt", "retry_of", "retried_as",
        "max_attempts", "action")
json.dump([{k: r.get(k) for k in keys} for r in rows], open(dest, "w"), indent=1)
for r in rows:
    print(" | ".join(str(r.get(k)) for k in keys))
EOF
cp "$OUT/notify.log" "$OUT/notify.final.log" 2>/dev/null

# ── 판정 ──────────────────────────────────────────────────────────────────
row() { "$PY" -I -c '
import json, sys
rows = {r["id"]: r for r in json.load(open(sys.argv[1]))}
r = rows.get(int(sys.argv[2]), {})
print(" ".join(str(r.get(k)) for k in sys.argv[3:]))' "$OUT/jobs.json" "$@"; }
secs() { "$PY" -I -c '
import sys, datetime as d
a, b = (d.datetime.fromisoformat(x.replace("Z", "+00:00")) for x in sys.argv[1:3])
print(int((b - a).total_seconds()))' "$1" "$2"; }
hook() { local n; n=$(grep -c "job=$1 state=$2 " "$OUT/notify.log" 2>/dev/null); echo "${n:-0}"; }

read -r st nb sa <<< "$(row "$J1" state not_before started_at)"
late=$(secs "$nb" "$sa")
if [ "$st" = succeeded ] && [ "$late" -ge 0 ] && [ "$late" -le 10 ] && [ "$S1_EXIT" = 0 ] \
  && grep -q "^s1-ok $J1 " "$RAN"; then
  verdict S1 PASS "#$J1 not_before $nb started $sa (+${late}s) succeeded, rcm wait 0"
else
  verdict S1 FAIL "#$J1 $st not_before $nb started $sa wait=$S1_EXIT"
fi

read -r st sa <<< "$(row "$J2" state started_at)"
if [ "$st" = cancelled ] && [ "$sa" = None ] && ! grep -q "^s2-ok " "$RAN"; then
  verdict S2 PASS "#$J2 cancelled, never started, no run line"
else
  verdict S2 FAIL "#$J2 $st started=$sa"
fi

read -r st <<< "$(row "$J3" state)"
if [ "$AFTER_RELOAD" = "scheduled scheduled" ] && [ "$AFTER_RESTART" = "scheduled scheduled" ] \
  && [ "$st" = succeeded ] && [ "$S3_EXIT" = 0 ]; then
  verdict S3 PASS "#$J1 #$J3 scheduled after reload and after restart; #$J3 ran at its time"
else
  verdict S3 FAIL "reload=$AFTER_RELOAD restart=$AFTER_RESTART #$J3=$st wait=$S3_EXIT"
fi

read -r st sa <<< "$(row "$J4" state started_at)"
if [ "$st" = expired ] && [ "$sa" = None ] && [ "$(hook "$J4" expired)" = 1 ] && ! grep -q "^s4-down " "$RAN"; then
  verdict S4a PASS "#$J4 expired at start-up, hook wrote it once"
else
  verdict S4a FAIL "#$J4 $st started=$sa hook=$(hook "$J4" expired)"
fi

read -r st sa <<< "$(row "$J6" state started_at)"
if [ "$st" = expired ] && [ "$sa" = None ] && [ "$(hook "$J6" expired)" = 1 ]; then
  verdict S4b PASS "#$J6 expired while #$J5 held the group, hook wrote it once"
else
  verdict S4b FAIL "#$J6 $st started=$sa hook=$(hook "$J6" expired)"
fi

S5_CHAIN=$("$PY" -I - "$OUT/jobs.json" "$J7" <<'EOF'
import json, sys, datetime as d
rows = [r for r in json.load(open(sys.argv[1])) if r.get("chain_id") == int(sys.argv[2])]
rows.sort(key=lambda r: r["attempt"])
ts = lambda x: d.datetime.fromisoformat(x.replace("Z", "+00:00"))
states = ",".join(r["state"] for r in rows)
gaps = [int((ts(b["started_at"]) - ts(a["finished_at"])).total_seconds()) for a, b in zip(rows, rows[1:])]
print(states, ",".join(map(str, gaps)) or "-", len(rows))
EOF
)
read -r s5_states s5_gaps s5_n <<< "$S5_CHAIN"
gaps_ok=$("$PY" -I -c 'import sys; g=[int(x) for x in sys.argv[1].split(",") if x not in ("", "-")]; print(int(len(g)==2 and all(20<=x<=27 for x in g)))' "$s5_gaps")
if [ "$s5_states" = "retried,retried,exhausted" ] && [ "$gaps_ok" = 1 ] && [ "$S5_EXIT" = 1 ] \
  && grep -q '"attempts"' "$OUT/s5-wait.json"; then
  verdict S5 PASS "chain #$J7: $s5_states, gaps ${s5_gaps}s, rcm wait 1 with attempts[]"
else
  verdict S5 FAIL "chain #$J7: $s5_states gaps=$s5_gaps n=$s5_n wait=$S5_EXIT"
fi

if [ "$X1_EXIT" = 4 ] && echo "$X1_ROWS" | grep -q ':needs_action:login' \
  && grep -q 'needs action' "$OUT/x1-wait.err"; then
  verdict X1 PASS "--return-on-action 4; needs_action rows: $(echo $X1_ROWS)"
else
  verdict X1 FAIL "wait=$X1_EXIT rows=$(echo $X1_ROWS)"
fi

if [ "$WEB" = 200 ]; then verdict X2 PASS "GET / 200"; else verdict X2 FAIL "GET / $WEB"; fi

stop_server
trap - EXIT
log "done · $FAILS failed · results in $OUT"
[ "$FAILS" = 0 ]
