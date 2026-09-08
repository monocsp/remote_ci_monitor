# M5d 작업 명세 — 웹 UI: 한국어 기본 + 정보 위계

> 오너 요청(2026-09-08): 「기본 한글로. 우측 상단에서 영어·한국어를 고를 수 있게. 디자인은 어디부터
> 어디까지 손볼지 모르겠으니 코덱스에게 화면을 주고 물어보고, 레퍼런스를 찾아 중요한 정보에 위계를
> 주도록.」
>
> 입력 셋: `docs/reviews/2026-09-08-codex-web-ui-design.md`(코덱스가 화면 다섯 장을 보고 낸 진단) ·
> `docs/reviews/2026-09-08-ui-hierarchy-research.md`(위계·상태 표기·한글 타이포 1차 자료 조사) ·
> 문자열/구조 전수 조사(격리 에이전트, 이 문서 §2 에 요약).
>
> 바꾸지 않는 것: 빌드 도구 0 · 외부 자산 0 · 프레임워크 0(정적 파일 셋 그대로) · 스키마 v1 ·
> 정직성 규칙(모르면 `—`, ETA 에 confidence, `lost` 는 조용히 되살리지 않는다) · 읽기는 토큰 없이,
> 쓰기는 토큰.

## 1. 이 화면이 답해야 하는 것

빈도순이다. 위계는 이 순서를 따른다.

1. **내 잡 끝났나, 통과했나** — 4분 전에 낸 사람이 탭을 흘긋 본다.
2. **왜 안 움직이나** — 워커가 죽었나, 뭐가 걸렸나, 머신이 버거운가, 큐가 멈췄나.
3. **내 앞에 누가 있고 언제 내 차례인가** — 순번과 정직한 ETA.
4. (드물게) 빌드 머신이 건강한가 — 오너.

조사에서 가져온 근거: 색과 모양은 **범주만** 전달하고 크기·순서를 전달하지 못한다. 순서는 세로
위치로, 대기는 길이로 준다. 그리고 **설명 없는 기다림이 설명된 기다림보다 길게 느껴진다** — 「왜
안 움직이는지」를 상태 문자열 **안에** 넣는다(툴팁 금지).

## 2. 지금 상태 (전수 조사 결과)

- **다국어 장치가 전혀 없다.** 문자열은 전부 인라인 리터럴이고 대부분 `+=` 로 `innerHTML` 에 붙는다.
- **순수 함수 층(`app.js` 1–641)이 사용자 문장을 만든다.** `reasonText` · `queueHeader` · `yourJobs` ·
  `recentLine` · `progressHead` · `workerPills` · `poolHeader` · `hostCards` · `confidenceBadge` ·
  `etaText` · `elapsedText` · `headerNoteKind` · `transitionsLine`. 이 층이 곧 `tests/web/*.test.js`
  가 잠근 계약이다 — **약 200개의 영어 문자열 단언**이 여기 걸려 있다.
- **정적 문자열은 다시 그려지지 않는다.** `index.html` 의 요약 라벨 셋, 섹션 제목 셋, 토큰 대화상자
  전체, `Keep`, 모든 `aria-label`, `<title>`, `lang="en"`.
- **서버 문장은 페이지가 번역할 수 없다.** `recent[].summary`(최근 목록에서 가장 넓은 칸) ·
  `*_error` 넷(파이썬 예외 문자열) · `server.last_error` · 워커 `state` · `estimate.source` ·
  `confidence` 의 `group wait`. 결정 36 대로 **서버 문장은 서버의 것**으로 두되, 어디가 그런지 이
  명세가 못 박는다.
