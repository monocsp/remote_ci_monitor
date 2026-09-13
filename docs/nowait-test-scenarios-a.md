# `--no-wait` 테스트 시나리오 A — 출력 계약 (2026-09-09)

`docs/nowait-workplan.md` §3(stdout JSON 의 키·순서·값) · §4(stderr 한 줄의 모양) · §9 완료 기준
1~3(순번이 큐의 실제 순번과 같다 · 한 줄이 순번·대기·ETA·URL 을 말한다 · `running`·종료 잡은 순번
조각이 통째로 빠진다)을 테스트로 옮긴 것이다. 파일은 `tests/test_nowait_contract.py` 하나이고
`src/` 는 건드리지 않았다.

**이 문서가 안 다루는 것**(다른 담당):

| 무엇 | 담당 |
|---|---|
| 표시용 조회의 실패·느림·이상 응답(404 · 500 · 빈 몸통 · 리스트 · 타임아웃) · 왕복 횟수 · 상한 전달 · 기다리는 경로의 GOLDEN · 플래그 조합(`--no-join` · `--pool` · `--no-cache` · `--fetch-artifacts` 거절) | B (`tests/test_nowait_resilience.py`) |
| 진짜 워커로 큐를 쌓는 e2e · concurrency 그룹의 `blocked_by` · 워커 다운/정지의 `finish_at` null · 합류 뒤 원 요청자 취소 · `docs/usage*.md` 와 스크린샷 앵커 대조 | C (`tests/test_nowait_e2e.py`) |

`--priority` 는 두 문서에 걸친다. **플래그가 서버에 전달되는가**는 B 의 몫이고, 여기서는
**순번이 우선순위를 따르는가**(시나리오 13·18)만 잠근다.

## 공통 — 도우미와 규칙

**`Server(tmp_path, workers=False)`.** 낸 잡이 전부 `queued` 로 서 있어 순번이 시간에 안 흔들린다.
레인은 1개이고 표본이 없으므로 대기 잡의 `expected_seconds` 는 전부 기본 **600초**다 — 그래서
1번째의 `wait 0s`, 2번째의 `wait 10m 00s`, 3번째의 `wait 20m 00s` 가 **결정적**이다(시각이 아니라
서버가 같은 `now` 로 계산한 값이라 흔들리지 않는다). `git_server` 의 `deploy` 는
`expected_seconds = 5` 라 값이 다르지만, git 쪽 시나리오는 1번째만 보므로 `wait 0s` 로 같다.

**`id_line(err)` / `id_lines(err)`.** 「잡을 이름 붙이는 줄」만 고른다 —
정규식 `^(?:submitted|joined) job #\d+`. 이 그물을 쓰는 이유가 두 가지다:

1. stderr 에는 **업로드 진행 줄**(`uploading #7: 0.0 / 0.0 MB (100%)`)도 있다. 비-TTY 에서
   `_StatusLine` 은 첫 갱신을 곧바로 찍으므로(`cli.py:91-98`, `last_write` 가 0.0 에서 시작한다)
   작은 트리에서도 반드시 나온다. `#<id>` 로 세면 이 줄이 걸린다 — 진행 표시는 식별 줄이 아니다.
2. `--no-wait` 은 그 뒤에 `fetch its artifacts later with \`rcm artifacts 7 …\`` 한 줄을 더 찍는다.
   여기엔 `#` 이 없다.

`id_line` 은 **줄이 정확히 하나임을 단언한 뒤** 그 줄을 돌려준다. 즉 이 도우미를 쓰는 모든
시나리오가 명세 §4 의 「옛 식별 줄은 안 찍는다 — 한 줄이 그 자리를 대신한다」를 같이 잠근다.

**`eta_text(finish_at)`** — `fmt_clock(finish_at, datetime.now().astimezone().tzinfo)`, 즉 CLI 와
**같은 함수·같은 tz**다. 시각을 문자열로 새로 쓰지 않는다(테스트가 자기 포맷을 만들면 그게 또
하나의 명세가 된다). 한계: `fmt_clock` 자체가 틀리면 양쪽이 같이 틀리므로 이 파일은 못 잡는다 —
그건 `tests/test_render_text.py` 의 몫이다.

