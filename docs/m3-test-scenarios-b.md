# M3 테스트 시나리오 — 담당 B: janitor · 서버 git_ref/Basic · CLI · 신호 e2e (2026-09-05)

`docs/m3-workplan.md`(Codex 리뷰 반영본) §1.4 · §1.5 · §2 · §3 · §5 를 pytest 로 옮긴 것이다. `src/` 는 건드리지
않았다. 쓰는 동안 구현이 같은 워크트리에 병렬로 들어와서(`gitops.py` · `janitor.py` · `core/gitref.py` ·
`core/retention.py` · server/store/cli 수정), 인계 시점에는 **67건 전부 초록**이다(전체 스위트 487 passed · 1 skipped,
headless Chrome 제외). 도중에 빨갰던 두 건 — annotated 태그 `v1` 이 `main` 에 합류하지 않던 것(`ls-remote` 의
`^{}` 줄 누락 → 구현이 `ref ref^{}` 두 패턴으로 고침)과 리뷰 전 초안의 「쓰기도 Basic 허용」 — 은 각각 구현 수정과
명세 개정으로 사라졌다.

| 파일 | 대상 | 테스트 수 |
|---|---|---|
| `tests/test_store.py`(추가) | 스키마 v2 · 1→2 마이그레이션 · `list_unpurged_finished` · `mark_artifacts_purged` · `delete_old_jobs` | 함수 5 |
| `tests/test_janitor.py` | `sweep_once` 삭제/표시/보호/실패 재시도/idempotent · `mirrors/` · 메타데이터 보존 · `start/stop` · 로그 404 · App wiring · stale · 스레드 죽음 | 함수 14 |
| `tests/test_server_m3.py` | git_ref 제출(201/200·400·502·504·합류·409·`presets[].repo`) · `read_auth = basic` 전 경로 | 함수 17 (parametrize 포함 33건) |
| `tests/test_cli_m3.py` | `rcm run --ref` 결정 규칙 · usage 2 · JSON 출력 · 스냅샷 생략 · wait e2e · `top`/`jobs`/`presets` 표시 | 함수 7 (parametrize 포함 11건) |
| `tests/test_e2e_m3.py` | 그룹 직렬화(레인 2) · 손자 kill · TERM 무시 → KILL · 타임아웃 | 4 |

공통 규칙: 기다림은 전부 **마감 있는 폴링**(0.05초 간격, 마감 2~20초). 실제 프로세스 테스트는 각 2.5초 안쪽
(`--durations` 실측: 그룹 2.3s · TERM 무시 2.2s · 타임아웃 2.2s · 손자 1.1s). git 이 PATH 에 없으면 git 테스트는
skip(`test_server_m3` 의 git 절 · `test_cli_m3` 전체). janitor · e2e 는 git 이 필요 없다. 실제 원격은 부르지 않는다 —
URL 은 전부 tmp 의 bare 레포 절대 경로다.

## 공용 도우미 (`tests/test_server_m3.py`)

- `make_server(tmp_path, presets, *, workers, repos=(), **overrides)` — `test_server.Server(workers=False)` 를 만든
  뒤 `cfg.presets`·`cfg.repos` 를 바꿔 끼우고, `workers=True` 면 그제야 `app.start()`. `Server` 는 프리셋을 생성자에서
  고정하므로 이 순서가 필요하다(App·Worker 는 설정 객체를 참조로 들고 있어 호출 시점에 읽는다). `test_cli_m3` ·
  `test_e2e_m3` 가 같이 쓴다.
- `build_bare_repo(tmp_path)` — 작업 레포에 커밋 2 · 브랜치 `main` · **annotated** 태그 `v1`(= main) 을 만들고
  `git clone --bare` 로 `remote.git` 을 만든다. `HOME` 을 tmp 로 돌리고 `GIT_CONFIG_NOSYSTEM=1` · 작성자 env 를 줘서
  사용자 gitconfig(서명·훅 템플릿)가 섞이지 않는다. annotated 를 고른 이유: `pick_sha` 우선순위 ②(`^{}` peel)가
  실제로 도는지 합류 판정으로 잠그기 위해서다(lightweight 면 태그 객체 = 커밋이라 구분이 안 된다).
