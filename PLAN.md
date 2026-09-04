# remote_ci_monitor — 계획서 (v1.1, 2026-09-04)

> 정본이다. 세션을 시작하면 끝까지 읽는다.
> v0(2026-09-04 오전)의 원칙·함정·마일스톤을 그대로 품고, **어떻게 만들지**(모듈·설정·스키마·API 예산·수집기 프로토콜·테스트·CI)를 결정 수준까지 내렸다.
> 2026-09-04 Codex(gpt-5.5) 크로스리뷰를 반영했고(`docs/reviews/2026-09-04-codex-plan-v1.md`), 남아 있던 결정 항목은 같은 날 오너가 확정했다(「결정 항목」). 같은 날 오후 오너 검토(연결·실시간 자원·큐·전달 여섯 질문)로 **디스패치 `rcm run` · 세션 명령 `rcm eta`/`rcm wait` · GPU** 를 추가했다(v1.1). 열린 ⛔ 는 없다.

## 한 줄

**self-hosted GitHub Actions 러너에서 지금 뭐가 돌고, 누가 시켰고, 어느 스텝을 몇 분째 도는지, 그 머신의 CPU·메모리·GPU 는 어떤지** 를 어느 컴퓨터에서든 웹 화면 하나로 보고, 다른 컴퓨터의 세션에서 **한 줄로 CI 를 넣고(`rcm run`) 결과를 종료 코드로 받는다(`rcm wait`)**.

## 왜 만드나

러너가 한 대뿐인 팀은 CI 요청이 여러 사람·여러 세션에서 겹친다. GitHub Actions 화면은 run 목록만 보여주고 「내 차례가 언제 오는지」「앞 run 이 걸린 건지 느린 건지」「러너 머신이 지금 버거운지」를 안 알려준다.
이 문제를 한 레포 안에서 터미널 스크립트로 한 번 풀어 봤다(private 레포 `fmmc-tech/dolomood-app-renew` 의 `scripts/ci_top.py`·`ci_queue.py`, 2026-09-01~04). 잘 돌지만 그 레포·그 머신·그 팀 규약에 묶여 있다. 이 프로젝트는 그걸 **누구나 자기 러너에 붙일 수 있는 독립 도구**로 다시 만든다.

## 반드시 지킬 것 — 이식성

이 레포는 public 이고 다른 사람이 자기 환경에서 쓴다. 아래는 위반하면 안 된다.

- 특정 머신·계정·팀 규약을 코드에 박지 않는다. 금지 예: 러너 이름 `dolomood-macmini`, gh 계정 `pcs-fmmc`, 시간대 KST 고정, `~/actions-runner` 존재로 「러너 머신인지」판별, run 이름 형식 `ci · <scope> · <branch> · ← <caller>`, 워크플로 이름 집합 `{ci, deploy-dev, …}`, 종류별 기본 소요시간 표.
- 전부 설정으로 받는다(파일 + 환경변수 + CLI 플래그). 위 팀 규약은 `examples/` 의 예시 설정 한 벌로만 남긴다.
- run 이름 규약이 없어도 동작해야 한다. 요청자는 기본적으로 GitHub API 의 `triggering_actor` 로, 범위(scope)는 워크플로 이름으로 잡고, 규약이 있는 팀은 정규식(named group `scope`·`caller`)을 설정으로 더한다.
- macOS 러너만 가정하지 않는다. 호스트 자원 수집기는 macOS(`vm_stat`/`top`/`ps`)와 Linux(`/proc/meminfo`·`/proc/stat`·`/proc/loadavg`)를 둘 다 지원한다. Windows 는 범위 밖으로 명시한다.
- 설치가 한 줄이어야 한다. `pipx install` 또는 `uvx`, 그리고 Docker 이미지. 로컬 클론 없이 돌 수 있어야 한다.
- README 는 영어로 쓴다(사용자층이 한국어 팀 밖으로 열린다). 이 계획서 같은 내부 문서는 한국어여도 된다.
- 코드의 식별자·UI 문자열·README·CLI 도움말은 영어, **주석·docstring 은 한국어**로 쓴다(2026-09-04 오너 결정, 참고 구현과 같은 스타일). 커밋 메시지 본문·리뷰 기록도 한국어.
- 시크릿(GitHub 토큰, 수집기 공유 토큰)은 환경변수나 설정 파일로만 받고 절대 커밋하지 않는다. `.gitignore` 와 gitleaks 검사를 CI 에 건다.
- `gh` CLI 에 의존하지 않는다. 서버는 Docker 안에서도 돌아야 하므로 GitHub 는 REST 를 직접 부른다. `gh` 는 토큰을 못 찾았을 때의 **폴백 토큰 출처**로만 쓴다.

## 브랜치 정책 (2026-09-04 적용 — GitHub 룰셋으로 강제)

브랜치는 `main`(릴리스)과 `dev`(통합) 둘이 상시 존재한다. 규칙은 문서가 아니라 GitHub 룰셋과 워크플로가 막는다. 관리자도 bypass 없다.

| 규칙 | 강제 수단 |
|---|---|
| `main`·`dev` 에 직접 커밋·push 불가. 강제 push·삭제도 불가 | 룰셋 `main — PR only, from dev, checks required` · `dev — PR only` 의 `pull_request` · `non_fast_forward` · `deletion` |
| `main` 은 **이 레포의 `dev`** 에서 보낸 PR 로만 머지 | `.github/workflows/pr-policy.yml` 의 `main-from-dev-only` 잡을 main 룰셋 필수 체크로 지정 |
| `dev` 는 PR 로만 받는다(feature 브랜치 → dev) | dev 룰셋 `pull_request` |
| `main` 으로 올라가려면 CI 가 전부 통과 | `.github/workflows/ci.yml` 의 `test` 잡을 main 룰셋 필수 체크로 지정. M0 에서 pytest · ruff · 시크릿 스캔으로 채운다 |
| `dev` → `main` 은 merge commit 만 허용 | main 룰셋 `allowed_merge_methods: ["merge"]`. squash · rebase 는 main 과 dev 의 히스토리를 갈라놓아 다음 PR 부터 꼬인다 |

- 승인 수는 0 이다(혼자 하는 레포). 리뷰어가 생기면 룰셋에서 올린다.
- 필수 체크는 **잡 이름**으로 잡힌다. `test` · `main-from-dev-only` 를 바꾸면 룰셋도 같이 바꿔야 한다.
- ⚠️ **matrix 함정**: 잡에 `strategy.matrix` 를 걸면 체크 이름이 `test (3.11)` 처럼 바뀌어 룰셋의 `test` 와 안 맞는다. 여러 파이썬 버전을 돌리려면 matrix 잡을 따로 두고(`unit`), `needs: [unit, …]` 인 **집계 잡 `test`** 가 통과/실패를 대표한다(「테스트·품질」 절).
- 필수 체크를 더하거나 dev 에도 걸려면: `gh api repos/{owner}/{repo}/rulesets` 로 룰셋 id 를 찾아 `required_status_checks` 항목을 고친다.
- 일상 흐름: `git switch dev && git pull` → `git switch -c feat/<topic>` → 작업 · 커밋 → `gh pr create --base dev` → 머지. 릴리스는 `gh pr create --base main --head dev`.

## 무엇을 보여주나

| 항목 | 출처 | 비고 |
|---|---|---|
| 러너 상태 (online/offline · busy/idle · 라벨 · 러너 그룹) | `GET /repos/{o}/{r}/actions/runners` | 러너 여러 대 지원. 레인 수 기본값 = 매칭된 online 러너 수 |
| 큐 (FIFO) — 순번 · 실행중/대기 · 키(워크플로[:scope]) · 브랜치 · 요청자 · 이벤트 · 대기 · 잔여 · 예상 완료 · 초과 여부 · 표본 수 | `GET .../actions/runs` (+ jobs) | 예상 완료 = 최근 N 일 **잡** 실행시간 중앙값 − 경과, 앞선 run 잔여 누적 |
| 진행 — 잡 N/M · 스텝 N/M(지금까지 알려진) · 지금 스텝 · 그 스텝 경과 · 잡 전체 경과 · 스텝 타임라인 · 실패 스텝 | `GET .../actions/runs/{id}/jobs` | 「jobs API 실측 함정」 필수 |
| 최근 완료 — 결과(성공·실패·취소·타임아웃 전부) · 실측 소요 · 요청자 | runs + jobs | |
| 호스트 자원 — 러너 머신별 load · CPU % · 메모리 · **GPU 사용률·GPU 메모리** · 상위 프로세스 · **마지막 수신 시각/stale** | 러너 머신의 수집기 | 서버가 러너 머신에서 돌면 로컬 수집, 아니면 push. 폴링이지 스트리밍이 아니다(기본 10초, 하한 2초) |
| 폴링 건강 — 마지막 성공 시각 · 마지막 오류 · rate limit 잔량 · 저하 모드 | 서버 자체 | 「조용히 고장」을 화면에서 잡기 위해 |
| **세션 명령** — 요청 넣기(중복이면 합류) · 내 run 의 위치·대기·예상 완료 · 끝날 때까지 대기 후 성공/실패를 **종료 코드**로 | `rcm run` · `rcm eta` · `rcm wait` (GitHub 직접 또는 서버 경유) | 「세션 명령」절. 순차 처리 자체는 GitHub 큐가 보장한다 |

