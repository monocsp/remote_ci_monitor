# M5f 테스트 시나리오 B — 큐·ETA·보류 레인과 그 화면 (2026-09-09)

`docs/m5f-workplan.md` §5.1(워커 상태 `held` · `hold_code` · `hold_detail` · `held_since`) · §5.2(`held_by_load`
와 사유 순서) · §5.3(`(worker, lane)` 레인 키 고침과 ETA 그리디에서 보류 레인 제외) · §5.4(`rcm top` ·
`rcm check` · SSE · 웹 두 언어) · §6(`Estimate.shared` 와 신뢰도 세 곳)을 `tests/test_queue.py` 추가 ·
`tests/test_status_schema.py`(+`test_server.py`·`test_worker_api.py`) 수정 · 새 `tests/test_render_m5f.py` ·
새 `tests/web/admission.test.js` 로 옮긴 것이다(test-first, 역할 B). **판정 자체(`core/admission.py`)와 설정
키·서버 배선은 A 의 몫이고, PR 2a-0 의 병목(마커 배치 · `ANALYZE` · 503 · 지터)은 C 의 몫이다** — 여기서는
「이미 판정이 끝나 `WorkerInfo.state == "held"` 로 들어온 뒤」부터만 본다. `src/` 와 기존 테스트는 안 건드렸다.
아래 「오늘 값」은 전부 이 워크트리의 `origin/dev` 코드에 프로브를 돌려 **실제로 찍어 본 값**이다(§14-B1·B2 ·
§14-B7 재확인).

## 공통

- 순수 계층은 `tests/jobfactory.py` 를 그대로 쓴다 — `NOW`(2026-09-04T00:52:12Z) · `CFG = QueueConfig()`
  (`not_scheduled_seconds = 10` · `floor_remaining_seconds = 30`) · `PRESETS`(`gate` 480 · `deploy-dev` 900 ·
  `qa` 540, 그룹 `devices`) · `MEDIANS`(`gate:full` **400초 n=7** · `deploy-dev` 600초 n=3) · `ago()` · `job()`.
  `job()` 은 `**kw` 를 `Job` 에 그대로 넘기므로 **`worker_name="build-02"` 를 그냥 줄 수 있다**.
- `jobfactory.workers()`·`default_workers()` 는 **로컬 레인만** 만든다. 원격 레인이 필요하므로
  `tests/test_queue.py` 안에 도우미를 하나 더 둔다(기존 함수는 안 건드린다):

  ```python
  def lane(n, state=WORKER_IDLE, *, worker=None, job_id=None, since=None, hold_code=None):
      return WorkerInfo(
          lane=n,
          state=state,
          job_id=job_id,
          worker=worker,
          since=since or ago(minutes=30),
          hold_code=hold_code,
      )


  POOL4 = [lane(1), lane(2), lane(1, worker="build-02"), lane(2, worker="build-02")]  # 4레인
  ```

  `rows_for(..., wk=POOL4)` 로 넣는다(`rows_for` 에 이미 `wk=` 가 있다).
- **잡의 나이가 사유를 가른다.** `not_scheduled` 는 `max(idle_since, queued_at)` 이 10초를 넘을 때 뜬다.
  그래서 `held_by_load` 를 보려는 시나리오는 잡을 **방금 queued**(`job(401)` — `created_min` 기본 0)로 만들고,
  `not_scheduled` 를 보려는 시나리오만 `created_min=5` 로 만든다. 섞으면 사유가 바뀐다.
- 터미널 렌더는 `test_render_m5b2.local_doc()` · `local_entry(lane, state)` · `remote(name, lane, state)` ·
  `with_workers()` · `head_line()` 를 쓴다. `local_entry`·`remote` 는 **`hold_code`·`hold_detail`·`held_since`
  를 받는 선택 인자가 필요하다**(기본 `None`) — 이 둘은 dict 를 만드는 도우미라 키를 더해도 기존 단언이
  안 깨진다(확인함).
- `rcm check` 는 `test_cli_m5b4.py` 방식 — `main(["check"])` 를 in-process 로 부르고 `RCM_SERVER`/`RCM_TOKEN`
  으로 `test_worker_api.WorkerServer`(진짜 `/worker/*` + 주입 시계)를 가리킨다. 행 형식은
  `{'ok '|'warn'|'FAIL'}  {name:<13} {detail}`.
- 웹은 `tests/web/helpers.js` 의 `load()`·`fixture()`·`NOW`. 순수 함수만 본다(DOM 은 여기 없다).

## 잠근 문자열 (구현이 그대로 내야 한다)

- `rcm top` 머리줄: `lanes 1/2 busy · 1 held (cpu 92%)` · `lanes 0/2 busy · 1 held (cpu 92%) · DOWN: lane 1` ·
  원격 필 `build-02/2 held`(오늘 이미 나온다).
- `rcm top` 큐 행 사유: `held by load · cpu 92%` · 상세가 없으면 `held by load`.
- `rcm check` pools 행: `default (2 lanes · 1 held) · linux (build-02/1 idle · build-02/2 held)`.
- 불변(오늘과 바이트 단위로 같아야 한다): `lanes 1/2 busy`(보류 레인이 하나도 없을 때) ·
  `worker busy #412` / `worker idle`(`lanes == 1`) · `default (2 lanes)`(보류 레인 없음) ·
  `test_render_m5b.GOLDEN`.

---

## 1. `tests/test_queue.py` — 레인 키 (§5.3) · **여섯 건 전부 오늘 빨강**

