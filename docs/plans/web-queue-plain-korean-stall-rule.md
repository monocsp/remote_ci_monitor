# 웹 큐 — 쉬운 한국어 · 멈춤 판정 규칙 · 레인 시각 위계

브랜치 `feat/web-queue-plain-korean-stall-rule` (origin/dev 에서). 워크트리
`remote_ci_monitor-queue-plain-korean-stall-rule`.

세 덩어리다. (1) 서버의 멈춤 판정을 **단계 실측**으로 바꾸고 침묵은 새 상태 `quiet` 로 낸다.
(2) 화면의 한국어 문구를 전면 교체하고 조사 회피·문체 흔들림·이모지를 없앤다.
(3) 큐 표의 시각 위계를 「레인」안으로 다시 잡는다.

순서가 중요하다 — (1)이 만드는 새 상태를 (2)의 문구와 (3)의 스타일이 받는다.

---

## 0. 지금 무엇이 문제인가 (근거)

`core/queue.py:195-198`:

```python
stuck = job.state != CANCELLING and (
    elapsed > cfg.stuck_multiplier * expected
    or (job.phase != PHASE_MATERIALIZING and _seconds(last_output, now) > cfg.no_output_seconds)
)
```

`gate` 프리셋은 마지막 단계에서 test 동시 9 · gitleaks · build web 을 병렬로 돌린다. 이 도구들은
끝날 때 출력을 몰아 뱉는다. 그래서 **정상으로 도는 매 실행마다** 4분 침묵 조건에 걸린다.
2026-09-15 관찰: 경과 17m 24s · 예상의 1배 · 7m 53s 출력 없음 · 단계 49/49 진행 중인데 「⚠ 멈춘 듯」.

두 번째 문제는 같은 사실을 **세 곳에 빨갛게 세 번** 칠하는 것이다:
「안 움직이는 것」 패널(`app.js:397 notMoving`) · 이유 칸의 `.stuck` 배경 칩(`style.css:191`) ·
진행바의 빗금 + 라벨(`style.css:152·155`). 화면이 시끄러운 원인은 색 수가 아니라 이 중복이다.

---

## 1. 덩어리 (1) — 멈춤 판정을 단계 실측으로

### 1.1 오너가 정한 규칙

- 침묵만으로는 `stuck` 이 아니다. 침묵은 새 상태 **`quiet`(조용함)** — 회색, 경보 아님.
- 판정 기준은 **현재 단계의 실측**이다. 현재 단계가 자기 실측 중앙값의 배수를 넘으면 `stuck`.
- 단계 실측이 없을 때만 오늘의 `no_output_seconds` 로 떨어진다.
- `elapsed > stuck_multiplier * expected` 는 남긴다.

### 1.2 결정 D1 — 단계 실측을 어디서 어떻게 읽나

**마커 전용 표는 없다.** `Store.add_marker`(`store.py:2217`)는 `events` 표에 `kind='marker'` 행을
넣는다. 인덱스는 `events_job ON events(job_id, id)` 하나뿐 — `kind` 로 훑는 인덱스가 없다.
그래서 45일치 표본 전체의 마커를 읽는 설계는 **금지**한다. `/api/status` 가 보존된 이벤트 수에
선형으로 끌려간다(같은 함정을 M5f 결정 49 가 `list_sample_rows` 로 이미 한 번 고쳤다 —
`store.py:912-940` 의 주석이 그 기록이다).

대신 **필요한 key 에만** 읽는다. 지금 도는 잡은 보통 1~3개이고, 그 key 들의 최근 성공 잡 10개면
중앙값에 충분하다. `jobs_key_finished ON jobs(key, finished_at DESC)`(v13, `store.py:133`)가 이미
있어 잡 고르기는 인덱스 한 번, 마커 읽기는 `markers_for()` 가 `events_job` 을 탄다.

**새 Store 메서드** — `store.py`, `list_sample_rows` 옆:

```python
def list_step_sample_ids(self, key: str, *, since: datetime, limit: int) -> list[int]:
    """그 key 의 최근 **성공** 잡 id, 늦게 끝난 것부터 `limit` 개.

    단계 실측 중앙값의 표본이다. `jobs_key_finished` 를 탄다 — 45일치를 다 읽지 않는다.
    """
```

`sample_policy` 는 여기서 **쓰지 않는다**(항상 `succeeded`). 실패한 잡의 단계 소요는
「평소 얼마나 걸리나」의 표본이 아니다 — 실패는 대개 단계를 일찍 끊는다. 이 판단을 메서드
독스트링에 적는다.

`limit` 은 설정 키가 아니라 `server.py` 의 모듈 상수 `STEP_SAMPLE_JOBS = 10` 으로 둔다.
성능 손잡이일 뿐이고, **되돌리기 어려운 이름(설정 키)을 하나라도 덜 만든다**(§7 참조).

### 1.3 결정 D2 — 순수 함수의 경계

`core/queue.py` 는 시계를 안 보고 마커도 안 판다. 서버가 여기까지 만든다:

```
store.list_step_sample_ids(key)  →  store.markers_for(ids)
    →  core.progress.progress_from_markers(...)   # 이미 있는 함수
    →  Progress.steps                              # Step(name, state, seconds, ...)
    →  core.queue.step_medians_from({key: [steps, steps, ...]}, cfg)
```

**새 순수 함수** — `core/queue.py`, `medians_from` 바로 아래:

