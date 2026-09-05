# M2 작업 명세 — 웹 UI (2026-09-05)

정본은 `docs/wireframes/web-queue.html`(기획 항목 35개 · 「4. 이 화면이 정한 규칙」)과 PLAN.md 「웹 UI (M2)」·「/api/status 스키마 v1」·「fail-open 금지」다. 이 문서는 그것을 **파일 · 함수 · DOM 계약 · 상태기계 · 테스트**로 쪼갠 것이다. 어긋나면 목업·PLAN 이 이긴다.

서버 쪽은 M1 에서 다 준비됐다: `GET /events`(hello · keep-alive · `reset`/`lag` · 503 폴백), `POST /api/eta`, `estimate.confidence`, `hosts[]`·`history[]`, `GET /jobs/{id}?tail=N`, `GET /jobs/{id}/log?offset=`, `GET /api/whoami`, `POST /jobs/{id}/cancel`.

## 0. 결정 (추천값으로 진행, 오너가 바꾸면 여기서 고친다)

| # | 항목 | 결정 |
|---|---|---|
| A | 파일 | `src/remote_ci_monitor/web/index.html` · `app.js` · `style.css` 세 개. 빌드 도구·번들러·외부 CDN 없음(폰트도 시스템 스택). 서버가 `GET /` 로 `index.html`, `GET /static/app.js`·`/static/style.css` 로 나머지를 준다. `read_auth` 적용, 그 밖의 `/static/*` 는 404 |
| B | JS 구조 | `app.js` 하나. 순수 함수(표기·문구·요약 계산·상태기계 전이)는 `window.rcm` 에 노출하고, 파일 끝에서 `typeof document !== "undefined"` 일 때만 `boot()`. Node 에서 `require()` 하면 `module.exports = rcm` 이 되어 **순수 함수를 `node --test` 로 검사**한다(런타임 의존성 0 은 패키지 얘기, 개발 도구는 node 허용 — CI 러너에 있다) |
| C | 렌더 | 상태 → HTML 문자열 → 섹션별 `innerHTML` 교체(요약 · 큐 · 호스트 · 최근 · Estimates). 1초 틱은 `[data-tick]` 요소만 텍스트 갱신. 펼침·서랍·다이얼로그 상태는 DOM 교체와 무관하게 `state` 에 둔다. 가상 DOM 없음 |
| D | 갱신 | 처음 `GET /api/status` → `EventSource("/events")`. 잡·호스트·서버 이벤트가 오면 `GET /api/status` 재조회(300ms 합침). `reset`/`lag` 도 재조회. **`EventSource` 는 503 본문·`Retry-After` 를 JS 에서 읽을 수 없다**(Codex M2 리뷰 3) — `onerror` 하나로 처리: 즉시 `polling`, 10초 폴링, SSE 재접속은 2→30s 타이머로 직접 다시 만든다. **30초 넘게 성공 응답이 없으면 `Lost connection`** — 화면은 dim 하지 않고 띠 + 나이만(dim 은 호스트 stale 에만) |
| E | 시계 | 응답마다 `skew = Date.parse(generated_at) − Date.now()`. 모든 상대 시간은 `now = Date.now() + skew`. \|skew\| > 30s 면 `clock ±Nm` 칩 |
| F | 토큰 | `localStorage["rcm.token"]`. 저장·삭제는 `storage` 이벤트로 탭 간 동기화. `GET /api/whoami` 로 검증; 401/403 만 지운다, 네트워크 오류는 `couldn't verify — kept`. 토큰이 있으면 `/api/status` 와 `/jobs/{id}` 를 `Authorization` 헤더와 함께 부른다(그래야 `log_tail` 이 온다). 모든 라우트의 401/403 은 같은 `tokenRejected()` 경로. 토큰 없이 `/api/status` 가 401 이면(read_auth) 버튼은 `Read auth required`. XSS 대비 CSP(`default-src 'none'; script-src 'self'; …; frame-ancestors 'none'`) 를 `index.html` 응답에 붙이고 인라인 스크립트·스타일 금지. localStorage 토큰의 위험은 README 에 명시(공용 브라우저 금지) — 오너 확인 대기(PLAN 결정 22) |
| G | 신뢰도·이유 | 서버가 준 `estimate.confidence` 와 `reason` 을 그대로 그린다. UI 는 계산하지 않는다(어긋남 방지) |
| H | 첫 PR 범위 | **PR A**(`feat/m2-ui`): 정적 서빙 + 화면 전부(1~35) 중 토큰이 필요 없는 것 + 갱신 상태기계 + 모바일 + 다크/라이트 + 토큰 입력(4·29) + 내 잡(23) + 로그 tail(13 의 tail) + 로그 서랍 + 취소 다이얼로그(13·30). 즉 M2 전부를 한 PR 로 하되 커밋은 섹션별로 자른다 |

