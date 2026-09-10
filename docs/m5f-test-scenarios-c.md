# M5f 테스트 시나리오 C — PR 2a-0 선행 병목 네 가지 (2026-09-09)

`docs/m5f-workplan.md` §10 의 **PR 2a-0** 과 §15.2 의 실측 표(C1 · C1b · D6 · C4)를 테스트로 옮긴 것이다.
게이트를 다는 자리 자체가 느리면 게이트가 아니라 그게 병목이 된다 — 이 문서가 잠그는 것은 그 자리를
비우는 네 가지다: ① `Store.add_markers()` 배치(마커 줄마다 트랜잭션 하나 → 한 번, SSE 는 커밋 뒤)
② `_try_claim` 이 바쁜 DB 를 500 이 아니라 **503 + `Retry-After`** 로 ③ janitor 주기 sweep 의 `ANALYZE`
④ `CLAIM_MIN_INTERVAL` 지터. PR 2a-0 의 다섯째인 `core/queue.py` 의 `(worker, lane)` 레인 키 버그(§5.3)는
**다른 문서(큐·ETA 담당)가 맡는다** — 여기서는 의존으로만 적고 시나리오를 쓰지 않는다. 순수 판정
(`core/admission.py`) · 설정 키 · 화면도 다른 문서의 몫이다. 이 문서는 `src/` 를 건드리지 않고, 대상 파일은
`tests/test_store.py` · `tests/test_worker_api.py` · `tests/test_janitor.py` ·
`tests/test_worker_client.py` 와 새 파일 `tests/test_e2e_m5f.py` 하나다.

**의존**: §5.3 의 레인 키 고침(`core/queue.py` 의 `lane_free`·`lane_last_job`·`idle_since` 를
`(w.worker, w.lane)` 로)은 이 문서의 어떤 시나리오와도 겹치지 않는다. 같은 PR 에 들어가지만 담당이 다르다.

## 공통 — 도우미와 규칙

**`sql_trace` 픽스처.** `sqlite3.Connection.set_trace_callback` 은 **연결마다** 걸리는데
`Store._conn()`(`store.py:267-278`)은 **스레드 로컬**이라 테스트 스레드에서 건 콜백은 서버 스레드의 SQL 을
못 본다. 그래서 `Store._conn` 을 monkeypatch 로 감싸 **새로 만들어지는 연결마다** 콜백을 걸어 준다. HTTP
요청 스레드는 요청이 끝날 때 연결을 닫으므로(`server.py:1492`) 요청마다 새 연결 = 콜백이 다시 걸린다.

실측으로 확인한 콜백의 성질(SQLite 3.53.2):

| 무엇 | 콜백이 보는 것 |
|---|---|
| autocommit `execute` 3번(오늘의 `add_marker`) | `INSERT …` 3줄. **`BEGIN` 도 `COMMIT` 도 없다** |
| `BEGIN IMMEDIATE` + `executemany`(3행) + `COMMIT` | `BEGIN IMMEDIATE` · `INSERT …` **3줄** · `COMMIT` |
| `executemany` 에 빈 리스트 | **아무것도 없다** |

즉 **트랜잭션 수 = `BEGIN` 으로 시작하는 줄 수**, **행 수 = `INSERT INTO events` 줄 수**다. 둘이 따로
세어지므로 「행은 3개인데 트랜잭션은 1개」를 직접 단언할 수 있다. 콜백에 오는 SQL 은 값이 박힌 **확장된**
문장이라 `startswith` 로 가르고 정확한 문장 비교는 하지 않는다.

**`plan_of(conn, sql, params)`** — `[r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]`.

**`RecordingEvent(threading.Event)`** — `wait(timeout)` 값을 전부 기록하고 진짜 `Event.wait` 에 넘긴다.
`_lane_loop` 를 돌리기 전에 `worker.stopping` 에 갈아 끼운다. 잠자는 **값**을 재는 것이지 잠자는 **시간**을
재지 않는다.

**시간 단언 금지.** 어떤 시나리오도 `elapsed < N ms` 를 쓰지 않는다. 같은 프로브가 로컬에서 0.03 ms 와
275.9 ms 를 냈고(§15 C1) CI 러너는 그 사이 어디든 나올 수 있다 — 두 값 사이에 그은 선은 동전 던지기다.
기다림은 전부 **마감 있는 join/폴링**이고, 마감을 넘기면 「느리다」가 아니라 「멈췄다」는 뜻이다.
증명은 **횟수**(트랜잭션 창 수 · 이벤트 수 · 요청 수)와 **결과**(어떤 잡을 받았나 · 어떤 상태 코드인가)로 한다.

**픽스처 크기의 근거**(스크래치 프로브, SQLite 3.53.2, 2026-09-09 — 저장소는 안 건드렸다):

| 픽스처 | `ANALYZE` 전 플랜 | `ANALYZE` 후 플랜 | `sqlite_stat1` |
|---|---|---|---|
| 빈 DB | `SEARCH jobs USING INDEX jobs_pool` | **안 바뀐다** | 표는 생기고 **행은 0** |
| queued 1 (다른 상태 없음) | `jobs_pool` | **안 바뀐다** | 6행 |
| **완료 2 + queued 1** | `jobs_pool` | **`SEARCH jobs USING INDEX jobs_state`** | 6행 |
| 완료 99 + queued 3 | `jobs_pool` | `jobs_state` | 6행 |

