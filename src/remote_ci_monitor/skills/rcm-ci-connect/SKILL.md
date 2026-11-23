---
name: rcm-ci-connect
description: Let a repository choose, in one file (`.rcm/ci.toml`), whether its checks run on GitHub Actions, on the build machine's rcm, or both — GitHub's required check becomes a light job that waits for the `rcm/ci` commit status a build-machine watcher and a `[[notify]]` hook post, and GitHub runs the checks itself whenever rcm does not answer in time. Use it when the owner wants a repository's CI on the build machine without losing GitHub as the fallback; the rcm server itself is not changed.
---

# rcm-ci-connect

GitHub cannot reach a build machine behind a home router, so the build machine talks to GitHub.
A watcher on the build machine sees open pull requests and pushes, submits the head commit to
rcm and posts a commit status; a `[[notify]]` hook posts the result when the job ends. On the
GitHub side, the required check `test` waits for that status and judges it — and when rcm does
not answer (no status, queued or running too long, the server down, an environment failure), it
runs the checks on GitHub instead. The rcm server never calls GitHub: everything that does is in
this skill's templates (remote_ci_monitor decision 175).

The contract is `docs/m5r-workplan.md` in remote_ci_monitor: §2 for the design, §5 for the names.

## When to use

- A repository whose CI should run on the build machine (speed, runner shortage, a device only
  that machine has) while GitHub stays the fallback — or a repository that wants rcm only
  (`fallback = "none"`).
- The build machine already runs `rcm serve` with the repository under `[[repos]]` (git_ref
  presets need it), and the owner can create two tokens (below).
- Not for forks: pull requests from forks, deleted forks and `dependabot[bot]` always run on
  GitHub. Someone else's code never runs on the build machine.

## What it creates

| File | Where | From |
|---|---|---|
| `.rcm/ci.toml` | the repository | `templates/ci.toml` |
| `scripts/ci/rcm_ci_config.py` · `rcm_ci_wait.py` | the repository, byte-for-byte copies | `templates/` |
| `scripts/ci/rcm_ci.sh` | the repository: the job the preset runs | `templates/rcm_ci_job.sh` (fill the steps) |
| `.github/workflows/ci.yml` · `checks.yml` | the repository | `templates/ci.yml` · `checks.yml` |
| `rcm_ci_config.py` · `rcm_ci_watch.py` · `rcm_ci_status.py` | build machine `~/.config/rcm/scripts/` | `templates/` |
| `ci-watch.toml` | build machine `~/.config/rcm/` | `templates/ci-watch.toml` |
| `presets.<preset>.toml` + a `[[notify]]` rule | build machine, next to `server.toml` (`include`) | `templates/presets.ci.toml` |
| `com.remote-ci-monitor.ci-watch.plist` | build machine `~/Library/LaunchAgents/` | `templates/` |

The scripts live under `~/.config/rcm/scripts/` on the build machine because launchd agents
cannot read `~/Documents`. The three scripts import `rcm_ci_config.py` from their own folder;
copy all of them together.

## Inputs it asks for

| Question | Default |
|---|---|
| GitHub `owner/repo` | `git remote get-url origin` |
| Mode · fallback | `rcm` · `github` |
| Preset name | `<repo>-ci` |
| Legs of the workflow and where each runs | `ubuntu = "rcm"`, `macos = "rcm-only"` |
| Branches whose pushes also run on rcm | `["dev"]` |
| Accounts whose `rcm/ci` statuses count (`creators`) | the owner's login |
| The build machine's server URL and pool | `http://127.0.0.1:8787` · none |

## Steps

1. Ask the inputs. Write `.rcm/ci.toml` from `templates/ci.toml`, filling `{{preset}}` and
   `{{owner}}`. Start with `mode = "github"` and switch to `rcm` only after step 7 passes —
   with no watcher running, every pull request would wait `pickup_seconds` before GitHub takes
   over.
2. Copy `rcm_ci_config.py` and `rcm_ci_wait.py` to `scripts/ci/`, unchanged. Copy
   `rcm_ci_job.sh` to `scripts/ci/rcm_ci.sh`, fill `STEPS` and the `run_<step>` functions with
   what the current CI does, keep `isolate` and `venv` first: their failures exit 75, which the
   hook reports as `unavailable · infra` instead of a code failure.
