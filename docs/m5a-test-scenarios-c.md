# M5a 테스트 시나리오 — 담당 C (CLI · 설정 · 렌더 · 웹 칩 · 문서) (2026-09-06)

> `docs/m5-workplan.md` 「순서」 2 의 **C**(CLI·웹·설정: `--priority` · `rcm bump` · 칩 · `[[notify]]` 검증 · README).
> `src/` · `scripts/` · `.github/` · README · 기존 테스트는 건드리지 않았다(`tests/test_config.py` 는 **끝에 M5 블록만 추가**).
> 구현 전이라 **빨간 것이 정상**이다. 같은 워크트리에 A/B 가 병렬로 들어오고 있다(`core/notify.py` · `core/manifest.py` ·
> `tests/test_priority.py` · `tests/test_notify_rules.py` · `tests/test_store_m5.py` · `tests/test_manifest.py` ·
> `tests/test_retention_blobs.py`) — 그쪽 파일은 읽기만 했고 겹치는 케이스는 두지 않았다.

## 1. 파일과 개수

| 파일 | 대상 | 수집 | 작성 시점 |
|---|---|---|---|
| `tests/test_config.py` (M5 블록 추가) | 프리셋 `priority` · `[server] snapshot_cache*` · `[[notify]]` 검증 · `cfg.notify` | 33 (함수 22 · parametrize 11) | 26 빨강 · 7 초록(§4 「우연히 초록」) — 기존 44 는 전부 초록 |
| `tests/test_cli_m5.py` (신규) | `rcm run --priority` · `--no-cache` · `rcm bump` · `rcm top` `↑`/`↓` · `rcm jobs --json` · `rcm eta --priority` · 캐시 stderr · 헤더 cache/notify | 17 | 15 빨강 · 1 error(`prio_srv` 픽스처가 `priority` 키에서 ConfigError) · 1 초록 |
| `tests/test_render_m5.py` (신규) | `render_queue_row` 의 `↑`/`↓` · `render` 헤더의 `cache N blobs · X MB` · `notify failures N` | 14 | 9 빨강 · 5 초록(옛 문서는 지금과 같아야 한다는 회귀 가드) |
| `tests/web/priority.test.js` (신규) | `rcm.priorityChip` · `rcm.cacheText` · 「우선순위는 이유가 아니다」 불변 | 21 | 14 빨강 · 7 초록(불변 가드) |
| `tests/test_docs_m5.py` (신규) | README · `examples/server.toml` · CHANGELOG 문면 | 25 | 25 빨강 |

실행: `ruff check` · `ruff format --check` 통과(CJK 2칸 기준 100). `node --check tests/web/priority.test.js` 문법 오류 없음.
pytest·ruff 는 이 워크트리에 venv 가 없어 스크래치 venv(python3.11 · pytest 8 · ruff 최신)로 돌렸다. 합계 110건
(pytest 89 · node 21) 중 90 빨강(pytest 76 · node 14) · 20 초록(pytest 13 · node 7).

## 2. 공용 규칙 · 도우미

- `tests/test_cli_m5.py` 의 `run(capsys, argv)` 는 test_cli_m4 와 같이 **SystemExit 을 코드로 바꾼다** — `--priority urgent`
  (argparse choices) 와 아직 없는 `bump` 하위 명령이 `parse_args` 에서 `SystemExit(2)` 로 나온다.
- 서버는 `test_server.Server`. `cache_srv` 는 `max_snapshot_bytes=50_000_000`(난수 300 KB 트리가 기본 10 KB 상한에 걸린다).
  `prio_srv` 는 test_server_m3.make_server 의 요령으로 `cfg.presets` 에 `priority = "high"` 인 `urgent` 프리셋을 끼운다.
- `submit(capsys, tree, preset, *extra)` — `rcm run <preset> --no-wait --dir <tree> …` 로 제출·업로드까지 끝내고 job id 를 준다
  (workers=False 라 queued 로 머문다). 같은 트리에 다른 프리셋을 쓰면 합류하지 않는다(join key 가 다르다).
