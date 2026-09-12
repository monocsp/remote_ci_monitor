# remote_ci_monitor — 계획서 (v2.11, 2026-09-09)

> 정본이다. 세션을 시작하면 끝까지 읽는다. 웹 큐 화면의 배치·상태·문구는 `docs/wireframes/web-queue.html` 이 정본이다(「웹 UI (M2)」).
> **v2 는 방향 전환이다.** v1(오전)은 GitHub Actions 를 컨트롤 플레인으로 쓰는 관찰+디스패치 도구였다. 오너 검토에서 「GitHub 에 의존하지 않으면 좋겠다」가 나왔고, Codex 크로스리뷰(`docs/reviews/2026-09-04-codex-github-dependency.md`)를 거쳐 **도구가 큐와 실행을 직접 소유하는 로컬 잡 서버**로 바꿨다. GitHub 경로 설계는 커밋 `15e8220`(v1.1)에 남아 있고 M5 의 GitHub 백엔드를 만들 때 참고한다.
> **v2.1 은 웹 큐 화면 기획(v1.3)의 「5. PLAN 반영 제안」을 데이터 모델·큐 규칙·스키마·API·설정에 반영한 것**이다. 바뀐 곳: 잡 상태 `cancelling` · 합류자 `joiners[]` · `position`/`reason` 규칙 · 살아 있는 레인 수로 대기 계산 · 그룹 대기 하한 · 신뢰도 규칙 · `stuck` 판정 · 스키마 v1 필드 추가 · `GET /api/whoami`·`POST /pause`·`/resume` · 설정 키 6개 · 「웹 UI (M2)」 절 교체 · 오너 결정 5개(12~16).
> **v2.2** 는 M3(운영) 반영: `git_ref` 소스 모드의 실제 동작(제출 시 sha 확정 · 미러 · 로컬 clone) · 프리셋 `repo` · 보존 정리(janitor · `metadata_retention_days`) · `read_auth = basic` 의 확정(결정 23) · 서비스 파일. 명세 `docs/m3-workplan.md`, 리뷰 `docs/reviews/2026-09-05-codex-m3-design.md`.
> **v2.3** 은 M4(배포·문서) 반영: 동적 버전 · MIT · `rcm init` · 설치 스모크 · 릴리스 워크플로 · Docker · README 재구성. 명세 `docs/m4-workplan.md`, 리뷰 `docs/reviews/2026-09-06-codex-m4-design.md`.
> **v2.4** 는 M5a(우선순위 · 내용 주소 스냅샷 캐시 · 알림) 반영 + 수용 검사(`docs/acceptance/`) 결과. **v2.5** 는 M5b-1(풀 축 · DB v4). 명세 `docs/m5-workplan.md`, 리뷰 `docs/reviews/2026-09-06-codex-m5-design.md`.
> **v2.6** 은 M5b 완료 · v0.2.0 릴리스 · 오너 결정 30(GitHub 백엔드 폐기). **v2.7** 은 M5c(내부망 자동 발견) 계획, **v2.8** 은 그 완료(v0.2.2) — 계획서의 마지막 기능 항목.
> **v2.9** 는 웹 화면 손질(2026-09-09 오너 요청): 도는 잡의 **전체 진행 막대** · 상세는 **기본 접힘**(결정 13 개정) · 최근 완료 행에도 잡 번호. 「웹 UI (M2)」 절과 목업(`docs/wireframes/web-queue.html`)이 정본이다.
> **v2.10** 은 마일스톤이 끝난 뒤의 운영 개선 둘 — M5f(부하를 보는 병렬 레인, 결정 39~50)와 **M5g(증거를 얼마나·얼마나 오래 들고 있을지, 결정 51~62)**. 명세 `docs/m5f-workplan.md` · `docs/m5g-workplan.md`.
> **v2.11** 은 M5h(끝난 잡이 무엇이 왜 깨졌는지 말하게 한다, 결정 63~72) — 다른 머신의 세션이 운영 인스턴스를 쓰며 남긴 사용기 여섯 개에서 나왔다. 명세 `docs/m5h-workplan.md`, 구현 `docs/m5h-implementation.md`.
> ⛔ 는 사람이 정해야 하는 항목이다. 현재 열린 ⛔ 는 없다(「결정 항목」 17~29 · 31 · 32 · 39~50 · 51~62 · 63~72 · **73~83** 은 추천값으로 구현하거나 구현 예정, 오너 확인 대기; 30 은 확정).

## 한 줄

**빌드 머신 한 대(예: Mac mini)에 여러 컴퓨터의 세션이 잡을 던지면, 서버가 자기 큐로 순차 실행하고, 대기 위치·예상 완료·지금 스텝·CPU·메모리·GPU 를 웹과 터미널로 보여주며, 결과를 종료 코드로 돌려준다.** GitHub 에 의존하지 않는다.

## 왜 만드나

빌드 머신이 한 대뿐인 팀은 CI·QA 요청이 여러 사람·여러 세션에서 겹친다. 「내 차례가 언제 오는지」「앞 잡이 걸린 건지 느린 건지」「머신이 지금 버거운지」「끝났는지, 성공인지」를 한 화면·한 명령으로 알고 싶다.
참고 구현(한 팀의 비공개 저장소에 있던 dispatch 래퍼·큐 스크립트·top 스크립트)은 이걸 GitHub Actions dispatch 위에서 풀었다. 잘 돌지만 큐·실행·진행 데이터·코드 전달이 전부 GitHub 에 묶여, 폴링 지연·rate limit·「dispatch 는 원격 HEAD 만 본다」(미커밋 변경이 조용히 빠져 초록이 가짜가 된다) 같은 함정을 가드로 막아야 했다. v2 는 **큐와 실행을 도구가 소유**해서 그 함정을 없애고, 세션이 **미커밋 작업 트리를 그대로 보내** 검사할 수 있게 한다. 기존 GitHub Actions 러너는 배포·QA 용으로 그대로 둔다.

## 반드시 지킬 것 — 이식성

이 레포는 public 이고 다른 사람이 자기 빌드 머신에 쓴다.

