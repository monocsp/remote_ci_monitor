# Codex 크로스리뷰 — 전체 진행 막대 · 기본 접힘 · 최근 완료의 번호 (2026-09-09)

- 리뷰어: Codex CLI 0.153.4 · `codex exec --sandbox read-only` · 화면 세 장(큐 · 폰 · 최근) 첨부
- 대상: PR #70(커밋 `2ea8178`, `dev`) — `overallProgress` · `progressBarHtml` · `tickProgress` ·
  기본 접힘(`rcm.expanded`) · 최근 완료 행의 잡 번호
- 프롬프트: `docs/reviews/2026-09-09-codex-progress-bar-prompt.md`
- 한 줄 판정: **번호와 기본 접힘은 그대로 두고, 막대는 한 번 더 손봐야 한다** — 「약한 예측을
  자신있는 도형으로 바꾸고 있다」.

## 반영표

| # | 지적 | 판단 | 어디에 |
|---|---|---|---|
| 1 | `estimate.source = "default"`(표본 0 · 프리셋 값 없음)인데도 시간 막대가 확정 눈금처럼 그려진다. 눈금의 출처를 라벨이 안 밝힌다 | **반영** — `default` 는 눈금 없음, 시간 라벨은 `측정 소요 기준`·`프리셋 예상 기준` 으로 갈랐다 | `app.js` · `i18n.js` |
| 2 | 도는 잡에 파란 100% 막대가 나올 수 있다(반올림 · `steps_done == steps_total`). 초기 렌더는 `>`, 틱은 `>=` 로 경계가 다르다 | **반영** — 시간 눈금은 내림 + 99% 상한, 스텝을 다 끝냈으면 퍼센트 대신 `마무리 중`, 경계는 양쪽 다 `>=` | `app.js` |
| 3 | `kind` 하나에 **눈금의 근거**(스텝·시간)와 **잡의 상태**(초과·stuck·준비 중)가 섞였다 | **반영** — `{basis, condition}` 두 축으로 갈랐다. stuck 은 스텝 눈금을 지키되 라벨과 빗금으로 함께 말한다 | `app.js` · `style.css` |
| 4 | 도는 잡의 Cancel 이 ▸ 뒤로 들어갔다. 폭주하는 잡을 세우는 일이 스텝 구경보다 급하다(폰에서 특히) | **반영** — 접힌 도는 행에도 인라인 Cancel, ▸ 버튼의 터치 영역을 키우고 접근 이름에 잡 번호를 넣었다 | `app.js` · `style.css` · `i18n.js` |
| 5 | 펼치면 한 잡에 `role="progressbar"` 가 둘이다 | **반영** — 펼침 블록의 스텝 띠는 장식(`aria-hidden`)으로 내리고 의미는 바깥 막대 하나가 진다 | `app.js` · 목업 · m2 명세 |
| 6 | 시험이 「고른 규칙」만 잠그고 경계·전이·정지 상태를 안 본다. 마지막 escape 시험은 빈 시험이다 | **반영** — 경계(직전·같음·직후) · 반올림 · 근거 전환 · `default` · 정지/끊김 중 틱 정지 · 인라인 Cancel · progressbar 하나. 빈 시험은 지웠다 | `tests/web/progress_overall.test.js` · `tests/test_web_browser.py` |
| 7 | 진짜 「전체 진행률」은 잡이 직접 알려 줘야 한다(가중치·숫자 마커) | **보류** — 마커 프로토콜 확장은 별개 항목이다. 필요해지면 `::rcm::progress::` 를 계획서에 올린다 | — |
| 8 | 1초마다 폭이 움직이는 900ms 전환은 정보량에 비해 과하다 | **반영** — 전환을 250ms 로 줄였다(움직임 설정을 끈 사람에겐 원래 없다) | `style.css` |
| 9 | 정지·연결 끊김 상태에서도 1초 틱이 새 예측값을 만들어 낸다 | **반영** — 예측 막대는 갱신이 멈추면 같이 멈춘다(경과 초는 사실이라 그대로 오른다) | `app.js` |

반영을 확인하다 **막대가 실제로 거짓말을 하고 있었다**는 것을 찾았다(리뷰가 시킨 「그린 길이를 재
보라」의 결과다). 둘 다 이번에 같이 고쳤고, 진짜 브라우저에서 길이와 숫자가 같은지 재는 시험을
붙였다(`tests/test_web_browser.py`).

