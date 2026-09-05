# 서브에이전트 리뷰 — 웹 큐 화면 기획의 누락·보완 (2026-09-04 오후)

- 리뷰어: Claude 서브에이전트(격리 워크트리, 읽기 전용)
- 대상: `docs/wireframes/web-queue.html` v1.2(항목 31개) · `PLAN.md` v2 · Codex 디자인 리뷰 기록(중복 지적 금지 조건)
- 관점: 빠진 상태(A) · 스키마 필드 대조(B) · 상호작용 빈틈(C) · fail-open 검증(D) · 표기 일관성(E) · 접근성(F) · 모바일(G) · PLAN 불일치(H) · 다음 화면 경계(I)
- 반영은 v1.3 에서(레퍼런스 비교 리뷰와 합쳐서). 아래는 원문.

## 1. 결론

가장 큰 빈틈 세 개. **첫째, `#N` 이 순번과 잡 id 두 뜻으로 같은 상자 안에 섞여 있다** — 23 은 `#1 running`·`#4 queued`(순번), 1·11 은 `#412`·`#409`(id), 24 는 한 칸에 `#3 blocked by #409` 와 `#412 … delays #4` 를 같이 쓴다. 게다가 4절 「정렬」이 running 을 위로 재정렬하므로 레인 2 + 그룹 대기에선 id 순 `position` 이 화면에서 1·2·4·3 으로 뒤섞인다. 우선순위 1 질문(「내 잡은 몇 번째」)의 답 자체가 흔들린다. **둘째, 합류(`joined`)된 잡이 화면에 없다.** PLAN 「큐 규칙」의 합류는 두 번째 세션의 잡을 만들지 않으므로, 23 「Your jobs」를 `requester.name == whoami.name` 으로 거르면 합류한 사람에겐 `No jobs of yours` 가 뜨고 그 사람의 `rcm wait` 는 화면에 없는 잡을 기다린다. 스키마에 `joiners[]` 가 없다. **셋째, fail-open 구멍이 요약 줄과 최근 완료, ETA 에 남아 있다** — 24 는 `queue_error`·연결 끊김 상황의 문구가 없어 초록 `Nothing is stuck` 이 나갈 수 있고, `recent_error` 는 어디에도 그려지지 않으며, 28(정지)·27(워커 다운)에서도 ETA 열이 `10:05` 같은 시각을 계속 보여준다(스키마·큐 규칙에 「정지·레인 없음이면 `finish_at: null`」 규정이 없다).

## 2. 빠진 것 · 보완할 것