```python
@dataclass(frozen=True)
class StepMedian:
    """한 key 의 한 **단계 이름**이 평소 얼마나 걸리나. `medians_from` 의 단계판이다."""

    seconds: float
    sample_count: int


def step_medians_from(
    runs: Mapping[str, Sequence[Sequence[Step]]], cfg: QueueConfig
) -> dict[str, dict[str, StepMedian]]:
    """key → 단계 이름 → 중앙값. `step_min_samples` 미만이면 넣지 않는다.

    **단계의 신원은 이름이다**(번호가 아니다). 되재생·병렬 스크립트에서 번호는 실행마다
    흔들리지만 이름은 잡이 선언한 것이다(M5h 결정 63 이 `failed_step` 에서 배운 것과 같다).
    끝난(`state == "done"`) 단계의 `seconds` 만 센다 — 도는 중인 단계는 아직 표본이 아니다.
    """
```

`Step` 은 `core/model.py:264` 에 이미 있고 `queue.py` 는 `model` 만 더 import 하면 된다
(순환 없음 — `progress.py` 는 `queue.py` 를 안 본다).

### 1.4 결정 D3 — 판정식 (확정)

`_busy_estimate` 의 시그니처에 `progress: Progress | None` 과
`step_medians: Mapping[str, StepMedian]` 을 더한다(`compute_queue` 가 이미 `progress` 맵을 받는다 —
`queue.py:239`).

```python
silent = job.phase != PHASE_MATERIALIZING and _seconds(last_output, now) > cfg.no_output_seconds

step_stuck = False
stuck_code: str | None = None
step_expected: float | None = None
cur = progress.current_name if progress else None
m = step_medians.get(cur) if cur else None
if m is not None:  # ① 이 단계의 실측이 있다 — 그것으로 판정한다
    step_expected = m.seconds
    cur_seconds = progress.current_seconds or 0.0
    step_stuck = cur_seconds > cfg.step_stuck_multiplier * m.seconds
    if step_stuck:
        stuck_code = STUCK_STEP
elif progress is None or not progress.steps:  # ② 단계 이야기를 아예 안 한다 — 오늘 규칙으로
    step_stuck = silent
    if step_stuck:
        stuck_code = STUCK_NO_OUTPUT
# ③ 단계는 도는데 실측이 아직 없다 → stuck 아님. `quiet` 만 낸다.

over_elapsed = elapsed > cfg.stuck_multiplier * expected
stuck = job.state != CANCELLING and (over_elapsed or step_stuck)
if stuck and stuck_code is None:
    stuck_code = STUCK_ELAPSED
quiet = silent and not stuck and job.state != CANCELLING
```

**③ 이 오너의 문장을 한 칸 좁힌 자리다.** 「단계 실측이 없으면 `no_output_seconds` 로 떨어진다」를
글자 그대로 쓰면, 이력이 아직 없는 새 key 의 `gate` 는 오늘과 똑같이 매번 빨개진다 — 고치려던 바로
그 사고다. **침묵이 우리가 가진 유일한 신호일 때만** 침묵을 근거로 쓴다. 잡이 단계를 찍고 있으면
「몇 번째 단계에 있다」가 이미 살아 있다는 증거이고, 그 단계가 얼마나 걸려야 하는지를 모르는 것은
**모르는 것**이지 죽은 것이 아니다(「없는 숫자를 만들지 않는다」).

→ **결정 A4(§8): 이 좁힘을 채택한다.** 진짜로 죽은 잡의 안전망은 그대로 둘 있다 —
`elapsed > 3×expected` 와 프리셋의 `timeout_seconds`(이 변경과 무관하게 돈다).

### 1.5 `quiet` 의 표현 — 플래그 + reason 둘 다

- `core/model.py`
  - `Estimate` 에 `quiet: bool = False`, `stuck_code: str | None = None`,
    `step_expected_seconds: float | None = None` 를 **기본값 있는 필드로** 더한다
    (기존 `Estimate(...)` 호출자 전부가 그대로 산다 — `shared` 가 M5f 에 같은 방식으로 들어왔다).
  - `REASON_QUIET = "quiet"` 를 더한다.
  - `STUCK_ELAPSED = "over_elapsed"` · `STUCK_STEP = "over_step"` · `STUCK_NO_OUTPUT = "no_output"`.
  - **`ACTIONABLE_REASONS` 에는 안 넣는다.** `held_by_load` 와 같은 종류다 — 의도되지 않았지만
    **경보가 아니고**, 「확인이 필요한 작업」 패널에 올리면 오늘의 빨간 소음이 이름만 바꿔 남는다.
    이 문장을 `model.py` 의 주석에 결정 근거로 적는다.
- `core/queue.py::_busy_reason` — 우선순위는 `cancelling → materializing → stuck → overdue → quiet → running`.
  초과 실행이면서 조용한 잡은 `overdue` 로 낸다(더 행동 가능한 사실이 이긴다).
- `core/status.py::estimate_json`(`status.py:75-91`) — `"quiet"`, `"stuck_code"`,
  `"step_expected_seconds"` 세 키를 더한다. `confidence(...)` 의 `overdue=` 인자에는
  **`quiet` 를 섞지 않는다**(`status.py:217`) — 조용한 잡의 ETA 신뢰도는 그대로다.
- `core/render_text.py::_reason_text`(`render_text.py:395`) — `"⚠ likely stuck"` 를
  근거별 문장으로 바꾸고 `quiet` 분기를 더한다. `⚠` 를 뗀다.

### 1.6 서버 배선

- `server.py::_DbSnapshot`(`server.py:256`) 에 `step_medians: dict[str, dict[str, StepMedian]]`
  와 `step_medians_error(_code)` 를 더한다(기본값 있는 필드로 — 뒤쪽에 붙인다).
- `server.py::_load_medians`(`server.py:993`) 옆에 `_load_step_medians(now, cfg, keys)`.
  **같은 캐시 정책**(`_medians_dirty` + `MEDIANS_MAX_AGE_SECONDS`)을 쓰고, 실패는 캐시하지 않는다
  (`server.py:1017` 위의 주석이 그 이유를 이미 적어 뒀다).
  `keys` 는 `snap.jobs` 중 `is_busy` 인 잡의 `key` 집합이다 — 도는 잡이 없으면 질의 0회.
