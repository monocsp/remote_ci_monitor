# `--no-wait` 테스트 시나리오 B — 조회가 깨져도 제출은 선다 (2026-09-09)

`docs/nowait-workplan.md` §5 「표시용 조회는 제출을 깨뜨리지 않는다」와 §9 완료 기준 **4**(어떻게 깨뜨려도
종료 코드 0 이고 그때 순번 키는 없다) · **5**(기다리는 경로의 출력이 그대로다)를 테스트로 옮긴 것이다.
이 문서가 재는 것은 하나다: **`--no-wait` 의 0 은 「제출됐다」는 뜻이지 「조회됐다」가 아니다.** 그래서
조회를 낼 수 있는 모든 방법으로 깨뜨려 놓고 — 연결 거부 · 401 · 404 · 500 · 503 · JSON 아님 · 빈 몸통 ·
리스트 · 문자열 · null · 타임아웃 · 소켓 오류 — 그래도 잡이 **서버의 큐에 서 있고** 종료 코드가 0 이며
순번 다섯 칸이 **아예 없는지**(`null` 도 아니다) 를 본다. 그리고 이 변경이 **옮기지 않았어야 할 것**
(기다리는 경로 · 다른 플래그 · 실패 경로)을 골든으로 잠근다.

**다른 담당의 몫은 안 건드린다.** 성공한 조회의 JSON 키·순서·한 줄의 모양과 순번 경계(대기 여럿 ·
`running` · 종료 · 합류 · 우선순위)는 A(`tests/test_nowait_contract.py` · `docs/nowait-test-scenarios-a.md`),
진짜 워커 e2e · `blocked_by` · 문서와 스크린샷 앵커 대조는 C 다. 이 문서는 `src/` 를 고치지 않고,
대상 파일은 새 파일 `tests/test_nowait_resilience.py` 하나다.

**65건 · 60 초록 · 5 빨강.** 빨간 5건은 시나리오 6 이고, 아래 「찾은 결함」 1 의 버그다.

## 공통 — 도우미와 규칙

**가짜는 `Client.job` 이 아니라 `urlopen` 바로 위에 둔다.** `spy_http(monkeypatch, on_view=…)` 는
`urllib.request.urlopen` 을 감싸 **모든** 호출을 `(url, timeout)` 으로 적고, `on_view` 를 주면
`GET /jobs/{id}?tail=0` 만 그것으로 대신한다. 나머지 요청(`/api/status` · `/api/whoami` · `POST /jobs` ·
`PUT /jobs/{id}/tree`)은 **진짜 소켓으로 나간다** — 그래서 「그래도 잡은 큐에 있다」를 서버의
`/api/status` 로 진짜 확인할 수 있다. `Client.job` 을 갈아 끼우면 `get_json` · `_request` 는 한 줄도 안
돌아서 상한이 정말 내려가는지도, 몸통 파싱이 어디서 터지는지도 못 본다.

**상한이 소켓까지 갔다는 증명.** `urlopen` 이 실제로 받은 `timeout` 값이다(시나리오 7).
`cli.NO_WAIT_VIEW_TIMEOUT`(5.0)이 **`Client` 의 기본 상한 15.0 과 다르다**는 것까지 봐야 「그냥
기본값이 흘러간 것」이 아니다. 같은 실행의 다른 요청들이 5.0 이 아닌 것도 함께 단언한다.

**왕복 세기의 두 함정.**

| 무엇 | 왜 안 되나 | 그래서 |
|---|---|---|
| URL 로만 세기 | 기다리는 경로의 폴링(`wait_for_job` → `client.job(job_id)`)도 **같은** `/jobs/{id}?tail=0` 이다 | 표시용은 상한으로 갈린다 |
| 상한으로만 세기 | SSE 스트림의 `idle_timeout` 이 `SSE_TICK_SECONDS = 5.0`(client.py:48) 이라 **마침 같다** — 실측으로 `/jobs/1/events` 가 걸렸다 | URL 과 상한을 **둘 다** 본다(`capped_calls`) |