- `make_tree(root, small=…)` — `random.Random(7).randbytes(300_000)` 의 `big.bin` + 작은 `small.txt`. 두 번째 업로드에서
  `big.bin` 은 캐시에 있고 `small.txt` 만 바뀐다 → `cache N%` 의 N ≥ 50.
- `tests/test_config.py` 의 `notify_toml(**fields)` / `notify_rule(tmp_path, **fields)` — `[[notify]]` 하나를 TOML 로 써서
  로드하고 `cfg.notify[0]` 을 준다. `msg(e, tmp_path)` 는 오류 문구에서 tmp 경로를 뺀다(경로에 테스트 이름이 들어 있어
  `on` · `argv` · `url` 같은 단어 검사가 우연히 통과할 수 있다).
- `tests/test_render_m5.py` 의 `doc(queue=None, **server_extra)` — `status_json(model(...))` 에 `server` 추가 키를 얹는다.
  `queued_row(priority)` 는 #413(queued) 행 dict, `priority=None` 이면 키를 아예 안 넣는다(옛 서버).

## 3. 시나리오

### 3.1 `tests/test_config.py` — 프리셋 `priority` (M5a-1)

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | `parse_preset` 에 priority 없음 · `Preset(...)` 기본값 | 둘 다 `.priority == 0` |
| 2 | `"high"` · `"normal"` · `"low"` (3건) | 1 · 0 · −1 |
| 3 | `"urgent"` · `"HIGH"` · `1` · `true` (4건) | `ConfigError` 에 `preset 'gate'` 와 `priority` — 세 단어만, 대소문자·정수 금지 |
| 4 | 파일에서 `gate` 는 high, `deploy` 는 없음 | `cfg.preset("gate").priority == 1` · `deploy` 는 0 |

### 3.2 `tests/test_config.py` — `[server] snapshot_cache*` (M5a-2 「저장 · 정리」)

| # | 시나리오 | 기대 |
|---|---|---|
| 5 | 기본값 | `snapshot_cache is True` · `_days == 30` · `_max_bytes == 4 * 2**30` · `_scope == "global"` |
| 6 | 파일 | `false` · 7 · 1048576 · `"token"` 이 그대로 |
| 7 | env `RCM_SERVER_SNAPSHOT_CACHE=false` · `_DAYS=3` · `_SCOPE=token` · `_DAYS=soon` | 덮어쓴다 · `soon` 은 `[server] snapshot_cache_days` 를 담은 ConfigError |
| 8 | `_days = 0` / `1` | 0 은 ConfigError(키 이름) · 1 은 허용 |
| 9 | `_max_bytes = 1048575` / `1048576` | 1 MiB 미만은 ConfigError · 1 MiB 는 허용 |
| 10 | `_scope = "team"` · `"Global"` · `""` (3건) | ConfigError 에 키 이름 |

### 3.3 `tests/test_config.py` — `[[notify]]` (M5a-3)