**끝난 잡을 만드는 법**(시나리오 19). `Client.job` 을 감싸 **조회 직전에** `POST /jobs/{id}/cancel`
을 한 번 치고, 그 다음 **진짜** `Client.job` 을 부른다. 조회 자체는 손대지 않는다 — 재현하는 것은
「업로드와 조회 사이에 잡이 끝났다」는 명세 §10 의 경합이지 조회의 고장이 아니다(그건 B).
`Client.upload` 를 감싸는 방법은 **안 된다**: 서버의 `snapshot_cache` 가 켜져 있으면 CLI 는
`upload_cached()`(manifest → blob PUT) 로 가고 `Client.upload` 는 한 번도 안 불린다. 처음에 그렇게
썼다가 취소가 일어나지 않아 `state == "queued"` 로 빨갛게 실패했다.

**순번은 큐 문서와 대조한다.** 「JSON 이 2 라고 말한다」만으로는 부족하다 — 같은 순간의
`GET /api/status` 의 `pools[0].queue` 에서 그 잡의 `position` 을 읽어 **같은 값**임을 본다
(시나리오 13·16·17·18). 완료 기준 1 의 「큐의 실제 순번과 같다」가 이 대조다.

**시간 단언 금지.** `elapsed < N ms` 같은 단언은 하나도 없다. 유일한 기다림은 시나리오 17 의
`live.wait_state(id, "running")`(마감 있는 폴링)이고, 마감을 넘기면 「느리다」가 아니라 「안 돈다」는 뜻이다.

**git.** git 이 PATH 에 없으면 `needs_git` 로 skip 한다(`bare` 픽스처가 진짜 `git init` 을 한다).

## 시나리오

### `describe(head=)` — 순수 단위 (§4 「포매터를 새로 만들지 않는다」)

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 1 | **`head` 는 첫 조각만 바꾼다**. queued(순번 2 · `blocked_by_group` · wait · eta) · running(step 5/8 · elapsed · eta) · terminal(summary) 세 문서로, `describe(job)` 와 `describe(job, head="submitted job #9 <state>")` 를 ` · ` 로 쪼개 비교. 결과: 첫 조각만 다르고 **`[1:]` 이 완전히 같다** | `test_a_head_replaces_only_the_first_fragment_whatever_the_document_is` (3건) | 명세는 「순번·스텝·경과·ETA·이유의 규칙은 기다리는 경로와 **같은 코드**」라고 했다. 「같은 코드를 부른다」가 아니라 **「나오는 글자가 같다」**로 재야 두 화면이 어긋나지 않는다 |
| 2 | **순번이 없으면 조각이 통째로 빠진다**. `position` 이 `None` 과 `0`, 그런데 `reason` 은 `blocked_by_group`. 결과: 줄이 정확히 `joined job #7 running · elapsed 3s` — `in line` 도 `0th` 도, **이유도** 없다 | `test_a_job_without_a_position_says_nothing_about_a_line_or_a_reason` (2건) | 완료 기준 3. `position: 0` 을 같이 재는 이유: 구현이 `if job.get("position")` 이라 0 도 빠지는데, 이건 우연이 아니라 **약속**이어야 한다. 그리고 이유는 순번의 **부속**이다 — 순번 없이 이유만 나오면 「몇 번째인지는 모르는데 막혀는 있다」가 된다 |
| 3 | **순번은 영어 서수로 쓴다**. 1·2·3·4·11·12·13·21·22·23·101·111·112 → `1st`·`2nd`·`3rd`·`4th`·`11th`·`12th`·`13th`·`21st`·`22nd`·`23rd`·`101st`·`111th`·`112th` | `test_the_position_is_written_as_an_english_ordinal` (13건) | `_ordinal` 은 저장소 어디에서도 직접 시험된 적이 없었다(`grep -rn "_ordinal" tests/` → 0건). 11~13 과 111~113 은 서수 규칙이 늘 틀리는 자리다 |
| 4 | **말할 값이 있는 이유만 붙는다**. `waiting_for_lane` · `uploading` · `None` → 아무것도 안 붙는다. `blocked_by_group` → `blocked by group`, `worker_down` → `worker down`(밑줄이 공백으로) | `test_only_a_reason_worth_reading_is_added_after_the_position` (5건) | 「줄 서 있다」는 순번이 이미 말했다 — 그걸 또 쓰면 짧은 줄이 길어지기만 한다. 반대로 막힌 이유는 순번만으로는 안 보인다 |
| 5 | **빈 문서면 head 만 남는다**. `describe({}, head=…)` · `describe({"state": "queued"}, head=…)` → head 그대로. `describe({})`(head 없이) → `#None ?` | `test_a_document_with_nothing_in_it_leaves_just_the_head` | 조회한 문서가 낯설어도 줄은 만들어져야 한다(제출은 이미 끝났다). 그리고 없는 값을 채워 넣지 않는다 |

