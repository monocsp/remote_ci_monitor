# M5h 테스트 시나리오 C — 표시와 클라이언트 (2026-09-09)

`docs/m5h-implementation.md` 의 **§1.5**(문구 갈림 · 알림 env) · **§2.5**(실패한 대기의 끝줄) ·
**§2.6**(웹의 이름별 배지 문구) · **§4.1**(`source_ident`) · **§4.2**(tree 잡의 브랜치와 불변식) ·
**§4.3**(`rcm jobs --ref`) · **§5**(대기 전에 놓는 스냅샷)를 테스트로 옮긴 것이다. 한 줄 요약:
**서버가 모르는 것을 화면이 아는 척하지 않게 하고**(`(step X)` ↔ `(last step X)`), 실패한 대기가
**길을 알려 주게** 하고, 목록이 **어느 코드였는지** 말하게 하고, 대기 클라이언트가 **안 쓰는 장부를
놓게** 한다.

이 문서가 **안 건드리는 것**: 마커 파싱과 `failed_step`·`last_step`·`fail_names` 규칙(§1.1 · R1~R6,
역할 A) · 판정 4종(`core/failures.py` §2.2, 역할 A) · `outcome_for` 의 상태별 갈래(§1.2) ·
DB v11 · v12 와 대장의 쓰기·읽기·삭제(§1.3 · §2.1, 역할 B) · `GET /jobs/{id}` 가 `failures` 를
만드는 자리(§2.3, 역할 B) · 설정 검증(§2.4) · 404 `hint`(§3). 그리고 **§1.4**(`core/status.py` 의
`recent_json`·`progress_json` 에 `last_step` 을 더하는 일)는 **어느 역할 표에도 없다** — 이 문서는
그 키가 행에 있다고 **가정**하고 표시만 잠근다(아래 「명세가 안 정한 것」 15).

대상 파일 넷: `tests/test_render_m5h.py`(순수) · `tests/test_cli_m5h.py` · `tests/test_client_m5h.py`
· `tests/web/m5h.test.js`. `src/` 는 한 글자도 안 건드렸고, 다른 역할의 시나리오 문서·테스트 파일도
안 건드렸다.

## 공통 — 픽스처와 규칙

**시각은 하나.** `tests/jobfactory.py` 의 `NOW`(2026-09-04 00:52:12 UTC)와 웹의 같은 상수를 쓴다.
sleep · 스레드 · 네트워크가 없다(§H 의 `rcm run` 만 in-process 서버를 쓰고, 그것도 워커가 없다).

**렌더 픽스처는 dict 다.** 최근 줄은 `render_pool({"recent": [행], …}, tz=UTC, now=NOW)` 로 그린다 —
`Job` 모델도 `status_json` 도 안 거친다. 그래서 이 파일은 §1.3(`Job.last_step`)·§1.4 의 구현을
**기다리지 않는다**. 행은 `recent_row(**바꿀 칸)` 이 만든다(#162 를 본뜬 실패 잡: `key="gate"` ·
`exit_code=1` · `summary="exit 1"` · tree 소스 `chore/ci-guard @25e1494`).

**꼬리를 읽는 법.** `tail_of(행)` 은 최근 줄을 두 칸 공백으로 잘라 **마지막 조각**(요약 + 스텝
라벨)을 준다. 코드 신원 칸이 요약 **앞**에 들어간다는 잠근 선택(아래 3) 위에 서 있다.

**대기 끝줄은 대본으로 잰다.** `cli.wait_for_job` 을 `(code, job, None)` 을 돌려주는 함수로 갈아
끼우고 `cli._wait(object(), 162, …)` 을 직접 부른다 — 종료 코드 1·2·3 을 **정해서** 주므로 진짜
실패 잡을 만들 필요가 없다. 줄은 `_err` 가 찍으므로 `rcm: ` 접두가 붙는다. `failure_line(err, 접두)`
가 그 접두를 떼고 한 줄을 돌려준다(**들여쓰기와 접두는 안 잠갔다** — 잠근 선택 5).

**`rcm jobs` 는 HTTP 없이 잰다.** `Client.status`(와 `--mine` 의 `Client.whoami`)만 갈아 끼운다.
행은 손으로 쓴 스키마 v1 문서라 순번도 시각도 안 흔들린다.

**스냅샷이 살아 있는지는 RSS 로 안 잰다.** `cli.make_snapshot` 을 감싸 **약한 참조**를 걸고,
`cli._wait` 을 대본으로 갈아 끼워 그 안에서 `gc.collect()` 뒤 `ref() is None` 을 본다. §4.6 이
「붙잡힌 객체 수로 잠근다」고 적은 그대로다 — 「31.6 MB → 64 MB」 같은 수는 CI 에서 결정적이지 않다.
구현 전에 이 확인이 **`alive is True`** 로 빨간 것을 실제로 확인했다(픽스처가 도는 증거).

**git 은 `tests/gitrepo.py` 로만 부른다.** `git()` 은 `GIT_CONFIG_NOSYSTEM` · 빈 전역 설정 ·
`init.defaultBranch=main` 으로 돈다 — 개발자의 `~/.gitconfig` 가 안 낀다. 실측(구현 전 스크래치):
같은 트리를 `main` → `feature/x` 로 옮기면 `tree_hash` 와 `base_sha` 가 **둘 다 그대로**이고,
`git checkout --detach` 뒤 `git rev-parse --abbrev-ref HEAD` 는 문자열 **`HEAD`** 를 준다.

## 잠근 API

명세가 이름만 준 자리에서 시험이 고른 모양이다. 구현이 달리 정하면 **이 문서와 테스트를 고친다.**

