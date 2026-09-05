# M4 작업 명세 — 배포·문서 (패키징 · `rcm init` · 릴리스 · README 5분 셋업)

> PLAN.md 「M4 — 배포·문서」의 구현 명세. **Codex 리뷰(`docs/reviews/2026-09-06-codex-m4-design.md`) 반영본** — 바뀐 곳은 「(리뷰 반영)」. 완료 기준(PLAN): **새 머신에서 README 만 보고 5분 안에 `rcm run` 이 된다.** 그걸 사람 대신 스크립트(`scripts/smoke_install.sh`)가 매번 증명한다. 브랜치 `feat/m4-release` → PR → `dev` → (릴리스) PR `dev` → `main` → 태그 `v0.1.0`.

## 0. 범위와 지금 상태

| 항목 | 지금(M3 까지) | M4 에서 |
|---|---|---|
| `pyproject.toml` | hatchling · deps 0 · scripts `rcm`·`remote-ci-monitor` · wheel 에 `web/` | 버전을 `__init__.__version__` 에서(동적) · `license` + `LICENSE` · 분류자·URL·키워드 · sdist 에 `examples/`·`LICENSE`·`CHANGELOG.md` · 패키지 데이터 `templates/` |
| 설정 만들기 | `cp examples/server.toml ~/.config/rcm/server.toml` | `rcm init server` · `rcm init client --server URL` 이 패키지 안 템플릿을 쓴다(덮어쓰기 금지, `--force`) |
| `rcm version` · `rcm check` | 버전만 · 클라이언트 관점 | `rcm version` 에 python·OS(`--json`) · `rcm check` 에 python(3.11.4+ tar 필터)·git(repos 가 있을 때) 행 |
| 릴리스 | 없음 | `.github/workflows/release.yml`: 태그 `v*` → main 위인지·버전 일치 확인 → 빌드 → 두 OS 에서 wheel 설치 스모크 → GitHub Release(wheel·sdist) → PyPI(trusted publishing, 저장소 변수 `PYPI_PUBLISH=true` 일 때만) |
| 스모크 | `test_wheel_ships_web_assets` 만 | `scripts/smoke_install.sh`: 새 venv 에 wheel 설치 → README 5분 절차를 루프백으로 그대로 실행. CI `smoke` 잡(ubuntu·macos)이 PR 마다 돌리고 집계 `test` 가 `needs` 에 포함 |
| Docker | 없음 | Linux 서버 이미지 `Dockerfile`(+ `.dockerignore`) · README 절. 빌드는 CI 밖(선택) |
| 문서 | README(M1 문구) · PLAN | README 를 5분 절차 중심으로 재구성(Install · Build machine · Session machine · 이후 기존 절) · `CHANGELOG.md` · PLAN M4 완료·결정 26~27 |

바꾸지 않는 것: 런타임 의존성 0 · `test` 잡 이름 · 브랜치 정책 · 스키마 v1 · 서버 API.

## 1. 패키징

- `pyproject.toml`: `dynamic = ["version"]` + `[tool.hatch.version] path = "src/remote_ci_monitor/__init__.py"`(단일 출처 — `rcm version` · `/api/status.server.version` · wheel 이름이 같다). (리뷰 반영) `hatchling >= 1.27` · `license = "MIT"`(PEP 639 SPDX) + `license-files = ["LICENSE"]`, **license classifier 는 넣지 않는다**(PEP 639 에서 대체 대상). `classifiers` 에 `Development Status :: 4 - Beta` · `Programming Language :: Python :: 3.11/3.12/3.13` · `Intended Audience :: Developers`. `[project.urls]` Homepage · Repository · Issues · Changelog. `keywords`. `[tool.hatch.build.targets.sdist] include` 에 `examples/`, `LICENSE`, `CHANGELOG.md` 추가(tests 는 제외 유지).
- 패키지 데이터 `src/remote_ci_monitor/templates/server.toml` · `client.toml` — **`examples/server.toml` · `examples/client.toml` 과 바이트 단위로 같다**(테스트가 잠근다. 예시를 고치면 템플릿도 고친다).
- `dev` extra 에 `build` 추가(스모크·릴리스가 `python -m build` 를 쓴다). 런타임 의존성은 그대로 0.

## 2. CLI

### 2.1 `rcm init`

```
rcm init server [--path PATH] [--force]
rcm init client --server URL [--path PATH] [--force]
```