## 구조

```
[러너 머신]                          [아무 데나]                       [브라우저 / 터미널]
collector ──push(JSON, N초)──▶  server ──GET /api/status──▶  web UI (정적 HTML+JS, 자동 갱신)
(vm_stat/top/ps/ioreg 또는      (GitHub REST 폴링 + 메모리 캐시)        rcm top --server URL
 /proc/nvidia-smi)                   ▲                                 rcm wait --server URL
                                     │                                        │
[GitHub Actions]  ◀── 폴링 ──────────┘                                        │
  큐(FIFO) · runs · jobs · runners  ◀── POST dispatches ── [다른 컴퓨터의 세션] rcm run ──┘
                                                          (서버가 없으면 GitHub 를 직접 폴링)
```

실행 형태는 셋이고 전부 같은 코드(같은 `StatusModel` → 같은 JSON 스키마)다.

| 형태 | 명령 | 언제 |
|---|---|---|
| **A. 원격 서버 + 수집기 push** (기본) | 아무 데나 `rcm serve` · 러너 머신에 `rcm collect --server URL` | 서버를 VPS·노트북에 두고 러너 머신엔 인바운드 포트를 안 연다 |
| **B. 서버가 러너 머신에서 직접** | 러너 머신에 `rcm serve --host-local` | 러너 머신에 Tailscale·LAN 으로 닿을 수 있으면 수집기·토큰이 필요 없다 |
| **C. 터미널 단독** | 어디서든 `rcm top` (GitHub 직접 조회) · `rcm top --server URL` (서버 JSON 렌더) | 서버 없이 한 컷. 호스트 자원 칸은 서버가 있을 때만 |
| **D. 세션에서 넣고 기다리기** | 어디서든 `rcm run <workflow> -f k=v` → (자동으로) `rcm wait` | 요청은 GitHub 에 직접 넣는다(도구는 저장하지 않는다). 상태 조회는 서버가 있으면 서버, 없으면 GitHub |

- `server` 하나가 GitHub API 를 폴링해 JSON 을 만들고 정적 UI 를 서빙한다. 상태는 메모리에만 두고(DB 없음), 완료 run 의 잡 소요시간만 파일 캐시에 남긴다(재시작해도 표본이 유지되게).
- 순수 계산(큐·잔여·진행률·파서·렌더)과 I/O(GitHub API·프로세스 실행·HTTP)를 패키지로 가른다. 순수 부분은 픽스처로 테스트한다.
- JSON 스키마(`schema_version: 1`)를 먼저 고정하고 UI·터미널은 그걸 소비한다. 스키마는 처음부터 **풀(pool) 축**을 갖는다 — M0~M3 는 풀 하나(`default`)만 만들지만, M4 에서 레포 여러 개·러너 풀 여러 개로 갈 때 최상위 키가 안 깨지게.

## 패키지·모듈 구조

이름: PyPI `remote-ci-monitor`(비어 있음, 2026-09-04 확인) · import `remote_ci_monitor` · 명령 `rcm`(PyPI 의 `rcm` 은 다른 패키지라 패키지명으로는 못 쓰지만 콘솔 스크립트 이름으로는 문제 없다) · 보조 명령 `remote-ci-monitor`(같은 진입점 — `uvx remote-ci-monitor serve` 가 되게).

```
pyproject.toml                 # hatchling · requires-python >=3.11 · 런타임 의존성 0
src/remote_ci_monitor/
  __init__.py                  # __version__
  cli.py                       # argparse: rcm top | run | eta | wait | serve | collect | check | version
  config.py                    # Config dataclass · 파일(TOML)+env+플래그 로딩 · 우선순위 · 검증 · 오류 메시지
  core/                        # ── 순수: I/O 도 시계도 안 본다. now 는 인자로 받는다 ──
    model.py                   # Run · Job · Step · Runner · HostSample · Pool · StatusModel (dataclass)
    naming.py                  # key_of(run, cfg) · requester_of(run, cfg) — 규약 없음/있음 둘 다
    membership.py              # 이 run/잡이 「이 풀」인가 (라벨 ⊇ 설정 · 워크플로 allowlist · 미상 상태)
    queue.py                   # fifo 정렬 · expected · remaining(하한) · wait(레인) · finish_at · medians
    progress.py                # jobs payload → Progress (스텝 N/M · 현재 · 경과 · 실패 스텝 · 대기시간)
    dispatchmatch.py           # 합류 판정(같은 워크플로·ref·sha[·scope]) · dispatch 뒤 내 run 고르기 · wait 종료 코드 매핑
    hostparse.py               # macOS: vm_stat/top/ps/ioreg(GPU) · Linux: /proc/meminfo, /proc/stat, /proc/loadavg, ps, nvidia-smi(GPU)
    status.py                  # 조각들 → StatusModel → to_json() (스키마 v1) · 실패는 null + *_error
    render_text.py             # StatusModel → 터미널 문자열 (rcm top)
  github/                      # ── I/O ──
    client.py                  # urllib 기반 REST: get(path, etag) → Response(status, json, etag, ratelimit) · 페이지네이션 · 토큰 결정 · ApiError
    fetch.py                   # fetch_runs(필터: event·branch·actor·created) · fetch_run · fetch_jobs · fetch_runners · whoami — 실패는 예외
    dispatch.py                # POST /actions/workflows/{id}/dispatches (204, run id 없음) · 쓰기 토큰
  timing_cache.py              # 완료 run 의 잡 소요·라벨·러너명 파일 캐시 (JSON, XDG cache, 키 repo/run_id/attempt)
  poller.py                    # 백그라운드 스레드 1개: 직렬 호출 · ETag · 적응 주기 · rate limit 가드 · 모델 조립
  hostsample.py                # 수집기의 I/O: 명령 실행/파일 읽기 → hostparse 로 넘김 (macOS/Linux 분기)
  collector.py                 # rcm collect: 주기 샘플 → POST /api/host, 백오프, 절대 죽지 않음
  server.py                    # ThreadingHTTPServer: GET / · /static/* · /api/status · /api/health · POST /api/host · 인증 · hardening
  web/                         # 정적 UI: index.html · app.js · style.css (빌드 없음, 패키지 데이터로 포함)
tests/
  fixtures/                    # 실측 캡처를 익명화해 줄인 JSON/텍스트 (아래 「테스트」)
  test_*.py
examples/
  rcm.toml                     # 일반 팀(규약 없음)
  rcm.run-name-convention.toml # run 이름 규약이 있는 팀(참고 구현 팀의 규약을 예시 값으로)
  launchd/ · systemd/          # 수집기·서버 서비스 파일
  session/ci-gate.sh           # 세션(Claude Code 스킬 등)에서 rcm run 한 줄로 게이트 돌리고 종료 코드로 분기하는 예시
scripts/
  mutcheck.py                  # 테스트가 실제로 빨개지는지 확인하는 뮤테이션 3종 (아래)
docs/reviews/                  # 크로스리뷰 기록
```

**의존성 원칙**: 런타임 의존성 0(`urllib.request` · `http.server` · `tomllib` · `zoneinfo` · `hmac` · `threading`). 이유 — pipx/uvx 설치가 수 초, 러너 머신에 올리는 수집기가 가볍고, public 도구의 공급망 면적이 최소가 된다. 개발 의존성은 `pytest` · `ruff` 뿐. 전제는 **LAN·Tailscale 안의 내부 도구**라는 것이고, 그 전제에서 `http.server` 가 감당하려면 「서버」절의 hardening 목록을 M0 에서 다 채운다. 인터넷에 직접 노출하거나 다중 팀이 쓰는 서비스가 되면 그때 `server.py` 를 프레임워크로 갈아끼운다(순수 계층은 안 건드린다).

## 설정

형식은 TOML(3.11 표준 `tomllib`). **우선순위: CLI 플래그 > 환경변수 > 설정 파일 > 기본값.** 파일 탐색 순서: `--config PATH` → `$RCM_CONFIG` → `./rcm.toml` → `$XDG_CONFIG_HOME/rcm/config.toml`(기본 `~/.config/rcm/config.toml`). 환경변수 이름 규칙: `RCM_<섹션>_<키>`(대문자, 예 `RCM_SERVER_PORT`). 목록은 쉼표로 나눈다. 시크릿은 값 대신 **환경변수 이름**을 설정에 적는다(`token_env`).

