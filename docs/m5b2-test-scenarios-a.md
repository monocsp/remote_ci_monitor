# M5b-2 테스트 시나리오 A — 저장소 v5 · 토큰 종류 · 워커 표 · `claim(worker_name=)` (2026-09-06)

`docs/m5b2-workplan.md` §1(토큰 종류) · §2(워커 등록) · §3 「서버 재시작」·「워커 재시작」 · §4(lost 판정) ·
§6 「모델」·「저장소」 이름 고정을 `tests/test_store_m5b2.py` 로 옮긴 것이다(test-first, 역할 A).
`src/` · 기존 테스트는 건드리지 않았다. HTTP 라우트(`/worker/*`) · janitor · 설정 · CLI · 화면은 B·C 의 몫이다.

공통: `Store(tmp_path / "rcm.db")` · 시각은 `jobfactory.NOW`(2026-09-04 00:52:12Z) 기준 `at(seconds)` ·
스레드·sleep 없음. 도우미 `enqueue(store, ..., pool=)` · `register(store, name, pool=, lanes=, now=)` ·
`remote_running(store, worker, lane=, tree=, t=)`(그 풀에 잡을 넣고 그 워커 레인으로 claim — 그 풀에
다른 queued 잡이 없을 때만) · `columns(path, table)`(`PRAGMA table_info`) · `token_rows(path)`.

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 모델 이름 — `TOKEN_KINDS == ("client","admin","worker")` · `Job.worker_name` 기본 None · `WorkerInfo.worker` 기본 None · `TokenInfo.kind` 기본 `"client"` | `test_model_fixes_token_kinds_and_worker_name_defaults` | §6 L60 · L61 |
| 2 | 새 DB 는 v5 — `tokens.kind TEXT NOT NULL DEFAULT 'client'`(admin 열 유지) · `jobs.worker_name TEXT` NULL 허용 · `workers` 표의 열 집합·PK·NOT NULL·타입 | `test_fresh_db_is_schema_v5_with_token_kind_worker_name_and_workers_table` | §1 L9 · §2 L16-17 |
| 3 | 4→5 마이그레이션 — v5 DB 에서 `kind`·`worker_name`(과 인덱스)·`workers` 를 떼고 `user_version=4` → 다시 열면 5 · `admin=1` → kind admin, 나머지 client · 옛 running/queued 행은 `worker_name None` · `list_workers()==[]` · `register_worker`/`claim(worker_name=)` SQL 이 돈다 · 세 번째 열기는 변화 없음 | `test_migration_v4_to_v5_fills_kind_from_admin_and_adds_worker_name_and_workers` | §1 L9 · §2 L16 |
| 4 | `add_token(name, now=)` — `admin` 생략 = False → client · `admin=True` → admin · `verify_token`/`list_tokens` 의 `kind` · DB 행 `(kind, admin)` | `test_add_token_derives_kind_from_admin_when_kind_is_omitted` | §6 L61 |
| 5 | `kind="worker"` → admin False · `kind="admin"` → admin 열 1 · `kind="client"` · `admin is (kind == "admin")` 항상 · 폐기해도 kind 는 보인다 · 이름 중복은 기존 규칙 | `test_add_token_kind_worker_is_never_admin_and_kind_admin_sets_the_flag` | §1 L9-10 · §6 L61 |
| 6 | 모르는 kind(`"root"`·`""`·`"clients"`) → `ValueError` · 행이 안 남아 같은 이름을 바로 다시 쓸 수 있다 | `test_add_token_rejects_unknown_kind_without_inserting` | §1 L9 · §6 L60 |
| 7 | `register_worker` → `WorkerRow` 전 필드 · `registered_at == last_seen_at == now`(aware) · `get_worker` 같은 행 · 모르는 이름 None · host_name/version None 허용 · `lanes` 는 int | `test_register_worker_stores_the_row_and_stamps_registered_and_last_seen` | §2 L20 · §6 L61 |
| 8 | 재등록(upsert) — 풀·lanes·host_name·version 이 새 값 · `last_seen_at = now` · 행은 하나 · 먼저 받은 행은 불변 | `test_register_worker_again_updates_pool_lanes_version_and_last_seen` | §2 L20 · §6 L61 |
| 9 | `list_workers()` 는 이름순(등록 순서·touch 와 무관) · `get_worker("nope") is None` | `test_list_workers_is_sorted_by_name_not_by_registration_order` | §6 L61 |
| 10 | `touch_worker(name, now)` — `last_seen_at` 만 바뀐다(registered_at·풀·레인 그대로) · 모르는 워커는 False 이고 행을 만들지 않는다 | `test_touch_worker_updates_last_seen_only_and_is_false_for_unknown` | §2 L22 · §6 L61 |
| 11 | `claim(lane, now, pool=, worker_name=)` 이 `Job.worker_name`·`lane`·`started_at`·`phase` 를 남긴다 · 로컬 claim(생략·명시적 None)은 NULL · `list_active` 에 실린다 · 종료 뒤에도 `worker_name` 은 남고 `lane` 은 None | `test_claim_with_worker_name_records_the_worker_and_lane` | §3 L28 · §6 L60-61 |
| 12 | 워커는 자기 풀의 queued 잡만 — 잡 없는 풀은 None · 리눅스 워커는 기본 풀 잡을 안 잡는다 · 원격 `pool=default` 워커는 기본 풀 잡을 잡고 그러면 로컬 레인엔 남는 게 없다 | `test_claim_with_worker_name_takes_only_jobs_of_the_workers_pool` | §3 L28 · §4 L44 |
| 13 | 원격 claim 도 `(-priority, id)` 순서 · 같은 풀의 `devices` 는 다른 워커가 돌려도 막는다 · 기본 풀의 `devices` 는 리눅스와 무관 · 끝나면 막혔던 잡이 잡힌다 | `test_claim_with_worker_name_keeps_priority_order_and_group_exclusion_in_the_pool` | §3 L28 · m5-workplan 「프로토콜」 claim |
| 14 | `LaneBusy` — 그 `(worker, lane)` 에 running 잡이 있으면 예외 · queued 잡은 그대로 · 같은 워커 다른 레인 OK · 다른 워커 같은 레인 번호 OK · cancelling 도 활성 · 종료되면 그 레인으로 다시 · **큐가 비어도** 레인이 바쁘면 None 이 아니라 LaneBusy | `test_claim_raises_lane_busy_while_that_worker_lane_has_an_active_job` | §3 L28 · §6 L61 · 리뷰 must-fix 3 |
| 15 | 레인 배타는 워커 단위 — 원격 `build-01/1` 이 바빠도 로컬 레인 1 은 잡고, 로컬 레인 1 이 바빠도 원격 레인 1 은 잡는다 | `test_lane_busy_is_per_worker_so_local_and_remote_lane_numbers_never_collide` | §6 L61 |
| 16 | `jobs_of_worker(name)` — 그 워커의 running·cancelling 만(id 순) · 종료·queued·다른 워커·로컬 제외 · 온전한 `Job`(cancel.kill_at 포함) · 모르는 워커 [] | `test_jobs_of_worker_lists_running_and_cancelling_jobs_of_that_worker_only` | §6 L61 · §3 L33 |
| 17 | `mark_lost_for_worker(name, now, summary)` → id 목록(id 순) · 그 잡들은 lost + summary + `finished_at` + `lane/phase None` + 전이 이벤트 LOST · `worker_name` 은 남는다 · 다른 워커·로컬·queued 는 그대로 · 두 번째 호출·모르는 워커는 [] · 그 뒤 그 워커가 같은 레인으로 다시 claim 한다 | `test_mark_lost_for_worker_closes_only_that_workers_active_jobs` | §4 L42 · §3 L38 · §6 L61 |
| 18 | `recover_on_start` 는 `worker_name IS NULL` 인 running·cancelling 만 lost · 원격 running/cancelling 잡과 `workers.last_seen_at` 은 한 글자도 안 바뀐다 · 두 번째 호출은 `([], [])` · 그 뒤 `mark_lost_for_worker` 가 원격 잡을 닫는다 | `test_recover_on_start_marks_only_local_jobs_lost_and_keeps_remote_ones` | §3 L37 · §6 L61 · 리뷰 must-fix 1 |
| 19 | 재시작 시 uploading 은 풀과 무관하게 cancelled(`server restarted during upload` · `cancelled_by server`) · queued 는 어느 풀이든 남는다 · 원격 running 은 그대로 · 로컬 레인은 바로 다시 돈다 | `test_recover_on_start_still_cancels_uploads_and_keeps_queued_jobs_in_every_pool` | §3 L37 · 기존 규칙(store 모듈 docstring) |

