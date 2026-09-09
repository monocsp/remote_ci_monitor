# M5h 테스트 시나리오 A — 순수 계층: 마커와 판정 (2026-09-09)

`docs/m5h-implementation.md` §1.1(새 마커 `::rcm::fail::<이름>` 의 파싱과
`failed_step`·`last_step`·`fail_names` 규칙 R1~R6)과 §2.2(`core/failures.py` 의 판정 4종)를 §6 표의
`tests/test_progress_m5h.py` · `tests/test_failures.py` 두 행으로 옮긴 것이다(test-first, 역할 A).
`src/` 와 기존 테스트는 건드리지 않았다. **§1.2 `outcome_for` 의 상태별 억제 · §1.3 DB v11 ·
§1.4 `core/status.py` · §2.1 v12 대장(쓰기·읽기·삭제) · §2.3 `GET /jobs/{id}` · §2.4 설정 검증 ·
§3 404 `hint`** 는 역할 B 가, **§1.5 문구 갈림 · §2.5 실패한 대기의 끝줄 · §2.6 웹 배지 ·
§4 `source_ident`·`--ref`·`branch` · §5 스냅샷 해제**는 역할 C 가 맡는다.
여기 있는 것은 전부 시계도 I/O 도 DB 도 안 보는 함수 넷(`parse_marker` ·
`progress_from_markers` · `verdict` · `failures_json`)뿐이라 sleep·스레드·소켓·DB 가 없다.

**증인은 운영 잡 #162 다**(`docs/m5h-workplan.md` §2.1). 그 잡의 마커 순서를 그대로 픽스처로
넣고 `failed_step is None` · `last_step == "build web (…)"` 을 잠갔다 — 되재생된 머리말을 실패로
부르던 폴백(뮤테이션 ⑬)이 여기서 빨개진다.

## 공통 — 픽스처와 도우미

시각은 `jobfactory.NOW` 하나에서만 온다. 잡 #162 는 11분 0초를 돌고 exit 1 로 끝났다.

```python
# tests/test_progress_m5h.py
START = ago(minutes=11)
FINISHED = ago(seconds=5)


def markers(lines, *, start=START, step=1.0) -> list[Marker]:
    """로그 줄에 수신 시각(start + i×step)을 붙여 마커만 뽑는다 — 파서까지 같이 지난다."""


def progress(lines, *, exit_code=1, finished=FINISHED, start=START) -> Progress:
    """끝난 잡이 기본이다. 도는 잡은 `finished=None` · `exit_code=None`."""


def fails(*names) -> list[str]:            # ["::rcm::fail::a", …]
NAMES_100 = [f"case{i:03d}" for i in range(1, 101)]     # 상한을 시험할 이름 100개
```

**`JOB_162`** — 되재생 스크립트가 남긴 마커 순서 그대로(workplan §2.1). `step-end` 는 하나도
없고 잡은 exit 1 로 끝난다. `JOB_162_DECLARED` 는 여기에 `::rcm::fail::test` 를 **되재생된
`::rcm::step::test` 위**(실제 스크립트가 찍는 자리)에 끼운 것이다.

```python
JOB_162 = (
    "::rcm::step::무거운 셋 병렬 시작 (test[동시 9] · gitleaks · build web)",
    "FAIL: test (exit 1) — 깨진 테스트 1건(최대 80):",
    "  … just_audio_screen_music_port_test.dart: fadeIn — 0 에서 스며든다",
    "::rcm::step::test",  # ← 병렬 로그의 되재생
    "::rcm::step::secret-scan (gitleaks — 전체 히스토리)",
    "::rcm::step::build web (release — 셰이더 impellerc 컴파일 회귀 포함)",  # ← 성공한 스텝
)
LAST_162 = "build web (release — 셰이더 impellerc 컴파일 회귀 포함)"
```

```python
# tests/test_failures.py
WINDOW, MIN_JOBS = 8, 3          # 완료 기준 2 의 「같은 key 8회」
TEST_DART = "just_audio_screen_music_port_test.dart"


def row(name, seen, first=141, last=162) -> FailureRow:
def js(rows, **kw) -> list[dict]:   # steps=() · window=8 · unnamed=0 · min_jobs=3 이 기본
```

## 잠근 API

`src/remote_ci_monitor/core/progress.py` (§1.1)

- `KIND_FAIL = "fail"` · `MARKER_KINDS = (steps, step, step-end, summary, fail)` ·
  `MAX_FAIL_NAMES = 100` · `MAX_STEP_NAME` 은 그대로 120.
- `parse_marker` 의 `KIND_FAIL` 가지는 `KIND_STEP` 과 같은 규칙 — 빈 값이면 `None`,
  아니면 `value[:MAX_STEP_NAME]`.
- `Progress` 에 `last_step: str | None = None` · `fail_names: tuple[str, ...] = ()` ·
  `fail_truncated: bool = False`(§1.1 · `core/model.py`).

