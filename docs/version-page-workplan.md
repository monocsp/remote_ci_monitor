# 버전 페이지와 바텀시트 — 상세 구현 계획서 (v1.0 · 2026-09-18)

> 출처: 기획 초안 «버전 페이지와 바텀시트» — `docs/wireframes/web-version-page.html` (R1~R12 요구사항 · W1~W5 와이어프레임 · Q1~Q8). 이 문서는 그 단계 계획
> (A~F)을 **파일 · 함수 · 입력 · 테스트 · 완료 조건** 수준으로 내린 것이고, 마지막 세 절(§7 완료 체크리스트 · §8
> 엣지케이스 · §9 격리 검증 프로토콜)은 개발이 끝난 뒤 **격리 에이전트가 그대로 실행**하는 대본이다.
>
> 바꾸지 않는 것: 런타임 의존성 0 · rcm 은 특정 프로젝트를 모른다(스토어에 쓰는 것은 프로젝트 스크립트) · 출시 버튼 없음 ·
> 관리형 게시는 제출마다 사람이 · 빌드 번호는 플랜의 `n` 만 · 비밀은 서버에만 · 옛 빌드가 새 DB 를 거절하는 것.

## 0. 확정된 결정 (소유자 답 2026-09-18)

| Q | 결정 | 적용되는 곳 |
|---|---|---|
| Q1 드래프트 수명 | **24시간**. 프로파일 키 `version_ttl_hours`(정수 ≥ 1)로 바꿀 수 있다 | A 설정 · B2 청소기 |
| Q2 편집한 드래프트 | **자동 삭제하지 않는다.** 만료되면 경고만 띄우고 사람이 «버리기»로만 지운다. 손도 안 댄 드래프트만 자동 삭제 | B2 청소기 · C1 목록 행 |
| Q3 두 스토어 버전 이름 | **둘 다 끝까지 따로 옮긴다**(§11). 화면은 언제나 둘 다 쓰고, 태그만 규칙으로 합친다. 자동과 직접의 차이는 대화상자가 힌트를 그대로 쓰느냐뿐이다 | A 계약 · B1 라우트 · C · D |
| Q4 편집본의 최종 자리 | **제출이 성공한 뒤** 프로젝트 스크립트가 `store/` 에 되쓰고 릴리스 브랜치에 커밋해 PR 로 보낸다. 앱 저장소는 비공개이고 `store/` 문안은 이미 그 저장소에 있으므로 노출이 늘지 않는다. 안 하면 다음 릴리스에서 옛 값이 이번 편집을 덮어써 조용히 원복된다 | E dolomood |
| Q5 지금의 행 넷 | **한 줄 요약 띠**로 접고 «자세히»는 `#/store/<repo>/status` 에서 지금 그대로. 띠의 내용은 §12 | C1 |
| Q6 맨 위 큰 막대 | **제거.** 바텀시트로 옮긴다. 회차·빌드·심사가 진행 중이면 **접힌 헤더에서도 막대와 진행 정도가 보인다**(줄여서라도) | D |
| Q7 언어 | **한국어 하나.** `prefill.json` 에 `locale` 만 기록한다 | A · C2 |
| Q8 스크린샷 편집 | 이번 범위는 **보기와 바뀜 표시까지**. 웹 업로드는 별도 작업으로 미룬다. 그때 다룰 것: 형식 거부 시 대체(png → jpg) · 스토어별 규격과 용량 · 알파 채널 금지 · 순서와 삭제 | C2 범위 밖 |

## 1. A — 계약과 스킬 (rcm 저장소 · 1 PR)

### 1.1 `docs/release-contract.md`

- §2 표에 역할 **`version`**(선택) 추가:
  - 입력: `mode = prefill|create|delete`(기본 **prefill** — create/delete 가 기본이면 `rcm check` FAIL) · `ios_version` · `android_version`(빈 값 = 그 스토어는 만들지 않음) · `asc_version_id`(delete 때).
  - 산출물: `version.json`(create · delete) · `prefill.json`(prefill · create).
  - 종료 코드: 0 · 1 실패 · 2 환경 · 3 이미 있음(같은 이름의 편집 중 ASC 버전) · 4 삭제 불가(제출된 버전).
- review · upload 입력에 **`listing_json`**(문자열, 기본 `""`) 추가: rcm 이 편집본을 JSON 문자열로 넘긴다. 스크립트는 비어 있지 않으면 `store/` 값보다 우선한다. `mode=plan` 도 받아서 `review-plan.json.listing.preview` 에 «이 값으로 올라간다»를 되돌려 준다.
- `plan.json` 선택 필드: `store.play.production_name`(문자열) · `next_version_hint: {ios, android}`. 없으면 rcm 이 `asc_live` · `production_name` 의 마지막 숫자 +1 로 계산한다(§3.1).
- 드라이버(§5): `--version-id <n>` 선택 인자, 상태 줄 `stage V …`, 단계 목록 `V S0 … S8`.
- 산출물 최소 필드:

```jsonc
// version.json — mode=create · delete 뒤 (어떻게 끝나든 쓴다)
{ "schema": 1, "mode": "create", "ios": { "version": "1.1.1", "asc_version_id": "abc123", "state": "PREPARE_FOR_SUBMISSION" } | null,
  "android": { "version": "1.0.1" } | null, "measured_at": "2026-09-18T02:00:00Z" }
// prefill.json — 이전 버전(라이브)의 문안. 키는 review 계약의 listing 필드 이름과 같다
{ "schema": 1, "source": "asc_live:1.1.0 · play_listing", "locale": "ko",
  "ios": { "subtitle": "…", "promotional_text": "", "description": "…", "keywords": "…", "support_url": "…", "marketing_url": "…",
           "whats_new": "…", "screenshots": [ { "path": "store/screenshots/ios/ko/0.png" } ] },
  "android": { "title": "…", "short_description": "…", "full_description": "…", "whats_new": "…", "graphics": [] } }
```

### 1.2 `src/remote_ci_monitor/config.py` · `release_state.py` · `cli.py`

- `RELEASE_ROLES` · `LISTED_ROLES` 에 `"version"`; `ROLE_FILES["version"] = ("version.json", "prefill.json")`.
- 프로파일 키 `version_ttl_hours`(기본 24, 정수 ≥ 1, `_RELEASE_KEYS` 에 추가, `ReleaseProfile.version_ttl_hours`).
- `rcm check` `release <repo>` 행: `version` 프리셋이 있으면 입력 넷을 요구하고 `mode` 기본이 `prefill` 이어야 한다(아니면 FAIL); review/upload 프리셋에 `listing_json` 이 없으면 **warn** «listing_json 없음 — 웹에서 편집한 문안이 전달되지 않는다».
- 테스트: `tests/test_config_release_profile.py` 하나에 다 넣는다(키 · 기본값 · 오류 문구 · `rcm check` 의 version 프리셋 FAIL/OK · listing_json warn · build_name_android warn). 그 파일에 이미 `rcm check` 절이 있다.