- `server.py::_queue_rows`(`server.py:1046`) 가 `compute_queue(..., step_medians=...)` 로 넘긴다.
  풀별로 나누지 않는다 — 단계 소요는 key 에 붙고, 풀 차이는 `expected` 쪽(`_pool_medians`)이 이미
  흡수한다. 이 판단을 주석에 적는다.
- `server.py::queue_config`(`server.py:936`) 에 새 두 키를 싣는다.

### 1.7 설정 — 새 키 2개

`config.py::EstimateSection`(`config.py:141`):

```python
step_stuck_multiplier: float = 3.0  # 현재 단계가 자기 실측 중앙값의 몇 배를 넘으면 stuck 인가
step_min_samples: int = 3  # 단계 중앙값을 믿기 시작하는 표본 수(잡 중앙값보다 높다)
```

`step_min_samples` 가 `min_samples`(2)보다 높은 이유: 단계 소요는 잡 소요보다 훨씬 시끄럽다
(캐시 적중·병렬도·기계 부하가 단계 단위로 다르다). 3 이면 중앙값이 한 번의 이상치에 끌려가지 않는다.
→ **결정 A5(§8): 3 으로 확정.**

`config.py:804` 옆에 검증을 더한다: `step_stuck_multiplier > 1`, `step_min_samples >= 2`.
`examples/server.toml:56-63` 의 `[estimate]` 블록에도 두 줄을 더한다(`tests/test_examples.py:153` 이
이 파일을 실제로 파싱한다).

### 1.8 마이그레이션 — 필요 없다

과거 마커를 **읽기만** 한다. `events` 표도 인덱스도 그대로다. `PRAGMA user_version` 은 안 움직인다.
새 잡에만 값이 생기는 칸도 없다 — 오늘 이미 찍히고 있는 마커로 계산한다.

### 1.9 `schema_version` — 안 올린다 (결정 D4)

CHANGELOG 머리말의 규칙: *"removing or changing the meaning of a key bumps that number"*.
`CONTRIBUTING.md` 도 같다: *"Adding a key is free"*.

- 더하는 키(`quiet`·`stuck_code`·`step_expected_seconds`)와 더하는 `reason` 열거값(`quiet`)은 **공짜**다.
- `estimate.stuck` 의 **뜻**은 안 바뀐다 — 여전히 「이 잡은 죽었을지 모른다」다. 바뀐 것은 그 판단의
  **근거**이고, 근거는 오늘도 `no_output_seconds` 설정 하나로 바뀐다(그 값을 크게 주면 침묵 조건이
  사실상 꺼진다). 설정으로 바꿀 수 있는 것을 기본 규칙으로 바꾸는 일이 스키마를 깨지는 않는다.
- 값 하나를 더 받게 되는 `reason` 은 옛 클라이언트가 이미 모르는 값을 다룰 수 있다
  (`app.js:268 default: reason.unknown`, `render_text.py:416 return reason or "unknown"`).

**올려야 했을 경우**: `estimate.stuck` 을 없애고 `estimate.stall: {state, code}` 객체로 갈아엎는 안.
그 안은 안 고른다 — 되돌릴 수 없고(§7), 얻는 것이 키 두 개뿐이다.

근거를 CHANGELOG `[Unreleased]` 에 한 문단으로 적는다. `schema_version == 1` 을 박아 둔 시험이
15곳 있고(`tests/test_compat_m5e.py:181` 등) 그대로 초록으로 남는다는 것 자체가 결정의 검증이다.

---

## 2. 덩어리 (2) — 화면 한국어 문구

### 2.1 일괄 교체 (`web/i18n.js` KO)

전역 치환 하나(**잡 → 작업**, 약 30곳)는 문자열을 **한 줄씩 눈으로** 바꾼다 — `sed` 로 훑으면
「잡은 상태」 같은 곳까지 먹는다.

