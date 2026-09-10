# 게이트 최적화 요청 다섯 — 작업 명세 초안 (M5j · v0.2, 2026-09-10)

> 출처: 다른 머신(맥미니 10코어·24GB 에서 Flutter 앱 게이트를 rcm 으로 돌리는 팀)이 2026-09-10 하루 동안 게이트 구조를
> 실측하며 보낸 요청 다섯. 번호는 **그쪽이 잰 효과 순**이다. 이 문서는 그 요청을 rcm 의 코드(dev `8190401`, M5i 전부 머지)에
> 대고 파악한 결과와 설계다 — **오너 승인 전이다.** v0.1 을 Codex 크로스리뷰에 걸었고
> (`docs/reviews/2026-09-10-codex-gate-optimization-design.md`), 초안이 틀렸던 곳 13개(§6)를 고쳐 v0.2 로 만들었다. 가장 큰 변화:
> G3 의 메모리 admission 은 **PLAN 결정 42 에 반해 뺐고**, G5 의 권한 모델은 잡당 열 하나가 아니라 **제출 참여자별 capability**
> 로, G2 는 「고정 슬롯」이 아니라 **잡별 워크스페이스 + 별도 캐시 세대**로 다시 썼다.
>
> 바꾸지 않는 것: 런타임 의존성 0 · 스키마 v1(키 추가만) · 순수 계층은 I/O 도 시계도 안 본다 · **서버는 임의 출력을 파싱하지
> 않는다**(마커만 믿는다) · fail-open 금지 · 옛 클라이언트(0.2.x)는 계속 붙는다 · 상태·오류·로그에 경로·비밀을 싣지 않는다.

## 0. 한 줄 요약

| # | 요청 | 지금 코드의 사실 | 방향(v0.2) | 크기 | PR |
|---|---|---|---|---|---|
| G1 | 끝난 잡의 스텝별 시간이 사라진다 | 마커는 `events` 표에 영구 저장(잡 행과 같은 트랜잭션에서 지워진다 — 「마커만 지워진 잡」은 없다). `progress_for_job()` 은 순수. `GET /jobs/<id>` 가 종료 잡에 `progress` 를 안 붙일 뿐 | 종료 잡에 **`step_timeline` 객체 하나**(마커 조회 실패면 `null` + `step_timeline_error_code`). `rcm wait` JSON 에도 실리는 것을 정식 계약으로. 재구성한 `failed_step` 은 싣지 않는다 | 작음 | 2 |
| G2 | 워크스페이스가 잡마다 새 clone 이라 빌드 캐시가 버려진다 | `prepare_git_ref`: `rmtree` → 미러에서 clone → checkout. janitor 인벤토리는 **숫자 이름 디렉터리만** 읽는다(`_scan_ids`) — 이름 있는 슬롯은 고아로도 안 잡히고 **회계에서 통째로 빠진다** | 고정 슬롯이 아니라 **잡별 워크스페이스 + 별도 캐시 세대**(`cache/<preset>/…/generations/`) · `cache_paths` 복원 → 성공 뒤 staging → rename promote · 키는 선언된 파일 해시만 · 회계에 `kind=cache|cache_staging` 추가 — **별도 명세(M5k)** | 큼 | 5(별도) |
| G3 | 잡이 한 번에 하나라 큐가 밀린다 | `admission = "load"` 는 잡을 **claim 하기 직전 한 번** CPU 창을 본다 — 구간 락이 아니다. `concurrency_group` 을 빼면 서버 쪽 상호배제는 없어진다. 메모리 게이트는 **PLAN 결정 42 가 배제**(macOS·Linux 의 `used` 정의가 다르다) | ① `lanes = 2` + `admission = "load"` + `concurrency_group` 제거는 **게이트 스크립트에 heavy 구간의 OS 락이 실제로 있을 때만** 운영 실험으로. ② 메모리 admission **기각**. ③ heavy 구간 선언은 **보류**(stdout 마커는 버퍼링될 수 있어 서버 승인 뒤 진입하는 lease 가 필요하다) | ① 문서만 | 3 |
| G4 | 워커 환경에서 도구를 못 찾으면 조용히 다른 걸 쓴다 | 잡 환경은 `runner.build_env()` — `env_passthrough` 허용목록 → 프리셋 `env` → RCM/input. 도구 검사는 `git` 하나. 원격 claim 문서에 `requires` 가 없고 원격 finish 는 문자열 `summary` 만 받는다 | 프리셋 `requires = ["fvm", "gitleaks"]` → **`build_env()` 의 최종 환경**에서 찾고(PATH 없으면 빈 PATH 로) 없으면 `failed` · `summary_code = "tool_missing"` · `summary_args = {"tool": "fvm"}` **까지만**(PATH·경로는 상태·로그에 안 싣는다) · 라벨·대장 행 없음 · 원격은 claim 에 `requires`, finish 에 구조화된 `summary_code/args` · `rcm check` 행 이름은 `local preset tools` | 작음~중간 | 1 |
| G5 | 취소가 «내 잡인가» 를 서버가 안 가린다 | `cancel()`: 토큰 이름 == 요청자 이름 · joiner · admin. joiner 표 PK 는 `(job_id, name)` 이라 같은 토큰의 세션들을 못 가른다. **Ctrl-C 는 요청자에게 detach 이고 잡을 취소하지 않는다**(합류자만 자기 합류를 뺀다) | **제출 참여자별 capability 표**(v17): `POST /jobs` 시도마다 `submission_id` + 서로 다른 비밀(해시만 저장) · 요청자 = `cancel_job`, 합류자 = `leave_submission` · 강제 모드에서는 capability 또는 admin 만(비-admin `--force` 없음) · Ctrl-C 계약 유지 · 서버 키 기본 off · 켜면 health/check 가 유효 최소 클라 버전을 알린다 | 중간 | 4 |

