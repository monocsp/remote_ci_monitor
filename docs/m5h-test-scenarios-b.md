# M5h 테스트 시나리오 B — 저장소와 서버 (2026-09-09)

`docs/m5h-implementation.md` §1.2(`outcome_for` 의 상태별 스텝 라벨) · §1.3(DB v11 —
`jobs.last_step`) · §2.1(DB v12 — 실패 대장 · `finish` 의 같은 트랜잭션 · `failure_stats` ·
삭제) · §2.3(`GET /jobs/{id}` 의 `failures[]`) · §2.4(설정 두 키) · §3(404 의 `hint`)을 새
`tests/test_store_m5h.py` · 새 `tests/test_server_m5h.py` 로 옮긴 것이다(test-first, 역할 B).
**§1.1 마커 파서와 규칙 R1~R6 · §2.2 `core/failures.py` 의 판정 함수는 A 의 몫이고, §1.5 표시
문구 · §2.5 실패 끝줄 · §2.6 웹 · §4 · §5 는 C 의 몫이다** — 여기서는 「값이 저장되고 문서로
나가는 자리」만 본다. 판정 낱말(`unknown`·`first_seen`·`intermittent`·`persistent`)은 A 가
정하고, B 는 **서버가 그 낱말을 창·최소값과 함께 제대로 넘겨 주는가**만 잠근다.
`src/` 와 기존 테스트, 그리고 A·C 의 파일은 **하나도 안 건드렸다**.