- `submit_ref(srv, ref, *, token, preset, join, source)` · `basic(user, password)` · `sse_first_event(srv, headers)`
  (`GET /events` 를 열어 상태와 첫 프레임 이름만 읽고 닫는다 — `SseStream` 은 Bearer 만 붙일 수 있어서 따로 뒀다) ·
  `queue_ids(srv)`.
- `test_cli_m3` 는 `env`·`bare` 픽스처를 **자기 파일에 다시 정의**한다. 다른 모듈의 픽스처를 import 해 인자 이름으로
  쓰면 ruff 가 F811(재정의)로 잡는다. `run`·`last_json` 은 함수라 `test_cli_m1` 에서 그대로 가져온다.

## 1. `tests/test_store.py` — 추가 5건

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 새 DB | `DB_VERSION == 2` · `user_version() == 2` · `PRAGMA table_info(jobs)` 에 `artifacts_purged_at` · 새 잡의 값은 None |
| 2 | 1 → 2 마이그레이션 | v2 로 만든 DB 에 잡 하나 넣고 닫은 뒤 raw sqlite 로 그 열을 참조하는 인덱스 DROP → `ALTER TABLE jobs DROP COLUMN artifacts_purged_at` → `user_version=1`. 다시 열면 version 2 · `healthy()` · 기존 행(키·상태·created_at) 그대로 · 새 열 None · `list_unpurged_finished()` 가 돈다 · 세 번째 열기는 그대로 |
| 3 | `list_unpurged_finished` | 종료 잡 3(succeeded·failed·timed_out, finished_at 30/10/20초) + running·queued·uploading → `[b, c, a]`(finished_at 오름차순, 활성 제외) · `limit=2` · `mark_artifacts_purged([b])` 뒤 `[c, a]` |
| 4 | `mark_artifacts_purged` | 빈 목록 무해 · 두 잡 표시 → `get_job().artifacts_purged_at == now` · 두 번째 호출 오류 없음(값은 not None 만 확인) · 표시 뒤 `list_unpurged_finished() == []` · `list_recent`·`list_samples`·`state` 는 그대로 |
| 5 | `delete_old_jobs(cutoff)` (리뷰 반영) | purged+오래됨 → 삭제되어 반환 1 · 행/`events`/`joiners` 가 raw count 0 · `markers()` 빈 목록 · purged 이지만 최근인 것 · **purged 아닌** 오래된 것 · 활성 잡은 남는다 · 두 번째 호출 0 |

## 2. `tests/test_janitor.py` — 14건