즉 **완료 2 + queued 1, 세 행이면 플랜이 뒤집힌다** — 큰 픽스처가 필요 없다. 대신 **행이 하나도 없거나
전부 같은 상태면 안 뒤집힌다**(`state` 가 선택적이지 않다). 그리고 `USE TEMP B-TREE FOR ORDER BY` 는
**어느 경우에도 사라지지 않는다**(`ORDER BY priority DESC, id` 를 만족하는 인덱스가 없다) — 그것이
없어지는 것을 단언하면 안 된다.

## `tests/test_store.py` — `Store.add_markers` (§15 C1)

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 1 | **한 트랜잭션**. `enqueue` → `claim(1, at(1))` → 트레이스를 걸고 `store.add_markers(j.id, [("steps","3"),("step","build"),("step","test")], at(2))`. 결과: `store.markers(j.id)` 가 **넣은 순서 그대로** 세 개이고 `at` 이 **셋 다 `at(2)`** · 트레이스에서 `BEGIN` 으로 시작하는 줄이 **정확히 1개**(`BEGIN IMMEDIATE`) · `COMMIT` 1개 · `ROLLBACK` 0개 · `INSERT INTO events` **3줄** · 끝난 뒤 `conn.in_transaction is False` | `test_add_markers_writes_every_line_in_order_in_one_transaction` | 행 수만 세면 오늘의 코드도 통과한다. **창 수**를 세야 256 KB flush 중 claim 이 275.9 ms → 0.03 ms 가 된 이유를 잠근다 |
| 2 | **빈 목록은 아무 일도 안 한다**. 트레이스를 걸고 `store.add_markers(j.id, [], at(2))`. 결과: 예외 없음 · 트레이스에 `BEGIN` 0개 · `INSERT INTO events` 0개 · `store.markers(j.id) == []` · `in_transaction is False` | `test_add_markers_with_an_empty_list_opens_no_transaction` | 마커 없는 로그 본문이 압도적으로 흔하다. 그때 `BEGIN IMMEDIATE` 를 잡으면 writer 락을 공짜로 뺏는다 |
| 3 | **중간 실패는 배치째 되돌린다**. 먼저 정상 배치로 마커 2개를 쓴다. 그 다음 연결을 감싸(`Store._conn` 을 프록시로) **두 번째 `INSERT INTO events` 에서** `sqlite3.OperationalError("database is locked")` 를 던지게 하고 마커 3개짜리 배치를 부른다. 결과: 그 예외가 **`add_markers` 밖으로 그대로 올라온다**(오늘처럼 요청이 실패한다 — 삼키지 않는다) · `store.markers(j.id)` 는 **앞의 2개 그대로**(부분 마커 0개) · `events` 행 수가 배치 전과 같다 · 트레이스가 `ROLLBACK` 으로 끝나고 `COMMIT` 이 없다 · **직후의 `store.claim` 이 정상 동작한다**(열린 트랜잭션이 안 남았다) | `test_add_markers_rolls_the_whole_batch_back_and_leaves_no_open_transaction` | 「아무것도 fail-open 하지 않는다」. 그리고 배치가 트랜잭션을 열어 둔 채 죽으면 그 다음 **모든** 쓰기가 5초 `busy_timeout` 에 걸린다 — 고치려던 병목보다 나쁜 병목이 된다 |
| 4 | **단수 `add_marker` 는 그대로 산다**. `store.add_marker(j.id, "step", "build", at(3))` 한 번 → 마커 1개 · 오늘과 같은 값. 남는 호출부: `src/remote_ci_monitor/worker.py:407`(로컬 워커의 `_LocalObserver.output`) · `tests/test_store.py:282·283·434` · `tests/test_janitor.py:270·386` | `test_add_marker_singular_still_writes_one_marker_for_the_local_worker` | 오늘 초록인 회귀 잠금이다. **지우기로 하면** 위 여섯 자리를 전부 고쳐야 한다 — 이 시나리오가 그 목록이다(Q8) |
| 5 | **덩치가 커도 창은 하나**. 마커 500줄을 한 번에 `add_markers`. 결과: `BEGIN` 1개 · `COMMIT` 1개 · `INSERT INTO events` 500줄 · `len(store.markers(j.id)) == 500` 이고 순서가 입력과 같다. 500 이라는 수에 대한 시간 단언은 **없다** | `test_add_markers_of_five_hundred_lines_still_opens_exactly_one_writer_window` | §15 C1 의 「11.5k~31k txn/s」가 「1 txn」이 되는 것이 이 PR 의 전부다. 창 수가 줄 수에 **비례하지 않는다**를 크기를 바꿔 두 번 재서(3줄·500줄) 못 박는다 |
| 6 | **열린 창 하나는 claim 이 기다려 낸다**(결정적 재현). 두 번째 연결(`sqlite3.connect(store.path, …)`)에서 `BEGIN IMMEDIATE` + `INSERT INTO events` 로 **배치가 떠 있는 상태**를 만든다. 다른 스레드에서 `store.claim(1, at(5))` 을 부른다. 그 스레드는 아직 안 끝난다. 두 번째 연결이 `COMMIT` 하면 claim 이 **queued 잡을 돌려주고** 끝난다. 결과: `thread.join(10)` 뒤 `thread.is_alive() is False` · 반환값이 그 잡(`None` 도 아니고 `sqlite3.OperationalError` 도 아니다) · 경과 시간 단언 **없음** | `test_a_claim_waits_out_one_open_writer_window_and_still_gets_the_job` | 회귀 가드의 결정적 형태. 배치 전에는 창이 마커 줄 수만큼 있었고 4 MB 본문에서 `busy_timeout` 이 터져 워커가 5.43초 뒤 **HTTP 500** 을 받았다(§15 C1). 여기서 잠그는 성질은 「빠르다」가 아니라 **「창이 하나이고, 그 하나는 기다리면 지나간다」** 다 |