| # | 시나리오 | 기대 |
|---|---|---|
| 11 | 절 없음 | `cfg.notify == ()` |
| 12 | `name` + `argv` 만 | `core.notify.NotifyRule` 인스턴스 · `argv` 튜플 · `url` 없음 · `set(on)` 은 종료 상태 5개 · `presets` 비어 있음(None/빈) · `timeout_seconds == 30` |
| 13 | `url = "https://…"` + `timeout_seconds = 5` · `url = "http://127.0.0.1:9/hook"` | url 그대로 · argv 없음 · 5 · 로컬 http 허용 |
| 14 | argv 와 url 둘 다 / 둘 다 없음 | ConfigError 에 규칙 이름 + `argv` + `url` |
| 15 | `argv = []` · `argv = "bash notify.sh"`(셸 문자열) · `["bash", 1]` | ConfigError 에 규칙 이름 + `argv` |
| 16 | url `ftp://…` · `hooks.example/x` · `//h/x` · `""` (4건) | ConfigError 에 규칙 이름 + `url` |
| 17 | `on = ["failed","timed_out","lost"]` · `on = ["failed","running"]` · `on = []` | 부분집합 그대로 · `running` 은 ConfigError(규칙 이름 · `on` · `running`) · 빈 목록도 ConfigError |
| 18 | `presets = ["gate","deploy"]`(둘 다 존재) · `["gate","nope"]` | 집합으로 같다 · `'nope'` 와 규칙 이름을 담은 ConfigError |
| 19 | `timeout_seconds = 0` / `-1` | ConfigError(규칙 이름 · `timeout_seconds`) |
| 20 | 같은 이름 둘 | ConfigError 에 `duplicate`(대소문자 무관) + 이름 |
| 21 | `name = "bad name"` · name 없음 | ConfigError 에 `bad name` / `name` |
| 22 | 모르는 키 `shell = "curl …"` | ConfigError 에 규칙 이름 + `shell`(프리셋과 같은 「등록된 명령만」) |
| 23 | 규칙 둘(b, a) | `[r.name for r in cfg.notify] == ["b", "a"]` — 파일 순서 |

### 3.4 `tests/test_cli_m5.py`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | `rcm run ok --no-wait` · `--priority normal` | `/api/status` 와 `rcm jobs --json` 행의 `priority == 0` |
| 2 | `--priority low` (alice) | −1 — 낮추는 건 누구나 |
| 3 | alice 가 normal 프리셋에 `--priority high` | **exit 2** · stderr 에 `admin` · stdout 비어 있음 · 큐에 잡 없음(403). admin 토큰이면 1 |
| 4 | `prio_srv` 의 `urgent`(high 프리셋)에 alice 가 `--priority high` / `--priority low --no-join` | 1 / −1 |
| 5 | `--priority urgent` | exit 2 · stderr 에 `priority` · `Client.submit` 안 불림 · 큐 비어 있음 |
| 6 | alice normal(ok) · alice low(slow) · admin high(bad) → `rcm top` · `rcm jobs --json` | high 행은 `↑` 가 `#id` 앞 · low 행은 `↓` · normal 행엔 화살표 없음(셋 다 queued 라 업로드 글리프 없음) · position high 1 → normal 2 → low 3 · priority 1/0/−1 |
| 7 | admin `rcm bump <first> --priority high` (first 는 admin 의 high 잡보다 먼저 들어온 normal) | exit 0 · stdout JSON 에 `job_id`·`priority: 1` · 이후 first 가 position 1, high 가 2(같은 high 는 id 순) · `--priority low` 로 다시 낮추면 −1, position 2 |
| 8 | alice `rcm bump` / 토큰 없이 | exit 2 · stderr 에 `admin` / `token` · priority 그대로 |
| 9 | running 잡에 admin `bump` (live · slow) | exit 2 · stderr 에 `bump` + (`409` \| `not waiting` \| `running`) · stdout 비어 있음. cancel 의 409 처럼 `bump failed: <서버 문구>` 꼴을 기대. 끝에 cancel 로 정리 |
| 10 | `bump 999` · `bump 1 --priority urgent` | 둘 다 exit 2 · stderr 에 `job` / `priority` |
| 11 | normal 잡 둘 뒤 `rcm eta ok` / `--priority high` / `--priority low` → admin 이 first 를 high 로 bump → `--priority high --json` / `--priority normal` / `--priority urgent` | `3rd in line` / `1st in line` + `0 ahead` / `3rd` → `position == 2`(ahead 1) / `3rd` / exit 2 |
| 12 | `cache_srv` 에 난수 트리 업로드 → `small.txt` 만 바꿔 다시 `rcm run` | 두 번째 stderr 에 `cache N%`(N ≥ 50) 와 `uploading` · `/jobs/{id}.source.cached_bytes > 0` · `uploaded_bytes < bytes` · 상태 queued |
| 13 | `--no-cache --no-join` (같은 트리) | exit 0 · stderr 에 `uploading` 있고 `cache` 없음 · `cached_bytes` 없음/0 · queued |
| 14 | `snapshot_cache=False` 서버에 두 번째 업로드 | stderr 에 `cache` 없음(서버가 `cache: true` 를 안 주면 전체 tar) |
| 15 | `rcm top --json` 새 서버 / 업로드 뒤 / `rcm top` | `server.snapshot_cache.blobs == 0 and bytes == 0` / `blobs ≥ 1 · bytes ≥ 300000` / `queue` 앞 머리 부분에 `cache \d+ blobs · \d+(\.\d+)? MB` |
| 16 | `rcm top --json` · `rcm top` (토큰 없이) | `server.notify_failures == 0` · 텍스트에 `notify failures` 없음 |
| 17 | live 에 admin `rcm run ok --priority high`(wait 포함) | exit 0 · JSON `state == "succeeded"` · `wait_exit_code == 0` · 30초 안 |