### `_submitted_line` — 명세 §4 의 예시 셋

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 6 | **§4 첫째 예시**. queued · 순번 1 · `wait_seconds 0` · `finish_at` → `submitted job #8 queued · 1st in line · wait 0s · eta <HH:MM> · <url>` **통째로 같다** | `test_the_line_of_a_queued_job_is_the_documented_shape` | 조각이 다 있는지가 아니라 **순서까지** 잠근다. URL 은 언제나 마지막이다 |
| 7 | **§4 둘째 예시**. running · `position None` · progress(step 1/4 fetch deps) · elapsed 0 · joined · detail `same preset, inputs and tree` → `joined job #10 running · step 1/4 fetch deps · elapsed 0s · eta <HH:MM> · same preset, inputs and tree · <url>` | `test_a_joined_line_puts_the_join_reason_between_the_estimate_and_the_url` | detail 의 자리(순번 조각 **뒤**, URL **앞**)가 §4 의 모양 규칙이다 |
| 8 | **새 git_ref 잡의 detail**. detail `deploy · main @a1b2c3d` → `submitted job #12 queued · 1st in line · wait 0s · eta <HH:MM> · deploy · main @a1b2c3d · <url>` | `test_a_new_git_ref_line_carries_the_preset_the_ref_and_the_sha_after_the_estimate` | ⚠️ **명세 §4 의 셋째 예시와 어긋난다** — 예시는 `queued (deploy · main @a1b2c3d) · 1st in line …` 처럼 detail 을 상태 뒤 괄호에 두었다. 같은 절의 **모양 규칙** 쪽을 잠갔다(아래 「명세에 대한 의견」 1) |

