# M5r 시나리오: CI 를 어디서 돌릴지 설정으로 고른다

> `docs/m5r-workplan.md`(셋째 판)로 만든 시나리오다. 베스트 4 · 에러 9 · 엣지 9, 모두 22개다. 시나리오마다 단계,
> 기대 결과, 그것을 잡는 시험 ID 를 적었다. 시험 ID 는 명세 §6 의 표와 같다(C · W · V · H · N · Y · J · K). 이 문서를
> 쓰며 새로 만든 ID 는 C14 · W16 · V5 · H21~H25 · N1~N15 · Y1~Y8 · K1 이고 §6 에 더했다.
>
> 모든 시나리오는 rcm 레포에 명세 §3.1 의 설정이 들어간 뒤(PR D 뒤)를 전제로 한다. 곧 dev 로 가는 PR 과 릴리스 PR 은
> rcm 에서만 돌고, 대체는 ubuntu 갈래만이며, 문턱은 30분이다. 「`test`」는 GitHub 의 필수 체크, 「`rcm/ci`」는 커밋
> 상태, 「잡」은 rcm 의 `rcm-ci` 잡이다. 「로그」는 감시 스크립트의 `~/Library/Logs/rcm/ci-watch.log` 와 서버의 잡
> 로그를 말한다.
>
> 2026-11-20 오너 결정(R15, 명세 §13 결정 196~200)으로 이 레포는 GitHub 대체를 쓰지 않는다. 필수 체크는 커밋
> 상태 `rcm/ci` 하나이고 `.rcm/ci.toml` 은 `mode = "rcm"`, `fallback = "none"` 이다. 그래서 시나리오마다 이 레포에
> 해당하는지를 머리에 적었다. 「이 레포 해당 없음」인 시나리오도 대체를 쓰는 다른 레포에는 그대로 해당한다.

## 1. 베스트

### B1. dev 로 가는 PR 이 rcm 에서 통과하고 머지된다

> 이 레포에서는 일부만 해당한다. 단계 2 의 `route` · 대기 잡과 단계 5 · 6 의 `test` 는 해당
> 없다(결정 196). 감시의 제출과 `queued` 는 그대로다. 진행과 결과는 운영 잡 스크립트가 「rcm job
> N running」 · 「rcm job N passed」로 올리고(결정 198) `tools/merge_pr.py`
> 가 `rcm/ci` 의 success 를 본다.

1. 세션이 브랜치를 push 하고 dev 로 PR 을 연다.
2. GitHub 의 `route` 가 head 커밋의 `.rcm/ci.toml` 을 읽어 `mode=rcm` · `late=ubuntu` 로 정한다. `rcm` 대기 잡이 뜬다.
3. 60초 안에 감시 스크립트가 그 PR 을 보고 `rcm run rcm-ci --source git_ref --ref <브랜치> --no-wait --priority low
   --link <PR> --link-label "PR #N"` 를 낸다.
4. `mac2` 워커가 잡을 집고 여덟 단계를 돈다(약 21분). 감시가 `running` 을 올린다.
5. 잡이 성공하면 알림 훅이 `passed` 를 올리고 대기 잡이 `pass` 를 낸다.
6. `gh pr checks --required` 가 초록이고 PR 을 머지한다.

| 기대 | 값 |
|---|---|
| `test` | success. `now` · `late` 는 건너뜀 |
| `rcm/ci` | `queued · #N` → `running · #N` → `passed · #N`. `target_url` 없음 |
| 잡 | `succeeded`, 풀 `mac2`, 단계 8/8 |
| 로그 | 감시: `submit` · `post queued` · `post running` 세 줄. 토큰 문자열 없음 |
| 시험 | C1 · H1 · H2 · H16 · W1 · N3 · V(판정 표 `rcm pass`) · Y1 · Y5 |

### B2. 머지 뒤 dev push 와 릴리스 PR

