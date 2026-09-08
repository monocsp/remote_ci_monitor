# 정보 위계 · 상태 표기 · 한국어 타이포그래피 조사 (2026-09-08)

> 웹 UI 를 다시 세우기 전에, 「무엇이 위계를 만드는가」를 1차 자료로 확인한 기록. 각 주장에 출처가
> 붙어 있고, 확인하지 못한 것은 **[미확인]** 으로 표시했다. 조사는 격리 에이전트가 했고, 이 문서는
> 그 결과를 저장소 판단 기준으로 남긴 것이다.

## 1. 위계를 만드는 것

- **크기.** 비율을 새로 만들지 말고 검증된 단계를 쓴다. Material 3 의 기본 단계는 11/12/14/16/22/24/
  28/32/36/45/57 px([m3ts], 공식 페이지는 JS 렌더라 미러로 확인 — **[2차]**). 실용적으로는 다섯 단계
  (12/14/16/20/28)만 쓰고 멈춘다. 한국 정부 디자인 시스템 KRDS 는 본문 17px, 하한 16px, 굵기는 400 과
  700 둘만 쓴다([krds]).
- **굵기와 색이 크기가 못 하는 일을 한다.** 크기만으로 위계를 주려는 것이 흔한 실수다. 400 미만 굵기는
  피하고, 400–500 과 600–700 두 단계면 충분하다(**[2차]** — 원문 Medium 이 403). 색 규칙은 1차로 확인:
  회색 8–10 단계, 강조색마다 5–10 단계, 의미색은 빨강 파괴 · 노랑 경고 · 초록 긍정([rui-color]).
- **묶음과 여백.** 간격은 8 의 배수, 컴포넌트 내부만 4([m3-space], **[2차]**). 실제로 중요한 규칙은
  이것이다 — **묶음과 묶음 사이 간격이 묶음 안 간격보다 눈에 띄게 커야 한다.** 안 4px / 밖 16px 는 한
  덩어리로 읽히고, 8/12 는 잡음으로 읽힌다.
