# M5b-2 테스트 시나리오 — 담당 C (CLI 토큰 · 설정 키 · 터미널/웹 워커 필) (2026-09-06)

> `docs/m5b2-workplan.md` §5 의 **담당 C** 몫: `rcm token add --worker` / `list` 의 kind 열(§1 · §6 「CLI」) · 새 `[server]`
> 워커 키 검증(§2 · §6 「설정」) · `rcm top` 머리줄의 원격 워커 필(§4 · §6 「상태 JSON」) · 웹 `workerPills` 의 이름 표시.
> `src/` 는 건드리지 않았다. `tests/test_config.py` 는 **끝에 M5b-2 블록만 추가**. 저장소·`/worker/*` 라우트·lost 판정은
> 담당 A·B(`tests/test_store_m5b2.py` · `tests/test_worker_api.py` · `tests/test_worker_lost.py`) 몫이라 읽기만 했다.
> `/api/health` 의 `pools_without_workers` 는 HTTP 로 서버를 띄워야 보이는 값이라(§4 마지막 항목) 담당 B 의
> `test_worker_lost.py` 쪽 관찰에 맡기고 여기서는 잠그지 않았다(§5 참고).

## 1. 파일과 개수

| 파일 | 대상 | 수집 | 지금 |
|---|---|---|---|
| `tests/test_cli_m5b2.py` (신규) | `rcm token add NAME --worker` · `--admin --worker` 충돌 · `list` 헤더/kind 열 · 워커 토큰 revoke | 12 (함수 11 · parametrize 2) | 작성 시점 10 빨강(`unrecognized arguments: --worker` · list 에 헤더 없음) · 2 초록 → 구현 뒤 **12 초록** |
| `tests/test_config.py` (M5b-2 블록 추가) | `worker_timeout_seconds` · `worker_heartbeat_seconds` · `worker_claim_wait_seconds` 기본값 · 파일/env · 하한/상한 · heartbeat < timeout · 정수 아님 · examples/server.toml | 28 (함수 12 · parametrize 21) | **28 초록** — 구현자가 `ServerSection` 키와 `_validate_server` 검증을 같은 워크트리에 먼저 넣었다(작성 시점에 확인). 기존 110 도 전부 초록 |
| `tests/test_render_m5b2.py` (신규) | `render` 머리줄의 원격 워커 필 · 로컬 요약은 로컬만 · `DOWN: lane` 은 로컬만 · 순서 · GOLDEN 불변 · 풀 헤더에 레인 수 없음 | 13 | 작성 시점 9 빨강 · 4 초록(GOLDEN 불변 2 · 절 불변 · 풀 헤더 lanes 2) → 구현 뒤 **13 초록** |
| `tests/web/workers.test.js` (신규) | `rcm.workerPills` 의 원격 필 · 로컬 필 모양 유지 · `queueHeader`/`reasonText` 의 레인 수는 로컬만 · `poolHeader` 불변 | 18 | 작성 시점 10 빨강 · 8 초록(로컬 회귀 가드 · 불변 · poolHeader) → 구현 뒤 **18 초록** |

실행: `ruff format --check` · `ruff check` 통과(CJK 2칸 기준 100). pytest·ruff 는 스크래치 venv(python3.11)로 돌렸다.
합계 71건(pytest 53 · node 18). **작성 직후**(구현 전) 29 빨강(pytest 19 · node 10) · 42 초록 — 빨강은 전부 「기능 없음」이었다.
**같은 세션 안에서 구현이 같은 워크트리에 들어온 뒤** 마지막 실행은 71 전부 초록(test_config.py 126 · render 13 · cli 12 · node 18).

## 2. 공용 규칙 · 도우미

- `tests/test_cli_m5b2.py`: `run(capsys, argv)` 는 test_cli_m5b 처럼 SystemExit 을 코드로 바꾼다. `setup` 픽스처가 tmp 에
  `server.toml`(`data_dir = tmp/data`)을 쓰고 HOME · XDG_CONFIG_HOME · RCM_CONFIG · RCM_SERVER · RCM_TOKEN 을 격리한다.
  `token(capsys, setup, *args)` = `rcm token --config <server.toml> …`(test_e2e_loopback.ServerProc.token 과 같은 호출 —
  서버 프로세스 없이 DB 만 만진다). `add(...)` 는 stdout 이 정확히 한 줄인지 확인하고 비밀을 준다. `stored(setup)` 은
  `Store.list_tokens()` 를 이름 → `TokenInfo` 로, `verified(setup, secret)` 는 `Store.verify_token`. `list_rows` 는 `rcm token list`
  출력을 (헤더 단어들, 이름 → 행 단어들) 로 나눈다.
