---
name: rcm-store-connect
description: Connect a project to the rcm Store tab at the required tier — the release profile block for server.toml, the secrets list, the three presets (plan / upload / review) and three script skeletons that honour the artifact contract, each with a --selftest. Use it inside the project that ships to the stores, once per repository, before /rcm-gate-connect, /rcm-qa-connect or /rcm-release-driver; re-run it to verify (it never overwrites an owner-edited file without --force).
---

# rcm-store-connect

The Store tab draws files and runs presets; it does not know this project. This skill gives it the
four things `docs/release-contract.md` asks for (profile, presets, artifact files, markers) as
**skeletons with a working contract**: the scripts already parse rcm's inputs, print the markers,
write the JSON files with the required fields, exit with the documented codes, and refuse to write
a document that would lie. The store calls themselves are `# TODO(project):` blocks the owner fills.

Everything below runs from the project root. `SKILL_DIR` is the folder holding this file
(`<project>/.claude/skills/rcm-store-connect`); the templates are in `$SKILL_DIR/templates/`.

## When to use

- The project should appear in the Store tab and has no `[repos.<name>.release]` profile yet.
- The project already has release scripts, but no `plan.json` / `upload.json` / `review-plan.json`
  / `review.json` writers, no `::rcm::step::` markers, or no presets for the three roles.
- A re-run to check the state or resume: files the skill wrote earlier are rewritten, owner-edited
  files are reported, the selftests and `rcm check` run again, and a fresh section is appended.

Not for: the CI gate (`/rcm-gate-connect`), device QA (`/rcm-qa-connect`), the S0–S8 driver
(`/rcm-release-driver`), or dev distribution. Those are optional tiers; the tab opens without them.

## Inputs it asks for

If `docs/rcm-connect.md` already exists with the header `# rcm connect — <repo>` and its answer
lines (`/rcm-connect` writes them), take the answers from there and ask nothing. Otherwise ask
only what detection (Step 1) could not answer, and state the default with each question.

| # | Question | Default (from detection) |
|---|---|---|
| 1 | Repository name — the `name` of the `[[repos]]` entry in the rcm server's `server.toml` | the `repo = "…"` value of an existing rcm presets file in the project; else the project folder name |
| 2 | Platforms shipped: `ios,android`, `ios` or `android` | `ios,android` when `ios/` and `android/` (or `*.xcodeproj` and `build.gradle*`) both exist; else the one that exists |
| 3 | Name of the environment variable the jobs receive the secrets folder in (`^[A-Z][A-Z0-9_]*$`) | `<REPO>_SECRETS`, repo upper-cased, `-` → `_` |
| 4 | Path of the rcm server's `server.toml` to verify against, or `skip` | `~/.config/rcm/server.toml` when it exists; else `skip` (the two TOML files are handed to the server owner) |

`--force` in the request ("/rcm-store-connect --force") overwrites every file that differs;
`FORCE_FILES="path1 path2"` overwrites only those. Files the skill itself wrote earlier are
rewritten without either (see Step 3). Nothing else is asked: the build number, the tag and the
store credentials are never inputs of this skill.

## What it creates

| Path (relative to the project) | Why |
|---|---|
| `scripts/release/rcm_contract.py` | stdlib-only helper: `write <kind>` builds + validates + atomically writes each artifact; `validate <kind> --file` exits 1 on a missing / ill-typed field; `--selftest` proves poisoned documents are rejected |
| `scripts/release/release_plan.sh` | role `plan`: read-only store snapshot → `plan.json` (n = store max + 1, or null with blockers) |
| `scripts/release/release_upload.sh` | role `upload`: rehearsal by default; real upload only with the N a human typed |
| `scripts/release/release_review.sh` | role `review`: review plan by default; submit only with the typed N; Android only with the per-submission managed-publishing statement |
| `scripts/rcm/presets.release.toml` | the presets `release-plan`, `release-upload`, `release-review` with exactly the inputs rcm sends, `source_modes = ["git_ref"]`, `repo`, `artifacts`, `artifacts_on = "always"` |
| `scripts/rcm/profile.release.toml` | the `[repos.<repo>.release]` block: roles → presets, `secrets_dir_env`, the secrets list (names and kinds only) |
| `scripts/rcm/rcm_candidate.py` | stdlib-only merger: writes a candidate `server.toml` (profile block after the right `[[repos]]` entry, presets appended, same-name presets replaced) and, with `--check`, runs `rcm check` on it; reads its defaults from the `docs/rcm-connect.md` header; `--selftest` |
| `docs/rcm-connect.md` (created or section appended) | the answers in its header and, per skill, what was created / still to fill in / verified — `/rcm-connect` and the other skills read it |
| `.gitignore` (two lines, if missing) | `build/.rcm-release/` and `__pycache__/` — artifacts, store snapshots and bytecode never enter git |

