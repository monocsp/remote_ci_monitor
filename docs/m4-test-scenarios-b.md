# M4 테스트 시나리오 — 담당 B: 릴리스·배포 파일 문면 (2026-09-06)

`docs/m4-workplan.md` §3(스모크) · §4(release.yml) · §5(Docker) · §6(문서) · §7(분담표의 B 행)을
`tests/test_release_files.py` 로 옮긴 것이다. **함수 33 · parametrize 포함 78건.** `src/` · `scripts/` ·
워크플로 · README · Dockerfile 은 만들지 않았다. 쓰는 동안 구현이 같은 워크트리에 병렬로 들어와서(release.yml ·
smoke_install.sh · Dockerfile · README 재구성 · CHANGELOG · LICENSE) 인계 시점에는 **78건 전부 초록**이다.
빨갰다가 초록이 된 경로는 두 갈래 — (a) 파일이 생기며 자연히 초록, (b) 구현이 명세와 다른 곳 4건은 테스트를
완화했다(아래 「명세 이슈」 1~3, 6).

| 절 | 대상 | 함수 | 건수 |
|---|---|---|---|
| release.yml | 트리거 · build(main 조상 · 버전 · build/twine/artifact) · smoke matrix · github-release · pypi | 8 | 8 |
| ci.yml | 잡 이름 `unit`·`secrets`·`test` 유지 · `smoke` 잡 · `test` 의 `needs` 셋 | 3 | 5 |
| scripts/smoke_install.sh | 실행 비트·셔뱅 · `bash -n` · strict/trap/HOME · rcm 명령 9 · 문면 10 · bash 3.2 · README 마커 | 7 | 26 |
| Dockerfile · .dockerignore | FROM · apt git · 사용자 rcm · ENTRYPOINT/CMD · EXPOSE/VOLUME · ignore 3 | 6 | 8 |
| README.md | Status 줄 · 절 순서 · 절별 문구 19 · Development 「8 mutations」 | 4 | 22 |
| CHANGELOG.md | `[Unreleased]`·`[0.1.0] - 날짜` 순서 · 0.1.0 절 용어 4 | 2 | 5 |
| examples · LICENSE · pyproject | plist·unit 존재 · `MIT License` · `license` 필드 | 3 | 4 |

공통 규칙: **YAML 파서 없음**(표준 라이브러리만) — 정규식·줄 스캔·들여쓰기로만 본다. 워크플로·이미지·스크립트는
돌리지 않는다(`bash -n` 만, bash 가 PATH 에 없으면 skip). 네트워크·docker 불필요. 전체 0.05초 안쪽. 아직 없는
파일은 `read()` 의 `assert path.is_file(), "missing: …"` 로 빨갛다(import 오류 아님).

## 공용 도우미

- `read(path)` — 존재 확인 + 문면. `has(text, pattern, flags=re.M)` — 정규식 검색.
- `job_block(text, job)` — `jobs:` 아래 `  <job>:` 부터 **같은 들여쓰기의 다음 키 직전까지**. 들여쓰기 폭은
  첫 매치에서 읽으므로 2칸이 아니어도 된다. `job_head(block)` — `steps:` 앞부분. `needs` · `if` · `permissions` ·
  `environment` 는 여기(잡 수준)에 있어야 통과한다. 스텝 안에 같은 키가 있어도 잡 수준으로 안 친다.
- `needs_of(block)` — `needs: [a, b]` · `needs: a` · 블록 목록 `- a` 세 꼴을 집합으로. 주석·따옴표 제거.
- `heading(text, prefix)` / `section(text, prefix)` — `## <prefix>` 로 **시작하는** 2단계 제목(대소문자 무시,
  `(3 commands)` 같은 꼬리 허용)부터 다음 `## ` 직전까지. `###` 하위 절은 포함된다.
