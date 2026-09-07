# M5b-4 작업 명세 — 다중 풀 표시 마무리 · `rcm check` 풀 행 · 실기

> `docs/m5-workplan.md` 「M5b」의 마지막 PR. M5b-2(프로토콜)·M5b-3(`rcm worker`)이 들어간 뒤. 새 기능은 없다 — 화면·CLI 가 여러 풀과 원격 워커를 **읽히게** 보여 주는 것과 실기 기록.
>
> 바꾸지 않는 것: 풀 하나(기본 풀만)일 때 `rcm top`·웹 화면은 **바이트 단위로 오늘 그대로**(M5b-1·M5b-2 의 GOLDEN 테스트가 잠근다) · 상태 JSON 스키마.

## 1. `rcm top` (`core/render_text.py`)

- 기본 풀 밖의 풀 블록 헤더는 **언제나** 풀 이름을 단다: `queue — empty (pool linux)` · `queue — empty (pool linux · no workers)` · `queue — 2 (pool linux)` · `queue — unavailable: … (pool linux)`. 오늘은 잡이 없고 워커가 있으면 라벨 없이 기본 문구(`rcm run <preset> starts immediately`)가 나와 어느 풀인지 알 수 없다.
- 원격 풀의 「정지/워커 없음」 문구: 서버가 paused 면 `queue — empty (pool linux) · paused`. 워커가 전부 down 이면 `no workers` 라벨이 이미 말한다.
- 원격 풀의 `recent — no completed jobs yet` · `medians: …` · `host …` 줄은 그대로(풀 블록 안에 있으니 라벨 불필요).
- 머리줄의 원격 워커 필(`build-02/1 busy #511`)은 M5b-2 그대로. 워커가 5개를 넘으면 `+N workers` 로 접는다: `build-02/1 busy #511 · build-03/1 idle · +4 workers`(down 은 접지 않는다 — 항상 보인다).

## 2. 웹 (`web/app.js` · `index.html` · `style.css`)

- 풀 블록(`extraPoolsQueueHtml`)은 오늘처럼 **잡이 있는 풀만**(빈 풀은 머리 필로 충분). 단 잡은 없는데 워커가 down 인 풀은 노란 띠(M5b-2 배너)가 말한다.
- 원격 워커의 호스트 표본은 **Host 절**에 카드로: 제목 `build-02 · pool linux`(기본 풀 로컬 카드는 오늘 그대로 · 제목 변화 없음). Recent 절 밑에 붙던 풀별 host 블록(`POOL LINUX · NO WORKERS · NO HOST SAMPLE` 헤더 줄)은 없앤다 — 표본이 없는 원격 풀은 카드를 만들지 않는다.
- `renderQueue` 가 빈 기본 풀 분기에서 `extraPoolsQueueHtml` 을 두 번 부르는 것 정리(결과 동일).
- Your jobs · Not moving · Recent 는 이미 모든 풀을 본다(M5b-1). 변화 없음.

## 3. `rcm check` (`cli.py`)

- 행 `pools`: `default (1 lane)` 뒤에 풀마다 ` · linux (build-02/1 idle)` / ` · linux (build-02 down)` — `/api/status` 의 `pools[].lanes` 와 `server.workers[]` 로 만든다. 원격 워커가 하나도 없으면 행은 `default (1 lane)` 만. FAIL 조건: 어떤 풀의 워커가 **전부** down(`/api/health` 의 `pools_without_workers`).

## 4. 실기 · 문서

- 같은 머신 두 프로세스 실기는 M5b-3 검증이 했다(README 「Verify」 11단계는 M5b-3 에서 적었다). 여기서는 격리 검증이 **웹 화면**(원격 워커 카드 · 풀 블록 · 배너)을 헤드리스 Chrome 스크린샷으로 남긴다: `docs/acceptance/reports/shots/m5b4-*.png`.
- PLAN: M5b 완료 · M5 완료 기준 ④ 달성 기록. CHANGELOG [Unreleased] 에 M5b-4 한 줄. 이어서 dev → main 릴리스 v0.2.0.

## 5. 테스트 배치

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_render_m5b4.py` | A | 원격 풀 빈 큐 헤더 라벨 4종 · paused 문구 · 워커 5개 초과 접기(down 은 안 접음) · 풀 하나 GOLDEN 불변 |
| `tests/web/hosts_pools.test.js` · `tests/test_web_browser.py`(추가 1건) | A | 순수 함수 `hostCards(status)`(카드 목록: 로컬 → 원격, 제목 규칙) · 브라우저: 원격 표본이 Host 절 카드로, Recent 밑에 풀 host 헤더가 없다 |
| `tests/test_cli_m5b4.py` | B | `rcm check` 의 `pools` 행 3종(원격 없음 · idle · down → FAIL) |

규칙: `src/` 금지 · 각자 `docs/m5b4-test-scenarios-<담당>.md`.
