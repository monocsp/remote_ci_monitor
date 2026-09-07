# 계획서 준수 체크리스트 (PLAN.md v2.3 ↔ 구현 v0.1.0)

> 목적: PLAN.md 가 「반드시」라고 적은 규칙과 각 절의 동작이 **실제 코드·실제 실행**에서 지켜지는지 항목마다 확인한다. 테스트가 있다고 통과가 아니다 — 항목의 「확인 방법」을 실제로 돌려 관찰한 값으로 PASS/FAIL 을 매긴다. 판정은 `docs/acceptance/reports/` 에 남긴다(양식은 맨 아래).
>
> 표기: **P** 규칙의 PLAN 절 · **확인 방법** 실제로 할 일 · **기대** 통과 기준. 「코드 읽기」로만 되는 항목은 그렇게 적었다.

## A. 이식성 · 의존성 (P 「반드시 지킬 것 — 이식성」「패키지·모듈 구조」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| A1 | 특정 머신·계정·팀 규약·팀 명령을 코드에 박지 않는다 | `grep -rniE "dolomood|macmini-admin|fmmc|local_ci|flutter|KST|Asia/Seoul" src/` | src/ 에 0건(주석의 예시 문구 제외 — 있으면 목록으로 보고) |
| A2 | 핵심 경로가 GitHub 을 부르지 않는다 | `grep -rniE "github|api\.github|gh " src/remote_ci_monitor/*.py src/remote_ci_monitor/core/*.py` | 0건(예시 URL `git@github.com:org/app.git` 같은 문서 문자열만 허용) |
| A3 | 런타임 의존성 0 | `pyproject` 의 `dependencies == []`; wheel METADATA 에 `Requires-Dist` 없음; `python -X importtime -c "import remote_ci_monitor.cli"` 에서 표준 라이브러리 밖 모듈 없음 | 셋 다 |
| A4 | Python 3.11+ · 표준 라이브러리만 | `grep -rn "^import\|^from" src/ \| grep -vE "remote_ci_monitor|__future__"` 로 모듈 목록을 만들고 전부 stdlib 인지 | 전부 stdlib |
| A5 | macOS · Linux 지원, Windows 범위 밖 명시 | README/PLAN 에 Windows 범위 밖 문구; `hostsample.py` 가 darwin/linux 분기 | 둘 다 |
| A6 | 설치 한 줄 · 서버·클라이언트 같은 패키지 | `pipx install <wheel>` 뒤 `rcm serve --help` 와 `rcm run --help` 둘 다 | 둘 다 |
| A7 | 세션 쪽은 SSH·rsync 를 요구하지 않는다 | `grep -rniE "ssh|rsync" src/remote_ci_monitor/client.py src/remote_ci_monitor/cli.py` | 0건(gitops 의 GIT_SSH 통과는 서버 쪽) |
| A8 | 시크릿은 env·설정으로만, 커밋 금지 | `.gitignore` 에 토큰·DB 경로; gitleaks CI 잡; `git log -p --all -S "RCM_TOKEN=" -- '*.toml' '*.sh'` 에 실제 값 없음 | 없음 |
| A9 | 주석·docstring 한국어, 식별자·README·UI·CLI 도움말 영어 | `rcm --help` 와 각 부명령 `--help` 영어; `grep -rn "def \|class " src/ \| grep -P "[가-힣]"` 0건; 웹 UI 문자열 영어 | 전부 |
| A10 | 순수 계층(`core/`)은 I/O·시계를 안 본다 | `grep -rnE "import (os|subprocess|socket|sqlite3|time|http|threading)|datetime\.now|time\.time" src/remote_ci_monitor/core/` | 0건(`datetime` 타입 import 만 허용) |
| A11 | 모듈 구조가 PLAN 「패키지·모듈 구조」 트리와 같다 | 트리 대조 | 빠진 파일·추가된 파일 목록으로 보고(추가는 PLAN 에 적혀 있어야) |