기본 풀에는 로컬 레인 1..N 과 **모든 원격 `default` 워커의 레인 1..M** 이 함께 들어온다(`server.py:348-352`,
`rcm worker` 의 `pool` 기본값이 `default` — `config.py:209`). 오늘 `lane_free`·`lane_last_job`·`idle_since` 는
레인 **번호**로 키를 잡아 로컬 레인 2 와 `build-02/2` 를 한 칸으로 뭉갠다.

| # | 시나리오 | 테스트 함수 | 오늘 → 고침 뒤 | 왜 중요한가 |
|---|---|---|---|---|
| 1 | 로컬 1·2 + `build-02/1`·`build-02/2` = **4 idle 레인**. `gate:full`(400초) 대기 잡 4개(`created_min=5`) | `test_four_idle_lanes_across_two_workers_start_four_jobs_at_once` | `wait_seconds` **`[0, 0, 400, 400]`** → `[0, 0, 0, 0]`. 사유도 `['not_scheduled','not_scheduled','waiting_for_lane','waiting_for_lane']` → 넷 다 `not_scheduled` | 4레인 풀이 **2레인 풀과 바이트 단위로 같은 결과**를 낸다 — 오늘 용량을 절반으로 세고 있다는 증거(§14-B1) |
| 2 | 로컬 레인 1 이 `#500`(`worker_name=None`, `lane=1`, `started_min=1`) 으로 busy · `build-02/1` idle · 대기 잡 `#401` | `test_a_busy_local_job_frees_only_the_local_lane` | `wait 340.0 · ahead_job_id 500` → `wait 0.0 · ahead_job_id None` | 잡 귀속이 `job.lane` 만 보면 남의 워커 레인을 자기가 채운 것으로 센다 |
| 3 | 거꾸로 — `build-02/1` 이 `#500`(`worker_name="build-02"`, `lane=1`)으로 busy · 로컬 레인 1 idle · 대기 잡 `#401` | `test_a_busy_remote_job_frees_only_that_workers_lane` | `wait 340.0 · ahead 500` → `wait 0.0 · ahead None` | 귀속 키는 `(job.worker_name, job.lane)` 다 — 한 방향만 고치면 반대가 남는다 |
| 4 | `#500` 원격(`build-02/1`, `started_min=5` → 잔여 100초) · `#501` 로컬(`lane 1`, `started_min=1` → 잔여 340초) · 대기 `#401` | `test_ahead_job_id_names_the_job_on_the_lane_that_frees_first` | `wait 340.0 · ahead **501**` → `wait 100.0 · ahead **500**` | `lane_last_job` 도 같은 키를 쓴다 — 「누구 뒤에 서 있나」가 오늘 틀린 잡을 가리킨다 |
| 5 | 진짜로 노는 레인 3개(로컬 1·2 + `build-02/1`, 전부 `since=ago(30분)`) · 대기 잡 3개(`created_min=5`) | `test_idle_since_is_kept_per_worker_lane` | 사유 `['not_scheduled','not_scheduled','waiting_for_lane']` → 셋 다 `not_scheduled` | `idle_since` 도 **같이 뭉개진다**(idle 3 → 키 2). 세 번째 잡이 30분 노는 레인을 앞에 두고도 「정상 대기」라고 말한다(§14-B2) |
| 6 | 같은 4레인 풀 · 대기 잡 4개(`created_min=5`)를 **전부 idle** 일 때와 **`build-02/2` 만 held** 일 때 두 번 돌린다 | `test_removing_one_lane_from_a_four_lane_pool_changes_the_eta` | 오늘 두 경우가 **완전히 동일**(`[0,0,400,400]`) → `[0,0,0,0]` vs `[0,0,0,400]`(마지막 잡 사유 `held_by_load`) | §5.2 의 「보류 레인을 뺀다」가 **키를 고치기 전엔 아무 효과가 없다**는 것. 결정 43 의 전제 그 자체 |

**주의** — 1·5·6 은 idle 레인이 30분째 놀아서 사유가 `not_scheduled` 로 나온다. 이건 오늘의 규칙 그대로이고
잡을 `created_min=0` 으로 만들면 `waiting_for_lane` 이 된다. 시나리오는 **`wait_seconds` 를 주 단언으로,
사유는 부 단언으로** 쓴다.

## 2. `tests/test_queue.py` — `held` · `held_by_load` (§5.1 · §5.2)

`open = [w for w in live if w.state != WORKER_HELD]`. `live`(= `down` 아님)는 `worker_down` 판정에 그대로,
ETA 그리디와 `can_start` 는 `open` 으로.