| 키 | 줄 | 새 문구 |
|---|---|---|
| `queue.group_running` / `_waiting` | 493 / 494 | 작업 중 (n) / 대기열 (n) |
| `queue.group_none_running` / `_waiting` | 495 / 496 | 진행 중인 작업이 없습니다 / 대기 중인 작업이 없습니다 |
| `queue.col_job…col_source` | 497–503 | 작업 · 작업 이름 · 실행한 사람 · 상태 · 진행 시간 · 완료 예상 · 코드 출처 |
| `reason.running` / `reason.running_lane` | 631 / 632 | 진행 중 / 진행 중 · 레인 n |
| `state.running` / `state.busy` | 608 / 618 | 진행 중 (결정 A3) |
| `reason.stuck` · `pbar.stuck` | 644 / 704 | 응답 없음 / 응답 없음 (⚠ 제거) |
| `reason.quiet` · `pbar.quiet` | **신규** | 출력이 조용합니다 / 조용함 |
| `reason.step_over` | **신규** | `<단계 이름>` 단계가 평소보다 n배 오래 |
| `reason.times_expected` | 645 | 지난 실행보다 n배 오래 |
| `reason.waiting_for_lane` | 633 | 대기 중 |
| `reason.lanes_busy` · `header.lanes_busy` · `queue.lanes_busy` | 634 / 445 / 673 | 동시 실행 n/m |
| `reason.not_scheduled` | 649 | 시작되지 않음 |
| `reason.held_by_load` | 622 | 머신이 바빠 대기 중 |
| `summary.stuck` (+ `index.html:38`) | 605 | 확인이 필요한 작업 |
| `summary.nothing_stuck` · `nothing_else_stuck` | 471 / 472 | 모두 정상입니다 / 그 밖에는 모두 정상입니다 |
| `summary.load` · `host.cores_load` | 484 / 534 | 처리 대기 n · 코어 m개 기준 |
| `pbar.time_measured` / `_preset` / `none` | 701–707 | 지난 실행 기준 / 설정한 예상 시간 기준 / 진행률을 알 수 없음 |
| `conf.measured` | 657 | 보통 · 최근 6회 기준 |
| `est.how` · `est.no_samples` · `est.title`(+`index.html:60`) · `est.to_preset` | 558 / 551 / 607 / 556 | 아래 본문 |
| `queue.presets` · `pool.name` | 491 / 681 | 작업 유형 / 실행 그룹 |
| `progress.*` · `recent.step` 등 「스텝」 | 690–697, 718–719, 727, 760–761 | 단계 |
| `row.you_joined` · `your.joined` | 509 / 676 | 같은 작업에 묶임 / 함께 기다리는 사람 n명 |
| `art.label` · `art.state.dropped` · `art.state.empty` | 731 / 734 / 733 | 결과 파일 / 저장하지 않음 / 가져온 파일 없음 |
| `state.lost` · `recent.lost` | 616 / 715 | 결과를 잃음 |
| `header.worker_unreachable` | 457 | 신호가 끊겼습니다 |
| `reason.sigterm` · `cancel.running_body` | 647 / 575 | 정상 종료를 먼저 요청하고, 그래도 끝나지 않으면 강제로 종료합니다 |
| `conn.polling` | 437 | 주기 확인 |
| `cache.text` | 770 | 보관 중인 파일 n개 |
| `summary.host_no_sample` · `host.no_sample` · `host.sampled` · `summary.host_sampled` | 474 / 530 / 533 / 475 | 측정값 / n 전 측정 |
| `summary.verdict_partial` | 478 | 일부만 확인됨 |
| `failures.intermittent` | 722 | … 실패 · 가끔 실패합니다 |
| `header.paused` | 449 | 진행 중인 작업은 끝까지 실행하고, 새 작업은 시작하지 않습니다 |
| `row.stalled_note` | 512 | 응답이 계속 없으면 서버가 자동으로 취소합니다 |

`est.no_samples` 전문: 「아직 기록이 없습니다. 같은 작업이 2번 성공하면 그때부터 실제 걸린 시간으로
예상합니다.」 · `est.how` 전문: 「최근 5회 이상 기록이 있으면 높음, 그보다 적으면 보통, 기록이 없어
설정값을 쓰면 낮음」 · `est.title`: 「예상 시간 계산 근거」 · `est.to_preset`: 「기록 2회 미만이라
설정값 사용」.

**그대로 두는 것**: `tree` · `ref` · `미커밋` · 프리셋 이름 · 키 · SHA · 명령 · 그룹 이름
(카탈로그 머리말 `i18n.js:6-7` 의 「번역하지 않는 것」).

**`host.job_storage` 의 「n 전 측정」은 건드리지 않는다** — `tests/web/job_storage.test.js:125` 가
`"rcm 데이터 3 GB · 57m 전 측정"` 를 글자 그대로 잠그고 있고, 새 규칙과 이미 같은 꼴이다.

### 2.2 문체 — 합쇼체로 맞추는 3곳

| 키 | 줄 | 지금 | 새 문구 |
|---|---|---|---|
| `host.job_storage_unknown` | 542 | rcm 데이터: 용량을 재지 못했다 | rcm 데이터: 용량을 재지 못했습니다 |
| `reason.held_by_load` | 622 | 부하로 대기 — 머신이 바쁘다 | 머신이 바빠 대기 중 |
| `failures.unnamed` | 725 | 그 중 n회는 이름 없이 실패했다 | 그 중 n회는 이름 없이 실패했습니다 |

`reason.held_by_load` 는 이유 칸에 들어가는 짧은 조각이라 명사형으로 끝낸다. 이 예외를 카탈로그
주석에 한 줄로 적는다.

### 2.3 조사 헬퍼 — `이(가)` 7곳 (`i18n.js`)

대상: `header.paused`(450) · `reason.blocked_by`(637) · `reason.sigterm`(647) · `recent.by`(717) ·
`outcome.cancelled_by`(775) · `outcome.worker_restarted_without_job`(795) ·
`outcome.worker_unreachable`(798).

```js
/** 마지막 글자에 받침이 있는가. 모르면 null(호출자가 받침 없는 쪽으로 읽는다). */
function hasFinalConsonant(word) { ... }
/** 이름 뒤에 붙일 조사 하나. `pair` 는 "이/가" · "을/를" · "은/는" · "와/과". */
function josa(word, pair) { ... }
/** 이름 + 조사. 문장 쪽은 이것만 쓴다. */
function withJosa(word, pair) { return String(word) + josa(word, pair); }
```

판정 규칙(주석에 표로 적는다):

1. 뒤에서부터 **판정할 수 있는 글자**를 찾는다 — 따옴표·괄호·마침표 등은 건너뛴다.
2. 한글 음절(U+AC00–U+D7A3): `(code - 0xAC00) % 28 !== 0` → 받침 있음.
3. 숫자: 읽는 소리로. 받침 **있음** `0 영 · 1 일 · 3 삼 · 6 육 · 7 칠 · 8 팔`,
   **없음** `2 이 · 4 사 · 5 오 · 9 구`.
4. 라틴 글자(대소문자 무시): 글자 이름의 읽는 소리로. 받침 **있는 것은 `l`(엘) · `m`(엠) ·
   `n`(엔) · `r`(알) 넷뿐**이고 나머지 22개는 없다.
   (2026-09-15 정정: 처음에 `f` 를 넣었는데 「에프」의 끝 글자 `프` 는 받침이 없다 —
   규칙이 「글자 이름의 읽는 소리」이므로 `f` 는 받침 없는 쪽이다. `perf가` · `mail이`.)