`src/remote_ci_monitor/core/failures.py` (§2.2, 새 · 순수)

- `VERDICT_UNKNOWN` · `VERDICT_FIRST_SEEN` · `VERDICT_INTERMITTENT` · `VERDICT_PERSISTENT`
  = `"unknown"` · `"first_seen"` · `"intermittent"` · `"persistent"`.
- `@dataclass(frozen=True) class FailureRow` — `name` · `seen` · `first_seen_job_id` ·
  `last_seen_job_id`(뒤 둘은 `int | None`).
- `def verdict(seen: int, window: int, *, min_jobs: int) -> str`.
- `def failures_json(rows, *, steps, window, window_unnamed, min_jobs) -> list[dict[str, Any]]`
  — 항목의 키 여덟은 `name` · `step` · `seen` · `window` · `window_unnamed` ·
  `first_seen_job_id` · `last_seen_job_id` · `verdict`.

---

## A. 파서 — kind 하나가 늘었다 (§1.1 · 결정 65)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| A1 | `::rcm::fail::test` → `("fail", "test")` · `\r\n` 붙은 줄 · 앞뒤 공백은 `strip` (×3) | `test_parse_marker_reads_the_fail_kind` | 새 kind 의 기본 모양. `strip()` 을 빼면 `echo` 가 붙인 공백 하나로 대장의 이름이 갈라진다 |
| A2 | `::rcm::fail::` · `::rcm::fail::   ` · `::rcm::fail::\n` · `::rcm::fail`(구분자 없음) → 전부 `None` (×4) | `test_an_empty_fail_value_is_not_a_marker` | 빈 값은 마커가 아니다(`KIND_STEP` 과 같은 규칙). 빈 이름이 통과하면 `job_failures` 에 `""` 이 쌓이고 창 질의가 그걸 센다 |
| A3 | 이름 200자 → `value == "x"*120` · `MAX_STEP_NAME == 120` | `test_a_fail_name_is_cut_at_the_step_name_limit` | R6. 11,000줄 테스트 출력의 한 줄이 그대로 DB 컬럼이 되면 안 된다 |
| A4 | `::rcm::flaky::x` · `::rcm::fail-name::x` · `::rcm::FAIL::x` · `::rcm::failed::x` → 전부 `None` (×4) | `test_an_unknown_marker_kind_is_still_ignored` | **옛 서버 호환의 거울**(§4.2) — 모르는 kind 는 조용히 무시하고 줄은 로그에 남는다. 스크립트가 서버 버전을 안 봐도 되는 이유. 대소문자를 안 받는 것도 같이 |
| A5 | `prefix ::rcm::fail::x` · `" ::rcm::fail::x"`(앞 공백) → `None` (×2) | `test_a_fail_marker_must_start_the_line` | 마커는 줄 맨 앞이다. 로그 본문에 인용된 마커가 대장에 들어가면 서버가 임의 출력을 파싱하는 셈이 된다 |
| A6 | `::rcm::fail::pkg::test_a` → `("fail", "pkg::test_a")` | `test_a_fail_name_may_contain_the_separator` | `partition` 은 **첫** `::` 에서만 자른다. 이름 안의 `::`(Dart·Rust 경로)를 자르면 대장의 이름이 잘못 뭉친다 |
| A7 | `KIND_FAIL == "fail"` · `MARKER_KINDS` 다섯 개가 순서대로 · `MAX_FAIL_NAMES == 100` | `test_the_fail_kind_and_the_cap_are_locked` | 이름과 상한이 계약이다. `MARKER_KINDS` 에 안 넣으면 `parse_marker` 가 첫 줄에서 `None` 을 돌려주고 기능 전체가 조용히 죽는다 |
| A8 | `step` · `fail` · 모르는 kind · 평문 네 줄 → 마커 두 개가 **수신 순서대로**, `at` 은 받은 시각 | `test_markers_from_log_keeps_fail_lines_in_receive_order` | `markers_from_log` 는 로그 재파싱의 유일한 길이다(워커·서버가 같이 쓴다). 새 kind 를 여기서 떨어뜨리면 §2.1 의 대장이 영영 비어 있다 |

## B. 증인 — 운영 잡 #162 (workplan §2.1 · 완료 기준 1 · 뮤테이션 ⑬)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| B1 | `JOB_162` · `exit_code=1` → `failed_step is None` · `last_step == LAST_162` · 스텝 넷의 `ok == [True, True, True, None]` · `steps_done == 4` · `fail_names == ()` · `fail_truncated is False` | `test_job_162_marker_order_leaves_the_failed_step_empty` | **이 마일스톤의 증인.** 오늘은 `build web`(실제로 **성공한** 스텝)이 실패로 보고되고 #169·#170·#171 이 같은 오해를 물려받았다. 폴백을 되살리면(뮤테이션 ⑬) 이 줄이 제일 먼저 빨개진다 |
| B2 | `JOB_162_DECLARED`(되재생된 `::rcm::step::test` **위**에 `::rcm::fail::test`) · `exit 1` → `failed_step == "test"` · `ok == [True, False, True, None]` · `last_step` 은 그대로 · `fail_names == ("test",)` | `test_job_162_with_a_fail_marker_names_the_step` | 게이트 스크립트가 고칠 수 있는 길이 실제로 열려 있는가(§6 「게이트 스크립트가 할 일」). 선언이 **스텝보다 먼저** 와도 먹혀야 한다 — 되재생 순서는 못 바꾼다 |
| B3 | B1 과 B2 의 스텝 이름 목록 · `steps_total` · `steps_done` 이 **같다** | `test_the_fail_marker_of_job_162_does_not_add_a_step` | `KIND_FAIL` 을 `KIND_STEP` 옆에 붙여 스텝으로 세는 구현을 잡는다. 진행 막대와 ETA 가 전부 스텝 수 위에 서 있다 |

