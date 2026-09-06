# remote_ci_monitor — 계획서 (v2.4, 2026-09-06)

> 정본이다. 세션을 시작하면 끝까지 읽는다. 웹 큐 화면의 배치·상태·문구는 `docs/wireframes/web-queue.html` 이 정본이다(「웹 UI (M2)」).
> **v2 는 방향 전환이다.** v1(오전)은 GitHub Actions 를 컨트롤 플레인으로 쓰는 관찰+디스패치 도구였다. 오너 검토에서 「GitHub 에 의존하지 않으면 좋겠다」가 나왔고, Codex 크로스리뷰(`docs/reviews/2026-09-04-codex-github-dependency.md`)를 거쳐 **도구가 큐와 실행을 직접 소유하는 로컬 잡 서버**로 바꿨다. GitHub 경로 설계는 커밋 `15e8220`(v1.1)에 남아 있고 M5 의 GitHub 백엔드를 만들 때 참고한다.
> **v2.1 은 웹 큐 화면 기획(v1.3)의 「5. PLAN 반영 제안」을 데이터 모델·큐 규칙·스키마·API·설정에 반영한 것**이다. 바뀐 곳: 잡 상태 `cancelling` · 합류자 `joiners[]` · `position`/`reason` 규칙 · 살아 있는 레인 수로 대기 계산 · 그룹 대기 하한 · 신뢰도 규칙 · `stuck` 판정 · 스키마 v1 필드 추가 · `GET /api/whoami`·`POST /pause`·`/resume` · 설정 키 6개 · 「웹 UI (M2)」 절 교체 · 오너 결정 5개(12~16).
> **v2.2** 는 M3(운영) 반영: `git_ref` 소스 모드의 실제 동작(제출 시 sha 확정 · 미러 · 로컬 clone) · 프리셋 `repo` · 보존 정리(janitor · `metadata_retention_days`) · `read_auth = basic` 의 확정(결정 23) · 서비스 파일. 명세 `docs/m3-workplan.md`, 리뷰 `docs/reviews/2026-09-05-codex-m3-design.md`.
> **v2.3** 은 M4(배포·문서) 반영: 동적 버전 · MIT · `rcm init` · 설치 스모크 · 릴리스 워크플로 · Docker · README 재구성. 명세 `docs/m4-workplan.md`, 리뷰 `docs/reviews/2026-09-06-codex-m4-design.md`.
> **v2.4** 는 M5a(우선순위 · 내용 주소 스냅샷 캐시 · 알림) 반영 + 수용 검사(`docs/acceptance/`) 결과. 명세 `docs/m5-workplan.md`, 리뷰 `docs/reviews/2026-09-06-codex-m5-design.md`.
> ⛔ 는 사람이 정해야 하는 항목이다. 현재 열린 ⛔ 는 없다(「결정 항목」 17~32 는 추천값으로 구현, 오너 확인 대기).

## 한 줄

**빌드 머신 한 대(예: Mac mini)에 여러 컴퓨터의 세션이 잡을 던지면, 서버가 자기 큐로 순차 실행하고, 대기 위치·예상 완료·지금 스텝·CPU·메모리·GPU 를 웹과 터미널로 보여주며, 결과를 종료 코드로 돌려준다.** GitHub 에 의존하지 않는다.

## 왜 만드나

빌드 머신이 한 대뿐인 팀은 CI·QA 요청이 여러 사람·여러 세션에서 겹친다. 「내 차례가 언제 오는지」「앞 잡이 걸린 건지 느린 건지」「머신이 지금 버거운지」「끝났는지, 성공인지」를 한 화면·한 명령으로 알고 싶다.
참고 구현(private `fmmc-tech/dolomood-app-renew` 의 `remote_ci.sh`·`ci_queue.py`·`ci_top.py`)은 이걸 GitHub Actions dispatch 위에서 풀었다. 잘 돌지만 큐·실행·진행 데이터·코드 전달이 전부 GitHub 에 묶여, 폴링 지연·rate limit·「dispatch 는 원격 HEAD 만 본다」(미커밋 변경이 조용히 빠져 초록이 가짜가 된다) 같은 함정을 가드로 막아야 했다. v2 는 **큐와 실행을 도구가 소유**해서 그 함정을 없애고, 세션이 **미커밋 작업 트리를 그대로 보내** 검사할 수 있게 한다. 기존 GitHub Actions 러너는 배포·QA 용으로 그대로 둔다.

## 반드시 지킬 것 — 이식성

이 레포는 public 이고 다른 사람이 자기 빌드 머신에 쓴다.

- 특정 머신·계정·팀 규약·팀 명령을 코드에 박지 않는다. 금지 예: 러너 이름 `dolomood-macmini`, 시간대 KST 고정, `local_ci.sh` 같은 팀 스크립트 이름, 시뮬레이터·Flutter 가정. **실행할 명령은 전부 설정의 프리셋**으로 받고, 참고 팀의 프리셋은 `examples/` 에만 둔다.
- 핵심 경로(제출·큐·실행·진행·결과)는 GitHub 을 부르지 않는다. `git_ref` 소스 모드가 git 원격을 fetch 하는 건 git 의존이지 GitHub 의존이 아니다(어느 호스팅이든 된다).
- macOS 와 Linux 빌드 머신을 둘 다 지원한다(호스트 자원 수집·프로세스 실행). Windows 는 범위 밖으로 명시한다.
- 설치가 한 줄이어야 한다: `pipx install remote-ci-monitor` / `uvx remote-ci-monitor`. 서버와 세션 클라이언트가 같은 패키지다. 런타임 의존성 0.
- 세션 쪽은 SSH·rsync 데몬 같은 두 번째 접속 경로를 요구하지 않는다. 코드 전달도 **같은 HTTP·같은 토큰**으로 한다.
- 시크릿(토큰)은 환경변수·설정 파일로만 받고 절대 커밋하지 않는다. `.gitignore` 와 gitleaks 를 CI 에 건다. 잡 페이로드에 시크릿을 싣지 않는다(빌드 머신에 상주).
- README·CLI 도움말·UI 문자열·식별자는 영어, **주석·docstring·이 계획서·커밋 본문·리뷰 기록은 한국어**(2026-09-04 오너 결정).

## 브랜치 정책 (2026-09-04 적용 — GitHub 룰셋으로 강제)

브랜치는 `main`(릴리스)과 `dev`(통합) 둘이 상시 존재한다. 규칙은 문서가 아니라 GitHub 룰셋과 워크플로가 막는다. 관리자도 bypass 없다.

| 규칙 | 강제 수단 |
|---|---|
| `main`·`dev` 에 직접 커밋·push 불가. 강제 push·삭제도 불가 | 룰셋 `main — PR only, from dev, checks required` · `dev — PR only` 의 `pull_request` · `non_fast_forward` · `deletion` |
| `main` 은 **이 레포의 `dev`** 에서 보낸 PR 로만 머지 | `.github/workflows/pr-policy.yml` 의 `main-from-dev-only` 잡을 main 룰셋 필수 체크로 지정 |
| `dev` 는 PR 로만 받는다(feature 브랜치 → dev) | dev 룰셋 `pull_request` |
| `main` 으로 올라가려면 CI 가 전부 통과 | `.github/workflows/ci.yml` 의 `test` 잡을 main 룰셋 필수 체크로 지정. M0 에서 pytest · ruff · 시크릿 스캔으로 채운다 |
| `dev` → `main` 은 merge commit 만 허용 | main 룰셋 `allowed_merge_methods: ["merge"]` |

- 승인 수는 0 이다(혼자 하는 레포). 필수 체크는 **잡 이름**으로 잡힌다. `test` · `main-from-dev-only` 를 바꾸면 룰셋도 같이 바꿔야 한다.
- ⚠️ **matrix 함정**: `strategy.matrix` 를 걸면 체크 이름이 `test (3.11)` 처럼 바뀐다. matrix 잡은 `unit` 으로 두고 `needs` 로 묶은 **집계 잡 `test`** 가 대표한다(「테스트·품질」).
- 일상 흐름: `git switch dev && git pull` → `git switch -c feat/<topic>` → 작업 · 커밋 → `gh pr create --base dev` → 머지. 릴리스는 `gh pr create --base main --head dev`. (이 레포의 개발 자체는 GitHub 에서 한다 — 도구의 런타임이 GitHub 에 의존하지 않는다는 것과 다른 얘기다.)

## 무엇을 하나

| 항목 | 어떻게 | 비고 |
|---|---|---|
| 잡 넣기 | 세션에서 `rcm run <preset> -f k=v` → `POST /jobs` + 작업 트리 스냅샷 업로드 | 같은 프리셋·같은 입력·같은 트리가 이미 큐에 있으면 새로 넣지 않고 **합류** |
| 큐 (FIFO) — 순번 · 상태 · 프리셋·키 · 요청자 · 대기 · 잔여 · 예상 완료 · 초과 · 막고 있는 잡 | 서버의 SQLite 큐 · 레인 수 설정(기본 1) · concurrency 그룹 | 예상 완료 = 같은 키의 최근 성공 잡 소요 중앙값 − 경과, 앞선 잡 잔여 누적 |
| 진행 — 스텝 N/M · 지금 스텝 · 그 스텝 경과 · 잡 전체 경과 · 스텝 타임라인 · 실패 스텝 · 로그 tail | 프리셋 스크립트가 찍는 **스텝 마커** 줄을 서버가 로그에서 파싱 | 마커가 없어도 잡 전체 경과와 로그는 보인다 |
| 결과 전달 | `rcm wait --job ID` 가 끝날 때까지 기다렸다가 **종료 코드** 0/1/2/3 + JSON 한 줄 | SSE 로 즉시 반응, 폴링 폴백 |
| 최근 완료 — 결과 · 실측 소요 · 요약 한 줄 · 실패 스텝 | 서버 DB | 성공·실패·취소·타임아웃·유실 전부 |
| 호스트 자원 — load · CPU % · 메모리 · **GPU 사용률·GPU 메모리** · 상위 프로세스 · 표본 시각 | 서버 프로세스 안의 샘플러(macOS·Linux) | 기본 5초, 하한 2초. 폴링이지 스트리밍이 아니다 |
| 서버 건강 — 워커 상태 · 마지막 오류 · 큐 일시정지 | 서버 자체 | 「조용히 고장」을 화면에서 잡기 위해 |

## 구조

```
[세션 컴퓨터 (어디든)]                       [빌드 머신 (한 대)]                          [브라우저 / 터미널]
rcm run gate -f scope=full ──HTTP/Tailscale──▶ rcm serve                        ◀──GET /api/status── web UI · rcm top
  ├ 작업 트리 스냅샷(tar.gz) 업로드              ├─ 큐 (SQLite WAL, 재시작해도 남는다)   ◀──GET /events (SSE)── 
  └ rcm wait --job ID ◀──SSE/폴링──            ├─ 워커 레인 N(기본 1): 워크스페이스에 풀고 프리셋 argv 실행
                                                ├─ 로그 파일 + 스텝 마커 파싱
                                                └─ 호스트 자원 샘플러 (in-process: CPU·RAM·GPU)
```

- **프로세스 하나**가 빌드 머신에서 돈다: HTTP 서버 + 큐 + 워커 + 샘플러. 별도 수집기·별도 DB 서버가 없다. 서버는 빌드 머신 자체에서 돌기 때문에 v1 의 「수집기 push」경로가 필요 없다(빌드 머신이 여러 대가 되는 M5 에서 원격 워커로 다시 등장한다).
- 세션 쪽은 같은 패키지의 `rcm` 클라이언트다. Tailscale 이나 LAN 으로 서버에 닿기만 하면 된다. Flutter·시크릿·SSH 가 필요 없다.
- 순수 계산(큐·잔여·진행률·스냅샷 규칙·파서·렌더)과 I/O(HTTP·SQLite·프로세스·파일)를 패키지로 가른다. 순수 부분은 픽스처로 테스트한다.
- JSON 스키마(`schema_version: 1`)를 먼저 고정하고 UI·터미널·`rcm wait` 가 그걸 소비한다. `pools[]` 축은 v1 에서 유지한다(M5 의 여러 머신·여러 풀 대비).

## 잡 모델과 생명주기

```
uploading ──(트리 수신)──▶ queued ──(레인 비고 그룹 안 겹침)──▶ running ──▶ succeeded | failed | timed_out
   │                          │                                    │
   │                          └─ cancelled(대기 중 취소)            ├─ cancelling ──(SIGTERM → grace → SIGKILL)──▶ cancelled
   ├─ cancelled(업로드 포기 · 413 · 업로드 중 취소)                  └─ lost(서버 재시작 중 죽음)
   └─ failed(tar 거부 · 프리셋 소멸 — exit_code null + summary)
```

