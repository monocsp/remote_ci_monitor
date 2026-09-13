#!/usr/bin/env bash
# Session-side example: bring this machine's rcm client up to the server's version.
# The server hands out the wheel of the code it is running (GET /api/health → client_wheel),
# so no release, no GitHub and no network beyond the server itself are needed.
#
# Requires RCM_SERVER (http://<build-machine>:8787), curl and jq. RCM_TOKEN is sent when set
# (needed only with read_auth = "basic"). RCM_VENV is the virtual environment that holds rcm;
# it defaults to the one the `rcm` on PATH lives in. Every step must succeed: a health call
# that fails, a wheel whose sha256 does not match, or a pip error stops the script with a
# non-zero exit and nothing installed.
set -euo pipefail

server="${RCM_SERVER:?set RCM_SERVER to http://<build-machine>:8787}"
server="${server%/}"
venv="${RCM_VENV:-$(dirname "$(dirname "$(command -v rcm)")")}"
python="$venv/bin/python"
[ -x "$python" ] || { echo "update-client: no python at $python (set RCM_VENV)" >&2; exit 2; }

auth=()
[ -n "${RCM_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $RCM_TOKEN")

health=$(curl -fsS ${auth[@]+"${auth[@]}"} "$server/api/health")
version=$(jq -r .version <<<"$health")
path=$(jq -r '.client_wheel.path // empty' <<<"$health")
expected=$(jq -r '.client_wheel.sha256 // empty' <<<"$health")
if [ -z "$path" ] || [ -z "$expected" ]; then
  echo "update-client: server v$version has no client wheel:" \
    "$(jq -r '.client_wheel_error // "no client_wheel in /api/health (server too old?)"' <<<"$health")" >&2
  exit 3
fi

tmp=$(mktemp "${TMPDIR:-/tmp}/rcm-client.XXXXXX")
trap 'rm -f "$tmp"' EXIT
curl -fsS ${auth[@]+"${auth[@]}"} -o "$tmp" "$server$path"

actual=$("$python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$tmp")
if [ "$actual" != "$expected" ]; then
  echo "update-client: sha256 mismatch for $path — got $actual, server says $expected" >&2
  exit 4
fi

# pip wants the wheel's own file name to recognise it as a wheel
wheel="$(dirname "$tmp")/$(basename "$path")"
mv "$tmp" "$wheel"
trap 'rm -f "$wheel"' EXIT
"$python" -m pip install -q --disable-pip-version-check --upgrade "$wheel"

installed=$("$venv/bin/rcm" version)
echo "$installed"
case "$installed" in
  "rcm $version "*) ;;
  *) echo "update-client: installed '$installed' but the server is v$version" >&2; exit 5 ;;
esac
