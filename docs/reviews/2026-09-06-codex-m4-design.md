# Codex 크로스리뷰 — M4 배포·문서 명세 (2026-09-06)

- 대상: `docs/m4-workplan.md`(패키징 · `rcm init` · 스모크 · 릴리스 워크플로 · Docker · README)
- 실행: `codex exec --sandbox read-only`(gpt-5.5). 질문 A~E.
- 반영: 「반드시 고칠 것」 10건 — ① hatchling ≥ 1.27 + `license-files`, license classifier 제거 ② 동적 버전 + 빌드 후 METADATA 재검사 ③ 템플릿·예시 바이트 동일 테스트 유지 ④ `config.py` 탐색이 `$XDG_CONFIG_HOME/rcm` 을 먼저 본다 ⑤ 임시 파일 + `os.replace`, 권한은 생성 시점부터 ⑥ `server = "…"` 정규식 치환, 정확히 1개 아니면 실패 ⑦ python 행 첫 줄 · `load_server_config(check_tools=False)` 로 git 행 ⑧ README 의 `<!-- smoke:begin/end -->` 블록의 `rcm …` 명령을 스크립트가 시작 시 자기 본문과 대조(빠지면 실패) ⑨ `git fetch origin +refs/heads/main:refs/remotes/origin/main` 뒤 `merge-base --is-ancestor` ⑩ 템플릿에 `ok` 프리셋, README 첫 실행은 `rcm run ok`. 「고치면 좋은 것」 — 빈 포트를 python socket 으로 · 서버 즉사 시 1회 재시도 · jq 없음 · `gh release view` 분기 · environment 이름 안내 · Docker 에 `ca-certificates openssh-client bash` · Docker 의 `ps` 한계 문서 · README 에 pipx/방화벽/health.
- 그대로 둔 것: PR 집계 `test` 에 macOS 스모크 포함(macOS unit 잡이 이미 필수라 러너 리스크가 새로 늘지 않는다 — 결정 29 에 기록).
- 오너에게: PyPI pending publisher 를 누가 만들지(결정 27) · `v*` 태그 룰셋(결정 28) · macOS 스모크 필수 여부(결정 29).

---

결론: M4 명세는 방향은 맞지만, 그대로 구현하면 “README만 보고 5분 안에 `rcm run`”보다 릴리스·CI·설정 경로의 함정이 먼저 터진다. 특히 `hatchling>=1.25`와 `license = "MIT"` 조합, `rcm init`의 XDG 경로와 기존 설정 로더 불일치, README 명령과 스모크 스크립트의 동기화 방식, 태그 릴리스의 `origin/main` 검증, 그리고 Docker에서 실제 호스트 관측이 제한된다는 점은 명세 단계에서 고쳐야 한다.

**반드시 고칠 것**

1. 1절 패키징: `license = "MIT"`를 쓸 거면 `hatchling>=1.27.0`으로 올리고 `[project] license-files = ["LICENSE"]`를 넣어라. `License :: OSI Approved :: MIT License` classifier는 빼라. PEP 639에서는 license classifier가 대체 대상이고, PyPA 문서는 hatchling PEP 639 지원 시작을 1.27.0으로 적고 있다.  
   근거: https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#license-and-license-files

2. 1절 패키징: `version = "0.1.0"`를 제거하고 `dynamic = ["version"]`, `[tool.hatch.version] path = "src/remote_ci_monitor/__init__.py"`로 바꿔라. 릴리스 워크플로는 태그 비교를 빌드 전에 한 번, 빌드 후 wheel `METADATA`/파일명으로 한 번 더 검사하게 해라.

3. 1절 템플릿: `examples/*.toml`과 `src/remote_ci_monitor/templates/*.toml` 중복은 그대로 두되, “바이트 동일 테스트”를 정식 규칙으로 삼아라. 단일 출처로 합치려고 `examples/`를 wheel에 넣으면 설치 산출물이 지저분해지고, symlink는 sdist/wheel/플랫폼에서 더 불안하다.

4. 2절 `rcm init`: XDG 규칙을 `init`에만 넣지 말고 `config.py`의 탐색 상수도 같이 바꿔라. 현재 로더는 `~/.config/rcm/*.toml`만 찾으므로, `XDG_CONFIG_HOME` 아래 생성하면 `rcm serve/check`가 못 찾는다.

5. 2절 `rcm init`: 파일 생성은 `os.open(..., O_CREAT|O_EXCL, 0o600/0o644)` 또는 임시 파일 후 `os.replace`로 해라. client는 처음부터 0600으로 만들고, `--force`도 교체 후 chmod가 아니라 교체 대상 파일 자체가 올바른 권한으로 생성되게 해라.

6. 2절 `--server` 치환: 고정 문자열 replace를 쓰지 말고 `^server\\s*=\\s*\"[^\"]*\"` 정규식으로 정확히 1개만 치환하고, 0개나 2개면 실패시켜라. 템플릿 줄이 바뀌었을 때 조용히 기본 URL이 남는 것이 최악이다.