시각은 고정 `NOW`(2026-09-05 12:00Z) 기준으로 `create_job → claim → finish` 를 명시적 시각으로 찍는다(`finished()` 도우미.
FIFO claim 이라 다른 queued 잡이 없을 때만 부른다). 디렉터리는 `touch_dirs()` 가 `jobs/<id>/log.txt` ·
`workspaces/<id>/{x, sub/y}` 를 만든다. `Janitor(store, cfg, now_fn=lambda: now, on_error=rec.error, log=rec.log)`.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 성공 1일·실패 2일 | 1.5일 된 succeeded 는 삭제·표시(`artifacts_purged_at == NOW`), 반환 1 · 같은 나이 failed 는 그대로 · `purged_total`·`last_sweep_at` · 바로 다시 sweep 하면 0(idempotent) · NOW+0.5일+1초 에 failed 도 삭제 |
| 2 | 종료 상태별 보존 | failed·timed_out·lost·cancelled 넷 다 실패 보존(2일): 1.5일엔 0, 2.5일엔 4 |
| 3 | 활성 보호 (`retention_days_* = 0`) | 10일 된 running·cancelling·queued·uploading 의 디렉터리와 queued 의 `tree.tar.gz` 가 전부 남는다 · running 을 finish 하면 다음 sweep 에 바로(0일) 지운다 |
| 4 | 디렉터리가 이미 없음 | 둘 다 없는 잡·워크스페이스만 없는 잡 → 오류 없이 표시, 반환 2 |
| 5 | `rmtree` 실패 → 보고·재시도 | `shutil.rmtree`(와 있으면 `janitor.rmtree`)를 monkeypatch 해 그 잡 id 경로만 `PermissionError(EACCES)` → 그 잡은 미표시(다른 잡은 표시, 반환 1) · `on_error` 문구에 `retention`·잡 id·`EACCES`/`Permission` 있고 data_dir 경로 없음 · 고장을 풀고 다음 sweep 에 표시(반환 1, 오류는 1건뿐) |
| 6 | `mirrors/`·무관 경로 | `mirrors/app/HEAD` · `jobs/not-a-job-id/` · `rcm.sqlite3` 가 sweep 뒤에도 있다 |
| 7 | 행·이벤트·표본 보존 | purge 뒤에도 `get_job` · `list_recent` · `list_samples` · `transitions` · `markers` 그대로 |
| 8 | `now` 생략 | `sweep_once()` 는 `now_fn` 을 쓴다(0.5일 전 시계면 0, NOW 면 1) |
| 9 | `start()`/`stop()` | `start()` 뒤 2초 안에 첫 sweep(폴링) · `stop()` 이 2초 안에 돌아오고(간격 3600초를 안 기다린다) 스레드 수가 시작 전으로 돌아온다 · 오류 없음 |
| 10 | 메타데이터 보존 (리뷰 반영) | `metadata_retention_days = 3`: 10일 된 잡은 한 sweep 에 산출물 삭제 + **행까지 삭제**(`get_job` None · `markers` 빈 목록) · 2일 된 잡은 산출물만 · 로그에 `purged 2 jobs` · `deleted 1 job record` · 두 번째 sweep 0 |
| 11 | 서버: 로그 404 | 3일 된 alice 잡: purge 전 `GET /jobs/{id}/log` 200 `line\n` → 직접 sweep → **404 + `log expired`**(admin 도 404) · 토큰 없음 401 · 남의 잡 403 은 그대로 · `GET /jobs/{id}` 200 succeeded + transitions · `/api/status.recent` 에 남음 · `last_error` None · **아직 시작 전인 대기 잡은 200·빈 본문·`X-RCM-More: 1`**(`rcm logs --follow` 가 기댄다) |
| 12 | 서버: `App.start()` wiring | 3일 된 잡은 3초 안에 purge(폴링), 0.5일 된 잡은 그대로 · 로그 404/200 · `/api/health` 200 `janitor: true` · close 뒤 3초 안에 `janitor`/`retention` 이름의 스레드 없음 |
| 13 | 서버: stale (리뷰 반영) | 첫 sweep 뒤 `app.retention.last_sweep_at` 을 주기의 3배 전으로 → `/api/health` 503 · `janitor: false` · `error` 에 `janitor stale` · db·workers 는 정상 |
| 14 | 서버: 스레드 죽음 | `Janitor.sweep_once` 를 `RuntimeError("disk gone at /var/rcm/data")` 로 monkeypatch → `app.start()` → 3초 안에 `/api/health` 503 `{ok:false, janitor:false, error:"janitor died: …"}` · db/workers 정상 · `server.last_error` 가 `janitor died` 로 시작하고 경로 없음 · 큐 조회는 멀쩡 · 제출도 된다(400 은 프리셋 검증) |

## 3. `tests/test_server_m3.py` — 33건