- `dockerfile_flat()` — `\` 줄이음을 붙여 `RUN … \ && useradd …` 가 한 줄로 보이게.

## 1. release.yml — 8건 (§4)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 트리거 | `on:` · `push:` · `tags:` 값이 `v*`(flow `["v*"]` 든 블록 `- "v*"` 든, 따옴표 유무 무관) |
| 2 | build: main 위 태그만 | `fetch-depth: 0` · `git merge-base --is-ancestor` · `origin/main` · 실패 문구 `release tags must point at … main` |
| 3 | build: 버전 일치 | `__version__` 문자열 · 태그를 `github.ref_name` / `GITHUB_REF_NAME` / `GITHUB_REF` 로 읽는다 |
| 4 | build: 빌드·검사·업로드 | `python -m build` · `twine check` · `actions/upload-artifact` · `name:`/`path:` 가 `dist` |
| 5 | smoke | `needs` 에 `build` · `matrix` · `ubuntu-latest`+`macos-latest` · `download-artifact` · `scripts/smoke_install.sh … dist/` |
| 6 | github-release | `needs` 에 `smoke` · **잡 수준** `permissions:` + `contents: write` · `download-artifact` · `gh release … dist/` |
| 7 | github-release 재실행·노트 | `CHANGELOG` 언급(해당 절이 노트) · `--clobber`(이미 있으면 업로드만) |
| 8 | pypi | `needs` 에 `smoke` · `if:` 에 `vars.PYPI_PUBLISH` 와 `'true'` · `environment: pypi`(또는 `name: pypi`) · `id-token: write` · `download-artifact` · `pypa/gh-action-pypi-publish` |

## 2. ci.yml — 5건 (§3)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 잡 이름 유지 (×3) | `unit` · `secrets` · `test` 블록이 있고 각 `name: <잡>` 그대로 — `test` 는 main 룰셋 필수 체크(PLAN 「브랜치 정책」) |
| 2 | `smoke` 잡 | `matrix` 에 `ubuntu-latest`+`macos-latest` · `python -m build`(또는 `pip wheel`) · `scripts/smoke_install.sh` |
| 3 | `test` 집계 | `if: always()` · `needs ⊇ {unit, secrets, smoke}` · 셸 스텝이 `needs.unit.result` · `needs.secrets.result` · `needs.smoke.result` 를 본다 |

## 3. scripts/smoke_install.sh — 26건 (§3)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 실행 가능한 bash (×2: smoke · `examples/session/ci-gate.sh`) | `os.access(X_OK)` · 첫 줄 `#!/usr/bin/env bash` 또는 `#!/bin/bash` |
| 2 | 문법 (×2) | `bash -n` 종료 0 (bash 없으면 skip) |
| 3 | strict · 정리 · 격리 | `set -euo pipefail` · `trap … EXIT` · `HOME=` 또는 `XDG_CONFIG_HOME=` 덮어쓰기(진짜 `~/.config/rcm` 을 건드리면 안 된다) |
| 4 | README 명령 전부 (×9) | `rcm version` · `init server` · `token add` · `serve` · `init client` · `check` · `run` · `top` · `jobs` — `rcm run` 도 `"$VENV/bin/rcm" run` 도 받는 정규식 |
| 5 | 문면 (×10) | `python3` · `curl` · `-m venv` · `pip install` · `/api/health` · `RCM_TOKEN` · `succeeded` · `rcm queue`(`<title>`) · `kill -TERM`/`SIGTERM` · `smoke: ok` |
| 6 | macOS 기본 bash 3.2 | `declare -A` 없음 · `mapfile`/`readarray` 없음 · **줄 머리** `timeout ` 없음(`--timeout` 플래그는 무관) |
| 7 | README 마커 ↔ 스크립트 | README 의 `<!-- smoke:begin -->…<!-- smoke:end -->` 블록 안 `rcm …` 줄(앞 세 단어)이 전부 스크립트 문면에 있다. 스크립트가 마커를 안 쓰면 skip |

## 4. Dockerfile · .dockerignore — 8건 (§5)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 베이스 | `FROM python:3.11+`(`3.1[1-9]` 또는 `3.2x`; 태그 꼬리 무관) |
| 2 | git | `RUN … apt-get … install` 줄(줄이음 합침)에 `--no-install-recommends` 와 `git` |
| 3 | 비루트 | `useradd`/`adduser … rcm` · `USER rcm` · `pip install` |
| 4 | 진입점 | `ENTRYPOINT ["rcm"]` · `CMD` 에 `serve` · `--bind` · `0.0.0.0` · `--data-dir` · `/data` |
| 5 | 포트·볼륨 | `EXPOSE 8787` · `VOLUME … /data` |
| 6 | .dockerignore (×3) | `.git` · `tests` · `.venv` 가 한 줄로(끝의 `/`·`**` 허용) |

