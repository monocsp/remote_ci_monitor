#!/usr/bin/env bash
# release_driver.sh — 한 회차의 «정상 릴리스» 를 끝까지 모는 드라이버(rcm Store 탭이 부르고, 노트북 세션도 부른다).
#
#   scripts/release/release_driver.sh --build-name X.Y.Z [--confirm-build-number N] [--dry-run]
#                                     [--skip-qa '<reason>'] [--retry] [--abort] [--status] [--selftest]
#
# 단계: S0 기본 브랜치에서 release/X.Y.Z 자르고 push → S1 plan 프리셋(plan.json) → S2 사람이 N 을 친다 →
#   S3 draft PR → S4 gate ∥ S5 qa(같이 제출, 차례로 기다림 · gate 빨강이면 qa 취소) → S6 기본 브랜치로 ff 머지 →
#   S7 upload 프리셋(mode=upload confirm_build_number=N) → S8 태그 확인 + 백머지 PR.
# 실패 경로: gate/qa 빨강 → PR close + 라벨 release-blocked + 보고 코멘트, 브랜치는 남긴다, exit 1.
# 재진입: 상태파일은 캐시일 뿐이다 — 매 호출 PR(head==sha) · 커밋 status · rcm jobs(같은 preset+sha) · 정확한 태그에서
#   단계를 다시 읽고 그 자리에서 잇는다. 업로드 잡은 어느 상태로든 있으면 **재제출하지 않는다**(lost 도 — 2차 업로드가 된다).
# 종료코드: 0 완료 · 1 빨강/계약 위반/N 불일치/이미 릴리스됨 · 2 전제 조건/N 없음 · 3 결과 불명 · 4 스토어 드리프트.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"   # cd 뒤에도 자기 자신을 절대 경로로
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

# ── 프로젝트 상수 ─────────────────────────────────────────────────────────────
# 프로파일 값(default_branch · tag · presets.*)은 실행 때 조각 파일에서 읽는다(load_profile) — 여기 두 벌로
# 적지 않는다. RELEASE_* 환경변수가 있으면 그것이 이긴다(selftest · 임시 실험용).
GH_REPO="${RELEASE_GH_REPO:-}"                      # TODO(project): "org/app" (gh 가 부르는 슬러그)
DEV_BRANCH="${RELEASE_DEV_BRANCH:-dev}"             # 없으면 빈 문자열 — S0 의 «default ⊂ dev» 검사를 건너뛴다
TAG_OWNER="${RELEASE_TAG_OWNER:-job}"               # TODO(project): job = 업로드 잡이 태그하고 S8 은 확인만 · driver = 없으면 S8 이 push
PROFILE_FILE="${RELEASE_PROFILE_FILE:-scripts/rcm/profile.release.toml}"   # rcm-store-connect 가 만든 조각
PRESETS_FILE="${RELEASE_PRESETS_FILE:-scripts/rcm/presets.release.toml}"   # 같은 곳의 프리셋 정본
REPO_NAME="${RELEASE_REPO_NAME:-}"                  # 비면 조각에 release 표가 하나뿐일 때 그것
DEFAULT_BRANCH=""; TAG_PATTERN=""; PRESET_PLAN=""; PRESET_UPLOAD=""; PRESET_REVIEW=""
PRESET_GATE=""; PRESET_QA=""                        # 비면 그 역할은 «미설정» — S4/S5 를 건너뛴다
CTX_GATE="ci/gate"; CTX_QA="ci/qa"                  # 커밋 status context — 드라이버가 스스로 올린다(post_status)
LABEL_BLOCKED="release-blocked"
CHECK="${RELEASE_CHECK:-python3 ${SCRIPT_DIR}/release_check.py}"
RCM="${RELEASE_RCM:-rcm}"

BUILD_NAME=""; CONFIRM_N=""; N_SOURCE=""             # N_SOURCE 는 s2_confirm 만 채운다: flag | tty
DRY_RUN=false; STATUS_ONLY=false; ABORT=false; RETRY=false; SELFTEST=false; SKIP_QA=""

usage() { sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --build-name)           BUILD_NAME="${2:-}"; shift ;;
    --confirm-build-number) CONFIRM_N="${2:-}"; shift ;;
    --dry-run)              DRY_RUN=true ;;
    --skip-qa)              SKIP_QA="${2:-}"; shift ;;
    --status)               STATUS_ONLY=true ;;
    --abort)                ABORT=true ;;
    --retry)                RETRY=true ;;
    --selftest)             SELFTEST=true ;;
    -h|--help)              usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ── 공통 ──────────────────────────────────────────────────────────────────────
