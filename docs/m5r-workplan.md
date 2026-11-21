# M5r 작업 명세: CI 를 어디서 돌릴지 설정으로 고른다

> 2026-11-20 오너 요청 요지: 「rcm 레포가 GitHub Actions 만 쓰는 건 너무하다. 애초에 설정에서 CI 위치를 고를 수 있게
> 하자.」 오너가 고른 답은 셋이다.
>
> 1. rcm 의 제품 기능으로 만든다. 어느 레포든 설정 파일 하나로 GitHub, rcm, 둘 다 가운데 하나를 고른다.
> 2. rcm 레포의 기본값은 「rcm 우선」이다. 모든 검사를 Mac mini 의 rcm 에서 돌리고 GitHub 은 rcm 이 응답하지 않을
>    때만 대신 돌린다.
> 3. rcm 레포의 검사는 dolomood 게이트보다 우선순위가 낮다. 낮은 우선순위와 별도 동시성 그룹으로 돌고, 오래
>    기다리면 GitHub 이 대신한다.
>
> 결정 번호는 175 부터다. M5q 가 149~174 를 쓴다(`docs/m5q-workplan.md` §11). 오너가 2026-11-20 에 다섯 결정(175 ·
> 182 · 186 · 187 · 188)을 확정했다. 대신 도는 문턱은 30분이고, 릴리스 PR 도 rcm 에서만 돌며, `rcm-ci` 는 `mac2`
> 워커에서 돈다. 같은 날 오너가 결정 194(주 1회 GitHub 전체 실행)와 195(대체는 ubuntu 갈래만)를 더했다. 나머지는
> 추천값으로 구현하고 확인을 기다린다. §10 표의 「오너 확인」 열이 어느 쪽인지 말한다.
>
> 이 판은 격리 설계 반박(P0 5 · P1 7 · P2 12)을 반영한 셋째 판이다. 무엇이 왜 바뀌었는지는 §12 에 있다.
>
> 같은 날 오너가 R15 를 정했다. 「rcm ci 를 로컬로 돌리면 github ci 는 왜 해? 하나만 하면 그걸 체크하면 되잖아」.
> 그래서 rcm 레포는 GitHub 대체를 쓰지 않는다. 필수 체크는 커밋 상태 `rcm/ci` 하나이고 감시 스크립트가 push 마다
> `rcm-ci` 를 낸다. 이 레포의 운영 형태는 §13(결정 196~200)이 정본이다. §3 · §7 · §9 가운데 대체에 기대는 부분은
> 이 레포에는 해당하지 않는다. 그 설계는 대체를 고르는 다른 레포를 위해 스킬에 남는다.

## 0. 한 줄

레포에 둔 `.rcm/ci.toml` 한 파일로 검사를 GitHub Actions 에서 돌릴지, 빌드 머신의 rcm 에서 돌릴지, 둘 다에서 돌릴지
고른다. rcm 에서 돌리면 GitHub 의 필수 체크 `test` 는 가벼운 대기 잡이 되어 rcm 이 커밋에 남긴 상태 `rcm/ci` 를
그대로 옮긴다. rcm 이 답하지 않거나 너무 오래 기다리게 하면 곧바로 GitHub 러너로 검사를 대신 돌린다. GitHub 과
말하는 코드는 rcm 본체가 아니라 연결 스킬 `rcm-ci-connect` 의 템플릿에 있다. 그래서 결정 30 은 그대로다.

## 1. 실측

### 1.1 GitHub Actions 쪽 (2026-11-20, `gh run list` 와 잡 API)

이 레포는 public 이다. 표준 GitHub 호스트 러너는 public 레포에서 무료라서 돈이 이유는 아니다. 이유는 시간과
러너를 못 받는 일이다.

`ci.yml` 실행 시간이다. 「최근 30회」는 2026-11-19 14:50Z 부터 22:55Z 까지 8시간이고 성공 18 · 실패 10 · 진행 중
2 다. 「최근 200회」는 2026-09-17 부터다. 실행은 몰려서 온다. 200회 가운데 123회가 11-17 이후다. 다시 돌린 잡은 시도마다
따로 셌다.

| 잡 | 최근 30회 중앙값 · p90 | 최근 200회 중앙값 · p90 |
|---|---|---|
| `unit (ubuntu-latest, 3.11)` | 20.6분 · 33.9분 | 14.5분 · 20.4분 |
| `unit (ubuntu-latest, 3.13)` | 19.9분 · 33.7분 | 14.5분 · 19.4분 |
| `unit (macos-latest, 3.13)` | 31.6분 · 57.2분 | 21.3분 · 31.6분 |
| 그 안의 pytest 단계, ubuntu | 14.6분 · 16.5분 | 11.2분 · 14.9분 |
| 그 안의 pytest 단계, macOS | 21.6분 · 23.7분 | 16.6분 · 21.1분 |
| 그 안의 mutcheck 단계, ubuntu | 7.1분 · 17.3분 | 3.2분 · 4.9분 |
| 그 안의 mutcheck 단계, macOS | 14.3분 · 33.0분 | 4.9분 · 10.3분 |
| `smoke` 두 OS · `secrets` · `test` | 각 1분 아래 | 각 1분 아래 |
| 실행을 만든 때부터 `test` 가 끝날 때까지 | 33.7분 · 57.6분 | 20.4분 · 32.5분 |

macOS 러너를 못 받은 일이 2026-11-17 부터 11-19 사이에 실행 9개, 잡 11개(unit 5 · smoke 6)에서 있었다. 모두 시작
15분 뒤 `cancelled` 로 끝났고 주석이 같다.

```
The job was not acquired by Runner of type hosted even after multiple attempts
Due to capacity constraints, jobs targeting macOS arm64 runners may experience …
```

최근 200회에서 첫 시도가 실패한 실행을 원인별로 나누면 ubuntu unit 실패 29회(그중 2회는 secrets 도), macOS 만 실패
16회(그중 9회가 러너를 못 받음), secrets 만 실패 2회다. 그 가운데 15회는 누군가 다시 돌렸다.

부하도 적는다. 2026-11-17 부터 11-20 08시(KST)까지 나흘 동안 123회가 돌았다(PR 79 · push 44). 날짜별로 16 · 25 · 54 ·
28회다. 러너 시간은 ubuntu 4,934분, macOS 3,753분이다.

룰셋은 이렇게 생겼다(`gh api repos/monocsp/remote_ci_monitor/rulesets/…`).

| 룰셋 | 필수 체크 | 비고 |
|---|---|---|
| `main — PR only, from dev, checks required`(22239723) | `test` 와 `main-from-dev-only`, 둘 다 `integration_id: 15368`(GitHub Actions 앱) | `strict_required_status_checks_policy: false`, 병합 방식은 merge 만 |
| `dev — PR only`(22239732) | `test`, `integration_id: 15368` | 병합 방식은 merge · squash · rebase |

첫 판을 쓸 때 dev 룰셋은 PR 만 요구했다. 같은 날 `test` 가 필수로 더해졌다(설계 반박 P0-3 이 찾았고 `gh api` 로 다시
확인했다). 그래서 rcm 쪽 판정의 잘못이 dev 머지를 바로 막는다(§3.6).

### 1.2 Mac mini 운영 큐 (DB v24 를 `sqlite3 -readonly` 로, 서버 0.7.0)

운영 설정은 읽기만 했다. `lanes = 2`, `cpu_max_percent = 85` 이고 dolomood 프리셋은 `include` 한 파일에 있다.
`[[repos]] name = "rcm"` 이 서버 설정과 `worker.toml` 양쪽에 이미 있다(https 주소). 둘째 워커 `mac2-worker`(풀
`mac2`, 레인 1, 0.7.0)는 같은 Mac mini 에서 돌지만 지난 30일 동안 잡을 하나도 받지 않았다. 그 30일의 잡 1,464개는
전부 서버 자신의 `default` 풀에서 돌았다.

지난 7일(2026-11-13 ~ 11-20 08시)에 잡 281개가 들어왔고 277개가 시작했다. 성공 197 · 실패 64 · 취소 19 이고 실행
시간은 모두 47.3시간이다.

| 프리셋 | 잡 수 | 중앙값 · p90 | 합계 |
|---|---|---|---|
| `gate` | 205 | 9.2분 · 17.5분 | 37.1시간 |
| `scenario-qa` | 5 | 39.4분 · — | 6.7시간 |
| `gate-fast` | 27 | 2.3분 · 3.6분 | 1.1시간 |
| `deploy-dev` | 4 | 12.1분 · — | 0.8시간 |
| 나머지 여섯 | 36 | 중앙값 1~9분 | 1.5시간 |

시작까지 기다린 시간(`created_at` 에서 `started_at` 까지)이다. 우선순위 low 잡은 이 기간에 없다.

| 우선순위 | 잡 수 | 중앙값 · p90 · 최대 |
|---|---|---|
| normal | 246 | 0분 · 4.7분 · 24.3분 |
| high | 31 | 0분 · 2.6분 · 15.3분 |

같은 7일을 1분 단위로 잘라 몇 개가 동시에 돌았는지 셌다. 잡이 0개인 분이 전체 10,080분 가운데 7,736분(77%)이고
1개가 1,615분, 2개가 693분이다. 3개로 센 36분은 끝과 시작이 같은 분에 걸친 칸이다. 낮 시간(09~21시 KST)만 보면
5,040분 가운데 3,468분(69%)이 비어 있다. 레인 둘이 다 찬 시간은 7% 정도다.

이 Mac mini 에 있는 도구도 확인했다. 설치는 건드리지 않았다.

| 도구 | 있음 |
|---|---|
| Python | `/opt/homebrew/bin/python3.11`(3.11.15) · `python3.14`. 3.13 은 없다 |
| Chrome | `/Applications/Google Chrome.app`. `tests/test_web_browser.py` 가 이 경로를 먼저 찾는다 |
| gitleaks | 8.30.1 |
| node | v22.22.3 |

### 1.3 같은 검사를 Mac mini 에서 돌리면 (이 워크트리, 2026-11-20 08시)

운영 rcm 에 잡을 넣지 않고 이 워크트리의 `.venv` 에서 직렬로 쟀다. 재는 동안 load average 는 2 안팎이었다.

| 단계 | 시간 | 비고 |
|---|---|---|
| 새 venv + `pip install -e '.[dev]'`(Python 3.11) | 2.6초 | pip 캐시가 데워진 상태 |
| `ruff check` + `ruff format --check` | 0.1초 | |
| `pytest`(직렬) | 840초(14.0분) | 6,764 통과 · 3 건너뜀. CPU 는 user 239초 + sys 225초로 평균 0.55 코어다. 대부분 기다리는 시간이다 |
| `node --test tests/web/*.test.js` | 0.6초 | 1,033 통과 |
| `python scripts/mutcheck.py` | 400.8초(6.7분) | 변이 100개를 전부 잡았다. 끝의 2분 동안 다른 잡이 돌아 load average 가 25 까지 올랐다 |
| `scripts/smoke_install.sh`(wheel 빌드 포함) | 5.0초 | |
| `gitleaks git .`(전체 이력) | 0.8초 | 걸린 것 없음 |
| 합계 | 약 1,250초(20.8분) | GitHub 의 생성부터 `test` 까지 중앙값은 최근 30회 33.7분, 최근 200회 20.4분이다. GitHub 은 갈래를 나란히 돌리고 여기는 한 줄로 돈다 |

### 1.4 코드와 GitHub 에서 확인한 제약

리드가 준 방향을 코드와 GitHub 에 대 보다가 다음이 걸렸다. 각각 §2 이하에서 다뤘다.

| 제약 | 자리 | 영향 |
|---|---|---|
| main 룰셋의 필수 체크는 GitHub Actions 앱(15368)이 낸 것만 받는다. 「다른 사람이나 다른 통합이 올린 상태로는 병합할 수 없다」(GitHub 문서) | 룰셋 22239723 | rcm 이 `test` 라는 이름으로 상태를 올려도 필수 체크가 차지 않는다. GitHub 워크플로의 잡 `test` 가 직접 판정해야 한다(§2.5) |
| `[[notify]]` 의 `on` 은 종료 상태만 받는다 | `config.py` `parse_notify` | 「돌기 시작했다」는 알림 훅으로 못 올린다. 감시 스크립트가 올린다(§2.6) |
| 알림 env 에 `RCM_SOURCE_SHA` · `RCM_SOURCE_REF` · `RCM_SOURCE_REPO` · `RCM_STATE` · `RCM_EXIT_CODE` · `RCM_URL` 이 이미 있다. `cancelled_by` 는 없다 | `core/notify.py` `notify_env` | 상태를 올리는 데 본체 변경이 필요 없다. 밀려나서 취소된 잡은 훅이 `GET /jobs/{id}` 로 가려낸다(§2.7) |
| 서버 설정 탐색 경로에 `./rcm.toml` 이 있다 | `PLAN.md` 「설정」 | 레포 뿌리의 `rcm.toml` 에 `[ci]` 를 두면 그 폴더에서 `rcm serve` 를 띄울 때 서버 설정으로 읽힌다. `.rcm/ci.toml` 을 쓴다(결정 176) |
| `jobs.<id>.if` 에서는 `matrix` 를 못 쓴다(GitHub 문서의 문맥 표) | 워크플로 문법 | 갈래별로 GitHub 에서 돌지 말지를 matrix 잡의 `if` 로 못 가른다. 무거운 잡을 재사용 워크플로로 옮기고 갈래 목록을 입력으로 넘긴다(§2.5) |
| `GITHUB_TOKEN` 의 REST 한도는 레포당 시간당 1,000 요청이다. 304 응답은 세지 않는다 | GitHub 문서 | 대기 잡은 30초마다 ETag 를 붙여 묻는다(결정 182) |
| supersede 는 git_ref 잡을 `source.ref` 로 묶는다 | `core/supersede.py` `branch_of` | 감시 스크립트가 sha 가 아니라 브랜치 이름으로 내야 같은 브랜치의 옛 잡이 치워진다(결정 184) |
| 운영의 dolomood 게이트 프리셋 이름이 `gate` 다. `gate-status.sh` 훅은 프리셋 이름으로 scope 를 고른다 | 운영 `presets.dolomood.toml` · `~/.config/rcm/scripts/gate-status.sh` | rcm 레포의 프리셋은 `rcm-ci` 로 짓고 새 알림 규칙은 `presets = ["rcm-ci"]` 로 묶는다 |
| launchd 아래에서는 `~/Documents` 를 TCC 가 막는다 | `gate-status.sh` 머리 주석 | 감시 스크립트와 훅은 `~/.config/rcm/scripts/` 에 둔다(결정 192) |
| Mac mini 에 Python 3.13 이 없다 | §1.2 | CI 의 3.13 갈래를 Mac mini 에서 그대로 옮길 수 없다(결정 187) |
| `tests/test_release_files.py` 가 `ci.yml` 의 잡 이름 `unit` · `secrets` · `smoke` · `test` 와 `test` 의 `needs` 를 잠근다 | 시험 | 워크플로를 바꾸는 PR 이 이 시험도 같이 고친다(§9 PR B) |
| dolomood 레포는 private 무료 조직이라 branch protection API 가 403 이다. 병합 판단은 `scripts/pr_ready.py` 가 `local-ci/full` 상태를 읽어서 한다 | dolomood `origin/dev` | dolomood 는 이미 「rcm 만, 대체 없음」으로 돈다(§4) |

## 2. 설계

### 2.1 전체 그림

```
 세션 ── git push ──▶ GitHub ── pull_request / push / schedule ──▶ ci.yml
                        ▲  ▲                              │
                        │  │ 상태 rcm/ci                    ├─ route   head 의 .rcm/ci.toml 을 읽고 모드를 정한다(수 초)
                        │  │                              ├─ rcm     rcm/ci 를 기다린다(가벼운 잡)
                        │  │                              ├─ now     지금 GitHub 에서 돌 갈래(checks.yml)
                        │  │                              ├─ late    rcm 이 답을 못 내면 돌 갈래(checks.yml, rcm 레포는 ubuntu 만)
                        │  │                              └─ test    셋을 모아 판정한다(필수 체크)
 Mac mini              │  │
  launchd ci-watch ────┘  │  열린 PR 과 push 브랜치의 head 를 1분마다 본다 → rcm run rcm-ci --ref <브랜치> --no-wait
                          │  → queued · running 을 올린다
  rcm serve ─ mac2 워커 ─ rcm-ci 잡(PR 의 코드로 ruff · pytest · node · mutcheck · smoke · gitleaks)
       └─ [[notify]] rcm_ci_status.py ──┘  끝나면 passed · failed · unavailable 을 올린다
```

GitHub 은 공유기 안의 Mac mini 에 닿지 못한다. 그래서 방향이 언제나 「Mac mini 가 GitHub 을 부른다」다. GitHub 쪽은
상태를 읽기만 하고, 넘겨받을 때 `taken-over` 한 줄만 쓴다. 주 1회 `schedule` 실행은 rcm 과 상관없이 dev 를 GitHub 의
모든 갈래로 돌린다(결정 194).

### 2.2 설정 파일 `.rcm/ci.toml` (결정 176 · 177 · 195)

레포에 하나만 둔다. GitHub 의 대기 잡과 Mac mini 의 감시 스크립트가 같은 파일을 같은 파서(`rcm_ci_config.py`)로
읽는다. 둘 다 검사하는 커밋, 곧 PR 이면 head 커밋의 파일을 읽는다. GitHub 이 체크아웃하는 merge 커밋의 파일이 아니다
(§2.5). 그래서 어떤 커밋의 검사 위치는 그 커밋에 든 파일이 정한다.

```toml
# .rcm/ci.toml — 이 레포의 검사를 어디서 돌리나 (rcm-ci-connect)
version = 1                 # 필수. 1 만 안다
mode = "rcm"                # "github" | "rcm" | "both". 기본 "github"
fallback = "github"         # "github" | "none". 기본 "github"

[rcm]                       # mode 가 github 가 아니면 필수
preset = "rcm-ci"           # 필수. 감시 스크립트가 rcm run 할 프리셋
context = "rcm/ci"          # 커밋 상태 이름. 기본 "rcm/ci"
priority = "low"            # rcm run --priority 로 넘긴다. 없으면 프리셋의 기본
push = ["dev"]              # 이 브랜치의 push 도 rcm 에서. 기본 [] (push 는 GitHub)
creators = ["monocsp"]      # 이 계정이 올린 rcm/ci 만 믿는다. 기본 [] (아무나). 신뢰 경계는 §2.9
pickup_seconds = 300        # 이 안에 queued 가 안 오면 rcm 이 응답하지 않는 것이다
queue_seconds = 1800        # queued 로 이만큼 머물면 GitHub 이 대신한다
run_seconds = 3600          # running 으로 이만큼 지나면 GitHub 이 대신한다
poll_seconds = 30           # 대기 잡이 묻는 간격

[platforms]                 # mode = "rcm" 일 때 갈래마다. 기본은 전부 "rcm"
ubuntu = "rcm"              # rcm 에서 돌고, rcm 이 답을 못 내면 GitHub 이 이 갈래를 돌린다
macos = "rcm-only"          # rcm 에서만 돈다. 대체 때도 GitHub 에서 돌지 않는다
# "github" 이면 언제나 GitHub 에서 돈다
```

`[platforms]` 의 키는 갈래 이름이다. 갈래는 GitHub 워크플로가 OS 마다 나눈 검사 묶음이고 rcm 레포에는
`ubuntu` 와 `macos` 가 있다. 워크플로가 대기 스크립트에 갈래 목록을 넘기고 파서는 그 목록으로 키를 검사한다.
`rcm-only` 는 오너 결정 (a)(2026-11-20)에서 나왔다. macOS 러너를 못 받는 일이 대체 경로까지 막지 않게 한다(결정 195).

검증 규칙이다.

| 항목 | 규칙 | 어기면 |
|---|---|---|
| 모르는 키 | 표마다 허용 목록 | `CiConfigError("unknown key(s) in [rcm]: …")` |
| `version` | 정수 1 | 오류. 2 이상은 「이 템플릿보다 새 설정」이라고 말한다 |
| `mode` · `fallback` · `platforms.*` | 위의 값만 | 오류 |
| `[rcm].preset` | `mode` 가 github 가 아니면 필수, rcm 프리셋 이름 규칙(`[A-Za-z0-9][A-Za-z0-9_.-]*`, 64자) | 오류 |
| `[rcm].context` | `[A-Za-z0-9][A-Za-z0-9_./-]*`, 100자 | 오류 |
| `[rcm].priority` | `low` · `normal` · `high` | 오류 |
| 초 값 | 정수. `pickup` 60~3600, `queue` 60~86400, `run` 60~86400, `poll` 10~300 | 오류 |
| `platforms` 의 키 | 워크플로가 넘긴 갈래 이름(`--legs ubuntu,macos`) 안에 있어야 한다 | 오류. 빠진 갈래는 `"rcm"` |
| `mode = "rcm"` 이고 `fallback = "github"` | `"rcm"` 갈래가 하나 이상 있어야 한다 | 오류. 대신 돌 갈래가 없으면 `fallback = "none"` 이라고 적으라고 말한다 |
| `push[]` | git ref 이름 규칙(`core/gitref.py` 와 같은 보수적 부분집합) | 오류 |