## B. 잡 모델 · 생명주기 (P 「잡 모델과 생명주기」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| B1 | 상태 집합 uploading·queued·running·cancelling·succeeded·failed·timed_out·cancelled·lost | `core/model.py` 상수; `/api/status` 에서 관찰 | 같다 |
| B2 | `rcm wait` 종료 코드 0/1/2/3 · `cancelling` 은 계속 기다린다 | 프리셋 4종(성공·실패·`sleep 300` 취소·`timeout_seconds=2`)을 실제로 돌려 `echo $?` | 0 · 1 · 2 · 2; `lost`/서버 다운 3 |
| B3 | 큐에서 조용히 사라지는 잡이 없다 — 업로드 포기·413·tar 거부·프리셋 소멸·부분 수신 중단·재시작 중 uploading | 각 경로를 재현: (a) `max_snapshot_bytes=1000` 에 큰 트리 → 413 (b) 손으로 만든 절대 경로 tar 를 `PUT` (c) 제출 뒤 서버 설정에서 프리셋 삭제·재시작 (d) `PUT` 도중 연결 끊기(`curl --max-time 1` + 큰 본문) (e) uploading 잡이 있는 채 서버 재시작 | 전부 `recent` 에 `cancelled`/`failed` + summary 문구(PLAN 문구와 대조) |
| B4 | 재시작 시 running·cancelling → lost(`lane` 비움, summary 「server restarted …」), queued 는 그대로 | `sleep 300` 잡 실행 중 `kill -9` 서버 → 재시작 | `lost` + summary; 대기 잡은 이어서 실행 |
| B5 | 합류자 취소는 자기 대기만, 요청자 취소는 합류자 wait 가 2 | 두 토큰으로 같은 트리 제출 → 각각 cancel | `left: true` / 합류자 `rcm wait` 종료 2 |
| B6 | `transitions[]` 가 events 에서 만들어진다 | `GET /jobs/{id}` 의 transitions 순서·시각 | uploading → queued → running → 종료, 시각 단조 |
| B7 | `source` 모양이 tree/git_ref 별로 PLAN 표와 같다 | `GET /jobs/{id}` JSON 키 대조 | tree: mode·repo·base_sha·dirty·tree_hash·bytes·received_bytes·last_received_at / git_ref: mode·repo·ref·sha |

## C. 큐 규칙 (P 「큐 규칙」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| C1 | FIFO by id · position 은 대기 잡에만 1부터 · running 은 null | 잡 3개(1 running · 2 queued)에서 `/api/status` | position null·1·2 |
| C2 | 레인 k 그리디 · concurrency 그룹 배제 · `blocked_by` | lanes=2, 그룹 프리셋 2개 + 그룹 없는 1개 | 두 번째 그룹 잡 `reason=blocked_by_group` + `blocked_by.job_id`; 그룹 없는 잡은 병행 |
| C3 | 살아 있는 레인 0 · 정지면 wait/finish_at null | `rcm pause` 뒤 대기 잡 | `wait_seconds: null` · `finish_at: null` · `reason: paused` |
| C4 | reason 12종 · Not moving 우선순위 | 코드 `ACTIONABLE_REASONS` 순서 = PLAN 순서(worker_down → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused) | 같다 |
| C5 | 합류 키 = preset + inputs + 소스 신원(tree_hash/sha) · `--no-join` | 같은 트리·다른 입력은 합류 안 됨; `--no-join` 은 새 잡 | 관찰 |
| C6 | 표본 정책·min_samples·중앙값·source measured/preset/default | 같은 키 3회 성공 뒤 `medians` · `estimate.source` | `measured`, `sample_count 3` |
| C7 | 신뢰도 high(n≥5)/med/low/group wait/overdue 를 **서버가** 싣는다 | `/api/status` `estimate.confidence` | 값 존재, 규칙 일치 |
| C8 | 잔여 하한 30초 · overdue · stuck(3× 또는 no_output 240) · overdue/stuck 이면 finish_at null | `expected_seconds=1` 프리셋에 `sleep 20` | `overdue: true`, `finish_at: null`, 뒤 잡 대기엔 하한 적용 |
| C9 | 그룹 대기 하한(막는 잡 finish_at + 자기 expected) | C2 상황의 finish_at 비교 | 두 번째 ≥ 첫 번째 + expected |