say()  { echo "━━━ [release] $*"; }
note() { echo "    $*"; }
die()  { local code="${2:-2}"; echo "⛔ $1" >&2; exit "${code}"; }   # die <message> [exit=2]
gh_()  { gh "$@" --repo "${GH_REPO}"; }
WORK=""
cleanup() { if [ -n "${WORK}" ]; then rm -rf "${WORK}"; fi; }   # `[ -n ] &&` 꼴은 빈 WORK 에서 종료코드를 덮는다
trap cleanup EXIT

init() {
  [ -n "${BUILD_NAME}" ] || die "--build-name X.Y.Z is required"
  [[ "${BUILD_NAME}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "build name must be X.Y.Z: '${BUILD_NAME}'"
  if [ -n "${CONFIRM_N}" ] && ! [[ "${CONFIRM_N}" =~ ^[1-9][0-9]*$ ]]; then die "--confirm-build-number must be a positive integer"; fi
  RELEASE_BRANCH="release/${BUILD_NAME}"
  STATE_DIR="$(git rev-parse --git-dir)/release-driver"
  STATE_FILE="${STATE_DIR}/${BUILD_NAME}.json"
  ART_DIR="${STATE_DIR}/artifacts/${BUILD_NAME}"
  # STATE_DIR/ART_DIR 는 첫 쓰기(state_set · rcm_fetch)에서야 만든다 — --status 는 아무것도 남기지 않는다
  WORK="$(mktemp -d -t release_driver.XXXXXX)"
}

# 캐시 — 원격 진실이 아니다. 잡 번호·N 을 남겨 다음 호출이 대조한다.
state_get() { [ -f "${STATE_FILE}" ] || { echo ""; return 0; }; ${CHECK} get --file "${STATE_FILE}" --key "$1"; }
state_set() {
  mkdir -p "${STATE_DIR}"
  python3 - "${STATE_FILE}" "$1" "$2" <<'PY'
import json, os, sys
path, key, val = sys.argv[1:4]
doc = {}
if os.path.exists(path):
    try: doc = json.load(open(path, encoding="utf-8"))
    except Exception: doc = {}
doc[key] = val
tmp = path + ".tmp"
json.dump(doc, open(tmp, "w", encoding="utf-8"), indent=1)
os.replace(tmp, path)
PY
}

# ── 프로파일 조각 → 상수 (네트워크 없음) ─────────────────────────────────────────
load_profile() {
  [ -f "${PROFILE_FILE}" ] || die "${PROFILE_FILE} is missing — run /rcm-store-connect first"
  local kv
  kv="$(${CHECK} profile --file "${PROFILE_FILE}" --repo "${REPO_NAME}" ${PRESETS_FILE:+--presets "${PRESETS_FILE}"})" || die "profile fragment is not usable (above)"
  local line key val env_name
  while IFS= read -r line; do
    key="${line%%=*}"; val="${line#*=}"
    case "${key}" in
      REPO_NAME|DEFAULT_BRANCH|TAG_PATTERN|PRESET_PLAN|PRESET_UPLOAD|PRESET_REVIEW|PRESET_GATE|PRESET_QA)
        env_name="RELEASE_${key}"; printf -v "${key}" '%s' "${!env_name:-${val}}" ;;   # RELEASE_<KEY> 가 이긴다
    esac
  done <<<"${kv}"
}

# ── 프리플라이트 — 로컬(파일·상수·git 리모트)은 항상, 원격(gh·서버·프리셋)은 실제 회차만 ───────
preflight_local() {
  [ -n "${GH_REPO}" ] || die "GH_REPO is empty — set it at the top of this script (TODO(project))"
  command -v gh >/dev/null || die "gh is not installed"
  command -v "${RCM%% *}" >/dev/null || die "rcm is not installed"
  git remote get-url origin >/dev/null 2>&1 || die "no git remote 'origin'"
  [ -n "${PRESET_PLAN}" ] && [ -n "${PRESET_UPLOAD}" ] || die "plan/upload presets are not set in ${PROFILE_FILE}"
  case "${TAG_PATTERN}" in *"{version}"*"{build}"*) ;; *) die "tag pattern must contain {version} and {build}: '${TAG_PATTERN}'" ;; esac
}
preflight_remote() {   # --status 와 --selftest 는 여기 오지 않는다 — 연결 시점엔 서버가 없다
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated"
  ${RCM} check >/dev/null 2>&1 || die "rcm check is red — fix the client/server first"
  local p
  for p in "${PRESET_PLAN}" "${PRESET_UPLOAD}" ${PRESET_GATE:+"${PRESET_GATE}"} ${PRESET_QA:+"${PRESET_QA}"}; do
    ${RCM} presets --json 2>/dev/null | grep -q "\"${p}\"" || die "preset '${p}' is not on the server"
  done
}

