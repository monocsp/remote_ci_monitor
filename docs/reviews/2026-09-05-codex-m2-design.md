# Codex 크로스리뷰 — M2 웹 UI 작업 명세 (2026-09-05 오전)

- 리뷰어: Codex CLI · `codex exec --sandbox read-only` (프롬프트는 아래 요지)
- 대상: `docs/m2-workplan.md`(결정 A~H · 정적 서빙 · app.js 순수 함수 · 갱신 상태기계 · DOM 계약 · 변형 19개 · 테스트 계획) · `docs/wireframes/web-queue.html` · PLAN.md · `server.py` · `core/render_text.py`
- 결론(Codex): 방향은 맞지만 그대로 구현하면 **XSS/CSP · EventSource 503 처리 착각 · fail-open 문구 · 접근성 누락 · CI/Chrome flaky** 가 실제 결함으로 난다. `innerHTML` 전면 렌더와 `localStorage` 토큰은 목표와 충돌하지 않지만 escape·상태 보존·인증 실패 처리 계약을 명세에 박아야 한다.

## 반영

| # | Codex 지적 | 판정 | 한 일 |
|---|---|---|---|
| 필수 1 | XSS 경로 | 동의 | 모든 삽입값(summary · failed_step · log_tail · last_error · *_error · label · repo · inputs · title 속성)을 `rcm.esc()` 로 escape(따옴표 포함이라 텍스트·속성 둘 다). `.tail` 은 escape 한 텍스트만 |
| 필수 2 | 포커스 유실 | 동의 | `withFocus()` — `innerHTML` 교체 전 `activeElement` 의 id/data-속성 키를 저장하고 교체 뒤 같은 요소로 `focus({preventScroll:true})` |
| 필수 3 | EventSource 는 503 본문을 못 읽는다 | 동의 | `onerror` 하나로: 즉시 polling · 10초 폴링 · SSE 재접속은 2→30s 타이머로 직접 재생성. 명세 0-D 문장 수정 |
| 필수 4 | lost 때 dim 여부 PLAN/명세 충돌 | 동의 | dim 하지 않고 띠 + 나이만(dim 은 호스트 stale 에만). **결정 21(오너 확인 대기)** |
| 필수 5 | CSP | 동의 | `index.html` 응답에 `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` + `Referrer-Policy: no-referrer`. 인라인 스크립트·스타일 없음 |
| 필수 6 | 401 처리 범위 · read_auth 구분 | 동의 | status/whoami/log/cancel 의 401/403 → 같은 `tokenRejected()`. 토큰 없이 `/api/status` 401 이면 버튼 `Read auth required` |
| 필수 7 | 모달 접근성 | 동의 | `<dialog>` 는 `aria-labelledby` + 닫기 버튼 + 네이티브 Escape/포커스 트랩, 서랍은 `role=dialog` + Escape + 닫기, 닫힐 때 트리거 버튼으로 포커스 복귀(`restoreTrigger`) |
| 필수 8 | 변형 31 의 카운트다운 | 동의 | `will be cancelled by the server if it stays stalled` — UI 는 `upload_abandon_seconds` 를 모른다 |
| 필수 9 | 긍정 문구 조건 | 동의 | `notMoving`·`hostPressure`·`yourJobs`·`queueHeader` 는 queue/hosts null · reason 누락 · workers 누락이면 unknown/partial. Node 테스트가 잠근다 |
| 필수 10 | Chrome lost 테스트 | 부분 | `file://` 방식은 버렸다. 서버 shutdown 뒤 virtual time 으로 `#banner-lost` 검사는 flaky 위험이 커서 Node 상태기계 테스트로 대신하고, 브라우저 테스트는 렌더·모바일·스크린샷만 |
| 좋음 1 | CommonJS 가드 · globalThis | 동의 | `module.exports` → `globalThis.rcm` → `document` 가드 순 |
| 좋음 2 | `generated_at` NaN | 동의 | `skewUnknown` → 상대 시간 전부 `—` + `clock unknown` 칩 |
| 좋음 3 | 표 접근성 | 부분 | 펼침 버튼 `aria-expanded`/`aria-controls="exp-<id>"` + 펼침 행 id. caption 은 섹션 `aria-label` 로 대신 |
| 좋음 4 | tick 침묵 | 동의 | `[data-tick]` 은 live region 아님, 전이만 `#toast[aria-live=polite]` |
| 좋음 5 | 배너 role 통일 | 동의 | 큐/최근/호스트 실패 띠와 worker down · paused 띠 전부 `role="alert"` |
| 좋음 6 | setup-node | 동의 | CI `unit` 잡에 `actions/setup-node@v4` Node 22 + `node --test tests/web/` |
| 좋음 7 | Chrome 탐색 | 동의 | 테스트가 env → 경로 후보 → PATH 순으로 찾고 없으면 skip |
| 좋음 8 | 정적 캐시 | 동의 | `index.html` 은 `no-store`, JS/CSS 는 `ETag` + `no-cache` |