> 이 레포에서는 dev push 의 제출과 같은 sha 를 다시 내지 않는 것만 해당한다. 대기 잡과 main
> push 의 GitHub 판정은 해당 없다. main push 에서는 아무것도 돌지 않는다(결정 196).

1. B1 의 PR 이 merge 커밋으로 dev 에 들어간다. push 이벤트가 생긴다.
2. 감시가 `branches = ["dev"]` 의 head 를 보고, 그 커밋의 설정이 `push = ["dev"]` 이므로 잡을 낸다.
3. 나중에 dev 에서 main 으로 릴리스 PR 을 연다. PR head 는 dev head 와 같은 sha 다.
4. 릴리스 PR 의 대기 잡은 이미 있는 `passed` 를 첫 요청에서 읽는다. 감시는 그 sha 를 다시 내지 않는다.
5. 릴리스가 main 에 머지되면 main push 는 `push` 목록 밖이라 GitHub 의 모든 갈래로 돈다.

| 기대 | 값 |
|---|---|
| `test` | dev push · 릴리스 PR 모두 success. 릴리스 PR 의 대기 잡은 몇 초 만에 끝난다. main push 는 GitHub 판정 |
| `rcm/ci` | dev head 에 `passed` 하나 |
| 잡 | dev head 로 하나. 릴리스 PR 때문에 새 잡이 생기지 않는다 |
| 로그 | 감시: 릴리스 PR 의 sha 에 `skip known` |
| 시험 | H3 · H8 · W16 · C1 · C8 · Y8 |

### B3. 세션이 먼저 `rcm run` 을 낸 커밋

> 이 레포에도 그대로 해당한다. `running` · `passed` 자리에는 잡 스크립트의 문장이 온다(결정
> 198).

1. 세션이 push 하고 곧바로 `rcm run rcm-ci --ref <브랜치>` 를 친다(트리거 a).
2. 감시가 같은 sha 를 보고 낸다. 서버가 같은 프리셋 · 입력 · sha 의 잡에 합류시킨다(`joined: true`).
3. 감시가 그 잡 번호로 `queued` 를 올리고 따라간다.

| 기대 | 값 |
|---|---|
| `test` | success |
| `rcm/ci` | 세션이 낸 잡 번호로 `queued` → `running` → `passed` |
| 잡 | 하나. 합류자에 `ci-watch` |
| 로그 | 감시: `submit joined #N` |
| 시험 | H21 · W1 · N3 |

### B4. 주 1회 GitHub 전체 실행

> 이 레포 해당 없음. 주 1회 GitHub 실행을 두지 않는다(결정 196).

1. 월요일 03시(KST)에 `schedule` 이 main 의 `ci.yml` 로 실행을 만든다.
2. `route` 가 `mode=github` · `now=ubuntu,macos` · `ref=dev` 로 정한다. `rcm` 대기 잡은 건너뛴다.
3. `checks.yml` 이 dev 를 체크아웃해 ubuntu 3.11 · 3.13, macOS 3.13, smoke, secrets 를 돌린다.
4. 모두 통과하면 `ran=ubuntu,macos` 이고 `test` 가 success 다. 하나라도 실패하면 GitHub 이 워크플로 실패 알림을 보낸다.

| 기대 | 값 |
|---|---|
| `test` | 통과 또는 실패. 어느 PR 에도 붙지 않아 머지를 막지 않는다 |
| `rcm/ci` | 바뀌지 않는다 |
| 잡 | 없다 |
| 로그 | 실행 요약에 갈래별 결과와 `ref=dev` |
| 시험 | C2 · V5 · V(판정 표 `github`) · Y5 |

## 2. 에러

### E1. rcm 이 진짜 실패를 낸다

> 이 레포에서는 잡 스크립트가 `failure` 「rcm job N failed at <단계>」를 올린다. 대기 잡과
> `late` 는 해당 없다(결정 196 · 198).

1. 시험 하나를 깨뜨린 커밋을 push 한다.
2. 잡의 `test` 단계가 종료 1 로 끝나고 `::rcm::fail::` 로 깨진 시험 이름을 찍는다.
3. 훅이 `failed` 를 올린다. 대기 잡이 `fail` 을 내고 종료 1 로 끝난다.