## 1. 정적 서빙 (`server.py`)

| 라우트 | 인증 | 동작 |
|---|---|---|
| `GET /` | `read_auth` | `web/index.html`, `text/html; charset=utf-8`, `Cache-Control: no-cache`, `X-Content-Type-Options: nosniff` |
| `GET /static/app.js` · `/static/style.css` | `read_auth` | 그 파일. `application/javascript` · `text/css`. `ETag`(sha256 앞 16자) + `If-None-Match` → 304 |
| `GET /static/<그 밖>` | | 404. 경로에 `..`·`//`·`\` 는 400(이미 있음) |

파일은 `importlib.resources.files("remote_ci_monitor") / "web"` 에서 읽는다(wheel 안에 들어간다 — `pyproject` 의 hatch `packages` 가 `src/remote_ci_monitor` 전체를 포함하므로 추가 설정 없음. 테스트가 wheel 을 만들어 확인한다). `HEAD` 도 된다. 파일이 없으면 500 이 아니라 404 + `web assets missing` (fail-open 아님: 없는 걸 없다고).

## 2. `app.js` 순수 함수 (`window.rcm` / `module.exports`)

```js
rcm.fmtDuration(seconds)            // null → "—" · 12 → "12s" · 310 → "5m 10s" · 3720 → "1h 02m" (render_text 와 동일)
rcm.fmtClock(iso, tz, nowMs)        // "09:57" · 오늘 아니면 "Sep 3 · 23:40" · null → "—". tz = display_timezone 또는 undefined(브라우저)
rcm.fmtAgo(seconds)                 // 60 미만 "4s ago", 이상 "3m ago" (거칠게)
rcm.fmtCountdown(seconds)           // "in 8s" / "in 2m 40s"
rcm.fmtBytes(n)                     // 48213344 → "48 MB" · 594411520 → "0.6 GB"(GB 소수 한 자리) · null → "—"
rcm.fmtMb(n)                        // MB 정수 "500 MB"
rcm.ordinal(n)                      // 1 → "1st" · 2 → "2nd" · 3 → "3rd" · 11 → "11th" · 22 → "22nd"
rcm.truncate(label, 40)             // 40자 넘으면 "…"
rcm.stateWord(state)                // "timed_out" → "timed out", 나머지 그대로
rcm.stateGlyph(state)               // running "▶" · queued "·" · uploading "↑" · cancelling "■" · succeeded "✓" · failed "✗" · timed_out "⏱" · cancelled "■" · lost "?"
rcm.reasonText(row, nowMs)          // 항목 11 문구: "running · lane 1" · "waiting for lane · 2/2 busy · behind #412 · frees in 5m 10s" ·
                                    //   "⛓ blocked by #409 · devices · frees in 2m 40s" · "uploading · 30 / 48 MB" · "upload stalled 2m · 30 / 48 MB" ·
                                    //   "preparing workspace · unpacking 48 MB" · "over by 3m 31s · expected 6m 09s" · "⚠ likely stuck · 3× expected · no output for 4m" ·
                                    //   "SIGTERM sent by alice@laptop · kill in 8s" · "paused" · "not scheduled" · "no worker"
                                    //   → {text, actionable: bool, links: [{jobId}]}  (busy 수는 status.server.workers 로 센다)
