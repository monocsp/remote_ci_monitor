# M4 테스트 시나리오 A — `rcm init` · `rcm version` · `rcm check` 행 · 패키징 (2026-09-06)

`docs/m4-workplan.md` §1 · §2 · §7 과 Codex 리뷰(`docs/reviews/2026-09-06-codex-m4-design.md`, 「반드시 고칠 것」
①④⑤⑥⑦⑩)를 pytest 로 옮긴 것이다. `src/` · `scripts/` · 워크플로 · README 는 건드리지 않았다. 쓰는 동안 구현이
같은 워크트리에 병렬로 들어와서(`cli.py` init/version/check · `config.py` XDG·`check_tools` · `templates/` ·
`LICENSE` · `CHANGELOG.md` · `pyproject.toml`), 인계 시점에는 **41건 전부 초록**이다(오프라인이면 wheel 3건 skip).
도중에 빨갰던 한 건 — `License :: OSI Approved :: MIT License` 분류자 — 은 명세 §1 이 아니라 Codex 리뷰 ① 쪽이 맞아
테스트를 리뷰 쪽으로 고쳤다(아래 「명세와 다른 것」 1).

| 파일 | 대상 | 테스트 함수 / 수집 건수 |
|---|---|---|
| `tests/test_cli_m4.py` | `rcm init server/client` · init→로더 왕복 · `rcm version [--json]` · `rcm check` 의 python·git 행 | 20 / 26 |
| `tests/test_packaging.py` | 템플릿 == 예시 · `pyproject` · `LICENSE` · `ok` 프리셋 · `CHANGELOG` · wheel 내용·METADATA | 13 / 15 |

공통 규칙: `main(argv)` 를 in-process 로 부른다(test_cli_m1 과 같다). `home` 픽스처가 `HOME` 을 tmp 로 옮기고
`XDG_CONFIG_HOME` · `RCM_SERVER` · `RCM_TOKEN` · `RCM_CONFIG` · `RCM_LABEL` 을 지우며 cwd 도 빈 tmp 로 바꾼다
(`./rcm.toml` 이 탐색에 끼지 않게). 서버는 `test_server.Server(workers=False)`. git 이 PATH 에 없으면 git-ok 행
테스트 1건만 skip 이고, git-없음 변형은 test_config 의 요령(빈 디렉터리만 PATH 에)으로 어디서나 돈다.

## 공용 도우미 (`tests/test_cli_m4.py`)

- `run(capsys, argv)` — test_cli_m1 의 `run` 과 같지만 **SystemExit 을 받아 코드로 바꾼다**. `main()` 은
  `parser.parse_args` 를 try 밖에서 부르므로 argparse 의 usage 오류(`init client` 에 `--server` 가 없을 때 등)는
  `SystemExit(2)` 로 밖으로 나온다. `_usage()` 경로의 `return 2` 와 argparse 의 `SystemExit(2)` 를 같은 2 로 본다.
- `row_status(out, name)` — `rcm check` 출력에서 `^(ok |FAIL)  <name>(\s|$)` 행의 상태. 형식은 cmd_check 의
  `{'ok ' if ok else 'FAIL'}  {name:<13} {detail}` 그대로라 `ok   python…` · `FAIL  git…` 로 찾는다. 이름 뒤에
  공백/줄끝을 요구해 `git` 이 다른 이름의 접두가 되지 않게 한다. 행이 없으면 `None`(「행을 만들지 않는다」 검사).
- `_VersionInfo(NamedTuple)` — `sys.version_info` 대역. 진짜는 structseq 라 `type(sys.version_info)(...)` 가
  `TypeError` 다. NamedTuple 이면 `>= (3, 11, 4)` 튜플 비교와 `.major` 속성 접근이 둘 다 된다.
- `server_toml(text)` — `rcm check --config` 에 줄 서버 설정을 tmp 에 쓴다. `REPO_SERVER_TOML`(`[[repos]]` +
  git_ref 프리셋 `deploy`) · `TREE_SERVER_TOML`(tree 프리셋만). URL 은 `git@example.com:org/app.git` — check 는
  git 을 부르지 않고 설정만 읽는다.
- `no_git` — 빈 디렉터리만 PATH 에 두고 `shutil.which("git") is None` 을 확인한다.