| 기대 | 값 |
|---|---|
| `test` | failure, 요약에 `rcm #N failed`. `late` 는 돌지 않는다 |
| `rcm/ci` | `failed · #N · test` |
| 잡 | `failed`, 종료 1, 실패 스텝 `test`, 실패 이름에 깨진 시험 |
| 로그 | 잡 로그에 pytest 의 실패 줄 |
| 시험 | J3 · N5 · W2 · V(판정 표 `rcm fail`) |

### E2. 빌드 머신 환경 탓 실패(종료 75)

> 이 레포 해당 없음. 운영 잡 스크립트에는 종료 75 단계가 없어 준비 단계의 실패도 `failure` 로
> 올라간다. 대신 돌 GitHub 도 없다. 환경을 고친 뒤 손으로 다시 낸다(결정 196).

1. PyPI 에 닿지 못해 `venv` 단계의 `pip install` 이 실패한다(또는 디스크가 찼다).
2. 잡 스크립트가 종료 75 로 끝난다. 훅이 `unavailable · infra at venv` 를 올린다.
3. 대기 잡이 `fallback`(`unavailable`)을 내고 `late` 가 ubuntu 갈래를 돌린다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로. 통과하면 success, 요약에 대체 사유 `unavailable` |
| `rcm/ci` | `unavailable · #N · infra at venv` (빨간 X 로 보이지만 필수가 아니다) |
| 잡 | `failed`, 종료 75, 실패 스텝 `venv` |
| 로그 | 잡 로그에 pip 의 오류 |
| 시험 | J5 · N4 · C13 · W6 · V(판정 표 `rcm fallback`) |

### E3. 운영 서버가 멈춰 있다

> 이 레포에서는 감시가 `unavailable · server_unreachable` 을 올리는 데까지만 해당한다.
> `late` 는 없다. 서버가 돌아온 뒤 사람이 손으로 낸다(결정 197).

1. 서버 프로세스가 죽은 상태에서 push 한다.
2. 감시의 `rcm run` 이 종료 2 와 `rcm: cannot read presets: cannot reach …` 로 끝난다. 감시가
   `unavailable · server_unreachable` 을 올린다.
3. 대기 잡이 바로 `fallback` 하고 `late` 가 돈다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로 |
| `rcm/ci` | `unavailable · server_unreachable`, 잡 번호 없음 |
| 잡 | 없다 |
| 로그 | 감시: `submit failed rc=2 server_unreachable` |
| 시험 | H6 · W6 |

### E4. 감시 스크립트가 죽었다

> 이 레포 해당 없음. `no_pickup` 으로 넘길 대기 잡이 없다. 감시가 죽으면 `rcm/ci` 가 붙지 않아
> 머지되지 않는다. 사람이 손으로 내고 감시를 살린다(결정 196 · 200).

1. launchd 에이전트가 멈춘 상태에서 push 한다. 아무도 잡을 내지 않는다.
2. 대기 잡이 300초 동안 상태를 못 보고 `fallback`(`no_pickup`)을 낸다. `taken-over` 는 올리지 않는다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로, 요약에 `no_pickup` |
| `rcm/ci` | 없다 |
| 잡 | 없다 |
| 로그 | 감시 로그에 그 시간대의 줄이 없다. 운영 반영 뒤에는 오너가 `launchctl` 로 확인한다 |
| 시험 | W3 · V(판정 표 `rcm fallback`) |

### E5. 토큰이 만료되거나 폐기되었다

> 이 레포에서는 전용 토큰 대신 gh 키체인 토큰과 오너의 `client.toml` 토큰을 쓴다(결정 199). 1
> · 2 의 `no_pickup` 은 해당 없다. 상태가 붙지 않고 PR 은 기다린다.

