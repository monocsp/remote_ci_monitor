# Codex 크로스리뷰 — 웹 큐 화면 목업·기획 (2026-09-04 오후)

- 리뷰어: Codex CLI 0.153.0 · gpt-5.5 · `codex exec --sandbox read-only` · 렌더링 이미지 2장 첨부
- 대상: `docs/wireframes/web-queue.html` v1.1(22개 주석) + PLAN.md v2
- 결론: 방향은 맞지만 첫 화면 우선순위가 뒤집혀 있다 — 「내 잡·왜 안 움직이나·호스트 압력」을 위로.

## 반영 (v1.2)

| Codex 지적 | 한 일 |
|---|---|
| 반드시 1 내 잡 요약 | 큐 위 요약 줄 「Your jobs」(23) + 행에 you 배지·파란 선 |
| 반드시 2 Reason 열 | `Reason` 열 신설(11), 값 7종 정의, 스키마 `reason` 제안 |
| 반드시 3 열 순서 | `# · Job · Requester · Reason · Elapsed/wait · ETA · Source` |
| 반드시 4 Remaining+ETA | `09:57 · in 5:10` + 신뢰도 배지(10) |
| 반드시 5 상태 모양 분리 | uncommitted(빈 원+테두리) · blocked(⛓ 점선) · stale(빗금) · overdue(굵은 바) · cancelled(■) · timed out(⏱) |
| 반드시 6 uploading 구분 | 점선 필 안에 진행 막대(6) + upload stalled 변형(31) |
| 반드시 7 세로 스텝 목록 | 두 열 목록 기본, 라벨 없는 요약 막대(12) |
| 반드시 8 호스트를 위로 | 요약 줄 「Host pressure」(25) + 호스트 카드를 최근 완료 위로(16) |
| 반드시 9 변형 추가 | no samples yet(26) · worker down(27) · paused(28) · token rejected(29) · cancelling(30) · upload stalled(31). 「read auth basic」은 브라우저 기본 프롬프트라 디자인 대상이 아니어서 제외 |
| 반드시 10 log_tail 인증 | 13 과 PLAN 반영 제안에 `GET /jobs/{id}` 포함 명시 |
| 좋으면 1~10 | 시간대를 ETA 열 머리로(3) · 토큰 상태 버튼(4) · 최근 5건+더 보기(14) · Estimates 접힘(15) · `uncommitted`(8) · 모바일 2줄에 Reason(21) · 신뢰도 high/med/low(10) · lost 문구 분리(2·14) · 키보드 규칙(4절) · GPU 메모리 텍스트만(16) |
| 그대로 둘 것 5개 | 유지 |
| 오너 질문 4개 | Codex 추천을 병기(6절). 결정은 오너 |

## 프롬프트

너는 이 프로젝트의 디자인·기획 크로스리뷰어다. 파일을 수정하지 말고 읽기만 해라. 답은 한국어로, 결론 먼저, 근거는 짧게. 추상적인 조언 말고 **구체적으로 무엇을 어떻게 바꾸라**고 써라.

## 왜 만드나 (배경)
빌드 머신(Mac mini M4 한 대)에 여러 컴퓨터의 사람·Claude Code 세션이 CI·QA 잡을 던진다. 지금은 GitHub Actions dispatch 위에서 돌리는데 「내 차례가 언제 오는지」「앞 잡이 걸린 건지」「머신이 버거운지」「끝났는지, 성공인지」를 한 화면에서 못 본다. 그래서 **도구가 큐와 실행을 직접 소유하는 로컬 잡 서버**를 만든다(GitHub 비의존). 세션은 `rcm run <preset>` 으로 작업 트리를 올리고 `rcm wait` 로 결과를 종료 코드로 받는다. 정본 계획서는 작업 디렉터리의 `PLAN.md`(v2) — 끝까지 읽어라. 특히 「무엇을 하나」「잡 모델」「큐 규칙」「진행 — 스텝 마커」「보안」「/api/status 스키마 v1」「웹 UI (M2)」「fail-open 금지」.