**실패를 만드는 세 가지.** `http_error(status, body)` 는 진짜 `urllib.error.HTTPError`(몸통은
`io.BytesIO`)를 던져 `_request` 의 `e.read()` → `ClientError` 길을 그대로 태운다. `responds(raw)` 는
`with` · `status` · `headers` · `read()` 만 있는 최소 응답을 돌려준다(`get_json` 이 보는 것 전부).
`raises(exc)` 는 소켓 층의 예외다. **`OSError` 는 진짜 스택에서는 `_job_view` 까지 못 온다** —
`_request`(client.py:502)가 먼저 잡아 `ClientError` 로 바꾼다. 그래서 `_job_view` 의 `except OSError`
가지는 `Client.job` 을 직접 갈아 끼워야 지나간다(시나리오 4).

**골든이 진짜 골든인지 확인했다.** 기다리는 경로의 시나리오 12·13·14·16(6건)을 **고치기 전 코드**
(`git archive 679266e^` 로 뽑아 `pytest -o pythonpath=<옛 src>`)로 돌려 **전부 초록**인 것을 봤다. 즉 이
단언들은 새 동작을 실수로 적어 넣은 것이 아니라 **바뀌지 않은 것**을 적은 것이다(시나리오 15 만은 옛
코드에 `NO_WAIT_VIEW_TIMEOUT` 이 없어 못 돌린다 — 새 상수를 재는 시험이라 당연하다). 반대로 네 가지
돌연변이를 스크래치 사본에 넣어 빨개지는 것도 확인했다(저장소의 `src/` 는 안 건드렸다):

| 돌연변이 | 빨개진 것(오늘 이미 빨간 시나리오 6 의 5건은 뺀 수) |
|---|---|
| `_no_wait_json` 이 조회 실패에도 다섯 칸을 `null` 로 채운다 | **16건** — 시나리오 1·3·4·7·9 |
| `_job_view` 가 `timeout=` 을 안 준다 | **2건** — 시나리오 8·9 |
| `_wait` 가 표시용 조회를 한다 | **1건** — 시나리오 15 |
| `_no_wait_json` 이 `state` 를 `"submitted"` 로 되돌린다 | **25건** — 시나리오 1·3·4·5·7·10·17 |

**시간 단언 금지.** 「느린 조회」는 초를 재지 않는다 — 소켓이 상한에서 끊겼을 때 나는 예외
(`TimeoutError`)를 그 자리에 놓고, 그 뒤의 **횟수와 종료 코드**로만 증명한다(시나리오 8).

## ① 조회를 깨뜨린다 — `tests/test_nowait_resilience.py`