## C. R1 · R2 — 선언이 없으면 빈칸이다

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| C1 | 스텝 `a`·`b` · `exit 2` · 선언 없음 → `failed_step is None` · `last_step == "b"` · `ok == [True, None]` | `test_a_nonzero_exit_without_any_declaration_names_no_step` | R2 그 자체 — **exit code 로 스텝을 고르지 않는다.** 오늘의 두 줄짜리 폴백이 만든 거짓말(#162·#176)의 최소 재현 |
| C2 | 마커 없는 로그 · `exit 1` → `steps == ()` · `failed_step is None` · `last_step is None` | `test_a_job_with_no_steps_has_no_failed_step_and_no_last_step` | 스텝이 없으면 둘 다 `null`. `steps[-1]` 을 그냥 읽는 구현은 `IndexError` 로 잡 문서를 통째로 죽인다 |
| C3 | `step a` → `step-end::fail` → `step b` · `exit 1` → `failed_step == "a"` · `ok == [False, None]` | `test_step_end_fail_still_names_the_step` | R1 의 나머지 절반 — 오늘 있는 마커는 그대로 산다. 새 마커를 넣다가 옛 길을 끊으면 이미 쓰는 프리셋이 조용히 빈칸이 된다 |
| C4 | `step a` + `::rcm::fail::a` · **`exit 0`** → `failed_step == "a"` | `test_a_declaration_on_a_zero_exit_job_still_names_the_step` | 순수 계층은 정직하다. 「성공한 잡은 이름을 안 남긴다」는 §1.2 의 `outcome_for` 가 하는 일이지 `progress` 가 하는 일이 아니다 — 두 곳에서 억제하면 역할 B 의 테스트가 무엇을 재는지 알 수 없다 |
| C5 | 스텝 `a`·`b`·`c` + `fail::c` → `fail::b` · exit 1 → `failed_step == "b"` · `ok == [True, False, False]` · `fail_names == ("c", "b")` | `test_the_failed_step_is_the_first_marked_step_not_the_first_marker` | R1 은 「`ok is False` 인 **첫 스텝**」이다(마커 순서가 아니라 **스텝 순서**). 반대로 `fail_names` 는 잡이 찍은 순서를 지킨다 — 두 순서가 다르다는 것을 한 줄에 잠근다 |
| C6 | `step a` → `step-end::ok` · `exit 1` → `failed_step is None` · `last_step == "a"` · `ok is True` | `test_a_step_closed_ok_before_a_nonzero_exit_names_no_step` | workplan §8 이 콕 집은 경우. 스텝 밖에서 죽은 잡(정리 단계·셸 오류)에 마지막 스텝을 뒤집어씌우지 않는다 |

## D. R3 — 암묵적 닫힘은 `True`, 선언은 그것을 덮는다

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| D1 | 스텝 `a`·`b`·`c` · `exit 0` → `ok == [True, True, True]` · `failed_step is None` | `test_an_implicitly_closed_step_stays_ok` | R3 의 앞쪽 — 프로토콜의 순차 전제는 그대로다. 「모르니 `None`」으로 바꾸면 성공한 잡의 스텝이 전부 회색이 되고 진행판이 무의미해진다 |
| D2 | 스텝 `a`·`b` → `fail::a`(둘 다 지난 뒤) · `exit 0` → `ok == [False, True]` · `failed_step == "a"` | `test_a_declaration_overrides_the_implicit_true` | R3 의 뒤쪽 — 선언이 추론을 **이긴다**. 루프 안에서만 칠하면 이미 `True` 로 닫힌 스텝을 못 되돌린다 |
| D3 | `fail::b` → 스텝 `a`·`b` · exit 1 → `failed_step == "b"` · `ok == [True, False]` | `test_a_fail_marker_before_its_step_still_marks_it` | #162 의 핵심을 최소 형태로. 칠하기는 **루프가 끝난 뒤**여야 한다 |
| D4 | 스텝 `a`·`b` + `fail::just_audio_port_test.dart` → `failed_step is None` · `ok == [True, None]` · 이름은 `fail_names` 에만 | `test_a_fail_name_that_is_not_a_step_marks_nothing` | §4.2 — 스텝 이름이 아니면 실패한 **단위**다. 이름이 스텝과 안 맞을 때 아무 스텝이나 칠하거나(거짓말) 이름을 버리면(대장이 빈다) 둘 다 틀렸다 |
| D5 | `steps::2` · `step a` · `fail::x` · 도는 잡 → 스텝 1개 · `steps_total == 2` · `steps_done == 0` · `current_index == 1` · `current_name == "a"` | `test_a_fail_marker_does_not_open_or_close_a_step` | 마커는 스텝 경계가 **아니다**. 열린 스텝을 닫아 버리면 도는 잡의 `current_seconds` 가 리셋되고 ETA 가 튄다 |

## E. R4 — 잡이 끝나며 닫히는 마지막 스텝

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| E1 | `step a` · `exit 0` → `ok is True` · `failed_step is None` · `last_step == "a"` | `test_the_last_open_step_closes_true_on_exit_zero` | R4 의 앞쪽. 성공한 잡의 마지막 스텝까지 `None` 이 되면 「전부 초록」이 화면에서 사라진다 |
| E2 | `step a` · `exit 1` → `ok is None` · `state == "done"` · `steps_done == 1` · `failed_step is None` | `test_the_last_open_step_closes_unknown_on_a_nonzero_exit` | **R4 의 뒤쪽이자 폴백의 뿌리.** `ok = exit_code == 0` 은 실패한 잡의 마지막 스텝을 `False` 로 물들이고, 그 `False` 가 R1 을 통과해 다시 「추론된 실패 스텝」이 된다. 여기가 초록이면 폴백을 지워도 되살아난다 |
| E3 | `step a` · `exit_code=None`(강제 종료) → `ok is None` · `failed_step is None` | `test_a_forced_end_with_no_exit_code_leaves_the_last_step_unknown` | §1.2 — 취소·시간초과·유실은 `exit_code=None` 으로 온다(오늘은 1 을 넣어 폴백을 켰다). #176 의 「취소된 잡의 실패 스텝」이 여기서 사라진다 |
| E4 | `step-end::ok` + `exit 1` → `ok is True` · `failed_step is None` / `step-end::fail` + `exit 0` → `ok is False` · `failed_step == "a"` (×2) | `test_an_explicitly_closed_last_step_keeps_its_own_verdict` | 이미 닫힌 스텝은 exit code 로 다시 칠하지 않는다. 잡의 판정과 스텝의 판정은 다른 정보다 |
| E5 | 스텝 `a`·`b` · 도는 잡 → `state == "running"` · `ok is None` · `current_name == "b"` · `last_step == "b"` | `test_a_running_job_keeps_the_last_step_open` | `last_step` 은 도는 잡에도 있다(마지막으로 **시작한** 스텝). R4 를 도는 잡에 잘못 적용해 스텝을 미리 닫는 구현을 잡는다 |
| E6 | `step a` + `fail::a` · 도는 잡 → `ok is False` · `state == "running"` · `current_name == "a"` · `failed_step == "a"` · `steps_done == 0` | `test_a_declaration_marks_a_still_running_step` | 큐 행이 `· ✘ {failed_step}` 을 찍는 자리(§1.5)가 도는 잡이다. 선언은 열린 스텝도 물들이되 **닫지는 않는다**(§1.1 3 은 `ok` 만 말한다). **가정 3** |

## F. R5 — 같은 이름의 스텝 전부 (매트릭스 · 함정 4)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| F1 | 스텝 `test`·`build`·`test` + `fail::test` · exit 1 → `ok == [False, True, False]` · 칠해진 index `[1, 3]` · `failed_step == "test"` | `test_every_step_with_the_declared_name_is_marked` | R5 그대로. 매트릭스는 같은 이름을 여러 번 찍는다(`test_duplicate_step_names_by_index` 가 이미 증인) — `next(...)` 로 하나만 칠하면 화면의 스텝 목록에 **초록 `test`** 가 남아 사람이 그걸 믿는다 |
| F2 | `fail::test` 를 두 번 + 스텝 둘 → `fail_names == ("test",)` · `fail_truncated is False` | `test_a_repeated_declaration_is_one_name` | R6 의 중복 규칙(`job_failures` 의 PK 가 `(job_id, name)`). 중복이 분자를 부풀리면 「최근 8회 중 12회」 같은 숫자가 나온다 |

## G. R6 — 이름 120자 · 잡당 100개

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| G1 | 스텝 이름과 fail 이름이 **같은 200자** → 둘 다 120자로 잘리고 서로 맞는다 · `ok is False` · `failed_step == "s"*120` | `test_a_long_fail_name_is_cut_like_a_step_name` | 두 자리가 같은 상한을 안 쓰면(예: fail 만 100자) 긴 이름의 스텝은 **영영 안 칠해진다**. 상한을 `MAX_STEP_NAME` 하나로 묶은 이유 |
| G2 | 이름 100개 → 전부 남고 `fail_truncated is False` · 순서 그대로 | `test_a_hundred_names_are_all_kept` | 경계 안쪽. `>` 를 `>=` 로 쓰면 100번째가 사라진다 |
| G3 | 이름 101개 → 앞 100개 · `fail_truncated is True` · 101번째는 없다 | `test_the_hundred_and_first_name_is_dropped_and_flagged` | 경계 바깥. 버리는 것까지는 조용해도 되지만 **버렸다는 사실은 밝힌다**(§2.3 `failures_truncated`) |
| G4 | `b, a, b, c, a` → `("b", "a", "c")` · `fail_truncated is False` | `test_duplicates_are_counted_once_and_the_order_is_the_jobs_own` | 중복은 한 번, 순서는 잡의 것. `set` 으로 모으면 순서가 무작위가 되고 `failures[]` 의 첫 줄(대개 진짜 원인)이 매번 달라진다 |
| G5 | 이름 100개 + 그중 하나를 한 번 더 → `fail_truncated is False` | `test_a_duplicate_after_the_cap_is_not_a_truncation` | 이미 센 이름은 아무것도 안 버린다. 「상한을 넘었다」를 마커 **개수**로 세면 반복이 많은 로그가 전부 truncated 로 찍히고 분모의 품질을 거짓말한다. **가정 1** |
| G6 | 스텝 `test` + 다른 이름 100개 + `fail::test`(101번째) → `fail_truncated is True` · `"test"` 없음 · `steps[0].ok is None` · `failed_step is None` | `test_a_dropped_name_does_not_mark_its_step` | 버린 이름은 **없는 이름**이다 — 대장에 안 들어간 이름으로 스텝을 칠하면 화면과 DB 가 어긋난다. **가정 2** |

## H. `Progress` 의 새 칸 · `progress_for_job` (§1.1 · `core/model.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| H1 | `Progress(phase=…)` → `last_step is None` · `fail_names == ()`(튜플) · `fail_truncated is False` | `test_the_new_progress_fields_default_to_empty` | 기본값이 셋 다 「없음」이어야 옛 호출부(`Progress(...)` 를 직접 만드는 테스트·서버 경로)가 안 깨진다. 스키마 v1 은 **키를 더하되 값을 안 바꾼다** |
| H2 | `progress_from_markers` 의 `fail_names` 가 `tuple` 이고 값이 순서대로 | `test_progress_from_markers_returns_a_tuple_of_names` | `Progress` 는 frozen dataclass 다 — 리스트를 담으면 값 비교·해시가 흔들리고 `list` 가 밖으로 새면 호출부가 제자리에서 고칠 수 있다 |
| H3 | 끝난 잡(`FAILED` · exit 1)에 `JOB_162` / `JOB_162_DECLARED` → `last_step == LAST_162` · `failed_step` 이 `None` / `"test"` · `fail_names == ("test",)` | `test_progress_for_job_carries_the_new_fields` | `progress_for_job` 은 잡의 `exit_code` 를 넘기는 실제 경로다. `progress_from_markers` 만 고치고 이쪽 인자를 안 맞추면 서버에서만 다르게 보인다 |

## I. `verdict` 의 경계 (§2.2 판정 표 · 결정 66)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| I1 | `min_jobs=3` 에서 `(1,2)` · `(2,2)` · `(5,2)` → `unknown`, `(0,0,min_jobs=1)` → `unknown` (×4) | `test_a_window_shallower_than_min_jobs_is_unknown` | 얕은 창 규칙이 **제일 먼저**다. `seen == window` 라고 `persistent` 를 먼저 내면 새 프리셋의 첫 실패가 「최근 2회 전부」로 보고된다 — 표본 2개로 내리는 판정이 이 기능의 신뢰를 통째로 깎는다 |
| I2 | `window == min_jobs == 3` 에서 `(1,3)`→`first_seen` · `(2,3)`→`intermittent` · `(3,3)`→`persistent` (×3) | `test_a_window_exactly_at_min_jobs_is_judged` | 경계 안쪽. `<` 를 `<=` 로 쓰면 창이 딱 찬 순간이 영영 `unknown` 이라 기본값(3)에서 판정이 한 칸씩 늦는다 |
| I3 | `(8,8)` · `(20,20)` → `persistent` (×2) | `test_seen_equal_to_the_window_is_persistent` | 창의 모든 잡에서 봤으면 간헐이 아니다. `>` 로 쓰면 절대 `persistent` 가 안 나오고 화면은 늘 `intermittent?` 라고 묻는다 |
| I4 | `(1,8)` · `(1,20)` → `first_seen` (×2) | `test_seen_once_is_first_seen` | 「내 변경일 가능성」을 말하는 유일한 판정. 이게 `intermittent` 로 새면 방금 깨뜨린 사람이 「원래 간헐이래」로 읽는다 |
| I5 | `(2,8)` · `(7,8)` · `(3,20)` → `intermittent` (×3) | `test_between_one_and_the_window_is_intermittent` | 사이값. 세 규칙 다 안 걸리는 자리가 기본값이라는 것 |
| I6 | `verdict(1, 1, min_jobs=1)` → **`persistent`** | `test_a_single_run_window_is_persistent_not_first_seen` | `seen >= window` 가 `seen == 1` 보다 **앞**이라는 순서 그 자체. 뒤집으면 창 1 짜리 설치에서 매번 `first_seen` 이 나온다 |
| I7 | `(9,8)` · `(2,1,min_jobs=1)` → `persistent` (×2) | `test_more_seen_than_the_window_is_still_persistent` | 방어적 경계 — 창 계산과 이름 계산이 다른 질의라(§2.1 ①②) 경합·경계에서 분자가 분모를 넘을 수 있다. `==` 로 쓰면 그때 `intermittent` 로 떨어진다 |
| I8 | 네 상수의 문자열 값과 서로 다름 · `seen`·`window` 0~10 격자 121칸이 전부 네 코드 중 하나 | `test_the_verdict_codes_are_locked` | 판정은 **코드**로 내려간다(결정 37) — 웹 i18n 키(`failures.*`)와 CLI 문구가 이 네 문자열에 걸려 있다. 격자는 「어떤 입력에도 `None` 이나 새 문자열이 안 나온다」를 잠근다 |
| I9 | `min_jobs` 가 KEYWORD_ONLY · `verdict(1, 8, 3)` → `TypeError` | `test_min_jobs_is_keyword_only` | 세 인자가 전부 int 다. 위치를 허용하면 `window` 와 `min_jobs` 를 바꿔 넣어도 조용히 돌고, 그 잘못은 화면의 숫자로만 드러난다 |

## J. `failures_json` — 모양 · 순서 · 분모 (§2.2 · §2.3)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| J1 | `row("test", 8)` · `steps={"test"}` · window 8 · unnamed 0 · min 3 → §2.2 의 예시 dict **그대로**(키 여덟, 값 여덟) | `test_a_row_becomes_exactly_the_documented_object` | 키 하나가 곧 웹·CLI 의 한 줄이다. `==` 로 통째로 비교해 **빠진 키도 늘어난 키도** 잡는다 |
| J2 | `row(TEST_DART, 1, first=162)` · `steps={"test"}` → `step: False` · `verdict: "first_seen"` · `first == last == 162` | `test_a_name_that_is_not_a_step_is_a_unit` | workplan §4.3 의 두 번째 항목 그대로. 테스트 **파일**은 스텝이 아니다 — 표시가 「스텝」과 「단위」를 이 칸으로 가른다 |
| J3 | 이름 `zulu`·`alpha`·`mike` 순서로 넣으면 결과도 그 순서 | `test_the_rows_keep_the_jobs_own_order` | §2.2 의 「이름 순이 아니라 **잡이 찍은 순서**(seq)」. 정렬하면 스크립트가 제일 먼저 찍은 이름(대개 진짜 원인)이 세 번째 줄로 밀리고 CLI 는 3줄만 찍는다(§2.5) |
| J4 | `steps={"test", "build web"}` · 행 셋 → `step == [True, False, True]` | `test_step_is_true_only_for_names_that_are_steps` | 이름 대조가 실제로 돈다는 것. 전부 `True` 로 두면 웹이 단위 실패를 스텝으로 그린다 |
| J5 | `steps` 가 tuple · list · frozenset · `{None, "test"}` → 전부 같은 답 (×8) | `test_the_steps_container_may_be_any_container` | §2.3 은 종료 잡의 `failed_step`·`last_step` **두 칸**을 넘긴다 — 둘 다 `None` 일 수 있어 `{None}` 이 실제로 온다. `set` 만 받거나 `None` 에서 터지면 실패 잡 문서가 500 이 된다 |
| J6 | window 8 · unnamed 2 · 행 셋 → 줄마다 `window == 8` · `window_unnamed == 2` | `test_the_window_and_its_quality_ride_on_every_row` | 결정 68 — 분모의 품질을 숨기지 않는다. 첫 줄에만 실으면 CLI 의 「note: …」 줄이 두 번째 이름부터 사라진다 |
| J7 | `seen` 8·1·4 · window 8 → `persistent`·`first_seen`·`intermittent` | `test_each_row_gets_its_own_verdict` | 판정은 행마다 다르다. 잡 단위로 하나만 내면 「간헐 하나 + 계속 빨간 것 하나」를 구별할 수 없다 |
| J8 | window 2 · min_jobs 3 · `seen` 2·1 → 둘 다 `unknown` · `window == 2` | `test_a_shallow_window_makes_every_verdict_unknown` | 얕은 창이 `failures_json` 을 지나서도 살아 있는지. 여기서 `verdict` 를 안 부르고 자체 판정을 쓰면 규칙이 두 벌이 된다 |
| J9 | `rows == []` → `[]` | `test_no_rows_is_an_empty_list` | **빈 배열은 「이름을 하나도 안 남겼다」**이고 키가 아예 없는 것은 「못 읽었다」(§2.3 fail-open 금지)이다. 둘을 섞으면 조회 실패가 「깨끗함」으로 보인다 |
| J10 | `first_seen_job_id`·`last_seen_job_id` 가 `None` → JSON 도 `None` | `test_unknown_job_ids_stay_null` | 집안 규칙 — 모르는 값은 `null` 이지 `0` 이 아니다. `0` 은 링크가 `/jobs/0` 이 된다 |
| J11 | `json.dumps` → `json.loads` 왕복이 같다 · 항목이 dict 이고 키가 전부 str | `test_the_result_is_plain_json` | 서버가 그대로 직렬화한다. `FailureRow` 나 `set` 이 새면 `GET /jobs/{id}` 가 통째로 500 이다 |
| J12 | `rows` 만 위치 인자, 나머지 넷은 KEYWORD_ONLY · 인자 이름 다섯이 명세대로 | `test_failures_json_takes_its_options_by_keyword` | `window` · `window_unnamed` · `min_jobs` 셋 다 int 다(I9 와 같은 이유). 이름까지 잠가야 §2.3 의 호출이 그대로 돈다 |
| J13 | 호출 뒤 `rows` 가 그대로 | `test_failures_json_does_not_mutate_its_rows` | 순수 함수의 기본. 호출부(`server._with_failures`)가 같은 리스트를 다시 쓸 수 있다 |

## K. `FailureRow` 와 모듈 경계 (순수 계층 · §2.2)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| K1 | 필드 이름 넷이 **순서대로** · 값이 같으면 `==` · 대입은 `FrozenInstanceError` | `test_failure_row_is_a_frozen_dataclass_with_four_fields` | `store.failure_stats` 가 만들고 `failures_json` 이 읽는 유일한 다리다. 위치 생성(`FailureRow("test", 8, 141, 162)`)이 쓰이므로 순서가 계약이다 |
| K2 | 모듈 원문에 `datetime.now` · `utcnow` · `time.time` · `time.monotonic` · `open(` · `random.` 이 하나도 없다 | `test_the_failures_module_never_reads_the_clock_or_the_filesystem` | `core/` 규칙. 창은 **인자로** 들어온다 — 안에서 시계를 보면 같은 잡 문서가 볼 때마다 달라지고 판정을 재현할 수 없다 |
| K3 | 모듈 원문에 `import sqlite3` · `remote_ci_monitor.store` · `remote_ci_monitor.config` 가 없다 | `test_the_failures_module_does_not_import_the_store_or_the_config` | 방향은 한쪽이다 — `store` 가 `FailureRow` 를 만들고 순수 모듈은 DB 를 모른다. 뒤집히면 `core/` 가 sqlite3 과 TOML 로딩 위에 얹힌다 |

---

**64건** — `tests/test_progress_m5h.py` 39건(A 8 · B 3 · C 6 · D 5 · E 6 · F 2 · G 6 · H 3),
`tests/test_failures.py` 25건(I 9 · J 13 · K 3). 파라미터를 펼치면 약 100건이다.
`ruff check` · `ruff format --check`(line-length 100, CJK 2폭) 통과.

## mutcheck ⑬ 이 빨개지는 자리 (§8)

`core/progress.py` 의 `failed` 를 `steps[-1].name` 폴백으로 되돌리면(뮤테이션 ⑬)
`tests/test_progress_m5h.py` 의 **여덟 건**이 빨개진다 — 참조 구현으로 실제로 확인했다.

| 빨개지는 테스트 | 왜 |
|---|---|
| **B1**(#162 증인) · C1 | 폴백 그 자체 |
| C6 · E2 · E4(앞 절반) · G6 | 「선언 없는 실패 = 빈칸」의 다른 얼굴들 |
| D4 · H3 | 이름이 스텝과 안 맞는 잡 · `progress_for_job` 경로 |

E2 가 특히 중요하다. `steps[-1].ok = exit_code == 0`(R4 를 안 고친 구현)만 남아도 그 `False` 가
R1 을 통과해 **폴백이 되살아난다** — 폴백 두 줄을 지우는 것만으로는 부족하다.

## 이 시나리오가 깨뜨리는 기존 테스트 (역할 A 는 안 고쳤다)

결정 63 은 동작 변경이다. 아래는 **추론된 `failed_step`** 을 그대로 단언하는 기존 테스트들이라
단계 1 을 구현하는 사람이 같은 커밋에서 고쳐야 한다(픽스처에 `::rcm::fail::` 를 넣거나 단언을
`failed_step is None` + `last_step` 으로 바꾼다).

| 파일 · 줄 | 지금 단언 |
|---|---|
| `tests/test_progress.py:118` | `test_nonzero_exit_without_fail_marker_blames_last_step` — `failed_step == "b"` |
| `tests/test_worker.py:141` · `tests/test_runner_local.py:125` | `bad` 프리셋(`::rcm::step::test` + exit 3) → `failed_step == "test"` |
| `tests/test_worker_api.py:1039` · `:1069` | `failed_step == "test"`(선언 없음) · `timed_out` 의 `failed_step == "build"` |
| `tests/test_server.py:269` · `tests/test_client.py:188` · `tests/test_cli_worker.py:497` | `bad` 프리셋(`echo ::rcm::step::t; exit 2`) → `failed_step == "t"` |
| `tests/test_e2e_loopback.py:168` | `fail` 갈래(`::rcm::step::test` + exit 1) → `failed_step == "test"` |
| `tests/test_progress_anchors.py:39` · `tests/test_status_schema.py:82` · `tests/test_status_outcome.py:89` | **키 집합**을 통째로 잠근 목록 — §1.4 가 `last_step` 을 더하면 여기도 같이 열어야 한다 |

## 가정 (명세가 안 정한 것 — 구현이 달리 정하면 이 테스트를 고쳐야 한다)

1. **상한을 넘은 「중복」은 잘림이 아니다** (G5). §1.1 1 은 「중복은 한 번만 센다」와 「`MAX_FAIL_NAMES`
   를 넘으면 버리고 `fail_truncated = True`」를 나란히 적었다. 이미 센 이름은 버릴 것이 없으므로
   중복 제거를 **먼저**, 상한을 뒤에 두는 쪽으로 잠갔다.
2. **버린 이름은 스텝도 안 칠한다** (G6). §1.1 3 의 「**모은** 이름과 같은 이름의 스텝」을 문자
   그대로 읽었다. 상한을 넘겨 버린 이름이 대장에는 없는데 스텝만 빨간 것보다 낫다고 봤다.
3. **선언은 열린 스텝을 닫지 않는다** (E6). §1.1 3 은 `ok = False` 만 말한다. 그래서 도는 잡에서
   `state == "running"` 이고 `ok is False` 인 스텝이 생긴다 — 웹 진행판이 이 조합을 그릴 수 있는지
   역할 C 가 확인해야 한다(오늘 `step-end::fail` 은 스텝을 **닫으므로** 이 조합은 새것이다).
4. **`exit 0` 인 잡도 순수 계층에서는 `failed_step` 을 갖는다** (C4). 억제는 §1.2 의
   `outcome_for` 가 상태로 한다. `progress` 에서도 같이 억제하면 두 곳이 같은 결정을 내리게 된다.
5. **`fail_names` 는 잘린 뒤의 이름**이다 (G1). `parse_marker` 가 120자로 자르므로 대장에
   들어가는 이름과 스텝 이름이 같은 자리에서 잘린다.

## 명세에 없는 것 — 답이 필요한 질문

1. **`seen == 0` 의 판정.** §2.2 표는 `window < min_jobs` → `unknown`, `seen >= window` →
   `persistent`, `seen == 1` → `first_seen`, 「그 밖」 → `intermittent` 다. `seen == 0` 은 문자
   그대로 `intermittent`(증거 0으로 「왔다 갔다 한다」)가 된다. §2.1 의 질의 ②는 창 안에 없는
   이름을 아예 행으로 안 만들므로 보통은 안 나지만, **창 밖으로 밀려난 옛 잡의 `GET /jobs/{id}`**
   에서는 그 잡이 찍은 이름이 `failures[]` 에서 통째로 **사라진다** — 「이름을 안 남겼다」와
   구별이 안 된다. 그래서 이 격자칸은 잠그지 않았다(I8 은 「네 코드 중 하나」만 본다).
2. **`fail_truncated` 와 `failures_truncated`.** §1.1·§1.3·§2.1 은 `fail_truncated`(Progress ·
   Job · 컬럼), §2.3 과 workplan §4.2 는 JSON 키 `failures_truncated` 다. 의도된 두 이름이라면
   그대로 두면 되지만, 한쪽 오타라면 **지금** 정해야 역할 B 의 테스트가 안 흔들린다.
3. **파일 이름.** 구현 문서 §6 은 `tests/test_failures.py`, workplan §8 은 `tests/test_flaky.py`
   다. 계약(구현 문서)을 따라 `tests/test_failures.py` 로 썼다.
4. **DB 버전.** 구현 문서 §1.3 은 v11 = `last_step`, §2.1 은 v12 = `job_failures` 인데
   workplan §4.3 은 「DB v11, 새 표 하나」라고 적었다. 역할 B 의 마이그레이션 테스트가 어느
   쪽을 잠글지는 구현 문서 쪽으로 읽었다.
5. **`::rcm::fail::` 가 스텝을 「닫는」 마커를 겸해야 하는가**(가정 3 의 반대편). 병렬 되재생
   스크립트는 `fail` 을 찍은 뒤에도 계속 로그를 붓는다. 지금 잠근 대로면 그 스텝은 다음
   `::rcm::step::` 이 올 때까지 열려 있고 `ok is False` 다.