- `tests/test_config.py` M5b-2 블록: `server_toml(**keys)` 가 `[server]` 표 하나를 만든다. 기존 `load` · `write` · `msg`(tmp 경로 제거)
  · `GOOD` 을 그대로 쓴다. `EXAMPLE_SERVER_TOML` 은 `examples/server.toml`.
- `tests/test_render_m5b2.py`: `local_doc()` = test_render_m5.doc() 의 로컬 항목에 `worker: None` · `display_name: None` 을 더한 것.
  `remote(name, lane, state, job_id)` 는 원격 항목(`worker: name` · `display_name: "<name>/<lane>"` · `lane` int).
  `local_entry(lane, state)` 는 로컬 항목 하나 더. `with_workers(d, *entries)` 는 `server.workers` 에 덧붙인다. `head_line(out)` 은
  첫 줄(`━━━ rcm ·` 로 시작해야), `body(out)` 은 나머지. `GOLDEN` · `two_pool_doc` 은 test_render_m5b 에서 가져온다.
- `tests/web/workers.test.js`: `pick(p)` 로 필 객체의 네 키(text · cls · jobId · lane)만 비교한다 — 구현이 키를 더 붙여도(예: `worker`)
  깨지지 않게. `remote(name, lane, state, jobId)` · `withNullKeys(server)` · `withRemote(server, ...entries)`.

## 3. 시나리오

### 3.1 `tests/test_cli_m5b2.py` — `rcm token` (명세 §1 · §6 「CLI」 · §6 「저장소」)

| # | 시나리오 | 테스트 | 기대 | 명세 § |
|---|---|---|---|---|
| 1 | `rcm token add build-02 --worker` | `test_add_worker_prints_the_token_once_and_stores_kind_worker` | 종료 0 · stdout 은 비밀 한 줄(≥ 32자, 공백 없음) · stderr 에 `build-02` · `created` · 저장 행 `kind == "worker"` · `admin is False` · `revoked_at is None` · `verify_token(비밀).kind == "worker"` | §1 · §6 저장소 |
| 2 | 플래그 없이 `add` | `test_add_without_flags_is_a_client_token` | `kind == "client"` · `admin False` | §1 |
| 3 | `add --admin` | `test_add_admin_is_kind_admin_and_the_admin_flag_stays_true` | `kind == "admin"` · `admin True`(불리언은 `kind == "admin"` 과 같다) | §1 · §6 저장소 |
| 4 | `--admin --worker` / `--worker --admin` (2건) | `test_add_admin_and_worker_together_is_a_usage_error` | 종료 2 · stdout 빈 문자열(비밀 없음) · stderr 에 `--worker` 와 `--admin` 둘 다 · 저장소에 이름 없음 | §1 · §6 CLI |
| 5 | `rcm token add --help` | `test_add_help_lists_the_worker_flag` | 종료 0 · 도움말에 `--worker` 와 `--admin` | §6 CLI |
| 6 | 워커 이름을 클라이언트 이름으로 재사용 | `test_worker_and_client_names_share_one_namespace` | 두 번째 `add` 는 종료 2 · stderr 에 `build-02` · `exists` · 처음 행 kind 는 worker 그대로(토큰 하나 = 워커 하나) | §2 첫 항목 |
| 7 | `rcm token list` 첫 줄 | `test_list_header_is_name_kind_created_revoked_in_that_order` | 공백으로 나누면 정확히 `["name", "kind", "created", "revoked"]` | §6 CLI |
| 8 | client · admin · worker 셋을 `list` | `test_list_shows_client_admin_and_worker_in_the_kind_column` | 헤더의 `kind` 열 위치에 `client` / `admin` / `worker` · 비밀은 출력에 없음 · `user` 라는 단어 없음(오늘의 `user `/`admin` 플래그 표기는 사라진다) | §1 · §6 CLI |
| 9 | 행의 열 수 · 값 | `test_list_rows_follow_the_header_columns` | 행 단어 수 == 헤더 단어 수 · name/kind 위치 · created 는 `YYYY-MM-DD` 한 단어 · revoked 는 폐기 전 `—` | §6 CLI (열 모양은 §4 「잠근 것」) |
| 10 | 워커 토큰 revoke | `test_revoke_works_for_worker_tokens` | 종료 0 · stderr `build-02` · `revoked` · `verify_token` 은 None · 행의 `kind` 는 worker 로 남고 `revoked_at` 있음 · list 의 revoked 열이 날짜 | §1 |
| 11 | 두 번 revoke | `test_revoke_twice_is_a_usage_error_for_worker_tokens_too` | 두 번째는 종료 2 · stderr 에 이름 | (오늘 규칙 유지) |