## D. 프리셋 · 입력 · 마커 (P 「프리셋」「진행 — 스텝 마커」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| D1 | 입력은 스키마 검증(타입·choices·pattern·길이 256) 후 `RCM_INPUT_<NAME>` env 로만 | 잘못된 값 4종을 `rcm run -f` 로 | 서버에 안 보내고 usage 2; 올바른 값은 env 로 도착(스크립트가 echo) |
| D2 | 워커가 항상 주는 env 목록 | 프리셋 `env` 를 전부 echo | RCM_JOB_ID · RCM_PRESET · RCM_REQUESTER · RCM_SOURCE_MODE · RCM_BASE_SHA · RCM_DIRTY · RCM_WORKSPACE · RCM_LOG_FILE (+ RCM_REF) |
| D3 | argv 에 입력을 끼워 넣지 않는다 · 셸 없음 | 코드 읽기 `worker.py` Popen 호출 | `list(preset.argv)` 그대로, `shell=False` |
| D4 | 설정 오류는 프리셋 이름·키 이름을 찍고 시작 실패 | choices 없는 choice · 빈 argv · 모르는 키 3종 | 셋 다 이름 포함 메시지, 종료 ≠ 0 |
| D5 | 마커 4종 · 함정 6개(`steps_total_partial` · 마지막 스텝 끝 · `timing: as_received` · 같은 이름 index · queued 엔 progress null · 초과 잔여 하한) | 픽스처 로그를 실제 프리셋으로 흘리고 `/api/status` | 각 값 PLAN 표와 같다 |
| D6 | 마커 없는 잡은 `steps: []`, `steps_total: null` | `echo ok` 프리셋 | 그렇다 |

## E. 코드 전달 · 워커 · 신호 (P 「코드 전달」「워커 실행」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| E1 | 스냅샷 파일 선택: 추적 + 무시되지 않은 미추적 − 삭제 − `.git/` + `.rcmignore` + `--exclude`; 심링크는 링크로 | 임시 git 레포(추적·수정·미추적·gitignore·삭제·심링크·`.rcmignore`)로 `rcm run` 뒤 워크스페이스 목록(프리셋이 `find .` 출력) | 규칙대로 |
| E2 | base_sha · dirty · tree_hash · repo | 깨끗한 트리 vs 수정된 트리 | dirty false/true, 같은 트리는 같은 tree_hash |
| E3 | 크기 상한 413 + 문구 「.rcmignore 로 빌드 산출물을 빼라」 | B3(a) | 문구 포함 |
| E4 | `tarfile` data 필터: 절대 경로·`..`·바깥 링크·장치 파일 거부 | 손으로 만든 tar 4종 | 전부 `failed` + `snapshot rejected: …` |
| E5 | git_ref: 제출 시 sha 확정 · 미러 · detached checkout · `.git` 유지 · ref 이동 뒤 옛 sha · 강제 push 로 사라진 sha 는 failed | 로컬 bare 레포로 시나리오 4종 | PLAN 「git_ref」 문단대로 |
| E6 | 프로세스 그룹 SIGTERM → grace → SIGKILL, 손자까지 | `sh -c 'sleep 300 & wait'` 취소 → 손자 pid 생존 확인 | 죽음 |
| E7 | 타임아웃 `timed_out` + `summary "limit Ns"` | `timeout_seconds=2` | 그렇다 |
| E8 | 워크스페이스: 성공 즉시 삭제, 실패는 `keep_workspace_on_failure` | 성공/실패 잡 뒤 `workspaces/` | 성공 없음, 실패 있음 |
| E9 | 워커 스레드가 죽으면 `down` + `error`(경로·토큰 없이) + `server.last_error` | 워커에 예외 주입(테스트 `test_worker_goes_down…` 방식) 또는 코드 읽기 | 그렇다 |
| E10 | `progress.phase` materializing → executing | 큰 tar(수 MB) 풀리는 동안 `/api/status` | `materializing` 관찰(짧으면 코드 읽기로) |
| E11 | 로그 줄 flush · `last_output_at` · tail 8KiB 상한 | 긴 로그 잡 | `log_tail` ≤ 5줄, `last_output_at` 갱신 |

