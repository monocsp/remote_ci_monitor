You are reviewing a change that has already shipped to a development branch of a small open-source
tool. Critique it. Do not write code and do not edit files — read what you need and answer in prose.

## What the product is

`rcm` is a local job server for a team that shares one build machine. Anyone on the team submits a
preset job from their laptop (`rcm run gate`); the server queues jobs and runs them, one per lane.
The web page is the whole UI: three static files (`src/remote_ci_monitor/web/{index.html,app.js,
style.css}` plus a string catalogue `i18n.js`), vanilla JS, no framework, no third-party assets,
served by the server itself. The page is Korean by default with an English switch.

## The rule the whole tool is built on

**No fail-open.** A value the tool does not know prints as an em dash, never as zero. An ETA carries
a confidence label (high / med / low / group wait / overdue) and is omitted entirely when the job is
past its estimate. `rcm wait` exits 3 for "unknown", never 1. A positive phrase ("nothing is stuck")
is only drawn when the section was actually fetched and every value is present. The failure mode
this rule exists to prevent: a glanced-at side screen that is quietly broken and therefore shows a
confident wrong number.

## What the owner asked for, in their words (translated)

1. "For the ones that are running, add an overall progress bar so I can see at a glance how far
   along it is."
2. "The detail about which step is running should be collapsible and hidden by default."
3. "Running rows show the job number, but the recently-finished ones don't. Fix that."

## What was built (commit `2ea8178` on `dev`, PR #70)

1. **An overall progress bar** under every running row, drawn whether or not the row is expanded.
   The pure function is `overallProgress(row)` in `app.js`; the markup comes from
   `progressBarHtml(row, lang, live)`; the one-second update is `tickProgress()`. It has four kinds:

   | kind | when | bar | label (en) |
   |---|---|---|---|
   | `steps` | the job declared a step total with `::rcm::steps::N` and it is not partial | `steps_done / steps_total` | `50% · 4/8 steps` |
   | `time` | no declared total | `elapsed / expected_seconds` — the same estimate the ETA column uses; grows every second between polls | `70% · by expected time` |
   | `over` | elapsed exceeds the estimate (or `estimate.overdue`) | full width, hatched warn colour, no `aria-valuenow` | `past the estimate` |
   | `unknown` | `estimate.stuck`, `phase: materializing`, or no basis at all | a hatched track with no fill | `progress —` |

   Deliberate choices: a *partial* step total (the server reports `steps_total_partial: true` when
   the job never declared a total, so the number is "how many we have seen so far") is not used as a
   denominator; a stuck job gets no time-based percentage (but keeps a step-based one, since
   finished steps are facts); an over-estimate job is hatched rather than filled blue, because a
   full blue bar reads as "done".

2. **Rows are folded by default.** Running rows used to expand themselves. Now the row keeps the
   bar and, in the reason column, `step 2/4 build 2s`; the step list, the log tail and the
   Log/Cancel buttons are behind a `▸` toggle. What is remembered in `localStorage` flipped from
   `rcm.collapsed` (rows you closed) to `rcm.expanded` (rows you opened). A deep link
   `#/jobs/<id>` opens that row.

3. **The recent-results rows gained a leading `#412` column.**

## Where to look

- `src/remote_ci_monitor/web/app.js` — `overallProgress`, `progressBarHtml`, `tickProgress`,
  `queueRowHtml`, `renderRecent`, `loadExpanded`/`saveExpanded`, `gotoJob`.
- `src/remote_ci_monitor/web/style.css` — `.pbar`, `.pwrap`, `.plab`, `tr.qbar`, `tr.hasbar`,
  `.rrow`; the reduced-motion block at the end.
- `src/remote_ci_monitor/web/i18n.js` — the `pbar.*` keys in both languages.
- `tests/web/progress_overall.test.js` (node --test) and the two new browser tests at the end of
  `tests/test_web_browser.py`.
- `PLAN.md` section 「웹 UI (M2)」 and owner decision 13; the layout source of truth is the mock-up
  `docs/wireframes/web-queue.html` (item 12 and section 4).
- Screenshots attached: the desktop queue, the phone layout, and the recent list.

## What I want from you

1. **Where does the bar lie?** Take the honesty rule seriously and hunt for cases where this bar
   tells a confident wrong story: the basis switching from `time` to `steps` mid-job when the first
   `::rcm::steps::N` arrives, a step-based bar sitting at 87% for twenty minutes because the last
   step is the long one, a `time` bar pinned at 99% just before it flips to `past the estimate`, a
   job whose estimate comes from `default_seconds` (600) with no samples at all. Which of these
   deserve a different treatment, and which am I over-thinking?
2. **Is the four-kind model right?** Would you merge or split kinds? Is "stuck loses the time
   percentage but keeps the step percentage" a rule a user can hold in their head, or is it a rule
   only the author can see?
3. **Folding.** Does the folded row still answer "is my job done yet" and "why is nothing moving"?
   Note that Cancel for a *running* job now lives behind the toggle (waiting rows keep an inline
   Cancel). Is that a real loss, especially on a phone?
4. **Visual weight.** One bar per running row, plus the summary strip at the top, plus a per-step
   mini bar inside the expanded block. Is that one bar too many? Where should the bar sit, how wide,
   and should the label be where it is?
5. **Accessibility.** Two `role="progressbar"` elements for the same job when a row is expanded; an
   indeterminate bar expressed by dropping `aria-valuenow` and keeping `aria-valuetext`; hatching as
   the only difference between `over` and a filled bar for someone who cannot see colour; a bar that
   animates its width every second.
6. **Traps.** What will bite this later — in the code, in the tests, or in the wording of the two
   language catalogues?

Rank what you would change by value per unit of work, and be blunt where the design is wrong.