## 지금 검토할 것 — 웹 큐 화면(M2 의 첫 화면)
- HTML 목업 + 주석 기획: `docs/wireframes/web-queue.html` (소스를 읽어라. 목업은 `/api/status` 스키마 v1 값으로 그렸고, `data-c="N"` 요소가 기획 항목 N 과 대응한다. 기획 항목 22개는 같은 파일의 `<article class="spec">` 들이다.)
- 렌더링 이미지 2장(다크·라이트)을 첨부했다. 이미지는 화면 위쪽(목업 + 변형 상태)만이고 기획 항목 본문은 소스에서 읽어라.

## 이 화면이 구현하는 것 (제약)
- 빌드 도구 없는 정적 HTML+JS, 런타임 의존성 0. 처음 `GET /api/status` 한 번, 이후 `GET /events`(SSE)로 부분 갱신, 끊기면 10초 폴링.
- 읽기 인증 none(Tailscale/LAN 전제). 로그 tail·로그·취소만 토큰(localStorage, URL 금지).
- UI 문자열은 영어. 모바일 한 열. 다크/라이트.
- fail-open 금지: 조회 실패·빈 값·모름을 전부 다른 모양으로. 모르는 숫자는 null → "—".
- 사용자는 대부분 「내 잡이 몇 번째고 언제 끝나나」와 「왜 안 움직이나」를 보러 온다. 두 번째 사용자는 「머신이 지금 버거운가」.

## 검토해 달라는 것
A. **정보 위계**: 위 두 질문에 5초 안에 답이 되는가. 안 되면 무엇을 위로 올리고 무엇을 내릴지.
B. **상태 부호화**: 상태 필(running/queued/uploading/succeeded/failed/cancelled/timed out/lost)·dirty·overdue·blocked·stale 의 색·모양·문구가 서로 구분되고 색맹에도 읽히는가. 색만으로 구분하는 곳을 찾아라.
C. **큐 표**: 열 구성·순서·밀도. 빼야 할 열, 합칠 열, 없는 열. ETA 근거(measured n=7 / preset / default) 표기 방식.
D. **펼친 진행 블록**: 스텝 N/M · 타임라인 막대 · 로그 tail · 액션 버튼의 배치. 타임라인이 실제로 유용한가, 더 나은 표현이 있는가(예: 세로 목록).
E. **머리(서버·워커·갱신·시간대·토큰)**: 과한가, 빠진 게 있는가.
F. **최근 완료·중앙값·호스트 카드**: 필요한 정보인가, 배치가 맞는가. GPU 막대는 Apple Silicon 통합 메모리라 total 이 없다 — 표현이 맞는가.
G. **변형 상태 6개**(빈 큐·연결 끊김·stale·초과·모바일·조회 실패): 빠진 상태가 있는가(예: 서버 시작 직후 표본 없음, 워커 죽음, 토큰 거부, 큐 일시정지, 잡 취소 진행 중, 업로드 끊김).
H. **기획 항목 22개의 본문**: PLAN.md 와 어긋나는 규칙, 모호한 문장, 빠진 데이터 필드. 「PLAN 반영 제안」 5개가 타당한가.
I. **접근성·모바일**: 키보드 포커스, 대비, 720px 카드에서 잃는 정보.
J. 오너에게 묻는 4개(파일의 「6. 오너에게 묻는 것」)에 대한 네 추천 답.

## 답 형식
1. 결론 한 단락.
2. **반드시 고칠 것** — 번호 · 무엇을 · 어떻게 (최대 10개, 중요한 순)
3. **고치면 좋은 것** — 같은 형식 (최대 10개)
4. **그대로 둘 것** — 네가 바꾸지 말라고 보는 선택과 이유 (최대 5개)
5. 오너 질문 4개 각각 추천 답 + 한 줄 이유
6. PLAN.md 에 반영할 문장 (있으면)

## 리뷰 원문

결론: 방향은 맞지만 첫 화면의 우선순위가 조금 뒤집혀 있다. 지금은 “큐 전체를 자세히 보는 화면”이고, 실제 5초 질문인 “내 잡은 몇 번째/언제 끝나나/왜 안 움직이나”에는 한 번 더 찾아야 답한다. 큐 위에 `My jobs / Blocking / Host pressure` 요약을 올리고, 표에서는 소스·중앙값·타임라인의 비중을 낮춰야 한다.

**반드시 고칠 것**

1. `내 잡`을 위로 올려라.  
   토큰이 있으면 큐 헤더 바로 아래에 `Your jobs: #4 queued · ETA 09:59 · waiting behind #412` 같은 1줄 요약을 넣어라. 토큰이 없으면 생략하지 말고 `Add token to highlight your jobs`를 작게 둬라.

2. “왜 안 움직이나” 열을 추가하라.  
   현재 `blocked by #409`가 key 칸 아래에 숨어 있다. `Reason` 열을 만들고 `running`, `waiting for lane`, `blocked by #409 · devices`, `uploading 30/48 MB`, `overdue #412`를 한곳에 표시하라.

3. 표 열 순서를 바꿔라.  
   추천: `# / State` → `Job` → `Requester` → `Reason` → `Elapsed / Wait` → `ETA` → `Source`.  
   `Source`는 중요하지만 첫 판단 정보가 아니므로 오른쪽이나 두 번째 줄로 내려라.

4. `Remaining`과 `ETA`를 합쳐라.  
   사용자는 잔여보다 완료 시각을 먼저 본다. `ETA 09:57 · in 5:10`처럼 한 칸에 쓰고, 아래에 `measured n=7`을 작은 배지로 둬라.

5. 상태 색을 더 분리하라.  
   지금 `dirty`, `blocked`, `stale`, `overdue`, `cancelled`, `timed out`가 모두 warn 계열이라 섞인다.  
   `dirty`는 노란 점/`uncommitted`, `blocked`는 사슬 아이콘/테두리, `stale`은 빗금 배경, `overdue`는 왼쪽 굵은 바+`over by`, `cancelled`는 정지 아이콘, `timed out`은 시계 아이콘으로 분리하라.

6. `queued`와 `uploading`을 회색 하나로 두지 마라.  
   `uploading`은 움직이는 진행률/업로드 아이콘을 상태 필 자체에 넣어라. 끊긴 업로드 상태도 별도 변형으로 필요하다.

7. 가로 타임라인을 기본 표현으로 쓰지 마라.  
   현재 라벨이 겹치고, 막대 길이가 “전체 진행률”처럼 오해된다. 기본은 세로/스택형 목록으로 바꿔라: `done analyze 12s`, `done deps 21s`, `running test 51s`, `next build-web`. 가로 막대는 선택적 미니 요약만 남겨라.

8. 호스트 카드를 최근 완료보다 위로 올려라.  
   두 번째 사용자는 “머신이 버거운가”를 보러 온다. 큐 바로 아래 또는 큐 헤더 오른쪽에 `CPU 21% · Mem 58% · GPU 13% · load 3.48/10` 한 줄을 먼저 보여주고, 상세 프로세스는 아래로 내려라.

9. 변형 상태를 더 추가하라.  
   반드시 추가할 상태: `server just started · no samples yet`, `worker down`, `queue paused`, `token rejected`, `cancelling`, `upload stalled`, `all workers idle but queue blocked`, `read auth required/basic`.

10. PLAN과 로그 인증 스키마를 맞춰라.  
   `GET /jobs/{id}`가 “없음 인증 + log_tail”로 되어 있어 보안 규칙과 충돌한다. `log_tail`은 토큰 요청에만 포함하거나 별도 인증 라우트로 빼라.

**고치면 좋은 것**

1. 헤더에서 시간대는 작게 내려라.  
   `Asia/Seoul`은 툴팁이나 설정 메뉴 위치면 충분하다. 헤더 공간은 `worker down`, `paused`, `last error`에 써라.

2. 토큰 박스는 상태형 버튼으로 줄여라.  
   `token alice-laptop ✓`보다 `alice-laptop` + 키 아이콘이 낫다. 거부 상태는 `Token rejected`를 같은 자리에서 빨간 배지로 보여라.

3. `Recent`는 5건만 보이고 나머지는 접어라.  
   첫 화면에서는 실패/타임아웃만 강하게 보이면 된다. 성공 이력 8건은 밀도가 높다.

4. 중앙값 줄은 기본 노출을 줄여라.  
   `medians`는 ETA 설명에는 좋지만 주 사용자 질문은 아니다. ETA 배지 툴팁이나 `Estimates` 접힘 영역으로 보내라.

