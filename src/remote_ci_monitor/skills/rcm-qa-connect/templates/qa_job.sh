#!/usr/bin/env bash
# scripts/rcm/qa.sh — body of the rcm `qa` preset (installed by the rcm-qa-connect skill).
#
# 무엇을 하나: 프리플라이트 → [빌드 → 단위 실행] × 플랫폼(차례대로, 앞이 빨가면 뒤는 not_run) → 집계
#   (report.json) → 정리 → `::rcm::summary::`. 판정은 여기서 하지 않는다 — 단위마다 결과 한 줄을
#   results.tsv 에 적고 qa_report.py 가 report.json 을 쓴다. 사람도 모델도 판정에 끼지 않는다.
#
# 「단위(unit)」 = 이 프로젝트의 QA 가 세는 하나(청크 · 스펙 파일 · 시나리오) × 플랫폼. 분모는 **돌기 전에**
#   list_units 가 낸 목록 × 플랫폼 수다. 목록을 못 내면 분모를 지어내지 않고 BLOCKED(2) 로 끝난다.
#
# 입력은 환경변수뿐 — rcm 이 프리셋 입력을 RCM_INPUT_<NAME> 으로 준다. argv 는 받지 않는다(--selftest 제외):
#   RCM_INPUT_ORDER   플랫폼 차례, 쉼표 구분 (기본 "ios,android" — 프로젝트에 맞게 바꾼다; 단일 플랫폼이면 "web")
#   QA_OUT_DIR        산출물 폴더 (기본 qa-reports/rcm/<sha7>) — 프리셋 artifacts 글롭과 맞춘다
#
# 산출물(프리셋 artifacts 글롭이 가져간다): $QA_OUT_DIR/report.json (계약 §2 — 없으면 페이지가 초록으로 못
#   본다) · declared_units.txt · results.tsv · <unit>-<plat>.log · build-<plat>.log · step-captures.html(선택)
#
# 마커(계약 §3): ::rcm::steps::N (런타임 계산: 1 preflight + 2×플랫폼 + collect + cleanup) · ::rcm::step::<이름>
#   · ::rcm::progress::<done>/<total>::<unit>/<plat>::run|ok|fail|skip|env|blocked — 단위마다 앞뒤로
#   · ::rcm::summary::<VERDICT> … 가 **마지막** ::rcm:: 줄.
#
# exit: 0 PASS · 1 FAIL(코드 결함 — 릴리스를 막는다) · 2 BLOCKED(환경 — 빌드 전 프리플라이트 실패, 증거 없이
#   죽은 단위, 덜 돈 채 초록) · 10 BLOCKED(기기). 2·10 은 페이지에 빨강이 아니라 황토 «environment» 로 뜬다 —
#   코드가 틀린 게 아니라 머신이 못 돌린 것이라 PR 은 열어 둔다.
#
# 훅 — `# TODO(project):` 넷을 채운다. 같은 이름의 QA_HOOK_* 환경변수(실행 파일 경로)가 있으면 그것이 대신
#   불린다: --selftest 가 가짜 러너를 그렇게 끼운다. 실제 기기는 이 파일의 selftest 가 절대 만지지 않는다.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SELF="$HERE/$(basename "${BASH_SOURCE[0]}")"
REPORT_PY="${QA_REPORT_PY:-$HERE/qa_report.py}"
cd "$ROOT"

# ═══════════════════════════ hooks — the four things only the project knows ═══════════════════════════

# list_units — 단위 id 를 한 줄에 하나씩 stdout 으로. **돌기 전에** 낼 수 있어야 한다(매니페스트 · glob 수 ·
#   러너의 --list). 0 줄이거나 실패하면 BLOCKED(2): 분모를 지어내지 않는다.
list_units() {
  if [ -n "${QA_HOOK_LIST_UNITS:-}" ]; then "$QA_HOOK_LIST_UNITS"; return; fi
  # TODO(project): print one unit id per line, deterministic order. Examples (pick one, delete the rest):
  #   ls integration_test/*_test.dart | xargs -n1 basename | sed 's/_test\.dart$//'
  #   find e2e -name '*.spec.ts' | sort | xargs -n1 basename | sed 's/\.spec\.ts$//'
  #   <your runner> --list
  echo "TODO(project): list_units is not implemented in $SELF" >&2
  return 2
}