# ── rcm 헬퍼 — 제출은 --no-wait 로 2 단계(잡 번호를 캐시에 남기고 나서 기다린다) ─────────
rcm_jobs_json() { ${RCM} jobs --json >"${WORK}/jobs.json" 2>/dev/null || echo "[]" >"${WORK}/jobs.json"; echo "${WORK}/jobs.json"; }
find_job() { ${CHECK} find-job --jobs "$(rcm_jobs_json)" --preset "$1" --sha "$2" --states "${3:-}" --inputs "${4:-}"; }
rcm_submit() {   # rcm_submit <preset> <ref> [-f k=v ...] → job_id
  local preset="$1" ref="$2"; shift 2
  local out
  out="$(${RCM} run "${preset}" --source git_ref --ref "${ref}" --by "release-driver@$(hostname -s)" --no-wait "$@" 2>&1)" \
    || { printf '%s\n' "${out}" >&2; die "rcm run ${preset} failed to submit"; }
  printf '%s' "${out}" | grep -a '^{' | tail -1 | python3 -c 'import sys,json; print(json.loads(sys.stdin.read() or "{}").get("job_id") or "")'
}
rcm_wait() {     # rcm_wait <job_id> → path of the last JSON line (exit code is decided from the JSON, not from rcm wait)
  local jid="$1" out="${WORK}/wait_${1}.json"
  ${RCM} wait --job "${jid}" 2>&1 | tee "${WORK}/wait_${jid}.log" | grep -a '^{' | tail -1 >"${out}" || true
  echo "${out}"
}
rcm_fetch() { mkdir -p "$2"; ${RCM} artifacts "$1" --fetch --output "$2" --force >/dev/null 2>&1 || note "artifacts of #$1 not fetched"; }
job_exit() { ${CHECK} exitcode --rcm-json "$1"; }   # → "code reason"

# ── GitHub 헬퍼 ──────────────────────────────────────────────────────────────
prs_json()      { gh_ pr list --head "${RELEASE_BRANCH}" --state all --json number,state,headRefOid,mergedAt,isDraft >"${WORK}/prs.json" 2>/dev/null || echo "[]" >"${WORK}/prs.json"; echo "${WORK}/prs.json"; }
statuses_json() { gh api "repos/${GH_REPO}/commits/$1/status" >"${WORK}/statuses.json" 2>/dev/null || echo '{"statuses":[]}' >"${WORK}/statuses.json"; echo "${WORK}/statuses.json"; }
tags_file()     { git ls-remote --tags origin "refs/tags/${TAG_PATTERN%%\{*}*" 2>/dev/null | sed 's#.*refs/tags/##' >"${WORK}/tags.txt" || true; echo "${WORK}/tags.txt"; }
pr_pick()       { python3 -c 'import json,sys; rank={"MERGED":0,"OPEN":1,"CLOSED":2}; rows=[p for p in json.load(open(sys.argv[1])) if p.get("headRefOid")==sys.argv[2]]; rows.sort(key=lambda p: rank.get(p.get("state",""),3)); print(rows[0]["number"] if rows else "")' "$(prs_json)" "${HEAD_SHA}"; }
post_status()   { gh api -X POST "repos/${GH_REPO}/statuses/$1" -f context="$2" -f state="$3" -f description="${4:-release driver}" >/dev/null 2>&1 || note "could not post status $2=$3"; }
notify()        { :; }   # TODO(project): Teams/Slack webhook — notify <event> <text>; never print secrets