### git_ref 제출 (9 함수, git 필요)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `main` 제출 | **201** · `joined false` · `state queued` · `sha == rev-parse main` · `upload` 키 없음 · `url` · `GET /jobs/{id}` 가 `position 1` · `source == {mode, repo:"app", ref:"main", sha}` · `queued_at == created_at` · `reason waiting_for_lane` · Store 의 `source.identity == sha` · `tree.tar.gz` 없음 |
| 2 | 같은 커밋은 이름이 달라도 합류 | bob `main` → 200 joined · admin `v1`(annotated) → joined · alice 40-hex → joined · `joiners == [bob-desk, macmini-admin]` · `join=false` 면 새 잡(sha 는 같다) · 큐 id 목록 |
| 3 | 나쁜 ref (parametrize 10) | `--upload-pack=x` · `-x` · `a..b` · `""` · `main.lock` · `a b` · `x^` · `y:z` · `~1` · 201자 → 400 + `source.ref` · 잡 없음 |
| 4 | ref 없음/타입 오류 | `ref` 누락 · 123 · null · 리스트 → 400 `source.ref` |
| 5 | 모르는 ref | `nope` → **502** · `cannot resolve` · `'nope'` · `'app'` · bare 경로/`remote.git` 없음 · 잡 없음 · `last_error` None(사용자 오류는 서버 오류가 아니다) |
| 6 | 모드 불일치 | tree → deploy 400 `accepts source modes` + `git_ref` · git_ref → gate 400 + `tree` · `svn` 400 |
| 7 | git_ref 잡에 tree 업로드 | `PUT /jobs/{id}/tree` 409 · 상태 queued 유지 · tar 없음 |
| 8 | `presets[].repo` | deploy `repo == "app"` · gate `repo is None` |
| 9 | 해석 타임아웃 | `gitops.resolve_ref`(와 `server.resolve_ref`, 있으면)를 타임아웃 예외 클래스(`GitTimeout`/`GitTimeoutError` 가 있으면 그것, 없으면 `GitError("… timed out after 20s")`)로 monkeypatch → **504** `resolving 'main' timed out after` · 경로 없음 · 첫 인자가 repo url · 잡 없음 |

### `read_auth = basic` (8 함수)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 10 | 익명 읽기 챌린지 | `/` · `/api/status` · `/static/app.js` · `/events` → 401 + `WWW-Authenticate` 가 `Basic realm="rcm"` 로 시작하고 `charset="UTF-8"` 포함 · 본문에 `<html` 없음 · `/api/eta` 401 · `GET /jobs/{id}` 401 Basic |
| 11 | Basic 으로 읽기 | status · `/` · static · eta · whoami(`{name:"alice-laptop", admin:false}` · admin true) · `/events` hello · `GET /jobs/{id}` · **로그**(내 잡·admin 200, 남의 잡 403) |
| 12 | 쓰기는 Bearer 만 (리뷰 반영) | `POST /jobs` · `PUT tree` · `/cancel` · `/pause` · `/resume` 에 Basic → 401 + `Bearer realm="rcm"` · 상태·paused 안 바뀜 · 같은 것을 Bearer 로 하면 된다 |
| 13 | 틀린 자격 | 틀린 비밀번호 · 남의 이름+내 토큰 · 빈 이름 · 빈 비밀번호 → 401 Basic · 폐기 뒤 Basic·Bearer·SSE 전부 401 · 다른 토큰은 여전히 200 |
| 14 | Bearer 유지 · 챌린지 종류 | Bearer 로 status/whoami/SSE 200 · **읽기 라우트의 401 은 틀린 Bearer 든 자격 없음이든 Basic 챌린지** · 쓰기 라우트의 401 은 자격 없음/틀린 Bearer 모두 Bearer 챌린지 |
| 15 | 깨진 헤더 (parametrize 8) | `Basic` · `Basic ` · base64 아님 · 콜론 없음 · UTF-8 아님 · 토큰 둘 · 소문자 `basic` · `Digest` → 401 Basic, 500 아님 · `last_error` None |
| 16 | health | 자격 없이 200 `ok`·`db` |
| 17 | `read_auth = none` | 틀린 Basic 으로 status/`/`/SSE 200(익명) · 맞는 Basic 도 `/api/whoami` 401 Bearer · `POST /jobs` 401 — Basic 은 basic 모드에서만 자격이다 |

