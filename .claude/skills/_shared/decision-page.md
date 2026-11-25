# Decision page — how to ask the owner

Whenever the loop needs the owner (approving a workplan, a ⛔, an open question, a defect's
classification), the main session asks **once, on one page**: every open question of that moment
together, each with one recommended answer. Leads and verifiers never ask the owner; they put
questions in their results and the main session collects them here.

Why one page: questions asked one by one stall the work, and a list in the chat makes the owner
rebuild which screen, command or rule each line is about before answering. An answer stored on the
page is also read back as written, with nothing lost to summarising.

## Before asking

- **Look for the answer first.** PLAN.md (its decision table and 「fail-open 금지」), the workplan,
  AGENTS.md, CHANGELOG, a code comment or a test that locks the behaviour. If one answers it, it is
  not a question; record it as `RESOLVED` with the `file:line`. Asking what is already decided is not
  neutral: it hands the decision back and invites a different answer.
- **Too few questions is as bad.** A choice an agent made silently is a decision nobody reviewed, and
  a test then locks it. Ask what the sources do not settle.
- Each question is `EXTERNAL` (a fact outside the repository), `POLICY` (a product or UX choice; it
  carries a default) or `RESOLVED` (answered, not asked).

## One question

- **Title** — a short question, not a topic: "Should `rcm wait` return 3 or 1 when …?"
- **Why** — one to three lines of observed fact (`file:line`, an output, the owner's earlier words).
  A guess is labelled as a guess.
- **Options** — two to four, each a bold one-line label and one or two lines on what happens if
  chosen. "Ask someone else first" is a valid option when the answer is not ours.
- **Exactly one `default`** — the agent's recommendation, nothing more. It is never applied on
  silence: **no answer is not approval**, and an open ⛔ stops the work. The page says so.
- **What changes** — one line on the trade-off. An option that changes a frozen contract (schema,
  database version, wire format, exit code, CLI) or an explicit owner request says so plainly.
- **A note field under each question**, so the owner can answer "b, but c if …" where it belongs.
  One field at the bottom takes only remarks that span questions.

Questions with something to show (a web screen, a terminal output, a diff) show it next to the
question: for the web, a screenshot taken with the CDP helper on a test server and redacted by hand
(`.claude/skills/_shared/test-server.md` «Driving it»), or an existing image from `docs/images/ui/`
when the screen is unchanged; a code block for the terminal. A question with nothing to show goes in
a second group without a picture.

## Publishing

When the Artifact tool is available, the page is an Artifact. For a new page, call that tool's
`quickstart` action first (`intent: "other"`) and follow what it returns; then load the
`artifact-capabilities` skill for the `db` capability (and the `artifact-design` skill if the
quickstart did not cover it) before writing the page. Answers are stored as
documents `decisions/<QID>` = `{choice?, note?, at}` (choice and note in one document, rewritten
together so changing one does not drop the other) and `decisions/_note` for the bottom field. A
failed save is shown on the page and in its status line, never swallowed. Read the answers back with
`ArtifactData` (`action: "list"`, `collection: "decisions"`).

Without the Artifact tool, the page is one message with the same shape, and the owner answers by
question id.

Hand the URL over on its own line, undecorated, so the terminal links it. Do not open the browser for
the owner. An Artifact is private by default; say so if it is meant for someone else.

## Reading the answers

- **Verbatim.** Each answer, and its note, goes into the workplan's decision section and, when it
  binds the project, PLAN.md's decision table as a new decision number, quoted as the owner wrote it,
  dated. No summarising: the deciding word is the first thing a summary drops.
- **A note that disagrees with the choice** ("a, though really b") is asked again, not resolved by
  taking the choice. A note without a choice leaves the question open.
- **An answer different from the default** is marked: that is where the plan changes.
- **Unanswered is open.** Do not proceed on the default.
- **The agent never closes its own ⛔.** Closing needs the owner's words.
- A small boundary question that comes up after the page was answered, changes no frozen contract and
  no explicit owner request, may proceed on its default; list it for confirmation in the PR body.
  Anything else is a new question on a new page.

## The page is not the gate

Making the page is not approval. For a workplan the gate is the spec PR merged after the answers are
folded in (the `workplan` skill); for code it is `rcm/ci` and isolated verification. A workplan's
page always carries the lane question: full or short for each PR, as `workplan` «Lane» defines them.