| # | 시나리오 | 테스트 함수 | 정확한 기대값 | 왜 중요한가 |
|---|---|---|---|---|
| 7 | 레인 둘이 **전부 held** · 대기 잡 `#401` | `test_every_lane_held_is_not_worker_down` | 사유 `held_by_load` · `wait_seconds is None` · `finish_at is None`. 오늘은 `waiting_for_lane` · `wait 0.0` · finish 있음 | held 는 `state != WORKER_DOWN` 을 **통과한다**(살아 있다). 그래서 `worker_down` 이 아니고, 그렇다고 시작할 수도 없다 |
| 8 | 레인 1 busy(`#500`, 잔여 340) · 레인 2 held · 대기 잡 `#401` | `test_a_held_lane_is_left_out_of_the_greedy` | `wait 340.0`(레인 1 이 빌 때). 오늘은 `wait 0.0` — held 레인에 바로 넣는다 | 「열릴 것처럼 계산하면 PLAN 이 금지한 **자신있는 틀린 시각**」(결정 43). 늦게 잡고 열리면 앞당긴다 |
| 9 | 사유 우선순위 — 한 분기에 한 건씩, 전부 held 레인을 하나 둔 채로 (파라미터 7건) | `test_reason_precedence_places_held_by_load_between_not_scheduled_and_waiting_for_lane` | ① `uploading` 잡 → `uploading` ② upload 가 `upload_stall_seconds` 넘게 멈춤 → `upload_stalled` ③ `paused=True` → `paused`(wait·finish None) ④ 레인이 **전부 down** → `worker_down`(wait·finish None) ⑤ 같은 `devices` 그룹의 busy 잡이 있음 → `blocked_by_group`(wait 480.0) ⑥ 30분 논 idle 레인 + 5분 전 잡 → `not_scheduled` ⑦ idle 없음 + held 있음 → `held_by_load` | 새 사유를 **정확히 한 칸**에 끼운다. 위 여섯 중 하나라도 밀리면 오늘의 알람(`worker_down`·`not_scheduled`)이 죽는다 |
| 10 | 레인 1 busy(`#500`) · 레인 2 held · 대기 잡 **둘**(`#401`·`#402`, 방금 queued) | `test_one_waiting_job_consumes_one_held_lane` | `['held_by_load', 'waiting_for_lane']` · wait `[340.0, 740.0]` · ahead `[500, 401]` | 「너를 집었을 레인이 막혀 있다」는 **한 잡에만** 참이다. 안 그러면 큐 전체가 부하 탓이라고 말한다(오늘의 `idle_since` 소비와 같은 방식) |
| 11 | **레인 1 `down` + 레인 2 `held`** · 대기 잡 `#401` | `test_lane_one_down_and_lane_two_held_is_held_by_load_with_a_null_eta` | 사유 `held_by_load` · `wait_seconds is None` · `finish_at is None` · `estimate.expected_seconds` 는 그대로 400.0 · `server.workers[]` 에 **down 레인이 그대로 보인다**(머리줄 `DOWN: lane 1` 도) | `live` 는 안 비었는데 `open` 이 빈 **새 경우**(리뷰 D). 사유가 진짜 원인(죽은 레인)을 가리지 않는다는 걸 **의도된 동작으로 못 박는다** |
| 12 | 레인 1 busy · 레인 2 held(`since=ago(30분)`) · 잡 `#401`(`created_min=5`) | `test_a_held_lane_never_triggers_not_scheduled` | 사유 `held_by_load` — **`not_scheduled` 아님** | held 레인은 `idle_since` 에 안 들어간다(`state == WORKER_IDLE` 만 넣으므로 저절로). 넣으면 「30분째 노는 레인이 있는데 안 집는다」는 거짓 알람이 매번 뜬다 |
| 13 | `eta_for_new`(가상 잡) — 열린 레인이 held 하나뿐인 풀 | `test_eta_for_new_reports_held_by_load_for_the_ghost_job` | 행 사유 `held_by_load` · `wait_seconds is None` · `ahead` 는 실제 활성 잡 수 그대로 | `/api/eta` 와 `rcm eta` 는 `compute_queue` 를 그대로 탄다 — 여기가 빠지면 「지금 넣으면 언제?」가 없는 레인을 보고 답한다 |
| 14 | held 레인이 **busy 수·레인 수**를 안 바꾼다 — 로컬 2레인(1 busy · 1 held) | `test_a_held_lane_is_not_counted_as_busy` | `compute_queue` 결과의 running 행 수 1 · `live` 2 · `open` 1 | held 는 「도는 중」이 아니다. busy 로 세면 `lanes 2/2 busy` 가 되어 사람이 「꽉 찼다」로 읽는다 |

## 3. 상태 JSON (§5.1) — **추가가 아니라 기존 단언의 수정**

`WorkerInfo` 에 `hold_code: str | None`(`cpu_busy`·`no_sample`·`cooldown`) · `hold_detail: dict | None`
(예 `{"cpu_busy": 92.4}`) · `held_since: datetime | None` 이 붙고 `server_json` 의 워커 항목이 그 셋을 낸다.
`state` 에 **값이 하나 느는 것**이라 `schema_version` 은 1 그대로다.

**세 파일의 완전 일치 단언이 깨진다 — 새 테스트를 더하는 게 아니라 이 줄들을 고쳐야 한다**(실측 §14-A3,
줄 번호 재확인함):

| 파일·줄 | 오늘 단언 | 고칠 것 |
|---|---|---|
| `tests/test_status_schema.py:224` | `assert set(back["server"]["workers"][0]) == {lane, state, job_id, error, since, worker, display_name, pool}` | 키 집합에 `hold_code`·`hold_detail`·`held_since` 를 더한다 |
| `tests/test_server.py:400` | `assert doc["server"]["workers"] == [{…}]` — dict **전체** 비교 | 같은 dict 에 세 키를 `None` 으로 더한다(로컬 idle 레인) |
| `tests/test_worker_api.py:678` | `assert lane == {…}` — 원격 레인 dict 전체 비교 | 같은 dict 에 세 키를 `None` 으로 더한다(원격 busy 레인) |