## 4. `tests/test_cli_m3.py` — 11건 (git 필요)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `run deploy --ref main --no-wait` | exit 0 · JSON `job_id` int · `joined false` · `state "queued"` · `ref "main"` · `sha` 40 hex == rev-parse · `url` · stderr 에 `snapshot`/`uploading` 없음 · `make_snapshot` 호출 0 · stderr 에 `#N` 과 sha7 · 서버의 잡이 queued/git_ref · bob `--ref v1` → joined 같은 id, `ref "v1"`, stderr `joined job` · `--source git_ref` 명시도 같다 |
| 2 | `--ref` 없음 | `run deploy --no-wait` · `--source git_ref` 만 → exit 2 `needs --ref` + `deploy` · stdout 비어 있음 · submit 호출 0 · 스냅샷 0 · 큐 비어 있음 |
| 3 | 모드 불일치 | `run gate --ref main` → 2(`gate` 언급) · `run deploy --source tree` → 2(`deploy` 언급) · submit 0 |
| 4 | 나쁜 ref (parametrize 5) | `--ref=-x` · `a..b` · `""` · `x^` · `a b` → 2 · stderr 에 `ref` · `Client.submit` 호출 0 · 큐 비어 있음 |
| 5 | wait e2e | `run deploy --ref main --by cli@m3` → 0 · `state succeeded` · `wait_exit_code 0` · `exit_code 0` · `source` 전체 · label · transitions `queued→running→succeeded` · 로그에 `app repo`(README 내용 = 체크아웃됨) · `ref=main`(`RCM_REF`) · `ok` · 스냅샷 0 · `mirrors/app` 생김 · 워크스페이스 삭제됨 |
| 6 | `top`/`jobs` | `rcm top` 의 `#N` 행에 `@<sha7>` · `ref main` · `app` · `not received yet` 없음 · `jobs --json` 행의 `source` 전체 · `jobs` 텍스트에 `#N`·`queued` |
| 7 | `presets` | `--json` 에 deploy `repo "app"` · gate None · 텍스트 deploy 줄에 `git_ref`·`app`, gate 줄엔 `app` 없고 `tree` |

## 5. `tests/test_e2e_m3.py` — 4건 (git 불필요)

프리셋 env 로 `RCM_MARK_DIR` 와 `PY = sys.executable` 을 준다. 시각은 `"$PY" -c "import time; print(repr(time.time()))"`
(macOS `date` 는 `%N` 을 모른다 — 리뷰 「고치면 좋은 것」 4). `Server` 기본 `grace_seconds = 1`.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 그룹 직렬화 (`lanes = 2`) | qa#1·qa#2(`devices`)·solo#3 을 **올린 뒤** `app.start()`(두 레인이 같이 깬다 — pause/resume 보다 결정적). 셋 다 succeeded · `end1 − start1 ≥ 0.9` · **`start2 ≥ end1`** · **`start3 < end1`**(레인 2 가 놀지 않는다) · DB 전이로도 second 의 running ≥ first 의 finished |
| 2 | 손자까지 죽는다 | `sleep 300 & echo $! > pid; wait` → running + pid 파일(5초 폴링) → 살아 있음 확인 → 취소 → cancelled `cancelled by alice-laptop` 5초 안 → pid 가 5초 안에 죽음(`os.kill(pid, 0)` ESRCH **또는** `ps -o stat=` 가 비거나 `Z` — 리뷰 5) |
| 3 | TERM 무시 → KILL | `trap '' TERM; sleep 300` 취소 → 즉시 `GET` 이 `cancelling`·`reason cancelling`·`cancel.by`·**`kill_at − requested_at == 1s`** → cancelled 가 4초 안 · **`exit_code == -9`**(KILL 이 죽였다) · 최근 완료 행의 `cancelled_by` · transitions `uploading→queued→running→cancelling→cancelled` · `cancelled − cancelling` 이 1~4초 |
| 4 | 타임아웃도 같은 경로 | `timeout_seconds = 1` + TERM 무시 → `timed_out` · `summary "limit 1s"` · `timeout_seconds 1` · `exit_code -9` · executing 부터 4.5초 안 · `job_seconds ≤ 4` · transitions `…→running→timed_out` · `cancelled_by` None |

