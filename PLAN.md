# remote_ci_monitor — 계획서 (v2, 2026-09-04)

> 정본이다. 세션을 시작하면 끝까지 읽는다.
> **v2 는 방향 전환이다.** v1(오전)은 GitHub Actions 를 컨트롤 플레인으로 쓰는 관찰+디스패치 도구였다. 오너 검토에서 「GitHub 에 의존하지 않으면 좋겠다」가 나왔고, Codex 크로스리뷰(`docs/reviews/2026-09-04-codex-github-dependency.md`)를 거쳐 **도구가 큐와 실행을 직접 소유하는 로컬 잡 서버**로 바꿨다. GitHub 경로 설계는 커밋 `15e8220`(v1.1)에 남아 있고 M5 의 GitHub 백엔드를 만들 때 참고한다.
> ⛔ 는 사람이 정해야 하는 항목이다. 현재 열린 ⛔ 는 없다(「결정 항목」).

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
                                   └─ cancelled(대기 중 취소)          └─ cancelled(실행 중 취소) | lost(서버 재시작 중 죽음)
```

| 필드 | 뜻 |
|---|---|
| `id` | 서버가 발급하는 단조 증가 정수(순서 = 큐 순서) |
| `preset` · `inputs` | 실행할 프리셋 이름과 검증된 입력값 |
| `key` | 소요시간 버킷. `preset` + 프리셋이 `duration_key_inputs` 로 지정한 입력값(예 `gate:full`) |
| `source` | `{mode: "tree", base_sha, dirty, tree_hash, repo, bytes}` 또는 `{mode: "git_ref", repo, ref, sha}` |
| `requester` | `{name: 토큰 이름, label: "<이름>@<호스트>" 또는 --by 값}` |
| `state` · `created_at` · `started_at` · `finished_at` · `exit_code` · `summary` · `failed_step` | 서버 시계 기준 |

- **`rcm wait` 종료 코드**: `succeeded` 0 · `failed` 1 · `cancelled`/`timed_out` 2 · `lost`/조회 실패/`--timeout` 초과 3. 세션 스크립트는 이 코드로 바로 분기한다. 3 은 「모른다」이지 「실패」가 아니다(fail-open 금지).
- 잡은 서버 재시작을 넘어 살아남는다(`queued` 는 그대로, `running` 이던 것은 `lost`). 워크스페이스·로그·스냅샷은 보존 기간 뒤 정리한다.

## 큐 규칙 (순수 · `core/queue.py`)

- **순서**: `id` 오름차순 = 생성 순 FIFO. 우선순위는 없다(필요해지면 M5). `position` 을 1부터 싣는다.
- **레인**: `server.lanes`(기본 1). 레인이 비어 있어도 **concurrency 그룹**이 겹치면 못 올라간다 — 프리셋에 `concurrency_group = "devices"` 를 주면 같은 그룹의 잡은 동시에 하나만 돈다(시뮬레이터·에뮬레이터를 공유하는 QA·배포용). 막고 있는 잡을 `blocked_by` 로 보여준다.
- **합류**(`join_duplicates`): 활성 잡(`uploading`·`queued`·`running`) 중 같은 `preset` · 같은 `inputs` · 같은 소스 신원(`tree` 면 `tree_hash`, `git_ref` 면 `sha`)이 있으면 새 잡을 만들지 않고 그 잡 id 를 돌려준다(`joined: true`). 두 세션이 같은 코드를 확인하려는 것뿐이라 두 번 돌릴 이유가 없다. 스냅샷 업로드도 생략된다. `--no-join` 으로 끈다. (v1 의 GitHub 경로에선 inputs 를 비교할 수 없어 run 이름 규약에 기대야 했다 — 이제 정확히 비교한다.)
- **취소**: 자기 토큰의 잡만, `admin` 토큰은 전부. 대기 중이면 즉시 `cancelled`, 실행 중이면 SIGTERM → 유예 → SIGKILL.
- **표본**: 같은 `key` 의 완료 잡 중 `sample_policy`(기본 `success`) · `min_job_seconds`(30) 이상 · `sample_days`(45) 안. 소요는 `started_at`~`finished_at`(큐 대기는 안 섞인다 — 우리가 시각을 찍으니 v1 의 「run 시각 vs 잡 시각」 함정이 없다).
- **중앙값**: 키별, `min_samples`(2) 이상일 때만. 아니면 프리셋의 `expected_seconds` → `default_seconds`(600). 출력에 `source: measured|preset|default` 와 `sample_count`.
- **잔여**: `queued` → expected 전체. `running` → `max(expected − elapsed, floor 30초)`. `overdue = elapsed > expected`.
- **대기**: 레인 1 → 앞선 잔여 합. 레인 k → 잔여를 큰 순으로 가장 빨리 비는 레인에 얹는 그리디. 그룹 제약은 근사로 무시하고 UI 에 「그룹 대기」 표시만(정확한 스케줄 시뮬레이션은 M5).
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
- 워커가 항상 주는 env: `RCM_JOB_ID` · `RCM_PRESET` · `RCM_REQUESTER` · `RCM_SOURCE_MODE` · `RCM_BASE_SHA` · `RCM_DIRTY` · `RCM_WORKSPACE` · `RCM_LOG_FILE`.
- 프리셋 목록은 `GET /api/status.presets` 와 `rcm presets` 로 세션이 볼 수 있다(입력 스키마 포함).
- 설정 오류(모르는 키·argv 비어 있음·choices 없는 choice)는 서버 시작 시 **프리셋 이름과 키 이름**을 찍고 실패한다.

## 코드 전달

### `tree` — 작업 트리 스냅샷 (게이트 기본, 2026-09-04 오너 결정)

세션의 **지금 작업 트리**(미커밋·미푸시 포함)를 그대로 보낸다. 「dispatch 는 원격 HEAD 만 본다」 함정이 사라진다.

- **파일 선택**(순수 · `core/snapshot.py`): git 체크아웃이면 `git ls-files -z --cached --others --exclude-standard` (추적 + 무시되지 않은 미추적) 에서 작업 트리에 없는 것(삭제)을 빼고, `.git/` 은 항상 제외. `.rcmignore`(gitignore 문법)와 `--exclude` 를 더한다. git 이 아니면 `.rcmignore` 만. 심볼릭 링크는 링크로 담는다.
- **신원**: `base_sha = HEAD`, `dirty = 작업 트리가 HEAD 와 다른가`, `tree_hash = sha256(정렬된 (경로, 모드, 내용 sha256) 목록)`. 합류 판정과 감사에 쓴다. `repo = git remote get-url origin`(표시용).
- **전송**: `tar.gz` 를 `PUT /jobs/{id}/tree` 로 올린다(같은 HTTP·같은 Bearer). rsync·SSH 를 안 쓰는 이유: 두 번째 접속·인증 경로가 생기고 rsync 데몬·키 관리가 따라온다. 크기 상한 `max_snapshot_bytes`(기본 512MB) 초과는 413 + 「.rcmignore 로 빌드 산출물을 빼라」. 참고 팀의 앱 트리(에셋 포함 수십 MB)는 Tailscale 에서 수 초다. 내용 주소 캐시(이미 있는 파일은 안 보냄)는 M5.
- **서버 풀기**: `tarfile.extractall(filter="data")`(3.11.4+) — 절대 경로·`..`·바깥을 가리키는 링크·장치 파일을 거부한다. 워크스페이스 `<data_dir>/workspaces/<job_id>/`.

### `git_ref` — 원격 브랜치 (배포·릴리스용)

서버가 설정된 원격(`repo_url`)에서 `ref` 를 fetch 해 `sha` 를 확정하고 워크스페이스에 체크아웃한다(`--reference` 로 로컬 미러를 써 빠르게). 재현성·감사가 좋다. 세션은 `--ref` 만 보내고 트리를 안 올린다. 프리셋이 `source_modes = ["git_ref"]` 면 tree 요청은 400.

## 워커 실행 (`worker.py`)

- 레인마다 스레드 하나: `claim`(SQLite 트랜잭션으로 원자적) → 워크스페이스 준비 → `subprocess.Popen(argv, cwd=workspace, env=…, stdout=PIPE, stderr=STDOUT, start_new_session=True)` → 로그 줄을 파일에 쓰며 스텝 마커를 파싱 → 종료 코드로 상태 확정.
- 타임아웃·취소: 프로세스 그룹에 SIGTERM → `grace_seconds`(10) → SIGKILL. 자식이 만든 손자까지 죽이려고 `start_new_session` 을 쓴다.
- 로그: `<data_dir>/jobs/<id>/log.txt` 줄 단위 flush. 최근 `tail` 은 상태 JSON 에 싣고 전체는 `GET /jobs/{id}/log`. 로그엔 시크릿이 섞일 수 있어 **읽기에 그 잡의 토큰 또는 admin** 이 필요하다.
- ⚠️ 자식 프로세스의 stdout 버퍼링 때문에 마커가 늦게 도착한다. README 에 `PYTHONUNBUFFERED=1`·`stdbuf -oL`·`flutter --no-color` 같은 팁을 쓴다. 마커가 늦어도 잡 전체 경과는 정확하다.
- 정리: 성공 잡 워크스페이스는 완료 즉시 삭제(`keep_workspace_on_failure = true` 면 실패는 보존 기간까지), 로그·스냅샷은 `retention_days`(성공 14 · 실패 30).
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
- **읽기**(`/api/status`·`/events`·UI)는 기본 `none`(Tailscale/LAN 전제, 2026-09-04 오너 결정). `basic` 옵션은 TLS 프록시 뒤에서만. **잡 로그**는 예외로 항상 토큰이 필요하다(시크릿이 섞일 수 있다).
- **프리셋만 실행.** argv 배열, 셸 없음, 입력은 env 로만. 입력 길이·타입·choices 검증. 임의 명령 옵션은 만들지 않는다.
- 바인드 기본 `127.0.0.1`. Tailscale IP 나 `0.0.0.0` 은 명시. TLS 는 서버가 안 한다(Tailscale 이 암호화한다).
- 스냅샷: 크기 상한 · `tarfile` data 필터 · 워크스페이스 밖 쓰기 불가. `.git` 은 받지 않는다.
- 전용 OS 사용자 · sudo 없음 · 시크릿은 빌드 머신 파일에서 프리셋 스크립트가 읽는다(README 런북).
- 오류 응답에 스택·토큰·경로를 싣지 않는다. 요청 로그는 debug 에만.

## 서버 API (`server.py`)

| 라우트 | 인증 | 동작 |
|---|---|---|
| `POST /jobs` | 토큰 | `{preset, inputs, source, requester_label, join}` → 검증 → 합류면 `{job_id, joined: true}`, 아니면 새 잡(`uploading` 또는 `git_ref` 면 바로 `queued`) `{job_id, joined: false, upload: "/jobs/{id}/tree"}` |
| `PUT /jobs/{id}/tree` | 토큰(그 잡의) | 본문 tar.gz(`Content-Length` 필수, 상한) → 풀지 않고 저장만 → `queued` |
| `GET /jobs/{id}` | 없음 | 잡 스냅샷(스키마의 queue/recent 행과 같은 모양 + `log_tail`) |
| `GET /jobs/{id}/log?offset=N` | 토큰 | 로그 바이트 스트림(증분) |
| `GET /jobs/{id}/events` | 없음 | SSE: 상태 변화·스텝 마커·요약(로그 줄은 아님) |
| `POST /jobs/{id}/cancel` | 토큰(그 잡의 또는 admin) | 취소 |
| `GET /api/status` | `read_auth` | 전체 `StatusModel`. `ETag` 지원 |
| `GET /events` | `read_auth` | SSE: 큐 변화·호스트 표본(UI 용) |
| `GET /api/health` | 없음 | 워커 스레드 살아 있고 DB 열리면 200, 아니면 503 + 사유 |
| `GET /` · `/static/*` | `read_auth` | 정적 UI |

- `http.server.ThreadingHTTPServer`(표준 라이브러리). SSE 는 응답을 열어 두고 줄을 흘리는 스레드라 keep-alive 문제가 없다(요청당 스레드). **hardening**: 소켓 타임아웃(일반 10초, SSE·업로드는 별도) · `Content-Length` 필수, chunked 거부 · 동시 요청 `max_concurrent_requests`(32) 초과 503 · SSE 동시 연결 상한(16) · 정적 경로 정규화 · 405/400/413/401/403 명확히 · 예외는 500 한 줄.
- 상태 모델은 폴러가 아니라 **이벤트로 갱신**한다(잡 상태 변화·마커·호스트 표본이 들어올 때 모델을 다시 만들어 참조 교체). `/api/status` 는 항상 최신이다.

## 저장소 (`store.py` · SQLite WAL · 표준 `sqlite3`)

```
<data_dir>/                       # 기본 ~/.local/share/rcm (XDG), --data-dir
  rcm.sqlite3                     # jobs · events · tokens · duration_samples
  jobs/<id>/log.txt · tree.tar.gz · meta.json
  workspaces/<id>/                # 실행 중·실패 보존
  mirrors/<repo>/                 # git_ref 모드용 로컬 미러
```

- `jobs(id, preset, inputs_json, key, source_json, requester_name, requester_label, state, created_at, started_at, finished_at, exit_code, summary, failed_step, lane, tree_hash, sha)` · `events(job_id, at, kind, payload)` · `tokens(name, sha256, admin, created_at, revoked_at)`.
- 마이그레이션은 `PRAGMA user_version` 으로 번호를 매긴다. 시작 시 `running` → `lost` 로 정리한다.

## 설정

**서버**(`rcm serve --config`, 탐색: `--config` → `$RCM_CONFIG` → `./rcm.toml` → `~/.config/rcm/server.toml`). 우선순위 **플래그 > 환경변수(`RCM_<섹션>_<키>`) > 파일 > 기본값**.

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

[estimate]
sample_days = 45
min_samples = 2
min_job_seconds = 30
sample_policy = "success"           # "success" | "completed"
default_seconds = 600
floor_remaining_seconds = 30

[host]
interval_seconds = 5                # 하한 2
gpu = "auto"                        # "auto" | "off"
top_processes = 5

[display]
timezone = ""                       # IANA. 시작 시 zoneinfo 로 검증. 비면 서버 로컬 / 브라우저 로컬

[[repos]]                           # git_ref 모드용(선택)
name = "app"
url = "git@github.com:org/app.git"  # 어떤 git 호스팅이든. 빌드 머신의 git 자격을 쓴다

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
  "server": {"version": "0.1.0", "uptime_seconds": 8123, "lanes": 1, "last_error": null,
             "workers": [{"lane": 1, "state": "busy", "job_id": 412}]},
  "presets": [{"name": "gate", "description": "Full local gate", "source_modes": ["tree"], "concurrency_group": null,
               "inputs": [{"name": "scope", "type": "choice", "choices": ["full", "commit", "fast"], "default": "full"}]}],
  "pools": [{
    "name": "default", "lanes": 1,
    "queue": [{
      "position": 1, "id": 412, "preset": "gate", "key": "gate:full", "inputs": {"scope": "full"},
      "requester": {"name": "alice-laptop", "label": "alice@laptop"},
      "state": "running", "blocked_by": null,
      "source": {"mode": "tree", "repo": "org/app", "base_sha": "abc123…", "dirty": true, "tree_hash": "9f8e…", "bytes": 48213344},
      "created_at": "2026-09-04T00:50:40Z", "started_at": "2026-09-04T00:51:13Z",
      "estimate": {"expected_seconds": 369, "source": "measured", "sample_count": 7,
                   "elapsed_seconds": 59, "remaining_seconds": 310, "wait_seconds": 0,
                   "overdue": false, "finish_at": "2026-09-04T00:57:22Z"},
      "progress": {"timing": "as_received", "steps_total": 8, "steps_total_partial": false, "steps_done": 4,
                   "current_index": 5, "current_name": "test", "current_seconds": 51, "job_seconds": 59,
                   "failed_step": null,
                   "steps": [{"index": 1, "name": "analyze", "state": "done", "ok": true, "seconds": 12},
                             {"index": 5, "name": "test", "state": "running", "ok": null, "seconds": 51}]},
      "log_tail": ["[test] 3/9 packages…"],
      "url": "http://macmini:8787/#/jobs/412"
    }],
    "queue_error": null,
    "recent": [{"id": 411, "preset": "gate", "key": "gate:fast", "requester": {"name": "bob-desk", "label": "bob@desk"},
                "state": "failed", "exit_code": 1, "job_seconds": 62, "finished_at": "2026-09-04T00:47:03Z",
                "summary": "2 tests failed", "failed_step": "test", "url": "…"}],
    "recent_error": null,
    "medians": {"gate:full": {"seconds": 369, "sample_count": 7}},
    "hosts": [{"name": "macmini", "source": "local", "sampled_at": "2026-09-04T00:52:08Z", "age_seconds": 4, "stale": false,
               "os": "darwin", "cores": 10, "load": [3.48, 3.1, 2.9],
               "cpu": {"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
               "memory": {"total_bytes": 25769803776, "used_bytes": 15032385536},
               "gpu": {"util_pct": 13, "mem_used_bytes": 594411520, "mem_total_bytes": null, "source": "ioreg"}, "gpu_note": null,
               "top": [{"comm": "dart", "cpu": 180.4, "rss_mb": 500}]}],
    "hosts_error": null
  }]
}
```

규칙: 시각은 UTC ISO-8601(`Z`). 조회·수집 실패 섹션은 `null` + `*_error`. 모르는 숫자는 `null`. M0~M4 는 `pools` 가 한 개. 키 삭제·의미 변경은 `schema_version` 을 올리고 CHANGELOG 에 적는다.

## CLI (`cli.py`)

| 명령 | 하는 일 |
|---|---|
| `rcm run PRESET [-f K=V …] [--source tree\|git_ref] [--ref REF] [--by LABEL] [--no-join] [--no-wait] [--exclude PAT]` | 스냅샷 → 제출(합류) → 업로드 → 기본으로 `wait` 이어짐. stdout 에 JSON 한 줄, stderr 에 사람용 |
| `rcm wait --job ID [--timeout S]` | SSE 로 기다리며 stderr 에 위치·스텝·경과·ETA 갱신(TTY 면 한 줄 덮어쓰기), 끝나면 stdout JSON + **종료 코드 0/1/2/3**. SSE 가 끊기면 폴링(5초)으로 폴백 |
| `rcm eta (--job ID \| PRESET [-f K=V])` | 앞선 건수·대기·자기 소요·예상 완료·표본 출처 |
| `rcm top [--watch N] [--json]` | 한 화면(아래) |
| `rcm jobs [--mine] [--state S]` · `rcm logs ID [--follow]` · `rcm cancel ID` · `rcm presets` | 큐·로그·취소·프리셋 |
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

빌드 도구 없이 `index.html` + `app.js` + `style.css`. `GET /api/status` 한 번 + `GET /events` SSE 로 갱신(끊기면 10초 폴링). 문자열은 영어. `pools[]` 를 순회한다.

```
┌ rcm · macmini                                   ● worker busy · 4s ago · [live] ┐
│ QUEUE (lanes 1)                                                                 │
│  # │ state   │ key       │ source            │ requester    │ elapsed │ eta     │
│  1 │ ▶ run   │ gate:full │ abc123 +dirty     │ alice@laptop │ 0:59    │ 09:57 ▾ │
│      ├ step 5/8 · test · 51s     ████████░░░░░░  [log ▾]                        │
│  2 │ · queue │ qa:smoke  │ def456            │ bob@desk     │ wait 35s│ 10:05   │
│ RECENT  ❌ gate:fast bob 1m02s 09:47 "2 tests failed"   ✅ gate:full alice 5m50s │
│ HOST  macmini · 4s ago  load 3.48/10  CPU 21% ▮▮▮▯▯  mem 14/24GB ▮▮▮▮▮▯▯  GPU 13% │
└─────────────────────────────────────────────────────────────────────────────────┘
```

- 배지: `stale` · `overdue` · `blocked by #N (devices)` · `lost` · `hosts_error`. 초과 실행은 잔여 대신 경과를 강조.
- 로그 보기와 취소는 토큰이 필요하다 → UI 상단 「token」 입력(localStorage). 없으면 읽기 전용.
- 모바일 한 열 · 다크/라이트 `prefers-color-scheme` · 시간대는 `display_timezone` 또는 브라우저 로컬.

## fail-open 금지 (이 도구의 핵심 규칙)

아무도 안 쳐다보는 보조 화면일수록 조용히 고장나면 **틀린 값을 자신있게 보여주게 된다.**

- 수집·조회에 실패한 칸은 「실패」로 그린다. 실패를 0건·0/0·0초로 그리지 않는다. 모르는 숫자는 `null`.
- `rcm wait` 의 「모른다」(서버 연결 끊김·`lost`·타임아웃)는 3 이지 1 이 아니다. 게이트를 빨강으로 위장하지 않는다.
- 서버 재시작 중 죽은 잡은 `lost` 로 남긴다. 조용히 `queued` 로 되돌리거나 지우지 않는다(같은 트리를 다시 넣는 건 세션의 결정).
- 스텝 시각은 수신 시각임을 스키마가 밝힌다(`timing: "as_received"`).
- 서버 건강(워커 스레드·마지막 오류·호스트 표본 나이)을 JSON 과 화면 머리에 찍는다.
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
    status.py                  # 조각들 → StatusModel → to_json() (스키마 v1)
    render_text.py             # StatusModel → 터미널 문자열
  store.py                     # SQLite: jobs · events · tokens · samples · 마이그레이션 · claim
  worker.py                    # 레인 스레드: 워크스페이스 · Popen · 로그 · 마커 · 신호 · 정리
  materialize.py               # tree(tar 안전 추출) · git_ref(미러 fetch · 체크아웃)
  hostsample.py                # 샘플러 스레드(명령 실행·파일 읽기 → hostparse)
  server.py                    # ThreadingHTTPServer · 라우트 · 인증 · SSE · hardening
  client.py                    # 세션 쪽: 스냅샷 tar 만들기 · 제출 · 업로드 · SSE/폴링 wait
  web/                         # index.html · app.js · style.css
tests/  fixtures/ · test_*.py
examples/
  server.toml                  # 일반 프리셋 예시(gate / gate-fast / qa 를 셸 스크립트로)
  server.flutter-team.toml     # 참고 팀의 프리셋(local_ci 게이트 · 시뮬 QA 그룹 · 배포 git_ref) — 예시일 뿐
  client.toml · session/ci-gate.sh · launchd/ · systemd/
scripts/mutcheck.py
docs/reviews/
```

**의존성 원칙**: 런타임 의존성 0(`http.server` · `sqlite3` · `tarfile` · `subprocess` · `tomllib` · `zoneinfo` · `hmac`). 세션 클라이언트가 어느 컴퓨터에나 수 초에 깔리고, 빌드 머신에 올리는 서버가 가볍고, public 도구의 공급망 면적이 최소가 된다. 전제는 Tailscale/LAN 안의 내부 도구. 외부 바이너리: `git`(체크아웃이면), `tar` 아님(`tarfile`), macOS `vm_stat`/`top`/`ps`/`ioreg`, Linux `ps`/`nvidia-smi`(선택).

## 테스트·품질

- **픽스처**: 마커가 섞인 로그 3종(선언 있음·없음·실패) · 스냅샷용 임시 git 레포(추적·수정·미추적·무시·삭제·심링크) · macOS `vm_stat`/`top`/`ps`/`ioreg` · Linux `/proc/*`/`ps`/`nvidia-smi` 캡처(팀 정보 제거).
- **테스트**(M0~M1): `test_queue.py`(v1 의 21 시나리오 이식 + 그룹 대기 + 합류 키) · `test_progress.py`(마커 6 함정) · `test_snapshot.py` · `test_inputs.py` · `test_hostparse.py`(두 OS + GPU) · `test_status_schema.py`(`json.dumps` · null+`*_error` · pools 한 개) · `test_render_text.py`(빈 큐 vs 실패가 다르게) · `test_config.py`(우선순위·프리셋 오류 메시지·시간대) · `test_store.py`(enqueue·claim 원자성·재시작 lost·마이그레이션) · `test_worker.py`(가짜 프리셋 `sh -c` 로 성공·실패·타임아웃·취소·마커) · `test_server.py`(in-process: 401 · 합류 · 413 · tar 탈출 거부 · SSE 한 이벤트 · 로그 인증) · `test_client.py`(제출→업로드→wait 종료 코드 매핑, 서버 끊김 → 3).
- **뮤테이션 확인** `scripts/mutcheck.py`: `src/`+`tests/` 를 tmpdir 에 복사해 변이 하나를 넣고 그 복사본에서 pytest 가 **빨개지는지** 본다. 패턴이 없으면 그 자체로 실패. 3종 — ① 잔여 하한 제거 ② 합류 키에서 `inputs` 제외 ③ 재시작 시 `running` 을 `succeeded` 로. 셋 다 빨개져야 「검증됨」.
- **CI**(`ci.yml`): `unit`(matrix: py 3.11·3.13 × ubuntu, macos-latest 는 3.13 만 — `hostparse`·`snapshot`·`worker` 가 실제 OS 에서 돈다) → `ruff check` · `ruff format --check` · `pytest` · `mutcheck.py`. `secrets` → `gitleaks/gitleaks-action@v3`(개인 계정은 라이선스 불필요, 2026-09-04 확인). 집계 잡 **`test`**: `needs: [unit, secrets]` + `if: always()`, 두 `needs.*.result` 가 모두 `success` 가 아니면 `exit 1`.
- 스타일: ruff(기본 + `I`), 줄 100자, 타입 힌트 필수.

## 패키징·배포 (M4)

- `pyproject.toml`: hatchling · `dependencies = []` · dev `pytest`·`ruff` · scripts `rcm`·`remote-ci-monitor` · `web/` 패키지 데이터.
- 설치: 빌드 머신 `pipx install remote-ci-monitor && rcm serve` · 세션 `uvx --from remote-ci-monitor rcm run gate`.
- 서비스: `examples/launchd/com.remote-ci-monitor.server.plist` · `examples/systemd/rcm-server.service`. 빌드 머신 잠자기 금지 안내.
- Docker: Linux 빌드 머신용 서버 이미지만(macOS 툴체인은 Docker 밖). M4 에서 필요하면.
- 릴리스: 태그 `vX.Y.Z` → PyPI trusted publishing(OIDC). `main` 에서만.
- README(영어): 5분 셋업(서버 → 토큰 → 클라이언트 설정 → `rcm check` → `rcm run`) · 프리셋 쓰는 법과 마커 · 보안 런북(전용 사용자·Tailscale·시크릿 위치) · 「why the numbers can be wrong」(default 추정 · so-far 스텝 · 마커 지연 · stale · lost).

## 마일스톤과 완료 기준

- **M0 — 서버·큐·워커·run/wait** (한 세션 이상): 모듈 뼈대 · 설정+프리셋 스키마 · SQLite 저장소 · 워커(tree 모드) · 스냅샷 클라이언트 · `POST /jobs`·`PUT tree`·`GET /jobs/{id}`·`/api/health` · 토큰 · `rcm run`/`wait`(폴링) · 순수 계산 + 테스트 · `mutcheck.py` · CI. 완료 기준: 한 머신에서 루프백으로 `rcm run gate` 가 실제 스크립트를 돌리고 종료 코드 0/1/2/3 이 맞다 · 서버를 죽였다 살려도 큐가 남고 실행 중이던 잡은 `lost` 다 · 테스트 전부 통과 · 뮤테이션 3종 빨개짐 · CI 초록.
- **M1 — 보이는 것**: 호스트 자원(CPU·RAM·**GPU**) · 중앙값/ETA/합류 · 스텝 마커 진행 · SSE · `rcm eta`/`top`/`jobs`/`logs`/`cancel`/`presets` · `/api/status` 완성. 완료 기준: 다른 컴퓨터에서 Tailscale 로 `rcm run` 을 넣고 `rcm top` 에 위치·ETA·스텝·GPU 가 보인다 · 같은 트리를 두 세션이 넣으면 두 번째는 합류한다.
- **M2 — 웹 UI**: 정적 UI · SSE 갱신 · 배지 · 로그 뷰어(토큰) · 모바일 · 다크/라이트. 완료 기준: 폰에서 큐·스텝·자원이 읽히고 서버를 끊으면 stale 이 뜬다.
- **M3 — 운영**: `git_ref` 소스 · concurrency 그룹 · 보존 정리 · 타임아웃/취소 신호 검증 · launchd/systemd · macOS CI 잡. 완료 기준: 배포 프리셋이 원격 ref 로 돌고, QA 두 개가 그룹으로 직렬화된다.
- **M4 — 배포·문서**: pipx/uvx · PyPI 릴리스 · `examples/` · README. 완료 기준: 새 머신에서 README 만 보고 5분 안에 `rcm run` 이 된다.
- **M5 — 확장**: GitHub 백엔드(Actions run 관찰·dispatch — v1.1 설계 참조) · 원격 워커(빌드 머신 여러 대, 수집기 push) · 우선순위 · 내용 주소 스냅샷 캐시 · 알림.

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

## 참고 구현과 이전 설계

- `fmmc-tech/dolomood-app-renew`(로컬에선 `dolomood-ci-monitor` 워크트리)의 `scripts/remote_ci.sh`(dispatch·가드·합류·대기) · `ci_queue.py`(큐·중앙값·잔여 21 자기검증) · `ci_top.py`(진행률·파서·렌더 18 자기검증) · `docs/renew-guide/ci-cd/30-remote-dispatch.md`. **가져오는 것**: 큐·ETA 수식과 하한 · 실패/빈 큐 분리 · `top` 두 번째 표본 · 파서 픽스처 · 취소 대신 합류 · 시뮬 공유 직렬화(concurrency 그룹) · 요청자 라벨 `계정@호스트`. **버리는 것**: GitHub API 전부 · run 이름 규약 · `gh` · KST 상수 · `~/actions-runner` 판별 · 팀 스크립트 이름.
- v1/v1.1(GitHub 경로) 계획은 커밋 `9abef42`·`15e8220`. jobs API 함정 6개·rate limit 예산·큐 판정 규칙은 M5 GitHub 백엔드 때 그대로 쓴다.
- Codex 크로스리뷰 기록: `docs/reviews/2026-09-04-codex-plan-v1.md`(v1 설계) · `docs/reviews/2026-09-04-codex-github-dependency.md`(방향 전환).

---

## 세션 시작 프롬프트 (M0 — 복사해서 붙여 넣기)

```
이 레포(remote_ci_monitor)는 빌드 머신 한 대에 여러 컴퓨터의 세션이 잡을 던지면 서버가 자기 큐로
순차 실행하고, 대기 위치·예상 완료·스텝 진행·CPU/RAM/GPU 를 보여주며, 결과를 종료 코드로 돌려주는
도구다. GitHub 에 의존하지 않는다. PLAN.md 가 정본이다 — 먼저 끝까지 읽어라.

이번 세션 목표: M0 를 끝낸다 (PLAN.md 「마일스톤과 완료 기준」의 M0).
  1. 「패키지·모듈 구조」대로 뼈대 + pyproject(런타임 의존성 0) + 서버/클라이언트 설정 로딩 + 프리셋 스키마 검증
  2. store.py(SQLite WAL: jobs·events·tokens, claim 원자성, 시작 시 running→lost, user_version 마이그레이션)
  3. worker.py(tree 모드: tarfile data 필터로 풀기 · Popen argv · env · 로그 파일 · SIGTERM→SIGKILL · 타임아웃)
  4. server.py(POST /jobs · PUT /jobs/{id}/tree · GET /jobs/{id} · POST cancel · GET /api/health · 토큰 인증 · hardening)
  5. client.py + cli.py: rcm run(스냅샷 규칙·tree_hash·제출·합류·업로드) · rcm wait(폴링, 종료 코드 0/1/2/3, JSON 한 줄) ·
     rcm serve · rcm check · rcm token add|list|revoke
  6. 순수 계산(queue · progress 마커 · snapshot · inputs · status · render_text) + 픽스처 테스트.
     「진행 규칙」 표의 테스트 6개는 그 이름 그대로.
  7. CI: unit(matrix, ubuntu + macos) · secrets(gitleaks v3) · 집계 잡 `test`. `test` 이름은 main 룰셋 필수 체크라 바꾸지 마라.
  8. scripts/mutcheck.py 3종을 만들고 셋 다 빨개지는 걸 확인한 뒤에만 「검증됨」이라고 써라.

지킬 것:
  - 「반드시 지킬 것 — 이식성」. 팀 명령·머신 이름을 코드에 박지 말고 프리셋과 설정으로 받아라. 핵심 경로에서 GitHub 을 부르지 마라.
  - 「보안」. 쓰기는 토큰 필수, 프리셋만 실행, 셸 보간 금지, tar 탈출 차단, 오류 응답에 스택·토큰 금지.
  - 「fail-open 금지」. 모르는 값은 null, wait 의 「모른다」는 3, lost 는 lost.
  - 순수 계층(core/)은 I/O 도 시계도 안 본다. now 는 인자다.
  - 결정 항목은 PLAN.md 「결정 항목」대로. 벗어나야 할 이유가 생기면 그때 물어라.
  - 브랜치 정책: dev 에서 feat/<topic> 을 파서 작업하고 dev 로 PR. main·dev 직접 push 금지(룰셋이 막는다).
  - 식별자·UI 문자열·README·CLI 도움말은 영어, 주석·docstring 은 한국어. 커밋 메시지는 Conventional Commits.

끝나면: 무엇을 만들었는지, 테스트가 몇 개이고 어떤 뮤테이션으로 확인했는지, 루프백 e2e 에서 rcm run 이 무엇을
돌려줬는지(종료 코드 4종), M1 에서 먼저 정해야 할 것이 무엇인지 짧게 보고해라.
```