- **함정 넷**(고치지 않으면 조용히 깨진다):
  1. 이유 칸이 `#<id>` 를 **문자열 치환**으로 링크로 바꾼다(`app.js:1052`). 번역이 `#412` 토큰을
     흘리면 `String.replace` 는 조용히 아무 일도 안 한다 — 잡 링크가 사라진다.
  2. `yourJobs` 가 **자기 영어 출력을 다시 파싱**한다(`reasonText(...).split(" · ")[0]`, 그리고
     `"unknown"` 리터럴 비교).
  3. `recentLine` 이 **서버 요약을 ` · ` 로 쪼개** 앞부분만 굵게 한다 — 구분자 규약이 클라이언트와
     서버에 걸쳐 있다.
  4. 상태 enum 이 **CSS 클래스이자 정렬 키**다(`.pill.timed_out`, `ACTIONABLE.indexOf`). 값을
     번역하면 스타일과 「안 움직이는 것」 순위가 깨진다 — **표시 경계에서만** 바꾼다.
- **CSS 쪽 위험**: `--mono` 스택에 한글 폰트가 없는데 동적 문자열 대부분이 mono 다 · 고정 px 그리드가
  영어 길이에 맞춰져 있다(`.rrow` 66px 소요 칸 등) · 데스크톱은 `white-space: nowrap` 이고 720px
  아래에서만 풀린다 · `text-transform: uppercase` 는 한글에서 아무 일도 안 한다(그 위계가 사라진다) ·
  `truncate()` 가 표시 너비가 아니라 UTF-16 단위를 센다(한글은 두 배 넓다).

## 3. 언어 전환 설계

### 3.1 계약

- **카탈로그 하나**(`web/i18n.js`): `MESSAGES = { ko: {...}, en: {...} }`, 키는 점 표기
  (`queue.header.jobs`, `reason.waiting_for_lane`). 값은 **함수 또는 문자열**. 매개변수가 있으면
  함수로 받아 어순을 언어마다 자유롭게 짠다 — 영어의 `t += " · 조각"` 누적 방식은 한국어에서 못 쓴다.
- **순수 함수는 언어를 인자로 받는다.** `reasonText(row, now, lang = "en")` 처럼 **기본값 영어**.
  그러면 `tests/web/*.test.js` 200여 단언이 **한 줄도 안 고치고** 계속 통과한다. DOM 층이 현재 언어를
  넘긴다. 새 한국어 단언은 같은 함수에 `"ko"` 를 줘서 따로 추가한다.
- **정적 노드는 키를 단다.** `index.html` 의 번역 대상마다 `data-i18n="…"`(텍스트) ·
  `data-i18n-attr="aria-label:…"`(속성). 전환 시 `applyStatic()` 이 훑어 다시 쓴다. 문서
  `<title>` 과 `document.documentElement.lang` 도 같이 바꾼다.
- **저장과 동기화**: `localStorage['rcm.lang']`. 없으면 `navigator.language` 가 `ko` 로 시작할 때
  한국어, 아니면 **한국어**(결정 36 — 기본이 한국어다. 브라우저가 영어라도 기본은 한국어이고, 영어는
  고르는 것이다). `?lang=ko|en` 이 있으면 그것이 이기고 저장한다. `storage` 이벤트에 세 번째 가지를
  더해 다른 탭도 따라오게 한다. **해시에는 넣지 않는다** — `#/jobs/N` 정규식이 거부하고 서랍을 닫을 때
  덮어쓴다.
- **전환은 `render()` 한 번**으로 끝난다. 다섯 렌더 함수가 각자 `innerHTML` 을 새로 만들기 때문이다.
  추가로 해야 하는 일: `applyStatic()`(정적 노드) · 열려 있는 대화상자·서랍 다시 쓰기 · 1초 틱이
  읽는 문자열은 **호출 시점에** 언어를 읽게 하기(렌더 시점에 굳히면 안 된다).
- **번역하지 않는 것**(결정 36): 프리셋 이름 · 잡 키 · 요청자 라벨 · 커밋 해시 · ref · 저장소 주소 ·
  동시성 그룹 이름 · 스텝 이름(잡이 찍은 것) · 로그 · `rcm run …` 재실행 명령 · 상태 enum 값 자체
  (표시 문자열만 바꾼다) · 서버 문장(§2).

### 3.2 어려운 자리