1. GitHub 토큰이 만료된 채 push 한다. 감시의 GitHub 요청이 401 을 받는다. 감시는 제출도 상태도 하지 않는다.
2. 대기 잡은 300초 뒤 `no_pickup` 으로 넘긴다.
3. 따로, rcm 토큰 `ci-watch` 를 폐기했다면 GitHub 토큰은 살아 있으므로 감시가 `unavailable · rcm_auth` 를 올리고 대기
   잡이 바로 넘긴다.
4. 이미 돌던 잡이 끝날 때 GitHub 토큰이 만료돼 있으면 훅이 종료 1 로 끝나 서버의 `notify_failures` 가 오른다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로 |
| `rcm/ci` | 1 · 2: 없다. 3: `unavailable · rcm_auth` |
| 잡 | 1 · 3: 없다. 4: 잡은 제 결과로 끝나지만 상태가 안 붙는다 |
| 로그 | 감시: 바퀴마다 `github_auth` 한 줄, 늘지 않는다. `--check` 의 GitHub 행 FAIL. 훅: 토큰 문자열 없이 401 한 줄 |
| 시험 | H22 · H23 · H15 · N12 · W3 |

### E6. 사람이 「Re-run」 으로 rcm 의 빨강을 넘긴다

> 이 레포 해당 없음. 다시 돌릴 대기 잡이 없다. rcm 의 빨강이 틀렸으면 고친 커밋을 push 하거나 손으로
> 다시 낸다(결정 196).

1. E1 처럼 `rcm/ci` 가 `failed` 이고 `test` 가 빨강이다.
2. 사람이 rcm 의 빨강이 틀렸다고 보고 그 실행을 「Re-run」 한다.
3. `run_attempt=2` 라서 대기 잡이 API 를 부르지 않고 `fallback`(`rerun`)을 낸다. `late` 가 ubuntu 갈래로 판정한다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로. 통과하면 success, 요약에 `rerun` |
| `rcm/ci` | `failed` 그대로 |
| 잡 | 바뀌지 않는다 |
| 로그 | 실행 요약에 「탈출구로 판정했다」 |
| 시험 | W7 · V3 · V4 |

### E7. 30분 넘게 큐에서 기다린다

> 이 레포 해당 없음. 30분 문턱과 `taken-over` 가 없다. `rcm-ci` 는 `mac2` 풀에서 제
> 차례를 기다린다(결정 196).

1. `rcm-ci` 잡 하나가 도는 동안 다른 브랜치의 커밋이 들어와 `queued` 로 30분을 넘긴다.
2. 대기 잡이 `taken-over · GitHub Actions run <id>` 를 올리고 `fallback`(`queued_too_long`)한다.
3. 다음 바퀴에 감시가 가장 새 상태 `taken-over` 를 보고 `rcm cancel N` 한다. 훅은 `taken-over` 를 덮지 않는다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로, 요약에 `queued_too_long` |
| `rcm/ci` | `queued · #N` → `taken-over` |
| 잡 | `cancelled`(사람 취소가 아니라 감시의 취소) |
| 로그 | 감시: `cancel #N taken-over`. 훅: `skip taken-over` |
| 시험 | W4 · H11 · N2 |

### E8. 잡이 너무 오래 돈다

> 이 레포에서는 `timed_out` 이면 잡 스크립트가 결과를 못 올리므로 감시가 120초 뒤
> `unavailable · #N · timed out` 을 올린다(결정 198 규칙 3). 대기 잡의
> `run_seconds` 울타리는 해당 없다.

1. 시험이 멈추는 회귀가 들어와 잡이 3300초(프리셋 timeout)를 넘긴다.
2. rcm 이 `timed_out` 으로 끝내고 훅이 `unavailable · timed out` 을 올린다. 대기 잡이 넘긴다.
3. 훅이 놓쳤다면 대기 잡의 `run_seconds`(3600초)가 바깥 울타리다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래 결과대로. 그 회귀가 macOS 에서만 멈추면 통과할 수 있다(명세 §2.4 둘째 원칙) |
| `rcm/ci` | `running · #N` → `unavailable · #N · timed out` |
| 잡 | `timed_out` |
| 로그 | 잡 로그의 마지막 단계 `test` |
| 시험 | N7 · W5 · W6 |