### 3.5 `tests/test_render_m5.py`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | queued #413 · `priority: 1` | 첫 줄에 `↑` 가 `#413` 앞 · `↑` 는 정확히 1개 · `↓` 없음 |
| 2 | `priority: -1` | `↓` 가 `#413` 앞 · 1개 · `↑` 없음 |
| 3 | `priority: 0` · 키 없음 (2건) | 화살표 없음 |
| 4 | high 행에서 `↑` 를 빼면 | normal 행과 토큰이 같다(화살표는 접두일 뿐) |
| 5 | running #412 · `priority: 1` | `↑` 가 `#412` 앞 · `▶` 유지 |
| 6 | `render` 전체 — #412 low · #413 high | 각 행에 화살표 · `1 running · 1 waiting` 집계 불변 |
| 7 | `server.snapshot_cache = {blobs: 12, bytes: 48_213_344}` | `queue` 앞 머리에 `cache 12 blobs · 48 MB`(소수 허용) |
| 8 | `{blobs: 0, bytes: 0}` | `cache 0 blobs · 0 MB` — 0 은 진짜 0, 숨기지 않는다 |
| 9 | 키 없음 · `null` | 머리에 `cache` 없음 |
| 10 | `{blobs: null, bytes: null}` | `cache` 는 있고 `—` 로, `cache 0 blobs` 는 아님 |
| 11 | `notify_failures = 3` / `0` / 없음 | `notify failures 3` 있음 / 없음 / 없음 |
| 12 | `notify_failures = 2` | 그 문구는 한 줄에만, 머리(━━━ 줄들) 또는 `host` 줄에, 큐 행(`#412` · `#413`)엔 없음 |
| 13 | `notify_failures = 5` | `  error ·` 줄이 생기지 않는다(`last_error` 를 건드리지 않는다) |

### 3.6 `tests/web/priority.test.js`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | 계약 | `rcm.priorityChip` · `rcm.cacheText` 가 함수 |
| 2 | `priorityChip({priority: 1})` / `-1` | `class="… prio high …"` + 텍스트 `high` / `prio low` + `low` · 반대 단어 없음 |
| 3 | `0` · 없음 · `null` · `undefined` · `"<b>x</b>"` · row 가 `null`/`undefined` | 전부 `""` (빈 문자열) — 던지지 않는다 · 문자열은 절대 HTML 로 새지 않는다 |
| 4 | main 픽스처 행 전부 | `""` (아직 priority 키 없음) |
| 5 | `cacheText({snapshot_cache: {blobs: 12, bytes: 48213344}})` | 문자열에 `cache 12 blobs · 48 MB` |
| 6 | `{blobs: 0, bytes: 0}` | `cache 0 blobs` |
| 7 | 키 없음 · `null` · server 가 `null`/`undefined` | `null` |
| 8 | `{blobs: null, bytes: null}` | `cache` 로 시작 · `—` 포함 · `0 blobs`/`0 MB` 아님 |
| 9 | 4 GiB | `4.3 GB` 또는 `4295 MB` · 원시 바이트 숫자 없음 |
| 10 | `notMoving` — 행에 priority 를 붙여도 | kind · (jobId, reason) 목록이 같다 |
| 11 | low 잡이 `waiting_for_lane` 로 high 뒤에 | `ok` — 기다리는 건 이유가 아니다 |
| 12 | high 잡이 `blocked_by_group` | 여전히 1건, 문구 같음, `high`/`priority` 단어 없음 |
| 13 | `reasonText` | priority 1/−1 이어도 문구 같음 |
| 14 | `sortQueue` — position 1(normal) · 2(high) · 3(low) | position 순 `[420, 421, 422]` — UI 는 서버 순번을 믿는다 |
| 15 | `yourJobs` | 문구 같음 |
| 16 | `ACTIONABLE` | prio/starv/high/low 가 든 이유 없음 |

