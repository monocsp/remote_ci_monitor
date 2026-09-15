# 엣지케이스 · 시험 시나리오 — 웹 큐 쉬운 한국어 · 멈춤 판정 규칙 · 레인 시각 위계

정본은 [`web-queue-plain-korean-stall-rule.md`](web-queue-plain-korean-stall-rule.md) 이다. 이 문서는
그 계획서를 **공격하기 위한 대본**이다. 격리된 검증 에이전트가 이것만 읽고 그대로 따라 해서
구현을 깨뜨릴 수 있어야 한다 — 그래서 시나리오마다 「무엇을 만들어 넣는지」와 「무엇이 나와야
하는지」를 숫자로 적었다.

읽는 법:

- **어디** — 손대는 소스와 시험 파일.
- **넣는 것** — 재현 입력. 값은 그대로 쓸 수 있게 적었다.
- **나와야 하는 것** — 단언. `???` 는 없다. 계획서가 안 정한 자리는 여기서 못 박고 **「계획서
  구멍」**으로 표시했다.
- **위험도** — 높음 / 보통 / 낮음.
- **자동화** — 새 시험 함수 이름, 또는 「사람 눈」.

계획서가 안 정한 것을 이 문서가 정한 자리는 전부 **[결정 필요]** 로 표시했다. 구현 전에 오너가
한 번 읽어야 한다.

공통 기준값(달리 적지 않으면 이 값이다):

| 이름 | 값 |
|---|---|
| `cfg.stuck_multiplier` | 3.0 |
| `cfg.step_stuck_multiplier` | 3.0 |
| `cfg.step_min_samples` | 3 |
| `cfg.min_samples` | 2 |
| `cfg.no_output_seconds` | 240 |
| `NOW` | `datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)` |

---

## 0. 이 문서가 먼저 못 박는 세 가지

구현하기 전에 읽어야 하는 것이 셋 있다. 아래 시나리오들이 전부 이 셋에 기댄다.

### 0.1 아주 짧은 단계의 하한 — **필요하다** [결정 필요]

계획서 §1.4 의 `step_stuck = cur_seconds > cfg.step_stuck_multiplier * m.seconds` 는
`m.seconds` 가 작을 때 그대로 무너진다. `lint` 의 중앙값이 0.5초면 1.5초 만에 `stuck` 이고,
중앙값이 정확히 0.0초면 (한 단계가 같은 초에 시작하고 끝난 경우 — 마커 두 줄이 같은
`at` 을 받으면 실제로 그렇다) 임계가 0 이 되어 **그 단계는 시작하자마자 stuck** 이다.

이 문서가 못 박는 값:

```python
# 단계 임계는 침묵 창보다 짧아질 수 없다. 0.5초짜리 단계의 3배(1.5초)로 「죽었다」고
# 말하는 것은, 침묵 규칙이 절대 못 하던 주장을 단계 규칙이 하게 두는 것이다.
threshold = max(cfg.step_stuck_multiplier * m.seconds, cfg.no_output_seconds)
step_stuck = cur_seconds > threshold
```

**새 설정 키를 만들지 않는다**(계획서 §7 — 되돌리기 어려운 이름을 하나라도 덜 만든다).
`no_output_seconds` 를 하한으로 재사용하면 규칙이 한 문장으로 읽힌다: *「우리는 침묵 규칙이
낼 수 있었던 것보다 빨리 죽었다고 말하지 않는다」*. 기본값 240초에서 이 하한이 실제로 이기는
구간은 단계 중앙값 80초 미만이다.

2026-09-15 사고에는 영향이 없다: 중앙값 9m(540s) × 3 = 1620s > 240s 라 하한이 안 걸린다.

### 0.2 `overallProgress` 의 `condition` 우선순위 [결정 필요]

계획서 §3.3 은 「`stuck` 이 `quiet` 를 이긴다」만 적었다. `over` 와 `finalizing` 은 안 적었다.
이 문서가 못 박는 순서:

```
preparing  >  stuck  >  over  >  finalizing  >  quiet  >  normal
```

근거: `quiet` 는 경보가 아니라 **관측**이고, 「추정을 넘겼다」(`over`)·「선언한 단계를 다 끝냈다」
(`finalizing`)는 더 행동 가능한 사실이다. 서버의 `_busy_reason` 우선순위
(`… stuck → overdue → quiet → running`)와 같은 방향이다.

### 0.3 「예상의 1배」가 나오던 진짜 경로

`reasonText` 의 `case "stuck"` 안에서만 `reason.times_expected` 를 붙인다. 2026-09-15 에
`floor(1044 / 1020) == 1` 이라 「예상의 1배」가 나왔다. 새 규칙에서 그 잡은 `stuck` 이 아니므로
**그 가지에 도달하지 않는다**. 즉 「1배」를 막는 1차 방어선은 `stuck_code` 분기가 아니라
「stuck 이 아니다」이고, `stuck_code` 분기는 2차 방어선이다. 시나리오 C-45·C-46 이 둘을 따로 잠근다.

---

## 1. 멈춤 / 조용함 판정 (`core/queue.py`)

### C-1 · 단계 실측이 정확히 배수 경계에 있다
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** key `gate:full`, 단계 `test` 중앙값 `m.seconds = 600.0`(표본 3),
  `progress.current_name = "test"`, `progress.current_seconds = 1800.0`(정확히 3배),
  `elapsed = 1900`, `expected = 3600`, 침묵 아님
- **나와야 하는 것** `est.stuck is False`, `est.stuck_code is None`.
  `1800.0 > 3 * 600.0` 은 거짓이다 — **`>` 이지 `>=` 가 아니다**. 기존
  `elapsed > cfg.stuck_multiplier * expected` 와 `overdue = elapsed > expected` 가 둘 다 `>` 라
  이 자리만 `>=` 로 두면 규칙 셋이 서로 다른 말을 한다.
  같은 입력에서 `current_seconds = 1800.001` 이면 `stuck is True`, `stuck_code == "over_step"`.
- **왜 위험한가** 경계 한 칸 차이가 정상 실행을 매번 빨갛게 만든다 — 고치려던 바로 그 사고다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_step_exactly_at_the_multiplier_is_not_stuck`

### C-2 · 아주 짧은 단계의 오경보 (하한)
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** key `fast`, 단계 `lint` 중앙값 `0.5`(표본 3), `current_name = "lint"`,
  `current_seconds = 2.0`, `elapsed = 40`, `expected = 120`, 침묵 아님
- **나와야 하는 것** `est.stuck is False`. §0.1 의 하한으로 임계는
  `max(3 * 0.5, 240) = 240` 초다. 같은 단계가 `current_seconds = 240.0` 이어도 `stuck is False`
  (`>` 경계), `240.001` 에서 처음 `stuck is True` · `stuck_code == "over_step"`.
- **왜 위험한가** 하한이 없으면 짧은 단계를 가진 모든 프리셋이 매 실행마다 1.5초 만에 빨개진다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_half_second_step_never_goes_stuck_in_two_seconds`

### C-3 · 중앙값이 정확히 0 초인 단계
- **어디** `core/queue.py::step_medians_from` · `_busy_estimate` · `tests/test_queue.py`
- **넣는 것** 과거 성공 실행 3개에서 단계 `noop` 의 `seconds` 가 `0.0`(같은 초에
  `::rcm::step::noop` 과 `::rcm::step-end::ok` 가 찍혔다). 현재 잡이 `noop` 을 0.2초째 돌고 있다
- **나와야 하는 것** `step_medians_from` 은 `StepMedian(seconds=0.0, sample_count=3)` 을
  **그대로 담는다**(없는 숫자를 만들지 않는다 — 0 은 실측이다). `_busy_estimate` 는
  §0.1 하한으로 임계 240 초를 써서 `stuck is False`.
  하한이 없으면 `0.2 > 3 * 0.0` 이 참이라 **시작하자마자 stuck** 이다.
- **왜 위험한가** 0 은 곱셈으로 못 막는다. 하한만이 막는다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_zero_second_median_does_not_make_every_step_stuck`

### C-4 · `step_min_samples` 경계 — 표본 2개
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** key `gate:full` 의 성공 실행 2개, 둘 다 단계 `build` 가 `state="done"`,
  `seconds` 는 `100.0` · `140.0`
- **나와야 하는 것** `step_medians_from({...}, cfg)["gate:full"]` 에 `"build"` 키가 **없다**
  (`dict` 에 넣지 않는다 — `StepMedian(sample_count=2)` 를 넣고 호출자가 거르게 하지 않는다).
  판정은 §1.4-③ 으로 떨어져 `stuck is False`, 침묵이면 `quiet is True`.
- **왜 위험한가** 2개는 「중앙값」이 아니라 「둘 중 하나」다. 이상치 한 번이 임계를 통째로 옮긴다.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_two_samples_are_not_enough_for_a_step_median`

### C-5 · `step_min_samples` 경계 — 표본 정확히 3개
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** C-4 와 같되 실행 3개, `build` 의 `seconds` 가 `100.0` · `140.0` · `120.0`
- **나와야 하는 것** `["gate:full"]["build"] == StepMedian(seconds=120.0, sample_count=3)`.
  중앙값이지 평균(`120.0` 과 우연히 같아지지 않게 값을 `100 · 140 · 400` 으로 바꾸면
  중앙값 `140.0`, 평균은 `213.3` — **`140.0` 이 나와야 한다**).
- **왜 위험한가** `statistics.median` 대신 `mean` 을 쓰면 한 번의 느린 실행이 임계를 두 배로 만든다.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_three_samples_give_the_median_not_the_mean`

### C-6 · 현재 단계 이름이 과거 실행에 없다
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** 과거 3개 실행의 단계는 `lint` · `test` · `build`. 현재 잡의
  `current_name = "e2e"`(스크립트가 단계를 추가했다), `current_seconds = 900`,
  `elapsed = 1000`, `expected = 1800`, 침묵 7분
- **나와야 하는 것** `m is None` → §1.4-② 조건(`progress is None or not progress.steps`)이
  **거짓**이다(단계는 돈다) → ③ 으로 떨어진다. `est.stuck is False`, `est.stuck_code is None`,
  `est.quiet is True`, `row.reason == "quiet"`.
- **왜 위험한가** ② 를 `m is None` 으로 잘못 쓰면 스크립트에 단계 하나 추가한 날 전부 빨개진다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_step_name_the_history_never_saw_only_goes_quiet`

### C-7 · 과거 실행마다 단계 개수가 다르다 (조건부 단계)
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** 성공 실행 5개. `lint` 는 5개 모두에 있고, `e2e` 는 그 중 2개에만 있다
  (`seconds` 300 · 340). 현재 잡이 `e2e` 를 700초째 돌고 있다
- **나와야 하는 것** `["e2e"]` 는 `sample_count = 2` 라 **표에 없다**. `["lint"]` 는 있다.
  → `est.stuck is False`. **번호로 맞추면 안 된다** — 실행마다 `e2e` 의 `index` 가 다르다.