| 필드 | 뜻 |
|---|---|
| `id` | 서버가 발급하는 단조 증가 정수(순서 = 큐 순서) |
| `preset` · `inputs` | 실행할 프리셋 이름과 검증된 입력값 |
| `key` | 소요시간 버킷. `preset` + 프리셋이 `duration_key_inputs` 로 지정한 입력값(예 `gate:full`) |
| `concurrency_group` | 제출 시점 프리셋의 그룹을 잡에 **박아 둔다**(프리셋을 다시 설정해도 큐에 있는 잡은 안 바뀐다) |
| `source` | `{mode: "tree", base_sha, dirty, tree_hash, repo, bytes, received_bytes, last_received_at}` 또는 `{mode: "git_ref", repo, ref, sha}` |
| `requester` | `{name: 토큰 이름, label: "<이름>@<호스트>" 또는 --by 값}` |
| `joiners[]` | 합류한 세션 `[{name, label, joined_at}]`. 요청자와 합류자 **모두** 「내 잡」이다(화면의 Your jobs · `rcm jobs --mine`) |
| `cancel` | 취소 요청 `{requested_at, by, kill_at}`. `cancelling` 동안만 값이 있고 나머지는 null |
| `state` · `created_at` · `queued_at` · `started_at` · `finished_at` · `exit_code` · `summary` · `failed_step` · `cancelled_by` · `timeout_seconds` | 서버 시계 기준. `queued_at` 은 트리를 다 받아 `queued` 가 된 시각(`git_ref` 면 `created_at`). `exit_code` 는 프로세스가 돌지 못한 실패(자재화 실패 등)면 null |
| `transitions[]` | 상태 전이 이력 `[{state, at}]`(events 테이블에서 만든다). 최근 완료 서랍의 `uploading 09:50:40 → queued → running 09:51:13 → failed 09:52:15` |

- **`rcm wait` 종료 코드**: `succeeded` 0 · `failed` 1 · `cancelled`/`timed_out` 2 · `lost`/조회 실패/`--timeout` 초과 3. 세션 스크립트는 이 코드로 바로 분기한다. 3 은 「모른다」이지 「실패」가 아니다(fail-open 금지). `cancelling` 은 종료 상태가 아니라 `wait` 는 계속 기다린다.
- **큐에서 조용히 사라지는 잡은 없다.** 업로드 포기(`upload_abandon_seconds`)·413(즉시)·업로드 중 취소·**부분 수신 중 연결 끊김(즉시)**·**서버 재시작 때 `uploading` 이던 잡(즉시)**은 `cancelled` + `summary`(`upload abandoned after 5m` · `snapshot 600 MB exceeds 512 MB` · `upload interrupted after 30 MB` · `server restarted during upload`). 부분 업로드 재개는 M0 범위 밖이다 — 새 `rcm run` 으로 다시 제출한다. tar 거부·claim 시 프리셋 소멸·입력이 새 설정에서 무효는 `failed` + `exit_code: null` + `summary`. 전부 최근 완료에 남고 SSE `job_finished` 로 알린다.
- **취소 전파**: 원 요청자(또는 admin)의 취소는 잡을 죽이고, 합류자의 `rcm wait` 는 2 로 끝난다. 합류자가 취소하면 **자기 대기만 빠진다**(`rcm wait` 중단, 잡은 원 요청자 것으로 유지 — 2026-09-04 오너 결정 16).
- 잡은 서버 재시작을 넘어 살아남는다(`queued` 는 그대로, `running`·`cancelling` 이던 것은 `lost`). 워크스페이스·로그·스냅샷은 보존 기간 뒤 정리한다.

## 큐 규칙 (순수 · `core/queue.py`)

- **순서**: `id` 오름차순 = 생성 순 FIFO. 우선순위는 없다(필요해지면 M5). 화면 정렬은 running → cancelling → 대기(순번순).
- **순번 `position`**: **대기 잡(`uploading`·`queued`)에만 1부터** 매긴다. `running`·`cancelling` 은 null. `#` 는 언제나 잡 id 이고 순번은 `2nd in line` 으로 따로 쓴다(둘을 섞지 않는다).
- **레인**: `server.lanes`(기본 1). 레인이 비어 있어도 **concurrency 그룹**이 겹치면 못 올라간다 — 프리셋에 `concurrency_group = "devices"` 를 주면 같은 그룹의 잡은 동시에 하나만 돈다(시뮬레이터·에뮬레이터를 공유하는 QA·배포용). 막고 있는 잡을 `blocked_by: {job_id, group, remaining_seconds}` 로 보여준다. 그룹은 제출 시점에 잡에 박힌 `concurrency_group` 을 쓴다. 레인 1 이면 `blocked_by` 는 자연히 안 나온다(오너 결정 12).
- **살아 있는 레인**: 대기 계산은 `server.lanes` 가 아니라 **`state ≠ down` 인 워커 수**로 한다. 그 수가 0 이거나 `server.paused` 면 대기 잡의 `wait_seconds`·`finish_at` 은 **null**(ETA `—`) — 시작할 수 없는 잡에 시각을 주지 않는다.
- **이유 `reason`**: 잡이 지금 왜 이 상태인지 서버가 계산해 싣는 **단일 표시 사유**다(`estimate.overdue`·`estimate.stuck` 은 근거 플래그로 따로 싣는다). `running` · `waiting_for_lane`(`ahead_job_id` = 가장 먼저 비는 레인의 잡) · `blocked_by_group` · `uploading` · `upload_stalled`(`upload_stall_seconds` 동안 바이트가 안 옴) · `materializing` · `overdue` · `stuck` · `cancelling` · `paused` · `not_scheduled`(대기 잡이 있고 정지 아니고 그룹에 안 막혔는데 idle 레인이 있는 채로 `max(워커 since, 잡 queued_at)` 부터 10초가 넘음 — 스케줄러 이상) · `worker_down`(모든 레인 다운). 행동 가능한 이유(`worker_down` → `stuck` → `upload_stalled` → `not_scheduled` → `blocked_by_group` → `overdue` → `paused`)는 화면 요약 「Not moving」에 이 순서로 오른다.
- **합류**(`join_duplicates`): 활성 잡(`uploading`·`queued`·`running`) 중 같은 `preset` · 같은 `inputs` · 같은 소스 신원(`tree` 면 `tree_hash`, `git_ref` 면 `sha`)이 있으면 새 잡을 만들지 않고 그 잡 id 를 돌려준다(`joined: true`). 두 세션이 같은 코드를 확인하려는 것뿐이라 두 번 돌릴 이유가 없다. 스냅샷 업로드도 생략된다. `--no-join` 으로 끈다. (v1 의 GitHub 경로에선 inputs 를 비교할 수 없어 run 이름 규약에 기대야 했다 — 이제 정확히 비교한다.) 합류한 세션은 `joiners[]` 에 `{name, label, joined_at}` 으로 남고, 요청자와 합류자 모두 「내 잡」이다.
- **취소**: 자기 토큰의 잡(요청자)만, `admin` 토큰은 전부. `uploading`·`queued` 면 즉시 `cancelled`(진행 중이던 `PUT` 은 409), `running` 이면 `cancelling` 으로 바꾸고 SIGTERM → `grace_seconds` → SIGKILL → `cancelled`. `cancel.{requested_at, by, kill_at}` 을 싣는다. 원 요청자의 취소는 합류자의 `rcm wait` 에 종료 코드 2 로 전파된다. 합류자의 취소는 잡을 건드리지 않고 자기 `joiners[]` 항목만 지운다(오너 결정 16).
- **표본**: 같은 `key` 의 완료 잡 중 `sample_policy`(기본 `success`) · `min_job_seconds`(30) 이상 · `sample_days`(45) 안. 소요는 `started_at`~`finished_at`(큐 대기는 안 섞인다 — 우리가 시각을 찍으니 v1 의 「run 시각 vs 잡 시각」 함정이 없다). 대기 중앙값(`medians[key].wait_seconds`)은 같은 표본의 `created_at`~`started_at`.
- **중앙값**: 키별, `min_samples`(2) 이상일 때만. 아니면 프리셋의 `expected_seconds` → `default_seconds`(600). 출력에 `source: measured|preset|default` 와 `sample_count`.
- **신뢰도**(화면 배지): `measured` 이고 `sample_count ≥ 5` → `high`, `measured` 이고 `< 5` → `med`, `preset`·`default` → `low`. 그룹 대기 잡은 `group wait`, 초과 실행·stuck 잡은 `overdue`. **서버가 `estimate.confidence` 로 싣는다**(M1 결정 B — UI 와 `rcm top` 이 어긋날 수 없게. `core/queue.confidence()`).
- **잔여**: `queued` → expected 전체. `running` → `max(expected − elapsed, floor 30초)`. `overdue = elapsed > expected`. 초과 실행·stuck 잡의 `finish_at` 은 **null**(`now + 30s` 는 하한이 만든 자신있는 틀린 시각이라 싣지 않는다) — 대신 `remaining_seconds` 하한은 뒤 잡의 대기 계산에만 쓴다. `wait_seconds` 는 `running`·`cancelling` 이면 항상 0 이고, 대기 잡이 정지·레인 0 이면 null 이다.
- **stuck**: `running` 이고 `elapsed > stuck_multiplier(3) × expected` 이거나 `now − progress.last_output_at > no_output_seconds(240)` 이면 `estimate.stuck: true`. `overdue` 와 다르다(overdue 는 「늦다」, stuck 은 「죽었을지 모른다」).
- **대기**: 살아 있는 레인 1 → 앞선 잔여 합. 레인 k → 잔여를 큰 순으로 가장 빨리 비는 레인에 얹는 그리디. 그룹 제약은 근사로 무시하되, 그룹에 막힌 잡은 **`finish_at ≥ 막는 잡의 finish_at + 자기 expected`** 하한을 적용한다(막는 잡보다 이른 시각이 나오지 않게. 정확한 스케줄 시뮬레이션은 M5). 실행 중 잡의 `waited_seconds` 는 `created_at`~`started_at`.
- **완료 시각**: `now + wait + remaining`. 시각은 전부 UTC aware, 표시 때만 시간대.

## 프리셋

서버 설정의 `[[presets]]`. 세션은 **프리셋 이름과 입력값만** 보낸다. 임의 명령은 없다(2026-09-04 오너 결정).

```toml
[[presets]]
name = "gate"
description = "Full local gate: analyze, test, lint"
argv = ["bash", "scripts/gate.sh"]          # 워크스페이스 기준. 셸 보간 없음 — 입력은 env 로만 전달
timeout_seconds = 1200
source_modes = ["tree"]                     # "tree" | "git_ref". 게이트는 tree 만, 배포는 git_ref 만
repo = ""                                   # git_ref 프리셋이 가리키는 [[repos]].name (repos 가 하나면 생략 가능) — M3
concurrency_group = ""                      # 같은 그룹은 동시에 하나
expected_seconds = 480                      # 표본이 모자랄 때
duration_key_inputs = ["scope"]             # key = "gate:<scope>"
env_passthrough = ["PATH", "HOME", "LANG"]  # 서버 프로세스 env 중 넘길 것. 기본은 이 셋
[presets.env]                               # 고정 env(시크릿은 여기 말고 빌드 머신의 파일에서 스크립트가 읽는다)
CI = "1"
[[presets.inputs]]
name = "scope"
type = "choice"                             # "string" | "choice" | "bool" | "int"
choices = ["full", "commit", "fast"]
default = "full"
```

- 입력은 스키마로 검증하고(타입·choices·정규식 `pattern`·길이 256), `RCM_INPUT_<NAME>` 환경변수로 넘긴다. `argv` 에 입력을 끼워 넣지 않는다.
- 워커가 항상 주는 env: `RCM_JOB_ID` · `RCM_PRESET` · `RCM_REQUESTER` · `RCM_SOURCE_MODE` · `RCM_BASE_SHA` · `RCM_DIRTY` · `RCM_REF`(tree 잡은 빈 값) · `RCM_WORKSPACE` · `RCM_LOG_FILE`.
- 프리셋 목록은 `GET /api/status.presets` 와 `rcm presets` 로 세션이 볼 수 있다(입력 스키마 포함).
- 설정 오류(모르는 키·argv 비어 있음·choices 없는 choice)는 서버 시작 시 **프리셋 이름과 키 이름**을 찍고 실패한다.

## 코드 전달

### `tree` — 작업 트리 스냅샷 (게이트 기본, 2026-09-04 오너 결정)

세션의 **지금 작업 트리**(미커밋·미푸시 포함)를 그대로 보낸다. 「dispatch 는 원격 HEAD 만 본다」 함정이 사라진다.

