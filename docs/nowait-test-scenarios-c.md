# `--no-wait` 테스트 시나리오 C — 숫자가 진짜인가, 문서가 그것과 맞는가 (2026-09-09)

`docs/nowait-workplan.md` §8 의 **C** 행이다. 대상 파일은 새 파일 `tests/test_nowait_e2e.py` 하나이고
`src/` · `tools/` · 남의 테스트 · 남의 문서는 한 글자도 안 건드렸다.

이 문서가 잠그는 것은 두 가지다.

1. **진짜 서버로 잰 순번과 ETA.** 큐를 넷 쌓아 각자의 순번이 그 순간의 `GET /api/status` 행과 같은지 ·
   concurrency 그룹에 막힌 잡이 막은 잡을 이름으로 대는지 · **시작할 수 없는 잡**(큐 정지 · 레인 없는 풀)이
   `wait_seconds`·`finish_at` 을 null 로 주고 줄에서 `eta` 를 빼는지(fail-open 금지) · 합류자와 원 요청자가
   같은 순간에 같은 숫자를 말하는지 · 그 숫자가 `rcm eta --job N` · `rcm jobs --json` · `rcm top --json` 과
   같은지.
2. **문서와 스크린샷 기계.** `docs/usage*.md` §5 두 거울 · README 두 언어의 `rcm run` 행 ·
   CHANGELOG `[Unreleased]` 항목 · 그리고 `tools/screenshots/build.py` 의 상자 앵커가 **CLI 가 실제로 찍는
   줄**을 96칸에서 접어도 여전히 찾아내는가.

**남에게 맡긴 것**: JSON 키 계약(키 · 순서 · 형태)과 순번 경계(대기 여럿 · `running` · 종료 · 우선순위로
순번이 바뀔 때)는 **A**(`tests/test_nowait_contract.py`), 표시용 조회의 실패·느림·이상 응답과 다른 플래그와의
조합, 기다리는 경로의 GOLDEN 은 **B**(`tests/test_nowait_resilience.py`). 여기서는 하나도 안 쓴다.
`tests/test_cli_nowait.py` · `tests/test_cli_m3.py` 의 기존 7건도 그대로 두었다 — 겹치지 않게 읽고 썼다.

## 공통 — 도우미와 규칙

**「시작할 수 없는 잡」을 만드는 두 길.** `Server(tmp_path, workers=False)` 는 **레인이 없는 게 아니라 놀고
있는** 상태다 — 상태 문서는 lane 1 을 `idle` 로 보고하고 큐 계산은 정상적으로 ETA 를 준다(실측). 그래서
「워커가 없다」는 `pool = "linux"` 인 프리셋으로 만든다: 그 풀에 레인이 하나도 없으므로 `core/queue.py:320`
의 `not live_lanes` 가지가 잡히고 `reason = worker_down` · `wait_seconds`·`finish_at` 이 null 이 된다.
「정지」는 admin 토큰으로 `rcm pause` 를 치는 쪽이다(`reason = paused`, 같은 null 둘).

**그룹 차단은 레인이 둘이어야 뜻이 있다.** 레인 하나면 두 번째 잡의 이유는 `waiting_for_lane` 이라 그룹을
증명하지 못한다. 그리고 부하 게이트가 켜져 있으면 `app.start()` 직후에는 표본이 없어 레인 2 가 설계대로
보류된다 — `tests/test_e2e_m3.py:86` 의 주석과 같은 이유로 `admission="always"` 를 준다. 픽스처는
`lanes=2 · admission="always"` 에 `concurrency_group="devices"` 프리셋 하나를 얹고, 막는 잡을 원시 API 로
먼저 올려 `running` 까지 기다린 뒤 CLI 를 부른다(막는 잡의 `tree_hash` 는 시험 트리와 달라 합류하지 않는다).