| 자리 | 지금 | 한국어에서 |
|---|---|---|
| 시각 | `Intl.DateTimeFormat("en-US")` 하드코딩, 날짜가 다르면 `Sep 3 · 23:40` | 로케일을 언어에서 받는다. 한국어는 `9월 3일 · 23:40`. `${month} ${day}` 조립을 버리고 언어별 템플릿으로 |
| 순번 | `ordinal()` → `1st/2nd/3rd`, 문장에 끼워 넣음 | `N번째`. 숫자와 조사가 붙으므로 함수와 두 호출부를 함께 바꾼다 |
| 복수 | 아홉 자리, 네 가지 관용, 기존 불일치 하나(`step` 하나는 복수, 하나는 아님) | 한국어는 복수가 없다. 영어 쪽 불일치도 이 기회에 맞춘다 |
| 세션 수 | `N other session(s) is/are waiting` 이 세 곳에 복사됨 | 한 키로 합치고 어순을 언어별로 |
| 대문자 위계 | `text-transform: uppercase` 로 섹션·라벨을 구분 | 한글에는 효과가 없다. **굵기와 색으로 대체**한다(조사: 크기만으로 위계를 주지 마라) |
| 잘라내기 | UTF-16 길이 40·60 | 표시 너비 기준으로. 한글 한 글자를 2로 세는 폭 계산 |

### 3.3 전환 UI

머리줄 오른쪽, 토큰 버튼 **왼쪽**에 둔다. `한국어 / EN` 두 상태 토글 버튼 하나. `aria-pressed` 로
상태를 알리고, 누르면 즉시 반영된다(새로고침 없음). 폭이 좁으면(720px 아래) 글자만 `KO/EN` 으로.

## 4. 위계 다시 세우기

코덱스 진단 + 조사 근거를 합친 결론. **카드를 더 만들지 않는다** — 지금 있는 것을 재배치하고 무게를
다시 준다.

### 4.1 첫 화면 구성

```
머리줄        rcm · 호스트 · 워커 칩 ······· 신선도 · [한국어/EN] · [토큰]
─────────────────────────────────────────────────────────────────────
상태 띠       [ 내 잡 / 지금 도는 잡 ]        [ 큐 상태 ]
              #4 실행 중 · build · 종료 10:19   움직임 / 막힘 / 일시정지 / 워커 down
              스텝 2/4 · (토큰 있으면 로그 꼬리)  가장 오래 기다린 잡 · lane · 갱신 시각
─────────────────────────────────────────────────────────────────────
큐            지금 도는 것
                #4 demo · alice · build · 30초 남음 · med
              다음
                #5 demo · bob · 1번째 · #4 끝나면 시작
              다른 풀
                #6 remote-demo · mac2 에서 실행 중
─────────────────────────────────────────────────────────────────────
최근          결과 목록(압축). 실패한 것만 기본으로 펼침
─────────────────────────────────────────────────────────────────────
호스트        접힌 한 줄 요약. 경고가 있을 때만 펼쳐서 보여 준다
바닥글        버전 · 가동 시간 · 스키마
```

바뀌는 것:

- **호스트를 접는다.** 지금은 화면의 큰 덩어리다. 조사: 머신 비교는 백분율로, 그리고 페이지에서 가장
  작은 글자로. 세 질문 어디에도 1순위가 아니다. 경고(stale · GPU 없음 · 부하 높음)일 때만 펼친다.
- **소스 열을 접는다.** 커밋 해시만 남기고 저장소 주소는 상세로. 모든 행에 같은 URL 이 반복될 이유가
  없다.
- **표를 우선순위 행으로.** 칸을 줄인다. 상태 · 키 · 요청자 · 이유 · ETA 만 1차, 나머지는 펼침.
- **토큰 없는 사람에게 비활성 버튼을 보여 주지 않는다.** 행마다 「토큰을 넣으면 로그가 보입니다」를
  반복하지 말고 상태 띠에 한 번만.
- **최근은 다섯 줄**까지. 나머지는 버튼.

### 4.2 상태 표기 (색에 기대지 않는다)

WCAG 1.4.1. 그리고 **움직임도 상태 채널**이다(Vercel: 종료 아닌 상태만 움직인다).

