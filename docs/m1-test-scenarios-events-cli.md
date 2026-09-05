# M1 테스트 시나리오 — 이벤트 버스 · SSE · CLI (2026-09-05)

`docs/m1-workplan.md` 0(A·B·E·F·G) · 3 · 4 · 5절을 테스트로 옮긴 것이다. 세 파일이 대상이다.

| 파일 | 대상 | 테스트 수 |
|---|---|---|
| `tests/test_events.py` | `remote_ci_monitor.events.EventBus` 단위 | 15 |
| `tests/test_server_m1.py` | `GET /events` · `GET /jobs/{id}/events` · `POST /api/eta` · 스키마 추가 키 · `hosts[]` | 16 |
| `tests/test_cli_m1.py` | `rcm presets/top/jobs/eta/logs/wait` (in-process `main(argv)`) | 18 |

공통 규칙: 기다림은 전부 **마감이 있는 폴링 또는 `get(timeout)`** 이고 맨 `sleep` 은 0.5초 이하만 쓴다.
「안 온다」는 시간을 기다려 증명하지 않고, **그보다 뒤에 발행된 이벤트가 먼저 보이는지**로 증명한다
(버스가 순서를 보장하므로). SSE 는 `http.client` 로 열고 줄 단위로 읽으며, 읽기마다 남은 마감을 소켓
타임아웃으로 걸어 타임아웃은 곧 실패다.

## 1. `tests/test_events.py` — EventBus

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | id 단조 증가 | `last_id` 0 에서 시작, 첫 이벤트 id 1, 이후 +1. `Event(id, kind, data, at)` 값 보존 |
| 2 | Event 불변 | 필드 대입은 `AttributeError`(frozen dataclass) |
| 3 | 구독 후 발행 | 발행 순서대로 도착, 다 꺼내면 `get(0.05)` 는 None |
| 4 | `get(timeout)` | 비었으면 None, 그 시간만큼 기다린다(0.15s ≤ 경과 < 2s) |
| 5 | `last_id=None` 구독 | 과거는 재생하지 않고 이후 이벤트만 |
| 6 | 구독자 둘 | 각자 모든 이벤트를 받는다 |
| 7 | 링 버퍼 안 재생 | 1..5 발행 후 `last_id=3` → 4, 5 재생(reset 아님) → 그 뒤 실시간 6 |
| 8 | 최신 `last_id` 재생 | `last_id == bus.last_id` 면 아무것도 안 온다(reset 도 없음) |
| 9 | 링 버퍼 밖 → reset | `history=5` · 12개 발행 · `last_id=1` → `reset {}` 하나, 옛것은 안 흘림, 이후 13 정상. 같은 버스에서 `last_id=9` 는 10..13 재생 |
| 10 | 기본 history 2048 | 2050개 발행 후 `last_id=3` → 2047개 재생, `last_id=1` → reset (`test_default_history_is_2048`) |
| 11 | 큐 넘침 → lag | `maxsize=3` · 10개 발행 → `lag {}` 정확히 한 개, 남은 실이벤트의 마지막은 id 10(가장 새 것), id 오름차순·중복 없음, 총 ≤ 4개, 넘친 뒤에도 구독 유지 |
| 12 | 기본 maxsize = 링 크기 | 정확히 2048개는 lag 없음, 2048+52개 → lag 하나 + 최신 유지, ≤ 2049개 (`test_default_subscription_queue_matches_ring_size`) |
| 13 | lag 는 구독자 별 | 작은 큐가 넘쳐도 큰 큐 구독자는 1..8 전부 받는다 |
| 14 | 구독 해제 | 해제하면 안 오고 `subscriber_count` 감소, 구독자 0 이어도 발행·id 증가 |
| 15 | 스레드 안전성 | 발행 스레드 2개(각 250) + 소비 스레드 — id 1..500 빠짐·중복·역순 없음, 스레드별 발행 순서 보존, 마감 10s |

