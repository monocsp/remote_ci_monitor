# M2 테스트 시나리오 — 정적 서빙 · 브라우저 DOM (2026-09-05)

`docs/m2-workplan.md` §1(정적 서빙) · §4(DOM 계약) · §6(테스트) 을 Python 테스트로 옮긴 것이다. 구현보다 먼저
썼다(test-first). `app.js` 순수 함수의 Node 테스트(`tests/web/*.test.js`)는 이 문서 밖이다.

| 파일 | 대상 | 테스트 수 |
|---|---|---|
| `tests/test_web.py` | `GET /` · `/static/*` · ETag/304 · 404/400 · HEAD · 405 · `read_auth` · 패키징 | 함수 9 (parametrize 포함 10건) |
| `tests/test_web_browser.py` | 진짜 서버 + headless Chrome — DOM 계약 · 모바일 · 스크린샷 | 3 (Chrome 없으면 skip) |

공통 규칙: 기다림은 전부 **마감이 있는 폴링**이다(`status_until` 3~5초, CDP 호출 15초, 페이지 준비 15초).
맨 `sleep` 은 0.1초 폴링 간격뿐이다. 서버는 `test_server.Server`(in-process) 를 그대로 쓰고 `src/` 는 건드리지
않는다.

## 1. `tests/test_web.py` — 정적 서빙

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | `GET /` | 200 · `Content-Type` 이 `text/html` 로 시작 · `Cache-Control` 토큰에 `no-cache` 있고 `no-store` 없음 · `X-Content-Type-Options: nosniff` · `Content-Length` = 본문 길이 · 본문 = `importlib.resources` 의 `web/index.html` 바이트 그대로 · `<title>` · `id="hdr"` · `id="queue"` · `id="banner-lost"` · `<script src="/static/app.js"` · `<link rel="stylesheet" href="/static/style.css"` |
| 2 | `/static/app.js` · `/static/style.css` (parametrize) | 200 · `application/javascript` / `text/css` · `nosniff` · 본문 = 패키지 파일, 비어 있지 않음 · `ETag` 는 `sha256(본문)` 앞 16 hex(따옴표 허용) · 같은 `If-None-Match` → **304 + 빈 본문 + 같은 ETag** · 다른 값 → 200 + 본문 |
| 3 | 그 밖의 `/static/*` → 404 JSON | `/static/nope.js` · `/static/` · `/static` · `/static/index.html` · `/static/app.js/extra` · `/static/app.js.map` 전부 404 이고 본문은 `{"error": "<str>"}` |
| 4 | 경로 탈출 → 400 | `/static/../pyproject.toml` · `/static/../../etc/passwd` · `/../static/app.js` · `/static//app.js` · `/static/\app.js` 전부 400 JSON |
| 5 | HEAD | `/` · 두 정적 파일: 200 · 본문 없음 · `Content-Length`·`Content-Type`·`ETag` 가 GET 과 같다 |
| 6 | 쓰기 메서드 → 405 | `POST /` · `PUT /static/app.js` · `POST /static/style.css` · `DELETE /` |
| 7 | `read_auth = basic` | 토큰 없는 `GET`·`HEAD` 의 `/` 와 두 정적 파일은 401 + `WWW-Authenticate: Bearer` + JSON 한 줄(로그인 HTML 아님), 토큰 있으면 200. `/api/health` 는 그대로 200 |
| 8 | `importlib.resources` | `files("remote_ci_monitor") / "web" / {index.html, app.js, style.css}` 가 파일이고 비어 있지 않다(소스 트리 기준) |
| 9 | wheel 패키징 | 저장소 루트에서 `python -m pip wheel . -w tmp/dist --no-deps -q` → `remote_ci_monitor-*.whl` 하나 → 안에 `remote_ci_monitor/web/{index.html, app.js, style.css}` 가 있고 **바이트가 소스 트리와 같다**. pip 가 실패하고 stderr 에 `Could not` / `No matching` / `No module named pip` 가 있으면 skip(격리 빌드가 hatchling 을 내려받으므로 오프라인은 실패가 아니다) |