| # | 시나리오 | 참이어야 하는 것 | 테스트 함수 |
|---|---|---|---|
| 1 | **깨뜨리는 방법 12가지**. `srv`(워커 없음)에 트리를 내고, 표시용 조회만 각각 연결 거부(`URLError(ConnectionRefusedError)`) · 401 · 404 · 500 · 503 · `<html>` 몸통 · 빈 몸통 · `[1,2,3]` · `"queued"` · `null` · `TimeoutError` · `OSError(ECONNRESET)` 로 만든다 | 12가지 전부: **exit 0** · `joined false` · `state == "queued"` 이고 `ALL_STATES` 안 · 다섯 칸(`position`·`reason`·`ahead_job_id`·`blocked_by`·`estimate`)이 **키 자체로 없다** · JSON 키가 정확히 `["job_id","joined","state","url"]` · `/api/status` 의 큐에 그 잡이 `queued` 로 **진짜** 있다 · 왕복 **1번**(재시도 없음) | `test_a_broken_display_lookup_leaves_the_submission_standing` |
| 2 | **같은 12가지의 stderr**. 사람이 읽는 줄이 남아야 세션이 잡을 되찾는다 | 줄이 **정확히** `submitted job #<id> queued · <url>`(§4 마지막 줄) · 어디에도 `in line` · `eta ` 조각이 없다(순번 조각만 빠진다) | `test_a_broken_display_lookup_still_names_the_job_and_its_url` |
| 3 | **합류한 세션의 조회가 깨진다**. alice 가 낸 잡에 bob 이 같은 트리로 합류하고 그때 조회가 500 | exit 0 · `joined true` · 같은 `job_id` · `state` 가 `ALL_STATES` 안(합류 응답의 상태 — 앞 잡이 아직 `uploading` 일 수 있다) · 다섯 칸 없음 · 줄이 `joined job #<id> <state> · same preset, inputs and tree · <url>` | `test_a_broken_display_lookup_on_a_joined_submission_keeps_the_detail` |
| 4 | **`except OSError` 가지**. `Client.job` 이 `OSError(EMFILE)` 를 던진다(진짜 스택에서는 `_request` 가 먼저 잡으므로 이 층에서만 재현된다) | exit 0 · `state == "queued"` · 다섯 칸 없음 · 잡은 큐에 | `test_client_job_raising_oserror_is_swallowed_too` |
| 5 | **몸통이 `{}` 인 200**. dict 라서 구현은 「조회됐다」로 친다 | exit 0 · `state` 는 업로드 응답의 `queued` 로 떨어진다(`{}.get("state") or state`) · 잡은 큐에 · 왕복 1번. **다섯 칸이 `null` 로 들어가는 것은 단언하지 않는다** — 「명세에 대한 의견」 1 | `test_an_empty_json_object_falls_back_to_the_upload_state` |
| 6 | ⚠️ **JSON 객체이긴 한데 타입이 다르다**(빨강 5건). `{"position":"3"}` · `{"estimate":"soon"}` · `{"estimate":{"wait_seconds":"soon"}}` · `{"estimate":{"finish_at":12345}}` · `{"reason":7}` | §5·§9-4 가 약속한 최소치: **exit 0** · 그 잡이 큐에 `queued` 로 있다 · stdout 에 그 잡의 JSON. **오늘은 다섯 건 다 트레이스백으로 죽는다** — 「찾은 결함」 1 | `test_a_wrong_typed_display_document_must_not_kill_the_submission` |
| 7 | **git_ref 분기도 같다**(`_run_git_ref` 는 다른 코드다). `deploy --ref main --no-wait` 에 조회 503 | exit 0 · `state == "queued"`(제출 응답이 그렇게 말한다 — server.py:1508) · `ref`·`sha` 는 그대로 · 키 순서가 `job_id·joined·state·ref·sha·url` · 다섯 칸 없음 · 잡은 큐에 · 왕복 1번 · 줄이 `submitted job #N queued · deploy · main @<sha7>` 로 시작 | `test_a_broken_display_lookup_on_a_git_ref_submission_keeps_ref_and_sha` |

## ② 상한과 왕복 횟수

| # | 시나리오 | 참이어야 하는 것 | 테스트 함수 |
|---|---|---|---|
| 8 | **상한이 소켓까지 내려간다**. 조회를 성공시키고 `urlopen` 이 받은 값을 본다 | `view_calls` 가 정확히 `[(".../jobs/<id>?tail=0", 5.0)]` — 즉 `cli` → `Client.job` → `get_json` → `_request` → `urlopen` 을 통과했다 · `NO_WAIT_VIEW_TIMEOUT == 5.0` 이고 `Client(...).timeout`(15.0)과 **다르다** · 같은 실행의 다른 요청은 아무도 5.0 을 안 쓴다 · 조회가 성공했으니 `position == 1` | `test_the_five_second_cap_reaches_the_socket_and_nothing_else_uses_it` |
| 9 | **느린 조회는 다시 걸지 않는다**. 조회가 `TimeoutError` 로 끊긴다 | exit 0 · 왕복이 **정확히 1번**이고 그 상한이 5.0 · 다섯 칸 없음. 초를 재는 단언은 없다 | `test_a_timing_out_lookup_is_not_retried` |

