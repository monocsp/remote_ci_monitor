# M5h 작업 명세 — 끝난 잡이 무엇이 왜 깨졌는지 말하게 한다

> 출처: 2026-09-09, **다른 머신의 세션**이 이 맥의 운영 인스턴스(v0.2.5)를 실제로 쓰면서 남긴
> 사용기 여섯 개. 여섯 중 다섯이 「끝난 잡을 읽는 일」에 몰려 있다 — 잡을 넣는 길은 이미
> 괜찮고, 결과를 읽는 길이 나쁘다.
>
> 한 줄 요약: **서버가 모르는 것을 아는 척하지 않게 하고**(`failed_step`), 프리셋이 무엇이
> 깨졌는지 **이름으로** 말할 수 있게 하고(`::rcm::fail::`), 그 이름의 **최근 이력**을 실패
> 보고에 붙인다. 나머지 셋은 길 안내다 — 로그·목록·대기 클라이언트.
>
> 바꾸지 않는 것: 런타임 의존성 0 · 스키마 v1(**키를 더하지 값을 바꾸지 않는다**) · 순수
> 계층은 I/O 도 시계도 안 본다 · 잡 로그는 언제나 토큰 · `/worker/*` 프로토콜의 모양 ·
> **서버는 임의 출력을 파싱하지 않는다**(마커만 믿는다).
>
> 구현(잠근 API · 파일별 변경 · 단계)은 `docs/m5h-implementation.md`.

## 1. 신고 여섯 개와 이 문서가 확인한 것