```toml
[github]
repos = ["owner/repo"]              # 1개 이상. 여러 개면 같은 러너를 나눠 쓰는 레포들의 큐를 합친다(M4)
token_env = "GITHUB_TOKEN"          # 이 env 가 비어 있으면 `gh auth token` 을 시도하고, 그것도 없으면 명확한 에러
write_token_env = ""                # rcm run(dispatch) 전용 토큰의 env 이름. 비어 있으면 token_env 의 토큰을 쓴다. Actions: write 가 필요하다
api_url = "https://api.github.com"  # GHES 면 바꾼다

[runner]                            # = 풀 하나. M4 에서 [[pools]] 목록으로 확장한다
names = []                          # 비어 있으면 제한 없음. 채우면 **이미 배정된 잡**에만 적용된다(대기 중 잡엔 러너 이름이 없다)
labels = ["self-hosted"]            # 잡의 labels 가 이 집합을 **포함**하면 「이 풀」 (기본값 확정 — 아래 「큐 판정」)
workflows = []                      # 비어 있으면 제한 없음. 채우면 이 워크플로 이름만
lanes = 0                           # 0 = 자동(매칭된 online 러너 수, 최소 1). 수동이면 양수

[estimate]
sample_days = 45                    # 이보다 오래된 표본은 버린다(코드가 커지면 같은 게이트도 느려진다)
min_samples = 2                     # 표본이 이보다 적으면 중앙값을 만들지 않고 기본값을 쓴다
min_job_seconds = 30                # 즉사 run 제외
sample_policy = "success"           # "success"(기본) | "completed"(실패·취소·타임아웃도 표본에 넣는다)
default_seconds = 600               # 표본이 없을 때
floor_remaining_seconds = 30        # 초과 실행 run 의 잔여 하한(음수 금지)
[estimate.defaults]                 # 키별 기본값(선택)
# "ci:full" = 480

[naming]
run_name_regex = ""                 # 비어 있으면 규약 없음. named group `scope`·`caller` 만 읽는다
key_template = "{workflow}"         # scope 가 잡히면 예: "{workflow}:{scope}"

[dispatch]                          # rcm run
join_duplicates = true              # 같은 워크플로·ref·head_sha(·scope) 가 이미 활성이면 새로 넣지 않고 그 run 에 붙는다
require_pushed_head = true          # git 체크아웃 안에서 실행하면 미커밋 변경·미푸시 커밋이 있을 때 멈춘다
find_run_timeout_seconds = 90       # dispatch 뒤 내 run 이 목록에 나타나길 기다리는 상한
wait_poll_seconds = 10              # rcm wait 의 폴링 주기(최소 3)

[server]
bind = "127.0.0.1"                  # 바깥에 열려면 명시적으로 0.0.0.0 — 그때 read_auth 가 none 이면 시작 로그에 경고
port = 8787
poll_seconds = 10                   # 큐에 활성 run 이 있을 때
idle_poll_seconds = 30              # 큐가 비었을 때
rate_limit_reserve = 500            # 잔량이 이 밑이면 저하 모드(60초 주기)
backfill_per_hour = 120             # 완료 run 잡 소요 백필의 시간당 상한
recent_count = 8
stale_after_seconds = 60            # 수집기 마지막 수신이 이보다 오래되면 stale
read_auth = "none"                  # "none" | "basic". 기본 none(Tailscale·LAN 전제). basic 은 TLS 프록시 뒤에서만 — 평문 HTTP 에선 비밀번호가 샌다
read_user = ""                      # basic 일 때
read_password_env = "RCM_READ_PASSWORD"
host_token_env = "RCM_HOST_TOKEN"   # POST /api/host 의 Bearer 토큰. --host-local 이면 불필요
max_concurrent_requests = 32        # 넘으면 503

[collector]
server_url = ""                     # rcm collect 가 push 할 곳
interval_seconds = 10
token_env = "RCM_HOST_TOKEN"
top_processes = 5
gpu = "auto"                        # "auto"(macOS ioreg · Linux nvidia-smi 가 있으면) | "off". 못 읽으면 null + 사유
# interval_seconds 하한: push 5초, --host-local 2초. 그보다 촘촘하면 top(1초 표본) 자체가 병목이다

[display]
timezone = ""                       # IANA 이름. 시작 시 zoneinfo 로 검증. 비어 있으면 rcm top 은 프로세스 로컬, 웹 UI 는 브라우저 로컬
```

- `rcm check` 는 설정을 읽어 토큰 출처·레포 접근·러너 목록·rate limit 잔량·시간대를 한 번 조회해 표로 보여준다(설치 직후 셋업 확인용).
- 설정 오류(없는 키·타입 불일치·`repos` 비어 있음·잘못된 시간대·정규식 컴파일 실패)는 시작 시점에 **키 이름과 함께** 실패한다. 조용히 기본값으로 떨어지지 않는다.
- Docker 안의 서버는 로컬 시간대가 UTC 다. `display.timezone` 이나 `TZ` 를 주라고 README 에 쓴다.

## GitHub API 클라이언트

- 인증: `token_env`(기본 `GITHUB_TOKEN`) → `gh auth token`(있을 때만, `subprocess`, 5초 타임아웃) → 둘 다 없으면 「토큰이 없다. GITHUB_TOKEN 을 주거나 gh auth login 을 하라」로 종료. 토큰 종류는 classic PAT(`repo`) · fine-grained PAT(Actions: read, Metadata: read) 둘 다. 토큰 값은 로그·JSON·오류 메시지에 절대 안 찍는다.
- ⚠️ Actions 워크플로 안에서 주는 `GITHUB_TOKEN` 은 레포당 시간당 1,000 이라 `rcm serve` 용으로 부적합하다. README 에 PAT 을 쓰라고 쓴다. PAT 의 5,000/시는 **그 계정의 다른 도구와 공유**된다.
- 전송: `urllib.request` + `Authorization: Bearer` + `X-GitHub-Api-Version: 2022-11-28` + `User-Agent: remote-ci-monitor/<ver>` · 타임아웃 20초 · 5xx·네트워크 오류는 1회 재시도.
- **페이지네이션**: `per_page=100` 으로 부르고 `Link: rel="next"` 를 따라간다. runs 는 1페이지만(최근 100건이면 큐 + 표본에 충분), jobs 는 `total_count > 100`(큰 matrix)이면 끝까지, runners 는 끝까지.
- **재실행**: `/runs/{id}/jobs` 는 기본(`filter=latest`)이 최신 attempt 다. 그걸 쓰고 캐시 키에 `run_attempt` 를 넣는다(`repo/run_id/attempt`) — 재실행하면 소요·라벨이 달라진다.
- **ETag**: `runs`·`runners` 응답의 `ETag` 를 기억하고 `If-None-Match` 로 보낸다. 304 는 `Authorization` 헤더로 인증된 요청이면 primary rate limit 에 안 센다(GitHub 문서 「Best practices」, 2026-09-04 확인). 2026-09-04 이 레포에서 `Etag: W/"…"` 반환 확인.
- **primary rate limit 가드**: 응답 헤더 `X-RateLimit-Remaining`·`Reset` 을 모델에 싣는다(`poll.rate_limit`). 잔량이 `rate_limit_reserve`(기본 500) 아래면 폴링 주기를 60초로 늘리고 백필을 멈추고 `poll.degraded = true` 로 표시한다.
- **secondary rate limit 가드**: 호출은 폴러 스레드 하나에서 **직렬**로만 한다(동시 요청 없음). 403/429 에 `Retry-After` 가 있으면 그만큼, 없으면 60초에서 시작해 두 배씩(최대 15분) 쉰다. 그동안 `poll.last_error` 에 사유를 싣는다.
- **에러 모델**: `ApiError(status, message, retry_after)` 예외 하나. `fetch_*` 는 성공 시 데이터, 실패 시 예외다. `[]`·`{}` 를 실패의 대용으로 돌려주는 코드는 리뷰에서 막는다.
- 엔드포인트와 필드(2026-09-04 이 레포에서 실측):
  - `GET /repos/{o}/{r}/actions/runs?per_page=100` → `id, name(워크플로 이름), display_title(run-name), path, workflow_id, run_number, run_attempt, head_sha, status, conclusion, event, created_at, run_started_at, updated_at, head_branch, actor.login, triggering_actor.login, html_url`
  - `GET /repos/{o}/{r}/actions/runs/{id}/jobs?per_page=100` → `total_count, jobs[].{id, name, status, conclusion, created_at, started_at, completed_at, runner_id, runner_name, runner_group_id, runner_group_name, labels[], steps[].{number, name, status, conclusion, started_at, completed_at}}`
  - `GET /repos/{o}/{r}/actions/runners?per_page=100` → `runners[].{id, name, os, status, busy, labels[].name}`

**호출 예산(레포 1개 · 풀 1개 · PAT 5,000/시 기준)**

| 호출 | 주기당 | 시간당 상한(활성 10초 주기) |
|---|---|---|
| runs 목록 | 1 (변화 없으면 304 → 0) | 360 |
| runners | 1 (변화 없으면 304 → 0) | 360 |
| 활성 run 의 jobs | 활성 run 수 N (+ 100 초과 시 페이지) | 360·N |
| 완료 run 잡 소요 백필 | 캐시 미스만, 주기당 ≤ 5 | `backfill_per_hour` = 120 |
| 키별 표본 보강 | 표본이 `min_samples` 미만인 키가 있을 때 그 워크플로 runs 1페이지(`?workflow_id=`) | 키당 시간당 1 |