```python
# core/render_text.py
MAX_IDENT = 32
def source_ident(src: dict[str, Any] | None) -> str: ...
def failure_lines(job: dict, *, job_id: int, url: str | None, limit: int = 3) -> list[str]: ...

# core/notify.py  — notify_env() 의 반환 dict 에 키 하나
"RCM_LAST_STEP": sanitize_text(job_row.get("last_step"))

# client.py
Snapshot.branch: str | None          # detached 면 None ("HEAD" 라는 문자열이 아니다)

# core/model.py · core/status.py
Source.branch: str | None = None     # source_json() 의 tree 갈래에만 실린다
```

`failures[]` 항목의 키는 §2.2 그대로다(`name` · `step` · `seen` · `window` · `window_unnamed` ·
`first_seen_job_id` · `last_seen_job_id` · `verdict`). 웹의 새 i18n 키는 §1.5 의 `recent.last_step` ·
`recent.last_step_label` 과 §2.6 의 `failures.title|persistent|intermittent|first_seen|unknown|unnamed`
여덟이다.

## A. 최근 줄의 스텝 문구 갈림 — §1.5 (`tests/test_render_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 1 | `failed_step="test"` · `last_step` 없음 → 꼬리가 **정확히** `exit 1 (step test)` | `test_a_recent_row_with_a_declared_failed_step_says_step` | 선언이 있는 잡의 문면은 **오늘 그대로**다. 구현 전에도 초록이어야 하는 회귀 잠금 — 여기가 빨가면 픽스처가 틀린 것이다 |
| 2 | `last_step="build web"` 만 → 꼬리가 `exit 1 (last step build web)` 이고 `(step ` 이 **없다** | `test_a_recent_row_with_only_a_last_step_says_last_step` | #162 그 자체. 오늘은 추론값이 `(step build web …)` 으로 나와 **성공한 스텝을 범인으로 지목**한다(신고 1). 문면이 인과를 주장하지 않게 바꾸는 것이 이 마일스톤의 얼굴이다 |
| 3 | 둘 다 없음 → 꼬리가 `exit 1` (괄호 없음) | `test_a_recent_row_with_neither_step_shows_only_the_summary` | 결정 63 은 「오늘 라벨이 붙던 잡의 상당수가 빈칸이 된다」는 뜻이다. 빈칸이 `()` 나 `(step —)` 로 새면 안 된다 |
| 4 | 둘 다 있음 → `exit 1 (step test)` 이고 `last step` 이 **안 보인다** | `test_a_declared_failed_step_wins_over_the_last_step` | 갈림이 `if/elif` 라는 것. 두 라벨이 같이 나오면 한 줄에 서로 다른 두 스텝 이름이 놓인다 |
| 5 | 취소 잡(`state="cancelled"` · 두 칸 다 null) → 줄이 `cancelled by macbook` 으로 끝나고 `(step`·`(last step` 이 하나도 없다 | `test_a_cancelled_row_carries_no_step_label_at_all` | **#176 의 증인**(신고 6). 「요약에 무관한 문자열이 섞였다」의 정체가 취소 잡의 실패 스텝이었다. 결정 64 가 지켜지는지는 화면에서 보인다 |

## B. 알림 env — §1.5 `core/notify.py` (`tests/test_render_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 6 | `{"last_step": "build web"}` → `RCM_LAST_STEP == "build web"` | `test_notify_env_exports_the_last_step` | 훅이 「어디까지 갔나」를 읽을 수 있어야 §1.5 표의 마지막 줄이 지켜진다 |
| 7 | `failed_step=None` · `last_step="build web"` → `RCM_FAILED_STEP == ""` **이고** `RCM_LAST_STEP` 은 채워진다 | `test_notify_env_never_fills_the_failed_step_from_the_last_step` | 「선언된 것만」이 env 에서도 참이어야 한다. 폴백이 여기 남으면 훅이 **엉뚱한 스텝 이름으로 커밋 status** 를 남긴다 — 사람이 보는 화면보다 오래 남는 거짓말이다 |
| 8 | 성공 잡 → 두 키 모두 `""` (키 자체는 있다) · `last_step` 에 제어문자 + 5,000자 → 제어문자가 지워지고 4,096바이트 이하 | `test_notify_env_always_has_both_step_keys_and_sanitizes_the_last_step` | env 키 집합은 잡마다 같아야 훅이 `${RCM_LAST_STEP}` 을 그냥 쓸 수 있다. 정화는 다른 사용자 문자열과 **같은 규칙**이어야 한다(`sanitize_text`) |

