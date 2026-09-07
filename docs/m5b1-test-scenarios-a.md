# M5b-1 테스트 시나리오 A — `pool` 컬럼 · 풀별 큐 · `pools[]` 순회 (2026-09-06)

`docs/m5-workplan.md` 「M5b. 원격 워커」(모델 · 프로토콜의 claim 규칙) 와 순서 3 의 **M5b-1**(DB 기반 +
`pool` 컬럼 + 풀별 `status()`/render/eta/jobs/web 순회) 을 pytest 로 옮긴 것이다(test-first, 역할 A).
`src/` · `scripts/` · `.github/` · 기존 테스트는 건드리지 않았다. 원격 워커 자체(`/worker/*` · 토큰 kind ·
`runner.py` · `rcm worker`)는 M5b-2~4 의 몫이라 여기 없다 — **M5b-1 에서 기본 풀이 아닌 풀은 워커가 없다**는
것이 이 시나리오의 전제다(그 풀의 대기 잡은 `worker_down` · `finish_at`/`wait_seconds` null, fail-open 금지).

인계 시점 상태(`ruff check` · `ruff format --check` 깨끗, line-length 100):

| 파일 | 대상 | 테스트 함수 / 수집 건수 | 상태 |
|---|---|---|---|
| `tests/test_pools.py` | `core/model.py` 의 `DEFAULT_POOL` · `Job.pool` · `Preset.pool`/`pools` · `core/queue.py` 의 `split_by_pool` · 풀별 `compute_queue` · `eta_for_new(pool=)` · 풀별 `medians_from` | 14 / 14 | ImportError(빨강 — `DEFAULT_POOL` 없음) |
| `tests/test_store_m5b.py` | `store.py` — DB v4 `jobs.pool` · 3→4 마이그레이션 · `create_job(pool=)` · `claim(lane, now, pool=)` · 풀 안의 그룹 배제 · `list_pools()` | 12 / 12 | 11 빨강 · 1 초록(회귀 잠금) |
| `tests/test_status_m5b.py` | `core/status.py` — `pools[]` 여러 개 · 행의 `pool` 키 · 원격 풀 `lanes 0`/`hosts []` · 풀별 중앙값 · `schema_version 1` | 9 / 9 | 8 빨강 · 1 초록(회귀 잠금) |
| `tests/test_server_m5b.py` | `config.parse_preset` 의 `pool`/`pools` · `POST /jobs` · `/api/status` · `POST /api/eta` · `GET /jobs/{id}` · 로컬 워커의 풀 격리 · `rcm top --json`/텍스트 | 19 / 33 | 전부 빨강(픽스처가 `unknown key(s): pool, pools` 로 죽는다) |

지금 빨간 이유는 전부 「아직 없는 인터페이스」다: `cannot import name 'DEFAULT_POOL'` ·
`create_job() got an unexpected keyword argument 'pool'` · `Job.__init__() ... 'pool'` ·
`'Preset' object has no attribute 'pool'` · `no attribute 'list_pools'` · `no such column: "pool"` ·
`assert 3 == 4`(DB_VERSION) · `preset 'lin': unknown key(s): pool, pools`. 내 실수로 빨간 것은 없다.

공통 규칙: 잡은 `jobfactory.job()` 으로 만들고 `pool=` 은 `**kw` 로 넘긴다(팩토리는 안 고쳤다 — `Job` 에
필드가 생기면 그대로 통과한다). 시각은 고정 NOW. `compute_queue`·`eta_for_new` 호출 모양은 `test_queue.py`
의 `rows_for`/`eta` 와 같고, 서버 테스트는 `test_server.Server`(in-process HTTP, 워커 off) 에 프리셋만
바꿔 단다(`test_server_m5` 방식). 키 집합은 `test_status_schema` 의 `ROW_KEYS`/`RECENT_KEYS`/`POOL_KEYS` 를
import 해 `| {"pool"}` 로 비교한다(그 파일은 손대지 않았다).

## 1. `tests/test_pools.py` — 순수 규칙