### 3.2 `tests/test_config.py` — `[server] worker_*` (명세 §2 · §6 「설정」)

| # | 시나리오 | 테스트 | 기대 | 명세 § |
|---|---|---|---|---|
| 1 | 기본값(`ServerSection()` · 키 없는 파일) | `test_worker_keys_have_defaults` | 60 · 5 · 20 | §2 · §6 설정 |
| 2 | 파일에서 30 · 2 · 0 | `test_worker_keys_from_file` | 그대로 실린다 | §2 |
| 3 | env `RCM_SERVER_WORKER_*` 120 · 10 · 60 / `"soon"` | `test_worker_keys_from_env` | 실린다 / `ConfigError` 에 `[server] worker_timeout_seconds` | §2 (env 규칙은 기존) |
| 4 | timeout 9 · 0 / 10 | `test_worker_timeout_floor_is_10` | 오류(`[server] worker_timeout_seconds`) / 10 은 된다 | §2 (하한 10) |
| 5 | heartbeat 0 · -5 / 1 | `test_worker_heartbeat_floor_is_1` | 오류(`[server] worker_heartbeat_seconds`) / 1 은 된다 | §2 (하한 1) |
| 6 | heartbeat ≥ timeout: (10,10) (10,11) (60,60) (60,90) (4건) | `test_worker_heartbeat_must_be_less_than_timeout` | 오류에 `[server] worker_heartbeat_seconds` 와 `worker_timeout_seconds` 둘 다 · `timeout - 1` 은 된다 | §6 설정 |
| 7 | timeout 10 만 주고 heartbeat 기본 5 | `test_worker_timeout_at_the_floor_keeps_the_default_heartbeat` | 5 < 10 이라 통과 | §2 · §6 |
| 8 | claim wait -1 · 61 · 1000 (3건) | `test_worker_claim_wait_must_be_between_0_and_60` | 오류(`[server] worker_claim_wait_seconds`) | §2 (0~60) |
| 9 | claim wait 0 · 60 (2건) | `test_worker_claim_wait_bounds_are_inclusive` | 양끝 포함 | §2 · §3 (0 = 테스트용 즉시 204) |
| 10 | 세 키 × `"soon"` · `true` · `5.5` · `[5]` (12건) | `test_worker_keys_must_be_integers` | 오류에 `[server] <키>` | §2 (타입은 기본값 = 스키마) |
| 11 | `examples/server.toml` 로드 | `test_example_server_toml_accepts_the_worker_keys_if_present` | 검증 통과 · 값이 규칙 안(키가 없어도 기본값으로 통과 — 키를 **요구하지 않는다**) | §2 |

### 3.3 `tests/test_render_m5b2.py` — `rcm top` 머리줄 (명세 §4 · §6 「상태 JSON」)