## `tests/test_worker_api.py` — `worker_log` 의 SSE · `_try_claim` 의 503 (§15 C1 · C1b)

`WorkerServer` 는 기본이 `workers=False` 라 로컬 레인 스레드가 없다 — 트레이스에 남는 SQL 은 이 시험이
낸 것뿐이다. 마커 SSE 는 `srv.app._on_marker`(`server.py:394`)를 기록기로 갈아 끼워 본다
(`worker_log` 가 `self._on_marker(...)` 로 인스턴스 속성을 찾는다).

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 7 | **SSE 는 커밋 뒤에, 줄마다 하나, 순서대로**. `running_job(srv)` · `phase executing` · `srv.app._on_marker` 를 `("publish", kind, value)` 를 붙이는 기록기로, `sql_trace` 는 **같은 리스트**에 `("sql", stmt)` 를 붙인다(하나의 시간축). 본문 `b"::rcm::steps::3\nplain\n::rcm::step::build\n::rcm::step::test\n"`. 결과: 응답 `{"markers": 3}` · `publish` 항목이 **정확히 3개**이고 `[("steps","3"),("step","build"),("step","test")]` 순서 · **모든 `publish` 의 인덱스가 그 요청의 `COMMIT` 인덱스보다 크다** · 그 요청의 `BEGIN` 은 1개 | `test_log_publishes_one_marker_event_per_line_after_the_write_commits` | SSE 계약은 **마커당 하나**다. 배치가 이벤트를 합치거나 순서를 바꾸면 화면의 스텝이 어긋난다. 그리고 커밋 **전에** 발행하면 롤백된 마커를 구독자가 이미 본 뒤다 — §15 의 성능 고침이 정합성 버그를 낳는 자리 |
| 8 | **마커 없는 본문은 트랜잭션을 안 연다**. 본문 `b"hello world\nno markers here\n"`. 결과: 200 · `{"markers": 0}` · 트레이스에 `INSERT INTO events` **0줄**, `BEGIN` **0개** · 그런데 `last_output_at` 은 갱신됐다(`row(jid)["progress"]["last_output_at"] == iso(now)`) · `log_text(jid)` 는 보낸 바이트 그대로 · `publish` 0개 | `test_a_log_body_without_markers_opens_no_marker_transaction` | 로그 폭주의 대부분이 이 경우다. 「줄마다 파싱은 하되 쓸 게 없으면 창을 안 연다」가 지켜지는지는 트레이스로만 보인다 |
| 9 | **요청이 배치의 경계다**. `b"::rcm::step::bu"` → 200 · `markers 0` · `BEGIN` 0개 · `publish` 0개. 이어서 `b"ild\n::rcm::summary::all good\n"` → 200 · `markers 2` · 그 요청의 `BEGIN` **1개** · `publish` 2개. 최종 `store.markers(jid)` == `[("step","build"),("summary","all good")]` | `test_a_marker_split_across_two_requests_opens_the_transaction_only_in_the_second` | 기존 `test_log_joins_a_marker_split_across_two_requests`(`:938`)는 **한 글자도 안 바뀌고 초록이어야 한다**. 이 시나리오는 거기에 창 수만 얹는다 |
| 10 | **바쁜 DB 는 503, 500 이 아니다**. `srv.registered("build-02")` · queued 잡 하나 · `monkeypatch.setattr(srv.store, "claim", boom)`(`boom` 은 `sqlite3.OperationalError("database is locked")`) · `POST /worker/claim {"lane":1,"wait_seconds":0}`. 결과: **503** · 헤더 `Retry-After` 가 있고 **양의 정수로 파싱된다** · 본문 `{"error": …}` 에 SQL 문장도 트레이스백도 **없다** · `GET /api/status` 의 `server.last_error` 가 **여전히 `None`** · `boom` 을 걷으면 같은 요청이 200 으로 잡을 준다 | `test_a_busy_database_makes_claim_503_with_retry_after_not_500` | 오늘은 일반 500 으로 새고(`remote_workers.py:348` 이 `LaneBusy` 만 잡는다) 워커가 뜻 모를 메시지에 2초 잔다 — 레인 7.4초 정지. 게다가 500 은 `record_error` 를 타 `last_error` 를 더럽혀(`server.py:1520`) 운영자가 없는 병을 쫓는다 |
| 11 | **long-poll 슬롯을 안 흘린다**. `claim` 이 **첫 번째 호출엔 `None`**, 두 번째부터 `OperationalError` 를 던지게 한다(루프 안쪽 `remote_workers.py:330` 경로). `wait_seconds=20` 으로 요청. 결과: 503 이 돌아오고 · 그 뒤 `srv.app._claim_slots.acquire(blocking=False)` 가 **`CLAIM_WAIT_SLOTS`(8) 번 연속 성공**한다(전부 되돌려 놓는다) · 이어지는 정상 claim 이 200 | `test_a_busy_database_during_the_long_poll_releases_the_claim_slot` | 예외가 `try/finally` 안에서 나므로 슬롯은 풀려야 한다. 안 풀리면 8번의 DB 딸꾹질로 long-poll 이 영구히 죽고, 그 증상은 「워커가 느려졌다」로만 보인다 |
| 12 | **409 와 정상 경로는 그대로**. 같은 워커·레인에 잡이 이미 있을 때 claim → **409** 이고 문구가 `lane 1 already has job #<id>` · 아무것도 monkeypatch 하지 않은 claim 은 200 이고 payload 가 오늘과 같다(`job`·`preset`·`tree_url`) | `test_lane_busy_is_still_409_and_a_healthy_claim_is_unaffected` | `except Exception → 503` 으로 넓게 잡으면 `LaneBusy` 도 503 이 되어 **레인 과할당 금지가 조용히 풀린다**. 오늘 초록인 회귀 잠금(`:725`)에 붙는 짝 |
| 13 | ⚠️ **Q2 가 정해져야 쓸 수 있다.** `store.get_paused`(`remote_workers.py:344`) 와 `store.touch_worker`(`:320`)도 같은 DB 를 두드린다. 이들이 `OperationalError` 를 낼 때도 503 인가, 아니면 오늘처럼 500 인가 | `test_a_busy_database_in_get_paused_is_also_503` | 명세는 「`_try_claim` 이 `OperationalError` 를 503 으로」만 말한다. **claim 만** 감싸면 같은 요청의 두 줄 위에서 나는 같은 오류가 여전히 500 이다 — 반쪽 고침 |