## C. `failure_lines()` — §2.5 순수 (`tests/test_render_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 9 | `failures` 키 **없음** · url 있음 → 줄 목록이 `["log: rcm logs 162 · http://macmini:8787/#/jobs/162"]` 하나 | `test_the_log_line_names_the_command_and_the_url` | 신고 2 의 답. 대장을 못 읽은 서버(§2.3 의 fail-open 금지)라도 **길은 잃지 않는다** |
| 10 | url `None` → `["log: rcm logs 162"]` | `test_the_log_line_without_a_url_is_just_the_command` | 꼬리에 빈 `· ` 를 남기지 않는다. 서버 주소를 모르는 세션이 흔하다 |
| 11 | `failures: []` → 역시 로그 줄만 | `test_an_empty_failure_list_is_not_the_same_as_a_missing_one_but_prints_the_same_line` | 빈 배열(「이름이 없다」)과 키 없음(「못 읽었다」)은 **뜻이 다르지만 화면은 같다** — 다르게 그리려는 구현을 막는다 |
| 12 | `seen=8 · window=8 · verdict=persistent` → `failed: test — every one of the last 8 gate runs` | `test_a_persistent_name_says_every_one_of_the_last_runs` | 네 판정의 문구는 계약이다(§2.5 표). `{key}` 가 잡의 key(`gate`)라는 것도 여기서 잠근다 |
| 13 | `seen=3 · window=8 · verdict=intermittent` → `… — 3 of the last 8 gate runs · intermittent?` | `test_an_intermittent_name_keeps_the_question_mark` | **물음표가 계약**이다(§2.5). 판정이 아니라 제안이라는 뜻이고, 그것을 지우면 rcm 이 안 가진 확신을 주장한다 |
| 14 | `seen=1 · verdict=first_seen` → `… — first time in the last 8 gate runs` | `test_a_first_seen_name_says_first_time` | 처음 본 이름을 「간헐」이라고 부르면 사람이 재시도로 넘긴다 |
| 15 | `window=2 · verdict=unknown` → `… — 1 of 2 gate runs so far` 이고 `intermittent` 라는 낱말이 **없다** | `test_a_shallow_window_says_so_far_and_never_judges` | 창이 얕으면(`window < min_jobs`) 판정하지 않는다(결정 66). 숫자는 보여 주되 이름은 안 붙인다 |
| 16 | 이름 5개(`e d c b a` 순) → 앞 3개가 **입력 순서 그대로** · 넷째 줄이 `… and 2 more (rcm logs 162)` · 줄 수 5 | `test_the_name_lines_keep_the_jobs_own_order_and_stop_at_three` | 이름 순으로 정렬하면 잡이 찍은 순서(seq)가 사라진다(§2.2). 그리고 끝줄은 요약이다 — 스무 개를 쏟으면 아무도 안 읽는다 |
| 17 | 이름 정확히 3개 → 줄 수 4 · `more` 줄 없음 | `test_exactly_three_names_need_no_more_line` | 경계. 「3개 넘으면」이 「3개 이상이면」으로 구현되는 실수 |
| 18 | `limit=1` · 이름 5개 → 이름 한 줄 + `… and 4 more (rcm logs 162)` | `test_the_limit_is_an_argument_and_the_remainder_is_counted_from_it` | 나머지 수는 **상한에서** 센다(5-1), 3 에서 세지 않는다 |
| 19 | `window_unnamed=2 · window=8` → **마지막 줄**이 `note: 2 of those 8 runs failed without naming anything` | `test_an_unnamed_failure_in_the_window_is_declared_as_a_note` | 결정 68 — 분모의 품질을 밝힌다. 이 줄이 없으면 `1 of 8` 이 실제보다 낙관적으로 읽힌다 |
| 20 | `window_unnamed=0` → `note:` 줄 없음 · 줄 수 2 | `test_a_clean_window_gets_no_note_line` | 깨끗한 창에 「0회는 이름 없이 실패했다」를 찍으면 매 실패마다 잡음이 는다 |
| 21 | 성공 잡(`state="succeeded"` · exit 0) → 빈 목록 | `test_a_succeeded_job_gets_no_lines_at_all` | 초록에 로그 안내를 얹지 않는다. **잠근 선택 4** — 갈래가 순수 함수에 있다고 정했다 |

## D. `source_ident()` — §4.1 순수 (`tests/test_render_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 22 | `git_ref` · `ref="main"` · sha → `main @25e1494` | `test_a_git_ref_source_is_the_ref_and_a_short_sha` | sha 는 **7자**다. 전체 40자를 넣으면 목록의 칸이 무너진다 |
| 23 | tree + `branch="chore/ci-guard"` → `chore/ci-guard @25e1494` | `test_a_tree_source_with_a_branch_is_the_branch_and_a_short_base_sha` | 신고 3 의 답 — 신고자가 sha 로 자기 잡을 찾던 이유다 |
| 24 | tree + branch + `dirty=True` → `chore/ci-guard @25e1494+` | `test_a_dirty_tree_gets_a_plus_after_the_sha` | `+` 는 「올린 트리가 그 커밋이 아니다」다. 자리는 **sha 뒤**이고 `_source_text` 의 `+uncommitted` 와는 다른 표기다(칸이 좁다) |
| 25 | tree · branch 없음 · `repo="git@github.com:org/app"` → `app @25e1494` (dirty 면 `app @25e1494+`) | `test_a_tree_without_a_branch_falls_back_to_the_last_piece_of_the_repo` | 브랜치를 안 보내던 옛 클라이언트의 잡도 자기 코드를 말한다. 여기서 저장소 **전체 주소**를 쓰면 칸이 넘친다 |
| 26 | `None` · `{}` · `{"mode": "tree"}` · `{"mode": "git_ref"}` → 전부 `—` | `test_a_source_without_anything_to_say_is_a_dash` | 「모르는 값은 `—`」(집안 규칙). 빈 문자열이면 칸이 사라져 그 다음 칸이 앞으로 밀린다 |
| 27 | 44자짜리 신원 → 길이가 정확히 32 · 끝이 `…` · 앞 31자가 그대로 | `test_a_long_identity_is_cut_from_the_back_and_marked` | **잠근 선택 6**(자르기 산술). 브랜치 이름은 길다 — 자르되 잘렸다고 말한다 |
| 28 | 정확히 32자 → 안 자른다 · `…` 없음 | `test_an_identity_of_exactly_thirty_two_characters_is_left_alone` | 경계. `>` 를 `>=` 로 쓰면 딱 맞는 이름의 마지막 글자를 먹는다 |
| 29 | 갈래 다섯(200자 브랜치 · 200자 repo · 200자 ref 포함) → 전부 `len <= MAX_IDENT` | `test_no_branch_ever_makes_the_column_wider_than_the_limit` | 상한은 갈래마다 따로 걸리기 쉽다. 한 갈래만 새도 표의 열이 흔들린다 |