## 2. `tests/test_server_m1.py` — SSE · eta · 스키마 · hosts

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `GET /events` 수명주기 (`live`) | 200 · `text/event-stream` · `no-store`. 첫 프레임 `hello` 에 `last_id`(int) · `generated_at`(Z) · `server.version`(=App.version) · `uptime_seconds`. `submit()` → `job_changed{job_id, state:"uploading"}` 이고 `id` > hello.last_id. 업로드 후 `job_finished{state:"succeeded", exit_code:0}` 까지 사이에 `job_changed` 의 `queued`·`running`, `marker{kind:"step", value:"a"}` 가 있다. 프레임 id 는 단조·중복 없음 |
| 2 | 합류·정지 이벤트 | 합류자 변경 → 그 잡의 `job_changed`. `POST /pause` → `server{paused.by, workers[]}`, `/resume` → `server{paused:null}` |
| 3 | `Last-Event-ID` 재생 | 스트림 1 에서 본 `job_changed` 의 id 를 들고 끊음 → 아무도 안 듣는 동안 잡 2 제출 → `Last-Event-ID: <id>` 로 재접속하면 hello(`last_id` > id) 뒤에 잡 2 의 `job_changed` 가 재생, id 는 전부 > 본 id, 잡 1 은 다시 안 옴 |
| 4 | 링 버퍼 밖 → `reset` 프레임 | `app.bus` 를 `EventBus(history=8)` 로 바꾸고 20개 발행 → `Last-Event-ID: 1` → hello(`last_id 20`), `event: reset` `data: {}`, 그 뒤 실시간 이벤트(id > 20) 정상 |
| 5 | keep-alive | `sse_keepalive_seconds=1` 서버에서 hello 뒤 3.5s 안에 `:` 로 시작하는 줄 |
| 6 | 연결 상한 (결정 E) | `sse_max_connections=2` — 스트림 2개 열면 `server.sse_connections == 2`, 세 번째 `GET /events` 는 503 + `Retry-After: 10` + JSON `{"error":"too many event streams","fallback":"poll","poll_seconds":10}`. 일반 요청(`/api/status`)은 여전히 200 |
| 7 | HEAD/POST → 405 | `/events` · `/jobs/{id}/events` 둘 다 |
| 8 | `read_auth = basic` | 토큰 없는 `/events` 401, `/api/eta` 401, 토큰 있으면 둘 다 200 |
| 9 | 종료 잡의 `/jobs/{id}/events` | hello → `job_finished{job_id, state:"succeeded", exit_code:0}` → 스트림 종료(EOF) |
| 10 | 실행 중 잡의 `/jobs/{id}/events` | 잡 A(slow) 스트림을 연 뒤 잡 B 제출·업로드(B 이벤트가 먼저 발행됨) → A 취소 → A 의 `job_finished{cancelled}` 까지 온 모든 프레임의 `job_id == A`, 종류는 `job_changed/job_finished/marker` 만, `cancelling` 이 보인다 |
| 11 | 없는 잡 스트림 | `GET /jobs/999/events` → 404 |
| 12 | `POST /api/eta` 가상 행 | `{preset:"gate", inputs:{scope:"full"}}` → 200 `{job, ahead, generated_at}`. `job.id null` · `position 1` · `ahead 0` · `state queued` · `reason waiting_for_lane` · `estimate.confidence "low"` · `source preset/default` · `expected_seconds 600` · `wait_seconds 0` · `finish_at` 있음. 대기 잡 둘 뒤에서는 `position 3` · `ahead 2` · `ahead_job_id == 두 번째 잡` · `wait_seconds > 0`. `inputs` 생략은 기본값으로 200 |
| 13 | `/api/eta` 거부 | 모르는 프리셋 400(`unknown preset`) · 잘못된 choice 400(`is not one of`) · 모르는 입력 400 · 빈 본문 400 · 배열 본문 400 · GET 405 |
| 14 | 정지 중 eta | `reason paused` · `finish_at null` · `wait_seconds null`(fail-open 금지), 재개하면 `finish_at` 복귀 |
| 15 | 스키마 추가 키 | `server.sse_connections` 는 int(0 → 스트림 하나 열면 1). 큐 행과 `GET /jobs/{id}` 둘 다 `estimate.confidence == "low"` |
| 16 | `hosts[]` — 가짜 샘플러 | `app.sampler.latest()` 스텁: 2초 전 표본 → `hosts[0]` 의 name/source/os/cores/load/cpu/memory/gpu/gpu_note/top/history 그대로, `stale false`, `age_seconds` 0..10, `hosts_error null`. 60초 전 표본 → `stale true`. `([], "sampler: boom")` → `hosts null` + `hosts_error`. `([], None)` → `hosts []` + 오류 없음 |

## 3. `tests/test_cli_m1.py` — CLI