파일이 없으면 `mode = "github"` 다. 오늘과 똑같이 돈다. 앞 판에 있던 기준 브랜치별 덮어쓰기(`[[branches]]`)는 쓰는 곳이
없어 뺐다(§8).

### 2.3 모드를 정하는 순서 (결정 178)

대기 잡의 `route` 와 감시 스크립트가 같은 함수 `resolve()` 를 부른다. 위에서부터 처음 걸리는 것이 이긴다.

| 순서 | 조건 | 모드 | 이유 |
|---|---|---|---|
| 1 | `schedule` 이벤트 | `github`, 모든 갈래 | 주 1회 GitHub 전체 실행이다(결정 194). 감시는 이 이벤트를 보지 않는다 |
| 2 | 포크에서 온 PR(head 레포 ≠ base 레포), head 레포가 비어 있음(지워진 포크), PR 을 연 계정이 `dependabot[bot]` | `github` | 남의 코드를 빌드 머신에서 돌리지 않는다. 이 셋은 `GITHUB_TOKEN` 이 읽기 전용이라 `taken-over` 도 못 쓴다(쓰면 403) |
| 3 | 저장소 변수 `RCM_CI_MODE` 가 `github` · `rcm` · `both` | 그 값 | 커밋 없이 전부를 한 번에 돌린다. 운영 rcm 이 고장 났을 때 쓰는 손잡이다(§3.6) |
| 4 | 파일이 없다 | `github` | 오늘의 동작 |
| 5 | 파일이 틀렸다 | `github` + 경고 주석 | 검사를 빼먹는 쪽으로 넘어지지 않는다. 전부 GitHub 에서 돈다 |
| 6 | push 이벤트이고 브랜치가 `[rcm].push` 에 없다 | `github` | |
| 7 | 그 밖 | 파일의 `mode` | |

저장소 변수가 없으면(API 404) 덮어쓰기도 없다. 모르는 값이면 무시하고 경고 주석을 남긴다. 이 변수는 오너가
바꾼다. 급할 때는 세션도 바꾸되, 바꾼 사실과 이유를 그 일의 PR 댓글로 남긴다.

`resolve()` 는 `Route(mode, why, now, late, wait, fallback, context)` 를 돌려준다. `now` 는 지금 GitHub 에서 돌
갈래, `late` 는 rcm 이 답을 못 낼 때 GitHub 에서 돌 갈래다.

| 모드 | `wait` | `now` | `late` |
|---|---|---|---|
| `github` | false | 모든 갈래(`rcm-only` 도) | 없음 |
| `rcm`, fallback `github` | true | `platforms` 가 `github` 인 갈래 | `platforms` 가 `rcm` 인 갈래 |
| `rcm`, fallback `none` | true | `platforms` 가 `github` 인 갈래 | 없음 |
| `both` | true | 모든 갈래 | 없음 |

`github` 모드는 `rcm-only` 갈래도 GitHub 에서 돌린다. 그래서 저장소 변수로 `github` 를 걸면 그동안은 macOS 러너를 다시
기다린다.

### 2.4 커밋 상태 계약 `rcm/ci` (결정 179 · 180)

상태 이름은 설정의 `context` 다. 기본 `rcm/ci`. 설명(description)의 첫 낱말이 단계를 말한다. 형식은
`<낱말> · #<잡 번호> · <짧은 문장>` 이고 140자에서 자른다. 잡 번호가 없으면 가운데 칸을 뺀다.

`target_url` 은 기본으로 비운다. rcm 잡 링크는 공유기 밖에서 열리지 않고, public 레포의 커밋 상태에 내부 호스트 이름과
포트를 드러낸다. 잡 번호는 설명에 있다. 링크가 필요한 private 레포는 감시 설정 `job_url = true` 로 켠다. 켜면 링크는
서버 화면의 잡 링크 `<server>/#/jobs/<N>` 이다. `/jobs/<N>` 은 JSON 이라 사람이 열 링크가 아니다. 훅이 싣는 `RCM_URL` 도 같은 꼴이다.

| state | 첫 낱말 | 누가 올리나 | 뜻 | 대기 잡의 판단 |
|---|---|---|---|---|
| `pending` | `queued` | 감시 | rcm 큐에 들어갔다 | `queue_seconds` 까지 기다린다 |
| `pending` | `running` | 감시 | 레인이 집었다 | `run_seconds` 까지 기다린다 |
| `success` | `passed` | 훅(훅이 놓치면 감시) | `succeeded` | 통과 |
| `failure` | `failed` | 훅(훅이 놓치면 감시) | `failed` 이고 종료 코드가 있으며 75 가 아니다 | 실패. 첫 실행에서는 대신 돌리지 않는다 |
| `error` | `unavailable` | 감시 · 훅 | rcm 이 판정을 못 냈다. 제출 실패, 서버 없음, 워커 다운, 큐 정지, `lost`, `timed_out`, 준비 실패(종료 코드 없는 `failed`, 도구 없음 포함), 환경 탓 실패(종료 코드 75), 사람이 한 취소 | 바로 대신 돈다 |
| `error` | `superseded` | 훅 | 같은 브랜치의 새 커밋이 이 잡을 치웠다 | 바로 대신 돈다. 보통은 GitHub 의 `concurrency` 가 이 대기 잡을 먼저 취소한다 |
| `error` | `taken-over` | 대기 잡 | GitHub 이 넘겨받았다 | 다시 돌린 실행이 이것을 보면 바로 대신 돈다 |

판단의 원칙은 셋이다.

첫째, `failure` 는 코드에 대한 판정이라 첫 실행에서는 대신 돌리지 않는다. 대신 GitHub 의 「Re-run」 은 사람의
탈출구로 남긴다. 다시 돌린 실행은 rcm 의 판정을 보지 않고 GitHub 의 대체 갈래로 판정한다(§2.5 1단계). rcm 레포에서
그 갈래는 ubuntu 뿐이라서 macOS 에서만 깨지는 변경은 다시 돌리기로 통과할 수 있다. 이것은 사람이 「rcm 의 빨강이 틀렸다」
고 판단했을 때 쓰라고 둔 길이다. rcm 에서 다시 판정받고 싶으면 새 커밋을 push 한다. 빈 커밋도 된다.

둘째, rcm 이 판정을 못 낸 경우는 전부 `error` 로 모은다. `timed_out` 도 여기에 넣는다. `rcm wait` 가 시간 초과를
1(실패)이 아니라 2 로 돌려주는 것과 같은 생각이다. 빌드 머신이 바빠서 늦은 것을 코드의 잘못으로 부르지 않는다. 그
대가도 적는다. 시험이 멈추는 회귀는 `timed_out` 이 되어 GitHub 으로 넘어간다. 그 회귀가 macOS 에서만 멈춘다면 ubuntu
갈래가 통과시켜 그대로 머지될 수 있다.

셋째, 환경 탓 실패는 실패가 아니다. 디스크가 찼거나, PyPI 에 닿지 못했거나, 도구가 없거나, Chrome 이 뜨지 않거나,
로컬 네트워크 권한이 없어서 깨진 잡을 `failure` 로 부르면 rcm 의 빨강 하나가 dev 머지를 막는다(§3.6). 잡 스크립트는
준비 단계(`isolate` · `venv` · 도구 확인)의 실패를 종료 코드 75(`EX_TEMPFAIL`)로 끝내고, 훅은 그것을 `unavailable · infra`
로 올린다(결정 189). 시험 단계 안에서 환경 때문에 깨진 것은 이 장치로 가려지지 않는다. 그런 빨강이 보이면 §3.6 의
손잡이를 쓴다.

`queued` 와 `running` 의 경과는 GitHub 이 붙인 상태의 `created_at` 으로 잰다. 대기 잡이 늦게 떠도, 다시 돌려도
같은 시계다. `creators` 를 정했으면 그 계정이 올린 상태만 본다. `taken-over` 는 예외로 `github-actions[bot]` 이
올린 것도 본다.

### 2.5 GitHub 쪽: 기다리는 `test` (결정 181 · 182)

룰셋은 그대로 둔다. 필수 체크 `test` 는 지금처럼 `ci.yml` 의 집계 잡이고 GitHub Actions 앱이 낸다. 바뀌는 것은 그
앞에 놓인 잡들이다.

```yaml
# ci.yml (틀). 무거운 잡은 checks.yml 로 옮긴다
on:
  pull_request: { branches: [main, dev] }
  push: { branches: [main, dev] }
  schedule: [{ cron: "0 18 * * 0" }]   # 주 1회(월 03시 KST). 기본 브랜치 main 의 ci.yml 로 돈다
concurrency:
  group: ci-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}

jobs:
  route:            # head sha 를 체크아웃해 .rcm/ci.toml 을 읽는다(merge 커밋이 아니다)
                    # schedule 이면 ref = dev(명시), mode = github, now = 모든 갈래
                    # outputs: mode · wait · now · late · context · sha · ref · why
  rcm:              # needs: route · if: needs.route.outputs.wait == 'true'
                    # permissions: statuses: write · timeout-minutes: 120
                    # outputs: result(pass|fail|fallback) · reason · job. result 가 fail 이면 종료 1
  now:              # needs: route · if: needs.route.outputs.now != ''
    uses: ./.github/workflows/checks.yml
    with: { legs: "${{ needs.route.outputs.now }}", ref: "${{ needs.route.outputs.ref }}" }
  late:             # needs: [route, rcm]
                    # if: !cancelled() && needs.route.outputs.late != '' &&
                    #     (needs.rcm.outputs.result == 'fallback' ||
                    #      (needs.rcm.result != 'success' && needs.rcm.outputs.result == ''))
    uses: ./.github/workflows/checks.yml
    with: { legs: "${{ needs.route.outputs.late }}", ref: "${{ needs.route.outputs.ref }}" }
  test:             # name: test · needs: [route, rcm, now, late] · if: always()
                    # 판정은 rcm_ci_wait.py verdict 가 needs 의 결과 · 출력과 rcm/ci 의 최신 상태로 한다
```

`late` 의 조건은 둘을 받는다. 대기 잡이 `fallback` 을 냈을 때, 그리고 대기 잡이 출력도 없이 죽었을 때다. 대기 잡이
`fail` 을 내고 종료 1 로 끝난 경우는 출력이 있어서 `late` 가 돌지 않는다. `late` 는 `always()` 대신 `!cancelled()` 를
쓴다. 실행 전체가 취소되면 대체도 돌지 않는다. `test` 는 `always()` 그대로다.

`checks.yml` 은 `on: workflow_call` 이고 입력 둘(`legs`: 쉼표로 이은 갈래 이름, `ref`: 체크아웃할 ref)과 출력 하나
(`ran`)를 갖는다.

- 첫 잡 `legs` 가 입력을 검사한다. 비어 있거나 모르는 갈래 이름이 있으면 실패한다.
- 안의 잡마다 자기 갈래를 `if: contains(format(',{0},', inputs.legs), ',ubuntu,')` 처럼 확인한다. `jobs.<id>.if` 는
  `inputs` 는 보고 `matrix` 는 못 보기 때문에 갈래가 다른 matrix 잡은 갈래마다 따로 둔다.
- 마지막 잡 `ran` 은 `if: always()` 로 모든 잡을 기다린 뒤, 안의 잡이 실제로 success 로 끝난 갈래의 목록을 출력한다.
  건너뛴 잡은 호출한 쪽에 success 로 보인다(GitHub 문서). 그래서 호출 잡의 결과만 보고는 「돌아서 통과했다」와
  「아무것도 안 돌았다」를 가르지 못한다. `verdict` 는 `ran` 이 `route` 가 정한 `now` · `late` 와 같은지 본다.

체크 이름은 `now / unit (3.11)` 처럼 호출한 잡 이름이 앞에 붙는다. 이 표시 방식, 빈 matrix 의 동작, 안의 잡이 전부
건너뛰었을 때 호출 잡의 결과는 문서에서 확인하지 못했다. PR B 의 draft 실험으로 잠근다(§7).

`schedule` 은 기본 브랜치(main)의 `ci.yml` 로 돌고 `GITHUB_SHA` 도 main 의 커밋이다. 그래서 `route` 가 `ref = dev` 를
명시해 넘기고 `checks.yml` 이 그 ref 를 체크아웃한다. 잡 정의는 main 의 것이고 코드는 dev 의 것이다. 이 실행은 PR 에
붙지 않아 머지를 막지 않는다. 실패하면 GitHub 이 워크플로 실패 알림을 보내고 요약에 갈래별 결과를 남긴다(결정 194).
`schedule` 이 든 `ci.yml` 이 main 에 들어간 뒤, 곧 다음 릴리스부터 돈다.

대기 잡 `rcm` 의 순서다.

1. `github.run_attempt > 1` 이면 API 를 부르지 않고 바로 `fallback`(사유 `rerun`)이다. §2.4 의 첫째 원칙대로 사람의
   탈출구다.
2. `poll_seconds` 마다 `GET /repos/{repo}/commits/{sha}/statuses` 를 ETag 와 함께 부른다. 응답은 새것부터다. 그
   context 의 가장 새 상태 하나만 본다. sha 는 PR 이면 `github.event.pull_request.head.sha`, push 면 `github.sha` 다.
3. 상태가 없으면 `pickup_seconds` 까지 기다린다. 넘으면 `fallback`(`no_pickup`).
4. `success` 면 `pass`, `failure` 면 `fail` 이다. `error` 면 `fallback` 이고 사유는 첫 낱말이다.
5. `pending` 이면 첫 낱말로 시한을 고른다. 모르는 낱말은 `queued` 로 본다. 시한을 넘으면 `error taken-over · GitHub
   Actions run <id>` 를 올리고 `fallback`(`queued_too_long` · `run_too_long`)이다.
6. API 오류는 다시 묻는다. 오류가 `pickup_seconds` 동안 이어지면 `fallback`(`api_error`)이다.
7. `fallback` 이고 설정이 `fallback = "none"` 이면 결과는 `fail` 이고 사유를 그대로 싣는다.
8. 결과가 `fail` 이면 출력을 쓴 뒤 종료 1 로 끝난다. 그래야 실행 화면에서 이 잡이 빨강으로 보인다.

`verdict` 는 마지막에 `rcm/ci` 의 최신 상태를 한 번 더 읽는다. `run_attempt` 가 1 인데 그 상태가 `failure` 면 다른 조건과
상관없이 실패다. 대기 잡이 시한을 넘겨 `taken-over` 를 올리는 순간과 훅이 `failed` 를 올리는 순간이 겹치는 경우를 막는다.

판정 `test` 의 표다. 「`ran` 일치」는 `checks.yml` 이 낸 `ran` 이 `route` 의 갈래 목록과 같다는 뜻이다. 표에 없는 조합은
실패다.

| 모드 | `rcm` 결과 | GitHub 잡 | `test` |
|---|---|---|---|
| `github`(schedule 포함) | 건너뜀 | `now` success · `ran` 일치 | 통과 |
| `rcm` | `pass` | `now` 가 없거나 success · `ran` 일치 | 통과 |
| `rcm` | `fail`(첫 실행) | 무관 | 실패, 「rcm #N failed」 |
| `rcm` | `fallback` | `late` success · `ran` 일치, `now` 가 없거나 success · `ran` 일치 | 통과, 요약에 대체 사유 |
| `rcm` | 대기 잡이 출력 없이 죽음 | `late` success · `ran` 일치 | 통과, 요약에 「waiter crashed」 |
| `rcm` | `fallback` | `late` success 인데 `ran` 이 비어 있음 | 실패 |
| `both` | `pass` 또는 `fallback` | `now` success · `ran` 일치 | 통과 |
| `both` | `fail` | 무관 | 실패 |
| 아무 모드 | `route` 가 success 가 아님 | 무관 | 실패 |

판정 결과와 사유와 rcm 잡 번호는 `GITHUB_STEP_SUMMARY` 에 표로 남는다. 대기 잡이 GitHub 러너 하나를 최대 2시간
붙잡는다. public 레포라 돈은 들지 않는다. 동시 잡 한도(무료 계정 20개, macOS 5개) 가운데 하나를 쓴다.

대체가 일어나면 `rcm/ci` 는 `error` 로 남아 PR 화면에 빨간 X 가 하나 보이고 `gh pr checks` 는 0 이 아닌 코드로 끝난다.
필수 체크는 `test` 하나다. 그래서 머지 판단은 `gh pr checks --required` 로 해야 한다. 이 조건은 pr 스킬과 AGENTS.md 에
적고, 지금 다른 PR 로 만들고 있는 머지 관문(R14, `merge_pr.py`)에 넘긴다(§9 B).

### 2.6 rcm 쪽: 감시 스크립트가 잡을 낸다 (결정 183 · 184 · 192)

트리거는 셋 가운데 (c) 둘 다를 고른다. 기본은 (b) Mac mini 의 감시 스크립트다. 「설정만으로」라는 요구에 맞는
것은 이쪽이다. 세션은 push 하고 PR 을 열기만 하면 된다. (a) 세션이 직접 `rcm run rcm-ci --ref <브랜치>` 를 쳐도
된다. 같은 프리셋 · 같은 입력 · 같은 sha 라 감시 스크립트의 제출이 그 잡에 합류한다. 두 번 돌지 않는다.

`rcm_ci_watch.py` 는 launchd 가 60초마다 한 번 부르는 짧은 프로세스다. 스크립트와 설정은 `~/.config/rcm/scripts/` 에
둔다. launchd 아래에서는 `~/Documents` 가 막히기 때문이다.

멈추지 않게 하는 장치를 먼저 적는다.

- GitHub HTTP 요청은 15초, `rcm` 하위 프로세스는 60초에서 끊는다. 끊긴 요청은 그 바퀴의 그 일만 건너뛴다.
- 겹쳐 뜨면 뒤에 뜬 것은 잠금을 못 얻고 조용히 끝난다. 잠금이 10분 넘게 잡혀 있으면 앞 프로세스가 멈춘 것으로 본다.
  로그에 `lock stale` 을 크게 남기고 잠금을 빼앗는다.
- 한 바퀴 전체도 50초에서 끊는다. 다음 바퀴가 이어서 한다.

한 바퀴에 하는 일이다.

1. 저장소 변수 `RCM_CI_MODE` 를 읽는다(5분 캐시). 404 면 덮어쓰기가 없다.
2. 후보를 모은다. 열린 PR 가운데 head 레포가 base 레포와 같고, head 레포가 비어 있지 않고, 연 계정이
   `dependabot[bot]` 이 아닌 것의 head(sha · 브랜치 · base · PR 번호)다. 그리고 `ci-watch.toml` 의 `branches` 에 적은
   브랜치의 head 가운데 그 커밋의 `.rcm/ci.toml` 이 `[rcm].push` 에 그 브랜치를 넣은 것이다. 목록 조회는 ETag 를 붙인다.
3. 이미 다룬 sha 는 건너뛴다. 처음 보는 sha 는 그 커밋의 `.rcm/ci.toml` 을 contents API 로 읽어 `resolve()` 한다(sha 별
   캐시). 읽지 못하면(파일 오류가 아니라 API 오류) `error unavailable · watcher_config` 를 바로 올리고 다룬 것으로 적는다.
   GitHub 이 곧바로 대신 돈다. 파일이 틀린 경우는 `resolve()` 가 `github` 로 정하므로 아무것도 올리지 않는다.
   `wait` 가 아니면 「github 로 다룸」으로 적고 끝낸다.
4. 그 sha 에 이미 그 context 의 상태가 있으면 다른 누가 이미 다뤘다. 적고 끝낸다.
5. 낸다. 아래 명령의 JSON 한 줄에서 `job_id` · `sha` · `joined` 를 읽는다.

   ```sh
   rcm run <preset> --source git_ref --ref <브랜치> --no-wait --by ci-watch@<host> \
     [--priority <priority>] [--link <PR 주소> --link-label "PR #<번호>"]
   ```

   서버가 정한 sha 가 본 sha 와 다르면 그사이에 새 커밋이 왔다. 상태는 서버가 정한 sha 에 올린다. 본 sha 의 대기
   잡은 GitHub 의 `concurrency` 가 취소한다.