## E. 그 칸이 놓이는 자리 — §4.1 표시 (`tests/test_render_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 30 | 최근 행(tree + branch) → 줄에 `chore/ci-guard @25e1494` 가 있고 **요청자 뒤 · 요약 앞** | `test_the_recent_line_says_which_code_ran` | §4.1 은 「`rcm top` 의 최근 줄에도 넣는다」고만 한다. 자리를 안 정하면 A 그룹의 꼬리 단언이 흔들린다 — **잠근 선택 3** 이 그 자리다 |
| 31 | 큐 행 → `git@github.com:org/app @25e1494+uncommitted` 가 그대로 | `test_the_queue_row_still_uses_the_long_source_text` | §1.5·§4.1 이 「큐 행은 그대로」라고 못박았다. `_source_text` 를 `source_ident` 로 갈아 끼우는 「정리」를 막는 회귀 잠금(구현 전에도 초록) |

## F. 실패한 대기의 끝줄 — §2.5 `cli._wait` (`tests/test_cli_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 32 | 종료 코드 1 · 이름 둘(persistent · first_seen) → stderr 에 로그 줄과 두 이름 줄, stdout JSON 의 `wait_exit_code == 1` | `test_a_failed_wait_ends_with_the_log_path_and_the_named_history` | §5 의 화면 그대로. **JSON 은 안 바뀐다** — 래퍼가 stdout 마지막 줄을 jq 로 읽는다 |
| 33 | 종료 코드 1 · 2 · 3 각각 → 셋 다 로그 줄 | `test_every_non_zero_exit_gets_the_log_path` | 결정 70. 「모를수록 로그가 필요하다」 — 3(모름)에서 안내를 빼면 가장 필요한 사람이 못 받는다 |
| 34 | 종료 코드 0(succeeded) → stderr 에 `rcm logs` 가 **없다** | `test_a_green_wait_says_nothing_about_logs` | 초록에 잡음을 얹지 않는다. 구현 전에도 초록인 잠금 |
| 35 | `failures` 키 없는 실패 잡 → 로그 줄만, `failed:` 줄 없음 | `test_a_job_document_without_failures_still_gets_the_log_path` | 옛 서버·조회 실패에서 끝줄이 통째로 사라지면 안 된다 |
| 36 | `_err` 와 `_print_json` 을 **한 리스트**에 기록 → `json` 항목이 정확히 1개이고 **마지막**, 그 앞에 `log: rcm logs 162` 로 시작하는 `err` 가 있다 | `test_the_end_lines_come_before_the_json_line` | 두 스트림을 한 시간축으로 본다. 순서가 뒤집히면 `rcm run … \| tail -1 \| jq` 가 사람용 문장을 먹는다(§2.5 「`_print_json(out)` 앞」) |
| 37 | 잡 문서에 `url` 이 없다 → `log: rcm logs 162` | `test_the_url_comes_from_the_job_document_and_may_be_absent` | §2.5 는 `url` 을 인자로만 말하고 **어디서 오는지 안 말한다** — 잠근 선택 2 |
| 38 | 이름 5개 → `failed: ` 줄이 정확히 3개 + `… and 2 more (rcm logs 162)` | `test_the_history_stops_at_three_names_and_sends_the_rest_to_the_log` | 순수 함수의 상한(시나리오 16)이 CLI 를 통과해서도 지켜지는지 — `_wait` 이 `limit` 을 안 넘기고 자기 값으로 부르는 실수 |

## G. `rcm jobs` — 코드 신원과 `--ref` · §4.1 · §4.3 (`tests/test_cli_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 39 | 최근 tree 잡 한 줄 → `chore/ci-guard @25e1494` 가 있고 자리가 **요청자 → 신원 → `took`** | `test_a_jobs_row_says_which_code_ran` | 오늘 `rcm jobs` 한 줄에는 코드 신원이 **없다**(§3.2). 자리는 workplan §4.5 의 예시 순서다 |
| 40 | git_ref 잡 → `main @25e1494` · 소스가 `{}` 인 잡 → `—` | `test_a_git_ref_row_shows_the_ref_and_an_unknown_source_shows_a_dash` | 두 모드가 **같은 칸**을 쓴다. 모르는 잡에서 칸이 사라지면 열이 밀린다 |
| 41 | 세 행(branch `chore/ci-guard` · ref `main` · branch `dev`) · `--ref ci-guard` → 첫 행만 · `--ref ain` → git_ref 행만 | `test_ref_keeps_only_rows_whose_branch_or_ref_contains_the_value` | §4.3 의 전부 — **부분 일치**이고 `source.ref` 와 `source.branch` **둘 다** 본다. 한쪽만 보면 tree 세션이나 git_ref 세션 중 하나가 못 찾는다 |
| 42 | `--ref CI-GUARD` → `no jobs` | `test_ref_is_case_sensitive` | §4.3 이 「대소문자 구분」이라고 못박았다. 브랜치 이름은 대소문자가 다른 것을 다른 것으로 센다 |
| 43 | `--ref ci-guard --state failed` → 그 둘을 다 만족하는 행만 | `test_ref_combines_with_state` | 「함께 쓸 수 있다」(§4.3). 새 필터가 기존 필터를 덮어쓰면 조용히 잘못된 목록이 나온다 |
| 44 | `--ref ci-guard --mine` → 내 잡이면서 그 브랜치인 행만 | `test_ref_combines_with_mine` | 같은 이유. `--mine` 은 토큰 신원이라 한 기계의 여러 세션을 못 가른다(§13-3) — `--ref` 가 그 위에 얹혀야 뜻이 있다 |
| 45 | `--ref dev --json` → JSON 배열이 `[164]` 하나 | `test_ref_filters_the_json_output_too` | 사람 눈과 스크립트가 **같은 목록**을 봐야 한다. `--json` 이 필터 앞에서 빠져나가는 배치가 흔한 실수다(오늘 코드에서 `--json` 은 `--state` 뒤에 있다) |
| 46 | `--ref` 없음 → 두 행 다 나온다 | `test_without_ref_nothing_is_filtered` | 구현 전에도 초록인 회귀 잠금 — 기본 동작이 안 바뀐다 |
| 47 | `rcm jobs --help` 에 `only jobs whose ref or branch contains REF` · `parse_args` 가 `args.ref` 를 채운다 | `test_the_ref_help_says_what_it_matches` | 도움말 문구는 계약이다(§4.3). 줄바꿈은 터미널 폭을 타므로 공백을 눌러서 본다 |

