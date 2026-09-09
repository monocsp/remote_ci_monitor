# M5h 상세 구현 계획 — 잠근 API · 파일별 변경 · 단계

> 설계(무엇을·왜)는 `docs/m5h-workplan.md`. 이 문서는 그것을 **구현**으로 옮긴 것이고,
> **테스트를 먼저 쓰는 에이전트가 읽는 계약**이다. 여기 적힌 이름 · JSON 키 · 영어 문구 ·
> 종료 코드 · 상한은 전부 계약이다 — 바꾸려면 **이 문서를 먼저 고치고** 코드와 테스트를 뒤에
> 맞춘다.
>
> 집안 규칙(변하지 않는다): 런타임 의존성 0 · 주석과 문서 문자열은 한국어, 식별자 · CLI 도움말 ·
> 영어 문서는 영어 · 순수 계층(`core/`)은 시계도 I/O 도 안 본다 · 모르는 값은 `null`(0 이 아니다) ·
> 스키마 v1 은 **키를 더하되 값을 안 바꾼다**.

## 0. 단계

브랜치 `fix/cli-ux`(이 워크트리), 커밋 다섯 개, `dev` 로 PR 하나. 단계 1 → 2 는 순서가 있고
(대장이 새 마커 위에 선다) 3 · 4 · 5 는 서로 독립이다.

| 단계 | 이름 | 건드리는 파일 | DB | 시나리오 역할 |
|---|---|---|---|---|
| 1 | 실패 스텝은 선언된 것만 | `core/progress.py` · `core/model.py` · `worker.py` · `remote_workers.py` · `store.py` · `core/status.py` · `core/render_text.py` · `core/notify.py` · `web/app.js` · `web/i18n.js` | **v11** | A(순수) · B(저장·서버) · C(표시) |
| 2 | 실패 대장과 최근 이력 | `store.py` · **`core/failures.py`(새)** · `server.py` · `config.py` · `core/render_text.py` · `cli.py` · `web/app.js` · `web/i18n.js` | **v12** | A · B · C |
| 3 | 404 가 길을 알려 준다 | `server.py` · `cli.py` · `examples/session/ci-gate.sh` | — | B · C |
| 4 | 목록이 코드를 말한다 | `core/render_text.py` · `cli.py` · `client.py` · `core/model.py` · `core/status.py` | — | C |
| 5 | 대기 전에 스냅샷을 놓는다 | `cli.py` | — | C |

---

## 1. 단계 1 — 실패 스텝은 선언된 것만

### 1.1 `core/progress.py` (순수)

**상수 추가**

```python
KIND_FAIL = "fail"
MARKER_KINDS = (KIND_STEPS, KIND_STEP, KIND_STEP_END, KIND_SUMMARY, KIND_FAIL)
MAX_FAIL_NAMES = 100          # 한 잡이 남길 수 있는 실패 이름 수
```

**`parse_marker(line)`** — `KIND_FAIL` 가지를 `KIND_STEP` 과 같은 규칙으로 더한다: 빈 값이면
`None`(마커 아님), 아니면 `value[:MAX_STEP_NAME]`(120자). 그 밖의 동작은 그대로.

**`progress_from_markers(...)`** — 세 곳이 바뀐다.

1. **실패 이름을 모은다.** 루프에서 `KIND_FAIL` 을 만나면 순서를 지켜 모으고 중복은 한 번만
   센다. `MAX_FAIL_NAMES` 를 넘으면 **버리고** `fail_truncated = True`.
2. **잡이 끝났을 때 열린 스텝의 마감**을 바꾼다.

   ```python
   # 지금: steps[-1].ok = exit_code == 0        ← 실패면 False 로 물들여 폴백을 만든다
   # M5h:
   if steps[-1].ok is None:
       steps[-1].ok = True if exit_code == 0 else None      # 모르면 None 이다
   ```
3. **선언이 추론을 이긴다.** 루프가 끝난 **뒤**에, 모은 이름과 같은 이름의 스텝을 전부
   `ok = False` 로 바꾼다(마커가 스텝보다 먼저 와도 되게 — #162 의 `FAIL: test` 는 되재생된
   `::rcm::step::test` 보다 **위**에 있다). 그리고

   ```python
   failed = next((s.name for s in steps if s.ok is False), None)   # 폴백 없음
   last   = steps[-1].name if steps else None
   ```

**`Progress` 에 더하는 칸** (`core/model.py`)