19 건. `ruff check` · `ruff format --check` 깨끗(line-length 100, CJK 2폭).

## 가정 (명세가 안 정한 것 — 구현이 달리 정하면 테스트를 고쳐야 한다)

1. **`LaneBusy` 는 큐가 비어 있어도 난다** (14). §3 표는 「레인에 활성 잡이 있으면 409」를 「queued 잡을 잡는다」
   보다 먼저 적었고 §6 은 조건 없이 「활성 잡이 있으면 예외」라 했으므로, 레인 검사를 후보 조회보다 먼저 하는 것으로 잠갔다.
2. **`jobs_of_worker` · `mark_lost_for_worker` 의 순서는 id 오름차순** (16 · 17). 명세는 목록이라고만 했다.
   `recover_on_start` 가 `ORDER BY id` 인 것과 맞췄다.
3. **`worker_name` 은 종료 뒤에도 남는다** (11 · 17). 명세는 지우라고 하지 않았고, 누가 돌렸는지는 기록 가치가 있다.
4. **`mark_lost_for_worker` 는 `lane`·`phase` 를 None 으로** (17) — `finish`·`recover_on_start` 와 같은 모양.
   `transitions[-1] == Transition(LOST, now)` 도 잠갔다(상태 전이는 같은 트랜잭션의 이벤트로 남긴다는 기존 규칙).