## ③ `"submitted"` 는 상태 이름이 아니다

| # | 시나리오 | 참이어야 하는 것 | 테스트 함수 |
|---|---|---|---|
| 10 | **네 갈래의 `state`**. 새 잡 · 합류 · 조회 실패를 한 시험에서 모은다 | 셋 다 `"queued"` 이고 전부 `core.model.ALL_STATES` 안 · `"submitted" not in ALL_STATES` | `test_no_no_wait_path_ever_reports_the_state_submitted` |
| 11 | **`src/` 전체 grep**. `.py`·`.js`·`.html`·`.css`·`.json` 을 훑어 `"state": "submitted"` · `state = "submitted"` · `or "submitted"` 세 모양을 찾는다 | 0건. (`'submitted' job #` 같은 **문구**는 안 잡는다 — 상태 값으로 쓰인 자리만 본다) | `test_the_word_submitted_is_not_a_state_anywhere_in_the_source` |

## ④ 기다리는 경로는 한 글자도 안 바뀌었다 (GOLDEN)

시나리오 12~16 은 **고치기 전 코드에서도 초록**인 것을 확인했다(위 「공통」).

| # | 시나리오 | 참이어야 하는 것 | 테스트 함수 |
|---|---|---|---|
| 12 | **`rcm run`(기다림) tree 모드**. 진짜 워커로 끝까지 | exit 0 · 식별 줄이 **하나**이고 정확히 `submitted job #N · <url>` · 마지막 줄이 `#N succeeded · green` · JSON 이 「서버의 `GET /jobs/N?tail=0` 문서 + `job_id` + `wait_exit_code`」와 **완전히 같다**(딕셔너리 등식). 스키마 v1 은 키를 더할 수 있으므로(§10) 서버 문서와의 **등식**으로 잠가 CLI 가 더한 것만 본다 | `test_the_waiting_run_prints_the_same_lines_and_the_same_json` |
| 13 | **합류 + `--timeout`**. 앞 잡에 합류하고 `--timeout 0` 으로 즉시 포기 | 식별 줄이 **하나**이고 정확히 `joined job #N (queued) — same preset, inputs and tree`(em dash 그대로 — `--no-wait` 의 `·` 한 줄이 아니다) · `rcm: --timeout 0s elapsed; job N is still queued` · exit **3** · JSON 에 `wait_exit_code 3` · `joined true` | `test_the_waiting_join_line_and_the_timeout_exit_are_unchanged` |
| 14 | **git_ref 의 식별 줄**. 이 변경에서 **자리가 옮겨진** 줄이다(`--no-wait` 분기 뒤로 내려갔다) | 식별 줄이 하나이고 정확히 `submitted job #N (deploy · main @<sha7>) · <url>` · exit 3 · `state queued` | `test_the_waiting_git_ref_line_is_unchanged` |
| 15 | **기다리는 경로는 표시용 조회를 안 한다**. 진짜 워커로 끝까지 돌리며 `urlopen` 을 전부 적는다 | 상한 5.0 이 걸린 `/jobs/{id}?tail=0` 왕복이 **0건** · 그런데 폴링의 조회는 있다(픽스처가 헛돈 것이 아니다) | `test_the_waiting_path_never_makes_the_display_lookup` |
| 16 | **`rcm wait --job N` 의 종료 코드 표**. succeeded · failed · cancelled · 「아직 모른다」 | 0 · 1 · 2 · 3 이고 JSON 의 `wait_exit_code` 도 같은 값 · 3 은 `--timeout 0s elapsed; … is still queued` 한 줄과 함께 나온다(3 은 실패가 아니다) | `test_rcm_wait_still_maps_finished_states_to_0_and_1` · `test_rcm_wait_still_maps_cancelled_to_2_and_a_waiting_job_to_3` |