- 기본 경로: server → `~/.config/rcm/server.toml`, client → `~/.config/rcm/client.toml`(`$XDG_CONFIG_HOME` 이 있으면 그 아래 `rcm/`). 부모 디렉터리는 만든다. (리뷰 반영) `config.py` 의 탐색(`find_server_config` · `find_client_config`)도 `$XDG_CONFIG_HOME/rcm/*.toml` 을 **먼저** 본다 — `rcm init` 이 만든 파일을 `rcm serve/check` 가 못 찾는 일이 없게(`config.user_config_dir()` 하나가 두 곳을 결정).
- 이미 있으면 **덮어쓰지 않고** 종료 2 + `refusing to overwrite <path> (use --force)`. `--force` 면 덮어쓴다.
- client 는 (리뷰 반영) 정규식 `^server\s*=\s*"[^"]*"` 로 **정확히 한 줄**을 치환한다(0개·2개면 실패 — 템플릿이 바뀌었는데 기본 URL 이 조용히 남는 게 최악). 파일 권한 **0600**(토큰을 넣을 파일). `--server` 는 `http://`·`https://` 로 시작해야 한다(아니면 usage 2). 끝의 `/` 는 뗀다.
- server 는 템플릿 그대로. 권한 0644. (리뷰 반영) 쓰기는 임시 파일을 `O_CREAT|O_EXCL` + 원하는 mode 로 만들고 `os.replace` — 잘못된 권한의 순간이 없다(`--force` 도 같은 경로). 템플릿에는 새 설치가 전체 경로를 증명할 `ok` 프리셋(`sh -c "echo ::rcm::step::hello; echo ok"`)이 들어 있다 — README 첫 실행은 `rcm run ok`.
- 성공 시 stdout 에 경로 한 줄, stderr 에 다음 단계 안내(server: `edit presets → rcm token add <name> → rcm serve`, client: `export RCM_TOKEN=… → rcm check`). 종료 0.
- 템플릿은 `importlib.resources.files("remote_ci_monitor") / "templates" / …` 로 읽는다(wheel 에 들어간다 — 테스트).

### 2.2 `rcm version`

- `rcm 0.1.0 (Python 3.13.2, darwin arm64)` 한 줄. `--json` 이면 `{"version": "0.1.0", "python": "3.13.2", "platform": "darwin", "machine": "arm64", "schema_version": 1}`.

### 2.3 `rcm check` 추가 행

- `python` — `3.11.4` 이상이고 `tarfile.data_filter` 가 있으면 ok, 아니면 FAIL `tarfile data filter needs Python 3.11.4+`. 항상 첫 행.
- `git` — 서버 설정이 있고 `[[repos]]` 가 하나라도 있으면 `shutil.which("git")` 을 확인(없으면 FAIL). repos 가 없으면 행을 만들지 않는다. (리뷰 반영) `load_server_config(..., check_tools=False)` 로 읽어 로더가 git 부재로 통째로 실패하지 않게 한다 — 행 단위로 보여준다.
- 기존 행(client config · server · token · presets · timezone · data dir)은 그대로.

## 3. 스모크 — `scripts/smoke_install.sh`

README 「Build machine」「Session machine」 블록을 **그대로** 실행한다. (리뷰 반영) 기계적 보장: README 의 두 블록을 `<!-- smoke:begin -->` … `<!-- smoke:end -->` 로 감싸고, 스크립트가 시작할 때 그 안의 `rcm …` 명령(앞 세 단어, 주석 제거)을 전부 뽑아 **자기 본문에 있는지** 대조한다 — README 에 명령을 더했는데 스크립트가 안 돌리면 첫 단계에서 실패.

```
usage: scripts/smoke_install.sh [WHEEL]     # WHEEL 없으면 `python -m build --wheel` 로 만든다
```

1. 임시 디렉터리에 `python3 -m venv` → `pip install <wheel>`(런타임 의존성 0 이라 인터넷 불필요) → `rcm version` 이 wheel 버전과 같다.
2. **Build machine 절차**: `HOME=<tmp>` 로 `rcm init server`(템플릿의 `ok` 프리셋이 있는지 확인) → `rcm token add laptop`(토큰은 stdout 한 줄) → (리뷰 반영) 빈 포트를 python `socket` 으로 고르고 `rcm serve --port <포트>` 를 백그라운드로(데이터 디렉터리는 tmp HOME 아래 기본값) → `/api/health` 가 200 이 될 때까지 최대 30초, 서버가 즉시 죽으면 로그를 찍고 포트를 다시 골라 1회 재시도.
3. **Session machine 절차**: `rcm init client --server http://127.0.0.1:<port>` → `RCM_TOKEN=<토큰> rcm check` 가 0 → 빈 디렉터리(파일 하나)에서 `rcm run ok` 가 **0** 이고 stdout JSON 의 `state` 가 `succeeded` → `rcm top` 에 `ok` 행이 최근에 보인다 → `rcm jobs --json` 파싱 가능.
4. 웹: `curl -sf http://127.0.0.1:<port>/` 가 `<title>rcm queue</title>` 을 포함.
5. 서버에 SIGTERM → 5초 안에 종료. 임시 디렉터리 삭제(`trap`).
6. 어느 단계든 실패하면 그 단계 이름을 찍고 종료 1. 성공이면 `smoke: ok (rcm <version>, <python>, <os>)`.

