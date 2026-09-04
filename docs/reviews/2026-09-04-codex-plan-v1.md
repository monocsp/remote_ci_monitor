# Codex 크로스리뷰 — PLAN.md v1 초안 (2026-09-04)

- 리뷰어: Codex CLI 0.153.0 · gpt-5.5 · `codex exec --sandbox read-only`
- 대상: PLAN.md v1 초안(441줄) + 참고 구현 `ci_queue.py` · `ci_top.py` · `30-remote-dispatch.md`
- 질문 목록: 아래 「프롬프트」. 답변 원문은 「리뷰 원문」. 무엇을 어떻게 반영했는지는 「반영」.

## 반영

| 항목 | 판정 | PLAN 에 한 일 |
|---|---|---|
| A 의존성 0 서버 | 조건부 | 「서버」절에 hardening 목록 10개 추가(타임아웃 · HTTP/1.0 · 본문 상한 · 동시 요청 상한 · 경로 정규화 · 오류 응답). 전제(LAN/Tailscale 내부 도구) 명시 |
| B REST 직접 | 동의 | 유지 |
| C 큐 판정 | 조건부 | 잡 단위 매칭 + 매칭 잡만으로 계산 · jobs 페이지네이션 · `run_attempt` 캐시 키 · `membership: unknown` 상태 · `names` 는 배정된 잡에만 · 러너 그룹은 싣기만 · concurrency 그룹 경고 |
| D 성공 run 만 표본 | 조건부 | `sample_policy = "success" \| "completed"` 로 열고, 「최근 완료」엔 실패·타임아웃도 보이게. `sample_count` 추가 |
| E rate limit 예산 | 조건부 | 백필 시간당 상한 `backfill_per_hour` · Actions `GITHUB_TOKEN` 1,000/시 경고 · secondary limit(직렬 호출 · Retry-After 없으면 60초부터 배가) · 워크플로별 표본 보강 · 예산표 재계산(N=3 → 약 1,920/시) |
| F 전송 대안 | 동의 | `--host-local` 유지. 호스트 샘플을 호스트명별 `hosts[]` 로 |
| G 읽기 인증 | 조건부 | Basic 은 TLS 프록시 뒤에서만이라고 명시. 비-루프백 바인드 + none 이면 시작 경고 |
| H 스키마 | 조건부 | `pools[]` 축 도입(M3 까지 한 개). `repos[].error` · `head_sha` · `run_number` · `workflow_id` · `runner_group_name` · `position` · `wait_seconds` · `sample_count` · `matched_jobs` 추가 |
| I CI 집계 잡 | 동의 | `if: always()` + 두 `needs.*.result` 가 모두 success 아니면 exit 1 명시 |
| J 뮤테이션 확인 | 조건부 | tmpdir 복사본에 변이 → 원본 무손상. 패턴 미발견은 실패. `mutcheck.sh` → `mutcheck.py` |
| K 시간대 | 동의 | 시작 시 zoneinfo 검증 · JSON 에 적용 tz 또는 null · Docker 는 UTC 라고 명시 |
| L 참고 구현 | 조건부 | 「반드시 지키는 것」 목록 추가(job_started_at 우선 · 실패/빈 큐 분리 · number 미사용 · top 두 번째 표본 · 계산 공용 함수 · 워크플로별 이력 보강) |
| M 자기위반 | 조건부 | 러너 조회 실패 시 `lanes_source: "assumed"` 배지 · 소속 미상은 빈 큐로 접지 않음 |

Codex 가 「사용자에게 물어야 한다」고 한 5개 중 3개(큐 판정 기본값 · 표본 정책 · 풀 스키마)는 Codex 추천이 초안과 같아 **확정**으로 옮겼다. 남은 셋은 같은 날 오너가 확정했다: 서버는 Tailscale/LAN 안에서만(읽기 인증 기본 none) · 호스트 자원 2차 경로는 gist 대신 `--host-local` · 주석·docstring 은 한국어, 식별자·README·UI 는 영어.

## 프롬프트

너는 설계 크로스리뷰어다. 파일을 수정하지 말고 읽기만 해라. 답은 한국어로, 결론 먼저, 근거는 짧게.