도우미: `rows_for(jobs, wk=, medians=)` · `eta(jobs, pool=None|이름, wk=)`(`pool=None` 이면 인자를 아예 안
넘겨 기본값을 본다) · `sample(id, key, job_seconds, pool=)`(하루 전 성공 표본).

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 상수·잡 기본값 | `DEFAULT_POOL == "default"` · `job(1).pool == "default"` · `job(2, pool="linux")` · `replace(job, pool=)` |
| 2 | 프리셋 기본값 | `GATE.pool == "default"` · `pools == ()` · `Preset(pool="linux", pools=("default",))` · `pools` 없이 `pool="linux"` 도 된다 |
| 3 | `split_by_pool` 순서 | mac2·linux·default·arm·linux·default → 키 `["default","arm","linux","mac2"]` · 풀 안은 입력 순서 · 잡 수 보존 · 각 잡의 `pool` 이 키와 같다 |
| 4 | `split_by_pool` 빈 입력·기본 풀 없음 | `[]` → `{}` 또는 `{"default": []}` 둘 다 허용(없는 풀을 지어내지 않는다) · linux 만 있으면 다른 키는 `["linux"]` 뿐이고 `out.get("default")` 는 비어 있다 |
| 5 | `split_by_pool` 은 상태로 안 거른다 | 종료 표본·running·queued 가 같이 들어가도 풀별로만 나눈다(중앙값 계산에도 같은 함수를 쓴다) |
| 6 | 그룹 배제는 풀 안에서 | 기본 풀 `devices` running(409) + 기본 `devices` queued(413) + 리눅스 `devices` queued(414): 기본 풀 계산은 413 이 `blocked_by_group`(409), 리눅스 풀 계산은 414 가 `waiting_for_lane` · `blocked_by None` · position 1 · wait 0 · finish NOW+540 |
| 7 | 순번·대기는 풀마다 다시 | 기본 1,3 / 리눅스 2,4 → 각 풀에서 position 1,2 · 둘째 잡의 wait 400 · `ahead_job_id` 는 같은 풀의 앞 잡 |
| 8 | 워커 없는 풀(순수) | `workers=[]` → `worker_down` · position 1 · `finish_at`/`wait_seconds` None · `expected_seconds` 는 안다 |
| 9 | `eta_for_new(pool="linux")` | 기본 풀에 running 1 + queued 2 가 있어도 리눅스 가상 잡은 `row.job.pool == "linux"` · position 1 · `ahead 0` · wait 0 · finish NOW+400; 같은 목록으로 `pool="default"` 면 position 3 · `ahead 3` · wait 1280 |
| 10 | `eta_for_new` 기본값 | `pool` 생략 = 기본 풀 — 리눅스 잡 2개는 안 센다(position 1 · ahead 0) |
| 11 | `eta_for_new` 워커 없는 풀 | 리눅스 잡 1 + `workers=[]` → position 2 · `ahead 1` · `worker_down` · 시각 None |
| 12 | 가상 잡 id | 다른 풀의 잡 id 와 겹치지 않는다 |
| 13 | 풀별 중앙값 | 같은 키 표본을 `split_by_pool` 로 나누면 100/300, 섞으면 `sample_count 4` — 호출자가 나눠 넘겨야 하는 이유 |
| 14 | 풀 중앙값 → 그 풀 추정 | 리눅스 중앙값 120 을 리눅스 잡에만 넘기면 `expected 120` · finish NOW+120, 기본 풀은 400 |

## 2. `tests/test_store_m5b.py` — DB v4