65개 테스트 함수(파라미터를 풀면 70건) 중 **오늘 초록은 셋뿐**이다(#14 · #42 · #65) — 셋 다
「고치다 깨뜨리는 것」을 막는 대조군이고, 나머지 67건은 구현이 없어서 빨갛다.

> 이 문서는 `failure_stats` 가 **앵커 인자를 받도록 바뀐 뒤**의 계약을 따른다 —
> `failure_stats(job_id, key, finished_at, *, window)` 이고 창은 「같은 `key` 의 종료 잡 중
> **이 잡까지** 최근 N개」다(§2.1 질의 ①).

## 공통 — 픽스처와 시각

- **저장소 쪽**(`tests/test_store_m5h.py`)은 자기 `store` 픽스처(`Store(tmp_path/rcm.sqlite3)`)와
  자기 `enqueue()` 를 둔다(`tests/test_store_m5e.py` 와 같은 모양). 시각은 고정
  `NOW = 2026-09-09T12:00:00Z` 와 `at(seconds)` 뿐 — sleep 도 스레드도 벽시계도 없다.
- `finished(store, *, seconds, state, key, names, …)` 하나가 종료 잡을 만든다. **창의 순서는
  `seconds`(= `finished_at`) 로만** 만들고, 트리 해시는 `f"{key}-{seconds}"` 라 합류 신원이
  겹치지 않는다.
- `stats(store, job_id, *, key, window)` 는 앵커를 **서버가 하는 것과 똑같이** 그 잡의 행에서
  읽어 넘긴다(`store.get_job(job_id).finished_at` — §2.3 의 `job.finished_at`).
- `ledger(store, job_id)` 는 대장을 `seq` 순으로 직접 읽는다 — 읽는 공개 API 는
  `failure_stats` 뿐이라 SQL 로 본다(`raw(store)`, m5e 의 관례). 이 함수는 **seq 가 단조
  증가하는지**만 같이 확인하고 시작값은 안 본다(§「명세가 답을 안 주는 것」 3).
- **서버 쪽**(`tests/test_server_m5h.py`)은 `tests/test_worker_api.py` 의 `WorkerServer` —
  in-process HTTP · 주입한 시계(`T0`) · 로컬 워커 스레드 없음 · `admission = "always"`.
  종료 잡은 `srv.new_job()`(HTTP) 로 만들고 `srv.store.finish(...)` 로 끝낸다. 저장소를 직접
  고쳤으므로 그때마다 `srv.app._mark_dirty()` 를 부른다(상태 캐시는 잡 이벤트로만 더러워진다).
- **아직 없는 이름은 모듈 최상단에서 import 하지 않는다.** `Outcome.last_step` ·
  `finish(fail_names=…)` · `failure_stats` · `ServerSection.failure_window_jobs` 는 전부
  테스트 **안에서** 만난다 — 그래야 파일 하나가 통째로 수집 오류가 되지 않고 어느 시나리오가
  빨간지 보인다. `core/failures.FailureRow` 도 import 하지 않고 `name`·`seen`·
  `first_seen_job_id`·`last_seen_job_id` 네 칸만 읽는다(A 의 파일이다).
- `outcome_for` 는 `worker.py` 에 있지만 시나리오는 **저장소 파일**에 뒀다. 그 함수가 정하는
  세 칸이 곧 `finish()` 가 쓰는 세 칸이라, 「무엇을 저장하는가」와 한자리에서 읽힌다.

## 잠근 API (구현이 그대로 내야 한다)

- `Outcome` 에 `last_step: str | None` · `fail_names: tuple[str, ...]` · `fail_truncated: bool`.
  `__iter__` 의 3-튜플 풀기는 **그대로**(#8).
- `Store.finish(..., last_step=None, fail_names=(), fail_truncated=False)` ·
  `Store.failure_stats(job_id, key, finished_at, *, window) -> (rows, window_jobs,
  window_unnamed)`.
- `job_failures(job_id, name, seq)` · PK `(job_id, name)` · 세 열 전부 `NOT NULL` ·
  인덱스 `job_failures_name` · `jobs_key_finished` · `jobs.fail_truncated INTEGER NOT NULL
  DEFAULT 0` · `jobs.last_step TEXT`. **`_SCHEMA_V1` 에도 전부 들어간다**(새 DB 는 마이그레이션을
  건너뛴다 — #10 · #16).
- 안쪽 이름은 `fail_truncated`, 공개 JSON 키는 `failures_truncated`. 서로 옮겨 쓰지 않는다.
- `failures[]` 항목 하나의 키 집합은 **정확히** 여덟 개:
  `{name, step, seen, window, window_unnamed, first_seen_job_id, last_seen_job_id, verdict}`.
- 404 본문 두 문구(글자 하나까지):
  - `job #162 is GET /jobs/162 · its log is GET /jobs/162/log with that job's token (try: rcm logs 162)`
  - `routes: GET /api/status · GET /api/health · GET /jobs/<id> · GET /jobs/<id>/log · POST /jobs`
- 설정 오류 문구의 **머리**: `[server] failure_window_jobs must be …` ·
  `[server] failure_min_jobs must be …`(뒷부분은 명세가 안 정했다 — 머리만 단언한다).
- 불변(오늘과 같아야 한다): `/api/status` 의 `schema_version == 1` · 종료 잡 문서의
  `artifacts` 칸 · `GET /jobs/<없는 id>` 의 `no such job` · 진짜 라우트의 200.

---

## 1. `outcome_for` — 어느 상태가 스텝 라벨을 갖나 (§1.2) · `tests/test_store_m5h.py`

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 1 | `::rcm::step::test` + `::rcm::fail::test` + `::rcm::fail::port_test.dart`, rc 1 → `failed` · `failed_step == "test"` · `last_step == "test"` · `fail_names == ("test", "port_test.dart")` · `fail_truncated is False` | `test_a_failed_job_carries_the_declared_step_and_its_names` | 선언이 있는 잡은 세 칸을 **전부** 갖는다. 대장의 유일한 입구라 여기가 비면 §2 전체가 빈 표가 된다 |
| 2 | **#176 그대로** — 되재생 머리말 둘(마지막이 `디바이스 QA chunk (…)`), rc −15, `cancelled=True`, `job.cancel.by == "macbook"` → `cancelled` · `summary == "cancelled by macbook"` · `failed_step is None` · `last_step is None` · `fail_names == ()` | `test_a_cancelled_job_keeps_no_step_label_bug_176` | **이 마일스톤의 증인.** 오늘은 마지막 머리말이 `failed_step` 에 붙어 화면에 `(step 디바이스 QA chunk …)` 로 샌다(워크플랜 §2.2). 요약은 처음부터 깨끗했다는 것을 같이 못 박는다 — 「프리셋 설명이 샌다」는 오해가 여기서 끝난다 |
| 3 | 취소 직전에 `::rcm::fail::test` 를 찍었다 → 세 칸 전부 비어 있다 | `test_a_cancelled_job_that_declared_a_failure_names_nothing` | 취소는 **판정이 아니다.** 이름이 대장에 들어가면 다음 잡의 `seen` 이 부풀고 「간헐」이 사람이 누른 취소 때문에 생긴다 |
| 4 | 같은 마커에 `lost=True`, rc `None` → 세 칸 전부 비어 있다 | `test_a_lost_job_keeps_no_step_label_and_no_names` | 서버가 잃은 잡도 같다(결정 64). 재시작 한 번이 대장을 오염시키면 안 된다 |
| 5 | 열린 스텝 둘(마지막이 #162 의 `build web (…)`), `timed_out=True`, rc `None` → `timed_out` · `failed_step is None` · `last_step == "build web (…)"` · `fail_names == ()` | `test_a_timed_out_job_keeps_the_last_step_without_naming_a_failed_one` | **폴백을 켜고 들어가던 자리**(§1.2 `exit_code=None if forced else rc`). 오늘은 강제 종료에 `exit_code=1` 을 넣어 마지막 열린 스텝을 `ok=False` 로 물들이고, 그게 그대로 `failed_step` 이 된다(워크플랜 §13-2) |
| 6 | `::rcm::step::test` + `::rcm::fail::flaky_port_test.dart`, `timed_out=True` → `fail_names == ("flaky_port_test.dart",)` · `last_step == "test"` · `failed_step is None` | `test_a_timed_out_job_keeps_the_names_it_declared` | `STEP_STATES = (FAILED, TIMED_OUT)` 을 문면 그대로 잠근다. 스텝 이름이 아닌 **단위**를 지목하면 `failed_step` 은 비어 있고 대장에만 남는다(§4.2). ⚠️ 워크플랜 §4.1·결정 64 와 어긋난다 — 아래 「명세가 답을 안 주는 것」 1 |
| 7 | `::rcm::fail::test` 를 찍고 **exit 0** → `succeeded` · `summary == "all green"` · 세 칸 전부 비어 있다 | `test_a_succeeded_job_that_declared_a_failure_records_nothing` | **잡 자신의 판정이 이긴다.** 정보성 스텝이 `::rcm::fail::` 를 찍고 성공으로 끝나는 스크립트는 흔하다 — 그것까지 세면 `persistent` 가 늘 켜져 있다 |
| 8 | `state, summary, failed_step = outcome_for(...)` | `test_the_three_tuple_unpacking_is_unchanged` | `Outcome.__iter__` 는 그대로 둔다(§1.2). 칸을 셋 더하면서 튜플 길이를 바꾸면 옛 호출부가 조용히 어긋난다 |
| 9 | 서로 다른 `::rcm::fail::` 101개 → `len(fail_names) == 100` · `fail_truncated is True`. 100개면 `fail_truncated is False` | `test_a_failed_job_that_named_too_many_things_is_truncated` | 상한이 `Outcome` 까지 **전해지는지**(R6 자체는 A). 11,000줄짜리 테스트 출력이 DB 를 채우는 것을 막는 값이 여기서 끊기면 저장 쪽은 모른 채로 넘어간다 |

## 2. DB v11 — `jobs.last_step` (§1.3) · `tests/test_store_m5h.py`

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 10 | 새 DB → `DB_VERSION >= 12` · `user_version() == DB_VERSION` · `jobs.last_step` 이 있다 · `"last_step" in _SCHEMA_V1` | `test_a_fresh_database_is_at_the_newest_schema_with_last_step` | 새 DB 는 마이그레이션을 **건너뛰고** `_SCHEMA_V1` 만 실행한다. `_MIGRATIONS` 만 고치면 새 설치에 열이 없고, 10→11 만 보는 검사로는 안 잡힌다(m5e §9 에서 이미 값을 치른 함정) |
| 11 | `_MIGRATIONS[11]`·`[12]` 가 문장 하나씩의 **튜플** · 11 은 `last_step`, 12 는 `job_failures`·`fail_truncated` 를 말한다 | `test_migrations_11_and_12_are_one_statement_per_tuple_entry` | `migrate()` 는 `executescript` 가 아니라 문장 단위로 돈다. 세미콜론으로 묶으면 두 번째 문장이 조용히 안 돈다 |
| 12 | v10 DB(새 열·표를 떼고 `user_version=10`) 를 연다 → `DB_VERSION` 까지 올라오고 기존 행이 살아 있다 · 옛 잡은 `last_step is None` · `fail_truncated is False` · `job_failures`·인덱스 둘이 생겼다 · 두 번째 열기는 아무것도 안 바꾼다 | `test_a_v10_database_migrates_through_11_and_12_and_keeps_its_rows` | 운영 DB 는 v10 이다. 「모르는 값은 null」 — 옛 잡의 `last_step` 이 빈 문자열이 되면 화면이 「마지막 스텝: 」을 그린다 |
| 13 | v11 DB(대장만 떼고 `user_version=11`, `last_step` 값은 남긴 채) → 12 만 돌고 그 값이 그대로다 | `test_a_v11_database_gains_the_ledger_and_keeps_its_last_step` | 「11 을 거쳐 12 로」를 **각 단계로** 잠근다. 12 가 잡 표를 다시 만들면(그런 마이그레이션을 쓰기 쉽다) v11 이 채운 값이 사라진다 |
| 14 | `user_version = DB_VERSION + 1` 인 DB → `StoreError("… newer than this build …")` | `test_a_newer_database_is_refused` | v12 를 쓴 서버의 데이터 폴더를 옛 빌드가 열면 대장을 못 읽으면서 잡은 끝낸다 — 증거만 조용히 사라진다. **오늘 이미 초록**(회귀 잠금) |
| 15 | `finish(last_step=STEP_162)` → `Job.last_step == STEP_162`. 안 주면 `None` | `test_last_step_round_trips_through_finish` | `finish` → `_set_state` → `_job_from_row` 세 곳을 한 줄로 잇는다. 하나라도 빠지면 값은 쓰이는데 안 읽힌다 |

## 3. DB v12 — 실패 대장 쓰기·지우기 (§2.1) · `tests/test_store_m5h.py`

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 16 | 새 DB → `job_failures` 의 열이 정확히 `{job_id, name, seq}` · PK 가 `(job_id, name)` · 셋 다 `NOT NULL` · `job_failures_name` · `jobs_key_finished` · `jobs.fail_truncated` 가 `NOT NULL DEFAULT 0` · `_SCHEMA_V1` 문면 | `test_a_fresh_database_has_the_ledger_table_its_key_and_both_indexes` | PK 가 없으면 같은 이름이 두 번 들어가 `seen` 이 부풀고, `jobs_key_finished` 가 없으면 창 질의가 종료 잡 **전체**를 정렬한다(v9 에서 이미 겪었다) |
| 17 | `finish(FAILED, failed_step="test", fail_names=["test","lint"])` → 대장에 두 행 · `Job.failed_step == "test"` | `test_finish_writes_the_names_it_was_given` | 증거와 결과가 **한 번의 호출**로 남는다는 기본. 여기가 빨간 동안은 아래 창 시나리오가 전부 무의미하다 |
| 18 | 이미 끝난 잡의 두 번째 `finish` → `False` · 대장 빈 채. `only_from` 이 안 맞는 `finish` → `False` · 대장 빈 채 · 잡은 `queued` 그대로 | `test_a_rejected_finish_leaves_no_ledger_rows` | 「`finish` 가 거절되면 대장도 안 남는다」(§2.1). 원격 워커가 취소와 경쟁해 늦게 보고하면 이 갈래를 탄다 — 남으면 **끝나지도 않은 잡의 증거**가 창에 낀다 |
| 19 | `job_failures` 에 `BEFORE INSERT … RAISE(ABORT)` 트리거를 걸고 `finish(FAILED, fail_names=["test"])` → 예외가 올라오고 **잡이 `queued` 로 되돌아간다** · 대장 빈 채 | `test_a_failing_ledger_write_rolls_the_finish_back` | **mutcheck 15 의 표적.** 대장을 커밋 **뒤에** 쓰면 잡은 이미 `failed` 로 남고 증거만 사라진다 — 그러면 「실패했는데 이름이 없는 잡」이 조용히 늘고 `window_unnamed` 가 그것을 사실로 받아들인다. 트리거는 SQL 뿐이라 `src/` 를 안 건드리고 결과가 결정적이다 |
| 20 | `fail_names=["a","b","a"]` → 대장은 `["a","b"]` | `test_a_repeated_name_is_recorded_once` | PK + `INSERT OR IGNORE` 의 뜻(워크플랜 §4.3: 「같은 이름을 두 번 찍어도 한 번이다」). 매트릭스 스크립트가 같은 이름을 여러 번 찍는다 |
| 21 | `fail_names=["zeta","alpha","mid"]` → `ORDER BY seq` 가 **그 순서** | `test_the_ledger_keeps_the_order_the_job_declared` | 「이름 순이 아니라 잡이 찍은 순서」(§2.2). 저장이 정렬해 버리면 `failures_json` 이 지킬 순서가 애초에 없다 |
| 22 | 이름 없이 끝난 실패 잡 → 대장에 행이 **없다** | `test_a_job_that_named_nothing_leaves_no_ledger_rows` | `window_unnamed` 의 정의가 「대장에 행이 없는 실패 잡」이다. 빈 이름 행을 하나 넣어 두면 분모의 품질 신호가 영원히 0 이 된다 |
| 23 | `finish(fail_truncated=True)` → `Job.fail_truncated is True`, 안 주면 `False` | `test_fail_truncated_rides_on_the_job_row` | §2.3 이 `doc["failures_truncated"] = job.fail_truncated` 로 읽는 칸이다. 잡 행에 안 앉으면 문서가 늘 `false` 를 말한다 — **분자가 잘렸다는 사실을 숨기는** 유일한 방법 |
| 24 | 오래된 잡(이름 둘)과 최근 잡(이름 하나)을 purge 표시 → `delete_old_jobs(cutoff)` 가 오래된 것 하나만 지운다 · 그 잡의 대장도 사라진다 · 최근 잡의 대장은 남는다 | `test_delete_old_jobs_removes_the_ledger_with_the_job_row` | 대장은 잡 행과 같은 시계를 쓴다(`metadata_retention_days`). 안 지우면 고아 행이 `job_failures_name` 인덱스에 쌓이고, 하루 50잡 × 100이름이면 그게 곧 영구 표다 |

## 4. `failure_stats` — 창과 분모 (§2.1) · `tests/test_store_m5h.py`

`failure_stats(job_id, key, finished_at, *, window) -> (rows, window_jobs, window_unnamed)`.
`rows` 는 `name`·`seen`·`first_seen_job_id`·`last_seen_job_id` 네 칸만 읽는다(`FailureRow` 는
A 의 것). 창은 **이 잡까지** 최근 N개다 — 그래서 #29 가 「나중 잡은 안 센다」를 잠근다.

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 25 | 같은 key 로 `test` 를 찍은 실패 잡 5개, `window=3` → `window_jobs == 3` · `seen == 3` · `unnamed == 0` | `test_the_window_is_the_last_n_terminal_jobs_of_the_same_key` | 창의 기본형. 다섯 번 실패했어도 창이 셋이면 분모도 분자도 셋이다 — 「최근 N회 중」의 N 이 흔들리면 판정이 흔들린다 |
| 26 | 오래된 것부터 `failed(test)` @10 · `failed(test)` @20 · `cancelled` @30 · `lost` @40 · **앵커** `failed(test)` @50, `window=3` → `window_jobs == 3` · `seen == 3` | `test_cancelled_and_lost_jobs_are_left_out_of_the_window` | **mutcheck 14 의 표적.** 취소·유실을 **앵커와 옛 잡 사이**에 둔다 — `state IN (…)` 을 지우면 창이 `[앵커, lost, cancelled]` 가 되어 `seen` 이 3 에서 **1** 로 떨어진다. 취소·유실은 아무 말도 안 하므로 분모에서도 뺀다(워크플랜 §4.3) |
| 27 | `deploy` 키의 실패 잡이 같은 이름을 찍었다 + `gate` 잡 하나 → `window_jobs == 1` · `seen == 1` · 남의 잡 id 가 안 섞인다 | `test_another_key_is_never_in_the_window` | 창은 중앙값과 **같은 축**(`key`)을 쓴다(§13-4). 키를 안 걸면 서버 전체의 `test` 가 한 통에 섞여 모든 프리셋이 `persistent` 가 된다 |
| 28 | 같은 이름을 찍은 잡 6개, `window=3` → `seen == 3` · 가장 오래된 잡 id 가 `first_seen_job_id` 에 **안** 나온다 | `test_a_job_outside_the_window_is_not_counted` | 질의 ②의 `job_id IN (창)` 이 빠지면 대장 전체를 세고 `seen > window` 가 나온다 — 그러면 `seen >= window` 라 **무조건 `persistent`** 다 |
| 29 | @10 · **앵커** @20 · @30 세 잡이 같은 이름 → `window_jobs == 2` · `seen == 2` · `last_seen_job_id` 가 앵커 | `test_a_job_that_finished_after_this_one_is_not_in_the_window` | 앵커 조건 `finished_at < :at OR (finished_at = :at AND id <= :id)`. 없으면 「한 달 뒤에 같은 잡을 다시 열면 다른 답」이 나오고, 이미 끝난 잡의 보고서가 시간이 지나며 **조용히 바뀐다** |
| 30 | `failed(test)` · `succeeded` · `failed(이름 없음)` · `failed(test)` → `window_jobs == 4` · `unnamed == 1` · `seen == 2` | `test_window_unnamed_counts_failed_window_jobs_that_named_nothing` | 결정 68. **성공한 잡은 「이름 없이 실패」가 아니다** — 질의 ③의 `state IN ('failed','timed_out')` 를 빼면 정상 창에서도 경고가 늘 뜬다 |
| 31 | `finished_at` 이 같은 잡 둘 — 뒤 잡으로 물으면(`window=1`) 그 잡이 창이고, **앞 잡**으로 물으면(`window=20`) 창이 그 하나뿐이다 | `test_the_window_is_ordered_by_finished_at_then_id` | `ORDER BY … id DESC` 와 앵커의 `id <= :id` 두 반쪽. 초 단위로 같은 시각에 끝나는 잡은 흔하고(합류·병렬), 동점 규칙이 없으면 같은 질문에 다른 답이 나온다 |
| 32 | 이름이 창의 첫 잡과 마지막 잡에만 있다(가운데 잡은 안 찍었다) → `seen == 2` · `(first, last)` 가 그 둘 · `unnamed == 1` | `test_first_and_last_seen_name_the_oldest_and_newest_in_the_window` | 「언제부터 언제까지 빨갰나」가 사람이 자기 변경과 맞대어 볼 유일한 단서다. `MIN/MAX(job_id)` 를 안 쓰고 아무 행이나 집으면 조용히 틀린 잡을 가리킨다 |
| 33 | 창 안의 **다른** 잡이 `lint` 를 찍었다 → 이 잡의 결과에는 `test` 만 | `test_only_the_names_this_job_declared_come_back` | 질의 ②의 `name IN (이 잡의 이름)`. 이건 **이 잡의 보고서**지 창의 요약이 아니다 — 안 걸면 실패 한 건에 남의 이름 수십 개가 붙는다 |
| 34 | 종료 잡이 둘뿐인데 `window=20` → `window_jobs == 2` | `test_a_shallow_window_reports_the_jobs_it_actually_found` | **요청한 20 이 아니라 실제로 있는 2** 를 돌려줘야 `verdict(seen, window, min_jobs)` 가 `unknown` 을 낼 수 있다(§2.2 판정 1번). 20 을 그대로 돌려주면 새 프리셋의 첫 실패가 `intermittent` 로 나온다 |
| 35 | 같은 key 의 `running` 잡이 하나 있다 → 창에 안 들어간다 | `test_a_job_still_running_is_not_in_the_window` | `finished_at IS NOT NULL`. 아직 안 끝난 잡은 분모가 아니다 — 도는 잡이 분모에 끼면 오래 도는 프리셋일수록 판정이 약해진다 |
| 36 | 이름을 안 찍은 실패 잡으로 물으면 → `rows == []` · `window_jobs == 2` · `unnamed == 1` | `test_a_job_that_named_nothing_gets_no_rows_but_still_reports_the_window` | 행이 없어도 **창은 재서 돌려준다**. 그래야 화면이 「이 잡은 이름을 안 남겼고, 창의 N회 중 M회가 그렇다」를 말할 수 있다 |

## 5. `GET /jobs/{id}` — 종료한 실패 잡에만 (§2.3) · `tests/test_server_m5h.py`

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 37 | 같은 key 실패 잡 3개(마지막이 `test`·`lint`) → `failures` 가 선언 순서 · 항목 키 집합이 **정확히 여덟 개** · `(seen, window, window_unnamed) == (3, 3, 0)` · `failures_truncated is False` · `artifacts` 칸 그대로 | `test_a_failed_job_carries_failures_and_failures_truncated` | 키 집합을 정확히 잠근다 — 화면 셋(터미널·웹·`rcm run` 의 stdout JSON)이 전부 이 여덟 개만 보고 그린다. `artifacts` 를 같이 보는 이유는 「키를 더하되 값을 안 바꾼다」(스키마 v1) |
| 38 | `timed_out` 잡 둘 → `failures` 가 실린다 | `test_a_timed_out_job_carries_failures` | `_with_failures` 의 갈래가 `(FAILED, TIMED_OUT)` 이다. 시한에 걸린 잡도 판정을 냈다 |
| 39 | 마지막 잡이 `succeeded` → **`failures` 키가 없다** · `failures_truncated` 도 없다 | `test_a_succeeded_job_has_no_failures_key` | 「성공·취소·유실은 대장이 없다」. 빈 배열이 아니라 **키 없음**이어야 `failures` 가 있다는 것 자체가 「실패했고 이력을 쟀다」는 신호가 된다 |
| 40 | 마지막 잡이 `cancelled` → 키 없음 | `test_a_cancelled_job_has_no_failures_key` | #176 의 서버 쪽 짝. 취소 잡 문서에 이력이 붙으면 §1.2 에서 지운 오해가 API 로 되돌아온다 |
| 41 | 마지막 잡이 `lost` → 키 없음 | `test_a_lost_job_has_no_failures_key` | 위와 같은 이유 |
| 42 | `queued` 잡 · `uploading` 잡 → 키 없음 | `test_a_job_that_is_still_going_has_no_failures_key` | 도는 잡은 2초마다 폴링된다(결정 67). **오늘 초록** — `_with_failures` 를 `job_view` 의 잘못된 갈래(도는 잡 쪽 · `_with_artifacts` 는 양쪽에 있다)에 넣으면 그때 빨개지는 대조군 |
| 43 | `failed_step="test"` · `last_step="build web"` · 이름 `["test","build web","port_test.dart"]` → `step` 이 `True, True, False` | `test_step_is_true_only_for_names_that_match_failed_step_or_last_step` | 종료 잡에는 스텝 목록이 없다(§13-1). 그래서 **그 두 칸으로만** 「스텝」과 「단위」를 가른다 — 마커를 다시 읽으면 질의가 는다 |
| 44 | 이름을 `zeta`·`alpha`·`mid` 순으로 찍었다 → 문서도 그 순서 | `test_failures_keeps_the_order_the_job_declared` | 대장의 `seq` 가 문서까지 살아 오는지(#21 의 서버 쪽 짝). 스크립트가 정한 순서가 곧 읽는 순서다 |
| 45 | `fail_truncated=True` 로 끝난 잡 → `failures_truncated is True` | `test_failures_truncated_reports_the_job_row_flag` | 분자가 잘렸다는 사실을 숨기지 않는다. 이 값이 늘 `false` 면 100개를 넘긴 잡의 이력이 **조용히 과소**로 읽힌다. 안쪽 `fail_truncated` 와 공개 `failures_truncated` 를 잇는 유일한 자리 |
| 46 | `failure_window_jobs = 2` 인 서버 + 같은 key 실패 잡 5개 → `(seen, window) == (2, 2)` | `test_the_window_comes_from_failure_window_jobs` | 창은 설정이 정한다(§2.4). 상수를 박아 두면 운영자가 값을 바꿔도 아무 일도 안 일어난다 |
| 47 | `failure_min_jobs = 3` · 종료 잡 둘 → `verdict == "unknown"` · 숫자(`seen`·`window`)는 그대로 실린다 | `test_a_window_shallower_than_failure_min_jobs_is_unknown` | 「표본이 없으면 아무 말도 안 한다」. 숫자를 같이 싣는 것도 계약이다 — 화면이 `2 of 2 runs so far` 를 그린다(§2.5) |
| 48 | 성공 7 + 실패 1(`port_test.dart`) → `(seen, window, window_unnamed) == (1, 8, 0)` · `verdict == "first_seen"` | `test_a_name_seen_once_in_a_deep_window_is_first_seen` | §2.2 판정표의 4번 줄. ⚠️ §7 의 완료 기준과 워크플랜 §5 의 예시는 이 경우를 `intermittent?` 로 적었다 — 아래 「명세가 답을 안 주는 것」 4 |
| 49 | 같은 이름을 찍은 실패 잡 4개, 창 4 → `verdict == "persistent"` | `test_a_name_in_every_window_job_is_persistent` | `seen >= window`. 「계속 빨갛다」와 「간헐」을 섞으면 사람이 물음표를 안 믿는다 |
| 50 | 성공·실패가 번갈아 4개(그 중 둘이 같은 이름) → `(seen, window) == (2, 4)` · `verdict == "intermittent"` | `test_a_name_seen_some_of_the_time_is_intermittent` | `1 < seen < window` 의 가운데 칸. M5h 가 답하려는 신고 5번 그 자체다 |
| 51 | 이름 없는 실패 2 + 이름 있는 실패 1 → `(seen, window, window_unnamed) == (1, 3, 2)` | `test_window_unnamed_reaches_the_document` | 결정 68 이 문서까지 온다. 옛 스크립트가 섞여 있으면 분자가 과소인데 그걸 숨기지 않는다 |
| 52 | 이름을 안 찍고 실패한 잡 → `failures == []`(빈 배열) · `failures_truncated is False` | `test_a_failed_job_that_named_nothing_still_carries_an_empty_list` | **빈 배열은 「없다」는 뜻**이고 키 없음은 「못 쟀다」는 뜻이다(§2.3). 이 둘을 안 가르면 #53 의 fail-open 규칙이 관측 불가능해진다 |
| 53 | `store.failure_stats` 가 `sqlite3.OperationalError` → 200 · `state`·`id`·`last_step` 그대로 · **`failures` 키가 없다** | `test_a_store_error_drops_the_failures_key_instead_of_the_job` | 조회 실패가 잡 문서를 죽이면, 대기 클라이언트가 마지막에 정확히 한 번 부르는 그 자리에서 **결과 자체를 못 받는다**. 그렇다고 `[]` 로 물러서면 「깨끗했다」는 거짓말이다 |
| 54 | 종료 잡 하나(`last_step` 있음) → `/api/status` 의 최근 행에 `failures`·`failures_truncated` 가 **없고** `last_step` 은 **있다** · `schema_version == 1` | `test_api_status_recent_rows_have_last_step_but_never_failures` | 결정 67 의 관측 가능한 형태. `recent_json` 은 `/api/status` 와 `GET /jobs/{id}` 가 **같이 쓰는 함수**라, 거기에 `failures` 를 넣으면 가장 뜨거운 요청이 같이 커진다 |
| 55 | `store.failure_stats` 를 「부르면 터지는 것」으로 바꾸고 `/api/status` → 200 | `test_api_status_never_asks_for_the_failure_history` | 키 유무보다 강하다 — **부르지도 않는지**를 본다. §7 의 「`/api/status` 의 질의 수 불변」을 결정적으로 잴 수 있는 유일한 형태 |

## 6. 설정 두 키 (§2.4) · `tests/test_server_m5h.py`

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 56 | `ServerSection()` → `failure_window_jobs == 20` · `failure_min_jobs == 3` | `test_the_two_failure_keys_have_their_documented_defaults` | 기본값이 곧 문서다(결정 66). 여기가 다르면 `examples/server.toml` 의 주석이 거짓이 된다 |
| 57 | 파일에 두 키를 쓴다 → 그대로 실린다 | `test_the_two_failure_keys_load_from_a_file` | 진짜 설정 키인지(= `unknown key` 로 거절당하지 않는지). 오늘은 여기서 `[server] unknown key 'failure_min_jobs'` 가 난다 |
| 58 | `failure_window_jobs` 가 0 · −1 · 501 → `ConfigError("[server] failure_window_jobs must be …")` | `test_a_window_outside_one_to_five_hundred_is_rejected` | 0 이면 창이 없고(모든 판정이 `unknown`), 500 을 넘으면 실패한 잡 하나가 대장을 크게 훑는다. 조용히 고쳐 주지 않고 **거절**하는 것이 이 도구의 관례다 |
| 59 | 1 과 500 → 통과 | `test_the_edges_of_the_window_are_accepted` | 경계는 **포함**이다(「1 이상 500 이하」). 하나 어긋나면 문서와 코드가 다른 말을 한다 |
| 60 | `failure_min_jobs = 0` → `ConfigError("[server] failure_min_jobs must be …")` | `test_a_min_jobs_below_one_is_rejected` | 0 이면 창이 비어도 판정을 내린다 — 표본 0에 `persistent` 가 붙는다 |
| 61 | `window=5, min=6` → 거절 · `window=5, min=5` → 통과 | `test_a_min_jobs_larger_than_the_window_is_rejected` | `min > window` 면 판정이 **영원히 `unknown`** 이다. 기능이 조용히 꺼지는 대신 설정을 고치라고 말한다 |

## 7. 404 가 길을 알려 준다 (§3) · `tests/test_server_m5h.py`

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 62 | `/api/jobs/162` · `/logs/162` · `/job/162/log` (토큰 없이) → 404 · `error == "not found"` · `hint` 가 **잠근 문구 그대로** | `test_a_path_with_a_job_number_names_the_real_route` | 신고 2번. `/api/status`·`/api/health` 가 `/api` 아래인데 잡은 `/jobs` 아래라 `/api/jobs/…` 는 **자연스러운 오추측**이고, 오늘은 그 오추측에 아무 말도 안 한다(워크플랜 §2.4) |
| 63 | `/nope` → `hint` 가 라우트 목록 문구 | `test_a_path_without_a_number_lists_the_routes` | 숫자가 없으면 그 사람은 API 를 처음 만지는 중이다. 목록 한 줄이 문서를 대신한다 |
| 64 | `/api/jobs/9001` → `hint` 에 `job #9001 is GET /jobs/9001` · `162` 가 안 들어간다 | `test_the_hint_names_the_number_in_the_path` | 번호가 **경로에서** 와야 한다. 예시를 그대로 박아 두면 모든 오추측이 162 번 잡을 가리킨다 |
| 65 | 진짜 잡의 `GET /jobs/{id}` → 200 · `hint` 없음 · `/api/status` 200 · `GET /jobs/999999` → 404 `no such job` | `test_a_real_route_is_untouched` | **별칭은 만들지 않는다**(결정 69) — 404 가 가르칠 뿐 라우트 표는 안 는다. **오늘 이미 초록**(고치다 깨뜨리는 것을 막는 회귀) |

---

## 명세가 열어 둔 곳을 시나리오가 정한 값 (구현이 따를 것)

1. **`seq` 의 시작값은 안 정한다.** `ledger()` 는 `ORDER BY seq` 의 **순서**와 단조 증가만
   본다(#21). 0 부터든 1 부터든, 중복이 무시되며 번호가 비든 초록이다.
2. **`_SCHEMA_V1` 에 v12 의 표·인덱스·열도 들어간다.** §1.3 은 `last_step` 에 대해서만
   「`_SCHEMA_V1` 에도 더한다」고 적었지만, `migrate()` 는 새 DB 에 `_SCHEMA_V1` **만**
   실행하므로 v12 도 같이 들어가야 새 설치가 대장을 갖는다(#10 · #16).
3. **거절된 `finish` 의 실패는 예외로 올라온다.** #19 는 `sqlite3.Error` 또는 `StoreError`
   둘 다 받는다 — 어느 쪽으로 감싸도 초록이고, **잡이 롤백되는지**만 계약이다.
4. **앵커는 `Job.finished_at` 에서 읽는다**(#29 · #31). §2.1 의 질의는 `:at`·`:id` 를
   말하지만 어디서 오는지 안 적었다 — `_with_failures` 가 `job.finished_at` 을 넘기는 것과
   같은 값을 테스트도 쓴다. 종료 잡이라 `finished_at` 은 언제나 있다.
5. **`GET /jobs/<없는 id>` 에는 `hint` 를 안 요구한다**(#65). §3 이 바꾸는 것은 `_route` 의
   마지막 `raise` 하나뿐이라, 그 404 는 오늘 문구(`no such job`)만 단언한다.
6. **설정 오류 문구는 머리만 단언한다** — `[server] <키> must be` 까지. §2.4 의 「… 꼴」이
   뒷부분을 안 정했다.

## 명세가 답을 안 주는 것 — 오너가 정해야 할 것

1. **`timed_out` 이 갖는 칸이 두 문서에서 다르다.** 구현 계획 §1.2 는
   「`STEP_STATES = (FAILED, TIMED_OUT)` … `fail_names = progress.fail_names if state in
   STEP_STATES else ()`」라 시한 잡이 **세 칸을 전부** 갖는데, 워크플랜 §4.1 은
   「`timed_out` 은 `last_step` 만 싣는다」, 결정 64 도 「`timed_out` 은 `last_step` 만.」이다.
   구현 계획을 따랐다(#6). 뒤집으면 **#6 하나가 반대로 바뀐다**.
2. **강제 종료 잡의 `fail_truncated`.** §1.2 의 코드 조각은 `failed_step`·`last_step`·
   `fail_names` 셋만 `STEP_STATES` 로 거르고 `fail_truncated` 는 말하지 않는다. 취소·유실 잡의
   `Outcome.fail_truncated` 가 `False` 로 리셋되는지 마커에서 온 값 그대로인지 알 수 없어
   **그 경우는 안 잠갔다**(#9 는 `failed` 잡만 본다).
3. **`seq` 의 시작값과 중복의 번호.** §2.1 은 `INSERT OR IGNORE INTO job_failures(job_id,
   name, seq)` 라고만 적는다. 0 기반인가 1 기반인가, 무시된 중복이 번호를 소비하는가.
   표시가 순서만 쓰므로 지금은 무해하지만, 나중에 「몇 번째로 찍혔나」를 보여 주려면 정해야
   한다.
4. **`seen == 1` 인 깊은 창의 판정이 세 곳에서 다르다.** §2.2 판정표 4번은
   「`seen == 1` → `first_seen`」인데 §7 의 2단계 완료 기준은 「같은 key 8회 중 1회 실패한
   이름이 `1 of the last 8 gate runs · intermittent?` 로 나온다」고 적었고, 워크플랜 §5 의
   CLI 예시도 같은 이름을 `· intermittent?` 로 그린다 — 그런데 워크플랜 §4.3 의 JSON 예시는
   같은 이름에 `"verdict": "first_seen"` 을 준다. 판정표를 따랐다(#48). **완료 기준 문장을
   고치는 것이 맞아 보인다** — 아니면 `first_seen` 칸이 영영 안 나온다.
5. **404 `hint` 의 문구가 두 문서에서 다르다.** 워크플랜 §4.4 는
   `… with a Bearer token (rcm logs 162)`, 구현 계획 §3 은
   `… with that job's token (try: rcm logs 162)`. 구현 계획을 따랐다(#62).
6. **조회 실패에서 `failures_truncated` 는 어떻게 되나.** §2.3 은 「`sqlite3.Error` 면
   `failures` 키를 **아예 안 싣는다**」고만 말한다. 코드 조각의 순서대로면 둘 다 안 실리는데
   문면은 하나만 말한다 — #53 은 `failures` 만 단언했다.
7. **`_with_failures` 가 설정을 어디서 읽나.** §2.3 의 조각은 `cfg.failure_window_jobs` ·
   `cfg.failure_min_jobs` 라고만 쓴다(`self.config.server` 겠지만 문면에 `cfg` 의 정의가
   없다). 서버 테스트는 `ServerSection` 의 칸을 바꿔 넣는 것으로만 잠갔다(#46 · #47).

> 이미 고쳐진 것: 워크플랜 §4.3 의 「DB v11」(→ v12) · 창이 흘러가는 문제(→ 앵커 조건과
> `failure_stats` 의 세 번째 인자) · 판정표의 `seen <= 0` 방어 줄. 이 문서는 그 뒤의
> 계약을 따른다.
