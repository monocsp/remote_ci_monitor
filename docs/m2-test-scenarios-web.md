# M2 테스트 시나리오 — 웹 UI 순수 함수 (`tests/web/*.test.js`)

`docs/m2-workplan.md` §0-B·§2·§3·§5·§6, 목업 `docs/wireframes/web-queue.html`(기획 항목 35개 · 「4. 이 화면이 정한 규칙」), PLAN.md 「/api/status 스키마 v1」·「fail-open 금지」에 대한 Node 테스트 목록이다. 구현보다 먼저 썼다(test-first). 시그니처는 workplan §2 를 따르고 기대값은 목업 1절의 문구와 `core/render_text.py` 의 `fmt_duration`·`fmt_clock` 에서 리터럴로 뽑았다. 구현과 어긋나면 맨 아래 「결정한 모호점」과 「현재 app.js 와 어긋나는 곳」부터 본다.

## 실행

- `node --test 'tests/web/*.test.js'` — 외부 패키지 없음(`node:test` · `node:assert/strict`). DOM 은 시험하지 않는다(jsdom 없음).
- **Node 22 에서 `node --test tests/web` · `node --test tests/web/` 는 디렉터리를 모듈로 취급해 `Cannot find module …/tests/web` 로 실패한다.** CI(workplan §6 `unit` 잡)는 glob 으로 부른다.
- `tests/web/helpers.js`: `load()` 가 `src/remote_ci_monitor/web/app.js` 를 `require` 한다(app.js 는 `module.exports = rcm`). Node 에는 `window` 가 없으니 `globalThis.window = globalThis` shim 만 두고 `document` 는 정의하지 않는다 — `boot()` 가 돌면 안 된다. `fixture(name)` 은 `fixtures/status-<name>.json` 을 매번 새 객체로 준다. `NOW` = `Date.UTC(2026, 8, 4, 0, 52, 12)`(= 모든 픽스처의 `generated_at`, Asia/Seoul 09:52:12), `TZ = "Asia/Seoul"`, `job(status, id)` · `recentJob(status, id)` · `fromNow(sec)`.
- 케이스 수: format 46 · reason 50 · summary 47 · progress 32 · connection 19 = **194**.

## 픽스처 (`tests/web/fixtures/`)

전부 PLAN 스키마 v1 예시와 같은 모양(`server.sse_connections` · `queue[].queued_at` · `estimate.confidence` 포함). 시각은 UTC `Z`, 표시는 `display_timezone: "Asia/Seoul"`.

| 파일 | 내용 | 테스트가 기대는 값 |
|---|---|---|
| `status-main.json` | 목업 1절. 큐 5 · 최근 8 · 중앙값 3키 · 호스트 1 · 레인 2/2 busy · uptime 3012 | 아래 표 |
| `status-empty.json` | `queue: []` · `recent: []` · `hosts: []` · `medians: {}` · 워커 둘 idle | notMoving ok · yourJobs none · hostPressure no_sample · queueHeader `0 jobs · 0 running · 0 waiting · lanes 0/2 busy` · workerPills `lane 1 · idle`, `lane 2 · idle` |
| `status-errors.json` | 네 섹션 전부 `null` + `*_error`(`database locked (retrying)` · `sampler: all collectors failed`) | notMoving unknown · yourJobs unknown · queueHeader `unknown` |
| `status-paused-down.json` | `server.paused {by: macmini-admin, at 00:50Z}` · lane 1 `down` error `ENOSPC` · lane 2 busy #409 · `last_error` · #413 queued reason `paused`, `finish_at`·`wait_seconds` null, confidence low/preset | reasonText `paused` · etaText `—` · confidenceBadge `low · preset` · workerPills `lane 1 · down` / `#409` / `paused` |
| `status-single-lane.json` | lanes 1 · worker busy #412 · #414 position 1 waiting_for_lane(waited 40s) | workerPills `worker busy #412` · queueHeader `2 jobs · 1 running · 1 waiting · oldest waiting 40s · lanes 1/1 busy` |

`status-main.json` 의 값(테스트 리터럴의 근거):