# build_platform <plat> — 그 플랫폼용 앱/번들을 만든다. stdout+stderr 는 이미 build-<plat>.log 로 간다.
#   실패(≠0) = 코드 결함(FAIL) 이지 환경이 아니다 — 게이트 초록 뒤 릴리스 ref 에서 빌드가 깨진 것이다.
build_platform() {
  if [ -n "${QA_HOOK_BUILD:-}" ]; then "$QA_HOOK_BUILD" "$@"; return; fi
  # TODO(project): build for "$1" (e.g. a debug simulator/emulator build with QA defines). No-op is allowed
  #   when the runner builds on its own — then say so here so the log explains the silence.
  echo "build_platform($1): nothing to do (TODO(project))"
  return 0
}

# check_devices <plat> — 기기/시뮬/브라우저가 뜨나. 0 = OK · 10 = 기기 BLOCKED · 그 밖 = 환경(2).
check_devices() {
  if [ -n "${QA_HOOK_CHECK_DEVICES:-}" ]; then "$QA_HOOK_CHECK_DEVICES" "$@"; return; fi
  # TODO(project): boot / health-check the device for "$1"; exit 10 when it cannot be made healthy.
  return 0
}

# run_unit <unit> <plat> <out_dir> — 단위 하나를 돌린다. exit: 0 PASS · 1 FAIL · 2 증거 없이 죽음(환경) ·
#   10 기기 BLOCKED. 실패 화면을 남기면 그 경로를 stdout 마지막 줄에 `screenshot=<path>` 로 찍는다(선택).
run_unit() {
  if [ -n "${QA_HOOK_RUN_UNIT:-}" ]; then "$QA_HOOK_RUN_UNIT" "$@"; return; fi
  # TODO(project): run one unit on one platform, e.g.
  #   <your runner> run "$1" --platform "$2" --out "$3"
  echo "TODO(project): run_unit is not implemented in $SELF" >&2
  return 2
}

# collect_extra <out_dir> — 선택. step-captures.html(단위 × 스텝 캡처 표) 같은 정보성 산출물. 결과엔 안 섞는다.
collect_extra() {
  if [ -n "${QA_HOOK_COLLECT_EXTRA:-}" ]; then "$QA_HOOK_COLLECT_EXTRA" "$@"; return; fi
  # TODO(project): optional — render "$1/step-captures.html" from the per-unit screenshots.
  return 0
}

# cleanup_devices — 선택. 기기 내리기 · 캐시 정리. 실패해도 결과엔 안 섞는다.
cleanup_devices() {
  if [ -n "${QA_HOOK_CLEANUP:-}" ]; then "$QA_HOOK_CLEANUP" "$@"; return; fi
  # TODO(project): optional — shut down simulators/emulators so the next job cold-boots a clean one.
  return 0
}