Every file the skill writes carries `# generated by rcm-store-connect` in its first five lines;
that marker is how a re-run tells its own output from an owner's file.

Two project-side files are shared by every `rcm-*-connect` skill and exist once:
`scripts/rcm/presets.release.toml` holds **all** rcm presets of the project in one file (this skill
adds the three store roles; gate / qa / driver skills append theirs), and
`scripts/rcm/profile.release.toml` holds the **whole** `[repos.<repo>.release]` block, including the
optional roles other skills add later. If the project already keeps rcm presets in another TOML file
(Step 1 finds it), that file plays the presets role — its path goes into the header's
`presets_file:` line and the three presets are appended to it — and `scripts/release` becomes
whatever folder already holds the release scripts.

`docs/rcm-connect.md` starts with exactly this header, which the other skills read instead of
re-asking (kept as-is when the file already has it; `tiers:` lists the optional tiers connected so
far, `none` at first):

```markdown
# rcm connect — <repo>

repo: <name>
platforms: <ios,android|ios|android>
server_toml: <path or skip>
secrets_env: <VAR>
presets_file: <scripts/rcm/presets.release.toml or the existing presets file>
tiers: <comma list or none>
```

## What the human still fills in

Explicit, never silently stubbed — the untouched skeletons run and produce an honest **blocked**
result (`B-TODO`, `not_implemented`), never a fake number or a fake `ready`:

1. `release_plan.sh` → `store_snapshot()`: print one JSON object `{max_build, first_release, store,
   blockers, warnings}` read from App Store Connect / Google Play with the credentials in
   `$<secrets_env>`. Read-only.
2. `release_upload.sh` → `store_max_build()` (the same number, read-only) and `store_upload PLATFORM
   N BUILD_NAME TRACK` (build + upload one platform; must not release, promote or roll out).
3. `release_review.sh` → `store_review_observe BUILD_NAME` (`{n, ios{verdict,state},
   android{verdict,state}, auto_release}`), `listing_lines preview|diff` (optional), and
   `store_submit PLATFORM N BUILD_NAME LISTING PHASED` (submit for review; manual release always).
4. `profile.release.toml` → the secrets list: keep the names the scripts really read; delete the
   group of a platform the project does not ship; add `GH_TOKEN` / `review_information` only if used.
5. `server.toml` on the rcm server → put the profile block directly under the project's
   `[[repos]]` entry and the presets among `[[presets]]` (`rcm_candidate.py --out` produces the
   exact file); restart the server; open the Store tab Settings and enter the secrets there (they
   never go into git or the profile).
6. The `[[repos]]` entry itself, if the server does not have one yet (`name`, `url`).

Where an existing lane or script already does a store read or upload (Step 1 lists them), the
TODO body is usually one call to it.

## Steps

Each step names the file it touches and the check that proves it. Stop and report at the first
failed check; do not paper over it. Run the snippets in **bash** (zsh aborts on unmatched globs).

1. **Detect what exists** (read-only). Run and keep the output for the report:
   ```sh
   X="--exclude-dir=.claude --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=build"
   ls -d ios android *.xcodeproj 2>/dev/null; ls build.gradle build.gradle.kts android/build.gradle* 2>/dev/null
   grep -rl $X --include='*.toml' '^\[\[presets\]\]' .                          # existing rcm presets file → reuse its repo = and its path
   ls scripts/release/*.sh scripts/*.sh 2>/dev/null | grep -Ev '/[^/]*ci[^/]*\.sh$'   # existing release scripts (the CI gate script is not one)
   grep -n 'lane :' fastlane/Fastfile 2>/dev/null | grep -Ei 'upload|beta|deliver|supply|submit|release|build'   # lanes that already talk to the store
   grep -rn $X '::rcm::step::' --include='*.sh' --include='*.py' . | head        # scripts that already speak rcm markers
   grep -rlE $X 'plan\.json|upload\.json|review(-plan)?\.json' --include='*.sh' --include='*.py' .   # existing artifact writers
   grep -rhoE $X 'ASC_[A-Z_]+|APP_STORE_CONNECT_[A-Z_]+|PLAY_[A-Z_]+|[A-Z_]*KEYSTORE[A-Z_]*|[A-Z_]*SERVICE_ACCOUNT[A-Z_]*|[A-Z_]*KEY_ALIAS[A-Z_]*' fastlane scripts android 2>/dev/null | sort -u   # credential names already in use
   grep -rl $X '^# generated by rcm-store-connect' scripts docs 2>/dev/null       # files this skill wrote before (resume)
   grep -A30 '^## rcm-store-connect' docs/rcm-connect.md 2>/dev/null | grep '^Created:'   # what a previous run says it created
   git check-ignore -q build/.rcm-release/plan.json && echo ignored || echo not-ignored
   ```
   Decide: `scripts_dir` (prefer the folder holding a skill-owned file, else the folder with the
   most release-script hits, else `scripts/release`),
   `presets_file` (an existing rcm presets file or `scripts/rcm/presets.release.toml`), the
   platform default, and `OWNED` = the files the last two greps name (skill-owned: rewritten in
   Step 3 even without `--force`). Check: a findings table is printed before anything is written;
   every existing script that already writes an artifact or lane that touches a store is named in
   it; nothing under `.claude/` appears in it.