rcm.confidenceBadge(est)            // {cls: "high"|"med"|"low"|"over", text: "high · measured n=7" | "med · measured n=3" | "low · preset" | "low · default" | "low · group wait" | "overdue"}
rcm.etaText(row, tz, nowMs)         // {clock: "09:57" | "after #409", rel: "in 5m 10s" | "~10:04" | null} ; finish_at null → clock "—"
rcm.elapsedText(row, nowMs)         // running: {main: "59s", sub: "waited 33s"} · queued: {main: "waiting 1m 35s"} · uploading: {main: "—"}
rcm.notMoving(status, me)           // 항목 24: {kind: "ok"|"list"|"unknown"|"lost", lines: [{jobId, text, reason}]} — 순서 worker_down → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused.
                                    //   queue null → unknown. reason 필드 없는 옛 서버 → unknown. 정상 대기·스텝 실패 후 계속 도는 잡은 제외.
rcm.yourJobs(status, me)            // 항목 23: me(토큰 이름) 가 requester.name 이거나 joiners[].name 인 활성 잡 → [{jobId, text}] 최대 2 + more 수. me 없음 → {kind:"no_token"} · 없음 → {kind:"none"} · queue null → {kind:"unknown"}
rcm.hostPressure(host)              // 항목 25: {cpu, mem, gpu, load, verdict: "fine"|"busy"|"partial"|"unknown"} — 85% 이상 busy, 하나라도 null partial, 전부 null unknown. host 없음 → {verdict: "no_sample"}
rcm.queueHeader(status, nowMs)      // 항목 5: "5 jobs · 2 running · 3 waiting · oldest waiting 1m 35s · lanes 2/2 busy"
rcm.sortQueue(rows)                 // running → cancelling → 대기(position 순)
rcm.progressHead(prog)              // 항목 12: "step 5/8 · test · 51s · job 59s" · partial → "step 5/8 (so far)" · 실패 스텝 있으면 " · 1 step failed" · 마커 없음 → "no step markers · job 59s" · materializing → null(Reason 한 줄로)
rcm.stepMark(step)                  // "✔" | "▶" | "✘"(ok===false) | "·"(pending — 목록에는 done/running 만 있으니 pend 는 declared total 로 채운다)
rcm.recentLine(job, tz, nowMs)      // 항목 14: pill 텍스트 "failed · exit 1" · "cancelled · exit 2" · "lost · exit 3" · exit_code null → "failed"; 소요 "1m 02s" 또는 "—"; 요약 "2 tests failed · step test"; lost → "server restarted 09:02"; 시작 전 취소 → "before start · by carol@mbp"
rcm.rerunCommand(job)               // "rcm run gate -f scope=fast" (inputs 를 -f 로)
rcm.transitionsLine(job, tz)        // "uploading 09:50:40 → queued (waited 21s) → running 09:51:13 → failed 09:52:15 · exit 1"
rcm.connection(prev, event, nowMs)  // 갱신 상태기계(3절). 순수: (state, event) → state
rcm.nextBackoff(attempt)            // 2, 4, 8, 16, 30, 30 … (초)
rcm.workerPills(server)             // 항목 1: lanes 1 → [{text: "worker busy #412", cls}] 하나; 그 밖엔 레인마다. down → {text: "lane 1 · down", cls:"down"}; paused → 추가 {text:"paused", cls:"paused"}
rcm.headerNote(status, nowMs, prev) // uptime 감소 → "Server restarted at 09:58 — running jobs were marked lost"; schema/version 변화 → "UI out of date — reload"
```

모든 함수는 null 입력을 견딘다: 모르는 값은 `—`, 절대 0 이나 빈 문자열로 그리지 않는다(fail-open 금지). 문자열 삽입은 `rcm.esc()` 로 escape.

## 3. 갱신 상태기계 (`rcm.connection`)

상태: `{mode: "live"|"polling"|"lost"|"paused", attempt, lastOkAt, sseOpen}`.

| 이벤트 | 전이 |
|---|---|
| `status_ok`(응답 성공) | `lastOkAt = now`; mode 가 lost 였으면 → live/polling(sseOpen 에 따라), 전체 재조회 |
| `sse_open` | `sseOpen = true, attempt = 0`, mode live |
| `sse_error`(onerror · 503) | `sseOpen = false`, mode polling, 다음 재접속 `nextBackoff(attempt++)` 초 뒤, 폴링 타이머 10s 시작 |
| `tick`(1초) | `now − lastOkAt > 30s` 이고 mode ≠ paused → lost |
| `manual_pause` / `manual_resume` | paused ↔ 이전 mode(재개 시 전체 재조회) |
| `hidden_60s` / `visible` | paused ↔ 재조회 |

띠(항목 18): lost 면 맨 위 `role="alert"` 황토 띠 `Lost connection to <host> · last update 42s ago · showing last known state`, 아래 `reconnecting in 8s…`. 화면은 dim 하지 않고 나이만 계속 올라간다.

## 4. DOM 계약 (테스트가 잡는 id · class · data 속성)

```
header:   #hdr  .wordmark  .host  [data-workers] .wk(.busy|.down|.paused)  #live-btn(.paused)  #tok-btn(.bad)  .errchip  .skew  .banner.restart
summary:  #summary  [data-c="23"] .sum-yours  [data-c="24"] .sum-stuck (.ok|.list|.unknown)  [data-c="25"] .sum-host
queue:    #queue  .queue-header  table.q  tr[data-job="412"](.mine .overdue .exp)  td.job .id .pill.<state> .pos  td.key .key .chip  td.requester .you .joiners
          td.reason .reason(.act) .blocked .stalled .stuck  td.elapsed  td.eta .eta .conf.(high|med|low|over)  td.source .sha .uncommitted
          tr.expanded[data-job] .prog .head .minibar .steps .step(.run|.pend|.fail)  .tail  .actions button.log button.cancel  [data-more]
          .empty (빈 큐)  .banner.bad[data-error="queue"] (조회 실패)