### stdout JSON 계약 (§3)

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 9 | **tree 모드의 키와 순서**. `rcm run ok --no-wait --dir <tree>` → stdout 이 **한 줄**이고 `list(body) == [job_id, joined, state, position, reason, ahead_job_id, blocked_by, estimate, url]` · `state == "queued"` · `position 1` · `ahead_job_id`·`blocked_by` 는 null | `test_the_no_wait_json_has_the_documented_keys_in_the_documented_order` | §3 은 키 순서를 못 박았다. 순서는 `dict` 삽입 순서로만 지켜지므로(`_no_wait_json` 이 `update` 3번) 리팩터 한 번에 조용히 뒤집힌다. `"submitted"` 라는 가짜 상태가 사라진 것도 여기서 본다 |
| 10 | **진짜 서버로 §4 첫째 예시**. 같은 제출의 stderr 한 줄이 `submitted job #<id> queued · 1st in line · wait 0s · eta <JSON 의 finish_at> · <JSON 의 url>` **통째로** 같다 | `test_the_submitted_line_of_a_first_in_line_job_is_exactly_the_documented_shape` | 단위(6)가 통과해도 배선이 틀리면 소용없다. 그리고 **줄의 eta 와 JSON 의 `estimate.finish_at` 이 같은 값**임을 같이 잠근다 — 사람이 읽는 줄과 스크립트가 읽는 값이 다르면 그게 제일 나쁜 버그다 |
| 11 | **합류한 tree 잡의 줄**. bob 이 같은 트리·프리셋을 낸다 → `joined: true` · 키 순서 그대로 · 줄이 `joined job #<id> queued · 1st in line · wait 0s · eta … · same preset, inputs and tree · <url>` 통째로 같다 | `test_a_joined_tree_line_says_same_preset_inputs_and_tree_before_the_url` | detail 이 **왜 합류했는지**를 말하고 그 뒤에 URL 이 온다. 기존 테스트는 `1st in line` 과 `eta ` 만 봤다 |
| 12 | **2번째 잡에 합류하면 2 라고 듣는다**. alice 가 `ok`(1번째) · `bad`(2번째)를 내고, bob 이 같은 트리로 `bad` 를 낸다 → `joined` · `job_id` 는 2번째 잡 · `position == 2` · 줄에 `2nd in line` | `test_a_joiner_of_the_second_job_in_line_is_told_second_not_first` | 완료 기준 2 의 「합류한 세션은 **그 잡의** 순번을 본다」. 기존 테스트는 합류 대상이 늘 1번째라 「항상 1 을 찍는 구현」도 통과한다 |
| 13 | **합류가 순번을 당기면 그 새 순번을 본다**. admin 이 2번째 잡에 `--priority high` 로 합류 → `join_or_bump`(`store.py:1090`)가 우선순위를 올린다 → `position == 1` · 줄에 `1st in line` · 큐 문서에서 두 잡이 `[1, 2]` 로 뒤바뀐다 | `test_a_join_that_bumps_the_priority_reports_the_new_position` | 조회가 **제출 뒤** 한 번 일어나는 덕에 「합류가 바꾼 결과」를 본다. 제출 응답만 믿었다면(옛 `state: "submitted"`) 못 보는 값이다 |
| 14 | **git_ref 의 키 순서**. `rcm run deploy --ref main --no-wait` → `[…, estimate, ref, sha, url]` · `ref == "main"` · `sha` 가 40자 커밋 · 줄이 `submitted job #<id> queued · 1st in line · wait 0s · eta … · deploy · main @<sha7> · <url>` 통째로 같다 | `test_a_git_ref_json_puts_ref_and_sha_between_the_estimate_and_the_url` | §3 은 `ref`·`sha` 가 순번 다섯 **뒤**, `url` **앞**이라고 했다. 그리고 8 의 모양이 진짜 서버에서도 그대로임을 본다 |
| 15 | **합류한 git_ref**. 다른 세션이 태그 `v1`(= `main` 커밋)로 낸다 → `joined` · 같은 잡 · `position == 1` · `ref == "v1"`(내가 낸 이름) · `sha` 는 확정된 커밋 · 줄의 detail 이 `same preset, inputs, commit <sha7>` | `test_a_joined_git_ref_reports_the_joined_jobs_position_and_names_the_commit` | git_ref 합류의 detail 은 저장소 어디에서도 시험된 적이 없었다(기존 m3 테스트는 `"joined job" in err` 까지만). `ref` 는 **요청한 이름**이고 `sha` 는 **확정된 커밋**이라는 것도 여기서 갈린다 |

### 순번 경계 (§9-1 · §9-3 · §10)

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 16 | **3번째는 바로 앞을 가리킨다**. `ok`·`bad`·`slow` 를 차례로 → 3번째의 `position == 3` · `ahead_job_id` 가 **2번째**(맨 앞이 아니다) · `blocked_by` null · 줄에 `3rd in line` 과 `wait 20m 00s` · 큐 문서가 `[1, 2, 3]` | `test_the_third_in_line_names_the_job_just_ahead_of_it_and_waits_for_both` | §6 은 `ahead`(앞선 건수)를 **안 넣기로** 정하고 「누가 앞에 있냐는 `ahead_job_id` 가 답한다」고 했다. 그 답이 **바로 앞 잡**이라는 뜻임을 못 박는다(줄의 맨 앞 잡이 아니다). 대기 시간이 600 × 2 인 것으로 「앞의 둘을 다 세었다」도 같이 본다 |
| 17 | **도는 잡은 순번을 안 먹는다**. 워커를 켜고 `slow` 를 돌린 뒤(`wait_state(running)`) 두 잡을 더 낸다 → 각각 `position` 1·2 · 첫 대기 잡의 `ahead_job_id` 가 **도는 잡** · 큐 문서에서 도는 잡은 `position: null` · 줄에 `1st in line`·`2nd in line` | `test_a_running_job_takes_no_slot_so_the_next_job_is_first_in_line` | 「순번은 **대기 잡만** 세고 1 부터 시작한다」. 도는 잡을 1 로 세면 모든 대기 잡이 한 칸씩 밀려 사람이 「내 앞에 하나 더 있다」고 읽는다 |
| 18 | **우선순위가 순번을 바꾼다**. alice 의 `ok`·`bad` 뒤에 admin 이 `slow --priority high` → `position == 1` · `ahead_job_id` null · 줄에 `1st in line`·`wait 0s` 이고 **`3rd` 가 없다** · 큐 문서가 `[high 1, ok 2, bad 3]` | `test_a_high_priority_job_reports_first_in_line_and_pushes_the_others_back` | §10 의 「`position` 은 우선순위를 따른다」. 제출 순서를 순번으로 쓰는 구현(`len(queue)+1`)은 여기서만 빨개진다 |
| 19 | **조회 전에 끝난 잡**. 조회 직전에 취소 → `state == "cancelled"`(업로드 응답의 `queued` 가 **아니다**) · `position` null · `estimate` null · 첫 세 키와 마지막 키는 그대로 · 줄에 `in line` 도 `0th` 도 없고 `submitted job #<id> cancelled · …` 로 시작해 URL 로 끝난다. 덧붙여 **`GET /jobs/{id}` 의 종료 잡 문서에 순번 다섯 칸이 아예 없다**는 것도 단언한다 | `test_a_job_that_finished_before_the_lookup_has_no_position_and_no_in_line_fragment` | 완료 기준 3 의 「종료 잡」 쪽. 그리고 **「조회한 문서가 업로드 응답을 이긴다」**는 §3 의 핵심 규칙을 이기는 쪽이 명확한 경우로 잠근다. 마지막 단언은 명세 §3 의 사실 오류를 못 박는다(아래 「의견」 2) |