| # | 종류 | 무엇이 빠졌나 | 제안 | 우선 |
|---|---|---|---|---|
| 1 | H·E | 순번과 id 가 둘 다 `#N`. 23·24 에서 한 상자에 섞임(`#3 blocked by #409`, `#412 … delays #4`). 4절 정렬(running 먼저)과 PLAN 「`position` = id 순」이 충돌 — 레인 2 + 그룹에서 순번이 비단조 | `#` 는 id 전용. 순번은 `4th in line` / 열 머리 `pos`. 스키마: running·cancelling 은 `position: null`, 대기 잡만 1.. 을 매김(서버가 계산). 23 문구 `#415 · 4th · ETA 09:59` | 높 |
| 2 | A·B | 합류 잡. `queue[]` 에 요청자가 하나뿐이라 합류한 사람의 「내 잡」·`you` 배지·취소 권한이 정의 안 됨. 원 요청자가 취소하면 합류자 `rcm wait` 가 2 로 끝나는데 경고 없음 | `queue[].joiners: [{name,label}]` 추가. 9 에 `alice@laptop +1` 칩, 툴팁에 합류자. 23 필터를 `requester.name == me ∥ joiners[].name == me` 로. 취소 대화상자에 `2 other sessions are waiting on this job`. 합류자 취소 권한은 ⛔ 오너 결정(추천: 합류자는 「내 대기만 빠지기」 = `rcm wait` 중단, 잡은 유지) | 높 |
| 3 | D | 24 「Not moving」: `queue: null`·연결 끊김·`reason` 필드 없는 옛 서버에서 초록 `Nothing is stuck` 이 나갈 수 있음. 23 도 `queue_error` 때 문구 없음 | 상태 4개 명시: 정상 없음 → 초록 `Nothing is stuck` · `queue_error` → 회색 `unknown — queue unavailable` · 연결 끊김(18) → 회색 `last known: …` + dim · `reason` 없음 → `unknown` . 23 도 `queue unavailable` 분기 | 높 |
| 4 | D·H | 28 정지·27 레인 다운·전 레인 다운·17 빈 큐 `starts immediately` 에서 ETA 가 시각을 계속 보임. PLAN 「대기」 계산이 `server.lanes` 를 그대로 씀 | 큐 규칙에 「대기 계산은 `state ≠ down` 레인 수. 0 이거나 `paused` 면 `finish_at: null`·`wait_seconds: null`」. 화면: ETA `—` + Reason `paused`/`no worker`. `reason` 에 `worker_down` 추가. 17 문구는 정지·다운이면 `Queue is empty but paused — nothing will start` | 높 |
| 5 | A | 큐에서 잡이 종료 이벤트 없이 사라지는 경로: 업로드 포기(31 `removed in 3m`)·413 거부·tar 필터 거부·claim 시 프리셋이 설정에서 없어짐·입력이 새 설정에서 무효. 어디로 가는지(recent?) 미정 | 전부 종료 상태로 남긴다(지우지 않는다): 업로드 포기·413 → `cancelled` + `summary: "upload abandoned after 5m"` / `"snapshot 600 MB exceeds 512 MB"` (413 은 즉시, 5분 기다리지 않음) · tar 거부·프리셋 소멸 → `failed` + `exit_code: null` + summary. SSE `job_finished` 로 UI 가 안다. 예외 없이 사라지면 토스트 `#415 disappeared from the queue` | 높 |
| 6 | D | `recent_error` 를 그리는 곳이 없다(4절 「실패 표시」에 큐·호스트·연결·워커만). `recent: []` 와 `null` 구분 없음 | 14 에 상태 추가: `recent: null` → 빨간 띠 `Recent unavailable — <recent_error>` · `[]` → `No completed jobs yet`. 15 Estimates 도 `medians` 가 `null` 이면 같은 규칙 | 높 |
| 7 | B | 스키마에 없는 필드 다수(아래 2b 표). `server.last_error` 는 스키마엔 있는데 화면 어디에도 없음(PLAN 「서버 건강」은 머리에 찍으라 함) | 2b 표대로 스키마 추가. 1 번 머리에 `last_error` 배지(빨강 `error · <first 60 chars>`, 클릭 시 전체) | 높 |
| 8 | H | 그룹 대기 잡의 ETA(행 3 `10:05 · in 9:00`)가 PLAN 「그룹 제약은 근사로 무시」 결과 — 막는 잡보다 이른 시각이 나올 수 있는데 신뢰도 배지가 `low · preset` 으로만 가림 | blocked 행은 `after #409 · ~10:05` 로 쓰고 서버가 `finish_at ≥ blocker.finish_at + expected` 하한을 적용. 신뢰도 강제 `low · group wait` | 중 |
| 9 | C | 취소 확인 문구·되돌리기·대기 잡 취소의 즉시 반응이 미정. uploading 잡 취소 가능 여부 미정 | 대화상자: `Cancel #412 gate:full (alice@laptop)?` 본문 running → `SIGTERM now, SIGKILL after 10 s. Cannot be undone.` / queued → `Removed from the queue. Re-run rcm run to resubmit.` 버튼 `Cancel job` / `Keep`. 요청 직후 행을 dim + `cancel requested…`, 5초 안에 이벤트 없으면 `/api/status` 재조회. uploading 취소 = `cancelled`, 진행 중 `PUT` 은 409 | 중 |
| 10 | A | 워크스페이스 준비 단계(tar 풀기·`git_ref` fetch — 수십 초)가 `running` 인데 스텝이 없어 12 규칙상 `no step markers · job 59s` 로 나감 | `progress.phase: "materializing" \| "executing"` 추가. Reason `preparing workspace · 48 MB` / `fetching dev`. 스텝 블록 대신 한 줄 | 중 |
| 11 | A | 시계 차이. `in 5:10` 을 브라우저 `Date.now()` 로 계산하면 `09:57` 과 어긋남 | 응답마다 `skew = generated_at − Date.now()` 저장, 상대 표기는 전부 `server_now = Date.now()+skew` 기준. `\|skew\| > 30 s` 면 2 번 옆에 `clock +2m` 칩 | 중 |
| 12 | A | 서버 재시작·버전 불일치. 재연결 뒤 running 이 lost 로 바뀐 이유를 화면이 말하지 않음. 캐시된 `app.js` 가 새 `schema_version` 을 받을 때 규칙 없음 | 재조회 시 `server.uptime_seconds` 가 줄면 띠 `Server restarted at 09:58 — running jobs were marked lost`. `schema_version` 이 UI 가 아는 것보다 크거나 `server.version` 이 바뀌면 `UI out of date — reload` 띠(자동 새로고침 1회) | 중 |
| 13 | A | 잡 수백 개. 표·SSE 페이로드·`log_tail` 규칙 없음 | 표는 running 전부 + 대기 20행 + `and 130 more`(펼침). 내 잡·24 에 걸린 잡은 접힘과 무관하게 항상 표시. `log_tail` 은 running 행에만 싣는다(스키마 문장) | 중 |
| 14 | C | 여러 탭. 한 탭이 401 로 토큰을 지우면 다른 탭은 메모리 토큰으로 계속 401. SSE 상한 16 이라 17번째 탭은 폴링 | `storage` 이벤트로 `rcm.token`·접힘 상태 동기화, 토큰 변경 시 whoami 재호출. `document.hidden` 60초 넘으면 SSE 닫고 복귀 시 재조회 | 중 |
| 15 | C | 뒤로가기·새로고침·딥링크. 스키마 `url: …/#/jobs/412` 가 있는데 화면에 라우트 정의 없음. 접힘은 localStorage 라지만 정리 규칙 없음 | `#/jobs/<id>` = 그 행 스크롤·강조·펼침(+토큰 있으면 로그 서랍). 큐에 없으면 recent 로, 거기도 없으면 토스트 `#409 finished 09:55 · succeeded`. 접힘 키는 큐에 없는 id 를 매 갱신 때 정리. 서랍 열림도 해시로(뒤로가기 = 닫기) | 중 |
| 16 | C·F | hover 전용 정보: 7 칩 툴팁(전체 inputs JSON)·8 sha 툴팁·9 같은 요청자 강조·8 같은 tree_hash 강조 — 키보드·터치 대응 없음. 12 의 ▾ 버튼이 목업 행에 없음 | 칩·sha 를 `button` 으로, 포커스 시 같은 강조, Enter 로 서랍(I 참고). 목업 행 1 에 ▾ 를 실제로 그린다(`aria-expanded`·`aria-controls`) | 중 |
| 17 | F | 글리프(`▶ · ↑ ■ ⏱ ? ✓ ✗ ⛓ ◌`)가 스크린리더에 「black right-pointing triangle」로 읽힘. 라이브 영역 정책 없음(경과가 1초마다 바뀜). 진행 막대 시맨틱 없음 | 글리프 `aria-hidden`, 필 텍스트만 읽힘. `aria-live="polite"` 는 상태 전이(내 잡 시작·종료·lost, 연결 끊김/복구)만, 초당 갱신 요소는 `aria-live="off"`. 18·22·27 띠는 `role="alert"`. 12 막대 `role="progressbar"`, `steps_total_partial` 이면 `aria-valuetext="step 5 of at least 8"` + pend 세그먼트 안 그림. 16 막대는 `<meter>` | 중 |
| 18 | F | 왼쪽 바 충돌: `table.q tr.mine td:first-child` 와 `tr.overdue td:first-child` 가 같은 `box-shadow` 를 덮어써 **내 잡이 초과 실행이면 overdue 바가 사라진다**(`.mine` 특이도가 더 높음) | 「내 잡」은 `you` 배지만, 왼쪽 굵은 바는 overdue 전용으로 4절 「상태 부호화」 수정. 또는 `box-shadow: inset 3px 0 var(--accent), inset 7px 0 var(--warn)` 겹치기 | 중 |
| 19 | G | 720px 미만 정의가 큐 행 카드(21)뿐. 머리(레인 필·live·토큰)·진행 블록(`.steps` 2열 280px)·로그 tail(`white-space: pre; overflow: hidden` → 잘림)·recent 6열 그리드(≈504px)·호스트 카드 확장 방법이 없음. 720~960px 중간 폭 규칙 없음 | 머리: 레인 필 → `2 busy`, live → 점만, 토큰 → 아이콘만. 스텝 1열, tail `overflow-x: auto`. recent 2줄 카드 `✗ failed · gate:fast · 1:02 · 09:47` / `2 tests failed · step test · bob@desk`. 25 탭 → 카드 펼침. 960px 미만은 Source 열을 펼침 블록으로 | 중 |
| 20 | D | 25 판정 `85% 이상이면 busy 아니면 fine` 은 null 을 fine 으로 만든다. `hosts: []`(오류 아님) 미정. 19 「3×주기」는 클라이언트가 주기를 모른다 | null 이 하나라도 있으면 `· partial`, 전부 null 이면 `· unknown`. `hosts: []` 는 `host: no sample` (빨강 아님, 회색). stale 은 서버 `stale` 플래그 + `hosts[].interval_seconds` 로 클라이언트 외삽 | 중 |
| 21 | D | 실패 종류별 표시 미정: `exit_code: null` 인 failed(자재화 실패), 시작 전 취소(`job_seconds: null`, 목업은 `0m 12s`), 취소자·타임아웃 한계값 근거 필드 없음 | 14: `exit_code` null → `✗ failed`(exit 생략) + summary. 시작 전 취소 → 소요 `—` + `cancelled before start by carol`. 필드 `recent[].cancelled_by`·`timeout_seconds` 추가(2b) | 중 |
| 22 | A·F | `step-end::fail` 로 스텝은 실패했는데 스크립트가 계속 도는 경우(`steps[].ok: false`, running) — 12 에 ✘ 글리프·failed_step 표시가 없음 | 목록에 `✘ test 51s`(빨강), 머리에 `1 step failed`, `progress.failed_step` 을 running 행에서도 씀. 실패 뒤 계속 도는 잡은 24 에 안 올림(행동 불가) | 중 |
| 23 | E | 시간 표기 혼재(3절 참고). `over by`(20) vs `overdue by`(24), `updated`/`sampled`/`Host pressure ·` 나이 세 가지, `now + 0:30`(20) vs `in`. 초과 실행의 ETA `now + 0:30` 은 잔여 하한이 만든 자신있는 틀린 시각 | 3절 규칙. 초과 실행 ETA 는 `—` + 신뢰도 `overdue`, Reason 에 `over by 3:31 · expected 6:09` | 중 |
| 24 | H | 2 「끊기면 10초 폴링」 vs 18 「지수 백오프 2s→30s」 vs PLAN 「끊기면 10초 폴링」 | 「SSE 재연결 시도는 2→30s 백오프, 그 사이 폴링은 10s 고정, 둘 다 30s 무응답이면 18 띠」로 세 곳 통일 | 낮 |
| 25 | H | PLAN 「웹 UI (M2)」 ASCII·배지 목록·「상단 token 입력」은 v1.1 배치(Remaining/ETA 분리, source 앞). M2 완료 기준 「서버를 끊으면 stale」은 화면상 `Lost connection`(18) 이지 stale(19)이 아님. PLAN 「열린 ⛔ 없음」 vs 6절 오너 질문 4개 | PLAN 웹 UI 절을 「배치는 `docs/wireframes/web-queue.html` 이 정본」 한 줄 + 요약 줄 세 칸으로 교체. 완료 기준 문구 수정. 6절 4개를 결정 항목 12~15 로 올리거나 ⛔ 로 표시 | 낮 |
| 26 | B·H | 28 `rcm pause` 가 PLAN CLI 표·API 표에 없음. 4 `GET /api/whoami` 라우트 없음. 14 `recent_count` 설정 키 없음 | CLI 표에 `rcm pause\|resume`(admin), API 에 `POST /pause`·`POST /resume`(admin)·`GET /api/whoami`(토큰), `[server] recent_count = 8` | 낮 |
| 27 | I | 툴팁에 넣은 상세(7 inputs JSON·8 tree_hash·9 합류자)와 14 「행 클릭 → 잡 상세(토큰 필요)」. 상세 자체(요약·실패 스텝·inputs·소스)는 토큰이 필요 없고 로그만 필요 | 경계: 이 화면 = 큐·요약·호스트·recent 5건·Estimates. 서랍 = inputs 전체·소스 상세·합류자·스텝 타임스탬프·전체 로그(로그 창만 `Add a token`)·`rcm wait --job` 복사. 프리셋 입력 스키마·표본 목록은 프리셋 화면, 토큰 발급·폐기는 CLI 전용(웹은 저장·확인만) | 낮 |
| 28 | A | 긴 라벨 40자 절단(9)이 23·24·21 에는 없음. `--by` 라벨은 남을 흉내 낼 수 있는데 hover 강조가 `label` 기준인지 `name` 기준인지 미정 | 절단 규칙을 4절로 올려 전 영역 적용(`…` + 툴팁 전체). 강조·`you` 는 항상 `requester.name`(토큰 이름), 툴팁에 `token: alice-laptop` | 낮 |
| 29 | C | 토큰 입력 UI 미정: Enter/Escape, 검증 중 표시, **네트워크 오류 ≠ 401**(29 는 401 만 지우라 하지만 명시 필요) | `<dialog>`: Enter 제출·Escape 닫기·`checking…`. 401/403 만 저장값 삭제, 네트워크 오류는 `couldn't verify — kept` | 낮 |
| 30 | A | Codex 「반드시 9」의 `all workers idle but queue blocked` 가 반영표(6개)에서 빠졌다 — 스케줄러가 걸린 상태에 문구가 없음 | 대기 잡이 있고 `paused` 아니고 `blocked_by` 없고 idle 레인이 10초 넘게 있으면 27 과 다른 띠 `Idle lane but nothing starts — see server log`, Reason `not scheduled` | 중 |