- **대비.** WCAG 2.2: 본문 4.5:1, 큰 글자 3:1(큰 글자는 18pt 이상 또는 14pt 굵게, **CJK 는 "동등한
  크기"로 따로 규정**), UI 요소와 의미 있는 그래픽은 3:1([wcag]). **의미를 지닌 상태 점은 그래픽
  객체다 — 배경 대비 3:1 을 지켜야 한다.**
- **위치와 시선.** NN/g 의 아이트래킹은 F 패턴을 목표가 아니라 **나쁜 서식의 증상**으로 본다. 서식이
  잘 된 페이지는 제목을 훑고 본문을 건너뛰는 레이어케이크 패턴을 만든다([fpat]). 처방은 앞에 결론,
  뚜렷한 제목, 시각적 묶음, 핵심어 굵게, 분량 줄이기. **Z 패턴을 뒷받침하는 NN/g 근거는 찾지 못했다
  — 디자이너 민담으로 취급한다 [미확인].**
- **전주의 처리(preattentive).** 2D 위치와 길이는 빠르고 **정확하게** 읽힌다. 면적과 각도는 빠르지만
  부정확하다. **색과 모양은 범주만 전달하고 크기·순서는 전달하지 못한다**([pre]). 그래서 큐 순서는
  세로 위치로, 대기 시간은 막대 길이로, 상태는 색 + 모양 + 글자로 준다. 게이지 · 파이 · 3D 는 쓰지
  않는다. 중요한 것을 튀게 하는 방법은 그 주변 장식을 지우는 것이다([nng8]).
- **점진적 공개.** 첫 화면에 자주 쓰는 것이 전부 있어야 하고, 상세로 들어가는 일이 드물어야 한다.
  **몇 퍼센트를 숨기라는 기준은 없다** — 작업 분석으로 정하라고 되어 있다([pd]).

## 2. 색에 기대지 않는 상태 표기

WCAG 1.4.1 「색이 정보를 전달하는 유일한 시각 수단이 되어서는 안 된다」([wcag]). 기법 G182 는 중복
시각 단서, G14 는 글자로도 제공([g182]).

가장 잘 문서화된 실제 사례는 Vercel Geist 의 StatusDot 이다. QUEUED/BUILDING/READY/ERROR/CANCELED/
DELETED 를 쓰고, **비종료 상태에서만 점이 움직이고 종료 상태에서 멈춘다 — 움직임 자체가 상태 채널이다.**
그리고 명시적으로 「색이 유일한 신호가 아니다. 모든 상태에 고유한 제목과 라벨이 붙는다」고 적어 두었다.
옆에 상태를 말하는 글자가 있으면 점은 `aria-hidden` 으로 둔다([geist]). 배지 문구는 **API·로그의 표준
용어와 일치**시킨다.

CSS 만으로 만드는 어휘(아이콘 폰트 없이 `border-radius` · `border` · 채움으로):

| 상태 | 모양 | 움직임 | 라벨 |
|---|---|---|---|
| 실행 중 | 채운 원 | 맥박 | 실행 중 |
| 대기 | 빈 원 | 느린 맥박 | 대기 #3 |
| 성공 | 채운 원 + 체크 | 없음 | 성공 |
| 실패 | 채운 사각 + 엑스 | 없음 | 실패 |
| 유실 · 워커 down | 빈 사각, 점선 | 없음 | 연결 끊김 |
| 일시정지 | 세로 막대 둘 | 없음 | 일시정지 |

모양 중복이 왜 필요한지 보여 주는 실제 사례: GitHub 의 Actions 상태 아이콘이 `:visited` 링크 스타일에
색을 잃는다는 보고([ghvis]).

**심각도 순서**는 색으로 줄 수 없다([pre]). **정렬 위치**로 먼저 주고(실패·막힘을 위로), 그다음 굵기·
크기, 색은 마지막, 글자 라벨은 항상. Buildkite 가 이렇게 한다 — 상태로 묶으면 막히거나 실패한 스텝이
위로 뜬다([bk]). **표준 심각도 글리프 체계를 규정한 1차 자료는 찾지 못했다 [미확인]**.

## 3. 훔칠 만한 실제 화면

- **Buildkite**([bk]): 접히는 스텝 사이드바, 기본은 파이프라인 순서이고 **상태별 묶기로 전환** 가능,
  스텝 상세는 새 페이지가 아니라 **크기 조절되는 서랍**, **건너뛴 스텝은 로그를 열지 않아도 이유를
  보여 준다**, 진행 중 스텝을 자동으로 따라가는 모드. → 서랍, 상태별 묶기, 그리고 「이유를 알려고 로그를
  열게 하지 않는다」.
- **Jenkins Blue Ocean**([bo]): 실행 상태를 머리줄의 **색 띠**로, 머리줄에 상태·이름·번호·브랜치·커밋·
  소요·종료 시각·작성자, 파이프라인 탭이 기본이고 **실패한 스텝의 로그를 자동으로 펼친다**. → 「통과했나」
  와 「왜」를 클릭 없이 답한다.
- **Vercel**([vc], [geist]): 배포 목록을 **더 촘촘하게** 다시 만들어 한 화면에 더 담고, 환경별로 묶고,
  브랜치와 커밋을 스캔하기 쉽게, 모바일은 스캔에 최적화.
- **Sentry**([sentry]): 「처음 발견」과 「나이」를 **다른 칸으로 분리**, **행 전체를 클릭 대상으로**.
- **Grafana**([graf]): 「대시보드는 인지 부하를 줄여야지 늘리면 안 된다」, 임계값 색은 일관되게,
  **머신 비교는 원시 수치가 아니라 백분율로**(코어 수가 다르다) — 우리 호스트 카드에 그대로 적용된다.
  쌓은 그래프 금지, 행은 일반 → 구체 순서로.
- **Linear**([lin]): 테마 변수를 셋으로 줄이고, LCH 로 지각 균일성을 맞추고, **강조색 사용량을 의도적으로
  제한**했다. 흔히 인용되는 「밀도」 주장은 그 글에 없다 — **[2차]**.
- **Netlify**: 상태 어휘(Enqueued/Building/Ready/Error)는 확인됨. **「Enqueued: Awaiting Capacity」**
  처럼 라벨 안에서 「왜 안 움직이는지」를 답하는 방식이 훔칠 만하다. 레이아웃은 문서가 404 — **[미확인]**.
- **CircleCI · GitHub Actions**: 시각 표기를 공식 문서가 설명하지 않는다 — **[미확인/2차]**.

## 4. 살아 움직이는 화면

- **시간 한계**([rt]): 0.1초 즉각, 1초 사고 흐름 유지, 10초 주의 이탈(그 이상은 진행률 + 취소 필요).
  표시 기준([pi]): 1초 미만 없음, 1–2초 기본 피드백, 2–10초 반복 애니메이션, 10초 이상 진행률. 추정은
  **대략**으로 주고 여유를 둔다.
- **왜 기다림이 괴로운가**([maister]): 끝을 모르는 기다림이 더 길게 느껴지고, **설명 없는 기다림이 설명된
  기다림보다 길게 느껴진다.** → 「대기 #3 · 약 4분」이 스피너보다 낫고, 「워커 mac2 오프라인 — 12분째」가
  이유 없는 정지보다 낫다. 이것이 「왜 안 움직이나」와 「언제 내 차례인가」의 답 그 자체다.
- **갱신 알림**([status]): `role="status"` 는 `aria-live="polite"` + `aria-atomic="true"` 를 함의한다.
  `assertive`·`role="alert"` 는 진짜 실패에만. 폴링마다 영역을 갱신하지 않는다.
- **읽는 중에 화면이 튀지 않게**([cls]): CLS 는 0.1 이하가 좋고 0.25 초과가 나쁨. 자리를 미리 잡아 두고,
  애니메이션은 `transform` 으로만. 사용자 입력 500ms 안의 이동은 제외된다. **스크롤 앵커링은 기본으로
  켜져 있어**([anchor]) 위에 행이 추가돼도 화면이 고정된다 — 문제가 실제로 생기기 전에는 끄지 않는다.
  숫자가 매초 바뀌는 칸에는 `font-variant-numeric: tabular-nums`([tnum]).
- **신선도.** 절대 시각으로 마지막 갱신을 보여 주고, 끊기면 옛 숫자를 현재처럼 그리지 말고 명시적으로
  「낡음」 상태로 바꾼다. **이 패턴의 1차 규격은 없다 [미확인]**.
- 모든 맥박·스피너는 `prefers-reduced-motion` 뒤에 둔다([prm]).

## 5. 한국어 우선 타이포그래피

- **`word-break: keep-all`** 은 CJK 안에서 줄바꿈을 막는다. **비 CJK 텍스트에는 `normal` 과 똑같이
  동작한다**([wb]). 한국어 기본값은 음절 아무 데서나 끊어서 단어가 깨져 보이므로 산문·제목·라벨에는
  `keep-all` 을 쓴다. **한계:** 긴 영문 식별자(브랜치명·SHA·잡 id)가 든 칸은 `keep-all` 로 해결되지
  않는다. 산문에는 `word-break: keep-all; overflow-wrap: anywhere;`, 식별자 칸에는 `min-width: 0;
  overflow: hidden; text-overflow: ellipsis` 를 쓴다. W3C klreq 는 음절 단위와 어절 단위 둘 다
  정당하다고 하고 **기본값을 규정하지 않는다**([klreq] §4.3.1).
- **줄 간격.** KRDS 는 **최소 150%**([krds]). 실제 한국 사이트 조사는 1.38–1.9([lqez]). 한글은 같은
  px 에서 라틴보다 광학적으로 크므로 라틴 기준 1.4 는 답답하다. 산문 1.5–1.6, 한 줄짜리 표 칸과 라벨
  1.25–1.35.
- **웹폰트 없는 글꼴 스택**([pret], [lqez]):
  `system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif`.
- **자간.** klreq 는 한글 기본 자간이 **0** 이라고 적는다([klreq] §4.5). KRDS 는 본문·라벨 0px,
  디스플레이·제목만 0–1px([krds]). 실제 사이트는 −0.3 ~ −1px 도 쓰지만 조사의 결론은 좁은 자간이
  보편적으로 선호되지는 않는다는 것이다([lqez]). **규칙: 한글 본문은 0. 큰 제목에만, 그것도 아껴서
  음수. 한글 본문에 양수 자간은 쓰지 않는다.**
- **숫자와 식별자.** 소요·순번·백분율·시각에는 전부 `tabular-nums`([tnum]) — 칸이 흔들리지 않는다.
  SHA·잡 id 는 고정폭. 숫자 칸은 오른쪽 정렬.
- **크기.** KRDS 본문 17px, 하한 16px — 흔한 14px 라틴 UI 보다 크고, 보조 모니터에 띄워 두는 화면에
  맞는다. WCAG 의 큰 글자 예외도 CJK 는 같은 포인트가 아니라 「동등한 크기」로 본다([wcag]).

## 6. 해라 / 하지 마라

1. **해라** — 「통과했나」의 답을 페이지에서 가장 큰 글자로, 접히지 않는 위치에, **글자로** 둔다. 색만으로
   두지 않는다.
2. **해라** — 큐 순서는 세로 위치, 대기는 막대 길이. 색과 모양은 범주만([pre]).
3. **해라** — 왜 안 움직이는지를 상태 문자열 안에 넣는다(「대기 — 워커 없음」). 툴팁에 숨기지 않는다([maister]).
4. **해라** — 움직임을 상태 채널로 쓴다. 비종료 상태만 움직이고 종료되면 멈춘다([geist]). `prefers-reduced-motion` 뒤에([prm]).
5. **해라** — 상태별로 묶어 실패·막힘이 위로 뜨는 정렬을 제공한다([bk]).
6. **해라** — 매초 바뀌는 숫자에 `tabular-nums` 와 고정 너비([tnum], [cls]).
7. **하지 마라** — 실행/대기/실패의 차이를 색으로만 두지 않는다(WCAG 1.4.1, 그리고 GitHub 아이콘이 실제로
   색을 잃은 사례([ghvis])).
8. **하지 마라** — 영문 식별자가 든 칸에 `keep-all` 을 걸지 않는다. 아무 효과 없이 넘친다([wb]).
9. **하지 마라** — 폴링마다 `role="alert"` 를 울리지 않는다. `role="status"` 로, 진짜 상태 변화에만([status]).
10. **하지 마라** — 머신 비교를 원시 수치로 하지 않는다. 백분율로 정규화하고([graf]) 페이지에서 가장 작은
    글자로 둔다. 세 질문 어디에도 1순위가 아니다.

## 확인하지 못한 것

`m3.material.io` 와 Apple HIG 는 JS 렌더라 제목만 돌아왔다(Material 수치는 미러, Apple 11pt 최소 크기는
**[2차]**). Refactoring UI 의 위계 장(Medium)은 403 — 색 장만 1차. Netlify · CircleCI 의 레이아웃은 공개
문서가 없다. 토스 · 네이버 · 카카오 디자인 시스템의 타이포 수치는 얻지 못했고, 대신 KRDS(정부)를 1차
자료로 썼다. 심각도 글리프 체계 · 낡은 데이터 배너 패턴 · Z 패턴은 1차 근거를 찾지 못했다.

[m3ts]: https://m3.material.io/styles/typography/type-scale-tokens
[krds]: https://www.krds.go.kr/html/site/style/style_03.html
[rui-color]: https://www.refactoringui.com/previews/building-your-color-palette
[m3-space]: https://m3.material.io/foundations/layout/understanding-layout/spacing
[wcag]: https://www.w3.org/TR/WCAG22/#use-of-color
[fpat]: https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/
[pre]: https://www.nngroup.com/articles/dashboards-preattentive/
[nng8]: https://www.nngroup.com/articles/complex-application-design/
[pd]: https://www.nngroup.com/articles/progressive-disclosure/
[g182]: https://www.w3.org/WAI/WCAG21/Techniques/general/G182
[geist]: https://vercel.com/geist/status-dot
[ghvis]: https://github.com/orgs/community/discussions/188130
[bk]: https://buildkite.com/docs/pipelines/build-page
[bo]: https://www.jenkins.io/doc/book/blueocean/pipeline-run-details/
[vc]: https://vercel.com/changelog/redesigned-deployments-list
[sentry]: https://sentry.io/changelog/issue-stream-ui-enhancements/
[graf]: https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/best-practices/
[lin]: https://linear.app/now/how-we-redesigned-the-linear-ui
[rt]: https://www.nngroup.com/articles/response-times-3-important-limits/
[pi]: https://www.nngroup.com/articles/progress-indicators/
[maister]: https://www.columbia.edu/~ww2040/4615S13/Psychology_of_Waiting_Lines.pdf
[status]: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Roles/status_role
[cls]: https://web.dev/articles/cls
[anchor]: https://developer.mozilla.org/en-US/docs/Web/CSS/overflow-anchor
[tnum]: https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric
[prm]: https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
[wb]: https://developer.mozilla.org/en-US/docs/Web/CSS/word-break
[klreq]: https://w3c.github.io/klreq/
[lqez]: https://lqez.github.io/blog/hangul-typo-on-web.html
[pret]: https://github.com/orioncactus/pretendard