host:     #host  .hostcard(.dim)  .meter[data-metric="cpu|mem|gpu"]  meter  .spark svg  .stale-badge  .top  .banner.bad[data-error="hosts"]
recent:   #recent  .rrow[data-job]  .pill  .rerun  details.est  .banner.bad[data-error="recent"]  .empty
overlays: #banner-lost(role=alert)  dialog#tok-dialog  dialog#cancel-dialog  #drawer(role=dialog)  #toast
```

- 시각 표기: `<time datetime="...">09:57</time>`. 1초 틱 요소는 `data-tick="elapsed|waiting|age|countdown" data-from="<iso>"`.
- 접근성(목업 4절): 글리프 `aria-hidden`, 필 텍스트만 읽힘, 띠 `role="alert"`, 진행 막대 `role="progressbar"` + `aria-valuetext="step 5 of at least 8"`, 호스트 막대 `<meter>`, 잡 id·칩·sha·펼침·Log·Cancel·show more·live 토글은 전부 `<button>`, `aria-live="polite"` 는 상태 전이 영역(`#toast`)만.
- 딥링크 `#/jobs/<id>`: 그 행 스크롤 + 강조 + 펼침(토큰 있으면 서랍). 큐에 없으면 최근으로, 거기도 없으면 토스트 `#409 finished 09:55 · succeeded`(`GET /jobs/{id}` 로 확인).
- 접힘 상태: `localStorage["rcm.collapsed"]` = 잡 id 배열, 매 갱신 때 큐에 없는 id 정리. 실행 중 행은 기본 펼침(오너 결정 13).
- 레인 1 이면 워커 필 하나(오너 결정 12). 최근 완료는 `recent_count`(8) 중 5 + `show N more`(오너 결정 14).
- 토큰(오너 결정 15): `#tok-btn` → `dialog#tok-dialog`(붙여넣기 · Enter 제출 · Escape 닫기 · `checking…`) → `GET /api/whoami`. 성공 → 버튼 `🔑 <name>`, 23·13 활성. 401/403 → 버튼 빨강 `Token rejected`, 저장값 삭제. 네트워크 오류 → `couldn't verify — kept`.
- 로그 tail(13): 토큰 있고 running/cancelling 이면 `/api/status` 의 `log_tail` 3~5줄을 `.tail` 에. `Log` 버튼 → 서랍: `GET /jobs/{id}/log?offset=` 증분 2초, 실패 스텝 마커 줄로 스크롤, 해시 `#/jobs/<id>/log`(뒤로가기 = 닫기). `Cancel` → `dialog#cancel-dialog`(제목 `Cancel #412 gate:full (alice@laptop)?`, running 문구 / queued 문구, 합류 세션 수) → `POST /jobs/{id}/cancel` → 행 dim + `cancel requested…`, 5초 안에 이벤트 없으면 재조회. 합류자면 `left` 응답 → 토스트 `left the join list`.
- 모바일(< 720px): 큐 행을 카드 3줄로(항목 21), 머리는 `2 busy` · 점 · 아이콘, 요약 세 칸 세로, 스텝 1열, tail 가로 스크롤, 최근 완료 2줄 카드. 720~960px 은 Source 열을 펼침 블록으로.
- 다크/라이트: `prefers-color-scheme` + 목업의 토큰 팔레트 그대로(`:root` 라이트 · 미디어쿼리 다크).