```python
last_step: str | None = None          # 마지막으로 시작한 스텝. 인과를 주장하지 않는다
fail_names: tuple[str, ...] = ()      # 선언된 실패 이름(순서 유지 · 중복 제거)
fail_truncated: bool = False          # MAX_FAIL_NAMES 를 넘겨 버린 것이 있다
```

**잠근 규칙 여섯**

| # | 규칙 |
|---|---|
| R1 | `failed_step` 은 `ok is False` 인 **첫** 스텝이다. `ok` 를 False 로 만드는 것은 `::rcm::step-end::fail` 과 `::rcm::fail::<스텝 이름>` **둘뿐**이다 |
| R2 | 스텝이 없거나 선언이 없으면 `failed_step is None`. **exit code 로 스텝을 고르지 않는다** |
| R3 | 새 스텝이 시작해 앞 스텝이 암묵적으로 닫힐 때는 지금처럼 `ok = True`(프로토콜의 순차 전제). 선언이 오면 그 True 를 **덮는다** |
| R4 | 잡이 끝나며 닫히는 마지막 스텝은 exit 0 이면 `True`, 아니면 `None`(모른다) |
| R5 | `::rcm::fail::<이름>` 이 스텝 이름과 같으면 **같은 이름의 스텝 전부**가 `ok = False`(매트릭스에서 이름이 반복될 수 있다 — 함정 4) |
| R6 | 이름은 120자로 자르고 잡당 100개까지. 넘친 것은 버리고 `fail_truncated` 로 밝힌다 |

### 1.2 `worker.py` — `outcome_for()`

```python
forced = cancelled or timed_out or lost
progress = progress_from_markers(..., exit_code=None if forced else rc)   # 1 을 넣지 않는다
...
STEP_STATES = (FAILED, TIMED_OUT)          # 스텝 라벨을 갖는 상태
failed_step = progress.failed_step if state in STEP_STATES else None
last_step   = progress.last_step   if state in STEP_STATES else None
fail_names  = progress.fail_names  if state in STEP_STATES else ()
```

`Outcome` 에 `last_step: str | None = None` · `fail_names: tuple[str, ...] = ()` ·
`fail_truncated: bool = False` 를 더한다. `__iter__` 의 3-튜플 풀기는 **그대로 둔다**(옛 호출부).

- `cancelled` · `lost` 는 세 칸 모두 비어 있다(결정 64).
- `succeeded` 는 `failed_step` 도 이름도 안 남긴다 — **잡 자신의 판정이 이긴다**. 정보성
  스텝이 `::rcm::fail::` 를 찍고 exit 0 으로 끝나면 대장에 안 들어간다.

### 1.3 `store.py` — DB v11

```python
DB_VERSION = 11
_MIGRATIONS[11] = ("ALTER TABLE jobs ADD COLUMN last_step TEXT",)
```

`_SCHEMA_V1` 의 `jobs` 정의에도 `last_step TEXT` 를 더한다(새 DB 는 한 번에 최신 스키마다).
`finish(...)` 에 `last_step: str | None = None` 인자를 더해 `_set_state` 로 넘기고,
`Job` 로 읽는 `_job_from_row` 에 `last_step=row["last_step"]` 을 더한다.

`Job` 에 `last_step: str | None = None`.

### 1.4 `core/status.py`

- `progress_json()` 에 `"last_step": p.last_step` 을 더한다(도는 잡의 상세용).
- `recent_json()` 에 `"last_step": job.last_step` 을 더한다 — **`/api/status` 의 최근 행과
  `GET /jobs/{id}` 의 종료 잡이 같은 함수를 쓴다**. 질의는 안 늘어난다(잡 행의 컬럼이다).

### 1.5 표시

| 파일 | 지금 | M5h |
|---|---|---|
| `core/render_text.py` 최근 줄 | `tail += f" (step {failed_step})"` | `failed_step` 이면 `" (step {failed_step})"`, 아니면 `last_step` 일 때 `" (last step {last_step})"` |
| `core/render_text.py` 큐 행 | `head += f" · ✘ {failed_step}"` | 그대로(도는 잡은 선언된 것만 오므로 이미 참이다) |
| `web/app.js` 최근 요약 | `recent.step` | `failed_step` → `recent.step`, 없고 `last_step` 이면 `recent.last_step` |
| `web/app.js` 최근 상세 | `recent.failed_step` | 같은 갈래로 `recent.last_step_label` |
| `core/notify.py` | `RCM_FAILED_STEP`(추론값) | 선언된 것만. `RCM_LAST_STEP` 추가 |

**잠근 문구**