## H. `rcm run` — 브랜치를 싣고 대기 전에 장부를 놓는다 · §4.2 · §5 (`tests/test_cli_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 48 | git 체크아웃(`main`)을 `rcm run ok --no-wait --dir` → `Client.submit` 이 받은 `source["branch"] == "main"` · `tree_hash` 도 그대로 있다 | `test_a_tree_submit_carries_the_branch` | §4.2 의 클라이언트 갈래. 서버가 그 키를 무시해도(오늘) 이 시험은 **클라이언트가 보내는지**만 본다 |
| 49 | 대기에 들어간 순간 `gc.collect()` 뒤 스냅샷의 약한 참조가 `None` | `test_the_snapshot_is_released_before_the_wait` | 신고 4 — 2만 파일에서 장부만 32 MB 이고 대기 20분 내내 안 내려온다. **RSS 를 안 잰다**(§4.6). 구현 전 이 값은 `True`(살아 있다)로 빨갛다 |
| 50 | `--fetch-artifacts` → 스냅샷은 죽었고 `_FetchSpec.baseline` 에 `hello.txt` 가 있다 | `test_fetching_artifacts_keeps_the_baseline_but_not_the_snapshot` | 필요한 것은 `{경로: sha256}` 하나뿐이다(§5). 「필요하니까 통째로 들고 있자」를 막는다 |
| 51 | 대기 뒤 스냅샷의 `tar_path` 가 **없다** | `test_releasing_the_snapshot_does_not_skip_the_tar_cleanup` | 장부를 놓는 자리는 업로드 `finally` **뒤**다. 그 앞에서 `snap = None` 을 하면 tar 이 임시 폴더에 남는다(224 MB 짜리가 남은 적이 있다) |

## I. 스냅샷의 브랜치와 합류 불변식 — §4.2 (`tests/test_client_m5h.py`)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 52 | 커밋 하나짜리 체크아웃 → `snap.branch == "main"` · `base_sha` 는 그대로 | `test_a_checkout_snapshot_carries_its_branch` | §4.2 의 새 칸 하나 |
| 53 | `git checkout --detach` → `snap.branch is None` | `test_a_detached_head_has_no_branch` | 실측: git 은 그 상태에서 문자열 **`HEAD`** 를 준다. 그대로 실으면 목록에 `HEAD @25e1494` 가 뜨고 `--ref HEAD` 가 엉뚱한 잡을 잡는다 |
| 54 | git 이 아닌 디렉터리 → `branch is None` · `base_sha is None` | `test_a_directory_that_is_not_a_checkout_has_no_branch` | git 이 없는 트리도 잡을 낸다. 모르는 값은 null 이다 |
| 55 | 같은 트리를 `main` · `feature/x` 에서 스냅샷 → **`tree_hash` 가 같다** · `base_sha` 도 같다 · `branch` 만 다르다 | `test_the_branch_never_changes_the_tree_hash` | **잠근 불변식 ①**(§4.2 · 결정 71). 브랜치가 해시에 들어가면 같은 트리가 브랜치마다 새 잡이 되고 캐시·합류가 통째로 무너진다 |
| 56 | 그 두 스냅샷으로 만든 `Source` → `identity` 가 같고 `join_key("gate", {…}, identity)` 도 같다 | `test_two_branches_with_the_same_tree_still_join_the_same_job` | **잠근 불변식 ②**. 「같은 트리를 다른 브랜치에서 올린 두 세션은 지금처럼 합류한다」를 합류 키 자체로 잰다 |
| 57 | `source_json(Source(mode="tree", branch=…))["branch"]` 가 그 값 · 브랜치 없는 잡은 `None` | `test_the_tree_source_document_carries_the_branch_and_nulls_it_for_old_jobs` | §4.2 「마이그레이션이 없다 — 옛 잡은 null」. 0 이나 빈 문자열이면 화면이 「브랜치 없음」을 못 가른다 |
| 58 | `source_json(Source(mode="git_ref", …))` 에 `branch` 키가 **없다** | `test_a_git_ref_source_document_has_no_branch_field` | 한 가지에 이름 하나 — git_ref 의 코드 신원은 `ref` 다. 구현 전에도 초록인 잠금 |

## J. 웹 — §1.5 문구 갈림 · §2.6 배지 (`tests/web/m5h.test.js`)