그리고 **§6 이 `Estimate.shared` 를 실으면 네 번째가 깨진다**(명세 §5.1 목록에 없다):
`tests/test_status_schema.py:48` 의 `ESTIMATE_KEYS` 에 `shared` 를 더해야 하고, 그 상수를 빌려 쓰는
`tests/test_status_m5b.py:145` 도 같이 초록으로 돌아온다(상수 하나만 고치면 된다).

| # | 시나리오 | 테스트 함수 | 정확한 기대값 | 왜 중요한가 |
|---|---|---|---|---|
| 15 | 세 키를 더한 뒤에도 문서 최상위·`server`·`pools` 키 집합과 **`schema_version == 1`** 이 그대로 | `test_status_document_is_schema_v1_and_the_worker_keys_are_exact`(224줄 수정) | `set(workers[0])` 이 위 11개와 정확히 같고 `back["schema_version"] == 1` | 「키를 더하지 값을 바꾸지 않는다」가 M5f 의 전제다. 여기서 v2 가 되면 옛 CLI 가 전부 죽는다 |
| 16 | `held` 레인 하나를 직렬화 | `test_a_held_lane_serialises_its_hold_code_detail_and_since` | `{"lane": 2, "state": "held", "job_id": None, "error": None, "since": <iso>, "worker": None, "display_name": None, "pool": "default", "hold_code": "cpu_busy", "hold_detail": {"cpu_busy": 92.4}, "held_since": "2026-09-04T00:50:12Z"}` · `json.dumps` 가 터지지 않는다 | 화면 셋(터미널·웹·`rcm check`)이 전부 이 셋만 보고 그린다. `held_since` 는 `iso()` 로 `Z` 표기 |
| 17 | `idle`·`busy`·`down` 레인은 세 키가 전부 `None` | `test_hold_fields_are_null_on_idle_busy_and_down_lanes` | 세 키 `None`, 나머지 값 불변 | 「모르는 값은 null」 — 0 이나 빈 dict 로 새면 화면이 「보류됨」으로 그린다 |
| 18 | SSE `server` 이벤트(`server.py:405-410`)가 `hold_code` 를 싣는다 | `test_server_sse_event_carries_the_hold_code_of_each_lane` | `SseStream(srv, "/events")` + `srv.app._publish_server()` → 프레임 `event == "server"`, `data["workers"][i]` 의 키가 **정확히** `{lane, state, job_id, worker, hold_code}` · held 레인의 `hold_code == "cpu_busy"` | 안 실으면 필이 **다음 전체 폴링(10초)까지 이유 없는 `held`** 로 남는다. 「모르는 값은 —」이 「이유 없는 보류」로 보이는 자리 |
| 19 | held 가 아닌 레인의 SSE `hold_code` 는 `None` | `test_server_sse_event_sends_a_null_hold_code_for_open_lanes` | `data["workers"][0]["hold_code"] is None` | 위와 같은 이유의 반대 방향 |

## 4. `tests/test_render_m5f.py` — `rcm top` (§5.4)

⚠️ **오늘 `lanes >= 2` 머리줄은 보류 레인을 글자 하나까지 idle 과 똑같이 그린다**(§14-B7 재확인:
`lanes 1/2 busy` 두 경우가 `==` 로 참). 세는 자리를 새로 넣어야 한다.

| # | 시나리오 | 테스트 함수 | 정확한 기대값 | 왜 중요한가 |
|---|---|---|---|---|
| 20 | 로컬 2레인 · 레인 1 busy `#412` · 레인 2 `held`(`hold_code="cpu_busy"`, `hold_detail={"cpu_busy": 92.4}`) | `test_header_counts_a_held_local_lane` | 머리줄에 `lanes 1/2 busy · 1 held (cpu 92%)`. **오늘은 `lanes 1/2 busy`** — 빨강 | 첫 증상이 「두 번째 레인이 갑자기 멈췄다」가 되는 것을 막는 자리(§12 마지막 줄) |
| 21 | 같은 문서에서 레인 2 를 `idle` 로만 바꾼다 | `test_an_idle_lane_and_a_held_lane_no_longer_look_the_same` | 두 머리줄이 **달라야 한다**(`!=`). 오늘은 `==` | 20번의 대조군. 「센다」가 아니라 「보인다」를 잠근다 |
| 22 | 보류 레인이 하나도 없는 문서 | `test_header_without_a_held_lane_is_byte_identical_to_today` | `render(local_doc(), tz=UTC) == GOLDEN` · 2레인 문서 머리줄이 `lanes 1/2 busy` 로 끝난다(`held` 문자열 없음) | 완료 기준 1 — 보류가 없으면 오늘과 같다 |
| 23 | 4레인 · 2개 held(`cpu_busy` 92.4 · `no_sample`) | `test_several_held_lanes_are_all_counted` | `lanes 1/4 busy · 2 held (…)` — **세는 수는 2**. 오늘은 `lanes 1/4 busy` | 실측 §14-B7: 4레인 중 2개가 held 여도 오늘 머리줄은 `lanes 1/4 busy` 다 |
| 24 | `lanes == 1` — 로컬 레인 하나 | `test_a_single_lane_header_is_unchanged` | `worker busy #412` / `worker idle` 가 오늘과 바이트 단위로 같다. `held` 를 세는 조각이 **붙지 않는다** | 레인 1 은 게이트를 안 지나므로(결정 40) `held` 가 나올 수 없다. 필 접기는 결정 12 그대로 |
| 25 | 원격 필 — `build-02/1 idle` + `build-02/2 held` | `test_a_remote_held_pill_prints_the_state_word` | 머리줄에 `· build-02/1 idle · build-02/2 held`. **오늘 이미 초록**(상태 문자열이 그대로 나간다) | 원격 쪽은 손댈 필요가 없다는 것을 잠근다 — 고치다 깨뜨리는 것을 막는 회귀 |
| 26 | 레인 1 `down` + 레인 2 `held` | `test_a_down_lane_and_a_held_lane_are_both_visible` | `lanes 0/2 busy · 1 held (cpu 92%) · DOWN: lane 1` | 시나리오 11 의 화면 쪽. 죽은 레인이 보류 표시에 묻히면 진짜 원인을 못 찾는다 |
| 27 | 큐 행 사유 — `reason == "held_by_load"` + 그 풀에 `hold_detail={"cpu_busy": 92.4}` 인 held 레인 | `test_queue_row_reason_says_held_by_load_with_the_cpu_number` | 사유 줄이 `held by load · cpu 92%`. **오늘은 `held_by_load`**(`_reason_text` 의 `return reason or "unknown"` 폴백) — 빨강. 숫자는 기존 `_pct()`(`92.4 → "92%"`) | 코드가 아니라 문장을 보여 주는 자리(결정 37). 92% 가 없으면 「왜 보류인가」를 다시 물어야 한다 |
| 28 | 같은 사유인데 `hold_detail` 이 없다(`no_sample`·`cooldown`) | `test_queue_row_reason_without_a_detail_is_just_held_by_load` | 사유 줄이 정확히 `held by load` — `cpu —%` 나 `cpu None` 이 아니다 | 「모르는 값은 —, 절대 0 이나 빈칸이 아니다」. 없는 조각은 아예 안 붙인다 |
| 29 | 보류 레인이 있어도 나머지 절(`recent` · `medians` · `host`)과 큐 표의 다른 행이 그대로 | `test_the_rest_of_the_screen_is_unchanged_by_a_held_lane` | `body(out)` 이 held 없는 문서와 같다(큐 행 하나만 다르다) | 머리줄 세는 자리를 넣다가 `render_pool` 을 건드리는 것을 막는다 |

