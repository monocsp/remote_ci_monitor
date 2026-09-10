# `--no-wait` 작업 명세 — 제출만 하고 빠지는 세션에게 순번과 ETA 를 준다

> 오너 요청(2026-09-09) · PR [#72](https://github.com/monocsp/remote_ci_monitor/pull/72).
> `PLAN.md` 의 마일스톤 밖이다 — M0~M5 는 끝났고(결정 30) 이후는 실기 결과에 따른 운영 개선이다.
> 이 문서는 그 개선 하나의 정본이다.
>
> **바꾸지 않는 것**: 서버(라우트·응답·`/api/status` 스키마 v1) · 기다리는 경로(`rcm run` ·
> `rcm wait`)의 stderr·stdout 한 글자 · `--no-wait` 의 종료 코드 `0` · 런타임 의존성 0 ·
> 주석은 한국어, 식별자·CLI help·문서는 영어.

## 1. 문제

`--no-wait` 로 제출하면 이것만 돌아왔다:

```json
{"job_id":155,"joined":false,"state":"submitted","url":"http://…/#/jobs/155"}
```

**순번도 ETA 도 없다.** 제출만 하고 빠지는 세션은 「내가 몇 번째냐 · 언제 끝나냐」를 알 길이 없어
`rcm eta --job N` 을 따로 쳐야 했다. 기다리는 경로는 그 값을 이미 한 줄로 보여 주고(`cli.describe`),
`rcm eta` 도 보여 준다 — `--no-wait` 만 빠져 있었다.

`state` 도 틀렸다. `"submitted"` 는 **서버가 쓰지 않는 이름**이다(진짜는 `uploading`·`queued`·
`running`·…). tree 모드는 그 문자열을 하드코딩했고, git_ref 모드의 `resp.get("state") or "submitted"`
는 **제출 응답**이라 업로드·자재화 전의 상태였다. 세션 스크립트가 `state` 로 분기하면 헷갈린다 —
`docs/m3-test-scenarios-b.md` 「명세에 대한 의견」 6 이 2026-09-05 에 이미 지적했다.

## 2. 어디서 값을 가져오나 — 서버는 안 고친다

`GET /jobs/{id}?tail=0` 은 활성 잡에 **이미** 큐 행 문서를 준다(`core/status.queue_row_json`):
`state` · `position` · `reason` · `ahead_job_id` · `blocked_by` · `estimate` · `progress` · `url`.
운영 서버의 잡 #155 로 실제 호출해 확인했다. **이건 CLI 만의 일이다.**

`--no-wait` 은 JSON 을 찍기 직전에 그 문서를 **한 번** 받는다. 그 시점의 잡은 이미 큐에 있다 —
tree 모드는 업로드가 끝나 `queued` 이고(PUT 응답이 그렇게 말한다), git_ref 모드는 제출과 동시에
`queued` 다.

## 3. stdout JSON

| 키 | 어디서 | 비고 |
|---|---|---|
| `job_id` · `joined` · `url` | 제출 응답 | 오늘 그대로 |
| `state` | **조회한 문서** | 없으면 업로드/제출 응답의 상태. `"submitted"` 는 사라진다 |
| `position` · `reason` · `ahead_job_id` · `blocked_by` · `estimate` | 조회한 문서 | 그대로 옮긴다(형태를 바꾸지 않는다) |
| `ref` · `sha` | 제출 응답 | git_ref 모드만, 오늘 그대로 |

- 키 순서는 `job_id` · `joined` · `state` · 순번 다섯 · (`ref` · `sha`) · `url`.
- **조회가 실패하면 순번 다섯 개를 아예 넣지 않는다.** `null` 은 「순번이 없다」는 뜻이라 「모른다」와
  다르다(fail-open 금지 — 모르는 것을 아는 척하지 않는다).
- **조회에 성공하면 다섯 키를 언제나 싣는다.** 활성 잡(`queue_row_json`)은 `running`·`cancelling`
  이면 서버가 `position: null` 을 주고, **종료 잡은 문서 모양이 다르다**(`recent_json` — 다섯 키가
  아예 없다). 그래서 종료 잡의 다섯 칸은 `.get()` 이 만든 null 이다. 뜻은 같다: 「그런 건 없다」.
  키가 **빠진** 것만이 「모른다」다.

## 4. stderr 한 줄

```
submitted job #8 queued · 1st in line · wait 0s · eta 16:45 · http://macmini:8787/#/jobs/8
joined job #10 running · step 1/4 fetch deps · elapsed 0s · eta 16:45 · same preset, inputs and tree · http://…
submitted job #12 queued · 1st in line · wait 0s · eta 16:45 · (deploy · main @a1b2c3d) · http://…
```

- 모양은 `<submitted|joined> job #<id> <state> · <describe 조각> · [detail ·] <url>`.
- **포매터를 새로 만들지 않는다.** `cli.describe()` 가 `head=` 를 받아 맨 앞의 `#<id> <state>` 자리를
  대신 채운다. 순번·스텝·경과·ETA·이유의 규칙은 기다리는 경로와 **같은 코드**다 — 두 화면이
  어긋날 수 없다.
- `detail` 은 모드가 아는 것: 합류면 `same preset, inputs and tree`(git_ref 는
  `same preset, inputs, commit <sha7>`), 새 git_ref 잡이면 `(<preset> · <ref> @<sha7>)`. **안에
  `·` 가 있는 detail 은 괄호로 묶는다** — 안 묶으면 목록 항목 둘로 읽히고, 기다리는 경로가 이미
  같은 정보를 괄호로 찍고 있어 한 정보가 두 모양이 된다.
- `--no-wait` 이면 오늘의 식별 줄(`submitted job #N · <url>` · `joined job #N (queued) — …`)은
  **찍지 않는다**. 한 줄이 그 자리를 대신한다. 기다리는 경로에서는 그 줄이 오늘 그대로 나온다.
- 조회가 실패하면 `submitted job #8 queued · http://…` — 순번 조각만 빠진다.

## 5. 표시용 조회는 제출을 깨뜨리지 않는다

이게 이 변경의 핵심 제약이다. **`--no-wait` 의 종료 코드는 「제출됐다」는 뜻이지 「조회됐다」가
아니다.**

- 상한 **5초**(`cli.NO_WAIT_VIEW_TIMEOUT`). `Client.job(..., timeout=)` 을 새로 받는다
  (`get_json` 에 키워드 하나 — `_request` 는 이미 `timeout` 을 받고 있었다). 이건 `urlopen` 의
  타임아웃이라 **소켓 동작 하나당** 5초지 왕복 전체의 마감이 아니다 — 4초마다 한 바이트씩 흘리는
  응답기는 이걸로 못 막는다(§10).
- 삼키는 것: `ClientError`(HTTP 오류 · 연결 실패 · 타임아웃) · `ValueError`(JSON 이 아님) ·
  `OSError` · **`KeyboardInterrupt`**(잡은 이미 났다 — Ctrl-C 로 잡 번호를 잃게 두지 않는다).
- **문서의 타입을 믿지 않는다.** dict 가 아니거나 `state` 가 없으면 안 쓴다. dict 라도 값의 타입이
  달라 `describe()` 가 터지면(`{"position": "3"}` 하나면 된다) 그 예외는 `main()` 의 그물에도 안
  걸려 이미 큐에 있는 잡을 실패로 만든다. 그래서 **한 번 그려 보고** 터지면 조회 실패와 똑같이
  다룬다 — 줄도 JSON 도 순번 조각을 통째로 뺀다.
- 그러고도 **exit 0**. 잡은 이미 큐에 있고, 그 사실이 종료 코드의 뜻이다.
- 우리가 거는 요청은 **하나**다. 재시도하지 않는다 — 재시도는 「기다리지 않겠다」는 요청과 반대다
  (urllib 이 3xx 를 따라가면 소켓 왕복은 그보다 많을 수 있다).

## 6. 정한 것 — `ahead`(앞선 건수)는 안 넣는다

`cmd_eta` 는 `busy_others + (position - 1)` 로 **전체 status 문서에서** 계산한다. `GET /jobs/{id}` 에는
없으므로 넣으려면 `/api/status` 를 한 번 더 받아야 한다. 그건 요청 시간의 91~93% 가
`list_samples` 인 무거운 문서다(`PLAN.md` 결정 49, 1만 행에서 245 ms). **제출 경로에 왕복을 하나 더
다는 값어치가 없다**고 봤다 — 「몇 번째냐」는 `position` 이 답하고, 「누가 앞에 있냐」는
`ahead_job_id` 와 `blocked_by.job_id` 가 답한다. 없는 값은 지어내지 않는다.

## 7. 코드가 바뀐 곳

| 파일 | 무엇 |
|---|---|
| `src/remote_ci_monitor/cli.py` | `describe(job, *, head=None)` · 상수 `NO_WAIT_VIEW_TIMEOUT`·`NO_WAIT_KEYS` · `_job_view()` · `_submitted_line()` · `_no_wait_json()` · `cmd_run` 의 tree 분기 · `_run_git_ref` |
| `src/remote_ci_monitor/client.py` | `get_json(path, *, timeout=None)` · `job(job_id, *, tail=0, timeout=None)` |
| `scripts/mutcheck.py` | 변이 ⑭ `nowait-view-trusted`(§11) |
| `tools/screenshots/build.py` | `cli-nowait` · `cli-join` 의 상자 앵커(줄이 접혀도 맞게) · `x-join` 도 96칸 접기 |

## 8. 테스트 배치 (격리 검증 A·B·C)

구현·문서는 이미 들어갔다(PR #72). 이 세 문서는 **밖에서 다시 재는** 것이다 — 명세와 코드를 읽고
스스로 시나리오를 세워, 잠기지 않은 곳과 어긋난 곳을 찾는다.

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_nowait_contract.py` | A | JSON 계약(키·순서·형태) · stderr 한 줄의 모양 · 순번 경계(대기 여럿 · `running` · 종료 · 합류 · 우선순위로 순번이 바뀔 때) · 두 소스 모드 · `describe(head=)` 순수 단위 |
| `tests/test_nowait_resilience.py` | B | 조회 실패·느림·이상 응답(404 · 500 · 빈 몸통 · 리스트 · 타임아웃) → exit 0 이고 순번 키 없음 · 상한이 실제로 전달되는가 · 왕복 횟수 · **기다리는 경로가 바이트 단위로 그대로인가**(GOLDEN) · 다른 플래그와의 조합(`--no-join` · `--priority` · `--pool` · `--no-cache` · `--fetch-artifacts` 거절) |
| `tests/test_nowait_e2e.py` | C | 진짜 서버·워커로: 큐 셋을 쌓고 각자의 순번 · concurrency 그룹에 막힌 잡의 `blocked_by` · 워커 다운/정지면 `finish_at` 이 null 이고 줄에 `eta` 가 안 나오는가 · 문서(`docs/usage*.md` §5)와 스크린샷 앵커(`tools/screenshots/build.py`) 대조 |

**규칙**: `src/` 를 고치지 않는다(버그를 찾으면 **빨간 테스트로 남기고 보고**한다) · 자기 파일만
만진다(남의 테스트 파일·`src/`·`docs/` 의 남의 문서 금지) · 각자 `docs/nowait-test-scenarios-<담당>.md`
를 남긴다 · 기존 테스트를 약화시키지 않는다 · 시간 단언 금지(마감 있는 폴링으로).

## 9. 완료 기준

1. `--no-wait` JSON 에 `state`(진짜 이름) · `position` · `reason` · `ahead_job_id` · `blocked_by` ·
   `estimate` 가 있고, 대기 잡의 `position` 이 큐의 실제 순번과 같다.
2. stderr 한 줄이 순번·대기·ETA·URL 을 말한다. 합류한 세션은 **그 잡의** 순번을 본다.
3. `running`·종료 잡은 순번 조각이 통째로 빠진다 — `0th` 같은 건 어디에도 안 나온다.
4. 표시용 조회를 어떻게 깨뜨려도 종료 코드는 0 이고, 그때 순번 키는 **없다**.
5. 기다리는 경로(`rcm run` · `rcm wait`)의 출력이 그대로다.
6. `ruff` · `pytest` · `node --test` · `mutcheck`(⑭ 포함) · `smoke_install.sh` 전부 초록.

## 10. 알려진 한계 · 위험

- **경합**: 조회는 업로드 직후 한 번이라, 그 사이에 잡이 집히면 `queued` 대신 `running` 이 나온다.
  틀린 게 아니라 그 순간의 사실이다 — 스크린샷 두 번 중 한 번이 그랬다.
- **왕복 하나**: 제출 경로가 요청 하나만큼 느려진다(로컬 5 ms 안팎). 상한 5초로 막았다.
- **`estimate` 통째로 싣기**: 서버가 그 문서에 키를 더하면 `--no-wait` JSON 에도 자동으로 는다.
  스키마 v1 은 **키 추가만** 하므로 이건 의도된 것이다(삭제·의미 변경은 `schema_version` 을 올린다).
- **`position` 은 우선순위를 따른다**: 뒤에 `high` 잡이 들어오면 내 순번은 커진다. **합류만으로도**
  바뀔 수 있다 — `store.join_or_bump` 이 대상 잡의 우선순위를 `max(기존, 요청)` 으로 올린다.
  제출 시점의 순번을 약속으로 읽으면 안 된다 — 화면(`rcm top`·웹)과 `rcm eta` 가 여전히 정본이다.
- **`position` 은 풀 안의 순번**이고 줄은 풀 이름을 말하지 않는다. `rcm eta` 도 그렇다(기존 성질).
- **상한 5초는 소켓 동작 하나당**이다(§5). 왕복 전체 마감이 필요해지면 별도 스레드나 마감 있는
  opener 가 필요하다 — 표시용 한 줄에 그만한 장치를 달지 않았다.
- **`ahead` 의 뜻이 화면마다 다르다**: `rcm eta` 의 `N ahead` 는 도는 잡까지 세고, 여기 `position`
  은 대기 잡만 센다. 그래서 문서는 「앞에 몇 개」가 아니라 「몇 번째」라고 쓴다.
- **git_ref 의 `--no-wait` 은 산출물 안내 줄을 안 찍는다**(tree 만 찍는다, `cli.py` `cmd_run`).
  M5e 부터 있던 비대칭이고 이번 변경과 무관하다 — 고칠 때는 두 모드를 같이 본다.

## 11. 격리 검증이 바꾼 것 (2026-09-09)

A·B·C 가 이 명세와 코드만 보고 밖에서 다시 쟀다 — 테스트 **139개**(A 40 · B 65 · C 34)와 시나리오
문서 셋. 셋이 찾은 것과 그래서 바꾼 것:

| # | 무엇 | 누가 | 그래서 |
|---|---|---|---|
| 1 | **타입이 다른 문서 하나가 제출을 죽인다.** `{"position": "3"}` 이면 `describe()` 가 `TypeError`, 그 예외는 `main()` 의 그물(`SystemExit`·`KeyboardInterrupt`·`ClientError`)에도 안 걸려 트레이스백 + 0 아닌 종료 코드 + 빈 stdout. **잡은 큐에 있는데 세션은 실패로 읽는다** — §5 가 막으려던 바로 그것 | B(빨강 5) | `_job_view` 가 **한 번 그려 보고** 터지면 조회 실패와 똑같이 다룬다. 진짜 방아쇠는 적대적 프록시가 아니라 **결정 37** 이다 — 서버가 `reason` 을 코드 구조로 바꾸면 모든 `--no-wait` 이 죽는다 |
| 2 | 조회 중 Ctrl-C → 종료 코드 130 · 빈 stdout · 잡은 큐에. 이 변경 **전에는 없던 창**이다(옛 코드는 업로드 응답 직후 JSON 을 찍었다) | B | `KeyboardInterrupt` 도 삼킨다 |
| 3 | `{}` 를 「순번 없는 대기 잡」으로 읽는다(좁은 fail-open) | B | `state` 없는 문서는 잡 문서가 아니다 |
| 4 | `describe()` 가 자기 `head` 인자를 지역 변수로 가린다 — 오늘은 `parts[0]` 을 먼저 만들어 무해하지만 순서만 바뀌면 머리가 조용히 `step 1/4` 가 된다 | A·B | 지역 이름을 `step` 으로 |
| 5 | §4 셋째 예시가 §4 규칙과 어긋나고, 같은 정보가 기다리는 경로와 **두 모양**이다 | A·C | git_ref detail 을 괄호로(§4) |
| 6 | §3 「종료 잡도 서버가 `position: null` 을 준다」가 **거짓** — `recent_json` 엔 다섯 키가 아예 없고 null 은 `.get()` 이 만든다 | A·B | §3 다시 씀 |
| 7 | §5 「상한 5초」는 `urlopen` 의 **소켓 동작당** 타임아웃이지 왕복 마감이 아니다(4초마다 한 바이트씩 흘리면 못 막는다) | B | §5·§10 에 적음 |
| 8 | §5 그림의 **대체 텍스트가 옛 그림을 설명한다** — ① 상자에 순번과 ETA 가 생겼는데 alt 는 「잡 번호를 찍고」에 머물러 있다 | C(빨강 2) | 두 언어 alt 수정 |
| 9 | 두 가이드가 「앞에 몇 개」라고 쓰는데 찍는 건 **순번**이다. `rcm eta` 의 `N ahead` 는 도는 잡까지 세어 같은 낱말에 다른 숫자가 된다 | C | 「몇 번째」로 |
| 10 | 시작할 수 없는 큐(정지·워커 없음)의 「eta 없음」을 두 가이드도 CHANGELOG 도 안 말한다 | C | 한 문장씩 추가 |
| 11 | `PLAN.md` 「`--no-wait` 면 JSON 만 찍고 0 으로 끝난다」가 낡았다 | C | 고침 |

뮤테이션 ⑭ `nowait-view-trusted` 를 더했다 — `_job_view` 의 가드를 고치기 **전으로 되돌리면**
B 의 테스트가 빨개진다(`scripts/mutcheck.py`). 검증이 진짜로 무언가를 잡는지 CI 가 매번 확인한다.

**안 고친 것**: git_ref 의 `--no-wait` 만 산출물 안내 줄을 안 찍는 비대칭(셋 다 봤다). M5e 부터
있던 것이고 이 변경과 무관하다 — §10 에 적어 두고 두 모드를 같이 볼 때 고친다.
