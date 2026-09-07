# M5b-1 테스트 시나리오 — 담당 B (설정 · CLI · 렌더 · 웹 · 문서) (2026-09-06)

> `docs/m5-workplan.md` 「순서」 3 의 **M5b-1**(DB v3 기반 + `pool` 컬럼 + 풀별 `status()`/`render_text`/`cmd_eta`/`cmd_jobs`/
> 웹의 `pools[]` 순회 — 풀 하나일 때 화면은 그대로) 가운데 **설정 · CLI · 렌더 · 웹 순수 함수 · 문서** 몫.
> `src/` · `scripts/` · `.github/` · README · 기존 테스트는 건드리지 않았다(`tests/test_config.py` 는 **끝에 M5b-1 블록만 추가**).
> 구현 전이라 **빨간 것이 정상**이다. 서버·저장소 쪽(DB `pool` 컬럼 · `status()` 의 `pools[]` · 제출 검증 · claim 의 풀 배제)은
> 같은 워크트리에 병렬로 들어온 다른 담당의 몫(`tests/test_server_m5b.py` · `tests/test_store_m5b.py` · `tests/test_status_m5b.py` ·
> `tests/test_pools.py`) — 그쪽은 읽기만 했고, 여기서는 CLI 를 통해 관찰되는 결과만 잠갔다. 겹치는 `parse_preset` 의 `pool`/`pools`
> 케이스는 해석을 그쪽에 맞췄다(§5-2).

## 1. 파일과 개수

| 파일 | 대상 | 수집 | 지금 |
|---|---|---|---|
| `tests/test_config.py` (M5b-1 블록 추가) | 프리셋 `pool` · `pools` 파싱 · 기본값 · 잘못된 값 · `preset_json` | 21 (함수 6 · parametrize 15) | 6 빨강 · 15 초록(§4 「우연히 초록」) — 기존 77 은 전부 초록 |
| `tests/test_cli_m5b.py` (신규) | `rcm run --pool` · `rcm jobs [--pool]` · `rcm top` 풀 헤더 · `rcm eta --pool` · `presets[].pool/pools` · 로컬 워커는 default 만 · recent 행의 pool | 10 | 10 error(`pool_srv`/`pool_live` 픽스처가 `parse_preset` 의 `unknown key(s): pool, pools` 로 ConfigError) |
| `tests/test_render_m5b.py` (신규) | `render` 의 `pools[]` 순회 · `render_pool` · 풀 하나면 바이트 단위로 같음(GOLDEN) · `(pool linux · no workers)` · 절 불변 · 머리 집계 · host 블록 | 9 | 6 빨강 · 3 초록(GOLDEN · pools 빈 문서 회귀 · 절 불변 가드) |
| `tests/web/pools.test.js` (신규) | `rcm.poolSummary` · `rcm.poolHeader` · `notMoving`/`yourJobs` 가 모든 풀을 봄 · 회귀 | 24 | 21 빨강 · 3 초록(회귀 가드) |
| `tests/test_docs_m5b.py` (신규) | README 「Session commands」 표 · 「Presets」 절 · `examples/server.toml` · CHANGELOG | 10 (함수 5 · parametrize 5) | 9 빨강 · 1 초록(파일 존재) |

실행: `ruff check` · `ruff format --check` 통과(CJK 2칸 기준 100). `node --check tests/web/pools.test.js` 문법 오류 없음.
pytest·ruff 는 스크래치 venv(python3.11 · pytest 8 · ruff)로 돌렸다(이 워크트리엔 venv 가 없다).
합계 74건(pytest 50 · node 24) 중 **52 빨강**(pytest 31 = 실패 21 + error 10 · node 21) · 22 초록(pytest 19 · node 3).

## 2. 공용 규칙 · 도우미

- `tests/test_cli_m5b.py` 의 `run(capsys, argv)` 는 test_cli_m5 처럼 **SystemExit 을 코드로 바꾼다**(아직 없는 `--pool` 옵션이
  argparse 에서 `SystemExit(2)` 로 나온다).