활성 N=3 이면 최악 약 1,920/시(304 절약 전), 유휴 30초 주기면 약 300/시. 레포가 늘면 비례한다. 잔량 500 을 남기는 가드가 마지막 방어선이고, 예산이 모자라면 주기를 늘리는 게 정답이지 호출을 빼는 게 아니다(진행·잔여의 정확도가 여기 걸려 있다).

## 큐 판정 — 어떤 run 이 「이 풀」의 큐인가 (기본값 확정)

GitHub 은 run 이 어느 러너로 갈지 run 목록에서 안 알려준다. 잡 단위로만 `labels`(요구 라벨)와 `runner_name`(집어간 뒤)이 있다. 규칙은 `core/membership.py` 의 순수 함수이고 픽스처로 잠근다.

1. runs 목록에서 활성 상태(`queued` · `in_progress` · `pending` · `requested`)를 후보로 고른다. `waiting`(환경 승인 대기)은 러너 큐가 아니므로 `awaiting_approval: true` 로 표시만 하고 대기시간 계산에서 뺀다.
2. 후보마다 jobs 를 받는다(진행 표시에 어차피 필요하다).
3. **잡 단위 매칭**: `labels ⊇ runner.labels`(기본 `{"self-hosted"}`) 이고, `runner.workflows` 가 채워져 있으면 워크플로 이름도 맞고, `runner.names` 가 채워져 있고 잡이 이미 배정됐으면 그 이름도 맞아야 한다. run 은 **매칭된 잡이 하나라도 있으면** 이 풀이고, 진행·소요·잔여는 **매칭된 잡들만으로** 계산한다(hosted 잡과 self-hosted 잡이 섞인 run 에서 hosted 잡 시간이 표본에 섞이지 않게). `matched_jobs / jobs_total` 을 같이 싣는다.
4. **판정 불가**: 잡 목록이 아직 비어 있거나(`pending`·`requested`·막 생성된 run) jobs 조회가 실패하면 그 run 을 **버리지 않는다**. `membership: "unknown"` 으로 큐에 남기고 잔여는 기본값으로 센다(잠깐 비관적인 쪽이 조용히 빠지는 쪽보다 낫다). 다음 폴링에서 잡이 보이면 `matched` 또는 목록에서 제거된다.
5. 완료 run 은 소요시간 표본용이다. 잡 캐시에 `labels`·`runner_name`·`attempt` 를 같이 저장해 같은 규칙으로 거른다.
6. 러너 그룹은 `runner_group_name` 을 싣기만 하고 v1 에서 필터로 쓰지 않는다(그룹은 org 단위 개념이라 레포 API 로는 반쪽만 보인다).
7. `labels = ["self-hosted"]` 기본값은 「이 레포의 self-hosted 잡은 전부 한 풀에 선다」는 가정이다. 라벨을 갈라 러너를 여러 풀로 쓰는 팀은 `labels` 를 좁힌다(README 의 첫 번째 「숫자가 틀릴 수 있는 이유」). M4 에서 `[[pools]]` 목록으로 풀을 여러 개 정의한다.
8. ⚠️ **GitHub `concurrency` 그룹을 쓰는 워크플로**는 큐가 FIFO 가 아니다 — 그룹당 「실행 1 + 대기 1」만 남기고 세 번째가 오면 대기 중이던 것을 취소한다. 그러면 예상 완료가 틀리고 run 이 큐에서 사라진다. 도구가 고칠 수 있는 게 아니라 README 에 적는다(참고 구현의 팀은 이 때문에 게이트 워크플로에 concurrency 를 안 건다).

## 계산 규칙 (순수 · `core/queue.py` · `core/naming.py`)

- **키**(소요시간 버킷): 기본 `{workflow}`. `naming.run_name_regex` 의 `scope` 그룹이 잡히면 `key_template` 대로(예 `ci:full`). 정규식이 있는데 안 맞는 run 은 `{workflow}` 로 떨어진다(오류 아님).
- **요청자**: `login = triggering_actor.login`. 정규식의 `caller` 그룹이 잡히면 `label = caller`, `source = "run_name"`; 아니면 `label = login`, `source = "triggering_actor"`. `event`(`workflow_dispatch`·`push`·`pull_request`·`schedule`)도 같이 싣는다. 요청자 문자열은 40자에서 자르고 제어문자를 지운다.
- **표본**: 완료 run 중 `sample_policy` 에 맞는 것(`success` 기본 — 실패 run 은 도중에 죽어 소요가 짧아 중앙값을 끌어내린다) · 매칭된 잡의 `started_at`~`completed_at`(여러 잡이면 min~max) · `min_job_seconds` 이상 · `sample_days` 안. run 의 `run_started_at`~`updated_at` 은 **큐 대기가 섞여** 쓰지 않는다. 「최근 완료」에는 정책과 무관하게 실패·취소·타임아웃도 다 보인다(걸린 run 은 그 자체가 신호다).
- **중앙값**: 키별, `min_samples` 이상일 때만. 아니면 `estimate.defaults[key]` → `default_seconds`. 출력에 `source: measured|default` 와 `sample_count` 를 싣는다.
- **잔여**: `queued` → expected 전체. `in_progress` → `max(expected − (now − job_started_at), floor)`. `job_started_at` 은 매칭된 잡의 `started_at` 중 최소이고 없으면(러너 배정 전) queued 취급. `overdue = elapsed > expected`.
- **순서**: `(created_at, id)` 오름차순 = 러너가 집어가는 FIFO. `position` 을 1부터 싣는다.
- **레인**: `runner.lanes > 0` 이면 그 값(`lanes_source: "config"`). 0 이면 매칭된 online 러너 수(`"runners"`). 러너 조회가 실패하면 마지막으로 알던 값, 그것도 없으면 1 로 두되 `lanes_source: "assumed"` 를 싣고 화면에 배지를 띄운다(조용히 1 로 두면 fail-open 이다).
- **대기**: 레인 1 → 앞선 잔여의 합. 레인 k → 잔여를 큰 순으로 가장 빨리 비는 레인에 얹는 그리디, 최소 레인 시각. `wait_seconds` 로 싣는다.
- **완료 시각**: `now + wait + remaining`.
- 시각은 내부 전부 UTC aware `datetime`. 표시 시점에만 시간대를 입힌다.

## 진행 규칙 (순수 · `core/progress.py`) — jobs API 실측 함정 (픽스처로 잠근다)

| # | 실측 사실 (2026-09-04) | 규칙 | 테스트 이름 |
|---|---|---|---|
| 1 | 아직 안 시작한 스텝은 `status: "pending"` (`queued` 아님) | pending 을 「미래」로 분류 | `test_pending_steps_are_future` |
| 2 | post 스텝(`Post Run actions/checkout@v4`, number 14)은 도중에 보이지만 `Complete job`(15)은 끝날 때 붙는다 → 알려진 스텝 수가 8 → 9 로 는다 | `steps_total` 은 「지금까지 알려진 수」. 진행 중엔 `steps_total_partial: true`, UI 는 「N/M (so far)」 | `test_known_step_count_grows_on_completion` |
| 3 | 스텝 `number` 는 연속이 아니다(1~7 다음 14, 15) | 「N번째」는 목록 위치(`index`) | `test_step_position_not_number` |
| 4 | 러너가 안 집어간 잡은 `steps: []` · `runner_name: null` · `started_at == created_at` | state `queued`, 대기시간은 `created_at` 기준, 스텝 수 없음(0/0 금지) | `test_unassigned_job_waits_from_created_at` |
| 5 | run 의 `run_started_at` 은 큐 진입 시각 | 표본·경과는 잡 시각만 | `test_samples_use_job_window_not_run_window` |
| 6 | 초과 실행 run 의 잔여를 음수로 두면 큐 전체가 앞당겨진다 | 하한 30초 · `overdue` 표시 · 실제 경과는 그대로 | `test_overdue_run_floors_remaining` |

그 외: 여러 잡이면 진행 중인 잡 → 아직 안 돈 잡 → 마지막 잡 순으로 대표 잡을 고르되 `jobs[]` 요약 전부를 싣는다. 스텝 사이(진행 중 스텝이 없음)면 다음 스텝을 `current`, 경과 `null`(「시작 대기」). `skipped` 는 완료로 센다. 시각이 빠진 스텝의 경과는 `null`.

## fail-open 금지 (이 도구의 핵심 규칙)

아무도 안 쳐다보는 보조 화면일수록 조용히 고장나면 **틀린 값을 자신있게 보여주게 된다.**