5. 그 밖(한자·이모지·빈 문자열): `null` → **받침 없는 쪽**(가/를/는/와). 「이(가)」로 되돌아가지
   않는다 — 괄호를 없애는 것이 이 작업의 목적이다.

`api` 객체에 `josa`·`withJosa`·`hasFinalConsonant` 를 노출한다(`i18n.js:838-841`).

**새 시험 `tests/web/josa.test.js`** — 필수. 잠그는 것: 한글 받침 있음/없음/ㄹ받침 · 숫자 열 개 전부 ·
라틴 26자 전부(다섯만 받침) · 실제 값 셋(`mac2` `PCS-MACBOOK-PRO` `web@studio`) · 네 쌍이 같은
판정을 씀 · 모르는 글자·빈 문자열·`null` 이 안 던짐 · 일곱 문자열이 `이(가)` 를 **안 담음**.

### 2.4 이모지·장식 기호

**i18n 문자열에 HTML 을 넣지 않는다.** 오늘 `data-i18n-html` 은 `token.help` 하나뿐이고
(`index.html:73`), 그 하나가 예외로 남아 있는 것이 값이다. 모양은 `app.js`/`index.html` 의 렌더
쪽에서 붙인다.

```js
/** 인라인 SVG 아이콘. 색은 `currentColor`, 크기는 1em — 문장 흐름에 붙어 산다.
    i18n 문자열에는 태그를 넣지 않는다(카탈로그는 글자만 담는다 — `token.help` 가 유일한 예외다). */
var ICON = { key: '<svg …>', chain: '<svg …>', chevronDown: '…', chevronUp: '…' };
function icon(name) { return '<span class="ic" aria-hidden="true">' + ICON[name] + "</span>"; }
```

| 자리 | 지금 | 어떻게 |
|---|---|---|
| `token.unverified`·`read_auth`·`bad_button`·`named`·`add` (431–434·587, `index.html:32`) | 🔑 | 문자열에서 뗀다. `renderTokenButton`(`app.js:1151`)과 `index.html` 버튼이 `icon("key")` |
| `reason.stuck` | ⚠ | **없앤다.** 빨강은 왼쪽 레인이 이미 말한다(§3) |
| `reason.blocked_by` (637) | ⛓ | 문자열에서 뗀다. `reasonText` 의 `blocked` 가지(`app.js:218-227`)가 `icon("chain")` |
| `queue.more`(492) · `recent.show_more`/`show_fewer`(765·766) | ▾ ▴ | 문자열에서 뗀다. 버튼 마크업(`app.js:1446`·`1829`)이 `icon("chevron*")` |
| `row.uploading`(505) · `row.cancelling`(506) | ↑ ■ | **죽은 키다** — `app.js` 가 필을 직접 조립한다(`app.js:1519-1521`). 두 언어에서 **지운다** |

**필의 글리프(`GLYPH`, `app.js:22`)는 건드리지 않는다** — 결정 A2(§8). `▶ ○ ↑ ■ ✓ …` 는 장식이
아니라 `style.css:2-3` 이 선언한 **모양 채널**이고, `tests/test_web_layout.py:235-256` 이
「필마다 `aria-hidden` 글리프와 글자가 둘 다 있다」를 WCAG 1.4.1 로 잠근다(`PILLS_JS` 가
`g.textContent.trim()` 을 본다 — SVG 는 빈 문자열이라 그 자리에서 빨개진다).

### 2.5 EN 카탈로그

키 집합은 `tests/web/i18n.test.js:27` 이 정확 비교로 잠근다 — **새 키는 반드시 양쪽에**.

| 키 | EN |
|---|---|
| `reason.stuck` | `Not responding` (결정 A1) |
| `pbar.stuck` | `not responding` |
| `reason.quiet` (신규) | `output has gone quiet` |
| `pbar.quiet` (신규) | `quiet` |
| `reason.step_over` (신규) | `step <name> is N× its usual time` |
| `reason.times_expected` | `N× longer than usual` |
| `queue.col_*` | `Job · Job name · Requested by · Status · Elapsed · Finishes · Source` |
| `summary.stuck` | `Needs a look` |
| `summary.nothing_stuck` | `Everything is fine` |
| `state.running` · `state.busy` | `Running` (영어는 그대로 — 한국어만 갈라 쓴다) |

`pbar.none` 의 EN `progress —` 는 **안 바꾼다** — `tests/test_web_browser.py:907` 이 글자 그대로
잠그고 있고 바꿀 이유가 없다.

---

## 3. 덩어리 (3) — 레인 시각 위계

### 3.1 원칙

**한 행에서 상태를 말하는 채운 칩은 하나.** 한 작업의 이상은 한 곳에서만 빨강으로 말한다.

(2026-09-15 좁힘. 처음에 「한 행에 채운 칩은 하나」로 적었는데, 그러면 신뢰도 배지(`.conf.*`)가
원칙을 어긴다. 그 배지는 잡의 상태가 아니라 **완료 예상을 얼마나 믿나**를 말하고 자리도 「완료
예상」 칸이다 — 소음의 원인이던 중복은 **같은 사실을 세 번 칠하는 것**이지 다른 두 사실이 각자
칩을 갖는 것이 아니다. 근거는 `style.css` 의 `.conf` 위 주석에, 잠그는 시험은
`tests/test_web_layout.py::test_a_row_never_shows_two_filled_status_chips` 에 있다.)

### 3.2 `style.css` 변경

