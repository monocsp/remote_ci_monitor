#!/usr/bin/env bash
# Session-side example: run the "gate" preset on the build machine and branch on the exit code.
# Requires RCM_SERVER / RCM_TOKEN (or ~/.config/rcm/client.toml) and `jq`.
set -u
out=$(rcm run gate -f scope="${1:-full}" --by "$(whoami)@$(hostname -s)")
rc=$?
case $rc in
  0) echo "gate green: $(jq -r .url <<<"$out")" ;;
  1) echo "gate red — failed step: $(jq -r .failed_step <<<"$out")"; jq -r .summary <<<"$out" ;;
  2) echo "cancelled or timed out: $(jq -r .state <<<"$out")" ;;
  *) echo "unknown (exit $rc) — check $(jq -r .url <<<"$out")" ;;
esac
exit $rc