2. **Ask** the questions of the table above (or read the header). Check: the answers are echoed
   back as `REPO`, `PLATFORMS`, `SECRETS_ENV`, `SERVER_TOML`; `SECRETS_ENV` matches
   `^[A-Z][A-Z0-9_]*$`; `REPO` has no spaces; `PLATFORMS` is one of `ios,android` / `ios` /
   `android`.
3. **Install the scripts** into `$scripts_dir` from `$SKILL_DIR/templates/`, filling placeholders.
   **Adopt before installing**: a destination that exists, is not skill-owned, and already meets
   the role's contract — its `--selftest` exits 0 with a `selftest (PASS|ok)` last line, *or* a
   read-only run writes the contract file and `rcm_candidate.py`/`validate` accepts it — is the
   project's real implementation. Record it as `kept — real implementation` in the report and
   skip the template for that role; it is never `differs`, never a stop, never replaced. When
   every role is adopted, `rcm_contract.py` is not installed either (nothing would call it).
   Run every python snippet in this skill with `python3 -B` so no `__pycache__` is left behind.
   ```sh
   case "$PLATFORMS" in ios,android) PLATFORM_DEFAULT=both;; ios|android) PLATFORM_DEFAULT=$PLATFORMS;; esac
   fill() { sed -e "s|{{repo}}|$REPO|g" -e "s|{{secrets_env}}|$SECRETS_ENV|g" \
                -e "s|{{scripts_dir}}|$scripts_dir|g" -e "s|{{platform_default}}|$PLATFORM_DEFAULT|g" "$1"; }
   owned() {  # skill-owned: carries the marker, or was listed under Created: of an earlier section, or named in OWNED
     head -5 "$1" 2>/dev/null | grep -q '^# generated by rcm-store-connect' && return 0
     case " ${OWNED:-} " in *" $1 "*) return 0;; esac; return 1
   }
   install_file() {  # $1 template · $2 destination — kept / written / rewritten / differs
     if [ -e "$2" ] && fill "$1" | cmp -s - "$2"; then echo "kept      $2"; return; fi
     if [ -e "$2" ]; then
       case " ${FORCE_FILES:-} " in *" $2 "*) FORCE=1;; esac
       if ! owned "$2" && [ "${FORCE:-0}" != 1 ]; then echo "differs   $2 — owner-edited; re-run with --force or FORCE_FILES=\"$2\" (a .bak is kept)"; return; fi
       cp "$2" "$2.bak"; fill "$1" > "$2"; echo "rewritten $2 (was skill-owned or forced; .bak kept)"; return
     fi
     mkdir -p "$(dirname "$2")"; fill "$1" > "$2"; echo "written   $2"
   }
   for f in rcm_contract.py release_plan.sh release_upload.sh release_review.sh; do install_file "$SKILL_DIR/templates/$f" "$scripts_dir/$f"; done
   install_file "$SKILL_DIR/templates/rcm_candidate.py" scripts/rcm/rcm_candidate.py
   chmod +x "$scripts_dir"/release_*.sh "$scripts_dir/rcm_contract.py" scripts/rcm/rcm_candidate.py
   ```
   Placeholders: `{{repo}}` = answer 1 · `{{secrets_env}}` = answer 3 · `{{scripts_dir}}` = the
   folder chosen in Step 1 · `{{platform_default}}` = answer 2 mapped as above (`ios,android` →
   `both`, the preset's `platform` choice). Check: `grep -l '{{' "$scripts_dir"/release_*.sh
   "$scripts_dir"/*.py scripts/rcm/rcm_candidate.py` prints nothing; `bash -n` passes on the three
   scripts; `python3 -m py_compile` passes on `rcm_contract.py` and `scripts/rcm/rcm_candidate.py`;
   no line says `differs` (or the owner has decided about each one).
4. **Run the selftests** — they need no store, no secrets and no network. A stub that merely exits
   0 must not pass, so test the exit code **and** the PASS line:
   ```sh
   fails=0
   for cmd in "python3 $scripts_dir/rcm_contract.py" "python3 scripts/rcm/rcm_candidate.py" \
              "bash $scripts_dir/release_plan.sh" "bash $scripts_dir/release_upload.sh" "bash $scripts_dir/release_review.sh"; do
     out="$($cmd --selftest 2>&1)"; rc=$?
     if [ "$rc" = 0 ] && printf '%s\n' "$out" | tail -1 | grep -Eq 'selftest (PASS|ok)'; then echo "PASS  $cmd"; else echo "FAIL  $cmd (rc=$rc)"; printf '%s\n' "$out" | tail -5; fails=$((fails+1)); fi
   done; echo "selftests failed: $fails"
   ```
   Check: `selftests failed: 0` and five `PASS` lines. They prove: a poisoned store answer ends red
   with `n = null` (never a fake number), the default modes call no store, `mode=upload` /
   `mode=submit` without a typed N exit 2 before any store call, a mismatch exits 3, drift 4, an
   unconfirmed Android submission is skipped and the run is partial (6), an observed
   `auto_release` blocks everything, and the merger shows `FAIL server config` rows.
5. **Write the presets** from `$SKILL_DIR/templates/presets.release.toml` through `fill` into
   `$presets_file`:
   - new file → `install_file`;
   - existing presets file → for each of the three `[[presets]]` groups: append it when no preset
     of that name exists; when one exists and **passes the invariants below** (the inputs rcm
     sends are declared, the irreversible `mode` is not the default, no release-after-approval
     input) it is the project's real preset — record `kept — real implementation` and continue;
     when one exists, differs and fails an invariant, print `differs   <name> in $presets_file`
     with the old block and **stop** unless `--force` / `FORCE_FILES` names the file — then back
     the file up to `.bak` and replace the group with the merger's semantics:
     ```sh
     fill "$SKILL_DIR/templates/presets.release.toml" > /tmp/rcm-store-presets.toml
     cp "$presets_file" "$presets_file.bak"
     python3 -c 'import sys; sys.path.insert(0, "scripts/rcm"); import rcm_candidate as c
     old = open(sys.argv[1]).read(); new = open(sys.argv[2]).read()
     open(sys.argv[1], "w").write(c.merge(old, "-", None, new, None))' "$presets_file" /tmp/rcm-store-presets.toml
     ```
     Leaving a required-tier preset stale is never an option: the run ends red until it is
     resolved.
   Check:
   ```sh
   python3 - "$presets_file" <<'PY'
   import sys, tomllib
   d = tomllib.load(open(sys.argv[1], "rb"))
   names = [p["name"] for p in d["presets"]]
   assert len(names) == len(set(names)), f"duplicate preset names: {names}"
   for want in ("release-plan", "release-upload", "release-review"):
       assert want in names, f"{want} missing"
   for p in d["presets"]:
       for i in p.get("inputs", []):
           if i.get("type") == "choice" and "default" in i:
               assert i["default"] in i["choices"], f"{p['name']}.{i['name']}: default {i['default']!r} not in {i['choices']}"
           if p["name"] == "release-upload" and i["name"] == "mode": assert i["default"] == "rehearsal", i
           if p["name"] == "release-review" and i["name"] == "mode": assert i["default"] == "plan", i
   print("presets ok:", names)
   PY
   grep -nEi 'automatic_release|rollout|auto_release|promote' "$presets_file" && echo "FAIL: release-after-approval input" || echo ok
   ```
   The first prints `presets ok` (every `platform` default is inside its choices, irreversible
   modes are not defaults); the second prints `ok`.
6. **Write the profile** from `$SKILL_DIR/templates/profile.toml` through `fill` into
   `scripts/rcm/profile.release.toml` (`install_file`). Then edit the secrets list: delete the iOS
   group when `PLATFORMS = android`, the Android group when `PLATFORMS = ios`; rename entries to
   the credential names Step 1 found the project already uses (one entry per file the scripts will
   read from the secrets folder). Check:
   ```sh
   python3 - "$REPO" scripts/rcm/profile.release.toml <<'PY'
   import sys, tomllib
   repo, path = sys.argv[1:3]
   d = tomllib.load(open(path, "rb"))["repos"][repo]["release"]
   names = [s["name"] for s in d.get("secrets", [])]
   assert len(names) == len(set(names)), f"duplicate secret names: {names}"
   for role, want in (("plan", "release-plan"), ("upload", "release-upload"), ("review", "release-review")):
       assert d["presets"].get(role) == want, (role, d["presets"])
   assert names and d.get("secrets_dir_env"), "secrets listed but secrets_dir_env empty (or no secrets at all)"
   print("profile ok:", len(names), "secrets, secrets_dir_env =", d["secrets_dir_env"])
   PY
   ```
7. **Ignore the artifacts folder** (`.gitignore`), guarded so a re-run adds nothing twice. The
   folder is the one the `plan` preset's first `artifacts` glob points at (`build/.rcm-release/`
   for the skeleton; a project's real preset may say otherwise), and nothing is added when git
   already ignores it:
   ```sh
   art_dir="$(python3 -B - "$presets_file" <<'PY'
   import sys, tomllib
   for p in tomllib.load(open(sys.argv[1], "rb")).get("presets", []):
       if p.get("name") == "release-plan" and p.get("artifacts"):
           print(p["artifacts"][0].split("*")[0].rsplit("/", 1)[0] + "/"); break
   PY
   )"; art_dir="${art_dir:-build/.rcm-release/}"
   for line in "$art_dir" '__pycache__/'; do git check-ignore -q "${line}x" 2>/dev/null || grep -qxF "$line" .gitignore 2>/dev/null || echo "$line" >> .gitignore; done
   git check-ignore -q "${art_dir}plan.json" && echo ignored
   ```
   Check: prints `ignored`; the folder line appears at most once in `.gitignore`.
8. **`rcm check`** on a candidate copy of the server config, through the installed merger (the
   live `server.toml` is only read; the candidate lands in a temp dir unless `--out` is given).
   Use the **same `rcm` the server runs** — `RCM=<path>` when it is not the first on PATH; the
   merger prints the binary and version it calls:
   ```sh
   python3 scripts/rcm/rcm_candidate.py --server "$SERVER_TOML" --repo "$REPO" \
       --presets "$presets_file" --profile scripts/rcm/profile.release.toml --check
   ```
   It inserts the profile block right after the `[[repos]]` entry named `$REPO` (creating the entry
   with `url` from `git remote get-url origin` when the server file has none), appends the presets
   (replacing any same-name `[[presets]]`), prints the candidate path, then runs
   `rcm check --config <candidate> --server http://127.0.0.1:9 --token none` and prints the rows.
   Rows that fail only because of the dummy server (`server`, `client`, `token`, a `presets` row
   that cannot reach it) are hidden; `server config` is always shown. Exit is 1 when any shown row
   is `FAIL` **or no `release <repo>` row appears**. Check: exit 0 and the `release <repo>` row is
   `ok` or `warn` with every warning one of `secrets dir … does not exist yet` / `<role> not
   configured` — the secrets-dir warning always fires on a candidate, because the folder lives next
   to the real config. A `FAIL` names the rule broken (duplicate preset, default outside choices,
   missing input, irreversible default) — fix the file it names and repeat. When
   `SERVER_TOML = skip`, record "not verified — server.toml not reachable from here" instead and
   hand `scripts/rcm/profile.release.toml` + `$presets_file` to the server owner with the
   instructions from «What the human still fills in» 5 (they run the same command on the server
   with `--out` to produce the file to move into place).
9. **Read-only plan run**, twice. Skip the run (and say so in the report) when the script's own
   preflight needs a network identity before it reads the secrets folder — a `gh api` or store
   call at the top; its `--selftest` is the evidence then. Otherwise:
   - locally, on the untouched skeleton, to prove the contract end to end without a store:
     ```sh
     d="$(mktemp -d)"; env "$SECRETS_ENV=$d" RCM_INPUT_BUILD_NAME=0.0.1 RELEASE_WORK="$d/work" bash "$scripts_dir/release_plan.sh"; echo "exit $?"
     python3 "$scripts_dir/rcm_contract.py" validate plan --file "$d/work/plan.json"
     ```
     Check: markers `::rcm::steps::3`, three `::rcm::step::` lines and a final `::rcm::summary::`
     appear; exit is 1; `validate` prints `plan: ok`; the file has `n: null` and a `B-TODO` blocker.
   - through rcm, once the profile is on the server and `store_snapshot()` is filled (this may be
     later — say so): `rcm run release-plan --ref <default_branch> -f build_name=<X.Y.Z> --fetch-artifacts`
     and then `validate plan --file build/.rcm-release/plan.json`. Check: exit 0 with an integer
     `n`, or exit 1 with blockers that name a real store condition. Never run `release-upload`
     with `mode=upload` or `release-review` with `mode=submit` from this skill.
10. **Record** in `docs/rcm-connect.md`. If the file is missing, create it with the header shown
    under «What it creates» — all six lines, `tiers: none` — filled from Step 2 and Step 1
    (`presets_file`). If it exists with that header, keep every line but fix the presets line:
    replace `presets_file: pending` with the real `$presets_file`, and when an older header lacks
    `presets_file:` / `tiers:` insert `presets_file: <path>` and `tiers: none` right after the
    `secrets_env:` line:
    ```sh
    f=docs/rcm-connect.md
    grep -q '^presets_file: ' "$f" || sed -i.bak "/^secrets_env: /a\\
    presets_file: $presets_file" "$f"
    grep -q '^tiers: ' "$f" || sed -i.bak "/^presets_file: /a\\
    tiers: none" "$f"
    sed -i.bak "s|^presets_file: pending$|presets_file: $presets_file|" "$f"; rm -f "$f.bak"
    ```
    Then append:
    ```markdown
    ## rcm-store-connect — <YYYY-MM-DD>
    Created: <one line per file: written / rewritten / kept / differs>
    You must fill in: <the numbered items of «What the human still fills in» that still apply>
    Verified: <selftests PASS ×5> · <rcm check release <repo>: ok|warn (<warnings>)|not verified> · <local plan run: exit 1, B-TODO> · <rcm plan run: n=<N> | pending>
    ```
    Check: `head -1 docs/rcm-connect.md` is `# rcm connect — <repo>`; the six `repo:` /
    `platforms:` / `server_toml:` / `secrets_env:` / `presets_file:` / `tiers:` lines are present;
    `presets_file:` is not `pending`; the section is the last one in the file and each of its
    three lists is non-empty.
    Do not commit; tell the owner which files to commit (never the secrets folder or `build/`).

## Done means

- `rcm_candidate.py … --check` exits 0 and shows `release <repo>` as `ok` or `warn` with only the
  "secrets dir does not exist yet" / "not configured" warnings (or the report says exactly why it
  could not be run here).
- `rcm_contract.py --selftest`, `rcm_candidate.py --selftest` and the three script `--selftest`
  runs all exit 0 **and** print their PASS line in this project.
- The local read-only plan run left a `plan.json` that `validate plan` accepts, with `n: null`
  and a `B-TODO` blocker (or, after the owner filled the TODO, an integer `n` via `rcm run`).
- `docs/rcm-connect.md` has the `# rcm connect — <repo>` header with the six answer lines and
  the `## rcm-store-connect — <date>` section with all three lists.
- No owner-edited file was overwritten without `--force` / `FORCE_FILES`; every `differs` line was
  reported; every skill-owned file was rewritten with a `.bak`.

## Never

- **No release after approval.** The presets have no `automatic_release`, `rollout`, `promote`
  or `auto_release` input, and the scripts have no such call; the skill does not add one even when
  asked "to save a step". `review.json.auto_release` is always `false` from these scripts.
- **The irreversible mode is never the default**: `release-upload` `mode` defaults to
  `rehearsal`, `release-review` `mode` defaults to `plan`. `rcm check` fails otherwise; do not
  change the default to make a run "easier".
- **The build number is typed by a human**, again, in every irreversible submission
  (`confirm_build_number`). The skill never sets it, never reads it from a file into a flag, and
  never suggests a preset default for it. The scripts compare it with the store as it is now.
- **Secrets live only in the secrets folder** the server passes as `$<secrets_env>`. They never go
  into the profile, the presets, `.env` files, git, the log or this skill's report; the skill
  never asks for a value, only for names.
- **Nothing project-specific goes into rcm.** App names, script paths, key names and verdict words
  stay in this project's profile and scripts; the skill templates carry only placeholders.
- **The skill runs no irreversible path**: never `mode=upload`, never `mode=submit`, never
  `--force` on a file the owner has edited without saying so. Verification is `rcm check`,
  `--selftest` and read-only runs only.