## 5. 변형 상태 (목업 2절 19개 → 언제 어떻게)

| # | 조건 | 표시 |
|---|---|---|
| 17 | `queue: []` | 점선 상자 `Queue is empty — rcm run <preset> starts immediately.` + 프리셋 목록. 정지·전 레인 다운이면 `Queue is empty but paused — nothing will start` |
| 18 | 30초 무응답 | 맨 위 황토 띠(3절) |
| 19 | `hosts[0].stale` 또는 클라이언트 외삽 `age > 3×interval_seconds` | 호스트 카드 dim + `stale 3m` 빗금 배지 + 막대 회색 `last known` |
| 20 | `estimate.overdue` | 행 왼쪽 굵은 황토 바(내 잡이면 파란 선과 겹쳐), Reason `over by …`, ETA `—` + `overdue` 배지, 뒤 잡 툴팁 `waiting on an overdue job` |
| 22 | `queue: null` | 표 자리에 빨간 띠 `Queue unavailable — <queue_error>`(`role="alert"`), 요약 23·24 는 `unknown — queue unavailable` |
| 26 | `medians: {}` | Estimates 펼침 + `no samples yet — using preset/default until 2 successful jobs per key`, ETA 들은 `low · default` |
| 27 | `workers[].state == down` | 필 `lane 1 · down` + `error · <60자>` 칩 + 빨간 띠 `Worker on lane 1 stopped: <error> · waiting jobs use lane 2 only`. 전부 다운 → 대기 행 Reason `no worker`, ETA `—` |
| 28 | `server.paused` | 필 `paused`, 띠 `Queue paused by <by> at <at> — running jobs finish, nothing new starts · rcm resume`, 대기 행 Reason `paused`, ETA `—` |
| 29 | whoami 401/403 | 토큰 버튼 빨강, tail 숨김, Log/Cancel 비활성, 23 은 `Add a token to highlight your jobs` |
| 30 | `state == cancelling` | 필 `■ cancelling…`, Reason `SIGTERM sent by <cancel.by> · kill in <kill_at 카운트다운>`, Cancel 비활성 |
| 31 | `reason == upload_stalled` | Reason 빗금 `upload stalled 2m · 30 / 48 MB` + `will be cancelled by the server if it stays stalled`(UI 는 `upload_abandon_seconds` 를 모르므로 카운트다운 금지 — Codex M2 리뷰 8) |
| 32 | `recent: null` / `[]` | 빨간 띠 `Recent unavailable — <recent_error>` / 점선 `No completed jobs yet` |
| 33 | `progress.phase == materializing` | 스텝 블록 대신 Reason `preparing workspace · unpacking 48 MB` |
| 34 | `estimate.stuck` | Reason `⚠ likely stuck · N× expected · no output for Nm`, 24 의 맨 위 |
| 35 | `reason == not_scheduled` | 황토 띠 `Lane N is idle but #413 has not started for 40s — check the server log`, Reason `not scheduled` |