## 5. `tests/test_render_m5f.py` — `rcm check` (§5.4)

오늘 `_pools_row`(`cli.py:879`)는 `default (<N> lane[s])` 뒤에 원격 풀만 붙인다. 원격 held 는 이미
`build-02/2 held` 로 나오고 **FAIL 도 아니다**(프로브로 확인: `('pools', True, 'default (2 lanes) · linux
(build-02/1 idle · build-02/2 held)')`). 빠진 것은 **로컬 held 를 세는 자리**와 **경고 둘**이다.

| # | 시나리오 | 테스트 함수 | 정확한 기대값 | 왜 중요한가 |
|---|---|---|---|---|
| 30 | 로컬 2레인 중 1개 held · linux 풀에 `build-02/1 idle`·`build-02/2 held` | `test_pools_row_counts_held_local_lanes` | 상세가 `default (2 lanes · 1 held) · linux (build-02/1 idle · build-02/2 held)` | 오너가 「왜 안 도나」를 물을 때 처음 치는 명령이 `rcm check` 다 |
| 31 | 같은 상태의 종료 코드·라벨 | `test_a_held_lane_is_not_a_check_failure` | 행 라벨 `ok ` · `rcm check` 종료 코드 **0** | 보류는 **의도된 정상**이다(결정 45). FAIL 로 만들면 CI 가 게이트 때문에 빨개진다 |
| 32 | 보류 레인이 없을 때 | `test_pools_row_is_unchanged_without_a_held_lane` | `default (2 lanes)` — `· 0 held` 를 붙이지 않는다 | 완료 기준 1 |
| 33 | 어떤 레인이 `10 × admission_cooldown_seconds` **넘게** held(`held_since` 가 `generated_at` 보다 301초 앞, 기본 쿨다운 30초 → 임계 300초) | `test_a_lane_held_longer_than_ten_cooldowns_warns` | 새 행이 `warn` 라벨로 뜨고 **종료 코드 0** · 상세에 그 레인 이름과 잰 시간이 들어간다 | 「밸브 대신 계기」(결정 40). `hold_max_seconds` 를 안 두는 대신 이게 유일한 경보다 |
| 34 | 경계 — 정확히 300초 held | `test_a_lane_held_for_exactly_ten_cooldowns_does_not_warn` | 경고 없음(임계는 **배타적** — 명세 문면 「넘게」) | `stale()` 의 `age > 3 × interval` 과 같은 관례(§14-A4에서 경계가 배타적임을 실측) |
| 35 | 레인 ≥ 2 인 등록 워커의 `version` 이 서버보다 낮다 | `test_a_two_lane_worker_on_an_older_version_warns` | 새 행이 `warn` · 종료 코드 0 · 상세에 워커 이름과 두 버전 | §3.1 의 fail-open 구멍이 **그 워커에만 남아 있다**는 유일한 신호. 옛 워커는 굳은 표본을 계속 보내고 서버는 구분을 못 한다 |
| 36 | 레인 1 인 옛 워커 | `test_a_single_lane_worker_on_an_older_version_does_not_warn` | 경고 없음 | 레인 1 은 게이트를 안 지나므로 구멍이 없다. 여기서 경고하면 경고가 소음이 된다 |
| 37 | 두 경고가 동시에 · 나머지 행 전부 ok | `test_the_new_warnings_never_change_the_exit_code` | `warn` 행 2개 · 종료 코드 **0** · `presets`·`timezone` 행 위치 불변 | `cmd_check` 의 `ok_all = all(ok is not False …)` 가 `None` 을 통과시키는 것을 잠근다 |