| # | 시나리오(설정 → 기대) | 테스트 함수(TAP 이름) | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| 59 | `failed_step="test"` → 요약이 `exit 1 · step test`(en) / `exit 1 · 스텝 test`(ko) | `선언된 failed_step 은 오늘 그대로 「스텝 X」` | 오늘 문면은 안 바뀐다(두 언어 모두). 구현 전에도 초록 |
| 60 | `last_step="build web"` 만 → `exit 1 · last step build web` / `exit 1 · 마지막 스텝 build web` · 「스텝 X」가 **없다** | `last_step 뿐이면 「마지막 스텝 X」 — 인과를 주장하지 않는다` | 화면의 #162. 두 언어를 **같은 시험**에서 도는 이유: 한쪽만 고치면 언어를 바꾸는 순간 뜻이 달라진다 |
| 61 | 둘 다 없음 → 요약뿐 | `둘 다 없으면 요약뿐이다 — 괄호도 라벨도 없다` | 빈 라벨(`· `)이 붙는 것을 막는다 |
| 62 | 둘 다 있음 → 「스텝 X」만 | `둘 다 있으면 선언이 이긴다` | 터미널(A-4)과 같은 갈림. 두 화면이 다른 순서를 타면 같은 잡이 달리 읽힌다 |
| 63 | 취소 잡(두 칸 다 null · `started_at` 있음) → 요약이 `cancelled by macbook` 그대로 | `취소된 잡은 두 칸이 다 비어 있어 스텝 라벨이 없다 (#176 · 결정 64)` | #176 의 웹 쪽 증인 |
| 64 | `recent.step` · `recent.last_step` · `recent.failed_step` · `recent.last_step_label` 네 키의 **정확한 문면**(두 언어) | `스텝 두 갈래의 문구가 두 언어에 있다` | §1.5 의 「잠근 문구」 표 그대로. 스텝 **이름**(`test` · `build web`)은 두 언어에서 번역되지 않는다 — 잡이 찍은 식별자다 |
| 65 | `failures.` + `title`·`persistent`·`intermittent`·`first_seen`·`unknown`·`unnamed` 여섯 키의 정확한 문면(두 언어) | `이름별 실패 배지의 여섯 문구가 두 언어에 있다` | §2.6 표. 영어의 `· intermittent?` 물음표와 한국어의 `· 간헐?` 이 둘 다 계약이다 |
| 66 | 판정 코드 4종으로 만든 문구가 두 언어에서 비어 있지 않고 `undefined`·`NaN` 이 없다 | `판정 이름(verdict)은 번역하지 않는다 — 문구만 언어를 탄다` | 서버가 보내는 것은 코드다(결정 37). 키가 코드와 1:1 이 아니면 화면이 조용히 빈칸을 그린다 |
| 67 | `app.js` 원문에 `recent.last_step_label` 과 `job.last_step` 이 있고 **한자리에 붙어** 있다 | `상세가 「마지막 스텝」 갈래를 실제로 읽는다` | 최근 **상세**는 `renderRecent` 안의 DOM 문자열이라 node 시험이 못 부른다(브라우저 시험은 `tests/test_web_browser.py`, 내 파일이 아니다). 카탈로그에 키만 넣고 화면이 안 읽는 상태를 잡는 최소한의 잠금 — **명세가 안 정한 것 16** 을 보라 |

**67 건.** 파일별로 31(`test_render_m5h.py`) · 20(`test_cli_m5h.py`) · 7(`test_client_m5h.py`) ·
9(`tests/web/m5h.test.js`)이다(파라미터를 풀면 pytest 항목 64개 + node 항목 9개).

## 잠근 선택 (명세가 안 정한 것 — 구현이 달리 정하면 테스트를 고쳐야 한다)

1. **`failure_lines` 는 항목의 `verdict` 를 읽는다** (12~15). `seen`·`window`·`min_jobs` 로 다시
   계산하지 않는다 — 판정은 `core/failures.py`(역할 A)의 것이고 표시는 그 코드를 문장으로 바꿀
   뿐이다(결정 37 과 같은 결). 모르는 `verdict` 값이 왔을 때의 동작은 **안 잠갔다**.
2. **로그 줄의 `url` 은 잡 문서의 `url` 키에서 온다** (37). `_wait` 이 `client.server` 로 URL 을
   조립하는 구현도 가능하지만, 잡 문서가 이미 그 값을 들고 온다(`recent_json`·`job_view` 의 `url`).
3. **코드 신원 칸은 요약 앞, 요청자 뒤** (30 · 39). §4.1 은 자리를 안 정한다. workplan §4.5 의 예시
   줄(`… macbook@PCS-MACBOOK-PRO  chore/… @25e1494  took 11m  16:50  exit 1`)을 그대로 따랐다.
   **A 그룹의 꼬리 단언이 이 선택 위에 서 있다** — 칸을 줄 끝에 붙이면 A 1~5 를 고쳐야 한다.
4. **성공 잡의 갈래는 순수 함수에 있다** (21 · 34). `failure_lines(succeeded job)` 이 `[]` 다.
   `_wait` 이 `if code != 0` 으로 감싸는 구현도 가능하지만, 그러면 순수 함수가 성공 잡에도 로그
   줄을 내는 셈이라 뜻이 흐려진다. 구현이 후자를 고르면 **21 을 옮긴다**(34 는 그대로 산다).
5. **끝줄의 들여쓰기와 접두는 안 잠갔다** (32~38). `_err` 가 붙이는 `rcm: ` 접두를 시험이 떼고 본다.
   workplan §5 의 화면은 두 칸 들여쓰기지만 §2.5 의 표는 줄 **내용**만 말한다.