| # | 시나리오 | 테스트 | 기대 | 명세 § |
|---|---|---|---|---|
| 1 | 풀 하나 · 로컬 항목에 `worker: null` · `display_name: null` | `test_one_pool_golden_is_unchanged_with_null_worker_keys_on_local_entries` | `render` 출력 == GOLDEN(바이트 단위) | §4 (추가 키만) |
| 2 | 옛 항목(키 없음) | `test_local_entries_without_the_new_keys_still_render_as_today` | == GOLDEN — 키 없음 = 로컬 | 스키마 v1 「추가만」 |
| 3 | 원격 busy #511 | `test_remote_busy_worker_gets_its_own_pill_after_the_local_one` | 머리줄에 `worker busy #412 · build-02/1 busy #511` · 나머지 절은 GOLDEN 과 같다 | §4 `display_name` |
| 4 | 원격 idle | `test_remote_idle_worker_pill_has_no_job_number` | `worker busy #412 · build-02/1 idle` · `#` 없음 · `None` 없음 | §4 |
| 5 | 원격 down | `test_remote_down_worker_pill_says_down_and_is_not_a_local_DOWN_lane` | `build-02/1 down` · 머리줄에 `DOWN` 없음 | §4 (down 은 워커 상태) |
| 6 | 로컬 down + 원격 idle | `test_local_down_keeps_todays_DOWN_lane_suffix_before_the_remote_pills` | `worker down · DOWN: lane 1 · build-02/1 idle` | (오늘 규칙 유지) |
| 7 | 원격 셋(build-02/1 busy · build-02/2 idle · build-03/1 down) | `test_remote_pills_keep_the_array_order` | `build-02/1 busy #511 · build-02/2 idle · build-03/1 down` · 필은 항목당 하나 | §4 (이름순은 서버가) |
| 8 | 로컬 2레인(1 busy) + 원격 busy | `test_local_lane_summary_counts_local_lanes_only` | `lanes 1/2 busy · build-02/1 busy #511` · `2/2` · `3/2` 없음 | §6 (`server.lanes` 는 로컬) |
| 9 | 로컬 1레인 + 원격 둘 | `test_remote_pill_is_used_even_when_the_local_lane_count_is_one` | 머리줄이 `… · worker busy #412 · build-02/1 idle` 로 시작 · `lanes ` 없음 | §6 |
| 10 | paused + cache + 원격 | `test_remote_pills_come_before_paused_and_cache` | `worker busy #412 · build-02/1 idle · PAUSED by macmini-admin · cache 12 blobs` | (순서는 §4 「잠근 것」) |
| 11 | 원격 busy + down 이 있어도 | `test_remote_workers_do_not_change_the_default_pool_section` | 큐·recent·medians·host 절 == GOLDEN 의 절 | §4 |
| 12 | 풀 linux lanes 2 | `test_pool_header_with_two_lanes_is_still_just_pool_name` | `queue — 3 (pool linux)` · `lanes` 문구 없음 | M5b-1 헤더 유지 |
| 13 | 풀 linux lanes 0 + 원격 down | `test_pool_header_no_workers_is_unchanged_with_remote_workers_elsewhere` | `(pool linux · no workers)` 그대로 · 머리줄에 `build-02/1 down` | §4 (`pools[].lanes` = 살아 있는 레인) |

### 3.4 `tests/web/workers.test.js` — `rcm.workerPills` (명세 §4 · §6 「상태 JSON」)

| # | 시나리오 | 테스트 | 기대 | 명세 § |
|---|---|---|---|---|
| 1 | 내보내기 | `workerPills is exported on rcm` | 함수 | (기존) |
| 2 | single-lane · main · paused-down 픽스처 (3건) | `local lanes render as today › …` | 오늘의 필 그대로(`worker busy #412` / `#412` `#409` / `lane 1 · down` `#409` `paused`) | 회귀 |
| 3 | 로컬 항목에 null 키 (4 픽스처) | `worker: null / display_name: null on local entries change nothing` | 필 동일 | §4 |
| 4 | 원격 busy | `busy remote → 'build-02/1 busy #511' …` | `[worker busy #412, {text "build-02/1 busy #511", cls "busy", jobId 511, lane 1}]` | §4 |
| 5 | 원격 idle | `idle remote → 'build-02/1 idle', no job` | cls `idle` · jobId null | §4 |
| 6 | 원격 down | `down remote → 'build-02/1 down' with the same class as a local down lane` | cls == 로컬 down 의 cls(`down`) · `lane 1 · down` 문구 아님 | §4 |
| 7 | 로컬 1레인 + 원격 둘 | `the local single-lane pill keeps its shape when remote workers are appended` | 첫 필 `worker busy #412` 유지(「레인 하나」는 로컬 항목 수로) | §6 (`server.lanes`) |
| 8 | main + 원격 셋 | `remote entries keep the array order and come after every local lane` | `#412 · #409 · build-02/1 busy #511 · build-02/2 idle · build-03/1 down` | §4 |
| 9 | paused-down + 원격 | `paused pill stays last, after the remote pills` | `lane 1 · down · #409 · build-02/1 idle · paused` | (순서는 §4 「잠근 것」) |
| 10 | 키 없는 로컬 + 원격 | `a local entry without the worker key next to a remote one is still local` | `worker busy #412 · build-02/1 busy #511` | 스키마 「추가만」 |
| 11 | `display_name: "a<b/1"` | `display_name is plain text — not escaped here` | text `a<b/1 idle`(esc 는 DOM 층) | (pools.test.js 와 같은 규칙) |
| 12 | 입력 불변 | `does not mutate its input` | JSON 동일 | — |
| 13 | main + 원격 busy 둘 | `queueHeader's 'lanes 2/2 busy' is unchanged …` | `queueHeader` 문자열 동일(`3/2` · `4/2` 아님) | §6 (`server.lanes` 는 로컬) |
| 14 | #414 의 reasonText | `reasonText 'waiting for lane · 2/2 busy' is unchanged …` | 동일 | §6 |
| 15 | 원격 idle | `an idle remote worker does not lower the busy count either` | `queueHeader` 동일 | §6 |
| 16 | `poolHeader` lanes 2 · 1 · 0 | `lanes 2 → 'pool linux', lanes 0 → 'pool linux · no workers'` | 레인 수 형태 없음 | M5b-1 유지 |