3. Move the heavy jobs of the current workflow into `checks.yml`, one job per leg (`if:
   contains(format(',{0},', inputs.legs), ',<leg>,')`), keep the `legs` and `ran` jobs, and
   replace the workflow with `templates/ci.yml`. Keep the required check's job name `test`.
4. Owner, on GitHub: a fine-grained token for this repository only — Commit statuses read and
   write; Contents, Pull requests and Variables read — saved as a `0600` file. Owner, on the build
   machine: an rcm client token `ci-watch` (not admin) from the service's own `rcm token add`,
   saved as a `0600` file.
5. On the build machine: copy the three scripts, write `ci-watch.toml` (point its `rcm` at a client
   venv of its own, not at the service venv a deploy changes; `--check` warns), add the preset and the
   `[[notify]]` rule from `presets.ci.toml` to the server config, reload, and load the launchd
   agent (fill `{{home}}`).
6. Run `python3 ~/.config/rcm/scripts/rcm_ci_watch.py --config ~/.config/rcm/ci-watch.toml
   --check`: every row must be OK. `--dry-run` prints what one round would do without writing.
7. Open a draft pull request with `mode = "rcm"` and confirm: the watcher posts `queued` within a
   minute, the hook posts `passed` when the job ends, `test` turns green, `now` and `late` are
   skipped. Then stop the watcher once and confirm GitHub takes over after `pickup_seconds`.

## The contract in one table

The status description's first word says the stage: `<word> · #<job> · <text>`, cut at 140
characters. `target_url` stays empty unless `job_url = true` (an rcm link does not open outside
the router and names an internal host).

| state | word | posted by | the waiting job |
|---|---|---|---|
| `pending` | `queued` · `running` | watcher | waits `queue_seconds` · `run_seconds` |
| `success` | `passed` | hook (watcher if the hook missed it) | pass |
| `failure` | `failed` | hook (watcher if the hook missed it) | fail — not taken over on the first attempt |
| `error` | `unavailable` | watcher · hook | GitHub runs the `late` legs now |
| `error` | `superseded` | hook | GitHub runs the `late` legs now |
| `error` | `taken-over` | the waiting job | a re-run goes to GitHub at once |

A submit that fails with `server_unreachable` or a timeout (the server restarting) is retried
for three rounds, with `pending` `queued · retrying submit (n/3)` meanwhile; the fourth failure
posts `unavailable`. Setup and token failures post `unavailable` at once. When a pull request is
closed or merged, the watcher cancels its job if it is still queued, was submitted by the watcher
alone (no joiners) and its commit is not a watched branch's head; a running job is left to finish.
A reopened pull request whose job was cancelled is submitted again.

A re-run of the workflow (`run_attempt > 1`) does not read rcm at all: it is the human's way out
when an rcm failure is wrong. To be judged by rcm again, push a new commit.

The repository variable `RCM_CI_MODE` (`github` · `rcm` · `both`) overrides the file for every
commit at once; the watcher reads it too. Set it to `github` when the build machine is broken.

## Without a GitHub fallback

`fallback = "none"` is for a repository whose required check is the `rcm/ci` status itself, with
no waiting workflow (remote_ci_monitor's own setup, `docs/m5r-workplan.md` §13). Then:

- Only the watcher is needed on the build machine; the job script may post its own `pending` and
  result instead of the `[[notify]]` hook. The watcher never posts `pending` over a result that is
  already there, nor `running` over a `pending` it did not post; it still covers a job that ended
  without a status (cancelled, superseded, timed out, lost, expired) after 120 seconds.
- A job held by `worker_down` or `paused` is left to wait — nobody would take it over, and a queue
  paused for a deploy must not turn pull requests into errors. The log says so once.
- The tokens can come from where they already are: `rcm_client_config` reads the rcm client's own
  `client.toml`, and `github_token_command` runs e.g. `gh auth token --user <login>` each round.
  Nothing is copied into a new file; `--check` warns (not fails) when that rcm token is admin.

## What the human still fills in

The checks themselves (`run_<step>` in `scripts/ci/rcm_ci.sh`, the leg jobs in `checks.yml`),
the two tokens, and the launchd agent. The skill never edits the live `server.toml` and never
creates tokens.