### 3.7 `tests/test_docs_m5.py`

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | README 에 (6건) | `--priority` · `rcm bump` · `snapshot_cache` · `--no-cache` · `[[notify]]` · `RCM_STATE` |
| 2 | `--priority` 문단 | `high` · `normal` · `low` 셋 다 |
| 3 | 기아 안내 | `priority` 를 말하는 어떤 문단에 `high` · `normal` · `wait…`(waiting/waits) 가 함께 |
| 4 | `rcm bump` 문단 | `admin` |
| 5 | `--no-cache` 문단 | `cache` + (`blob` \| `sha256` \| `changed`) — 왜 빠른지 설명 |
| 6 | `[[notify]]`/`RCM_STATE` 문단들 | `RCM_JOB_ID` · `RCM_STATE` · `argv` · `url` |
| 7 | `examples/server.toml` | 주석 `# [[notify]]` + `# name =` + `# argv =`/`# url =` + `# on = [` · 살아 있는 `[[notify]]` 는 없음 |
| 8 | (4건) | `snapshot_cache` · `_days` · `_max_bytes` · `_scope` 가 줄 머리 키로(주석 허용) |
| 9 | 프리셋 `priority = "…"` 예시(주석 허용) | 있음 |
| 10 | 예시 로드(`check_tools=False`) | `ok` 프리셋 · `snapshot_cache` bool · `notify == ()` |
| 11 | 주석 `[[notify]]` 블록의 `#` 를 벗겨 붙여 로드 | 규칙 1개, name 과 argv/url 있음 — **`presets = [...]` 가 주석 처리된 프리셋을 가리키면 빨갛다** |
| 12 | CHANGELOG `[Unreleased]` (3건 + 2) | `priorit` · `cache` · `notif` · `### Added` 아래 항목 · `schema`/additive 언급(추가 키만, 버전 그대로) |

## 4. 지금 빨간 것 · 우연히 초록인 것 · 구현자가 할 일

- **설정**: `parse_preset` 의 `_PRESET_KEYS` 에 `priority`, `Preset.priority: int = 0`(모델), `ServerSection` 에
  `snapshot_cache*` 4개 + `_validate_server` 하한, `_TOP_KEYS` 에 `notify` + `[[notify]]` 파서(`core.notify.NotifyRule`
  로 — A 가 이미 `name · on: frozenset · presets: frozenset|None · argv: tuple|None · url · timeout_seconds` 로 만들어 뒀다).
  **우연히 초록**: 3(모르는 키 오류가 프리셋 이름과 `priority` 를 담는다) · 10(모르는 키 오류가 키 이름을 담는다) —
  구현 뒤에도 초록이어야 하니 그대로 둔다. 21 은 처음엔 tmp 경로의 테스트 이름(`…notify_name…`) 덕에 초록이었다 —
  `msg()` 로 경로를 빼자 빨개졌다(그래서 그 도우미가 있다).
- **CLI**: `run` 에 `--priority {high,normal,low}`(기본 normal) · `--no-cache`, 새 하위 명령 `bump JOB --priority …`
  (`POST /jobs/{id}/priority`, 오류는 cancel 처럼 `bump failed: <문구>` + `USAGE_EXIT if e.status`), `eta` 에 `--priority`
  (`/api/eta` 본문에 `priority`), `client.upload_cached` 의 진행 콜백이 `(cache N%)` 를 stderr 한 줄에 싣는다.