## 6. 가정 (명세가 애매했던 곳에서 내가 고른 것)

1. **새 git_ref 잡의 응답 코드는 201.** 명세 §1.4-5 는 「응답 200」 이라 쓰지만 tree 경로의 새 잡은 201 Created 이고
   합류만 200 이다. HTTP 의미와 기존 규약을 따라 201 로 잠갔다(구현도 201). 명세 문구를 고치는 쪽을 제안한다.
2. **`rcm run --no-wait` JSON 의 `state`.** tree 경로는 `"submitted"` 를 찍고, git_ref 경로는 서버 응답의
   `"queued"` 를 찍는다(코디네이터 지시대로 `queued` 로 잠갔다). 둘이 다른 것은 아래 「명세에 대한 의견」 6.
3. **`rmtree` 실패는 monkeypatch 로 만든다**(chmod 0o500 은 root/CI 에서 실패하지 않는다). `shutil.rmtree` 와,
   있으면 `janitor.rmtree` 둘 다 바꿔 `from shutil import rmtree` 든 `shutil.rmtree` 든 잡힌다.
4. **해석 타임아웃 504** 는 `gitops.resolve_ref` 를 통째로 바꿔 만든다. 타임아웃 전용 예외 클래스가 있으면
   그것을, 없으면 `GitError("… timed out after 20s")` 를 던진다(구현은 메시지의 `timed out` 로 구분한다 — 의견 3).
5. **`mark_artifacts_purged` 의 두 번째 호출**은 「오류 없고 여전히 purged」 만 확인한다. 시각을 덮어쓰는지(구현은
   `IS NULL` 조건이라 첫 시각을 유지) 는 명세가 말하지 않아 못 박지 않았다.
6. **janitor 스레드 죽음**은 `sweep_once` 를 예외로 바꿔 만든다. 구현이 `dead` 를 기록한 뒤 다시 던지므로 pytest 가
   `PytestUnhandledThreadExceptionWarning` 을 내고, 그 테스트만 `filterwarnings` 로 막았다.
7. **`metadata_retention_days = 3`** 은 설정 검증(`≥ sample_days`)을 우회하는 값이다 — `make_config` 가 검증을
   안 거치므로 가능하고, 테스트를 빠르게 하려고 골랐다. 실제 하한 검증은 담당 C 의 `test_config.py`.
8. **텍스트 표시**: `render_text` 는 `app @a1b2c3d ref main`, 웹은 `<sha7>` + `app · ref main` 이라 글리프가 다르다.
   `rcm top` 은 같은 행에 `@sha7`·`ref main`·`app` 이 있는지만 본다. `rcm jobs` 텍스트는 소스 열이 없어 `--json` 으로
   확인했다(의견 2).
9. **e2e 시간 상한**은 워커의 1초 폴링(`POLL_SECONDS`)을 감안해 명세의 「3초」 대신 4초(취소)·4.5초(타임아웃)로
   뒀다. 실측은 2.2초. `exit_code == -9` 가 「KILL 로 죽였다」 를 시간보다 정확히 증명한다.
10. **Basic 자격의 사용자명은 토큰 이름**(`alice-laptop` — `Server` 도우미의 이름)이고, 401 챌린지 문자열은
    `Basic realm="rcm", charset="UTF-8"` 전체를 본다(명세 §5 · 리뷰 「고치면 좋은 것」 8).