# ── 원격 진실 → 단계 ──────────────────────────────────────────────────────────
head_sha() { git ls-remote --heads origin "${RELEASE_BRANCH}" 2>/dev/null | cut -f1; }
current_stage() {
  HEAD_SHA="$(head_sha)"
  [ -n "${HEAD_SHA}" ] || { echo "S0"; return 0; }
  local qa_flag=(); [ -n "${SKIP_QA}" ] && qa_flag=(--skip-qa)
  ${CHECK} stage --prs "$(prs_json)" --statuses "$(statuses_json "${HEAD_SHA}")" --jobs "$(rcm_jobs_json)" \
    --tags "$(tags_file)" --sha "${HEAD_SHA}" --build-name "${BUILD_NAME}" --build-number "${CONFIRM_N:-$(state_get confirmed_n)}" \
    --tag-pattern "${TAG_PATTERN}" --upload-preset "${PRESET_UPLOAD}" --gate-context "${PRESET_GATE:+${CTX_GATE}}" \
    --qa-context "${PRESET_QA:+${CTX_QA}}" "${qa_flag[@]}"
}

# ── 사람이 친 N — 되돌릴 수 없는 호출(기본 브랜치 push · upload · tag) 은 전부 이 뒤에서만 ──────
require_typed_n() {
  [ -n "${CONFIRM_N}" ] || die "build number N was not typed in this session (--confirm-build-number or the prompt)" 2
  [ "${N_SOURCE}" = "flag" ] || [ "${N_SOURCE}" = "tty" ] || die "N did not come from a human in this call — refusing" 2
  ${CHECK} confirm --typed "${CONFIRM_N}" --plan "${ART_DIR}/plan.json" || die "typed N does not match plan.json" 1
}