| 키 | 영어 | 한국어 |
|---|---|---|
| `recent.step` | `step {step}` | `스텝 {step}` |
| `recent.last_step` | `last step {step}` | `마지막 스텝 {step}` |
| `recent.failed_step` | `failed step: ` | `실패한 스텝: ` |
| `recent.last_step_label` | `last step: ` | `마지막 스텝: ` |

CLI(영어, 한 줄): `❌ failed · exit 1 gate  ← macbook@…  11m 0s  16:50  exit 1 (last step build web …)`

### 1.6 문서 (단계 1 과 같은 커밋)

- `docs/usage.md` · `docs/usage.ko.md` 의 마커 절: `::rcm::fail::<name>` 추가 · 되재생/병렬
  스크립트의 안내(§6 예시) · 「선언하지 않으면 실패 스텝은 비어 있다」.
- `docs/configuration.md` 의 마커 표에 `::rcm::fail::` 한 줄.
- `examples/server.toml` 주석(74~77줄)에 `::rcm::fail::flaky_test` 한 줄.
- `CHANGELOG.md` `[Unreleased] ### Changed` — 동작 변경이라고 분명히 적는다.

---

## 2. 단계 2 — 실패 대장과 최근 이력

### 2.1 `store.py` — DB v12

```sql
CREATE TABLE IF NOT EXISTS job_failures (
  job_id INTEGER NOT NULL,
  name   TEXT    NOT NULL,
  seq    INTEGER NOT NULL,
  PRIMARY KEY (job_id, name)
);
CREATE INDEX IF NOT EXISTS job_failures_name ON job_failures(name);
CREATE INDEX IF NOT EXISTS jobs_key_finished ON jobs(key, finished_at DESC);
ALTER TABLE jobs ADD COLUMN fail_truncated INTEGER NOT NULL DEFAULT 0;
```

**쓰기** — `finish(...)` 에 `fail_names: Sequence[str] = ()` · `fail_truncated: bool = False`
를 더하고 **같은 트랜잭션 안에서** `INSERT OR IGNORE INTO job_failures(job_id, name, seq)` 를
돈다. `finish` 가 거절되면(이미 종료) 대장도 안 남는다.

**읽기** — `failure_stats(job_id, key, *, window) -> tuple[list[FailureRow], int, int]`
(`rows`, `window_jobs`, `window_unnamed`). 한 번의 호출에 질의 셋:

```sql
-- ① 창: 같은 key 의 최근 window 개 종료 잡(취소·유실 제외)
SELECT id, state FROM jobs
 WHERE key = ? AND state IN ('succeeded','failed','timed_out') AND finished_at IS NOT NULL
 ORDER BY finished_at DESC, id DESC LIMIT ?
-- ② 이 잡의 이름들이 창 안에서 몇 번 보였나
SELECT name, COUNT(*) seen, MIN(job_id) first_id, MAX(job_id) last_id
  FROM job_failures WHERE job_id IN (창) AND name IN (이 잡의 이름) GROUP BY name
-- ③ 창 안에서 실패했지만 이름을 하나도 안 남긴 잡 수
SELECT COUNT(*) FROM (창) w WHERE w.state IN ('failed','timed_out')
   AND w.id NOT IN (SELECT job_id FROM job_failures WHERE job_id IN (창))
```

**지우기** — `delete_old_jobs()` 의 삭제 목록에 `DELETE FROM job_failures WHERE job_id IN (…)`
를 `job_artifacts` 옆에 더한다.

### 2.2 `core/failures.py` (새 · 순수 · I/O 도 시계도 없음)

```python
VERDICT_UNKNOWN = "unknown"
VERDICT_FIRST_SEEN = "first_seen"
VERDICT_INTERMITTENT = "intermittent"
VERDICT_PERSISTENT = "persistent"

@dataclass(frozen=True)
class FailureRow:
    name: str
    seen: int
    first_seen_job_id: int | None
    last_seen_job_id: int | None

def verdict(seen: int, window: int, *, min_jobs: int) -> str:
    """창이 얕으면 unknown. seen==window 면 persistent, seen==1 이면 first_seen, 그 사이는 intermittent."""

def failures_json(
    rows: Sequence[FailureRow], *, steps: Container[str], window: int,
    window_unnamed: int, min_jobs: int,
) -> list[dict[str, Any]]:
    """`GET /jobs/{id}` 의 `failures[]`. 이름 순이 아니라 **잡이 찍은 순서**(seq)를 지킨다."""
```

`failures_json` 이 내는 항목 하나(키 이름 고정):