도우미: `enqueue(store, ..., pool=None|이름)`(None 이면 인자를 안 넘긴다) · `finished_job(store, pool=, finished=, tree=)`
(그 풀에서 claim → finish) · `job_columns(path)`(`PRAGMA table_info(jobs)` 를 이름→행 dict 로).

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 새 DB | `DB_VERSION == 4` · `user_version() == 4` · `jobs.pool` 이 TEXT · `notnull 1` · 기본값 `default`(따옴표는 무시) |
| 2 | 3→4 마이그레이션 | v4 DB 에서 pool 열(과 그 인덱스)을 떼고 `user_version=3` → 다시 열면 4 · `healthy()` · running/queued 행 그대로 · 옛 행은 `pool == "default"` · `list_pools() == ["default"]` · `claim` SQL 이 돈다 · `claim(pool="linux")` 는 None · 세 번째 열기는 변화 없음 |
| 3 | `create_job(pool=)` 왕복 | 생략 → default · `"linux"`/`"mac2"`(uploading 포함) · 명시적 `"default"` |
| 4 | `list_active`/`list_recent`/`list_samples` | 풀을 안 가린다(모든 풀) · 각 잡이 `pool` 을 싣는다 |
| 5 | 풀이 살아남는다 | claim → cancelling → cancelled 를 거쳐도 `pool == "linux"` |
| 6 | 풀별 claim | 더 오래된 리눅스 잡을 두고 기본 풀 claim 은 기본 잡을 잡는다 · `pool="default"` 는 생략과 같다 · `pool="linux"` 가 리눅스 잡(lane·state·pool) · 모르는 풀 `mac2` 는 None |
| 7 | 풀 안의 우선순위 | 리눅스 high 가 리눅스 older normal 보다 먼저 · 기본 풀의 high 는 무관 |
| 8 | 그룹 배제는 풀 안에서 | 기본 `devices` running 중에 리눅스 `devices` claim 됨 · 리눅스 안 둘째 `devices` 는 None · 그룹 없는 리눅스 잡은 건너뛰어 잡힘 · 끝나면 둘째가 잡힘 · **반대 방향**(리눅스 running devices 가 기본 풀 devices 를 안 막음) |
| 9 | 회귀 | 기본 풀 안의 그룹 배제는 그대로(지금 초록) |
| 10 | `list_pools` 순서·중복 | 빈 DB → `["default"]` · mac2·linux·linux → `["default","linux","mac2"]`(기본 풀에 잡이 없어도 먼저) · default·arm(uploading) 추가 → `["default","arm","linux","mac2"]` |
| 11 | 종료 잡만 있는 풀 | 성공한 리눅스 잡 하나뿐이어도 `linux` 가 남는다 · 대기 중 취소된 mac2 도 남는다 |
| 12 | 타입·중복 | 전부 `str` · 중복 없음 |

## 3. `tests/test_status_m5b.py` — 스키마 v1 + 풀

도우미: `pool(name, queue=, recent=, medians=, lanes=)`(기본 풀 레인 1, 나머지 0 · hosts `()`) · `model(*pools)`
(server.lanes 1 · 워커 1) · `two_pool_doc()`(기본: 412 running + 413 queued + 최근 411 · 리눅스: 414·415 queued,
`workers=[]`, 리눅스 중앙값 `gate:full 120/3`, 최근 410).

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 모양 | `json.dumps` 됨 · `schema_version == 1` · `pools` 이름 `["default","linux"]` · 풀마다 `set == POOL_KEYS`(`workers` 없음) · 큐 행 `ROW_KEYS | {"pool"}` · 최근 행 `RECENT_KEYS | {"pool"}` · `estimate` 키 그대로 · `server.lanes 1` |
| 2 | 행의 `pool` | 큐·최근 행 전부 자기 풀 이름 · `log_tail` 토큰 조건은 그대로 |
| 3 | 원격 풀 항목 | `lanes 0` · `hosts []` + `hosts_error None`(실패가 아니다) · `*_error None` · `recent_count` 같음 |
| 4 | 워커 없는 풀의 대기 잡 | 414: queued · `worker_down` · position 1 · `finish_at`/`wait_seconds` None · `expected 120`(리눅스 실측) · `source measured` · `confidence med` · `lane`/`ahead_job_id`/`progress` None; 415 position 2 |
| 5 | 기본 풀은 평소 이유 | 412 `running`(finish 있음) · 413 `waiting_for_lane` · position 1 · `ahead 412` · wait 340 · expected 400 |
| 6 | 풀별 중앙값 | 같은 키 `gate:full` 이 기본 400/80/7, 리눅스 120/5/3 · `deploy-dev` 는 기본 풀에만 |
| 7 | 행 함수 | `queue_row_json`/`recent_json` 에 `pool` 이 있고 기본값 `"default"` · `replace(job, pool="linux")`/`"mac2"` 반영 · 키 집합 |
| 8 | 풀 하나 회귀 | pools 한 개 · 이름 default · 행마다 `pool` 만 더 있다 |
| 9 | 실패 섹션 | 원격 풀도 `null + *_error` 규칙 그대로(지금 초록) |