# ── 실패 경로: PR close + 라벨 + 보고 코멘트, 브랜치는 남긴다 ─────────────────────────
fail_red() {   # fail_red <stage> <job_id|-> <reason>
  local stage="$1" jid="$2" reason="$3" pr
  say "${stage} red — ${reason}"
  if [ -n "${QA_JID:-}" ] && [ "${stage}" = "S4" ]; then ${RCM} cancel "${QA_JID}" >/dev/null 2>&1 && note "cancelled qa job #${QA_JID}"; fi
  pr="$(pr_pick)"
  if [ -n "${pr}" ]; then
    gh_ label create "${LABEL_BLOCKED}" --force >/dev/null 2>&1 || true
    # TODO(project): attach report.md / step-captures links fetched from the job's artifacts
    gh_ pr comment "${pr}" --body "**${stage} red** — ${reason}$( [ "${jid}" != "-" ] && echo " (rcm job #${jid})" ). Branch \`${RELEASE_BRANCH}\` kept; \`${DEFAULT_BRANCH}\` untouched. Retry with \`--retry\`." >/dev/null
    gh_ pr edit "${pr}" --add-label "${LABEL_BLOCKED}" >/dev/null && gh_ pr close "${pr}" >/dev/null
    note "PR #${pr} closed · label ${LABEL_BLOCKED}"
  fi
  notify release-red "${BUILD_NAME} ${stage}: ${reason}"
  exit 1
}

# ── S0 branch ────────────────────────────────────────────────────────────────
s0_branch() {
  say "S0 cut ${RELEASE_BRANCH} from ${DEFAULT_BRANCH}"
  git fetch --quiet origin "${DEFAULT_BRANCH}" ${DEV_BRANCH:+"${DEV_BRANCH}"}
  if [ -n "${DEV_BRANCH}" ] && ! git merge-base --is-ancestor "origin/${DEFAULT_BRANCH}" "origin/${DEV_BRANCH}"; then
    die "${DEFAULT_BRANCH} is not contained in ${DEV_BRANCH} — back-merge first" 2
  fi
  # TODO(project): the branch may be cut from dev instead of the default branch — change the source ref here
  git push --no-verify origin "origin/${DEFAULT_BRANCH}:refs/heads/${RELEASE_BRANCH}" || die "push of ${RELEASE_BRANCH} failed" 2
  HEAD_SHA="$(head_sha)"; state_set head_sha "${HEAD_SHA}"
  note "✅ ${RELEASE_BRANCH} @ ${HEAD_SHA:0:8}"
}

# ── S1 plan → S2 N ───────────────────────────────────────────────────────────
s1_plan() {
  say "S1 ${PRESET_PLAN} @ ${HEAD_SHA:0:8}"
  local found jid state js code reason
  found="$(find_job "${PRESET_PLAN}" "${HEAD_SHA}" "succeeded,queued,running" "build_name=${BUILD_NAME}")"
  if [ -n "${found}" ]; then read -r jid state <<<"${found}"; note "joining plan job #${jid} (${state})"
  else jid="$(rcm_submit "${PRESET_PLAN}" "${HEAD_SHA}" -f "build_name=${BUILD_NAME}")"; fi
  [ -n "${jid}" ] || die "no plan job id" 3
  state_set plan_job "${jid}"
  js="$(rcm_wait "${jid}")"; read -r code reason <<<"$(job_exit "${js}")"
  rcm_fetch "${jid}" "${ART_DIR}"
  local plan; plan="$(find "${ART_DIR}" -name plan.json | head -1)"
  [ -n "${plan}" ] || die "plan job #${jid} left no plan.json (${reason})" "$([ "${code}" = 0 ] && echo 1 || echo "${code}")"
  cp "${plan}" "${ART_DIR}/plan.json"
  ${CHECK} validate plan "${ART_DIR}/plan.json" --build-name "${BUILD_NAME}" || die "plan.json breaks the contract" 1
  PLAN_N="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("n") or "")' "${ART_DIR}/plan.json")"
  [ -n "${PLAN_N}" ] || die "plan blocked: $(python3 -c 'import json,sys; print("; ".join(b.get("text","") for b in json.load(open(sys.argv[1])).get("blockers",[])))' "${ART_DIR}/plan.json")" 1
}
s2_confirm() {
  say "S2 confirm build number (plan says N = ${PLAN_N})"
  if [ -n "${CONFIRM_N}" ]; then N_SOURCE="flag"
  elif [ -t 0 ]; then printf 'Type the build number to continue (%s): ' "${PLAN_N}" >&2; read -r CONFIRM_N; N_SOURCE="tty"
  else echo "plan: N = ${PLAN_N}"; exit 2; fi   # 비대화(Store 탭): rcm 이 대화상자를 띄우고 --confirm-build-number 로 다시 부른다
  require_typed_n
  if git ls-remote --tags origin "refs/tags/$(echo "${TAG_PATTERN}" | sed "s/{version}/${BUILD_NAME}/;s/{build}/${CONFIRM_N}/")" | grep -q .; then
    die "already released: tag for ${BUILD_NAME}-${CONFIRM_N} exists" 1
  fi
  state_set confirmed_n "${CONFIRM_N}"; state_set confirmed_at "$(date -u +%FT%TZ)"; state_set confirmed_by "${USER:-?}"
  note "✅ N = ${CONFIRM_N} typed (${N_SOURCE})"
}

# ── S3 draft PR ──────────────────────────────────────────────────────────────
s3_pr() {
  say "S3 draft PR ${RELEASE_BRANCH} → ${DEFAULT_BRANCH}"
  PR_NUM="$(pr_pick)"
  if [ -z "${PR_NUM}" ]; then
    # TODO(project): PR title/body house style, reviewers, labels
    PR_NUM="$(gh_ pr create --draft --base "${DEFAULT_BRANCH}" --head "${RELEASE_BRANCH}" \
      --title "release: ${BUILD_NAME} (${CONFIRM_N})" --body "Release round ${BUILD_NAME} · N=${CONFIRM_N} · driven by release_driver.sh" | grep -oE '[0-9]+$')"
  fi
  [ -n "${PR_NUM}" ] || die "no PR" 2
  state_set pr "${PR_NUM}"; note "PR #${PR_NUM}"
}

# ── S4 gate ∥ S5 qa — 같이 제출, 차례로 기다림 ───────────────────────────────────
GATE_JID=""; QA_JID=""
submit_or_join() {   # submit_or_join <preset> <ctx> → job id (empty when the status is already green)
  local preset="$1" ctx="$2" found jid state
  [ "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(next((s["state"] for s in d.get("statuses",[]) if s.get("context")==sys.argv[2]),""))' "$(statuses_json "${HEAD_SHA}")" "${ctx}")" = "success" ] && { echo ""; return 0; }
  found="$(find_job "${preset}" "${HEAD_SHA}" "succeeded,queued,running,uploading,submitted")"
  if [ -n "${found}" ]; then read -r jid state <<<"${found}"; note "joining ${preset} job #${jid} (${state})" >&2
  else jid="$(rcm_submit "${preset}" "${HEAD_SHA}")"; fi
  echo "${jid}"
}
s4_submit() {
  [ -n "${PRESET_GATE}" ] || { note "S4 skipped: no gate role"; return 0; }
  say "S4 ${PRESET_GATE}"; GATE_JID="$(submit_or_join "${PRESET_GATE}" "${CTX_GATE}")"; [ -z "${GATE_JID}" ] || state_set gate_job "${GATE_JID}"
}
s5_submit() {
  [ -n "${PRESET_QA}" ] || { note "S5 skipped: no qa role"; return 0; }
  if [ -n "${SKIP_QA}" ]; then say "S5 skipped by a human: ${SKIP_QA}"; state_set skip_qa "${SKIP_QA}"; [ -z "${PR_NUM:-}" ] || gh_ pr comment "${PR_NUM}" --body "S5 qa skipped: ${SKIP_QA}" >/dev/null; return 0; fi
  say "S5 ${PRESET_QA}"; QA_JID="$(submit_or_join "${PRESET_QA}" "${CTX_QA}")"; [ -z "${QA_JID}" ] || state_set qa_job "${QA_JID}"
}
wait_stage() {   # wait_stage <stage> <job_id> <ctx>
  local stage="$1" jid="$2" ctx="$3" js code reason
  [ -n "${jid}" ] || return 0
  say "${stage} waiting for job #${jid}"
  js="$(rcm_wait "${jid}")"; read -r code reason <<<"$(job_exit "${js}")"
  case "${code}" in
    0) post_status "${HEAD_SHA}" "${ctx}" success "job #${jid}"; note "✅ ${stage} green" ;;
    3) die "${stage} job #${jid} result unknown (${reason}) — re-run to rejoin: rcm wait --job ${jid}" 3 ;;
    2) die "${stage} job #${jid} environment (${reason}) — PR stays open, re-run when fixed" 2 ;;
    *) post_status "${HEAD_SHA}" "${ctx}" failure "job #${jid}"; fail_red "${stage}" "${jid}" "${reason}" ;;
  esac
}
s4_wait() { wait_stage S4 "${GATE_JID}" "${CTX_GATE}"; }
s5_wait() { wait_stage S5 "${QA_JID}" "${CTX_QA}"; }