### E9. 운영 rcm 이 고장 났다

> 이 레포에서는 1 이 해당 없다. 대체가 없어 감시는 멈춘 잡을 취소하지 않고 기다린다(결정 197). 2 에서
> 저장소 변수를 걸면 감시가 내지 않고 GitHub 도 대신 돌지 않는다. 오너 지시의
> `workflow_dispatch` 뿐이다(결정 196).

세 갈래로 나눈다.

1. `mac2-worker` 가 죽었다. 잡이 `worker_down` 으로 120초를 넘기면 감시가 `unavailable · worker_down` 을 올리고 잡을
   취소한다. 큐가 정지(`paused`)된 경우는 300초 뒤 같은 일을 한다.
2. rcm 이 환경 탓인데 시험 단계 안에서 깨져 엉뚱한 빨강을 낸다. dev 머지가 멈춘다. 오너(급하면 세션, PR 댓글로
   남긴다)가 저장소 변수 `RCM_CI_MODE=github` 를 건다. 다음 실행부터 `route` 와 감시가 모두 `github` 로 정한다.
3. rcm 이 엉뚱한 초록을 낸다. 이 경우는 시험이 없다. 주 1회 실행(B4)이 다시 본다. 명세 §3.4 의 알고 고른 위험이다.

| 기대 | 값 |
|---|---|
| `test` | 1: ubuntu 갈래로 판정. 2: 변수를 건 뒤에는 GitHub 의 모든 갈래로 판정(macOS 포함) |
| `rcm/ci` | 1: `unavailable · worker_down` 또는 `paused`. 2: 변수를 건 뒤에는 새로 붙지 않는다 |
| 잡 | 1: `cancelled`. 2: 변수를 건 뒤에는 생기지 않는다 |
| 로그 | 1: 감시 `cancel #N worker_down`. 2: 감시 `skip override=github` |
| 시험 | H10 · C4 · H5 |

## 3. 엣지

### X1. 같은 sha 가 두 번 들어온다

> 이 레포에도 그대로 해당한다. 둘째 실행의 대기 잡은 없다.

PR 의 head 와 dev push 의 head 가 같거나(B2), 같은 커밋을 두 PR 이 가리킨다. 감시는 sha 로 한 번만 낸다. 둘째
실행의 대기 잡은 이미 있는 상태를 읽는다.

| 기대 | 값 |
|---|---|
| `test` | 두 실행 모두 같은 판정 |
| `rcm/ci` | 하나 |
| 잡 | 하나 |
| 로그 | 감시: 둘째에 `skip known` |
| 시험 | H8 · W16 |

### X2. 새 push 가 앞 잡을 치운다

> 이 레포에서는 sha1 의 `superseded` 를 잡 스크립트가 못 올리므로 감시가 120초 뒤 올린다(결정
> 198 규칙 3). GitHub 의 `concurrency` 는 해당 없다.

1. sha1 의 잡이 도는 중에 같은 브랜치에 sha2 를 push 한다.
2. 감시가 sha2 를 낸다. 서버의 `supersede = "all"` 이 sha1 의 잡을 취소한다.
3. 훅이 sha1 에 `superseded · #1 · by #2` 를 올린다. sha1 의 GitHub 실행은 `concurrency` 가 먼저 취소했다.

| 기대 | 값 |
|---|---|
| `test` | sha1: cancelled(PR 에서 사라진다). sha2: 제 판정 |
| `rcm/ci` | sha1 `superseded`, sha2 `queued` 부터 |
| 잡 | #1 `cancelled`(`cancelled_by=superseded`), #2 정상 |
| 로그 | 감시: sha2 `submit`, sha1 은 다시 내지 않는다 |
| 시험 | H24 · N9 · W6 · Y6 |

### X3. 포크에서 온 PR