**흔들리는 값과 안 흔들리는 값.** `estimate.wait_seconds` · `estimate.finish_at` ·
`blocked_by.remaining_seconds` 는 **초마다 다르다** — 같은 잡을 두 번 조회하면 값이 다르게 나온다. 그래서
비교는 `numbers(doc)` 튜플로 한다: `position` · `reason` · `ahead_job_id` · `blocked_by` 의 `(job_id, group)` ·
`expected_seconds` · `wait_seconds is None` · `finish_at is None`. 시각은 따로 `ETA_SLACK = 30초` 안인지만
본다. **이건 시간 단언이 아니다** — 진짜 불일치(레인이나 그룹을 다르게 잡는 것)는 600초 단위로 벌어지므로
30초는 그 사이를 가르는 선이지 속도를 재는 선이 아니다.

**`screenshot_helpers()` — Pillow 없이 `build.py` 의 진짜 코드를 돌린다.** `tools/screenshots/build.py` 를
import 하면 `annotate`·`term` 을 타고 Pillow 가 딸려 온다. Pillow 는 `docs` extra 이고 CI 는 `.[dev]` 만
깐다(`.github/workflows/ci.yml`) — import 하면 CI 에서 죽는다. 그래서 `ast` 로 `lines` 와 `at` **두 함수만**
꺼내 원본 그대로 exec 한다(`RAW` 는 모듈 전역이라 이름 공간에 끼워 넣는다). 함수 이름이나 서명이 바뀌면
그 자리에서 빨개진다.

**캡처는 조립한다, 다시 찍지 않는다.** `tools/screenshots/capture.py` 는 서버와 Chrome 을 띄우고 몇 분이
걸린다(지시에서 금지). 대신 `capture.py::_block` 과 **같은 방식**(`$ <명령>\n` + stderr + stdout, 필요하면
`$ echo $?`)으로 **진짜 CLI 출력**을 조립하고, 포트·호스트만 `_tidy` 처럼 `macmini:8787` 로 바꾼다. 프롬프트
줄(`$ rcm run demo --no-wait …`)은 capture.py 가 쓰는 문구를 그대로 베꼈다 — 앵커가 흔들릴 수 있는 곳은
capture.py 가 아니라 **CLI 가 찍는 줄**이기 때문이다.

**실측한 접힘의 성질**(`textwrap.wrap(width=96, break_long_words=True, break_on_hyphens=False)`):

| 무엇 | 접힌 뒤 |
|---|---|
| 공백이 있는 긴 줄(제출/합류 줄) | 첫 조각이 **줄머리를 그대로** 갖는다 → `at()` 의 `startswith` 가 계속 맞는다 |
| 공백이 없는 JSON 한 줄 | 정확히 96칸마다 잘린다. 어떤 조각도 `submitted job`·`joined job`·`fetch its artifacts` 로 시작하지 않는다(JSON 안의 `joined` 는 `"joined":` 라 따옴표로 시작한다) |
| 96칸 이하인 줄 | `lines()` 가 `textwrap` 을 아예 안 부른다 → `$ rcm run … demo   # 다른 세션…` 의 공백 세 칸이 살아남는다 |

**실측한 줄 길이**(`macmini:8787` 로 치환한 뒤): 가장 짧은 제출 줄 95칸(안 접힌다) · 합류 줄 118칸 ·
그룹에 막힌 제출 줄 114칸. 즉 **평범한 제출 줄은 경계에 걸쳐 있고**(실기의 `#155` 처럼 잡 번호가 세 자리면
넘는다) 합류·그룹 줄은 반드시 접힌다. 그래서 「접히는 경우」는 시나리오 13·15 가 지나간다.

**시간 단언 없음.** 기다림은 전부 `Server.wait_state` · `Server.wait_terminal` 의 마감 있는 폴링이다.
`elapsed < N` 은 한 줄도 없다.

