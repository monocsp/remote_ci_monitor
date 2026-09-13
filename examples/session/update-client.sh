#!/usr/bin/env bash
# 세션 쪽 예시: 이 머신의 rcm 클라이언트를 서버의 버전으로 올린다.
# 서버는 자기가 돌리는 코드의 wheel 을 준다(GET /api/health → client_wheel) — 릴리스도,
# GitHub 도, 서버 밖 네트워크도 필요 없다.
#
# 필요한 것: RCM_SERVER(http://<build-machine>:8787) · curl · jq. RCM_TOKEN 은 있으면 보낸다
# (read_auth = "basic" 일 때만 필요). RCM_VENV 는 rcm 이 든 가상환경 — 없으면 PATH 의 rcm 이
# 사는 곳. 어느 단계가 실패해도 0 이 아닌 종료 코드로 멈춘다: health 를 못 읽으면 3,
# sha256 이 다르면 4, 설치 뒤 버전이 다르면 5. pip 자체가 도중에 실패하면 pip 의 종료 코드이고
# 그때의 환경은 pip 가 남긴 그대로다.
#
# 모든 임시 파일은 이 실행만의 디렉터리(mktemp -d, 0700) 안에 있고 끝나면 통째로 지운다 —
# 검증한 파일을 다른 실행이 덮을 수 있는 공용 경로는 없다. 토큰은 curl 의 인수가 아니라
# 그 디렉터리 안의 0600 설정 파일(-K)로 건넨다: 같은 머신의 다른 사용자가 ps 로 읽지 못한다.
set -euo pipefail

server="${RCM_SERVER:?set RCM_SERVER to http://<build-machine>:8787}"
server="${server%/}"
venv="${RCM_VENV:-$(dirname "$(dirname "$(command -v rcm)")")}"
python="$venv/bin/python"
[ -x "$python" ] || { echo "update-client: no python at $python (set RCM_VENV)" >&2; exit 2; }

umask 077
tmp=$(mktemp -d "${TMPDIR:-/tmp}/rcm-client.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# curl 설정 파일 — 토큰이 있으면 header 한 줄, 없으면 빈 파일(둘 다 argv 에는 안 보인다)
curlrc="$tmp/curlrc"
: >"$curlrc"
if [ -n "${RCM_TOKEN:-}" ]; then
  printf 'header = "Authorization: Bearer %s"\n' "$RCM_TOKEN" >"$curlrc"
fi

# -f 대신 상태 코드를 직접 본다: janitor stale · worker down 이면 health 는 503 이지만
# 본문의 client_wheel 은 멀쩡하다 — 그 판정은 본문으로 한다.
status=$(curl -sS -K "$curlrc" -o "$tmp/health.json" -w '%{http_code}' "$server/api/health")
health=$(cat "$tmp/health.json")
version=$(jq -r '.version // empty' <<<"$health" 2>/dev/null || true)
if [ -z "$version" ]; then
  echo "update-client: $server/api/health answered $status:" \
    "$(jq -r '.error // "no health document"' <<<"$health" 2>/dev/null || echo "not JSON")" >&2
  exit 3
fi
path=$(jq -r '.client_wheel.path // empty' <<<"$health")
expected=$(jq -r '.client_wheel.sha256 // empty' <<<"$health")
if [ -z "$path" ] || [ -z "$expected" ]; then
  echo "update-client: server v$version has no client wheel:" \
    "$(jq -r '.client_wheel_error // "no client_wheel in /api/health (server too old?)"' <<<"$health")" >&2
  exit 3
fi
if [ "$status" != "200" ]; then
  echo "update-client: server health is $status ($(jq -r '.error // "?"' <<<"$health"))" \
    "but its client wheel is fine — installing v$version" >&2
fi

# pip 는 파일 이름으로 wheel 임을 안다 — 자기 디렉터리 안에 서버가 준 이름 그대로 받는다
wheel="$tmp/$(basename "$path")"
status=$(curl -sS -K "$curlrc" -o "$wheel" -w '%{http_code}' "$server$path")
if [ "$status" != "200" ]; then
  echo "update-client: $server$path answered $status:" \
    "$(jq -r '.error // "?"' <"$wheel" 2>/dev/null || echo "not JSON")" >&2
  exit 3
fi

actual=$("$python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$wheel")
if [ "$actual" != "$expected" ]; then
  echo "update-client: sha256 mismatch for $path — got $actual, server says $expected" >&2
  exit 4
fi

"$python" -m pip install -q --disable-pip-version-check --upgrade "$wheel"

installed=$("$venv/bin/rcm" version)
echo "$installed"
case "$installed" in
  "rcm $version "*) ;;
  *) echo "update-client: installed '$installed' but the server is v$version" >&2; exit 5 ;;
esac