| 선택자 | 지금 | 새 규칙 |
|---|---|---|
| `table.q tr.mine td:first-child`(119) · `tr.overdue …`(120-121) | `inset 3px/4px` 그림자 | **행 전체의 왼쪽 레인**으로 정리: `td:first-child` 에 `inset 3px 0 0 <상태색>`. `--accent`(진행) · `--bad`(응답 없음) · `--warn`(초과) · `--queued`(대기·조용함) |
| `.stuck`(191) | `background: var(--bad-soft)` 칩 | **배경을 없앤다.** `color: var(--bad); font-weight: 600` 한 줄 텍스트 |
| `.stalled`(190) · `.blocked`(188) | 빗금/점선 칩 | 같은 규칙으로 낮춘다 |
| `.quiet` (신규) | — | `color: var(--muted)` 만. 경보가 아니다 |
| `tr.qbar`(137) · `tr.hasbar`(136) · `tr.qbar + tr.expanded`(138) | 별도 막대 행 | **없앤다** |
| `.pwrap`(139) · `.pbar`(140) | `max-width: 720px`, `flex: 1 1 auto` | `.q td.elapsed .pbar { width: 84px; flex: 0 0 84px; height: 7px }` |
| `th:nth-child(5)`(109) | `width: 96px` | `width: 168px` |
| `.summary .sum`(71) | 격자 칸 | 카드: `border: 1px solid var(--line); border-radius: 8px; padding: 11px 12px; border-top: 3px solid <색>` |
| `.hostcard`(237) | 이름 + 미터 3~4 | 지표 카드 5장, `repeat(auto-fit, minmax(150px, 1fr))` |
| `.rrow`(264) | 격자 행 | 왼쪽 3px 레인: 성공 `--ok` · 실패 `--bad` · 취소 `--queued` · 결과를 잃음 `--lost` |

**색 토큰은 새로 만들지 않는다.** `style.css:7-9` 의 대비 주석(WCAG 1.4.3 재계산 기록)은 **필의
글자색**에 대한 기록이다 — 새 규칙은 글자색을 안 건드리고 **배경만 없앤다**. 그래도
`.stuck`·`.stalled`·`.blocked` 세 색을 새 배경(=칸 배경) 위에서 **다시 재고 주석을 갱신한다**.

### 3.3 `app.js` 변경

- `queueRowHtml`(`app.js:1469`): `progressBarHtml` 을 `elapsedCell` 안으로. `hasbar`/`tr.qbar`
  (`app.js:1540`)를 지운다. `reasonCell` 의 세 갈래 칩 분기(`app.js:1516`)를 한 줄로. `cls` 에
  `stuck`·`quiet`·`overdue`·`mine` 을 실어 레인 색을 고른다.
- `progressBarHtml`(`app.js:745`): 라벨은 막대 아래 작은 글씨. `data-basis`/`data-cond`/`aria-*`/
  `data-fill` 은 **전부 그대로** — `applyBarFills`(`app.js:1382`)가 표 칸 안 퍼센트 폭 버그를 막는
  유일한 장치다(`app.js:1373-1379` 주석이 실측 기록).
- `overallProgress`(`app.js:706`): `est.quiet` → `condition = "quiet"`. **`stuck` 이 `quiet` 를 이긴다.**
  ⚠️ `mutcheck` 가 이 함수의 문자열 셋을 정확히 매치한다 — 다음 셋은 **글자 하나도 바꾸지 않는다**:
  `mutcheck.py:153` · `:162` · `:170` 의 패턴.
- `reasonText`(`app.js:188`): `case "stuck"` 을 `stuck_code` 로 갈라 쓴다
  (`over_step`/`over_elapsed`/`no_output`). `reason.times_expected` 는 `stuck_code === "over_elapsed"`
  일 때만. `stuck_code` 없는 옛 문서는 `floor(elapsed/expected) >= 2` 일 때만 — 「예상의 1배」가
  절대 안 나온다. `case "quiet"` 를 더한다(`actionable = false`, `cls = "quiet"`).
- `notMoving`(`app.js:397`) — **안 고친다.** `ACTIONABLE`(`app.js:16`)에 `quiet` 를 안 넣었으므로
  자동으로 패널에 안 뜬다. ⚠️ `mutcheck.py:179` 가 이 함수 앞 두 줄을 정확히 매치한다.
- `renderSummary`(`app.js:1311`) · `renderHost` · `renderRecent` — 카드·레인 클래스.
- `queueHeadHtml`(`app.js:1388`) — **`colspan="7"` 세 자리(`app.js:1438`·`1440`·`1541`)** 확인.

시안(`Main.dc.html`)은 **그림**이다. 치수(3px 레인 · 84px 막대 · 카드 `border-top: 3px`)만 가져온다.

---

## 4. 순서

| 단계 | 하는 일 | 왜 이 자리인가 |
|---|---|---|
| **A** | `model.py` 새 상수·필드, `queue.py` `StepMedian`·`step_medians_from`·판정식, `config.py` 두 키. **시험 먼저**(사고 재현: 49/49 단계 · 침묵 7m53s · 예상의 1배 → `quiet`) | 나머지 전부가 이 상태 이름에 기댄다 |
| **B** | `store.list_step_sample_ids` + `server.py` 배선 + 캐시 | A 의 함수를 실제 데이터로 먹인다 |
| **C** | `status.py` 직렬화 + `render_text.py` CLI 문면 | 화면이 읽을 JSON 이 여기서 확정 |
| **D** | `i18n.js` — 조사 헬퍼 + `josa.test.js` **먼저**, 그다음 KO 전면 교체, 그다음 EN | 헬퍼가 먼저 있어야 일곱 문자열을 한 번에 |
| **E** | `app.js` 이유 칸·`overallProgress`·아이콘 | D 의 키가 있어야 한다(없는 키는 `i18n.t` 가 던진다 — `i18n.js:825`) |
| **F** | `style.css` 레인·카드 + `app.js` 막대 이동 | 가장 시끄러운 변경을 마지막에 |
| **G** | 브라우저 시험 갱신 · 문서 · 스크린샷 · CHANGELOG | F 가 DOM 을 확정한 뒤 |