## 1부 — 진짜 서버로 잰 숫자 (`tests/test_nowait_e2e.py`)

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 1 | **큐 넷을 쌓는다.** `workers=False` 에 `ok` · `bad` · `slow` · `gate -f scope=fast` 를 차례로 `--no-wait` 으로 낸다(프리셋·입력이 달라 합류하지 않는다). 결과: 낼 때마다 그 잡의 `position` 이 **그 순간의 `/api/status` 행**과 같고 · `numbers()` 전체가 같고 · `finish_at` 이 30초 안이고 · 순번이 `[1,2,3,4]` 이고 · **마지막 잡의 순번 == 대기 중인 행의 수** · `ahead_job_id` 가 `[None, #1, #2, #3]` 으로 앞 잡을 가리킨다 | `test_four_stacked_jobs_each_report_the_position_of_their_own_queue_row` | 「몇 번째냐」가 이 변경의 전부다. 순번 하나만 보면 우연히 맞을 수 있어서 **네 번 연속으로** 큐 행과 대조하고, 「내가 꼴찌」와 「내 앞의 잡 번호」까지 같은 문서에서 확인한다 |
| 2 | **그룹이 막는다.** 레인 2 · `admission="always"` · `concurrency_group="devices"` 프리셋. 막는 잡을 먼저 `running` 으로 두고 CLI 로 하나 더 낸다. 결과: `reason == "blocked_by_group"` · `blocked_by.job_id` 가 **막고 있는 그 잡** · `blocked_by.group == "devices"` · `remaining_seconds > 0` · `ahead_job_id is None`(레인은 비어 있다 — 막는 건 레인이 아니라 그룹이다) · 큐 행과 `numbers()` 일치 · 줄에 `blocked by group` 과 `eta` 가 둘 다 있다 · 단언이 끝날 때까지 막는 잡이 **여전히 `running`** | `test_a_group_blocked_job_names_its_blocker_and_says_blocked_by_group` | 「누가 앞에 있냐」에 명세(§6)가 준 답이 `blocked_by.job_id` 다. 그 값이 진짜 그 잡인지는 **서버를 세워 봐야** 안다. `ahead_job_id is None` 을 같이 단언하는 이유: 둘을 섞어 채우면 「레인이 없어서 기다린다」와 「그룹 때문에 못 올라간다」가 화면에서 구분되지 않는다 |
| 3 | **큐를 세운다.** admin 토큰으로 `rcm pause` → `--no-wait`. 결과: exit 0 · `reason == "paused"` · `estimate.wait_seconds is None` · `estimate.finish_at is None` · `expected_seconds > 0`(기대치는 여전히 안다) · 줄에 `paused` 와 `1st in line` 은 있고 **`eta ` 도 `wait ` 도 없다** · 큐 행과 일치. 이어서 `rcm resume` 하면 같은 잡에 `finish_at` 이 생기고 이유가 `waiting_for_lane` 이 된다 | `test_a_paused_queue_gives_null_wait_and_finish_and_the_line_carries_no_eta` | AGENTS.md 의 「Never invent a number」. 「모르는 것을 아는 척하지 않는다」는 **없는 것을 안 찍는다**로만 증명된다. 풀고 나서 값이 생기는 것까지 봐야 그 null 이 「모른다」가 아니라 **「멈춰 있다」**였음이 드러난다 |
| 4 | **레인이 없는 풀.** `pool = "linux"` 프리셋 하나(그 풀에 레인 0). 기본 풀에 잡 둘을 먼저 세워 둔다. 결과: exit 0 · `reason == "worker_down"` · `wait_seconds`·`finish_at` 이 null · 줄에 `worker down` 과 `1st in line`, `eta `·`wait ` 없음 · 큐 행과 일치 · **`position == 1`** 인데 전체 대기 행은 3개다 | `test_a_pool_with_no_live_worker_gives_null_wait_and_finish_and_no_eta` | 정지와 워커 없음은 다른 가지다(`queue.py:318` vs `:320`) — 하나만 재면 나머지가 조용히 fail-open 할 수 있다. 그리고 순번이 **자기 풀 안의** 순번이라는 것을 같이 못 박는다: 「3번째」라고 말했다면 그건 지어낸 숫자다 |
| 5 | **합류자와 원 요청자.** alice 가 내고 → 뒤에 하나 더 세우고 → alice 가 같은 트리를 다시 내고(합류) → bob 이 같은 트리를 낸다(합류). 결과: 둘 다 `joined is True` 이고 같은 잡 번호 · `numbers()` 가 **서로 같고** 큐 행과도 같다 · `finish_at` 이 30초 안 · **`url` 이 셋 다 같다**(합류자도 볼 링크를 받는다) · 두 stderr 줄이 `joined job #N queued` 로 시작하고 `1st in line` 과 `eta` 를 말한다 | `test_the_joiner_and_the_requester_report_the_same_numbers_for_the_same_job` | 완료 기준 2 는 「합류한 세션은 **그 잡의** 순번을 본다」다. 기존 시험은 합류자 하나만 본다 — 두 세션이 같은 순간에 **같은 말**을 하는지는 안 본다. 화면 둘이 어긋나면 사용자는 어느 쪽을 믿을지 모른다. `url` 을 같이 잠그는 이유: 합류 응답에서 URL 이 빠지면 링크 없는 줄이 나오는데 아무 시험도 그것을 안 보고 있었다 |
| 6 | **다른 화면과 같은 숫자.** 잡 둘을 세우고 두 번째 것에 대해 `rcm eta --job N --json` · `rcm jobs --json` · `rcm top --json` 을 부른다. 결과: 셋 다 `numbers()` 가 `--no-wait` 의 몸통과 같고 `finish_at` 이 30초 안 · 사람이 읽는 `rcm eta --job N` 한 줄이 `#N · 2nd in line` 으로 시작하고 stderr 줄도 `2nd in line` 이라고 말한다 | `test_the_numbers_agree_with_rcm_eta_rcm_jobs_and_rcm_top_for_the_same_job` | `--no-wait` 은 `GET /jobs/{id}` 를, 나머지 셋은 `/api/status` 를 읽는다. **문서가 둘인데 답은 하나여야 한다.** 이 시험이 없으면 두 경로가 갈라져도 각자의 시험은 초록이다 |