11. **`/api/whoami` 에 자격이 전혀 없을 때**의 챌린지는 명세가 읽기 라우트 목록에 whoami 를 넣었으므로 Basic 으로
    잠갔다(basic 모드). none 모드에서는 Bearer.
12. 그룹 e2e 는 `pause/resume` 대신 「세 잡을 다 올린 뒤 `app.start()`」 로 두 레인을 같이 깨운다 — 레인 2 가 0.5초
    idle 대기 때문에 늦게 깨는 경우를 없앤다.

## 7. 명세에 대한 의견 (틀렸거나 비어 있다고 보는 곳)

1. **§1.4-5 「응답 200」** — tree 와 어긋난다. `201`(새 잡)/`200`(합류)로 고치자. 클라이언트는 2xx 만 본다.
2. **§1.5 「`rcm jobs`·`rcm top`·웹은 `app · ref main` 과 `@a1b2c3d`」** — `rcm jobs` 텍스트 출력에는 소스 열이
   없다(키·라벨·시각·요약뿐). `rcm top` 은 `app @a1b2c3d ref main` 이다. 문구를 「`rcm top`·웹」 으로 줄이거나
   `rcm jobs` 에 소스 열을 넣어야 한다.
3. **§1.4-3 504 판정** — 서버가 타임아웃과 일반 실패를 어떻게 구분하는지 명세에 없다. 구현은 `GitError` 메시지의
   `timed out` 부분 문자열로 구분하는데, 문구가 바뀌면 조용히 502 로 떨어진다. `GitTimeout(GitError)` 같은 하위
   클래스를 두는 편이 안전하다(테스트는 그 클래스가 생기면 자동으로 그것을 쓴다).
4. **§3 「3초 안에 cancelled」** — 워커가 취소·grace 를 1초마다 보므로 최악 ≈3.2초다. 상한을 4초로 적거나
   `POLL_SECONDS` 를 줄이는 결정이 필요하다. 테스트는 4초.
5. **§5 `/api/whoami`** — 읽기 목록에 있으므로 Basic 챌린지로 잠갔지만, UI 는 저장 토큰을 Bearer 로 보내므로
   브라우저 프롬프트가 뜰 일은 없다. 의도가 「whoami 는 Bearer 챌린지 유지」 라면 테스트 14 의 한 줄을 바꾼다.
6. **`rcm run --no-wait` JSON 의 `state`** — tree 는 `"submitted"`(업로드 뒤 사실은 queued), git_ref 는 `"queued"`.
   세션 스크립트가 `state` 로 분기하면 헷갈린다. 둘 다 서버 상태(`queued`)를 찍는 쪽을 제안한다.
7. **§2.2 `mark_artifacts_purged`** — 반환값(구현은 표시한 수)과 재호출 의미가 없다. 「`IS NULL` 인 것만, 표시한 수를
   돌려준다」 로 적어 두자.
8. **§2.2 `start()` 「시작 직후 한 번」** — 첫 sweep 이 스레드 안에서 도는지 `start()` 가 동기로 한 번 돌리고 스레드를
   띄우는지 애매하다. 구현(스레드 안)에 맞춰 폴링으로 잠갔다. 동기였다면 sweep 예외가 `App.start()` 를 죽인다는 뜻이
   되어 fail-open 규칙과 다른 결과가 된다 — 명세에 「스레드 안에서」 를 적자.
9. **스레드 죽음 뒤 re-raise** — `dead` 를 기록하고 다시 던지면 서버 stderr 에 스택이 남는다(운영에선 유용). 다만
   pytest 는 경고를 낸다. 명세에 어느 쪽인지 적혀 있으면 좋겠다.
10. **기존 `tests/test_web.py::test_read_auth_basic_requires_token_for_ui_and_assets`** 는 basic 모드의 `/` 에
    `Bearer` 챌린지를 요구했다 — §5 와 충돌한다(구현자가 이미 고쳤다. 기록만 남긴다).
