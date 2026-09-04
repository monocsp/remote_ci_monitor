# 서브에이전트 리서치 — 잡·큐·태스크 관리 제품과의 비교 (2026-09-04 오후)

- 리서처: Claude 서브에이전트(격리 워크트리, 웹 검색·문서 읽기). 스크린샷은 직접 보지 못했고 문서·소스 문자열·API 스키마를 근거로 삼았으며 그런 곳은 「추정」으로 표시했다.
- 대상: `docs/wireframes/web-queue.html` v1.2(항목 31개·변형 12개) · `PLAN.md` v2
- 반영은 v1.3 에서(기획 누락 리뷰와 합쳐서). 아래는 원문.

## 0. 비교 기준

우리 화면의 질문 순서는 「내 잡은 몇 번째 · 언제 끝나나 · 왜 안 움직이나 · 머신이 버거운가 · 뭐가 실패했나」다(요약 줄 23·24·25 → 큐 표 5~13 → 호스트 16 → 최근 14 → Estimates 15). 각 제품을 이 다섯 질문에 어떻게 답하는지로 읽었다.

## 1. 제품별 비교

### CI · 빌드 큐

**Buildkite** — 파이프라인 빌드 페이지(canvas·table·waterfall)와 클러스터 큐 메트릭 페이지.
- 잘 하는 것: 잡 상태가 대기 단계별로 갈라져 있다 — `limiting/limited`(concurrency 그룹 대기) · `scheduled`(에이전트 대기) · `assigned/accepted`(잡았지만 아직 시작 전) · `timing_out`·`canceling`(진행형) · `expired`(아무도 안 집어가서 만료). 큐 페이지는 「Jobs Waiting」「Jobs Running」「Agents Connected(busy/idle)」에 **「Current Wait」 대기 백분위**와 **데이터 신선도 표시**를 10초마다 갱신한다. 캔버스 노드는 소요를 직접 보이고 **아직 안 시작한 스텝은 `--` 자리표시자**를 쓴다. 사이드바는 「state 로 묶기」 토글이 있다. 반대로 사용자 피드백 #477 은 「waiting for agent」 노란 배지만 보이고 **이 잡을 돌릴 수 있는 에이전트가 몇 개인지**를 안 알려준다고 지적했다 — 우리 Reason 열(11)이 정확히 이 빈틈을 겨냥한다.
- 일부러 안 할 것: 26개짜리 상태 머신, 30일 만료(우리는 `upload_abandon_seconds` 300초와 `lost` 로 충분), 유료 플랫폼 제한 상태.
- 출처: [잡 상태](https://buildkite.com/docs/pipelines/configure/defining-steps) · [큐 메트릭](https://buildkite.com/docs/pipelines/cluster-queue-metrics) · [빌드 페이지](https://buildkite.com/docs/pipelines/build-page) · [타임아웃·만료](https://buildkite.com/docs/pipelines/configure/build-timeouts) · [잡 타임스탬프 `runnable_at`/`started_at`](https://buildkite.com/docs/apis/rest-api/builds) · [피드백 #477](https://github.com/buildkite/feedback/issues/477) · [노드 소요·`--` 변경로그](https://buildkite.com/resources/changelog/page/2/)

**GitHub Actions** — 워크플로 run 목록 → run 요약(잡 그래프) → 잡 로그.
- 잘 하는 것: 실패하면 **실패한 스텝이 자동으로 펼쳐지고**, 「Search logs」가 있으며(펼친 스텝만 검색), 로그 줄 permalink 가 있다. 잡 그래프의 각 노드는 상태 아이콘 + 이름 + 소요. check-run 스키마가 `status`(queued/in_progress/completed/waiting/pending)와 `conclusion`(success/failure/cancelled/skipped/timed_out/stale/…)을 **두 축으로 분리**한다. 시각은 `<relative-time>` 요소로 「5 minutes ago / in 5 minutes」, 축약형 `4h`·`21m`·`37s`, 30일 넘으면 절대 날짜로 자동 전환.
- 못 하는 것(우리가 안 따라갈 것): 대기 중 잡은 「Waiting for a runner to pick up this job...」 한 줄뿐 — 순번·앞 잡·ETA 없음(커뮤니티가 대기 타임아웃·알림을 요구 중). 러너 상태도 Settings 깊숙이 Idle/Active/Offline 만.
- 출처: [로그 화면](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs) · [그래프](https://docs.github.com/en/actions/how-tos/monitor-workflows/use-the-visualization-graph) · [check-run status/conclusion](https://docs.github.com/en/rest/checks/runs) · [relative-time](https://github.com/github/relative-time-element) · [러너 상태](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/monitoring-and-troubleshooting-self-hosted-runners) · [「Waiting for a runner」 토론](https://github.com/orgs/community/discussions/31587) · [대기 타임아웃 요구](https://github.com/orgs/community/discussions/50926) · [아이콘 요약(Graphite)](https://graphite.com/guides/github-actions-status)

**GitLab CI** — 파이프라인 목록 · 잡 목록 · 잡 상세(로그 + 오른쪽 사이드바).
- 잘 하는 것: 상태에 `waiting_for_resource`·`preparing`·`canceling` 이 있고, `resource_group` 대기 잡의 페이지는 **「This job is waiting for resource: <group>」 + 「View job currently using resource」 링크** — 우리 `⛓ blocked by #409 · devices`(11)와 같은 발상이다. 사이드바는 실행 중엔 **「Elapsed time」**, 끝나면 **「Duration」** 으로 라벨을 바꾸고, **「Queued」(큐 대기 시간)** 과 「Finished」(상대 시각)를 따로 둔다. 서버는 멈춘 잡을 `stuck_pending_no_matching_runners`(1시간)·`no_updates_running`(30분) 같은 **사유 코드**로 떨어뜨린다. Pajamas 색: pending=orange, running=blue, success=green, failed=red, **canceled=red**.
- 일부러 안 할 것: 실패 사유를 파이프라인 그래프 hover 로만 보이는 것, canceled 를 빨강으로(아래 3절).
- 출처: [잡 상태](https://docs.gitlab.com/ee/ci/jobs/) · [resource_group UI](https://docs.gitlab.com/ee/ci/resource_groups/) · [stuck 사유](https://docs.gitlab.com/ee/ci/jobs/job_troubleshooting.html) · [Queued 표시 MR](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/91087) · [Elapsed time/Duration MR](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/76668) · [Pajamas 아이콘 색](https://design.gitlab.com/product-foundations/iconography/) · [ETA 요구 이슈](https://gitlab.com/gitlab-org/gitlab/-/issues/17218)

**CircleCI** — 워크플로 그래프와 잡 목록.
- 배울 것: 워크플로 `QUEUED` 를 「serial group 때문」이라고 정의해 **대기 사유가 상태 이름에 박혀** 있다. `ON HOLD`(승인 대기)·`UNAUTHORIZED` 도 분리.
- 안 할 것: 「Blocked/Not Running」이 「의존 대기」와 「아직 안 시작」을 겸해 지원 문서가 「대개 동시성 한계」라고 풀이해야 한다 — 상태 하나에 뜻 둘은 우리 Reason 열이 막는 함정.
- 출처: [잡 상태](https://circleci.com/docs/guides/orchestrate/jobs-steps/) · [워크플로 상태](https://circleci.com/docs/guides/orchestrate/workflows/) · [Not Running/Queued 지원 문서](https://support.circleci.com/hc/en-us/articles/48487239978651-Jobs-Queuing-or-Stuck-in-Not-Running-Preparing-State)

**Jenkins(classic 큐 + Blue Ocean)** — 좌측 「Build Queue」·「Build Executor Status」 패널, Blue Ocean run 상세.
- 잘 하는 것: 큐 항목마다 **사유 문자열**이 있다: 「Waiting for next available executor on 'X'」「Blocked by 'Y'」「'X' is offline」「All nodes of label 'X' are offline」「In the quiet period. Expires in N」「A build is already in progress」. 실행기 진행 막대는 `getProgress()` 가 **99% 에서 멈추고**, 잔여는 초과하면 「N/A」, `isLikelyStuck()` 은 **예상의 10배(예상 없으면 24시간)** 를 넘으면 참. 예상치는 **최근 성공 3건 평균**(없으면 실패 3건, 최대 6건 거슬러). 시간 표기는 「{0} sec / {0} min / {0} hr」. Blue Ocean 은 실패 시 **실패 스텝의 콘솔 로그를 기본으로 열고**, 색은 blue=진행·green·yellow(unstable)·red·**gray=aborted**.
- 안 할 것: 평균(우리는 중앙값), 초과 시 「N/A」로 숨기기(우리는 20번처럼 `over by 3:31` 을 앞세움), 전체 진행 막대(12번 규칙과 충돌).
- 출처: [큐 문자열 Messages.properties](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/resources/hudson/model/Messages.properties) · [Executor.java](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Executor.java) · [Job.getEstimatedDuration](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/java/hudson/model/Job.java) · [시간 표기](https://github.com/jenkinsci/jenkins/blob/master/core/src/main/resources/hudson/Messages.properties) · [Blue Ocean](https://www.jenkins.io/doc/book/blueocean/pipeline-run-details/)

### 배포 큐

**Vercel** — 프로젝트 Deployments 목록·상세.
- 배울 것: 상태 열거 `QUEUED/BUILDING/INITIALIZING/READY/ERROR/CANCELED/BLOCKED` 에 `readySubstate`(STAGED/ROLLING/PROMOTED), `buildingAt`·`ready` 타임스탬프, 그리고 **취소·오류에도 `errorMessage`**(예: "The Deployment has been canceled because this project was not affected"). 큐는 FIFO, 대기 배포에 **「Start Building Now」**(줄 건너뛰기)와 「Prioritize Production Builds」. 같은 브랜치 큐는 중간 커밋을 **건너뛴다** — 우리 「합류」와 같은 문제의식이나 우리는 동일 트리만 합친다.
- 안 할 것: 큐 위치·대기 시간을 안 보이는 것(과금으로 해결), 브랜치 단위 스킵.
- 출처: [빌드 큐](https://vercel.com/docs/builds/build-queues) · [Start Building Now·우선순위](https://vercel.com/docs/builds/managing-builds) · [state·readySubstate·errorMessage](https://vercel.com/docs/rest-api/deployments/list-deployments)

**Netlify** — Deploys 목록·상세(요약 + 로그).
- 배울 것: 대기 상태를 **「Enqueued: Awaiting Capacity」**, 머신 기동을 「Starting Up」 으로 라벨에 사유를 박았다. 목록 행에 「Deployed in 1m 25s」. 성공 배포는 로그 위에 「Deploy summary」. 포럼 사례: 끝난 빌드가 「Building」 에 걸려 슬롯을 물고 뒤 빌드가 전부 Canceled/Enqueued — 우리 27(워커 다운)·30(취소 진행)·`lost` 설계가 막으려는 바로 그 장면.
- 안 할 것: 상태 라벨에 콜론으로 사유 붙이기(우리는 상태 필 6 과 Reason 11 을 분리 — 더 낫다).
- 출처: [빌드 상태 블로그](https://www.netlify.com/blog/2020/09/17/netlify-releases-to-help-you-optimize-deploy-time-understand-build-states-and-more/) · [Deploy summary/log](https://docs.netlify.com/site-deploys/overview/) · [포럼 사례](https://answers.netlify.com/t/stuck-building-deploy-is-holding-the-build-slot-later-builds-canceled-enqueued-awaiting-capacity/166549)

**Render** — Deploys 탭.
- 배울 것: 「Overlapping Deploy Policy = Wait」: 진행 중 배포가 끝나면 **가장 최근 것만** 돌리고, 진행 중을 취소하면 **대기 중인 것을 즉시 시작**. 상태 「Build in progress / Deploying / Live / Build failed / Update failed / Canceled」로 실패 단계가 이름에 들어 있다.
- 안 할 것: Override(새 요청이 진행 중을 죽임) — 게이트 잡엔 위험.
- 출처: [Render deploys](https://render.com/docs/deploys)

### 백그라운드 잡 대시보드

**Sidekiq Web UI** — Dashboard·Busy·Queues·Retries·Scheduled·Dead.
- 배울 것: 큐마다 **「Latency」(가장 오래된 잡이 들어온 뒤 경과)** 한 숫자로 「얼마나 밀렸나」를 답한다. Busy 는 Process/Thread/Jobs/Arguments/**Started** 열과 Quiet·Stop 버튼, 헤더에 **「Live Poll / Stop Polling」 토글**. Retries 는 「Next Retry · Retry Count · Error Message」.
- 안 할 것: Dead/Retry 집합(우리는 재시도 없음), 인수 노출(우리는 입력 칩 툴팁 7).
- 출처: [Monitoring 위키(latency 정의)](https://github.com/sidekiq/sidekiq/wiki/Monitoring) · [UI 문자열 en.yml](https://github.com/sidekiq/sidekiq/blob/main/web/locales/en.yml)

**Celery Flower** — Workers·Tasks·Monitor.
- 배울 것: Tasks 표가 **「Received」와 「Started」를 따로** 둬 큐 대기가 보이고, Runtime·Worker·Retries·ETA·Expires 열, 상단에 STARTED/SUCCESS/FAILURE/RETRY 필터 버튼.
- 안 할 것: 17열 표 — 우리 7열(5)이 맞다.
- 출처: [README](https://github.com/mher/flower) · [tasks.html 열](https://github.com/mher/flower/blob/master/flower/templates/tasks.html)

**RQ dashboard** — 큐·워커·잡 목록. `--poll-interval` 로 갱신 주기를 노출하고 상태는 queued/started/finished/failed/deferred/scheduled. 우리 2번(live→polling 폴백)보다 단순하다. [출처](https://github.com/Parallels/rq-dashboard)

**Oban(Web)** — 상태별 카운트·큐별 executing/available·pause/scale.
- 배울 것: 상태 `available/scheduled/executing/retryable/completed/cancelled/discarded` 와 `attempted_by`(어느 노드가 잡았나). 노드가 죽어 `executing` 에 박힌 잡을 **「orphaned」** 라 부르고, Lifeline 이 1시간 뒤 available/discarded 로 구조하되 문서가 **「진짜 실행 중인 잡을 옮겨 중복 실행을 일으킬 수 있다」** 고 경고한다 — 우리가 `lost` 를 자동으로 `queued` 로 되돌리지 않는 결정(fail-open 금지)의 근거로 인용할 만하다. Oban Web 은 **탭이 가려지면 자동 일시정지**, 갱신 주기 조절, 관리자만 조작.
- 안 할 것: 자동 구조.
- 출처: [Oban.Job 상태](https://oban.hexdocs.pm/Oban.Job.html) · [Oban.Lifeline](https://oban.hexdocs.pm/Oban.Lifeline.html) · [Oban Web 개요](https://oban-web.hexdocs.pm/overview.html)

**Hangfire** — Jobs(Enqueued/Scheduled/Processing/Succeeded/Failed/Deleted/Awaiting)·Retries·Servers, 잡 상세.
- 배울 것: 잡 상세의 **「State」 이력 카드**: 상태 이름 · 상대 시각 · 전이 사이 소요 · **Reason 문구**("Retry attempt 2 of 10: <예외 50자>", "Exceeded the maximum number of retry attempts."). 「왜 이 상태가 됐나」가 이력에 남는다.
- 안 할 것: Requeue 기본 버튼.
- 출처: [JobDetailsPage.cshtml](https://github.com/HangfireIO/Hangfire/blob/main/src/Hangfire.Core/Dashboard/Pages/JobDetailsPage.cshtml) · [Reason 문자열](https://github.com/HangfireIO/Hangfire/blob/master/src/Hangfire.Core/AutomaticRetryAttribute.cs) · [대시보드 개요](https://www.hangfire.io/overview.html)

### 워크플로 오케스트레이션

**Temporal Web UI** — Workflows 목록·상세(Timeline/Compact/JSON)·Task Queues.
- 배울 것: `Cancelled`(취소 요청을 **잡이 처리함**)와 `Terminated`(강제 종료)를 가르고 `TimedOut` 이 별도. Task Queue 페이지는 폴링 워커 수를 세고 **「If no Workers are polling, an error displays」** — 우리 27 과 같은 규칙.
- 안 할 것: 취소 2단계.
- 출처: [Web UI](https://docs.temporal.io/web-ui) · [상태 정의](https://docs.temporal.io/workflow-execution)

**Airflow** — DAGs 목록·Grid·Graph·Task Instance 상세.
- 배울 것: Task Instance 페이지의 **「Dependencies Blocking Task From Getting Scheduled」 표(Dependency | Reason)**. 사유 문구 예: 「Pool Slots Available: Not scheduling since there are 0 open slots in pool default_pool and require 1 pool slots」. 상태 색은 설정 가능하며 기본 예시가 queued=darkgray, scheduled=tan, running=#01FF70, success=#2ECC40, failed=firebrick, upstream_failed=orange, up_for_retry=yellow, deferred=mediumpurple.
- 안 할 것: 상태 12개·색 12개(색만으로 구분 — 색맹에 약함), Grid 매트릭스.
- 출처: [UI](https://airflow.apache.org/docs/apache-airflow/stable/ui.html) · [task.html](https://github.com/apache/airflow/blob/2.10.5/airflow/www/templates/airflow/task.html) · [pool 사유 문자열](https://github.com/apache/airflow/blob/2.10.5/airflow/ti_deps/deps/pool_slots_available_dep.py) · [상태 색](https://airflow.apache.org/docs/apache-airflow/2.10.5/howto/customize-ui.html) · [원 PR #1729](https://github.com/apache/airflow/pull/1729)

**Prefect** — Flow runs 목록·상세.
- 배울 것: `Late`(**예정 시각이 지났는데 15초 안에 PENDING 이 안 됨**), `AwaitingConcurrencySlot`, `Cancelling`, `Crashed`(**인프라 문제**, 코드 실패 `Failed` 와 분리), `TimedOut`(FAILED 유형). 우리 `lost`↔Crashed, `upload_stalled`↔Late 가 대응한다.
- 출처: [States](https://docs.prefect.io/v3/concepts/states)

**Dagster** — Runs(Queued/In Progress 탭)·Deployment > Daemons/Concurrency.
- 배울 것: **Daemons 페이지가 「Run queue」 데몬의 생사를 보이고**, 죽은 run 이 잡은 슬롯을 **「Free concurrency slots」** 로 푼다. `STARTING` 에서 워커가 안 뜨면 박힌다는 문서 — 우리 `uploading→queued` 사이 정체(31)와 같은 종류.
- 안 할 것: 태그 기반 동시성 규칙 편집 UI.
- 출처: [동시성 문제해결](https://docs.dagster.io/guides/operate/managing-concurrency/troubleshooting-concurrency) · [Run monitoring](https://docs.dagster.io/deployment/execution/run-monitoring) · [run 상태 소스](https://docs.dagster.io/_modules/dagster/_core/run_coordinator/queued_run_coordinator)

### 태스크 관리

**Linear** — 이슈 목록·보드.
- 배울 것: 상태 **카테고리 순서 고정**(Backlog→Unstarted→Started→Completed→Canceled), 우선순위 5단(No/Low/Medium/High/Urgent)을 **한 키 `P`, `Shift+1~4`** 로, 「우선순위 없음은 항상 맨 뒤」. 상태 아이콘이 원의 채움으로 진행을 나타내는 것은 문서에 글로 없어 **추정**.
- 안 할 것: 우선순위(PLAN 큐 규칙: M5 전엔 없음), 드래그 정렬.
- 출처: [Issue status](https://linear.app/docs/configuring-workflows) · [Priority](https://linear.app/docs/priority)

**Asana Timeline** — 의존선이 평소 회색, **날짜가 충돌하면 빨간 선**(포럼·도움말 제목 기준, 본문은 못 읽어 추정). 우리 「delays #4」(20)를 선으로 그리면 같은 효과지만 표에선 링크가 맞다. [출처](https://forum.asana.com/t/dependencies-red-line-on-same-day/62730) · [도움말](https://help.asana.com/s/article/managing-tasks-and-dependencies-with-timeline?language=en_US)

### 시스템 모니터

**htop / btop** — CPU 막대가 **「[low/normal/kernel/irq/soft-irq/steal/guest/io-wait used%]」 로 구간이 쌓이고**, 메모리도 Used/Shared/Compressed/Buffers/Cache/Available 계층, `--no-color` 단색 모드가 있다. btop 은 갱신 주기를 **「2000ms 이상 권장(그래프 표본 품질)」**, TTY 16색 모드, Apple Silicon GPU 지원. 우리 16 은 CPU 막대가 단일 구간이고 user/sys 는 글자뿐. 프로세스 시그널·코어별 막대는 안 한다. 출처: [htop Action.c 도움말](https://github.com/htop-dev/htop/blob/main/Action.c) · [htop man](https://man7.org/linux/man-pages/man1/htop.1.html) · [linux/Platform.c 메모리 계층](https://github.com/htop-dev/htop/blob/main/linux/Platform.c) · [btop README](https://github.com/aristocratos/btop)

**Grafana** — Stat 은 Color mode None/Value/Background, 임계값→색, **「No value」 기본 하이픈 `-`**(0 아님), sparkline(Area). State timeline 은 **null 을 간격으로 그리고 「Connect null values: Never/Always/Threshold」** 로 낡은 구간을 잇지 않는다. 우리 19(stale 빗금)·22(조회 실패≠빈 큐)와 같은 원칙. 연속 팔레트(Green-Yellow-Red 등)는 안 쓴다. 출처: [Stat](https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/stat/) · [State timeline](https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/state-timeline/) · [표준 옵션](https://grafana.com/docs/grafana/latest/panels-visualizations/configure-standard-options/)

## 2. 종합

### 2-1. 가져올 패턴 Top 10 (우선순위 순)

| # | 어디서 | 우리 항목 | 구체적으로 |
|---|---|---|---|
| 1 | Airflow 「Dependency · Reason」 표, Buildkite #477 | **11 · 24** | 정상 대기도 근거를 숫자로: `waiting for lane · 2/2 busy (#412, #409) · 1 ahead`. `blocked by #409 · devices` 뒤에 **`· frees in 2:40`**(막는 잡의 remaining). 24 「Not moving」 첫 줄은 「행동 가능」순: down → stalled → blocked → overdue |
| 2 | Jenkins `isLikelyStuck`(10×) | **20 · 24** | 「overdue」(expected 초과)와 「likely stuck」(k×expected, 설정 `stuck_multiplier` 기본 3)을 갈라 문구·글리프를 다르게: `over by 3:31` vs `⚠ likely stuck · 3× expected · no log for 4m`(log_tail 마지막 수신 나이 함께). 24 에서 stuck 을 overdue 위에 |
| 3 | Hangfire 상태 이력 + Reason, Temporal Timeline | **13 · 14** | 잡 서랍 맨 위에 토큰 없이 보이는 **전이 타임라인**: `uploading 09:50:40 → queued 09:50:52 (waited 21s) → running 09:51:13 → cancelled 09:53 by carol@mbp`, `timed out · limit 20m · SIGTERM→SIGKILL`. 로그만 토큰 |
| 4 | GitLab 「Queued」·「Elapsed time/Duration」, Flower Received/Started, Buildkite `runnable_at→started_at` | **10 · 14 · 15** | running 행 `0:59 · waited 33s`, 최근 행에 `waited` 열 추가, Estimates 에 키별 **중앙 대기시간** 한 줄. 열 머리 「Elapsed / wait」는 상태별로 「elapsed」·「waiting」 로 갈라 표기 |
| 5 | Sidekiq Latency, Buildkite 「Current Wait」 | **5** | 큐 헤더에 `oldest waiting 1:35 · lanes 2/2 busy`. 상태 분포보다 「얼마나 밀렸나」 한 숫자가 먼저 |
| 6 | GitHub 실패 스텝 자동 펼침, Blue Ocean 실패 콘솔 기본 | **14 · 13** | 실패·타임아웃 행은 **클릭 한 번**에 행 아래로 펼쳐 실패 스텝 타임라인 + tail 5줄(토큰 시) 표시. 「Log」 서랍은 **실패 스텝 마커 위치로 스크롤해 연다**. 토큰 없으면 「summary · failed step 만 보임 · add token for log」 |
| 7 | Sidekiq 「Live Poll / Stop Polling」, Oban Web blur 일시정지 | **2** | 갱신 표시를 버튼으로: `live · 4s ago` 클릭 → `paused · resume`. 탭이 숨겨지면 자동 정지하고 돌아오면 `/api/status` 재수신(18 의 복귀 규칙 재사용). 로그 읽는 중 행이 튀는 문제 해결 |
| 8 | Vercel `errorMessage`, Netlify 빈 큐 안내와 동일 발상 | **14 · 17** | 최근 실패·lost·timed out 행에 **복사 가능한 재실행 명령** `rcm run gate -f scope=full`(같은 트리 재전송은 세션의 결정이라는 PLAN 규칙과 맞음). lost 문구는 `job lost · server restarted 09:02 · workspace kept 30d` 처럼 무엇이 남았는지까지 |
| 9 | htop 구간 막대, btop/Grafana sparkline | **16 · 25** | CPU 막대를 `user | sys` 두 구간으로, 메모리는 `used | compressed`(macOS) 구간. 각 미터 옆 **최근 60표본(5분) sparkline** — 「지금 바쁜가」와 「10분째 바쁜가」를 가른다. 표본이 없는 구간은 Grafana 처럼 끊어 그린다 |
| 10 | Buildkite `--` 자리표시자, GitHub `relative-time` 임계값 | **12 · 10 · 14** | 미시작 스텝 소요를 빈칸 대신 `—`; 상대 시각은 30일 넘으면 절대 날짜(최근 목록 8건이 오래됐을 때) |

### 2-2. 우리가 더 잘하는 것 — 유지할 이유

- **Reason 열(11)과 요약 줄(23·24·25)**: 조사한 어떤 제품도 「N번째·앞에 M개·왜」를 한 칸에 두지 않는다. GitHub 은 한 줄 문구, Buildkite 는 배지(사용자가 #477 로 요구), Jenkins 는 툴팁, GitLab 만 resource_group 에 한해 링크. 우리 결정이 앞서 있다.
- **ETA 신뢰도 배지(10·15·26)**: Jenkins 는 평균 3건을 숨기고 「N/A」로 도망가고, GitLab 은 ETA 자체가 열린 이슈다. `high · measured n=7` 은 유일하다. 근거를 접힘(15)에 두는 배치도 맞다.
- **실패와 빈 값의 분리(17·19·22·26)**: Grafana 의 「No value = -」·null 간격만이 같은 원칙을 지킨다. 대부분의 잡 대시보드는 0 건으로 그린다.
- **`lost` 를 자동 복구하지 않음**: Oban Lifeline 문서의 중복 실행 경고가 정확한 반례다. Prefect Crashed·Dagster STARTING 박힘도 「인프라 실패는 코드 실패와 다른 상태」를 지지한다.
- **색 + 글리프 + 테두리 세 채널**: Airflow 는 색만, GitLab/GitHub 은 아이콘+색. 우리 빗금(낡음)·점선(미확정)·왼쪽 바(초과)는 흑백 인쇄에서도 산다. htop `--no-color` 처럼 「단색 모드에서 읽히는지」를 수용 기준으로 적어 두자.
- **진행형 상태(30 cancelling · 31 upload stalled)와 카운트다운**: GitLab `canceling`·Buildkite `timing_out`·Prefect `Cancelling` 이 같은 계열이고, `kill in 8s`·`removed in 3m` 은 더 나아갔다.

### 2-3. 상태 부호화 비교표

| 우리(필 · 색) | GitHub Actions | GitLab (Pajamas 색) | Buildkite 잡 | Prefect / Temporal | 지적 |
|---|---|---|---|---|---|
| `▶ running` 파랑 | `in_progress` 노란 원(회전) | `running` info-blue | `running` | Running | GitLab·Jenkins Blue Ocean 과 같이 파랑. GitHub 만 노랑. 유지 |
| `· queued` 회색 | `queued` 노란 점(추정) | `pending` warning-orange | `scheduled`(에이전트 대기) · `limited`(그룹 대기) 노란 배지 | Scheduled / AwaitingConcurrencySlot | CI 관례는 **대기=노랑**. 우리는 「황토=행동 가능」에 배정해 회색. 그룹 대기는 상태를 바꾸지 않고 Reason 에 두는 우리 방식이 Buildkite `limited` 보다 단순. 유지하되 24 가 없는 모바일(21)에선 회색 대기가 「멈춤」과 구분되는지 확인 |
| `↑ uploading` 점선 | — (`pending`/`requested`) | `created`/`preparing` | `pending`·`accepted` | Pending / Submitting | 대응 없음. Netlify 「Uploading」은 반대 방향. 유지 |
| `■ cancelling…` 황토 점선 | — | `canceling`(after_script 중) | `canceling`·`timing_out` | Cancelling | 선두 제품과 일치 |
| `✓ succeeded` 초록 | `success` 초록 체크 | `success` green | `passed` | Completed | Sidekiq/Hangfire 「Succeeded」와 같은 어휘. 유지 |
| `✗ failed · exit 1` 빨강 | `failure` 빨간 X | `failed` red | `failed` | Failed | 일치 |
| `■ cancelled` **황토** | `cancelled` 회색 정지 아이콘(추정) | `canceled` **red** | `canceled` | Cancelled | **어긋남**. GitHub·Jenkins(aborted gray)는 회색, GitLab 은 빨강. 황토는 우리 화면에서 blocked·overdue·stale 의 「봐야 할 것」색이라 「누가 일부러 끊음」에 쓰면 24 의 경고와 섞인다. **회색 채움 + ■ 글리프** 권장. `rcm wait` 코드 2 는 timed out 과 같으니 두 필 모두 `exit 2` 를 문구에 |
| `⏱ timed out` 황토 | `timed_out` conclusion → 실패 취급(빨강, 추정) | `failed` + failure_reason | `timed_out`(별도) | TimedOut = FAILED 유형 / TimedOut 별도 | 업계는 **실패의 하위**로 빨강 계열. 우리는 CLI 코드 2(cancelled 와 묶음)라 황토가 자체 일관성은 있다. 절충: 빨간 글자 + 점선 테두리 + `⏱ timed out · exit 2`. 최소한 `limit 20m · step test` 문구는 유지 |
| `? lost` 보라 | `stale`(GitHub 만 설정) | `runner_system_failure`·`no_updates_running` 사유 코드 | `expired`(시작 전 만료) | Crashed / Terminated | 「lost」어휘는 어디에도 없지만 Oban 「orphaned」·Prefect 「Crashed」가 같은 뜻. 보라는 Airflow 에서 `deferred`(비종료) 색이라 오해 소지가 약간 있으나 우리 화면 안에선 유일해 유지. 문구 `job lost after server restart` 가 뜻을 잡아 준다 |

### 2-4. ETA·소요 표기 관례

- **경과·소요**: 단위 접미사가 관례다 — Jenkins 「5 min 10 sec」, Netlify 「Deployed in 1m 25s」, GitHub `relative-time` micro 「21m·37s」, GitLab 은 라벨을 「Elapsed time」→「Duration」으로 바꾼다. Buildkite 는 미시작에 `--`.
- **우리 문제**: 큐 표(10)는 `0:59`·`waiting 1:35`·`in 5:10`, 최근(14)은 `1m 02s`·`5m 50s`, Estimates(15)는 `6m 9s` — **한 화면에 두 표기**. 게다가 `in 5:10` 이 시계 `09:57` 옆에 있어 「5시 10분」로 읽힐 수 있다. 권장: 소요·잔여는 전부 `59s`·`5m 10s`·`in 5m 10s`, 시계만 `09:57`. 터미널 `rcm top` 의 `remaining 5m 10s` 와도 맞는다.
- **잔여·ETA**: 제품 대부분이 **잔여를 아예 안 보인다**(GitHub·GitLab·Vercel·Netlify). Jenkins 만 진행 막대 + 「Estimated Remaining Time」 툴팁이고, 99% 캡·초과 시 「N/A」. Prefect 는 예측 대신 「Late」(15초) 판정. 우리 `09:57 · in 5:10` + `now + 0:30 / expected 6:09`(20) 조합은 이 중 가장 정직하다.
- **신뢰도**: 표시하는 제품이 없다. Jenkins 의 「최근 성공 3건 평균, 없으면 실패 3건」이 숨은 신뢰도의 전부. 우리 `high/med/low · n` 은 유지하고, 26(표본 없음)의 설명문을 그대로 두자.
- **진행 막대**: Jenkins 전체 막대는 초과 시 99% 에서 멈춰 오해를 낳는다. 12 의 「세로 목록이 기본, 막대는 요약」 결정과 부합한다.

### 2-5. 큐 위치·대기 이유 표현 관례

- **순번·앞 개수**: 어떤 제품도 「N번째·앞에 M개」를 행에 쓰지 않는다. Vercel 은 FIFO 라고 문서에만 적고 「Start Building Now」로 건너뛰게 한다. Buildkite 큐 페이지가 「Jobs Waiting N · Current Wait p50/p95」로 집계만 보인다. 우리 `#`+`behind #412` 는 앞서 있고, 1번 제안(레인 수·앞 개수·잔여 합)으로 완성된다.
- **대기 사유 문구**: Jenkins 가 가장 풍부하다(「Waiting for next available executor on 'X'」「Blocked by 'Y'」「'X' is offline」「In the quiet period. Expires in N」). GitLab 은 「This job is waiting for resource: X」+ 보유 잡 링크. Airflow 는 Dependency/Reason 표. GitHub 은 「Waiting for a runner to pick up this job...」 한 줄. Netlify 는 「Enqueued: Awaiting Capacity」. CircleCI 는 「Queued = serial group」.
- **우리에게 없는 두 사유**: (a) **레인이 실제로 몇 개 살아 있는지**(Jenkins 「'X' is offline」·Buildkite 「Agents Connected」·Temporal 「no Workers polling」) — 1 번 워커 필과 11 을 잇는 `waiting for lane · 1/2 lanes up` 문구, (b) **정지 만료 시각**(Jenkins 「Expires in N」) — 28 `paused` 에 「since 09:50」은 있으니 `rcm resume` 안내만 더한다.
- **대기 만료**: Buildkite 30일 만료·GitLab 1시간/24시간 stuck 낙인은 우리 `upload_abandon_seconds`(31)와 같은 계열. `queued` 에도 「N시간 넘게 대기 = 경고」 임계값(설정)을 두면 24 에 오를 수 있다.

### 2-6. 실패를 알리는 방식 — 로그까지 몇 클릭

| 제품 | 실패 요약 | 실패 스텝 | 로그 | 비고 |
|---|---|---|---|---|
| GitHub Actions | run 요약의 annotations(check-run `output.title/summary`) — 1클릭 | 잡 클릭 → **실패 스텝 자동 펼침** — 2클릭 | 같은 화면 | 검색은 펼친 스텝만 |
| Jenkins Blue Ocean | run 헤더 상태 — 1클릭 | **실패 스텝 콘솔이 기본** — 1클릭 | 같은 화면 | |
| GitLab | 그래프 hover 로 failure_reason — 0~1 | 잡 페이지 — 1클릭 | 같은 화면 | |
| Buildkite | 사이드바 「state 로 묶기」 — 0~1 | 스텝 패널 Logs 탭 — 1~2클릭 | 패널 | |
| Netlify / Vercel | 목록 카드에 `errorMessage`/summary — 0클릭 | 로그 상단 | 1클릭 | 배포는 스텝 개념이 얇다 |
| Hangfire / Sidekiq | Failed·Retries 목록에 예외 메시지 inline — 0클릭 | 상세 카드 Reason — 1클릭 | 없음 | |
| **우리(14·13)** | 행에 `2 tests failed · step test` — **0클릭** | 행 클릭 → 서랍(토큰 필요) — 1클릭+토큰 | 서랍 | 토큰 없으면 스텝 타임라인도 못 봄 |

결론: 요약·실패 스텝을 0클릭에 두는 점은 최상위다. 부족한 건 **토큰 없이 볼 수 있는 스텝 타임라인**(3·6 번 제안)과 **로그를 실패 스텝 위치로 여는 것**(GitHub·Blue Ocean 관례).

### 2-7. 스키마·PLAN 에 반영할 것 (요약)

`queue[].reason` 에 `lanes_up/lanes_total`·`ahead_count`·`blocked_by_remaining_seconds` 를 더하고, `estimate` 에 `waited_seconds`·`stuck: bool`(설정 `stuck_multiplier`), `recent[]` 에 `waited_seconds`·`transitions[]`(상태·시각·reason), `hosts[]` 에 `cpu.user/sys` 분리(이미 있음)와 최근 표본 배열(sparkline 용). UI 문자열 규칙에 「소요는 `5m 10s`, 시계는 `09:57`」을 명문화한다.