- **렌더**: `render_queue_row` 가 `row.get("priority")` 로 `↑ `/`↓ ` 접두, `render` 머리에
  `cache {blobs} blobs · {_mb(bytes)}`(키 있을 때만 · None 은 `—`), `notify_failures > 0` 일 때만 `notify failures N`.
- **웹**: `priorityChip(row)` · `cacheText(server)` 를 `rcm` 객체에 export 하고 `queueRowHtml` 의 chips 자리와
  `renderHeader` 의 footBase(또는 `renderEstimates` summary)에서 부른다. `style.css` 에 `.chip.prio.high/.low`.
- **문서**: README(Session commands 표의 `rcm run` 행 + 「Priority」·「Snapshot cache」·「Notifications」 문단) ·
  `examples/server.toml`(`snapshot_cache*` 4키, 프리셋 `priority` 주석, `# [[notify]]` 블록 — `presets` 는 살아 있는
  프리셋만) · CHANGELOG `[Unreleased] / ### Added` 세 항목 + 「schema_version 1 유지, 추가 키만」.

## 5. 가정

1. **웹 대상 함수는 새로 만든다.** app.js 에 행을 HTML 로 그리는 export 는 `sourceHtml` 뿐이고 `queueRowHtml` 은
   DOM 쪽(`state` 의존)이라 node 에서 부를 수 없다. 그래서 칩 하나를 만드는 가장 작은 순수 함수 **`rcm.priorityChip(row)`**
   (row 를 받는다 — 숫자가 아니라)와 푸터/Estimates 문구 **`rcm.cacheText(server)`** 를 계약으로 잡았다. 이름이 다르면
   테스트의 두 이름만 바꾸면 된다(`describe("module contract")` 가 먼저 빨갛게 알려 준다).
2. `priorityChip` 의 class 는 `prio high` / `prio low` 를 **포함**하면 된다(`chip prio high` 를 기대하지만 앞뒤에 다른
   class 가 있어도 통과). 텍스트는 `high`/`low` 그대로, normal 은 칩 없음(`""`).
3. `cacheText` 의 크기 단위는 `fmtBytes`(십진 · 0.5 GB 부터 GB 한 자리)를 기대하지만 `4295 MB` 도 받는다.
4. `rcm top` 의 화살표는 **잡 id 앞**이면 된다(행 맨 앞이든 position 뒤든). 업로드 상태 글리프가 이미 `↑` 라 테스트는
   **queued·running 행만** 쓴다. uploading + high 행에 `↑` 가 둘 찍히는 문제는 구현자가 정한다(§6-4).
5. `↑`/`↓` 는 `render_queue_row` 의 **첫 줄**에만 본다. 두 번째 줄(이유)엔 없어도 된다.
6. 헤더의 `cache …` 위치는 `queue` 절 앞 어느 줄이든 된다(━━━ 줄 끝이든 둘째 줄이든). `notify failures N` 도 머리 줄들
   또는 `host` 줄.
7. `rcm bump` 의 stdout JSON 은 `job_id` · `priority` 를 **포함**하면 된다(다른 키가 더 있어도 됨). 실패는 exit 2 +
   stderr 에 `bump`(cancel/pause 와 같은 `<명령> failed:` 꼴) — 409 문구는 `409` · `not waiting` · `running` 중 하나.
8. `rcm eta --json` 의 `ahead` 는 있으면 1 이어야 하고 없어도 된다(`position == 2` 가 주 검사).
9. `GET /jobs/{id}` 의 `priority` 는 있으면 보고 없으면 0 으로 본다(`queue[].priority` 만 명세에 있다). `rcm jobs --json`
   은 큐 행에 `priority` 가 있어야 한다(recent 행은 안 본다).