### 한 줄이 옛 두 줄을 대신한다 (§4)

| # | 시나리오 | 테스트 함수 | 왜 |
|---|---|---|---|
| 20 | **tree**: 새 잡과 합류 각각에서 식별 줄이 **정확히 하나**이고, 옛 `submitted job #N · <url>`(상태 없는 줄)도 옛 `joined job #N (queued) — …` 도 없다 | `test_no_wait_names_the_job_exactly_once_and_drops_the_old_identification_lines` | 옛 줄을 지우지 않으면 같은 잡이 두 번 소개된다. 「상태 없는 옛 제출 줄」은 `submitted job #N · ` 라는 **부분 문자열**로 잡는다 — 새 줄에는 id 뒤에 반드시 상태가 온다 |
| 21 | **git_ref**: 새 잡과 합류 각각에서 식별 줄이 하나이고 `^submitted job #\d+ \(` · `^joined job #\d+ \(` 가 없다(옛 괄호 줄) | `test_a_git_ref_no_wait_names_the_job_exactly_once` | git_ref 는 옛 코드가 `--no-wait` 검사 **앞에서** 줄을 찍었다 — 이 모드가 두 줄이 나던 자리다 |

**21개 함수 · 40건**(파라미터 포함). 전부 초록이다.

## 못 잠근 것

1. **`uploading` 상태의 조회 문서.** 합류 대상이 아직 업로드 중이면 줄은
   `joined job #N uploading · 1st in line · …` 이고 `reason` 은 `uploading`(§4 규칙상 안 보인다)이다.
   CLI 를 통해 이 상태를 **결정적으로** 만들려면 제출 경로를 가짜로 만들어야 해서 안 만들었다 —
   `describe` 쪽만 단위(시나리오 4)로 덮었다.
2. **새(합류 아닌) 잡이 조회 시점에 `running` 인 경우.** 업로드와 조회 사이에 워커가 집어야
   하는데 그건 경합이다(§10). 단위(1·2)와 기존
   `test_a_running_job_has_no_position_and_no_0th_in_line`(합류 경로)로 대신했다.
3. **`blocked_by` 가 채워진 줄**(concurrency 그룹) · **워커 다운·정지의 `eta` 없음** — C 의 몫이다.
   그래서 「`estimate.finish_at` 이 null 일 때 줄에 `eta —` 가 안 나온다」는 이 파일에 없다.
4. **`fmt_clock` 자체.** 줄의 기대값을 CLI 와 같은 함수로 만들기 때문에 그 함수가 틀리면 양쪽이
   같이 틀린다. 시각 포맷의 정본은 `core/render_text` 의 시험이다.
