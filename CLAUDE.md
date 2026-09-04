# remote_ci_monitor

PLAN.md 가 정본이다. 세션을 시작하면 먼저 끝까지 읽는다.

## 브랜치 정책 (GitHub 룰셋으로 강제됨 — 자세한 건 PLAN.md 「브랜치 정책」)

- `main`·`dev` 에는 직접 커밋·push 할 수 없다. 관리자도 예외 없다. 시도하면 push 가 거부된다.
- 작업은 `dev` 에서 feature 브랜치를 파서 하고 `dev` 로 PR 을 보낸다.
- `main` 은 `dev` 에서 보낸 PR 로만 받는다. `test`·`main-from-dev-only` 체크가 통과해야 머지된다.
- 세션 시작 절차: `git switch dev && git pull` → `git switch -c <type>/<topic>` → 작업 → `gh pr create --base dev`.
- 워크플로 잡 이름 `test`(ci.yml)·`main-from-dev-only`(pr-policy.yml)는 룰셋 필수 체크와 묶여 있다. 바꾸면 룰셋도 같이 바꾼다.
