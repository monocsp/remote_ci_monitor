---
name: commit
description: Write a commit for remote_ci_monitor — Conventional Commits header in the house style (type(scope): Korean summary), a body that says what and why, and the trailers this repository uses. Use it before every `git commit`.
---

# Commit

A commit message is the only thing that survives when the PR, the chat and the author are gone.
The header follows [Conventional Commits](https://www.conventionalcommits.org/); the body follows
the seven rules of [cbea.ms/git-commit](https://cbea.ms/git-commit/) and Google's "what and why".
`tools/guard_naming.py` refuses a header that does not fit; `CONTRIBUTING.md` (Names) is the rule.

## Header

```
<type>(<scope>)!: <summary>
```

| part | rule |
|---|---|
| `type` | `feat` `fix` `docs` `test` `refactor` `perf` `ci` `build` `chore` `release` `revert` |
| `scope` | optional, lowercase: a module (`web`, `store`, `gc`) or a milestone code (`m5i`). Omit it when the change crosses modules |
| `!` | before the colon when the change breaks a contract (schema key removed, exit code changed, config key renamed) — and a `BREAKING CHANGE:` footer says what to do |
| `summary` | Korean, present tense, says the change and, after ` — `, the why or the pieces; no period at the end; the whole first line at most 72 characters |

The body and the summary are Korean (house rule: identifiers, CLI help and docs in English;
everything written *about* the code in Korean). Identifiers, keys, commands and SHAs stay as they
are.

| bad | good |
|---|---|
| `fix web card` | `fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것` |
| `Fix bug` · `WIP` · `misc` | `fix(gc): 오프라인 dry-run 이 운영 DB 를 마이그레이션하던 것 — 임시 사본 위에서 돈다` |
| `feat(m5i): 단계 2` | `feat(store): 마이그레이션 전에 백업을 만든다 — 못 만들면 멈춘다` |

## Body

- A blank line, then paragraphs wrapped at 72 columns (Korean counts double — wrap early).
- **What** changed, at the level of behaviour, and **why**: the problem, the decision, the
  trade-off. The code already says how.
- Cite the plan: `PLAN.md 결정 74`, `docs/m5i-workplan.md §3 B2`, the incident date, the job number.
- What the tests lock: "테스트: v7 픽스처로 dry-run 뒤 user_version 그대로".
- No `@mentions`, no `Fixes #N` in the body (that is a footer).

## Footers (trailers)

```
Refs: #80
Closes #81
BREAKING CHANGE: `freed_bytes` 는 이제 계획상 합이 아니라 …
Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_…
```

The attribution lines are given to you at session start — end the message with them exactly as
given.

## Steps

1. `git status` and `git diff --staged`: is this **one** change? Split otherwise (Kubernetes:
   "layers → don't squash; sausage → squash").
2. Write the header; check it: `python3 tools/guard_naming.py commit "<header>"`.
3. Commit with a heredoc so the body keeps its lines:
   ```sh
   git commit -m "$(cat <<'EOF'
   fix(web): 호스트 카드가 정의 안 된 local 을 읽던 것

   `hostCardHtml` 이 `local` 을 참조해 render() 마다 ReferenceError 가 났고, 그래서
   최근 목록이 안 그려지고 30초 뒤 「연결이 끊겼습니다」 띠가 떴다(2026-09-10 실배치
   재현, docs/m5i-workplan.md §3 B1). 로컬 표본은 source == "local" 이다.

   테스트: tests/test_web_browser.py 의 표본에 disk 와 job_storage 를 넣어 카드 문구와
   콘솔 예외 0건을 단언한다 — 고치기 전에는 빨갛다.

   Refs: #80
   Co-Authored-By: …
   EOF
   )"
   ```
4. Fixing review feedback: `git commit --fixup <sha>` and squash before merge, or a new
   `fix(...)` commit when it is a separate change. Never rewrite a commit that is already merged.
