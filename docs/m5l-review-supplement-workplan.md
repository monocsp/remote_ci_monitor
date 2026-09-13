# 구현 리뷰 보완 — 작업 명세 (M5l · v0.1, 2026-09-13)

> 출처: M5i(게이트 재현 수정, 8 PR)와 M5j(게이트 최적화, 4 PR)의 **구현**을 PR 마다 다시 봤다 — Codex(`codex exec --sandbox read-only`,
> gpt-5.6-sol, 5건: #87 #88 #90 #109 #110)와, Codex 가 사용량 한도에 걸린 뒤 격리 에이전트 셋(11건). 리뷰 전문은
> `docs/reviews/2026-09-13-impl-reviews/pr-<N>.md`. 설계는 다시 묻지 않았다(명세와 앞선 설계 리뷰로 확정) — 「구현이 명세대로인가 ·
> 오류 경로가 fail-open 인가 · 경계(경로·비밀·권한·호환)를 지키는가 · 테스트가 잠갔는가」만 봤다.
>
> 결과: **P0 없음 · P1 19 · P2 다수**. 되돌려야 할 PR 은 없다. 열린 PR 넷(#106 #108 #109 #110)의 지적은 **그 브랜치에서 머지 전에**
> 고치고, 머지된 PR 의 지적은 새 브랜치(L1~L8)로 고친다. 지금 진행 중인 것과의 순서는 §3.

## 0. 판정표

| PR | 총평 | P1 | P2 | 한 줄 |
|---|---|---|---|---|
| #87 오프라인 dry-run 사본·백업·v16 | 보완 | 3 | 2 | 사본 마이그레이션의 `sqlite3.Error` 가 exit 3 이 아니라 트레이스백 · 임시 사본 삭제 실패를 무시하고 exit 0 · 거절 메시지가 없는 백업(`v<DB_VERSION>.bak`)을 가리킬 수 있음 |
| #88 회계 charged/reclaimable | 보완 | 3 | 1 | 측정 불명 항목을 지우고 `freed 0 B` 로 확정 표시 · 부분 삭제 실패 뒤 `storage_after`·캐시가 삭제 전 값 · floor 분기가 `error_code` 를 가림 |
| #90 서버가 자기 wheel | 보완 | 2 | 2 | 래퍼가 검증 뒤 공용 `$TMPDIR` 경로로 옮겨 설치(TOCTOU) · `RCM_TOKEN` 이 curl argv 에 노출 · health 503 이면 판정이 사라짐 · `/client/<name>.whl/` 별칭 |
| #109 프리셋 `requires` | 보완(머지 전) | 4 | — | PATH 의 상대 항목을 서버 cwd 로 해석(fail-open, 잡은 워크스페이스 cwd) · `requires=["/dir/"]` 처럼 basename 이 비면 공개 args 에 경로 · 선언한 절대경로가 로그에 · preflight 전 취소를 안 보고 `cancelling` → `failed` |
| #110 취소 capability | 보완(머지 전) | 3 | 5 | 상태 파일 조회가 같은 토큰의 다른 세션 `cancel_job` 을 집어 잡 전체 취소 · leave 비밀이 참여자에 묶이지 않고 소비가 원자적이지 않음 · 상태 파일 다중 프로세스 쓰기 손상 · 잡/합류와 capability 생성 비원자 · 200개 상한이 활성 잡 권한을 버림 · 모르는 role 이 `cancel_job` 승격 · stderr 에 절대경로 · 0.2.7 전 옛 클라 check 거짓 OK |
| #108 `step_timeline` | 보완(머지 전) | 1 | 5 | rebase 뒤 CHANGELOG 항목이 `[0.2.6]` 절 안에 · `sqlite3.Error` 만 잡아 깨진 payload 는 500 · `finished_at` 없을 때 시계 폴백 · 보고서의 stale 커밋 |
| #85 자동 태그 | 보완 | 1 | 4 | 첫 릴리스 뒤 버전을 안 올린 main 푸시가 전부 빨간 run(「무동작」이어야) · 태그 뒤 실패의 재실행 절차 · 워크플로 수준 `contents: write`·`secrets: inherit` · smoke 잡의 checkout ref |
| #89 가드 token | 보완 | 1 | 10 | 「서비스 venv」= `which rcm` 의 venv 라 워크트리 venv 활성 셸에선 사고 모양이 허용됨 · serve/worker 는 유효 data_dir 미적용 · `( )`·`exec`·`env -i/-u`·`cd &&`·심링크·XDG 우회 |
| #93 가드 env | 그대로 | 0 | 4 | 빈 `RCM_SERVER_DATA_DIR=` 은 CLI 가 cwd 에 DB 를 만든다 · env 덮어쓰기 규칙이 문서에 없음 |
| #94 한 줄 거절 | 보완(작음) | 1 | 4 | `serve()` 가 포트를 잡기 **전에** 마이그레이션 — 도는 서비스에 새 빌드를 치면 DB 가 먼저 바뀐 뒤 「주소 사용 중」 · `sqlite3.Error`·`token` 의 `OSError` 는 여전히 트레이스백 |
| #84 `~` data_dir disk | 보완(오너) | 1 | 1 | `/api/status` 의 `hosts[].disk.path` 가 사용자 이름 든 절대경로를 인증 없이 노출(기존 문제, 이 PR 이 범위를 넓힘) |
| #98 Chrome 마감 | 보완 | 1 | 1 | 60초 마감이 실제로 죽는 `Target.getTargets`(`_attach_first_page`)에 안 닿아 CI 가 같은 프레임으로 세 번 더 빨갰다 |
| #106 두 레인 문서 | 보완(머지 전) | 3 | 2 | 예시의 `flock` 이 macOS 에 없어 락 없이 도는 fail-open · `concurrent_at_start` 는 어디에도 노출 안 됨 · `step_timeline` 은 #108 뒤 |
| #82 웹 카드·Chrome | 그대로 | 0 | 3 | 수집기 설치를 빼도 초록(`__rcmErrors` undefined → `[]`) · `fetchStatus().catch` 가 렌더 오류를 삼킴 |
| #92 문서·손질 | 그대로 | 0 | 3 | 래퍼 `--pattern` 에 `/` 가 있으면 sed 식이 깨져 마커 없음 |
| #100 `--ref` 중복 | 그대로 | 0 | 2 | — |

## 1. 작업 항목 (S = supplement)

### 열린 PR 의 브랜치에서 (머지 전)

| # | PR | 항목 | 고치는 법 | 테스트 |
|---|---|---|---|---|
| S1 | #109 | PATH 상대 항목의 cwd fail-open | preflight 의 `which` 를 **잡의 cwd(워크스페이스)** 기준으로: 상대 PATH 항목은 `os.path.join(workspace, entry)` 로 풀거나, 상대·빈 항목은 무시하고 로그에 한 줄 | 서버 cwd 에만 도구가 있는 배치 → `tool_missing`; 워크스페이스 안에 있고 PATH 가 `.`/`tools` → 통과 |
| S2 | #109 | basename 빈 절대경로가 공개 args 에 | `requires` 검증에서 끝이 `/` 이거나 basename 이 비면 설정 오류; `public_name` 은 언제나 basename 만 | `["/dir/"]`·`["/"]` → ConfigError |
| S3 | #109 | 선언한 절대경로가 잡 로그에 | 로그도 basename + 판정만(명세 §2 G4) — 오너가 반대하면 유지(G4.5 비고) | 로그에 `/` 없음 |
| S4 | #109 | preflight 전 취소 경쟁 | preflight 직전 `observer.should_cancel()/should_stop()` 확인 · `finish(..., only_from=("running",))` 로 `cancelling` 을 못 덮게 · 원격도 같이 | 취소 요청 뒤 preflight → `cancelled`/exit 2 |
| S5 | #110 | 같은 토큰의 다른 세션이 잡 전체 취소 | 상태 파일 조회를 **자기 `submission_id`**(제출 응답에서 받은 것)로만 — 토큰 지문 폴백 제거; 테스트의 기대값도 바꾼다 | A 제출·B 합류 뒤 B `rcm cancel` → B 만 leave |
| S6 | #110 | leave 비밀의 대상 바인딩 · 원자적 소비 | capability 행에 참여자 이름을 두고 검증 시 bearer 이름과 일치 요구; 조회·삭제·joiner 삭제를 한 트랜잭션(`BEGIN IMMEDIATE`)에, `remove_submission()` 결과 확인 | Bob 비밀 + Charlie bearer → 403; 같은 leave 동시 두 번 → 하나만 200 |
| S7 | #110 | 상태 파일 다중 프로세스 쓰기 | `fcntl.flock` 으로 파일 잠금 뒤 read-modify-write, `.tmp` 는 pid 접미사 | 두 프로세스 동시 쓰기 → 두 항목 다 남음 |
| S8 | #110 | 잡/합류와 capability 비원자 | `create_job`/`join_or_bump` 와 `add_submission` 을 같은 트랜잭션에(store 메서드 확장) | capability 삽입 실패 주입 → 잡 행 없음 |
| S9 | #110 | 200개 상한·저장 실패 시 권한 유실 | 상한은 **종료된 잡**만 정리(서버에 물어 활성이면 보존) · 저장 실패면 stderr 에 「비밀을 저장 못 했다 — `--cancel-token` 으로만 취소 가능」 + JSON 에는 이미 있음 | 201개 활성 → 전부 보존 |
| S10 | #110 | 모르는 role 승격 · stderr 절대경로 · 옛 클라 check | role 은 화이트리스트(그 외 403) · 상태 파일 경로는 `~` 축약 또는 이름만 · `_client_row` 가 `min_client_version` 을 먼저 비교 | 각 1건 |
| S11 | #108 | CHANGELOG 항목 위치 · 예외 폭 · 시계 폴백 | 항목을 `[Unreleased]` 로 · `except Exception` 으로 `null`+코드(형제 경로와 같이) · `finished_at` 없으면 `null`+`no_finished_at` | 깨진 payload 행 → 200 + `null` |
| S12 | #106 | `flock` 없음 · `concurrent_at_start` 미노출 · `step_timeline` 순서 | 예시를 macOS·Linux 둘 다 되는 것으로(파이썬 한 줄 `fcntl.flock` 또는 `mkdir` 락) · `concurrent_at_start` 를 `GET /jobs/<id>` 에 노출(키 추가, #108 뒤) 또는 문장 삭제 · #108 뒤에 머지 | 문서 잠금 갱신 |

### 머지된 PR 의 보완 (새 브랜치)

| # | 브랜치 | 항목 | 고치는 법 | 테스트 · mutcheck |
|---|---|---|---|---|
| L1 | `fix/gc-offline-error-paths` | #87 P1 셋 + P2 | `Store(copy)` 주위를 `except (StoreError, sqlite3.Error)` → exit 3 · `rmtree` 실패는 stderr 경고 + exit 3(사본이 남았다는 사실) · 거절 메시지는 `backup/` 에 **실제로 있는** 가장 높은 `.bak` 을 가리키고 없으면 「백업 없음」 · `_prune_backups` 의 `iterdir` 실패 경고 | raw `sqlite3.OperationalError` 주입 → 3 · rmtree 실패 주입 → 3 · v15.bak 만 있을 때 메시지 · mutcheck: `sqlite3.Error` 를 안 잡으면 빨강 |
| L2 | `fix/janitor-unknown-freed-partial-delete` | #88 P1 셋 + P2 | `unknown_count` 를 응답에 싣고 렌더는 `freed ≥ N B (M unknown)` · 부분 삭제(워크스페이스 성공·tar 실패)도 재측정·캐시 무효화 · 렌더 분기에서 `error_code` 를 floor 보다 먼저 · `budget_unreachable` 에도 나이 | 각 재현 픽스처 · mutcheck: unknown 을 0 으로 세면 빨강 |
| L3 | `fix/update-client-wrapper-token-and-tmp` | #90 P1 둘 + P2 | 래퍼는 고유 임시 디렉터리 안에서 검증·설치(공용 경로 없음) · 토큰은 `curl -H @file` 또는 `--config` 파일로 · health 503 이어도 본문의 `client_wheel` 로 판정(`rcm check`·`rcm run`) · `/client/<name>.whl/` 404 | 래퍼 테스트 확장 · argv 에 토큰 없음(ps 검사) |
| L4 | `fix/guard-service-venv-identity` | #89 P1 + P2 일부 + #93 P2 | 「서비스 venv」를 PATH 가 아니라 **editable 설치의 `direct_url.json` 이 운영 체크아웃을 가리키는 venv** 로 정의(발견 단계) · serve/worker 도 `_effective_data_dir` · `env -i/-u/--` 처리 · `cd X && …` 는 cwd 갱신 · XDG 경로 · 빈 `RCM_SERVER_DATA_DIR=` 문서 | 활성 venv 셸 시뮬레이션에서 deny · 11개 변이 중 살아남은 4개 잠금 |
| L5 | `fix/serve-bind-before-migrate` | #94 P1 + P2 | `serve()` 순서를 bind → Store → App 으로(포트를 못 잡으면 DB 를 안 연다) · `cmd_serve`/`cmd_token` 이 `sqlite3.Error`·`OSError` 도 한 줄 | 포트 점유 상태에서 `serve` → DB `user_version` 불변 · 깨진 DB → 한 줄 |
| L6 | `ci/tag-release-noop-when-tag-is-ancestor` | #85 P1 + P2 | 태그 커밋이 `GITHUB_SHA` 의 조상이면 무동작(exit 0) · CONTRIBUTING 의 재실행 절차(Actions Re-run 또는 태그 삭제 후 재푸시) · 권한을 잡 수준으로, `secrets: inherit` 제거 · smoke 잡 `ref` | 워크플로 문자열 잠금 갱신 |
| L7 | `test/web-browser-attach-deadline` | #98 P1 + #82 P2 | `_attach_first_page` 안의 `Target.getTargets`/`attachToTarget` 호출에 `COLD_START_SECONDS` 적용 · `page_errors()` 가 `__rcmErrors` undefined 면 실패(수집기 누락을 잠금) | mutcheck: 수집기 설치 제거 → 빨강 |
| L8 | `fix/status-disk-path-not-public`(오너 결정) | #84 P1 | `hosts[].disk.path` 를 공개 문서에서 빼거나 `read_auth = basic` 일 때만 · 테스트 갱신 | — |

보류(P2 · 다음 기회): #92 래퍼 `--pattern` 의 `/` 이스케이프 · #82 `fetchStatus().catch` · #108 마커 행 상한 · #89 나머지 셸 형태(`bash -c`, `xargs`, `uv run`) · #87 오프라인 JSON 의 `database` 경로(오너: 복원 안내와 충돌).

## 2. G6 (오너 승인 대기 · 별도)

돌로무드 보고 「gitleaks 2건, 어디인지 로그에 없다」: rcm 몫은 **실패 스텝 발췌** — `rcm logs N --step <name>`(서버가 자기 마커 줄로 구간을 잘라 준다) + 실패 끝줄 힌트 `log: rcm logs N --step gitleaks`. 승인되면 M5j 에 G6 로 추가.

## 3. 순서 (진행 중인 것 포함)

1. **v0.2.6 → main**(#105, 오너). 그 전엔 dev 에 아무것도 머지하지 않는다(머지마다 #105 CI 가 다시 돈다).
2. 열린 PR 의 보완 **S1~S12 를 각 브랜치에서**(격리 에이전트, 시나리오 §4) → CI 초록 → #107(시나리오) → #106 → #109 → #108 → #110 순 dev 머지.
   #106 은 S12 의 `step_timeline` 순서 때문에 #108 뒤로 옮긴다: #107 → #109 → #108 → #106 → #110.
3. L7(CI 안정) → L1 → L5 → L2 → L4 → L3 → L6 → L8(오너 결정 뒤). 각각 새 워크트리 · 시나리오 · 격리 검증 · dev PR.
4. 끝나면 v0.2.7 릴리스 PR(M5j + M5l).

## 4. 시나리오·테스트

`docs/acceptance/m5l-verification-scenarios.md`(격리 에이전트가 작성): S1~S12 · L1~L8 항목마다 재현 조건(리뷰의 「재현」)을 그대로 행으로,
엣지는 리뷰의 P2 를 포함. 포트 8811~8818. mutcheck 추가 4종(L1 `sqlite3.Error` 미포착 · L2 unknown→0 · L7 수집기 제거 · S6 leave 바인딩 제거).

## 5. 오너가 정할 것

1. L8 — `hosts[].disk.path` 를 공개 상태에서 뺄지(집안 규칙은 「경로 금지」, 운영 화면은 경로가 없어도 된다).
2. S3 — 선언한 절대경로를 잡 로그에서도 basename 으로 할지.
3. #87 P2 — 오프라인 dry-run JSON 의 `offline.database` 절대경로(복원 안내에는 필요, 상태 규칙에는 어긋남).
4. G6 채택 여부.
5. #110 의 `"yes"`·`"1"` bool 허용(로더 공통 규칙) 유지.