## `tests/test_janitor.py` — sweep 의 `ANALYZE` (§15 D6)

오늘 `src/` 어디에도 `ANALYZE` 도 `PRAGMA optimize` 도 없다(`grep -rn "ANALYZE\|PRAGMA optimize" src/`
→ 0건). 아래 픽스처는 **완료 2 + queued 1** 이상을 만든다(위 표의 근거).

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 14 | **sweep 이 `ANALYZE` 를 돌리고 통계를 남긴다**. `env` 픽스처 + 완료 2 · queued 1 + 기간 지난 잡 하나 · `sql_trace` · `jan.sweep_once(NOW)`. 결과: 트레이스에 `ANALYZE` 로 시작하는 문장 **정확히 1개** · `sqlite_master` 에 `sqlite_stat1` 이 있고 **`SELECT count(*) FROM sqlite_stat1 > 0`** · `rec.errors == []` · 반환값(지운 잡 수)이 `ANALYZE` 를 넣기 전과 같다 | `test_sweep_runs_analyze_once_and_leaves_statistics_behind` | 「표가 있다」만으로는 부족하다 — 실측: **빈 DB 에 `ANALYZE` 하면 표는 생기고 행은 0개**다. 행이 있어야 플래너가 쓴다 |
| 15 | **sweep 보다 자주 돌지 않는다**. 기간 지난 잡을 **5개** 만들고 `sweep_once` 를 3번 부른다. 결과: `ANALYZE` 문장이 **정확히 3개**(잡 5개에 5번이 아니라 sweep 1번에 1번) · 두 sweep 사이에는 0개 | `test_analyze_does_not_run_more_often_than_the_sweep` | 20만 행에서 `ANALYZE` 는 공짜가 아니다. 자리는 기본 1시간짜리 주기 sweep(`retention_sweep_interval_seconds = 3600`)이지 purge 루프 안이 아니다 |
| 16 | **실패해도 sweep 을 죽이거나 자르지 않는다**. `ANALYZE` 만 `sqlite3.OperationalError("database is locked")` 로 실패시킨다(Q3 이 정하는 이음매에 따라 `Store.analyze` 를 monkeypatch 하거나 `Store._conn` 프록시가 `ANALYZE` 로 시작하는 문장에서 던진다). 기간 지난 잡 하나. 결과: `sweep_once(NOW)` 가 **예외를 안 내고 1을 반환** · `jobs/<id>`·`workspaces/<id>` 가 지워졌다 · `store.get_job(id).artifacts_purged_at == NOW` · `jan.last_sweep_at == NOW` · `jan.dead is None` · 오류는 `on_error` 한 줄로만 나온다(문구에 SQL 없음) | `test_a_failing_analyze_neither_fails_nor_shortens_the_sweep` | `sweep_once` 밖으로 나간 예외는 janitor 스레드를 죽이고 `/api/health` 를 503 으로 만든다(`test_dead_janitor_thread_shows_in_last_error_and_health:424`). **통계 갱신이 보존 정리를 죽일 수 있으면 안 된다.** 그리고 순서도 중요하다 — `ANALYZE` 는 purge·delete 가 끝난 **뒤**에 둔다(자르지 않는다) |
| 17 | **플랜이 `jobs_pool` 을 그만 부른다**. 완료 2 + queued 1 을 만든 뒤 `store.claim` 의 **실제 서브쿼리 문장**(`store.py:869-874`)으로 `EXPLAIN QUERY PLAN` 을 본다(구현이 상수로 빼면 import 하고, 아니면 시험이 같은 문장을 들고 있되 주석에 `store.py:869` 를 적는다). 결과: sweep **전** 에는 플랜 줄에 `jobs_pool` 이 있고, sweep **후** 에는 **어느 줄도 `jobs_pool` 을 안 부른다**(`"jobs_pool" not in " ".join(rows)`) · 바깥 검색이 `jobs_state` 를 부른다. **`USE TEMP B-TREE FOR ORDER BY` 가 사라지는 것은 단언하지 않는다**(실측: 어느 경우에도 남는다) · 빈 DB 로는 이 단언이 성립하지 않으므로 행을 먼저 넣는다 | `test_the_claim_plan_stops_naming_jobs_pool_once_statistics_exist` | 이 한 줄이 20만 행에서 16.5 ms → 0.004 ms(3727배)다. 플랜을 안 보면 `ANALYZE` 줄을 지워도 나머지 테스트가 전부 초록이라 **회귀가 조용히 지나간다**. 단언을 「`jobs_pool` 을 안 부른다」는 **부정형**으로 두는 이유: 전부 queued 인 표에서는 `SCAN jobs` 가 나오고 플래너 문구는 SQLite 판올림마다 바뀐다 |
| 18 | ⚠️ **Q4 가 정해져야 쓸 수 있다.** 통계는 연결을 안 넘는다. 실측(SQLite 3.53.2): sweep 전에 열어 둔 연결은 다른 연결이 `ANALYZE` 를 돌린 **뒤에도** `jobs_pool` 을 계속 쓰고, **두 번째 `ANALYZE` 뒤에도** 그대로다. 새로 연 연결은 `jobs_state` 를 쓰고, 그 연결이 스스로 `PRAGMA optimize` 를 돌리면 바뀐다 | `test_statistics_do_not_reach_a_connection_that_was_already_open` | `Store._conn()` 은 스레드 로컬이다. HTTP 요청 스레드는 요청마다 닫으니(`server.py:1492`) 새 플랜을 받지만, **로컬 레인 스레드(`worker.py`)와 janitor 스레드는 `store.close()` 를 한 번도 안 부른다** — 로컬 레인은 프로세스가 사는 내내 16.5 ms 플랜을 쓴다. 답이 정해지기 전에는 3727배가 **HTTP 경로에만** 참이다 |