- **파일 선택**(순수 · `core/snapshot.py`): git 체크아웃이면 `git ls-files -z --cached --others --exclude-standard` (추적 + 무시되지 않은 미추적) 에서 작업 트리에 없는 것(삭제)을 빼고, `.git/` 은 항상 제외. `.rcmignore`(gitignore 문법)와 `--exclude` 를 더한다. git 이 아니면 `.rcmignore` 만. 심볼릭 링크는 링크로 담는다.
- **신원**: `base_sha = HEAD`, `dirty = 작업 트리가 HEAD 와 다른가`, `tree_hash = sha256(정렬된 (경로, 모드, 내용 sha256) 목록)`. 합류 판정과 감사에 쓴다. `repo = git remote get-url origin`(표시용).
- **전송**: `tar.gz` 를 `PUT /jobs/{id}/tree` 로 올린다(같은 HTTP·같은 Bearer). rsync·SSH 를 안 쓰는 이유: 두 번째 접속·인증 경로가 생기고 rsync 데몬·키 관리가 따라온다. 크기 상한 `max_snapshot_bytes`(기본 512MB) 초과는 413 + 「.rcmignore 로 빌드 산출물을 빼라」. 참고 팀의 앱 트리(에셋 포함 수십 MB)는 Tailscale 에서 수 초다. 내용 주소 캐시(이미 있는 파일은 안 보냄)는 M5.
- **서버 풀기**: `tarfile.extractall(filter="data")`(3.11.4+) — `..`·바깥을 가리키는 링크·장치 파일을 거부하고, 절대 경로 멤버는 앞의 `/` 를 떼어 워크스페이스 안으로 **상대화**한다(표준 라이브러리 data 필터의 동작 — 밖으로는 못 나간다). 워크스페이스 `<data_dir>/workspaces/<job_id>/`.

### `git_ref` — 원격 브랜치 (배포·릴리스용, M3 구현)

세션은 `rcm run deploy --ref v1.2.3` 처럼 `--ref` 만 보내고 트리를 안 올린다. 프리셋은 `source_modes = ["git_ref"]` + `repo = "<[[repos]].name>"`(repos 가 하나면 생략 가능). tree 요청은 400, tree 프리셋에 `--ref` 는 usage 2.