## F. 호스트 자원 (P 「호스트 자원」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| F1 | 주기 5 · 하한 2 · `sampled_at`·`age_seconds`·`stale`(3×interval) | 설정 `interval_seconds=1` → ConfigError; `kill -STOP` 20초 → stale | 둘 다 |
| F2 | macOS: load · memsize · vm_stat(active+wired+compressor) · top 두 번째 표본 · ps · ioreg GPU | 이 Mac 에서 `/api/status.hosts[0]` 와 `vm_stat`·`top -l 2` 수동 값 대조 | 오차 ≤ 10%, GPU 값 존재(Apple Silicon) |
| F3 | 값 없는 칸은 null, 부분 실패는 그 칸만, 전부 실패면 `hosts_error` | `PATH` 를 비워 샘플러 실패 유도(재시작) | `hosts: null` + `hosts_error` 또는 칸별 null |
| F4 | `history[]` 길이 `history_samples`, 빠진 표본은 건너뜀 | 6분 뒤 길이 | ≤ 60 |

## G. 보안 (P 「보안」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| G1 | 쓰기는 토큰 필수 · 토큰은 sha256 만 저장 · compare_digest | `POST /jobs` 무토큰 401; `sqlite3 rcm.sqlite3 'select * from tokens'` 에 평문 없음; 코드 `hmac.compare_digest` | 셋 다 |
| G2 | `rcm token add` 는 한 번만 출력 · `list` 에 비밀 없음 · revoke 뒤 401 | 실행 | 그렇다 |
| G3 | 읽기 기본 none · basic 은 읽기만 Basic · 쓰기는 Bearer 만 · health 무인증 | `read_auth="basic"` 로 curl 6종 | 규칙대로 |
| G4 | 로그는 항상 토큰(그 잡의·합류자·admin) | 남의 토큰으로 `GET /jobs/{id}/log` | 403 |
| G5 | 바인드 기본 127.0.0.1 · 비루프백 + none 경고 | `rcm serve --bind 0.0.0.0` stderr | 경고 줄 |
| G6 | 오류 응답에 스택·토큰·경로 없음 | 400/404/413/500 을 유도한 응답 본문 · `failed` summary 들 | 절대 경로·`Traceback`·토큰 값 0건 |
| G7 | 요청 로그는 debug 에만 | `rcm serve` 기본 stderr 에 요청 줄 없음, `--debug` 면 있음 | 그렇다 |
| G8 | 등록된 프리셋만 · 임의 명령 옵션 없음 | `rcm run --help`, API 에 argv 를 받는 필드 없음 | 없음 |
| G9 | 웹 CSP · 토큰은 localStorage · URL 금지 | `curl -I /` 의 CSP 헤더; app.js 에 토큰을 URL 에 넣는 코드 없음 | 그렇다 |
| G10 | ref/URL 검증(옵션 주입) | `--ref -x`, `[[repos]] url = "ext::sh -c id"` | 둘 다 거부 |

## H. 서버 API · SSE · hardening (P 「서버 API」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| H1 | 라우트 표 전부 존재 · 인증 열 일치 | 표의 12개 라우트를 curl 로(인증 없이/있이) | 상태 코드가 표와 같다 |
| H2 | `PUT tree`: Content-Length 필수 · chunked 거부 · 취소된 잡 409 | `curl -H "Transfer-Encoding: chunked"` · 취소 뒤 PUT | 400 / 409 |
| H3 | `GET /jobs/{id}?tail=N` 의 log_tail 규칙(토큰·running 만) | 토큰 유무 × 상태 | 규칙대로 |
| H4 | `/jobs/{id}/events`: 끝난 잡은 hello → job_finished → 닫음 | `curl -N` | 그렇다 |
| H5 | `/events`: hello · keep-alive 15s · Last-Event-ID 재생 · reset/lag · 상한 초과 503 + Retry-After + `{fallback: "poll", poll_seconds: 10}` | `sse_max_connections=2` 로 3연결 | 세 번째 503 + 본문 |
| H6 | `POST /api/eta` 가상 잡 행 · `ahead` | `rcm eta` | position = 대기 수 + 1 |
| H7 | `/api/health` 워커·DB·janitor | 정상 200; 워커 다운/janitor 죽음 시뮬 503 | 그렇다 |
| H8 | 동시 요청 상한 503 · 소켓 타임아웃 · 경로 정규화 · 405/400/413/401/403 · 500 한 줄 | `max_concurrent_requests=2` 로 SSE 3개; `//` 경로; `GET /jobs`(405) | 코드대로 |
| H9 | 상태 모델은 이벤트로 갱신(폴러 아님) · ETag/304 | `If-None-Match` 재요청 | 304 |
| H10 | `pools[]` 한 개 · `schema_version 1` · 키 이름 PLAN 스키마와 같다(추가 키만 허용) | `/api/status` 키 집합을 PLAN 예시 JSON 과 diff | 삭제·의미 변경 0, 추가 키 목록 보고 |