# ── S6 ff-merge — 되돌릴 수 없다 ─────────────────────────────────────────────────
s6_merge() {
  say "S6 fast-forward ${DEFAULT_BRANCH} ← ${RELEASE_BRANCH}"
  if [ "${DRY_RUN}" = true ]; then note "--dry-run: no merge"; return 0; fi
  require_typed_n
  PR_NUM="${PR_NUM:-$(pr_pick)}"; [ -n "${PR_NUM}" ] || die "no PR for ${HEAD_SHA:0:8}" 1
  git fetch --quiet origin "${DEFAULT_BRANCH}"
  git merge-base --is-ancestor "origin/${DEFAULT_BRANCH}" "${HEAD_SHA}" || die "${DEFAULT_BRANCH} moved — not fast-forwardable; back-merge and start a new version" 1
  gh_ pr ready "${PR_NUM}" >/dev/null || true
  git push --no-verify origin "${HEAD_SHA}:refs/heads/${DEFAULT_BRANCH}" || die "ff push to ${DEFAULT_BRANCH} failed" 1
  notify release-merged "${BUILD_NAME} (${CONFIRM_N}) merged to ${DEFAULT_BRANCH}"
}

# ── S7 upload — 되돌릴 수 없다 · 재제출 금지 ──────────────────────────────────────
s7_upload() {
  local mode="upload"; [ "${DRY_RUN}" = true ] && mode="rehearsal"
  say "S7 ${PRESET_UPLOAD} mode=${mode} N=${CONFIRM_N}"
  require_typed_n
  local found jid state js code reason
  found="$(find_job "${PRESET_UPLOAD}" "${HEAD_SHA}" "" "mode=${mode},build_name=${BUILD_NAME},confirm_build_number=${CONFIRM_N}")"
  if [ -n "${found}" ]; then
    read -r jid state <<<"${found}"
    case "${state}" in
      lost|timed_out) [ "${mode}" = rehearsal ] && jid="" || die "upload job #${jid} is ${state} — whether it uploaded is unknown; NOT resubmitting. Reconcile with the store first (rcm logs ${jid})" 3 ;;
      failed|cancelled) [ "${mode}" = rehearsal ] && jid="" || die "upload job #${jid} ${state} — NOT resubmitting; check the store (rcm logs ${jid})" 1 ;;
      *) note "joining upload job #${jid} (${state})" ;;
    esac
  fi
  if [ -z "${jid:-}" ]; then
    # TODO(project): extra inputs (platform, android_track) — keep every default the reversible one
    jid="$(rcm_submit "${PRESET_UPLOAD}" "${HEAD_SHA}" -f "build_name=${BUILD_NAME}" -f "confirm_build_number=${CONFIRM_N}" -f "mode=${mode}" --no-join)"
  fi
  state_set upload_job "${jid}"
  js="$(rcm_wait "${jid}")"; read -r code reason <<<"$(job_exit "${js}")"
  rcm_fetch "${jid}" "${ART_DIR}/upload"
  local up; up="$(find "${ART_DIR}/upload" -name upload.json | head -1)"
  case "${code}" in
    0) [ -n "${up}" ] || die "upload job #${jid} left no upload.json" 1
       ${CHECK} validate upload "${up}" --build-name "${BUILD_NAME}" --build-number "${CONFIRM_N}" --mode "${mode}" --tag-pattern "${TAG_PATTERN}" || die "upload.json breaks the contract" 1 ;;
    3) die "upload job #${jid} result unknown (${reason}) — NOT resubmitting" 3 ;;
    4) die "store drift (${reason}) — ${DEFAULT_BRANCH} stays; plan again with the next N" 4 ;;
    *) die "upload job #${jid}: ${reason}" "${code}" ;;
  esac
}