- **제출 시 sha 확정**: 서버가 `git ls-remote -- <url> <ref>` 로 커밋 sha 를 정한다(`git_resolve_timeout_seconds` 20, 동시 2개까지 — 핸들러가 원격 호출에 묶이지 않게). 실패 502 · 타임아웃 504. 40 hex 는 원격을 안 부른다. 합류 신원은 이 sha(ref 이름이 달라도 같은 커밋이면 합류). 잡은 바로 `queued`, `queued_at = created_at`.
- **ref 검증**(`core/gitref.py`, 순수): `git check-ref-format` 의 보수적 부분집합 — `-` 로 시작 금지(옵션 주입) · 공백·제어문자 · `..` `@{` `\` `^` `:` `?` `*` `[` `~` · 앞뒤 `/` · `//` · `.lock` · 200자. `[[repos]].url` 은 `https://` · `ssh://` · `git://` · `file://` · scp 형 · 절대 경로만.
- **자재화**(`gitops.py` · `materialize.prepare_git_ref`): 미러 `<data_dir>/mirrors/<name>/`(bare, `gc.auto=0`)에 **ref 하나만 먼저** fetch 하고 sha 가 안 오면 전체(heads · tags, `--prune`) fetch 로 폴백 → `cat-file -e <sha>^{commit}` 으로 확인(없으면 `failed` + 「ref moved or was force-pushed?」) → 로컬 clone(객체 하드링크 — 미러가 gc 해도 워크스페이스가 안 깨진다) + `checkout --detach <sha>`. `.git` 이 남아 `git describe` 가 된다. submodule 은 스크립트 몫. 같은 미러는 프로세스 안에서 직렬화. git 의 stderr 는 잡 로그(`[git] …`)에만, summary 엔 URL·경로 없음.
- env: `RCM_REF` · `RCM_BASE_SHA = sha` · `RCM_DIRTY = 0`. 표시는 `app @a1b2c3d · ref main`.

## 워커 실행 (`worker.py`)

- 레인마다 스레드 하나: `claim`(SQLite 트랜잭션으로 원자적) → 워크스페이스 준비 → `subprocess.Popen(argv, cwd=workspace, env=…, stdout=PIPE, stderr=STDOUT, start_new_session=True)` → 로그 줄을 파일에 쓰며 스텝 마커를 파싱 → 종료 코드로 상태 확정.
- 타임아웃·취소: 프로세스 그룹에 SIGTERM → `grace_seconds`(10) → SIGKILL. 자식이 만든 손자까지 죽이려고 `start_new_session` 을 쓴다. 취소는 `cancelling` 상태를 거친다(`cancel.kill_at` = 요청 시각 + grace). 타임아웃은 `timed_out` + `summary: "limit 20m"` + `timeout_seconds`.
- 워크스페이스 준비(tar 풀기·`git_ref` fetch)는 `running` 이지만 `progress.phase: "materializing"` 이다(스텝이 없는 것과 구분). 프로세스가 뜨면 `executing`. 준비 실패는 `failed` + `exit_code: null` + `summary`.
- 로그: `<data_dir>/jobs/<id>/log.txt` 줄 단위 flush. 최근 `tail` 은 상태 JSON 에 싣고 전체는 `GET /jobs/{id}/log`. 로그엔 시크릿이 섞일 수 있어 **읽기에 그 잡의 토큰 또는 admin** 이 필요하다. 마지막 줄을 받은 시각을 `progress.last_output_at` 으로 싣는다(stuck 판정).
- 워커 상태: 레인마다 `{lane, state ∈ idle|busy|down, job_id, error, since}` 를 `server.workers[]` 로 싣는다. 스레드가 예외로 죽으면 `down` + `error`(앞 200자, 경로·토큰 없이) 로 남고 `server.last_error` 에도 적는다. 워커가 죽었는데 큐만 멀쩡해 보이는 화면이 가장 위험하다.
- ⚠️ 자식 프로세스의 stdout 버퍼링 때문에 마커가 늦게 도착한다. README 에 `PYTHONUNBUFFERED=1`·`stdbuf -oL`·`flutter --no-color` 같은 팁을 쓴다. 마커가 늦어도 잡 전체 경과는 정확하다.
- 정리(M3 `janitor.py` + 순수 `core/retention.py`): 성공 잡 워크스페이스는 완료 즉시 삭제(`keep_workspace_on_failure = true` 면 succeeded 가 아닌 모든 종료 상태 — failed·timed_out·cancelled·lost — 는 보존 기간까지). 서버 안 청소 스레드가 시작 직후와 `retention_sweep_interval_seconds`(3600)마다 `retention_days_success`(14) · `retention_days_failure`(30) 지난 종료 잡의 `jobs/<id>/`·`workspaces/<id>/` 를 지우고 `jobs.artifacts_purged_at` 에 표시한다(DB v2). 활성 잡은 삼중으로 보호(순수 규칙 · janitor 재확인 · UPDATE 조건). 심볼릭 링크는 링크만, data_dir 밖을 가리키면 손대지 않는다. 산출물이 지워진 뒤 `metadata_retention_days`(180, `sample_days` 이상) 지난 잡 행·이벤트·합류자는 삭제한다. 미러는 안 지운다. 지운 잡의 로그는 404 `log expired`. 스레드가 죽거나 주기의 2배가 지나도록 sweep 이 없으면 `/api/health` 503.
- 권한: 서버가 도는 OS 사용자로 실행된다. README 에 「전용 사용자로 돌리고 sudo 를 주지 말라」.

## 진행 — 스텝 마커 프로토콜 (순수 · `core/progress.py`)

프리셋 스크립트가 stdout 에 줄 단위로 찍는다. 줄 맨 앞이어야 하고, 마커 줄은 로그에도 그대로 남는다.

| 마커 | 뜻 |
|---|---|
| `::rcm::steps::<N>` | 앞으로 스텝이 N 개다(선택). 있으면 「N/M」의 M 이 확정, 없으면 「지금까지 알려진 수 (so far)」 |
| `::rcm::step::<이름>` | 새 스텝 시작. 앞 스텝은 이 시각에 끝난 것으로 본다 |
| `::rcm::step-end::<ok|fail>` | 스텝 끝을 명시(선택). 없으면 다음 마커나 잡 종료가 끝이다 |
| `::rcm::summary::<한 줄>` | 결과 요약(선택). 마지막 것이 `summary` |

- 마커가 하나도 없는 잡은 `steps: []`, `steps_total: null`(0 이 아니다) 로 「스텝 정보 없음」. 잡 전체 경과와 로그 tail 은 그대로.
- 실패 스텝: 종료 코드 ≠ 0 이면 마지막 스텝(또는 `step-end::fail` 이 찍힌 스텝)이 `failed_step`.
- 함정과 테스트(픽스처로 잠근다):

| # | 사실 | 규칙 | 테스트 |
|---|---|---|---|
| 1 | 총 스텝 수를 안 알리는 스크립트가 많다 | `steps_total_partial: true`, UI 「N/M (so far)」 | `test_step_total_partial_without_declaration` |
| 2 | 마지막 스텝은 끝 마커가 없다 | 잡 종료 시각이 끝 | `test_last_step_ends_at_job_end` |
| 3 | 버퍼링으로 마커가 몰려서 온다 | 스텝 시각은 **서버 수신 시각**이라 실제보다 늦을 수 있음을 스키마에 `timing: "as_received"` 로 밝힌다 | `test_marker_timestamps_are_receive_times` |
| 4 | 같은 이름 스텝이 반복된다(매트릭스) | 위치(`index`)로 세고 이름은 표시용 | `test_duplicate_step_names_by_index` |
| 5 | 잡이 시작 전이면 스텝이 없다 | state `queued` 엔 진행 칸 없음, 0/0 금지 | `test_queued_job_has_no_progress` |
| 6 | 초과 실행 잡의 잔여를 음수로 두면 큐 전체가 앞당겨진다 | 하한 30초 · `overdue` · 실제 경과 표시 | `test_overdue_run_floors_remaining` |

## 호스트 자원 (서버 프로세스 안의 샘플러 · `hostsample.py` + 순수 `core/hostparse.py`)

- 주기 `host.interval_seconds`(기본 5, 하한 2 — `top` 1초 표본이 병목). 마지막 표본 하나를 메모리에 두고 `hosts[]` 에 싣는다(`sampled_at`·`age_seconds`·`stale`).
- **macOS**: `os.getloadavg()` · `sysctl -n hw.memsize` · `vm_stat`(active + wired + compressor) · `top -l 2 -n 0 -s 1`(**두 번째** 표본만) · `ps -Aro %cpu=,rss=,comm=` · **GPU** `ioreg -r -d 1 -w 0 -c IOAccelerator` 의 `PerformanceStatistics` → `Device Utilization %`·`In use system memory`(2026-09-04 Apple Silicon 에서 sudo 없이 확인. `powermetrics` 는 sudo 라 안 쓴다. 통합 메모리라 `mem_total_bytes` 는 null).
- **Linux**: `/proc/loadavg` · `/proc/meminfo`(`MemTotal − MemAvailable`) · `/proc/stat` 1초 차분 · `ps -eo %cpu=,rss=,comm= --sort=-%cpu` · **GPU** `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits` 가 있을 때만. AMD·Intel GPU 는 범위 밖.
- 값이 없는 칸은 `null`(0 아님), 부분 실패는 그 칸만. 전부 실패면 `hosts_error`.
- 파서는 두 OS 의 실제 출력 캡처를 픽스처로 잠근다.

## 보안 (원격에서 명령을 실행시키는 서버다)

- **쓰기(`POST /jobs`·업로드·취소)는 인증 필수.** `none` 은 없다. 토큰은 **클라이언트별**(2026-09-04 오너 결정): `rcm token add <name> [--admin]` 이 무작위 32바이트 토큰을 만들어 한 번만 출력하고 서버 DB 에는 sha256 만 저장한다. 비교는 `hmac.compare_digest`. `rcm token revoke <name>`. 요청자 표시는 토큰 이름에서 온다.
- **읽기**(`/api/status`·`/events`·UI)는 기본 `none`(Tailscale/LAN 전제, 2026-09-04 오너 결정). `basic`(M3): 읽기 라우트가 Bearer **또는** HTTP Basic(`<토큰 이름>:<토큰>`)을 요구한다 — 별도 자격 저장소 없이 브라우저 프롬프트로 열린다. 401 은 `WWW-Authenticate: Basic realm="rcm"`. **쓰기는 Bearer 만**(브라우저가 Basic 을 자동으로 붙이므로 쓰기에 허용하면 CSRF). 평문이라 TLS 프록시 뒤 전용. **잡 로그**는 예외로 항상 토큰이 필요하다(시크릿이 섞일 수 있다).
- **프리셋만 실행.** argv 배열, 셸 없음, 입력은 env 로만. 입력 길이·타입·choices 검증. 임의 명령 옵션은 만들지 않는다.
- 바인드 기본 `127.0.0.1`. Tailscale IP 나 `0.0.0.0` 은 명시. TLS 는 서버가 안 한다(Tailscale 이 암호화한다).
- 스냅샷: 크기 상한 · `tarfile` data 필터 · 워크스페이스 밖 쓰기 불가. `.git` 은 받지 않는다.
- 전용 OS 사용자 · sudo 없음 · 시크릿은 빌드 머신 파일에서 프리셋 스크립트가 읽는다(README 런북).
- 오류 응답에 스택·토큰·경로를 싣지 않는다. 요청 로그는 debug 에만.

## 서버 API (`server.py`)

| 라우트 | 인증 | 동작 |
|---|---|---|
| `POST /jobs` | 토큰 | `{preset, inputs, source, requester_label, join}` → 검증 → 합류면 `{job_id, joined: true}`(+ `joiners[]` 에 기록), 아니면 새 잡(`uploading` 또는 `git_ref` 면 바로 `queued`) `{job_id, joined: false, upload: "/jobs/{id}/tree"}`. `git_ref` 는 `source: {mode, ref}` → 서버가 sha 확정 → `{job_id, joined, state: "queued", sha, url}`(400 ref 검증 · 502 해석 실패 · 504 타임아웃). git_ref 잡에 `PUT tree` 는 409 |
| `PUT /jobs/{id}/tree` | 토큰(그 잡의) | 본문 tar.gz(`Content-Length` 필수, 상한) → 풀지 않고 저장만 → `queued`. 수신 중 `source.received_bytes`·`last_received_at` 갱신. 이미 취소된 잡이면 409 |
| `GET /jobs/{id}?tail=N` | 없음(`log_tail` 은 토큰) | 잡 스냅샷(활성 잡은 queue 행, 종료 잡은 recent 행 모양 — recent 행엔 `log_tail` 키가 없다). `log_tail` 은 **유효 토큰(그 잡의·합류자·admin) 요청이고 `running`/`cancelling` 일 때만** 싣고 아니면 null. `tail` 기본 5줄, 잡당 8KiB 상한, `rcm wait` 는 `tail=0` |
| `GET /jobs/{id}/log?offset=N` | 토큰(그 잡의·합류자·admin) | 로그 바이트 스트림(증분). 보존 정리로 지워졌으면 404 `log expired` |
| `GET /jobs/{id}/events` | 없음 | SSE: 그 잡의 `job_changed`·`job_finished`·`marker` 만(로그 줄은 아님). 이미 끝난 잡이면 `hello` 뒤 `job_finished` 하나를 보내고 닫는다 |
| `POST /jobs/{id}/cancel` | 토큰(그 잡의 또는 admin) | 취소 → `{job_id, state}`. 합류자 토큰이면 잡은 두고 자기 `joiners[]` 항목만 지운다 → `{left: true, job_id, job_state}` 이고 그 세션의 `rcm wait` 는 같은 JSON 을 찍고 **2** 로 끝난다 |
| `GET /api/whoami` | 토큰 | `{name, admin}`. 401 이면 UI 가 저장 토큰을 지운다(네트워크 오류는 지우지 않는다) |
| `POST /pause` · `POST /resume` | admin | 큐 정지·재개. 실행 중 잡은 끝까지 돌고 새 잡만 안 올라간다. `server.paused: {by, at}` 또는 null |
| `GET /api/status` | `read_auth` | 전체 `StatusModel`. `ETag` 지원. `log_tail` 은 토큰 조건(위) |
| `GET /events` | `read_auth` | SSE: `hello` → `job_changed`·`job_finished`·`marker`·`host_sample`·`server`(+ `reset`·`lag` = 전체 재조회). `Last-Event-ID` 재생, 15초 keep-alive. 동시 연결 `sse_max_connections`(16) 초과는 503 + `Retry-After` + `{fallback: "poll", poll_seconds: 10}` — 웹은 백오프로 재시도하며 10초 폴링, CLI `wait` 는 2초 폴링 |
| `POST /api/eta` | `read_auth` | `{preset, inputs}` → 가상 잡의 큐 행(`id` null, `position` = 대기 수 + 1) + `ahead`. `rcm eta` 용 |
| `GET /api/health` | 없음 | 워커 스레드 살아 있고 DB 열리면 200, 아니면 503 + 사유 |
| `GET /` · `/static/*` | `read_auth` | 정적 UI |

- `http.server.ThreadingHTTPServer`(표준 라이브러리). SSE 는 응답을 열어 두고 줄을 흘리는 스레드라 keep-alive 문제가 없다(요청당 스레드). **hardening**: 소켓 타임아웃(일반 10초, SSE·업로드는 별도) · `Content-Length` 필수(없거나 chunked 면 411) · 동시 요청 `max_concurrent_requests`(32) 초과 503 · SSE 동시 연결 상한(16) · 정적 경로 정규화(`http.server` 가 앞의 `//` 는 `/` 로 합치고, 안쪽 `//`·`..` 는 400) · 모르는 메서드도 JSON 405/404(표준 라이브러리의 HTML 501 이 아니다) · 405/400/413/401/403 명확히 · 예외는 500 한 줄.
- 상태 모델은 폴러가 아니라 **이벤트로 갱신**한다(잡 상태 변화·마커·호스트 표본이 들어올 때 모델을 다시 만들어 참조 교체). `/api/status` 는 항상 최신이다.

## 저장소 (`store.py` · SQLite WAL · 표준 `sqlite3`)

```
<data_dir>/                       # 기본 ~/.local/share/rcm (XDG), --data-dir
  rcm.sqlite3                     # jobs · events · tokens · duration_samples
  jobs/<id>/log.txt · tree.tar.gz        # 메타데이터는 DB 에만(meta.json 은 두지 않는다)
  workspaces/<id>/                # 실행 중·실패 보존
  mirrors/<repo>/                 # git_ref 모드용 로컬 미러
```

- `jobs(id, preset, inputs_json, key, concurrency_group, source_json, requester_name, requester_label, state, created_at, started_at, finished_at, exit_code, summary, failed_step, lane, tree_hash, sha, timeout_seconds, cancel_requested_at, cancel_by, cancel_kill_at, cancelled_by)` · `joiners(job_id, name, label, joined_at)` · `events(job_id, at, kind, payload)`(상태 전이는 `kind = "state"` 로 남겨 `transitions[]` 를 만든다) · `tokens(name, sha256, admin, created_at, revoked_at)` · `server_state(key, value)`(`paused` 등).
- 마이그레이션은 `PRAGMA user_version` 으로 번호를 매긴다. 시작 시 `running`·`cancelling` → `lost`(`lane` 도 비운다, `summary: "server restarted <시각>"`), `uploading` → `cancelled`(`summary: "server restarted during upload"`) 로 정리한다. 상태 전이 이벤트는 잡 갱신과 **같은 트랜잭션**에 넣는다. `concurrency_group` 은 빈 문자열을 NULL 로 정규화한다.

## 설정

**서버**(`rcm serve --config`, 탐색: `--config` → `$RCM_CONFIG` → `./rcm.toml` → `$XDG_CONFIG_HOME/rcm/server.toml` → `~/.config/rcm/server.toml`). 우선순위 **플래그 > 환경변수(`RCM_<섹션>_<키>`) > 파일 > 기본값**.

```toml
[server]
bind = "127.0.0.1"                  # Tailscale 로 열려면 그 IP 나 0.0.0.0 을 명시
port = 8787
data_dir = "~/.local/share/rcm"
lanes = 1
read_auth = "none"                  # "none" | "basic" (TLS 프록시 뒤에서만)
max_snapshot_bytes = 536870912
max_concurrent_requests = 32
join_duplicates = true
grace_seconds = 10                  # SIGTERM 뒤 SIGKILL 까지
retention_days_success = 14
retention_days_failure = 30
keep_workspace_on_failure = true
recent_count = 8                    # /api/status.recent 건수 (오너 결정 14)
sse_max_connections = 16            # 초과는 503 + fallback: poll
sse_keepalive_seconds = 15
public_url = ""                     # 잡 url 에 쓸 바깥 주소(예 http://macmini:8787). 비면 요청의 Host 로
upload_stall_seconds = 60           # 이 동안 바이트가 안 오면 reason = upload_stalled
upload_abandon_seconds = 300        # 이 동안 바이트가 안 오면 cancelled + "upload abandoned after 5m"
retention_sweep_interval_seconds = 3600  # 보존 정리 주기(하한 60) — M3
metadata_retention_days = 180       # 잡 행·이벤트 삭제(sample_days · retention_days_* 이상) — M3
git_resolve_timeout_seconds = 20    # 제출 시 ls-remote 상한 — M3
git_fetch_timeout_seconds = 600     # 자재화 fetch·clone 상한 — M3

[estimate]
sample_days = 45
min_samples = 2
min_job_seconds = 30
sample_policy = "success"           # "success" | "completed"
default_seconds = 600
floor_remaining_seconds = 30
stuck_multiplier = 3                # elapsed > 3 × expected → stuck
no_output_seconds = 240             # 로그가 이만큼 없으면 → stuck

[host]
interval_seconds = 5                # 하한 2
gpu = "auto"                        # "auto" | "off"
top_processes = 5
history_samples = 60                # hosts[].history[] 길이 (5초 × 60 = 5분 sparkline)

[display]
timezone = ""                       # IANA. 시작 시 zoneinfo 로 검증. 비면 서버 로컬 / 브라우저 로컬

[[repos]]                           # git_ref 모드용(선택). 프리셋이 repo = "app" 으로 가리킨다
name = "app"
url = "git@github.com:org/app.git"  # 어떤 git 호스팅이든. 빌드 머신의 git 자격을 쓴다. https:// ssh:// git:// file:// scp형 · 절대경로만

[[presets]]                         # 위 「프리셋」
```

**클라이언트**(`~/.config/rcm/client.toml`, 또는 `--server`·`RCM_SERVER`·`RCM_TOKEN`):

```toml
server = "http://macmini:8787"
token_env = "RCM_TOKEN"             # 값이 아니라 env 이름. 파일에 토큰을 직접 두려면 token = "…" (파일 권한 600 검사)
label = ""                          # 비면 "<토큰 이름>@<호스트명>"
```

- `rcm check`: 서버 접속·토큰 유효·프리셋 목록·시간대·데이터 디렉터리 쓰기 가능을 표로 보여준다(셋업 확인용).
- 설정 오류는 시작 시 **키 이름과 함께** 실패한다. 조용히 기본값으로 떨어지지 않는다.

## `/api/status` 스키마 v1 (`rcm top --json` 과 동일)

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-04T00:52:12Z",
  "display_timezone": null,
  "server": {"version": "0.1.0", "uptime_seconds": 8123, "lanes": 1, "paused": null, "last_error": null,
             "workers": [{"lane": 1, "state": "busy", "job_id": 412, "error": null, "since": "2026-09-04T00:51:13Z"}]},
  "presets": [{"name": "gate", "description": "Full local gate", "source_modes": ["tree"], "repo": null, "concurrency_group": null,
               "expected_seconds": 480, "timeout_seconds": 1200,
               "inputs": [{"name": "scope", "type": "choice", "choices": ["full", "commit", "fast"], "default": "full"}]}],
  "pools": [{
    "name": "default", "lanes": 1,
    "queue": [{
      "id": 412, "position": null, "preset": "gate", "key": "gate:full", "inputs": {"scope": "full"},
      "concurrency_group": null,
      "requester": {"name": "alice-laptop", "label": "alice@laptop"},
      "joiners": [{"name": "eve-ci", "label": "eve@ci", "joined_at": "2026-09-04T00:50:58Z"}],
      "state": "running", "reason": "running", "lane": 1, "ahead_job_id": null, "blocked_by": null, "cancel": null,
      "source": {"mode": "tree", "repo": "org/app", "base_sha": "abc123…", "dirty": true, "tree_hash": "9f8e…",
                 "bytes": 48213344, "received_bytes": 48213344, "last_received_at": "2026-09-04T00:50:52Z"},
      "created_at": "2026-09-04T00:50:40Z", "started_at": "2026-09-04T00:51:13Z",
      "estimate": {"expected_seconds": 369, "source": "measured", "sample_count": 7,
                   "elapsed_seconds": 59, "waited_seconds": 33, "remaining_seconds": 310, "wait_seconds": 0,
                   "overdue": false, "stuck": false, "finish_at": "2026-09-04T00:57:22Z"},
      "progress": {"timing": "as_received", "phase": "executing", "last_output_at": "2026-09-04T00:52:10Z",
                   "steps_total": 8, "steps_total_partial": false, "steps_done": 4,
                   "current_index": 5, "current_name": "test", "current_seconds": 51, "job_seconds": 59,
                   "failed_step": null,
                   "steps": [{"index": 1, "name": "analyze", "state": "done", "ok": true, "seconds": 12},
                             {"index": 5, "name": "test", "state": "running", "ok": null, "seconds": 51}]},
      "log_tail": ["[test] 3/9 packages…"],
      "url": "http://macmini:8787/#/jobs/412"
    }, {
      "id": 413, "position": 1, "preset": "qa", "key": "qa:smoke", "inputs": {}, "concurrency_group": "devices",
      "requester": {"name": "bob-desk", "label": "bob@desk"}, "joiners": [],
      "state": "queued", "reason": "blocked_by_group", "lane": null, "ahead_job_id": null,
      "blocked_by": {"job_id": 409, "group": "devices", "remaining_seconds": 160}, "cancel": null,
      "source": {"mode": "tree", "repo": "org/app", "base_sha": "def456…", "dirty": false, "tree_hash": "1a2b…",
                 "bytes": 48213344, "received_bytes": 48213344, "last_received_at": "2026-09-04T00:50:20Z"},
      "created_at": "2026-09-04T00:50:37Z", "started_at": null,
      "estimate": {"expected_seconds": 540, "source": "preset", "sample_count": 1,
                   "elapsed_seconds": null, "waited_seconds": 95, "remaining_seconds": 540, "wait_seconds": 160,
                   "overdue": false, "stuck": false, "finish_at": "2026-09-04T01:03:52Z"},
      "progress": null, "log_tail": null, "url": "http://macmini:8787/#/jobs/413"
    }],
    "queue_error": null,
    "recent": [{"id": 411, "preset": "gate", "key": "gate:fast", "inputs": {"scope": "fast"},
                "requester": {"name": "bob-desk", "label": "bob@desk"},
                "state": "failed", "exit_code": 1, "job_seconds": 62, "waited_seconds": 21,
                "started_at": "2026-09-04T00:46:01Z", "finished_at": "2026-09-04T00:47:03Z",
                "summary": "2 tests failed", "failed_step": "test", "cancelled_by": null, "timeout_seconds": 1200,
                "transitions": [{"state": "uploading", "at": "2026-09-04T00:45:40Z"}, {"state": "queued", "at": "2026-09-04T00:45:40Z"},
                                {"state": "running", "at": "2026-09-04T00:46:01Z"}, {"state": "failed", "at": "2026-09-04T00:47:03Z"}],
                "url": "…"}],
    "recent_count": 8,
    "recent_error": null,
    "medians": {"gate:full": {"seconds": 369, "wait_seconds": 80, "sample_count": 7}},
    "medians_error": null,
    "hosts": [{"name": "macmini", "source": "local", "sampled_at": "2026-09-04T00:52:08Z", "age_seconds": 4, "stale": false,
               "interval_seconds": 5,
               "os": "darwin", "cores": 10, "load": [3.48, 3.1, 2.9],
               "cpu": {"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
               "memory": {"total_bytes": 25769803776, "used_bytes": 15032385536, "compressed_bytes": 2254857830},
               "gpu": {"util_pct": 13, "mem_used_bytes": 594411520, "mem_total_bytes": null, "source": "ioreg"}, "gpu_note": null,
               "top": [{"comm": "dart", "cpu": 180.4, "rss_mb": 500}],
               "history": [{"at": "2026-09-04T00:47:08Z", "cpu_busy": 18.0, "mem_used_bytes": 14900000000, "gpu_util_pct": 10}]}],
    "hosts_error": null
  }]
}
```

규칙: 시각은 UTC ISO-8601(`Z`). 조회·수집 실패 섹션은 `null` + `*_error` — `queue`·`recent`·`medians`·`hosts` 넷 다 같은 규칙(`recent: null` + `recent_error` 는 「조회 실패」, `recent: []` 는 「완료 잡 없음」으로 다른 모양). 모르는 숫자는 `null`. `position` 은 대기 잡에만 1부터, `running`·`cancelling` 은 null. `finish_at`·`wait_seconds` 는 정지·살아 있는 레인 0·초과 실행이면 null. `log_tail` 은 **유효 토큰(그 잡의·합류자·admin) 요청이고 `running`/`cancelling` 인 잡에만**, 아니면 null. `progress` 는 `queued`/`uploading` 이면 null(0/0 금지), `phase: "materializing"` 이면 `steps: []`. `hosts[].history[]` 는 `history_samples` 개의 `{at, cpu_busy, mem_used_bytes, gpu_util_pct}`(각 값 nullable), 빠진 표본은 그 시각을 건너뛴다(UI 가 점선으로 끊어 그린다). 바이트 필드 이름은 전부 `_bytes` 로 끝난다. M0~M4 는 `pools` 가 한 개. M0 서버는 이 스키마를 처음부터 낸다(`hosts: []`·`medians: {}` 로 시작). 키 삭제·의미 변경은 `schema_version` 을 올리고 CHANGELOG 에 적는다.

## CLI (`cli.py`)

| 명령 | 하는 일 |
|---|---|
| `rcm run PRESET [-f K=V …] [--source tree\|git_ref] [--ref REF] [--by LABEL] [--no-join] [--no-wait] [--exclude PAT]` | 스냅샷 → 제출(합류) → 업로드 → 기본으로 `wait` 이어짐. stdout 에 JSON 한 줄, stderr 에 사람용 |
| `rcm wait --job ID [--timeout S]` | SSE(M1)로 기다리며 stderr 에 위치·스텝·경과·ETA 갱신(TTY 면 한 줄 덮어쓰기), 끝나면 stdout JSON + **종료 코드 0/1/2/3**. SSE 가 끊기면 폴링(2초)으로 폴백(M0 는 폴링만). 서버 연결 실패가 60초 넘게 이어지면 3(`--timeout` 이 더 짧으면 그때 3). **Ctrl-C 는 detach** — 잡은 계속 돌고 `rcm wait --job ID` / `rcm cancel ID` 를 안내한다(합류자면 자기 `joiners[]` 항목만 best-effort 로 뺀다). 잡 취소는 명시적 `rcm cancel` 만 |
| `rcm eta (--job ID \| PRESET [-f K=V])` | 앞선 건수·대기·자기 소요·예상 완료·표본 출처 |
| `rcm top [--watch N] [--json]` | 한 화면(아래) |
| `rcm jobs [--mine] [--state S]` · `rcm logs ID [--follow]` · `rcm cancel ID` · `rcm presets` | 큐·로그·취소·프리셋. `--mine` 은 요청자와 합류자 둘 다. 합류자의 `cancel` 은 자기 대기만 뺀다 |
| `rcm pause` · `rcm resume` | 큐 정지·재개(admin 토큰). `POST /pause`·`/resume` |
| `rcm serve [--config] [--bind] [--port] [--data-dir]` · `rcm check` · `rcm token add\|list\|revoke` · `rcm version` | 서버·셋업·토큰 |

**`rcm run` 흐름**: ① 프리셋·입력을 서버 스키마로 검증(`GET /api/status.presets`, 실패면 서버에 안 보내고 종료 2) ② `tree` 면 스냅샷 규칙으로 파일 목록·`tree_hash` 계산 ③ `POST /jobs` → 합류면 업로드 생략 ④ `PUT …/tree` 업로드(진행률 stderr) ⑤ `rcm wait`. `--no-wait` 면 ③/④ 뒤 JSON 만 찍고 0 으로 끝난다(제출 성공 ≠ 잡 성공 — JSON 의 `state` 를 보라).

**세션에서 쓰는 모양**(`examples/session/ci-gate.sh`):

```bash
out=$(rcm run gate -f scope=full --by "$(whoami)@$(hostname -s)"); rc=$?
case $rc in
  0) echo "gate green: $(jq -r .url <<<"$out")";;
  1) echo "gate red — failed step: $(jq -r .failed_step <<<"$out")"; echo "$(jq -r .summary <<<"$out")";;
  2) echo "cancelled or timed out";;
  *) echo "unknown — check $(jq -r .url <<<"$out")";;