## `tests/test_worker_client.py` — `CLAIM_MIN_INTERVAL` 지터 (§15 C4)

`RemoteWorker.__init__` 은 이미 `now_fn` 을 주입받는다(`remote_worker.py:165`) — 난수도 **같은 방식으로**
`rand_fn: Callable[[], float] = random.random` 을 하나 더 받는다. 시험은 값을 정해 주므로 결정적이다.
클라이언트는 이 파일의 `FakeServer` 가 아니라 `claim` 만 대본대로 돌려주는 최소 스텁을 쓴다 — 재는 것이
HTTP 가 아니라 **루프의 잠자기 값**이기 때문이다.

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 19 | **빈 204 뒤의 잠자기 = 기본 + 지터**. `rand_fn=lambda: 0.25`, 스텁 `claim` 은 즉시 `None`. `worker.stopping = RecordingEvent()` 로 갈아 끼우고 `_lane_loop(1)` 을 스레드로 돌린 뒤 첫 기록이 남으면 `stopping.set()`. 결과: 기록된 첫 `wait` 값이 **`CLAIM_MIN_INTERVAL + 0.25 == 1.25`**. `rand_fn` 을 `0.0` 으로 하면 정확히 `1.0`, `0.999` 로 하면 `1.999` — 즉 **`[1.0, 2.0)`** | `test_an_empty_claim_sleeps_the_base_interval_plus_the_injected_jitter` | 오늘은 고정 1.0 이라(`remote_worker.py:314-315`) 빠른 204 를 받은 레인들이 같은 1 Hz 격자에 묶여 **영영 안 흩어진다**. 32레인이면 `max_concurrent_requests = 32` 가 정확히 만석이고 48레인에서 첫 `/api/status` 503, 128레인이면 워커 claim 406건 거절 |
| 20 | **호출마다 다시 뽑는다**. `rand_fn` 이 `[0.1, 0.7, 0.4]` 를 차례로 돌려준다. 빈 claim 세 번. 결과: 기록된 `wait` 가 **`[1.1, 1.7, 1.4]`** — 값이 매번 바뀐다(레인마다 한 번 뽑고 마는 것도, 기동 시 한 번 뽑는 것도 아니다) | `test_the_jitter_is_drawn_again_for_every_empty_claim` | 레인마다 고정 오프셋을 주면 각 레인이 **자기만의 고정 1 Hz 격자**를 갖는다 — 실측의 「503 406 → 36건(91% 감소)」은 폴링마다 다시 뽑는 모델의 숫자다 |
| 21 | **정지는 지터를 안 기다린다**. `rand_fn=lambda: 0.99`(잠자기 1.99초). 첫 `wait` 가 시작된 것을 보면 `worker.stopping.set()`. 결과: 기록된 값은 `1.99` 인데 **스레드가 `join(5)` 안에 끝난다**(`thread.is_alive() is False`) — 잠자기가 `time.sleep` 이 아니라 `Event.wait` 라서다. 경과 시간 단언 **없음**(마감은 「멈췄다」를 잡는 용도) | `test_a_stop_cuts_the_jittered_wait_short` | SIGTERM 이 레인마다 최대 2초씩 붙들리면 안 된다. 오늘 `self.stopping.wait(...)` 인 것을 `time.sleep` 으로 바꿔 「지터를 정확히 자게」 만드는 구현을 막는다 |
| 22 | **`--once` 는 지터로 늦어지지 않는다**. `once=True`, 스텁 `claim` 이 payload 를 한 번 주고, `run_claimed` 는 아무것도 안 하는 스텁. 결과: 잡 하나 뒤 `stopping.is_set()` 이고 루프가 끝난다 · 기록된 `wait` 목록에 **`CLAIM_MIN_INTERVAL` 이상 값이 하나도 없다**(잡을 받은 경로는 빈-204 가지를 안 지난다) · `worker.processed == 1` | `test_once_is_not_delayed_by_the_jitter` | `--once` 는 시험·cron 용이다(`cli.py:1352`). 잡을 받은 뒤의 종료 경로에 지터가 끼면 모든 `--once` 호출이 최대 2초 길어진다 |
| 23 | **503 은 여전히 일시적이다**. 스텁 `claim` 이 `ClientError(503, "database is busy")` 를 두 번 던지고 세 번째에 payload. `log` 는 기록기. 결과: 루프가 계속 돌아 `worker.processed == 1` · **`lane 1: claim:` 로 시작하는 로그 줄이 없다**(오늘 `:306-308` 가지는 조용하다) · `register()` 를 다시 부르지 않았다 · `stopping` 이 안 켜졌다 | `test_a_503_claim_is_transient_and_does_not_stop_or_re_register_the_lane` | 서버 쪽 503(시나리오 10)은 워커가 그것을 일시적으로 볼 때만 값이 있다. 이 시험은 **오늘 이미 초록**이어야 한다 — 새 상태 코드가 기존 가지에 실제로 들어맞는지를 확인하는 잠금이다 |