## 6. 테스트

- `tests/test_web.py`(Python): `GET /` 200 html · `/static/app.js`·`style.css` 200 + 올바른 Content-Type + ETag/304 · `/static/nope` 404 · `/static/../x` 400 · `read_auth=basic` 이면 401 · HEAD · wheel 을 만들어(`pip wheel . -w tmp`) 안에 `remote_ci_monitor/web/index.html` 이 있는지.
- `tests/web/*.test.js`(Node `node --test`, 외부 패키지 없음): `require("../../src/remote_ci_monitor/web/app.js")` 로 순수 함수 전부 — 표기(4절 규칙 그대로) · `reasonText` 12종 · `confidenceBadge` · `etaText`(finish_at null → `—`) · `notMoving` 순서와 unknown/ok 분기 · `yourJobs`(requester·joiner·없음·토큰 없음·queue null) · `hostPressure`(85%·partial·unknown·no_sample) · `queueHeader` · `sortQueue` · `progressHead`(partial·failed·none·materializing) · `recentLine`(exit null·lost·before start) · `rerunCommand` · `transitionsLine` · `connection` 상태기계(sse_error → polling → 30s → lost → status_ok → live) · `nextBackoff` · `workerPills`(레인 1 접기·down·paused) · `headerNote`. 목업 1절의 데이터를 픽스처 JSON(`tests/web/fixtures/status-main.json`)으로 두고 PLAN 스키마 예시와 같은 모양으로.
- `tests/test_web_browser.py`(Python + headless Chrome, 없으면 skip): 실제 서버(in-process, 워커 on, 가짜 샘플러 주입)에 slow 잡 하나 running + queued 하나를 만들고 `chrome --headless=new --dump-dom --virtual-time-budget=6000 http://127.0.0.1:PORT/` → DOM 에 `#412`·`running · lane 1`·`1st in line`·호스트 CPU 숫자·`high|med|low` 배지·요약 세 칸이 있는지. 두 번째: 서버를 닫고 `file://` 로 연 뒤 `Lost connection` 띠가 뜨는지는 virtual time 으로 30초를 돌려 확인(가능하면). 스크린샷 `--screenshot` 을 `.pytest_cache` 밖 tmp 에 남기고 경로를 출력(오너 확인용).
- CI: `unit` 잡에 `node --test tests/web` 단계(러너에 node 있음) 추가. Chrome 테스트는 러너에 Chrome 이 있으면 돌고 없으면 skip.
- mutcheck ⑥: `app.js` 의 `notMoving` 에서 `queue == null → unknown` 분기를 `ok` 로 바꾸는 변이 — Node 테스트가 잡는다(`mutcheck.py` 에 `tests` 가 `node --test …` 인 변이 종류를 추가).

## 7. 완료 기준 (PLAN M2)

폰에서 큐·스텝·자원이 읽히고(모바일 카드), 서버를 끊으면 `Lost connection` 띠가, 샘플러만 멈추면 `stale` 배지가 뜬다. 루프백에서는 headless Chrome DOM 검사 + 스크린샷으로 확인하고, 폰 확인은 오너가 Tailscale 로 한다(README 체크리스트에 두 줄 추가).