- **왜 위험한가** 「단계의 신원은 이름」이 무너지는 첫 자리다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_conditional_step_counts_only_the_runs_that_had_it`

### C-8 · 같은 이름의 단계가 한 실행에 두 번 (루프) [결정 필요]
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** 성공 실행 **1개** 안에 `retry` 가 세 번(`seconds` 10 · 20 · 30). 다른 실행에는
  `retry` 가 없다
- **나와야 하는 것** `sample_count == 1` — **실행 수를 센다, 출현 수가 아니다**.
  따라서 `step_min_samples = 3` 을 못 채워 표에 안 들어간다.
  한 실행 안의 여러 출현은 그 실행의 **중앙값 하나**(`20.0`)로 접는다.
  실행 3개가 각각 `retry` 를 세 번씩 돌았다면 `sample_count == 3`, 표본값은 각 실행의
  중앙값 셋이다.
- **왜 위험한가** 출현 수를 세면 **루프 도는 스크립트 하나가 혼자 표본을 채운다** — 한 번의
  실행으로 「평소」를 정의하게 된다. 계획서는 이 경우를 말하지 않는다(**계획서 구멍**).
  비교 대상인 `progress.current_seconds` 는 **한 번의 출현**이므로 표본도 한 번의 출현이어야
  단위가 맞는다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_looping_step_contributes_one_sample_per_run`

### C-9 · `progress.current_seconds` 가 `None`
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `progress.steps` 는 비어 있지 않고 `current_name = "build"`, 그런데
  `current_seconds = None`(모든 단계가 닫혔고 다음 단계 마커가 아직 안 왔다). 단계 `build` 의
  중앙값 600(표본 3). 침묵 7분
- **나와야 하는 것** `cur_seconds = progress.current_seconds or 0.0` → `0.0`.
  `0.0 > max(1800, 240)` 은 거짓 → `stuck is False`, `quiet is True`.
  **`None` 을 큰 수로 읽거나 예외를 내면 안 된다.**
- **왜 위험한가** `or 0.0` 이 빠지면 `TypeError` 로 `/api/status` 가 500 이 된다.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_a_none_current_seconds_is_zero_not_an_error`

### C-10 · `progress.current_seconds` 가 정확히 0
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** 단계 `build` 가 방금 시작(`current_seconds = 0.0`), 중앙값 600(표본 3)
- **나와야 하는 것** `stuck is False`. (C-3 과 합치면: 중앙값 0 · 현재 0 이어도 `False`.)
- **위험도** 낮음
- **자동화** C-9 와 같은 함수에 단언 추가

### C-11 · 단계 이야기를 아예 안 하는 잡 + 침묵 → 오늘 규칙
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `progress = None`, `elapsed = 500`, `expected = 1800`,
  `last_output_at = NOW - 300s`(> 240), `phase != materializing`
- **나와야 하는 것** §1.4-② 가 탄다. `stuck is True`, `stuck_code == "no_output"`,
  `quiet is False`(`quiet = silent and not stuck`), `row.reason == "stuck"`.
- **왜 위험한가** 마커를 전혀 안 찍는 잡의 안전망이 여기 하나뿐이다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_job_with_no_steps_still_falls_back_to_silence`

### C-12 · `progress.steps` 가 빈 튜플 (Progress 는 있는데 단계가 없다)
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `Progress(phase="executing", steps=(), current_name=None)`, 침묵 300초
- **나와야 하는 것** ② 가 탄다(`not progress.steps` 가 참) → `stuck is True`,
  `stuck_code == "no_output"`. `progress is None` 만 보면 이 잡이 ③ 으로 새 나간다.
- **위험도** 보통
- **자동화** C-11 과 같은 함수에 단언 추가

### C-13 · 단계를 다 끝내고 조용해진 잡 (`current_name is None`, `steps` 는 있다) [결정 필요]
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `steps` 49개가 전부 `state="done"`, `current_name = None`,
  `current_seconds = None`, 침묵 20분(1200초), `elapsed = 2000`, `expected = 1800`
- **나와야 하는 것** `cur is None` → `m is None`; ② 는 `progress.steps` 가 비어 있지 않아
  거짓 → ③. **`stuck is False`, `quiet is True`, `reason == "overdue"`**(overdue 가 quiet 을
  이긴다).
  이 자리는 **의도된 구멍**이다: 선언한 단계를 다 끝내고 20분을 조용히 있어도 `stuck` 이
  아니다. 안전망은 `elapsed > 3 × expected`(5400초)와 프리셋 `timeout_seconds` 둘뿐이다.
  계획서 §1.4-③ 이 이 경우를 말하지 않는다(**계획서 구멍**) — 채택하되 `queue.py` 주석에
  「단계를 다 끝낸 뒤의 침묵도 ③ 이다」를 한 줄로 남긴다.
- **왜 위험한가** `gate` 의 마지막 단계가 끝난 뒤 산출물 업로드에서 죽으면 이 자리에 온다.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_a_job_past_its_last_step_is_quiet_not_stuck`

### C-14 · `CANCELLING` 에서는 둘 다 안 난다
- **어디** `core/queue.py::_busy_estimate` · `_busy_reason` · `tests/test_queue.py`
- **넣는 것** `job.state = CANCELLING`, `elapsed = 10000`, `expected = 600`(3배 초과),
  침묵 1000초, 현재 단계가 자기 중앙값의 10배
- **나와야 하는 것** `est.stuck is False` **그리고** `est.quiet is False`
  (`quiet = silent and not stuck and job.state != CANCELLING`), `reason == "cancelling"`.
  `est.overdue is True`(overdue 는 상태를 안 본다 — 오늘 동작 그대로).
- **왜 위험한가** 취소 중인 잡은 정의상 조용하다. `quiet` 를 내면 취소할 때마다 회색 칩이 뜬다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_cancelling_is_neither_stuck_nor_quiet`

### C-15 · `PHASE_MATERIALIZING` 에서는 둘 다 안 난다
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `job.phase = PHASE_MATERIALIZING`, `last_output_at = NOW - 900s`,
  `elapsed = 900`, `expected = 1800`, `progress = None`
- **나와야 하는 것** `silent is False`(materializing 은 침묵 조건에서 빠진다) →
  `stuck is False`, `quiet is False`, `reason == "materializing"`.
  48 MB 트리를 푸는 동안 출력이 없는 것은 정상이다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_materializing_is_neither_stuck_nor_quiet`

### C-16 · `overdue` 와 `quiet` 이 동시에 참
- **어디** `core/queue.py::_busy_reason` · `tests/test_queue.py`
- **넣는 것** `elapsed = 2000`, `expected = 1800`(overdue), 침묵 500초(silent),
  단계 실측 있음 · 정상 범위(그래서 stuck 아님)
- **나와야 하는 것** `est.overdue is True`, `est.quiet is True`, **`reason == "overdue"`**.
  플래그는 둘 다 켜져 있고 **표시 사유만** overdue 다(더 행동 가능한 사실이 이긴다).
  화면은 `reason.over_by` 를 그린다.
- **왜 위험한가** 우선순위를 뒤집으면 초과 실행이 회색 「조용함」 뒤에 숨는다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_overdue_beats_quiet_in_the_reason`

### C-17 · `stuck` 과 `quiet` 은 절대 동시에 참이 아니다
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** 아래 네 조합을 전부 돌린다 —
  ① 침묵 + 단계 3배 초과, ② 침묵 + `elapsed > 3×expected`, ③ 침묵 + `progress=None`,
  ④ 침묵 + 단계 실측 없음
- **나와야 하는 것** 네 경우 모두 `not (est.stuck and est.quiet)`.
  ①②③ 은 `stuck is True, quiet is False`, ④ 는 `stuck is False, quiet is True`.
- **왜 위험한가** 둘 다 참이면 화면이 어느 색을 쓸지 서버가 안 정해 준 셈이 된다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_stuck_and_quiet_are_mutually_exclusive`

### C-18 · 잡이 방금 시작해 `last_output_at` 이 `None`
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `job.started_at = NOW - 5s`, `job.last_output_at = None`, `expected = 1800`,
  `progress = None`
- **나와야 하는 것** `last_output = job.last_output_at or started` → 침묵 5초 →
  `silent is False` → `stuck is False`, `quiet is False`, `reason == "running"`.
  이어서 같은 잡을 `started_at = NOW - 300s` · `last_output_at = None` 로 바꾸면
  `silent is True` → ② 로 `stuck is True`, `stuck_code == "no_output"`
  (한 줄도 안 뱉고 5분 지난 잡은 오늘도 stuck 이다 — 이 동작은 안 바뀐다).
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_a_job_that_never_printed_measures_silence_from_its_start`

### C-19 · 시계가 뒤로 갔다 (음수 경과)
- **어디** `core/queue.py::_busy_estimate` · `tests/test_queue.py`
- **넣는 것** `job.started_at = NOW + 120s`(NTP 보정으로 시계가 2분 뒤로 갔다),
  `last_output_at = NOW + 60s`, 단계 중앙값 600(표본 3), `current_seconds = -30.0`
- **나와야 하는 것** 예외 없음. `elapsed == -120.0`, `overdue is False`,
  `silent is False`(`_seconds` 가 음수), `stuck is False`, `quiet is False`,
  `reason == "running"`. `remaining_seconds` 는 하한 `floor_remaining_seconds` 이상.
  **음수를 0 으로 깎지 않는다** — 오늘 동작이 그렇고, 깎으면 「지어낸 숫자」다.
- **왜 위험한가** 노트북 뚜껑을 닫았다 여는 것만으로 화면이 빨개지면 안 된다.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_a_clock_that_went_backwards_raises_no_alarm`

### C-20 · 2026-09-15 사고 재현 (가) — 추정 안쪽
- **어디** `core/queue.py::_busy_estimate` · `core/status.py` · `tests/test_queue.py`
- **넣는 것** key `gate:full`. `started_at = NOW - 1044s`(17m 24s),
  `expected = 1080.0`(18m, `source="measured"`, `sample_count=6`),
  `last_output_at = NOW - 473s`(7m 53s), `phase="executing"`,
  `progress.steps_total = 49`, `steps_done = 48`, `current_index = 49`,
  `current_name = "build web"`, `current_seconds = 473.0`,
  단계 `build web` 의 `StepMedian(seconds=540.0, sample_count=5)`(9분)
- **나와야 하는 것**
  - `est.stuck is False` — `473.0 > max(3 × 540.0, 240) = 1620` 이 거짓
  - `est.stuck_code is None`
  - `est.step_expected_seconds == 540.0`
  - `est.overdue is False` — `1044 < 1080`
  - `est.quiet is True` — `473 > 240`
  - `row.reason == "quiet"`
  - `estimate_json(...)` 에 `{"quiet": True, "stuck_code": None, "step_expected_seconds": 540.0}`
  - 화면 문면에 **「배」가 한 글자도 없다**(`reason.times_expected` 가지에 도달하지 않는다)
- **왜 위험한가** 이 한 줄이 이 작업의 존재 이유다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_the_2026_09_15_gate_incident_is_quiet_not_stuck`

### C-21 · 2026-09-15 사고 재현 (나) — 추정 바깥 (경계 해석)
- **어디** `core/queue.py` · `tests/test_queue.py`
- **넣는 것** C-20 과 같되 `expected = 1020.0`(17m). `elapsed = 1044 > 1020`
- **나와야 하는 것** `est.overdue is True`, `est.quiet is True`, `est.stuck is False`,
  **`reason == "overdue"`**. 화면은 `reason.over_by`(「24s 초과 · 예상 17m」)를 그리고
  역시 **「배」가 안 나온다**.