### 1.3 스킬 템플릿 (`src/remote_ci_monitor/skills/…`)

| 파일 | 변경 |
|---|---|
| `rcm-store-connect/templates/release_version.sh` (신규) | 역할 `version`. 훅 `store_version_create IOS ANDROID` · `store_version_delete ASC_ID` · `store_prefill` (각각 `TODO(project)` 블록 + 셀프테스트 shim). 기본 `mode=prefill`. `--selftest`: prefill 이 store/ 파일에서 채워지는지 · create 가 shim 을 부르고 `version.json` 을 쓰는지 · delete 가 제출된 버전(shim `submitted`)에서 4 로 끝나는지 · 잘못된 이름 → 2 |
| `rcm-store-connect/templates/rcm_contract.py` | `KINDS` 에 `version` · `prefill`; 검증 규칙(§1.1 최소 필드); `write_version` · `write_prefill` |
| `rcm-store-connect/templates/release_review.sh` · `release_upload.sh` | `RCM_INPUT_LISTING_JSON` 을 읽어 비어 있지 않으면 `$WORK/listing.json` 에 쓰고 훅에 경로를 넘긴다(`store_submit … LISTING_FILE`). `listing_lines preview` 는 파일이 있으면 그 값을 `key: value` 줄로 낸다 |
| `rcm-store-connect/templates/presets.release.toml` | `release-version` 프리셋(입력 넷, `mode` 기본 prefill, `source_modes = ["git_ref"]`) · review/upload 에 `listing_json` 입력 |
| `rcm-store-connect/templates/profile.toml` | `presets.version = "release-version"` · `version_ttl_hours = 24` |
| `rcm-store-connect/SKILL.md` | 파일 표 · 검증 단계에 위 셋 · «이미 있는 review/upload 스크립트에는 `listing_json` 처리만 덧붙인다(adopt-existing 규칙)» |
| `rcm-release-driver/templates/release_driver.sh` · `release_check.py` | `--version-id` 인자(있으면 `V` 단계: rcm API `GET …/release/versions/<id>` 로 버전 이름을 받아 `BUILD_NAME` 로 씀 · 없으면 지금처럼) · `stage V` 출력 · `STAGES = ["V","S0",…]` |
| `rcm-connect/SKILL.md` | tiers 에 `version`(선택) · 「새 버전 만들기를 쓰려면 version 역할」 · 헤더 `version:` 줄 |

- 템플릿 셀프테스트는 저장소 루트에서 `bash <template> --selftest` 로 돌아야 한다(`SELF=` 규칙).
- `tests/test_skills_templates.py`(있으면 추가): `release_version.sh --selftest` 0 · `rcm_contract.py validate version --file` 거절 사례.

### 1.4 완료 조건 → §7 AC-A1~A8

## 2. B — 서버 (rcm 저장소 · 2 PR: B1 저장소+라우트 · B2 전달+청소기+CLI)

### 2.1 저장소 (`store.py`) — DB **v20**

```sql
CREATE TABLE versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL,
  ios_version TEXT, android_version TEXT,                 -- 둘 중 하나는 NOT NULL (앱 레벨 검사)
  state TEXT NOT NULL,                                    -- creating | editing | running | submitted | discarded | failed
  created_by TEXT NOT NULL, created_at REAL NOT NULL, last_edit_at REAL, expires_at REAL NOT NULL,
  asc_version_id TEXT, prefill_json TEXT, edited_json TEXT, error TEXT,
  create_job_id INTEGER, delete_job_id INTEGER, release_id INTEGER, review_job_id INTEGER, expiry_warned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX versions_repo ON versions(repo, id DESC);
```

- 마이그레이션 20: `CREATE TABLE IF NOT EXISTS …` + 인덱스. v19 파일은 백업 뒤 올린다(기존 규칙).
- 메서드: `create_version(...) -> id` · `get_version(id)` · `list_versions(repo, include_closed=True)` · `update_version(id, **fields)`(허용 열만) · `open_versions_expired(now)`(state editing · last_edit_at NULL · expires_at < now) · `versions_to_warn(now)`(편집 있음 · expires_at < now · expiry_warned 0) · `count_versions()`.
- 청소기(`rcm gc` · 보존 정리)는 이 표를 **건드리지 않는다**(releases 와 같다).
- 테스트: `tests/test_store_versions.py`(새 DB v20 열 집합 · v19→v20 백업+마이그레이션 · 옛 빌드 거절 · CRUD · 만료 조회 · `update_version` 이 모르는 열을 거절).

### 2.2 라우트 (`server.py` — `_route` 표와 핸들러)