## 5. README.md — 22건 (§6)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | Status 줄 | `Status:`(별표 감쌈 허용) 줄에 `M4` 또는 `v0.1.0` · `Status: **M1**` 없음 |
| 2 | 절 순서 | `## Install` → `## Build machine` → `## Session machine` → `## Docker` → `## Releasing` → `## Development` 위치가 단조 증가 |
| 3 | 절별 문구 (×19) | Install: `pipx install remote-ci-monitor` · `uvx` · `git+https://github.com/monocsp/remote_ci_monitor` / Build machine: `rcm init server` · `rcm token add` · `rcm serve` / Session machine: `rcm init client --server` · `RCM_TOKEN` · `rcm check` · `rcm run` / Docker: `docker build|run` · `127.0.0.1:8787:8787` 또는 Tailscale / Releasing: dev→main(화살표 · `--base main --head dev` · "PRs from dev") · `git tag v` · `PYPI_PUBLISH` · trusted publish / Verify: `--ref` · `read_auth` · `retention` |
| 4 | Development | `three known mutations` 없음 · `8` 이 `mutation` 과 40자 안에 |

## 6. CHANGELOG.md — 5건 (§6)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 골격 | `## [Unreleased]` · `## [0.1.0] - YYYY-MM-DD` · Unreleased 가 위(Keep a Changelog) |
| 2 | 0.1.0 절 (×4) | `schema_version 1`(또는 `schema version 1`) · `git_ref` · `web UI` · `retention` — 절은 다음 `## [` 직전까지 |

## 7. examples · LICENSE · pyproject — 4건

plist · systemd unit 파일 존재(회귀) · `LICENSE` 에 `MIT License` · `pyproject.toml` 에 `license` 키와 `MIT`
(동적 버전 · 분류자 · 템플릿 == 예시는 A 의 `test_packaging.py`).

## 가정

1. **잡 이름을 고정**한다: release.yml `build` · `smoke` · `github-release` · `pypi`, ci.yml `unit` · `secrets` ·
   `smoke` · `test`. `needs.<잡>.result` 참조와 룰셋 필수 체크(`test`)가 이름에 묶여 있어서 이름 자체가 계약이다.
2. `needs` · `if` · `permissions` · `environment` 는 잡 블록의 `steps:` **앞**에 있어야 한다. 최상위
   `permissions: contents: write` 로는 github-release 테스트가 통과하지 않는다(최소 권한 — 최상위는 read 로 두고
   그 잡만 write).
3. 버전 일치 검사는 「`__version__` 문자열 + 태그를 읽는 표현」까지만 본다. 실제 비교 논리는 릴리스에서 돈다.
4. release.yml 의 실패 문구는 `release tags must point at … main` 으로 완화했다(명세 §4 문구는 「… at main」,
   구현은 「… at a commit on main」).
5. `--clobber` 와 `CHANGELOG` 는 명세 §4 문장 그대로 잠갔다. 재실행 안전성을 다른 방식(예: `gh release view` 만)
   으로 구현하면 이 두 문자열은 그대로 두는 게 싸다.
6. `pypi` 잡도 `download-artifact` 를 해야 한다(과제 목록엔 없지만 dist 없이는 publish 가 안 된다).
7. README 제목은 **접두 일치**다: `## Build machine (3 commands)` 는 통과, `## Installation` 은 `Install\b` 라
   **불통과**. 절은 다음 `## ` 까지라서 `###` 하위 절 문구도 절에 포함된다.
8. Docker 절은 `docker build|run` 과 「포트를 `127.0.0.1:8787:8787` 또는 Tailscale IP 로 좁혀라」는 안내(§5)를
   요구한다. CMD 의 `--config /config/server.toml` 은 잠그지 않았다(README 가 `-v …:/config/server.toml` 로 설명).
9. 스모크 스크립트의 rcm 명령 검사는 `rcm <하위명령>` 정규식이라 `"$RCM" run ok` 처럼 변수로 부르면 문면상
   `rcm run` 이 없을 수 있다 — 지금 구현은 `step "rcm run ok"` 라벨로 같은 문자열을 갖고 있고, 스크립트 0단계의
   README 커버리지 자기검사(`grep -qF "$cmd" "$0"`)도 같은 문자열에 기댄다. 라벨을 지우면 둘 다 빨개진다.
10. `--data-dir` 는 잠그지 않았다. 템플릿의 `data_dir = "~/.local/share/rcm"` 이 `HOME` 을 따라가므로
    `HOME=$WORK/home` 덮어쓰기(잠금)만으로 격리된다. 템플릿 기본값이 절대 경로가 되면 이 가정이 깨진다.