# ═══════════════════════════ selftest — fake runner, no devices ═══════════════════════════
if [ "${1:-}" = "--selftest" ]; then
  fails=0
  t() { if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FAIL $1 — got '$2' want '$3'"; fails=$((fails + 1)); fi; }
  echo "[qa] selftest"
  python3 "$REPORT_PY" --selftest >/dev/null 2>&1; t "qa_report.py --selftest is green" "$?" 0
  T="$(mktemp -d)"
  # 가짜 목록 · 가짜 러너(FAKE_TABLE 의 "<unit> <plat> <rc>" 대로 끝난다) · 가짜 기기 검사(FAKE_DEV_BLOCK 플랫폼만 10)
  printf '%s\n' "alpha" "beta" "gamma" > "$T/units.txt"
  cat > "$T/list.sh" <<'FAKE'
#!/usr/bin/env bash
[ "${FAKE_EMPTY_LIST:-}" = 1 ] && exit 0
[ "${FAKE_BAD_ID:-}" = 1 ] && { echo "e2e/login.spec"; exit 0; }
cat "$FAKE_UNITS"
FAKE
  cat > "$T/run.sh" <<'FAKE'
#!/usr/bin/env bash
unit="$1"; plat="$2"; out="$3"
rc="$(awk -v u="$unit" -v p="$plat" '$1==u && $2==p {print $3}' "$FAKE_TABLE" | head -1)"; rc="${rc:-0}"
echo "fake run $unit/$plat rc=$rc"
[ "$rc" = 1 ] && { mkdir -p "$out"; : > "$out/$unit-$plat.png"; echo "screenshot=$out/$unit-$plat.png"; }
exit "$rc"
FAKE
  cat > "$T/dev.sh" <<'FAKE'
#!/usr/bin/env bash
[ "${FAKE_DEV_BLOCK:-}" = "$1" ] && exit 10; exit 0
FAKE
  chmod +x "$T"/*.sh
  export QA_HOOK_LIST_UNITS="$T/list.sh" QA_HOOK_RUN_UNIT="$T/run.sh" QA_HOOK_CHECK_DEVICES="$T/dev.sh"
  export FAKE_UNITS="$T/units.txt" FAKE_TABLE="$T/table"
  run_case() { # name order → L=stdout 로그, R=report.json, CASE_RC
    local name="$1" order="$2"
    mkdir -p "$T/$name"
    RCM_INPUT_ORDER="$order" QA_OUT_DIR="$T/$name/out" bash "$SELF" > "$T/$name/stdout.log" 2>&1; CASE_RC=$?
    L="$T/$name/stdout.log"; R="$T/$name/out/report.json"
  }
  jq_() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$R" "$1" 2>/dev/null; }

  # ① 전부 PASS · 두 플랫폼 → exit 0 · steps::N == step 줄 수 · 분자 ≤ 분모 · 마지막 진행 줄 6/6 · summary 마지막
  : > "$FAKE_TABLE"; unset FAKE_DEV_BLOCK FAKE_EMPTY_LIST
  run_case pass ios,android; t "① all pass exits 0" "$CASE_RC" 0
  t "① steps::N computed at run time (1+2×2+2=7)" "$(grep -c '^::rcm::steps::7$' "$L")" 1
  t "① step lines == declared steps" "$(grep -c '^::rcm::step::' "$L")" 7
  t "① markers keep their invariants (numerator ≤ denominator, summary last)" "$(python3 "$REPORT_PY" check-markers "$L" >/dev/null 2>&1 && echo ok || echo bad)" ok
  t "① last progress line is 6/6" "$(grep '^::rcm::progress::' "$L" | tail -1 | awk -F'::' '{print $4}')" "6/6"
  t "① one run + one ok line per unit×platform" "$(grep -c '^::rcm::progress::[0-9]*/6::[a-z]*/[a-z]*::\(run\|ok\)$' "$L")" 12
  t "① report.json validates" "$(python3 "$REPORT_PY" validate "$R" >/dev/null 2>&1 && echo ok || echo bad)" ok
  t "① verdict PASS, both platforms PASS" "$(jq_ 'd["verdict"]+" "+d["platforms"]["ios"]["verdict"]+" "+d["platforms"]["android"]["verdict"]')" "PASS PASS PASS"
  t "① summary names the verdict" "$(grep -c '^::rcm::summary::PASS ' "$L")" 1
  t "① declared list and steps::N come before the build step" "$([ "$(grep -n 'declared_units.txt' "$L" | head -1 | cut -d: -f1)" -lt "$(grep -n '^::rcm::steps::' "$L" | cut -d: -f1)" ] && [ "$(grep -n '^::rcm::steps::' "$L" | cut -d: -f1)" -lt "$(grep -n '^::rcm::step::build ios' "$L" | cut -d: -f1)" ] && echo yes || echo no)" yes

  # ② iOS 에 FAIL 하나 → exit 1 · android not_run (진행 줄 없음) · failures 에 스크린샷 · 분모는 그대로 6
  printf 'beta ios 1\n' > "$FAKE_TABLE"
  run_case iosfail ios,android; t "② one FAIL exits 1" "$CASE_RC" 1
  t "② android is not_run in report.json" "$(jq_ 'd["platforms"]["android"]')" not_run
  t "② android has no progress lines" "$(grep -c '::[a-z]*/android::' "$L")" 0
  t "② failures[] carries unit, platform, screenshot" "$(jq_ 'd["failures"][0]["chunk_id"]+" "+d["failures"][0]["platform"]+" "+str(d["failures"][0]["screenshot"]!=None)')" "beta ios True"
  t "② denominator does not shrink when a platform is skipped" "$(grep '^::rcm::progress::' "$L" | tail -1 | awk -F'::' '{print $4}')" "3/6"
  t "② markers still valid" "$(python3 "$REPORT_PY" check-markers "$L" >/dev/null 2>&1 && echo ok || echo bad)" ok
  t "② summary is FAIL" "$(grep -c '^::rcm::summary::FAIL ' "$L")" 1

  # ③ 기기 BLOCKED (check_devices → 10) → exit 10 · 단위 0 개 · android not_run
  : > "$FAKE_TABLE"; export FAKE_DEV_BLOCK=ios
  run_case devblocked ios,android; t "③ device blocked exits 10" "$CASE_RC" 10
  t "③ no unit ran" "$(grep -c '^::rcm::progress::' "$L")" 0
  t "③ verdict BLOCKED, android not_run" "$(jq_ 'd["verdict"]+" "+d["platforms"]["android"]')" "BLOCKED not_run"
  t "③ summary is BLOCKED" "$(grep -c '^::rcm::summary::BLOCKED' "$L")" 1
  unset FAKE_DEV_BLOCK

  # ④ 단위가 증거 없이 죽음(rc 2) → exit 2 · 나머지 단위는 계속 돈다 · state env
  printf 'alpha ios 2\n' > "$FAKE_TABLE"
  run_case env ios; t "④ rc 2 exits 2" "$CASE_RC" 2
  t "④ env state on that unit" "$(grep -c '^::rcm::progress::1/3::alpha/ios::env$' "$L")" 1
  t "④ later units still run" "$(grep -c '^::rcm::progress::3/3::gamma/ios::ok$' "$L")" 1

  # ⑤ 목록이 비면 분모를 짓지 않고 BLOCKED(2) — 빌드 전에, report.json 은 그래도 쓴다
  : > "$FAKE_TABLE"; export FAKE_EMPTY_LIST=1
  run_case nolist ios,android; t "⑤ empty unit list exits 2" "$CASE_RC" 2
  t "⑤ stops before the build step" "$(grep -c '^::rcm::step::build' "$L")" 0
  t "⑤ report.json still written, all not_run" "$(jq_ 'd["verdict"]+" "+d["platforms"]["ios"]+" "+d["platforms"]["android"]')" "BLOCKED not_run not_run"
  t "⑤ no progress line without a denominator" "$(grep -c '^::rcm::progress::' "$L")" 0
  unset FAKE_EMPTY_LIST
  # ⑤b 단위 id 에 '/' → 마커가 깨지기 전에 BLOCKED(2)
  export FAKE_BAD_ID=1
  run_case badid ios; t "⑤ unit id with '/' exits 2 before any progress line" "$CASE_RC $(grep -c '^::rcm::progress::' "$L")" "2 0"
  unset FAKE_BAD_ID

  # ⑤c 훅을 안 채운 본체 — run_unit 의 TODO 가 blockers 에 그대로 실린다(검증기 불평이 아니라 진짜 사유)
  mkdir -p "$T/untouched"
  RCM_INPUT_ORDER=ios QA_OUT_DIR="$T/untouched/out" QA_HOOK_RUN_UNIT= bash "$SELF" > "$T/untouched/stdout.log" 2>&1; CASE_RC=$?; R="$T/untouched/out/report.json"
  t "⑤ untouched run_unit exits 2" "$CASE_RC" 2
  t "⑤ blockers name the real cause" "$(jq_ 'any("run_unit is not implemented" in b for b in d["blockers"])')" True
  t "⑤ no validator complaint in blockers" "$(jq_ 'any("should name" in b for b in d["blockers"])')" False

  # ⑥ 입력 오류 → 2, 스텝 전
  run_case badorder "ios,ios"; t "⑥ duplicate platform in order exits 2" "$CASE_RC" 2
  t "⑥ rejected before any step" "$(grep -c '^::rcm::step::' "$L")" 0
  run_case badargs ios; RCM_INPUT_ORDER=ios bash "$SELF" --judge > "$T/args.log" 2>&1; t "⑥ argv is refused (2)" "$?" 2

  rm -rf "$T"
  if [ "$fails" = 0 ]; then echo "[qa] selftest PASS"; exit 0; else echo "[qa] selftest FAIL($fails)"; exit 1; fi
fi

# ═══════════════════════════ inputs — env only ═══════════════════════════
[ $# -eq 0 ] || { echo "qa.sh takes no arguments — inputs arrive as RCM_INPUT_* (only --selftest is an argument)" >&2; exit 2; }
ORDER="${RCM_INPUT_ORDER:-ios,android}"      # TODO(project): default platform order for this project
PLATS=""; seen=""
IFS=',' read -r -a _parts <<< "$ORDER"
for p in "${_parts[@]}"; do
  case "$p" in *[!a-z0-9_]*|"") echo "RCM_INPUT_ORDER: bad platform name '$p'" >&2; exit 2;; esac
  case " $seen " in *" $p "*) echo "RCM_INPUT_ORDER: platform '$p' repeats" >&2; exit 2;; esac
  seen="$seen $p"; PLATS="$PLATS $p"