6. **자르기 산술은 `full[:31] + "…"`** (27 · 28). 결과 길이가 `MAX_IDENT`(32)를 넘지 않고 `…` 를
   포함한다. 「32자까지 남기고 그 뒤에 `…`」(33자)도 가능한 읽기다.
7. **`repo` 의 마지막 조각은 `/` 뒤** (25). `.git` 접미사 처리는 **안 잠갔다** — 픽스처가 `.git` 없는
   주소를 쓴다(명세가 안 정한 것 10).
8. **`--ref` 는 큐 행과 최근 행에 똑같이 걸린다** (41~45). `cmd_jobs` 가 두 목록을 한 `rows` 로 모은
   뒤 거르므로 자연스러운 배치다.
9. **웹 상세는 원문 스캔으로만 잠근다** (67). 순수 이음매가 없다 — 명세가 안 정한 것 16.
10. **시간을 재는 단언이 하나도 없다** (전부). 스냅샷 해제도 RSS 가 아니라 **약한 참조**로 본다.

## 명세가 안 정한 것 (테스트 작성자가 혼자 못 정한다 — 계약을 고쳐 주세요)

아래 1~4 는 **구현 명세(`m5h-implementation.md`)와 작업 명세(`m5h-workplan.md`)가 서로 다른 문면을
말하는 자리**다. 시험은 「구현 명세가 계약」이라는 지침대로 **전자**를 잠갔다.

1. **로그 줄의 전문.** §2.5: `log: rcm logs {id}` + url 이 있으면 ` · {url}`.
   §4.4: `rcm wait`·`rcm run` 이 「stderr 마지막 줄에 로그 길을 적는다:
   `job #162 failed · log: rcm logs 162 · http://macmini:8787/#/jobs/162`」. 앞머리
   (`job #162 failed · `)가 있는지 없는지가 다르다. 시험은 §2.5 로 잠갔고, 접두가 붙는다면
   32·33·35·37 의 등식을 부분 일치로 바꿔야 한다.
2. **이름 줄의 모양.** §2.5: `failed: {name} — {history}`(em dash). §5 의 화면:
   ```
   failed: test                                    every one of the last 8 gate runs
   failed: just_audio_screen_music_port_test.dart  1 of the last 8 gate runs · intermittent?
   ```
   — 대시가 없고 **열을 맞춘** 모양이다. 열 맞춤은 이름 길이에 따라 폭이 변하므로 순수 함수의
   계약으로는 훨씬 무겁다. 시험은 em dash 로 잠갔다.
3. **분모 품질 줄.** §2.5: `note: {n} of those {window} runs failed without naming anything`.
   §5: `(2 of those 8 runs failed without naming anything)` — 괄호이고 `note:` 가 없다.
   (웹의 `failures.unnamed`(§2.6)에는 `{window}` 가 아예 없다: `{n} of those runs failed …`.)
4. **§5 의 예시 판정이 §2.2 의 표와 어긋난다.** 예시는 `seen=1` 에 대해
   `1 of the last 8 gate runs · intermittent?` 를 보여 주는데, §2.2 의 판정 표는 `seen == 1` →
   `first_seen` → `first time in the last 8 gate runs` 다. 예시를 고치거나(권장) 판정 표를 고쳐야
   한다. 시험은 판정 표를 따랐다.

그리고 명세가 **아직 아무 말도 안 한** 자리:

5. **`failures[]` 의 `step` 을 CLI 가 쓰나.** §2.2 는 「표시가 「스텝」과 「단위」를 구분한다」고
   적었는데 §2.5 의 잠근 문구에는 그 구분이 **없다**(둘 다 `failed: {name}`). 시험은 구분 없음으로
   잠갔다 — 구분한다면 문구를 §2.5 에 적어 주세요.
6. **`failures_truncated` 의 문면.** §2.3 이 잡 문서에 싣는데 §2.5·§2.6 에 그 줄이 없다. 100개를
   넘겨 버린 잡에서 CLI·웹이 무엇을 말하나? 시험은 아무것도 단언하지 않았다.
7. **`note:` 줄이 상한에 포함되나.** 시험은 「이름 줄만 3줄로 자르고 `note:` 는 별도」로 잠갔다
   (19·20). 「끝줄 전체가 3줄」이면 `note:` 가 이름을 밀어낸다.
8. **`failures` 가 비었는데 `window_unnamed` 가 있는 창.** `window_unnamed` 는 항목마다 실려 있어
   (§2.2 의 JSON) 항목이 없으면 실을 자리가 없다. 그런 창에서 분모 품질을 말할 방법이 없다.
9. **`{key}` 가 없는 잡.** `failure_lines` 는 `job["key"]` 를 문구에 넣는데, 그 키가 없거나
   `null` 인 문서에서 무엇을 쓰나(시험은 언제나 `gate` 를 준다).
10. **`source_ident` 의 repo 조각과 `.git`.** `git@github.com:org/app.git` 은 `app.git` 인가
    `app` 인가. 그리고 `repo` 가 `null` 인 tree 잡(=대부분의 로컬 체크아웃)은 브랜치가 없을 때
    무엇을 보이나 — 시험은 `—` 로 잠갔지만(26) `base_sha` 는 있는 상태다.
11. **`source_ident` 의 결손 갈래.** git_ref 인데 `sha` 가 없다 / tree 인데 `base_sha` 가 없다
    (업로드 전)의 문면이 없다. `_source_text` 는 그 자리에서 `—` 와 `not received yet` 을 쓴다.