## 4. 잠근 것 (명세가 열어 둔 모양을 테스트가 정했다)

1. **`rcm token list` 열 모양** — 첫 줄 헤더 `name  kind  created  revoked`(공백 분리 네 단어, §6 의 열 이름 그대로). 행은
   `<name> <kind> <YYYY-MM-DD> <revoked>` 네 단어: created 는 날짜 한 단어(오늘의 `created 2026-09-06` 두 단어가 아니다), revoked 는
   폐기 전 `—`(프로젝트의 DASH 관례) · 폐기 뒤 `YYYY-MM-DD`(오늘의 `active`/`revoked 2026-…` 대신). 열 너비·정렬은 잠그지 않았다.
2. **`--admin --worker`** — 종료 2, stdout 없음, stderr 에 두 플래그 이름이 모두 나온다(argparse `add_mutually_exclusive_group` 의
   `not allowed with argument` 문구든 `_usage(...)` 든 상관없다).
3. **터미널 머리줄의 원격 필** — `<display_name> <state>[ #job_id]`(`build-02/1 busy #511` · `build-02/1 idle` · `build-02/1 down`),
   로컬 요약 뒤에 ` · ` 로 이어 붙이고 배열 순서를 지킨다. 로컬 요약(`worker busy #412` / `lanes 1/2 busy`)과 「레인 하나」 판정은
   **로컬 항목만**(`worker` 가 없거나 null) 으로 센다 — `server.lanes` 가 로컬 수라 원격을 섞으면 `2/2 busy` 같은 거짓말이 된다.
   `DOWN: lane N` 은 로컬 down 만(원격 down 은 필의 `down` 으로만). 순서: 로컬 요약(+DOWN) → 원격 필 → `PAUSED by` → `cache` →
   풀 집계.
4. **웹 `workerPills`** — 같은 문구를 필 객체 `{text, cls: state, jobId, lane}` 로. 로컬 down 과 원격 down 은 같은 `cls`(`down`) 라
   같은 스타일. `busyCount`/`laneCount`(→ `queueHeader` · `reasonText`) 도 로컬만 센다. 구현이 필 객체에 키를 더 붙이는 건 자유
   (테스트는 네 키만 본다).
5. **풀 헤더** — 지시대로 M5b-1 모양 유지: lanes 0 → `(pool linux · no workers)`, lanes ≥ 1 → `(pool linux)`. `· 2 lanes` 같은 레인
   수 형태는 만들지 않는다(터미널 · 웹 둘 다 잠갔다).

## 5. 뺀 것 · 남긴 것

- `/api/health` 의 `pools_without_workers`(§4) — 등록된 워커가 있어야 하는 서버 상태라 HTTP 로 워커를 흉내 내는 담당 B 의
  `test_worker_lost.py` 에서 보는 게 맞다. 여기서는 잠그지 않았다(중복을 피함).
- 웹 배너(「Worker on lane N stopped」)에 원격 워커 이름을 넣는 것 — DOM 코드라 node 순수 함수 테스트로는 못 본다. 구현자가
  `headerNote`/배너를 손볼 때 `display_name` 을 쓰면 된다.
- `tests/test_status_schema.py::…` 는 `server.workers[0]` 의 키 집합을 `{lane, state, job_id, error, since}` 로 잠그고 있다 —
  §6 「상태 JSON」 이 `worker` · `display_name` 을 더하므로 구현 PR 에서 그 집합에 두 키를 더해야 한다(담당 A/B 의 status 테스트
  범위; 여기서는 건드리지 않았다).
- 작성 중 구현자가 같은 워크트리에서 `config.py`(세 키 + 검증) · `store.py`(`TokenInfo.kind`) → `cli.py` · `render_text.py` ·
  `app.js` 순으로 들어왔다. 작성 직후의 빨강은 전부 「기능 없음」(`--worker` 인식 · list 헤더 · 렌더/웹의 원격 필)이었고 픽스처
  오류는 없었다. 마지막 실행에서는 이 문서의 테스트가 전부 초록이다 — 즉 §4 에서 잠근 모양을 구현이 그대로 채택했다.