## 6. `tests/web/admission.test.js` (§5.4) — 순수 함수만

| # | 시나리오 | 테스트 | 정확한 기대값 | 왜 중요한가 |
|---|---|---|---|---|
| 38 | `reasonText({reason:"held_by_load"}, NOW, status, "en")` | reasonText — held_by_load (en) | `text === T("en","reason.held_by_load")` 로 시작하고 CPU 값이 있으면 `· cpu 92%` 가 붙는다. **오늘은 `"unknown"`** — 빨강 | 서버는 코드를 보내고 화면이 문장을 만든다(결정 37) |
| 39 | 같은 행, `lang = "ko"` | reasonText — held_by_load (ko) | 오늘 `"알 수 없음"` → 한국어 문구(초안 「부하로 대기」). 두 언어의 문자열이 **서로 달라야** 한다 | 웹은 한국어 우선이다. 영어 문자열이 그대로 나오면 번역이 빠진 것 |
| 40 | 그 행의 나머지 계약 | reasonText — held_by_load is not actionable | `actionable === false` · `links` 는 `[]` · `cls` 는 `""` 또는 `"held"`(붉은 계열 아님) | 「의도된·자가 치유되는 상태」다. actionable 로 칠하면 `worker_down`·`stuck` 이 묻힌다 |
| 41 | 공개된 `rcm.ACTIONABLE` | ACTIONABLE does not list held_by_load | `rcm.ACTIONABLE.indexOf("held_by_load") === -1` · 배열의 나머지 7개는 그대로 | 결정 45 를 뒤집은 §13-H 를 **관측 가능한 형태로** 잠그는 유일한 방법(`ACTIONABLE` 은 export 돼 있다) |
| 42 | `notMoving(status)` — 큐에 `held_by_load` 행 하나뿐 | notMoving stays quiet for held_by_load | `{kind: "ok", lines: []}` | 「Not moving」이 **정직하게 조용**해야 한다. 늘 켜져 있으면 사람들이 패널을 무시한다 |
| 43 | `held_by_load` 행 + `worker_down` 행 | notMoving still reports the real problem next to a held job | `kind === "list"` · `lines.length === 1` · 그 줄의 `reason === "worker_down"` | 보류가 진짜 고장을 **가리지 않는다** |
| 44 | `workerState("frobnicated", lang)` — i18n 키가 없는 상태 | workerState falls back to the raw string | 두 언어 모두 `"frobnicated"` 를 그대로 돌려준다(예외 없음) | `I18N.has` 가드가 옛 페이지를 지킨다 — 서버가 새 상태 값을 보내도 화면이 안 깨진다 |
| 45 | `workerState("held", lang)` — 키를 더한 뒤 | workerState uses the new i18n key in both languages | `I18N.has("state.held") === true` · `workerState("held","en")` 은 `"held"` · `workerState("held","ko")` 는 `"held"` 가 **아니다**(한국어 낱말) | 44 와 짝. **오늘은 두 언어 다 `"held"`** — ko 쪽이 빨강 |
| 46 | `workerPills` — 로컬 2레인(1 busy · 2 held) + 원격 `build-02/2 held` | workerPills renders a held lane in both places | 로컬 필 `text` 가 `T(lang,"worker.lane_state",{lane:2,state:workerState("held",lang)})` · 원격 필 `text === "build-02/2 " + workerState("held",lang)` · 두 필 모두 `cls === "held"` · `jobId === null` | 필의 `cls` 가 곧 CSS 클래스다 — `held` 클래스가 없으면 색이 없는 채로 나간다 |
| 47 | `queueHeader(status)` 가 held 로 안 바뀐다 | queueHeader is unchanged by a held lane | `busyCount`/`laneCount` 는 로컬만 세고 held 는 busy 가 아니다 → 문구가 held 없는 문서와 같다 | 「`2/2 busy`」가 「`3/2`」처럼 새는 것을 막던 M5b-2 계약을 유지 |
| 48 | `hold_code`·`hold_detail`·`held_since` 가 **아예 없는** 옛 상태 문서 | an old status document without hold_code does not break the page | `workerPills`·`queueHeader`·`notMoving`·`reasonText`·`confidenceBadge` 가 전부 예외 없이 돌고, 결과 문자열 어디에도 `undefined`·`NaN`·`null` 이 안 나온다 | 서버만 먼저 올라가거나 페이지가 캐시된 옛것일 때. 「모르면 —」 |
| 49 | `reason` 은 `held_by_load` 인데 `server.workers[]` 에 held 레인이 하나도 없다(폴링 사이의 경합) | reasonText with no held lane in the document | 예외 없이 `T(lang,"reason.held_by_load")` 만 — 숫자 조각 없이 | 상태 경로와 큐 경로가 **다른 스냅샷**일 수 있다. 숫자를 지어내지 않는다 |

## 7. `Estimate.shared` 와 신뢰도 세 곳 (§6)

⚠️ `confidence()` 는 `compute_queue` 가 부르지 않는다 — **렌더 시점에 세 곳에서 따로** 계산한다:
`core/status.py:176`(`queue_row_json` 이 `estimate_json(..., confidence=...)` 로) · `core/render_text.py:193`
(`render_queue_row` 가 행 dict 에서 다시) · `web/app.js:263`(`confidenceBadge` 가 `est.confidence` 가 없을 때
`source`·`sample_count` 로 다시). 셋을 같이 고치지 않으면 `rcm top` 과 웹이 서로 다른 배지를 찍는다.

