# M5b-2 작업 명세 — 워커 토큰 · `/worker/*` 프로토콜 · heartbeat 로 down/lost

> `docs/m5-workplan.md` 「M5b」의 두 번째 PR. M5b-1(풀 축)이 들어간 뒤. **원격 워커 프로세스(`rcm worker`)는 M5b-3** — 여기서는 서버 쪽만: 워커가 등록하고, 잡을 claim 하고, 로그·단계·종료를 보고하고, heartbeat 로 살아 있음을 알리는 API 와 그 규칙. 테스트는 HTTP 로 워커 역할을 흉내 낸다.
>
> 바꾸지 않는 것: 스키마 v1 의 기존 키(추가만) · 클라이언트 라우트의 인증 규칙 · 로컬 워커(기본 풀) 동작 · fail-open 금지.

## 1. 토큰 종류 (DB v5)

- `tokens.kind TEXT NOT NULL DEFAULT 'client'` — `client` · `admin` · `worker`. 기존 `admin` 열은 유지(0/1)하되 `kind` 가 정본: 마이그레이션 v5 가 `admin = 1` 이면 `kind = 'admin'`.
- `rcm token add NAME --worker` → kind worker(`--admin` 과 같이 쓰면 오류). `rcm token list` 에 kind 열. `TokenInfo.kind: str`.
- 인증 규칙: `/worker/*` 는 **worker 토큰만**(client·admin 은 403 `worker token required`). 워커 토큰으로 클라이언트 라우트(`POST /jobs` · `/cancel` · `/pause` · `GET /jobs/{id}/log` …)를 부르면 403 `worker tokens cannot use the client API`. 읽기 라우트(`/api/status` · `/events`)는 `read_auth` 규칙 그대로(워커 토큰도 Bearer 로 읽기는 된다).

## 2. 워커 등록 (DB v5 `workers`)

```
workers(name TEXT PRIMARY KEY, pool TEXT NOT NULL, lanes INTEGER NOT NULL, host_name TEXT,
        version TEXT, registered_at REAL NOT NULL, last_seen_at REAL NOT NULL)
```

- 워커 이름 = 토큰 이름(토큰 하나 = 워커 하나). `POST /worker/register {pool, lanes, host_name, version}` → 200 `{name, pool, lanes, heartbeat_seconds, worker_timeout_seconds, claim_wait_seconds}`. 이미 있으면 갱신(풀 바꾸기 허용 — 그 워커가 도는 잡이 없을 때만, 있으면 409). `version` 이 서버 `__version__` 과 다르면 409 `worker version X, server Y — install the same release`. `pool` 은 이름 규칙 · `lanes` 1~64.
- 설정 `[server] worker_timeout_seconds = 60`(하한 10) · `worker_heartbeat_seconds = 5`(하한 1) · `worker_claim_wait_seconds = 20`(0~60).
- 워커 상태는 **서버가 받은 시각** `last_seen_at` 로만: `now − last_seen_at > worker_timeout_seconds` → down. 워커 payload 의 시각은 어디에도 쓰지 않는다.

## 3. claim · 보고 · heartbeat

| 라우트 | 동작 |
|---|---|
| `POST /worker/claim {lane}` | 등록된 풀의 `queued` 잡 하나를 원자적으로 running 으로(`store.claim(lane, now, pool=워커 풀)` + `jobs.worker_name = 이름`). 없으면 `wake` 를 최대 `claim_wait_seconds`(요청 `wait_seconds` 로 줄일 수 있음, 테스트용 0) 기다렸다 **204**. 있으면 200 `{job: <queue 행 JSON>, tree_url: "/worker/jobs/{id}/tree", preset: {argv, timeout_seconds, env, env_passthrough, source_modes, repo?}}` — 워커가 실행에 필요한 것 전부(프리셋 argv 는 여기서만 워커에 간다). |
| `GET /worker/jobs/{id}/tree` | 그 잡의 tar.gz(`Content-Length`). 캐시 잡(manifest)은 서버가 manifest+blob 으로 임시 tar.gz 를 조립해 준다. git_ref 잡은 404 `git_ref jobs are fetched by the worker`(워커 설정의 `[[repos]]`). |
| `POST /worker/jobs/{id}/phase {phase}` | `materializing` · `executing`. |
| `POST /worker/jobs/{id}/log` (본문 raw 바이트, `Content-Length`) | `jobs/<id>/log.txt` 에 append. **마커는 서버가 파싱**(`parse_marker` → `add_marker` + 이벤트). `last_output_at` 갱신. 상한 요청당 4 MiB. |
| `POST /worker/jobs/{id}/finish {outcome, exit_code, summary?}` | `outcome ∈ succeeded·failed·timed_out·cancelled·lost` → `store.finish`(로컬 워커와 같은 규칙: 마커의 summary·failed_step 계산 포함). 두 번 오면 409. |
| `POST /worker/heartbeat {lanes: [{lane, job_id}], host_sample?}` | `last_seen_at` 갱신 · 응답 `{cancel: [job_id…](이 워커의 잡 중 cancelling), paused: bool, timeout_seconds}`. `host_sample` 이 있으면 그 풀의 `hosts[]` 항목으로(`name = 워커 이름`, `source = "worker"`, `sampled_at` 은 **서버 시각**). |

- 워커는 **자기가 claim 한 잡만** 보고할 수 있다(`jobs.worker_name` 불일치 → 403). 다른 워커 토큰·클라이언트 토큰은 403.
- 취소: 클라이언트가 `POST /jobs/{id}/cancel` → 잡은 `cancelling`(kill_at 계산은 기존과 같다) → 워커가 heartbeat 응답의 `cancel` 목록을 보고 SIGTERM→KILL → `finish {outcome: cancelled}`. 워커가 grace + heartbeat 2회 안에 finish 를 안 보내면 서버가 `cancelled` 로 닫는다(요약 `worker did not confirm the cancel`).
- 서버 재시작: 원격 워커의 running 잡은 **lost 로 만들지 않는다**(로컬과 다르다 — 워커는 살아 있을 수 있다). 대신 `last_seen_at` 이 timeout 을 넘기면 lost. 재시작 직후 `workers` 표의 `last_seen_at` 은 그대로라 곧 판정된다.

## 4. down · lost 판정 (서버 안 루프)

- `App._janitor_loop`(5초)에서: `workers` 중 `now − last_seen_at > worker_timeout_seconds` 인 워커의 `running`·`cancelling` 잡(`worker_name` 일치) → `lost` + summary `worker <name> unreachable for <N>s` + `job_finished` 이벤트(알림 대상). 워커 행은 남긴다(down 으로 보임). 다시 heartbeat 이 오면 up.
- `server.workers[]`(추가 키 `worker` · `display_name`): 로컬 레인은 `worker: null`, 원격은 `worker: "<name>"`, `display_name: "<name>/<lane>"`, `state` 는 heartbeat 의 lane 보고(`busy`/`idle`) 또는 down, `job_id`, `since`(서버 시각). `lane` 은 int 유지.
- `pools[].lanes` = 그 풀의 **살아 있는** 워커 레인 합(기본 풀은 로컬 + 원격 `pool = default` 워커). `compute_queue` 는 풀의 `WorkerInfo` 목록을 받는다 → 다운된 워커는 `state = down` 으로 넣어 「살아 있는 레인 수」 규칙이 그대로 먹는다.
- `/api/health`: 등록된 워커가 전부 down 인 풀이 있으면 200 이지만 본문에 `pools_without_workers: [...]`(정보). 503 은 아니다(로컬은 살아 있다).

## 5. 테스트 배치

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_store_m5b2.py` | A | v5 마이그레이션(kind · workers) · `add_token(kind=)` · `list_tokens` kind · `register_worker` · `touch_worker` · `list_workers` · `claim(..., worker_name=)` · `jobs_of_worker` · `mark_lost_for_worker` |
| `tests/test_worker_api.py` | B | `/worker/*` 전 라우트를 HTTP 로: 인증 규칙 6종 · register(신규·갱신·버전 409·풀 변경 409) · claim(200/204/long-poll·풀 격리·그룹 배제·worker_name) · tree(tar · 캐시 조립 · git_ref 404) · phase/log(마커 파싱 → `/api/status` progress)/finish(요약·failed_step·409) · heartbeat(cancel 목록 · paused · host_sample → hosts[] 에 source worker) · 다른 워커의 잡 403 |
| `tests/test_worker_lost.py` | B | timeout 넘긴 워커의 running 잡 → lost + 알림 이벤트 · 다시 heartbeat → up · 서버 재시작 뒤 원격 running 잡은 그대로 → timeout 뒤 lost · pools[].lanes 계산 · server.workers[] 의 worker/display_name · 취소 미확인 → cancelled |
| `tests/test_cli_m5b2.py` · `tests/test_config.py`(추가) · `tests/test_render_m5b2.py` · `tests/web/workers.test.js` | C | `rcm token add --worker`/list kind · 새 설정 키 검증 · `rcm top` 의 원격 워커 필(`build-02/1`) · 풀 헤더에 lanes · 웹 `workerPill` 이름 표시 · `/api/health` 본문 |

규칙: `src/` 금지 · 실제 원격 없음(HTTP 로 워커 흉내) · 시각은 `now_fn` 주입 · 각자 `docs/m5b2-test-scenarios-<담당>.md`.

## 6. 이름 고정 (테스트-퍼스트가 같은 이름을 쓰도록)

- 모델(`core/model.py`): `Job.worker_name: str | None = None`(DB v5 `jobs.worker_name TEXT`, 로컬 claim 은 NULL) · `WorkerInfo.worker: str | None = None`(원격 워커 이름) · `TOKEN_KINDS = ("client", "admin", "worker")`.
- 저장소(`store.py`): `TokenInfo.kind: str = "client"`(`admin` 불리언은 유지 — `kind == "admin"` 과 항상 같다) · `add_token(name, *, admin=False, now, kind=None)`(kind 가 있으면 그것, 없으면 admin 으로) · `WorkerRow(name, pool, lanes, host_name, version, registered_at, last_seen_at)` · `register_worker(name, *, pool, lanes, host_name, version, now) -> WorkerRow`(upsert; `last_seen_at = now`) · `touch_worker(name, now) -> bool` · `get_worker(name)` · `list_workers() -> list[WorkerRow]`(이름순) · `claim(lane, now, pool=DEFAULT_POOL, worker_name=None)` · `jobs_of_worker(name) -> list[Job]`(running·cancelling) · `mark_lost_for_worker(name, now, summary) -> list[int]`(running·cancelling → lost, 잡 id 목록).
- 설정(`config.py`): `ServerSection.worker_timeout_seconds: int = 60`(≥ 10) · `worker_heartbeat_seconds: int = 5`(≥ 1, timeout 보다 작아야) · `worker_claim_wait_seconds: int = 20`(0~60).
- 서버(`server.py`): `App.worker_register(token, body) -> dict` · `worker_claim(token, body) -> dict | None`(None = 204) · `worker_tree_path(token, job_id) -> Path`(조립 tar 는 `jobs/<id>/tree.tar.gz` 에 만들어 둔다 — 두 번째 요청은 그대로) · `worker_phase` · `worker_log(token, job_id, data: bytes)` · `worker_finish` · `worker_heartbeat(token, body) -> dict` · `mark_lost_workers(now) -> list[int]`(janitor 루프가 부른다; 테스트는 직접 부른다) · `remote_worker_infos(pool, now) -> list[WorkerInfo]`. 오류는 기존 `HttpError(status, message)` 규칙. 원격 레인 상태는 **DB 로 계산**(그 워커의 running 잡 `lane` = busy · 나머지 idle · `last_seen_at` 이 오래되면 전부 down) — 서버 재시작에도 같다. heartbeat 의 `jobs: [job_id…]`(선택)가 오면 그 워커의 running 잡 중 목록에 없는 것은 워커가 잊은 잡 → lost(summary `worker <name> restarted without the job`).
- 호스트 표본: heartbeat `host_sample` 은 `hosts[]` 항목 모양의 JSON. 서버가 검증(허용 키만 · 숫자 타입 · `top` ≤ 10 · `history` ≤ 60 · 문자열 200자)하고 `name = 워커 이름` · `source = "worker"` · `sampled_at = 서버 시각` 으로 덮어쓴 뒤 메모리에 둔다(`App._worker_samples`). 풀의 `hosts[]` = 살아 있는 그 풀 워커들의 표본(없으면 `()`). 기본 풀은 로컬 표본 + 원격 default 워커 표본.
- 상태 JSON(`core/status.py`): `worker_json` 에 `worker`(null|이름) · `display_name`(로컬 null · 원격 `"<name>/<lane>"`) 추가. `pools[].lanes` 규칙은 §4. `server.lanes` 는 그대로 로컬 레인 수.
- 인증: `App.authenticate_worker(header) -> TokenInfo`(없거나 kind 가 worker 가 아니면 `HttpError(401|403)`) · 기존 `authenticate` 는 kind worker 면 쓰기 라우트에서 403 `worker tokens cannot use the client API`(읽기 라우트는 허용).
- CLI: `rcm token add NAME [--admin|--worker]` · `rcm token list` 열 `name  kind  created  revoked`.
- 로그: `POST /worker/jobs/{id}/log` 는 `Content-Type: application/octet-stream`. 서버는 줄 단위로 `parse_marker`, 마지막 조각 줄은 다음 요청과 이어 붙인다(`App._log_partial[job_id]`). 로그 상한은 로컬 워커와 같은 규칙(넘치면 잘림 표시, 이후 버림).
- 종료 규칙: 로컬 워커의 종료 상태·요약·failed_step 계산을 `worker.py` 의 모듈 함수 `outcome_for(job, progress, rc, *, ...)` 로 뽑아 서버 `worker_finish` 가 같이 쓴다(runner 분리 자체는 M5b-3).