- 조회에 실패한 칸은 「조회 실패」로 그린다. 실패를 0건·0/0·0초로 그리지 않는다.
- API 의 「실패」와 「빈 목록」을 코드에서 다른 값으로 돌려준다(예외 vs `[]`).
- JSON 스키마도 실패를 `null` + `*_error` 로 가른다. 수집기가 안 오면 `stale: true` 와 `age_seconds` 를 같이 찍는다. 아직 한 번도 안 왔으면 `hosts: []` + `hosts_note`.
- 큐 소속을 모르는 run(잡 목록 없음·jobs 조회 실패)은 빈 큐로 접지 않고 `membership: "unknown"` 으로 남긴다. 러너 조회 실패로 레인 수를 모르면 `lanes_source: "assumed"` 를 띄운다.
- `rcm run` 이 dispatch 는 했는데 내 run 을 못 찾은 것은 「실패」가 아니라 「미확인」이다(종료 코드 3). `rcm wait` 가 조회에 실패하면 「실패(1)」가 아니라 「미확인(3)」이다 — 게이트를 빨강으로 위장하지 않는다.
- 서버 자체의 건강(`poll.last_ok_at` · `last_error` · `degraded`)을 JSON 과 화면 머리에 찍는다. 마지막 성공이 3 주기보다 오래되면 UI 헤더가 경고색이 된다.
- 순수 계산 모듈은 픽스처 테스트를 갖고 CI 가 매번 돌린다. 테스트가 실제로 빨개지는지 `scripts/mutcheck.py` 로 확인한다.

## 호스트 자원 — 수집기 (M2)

**프로토콜**: `POST /api/host` · `Authorization: Bearer <RCM_HOST_TOKEN>` · `Content-Type: application/json` · 본문 64KB 이하.

```json
{"schema_version": 1, "sampled_at": "2026-09-04T00:52:12Z", "hostname": "runner-1", "os": "darwin",
 "cores": 10, "load": [3.48, 3.1, 2.9],
 "cpu": {"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
 "memory": {"total_bytes": 25769803776, "used_bytes": 15032385536},
 "gpu": {"util_pct": 13, "mem_used_bytes": 594411520, "mem_total_bytes": null, "source": "ioreg"},
 "top": [{"comm": "gen_snapshot", "cpu": 103.0, "rss_mb": 169}]}
```

- 서버는 `received_at` 을 **자기 시계**로 찍고 stale 도 그걸로 판단한다(클라이언트 시계를 안 믿는다). 토큰 비교는 `hmac.compare_digest`. 토큰 불일치 401, 본문 초과 413, 스키마 불일치 400. **호스트명별로 마지막 샘플 하나**를 메모리에 둔다(러너 머신이 여러 대면 `hosts[]` 에 여러 개). 이력은 범위 밖.
- 수집기(`rcm collect`)는 실패해도 죽지 않는다: 연결 실패·5xx 는 지수 백오프(최대 60초) 후 재시도, 401 은 로그에 크게 찍고 계속 재시도(토큰을 고치면 바로 살아나게).
- 값이 없는 칸은 `null`(0 아님). 부분 실패(예 `top` 만 실패)는 그 칸만 `null`.
- **macOS** 소스: `os.getloadavg()` · `sysctl -n hw.memsize` · `vm_stat`(active + wired + compressor = 사용량) · `top -l 2 -n 0 -s 1`(**두 번째** 표본만 — 첫 표본은 부팅 후 누적) · `ps -Aro %cpu=,rss=,comm=`.
- **Linux** 소스: `/proc/loadavg` · `/proc/meminfo`(`MemTotal − MemAvailable` = 사용량) · `/proc/stat` 1초 간격 두 표본의 차로 user/sys/idle · `ps -eo %cpu=,rss=,comm= --sort=-%cpu` · 코어 수 `os.cpu_count()`.
- **GPU**: macOS 는 `ioreg -r -d 1 -w 0 -c IOAccelerator` 의 `PerformanceStatistics` 에서 `Device Utilization %` 와 `In use system memory` 를 읽는다(2026-09-04 Apple Silicon 에서 **sudo 없이** 확인. `powermetrics` 는 sudo 가 필요해 안 쓴다. 통합 메모리라 `mem_total_bytes` 는 null). Linux 는 `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits` 가 있을 때만(`source: "nvidia-smi"`). 둘 다 없으면 `gpu: null` + `gpu_note`("no supported GPU reader"). 다른 GPU(AMD·Intel)는 범위 밖으로 명시.
- 파서는 전부 `core/hostparse.py` 의 순수 함수이고 두 OS 의 실제 출력 캡처를 픽스처로 잠근다. 명령 실행은 `hostsample.py` 에만 있다.
- **로컬 모드** `rcm serve --host-local`: 서버가 러너 머신에서 돌 때 같은 샘플러를 in-process 로 돌린다. 전송·토큰이 없다(`source: "local"`). (확정 2026-09-04 — v0 의 「gist 주기 갱신」 대안을 이걸로 대체한다. gist 는 지연이 크고 토큰 권한이 하나 더 필요하다. Codex 도 같은 의견)
- 서비스 파일: `examples/launchd/com.remote-ci-monitor.collector.plist` · `examples/systemd/rcm-collector.service`(+ `rcm-server.service`). 러너 머신이 잠들면 러너도 수집기도 같이 죽으니 README 에 잠자기 금지 안내.

## 세션 명령 — `rcm run` · `rcm eta` · `rcm wait` (M1)

다른 컴퓨터의 세션(사람 터미널·Claude Code 스킬·스크립트)이 **한 줄로 CI 를 넣고, 자기 차례를 기다리고, 성공/실패를 종료 코드로 받는** 경로다. 참고 구현의 `remote_ci.sh` 가 하던 일을 이식성 규칙에 맞게 흡수한다. 요청은 GitHub 에 직접 넣고 **도구는 아무것도 저장하지 않는다** — 순차 처리는 GitHub 큐가 보장하고(러너 1대 = 동시 1잡, 생성 시각 순 FIFO), 서버가 죽어도 요청은 안 사라진다. 서버는 있으면 상태 조회에만 쓴다(rate limit 절약).

### `rcm run WORKFLOW [--ref REF] [-f KEY=VALUE …] [--by LABEL] [--no-join] [--no-wait] [--allow-dirty]`

1. **ref 결정**: `--ref` 가 없으면 현재 git 체크아웃의 브랜치. 체크아웃이 아니면 `--ref` 필수.
2. **가드**(체크아웃 안에서 실행할 때, `require_pushed_head`): 미커밋 변경이 있으면 멈춘다(`--allow-dirty` 로만 강행, 그때도 무엇이 빠지는지 출력). 로컬 HEAD ≠ `origin/<ref>` 면 멈춘다. **dispatch 는 원격 브랜치 HEAD 만 본다** — 로컬에만 있는 커밋은 조용히 빠지고 그 초록은 가짜가 된다(참고 구현이 실제로 겪은 함정).
3. **합류**(`join_duplicates`): 활성 run(`queued`·`in_progress`·`pending`·`requested`) 중 같은 워크플로·같은 ref·같은 `head_sha`(+ `naming.run_name_regex` 가 있으면 같은 `scope`) 가 있으면 **새로 넣지 않고 그 run 에 붙는다**. 두 세션이 같은 커밋을 확인하려는 것뿐이라 두 번 돌릴 이유가 없다. ⚠️ `workflow_dispatch` 의 `inputs` 는 runs API 에 안 나와 비교할 수 없다. run 이름 규약이 없는 팀에서 입력이 다른 요청을 겹쳐 넣을 땐 `--no-join`(README 에 명시).
4. **dispatch**: `POST /repos/{o}/{r}/actions/workflows/{file|id}/dispatches` `{ref, inputs}`. 204 가 오고 **run id 는 안 돌아온다**. 직전 시각 `t0` 와 기대 sha(`origin/<ref>` 의 HEAD, 체크아웃이 없으면 `GET /repos/{o}/{r}/commits/<ref>`) 를 기억한다. `--by LABEL` 은 `inputs.caller` 같은 이름으로 워크플로가 받을 때만 실어 보낸다(설정 `dispatch.caller_input`, 기본 없음) — 요청자 기본값은 어차피 `triggering_actor` 다.
5. **내 run 찾기**: `GET runs?event=workflow_dispatch&branch=<ref>&actor=<login>&created=>=<t0>` 를 2초 간격으로 `find_run_timeout_seconds` 까지 폴링(`login` 은 `GET /user`). 후보 중 `head_sha == 기대 sha` · `created_at >= t0` · 워크플로 일치인 것의 **가장 이른** run. 못 찾으면 종료 코드 3 + 「dispatch 는 됐으나 run 을 못 찾았다」(실패로 위장 안 함). 판정은 `core/dispatchmatch.py` 의 순수 함수.
6. **출력**: stdout 에 JSON 한 줄(`run_id`·`url`·`joined`·`position`·`wait_seconds`·`finish_at`), stderr 에 사람용 한 줄. `--no-wait` 가 아니면 그대로 `rcm wait` 로 이어진다.
7. **토큰**: dispatch 는 쓰기 권한이다(fine-grained PAT `Actions: Read and write`, classic `repo`; public 레포는 `public_repo`). 403 은 「토큰에 Actions: write 가 없다」로 번역해 보여준다. `write_token_env` 로 읽기 토큰과 분리할 수 있다(서버는 읽기 토큰만 갖게).

### `rcm eta (--run ID | --workflow W [--scope S]) [--server URL]`