| # | 신고 | 코드까지 따라가 확인한 것 | 고칠 자리 |
|---|---|---|---|
| 1 | `failed_step` 이 틀린 스텝을 가리킨다(#162) | **사실.** 추론 한 줄이 「마지막으로 시작한 스텝」을 실패로 부른다. #162 의 마지막 마커는 **성공한** `build web` 이었다 | `core/progress.py` · PR 1 |
| 2 | 로그를 API 로 못 받는다 | **사실.** `/api/jobs/162`·`/logs/162` 는 `{"error":"not found"}` 한 줄이다 — 맞는 길을 안 알려 준다 | `server.py` · `cli.py` · PR 3 |
| 3 | 목록에서 「내 잡」을 못 알아본다 | **절반 사실.** 데이터는 이미 있다(`/api/status` 의 큐 행·최근 행 둘 다 `source` 를 싣는다). 그리는 쪽이 버린다 | `render_text.py` · `cli.py` · PR 4 |
| 4 | 대기 클라이언트가 무거워 두 번 OOM 으로 죽었다 | **절반 사실.** 정상 상태는 **31.6 MB 이고 평평하다**(누수 없음). 다만 tree 잡은 스냅샷 장부를 대기 내내 쥔다 — 2만 파일에서 **64 MB** | `cli.py` · PR 5 |
| 5 | 간헐 테스트 표시 (가장 갖고 싶은 것) | 서버에 **답이 없다.** 로그는 30일 있지만 「무엇이 깨졌는지」는 어디에도 **저장되지 않는다** | 새 마커 + DB v11 · PR 2 |
| 6 | 취소한 #176 의 요약에 무관한 문자열이 섞였다 | **프리셋 설명이 새는 것이 아니다.** `summary` 는 깨끗한 `cancelled by macbook` 이고, 옆에 붙은 것은 그 잡의 `failed_step` 이다 — **취소된 잡이 실패 스텝을 갖고 있다.** 1번과 같은 버그 | `worker.py` · PR 1 |

## 2. 실측

운영 인스턴스(8787)는 **읽기만** 했다. 메모리 측정은 이 워크트리의 `.venv` 로 띄운 임시
서버(포트 8799 · 자기 `data_dir`)에서 했다.

### 2.1 #162 — 서버가 말한 실패 스텝과 실제로 깨진 곳

`GET /jobs/162` (운영, 읽기):

| 칸 | 값 |
|---|---|
| `state` · `exit_code` | `failed` · `1` |
| `summary` · `summary_code` | `exit 1` · `exit_code` |
| **`failed_step`** | **`build web (release — 셰이더 impellerc 컴파일 회귀 포함)`** |
| `source` | `git_ref` · `dolomood` · `chore/ci-guard-scan-scope` @`25e1494` |

로그(634줄)의 마커 순서, 마지막 다섯 개:

```
::rcm::step::무거운 셋 병렬 시작 (test[동시 9] · gitleaks · build web)
FAIL: test (exit 1) — 깨진 테스트 1건(최대 80):        ← 실제 실패는 여기서 이미 확정됐다
  … just_audio_screen_music_port_test.dart: fadeIn — 0 에서 스며든다
::rcm::step::test                                      ← 병렬 잡의 로그를 순서대로 되재생한다
::rcm::step::secret-scan (gitleaks — …)                   ok
::rcm::step::build web (release — …)                      ok: build/web   ← 마지막 마커
```

- 게이트 스크립트는 무거운 셋을 **병렬로** 돌리고, 끝난 뒤 각 로그를 `::rcm::step::<이름>` 을
  머리말로 붙여 **되재생**한다. rcm 의 마커 모델은 「`step` 이 오면 앞 스텝이 끝난 것」이라
  순차 전제다 — 되재생된 머리말을 「지금 시작한 스텝」으로 읽는다.
- 그래서 `test` 는 **성공으로 닫히고**(`ok` 를 안 밝힌 스텝은 `True` 로 닫는다), 실패는
  마지막 머리말인 `build web` 에 붙는다. **성공한 스텝이 실패 스텝으로 보고됐다.**
- 이 라벨은 #169·#170·#171 에도 그대로 있다. 한 세션의 오해가 아니라 **모두가 같은 오해**를
  하게 되어 있다.

### 2.2 #176 — 취소된 잡의 「실패 스텝」

| 칸 | 값 |
|---|---|
| `state` · `exit_code` | `cancelled` · `-15` |
| `summary` · `summary_code` · `summary_args` | `cancelled by macbook` · `cancelled_by` · `{"by": "macbook"}` |
| **`failed_step`** | **`디바이스 QA chunk (실행기 스텝 · OS 권한 · 진행판 동기)`** |

요약은 깨끗하다. 신고자가 본 문자열은 `render_text.py` 의 최근 줄이 `summary` 뒤에
`(step <failed_step>)` 을 붙이고(웹은 `app.js` 가 `· 실패한 스텝 …`), 그 `failed_step` 이
**취소 시점에 마지막으로 되재생된 머리말**이었기 때문이다. 프리셋 설명은 새지 않았다.

### 2.3 대기 클라이언트의 메모리 (임시 서버 · 45초 sleep 프리셋 · `ps -o rss`)

| 무엇 | RSS | 모양 |
|---|---|---|
| `rcm run`(이 레포 트리, 348 파일) 업로드 뒤 대기 | **31.6 MB** | 45초 내내 **평평** · CPU 0.0% |
| `rcm run`(합성 트리, 20,000 파일 20 MB) 대기 | **64.0 MB** | 스냅샷 6초 만에 올라가 **대기 내내 안 내려온다** |
| `python -c pass` | 13.3 MB | 파이썬 자체의 바닥 |
| `import remote_ci_monitor.cli` | 29.8 MB | 그 중 stdlib(urllib·json·tarfile·hashlib)만 22.9 MB |

- **누수는 없다.** 대기 루프는 잡 JSON 하나만 들고 있고 SSE 는 줄 단위로 읽는다.
- 20,000 파일에서 늘어난 **32 MB 는 스냅샷 장부**(`Snapshot.files` + `entries`, 파일당 약
  1.6 KB)다. tar 은 업로드 직후 지우는데(`cmd_run` 의 `finally`) **장부는 `snap` 지역변수가
  대기가 끝날 때까지 붙잡고 있다.** `--fetch-artifacts` 가 아니면 아무도 안 쓴다.
- 신고자의 잡은 `git_ref` 라 스냅샷이 없다 — **그 세션이 죽은 건 31.6 MB 짜리 프로세스가
  기계 전체의 압박에 휘말린 것**이다. 여기서 고칠 수 있는 건 tree 잡의 절반(64 → 32 MB)과
  **죽어도 복구되는 절차**이지, 대기 자체를 가볍게 만드는 일이 아니다(§7).

### 2.4 404 가 말해 주지 않는 것

```
GET /api/jobs/162  → 404 {"error":"not found"}
GET /logs/162      → 404 {"error":"not found"}
GET /jobs/162/log  → 200 (그 잡의 토큰 · 합류자 · admin 만)
```

라우트 표는 `server.py` 의 `_route()` 한 곳에 모여 있고 맨 끝이 `raise ApiError(404, "not
found")` 다. `/api/status`·`/api/health`·`/api/whoami`·`/api/eta` 가 `/api` 아래인데 잡은
`/jobs` 아래라, `/api/jobs/…` 는 **자연스러운 오추측**이다. 오늘은 그 오추측에 아무 말도 안 한다.

## 3. 지금 상태 (코드)

### 3.1 추론 한 줄이 만드는 두 가지 거짓말

`core/progress.py` `progress_from_markers()`:

```python
failed = next((s.name for s in steps if s.ok is False), None)
if failed is None and exit_code not in (None, 0) and steps:
    failed = steps[-1].name          # ← 마지막으로 시작한 스텝을 실패로 부른다
```

1. **되재생·병렬 스크립트**에서 마지막 머리말을 실패로 부른다(§2.1).
2. `::rcm::step-end::ok` 로 **성공이 명시된** 스텝도 이 폴백에 걸린다 — 스텝을 다 끝내고
   그 뒤(집계·정리)에서 죽은 잡은 「성공했다고 스스로 밝힌 스텝」이 실패 스텝이 된다.

그리고 `worker.py`:

```python
failed_step = progress.failed_step if state != SUCCEEDED else None
```

`cancelled`·`timed_out`·`lost` 도 `SUCCEEDED` 가 아니므로 실패 스텝을 갖는다. 게다가
`outcome_for` 는 이 세 경우 `progress_from_markers(exit_code=1)` 로 부른다 — **폴백을 일부러
켜고 들어간다.** #176 이 그 결과다(§2.2).

### 3.2 종료된 잡은 목록에서 자기 코드를 못 말한다

`/api/status` 의 최근 행은 `source` 를 **싣는다**(`core/status.py` `recent_json()`). 못 그리는
쪽은 표시다.

| 화면 | 코드 신원 |
|---|---|
| `rcm top` 큐 행 | 있다 — `_source_text()` 가 `dolomood @25e1494 ref chore/ci-guard-scan-scope` 를 만든다 |
| `rcm top` 최근 행 | **없다** |
| `rcm jobs` | **없다**(`#id state key label 순번 시간 시각 summary`) |
| 웹 최근 행 | 펼치면 있다 |

`--mine` 은 **토큰 이름**으로 거른다. 이 맥의 모든 세션이 토큰 하나(`macbook`)를 공유하므로
「내 잡」이 아니라 「이 기계의 잡」이 된다. 신고자가 sha 로 찾은 이유다.

tree 잡에는 브랜치 이름이 아예 없다 — 클라이언트가 `base_sha`·`dirty`·`repo` 만 보낸다
(`client.make_snapshot()`).

### 3.3 「무엇이」 깨졌는지는 어디에도 저장되지 않는다

- `jobs.failed_step` 하나뿐이고, 그것도 추론값이다(§3.1).
- 로그는 30일 남지만 **텍스트**다. 「이 테스트가 최근 몇 번 빨갰나」를 답하려면 서버가 로그를
  파싱해야 하는데, 그건 이 도구가 안 하기로 한 일이다.
- 즉 신고 5번(「rcm 은 그 답을 이미 갖고 있다」)은 **아직 사실이 아니다.** 이력을 쌓으려면
  프리셋이 이름을 말해 주는 통로가 먼저 필요하다.

## 4. 설계

### 4.1 `failed_step` 은 **선언된 것만**이다 (결정 63·64)

- 실패 스텝은 다음 둘 중 하나로만 정해진다.
  - `::rcm::step-end::fail` — 지금 열린 스텝이 실패했다(오늘 있는 마커).
  - `::rcm::fail::<이름>` — 이름으로 지목한다(§4.2, 새 마커).
- 그 밖에는 **`failed_step: null`**. 「모른다」를 null 로 말하는 것은 이 도구의 기존 규칙이다
  (모르는 숫자는 null · ETA 는 자신있는 틀린 시각을 안 만든다).
- 대신 **`last_step`** 을 새로 싣는다 — 마지막으로 시작한 스텝의 이름. 인과를 주장하지 않고
  「끝났을 때 어디였나」만 말한다. #162 는 이렇게 바뀐다:

  | 오늘 | M5h |
  |---|---|
  | `failed_step: "build web (…)"` | `failed_step: null` · `last_step: "build web (…)"` |
  | 화면: `exit 1 (step build web …)` | 화면: `exit 1 · last step: build web …` |

- `cancelled`·`lost` 는 `failed_step` 도 `last_step` **둘 다 안 싣는다** — 사람이 세운 잡과
  서버가 잃은 잡에 스텝 라벨을 붙이면 그게 곧 #176 의 오해다. `timed_out` 은 `last_step` 만
  싣는다(제한 시간에 걸렸을 때 어디였는지는 진짜 정보다).
  ⚠️ 종료된 잡의 문서에는 `progress` 가 **없다**(§13-1). 그래서 `last_step` 은 「어디였나」를
  말하는 **유일한** 칸이 된다 — 취소 잡에서 이걸 빼면 그 정보는 로그에만 남는다. 뒤집고
  싶으면 결정 64 를 `cancelled` 도 `last_step` 을 싣는 쪽으로 고치면 되고, 그때도 문구는
  「실패한 스텝」이 아니라 「마지막 스텝」이다.
- `outcome_for` 가 강제 종료(`cancelled`·`timed_out`·`lost`)에 `exit_code=1` 을 넣어 폴백을
  켜던 것도 함께 없앤다.

### 4.2 새 마커 `::rcm::fail::<이름>` (결정 65)

```
::rcm::fail::test
::rcm::fail::just_audio_screen_music_port_test.dart
```

- 뜻: **이 이름의 것이 실패했다.** 스텝 이름이면 그 스텝이 `ok: false` 가 되고 첫 번째 것이
  `failed_step` 이 된다. 스텝 이름이 아니면 실패한 **단위**(테스트·파일·케이스)로 대장에만
  남는다. 한 잡에서 여러 번 찍을 수 있다.
- 상한: 이름 120자(스텝과 같다) · 한 잡에 **100개**까지. 넘으면 뒤는 버리고
  `failures_truncated: true` 를 싣는다. 11,000줄짜리 테스트 출력이 DB 를 채우면 안 된다.
- **옛 서버는 조용히 무시한다** — `parse_marker()` 가 모르는 kind 를 `None` 으로 돌려주고
  마커 줄은 로그에 그대로 남는다. 스크립트는 서버 버전을 안 봐도 된다.
- 이 마커는 `::rcm::summary::` 와 다르다. summary 는 **문장**(사람이 읽는다)이고 fail 은
  **이름**(서버가 센다)이다.

### 4.3 실패 대장과 간헐 판정 (결정 66·67·68)

**저장** — DB v12, 새 표 하나(`last_step` 은 v11 이다 — `docs/m5h-implementation.md` §1.3):

```sql
CREATE TABLE job_failures (
  job_id INTEGER NOT NULL,
  name   TEXT    NOT NULL,
  seq    INTEGER NOT NULL,          -- 잡 안에서의 순서(표시용)
  PRIMARY KEY (job_id, name)        -- 같은 이름을 두 번 찍어도 한 번이다
);
CREATE INDEX job_failures_name ON job_failures(name);
CREATE INDEX jobs_key_finished ON jobs(key, finished_at DESC);
```

- 쓰는 시점은 **잡을 끝내는 그 트랜잭션**이다(`store.finish(..., fail_names=…)`). 증거와
  결과가 따로 커밋되면 어긋난다. 로컬 워커와 원격 워커가 같은 `outcome_for` 를 쓰므로
  (`worker.py` · `remote_workers.py`) 배선은 한 곳이다.
- 지우는 시점은 잡 행과 같다 — `metadata_retention_days`(180) 의 메타데이터 삭제에 얹는다.

**판정** — 창은 같은 `key` 의 종료 잡(`succeeded`·`failed`·`timed_out`. `cancelled`·`lost` 는
아무 말도 안 하므로 뺀다) 중 **이 잡까지 최근 N개**다. 이 잡이 늘 창의 맨 앞이라 자기가 선언한
이름은 반드시 한 번 이상 세어지고, **한 달 뒤에 같은 잡을 다시 열어도 답이 같다**. 기본
`failure_window_jobs = 20`, 최소 `failure_min_jobs = 3`.

| verdict | 조건 | 뜻 |
|---|---|---|
| `unknown` | 창의 잡이 `failure_min_jobs` 미만 | 표본이 없다 — 아무 말도 안 한다 |
| `first_seen` | 창에서 이 이름을 가진 잡이 이 잡 하나 | 이 창에서 처음 본다(내 변경일 가능성) |
| `intermittent` | 1 < 본 횟수 < 창의 잡 수 | 왔다 갔다 한다 |
| `persistent` | 창의 모든 잡에서 봤다 | 계속 빨갛다 — 간헐이 아니다 |

- **분모의 품질을 같이 싣는다**: `window_unnamed` = 창 안에서 실패했지만 이름을 하나도 안
  찍은 잡 수. 옛 스크립트가 섞여 있으면 분자가 **과소**로 나오는데, 그걸 숨기지 않는다.
- 판정은 **서버가 코드로** 내려보낸다(결정 37: 문장이 아니라 코드. 그리고 M1 결정 B: 화면과
  CLI 가 어긋날 수 없게 서버가 정한다). 문장은 보는 쪽이 만든다.
- **`/api/status` 는 건드리지 않는다**(결정 67). 이미 가장 뜨거운 요청이고(결정 49),
  최근 행 10개마다 이력 질의를 붙이면 그 병목 위에 짐을 얹는다. 이력은 **`GET /jobs/{id}` 의
  종료 잡에만** 싣는다 — 대기 클라이언트가 마지막에 정확히 한 번 부르는 그 자리다.

**모양** (`GET /jobs/{id}`, 종료 잡):

```json
"failures": [
  {"name": "test", "step": true, "seen": 8, "window": 8, "window_unnamed": 0,
   "first_seen_job_id": 141, "last_seen_job_id": 162, "verdict": "persistent"},
  {"name": "just_audio_screen_music_port_test.dart", "step": false, "seen": 1, "window": 8,
   "window_unnamed": 0, "first_seen_job_id": 162, "last_seen_job_id": 162,
   "verdict": "first_seen"}
],
"failures_truncated": false
```

### 4.4 길 안내 — 404 와 실패 줄 (결정 69·70)

- 404 본문에 **`hint`** 를 더한다. 잡처럼 생긴 경로(`/api/jobs/162`·`/logs/162`·`/job/162`)면
  그 잡의 진짜 경로를, 아니면 주요 라우트 목록을 준다.

  ```json
  {"error": "not found",
   "hint": "job #162 is GET /jobs/162 · its log is GET /jobs/162/log with a Bearer token (rcm logs 162)"}
  ```

- **별칭(`/api/jobs/…`)은 만들지 않는다** — 한 가지에 이름 하나. 404 가 가르치면 된다.
- `rcm wait`·`rcm run` 이 **0 이 아닌 코드로 끝날 때** stderr 마지막 줄에 로그 길을 적는다:
  `job #162 failed · log: rcm logs 162 · http://macmini:8787/#/jobs/162`.
  종료 코드 3(모른다)에도 적는다 — 모를수록 로그가 필요하다.
- `examples/session/ci-gate.sh` 의 실패 갈래에 `rcm logs "$job"` 을 넣는다.

### 4.5 목록이 코드를 말한다 (결정 71)

- `rcm jobs` 한 줄에 `_source_text()` 의 결과를 넣는다. `rcm top` 의 최근 줄에도 같은 것을
  넣는다(큐 행은 이미 있다).

  ```
  #162  failed     gate             macbook@PCS-MACBOOK-PRO  chore/ci-guard-scan-scope @25e1494  took 11m  16:50  exit 1
  ```

- tree 잡은 클라이언트가 **브랜치를 실어 보낸다** — `source.branch = git rev-parse
  --abbrev-ref HEAD`. **표시용이다**: `tree_hash` 에도, 합류 신원에도 들어가지 않는다(같은
  트리를 다른 브랜치 이름으로 올린 두 세션은 지금처럼 합류해야 한다).
- `rcm jobs --ref <값>` 을 더한다 — `source.ref`(git_ref) 또는 `source.branch`(tree) 가 그
  값인 행만. 토큰 하나를 여러 세션이 나눠 쓰는 배치에서 「내 잡」에 가장 가까운 필터다.

### 4.6 대기 클라이언트 (결정 72)

- `cmd_run` 이 `_wait` 로 넘어가기 전에 **스냅샷을 놓는다**. `--fetch-artifacts` 면 필요한
  것은 `{path: sha256}` 하나뿐이니 그것만 만들어 들고 나머지(`entries`·`files`·`Snapshot`)는
  버린다. 2만 파일에서 64 → 32 MB.
- 회귀 테스트는 RSS 가 아니라 **붙잡힌 객체 수**로 잠근다(`tracemalloc` · CI 에서 흔들리지
  않게). 「대기에 들어간 뒤 `Snapshot` 인스턴스가 살아 있으면 빨갛다」.
- 죽어도 복구되게 만든다: `docs/usage.md` 에 **메모리가 빠듯한 기계의 패턴**을 적는다 —
  `rcm run --no-wait` 로 번호를 받고 `rcm wait --job N` 으로 붙는다. 대기 프로세스가 죽어도
  잡은 서버에 살아 있고, 셸이 보는 137 은 **잡의 실패가 아니다**.
- 파이썬 자체가 13.3 MB 라 게으른 import 다이어트는 안 한다(§7).

## 5. 보이게 하기

| 자리 | 오늘 | M5h |
|---|---|---|
| `rcm top` 최근 | `❌ failed gate ← macbook 11m 16:50 exit 1 (step build web …)` | `… exit 1 · last step: build web …` + 코드 신원 |
| `rcm jobs` | 코드 신원 없음 | `chore/ci-guard-scan-scope @25e1494` |
| `rcm wait` 실패 끝줄 | `#162 failed · exit 1` | `+ log: rcm logs 162` · 이름별 이력 최대 3줄 |
| `rcm run` stdout JSON | 잡 문서 그대로 | `failures[]` · `last_step` 이 그대로 실린다(래퍼가 jq 로 읽는다) |
| 웹 최근 행 | `· 실패한 스텝: build web …` | 선언된 것만 「실패한 스텝」. 아니면 「마지막 스텝」 |
| 웹 최근 상세 | — | 이름별 `1 / 최근 8회` 배지 |
| `notify` env | `RCM_FAILED_STEP` (추론값) | 선언된 것만. `RCM_LAST_STEP` 추가 |

CLI 문구(영어, 실패한 잡):

```
#162 failed · exit 1 · 11m 0s
  log: rcm logs 162 · http://macmini:8787/#/jobs/162
  failed: test                                    every one of the last 8 gate runs
  failed: just_audio_screen_music_port_test.dart  1 of the last 8 gate runs · intermittent?
```

- `intermittent?` 의 물음표는 장식이 아니다 — **판정이 아니라 제안**이다. 숫자를 옆에 두고
  사람이 정한다.
- 창에 이름 없는 실패가 있으면 한 줄 더: `(2 of those 8 runs failed without naming anything)`.

## 6. 게이트 스크립트가 할 일 (이 레포 밖)

rcm 은 이름을 **받아 적을 뿐** 만들지 않는다. `dolomood` 의 `scripts/local_ci.sh` 는 이미
깨진 테스트 목록을 계산해 `FAIL: test (exit 1) — 깨진 테스트 1건` 으로 찍고 있으므로, 그
자리에서 한 줄씩 더 찍으면 된다.

```sh
# 병렬 셋의 결과를 되재생하는 자리에서
echo "::rcm::fail::$name"                    # 스텝 이름 — failed_step 이 된다
sed -n 's/.*: \(.*_test\.dart\).*/::rcm::fail::\1/p' "$log" | sort -u | head -50
```

문서는 `docs/usage.md`(+ `usage.ko.md`)의 마커 절에 이 예시를 넣는다. 되재생·병렬 스크립트가
`::rcm::step::` 을 머리말로 쓰는 것은 **막지 않는다** — 그건 흔한 모양이고, M5h 뒤에는 그저
`last_step` 으로 정직하게 표시될 뿐이다.

## 7. 안 만드는 것

| 안 하는 것 | 왜 |
|---|---|
| 프리셋의 `fail_pattern` 정규식으로 로그에서 이름 뽑기 | 서버가 임의 출력을 파싱하기 시작하면 틀린 이름을 **자신있게** 대장에 넣는다. 스크립트 한 줄이 정확하고 싸다 |
| `::rcm::step::` 을 병렬 지원(열린 스텝 여러 개) | 진행 막대·ETA·`current_index` 가 전부 「한 번에 하나」 위에 서 있다. 병렬을 표현하려면 스키마와 화면이 같이 바뀐다 — M5h 범위 밖 |
| `/api/jobs/…` 별칭 | 한 가지에 이름 하나. 404 의 `hint` 가 가르친다 |
| 대기 클라이언트 게으른 import 다이어트 | 31.6 MB 중 13.3 MB 는 파이썬 자체, 22.9 MB 는 필요한 stdlib 다. 아껴야 5~7 MB 고 코드는 어려워진다 |
| `--mine` 을 세션 단위로 | 토큰이 세션 신원이 아니다. 세션마다 토큰을 만들면 되고, 아니면 `--ref` 로 거른다(§4.5) |
| `rcm flaky` 목록 명령 | 실패 보고에 붙는 것이 먼저다. 창을 훑는 명령은 그 다음에 필요하면(§11 결정 66 각주) |

## 8. 테스트 배치

파일 이름과 역할 분담은 `docs/m5h-implementation.md` §6 이 정본이다. 기존 테스트는 안 고친다 —
전부 **새 파일**이다(옛 동작을 잠근 테스트가 있으면 그건 이 마일스톤이 바꾸는 동작이므로 그
테스트를 고치는 것이 아니라 **왜 바뀌는지**를 여기 적고 옮긴다).

| 파일 | 역할 | 무엇 |
|---|---|---|
| `tests/test_progress_m5h.py` | A | **되재생 픽스처**(#162 의 마커 순서 그대로) → `failed_step is None` · `last_step == "build web …"` · `::rcm::fail::test` 를 더하면 `failed_step == "test"` · `step-end::ok` 뒤의 exit 1 · 이름 120자·100개 상한 · 모르는 kind 무시 |
| `tests/test_failures.py` | A | 창 판정 4종(`unknown`·`first_seen`·`intermittent`·`persistent`)과 경계 · `failures_json` 의 순서와 `step` 표시 |
| `tests/test_store_m5h.py` | B | v10 → v11 → v12 마이그레이션 · `finish(fail_names=…)` 가 같은 트랜잭션 · 중복 이름 한 번 · `failure_stats` 의 창(취소·유실 제외, 다른 key 제외, `window_unnamed`) · 메타데이터 삭제가 대장도 지운다 |
| `tests/test_server_m5h.py` | B | `outcome_for` 상태별(#176 취소 잡) · 종료 잡의 `GET /jobs/{id}` 에만 `failures` · `/api/status` 에는 없고 `last_step` 은 있다 · 대장 질의 실패는 키를 뺀다 · 설정 검증 · 404 `hint` 4종 |
| `tests/test_render_m5h.py` | C | 「실패한 스텝」과 「마지막 스텝」 갈림 · `failure_lines()` 문구 전부 · `source_ident()` |
| `tests/test_cli_m5h.py` | C | 실패·2·3 끝줄의 `log: rcm logs N` · 이력 3줄 + `… and N more` · `rcm jobs` 의 코드 신원 · `--ref` 필터 |
| `tests/test_client_m5h.py` | C | tree 스냅샷의 `branch` 는 `tree_hash` 를 **안 바꾼다** · detached 면 null · 대기 진입 뒤 `Snapshot` 이 살아 있지 않다 |
| `tests/web/m5h.test.js` | C | 두 언어의 문구 갈림 · 이력 배지 |
| `tests/test_docs_m5h.py` | (구현자) | 새 마커·설정 키가 `docs/usage.md`·`docs/usage.ko.md`·`docs/configuration.md`·`examples/server.toml` 에 있다 |

## 9. mutcheck (16 → 19)

| # | 뮤테이션 | 빨개져야 하는 테스트 |
|---|---|---|
| 17 | `failed_step` 을 다시 `steps[-1].name` 으로 폴백 | `test_progress.py` 되재생 픽스처 |
| 18 | 창 판정에서 `cancelled` 를 안 뺀다 | `test_flaky.py` |
| 19 | `finish()` 가 대장을 다른 트랜잭션에 쓴다 | `test_store.py` |

## 10. PR 순서 · 완료 기준

다섯 단계는 브랜치 `fix/cli-ux` 의 **커밋 다섯 개**로 가고 `dev` 로 PR 하나를 낸다 — 표시 문구가
1·3·4 에 걸쳐 있고 2 가 1 의 마커 위에 서므로 따로 머지하면 중간 상태가 사람에게 보인다.
커밋 하나하나는 그 자체로 초록이다(테스트·ruff·문서).

| 단계 | 이름 | 내용 | 완료 기준 |
|---|---|---|---|
| 1 | `fix(progress)`: 실패 스텝은 선언된 것만 | §4.1 · §4.2 파서 · 표시 문구 · notify env · 문서 | #162 의 마커 픽스처로 `failed_step is None` · 취소 잡에 스텝 없음 · 웹·CLI 문구 갈림 · CHANGELOG **Changed**(동작 변경) |
| 2 | `feat(failures)`: 실패 대장과 최근 이력 | §4.3 DB v11 · `GET /jobs/{id}` · CLI 3줄 · 웹 배지 · 설정 키 2개 | 같은 key 8회 중 1회 실패한 이름이 `1 of the last 8 · intermittent?` 로 보인다 · `/api/status` 질의 수 그대로 |
| 3 | `feat(hints)`: 404 가 길을 알려 준다 | §4.4 | `/api/jobs/162` 가 진짜 경로를 말한다 · 실패한 wait 의 끝줄에 `rcm logs` |
| 4 | `feat(cli)`: 목록이 코드를 말한다 | §4.5 | `rcm jobs` 한 줄에 `<ref> @<sha>` · `--ref` 필터 · tree 잡의 `branch` 가 해시를 안 바꾼다 |
| 5 | `perf(cli)`: 대기 전에 스냅샷을 놓는다 | §4.6 + 문서 | 2만 파일 트리에서 대기 RSS 가 절반 · 회귀 테스트 |

- 1 → 2 순서는 고정이다(대장이 새 마커 위에 선다). 3·4·5 는 서로 독립이고 1 과도 독립이다.
- 각 PR 은 `ruff` · `pytest` · `node --test tests/web/*.test.js` · `scripts/mutcheck.py` ·
  `scripts/smoke_install.sh` 를 지나고, 사용자에게 보이는 변화는 `CHANGELOG.md` 의
  `[Unreleased]` 에 PR 링크와 함께 적는다(`docs` 스킬 체크리스트).
- 스크린샷: PR 1·2 가 최근 행 문구를 바꾸므로 `tools/screenshots` 로 다시 찍는다.

## 11. 오너 결정 (63~72 · 기본값으로 구현하고 확인 대기)

| # | 항목 | 값 |
|---|---|---|
| 63 | 추론된 실패 스텝 | **없앤다.** `failed_step` 은 `step-end::fail` 또는 `::rcm::fail::` 로 선언된 것만. 나머지는 null 이고 `last_step` 이 「어디였나」를 말한다. 오늘 라벨이 붙던 잡의 상당수가 앞으로 빈칸이 된다 — CHANGELOG 에 동작 변경으로 적는다 |
| 64 | 취소·유실 잡 | `failed_step`·`last_step` 둘 다 안 싣는다. `timed_out` 은 `last_step` 만. (대안: 취소 잡도 `last_step` 을 싣고 문구만 「마지막 스텝」으로 — 종료 잡엔 스텝 목록이 없어서 그 칸이 유일한 단서다. §4.1 각주) |
| 65 | 새 마커 | `::rcm::fail::<이름>` 하나로 스텝과 단위를 같이 받는다. 이름 120자 · 잡당 100개 · 옛 서버는 무시 |
| 66 | 이력 창 | 같은 `key` 의 최근 `failure_window_jobs = 20` 개 종료 잡(취소·유실 제외), 최소 `failure_min_jobs = 3`. 목록 명령(`rcm flaky`)은 나중에 |
| 67 | 어디에 싣나 | `GET /jobs/{id}` 의 **종료 잡에만**. `/api/status` 는 안 건드린다(결정 49 의 병목 위에 얹지 않는다) |
| 68 | 분모의 품질 | 이름을 안 찍은 실패 잡은 분모에 남기고 `window_unnamed` 로 밝힌다. 분자는 과소일지언정 과대는 아니다 |
| 69 | 404 | `hint` 를 준다. `/api/jobs/…` 별칭은 **안 만든다** |
| 70 | 실패한 대기의 끝줄 | 종료 코드 1·2·3 모두 `rcm logs <N>` 과 URL 을 적는다 |
| 71 | 목록의 코드 신원 | `rcm jobs`·`rcm top` 최근에 `<ref\|branch> @<짧은 sha>`. tree 잡의 `branch` 는 **표시용** — `tree_hash`·합류 신원 불변 |
| 72 | 대기 클라이언트 | 대기 전에 스냅샷을 놓는다(64 → 32 MB). 게으른 import 다이어트는 안 한다. `--no-wait` + `rcm wait` 패턴을 문서에 적는다 |

## 12. 위험

| 위험 | 대응 |
|---|---|
| 63 이 지금 보이던 라벨을 **없앤다** — 「전보다 정보가 줄었다」로 읽힐 수 있다 | `last_step` 이 같은 자리에 남는다. 문구가 「실패한 스텝」에서 「마지막 스텝」으로 바뀔 뿐이고, CHANGELOG 와 릴리스 노트가 이유를 말한다 |
| 새 마커를 아무도 안 쓰면 5번 신고가 그대로 남는다 | §6 의 세 줄을 `docs/usage.md` 의 마커 절 · `examples/server.toml` 의 마커 주석(74~77줄) · 릴리스 노트에 넣는다. `examples/session/ci-gate.sh` 의 실패 갈래가 `failures[]` 를 읽어 예시가 실제로 돈다 |
| `job_failures` 가 커진다 | 잡당 100개 상한 · 잡 행과 같이 지워진다 · 이름 인덱스 하나. 하루 50잡이면 하루 5,000행이 최악이다 |
| 창 질의가 `GET /jobs/{id}` 를 느리게 한다 | 종료 잡에만, `(key, finished_at)` 인덱스로 20행 + 대장 조인. 도는 잡(2초마다 폴링되는 쪽)은 안 건드린다 |
| tree 잡의 `branch` 가 합류를 깬다 | `tree_hash` 계산에 안 들어간다는 테스트를 PR 4 에 넣는다 |

## 13. 이 명세가 발견한 기존 문제

1. **종료된 잡의 문서에는 `progress` 가 없다.** `GET /jobs/176` 의 칸은 `state`·`summary`·
   `failed_step`·`transitions`… 뿐이고 **스텝 목록이 없다** — 잡이 끝나는 순간 스텝 정보가
   API 에서 사라진다. `failed_step` 하나에 무게가 쏠린 이유이자, 신고 1·6 이 아프게 느껴진
   이유다. M5h 는 `last_step` 을 더하는 선에서 멈춘다(종료 잡에 스텝 목록을 싣는 것은
   최근 행의 크기를 바꾸는 일이라 따로 잰다).
2. **강제 종료가 스텝을 실패로 물들인다.** `outcome_for` 는 `cancelled`·`timed_out`·`lost` 에
   `progress_from_markers(exit_code=1)` 을 부른다. 마지막 열린 스텝은 `ok: False` 로 닫히고,
   그게 곧 #176 의 `failed_step` 이다. §4.1 이 이 호출부를 함께 고친다.
3. **`--mine` 의 신원은 세션이 아니라 토큰이다.** 한 기계의 모든 세션이 토큰을 공유하면
   `rcm jobs --mine` 은 「이 기계의 잡」이 된다. 세션마다 토큰을 만드는 것이 정공법이고,
   §4.5 의 `--ref` 는 그걸 안 바꾸고도 자기 잡을 찾게 해 준다.
4. **`key` 가 프리셋 이름과 같으면 창이 넓어진다.** 운영의 게이트는 `duration_key_inputs` 가
   없어 `key = "gate"` 다 — `gate`·`gate --smoke` 를 나누고 싶으면 프리셋을 나누거나
   `duration_key_inputs` 를 준다. 이력 창(§4.3)은 중앙값과 **같은 축**을 쓴다.