| # | 시나리오 | 테스트 함수 / 테스트 | 정확한 기대값 | 왜 중요한가 |
|---|---|---|---|---|
| 50 | `Estimate` 에 `shared: bool` 이 있고 기본이 `False` | `test_estimate_carries_a_shared_flag_defaulting_to_false`(`tests/test_queue.py`) | `compute_queue`·`eta_for_new` 가 낸 모든 행의 `estimate.shared is False` 이고 오늘의 숫자가 하나도 안 바뀐다 | 표본이 쌓이기 전(PR 2a·2b)에는 **아무 일도 일어나면 안 된다** |
| 51 | `confidence(..., shared=True)` 가 한 칸 내린다 | `test_confidence_demotes_one_notch_when_the_estimate_is_shared`(`tests/test_queue.py`) | `confidence("measured", 7, shared=True) == "med"` · `confidence("measured", 3, shared=True) == "low"` · `confidence("preset", 1, shared=True) == "low"` · `confidence("default", 0, shared=True) == "low"` — **`low` 는 `low` 로 둔다**(더 내려갈 곳이 없다) | 명세가 유일하게 못 박은 값. 오늘의 `test_confidence_rule`(`tests/test_queue.py:314`) 옆에 붙인다 |
| 52 | `shared` 가 `overdue`·`group wait` 를 못 이긴다 | `test_shared_does_not_override_overdue_or_group_wait` | `confidence("measured", 9, overdue=True, shared=True) == "overdue"` · `confidence("measured", 9, group_wait=True, shared=True) == "group wait"` | 오늘의 순서(`overdue` → `group_wait` → 나머지)를 유지한다. **명세가 안 정한 자리 — 질문 ⑥** |
| 53 | `estimate_json` 이 `shared` 를 낸다 | `test_estimate_json_carries_shared`(`tests/test_status_schema.py`) | `set(rows[0]["estimate"]) == ESTIMATE_KEYS` (= 오늘 11개 + `shared`) · 값은 bool · `schema_version` 1 그대로 | **`ESTIMATE_KEYS`(`test_status_schema.py:48`)가 깨진다** — 명세 §5.1 의 「깨지는 셋」 목록에 없는 **네 번째** 자리다 |
| 54 | 터미널 배지가 `shared` 를 읽는다 | `test_top_badge_demotes_a_shared_estimate`(`tests/test_render_m5f.py`) | `estimate = {source:"measured", sample_count:7, shared:true}` 인 행의 꼬리가 `(med · measured n=7)` — 오늘은 `(high · measured n=7)` | `render_text.py:193` 이 `est.get("shared")` 를 안 읽으면 `rcm top` 만 옛 배지를 찍는다 |
| 55 | 웹 배지가 `shared` 를 읽는다 | confidenceBadge — shared demotes (`tests/web/admission.test.js`) | `confidenceBadge({source:"measured", sample_count:7, shared:true})` → `cls "med"` · `confidence` 키가 **있으면** 서버 값이 이긴다(오늘 계약 그대로) | `app.js:263` 의 폴백 경로. 서버가 보낸 `confidence` 를 화면이 다시 계산해 뒤집으면 안 된다 |
| 56 | 세 곳이 같은 입력에 같은 답을 낸다 | `test_the_three_confidence_sites_agree_on_a_shared_estimate` | 같은 `Estimate`(measured·n=7·shared) 로 ① `status.py` 의 `queue_row_json(...)["estimate"]["confidence"]` ② `render_text` 가 그 행 dict 로 찍은 배지 낱말 ③ `rcm.confidenceBadge` 의 `cls` 가 **전부 `med`** | 명세가 「셋을 같이 고치지 않으면 서로 다른 배지를 찍는다」고 경고한 자리를 하나의 테스트로 묶는다 |

---

## 명세가 열어 둔 곳을 시나리오가 정한 값 (구현이 따를 것)