## 1. `tests/test_cli_m4.py` — `rcm init server`

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 기본 경로 | 종료 0 · `<HOME>/.config/rcm/server.toml` 이 생기고 바이트가 `examples/server.toml` 과 같다 · stdout 은 **그 경로 한 줄뿐**(`out == f"{path}\n"`) · 권한 0644 · stderr 에 `preset` · `rcm token add` · `rcm serve` |
| 2 | XDG(server·client 2건) | `XDG_CONFIG_HOME=<tmp>/xdg` → `<xdg>/rcm/<kind>.toml`. HOME 쪽 `.config` 은 생기지 않는다 |
| 3 | 덮어쓰기 거부 → `--force` | 손으로 고친 파일이 있으면 종료 2 · stderr 에 `refusing to overwrite` + 경로 + `--force` · stdout 비어 있음 · 파일 그대로. `--force` 면 0 · 템플릿 바이트 · 0644 |
| 4 | `--path` | 없는 부모 두 단계 아래에 쓴다 · 0644 · 기본 경로에는 안 생긴다 |
| 5 | 종류 없음 · 모르는 종류 | `rcm init` · `rcm init agent` → 2, stdout 비어 있음, 아무것도 안 만든다 |

## 2. `tests/test_cli_m4.py` — `rcm init client`

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 6 | 치환 · 보존 · 0600 | `--server http://build:8787/` → `server =` 줄이 **정확히 1개**, `server = "http://build:8787"`(끝 `/` 제거; 꼬리 주석은 있어도 없어도 됨) · **server 줄을 뺀 나머지는 `examples/client.toml` 과 줄 단위로 같다**(주석 · `token_env = "RCM_TOKEN"` · `label`) · 권한 0600 · stderr 에 `RCM_TOKEN` · `rcm check` · `load_client_config(path, environ={})` 가 `server == "http://build:8787"`, `token_env == "RCM_TOKEN"`, 토큰 없음 |
| 7 | URL 정규화(3건) | `http://build:8787/` → 슬래시 제거 · `http://127.0.0.1:8787` 그대로 · `https://…:8443/` 도 받고 슬래시 제거 |
| 8 | `--server` 없음 | 2 · stderr 에 `--server` · 파일 없음(argparse `required`) |
| 9 | 스킴 거부(3건) | `ftp://x` · `build:8787` · `//build:8787` → 2 · stderr 에 `http://` · 파일 없음 |
| 10 | 덮어쓰기 거부 → `--force` | 두 번째 `init client` 는 2 + `refusing to overwrite` + 파일 그대로. `--force` 면 새 URL 로 바뀌고 0600 유지 |
| 11 | `--path` | 지정 경로에 0600 으로, 로더가 읽는다, 기본 경로에는 안 생긴다 |

## 3. `tests/test_cli_m4.py` — init 이 만든 파일을 로더가 찾는다 (Codex 리뷰 ④)

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 12 | `home` · `xdg` 2건 | `init server` + `init client --server <in-process 서버>/` 뒤에 **명시 경로 · `RCM_CONFIG` · `RCM_SERVER` 없이** `load_server_config(None).path` 와 `load_client_config(None).path` 가 그 파일이고 `server` 가 정규화된 URL. `RCM_TOKEN` 만 주고 `rcm check` → 0, `server`·`token`·`data dir` 행 ok(템플릿의 `~/.local/share/rcm` 부모를 미리 만든다) |

리뷰 ④ 의 요지 — `init` 만 XDG 를 알고 로더가 `~/.config/rcm` 만 보면 「만들었는데 못 찾는」 5분 셋업 실패가 된다 —
를 한 테스트로 잠근다. 구현은 `config.user_config_dir()` + `_candidates()` 로 XDG 를 후보 맨 앞에 넣었다.

## 4. `tests/test_cli_m4.py` — `rcm version`

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 13 | 한 줄 | 0 · stderr 비어 있음 · stdout 한 줄 `rcm <__version__> (Python <platform.python_version()>, <sys.platform> <platform.machine()>)` — 정규식 `^rcm (\S+) \(Python (\S+), (\S+) (\S+)\)$` 로 네 조각을 각각 대조 |
| 14 | `--json` | 키가 정확히 `{version, python, platform, machine, schema_version}` · `version == __version__` · `schema_version == SCHEMA_VERSION` · 나머지도 위와 같은 값 · 한 줄 |
| 15 | `rcm --version` | 기존 argparse action 그대로 `rcm <version>` · 종료 0(회귀 방지) |