## 4. `tests/test_server_m5b.py` — 설정 · 라우트 · CLI

프리셋: 기존 `PRESETS` + `lin`(`pool = "linux"`, `pools = ["default"]`) + `strict`(`pool = "linux"`, pools 없음).
도우미: `pool_server(tmp_path, workers=)` · `submit(srv, token, preset=, tree_hash=, pool=, join=)` · `new_job` ·
`view` · `status_doc` · `pools_by_name` · `eta(srv, preset, pool=)` · `finish_via_store(srv, jid, pool=, seconds=)`
(워커 없이 그 풀에서 claim → `seconds` 뒤 성공, 스냅샷 TTL 0.2초를 넘기려 0.3초 잔다) · `env`(test_cli_m5 와 같다).

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `parse_preset` | 생략 → `pool "default"` · `pools ()` · `lin` → `("default",)` · `strict` → `()` · `pools=["linux","mac2"]` 만 있는 프리셋 |
| 2 | 잘못된 값(9건) | `pool` 이 `""`·공백·`-linux`·정수·리스트, `pools` 가 문자열·정수 항목·나쁜 이름·`""` → `ConfigError` 에 `preset 'lin'` 과 `pool` 이 있고 **`unknown key` 는 아니다**(지금은 unknown key 로 죽어 빨강) |
| 3 | TOML 파일 | `load_server_config` 로 `pool`/`pools` 키가 읽힌다 |
| 4 | `POST /jobs` 생략 | `lin` → linux · `ok` → default · `strict` → linux (`GET /jobs/{id}` 와 store 둘 다) |
| 5 | 허용 풀 지정 | `lin`+`default` → default · `lin`+`linux` · `ok`+`default` |
| 6 | 거부 | `strict`+`default` → 400, 문구에 `linux` 와 `pool`(admin 도 예외 없음) · `lin`+`windows` → 400, 문구에 `linux` 와 `default` · `ok`+`linux` → 400, 문구에 `default` · 잡이 안 생긴다 |
| 7 | 형식(7건) | 정수·bool·리스트·dict·`""`·공백·`-x` → 400, 문구에 `pool` · 잡 없음 |
| 8 | `/api/status` 두 풀 | 이름 `["default","linux"]` · 풀마다 `POOL_KEYS` · 기본 큐 `[(ok,"default",1)]` · 리눅스 큐 `[(lin,1),(strict,2)]` · `server.lanes 1`·워커 1 · 리눅스 `lanes 0`·`hosts []`·`recent []`·`medians {}` · `recent_count` 같음 |
| 9 | 기본 풀은 비어도 먼저 | 리눅스 잡만 있어도 `pools[0].name == "default"`(빈 큐 · lanes 1), `pools[1]` 이 linux |
| 10 | 기본 풀만 있으면 풀 하나 | `["default"]` — 풀 하나일 때 화면 그대로 · 행에 `pool` |
| 11 | 워커 없는 풀의 queued 잡 | 트리를 올려 queued 로 만든 리눅스 잡: `worker_down` · position 1 · `finish_at`/`wait_seconds` None · `expected_seconds` 는 있음; 같은 시각 기본 풀 queued 잡은 `waiting_for_lane`/`not_scheduled` · finish 있음 · wait 0; `GET /jobs/{id}` 도 같다 |
| 12 | uploading 은 uploading | 원격 풀이라도 업로드 중이면 `reason uploading`(업로드가 먼저) |
| 13 | 로컬 워커의 풀 격리(워커 on) | 리눅스 queued 잡을 두고 기본 풀 잡은 돌아 succeeded · 1초 뒤에도 리눅스 잡은 queued(`started_at`/`lane` None) · 로컬 레인은 idle · 리눅스 행은 `worker_down` |
| 14 | 최근 완료·중앙값은 풀별 | store 로 리눅스 `lin` 잡 60s·100s, 기본 풀 `lin` 잡(`pool="default"`) 200s·200s 를 끝내면 `recent` 가 풀별로 갈리고(`pool` 키) 같은 키 `lin` 의 중앙값이 리눅스 80/2 · 기본 200/2 |
| 15 | 종료 잡만 남은 풀 | 리눅스 잡을 대기 중 취소 → 풀 목록에 linux 가 남고 `recent` 에 `(id, cancelled, linux)` · 기본 풀 recent 는 비어 있다 |
| 16 | `/api/eta` 풀별 | 기본 풀 uploading 2개: `lin` → `job.pool linux` · position 1 · `ahead 0` · `worker_down` · finish None · `id`/`url` None; `lin`+`pool default` → position 3 · `ahead 2` · finish 있음; `ok` → position 3 |
| 17 | `/api/eta` 검증 | `strict`+`default` → 400(`linux`) · `lin`+`windows` → 400(`pool`) · `ok`+정수 → 400 · `strict` 생략 → linux |
| 18 | `GET /jobs/{id}` 의 `pool` | uploading → queued → (store claim) running(position None) → (store finish) succeeded 최근 완료 모양까지 전부 `pool == "linux"` |
| 19 | `rcm top` | `top --json` 문서의 `pools[0].name == "default"`(빈 큐) · `pools[1]` linux 에 잡 · 텍스트 `top` 도 두 풀에서 죽지 않고 `#<id>` 를 보인다 |