## 대상
- 이 레포(작업 디렉터리)의 `PLAN.md` — remote_ci_monitor 계획서 v1 초안. 끝까지 읽어라.
- 참고 구현(private 레포의 워크트리, 읽기 전용): 
  - /Users/fmmentalcare/Documents/GitHub/dolomood-ci-monitor/scripts/ci_queue.py
  - /Users/fmmentalcare/Documents/GitHub/dolomood-ci-monitor/scripts/ci_top.py
  - /Users/fmmentalcare/Documents/GitHub/dolomood-ci-monitor/docs/renew-guide/ci-cd/30-remote-dispatch.md
  이 셋은 한 팀·한 머신에 묶인 기존 스크립트이고, PLAN.md 는 그걸 누구나 쓸 수 있는 독립 도구(Python 패키지 + 서버 + 웹 UI + 수집기)로 다시 만드는 계획이다.

## 프로젝트 요약
self-hosted GitHub Actions 러너에서 지금 뭐가 돌고, 누가 시켰고, 어느 스텝을 몇 분째 도는지, 그 머신의 CPU·메모리는 어떤지를 어느 컴퓨터에서든 웹 화면으로 본다. public 레포, 이식성(특정 머신·계정·규약 금지)과 fail-open 금지(조회 실패를 0건/0초로 그리지 않기)가 핵심 규칙이다.

## 검토해 달라는 것 — 각 항목에 [동의 / 반대 / 조건부] 와 근거 1~3문장

A. 런타임 의존성 0 (urllib + http.server ThreadingHTTPServer + tomllib) 으로 장시간 도는 서버를 만드는 것. LAN/Tailscale 용 내부 도구라는 전제에서 위험(keep-alive, slowloris, 스레드 폭주, 에러 처리)이 감당 가능한가? FastAPI/httpx 를 넣는 게 낫다고 보면 왜인지.
B. GitHub 를 `gh` CLI 대신 REST 직접 호출로 바꾸고 `gh auth token` 은 폴백으로만 쓰는 것.
C. 「큐 판정」 규칙: 활성 run 의 jobs 를 받아 잡 labels ⊇ 설정(기본 {"self-hosted"}) 이면 내 러너 큐로 본다 + 워크플로 allowlist 옵션 + 완료 run 표본도 같은 규칙으로 거른다. 빠진 케이스(matrix 잡, 재실행 run_attempt, waiting/pending/requested 상태, 러너 그룹, 라벨이 다른 여러 풀)를 지적해 달라.
D. 소요시간 표본을 conclusion == success 만으로 제한하는 것(`successful_only = true` 기본). 참고 구현은 실패 run 도 넣었다.
E. rate limit 예산 계산(PLAN 「호출 예산」 표)과 대응(ETag 304 는 primary limit 에 안 셈 · 잔량 500 미만이면 60초로 늘림 · 적응 주기 10s/30s). 계산이 틀렸거나 빠진 호출이 있으면 지적.
F. 호스트 자원 전송: 기본 「수집기 → 서버 HTTP push (Bearer 토큰)」 + 두 번째 경로로 v0 의 「gist 주기 갱신」 대신 「서버가 러너 머신에서 직접 수집(--host-local)」 로 바꾸는 제안. 
G. 읽기 측 인증: 기본 none, 옵션 HTTP Basic. 토큰-in-URL 은 배제. 다른 방식이 더 맞는가?
H. `/api/status` 스키마 v1 — 빠진 필드, 잘못된 구조, M4(레포 여러 개·러너 풀 여러 개) 로 갈 때 깨질 부분.
I. CI: matrix 잡 `unit` + 집계 잡 `test`(룰셋 필수 체크 이름) 구성과, `if: always()` 집계 잡에서 실패를 제대로 전파하는 방법.
J. 뮤테이션 확인 `scripts/mutcheck.sh` 3종(sed 로 변이 → pytest 빨개짐 확인 → 되돌림)을 CI 에서 돌리는 것. 더 나은 방법이 있으면.
K. 시간대: 내부 UTC, rcm top 은 설정 tz 또는 서버 로컬, 웹 UI 는 설정 tz 또는 브라우저 로컬.
L. 참고 구현에서 PLAN 이 버리기로 한 것들 중 버리면 안 되는 것이 있는가. 반대로 참고 구현의 함정 중 PLAN 이 빠뜨린 것이 있는가.
M. 이식성 규칙·fail-open 금지 규칙을 PLAN 자체가 위반하는 곳이 있는가.