- bash 만 쓴다(`set -euo pipefail`), macOS 기본 bash 3.2 에서도 돈다(연관 배열 금지 · `timeout` 없음). `python3` · `curl` 만 필요, `jq` 는 쓰지 않는다(JSON 은 `python -c`). 실패하면 단계 이름과 서버 로그 tail 을 찍는다.
- CI: `ci.yml` 에 `smoke` 잡(matrix ubuntu-latest · macos-latest, Python 3.13): `pip install build` → `python -m build --wheel` → `scripts/smoke_install.sh dist/*.whl`. 집계 `test` 는 `needs: [unit, secrets, smoke]` 이고 셋 다 success 여야 통과(잡 이름 `test` 유지).

## 4. 릴리스 — `.github/workflows/release.yml`

트리거 `push: tags: ["v*"]`. 잡:

1. `build` (ubuntu): `fetch-depth: 0` → (리뷰 반영) `git fetch origin +refs/heads/main:refs/remotes/origin/main --tags` 를 명시한 뒤 **태그 커밋이 `origin/main` 의 조상**인지(`git merge-base --is-ancestor`, 아니면 실패 「release tags must point at a commit on main」) → 태그 `vX.Y.Z` 의 `X.Y.Z` 가 `__version__` 과 같은지 → `python -m build` → `twine check dist/*` → (리뷰 반영) 빌드 후 wheel 파일명·METADATA `Version` · `web/`·`templates/` 포함을 다시 검사 → 아티팩트 `dist` 업로드.
2. `smoke` (matrix ubuntu · macos, needs build): 아티팩트 내려받아 `scripts/smoke_install.sh dist/*.whl`.
3. `github-release` (needs smoke, 이 잡에만 `permissions: contents: write`): CHANGELOG 에서 `## [X.Y.Z]` 절을 잘라 노트로(python 인라인) → (리뷰 반영) `gh release view "$TAG"` 로 있으면 `gh release upload --clobber`, 없으면 `gh release create`.
4. `pypi` (needs smoke, `if: vars.PYPI_PUBLISH == 'true'`, `environment: pypi`, `permissions: id-token: write`): `pypa/gh-action-pypi-publish@release/v1`. 오너가 PyPI 에 trusted publisher(owner `monocsp` · repo `remote_ci_monitor` · workflow `release.yml` · environment `pypi`)를 등록하고 저장소 변수 `PYPI_PUBLISH=true` 를 켜기 전까지는 **건너뛴다**(결정 27). 그 전에도 GitHub Release 의 wheel 로 `pipx install <release wheel URL>` 이 된다.

- 릴리스 절차(README 「Releasing」·PLAN): `dev` → `main` PR(`main-from-dev-only` · `test` 통과) → 머지 → `git tag v0.1.0 <main sha> && git push origin v0.1.0` → 워크플로. 버전 올리기는 `__init__.py` 한 곳 + `CHANGELOG.md`.

## 5. Docker (Linux 서버)

- `Dockerfile`: `FROM python:3.12-slim` → (리뷰 반영) `apt-get install -y --no-install-recommends git ca-certificates openssh-client procps bash`(git_ref 의 https·ssh · `ps` · 예시 프리셋의 bash) → 사용자 `rcm`(uid 1000) → `COPY` 소스 → `pip install --no-cache-dir .` → `USER rcm` → `VOLUME ["/data", "/config"]` → `EXPOSE 8787` → `ENTRYPOINT ["rcm"]` · `CMD ["serve", "--config", "/config/server.toml", "--data-dir", "/data", "--bind", "0.0.0.0"]`. 컨테이너 안이라 `0.0.0.0` 이 기본이지만 호스트 쪽 포트 매핑을 `127.0.0.1:8787:8787` 또는 Tailscale IP 로 제한하라고 README 에 쓴다. GPU: 이미지에 `nvidia-smi` 가 없으니 `gpu: null`(통과) — 필요하면 `--gpus all` + nvidia 베이스 이미지는 사용자 몫. (리뷰 반영) 컨테이너 안 `ps`·`/proc` 은 컨테이너만 보므로 호스트 압력이 네이티브 서비스보다 부정확하다고 README 에 쓴다.
- `.dockerignore`: `.git` · `.venv` · `tests` · `docs` · `dist` · `__pycache__` · `.pytest_cache`.
- 테스트는 파일 문면만(`FROM python:3.1`, `USER rcm`, `EXPOSE 8787`, `ENTRYPOINT ["rcm"]`, git 설치). 이미지 빌드는 CI 밖.

## 6. 문서