5. **재등록 때 `registered_at`** 은 잠그지 않았다 (8). §3 「워커 재시작」은 register = 새 프로세스라 하므로 now 로
   갱신하는 것도 말이 되고, §6 은 「upsert; `last_seen_at = now`」만 적었다. `server.workers[].since`(idle 이면
   `registered_at`) 에 영향이 있으니 B 의 서버 테스트나 구현자가 정해야 한다.
6. **`add_token(admin=True, kind="worker")` 같은 충돌**은 잠그지 않았다. §6 은 「kind 가 있으면 그것」이라 kind 가
   이기지만, CLI 가 `--admin --worker` 를 먼저 막으므로 저장소가 볼 일이 없다.
7. **저장소 `claim` 은 워커 등록을 검사하지 않는다**고 봤다(명세에 없음). 테스트는 그래도 늘 `register_worker` 를
   먼저 불러 두어 검사를 넣어도 초록이다. 단 시나리오 12 의 `claim(pool="mac2", worker_name="build-09")` 는
   등록 안 된 워커라 등록 검사를 넣으면 예외가 된다 — 그때는 그 한 줄을 고친다.
8. **`LaneBusy` 의 부모 클래스·메시지**는 잠그지 않았다(`Exception` 인 것만). 서버 문구 `lane N already has job #M`
   은 B 의 HTTP 테스트 몫이다.

## 기존 테스트 중 DB_VERSION 을 고정한 것

`tests/test_store.py` · `tests/test_store_m5.py` · `tests/test_store_m5b.py` 가 `DB_VERSION == 4` / `user_version() == 4`
를 잠그고 있었고, 옛 버전을 흉내 내는 마이그레이션 테스트는 v5 가 더한 `tokens.kind` · `jobs.worker_name`(과
`jobs_worker` 인덱스) · `workers` 도 떼어야 진짜 v1/v2/v3 이 된다(안 떼면 5 번 마이그레이션의 `ADD COLUMN` 이
duplicate column 으로 죽는다). 이 문서를 쓰는 시점에 세 파일 모두 이미 5 로 고쳐져 있었다(구현이 같은
워크트리에서 병렬로 들어왔다) — 네 파일을 같이 돌려 전부 초록인 것을 확인했다.