## `tests/test_e2e_m5f.py` — 폭주해도 500 이 없다

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 24 | **마커 폭주 중에도 claim 이 산다**. in-process `WorkerServer`(`workers=False`) · `build-02` 로 running 잡 하나 · `build-03` 을 등록하고 queued 잡을 하나 더 넣는다. 스레드 A 가 마커 **4000줄**(4 MiB 상한보다 충분히 작게)을 **한 요청**으로 `POST /worker/jobs/{id}/log`, 스레드 B 가 동시에 `POST /worker/claim {"lane":1,"wait_seconds":0}` 을 5번 친다. 결과: 로그 요청 **200** 이고 `{"markers": 4000}` · claim 응답이 전부 **200 또는 204**, **500 은 0건** · `GET /api/status` 의 `server.last_error is None` · 트레이스에서 그 로그 요청의 `BEGIN` 이 **1개** · 두 스레드 모두 `join(30)` 안에 끝난다. 초 단위 단언 **없음** | `test_a_marker_flood_never_returns_500_and_a_concurrent_claim_still_works` | §15 C1 의 증상 그대로다 — 4 MB 본문에서 `busy_timeout` 이 터져 워커가 **5.43초 뒤 HTTP 500** 을 받았고 `database is locked` 가 로그에 찍혔다. 단언은 **상태 코드와 횟수**로만 하고 「빨라졌다」는 재지 않는다 |

**24 건.** 파일별로 6(store) · 7(worker_api) · 5(janitor) · 5(worker_client) · 1(e2e) 이다.

## 잠근 선택 (명세가 안 정한 것 — 구현이 달리 정하면 테스트를 고쳐야 한다)

1. **`add_markers(job_id, items, at)` 의 모양** (1·2·3·5). `items` 는 `(kind, value)` 쌍의 시퀀스, `at` 은
   배치 전체에 **하나**. `Marker` 객체 목록이나 항목별 `at` 도 가능한 설계지만 시험은 튜플 쌍을 쓴다.
   반환값은 안 잠갔다(`None` 이든 쓴 개수든 시험은 안 본다).