10. 캐시 stderr 의 `N%` 는 「캐시 히트 바이트 / 전체」로 보고 ≥ 50 만 요구한다(300 KB 난수 파일이 히트, 3 바이트 텍스트만
    바뀜 → 실제로는 99%). 첫 업로드(blob 없음)의 stderr 는 단정하지 않는다(`cache 0%` 든 없든).
11. `snapshot_cache = false` 서버의 `server.snapshot_cache` 가 `null` 인지 `{0, 0}` 인지는 단정하지 않는다 — 그 서버에
    두 번째 업로드 stderr 에 `cache` 가 없는 것만 본다.
12. `[[notify]] on = []` 은 오류로 잡았다(빈 목록 = 아무것도 안 보내는 규칙은 설정 실수다). `presets` 는 `frozenset` 이라
    집합으로 비교한다. 모르는 키는 프리셋처럼 오류.
13. `Preset.priority` 의 `1`/`true` 거절: 명세가 「`priority = "high"|"normal"|"low"`」로 문자열만 적었다. 정수를 받고 싶으면
    3 의 parametrize 에서 `1` 을 빼면 된다.
14. 문서 테스트의 「문단」은 빈 줄로 나눈 덩어리이고 표는 행마다 한 문단이다. 코드 블록 안에 빈 줄이 없으면 한 문단.

## 6. 명세에서 이상하거나 빠진 것

1. **`--priority` 기본값 충돌.** M5a-1 은 CLI 기본을 `normal` 로, 동시에 프리셋 `priority = "high"` 를 「기본값」으로 적었다.
   high 프리셋에 플래그 없이 제출하면 high 인가 normal 인가? 테스트는 이 경우를 **피했다**(4 는 항상 플래그를 준다).
   제안: CLI 는 플래그가 없으면 `priority` 를 보내지 않고(또는 null), 서버가 프리셋 기본을 쓴다 — README 에 한 줄.
2. **`rcm bump N` 의 `--priority` 가 필수인지** (없으면 high?) 명세에 없다. 테스트는 항상 준다.
3. **`presets[].priority` 가 `/api/status` 에 없다.** 세션이 403 을 미리 알 수 없고 `rcm presets` 도 못 보여 준다. 추가 키로
   넣기를 제안 — 그러면 `test_status_schema` 의 프리셋 키 집합 단정을 구현자가 같이 고쳐야 한다(추가 키라 스키마 v1 유지).
4. **`↑` 충돌.** uploading 글리프가 이미 `↑` 다. high + uploading 행은 `↑ ↑ uploading #N` 이 된다. 다른 접두(예 `⇧`)를
   쓰거나 우선순위 표시를 position 칸에 넣는 걸 검토. 테스트는 두 상태를 섞지 않았으니 어느 쪽이든 통과한다.
5. **예시 `[[notify]]` 의 `presets = ["gate", "deploy"]`** — `examples/server.toml` 의 `deploy` 는 주석이라 그대로 베끼면
   설정 오류다. 예시엔 살아 있는 프리셋만 적거나 `presets` 줄을 빼야 한다(3.7-11 이 잡는다).
6. **`--no-cache` 와 합류.** 같은 트리를 `--no-cache` 로 다시 올리려면 `--no-join` 도 줘야 새 잡이 된다(아니면 합류해서
   업로드 자체가 없다). README 의 `--no-cache` 안내에 한 줄 필요.
7. `server.snapshot_cache` 가 캐시 off 일 때 `null` 인지, `notify_failures` 가 `[[notify]]` 없을 때도 0 인지 — 추가 키의
   null 규칙을 PLAN 스키마 절에 적어야 한다(테스트는 후자를 0 으로 잠갔다: 「모른다」가 아니라 「없었다」이므로).
8. `rcm jobs --json` 의 **recent 행**에도 `priority` 를 실을지 명세에 없다(안 본다).
9. `rcm eta --priority` 의 `ahead` 정의(같은 우선순위 이상만? running 포함?)가 없다 — `position` 만 단정했다.