## 가정 (구현이 달리 정하면 테스트를 고쳐야 하는 것)

1. **`split_by_pool` 은 잡이 있는 풀만 돌려준다**고 봤다(빈 입력은 `{}` 또는 `{"default": []}` 둘 다 통과하게
   느슨히 잠갔다). 기본 풀을 항상 넣는 것은 서버 `status()` 의 일이다(`store.list_pools()` + 로컬 풀).
2. **`split_by_pool` 은 상태로 거르지 않는다** — 표본(종료 잡)도 같은 함수로 나눈다.
3. **풀의 `recent` 는 그 풀의 종료 잡만** 싣는다(명세는 `medians` 만 「풀별」이라고 적었지만 `recent` 가
   `pools[]` 안에 있으니 같은 규칙으로 봤다). `recent_count` 는 풀마다 같은 설정값.
4. **`list_pools()` 의 「최근」은 종료 잡 전부**(보존 기간 안)로 봤다 — 최근 완료 창(`recent_count`) 으로
   자르는지는 안 잠갔다(테스트는 「가장 최근 종료 잡 하나」만 쓴다). 반환은 `list[str]`, 기본 풀이 잡이 없어도 첫 항목.
5. `jobs.pool` 기본값 검사는 따옴표를 벗겨 `default` 인지만 본다(`DEFAULT 'default'` 든 `"default"` 든).
6. `eta_for_new(pool=)` 의 `ahead` 는 **그 풀의 잡만** 센다. 가상 잡 id 는 다른 풀 id 와 겹치지 않으면 된다.
7. 서버 400 문구: 허용 풀 이름을 전부 적는다고 봤다(`strict`+`default` → `linux` 포함, `lin`+`windows` →
   `linux` 와 `default` 포함). 형식 오류(비문자열·빈 문자열·이름 규칙 위반)는 문구에 `pool` 만 있으면 된다.
   admin 도 허용 풀 밖은 400(우선순위와 달리 admin 예외가 없다).
8. 원격 풀의 `hosts` 는 `[]` + `hosts_error None`(워커 표본이 없는 것은 실패가 아니다). `lanes` 는 0.
9. 로컬 워커(`worker.py`)는 `store.claim(lane, now)` 를 그대로 불러 기본 풀만 잡는다(시나리오 4-13).
10. `rcm top` 텍스트(`render_text`)가 `pools[]` 를 순회한다는 것은 M5b-1 범위 문구(「풀별 … render … 순회」)를
    따랐다. 웹(`app.js` 의 `pool0`) 은 여기서 잠그지 않았다(브라우저 테스트는 역할 밖).

## 명세 의문 (오너/구현자 결정 필요)