esac
```

## 터미널 `rcm top`

```
━━━ rcm · macmini · 09:52:12 KST · lanes 1 · worker busy
queue — 2
  1. ▶ running  gate:full   org/app @abc123+dirty   ← alice@laptop   remaining 5m 10s   eta 09:57  (measured, n=7)
        step 5/8 · test · 51s · job 59s
        ✔ analyze 12s  ✔ format 3s  ✔ … ▶ test 51s  · lint  · build-web
        [test] 3/9 packages…
  2. · queued   qa:smoke    org/app @def456         ← bob@desk       remaining 8m 20s   eta 10:05  (preset)   group devices
recent
  ❌ gate:fast   ← bob@desk    1m 2s   09:47   2 tests failed (step test)
  ✅ gate:full   ← alice@laptop 5m 50s  09:40
medians: gate:full 6m 9s (n=7)
host  macmini (4s ago)  load 3.48 / 10 cores · CPU 21% (user 17 · sys 4) · mem 14.0 / 24 GB · GPU 13%
      top: dart 180% 500MB · flutter_tester 95% 300MB
```

## 웹 UI (M2)

빌드 도구 없이 `index.html` + `app.js` + `style.css`. 문자열은 영어. `pools[]` 를 순회한다.

**배치·상태·문구·표기 규칙은 `docs/wireframes/web-queue.html` 이 정본이다**(기획 항목 35개 + 「4. 이 화면이 정한 규칙」). 요지: 큐 표 **위에** 요약 세 칸 — **Your jobs**(요청자·합류자 기준 내 잡의 순번·ETA) · **Not moving**(행동 가능한 이유만, `worker_down → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused` 순) · **Host pressure**(CPU·Mem·GPU·load 한 줄) — 를 먼저 보이고, 그 아래 큐 표(Job · Key · Requester · **Reason** · Elapsed · ETA+신뢰도 · Source), 호스트 카드(구간 막대 + 5분 sparkline), 최근 완료 `recent_count` 건, Estimates(접힘). 변형 상태 19개(빈 큐 ≠ 조회 실패, 연결 끊김, stale, 초과, stuck, 워커 다운, 정지, 토큰 거부, 취소 중, 업로드 멈춤, 워크스페이스 준비, …)는 목업의 2절.

- **갱신 규칙**: 처음 `GET /api/status`, 이후 `GET /events` SSE 부분 갱신. SSE 재연결은 **2s → 30s 지수 백오프**, 그 사이 **폴링 10s 고정**, 둘 다 **30s 무응답이면 `Lost connection` 띠**(마지막 상태를 dim 으로 유지). 경과·대기·나이는 `generated_at` 으로 보정한 클라이언트 시계로 1초마다 올리고(30s 넘게 어긋나면 `clock +2m` 칩), 서버 값이 오면 덮어쓴다. 재조회에서 `server.uptime_seconds` 가 줄면 `Server restarted — running jobs were marked lost` 띠. **`schema_version` 이나 `server.version` 이 바뀌면 `UI out of date — reload`(자동 새로고침 1회)**. 탭이 60초 넘게 숨겨지면 정지, 돌아오면 재조회. live 토글로 수동 정지.
- **권한**: 보기는 토큰 없이. 내 잡 강조·로그 tail·로그·취소만 토큰(`localStorage` 의 `rcm.token`, URL 금지, `GET /api/whoami` 로 확인, 401/403 만 저장값 삭제). 토큰 입력은 M2 에 포함한다(오너 결정 15).
- **펼침**: 실행 중 행은 전부 펼친다. 접힘만 잡 id 별로 기억한다(오너 결정 13). 레인 1 이면 워커 필을 하나로 접는다(오너 결정 12).
- 모바일 한 열 · 다크/라이트 `prefers-color-scheme` · 시간대는 `display_timezone` 또는 브라우저 로컬 · 키보드·보조기기 규칙은 목업 4절.

## fail-open 금지 (이 도구의 핵심 규칙)

아무도 안 쳐다보는 보조 화면일수록 조용히 고장나면 **틀린 값을 자신있게 보여주게 된다.**

- 수집·조회에 실패한 칸은 「실패」로 그린다. 실패를 0건·0/0·0초로 그리지 않는다. 모르는 숫자는 `null`.
- `rcm wait` 의 「모른다」(서버 연결 끊김·`lost`·타임아웃)는 3 이지 1 이 아니다. 게이트를 빨강으로 위장하지 않는다.
- 서버 재시작 중 죽은 잡은 `lost` 로 남긴다. 조용히 `queued` 로 되돌리거나 지우지 않는다(같은 트리를 다시 넣는 건 세션의 결정).
- 스텝 시각은 수신 시각임을 스키마가 밝힌다(`timing: "as_received"`).
- 서버 건강(워커 스레드·마지막 오류·호스트 표본 나이)을 JSON 과 화면 머리에 찍는다.
- **요약 줄의 긍정 문구(`Nothing is stuck` · `fine`)는 해당 섹션 조회가 성공했고 값이 전부 있을 때만 그린다.** `queue_error`·연결 끊김·`reason` 없음·호스트 값 일부 null 은 `unknown`/`partial` 이지 `fine` 이 아니다.
- **큐에서 조용히 사라지는 잡은 없다.** 업로드 포기·413·tar 거부·프리셋 소멸도 전부 종료 상태(`cancelled`/`failed` + summary)로 최근 완료에 남는다.
- 시작할 수 없는 잡(정지·모든 레인 다운)과 초과 실행 잡에는 `finish_at` 을 주지 않는다(null → `—`). 하한이 만든 「자신있는 틀린 시각」을 보이지 않는다.
- 순수 계산 모듈은 픽스처 테스트를 갖고 CI 가 매번 돌린다. 테스트가 실제로 빨개지는지 `scripts/mutcheck.py` 로 확인한다.

## 패키지·모듈 구조

이름: PyPI `remote-ci-monitor`(비어 있음, 2026-09-04 확인) · import `remote_ci_monitor` · 명령 `rcm`(+ `remote-ci-monitor`). 레포 이름은 유지한다.

```
pyproject.toml                 # hatchling · requires-python >=3.11 · 런타임 의존성 0
src/remote_ci_monitor/
  cli.py                       # 위 CLI 표
  config.py                    # 서버·클라이언트 설정 로딩 · 프리셋 스키마 검증 · 우선순위 · 오류 메시지
  core/                        # ── 순수: I/O 도 시계도 안 본다. now 는 인자 ──
    model.py                   # Job · JobSpec · Preset · Step · Estimate · HostSample · Pool · StatusModel
    queue.py                   # FIFO · 레인·그룹 · 합류 키 · expected/remaining/wait/finish_at · medians
    progress.py                # 로그 줄 → 마커 파싱 → Progress
    snapshot.py                # 파일 선택 규칙 · tree_hash · 제외 패턴(gitignore 문법)
    inputs.py                  # 프리셋 입력 스키마 검증
    hostparse.py               # macOS: vm_stat/top/ps/ioreg · Linux: /proc/*, ps, nvidia-smi
    gitref.py                  # ref 검증 · ls-remote 출력에서 sha 고르기 · repo url 허용 목록 (M3)
    retention.py               # 보존 정리 규칙(어느 잡의 산출물을 지울 때인가) (M3)
    status.py                  # 조각들 → StatusModel → to_json() (스키마 v1)
    render_text.py             # StatusModel → 터미널 문자열
  store.py                     # SQLite: jobs · events · tokens · samples · 마이그레이션 · claim
  worker.py                    # 레인 스레드: 워크스페이스 · Popen · 로그 · 마커 · 신호 · 정리
  materialize.py               # tree(tar 안전 추출) · git_ref(미러 fetch · 체크아웃 — gitops.py 를 부른다)
  gitops.py                    # git 호출: ls-remote · 미러 fetch(부분 → 전체) · clone · checkout (M3)
  janitor.py                   # 보존 정리 스레드 (M3)
  events.py                    # 이벤트 버스(링 2048 · Last-Event-ID 재생 · lag) (M1)
  templates/                   # rcm init 이 쓰는 server.toml · client.toml (examples/ 와 바이트 동일) (M4)
  hostsample.py                # 샘플러 스레드(명령 실행·파일 읽기 → hostparse)
  server.py                    # ThreadingHTTPServer · 라우트 · 인증 · SSE · hardening
  client.py                    # 세션 쪽: 스냅샷 tar 만들기 · 제출 · 업로드 · SSE/폴링 wait
  web/                         # index.html · app.js · style.css
