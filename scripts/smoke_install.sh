#!/usr/bin/env bash
# remote_ci_monitor — install smoke.
#
# Proves the README "5-minute setup" on a fresh interpreter: build (or take) the wheel, install it
# into a brand-new venv, then run the exact commands the README shows — build machine first
# (`rcm init server` → `rcm token add` → `rcm serve`), then session machine (`rcm init client`
# → `rcm check` → `rcm run ok`) — all on loopback. No network is needed once the wheel exists.
#
#   scripts/smoke_install.sh [WHEEL]      # no WHEEL: builds one with `python -m build`
#
# Portable bash 3.2 (macOS default): no associative arrays, no `timeout`, python3 + curl only.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WHEEL="${1:-}"
PY="${PYTHON:-python3}"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/rcm-smoke.XXXXXX")
SERVER_PID=""
STEP="start"

step() { STEP="$*"; echo "smoke: $*" >&2; }

cleanup() {
  rc=$?
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 0.5; done
    kill -KILL "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$rc" -ne 0 ]; then
    echo "smoke: FAILED at step: $STEP" >&2
    if [ -f "$WORK/server.log" ]; then
      echo "--- server log (tail) ---" >&2
      tail -n 40 "$WORK/server.log" >&2
    fi
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap cleanup EXIT

# ── 0. README ↔ script: every `rcm …` command in the README's marked blocks must appear here ──
step "README commands are covered by this script"
readme_cmds=$(sed -n '/<!-- smoke:begin/,/<!-- smoke:end -->/p' "$ROOT/README.md" \
  | sed 's/#.*//' | grep -E '^[[:space:]]*rcm ' | awk '{print $1, $2, $3}' | sed 's/ *$//' | sort -u)
[ -n "$readme_cmds" ] || { echo "smoke: README has no <!-- smoke:begin --> block" >&2; exit 1; }
while IFS= read -r cmd; do
  grep -qF -- "$cmd" "$0" || { echo "smoke: README command not covered: $cmd" >&2; exit 1; }
done <<< "$readme_cmds"

# ── 1. fresh venv + wheel ──
step "python version"
"$PY" - <<'PYEOF'
import sys, tarfile
assert sys.version_info >= (3, 11, 4) and hasattr(tarfile, "data_filter"), sys.version
PYEOF
step "create venv"
"$PY" -m venv "$WORK/venv"
VENV="$WORK/venv/bin"
if [ -z "$WHEEL" ]; then
  step "build wheel (python -m build)"
  "$VENV/python" -m pip install -q --disable-pip-version-check build
  "$VENV/python" -m build --wheel --outdir "$WORK/dist" "$ROOT" >"$WORK/build.log" 2>&1 \
    || { cat "$WORK/build.log" >&2; exit 1; }
  WHEEL=$(ls "$WORK"/dist/remote_ci_monitor-*.whl)
fi
step "install wheel $(basename "$WHEEL")"
"$VENV/python" -m pip install -q --disable-pip-version-check "$WHEEL"
RCM="$VENV/rcm"
WHEEL_VER=$(basename "$WHEEL" | cut -d- -f2)

step "rcm version"
VER_LINE=$("$RCM" version)
case "$VER_LINE" in
  "rcm $WHEEL_VER "*) ;;
  *) echo "smoke: version mismatch: '$VER_LINE' vs wheel $WHEEL_VER" >&2; exit 1 ;;
esac

# ── 2. build machine (README block 1) ──
export HOME="$WORK/home"
mkdir -p "$HOME"
unset XDG_CONFIG_HOME RCM_CONFIG RCM_SERVER RCM_TOKEN
step "rcm init server"
CFG=$("$RCM" init server)
[ -f "$CFG" ] || { echo "smoke: init server did not write $CFG" >&2; exit 1; }
grep -q '^name = "ok"' "$CFG" || { echo "smoke: template lacks the ok preset" >&2; exit 1; }

step "rcm token add"
TOKEN=$("$RCM" token add laptop)
[ -n "$TOKEN" ] || { echo "smoke: empty token" >&2; exit 1; }

start_server() {
  PORT=$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  "$RCM" serve --port "$PORT" --data-dir "$WORK/data" >"$WORK/server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then return 0; fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then return 1; fi
    sleep 0.5
  done
  return 1
}
step "rcm serve"
if ! start_server; then
  echo "smoke: server did not come up on port $PORT, retrying once" >&2
  SERVER_PID=""
  start_server || exit 1
fi
SERVER_URL="http://127.0.0.1:$PORT"
curl -sf "$SERVER_URL/api/health" | grep -q '"ok": *true' || { echo "smoke: health not ok" >&2; exit 1; }

# ── 3. session machine (README block 2) ──
step "rcm init client --server"
"$RCM" init client --server "$SERVER_URL" >/dev/null
export RCM_TOKEN="$TOKEN"
step "rcm check"
"$RCM" check

step "rcm run ok"
mkdir -p "$WORK/project" && echo "hello" >"$WORK/project/README.txt"
cd "$WORK/project"
set +e
OUT=$("$RCM" run ok)
RC=$?
set -e
[ "$RC" -eq 0 ] || { echo "smoke: rcm run ok exited $RC: $OUT" >&2; exit 1; }
"$PY" -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["state"]=="succeeded", d; assert d["wait_exit_code"]==0, d' "$OUT"

step "rcm top"
"$RCM" top | grep -q "ok" || { echo "smoke: rcm top does not show the ok job" >&2; exit 1; }
step "rcm jobs --json"
"$RCM" jobs --json | "$PY" -c 'import json,sys; rows=json.load(sys.stdin); assert any(r.get("preset")=="ok" for r in rows), rows'

step "web UI"
curl -sf "$SERVER_URL/" | grep -q '<title>rcm queue</title>' || { echo "smoke: web UI missing" >&2; exit 1; }

# ── 4. stop cleanly ──
step "SIGTERM stops the server"
kill -TERM "$SERVER_PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 0.5; done
kill -0 "$SERVER_PID" 2>/dev/null && { echo "smoke: server still alive 5 s after SIGTERM" >&2; exit 1; }
SERVER_PID=""

PYV=$("$VENV/python" -c 'import platform; print(platform.python_version())')
echo "smoke: ok (rcm $WHEEL_VER, python $PYV, $(uname -s))"