## 2부 — 문서와 스크린샷 기계

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 7 | **§5 가 지금 찍히는 것을 말한다** — 두 언어 각각, 사실 일곱 개를 정규식으로: `` `position` `` · `` `estimate` `` · `finish\|끝날` · `already running\|이미 도는 잡` · `guess\|지어내` · `exit code is still 0\|종료 코드는 그대로 0` · `not because it was looked up\|조회됐다` | `test_usage_section_5_describes_what_no_wait_now_prints` | 문서 문면 잠금(`test_docs_m5*.py` 의 방식). 기능이 있는데 문서가 안 말하면 빨개진다 — 그리고 **두 거울이 같은 사실을 말하는지**를 같은 정규식으로 확인한다 |
| 8 | **§5 의 번호가 두 언어에서 같다.** `1.`~`4.` 넷이고 둘 다 `images/ui/cli-nowait.png` 를 가리킨다 | `test_the_two_usage_mirrors_number_the_same_steps_in_section_5` | `build.py` 가 그리는 ①~④ 는 두 언어에서 같은 뜻이어야 한다(`docs/documentation.md` 「Screenshots」). 이번 변경이 ① 의 범위를 넓혔으므로 짝이 여전히 넷인지 다시 잠근다 |
| 9 | **§7 의 번호도 그렇다.** `1.`~`2.` 둘이고 둘 다 `images/ui/cli-join.png` 를 가리킨다 | `test_the_two_usage_mirrors_number_the_same_two_steps_in_section_7` | `cli-join.png` 의 상자 ② 범위가 이번에 바뀌었다(`build.py:327`). 그림의 상자와 글의 번호가 갈라지는 것을 막는다 |
| 10 | ❌ **§5 그림의 대체 텍스트가 그림이 보여 주는 것을 말한다.** 두 언어의 `![…](images/ui/cli-nowait.png)` 대체 텍스트에 `position\|in line\|ETA\|순번\|몇 번째` 중 하나가 있어야 한다 | `test_the_section_5_image_alt_text_says_what_the_picture_now_shows` | **오늘 빨갛다.** 아래 「고쳐야 할 것」 1 |
| 11 | **README 두 언어의 `rcm run` 행**에 `--no-wait` 이 있고 `position\|순번` 과 `ETA` 를 말한다 | `test_readme_run_row_says_no_wait_returns_the_position_and_the_eta` | 앞문의 표가 「이 플래그는 무엇을 주나」를 답한다. 두 언어가 같이 바뀌었는지를 본다 |
| 12 | **CHANGELOG `[Unreleased]` 절**(다음 릴리스 절 앞까지)에 `--no-wait` · `` `position` `` · `state` · `exits 0` · `pull/72` 가 있다 | `test_changelog_unreleased_entry_covers_the_new_no_wait_output` | `docs/documentation.md` 「What a pull request updates」 1 번. 기존 `unreleased()` 도우미는 0.1.0 아래 전부를 훑어 옛 항목에도 걸린다 — 여기서는 **`[Unreleased]` 절만** 잘라 본다 |
| 13 | **`cli-nowait` 앵커 넷.** `workers=True` 서버의 레인 하나를 `slow` 잡으로 채워 두고(그래야 제출 줄에 순번·ETA 가 다 붙는다) 진짜 `rcm run … --no-wait` · `rcm jobs` 를 돌리고, 레인을 비운 뒤 진짜 `rcm wait --job N` 까지 돌려 캡처를 조립한다. 96칸으로 접은 뒤: `at(ls,"submitted job")` · `at(ls,"$ rcm jobs")` · `at(ls,"$ rcm wait")` · `at(ls,"$ echo")` 가 **이 순서로** 나오고 · `build.py:299-304` 의 상자 넷이 전부 `0 <= 시작 <= 끝 < len(ls)` 이고 · ① 의 마지막 줄이 **`}` 로 끝난다**(접힌 JSON 이 상자 밖으로 안 샌다) · `fetch its artifacts` 가 ① 안에 있고 · `submitted job` 으로 시작하는 줄이 **정확히 하나** | `test_the_cli_nowait_anchors_still_find_the_submission_block` | 앵커가 안 맞으면 `at()` 이 `KeyError` 를 던져 `build.py` 가 죽는다 — 그때는 이미 몇 분짜리 `capture.py` 를 돌린 뒤다. 「정확히 하나」를 세는 이유: 옛 식별 줄이 되살아나 두 줄이 되는 회귀는 `KeyError` 를 안 내고 **상자만 조용히 틀어진다** |
| 14 | **`cli-join` 앵커 둘.** 두 세션이 같은 트리를 `--no-wait` 으로 내 두 번째가 합류하게 하고 같은 방식으로 조립한다. 결과: 합류 줄이 **96칸을 넘고**(접힌다) · `at(ls,"$ rcm run demo --no-wait -f speed=fast   #")` 와 `at(ls,"joined job")` 이 이 순서로 잡히고 · 상자 둘이 범위 안이고 · ① 의 마지막 줄이 `}` 로 끝나고 · ② 의 끝이 `i_joined` **보다 크다**(접힌 조각을 전부 덮는다) · `joined job` 으로 시작하는 줄이 정확히 하나 · **`after=` 없이 `fetch its artifacts` 를 찾으면 첫 블록의 것이 잡힌다**(그 인자가 왜 필요한지) | `test_the_cli_join_anchors_still_find_the_joined_block` | `x-join` 은 이번에 `wrap=96` 이 처음 붙은 캡처다(`build.py:320`). 합류 줄은 순번·ETA·`same preset, inputs and tree`·URL 이 다 붙어 **반드시** 접힌다 — 접힌 조각을 ② 가 안 덮으면 그림의 상자가 줄 중간에서 잘린다 |
| 15 | **가장 긴 제출 줄.** 그룹에 막힌 잡의 제출 줄(114칸)로 같은 검사. 결과: 접힌 뒤 `at(ls,"submitted job")` 이 가리키는 줄이 원래 줄의 **접두사이고 전부는 아니며** · 그 앵커로 시작하는 줄이 하나뿐이고 · `fetch its artifacts` 가 그 뒤에 있다 | `test_a_long_submitted_line_keeps_the_anchor_on_its_first_fragment` | 13 의 제출 줄은 95칸이라 **안 접힌다** — 접힘을 지나가는 경로가 필요하다. 실기(`#155`)처럼 잡 번호가 세 자리이거나 이유가 붙으면 넘는다 |