## 2. `tests/test_web_browser.py` — headless Chrome

Chrome 찾기: `RCM_CHROME` → `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` → PATH 의
`google-chrome` · `google-chrome-stable` · `chromium` · `chromium-browser`. 없으면 모듈 전체 skip.

배치(`scene` 픽스처): `Server(workers=True)` 에 `app.sampler` 를 **부를 때마다 2초 전 표본을 새로 만드는**
스텁으로 덮고(CPU busy 21.0 · 표본 이름 `macmini`), alice 의 `slow` 잡을 올려 `running`(phase executing) 까지
기다린 뒤 bob 의 `slow` 잡을 다른 `tree_hash` 로 올려 `queued` 로 둔다. `/api/status` 가 `running(lane 1,
reason running)` · `queued(position 1, reason waiting_for_lane, confidence low)` · `hosts[0].cpu.busy == 21` 을
보일 때까지 5초 마감으로 폴링한 뒤에야 Chrome 을 연다. teardown 에서 두 잡을 취소하고 서버를 닫는다.

Chrome 은 `--remote-debugging-pipe`(CDP, fd 3/4, 표준 라이브러리만) 로 몬다. 페이지는 **`/?poll=1`** 로
연다(앱이 SSE 를 열지 않고 10초 폴링만 하는 모드 — 열린 `EventSource` 는 headless Chrome 의 종료를 막는다).
「두 잡의 `#queue [data-job=<id>]` 와 `#host .meter[data-metric="cpu"]` 가 다 있다」가 참이 될 때까지(15초)
기다린 뒤 `outerHTML` 을 읽는다. 구조 질문(`.conf` 텍스트, 띠의 hidden, `innerText`)은 직렬화 문자열을
파싱하지 않고 같은 세션에서 `querySelector` 로 한다. 라벨 비교는 `textContent` 로 한다 — `innerText` 는 CSS
`text-transform: uppercase` 를 반영해 `YOUR JOBS` 가 된다. 캡처 뒤 running 잡이 아직 `running` 인지 다시
확인해 「20초 안에 못 찍었다」를 명확한 메시지로 만든다.

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 데스크톱(1240×900) DOM | `data-job="<running>"` · `data-job="<queued>"` · `table.q` · `td.reason` · 텍스트 `running · lane 1` · `1st in line` · `waiting for lane` · `#queue .conf` 중 하나에 `low`(preset/default) 이고 `undefined`/`NaN`/`null` 없음 · `#summary [data-c="23"/"24"/"25"]` 의 `textContent` 에 `Your jobs` / `Not moving` / `Host pressure` · `data-metric="cpu"` · 보이는 텍스트에 `21%` · `#banner-lost` 태그에 `role="alert"` 와 `hidden` 이 있고 계산된 display 가 none · 보이는 텍스트에 `lost connection`(대소문자 무시) 없음 · `#hdr #summary #queue #host #recent #drawer #toast` 존재 · `dialog#tok-dialog` · `dialog#cancel-dialog` · 비어 있지 않은 `<title>` · 보이는 텍스트에 `undefined`/`NaN` 없음 · Chrome 이 열려 있는 동안 `server.sse_connections == 0`(`?poll=1` 이 SSE 를 안 연다) |
| 2 | 모바일(390×844) | `window.innerWidth ≤ 720`(카드 레이아웃 구간) · 두 잡 id · 보이는 텍스트에 `1st in line` · `21%` · `#summary` 의 `textContent` 에 세 라벨 · `lost connection` 없음. 레이아웃은 CSS 라 내용만 본다 |
| 3 | 스크린샷(1240×1400) | `Page.captureScreenshot` PNG 를 `RCM_SHOT_DIR`(없으면 pytest tmp)의 `queue.png` 에 쓰고 `screenshot: <path>` 를 출력. 파일이 있고 10 KB 초과(빈 페이지는 ~3 KB) |

