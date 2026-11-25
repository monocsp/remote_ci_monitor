# Workplan diff — what was built differently from the spec

Whatever the loop builds differently from the spec, or builds where the spec says nothing, is
recorded the moment it is decided, in one shape, from the lead's result to the PR body. A reviewer,
the owner and whoever bisects later need to see "it was going to be X, it became Y, because Z"
without reading the diff. rcm already keeps such a record: `docs/m5q-workplan.md` §12
「구현하며 바뀐 것」.

## What the spec is

The approved workplan as merged (`docs/m5x-workplan.md`), the owner decisions it and PLAN.md record,
and the owner's request text it cites. Something that later changed the workplan through a new
approval is still marked: an approval does not erase the difference.

Marked: what a user sees or relies on — a command, a flag, output, an exit code, a key in a JSON
document or the config, the web UI, a default, a limit, a documented behaviour. Not marked: internal
structure with no visible effect (a helper's shape, a refactor); when its effect shows, mark the
effect. A QA fix that brings rcm back to what the spec says is not marked.

## Six columns

| mark | what | spec said | built | why | decided where |
|---|---|---|---|---|---|
| differs from spec | `<command>` when the server is gone | exit 1 | exit 3 | unknown is not a failure (PLAN.md 「fail-open 금지」) | owner `<date>` · decision `<N>` |
| not in spec | the queue card of a `<kind>` job | no definition | shows its start time in local time | the card showed no time at all | implementation call (P2), owner not yet confirmed |

- **mark** is `differs from spec` or `not in spec`; "spec said" is `no definition` for the second.
- **why** is one line. When the source gives no reason, write `no reason in the request`; never invent
  one.
- **decided where** is the owner's words with their date, a decision number, a decision-page
  question (`Q3 a`), or a QA finding id. A choice the implementer made is
  `implementation call (<stage>), owner not yet confirmed`, and the owner hears of it at once.

## When

At the moment of deciding: when the plan is broken into items, when an answer comes back from the
decision page, and whenever a lead, a fix or a verifier settles something the spec did not.

- The main session says it in the chat in one line and **carries on**; this is a notice, not a
  question. Following an explicit owner request while noting that it differs from the workplan is
  this case.
- Changing or reversing an explicit owner request is not: that is a ⛔ on the decision page
  (`.claude/skills/_shared/decision-page.md`), and the line is written after the answer.
- A verifier (P3, P4, QA B5) that finds a visible difference with no line raises it as a finding;
  a line is added only with the owner's words or decision behind it, never to make a finding go away.

## Where it is recorded

| flow | collected from | recorded in |
|---|---|---|
| dev-loop | the lead's `out.json` `workplan_diff`, decision answers, P3–P6 fixes | the PR body section, and the workplan's §13 「구현하며 바뀐 것 / Changed while implementing」 in the same PR |
| dev-lite | the request card and the fix | the PR body section (there is no workplan) |
| dev-batch | each PR's lead result | each PR's body; the main session keeps the batch's list until the last merge |
| QA | the fix lead's result, the triage answer | the PR body; the round report lists them again |

Leads write each line in the six columns, ` · ` between them:
`differs from spec · <what> · <spec said> → <built> · <why> · <decided where>`. Only the main session
writes §13 (`workplan` §1); a lead returns lines in `out.json` and never edits the frozen workplan.

## The PR section

The PR body carries this section after `## 한 줄`, with the heading exactly as below:

```markdown
## Differs from the spec
| mark | what | spec said | built | why | decided where |
|---|---|---|---|---|---|
| … |
```

- Nothing to report: the single line `None: built as specified.`
- Not compared (no spec for this change, or it could not be read): `Not compared: <reason>.`
- An empty section is not "built as specified". It means nobody looked.
