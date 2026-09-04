# Codex 크로스리뷰 — GitHub 의존 · 큐/실행 소유권 (2026-09-04 오후)

- 리뷰어: Codex CLI 0.153.0 · gpt-5.5 · `codex exec --sandbox read-only`
- 계기: 오너 질문 「github 에 되게 많이 의존하는 것 같은데 맞아? 의존하지 않으면 좋겠는데」
- 대상: PLAN.md v1.1(GitHub 관찰 + `rcm run` dispatch) · 팀의 원격 실행 구조 문서(2026-07 dispatch 선택 기록)
- 결론: 맞다. 실행·큐·진행·러너 상태·코드 전달·인증·감사까지 GitHub 이 컨트롤 플레인이다. 추천은 백엔드 플러그인 구조 + 로컬 잡 서버 먼저.
- 오너 결정은 PLAN.md 「결정 항목」에 기록한다.

## 프롬프트

너는 아키텍처 크로스리뷰어다. 파일을 수정하지 말고 읽기만 해라. 답은 한국어로, 결론 먼저, 근거는 짧게. 추정 공수는 「사람 하루」 단위의 거친 범위로만.

## 상황
- 작업 디렉터리의 `PLAN.md`(v1.1, 534줄)는 self-hosted GitHub Actions 러너(Mac mini 1대)를 **GitHub REST 로 관찰**하고, 세션이 `rcm run` 으로 **workflow_dispatch 를 넣고** `rcm wait` 로 결과를 종료 코드로 받는 도구의 계획이다. 끝까지 읽어라.
- 참고: 팀의 기존 원격 실행 구조 문서(읽기 전용) /Users/fmmentalcare/Documents/GitHub/dolomood-ci-monitor/docs/renew-guide/ci-cd/30-remote-dispatch.md — 2026-07 에 「Tailscale SSH · 웹훅 리스너 · 폴링 데몬」과 비교해 GitHub dispatch 방식을 골랐던 기록이 있다(큐·권한·감사로그가 공짜, 인바운드 포트 불필요).
- 오너(1인 개발, Mac mini M4 10코어 24GB 한 대, 여러 컴퓨터의 Claude Code 세션에서 CI·QA·배포를 던짐, Tailscale 사용)가 방금 이렇게 말했다:
  「github에 되게 많이 의존하는 거 같은데 맞아? github에 의존하지 않으면 좋겠는데?」

## 오너가 실제로 원하는 것(오늘 확인한 여섯 항목)
1. 세션 ↔ 실행 머신 연결 방식이 명확할 것
2. 실행 머신(켜는 곳)과 다른 세션(쓰는 곳)의 연결
3. CPU · RAM · GPU 사용량 실시간 트래킹
4. 여러 세션의 요청이 몰리면 큐에 쌓여 순차 처리
5. 대기 중인 세션에 위치·예상 시간 전달
6. 완료·실패를 세션에 전달하고, 받는 쪽이 종료 코드로 바로 분기

오너가 오늘 정한 것: 디스패치까지 도구가 담당한다 · 결과 전달은 폴링 명령(webhook · PR status 는 범위 밖) · 서버는 Tailscale/LAN 안에서만 · 읽기 인증 none.

## 검토해 달라는 것
A. 현재 계획에서 GitHub 이 맡는 역할을 전부 나열해라(실행 엔진 · 큐 · 진행 데이터 · 러너 상태 · 감사 · 코드 전달 · 인증 등). 「GitHub 의존을 없앤다」면 그중 무엇을 이 도구가 직접 만들어야 하고, 무엇은 안 만들어도 되는가.
B. **GitHub-free 잡 서버** 최소 설계를 그려라: 세션이 Tailscale 로 `POST /jobs`(레포·ref 또는 로컬 트리 전송·프리셋 명령) → 서버가 큐(재시작 내구성)에 넣고 → 워커가 순차 실행(로그 캡처 · 스텝 마커) → `/api/status` · `rcm wait` · SSE. 코드 전달은 `git fetch` 대 세션에서 rsync 로 미커밋 트리 전송 — 어느 쪽이 맞나(오너 팀은 「dispatch 는 원격 HEAD 만 본다」 함정 때문에 가드 3겹을 만들었었다). **원격에서 명령을 실행시키는 서버**라 보안(인증 · 프리셋만 허용 vs 임의 명령 · 워크스페이스 격리)을 어떻게 잡아야 하는가.
C. 세 갈래를 비교해라: ① 현재대로 GitHub 위에(관찰 + dispatch) ② GitHub-free 잡 서버(기존 Actions 러너는 배포·QA 용으로 그냥 두고, 이 도구는 자기 큐로 게이트를 돈다) ③ 백엔드 플러그인(순수 계층·스키마·UI·wait 는 공용, `github` 백엔드와 `local` 백엔드 둘 다). 각각 오너의 여섯 항목을 얼마나 잘 채우는지, 잃는 것, 공수, 「지금 PLAN 에서 살아남는 부분」.
D. ③ 의 추상화가 실제로 성립하는가(관찰 도구와 실행 도구를 한 인터페이스로 묶는 게 새는 추상화가 아닌지). 성립한다면 인터페이스의 최소 형태.
E. 이 오너에게 네가 추천하는 갈래와 이유. 7월에 dispatch 를 골랐던 근거(큐·권한·감사로그 공짜, 인바운드 포트 불필요)가 지금 질문(큐·실행을 누가 소유하나)에도 그대로 적용되는가.
F. 오너에게 물어야 할 결정 — 답에 따라 만들 물건이 달라지는 것만, 각각 추천 답 포함.