| 잡 | 상태·이유 | 시각(UTC) | estimate | 기대 표기 |
|---|---|---|---|---|
| #412 gate:full alice-laptop(alice@laptop) + joiner eve-ci(eve@ci) | running · lane 1 | created 00:50:40 · started 00:51:13 · finish 00:57:22 | high measured n=7 · expected 369 · elapsed 59 · waited 33 · remaining 310 | `running · lane 1` · `59s` / `waited 33s` · `09:57 · in 5m 10s` · `step 5/8 · test · 51s · job 59s` |
| #409 qa:device-smoke carol-mbp · group devices · git_ref dev | running · lane 2 | started 00:45:52 · finish 00:54:52 | med measured n=3 · expected 540 · elapsed 380 · waited 8 · remaining 160 · steps_total 3 partial | `running · lane 2` · `6m 20s` / `waited 8s` · `step 3/3 (so far) · boot-simulators · 3m 20s · job 6m 20s` |
| #413 qa:smoke bob-desk · group devices | queued pos 1 · blocked_by_group {409, devices, 160} | created 00:50:37 · finish 01:04:12 | group wait · preset n=1 · expected 540 · waited 95 · wait 180 | `⛓ blocked by #409 · devices · frees in 2m 40s` · `waiting 1m 35s` · `after #409` / `~10:04` · `low · group wait` |
| #414 gate:fast alice-laptop | queued pos 2 · waiting_for_lane ahead 412 | created 00:51:37 · finish 00:58:27 | high measured n=12 · expected 65 · waited 35 · wait 310 · remaining 65 | `waiting for lane · 2/2 busy · behind #412 · frees in 5m 10s` · `waiting 35s` · `09:58 · in 6m 15s` |
| #415 gate:full dan-pc | uploading pos 3 · received 30133340 / 48213344 · last_received 00:52:08 | created 00:51:50 · finish 01:04:36 | high measured n=7 · wait 375 · remaining 369 | `uploading · 30 / 48 MB` · `—` · `in 12m 24s` |

최근 완료(`finished_at` 내림차순): #411 failed exit 1 gate:fast bob `2 tests failed` step test 62s 09:47 · #410 succeeded gate:full alice 350s 09:40 `all 9 packages green` · #408 cancelled exit 2 qa:smoke carol `cancelled before start` cancelled_by `carol-mbp` started null 09:31 · #407 timed_out exit 2 gate:full dan `limit 20m` step test 1200s 09:22 · #406 lost exit 3 gate:full dan `server restarted 09:02` job_seconds null 09:02 · #405 succeeded gate:fast alice summary null 08:31 · #403 failed exit **null** gate:full dan `tar rejected: absolute path in archive` started null Sep 3 23:40 · #404 succeeded gate:full bob Sep 3 23:36. 호스트 macmini: cpu.busy 21 · memory 15032385536 / 25769803776(58.3%) · gpu 13 · load 3.48 · cores 10 · history 6표본(00:51:53 빠짐). 중앙값: gate:full 369/80/7 · gate:fast 65/40/12 · qa:device-smoke 540/12/3.

## `tests/web/format.test.js` — 46

### module contract
| 시나리오 | 테스트 |
|---|---|
| `require(app.js)` 가 §2 의 29개 함수를 전부 함수로 노출 | `require(app.js) returns rcm with every §2 pure function` |

### `fmtDuration` (render_text `fmt_duration` 과 동일)
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| null · undefined | `—` | `null and undefined → —` |
| 0 | `0s` | `0 → 0s` |
| 12 · 59 | `12s` · `59s` | `under a minute → Ns` |
| 310 · 60 · 62 · 3599 | `5m 10s` · `1m 00s` · `1m 02s` · `59m 59s` | `minutes → Mm SSs …` |
| 3720 · 3600 · 7380 · 86400 | `1h 02m` · `1h 00m` · `2h 03m` · `24h 00m` | `hours → Hh MMm …` |
| 59.6 · 59.4 · 0.4 | `1m 00s` · `59s` · `0s`(초 반올림 뒤 분 계산) | `rounds to the nearest second` |
| −5 | `0s` | `negative clamps to 0s` |

### `fmtClock(iso, tz, nowMs)` (render_text `fmt_clock` 과 동일 — 분 절삭)
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| 00:57:22Z · 00:54:52Z, Seoul, 오늘 | `09:57` · `09:54`(반올림 아님) | `today in the display tz → HH:MM, seconds truncated` |
| 2026-09-03T14:40Z | `Sep 3 · 23:40` | `another day → 'Mon D · HH:MM'` |
| 09-03 15:30Z(서울로는 9/4 00:30) | `00:30` — 「오늘」은 표시 시간대 기준 | `'today' is decided in the display tz` |
| tz UTC | `00:57` | `UTC display tz` |
| nowMs 없음 | 날짜 접두 없이 `23:40` | `nowMs omitted → always HH:MM` |
| null · undefined · "" · 쓰레기 | `—` | `null, undefined, empty and unparsable → —` |

### `fmtAgo` · `fmtCountdown`
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| 4 · 0 · 59 | `4s ago` · `0s ago` · `59s ago` | `under a minute → Ns ago` |
| 60 · 185 · 179 | `1m ago` · `3m ago` · `2m ago`(분은 내림) | `a minute and over → Nm ago, coarse (floor)` |
| 3600 · 7199 | `1h ago` · `1h ago` | `an hour and over → Nh ago` |
| null | `—` | `null → —` |
| 8 · 160 | `in 8s` · `in 2m 40s` | `'in ' + duration` |
| 0 · −3 | `now` | `zero and negative → 'now'` |
| null | `—` | `null → —` |