- 특정 머신·계정·팀 규약·팀 명령을 코드에 박지 않는다. 금지 예: 특정 러너 이름, 시간대 KST 고정, 팀 CI 스크립트 이름, 시뮬레이터·Flutter 가정. **실행할 명령은 전부 설정의 프리셋**으로 받고, 참고 팀의 프리셋은 `examples/` 에만 둔다.
- 핵심 경로(제출·큐·실행·진행·결과)는 GitHub 을 부르지 않는다. `git_ref` 소스 모드가 git 원격을 fetch 하는 건 git 의존이지 GitHub 의존이 아니다(어느 호스팅이든 된다).
- macOS 와 Linux 빌드 머신을 둘 다 지원한다(호스트 자원 수집·프로세스 실행). Windows 는 범위 밖으로 명시한다.
- 설치가 한 줄이어야 한다: `pipx install remote-ci-monitor` / `uvx remote-ci-monitor`. 서버와 세션 클라이언트가 같은 패키지다. 런타임 의존성 0.
- 세션 쪽은 SSH·rsync 데몬 같은 두 번째 접속 경로를 요구하지 않는다. 코드 전달도 **같은 HTTP·같은 토큰**으로 한다.
- 시크릿(토큰)은 환경변수·설정 파일로만 받고 절대 커밋하지 않는다. `.gitignore` 와 gitleaks 를 CI 에 건다. 잡 페이로드에 시크릿을 싣지 않는다(빌드 머신에 상주).
- README·CLI 도움말·식별자는 영어, **주석·docstring·이 계획서·커밋 본문·리뷰 기록은 한국어**(2026-09-04 오너 결정).
- **웹 UI 문자열은 한국어가 기본이고 영어를 고를 수 있다**(2026-09-08 오너 결정 36). 화면 오른쪽 위에서 바꾼다. 식별자·프리셋 이름·명령·커밋 해시·저장소 주소는 어느 언어에서도 번역하지 않는다. 서버가 내려보내는 문장은 서버의 것이라 페이지가 번역하지 않는다 — 무엇을 서버가 말하고 무엇을 페이지가 말하는지 명세에서 가른다.

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
- 일상 흐름: 브랜치마다 워크트리를 하나 판다 — `git worktree add -b feat/<topic> ../remote_ci_monitor-<topic> origin/dev` → 작업 · 커밋 → `gh pr create --base dev` → 머지 → `git worktree remove`. 이미 있는 워크트리의 브랜치를 갈아타지 않는다(다른 세션이 쓰고 있을 수 있다). 릴리스는 `gh pr create --base main --head dev`. (이 레포의 개발 자체는 GitHub 에서 한다 — 도구의 런타임이 GitHub 에 의존하지 않는다는 것과 다른 얘기다.)

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
- **레인**: `server.lanes`(기본 1). 레인이 비어 있어도 **concurrency 그룹**이 겹치면 못 올라간다 — 프리셋에 `concurrency_group = "devices"` 를 주면 같은 그룹의 잡은 동시에 하나만 돈다(시뮬레이터·에뮬레이터를 공유하는 QA·배포용). 막고 있는 잡을 `blocked_by: {job_id, group, remaining_seconds}` 로 보여준다. 그룹은 제출 시점에 잡에 박힌 `concurrency_group` 을 쓴다. 레인 1 이면 `blocked_by` 는 자연히 안 나온다(오너 결정 12). **레인 2 부터는 호스트 CPU 가 `cpu_max_percent`(기본 80) 아래일 때만 잡을 집는다**(M5f) — 레인 1 은 게이트를 안 지나므로 큐는 어떤 부하에서도 계속 움직인다.
- **살아 있는 레인**: 대기 계산은 `server.lanes` 가 아니라 **`state ≠ down` 인 워커 수**로 한다. 그 수가 0 이거나 `server.paused` 면 대기 잡의 `wait_seconds`·`finish_at` 은 **null**(ETA `—`) — 시작할 수 없는 잡에 시각을 주지 않는다. **부하로 보류된(`held`) 레인은 ETA 그리디에서 뺀다**(M5f) — 언제 열릴지 모르는 레인에 기대면 「자신있는 틀린 시각」이 된다. 레인 배정은 레인 **번호**가 아니라 `(worker, lane)` 로 센다(로컬 레인 2 와 `build-02/2` 가 뭉개지지 않게).
- **이유 `reason`**: 잡이 지금 왜 이 상태인지 서버가 계산해 싣는 **단일 표시 사유**다(`estimate.overdue`·`estimate.stuck` 은 근거 플래그로 따로 싣는다). `running` · `waiting_for_lane`(`ahead_job_id` = 가장 먼저 비는 레인의 잡) · `blocked_by_group` · `uploading` · `upload_stalled`(`upload_stall_seconds` 동안 바이트가 안 옴) · `materializing` · `overdue` · `stuck` · `cancelling` · `paused` · `not_scheduled`(대기 잡이 있고 정지 아니고 그룹에 안 막혔는데 idle 레인이 있는 채로 `max(워커 since, 잡 queued_at)` 부터 10초가 넘음 — 스케줄러 이상) · `worker_down`(모든 레인 다운) · `held_by_load`(집었을 레인이 호스트 부하로 보류 중 — M5f). 행동 가능한 이유(`worker_down` → `stuck` → `upload_stalled` → `not_scheduled` → `blocked_by_group` → `overdue` → `paused`)는 화면 요약 「Not moving」에 이 순서로 오른다. **`held_by_load` 는 여기 안 올린다**(의도된·자가 치유되는 상태라 `paused` 와 같은 종류다 — 오너 결정 45). 오래 닫혀 있으면 `rcm check` 가 경고한다.
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
- 워커 상태: 레인마다 `{lane, state ∈ idle|busy|held|down, job_id, error, since}` 를 `server.workers[]` 로 싣는다. `held` 는 부하 게이트가 막고 있는 레인이고 `hold_code`(`cpu_busy`·`no_sample`·`cooldown`)·`hold_detail`·`held_since` 를 함께 싣는다(M5f). 스레드가 예외로 죽으면 `down` + `error`(앞 200자, 경로·토큰 없이) 로 남고 `server.last_error` 에도 적는다. 워커가 죽었는데 큐만 멀쩡해 보이는 화면이 가장 위험하다.
- ⚠️ 자식 프로세스의 stdout 버퍼링 때문에 마커가 늦게 도착한다. README 에 `PYTHONUNBUFFERED=1`·`stdbuf -oL`·`flutter --no-color` 같은 팁을 쓴다. 마커가 늦어도 잡 전체 경과는 정확하다.
- 정리(M3 `janitor.py` + 순수 `core/retention.py`): 성공 잡 워크스페이스는 완료 즉시 삭제(`keep_workspace_on_failure = true` 면 succeeded 가 아닌 모든 종료 상태 — failed·timed_out·cancelled·lost — 는 보존 기간까지). 서버 안 청소 스레드가 시작 직후와 `retention_sweep_interval_seconds`(3600)마다 `retention_days_success`(14) · `retention_days_failure`(30) 지난 종료 잡의 `jobs/<id>/`·`workspaces/<id>/` 를 지우고 `jobs.artifacts_purged_at` 에 표시한다(DB v2). 활성 잡은 삼중으로 보호(순수 규칙 · janitor 재확인 · UPDATE 조건). 심볼릭 링크는 링크만, data_dir 밖을 가리키면 손대지 않는다. 산출물이 지워진 뒤 `metadata_retention_days`(180, `sample_days` 이상) 지난 잡 행·이벤트·합류자는 삭제한다. 미러는 안 지운다. 지운 잡의 로그는 404 `log expired`. 스레드가 죽거나 주기의 2배가 지나도록 sweep 이 없으면 `/api/health` 503. **M5g 부터 워크스페이스는 로그와 다른 시계로 잔다** — `workspace_retention_days`(1)가 남겨 둔 워크스페이스와 그 잡의 스냅샷 tar 을 지배하고, 로그·메타데이터는 위 값 그대로다. 거기에 날짜와 무관한 `workspace_storage_max_bytes`(100 GiB)와 `min_free_bytes`(10 GiB)가 오래된 종료 잡부터 부피를 회수한다. **증거(로그·잡 행)는 어떤 압박에서도 안 지우고, 크기를 못 재면 그 회차의 예산·바닥을 건너뛴다**(나이 규칙만 돈다). 회계는 `server.job_storage`·`/api/health.storage`·`rcm check`·웹 호스트 카드에 싣고 `rcm gc [--dry-run]` 이 같은 계획을 손으로 돌린다.
- 권한: 서버가 도는 OS 사용자로 실행된다. README 에 「전용 사용자로 돌리고 sudo 를 주지 말라」.

## 진행 — 스텝 마커 프로토콜 (순수 · `core/progress.py`)

프리셋 스크립트가 stdout 에 줄 단위로 찍는다. 줄 맨 앞이어야 하고, 마커 줄은 로그에도 그대로 남는다.

| 마커 | 뜻 |
|---|---|
| `::rcm::steps::<N>` | 앞으로 스텝이 N 개다(선택). 있으면 「N/M」의 M 이 확정, 없으면 「지금까지 알려진 수 (so far)」 |
| `::rcm::step::<이름>` | 새 스텝 시작. 앞 스텝은 이 시각에 끝난 것으로 본다 |
| `::rcm::step-end::<ok|fail>` | 스텝 끝을 명시(선택). 없으면 다음 마커나 잡 종료가 끝이다 |
| `::rcm::summary::<한 줄>` | 결과 요약(선택). 마지막 것이 `summary` |
| `::rcm::fail::<이름>` | **무엇이 실패했는지 이름으로 지목한다**(선택, M5h). 스텝 이름이면 그 스텝이 실패이고, 아니면 실패한 단위(테스트·파일)다. 여러 번 찍을 수 있다 — 이름 120자 · 잡당 100개 |