5. `dirty` 문구를 바꿔라.  
   `+dirty`는 git 사용자에게만 즉시 읽힌다. UI 문자열은 `uncommitted`가 더 명확하다. 툴팁에 `tree differs from base sha`를 넣어라.

6. 모바일 카드 1줄에 `Reason`을 넣어라.  
   지금 모바일은 ETA는 보이지만 왜 대기 중인지 3줄까지 내려간다. `#3 queued · blocked · ETA 10:05`처럼 첫 줄에 넣어라.

7. ETA 신뢰도 표기는 단어보다 등급을 붙여라.  
   `measured n=7`, `preset`, `default` 앞에 `high/medium/low` 또는 아이콘을 붙여라. 예: `high · measured n=7`, `low · default`.

8. `lost`와 연결 끊김을 문구로 확실히 분리하라.  
   `lost`는 잡 결과, `Lost connection`은 화면 연결 상태다. 최근 완료의 `lost`에는 `job lost after server restart`처럼 써라.

9. 키보드 포커스 설계를 명시하라.  
   행 펼침, 로그, 취소, 워커 잡 번호, blocked 링크는 전부 `button/a`이고 Enter/Space 동작을 문서에 넣어라.

10. GPU 메모리는 비율 막대로 보이지 않게 하라.  
   Apple Silicon은 total이 `null`이므로 `GPU 13% · memory in use 0.6 GB` 텍스트가 맞다. total 없는 메모리 막대나 퍼센트처럼 보이는 표현은 금지하라.

**그대로 둘 것**

1. 빈 큐와 조회 실패를 완전히 다른 모양으로 둔 선택은 맞다. fail-open 금지의 핵심이다.

2. SSE 후 폴링 폴백과 `updated Ns ago`를 항상 보이는 선택은 유지하라. 화면 신뢰도를 판단하는 최소 장치다.

3. 실행 중 행을 기본 펼침으로 두는 방향은 맞다. 단, 표현은 가로 타임라인보다 목록형이 낫다.

4. 로그와 취소에만 토큰을 요구하는 UX는 맞다. 읽기 none 전제와 보안 균형이 좋다.

5. `pools[]` 축을 유지하는 것은 맞다. 지금은 하나여도 M5 확장 비용을 줄인다.

**오너 질문 추천 답**

1. 레인 1이면 워커 필을 접고 그룹 열은 숨겨라.  
   이유: 레인 1에서 그룹 대기는 사용자가 행동할 수 있는 별도 원인이 아니다.

2. 실행 중 행은 전부 펼쳐라.  
   이유: 레인 2 이상에서는 어느 잡이 막혔는지 비교해야 한다. 사용자가 접은 상태만 기억하면 된다.

3. 최근 완료는 기본 8건, 설정값 `recent_count`로 가라.  
   이유: 시간 기준 24시간은 조용한 서버와 바쁜 서버에서 정보량이 너무 달라진다.

4. 토큰 입력은 M2에 넣어라.  
   이유: 모바일에서 로그 확인과 취소가 실제 운영 행동이고, CLI로만 두면 웹의 문제 해결 능력이 반쪽이 된다.

**PLAN.md에 반영할 문장**

- `/api/status`의 `queue[].log_tail`은 인증된 토큰 요청에만 포함한다. 토큰이 없거나 권한이 없으면 `log_tail: null`이며 UI는 로그 영역에 `Add a token to see the log`를 표시한다.
- M2 웹 UI는 큐 상단에 `Your jobs`, `Blocking reasons`, `Host pressure` 요약을 먼저 표시한다. 토큰이 없으면 `Your jobs` 대신 토큰 추가 안내만 표시한다.
- 큐 행은 `reason` 표시를 가진다. 값은 `running`, `waiting_for_lane`, `blocked_by_group`, `uploading`, `upload_stalled`, `overdue_dependency`, `paused` 중 하나로 렌더링할 수 있어야 한다.
- Apple Silicon GPU 메모리는 total이 없으므로 비율 막대를 그리지 않고 `memory in use N GB` 텍스트로만 표시한다.