done
PLATS="${PLATS# }"; NPLAT="$(printf '%s\n' "$PLATS" | wc -w | tr -d ' ')"
[ "$NPLAT" -ge 1 ] || { echo "RCM_INPUT_ORDER is empty" >&2; exit 2; }

SHA7="$(git rev-parse --short=7 HEAD 2>/dev/null || echo nogit)"
OUT_DIR="${QA_OUT_DIR:-$ROOT/qa-reports/rcm/$SHA7}"
mkdir -p "$OUT_DIR"
RESULTS="$OUT_DIR/results.tsv"; : > "$RESULTS"
DECLARED="$OUT_DIR/declared_units.txt"
[ -f "$REPORT_PY" ] || { echo "qa_report.py missing next to $SELF" >&2; exit 2; }

step() { CUR_STEP="$1"; echo; echo "━━━ [qa] $1"; echo "::rcm::step::$1"; }
say() { printf '[qa] %s\n' "$*"; }
progress() { echo "::rcm::progress::$1/$2::$3::$4"; }
# 어떻게 끝나든 report.json 은 남는다 — collect 를 못 만난 채 죽으면 EXIT 트랩이 BLOCKED 로 쓴다.
finish_blocked() { # <exit 2|10> <reason>
  local code="$1"; shift
  say "BLOCKED — $*"
  python3 "$REPORT_PY" collect --order "$ORDER" --out "$OUT_DIR/report.json" --blocked "$*" --exit-code "$code" >/dev/null
  echo "::rcm::summary::BLOCKED $([ "$code" = 10 ] && echo '(device)' || echo '(environment)'): $*"
  exit "$code"
}
DONE_FLAG=0
trap 'rc=$?; [ "$DONE_FLAG" = 1 ] || [ -f "$OUT_DIR/report.json" ] || python3 "$REPORT_PY" collect --order "$ORDER" --out "$OUT_DIR/report.json" --blocked "qa.sh ended before collect (last exit $rc, step: ${CUR_STEP:-none})" >/dev/null' EXIT