각 단계 끝에 `ruff check . && ruff format --check . && pytest` + `node --test tests/web/*.test.js`.
E·F 뒤에는 `python scripts/mutcheck.py` 도 돌린다.

---

## 5. 기존 테스트 영향 (grep 으로 확인 — 추측 아님)

### 5.1 반드시 깨진다 — 고쳐야 함

| 파일:줄 | 무엇 | 어떻게 |
|---|---|---|
| `tests/test_status_schema.py:48-60` `ESTIMATE_KEYS` / `:244` | estimate 키 집합 **정확 비교** | 세 키를 집합에 더한다. 주석에 「키 추가만 — 스키마 v1 그대로」 |
| `tests/web/reason.test.js:165-182` | `"⚠ likely stuck · 3× expected · no output for 4m"` 3곳 | 새 문면 + `stuck_code` 갈래. **근거별 세 케이스**를 새로 잠근다 |
| `tests/web/summary.test.js:53`·`:57` | Not-moving 순서·문면 | 순서 그대로(`quiet` 미포함). `:57` 은 픽스처가 3배라 새 규칙에서도 붙는다 — **문면만** |
| `tests/web/progress_overall.test.js:271` | `"50% · 4/8 steps · likely stuck"` | EN `pbar.stuck`. `quiet` condition 케이스 추가 |
| `tests/web/i18n.test.js:27` | 두 언어 키 집합 동일 | 새 키 5개를 양쪽에, 죽은 키 2개를 양쪽에서 삭제 |
| `tests/web/i18n.test.js:56-81` | 모든 키가 두 언어에서 비어 있지 않음 | `args` 표(`:58-72`) 확인. `reason.step_over` 는 기존 인자(`step`,`n`)를 쓴다 |
| `tests/web/i18n.test.js:117` | `stateWord("running","ko") === "실행 중"` | → 「진행 중」 (결정 A3) |
| `tests/web/m5h.test.js:82-88` | `"스텝 test"` 등 4곳 | 「스텝」 → 「단계」 |
| `tests/web/artifacts.test.js:93` | `ko.text.includes("산출물")` | → 「결과 파일」 |
| `tests/test_web_browser.py:608` | `"안 움직이는 것"` | → 「확인이 필요한 작업」 |
| `tests/test_web_browser.py:611` | `"실행 중" in body` | → 「진행 중」 |
| `tests/test_web_browser.py:849-850`·`874-925` `FOLD_JS` | `tr.qbar[data-bar]`, `folded["bars"] == 1` | 셀렉터를 `tr[data-job] td.elapsed .pbar` 로. `bars` 는 `#queue [role="progressbar"]` 수로 |
| `tests/test_web_browser.py:959-961`·`1024`·`1031`·`1043` | 같은 `tr.qbar` 셀렉터 | 같은 방식. **`drawn` == `valuenow` (±1%) 단언(`:1083`)은 반드시 지킨다** |
| `tests/test_examples.py:153` | `examples/server.toml` 실제 파싱 | 새 두 키를 `EstimateSection` 과 `examples/server.toml` 에 **같이** |

### 5.2 초록으로 살지만 보강해야 하는 것

| 파일:줄 | 왜 |
|---|---|
| `tests/test_queue.py:271-277` | `rows_for` 가 `progress` 를 안 넘겨 폴백이 오늘과 같게 돈다. **이름을 바꾸고** 새 케이스 셋: ① 단계 실측 + 3배 초과 → `stuck`/`over_step` ② 단계 실측 + 정상 + 침묵 → `quiet`(2026-09-15 사고) ③ 단계는 도는데 실측 없음 + 침묵 → `quiet` |
| `tests/test_queue.py:262-268`·`280-283` | `overdue`·materializing 에서 `quiet` 도 함께 단언 |
| `tests/web/state_marks.test.js:29-40` | `state.*` 에서 잡 상태 9개를 뽑는다 → **`state.quiet` 를 만들면 즉사.** 안 만드는 것이 결정. 회귀 방지 주석을 `i18n.js` 에 |
| `tests/test_web_layout.py:235-256` | 필 글리프를 SVG 로 바꾸면 깨진다 → §2.4 결정으로 초록 유지 |
| `scripts/mutcheck.py:150-183`·`484-505` | §3.3 의 「글자 하나도 바꾸지 않는다」를 지키면 초록. **E·F 뒤에 반드시 한 번** |
| `tests/web/queue_groups.test.js:98·123-137` | 구조로 본다 → 무사 |
| `tests/web/job_storage.test.js:88·125` | 새 규칙과 같은 꼴 → 건드리지 않는다 |
| `tests/test_changelog_pr_links.py` | `[Unreleased]` 의 글자와 주소 번호가 같아야 한다 |

### 5.3 새로 만드는 시험

- `tests/web/josa.test.js` — §2.3 목록(필수).
- `tests/web/stall.test.js` — `stuck_code` 세 갈래 + `quiet` + 「예상의 1배는 절대 안 나온다」 +
  `stuck_code` 없는 옛 문서의 폴백.
- `tests/test_queue.py` 에 `step_medians_from` 시험(표본 미달 · 도는 단계 제외 · 이름이 신원 · 빈 입력).
- `tests/test_render_text.py` 에 CLI `stuck`/`quiet` 문면(오늘 `render_text.py:396` 을 잠그는 시험이
  **없다** — 이 브랜치가 그 빈자리를 채운다).
- `scripts/mutcheck.py` 변이 2종: `step-stuck-ignores-step-median` · `web-quiet-is-actionable`.
  `AGENTS.md:75`·`CONTRIBUTING.md:13` 의 「38」을 **40** 으로.

---

## 6. 문서 영향