6. `pending queued · #N · <순번과 ETA>` 를 올린다. 제출이 실패하면 `error unavailable · <사유>` 를 본 sha 에 올린다.
   사유는 종료 코드와 stderr 첫 줄에서 고른 코드다(`server_unreachable` · `unknown_preset` · `ref_not_found` 등).
   사유가 `server_unreachable` 이거나 `rcm` 이 시간을 넘겼으면 error 대신 세 바퀴까지 다시 낸다(§13.8).
7. 다룬 잡을 따라간다. `GET /jobs/{id}`(토큰이 필요 없다)로 상태를 본다.
   - `running` 이 되면 `pending running · #N` 을 한 번 올린다.
   - `reason` 이 `worker_down` 이나 `paused` 로 이어지면 각각 120초 · 300초 뒤 `error unavailable · <reason>` 을 올리고
     잡을 취소한다. 큐 정지는 사람이 건 것이라 조금 더 기다린다.
   - 종료 상태인데 상태가 120초 넘게 `pending` 이면 훅이 놓쳤다. 같은 대응표(§2.7)로 직접 올린다.
   - 가장 새 상태가 `taken-over` 이고 잡이 아직 안 끝났으면 `rcm cancel N` 한다. 결과가 버려질 잡에 레인을 쓰지
     않는다.
8. 14일 지난 기록은 지운다.

PR 은 head 를 검사한다. GitHub Actions 의 `pull_request` 는 base 와 합친 merge 커밋을 검사하지만 그 sha 는 base 가
움직일 때마다 바뀌고 커밋 상태는 head sha 에 붙는다. 합친 결과는 dev 에 머지된 뒤의 push 검사가 본다
(`push = ["dev"]`). dev 룰셋은 squash 와 rebase 병합도 받으므로 머지 뒤 dev 의 head 는 PR head 와 다른 커밋일 수 있다.
그 커밋도 dev push 검사가 rcm 에서 본다. 다만 이 결과는 이미 머지된 뒤라 아무것도 막지 않는다. 이 차이는
`strict_required_status_checks_policy: false` 인 오늘의 룰셋에서 이미 받아들인 것과 같다.

`ci-watch.toml` 은 빌드 머신의 파일이다. 레포에 넣지 않는다.

```toml
rcm = "~/.local/share/rcm-client-venv/bin/rcm"   # 감시가 부를 rcm 클라이언트
server = "http://127.0.0.1:8787"
rcm_token_file = "~/.config/rcm/tokens/ci-watch.rcm"   # rcm 클라이언트 토큰(이름 ci-watch, admin 아님)
state_dir = "~/.local/state/rcm-ci-watch"
log_file = "~/Library/Logs/rcm/ci-watch.log"
job_url = false                               # true 면 rcm 잡 링크를 target_url 에 싣는다(§2.4)

[[repos]]
github = "monocsp/remote_ci_monitor"
github_token_file = "~/.config/rcm/tokens/gh-monocsp-ci.token"
branches = ["dev"]                            # push head 를 볼 브랜치
```

GitHub API 사용량은 한 바퀴에 목록 두세 번과 새 sha 마다 두 번 정도다. 개인 토큰 한도(시간당 5,000)의 몇 퍼센트다.

### 2.7 rcm 쪽: 알림 훅이 결과를 올린다 (결정 185)

`rcm_ci_status.py` 는 서버 설정의 `[[notify]]` 규칙 하나로 붙는다. 종료 상태에서만 돈다.

```toml
[[notify]]
name = "rcm-ci-status"
presets = ["rcm-ci"]
argv = ["/opt/homebrew/bin/python3", "/Users/<user>/.config/rcm/scripts/rcm_ci_status.py",
        "--github", "monocsp/remote_ci_monitor", "--context", "rcm/ci",
        "--token-file", "/Users/<user>/.config/rcm/tokens/gh-monocsp-ci.token",
        "--server", "http://127.0.0.1:8787"]
timeout_seconds = 60
```

대응표는 `rcm_ci_config.terminal_status()` 하나에 있고 감시 스크립트도 같은 함수를 쓴다. 설명의 「단계」는
`RCM_FAILED_STEP` 이 있으면 그것, 없으면 `RCM_LAST_STEP` 이다(두 값 모두 알림 env 에 이미 있다).

| 잡 | 조건 | 올리는 상태 |
|---|---|---|
| `RCM_SOURCE_MODE` 가 `git_ref` 가 아니거나 sha 가 빔 | | 아무것도 안 한다. 로그 한 줄 |
| 가장 새 상태가 `taken-over` | | 아무것도 안 한다. GitHub 이 넘겨받았다 |
| `succeeded` | | `success passed · #N · <요약>` |
| `failed` | 종료 코드 75 | `error unavailable · #N · infra at <단계>` |
| `failed` | 그 밖의 종료 코드 | `failure failed · #N · <단계나 요약>` |
| `failed` | 종료 코드 없음(준비 실패, `tool_missing` 포함) | `error unavailable · #N · could not prepare` |
| `timed_out` | | `error unavailable · #N · timed out` |
| `lost` | | `error unavailable · #N · lost` |
| `cancelled` | `GET /jobs/{id}` 의 `cancelled_by` 가 `superseded` | `error superseded · #N · by #M` |
| `cancelled` | 그 밖 | `error unavailable · #N · cancelled` |

상태를 못 올리면 종료 코드 1 로 끝난다. 그러면 서버의 `notify_failures` 에 잡히고 감시 스크립트가 120초 뒤에 같은
일을 다시 한다. GitHub 요청은 15초에서 끊는다. 토큰은 파일에서 읽어 요청 헤더에만 싣는다. 로그와 오류 문장에는 싣지
않는다. `target_url` 은 감시 설정과 같은 규칙으로 기본은 비운다.

### 2.8 결정 30 과의 경계: 본체와 스킬 (결정 175)

GitHub API 를 부르는 코드(상태 읽기 · 쓰기 · PR 목록 · 저장소 변수)는 전부 스킬 템플릿에 있다. 본체는 GitHub 을
모른다. 본체에 필요한 것은 이미 다 있다.

| 필요한 것 | 본체에 이미 있는 것 |
|---|---|
| 커밋을 지정해 검사 | `rcm run --source git_ref --ref`(M3) |
| 기다리지 않고 내고 sha 받기 | `--no-wait` 의 JSON 에 `job_id` · `ref` · `sha`(`cli.py` `_no_wait_json`) |
| 낮은 우선순위 · 하나씩 · 옛 잡 치우기 | 프리셋 `priority` · `concurrency_group` · `supersede`(M5a · M5o) |
| 잡 옆의 PR 링크 | `--link` · `--link-label` |
| 결과 알림과 커밋 신원 | `[[notify]]` 와 `RCM_SOURCE_*`(M5a · M5 실배치) |
| 잡 상태와 취소 사유 | `GET /jobs/{id}` 의 `state` · `reason` · `cancelled_by` |
| 다른 워커 · 풀 | 프리셋 `pool`(M5b) |
| 스킬을 프로젝트에 깔기 | `rcm skills install`(clientwheel 이 skills 를 싣는다, M5q A0) |

그래서 이 마일스톤은 본체를 고치지 않는다. 구현 중에 본체 변경이 꼭 필요해지면 바로 멈추고 §11 에 남긴 뒤
오너에게 묻는다. 오너가 확인할 질문은 하나다. 「GitHub 과 말하는 코드가 rcm 패키지의 `skills/` 폴더에 실려
배포되는 것도 결정 30 의 『GitHub 은 커밋 · 푸시 · PR 머지에만』 안에 드는가.」 오너는 2026-11-20 에 「든다」로 확정했다. 런타임 경로
(제출 · 큐 · 실행 · 진행 · 결과)는 GitHub 을 부르지 않고, 스킬은 사용자가 고를 때만 프로젝트에 복사된다. 선례는
dolomood 의 `gate-status.sh` 와 `rcm-release-driver` 의 `release_driver.sh` 다. 둘 다 `gh api` 로 커밋 상태를 읽고 쓴다.

### 2.9 보안 (결정 186)

| 위험 | 막는 법 |
|---|---|
| 포크 PR 의 코드가 빌드 머신에서 돈다 | 감시 스크립트가 같은 레포 브랜치의 PR 만 받는다. head 레포가 빈 PR(지워진 포크)과 `dependabot[bot]` 의 PR 도 받지 않는다. `route` 도 이 셋을 `github` 로 정한다. 시험으로 잠근다(C3 · C11 · H4) |
| GitHub 토큰이 샌다 | 범위를 좁힌 fine-grained 토큰 하나를 빌드 머신 파일(0600)에 둔다. 권한은 부록 A.1 이다. 키체인은 launchd 아래에서 못 쓴다. gh 의 활성 계정을 바꾸지 않는다 |
| rcm 토큰이 샌다 | 감시 전용 클라이언트 토큰 `ci-watch` 를 따로 만든다. admin 이 아니다. GitHub 에는 rcm 토큰을 두지 않는다 |
| 누가 `rcm/ci` 를 위조한다 | `creators` 는 「`monocsp` 계정이 올린 상태」만 거른다. 그 계정의 토큰을 쥔 것은 둘이다. 하나는 `gh auth token --user monocsp` 를 쓸 수 있는 모든 세션이다. 다른 하나는 Mac mini 에서 같은 OS 사용자로 도는 모든 프로세스다. 여기에는 rcm 잡 자신, 곧 PR 의 코드가 든다. PR 의 시험 코드가 토큰 파일을 읽어 자기 커밋에 `success` 를 올리는 것을 지금 설계는 막지 못한다. 신뢰 경계는 「이 레포에 push 할 수 있는 사람과 그 사람의 세션」이다. 오늘도 그 사람은 `ci.yml` 을 고쳐 `test` 를 통과시킬 수 있다 |
| PR 의 코드가 빌드 머신 사용자의 파일을 읽는다 | 막지 못한다. rcm 은 샌드박스가 아니다. 오늘 dolomood 게이트와 세션의 `rcm run` 이 이미 같은 권한으로 돈다. 잡 스크립트는 `HOME` · `XDG_*` · `TMPDIR` 를 돌리고 `RCM_*` 환경 변수를 지운다. 이것은 실수를 막는 장치이고 공격을 막는 장치가 아니다 |
| 커밋 상태가 내부 주소를 드러낸다 | `target_url` 을 기본으로 비운다(§2.4) |

경계를 좁히는 후속 후보는 셋이다. 이 마일스톤에서는 하지 않는다. 상태 쓰기만 가진 토큰을 훅 전용으로 떼는 것, 토큰
파일을 훅만 읽게 하는 것(훅을 다른 OS 사용자로 돌린다), `mac2-worker` 를 다른 OS 사용자로 돌려 잡이 토큰 파일에 닿지
못하게 하는 것이다.

## 3. rcm 레포에 적용 (첫 사용처)

### 3.1 설정 값 (결정 187 · 195)

```toml
version = 1
mode = "rcm"
fallback = "github"

[rcm]
preset = "rcm-ci"
context = "rcm/ci"
priority = "low"
push = ["dev"]
creators = ["monocsp"]
pickup_seconds = 300
queue_seconds = 1800
run_seconds = 3600
poll_seconds = 30

[platforms]
ubuntu = "rcm"
macos = "rcm-only"
```

dev 로 가는 PR, dev push, main 으로 가는 릴리스 PR 이 모두 rcm 에서만 돈다(오너 확정 2026-11-20). 릴리스 PR 의 head 는
dev 의 head 와 같은 sha 라서 rcm 쪽은 한 번만 돌고, 릴리스 PR 의 대기 잡은 이미 올라온 결과를 바로 옮긴다. main push 는
`push` 에 없어서 GitHub 에서 돈다. 릴리스 머지 커밋은 어떤 룰셋도 막지 않으므로 그 결과는 기록일 뿐이다.

rcm 이 답을 못 내면 GitHub 은 ubuntu 갈래(3.11 · 3.13 의 `unit`, `smoke`, `secrets`)만 돌린다(오너 결정 (a)). macOS 갈래는
`rcm-only` 라서 대체 때 돌지 않는다. macOS 러너를 못 받는 일이 dev 머지를 막지 않게 하려고 이렇게 정했다. 그 대가로 대체된 커밋은
macOS 를 거치지 않고 머지된다.

앞 판은 릴리스 PR 에 GitHub 을 남기자고 추천했다(`mode = "both"`). 오너는 그 추천을 받지 않았다. 그래서 Mac mini 가
덮지 못하는 Linux 와 Python 3.13 검사가 평소의 PR 에서 사라진다. 이것은 오너가 알고 고른 위험이고 §3.4 에 적었다.

### 3.2 프리셋 `rcm-ci` (결정 188)

운영 서버 설정에 넣는 일은 오너가 한다(§9 C). include 파일 하나로 둔다.