## ⑤ 다른 플래그와의 조합

| # | 시나리오 | 참이어야 하는 것 | 테스트 함수 |
|---|---|---|---|
| 17 | **8가지 조합**: 없음 · `--no-join` · `--priority low` · `--priority normal` · `--pool default` · `--no-cache` · `--by` · 다섯 개 한꺼번에 | 전부 exit 0 · `state queued` · `position 1` · 다섯 칸이 **다 있다** · 왕복 **1번**(조합이 조회를 건너뛰거나 두 번 하게 만들지 않는다) · 잡은 큐에 | `test_no_wait_works_with_every_other_run_flag` |
| 18 | **조합이 「통과만」 하는 게 아니다**. 앞에 잡 하나를 세워 두고 다섯 플래그를 한꺼번에 | `joined false` 이고 **새 잡**(`--no-join`) · 큐 행의 `priority == -1` · `pool == "default"` · `requester.label == "release-bot"` · `--no-cache` 라 `/tree/manifest` 요청이 **0건** | `test_the_flags_still_reach_the_server_under_no_wait` |
| 19 | **`--priority high`**. 비 admin → 서버 403, admin → 통과 | 비 admin: exit **2** · `submit failed` · stdout 비었고 큐도 비었다. admin: exit 0 · `position 1` · 큐 행의 `priority == 1` | `test_priority_high_needs_an_admin_token_and_then_still_exits_0` |
| 20 | **`--priority urgent`**. argparse 가 끊는다(`SystemExit(2)`) | exit 2 · 문구에 `urgent` · stdout 비었고 **큐가 비었다** | `test_an_unknown_priority_is_argparse_usage_2_and_submits_nothing` |
| 21 | **`--fetch-artifacts --no-wait`**. 제출 **앞**에서 끝나야 한다 | exit 2 · stderr 가 **정확히** `rcm: --fetch-artifacts cannot be used with --no-wait (you have to wait to fetch)` 한 줄 · `Client.submit` 이 **한 번도 안 불린다**(불리면 그 자리에서 실패) · 스냅샷 줄도 없다 · 큐 비었다 · 표시용 조회 0건 | `test_fetch_artifacts_with_no_wait_is_refused_before_anything_is_sent` |

## ⑥ 실패한 제출·업로드는 조회로 이어지지 않는다

| # | 시나리오 | 참이어야 하는 것 | 테스트 함수 |
|---|---|---|---|
| 22 | **제출 실패 7가지**: 400 · 401 · 403 · 413 · 연결 실패(0) → **2**, 500 · 503 → **3** | 종료 코드 표가 그대로 · `rcm: submit failed: nope` · stdout 에 JSON 없음 · `/jobs/{id}?tail=0` 왕복 **0건**(잡이 없으니 조회할 것도 없다) | `test_a_failed_submit_keeps_its_exit_code_and_makes_no_lookup` |
| 23 | **업로드 실패 2가지**: 연결 실패(0) · 409 | 둘 다 exit **3** · `rcm: upload failed: upload broke` 이고 409 에는 `(retry with --no-cache)` 가 붙는다 · stdout 에 JSON 없음 · 조회 0건 · 잡은 `uploading` 인 채 큐에 남는다 | `test_a_failed_upload_is_still_exit_3_and_makes_no_lookup` |

**65건.** ① 33 · ② 2 · ③ 2 · ④ 7 · ⑤ 12 · ⑥ 9. 시험 함수는 24개이고 나머지는 매개변수다.

## 찾은 결함

