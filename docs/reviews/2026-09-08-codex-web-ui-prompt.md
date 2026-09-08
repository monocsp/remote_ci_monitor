You are reviewing the design of a web page. Give a design critique and a concrete direction. Do not write code.

## What the product is

`rcm` is a local job server for a team that shares one build machine. Anyone on the team submits a preset job from their laptop (`rcm run gate`); the server queues jobs and runs them one at a time. The page attached is the whole UI: three static files (108 lines of HTML, 254 lines of CSS, 1452 lines of vanilla JS), no framework, no third-party assets, served by the server itself.

## Who looks at it and why

Three moments, in order of how often they happen:

1. "Is my job done yet, and did it pass?" — someone who submitted a job 4 minutes ago and is waiting.
2. "Why is nothing moving?" — the queue looks stuck; is a worker down, is something hung, is the machine overloaded, is the queue paused?
3. "Whose job is in front of mine and how long until my turn?" — queue position and an honest ETA.

A fourth, rarer: the person who owns the build machine checking whether it is healthy.

Most viewers keep the tab open on a second monitor and glance at it. Some open it on a phone.

## Hard constraints (do not propose designs that break these)

- No build step, no framework, no third-party assets, no external fonts or icons. Vanilla HTML/CSS/JS only, shipped inside a Python package.
- Everything updates live over an event stream, with polling fallback; the page must never look current when it is not.
- Honesty rules: an unknown value prints as an em dash, never zero. An ETA carries a confidence label (high / med / low / group wait / overdue). A job that was lost when the server restarted stays "lost", it is not silently retried.
- Reads are open (no token). Writes need a token pasted into the page, kept in localStorage. Without a token the viewer sees the queue but cannot cancel or read logs.
- Must work on a phone (single column below 720px) and follow the system light/dark setting.

## What is on the page now (attached screenshots, in this order)

1. `queue.png` — the whole desktop page: header, a three-answer summary strip, the queue table with a running job expanded to show steps, a waiting job, and a second worker pool.
2. `yours.png` — the same page after the viewer pastes a token: their jobs are marked, a log tail appears, Log and Cancel buttons show.
3. `host.png` — the host section: one card per machine with CPU, memory, GPU meters, five-minute sparklines and the heaviest processes.
4. `recent.png` — recent results with a failed job expanded.
5. `phone.png` — the 500px-wide layout.

## The owner's complaint

"The UI is not good." Their specific observation, and mine: everything is on one screen at the same visual weight, so the thing that matters right now does not stand out. The page reads like a data dump.

Two decisions already made: the page will be **Korean by default** with a language switch at the top right, and the information needs a real hierarchy.

## What I want from you

1. **Diagnosis.** What specifically makes this read as flat? Name the mechanisms, not adjectives. Point at concrete elements in the screenshots.
2. **Hierarchy.** Given the three moments above, what should dominate the first screen, what should be secondary, what should be one interaction away or removed? Be willing to say "delete this".
3. **A layout direction.** Describe it precisely enough to implement: regions, their order, their relative weight, what collapses on a phone. Sketch in text or ASCII if that helps.
4. **Typography, colour and density.** What system-font stack and scale; how to encode state (running / queued / failed / lost / down) so it reads at a glance without relying on colour alone; how to keep density high without flattening.
5. **Korean-first typography.** What changes when the primary language is Korean (line height, word-break, the fact that Korean labels are shorter but wrap badly, mixing Korean text with English identifiers like preset names and `git@github.com:...`).
6. **What NOT to do.** Traps you would expect a coding agent to fall into here.

Rank your recommendations by how much they improve the three moments per unit of work. Be blunt where the current design is wrong.