# ── S8 tag + back-merge PR ───────────────────────────────────────────────────
s8_verify() {
  local tag; tag="$(echo "${TAG_PATTERN}" | sed "s/{version}/${BUILD_NAME}/;s/{build}/${CONFIRM_N}/")"
  say "S8 verify ${tag}"
  if [ "${DRY_RUN}" = true ]; then note "--dry-run: rehearsal done, nothing tagged"; return 0; fi
  if ! git ls-remote --tags origin "refs/tags/${tag}" | grep -q .; then
    case "${TAG_OWNER}" in
      driver) require_typed_n
              git push --no-verify origin "${HEAD_SHA}:refs/tags/${tag}" || die "tag ${tag} missing and could not be pushed" 1 ;;
      job)    die "tag ${tag} is missing although the upload job succeeded (TAG_OWNER=job) — check the job's tagging step: rcm logs $(state_get upload_job)" 1 ;;
      *)      die "TAG_OWNER must be job|driver: '${TAG_OWNER}'" 2 ;;
    esac
  fi
  if [ -n "${DEV_BRANCH}" ] && ! gh_ pr list --head "${DEFAULT_BRANCH}" --base "${DEV_BRANCH}" --state open --json number --jq 'length' | grep -qv '^0$'; then
    gh_ pr create --base "${DEV_BRANCH}" --head "${DEFAULT_BRANCH}" --title "chore(release): back-merge ${BUILD_NAME} (${CONFIRM_N})" --body "Back-merge after ${tag}." >/dev/null || note "back-merge PR not opened"
  fi
  notify release-done "${BUILD_NAME} (${CONFIRM_N}) uploaded · ${tag}"
  say "DONE ${tag}"
}

# ── --status / --abort / --retry ─────────────────────────────────────────────
do_status() {   # 읽기 전용 — 브랜치도 잡도 만들지 않는다
  local stage; stage="$(current_stage)"
  echo "build ${BUILD_NAME} · branch ${RELEASE_BRANCH} @ ${HEAD_SHA:-none} · N ${CONFIRM_N:-$(state_get confirmed_n)} · pr ${PR_NUM:-$(state_get pr)} · stage ${stage}"
  echo "jobs: plan #$(state_get plan_job) gate #$(state_get gate_job) qa #$(state_get qa_job) upload #$(state_get upload_job)"
}
do_abort() {
  HEAD_SHA="$(head_sha)"; local pr; pr="$( [ -n "${HEAD_SHA}" ] && pr_pick || echo "")"
  if [ -n "${pr}" ] && [ "$(gh_ pr view "${pr}" --json state --jq .state)" = "OPEN" ]; then gh_ pr close "${pr}" >/dev/null; note "PR #${pr} closed"; fi
  [ -z "${GATE_JID:-$(state_get gate_job)}" ] || ${RCM} cancel "$(state_get gate_job)" >/dev/null 2>&1 || true
  [ -z "$(state_get qa_job)" ] || ${RCM} cancel "$(state_get qa_job)" >/dev/null 2>&1 || true
  rm -f "${STATE_FILE}"; say "aborted — ${DEFAULT_BRANCH} untouched, ${RELEASE_BRANCH} kept"
}
do_retry() {    # 빨강으로 닫힌 PR 뒤 같은 버전명으로 다시: PR 재개 + 라벨 제거 + 브랜치 갱신 + 캐시 삭제
  HEAD_SHA="$(head_sha)"; local pr; pr="$( [ -n "${HEAD_SHA}" ] && pr_pick || echo "")"
  if [ -n "${pr}" ]; then gh_ pr edit "${pr}" --remove-label "${LABEL_BLOCKED}" >/dev/null 2>&1 || true; gh_ pr reopen "${pr}" >/dev/null 2>&1 || true; fi
  git fetch --quiet origin "${DEFAULT_BRANCH}"
  git push --no-verify --force-with-lease origin "origin/${DEFAULT_BRANCH}:refs/heads/${RELEASE_BRANCH}" || die "could not refresh ${RELEASE_BRANCH}" 2
  rm -f "${STATE_FILE}"; rm -rf "${ART_DIR}"; mkdir -p "${ART_DIR}"; say "retry — resuming from remote truth"
}