5. **종료 잡의 순번 다섯 키를 넣을 것인가 뺄 것인가.** 명세가 안 정했다(아래 2). 시나리오 19 는
   `body.get(...) is None` 으로 **두 답 모두에서 초록**이 되게 썼다 — 정해지면 조여야 한다.

## 명세·구현에 대한 의견

1. **§4 의 셋째 예시가 같은 절의 모양 규칙과 어긋난다.** 규칙은
   `<submitted|joined> job #<id> <state> · <describe 조각> · [detail ·] <url>` 인데 예시는
   `submitted job #12 queued (deploy · main @a1b2c3d) · 1st in line · …` 로 detail 을 **상태 뒤
   괄호**에 넣었다. 구현은 규칙을 따라
   `submitted job #12 queued · 1st in line · wait 0s · eta 16:45 · deploy · main @a1b2c3d · http://…`
   를 찍는다(시나리오 8·14 가 이걸 잠갔다). 둘 중 하나는 고쳐야 한다. 참고로 **기다리는 경로는
   여전히 괄호 형태**(`submitted job #12 (deploy · main @a1b2c3d) · <url>`, `cli.py:502`)라, 지금은
   같은 정보가 모드에 따라 두 모양으로 나온다. 그리고 규칙 쪽 모양은 detail 자체가 ` · ` 를 품고
   있어서 `deploy` 와 `main @a1b2c3d` 가 `describe` 조각처럼 보인다 — 읽는 사람에겐 예시 쪽이 낫다.
2. **§3 의 「종료 잡은 서버가 `position: null` 을 준다」는 사실이 아니다.** `GET /jobs/{id}` 는 끝난
   잡에 `recent_json`(`core/status.py:189`)을 주는데 거기엔 `position`·`reason`·`ahead_job_id`·
   `blocked_by`·`estimate` 가 **한 칸도 없다**(시나리오 19 의 마지막 단언이 이걸 확인한다).
   `null` 은 서버가 준 게 아니라 `_no_wait_json` 의 `view.get(k)` 가 만든 것이다. `running`·
   `cancelling` 에 대해서만 참인 문장이다. 그 결과 끝난 잡의 JSON 은 `"estimate": null` ·
   `"reason": null` 을 싣는데, 이건 §3 이 바로 다음 줄에서 세운 규칙(**null = 「없다」**,
   **빠짐 = 「모른다」**)과 미묘하게 어긋난다 — 「이 잡은 estimate 가 없다」와 「이 잡은 estimate 를
   더 이상 안 준다」는 다르다. **정해야 할 것**: 종료 잡의 JSON 은 다섯 키를 null 로 실을 것인가,
   조회 실패 때처럼 아예 뺄 것인가. (실용적으로는 지금 동작이 낫다 — 키가 늘 있으면 스크립트가
   `body["position"] is None` 하나로 「순번 없음」을 읽는다. 다만 명세가 그렇게 **말하지는** 않는다.)
3. **`describe()` 안에서 `head` 매개변수가 가려진다.** `cli.py:128` 의 progress 가지가
   `head = f"step {...}"` 로 **같은 이름**에 다시 쓴다. 오늘은 `parts[0]` 을 이미 만든 뒤라 무해하지만,
   누가 순서를 바꾸거나 조각을 하나 더 넣으면 제출 줄의 앞머리가 조용히 `step 1/4` 가 된다.
   지역 변수 이름만 바꾸면 된다(`step` 등). 시나리오 1 이 그 사고를 잡는다.
4. **git_ref 의 `--no-wait` 은 산출물 안내 줄을 안 찍는다.** tree 모드는
   `fetch its artifacts later with \`rcm artifacts N --fetch --output DIR\`` 을 찍는데
   (`cli.py:437`) `_run_git_ref` 는 그 앞에서 `return` 한다. 이 변경 이전부터 그랬으므로 회귀는
   아니지만, 이제 두 모드의 `--no-wait` stderr 가 한 줄 다르다. 문서(C)와 함께 정할 일이다.
5. **`--no-wait` 의 순번은 「그 순간의 사실」이지 약속이 아니다** — §10 이 이미 적었고, 시나리오 13 은
   그게 **뒤에 들어온 high 잡** 때문만이 아니라 **합류 하나만으로도** 바뀔 수 있음을 보인다
   (`join_or_bump` 이 우선순위를 올린다). 문서에 한 줄 보태면 좋겠다.
