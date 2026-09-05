# Codex 크로스리뷰 — PLAN v2.1 정합성 + M0 구현 결정 (2026-09-04 저녁)

- 리뷰어: Codex CLI · `codex exec --sandbox read-only` (프롬프트는 아래 그대로)
- 대상: PLAN.md v2.1(웹 큐 화면 5절 반영 직후) · `docs/wireframes/web-queue.html` v1.3 · M0 구현 계획(B 절)
- 결론(Codex): M0 는 진행해도 되지만 `reason/stuck` 이중 표현, `wait_seconds` null 규칙, 합류자 취소 뒤 `rcm wait` 결과, 업로드 중단 처리, M0 의 `/api/status` 완성 범위가 덜 잠겼다. 구현은 대체로 맞지만 **tar 스트리밍 업로드와 `Content-Length` 필수가 충돌**하므로 반드시 고쳐야 한다.

## 반영

| # | Codex 지적 | 판정 | 한 일 |
|---|---|---|---|
| A1 | `stuck` 이 `reason` 값이면서 `estimate.stuck` 플래그 | 동의 | `reason` 은 단일 표시 사유(우선순위 `worker_down > stuck > upload_stalled > not_scheduled > blocked_by_group > overdue > paused`), `estimate.stuck` 은 근거 플래그. PLAN 「큐 규칙」 문장 정리 |
| A2 | 초과 실행에 `wait_seconds` null 은 running 예시 `wait_seconds: 0` 과 충돌 | 동의 | `finish_at` 만 overdue/stuck 에서 null. running/cancelling 의 `wait_seconds` 는 0, 대기 잡이 paused/worker_down 이면 null 로 문장 수정 |
| A3 | `log_tail` 을 PLAN 은 running/cancelling, 목업은 running 만 | 동의 | 둘 다 허용 + 토큰 없으면 항상 null 로 PLAN 통일(목업 13 은 M2 때 맞춘다) |
| A4 | 합류자 취소 `{left: true}` 뒤 `rcm wait` 의 JSON·종료 코드 미정 | 동의 | 응답 `{"left": true, "job_id", "job_state"}`, `rcm wait` 는 같은 JSON 을 찍고 **2** 로 끝난다. PLAN API 표·CLI 표 |
| A5 | `memory.compressed_bytes`·`history[]` 이름·형태 | 동의 | 바이트 필드는 전부 `_bytes`, `history[]` 는 `{at, cpu_busy, mem_used_bytes, gpu_util_pct}` 각 값 nullable 로 스키마 규칙 문단에 명시 |
| A6 | 클라이언트 disconnect·서버 재시작 중 `uploading` 규칙 없음 | 동의 | 부분 수신 disconnect → 즉시 `cancelled` + `summary: "upload interrupted after N MB"`. 서버 시작 시 `uploading` → `cancelled` + `"server restarted during upload"`. PLAN 「잡 모델」「저장소」 |
| A7 | M0 「스키마 v1 처음부터」 vs M1 「/api/status 완성」 | 동의 | M0 는 완전한 shape 를 내되 `hosts: []`·`medians: {}` 등 stub 허용이라고 마일스톤에 명시 |
| A8 | `not_scheduled` 의 「idle 10초」 기준 시각 없음 | 동의 | `max(worker.since, job.queued_at)` 기준 10초 초과. `queued_at` 을 잡 필드로 추가 |
| B1 | store: 그룹 `""` 은 NULL 로 정규화, 전이 이벤트는 같은 트랜잭션, lost 정리 때 `lane=null` 도 | 동의 | 그대로 구현 |
| B2 | worker: `readline` 대신 `os.read` 청크 + 줄 분리, 스레드 죽기 전 현재 잡을 닫아라 | 동의 | 그대로 구현(현재 잡은 `failed` + `exit_code: null` + `summary: "worker error: …"`) |
| B3 | server: 413 에 `Connection: close`, chunked 는 411/400, partial upload 은 terminal 상태 | 동의 | 그대로 구현 |
| B4 | client: tar 를 스트리밍하면 `Content-Length` 를 못 준다 → 임시 파일로 먼저 만들고 크기 확인 뒤 PUT. 폴링은 2초로 통일 | 동의 | `tempfile.NamedTemporaryFile` 로 tar.gz 를 만든 뒤 크기·상한 검사 → PUT. `rcm wait` 폴링 2초(PLAN CLI 표의 5초 → 2초) |
| B5 | mutcheck: 변이별 timeout, 로그에 mutant 이름·명령 | 동의 | 그대로 구현 |
| C1 | 부분 수신 disconnect 는 즉시 `cancelled` | 채택 | A6 |
| C2 | 합류자 Ctrl-C 는 클라이언트가 best-effort `POST cancel` | 채택 | `rcm run`/`rcm wait` 의 KeyboardInterrupt 핸들러에서 합류자면 cancel(자기 대기만 빠짐) |
| C3 | 재시작 뒤 `uploading` 은 즉시 `cancelled` | 채택 | A6 |
| C4 | `log_tail` 기본 5줄·8KiB 상한, `GET /jobs/{id}?tail=N`, `rcm wait` 는 `tail=0` | 채택 | 그대로 구현 |

