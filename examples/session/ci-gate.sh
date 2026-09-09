#!/usr/bin/env bash
# Session-side example: run the "gate" preset on the build machine and branch on the exit code.
# Requires RCM_SERVER / RCM_TOKEN (or ~/.config/rcm/client.toml) and `jq`.
set -u
out=$(rcm run gate -f scope="${1:-full}" --by "$(whoami)@$(hostname -s)")
rc=$?
job=$(jq -r .job_id <<<"$out")
case $rc in
  0) echo "gate green: $(jq -r .url <<<"$out")" ;;
  1)
    # failed_step is only set when the script declared it (::rcm::step-end::fail or
    # ::rcm::fail::<name>); otherwise last_step says where the job was, without blaming it.
    echo "gate red — $(jq -r '.failed_step // ("last step " + (.last_step // "unknown"))' <<<"$out")"
    jq -r .summary <<<"$out"
    # names the job declared, with how often each was red in the recent runs of this key
    jq -r '(.failures // [])[] | "  \(.name): \(.seen)/\(.window) recent runs (\(.verdict))"' <<<"$out"
    echo "log: rcm logs $job"
    ;;
  2) echo "cancelled or timed out: $(jq -r .state <<<"$out")" ;;
  *) echo "unknown (exit $rc) — check $(jq -r .url <<<"$out"); log: rcm logs $job" ;;
esac
exit $rc