2. **예외는 삼키지 않는다** (3). SQLite 오류는 그대로 올라오고 요청은 **오늘처럼** 실패한다. 「부분이라도
   쓴다」도 「조용히 버린다」도 아니다. 다만 열린 트랜잭션은 절대 안 남긴다.
3. **빈 목록은 트랜잭션도 안 연다** (2). 「`BEGIN` 은 열되 아무것도 안 쓴다」가 아니다.
4. **SSE 는 커밋 뒤 두 번째 루프에서, 마커당 하나, 순서대로** (7). 발행을 배치 하나로 합치지 않는다 —
   `KIND_MARKER` 의 payload 는 `{job_id, kind, value}` 로 오늘 그대로다(`server.py:396`).
5. **단수 `add_marker` 는 남는다** (4). 로컬 워커(`worker.py:407`)가 계속 쓴다. 지우기로 하면 Q8.
6. **`_try_claim` 의 매핑** (10·12). `sqlite3.OperationalError` → 503 + `Retry-After`,
   `LaneBusy` → 409(그대로), 그 밖 → 오늘 그대로. 503 은 `record_error` 를 **안** 탄다.
7. **지터는 `CLAIM_MIN_INTERVAL + rand_fn()`** (19·20). `rand_fn` 은 `now_fn` 옆에 주입하고 기본값은
   `random.random`, 범위는 **`[1.0, 2.0)`**. 가드(`remote_worker.py:314` 의 `< CLAIM_MIN_INTERVAL`)는
   **기본값과** 비교한다(잠자는 값만 흔든다). Q5.
8. **`ANALYZE` 는 `sweep_once` 안에서, purge·delete 가 끝난 뒤 한 번** (14·15·16). `PRAGMA optimize` 가
   아니라 `ANALYZE` 다(§10 의 문구). Q3.
9. **플랜 단언은 부정형** (17). 「`jobs_state` 를 쓴다」가 아니라 **「`jobs_pool` 을 안 쓴다」**. 그리고
   `USE TEMP B-TREE FOR ORDER BY` 는 남는 것이 정상이다.
10. **시간을 재는 단언은 하나도 없다** (전부). 창 수 · 이벤트 수 · 요청 수 · 상태 코드로만 증명한다.

## 명세가 안 정한 것 (테스트 작성자가 혼자 못 정한다)

1. **Q1 — `Retry-After` 의 값과 배선.** `ApiError(status, message, **extra)` 의 `extra` 는 **본문**으로
   가고(`server.py:153-157`), `_send_error` 에는 401 말고는 헤더를 붙일 자리가 없다(`:1463-1479`).
   `ApiError` 에 `headers` 를 더할지, `_send_error` 가 503 을 특례로 처리할지 명세에 없다. **값**도 없다 —
   저장소의 유일한 선례는 SSE 과부하의 `Retry-After: 10`(`server.py:1870`)이고, 워커는 고정 2.0초를 자며
   헤더를 **읽지 않는다**(`ClientError` 가 헤더를 안 들고 온다, `client.py:63-67`). 정해질 때까지
   시나리오 10 은 「헤더가 있고 양의 정수로 파싱된다」까지만 잠근다.
2. **Q2 — 무엇이 503 이 되나.** 명세는 `sqlite3.OperationalError` 라고만 한다. 그 클래스에는
   `no such table: jobs` 같은 **영구 고장**도 들어 있어서, 그대로 감싸면 진짜 고장이 영원히 「일시적」으로
   보고되고 `last_error` 에 영영 안 뜬다. `database is locked`/`is busy` 로 좁힐 것인가?
   그리고 같은 요청의 `store.get_paused()`(`remote_workers.py:344`)·`store.touch_worker()`(`:320`)도
   같은 그물에 넣나(시나리오 13)?
3. **Q3 — `ANALYZE` 의 범위와 주인.** `ANALYZE`(전체) · `ANALYZE jobs` · `PRAGMA optimize` 중 무엇인가.
   그리고 누가 부르나 — 새 `Store.analyze()`(계층이 지켜지고 실패 주입이 쉽다)인가, janitor 가
   `store._conn()` 을 직접 만지는가. 시나리오 16 의 이음매가 여기서 갈린다.
4. **Q4 — 통계가 연결을 안 넘는다(실측).** SQLite 3.53.2 에서, 다른 연결이 `ANALYZE` 를 돌려도
   **이미 열려 있던 연결은 옛 플랜(`jobs_pool`)을 계속 쓴다**. 두 번째 `ANALYZE` 뒤에도 그대로다.
   새로 연 연결은 새 플랜을 쓰고, 옛 연결도 스스로 `PRAGMA optimize` 를 돌리면 바뀐다.
   `Store._conn()` 은 스레드 로컬이고 **로컬 레인 스레드(`worker.py`)와 janitor 스레드는 `store.close()`
   를 한 번도 안 부른다** — 그러면 로컬 레인은 프로세스가 사는 내내 16.5 ms 플랜이다. `_conn()` 이 열릴 때
   `PRAGMA optimize` 를 같이 걸까, 주기적으로 다시 열까, 아니면 「HTTP 경로만 빨라진다」로 받아들일까?