내 run(또는 「지금 넣으면」)의 앞선 건수·대기·자기 소요·예상 완료·표본 출처(`measured n=7` / `default`)를 JSON 과 한 줄로. 계산은 `core/queue.py` 의 **같은 함수**(터미널·서버·eta 가 세 벌을 두지 않는다).

### `rcm wait --run ID [--timeout S] [--server URL]`

- 폴링(`wait_poll_seconds`, 최소 3). 서버가 있으면 `/api/status` 를 읽어 GitHub 호출이 없고, 없으면 `GET runs/{id}` + jobs 를 직접 본다.
- 기다리는 동안 stderr 에 위치·현재 스텝·경과·ETA 를 갱신한다(TTY 면 한 줄 덮어쓰기, 아니면 변화가 있을 때만 새 줄).
- 끝나면 stdout 에 JSON 한 줄(`run_id`·`conclusion`·`job_seconds`·`failed_step`·`url`). **종료 코드**: `success` 0 · `failure` 1 · `cancelled`/`timed_out`/`action_required`/`stale` 2 · 조회 실패·`--timeout` 초과 3. 세션 스크립트는 이 코드로 바로 분기한다.
- run 이 활성 목록에서 사라지면(concurrency 그룹 취소 등) `GET runs/{id}` 로 확정한 뒤 2 를 돌려준다. 완료 뒤 `Complete job` 이 붙으며 스텝 수가 느는 건 정상(「진행 규칙」 2).

### 세션에서 쓰는 모양 (`examples/session/ci-gate.sh`)

```bash
if out=$(rcm run ci -f scope=full --by "$(whoami)@$(hostname -s)"); then
  echo "gate green: $(jq -r .url <<<"$out")"
else
  case $? in 1) echo "gate red — $(jq -r .failed_step <<<"$out")";; 2) echo "cancelled/timed out";; *) echo "unknown — check $(jq -r .url <<<"$out")";; esac
fi
```

범위 밖(오너 결정 2026-09-04): 서버가 밖으로 미는 webhook, 커밋 status·PR 코멘트 갱신. 결과 전달은 위 폴링 명령뿐이다.

## 서버 (`server.py`)

| 라우트 | 동작 |
|---|---|
| `GET /` · `GET /static/*` | 패키지 안의 정적 UI. 캐시 헤더 짧게 |
| `GET /api/status` | 최신 `StatusModel` JSON. `ETag`(= `generated_at`) 지원. `read_auth` 적용 |
| `GET /api/health` | 폴러 스레드 살아 있고 마지막 성공이 3 주기 안이면 200, 아니면 503 + 사유. 인증 없음(모니터링용) |
| `POST /api/host` | 수집기 샘플 수신. Bearer 토큰 필수 |

- `http.server.ThreadingHTTPServer`, 요청 스레드는 daemon. 폴러는 별도 스레드 1개, 모델 교체는 참조 교체 한 번(락 최소).
- **hardening 목록(M0 에서 전부)**: 소켓 타임아웃 10초 · `protocol_version = "HTTP/1.0"`(keep-alive 없음 → 놀고 있는 스레드 없음) · `Content-Length` 필수, 상한 64KB, chunked 거부 · 동시 요청 `max_concurrent_requests` 초과 시 503 · 정적 경로는 `posixpath.normpath` 뒤 `web/` 안인지 확인(`..` 차단) · 모르는 메서드 405, JSON 파싱 실패 400 · 어떤 예외도 스택·토큰을 응답에 싣지 않고 500 한 줄 · 요청 로그는 debug 레벨에만.
- 기본 바인드 `127.0.0.1`. 바깥에 열 땐 `--bind 0.0.0.0` 을 명시해야 하고, 그때 `read_auth = none` 이면 시작 로그에 「이 포트에 닿는 누구나 CI 큐와 프로세스 이름을 본다」경고를 찍는다.
- **읽기 인증 (확정: 기본 `none`)**: 이 도구는 Tailscale·LAN 안에서 쓰는 것을 전제로 한다(2026-09-04 오너 결정). `basic` 은 HTTP Basic(브라우저가 알아서 묻고 UI 코드 변경 없음)이지만 **요청마다 비밀번호가 평문으로 가므로 TLS 프록시(Caddy·Tailscale Serve) 뒤에서만** 쓴다. 토큰을 URL 에 싣는 방식은 로그에 새서 안 쓴다. TLS 는 서버가 직접 하지 않는다.

## `/api/status` 스키마 v1 (`rcm top --json` 과 동일)

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-04T00:52:12Z",
  "display_timezone": null,
  "poll": {"interval_seconds": 10, "last_ok_at": "2026-09-04T00:52:12Z", "last_error": null, "degraded": false,
           "rate_limit": {"remaining": 4961, "limit": 5000, "reset_at": "2026-09-04T01:00:00Z"}},
  "repos": [{"name": "owner/repo", "error": null}],
  "pools": [{
    "name": "default", "labels": ["self-hosted"], "workflows": [],
    "lanes": 1, "lanes_source": "runners",
    "runners": [{"id": 1, "name": "runner-1", "os": "macOS", "status": "online", "busy": true,
                 "labels": ["self-hosted", "macOS", "arm64"]}],
    "runners_error": null,
    "queue": [{
      "position": 1, "id": 33823449912, "repo": "owner/repo",
      "workflow": "ci", "workflow_id": 123, "key": "ci:full",
      "title": "ci · full · feat/x", "branch": "feat/x", "head_sha": "abc123…", "run_number": 412, "run_attempt": 1,
      "event": "workflow_dispatch",
      "requester": {"login": "alice", "label": "alice@laptop", "source": "run_name"},
      "status": "in_progress", "awaiting_approval": false,
      "membership": "matched", "matched_jobs": 1, "jobs_total": 1,
      "created_at": "2026-09-04T00:50:40Z", "job_started_at": "2026-09-04T00:51:13Z",
      "estimate": {"expected_seconds": 369, "source": "measured", "sample_count": 7,
                   "elapsed_seconds": 59, "remaining_seconds": 310, "wait_seconds": 0,
                   "overdue": false, "finish_at": "2026-09-04T00:57:22Z"},
      "progress": {
        "state": "in_progress", "jobs_total": 1, "jobs_done": 0,
        "job_name": "ci", "runner_name": "runner-1", "runner_group_name": "Default",
        "steps_total": 8, "steps_total_partial": true, "steps_done": 4,
        "current_index": 5, "current_name": "local_ci", "current_seconds": 51, "job_seconds": 59,
        "failed_step": null, "waited_seconds": null,
        "steps": [{"index": 1, "number": 1, "name": "Set up job", "status": "completed", "conclusion": "success", "seconds": 2},
                  {"index": 5, "number": 5, "name": "local_ci", "status": "in_progress", "conclusion": null, "seconds": 51},
                  {"index": 8, "number": 14, "name": "Post Run actions/checkout@v4", "status": "pending", "conclusion": null, "seconds": null}],
        "jobs": [{"name": "ci", "status": "in_progress", "conclusion": null, "matched": true}]
      },
      "progress_error": null,
      "url": "https://github.com/owner/repo/actions/runs/33823449912"
    }],
    "queue_error": null,
    "recent": [{"id": 33822932416, "repo": "owner/repo", "workflow": "ci", "key": "ci:full", "branch": "feat/y",
                "requester": {"login": "bob", "label": "bob", "source": "triggering_actor"},
                "conclusion": "success", "job_seconds": 350, "finished_at": "2026-09-04T00:47:03Z", "url": "…"}],
    "recent_error": null,
    "medians": {"ci:full": {"seconds": 369, "sample_count": 7}},
    "hosts": [{"name": "runner-1", "source": "push", "received_at": "2026-09-04T00:52:08Z", "age_seconds": 4, "stale": false,
               "os": "darwin", "cores": 10, "load": [3.48, 3.1, 2.9],
               "cpu": {"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
               "memory": {"total_bytes": 25769803776, "used_bytes": 15032385536},
               "gpu": {"util_pct": 13, "mem_used_bytes": 594411520, "mem_total_bytes": null, "source": "ioreg"}, "gpu_note": null,
               "top": [{"comm": "gen_snapshot", "cpu": 103.0, "rss_mb": 169}]}],
    "hosts_note": null
  }]
}
```

규칙: 시각은 전부 UTC ISO-8601(`Z`). 조회 실패 섹션은 `null` + `*_error: "문자열"`. 모르는 숫자는 `null`. M0~M3 는 `pools` 가 항상 한 개(`default`)다. `schema_version` 을 올리는 변경(키 삭제·의미 변경)은 CHANGELOG 에 적는다. 키 추가는 버전을 안 올린다.

## 터미널 `rcm top`

- `rcm top`: GitHub 를 직접 한 번 조회해 그린다. `--watch [초]`(기본 10, 최소 3) · `--json`.
- `rcm top --server URL`: 서버의 `/api/status` 를 받아 그린다(GitHub 호출 없음, 호스트 자원 칸도 보인다).
- 실패 칸은 「조회 실패 — 사유」. 시간대는 `display.timezone` 또는 로컬.
- 종료 코드: 큐 조회 성공 0, 실패 2(스크립트가 물려 쓰게).

```
━━━ remote-ci-monitor · owner/repo · 09:52:12 KST · poll ok 4s ago · rate 4961/5000
runner  runner-1: online · busy   (lanes 1)

