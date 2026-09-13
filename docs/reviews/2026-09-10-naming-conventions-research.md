# 브랜치 · 커밋 · PR 이름 규약 — 조사 기록 (2026-09-10)

> 이유: 이 레포의 브랜치 이름이 어느새 읽기 어려워졌다 — `fix/cli-ux`(어디인지만 말한다) ·
> `feat/m5g-retention`(코드 + 낱말 하나) · `worktree-agent-a8b49041eb08c77fe`(세션이 만든 id).
> 오너 요청으로 스타가 많은 공개 프로젝트들이 무엇을 하는지 조사해 규약을 정하고, 스킬 셋
> (`.claude/skills/branch` · `commit` · `pr`)과 훅(`tools/guard_naming.py`)으로 잠갔다. 정본은
> `CONTRIBUTING.md` 「Names」.

## 1. 무엇을 봤나

| 출처 | 브랜치 | 커밋 제목 | 본문 · PR |
|---|---|---|---|
| [Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/) | — | `<type>[scope][!]: <description>` MUST · `feat`/`fix` 필수, 나머지 타입은 허용 · `BREAKING CHANGE:` 푸터 또는 `!` | 본문 MAY · 푸터는 git trailer 꼴 · 타입이 둘이면 커밋을 나눈다 |
| [Angular commit-message-guidelines](https://github.com/angular/angular/blob/main/contributing-docs/commit-message-guidelines.md) | `my-fix-branch` 정도, 규약 없음 | 타입 8개 `build ci docs feat fix perf refactor test` · 스코프는 패키지 이름 · 현재형 명령문 · 소문자 · 마침표 없음 | 본문 20자 이상 필수(docs 제외) · `BREAKING CHANGE:` · `DEPRECATED:` · `Closes #` · `revert:` + 원 제목 + `This reverts commit <sha>` |
| [Node.js pull-requests.md](https://github.com/nodejs/node/blob/main/doc/contributing/pull-requests.md) | 기본 브랜치에서 `git checkout -b my-branch -t upstream/HEAD` | **서브시스템 접두** + 명령형 동사 · 소문자 · 50자 권장 72자 상한(`net: add localAddress …`) | 둘째 줄 공백 · 72칸 · `Fixes: <url>` · `Refs:` · breaking 이면 이유·상황·정확한 변화 · 도구 `core-validate-commit` 이 강제 |
| [Kubernetes pull-requests.md](https://github.com/kubernetes/community/blob/master/contributors/guide/pull-requests.md) | — | 50/72 · 명령형 · 마침표 없음 · `etcd:` `cleanup:` 같은 영역 접두 | 본문은 what/why · `@` 멘션 금지 · GitHub 키워드 금지 · **작게**(「무관한 변경은 다른 PR 로」) · 「소시지는 squash, 층은 squash 안 함」 · `WIP`/`[WIP]` 제목이 머지를 막음 |
| [Google eng-practices: CL descriptions](https://google.github.io/eng-practices/review/developer/cl-descriptions.html) | — | 첫 줄은 혼자 서는 명령문 요약 | 본문은 **무엇**과 **왜**(문제 · 결정 · 트레이드오프) · 「Fix bug」「Phase 1」 금지 · 링크는 사라지니 설명을 남긴다 · 리뷰 뒤 설명을 갱신 |
| [cbea.ms/git-commit](https://cbea.ms/git-commit/) | — | 일곱 규칙: 제목/본문 빈 줄 · 50자 · 첫 글자 대문자 · 마침표 없음 · 명령형(「If applied, this commit will …」) · 본문 72칸 · what/why | GitHub 은 72자 넘으면 자른다 |
| [Vue contributing](https://github.com/vuejs/core/blob/main/.github/contributing.md) | 버그는 `main`, API 추가는 `minor` | PR 제목이 커밋 규약을 따른다 · `(fix #3899)` 를 제목에 | PR 하나에 관심사 하나 · 테스트 필수 |
| [Flutter Tree-hygiene](https://github.com/flutter/flutter/blob/master/docs/contributing/Tree-hygiene.md) | `git checkout upstream/main -b name_of_your_branch` | 「무엇이 문제였고 무엇이 해결인지」 상세 | `@` 멘션 금지 · Revert 반복 경고 |
| [Graphite: branch naming](https://graphite.com/guides/git-branch-naming-conventions) · [Medium 2025](https://medium.com/@jaychu259/git-branch-naming-conventions-2025-the-ultimate-guide-for-developers-5f8e0b3bb9f7) · [phoenixNAP](https://phoenixnap.com/kb/git-branch-name-convention) | `prefix/TICKET-description` · 소문자 · 하이픈 · 짧게 · 접두 `feature/ bugfix/ hotfix/ release/` · CI 가 접두로 분기 | — | — |
| [GitHub docs: PR quickstart](https://docs.github.com/en/pull-requests/get-started/pull-request-quickstart) · [Graphite: PR titles](https://graphite.com/guides/best-pr-title-guidelines) | — | — | 작고 집중된 PR · 제목은 짧고 서술적(50자 안팎) · 본문은 문제 · 해결 · 배경 |

## 2. 무엇이 공통인가

1. **커밋 제목은 구조가 있다.** 큰 프로젝트일수록 `type(scope):`(Angular · Conventional Commits) 또는
   `subsystem:`(Node · Kubernetes) 접두를 강제하고, 도구(`core-validate-commit` · commitlint)가 막는다.
   릴리스 노트가 여기서 자동 생성되기 때문이다.
2. **제목은 한 줄로 혼자 선다.** 50자 권장 · 72자 상한 · 마침표 없음 · 명령형. 「Fix bug」「misc」「WIP」는
   어디서나 금지어다.
3. **본문은 무엇·왜.** how 는 코드가 말한다. 문제 · 결정 · 트레이드오프 · 참조. `@` 멘션과 GitHub 키워드는
   본문이 아니라 푸터(trailer)에.
4. **브랜치 이름은 프로젝트마다 느슨하다** — 대형 프로젝트는 포크에서 오므로 브랜치 이름을 강제하지
   않는다(`my-fix-branch`). 규약이 있는 곳은 팀 저장소이고, 모양은 한결같이 `prefix/ticket-description`,
   소문자 · 하이픈 · 짧게.
5. **PR 하나에 관심사 하나**, 제목은 커밋 제목과 같은 규약(Vue), 미완이면 `WIP`(Kubernetes) 또는 draft.
6. **squash 는 층이 아니라 소시지에.** 리뷰 반영 커밋은 합치고, 독립된 층은 남긴다(Kubernetes).

## 3. 이 레포에 맞춘 것 — 그리고 왜 다른가

| 항목 | 정한 것 | 다른 이유 |
|---|---|---|
| 브랜치 | `<type>/<scope>-<what-it-does>` · 타입은 커밋 타입과 같은 집합 · 2~7 낱말 · 48자 · **어디·코드만 있는 이름은 거부**(`fix/cli-ux`, `feat/m5g`) · `release/vX.Y.Z` | 티켓 번호 대신 스코프: 이 레포는 이슈 트래커가 아니라 `PLAN.md` 의 마일스톤·결정 번호로 일한다. 워크트리 폴더가 브랜치에서 나오므로(`remote_ci_monitor-<what-it-does>`) 이름이 곧 폴더 이름이다 |
| 커밋 제목 | `type(scope)!: 한국어 요약 — 세부` · 72자 · 마침표 없음 · git 이 만드는 `Merge`·`Revert`·`fixup!` 는 예외 | 요약이 **한국어**다(집안 규칙: 코드에 대해 쓰는 글은 한국어). 그래서 「첫 글자 대문자」「명령형」은 적용되지 않고, 「현재형으로 무엇이 달라지는가」로 바꿨다. 50자 권장은 한글 폭 때문에 72자 상한만 둔다 |
| 본문 | 무엇 · 왜 · 계획서 절과 결정 번호 · 테스트가 잠그는 것 · 72칸 | Google/cbea 그대로. 참조는 링크가 아니라 `PLAN.md 결정 N` — 이 레포의 링크 수명은 계획서가 정한다 |
| 푸터 | `Refs:` `Closes` `BREAKING CHANGE:` + 세션이 받은 attribution 두 줄 | Node 의 trailer 꼴. `Signed-off-by` 는 안 쓴다(혼자 하는 레포) |
| PR | 제목 = 커밋 제목 · 본문 다섯 절(한 줄 · 왜 · 무엇 · 검사 · 문서) · `WIP:` + draft · CI 초록 뒤 REST 머지 · 머지 뒤 브랜치·워크트리 삭제 | 머지는 squash 가 아니라 **merge commit**(main 룰셋이 그렇다) — 그래서 PR 제목이 머지 커밋 제목이 되고 커밋 규약을 따라야 한다 |
| 강제 | `tools/guard_naming.py`(PreToolUse 훅 + CLI) · `tests/test_guard_naming.py` | Node 의 `core-validate-commit` 과 같은 생각을 이 레포의 훅 자리(`guard_production.py` 옆)에 둔다. 훅은 세션(Claude Code)만 막는다 — 사람의 셸은 CONTRIBUTING 이 막는다 |

## 4. 강제하지 않은 것

- 본문 최소 길이(Angular 20자) — 훅이 heredoc 본문까지 읽는 것은 과하다. 스킬이 요구한다.
- 이슈 번호 필수 — 이슈를 안 쓴다.
- 스코프 목록 고정(Angular 의 패키지 목록) — 모듈이 자주 는다. 소문자·하이픈만 본다.
- 브랜치 이름의 「좋은 낱말」 판정 — 훅은 「어디·코드·아무 말도 아닌 낱말**만** 있는가」만 본다. 그 이상은
  사람의 몫이다.