### `fmtBytes` · `fmtMb`
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| 48213344 · 594411520 | `48 MB` · `0.6 GB` (§2 예시) | `§2 examples` |
| 25769803776 · 12000000 | `25.8 GB` · `12 MB`(십진 1e6 · 1e9) | `decimal units …` |
| 499000000 · 500000000 | `499 MB` · `0.5 GB` | `MB/GB boundary is 500 MB` |
| 1000 · 999 · 0 | `1 KB` · `999 B` · `0 B` | `small sizes: B and KB` |
| null | `—` | `null → —` |
| fmtMb 500 · 169.6 · 0 (MB 입력) | `500 MB` · `170 MB` · `0 MB` | `megabytes in (top[].rss_mb) …` |
| fmtMb null | `—` | `null → —` |

### `ordinal` · `truncate` · `stateWord` · `stateGlyph` · `esc`
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| 1 2 3 4 · 11 12 13 111 112 113 · 21 22 23 101 · null | `1st 2nd 3rd 4th` · 전부 `th` · `21st 22nd 23rd 101st` · `—` | `ordinal` 4 케이스 |
| 40자 · 41자 · 기본 40 · max 8 · null | 그대로 · 39자+`…`(총 40) · 같음 · `alice@l…` · `—` | `truncate` 5 케이스 |
| timed_out · 나머지 8 · null | `timed out` · 그대로 · `unknown` | `stateWord` 2 케이스 |
| 9 상태 · null | ▶ · ↑ ■ ✓ ✗ ⏱ ■ ? · `·` | `stateGlyph` 2 케이스 |
| `<b>` · `a & b` · `"` · `'` · 섞임 · `&amp;` · null/undefined/412 | `&lt;b&gt;` · `&amp;` · `&quot;` · `&#39;`/`&#x27;` · 원문자 안 남음 · `&amp;amp;` · `""`/`""`/`"412"` | `esc` 5 케이스 |

## `tests/web/reason.test.js` — 50

`reasonText(row, nowMs, status)` → `{text, actionable, links: [{jobId}]}`. 픽스처 행에서 `variant(id, patch)` 로 변형을 만든다.

### 정상(actionable false)
| 이유 | 입력 | 기대 text | 테스트 |
|---|---|---|---|
| running | #412 · #409 · lane null | `running · lane 1` · `running · lane 2` · `running` | 2 |
| waiting_for_lane | #414 + status | `waiting for lane · 2/2 busy · behind #412 · frees in 5m 10s`, links `[{jobId: 412}]` | `waiting_for_lane → busy count …` |
| waiting_for_lane | status 없음 | `waiting for lane` 로 시작, `behind #412 · frees in 5m 10s` 포함 | `… without server` |
| waiting_for_lane | ahead null · wait null | `waiting for lane · 2/2 busy`, links `[]` | `… only the busy count` |
| uploading | #415 · received null | `uploading · 30 / 48 MB` · `uploading · — / 48 MB` | 2 |
| materializing | tree 48213344 · git_ref dev · bytes null | `preparing workspace · unpacking 48 MB` · `preparing workspace · fetching dev` · `preparing workspace` | 3 |
| cancelling | by alice-laptop kill +8s · by macmini-admin · kill null | `SIGTERM sent by alice@laptop · kill in 8s` · `SIGTERM sent by macmini-admin · kill in 8s` · `SIGTERM sent by alice@laptop · kill —` | 3 |

### 행동 가능(actionable true)
| 이유 | 입력 | 기대 text | 테스트 |
|---|---|---|---|
| blocked_by_group | #413 · remaining null | `⛓ blocked by #409 · devices · frees in 2m 40s` links `[{jobId: 409}]` · `… · frees in —` | 2 |
| upload_stalled | last_received −120s · −45s · null | `upload stalled 2m · 30 / 48 MB` · `upload stalled 45s · …` · `upload stalled — · …` | 3 |
| overdue | elapsed 580 expected 369 | `over by 3m 31s · expected 6m 09s`, links `[]` | 1 |
| stuck | elapsed 1150 expected 369 last_output −250s · last_output null · progress null | `⚠ likely stuck · 3× expected · no output for 4m` · `⚠ likely stuck · 3× expected` ×2 | 2 |
| paused · not_scheduled · worker_down | paused-down #413 · #414 변형 | `paused` · `not scheduled` · `no worker` | 3 |

### unknown · null · 표
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| `frobnicate` · null · 필드 없음 · row null | `unknown`, actionable false, links `[]` | 3 |
| 12개 이유 표 | actionable 은 정확히 `worker_down stuck upload_stalled not_scheduled blocked_by_group overdue paused` 만 true; links 는 항상 배열 | `actionable is exactly the Not-moving set` |