- 서버는 `test_server.Server` 에 `LIN_PRESET = sh("lin", "echo lin", pool="linux", pools=["default"])` 를 끼운 `pool_srv`
  (workers=False) · `pool_live`(workers=True). `make_pool_server` 는 test_server_m3.make_server 의 요령(`cfg.presets` 교체 뒤 `app.start()`).
- `submit(capsys, tree, preset, *extra)` — `rcm run <preset> --no-wait --dir <tree> …` 로 제출·업로드까지 끝내고 job id 를 준다.
  같은 트리라도 프리셋이 다르면 합류하지 않으므로 기본 풀에 둘을 넣을 땐 `ok` · `bad` 를 쓴다.
- `pools_of(server)` → `/api/status.pools[]` 를 이름으로(이름 유일). `all_rows` → 모든 풀의 큐 행. `pool_of(server, jid)` → 그 잡이
  든 풀 이름(정확히 한 풀 · 행의 `pool` 키도 같아야 한다). `pool_header_lines(out)` → `rcm jobs` 텍스트에서 `pool <name>` 로 시작하는 줄.
  `record_posts(monkeypatch)` → `Client.post_json` 의 (path, body) 기록(원래 동작 유지).
- `tests/test_render_m5b.py` 의 `one_pool_doc()` = test_render_m5.doc()(running #412 · queued #413). `linux_pool(default, lanes, running)`
  은 기본 풀을 복사해 `linux` 로: `lanes` · `hosts: []` · 큐는 `waiting_row`(#413 복사 → #512 · #513, `reason: worker_down`,
  `finish_at/wait_seconds/remaining_seconds: null`) 와 running 이면 `running_row`(#412 복사 → #511, lane 1). `default_section(out)` 은 첫
  `queue — ` 줄부터 `(pool ` 가 든 줄 앞까지(풀 하나면 끝까지). `GOLDEN` 은 오늘(M5a 시점) `render(doc(), tz=UTC)` 의 출력 그대로.
- `tests/web/pools.test.js` 의 `withLinux(status, queue, patch)` 는 `pools[0]` 을 복사해 `linux · lanes 0 · hosts []` 풀을 붙인다.
  `downRow(status, id, position, patch)` 는 #414 를 복사한 `worker_down` 행(ETA 없음 · 5분 대기).
- `tests/test_docs_m5b.py` 는 test_docs_m5 의 `read` · `has` · `unreleased` 를 그대로 쓰고 `section(text, heading)`(`## …` 절) ·
  `table_row(text, command)`(첫 칸이 `` `rcm <command>`` 인 표 행) 을 더했다.

## 3. 시나리오

### 3.1 `tests/test_config.py` — 프리셋 `pool` · `pools`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | `pool` 없음 · `Preset(...)` 모델 기본값 | `.pool == "default"` · `pools == ()`(추가 허용 풀 없음 — 자기 풀만) |
| 2 | `pool = "linux"` 만 | `.pool == "linux"` · `pools == ()` |
| 3 | `pool = "linux"` + `pools = ["default", "linux"]` / `pools = ["linux", "default"]`(pool 기본) | 준 그대로, 파일 순서 |
| 4 | `pool = "linux"` + `pools = ["default"]`(CLI 의 `lin`) | `pools == ("default",)` — `--pool linux` 는 규칙으로 허용(CLI 3.2-2 가 본다) |
| 5 | 파일에서 `gate` 는 `pool = "linux"` + `pools`, `deploy` 는 없음 | `gate` linux/`("default","linux")` · `deploy` default/`()` |
| 6 | `pool` 에 `1` · `true` · `""` · `"bad name"` · `"-linux"` · `["linux"]` · 65자 (7건) | ConfigError 에 `preset 'gate'` + `pool` |
| 7 | `pools` 에 `"linux"`(문자열) · `1` · `[1]` · `["linux", 2]` · `["bad name"]` · `[""]` · `[["linux"]]` · 테이블 (8건) | ConfigError 에 `preset 'gate'` + `pools` |
| 8 | `preset_json` | `pool == "linux"` · `pools == ["default", "linux"]`(list) · 기본은 `"default"` 와 `pools == []`(null 아님) |

### 3.2 `tests/test_cli_m5b.py`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | `rcm run lin --no-wait` · `rcm run ok --no-wait` | linux / default 풀에 들어간다(status 의 그 풀 큐에 있고 행 `pool` 키도 같다) · `rcm jobs --json` 행의 `pool` |
| 2 | `rcm run lin --no-wait --pool default` (`Client.post_json` 기록) | `/jobs` 본문에 `pool: "default"` 한 번 · 잡은 default 풀 · `--pool linux --no-join` 도 된다(자기 풀은 `pools` 에 없어도 허용) |
| 3 | `rcm run ok --pool nope` / `rcm run lin --pool nope` | exit 2 · stderr 에 `pool` 과 허용 풀(`default` / `linux` + `default`) · stdout 비어 있음 · 어느 풀에도 잡 없음. 클라이언트가 `presets[].pools` 로 걸러도, 서버 400 을 2 로 바꿔도 된다 |
| 4 | ok(default) + lin(linux) → `jobs --json` / `--pool linux --json` / `--pool default --json` / `--pool linux`(텍스트) | 기본은 두 풀 다, 행마다 `pool` / linux 만 / default 만 / 텍스트도 걸러진다 |
| 5 | ok 만 → `jobs` / lin 추가 → `jobs` / `jobs --pool linux` | `pool …` 헤더 줄 없음 / `pool default` 와 `pool linux` 헤더, 각 잡은 자기 헤더 아래(default 먼저) / 풀 하나로 거르면 헤더 다시 없음 |
| 6 | ok 만 → `top` / lin 추가 → `top` / `top --json` | `(pool ` 없음 + `queue — 1 jobs · 0 running · 1 waiting` / `queue — 1 (pool linux · no workers)` 가 있고 lin 행이 그 아래, ok 행은 그 위, 기본 풀 절은 그대로 / `pools` 에 `default` · `linux`, linux 는 `lanes 0` · 큐 `[lin]` · `reason worker_down`, default 큐 `[ok]` |
| 7 | ok · bad(default) · lin(linux) → `eta lin --json` / `eta ok --json` / `eta lin --pool default --json` / `eta lin` / `eta ok --pool nope` | `job.pool == "linux"` · position 2 · ahead 1(있으면) / default · 3 / default · 3 / `2nd in line` / exit 2 + `pool` |
| 8 | `top --json`(토큰 없이) 의 `presets[]` | `lin`: `pool == "linux"` · `pools == ["default"]` · `ok`: `pool == "default"` · `pools == []` |
| 9 | live: `run lin --no-wait` 뒤 1초 | 여전히 `queued`(로컬 워커는 default 만) · `top` 에 `(pool linux · no workers)` · 끝에 cancel |
| 10 | live: `run ok`(wait 포함) → `jobs --json` | 완료(recent) 행도 `pool == "default"` |

### 3.3 `tests/test_render_m5b.py`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | 풀 하나(`doc()`) | `render(...) == GOLDEN`(오늘 출력 그대로) · `(pool ` 없음 |
| 2 | `pools: []` | `queue — unavailable` · 큐 행·풀 헤더 없음(오늘과 같다 — 회귀) |
| 3 | `render_pool` | import 된다 · 첫 매개변수 이름 `pool` · `render_pool(pool, tz=UTC, now=NOW)` 결과(str 또는 list[str])에 `queue — 2 jobs · 1 running · 1 waiting` · `#412` · `#413` · `host — no sample yet` |
| 4 | 풀 둘(linux: lanes 0 · hosts [] · #512 · #513 worker_down) | 네 잡 다 있음 · `queue — 2 (pool linux · no workers)` · `queue — ` 줄 정확히 2개 · `#413` < `(pool linux` < `#512` 순서 |
| 5 | 같은 문서 | #512 · #513 줄에 `eta —`, 그 다음 이유 줄에 `worker down` |
| 6 | linux: lanes 1 · #511 running + #512 · #513 | `queue — 3 (pool linux)` · `no workers` 없음 |
| 7 | 풀 하나 vs 둘 | `default_section(two) == default_section(one)` · 그것이 GOLDEN 의 둘째 줄부터와 같다 |
| 8 | 머리 집계 | lanes 1 · running 문서: `queue` 앞 머리에 `2 running · 3 waiting` · 기본 풀 절의 `queue — 2 jobs · 1 running · 1 waiting` 그대로 / lanes 0 문서: `1 running · 3 waiting` / 풀 하나면 머리에 `running ·` 없음 |
| 9 | 풀 둘 | `host` 로 시작하는 줄 2개, 둘째는 linux 헤더 뒤 |

### 3.4 `tests/web/pools.test.js`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | 계약 | `rcm.poolSummary` · `rcm.poolHeader` 가 함수 |
| 2 | `poolSummary(main.pools)` / 풀 둘(+1 queued) / cancelling / empty | `{2, 3, 1}` / `{2, 4, 2}` / cancelling 은 running · uploading 은 waiting / `{0, 0, 1}` |
| 3 | errors 픽스처(queue null) / 첫 풀 멀쩡 + 둘째 풀 queue null | running·waiting `null`(0 아님) · pools 1 / 2 |
| 4 | `poolSummary(null/undefined)` · 입력 불변 | `{null, null, 0}` · 던지지 않음 · 입력 JSON 그대로 |
| 5 | `poolHeader(main.pools[0])` · single-lane | `""` |
| 6 | `{name: "linux", lanes: 2, hosts: [...]}` / `{name: "linux", lanes: 0, hosts: []}` / lanes 없음 / `"a<b"` / null | `"pool linux"` / `"pool linux · no workers"` / `pool linux` 로 시작하되 `no workers` 없음 / `"pool a<b"`(평문) / `""` |
| 7 | `notMoving` 풀 둘(linux 에 #521 worker_down) | `list` · `[[521, worker_down], [413, blocked_by_group]]` · 문구 `no worker` |
| 8 | 첫 풀 ok + 둘째 풀 worker_down / 둘째 풀 queue null / 둘째 풀 빈 큐 | `list [521]` / `unknown` / 풀 하나와 같은 답 |
| 9 | `yourJobs` — linux 에 alice 잡 #522 / 남의 linux 잡 / 첫 풀엔 내 잡 없고 linux 에만 | `lines + more == 3` / 2 / `list [522]`(none 아님) |
| 10 | 회귀 | main: `notMoving [413]` · `yourJobs [412, 414]` · `queueHeader` 문자열 · `hostPressure fine` · `sortQueue` / errors·empty 픽스처 / 풀 하나인 픽스처 4개에서 `poolSummary` 의 running·waiting 이 `queueHeader` 문구와 일치 |

### 3.5 `tests/test_docs_m5b.py`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | README 「Session commands」 표의 `rcm run` · `rcm eta` · `rcm jobs` 행 (3건) | 각 행에 `--pool` |
| 2 | 「Presets and step markers」 절 | `pool =` · `pools = [` · `"default"` |
| 3 | `--pool` 을 말하는 문단 | `pools` 와 `preset` 이 같은 문단에(세션은 프리셋이 허용한 풀 안에서 고른다) |
| 4 | `examples/server.toml` | `pool = "…"` · `pools = [` 가 줄 머리 키로(주석 허용) |
| 5 | CHANGELOG `[Unreleased]` (2건 + 1) | `pool`/`pools` · `--pool` · `pool` 을 말하는 항목에 `presets[].pool` \| `queue[].pool` \| `pools[]`(추가 키) |
| 6 | 경로 | README · CHANGELOG · server.toml 존재(초록) |

## 4. 지금 빨간 것 · 우연히 초록인 것 · 구현자가 할 일

- **설정**: `_PRESET_KEYS` 에 `pool` · `pools`, `Preset.pool: str = "default"` · `Preset.pools: tuple[str, ...] = ()`(모델), `parse_preset` 에서
  `_NAME_RE` 검증(값은 준 그대로 — 정규화 없음), `preset_json` 에 두 키(`pools` 는 list, 비면 `[]`), `client.preset_from_json` 도 두 키를
  읽어야 `rcm run --pool` 이 서버에 보내기 전에 거를 수 있다(안 걸러도 서버 400 → 2 면 된다). 허용 판정은 `name == preset.pool or name in
  preset.pools`(서버 담당의 tests/test_server_m5b.py 와 같은 규칙).
  **우연히 초록**: 3.1 의 6 · 7(모르는 키 오류가 이미 프리셋 이름과 `pool`/`pools` 를 담는다) — 구현 뒤에도 초록이어야 하니 그대로 둔다.
- **CLI**: `run` · `eta` 에 `--pool NAME`(본문 `pool`), `jobs` 에 `--pool NAME`(필터) + 텍스트는 풀이 둘 이상일 때만 `pool <name>` 헤더 줄로 묶기,
  `cmd_eta`/`cmd_jobs` 의 `pools[0]` → 순회, `rcm eta --json` 의 `job.pool`. `rcm jobs --json` 의 큐·recent 행에 `pool`(서버 `queue_row_json`/
  `recent_json` 이 싣거나 CLI 가 풀 이름을 붙이거나).
- **렌더**: `render` 가 `pools` 를 순회하고 `render_pool(pool, tz, now)` 로 풀 하나(큐 · recent · medians · host)를 그린다. 기본 풀은 오늘
  그대로, 다른 풀은 `queue — N (pool <name>[ · no workers])`(lanes 0 · hosts 비면 `no workers`) 헤더. 워커 없는 풀의 대기 행 이유는
  `worker down`. 풀이 둘 이상이면 머리(`queue` 앞)에 전체 `R running · W waiting`. 풀 하나면 머리에 아무것도 덧붙이지 않는다(GOLDEN).
- **웹**: `poolSummary(pools)` · `poolHeader(pool)` 을 `rcm` 에 export, `notMoving` · `yourJobs` 가 `status.pools[*].queue` 를 전부 본다
  (어느 풀이든 queue null 이면 `unknown`). `renderQueue`/`renderHost`/`renderRecent` 는 풀마다 블록(`poolHeader` 가 `""` 이면 헤더 생략) —
  DOM 쪽은 여기서 검사하지 않는다(test_web_browser 몫).
- **문서**: README(Session commands 표 세 행 + Presets 절의 `pool = "…"` / `pools = [...]` 예시와 기본 `"default"` + `--pool` 설명 문단) ·
  `examples/server.toml`(`pool`/`pools` 주석 예시) · CHANGELOG `[Unreleased]` 에 pools 항목(`--pool` · `presets[].pool/pools` · `queue[].pool` ·
  `pools[]` 다중화가 추가 키라는 것).

## 5. 가정 (틀리면 테스트의 그 줄만 고치면 된다)

1. **풀 이름 규칙**은 프리셋·토큰·저장소와 같은 `_NAME_RE`(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`). 그래서 `""` · `"bad name"` · `"-linux"` ·
   65자는 오류.
2. **`Preset.pools` 는 추가로 허용하는 풀**(기본 `()`, 준 그대로 파일 순서). 자기 `pool` 은 규칙으로 언제나 허용된다(`name == pool or name in
   pools`). 처음엔 「`pools` 에 자기 풀을 넣는 정규화」로 썼다가, 같은 워크트리에 병렬로 들어온 서버 담당의 `tests/test_server_m5b.py`
   (`plain.pools == ()` · `strict.pools == ()`) · `tests/test_pools.py` 와 어긋나서 그쪽 해석에 맞췄다 — 두 묶음이 서로 다른 답을 요구하면 구현
   뒤 한쪽이 빨갛게 남기 때문이다.
3. `pools = []`(빈 목록) · 중복 항목(`["linux", "linux"]`) 은 단정하지 않았다(§6-1).
4. **웹 대상 함수는 새로 만든다**: `rcm.poolSummary(pools)`(status 가 아니라 `pools` 배열을 받는다 — 시그니처는 과제 명세대로) ·
   `rcm.poolHeader(pool)`(풀 dict 하나 → 평문). 이름이 다르면 `describe("module contract")` 가 먼저 빨갛게 알려 준다.
   Not-moving 빌더는 기존 **`rcm.notMoving(status, me)`**, Your-jobs 는 **`rcm.yourJobs(status, me)`** 를 그대로 겨냥했다(exports 에서 확인).
   두 함수가 `status.pools` 전체를 스스로 순회한다고 본다(호출자가 이어 붙이는 게 아니라).
5. `poolHeader` 는 **기본 풀(`name == "default"`)이면 언제나 `""`** — 풀 둘일 때도 기본 풀 헤더는 없다(터미널의 「기본 풀 절 불변」과 같은 규칙).
   `lanes` 를 모르면 `no workers` 를 주장하지 않는다(fail-open 금지). 반환은 평문(HTML 이스케이프는 DOM 층).
6. `poolSummary` 의 `running` 은 running + cancelling, `waiting` 은 나머지(queued · uploading) — `queueHeader` 와 같은 셈. 어느 풀의 queue 가
   null 이면 running/waiting 은 null(부분 합계를 내지 않는다). `pools` 가 배열이 아니면 `{null, null, 0}`.
7. **`rcm top` · `render`**: 다른 풀의 헤더는 `queue — N (pool <name>[ · no workers])` — N 은 그 풀의 잡 수. 기본 풀 절은 오늘 그대로(`queue — N jobs
   · R running · W waiting`, `(pool default)` 없음). 풀 순서는 문서의 `pools[]` 순서(기본 풀 먼저). 전체 집계는 `queue` 앞 어느 줄이든 된다.
8. **워커 없는 풀의 대기 행 이유는 `worker down`**(과제 명세). 오늘 `_reason_text` 의 `worker_down` 은 `no worker` 인데(웹 `reasonText` 도 —
   `tests/web/reason.test.js` 가 잠근다) 터미널만 바뀌는 셈이다(§6-2).
9. **`rcm top` 은 잡이 없는 비기본 풀을 그리지 않는다**(과제 명세 「no pool header when only default has jobs」). 서버 `status()` 가 빈 linux 풀을
   아예 안 내든, 렌더가 「빈 큐 + 워커 없음」 풀을 건너뛰든 둘 다 통과한다(§6-3). 렌더 테스트는 이 경우를 단정하지 않았다. 서버 담당의
   `test_status_with_only_default_pool_jobs_is_a_single_pool`(pools 가 `["default"]` 하나) 이 앞쪽 경로를 잠근다 — 서로 맞는다.
10. `rcm jobs` 텍스트의 풀 헤더는 **`pool <name>` 로 시작하는 줄**(뒤에 다른 조각이 붙어도 됨). 풀이 하나만 보이면(잡이 한 풀에만 있거나 `--pool`
    로 걸렀거나) 헤더 없음.
11. `rcm eta` 의 `ahead` 는 있으면 값을 보고 없으면 넘어간다(`position` 이 주 검사). `--pool` 은 `/api/eta` 본문에 `pool` 로 간다고 본다.
12. `rcm jobs --json` 의 **recent 행도 `pool`** 을 싣는다(3.2 의 10). 큐 행만 명세에 있다면 그 테스트 하나만 빼면 된다.
13. `rcm run --pool nope` 의 오류 문구는 허용 풀 이름을 담는다(`ok` → `default`, `lin` → `linux` 와 `default`). 클라이언트가 걸러도(권장 — 서버에
    보내기 전에 2) 서버 400 이어도 된다. `Client.submit` 호출 여부는 단정하지 않았다.
14. `render_pool` 의 시그니처는 `render_pool(pool, tz=…, now=…)`, 반환은 `str` 또는 `list[str]`(둘 다 받는다). 첫 매개변수 이름이 `pool` 인 것만
    `inspect.signature` 로 잠갔다.
15. 로컬 워커(같은 프로세스)는 풀 `default` 만 claim 한다 — `pool_live` 에서 linux 잡이 1초 뒤에도 queued(3.2 의 9). 이건 서버 쪽 규칙이지만 CLI 로
    관찰되는 완료 기준이라 넣었다.
16. `examples/server.toml` 의 `pool`/`pools` 예시는 과제 문서 목록엔 없지만 M5a 의 `priority` 예시와 같은 관례라 넣었다(3.5 의 4). 원치 않으면 그
    테스트 하나만 빼면 된다.

## 6. 명세에서 갸우뚱한 것 (오너/구현자 결정)

1. **`pools` 의 기본값과 빈 목록**: 명세는 `pools = ["default", "linux"]` 만 보여 준다. 기본(키 없음)은 「자기 풀만」으로 봤고(가정 2), `pools = []`
   은 「자기 풀만」인지 오류인지 정하지 않아 테스트에 없다. 중복 항목도 마찬가지.
2. **`worker down` vs `no worker`**: 과제 명세는 터미널 대기 행에 `worker down` 을 요구하지만 오늘 터미널·웹 둘 다 `no worker` 다(웹은 기존 테스트가
   잠근다). 터미널만 바꾸면 두 화면의 문구가 갈린다 — 하나로 맞출지(웹도 `worker down` 으로 바꾸면 `reason.test.js` · `summary.test.js` 두 줄 수정)
   결정이 필요하다. 테스트는 과제 명세를 따랐다.
3. **잡 없는 비기본 풀**: `rcm top` 에서 「default 에만 잡이 있으면 풀 헤더 없음」 — 그런데 M5b-2 에서 워커가 등록된 빈 풀(`lanes ≥ 1`, 잡 0)은
   보여야 자연스럽다(「이 풀은 살아 있다」). 지금 테스트는 「lanes 0 · 잡 0 인 풀은 안 보인다」 만 요구하는 셈이니 M5b-2/4 에서 「워커 있는 빈 풀은
   `queue — empty (pool linux)`」 로 확장해도 충돌하지 않는다.
4. **머리줄 전체 집계의 위치**: 「summary head line counts all pools」 — `━━━` 줄에 붙일지 둘째 줄로 둘지 정하지 않았다. 테스트는 `queue` 앞이면
   된다. 풀 하나일 땐 덧붙이면 안 된다(GOLDEN · 「풀 하나면 화면 그대로」).
5. **`poolHeader(pool)` 의 단일 인자**: 「single default pool → `""`」 인데 인자가 풀 하나라 「single」 을 알 수 없다. 기본 풀은 언제나 `""` 로 잡았다
   (가정 5). 기본 풀이 lanes 0 이 되는 경우(로컬 워커 없이 원격 워커만)의 문구는 정하지 않았다.
6. **`rcm eta --job ID`**: 잡 id 로 물을 때 `cmd_eta` 가 `pools[0]` 만 뒤지면 linux 잡은 「not found → GET /jobs/{id}」 경로로 빠진다. 순회로 바꾸는 게
   맞지만 과제 목록에 없어 테스트하지 않았다(구현자가 같이 고치길 권한다).
7. **`recent` · `medians` 의 풀별 분리**: 명세는 「중앙값은 풀별」이라 하지만 이번 PR 범위(M5b-1)에서 `recent`/`medians` 가 풀별로 갈리는지(잡의
   `pool` 컬럼으로 나누는지)는 서버 담당의 결정 — 렌더 테스트는 풀마다 `recent`/`medians`/`host` 블록이 **있다** 는 것만 본다(host 줄 수로).
