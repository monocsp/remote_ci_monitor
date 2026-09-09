# Codex 크로스리뷰 — M5g 설계 (2026-09-09)

> 대상: `docs/m5g-workplan.md`(PR #68) · 오너 승인으로 실행 · `codex exec --sandbox read-only`
> **1차는 중단**(모델 접근 404), **2차가 완주**. 판정은 **「조건부 승인」** — 방향은 맞지만 구현 전에
> 고쳐야 할 P0 계약 구멍 넷. 전부 반영했다.

## 0. 두 번 돌린 이유

1차(`gpt-5.5`, high)는 레포를 다 읽고 항목별 판단을 내리던 중 끊겼다.

```
ERROR: unexpected status 404 Not Found:
       The model `gpt-5.5` does not exist or you do not have access to it.
```

다른 이름(`gpt-5`·`gpt-5.1`·`gpt-5.1-codex`·`gpt-5-codex`·`gpt-5.5-codex`·`codex-mini-latest`)은
CLI 가 「ChatGPT 계정 미지원」 400 으로 먼저 막았다. 154,583 토큰을 쓰고 열 번째 turn 에서 끊겼다.
오너가 Codex 를 업데이트한 뒤(**`codex-cli 0.153.4` · `gpt-5.6-sol`**) 2차를 같은 프롬프트로
돌렸다(reasoning effort high, 421,312 토큰). 1차의 관찰 넷은 2차 프롬프트에 「이미 반영했다」로
알려 주고 반영이 옳은지만 확인받았다.

## 1. 준 프롬프트 (2차 · 전문)

```
이 레포(remote_ci_monitor)는 빌드 머신 한 대의 로컬 잡 서버다. Python 3.11+, 런타임 의존성 0,
표준 라이브러리만. `PLAN.md` 가 정본이고 `AGENTS.md` 에 집안 규칙이 있다.

`docs/m5g-workplan.md` 는 아직 구현 전인 명세다(PR 1). 이것을 **설계 크로스리뷰** 해 달라.
읽을 것: docs/m5g-workplan.md · PLAN.md(「보존」·「fail-open 금지」·결정 표 51~60) ·
src/remote_ci_monitor/core/retention.py · janitor.py · config.py(ServerSection 과 검증) ·
server.py(App.artifact_storage · health · 라우트) · worker.py 와 remote_worker.py 의 워크스페이스 정리 ·
docs/m5f-workplan.md(같은 형식의 앞선 명세) · docs/m5e-workplan.md §7~§10(산출물 보존의 선례).

배경 실측(다시 재지 마라, 명세 §2 에 있다): 운영 데이터 디렉터리의 98%가 workspaces/ 이고
28.8 GiB · 파일 38만 개다. 한 번 훑는 데 6.65초. 실패 잡 하나가 로그 50 KB · 워크스페이스 720 MB 를
남긴다. 하루 비성공 잡 50개, 여유 628 GiB.

이 명세가 정한 것: ① 증거(로그 14/30일)와 부피(워크스페이스 + 그 잡의 tree.tar.gz, 새 키
workspace_retention_days = 1)를 다른 시계로 재운다 ② 날짜와 무관한 workspace_storage_max_bytes
(100 GiB)와 min_free_bytes(10 GiB)가 오래된 종료 잡부터 부피를 회수한다 ③ 회계를
server.job_storage · /api/health.storage · rcm check · 웹에 싣고 rcm gc [--dry-run] 을 만든다
④ 프리셋 권고와 artifacts_on = "failure".

특히 다음을 따져 달라. 동의하면 짧게, 반대하면 근거와 대안을 구체적으로.
 1. 안전성 — 「증거는 안 지운다」와 「못 재면 예산·바닥을 건너뛴다」가 실제로 fail-closed 인가.
 2. 「한 회차에 한 번만 계획한다」가 바닥 규칙을 무력하게 만드는 경우가 있나.
 3. 후보를 DB 가 아니라 workspaces/ 스캔에서 얻는 것 — 경합·TOCTOU.
 4. 측정 캐시 · st_blocks × 512 · 하드링크 이중 계수.
 5. tree.tar.gz 를 부피 시계로 옮기는 것 — 종료 잡에서 정말 안 읽나.
 6. DB 마이그레이션 없이 가능한가.
 7. POST /gc 와 janitor 가 계획 함수를 공유하는 설계 · 락 · dry-run 의 정직성.
 8. 기본값 셋과 「업그레이드만으로 27 GB 가 사라지는 것」의 처리.
 9. 스키마 v1 에 키만 더하는 것과 이름(job_storage · workspace_*).
10. 테스트 배치와 mutcheck 2종이 안전 성질을 실제로 잠그나.
11. 명세가 틀린 사실(코드와 어긋나는 file:line, 잘못된 동작 설명)을 말하는 곳.

출력은 한국어로. 번호별로 「동의/반대/조건부」와 근거를 적고, 마지막에 「명세를 고쳐야 하는 것」을
우선순위 순으로 정리해라.

--- (2차에 덧붙인 것) 1차가 끊겨 관찰 넷(D·E·F·G)은 이미 반영했으니 다시 지적하지 말고 반영이
옳은지만 확인해라. 그 사이 기본값이 2일 → 1일로 바뀌었고 결정 60(설정 표면)이 추가됐다.
1차에서 답을 못 받은 2 · 7 · 10 을 특히 자세히 봐 달라.
```

## 2. 1차 관찰(D·E·F·G)에 대한 2차의 확인

| | 1차 관찰 | 2차 판정 |
|---|---|---|
| D | 두 스캔은 독립 — 원격 잡의 서버 쪽 입력 tar | **올바르게 반영됨** |
| E | `budget_unreachable` — 못 이룰 목표에 증거를 태우지 않는다 | 의도는 맞지만 **순수 함수 계약에 안 들어가 있었다** → 반환형에 넣었다 |
| F | 압박 삭제와 나이 삭제의 용어 분리 | **올바르게 반영됨** |
| G | 측정 캐시의 불변 조건 | 워크스페이스만 보면 맞지만 **tar 까지 합친 캐시로는 불완전** → 캐시를 갈랐다 |

## 3. 항목별 판정과 반영

### 1. 조건부 — 「fail-closed」라는 말을 좁게 써라

증거를 안 지운다는 뜻에서는 fail-closed 가 맞다. 그러나 **측정이 계속 실패하면 디스크는 계속 찬다** —
가용성 관점의 fail-closed 가 아니다. 그리고 `bytes = None` 의 범위는 종료 후보가 아니라
**인벤토리 전체**(활성·고아·tar-only 포함)여야 하고, 루트 스캔 실패·DB 실패를 「0개」나 「고아」로
바꾸면 안 되며, 파생값(`over_budget_bytes`·`short_free_bytes`·`freed_bytes`)이 무조건 `int` 인 것은
PLAN 의 「모르는 숫자는 null」과 충돌한다. `budget_unreachable` 이 JSON 에만 있고 순수 반환형에 없다.

→ **반영**: §4.3 을 다시 썼다. `VolumeItem`(활성·고아·tar-only 포함) · `PurgePlan` 에
`volume_bytes`·`evictable_bytes`·`non_evictable_bytes`·`budget_unreachable`·`inventory_error` ·
파생값 전부 `| None` · `known_freed_bytes`/`unknown_freed_count` 분리 ·
`projected_short_free_bytes` 로 개명. §4.4 에 실패 종류별 처리 표. §12 에 「디스크는 계속 찬다」.

### 2. 조건부 — 바닥은 불변식이 아니라 회수 시도다

한 회차 루프를 없앤 것은 맞다. 그러나 그 보호는 **한 회차짜리**다: `df` 가 계속 안 움직이면 여러
회차에 걸쳐 결국 전부 지운다. 부족분이 evictable 전체보다 크면 **첫 계획이 이미 전부를 고른다** —
「한 번만 계획한다」는 삭제량 상한이 아니다. 그리고 「evictable 이 없을 때만 FAIL」은 **삭제가 효과가
없었던 상황을 warn 으로 숨긴다.**

→ **반영**: **결정 62 무진전 latch** — 바닥 규칙으로 지운 회차 끝에 `free_after - free_before` 가
지운 바이트의 절반에 못 미치면 latch 를 세우고 그 뒤 자동 sweep 의 **바닥 규칙만** 멈춘다.
`rcm gc` 나 재시작으로 풀린다. `rcm check` 에 FAIL 줄을 셋으로 늘렸다(§5.3). 문서에
「`min_free_bytes` 는 유지되는 불변식이 아니다」를 적었다.

### 3. 조건부 동의 — union 과 실패 의미를 잠가라

디렉터리 스캔은 맞고 경합도 대체로 안전하다(종료 상태는 활성으로 안 돌아간다). 다만 두 스캔의 union
규칙, 「DB 가 행이 없다고 말했을 때만 고아」, `scandir` 실패는 빈 디렉터리가 아니라는 것, 네 모양
(workspace-only · tar-only · 둘 다 · 고아)의 회계, **삭제 직전 상태 재조회**가 명세에 없다.

→ **반영**: §4.4 의 union 표와 실패 표, §4.6 의 「삭제 직전에 `store.get_job` 으로 재확인」.

### 4. 반대 — 캐시 키가 tar 을 표현 못 하고 하드링크는 「보수적」이 아니다

`bytes` 가 「워크스페이스 + 다른 경로의 tar」인데 signature 는 워크스페이스 최상위 mtime 하나뿐이다.
tar 생성·교체·삭제가 signature 를 안 바꾼다. 그리고 하드링크 이중 계수는 임의로 커질 수 있고
**지워도 실제 free 가 전혀 안 늘 수 있다** — `du` 도 한 순회 안에서는 중복 계수하지 않는다.

→ **반영**: 캐시를 갈랐다(tar 은 매번 `lstat`, 캐시 없음) · `MEASURE_MAX_AGE = 24시간` 추가 ·
`measured_at` 은 **재귀 측정 시각**이라고 못 박음 · **`charged_bytes` 와 `reclaimable_bytes` 분리**
(바닥의 예상 회수량은 `st_nlink > 1` 블록을 안 센다).

### 5. 조건부 동의 — 재현 가능성은 잃는다

종료 잡 reader 가 없다는 좁은 명제는 맞다(`worker.py:387` · `remote_workers.py:494`, 둘 다 활성
잡). 그러나 tar 을 지우면 **그 잡을 그대로 다시 돌릴 입력이 서버에 없다.** `tree_hash`·`base_sha` 는
신원이지 내용이 아니고 manifest 도 blob GC 뒤엔 못 되살린다.

→ **반영**: §7 에 「하루 뒤 정확한 재현은 보장하지 않는다 · 감사 기록은 로그와 잡 행이지 입력 트리가
아니다 · 정확한 재현이 필요하면 `artifacts` 로 가져간다」.

### 6. 조건부 동의 — 마이그레이션은 없어도 되지만 검증이 틀렸다

marker 없이 멱등으로 가는 것은 맞다. 그러나 **성공 잡의 tar 도 새 시계를 타므로**
`workspace_retention_days <= retention_days_failure` 만으로는 부족하다 —
`min(retention_days_success, retention_days_failure)` 여야 한다. 그리고 `WorkspaceInfo` 에
`created_at` 이 없는데 판정이 `finished_at or created_at` 을 쓴다(구현 불가).

→ **반영**: §4.2 검증을 `min(...)` 으로, `VolumeItem` 에 `created_at` 추가.

### 7. 반대 — 락과 dry-run 계약이 부족하다

지금 `Janitor._lock` 은 `last_sweep_at`·카운터용 **상태 락**이지 sweep 직렬화 락이 아니다.
**「dry-run 이 보여준 것과 실제가 다를 수 없다」는 틀린 문장이다** — 두 요청 사이에 잡이 끝나고
디렉터리가 생긴다. 실제 gc 가 계획만 돌려주면 **삭제 실패를 회수처럼** 보고하게 된다. 그리고 일반
클라이언트 타임아웃은 15초(`client.py:448`)인데 스캔이 140초가 될 수 있다 — 「CLI 는 타임아웃, 서버는
계속 삭제」가 최악의 UX 다.

→ **반영**: §4.6 에 `_operation_lock` 의 **소유자(Janitor)·범위(계획+삭제+사후측정)·순서(App 은 락을
안 잡는다 · 상태 락을 쥔 채 I/O 안 한다)**. §5.5 의 응답을 `planned`/`deleted`/`failed`/
`storage_before`/`storage_after` 로 분리(dry-run 이면 `storage_after` 는 null). 문제의 문장 삭제.
`--timeout`(기본 600초)과 **종료 코드 3(모른다)**. 결정 57 갱신.

### 8. 반대 — 업그레이드 게이트가 실행 불가능하다

**완료 기준 8 이 성립하지 않는다.** `POST /gc` 는 새 서버에만 있는데 새 서버는 뜨자마자 sweep 한다.
시작 전에는 dry-run 할 수 없고, 시작 뒤에 하면 이미 지워졌다. CHANGELOG 는 자동 삭제를 막지 못한다.

→ **반영**: **결정 61** — `rcm gc --dry-run --config <server.toml>` 이 **서버 없이** 돈다
(`rcm check --config` 의 선례). 절차는 `git pull --ff-only`(서비스는 아직 옛 코드) → 오프라인
dry-run → 재시작. ⛔ 코덱스의 대안(「기존 설정에 키가 없으면 압박 삭제를 안 켠다」)은 결정 61 에
병기했다 — 오너가 그쪽을 원하면 바꾼다.

### 9. 조건부 동의 — 산식이 안 맞았다

`schema_version = 1` 유지와 `job_storage` 라는 이름은 적절하다. 그러나 「`workspace_bytes -
evictable_bytes` 가 활성 몫」은 **tar 때문에 거짓**이고, 예산의 분자인 `volume_bytes` 가 없고,
`budget_unreachable` 의 원인을 설명할 `non_evictable_bytes` 가 없다.

→ **반영**: §5.1 에 `volume_bytes`·`non_evictable_bytes`·`orphan_bytes`·`log_bytes`·`no_progress`
추가하고 산식 세 줄을 명시했다.

### 10. 반대 — 테스트가 핵심 성질을 다 못 잠근다

⑬은 순수 필터만, ⑭는 `None → 0` 만 잡는다. `budget_unreachable` 제거 변이, 스캔·DB·`disk_usage`
실패, 증거 불변 통합, union 네 모양, 성공 잡 tar 경계, 부분 삭제 재시도, 캐시 무효화, 실제 gc 의
실패 응답, 락 경합 셋, 무진전 다음 회차 정책, 15초 넘는 gc, 잘못된 본문 타입, 삭제 직전 재확인이
빠져 있다.

→ **반영**: §8 을 다시 썼다(`test_janitor_evidence.py` 신설 포함). §9 에 **mutcheck ⑮
`retention-budget-unreachable`** 추가 — **12 → 15종**.

### 11. 반대 — 틀린 사실 목록

| 틀린 곳 | 고침 |
|---|---|
| `WorkspaceInfo` 에 `created_at` 이 없는데 판정이 쓴다 | `VolumeItem` 에 추가 |
| `PurgePlan` 에 `budget_unreachable` 없음 · 파생값 타입 | 반환형 재작성 |
| `workspace_bytes − evictable_bytes` = 활성 몫 | 산식 재정의(§5.1) |
| **「성공 잡의 워크스페이스라는 경우가 존재하지 않는다」** | 거짓 — 로컬·원격 둘 다 `rmtree(ignore_errors=True)` 라 삭제가 조용히 실패하거나 종료 직전 crash 로 남을 수 있다. 규칙이 **종료 상태 전부**를 후보로 삼는 것으로 고쳤다 |
| 검증이 failure 만 본다 | `min(success, failure)` |
| 「정상 상태 44 GB」 | 기본값 2일 때 수치. 1일이면 22 GB |
| PLAN 결정 58 「24시간 < 워크스페이스 2일」 | 둘 다 약 하루 |
| PLAN 결정 57 「보여준 것과 실제가 다를 수 없다」 | 삭제 |
| PLAN 결정 55 에 `budget_unreachable` 없음 | 반영 |
| 「DB v9 는 M5f 몫」 | 현재 `DB_VERSION = 8` 이고 M5f 의 claim 인덱스가 v8, 이미 머지됨 |
| `artifacts_on` 은 호출부 둘만 고치면 된다 | **원격에서 안 돈다** — claim payload 에 정책이 없다(`remote_workers.py:373` → `remote_worker.py:167`). §6 에 배선 넷을 적었다. ⚠️ **이걸 쫓다가 더 큰 것을 찾았다** — 정책이 없는 건 새 키만이 아니라 **글롭 자체**다. 실측으로 확인했다: 서버의 `preset_doc` 에 `artifacts` 가 없어서 `_policy_from_claim` 이 `None` 을 돌려주고, **원격 워커는 산출물을 하나도 안 모은다**(M5e 가 원격 풀에서 죽어 있다). 시험이 손으로 만든 payload 를 써서 못 잡았다. 명세 §13 D |

## 4. 우선순위 목록과 처리

| | 코덱스가 매긴 것 | 처리 |
|---|---|---|
| P0 1 | 업그레이드 안전 게이트 | 결정 61(오프라인 dry-run) · 완료 기준 8 재작성 |
| P0 2 | 순수 모델 완성 | §4.3 재작성 |
| P0 3 | gc 락·실행 응답·틀린 문장 | §4.6 · §5.5 · 결정 57 |
| P0 4 | `min(success, failure)` 검증 | §4.2 |
| P1 5 | 무진전 회차의 등급·정책 | 결정 62 latch · §5.3 FAIL 셋 |
| P1 6 | 캐시 분리·`measured_at`·수명·하드링크 | §4.4 |
| P1 7 | 테스트·mutcheck ⑮ | §8 · §9 |
| P1 8 | gc 타임아웃 | §5.5(`--timeout` · 종료 코드 3) |
| P2 9 | `volume_bytes`·`non_evictable_bytes` | §5.1 |
| P2 10 | PLAN·명세의 낡은 사실 | 전부 고침 |
| P2 11 | `artifacts_on` 원격 배선·판정 시점 | §6 |

**남은 ⛔**: 결정 61 의 대안(「기존 설정에 키가 없으면 압박 삭제를 안 켠다」)만 오너 확인 대기다.