### `confidenceBadge(est)` · `etaText(row, tz, nowMs)` · `elapsedText(row, nowMs)`
| 함수 | 입력 | 기대 | 테스트 수 |
|---|---|---|---|
| confidenceBadge | #412 · #409 · paused-down #413 · default · #413 · overdue/stuck · null | `{high, high · measured n=7}` · `{med, med · measured n=3}` · `{low, low · preset}` · `{low, low · default}` · `{low, low · group wait}` · `{over, overdue}` ×2 · `{low, low · —}` | 7 |
| etaText | #412 · #414 · #415 · #413 · #413 finish null · overdue · paused · null | `{09:57, in 5m 10s}` · `{09:58, in 6m 15s}` · rel `in 12m 24s` · `{after #409, ~10:04}` · `{—, null}` · `{—, null}` · `{—, null}` · `{—, null}` | 8 |
| elapsedText | #412 · #409 · waited 0 · cancelling · #413 · #414 · #415 · estimate null · null | `{59s, waited 33s}` · `{6m 20s, waited 8s}` · sub 없음 · `{59s, waited 33s}` · `waiting 1m 35s` · `waiting 35s` · `—` · `—` · `—` | 7 |

## `tests/web/summary.test.js` — 47

### `notMoving(status, me)` → `{kind, lines: [{jobId, text, reason}]}`
| 시나리오 | 픽스처 | 기대 | 테스트 |
|---|---|---|---|
| 기본 화면 | main | list · 1줄 · #413 blocked_by_group · text 에 `blocked by #409`·`frees in 2m 40s` 포함, `#` 로 시작 안 함 | `main fixture → one line …` |
| 7가지 이유를 역순으로 넣음 | main + 변형 6행 | reason 순서 정확히 worker_down → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused, jobId `[421 422 423 424 413 425 426]`, 각 text 에 근거 숫자(`no worker` · `3× expected` · `upload stalled 2m` · `not scheduled` · `over by 3m 31s` · `paused`) | `orders worker_down → …` |
| 같은 이유 두 행 | main + #427 blocked | `[413, 427]` | `two rows with the same reason …` |
| 행동 가능 없음(#413 → waiting_for_lane) | main | ok, lines 없음 | `nothing actionable → ok` |
| 빈 큐 | empty | ok | `empty queue → ok` |
| queue null | errors | unknown (**mutcheck ⑥ 표적**) | `queue null → unknown, never ok` |
| 모든 행에 reason 없음 | main | unknown | `old server without a reason field …` |
| 한 행만 reason 없음 | main | ok 가 아님 | `one row missing its reason → not ok` |
| 스텝 실패했지만 계속 도는 잡 | main | ok | `a running job with a failed step …` |
| me 유무 | main | 결과 같음 | `me does not change what is listed` |

### `yourJobs(status, me)` → `{kind, lines: [{jobId, text}], more}`
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| me alice-laptop | list · `[412, 414]` · `running · ETA 09:57 · in 5m 10s · +1 joined` · `2nd in line · ETA 09:58 · waiting for lane` · more 0 | `requester → both active jobs …` |
| me eve-ci(합류자) | `[412]` | `joiner counts as mine` |
| null · undefined · "" | no_token | `no token → no_token` |
| nobody · 빈 큐 | none | `token with no jobs → none` |
| me = label `alice@laptop` | none(이름 기준) | `matches requester.name, not the label` |
| queue null | unknown | `queue null → unknown` |
| alice 잡 3개(#416 uploading 추가) | `[412, 414]` · more 1 | `more than two → …` |
| 큐 순서 뒤섞음 | `[412, 414]` | `order follows the display sort` |

### `hostPressure(host)` → `{cpu, mem, gpu, load, verdict}`
| 시나리오 | 기대 | 테스트 |
|---|---|---|
| main 표본 | `{21, 58, 13, "3.5 / 10", fine}` | `main sample → fine …` |
| cpu 90 · 85 · 84 · mem 23e9(89%) · gpu 85 | busy(cpu 90) · busy · fine · busy(mem 89) · busy | `85% or more on any value → busy` |
| gpu null · gpu.util null · cpu null · memory null | partial, 그 값 null | `any value null → partial` |
| cpu 90 + gpu null | busy | `busy wins over partial` |
| cpu·memory·gpu 전부 null | unknown | `all of cpu, memory, gpu null → unknown` |
| load null · cores null | load `—` · `3.5 / —`, verdict 는 fine 그대로 | `load or cores unknown → …` |
| null · undefined · empty 픽스처 hosts[0] | no_sample | `no sample → no_sample` |

### `queueHeader` · `sortQueue` · `workerPills` · `headerNote`
| 함수 | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| queueHeader | main · #412 cancelling · empty · single-lane · errors | `5 jobs · 2 running · 3 waiting · oldest waiting 1m 35s · lanes 2/2 busy` · 같음 · `0 jobs · 0 running · 0 waiting · lanes 0/2 busy` · `2 jobs · 1 running · 1 waiting · oldest waiting 40s · lanes 1/1 busy` · `unknown` | 5 |
| sortQueue | `[415 414 409 413 412]` · #412 cancelling `[412 415 414 413 409]` · 입력 불변 · null | `[412 409 413 414 415]` · `[409 412 413 414 415]` · 원본 그대로 · `[]` | 4 |
| workerPills(text·cls 만 비교) | single-lane · single idle · main · empty · paused-down · single + paused | `[worker busy #412/busy]` · `[worker idle/idle]` · `[#412/busy, #409/busy]` · `[lane 1 · idle, lane 2 · idle]` · `[lane 1 · down/down, #409/busy, paused/paused]` · `[worker busy #412, paused]` | 6 |
| headerNote(status, nowMs, prev) | prev null · 같음/uptime 증가 · uptime 3012→30 · schema 1→2 · version 0.1.0→0.2.0 · 둘 다 · uptime null | null · null · `Server restarted at 09:51 — running jobs were marked lost` · `UI out of date — reload` ×3 · null | 7 |

## `tests/web/progress.test.js` — 32

| 함수 | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| progressHead | #412 · partial · #409 · failed_step format + ok false · 두 개 · steps [] · materializing · null/undefined · job null | `step 5/8 · test · 51s · job 59s` · `step 5/8 (so far) · …` · `step 3/3 (so far) · boot-simulators · 3m 20s · job 6m 20s` · `… · 1 step failed` · `… · 2 steps failed` · `no step markers · job 59s` · null · null · `… · job —` | 8 |
| stepMark | done ok true/null · running · ok false · pending/null/undefined | ✔ · ▶ · ✘ · · | 4 |
| recentLine → `{pill, duration, when, summary}` | #411 · #410 · #408 · #408 summary null · cancelled_by macmini-admin · #407 · #406 · #403 · #405 · null | `failed · exit 1`/`1m 02s`/`09:47`/`2 tests failed · step test` · `succeeded`/`5m 50s`/`09:40`/`all 9 packages green` · `cancelled · exit 2`/`—`/`09:31`/`before start`+`by carol@mbp` 포함 · 같음 · `by macmini-admin` 포함 · `timed out · exit 2`/`20m 00s`/`09:22`/`limit 20m · step test` · `lost · exit 3`/`—`/`09:02`/`server restarted 09:02` 포함 · `failed`/`—`/`Sep 3 · 23:40`/`tar rejected: …` · `succeeded`/`08:31`/`""` · duration·when `—` | 10 |
| rerunCommand | #411 · #407 · inputs {} / null / 없음 · bool · 셋 섞음 · null · preset 없음 | `rcm run gate -f scope=fast` · `rcm run gate -f scope=full` · `rcm run gate` · `rcm run gate -f verbose=true` · `rcm run gate -f scope=fast -f verbose=true -f n=3` · `—` · `—` | 4 |
| transitionsLine(job, tz) | #411 · §2 예시 · #407 · #408 · #403 · [] / null / job null | `uploading 09:45:40 → queued (waited 21s) → running 09:46:01 → failed 09:47:03 · exit 1` · `uploading 09:50:40 → queued (waited 21s) → running 09:51:13 → failed 09:52:15 · exit 1` · `uploading 09:02:15 → queued (waited 15s) → running 09:02:30 → timed out 09:22:30 · exit 2` · `uploading 09:30:40 → queued 09:30:40 → cancelled 09:31:00 · exit 2` · `uploading 23:38:00 → failed 23:40:00` · `—` | 6 |

## `tests/web/connection.test.js` — 19

시작 상태 `LIVE = {mode: live, attempt: 0, lastOkAt: NOW, sseOpen: true}`. `step(state, event, sec)` = `connection(state, event, NOW + sec·1000)`.

| 시나리오 | 기대 | 테스트 |
|---|---|---|
| nextBackoff 0..6 | `2 4 8 16 30 30 30` | 1 |
| live → sse_error | polling · attempt 1 · sseOpen false · lastOkAt 유지 · `nextBackoff(0) === 2` | 1 |
| sse_error ×6 | attempt 1..6 · `nextBackoff(이전 attempt)` = `2 4 8 16 30 30` | 1 |
| polling(attempt 3) → sse_open | live · attempt 0 · sseOpen true | 1 |
| tick +30s · +31s | polling 유지 · lost(lastOkAt 유지) | 2 |
| live 에서 tick +31s | lost | 1 |
| lost → status_ok(sseOpen false / true) | polling / live · lastOkAt = now | 2 |
| polling → status_ok | polling 유지 · attempt 유지 · lastOkAt 갱신 | 1 |
| 전체 순환 | sse_error → polling → tick 31s → lost → status_ok → polling → sse_open → live(attempt 0) | 1 |
| manual_pause/resume from live · from polling | paused → live · paused → polling | 2 |
| paused 중 tick +100s · status_ok | paused 유지 · lastOkAt 갱신 + paused 유지 | 2 |
| hidden_60s / visible (live · polling) | paused → live · paused → polling | 1 |
| 모르는 이벤트 · 불변성 · prev null + status_ok | 계약 4필드 불변 · 새 객체 + prev 불변 · `{polling, 0, NOW, false}` | 3 |

## 결정한 모호점 (구현과 어긋나면 여기부터 본다)

1. **`fmtClock` 은 분 절삭**(render_text `%H:%M` 과 동일). 목업의 `09:55`(#409)·`10:05`(#415)는 반올림한 값이라 절삭하면 `09:54`·`10:04` — 이 둘의 clock 은 시험하지 않고, #413 은 `finish_at` 을 `01:04:12Z` 로 둬 `~10:04` 가 절삭으로도 나오게 했다(wait 180 > blocker remaining 160 — 서버가 여유를 둔 값으로 본다). `nowMs` 를 안 주면 날짜 접두 없이 항상 `HH:MM`. 파싱 실패는 `—`.
2. **`fmtAgo` 의 분·시는 내림**(179 → `2m ago`, 3600 → `1h ago`). 초 단위의 반올림·내림은 정수만 시험해 열어 뒀다. `fmtCountdown` 은 거칠지 않다 — `in ` + `fmtDuration`(§2 예 `in 2m 40s`), **≤ 0 → `now`**.
3. **`fmtBytes` 는 십진**(1e6 · 1e9)이고 **GB 경계는 500 MB**(`≥ 5e8` → 소수 한 자리 GB): 48213344 → `48 MB`, 594411520 → `0.6 GB`, 500000000 → `0.5 GB`. 1e3 미만 `B`, 1e6 미만 `KB`. 목업 호스트 카드의 `14.0 / 24 GB` 는 GiB(2^30) 눈금이고 render_text `_gb` 도 GiB 로 바뀌었다 — 그 표기는 `fmtBytes` 계약 밖이다(구현은 별도 `fmtMemory`).
4. **`fmtMb` 는 MB 입력**(`top[].rss_mb` → `500 MB`). `uploading · 30 / 48 MB` 는 바이트를 1e6 으로 나눈 정수 두 개에 단위를 한 번만 붙인다(목업 11·31 문구 그대로 — `30 MB / 48 MB` 가 아니다).
5. `ordinal(null)` · `truncate(null)` → `—`(§2 「모르는 값은 —」). `truncate` 는 `length` 기준이라 ASCII 만 시험, 기본 40, 넘치면 39자 + `…` = 40자. `stateWord(null)` → `unknown`, `stateGlyph(null)` → `·`(render_text 큐 행의 기본 글리프와 같음). `esc(null)` → `""`(— 처리는 호출자 몫), `'` 는 `&#39;`/`&#x27;` 둘 다 허용.
6. **`reasonText` 의 세 번째 인자는 `status` 전체**(§2 「busy 수는 status.server.workers 로 센다」). 없으면 `2/2 busy` 조각만 생략한다. busy 분모는 `server.lanes`(없으면 workers 수). 워커가 down 인 경우의 분모(살아 있는 레인 수인지)는 시험하지 않았다.
7. **조각은 남기고 모르는 숫자만 `—`**: `frees in —` · `kill —` · `upload stalled — · …` · `— / 48 MB`(render_text 와 같은 원칙). 예외로 `stuck` 의 `N× expected` 는 elapsed·expected 가 있을 때만, `no output for …` 는 `progress.last_output_at` 이 있을 때만 붙는다(둘 다 없으면 `⚠ likely stuck`). 배수는 내림(1150/369 → `3×`).
8. **거친 시간**: `upload stalled 2m` · `no output for 4m` 은 `fmtAgo` 와 같은 눈금(`ago` 없이) — 목업 31·34 그대로, `2m 00s`·`4m 10s` 가 아니다.
9. **사람 이름**: 서버는 `cancel.by`·`cancelled_by` 에 **토큰 이름**을 쓴다(`server.py cancel()` → `token.name`). 목업은 라벨(`alice@laptop`·`carol@mbp`)을 보이므로 `requester.name`·`joiners[].name` 과 같으면 그 `label`, 아니면 이름 그대로(`macmini-admin`·`server`).
10. `materializing`: tree → `preparing workspace · unpacking <fmtBytes(bytes)>`, git_ref → `preparing workspace · fetching <ref>`(목업 33), 크기·ref 모름 → `preparing workspace`.
11. **`actionable` 은 `model.ACTIONABLE_REASONS` 7개만**. `uploading`·`materializing`·`cancelling` 은 false — 목업 CSS 의 `.reason.act` 는 「회색이 아닌 글자」 스타일이지 이 플래그가 아니다.
12. 모르는 이유(문자열·null·필드 없음·row null) → `unknown`, actionable false(부모 지시. render_text 는 raw 문자열을 보여주는데 웹은 안 그런다). `links` 는 §2 대로 `[{jobId}]`.
13. `confidenceBadge` 는 서버 `estimate.confidence` 를 그대로(결정 G). null → `{cls: "low", text: "low · —"}`. `confidence` 필드가 없는 옛 서버는 시험하지 않았다(결정 G 는 「계산하지 않는다」, 구현은 source/n 으로 되살린다 — 열어 둠).
14. `etaText`: **`finish_at` null 이면 무조건 `{clock: "—", rel: null}`**(§2 리터럴, blocked 라도). blocked → `after #<blocker>` + `~<clock>`. rel 은 running·cancelling 이면 `remaining`, 대기면 `wait + remaining`(render_text 와 같음) — 픽스처는 `finish_at − now` 로 계산해도 같은 값이 나오게 맞췄다.
15. `elapsedText`: `waited_seconds` 0·null 이면 sub 없음(render_text 의 truthy 검사와 같음). cancelling 은 running 과 같다. uploading 은 `—`. 반환 sub 는 없거나 null 이면 된다(`!r.sub` 로 검사).
16. `notMoving`: 정렬은 `ACTIONABLE_REASONS` 순, 같은 이유 안에서는 표시 정렬(`sortQueue`) 순(픽스처에선 입력 순과 같다). **줄 수 제한 없음**(§24 는 자르지 않는다). `text` 는 `reasonText(row, generated_at, status).text`(id 없음) — `includes` 로만 검사한다. `reason` 이 하나라도 없으면 `ok` 가 아니다(전부 없으면 `unknown`). `queue: []` 는 `ok`. `me` 는 결과를 바꾸지 않는다. `kind: "lost"` 는 연결 상태라 렌더 층이 정한다(시험 안 함).
17. `yourJobs`: 반환은 `{kind: "list", lines: [{jobId, text}], more}`. **`text` 에 `#412` 를 넣지 않는다**(id 는 별도 버튼 — 목업 `<b>#412</b> running …`). running 줄 `running · ETA 09:57 · in 5m 10s · +1 joined`, 대기 줄 `2nd in line · ETA 09:58 · waiting for lane`(목업 23 리터럴 — rel 없이 짧은 이유). 시간대는 `status.display_timezone`, now 는 `generated_at`(시그니처가 `(status, me)` 라 다른 데서 올 수 없다). 정렬은 `sortQueue`, uploading 도 활성, `requester.name`/`joiners[].name` 기준(라벨 아님), 셋 이상이면 두 줄 + `more`.
18. `hostPressure`: cpu·mem·gpu 는 **정수 반올림**(4절 「퍼센트 정수」), `load` 는 `"3.5 / 10"`(load[0] 소수 한 자리 / cores). busy 는 반올림 뒤 ≥ 85. **busy 가 partial 보다 우선**(아는 값이 이미 바쁘다고 말한다). 셋 다 null → unknown. **load·cores 는 판정에 안 들어가고** 텍스트만 `—`/`3.5 / —`. host 없음 → `{verdict: "no_sample"}`.
19. `queueHeader`: cancelling 은 running 으로 센다. `oldest waiting` 은 대기 잡 `estimate.waited_seconds` 최댓값, 대기가 0이면 조각 생략. `queue: null` → `"unknown"`. `1 job` 단수형은 시험하지 않았다(single-lane 픽스처를 2 잡으로 둠).
20. `sortQueue`: running·cancelling 은 **lane 오름차순**(목업 표와 머리 필이 #412 → #409 — id 순이면 #409 가 먼저다), 대기는 position, 동률은 id. 입력 배열을 바꾸지 않는다. null → `[]`.
21. `workerPills`: 계약은 `text`·`cls` 만(구현이 `jobId`·`lane` 을 더 실어도 된다). lanes 1 → `worker busy #412` / `worker idle`; 여러 레인 → busy `#412`, idle `lane 2 · idle`, down `lane 1 · down`; paused 는 마지막에. 단일 레인 down 은 시험하지 않았다.
22. `headerNote(status, nowMs, prev)` 는 **문자열 | null**(§2). 재시작 시각 = `generated_at − uptime_seconds`(display_timezone), 버전·스키마 변화가 재시작보다 우선, uptime 이 어느 쪽이든 null 이면 재시작을 주장하지 않는다, prev 없으면 null.
23. `progressHead`: 실패 수 = `steps[].ok === false` 수(없고 `failed_step` 만 있으면 1) → `1 step failed` / `2 steps failed`. partial 은 `5/8 (so far)`. `job_seconds` null → `job —`. materializing · null → null. 머리에 `waited` 는 없다(§2 — prog 에 없는 값).
24. `stepMark`: `ok === false` 가 running 보다 우선 ✘, running ▶, done ✔(ok null 포함), 그 밖·null·undefined → `·`.
25. `recentLine` 필드는 `{pill, duration, when, summary}`(key·requester 는 렌더 층이 job 에서 읽는다). succeeded 는 exit 를 안 붙인다. `exit_code` null → `failed`. duration 은 `job_seconds` 만 쓴다(시각으로 계산하지 않는다 — #406 lost 는 null 이라 `—`). summary = `[서버 summary] · [before start: cancelled 이고 started_at null] · [by <label>] · [step <failed_step>]`, 아무것도 없으면 `""`. 서버는 시작 전 취소에 `cancelled before start` 를 쓰므로 `before start`·`by carol@mbp` 는 `includes` 로만 본다. lost 는 서버 summary 그대로 — 실제 서버는 `server restarted 2026-09-04 00:02:00Z`(UTC 전체 스탬프)를 쓰고 목업은 `09:02` 다(픽스처는 목업 문구, `includes` 검사). **오너 결정 필요**: 서버가 표시 시간대 `HH:MM` 을 쓰든 UI 가 다시 그리든.
26. `rerunCommand`: `-f k=v` 를 삽입 순서로, bool 은 `true`/`false`. `null`·preset 없음 → `—`(빈 명령을 복사시키지 않는다). 공백 든 값의 따옴표, git_ref 의 `--source/--ref` 는 시험하지 않았다.
27. `transitionsLine(job, tz)`: 시각은 `HH:MM:SS`(display tz, 날짜 접두 없음). queued 바로 다음이 running 이면 `queued (waited N)` = `running.at − queued.at`(픽스처는 `waited_seconds` 와 같게 맞춤 — 어느 쪽을 써도 같다), 아니면 `queued HH:MM:SS`. 상태 단어는 `stateWord`(`timed out`). `exit_code` null 이면 접미 없음. 전이 없음·null → `—`. §2 예시 문자열은 픽스처(#411 은 09:45~09:47)와 시각이 달라 별도 인라인 케이스로 잠갔다(§2 의 `waited 21s` 가 맞도록 queued 09:50:52).
28. `connection`: 계약 4필드(`mode attempt lastOkAt sseOpen`)만 비교한다 — 구현이 `before`·`retryIn` 을 더 둬도 된다. 백오프는 `nextBackoff(이전 attempt)`(sse_error 가 attempt 를 올린다). lost 는 `now − lastOkAt > 30s`(정확히 30s 는 아직). paused 중 `status_ok` 는 `lastOkAt` 만 갱신. resume·visible 은 `sseOpen` 에 따라 live/polling(manual_pause 뒤 visible 은 시험 안 함). `prev` null + `status_ok` → `{polling, 0, now, false}`. 함수는 새 객체를 돌려주고 prev 를 바꾸지 않는다.
29. 픽스처 시간 정합: 서버 재시작 00:02:00Z(uptime 3012, #406 lost) 뒤에 #407 이 시작하므로 timed out 행은 목업의 `09:20` 이 아니라 `09:22`. `progress.steps_total_partial` 이면 `steps_total` 은 지금까지 센 수(#409 3/3).

## 현재 `app.js`(feat/m2 작업본) 와 어긋나는 곳 — 27 케이스

테스트를 실제 `src/remote_ci_monitor/web/app.js` 에 돌리면 167/194. 나머지 27 은 전부 위 결정과 목업·§2 문구의 차이다. 남은 케이스는 스펙이 이기는 쪽으로 뒀다.

| 함수 | 구현 | 테스트(스펙 근거) |
|---|---|---|
| `truncate(null)` | `""` | `—` (§2 「모르는 값은 —」) |
| `reasonText` 3번째 인자 | `(row, status, nowMs)`/`(row, nowMs, status)` 둘 다 받음 — 통과 | — |
| `reasonText` links | `[412]` 숫자 배열 | `[{jobId: 412}]` (§2) |
| uploading · upload_stalled | `30 MB / 48 MB`, `upload stalled 2m 00s`, 모름 `upload stalled —`(통과) | `30 / 48 MB`, `upload stalled 2m` (목업 11·31) |
| stuck | `no output for 4m 10s` | `no output for 4m` (목업 34) |
| blocked remaining null | `frees in` 조각 생략 | `frees in —` (결정 7) |
| materializing git_ref | `preparing workspace` | `preparing workspace · fetching dev` (목업 33) |
| cancelling by | 토큰 이름 `alice-laptop` | 라벨 `alice@laptop` (목업 30; recentLine 은 이미 라벨로 바꾼다) |
| `yourJobs` text | `#412 running · …`, 대기 줄에 `in 6m 15s` + 이유 전체; `(status, me, tz, nowMs)` 라 tz 를 안 주면 브라우저 시간대 | id 없음, `2nd in line · ETA 09:58 · waiting for lane`, tz 는 `display_timezone` (목업 23 · §2 시그니처). **주의**: 이 Mac 은 KST 라 시간대 문제는 여기서 안 드러나고 CI(UTC)에서 드러난다 |
| `hostPressure` | 원값(89.25…, load 3.48 · cores 별도) | 정수 반올림 · `"3.5 / 10"` (4절 · §2) |
| `sortQueue` | running 을 position→id 순(#409 먼저) · null 이면 throw | lane 순(#412 먼저, 목업) · `[]` |
| `rerunCommand(null)` | throw | `—` (§2 「null 입력을 견딘다」) |
| `transitionsLine` | `timed_out 09:22:30` | `timed out 09:22:30` (`stateWord`) |
| `headerNote` | `(status, prev, tz)` · `{kind, text}` 반환 | `(status, nowMs, prev)` · 문자열 (§2) |