1. ⚠️ **타입이 다른 조회 문서가 제출을 죽인다** (시나리오 6, **빨간 테스트 5건**).
   `_job_view`(cli.py:154-163)는 `ClientError`·`ValueError`·`OSError` 와 「dict 가 아님」만 막는다.
   **dict 이긴 한데 칸의 타입이 다르면** 그 다음 `_submitted_line` → `describe()` 에서 터지고, 그 예외는
   `main()`(cli.py:1688-1697)의 그물(`SystemExit`·`KeyboardInterrupt`·`ClientError`)에도 안 걸려
   **트레이스백과 exit 1**(콘솔 스크립트는 `sys.exit(main())` 에 닿지도 못한다)로 나간다. 잡은 이미 큐에
   있는데 세션은 제출 실패로 읽는다 — §5 가 막으려던 바로 그것이다. 터지는 자리:

   | 조회 문서 | 터지는 곳 | 예외 |
   |---|---|---|
   | `{"position":"3"}` | `_ordinal`(cli.py:67) 의 `n % 100` | `TypeError: not all arguments converted during string formatting` |
   | `{"estimate":"soon"}` | `describe`(cli.py:123) 의 `est.get(...)` | `AttributeError: 'str' object has no attribute 'get'` |
   | `{"estimate":{"wait_seconds":"soon"}}` | `fmt_duration`(render_text.py:77) 의 `round` | `TypeError: type str doesn't define __round__ method` |
   | `{"estimate":{"finish_at":12345}}` | `fmt_clock` → `parse_iso`(status.py:44) | `AttributeError: 'int' object has no attribute 'replace'` |
   | `{"reason":7}` | `describe`(cli.py:122) 의 `reason.replace` | `AttributeError: 'int' object has no attribute 'replace'` |

   다섯 갈래 전부 `cmd_run`(cli.py:436) → `main`(cli.py:1689) 으로 새어 나간다. `_run_git_ref`(cli.py:492)
   도 같은 `_submitted_line` 을 부르므로 같은 구멍이다. 낼 수 있는 곳: 판이 다른 서버 · 중간의 프록시 ·
   그리고 **이 저장소 자신의 앞날** — 결정 37(「서버가 문장이 아니라 코드를 보낸다」)대로 `reason` 이
   코드 객체가 되면 그날로 모든 `rcm run --no-wait` 이 죽는다. 고치는 방법은 두 갈래이고 **정하는 것은
   조율자의 몫**이라 시험은 §5·§9-4 의 최소치(exit 0 · 잡은 큐에 · stdout 에 JSON)만 단언한다:
   ① `_job_view` 가 아니라 **그리는 자리**(`_submitted_line`·`_no_wait_json`)를 `except Exception` 으로
   감싼다, ② `_job_view` 가 다섯 칸과 `state` 의 타입을 검사해 아니면 `None` 을 돌려준다(②가 §5 의
   「dict 가 아닌 응답도 `None`」 과 결이 같다).

2. **Ctrl-C 창이 새로 생겼다**(시험 없음 — 명세가 안 정한 것). 조회는 잡이 **이미 큐에 들어간 뒤**에
   걸리는 왕복인데, 그 사이 `KeyboardInterrupt` 가 오면 `main` 이 `rcm: interrupted` 를 찍고 **exit 130 ·
   stdout 비어 있음**으로 끝난다(실측: `Client.job` 이 `KeyboardInterrupt` 를 던지게 하고 확인 — 잡 #1 은
   서버에 `queued` 로 남아 있었다). 고치기 전에는 업로드 응답 직후 곧바로 JSON 을 찍었으므로 이 창이
   **없었다**. 세션은 잡 번호도 URL 도 못 받고 잡은 돌아간다. §5 는 「조회 실패」만 말하고 중단은 말하지
   않는다 — 잡아서 exit 0 으로 갈지, 130 이 맞는지 정해야 한다.

## 잠그지 못한 것

1. **벽시계 상한**. 「상한 5초」가 정말 5초 안에 끝나는지는 안 쟀다(시간 단언 금지). 잰 것은 **그 값이
   소켓까지 내려간다**는 것뿐이다 — 아래 「명세에 대한 의견」 2 가 그 차이다.