queue — 2
  1. ▶ running  ci:full   feat/x                     ← alice@laptop      remaining 5m 10s   eta 09:57  (measured, n=7)
        step 5/8 (so far) · local_ci · 51s · job 59s
        ✔ Set up job 2s  ✔ checkout 1s  ✔ … ▶ local_ci 51s  · Report to job summary  · Post Run checkout
  2. · queued   deploy-dev dev                        ← bob               remaining 8m 20s   eta 10:05  (default)
        waiting for a runner · 35s
recent
  ✅ ci:full   feat/y   ← bob    5m 50s   09:47
medians: ci:full 6m 9s (n=7)
host  runner-1 (4s ago)  load 3.48 / 10 cores · CPU 21% (user 17 · sys 4) · mem 14.0 / 24 GB
      top: gen_snapshot 103% 169MB · dart 40% 512MB
```

## 웹 UI (M1)

빌드 도구 없이 `index.html` + `app.js` + `style.css`. `fetch('/api/status')` 를 응답의 `poll.interval_seconds` 마다 다시 부른다(ETag 로 304 면 갱신 생략). 문자열은 영어. `pools[]` 를 순회해 그리므로 M4 에서 UI 구조가 안 바뀐다.

```
┌ remote-ci-monitor · owner/repo            ● poll ok 4s ago · rate 4961 · [auto 10s] ┐
│ RUNNERS  [runner-1  online · busy  self-hosted macOS arm64]  [runner-2 offline]     │
│ QUEUE (lanes 1)                                                                     │
│  # │ state   │ key      │ branch │ requester      │ elapsed │ remaining │ eta       │
│  1 │ ▶ run   │ ci:full  │ feat/x │ alice@laptop   │ 0:59    │ 5:10      │ 09:57 ▾   │
│      ├ step 5/8 (so far) · local_ci · 51s      ████████░░░░░░░ (timeline bars)      │
│  2 │ · queue │ deploy   │ dev    │ bob            │ waiting 35s │ 8:20  │ 10:05 (default)│
│ RECENT   ✅ ci:full feat/y bob 5m50s 09:47   ❌ ci:fast feat/z alice 1m02s 09:30   │
│ HOST  runner-1 · 4s ago   load 3.48/10   CPU 21%  ▮▮▮▯▯   mem 14.0/24 GB  ▮▮▮▮▮▯▯   │
│       top: gen_snapshot 103% 169MB · dart 40% 512MB                                 │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

- 실패·stale·미상 상태는 각 카드 머리에 배지(「fetch failed: …」 · 「stale 3m」 · 「membership unknown」 · 「lanes assumed」). 초과 실행은 잔여 대신 경과를 강조한다.
- 모바일: 한 열, 표는 카드로. 다크/라이트: `prefers-color-scheme`. 시간대: `display_timezone` 이 있으면 그것, 없으면 브라우저 로컬(`Intl.DateTimeFormat`).
- 행을 펼치면 스텝 타임라인(막대) · 잡 여러 개 · run 링크.

## 테스트·품질

- **픽스처**(`tests/fixtures/`): jobs 응답 3종(진행 중 · 러너 배정 전 · 완료, post 스텝 포함) + hosted/self-hosted 혼합 run + 100 초과 페이지네이션 · runs 목록(활성·완료·waiting·재실행 attempt 2) · runners · macOS `vm_stat`/`top`/`ps` · Linux `/proc/meminfo`/`/proc/stat`/`/proc/loadavg`/`ps`. 실측 캡처에서 브랜치명·요청자·run id·호스트명을 지운다.
- **테스트 목록**(M0): `test_queue.py`(참고 구현의 21 시나리오를 옮기되 팀 상수 제거 + 레인 assumed) · `test_progress.py`(18 시나리오 + 위 표의 6개 이름) · `test_naming.py`(규약 없음/있음/안 맞음) · `test_membership.py`(hosted 잡 · self-hosted 잡 · 혼합 run · 라벨 부분집합 · 잡 없음 → unknown · names 는 배정된 잡에만) · `test_hostparse.py`(두 OS) · `test_status_schema.py`(`json.dumps` 성공 · 실패는 `null`+`*_error` · datetime 안 샘 · pools 한 개) · `test_render_text.py`(빈 큐 vs 조회 실패가 다르게 그려짐) · `test_config.py`(우선순위 · 오류 메시지 · 시간대 검증) · `test_client.py`(가짜 전송으로 304·401·403·429·`Retry-After`·페이지네이션 경로) · `test_server.py`(in-process 서버: 인증 · 413 · 경로 탈출 차단 · stale 계산) · `test_dispatchmatch.py`(합류: 같은 sha+ref → 붙음 · sha 다름 → 새로 · scope 다름 → 새로 · 후보 여러 개 → 가장 이른 것 · 못 찾음 → 미확인 · wait 종료 코드 매핑 전부) · `test_hostparse.py` 에 `ioreg`·`nvidia-smi` 캡처.
- **뮤테이션 확인** `scripts/mutcheck.py`: `src/`+`tests/` 를 tmpdir 에 복사하고 변이 하나를 넣은 뒤 그 복사본에서 pytest 를 돌려 **빨개지는지** 본다(원본은 안 건드리니 되돌릴 게 없다). 변이 패턴이 소스에 없으면 그 자체로 실패한다(코드가 바뀌어 뮤테이션이 헛돌면 잡아야 한다). 3종 — ① 잔여 하한 제거(`max(…, floor)` → `…`) ② `ApiError` 를 `[]` 로 삼킴 ③ 스텝 위치 대신 `number` 사용. 셋 다 빨개져야 「검증됨」이라고 말한다. CI 에서도 돌린다(초 단위라 싸다).
- **CI**(`ci.yml`): `unit` 잡(matrix: py 3.11 · 3.13, ubuntu) → `ruff check` · `ruff format --check` · `pytest` · `scripts/mutcheck.py`. `secrets` 잡 → `gitleaks/gitleaks-action@v3`(개인 계정 레포는 라이선스 키 불필요 — 조직 레포로 옮기면 무료 키 필요, 2026-09-04 README 확인). 집계 잡 **`test`**(룰셋 필수 체크)는 `needs: [unit, secrets]` + `if: always()` 로 항상 돌고, `needs.unit.result`·`needs.secrets.result` 가 **둘 다 `success`** 가 아니면 `exit 1`(`cancelled`·`skipped` 도 실패로 전파). macOS 러너 잡은 M2 에서 `hostsample` 스모크용으로 하나 추가(public 이라 무료).
- 코드 스타일: ruff(기본 + `I` import 정렬), 줄 100자, 타입 힌트 필수(순수 계층은 `mypy --strict` 를 M1 에서 검토).

## 패키징·배포 (M3)

- `pyproject.toml`: hatchling · `requires-python = ">=3.11"` · `dependencies = []` · `[project.optional-dependencies] dev = ["pytest", "ruff"]` · `[project.scripts] rcm = "remote_ci_monitor.cli:main"`, `remote-ci-monitor = "remote_ci_monitor.cli:main"` · `web/` 은 패키지 데이터.
- 설치: `pipx install remote-ci-monitor` → `rcm` · `uvx remote-ci-monitor serve` · `uvx --from remote-ci-monitor rcm top`.
- Docker: `python:3.12-slim` · `pip install .` · `ENTRYPOINT ["rcm"]` · `CMD ["serve", "--bind", "0.0.0.0"]` · 환경변수로 설정 · 시간대는 `TZ` 또는 `display.timezone`. 서버만 담는다(수집기는 호스트 프로세스가 필요해 pipx 로).
- 릴리스: 태그 `vX.Y.Z` → `release.yml` 이 sdist/wheel 빌드 → PyPI trusted publishing(OIDC, 시크릿 없음) → GHCR 이미지. `main` 에서만.
- README(영어): 30초 셋업(PAT → `rcm check` → `rcm top`) · 세 실행 형태 · 설정 표 · 스키마 링크 · 인터넷 노출은 TLS 프록시 뒤에서 · 「why the numbers can be wrong」(라벨 과포함 · concurrency 그룹 · so-far 스텝 수 · default 추정 · stale · lanes assumed).

## 마일스톤과 완료 기준