## 마지막에 따로 정리해 달라
1. 「사용자(프로젝트 오너)에게 물어야 할 결정」 — 답에 따라 만들 물건이 실제로 달라지는 것만. 각각 네가 추천하는 답과 이유.
2. 「그냥 정해도 되는 것」 — PLAN 의 기본값을 그대로 가거나 네가 제안하는 값으로 바꾸면 되는 것.
3. PLAN 에 추가해야 할 함정·요구사항 — 최대 10개, 중요한 순.

## 리뷰 원문

결론: **조건부 승인**입니다. M0는 진행해도 되지만, PLAN에 **API 예산 보정, run_attempt/jobs pagination, 큐 판정 불확실 상태, M4용 pool/repo 스키마 축**은 지금 넣어야 합니다. 파일은 읽기만 했고 수정하지 않았습니다.

**A. [조건부]** 런타임 의존성 0은 LAN/Tailscale 내부 도구면 감당 가능합니다. 단, `ThreadingHTTPServer`에는 소켓 timeout, 요청 본문 상한, HTTP/1.0 close 또는 keep-alive 제한, 에러 응답 정리, thread 폭주 완화가 PLAN에 명시돼야 합니다. 인터넷 공개나 다중 사용자 운영이면 FastAPI/httpx/uvicorn 쪽이 낫습니다.

**B. [동의]** REST 직접 호출 전환이 맞습니다. Docker·pipx·public 도구 이식성에서 `gh` CLI 의존은 약하고, 참고 구현도 `gh auth switch` 같은 팀/셸 상태 함정을 갖고 있습니다. `gh auth token`은 시작 시 토큰 폴백으로만 쓰면 충분합니다.

**C. [조건부]** 라벨 기반 큐 판정은 현실적인 기본값입니다. 빠진 케이스는 matrix 100개 초과 jobs pagination, run 재실행의 `run_attempt`별 jobs/캐시 키, `pending/requested`에서 jobs가 아직 없을 때의 “판정 불가”, runner group, queued 상태에서 `runner.names` 필터가 불가능한 점, 여러 라벨 풀 혼재입니다. 혼합 run은 “run 전체”가 아니라 **매칭 job 기준**으로 소요·진행·큐 포함을 계산해야 합니다.

**D. [조건부]** 기본 `successful_only = true`는 ETA 품질에는 맞습니다. 실패 run은 짧게 죽는 경우가 많아 중앙값을 망치지만, timeout·hang 실패는 오히려 중요한 신호라 recent에는 남기고 표본 설정은 옵션으로 열어야 합니다. 출력에 `sample_count`가 필요합니다.

**E. [조건부]** 예산 계산은 방향은 맞지만 과소계산입니다. 활성 N=3이면 10초마다 runs+runners+jobs = 시간당 1,800이고, cold cache 백필 5/주기를 그대로 돌리면 추가 1,800이라 3,600까지 갑니다. `GITHUB_TOKEN`은 레포당 1,000/h인 경우가 있어 PAT 5,000/h 전제와 분리해야 하고, jobs pagination·per-workflow history·secondary limit 대응도 넣어야 합니다.

**F. [동의]** 기본 push + `--host-local`은 gist보다 낫습니다. gist는 지연·토큰 권한·외부 상태가 늘어 내부 모니터에는 부적합합니다. 다만 여러 러너가 생기면 host 샘플은 단일 객체가 아니라 runner/pool별로 저장해야 합니다.

**G. [조건부]** 기본 none은 `127.0.0.1` 또는 Tailscale 내부면 괜찮습니다. HTTP Basic은 TLS 없는 LAN HTTP에서는 비밀번호가 평문이라 “인터넷 공개용 인증”으로 쓰면 안 됩니다. 인터넷 공개는 Caddy/Tailscale/OAuth proxy 뒤에 두는 방식을 README 기본 안내로 두는 게 맞습니다.

**H. [조건부]** v1 스키마는 단일 레포·단일 풀에는 충분합니다. M4로 가면 top-level `lanes`, `medians`, `host`가 깨지므로 `pools[]` 또는 `targets[]` 축이 필요합니다. 추가 필드는 `head_sha`, `run_number`, `workflow_id/path`, `runner_group_id/name`, `estimate.sample_count`, `wait_seconds`, `position`, per-repo/per-pool error입니다.

**I. [동의]** matrix `unit` + 집계 `test`는 룰셋 필수 체크 이름 문제를 잘 피합니다. `test`는 `if: always()`로 항상 돌리고, `needs.unit.result`와 `needs.secrets.result`가 모두 `success`가 아니면 `exit 1` 해야 합니다. `cancelled`·`skipped`도 실패로 전파해야 합니다.