# ═══════════════════════════ 1. preflight — everything that can fail before the build ═══════════════════════════
step "preflight"
say "sha=$SHA7 order=$ORDER out=$OUT_DIR"
# TODO(project): tool / secret / token checks go here — each one `|| finish_blocked 2 "<what is missing>"`.
#   Read secrets from the folder rcm hands you in $<secrets_dir_env> (profile); never echo their values.
if ! list_units > "$DECLARED" 2> "$OUT_DIR/list_units.err"; then
  tail -5 "$OUT_DIR/list_units.err" >&2
  finish_blocked 2 "list_units failed — the denominator is unknown: $(tail -1 "$OUT_DIR/list_units.err" 2>/dev/null)"
fi
sed -i.bak '/^[[:space:]]*$/d' "$DECLARED" 2>/dev/null; rm -f "$DECLARED.bak"
NUNITS="$(grep -c . "$DECLARED" || true)"
[ "$NUNITS" -ge 1 ] || finish_blocked 2 "list_units returned nothing — refusing to invent a denominator"
# 단위 id 에 '/' 나 '::' 가 있으면 진행 마커(<unit>/<plat>, :: 구분)가 깨져 격자 칸이 갈린다 — 슬러그로 바꿔 내라.
bad_id="$(grep -E '/|::' "$DECLARED" | head -1 || true)"
[ -z "$bad_id" ] || finish_blocked 2 "unit id '$bad_id' contains '/' or '::' — list_units must print slugs"
TOTAL=$((NUNITS * NPLAT))
say "declared units: $NUNITS × platforms: $NPLAT = $TOTAL (written to declared_units.txt before the build)"
echo "::rcm::steps::$((1 + 2 * NPLAT + 2))"   # preflight + (build, run) per platform + collect + cleanup — computed, not typed