### 오너에게 물을 것 (추천값으로 구현, 확인 대기 — PLAN 결정 21·22)

1. Lost connection 때 화면 전체 dim 여부. 추천·구현: dim 없이 띠와 나이 증가만.
2. 토큰을 `localStorage` 에 두는 위험. 추천·구현: M2 에서 허용 + CSP 강제 + README 「공용 브라우저 금지 / XSS 면 토큰 노출」.

## 프롬프트 (요지)

검토 항목 A(결정 A~H 의 함정 — innerHTML 렌더의 XSS·포커스·성능, EventSource 폴백, 시계 보정, 토큰 보안) · B(DOM 계약이 목업 4절 접근성을 만족하는가) · C(변형 19개 매핑에서 스키마에 없는 값·fail-open 위반) · D(node --test / headless Chrome 이 러너에서 도는가, flaky, mutcheck ⑥) · E(빠진 것 — 완료 기준·정적 서빙 hardening). 답 형식: 결론 · 반드시 고칠 것(≤10) · 고치면 좋은 것(≤8) · 그대로 둘 것(≤5) · 오너에게 물어야 할 것.

## 리뷰 원문

결론: M2 명세는 큰 방향은 맞지만, 지금 그대로 구현하면 **보안/XSS, EventSource 503 처리 착각, fail-open 문구, 접근성 누락, CI/Chrome flaky**가 실제 결함으로 날 가능성이 높다. 특히 `innerHTML` 전면 렌더와 `localStorage` 토큰은 "표준 라이브러리만" 목표와 충돌하지 않지만, 반드시 더 엄격한 escape·상태 보존·인증 실패 처리 계약을 명세에 박아야 한다.

**반드시 고칠 것**
1. `0-C 렌더` · XSS 경로 차단 · `rcm.escText()`와 `rcm.escAttr()`를 분리하고, `summary`, `failed_step`, `log_tail`, `server.last_error`, `queue_error`, `recent_error`, `worker.error`, `requester.label`, `repo`, `inputs` 값은 전부 escape 후 삽입하라. `.tail` 로그는 HTML 문자열이 아니라 `<pre>`/`div`에 `textContent`로 넣는 계약을 추가하라.
2. `0-C / 4 DOM 계약` · 포커스 유실 방지 · 섹션 `innerHTML` 교체 전 `document.activeElement`의 `data-action`, `data-job`, `id`를 저장하고, 교체 후 같은 요소가 있으면 `focus({preventScroll:true})`로 복구하라. 없으면 포커스를 `body`로 날리지 말고 가장 가까운 행의 펼침 버튼으로 보내라.
3. `0-D 갱신` · 503 SSE 폴백 오해 수정 · `EventSource`는 503 JSON body와 `Retry-After`를 JS에서 읽을 수 없다는 점을 명세에 써라. 구현은 `onerror` 하나로 처리하고, 즉시 `mode=polling`, 10초 폴링 시작, SSE 재시도는 `2,4,8,16,30`초 타이머로 직접 재생성하라.
4. `3 갱신 상태기계` · 목업/PLAN 충돌 정리 · `m2-workplan`은 lost 때 "화면은 dim 하지 않음"이라고 하고, PLAN M2는 "마지막 상태를 dim"이라고 한다. 목업 4절 기준으로 하나만 고정하라. 추천: 전체 dim 금지, `#banner-lost`와 stale/age 증가만 표시. dim은 host stale에만 써라.
5. `0-F 토큰` · 보안 누락 보강 · `localStorage` 토큰은 XSS에 취약하므로 CSP와 함께 묶어라. 정적 응답에 `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`를 추가하고, 인라인 스크립트/스타일 금지를 명세화하라.
6. `0-F / 4 토큰` · 401 처리 범위 수정 · `/api/status`, `/jobs/{id}`, `/jobs/{id}/log`, `/jobs/{id}/cancel`, `/api/whoami`에서 401/403이 오면 모두 같은 `tokenRejected()` 경로를 타게 하라. 단, `read_auth=basic`의 읽기 401과 bearer 토큰 거부 401을 UI가 구분할 수 있게 버튼 문구를 `Read auth required` / `Token rejected`로 나눠라.
7. `4 접근성` · 누락 요소 추가 · `dialog#tok-dialog`, `dialog#cancel-dialog`, `#drawer(role=dialog)`에 `aria-labelledby`, 닫기 버튼, Escape 닫기, 열린 동안 포커스 트랩, 닫힐 때 트리거 버튼으로 포커스 복귀를 명세에 넣어라. 현재 목업 4절의 "button/summary"만으로는 모달 접근성이 부족하다.
8. `5 변형 31` · 스키마 없는 값 제거 · UI는 `upload_abandon_seconds`를 모른다. `cancelled in 3m`을 그리지 말고 `upload stalled 2m · 30 / 48 MB`와 `will be cancelled by server if it stays stalled`처럼 시간 없는 문구로 고정하라. 서버가 `upload_abandon_at`을 스키마에 추가하기 전까지 countdown 금지.
9. `5 변형 / fail-open` · 긍정 문구 조건 강화 · `notMoving`, `hostPressure`, `yourJobs`, `queueHeader`는 `queue === null`, `hosts === null`, `reason` 누락, `estimate` 누락, `workers` 누락이면 `ok/fine/none`을 반환하지 말고 `unknown`을 반환하라. 테스트에 "필드 누락 → 긍정 문구 금지" 케이스를 넣어라.
10. `6 테스트` · Chrome lost 테스트 수정 · 서버를 닫고 `file://`로 여는 테스트는 `/api/status`와 `/events` origin이 깨져 실제 운영 경로가 아니다. 같은 HTTP URL을 연 상태에서 서버만 shutdown하고 `--virtual-time-budget=35000`으로 `#banner-lost[role=alert]`를 검사하라.

