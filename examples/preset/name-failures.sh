#!/usr/bin/env bash
# Preset-side example: run one gate command, pass its output through untouched, and — once it
# has finished — print rcm's failure markers for the names it announced, in the exact format
# your script already prints. The wrapper exits with the command's own exit code.
#
#   [[presets]]
#   name = "gate"
#   argv = ["bash", "scripts/name-failures.sh", "--step", "test", "--", "flutter", "test"]
#
# Use it when the gate script cannot be changed. When it can, print `::rcm::fail::<name>` from
# the function that decides something is red — that is the primary way (docs/configuration.md,
# "Making a failure explain itself"). rcm never parses your output; it only records markers.
#
# What the wrapper never does is guess: a command that fails without printing a matching line
# names nothing, and the failure ledger stays empty. That is by design.
#
# Options
#   --step NAME       bracket the command in `::rcm::step::NAME` … `::rcm::step-end::ok|fail`
#                     (the end marker follows the command's exit code — this wrapper knows it)
#   --pattern REGEX   sed -E pattern with one group that captures the name; default `^FAIL: (.+)$`.
#                     Anchor it to the start of the line and match the exact line your script
#                     prints. A loose `FAIL` anywhere turns a mention into a verdict.
#
# Notes
# - bash only (PIPESTATUS). Not sh, not zsh.
# - The command's stdout and stderr are merged, as rcm itself does, so the log keeps their order.
# - Its stdout is a pipe here, so programs that block-buffer when not on a terminal deliver
#   their lines late and in bursts; `stdbuf -oL`, `PYTHONUNBUFFERED=1` or the tool's own
#   line-buffering flag restores the flow. Nothing is lost either way — markers come after.
# - Names are printed after the pipe has drained, so a marker never lands mid-output; control
#   characters are stripped and a name is cut at 120 characters, the same rules the server
#   applies; duplicates are printed once; at most 100 names (the server keeps 100 per job).
# - No `set -e`: it would end the wrapper at the failing command, before the exit code is saved.
set -u

usage() { echo "usage: $0 [--step NAME] [--pattern REGEX] -- COMMAND [ARG...]" >&2; exit 2; }

step=""
pattern='^FAIL: (.+)$'
while [ $# -gt 0 ]; do
  case $1 in
    --step) [ $# -ge 2 ] || usage; step=$2; shift 2 ;;
    --pattern) [ $# -ge 2 ] || usage; pattern=$2; shift 2 ;;
    --) shift; break ;;
    *) usage ;;
  esac
done
[ $# -gt 0 ] || usage

tmp=$(mktemp) || exit 3
trap 'rm -f "$tmp"' EXIT

[ -n "$step" ] && echo "::rcm::step::$step"
"$@" 2>&1 | tee "$tmp"
rc=${PIPESTATUS[0]}   # copy at once — the next command overwrites PIPESTATUS

if [ "$rc" -ne 0 ]; then
  # the names, only from lines that match the exact format, after the output has been passed on
  sed -nE "s/$pattern/\\1/p" "$tmp" \
    | tr -d '\000-\011\013-\037\177' \
    | awk '{ name = substr($0, 1, 120) } length(name) && !seen[name]++ { print name }' \
    | head -n 100 \
    | sed 's/^/::rcm::fail::/'
fi
if [ -n "$step" ]; then
  if [ "$rc" -eq 0 ]; then echo "::rcm::step-end::ok"; else echo "::rcm::step-end::fail"; fi
fi
exit "$rc"
