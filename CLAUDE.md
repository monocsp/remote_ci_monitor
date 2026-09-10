# remote_ci_monitor

PLAN.md 가 정본이다. 세션을 시작하면 먼저 끝까지 읽는다.

## 브랜치 정책 (GitHub 룰셋으로 강제됨 — 자세한 건 PLAN.md 「브랜치 정책」)

- `main`·`dev` 에는 직접 커밋·push 할 수 없다. 관리자도 예외 없다. 시도하면 push 가 거부된다.
- 작업은 `dev` 에서 feature 브랜치를 파서 하고 `dev` 로 PR 을 보낸다.
- `main` 은 `dev` 에서 보낸 PR 로만 받는다. `test`·`main-from-dev-only` 체크가 통과해야 머지된다.
- **브랜치 하나에 워크트리 하나.** 이 레포는 형제 워크트리(`remote_ci_monitor-<topic>`)를 여러 개
  동시에 체크아웃해 두고 쓴다. **이미 있는 워크트리에서 `git switch` 로 브랜치를 갈아타지 마라** —
  다른 세션이 그 폴더에서 작업 중일 수 있고, 그 세션의 발밑을 빼는 짓이다. `remote_ci_monitor-dev`
  는 `dev` 를 두는 자리다. 새 작업은 새 워크트리다. 금지되는 건 브랜치를 **바꾸는** 것이지,
  그 워크트리를 자기 브랜치 그대로 `git fetch`·`git pull` 로 최신화하는 건 언제든 괜찮다.
- 세션 시작 절차 — 워크트리를 만들고 그 안에 자기 `.venv` 를 깐다:

  ```sh
  git fetch origin
  git worktree add -b <type>/<topic> ../remote_ci_monitor-<topic> origin/dev
  cd ../remote_ci_monitor-<topic>
  python3.11 -m venv .venv && ./.venv/bin/python -m pip install -e '.[dev]'
  # 작업 → gh pr create --base dev → 머지
  git worktree remove ../remote_ci_monitor-<topic>   # 머지된 뒤에 치운다
  ```
- **이름은 일의 일부다.** 브랜치를 만들기 전에 `branch` 스킬, 커밋하기 전에 `commit` 스킬, PR 을 열거나
  머지하기 전에 `pr` 스킬을 부른다(`/branch` `/commit` `/pr`). 브랜치는 `<type>/<scope>-<what-it-does>`
  (`fix/cli-ux` 처럼 어디인지만 말하는 이름은 안 된다), 커밋·PR 제목은 `<type>(<scope>): 한국어 요약`.
  `tools/guard_naming.py` 훅이 안 맞는 이름을 거부한다 — 정본은 `CONTRIBUTING.md` 「Names」.
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