- 마커가 하나도 없는 잡은 `steps: []`, `steps_total: null`(0 이 아니다) 로 「스텝 정보 없음」. 잡 전체 경과와 로그 tail 은 그대로.
- **실패 스텝은 선언된 것만이다**(M5h · 결정 63). `::rcm::step-end::fail` 또는 `::rcm::fail::<스텝 이름>` 으로 밝힌 스텝만 `failed_step` 이고, 없으면 **`null`** 이다 — 종료 코드로 스텝을 고르지 않는다. 대신 `last_step`(마지막으로 시작한 스텝)을 실어 「끝났을 때 어디였나」를 인과 없이 말한다. **이 규칙은 PR #71 의 `failed_step_guessed`(추측한 이름을 남기고 「추측」이라고 밝히기)를 대체한다** — 같은 사고(2026-09-08, 실패 55건 중 16건이 무죄인 `build web` 을 지목)에 대한 두 번째 답이고, 그 커밋의 주석이 적은 「무죄인 스텝을 자신있게 지목하는 것이 아무 이름도 안 대는 것보다 나쁘다」를 끝까지 민 것이다. 둘 다 미출시라 사용자를 지나치지 않았다. 옛 규칙(「종료 코드 ≠ 0 이면 마지막 스텝」)은 되재생·병렬 스크립트에서 **성공한 스텝을 실패로 불렀다**(운영 잡 #162 · `docs/m5h-workplan.md` §2.1).
- 선언이 추론을 이긴다: 새 스텝이 시작해 앞 스텝이 암묵적으로 닫힐 때는 `ok: true` 지만, 뒤에 온 `::rcm::fail::` 이 그 값을 덮는다. 잡이 끝나며 닫히는 마지막 스텝은 exit 0 이면 `true`, 아니면 `null`(모른다)이다.
- `cancelled`·`lost` 잡에는 `failed_step`·`last_step` 이 **둘 다 없다**(결정 64 — 운영 잡 #176 이 취소되고도 실패 스텝을 달고 있었다). `succeeded` 는 잡 자신의 판정이 이겨 실패 이름을 안 남긴다.
- 함정과 테스트(픽스처로 잠근다):

| # | 사실 | 규칙 | 테스트 |
|---|---|---|---|
| 1 | 총 스텝 수를 안 알리는 스크립트가 많다 | `steps_total_partial: true`, UI 「N/M (so far)」 | `test_step_total_partial_without_declaration` |
| 2 | 마지막 스텝은 끝 마커가 없다 | 잡 종료 시각이 끝 | `test_last_step_ends_at_job_end` |
| 3 | 버퍼링으로 마커가 몰려서 온다 | 스텝 시각은 **서버 수신 시각**이라 실제보다 늦을 수 있음을 스키마에 `timing: "as_received"` 로 밝힌다 | `test_marker_timestamps_are_receive_times` |
| 4 | 같은 이름 스텝이 반복된다(매트릭스) | 위치(`index`)로 세고 이름은 표시용 | `test_duplicate_step_names_by_index` |
| 5 | 잡이 시작 전이면 스텝이 없다 | state `queued` 엔 진행 칸 없음, 0/0 금지 | `test_queued_job_has_no_progress` |
| 6 | 초과 실행 잡의 잔여를 음수로 두면 큐 전체가 앞당겨진다 | 하한 30초 · `overdue` · 실제 경과 표시 | `test_overdue_run_floors_remaining` |
| 7 | 스텝을 병렬로 돌리고 마커를 정해진 순서로 몰아 내보내는 스크립트가 있다 | 마지막 마커가 **성공한** 스텝일 수 있다. 마커만으로는 진짜 범인을 못 고르니 폴백이 고른 이름은 `failed_step_guessed: true` 로 밝히고 화면·CLI·알림이 추측으로 그린다 | `test_parallel_script_blames_a_step_that_actually_passed` |

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
| `POST /jobs` | 토큰 | `{preset, inputs, source, requester_label, join}` → 검증 → 합류면 `{job_id, joined: true}`(+ `joiners[]` 에 기록), 아니면 새 잡(`uploading` 또는 `git_ref` 면 바로 `queued`) `{job_id, joined: false, upload: "/jobs/{id}/tree"}`. `git_ref` 는 `source: {mode, ref}` → 서버가 sha 확정 → `{job_id, joined, state: "queued", sha, url}`(400 ref 검증 · 502 해석 실패 · 504 타임아웃). git_ref 잡에 `PUT tree` 는 409. **시도마다(합류 포함) `submission: {id, cancel_token}`** — 그 제출의 취소 capability(요청자 `cancel_job` · 합류자 `leave_submission`), 서버는 SHA-256 만 둔다(v17 `submissions` · M5j G5 · `docs/gate-optimization-workplan.md` 결정 87). 비밀은 여기서 한 번만 나간다 |
| `PUT /jobs/{id}/tree` | 토큰(그 잡의) | 본문 tar.gz(`Content-Length` 필수, 상한) → 풀지 않고 저장만 → `queued`. 수신 중 `source.received_bytes`·`last_received_at` 갱신. 이미 취소된 잡이면 409 |
| `GET /jobs/{id}?tail=N` | 없음(`log_tail` 은 토큰) | 잡 스냅샷(활성 잡은 queue 행, 종료 잡은 recent 행 모양 — recent 행엔 `log_tail` 키가 없다). **`failed`·`timed_out` 잡에만 `failures[]`·`failures_truncated` 를 더 싣는다**(M5h · 결정 67 — 이 이력 질의는 여기에만 있고 `/api/status` 에는 없다). `log_tail` 은 **유효 토큰(그 잡의·합류자·admin) 요청이고 `running`/`cancelling` 일 때만** 싣고 아니면 null. `tail` 기본 5줄, 잡당 8KiB 상한, `rcm wait` 는 `tail=0` |
| `GET /jobs/{id}/log?offset=N` | 토큰(그 잡의·합류자·admin) | 로그 바이트 스트림(증분). 보존 정리로 지워졌으면 404 `log expired` |
| `GET /jobs/{id}/events` | 없음 | SSE: 그 잡의 `job_changed`·`job_finished`·`marker` 만(로그 줄은 아님). 이미 끝난 잡이면 `hello` 뒤 `job_finished` 하나를 보내고 닫는다 |
| `POST /jobs/{id}/cancel` | 토큰(그 잡의 또는 admin) | 취소 → `{job_id, state}`. 합류자 토큰이면 잡은 두고 자기 `joiners[]` 항목만 지운다 → `{left: true, job_id, job_state}` 이고 그 세션의 `rcm wait` 는 같은 JSON 을 찍고 **2** 로 끝난다. 본문 `{cancel_token}` 은 선택(M5j G5 · 결정 87): 맞으면 그 역할대로(`cancel_job` 취소 · `leave_submission` 그 참여자만 나감, 비밀은 한 번만 통한다). `[server] cancel_requires_submission_token = true` 면 capability 또는 admin 만이고 나머지는 403 — 비-admin `--force` 없음. 기본 off 는 오늘 규칙 그대로(0.2.x 호환) |
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
admission = "load"                  # "load" | "always" — 레인 2 부터 부하를 보고 미룬다 (M5f)
cpu_max_percent = 80                # 이 위면 레인 2 부터 안 집는다. 레인 1 은 언제나 집는다
admission_samples = 3               # 연속으로 이만큼의 표본이 전부 기준 아래여야 연다
admission_cooldown_seconds = 30     # 한 머신에서 게이트를 지나 잡을 집으면 이만큼 쉰다
read_auth = "none"                  # "none" | "basic" (TLS 프록시 뒤에서만)
max_snapshot_bytes = 536870912
max_concurrent_requests = 32
join_duplicates = true
grace_seconds = 10                  # SIGTERM 뒤 SIGKILL 까지
retention_days_success = 14
retention_days_failure = 30
keep_workspace_on_failure = true
workspace_retention_days = 1        # 남겨 둔 워크스페이스와 그 잡의 스냅샷 tar — 로그보다 짧다 (M5g)
workspace_storage_max_bytes = 107374182400  # 부피의 상한(100 GiB). 0 = 무제한 (M5g)
min_free_bytes = 10737418240        # 파일 시스템 여유 바닥(10 GiB). 0 = 안 본다 (M5g)
recent_count = 8                    # /api/status.recent 건수 (오너 결정 14)
failure_window_jobs = 20            # 실패 이름의 이력 창 — 같은 key 의 최근 종료 잡 수 (M5h)
failure_min_jobs = 3                # 창이 이보다 얕으면 판정하지 않는다(unknown) (M5h)
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
                   "failed_step": null, "failed_step_guessed": false,
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
                "summary": "2 tests failed", "failed_step": "test", "failed_step_guessed": false,
                "cancelled_by": null, "timeout_seconds": 1200,
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

규칙: `last_step`(마지막으로 시작한 스텝)은 큐 행의 `progress` 와 최근 행에 있고, `failed_step` 은 **선언된 것만**이다(M5h · 결정 63). 이름별 실패 이력 `failures[]` 는 `GET /jobs/{id}` 의 종료 잡에만 있다(결정 67). 시각은 UTC ISO-8601(`Z`). 조회·수집 실패 섹션은 `null` + `*_error` — `queue`·`recent`·`medians`·`hosts` 넷 다 같은 규칙(`recent: null` + `recent_error` 는 「조회 실패」, `recent: []` 는 「완료 잡 없음」으로 다른 모양). 모르는 숫자는 `null`. `position` 은 대기 잡에만 1부터, `running`·`cancelling` 은 null. `finish_at`·`wait_seconds` 는 정지·살아 있는 레인 0·초과 실행이면 null. `log_tail` 은 **유효 토큰(그 잡의·합류자·admin) 요청이고 `running`/`cancelling` 인 잡에만**, 아니면 null. `progress` 는 `queued`/`uploading` 이면 null(0/0 금지), `phase: "materializing"` 이면 `steps: []`. `hosts[].history[]` 는 `history_samples` 개의 `{at, cpu_busy, mem_used_bytes, gpu_util_pct}`(각 값 nullable), 빠진 표본은 그 시각을 건너뛴다(UI 가 점선으로 끊어 그린다). 바이트 필드 이름은 전부 `_bytes` 로 끝난다. M0~M4 는 `pools` 가 한 개. M0 서버는 이 스키마를 처음부터 낸다(`hosts: []`·`medians: {}` 로 시작). 키 삭제·의미 변경은 `schema_version` 을 올리고 CHANGELOG 에 적는다.

## CLI (`cli.py`)

| 명령 | 하는 일 |
|---|---|
| `rcm run PRESET [-f K=V …] [--source tree\|git_ref] [--ref REF] [--by LABEL] [--no-join] [--no-wait] [--exclude PAT]` | 스냅샷 → 제출(합류) → 업로드 → 기본으로 `wait` 이어짐. stdout 에 JSON 한 줄, stderr 에 사람용 |
| `rcm wait --job ID [--timeout S]` | SSE(M1)로 기다리며 stderr 에 위치·스텝·경과·ETA 갱신(TTY 면 한 줄 덮어쓰기), 끝나면 stdout JSON + **종료 코드 0/1/2/3**. SSE 가 끊기면 폴링(2초)으로 폴백(M0 는 폴링만). 서버 연결 실패가 60초 넘게 이어지면 3(`--timeout` 이 더 짧으면 그때 3). **Ctrl-C 는 detach** — 잡은 계속 돌고 `rcm wait --job ID` / `rcm cancel ID` 를 안내한다(합류자면 자기 `joiners[]` 항목만 best-effort 로 뺀다). 잡 취소는 명시적 `rcm cancel` 만 |
| `rcm eta (--job ID \| PRESET [-f K=V])` | 앞선 건수·대기·자기 소요·예상 완료·표본 출처 |
| `rcm top [--watch N] [--json]` | 한 화면(아래) |
| `rcm jobs [--mine] [--state S] [--ref REF]` · `rcm logs ID [--follow]` · `rcm cancel ID` · `rcm presets` | 큐·로그·취소·프리셋. 한 줄에 `<ref\|branch> @<짧은 sha>` 를 싣고 `--ref` 는 그 값이 들어간 잡만 남긴다(M5h). `--mine` 은 요청자와 합류자 둘 다. 합류자의 `cancel` 은 자기 대기만 뺀다 |
| `rcm pause` · `rcm resume` | 큐 정지·재개(admin 토큰). `POST /pause`·`/resume` |
| `rcm serve [--config] [--bind] [--port] [--data-dir]` · `rcm check` · `rcm token add\|list\|revoke` · `rcm version` | 서버·셋업·토큰 |

**`rcm run` 흐름**: ① 프리셋·입력을 서버 스키마로 검증(`GET /api/status.presets`, 실패면 서버에 안 보내고 종료 2) ② `tree` 면 스냅샷 규칙으로 파일 목록·`tree_hash` 계산 ③ `POST /jobs` → 합류면 업로드 생략 ④ `PUT …/tree` 업로드(진행률 stderr) ⑤ `rcm wait`. `--no-wait` 면 ③/④ 뒤 **순번·ETA 를 한 번 조회해**(`GET /jobs/{id}`) stderr 한 줄(`submitted job #155 queued · 3rd in line · wait 4m 12s · eta 16:02 · <url>`)과 stdout JSON(`state`·`position`·`reason`·`ahead_job_id`·`blocked_by`·`estimate`)을 찍고 0 으로 끝난다(제출 성공 ≠ 잡 성공 — JSON 의 `state` 를 보라). 그 조회가 실패해도 **0** 이다 — 「제출됐다」는뜻이지 「조회됐다」가 아니다. 명세 `docs/nowait-workplan.md`.

**세션에서 쓰는 모양**(`examples/session/ci-gate.sh`):

```bash
out=$(rcm run gate -f scope=full --by "$(whoami)@$(hostname -s)"); rc=$?
job=$(jq -r .job_id <<<"$out")
case $rc in
  0) echo "gate green: $(jq -r .url <<<"$out")";;
  1) # failed_step 은 스크립트가 선언한 것만이다(결정 63) — 없으면 last_step 이 「어디였나」를 말한다
     echo "gate red — $(jq -r '.failed_step // ("last step " + (.last_step // "unknown"))' <<<"$out")"
     jq -r .summary <<<"$out"
     jq -r '(.failures // [])[] | "  \(.name): \(.seen)/\(.window) recent runs (\(.verdict))"' <<<"$out"
     echo "log: rcm logs $job";;
  2) echo "cancelled or timed out: $(jq -r .state <<<"$out")";;
  *) echo "unknown (exit $rc) — check $(jq -r .url <<<"$out"); log: rcm logs $job";;
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
- **펼침**: 모든 행은 **접힌 채로** 뜬다. 펼침만 잡 id 별로 기억한다(오너 결정 13 — 2026-09-09 개정). 접힌 도는 행도 「어디까지 왔나」(전체 진행 막대)와 「지금 뭘 하나」(이유 칸의 `step 2/4 build 2s`)는 보인다 — ▸ 뒤에 있는 것은 스텝 목록 · 로그 tail · 액션이다. 레인 1 이면 워커 필을 하나로 접는다(오너 결정 12).
- **전체 진행 막대**(2026-09-09 오너 요청 · 같은 날 Codex 리뷰 반영): 도는 행 아래 한 줄. 축이 둘이다 — **근거**(`basis`)와 **형편**(`condition`). 근거는 `steps`(잡이 `::rcm::steps::N` 으로 선언한 총계 → `62% · 스텝 5/8`) · `time`(추정 소요 대비 경과 → `62% · 측정 소요 기준`·`62% · 프리셋 예상 기준`, 1초마다 스스로 자란다) · `none`. 형편은 `preparing`(준비 중) · `stuck`(멈춘 듯) · `over`(예상 시간 초과) · `finalizing`(선언한 스텝을 다 끝냈는데 안 끝남) 이고 라벨에 ` · ` 로 붙는다.
  눈금을 **주지 않는** 자리: 「지금까지 본」 총계(`steps_total_partial`) · 설치 기본값 추정(`estimate.source == "default"` — ETA 는 `low · default` 로 스스로를 밝히지만 막대는 길이로만 말한다) · stuck 의 시간 눈금 · 준비 중. **도는 잡은 100% 가 되지 않는다**: 예측은 99% 가 상한(내림)이고, 스텝을 다 끝냈거나 추정을 넘긴 잡은 퍼센트 대신 빗금과 형편 문구다 — 꽉 찬 막대는 「끝났다」의 그림이다. 갱신이 멈추면(정지·연결 끊김) 예측도 멈춘다.
- **접힌 행의 액션**: 접혀 있으면 내 잡의 `Cancel` 이 이유 칸에 그대로 있다(펼치면 액션 블록이 맡는다) — 폭주하는 잡을 세우는 일이 스텝 목록을 여는 일보다 급하다. 펼침 버튼은 24px(폰 32px) 표적에 `#412 상세 펴기` 라는 접근 이름을 갖는다. 한 잡의 `role="progressbar"` 는 **하나**다(펼침 블록의 스텝 띠는 장식).
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
- **M5b — 확장 2 (원격 워커)** (**완료 2026-09-07**, PR #22 · #23 · #24 — M5 완료 기준 ①②③④ 전부 달성, **v0.2.0** 으로 릴리스): 빌드 머신 여러 대. 4 PR(명세 M5b-1~4).
  - **M5b-1 풀 축** (**완료 2026-09-06**): DB v4 `jobs.pool` · 프리셋 `pool`/`pools` · `rcm run/eta/jobs --pool` · `status()` 가 풀마다 `pools[]` 항목(기본 풀은 로컬 워커·호스트, 다른 풀은 lanes 0 → 대기 잡 `worker_down`·ETA 없음 — fail-open 금지) · 풀별 중앙값 · `rcm top`/웹이 풀을 순회(풀 하나면 화면 그대로).
  - **M5b-2 워커 프로토콜** (**완료 2026-09-07**, 명세 `docs/m5b2-workplan.md` · 리뷰 `docs/reviews/2026-09-06-codex-m5b2-design.md`): DB v5(`tokens.kind` · `jobs.worker_name` · `workers`) · `rcm token add --worker` · `/worker/register·claim(long-poll)·heartbeat` · `/worker/jobs/{id}/tree(캐시 잡 tar 조립)·phase·log(서버가 마커 파싱)·finish` · 워커 상태는 서버가 받은 `last_seen_at` 로만(timeout → down · 잡 lost · 재등록 = 옛 잡 lost · 미확인 취소는 서버가 닫음) · 재시작 복구는 로컬 잡만 · 인증 분리(워커 토큰은 `/worker/*` 만) · `server.workers[].worker/display_name` · `pools[].lanes` 는 살아 있는 레인 · 풀 hosts 에 워커 표본 · `/api/health.pools_without_workers`.
  - **M5b-3 `rcm worker`** (**완료 2026-09-07**, 명세 `docs/m5b3-workplan.md` — Codex 는 이날 모델 접근 불가로 생략): `runner.run_job`(자재화 → Popen → 펌프 → 신호; 로컬·원격 공용) · `WorkerClient` · `WorkerConfig`/`worker.toml`(`[[repos]]` 규칙은 서버와 같다) · `remote_worker.RemoteWorker`(등록 재시도 · heartbeat 스레드 · 레인 스레드 · 보고 재시도 · 409 면 정리 · SIGTERM → lost `worker stopped`) · `rcm worker --check/--once`. 두 프로세스 e2e 7건(실행·캐시 잡·취소·kill -9 → lost·SIGTERM·git_ref·서버 재시작). M5 완료 기준 ④ 달성.
  - **M5b-4 다중 풀 표시** (**완료 2026-09-07**, 명세 `docs/m5b4-workplan.md`): `rcm top` 원격 풀 헤더에 언제나 풀 이름(`queue — empty (pool linux)` · `· paused`) · 머리줄 원격 필 5개 초과는 `+N workers`(down 은 안 접음) · 웹 Host 절에 워커 표본 카드(`build-02 · pool linux`, Recent 밑 풀 host 헤더 제거) · `rcm check` `pools` 행(`default (1 lane) · linux (build-02/1 idle)`, 풀 워커 전부 down 이면 FAIL) · `server.workers[].pool`.
- **M5c — 내부망 자동 발견** (**완료 2026-09-08**, PR #37 · v0.2.2, 명세 `docs/m5c-workplan.md`): 서버가 `_rcm._tcp` 를 mDNS/DNS-SD 로 광고하고 클라이언트가 `server` 없이도 같은 네트워크의 서버를 찾는다(표준 라이브러리만 · 외부 도구 없음). `rcm discover` · `client.toml server = "auto"`. 실배치(노트북이 Tailscale 밖) 요청. 완료 기준은 명세 §6.
- **M5d — 웹 UI: 한국어 기본 + 정보 위계** (**완료 2026-09-08**, PR #47 · #48 · #51 · #52, 명세 `docs/m5d-workplan.md` — 완료 기준 7개 대조표는 명세 §7): 화면이 한국어로 뜨고 오른쪽 위에서 영어로 바꾼다(결정 36). 그리고 「내 잡 끝났나 · 왜 안 움직이나 · 언제 내 차례인가」 순서로 위계를 다시 세운다 — 호스트 지표와 소스 주소를 접고, 상태를 색이 아니라 모양·글자·움직임으로 표시하고, UI 글꼴을 산세리프로 되돌린다. 근거는 `docs/reviews/2026-09-08-codex-web-ui-design.md`(코덱스가 화면을 보고 낸 진단)와 `docs/reviews/2026-09-08-ui-hierarchy-research.md`(1차 자료 조사). PR 은 넷으로 나눴다 — 서버가 문장 대신 코드를 내려보낸다(M5d-0, 결정 37) · 언어 장치와 한국어
  카탈로그(M5d-1) · 위계(M5d-2) · 폰과 접근성(M5d-3). M5d-2 는 둘로 갈렸다: 오너가 화면을 보고 낸
  피드백 세 건(저장 공간 · 초가 튀는 것 · 뭐가 도는지 안 보이는 것, 명세 §4.6)을 먼저 고치고, 그
  다음에 위계 재구성을 했다. 마일스톤을 도는 동안 값을 치른 것 셋: M5d-0 이 호스트 문서에 키를
  더하면서 원격 워커 표본 파서를 안 고쳐 **최신 워커의 표본이 통째로 버려지고 있었다**(#48 에서
  수정, 이제 왕복 시험이 잠근다) · 표의 칸 폭을 무조건 못 박아 **폰에서 키·요청자 칸이 사라졌다**
  (사람 눈으로만 잡혔다 — #52 가 390px 에서 「글자와 자리」를 함께 본다) · 폰 시험이 macOS 크롬의
  최소 창 폭 때문에 **실제로는 500px 에서 돌고 있었다**(§4.7).


**계획서의 마일스톤은 여기서 끝난다.** M0~M5 가 전부 완료됐고(M6 는 결정 30 으로 폐기), 이후는 오너
실기 결과에 따른 수정과 운영 개선이다.

- **M5f — 부하를 보는 병렬 레인**(계획 2026-09-08~09, 명세 `docs/m5f-workplan.md` · 리뷰 `docs/reviews/2026-09-08-m5f-design-review.md`): 오너 요청 — 「CPU 를 너무 잡아먹지 않도록 설정하는 값을 주고 **기본값 80%**, 그거에 맞게 설정되면 병렬도 돌릴 수 있게」. `lanes` 를 올려도 안전하게 만든다. 레인 2 부터는 호스트 CPU 가 `cpu_max_percent`(80) 아래일 때만 claim 하고, 레인 1 은 게이트를 안 지난다(큐가 절대 안 멈춘다). 판정은 **서버가 claim 직전에** 한 곳에서 — 로컬 레인과 원격 워커의 claim 이 둘 다 서버 프로세스 안에서 돌고 서버가 두 머신의 표본을 이미 들고 있기 때문이다. `rcm worker` 프로토콜과 `store.claim` 의 SQL 은 안 바뀐다. 보류 레인은 `held` + 사유 코드로, 대기 잡은 `held_by_load` 로 보인다. 결정 39~50. PR 은 다섯 — 명세(1) · 선행 병목(2a-0) · 게이트(2a) · 화면·문서(2b) · 동시 실행 ETA(2c).
  - **재고 나서 정했다**: 리뷰(격리 에이전트 둘)와 실측 프로브가 초안의 다섯 곳을 뒤집었다 — 쿨다운이 버스트를 못 막던 것(락 없이는 20회 중 20회 전부 통과) · ETA 그리디의 레인 번호 충돌(4레인이 2레인처럼) · 메모리 게이트(두 OS 의 `used` 가 다른 뜻) · `held_by_load` 의 「Not moving」 자리 · 「마이그레이션 불필요」(overdue·stuck 이 같은 중앙값에서 나온다). 명세 §13~§15 에 근거가 있다.
  - **게이트 자체는 공짜다**(실측): `decide()` 1.29 µs, 보류 레인 48개가 코어의 0.012%, 그리고 보류하면 claim 을 안 하므로 오늘보다 싸다. 비싼 것은 게이트가 **앉는 자리**였다 — 마커 줄마다 SQLite 트랜잭션(다른 레인 claim 을 275 ms 로 밀어냄) · `store.claim` 이 `jobs_state` 를 두고 `jobs_pool` 을 타는 것(`ANALYZE` 하나로 3727배) · 지터 없는 1초 폴링 격자(48레인에서 `/api/status` 503). 그래서 PR 2a-0 이 먼저다.
- **M5g — 증거를 얼마나, 얼마나 오래 들고 있을지** (**완료 2026-09-10**, PR #68 · #75 · #76 · #77, 명세 `docs/m5g-workplan.md` · 리뷰 `docs/reviews/2026-09-09-codex-m5g-design.md`): 오너 요청 — 「지금은 조절값이 날짜뿐이고, 그 날짜가 오기 전에 디스크가 먼저 찬다.」 실측(오너의 Mac mini 운영 인스턴스): 워크스페이스 62개 27 GB(평균 435 MB) · 잡 로그 137개 39 MB · 여유 628 GiB · 비성공 잡 하루 최대 50개 → **하루 50개 × 30일 × 0.5 GB ≈ 750 GB > 여유 628 GB**. 보존 기간이 한 번도 발동하기 전에 디스크가 찬다. 고치는 것 셋 — ① **로그(증거)와 워크스페이스(부피)를 다른 시계로** 재운다(`workspace_retention_days = 1`, 로그는 14/30 그대로. 스냅샷 tar 은 부피 쪽) ② 날짜와 무관한 **바이트 예산**(`workspace_storage_max_bytes = 100 GiB`)과 **여유 공간 바닥**(`min_free_bytes = 10 GiB`) ③ **보이게** 한다(`server.job_storage` · `/api/health.storage` · `rcm check` 의 `storage` 행 · 웹 호스트 카드 · `rcm gc [--dry-run]`). 안전 성질 둘: **증거는 어떤 압박에서도 안 지운다**(부피만 내놓는다) · **못 재면 압박 삭제만 멈춘다**(예산·바닥을 건너뛰고 `error_code` 로 알린다. 나이 규칙은 그대로 돈다). 계획은 한 회차에 한 번만 세우고, 회차 사이는 무진전 latch 가 막는다(결정 62) — 「여유가 오를 때까지 지운다」는 지워도 여유가 안 오르는 파일 시스템에서 결국 전부 지운다. DB 마이그레이션 없음(워크스페이스 삭제는 멱등이라 표시가 필요 없다). 결정 51~62. PR 넷으로 다 들어갔다 — 명세(#68) · 규칙·janitor(#75) · 표시·`rcm gc`(#76) · 프리셋 권고·`artifacts_on`(#77). 테스트 2936 · mutcheck 20종 · 개발 인스턴스에서 세 규칙이 각각 발동하는 것을 확인했다(나이 · 예산은 필요한 만큼만 · 바닥은 다 지운 뒤 `rcm check` FAIL). **고친 기존 버그 둘**: `artifact_storage.last_sweep_at` 이 없는 속성을 봐서 언제나 null 이던 것 · **원격 워커가 산출물을 하나도 안 모으던 것**(claim 응답에 얼린 정책이 안 실렸다 — 시험이 손으로 만든 payload 를 써서 M5e 내내 안 보였다). 크로스리뷰(`docs/reviews/2026-09-09-codex-m5g-design.md`)는 1차가 모델 접근이 끊겨 중단됐고 **2차가 완주해 「조건부 승인」**을 냈다 — P0 넷 · P1 넷 · P2 셋을 반영했다. 가장 큰 것은 **업그레이드 게이트가 실행 불가능했던 것**(「올리기 전에 dry-run」인데 `POST /gc` 는 새 서버에만 있고 새 서버는 뜨자마자 sweep 한다 → 결정 61) · **순수 모델이 비어 있던 것**(`created_at`·고아·활성·`budget_unreachable`·null 가능 파생값) · **「dry-run 과 실제가 다를 수 없다」가 틀린 말이었던 것**(결정 57) · **검증이 `min(success, failure)` 여야 하는 것** · **「한 번만 계획」이 한 회차짜리 보호인 것**(결정 62). 그리고 리뷰를 쫓다가 **원격 워커가 산출물을 하나도 안 모으는 것**을 찾았다 — `_claim_payload` 가 얼린 정책을 안 실어서 워커의 `_policy_from_claim` 이 `None` 을 돌려준다(명세 §13 D, 실측 확인). **M5e 가 원격 풀에서 죽어 있다.**
  - 프리셋 쪽 문제도 같이 답한다: 참고 팀의 게이트 스크립트가 무거운 구간 출력을 `TMPDIR` 로 돌리고 지워서 **실패 잡 #133 의 로그 50,788 바이트 안에 `Expected:` 0줄 · 스택트레이스 0줄**이었다. 정본 권고는 새 개념 없이 — 판정 몇 줄은 stdout 으로(로그 30일), 부피 있는 나머지는 워크스페이스에 두고 M5e `artifacts` 로 선언(가져가는 통로, 24시간). 프리셋별 TTL 은 안 만들고 `artifacts_on = "failure"` 만 더한다.
- **M5h — 끝난 잡이 무엇이 왜 깨졌는지 말하게 한다**(계획 2026-09-09, 명세 `docs/m5h-workplan.md` · 구현 `docs/m5h-implementation.md`): 출처는 **다른 머신의 세션**이 운영 인스턴스(v0.2.5)를 쓰며 남긴 사용기 여섯 개다. 여섯 중 다섯이 「끝난 잡을 읽는 일」에 몰려 있었다. 실측으로 확인한 것 — ① `failed_step` 이 **성공한 스텝을 실패로 불렀다**(#162: 되재생·병렬 스크립트에서 마지막 머리말 `build web` 이 라벨을 가져갔고, 실제 실패는 `test` 였다. #169~#171 도 같은 라벨) ② 취소한 #176 의 요약 옆 문자열은 프리셋 설명 유출이 아니라 **취소된 잡이 들고 있던 `failed_step`** 이었다(①과 같은 버그) ③ `/api/jobs/162`·`/logs/162` 는 `{"error":"not found"}` 한 줄이라 로그로 가는 길을 안 알려 준다 ④ 종료 잡의 코드 신원(`source`)은 `/api/status` 에 **이미 있는데** `rcm jobs`·최근 줄이 안 그린다 ⑤ 대기 클라이언트는 **31.6 MB 로 평평**하고 누수가 없다(2만 파일 tree 잡만 스냅샷 장부 32 MB 를 대기 내내 쥔다) ⑥ 「이 테스트가 최근 몇 번 빨갰나」의 답은 **서버에 없다** — 로그는 30일 남지만 무엇이 깨졌는지는 저장되지 않는다. 고치는 것 — **선언된 실패 스텝만**(`failed_step` 은 `step-end::fail`·새 마커 `::rcm::fail::<이름>` 로만, 아니면 null + `last_step`) · **실패 대장과 최근 이력**(DB v12 `job_failures`, 같은 key 의 최근 20회 창에서 `first_seen`·`intermittent`·`persistent` 를 서버가 코드로 판정해 `GET /jobs/{id}` 에만 싣는다) · **404 의 `hint` 와 실패한 대기의 `rcm logs <N>` 줄** · **목록의 `<ref|branch> @<sha>` 와 `rcm jobs --ref`** · **대기 전에 스냅샷 놓기**. 서버는 임의 출력을 파싱하지 않는다 — 이름은 프리셋 스크립트가 마커로 말한다. 결정 63~72. DB v11(`last_step`) · v12(대장) · **v13·v14(옛 라벨 정리 — 취소·유실 잡의 `failed_step` 을 지우고, 옛 실패 잡의 라벨을 `last_step` 으로 옮긴다. 안 그러면 신고자가 #162 를 다시 열었을 때 「고쳤다」는 그 문자열을 그대로 본다)**. 단계 다섯, PR 하나. 격리 검증 셋(계약 대조 · 실기 · 엣지)이 초록 위에서 찾은 것은 `docs/m5h-implementation.md` §14 에 있다 — 가장 큰 셋은 **웹 배지가 영영 안 나오던 것**(결정 67 이 `/api/status` 에서 이력을 뺐는데 웹은 그 문서만 그렸다), **실패 이름이 제어문자를 터미널까지 나르던 것**, **옛 라벨이 그대로 남던 것**이다.
- ~~M6 — GitHub 백엔드~~ **폐기(오너 결정 30, 2026-09-07)**: Actions run 관찰·dispatch 는 만들지 않는다. GitHub 은 커밋·푸시·PR 머지용이다. 계획서의 마일스톤은 **M5 로 끝**이며, 이후는 오너 실기 결과에 따른 수정과 운영 개선만 남는다.

## 결정 항목 (2026-09-04, 전부 확정)

| # | 결정 | 내용 |
|---|---|---|
| 1 | 방향 | **로컬 잡 서버**. 도구가 큐·실행을 소유한다. GitHub 관찰/디스패치 백엔드는 두지 않는다(결정 30). 배포·QA 는 원격 워커(M5b)로 |
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
| 13 | 행 펼침 | ~~실행 중 행은 **전부** 펼친다. 접힘만 기억한다~~ → **2026-09-09 개정**: 행은 **전부 접힌 채로** 뜨고 **펼침**만 잡 id 별로 기억한다(`localStorage["rcm.expanded"]`, 큐에 없는 id 는 정리). 접힌 도는 행은 전체 진행 막대와 지금 스텝을 그대로 보여 준다 |
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
| 30 | GitHub 백엔드 | **폐기(오너 확정 2026-09-07)**. GitHub 은 커밋·푸시·PR 머지에만 쓴다 — Actions run 을 이 도구로 보거나 띄우지 않는다. 배포·QA 도 로컬/원격 워커(`rcm worker`)로. v1/v1.1 의 GitHub 경로와 참고 구현의 dispatch 코드는 가져오지 않는다 |
| 31 | 우선순위 | low/normal/high 세 단계. 프리셋 `priority` 가 그 프리셋 잡의 기본이자 비-admin 상한. 기아 보정 없음(화면이 보여준다). (Codex M5 리뷰, **오너 확인 대기**) |
| 32 | 캐시 blob 공유 범위 | 기본 `snapshot_cache_scope = "global"`(같은 내용은 클라이언트 간 공유 — `missing` 목록으로 존재 여부를 알 수 있다). 토큰별 분리는 `"token"`. (Codex M5 리뷰, **오너 확인 대기**) |
| 33 | 광고 기본값 | `rcm init server` 템플릿의 `bind` 는 `127.0.0.1` 로 두고, 광고는 `bind` 가 루프백이 아닐 때만 켠다 — 「설치하자마자 LAN 에 열리는」 것을 피한다 (M5c, 2026-09-08) |
| 34 | 발견 응답 내용 | TXT 에 `v`·`name`·`lanes` 만 넣는다. 토큰·프리셋·경로는 넣지 않는다(비밀 아님) (M5c, 2026-09-08) |
| 35 | IPv6 발견 | v1 범위 밖. IPv4 만 응답한다 (M5c, 2026-09-08) |
| 36 | 웹 UI 언어 | **한국어가 기본**, 오른쪽 위에서 영어로 바꾼다(브라우저에 기억). 식별자·프리셋 이름·명령·커밋 해시·저장소 주소는 번역하지 않는다. **서버가 내려보내는 문장은 서버의 것** — 페이지가 번역하지 않고, 무엇이 서버 문장인지 명세에 적는다 (오너 결정 2026-09-08) |
| 37 | 서버 문장 | **서버는 문장이 아니라 코드를 내려보낸다**(2026-09-08). `summary_code`·`summary_args`·`*_error_code`·`gpu_note_code` 를 **추가**하고(스키마 v1 그대로) 화면·CLI 가 각자의 말로 그린다. 잡이 `::rcm::summary::` 로 찍은 문장만 예외 — 팀이 쓴 것이라 그대로 보여 준다. 인자는 원시 값으로 보내고 포맷은 표시하는 쪽이 한다 |
| 39 | CPU 상한과 기본 동작 | `[server] cpu_max_percent = 80`, `admission = "load"` 를 **기본으로 켠다**. 영향 범위는 「`lanes = 1`」이 아니라 **「서버와 등록된 모든 워커가 각각 레인 1」**이다 — `[server] lanes` 는 로컬 레인만 세고 원격 레인은 `worker.toml` 이 정한다. 레인 ≥ 2 인 설치는 업그레이드만으로 동작이 바뀌므로 CHANGELOG 에 동작 변경으로 적고 기동 로그 한 줄을 남긴다. 끄려면 `admission = "always"` (M5f, 2026-09-09) |
| 40 | 레인 1 은 게이트를 안 지난다 | `hold_max_seconds`(무한 보류 방지) 대신 **계기**를 단다 — `held_since` 와 `rcm check` 경고. 밸브는 「CPU 95% 인데 두 번째 잡을 밀어 넣는」 장치라 두지 않는다 |
| 41 | 레인 1 의 claim 도 쿨다운을 **기록**한다 | 판정은 건너뛰되 기록은 남긴다. 안 그러면 레인 1 이 무거운 잡을 집은 0.5초 뒤 레인 2 가 「잡 시작 전」 표본을 보고 통과한다 |
| 42 | 메모리 게이트는 **넣지 않는다** | 두 OS 의 `used` 정의가 다르다 — 같은 픽스처에서 macOS free 31.9% vs Linux 60.4%(같은 파일의 `MemFree` 로 재면 14.8%). 스왑 압력 신호도 없다. 다시 볼 때의 신호는 macOS `memory.compressed_bytes` |
| 43 | ETA 는 보류 레인을 뺀다 | 늦게 잡고, 레인이 열리면 앞당겨진다. 전제로 레인 배정 키를 `(worker, lane)` 로 고친다 |
| 44 | 프리셋 무게(`heavy`)는 안 만든다 | 게이트는 재고 무게는 짐작한다. `concurrency_group` 은 라벨 상호 배제라 「무겁지만 자기들끼리는 병행」을 표현할 수 없다 — 그래도 결론은 같다 |
| 45 | `held_by_load` 는 「Not moving」에 **안 올린다** | 의도된·자가 치유되는 상태라 `paused` 와 같은 종류다. 늘 켜져 있으면 사람들이 패널을 무시하게 되고 `worker_down`·`stuck` 이 묻힌다 |
| 46 | 코로케이션은 자동 병합하지 않는다 | 한 머신의 `rcm serve` + `rcm worker` 는 게이트 없는 레인 **둘**을 갖는다(이 저장소 오너의 Mac 이 그 배치다). 합치려면 `/worker/register` 에 머신 식별자를 더해야 한다 — 필요해지면 그때 |
| 47 | 원격 표본의 낡음 예산 | 기본 15초(`3 × interval`) 그대로. heartbeat 이 두 번 밀리면 경계에 딱 걸리고(15.0초는 아직 fresh) 세 번이면 닫힌다. 거슬리면 `stale_seconds` 를 설정 키로 뺀다 |
| 48 | 선행 병목을 M5f 안에서 고친다 | PR 2a-0: 마커 배치 쓰기 · `ANALYZE` · `OperationalError` → 503 · `CLAIM_MIN_INTERVAL` 지터 · 레인 키. 게이트를 병목 위에 달지 않기 위해서다 |
| 49 | M5f **밖**의 병목은 별도 PR | `/api/status` 의 `list_samples`(요청의 91~93%, 1만 행 245 ms) · `remote_worker_infos` N+1(마커 줄당 SQL 555개) · `workers` 표 미정리 · `PRAGMA synchronous=NORMAL`(쓰기 5.6배, 내구성 변경이라 오너가 정한다) |
| 50 | 같은 레포의 병렬 레인은 직렬화된다 — 적고 안 고친다 | `gitops.py` 의 미러 락은 `git` 서브프로세스를 안고 잡는다(상한 600초). 「레인을 늘려도 같은 레포의 자재화는 겹치지 않는다」를 `docs/configuration.md` 에 적는다 |
| 38 | 기본 언어 | 브라우저 언어와 **무관하게 한국어가 기본**이다. 영어는 오른쪽 위에서 고르고, 고른 값은 그 브라우저에 남는다 (2026-09-08) |
| 39 | 잡 산출물 되돌려주기 | 프리셋이 선언한 글롭(`artifacts`)에 맞는 파일을 워커가 **워크스페이스를 지우기 전에** 모아 서버에 불변 묶음으로 두고, 그 잡의 자격자(요청자·합류자·admin)가 받아 간다. 로컬 워커·원격 워커 둘 다. 잡이 만든 파일은 코드에서 `bundle` 로 부른다 — `jobs.artifacts_purged_at`(M3, 로그·스냅샷·워크스페이스)과 **다른 것**이다 (M5e, 오너 결정 2026-09-08) |
| 40 | 산출물 삭제 규칙 | `jobs.join_count == 0` 인 잡은 클라이언트 확인(ack)이 오면 **즉시 삭제**, 한 번이라도 합류가 있었던 잡은 확인이 와도 **TTL 까지 유지**한다. TTL 은 `artifact_retention_hours = 24`. 합류자 표의 키가 토큰 이름이라 세션은 셀 수 없고(`store.py` `joiners`), 합류는 `ACTIVE_STATES` 에서만 일어나고 판정·종료가 같은 트랜잭션 직렬화를 쓰므로 `join_count` 는 **터미널 커밋에** 얼어붙는다 (M5e, 오너 결정 2026-09-08) |
| 41 | 산출물 상한 | 전부 설정 키다: `max_artifact_bytes`(1 GiB) · `artifact_storage_max_bytes`(10 GiB) · `max_artifact_files`(10000) · `artifact_timeout_seconds`(60) · `max_concurrent_artifact_transfers`(2). 넘으면 **잡은 그대로 성공/실패하고** 산출물만 버린다. 만료되지 않은 남의 묶음을 쫓아내지 않는다 (M5e, 오너 결정 2026-09-08) |
| 42 | 받기·덮어쓰기 | 받기는 옵트인(`--fetch-artifacts`). 트리에 쓸 때 제출 당시와 내용이 같은 파일은 덮어쓰고, 제출 뒤 사람이 손댄 파일(`conflicted`)은 `--force` 여야 덮는다. 골든 갱신이 플래그 하나로 돌아야 하고, 기다리는 동안 손댄 것만 지키면 된다 (M5e, **오너 확정 2026-09-09**) |
| 51 | 로그와 부피를 따로 재운다 | `workspace_retention_days = 1`(**오너 확정 2026-09-09**, 초안 2일) 을 새로 두고 로그·메타데이터는 그대로(14 · 30 · 180). 부피에는 **그 잡의 스냅샷 `tree.tar.gz` 도 포함**한다 — 종료된 잡에서 아무도 안 읽는다. 성공 잡의 tar 이 14일 → 하루로 짧아지는 것이 유일한 부수 효과다. 이름에 `_failure` 를 안 붙이는 이유: 성공 잡의 워크스페이스는 애초에 남지 않는다 (M5g, 2026-09-09) |
| 52 | 부피의 바이트 예산 | `workspace_storage_max_bytes = 107374182400`(100 GiB), `0` = 무제한. 재고 지우는 대상은 **워크스페이스 + 스냅샷 tar** 뿐 — 로그·번들·blob 은 각자의 예산이 있고 이 예산이 손대지 않는다. 정상 상태(하루 50개 × 435 MB ≈ 22 GB)의 네 배가 넘어 **평소에는 안 발동하는 천장**이다 |
| 53 | 여유 공간 바닥 | `min_free_bytes = 10737418240`(10 GiB), `0` = 안 본다. 웹 호스트 카드의 `DISK_LOW_FREE` 와 같은 값. **비율(85%)은 안 쓴다** — 926 GiB 디스크의 85% 는 여유가 139 GiB 다. 그건 사람에게 알리는 기준이지 지우는 기준이 아니다 |
| 54 | 한 회차에 한 번만 계획한다 | 잰 값으로 계획 → 실행 → 다시 잰다. 「여유가 바닥을 넘을 때까지 지운다」 루프는 두지 않는다 — macOS 로컬 스냅샷처럼 지워도 `df` 가 안 움직이는 경우에 **워크스페이스를 전부 지우고도** 못 넘는다. ⚠️ 그 보호는 **한 회차짜리**다 — 회차 사이는 결정 62 의 latch 가 막는다. `min_free_bytes` 는 유지되는 불변식이 아니라 **회수 시도**다 |
| 55 | 못 재면 **압박 삭제만** 멈춘다 | 크기를 못 잰 항목이 **인벤토리 어디든**(종료·활성·고아·tar) 하나라도 있거나 스캔·DB 가 실패하면 그 회차의 **예산·바닥을 건너뛰고** `job_storage.error_code` 와 `rcm check` warn 으로 알린다. **나이 규칙은 그대로 돈다** — 오늘도 도는 정상 보존 정책이고 크기를 안 본다. 둘을 한 문장으로 묶어 잠그면 디스크가 차는 동안 나이 규칙까지 멈춘다. 그리고 **지울 수 없는 바이트(도는 잡 + 고아)만으로 예산을 넘으면 예산 규칙은 아무것도 안 고른다**(`budget_unreachable`) — 못 이룰 목표를 위해 증거를 태우지 않는다. 바닥 규칙은 응급이라 그래도 돈다. ⚠️ 이 상태에서 **디스크는 계속 찬다** — 「fail-closed 니까 안전」이 아니다 |
| 56 | 어디에 보여주나 | 셋 다 — `/api/status` 의 `server.job_storage` · `/api/health` 의 `storage` · `rcm check` 의 `storage` 행 · 웹 호스트 카드 한 줄. 스키마 v1 에 **키를 더한다**(값·의미는 안 바꾼다) |
| 57 | 손으로 청소하기 | `rcm gc [--dry-run] [--json] [--timeout]` · `POST /gc`(admin 토큰). dry-run 은 janitor 와 **같은 계획 함수**를 돌린다 — 보장되는 것은 「같은 입력에 같은 판정」이지 「보여준 것과 실제가 같다」가 **아니다**(두 요청 사이에 잡이 끝난다). 실제 gc 응답은 `planned`/`deleted`/`failed` 를 가르고, CLI 타임아웃은 실패가 아니라 **3(모른다)** 이다 |
| 58 | 프리셋이 증거를 남기는 법 | 새 개념을 안 만든다 — 무거운 구간 출력은 `TMPDIR` 이 아니라 **워크스페이스**에 두고 M5e `artifacts` 로 선언하고, 「왜 깨졌는지」는 stdout 으로 흘려 **로그**(30일)에 남긴다. 묶음은 보관소가 아니라 가져가는 통로다(24시간 ≈ 워크스페이스 하루). **프리셋별 TTL 은 안 만든다** |
| 60 | 설정 표면 | 세 키를 `examples/server.toml`(= `templates/server.toml`, `rcm init server` 가 쓰는 파일)에 **주석 없이 기본값과 함께** 넣고 `docs/configuration.md` 에 보존 키 표를 새로 만든다. **데이터를 지우는 값은 설치할 때 파일에서 보여야 한다** — 오너의 운영 설정이 `recent_count` 한 줄만 바뀐 채 나머지가 전부 「보이지 않는 코드 기본값」이었다. 진짜 「고급 설정」 화면이 생기면 이 셋이 거기 첫 줄이다(그 화면은 M5g 범위 밖) (M5g, 오너 요청 2026-09-09) |
| 59 | 실패했을 때만 모으기 | 프리셋 키 `artifacts_on = "always"`(기본 — 오늘의 동작) \| `"failure"`. 초록 잡마다 무거운 로그를 모아 24시간 들고 있을 이유가 없고, 그 비용이 결정 58 의 권고를 안 따르게 만든다. ⚠️ 워커 호출부만 고쳐서는 **원격에서 안 돈다** — claim 응답의 얼린 정책에 `artifacts_on` 을 실어야 하고, 필드가 없는 옛 서버는 `"always"` 로 본다 |
| 61 | 업그레이드 안전 게이트 | `rcm gc --dry-run` 이 **서버 없이도** 돈다 — `rcm check --config` 처럼 설정과 데이터 디렉터리만 읽어(DB 는 읽기 전용) 같은 계획을 낸다. 절차는 `git pull --ff-only` → **오프라인 dry-run** → 서비스 재시작. 초안의 「올리기 전에 dry-run」은 불가능했다: `POST /gc` 는 새 서버에만 있고 새 서버는 뜨자마자 sweep 한다 (M5g, 코덱스 P0) |
| 62 | 무진전 latch | 바닥 규칙으로 지웠는데 여유가 **지운 바이트의 절반도 안 늘면** `no_progress` 를 세우고 그 뒤 자동 sweep 의 **바닥 규칙만** 멈춘다(나이·예산은 돈다). `rcm gc` 나 재시작으로 풀린다. 지워도 `df` 가 안 움직이면 계속 지우는 것은 증거를 태우는 일 말고 아무것도 아니다 (M5g, 코덱스 P1) |

| 63 | 추론된 실패 스텝 | **없앤다.** `failed_step` 은 `::rcm::step-end::fail` 또는 `::rcm::fail::<이름>` 으로 **선언된 것만**이고, 아니면 null 이다. 「어디였나」는 `last_step` 이 말한다. 오늘 라벨이 붙던 잡의 상당수가 앞으로 빈칸이 된다 — CHANGELOG 에 동작 변경으로 적는다 (M5h, 2026-09-09) |
| 64 | 취소·유실 잡 | `failed_step`·`last_step`·실패 이름을 **하나도 안 싣는다**(#176). `timed_out` 은 선언된 것을 그대로 싣는다. `succeeded` 는 잡 자신의 판정이 이겨 실패 이름을 안 남긴다 |
| 65 | 새 마커 | `::rcm::fail::<이름>` 하나로 **스텝과 단위를 같이** 받는다. 이름 120자 · 잡당 100개(넘으면 `failures_truncated`) · 옛 서버는 모르는 kind 라 조용히 무시한다 |
| 66 | 이력 창 | 같은 `key` 의 최근 `failure_window_jobs = 20` 개 **종료 잡**(`succeeded`·`failed`·`timed_out`. 취소·유실은 아무 말도 안 하므로 뺀다), 최소 `failure_min_jobs = 3`. 목록 명령(`rcm flaky`)은 나중에 |
| 67 | 어디에 싣나 | `GET /jobs/{id}` 의 **종료 잡에만**. `/api/status` 는 안 건드린다 — 이미 가장 뜨거운 요청이고(결정 49) 최근 행마다 이력 질의를 붙이면 그 병목 위에 짐을 얹는다 |
| 68 | 분모의 품질 | 이름을 안 찍고 실패한 잡은 분모에 남기고 `window_unnamed` 로 밝힌다. 분자는 과소일지언정 **과대는 아니다**. 판정 문구의 `intermittent?` 물음표는 계약이다 — 판정이 아니라 제안 |
| 69 | 404 | 모르는 경로에 `hint` 를 준다(숫자가 있으면 그 잡의 진짜 경로, 없으면 주요 라우트). `/api/jobs/…` **별칭은 안 만든다** — 한 가지에 이름 하나 |
| 70 | 실패한 대기의 끝줄 | 종료 코드 1·2·3 **모두** `log: rcm logs <N>` 과 URL 을 적는다. 모를수록 로그가 필요하다 |
| 71 | 목록의 코드 신원 | `rcm jobs`·`rcm top` 최근 줄에 `<ref\|branch> @<짧은 sha>`(32자에서 자른다). tree 잡의 `branch` 는 클라이언트가 실어 보내되 **표시용**이다 — `tree_hash` 와 합류 신원은 안 바뀐다 |
| 72 | 대기 클라이언트 | 대기 전에 스냅샷 장부를 놓는다(2만 파일 64 → 32 MB). 게으른 import 다이어트는 **안 한다**(31.6 MB 중 13.3 MB 는 파이썬 자체). `--no-wait` + `rcm wait` 를 메모리가 빠듯한 기계의 패턴으로 문서에 적는다 |
| 73 | 오프라인 dry-run | 살아 있는 DB 는 `mode=ro` 로만 연다. `backup()` 사본 위에서 **실제 마이그레이션과 실제 계획**을 돌리고 사본을 지운다. 어느 단계든 불완전하면 exit 3 — 빈 계획 성공은 없다. 옛 DB(v7)도 이렇게 프리뷰한다(초안의 「스키마가 다르면 계획하지 않는다」를 대체). 「서버 없이 읽기만 한다」던 `rcm gc --dry-run --config` 가 운영 DB 를 7→15 로 올려 옛 빌드가 재시작하지 못한 사고(2026-09-10)의 답이다 (M5i, `docs/gate-replay-fixes-workplan.md` B2) |
| 74 | 마이그레이션 전 백업 | `migrate()` 경계에서 실제 버전 상승 때 한 번, WAL 변경보다 먼저, `backup()`→검증→원자적 rename. **실패면 마이그레이션 중단.** 3개 보존(정리 실패는 경고). 옛 빌드의 거절 메시지가 복원 절차를 가리키고, 복원의 손실 범위를 문서가 밝힌다 (M5i B2) |
| 75 | 가드 | `serve`·`worker` 기존 규칙 + `token`(전부 쓰기) + `gc --dry-run --config`(허용). 쓰기 명령은 유효 `data_dir`(`--data-dir` → `RCM_SERVER_DATA_DIR` → 선택된 설정의 `[server].data_dir` → 기본)이 운영이고 실행 파일이 운영 venv 밖이면 deny. TOML 파싱 실패·`PYTHONPATH` 우회는 범위 밖 (M5i B2-5) |
| 76 | 회계 눈금 둘 | `charged`(링크마다 · 항목 표시·예산)와 `estimated_reclaimable = charged − shared`(일반 파일의 `nlink > 1` 블록 · 바닥·would free·latch). `floor_attempted` 로 latch 를 걸고 분모는 **삭제 성공분**. 하드링크 pack 이 「would free」를 약 1/3 과장하던 것의 답이다 (M5i B4) |
| 77 | 실패 이름 | 계속 마커로만. `fail_patterns`(프리셋이 준 정규식으로 서버가 stdout 을 대 보기)는 **만들지 않는다** — 출력 형식이 바뀌면 거짓 이름이 저장되고, 병적인 정규식이 로그 소비를 막고, 마커와 패턴의 우선순위라는 계약이 하나 더 생긴다. 대신 검증된 래퍼 예시(`examples/preset/name-failures.sh`, 테스트로 잠근다)와 「판정 함수에서 마커」 예시를 `docs/configuration.md` 에 둔다. 마커 없는 실패는 대장이 비는 것이 맞다 (M5i I1) |
| 78 | v16 | 한 번짜리 복구 마이그레이션 — 대장 행이 없는 실패 라벨은 `last_step` 으로, 취소·유실은 비운다. 매 기동 보정은 안 한다. 결정 73 의 사고 뒤 옛 빌드가 쓴 추론 라벨은 v15 가 재실행되지 않아 남기 때문이다 (M5i B2-v16) |
| 79 | 웹 회귀 | 기존 Chrome 테스트를 넓혀 렌더·예외 스모크로 삼고 ubuntu 잡에서 필수로. 표본에 `disk`·`job_storage` 가 있어야 한다 — 정의 안 된 변수 하나(`hostCardHtml` 의 `local`)가 `disk` 있는 모든 배치의 웹을 죽였는데 Chrome 테스트가 **있으면서** 못 잡았다. ESLint 는 보류 (M5i B1·I5) |
| 80 | 업그레이드 절차 | 별도 venv 검증 → 사본 dry-run → pause → drain 확인 → 백업 → 정지 → pull → 기동(마이그레이션) → 확인 → resume. **도는 editable 체크아웃을 먼저 pull 하지 않는다** — 옛 프로세스가 새 모듈을 늦게 import 할 수 있다고 문서 스스로 경고하면서 그렇게 시켰다 (M5i I7·O1, `docs/operating.md`) |
| 81 | 클라 wheel | 서버가 기동할 때 설치된 패키지에서 표준 라이브러리로 자기 wheel 을 조립해 `/client/remote_ci_monitor-<X>-py3-none-any.whl`(정확한 이름만)로 준다. `/api/health.client_wheel`(path · sha256 · bytes). 인증은 읽기 규칙. 조립 실패는 `null` + 503 — 옛 것·빈 것을 주지 않는다. 클라이언트가 서버에게 묻고 서버에게 받으니 GitHub 는 런타임 경로에서 빠진다(결정 30) (M5i I8) |
| 82 | 자동 태그 | `main` 에 push 된 `__version__` 의 태그가 없으면 워크플로가 만들고 릴리스 잡을 같은 run 에서 부른다(`GITHUB_TOKEN` 의 태그는 다른 워크플로를 안 깨운다). 손 태그 단계는 문서에서 뺀다. 밀린 v0.2.5 는 오너가 지금 끊는다 (M5i I8, #85) |
| 83 | 불일치 표시 | `/api/health.min_client_version`(상수 · 계약이 깨질 때만) · `rcm check` 에 `client` 행 · `rcm run` 은 경고만. `rcm self-update` 는 **보류** — pip 로 자기 venv 를 갈아끼우는 일은 설치 방식마다 다르고 도는 프로세스 자신을 바꾼다. 대신 검증된 래퍼 예시를 문서에 (M5i I8) |

⚠️ **결정 번호 39~42 가 두 번 나온다** — M5e(산출물)와 M5f(부하 게이트)가 같은 번호를 각각 붙인 채 머지 `d26b3f4` 에서 합쳐졌다. `docs/m5e-*.md` 와 `docs/m5f-*.md` 가 이미 각자의 번호로 서로를 가리키고 있어 여기서 한쪽을 옮기면 그 문서들의 상호 참조가 깨진다. 고칠 때 한 PR 에서 문서까지 같이 바꾼다. 새 결정은 **51번부터**다.

12~16 은 `docs/wireframes/web-queue.html` 「6. 오너에게 묻는 것」의 5개를 2026-09-04 오너가 확정한 것이다. 17~18 은 `docs/reviews/2026-09-04-codex-m0-design.md` 가 사람 결정이라고 본 것을 추천값으로 구현한 것이다. 바꾸려면 여기서 고친다.

## 참고 구현과 이전 설계

- 한 팀의 비공개 저장소에 있던 dispatch 래퍼(dispatch·가드·합류·대기) · 큐 스크립트(큐·중앙값·잔여 21 자기검증) · top 스크립트(진행률·파서·렌더 18 자기검증) · 그 팀의 원격 dispatch 문서. **가져오는 것**: 큐·ETA 수식과 하한 · 실패/빈 큐 분리 · `top` 두 번째 표본 · 파서 픽스처 · 취소 대신 합류 · 시뮬 공유 직렬화(concurrency 그룹) · 요청자 라벨 `계정@호스트`. **버리는 것**: GitHub API 전부 · run 이름 규약 · `gh` · KST 상수 · `~/actions-runner` 판별 · 팀 스크립트 이름.
- v1/v1.1(GitHub 경로) 계획은 커밋 `9abef42`·`15e8220` 에 역사로만 남는다(결정 30 으로 폐기 — jobs API 함정·rate limit 예산은 더 쓰지 않는다). 큐 판정 규칙은 이미 `core/queue.py` 에 들어갔다.
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
  - 식별자·README·CLI 도움말은 영어, 주석·docstring 은 한국어. 웹 UI 문자열은 한국어 기본 + 영어 선택(결정 36). 커밋 메시지는 Conventional Commits.
  - 브랜치 정책: main·dev 직접 push 금지. `git switch dev && git pull` → `git switch -c <type>/<topic>` → dev 로 PR. 워크트리를 써도 된다.
  - gh 계정·머지: 활성 gh 계정이 다른 계정으로 되돌아가는 일이 있다. 모든 GitHub 동작은 토큰을 고정해라:
      TOK=$(gh auth token --user monocsp); GH_TOKEN=$TOK gh api user --jq .login   # monocsp 인지 확인
      GH_TOKEN=$TOK git push -u origin <branch>; GH_TOKEN=$TOK gh pr create --base dev …
    `gh pr merge` 는 자동 모드 분류기가 막는다. CI 초록을 확인한 뒤 REST 로 머지하고 브랜치를 지워라:
      GH_TOKEN=$TOK gh api -X PUT repos/monocsp/remote_ci_monitor/pulls/N/merge -f merge_method=merge -f commit_title="<제목> (#N)"
      GH_TOKEN=$TOK gh api -X DELETE repos/monocsp/remote_ci_monitor/git/refs/heads/<branch>
  - 기술 결정이 필요하면 codex 크로스리뷰 → docs/reviews/ 기록 → 오너. 결정 항목 확정값에서 벗어나야 할 이유가 생기면 그때 물어라.

끝나면 짧게 보고해라: 만든 것 · 테스트 수와 뮤테이션 결과(몇 종) · 루프백에서 rcm top 이 보여준 것(호스트 표본 포함) ·
올린 PR 과 머지 여부 · 오너가 실기로 확인할 절차 · M2(웹 UI)에서 먼저 정해야 할 것.
```