이미 해결된 것(M5i, v0.2.6): 클라이언트가 서버 버전을 못 따라오던 문제 — 서버가 자기 wheel 을 `/client/…whl` 로 준다(#90),
main 머지가 태그·릴리스를 만든다(#85). 그쪽 래퍼는 GitHub 없이 서버에서 받는다(`examples/session/update-client.sh`).

## 1. 우선순위

1. **G4** — 지금 물고 있다(`.fvmrc` 3.47.3 로 올리는 순간 옛 SDK 로 초록). 그 전 **우회**: 프리셋 `[presets.env]` 에
   `PATH = "<fvm 경로>:/opt/homebrew/bin:/usr/bin:/bin"` — 지금 설정으로 된다.
2. **G1** — 작고 즉시 이득. 나머지 최적화를 판단할 눈.
3. **G3 ①** — 문서·실험(코드 없음). 두 레인 효과는 G1 의 스텝 시간과 `concurrent_at_start` 로 잰다.
4. **G5** — capability 모델. 옵트인.
5. **G2** — M5k 로.

## 2. 항목별

### G1 — 종료 잡의 `step_timeline` (P1 · 수정해서 채택)

**사실.** `worker.py` 가 마커마다 `store.add_marker()` 를 남기고 `/api/status` 는 활성 잡만 `progress_for_job()` 을 돌린다.
`Store.markers()` 는 행이 없으면 빈 목록이고, 메타데이터 만료(`delete_old_jobs`)는 잡 행과 이벤트를 같은 트랜잭션에서 지운다 —
「조회는 되는데 마커만 지워진 잡」은 없다(지워지면 404). `progress_for_job()` 은 다음 `step` 이 오면 앞 스텝을 `ok=True` 로
닫고 성공 종료의 마지막 열린 스텝도 `True` 로 추론한다(실패를 추론하지는 않는다). `rcm wait` 는 `GET /jobs/<id>` 문서를 그대로
복사해 출력한다 — 종료 문서에 키를 넣으면 wait JSON 에도 들어간다.

**설계.**
- 종료 잡의 `GET /jobs/<id>` 에 객체 하나 `step_timeline: {timing: "as_received", steps_total, steps_total_partial, steps: [{index, name, started_at, ended_at, seconds, ok}]}`.
  계산은 `progress_for_job(job, store.markers(id), now=finished_at)`. 마커 조회 성공·마커 없음 → `steps: []`, `steps_total: null`.
  DB 조회 실패 → `step_timeline: null` + `step_timeline_error_code`(fail-open 금지 — 빈 목록으로 뭉개지 않는다). 시작 전에
  실패해 `started_at` 이 없는 잡 → 빈 타임라인.
- **재구성한 `failed_step` 은 싣지 않는다.** 저장된 `failed_step`·`last_step`·`job_failures` 는 그대로(M5h · v16 불변식).
- `rcm wait` 의 최종 JSON 에 `step_timeline` 이 들어가는 것을 **정식 계약**으로 문서에 적는다(키 추가 · 옛 소비자 무해).
- `_with_failures()` 의 `failures[].step` 판정에 타임라인의 **모든 스텝 이름**을 넘긴다(지금은 `failed_step`·`last_step` 둘만).
- 「프리셋별 스텝 중앙값」은 보류.

**테스트.** 마커 픽스처로 끝난 잡 → `steps[].seconds` 가 활성 때와 같다 · 마커 없음 → `[]` · DB 오류(monkeypatch) → `null` +
코드 · 시작 전 실패 → 빈 타임라인 · `failed_step` 불변 · `rcm wait --json` 에 키 존재 · `schema_version` 불변.

### G2 — 캐시 세대 (P2 · **별도 명세 M5k**)

**사실.** `prepare_git_ref()` 는 `rmtree` → 미러에서 `git clone --no-checkout`(하드링크) → checkout. `janitor._scan_ids()` 는
숫자 이름만 읽으므로 `workspaces/gate-a` 같은 슬롯은 총량·고아·예산·바닥·dry-run 어디에도 안 나온다(숫자 이름은 잡 ID 와
충돌). `VolumeItem`·`PurgeItem`·`apply()` 는 전부 `job_id: int` 와 종료 잡 상태에 묶여 있다. 원격 워커 워크스페이스는 자체 7일
mtime 청소뿐이고 서버 회계 밖이다.

**v0.2 의 방향(Codex 대안 채택).** 「고정 실행 슬롯」이 아니라 **잡별 워크스페이스 + 별도 캐시 세대**.
- 실행은 계속 `workspaces/<job_id>` — 실패 워크스페이스 증거 규칙·janitor 규칙 보존.
- 캐시는 `cache/<preset>/<pool-or-worker>/<key>/generations/<n>/` 별도 종류. 시작 때 허용된 `cache_paths` 만 워크스페이스로
  복원, 성공 뒤 staging 에 복사 → rename 으로 promote. 실패·취소는 promote 안 함.
- 키는 명시적 `cache_namespace` + `cache_key_files` 의 내용 해시. **도구 출력(`fvm --version`)은 파싱하지 않는다.**
- 경로는 상대만, `..`·절대경로·`.git`·워크스페이스 밖 symlink 거부.
- 회계 모델에 `kind=workspace|snapshot|cache|cache_staging`, 문자열 ID, `active/idle`, `last_used_at`. idle 캐시는 증거가
  아니므로 예산·바닥에서 회수 가능, 실행 중 lease 와 staging 은 보호. charged/shared/reclaimable 같은 눈금 · 오프라인 dry-run
  도 같은 planner 로 실제 `cache/` 루트 포함.
- promote 직전·직후 크래시에서 어느 디렉터리가 활성·회수 가능·고아인지 명세.
- 원격 워커 지원은 **별도 결정**(지원하면 워커 자체 회계·GC·보고가 필요).

### G3 — 두 레인 실험 (P1 · ① 문서 · ② 기각 · ③ 보류)

**사실.** admission 은 claim 직전 한 번 판단한다(`server.py` claim 경로) — 이미 시작한 두 잡이 둘 다 heavy 에 들어가는 것은
못 막는다. `concurrency_group` 을 빼면 서버 쪽 상호배제가 사라진다. PLAN 결정 42 가 메모리 게이트를 배제했고, macOS
`used_bytes` 는 compressor 를 이미 포함한다(초안의 계산은 이중 계산이었다).

**설계.**
- ① `docs/configuration.md` 「Admission」에 실험 배치를 적는다: **게이트 스크립트가 heavy 구간을 OS 락(예: `flock`)으로
  직렬화하고 있을 때만** `lanes = 2` + `admission = "load"` + 게이트 프리셋의 `concurrency_group` 제거. 그 락이 없으면
  `concurrency_group` 을 유지한다. 「CPU admission 은 잡 시작 제어이지 구간 락이 아니다」를 문서에 명시(fail-open 금지).
  효과는 G1 스텝 시간과 `concurrent_at_start` 로 잰다.
- ② 메모리 admission — **기각**(결정 42).
- ③ heavy 구간 선언 — **보류**. 나중에 하려면 stdout 마커만으로는 안 된다(버퍼링으로 heavy 명령이 먼저 시작될 수 있다).
  서버의 승인 응답을 받은 뒤 진입하는 lease/helper 가 필요하다.

### G4 — 프리셋 `requires` (P0 · 수정해서 채택)

**사실.** 잡 환경은 `runner.build_env()`(`env_passthrough` 허용목록 → 프리셋 `env` → RCM/input) — 서버의 `os.environ` 전체가
아니다. 도구 검사는 `_validate_server` 의 `git` 하나. 원격 claim 의 preset 문서는 `argv/env/env_passthrough` 뿐이고 원격
finish 는 문자열 `summary` 를 코드 없는 워커 문장으로 처리한다. `summary_args` 는 `/api/status.recent` 에도 실린다(공개).

**설계.**
- 프리셋 키 `requires = [...]`: 이름 또는 절대경로만. 상대경로·빈 값·중복은 설정 오류.
- 잡 시작 직전, `build_env()` 가 만든 **최종 환경**에서 `shutil.which(name, path=env.get("PATH", ""))` — PATH 가 없으면 빈
  PATH 를 명시해 검사 프로세스의 PATH 로 물러나지 않는다. 하나라도 없으면 프로세스를 띄우지 않고 `failed` ·
  `summary_code = "tool_missing"` · `summary_args = {"tool": "fvm"}` — **PATH 와 도구 경로는 상태·오류·로그 어디에도 안
  싣는다**(PLAN 「보안」). 잡 로그에는 `[rcm] required tool fvm: missing` / `[rcm] required tools: fvm ok · gitleaks ok`
  처럼 이름과 판정만.
- `failed_step = None`, `last_step = None`, 대장 행 0 — `tool_missing` 은 서버가 만든 outcome 코드이지 스크립트의 선언이
  아니다(M5h · v16 불변식 유지).
- 원격 워커: claim 응답의 preset 문서에 `requires` 추가, finish 에 구조화된 `summary_code`/`summary_args`(또는 preflight
  failure) 추가 — 문자열 summary 로 우회하지 않는다. 옛 워커는 키를 무시한다(추가만).
- `rcm check --config` 에 `local preset tools` 행: 셸 환경 검사이지 launchd 서비스의 정본 검사가 아님을 행 이름과 문서가
  말한다. 정본은 잡 시작 전 검사다.

**테스트.** 없는 이름 → 프로세스 미실행 · `failed` · `tool_missing` · args 에 `tool` 만 · 로그에 PATH 없음 · 라벨·대장 없음 ·
있는 이름·절대경로 → 정상 · 프리셋 `env.PATH` 로 바꾼 경로에서 찾는다 · PATH 없는 환경 → 빈 PATH 로 검사 · 원격 claim 에
`requires` · 원격 finish 코드 보존 · 설정 오류 셋.

### G5 — 제출 참여자별 capability (P2 · 보류 → v0.2 모델로 채택 요청)

**사실.** `App.cancel()`: `token.admin or job.requester.name == token.name or joiner`. `joiners` PK `(job_id, name)` — 같은
토큰 이름의 세션들은 합쳐진다. `rcm run`/`wait` 의 Ctrl-C 는 요청자에게 **detach**(잡을 취소하지 않는다), 합류자는 자기
합류만 뺀다(PLAN 계약).

**설계(v17).**
- 표 `submissions(job_id, submission_id, role, capability_hash, created_at)` — `POST /jobs` 시도마다(합류 포함) `submission_id`
  와 서로 다른 비밀을 한 번 발급, 응답에 `submission: {id, cancel_token}`. 서버는 해시만 저장. 메타데이터 삭제 때 같은
  트랜잭션에서 지운다.
- 요청자 capability = `cancel_job`, 합류자 = `leave_submission`. 같은 토큰 이름의 세션들이 서로 다른 참여자로 남는다.
- 서버 키 `[server] cancel_requires_submission_token = false`(기본 · 0.2.x 호환). `true` 면 capability 또는 admin 만 취소 —
  **비-admin `--force` 없음**(옛 공유 토큰 권한을 되살리지 않는다). 켜면 `/api/health.min_client_version` 과 `rcm check` 가
  **설정의 유효 최소 클라 버전**을 알린다.
- Ctrl-C 계약 유지: 요청자는 detach, 합류자는 자기 participation 만 제거. 요청자의 취소는 명시적 `rcm cancel` 만.
- 새 클라이언트는 capability 를 권한 0600 의 로컬 상태(`~/.local/state/rcm/submissions.json` 같은)에 보관해 나중의
  `rcm cancel N` 이 쓰고, 명시적 입력 경로(`--cancel-token`)도 둔다. 상태·로그·오류·URL 에 절대 싣지 않는다 — 취소 권한을
  가진 **비밀**로 취급한다.
- 웹 취소 버튼: 키가 켜지면 admin 토큰이 아닌 한 비활성 + 이유 문구.
- v16 → v17 은 M5i 결정 74 의 경계 백업을 탄다 — 완료 기준에 「v16 백업 생성·검증 실패 시 열 추가 없음」을 넣는다.

## 3. PR 순서

| PR | 내용 | 완료 기준 |
|---|---|---|
| 1 | G4 `requires`(로컬·원격) + `local preset tools` 행 + 문서 | 없으면 프로세스 미실행·`tool_missing`·경로 노출 0 · 원격 코드 보존 |
| 2 | G1 `step_timeline` + `failures[].step` 전체 이름 | 픽스처 넷 · wait JSON 계약 · `schema_version` 불변 |
| 3 | G3 ① 문서(configuration.md 「Admission」 실험 배치 · 구간 락 아님 명시) | 문서 잠금 |
| 4 | G5 capability 표(v17) · 서버 키 · CLI 상태 파일 · health/check 안내 | 키 off/on · 참여자별 · Ctrl-C 계약 · v16 백업 경계 |
| 5 | G2 명세(M5k) → 오너 결정 → 구현 | 별도 |

릴리스: v0.2.6 은 M5i 로 끊는다(#102). M5j 는 v0.2.7.

## 4. 결정(제안 · 84~88)

| # | 결정 | 내용 |
|---|---|---|
| 84 | 종료 잡 타임라인 | `GET /jobs/<id>` 에 `step_timeline` 객체. 없으면 `[]`, 조회 실패면 `null` + 코드. 재구성 라벨은 안 싣는다. `rcm wait` JSON 에 실리는 것이 계약 |
| 85 | `requires` | `build_env()` 최종 환경에서 시작 전 검사. 없으면 `failed` + `tool_missing`, args 는 이름만, 라벨·대장 없음. 원격은 claim/finish 에 구조화 키 |
| 86 | 두 레인 | 메모리 admission 은 결정 42 대로 안 한다. 두 레인은 heavy 구간의 OS 락이 있는 프리셋에서만 실험. 구간 선언은 lease 없이는 안 한다(보류) |
| 87 | 취소 범위 | 제출 참여자별 capability(v17). 강제는 서버 키로 옵트인, 켜면 capability·admin 만. Ctrl-C 는 detach 계약 유지 |
| 88 | 캐시 | 고정 슬롯이 아니라 잡별 워크스페이스 + 캐시 세대(M5k). 도구 출력 파싱 없음. 회계 종류 추가 |

## 5. 테스트 배치

- `tests/test_requires.py` · `tests/test_step_timeline.py` · `tests/test_cancel_capability.py` · `tests/test_docs_m5j.py`.
- mutcheck: ① `requires` 검사를 건너뛰면 빨강 ② 강제 모드에서 capability 검사 제거 빨강 ③ `step_timeline` 의 DB 오류를 `[]`
  로 뭉개면 빨강.

## 6. M5i 와의 겹침·충돌 (v0.2 · Codex 가 더한 것 포함)

| M5j | 관련 | 관계 | 판단 |
|---|---|---|---|
| G1 | M5h 결정 63·64 · v16(#87) | 타임라인은 시간·`ok` 만. 재구성 `failed_step` 은 안 싣는다 | 조건부 충돌 없음 |
| G1 | `rcm wait` JSON(dolomood 래퍼) | 키 하나 추가 — 계약으로 문서화 | 없음 |
| G1 | `_with_failures()` | 스텝 이름 판정 범위 확장 | 추가 |
| G2 | PR 3(#88) 회계 · janitor `_scan_ids` 숫자만 | 슬롯은 고아가 아니라 **누락** — 그래서 별도 종류 `cache` | M5k |
| G2 | PR 2(#87) 오프라인 dry-run | 같은 planner 가 `cache/` 루트를 포함해야 한다 | M5k |
| G2 | 원격 워커 회계(7일 mtime 청소뿐) | 지원 여부 결정 | M5k |
| G3 ① | `concurrency_group` ETA · admission 이 claim 직전 1회 | 구간 락은 스크립트 몫 — 문서에 명시 | 문서 |
| G3 ② | **PLAN 결정 42** | 직접 충돌 | 기각 |
| G4 | M5h 대장 불변식 | `tool_missing` 은 라벨 없음 | 유지 |
| G4 | PLAN 「보안」 경로 노출 금지 · `/api/status.recent` 의 `summary_args` | args 에 이름만 | 반영 |
| G4 | 원격 워커 claim/finish 프로토콜(M5b-2) | 키 추가만 | 추가 |
| G5 | PLAN Ctrl-C detach 계약 | 유지 | 반영 |
| G5 | joiner PK `(job_id, name)` | 참여자별 표로 | v17 |
| G5 | PR 2(#87) 백업 경계 | v17 완료 기준에 포함 | 반영 |
| G5 | PR 6(#90) `min_client_version` | 강제 모드에서 유효 최소 버전 안내 | 추가 |
| 전부 | v0.2.6(#102) | M5j PR 은 v0.2.6 태그 뒤 | 순서 |

## 7. 오너가 정할 것

1. §3 순서(G4 → G1 → G3 문서 → G5 → M5k)로 가는가.
2. G5 를 v0.2 모델(참여자별 capability · 비-admin `--force` 없음 · 기본 off)로 가는가.
3. G2(M5k)의 결정: 원격 워커 지원 여부 · `tree` 모드 포함 여부 · idle 캐시의 회수 순서(예산·바닥에서 어디에 서나).

## 8. 리뷰 반영

`docs/reviews/2026-09-10-codex-gate-optimization-design.md` — 판정표 · 빠진 충돌 13 · 규칙 위반 표 · 대안. v0.1 → v0.2 의 변화는
이 문서 머리에.