5. **Q5 — 지터의 정확한 산술과 범위.** §4.5·§15.2 는 「0~1초 지터」라고만 한다.
   `CLAIM_MIN_INTERVAL + U(0,1)`(→ `[1.0, 2.0)`)인지 `U(0, CLAIM_MIN_INTERVAL)`(→ `[0.0, 1.0)`, 이러면
   **바닥이 낮아져 요청률이 오른다**)인지, 그리고 `remote_worker.py:314` 의 가드가 기본값과 비교하는지
   흔든 값과 비교하는지가 없다. 시나리오는 `[1.0, 2.0)` + 가드는 기본값으로 잠갔다.
6. **Q6 — 다른 동기화된 잠자기도 흔드나.** 503·연결 실패 가지는 평평한 `2.0`(`remote_worker.py:307`·`:310`),
   `report()` 의 재시도는 평평한 1·2·4초(`:195`·`:209`)다. 128레인이 동시에 503 을 받으면 **2초 격자로
   다시 뭉친다** — 지터가 깨려던 바로 그 lockstep 이다. 이 PR 범위인가?
7. **Q7 — 배치의 `at`.** 오늘 `worker_log` 는 요청 전체에 `now` 하나를 쓰고(`remote_workers.py:410`)
   그 요청의 마커가 전부 같은 `at` 을 갖는다. `add_markers(…, at)` 는 그대로 이어받는다. 확인이 필요한
   이유: M5d-2 가 마커 `at` 으로 스텝 초를 재므로 **한 flush 안의 두 스텝은 0초로 잰다**.
8. **Q8 — 로컬 워커도 배치하나.** `worker.py:399-413` 의 `_LocalObserver.output` 은 같은 모양이다 —
   줄마다 `add_marker` + `on_marker` + `_changed`. §15 는 원격 경로만 쟀다. 배치하지 않으면 단수
   `add_marker` 는 남아야 한다(잠근 선택 5). 배치하면 시나리오 4 가 「호출부 목록」으로 바뀐다.
9. **Q9 — 503 본문에 종류 코드를 넣나.** 상태 문서는 DB 오류를 `database_unavailable` 로 분류한다
   (`server.py:1420-1422`). claim 의 503 본문에도 같은 코드를 실을지 — 워커는 안 읽지만 `rcm check` 와
   웹이 「서버가 문장이 아니라 코드를 보낸다」(결정 37)를 지키려면 필요할 수 있다.

## 검증 방법 (구현 전 · 후)

- **구현 전에 빨개야 하는 것**: 1·2·3·5·6 은 `AttributeError: 'Store' object has no attribute
  'add_markers'` · 7·8·9 는 창 수에서(오늘 트레이스는 autocommit `INSERT` N줄에 `BEGIN` 0개) ·
  10·11 은 500 을 받아서 · 14~17 은 `ANALYZE` 가 없어서 · 19~22 는 `rand_fn` 이 없거나 잠자기가 평평한
  1.0 이라서 · 24 는 `database is locked` 나 500 으로.
- **구현 전에도 초록이어야 하는 것(회귀 잠금)**: 4 · 12 · 23. 이 셋이 처음부터 빨갛다면 픽스처가 틀린
  것이지 기능이 없는 것이 아니다.
- **한 글자도 안 바뀌고 초록으로 남아야 하는 기존 테스트**:
  `tests/test_worker_api.py::test_log_appends_raw_bytes_and_parses_markers_on_the_server`(`:900`) ·
  `::test_log_joins_a_marker_split_across_two_requests`(`:938`) ·
  `::test_claim_refuses_a_lane_that_already_has_a_job`(`:725`) ·
  `tests/test_store.py::test_markers_roundtrip_and_last_output`(`:279`) ·
  `tests/test_janitor.py` 의 마커를 쓰는 두 곳(`:270`·`:386`) ·
  `tests/test_worker_client.py::test_a_503_is_raised_immediately_without_retries`(`:480`).
  진행률(`progress`)이 안 바뀐다는 성질은 이들이 **그대로 초록**인 것으로 증명한다 — 같은 단언을
  새 파일에 복사하지 않는다.
- **픽스처 검증**: 시나리오 17·18 의 플랜 단언은 구현 전에 스크래치에서 한 번 재 보고 쓴다. 위
  「픽스처 크기의 근거」 표가 그 결과다(SQLite 3.53.2 · 완료 2 + queued 1 이면 뒤집힌다 · 빈 DB 는
  안 뒤집힌다 · temp B-tree 는 남는다). 다른 SQLite 판에서 문구가 달라지면 **부정형 단언**(잠근 선택 9)만
  살아남는다.
- 전부 벽시계 sleep 없이 돈다. 유일한 기다림은 6·21·24 의 `join(마감)` 이고, 마감은 「느리다」가 아니라
  **「멈췄다」**를 잡는 값이다(10·5·30초).