## I. 저장소 · 설정 (P 「저장소」「설정」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| I1 | data_dir 레이아웃 `rcm.sqlite3 · jobs/<id>/{log.txt,tree.tar.gz} · workspaces/<id> · mirrors/<repo>` | `find <data_dir>` | 그렇다(meta.json 은 PLAN 에 있으나 구현 여부 보고) |
| I2 | `PRAGMA user_version` 마이그레이션 · WAL | `sqlite3 rcm.sqlite3 'pragma user_version; pragma journal_mode'` | 2 · wal |
| I3 | 전이 이벤트가 잡 갱신과 같은 트랜잭션 · concurrency_group 빈 문자열 → NULL | 코드 읽기 `store.py` | 그렇다 |
| I4 | 설정 우선순위 플래그 > env > 파일 > 기본값 · 탐색 순서 | `--port` vs `RCM_SERVER_PORT` vs 파일 | 그렇다 |
| I5 | 설정 키 전부(PLAN 「설정」 두 블록) 가 존재하고 기본값이 같다 | `config.py` dataclass vs PLAN 표 | 빠짐·다름 목록 |
| I6 | 모르는 키·잘못된 값은 키 이름과 함께 실패, 조용히 기본값 금지 | 모르는 키 1개 | 종료 ≠ 0 + 키 이름 |
| I7 | 클라이언트 `token = "…"` 파일은 600 검사 | 644 파일 | ConfigError |
| I8 | `rcm check` 표 항목(서버·토큰·프리셋·시간대·data dir + python·git) | 실행 | 전부 |

## J. CLI · `rcm top` (P 「CLI」「터미널 rcm top」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| J1 | 명령 표의 모든 명령·옵션이 존재 | `rcm --help` 및 각 `--help` 를 PLAN 표와 대조 | 빠짐 목록 |
| J2 | `rcm run` 흐름 ①~⑤ · `--no-wait` JSON · Ctrl-C detach(종료 3, 안내 문구) | 실행 · `kill -INT` | 그렇다 |
| J3 | `rcm wait` SSE 우선, 끊기면 2초 폴링, 60초 연결 실패 3, `--timeout` 3 | 서버 내리고 wait | 3 + 이유 |
| J4 | `rcm top` 화면이 PLAN 예시의 요소(큐 행·스텝 줄·recent·medians·host 두 줄)를 다 가진다 | 실행 캡처와 예시 대조 | 요소별 있음/없음 |
| J5 | `rcm eta` 출력 항목(앞선 건수·대기·소요·완료·표본 출처) | 실행 | 전부 |
| J6 | `rcm jobs --mine` 은 요청자+합류자 · `--state` | 실행 | 그렇다 |
| J7 | 세션 예시 `examples/session/ci-gate.sh` 가 실제로 분기한다 | 성공/실패 프리셋으로 실행 | 문구별 |