환경변수 `RCM_SERVER`/`RCM_TOKEN` 만 쓰고 `HOME` 을 임시 디렉터리로 옮긴다(사용자 `client.toml` 격리).

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `rcm presets` | 종료 0, 네 프리셋 이름과 `scope` · choices `full`/`fast` 가 보인다 |
| 2 | `rcm top --json` | stdout 전체가 JSON 한 문서, `schema_version 1`, `server.sse_connections` 있음 |
| 3 | `rcm top` | 첫 줄 `━━━ rcm ·`, 빈 큐 `queue — empty`, `host — no sample yet`. 스텁 샘플러 + 잡 제출 뒤엔 `━━━ rcm · macmini ·` · `host  macmini` · `#id` · `uploading` |
| 4 | 토큰 없는 `rcm top` | 읽기는 토큰 없이 0 |
| 5 | `rcm jobs` · `--state` · `--json` | 제출한 `#id` 와 `uploading` 이 보임, `--state uploading` 에는 있고 `--state running` 에는 없음, `--json` 은 JSON 한 문서에 `"id": N` |
| 6 | `rcm jobs --mine` | bob 은 alice 의 잡을 못 보고(필터 없으면 보임), alice 는 봄, bob 이 합류하면 bob 도 봄, 토큰 없으면 2 + stderr 에 `token` |
| 7 | 최근 완료 | 끝난 잡이 `#id` · `succeeded` 로 보임 |
| 8 | `rcm eta gate -f scope=full` | 0, `in line` 또는 `ahead`, `low`. 대기 잡 둘 뒤엔 `3rd in line` · `ahead`. `--json` 은 `position 3` · `estimate.confidence low`(`job` 키 안이든 행 자체든 허용) |
| 9 | `rcm eta --job N` · 오류 | 대기 잡은 `#N` · `1st in line`. `eta nope` 2 + `unknown preset`, 잘못된 choice 2 + `is not one of`, `-f` 에 `=` 없음 2 |
| 10 | 토큰 없는 `rcm eta` | 0 |
| 11 | `rcm logs N` | 끝난 잡의 로그 원문(`::rcm::step::a`, `hello`), bob 은 403 → 2, admin 0, 토큰 없음 2 |
| 12 | `rcm logs N --follow` | 실행 중 `slow` 잡 — 로그에 `line2` 가 생긴 뒤 0.5초 후 다른 스레드가 취소 → follow 가 `line1\nline2\n` 를 찍고 0 으로 돌아온다(10s 안) |
| 13 | `rcm wait` SSE 경로 | `Client.events` 를 세는 래퍼로 감싼 채 `ok` 잡을 기다림 → 0, JSON `state succeeded` · `wait_exit_code 0`, `/jobs/N/events` 로 `events()` 가 불림. 실패한(이미 끝난) 잡은 1 |
| 14 | SSE 거부 → 폴링 폴백 | `sse_max_connections=0` 서버(`GET /events` 503) — `rcm wait` 는 SSE 를 시도하고 폴링으로 넘어가 0 |
| 15 | `rcm wait --poll` | `Client.events` 를 예외로 바꿔도 불리지 않고 0 |
| 16 | 조용한 5초 → 재연결 (결정 F) | `slow` 잡(첫 마커 뒤 조용)을 기다리며 `Client.events` 호출이 2회가 되면 다른 스레드가 취소 → 2 · `cancelled`, 호출 ≥ 2 (9초 안에 재연결이 없으면 실패) |
| 17 | SSE 경로의 `--timeout` | 실행 중 `slow` 잡에 `--timeout 1` → 3 + stderr `--timeout`, JSON `state running`, 10초 안(다음 5초 틱) |
| 18 | 없는 잡 | `rcm wait --job 999` → 3(2 가 아님) + `not found` |

## 4. 다루지 않은 것과 이유