## 5. `tests/test_cli_m4.py` — `rcm check` 의 python · git 행

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 16 | python ok | 전제 `sys.version_info >= (3, 11, 4)` + `tarfile.data_filter`. **첫 줄**이 `ok   python…` 이고 실행 중 파이썬 버전 문자열을 담는다 · server·token ok · 서버 설정이 없으니 `git` 행 없음 · 종료 0 |
| 17 | python FAIL | `monkeypatch.context()` 안에서 `sys.version_info` 를 `_VersionInfo(3, 11, 3, …)` 로, `tarfile.data_filter` 를 지우고 `check` → 첫 줄 `FAIL  python…` 에 `3.11.4` · 종료 1 · **server 행은 계속 ok**(python 이 실패해도 나머지 검사를 멈추지 않는다) |
| 18 | git ok (`@needs_git`) | `--config` 에 `[[repos]]` + git_ref 프리셋 → `git` 행 ok · `data dir` ok · 종료 0 |
| 19 | git FAIL | 같은 설정 + `no_git` → `FAIL  git…` 에 `PATH` · 첫 줄은 여전히 python · server 행 ok · 종료 1 |
| 20 | repos 없음 | tree 프리셋만 + `no_git` → `git` 행 없음 · 종료 0(repos 가 없으면 git 이 없어도 상관없다) · `data dir` ok |

19 는 명세 그대로 쓰면 로더가 먼저 막는 케이스였다 — `load_server_config` 가 `[[repos]] configured but git is not
on PATH` 로 `ConfigError` 를 던져 `cmd_check` 가 `cfg.repos` 를 볼 수 없다(`FAIL  server config` 행이 대신 나온다).
Codex 리뷰 ⑦ 이 같은 지적을 했고 구현은 `load_server_config(check_tools=False)` 를 추가했다. 테스트는 그 키워드
이름을 쓰지 않고 행으로만 검사한다.

## 6. `tests/test_packaging.py`

| # | 시나리오 | 확인하는 것 |
|---|---|---|
| 1 | 템플릿 == 예시(2건) | `src/remote_ci_monitor/templates/{server,client}.toml` 의 바이트가 `examples/` 와 같다(다르면 「둘을 같이 고쳐라」) |
| 2 | 패키지 리소스(2건) | `importlib.resources.files("remote_ci_monitor") / "templates" / name` 이 파일이고 같은 바이트 |
| 3 | 동적 버전 | `project.dynamic == ["version"]` · 정적 `project.version` 없음 · `tool.hatch.version.path == "src/remote_ci_monitor/__init__.py"` · `__version__` 은 `X.Y.Z` |
| 4 | 라이선스(PEP 639) | `project.license == "MIT"` · `license-files` 에 `LICENSE` · 파일에 `MIT License` · **`License ::` 분류자 없음** · `build-system.requires` 의 `hatchling>=` 이 1.27 이상 |
| 5 | 의존성 | `project.dependencies == []` · dev extra 에 `build` · `pytest` · `ruff` |
| 6 | sdist include | `src/remote_ci_monitor` · `README.md` · `pyproject.toml` 유지 + `examples`(`/` 유무 무관) · `LICENSE` · `CHANGELOG.md` · `tests` 없음 |
| 7 | URL · 분류자 · 키워드 | urls 에 Homepage/Repository/Issues/Changelog(https) · 3.11/3.12/3.13 · `Development Status :: 4 - Beta` · `Intended Audience :: Developers` · `requires-python == ">=3.11"` · keywords 비어 있지 않음 |
| 8 | 스크립트 | `rcm` · `remote-ci-monitor` → `remote_ci_monitor.cli:main` 그대로 |
| 9 | `ok` 프리셋(리뷰 ⑩) | `examples/server.toml` 에 `name = "ok"` 프리셋 · argv 가 `scripts/…` · `*.sh` 를 가리키지 않음 · `source_modes` 는 tree |
| 10 | CHANGELOG | 파일 존재 · 첫 `## [` 절이 `Unreleased` 또는 `__version__` · `## [<__version__>] - YYYY-MM-DD` 절 존재 |
| 11 | wheel 이름 | `remote_ci_monitor-<__version__>-py3-none-any.whl` |
| 12 | wheel 내용 | `remote_ci_monitor/templates/{server,client}.toml` 이 있고 `examples/` 와 같은 바이트 · `web/index.html` · `tests/` 항목 0 |
| 13 | METADATA | `.dist-info` 이름에 버전 · `Version:` · `License-Expression: MIT` · `License-File: LICENSE` · `Classifier: License ::` 없음 · `Requires-Python: >=3.11` · extra 가 없는 `Requires-Dist` 0(런타임 의존성 0) |