### `--dump-dom` 을 쓰지 않는 이유 (2026-09-05 macOS · Google Chrome 152.0.7977.76 실측)

workplan §6 은 `chrome --headless=new --dump-dom --virtual-time-budget=6000` 을 제안했다. 먼저 같은 모양의
합성 페이지(`fetch('/api/status')` + 열린 `EventSource`) 로, 다음엔 **진짜 앱을 `?poll=1`(SSE 없음) 로** 재 봤다:

| 페이지 | 플래그 | 결과 |
|---|---|---|
| 합성 | `--dump-dom` 만 | 1.6초 만에 끝나지만 **`load` 시점 DOM**(`fetch` 응답 전, 타이머 전) 을 찍는다 |
| 합성 | `--dump-dom --virtual-time-budget=N` | SSE 가 있든 없든 25초 넘게 끝나지 않는다 |
| 합성 | `--dump-dom --timeout=N` | 마찬가지로 끝나지 않는다(한 번은 1.5초에 `load` 시점 DOM 이 나왔다 — 재현 안 됨) |
| 진짜 앱 `?poll=1` | `--virtual-time-budget=6000 --timeout=20000 --dump-dom` | 60초 넘게 끝나지 않는다 |
| 진짜 앱 `?poll=1` | `--dump-dom` 만 | 60초 넘게 끝나지 않는다 |
| 진짜 앱 `?poll=1` | `--virtual-time-budget=6000 --screenshot=… --window-size=1240,1400` | 60초 넘게 끝나지 않는다 |
| 진짜 앱 `?poll=1` | CDP(`--remote-debugging-pipe`) | 조건을 기다린 뒤 outerHTML · 스크린샷, 테스트당 3~5초, 종료 코드 0 |

그래서 CDP 로 간다(코디네이터가 요청한 `--virtual-time-budget` 5000~8000 + 60초 타임아웃은 이 기계에서 매번
타임아웃으로 끝났다). 동시에 Chrome 을 여러 개 띄우면 멈추는 현상이 있었으므로(원인 미확인) 테스트마다 Chrome
하나만 띄우고 끝날 때까지 닫는다. `pytest -n`(xdist) 으로 병렬 실행하면 이 가정이 깨질 수 있다.

## 3. 테스트하지 않는 것 (이유)

| 시나리오 | 이유 |
|---|---|
| `web/` 파일이 없을 때 404 + `web assets missing` (§1) | `importlib.resources` 를 갈아끼워야 해 구현(모듈 경로·캐시 여부)에 결합된다. 있는 걸 없다고 하지 않는지는 시나리오 1·2·8·9 가 본다 |
| 정적 파일의 `Cache-Control` 값 | 명세가 `/` 에만 `no-cache` 를 정했다. ETag 재검증이 의미 있으려면 `no-store` 는 아니어야 하지만 단정하지 않는다 |
| `POST /static/nope.js` 가 404 인지 405 인지 · 401 과 405 의 우선순위 | 명세에 없다. 있는 파일에 대한 쓰기 메서드만 405 로 본다 |
| 브라우저에서 `Lost connection` 띠(항목 18) | `/api/status` 가 상대 경로라 서버를 내리면 페이지 자체가 안 열리고, `file://` 로 열면 API 가 없다. Chrome 152 의 virtual time 은 끝나지 않는다. 30초 무응답 → lost 전이는 Node 의 `rcm.connection` 상태기계 테스트가 덮는다 |
| 브라우저에서 `stale` 배지 · 다크 모드 · 토큰 다이얼로그 · 로그 서랍 · 취소 다이얼로그 | 상호작용·시간 조작이 필요하다. 문구·분기는 Node 순수 함수 테스트, 라우트는 M0/M1 서버 테스트가 덮는다 |
| 모바일 **레이아웃**(카드 3줄) | CSS 미디어쿼리 결과를 DOM 텍스트로 단정할 수 없다. 뷰포트 폭과 내용만 본다. 시각 확인은 스크린샷과 오너의 폰(Tailscale) |
| `read_auth = basic` 에서 브라우저가 실제로 페이지를 여는지 | 아래 「모호점」 참고 — 열 수 없다 |