**고치면 좋은 것**
1. `0-B JS 구조` · CommonJS 가드 명확화 · 파일 끝은 `if (typeof module !== "undefined" && module.exports) module.exports = rcm; if (typeof document !== "undefined") boot();` 순서로 고정하라. `window` 대신 `globalThis.rcm = rcm`로 노출하면 Node 파싱 함정이 줄어든다.
2. `0-E 시계` · 보정 실패 처리 · `Date.parse(generated_at)`가 `NaN`이면 상대 시간 전체를 `—`로 그리고 `clock unknown` 칩을 보여라. 브라우저 시계로 조용히 대체하면 fail-open이다.
3. `4 DOM 계약` · 표 구조 접근성 · 큐 표에 `<caption class="sr-only">Queue</caption>`, 행 펼침 버튼에 `aria-expanded`와 `aria-controls="job-412-progress"`, expanded 행에 해당 `id`를 넣어라.
4. `4 접근성` · 초당 tick 침묵 보장 · `[data-tick]`에는 `aria-live="off"` 또는 컨테이너 `aria-hidden`이 아니라 "텍스트는 보이되 live region 아님"을 테스트로 확인하라. 상태 전이만 `#toast[aria-live=polite]`로 보내라.
5. `5 변형 27/28` · 배너 role 통일 · worker down, queue paused, queue unavailable, recent unavailable 모두 `.banner[role="alert"]`로 계약을 통일하라. paused는 운영상 시작 불가 상태라 보조기기에도 즉시 알려야 한다.
6. `6 테스트` · CI Node 보장 · `actions/setup-node`를 추가해 Node 버전을 고정하라. GitHub runner에 Node가 있더라도 `node --test` 기능 차이로 흔들릴 수 있다. 추천: Node 22.
7. `6 테스트` · headless Chrome 탐색 · `chrome`, `google-chrome`, `/Applications/Google Chrome.app/...` 순으로 찾는 헬퍼를 두고 없으면 skip하라. macOS/ubuntu 경로를 하드코딩하지 마라.
8. `정적 서빙` · 캐시 hardening · `index.html`은 `Cache-Control: no-store` 또는 `no-cache, must-revalidate`, JS/CSS는 `ETag` + `Cache-Control: no-cache`로 하라. `schema_version/server.version` 불일치 자동 reload가 있으니 정적 파일이 오래 남으면 안 된다.

**그대로 둘 것**
1. `0-A` · 세 파일 + 번들러 없음은 맞다. 이 프로젝트의 런타임 의존성 0 원칙과 잘 맞는다.
2. `0-G` · `estimate.confidence`와 서버 `reason`을 기준으로 삼는 방향은 맞다. UI가 ETA 신뢰도를 재계산하면 `rcm top`과 어긋난다.
3. `4` · 실행 중 행 기본 펼침, 접힘 상태를 job id별 `localStorage`에 두는 결정은 적절하다.
4. `5` · 빈 큐와 조회 실패를 다른 모양으로 그리는 규칙은 유지해야 한다. 이게 fail-open 방지의 핵심이다.
5. `6` · 순수 함수는 `node --test`, 서버/서빙은 Python 테스트로 나누는 구조는 맞다.

**오너에게 물어야 할 것**
1. Lost connection 때 전체 화면을 dim 할지 여부. PLAN과 M2 명세가 충돌한다. 추천은 dim 하지 않고 배너만 표시.
2. 토큰을 `localStorage`에 저장하는 위험을 감수할지. 추천은 M2에서는 허용하되 CSP를 강제하고, README에 "공용 브라우저 금지 / XSS 시 토큰 노출"을 명시.