> 이 레포 해당 없음. 감시는 그대로 포크 PR 을 내지 않지만 GitHub 판정도 없어서 `rcm/ci` 가 붙지
> 않는다. 오너가 판단할 때까지 머지되지 않는다(명세 §13.7).

`route` 가 `mode=github` · `why=fork` 로 정하고 모든 갈래(macOS 포함)를 GitHub 에서 돌린다. 감시는 그 PR 을 보지 않는다.

| 기대 | 값 |
|---|---|
| `test` | GitHub 판정 |
| `rcm/ci` | 없다 |
| 잡 | 없다 |
| 로그 | 감시 로그에 그 PR 이 없다 |
| 시험 | C3 · H4 |

### X4. Dependabot 과 지워진 포크

> 이 레포 해당 없음. X3 와 같다.

PR 을 연 계정이 `dependabot[bot]` 이거나 head 레포가 `null` 이다. X3 와 같이 다룬다. 이 PR 의 `GITHUB_TOKEN` 은 읽기
전용이라 `taken-over` 를 쓰면 403 이 나는데, `github` 모드라 쓸 일이 없다.

| 기대 | 값 |
|---|---|
| `test` | GitHub 판정 |
| `rcm/ci` | 없다 |
| 잡 | 없다 |
| 로그 | 없다 |
| 시험 | C11 · H4 |

### X5. 갈래 이름 오타로 아무것도 안 돈다

> 이 레포 해당 없음. 갈래와 `checks.yml` 이 없다(결정 196).

`checks.yml` 의 `if` 에 `ubunut` 처럼 오타가 있거나 `route` 가 모르는 갈래 이름을 넘긴다.

1. `legs` 잡이 모르는 이름이나 빈 값을 보면 실패한다.
2. 오타가 `if` 쪽에만 있어 안의 잡이 전부 건너뛰면 호출 잡은 success 로 보이지만 `ran` 이 빈다. `verdict` 가 실패시킨다.
3. `.rcm/ci.toml` 의 `platforms` 에 모르는 키가 있으면 `bad_file` 로 `github` 가 된다.

| 기대 | 값 |
|---|---|
| `test` | 1 · 2: failure, 요약에 빠진 갈래. 3: GitHub 판정과 경고 주석 |
| `rcm/ci` | 대체 상황이면 `unavailable` 등 그 전의 상태 그대로 |
| 잡 | 영향 없음 |
| 로그 | 실행 요약에 `ran` 과 기대 갈래 |
| 시험 | Y4 · V2 · V5 · C6 |

### X6. squash 병합으로 dev head 가 PR head 와 다르다

> 이 레포에도 감시가 sha2 를 따로 내는 것은 해당한다. 대기 잡은 없다.

1. PR head(sha1)가 rcm 에서 `passed` 를 받고 squash 로 머지된다. dev 의 새 head 는 sha2 다.
2. dev push 이벤트의 대기 잡이 sha2 를 기다린다. 감시가 sha2 를 따로 낸다.
3. sha2 의 결과는 이미 머지된 뒤라 아무것도 막지 않는다. 깨졌다면 다음 PR 이 그것을 물려받는다.

| 기대 | 값 |
|---|---|
| `test` | sha2 의 dev push 실행이 제 판정을 낸다 |
| `rcm/ci` | sha1 `passed`, sha2 따로 |
| 잡 | 둘 |
| 로그 | 감시: sha2 `submit` |
| 시험 | H25 · H3 |

### X7. 저장소 변수가 없다

> 이 레포에도 감시 쪽은 그대로 해당한다. 워크플로 쪽은 없다.

`RCM_CI_MODE` 를 한 번도 만들지 않았다. 감시의 변수 API 는 404 를 받고, 워크플로는 빈 문자열을 넘긴다. 둘 다 덮어쓰기가
없는 것으로 보고 파일의 `mode` 대로 간다.

| 기대 | 값 |
|---|---|
| `test` | B1 과 같다 |
| `rcm/ci` | B1 과 같다 |
| 잡 | B1 과 같다 |
| 로그 | 경고 없음 |
| 시험 | H20 · C14 |