```json
{"name": "test", "step": true, "seen": 8, "window": 8, "window_unnamed": 0,
 "first_seen_job_id": 141, "last_seen_job_id": 162, "verdict": "persistent"}
```

- `step` = 이 이름이 그 잡의 스텝 이름이기도 한가(표시가 「스텝」과 「단위」를 구분한다).
- 판정 규칙(잠금):

  | 조건 | verdict |
  |---|---|
  | `window < min_jobs` | `unknown` |
  | `seen >= window` | `persistent` |
  | `seen == 1` | `first_seen` |
  | 그 밖 | `intermittent` |

### 2.3 `server.py`

`job_view()` 의 **종료 잡 갈래에만** `_with_failures(doc, job)` 를 더한다(`_with_artifacts`
바로 뒤). 도는 잡 · `/api/status` 는 **안 부른다**(결정 67).

```python
def _with_failures(self, doc, job):
    if job.state not in (FAILED, TIMED_OUT):     # 성공·취소·유실은 대장이 없다
        return doc
    rows, window, unnamed = self.store.failure_stats(job.id, job.key, window=cfg.failure_window_jobs)
    doc["failures"] = failures_json(rows, steps=…, window=window, window_unnamed=unnamed,
                                    min_jobs=cfg.failure_min_jobs)
    doc["failures_truncated"] = job.fail_truncated
    return doc
```

`steps=` 는 그 잡의 스텝 이름 집합이다. 종료 잡에는 `progress` 가 없으므로(workplan §13-1)
**`failed_step` · `last_step` 두 칸으로만 판정한다** — 이름이 그 둘 중 하나와 같으면
`step: true`. 마커를 다시 읽지 않는다(질의를 안 늘린다).

조회 실패는 fail-open 금지 규칙대로 **잡 문서를 죽이지 않는다**: `sqlite3.Error` 면
`failures` 키를 **아예 안 싣는다**(빈 배열은 「없다」는 뜻이라 다르다).

### 2.4 `config.py`

```python
failure_window_jobs: int = 20   # 이력 창 — 같은 key 의 최근 종료 잡 수
failure_min_jobs: int = 3       # 창이 이보다 얕으면 판정하지 않는다(unknown)
```

검증: `failure_window_jobs` 는 1 이상 500 이하, `failure_min_jobs` 는 1 이상이고
`failure_window_jobs` 이하. 위반 메시지는 `[server] failure_window_jobs must be …` 꼴.
`examples/server.toml`(= `templates/server.toml`)에 **기본값과 함께** 넣는다(결정 60).

### 2.5 CLI — 실패한 대기의 끝줄 (`core/render_text.py` 순수 + `cli.py`)

```python
def failure_lines(job: dict, *, job_id: int, url: str | None, limit: int = 3) -> list[str]:
```

잠근 문구(영어):

| 줄 | 문구 |
|---|---|
| 로그 | `log: rcm logs {id}` + url 이 있으면 ` · {url}` |
| 이름 | `failed: {name} — {history}` |
| persistent | `every one of the last {window} {key} runs` |
| intermittent | `{seen} of the last {window} {key} runs · intermittent?` |
| first_seen | `first time in the last {window} {key} runs` |
| unknown | `{seen} of {window} {key} runs so far` |
| 더 있음 | `… and {n} more (rcm logs {id})` |
| 분모 품질 | `note: {n} of those {window} runs failed without naming anything` |

`intermittent?` 의 물음표는 계약이다 — **판정이 아니라 제안**이다.

`cli._wait()` 는 `line.done()` 뒤 · `_print_json(out)` **앞**에 이 줄들을 `_err` 로 찍는다.
종료 코드 1 · 2 · 3 모두 로그 줄은 찍고(모를수록 로그가 필요하다), 이름 줄은 `failures` 가
있을 때만 찍는다.

### 2.6 웹

최근 행 상세에 이름별 배지. i18n 키(두 언어):

| 키 | 영어 | 한국어 |
|---|---|---|
| `failures.title` | `failed by name` | `이름별 실패` |
| `failures.persistent` | `every one of the last {window} runs` | `최근 {window}회 전부` |
| `failures.intermittent` | `{seen} of the last {window} runs · intermittent?` | `최근 {window}회 중 {seen}회 · 간헐?` |
| `failures.first_seen` | `first time in the last {window} runs` | `최근 {window}회 중 처음` |
| `failures.unknown` | `{seen} of {window} runs so far` | `아직 {window}회 중 {seen}회` |
| `failures.unnamed` | `{n} of those runs failed without naming anything` | `그 중 {n}회는 이름 없이 실패했다` |