| 시나리오 | 이유 |
|---|---|
| SSE 연결이 닫힌 뒤 `sse_connections` 가 줄어드는 시점 | 서버는 다음 쓰기(keep-alive/이벤트)에서야 끊김을 안다. 첫 쓰기는 커널 버퍼에 들어가 성공할 수 있어 결정적으로 잡기 어렵다 |
| `Last-Event-ID` 가 정수가 아닐 때 · `bus.last_id` 보다 클 때(서버 재시작 뒤 재접속) | 명세에 없음. 무시(재생 없음) 또는 reset 둘 다 가능 — 오너 결정 필요 |
| 링 버퍼 경계 정확히 한 칸(`last_id == oldest − 1`) | 「버퍼 안」의 두 해석(그 id 가 남아 있어야 / 그 뒤 것이 다 남아 있으면 됨)이 갈린다. 테스트는 두 해석에서 결과가 같은 값만 쓴다 |
| `reset`·`lag` 이벤트의 `id` 값 | 명세에 없음. 구독자별 합성 이벤트라 단정하지 않는다 |
| 실행 중 잡의 `/jobs/{id}/events` 가 `job_finished` 뒤 스트림을 닫는지 | 명세는 「이미 종료 상태면 닫는다」만 말한다. 클라이언트가 닫는 것으로 충분 |
| `/jobs/{id}/events` 가 `sse_max_connections` 를 함께 세는지 · `read_auth=basic` 아래서 토큰이 필요한지 | 명세 표는 인증 「없음」이지만 M0 의 `GET /jobs/{id}` 는 `read_auth` 를 따른다. 결정 필요 |
| `host_sample` 이벤트가 SSE 로 오는지 | 실제 샘플러(interval ≥ 2s · macOS `top` 1초)에 묶여 느리고 환경 의존. 샘플러의 `publish` 호출은 `test_hostsample.py` 가 본다 |
| `estimate.confidence` 의 `high`/`med`/`group wait`/`overdue` | 30초 이상 잡 표본 2개 이상 · 그룹 프리셋이 필요해 서버 테스트로는 느리다. `core/queue.confidence` 단위 테스트가 덮는다 |
| `rcm top --watch N` | Ctrl-C 로 끝나는 루프. 결정적으로 돌리려면 내부 sleep 을 갈아끼워야 해 구현에 결합된다 |
| `rcm wait` 가 SSE 연결이 **도중에** 끊길 때(서버 쪽 끊김) | 서버를 죽이지 않고 스트림만 끊는 방법이 없다. 조용한 5초 뒤 재연결은 CLI 16 이 본다 |
| `rcm wait` 의 재조회 초당 1회 합치기 | 이벤트 폭주를 결정적으로 만들기 어렵고 관찰 지점(`Client.job` 호출 수)이 구현에 결합된다 |
| `rcm eta --job N` 에서 N 이 없거나 끝난 잡일 때 · `rcm logs 999` | 종료 코드가 명세에 없다(2 인지 3 인지) |
| `rcm run` | 변화 없음 — `test_e2e_loopback.py` 가 덮는다 |

## 5. 명세가 모호해 테스트가 정한 것 (구현이 맞춰야 하는 값)

- `EventBus()` 기본 `history=2048`, `subscribe()` 기본 `maxsize` 는 링 크기와 같다 — Codex M1 리뷰(좋음 1·2) 반영 뒤 격리 검증에서 테스트를 정렬했다.
- 큐 넘침: `lag` 는 **한 번만**(연속 넘침에도 coalesce) 넣고, **가장 새 이벤트가 남는다**(「가장 오래된 것을
  버린다」). 남는 개수는 `maxsize + 1` 이하.
- `reset`·`lag` 의 `data` 는 정확히 `{}`.
- `hello` 프레임의 `data.server.version` 은 `App.version`, `generated_at` 은 `Z` 로 끝나는 ISO.
- `job_changed`/`job_finished`/`marker` 의 `data` 는 명세 키를 **포함**하면 된다(추가 키 허용).
- `GET /jobs/999/events` 는 404 (`GET /jobs/999` 와 같은 규칙).
- `POST /api/eta` 는 `read_auth` 만 따르고 토큰이 없어도 된다. 응답 `job` 은 `queue_row_json` 모양이며
  `id: null`. 정지 중이면 `finish_at`·`wait_seconds` 가 null.
- 상태 캐시(0.2s 디바운스) 때문에 `/api/status` 단언은 3초 마감 폴링(`status_until`)으로 한다.
- 테스트는 `srv.app.bus`(`EventBus`) 와 `srv.app.sampler`(`latest()` 만 있으면 됨) 에 직접 닿는다.
  `sse_max_connections`·`sse_keepalive_seconds` 는 `Server(..., <key>=값)` 으로 생성 시점에 넣는다
  (세마포어를 생성 시 만들어도 되게).
- `rcm presets` 출력 형식은 자유 — 이름·입력 이름·choices 가 텍스트에 있으면 된다.
- `rcm jobs --json` 은 JSON 한 문서(모양 자유) 에 `"id": N` 이 있으면 된다. `rcm eta --json` 은
  `{job: 행}` 또는 행 자체 둘 다 허용.
- `rcm eta` 한 줄에 `in line` 또는 `ahead`, 그리고 신뢰도 단어(`low`)가 있어야 한다. 순번은 `_ordinal`
  (`3rd in line`).
- `rcm logs --follow` 는 잡이 끝나면 0 으로 돌아온다(잡의 결과와 무관).
- 이미 끝난 잡의 `rcm wait` 는 SSE 를 열든 조회 한 번으로 끝내든 상관없다 — 종료 코드만 본다.
- `rcm wait --job 999` 는 M0 과 같이 3 이다(SSE 404 를 「모른다」로 다룬다).