| 라우트 | 권한 | 동작 |
|---|---|---|
| `GET /api/repos/<r>/release/versions` | 읽기 | `{ live: {ios, android, from_plan_job}, hints: {ios, android}, drafts: [row…], history: [ {ios, android, submitted_at, review_job_id} … ≤ 20 ] }`. `live`·`hints` 는 최신 성공 플랜의 `plan.json` 에서(§3.1 규칙을 서버도 같은 함수로 — `release_state.next_version_hint(plan_doc)`) |
| `POST …/release/versions` | admin · 관문 통과 | 본문 `{ios_version?, android_version?}`. 검증: 둘 중 하나 이상 · `major.minor.patch` · 라이브보다 큼(라이브를 알 때만) · 같은 이름의 열린 드래프트 없음(409 `version_exists`). 행 생성(state `creating`, `expires_at = now + ttl`). `version` 프리셋이 있으면 `mode=create` 잡 제출 → 202 `{id, job_id}`; 없으면 state `editing`, `prefill_json = null` → 201 `{id, job_id: null}` |
| `GET …/release/versions/<id>` | 읽기 | 행 + `prefill` + `edited` + `diff` + `release`(지금의 `/release` 보기, `build_name` 은 이 버전의 iOS 이름, 없으면 Android). **하위 프로세스를 하나도 돌리지 않는다**(#164) — `driver` 와 `listing` 은 여기 없고 웹이 `GET …/release/driver` · `GET …/release/listing` 을 자기 박자로 부른다. 소개 자료는 (저장소 · sha · build_name) 로 30초 기억한다 |
| `PUT …/release/versions/<id>/listing` | admin | 본문 `{ios: {...}, android: {...}}` — 허용 키만(§1.1 prefill 키 집합), 문자열만, 각 값 ≤ 16 KB. `edited_json` 저장 · `last_edit_at = now`. 상한 초과는 저장하고 카운터가 말한다(스토어가 최종 판정). state 가 `submitted`·`discarded` 면 409 `version_closed` |
| `GET …/release/versions/<id>/diff` | 읽기 | `{fields: [ {platform, key, old, new} … ], screenshots: {ios: "same"|"n/a", android: …}}` — `release_state.listing_diff(prefill, edited)` |
| `DELETE …/release/versions/<id>` | admin | «버리기». state `submitted` → 409. `asc_version_id` 가 있고 `version` 프리셋이 있으면 `mode=delete` 잡 → 202 `{job_id}`, 잡이 0 으로 끝나면 `discarded`; 아니면 즉시 `discarded` 200 |
| `POST …/release/plan` · `review` · `upload` · `start` | 기존 | 본문 `version_id` 선택. 있으면 (a) `build_name` 을 행에서 채운다(본문의 것이 있으면 같아야 함, 아니면 400) (b) review/upload: `edited_json` 이 있고 prefill 과 다르면 `inputs["listing_json"]` — 프리셋에 그 입력이 없으면 409 `listing_json_unsupported` (c) start: `--version-id` 전달, `release_id` 링크 (d) 행 `state = running`(잡·회차가 끝나면 다시 `editing`, review submit 이 `submitted` 로 끝나면 `submitted`) |

- 잡 완료 훅: 서버는 이미 `KIND_JOB_FINISHED` 이벤트를 낸다. `_version_job_finished(job)` 이 `create_job_id`·`delete_job_id`·`review_job_id` 와 맞는 잡을 찾아 산출물(`version.json` · `prefill.json` · `review.json`)을 읽고 행을 갱신한다. 실패(exit≠0 · 산출물 없음)는 `state=failed`·`error`(create 때) 또는 `editing` 으로 되돌림 + `error`(delete 때).
- 릴리스 뷰의 `jobs[]` 에 역할 `version` 도 보인다(LISTED_ROLES).
- 테스트: `tests/test_server_release_versions.py` — 위 표의 행마다 최소 1 케이스, §8 의 서버 엣지케이스 전부.

### 2.3 청소기 · 시작 복구 · CLI

- `Janitor` 에 `sweep_versions(now)`: `open_versions_expired` → 각 행을 DELETE 와 같은 경로로(잡 제출 또는 즉시 discarded), `versions_to_warn` → `expiry_warned=1` + 서버 로그 한 줄. 주기 1시간(기존 sweep 주기에 얹는다). 서버 기동 때 한 번.
- 서버 재시작 복구: `creating` 인데 잡이 이미 끝났으면 완료 훅을 다시 적용; `running` 인데 잡·회차가 없으면 `editing` 으로.
- CLI `rcm release`(`cli.py`): `new [--repo R] [--ios X] [--android Y] [--yes]`(인자가 없으면 힌트를 기본값으로 **버전만** 묻는다 — «새 버전을 만듭니다» 문장 뒤 `iOS [1.1.1]: ` · `Android [1.0.1]: `, 엔터 = 힌트, `-` = 만들지 않음) · `list [--repo R]` · `delete <id>` · `open <id>`(웹 주소 출력). 서버 API 만 부른다.
- 테스트: `tests/test_janitor_versions.py` · `tests/test_cli_release.py`(입력 프롬프트는 `input` 을 몽키패치).

### 2.4 완료 조건 → §7 AC-B1~B12

## 3. C — 웹: 버전 목록 · 새 버전 대화상자 · 버전 페이지 (2 PR)

### 3.1 순수 함수 (`app.js` 앞부분, `module.exports`)

| 함수 | 규칙 |
|---|---|
| `parseRoute(hash)` | `#/store/<repo>` → `{view:"store", repo, sub:"versions"}` · `#/store/<repo>/v/<id>` → `sub:"version", id` · `#/store/<repo>/status` → `sub:"status"` |
| `nextVersionHint(planDoc)` | `next_version_hint` 가 있으면 그대로. 없으면 `asc_live` · `play.production_name` 의 마지막 정수 +1(`1.1.0 → 1.1.1`, `2.0 → 2.1`, `1.0.0-rc1 → null`). 없으면 `null` |
| `versionNameCheck(name, live)` | `major.minor.patch` 꼴 · 정수 세 개 · live 가 있으면 semver 비교로 커야 함 → `{ok, reason: pattern|not_greater|empty}` |
| `listingDiff(prefill, edited)` | 플랫폼별 키 비교. 문자열 정규화(양끝 공백 · CRLF → LF). `changed[]` · `unchanged` 수 · 스크린샷은 `same|n/a` |
| `fieldCounter` | 기존 것. 상한 표 `FIELD_LIMITS` + `whats_new` 4000/500 |
| `versionListModel(doc, lang)` | 목록 행 셋(드래프트 · 라이브 · 지난 것)의 머리 문구 · 필 · 버튼 |
| `versionPageModel(vdoc, lang)` | 필드 값 = `edited[k] ?? prefill[k] ?? listingFields[k] ?? ""` · 출처 표시(prefill · file · edited) · `changed` 칩 |

### 3.2 화면

- **W1 버전 목록**: 머리에 «상태 띠»(설정 n/m · 소스 main ⊂ dev · 미러 나이 · 빨간 것이 있으면 빨강 + 자동 펼침 안내 «상태 자세히 →`#/store/<r>/status`»). «+ 새 버전 만들기»(admin · 관문 통과 · 드래프트 `creating` 중이면 비활성). 드래프트 행: 이름 · 필 · 만든 지 · 바뀐 필드 수 · 빌드 유무 · «열기» «버리기». 라이브 행 · 지난 행(≤ 20).
- **W2 대화상자** `#version-dialog`: 알림 문장 · iOS 체크+칸(힌트 프리필) · Android 체크+칸 · 규칙 문장 · «만들기»(둘 다 꺼짐 · 꼴 틀림 · 라이브보다 작음이면 비활성 + 이유). 만들기 → POST → 202 면 `#/store/<r>/v/<id>` 로 이동, 페이지는 `creating` 동안 «만드는 중 · 잡 #n» 을 보이고 5초 폴링.
- **W3 버전 페이지 본문**: 두 절(App Store · Google Play) 같은 배치, **모든 문안 칸이 `<textarea>`/`<input>`**. 값은 `versionPageModel`. 입력 800 ms 디바운스 → `PUT …/listing`(admin 아니면 읽기 전용 + 안내). 저장 상태 «자동 저장 · 12s 전» / «저장 실패: …». «되돌리기»(칸별, prefill 값으로). 스크린샷은 보기만(+ «이전 버전과 같음» 칩). 빌드·출시 설정 · 심사 정보 · 앱 콘텐츠는 지금 문구 그대로(읽기 전용).
- **`#/store/<r>/status`**: 지금의 네 행 그대로(코드 재사용, 심사 패널 없이).
- i18n: 모든 새 문구 EN/KO 둘 다, 「잡」 금지(작업).

### 3.3 완료 조건 → §7 AC-C1~C10

## 4. D — 웹: 바텀시트 (2 PR)

### 4.1 순수 함수 `sheetModel(ctx)`

입력: `{version, release, driver(stepperModel), layers(buildLayers), profile, choices:{platforms, managed, listingFull, phased, nMode, typedN}, token, admin, busy, lang, nowMs}`.
출력: `{ tone: new|running|human|ok|bad|lost|warn|done|expired, head: {ver, statusLine, remainingCount, firstRemaining}, pct, basis, stages:[{id,label,state}], now, elapsed, finishes, remaining:[{code, text, fix:{route|anchor}}], diff, canSubmit, reasons[], submitBody }`.

- `remaining` 판정 순서(첫 항목이 머리에 나온다): `build_missing`(업로드 없음/회차 없음) → `round_running` → `plan_missing|plan_stale|plan_blocked` → `n_unknown|n_mismatch` → `managed_unconfirmed` → `listing_bad`(카운터 bad · Play featureGraphic 없음은 **경고**로만) → `unsafe`. 각 항목은 고치는 곳(`anchor: "#sheet-build"`, `"#f-android-graphics"` …).
- `canSubmit` = `remaining` 에 «bad» 급이 없음 ∧ 기존 `submitDecision` 이 참. 근거 문구는 `submitDecision.reasons` 를 그대로 붙인다.
- `stages`: 드라이버가 있으면 `V S0…S8`(V 는 버전 행이 `editing` 이상이면 done), 없으면 잡 목록 기반(`buildLayers.items`).
- `pct`: 0.3.3 의 `releaseBarModel` 규칙 그대로(10 단계로 분모만 바뀜).
- 회차가 끝나 `upload.json` 이 있으면 `tone ok`, 제출되면 `done`.

### 4.2 화면

- `<section class="sheet" data-sheet>`: `position: sticky; bottom: 0` (본문 끝에 둠 — 스크롤해도 보이도록 `#store` 의 마지막 자식). 머리: 손잡이 · 버전 · 상태 한 줄 · «심사 제출…»(canSubmit 때만 활성) · «펼치기/접기»(상태는 `localStorage rcm.sheet.<repo>` 에 기억). 펼침: 막대 · 단계 칩 · 근거 줄 · 두 열(남은 것 / 이전 버전과 달라진 것) · 제출 조건(체크박스 넷 · 관리형 게시 · 빌드 번호 토글 · 직접 입력 칸) · «보내는 것» · 고정 문장 · 결과(제출 뒤).
- 사람 차례(S2 직접 입력)·N 대화상자는 시트 안 «빌드 번호» 칸으로 흡수(대화상자는 유지하되 시트에서도 입력 가능).
- 삭제: `releaseBarHtml` · `.rbar` CSS · 심사 패널의 버튼/체크박스/nbox(패널은 «절 둘 + 버전 띠» 읽기 전용으로 남고 버전 페이지에서는 편집 칸으로 대체된다 → 결국 `reviewPanelHtml` 은 `#/status` 에서만 쓰인다).
- 모바일(≤ 640px): 머리 두 줄, 펼침은 전체 높이 · 한 열.
- i18n EN/KO.

### 4.3 완료 조건 → §7 AC-D1~D10

## 5. E — dolomood 적용 (dolomood 저장소 · 워크트리 `dolomood-app-renew-rcmconnect` · 1~2 PR)

1. `/rcm-store-connect` 재실행(FORCE_FILES 없이 — 골격만 추가, 기존 스크립트는 «adopt-existing»으로 `listing_json` 처리 덧붙임).
2. `scripts/release/release_version.sh` 의 TODO: `store_version_create` = ASC `POST /v1/appStoreVersions`(platform IOS, versionString) — 기존 `store_review.sh` 의 JWT 도우미 재사용; `store_version_delete` = `DELETE /v1/appStoreVersions/{id}`(상태가 PREPARE_FOR_SUBMISSION 일 때만, 아니면 exit 4); `store_prefill` = 라이브 버전의 `appStoreVersionLocalizations`(ko) + Play `edits/listings`(ko-KR) → `prefill.json`. 실패하면 `store/` 파일로 폴백하고 `source` 에 `file:` 접두.
3. `store_listing.py`: `--listing-json <file>` 옵션 — 있으면 그 값이 `store/` 보다 우선(preview · validate · submit 경로 전부).
4. `release_plan.sh`: `store.play.production_name` · `next_version_hint`.
5. `product_release.sh`: `--version-id` 와 `V` 단계(`release_check.py stage` 에 V).
6. `scripts/rcm/profile.release.toml` 에 `presets.version` · `version_ttl_hours`; `rcm_candidate.py --check` 초록.
7. 검증(§9 E): 셀프테스트 · `rcm check` · **실배치 dry-run** — prefill 은 실제 라이브 문안을 읽는다(읽기 전용, OK). ASC 버전 **create 는 실제 스토어에 드래프트를 만든다** → 소유자 확인 뒤에만(«prod 앱에 1.1.1 draft 를 만들어도 되는가»), 그 전까지는 shim 으로.

## 6. F — 문서 · 설명서 (1 PR + 아티팩트)

- README + README.ko «Store tab» 절: 버전 목록 → 새 버전 → 편집 → 바텀시트 흐름으로 다시 씀. `docs/configuration.md`: `presets.version` · `version_ttl_hours` · `listing_json` 경고. `docs/release-contract.md` §2·§5(1.1 에서). `docs/wireframes/web-store.html` 에 W1~W5 절과 항목 50~62. CHANGELOG. 사용 설명서 아티팩트 v3(캡처는 §9 의 실기 서버에서).

## 7. 완료 체크리스트 (격리 에이전트가 하나씩 확인 · 증거를 남긴다)

형식: `AC-<단계><번호>` — **확인 방법** — 기대. 증거 = 테스트 이름/출력 · curl 응답 · 캡처 경로.

### A 계약·스킬
- AC-A1 — `pytest tests/test_config_release_profile.py` — `version` 역할 · `version_ttl_hours` 기본 24 · 잘못된 값 오류 문구.
- AC-A2 — `rcm check --config <fixture>` — version 프리셋 `mode` 기본 create → FAIL 문구에 «prefill»; prefill → ok; review 에 `listing_json` 없음 → warn 한 줄.
- AC-A3 — `bash src/…/rcm-store-connect/templates/release_version.sh --selftest`(루트에서) — 0 · 4 케이스 통과 출력.
- AC-A4 — `python rcm_contract.py validate version --file bad.json` — `ios`·`android` 둘 다 null 이면 거절.
- AC-A5 — `release_review.sh --selftest` — `RCM_INPUT_LISTING_JSON='{"ios":{"subtitle":"X"}}'` 로 돌리면 `listing.json` 이 생기고 preview 줄에 `subtitle: X`.
- AC-A6 — `release_driver.sh --selftest` — `--version-id 7` 이 `stage V` 를 찍고 이름을 API(shim)에서 받는다; 없으면 예전 경로.
- AC-A7 — `rcm skills install --into /tmp/x` 뒤 `ls` — `release_version.sh` 가 있다; SKILL.md 표에 있다.
- AC-A8 — `docs/release-contract.md` — §2 표에 `version` 행 · `listing_json` · `next_version_hint` · §5 `--version-id`(grep).

### B 서버
- AC-B1 — `pytest tests/test_store_versions.py` — 새 DB `user_version == 20` · 열 집합 · v19→v20 백업 파일 `rcm.sqlite3.v19.bak`.
- AC-B2 — `POST …/versions` (admin, 관문 통과, version 프리셋 있음) — 202 `{id, job_id}` · 행 `creating` · 잡 입력 `mode=create ios_version=… android_version=…`.
- AC-B3 — 같은 요청, version 프리셋 없음 — 201 `job_id null` · 행 `editing`.
- AC-B4 — 이름 검증 — `1.0` 400 · 라이브 `1.1.0` 에 `1.0.9` 400 `not_greater` · 둘 다 빈 값 400 · 같은 이름 열린 드래프트 409 `version_exists` · 클라이언트 토큰 403 · 관문 미통과 409 `setup_incomplete`.
- AC-B5 — 완료 훅 — create 잡이 `version.json`+`prefill.json` 을 쓰고 0 으로 끝나면 행 `editing` · `asc_version_id` · `prefill_json`; 1 로 끝나면 `failed` + `error`.
- AC-B6 — `PUT …/listing` — 허용 키만 저장 · 모르는 키 400 · 16 KB 초과 400 · `submitted` 행 409 `version_closed` · `last_edit_at` 갱신.
- AC-B7 — `GET …/diff` — 바뀐 필드만 `fields[]`, CRLF/공백 차이는 «같음».
- AC-B8 — `POST …/review` with `version_id` — 편집이 있으면 잡 입력 `listing_json` 에 JSON(edited ⊕ prefill); 프리셋에 입력 없으면 409 `listing_json_unsupported`; 편집이 없으면 입력을 보내지 않는다; `build_name` 불일치 400.
- AC-B9 — `DELETE …/versions/<id>` — asc id 있음 → delete 잡 202 → 0 이면 `discarded`; 없음 → 즉시 200 discarded; `submitted` → 409.
- AC-B10 — 청소기 — `expires_at` 지난 미편집 드래프트가 sweep 뒤 `discarded`(잡 있으면 잡 제출); 편집 있음 → `expiry_warned=1`, 상태 그대로.
- AC-B11 — 재시작 복구 — `creating` + 끝난 잡 → 훅 재적용; `running` + 회차 없음 → `editing`.
- AC-B12 — `rcm release new --repo app --yes`(힌트 그대로) → 202 출력 · `rcm release list` 표 · `delete` · `open` 주소.

### C 웹 — 목록 · 대화상자 · 버전 페이지
- AC-C1 — node — `parseRoute` 셋 · `nextVersionHint` 6 케이스 · `versionNameCheck` 5 · `listingDiff` 정규화.
- AC-C2 — CDP — `#/store/app` 이 버전 목록: 라이브 행 · 드래프트 행 · «+ 새 버전 만들기» · 상태 띠(초록) · 네 행은 `#/store/app/status` 에.
- AC-C3 — CDP — 대화상자: 힌트 `1.1.1` · `1.0.1` 프리필, Android 체크 끄면 본문에 `android_version` 없음, 꼴 틀리면 «만들기» 비활성 + 이유.
- AC-C4 — CDP — 만들기 → 202 → `#/store/app/v/<id>` 로 이동 · «만드는 중» → (스텁이 editing 을 주면) 필드가 살아난다.
- AC-C5 — CDP — 필드 값이 prefill 로 채워지고 카운터가 맞다(`23/30`); prefill 없고 파일만 있으면 파일 값 + «파일에서».
- AC-C6 — CDP — 칸을 고치면 800 ms 뒤 `PUT …/listing` 본문에 그 키만 · «자동 저장 · n s 전» · 칩 «바뀜» · «되돌리기» 로 prefill 값.
- AC-C7 — CDP — admin 아님 → 칸 읽기 전용 + 안내.
- AC-C8 — CDP — 드래프트 «버리기» → 확인 대화상자 → DELETE → 목록으로.
- AC-C9 — 폰 폭 390 — 가로 스크롤 없음 · 두 절 세로.
- AC-C10 — i18n 테스트 — 새 키 EN/KO 둘 다 · «잡» 없음.

### D 웹 — 바텀시트
- AC-D1 — node — `sheetModel`: 빌드 없음 → `remaining[0].code == build_missing` · 제출 닫힘; 도는 중 → tone running · pct 규칙(0.3.3 과 같은 수식, 분모 10); 제출 가능 → `canSubmit true` · `remaining` 빈 배열; 제출됨 → done.
- AC-D2 — CDP — 버전 페이지 맨 아래 sticky 시트, 접힘 머리에 버전 · 상태 한 줄(«남은 것 3 · 빌드 없음») · 제출 버튼 비활성 · 펼치기.
- AC-D3 — CDP — 펼치면 막대 · 단계 칩 10개(V 포함) · 남은 것 목록 · diff 두 줄(스텁) · 체크박스 · 관리형 게시 · 빌드 번호 토글.
- AC-D4 — CDP — 도는 중 스텁(S5 · 잡 57%) → 머리 «S5 … · 10단계 중 7번째 · 62%» · 예상 완료.
- AC-D5 — CDP — 제출 가능 스텁 → 관리형 게시 체크 전 닫힘, 체크 후 열림 → 클릭 → 확인 대화상자 → `review` 본문에 `version_id` · `confirm_build_number: "auto"`.
- AC-D6 — CDP — 맨 위 `.rbar` 가 없다 · 심사 패널에 버튼/체크박스가 없다(`#/status` 에도).
- AC-D7 — CDP — 접힘/펼침 상태가 새로고침 뒤 유지(localStorage).
- AC-D8 — CDP — 폰 폭 390 — 시트 머리 두 줄, 본문 스크롤 가능, 가로 스크롤 없음.
- AC-D9 — CDP — 남은 것 항목 클릭 → 해당 칸으로 스크롤 + 포커스.
- AC-D10 — 금지 버튼 검사(`FORBIDDEN_BUTTONS_JS`) == [] 모든 상태.

### E dolomood
- AC-E1 — 셀프테스트 셋 0 · `rcm_candidate.py --check` 초록 · `rcm check` 의 `release dolomood` 행 ok(version 포함).
- AC-E2 — 실기(dry-run) — `mode=prefill` 잡이 실제 라이브 문안으로 `prefill.json` 을 쓴다(값은 화면에서 확인, 로그에 비밀 없음).
- AC-E3 — 실기 — `listing_json` 을 준 `review mode=plan` 이 `review-plan.json.listing.preview` 에 편집값을 되돌려 준다.
- AC-E4 — (소유자 확인 뒤) create 가 ASC 에 draft 를 만들고 delete 가 지운다; 그 전에는 shim 으로 통과.

### F 문서
- AC-F1 — 문서 잠금 테스트 넷 초록 · README/README.ko 미러 동일 절 · CHANGELOG 항목 링크 · 와이어프레임 항목 50~62.
- AC-F2 — 사용 설명서 아티팩트 v3 에 W1~W5 실기 캡처 5장 이상.

## 8. 엣지케이스 시나리오 (각각 테스트 또는 실기로 잠근다 · 번호는 §7 증거에 인용)

| # | 시나리오 | 기대 | 어디서 |
|---|---|---|---|
| E1 | 라이브를 모른다(플랜 없음) | 힌트 없음 · 이름 검증은 꼴만 · «플랜을 먼저 만들면 힌트가 나옵니다» | B4 · C3 |
| E2 | iOS 만 있는 프로젝트(`platforms=ios`) | 대화상자에 Android 칸 없음 · 본문 `android_version` 생략 | C3 |
| E3 | 같은 이름의 드래프트가 이미 있음 | 409 `version_exists` · 대화상자가 «열기» 링크를 보인다 | B4 · C3 |
| E4 | ASC 에 같은 이름의 편집 중 버전이 이미 있음(스토어 쪽) | create 잡 exit 3 → 행 `failed` + error «already exists» · 페이지가 «스토어에 이미 있음 — 그 버전을 쓰려면 …» | B5 |
| E5 | create 잡이 lost/timed_out | 행 `failed` · 재시도 버튼(새 잡) · asc id 없음 | B5 |
| E6 | 서버 재시작 중 create 잡이 끝남 | 기동 복구가 훅을 다시 적용 | B11 |
| E7 | 편집 중 토큰이 사라짐(401) | 저장 실패 배지 · 값은 화면에 남고 재시도 버튼 | C6 |
| E8 | 두 브라우저가 같은 드래프트를 편집 | 마지막 저장이 이김 · 5초 폴링이 다른 쪽 값을 «다른 곳에서 바뀜» 으로 표시(강제 덮어쓰기 없음) | C6 |
| E9 | 칸 값이 상한 초과(설명 4001자) | 저장은 되고 카운터 빨강 · 남은 것에 `listing_bad` · 제출 닫힘 | B6 · D1 |
| E10 | 편집을 전부 되돌려 prefill 과 같아짐 | diff 비어 있음 · review 에 `listing_json` 을 보내지 않음 | B8 · C6 |
| E11 | 프리셋에 `listing_json` 입력이 없는데 편집이 있음 | 409 `listing_json_unsupported` · 시트 남은 것에 «스킬을 다시 돌려 listing_json 을 추가» | B8 · D1 |
| E12 | TTL 지남 · 미편집 · asc id 있음 · version 프리셋 있음 | delete 잡 → discarded; 잡 실패 → 행은 editing 유지 + error | B10 |
| E13 | TTL 지남 · 편집 있음 | 경고만 · 목록 행에 «만료 · 사람이 정리» | B10 · C2 |
| E14 | 제출된 버전 삭제 시도(웹·CLI·청소기) | 409 · 버튼 없음 · 청소기 건너뜀 | B9 |
| E15 | 회차 도는 중 «버리기» | 409 `version_running` | B9 |
| E16 | 드라이버가 `--version-id` 를 모른다(옛 스크립트) | start 는 옛 인자로 폴백하고 시트가 «드라이버가 V 단계를 모른다 — 스킬 재실행» 경고 | B · D |
| E17 | 시트가 열린 채 플랜이 낡음(30분) | 남은 것에 `plan_stale` · 제출 닫힘 · «플랜 새로고침» 링크 | D1 |
| E18 | Google Play 를 끔 | 관리형 게시 항목이 남은 것에서 사라짐 | D1 |
| E19 | `unsafe_release_type` | 시트 tone bad · 빨간 띠 유지 · 제출 닫힘 | D1 · D10 |
| E20 | 업로드 lost | 시트 tone lost · «재제출 금지» · 제출 닫힘 | D1 |
| E21 | 빌드 번호 직접 입력 + 틀린 값 | 시트 안 «≠ 181» · 닫힘 | D5 |
| E22 | 관문 미통과 상태에서 `#/store/<r>/v/<id>` 직접 진입 | 설정 화면으로 보내고 돌아올 주소 기억 | C2 |
| E23 | 폰 폭에서 시트를 펼침 | 본문이 시트 뒤에 가려지지 않음(패딩) · 접기 버튼 보임 | D8 |
| E24 | 드래프트 40개 | 목록은 열린 것 전부 + 지난 것 20 · 느리지 않음(한 요청) | C2 |
| E25 | `version` 프리셋 없음 + Android 만 | 만들기 즉시 editing · prefill 은 파일에서 · 시트 단계는 잡 기반 | B3 · C5 · D1 |

## 9. 격리 검증 프로토콜 (개발이 끝난 뒤 · 단계마다 에이전트 하나)

공통 준비(각 에이전트가 스스로):
1. `git fetch origin && git worktree add ../remote_ci_monitor-verify-<단계> origin/dev`(또는 검증 대상 브랜치) + `.venv` + `pip install -e '.[dev]'`.
2. `ruff check . && ruff format --check . && pytest -q -p no:cacheprovider && node --test tests/web/*.test.js` — 전부 초록이 아니면 **여기서 멈추고** 실패를 그대로 보고.
3. 실기 서버: 포트 `879<n>`(n = 단계 번호, 다른 세션이 8788·8790 을 쓴다) · `data_dir ~/.local/share/rcm-devtest-vp-<n>`(절대 `/tmp` 아래가 아님) · `advertise = false` · `bind 127.0.0.1` · 알림 훅 없음 · 시험 저장소는 `tests/gitrepo.py build_remote` 로 만든 bare 레포 + `tests/test_server_release_routes.py` 의 프로파일/드라이버 스텁을 그대로 쓴 `server.toml`.
4. 웹은 `tests/test_web_browser.py` 의 `Chrome` 클래스(CDP)로 연다. 캡처는 `scratchpad/verify-<단계>/` 에.
5. 보고: `AC-…` 하나마다 `PASS|FAIL|BLOCKED` + 증거(명령 · 응답 앞 3줄 · 캡처 경로) · §8 의 해당 E 번호. FAIL 은 **고치지 말고** 재현 명령과 함께 보고(수정은 조립하는 쪽이 한다). 보고 끝에 «규칙 위반 의심»(출시 버튼 · 비밀 노출 · 번호 지어냄) 절을 따로.
6. 끝나면 서버 종료 · 워크트리 제거.

단계별 대본: A → §7 AC-A1~A8 + E4·E16 의 계약 부분. B → AC-B1~B12 + E1·E3~E6·E9~E15·E25. C → AC-C1~C10 + E1~E3·E7·E8·E10·E13·E22·E24. D → AC-D1~D10 + E11·E17~E21·E23. E → AC-E1~E4(E4 는 소유자 확인 전까지 BLOCKED 가 정답). F → AC-F1~F2.

## 10. 순서와 PR

| 순서 | 브랜치 | 내용 | 뒤따르는 검증 |
|---|---|---|---|
| 1 | `feat/store-version-role-contract` | §1 전부 | 에이전트 A |
| 2 | `feat/store-versions-table-routes` | §2.1 · §2.2 | 에이전트 B(1/2) |
| 3 | `feat/store-versions-listing-janitor-cli` | §2.2 전달 · §2.3 | 에이전트 B(2/2) |
| 4 | `feat/web-version-list-dialog` | §3 W1 · W2 · 라우팅 · 상태 화면 | 에이전트 C(1/2) |
| 5 | `feat/web-version-page-editor` | §3 W3 · 자동 저장 · diff | 에이전트 C(2/2) |
| 6 | `feat/web-bottom-sheet` | §4 시트 · 막대 이동 · 제출 이동 | 에이전트 D |
| 7 | dolomood `feat/rcm-version-role` | §5 | 에이전트 E |
| 8 | `docs/store-version-page` + 릴리스 v0.4.0 | §6 · CHANGELOG · 배포 · 설명서 v3 | 에이전트 F |

1·2 는 병렬(계약 이름이 이 문서에 고정돼 있다). 3 은 2 뒤, 4·5·6 은 순서대로(같은 파일). 7 은 1·3 뒤. 각 PR 은 집안 규칙(`/branch` `/commit` `/pr`, REST 머지, 워크트리 하나)대로.

## 11. 두 스토어 버전 이름을 옮기는 규칙 (Q3 확정)

한 회차가 App Store 와 Google Play 에 서로 다른 버전 이름으로 나갈 수 있다. 라이브 버전이 이미 어긋나 있기
때문이다(예: App Store 1.1.0, Play 1.0.0 → 다음은 1.1.1 과 1.0.1). 빌드 번호는 두 스토어가 공유하는 하나의
숫자라서(플랜이 두 스토어 최대값 + 1), 회차를 유일하게 식별하는 것은 빌드 번호다. 버전 이름은 사람이 읽기
위한 것이다.

- **화면**: 언제나 둘 다 쓴다. `iOS 1.1.1 · Android 1.0.1`. 두 이름이 같으면 `1.1.1` 한 번만.
- **작업 입력**: `build_name` 은 대표 이름(iOS 가 있으면 iOS, 없으면 Android)이고, 두 이름이 **다를 때만**
  `build_name_android` 를 함께 보낸다. 같으면 오늘과 완전히 같은 입력이다. 받는 역할은 **`upload` 와 `review`**
  뿐이다. `plan` 은 스토어 상태를 읽을 뿐이라 버전 이름이 하나면 충분하다.
- **스크립트 안에서**: 훅 서명은 바꾸지 않는다. 플랫폼별로 부를 때 그 플랫폼의 이름을 `BUILD_NAME` 자리에
  넣는다(`store_upload android 181 1.0.1 …`). 도우미 `platform_build_name <ios|android>` 하나가 고른다.
- **프리셋이 모르면 막는다**: 두 이름이 다른데 `upload`·`review` 프리셋에 `build_name_android` 입력이
  없으면 409 `split_version_unsupported`. 조용히 한쪽 이름으로 빌드하지 않는다. `rcm check` 는 그 입력이 없는
  프리셋에 warn 한 줄을 낸다(두 이름을 늘 같게 쓰는 프로젝트는 그대로 두면 된다).
- **태그**: 프로파일의 `tag = "prod/{version}-{build}"` 는 그대로 두고, `{version}` 을 이렇게 푼다.

  | 경우 | `{version}` | 태그 |
  |---|---|---|
  | 두 이름이 같음 | `1.1.1` | `prod/1.1.1-181` |
  | 다름 | `1.1.1+1.0.1` | `prod/1.1.1+1.0.1-181` |

  `+` 는 git 태그 이름에 쓸 수 있다. 한 스토어만 만들면 그 이름 하나다.
- **`listing.release_notes` 의 `{version}`** 도 같은 규칙을 쓰되, 두 이름이 다르면 iOS 이름으로 먼저 찾고
  없으면 Android 이름으로 찾는다(문안 파일은 보통 하나다).

## 12. 목록 화면의 요약 띠 (Q5 확정)

버전을 고르기 전에 «이 앱을 지금 올릴 수 있는 상태인가»만 답한다. 버전마다 달라지는 것(빌드 번호 확인 ·
관리형 게시 · 심사 플랜)은 바텀시트가 맡는다. 칩 넷을 한 줄에 놓고, 하나라도 빨가면 띠가 빨갛다. 띠를 누르면
`#/store/<repo>/status` 로 간다.

| 칩 | 내용 | 빨개지는 조건 | 출처 |
|---|---|---|---|
| 자격 증명 | `비밀 4/4 있음 · 검증 21:53` | 하나라도 없음 · 검증 실패 · 검증 없음 | `setup` |
| 소스 | `main ⊂ dev · 미러 2분 전` | main 이 dev 에 없음 · 미러 30분 초과 · fetch 실패 | `branches` · `mirror` |
| 스토어 | `App Store 1.1.0 (180) · Play 1.0.0 (180) · 다음 빌드 181` | 플랜 없음 · 플랜이 `plan_max_age_minutes` 초과 | `plan.json` |
| 막힘 | `막힘 1 · 경고 2` (없으면 칩 자체가 없다) | 막힘이 하나라도 있음 | `plan.json blockers[] · warnings[]` |

막힘과 경고는 프로젝트 스크립트가 플랜에 적어 보낸 것을 그대로 보인다. 업로드를 실제로 막는 것들(Play 가 다른
편집 중 · App Store 에 이미 편집 중인 버전 · 계약이나 세금 미동의 · 서명 키 불일치)은 프로젝트가 여기에 채우면
이 칩에 뜬다. rcm 은 목록을 만들지 않는다.

## 13. A 단계 격리 검증이 찾은 것 (2026-09-18) — 후속 PR «A2»

AC-A1~A8 과 E4·E16 은 전부 PASS, 규칙 위반 없음. 체크리스트 밖에서 다섯 가지가 나왔다. 1~3 은 C·D·E 를
시작하기 전에 고친다. B1 이 머지된 뒤 한 PR 로 묶는다.

| # | 무엇 | 왜 아픈가 | 할 일 |
|---|---|---|---|
| 1 | `store.play.production_name` 과 `next_version_hint` 를 어느 스킬도 말하지 않는다 | 계약 §2 에는 있는데 `release_plan.sh` 의 TODO 블록과 `rcm-store-connect/SKILL.md` 가 이 이름을 한 번도 쓰지 않는다. 그래서 스킬로 붙인 프로젝트의 `plan.json` 에는 이 필드가 생기지 않고, **Android 라이브 이름을 모르게 된다**. 그러면 §12 요약 띠의 Play 칸과 새 버전 대화상자의 Android 힌트가 늘 빈다 — 소유자 요구 R4 가 바로 깨진다 | `release_plan.sh` 의 `store_snapshot()` TODO 에 두 필드를 넣고, SKILL.md 의 산출물 설명과 검증 단계에 더한다 |
| 2 | 드라이버가 `V` 단계를 아는지 서버가 알아낼 방법이 없다 | 새 템플릿의 `--status` 는 `--version-id` 를 함께 줬을 때만 `V` 를 찍는다. 옛 드라이버에 `--version-id` 를 주면 `unknown argument` 와 exit 2 로 죽는데, exit 2 는 이미 «빌드 번호가 필요하다» 와 «환경 막힘» 을 뜻한다. 종료 코드로 구분이 안 된다 | **`--status` 가 `--version-id` 없이도 언제나 `stages:` 줄을 찍게 한다.** 그 줄에 `V` 가 있으면 서버가 `--version-id` 를 쓰고, 없으면 옛 방식으로 부르고 화면이 «드라이버가 V 단계를 모른다»고 말한다. 읽기 전용이라 안전하다 |
| 3 | `rcm check` 가 `version` 프리셋의 `mode` 선택지를 안 본다 | `choices = ["prefill"]` 인 프리셋이 통과하지만 서버는 `create`·`delete` 를 보낸다. «새 버전 만들기» 가 제출 순간 400 으로만 드러난다 | 경고 한 줄: `mode` 선택지에 `create`·`delete` 가 없으면 warn |
| 4 | `release_check.py` 의 `STAGES` 는 죽은 상수이고 드라이버의 `--status` 와 어긋난다(`S2` 없음) | 단계 목록이 두 벌이다. D 가 이걸 파싱하기 시작하면 성가시다 | 한쪽을 지우거나 둘을 한 곳에서 만들게 한다 |
| 5 | 계획서 §1.2 가 없는 테스트 파일을 지목했다 | 문서 오류뿐 | 고쳤다 |

## 14. B 단계 격리 검증이 찾은 것 (2026-09-20) — 후속 PR «B3»

AC-B1~B12 와 E1·E3~E6·E9·E11~E15·E25 전부 PASS, 규칙 위반 없음. 검증이 서버를 실제로 띄워
잡 28개를 돌리고 코드를 13군데 부숴 테스트가 잡는지까지 확인했다. 그래도 체크리스트 밖에서 여섯이
나왔고, **1·2·3 은 C 단계를 시작하기 전에** 고친다.

| # | 무엇 | 왜 아픈가 | 할 일 |
|---|---|---|---|
| 1 | `GET …/release/versions/<id>` 한 번에 하위 프로세스가 셋 돈다 | 드라이버 `--status` 하나와 소개 자료 명령 둘이 **매 요청** 실행된다. 캐시는 체크아웃 sha 뿐이고 명령 결과는 캐시가 없다. 그런데 §3.2 는 이 화면이 5초마다 폴링한다고 정해 놓았다. 브라우저 하나당 5초마다 맥미니에서 프로세스 셋이 돈다는 뜻이고, dolomood 의 그 명령들은 진짜 스토어를 읽는다 | **`driver` 와 `listing` 을 상세 응답에서 뺀다.** 웹은 이미 있는 `GET …/release/driver` 와 `GET …/release/listing` 을 자기 박자로 부른다. 소개 자료는 (repo, sha, build_name) 로 짧은 TTL 캐시를 둔다. `release` 는 하위 프로세스가 없으니 남기되, 측정해서 비싸면 같이 뺀다 |
| 2 | **업로드 중에 «버리기» 가 열려 있다** | `review` 와 `start` 는 버전 행을 `running` 으로 두어 버리기가 409 로 막히는데, `upload` 만 행에 붙지 않는다. 그래서 `mode=upload` 가 스토어에 바이너리를 올리는 동안 같은 드래프트를 버릴 수 있고, App Store 버전이 있으면 그것을 지우는 잡까지 나간다. 셋 중 가장 되돌리기 어려운 것만 안 막힌 셈이다. §2.2 (d) 는 원래 upload 도 `running` 이라고 적혀 있다 | `mode=upload` 인 업로드 잡을 버전 행에 붙이고 행을 `running` 으로 둔다(`mode=rehearsal` 은 그대로 둔다 — 스토어에 쓰지 않는다). 잡이 끝나면 `editing` 으로 돌아온다. 버리기가 409 `version_running` 을 내는 것을 테스트로 잠근다 |
| 3 | `failed` 드래프트가 이름을 영원히 붙잡는다 | create 가 실패한 행은 청소기의 두 조회 어디에도 안 걸려서 자동 삭제도 경고도 없는데, `version_exists` 는 계속 409 를 낸다. 그래서 «같은 이름으로 다시» 가 안 되고 반드시 버리고 다시 만들어야 한다 | `version_exists` 가 `failed` 를 닫힌 것으로 치고, 청소기가 만료된 미편집 `failed` 행도 버린다. 그 행에는 App Store 버전 id 가 없으므로(서버는 성공했을 때만 기록한다) 스토어 삭제 잡은 나가지 않는다 |
| 4 | `version_for_job` 이 행을 하나만 돌려준다 | 합류(join) 가 있는 제출 경로에 «행은 하나» 가정이 박혀 있다. 지금은 `version_exists` 덕에 도달 불가 | 남겨 두고 주석으로 가정을 적는다 |
| 5 | 만료 경고가 딱 한 번이고 그 뒤로 조용하다 | 편집한 드래프트는 몇 주를 살아도 로그 한 줄뿐이다. 지속 신호는 행의 `expired` · `expiry_warned` 플래그뿐 | C 단계가 목록 행에 그 둘을 반드시 그린다(§12 · E13) |
| 6 | `plan` 과 `upload` 는 `version_id` 를 받아도 상태를 안 바꾼다 | `plan` 은 읽기 전용이라 타당하다. `upload` 는 2번과 같은 이야기 | 2번에서 함께 |

## 15. B3 이후 확정된 것 (2026-09-20 · PR #164)

C 단계가 이 모양 위에 짓는다.

- **버전 상세**(`GET …/release/versions/<id>`)는 드래프트 행 + `prefill` + `edited` + `diff` + `release` 넷뿐이고
  하위 프로세스를 돌리지 않는다. 드라이버와 소개 자료는 **따로** 부른다. 5초 폴링은 상세에만 건다.
  드라이버는 그보다 느리게(예: 10초), 소개 자료는 훨씬 느리게(서버가 30초 기억하므로 그보다 잦을 이유가 없다).
- **드래프트 행**에 `upload_job_id` 가 생겼다. 화면은 `review_job_id` · `release_id` 와 같이 다뤄야 한다.
- **버전이 «진행 중» 인 조건은 셋**이다 — 살아 있는 심사 작업 · 살아 있는 업로드 작업 · 이 버전의 드라이버 회차.
  셋 중 **마지막 하나가 끝날 때** 비로소 «편집 중» 으로 내려온다. 그 전에는 버리기가 409 `version_running` 이다.
  바텀시트의 «남은 것» 과 목록 행의 필이 이 규칙을 그대로 보여야 한다.
- **실패한 드래프트**는 이름을 붙잡지 않는다. 같은 이름으로 다시 만들 수 있고, 실패한 행은 목록에 남아 사유를
  보인다. 화면은 그 행에 «다시 만들기»(같은 이름으로 새로) 와 «버리기» 를 둔다.
- DB 스키마는 **v21** 이다.