2. **`urlopen` 안쪽의 왕복**. 왕복 횟수는 `urlopen` **호출 수**로 센다. 기본 opener 의
   `HTTPRedirectHandler` 가 3xx 를 따라가면 소켓 왕복은 둘인데 내 계수기는 하나로 본다. 이 변경이 만든
   문제는 아니지만 「왕복은 정확히 한 번」의 증명에는 구멍이다.
3. **진짜 느린 서버**. 몸통을 찔끔찔끔 흘리는 응답자는 in-process 서버로 못 만들었다(만들면 시간 단언이
   된다). 의견 2 는 코드를 읽어 낸 결론이지 실측이 아니다.
4. **`--no-wait` 과 `--exclude`·`--source`·`--server`·`--token`·`--client-config`** 조합은 안 봤다 —
   제출 경로에만 닿고 표시용 조회와 이음매가 없다.
5. **동시성**. 조회와 워커의 집기가 겹치는 경합(§10 첫 항목)은 C 의 진짜 워커 e2e 몫이다.

## 명세에 대한 의견

1. **`{}` 는 「조회됐다」인가 「모른다」인가**(시나리오 5). §5 는 「dict 가 아닌 응답도 `None`」 이라고만
   한다. `{}` 는 dict 라서 구현은 성공으로 치고 다섯 칸을 **`null` 로** 싣는다. 그런데 같은 JSON 의
   `state` 는 `queued` 다 — **줄 서 있는 잡인데 순번이 없다**는 모순이고, §3 의 「`null` 은 「순번이
   없다」는 뜻이라 「모른다」와 다르다」에 정면으로 걸린다. 명세가 「다섯 칸 중 하나도 없는 dict 는
   `None` 으로 친다」거나 「`state` 가 없으면 `None`」 이라고 한 줄 더 정해야 한다.
2. **5초는 소켓 상한이지 왕복 상한이 아니다**. `_request` 는 `urlopen(req, timeout=…)` 한 번에 그 값을
   넘기고(client.py:490), 그건 `socket.settimeout` 이라 **연산마다** 다시 시작된다. 4초마다 1바이트씩
   흘리는 응답자는 상한에 안 걸리고 제출 경로를 얼마든지 붙들 수 있다. DNS 해석도 이 상한 밖이다.
   §5 의 「상한 **5초**」를 「소켓 무응답 5초」로 고쳐 쓰거나, 벽시계 마감이 필요하다면 그렇게 정해야
   한다. 실전에서는 서버가 같은 랜에 있어 문제가 안 될 뿐이다.
3. **§3 의 「`running`·종료 잡은 서버가 `position: null` 을 준다」는 종료 잡에서는 사실이 아니다.**
   종료 잡의 `GET /jobs/{id}` 문서에는 다섯 칸이 **아예 없고**(터미널 잡은 `queue_row_json` 을 안 탄다),
   `--no-wait` JSON 의 `null` 은 서버가 준 게 아니라 `view.get(k)` 의 기본값이다. 결과는 같지만 명세의
   설명이 틀렸다 — 「서버가 안 주면 `null` 로 싣는다」로 고쳐야 다음 사람이 안 헷갈린다.
4. **`fetch its artifacts later with …` 안내 줄이 tree 모드에만 있다**(cli.py:437). git_ref 의
   `--no-wait` 은 안 찍는다. 이 변경이 만든 비대칭은 아니지만(고치기 전에도 그랬다) §4 가 「`--no-wait`
   이면 한 줄이 그 자리를 대신한다」고만 해서 두 모드의 줄 수가 다른 것이 의도인지 안 보인다.
5. **`describe()` 가 자기 `head` 매개변수를 덮어쓴다**(cli.py:128). 진행 조각을 만들 때 같은 이름의 지역
   변수로 다시 묶는다. `parts[0]` 을 먼저 만들어 두어 오늘은 무해하지만, 그 아래에서 `head` 를 한 번 더
   쓰는 순간 `--no-wait` 의 제출 줄이 `step 1/4 …` 로 바뀐다. 이름만 갈라 두면 될 일이다.