## 리뷰 원문

**결론**

맞다. 현재 `PLAN.md`는 “GitHub를 관찰하는 도구”가 아니라, 실행 요청·큐·러너 상태·진행 데이터·코드 전달·인증·감사까지 GitHub Actions를 컨트롤 플레인으로 쓰는 설계다. GitHub 의존을 줄이려면 단순히 REST 호출을 빼는 수준이 아니라 **잡 서버와 실행 큐를 직접 소유**해야 한다.

추천은 **③ 백엔드 플러그인 구조**다. 다만 순서는 `local` 백엔드를 먼저 세워서 GitHub-free 잡 서버를 만들고, `github` 백엔드는 기존 Actions 기반 실행·배포·QA 호환용으로 남긴다. 현재 GitHub-only 계획을 그대로 밀면 오너의 “큐·실행을 누가 소유하나” 질문에 답하지 못한다.

참고: 요청 경로의 `dolomood-ci-monitor`는 현재 파일시스템에서 보이지 않았고, 같은 문서는 `dolomood-app-renew` 및 그 worktree에서 확인했다.

**A. GitHub가 맡는 역할**

현재 계획에서 GitHub가 맡는 것:

| 역할 | 현재 GitHub 담당 | GitHub-free면 |
|---|---|---|
| 실행 엔진 | Actions runner가 workflow/job/step 실행 | 직접 워커·프로세스 실행기 필요 |
| 큐 | Actions queued/in_progress + 러너 1대 FIFO | 직접 durable queue 필요 |
| 디스패치 | `workflow_dispatch` | `POST /jobs` 필요 |
| 진행 데이터 | runs/jobs/steps API | 직접 job/step 상태 저장 필요 |
| 러너 상태 | Actions runners API | 워커 heartbeat 필요 |
| 코드 전달 | Actions checkout이 원격 ref/sha fetch | `git fetch` 또는 rsync materializer 필요 |
| 인증 | PAT, GitHub actor, Actions 권한 | 쓰기 인증 직접 필요 |
| 감사 | run UI, actor, timestamps | job DB 감사 로그 필요 |
| 결과 | conclusion, run URL, job summary | exit code, log, summary 직접 저장 |
| 중복 합류 | runs API에서 sha/ref 비교 | queue에서 idempotency key 직접 구현 |
| rate/장애 모델 | GitHub API rate limit에 종속 | 내부 서버 장애·DB 장애 처리 필요 |

직접 만들 필요 없는 것:

- GitHub Actions YAML 호환 엔진 전체
- Marketplace action 실행 모델
- PR status/comment 갱신
- hosted runner 과금/스케줄링
- GitHub 수준의 조직 권한 모델 전체
- 인터넷 공개형 멀티테넌트 서비스

오너 요구는 “여러 세션이 한 Mac mini에 순차 실행을 넣고 결과 코드를 받는 것”이므로, 최소 구현은 **프리셋 명령 실행 서버**면 된다.

**B. GitHub-free 잡 서버 최소 설계**

구조:

```text
세션 rcm run
  -> Tailscale/LAN POST /jobs
  -> server SQLite queue
  -> worker 1 lane sequential
  -> per-job workspace
  -> preset command 실행
  -> logs + step markers 저장
  -> /api/status, /jobs/{id}, /events SSE
  -> rcm wait 종료 코드
```

필수 컴포넌트:

- `SQLite WAL`: jobs, events, attempts, logs index, requester, source hash
- `POST /jobs`: preset, repo, source mode, ref/base sha, inputs, idempotency key
- `worker`: 한 번에 1개 실행, timeout, cancel, retry 없음 또는 명시
- `log capture`: stdout/stderr 파일 저장, 최근 tail은 status에 포함
- `step marker`: 프리셋 스크립트가 `::rcm step name` 같은 라인을 찍으면 서버가 파싱
- `SSE /events`: UI와 `rcm wait`가 폴링보다 빠르게 반응
- `GET /api/status`: PLAN의 공용 `StatusModel` 유지
- `rcm wait`: `success=0`, failure=1, cancelled/timeout=2, unknown/server error=3

코드 전달은 **하이브리드가 맞다**.

| 방식 | 맞는 곳 | 이유 |
|---|---|---|
| `git fetch ref/sha` | 배포, 릴리스, 원격 dev QA | 감사 가능하고 재현성 좋음 |
| 세션에서 rsync/tar 전송 | 게이트, “지금 고친 코드” 검사 | 미커밋·미푸시가 빠지는 dispatch 함정을 제거 |