## 4. 명세가 모호해 테스트가 정한 것 (구현이 맞춰야 하는 값)

- `ETag` 는 `sha256(본문)` 의 앞 16 hex. 강한 ETag 따옴표(`"…"`)는 있어도 없어도 되지만 클라이언트가 받은 값
  그대로 `If-None-Match` 로 보내면 304 여야 하고, **304 에도 `ETag` 가 실린다**(`/api/status` 의 304 와 같은 모양).
- `X-Content-Type-Options: nosniff` 는 `/` 뿐 아니라 두 정적 파일에도 붙인다(브라우저가 JS MIME 을 강제하는
  조건).
- `GET /` 의 본문은 패키지 안 `web/index.html` 과 **바이트 단위로 같다**(템플릿 치환 없음). 정적 파일도 같다.
- `index.html` 은 `<script src="/static/app.js"` 와 `<link rel="stylesheet" href="/static/style.css"` 를 이 속성
  순서로 담는다(추가 속성은 그 뒤에 와도 된다).
- 404 · 400 · 405 · 401 의 본문은 전부 JSON `{"error": "<str>"}`(M0 규칙). 401 은 `WWW-Authenticate: Bearer`.
- `/static/index.html` · `/static` · `/static/` 은 404 다(index 는 `/` 로만).
- `HEAD` 는 GET 과 같은 `Content-Length`·`Content-Type`·`ETag` 를 주고 본문은 없다. `POST/PUT/DELETE` 는 405.
- wheel 안의 세 파일은 소스 트리와 바이트가 같다.
- 브라우저 DOM: 대기 행의 `.pos` 텍스트는 `1st in line`, Reason 은 `waiting for lane …`, 실행 행 Reason 은
  `running · lane 1`, 신뢰도 배지 `.conf` 텍스트는 `low · preset` 또는 `low · default`(테스트 프리셋에
  `expected_seconds` 가 없으므로 실제로는 `default` 가 나온다), 호스트 CPU 는 `21%`, 요약 라벨은 `Your jobs` ·
  `Not moving` · `Host pressure`(정확히 이 대소문자), 연결이 살아 있으면 `#banner-lost` 는 `hidden` 속성.
- `#queue [data-job=<id>]` 는 큐 행이 그려졌다는 신호로 쓴다(펼침 행 `tr.expanded[data-job]` 이 하나 더 있어도
  된다). `#host .meter[data-metric="cpu"]` 는 호스트 카드가 그려졌다는 신호다.

### 오너 확인이 필요한 모호점

- **`read_auth = basic` 이면 브라우저로 UI 를 열 수 없다.** 명세대로 `/` 와 `/static/*` 에 `read_auth` 를
  적용하면 토큰 없는 첫 요청이 401 이라 `index.html` 자체가 안 온다 — `<script src>` 는 `Authorization` 헤더를
  못 붙인다. 결정 F 의 「토큰 없이 `/api/status` 가 401 이면 버튼은 `Read auth required`」는 페이지가 열린
  뒤의 얘기다. 테스트는 명세(401)를 따른다. 정적 파일은 열어 두고 API 만 막는 쪽이 맞다면 시나리오 7 을 뒤집는다.
- 기존 `tests/test_server.py::test_hardening_404_405_400_413` 의 `GET /` 단언은 병행 작업에서 `<!doctype html>`
  로 이미 바뀌었다(이 문서의 테스트는 그 파일을 건드리지 않는다).