---

## 3. 단계 3 — 404 가 길을 알려 준다

`server.py` 에 순수 함수 하나를 더하고 마지막 `raise ApiError(404, "not found")` 를 바꾼다.

```python
_ID_IN_PATH = re.compile(r"/(\d+)")

def not_found_hint(path: str) -> str:
    """모르는 경로에 맞는 길을 한 줄로. 숫자가 있으면 그 잡의 경로, 없으면 주요 라우트."""
```

잠근 문구:

| 입력 | `hint` |
|---|---|
| 숫자가 있는 경로(`/api/jobs/162` · `/logs/162` · `/job/162/log`) | `job #162 is GET /jobs/162 · its log is GET /jobs/162/log with that job's token (try: rcm logs 162)` |
| 그 밖 | `routes: GET /api/status · GET /api/health · GET /jobs/<id> · GET /jobs/<id>/log · POST /jobs` |

```python
raise ApiError(404, "not found", hint=not_found_hint(path))
```

`ApiError` 는 이미 임의 키를 본문에 싣는다(`state=` 를 그렇게 쓴다) — 확인하고, 아니면
`hint` 를 그 경로로 더한다. **별칭 라우트는 만들지 않는다**(결정 69).

`examples/session/ci-gate.sh` 의 실패 갈래에 `rcm logs "$job"` 안내를 넣는다.

---

## 4. 단계 4 — 목록이 코드를 말한다

### 4.1 `core/render_text.py` — 새 순수 함수

```python
MAX_IDENT = 32

def source_ident(src: dict[str, Any] | None) -> str:
    """목록 한 칸용 짧은 코드 신원. `_source_text()` 는 큐 행 전용이라 그대로 둔다."""
```

| 입력 | 결과 |
|---|---|
| `git_ref` | `{ref} @{sha[:7]}` |
| `tree` + `branch` | `{branch} @{base_sha[:7]}` + dirty 면 `+` |
| `tree` + branch 없음 | `{repo 의 마지막 조각} @{base_sha[:7]}` + dirty 면 `+` |
| 아무것도 없음 | `—`(DASH) |
| 32자 초과 | 앞을 남기고 `…` |

`rcm jobs` 한 줄과 `rcm top` 의 최근 줄에 이 칸을 넣는다(큐 행은 이미 `_source_text`).

### 4.2 `client.py` — tree 잡의 브랜치

`make_snapshot()` 이 git 체크아웃이면 `git rev-parse --abbrev-ref HEAD` 를 읽어
`Snapshot.branch` 에 담는다. 결과가 `HEAD`(detached)면 **`None`**. `cmd_run` 의 `source` 사전에
`"branch": snap.branch` 를 더한다.

**잠근 불변식**: `tree_hash` 는 `(경로, 모드, 내용 sha256)` 목록만으로 만든다 — 브랜치는
**안 들어간다**. `join_key(preset, inputs, source.identity)` 도 그대로다. 같은 트리를 다른
브랜치에서 올린 두 세션은 **지금처럼 합류한다**.

서버: `Source` 에 `branch: str | None = None`, `source_json()` 의 tree 갈래에 `"branch"`.
`source_json` 은 DB 의 JSON 을 그대로 읽으므로 마이그레이션이 없다(옛 잡은 `null`).

### 4.3 `cli.py` — `rcm jobs --ref`

`--ref REF` 는 `source.ref`(git_ref) 또는 `source.branch`(tree)에 **REF 가 들어 있는** 행만
남긴다(대소문자 구분, 부분 일치). `--mine` 과 `--state` 와 함께 쓸 수 있다.
도움말: `only jobs whose ref or branch contains REF`.

---

## 5. 단계 5 — 대기 전에 스냅샷을 놓는다

`cmd_run()` 의 업로드 `finally` 뒤, `_wait()` 를 부르기 전에:

```python
spec = _FetchSpec(root=…, baseline={e.path: e.sha256 for e in snap.entries if e.kind != "link"}, …) if fetch else None
snap = None      # 장부를 놓는다 — 대기는 20분이고 아무도 안 쓴다
```

`--no-wait` 갈래는 이미 곧바로 끝나므로 안 건드린다. 문서(`docs/usage.md` · `usage.ko.md`)에
**메모리가 빠듯한 기계의 패턴** 한 절: `rcm run --no-wait` → `rcm wait --job N`, 대기가 죽어도
잡은 서버에 살아 있고 셸이 보는 137 은 **잡의 실패가 아니다**.