`.claude/skills/docs/SKILL.md` 체크리스트를 따른다.

1. **`CHANGELOG.md` `[Unreleased]`** — `### Changed` 세 항목 + PR 링크. 멈춤 판정(사고·`quiet` 가
   경보가 아니라는 것·**`schema_version` 을 안 올린 근거**·새 설정 키) / 한국어 문면 전면 교체 /
   큐 화면 시각 위계.
2. **`docs/configuration.md`** — `[estimate]` 에 두 키. 판정 순서가 **단계 실측 → 침묵 폴백** 임을
   적고, `no_output_seconds` 의 설명을 「조용함 판정과, 단계 정보가 없을 때의 마지막 폴백」으로.
3. **`examples/server.toml`** (`:56-63`) — 두 줄.
4. **`docs/usage.md` / `docs/usage.ko.md` `:293-297`** — 진행 막대 설명, 「likely stuck」/「멈춘 듯」.
5. **`docs/usage.md:279`** — "tell 'stuck' from 'the machine is busy'".
6. **`README.md:239` / `README.ko.md:224`** — 「Not moving」/「안 움직이는 것」 → 새 이름, 🔑 표기.
7. **`PLAN.md` 「큐 규칙」 `:120`·`:113`** — 정본. 새 규칙 · `quiet` · 두 설정 키. `quiet` 를
   **`ACTIONABLE_REASONS` 에 안 올린다**를 명시.
8. **`docs/wireframes/web-queue.html` `:643-644`·`:665`·`:668`** — 항목 25 의 옛 규칙 세 곳.
9. **스크린샷** — `capture.py all && build.py`. 6장: `web-queue` · `web-header` · `web-host` ·
   `web-recent` · `web-your-jobs` · `hero-queue`. `build.py` 의 ①②③ 번호와 두 사용 안내의 번호를 맞춘다.
10. **`AGENTS.md:75` · `CONTRIBUTING.md:13`** — mutcheck 38 → 40.
11. 마친 뒤 `pytest tests/test_docs_m5*.py tests/test_examples.py` + `ruff check .`.

---

## 7. 위험과 되돌릴 수 있는 지점

| 항목 | 되돌릴 수 있나 | 판단 |
|---|---|---|
| **설정 키 이름** | **어렵다** — 남의 `server.toml` 에 들어가면 이름 변경 시 기동 실패(`config.py:365`) | 기존 어휘와 같은 `step_` 접두어. 개수를 **둘로 묶었다**(표본 잡 수는 모듈 상수) |
| **`schema_version`** | 한 방향 | 안 올린다(§1.9). 올려야 했을 조건을 CHANGELOG 에 남긴다 |
| **`reason` 값 `quiet`** | 추가뿐 | 옛 클라이언트는 `unknown` 으로 받는다 |
| **DB** | 변경 없음 | 마이그레이션 없음 → 롤백도 없다 |
| **한국어 문구** | 쉽다 | 카탈로그 한 파일 |
| **진행바를 칸 안으로** | 코드는 쉽다 / **정확성은 위험** | `applyBarFills` 의 실측 기록 — 자동 폭 칸에서 25% 막대가 가득 차 보였다. **84px 고정폭**이 줄이지만 `test_web_browser.py:1080-1084` 의 「그려진 길이 == aria-valuenow (±1%)」를 반드시 살린다 |
| **라이트 판 대비** | 눈으로 재야 한다 | 배경을 없애는 방향이라 대비는 올라가지만 세 색을 **다시 재고 주석 갱신** |
| **새 key 의 첫 며칠** | 자가 치유 | §1.4-③ 로 `quiet` 만. 진짜 죽은 잡은 `3×expected` 와 `timeout_seconds` 가 잡는다 |
| **성능** | 되돌릴 수 있다 | key 당 질의 2회 + TTL 캐시. 도는 잡 없으면 0회. `tests/test_status_perf.py` 에 「보존 이벤트 수에 안 끌린다」 시험 추가 |

---

## 8. 열어 둔 질문 — 전부 닫았다

| # | 질문 | 결정 | 근거 |
|---|---|---|---|
| **A1** | EN `stuck` 문구 | **`Not responding`** | 브리핑의 `No output` 은 새 `quiet`(=출력이 조용하다)와 영어에서 같은 말이 된다. 더구나 새 규칙에서 `stuck` 의 주된 근거는 출력이 아니라 **단계 소요**다. 한국어 「응답 없음」과 짝이 맞는다 |
| **A2** | 필 글리프 `↑`·`■` 도 SVG 로? | **아니다 — 필 글리프는 그대로** | `style.css:2-3` 이 선언한 모양 채널이고 `tests/test_web_layout.py:249` 가 WCAG 1.4.1 로 잠근다. 브리핑이 센 12곳 중 이 둘은 **죽은 카탈로그 키**의 것이라 그 키만 지우면 끝난다 |
| **A3** | `state.running` 도 「진행 중」? | **그렇다** (`state.busy` 도 따라간다) | 오너 지시가 「묶음 머리는 작업 중, 개별 행 상태는 진행 중」이고 행의 상태 필이 곧 개별 행 상태다. EN 은 `Running` 그대로 |
| **A4** | §1.4-③ 의 좁힘 | **채택** | 글자 그대로 가면 이력 없는 새 key 의 `gate` 가 오늘과 똑같이 매번 빨개진다 — 고치려던 바로 그 사고다. 진짜로 죽은 잡의 안전망은 `3×expected` 와 프리셋 `timeout_seconds` 로 둘 남는다 |
| **A5** | `step_min_samples` 기본값 | **3** | 단계 소요는 잡 소요보다 시끄럽다(캐시 적중·병렬도·부하가 단계 단위로 다르다). `gate` 는 하루에 여러 번 도니 반나절이면 찬다 |