**함수 15개 · 파라미터까지 34건.** 1부 6 · 2부 문서 6(파라미터 24) · 2부 앵커 3.
`ruff check` · `ruff format --check` 초록, `pytest tests/test_nowait_e2e.py` → **32 통과 · 2 실패**(시나리오 10,
두 언어. 아래 1번 문제).

## 고쳐야 할 것 (내가 못 고치는 것 — `src/`·문서는 조율자의 몫이다)

1. ❌ **§5 그림의 대체 텍스트가 옛날 그림을 설명한다** (시나리오 10, 두 언어 모두 빨강).
   `docs/documentation.md` 「README」는 *"Every image gets alt text that says what it shows."* 라고 못 박는데,
   `cli-nowait.png` 의 ① 상자는 이제 순번과 ETA 가 붙은 제출 줄인데도 대체 텍스트는 그대로다:

   - `docs/usage.md:131` — `![rcm run --no-wait prints the job id, rcm jobs lists the queue by pool, rcm wait follows the job to its end](images/ui/cli-nowait.png)`
   - `docs/usage.ko.md:128` — `![rcm run --no-wait 가 잡 번호를 찍고, rcm jobs 가 풀별로 큐를 보여 주고, rcm wait 이 끝까지 따라간다](images/ui/cli-nowait.png)`

   그림을 못 보는 사람에게는 이 변경이 통째로 없는 셈이다. 고침은 두 줄, 각 한 줄이다("prints the job id"
   → "prints the job id, its place in line and its ETA" 정도 / "잡 번호를 찍고" → "잡 번호와 순번 · ETA 를
   찍고"). `cli-join.png` 의 대체 텍스트는 `"joined job #10"` 을 인용하는데 그건 지금 줄의 접두사 그대로라
   여전히 맞다 — 안 건드려도 된다.

2. **§5 가 「시작할 수 없는 큐」를 안 말한다** (빨간 테스트로 안 남겼다 — 문장을 새로 쓰는 일이라 조율자의
   몫이다). §5 는 순번이 빠지는 경우를 둘만 든다: 「이미 도는 잡」과 「큐를 못 읽을 때」. 그런데 **큐가
   정지돼 있거나 그 풀에 워커가 없으면** 줄은 순번은 말하되 `eta` 를 통째로 뺀다(시나리오 3·4). 사용자가
   실제로 보게 되는 세 번째 모양인데 두 언어 어디에도 없다. 「Never invent a number」(AGENTS.md)가 이
   기능에서 가장 눈에 띄는 자리인 만큼 한 문장 값어치는 있다.

3. **§5 가 `ahead` 를 약속한다 — 명세는 그걸 안 넣기로 했다.**
   `docs/usage.md:133` *"The line names the job, its state, **how many are ahead of it** and when it should
   finish"* / `docs/usage.ko.md:130` 「줄 하나가 잡 번호와 상태, **앞에 몇 개가 있는지**, 언제 끝날지를
   말한다」. 그런데 명세 §6 은 **`ahead` 를 안 넣는다**고 못 박았고(`/api/status` 왕복을 하나 더 달 값어치가
   없다), 줄이 실제로 찍는 것은 `3rd in line` — **순번**이다. 「3번째」에서 「앞에 둘」을 읽어 낼 수는 있지만
   그건 대기 잡만의 이야기이고, 같은 풀에서 **도는** 잡은 세지 않는다(`rcm eta` 의 `N ahead` 는 그것까지
   센다 — `cli.py:690-693`). 두 화면이 다른 수를 말하는데 문서는 같은 말로 부른다. 「몇 번째인지」로 바꾸는
   편이 정확하다.

4. **§7 은 `cli-join.png` 의 주인인데 그림만 바뀌고 글은 그대로다.** 합류한 세션이 이제 그 잡의 순번과 ETA
   를 본다는 사실은 §5 와 CHANGELOG 에 있으니 **문서가 없는 것은 아니다**. 다만 그 사실을 보여 주는 그림
   옆에는 없다. 시나리오 9 가 번호 짝(①②)만 잠갔다 — 문면은 안 잠갔다.

5. **`PLAN.md:418` 이 낡았다.** 「`--no-wait` 면 ③/④ 뒤 **JSON 만 찍고** 0 으로 끝난다」 — 이제 stderr 에
   사람용 한 줄도 같이 찍는다. PLAN.md 는 사용자 문서가 아니라 `docs/documentation.md` 의 체크리스트
   대상이 아니다(그래서 테스트로 안 잠갔다). 정확성 차원의 지적이다.

## 명세(`docs/nowait-workplan.md`)에 대한 의견

1. **§4 의 세 번째 예시가 §4 의 규칙과도 구현과도 다르다.** 줄 57 은

   ```
   submitted job #12 queued (deploy · main @a1b2c3d) · 1st in line · wait 0s · eta 16:45 · http://…
   ```

   이라고 적어 detail 을 **상태 뒤 괄호 안**에 둔다. 그런데 바로 아래 줄 60 의 모양 규칙은
   `<submitted|joined> job #<id> <state> · <describe 조각> · [detail ·] <url>` 이고, 구현
   (`cli.py:489-492`)은 그 규칙대로 `… queued · 1st in line · wait 0s · eta 16:45 · deploy · main @a1b2c3d ·
   http://…` 를 찍는다. 규칙과 구현이 맞으니 **예시가 틀렸다**. 줄 56(합류)과 줄 55 는 규칙대로다.
   줄 모양은 A 의 몫이라 테스트로 안 남겼다 — 명세 문장만 고치면 된다.

2. **§8 의 C 행에 있는 「합류 뒤 원 요청자가 취소했을 때」는 이 명령이 닿지 않는 곳이다.** 조율자의 지시에는
   없었고, 나도 시나리오를 못 세웠다: `--no-wait` 은 그 시점에 **이미 반환한 뒤**라 찍을 줄도 고칠 값도
   없다. 취소가 합류자를 떼어 내는 규칙은 `tests/test_server.py::test_cancel_permissions_and_joiner_leaves`
   가 이미 잠그고 있다. 명세에서 지우거나, 「합류자가 취소하면 잡은 안 죽는다」로 다시 쓰는 편이 낫다.

3. **§6 과 문서가 다른 약속을 한다** — 위 「고쳐야 할 것」 3. 명세가 `ahead` 를 버렸으면 사용법 가이드도
   그 말을 쓰면 안 된다. 둘 중 하나는 고쳐야 하는데, 왕복 하나를 아끼는 §6 의 판단이 옳아 보이므로 문서
   쪽을 고치는 것을 권한다.

4. **§10 「알려진 한계」에 「풀」이 없다.** `position` 은 **자기 풀 안의** 순번인데(시나리오 4 로 확인) 줄은
   풀 이름을 말하지 않는다 — 두 풀에서 동시에 `1st in line` 이 나온다. `rcm eta` 도 같은 성질이라
   **회귀는 아니지만**, 「제출 시점의 순번을 약속으로 읽으면 안 된다」 옆에 한 줄 값어치는 있다.

## 못 잠근 것

1. **진짜 캡처 파일로는 못 쟀다.** `raw/x-nowait.txt`·`x-join.txt` 는 `capture.py` 가 서버와 Chrome 을
   띄워야 생기고 몇 분이 걸린다(지시에서 금지). 조립한 캡처는 CLI 출력만 진짜다 — 프롬프트 줄과 포트·호스트
   치환은 `capture.py` 의 문구를 베꼈다. **`capture.py` 쪽이 바뀌면**(예: `_block` 이 stdout 을 먼저 붙이게
   되면) 이 시험은 그것을 못 본다.
2. **상자를 실제로 그리는 코드(`term()`·`annotate.py`)는 안 돌린다.** Pillow 가 `docs` extra 라 CI 에 없다.
   잠근 것은 **줄 번호 계산**까지다 — 상자의 픽셀 위치나 이미지 크기 상한(400 KB)은 못 본다.
3. **`position` 이 벽시계에서 얼마나 오래 참인지**(§10 의 경합)는 못 잠근다. 시간 단언 금지이기도 하고,
   「그 순간의 사실」이라는 성질 자체가 마감으로 잴 수 있는 것이 아니다. 시나리오 1 이 잠그는 것은
   **같은 순간의 두 문서가 같은 말을 한다**까지다.
4. **`git_ref` 모드**는 서버에 `[[repos]]` 와 bare 레포가 필요하고 git 이 없으면 skip 이다. `tests/test_cli_m3.py`
   가 이미 순번·ETA 를 한 건 잠그고 있어(`test_run_ref_no_wait_reports_the_queue_position_and_the_eta`)
   여기서는 되풀이하지 않았다. 다만 그쪽 `--no-wait` 은 tree 모드와 달리 `fetch its artifacts later …`
   안내 줄을 **안 찍는다**(`cli.py:485-498` vs `:436-437`). 이번 변경 이전부터 그랬고 명세 밖이라 시험을
   안 썼다 — 의도된 것인지 확인은 필요해 보인다.
5. **`build.py` 의 `cli-nowait` 상자 ②** (`i_jobs + 1, i_jobs + 2`)는 `rcm jobs` 의 행이 96칸을 넘어 접히면
   두 번째 행의 첫 조각까지만 덮는다(조립한 캡처에서 실제로 그랬다 — 라벨이 길었다). 이번 변경 이전부터의
   성질이고 실기 캡처의 라벨은 `alice@laptop` 으로 짧아 안 접힌다. 상자 ② 는 이 PR 이 건드리지 않았으므로
   회귀로 잠그지 않았다.
6. **우선순위로 순번이 바뀌는 경계**(§10 의 마지막 항목)는 A 의 몫이라 안 썼다.

## 구현에 대한 판단

**1부에서 잘못된 숫자는 하나도 못 찾았다.** 순번은 큐 행과 같았고, 그룹에 막힌 잡은 막은 잡을 정확히
가리켰고, 정지·워커 없음에서는 두 경로 모두 null 을 그대로 실어 줄에서 `eta` 를 뺐고, 합류자와 원 요청자는
같은 말을 했고, `rcm eta`·`rcm jobs`·`rcm top` 과도 어긋나지 않았다. 지어낸다고 의심할 자리는 못 찾았다.
2부의 앵커도 접힌 줄 위에서 그대로 맞았다 — `build.py` 의 이번 수정(`i_jobs - 1` · `after=` · `x-join` 의
`wrap=96`)은 필요했고 충분했다. 남은 것은 위의 문서 문제 다섯 개와 명세 문장 넷이다.
