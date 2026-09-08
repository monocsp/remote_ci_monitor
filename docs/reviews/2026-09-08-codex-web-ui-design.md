# Codex 디자인 리뷰 — 웹 UI (2026-09-08)

> `codex exec` 에 화면 다섯 장(큐 · 내 잡 · 호스트 · 최근 · 폰)과 제품 설명 · 제약 · 오너의 불만을
> 붙여 물었다. 프롬프트는 `docs/reviews/2026-09-08-codex-web-ui-prompt.md`.

**Highest ROI Direction**
Make the first screen answer one thing first: **“내 작업은 지금 어디에 있고 언제 끝나나?”** Everything else should support that or move down/collapse.

**Diagnosis**
The UI reads flat because hierarchy is encoded mostly by table position, not visual priority.

- The top summary strip is conceptually important, but visually tiny. “Your jobs”, “Not moving”, and “Host pressure” use the same small label/value treatment.
- Nearly everything uses the same monospace weight, size, border, and dark surface. Queue rows, expanded steps, host metrics, recent jobs, chips, and metadata all compete.
- The queue table treats all columns as equal: job number, key, requester, reason, elapsed, ETA, source all have similar weight, even though ETA/status/ownership matter more.
- Expanded running-job details create another mini table, so the eye has to parse instead of scan.
- Host metrics are too prominent for a rare “machine owner” workflow. They occupy a large, visually dense region and look as important as the queue.
- Source metadata is overexposed. Commit, dirty state, and git URL are useful, but not first-scan information.
- Token-gated states repeat friction. “Add token to see the log” appears inside job rows, which adds noise for read-only viewers.

**Hierarchy**
First screen should be dominated by:

1. **My jobs / active answer**
   - If token exists: show my running/waiting/recent jobs as the primary block.
   - If no token: show “전체 대기열” first, but keep token CTA small and persistent.
   - The dominant information: status, position, ETA, requester, current step/result.

2. **Queue movement**
   - Current running job.
   - Next 2-3 waiting jobs.
   - Clear stuck/paused/down/overdue state.

3. **Honest ETA**
   - ETA time, confidence, queue position, “behind #4”, “lane busy”, overdue/lost labels.

Secondary:

- Full queue details.
- Logs.
- Source/commit metadata.
- Recent results.

One interaction away or lower page:

- Host CPU/memory/GPU details.
- Process lists.
- Sparklines.
- Full git remote URLs.
- More than 3-5 recent jobs.

Delete from the default first scan:

- Repeated git URLs in every visible row.
- Full host process names unless host panel is expanded.
- Disabled-looking log/cancel controls for users without a token. Replace with one small “Add token to view logs/cancel your jobs” affordance.

**Layout Direction**
Use a task-first dashboard, not one giant table.

```text
Header
rcm macmini        worker state chips              freshness + language + token

Primary status band
[ My job / Current job card ]       [ Queue health ]
#4 running · build · ETA 10:19      Moving / stuck / paused / host down
step 2/4 · log preview if allowed   oldest waiting · lanes busy · freshness

Queue
Now running
  #4 demo · alice · build · 30s left · med confidence
Next
  #5 demo · bob · 1st in line · starts after #4
Other pool
  #6 remote-demo · running on mac2

Recent
  compact result list, failures expanded by default

Host
  collapsed summary by default
  expand for CPU/memory/GPU/processes
Footer
```

On phone:

- Header wraps into two rows: product/host, then freshness/token/language.
- Primary status card comes first.
- Queue rows become stacked cards with fixed field order:
  `status → key/requester → ETA/position → current step → actions`.
- Hide source URL by default; show commit only, with details expandable.
- Host section should be collapsed unless there is a warning.

**Typography, Colour, Density**
Use system sans for UI, monospace only for logs, SHAs, commands, URLs, and job keys where useful.

Recommended stack:

```css
font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
             "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
```

Mono stack:

```css
font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

Scale:

- Base UI: 14px desktop, 15px mobile.
- Metadata: 12px.
- Primary status/ETA: 18-22px.
- Section labels: 12px uppercase in English, normal Korean labels without aggressive letter spacing.
- Logs: 12-13px mono.

State encoding should not rely only on color:

- Running: blue left rail + “실행 중” chip + play/triangle glyph.
- Queued: neutral dotted rail + “대기 중” chip + queue position.
- Succeeded: green check + “성공”.
- Failed: red X + “실패” + exit code.
- Lost: amber/gray warning + “유실됨” + “server restarted”.
- Down/stuck: prominent warning block in queue health, not just a small colored word.

Keep density by using stronger grouping, not more whitespace everywhere:

- One primary card can be larger.
- Rows can stay compact.
- Use indentation and left rails for nested job details.
- Reduce columns instead of shrinking text.
- Make metadata quieter, not smaller to the point of illegibility.

**Korean-First Notes**
Korean changes the rhythm.

- Increase line-height: use about `1.45-1.6` for Korean body text; mono logs can stay tighter.
- Avoid uppercase-style section treatment for Korean. “YOUR JOBS” works visually in English; “내 작업” should be normal weight/size, not spaced-out.
- Korean labels are often short but wrap awkwardly when mixed with long English identifiers. Keep labels separate from values:
  `상태  실행 중`
  `작업  demo`
  `소스  78a83c7`
- Use `word-break: keep-all` for Korean prose/labels, but allow breaking for URLs and git remotes with `overflow-wrap: anywhere`.
- Keep identifiers in monospace: preset names, job IDs, SHAs, commands, `git@github.com:...`.
- Do not make chips too narrow. Korean chips like `실행 중`, `대기 중`, `유실됨` need stable min-widths so rows do not jitter.

**What Not To Do**
- Do not solve this by adding more cards everywhere. That will make the dump prettier, not clearer.
- Do not put host graphs at the top. Host health is important only when it explains a queue problem.
- Do not make a landing page, welcome panel, or instructional hero.
- Do not hide honesty labels. ETA confidence is core product behavior.
- Do not use color-only status.
- Do not use emoji as the main icon system; simple text glyphs/CSS shapes are enough.
- Do not keep everything monospace. It makes operational data and UI labels fight each other.
- Do not make mobile a squeezed table. Mobile needs a reordered card layout.
- Do not translate identifiers, commands, preset names, SHAs, or remotes.
- Do not show the page as fresh unless the event stream/polling state is genuinely fresh.

**Ranked Work**
1. Rebuild first-screen hierarchy around “my/current job + queue health” and demote host details.
2. Replace the wide queue table with priority rows/cards where status, ETA, and position dominate.
3. Switch UI typography to system sans; reserve monospace for technical values.
4. Collapse source metadata, logs, host processes, and extra recent results behind details.
5. Add non-color state encoding with rails, chips, symbols, and consistent labels.
6. Tune Korean typography: line-height, `keep-all`, stable chip widths, and better label/value separation.