**J. [조건부]** `mutcheck.sh`를 CI에서 돌리는 건 이 프로젝트 성격상 좋습니다. 다만 sed 패턴이 안 맞으면 반드시 실패해야 하고, 중간 실패·Ctrl-C에도 원복되는 `trap`이 필요합니다. 더 나은 방식은 소스 복사본을 tmpdir에 만들어 변이하고 그 복사본에 대해 pytest를 돌리는 것입니다.

**K. [동의]** 내부 UTC, 표시 시점 변환이 맞습니다. `display.timezone`은 `zoneinfo`로 시작 시 검증하고, 서버 JSON에는 실제 적용 timezone 또는 `null`을 명확히 실어야 합니다. Docker 서버 로컬 timezone은 UTC일 수 있으니 문서에 적어야 합니다.

**L. [조건부]** 버리기로 한 `gh`, KST, 팀 워크플로, `~/actions-runner` 판별은 버려야 합니다. 버리면 안 되는 것은 참고 구현의 “job_started_at 우선”, “실패와 빈 큐 분리”, “스텝 number 미사용”, “top 두 번째 표본”, “큐 계산 공용 함수”입니다. PLAN이 추가로 품어야 할 참고 함정은 GitHub concurrency가 진짜 FIFO가 아니라는 점과 workflow별 history 보강입니다.

**M. [조건부]** PLAN 자체의 큰 방향은 이식성·fail-open을 지킵니다. 다만 `labels=["self-hosted"]` 기본은 여러 풀 환경에서 과포함될 수 있고, runner 조회 실패 시 lanes 자동값을 조용히 1로 두면 fail-open에 가깝습니다. “jobs를 못 받아 큐 소속을 모름”은 빈 큐가 아니라 `queue_error` 또는 `membership_unknown`으로 보여야 합니다.

**사용자에게 물어야 할 결정**
1. 서버 노출 범위: 추천은 **Tailscale/LAN + read_auth none**. 인터넷 공개 계획이 있으면 Basic이 아니라 TLS 프록시 전제를 제품 요구사항으로 바꿔야 합니다.
2. M0에서 다중 runner pool을 바로 스키마에 넣을지: 추천은 **스키마만 pool-ready로 잡고 UI/계산은 단일 pool 구현**. 나중에 깨지는 변경을 줄입니다.
3. 큐 판정 기본값: 추천은 **labels 기본 + workflow allowlist 선택**. allowlist 필수는 설치 첫 경험을 무겁게 만듭니다.
4. 표본 정책: 추천은 **successful_only=true 기본, 옵션으로 all 허용**. ETA 기본 품질과 예외 환경을 둘 다 잡습니다.
5. 호스트 자원 2차 경로: 추천은 **gist 폐기, `--host-local` 채택**. 지연·권한·외부 의존이 줄어듭니다.

**그냥 정해도 되는 것**
REST 직접 호출, `gh`는 토큰 폴백만.  
URL 토큰 배제.  
내부 UTC.  
정적 HTML+JS 폴링 UI.  
코드·README 영어.  
런타임 의존성 0은 서버 hardening 요구사항을 PLAN에 추가한 뒤 유지.

**PLAN에 추가해야 할 함정·요구사항**
1. jobs API pagination: matrix/job 수 100 초과 대응.
2. `run_attempt`별 jobs endpoint와 timing cache key `repo/run_id/attempt`.
3. cold cache 백필 시간당 상한.
4. `GITHUB_TOKEN` 1,000/h와 PAT 5,000/h 예산 분리.
5. secondary rate limit: 직렬 호출, `Retry-After` 없으면 최소 60초 백오프.
6. membership 판정 불가 상태를 빈 큐로 접지 않기.
7. multi-pool schema 축: `pools[]`, pool별 lanes/runners/queue/medians/host.
8. runner group 필드와 제한의 한계 명시.
9. workflow concurrency 사용 시 ETA/FIFO가 틀릴 수 있음.
10. 서버 hardening: timeout, body cap, path normalization, thread/request 제한.

외부 확인은 GitHub 공식 문서만 봤습니다: [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), [REST best practices conditional requests](https://docs.github.com/en/enterprise-cloud%40latest/rest/using-the-rest-api/best-practices-for-using-the-rest-api), [workflow jobs API](https://docs.github.com/en/rest/actions/workflow-jobs), [workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs).