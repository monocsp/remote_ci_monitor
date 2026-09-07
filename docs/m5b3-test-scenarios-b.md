# M5b-3 테스트 시나리오 B — `WorkerClient` · `load_worker_config` · `rcm worker` (2026-09-07)

`docs/m5b3-workplan.md` §2(`rcm worker` — 토큰 · `worker.toml` · 루프 · `--check`) · §3(`WorkerClient`)과
`docs/m5b2-workplan.md` §3(서버 `/worker/*` 의 본문·응답·상태 코드)을 `tests/test_worker_client.py` ·
`tests/test_worker_config.py` · `tests/test_cli_worker.py` 로 옮긴 것이다(역할 B). `src/` · 기존 테스트는
건드리지 않았다. `runner.py`(A) · 두 프로세스 e2e(C)는 각자 문서에 있다.

공통: 서버는 `test_worker_api.WorkerServer`(진짜 in-process `/worker/*` + 주입 시계 · 워커 토큰 `build-02`
등 · 로컬 워커 스레드 없음). 클라이언트·CLI 의 행복 경로와 401/403/404/409 매핑은 **진짜 서버**로 잠그고,
짧은 본문·503·요청 헤더처럼 서버가 내 줄 수 없는 것만 최소 가짜 HTTP 서버(`FakeServer`, 대본 하나)로
본다. CLI 는 `main(argv)` 를 in-process 로 부르고 토큰은 `RCM_WORKER_TOKEN`(monkeypatch), HOME 은 tmp.
벽시계 sleep 은 없고 `threading.Timer`(0.3·0.5초)로 「잡이 나중에 온다」만 만든다.

## 잠근 인터페이스 (구현이 이 이름·모양을 써야 한다)

- `client.WorkerClient(server, token, *, timeout=15.0)` — `.server`(`Client` 와 같은 정규화: 스킴 보완 ·
  끝 `/` 제거) · `.token`.
  - `register(*, pool, lanes, host_name, version) -> dict` — 서버 응답 그대로.
  - `claim(lane, wait_seconds) -> dict | None` — 204 → `None`, 200 → `{job, tree_url, preset}`.
  - `download_tree(job_id, dest: Path) -> int` — `<dest>.part` 로 흘려 받아 `dest` 로 교체(옛 `.part` 는
    덮어쓴다, 부모 디렉터리 생성), 받은 바이트 수 반환. `Content-Length` 와 다르면
    `ClientError(0, …<받은 수>…<기대 수>…)` 이고 `dest`·`.part` 는 없다. 404 는 `ClientError(404)`.
  - `phase(job_id, phase)` · `log(job_id, data: bytes)`(`application/octet-stream` · 정확한
    `Content-Length` · Bearer) · `finish(job_id, outcome, exit_code, summary=None) -> dict`
    (`{job_id, state}`) · `heartbeat(jobs: list[int], host_sample: dict | None) -> dict`
    (`{cancel, paused, timeout_seconds}`; `jobs` 는 **빈 목록도 보낸다**).
  - 오류는 전부 `ClientError(status, message, body)` — 서버 문구 그대로, `body['state']` 같은 추가 키
    포함. 연결 실패는 status 0 `cannot reach <server>: …`. **재시도 없음**(503 도 즉시 · 요청 1회).
- `config.load_worker_config(path: Path | None, *, overrides: dict) -> WorkerConfig` — `overrides` 키
  `server · pool · lanes · name · data_dir`(None 은 무시), `token` 이 있으면 `ValueError`.
  `WorkerConfig`: `server`(필수) · `token`(env `RCM_WORKER_TOKEN` > 파일 `token`(600 필수) > "") ·
  `pool`(기본 `default`, 이름 규칙) · `lanes`(1~64) · `name`(기본 "") · `data_dir: str`(기본
  `~/.local/share/rcm-worker`) + `data_path: Path`(`~` 풀림) · `repos`(+`repo(name)`) ·
  `host: HostSection` · `grace_seconds = 10` · `path`.
- `rcm worker --server URL --pool NAME [--lanes N] [--name NAME] [--config worker.toml] [--data DIR]
  [--check] [--once]` — `--token` 없음. 종료 2: 토큰 없음(stderr 에 `RCM_WORKER_TOKEN`) · 설정 오류 ·
  서버 403(`worker token required` + `rcm token add … --worker`) · register 409(서버 메시지 그대로).
  `--check`: `rcm check` 모양의 표(`server` · `token` · `pool` · `repos` · `data dir`), 문제면 1, 등록
  안 함. `--once`(추가): 잡 **하나** 돌리고 0 — 잡이 올 때까지 long-poll 로 기다린다.

## `tests/test_worker_client.py` (23)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | `WorkerClient("127.0.0.1:1/", "tok")` → `.server == "http://127.0.0.1:1"` · `.token` | `test_worker_client_normalizes_the_server_url_like_client` | §3 L68 |
| 2 | `register(pool, lanes, host_name, version)` → `{name, pool, lanes, heartbeat_seconds, worker_timeout_seconds, claim_wait_seconds}` 그대로 · 서버 행의 host_name | `test_register_returns_the_protocol_parameters` | §3 L68 · M5b-2 §2 L20 |
| 3 | 버전 불일치 → `ClientError(409, 'worker version 0.0.1, server … — install the same release')` · 등록 안 됨 | `test_register_version_mismatch_is_409_with_the_servers_message` | §2 L58 · §3 L68 |
| 4 | 빈 풀 `claim(lane=1, wait_seconds=0)` → `None`(서버 상한 20초를 기다리지 않는다) · 잡이 있으면 `{job, tree_url, preset}`(argv 포함) · running · worker_name | `test_claim_returns_none_on_204_and_the_payload_on_200` | §3 L68 · M5b-2 §3 L28 |
| 5 | `lane=2` 로 claim → 잡 lane 2 · `server.workers[]` busy | `test_claim_sends_the_lane` | M5b-2 §3 L28 |
| 6 | 등록 전 claim → 409 `worker build-02 is not registered` · 레인 과할당 → 409 `lane 1 already has job #N` | `test_claim_errors_carry_the_servers_status_and_message` | M5b-2 §3 L28 |
| 7 | `download_tree` → `.part` 로 받아 `dest` 로 교체 · 옛 `.part` 덮어씀 · 반환 = 바이트 수 · 내용 == TAR | `test_download_tree_streams_the_tar_to_dest_and_returns_the_byte_count` | §3 L68 · §2 L60 |
| 8 | 캐시 잡(manifest+blob) → 서버 조립 tar 를 받고 멤버가 같다 · 크기 == 반환값 | `test_download_tree_of_a_cache_job_gets_the_assembled_tar` | M5b-2 §3 L29 |
| 9 | git_ref 잡 → `ClientError(404, 'git_ref jobs are fetched by the worker')` · 파일 없음 | `test_download_tree_of_a_git_ref_job_is_404_and_leaves_no_file` | M5b-2 §3 L29 |
| 10 | 가짜 서버가 `Content-Length: 100` 에 40 바이트만 → `ClientError(0)`, 메시지에 40 · 100 · `dest`·`.part` 없음 · 요청 경로 `GET /worker/jobs/7/tree` | `test_download_tree_rejects_a_short_body` | §3 L68 「Content-Length 검증」 |
| 11 | `phase(jid, 'executing')` → 큐 행 phase · `log` 두 번(경계에서 잘린 마커) → 서버가 append + 마커 파싱 | `test_phase_and_log_report_to_the_server` | §3 L68 · M5b-2 §3 L30-31 · §6 L68 |
| 12 | 서버가 거부한 phase → `ClientError(400)` | `test_phase_rejected_by_the_server_is_a_400_client_error` | M5b-2 §3 L30 |
| 13 | `finish(jid, 'succeeded', 0)` → `{job_id, state}` · 요약은 마커 · 두 번째 finish → `ClientError(409, 'job #N is succeeded')` + `body['state']` · 레인 idle | `test_finish_returns_the_state_and_a_second_finish_is_409` | §3 L68 · M5b-2 §3 L32·L35 |
| 14 | `finish(jid, 'failed', None, summary=…)` → 요약 그대로 · exit_code null · `lost` + `worker stopped` 도 같은 모양 | `test_finish_with_a_summary_and_no_exit_code_keeps_the_summary` | §2 L60-61 · M5b-2 §3 L32 |
| 15 | 닫힌 잡에 phase·log → 409 `job #N is succeeded` · 로그 파일도 안 생긴다 | `test_late_reports_after_the_job_closed_are_409` | M5b-2 §3 L35 |
| 16 | 남의 잡 finish → 403 `not your job` · 없는 잡 log → 404 `no such job` | `test_reports_for_another_workers_job_are_403_and_unknown_jobs_404` | M5b-2 §3 L35 |
| 17 | `heartbeat([jid], None)` == `{cancel: [], paused: False, timeout_seconds: 60}` · 취소 뒤 `cancel == [jid]` · `heartbeat([], None)` 은 `[]` 를 보내 잡이 lost(`restarted without the job`) · `last_seen_at` 갱신 | `test_heartbeat_returns_cancel_paused_timeout_and_always_sends_the_job_list` | §2 L59 · M5b-2 §3 L33 |
| 18 | `heartbeat([], SAMPLE)` → 풀 `hosts[]` 에 `name = build-02` · `source = worker` · `None` 이면 없음 | `test_heartbeat_host_sample_reaches_the_pools_hosts` | M5b-2 §3 L33 · §6 L64 |
| 19 | 무효 토큰 → 401 · client 토큰 register → `ClientError(403, 'worker token required')`, `body == {"error": …}` | `test_auth_errors_carry_the_servers_status_and_message` | §2 L55 · M5b-2 §1 L11 |
| 20 | 가짜 503 `{"error": "queue is busy"}` → `ClientError(503, 'queue is busy')` 즉시 · 요청 정확히 1회 | `test_a_503_is_raised_immediately_without_retries` | §3 L68 「재시도 없음」 |
| 21 | 아무도 안 듣는 포트 → `ClientError(0, 'cannot reach http://127.0.0.1:<port>…')` | `test_an_unreachable_server_is_status_0_with_cannot_reach` | §3 L68 · §2 L58 |
| 22 | `log` 요청 헤더: `POST /worker/jobs/5/log` · `application/octet-stream` · `Content-Length: 3` · chunked 아님 · `Bearer tok` | `test_log_sends_octet_stream_with_the_exact_length_and_a_bearer_token` | M5b-2 §6 L68 |
| 23 | `dest` 의 부모가 없어도 만든다(`jobs/<id>/tree.tar.gz`) | `test_download_tree_creates_missing_parent_directories` | §2 L60 |

## `tests/test_worker_config.py` (21 함수 · 32 케이스)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | `server` 만 있는 파일 → pool default · lanes 1 · name "" · token "" · `data_dir` 원문 · `data_path` 는 HOME 으로 풀림 · repos () · `host == HostSection()` · grace 10 · `path` | `test_minimal_file_gives_defaults` | §2 L56 |
| 2 | 모든 키 · `[host]` · `[[repos]]` 순서 · `data_dir` 파일 값 · server 끝 `/` 제거 | `test_full_file_is_read_and_server_trailing_slash_is_stripped` | §2 L56 |
| 3 | `path=None` + 플래그만 → 된다 · `path is None` | `test_no_file_needs_a_server_from_overrides` | §2 L52·L56 |
| 4 | `server` 없음(파일·플래그 모두) → `ConfigError` 에 'server' | `test_missing_server_is_a_config_error_naming_the_key` | §2 L52 |
| 5 | 명시한 파일이 없으면 `ConfigError`(파일 이름) | `test_explicit_missing_path_is_a_config_error` | 서버·클라이언트 로더와 같다 |
| 6 | 깨진 TOML → `ConfigError` 에 파일 이름 | `test_invalid_toml_names_the_file` | 〃 |
| 7 | 플래그 > 파일(server · pool · lanes · name · data_dir) · None 플래그는 파일 값 유지 | `test_overrides_beat_file_values_and_none_does_not_override` | §2 L56 「CLI 플래그가 파일보다 우선」 |
| 8 | `--data ~/x` → `data_path` 가 HOME 으로 풀린다 | `test_override_data_dir_expands_tilde` | §2 L56 |
| 9 | `RCM_WORKER_TOKEN` > 파일 `token`(600) · 파일 없이 env 만도 된다 | `test_token_from_env_beats_the_file` | §2 L55 |
| 10 | 토큰 담은 파일이 644 → `chmod 600` 안내 실패 · 600 이면 읽는다 | `test_token_in_file_requires_600` | client.toml 규칙 |
| 11 | 토큰 없는 파일은 644 여도 된다 | `test_file_without_a_token_may_be_world_readable` | 〃 |
| 12 | `overrides={"token": …}` → `ValueError`(메시지에 token) | `test_token_may_not_come_from_overrides` | §2 L55 (플래그 금지) |
| 13 | 모르는 키 `pool_name` → `ConfigError` 에 그 이름 | `test_unknown_key_names_the_key` | §2 L56 |
| 14 | 모르는 섹션 `[hots]` → 그 이름 | `test_unknown_section_names_the_section` | §2 L56 |
| 15 | `[host] interval = 5` → `[host]` + 키 이름 | `test_unknown_host_key_names_section_and_key` | §2 L56 |
| 16 | lanes 0/65/true · server 5 · pool `bad pool`/"" · name 7 · data_dir 3 · `[host] interval_seconds = 1` · `gpu = "maybe"` → `ConfigError` 에 키 이름 (×10) | `test_bad_values_fail_naming_the_key` | §2 L56 · M5b-2 §2 L20 |
| 17 | 플래그 lanes 0/65/True 도 같은 검사 (×3) | `test_bad_lanes_from_overrides_fail_too` | 〃 |
| 18 | lanes 1 · 64 는 된다 | `test_lanes_bounds_are_inclusive` | M5b-2 §2 L20 |
| 19 | `[[repos]]` 는 정확히 name·url(빠지거나 남으면 실패) | `test_repos_need_exactly_name_and_url` | §2 L56 「서버 것과 같은 규칙」 |
| 20 | 이름 중복 · `-oProxyCommand=` URL · 이름 규칙 위반 → `ConfigError` **(빨강 — 아래 「어긋남」①)** | `test_repos_reject_duplicate_names_and_bad_urls` | 〃 |
| 21 | `cfg.repo("app")` · 없는 이름 None | `test_repo_lookup_by_name` | §2 L60 |