12. **`rcm top` 최근 줄의 형태.** §5 는 `… exit 1 · last step: build web …`(가운뎃점 + 콜론)으로,
    §1.5 는 `" (last step {last_step})"`(괄호)로 적었다. 시험은 §1.5 를 잠갔다.
13. **`--ref` 가 `--pool` 과 겹칠 때.** 문제될 것은 없지만 명세에 조합이 `--mine`·`--state` 만
    적혀 있다(시험도 그 둘만 잰다).
14. **`Snapshot.branch` 를 채우는 시점.** §4.2 는 `make_snapshot` 이라고 말한다. `git rev-parse` 가
    실패하거나(빈 레포) 60초 타임아웃일 때 `None` 인지 예외인지는 안 적혀 있다(시험은 `None` 을
    기대하는 갈래만 잰다 — 54).
15. **§1.4 가 어느 역할의 것도 아니다.** `recent_json`·`progress_json` 에 `last_step` 을 더하는 일이
    A·B·C 표 어디에도 없다. 표시(A 그룹 · J 그룹)는 그 키가 행에 있다고 가정하는데, **그 키를
    싣는다는 테스트는 이 세 문서 어디에도 없다.** 누군가에게 배정해야 한다.
16. **웹 최근 상세에 순수 이음매가 없다.** 요약 줄은 `recentLine()`(순수 · 노출됨)이라 두 언어를
    제대로 잴 수 있는데, 상세(`rdetail`)는 `renderRecent` 안의 DOM 문자열이라 `node --test` 가 못
    부른다. `recentDetail(job, lang) -> string[]` 같은 순수 함수를 하나 빼 주면 67 을 진짜 단언으로
    바꾼다.

## 이 마일스톤이 **기존 테스트**를 건드리는 자리 (구현이 처리해야 한다)

테스트를 약하게 만드는 것이 아니라 **인자와 목록을 채우는** 일이다. 미리 알고 시작하라고 적는다.

1. `tests/test_notify_rules.py` 의 `ENV_KEYS` 집합과 `assert set(env) == ENV_KEYS`(`:145`·`:198`)는
   `RCM_LAST_STEP` 이 늘면 **빨개진다**. 그 집합에 키를 더해야 한다.
   (`tests/test_notify.py` 의 `ENV_KEYS` 는 `all(k in e …)` 라 그대로 초록이다.)
2. `tests/web/i18n.test.js` 의 「모든 키가 두 언어에서 비어 있지 않은 문자열을 만든다」는 인자 표에
   `window` 와 `seen` 이 **없다** — `failures.*` 키가 들어오는 순간 `undefined` 가 섞여 빨개진다.
   그 표에 `window: 8, seen: 3` 을 더해야 한다.
3. `tests/test_render_text.py::test_recent_row_shows_exit_and_failed_step`(`:84`)는 `failed_step` 이
   **선언된** 잡이라 그대로 초록이어야 한다. 빨개지면 §1.5 의 갈림이 잘못 짜인 것이다.

## 검증 방법 (구현 전 · 후)

- **구현 전에 빨개야 하는 것**: D·C 그룹은 `ImportError: cannot import name 'MAX_IDENT'`(같은 모듈의
  `failure_lines` 도 아직 없다) · A-2 는 꼬리가 `exit 1` 로 나와서 · B 는 `KeyError` ·
  F 는 stderr 가 **빈 문자열**이라서 · G 는 `--ref` 가 없어 argparse 가 2 로 끝나거나 신원 칸이 없어서
  · H-48 은 `KeyError: 'branch'` · H-49·50 은 약한 참조가 **살아 있어서**(`assert True is False`) ·
  I 는 `AttributeError: 'Snapshot' object has no attribute 'branch'` 와
  `TypeError: Source.__init__() got an unexpected keyword argument 'branch'` · J 는
  `i18n: unknown key recent.last_step` 과 요약이 `exit 1` 로 나와서.
- **구현 전에도 초록이어야 하는 것(회귀 잠금)**: A-1 · A-3 · A-4 · A-5 · E-31 · F-34 · G-46 ·
  H-51 · I-58 · J-59 · J-61 · J-62 · J-63. **이 열셋이 처음부터 빨갛다면 픽스처가 틀린 것이지 기능이 없는
  것이 아니다.** 실측으로 확인했다 — 지금 도는 두 파일에서 F-34 · G-46 · H-51 · I-58 이
  초록이고 node 에서 J-59 · J-61 · J-62 · J-63 이 초록이다. `test_render_m5h.py` 는 `MAX_IDENT`
  import 때문에 **아직 통째로 수집되지 않으므로**, 없는 세 이름(`MAX_IDENT` · `failure_lines` ·
  `source_ident`)만 스크래치에서 모듈 객체에 임시로 꽂고 **그 파일 그대로** 돌려 확인했다
  (`src/` 는 안 건드렸다): 35개 항목 중 A-1 · A-3 · A-4 · A-5 · E-31 이 초록이었다.
- **한 글자도 안 바뀌고 초록으로 남아야 하는 기존 테스트**:
  `tests/test_render_text.py::test_recent_row_shows_exit_and_failed_step`(`:84`) ·
  `tests/web/progress.test.js` 의 `recentLine` 아홉 · `tests/test_client.py` 의 스냅샷 시험들 ·
  `tests/test_cli_nowait.py`(`--no-wait` 갈래는 §5 가 안 건드린다).
- 전부 벽시계 sleep 없이 돈다. 기다림도 없다 — H 그룹의 `_wait` 은 대본이라 즉시 돌아온다.