### X8. 시계가 어긋났다

> 이 레포 해당 없음. 시계를 읽을 대기 잡이 없다.

GitHub 러너의 시계가 몇 분, 혹은 몇 시간 어긋났다. 대기 잡은 경과를 상태의 `created_at` 과 GitHub 응답의 `Date`
헤더로 재므로 러너 시계를 쓰지 않는다. 감시의 120초 · 300초 판단은 Mac mini 한 시계 안의 차이라 어긋남과 상관없다.

| 기대 | 값 |
|---|---|
| `test` | 시계가 맞을 때와 같다 |
| `rcm/ci` | 같다 |
| 잡 | 같다 |
| 로그 | 없다 |
| 시험 | W15 |

### X9. 대체로 머지되는 커밋은 macOS 를 거치지 않는다

> 이 레포 해당 없음. 대체가 없어 GitHub 이 대신 도는 일이 없다(결정 196).

E2 · E3 · E4 · E7 · E8 처럼 GitHub 이 대신 돌면 ubuntu 갈래만 돈다(결정 195). macOS 에서만 깨지는 변경이 그대로 dev 에
들어갈 수 있다. 다음 dev push 의 rcm 검사나 주 1회 실행(B4)에서 드러난다.

| 기대 | 값 |
|---|---|
| `test` | ubuntu 갈래로 success |
| `rcm/ci` | 대체 사유 |
| 잡 | 대체 사유대로 |
| 로그 | 실행 요약의 `ran=ubuntu` |
| 시험 | C1(`late=ubuntu`) · V(판정 표 `rcm fallback`) |

## 4. 구현 PR 마다 첫 커밋에 넣을 시험

시험을 먼저 쓴다. 각 PR 의 첫 커밋은 아래 시험만 담고 빨갛게 실패해야 한다. 둘째 커밋부터 구현이다. PR 이름은 명세
§9 의 것이다. 조정 세션이 부르는 이름으로는 A 가 「감시 스크립트 · 훅」, B 가 「워크플로 · 실기」, C 가 「운영」, D 가
「모드 전환」이다.

| PR | 첫 커밋의 시험 | 가짜와 도구 |
|---|---|---|
| A `feat/m5r-ci-connect-skill` | C1~C14 · W1~W16 · V1~V5 와 판정 표 아홉 줄 · H1~H25 · N1~N15 · K1, 그리고 명세 §6 의 mutcheck 변이 아홉 개(변이는 구현이 들어온 뒤 빨강으로 바뀌는지 본다) | `tests/ci_fakes.py`(가짜 GitHub, `127.0.0.1` · 역방향 DNS 끔 · 주입 시계 · 304 세기), 가짜 `rcm` 실행 파일, 실제 rcm 서버는 임시 `data_dir` |
| B `ci/m5r-rcm-repo-waits-for-rcm` | Y1~Y7 · J1~J6. Y8 은 `mode = "github"` 의 기대값으로 넣는다 | 작은 가짜 프로젝트와 PATH 앞의 가짜 `pytest` · `ruff` · `gitleaks` · `node` · `smoke_install.sh`. 그다음 draft 실험(명세 §7 1~11) |
| C 운영(오너) | 시험이 아니라 확인이다. `rcm_ci_watch.py --check` 가 전부 OK, `rcm check` 의 `rcm-ci` 행, 운영에서 `rcm run rcm-ci --ref <B 의 브랜치>` 한 번이 `mac2` 에서 여덟 단계를 돌고 훅이 `passed` 를 올린다 | 부록 A 의 토큰 둘 |
| D `ci/m5r-rcm-repo-switches-to-rcm` | Y8 의 기대값을 §3.1(`mode=rcm` · `late=ubuntu` · macOS `rcm-only`)로 바꾼다. 파일을 고치기 전에는 빨강이다 | 없음 |

E(계획서 결정 행)는 문서뿐이라 시험이 없다.