## `tests/test_cli_worker.py` (21)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | `rcm worker --help` 에 8 플래그 · `--token` 없음 · 부명령 목록에 worker | `test_help_lists_the_flags` | §2 L52 |
| 2 | `--lanes 0/65/two` → usage 2, stderr 에 lanes · 등록 안 됨 | `test_bad_lanes_is_a_usage_error` | §2 L52 · M5b-2 §2 L20 |
| 3 | `--server` 도 파일 `server` 도 없음 → 2, stderr 에 server | `test_missing_server_is_a_usage_error` | §2 L52 |
| 4 | 토큰 없음 → 2, stderr 에 `RCM_WORKER_TOKEN` · 등록 안 됨 | `test_missing_token_is_a_usage_error_naming_the_env_var` | §2 L55 |
| 5 | `--check` 도 토큰 없으면 2 | `test_missing_token_with_check_is_also_2` | §2 L55 |
| 6 | `--config` 의 `token`(600)으로 `--check` ok · pool 행 linux | `test_token_from_config_file` | §2 L55-56 |
| 7 | client 토큰 → register 403 → stderr `worker token required` + `rcm token add` `--worker` · 2 · 등록 안 됨 | `test_client_token_is_refused_with_the_worker_hint` | §2 L55 |
| 8 | `--check` + client 토큰 → server ok · token FAIL(kind 를 말한다) · 1 | `test_check_with_a_client_token_fails_the_token_row` | §2 L64 |
| 9 | 서버가 모르는 토큰(401) → token FAIL · 1 · 비밀 안 찍음 | `test_invalid_token_fails_the_token_row` | §2 L64 |
| 10 | `--check` 표: server(URL·버전) · token(`build-02` · worker) · pool · repos(n/a·none) · data dir(`--data` 경로) 전부 ok · 0 · 등록 안 함 · stdout 은 표뿐 · 비밀 없음 | `test_check_prints_a_table_and_exits_0_with_a_worker_token` | §2 L64 |
| 11 | `--pool` 없으면 파일 pool, 그것도 없으면 default · 플래그 > 파일 | `test_check_pool_defaults_to_default_and_flag_beats_file` | §2 L56 |
| 12 | 서버 못 닿음 → server FAIL `cannot reach <url>` · 1 · 재시도 없이 바로 | `test_check_unreachable_server_says_cannot_reach_and_exits_1` | §2 L64 |
| 13 | `[[repos]]` bare 레포 → repos ok · 없는 경로 → repos FAIL(이름) · 1 **(빨강 — 「어긋남」②)** | `test_check_repos_row_runs_ls_remote` | §2 L64 「ls-remote」 |
| 14 | worker.toml 의 모르는 키 → usage 2(키 이름) | `test_check_with_a_bad_config_file_is_usage_2` | §2 L56 |
| 15 | 서버 버전을 9.9.9 로 → register 409 문구 그대로 · 2 · 등록 안 됨 | `test_version_mismatch_prints_the_servers_message_and_exits_2` | §2 L58 |
| 16 | `--once` 로 `lin` 잡 하나: succeeded · summary `built ok` · 마커 6개 · 로그에 `hello` · 워커 행(linux/1) · 레인 idle · stderr `claimed #N` · `#N succeeded` · 토큰 없음 · 로컬 `jobs/<id>/log.txt` 사본 · 워크스페이스 삭제 | `test_once_runs_one_queued_job_end_to_end` | §2 L60·L62-63 |
| 17 | `--once` 는 잡 하나만 — 같은 풀 둘째 · 다른 풀 잡은 queued 그대로 · `claimed #` 한 번 | `test_once_runs_exactly_one_job_and_leaves_the_rest_queued` | §2 (`--once` 정의) |
| 18 | 빈 큐에서 기다리다 0.3초 뒤 올라온 잡을 `wake` 로 받아 끝낸다(5초 안) | `test_once_waits_for_a_job_that_arrives_later` | §2 L60 「long-poll」 · M5b-2 §3 L28 |
| 19 | 즉시 204 인 서버(`worker_claim_wait_seconds = 0`)에서 0.5초 동안 claim ≤ 10 · 잡은 받는다 **(빨강 — 「어긋남」③, 276회)** | `test_an_immediate_204_is_not_retried_in_a_hot_loop` | §2 L60 (`204 면 바로 다시` 의 구멍) |
| 20 | 실패 잡(exit 2) → finish failed · failed_step `t` · stderr `#N failed` · 0 · 워크스페이스 보존 | `test_once_reports_a_failed_job_and_still_exits_0` | §2 L60·L62 |
| 21 | git_ref 잡 + `[[repos]]` 없음 → finish failed `repo 'app' is not configured on this worker` · exit_code null · 0 | `test_once_git_ref_job_without_the_repo_fails_naming_the_repo` | §2 L60 |