wheel 은 `test_web.test_wheel_ships_web_assets` 와 같은 명령(`pip wheel . -w dist --no-deps -q`)과 같은
`NO_NETWORK_MARKERS` skip 규칙이다. 다만 module 스코프 픽스처로 **한 번만** 빌드해 11~13 이 나눠 쓴다(약 10초).

## 가정

1. **템플릿의 정본은 `examples/`** 다. `SERVER_TEMPLATE`·`CLIENT_TEMPLATE` 은 테스트 실행 시점에 `examples/` 를
   읽는다. 예시가 바뀌면 init 테스트의 기대값도 따라 바뀌고, 패키지 안 사본이 안 따라오면 test_packaging 1 이 잡는다.
2. `rcm init` 의 stdout 은 **절대 경로 한 줄**이다. pytest 의 `tmp_path` 는 이미 realpath 라 `resolve()` 여부와 무관하게
   같다. `~` 로 줄여 찍으면 빨갛다(의도 — 다음 명령에 붙여 넣을 경로다).
3. 권한은 umask 와 무관하게 **server 0644 · client 0600** 이어야 한다(`stat.S_IMODE` 로 정확히 비교). umask 077 환경에서
   `open()` 기본값에 기대는 구현은 server 가 0600 이 되어 빨갛다.
4. client 치환은 `server =` 줄 **하나만** 바꾸고(리뷰 ⑥: 정확히 1개), 나머지 줄은 템플릿과 **순서까지 같다**. 치환한
   줄의 꼬리 주석(`# Tailscale / LAN address…`)은 남겨도 지워도 된다. 생성 헤더 주석을 덧붙이는 구현은 빨갛다 —
   그때는 6 의 「나머지 줄 동일」 비교를 「템플릿 줄이 모두 포함」 으로 느슨하게 바꾸면 된다.
5. `--server` 는 `http://` · `https://` 접두만 본다. 대문자 스킴(`HTTP://`) · 끝 `/` 여러 개는 명세가 정하지 않아 테스트에
   넣지 않았다.
6. `rcm version` 의 python 은 `platform.python_version()`, platform 은 `sys.platform`, machine 은 `platform.machine()`
   과 **같은 문자열**이어야 한다(명세 예 `3.13.2` · `darwin` · `arm64`). `sys.version` 첫 토큰을 쓰면 rc 빌드에서 다르다.
7. `rcm check` 의 python 행 detail 에 실행 중 파이썬 버전(`platform.python_version()`)이 들어간다 — 명세는 ok 일 때의
   문구를 정하지 않았지만 「어느 파이썬으로 검사했는지」 없는 check 행은 쓸모가 없다. FAIL 문구는 명세대로 `3.11.4` 포함.
8. python FAIL 변형(17)은 `sys.version_info` 와 `tarfile.data_filter` **둘 다** 바꾼다. 명세는 둘 다 검사하라고 하므로
   어느 한쪽만 보는 구현도 FAIL 을 낸다. `monkeypatch.context()` 로 `main()` 호출 동안만 바꾼다.
9. git FAIL 행의 detail 에 `PATH` 가 들어간다(`config.py` 의 기존 문구 `git is not on PATH` 와 맞춘다). ok 일 때의 detail
   (예: git 경로)은 단정하지 않는다.