1. **머리줄 조각의 자리** — `1 held (…)` 를 `lanes B/N busy` **바로 뒤 · `DOWN:` 앞**에 둔다(#26). §5.4 는
   `lanes 1/2 busy · 1 held (cpu 92%)` 만 보여 주고 down 과 같이 있을 때를 말하지 않는다.
2. **`(cpu 92%)` 의 반올림** — 기존 `render_text._pct()`(`{int(round(v))}%`)를 쓴다. `92.4 → 92%`.
3. **`rcm check` 경고 임계는 배타적** — `> 10 × admission_cooldown_seconds` 일 때만 경고(#34). 서버의
   `stale()` 이 `age > 3 × interval` 인 것과 같은 관례.
4. **`held_by_load` 는 actionable 이 아니다** — 웹 `reasonText` 의 `actionable === false`(#40).

## 명세가 답을 안 주는 것 — 테스트를 쓰기 전에 물어야 할 질문

1. **큐 행이 `cpu 92%` 를 어디서 얻나(#27 을 막는다).** `render_text._reason_text(row)` 는 **행 dict 하나만**
   받는다. 그런데 `hold_detail` 은 `server.workers[]` 에 있고, 명세 §5.1 은 깨지는 단언으로 **워커 키 집합만**
   들었다 — 즉 큐 행 JSON(`ROW_KEYS`)에는 키를 안 더한다는 뜻으로 읽힌다. 셋 중 무엇인가:
   ① 행 JSON 에 `hold` 조각을 더한다(그러면 `test_status_schema.py:22` 의 `ROW_KEYS` 도 깨진다 — 다섯 번째),
   ② `_reason_text` 에 두 번째 인자(서버 워커 목록)를 준다(시그니처 변경),
   ③ 숫자를 포기하고 `held by load` 만 찍는다. **웹은 `reasonText(row, nowMs, status, lang)` 라 status 를
   이미 들고 있어 ②가 자연스럽지만, 터미널은 그렇지 않다.**
2. **보류 레인이 여럿일 때 어느 숫자를 찍나(#23·#27).** 코드가 다른 레인 둘(`cpu_busy 92%` · `no_sample`)이
   같이 held 면 머리줄과 큐 행이 무엇을 보여 주는가 — 최신? 최악? 코드별로 나눠? 명세는 한 레인 예시뿐이다.
3. **`rcm check` 가 워커 버전을 어디서 보나(#35 를 막는다).** `WorkerRow.version`·`lanes` 는 DB 에 있지만
   **`/api/status` 의 `server.workers[]` 에 없다**(오늘 키 8개를 확인함). `rcm check` 가 읽는 것은
   `/api/status` 와 `/api/health` 뿐이다. 새 키를 상태 문서에 더하면 §5.1 이 든 목록 밖에서 또 하나가
   깨지고, 안 더하면 이 경고를 만들 데이터가 없다.
4. **`rcm check` 가 `admission_cooldown_seconds` 를 어디서 보나(#33·#34).** 임계가 `10 × 쿨다운`인데 그 값은
   **서버의** `server.toml` 에 있다. `cmd_check` 는 로컬 `load_server_config()` 도 부르지만 그건 자기 머신의
   설정이라 원격 서버를 볼 때는 틀린 값이다. 상태 문서에 실을 것인가, 고정 300초로 둘 것인가?
5. **`not_scheduled` 인 잡도 보류 레인을 소비하나(#9-⑥ · #10).** 오늘 코드는 사유가 `not_scheduled` 든
   `waiting_for_lane` 이든 잡 하나가 `idle_since` 하나를 뺀다. 「대기 잡 하나가 보류 레인 하나를 소비」가
   같은 방식이라면 `not_scheduled` 로 끝난 잡도 held 를 하나 빼야 하는가, 아니면 held 는 별도 통인가?
   시나리오는 **별도 통**(idle 이 없을 때만 held 를 본다)으로 썼지만 명세 문면으로는 못 정한다.
6. **`shared` 가 `overdue`·`group wait` 와 만나면(#52).** 명세는 「`low` 는 `low` 로 둔다」만 말한다.
   `overdue` 는 이미 강등의 끝이라 그대로 두는 게 자연스럽지만 문서에 없다.
7. **`shared` 의 정의(#50·#56 의 의미를 가른다).** 「이 추정치가 **같이-실행 중앙값**에서 나왔다」인가,
   「혼자 잰 중앙값인데 **같이 돌 예정**이라 못 믿는다」인가? 둘은 신뢰도를 **반대 방향**으로 움직인다.
   §6 의 계층 대체 문면(`같이-실행 중앙값 → 전체 중앙값 → preset → default`)만으로는 어느 단계에서
   `shared = True` 가 되는지 알 수 없다.
8. **계층 대체가 `Estimate.source` 값을 늘리나.** 「같이-실행 중앙값」이 `source == "measured"` 를 그대로
   쓰는지 새 값(예 `measured_shared`)을 쓰는지에 따라 `confidence()` 의 `source == SOURCE_MEASURED` 분기와
   `ESTIMATE_KEYS`(값이 아니라 열거값이라 키 집합은 안 깨지지만 화면 문구 `· measured n=7` 이 바뀐다)가
   달라진다.
9. **웹 i18n 낱말 넷.** 명세 초안이 준 것은 한국어 셋뿐이다 — 「부하로 대기」(`reason.held_by_load`) ·
   「CPU 가 바빠서 안 집는 중 (92%)」(`hold.cpu_busy`) · 「표본 없음 — 안전하게 멈춤」(`hold.no_sample`).
   **`state.held` 의 한국어·영어**(#45), **`hold.cooldown` 의 두 언어**, `reason.held_by_load` 의 영어가 없다.
   영어는 기존 관례상 enum 그대로(`held`)일 가능성이 높지만 한국어는 지어낼 수 없다.
10. **`hold_detail` 의 키 이름.** §5.1 예시는 `{"cpu_busy": 92.4}` 하나뿐이다. `no_sample`·`cooldown` 일 때
    detail 이 `None` 인지, `{"age_seconds": …}`·`{"remaining_seconds": …}` 같은 것이 들어가는지 안 나온다
    (#28 은 「없으면 조각을 안 붙인다」로만 잠갔다).
11. **필의 CSS 클래스 `held`(#46).** `workerPills` 는 `cls = w.state` 를 그대로 쓰므로 새 값이 저절로
    `"held"` 가 된다. 그런데 `style.css` 에는 `.wk.busy`·`.wk.down` 규칙만 있고 `.wk.idle` 은 없다 — 즉
    **`held` 필은 idle 필과 똑같이 보인다.** 터미널에서 그걸 고치는 게 §5.4 인데 웹은 어떻게 할지 안 나온다.