| 상태 | 모양(CSS 만) | 움직임 | 라벨(ko/en) |
|---|---|---|---|
| 실행 중 | 채운 원 | 맥박 | 실행 중 / running |
| 대기 | 빈 원 | 느린 맥박 | 대기 #3 / queued |
| 업로드 중 | 채운 원 + 진행 막대 | 막대 | 업로드 중 / uploading |
| 취소 중 | 채운 사각 | 맥박 | 취소 중 / cancelling |
| 성공 | 채운 원 + 체크 | 없음 | 성공 / succeeded |
| 실패 | 채운 사각 + 엑스 | 없음 | 실패 · exit N / failed |
| 시간 초과 | 채운 사각 + 시계 | 없음 | 시간 초과 / timed out |
| 취소됨 | 빈 사각 | 없음 | 취소됨 / cancelled |
| 유실 | 빈 사각, 점선 | 없음 | 유실 / lost |
| 워커 down | 빈 사각, 점선 + 경고 | 없음 | 연결 끊김 / down |

- 모든 맥박은 `@media (prefers-reduced-motion: reduce)` 뒤에 둔다.
- 옆에 상태 글자가 있으면 점은 `aria-hidden`.
- **심각도 순서는 정렬 위치로** 준다(실패·막힘이 위). 색은 마지막 채널.
- 의미를 지닌 점·막대는 배경 대비 **3:1**(WCAG 1.4.11).

### 4.3 타이포와 밀도

- **UI 는 시스템 산세리프**, 고정폭은 로그·SHA·명령·잡 키에만. 지금은 거의 전부 mono 라서 서로 싸운다.
  ```
  --sans: system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Apple SD Gothic Neo", monospace;
  ```
  (mono 에도 한글 대체를 넣는다 — 지금은 없어서 한글이 제멋대로 떨어진다.)
- **단계는 다섯**: 12 / 14 / 16 / 20 / 28. 굵기는 400 과 600 둘.
- **한글 본문 줄 간격 1.5–1.6**, 표 한 줄 칸은 1.25–1.35. **자간은 0**(한글 본문에 양수 자간 금지).
- 숫자·소요·순번·백분율에 `font-variant-numeric: tabular-nums`, 숫자 칸 오른쪽 정렬.
- 산문·라벨에 `word-break: keep-all`, **식별자 칸에는 쓰지 않는다**(효과가 없다). 식별자 칸은
  `min-width: 0; overflow: hidden; text-overflow: ellipsis`.
- 밀도는 여백을 늘려서가 아니라 **묶음을 강하게** 해서 지킨다. 묶음 밖 간격 > 묶음 안 간격.
- 고정 px 그리드를 다시 잰다. 한국어 라벨 기준으로.

## 4.5 서버는 문장 대신 코드를 내려보낸다 (오너 결정 37, 2026-09-08)

서버가 만든 영어 문장이 한국어 화면에 그대로 박히는 문제를, **서버가 무슨 일이 있었는지를 코드로
말하고 화면이 자기 언어로 그리는** 방식으로 푼다. 화면·CLI·알림이 같은 사실을 각자의 말로 쓴다.

**스키마는 v1 그대로다 — 키를 더하기만 한다.** 기존 `summary` 는 남는다(CLI 와 알림 훅이 쓰고, 잡이
스스로 찍은 요약도 거기로 온다). 옆에 `summary_code` 와 `summary_args` 를 더한다. **서버가 만든 요약일
때만** 코드가 붙고, 잡이 `::rcm::summary::` 로 찍은 문장에는 **코드가 없다** — 그건 팀이 쓴 문장이라
번역 대상이 아니다. 화면은 코드가 있으면 코드로 그리고, 없으면 문장을 그대로 보여 준다.

### 코드 목록 (서버가 만드는 요약 — 전수)

| 코드 | 인자 | 지금 문장 | 어디서 |
|---|---|---|---|
| `cancelled_before_start` | — | `cancelled before start` | `store.py:906` |
| `server_restarted` | `at` | `server restarted {when}` | `store.py:1000` |
| `server_restarted_during_upload` | — | `server restarted during upload` | `store.py:1014` |
| `upload_abandoned` | `seconds` | `upload abandoned after {span}` | `store.py:1245` |
| `upload_interrupted` | `bytes` | `upload interrupted after {n}` | `server.py:1250` |
| `snapshot_too_big` | `bytes`, `limit` | `snapshot {n} exceeds {limit}` | `server.py:932`, `1200` |
| `snapshot_rejected` | `kind` | `snapshot rejected: {ExceptionType}` | `server.py:1071` |
| `snapshot_blobs_missing` | `count` | `snapshot rejected: {n} blob(s) missing` | `server.py:1080` |
| `workspace_failed` | `detail` | materialize 오류 | `materialize.py` |
| `exit_code` | `code` | `exit {rc}` | `worker.py:127` |
| `cancelled_by` | `by` | `cancelled by {who}` | `worker.py:120` |
| `worker_error` | `detail` | `worker error: {err}` | `worker.py:228` |
| `server_stopped_while_running` | — | `server stopped while running` | `worker.py:104` |
| `worker_stopped_while_running` | — | `worker stopped while running` | `remote_workers.py:450` |
| `worker_failed` | — | `failed on the worker` | `remote_workers.py:453` |
| `worker_restarted_without_job` | `name` | `worker {name} restarted without the job` | `remote_workers.py:66` |
| `worker_unreachable` | `name`, `seconds` | `worker {name} unreachable for {s}s` | `remote_workers.py:67` |
| `cancel_unconfirmed` | — | `worker did not confirm the cancel` | `remote_workers.py:68` |

### 오류 필드

`queue_error` · `hosts_error` · `recent_error` · `medians_error` · `server.last_error` ·
`workers[].error` 는 파이썬 예외 문자열이라 닫힌 집합이 아니다. 각각 옆에 **`*_error_code`** 를 더한다:
`internal_error`(기본) · `database_unavailable` · `sampler_failed` 정도로 시작하고, 원문은 **상세**로
남긴다. 화면은 「큐를 읽지 못했습니다」를 자기 언어로 쓰고, 원문은 접어서 보여 준다(디버깅에 필요하다).

`gpu_note` 도 같은 방식으로 `gpu_note_code`(`no_sampler` · `no_gpu` · `sampler_failed`)를 더한다.

`estimate.confidence` 의 `group wait` 만 예외다 — 이미 CLI 가 그 값을 그대로 찍고 있어서 값을 바꾸면
호환이 깨진다. 값은 두고 **화면에서만** 대응 문자열로 그린다.

### 인자 규칙

`summary_args` 는 **원시 값**을 담는다(바이트 수, 초, 종료 코드, 이름). 포맷은 화면이 한다 — 한국어와
영어의 단위 표기가 다르고, 서버가 `48 MB` 로 굳혀 보내면 화면이 다시 쓸 수 없다.

### CLI 는 그대로 영어다

집안 규칙대로 CLI 는 영어를 쓴다(결정 36). 다만 CLI 도 같은 코드를 읽어 문장을 만들게 해서, 서버와
화면과 CLI 가 **한 곳에서 온 사실**을 그리게 한다. 이 작업의 부수 효과로 서버 코드에 박혀 있던 문장
열여덟 개가 한 표로 모인다.

## 5. PR 순서

**한 PR 에 두 가지를 섞지 않는다.** 섞으면 무엇 때문에 깨졌는지 알 수 없다.

- **M5d-0 — 서버가 코드를 내려보낸다.** `summary_code`/`summary_args` · `*_error_code` ·
  `gpu_note_code` 추가(키 추가만, 스키마 v1 그대로). 서버 안에 흩어진 문장 열여덟 개를 한 표로 모으고,
  CLI 는 그 표로 같은 영어 문장을 만든다. 화면은 아직 안 건드린다 — 이 PR 만으로 기존 화면과 CLI 가
  똑같이 동작해야 한다.