### 2b. 필드 대조 — 항목이 요구하지만 「/api/status 스키마 v1」에 없는 것

| 항목 | 필드 | 어디에 추가 |
|---|---|---|
| 1·27 | `server.workers[].state` 값 집합 `idle\|busy\|down`(예시엔 `busy` 만), `workers[].error`, `workers[].since` | `server.workers[]` |
| 1·28 | `server.paused: null \| {by, at}` | `server` |
| 4·13 | `GET /api/whoami` → `{name, admin}` | 서버 API 표 |
| 6·8·31 | `source.received_bytes`, `source.last_received_at`(멈춤 시간 계산용) | `queue[].source` |
| 11·24·20 | `queue[].reason`(+ `worker_down`·`not_scheduled`), `queue[].ahead_job_id`, `queue[].lane`(store 에 이미 `lane` 열이 있음), `estimate.delays[]` 또는 「클라이언트 계산」 명시 | `queue[]` |
| 7 | `queue[].concurrency_group`(프리셋 재설정과 무관하게 잡에 박힌 값) | `queue[]` |
| 10·26 | `estimate.confidence`? 아니면 「high ≥5 · med <5 · low」를 PLAN 에 규칙으로 | 큐 규칙 |
| 12·10 | `progress.phase`(materializing/executing) | `queue[].progress` |
| 13 | `log_tail` 은 토큰 요청에만, 아니면 `null`(PLAN 본문은 아직 옛 규칙) | API 표·스키마 규칙 |
| 14 | `recent_count`, `recent[].cancelled_by`, `recent[].timeout_seconds`, `recent[].started_at`, `recent[].source.{base_sha, dirty}`, `recent[].inputs` | `[server]`·`recent[]` |
| 15·26 | `presets[].expected_seconds`, `presets[].timeout_seconds`, `presets[].duration_key_inputs` | `presets[]` |
| 19·25 | `hosts[].interval_seconds` | `hosts[]` |
| 23·9 | `queue[].joiners[]` | `queue[]` |
| 30 | 상태 `cancelling`, `queue[].cancel: {requested_at, by, kill_at}`(「kill in 8s」 카운트다운 근거) | 잡 모델·`queue[]` |
| 31 | `[server] upload_stall_seconds = 60`, `upload_abandon_seconds = 300` | 설정 |
| 28 | `rcm pause\|resume`, `POST /pause`·`/resume` | CLI·API 표 |