## K. 웹 UI (P 「웹 UI (M2)」 + `docs/wireframes/web-queue.html` 4절)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| K1 | 요약 세 칸 · 큐 표 열(Job · Key · Requester · Reason · Elapsed · ETA+신뢰도 · Source) · 호스트 카드 · 최근 8 · Estimates | headless Chrome(`tests/test_web_browser.py` 의 `Chrome` 헬퍼)으로 DOM | 전부 |
| K2 | 갱신: SSE → 끊기면 폴링 10s · 백오프 2→30 · 30s 무응답 Lost connection · dim 없음 · 재접속 시 재조회 | 서버 내렸다 올리기 | 띠 등장·소멸 |
| K3 | `schema_version`/`server.version` 변경 → 자동 새로고침 1회 · uptime 감소 → Server restarted 띠 | 서버 재시작 | 띠 |
| K4 | 토큰: localStorage · whoami · 401/403 만 삭제 · 네트워크 오류는 유지 | 토큰 저장 뒤 서버 내리기 | 유지 |
| K5 | 실행 중 행 전부 펼침 · 접힘만 기억 · 레인 1 이면 워커 필 하나 | 실행 | 그렇다 |
| K6 | 목업 4절 키보드·보조기기 규칙(Escape · 포커스 복귀 · aria) | DOM 속성 · 키 이벤트 | 목업 항목별 |
| K7 | 변형 19개(빈 큐 ≠ 조회 실패 · 정지 · 워커 다운 · stale · 취소 중 · 업로드 멈춤 · …) | 각 상태를 만들고 DOM 문구 | 목업 2절 문구와 대조 |
| K8 | 모바일 한 열 · 다크/라이트 | viewport 500 · `prefers-color-scheme` | 그렇다 |

## L. fail-open 금지 (P 「fail-open 금지」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| L1 | 조회 실패는 `null + *_error`, 0건으로 그리지 않는다 | DB 파일을 읽기 불가로 만들고 `/api/status` · `rcm top` · 웹 | `queue_error` · 「Queue unavailable」 · 빈 큐 모양과 다름 |
| L2 | 긍정 문구(Nothing is stuck · fine)는 조회 성공일 때만 | L1 상황의 요약 칸 | `unknown` |
| L3 | 시작할 수 없는 잡·초과 실행에 finish_at 없음 | C3 · C8 | null |
| L4 | `wait` 의 모른다 = 3 | J3 | 3 |
| L5 | 서버 건강(워커·last_error·표본 나이)이 JSON 과 화면 머리에 | `/api/status.server` · 웹 헤더 | 있다 |

## M. 테스트 · CI · 패키징 · 문서 (P 「테스트·품질」「패키징·배포」「브랜치 정책」)

| ID | 규칙 | 확인 방법 | 기대 |
|---|---|---|---|
| M1 | 픽스처 목록(마커 로그 3종 · 스냅샷 git 레포 · macOS/Linux 호스트 캡처) | `tests/fixtures` 와 테스트 | 있다 |
| M2 | 테스트 파일 목록이 PLAN 「테스트」 문단의 이름을 덮는다 | 대조 | 빠짐 목록 |
| M3 | mutcheck 8종 · CI matrix · 집계 `test` · gitleaks · smoke | `ci.yml` 읽기 + 최근 run 결과 | 그렇다 |
| M4 | ruff 100자 · 타입 힌트 | `ruff check` · 함수 시그니처 표본 20개 | 통과 |
| M5 | 브랜치 정책이 룰셋으로 강제 | `gh api rulesets` · `pr-policy.yml` | main/dev PR only · 필수 체크 이름 |
| M6 | README 절 목록(PLAN 「패키징·배포」의 README 항목) | 헤딩 대조 | 전부 |
| M7 | 마일스톤 완료 기준 M0~M4 각각을 **지금** 다시 실행 | 각 기준 문장을 그대로 실행 | 전부 PASS |
| M8 | 결정 항목 1~29 가 구현과 일치(특히 12~16, 17~25 추천값) | 항목별 관찰 | 불일치 목록 |

## 보고 양식 (`docs/acceptance/reports/<날짜>-plan-<담당>.md`)

```
# 계획서 준수 검사 — <담당 절> · <날짜> · 커밋 <sha>
| ID | 판정 | 관찰(명령·출력 요약) | 비고(PLAN 문구와 다른 점 · 제안) |
...
## FAIL 요약 (심각도 순: 보안 > fail-open > 기능 > 문서/문구)
## PLAN 이 구현과 다른데 구현이 맞는 경우 (PLAN 을 고쳐야 하는 항목)
## 확인 못 한 항목과 이유
```