- **README** 재구성(영어): 머리 상태 문구 → `Status: M0–M4 done (v0.1.0) — server, queue, worker, live events, web UI, git_ref deploys, retention, service files. M5 (multi-machine, GitHub backend) is next.` 절 순서: Install(Python 3.11.4+ · pipx from PyPI · `uvx --from remote-ci-monitor rcm` · 릴리스 전엔 `pipx install git+https://github.com/monocsp/remote_ci_monitor` / GitHub Release wheel · (리뷰 반영) pipx 미설치 시 `pipx ensurepath` + 새 셸 · bind · macOS 방화벽 · `curl …/api/health`) → **Build machine (3 commands)**: `rcm init server` → edit presets → `rcm token add` → `rcm serve`(+ service 절 링크) → **Session machine (3 commands)**: `rcm init client --server` → `export RCM_TOKEN` → `rcm check` → `rcm run` → 이후 기존 절(Presets · Session commands · Web UI · Exit codes · Security · Retention · Run as a service · **Docker (Linux)** · Why the numbers can be wrong · Verify on the real build machine · **Releasing** · Development). 「Verify」 절에 M3 항목 3개 추가(git_ref 실제 원격 · basic 프롬프트 · retention). Development 절의 「three known mutations」 → 8.
- `CHANGELOG.md`(Keep a Changelog): `## [0.1.0] - 2026-09-06` — M0~M4 요약(Added) · `schema_version 1` · 알려진 제한(Windows 범위 밖 · GPU 는 Apple Silicon/NVIDIA · 부분 업로드 재개 없음). `[Unreleased]` 절.
- `PLAN.md`: M4 완료 · 결정 26(MIT) · 27(PyPI trusted publishing 은 변수로 켬) · 「패키징·배포 (M4)」 절을 실제 모양으로 · 세션 시작 프롬프트를 M5 용으로.

## 7. 테스트 배치(서브에이전트 분담)

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_cli_m4.py` | A | `rcm init server/client`(기본 경로 · XDG · 덮어쓰기 거부 2 · `--force` · 0600 · `--server` 검증·치환 · 안내 문구) · `rcm version` / `--json` · `rcm check` 의 python·git 행 |
| `tests/test_packaging.py` | A | 템플릿 == 예시(바이트) · `pyproject` 동적 버전(`tomllib` 로 읽어 `dynamic` 과 `[tool.hatch.version].path`) · `LICENSE` 존재 + `license` 필드 · wheel 에 `templates/` 두 파일(기존 `test_wheel_ships_web_assets` 방식, 오프라인이면 skip) · `CHANGELOG` 첫 절 버전 == `__version__` |
| `tests/test_release_files.py` | B | `release.yml` 문면(트리거 `v*` · `merge-base --is-ancestor` · `twine check` · `pypa/gh-action-pypi-publish` · `id-token: write` · `vars.PYPI_PUBLISH` 조건 · `environment: pypi` · `smoke_install.sh` 호출 · `gh release`) · `ci.yml` 에 `smoke` 잡과 `test` 의 `needs` 셋 · `Dockerfile`·`.dockerignore` 문면 · `scripts/smoke_install.sh` 가 `bash -n` 을 통과하고 README 의 5분 명령(`rcm init server` · `rcm token add` · `rcm serve` · `rcm init client` · `rcm check` · `rcm run`)을 전부 담는다 · README 절 순서와 문구(Install · Build machine · Session machine · Docker · Releasing · Status 줄) · `examples/session/ci-gate.sh` `bash -n` |

규칙: 서브에이전트는 `src/`·`scripts/`·워크플로·README 를 만들지 않는다(문면 검사는 아직 없는 파일에 대해 빨간 채로 인계). YAML 파서는 없다(표준 라이브러리만) — 문면·정규식으로 검사한다. 각자 가정을 `docs/m4-test-scenarios-<담당>.md` 에 적는다.

## 8. 순서

① 이 명세 → Codex 리뷰(`docs/reviews/2026-09-06-codex-m4-design.md`) 반영 ② 테스트-퍼스트 A·B 병행 ③ 구현(pyproject/LICENSE/templates → cli init/version/check → smoke 스크립트 + ci.yml → release.yml → Dockerfile → README/CHANGELOG/PLAN) ④ 격리 워크트리 에이전트: pytest·node·ruff·mutcheck 8/8 + **`scripts/smoke_install.sh` 를 실제로**(wheel 빌드 → 새 venv → README 절차) + `bash -n`·`plutil` ⑤ PR → CI(`smoke` 포함) → `dev` 머지 ⑥ 릴리스: PR `dev` → `main` → 머지 → 태그 `v0.1.0` → release 워크플로(GitHub Release 까지; PyPI 는 오너가 변수를 켠 뒤).