# ═══════════════════════════ 2. per platform: build → run every unit ═══════════════════════════
DONE=0; stopped=""; worst=0
rank() { case "$1" in 10) echo 4;; 1) echo 3;; 2) echo 2;; 0) echo 0;; *) echo 3;; esac; }
for plat in $PLATS; do
  if [ -n "$stopped" ]; then
    say "$plat — not run: $stopped was red"      # report.json will show not_run; no progress lines, so the grid stays grey
    continue
  fi
  step "build $plat"
  if ! build_platform "$plat" > "$OUT_DIR/build-$plat.log" 2>&1; then
    tail -20 "$OUT_DIR/build-$plat.log"
    printf 'unit\tbuild:%s\t%s\t1\t%s\t-\n' "$plat" "$plat" "$OUT_DIR/build-$plat.log" >> "$RESULTS"   # a broken build is a code defect (FAIL), not env
    stopped="$plat"; worst=3
    continue
  fi
  check_devices "$plat" > "$OUT_DIR/devices-$plat.log" 2>&1; drc=$?
  if [ "$drc" != 0 ]; then
    tail -5 "$OUT_DIR/devices-$plat.log"
    code=2; [ "$drc" = 10 ] && code=10
    printf 'blocked\t%s\t%s\t%s\n' "$plat" "$code" "check_devices failed rc=$drc: $(tail -1 "$OUT_DIR/devices-$plat.log" 2>/dev/null)" >> "$RESULTS"
    stopped="$plat"; worst=$([ "$code" = 10 ] && echo 4 || echo 2)
    continue
  fi
  step "run $plat"
  plat_worst=0
  while IFS= read -r unit; do
    [ -n "$unit" ] || continue
    progress "$DONE" "$TOTAL" "$unit/$plat" run
    log="$OUT_DIR/$unit-$plat.log"
    run_unit "$unit" "$plat" "$OUT_DIR/runs/$unit-$plat" > "$log" 2>&1 </dev/null; rc=$?
    shot="$(sed -nE 's/^screenshot=(.*)$/\1/p' "$log" | tail -1)"
    case "$rc" in 0) st=ok;; 1) st=fail;; 2) st=env;; 10) st=blocked;; *) st=fail;; esac
    DONE=$((DONE + 1))
    progress "$DONE" "$TOTAL" "$unit/$plat" "$st"
    printf 'unit\t%s\t%s\t%s\t%s\t%s\n' "$unit" "$plat" "$rc" "$log" "${shot:--}" >> "$RESULTS"
    [ "$(rank "$rc")" -gt "$(rank "$plat_worst")" ] && plat_worst="$rc"
  done < "$DECLARED"
  [ "$(rank "$plat_worst")" -gt "$worst" ] && worst="$(rank "$plat_worst")"
  case "$plat_worst" in 0) ;; *) stopped="$plat";; esac      # a red platform stops the later ones (they become not_run)
done

# ═══════════════════════════ 3. collect — report.json is written here, once ═══════════════════════════
step "collect"
COLLECT="$(python3 "$REPORT_PY" collect --results "$RESULTS" --declared "$DECLARED" --order "$ORDER" --out "$OUT_DIR/report.json")" \
  || finish_blocked 2 "qa_report.py collect failed"
read -r VERDICT EXIT_CODE PLAT_WORDS <<< "$COLLECT"
python3 "$REPORT_PY" validate "$OUT_DIR/report.json" || finish_blocked 2 "report.json violates the contract (see above)"
collect_extra "$OUT_DIR" > "$OUT_DIR/collect_extra.log" 2>&1 && say "collect_extra OK" || say "collect_extra failed (informational — not part of the verdict)"

# ═══════════════════════════ 4. cleanup — after collect, never part of the verdict ═══════════════════════════
step "cleanup"
cleanup_devices > "$OUT_DIR/cleanup.log" 2>&1 && say "cleanup OK" || say "cleanup failed (informational)"

DONE_FLAG=1
say "verdict=$VERDICT exit=$EXIT_CODE units=$DONE/$TOTAL $PLAT_WORDS"
echo "::rcm::summary::$VERDICT $PLAT_WORDS units $DONE/$TOTAL"
exit "$EXIT_CODE"