11. `timeout` 금지는 「줄 머리가 `timeout `」만 본다(`curl --max-time` · `--timeout` 플래그는 무관).
12. Dockerfile `FROM` 은 `python:3.11` 이상이면 어떤 꼬리(`-slim` 등)든 받는다. `.dockerignore` 는 과제의 3항목만
    잠근다(명세의 `docs`·`dist`·`__pycache__`·`.pytest_cache` 는 자유).
13. CHANGELOG 0.1.0 제목엔 ISO 날짜가 있어야 한다(`## [0.1.0] - 2026-09-06`). 버전 == `__version__` 은 A 가 본다.
14. 실행 비트는 `os.access(X_OK)` — git 이 mode 755 를 보존하는 POSIX 체크아웃을 전제한다(Windows 는 범위 밖).
15. README 마커 테스트(3-7)는 구현의 `<!-- smoke:begin/end -->` 방식에 기댄다. 마커를 버리면 실패가 아니라 skip 이다
    — 대신 3-4(고정 명령 9개)가 하한을 지킨다.

## 명세 이슈 · 구현이 명세와 다른 곳

1. **§3.2 「`[[presets]]` 뒤에 `ok` 프리셋을 `cat >>` 로 덧붙인다」 → 구현은 `ok` 를 템플릿에 넣었다.**
   `examples/server.toml` == `templates/server.toml`(바이트 동일 잠금) 에 `[[presets]] name = "ok"` 가 들어가고,
   README Session 절이 `rcm run ok`, 스크립트는 `grep '^name = "ok"'` 로 템플릿을 확인한다. 결과: 새 설치마다
   `rcm presets`·웹 UI 에 `ok` 가 보인다(템플릿 주석이 「쓰고 지워라」고 안내). 테스트는 이 선택을 받아들이고
   `[[presets]]`·`::rcm::step::` 를 스크립트에서 잠그지 않았다. → workplan §1·§3.2 와 PLAN 결정 목록에 반영 권장.
2. **§3.2 `rcm serve … --data-dir <tmp>/data` → 구현은 `--data-dir` 없이 `HOME` 덮어쓰기.** 지금 템플릿 기본값
   (`~/.local/share/rcm`) 이면 안전하다(가정 10). 이중 안전장치로 `--data-dir "$WORK/data"` 를 넣어도 손해가 없다.
3. **§4 실패 문구** 「release tags must point at main」 ↔ 구현 「… at a commit on main」. 테스트 완화(가정 4).
4. **§6 README 「Releasing」** 이 `dev → main` 화살표를 쓰지 않고 「`main` only takes PRs from `dev`」 ·
   `gh pr create --base main --head dev` 로 쓴다. 뜻은 같아 테스트가 세 표현을 모두 받는다.
5. **README 마커 ↔ 스크립트 결합**: 마커 블록 안에 `rcm` 줄을 추가·변경하면(예: `rcm run gate`) 스크립트에 그
   문자열이 없는 한 CI `smoke` 가 0단계에서 빨개진다. 이제 pytest(3-7)가 먼저 잡는다. README 쪽엔 마커가 HTML
   주석이라 편집자에게 보이지 않는다 — 마커 옆에 「이 블록은 scripts/smoke_install.sh 가 그대로 실행한다」는
   한 줄 주석(마커 문자열 자체는 그대로)을 두면 좋다.
6. **§6 Status 문구**: 명세의 정확한 문장 대신 「`Status: **M0–M4 done (v0.1.0)** — …`」. 테스트는 `M4` 또는
   `v0.1.0` 만 본다.
7. **ci.yml `smoke` 잡 비용**: PR 마다 ubuntu·macos 에서 wheel 빌드 + venv + 서버 기동을 한다(약 1~2분, macOS
   러너 비용). `test` 집계가 `needs` 로 묶여 있어 smoke 가 flaky 하면 main 머지가 막힌다 — 스크립트가 서버 기동을
   한 번 재시도하는 이유. 지켜볼 것.
8. release.yml `pypi` 잡은 변수가 없으면 **skipped** 로 끝나고 워크플로는 성공이다(결정 27 그대로). `environment:
   pypi` 는 저장소 설정에 같은 이름의 environment 가 있어야 하며, 없으면 잡이 대기·실패한다 — README Releasing
   절이 이미 안내한다.