| # | 무엇 | 왜 |
|---|---|---|
| 10 | 막대의 **class 이름이 화면의 다른 규칙과 부딪혔다** — `pbar steps` 의 `steps` 는 스텝 목록의 격자(`display: grid; grid-template-columns: repeat(2, minmax(0, 280px))`), `stuck`·`over` 는 이유 칸의 칩이다. 그 결과 616px 막대의 `width: 25%` 가 70px(11%)로 그려졌다 | 근거·형편을 `data-basis`·`data-cond` 로 옮겼다. class 는 `pbar` 하나뿐이다 |
| 11 | 자동 레이아웃 표의 `colspan` 칸 안에서는 **파싱된 퍼센트 폭이 「폭을 모름 → 100%」로 굳는다**(Chrome 실측). 25% 막대가 가득 차 보였다 | HTML 에는 `data-fill` 만 싣고, DOM 에 넣은 뒤 화면 층이 `style.width` 를 준다(`applyBarFills`) |

## 리뷰 원문 (그대로)

My verdict: keep the recent-job ID change and default folding. The progress bar needs another pass before promotion to `main`. Its defensive handling of missing values is good, but it still turns weak predictions into confident geometry.

## Ranked changes

| Rank | Change | Value / work |
|---|---|---|
| 1 | Never show a determinate time bar for `source: default`; include confidence/source for every other time estimate | Very high / low |
| 2 | Prevent a blue 100% bar while the job is still running | Very high / low |
| 3 | Separate progress basis from job condition (`stuck`, `overdue`, `preparing`, `cancelling`) | High / medium |
| 4 | Restore an inline Cancel action for an authorized running job, especially on mobile | High / low |
| 5 | Remove the second semantic progressbar from expanded details | Medium / low |
| 6 | Add transition, boundary, stale/pause, mobile, and accessibility tests | High / medium |
| 7 | Eventually add explicit weighted/numeric progress to the marker protocol | High / high |

## Where the bar lies

The default 600-second estimate is the clearest violation. [`overallProgress()`](src/remote_ci_monitor/web/app.js:680) never looks at `estimate.source` or `estimate.confidence`. Consequently, an arbitrary installation fallback with zero observations becomes, for example, “70% · by expected time.” The low-confidence badge elsewhere in the row does not neutralize the strongly determinate bar. `default` should produce indeterminate progress. A configured preset estimate can remain a forecast, but its label should say so explicitly; measured estimates should include `med/high` or the sample count.

There is also a concrete boundary bug, not just a design concern:

- `Math.round(elapsed / expected * 100)` can produce a full blue `100%` before the estimate has elapsed.
- Initial rendering calls it overdue only for `elapsed > expected`, while [`tickProgress()`](src/remote_ci_monitor/web/app.js:1697) uses `seconds >= expected`.
- Declared steps can reach `done === total` while the process is still doing cleanup—or after the script ends its final step marker but continues doing unmarked work—again showing a full blue 100% bar on a running job.

A running state should never have the same full-blue visual as completion. Use at most 99% for a time forecast, and render “all declared steps finished · finalizing” rather than “100% overall.”

The four cases you called out:

- Basis switching is not mathematically false, because the label changes, but it is perceptually false: a bar can jump from 70% time to 25% steps without explanation. Users glance at position, not the fine print. Either do not mix these bases or make the basis visually unmistakable and announce a change.
- A bar sitting at 87% through one long final step is acceptable only if it is plainly a completed-step counter. It is not acceptable as “overall progress.” Equal step count does not imply equal work.
- The 99% concern is real—and the code can actually show 100% early because of rounding.
- A default 600-second bar with no samples is plainly wrong under this product’s honesty rule.

My preferred low-cost model is:

- Declared total: “4 of 8 steps finished,” preferably segmented.
- Measured/preset duration: an explicitly named time forecast, including confidence, not “overall progress.”
- Default/no basis: indeterminate, with elapsed time in text.
- Longer term: support explicit numeric or weighted progress from the job. Without that, true overall completion is unknowable.

## The four-kind model

It mixes two independent axes:

- Basis: steps, elapsed-time forecast, none.
- Condition: normal, overdue, stuck, materializing, cancelling.

`over` is not a progress basis, and `unknown` currently collapses several materially different situations. “Preparing workspace,” “cannot estimate,” and “likely stuck” should not have the same label.