10. `rcm check` 의 git 행 위치는 단정하지 않는다(python 만 「항상 첫 행」). 기존 행 이름(`server` · `token` · `presets` ·
    `timezone` · `data dir` · `client config` · `server config`)과 `ok /FAIL` 접두 형식은 그대로라고 본다.
11. wheel 은 pip 의 격리 빌드가 `hatchling>=1.27` 최신판을 받으므로 METADATA 는 항상 `License-Expression`(2.4+) 이다.
    오프라인이면 `pip wheel` 실패 문구로 skip 한다.

## 명세와 다른 것 · 눈에 띈 것

1. **§1 의 `License :: OSI Approved :: MIT License` 분류자는 Codex 리뷰 ①과 충돌한다.** 리뷰는 PEP 639(`license = "MIT"`
   → `License-Expression`)에서 license 분류자는 대체 대상이니 빼고, `hatchling>=1.27` + `license-files = ["LICENSE"]`
   로 하라고 했고 §8 이 「반영」이라고 적었지만 §1 본문은 안 고쳐졌다. 구현은 리뷰를 따랐다. 실측: hatchling 이 둘을
   같이 줘도 빌드는 되고(`Metadata-Version: 2.5`, `License-Expression` + `Classifier: License ::` 공존), packaging 26.2 의
   `Metadata.from_email(validate=True)` 도 통과한다 — 즉 어느 쪽이든 `twine check` 는 막지 않는다. 그래도 PEP 639 가
   「분류자는 쓰지 말라」이므로 테스트는 **리뷰 쪽(분류자 없음)** 을 잠갔다. **§1 문구를 고쳐야 한다.**
2. **§2.3 git 행은 명세 그대로는 만들 수 없었다.** `load_server_config` 가 `[[repos]]` + git 없음에서 `ConfigError` 를
   던지므로 `cmd_check` 가 repos 유무를 볼 수 없다(리뷰 ⑦ 과 같은 지적). 구현이 `check_tools=False` 를 추가해 해결.
   §2.3 에 「check 는 도구 검사를 끈 로딩을 쓴다」 한 줄이 있어야 한다.
3. **§2.1 의 XDG 는 `init` 만이 아니라 로더도 알아야 한다**(리뷰 ④). 구현은 `user_config_dir()` 로 두 곳을 같이 바꿨고
   §3 왕복 테스트가 잠근다. `config.py` 의 docstring/PLAN 의 탐색 순서 설명(`./rcm.toml` → `~/.config/rcm/server.toml`)
   에 `$XDG_CONFIG_HOME/rcm/server.toml` 이 **맨 앞**에 들어가야 한다.
4. **argparse 오류의 종료 코드 경로가 둘이다.** `main()` 은 `parse_args` 를 try 밖에서 부르므로 `rcm init client`
   (`--server` 누락)는 `return 2` 가 아니라 `SystemExit(2)` 다. 콘솔 스크립트로는 같은 2 지만 in-process 호출자(테스트 ·
   향후 `rcm` 을 import 해 쓰는 코드)는 다르게 본다. 테스트 `run` 이 흡수했지만, `parse_args` 를 try 안으로 넣거나
   `parser.exit` 를 덮는 편이 일관된다(선택).
5. §2.2 의 `rcm version` 한 줄 예 `rcm 0.1.0 (Python 3.13.2, darwin arm64)` — `machine` 을 `platform.machine()` 으로
   쓰면 Linux x86 은 `x86_64`, Docker(ARM) 는 `aarch64` 다. 문서 예시에 그 점을 한 줄 적으면 「arm64 가 아니네」 질문을
   줄인다.
6. 명세 §7 의 A 담당 표에는 없지만 리뷰 ⑩(템플릿 `ok` 프리셋)은 템플릿 = 패키징 소관이라 `test_packaging` 9 로 잠갔다.
   B 의 smoke/README 테스트와 겹치지 않는다(여기서는 TOML 만 본다).
7. sdist 의 실제 내용(`examples/` · `LICENSE` · `CHANGELOG.md` 가 tar 에 들어가는지)은 검사하지 않는다 — `pip wheel` 은
   sdist 를 만들지 않고 `python -m build` 는 `build` 패키지가 필요하다. `release.yml` 의 `twine check dist/*` 와
   격리 검증 단계(④)의 실제 `python -m build` 가 그 몫이다.