- **M0 — 뼈대** (한 세션): 위 모듈 구조 · 설정 로딩 · GitHub 클라이언트(ETag·페이지네이션·attempt·양쪽 rate limit 가드·에러 모델) · 순수 계산 전부 + 픽스처 테스트 · `rcm top` · `rcm serve` 의 `/api/status`·`/api/health` + hardening 목록 · `rcm check` · CI 잡 채우기 · `mutcheck.py`. 완료 기준: `rcm top` 이 실제 레포에서 큐·진행을 맞게 그린다 · 테스트 전부 통과 · 뮤테이션 3종 빨개짐 · CI 초록.
- **M1 — 세션 명령**: `rcm eta` · `rcm wait`(종료 코드) · `rcm run`(가드·합류·내 run 찾기·쓰기 토큰) · `examples/session/ci-gate.sh`. 완료 기준: 다른 컴퓨터에서 `rcm run` 한 줄로 넣고 종료 코드로 결과를 받는다 · 같은 커밋을 두 세션이 넣으면 두 번째는 합류한다 · 미푸시 커밋이 있으면 멈춘다.
- **M2 — 웹 UI**: 정적 UI 전부 · 갱신 · 실패/stale/미상 배지 · 모바일 · 다크/라이트 · `read_auth = basic` 옵션(기본 none · TLS 프록시 뒤 전용). 완료 기준: 폰에서 큐와 스텝 진행이 읽힌다 · 서버를 끊으면 UI 가 「stale」을 띄운다.
- **M3 — 수집기**: `rcm collect` (macOS·Linux, CPU·메모리·**GPU**) · `POST /api/host` · `--host-local` · 서비스 파일 · macOS CI 스모크. 완료 기준: 러너 머신에서 push 한 값이 다른 컴퓨터의 UI 에 보이고, 수집기를 죽이면 60초 안에 stale 이 뜬다 · Apple Silicon 에서 GPU 사용률이 보인다.
- **M4 — 배포**: pipx/uvx · Docker · PyPI/GHCR 릴리스 워크플로 · `examples/` · README. 완료 기준: 새 머신에서 README 만 보고 5분 안에 `rcm top` 과 `rcm run` 이 된다.
- **M5 — 확장**: 레포 여러 개 · `[[pools]]` 로 러너 풀 여러 개(라벨별 레인·호스트) · run 취소(선택) · 알림(선택).

## 결정 항목

**확정(기본값으로 진행 — 2026-09-04 Codex 리뷰와 일치)**: 언어 Python 3.11+ · 런타임 의존성 0 + 서버 hardening 목록 · GitHub 는 REST 직접(`gh` 는 토큰 폴백) · 설정 TOML+env+플래그 · 시간대 내부 UTC, 표시만 설정/로컬 · **큐 판정은 잡 라벨 기반 + 워크플로 allowlist 선택**(allowlist 필수는 첫 설치를 무겁게 한다) · **표본은 성공 run 의 잡 시간만, `sample_policy = "completed"` 로 열 수 있음** · **스키마는 `pools[]` 축을 처음부터, 구현은 M3 까지 풀 하나** · UI 는 정적 HTML+JS, 폴링 · 패키지명 `remote-ci-monitor`/명령 `rcm` · CI 는 matrix + 집계 잡 `test`.

**오너 결정(2026-09-04)**:
1. **서버 노출 범위** — Tailscale/LAN 안에서만 쓴다. 읽기 인증 기본 `none`, 서버는 TLS 를 안 한다. `basic` 은 옵션으로만 두고(TLS 프록시 뒤 전용) 인터넷 노출은 README 의 「TLS 프록시 뒤에서」 안내로 끝낸다. 내장 로그인은 범위 밖.
2. **호스트 자원의 두 번째 경로** — v0 의 「gist 주기 갱신」을 버리고 `rcm serve --host-local`(서버가 러너 머신에서 직접 수집)로 대체한다.
3. **언어** — 식별자·UI 문자열·README·CLI 도움말은 영어, 주석·docstring 은 한국어. 계획서·커밋 본문·리뷰 기록도 한국어.
4. **큐 역할** — 이 도구가 **디스패치까지 담당**한다(`rcm run`: workflow_dispatch + 중복 합류 + 대기). 요청을 도구가 저장하는 자체 브로커는 하지 않는다 — 순차 처리는 GitHub 큐가 보장한다.
5. **세션 전달** — 대기 위치·ETA·완료/실패는 **폴링 명령**(`rcm eta`·`rcm wait`, 종료 코드)으로만. 서버 webhook 과 커밋 status/PR 코멘트 갱신은 범위 밖.
6. **GPU** — CPU·메모리와 같은 수집기에서 GPU 사용률·GPU 메모리를 읽는다(macOS `ioreg`, Linux `nvidia-smi`). 실시간은 폴링(기본 10초, 하한 2초)이다.

## 참고 구현 (private — 접근 가능한 사람만)

`fmmc-tech/dolomood-app-renew` 의 `scripts/ci_queue.py`(큐·중앙값·잔여 계산, 21개 자기검증) 와 `scripts/ci_top.py`(진행률·파서·렌더, 18개 자기검증), 문서 `docs/renew-guide/ci-cd/30-remote-dispatch.md`(로컬에선 `dolomood-ci-monitor` 워크트리의 `feat/ci-monitor` 브랜치에 있다). 설계와 함정은 가져오되 코드는 이식성 규칙에 맞게 다시 쓴다. 픽스처를 옮길 땐 브랜치명·요청자 라벨·run id 같은 팀 정보는 지운다.

- **버리는 것**: `gh` 서브프로세스 · `gh auth switch` 자동 실행 · 팀 워크플로 집합 · 종류별 기본 소요 표 · KST 상수 · `~/actions-runner` 판별 · run 이름의 `· <scope> ·` 문자열로 하던 합류 판정(→ `head_sha` 기준).
- **반드시 지키는 것**: `job_started_at` 우선(run 시각은 큐 대기 포함) · 조회 실패와 빈 큐를 다른 타입으로 · 스텝 `number` 미사용(위치로) · `top` 두 번째 표본만 · 큐·ETA 계산은 **공용 함수 하나**(터미널과 서버가 같은 것을 쓴다 — 두 벌 두면 하나가 조용히 어긋난다) · 워크플로별 이력 보강(전역 목록만 보면 다른 워크플로가 표본을 밀어낸다) · `remote_ci.sh` 의 dispatch 3중 가드(미커밋 → 멈춤 · 미푸시 → 멈춤 · 내 run 은 `head_sha` 로 고른다)와 취소 대신 합류.

---

## 세션 시작 프롬프트 (M0 — 복사해서 붙여 넣기)

```
이 레포(remote_ci_monitor)는 self-hosted GitHub Actions 러너의 큐·요청자·스텝 진행·호스트 자원을
어느 컴퓨터에서든 웹 화면으로 보는 독립 도구다. PLAN.md 가 정본이다 — 먼저 끝까지 읽어라.

이번 세션 목표: M0 를 끝낸다 (PLAN.md 「마일스톤과 완료 기준」의 M0).
  1. PLAN.md 「패키지·모듈 구조」대로 뼈대 + pyproject(런타임 의존성 0) + 설정 로딩(TOML·env·플래그, 우선순위·오류 메시지)
  2. GitHub REST 클라이언트 — 토큰 GITHUB_TOKEN → `gh auth token`, ETag, 페이지네이션, run_attempt 캐시 키,
     primary·secondary rate limit 가드, ApiError. 실패를 []로 돌려주지 마라.
  3. 순수 계산(naming · membership · queue · progress · hostparse · status · render_text) + 픽스처 테스트.
     「진행 규칙」 표의 테스트 6개는 그 이름 그대로 만들어라. membership 은 unknown 상태까지.
  4. `rcm top`(직접 조회 · --server) · `rcm serve`(/api/status · /api/health + hardening 목록 전부) · `rcm check`.
     전부 같은 StatusModel 에서. 스키마는 pools[] 한 개.
  5. CI: unit(matrix) · secrets(gitleaks v3) · 집계 잡 `test`(둘 다 success 아니면 exit 1). `test` 이름은 main 룰셋
     필수 체크라 바꾸지 마라.
  6. scripts/mutcheck.py 3종을 만들고 셋 다 빨개지는 걸 확인한 뒤에만 「검증됨」이라고 써라.

지킬 것:
  - 「반드시 지킬 것 — 이식성」 위반 금지. 특정 머신·계정·팀 규약을 코드에 박지 말고 설정으로 받아라.
  - 「fail-open 금지」. 실패와 빈 값을 다른 타입으로 돌려주고, JSON 은 null + *_error 로 갈라라.
    큐 소속을 모르는 run 은 unknown 으로 남기고, 레인 수를 모르면 assumed 를 띄워라.
  - 순수 계층(core/)은 I/O 도 시계도 안 본다. now 는 인자다.
  - 결정 항목은 PLAN.md 「결정 항목」의 확정값대로. 벗어나야 할 이유가 생기면 그때 물어라.
  - 브랜치 정책: dev 에서 feat/<topic> 을 파서 작업하고 dev 로 PR. main·dev 직접 push 금지(룰셋이 막는다).
  - 식별자·UI 문자열·README·CLI 도움말은 영어, 주석·docstring 은 한국어. 커밋 메시지는 Conventional Commits.

끝나면: 무엇을 만들었는지, 테스트가 몇 개이고 어떤 뮤테이션으로 확인했는지, 실제 레포에서 rcm top 이
무엇을 보여줬는지, M1(세션 명령 rcm run/eta/wait)에서 먼저 정해야 할 것이 무엇인지 짧게 보고해라.
```