---

## 6. 테스트 배치와 역할

역할 셋은 **서로의 파일을 건드리지 않는다.** 셋 다 `src/` 를 건드리지 않는다(test-first).

| 역할 | 시나리오 문서 | 테스트 파일 | 범위 |
|---|---|---|---|
| **A — 순수** | `docs/m5h-test-scenarios-a.md` | `tests/test_progress_m5h.py` · `tests/test_failures.py` | §1.1 마커·`failed_step`·`last_step`·`fail_names` 규칙 R1~R6 · §2.2 판정 4종과 경계 |
| **B — 저장·서버** | `docs/m5h-test-scenarios-b.md` | `tests/test_store_m5h.py` · `tests/test_server_m5h.py` | §1.2 `outcome_for` 상태별 · §1.3 v11 · §2.1 v12 · 대장 쓰기/읽기/삭제 · §2.3 `GET /jobs/{id}` · §2.4 설정 검증 · §3 404 hint |
| **C — 표시·클라이언트** | `docs/m5h-test-scenarios-c.md` | `tests/test_render_m5h.py` · `tests/test_cli_m5h.py` · `tests/test_client_m5h.py` · `tests/web/m5h.test.js` | §1.5 문구 갈림 · §2.5 실패 끝줄 · §2.6 웹 · §4 `source_ident`·`--ref`·`branch` 불변식 · §5 스냅샷 해제 |

공통 규칙:

- 시각은 `tests/jobfactory.py` 의 `NOW` 를 쓴다. sleep 금지, 스레드 금지(B 의 서버 테스트는
  기존 `tests/test_server*.py` 의 픽스처 방식을 따른다).
- 각 시나리오 문서는 **표**로 쓴다: `# | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그`.
- 문서 첫 줄에 「이 명세의 어느 절을 옮긴 것인가」와 「무엇을 안 건드렸나」를 적는다.
- **회귀 잠금**: A 는 #162 의 마커 순서를 그대로 픽스처로 넣는다(`무거운 셋 병렬 시작` →
  `test` → `secret-scan` → `build web`, exit 1). 이 픽스처가 이 마일스톤의 증인이다.

## 7. 검사와 완료 기준

```sh
ruff check . && ruff format --check . && pytest
node --test tests/web/*.test.js
python scripts/mutcheck.py            # 16 → 19
scripts/smoke_install.sh
```

| 단계 | 완료 기준 |
|---|---|
| 1 | #162 픽스처가 `failed_step is None` · `last_step == "build web …"` · 취소 잡에 스텝 라벨 없음 · 웹·CLI 문구 갈림 · v11 마이그레이션 |
| 2 | 같은 key 8회 중 1회 실패한 이름이 `1 of the last 8 gate runs · intermittent?` 로 나온다 · `/api/status` 의 질의 수 불변 · 대장이 잡 행과 함께 지워진다 |
| 3 | `/api/jobs/162` 가 진짜 경로를 말한다 · 실패·미상 종료에 `log: rcm logs 162` |
| 4 | `rcm jobs` 한 줄에 `<ref> @<sha>` · `--ref` 필터 · **브랜치가 `tree_hash` 를 안 바꾼다** |
| 5 | 2만 파일 트리에서 대기 중 `Snapshot` 이 살아 있지 않다 |

## 8. mutcheck 3종 (`scripts/mutcheck.py` — 16 → 19)

| # | 뮤테이션 | 빨개져야 하는 테스트 |
|---|---|---|
| 17 `failed-step-fallback` | `core/progress.py` 의 `failed` 를 `steps[-1].name` 폴백으로 되돌린다 | `tests/test_progress_m5h.py` (#162 픽스처) |
| 18 `failure-window-cancelled` | `failure_stats` 의 창에서 `state IN (…)` 을 지워 취소 잡을 포함시킨다 | `tests/test_store_m5h.py` |
| 19 `ledger-outside-tx` | `finish()` 가 대장을 커밋 **뒤에** 쓴다 | `tests/test_store_m5h.py` |

## 9. 안 하는 것 (이 단계에서)

- `RCM_FAIL_NAMES` 알림 env — 훅이 필요해지면 그때.
- 종료 잡에 스텝 목록 싣기(workplan §13-1) — 최근 행의 크기를 바꾸는 일이라 따로 잰다.
- `rcm flaky` 목록 명령 · `/api/jobs/…` 별칭 · 게으른 import 다이어트(workplan §7).