스키마에 있는데 화면이 안 쓰는 것: `server.last_error`(써야 함, #7) · `server.uptime_seconds`(재시작 감지에 쓸 것, #12) · `schema_version`(버전 검사, #12) · `queue[].url`·`recent[].url`(라우트 미정, #15) · `progress.steps[].ok`·`progress.failed_step`(running 행, #22) · `hosts[].source` · `presets[].source_modes`·`inputs`(프리셋 화면으로, 정상) · `pools[].name`(M5 까지 미표시, 정상).

## 3. 수치·시간 표기 규칙 제안

한 화면에 `0:59`·`waiting 1:35`·`in 5:10`·`1m 02s`·`0m 12s`·`6m 9s`·`1m 05s`(Estimates 한 줄 안에서 0 채움이 다름)·`stale 3m 12s`·`upload stalled 2m`·`kill in 8s`·`now + 0:30`·`9:40` 이 섞여 있고, `9:40`(경과)은 옆 열 `09:57`(시각)과 모양이 같다. 규칙: **시각은 항상 `HH:MM` 다섯 글자**(0 채움, 오늘이 아니면 `Sep 3 · 23:40`, 3 번 시간대), **지속시간은 한 함수 `fmtDur`** — `< 60 s` → `12s`, `< 1 h` → `m:ss`(분은 0 을 채우지 않음: `0:59`·`5:10`·`20:00`), `≥ 1 h` → `h:mm:ss`, 모름 → `—`. 분을 0 으로 채우지 않으니 `5:10` 과 `09:57` 이 눈으로 구분된다. 지속시간엔 항상 맥락 단어를 앞에 둔다(`in 5:10`·`waiting 1:35`·`ran 1:02`·`over by 3:31`·`stale 3:12`), `now + 0:30` 같은 두 번째 문법은 없앤다. **상대 나이·카운트다운은 거칠게**: `< 60 s` → `4s ago`/`in 8s`, 그 이상은 분 단위 `3m ago`/`in 3m`(초까지 갱신되는 나이는 화면만 떨린다), 나이 라벨은 `updated`(연결)·`sampled`(호스트) 둘만. 퍼센트는 정수, 메모리는 GB 소수 한 자리·MB 정수, 단위 앞 공백 고정(`500 MB`, `14.0 / 24 GB`, `30 / 48 MB`), 표본은 `n=7`, id 는 `#412`, 순번은 `4th`. 문구 통일: `over by`(`overdue by` 폐기), `stalled`.

## 4. PLAN.md 에 추가할 문장

- 「큐 규칙」: `position` 은 대기(`uploading`·`queued`) 잡에만 1 부터 매기고 실행 중·취소 중은 `null`. 대기 계산은 `state ≠ down` 인 레인 수를 쓰고, 그 수가 0 이거나 `paused` 면 `finish_at`·`wait_seconds` 는 `null`. 그룹에 막힌 잡의 `finish_at` 은 막는 잡의 `finish_at + expected` 를 하한으로 한다.
- 「큐 규칙 · 합류」: 합류한 요청자는 `queue[].joiners[]` 에 남기고, 화면·`rcm jobs --mine` 은 요청자와 합류자를 모두 「내 잡」으로 본다. 원 요청자의 취소는 합류자에게 종료 코드 2 로 전파된다(⛔ 합류자 취소 권한).
- 「잡 모델」: 상태 `cancelling`(실행 중 취소 요청 ~ 종료) 추가. 업로드 포기·413·tar 거부·claim 시 프리셋 소멸은 잡을 지우지 않고 `cancelled`/`failed` + `summary` 로 남긴다(`exit_code: null` 허용).
- 「서버 API」: `GET /api/whoami`(토큰 → `{name, admin}`) · `POST /pause`·`/resume`(admin) 추가. `/api/status`·`GET /jobs/{id}` 의 `log_tail` 은 유효 토큰 요청에만, 아니면 `null`. `log_tail` 은 running 잡에만 싣는다.
- 「스키마」: `server.paused`·`server.workers[].{state ∈ idle|busy|down, error}` · `queue[].{reason, ahead_job_id, lane, concurrency_group, joiners, cancel}` · `source.{received_bytes, last_received_at}` · `progress.phase` · `recent[].{cancelled_by, timeout_seconds, started_at}` · `presets[].{expected_seconds, timeout_seconds}` · `hosts[].interval_seconds`. `recent_error` 도 `queue_error` 와 같은 「null + 오류」 규칙.
- 「웹 UI (M2)」: ASCII 스케치를 지우고 「배치·상태·문구는 `docs/wireframes/web-queue.html` 이 정본. 큐 위에 Your jobs · Not moving · Host pressure 요약을 먼저 보인다」. 갱신은 「SSE 재연결 2→30s 백오프, 폴링 10s 고정, 30s 무응답이면 Lost connection 띠」. 표기 규칙(3절)을 「표기 규칙」 소절로. 응답의 `generated_at` 으로 시계 차이를 보정하고 30초 넘으면 표시. `schema_version`·`server.version` 이 바뀌면 UI 를 새로 고친다.
- 「fail-open 금지」: 요약 줄의 긍정 문구(`Nothing is stuck`·`fine`)는 해당 섹션 조회가 성공했고 값이 전부 있을 때만 그린다.
- 「설정」: `[server] recent_count = 8` · `upload_stall_seconds = 60` · `upload_abandon_seconds = 300`.
- 「결정 항목」: 6절의 오너 질문 4개와 합류자 취소 권한을 ⛔ 12~16 으로 올린다(현재 「열린 ⛔ 없음」과 모순).
- M2 완료 기준: 「서버를 끊으면 stale 이 뜬다」 → 「서버를 끊으면 Lost connection 띠가, 샘플러만 멈추면 stale 배지가 뜬다」.