### 오너에게 물을 것 (Codex 가 사람 결정이라고 본 것 — 추천값으로 구현하고 확인을 받는다)

1. **원 요청자의 `rcm run` Ctrl-C** 가 잡 취소인가 wait 분리(detach)인가. 추천·구현: **detach 기본**(잡은 계속 돈다, 메시지에 `rcm wait --job N` / `rcm cancel N` 안내). 잡 취소는 명시적 `rcm cancel` 만.
2. **부분 업로드 재시도/resume** 을 M0 에 넣나. 추천·구현: **M0 제외**. 끊기면 `cancelled` 로 남기고 새 `rcm run` 으로 다시 제출.

## 프롬프트

(아래는 `codex exec` 에 넣은 원문)

```
너는 이 프로젝트의 설계 크로스리뷰어다. 파일을 수정하지 말고 읽기만 해라. 답은 한국어로, 결론 먼저, 근거는 짧게. 추상적인 조언 말고 **구체적으로 무엇을 어떻게 바꾸라**고 써라.

## 배경
remote_ci_monitor — 빌드 머신 한 대에 여러 컴퓨터의 세션이 잡을 던지면 서버가 자기 큐로 순차 실행하고 대기 위치·ETA·스텝 진행·CPU/RAM/GPU 를 보여주며 결과를 종료 코드로 돌려주는 로컬 잡 서버(Python 3.11+, 런타임 의존성 0, GitHub 비의존). 정본은 작업 디렉터리의 `PLAN.md`(v2.1 — 방금 웹 큐 화면 기획 `docs/wireframes/web-queue.html` 5절을 반영했다). 둘 다 끝까지 읽어라. 리뷰 기록은 `docs/reviews/*.md`.

## 검토해 달라는 것

### A. PLAN.md v2.1 의 내부 모순·누락 (방금 고친 곳)
「잡 모델」「큐 규칙」「워커」「서버 API」「저장소」「설정」「/api/status 스키마 v1」「웹 UI (M2)」「fail-open 금지」「결정 항목 12~16」을 서로 대조해라. 특히:
1. `reason` 열거값과 화면 24 「Not moving」 우선순위, `position`/`finish_at`/`wait_seconds` 의 null 규칙, `log_tail` 조건이 스키마 예시·규칙 문단·API 표에서 같은 말을 하는가.
2. 합류자(joiners) 취소 의미론(결정 16)이 API 표(`POST /jobs/{id}/cancel` 의 `{left: true}`)와 `rcm wait` 종료 코드에 일관되게 적혀 있는가.
3. 스키마 예시 JSON 에 넣은 필드 중 5절 제안에 없는 것(`medians_error`, `recent_count`, `memory.compressed_bytes`, `history[]` 항목 모양)이 타당한가.
4. 빠진 것: M0 서버가 스키마 v1 을 「처음부터」 내려면 반드시 있어야 하는데 계획에 없는 필드·규칙.

### B. M0 구현 결정 — 내가 이렇게 만들 생각이다. 틀린 곳을 찍어라.
1. **store.py**: `sqlite3` 하나, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`, 연결은 스레드마다(`check_same_thread=False` 안 씀). `claim(lane)` 은 `BEGIN IMMEDIATE` 안에서 「`state='queued'` 이고 `concurrency_group` 이 running/cancelling 잡의 그룹과 겹치지 않는 가장 작은 id」를 골라 `UPDATE … SET state='running', lane=?, started_at=? WHERE id=? AND state='queued'` 후 `rowcount==1` 확인. 시작 시 `UPDATE jobs SET state='lost', finished_at=?, summary='server restarted …' WHERE state IN ('running','cancelling')`. 마이그레이션은 `PRAGMA user_version` 로 번호. 상태 전이는 전부 `events(job_id, at, kind='state', payload)` 에 남겨 `transitions[]` 를 만든다.
2. **worker.py**: 레인마다 스레드. 루프: `claim` → 없으면 `Event.wait(0.5)`(서버가 제출·취소 때 `set()`) → 있으면 실행. 취소는 DB 의 `cancel_requested_at` 를 워커가 1초마다 확인(프로세스 읽기 루프 안에서 `select` 타임아웃 대신 `readline` 스레드 + 큐). SIGTERM → grace → SIGKILL 은 `os.killpg`. 타임아웃도 같은 경로. 워커 스레드가 예외로 죽으면 `workers[].state='down'` + `error` 를 서버 메모리에 남기고 재시작하지 않는다(사람이 봐야 한다).
3. **server.py**: `http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler`. M0 에서는 `/api/status` 를 요청 때마다 DB 에서 다시 만든다(이벤트 갱신 모델은 SSE 와 함께 M1). 업로드는 `Content-Length` 를 보고 상한 초과면 본문을 읽지 않고 413 + 잡을 `cancelled`+summary. 본문은 64KB 씩 파일로 흘리며 `received_bytes` 갱신. 토큰은 `Authorization: Bearer`, 서버엔 sha256, `hmac.compare_digest`. 오류 응답은 `{"error": "<짧은 문구>"}` 한 줄.
4. **client.py / cli.py**: `urllib.request` 만. `rcm run` = `GET /api/status` 로 프리셋 스키마 검증 → `git ls-files -z --cached --others --exclude-standard` 로 파일 목록 → `tree_hash` → `POST /jobs` → 합류 아니면 `tarfile` 로 스트리밍 업로드 → `wait`. `rcm wait` 는 M0 에선 2초 폴링(`GET /jobs/{id}`), 종료 코드 0/1/2/3, 연결 실패가 60초 넘게 이어지면 3.
5. **mutcheck.py**: `src/`+`tests/` 를 tmpdir 에 복사하고 변이 3종 — ① `queue.py` 의 잔여 하한 `max(…, floor)` → `expected - elapsed` ② 합류 키에서 `inputs` 제외 ③ `store.py` 재시작 정리에서 `'lost'` → `'succeeded'` — 각각 pytest 가 빨개지는지. 패턴 미발견은 실패.

### C. 아직 정하지 않은 것 — 네 추천 답
1. `PUT /jobs/{id}/tree` 중 클라이언트가 끊겼을 때(부분 수신) — 잡을 곧장 `cancelled` 로 할까, `uploading` 으로 두고 `upload_abandon_seconds` 를 기다릴까(재시도 허용)?
2. `rcm wait` 가 합류자일 때 `POST cancel` 을 보내면 「자기 대기만 빠짐」인데, Ctrl-C 로 `rcm run` 을 끊은 세션은 자동으로 합류자에서 빠져야 하나(서버가 알 방법: wait 폴링 끊김?), 아니면 명시적 `rcm cancel` 만?
3. 서버 재시작 뒤 `uploading` 잡은 어떻게 하나 — 즉시 `cancelled`(클라이언트는 어차피 끊겼다) vs `upload_abandon_seconds` 대기?
4. `GET /jobs/{id}` 에서 `log_tail` 을 토큰 조건으로 하면 `rcm wait` 가 매 폴링마다 토큰을 보내는데, 폴링 응답 크기·로그 tail 줄 수(기본 5?)는?

## 답 형식
1. 결론 한 단락.
2. **A 모순·누락** — 번호 · 어디(절 이름) · 무엇 · 어떻게 고칠지 (최대 10개)
3. **B 구현 결정** — 번호 · 동의/조건부/반대 · 이유 한두 줄 · 바꿀 것
4. **C 추천 답** — 각각 한 줄 추천 + 이유
5. **오너에게 물어야 할 것** — 네가 보기에 기술 검토로는 못 정하고 사람이 정해야 하는 것만 (없으면 「없음」)
```

## 리뷰 원문

결론: M0는 진행해도 되지만, 지금 PLAN v2.1은 `reason/stuck`, `wait_seconds` null 규칙, 합류자 취소 후 `rcm wait` 결과, 업로드 중단 처리, M0의 `/api/status` 완성 범위가 아직 덜 잠겼다. 구현은 대체로 맞지만 `tarfile` 스트리밍 업로드와 `Content-Length` 필수 조건이 충돌하므로 반드시 고쳐야 한다.

**A 모순·누락**

1. `큐 규칙` · `웹 UI 24/11` · `reason` — `stuck`이 `reason` 열거값이면서 `estimate.stuck` 플래그다. `reason`은 단일 표시 사유로 정하고 우선순위는 `worker_down > stuck > upload_stalled > not_scheduled > blocked_by_group > overdue > paused`로 고정하라. `estimate.stuck`은 근거 플래그로 둬라.
2. `큐 규칙` · `/api/status 스키마 v1` · `wait_seconds` — "초과 실행이면 `finish_at`·`wait_seconds` null"은 running 예시의 `wait_seconds: 0`과 충돌한다. `finish_at`만 overdue/stuck에서 null로 하고, running/cancelling의 `wait_seconds`는 `0`, 대기 잡이 paused/worker_down이면 `null`로 써라.
3. `서버 API` · `웹 UI 13` · `log_tail` — PLAN은 running/cancelling, 와이어프레임은 running만이라고 한다. `running`과 `cancelling` 둘 다 허용하되 토큰 없으면 항상 `null`로 통일하라.
4. `잡 모델` · `서버 API` · `CLI` · 합류자 취소 — `{left: true}`는 있는데 그 뒤 `rcm wait`가 어떤 JSON과 종료 코드로 끝나는지 없다. 합류자 취소 응답과 wait 출력에 `{"left": true, "job_id": ID, "job_state": "running|queued|..."}`를 넣고 종료 코드는 2로 못 박아라.
5. `/api/status 스키마 v1` · `웹 UI 5절 제안` · 추가 필드 — `medians_error`, `recent_count`는 타당하다. `memory.compressed_bytes`, `history[]`는 이름·형태가 덜 정의됐다. 바이트 필드는 전부 `_bytes`로 통일하고, `history[]`는 `{at, cpu_busy, mem_used_bytes, gpu_util_pct}` + 각 값 nullable로 문서화하라.
6. `잡 모델` · `코드 전달` · 업로드 비정상 종료 — 클라이언트 disconnect, 서버 재시작 중 `uploading` 처리 규칙이 없다. M0부터 "부분 수신 disconnect는 `cancelled` + summary", "서버 시작 시 `uploading`은 `cancelled`"를 넣어라.
7. `마일스톤 M0/M1` · `/api/status 스키마 v1` — M0에서 스키마 v1을 처음부터 낸다고 하면서, M1에 `/api/status 완성`이 있다. M0는 완전한 shape를 내되 `hosts: []`, `medians: {}`, `progress: null|materializing 최소값`처럼 stub 허용이라고 명시하라.
8. `큐 규칙` · `not_scheduled` — "idle 레인 10초"의 기준 시각이 없다. `max(worker.since, job.eligible_since)` 기준으로 10초 초과 시 `not_scheduled`라고 정하고, `eligible_since`를 이벤트나 계산 규칙으로 남겨라.

**B 구현 결정**

1. `store.py` — 조건부 동의. SQLite WAL, per-thread connection, `BEGIN IMMEDIATE` claim은 맞다. 단 `concurrency_group`은 `NULL/""`이면 충돌 없음으로 정규화하고, 상태 전이 이벤트는 job update와 같은 트랜잭션에 넣어라. 시작 시 `running/cancelling -> lost`와 함께 `lane=null`, `finished_at`, `summary`도 갱신하라.
2. `worker.py` — 조건부 동의. 레인별 스레드, `os.killpg`, cancel polling은 M0에 충분하다. `readline`은 긴 무개행 출력에서 메모리와 tail 지연이 생긴다. reader는 `os.read` chunk + line splitter로 바꿔라. 워커 스레드 전체 예외 전에 현재 job은 `lost`나 `failed(exit_code:null)`로 닫아라.
3. `server.py` — 조건부 동의. `ThreadingHTTPServer`와 요청 시 DB 재구성은 M0에 맞다. 413에서 본문을 안 읽을 거면 반드시 `Connection: close`를 붙여라. chunked는 411/400으로 거부하고, partial upload는 C의 규칙대로 terminal 상태로 남겨라.
4. `client.py / cli.py` — 반대 일부. `urllib.request`는 좋지만 "tarfile 스트리밍 업로드"와 서버의 `Content-Length` 필수가 충돌한다. tar.gz는 임시 파일 또는 `SpooledTemporaryFile`로 먼저 만들고 크기 확인 후 파일 스트림으로 PUT하라. `rcm wait` 폴링 주기는 PLAN의 5초와 제안의 2초 중 하나로 통일하라. 추천은 M0 2초.
5. `mutcheck.py` — 동의. tmpdir 복사, 패턴 미발견 실패, 3종 변이는 적절하다. 각 변이는 개별 timeout을 두고, pytest nonzero가 "의도한 테스트 실패"인지 로그에 mutant 이름과 명령을 남겨라.

**C 추천 답**

1. 부분 수신 disconnect: 즉시 `cancelled`. 재시도 프로토콜이 없으므로 `uploading`으로 기다리면 좀비가 된다.
2. 합류자 Ctrl-C: 클라이언트가 best-effort `POST cancel`을 보내 자기 joiner 항목을 빼라. 서버가 wait 폴링 끊김만 보고 추정하면 안 된다.
3. 서버 재시작 뒤 `uploading`: 즉시 `cancelled` + `summary: "server restarted during upload"`. 클라이언트 연결은 이미 사라졌다.
4. `log_tail`: 기본 5줄, job당 8KiB hard cap. `GET /jobs/{id}?tail=0|5`를 두고 `rcm wait`는 기본 `tail=0`, UI만 `tail=5`로 받아라.

**오너에게 물어야 할 것**

1. 원 요청자의 `rcm run` Ctrl-C 의미: 잡 취소인지, wait만 detach인지 정해야 한다. 추천은 detach 기본, 명시적 `rcm cancel`만 잡 취소.
2. 부분 업로드 재시도/resume을 M0에 넣을지. 추천은 M0 제외, 새 `rcm run`으로 다시 제출.