1. **합류 키에 풀이 들어가나?** `join_key(preset, inputs, identity)` 에 풀이 없으면 `lin` 을 `pool default` 로
   낸 세션이 리눅스 풀에 있는 같은 트리 잡에 합류한다(다른 머신에서 도는데 「같은 잡」이 된다). 풀이 다르면
   합류하지 않는 게 맞아 보이지만 명세에 없어 **테스트로 잠그지 않았다**.
2. **`preset_json` 에 `pool`/`pools` 를 싣나?** 웹 제출 폼·`rcm presets` 가 허용 풀을 알려면 필요하지만,
   `test_status_schema.py` 가 프리셋 키 집합을 **정확히** 잠그고 있어 지금 넣으면 그 테스트가 깨진다. M5b-4 로
   미루거나 그 테스트를 같이 고쳐야 한다. 여기서는 잠그지 않았다.
3. **`POST /jobs` 응답에 `pool` 을 넣나?** (`priority` 는 넣는다.) 넣는 게 자연스럽지만 명세에 없어 잠그지 않았다.
4. **`git_ref` 제출의 `pool`** — 같은 규칙일 텐데 git 저장소 픽스처가 필요해 여기선 안 다뤘다.
5. **`rcm run --pool` · `rcm eta --pool`** — 모델 절은 「세션은 `--pool` 로 고른다」고 하지만 M5b-1 범위 문구엔
   CLI 플래그가 없다. 이번 PR 인지 M5b-4 인지 정해야 한다. 잠그지 않았다.
6. **`recover_on_start` 와 원격 풀** — M5b-1 에는 원격 running 잡이 없지만, M5b-2 부터는 서버 재시작이 원격
   워커의 running 잡을 `lost` 로 만들면 안 된다(워커는 살아 있다). M5b-2 명세에서 정해야 한다.
7. **`list_pools()` 가 설정에만 있는 풀도 세나?** 프리셋이 `pool = "linux"` 인데 잡이 하나도 없으면 화면에 linux
   풀이 안 보인다. 「잡이 있는 풀」로 봤지만(명세 문구), 워커가 등록되는 M5b-2 부터는 `workers` 표도 합쳐야 한다.
8. **`Preset.pools` 의 뜻 — 역할 A/B 충돌.** 나는 받은 인터페이스대로 `pools` 를 「추가로 허용하는 풀」(기본은
   `()`, 자기 `pool` 은 안 들어간다) 로 잠갔다(`GATE.pools == ()` · `strict.pools == ()` ·
   `lin.pools == ("default",)`: `tests/test_pools.py` 2번 · `tests/test_server_m5b.py` 1번). 같은 워크트리에
   병렬로 들어온 역할 B 의 `tests/test_config.py` 추가분(§5, `docs/m5b1-test-scenarios-b.md`)은 「`pools` 에는
   자기 `pool` 이 항상 들어 있다(정규화)」고 가정한다. 둘 다 초록일 수는 없다 — 구현자가 하나를 고르고 반대쪽
   테스트를 고쳐야 한다. 「기본 풀은 `pools` 에 없어도 항상 허용」(서버 규칙) 은 양쪽이 같다.

## 구현자가 같이 고쳐야 하는 기존 테스트 (DB_VERSION 3 을 고정)

- `tests/test_store.py::test_fresh_db_is_latest_schema_with_artifacts_purged_at` — `DB_VERSION == 3 and user_version() == 3`
- `tests/test_store.py::test_migration_from_v1_adds_the_columns_and_keeps_rows` — `s2.user_version() == 3` · `s3.user_version() == 3`
  (v1 흉내에 `pool` 열도 떼야 진짜 v1 이다)
- `tests/test_store_m5.py::test_fresh_db_is_schema_v3_with_priority_blobs_and_notifications` — `DB_VERSION == 3`
- `tests/test_store_m5.py::test_migration_v2_to_v3_adds_priority_and_tables_and_keeps_rows` — `== 3` 두 곳
  (v2 흉내에 `pool` 열도 떼야 한다)

그 밖의 기존 테스트(`test_status_schema.py` 의 `len(pools) == 1` · `test_server.py` 의 `len(doc["pools"]) == 1`)
는 기본 풀만 있을 때 풀이 하나라는 규칙과 맞으니 그대로 초록이어야 한다 — 시나리오 3-8 · 4-10 이 같은 것을 잠근다.