```toml
# ~/.config/rcm/presets.rcm-ci.toml  (server.toml 의 include 에 더한다)
[[presets]]
name = "rcm-ci"
description = "remote_ci_monitor checks: ruff, pytest, node, mutcheck, smoke, gitleaks"
source_modes = ["git_ref"]
repo = "rcm"
pool = "mac2"                         # 오너 확정 2026-11-20
priority = "low"                      # mac2 풀에서는 효과가 없다. 다른 풀로 옮길 때를 위해 둔다
concurrency_group = "rcm-ci"
supersede = "all"
expected_seconds = 1260
timeout_seconds = 3300                # run_seconds(3600)보다 짧게. rcm 이 먼저 timed_out 을 낸다
requires = ["/usr/bin/nice", "git", "node", "gitleaks"]
argv = ["/usr/bin/nice", "-n", "10", "/bin/bash", "scripts/ci/rcm_ci.sh"]
[presets.env]
RCM_CI_PYTHON = "/opt/homebrew/bin/python3.11"
RCM_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

`argv` 는 워크스페이스 안의 스크립트를 부른다. 그래서 검사 단계는 PR 의 코드가 정하고 운영 설정은 프리셋 이름과 풀만
정한다. `RCM_CHROME` 을 주면 Chrome 시험이 건너뛸 수 없는 필수가 된다(결정 79). 지금 ubuntu 갈래가 하는 일과 같다.

`requires` 에 적은 도구가 워커의 PATH 에 없으면 워커는 잡을 시작하기 전에 `tool_missing` 으로 실패시킨다(종료 코드
없음). 훅은 그것을 `unavailable · could not prepare` 로 올리고 GitHub 이 대신 돈다. 도구가 없는 것을 코드의 실패로
부르지 않는다.

`timeout_seconds` 를 대기 잡의 `run_seconds` 보다 짧게 둔 이유가 있다. 앞 판처럼 5400초면 대기 잡이 60분에 넘겨받은 뒤에도
rcm 이 30분을 더 돈다. 3300초면 rcm 이 먼저 `timed_out` 을 내고 훅이 `unavailable` 을 올려 GitHub 이 곧바로 대신 돈다.
대기 잡의 `run_seconds` 는 훅이 놓쳤을 때를 위한 바깥 울타리로 남는다.

`pool` 은 오너가 2026-11-20 에 (B) `mac2` 로 확정했다. 두 선택지를 견준 표는 기록으로 남긴다.

| | (A) 기본 풀 | (B) `mac2` 풀(확정) |
|---|---|---|
| 어디서 | 서버 자신의 레인 2개 가운데 하나 | 같은 Mac mini 의 둘째 워커 `mac2-worker`(레인 1, 지난 30일 잡 0) |
| dolomood 와 레인 | 둘째 레인은 CPU 85% 아래에서만 집는다. 그러나 한 번 집으면 끝날 때까지 레인을 쥔다. 그동안 dolomood 잡이 둘 들어오면 하나는 기다린다 | dolomood 의 레인 둘을 건드리지 않는다 |
| CPU | 부하 게이트가 막아 준다 | 워커의 레인 1 은 게이트를 지나지 않는다(결정 39 · 40). `nice -n 10` 이 CPU 를 양보한다 |
| 기다림 | dolomood 잡 뒤에 선다 | `rcm-ci` 끼리만 선다 |
| 필요한 것 | 없음 | `mac2-worker` 가 살아 있어야 한다. 죽으면 `worker_down` 으로 120초 뒤 GitHub 이 대신한다. `worker.toml` 에 `[[repos]] rcm` 이 이미 있다 |

`mac2` 에서도 dolomood 게이트가 조금 느려질 수 있다. `nice` 는 CPU 만 양보하고 메모리 대역과 디스크는 양보하지 않는다.
macOS 의 `taskpolicy -b` 는 디스크와 네트워크까지 낮은 우선순위로 돌린다. 둘 중 무엇을 쓸지는 실기에서 dolomood 게이트의
시간과 rcm-ci 의 시간을 함께 재고 정한다(§7 10). 반영 뒤 1주 동안 `gate` 의 중앙값을 반영 전 1주(9.2분)와 견준다.
기본 풀의 둘째 레인이 부하 때문에 보류(`held_by_load`)된 시간도 같이 본다.

### 3.3 잡 스크립트 `scripts/ci/rcm_ci.sh` (결정 189)

GitHub 의 `unit` · `smoke` · `secrets` 가 하는 일을 한 잡에서 차례로 한다. 단계마다 `::rcm::step::` 를 찍고 실패한
단계를 `::rcm::fail::` 로 밝힌다. pytest 실패 이름은 `examples/preset/name-failures.sh` 와 같은 방법으로 찍는다(결정 77).
환경 변수 `RCM_CI_STEPS`(쉼표로 이은 단계 이름)가 있으면 그 단계만 돈다. 시험(J)과 손으로 하는 확인에 쓴다.

| 단계 | 하는 일 | 실패하면 | Mac mini 실측(§1.3) |
|---|---|---|---|
| `isolate` | `HOME` · `XDG_CONFIG_HOME` · `XDG_CACHE_HOME` · `XDG_STATE_HOME` · `XDG_DATA_HOME` 를 워크스페이스 아래로. `TMPDIR` 는 짧은 경로(`mktemp -d /tmp/rcmci.XXXXXX`, 끝나면 지운다). `PIP_CACHE_DIR` 은 원래 자리로 고정한다. `RCM_CHROME` · `RCM_CI_PYTHON` · `RCM_CI_STEPS` 밖의 `RCM_*` 를 지운다. 도구(`$RCM_CI_PYTHON` · `node` · `gitleaks` · Chrome) 확인 | 종료 75 | 0초 |
| `venv` | `$RCM_CI_PYTHON -m venv .venv` · `pip install -e '.[dev]'` | 종료 75 | 2.6초 |
| `lint` | `ruff check .` · `ruff format --check .` | 종료 1 | 0.1초 |
| `test` | `pytest` | 종료 1 | 14.0분 |
| `web` | `node --test tests/web/*.test.js` | 종료 1 | 0.6초 |
| `mutcheck` | `python scripts/mutcheck.py` | 종료 1 | 6.7분 |
| `smoke` | `python -m build --wheel` · `PYTHON=$RCM_CI_PYTHON scripts/smoke_install.sh dist/*.whl` | 종료 1 | 5.0초 |
| `secrets` | `gitleaks git --no-banner .` | 종료 1 | 0.8초 |

합계는 20.8분이다. 프리셋의 `expected_seconds` 는 이 값을 넣고 실측 중앙값이 쌓이면 rcm 이 그것을 쓴다.

`smoke_install.sh` 는 `PYTHON` 이 없으면 PATH 의 `python3` 를 쓴다. launchd 아래 워커의 PATH 에서 그것이
`/usr/bin/python3`(3.9)일 수 있어서 3.11 을 명시한다. `TMPDIR` 를 워크스페이스 아래가 아닌 짧은 경로로 두는 이유는
macOS 의 유닉스 소켓 경로가 104바이트로 짧기 때문이다. 워크스페이스 경로는 길어서 그 아래에 소켓을 만드는 시험이 깨질
수 있다. 실기에서 확인한다(§7 3).

`isolate` 가 필요한 이유는 이 시험들이 운영 서버와 같은 사용자, 같은 머신에서 돌기 때문이다. 시험은 이미
`tests/conftest.py` 가 `XDG_*` 를 임시 폴더로 돌린다(M5q). 그래도 한 시험이라도 실수로 `~/.config/rcm` 이나
`~/.local/share/rcm` 을 건드리면 운영이 다친다. 스크립트가 한 번 더 막는다. mDNS 로 서버를 찾는 시험이 운영 8787 을
찾아 말을 걸지 않는지, launchd 아래에서 루프백 멀티캐스트가 되는지는 실기에서 확인한다(§7 3 · 4).

### 3.4 알고 고른 위험: Linux 와 Python 3.13 (결정 187 · 194)

rcm 레포의 검사는 이제 평소에 Linux 와 Python 3.13 을 보지 않는다. 오너가 이 빈틈을 알고 골랐다.

오너는 2026-11-20 에 dev 로 가는 PR 도, dev 에서 main 으로 가는 릴리스 PR 도 rcm 에서만 돌리기로 정했다(결정 187).
앞 판은 main 만 `mode = "both"` 로 두어 GitHub 과 rcm 양쪽에서 돌리자고 추천했다. 그 추천은 버렸다.

Mac mini 에는 Python 3.11.15 와 3.14 만 있고 3.13 은 없다. 그래서 잡은 지원 하한인 3.11 로 돈다. pyproject 의
classifiers 에는 3.11, 3.12, 3.13 이 적혀 있다. 지금 GitHub 갈래가 하던 일을 Mac mini 에서 할 수 있는지 나누면 이렇다.

| GitHub 갈래가 하던 것 | Mac mini 에서 |
|---|---|
| Chrome 필수 시험(`RCM_CHROME`, 결정 79) | 된다. Chrome 앱이 있다. launchd 아래의 워커에서 Chrome 이 뜨는지는 실기에서 본다(§7 3) |
| gitleaks | 된다. `gitleaks git .` 가 전체 이력을 보므로 범위가 더 넓다 |
| 실제 `/proc` 를 읽는 시험 1개(`tests/test_hostsample.py`, Linux 에서만) | 안 된다. 파서는 픽스처 시험이 덮는다 |
| Linux 위의 스모크(Docker 이미지와 같은 OS) | 안 된다 |
| Python 3.13 | 안 된다 |

그래서 Linux 에서만, 또는 3.13 에서만 깨지는 변경이 dev 를 지나 릴리스 PR 까지 갈 수 있다. 릴리스에서 처음 걸리는 곳은
`release.yml` 의 스모크다. 이 잡은 태그를 만든 뒤에 ubuntu 와 macOS 에서 Python 3.13 으로 돈다. 그래서 발견이 늦다.
태그가 이미 서 있고 GitHub Release 가 만들어지지 않은 채로 남는다. 스모크가 잡지 못하는 Linux 쪽 깨짐은 Linux 빌드
머신 사용자와 Docker 이미지 사용자가 먼저 겪는다. 그리고 README 와 classifiers 가 말하는 「Linux 지원 · 3.11~3.13」,
`examples/systemd/` 가 전제하는 Linux 서버는 평소의 검사라는 근거를 잃는다.

설계 안에는 이 빈틈을 일부 메우는 장치가 셋 있다.

첫째는 주 1회 GitHub 전체 실행이다(결정 194, 오너 결정 (b)). 한 주에 한 번 dev 의 head 를 ubuntu 3.11 · 3.13 과 macOS
3.13 으로 돌린다. 실패하면 알림만 오고 머지는 막지 않는다. 깨진 것을 최대 한 주 늦게 안다.

둘째는 대체다. rcm 이 답을 못 내 GitHub 이 대신 돌 때(`late`)는 ubuntu 3.11 · 3.13 이 돈다. 30분 문턱으로 돌린 모의에서는
sha 116개 가운데 5개(약 4%)가 이렇게 넘어갔다. 이 대체로 Linux 와 3.13 을 보는 것은 보장이 아니라 우연이다.

셋째는 저장소 변수 `RCM_CI_MODE` 다. 사람이 손으로 `both` 를 잠깐 걸어 두면 그동안의 실행은 GitHub 에서도 돈다.

오너 확인 없이 넣지 않은 선택지가 다섯 있다. 앞 판의 「주 1회 GitHub 전체 실행」은 오너가 골라 결정 194 가 되었다.

| 선택지 | 메우는 것 | 비용 |
|---|---|---|
| 1. 릴리스 PR 만 GitHub 도. 앞 판의 추천인 기준 브랜치별 `mode = "both"` 다(`[[branches]]` 를 되살려야 한다) | 릴리스마다 Linux · 3.13 을 본다 | 릴리스 PR 마다 GitHub 실행 하나(최근 200회 중앙값 20.4분). macOS 러너를 못 받으면 그 릴리스가 기다린다 |
| 2. ubuntu 갈래만 언제나 GitHub. `[platforms] ubuntu = "github"` 다 | 모든 PR 에서 Linux · 3.13 을 본다 | PR 마다 ubuntu 러너 두 개(중앙값 14.5분). ubuntu 러너는 최근 200회 동안 시작까지 p90 0.1분이었고 못 받은 적이 없다 |
| 3. Mac mini 에 Python 3.13 을 깔고 잡에서 두 버전을 돌린다 | 3.13 만 메운다. Linux 는 메우지 못한다 | 운영 머신의 설치를 바꾼다. 잡 시간이 pytest 한 번(14.0분)만큼 늘어난다 |
| 4. 이 Mac 에 이미 있는 `python3.14` 로 한 번 더 돌린다 | 3.13 은 메우지 못한다. 다음 버전에서 깨질 것을 미리 본다 | 설치는 바꾸지 않는다. 잡 시간이 14.0분 늘어난다. classifiers 에 3.14 가 없어서 무엇을 약속하는지부터 정해야 한다 |
| 5. Linux 워커 풀. Linux 기계나 컨테이너에서 `rcm worker` 를 돌린다 | ubuntu 갈래를 rcm 으로 덮는다 | 지금 운영에 Linux 기계가 없다 |

이 가운데 하나를 넣으려면 오너가 고르고 이 문서 §11 에 남긴다.

### 3.5 dolomood 보다 낮게 (결정 188)

| 장치 | 효과 |
|---|---|
| `pool = "mac2"` | dolomood 의 레인 둘을 쓰지 않는다(§3.2 B). 이 표에서 가장 큰 장치다 |
| `nice -n 10` | 같은 머신의 CPU 를 dolomood 잡에 양보한다. `taskpolicy -b` 는 실기에서 견준다 |
| `concurrency_group = "rcm-ci"` | `rcm-ci` 는 한 번에 하나만 돈다 |
| `supersede = "all"` | 같은 브랜치에 새 커밋이 오면 옛 잡을 치운다 |
| `queue_seconds = 1800` | 30분 넘게 기다리면 GitHub 이 넘겨받는다(오너 확정 2026-11-20) |
| `priority = "low"` | `mac2` 풀에는 `rcm-ci` 만 서므로 지금은 효과가 없다. 기본 풀로 옮기면 dolomood 잡(normal · high)이 먼저 집힌다. 기아 보정은 없다(결정 31) |

지난 GitHub 실행이 들어온 시각을 한 레인 큐에 그대로 흘려 보았다. 같은 sha 는 한 번만 돌고, 같은 브랜치의 옛 잡은
supersede 로 치우고, 문턱보다 오래 기다린 잡은 GitHub 으로 넘긴다고 두었다. 나흘(실제로는 2.9일) 동안 sha 116개가
들어왔다. 확정 문턱은 30분이고, 문턱이 15분인 마지막 줄은 앞 판의 값이라 견주려고 남긴다. 이 모의는 앞 판의
`push = ["dev", "main"]` 과 같은 도착 목록을 썼다. main push 는 몇 번 안 돼서 줄의 숫자가 크게 바뀌지 않는다.

| 문턱 | 한 잡의 시간 | 돈 잡 | 치워진 잡 | GitHub 으로 넘어간 잡 | 기다림 중앙값 · 최대 | 레인이 바쁜 시간 |
|---|---|---|---|---|---|---|
| 30분 | 15분 | 92 | 23 | 1 | 0분 · 28.5분 | 23.9시간 |
| 30분 | 20분 | 81 | 30 | 5 | 5.5분 · 29.4분 | 28.2시간 |
| 30분 | 25분 | 70 | 40 | 6 | 7.4분 · 29.9분 | 32.7시간 |
| 15분 | 20분 | 70 | 25 | 21 | 0분 · 15.0분 | 24.8시간 |

실측 20.8분에 가까운 「30분 · 20분」 줄을 읽는다. sha 116개 가운데 81개가 rcm 에서 돌고 30개가 치워지며 5개(약 4%)가
GitHub 으로 넘어간다. 15분 문턱에서는 21개(18%)였다. 대신 기다림의 중앙값이 0분에서 5.5분으로 늘고 몰리는 때에는
30분 가까이 기다린 뒤에 결과가 나온다. `mac2` 레인은 하루 9.7시간쯤 바쁘다. 이 모의는 대략이다. 도착 시각만 썼고
dolomood 잡과의 CPU 경쟁은 넣지 않았다. 기다림을 줄이려면 한 잡의 시간을 줄여야 하고 그 길은 §8 의 pytest 병렬화다.

### 3.6 운영 rcm 이 고장 나면 (결정 190)

dev 룰셋에도 필수 체크 `test` 가 걸렸다(§1.1). 그래서 rcm 쪽의 잘못된 빨강이나 대체 경로의 실패는 곧 dev 머지 전면
정지다. rcm 레포의 PR 은 운영 rcm 위에서 검사되는데, 운영 rcm 을 고치는 PR 도 rcm 레포의 PR 이다. 경우를 나눈다.

| 고장 | 일어나는 일 |
|---|---|
| 서버 프로세스가 죽었다 | 감시의 `rcm run` 이 실패한다. 감시는 세 바퀴 동안 다시 내고(§13.8) 그래도 안 되면 `unavailable · server_unreachable` 을 올린다. 그때 GitHub 이 대신 돈다 |
| Mac mini 가 꺼졌거나 감시가 죽었다 | 상태가 안 온다. 5분 뒤 GitHub 이 대신 돈다 |
| 서버는 살았는데 `mac2-worker` 가 죽었다 | `worker_down` 이 120초 이어지면 `unavailable` 이고 GitHub 이 대신 돈다 |
| 누가 큐를 정지했다 | `paused` 가 300초 이어지면 `unavailable` 이고 GitHub 이 대신 돈다 |
| 빌드 머신 환경이 깨졌다(디스크 · 도구 · PyPI) | 준비 단계가 종료 75 로 끝나 `unavailable · infra` 가 되고 GitHub 이 대신 돈다 |
| rcm 이 엉뚱한 빨강을 낸다(시험 단계 안에서 환경 탓으로 깨짐) | 대기 잡은 이것을 코드의 실패로 받는다. dev 머지가 멈춘다. 저장소 변수 `RCM_CI_MODE=github` 로 GitHub 에 돌리고 원인을 고친다. 그 PR 하나만 통과시키려면 「Re-run」 으로 GitHub 판정을 받는다(§2.4) |
| rcm 이 엉뚱한 초록을 낸다 | 대기 잡은 이것을 못 가린다. 잡은 PR 의 코드와 PR 의 venv 로 돌고 판정은 pytest 의 종료 코드라서 운영 서버의 버그가 끼어들 자리는 종료 코드 전달뿐이다. 릴리스 PR 도 rcm 에서만 돌므로(결정 187) 이것을 다시 보는 것은 주 1회 GitHub 실행뿐이다(결정 194). 알아챈 뒤에는 저장소 변수로 GitHub 에 돌린다 |
| PR 이 잡 스크립트를 잘못 고쳐 시험을 건너뛴다 | 잡 스크립트는 PR 안에 있어서 PR 이 자기 검사를 바꿀 수 있다. 오늘의 `ci.yml` 도 PR 안에 있으니 신뢰 경계는 같다. 실수는 J 시험(단계 여덟 개와 순서)이 잡는다. J 시험이 그 실수로 함께 빠지면 주 1회 GitHub 실행이 잡는다 |
| 대체 경로 자체가 깨졌다(GitHub 러너 장애 · `checks.yml` 오류) | rcm 이 멀쩡하면 영향이 없다. rcm 도 답을 못 내면 dev 머지가 멈춘다. GitHub 장애는 기다리는 수밖에 없다 |
| 무엇이든 급하다 | 저장소 변수 `RCM_CI_MODE=github` 하나로 다음 실행부터 전부 GitHub 에서 돈다. 감시도 같은 변수를 읽어 잡을 내지 않는다. 오너가 바꾸고, 급할 때는 세션도 바꾸되 PR 댓글로 남긴다 |

운영 rcm 이 위의 처음 다섯 경우처럼 답을 못 내면 그것을 고치는 PR 은 대체 경로로 GitHub 에서 검사된다. 그 PR 이
머지돼도 운영은 바로 바뀌지 않는다. 운영은 릴리스(main)를 거쳐 오너가 반영할 때만 바뀐다.

## 4. dolomood 에 미치는 영향 (결정 191)

dolomood 는 이미 이 기능의 한 꼴로 돈다. 세션이 push 한 뒤 `scripts/remote_ci.sh` 가 `rcm run gate --source git_ref
--ref <브랜치>` 를 내고(트리거 a), 운영의 `gate-status.sh` 훅이 `local-ci/full` 상태를 올리고, `scripts/pr_ready.py` 가
그 상태로 병합을 판단한다. GitHub 호스트 러너는 쓰지 않는다. 이 명세의 말로 하면 `mode = "rcm"`, `fallback =
"none"`, `context = "local-ci/full"`, 트리거 (a) 만이다.

바꾸지 않아도 된다. 바꿀 이유도 지금은 없다. 이 마일스톤이 운영에 더하는 것들은 dolomood 와 겹치지 않게 골랐다.

- 새 프리셋 이름은 `rcm-ci` 다. `gate-status.sh` 의 `case` 문은 모르는 프리셋에 `exit 0` 한다.
- 새 알림 규칙은 `presets = ["rcm-ci"]` 로 묶는다. dolomood 잡에서는 돌지 않는다.
- `rcm-ci` 는 `mac2` 워커에서 돌아 dolomood 의 레인 둘을 쓰지 않는다(§3.2).

dolomood 가 나중에 `rcm-ci-connect` 를 쓰고 싶다면 `.rcm/ci.toml` 에 위 값을 적고 감시 스크립트의 `[[repos]]` 에
레포를 더하면 된다. 그쪽 레포의 변경은 그쪽 세션의 일이다.

## 5. 인터페이스 계약

구현 에이전트가 나란히 짤 수 있게 이름과 모양을 여기서 고정한다. 바꿔야 하면 §11 에 남긴다.

### 5.1 스킬 폴더

```
src/remote_ci_monitor/skills/rcm-ci-connect/
  SKILL.md                      # 묻는 것 · 깔 곳 · 확인 절차
  templates/
    rcm_ci_config.py            # 설정 파서 · resolve · 상태 문장 · 종료 대응표 (표준 라이브러리, 3.11+)
    rcm_ci_wait.py              # GitHub 쪽: route · wait · verdict
    rcm_ci_watch.py             # 빌드 머신: 감시
    rcm_ci_status.py            # 빌드 머신: [[notify]] 훅
    ci.toml                     # .rcm/ci.toml 틀
    ci.yml                      # 워크플로 틀(route · rcm · now · late · test)
    checks.yml                  # 재사용 워크플로 틀(무거운 잡을 옮겨 담는 자리)
    ci-watch.toml               # 감시 설정 틀
    presets.ci.toml             # 프리셋 틀
    rcm_ci_job.sh               # 잡 스크립트 머리(isolate 단계와 마커 함수)
    com.remote-ci-monitor.ci-watch.plist   # launchd 틀(StartInterval 60)
```

세 스크립트는 `rcm_ci_config.py` 를 같은 폴더에서 import 한다. 레포에는 `scripts/ci/` 에, 빌드 머신에는
`~/.config/rcm/scripts/` 에 같은 파일을 복사한다. 레포의 사본이 템플릿과 바이트까지 같은지를 시험이 잠근다
(`examples/server.toml` 과 `templates/server.toml` 을 잠그는 것과 같다).

### 5.2 `rcm_ci_config.py`

```python
SCHEMA_VERSION = 1
MODES = ("github", "rcm", "both")
FALLBACKS = ("github", "none")
WHERE = ("github", "rcm", "rcm-only")
WORDS = ("queued", "running", "passed", "failed", "unavailable", "superseded", "taken-over")
DEFAULT_CONTEXT = "rcm/ci"
DESCRIPTION_LIMIT = 140
CONFIG_PATH = ".rcm/ci.toml"
EX_TEMPFAIL = 75                            # 잡 스크립트의 「환경 탓」 종료 코드
BOT_AUTHORS = ("dependabot[bot]",)

class CiConfigError(Exception): ...         # 문장에 표와 키 이름이 들어간다

@dataclass(frozen=True)
class RcmSide:
    preset: str
    context: str = DEFAULT_CONTEXT
    priority: str | None = None
    push: tuple[str, ...] = ()
    creators: tuple[str, ...] = ()
    pickup_seconds: int = 300
    queue_seconds: int = 1800
    run_seconds: int = 3600
    poll_seconds: int = 30

@dataclass(frozen=True)
class CiConfig:
    mode: str
    fallback: str
    rcm: RcmSide | None
    platforms: dict[str, str]

@dataclass(frozen=True)
class Route:
    mode: str
    why: str        # "schedule" · "fork" · "override" · "no_file" · "bad_file" · "push_not_listed" · "default"
    now: tuple[str, ...]
    late: tuple[str, ...]
    wait: bool
    fallback: str
    context: str | None
    error: str | None = None                # bad_file 일 때 CiConfigError 문장

def parse(text: str, *, legs: Sequence[str]) -> CiConfig
def load(path: Path, *, legs: Sequence[str]) -> CiConfig | None            # 파일 없으면 None
def resolve(cfg: CiConfig | None, *, event: str, base: str, fork: bool,
            override: str | None, legs: Sequence[str], error: str | None = None) -> Route
def is_fork(base_repo: str, head_repo: str | None, author: str | None) -> bool   # null head · 봇 포함
def describe(word: str, job: int | None, text: str) -> str                 # "<word> · #N · text", 140자
def read_word(description: str | None) -> tuple[str | None, int | None]    # 첫 낱말과 잡 번호
def terminal_status(state: str, exit_code: int | None, cancelled_by: str | None,
                    *, job: int, summary: str, step: str | None,
                    successor: int | None) -> tuple[str, str] | None       # (state, description)
```

`resolve()` 의 `event` 는 `"pull_request"` · `"push"` · `"schedule"` 이다. `base` 는 PR 이면 base 브랜치, push 면 그
브랜치, schedule 이면 무시한다. `terminal_status()` 의 `step` 은 실패 스텝, 없으면 마지막 스텝이다.

### 5.3 `rcm_ci_wait.py` (GitHub 러너에서, 표준 라이브러리)

```
python3 scripts/ci/rcm_ci_wait.py route   --legs ubuntu,macos [--schedule-ref dev]
python3 scripts/ci/rcm_ci_wait.py wait
python3 scripts/ci/rcm_ci_wait.py verdict
```

입력은 GitHub 이 주는 환경 변수에서 읽는다. `GITHUB_EVENT_NAME` · `GITHUB_EVENT_PATH`(PR 의 base · head 레포 · head
sha · 연 계정) · `GITHUB_REPOSITORY` · `GITHUB_SHA` · `GITHUB_REF_NAME` · `GITHUB_RUN_ATTEMPT` · `GITHUB_RUN_ID` ·
`GITHUB_TOKEN` · `RCM_CI_MODE`(저장소 변수를 워크플로가 넘긴다) · `GITHUB_OUTPUT` · `GITHUB_STEP_SUMMARY` 다. API
주소는 `GITHUB_API_URL` 을 따른다. 시험은 이 값을 가짜 서버로 돌린다. `route` 를 부르는 단계는 그 전에 head sha 를
체크아웃한다(PR 이면 `github.event.pull_request.head.sha`).

| 하위 명령 | `GITHUB_OUTPUT` 에 쓰는 것 | 종료 코드 |
|---|---|---|
| `route` | `mode` · `wait`(true/false) · `now` · `late`(쉼표로 이은 갈래, 없으면 빈 값) · `context` · `sha` · `ref` · `why` | 0. 설정 오류도 0 이고 `::warning::` 한 줄 |
| `wait` | `result`(pass · fail · fallback) · `reason` · `job` | `fail` 이면 1, 나머지는 0 |
| `verdict` | 없음. `GITHUB_STEP_SUMMARY` 에 표 | 통과 0, 실패 1 |

`verdict` 는 `NEEDS_JSON`(워크플로가 `toJSON(needs)` 를 넘긴다)을 읽는다. `now` · `late` 의 `outputs.ran` 도 거기 있다.
마지막에 `rcm/ci` 의 최신 상태를 한 번 더 읽는다(§2.5). 사유 코드는 `no_pickup` · `queued_too_long` · `run_too_long` ·
`unavailable` · `superseded` · `taken_over` · `rerun` · `api_error` 다.

### 5.4 `rcm_ci_watch.py` (빌드 머신, launchd, 표준 라이브러리)

```
python3 rcm_ci_watch.py --config ~/.config/rcm/ci-watch.toml [--once] [--dry-run] [--check]
```

`--dry-run` 은 GitHub 과 rcm 에 쓰지 않고 할 일을 줄마다 찍는다. `--check` 는 설정 · 토큰 파일 권한(0600) · rcm 클라이언트 ·
서버 health · GitHub 토큰 권한을 확인하고 표를 찍는다. 그 표에는 `.rcm/ci.toml` 이 `push` 에 적었는데 `branches` 에
없는 브랜치가 경고로 나온다. 상태 파일은 `<state_dir>/<owner>__<repo>.json` 이고 sha 마다
`{job, word, posted_at, first_seen}` 를 둔다. 잠금 파일은 같은 폴더의 `<owner>__<repo>.lock` 이고 잡은 시각을 적는다.
시간 제한은 GitHub 요청 15초, `rcm` 하위 프로세스 60초, 한 바퀴 50초, 잠금 빼앗기 10분이다. 로그는 한 줄에 한 사건이고
토큰과 토큰 파일 내용은 찍지 않는다.

### 5.5 `rcm_ci_status.py` (빌드 머신, `[[notify]]`)

인자는 §2.7 의 `argv` 그대로다. 읽는 env 는 `RCM_JOB_ID` · `RCM_STATE` · `RCM_EXIT_CODE` · `RCM_SOURCE_MODE` ·
`RCM_SOURCE_SHA` · `RCM_SUMMARY` · `RCM_FAILED_STEP` · `RCM_LAST_STEP` · `RCM_URL` 이다. `RCM_URL` 은 `--job-url` 을 줄
때만 `target_url` 에 싣는다. 종료 코드는 올렸거나 할 일이 없으면 0, 못 올렸으면 1 이다.

### 5.6 워크플로 틀

`ci.yml` 의 잡 이름은 `route` · `rcm` · `now` · `late` · `test` 다. `test` 는 main · dev 두 룰셋과 묶여 있어 이름을 바꾸지
않는다. `checks.yml` 의 잡은 레포마다 다르다. 처음과 끝은 같다. 첫 잡 `legs` 가 입력을 검사하고 마지막 잡 `ran` 이
`if: always()` 로 실제 success 인 갈래 목록을 `workflow_call` 출력 `ran` 으로 낸다. rcm 레포는 지금의 `unit` · `smoke` ·
`secrets` 를 옮기되 갈래마다 나눈다(`unit` 은 ubuntu 두 버전, `unit-macos` 는 `name: unit` 으로 macOS 하나, `smoke` 와
`smoke-macos`, `secrets`). `secrets` 의 gitleaks 는 `ubuntu` 갈래에 속한다. 모든 잡은 `inputs.ref` 를 체크아웃한다.

## 6. 테스트

시험을 먼저 쓴다. GitHub API 는 `tests/ci_fakes.py` 의 작은 `ThreadingHTTPServer` 가 흉내 낸다. 상태 목록 · 상태
쓰기 · PR 목록 · contents · 저장소 변수(404 포함) · 브랜치 · ETag 와 304 를 다룬다. 이 가짜 서버는 `127.0.0.1` 에만 묶고
요청 로그의 역방향 DNS 조회를 끈다(#235 와 같은 이유). 시계는 주입하고 진짜로 기다리지 않는다. `poll_seconds` 와 시한은
주입한 시계로 흐른다. rcm 쪽은 실제 서버를 임시 `data_dir` 로 띄우거나 `rcm` 을 대신하는 가짜 실행 파일을 쓴다. 템플릿은
`tests/test_skills_templates.py` 처럼 저장소 뿌리에서 부른다.

### C: 설정과 모드 (PR A)

| # | 종류 | 준비 | 기대 |
|---|---|---|---|
| C1 | 베스트 | §3.1 의 파일 · dev 로 가는 PR, 그리고 main 으로 가는 PR | 둘 다 `mode=rcm` · `wait=true` · `now` 빔 · `late=ubuntu` · `why=default` |
| C2 | 베스트 | 같은 파일 · `schedule` 이벤트 · `--schedule-ref dev` | `mode=github` · `now=ubuntu,macos` · `ref=dev` · `why=schedule`. `RCM_CI_MODE=rcm` 이어도 같다 |
| C3 | 보안 | 같은 파일 · 포크 PR | `mode=github` · `why=fork` |
| C4 | 우선순위 | `RCM_CI_MODE=github` · 파일은 rcm | `github` · `why=override` · `now` 에 `macos` 도 있다. 값이 `lates` 면 무시하고 경고 |
| C5 | 베스트 | 파일 없음 | `github` · `why=no_file` |
| C6 | 에러 | 모르는 키 · `version = 2` · `mode = "local"` · `pickup_seconds = 5` · `platforms.windows` · 남은 `[[branches]]` | 여섯 다 `github` · `why=bad_file` · `error` 에 표와 키 이름 |
| C7 | 에러 | `mode = "rcm"` 인데 `[rcm]` 없음 · `fallback = "github"` 인데 갈래가 전부 `rcm-only` | 둘 다 `bad_file`. 둘째의 문장에 `fallback = "none"` 이 들어 있다 |
| C8 | 엣지 | push 이벤트 · 브랜치 `main`(push 목록 밖) | `github` · `why=push_not_listed` |
| C9 | 엣지 | `platforms.ubuntu = "github"` · `macos = "rcm"` · dev PR | `now=ubuntu` · `late=macos` |
| C10 | 엣지 | `fallback = "none"` | `late` 빔 |
| C11 | 보안 | head 레포가 `null` · 연 계정이 `dependabot[bot]` | 둘 다 `github` · `why=fork` |
| C12 | 표시 | `describe("queued", 12, "x"*300)` · 잡 번호 없음 · 제어문자 | 140자, `queued · #12 · …`, 번호 칸 없음, 제어문자 지움. `read_word` 가 되돌린다 |
| C13 | 대응 | `terminal_status` 에 §2.7 표의 열 줄 | 표와 같다. 종료 75 는 `unavailable` 이고 설명에 단계가 있다 |
| C14 | 우선순위 | `RCM_CI_MODE` 가 빈 문자열(저장소 변수가 없을 때 워크플로가 넘기는 값) | 덮어쓰기 없음. 파일의 `mode` · `why=default` |

### W: 대기 잡 (PR A)

| # | 종류 | 가짜 GitHub 의 상태 순서 | 기대 |
|---|---|---|---|
| W1 | 베스트 | 없음 → queued → running → success | `pass` · `job` |
| W2 | 베스트 | queued → failure | `fail`, 종료 1, 대신 돌지 않는다 |
| W3 | 대체 | 끝까지 없음 | `pickup_seconds` 뒤 `fallback no_pickup`, `taken-over` 를 올리지 않는다 |
| W4 | 대체 | queued 가 `queue_seconds` 를 넘김 | `taken-over` 한 번 올리고 `fallback queued_too_long` |
| W5 | 대체 | running 이 `run_seconds` 를 넘김 | `fallback run_too_long` |
| W6 | 대체 | error `unavailable` · error `superseded` | 바로 `fallback`, 사유가 첫 낱말 |
| W7 | 탈출구 | `GITHUB_RUN_ATTEMPT=2` · 이미 failure | API 요청 0, `fallback rerun` |
| W8 | 에러 | 목록이 500 을 계속 냄 | `pickup_seconds` 뒤 `fallback api_error` |
| W9 | 에러 | 500 두 번 뒤 success | `pass` |
| W10 | 보안 | `creators=["monocsp"]` · `mallory` 가 올린 success · 뒤에 `monocsp` 의 failure | `fail`(mallory 의 것은 안 본다) |
| W11 | 엣지 | 같은 sha 에 다른 context 의 success | 무시하고 계속 기다린다 |
| W12 | 엣지 | 모르는 첫 낱말의 pending | queued 로 센다 |
| W13 | 엣지 | 상태가 바뀌지 않음 | 둘째 요청부터 `If-None-Match` 를 싣는다. 가짜 서버가 304 를 센다 |
| W14 | 엣지 | `fallback="none"` 에서 W3 | `fail no_pickup` |
| W15 | 시간 | 경과는 상태의 `created_at` 과 GitHub 응답의 `Date` 헤더로 잰다 | 대기 잡 러너의 시계를 2시간 앞당기거나 늦춰도 결과가 같다 |
| W16 | 엣지 | 첫 요청에서 이미 success(같은 sha 를 다른 실행이 끝냄) | 요청 한 번으로 `pass`. `pickup_seconds` 를 기다리지 않는다 |

### V: 판정 (PR A)

§2.5 판정 표의 아홉 줄을 `NEEDS_JSON` 과 가짜 GitHub 의 최신 상태로 넣는다. 그리고 다음 넷을 더 본다.

| # | 준비 | 기대 |
|---|---|---|
| V1 | `rcm` 이 `fallback`, `late` 가 `skipped` | 실패(대체해야 하는데 안 돌았다) |
| V2 | `rcm` 이 `fallback`, `late` 가 success 인데 `ran` 이 빔 | 실패 |
| V3 | `rcm` 이 `fallback`, `late` success · `ran=ubuntu` 인데 최신 상태가 `failure`, `run_attempt=1` | 실패 |
| V4 | V3 과 같은데 `run_attempt=2` | 통과(탈출구) |
| V5 | `schedule` · `now` 가 success 인데 `ran=ubuntu`(macOS 갈래가 건너뜀) | 실패. 요약에 빠진 갈래 `macos` |

### H: 감시 스크립트 (PR A)

| # | 종류 | 준비 | 기대 |
|---|---|---|---|
| H1 | 베스트 | 같은 레포 PR 하나 · 상태 없음 | `rcm run rcm-ci --source git_ref --ref <브랜치> --no-wait --priority low --link … --link-label "PR #7"` 한 번, `queued · #N` 하나, `target_url` 없음 |
| H2 | 베스트 | H1 다음 바퀴, 잡이 running | `running · #N` 한 번. 셋째 바퀴에는 아무것도 안 올린다 |
| H3 | 베스트 | push 브랜치 dev · 그 커밋의 `push = ["dev"]` | 제출한다. `push` 에 없는 `main` 은 제출하지 않는다 |
| H4 | 보안 | 포크 PR · head 레포 `null` · `dependabot[bot]` 의 PR | 셋 다 제출 0, 상태 0 |
| H5 | 우선순위 | 저장소 변수 `RCM_CI_MODE=github` | 제출 0 |
| H6 | 에러 | `rcm run` 이 종료 2 와 `cannot read presets: cannot reach …`(서버 없음) | 세 바퀴 동안 `pending queued · retrying submit (n/3)`, 넷째 실패에 `unavailable · server_unreachable`(§13.8) |
| H7 | 에러 | `rcm run` 이 종료 2 와 `unknown preset` | `unavailable · unknown_preset` |
| H8 | 엣지 | 이미 그 context 의 상태가 있음 | 제출 0 |
| H9 | 엣지 | 서버가 정한 sha 가 본 sha 와 다름 | 상태는 서버의 sha 에만 |
| H10 | 엣지 | 잡이 `worker_down` 으로 121초 · `paused` 로 301초 | 각각 `unavailable · <reason>` 과 `rcm cancel N` |
| H11 | 엣지 | 가장 새 상태가 `taken-over` · 잡이 queued | `rcm cancel N` |
| H12 | 엣지 | 잡이 끝났는데 상태가 121초째 pending(훅 실패) | 대응표로 직접 올린다 |
| H13 | 엣지 | 두 프로세스가 동시에 뜸 | 하나만 돈다. 다른 하나는 0 으로 끝난다 |
| H14 | 엣지 | 상태 파일이 깨짐 | 옮겨 두고 새로 시작한다. 이미 상태가 있는 sha 는 H8 이 막는다 |
| H15 | 보안 | 토큰 파일이 0644 | `--check` FAIL, 평소 실행은 로그 한 줄과 종료 2 |
| H16 | 로그 | 위의 모든 경우 | 로그와 stderr 에 토큰 문자열이 없다 |
| H17 | 시간 | 가짜 GitHub 이 응답하지 않음 · 가짜 `rcm` 이 멈춤 | 15초 · 60초에서 끊고 그 일만 건너뛴다. 한 바퀴는 50초 안에 끝난다 |
| H18 | 시간 | 잠금 파일이 11분 전에 잡혔다 | `lock stale` 로그와 함께 빼앗고 돈다. 9분 전이면 0 으로 끝난다 |
| H19 | 에러 | contents API 가 500 | `unavailable · watcher_config` 를 바로 올린다. 제출 0 |
| H20 | 엣지 | 저장소 변수 API 가 404 | 덮어쓰기 없음. 파일의 `mode` 대로 낸다 |
| H21 | 엣지 | 세션이 같은 sha 로 `rcm run rcm-ci` 를 이미 냈다(가짜 `rcm` 이 `joined: true` 와 그 잡 번호) | 상태 `queued · #<그 번호>` 하나. 따라가기도 그 번호로 한다 |
| H22 | 에러 | GitHub 이 401 을 낸다(토큰 만료) | 제출 0 · 상태 0. 로그에 `github_auth` 한 줄. 다음 바퀴에도 한 줄이고 늘지 않는다. `--check` 의 GitHub 행 FAIL |
| H23 | 에러 | `rcm run` 이 401(rcm 토큰 폐기) | `unavailable · rcm_auth` |
| H24 | 엣지 | 같은 브랜치의 head 가 sha1 에서 sha2 로 바뀜 | sha2 를 새로 낸다. sha1 은 다시 내지 않고 기록 그대로 둔다 |
| H25 | 엣지 | dev head 가 PR head 와 다른 커밋(squash 병합) | dev head 를 따로 낸다. PR head 의 기록은 건드리지 않는다 |

### N: 알림 훅 (PR A)

가짜 GitHub 이 받은 상태를 본다. N1~N10 은 §2.7 표의 열 줄을 차례로 env 로 넣는다.

| # | 준비 | 기대 |
|---|---|---|
| N1 | `RCM_SOURCE_MODE=tree` · 또는 `RCM_SOURCE_SHA` 빔 | 요청 0, 로그 한 줄 |
| N2 | 가장 새 상태가 `taken-over` | 요청은 목록 조회 하나뿐 |
| N3 | `succeeded` | `success passed · #N` |
| N4 | `failed` · 종료 75 · `RCM_FAILED_STEP` 빔 · `RCM_LAST_STEP=venv` | `error unavailable · #N · infra at venv` |
| N5 | `failed` · 종료 1 · `RCM_FAILED_STEP=test` | `failure failed · #N · test` |
| N6 | `failed` · 종료 코드 빔(`tool_missing`) | `error unavailable · #N · could not prepare` |
| N7 | `timed_out` | `error unavailable · #N · timed out` |
| N8 | `lost` | `error unavailable · #N · lost` |
| N9 | `cancelled` · 가짜 rcm 의 `GET /jobs/{id}` 가 `cancelled_by=superseded` | `error superseded · #N · by #M` |
| N10 | `cancelled` · `cancelled_by=owner` | `error unavailable · #N · cancelled` |
| N11 | GitHub 이 500 | 종료 1 |
| N12 | GitHub 이 401(토큰 만료) | 종료 1. 로그와 stderr 에 토큰 문자열이 없다 |
| N13 | `RCM_FAILED_STEP` 빔 · `RCM_LAST_STEP=test` · 종료 1 | 설명에 `test` |
| N14 | `--job-url` 없음 · 있음 | `target_url` 없음 · `RCM_URL` |
| N15 | GitHub 이 15초 넘게 응답하지 않음 | 끊고 종료 1 |

### Y: 워크플로 (PR B · D)

`tests/test_release_files.py` 의 `ci.yml` 시험을 고치고 더한다. `scripts/ci/` 의 사본은 템플릿과 바이트까지 같아야 한다.

| # | 보는 것 |
|---|---|
| Y1 | `test` 이름 · `if: always()` · `needs` 에 `route` · `rcm` · `now` · `late` · `needs.*.result` 와 `ran` 을 `verdict` 에 넘김 |
| Y2 | `late` 가 `always()` 가 아니라 `!cancelled()` 와 §2.5 의 조건을 쓴다. `now` · `late` 가 `checks.yml` 을 부르고 `legs` · `ref` 를 넘긴다 |
| Y3 | `checks.yml` 이 `workflow_call` 이고 `legs` 잡 · `unit` · `unit-macos` · `smoke` · `smoke-macos` · `secrets` · `ran` 잡과 갈래 `if` 가 있고 `ran` 이 출력이다 |
| Y4 | `checks.yml` 의 `legs` 잡이 모르는 갈래(`ubunut`)와 빈 값에서 실패한다(그 단계의 셸을 시험이 직접 돌린다) |
| Y5 | `route` 가 PR head sha 를 체크아웃한다. `schedule` 트리거와 `--schedule-ref dev` 가 있다 |
| Y6 | `rcm` 잡에 `statuses: write` 와 `timeout-minutes` 가 있다. `concurrency` 가 main push 를 취소하지 않는다 |
| Y7 | `scripts/ci/rcm_ci_config.py` · `rcm_ci_wait.py` 가 템플릿과 바이트까지 같다 |
| Y8 | 레포의 `.rcm/ci.toml` 을 실제 파서로 읽어 dev PR · main PR · dev push · main push · schedule 의 `Route` 를 잠근다. PR D 의 첫 커밋에서 이 시험을 rcm 기대값으로 바꾸면 빨강이고 파일을 고치면 초록이다 |

### K: 배포 (PR A)

| # | 보는 것 |
|---|---|
| K1 | 조립한 클라이언트 wheel 에 `skills/rcm-ci-connect/` 의 파일 전부가 있다. `rcm skills list` 가 설명과 함께 보이고 `install` 이 템플릿을 쓴다 |

### J: 잡 스크립트 (PR B)

시험은 작은 가짜 프로젝트(임시 폴더의 `pyproject.toml` 과 시험 파일 하나)에서 돈다. 가짜 `pytest` · `ruff` · `gitleaks` ·
`node` 를 PATH 앞에 두어 받은 인자를 적고 정해 둔 종료 코드를 낸다. `RCM_CI_STEPS` 로 단계를 고른다. 진짜 pytest 를 다시
돌리지 않는다.

| # | 준비 | 기대 |
|---|---|---|
| J1 | `RCM_SERVER` · `RCM_TOKEN` · `RCM_FOLLOWED` 를 준 채 `RCM_CI_STEPS=isolate` | 세 변수가 없고 `HOME` 과 `XDG_*` 가 워크스페이스 아래, `TMPDIR` 가 `/tmp/rcmci.` 로 시작하고 끝나면 지워진다 |
| J2 | `bash -n` · 실행 비트 · `set -euo pipefail` | 통과 |
| J3 | 가짜 `pytest` 가 실패 이름 하나와 종료 1 | `::rcm::fail::test` 와 그 이름, 종료 1 |
| J4 | 마커 | `::rcm::steps::8` 과 단계 이름 여덟 개가 §3.3 표의 순서다 |
| J5 | PATH 에 `gitleaks` 가 없음 · `RCM_CI_PYTHON` 이 없는 경로 · `pip install` 이 실패 | 셋 다 종료 75 와 `::rcm::fail::isolate` 또는 `venv` |
| J6 | `RCM_CI_STEPS=smoke` | 가짜 `smoke_install.sh` 가 받은 `PYTHON` 이 `$RCM_CI_PYTHON` 이다 |

### mutcheck 에 더할 변이

| 변이 | 잡는 시험 |
|---|---|
| `resolve` 의 포크 검사를 지운다 | C3 |
| 설정 오류를 `github` 대신 파일의 `mode` 로 둔다 | C6 |
| `rcm-only` 갈래를 `late` 에 넣는다 | C1 |
| 대기 잡이 `failure` 를 `fallback` 으로 읽는다 | W2 |
| `creators` 거르기를 지운다 | W10 |
| `verdict` 가 `ran` 을 보지 않는다 | V2 |
| 훅이 `taken-over` 를 덮는다 | N |
| 훅이 종료 75 를 `failure` 로 올린다 | C13 · N |
| 감시 스크립트가 포크 PR 을 받는다 | H4 |

## 7. 실기 확인 절차

운영 설정(`~/.config/rcm/server.toml`), 운영 데이터(`~/.local/share/rcm`), 포트 8787 은 쓰지 않는다. 다른 세션이
8788 · 8790 · 8791 을 쓴 적이 있어 8793 을 쓴다. 시험 서버의 `data_dir` 은 `/tmp` 아래에 두지 않는다.

| 구성 | 값 |
|---|---|
| 시험 서버 | PR B 빌드의 venv, 포트 8793, `data_dir = ~/.local/share/rcm-devtest-m5r/data`, `advertise = false`, `lanes = 1`, `[[repos]] rcm`(https), 프리셋 `rcm-ci`(풀 기본), 알림 규칙 `rcm-ci-status` |
| 감시 | `~/.local/share/rcm-devtest-m5r/ci-watch.toml`(서버 8793, 상태 폴더도 그 아래). launchd 없이 `--once` 로 손으로 돌린다 |
| GitHub | PR B 의 draft PR. 실험 커밋에서만 `.rcm/ci.toml` 을 `mode = "rcm"` · `queue_seconds = 120` 으로 둔다. 마지막 커밋에서 `mode = "github"` 로 되돌린다(§9). 저장소 변수는 건드리지 않는다 |
| 토큰 | 오너가 부록 A 의 토큰 둘을 B 의 실기 전에 만들어 두면 그것을 쓴다. 아직이면 실기는 세션의 `monocsp` 토큰(`gh auth token --user monocsp`)과 시험 서버에서 만든 클라이언트 토큰을 쓴다고 PR 본문에 밝힌다 |

1. `rcm_ci_watch.py --check` 표가 전부 OK 다.
2. draft PR 에 push 하고 `--once` 를 돌린다. 시험 서버에 잡이 서고 PR 에 `rcm/ci` queued 가 보인다. 잡이 끝나면 훅이
   passed 를 올리고 `test` 가 초록이다. `now` · `late` 는 건너뜀이다.
3. 2 의 잡 로그에서 본다. Chrome 시험이 건너뛰지 않고 돌았는가, `node` 와 `gitleaks` 를 PATH 에서 찾았는가, 유닉스
   소켓을 쓰는 시험이 짧은 `TMPDIR` 에서 통과했는가. launchd 아래에서도 확인하려면 시험 서버를 launchd 에이전트로 한 번
   띄워 같은 일을 한다. 운영의 `mac2-worker` 는 launchd 아래에서 돌기 때문이다. 루프백 멀티캐스트를 쓰는 mDNS 시험이
   launchd 아래에서 로컬 네트워크 권한 때문에 깨지지 않는지도 이때 본다.
4. 2 의 잡이 도는 동안과 끝난 뒤 `find ~/.config/rcm ~/.local/share/rcm -newer <시작 표지>` 가 비어 있다. 운영 서버
   로그(`~/Library/Logs/rcm/server.log`)에 그 시간대의 새 줄이 없다.
5. 일부러 시험 하나를 깨뜨린 커밋을 push 한다. `rcm/ci` 가 failed 이고 `test` 가 빨강이며 `late` 가 돌지 않는다. 그
   실행을 「Re-run」 하면 대기 잡이 API 를 부르지 않고 `late`(ubuntu)로 간다(탈출구).
6. 대체를 차례로 본다. `late` 에서 ubuntu 갈래만 돌고 macOS 갈래는 돌지 않는다.
   1. 시험 서버를 멈추고 push 한 뒤 `--once` 를 돌린다. `unavailable · server_unreachable` 이고 `late` 가 돈다.
   2. 감시를 돌리지 않는다. 5분 뒤 `late` 가 돈다.
   3. 시험 서버를 `rcm pause` 한다. 감시를 돌리지 않으면 2분(실험 값 `queue_seconds = 120`) 뒤 `taken-over` 가
      올라오고 `late` 가 돈다. 다음 `--once` 가 잡을 취소한다. 감시를 돌리면 300초 뒤 `unavailable · paused` 가 먼저 온다.
   4. 시험 서버의 프리셋 `requires` 에 없는 도구 이름을 하나 넣는다. `unavailable · could not prepare` 이고 `late` 가 돈다.
7. 같은 브랜치에 커밋 둘을 1분 간격으로 push 한다. 첫 잡이 `superseded` 로 끝나고 첫 실행은 `concurrency` 가 취소한다.
8. 문서에서 확인하지 못한 GitHub 동작을 잠근다. `checks.yml` 의 체크 이름이 어떻게 보이는지, `legs` 가 빈 호출을 건너뛰는지,
   안의 잡이 전부 건너뛰었을 때 호출 잡의 결과가 무엇인지, `ran` 이 그 경우 비는지, `checks.yml` 의 `secrets` 가
   `GITHUB_TOKEN` 을 받는지, 대기 잡의 304 응답이 `X-RateLimit-Remaining` 을 줄이지 않는지다.
9. 측정을 남긴다. 2 의 잡 시간(단계별), 대기 잡이 GitHub 러너를 쥔 시간, 감시 한 바퀴의 GitHub 요청 수.
10. 시험 서버에서 `rcm-ci` 를 `nice -n 10` 과 `taskpolicy -b` 로 한 번씩 돌리며 같은 시간에 데모 부하 잡을 돌린다. 두 쪽의
    시간을 남긴다(§3.2).
11. 끝나면 시험 서버를 멈추고 `~/.local/share/rcm-devtest-m5r` 를 통째로 지운다. draft PR 의 상태는 남는다.

운영 반영(§9 C) 뒤에 오너가 한 번 더 확인한다. 운영에서 `rcm run rcm-ci --ref <B 의 draft 브랜치>` 를 한 번 내서
`mac2` 워커에서 여덟 단계가 다 돌고 훅이 그 커밋에 passed 를 올리는지 본다(결정 193). 그 뒤 1주 동안 반영 전 1주와 견주어
`gate` 의 중앙값과 p90, `held_by_load` 시간, `rcm-ci` 잡 수, GitHub 으로 넘어간 수(사유별)를 본다. 운영 DB 는
`sqlite3 -readonly` 로만 읽는다.

## 8. 안 만드는 것

- **rcm 본체가 GitHub API 를 부르는 것.** 결정 30 이다. 상태 쓰기 · PR 목록 · 저장소 변수는 스킬 템플릿에만 있다.
- **Mac mini 에 GitHub Actions self-hosted 러너를 다는 것.** 가장 짧은 길이지만 셋이 걸린다. GitHub 은 public 레포에
  self-hosted 러너를 쓰지 말라고 안내한다(포크 PR 이 러너에서 코드를 돌릴 수 있다). 러너는 rcm 큐 밖에서 돌아서
  dolomood 보다 낮게 둘 수 없다. dolomood 도 그 길(`ci.yml` 의 self-hosted 러너와 dispatch)에서 rcm 으로 옮겨 왔다.
- **GitHub 러너가 Tailscale 로 들어와 `rcm run` 을 직접 부르는 것.** 감시와 훅이 필요 없고 rcm 이 꺼져 있으면 바로 안다는
  장점이 있다. 그러나 rcm 클라이언트 토큰에는 프리셋 범위가 없다. GitHub 에 둔 토큰 하나가 dolomood 의 `deploy-dev`
  같은 프리셋까지 낼 수 있게 된다. 그리고 tailnet 을 GitHub 러너에 연다. 토큰에 프리셋 범위가 생기면 다시 본다.
- **룰셋의 `integration_id` 를 빼서 rcm 이 `test` 를 직접 채우는 것.** 리드 방향대로 룰셋은 그대로다. 빼면 상태를
  쓸 수 있는 누구나 `test` 를 채운다. 그리고 대체 경로는 어차피 GitHub 워크플로가 있어야 한다.
- **Checks API.** GitHub App 이 있어야 쓸 수 있다. 커밋 상태로 충분하다.
- **웹훅 수신기.** 공유기에 들어오는 포트를 연다. 1분 폴링으로 충분하다.
- **PR 의 merge ref 검사.** §2.6.
- **본체의 `[[notify]] on = ["running"]`.** 감시 스크립트가 running 을 올린다.
- **pytest 병렬화(`pytest-xdist`).** 직렬 pytest 가 평균 0.55 코어만 쓰므로 이득이 크다. 그러나 시험 사이의 포트 ·
  임시 폴더 · Chrome 공유를 따로 따져야 하는 별도 일이다. 실측이 쌓인 뒤 후속으로 본다.
- **Linux 워커 풀.** `platforms` 가 받을 수 있게만 두었다. 지금 운영에 Linux 기계가 없다.
- **PR 코멘트.** 커밋 상태와 링크로 충분하다.
- **기준 브랜치별 `mode`(`[[branches]]`).** 앞 판에 있었지만 rcm 레포가 쓰지 않아 뺐다. 쓰는 곳이 생기면 되살린다.

## 9. PR 순서 · 완료 기준

| PR | 브랜치 | 만지는 곳 | 릴리스 |
|---|---|---|---|
| 0 | `docs/m5r-ci-location-workplan` | 이 문서 | |
| A | `feat/m5r-ci-connect-skill` | `skills/rcm-ci-connect/`(§5.1) · `tests/ci_fakes.py` · 시험 C · W · V · H · N · mutcheck 변이 · `docs/configuration.md` 의 스킬 절 · CHANGELOG [Unreleased] ### Added | M5q 의 v0.8.0 다음 릴리스 |
| B | `ci/m5r-rcm-repo-waits-for-rcm` | `.rcm/ci.toml`(마지막 커밋은 `mode = "github"`) · `scripts/ci/`(템플릿 사본 셋과 `rcm_ci.sh`) · `ci.yml`(`schedule` 포함) · `checks.yml` · 시험 Y · J · `CONTRIBUTING.md` 의 「Checks」 · pr 스킬과 AGENTS.md 의 「머지 판단은 `gh pr checks --required`」 | 레포 파일이라 릴리스와 무관 |
| C | 운영(오너) | 부록 A 의 토큰 둘 · `presets.rcm-ci.toml` include · `[[notify]] rcm-ci-status` · `ci-watch.toml` · launchd 에이전트 · 운영에서 `rcm-ci` 한 번 돌려 보기(§7 끝) | |
| D | `ci/m5r-rcm-repo-switches-to-rcm` | `.rcm/ci.toml` 의 `mode` 를 `rcm` 으로 | |
| E | `docs/m5r-plan-and-decisions` | `PLAN.md` 결정 175~ 행 · 이 문서 §11 | |

순서는 A, B, C, D, E 다. B 는 A 의 템플릿을 복사하므로 A 가 dev 에 머지된 뒤다. B 의 draft 실험 커밋은 `mode = "rcm"` 으로
§7 을 돌리고, 마지막 커밋에서 `mode = "github"` 로 되돌려 머지한다. 감시가 아직 없는데 `rcm` 으로 머지하면 모든 PR 이
5분씩 기다린 뒤에 GitHub 으로 넘어가기 때문이다. C 의 끝에 오너가 운영에서 `rcm-ci` 를 한 번 돌려 본다. 그것이 통과해야
D 로 간다.

`gh pr checks` 는 대체가 일어난 PR 에서 `rcm/ci` 의 error 때문에 0 이 아닌 코드로 끝난다. B 는 pr 스킬과 AGENTS.md 에
「머지 판단은 `gh pr checks --required` 로 한다(필수는 `test` 하나)」를 적는다. 같은 조건을 지금 다른 PR 로 만들고 있는
머지 관문(R14, `merge_pr.py`)에 넘긴다. 그쪽이 먼저 머지되면 B 가 그 스크립트의 판단 기준을 맞춘다.

PR 마다 완료 기준은 같다.

1. 시험을 먼저 쓴다. `ruff check . && ruff format --check . && pytest` · `node --test tests/web/*.test.js` ·
   `python scripts/mutcheck.py` · `scripts/smoke_install.sh` 가 통과한다.
2. **격리 에이전트 검증.** 구현하지 않은 새 에이전트가 이 문서와 diff 만 보고 §6 의 표를 돌리고, B 는 §7 을 draft PR 에서
   실제로 돌린다.
3. B 의 draft 실험이 §7 8 의 여섯 가지를 잠그고 그 결과를 §11 에 남긴다.

시나리오 스물두 개와 PR 마다 첫 커밋에 넣을 시험 목록은 `docs/m5r-scenarios.md` 에 있다.

rcm 레포의 B · C · D 는 R15(결정 196) 뒤에 바뀌었다. 바뀐 표는 §13.6 에 있다.

## 10. 결정 (175~200)

| # | 결정 | 추천 | 오너 확인 |
|---|---|---|---|
| 175 | 본체와 스킬의 경계 | 본체는 바꾸지 않는다. GitHub 과 말하는 코드는 rcm 패키지에 실리는 `rcm-ci-connect` 스킬 템플릿에만 둔다. 결정 30 안에 든다 | 오너 확정 2026-11-20 |
| 176 | 설정 자리 | `.rcm/ci.toml`, `version = 1`. `rcm.toml` 의 `[ci]` 는 서버 설정 탐색과 겹쳐서 안 쓴다 | |
| 177 | 모드 | `github` · `rcm` · `both` 와 `fallback` `github` · `none`. 갈래 값 `github` · `rcm` · `rcm-only`. 파일이 없으면 `github`. 기준 브랜치별 덮어쓰기는 뺐다 | |
| 178 | 모드 정하는 순서 | schedule → 포크 · 빈 head 레포 · `dependabot[bot]` → 저장소 변수 `RCM_CI_MODE`(404 는 없음) → 파일 없음 · 오류는 `github` → push 목록 → `mode`. 대기 잡과 감시가 같은 함수, 둘 다 head 커밋의 파일을 읽는다. 변수는 오너가, 급할 때는 세션도 바꾸되 PR 댓글로 남긴다 | |
| 179 | 상태 계약 | context `rcm/ci`, 설명의 첫 낱말 일곱 개, 140자, 경과는 GitHub 의 `created_at`, `creators` 거르기, `target_url` 은 기본으로 비움 | |
| 180 | 판정 | 첫 실행의 `failure` 는 대신 돌지 않는다. `timed_out` · 준비 실패 · 도구 없음 · 종료 75 · `lost` · 정지 · 사람 취소는 `unavailable` 로 대신 돈다. 다시 돌리기는 rcm 판정을 GitHub 판정으로 바꾸는 사람의 탈출구다 | |
| 181 | 워크플로 모양 | `route`(head 체크아웃) → `rcm`(fail 이면 종료 1) → `now` · `late`(재사용 워크플로, `late` 는 `!cancelled()`) → `test`(`always()`). `checks.yml` 의 `legs` 검사와 `ran` 출력, `verdict` 의 `ran` 대조와 최신 상태 재확인. 룰셋 · 잡 이름 `test` 는 그대로. main 밖은 `cancel-in-progress` | |
| 182 | 기다릴 시간 | pickup 300초 · queue 1800초(30분) · run 3600초 · poll 30초 · ETag. 다시 돌리기는 바로 GitHub | 오너 확정 2026-11-20 |
| 183 | 트리거 | (c) 둘 다. 기본은 (b) 감시 스크립트, (a) 세션의 `rcm run` 은 합류한다 | |
| 184 | 감시 대상 | 같은 레포 브랜치의 열린 PR head 와 `push` 브랜치 head. `--ref <브랜치>` 로 낸다. merge ref 는 안 본다. `worker_down` 120초 · `paused` 300초면 넘긴다 | |
| 185 | 알림 훅 | 종료 상태만. 종료 75 는 `unavailable · infra`. `taken-over` 는 덮지 않는다. supersede 는 `GET /jobs/{id}` 로 가린다. 단계는 실패 스텝, 없으면 마지막 스텝. 못 올리면 종료 1, 감시가 120초 뒤 다시 | |
| 186 | 비밀 | GitHub fine-grained 토큰 파일(0600)과 rcm 전용 클라이언트 토큰 `ci-watch`(admin 아님)를 오너가 만든다. 범위와 절차는 부록 A. GitHub 에는 rcm 토큰도 Tailscale 키도 두지 않는다. `creators` 의 신뢰 경계는 `monocsp` 토큰을 쥔 세션과 Mac mini 의 같은 사용자 프로세스다(§2.9) | 오너 확정 2026-11-20 |
| 187 | rcm 레포 값 | dev 로 가는 PR 도 릴리스 PR(dev→main)도 rcm 에서만 돈다. `push = ["dev"]`. Linux 와 Python 3.13 검사가 사라지는 것은 알고 고른 위험이다(§3.4) | 오너 확정 2026-11-20 |
| 188 | `rcm-ci` 프리셋 | git_ref · `repo = "rcm"` · `pool = "mac2"`(mac2 워커) · `nice -n 10` · `concurrency_group = "rcm-ci"` · supersede all · timeout 3300 · `requires` 넷 · low(지금은 효과 없음) | 오너 확정 2026-11-20 |
| 189 | 잡 스크립트 | 여덟 단계. 준비 단계와 도구 확인의 실패는 종료 75. 환경을 워크스페이스와 짧은 `TMPDIR` 로 돌리고 `RCM_*` 를 지운다. smoke 에 `PYTHON` 을 준다. `RCM_CHROME` 으로 Chrome 시험을 필수로. `RCM_CI_STEPS` 로 단계 고르기 | |
| 190 | 운영 rcm 고장 | dev 룰셋의 `test` 때문에 rcm 의 잘못된 빨강은 dev 머지 정지다. 대체 경로 다섯(서버 없음 · 상태 없음 · worker_down · 정지 · 종료 75) + 저장소 변수 + 「Re-run」 탈출구. 엉뚱한 초록은 주 1회 실행만 다시 본다 | |
| 191 | dolomood | 바꾸지 않는다. 지금의 `local-ci/full` 은 이 기능의 `rcm` · `fallback none` · 트리거 (a) 꼴이다 | |
| 192 | 감시 실행 자리 | `~/.config/rcm/scripts/`, launchd `StartInterval` 60, 잠금 하나(10분 넘으면 빼앗기), 시간 제한 15 · 60 · 50초, 상태는 `~/.local/state/rcm-ci-watch/`, 설정을 못 읽으면 `unavailable · watcher_config` | |
| 193 | 순서 | A → B(실험 커밋만 `rcm`, 마지막은 `github`) → C(운영, 오너, 끝에 운영에서 `rcm-ci` 한 번) → D(`rcm` 으로) → E. 스킬은 v0.8.0 다음 릴리스 | |
| 194 | 주 1회 GitHub 전체 실행 | `ci.yml` 의 `schedule`(월 03시 KST), `ref: dev` 명시(기본 브랜치가 main 이라서), ubuntu · macOS 모든 갈래. 실패하면 알림만이고 머지를 막지 않는다. main 에 들어간 뒤부터 돈다 | 오너 확정 2026-11-20 |
| 195 | 대체 갈래 | GitHub 이 대신 돌 때는 ubuntu 갈래(3.11 · 3.13 · smoke · secrets)만. macOS 는 `rcm-only`. macOS 러너 부족이 dev 머지를 막지 않게 한다 | 오너 확정 2026-11-20 |
| 196 | rcm 레포의 필수 체크 | 커밋 상태 `rcm/ci` 하나(main 은 `main-from-dev-only` 도). `ci.yml` 은 `workflow_dispatch` 만. 대기 잡 · `checks.yml` · schedule · `late` · `taken-over` 를 이 레포는 쓰지 않는다. `.rcm/ci.toml` 은 `mode = "rcm"` · `fallback = "none"`. 맥미니 고장 때는 오너 지시로 `workflow_dispatch`(§13.1) | 오너 확정 2026-11-20(R15) |
| 197 | 대체 없는 감시 | `fallback = "none"` 이면 `worker_down` · `paused` 가 이어져도 취소하지 않고 기다린다. 제출 실패는 예전대로 `unavailable` 을 올리고 다시 내지 않는다(§13.2). `server_unreachable` · 시간 초과만 세 바퀴까지 다시 낸다(§13.8) | |
| 198 | 상태를 쓰는 주체 | 이 레포는 알림 훅 대신 운영 잡 스크립트가 진행과 결과를 올린다. 감시는 `queued` 와 잡 스크립트가 못 올린 끝(120초 뒤)만. pending 이 아닌 상태 위에, 그리고 남이 올린 pending 위에 pending 을 올리지 않는다(§13.3) | |
| 199 | 토큰 | 전용 토큰 둘 대신 `rcm_client_config`(rcm 클라이언트의 `client.toml`)와 `github_token_command`(`gh auth token --user monocsp`). 값은 새 파일에 쓰지 않는다(§13.4) | 오너 지시 2026-11-20 |
| 200 | 운영 자리 | `~/.config/rcm/ci-watch/`(스크립트 둘 · 설정), launchd `com.remote-ci-monitor.ci-watch` 60초, 상태 `~/.local/state/rcm-ci-watch/`, 로그 `~/Library/Logs/rcm/ci-watch.log`. 결정 192 의 자리를 바꾼다(§13.5) | 오너 승인 C 단계 |

## 11. 구현하며 바뀐 것

(구현과 격리 검증이 끝난 뒤 PR 마다 채운다.)

### PR A (#238)

| 바뀐 것 | 이유 |
|---|---|
| `ci-watch.toml` 에 `github_api` 와 `[[repos]].legs` 를 더했다. `legs` 가 없으면 `--check` 가 경고하고 감시는 `[platforms]` 를 못 검사했다고 로그 한 줄을 남긴다 | 시험이 가짜 GitHub 을 가리키고, 감시가 `route` 와 같은 갈래 목록으로 설정을 검사하게 한다 |
| `rcm_ci_wait.py` 세 하위 명령에 `--config`, 훅에 `--api-url` 과 `--rcm-token-file` 을 더했다. 감시와 훅은 rcm 토큰이 있으면 `GET /jobs/{id}` 에 싣는다 | `wait` · `verdict` 는 체크아웃한 head 의 설정에서 context · creators · 시한을 읽는다. `read_auth` 를 건 서버에서도 잡을 읽는다 |
| `verdict` 는 상태를 다시 읽을 때 `taken-over` 를 건너뛴다 | 훅의 `failed` 가 `taken-over` 바로 앞에 올라와도 첫 실행에서 실패로 잡는다 |
| `route` 는 PR 이면 `ref` 로 head sha 를 낸다. schedule 이면 `sha` 를 비운다 | 검사 위치를 정한 커밋과 검사하는 커밋을 맞춘다. B 의 draft 실험에서 확인한다 |
| `rcm run` 의 서버 없음은 종료 3 이 아니라 2(`cannot read presets: cannot reach …`)다. 토큰 거부는 `token rejected by …` 다 | 진짜 CLI 의 출력이다. H6 · H7 · H23 은 진짜 `rcm run` 을 가짜 서버에 대고 돌려 얻은 문장을 쓴다(격리 검증 1) |
| 감시가 합류한 잡을 `rcm cancel` 하면 서버는 `{"left": true}` 로 합류만 떼고 잡은 계속 돈다. 감시는 그 출력을 읽어 `left join of #N (job keeps running)` 으로 적는다 | 세션이 먼저 낸 잡(B3)을 GitHub 이 넘겨받아도 세션의 잡은 지우지 않는다. 그 잡의 레인은 계속 쓰인다 |
| `job_url = true` 면 상태의 `target_url` 이 `<server>/#/jobs/<N>`(서버 화면의 잡 링크, 훅이 싣는 `RCM_URL` 과 같은 꼴)이다 | 읽기만 하고 쓰지 않던 설정이었다(격리 검증 2) |
| 한 레포의 예외(빈 토큰 파일, `IncompleteRead` 등)는 로그 한 줄로 넘기고 다음 레포를 돈다. `IncompleteRead` 는 네트워크 오류로 본다 | 격리 검증 3 |
| 429 와 남은 한도 0 인 403 은 `github_auth` 가 아니라 `rate_limited` 다. `Retry-After` 나 `X-RateLimit-Reset` 까지 그 레포를 묻지 않고, contents 의 429 는 `watcher_config` 로 올리지 않고 다음 바퀴로 미룬다 | 한도는 토큰 문제가 아니다. 한도 때문에 GitHub 이 대신 돌면 안 된다 |
| `route` 의 `::warning::` 문장은 줄바꿈과 `::` 를 지운다 | 저장소 변수나 설정 오류 문장이 워크플로 명령을 끼우지 못하게 한다 |
| `created_at` 을 못 읽는 pending 상태는 처음 본 때부터 `pickup_seconds` 를 재고 `no_pickup` 으로 넘긴다 | 그 상태 하나로 대기 잡이 2시간 시한까지 붙잡히지 않게 한다 |
| 감시는 이미 다룬 sha 의 `skip known` 을 처음 한 번만 남기고, 훅이 놓친 결과의 120초는 잡의 `finished_at` 으로 잰다 | 1분마다 같은 줄이 쌓이지 않게 한다. `finished_at` 은 같은 머신의 시계다 |
| `rcm skills install` 은 템플릿의 실행 비트를 지운다 | 원래 동작이다. B 에서 다룬다 |

## 12. 설계 검토가 바꾼 것

둘째 판(오너 확정 다섯을 반영한 판)을 격리 설계 반박이 읽었다. P0 다섯 · P1 일곱 · P2 열두 건이 나왔다. 같은 날 오너가
결정 둘을 더했다. 갈래 대체는 ubuntu 만(결정 195), 주 1회 GitHub 전체 실행(결정 194)이다. 반박의 근거를 코드와
GitHub 에 대 보았고 틀린 근거는 찾지 못했다. 그래서 전부 받아들였다. P2-1 하나만 일부를 받아들였다.

| 지적 | 이 판에서 바뀐 곳 | 받아들임 |
|---|---|---|
| P0-1 「failure 는 대신 안 돈다」와 「다시 돌리면 fallback」이 부딪친다 | 다시 돌리기를 사람의 탈출구로 적었다(§2.4). 「`rcm run` 으로 다시」는 「새 커밋을 push」로 바꿨다. 대기 잡은 `fail` 이면 종료 1, `late` 조건을 고쳤고, `verdict` 가 끝에 `rcm/ci` 를 다시 읽는다(§2.5, V3 · V4) | 받아들임 |
| P0-2 건너뛴 잡은 호출한 쪽에 success 로 보인다 | `checks.yml` 의 `legs` 검사 잡과 `ran` 출력, `verdict` 의 `ran` 대조(§2.5, V2), draft 실험 목록(§7 8). `late` 만 `!cancelled()` | 받아들임 |
| P0-3 dev 룰셋에 `test` 가 필수로 걸렸다 | §1.1 표, §3.6 머리와 행들, squash · rebase 병합 뒤 dev head 가 PR head 와 다르다는 것(§2.6). `gh api` 로 다시 확인했다 | 받아들임 |
| P0-4 환경 탓 실패가 `failure` 로 분류된다 | 준비 단계와 도구 확인의 종료 75(§3.3, 결정 189), 훅의 `unavailable · infra`(§2.7, 결정 185), 프리셋 `requires`(§3.2). 코드를 보니 `requires` 의 도구가 없으면 워커가 `tool_missing` 으로 종료 코드 없이 실패시키므로 그것도 `unavailable` 이다. 멈추는 회귀가 GitHub 으로 넘어가 통과할 수 있다는 것(§2.4), 엉뚱한 빨강의 손잡이(§3.6) | 받아들임 |
| P0-5 실기 절차가 돌 수 없다 | 실험 커밋에서만 `mode = "rcm"` · `queue_seconds = 120`, 토큰을 누가 어디서(§7 표), C 끝의 운영 한 번 돌려 보기(§7 끝, §9, 결정 193) | 받아들임 |
| P1-1 `route` 가 merge 커밋의 설정을 읽는다 | head sha 를 체크아웃해 읽는다(§2.2 · §2.5 · §5.3) | 받아들임 |
| P1-2 감시 스크립트가 멈출 수 있다 | 시간 제한 셋, 잠금 빼앗기, 설정을 못 읽으면 `watcher_config`, 변수 404(§2.6, H17~H20) | 받아들임 |
| P1-3 대체 때 `rcm/ci` 가 빨간 X 로 보이고 `gh pr checks` 가 0 이 아니다 | §2.5 끝과 §9 B. 판단 기준을 R14 머지 관문에 넘긴다 | 받아들임 |
| P1-4 J 시험이 진짜 pytest 를 다시 돌린다 | `RCM_CI_STEPS`, 가짜 도구를 PATH 앞에 둔 가짜 프로젝트, `ci_fakes.py` 의 DNS 끄기 · 주입 시계(§6) | 받아들임 |
| P1-5 `creators` 의 신뢰 경계를 부풀렸다 | `monocsp` 토큰을 쥔 세션과 같은 사용자 프로세스(rcm 잡 자신 포함)라고 고쳤다. 후속 후보 셋(§2.9) | 받아들임 |
| P1-6 §3.4 가 늦은 발견과 잃는 근거를 빠뜨렸다 | `release.yml` 스모크가 태그 뒤에 돈다는 것, README · classifiers · `examples/systemd/` 의 근거, `python3.14` 선택지, 주 1회 실행(§3.4) | 받아들임 |
| P1-7 실기 목록이 모자란다 | 루프백 멀티캐스트와 짧은 `TMPDIR`(§3.3 · §7 3) | 받아들임 |
| P2-1 `priority` 는 `mac2` 풀에서 효과가 없다 | 표와 프리셋 주석에 적었다. `held_by_load` 측정과 `taskpolicy -b` 비교를 실기에 넣었다(§3.2 · §3.5 · §7 10) | 일부. 키는 지우지 않았다. 기본 풀로 옮기면 바로 뜻이 생기고 지금은 해가 없다 |
| P2-2 `timeout_seconds` 5400 이 `run_seconds` 3600 과 맞지 않는다 | 3300 으로(§3.2) | 받아들임 |
| P2-3 main push 는 rcm 에서 돌 까닭이 없다 | `push = ["dev"]`(§3.1). 감시의 `branches` 도 dev 만 | 받아들임 |
| P2-4 큐 정지 때 끝없이 기다린다 | `paused` 300초면 `unavailable`(§2.6, H10, §7 6) | 받아들임 |
| P2-5 대체 경로의 갈래를 고를 수 있게(`late` 를 ubuntu 만) | 결정 195 로 반영했다. 갈래 값 `rcm-only`(§2.2 · §3.1) | 받아들임 |
| P2-6 `[[branches]]` 는 쓰는 곳이 없다 | 스키마 · 순서 · 시험에서 뺐다(§2.2 · §2.3 · §8). 남아 있으면 모르는 키(C6) | 받아들임 |
| P2-7 훅이 실패 스텝만 본다 | 없으면 `RCM_LAST_STEP`(§2.7 · §5.5, N) | 받아들임 |
| P2-8 `target_url` 이 내부 주소를 공개한다 | 기본으로 비우고 `job_url` 로 켠다(§2.4 · §2.6 · §2.9) | 받아들임 |
| P2-9 304 가 한도에 안 든다는 것은 문서 말이다 | draft 실험에서 `X-RateLimit-Remaining` 으로 잰다(§7 8) | 받아들임 |
| P2-10 head 레포가 빈 PR 과 Dependabot | 둘 다 포크처럼 `github`(§2.3, C11 · H4) | 받아들임 |
| P2-11 smoke 가 PATH 의 `python3` 를 쓴다 | `PYTHON=$RCM_CI_PYTHON`(§3.3, J6) | 받아들임 |
| P2-12 `RCM_CI_MODE` 를 누가 바꾸나 | 오너가, 급할 때는 세션도 바꾸되 PR 댓글로 남긴다(§2.3 · §3.6) | 받아들임 |

## 13. 이 레포의 운영 형태: GitHub 대체 없이 rcm 만 (결정 196~200)

계획서 셋째 판(§0~§12)은 GitHub 의 필수 체크 `test` 를 가벼운 대기 잡으로 만들고 rcm 이 답하지 않으면 GitHub 러너가
대신 도는 설계였다. 2026-11-20 오너가 이 레포에서는 그 대체를 쓰지 않기로 정했다(오너 요구 목록의 R15). 오너의 말은
「rcm ci 를 로컬로 돌리면 github ci 는 왜 해? 하나만 하면 그걸 체크하면 되잖아」, 「폴백으로 쓰거나 아예 쓰지 말라 하면
시도 안 하면 되잖아」였다. 이제 이 레포의 검사는 rcm 에서만 돈다. push 가 들어오면 감시 스크립트가 잡을 자동으로 낸다.

이 레포에 관해서는 이 절이 앞 절의 여러 곳을 대신한다. 대기 잡과 기다릴 시간(§2.5, 결정 181 · 182), 알림 훅(§2.7),
잡 스크립트(§3.3), 운영 rcm 이 고장 났을 때의 대체 경로(§3.6, 결정 190), 주 1회 실행과 대체 갈래(결정 194 · 195),
감시 자리(결정 192), 전용 토큰(부록 A), §9 의 B · C · D 가 그렇다. 앞 절의 설계는 다른 레포가 고를 수 있게
`rcm-ci-connect` 스킬의 템플릿에 그대로 남는다.

### 13.1 필수 체크는 `rcm/ci` 하나이고 GitHub 대체는 없다 (결정 196)

오너가 2026-11-20 에 확정했다(R15).

이 PR 전에 이미 바뀐 것이 있다. dev 룰셋의 필수 체크는 커밋 상태 `rcm/ci` 하나다. main 룰셋은 `rcm/ci` 와
`main-from-dev-only` 둘을 요구한다. `.github/workflows/ci.yml` 은 `workflow_dispatch` 만 받는다. 곧 사람이 Actions
화면에서 손으로 돌릴 때만 돈다. 운영 rcm 서버 설정에는 프리셋 `rcm-ci` 가 있다. 이 프리셋은 `mac2` 풀에서 운영 스크립트
`~/.config/rcm/scripts/rcm-ci.sh` 를 돌린다. 그 스크립트는 시작할 때 `rcm/ci` 에 pending 을, 끝날 때 success 나
failure 를 올린다. 이때 쓰는 토큰은 `gh auth token --user monocsp` 로 얻는다. 지금까지는 사람이 push 한 뒤
`rcm run rcm-ci --ref <브랜치>` 를 손으로 냈다.

이 PR 은 그 제출을 자동으로 바꾼다. 같은 레포 PR 의 head 나 dev 에 push 가 들어올 때마다 감시 스크립트가 `rcm-ci` 를
낸다. 레포 루트에는 `.rcm/ci.toml` 을 넣었다. 파일에는 `mode = "rcm"` 과 `fallback = "none"` 을 적었다. `[rcm]` 에는
`preset = "rcm-ci"` · `context = "rcm/ci"` · `push = ["dev"]` · `creators = ["monocsp"]` 를 적었고 `[platforms]` 는
두지 않았다.

이 레포는 대기 잡, `checks.yml`, 주 1회 schedule, 세 문턱(`pickup_seconds` 300 · `queue_seconds` 1800 ·
`run_seconds` 3600)을 쓰지 않는다. GitHub 이 대신 돌 때 쓰던 `late` 갈래와 `taken-over` 상태도 쓰지 않는다. 그래서
결정 181 · 182 · 194 · 195 와 결정 190 의 대체 경로는 이 레포에 해당하지 않는다. 그 템플릿들은 다른 레포가 고를 수
있게 스킬에 남는다.

Mac mini 가 고장 나면 오너가 지시할 때만 Actions 화면에서 `ci.yml` 을 손으로 돌린다. 이 실행은 `rcm/ci` 를 올리지
않는다. `ci.yml` 에 그런 단계가 없기 때문이다. 그래서 그 결과만으로는 머지 관문 `tools/merge_pr.py` 가 열리지 않는다.
그때 어떻게 머지할지는 오너가 정한다. 룰셋을 잠깐 바꾸는 것이 그 예다.

감시가 내지 못한 커밋은 세션이 아래 명령으로 손으로 낸다. 같은 커밋이면 서버가 새 제출을 앞 잡에 합류시키므로 검사가
두 번 돌지 않는다.

```sh
rcm run rcm-ci --source git_ref --ref <브랜치> --no-wait
```

알고 고른 위험(§3.4)은 더 커졌다. Linux 와 Python 3.13 을 평소에 보지 않는 것은 전과 같다. 주 1회 GitHub 실행도
없으니 그 둘을 다시 보는 길은 오너가 지시한 `workflow_dispatch` 하나뿐이다.

### 13.2 대체가 없는 감시는 멈춘 잡을 기다린다 (결정 197)

감시는 그 커밋의 `.rcm/ci.toml` 이 `fallback = "none"` 이면 그 사실을 잡 기록에 적는다. 그 뒤로는 잡의 사유가
`worker_down`(워커가 죽음)이나 `paused`(큐 정지)로 이어져도 error 를 올리지 않고 잡을 취소하지도 않는다. 사유가 바뀔
때 로그에 `#N worker_down but no fallback, waiting` 같은 줄을 하나 남길 뿐이다. 대체가 있는 레포는 예전대로
`worker_down` 이 120초, `paused` 가 300초 이어지면 `unavailable` 을 올리고 잡을 취소한다.

대신 돌 GitHub 이 없는데 감시가 잡을 취소하면 PR 에는 error 만 남고 사람이 다시 내야 한다. 배포도 걸린다. rcm 배포
절차는 큐를 멈추고 도는 잡이 0 이 되기를 기다린 뒤 서비스를 재시작한다. 그 사이에 감시가 300초 뒤 대기 중인 `rcm-ci`
잡을 취소하면 배포 한 번이 PR 들을 실패로 만든다.

이 절을 쓸 때는 제출 실패를 다루는 방식을 바꾸지 않았다. `server_unreachable` · `rcm_auth` · `unknown_preset` 같은
사유로 제출이 실패하면 감시는 `error unavailable · <사유>` 를 올리고 그 커밋을 다룬 것으로 적었다. 그 뒤 §13.8 에서
`server_unreachable` 과 시간 초과만 세 바퀴까지 다시 내도록 바꿨다. 설정 · 인증 실패는 지금도 곧바로 error 다.

시험은 `test_r15_without_a_fallback_a_stuck_job_is_left_to_wait` 이고 변이는 `m5r-watch-no-fallback-still-cancels` 다.

### 13.3 진행과 결과는 잡 스크립트가 올리고 감시는 `queued` 와 못 올린 결과만 올린다 (결정 198)

이 레포에는 알림 훅(`rcm_ci_status.py`, 서버 설정의 `[[notify]] rcm-ci-status`)을 달지 않는다. `rcm/ci` 상태는 운영
잡 스크립트 `rcm-ci.sh` 와 감시가 나누어 올린다. `rcm-ci.sh` 는 레포 밖의 운영 파일이고 이 PR 은 그 파일을 건드리지
않았다.

| 올리는 쪽 | 때 | state | 설명 |
|---|---|---|---|
| 감시 | 잡을 낸 직후 | `pending` | `queued · #N · position P · wait ~M min` |
| `rcm-ci.sh` | 시작 | `pending` | `rcm job N running` |
| `rcm-ci.sh` | 끝, 통과 | `success` | `rcm job N passed` |
| `rcm-ci.sh` | 끝, 실패 | `failure` | `rcm job N failed at <단계>` |
| 감시 | 잡이 끝났는데 120초 넘게 pending | §2.7 의 대응표대로 | 예: `error` · `superseded · #12 · by #13` |

둘이 겹칠 때 따르는 규칙은 셋이다. 템플릿 공통 규칙이라 대체가 있는 레포에도 같다.

1. `creators` 로 거른 가장 새 `rcm/ci` 상태가 pending 이 아니면 결과가 이미 올라온 것이다. 서버의 잡이 아직 running
   이어도 감시는 그 커밋을 더 따라가지 않는다. 고치기 전 템플릿은 잡 스크립트가 결과를 올린 뒤 서버에서 잡이 끝나기까지의
   몇 초 사이에 `running` 을 덮어 올렸다. 그러면 결과가 가려졌다가 120초 뒤 감시가 다시 덮어 썼다. 고치기 전 코드로
   돌린 시험이 queued · running · passed 세 번의 상태 쓰기로 이 현상을 재현했다.
2. 가장 새 pending 이 감시가 쓰는 낱말(`queued` · `running`)이 아니면 잡 스크립트가 진행을 올리고 있는 것이다. 이때
   감시는 `queued` 도 `running` 도 올리지 않는다.
3. 잡이 끝났는데 상태가 120초 넘게 pending 이면 잡 스크립트가 결과를 못 올린 것이다. 같은 브랜치의 새 잡이 옛 잡을
   치우는 supersede 로 취소된 잡, 사람이 취소한 잡, `timed_out` 이나 `lost` 로 끝난 잡이 그렇다. 이때는 감시가 §2.7 의
   대응표대로 올린다. 위 표의 마지막 행이 이 경우다.

잡 스크립트의 설명 문장(「rcm job N …」)은 상태 설명의 첫 낱말 계약(§2.4)을 따르지 않는다. 이 레포에는 그 낱말을 읽는
대기 잡이 없고 `tools/merge_pr.py` 는 상태의 state 만 본다. 그래서 그대로 둔다.

시험은 `test_r15_the_job_scripts_own_pending_is_not_overwritten_with_running` ·
`test_r15_a_result_posted_before_the_job_ends_is_never_covered_by_pending` ·
`test_r15_the_watcher_still_covers_a_job_that_could_not_post_its_end` 셋이다. 변이는
`m5r-watch-pending-over-a-result` · `m5r-watch-overwrites-the-job-pending` 둘이다.

### 13.4 토큰은 있던 자리에서 읽는다 (결정 199)

부록 A 의 전용 토큰 둘은 만들지 않는다. GitHub fine-grained 토큰 파일과 rcm 클라이언트 토큰 `ci-watch` 가 그 둘이다.
오너는 Mac mini 의 gh 키체인 토큰을 쓰라고 지시했다. 그래서 `ci-watch.toml` 에 새 키가 둘 생겼다.

| 새 키 | 대신하는 키 | 읽는 법 |
|---|---|---|
| `rcm_client_config` | `rcm_token_file` | rcm 클라이언트의 `client.toml` 을 가리킨다. 감시는 rcm 클라이언트와 같은 규칙으로 읽는다. 파일의 `token` 을 쓰고 없으면 `token_env` 가 가리키는 환경 변수(기본 `RCM_TOKEN`)를 쓴다 |
| `github_token_command` | `github_token_file` | 명령을 적는다. 감시가 바퀴를 돌 때마다 실행해 표준 출력의 첫 줄을 쓴다. 이 레포의 값은 `["/opt/homebrew/bin/gh", "auth", "token", "--user", "monocsp"]` 다 |

새 키와 대신하는 키는 쌍마다 정확히 하나만 적는다. 둘 다 적거나 하나도 없으면 설정 오류로 종료 2 다.

토큰 값은 메모리에만 두고 새 파일에 쓰지 않는다. 명령이 실패하면 감시는 그 바퀴에서 그 레포를 건너뛰고 로그에는 종료
코드만 남긴다. 명령의 출력은 로그에 싣지 않는다. `client.toml` 은 권한이 0600 이어야 한다. 아니면 감시는 아무것도 하지
않고 종료 2 로 끝난다.

launchd 아래에서 gh 가 키체인 토큰을 읽는다는 것은 2026-11-20 실기에서 확인했다. 첫 launchd 바퀴가 그 토큰으로
GitHub 의 PR 목록을 읽었다. 같은 머신의 `mac2` 워커도 launchd 아래에서 돈다. 그 워커에서 도는 `rcm-ci.sh` 는 같은
방식으로 이미 상태를 올리고 있었다. §2.9 의 「키체인은 launchd 아래에서 못 쓴다」는 이 머신에서는 맞지 않았다.

대가도 있다. `client.toml` 의 토큰은 오너의 admin 토큰이다. `--check` 는 이것을 FAIL 이 아니라 WARN 으로 적는다.
감시는 자기가 낸 잡 번호만 취소한다. rcm 의 supersede 는 같은 토큰 이름이 낸 잡끼리만 치운다. 감시와 세션의 손 제출은
같은 오너 토큰을 쓰므로 서로의 옛 잡을 치운다. 같은 브랜치의 옛 잡을 치우는 것은 원하는 동작이다.

GitHub 토큰은 `monocsp` 계정의 gh 토큰이라 상태 쓰기보다 권한이 넓다. 다만 같은 머신의 `rcm-ci.sh` 와 세션들이 이
토큰을 이미 쓰고 있다. 그래서 §2.9 의 신뢰 경계는 넓어지지 않는다.

### 13.5 운영 자리와 끄는 법 (결정 200)

오너가 승인한 C 단계를 이 PR 과 함께 끝냈다. 스크립트 자리는 결정 192 의 `~/.config/rcm/scripts/` 가 아니라
`~/.config/rcm/ci-watch/` 다. 오너가 그렇게 지시했다. 놓은 파일은 아래와 같다.

| 파일 | 내용 |
|---|---|
| `~/.config/rcm/ci-watch/rcm_ci_watch.py` · `rcm_ci_config.py` | 이 PR 의 템플릿 사본 |
| `~/.config/rcm/ci-watch/ci-watch.toml` | 권한 0600. 레포 `monocsp/remote_ci_monitor`, rcm 서버 `http://127.0.0.1:8787`, rcm 클라이언트 `~/.local/bin/rcm`, `branches = ["dev"]`. 토큰은 결정 199 대로 읽는다 |
| `~/Library/LaunchAgents/com.remote-ci-monitor.ci-watch.plist` | `/opt/homebrew/bin/python3.11 -I` 로 `--once` 를 60초마다 돌린다(`StartInterval` 60, `RunAtLoad`). PATH 맨 앞은 `/opt/homebrew/bin` |
| `~/.local/state/rcm-ci-watch/monocsp__remote_ci_monitor.json` | 감시의 상태 |
| `~/Library/Logs/rcm/ci-watch.log` | 사건을 한 줄씩 적는 로그 |
| `~/Library/Logs/rcm/ci-watch.out.log` · `ci-watch.err.log` | launchd 가 받은 표준 출력과 표준 오류 |

운영 `server.toml`, 운영 DB, 서비스 체크아웃, `rcm-ci.sh` 는 건드리지 않았다. 프리셋 `rcm-ci` 는 이미 있었다.

설치는 이 순서로 했다.

1. `--check`. 전부 OK 였고 WARN 이 셋 나왔다. admin 토큰, 갈래 목록 없음, main 에 아직 `.rcm/ci.toml` 없음이다.
2. `--once --dry-run`
3. `--once`
4. `launchctl bootstrap`

드라이런과 첫 바퀴에서 열린 PR 둘과 dev 의 head 는 모두 `skip … why=no_file` 였다. 그 커밋들에 아직 `.rcm/ci.toml` 이
없기 때문이다.

템플릿이 바뀌면 사본 둘을 다시 복사한다. launchd 는 다음 바퀴에 새 파일을 읽는다.

감시를 끌 때는 아래 세 줄을 친다.

```sh
launchctl bootout gui/$(id -u)/com.remote-ci-monitor.ci-watch
rm ~/Library/LaunchAgents/com.remote-ci-monitor.ci-watch.plist
rm -r ~/.config/rcm/ci-watch ~/.local/state/rcm-ci-watch
```

잠깐 멈출 때는 첫 줄만 친다. 다시 켤 때는 아래 명령을 친다.

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.remote-ci-monitor.ci-watch.plist
```

저장소 변수 `RCM_CI_MODE=github` 를 걸어도 감시는 새 커밋을 내지 않는다. 감시는 그 변수를 5분 캐시로 읽는다.

이 PR 이 dev 에 머지되기 전에는 다른 브랜치의 커밋에 `.rcm/ci.toml` 이 없다. 그래서 감시는 그 커밋들을
`skip … why=no_file` 로 넘긴다. 그동안 그 PR 들은 여전히 손으로 낸다. dev 를 받은 뒤의 push 부터 자동이다.

### 13.6 남은 단계 B · C · D 는 어떻게 바뀌었나

E 는 바뀌지 않았지만 같이 적는다.

| 단계 | §9 의 원래 내용 | 바뀐 것 |
|---|---|---|
| B `ci/m5r-rcm-repo-waits-for-rcm` | `.rcm/ci.toml`(마지막 커밋은 `mode = "github"`) · `scripts/ci/` 의 템플릿 사본과 `rcm_ci.sh` · 대기 잡 `ci.yml`(schedule 포함) · `checks.yml` · 시험 Y · J · draft 실험(§7) | 하지 않는다. 대기 잡 · `checks.yml` · schedule 은 결정 196 으로 필요 없다. 잡 스크립트는 레포 밖에 있는 운영의 `rcm-ci.sh` 가 맡았다. 레포에 들어오는 것은 이 PR 의 `.rcm/ci.toml` 하나다. Y · J 시험과 §7 의 대체 실기(5 · 6 · 8 · 10)는 해당 없다 |
| C 운영 | 부록 A 의 토큰 둘 · `presets.rcm-ci.toml` include · `[[notify]] rcm-ci-status` · `ci-watch.toml` · launchd · 운영에서 `rcm-ci` 한 번 | 이 PR 과 함께 끝냈다. 프리셋은 운영 설정에 이미 있다. 토큰은 결정 199 대로 읽고 알림 훅은 달지 않는다(결정 198). 감시와 launchd 는 결정 200 이다. 운영에서 `rcm-ci` 를 한 번 돌려 보는 일은 이 PR 의 자동 제출이 대신한다 |
| D `ci/m5r-rcm-repo-switches-to-rcm` | `.rcm/ci.toml` 의 `mode` 를 `rcm` 으로 | 이 PR 에 합쳤다. 파일은 처음부터 `mode = "rcm"`, `fallback = "none"` 이다 |
| E `docs/m5r-plan-and-decisions` | `PLAN.md` 의 결정 행과 §11 | 그대로 남는다. 결정 196~200 도 옮긴다 |

### 13.7 남은 위험

1. 설정 · 인증 실패로 끝난 제출은 다시 내지 않는다(결정 197). 서버가 재시작하는 순간의 실패는 §13.8 이후 세 바퀴까지
   다시 낸다. 서버가 3분 넘게 돌아오지 않으면 그 커밋은 error 로 남고 사람이 손으로 낸다.
2. 감시는 같은 커밋을 한 번만 본다. 이 PR 이 머지되기 전에 `no_file` 로 넘긴 커밋은 기록에 남아 다시 보지 않는다. 새
   push 는 새 커밋이라 다시 본다.
3. 오너의 admin rcm 토큰과 권한이 넓은 gh 토큰을 쓴다(결정 199).
4. Mac mini 가 고장 났을 때 `workflow_dispatch` 의 결과는 `rcm/ci` 를 채우지 않는다(결정 196). 그때 머지하는 방법은
   오너가 정한다.
5. 포크 PR 은 감시가 내지 않아 `rcm/ci` 를 받지 못한다. 대체가 없으니 그런 PR 은 오너가 판단할 때까지 머지되지 않는다.

### 13.8 격리 검증(#245)이 남긴 후속

#245 의 격리 검증이 감시에서 셋, 가드와 시험 하네스에서 셋을 찾았다. 감시의 셋은 아래와 같다. 가드와 하네스의 셋은
`tools/guard_merge.py` · `pyproject.toml` · `scripts/mutcheck.py` 의 주석에 적었다.

첫째, 제출이 서버 재시작 순간에 실패하면 다시 낸다. 고치기 전에는 `server_unreachable` 로 실패한 제출에 error 를 한 번
올리고 그 커밋을 다룬 것으로 적었다. 대체가 없는 이 레포에서는 그 PR 의 머지가 다음 push 까지 막혔다. 이제 사유가
`server_unreachable` 이거나 `rcm` 이 60초 안에 끝나지 않았거나(`rcm_timeout`) 실행 파일을 못 띄웠으면(`rcm_missing`, 배포가 venv 를 다시 까는 순간일 수 있다) 감시는 상태 파일에 횟수를 적고 다음
바퀴에 다시 낸다. 그 사이에는 `pending queued · retrying submit (n/3)` 을 올린다. 세 번 다시 내도 실패하면 그때
`error unavailable · <사유>` 를 올린다. 설정 · 인증 실패(`rcm_auth` · `unknown_preset` · `ref_not_found`)는 다시 내도
결과가 같으니 예전처럼 곧바로 error 다. 다시 내기 전에 그 커밋의 가장 새 상태가 감시의 `retrying submit` 이 아니면
누군가 손으로 낸 것이다. 그때는 다시 내지 않는다. dev 에서 main 으로 가는 PR 의 head 는 dev 의 head 와 같은 sha 라서
한 바퀴에 같은 sha 를 두 번 만난다. 한 바퀴에는 한 번만 센다. 대체가 있는 레포에서는 GitHub 이 넘겨받는 때가 세 바퀴만큼
늦어진다. 그동안 상태는 `queued` 라 대기 잡은 `queue_seconds` 를 잰다.

둘째, `--check` 는 감시가 부르는 `rcm` 이 서비스 venv(`~/.local/share/rcm-venv`) 안에 있으면 WARN 을 적는다. 심볼릭
링크는 따라가서 판단한다. 배포는 그 venv 가 가리키는 checkout 을 바꾸고 서비스를 재시작한다. 그 사이에 감시가 그 `rcm`
을 부르면 제출이 실패할 수 있다. 감시는 클라이언트 venv(`~/.local/share/rcm-client-venv`)를 부르는 편이 낫다. 템플릿
`ci-watch.toml` 의 `rcm` 줄에도 그렇게 적었다. 운영의 `~/.local/bin/rcm` 은 2026-11-21 에 서비스 venv 를 가리키고
있었다. 운영 설정은 이 PR 이 바꾸지 않는다.

셋째, PR 이 닫히거나 머지되어 열린 PR 목록에서 head 가 사라지면 그 커밋의 잡이 아직 queued 일 때만 감시가 취소한다.
목적은 레인을 비우는 것이다. 도는 잡은 두어도 결과가 그 커밋에만 간다. 아래 셋 중 하나면 취소하지 않는다.

1. 잡의 요청자 이름표가 감시의 것(`ci-watch@<host>`)이 아니거나 합류자가 있다. 남의 검사가 그 잡에 걸려 있다.
2. 그 커밋이 지금 감시하는 브랜치의 head 이거나 push 로 본 적이 있다. dev push 의 제출이 같은 잡에 합류했을 수 있다.
   감시와 세션은 같은 오너 토큰을 쓰고, 같은 토큰의 두 번째 제출은 합류자 행을 남기지 않는다. 그래서 잡만 봐서는 모른다.
3. 그 바퀴에 열린 PR 목록을 끝까지 읽지 못했다. 목록은 100개씩 쪽으로 나뉘고 감시는 열 쪽까지 읽는다.
4. 서버가 `--ref` 를 더 새 커밋으로 풀어 받은 제출이다. 그 커밋은 아직 PR 목록에 없을 수 있다. 감시는 목록에 있는 head
   의 기록이 가리키는 새 커밋(`moved_to`)도 열린 head 로 본다(#246 격리 검증).

한 커밋은 한 번만 판정한다. 취소한 잡은 더 따라가지 않으므로 닫힌 PR 의 커밋에 `unavailable · cancelled` 를 올리지
않는다. 취소한 커밋이 다시 열린 PR 이나 브랜치의 head 로 나타나면 감시는 그 커밋을 새로 낸다. 다만 취소한 뒤에 다른
상태가 올라왔으면 누가 다룬 것이니 내지 않는다(#246 격리 검증). 같은 PR 에 새 push 가 와서 head 가 바뀐 경우도 옛 head 는 목록에서 사라지므로 같은 규칙으로 다룬다.

| 항목 | 시험 | 변이 |
|---|---|---|
| 다시 내기 | `test_h6_an_unreachable_server_is_retried_for_three_rounds_then_error` 외 셋, `test_h7_h23_…` · `test_h17_a_stuck_rcm_is_cut_off` | `m5r-watch-no-submit-retry` |
| 서비스 venv 경고 | `test_check_warns_when_the_watcher_calls_the_service_venv` | `m5r-watch-check-misses-service-venv` |
| 닫힌 PR 의 잡 취소 | `test_a_closed_pr_*` 넷, `test_an_unreadable_pull_list_cancels_nothing` | `m5r-watch-cancels-a-joined-job` |
| 새 커밋으로 풀린 제출 | `test_a_submission_the_server_moved_to_a_newer_commit_is_not_cancelled_as_gone`, `test_h9_…` | `m5r-watch-cancels-a-moved-submission` |
| 다시 열린 PR | `test_a_reopened_pr_*` 둘 | `m5r-watch-reopened-pr-stays-gone` |
| PR 목록의 모든 쪽 | `test_every_page_of_open_pull_requests_is_read`, `test_an_unread_later_page_cancels_nothing` | `m5r-watch-reads-one-page-of-pulls` |

## 부록 A. 오너가 만들 토큰 둘

감시 스크립트와 알림 훅이 쓰는 토큰 둘은 오너가 만든다(결정 186).

### A.1 GitHub fine-grained 토큰

**범위.** 이 토큰은 Mac mini 의 감시 스크립트(`rcm_ci_watch.py`,
launchd)와 알림 훅(`rcm_ci_status.py`, `[[notify]]`)이 쓴다. GitHub 쪽
대기 잡은 이 토큰을 쓰지 않고 워크플로의 `GITHUB_TOKEN` 을 쓴다. 종류는
fine-grained personal access token 이다. 리소스 소유자는 `monocsp` 이고
Repository access 는 「Only select repositories」로 골라
`monocsp/remote_ci_monitor` 하나만 둔다. Repository permissions 는 다섯만
준다.

- Commit statuses: Read and write. 상태 `rcm/ci` 를 읽고 쓴다.
- Contents: Read-only. 커밋에 든 `.rcm/ci.toml` 을 읽는다.
- Pull requests: Read-only. 열린 PR 목록과 head 를 읽는다.
- Variables: Read-only. 저장소 변수 `RCM_CI_MODE` 를 읽는다.
- Metadata: Read-only. 필수 권한이라 자동으로 붙는다.

나머지는 No access 로 두고 Account permissions 는 주지 않는다. 쓰기
권한은 상태 하나뿐이라서 이 토큰으로는 코드를 push 하거나 머지할 수 없다.

**만드는 절차.**

1. GitHub 웹의 Settings 에서 Developer settings, Personal access tokens,
   Fine-grained tokens 를 차례로 열고 Generate new token 을 누른다.
2. 이름은 `rcm-ci-watch (Mac mini)` 처럼 짓고 위의 범위대로 고른다.
3. 만료일은 1년 이하로 정하고 그 날짜를 기록해 둔다. 토큰이 만료되면
   감시 스크립트가 상태를 올리지 못한다. 그래도 대기 잡이 접수 문턱
   (`pickup_seconds`) 300초 뒤에 GitHub 으로 넘어가므로 검사는 멈추지 않는다.
4. 만든 값은 한 번만 보인다. 터미널 기록에 남지 않도록 클립보드에서
   바로 파일로 옮긴다.

   ```sh
   umask 077
   pbpaste > ~/.config/rcm/tokens/gh-monocsp-ci.token
   chmod 600 ~/.config/rcm/tokens/gh-monocsp-ci.token
   ```

   키체인은 launchd 아래에서 쓸 수 없어서 파일에 둔다. gh 의 활성 계정은
   바꾸지 않는다.
5. 아래 명령을 돌려 GitHub 행을 확인한다. 이 행은 권한 넷을 실제로
   불러 본다.

   ```sh
   python3 ~/.config/rcm/scripts/rcm_ci_watch.py --config ~/.config/rcm/ci-watch.toml --check
   ```

폐기하려면 같은 화면에서 Revoke 를 누른다. 그러면 감시 스크립트와 알림
훅이 실패하고 대체 경로로 GitHub 이 대신 돈다.

### A.2 rcm 클라이언트 토큰 `ci-watch`

**범위.** 이 토큰은 감시 스크립트가 `rcm run rcm-ci … --no-wait` 와
`rcm cancel N` 을 부를 때 쓴다. 알림 훅에는 rcm 토큰이 필요 없다. 훅이
부르는 `GET /jobs/{id}` 는 읽기이기 때문이다. 종류는 rcm 클라이언트
토큰이고 이름은 `ci-watch` 다. admin 이 아니므로 만들 때 `--admin` 을
주지 않는다. 서버 DB 에는 SHA-256 만 남고 값은 만들 때 한 번만 찍힌다.

이 토큰으로 할 수 있는 일은 넷이다.

- 아무 프리셋이나 제출한다. rcm 토큰에는 프리셋 범위가 없다. 그래서 이
  토큰을 GitHub 에 두지 않는다(§8).
- 우선순위를 프리셋 기본까지만 준다. `rcm-ci` 의 기본은 low 다.
- 자기가 낸 잡을 취소한다.
- 합류한 잡에서 자기 대기를 뺀다.

다음 일은 하지 못한다.

- 큐를 멈추거나 다시 돌리는 일
- 남의 잡을 취소하는 일
- 우선순위를 프리셋 기본보다 올리는 일
- 비밀을 쓰거나 지우는 일
- 점검 프리셋이 아닌 프리셋에 `--machine` 을 붙이는 일. 이 일은 admin 이
  필요하다.

같은 브랜치의 옛 잡을 치우는 supersede 는 같은 토큰 이름이 낸 잡끼리만
치운다. 그래서 감시 스크립트만 쓰는 이름이 따로 있어야 한다.

**만드는 절차.** 운영 DB 를 여는 명령이므로 서비스 venv 의 `rcm` 으로
친다. 다른 빌드는 DB 를 열면서 마이그레이션하고 레포의 가드가 그 실행을
막는다. `--config` 는 하위 명령 앞에 온다.

```sh
umask 077
<서비스 venv>/bin/rcm token --config ~/.config/rcm/server.toml add ci-watch \
  > ~/.config/rcm/tokens/ci-watch.rcm
chmod 600 ~/.config/rcm/tokens/ci-watch.rcm
```

파일을 열어 토큰 한 줄만 들어 있는지 확인한다. 다른 줄이 섞여 있으면
토큰 줄만 남긴다. `ci-watch.toml` 의 `rcm_token_file` 이 이 파일을
가리킨다. 확인은 `--check` 의 rcm 행으로 한다. 이 행은 서버 health 를
보고 `/api/whoami` 가 `{name: ci-watch, admin: false}` 를 돌려주는지 본다.

폐기는 아래 명령으로 한다. 그러면 감시 스크립트의 제출이 401 로 실패해
`unavailable` 이 올라가고 GitHub 이 대신 돈다.

```sh
<서비스 venv>/bin/rcm token --config ~/.config/rcm/server.toml revoke ci-watch
```