- **M5d-1 — 언어 장치와 한국어 카탈로그.** 시각적 재배치 없음. `i18n.js` · 순수 함수에 `lang` 인자
  (기본 영어) · 정적 노드 `data-i18n` · 전환 버튼 · 저장/동기화 · mono 스택 한글 대체 · `lang` 속성.
  기존 JS 테스트는 그대로 통과해야 한다. 새 테스트: 한국어 카탈로그 단언, 키 누락 없음(양 언어 키
  집합이 같다), `#<id>` 토큰이 두 언어 모두에 남아 있다(§2 함정 1).
- **M5d-2 — 위계.** 레이아웃·타이포·상태 표기. 상태 띠 신설, 호스트 접기, 소스 접기, 칸 줄이기,
  대문자 위계를 굵기로 교체.
- **M5d-3 — 폰과 마무리.** 720px 아래 카드 재배치, `prefers-reduced-motion`, 대비 점검, `?debug=1`
  로 390px·1240px 넘침 확인.

## 6. 테스트

- `tests/web/*.test.js`: **기존 단언은 손대지 않는다**(순수 함수 기본값이 영어라서 그대로 통과).
  파일마다 한국어 블록을 더한다 — 같은 입력에 `"ko"` 를 주고 문장을 잠근다.
- 새 `tests/web/i18n.test.js`: 두 언어의 키 집합이 같다 · 매개변수 함수가 모든 인자를 쓴다 ·
  `#<id>` · `%` · 숫자 자리표시자가 번역에서 사라지지 않는다 · 카탈로그에 없는 키는 던진다(조용히
  `undefined` 를 그리지 않는다).
- `tests/test_web_browser.py`: 지금 13개가 영어 문자열에 걸려 있고, **6개는 부정 단언이라 언어가
  바뀌면 조용히 무의미해진다**(`"lost connection" not in …` 같은 것). 이것들을 **언어를 명시해서**
  다시 쓴다(`?lang=en` 으로 기존 단언을 유지하고, `?lang=ko` 로 한국어 단언을 새로 추가). 무의미해질
  부정 단언은 그 자리에서 고친다.
- `tests/test_web.py`: 구조만 보므로 그대로.
- `scripts/mutcheck.py`: 카탈로그 조회를 잘못 뒤집는 변이 하나를 더한다(예: 언어 결정 순서 뒤집기).

## 7. 완료 기준

1. 처음 여는 사람이 **한국어 화면**을 본다. 우측 상단에서 영어로 바꾸면 즉시 바뀌고, 새로고침해도
   유지되고, 다른 탭도 따라온다.
2. 두 언어 모두에서 `undefined` · `NaN` · 빈 문자열이 화면에 없다(브라우저 테스트가 이미 잡는다).
3. 이유 칸의 `#<id>` 링크가 두 언어에서 동작한다.
4. 첫 화면에서 「내 잡이 끝났나」의 답이 **페이지에서 가장 큰 글자**로 보인다. 호스트 그래프는 접혀
   있다.
5. 상태를 **회색조로 인쇄해도** 구분된다(모양 + 글자).
6. 390px 폭에서 가로 스크롤이 없다. `?debug=1` 이 넘치는 요소를 하나도 보고하지 않는다.
7. 기존 테스트 전부 초록 · 스키마 v1 그대로 · 런타임 의존성 0 그대로.

## 8. 오너 결정 (2026-09-08 확정)

- **결정 37 — 서버는 문장이 아니라 코드를 내려보낸다.** §4.5. 서버를 한국어로 만들지도 않고, 영어
  문장을 화면에 박아 두지도 않는다. 서버는 무슨 일이 있었는지를 코드와 원시 인자로 말하고, 화면·CLI 가
  각자의 말로 그린다. 잡이 스스로 찍은 요약(`::rcm::summary::`)만 예외다 — 팀이 쓴 문장이라 그대로
  보여 준다.
- **결정 38 — 브라우저 언어와 무관하게 기본은 한국어다.** 영어는 오른쪽 위에서 고르는 것이고, 고른
  값은 브라우저에 남는다.