## 결과 (2026-09-07, 구현 커밋 `1e42efd` 위에서)

76 케이스 중 73 초록 · **3 빨강** — 셋 다 픽스처가 아니라 구현이 명세와 어긋난 곳이다:

1. `load_worker_config` 의 `[[repos]]` 는 「정확히 name·url」만 보고 서버의 `_validate_server` 규칙(이름
   중복 · 이름 규칙 · `validate_repo_url`)을 안 거친다. 워커는 그 URL 을 그대로 `git fetch` 에 넘기므로
   `-oProxyCommand=…` 같은 값이 옵션으로 들어간다 — 서버와 같은 문지기가 필요하다.
2. `rcm worker --check` 의 repos 행은 이름만 나열하고 **ls-remote 를 하지 않는다**(명세 §2 L64: 「`[[repos]]`
   의 git 접근(ls-remote)」). 닿지 않는 레포도 ok 로 나온다.
3. 레인 루프가 `204 → continue` 라 서버가 즉시 204 를 주면(설정 `worker_claim_wait_seconds = 0`, 또는
   long-poll 슬롯 `CLAIM_WAIT_SLOTS = 8` 초과 — 워커 3대 × 4레인이면 4레인이 영구히 이 상태) CPU 100% 로
   서버를 두드린다(0.5초에 276회). `wait_seconds` 보다 먼저 돌아온 204 뒤에는 짧게 쉬어야 한다.

명세 해석(구현과 맞춘 것): `--once` 는 「잡 하나를 돌리고 종료」이며 빈 큐에서는 기다린다(즉시 종료가
아니다). `WorkerConfig.data_dir` 은 원문 문자열이고 푼 경로는 `data_path`(서버의 `ServerConfig.data_dir`
프로퍼티와 같은 역할). 정수 키의 숫자 문자열(`lanes = "2"`)은 다른 로더처럼 받아들인다(환경변수 규칙).
`--check` 의 repos 행 문구는 `n/a` 또는 `none` 둘 다 받는다.