7. 2절 `rcm check`: `python` 행을 무조건 첫 줄에 두고 `sys.version_info >= (3, 11, 4)`와 `hasattr(tarfile, "data_filter")`를 둘 다 검사해라. `git` 행은 `[[repos]]`가 있을 때만 만들되, `load_server_config`가 git 없음으로 바로 실패하지 않도록 `check_external_tools=False` 같은 경로를 추가해 행 단위로 보여줘라.

8. 3절 스모크: README 명령을 “포함 여부”로만 테스트하지 말고, README 5분 절의 fenced block에 안정적인 marker를 넣고 `scripts/smoke_install.sh`가 그 블록을 추출해 실행하게 해라. 변수 치환이 필요한 값은 `RCM_SMOKE_SERVER`, `RCM_SMOKE_TOKEN`처럼 환경변수로 주입해라.

9. 4절 릴리스: 태그 검증 전에 `actions/checkout`에 `fetch-depth: 0`을 주고, 별도로 `git fetch origin +refs/heads/main:refs/remotes/origin/main --tags`를 실행해라. 그 뒤 `git merge-base --is-ancestor "$GITHUB_SHA" origin/main`을 검사해야 `origin/main` 누락으로 오판하지 않는다.

10. 6절 README: 5분 절에 실제로 성공하는 기본 프리셋을 넣어라. 현재 예시의 `gate`는 `scripts/gate.sh`가 없으면 실패한다. `rcm init server` 템플릿에 `ok` 프리셋을 하나 넣고 README 첫 실행은 `rcm run ok`로 고정해라.

**고치면 좋은 것**

1. 3절 CI: PR의 집계 `test`에는 Linux smoke만 넣고 macOS smoke는 별도 잡으로 먼저 운영해라. 릴리스 워크플로에서는 ubuntu+macos 둘 다 필수로 둬라. macOS 러너 일시 장애로 main 병합이 막히는 리스크를 줄인다.

2. 3절 스모크: 포트는 Python socket으로 빈 포트를 고르되, 서버가 즉시 죽으면 로그를 찍고 포트를 한 번 더 고르는 재시도를 넣어라. 고정 포트는 CI 병렬 실행에서 불필요하게 깨진다.

3. 3절 스모크: JSON 파싱에 `jq`를 쓰지 말고 `python -c`만 써라. README의 `examples/session/ci-gate.sh`는 jq 의존 예시로 남겨도 되지만 스모크에는 넣지 마라.

4. 4절 릴리스: `gh release create`는 `gh release view "$TAG"`로 존재 여부를 먼저 보고, 있으면 `gh release upload "$TAG" dist/* --clobber`, 없으면 create로 분기해라. `permissions: contents: write`는 GitHub Release 잡에만 줘라.

5. 4절 PyPI: `environment: pypi` 이름은 PyPI trusted publisher 설정과 정확히 같아야 한다고 README 릴리스 절에 박아라. PyPI도 workflow/environment 불일치를 실패 원인으로 문서화한다.  
   근거: https://docs.pypi.org/trusted-publishers/using-a-publisher/

6. 5절 Docker: `git procps`에 `ca-certificates openssh-client bash`를 추가해라. `git_ref`의 HTTPS/SSH와 예시 bash 프리셋을 실제로 돌리려면 필요하다.

7. 5절 Docker 문서: Docker에서는 `ps`가 컨테이너 네임스페이스만 볼 수 있고 호스트 관측이 네이티브 서비스보다 부정확하다고 써라. 정확한 호스트 압력이 필요하면 systemd/launchd 설치를 권장해라.

8. 6절 README: `pipx` 미설치, `pipx ensurepath` 후 새 셸 필요, Python 3.11.4+, macOS 방화벽, Tailscale IP/MagicDNS 확인, `curl http://<host>:8787/api/health`를 5분 절 안에 넣어라.

**그대로 둘 것**

1. `examples/`와 패키지 `templates/`를 바이트 동일 테스트로 잠그는 방식은 유지해라.

2. 런타임 의존성 0 원칙은 유지해라. `build`, `twine`은 dev/CI 도구로만 설치하면 된다.

3. `test` 집계 잡 이름은 유지해라. 룰셋이 잡 이름을 보고 있으므로 바꾸면 운영 리스크가 크다.

4. 릴리스는 태그 `v*` 트리거로 두되, main 포함성 검사를 강화하는 쪽이 맞다.

5. Docker는 Linux 서버 이미지 범위로만 둬라. macOS 빌드 머신은 launchd 네이티브 경로가 맞다.

**오너에게 물어야 할 것**

1. PyPI 첫 배포를 “pending trusted publisher로 새 프로젝트 생성”할지, 아니면 오너가 먼저 프로젝트를 수동 생성할지 정해야 한다.

2. GitHub tag ruleset에서 `v*` 생성/수정/삭제 권한을 누구에게 줄지 정해야 한다.

3. PR에서 macOS smoke를 필수 체크로 막을지, 릴리스에서만 필수로 둘지 운영 기준을 정해야 한다.