이 팀은 이미 “dispatch는 원격 HEAD만 본다” 때문에 3중 가드를 만들었다. GitHub-free로 가는 핵심 이득은 그 함정을 없애는 것이므로, **게이트 기본값은 rsync working tree 전송**이어야 한다. 대신 deploy류는 반드시 remote ref만 허용한다.

보안 기본값:

- `POST /jobs`는 인증 필수. 읽기 none은 괜찮지만 쓰기 none은 안 된다.
- Tailscale/LAN + per-client bearer token 또는 Tailscale identity 확인.
- 임의 명령 금지. 서버에 등록된 preset만 실행.
- preset별 input schema 검증.
- 셸 보간 금지. argv 배열 또는 고정 스크립트 호출.
- job별 clean workspace, job 종료 후 정리/retention.
- payload 크기 제한, `.git`, secrets, build cache 제외 규칙.
- 전용 macOS 사용자로 실행. 가능한 한 로그인 키체인·개인 홈과 분리.
- deploy preset은 gate preset보다 더 강한 권한과 source 제한.

**C. 세 갈래 비교**

| 갈래 | 여섯 요구 충족 | 잃는 것 | 공수 | PLAN에서 살아남는 부분 |
|---|---:|---|---:|---|
| ① GitHub 위에 유지 | 4.5/6 | 큐 소유권 없음, GitHub 장애/rate limit, 원격 HEAD 함정, Actions API 의미에 종속 | 8-15 사람 하루 | 거의 전부 |
| ② GitHub-free 잡 서버 | 6/6 | GitHub run UI, 공짜 권한/감사, Actions 생태계, 재현 가능한 remote sha 기본값 | 15-30 사람 하루 | UI, status schema, queue 계산, host collector, wait exit code, fail-open 원칙 |
| ③ 백엔드 플러그인 | 5.5-6/6 | 초기 추상화 비용, 인터페이스 설계 실패 위험 | 18-35 사람 하루 | 가장 많이 살아남음. `github` I/O만 backend로 이동 |

①은 오너 질문에 정면 답이 아니다. ②는 가장 단순하게 요구를 만족한다. ③은 public 도구와 기존 GitHub 실행 경로를 둘 다 살린다.

**D. 추상화 성립 여부**

성립한다. 단, “GitHub runs API”와 “local process”를 억지로 같은 내부 모델로 맞추면 샌다. 최소 공통 개념은 **JobBackend**다.

최소 인터페이스:

```text
submit(JobSpec) -> SubmittedJob
get_job(JobRef) -> JobSnapshot
get_status() -> StatusModel
estimate(JobQuery) -> Estimate
stream(JobRef) -> EventStream optional
cancel(JobRef) -> optional
capabilities() -> BackendCapabilities
```

`JobSpec` 핵심 필드:

- `preset`
- `source`: `git_ref | rsync_tree`
- `repo`, `ref`, `sha`, `base_sha`
- `inputs`
- `requester`
- `idempotency_key`

`github` backend는 `source=git_ref`만 지원하고 `workflow_dispatch`로 submit한다. `local` backend는 `rsync_tree`와 `git_ref` 둘 다 지원한다. 차이는 `capabilities`에 드러내면 된다.

**E. 추천**

추천: **③으로 방향 전환, 첫 구현은 local backend 중심**.

이유는 짧다. 오너의 핵심 불만은 “GitHub를 많이 본다”가 아니라 “실행과 큐를 GitHub가 소유한다”다. 그러면 현재 계획의 GitHub-only 설계는 핵심 질문을 회피한다. 반면 local backend는 여섯 항목을 직접 만족하고, GitHub backend를 남기면 기존 Actions 배포·QA와 public 사용자도 버리지 않는다.

7월의 dispatch 선택 근거는 당시에는 맞았다. 큐·권한·감사로그가 공짜였고 인바운드 포트도 안 열었다. 하지만 지금은 서버를 Tailscale/LAN 안에서만 두기로 했고, 질문도 “실행 요청을 누가 소유하나”로 바뀌었다. 따라서 그 근거는 **관찰/배포에는 여전히 유효하지만, 게이트 잡 서버 선택에는 그대로 적용되지 않는다**.

**F. 오너에게 물을 결정**

1. 게이트 코드 전달 기본값  
   추천: `rsync_tree`. 배포·릴리스만 `git_ref`.

2. 서버가 받을 명령 범위  
   추천: preset만 허용. 임의 명령은 만들지 않음.

3. `POST /jobs` 쓰기 인증  
   추천: Tailscale/LAN + per-client bearer token. 읽기 none 유지.

4. GitHub Actions를 어디까지 남길지  
   추천: 배포·QA는 당분간 Actions 유지, 게이트만 local backend로 이전.

5. 로그·잡 보존 기간  
   추천: SQLite job 기록 30일, full log 14일, 실패 log 30일.

6. rsync에 포함할 트리 범위  
   추천: `.git`, secrets, build output, dependency cache 제외. base sha와 tree hash는 반드시 기록.

7. PR status/comment를 계속 범위 밖으로 둘지  
   추천: 지금은 범위 밖 유지. `rcm wait`와 SSE만 먼저 끝낸다.