- **왜 위험한가** 사고 기록의 「경과 17m 24s · 예상 약 17m」은 overdue 쪽일 수도 있다. 계획서
  §4-A 는 이 잡이 `quiet` 이라고만 적었는데(**계획서 구멍**), 실제로는 expected 가 elapsed 보다
  작으면 `overdue` 다. 두 경우 모두 「멈춘 듯」과 「1배」가 사라지는 것이 이 작업의 요구다 —
  그 요구는 두 시나리오 모두에서 만족된다. 시험은 **둘 다** 잠근다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_the_same_incident_one_minute_later_reads_as_overdue`

### C-22 · `step_medians_from` — 도는 단계는 표본이 아니다
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** 표본 실행 3개. 각 실행에 `build`(`state="done"`, 600초)와
  `deploy`(`state="running"`, `seconds=900`)가 있다
- **나와야 하는 것** `["build"]` 만 있고 `["deploy"]` 는 **없다**.
  (표본은 성공한 잡이라 원칙적으로 running 단계가 없지만, `progress_from_markers` 가
  `finished_at is None` 인 잡을 받으면 running 을 낸다 — 방어로 잠근다.)
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_a_running_step_is_not_a_sample`

### C-23 · `step_medians_from` — `seconds` 가 `None`
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** 표본 4개 중 하나의 `build` 가 `Step(state="done", seconds=None)`
- **나와야 하는 것** 그 하나는 건너뛴다. `["build"].sample_count == 3`.
  `statistics.median([600, None, ...])` 로 `TypeError` 가 나면 안 된다.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_a_step_with_no_duration_is_skipped`

### C-24 · `step_medians_from` — 빈 입력과 빈 key
- **어디** `core/queue.py::step_medians_from` · `tests/test_queue.py`
- **넣는 것** ① `{}`, ② `{"gate:full": []}`, ③ `{"gate:full": [[], [], []]}`
- **나와야 하는 것** ① `{}`, ② `{"gate:full": {}}` 또는 `{}` — **둘 중 하나로 못 박는다:
  `{}`**(key 자체를 안 넣는다. `_busy_estimate` 는 `step_medians.get(cur)` 을 쓰므로 어느 쪽이든
  돌지만, 빈 dict 를 남기면 서버 로그의 「몇 개 key 를 쟀나」가 거짓말이 된다),
  ③ `{}`. 어느 경우도 예외 없음.
- **위험도** 낮음
- **자동화** `tests/test_queue.py::test_step_medians_from_handles_empty_input`

### C-25 · 마지막 단계의 `seconds` 가 잡 종료까지 부풀어 있다
- **어디** `core/progress.py::progress_from_markers` · `core/queue.py::step_medians_from`
- **넣는 것** 성공 표본 3개. 각 실행의 마지막 단계 `build web` 은 `::rcm::step::build web` 만
  있고 `step-end` 가 없다. `progress_from_markers` 가 `finished_at` 으로 닫아
  `state="done"`, `ok=True`(exit 0), `seconds` 에 산출물 업로드 시간까지 포함된다
- **나와야 하는 것** 그 값을 **그대로 표본으로 쓴다**. 중앙값이 실제 단계보다 길어지는 쪽이라
  오경보가 줄어드는 방향이다. 「끝 마커가 없으니 버린다」로 고치면 `gate` 의 마지막 단계는
  표본을 영원히 못 채우고, 사고가 난 바로 그 단계가 판정 대상에서 빠진다.
- **왜 위험한가** 「정확도」를 이유로 이걸 버리면 이 작업이 아무것도 안 고친 것이 된다.
- **위험도** 높음
- **자동화** `tests/test_queue.py::test_a_step_closed_by_job_end_is_still_a_sample` +
  `queue.py` 주석 한 줄

### C-26 · `compute_queue` 가 `step_medians` 를 안 받았을 때 (기본값)
- **어디** `core/queue.py::compute_queue` · `tests/test_queue.py:271-277`
- **넣는 것** `compute_queue(..., step_medians=None)` 또는 인자 생략. 침묵 300초, `progress` 없음
- **나와야 하는 것** 오늘과 같다 — ② 로 `stuck is True`, `stuck_code == "no_output"`.
  기존 호출자(`rows_for` 픽스처, `render_text`, CLI)가 전부 그대로 산다.
- **위험도** 높음
- **자동화** `tests/test_queue.py` 기존 케이스 유지 + `test_compute_queue_without_step_medians_is_todays_rule`

---

## 2. 조사 헬퍼 (`web/i18n.js`)

모든 시나리오는 `tests/web/josa.test.js` 로 자동화한다. 네 쌍은 같은 판정을 쓰므로 표는 받침
유무만 적고, 각 시나리오는 **네 쌍을 모두** 돌린다:

| 받침 | 이/가 | 을/를 | 은/는 | 와/과 |
|---|---|---|---|---|
| 있음 | 이 | 을 | 은 | 과 |
| 없음 | 가 | 를 | 는 | 와 |

### C-27 · 한글 — 받침 있음 / 없음 / ㄹ받침
- **어디** `i18n.js::hasFinalConsonant` · `tests/web/josa.test.js`
- **넣는 것** 아래 표 그대로
- **나와야 하는 것**

  | 입력 | `hasFinalConsonant` | `withJosa(w,"이/가")` | `withJosa(w,"을/를")` | `withJosa(w,"은/는")` | `withJosa(w,"와/과")` |
  |---|---|---|---|---|---|
  | `"가"`(U+AC00, `%28==0`) | `false` | `가가` | `가를` | `가는` | `가와` |
  | `"각"`(U+AC01, `%28==1`) | `true` | `각이` | `각을` | `각은` | `각과` |
  | `"갈"`(U+AC08, `%28==8`, ㄹ) | `true` | `갈이` | `갈을` | `갈은` | `갈과` |
  | `"사람"` | `true` | `사람이` | `사람을` | `사람은` | `사람과` |
  | `"워커"` | `false` | `워커가` | `워커를` | `워커는` | `워커와` |
  | `"단계"` | `false` | `단계가` | `단계를` | `단계는` | `단계와` |
  | `"힣"`(U+D7A3, 범위 끝) | `true` | `힣이` | — | — | — |

  ㄹ받침은 이 네 쌍에서 **다른 받침과 똑같이** 다룬다(`으로/로` 는 이 작업의 대상이 아니다).
- **위험도** 높음
- **자동화** `tests/web/josa.test.js` — 「한글 음절」

### C-28 · 숫자 0~9 열 개 전부
- **어디** `i18n.js::hasFinalConsonant` · `tests/web/josa.test.js`
- **넣는 것** 문자열 `"0"`~`"9"` 열 개 (그리고 `"build-12"` 처럼 숫자로 끝나는 실제 값)
- **나와야 하는 것**

  | 끝 숫자 | 읽음 | 받침 | `이/가` | `을/를` | `은/는` | `와/과` |
  |---|---|---|---|---|---|---|
  | 0 | 영 | 있음 | 이 | 을 | 은 | 과 |
  | 1 | 일 | 있음 | 이 | 을 | 은 | 과 |
  | 2 | 이 | 없음 | 가 | 를 | 는 | 와 |
  | 3 | 삼 | 있음 | 이 | 을 | 은 | 과 |
  | 4 | 사 | 없음 | 가 | 를 | 는 | 와 |
  | 5 | 오 | 없음 | 가 | 를 | 는 | 와 |
  | 6 | 육 | 있음 | 이 | 을 | 은 | 과 |
  | 7 | 칠 | 있음 | 이 | 을 | 은 | 과 |
  | 8 | 팔 | 있음 | 이 | 을 | 은 | 과 |
  | 9 | 구 | 없음 | 가 | 를 | 는 | 와 |

  확인용: `withJosa("build-12", "이/가") === "build-12가"`.
- **위험도** 높음
- **자동화** `tests/web/josa.test.js` — 「숫자 열 개」

### C-29 · 라틴 26자 전부 [결정 필요 — 계획서와 다르다]
- **어디** `i18n.js::hasFinalConsonant` · `tests/web/josa.test.js`
- **넣는 것** `"a"`~`"z"` 와 `"A"`~`"Z"` 52개 전부
- **나와야 하는 것** **받침 있는 것은 `l`(엘) · `m`(엠) · `n`(엔) · `r`(알) 네 개뿐**이고
  나머지 22개는 받침 없음. 대소문자는 같은 판정.

  | 받침 있음 (4) | `l` `m` `n` `r` → `이 을 은 과` |
  |---|---|
  | 받침 없음 (22) | `a b c d e f g h i j k o p q s t u v w x y z` → `가 를 는 와` |

  **계획서 §2.3 규칙 4 는 `f` 를 받침 있는 쪽에 넣고 「에프」라고 주석까지 달았다. 모순이다 —
  「에프」의 마지막 글자 `프` 는 받침이 없다**(`(0xD504-0xAC00) % 28 == 0`). 규칙 자체가
  「글자 이름의 읽는 소리로」이므로 `f` 는 받침 없는 쪽이 맞다. 계획서의 그 줄을
  **「`l`·`m`·`n`·`r` 넷뿐」으로 고친 뒤** 구현한다.
  확인용: `withJosa("perf", "이/가") === "perf가"`, `withJosa("mail", "이/가") === "mail이"`.
- **왜 위험한가** 틀린 조사 하나를 박아 두면 이 작업이 없애려던 바로 그 어색함이 남는다.
  되돌리기는 쉽다(한 줄짜리 표) — 그래서 **지금 정하는 비용이 가장 싸다**.
- **위험도** 보통
- **자동화** `tests/web/josa.test.js` — 「라틴 26자」(52개 루프)

### C-30 · 실제로 화면에 오는 값 넷
- **어디** `i18n.js::withJosa` · `tests/web/josa.test.js`
- **넣는 것** `"mac2"` · `"PCS-MACBOOK-PRO"` · `"web@studio"` · `"ci@mac2"`
- **나와야 하는 것**

  | 입력 | 판정 글자 | 받침 | 결과(`이/가`) |
  |---|---|---|---|
  | `mac2` | `2`(이) | 없음 | `mac2가` |
  | `PCS-MACBOOK-PRO` | `O`(오) | 없음 | `PCS-MACBOOK-PRO가` |
  | `web@studio` | `o`(오) | 없음 | `web@studio가` |
  | `ci@mac2` | `2`(이) | 없음 | `ci@mac2가` |

  네 값 모두 `은/는` 은 `는`, `을/를` 은 `를`, `와/과` 는 `와`.
- **위험도** 높음
- **자동화** `tests/web/josa.test.js` — 「실제 값」

### C-31 · 끝이 따옴표·괄호·마침표·공백 [결정 필요 — 건너뛰기 집합을 못 박는다]
- **어디** `i18n.js::hasFinalConsonant` · `tests/web/josa.test.js`
- **넣는 것** `"alice (관리자)"` · `"alice)"` · `'"mac2"'` · `"mac2."` · `"mac2 "` ·
  `"mac2…"` · `"..."` · `"web@"`
- **나와야 하는 것** 건너뛰는 글자를 **정규식 하나로 못 박는다**:

  ```js
  // 뒤에서부터 건너뛰는 글자 — 문장 부호 · 괄호 · 따옴표 · 공백. 이 집합 **밖의** 글자를
  // 만나면 거기서 멈춘다(판정할 수 없으면 rule 5 로 null 이다).
  var SKIP = /[\s.,!?;:'"`’”\)\]\}\(\[\{…·\-_]/;
  ```

  | 입력 | 멈춘 글자 | 결과 |
  |---|---|---|
  | `alice (관리자)` | `자` | `alice (관리자)가` |
  | `alice)` | `e`(이) | `alice)가` |
  | `"mac2"` | `2` | `"mac2"가` |
  | `mac2.` | `2` | `mac2.가` |
  | `mac2 `(끝 공백) | `2` | `mac2 가` |
  | `mac2…` | `2` | `mac2…가` |
  | `...`(전부 건너뜀) | 없음 | `...가` (`hasFinalConsonant` → `null`) |
  | `web@` | `@` — **집합 밖** → 멈춤 → rule 5 | `web@가` (`null`) |

  `@` 를 건너뛰면 `b`(비, 받침 없음)에서 멈춰 결과가 같지만, **결과가 우연히 같다고 규칙을
  안 적으면 다음 값에서 갈린다**(`build-f@` → `f` 냐 `@` 냐). 계획서 §2.3 규칙 1 은
  「따옴표·괄호·마침표 등」이라고만 적었다(**계획서 구멍**) — 위 정규식을 코드 주석에 표로 남긴다.
- **위험도** 보통
- **자동화** `tests/web/josa.test.js` — 「끝의 부호는 건너뛴다」

### C-32 · 빈 문자열 · `null` · `undefined` — 던지지 않고, 그 글자도 안 찍는다
- **어디** `i18n.js::josa` · `withJosa` · `tests/web/josa.test.js`
- **넣는 것** `""` · `null` · `undefined`
- **나와야 하는 것**
  - `hasFinalConsonant("") === null`, `hasFinalConsonant(null) === null`,
    `hasFinalConsonant(undefined) === null` — **예외를 던지지 않는다**
  - `josa(null, "이/가") === "가"` (rule 5 — 받침 없는 쪽)
  - **`withJosa(null, "이/가") === "가"`** — 계획서의
    `String(word) + josa(word, pair)` 를 글자 그대로 쓰면 `String(null) === "null"` 이고
    **끝 글자 `l` 은 받침 있음**이라 화면에 **「null이」**가 찍힌다. 최악의 종류의 버그다
    (집안 규칙: 모르는 값은 `—`, 절대 `null` 문자열이 아니다). `withJosa` 는
    `word == null || word === ""` 이면 이름 부분을 **빈 문자열로** 둔다 — 화면에 넣을 대체값
    (`—`)은 호출자(`app.js::personLabel`)가 이미 고른다. (**계획서 구멍**)
  - 조사 헬퍼를 쓰는 **일곱 문자열 전부**를 `{by: null, who: undefined, name: undefined}` 로
    호출했을 때 결과에 `"null"` · `"undefined"` 가 **없다**
- **왜 위험한가** `tests/web/i18n.test.js:56-81` 은 그럴듯한 인자만 주므로 이걸 못 잡는다.
- **위험도** 높음
- **자동화** `tests/web/josa.test.js` — 「빈 값은 이름을 안 찍는다」

### C-33 · 숫자 타입 · 이모지 · 한자
- **어디** `i18n.js::hasFinalConsonant` · `tests/web/josa.test.js`
- **넣는 것** `2`(number) · `0`(number) · `"배포🚀"` · `"作業"` · `"✓"`
- **나와야 하는 것**
  - `hasFinalConsonant(2) === false` → `withJosa(2, "이/가") === "2가"` (문자열 변환 후 판정,
    `String(2)` 는 안전하다)
  - `hasFinalConsonant(0) === true` → `withJosa(0, "이/가") === "0이"`
  - `"배포🚀"` → `null` → `"배포🚀가"`. **이모지는 JS 에서 서로게이트 쌍이다** —
    `word[word.length-1]` 은 하위 서로게이트(U+DE80)를 준다. 그 코드포인트가 한글 범위
    (U+AC00–U+D7A3)에 들어가지 않는지 확인해야 한다(U+DE80 은 안 들어간다. 그래도 `null` 로
    떨어지는 경로를 시험이 잠근다). 예외를 던지면 안 된다.
  - `"作業"` → `null` → `"作業가"` (rule 5)
  - `"✓"` → `null` → `"✓가"`
- **위험도** 보통
- **자동화** `tests/web/josa.test.js` — 「모르는 글자는 받침 없는 쪽」

### C-34 · 네 쌍의 인자 꼴이 잘못 왔을 때
- **어디** `i18n.js::josa` · `tests/web/josa.test.js`
- **넣는 것** `josa("mac2", "이가")`(슬래시 없음) · `josa("mac2", "")` · `josa("mac2", null)` ·
  `josa("mac2", "으로/로")`(대상 아님)
- **나와야 하는 것** 던진다 — `throw new Error("josa: unknown pair …")`.
  조용히 `""` 를 돌려주면 문장에서 조사가 **사라진** 채로 배포된다. `i18n.t` 가 없는 키에
  던지는 것과 같은 판단이다.
- **위험도** 보통
- **자동화** `tests/web/josa.test.js` — 「모르는 쌍은 던진다」

### C-35 · `api` 노출
- **어디** `i18n.js` 끝의 `api` 객체 · `tests/web/josa.test.js`
- **넣는 것** `require("…/i18n.js")`
- **나와야 하는 것** `typeof api.josa === "function"`, `typeof api.withJosa === "function"`,
  `typeof api.hasFinalConsonant === "function"`. 브라우저 전역
  `globalThis.rcmI18n.withJosa` 도 같다.
- **위험도** 낮음
- **자동화** `tests/web/josa.test.js`

---

## 3. 문구 (`web/i18n.js` KO/EN)

이 절은 전부 `tests/web/i18n.test.js` 에 붙이는 **카탈로그 전수 검사**다. 문자열 키는 값
그대로, 함수 키는 기존 `args` 표(`i18n.test.js:58-72`)로 호출한 결과를 검사한다.

### C-36 · 두 언어의 키 집합이 정확히 같다
- **어디** `i18n.js` · `tests/web/i18n.test.js:27`
- **넣는 것** 신규 5개(`reason.quiet` · `pbar.quiet` · `reason.step_over` · 그리고 계획서
  §2.1·§2.5 가 더하는 나머지), 삭제 2개(`row.uploading` · `row.cancelling`)
- **나와야 하는 것** `Object.keys(ko).sort()` 가 `Object.keys(en).sort()` 와 정확히 같다.
  삭제한 두 키는 **두 언어에서 모두** 사라진다. `I18N.t("ko","row.uploading")` 은 **던진다**.
- **위험도** 높음
- **자동화** 기존 `tests/web/i18n.test.js` 케이스(수정 없이 통과해야 한다)

### C-37 · 모든 키가 두 언어에서 비어 있지 않은 문자열을 낸다
- **어디** `tests/web/i18n.test.js:56-81`
- **넣는 것** `args` 표에 `reason.step_over` 가 쓰는 인자(`step: "test"`, `n: 3`)가 **이미 있다**
  — 새 인자 이름을 만들지 말 것
- **나와야 하는 것** 두 언어 × 모든 키에서 `typeof out === "string"`, `out.length > 0`,
  `!/undefined|NaN|\[object/.test(out)`.
  함수 키는 인자를 **실제로 쓴다**: `reason.step_over` 결과가 `"test"` 와 `"3"` 을 둘 다 담는다.
- **위험도** 높음
- **자동화** 기존 케이스 + `test_step_over_uses_both_arguments`

### C-38 · 한국어 문자열에 `이(가)` 류가 하나도 없다
- **어디** `i18n.js` KO · `tests/web/i18n.test.js`
- **넣는 것** 카탈로그 전수
- **나와야 하는 것** 모든 KO 값(함수는 호출 결과)에 대해

  ```js
  assert.ok(!/이\(가\)|을\(를\)|은\(는\)|와\(과\)|가\(이\)|를\(을\)/.test(out), key);
  ```

  가 참. 오늘 걸리는 일곱 자리: `header.paused`(450) · `reason.blocked_by`(637) ·
  `reason.sigterm`(647) · `recent.by`(717) · `outcome.cancelled_by`(775) ·
  `outcome.worker_restarted_without_job`(795) · `outcome.worker_unreachable`(798).
- **위험도** 높음
- **자동화** `tests/web/i18n.test.js::"한국어 문장에 괄호 조사가 없다"`

### C-39 · 한국어 문자열에 「잡」이 단어로 남아 있지 않다
- **어디** `i18n.js` KO · `tests/web/i18n.test.js`
- **넣는 것** 카탈로그 전수. 오늘 KO 에 `잡` 이 **28곳**, 전부 단어 「잡(job)」이다
  (`grep -o '[가-힣]*잡[가-힣]*'` 로 확인 — 「복잡」·「잡음」 같은 고유어는 지금 하나도 없다)
- **나와야 하는 것** 규칙: **`잡` 이 나오면 실패한다. 예외는 명시적 허용 목록뿐이고, 지금 그
  목록은 비어 있다.**

  ```js
  // 「잡」은 다른 낱말의 일부일 수 있다(복잡·잡음·붙잡다). 그래서 정규식으로 가르지 않고
  // **허용 목록**으로 가른다 — 새 낱말이 들어오면 사람이 한 번 보고 여기에 적는다.
  const JAP_ALLOWED = new Set([]);  // 지금은 비어 있다
  Object.entries(I18N.MESSAGES.ko).forEach(([k, v]) => {
    const out = typeof v === "function" ? v(args) : v;
    if (JAP_ALLOWED.has(k)) return;
    assert.ok(!/잡/.test(out), `${k}: 「잡」이 남아 있다 — ${out}`);
  });
  ```

  정규식으로 「단어 경계」를 흉내 내면(`/(^|[^가-힣])잡/`) 「복잡」은 통과하지만 「잡음」은
  못 잡는다. 한국어에 단어 경계가 없으므로 **허용 목록이 유일하게 정직한 방법**이다.
- **왜 위험한가** 계획서 §2.1 이 경고한 대로 `sed` 전역 치환은 고유어를 먹는다. 이 시험이
  치환을 눈으로 하게 만드는 장치다.
- **위험도** 높음
- **자동화** `tests/web/i18n.test.js::"한국어에 잡이 남아 있지 않다"`

### C-40 · 「스텝」·「멈춘 듯」·「레인 기다리는」이 없다
- **어디** `i18n.js` KO · `tests/web/i18n.test.js`
- **넣는 것** 카탈로그 전수. 오늘 「스텝」은 13곳
- **나와야 하는 것** 세 정규식이 모두 불일치:
  `/스텝/` · `/멈춘\s*듯/` · `/레인\s*기다리는/`.
  「레인」 자체는 남는다(`reason.running_lane` = 「진행 중 · 레인 n」) — 금지어는
  **「레인 기다리는」이라는 표현**이다.
- **위험도** 보통
- **자동화** C-39 와 같은 함수

### C-41 · 카탈로그에 이모지·장식 기호가 없다
- **어디** `i18n.js` KO+EN · `tests/web/i18n.test.js`
- **넣는 것** 두 언어 전수
- **나와야 하는 것**

  ```js
  const DECOR = /[\u{1F300}-\u{1FAFF}\u{2190}-\u{21FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{25A0}-\u{25FF}\u{FE0F}]/u;
  ```

  가 **어느 값에도** 일치하지 않는다. 오늘 걸리는 것: `🔑`(U+1F511, 7곳) ·
  `⚠`(U+26A0) · `⛓`(U+26D3) · `▾`(U+25BE, 3곳) · `▴`(U+25B4) · `↑`(U+2191) · `■`(U+25A0).
  **남아야 하는 것**: `·`(U+00B7) · `—`(U+2014) · `…`(U+2026) · `’`(U+2019) — 위 범위 밖이라
  자동으로 통과한다. 확인용으로 `assert.ok(!DECOR.test("5m · 진행 중 — 예상 …"))` 을 같이 넣는다.
  **`app.js` 의 `GLYPH` 는 이 시험의 대상이 아니다**(계획서 §2.4 결정 A2) — 시험이 읽는 것은
  `i18n.js` 뿐이라 자동으로 지켜진다. 주석에 그 이유를 적는다.
- **위험도** 높음
- **자동화** `tests/web/i18n.test.js::"카탈로그는 글자만 담는다"`

### C-42 · 문체 — 종결어미로 끝나는 한국어는 전부 합쇼체
- **어디** `i18n.js` KO · `tests/web/i18n.test.js`
- **넣는 것** KO 전수
- **나와야 하는 것** 판정 규칙을 못 박는다:

  ```js
  // 종결어미로 끝나는 문장만 본다. 명사형·조각(「진행 중」·「대기」·「응답 없음」·
  // 「머신이 바빠 대기 중」)은 마침표도 종결어미도 없이 끝나므로 이 규칙 밖이다.
  // 판정: 한글 + `다` 로 끝나면 문장이다. 그러면 반드시 `니다` 로 끝나야 한다.
  const ENDS_SENTENCE = /[가-힣]다$/;
  const HAPSYO = /니다$/;
  if (ENDS_SENTENCE.test(out)) assert.ok(HAPSYO.test(out), `${k}: 합쇼체가 아니다 — ${out}`);
  ```

  오늘 걸리는 세 곳(계획서 §2.2): `host.job_storage_unknown`(「…재지 못했다」) ·
  `reason.held_by_load`(「…바쁘다」) · `failures.unnamed`(「…실패했다」).
  고친 뒤 `reason.held_by_load` = 「머신이 바빠 대기 중」은 `다` 로 안 끝나므로 규칙 밖 —
  이 예외를 **카탈로그 주석에 한 줄**로 적는다(계획서 §2.2 지시).
  주의: 함수 키의 결과가 `" · "` 로 끝나는 것이 있다(`header.paused`) — `$` 앞의 공백 때문에
  `ENDS_SENTENCE` 에 안 걸린다. 그런 키는 `out.trim().replace(/[·\s]+$/, "")` 로 다듬은 뒤 본다.
- **위험도** 보통
- **자동화** `tests/web/i18n.test.js::"종결어미는 합쇼체다"`

### C-43 · `state.quiet` 키를 만들지 않는다
- **어디** `i18n.js` · `tests/web/state_marks.test.js:29-40`
- **넣는 것** 없음 — 회귀 방지
- **나와야 하는 것** `state.` 로 시작하는 키에서 워커 상태(`unknown` `busy` `idle` `down`
  `held`)를 뺀 집합이 정확히
  `["cancelled","cancelling","failed","lost","queued","running","succeeded","timed_out","uploading"]`.
  `state.quiet` 을 더하면 이 `deepEqual` 이 즉사한다.
  `quiet` 은 **잡 상태가 아니라 표시 사유**다 — 키는 `reason.quiet` · `pbar.quiet` 두 개뿐.
  `i18n.js` 의 `state.` 블록 위에 회귀 방지 주석을 남긴다.
- **위험도** 높음
- **자동화** 기존 `tests/web/state_marks.test.js`(수정 없이 통과)

### C-44 · EN 쪽 문면 확정
- **어디** `i18n.js` EN · `tests/web/i18n.test.js`
- **넣는 것** —
- **나와야 하는 것** `reason.stuck === "Not responding"`(결정 A1) ·
  `pbar.stuck === "not responding"` · `reason.quiet === "output has gone quiet"` ·
  `pbar.quiet === "quiet"` · `state.running === "Running"` · `state.busy === "Running"`.
  **`pbar.none` 의 EN 은 `"progress —"` 그대로다** —
  `tests/test_web_browser.py:907` 이 글자 그대로 잠근다.
- **위험도** 보통
- **자동화** `tests/web/i18n.test.js` + 기존 브라우저 시험

---

## 4. 화면 렌더 (`web/app.js` · `web/style.css`)

### C-45 · `stuck_code` 세 값의 문면
- **어디** `app.js::reasonText` · `tests/web/stall.test.js`(신규)
- **넣는 것** 세 행. 공통: `reason: "stuck"`, `progress.last_output_at = generated_at - 473s`
  - ① `estimate: {stuck: true, stuck_code: "over_step", elapsed_seconds: 3000,
    expected_seconds: 1080, step_expected_seconds: 540}`,
    `progress.current_name: "build web"`, `progress.current_seconds: 2000`
  - ② `estimate: {stuck: true, stuck_code: "over_elapsed", elapsed_seconds: 4000,
    expected_seconds: 1080}`
  - ③ `estimate: {stuck: true, stuck_code: "no_output", elapsed_seconds: 600,
    expected_seconds: 1800}`
- **나와야 하는 것**
  - ① `cls === "stuck"`, `actionable === true`. 텍스트에 `reason.step_over` 가 있고
    (`build web` 과 배수 `Math.floor(2000/540) === 3`), **`reason.times_expected` 가 없다**
    (「지난 실행보다 n배 오래」는 `over_elapsed` 전용)
  - ② 텍스트에 `reason.times_expected` 가 있고 그 `n` 은 `Math.floor(4000/1080) === 3`.
    `reason.step_over` 는 없다
  - ③ 텍스트에 `reason.no_output_for` 가 있고 `times_expected`·`step_over` 둘 다 **없다**
    (`600 / 1800` 은 0 배다 — 「0배 오래」는 말이 안 된다)
  - 세 경우 모두 `⚠` 가 없다
- **위험도** 높음
- **자동화** `tests/web/stall.test.js::"stuck_code 세 갈래"`

### C-46 · `stuck_code` 없는 옛 서버 문서 (구버전 호환)
- **어디** `app.js::reasonText` · `tests/web/stall.test.js`
- **넣는 것** 세 행, 전부 `estimate.stuck_code` **없음**
  - ㉮ `elapsed_seconds: 1044, expected_seconds: 1020` → `floor = 1`
  - ㉯ `elapsed_seconds: 3700, expected_seconds: 1800` → `floor = 2`
  - ㉰ `elapsed_seconds: 100, expected_seconds: 0` → 0 으로 나눔
- **나와야 하는 것**
  - ㉮ **「1배」가 나오지 않는다** — `floor(elapsed/expected) >= 2` 일 때만 붙인다.
    결과에 `/1\s*배|1×/` 가 없다
  - ㉯ 「2배」가 나온다
  - ㉰ `expected_seconds > 0` 가드로 조각 자체가 없다. `Infinity` · `NaN` 이 화면에 없다
  - 세 경우 모두 `cls === "stuck"`, 예외 없음
- **왜 위험한가** 이 폴백이 2026-09-15 화면에 「예상의 1배」를 찍은 그 코드다.
- **위험도** 높음
- **자동화** `tests/web/stall.test.js::"stuck_code 없는 옛 문서"`

### C-47 · `quiet` 행은 「확인이 필요한 작업」 패널에 안 뜬다
- **어디** `app.js::notMoving` · `ACTIONABLE`(`app.js:16`) · `tests/web/summary.test.js`
- **넣는 것** 큐에 행 셋: `reason: "quiet"`(#401) · `reason: "stuck"`(#402) ·
  `reason: "overdue"`(#403)
- **나와야 하는 것** `notMoving(...).lines` 의 `jobId` 가 정확히 `[402, 403]`
  (`ACTIONABLE` 순서: stuck 이 overdue 앞). `401` 이 **없다**.
  `ACTIONABLE` 배열 자체가 오늘과 글자까지 같다:
  `["worker_down","stuck","upload_stalled","not_scheduled","blocked_by_group","overdue","paused"]`.
  `reasonText({reason:"quiet"}).actionable === false`, `.cls === "quiet"`.
- **왜 위험한가** `quiet` 을 패널에 올리면 오늘의 빨간 소음이 이름만 바꿔 남는다 — 이 작업이
  아무것도 안 고친 것이 된다.
- **위험도** 높음
- **자동화** `tests/web/summary.test.js::"조용한 잡은 패널에 안 뜬다"` +
  `scripts/mutcheck.py` 신규 변이 `web-quiet-is-actionable`

### C-48 · 서버가 `stuck` 과 `quiet` 을 같이 보냈다 (서버 버그)
- **어디** `app.js::reasonText` · `overallProgress` · `tests/web/stall.test.js`
- **넣는 것** `{reason: "stuck", estimate: {stuck: true, quiet: true,
  stuck_code: "over_step", ...}, state: "running"}`
- **나와야 하는 것** 화면은 **`stuck` 을 이기게 둔다**: `reasonText().cls === "stuck"`,
  `overallProgress().condition === "stuck"`. 「조용함」 문구가 같이 나오지 않는다.
  경보를 먹는 방향(quiet 이 이기는 것)은 fail-open 이라 금지다.
- **위험도** 보통
- **자동화** `tests/web/stall.test.js::"서버가 둘 다 보내면 stuck 이 이긴다"`

### C-49 · `overallProgress` 의 `condition` 우선순위 (§0.2)
- **어디** `app.js::overallProgress` · `tests/web/progress_overall.test.js`
- **넣는 것** 여섯 행, 전부 `state: "running"`
  - ㉠ `phase:"materializing"` + `est.quiet:true` → `"preparing"`
  - ㉡ `est.stuck:true` + `est.quiet:true` → `"stuck"`
  - ㉢ `est.overdue:true` + `est.quiet:true`(`elapsed 2000 / expected 1800`) → `"over"`
  - ㉣ 단계 눈금 `done === total`(4/4) + `est.quiet:true`, stuck 아님 → `"finalizing"`
  - ㉤ 단계 눈금 1/4 + `est.quiet:true` → `"quiet"`, `basis === "steps"`, `pct === 25`
  - ㉥ 시간 눈금 `elapsed 900 / expected 1800` + `est.quiet:true` → `"quiet"`,
    `basis === "time"`, `pct === 50`
- **나와야 하는 것** 위 여섯 값 그대로. **㉣ 가 갈리는 자리다** — 계획서는 `finalizing` 과
  `quiet` 의 순서를 안 정했다(**계획서 구멍**). `finalizing` 이 이긴다: 「선언한 단계를 다
  끝냈는데 아직 안 끝났다」가 「조용하다」보다 구체적인 사실이다.
  ㉤㉥ 에서 `p.pct` 가 `quiet` 때문에 `null` 이 되면 안 된다 — 조용한 것과 진행률을 모르는
  것은 다른 일이다.
- **위험도** 높음
- **자동화** `tests/web/progress_overall.test.js::"quiet 은 가장 약한 형편이다"`

### C-50 · `mutcheck` 가 잡는 `overallProgress` 문자열 셋을 안 건드렸다
- **어디** `app.js::overallProgress` · `scripts/mutcheck.py:150-183`
- **넣는 것** 없음 — `python scripts/mutcheck.py` 를 E·F 단계 뒤에 돌린다
- **나와야 하는 것** 세 패턴이 파일에 **글자 그대로** 남아 있다:
  1. `if (isNum(total) && total > 0 && isNum(done) && !(prog && prog.steps_total_partial)) {`
  2. `&& est.source !== "default"`
  3. `Math.max(0, Math.min(99, Math.floor(elapsed / expected * 100)))`
  그리고 `notMoving` 의 앞 두 줄
  (`if (!Array.isArray(q)) return { kind: "unknown", lines: [] };\n    var lines = [];`).
  확인 명령: `grep -F '<패턴>' src/remote_ci_monitor/web/app.js` 가 정확히 1 을 돌려준다.
  안 맞으면 `mutcheck` 가 **조용히 변이를 건너뛴다**(패치 실패 = 그 변이를 안 잰다).
- **위험도** 높음
- **자동화** `python scripts/mutcheck.py` + 위 `grep` 넷

### C-51 · 진행바를 `td.elapsed` 안으로 옮긴 뒤 — 그려진 길이 == `aria-valuenow`
- **어디** `app.js::progressBarHtml` · `applyBarFills` · `style.css` ·
  `tests/test_web_browser.py`
- **넣는 것** 도는 잡 둘 — ㉮ 단계 총계 4 선언 · 1 완료(`aria-valuenow = 25`),
  ㉯ 프리셋 추정만 있고 경과가 37%(`aria-valuenow = 37`). 창 `1240x900`
- **나와야 하는 것** 각 막대에 대해

  ```js
  const wrap = pbar.getBoundingClientRect().width;      // 84 이어야 한다
  const fill = pbar.querySelector('i').getBoundingClientRect().width;
  const drawn = Math.round(fill / wrap * 100);
  ```

  `Math.abs(drawn - Number(valuenow)) <= 1`. 그리고 `Math.abs(wrap - 84) <= 1`.
  84px 에서 1px 은 1.19% 이므로 반올림 오차는 최대 ±0.6% — 허용치 1% 안이다.
  **`wrap` 이 84 가 아니면(칸이 줄어 flex 가 눌렸거나 옛 `min-width: 80px`·
  `flex: 1 1 auto` 가 남았으면) 이 단언은 통과해도 의미가 없다** — 그래서 폭을 함께 잰다.
- **왜 위험한가** 계획서 §7 이 이 작업 최대 위험으로 지목한 자리다. 실측 기록이 있다:
  616px 막대의 25% 가 70px 로 그려졌다.
- **위험도** 높음
- **자동화** `tests/test_web_browser.py` 기존 `:1080-1084` 단언 유지 + `wrap` 폭 단언 추가

### C-52 · 좁은 화면(720px 미만)에서도 같은 계약
- **어디** `style.css` `@media (min-width: 721px)` 밖 · `tests/test_web_browser.py`
- **넣는 것** C-51 과 같은 장면, 창 `680x900`. 이 폭에서는 칸 너비 규칙이 안 걸리고
  칸이 블록이 된다
- **나와야 하는 것** ① 막대가 여전히 그려진다(`[role="progressbar"]` 수가 도는 잡 수와 같다),
  ② `wrap` 이 84±1, ③ `drawn == valuenow ± 1`,
  ④ `document.documentElement.scrollWidth <= window.innerWidth + 1`
  (**표가 옆으로 안 샌다**).
  ④ 가 진짜 위험이다: `td.elapsed { white-space: nowrap }` 인데 라벨
  「50% · 지난 실행 기준 · 조용함」을 84px 칸 안에 nowrap 으로 두면 칸이 라벨 폭만큼 벌어진다.
  → 라벨(`.plab`)은 `white-space: normal` 로 두고 막대 아래 두 줄까지 접히게 한다.
  (계획서 §3.2 가 라벨 줄바꿈을 안 말했다 — **계획서 구멍**)
- **위험도** 높음
- **자동화** `tests/test_web_browser.py::test_the_bar_keeps_its_contract_on_a_narrow_window`

### C-53 · 한 행에 채운 칩이 둘 이상 나오지 않는다
- **어디** `app.js::queueRowHtml` · `style.css` · `tests/test_web_browser.py`
- **넣는 것** 한 잡이 여러 조건을 동시에 만족하는 장면: `mine` + `overdue` +
  `reason: "stuck"` + 단계 표시 있음
- **나와야 하는 것** 그 `tr` 안에서
  `document.querySelectorAll('tr[data-job="N"] .stuck, … .stalled, … .blocked')` 중
  **`background` 가 `transparent`/`none` 이 아닌 것이 0 개**다. 즉
  `getComputedStyle(el).backgroundImage === "none"` 이고
  `backgroundColor` 가 `rgba(0, 0, 0, 0)` 이다(계획서 §3.2 — 배경을 없애고 글자색만 남긴다).
  상태 필(`.pill.running`)은 예외로 배경을 유지한다 — **행에 채운 칩은 상태 필 하나**다.
  왼쪽 레인 색 우선순위도 함께 잠근다: **stuck(`--bad`) > overdue(`--warn`) >
  quiet(`--queued`) > running(`--accent`)**, `mine` 은 3px `--accent` 를 유지하고 상태색은
  7px 자리로 밀린다(오늘 `tr.mine.overdue` 의 두 겹 그림자 규칙 그대로).
  계획서는 `mine` 과 상태색이 겹칠 때를 안 적었다(**계획서 구멍**).
- **위험도** 보통
- **자동화** `tests/test_web_browser.py::test_a_row_never_shows_two_filled_chips`

### C-54 · 대비 — `.stuck`·`.quiet`·`.stalled`·`.blocked` 글자색
- **어디** `style.css` · 계산은 손으로 + 주석 갱신
- **넣는 것** 칩 배경이 사라진 뒤 글자가 앉는 배경은 **칸 배경**이다:
  라이트 `--surface #FFFFFF`, 다크 `--surface #171F29`. 강조된 행은
  `tr.hl td { background: var(--accent-soft) }` 라 라이트에서 `#E3EBFB` 도 재야 한다
- **나와야 하는 것** WCAG 2.1 상대휘도로 계산한 명암비(계산식:
  `L = 0.2126R + 0.7152G + 0.0722B`, 각 채널은 `c/255` 를 `c<=0.03928 ? c/12.92 :
  ((c+0.055)/1.055)^2.4` 로 선형화; 비 = `(L_light+0.05)/(L_dark+0.05)`):

  | 색 | 배경 | 기대 명암비 | 판정 |
  |---|---|---|---|
  | `--bad` `#BC3930` | `#FFFFFF` | **5.58** | ✅ |
  | `--bad` `#BC3930` | `#E3EBFB`(`tr.hl`) | **4.66** | ✅ (여유 0.16 — 아슬하다) |
  | `--bad` `#F06E63` | `#171F29`(다크) | **5.63** | ✅ |
  | `--warn` `#9E5E00` | `#FFFFFF` | **5.18** | ✅ |
  | `--warn` `#E0A23A` | `#171F29` | **7.44** | ✅ |
  | `--muted` `#5D6B7B`(= `.quiet`) | `#FFFFFF` | **5.45** | ✅ |
  | `--muted` `#97A5B6` | `#171F29` | **6.62** | ✅ |

  전부 4.5:1 이상이다(칩 글자는 12px 600 이라 「큰 글자」가 아니므로 3:1 이 아니라 4.5:1 이
  기준). 허용 오차 ±0.05. **`--bad` on `tr.hl` 의 4.66 을 `style.css:7-9` 주석에 새로 적는다** —
  오늘의 주석은 `-soft` 배경 위의 기록이라 이 조합이 없다.
- **왜 위험한가** 배경을 없애면 대비가 오른다는 것은 흰 배경 기준이다. 강조된 행
  (`tr.hl`, 점프해 온 잡)에서는 거의 안 오른다.
- **위험도** 보통
- **자동화** 계산은 스크립트(예: 시험 안의 작은 헬퍼)로 자동화 가능 —
  `tests/test_web_layout.py` 에 토큰 값을 읽어 비를 재는 시험을 넣는다. 실제로 그 색이 그
  배경 위에 그려지는지는 **사람 눈**(§7 목록 참조).

### C-55 · `reasonText` — `quiet` 가지
- **어디** `app.js::reasonText` · `tests/web/reason.test.js`
- **넣는 것** `{reason: "quiet", progress: {last_output_at: generated_at - 473000},
  estimate: {quiet: true, elapsed_seconds: 1044, expected_seconds: 1080}}`, `lang: "ko"`
- **나와야 하는 것** `cls === "quiet"`, `actionable === false`,
  텍스트가 `reason.quiet`(「출력이 조용합니다」)와 `reason.no_output_for`(「7m 출력 없음」)를
  `" · "` 로 이은 것. 「배」·「멈춘」·`⚠` 가 없다.
  `lang` 없이(영어 기본) 부르면 `"output has gone quiet · …"`.
- **위험도** 보통
- **자동화** `tests/web/reason.test.js::"조용한 잡"`

### C-56 · 죽은 키를 지운 뒤 필 조립이 그대로다
- **어디** `app.js:1519-1521` · `tests/test_web_layout.py:235-256`
- **넣는 것** `row.uploading` · `row.cancelling` 두 키를 두 언어에서 삭제
- **나와야 하는 것** 업로드 중·취소 중 행의 필이 그대로 그려진다. `PILLS_JS` 가 보는
  「필마다 `aria-hidden` 글리프와 글자가 둘 다 있다」가 유지된다 —
  `GLYPH.uploading === "↑"`, `GLYPH.cancelling === "■"` 는 `app.js` 에 **그대로 남는다**.
  `i18n.t("ko", "row.uploading")` 은 던진다.
- **왜 위험한가** 계획서 §2.4 가 「12곳의 이모지」에서 이 둘을 빼는 근거가 「죽은 키」인데,
  정말 죽었는지는 그려 봐야 안다.
- **위험도** 보통
- **자동화** `tests/test_web_layout.py` 기존 시험(수정 없이 통과) +
  `grep -c 'row\.uploading' src/remote_ci_monitor/web/` 가 0

---

## 5. 서버 · 성능 (`store.py` · `server.py`)

### C-57 · 도는 잡이 없으면 질의 0회
- **어디** `server.py::_load_step_medians` · `tests/test_status_perf.py`
- **넣는 것** 성공 잡 5개(전부 종료), 활성 잡 0개. `/api/status` 호출
- **나와야 하는 것** `store.list_step_sample_ids` 호출 횟수 **0**, `store.markers_for` 의
  추가 호출 **0**. 측정법: `app.store.list_step_sample_ids` 를 세는 래퍼로 바꿔 놓고
  `app._snapshot()` 을 부른다(`test_status_perf.py` 의 `flaky` 패턴과 같은 방식).
  `snap.step_medians == {}`, `step_medians_error is None`.
- **위험도** 높음
- **자동화** `tests/test_status_perf.py::test_an_idle_server_reads_no_step_samples`

### C-58 · 같은 key 의 잡이 여럿 돌 때 key 당 1벌
- **어디** `server.py::_load_step_medians` · `tests/test_status_perf.py`
- **넣는 것** 같은 key `gate:full` 로 도는 잡 3개 + 다른 key `demo:quick` 으로 도는 잡 1개
- **나와야 하는 것** `list_step_sample_ids` 호출 인자의 `key` 집합이 정확히
  `{"gate:full", "demo:quick"}` 이고 호출 횟수가 **2**(잡 4개가 아니다).
  `markers_for` 는 **한 번**에 두 key 의 표본 id 를 전부 넘겨 부른다(호출 1회) — 또는 key 당
  1회로 **최대 2회**. 못 박는 값: **`markers_for` 호출 ≤ 2**.
- **위험도** 높음
- **자동화** `tests/test_status_perf.py::test_step_samples_are_read_once_per_key`

### C-59 · 보존된 `events` 행이 많아도 안 끌린다 (구조로 잠근다)
- **어디** `store.py::list_step_sample_ids` · `markers_for` · `tests/test_status_perf.py`
- **넣는 것** `test_status_perf.py` 의 `finished()` 를 확장해 key `gate:full` 의 성공 잡
  **50개**를 만들고, 그 중 40개(= `STEP_SAMPLE_JOBS = 10` 밖)에 마커를 **잡당 200줄** 넣는다
  (총 8000 이벤트 행). 그리고 도는 잡 1개
- **나와야 하는 것** `count_statements()` 로 센 SELECT 문장 수가 **보존 잡 수·이벤트 수와
  무관하게 상수**다. 못 박는 값: 단계 중앙값 경로가 내는 SELECT 는 **key 당 2개**
  (`list_step_sample_ids` 1 + `markers_for` 1).
  그리고 `markers_for` 에 넘어간 id 개수가 `<= STEP_SAMPLE_JOBS`(10)다 — 50 이 아니다.
  `EXPLAIN QUERY PLAN` 으로 `list_step_sample_ids` 의 계획에 **`jobs_key_finished`** 가
  들어 있는지도 본다(`test_recent_jobs_do_not_scan_every_terminal_row` 와 같은 방식).
  **타이밍 단언은 쓰지 않는다** — 이 파일의 머리말이 그렇게 정했다(CI 에서 흔들린다).
- **왜 위험한가** M5f 결정 49 가 이미 한 번 고친 함정이다. 45일치 마커를 훑는 설계가 들어오면
  `/api/status` 가 다시 보존 이벤트 수에 선형으로 끌린다.
- **위험도** 높음
- **자동화** `tests/test_status_perf.py::test_step_samples_do_not_scale_with_retained_events`

### C-60 · 마커 한 줄이 단계 중앙값을 무효화하지 않는다
- **어디** `server.py::_load_step_medians` · `tests/test_status_perf.py`
- **넣는 것** `app._snapshot()` 으로 데운 뒤 `app._on_marker(1, "step", "build")`,
  다시 `app._snapshot()`
- **나와야 하는 것** `app._snapshot().step_medians is before` — **같은 객체**.
  마커가 스냅샷은 더럽히지만 단계 중앙값은 그대로 쓴다. 잡이 **끝났을 때만**
  (`_mark_medians_dirty`) 다시 잰다.
- **왜 위험한가** 단계 중앙값의 원천이 마커라서 「마커가 오면 다시 재자」가 자연스러워 보인다.
  그것이 정확히 M5f 가 고친 그 버그다.
- **위험도** 높음
- **자동화** `tests/test_status_perf.py::test_a_marker_line_does_not_invalidate_the_step_medians`

### C-61 · 캐시가 신선한데 **새 key** 의 잡이 시작했다 [결정 필요]
- **어디** `server.py::_load_step_medians` · `tests/test_status_perf.py`
- **넣는 것** ① `gate:full` 만 도는 상태로 `_snapshot()` 을 데운다(캐시 신선),
  ② 잡을 끝내지 않은 채 새 key `demo:quick` 잡을 시작시키고 `_mark_dirty()` 후 `_snapshot()`
- **나와야 하는 것** `snap.step_medians` 에 **`demo:quick` 이 들어 있다**.
  계획서 §1.6 은 「`_load_medians` 와 같은 캐시 정책(`_medians_dirty` +
  `MEDIANS_MAX_AGE_SECONDS`)」이라고만 적었는데(**계획서 구멍**), 잡 중앙값과 달리 단계
  중앙값은 **입력(`keys`)이 요청마다 바뀐다**. TTL 만 보면 새 key 의 잡은 최대
  `MEDIANS_MAX_AGE_SECONDS`(≤ 3600초) 동안 단계 판정을 못 받는다 — 그 사이 §1.4-③ 으로
  `quiet` 만 나므로 **안전하지만 조용히 무력하다**.
  못 박는 값: 캐시를 **key 별 항목**으로 두고, 요청의 `keys` 중 캐시에 없는 것이 있으면
  **그 key 만** 읽는다. 이미 있는 key 는 TTL·dirty 규칙을 그대로 따른다.
  `list_step_sample_ids` 호출 인자가 `["demo:quick"]` 하나여야 한다(`gate:full` 재조회 없음).
- **위험도** 높음
- **자동화** `tests/test_status_perf.py::test_a_new_key_is_measured_without_rereading_the_old_ones`

### C-62 · 마커가 하나도 없는 잡 / 끝 마커 없이 시작 마커만
- **어디** `core/progress.py` · `server.py::_load_step_medians` · `tests/test_queue.py`
- **넣는 것** key `silent:job` 의 성공 잡 5개.
  ① 3개는 마커가 **한 줄도 없다**, ② 2개는 `::rcm::step::build` 만 있고 `step-end` 가 없다
- **나와야 하는 것** ① 은 `Progress.steps == ()` 라 아무 표본도 안 낸다.
  ② 는 `finished_at` 으로 닫혀 `state="done"` 인 단계 하나씩을 낸다(C-25) →
  `build` 의 `sample_count == 2` → `step_min_samples = 3` 미달 → 표에 없다.
  결과: `step_medians["silent:job"] == {}`(또는 key 없음 — C-24 의 결정에 따라 key 자체가 없다).
  예외 없음, `/api/status` 200.
- **위험도** 보통
- **자동화** `tests/test_queue.py::test_jobs_with_no_markers_produce_no_step_medians`

### C-63 · 성공 잡이 `limit` 보다 적다 / 45일보다 오래된 것만 있다
- **어디** `store.py::list_step_sample_ids` · `tests/test_store.py`
- **넣는 것** ① key `rare` 의 성공 잡 **4개**(limit 10),
  ② key `stale` 의 성공 잡 20개인데 전부 `finished_at < NOW - 45일`,
  ③ key `mixed` 의 성공 3개 + **실패 7개**(실패가 더 최근)
- **나와야 하는 것**
  - ① 길이 4, 늦게 끝난 것부터 내림차순
  - ② 빈 리스트 `[]` — `since` 밖은 안 센다. 예외 없음
  - ③ 길이 **3** — `sample_policy` 를 안 본다. **항상 `succeeded` 만**. 실패한 잡의 단계
    소요는 「평소」의 표본이 아니다(실패는 단계를 일찍 끊는다). 이 판단이 메서드 독스트링에
    적혀 있다
  - `finished_at IS NULL` 인 잡(도는 잡)은 어느 경우에도 안 들어간다
- **위험도** 보통
- **자동화** `tests/test_store.py::test_list_step_sample_ids_takes_only_recent_successes`

### C-64 · 단계 중앙값 계산이 실패해도 `/api/status` 는 산다
- **어디** `server.py::_load_step_medians` · `tests/test_status_perf.py`
- **넣는 것** `app.store.list_step_sample_ids` 를 `raise sqlite3.OperationalError("database is
  locked")` 로 바꾼다. 도는 잡 1개(침묵 300초, 단계 진행 중)
- **나와야 하는 것**
  - `/api/status` 가 **200**
  - `snap.step_medians == {}`, `snap.step_medians_error` 가 비어 있지 않다,
    `snap.step_medians_error_code == "OperationalError"`
  - 판정은 §1.4-③ 으로 폴백 → 그 잡은 `quiet`(stuck 아님).
    **침묵 폴백(②)으로 떨어지면 안 된다** — 단계는 여전히 돌고 있고, 「중앙값을 못 읽었다」는
    「단계 이야기를 안 한다」와 다른 사실이다
  - **실패는 캐시하지 않는다**: `_mark_dirty()` 뒤 다음 `_snapshot()` 이 다시 시도한다
    (호출 카운터가 2 로 늘어난다)
- **위험도** 높음
- **자동화** `tests/test_status_perf.py::test_a_step_median_failure_only_falls_back_the_verdict`

### C-65 · `queue_config` · `examples/server.toml` · 설정 검증
- **어디** `config.py::EstimateSection` · `server.py::queue_config` ·
  `examples/server.toml:56-63` · `tests/test_examples.py:153`
- **넣는 것** ① 기본 설정으로 `/api/status` → `server.queue_config`,
  ② `step_stuck_multiplier = 1.0`, ③ `step_stuck_multiplier = 0.5`,
  ④ `step_min_samples = 1`, ⑤ `step_min_samples = 2`
- **나와야 하는 것**
  - ① `queue_config` 에 `step_stuck_multiplier: 3.0` · `step_min_samples: 3` 이 실린다
  - ② `step_stuck_multiplier > 1` 이 거짓 → 기동 실패, 오류 메시지에 키 이름이 있다
  - ③ 같음
  - ④ `step_min_samples >= 2` 가 거짓 → 기동 실패
  - ⑤ 통과(경계는 허용)
  - `examples/server.toml` 이 실제로 파싱되고 두 키가 `EstimateSection` 에 **둘 다** 있다
- **위험도** 보통
- **자동화** `tests/test_config.py` + 기존 `tests/test_examples.py:153`

---

## 6. 회귀 — 깨지면 안 되는 것

### C-66 · `mutcheck` 전량이 그대로 빨갛다
- **어디** `scripts/mutcheck.py` · `AGENTS.md:75` · `CONTRIBUTING.md:13`
- **넣는 것** `python scripts/mutcheck.py`
- **나와야 하는 것** 모든 변이가 시험을 빨갛게 만든다. 변이 수가 **38 → 40**
  (신규 `step-stuck-ignores-step-median` · `web-quiet-is-actionable`), 문서 두 곳의 숫자도 40.
  **`app.js` 변이는 6종이다** — `web-host-busy-no-history` · `web-host-panel-follows-load`
  (Chrome 필요) · `web-not-moving-unknown` · `web-progress-default-estimate` ·
  `web-progress-full-bar` · `web-progress-partial-total`.
  (계획서·브리핑은 「5종」이라 적었다 — **오기**다. 확인 명령:
  `python3 -c "import re;s=open('scripts/mutcheck.py').read();print(sum(1 for m in re.finditer(r'path=\"([^\"]+)\"',s) if 'web/app.js' in m.group(1)))"` → `6`)
  `queue.py` 변이는 **3종**(`join-key-inputs` · `priority-order` · `remaining-floor`) 그대로.
- **위험도** 높음
- **자동화** `python scripts/mutcheck.py`

### C-67 · 신규 변이 두 종이 실제로 시험을 빨갛게 만든다
- **어디** `scripts/mutcheck.py`
- **넣는 것**
  - `step-stuck-ignores-step-median`: `queue.py` 의
    `step_stuck = cur_seconds > max(cfg.step_stuck_multiplier * m.seconds, cfg.no_output_seconds)`
    → `step_stuck = silent`
  - `web-quiet-is-actionable`: `app.js` 의 `ACTIONABLE` 배열 끝에 `, "quiet"` 를 더한다
- **나와야 하는 것** 전자는 `tests/test_queue.py`(C-20 · C-2)가, 후자는
  `tests/web/summary.test.js`(C-47)가 **반드시** 빨개진다.
  변이의 `old` 문자열이 파일에 정확히 한 번 있어야 한다(`grep -c -F` → 1).
- **위험도** 높음
- **자동화** `python scripts/mutcheck.py`

### C-68 · `schema_version` 이 1 로 남아 있다
- **어디** `core/status.py` · `tests/test_compat_m5e.py:181` 외 14곳
- **넣는 것** `pytest -k schema_version`
- **나와야 하는 것** `schema_version == 1` 을 박아 둔 **15곳이 전부 초록**.
  이것 자체가 결정 D4 의 검증이다 — 키를 더하기만 했으므로 공짜다.
  `grep -rn "schema_version" tests/ | wc -l` 로 자리 수가 줄지 않았는지도 본다.
- **위험도** 높음
- **자동화** `pytest tests/test_compat_m5e.py tests/test_status_schema.py`

### C-69 · `ESTIMATE_KEYS` 정확 비교
- **어디** `tests/test_status_schema.py:48-60` · `:244`
- **넣는 것** `estimate_json` 이 내는 키 집합
- **나와야 하는 것** 오늘 집합 + `{"quiet", "stuck_code", "step_expected_seconds"}` **정확히**.
  더도 덜도 아니다(예: 디버그용 `step_median_samples` 를 몰래 얹으면 실패한다).
  주석에 「키 추가만 — 스키마 v1 그대로」를 남긴다.
  `confidence(...)` 의 `overdue=` 인자에 `quiet` 이 **안 섞인다**(`status.py:217`) —
  조용한 잡의 ETA 신뢰도 배지는 오늘 그대로다. 별도 단언으로 잠근다.
- **위험도** 높음
- **자동화** 기존 `tests/test_status_schema.py` + `test_quiet_does_not_lower_the_eta_confidence`

### C-70 · 옛 `/api/status` 문서(새 키 없음)를 화면이 읽어도 안 깨진다
- **어디** `app.js` 전반 · `tests/web/stall.test.js`
- **넣는 것** `estimate` 에 `quiet`·`stuck_code`·`step_expected_seconds` 가 **전혀 없는**
  상태 문서 하나(오늘 서버가 내는 것 그대로). 도는 잡 · 대기 잡 · 끝난 잡을 섞는다
- **나와야 하는 것** 예외 없음. `reasonText` 가 모든 행에서 문자열을 낸다.
  `overallProgress().condition` 이 `"quiet"` 이 **되지 않는다**(`est.quiet` 이 `undefined`).
  `notMoving` 의 결과가 오늘과 같다. 화면 어디에도 `undefined` · `NaN` 이 없다.
  반대 방향도 본다: `reason: "quiet"` 을 **모르는** 옛 화면 코드 경로는
  `default: reason.unknown` 으로 떨어진다(`app.js:268`) — 던지지 않는다.
- **위험도** 높음
- **자동화** `tests/web/stall.test.js::"옛 문서도 그대로 읽는다"`

### C-71 · CLI 문면 (`render_text.py`)
- **어디** `core/render_text.py::_reason_text:395` · `tests/test_render_text.py`
- **넣는 것** `reason` 이 `stuck`(세 `stuck_code` 각각) · `quiet` · 모르는 값 `"brand_new"`
- **나와야 하는 것** 네 문장이 서로 다르고, `⚠` 가 없고, 모르는 값은
  `return reason or "unknown"` 으로 `"brand_new"` 를 그대로 낸다(던지지 않는다).
  **오늘 `render_text.py:396` 을 잠그는 시험이 하나도 없다** — 이 브랜치가 그 빈자리를 채운다.
- **위험도** 보통
- **자동화** `tests/test_render_text.py::test_the_stall_reasons_read_differently`

### C-72 · 브라우저 시험의 옛 셀렉터가 전부 옮겨졌다
- **어디** `tests/test_web_browser.py:849-850` · `874-925` · `959-961` · `1024` · `1031` · `1043`
- **넣는 것** `grep -n 'tr\.qbar\|data-bar\|hasbar' tests/ src/`
- **나와야 하는 것** **0건**. `FOLD_JS` 의 `bars` 가
  `document.querySelectorAll('#queue [role="progressbar"]').length` 로,
  막대 셀렉터가 `tr[data-job="N"] td.elapsed .pbar` 로 바뀌었다.
  `style.css` 의 `tr.qbar` · `tr.hasbar` · `tr.qbar + tr.expanded` 규칙 세 개도 사라졌다
  (`grep -c 'qbar\|hasbar' src/remote_ci_monitor/web/style.css` → 0).
  `colspan="7"` 세 자리(`app.js:1438` · `1440` · `1541`)가 여전히 7 이다(칸 수는 안 바뀐다).
- **위험도** 보통
- **자동화** 위 `grep` 셋 + `pytest tests/test_web_browser.py`

### C-73 · 문서 잠금 시험
- **어디** `tests/test_docs_m5*.py` · `tests/test_examples.py` · `tests/test_changelog_pr_links.py`
- **넣는 것** `pytest tests/test_docs_m5*.py tests/test_examples.py
  tests/test_changelog_pr_links.py`
- **나와야 하는 것** 전부 초록. 계획서 §6 의 11항목이 다 들어가야 통과한다 —
  특히 `docs/configuration.md` 의 두 키, `examples/server.toml` 의 두 줄,
  `README.md:239`/`README.ko.md:224` 의 새 패널 이름,
  `docs/usage.md:279`·`:293-297`/`docs/usage.ko.md` 의 진행바 설명,
  `CHANGELOG.md [Unreleased]` 의 PR 번호.
- **위험도** 보통
- **자동화** 위 `pytest`

---

## 7. 자동 시험으로 못 잡고 사람이 눈으로 봐야 하는 것

아래는 단언으로 못 박을 수 없거나, 못 박아도 「통과했는데 나쁘다」가 가능한 것들이다.
**F 단계가 끝난 뒤 한 번에** 라이트·다크 두 판에서 본다. 확인 방법은 `tools/screenshots` 의
`capture.py all` 로 여섯 장을 새로 찍고 나란히 놓는 것이다.

1. **`.stuck` 글자가 강조된 행(`tr.hl`) 위에서 읽히나** — 계산상 4.66:1 로 겨우 넘는다(C-54).
   숫자가 넘는 것과 눈에 읽히는 것은 다르다. 다크 판도 같이 본다.
2. **한 행에 채운 칩이 하나라는 규칙이 실제로 「조용해」 보이나** — 자동 시험은 배경이
   없는지만 본다. 굵기 600 짜리 빨간 글자가 여전히 시끄러울 수 있다.
3. **84px 막대가 정보로서 쓸모가 있나** — 25% 와 31% 가 5px 차이다. 계약(C-51)은 지켜지지만
   사람이 구별 못 할 수 있다. 라벨이 숫자를 말하므로 치명적이진 않다.
4. **라벨이 84px 칸 아래에서 두 줄로 접혔을 때 행 높이가 들쭉날쭉하지 않나** — 큐 표에서
   행 높이가 행마다 다르면 훑어보기가 망가진다.
5. **왼쪽 3px 레인 색이 라이트 판에서 보이나** — `--queued #606E7D` 3px 은 흰 배경에서
   거의 안 보일 수 있다. 「조용함」은 경보가 아니지만 **아무 표시도 아닌 것**과는 달라야 한다.
6. **한국어 문면이 한국어로 읽히나** — 이 작업의 진짜 목표다. 자동 시험은 금지어가 없다는
   것만 본다. 「출력이 조용합니다」·「지난 실행보다 3배 오래」·「`build web` 단계가 평소보다
   3배 오래」를 실제 화면에서 소리 내어 읽어 본다.
7. **조사가 실제 값에서 어색하지 않나** — `PCS-MACBOOK-PRO가 큐를 멈췄습니다`.
   시험은 규칙대로 붙었는지만 보고, 자연스러운지는 안 본다. 특히 대문자 약어와 `@` 가 든 값.
8. **합쇼체 통일 뒤 이유 칸이 길어지지 않았나** — 「…합니다」는 「…함」보다 길다. 이유 칸
   폭이 정해져 있으므로 줄이 늘어날 수 있다.
9. **SVG 아이콘(`ICON.key` · `chain` · `chevron*`)이 글자 크기(1em)와 기준선에 맞나** —
   `currentColor` · 1em 은 코드로 강제할 수 있지만 시각적 정렬은 눈이다.
10. **다크 판의 `.quiet`(`--muted #97A5B6`)이 일반 본문(`--ink #E7ECF2`)과 구별되나** —
    구별이 안 되면 「조용함」 표시가 사실상 없는 것이다.
11. **요약 카드(`border-top: 3px`)와 호스트 지표 카드 5장이 좁은 화면에서 접히는 모양** —
    `repeat(auto-fit, minmax(150px, 1fr))` 가 3장/2장으로 갈릴 때 마지막 줄이 혼자 남는 모양.
12. **`pbar.quiet`(「조용함」) 라벨이 진행바 아래에 붙었을 때, 막대 색(파랑)과 라벨의 회색이
    서로 다른 말을 하는 것처럼 보이지 않나** — 막대는 「잘 가고 있다」, 라벨은 「조용하다」다.

---

## 부록 · 영역별 시나리오 수

| 영역 | 번호 | 개수 | 높음 |
|---|---|---|---|
| 1. 멈춤/조용함 판정 | C-1 ~ C-26 | 26 | 15 |
| 2. 조사 헬퍼 | C-27 ~ C-35 | 9 | 4 |
| 3. 문구 | C-36 ~ C-44 | 9 | 6 |
| 4. 화면 렌더 | C-45 ~ C-56 | 12 | 7 |
| 5. 서버 · 성능 | C-57 ~ C-65 | 9 | 6 |
| 6. 회귀 | C-66 ~ C-73 | 8 | 5 |
| **합계** | | **73** | **43** |

위험도 보통 27 · 낮음 3. 「사람 눈」 목록은 §7 에 12개.

**[결정 필요]** 로 표시한 자리 여섯 — §0.1(짧은 단계의 하한) · §0.2(`condition` 우선순위) ·
C-8(루프 단계의 표본 단위) · C-29(라틴 `f`) · C-31(건너뛰기 집합) · C-61(새 key 와 캐시).
구현을 시작하기 전에 이 여섯을 계획서에 반영한다.