tests/  fixtures/ · test_*.py
examples/
  server.toml                  # 프리셋 예시(ok / gate / gate-fast / qa-smoke · 주석으로 [[repos]]·deploy). 참고 팀 전용 예시 파일은 두지 않는다(이식성)
  client.toml · session/ci-gate.sh · launchd/ · systemd/
scripts/mutcheck.py · scripts/smoke_install.sh · Dockerfile · CHANGELOG.md · LICENSE
docs/reviews/
```

**의존성 원칙**: 런타임 의존성 0(`http.server` · `sqlite3` · `tarfile` · `subprocess` · `tomllib` · `zoneinfo` · `hmac`). 세션 클라이언트가 어느 컴퓨터에나 수 초에 깔리고, 빌드 머신에 올리는 서버가 가볍고, public 도구의 공급망 면적이 최소가 된다. 전제는 Tailscale/LAN 안의 내부 도구. 외부 바이너리: `git`(체크아웃이면), `tar` 아님(`tarfile`), macOS `vm_stat`/`top`/`ps`/`ioreg`, Linux `ps`/`nvidia-smi`(선택).

## 테스트·품질

- **픽스처**: 마커가 섞인 로그 3종(선언 있음·없음·실패) · 스냅샷용 임시 git 레포(추적·수정·미추적·무시·삭제·심링크) · macOS `vm_stat`/`top`/`ps`/`ioreg` · Linux `/proc/*`/`ps`/`nvidia-smi` 캡처(팀 정보 제거).
- **테스트**(M0~M1): `test_queue.py`(v1 의 21 시나리오 이식 + 그룹 대기 하한 + 합류 키 + `position` 은 대기 잡만 + 살아 있는 레인 0·정지면 `finish_at` null + 초과 실행 `finish_at` null + `reason` + `stuck`) · `test_progress.py`(마커 6 함정 + `phase` + `steps[].ok`) · `test_snapshot.py` · `test_inputs.py` · `test_hostparse.py`(두 OS + GPU) · `test_status_schema.py`(`json.dumps` · null+`*_error` · pools 한 개) · `test_render_text.py`(빈 큐 vs 실패가 다르게) · `test_config.py`(우선순위·프리셋 오류 메시지·시간대) · `test_store.py`(enqueue·claim 원자성·재시작 lost·마이그레이션) · `test_worker.py`(가짜 프리셋 `sh -c` 로 성공·실패·타임아웃·취소·마커) · `test_server.py`(in-process: 401 · 합류 · 413 · tar 탈출 거부 · SSE 한 이벤트 · 로그 인증) · `test_client.py`(제출→업로드→wait 종료 코드 매핑, 서버 끊김 → 3).
- **뮤테이션 확인** `scripts/mutcheck.py`: `src/`+`tests/` 를 tmpdir 에 복사해 변이 하나를 넣고 그 복사본에서 pytest 가 **빨개지는지** 본다. 패턴이 없으면 그 자체로 실패. M0 3종 — ① 잔여 하한 제거 ② 합류 키에서 `inputs` 제외 ③ 재시작 시 `running` 을 `succeeded` 로. M1 2종 — ④ 호스트 stale 판정의 3×interval 제거 ⑤ macOS `top` 첫 표본 사용. 전부 빨개져야 「검증됨」.
- **CI**(`ci.yml`): `unit`(matrix: py 3.11·3.13 × ubuntu, macos-latest 는 3.13 만 — `hostparse`·`snapshot`·`worker` 가 실제 OS 에서 돈다) → `ruff check` · `ruff format --check` · `pytest` · `mutcheck.py`. `secrets` → `gitleaks/gitleaks-action@v3`(개인 계정은 라이선스 불필요, 2026-09-04 확인). 집계 잡 **`test`**: `needs: [unit, secrets]` + `if: always()`, 두 `needs.*.result` 가 모두 `success` 가 아니면 `exit 1`.
- 스타일: ruff(기본 + `I`), 줄 100자, 타입 힌트 필수.

## 패키징·배포 (M4 — 구현됨, 명세 `docs/m4-workplan.md`)

- `pyproject.toml`: hatchling ≥ 1.27 · `dynamic = ["version"]`(단일 출처 `__init__.__version__`) · `license = "MIT"` + `LICENSE`(결정 26) · `dependencies = []` · dev `pytest`·`ruff`·`build` · scripts `rcm`·`remote-ci-monitor` · 패키지 데이터 `web/`·`templates/`(`examples/*.toml` 과 바이트 동일 — 테스트가 잠근다) · sdist 에 `examples/`·`LICENSE`·`CHANGELOG.md`.
- 설정 만들기: `rcm init server`(`$XDG_CONFIG_HOME/rcm` 또는 `~/.config/rcm/server.toml`, 덮어쓰기는 `--force`) · `rcm init client --server URL`(0600, `server = "…"` 정규식 치환). 탐색도 XDG 를 먼저 본다. 템플릿에 `ok` 프리셋(`sh -c "echo ::rcm::step::hello; echo ok"`)이 있어 새 설치가 `rcm run ok` 로 전체 경로를 증명한다.
- `rcm version [--json]`(python · OS · schema_version) · `rcm check` 첫 행 `python`(3.11.4+ tar 필터) · `[[repos]]` 가 있으면 `git` 행(`load_server_config(check_tools=False)`).
- 설치 스모크 `scripts/smoke_install.sh [WHEEL]`: 새 venv 에 wheel 설치 → README `<!-- smoke:begin/end -->` 블록의 `rcm …` 명령이 스크립트 본문에 전부 있는지 대조 → 빌드 머신 절차(init server · token add · serve, 빈 포트) → 세션 절차(init client · check · run ok · top · jobs --json) → 웹 `/` → SIGTERM. CI `smoke` 잡(ubuntu · macos)이 PR 마다 돌리고 집계 `test` 가 `needs` 에 포함(결정 29).
- 릴리스 `.github/workflows/release.yml`: 태그 `v*` → main 위 확인(`fetch` 뒤 `merge-base --is-ancestor`) · 태그 == `__version__` → `python -m build` + `twine check` + METADATA 재검사 → 두 OS 스모크 → GitHub Release(CHANGELOG 절, 있으면 파일만 갱신) → PyPI trusted publishing 은 저장소 변수 `PYPI_PUBLISH = true` 일 때만(environment `pypi`, 결정 27). 절차는 README 「Releasing」.
- Docker: `Dockerfile`(python:3.12-slim · git·openssh·procps·bash · 비루트 `rcm` · `/config`·`/data` · 8787) + `.dockerignore`. Linux 서버 이미지만, 이미지 빌드는 CI 밖. 컨테이너 안 `ps` 의 한계를 README 에 명시.
- 서비스: `examples/launchd/com.remote-ci-monitor.server.plist` · `examples/systemd/rcm-server.service`(M3). 빌드 머신 잠자기 금지 안내.
- README(영어): Install(pipx · uvx · git) → Build machine 3 명령 → Session machine 3 명령 → 프리셋·마커 · 세션 명령 · 웹 UI · 종료 코드 · 보안 · 보존 · 서비스 · Docker · 「why the numbers can be wrong」 · 실기 검증 10단계 · Releasing · Development. `CHANGELOG.md`(Keep a Changelog).

## 마일스톤과 완료 기준

- **M0 — 서버·큐·워커·run/wait** (**완료 2026-09-05**, PR #5~#11, 테스트 150개 · mutcheck 3/3 · 루프백 종료 코드 4종 확인): 모듈 뼈대 · 설정+프리셋 스키마 · SQLite 저장소 · 워커(tree 모드) · 스냅샷 클라이언트 · `POST /jobs`·`PUT tree`·`GET /jobs/{id}`·`/api/health`·`/api/whoami`·`/api/status` · 토큰 · `rcm run`/`wait`(폴링) · 순수 계산 + 테스트 · `mutcheck.py` · CI. `/api/status` 는 스키마 v1 의 **완전한 모양**을 내되 `hosts: []`(샘플러는 M1)·`medians: {}`(표본 쌓이기 전) 같은 빈 값은 허용한다. 완료 기준: 한 머신에서 루프백으로 `rcm run gate` 가 실제 스크립트를 돌리고 종료 코드 0/1/2/3 이 맞다 · 서버를 죽였다 살려도 큐가 남고 실행 중이던 잡은 `lost` 다 · 테스트 전부 통과 · 뮤테이션 3종 빨개짐 · CI 초록.
- **M1 — 보이는 것** (**완료 2026-09-05**, PR #12 · #13, 테스트 258 · mutcheck 5/5 · 실기 검증 12단계 PASS — Tailscale 원격 실기는 오너): 호스트 자원(CPU·RAM·**GPU**) · 중앙값/ETA/합류 · 스텝 마커 진행 · SSE · `rcm eta`/`top`/`jobs`/`logs`/`cancel`/`presets` · `/api/status` 완성. 완료 기준: 다른 컴퓨터에서 Tailscale 로 `rcm run` 을 넣고 `rcm top` 에 위치·ETA·스텝·GPU 가 보인다 · 같은 트리를 두 세션이 넣으면 두 번째는 합류한다.
- **M2 — 웹 UI** (**완료 2026-09-05**, PR #14, 명세 `docs/m2-workplan.md` · 테스트 pytest 270 + node 194 · mutcheck 6/6 · headless Chrome DOM/스크린샷 — 폰·Lost connection·stale 실기는 오너, README 9단계): `docs/wireframes/web-queue.html` 대로 — 요약 세 칸 · 큐 표(Reason·신뢰도) · 호스트 카드(sparkline) · 최근 완료 · Estimates · 변형 19개 · SSE 갱신 · 토큰 입력 · 로그 뷰어·취소(토큰) · 모바일 · 다크/라이트. 완료 기준: 폰에서 큐·스텝·자원이 읽히고, **서버를 끊으면 `Lost connection` 띠가, 샘플러만 멈추면 `stale` 배지가** 뜬다.
- **M3 — 운영** (**완료 2026-09-05**, 명세 `docs/m3-workplan.md` · 리뷰 `docs/reviews/2026-09-05-codex-m3-design.md`): `git_ref` 소스(제출 시 sha 확정 · 미러 · 로컬 clone) · 프리셋 `repo` · concurrency 그룹 e2e(레인 2 에서 실제 프로세스 두 개가 직렬화, 그룹 없는 잡은 병행) · 보존 정리(janitor · DB v2 · `metadata_retention_days`) · 신호 e2e(손자 프로세스 · TERM 무시 → KILL · 타임아웃) · `examples/launchd/` · `examples/systemd/` · `read_auth = basic` 확정 · mutcheck 8종. macOS CI 잡은 M0 부터 있다. 완료 기준: 배포 프리셋이 원격 ref 로 돌고(로컬 bare 레포로 e2e — 실제 원격·자격은 오너 실기), QA 두 개가 그룹으로 직렬화된다.
- **M4 — 배포·문서** (**완료 2026-09-06**, 명세 `docs/m4-workplan.md` · 리뷰 `docs/reviews/2026-09-06-codex-m4-design.md`): 동적 버전 · MIT · `rcm init` · `rcm version/check` · 설치 스모크(CI 잡) · 릴리스 워크플로 · Dockerfile · README 재구성 · CHANGELOG. 완료 기준: 새 머신에서 README 만 보고 5분 안에 `rcm run` 이 된다 — `scripts/smoke_install.sh` 가 새 venv 에서 README 명령을 그대로 돌려 매 PR 마다 증명한다(ubuntu · macOS). PyPI 실제 게시는 오너가 publisher 를 등록하고 변수를 켠 뒤.
- **M5a — 확장 1** (**완료 2026-09-06**, 명세 `docs/m5-workplan.md`): 우선순위(low/normal/high · 프리셋 기본이 비-admin 상한 · `rcm bump` · 합류 시 상향) · 내용 주소 스냅샷 캐시(manifest → 빠진 blob 만 · `X-RCM-Tree: blobs` · blob GC · `--no-cache`) · 알림(`[[notify]]` argv/url · 정확히 한 번 · 재시작 스캔). 완료 기준 ①②③: e2e 로 잠금(high 가 normal 보다 먼저 · 1 MB 난수 트리 두 번째 업로드 uploaded_bytes ≤ 4 KB · 알림 잡당 한 번). DB v3 · 스키마 v1 에 추가 키(`queue[].priority` · `presets[].priority` · `source.uploaded_bytes/cached_bytes` · `server.snapshot_cache/notify_failures`).
- **M5b — 확장 2 (원격 워커)**: 빌드 머신 여러 대 — `pools[]` 다중화 · 워커 토큰 · `/worker/*` · `rcm worker` · `runner.py`. 4 PR(명세 M5b-1~4).
- **M6 — GitHub 백엔드**(보류): Actions run 관찰·dispatch(v1.1 설계 참조). 2026-09-04 「GitHub 비의존」 방향과 상충 — 오너 결정 30 뒤에.

## 결정 항목 (2026-09-04, 전부 확정)

| # | 결정 | 내용 |
|---|---|---|
| 1 | 방향 | **로컬 잡 서버**. 도구가 큐·실행을 소유한다. GitHub 관찰/디스패치 백엔드는 M5. 기존 Actions 러너는 배포·QA 용으로 유지 |
| 2 | 코드 전달 | 게이트는 **작업 트리 스냅샷**(미커밋 포함) 기본, 배포·릴리스는 `git_ref`. 전송은 rsync/SSH 가 아니라 같은 HTTP·같은 토큰의 tar 업로드 |
| 3 | 명령 범위 | **등록된 프리셋만**. 임의 명령 없음, 셸 보간 없음 |
| 4 | 쓰기 인증 | **클라이언트별 bearer 토큰**(서버엔 sha256 만). 읽기는 `none` 기본(Tailscale/LAN 전제), 로그는 항상 토큰 |
| 5 | 서버 노출 | Tailscale/LAN 안. TLS 는 서버가 안 한다. `basic` 은 TLS 프록시 뒤 전용 옵션 |
| 6 | 결과 전달 | 폴링/SSE 명령(`rcm wait` 종료 코드)만. webhook · 커밋 status · PR 코멘트는 범위 밖 |
| 7 | 자원 | CPU·RAM·GPU 를 서버 프로세스가 직접 샘플링(macOS `ioreg`, Linux `nvidia-smi`). 폴링 기본 5초, 하한 2초 |
| 8 | 언어·런타임 | Python 3.11+, 런타임 의존성 0, TOML 설정, 내부 UTC |
| 9 | 표본 | 성공 잡만 기본(`sample_policy`), `sample_count` 노출, 스키마 `pools[]` 축 |
| 10 | 코드 언어 | 식별자·README·UI·CLI 도움말 영어, 주석·docstring·계획서·커밋 본문 한국어 |
| 11 | CI | matrix `unit` + 집계 `test`(필수 체크 이름), gitleaks v3, `mutcheck.py` |
| 12 | 레인 1 표시 | 레인이 1 이면 워커 필을 하나로 접는다(`worker busy #412`). Reason 의 `blocked` 는 자연히 안 나온다 |
| 13 | 행 펼침 | 실행 중 행은 **전부** 펼친다. 접힘만 잡 id 별로 기억한다(localStorage, 큐에 없는 id 는 정리) |
| 14 | 최근 완료 | **건수** 기준 `recent_count = 8`(24시간이 아니다). 표본 정책과 무관하게 전부 보인다 |
| 15 | 토큰 입력 | 웹의 토큰 입력(로그 tail·로그·취소)은 **M2 에 포함**한다. 폰에서 로그 확인과 취소가 실제 운영 행동이다 |
| 16 | 합류자 취소 | 합류자가 취소하면 **자기 대기만 빠진다**(`rcm wait` 중단, 잡은 원 요청자 것으로 유지). 원 요청자가 취소하면 합류자의 `rcm wait` 는 2 로 끝나고, 취소 대화상자가 대기 세션 수를 미리 알린다 |

| 17 | `rcm run` Ctrl-C | **detach** — 잡은 계속 돌고 `rcm wait --job N` / `rcm cancel N` 을 안내(종료 코드 3). 합류자면 자기 `joiners[]` 항목만 best-effort 로 뺀다. 잡 취소는 명시적 `rcm cancel` 만. (Codex M0 리뷰 추천값으로 구현, **오너 확인 대기**) |
| 18 | 부분 업로드 재개 | **M0 범위 밖**. 끊기면 `cancelled` + `upload interrupted after N MB` 로 남기고 새 `rcm run` 으로 다시 제출. (Codex M0 리뷰 추천값으로 구현, **오너 확인 대기**) |
| 19 | macOS 메모리 used | `active + wired + compressor`(Activity Monitor 「Memory Used」). `top` 의 PhysMem used 와 다르다. (Codex M1 리뷰, 추천값으로 구현, **오너 확인 대기**) |
| 20 | GPU 없는 머신의 M1 완료 | `ioreg`/`nvidia-smi` 로 못 읽는 머신은 `gpu: null` + `gpu_note` 로 **통과**로 본다. 숫자는 Apple Silicon · NVIDIA 에서만. (Codex M1 리뷰, **오너 확인 대기**) |
| 21 | 연결 끊김 표시 | `Lost connection` 띠 + 나이 증가만. **화면 전체를 dim 하지 않는다**(dim 은 호스트 stale 에만). (Codex M2 리뷰, **오너 확인 대기**) |
| 22 | 웹 토큰 저장 | `localStorage` 에 둔다(M2 허용) + `index.html` 에 CSP 강제 + README 에 「공용 브라우저에서 쓰지 마라, XSS 면 토큰이 샌다」 명시. (Codex M2 리뷰, **오너 확인 대기**) |
| 23 | `read_auth = basic` 과 웹 | **M3 확정**: 진짜 HTTP Basic. 사용자명 = 토큰 이름, 비밀번호 = 토큰(별도 자격 저장소 없음). 읽기 라우트만 Basic 을 받고 쓰기는 Bearer 만(CSRF). TLS 프록시 뒤 전용. 브라우저 로그아웃은 불가하므로 README 에 안내. (Codex M3 리뷰, **오너 확인 대기**) |
| 24 | git_ref 워크스페이스 모양 | 로컬 clone(`.git` 유지, detached) — `git describe` 가 되고 submodule 은 스크립트가 `git submodule update --init`. `git archive`(더 단순·`.git` 없음)는 배포 스크립트가 `.git` 을 안 쓸 때만 나은 선택. (Codex M3 리뷰, **오너 확인 대기**) |
| 25 | 잡 메타데이터 보존 | `metadata_retention_days = 180` 뒤 잡 행·이벤트·합류자 삭제(산출물이 먼저 지워진 잡만). 감사 요구가 있으면 늘린다. `sample_days`·`retention_days_*` 보다 짧으면 설정 오류. (Codex M3 리뷰, **오너 확인 대기**) |
| 26 | 라이선스 | **MIT**(`LICENSE` · `license = "MIT"`). 공개 PyPI 패키지엔 라이선스가 필요하다. (**오너 확인 대기**) |
| 27 | PyPI 게시 | trusted publishing(OIDC, 토큰 없음). 오너가 PyPI 에 pending publisher(owner `monocsp` · repo `remote_ci_monitor` · workflow `release.yml` · environment `pypi`)를 만들고 저장소 environment `pypi` 와 변수 `PYPI_PUBLISH=true` 를 켜기 전까지 워크플로는 PyPI 잡을 건너뛴다(GitHub Release 는 항상). (**오너 확인 대기**) |
| 28 | 태그 룰셋 | `v*` 태그 생성·삭제를 제한하는 룰셋은 없다. 관리자만 만들게 하려면 태그 룰셋을 추가한다. (**오너 확인 대기**) |
| 29 | 스모크 필수 체크 | PR 집계 `test` 에 ubuntu·macOS 스모크를 모두 포함한다(macOS unit 잡이 이미 필수라 러너 리스크가 새로 늘지 않는다). 릴리스 워크플로도 둘 다 필수. (Codex M4 리뷰는 macOS 를 비필수로 제안 — **오너 확인 대기**) |
| 30 | GitHub 백엔드 | M5 에서 빼고 M6 으로 보류. 제품 방향이 「GitHub 비의존」이라 여전히 원하는지 확인. (**오너 확인 대기**) |
| 31 | 우선순위 | low/normal/high 세 단계. 프리셋 `priority` 가 그 프리셋 잡의 기본이자 비-admin 상한. 기아 보정 없음(화면이 보여준다). (Codex M5 리뷰, **오너 확인 대기**) |
| 32 | 캐시 blob 공유 범위 | 기본 `snapshot_cache_scope = "global"`(같은 내용은 클라이언트 간 공유 — `missing` 목록으로 존재 여부를 알 수 있다). 토큰별 분리는 `"token"`. (Codex M5 리뷰, **오너 확인 대기**) |

12~16 은 `docs/wireframes/web-queue.html` 「6. 오너에게 묻는 것」의 5개를 2026-09-04 오너가 확정한 것이다. 17~18 은 `docs/reviews/2026-09-04-codex-m0-design.md` 가 사람 결정이라고 본 것을 추천값으로 구현한 것이다. 바꾸려면 여기서 고친다.

## 참고 구현과 이전 설계

- `fmmc-tech/dolomood-app-renew`(로컬에선 `dolomood-ci-monitor` 워크트리)의 `scripts/remote_ci.sh`(dispatch·가드·합류·대기) · `ci_queue.py`(큐·중앙값·잔여 21 자기검증) · `ci_top.py`(진행률·파서·렌더 18 자기검증) · `docs/renew-guide/ci-cd/30-remote-dispatch.md`. **가져오는 것**: 큐·ETA 수식과 하한 · 실패/빈 큐 분리 · `top` 두 번째 표본 · 파서 픽스처 · 취소 대신 합류 · 시뮬 공유 직렬화(concurrency 그룹) · 요청자 라벨 `계정@호스트`. **버리는 것**: GitHub API 전부 · run 이름 규약 · `gh` · KST 상수 · `~/actions-runner` 판별 · 팀 스크립트 이름.
- v1/v1.1(GitHub 경로) 계획은 커밋 `9abef42`·`15e8220`. jobs API 함정 6개·rate limit 예산·큐 판정 규칙은 M5 GitHub 백엔드 때 그대로 쓴다.
- 수용 검사(2026-09-06): `docs/acceptance/plan-conformance-checklist.md`(A~M 90항목) · `docs/acceptance/user-checklist.md`(페르소나 3) · 보고서 `docs/acceptance/reports/` — 계획서 준수 PASS 93 · PARTIAL 7 · FAIL 0, 사용자 관점 막힘 2·헷갈림 9 → 전부 반영(PR #20).
- Codex 크로스리뷰 기록(M5): `docs/reviews/2026-09-06-codex-m5-design.md`(확장 명세 `docs/m5-workplan.md` — 제출 시 sha 확정 유지 · blob 경합/GC · 존재 오라클 · 알림 unique claim · pools 다중화 · M5b 4 PR).
- Codex 크로스리뷰 기록(M4): `docs/reviews/2026-09-06-codex-m4-design.md`(배포 명세 `docs/m4-workplan.md` — PEP 639 · XDG 탐색 · 원자적 쓰기 · README↔스모크 대조 · 릴리스 태그 검증 · Docker 패키지).
- Codex 크로스리뷰 기록(M3): `docs/reviews/2026-09-05-codex-m3-design.md`(운영 명세 `docs/m3-workplan.md` — 제출 시 sha 확정 · `--shared` 폐기 · 부분 fetch · URL 허용 목록 · Basic 은 읽기만 · 메타데이터 보존 · janitor symlink/health).
- Codex 크로스리뷰 기록(M2): `docs/reviews/2026-09-05-codex-m2-design.md`(웹 UI 명세 `docs/m2-workplan.md` — XSS/CSP · 포커스 보존 · EventSource 503 · fail-open 문구 · Chrome 테스트).
- Codex 크로스리뷰 기록: `docs/reviews/2026-09-04-codex-plan-v1.md`(v1 설계) · `docs/reviews/2026-09-04-codex-github-dependency.md`(방향 전환) · `docs/reviews/2026-09-04-codex-web-queue.md`(웹 큐 화면 디자인) · `docs/reviews/2026-09-04-codex-m0-design.md`(v2.1 정합성 + M0 구현 결정 — Ctrl-C detach·부분 업로드 재개 제외는 추천값으로 구현, 오너 확인 대기) · `docs/reviews/2026-09-05-codex-m1-design.md`(M1 명세 `docs/m1-workplan.md` — 캐시·SSE 폴백·재조회 합치기·macOS 메모리 정의·GPU 집계). 서브에이전트 리뷰: `docs/reviews/2026-09-04-subagent-spec-gaps.md`(기획 누락 30건 — v2.1 의 데이터 모델 변경 근거) · `docs/reviews/2026-09-04-subagent-reference-comparison.md`(제품 비교 리서치).

---

## 세션 시작 프롬프트 (M1 — 복사해서 붙여 넣기)

```
이 레포(remote_ci_monitor)는 빌드 머신 한 대에 여러 컴퓨터의 세션이 잡을 던지면 서버가 자기 큐로 순차 실행하고,
대기 위치·예상 완료·스텝 진행·CPU/RAM/GPU 를 보여주며, 결과를 종료 코드로 돌려주는 로컬 잡 서버다.
GitHub 에 의존하지 않는다(git 원격은 배포용 소스 모드에서만).

정본 — 이 순서로 먼저 끝까지 읽어라:
  1. PLAN.md (v2.1): 구조 · 잡 모델 · 큐 규칙 · 프리셋 · 워커 · 스텝 마커 · 호스트 자원 · 보안 · 서버 API · 저장소 · 설정 ·
     /api/status 스키마 v1 · CLI · 터미널 rcm top · 테스트·CI · 마일스톤 · 결정 항목(1~18).
  2. docs/wireframes/web-queue.html: 웹 큐 화면의 정본(항목 35개 · 「4. 규칙」). M1 의 /api/status 완성과 rcm top 은 이 화면이
     요구하는 필드·문구를 그대로 낸다. 화면을 눈으로 보려면:
     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --window-size=1240,2500 \
       --virtual-time-budget=8000 --screenshot=/tmp/q.png "file://$PWD/docs/wireframes/web-queue.html"
  3. docs/reviews/*.md: 왜 그렇게 정했는지. 특히 2026-09-04-codex-m0-design.md(M0 구현 결정 · 오너 확인 2건 = 결정 17·18).
  4. 지금 있는 코드 (M0, 2026-09-05 dev 에 머지, PR #5~#11): src/remote_ci_monitor/{config,store,worker,materialize,server,client,cli}.py ·
     core/{model,inputs,queue,progress,snapshot,status,render_text}.py · tests/ 150개 · scripts/mutcheck.py(3종) · README.md.
     시작하자마자 `pip install -e ".[dev]" && ruff check . && pytest && python scripts/mutcheck.py` 가 초록인 걸 확인해라.

지금 상태: 루프백에서 rcm run 이 종료 코드 0/1/2/3 을 맞게 낸다. /api/status 는 스키마 v1 의 완전한 모양을 내지만
  hosts: [] 이고(샘플러 없음), 요청마다 DB 에서 다시 만들며(이벤트 갱신 없음), SSE 가 없고, rcm wait 는 2초 폴링이다.
  rcm eta/top/jobs/logs/presets 는 없다. core/queue.eta_for_new · core/render_text.render · store.markers_for ·
  GET /jobs/{id}/log 의 X-RCM-Next-Offset/X-RCM-More 는 M1 용으로 이미 있다.

이번 세션 목표 — M1 「보이는 것」(PLAN 「마일스톤과 완료 기준」의 M1 그대로). 단계마다 dev 에서 feature 브랜치를 파고 dev 로 PR.

  0. 먼저 정할 것 — 아래 넷은 코드를 쓰기 전에 `codex exec --sandbox read-only` 크로스리뷰를 받고(프롬프트·원문·반영표를
     docs/reviews/2026-09-05-codex-m1-design.md 에 남겨라) 그 다음 오너에게 물어라. 추천값은 병기했다.
     - 이벤트 갱신 모델: 상태 모델을 요청마다 재구성하는 대신 이벤트(job_changed · job_finished · marker · host_sample · server)로
       다시 만들어 참조를 교체한다. 워커·업로드·janitor·샘플러가 App 의 이벤트 버스에 쏘고, SSE 는 그 버스를 구독한다.
       동시 SSE 연결 상한 16 초과는 503 이 아니라 「폴링으로 폴백하라」는 응답이어야 한다. 추천: 큐 하나 + 구독자 리스트, 이벤트마다 id.
     - 신뢰도 배지: estimate.confidence(high|med|low|group wait|overdue)를 서버가 싣는다(키 추가는 schema_version 유지).
       추천: 싣는다 — UI 와 rcm top 이 어긋날 수 없다. core/queue.confidence() 가 이미 있다.
     - hosts[].history[](60표본)의 보관: 서버 메모리(재시작하면 비움) vs DB. 추천: 메모리 — 5분치 스파크라인이 목적이다.
     - GPU 파서 픽스처: 실제 Mac mini 의 `ioreg -r -d 1 -w 0 -c IOAccelerator` 와 `top -l 2 -n 0 -s 1`, `vm_stat`, `ps -Aro %cpu=,rss=,comm=`
       출력을 오너에게 받아 tests/fixtures/host/ 에 넣는다(팀 정보 제거). Linux 는 /proc/loadavg · /proc/meminfo · /proc/stat ·
       `ps -eo %cpu=,rss=,comm= --sort=-%cpu` · nvidia-smi 캡처(CI 의 ubuntu 러너에서 직접 떠도 된다).

  1. feat/m1-hostparse: core/hostparse.py — macOS(vm_stat · top 두 번째 표본만 · ps · ioreg PerformanceStatistics 의
     Device Utilization % / In use system memory) · Linux(/proc/* · ps · nvidia-smi 있을 때만). 값 없는 칸은 null(0 아님),
     부분 실패는 그 칸만. 두 OS 의 실제 캡처를 픽스처로 잠근다(test_hostparse.py).
  2. feat/m1-hostsample: hostsample.py 샘플러 스레드 — interval_seconds(하한 2) · gpu auto/off · top_processes · history_samples.
     hosts[] 의 sampled_at · age_seconds · stale(3×interval) · interval_seconds · history[] · gpu_note · 전부 실패면 hosts_error.
     M0 의 hosts: [] 를 실제 표본으로 바꾼다.
  3. feat/m1-events: 이벤트 버스 + 상태 모델 참조 교체 + GET /events(SSE: 큐 변화·호스트 표본·server) +
     GET /jobs/{id}/events(SSE: 상태·마커·요약 — 로그 줄은 아님). Last-Event-ID 재연결 · 동시 연결 상한 · SSE 소켓 타임아웃 별도 ·
     keep-alive 코멘트 15초. test_server 에 「SSE 한 이벤트」와 「상한 초과 폴백」을 넣어라.
  4. feat/m1-cli: rcm eta (--job ID | PRESET [-f K=V]) · rcm top [--watch N] [--json](core/render_text 사용, --json 은 /api/status 그대로) ·
     rcm jobs [--mine] [--state S](--mine 은 요청자+합류자) · rcm logs ID [--follow](offset 증분) · rcm presets ·
     rcm wait 를 SSE 우선 + 끊기면 2초 폴링 폴백으로. stderr 진행 줄은 위치·스텝·경과·ETA(TTY 면 한 줄 덮어쓰기).
  5. 완료 기준(PLAN M1): 다른 컴퓨터에서 Tailscale 로 rcm run 을 넣고 rcm top 에 위치·ETA·스텝·GPU 가 보인다 ·
     같은 트리를 두 세션이 넣으면 두 번째는 합류한다. 실제 Mac mini 에서 확인하는 절차(서버 설정 · 토큰 · Tailscale IP 바인드 ·
     랩탑에서 rcm check → rcm run → rcm top)를 README 에 적고, 네가 못 하는 실기 확인은 오너가 할 일로 명시해라.

지킬 것:
  - 「반드시 지킬 것 — 이식성」: 머신 이름·팀 명령을 코드에 박지 마라. 핵심 경로에서 GitHub 을 부르지 마라. macOS·Linux 둘 다.
  - 「보안」: 쓰기는 토큰, 로그·SSE 의 로그성 데이터는 토큰, 오류 응답에 스택·토큰·경로 금지. 바인드 기본 127.0.0.1.
  - 「fail-open 금지」: 모르는 값은 null, 수집 실패는 null + hosts_error, stale 은 stale 로, wait 의 「모른다」는 3.
  - 순수 계층(core/)은 I/O 도 시계도 안 본다. hostparse 는 실제 캡처 픽스처가 있어야 한다.
  - 스키마 v1 은 키 **추가만**. 삭제·의미 변경이 필요하면 멈추고 물어라.
  - mutcheck 에 M1 변이를 최소 1개 더해라(예: stale 판정의 3×interval 제거 · top 첫 번째 표본 사용). 넷 이상 빨개져야 「검증됨」.
  - 식별자·UI 문자열·README·CLI 도움말은 영어, 주석·docstring 은 한국어. 커밋 메시지는 Conventional Commits.
  - 브랜치 정책: main·dev 직접 push 금지. `git switch dev && git pull` → `git switch -c <type>/<topic>` → dev 로 PR. 워크트리를 써도 된다.
  - gh 계정·머지: 활성 계정이 pcs-fmmc 로 되돌아가는 일이 있다. 모든 GitHub 동작은 토큰을 고정해라:
      TOK=$(gh auth token --user monocsp); GH_TOKEN=$TOK gh api user --jq .login   # monocsp 인지 확인
      GH_TOKEN=$TOK git push -u origin <branch>; GH_TOKEN=$TOK gh pr create --base dev …
    `gh pr merge` 는 자동 모드 분류기가 막는다. CI 초록을 확인한 뒤 REST 로 머지하고 브랜치를 지워라:
      GH_TOKEN=$TOK gh api -X PUT repos/monocsp/remote_ci_monitor/pulls/N/merge -f merge_method=merge -f commit_title="<제목> (#N)"
      GH_TOKEN=$TOK gh api -X DELETE repos/monocsp/remote_ci_monitor/git/refs/heads/<branch>
  - 기술 결정이 필요하면 codex 크로스리뷰 → docs/reviews/ 기록 → 오너. 결정 항목 확정값에서 벗어나야 할 이유가 생기면 그때 물어라.

끝나면 짧게 보고해라: 만든 것 · 테스트 수와 뮤테이션 결과(몇 종) · 루프백에서 rcm top 이 보여준 것(호스트 표본 포함) ·
올린 PR 과 머지 여부 · 오너가 실기로 확인할 절차 · M2(웹 UI)에서 먼저 정해야 할 것.
```
