# remote_ci_monitor

PLAN.md 가 정본이다. 세션을 시작하면 먼저 끝까지 읽는다.

## 브랜치 정책 (GitHub 룰셋으로 강제됨 — 자세한 건 PLAN.md 「브랜치 정책」)

- `main`·`dev` 에는 직접 커밋·push 할 수 없다. 관리자도 예외 없다. 시도하면 push 가 거부된다.
- 작업은 `dev` 에서 feature 브랜치를 파서 하고 `dev` 로 PR 을 보낸다.
- `main` 은 `dev` 에서 보낸 PR 로만 받는다. `test`·`main-from-dev-only` 체크가 통과해야 머지된다.
- 세션 시작 절차: `git switch dev && git pull` → `git switch -c <type>/<topic>` → 작업 → `gh pr create --base dev`.
- 워크플로 잡 이름 `test`(ci.yml)·`main-from-dev-only`(pr-policy.yml)는 룰셋 필수 체크와 묶여 있다. 바꾸면 룰셋도 같이 바꾼다.

## 운영 설치 — 이 머신이 빌드 머신이면 (AGENTS.md 「The build machine's own install」)

운영 서비스의 venv 가 이 레포의 체크아웃 하나를 editable 로 가리킬 수 있다. 그 폴더가 곧 운영이다.

- 그 폴더는 `main` 에 두고 안의 파일을 고치지 않는다. `git pull --ff-only` + 서비스 재시작으로만 바꾼다.
- 개발은 워크트리와 그 안의 `.venv` 에서 한다. 시험용 서버는 자기 설정 파일 · `port` · `data_dir` 을 쓴다.
- `tools/guard_production.py` 훅(`.claude/settings.json`)이 막아 준다 — 운영 체크아웃·설정·데이터를
  고치거나 운영 설정으로 서버를 띄우면 거부되고, 배포(서비스 venv 설치 · 재시작)는 물어본다.
- 절차: `docs/operating.md` 「From a git checkout」.

## 에이전트 안내

`AGENTS.md` 를 읽는다 — 브랜치 정책 · 집안 규칙 · 검사 명령 · 이미 값을 치른 함정이 거기 있다.
문서를 건드리는 작업은 `docs` 스킬(`.claude/skills/docs/SKILL.md`)의 체크리스트를 따른다.