# ── main ─────────────────────────────────────────────────────────────────────
main() {
  init; load_profile; preflight_local
  if [ "${STATUS_ONLY}" = true ]; then do_status; exit 0; fi   # 읽기 전용 — preflight_remote 를 거치지 않는다
  preflight_remote
  if [ "${ABORT}" = true ]; then do_abort; exit 0; fi
  if [ "${RETRY}" = true ]; then do_retry; fi
  local stage; stage="$(current_stage)"; say "stage from remote truth: ${stage}"
  case "${stage}" in
    DONE) die "already released: $(echo "${TAG_PATTERN}" | sed "s/{version}/${BUILD_NAME}/;s/{build}/${CONFIRM_N:-$(state_get confirmed_n)}/")" 1 ;;
    BLOCKED_PR_CLOSED) die "PR for ${HEAD_SHA:0:8} is closed (${LABEL_BLOCKED}) — use --retry or --abort" 1 ;;
  esac
  PLAN_N=""; PR_NUM=""
  [ "${stage}" = S0 ] && s0_branch
  if [ "${stage}" = S0 ] || [ "${stage}" = S1 ]; then s1_plan; s2_confirm; s3_pr; stage="S4"; fi
  # S4~S6 에 재진입했을 때도 N 은 이 세션에서 사람이 준 것이어야 한다 — 캐시의 N 은 표시용일 뿐 require_typed_n 을 못 연다
  if [ -n "${CONFIRM_N}" ] && [ -z "${N_SOURCE}" ]; then N_SOURCE="flag"; [ -f "${ART_DIR}/plan.json" ] || die "plan.json is not cached here — run once without --confirm-build-number on this machine" 2; fi
  case "${stage}" in
    S4|S5) s4_submit; s5_submit; s4_wait; s5_wait; s6_merge; s7_upload; s8_verify ;;
    S6) s6_merge; s7_upload; s8_verify ;;
    S7) s7_upload; s8_verify ;;
    S8) s8_verify ;;
  esac
}

# ── selftest — 판정기의 selftest + 이 스크립트의 문법 · 함수 존재 ───────────────────
selftest() {
  bash -n "${SELF}" || exit 1
  ${CHECK} --selftest || exit 1
  local fn; for fn in s0_branch s1_plan s2_confirm s3_pr s4_submit s5_submit s6_merge s7_upload s8_verify require_typed_n fail_red do_status do_abort do_retry; do
    declare -F "${fn}" >/dev/null || { echo "missing function ${fn}"; exit 1; }
  done
  for fn in s6_merge s7_upload; do grep -A4 "^${fn}()" "${SELF}" | grep -q require_typed_n || { echo "${fn} lacks require_typed_n"; exit 1; }; done
  # --status 는 preflight_remote 앞에서 끝나야 한다(연결 시점엔 gh 도 서버도 없다)
  [ "$(grep -n -E 'STATUS_ONLY.*do_status|^  preflight_remote$' "${SELF}" | head -1 | grep -c do_status)" = 1 ] || { echo "--status must return before preflight_remote"; exit 1; }
  echo "release_driver.sh selftest: all green"   # 공용 정규식 selftest[: ]+(PASS|ok|all green) 에 맞춘 마지막 줄
}

if [ "${SELFTEST}" = true ]; then selftest; else main; fi