“Stuck keeps step percentage but loses time percentage” is defensible internally, but users should not have to learn that rule. Render the actual proposition:

> 4 of 8 declared steps finished · likely stuck

That preserves the factual counter without letting the healthy blue bar contradict the warning. The current generic ARIA name, “overall progress,” overclaims what the step count means at [`progressBarHtml()`](src/remote_ci_monitor/web/app.js:706).

## Folding and actions

The folded row still answers the two primary questions reasonably well:

- “Is it done?” — state pill, elapsed time, ETA, and Your jobs.
- “Why is nothing moving?” — current step and duration in the row, plus the Not moving summary.

That part is successful, especially on the phone screenshot.

Hiding running Cancel is a real regression. Stopping a runaway job is more urgent than inspecting its steps or log. On mobile it now requires discovering and hitting a very small arrow, expanding a tall block, and reaching the bottom. The toggle’s styling at [`style.css:184`](src/remote_ci_monitor/web/style.css:184) also gives it a touch target far below the usual comfortable size.

Show Cancel inline for authorized running jobs; the confirmation dialog already protects against accidental activation. Log can remain folded. At minimum, enlarge the toggle and give its accessible name the job ID, such as “Expand details for job #4.”

## Visual weight

The outer bar is in the right vertical location: directly beneath its row, and retained when folded. Its width is somewhat dominant on desktop, but acceptable if it is the single primary progress visualization.

The expanded mini bar is one bar too many. When the outer bar uses steps, it duplicates the mini bar. When the outer bar uses time, the two bars report different quantities without making that distinction obvious. Keep the expanded step list and, if useful, retain the segmented strip as decorative structure rather than a second progress indicator.

On phones, full available width is appropriate. On desktop, something around the width of the key-through-reason region would carry less visual weight than the current 720px maximum. Keep the textual label next to the bar; it is doing essential honesty work, not decoration.

## Accessibility

Some details are correctly handled:

- Omitting `aria-valuenow` for indeterminate progress is correct.
- Overdue is not distinguished solely by colour: the visible and accessible text says “past the estimate.”
- Width transitions respect `prefers-reduced-motion`.

The problems are semantic:

- Expanded rows expose two `role="progressbar"` elements for one job at [`app.js:1470`](src/remote_ci_monitor/web/app.js:1470). They may contain the same value or conflicting time/step values.
- “Past the estimate” is a status, not an indeterminate progress value. It should be attached to the forecast/status text rather than represented as a progressbar.
- A stuck job’s factual step count needs the stuck condition in its accessible text.
- A separate table row for the bar adds another row during table navigation without providing table data or header relationships.

The 900ms transition on one-second updates is not a major accessibility failure, but it creates nearly perpetual movement for little informational benefit. Integer percentages often do not even change each second. I would shorten or remove it.

## Tests and future traps

The new tests mostly lock the implementation’s chosen rules rather than challenging their truthfulness. Important missing cases include:

- `estimate.source === "default"` with zero samples.
- `elapsed` just below, exactly equal to, and just above `expected`.
- Rounding to 100 while still running.
- `steps_done === steps_total` while still running.
- Time-to-steps basis changes.
- Stuck plus declared steps in visible and accessible text.
- Manual pause and lost connection.
- Two progressbars in an expanded row.
- Mobile Cancel reachability and toggle target size.

The browser test only verifies the initial time-bar markup; it never observes [`tickProgress()`](src/remote_ci_monitor/web/app.js:1697) crossing a boundary. Also, the final “escape” test in [`progress_overall.test.js`](tests/web/progress_overall.test.js:188) is vacuous: it never injects hostile text, and this bar does not render a step name anyway.

Another real trap: `canTick()` only checks clock knowledge. The one-second ticker continues changing the bar during manual pause and while the connection is lost. That means a supposedly paused or “last known” page continues manufacturing new forecast values locally.

Finally, the catalogue phrases “by expected time” and `예상 시간 기준` conceal both the estimate source and what the percentage actually means. As more estimate sources arrive, these two strings will drift from the underlying rules. Give the catalogue explicit variants for measured, preset, default/unknown, stuck, preparing, and finalizing.

Bluntly: the UI work is polished, but the time ratio should not have been promoted to “overall progress.” The product already has an honest ETA model with confidence; the bar currently strips away that caution and makes the estimate look more certain than the ETA itself.
