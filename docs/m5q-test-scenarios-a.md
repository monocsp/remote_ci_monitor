# M5q 테스트 시나리오 A (PR A · 클라이언트가 서버 버전을 따라간다, 2026-11-19)

`docs/m5q-workplan.md` 의 §2.1~§2.9(흐름 · 설정 · 안 따라가는 명령 · 버전 확인 · 받기와 풀기와 재실행 · 실패 · 목표 버전 ·
청소 · 표시)와 §5.1 · §5.2 의 PR A 인터페이스를 시험으로 옮긴다. §6 의 F 표(F1~F47)와 R 표 가운데 PR A 의 것(①~⑦,
R9~R13, F49~F52)은 A1~A93 에 모두 들어 있고, 코드를 읽으며 찾은 경우는 A94 부터 이어 붙였다. 결정 번호는 149~174,
검토 번호는 §13 의 것이다. 시험을 먼저 쓰므로 구현 전에는 빨간 것이 정상이다.

이 문서에 없는 것도 적어 둔다. 서버 쪽 S1~S4(wheel 의 skills, 제출 경로의 로그 한 줄), 원격 워커 W1~W18 과 R ⑧,
문서 잠금(`docs/operating.md` 「Keeping clients on the server's version」 재작성)은 다른 시나리오 문서가 맡는다.

| 파일 | 무엇을 잠그나 |
|---|---|
| `tests/conftest.py`(새) | 모든 시험의 `XDG_CACHE_HOME` · `XDG_CONFIG_HOME` 을 `tmp_path` 아래로 돌리고 `RCM_CLIENT_VERSION` · `RCM_FOLLOWED*` 를 환경에서 지운다. 개발자 셸에 `export RCM_CLIENT_VERSION=installed` 가 있어도 시험 결과가 같아야 한다(A101) |
| `tests/follow_helpers.py`(새) | 아래 「공통 픽스처」의 도우미 전부 |
| `tests/test_follow_rules.py` | `follow.py` 의 순수 함수: `prescan` · `follows` · `resolve_setting` · `read_pin_file` · `install_kind` · `parse_health` · `plan` · `python_ok` · `cache_root` · `VERSION_RE` · `SHA256_RE` · 상수 |
| `tests/test_follow_cache.py` | `server_info` · `load_record` · `save_record` · `fetch`(받기 · sha · 풀기 · 이름 허용 목록 · rename) · `scan_cache` · `prune` · 캐시 권한 |
| `tests/test_follow_cli.py` | `cli._main(None, follow_enabled=True, execve=가짜)` 로 도는 진입 전부, `--no-follow` 파서, `rcm version` 둘째 줄과 `--json` 의 `follow`, `rcm check` 의 `client` 꼬리 |
| `tests/test_follow_exec.py` | 실제 `execve`. 비 editable 부트로더 venv 를 만들어 하위 프로세스로 `rcm` 을 띄운다. BOOT 를 직접 띄우는 시험도 여기 있다 |
| 기존 잠금(고친다) | 아래 「갱신할 기존 잠금」 표 |
| 기존 시험(고치지 않는다) | `test_client_wheel.py` 의 `rcm check` `client` 행 시험들, `test_cli_m4.py` 의 `rcm version` 한 줄 시험, `python -m remote_ci_monitor.cli` 로 띄우는 e2e 시험들(A94 · A95) |
| `scripts/mutcheck.py` | PR A 변이 일곱. 「sha256 비교 삭제」는 A41 이, 「editable 판정 뒤집기」는 A10 이, 「`_follow_boot` 고리 방지 삭제」는 A62 가, 「임시 폴더 없이 바로 풀기」는 A43 · A61 이, 「`serve` 를 따라가는 명령에 넣기」는 A68 이, 「health `path` 검사 삭제」는 A51 · A54 가 잡는다 |

### 갱신할 기존 잠금

| 파일:줄 | 지금 | 바꿀 것 |
|---|---|---|
| `tests/test_cli_m4.py:375` | `set(doc) == {"version", "python", "platform", "machine", "schema_version"}` | `"follow"` 를 더한다. 나머지 키의 값은 그대로 잠근다 |
| `tests/test_cli_m4.py:357` | `rcm version` 이 한 줄이다 | 그대로 둔다. 개발 venv 는 editable 이라 질문 3 의 답에 따라 이 시험이 빨개질 수 있다 |
| `tests/test_packaging.py` | Windows 분류자 잠금이 없다 | pyproject classifiers 에 `Operating System :: Microsoft :: Windows` 가 없음을 잠근다(A69, F38) |

## 공통 픽스처

- 버전은 숫자로 박지 않는다. `INSTALLED = remote_ci_monitor.__version__`, `NEW = "99.0.1"`, `NEW2 = "99.0.2"`, `OLD = "0.0.9"` 를 쓴다.
  명세의 0.8.0 은 `INSTALLED`, 0.8.1 은 `NEW`, 0.8.2 는 `NEW2`, 0.6.1 은 `OLD` 로 읽는다. 고정 「설치본이 X 다」는 `pin = INSTALLED` 다.
  sha 는 `SHA_A = "a" * 64`, `SHA_B = "b" * 64` 이고 실제 wheel 의 sha 는 `hashlib.sha256(bytes)` 로 구한다.
- 시각은 주입한다. 순수 함수와 `server_info` · `maybe_follow` 에는 `now=` 를 넘기고 `T = 1_763_532_000.0`, `M = 60` 을 쓴다.
  sleep 은 쓰지 않는다. `_main` 을 거치는 시험은 `monkeypatch.setattr(follow.time, "time", clock)` 로 민다.
- `fake_wheel(version, *, init_version=None, requires_python=">=3.11", extra=(), drop=(), cli=None)` 는 명세 §6 의 도우미에 셋을
  더한 것이다. `clientwheel._package_files()` 와 `_dist_metadata()` 로 지금 패키지를 읽어 `__init__.py` 의 `__version__` 을
  `init_version or version` 으로, METADATA 의 `Version:` 을 `version` 으로, `Requires-Python:` 줄을 `requires_python` 으로 바꾼다
  (`None` 이면 줄을 지운다). `extra` 는 `(이름, 바이트, external_attr)` 묶음을 그대로 넣는다(악성 항목용). `drop` 은 뺄 패키지
  파일(`"follow.py"` 를 빼면 「M5q 이전 판」 흉내가 된다), `cli` 는 `cli.py` 를 바꿔 끼울 소스다. RECORD 는 다시 계산한다.
- `PROBE_CLI` 는 `cli=` 에 넣는 짧은 `cli.py` 다. `main()` 이 stdout 에 JSON 한 줄을 쓴다: `pid`, `version`(`remote_ci_monitor.__version__`),
  `file`(`remote_ci_monitor.__file__`), `argv`, `path0`(`sys.path[0]`), `boot`(`getattr(remote_ci_monitor, "_follow_boot", None)`),
  `env`(이름이 `RCM_FOLLOWED` 로 시작하는 환경 변수와 `RCM_SERVER`). `--exit N` 이면 N 으로 끝나고, `--sleep S` 면 S 초 자고,
  `--spawn` 이면 `env` 를 하위 프로세스로 띄워 그 출력의 `RCM_FOLLOWED*` 줄도 담는다. `KeyboardInterrupt` 는 130 이다. 명령 이름은
  설치본이 모르는 `probe` 다. 그래서 이 도우미를 쓰는 시험은 모두 R ① 「모르는 명령도 따라간다」에 기대고 있다.
- `FakeServer` 는 `tests/test_docs_client_wheel.py:130` 의 `_FakeWheelServer` 를 키운 것이다. `ThreadingHTTPServer(("127.0.0.1", 0))` 위에서
  `/api/health` 와 `/client/<이름>` 을 주고, 받은 요청을 `requests`(경로 · 헤더)에 남긴다. `health_count` · `client_count` 를 센다.
  바꿀 수 있는 것: health 상태와 본문(`health=`, dict 이거나 bytes), `/client/` 의 바이트 · 상태 · 앞에 붙는 지연, 3xx 와
  `Location`, `client_barrier`(`threading.Barrier(n)`, n 개의 `/client/` 요청이 모일 때까지 응답을 잡는다), `require_token`
  (토큰이 없으면 `/client/` 가 401), `extra_bytes`(선언한 크기보다 더 보낸다).
- `VersionedServer(tmp_path, version, wheel, **server_overrides)` 는 `tests/test_server.Server` 와 같되 `App(cfg, store, version=version)`
  으로 띄우고 `app._wheel = (wheel, sha256(wheel))`, `app._wheel_done = True` 를 먼저 넣는다. 그러면 실제 라우터가 그 wheel 을
  준다. 다른 파일 이름은 진짜 404, `read_auth = "basic"` 은 진짜 401 이다. 요청 수는 `httpd.RequestHandlerClass` 를 감싼
  하위 클래스가 `parse_request` 에서 경로를 남겨 센다. 잡을 실제로 돌려야 하는 시험(A74)은 `workers=True` 로 띄운다.
- 시험 안에서 도는 진입은 `follow_env` 픽스처가 차린다. `sys.argv = ["rcm", *argv]`, `follow.install_kind` 를 `lambda: "wheel"` 로,
  `RCM_SERVER` · `RCM_TOKEN` 을 그 서버로 둔다. 가짜 `execve` 는 `(path, args, env)` 를 `calls` 에 남기고 `Exec` 예외를 던져
  돌아오지 않는 척한다. 시험은 `cli._main(None, follow_enabled=True, execve=fake)` 를 부르고 `Exec` 을 잡는다.
- 실제 exec 시험은 `bootloader` 픽스처(세션 범위)가 비 editable 부트로더 venv 를 만든다. `python -m venv --without-pip` 뒤에
  `clientwheel.build_wheel(INSTALLED)` 를 `lib/pythonX.Y/site-packages` 에 풀고 `bin/rcm`(`#!<venv>/bin/python` · `from
  remote_ci_monitor.cli import main` · `sys.exit(main())`)을 쓴다. pip 와 네트워크가 없어도 된다. `direct_url.json` 이 없으니
  `install_kind()` 는 `wheel` 이다. 하위 프로세스의 환경은 처음부터 만든다: `HOME=tmp/home`, `XDG_CACHE_HOME=tmp/cache`,
  `XDG_CONFIG_HOME=tmp/config`, `PATH=<venv>/bin:/usr/bin:/bin`, `RCM_SERVER`, `RCM_TOKEN`, `LANG=C.UTF-8`. `PYTHONPATH` 와
  다른 `RCM_*` 는 넣지 않는다. 실행은 `subprocess.Popen` 이고 `proc.pid` 를 PROBE 가 찍은 `pid` 와 비교한다.
- BOOT 를 직접 띄우는 시험은 `[<venv>/bin/python, "-P", "-c", PREAMBLE + follow.BOOT, *argv]` 를 실행한다. `PREAMBLE` 은 주입할
  때만 쓴다(A76 의 `fcntl.flock` 바꿔치기). `follow.BOOT` 가 공개 상수여야 한다(질문 4).
- 응답하지 않는 서버는 `socket.socket()` 을 `bind` · `listen` 만 하고 `accept` 하지 않는다. 연결은 백로그로 성립하고 읽기가 막힌다.
- 캐시 권한의 남 소유는 `monkeypatch.setattr(follow.os, "geteuid", lambda: real + 1)` 이나 명세대로 `os.stat` 주입으로 만든다.
  상한은 `monkeypatch.setattr(follow, "MAX_UNPACKED_BYTES", 10_000)` 처럼 작게 주입해 경계를 본다.

## 베스트

### 명세에서

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A1 | 같은 버전 (F1 · §2.4 · 결정 153) | `follow_env` · FakeServer `INSTALLED` · `rcm jobs` 두 번, 둘째는 `now = T+9M` | exec 없음, `/client/` 0번, health 첫 실행 1번 · 둘째 0번. `follow.json` 에 그 서버 항목 하나 |
| A2 | 처음 받아 따라간다 (F2 · §2.5 · 결정 151 · 166) | 서버 `NEW` · 캐시 빔 · `rcm jobs --json` | `/client/` 1번, `clients/NEW-<sha8>/` 와 표지가 생긴다. exec 인자 `[sys.executable, "-P", "-c", BOOT, "jobs", "--json"]`, 환경 `RCM_FOLLOWED=NEW` · `RCM_FOLLOWED_PATH=<그 폴더>` · `RCM_FOLLOWED_FROM=INSTALLED`. stderr 는 `rcm: following server vNEW (installed vINSTALLED) — fetched N KB` 한 줄 |
| A3 | 캐시에서 바로 (F3) | A2 다음 실행, `now = T+1M` | 요청 0(health · `/client/` 모두), 같은 폴더로 exec, stderr 빔 |
| A4 | 내려간다 (F4 · 결정 150) | 서버 `OLD` | `OLD-<sha8>` 로 exec. 버전 크기를 비교하지 않는다 |
| A5 | 실제 exec (F5 · 결정 151) | `bootloader` · VersionedServer `NEW` + `fake_wheel(NEW)` · 하위 프로세스 `rcm presets` 와 `rcm check` | `presets` 는 서버의 프리셋 목록을 찍고 종료 0. `check` 의 `client` 행이 `vNEW · same as server · following (installed vINSTALLED)` 다(자식이 NEW 코드라는 증거). PROBE 판(`rcm probe`)은 `pid == proc.pid`, `version == NEW`, `file` 이 캐시 폴더 아래 |
| A6 | 고정 네 갈래 (F6 · §2.7 · 결정 158) | `RCM_CLIENT_VERSION` 을 (가) `INSTALLED` (나) `OLD` 이고 캐시에 `OLD-*` 있음 (다) `NEW` 이고 서버가 `NEW` (라) `NEW2` 이고 서버 `NEW` · 캐시 빔 | (가) exec 없음 · health 0 (나) 캐시로 exec · health 0 (다) 받아서 exec (라) exec 없음 · 경고 `pinned_unavailable` 한 줄 |
| A7 | `--no-follow` 두 자리 (F7 · §2.2 · 결정 154) | 서버 `NEW` · `rcm --no-follow jobs` · `rcm jobs --no-follow` | 둘 다 health 0 · exec 없음. 설치본 파서가 둘 다 받아 명령이 끝까지 돈다(종료 0) |
| A8 | 환경 변수 `installed` (F8) | `RCM_CLIENT_VERSION=installed` · 서버 `NEW` | health 0, exec 없음 |
| A9 | 환경 변수가 파일을 이긴다 (F9 · 결정 154) | 파일 `installed` + 환경 `follow`, 그리고 파일 `follow` + 환경 `installed` | 앞은 따라간다, 뒤는 health 0 |
| A10 | editable 기본은 끔 (F10 · 결정 156) | `install_kind` 를 `editable` 로 · 설정 없음 · 서버 `NEW` | health 0, exec 없음, stderr 빔. `rcm version` 둘째 줄은 질문 3 의 답 |
| A11 | editable 도 명시하면 (F11) | editable + `RCM_CLIENT_VERSION=follow` | 따라간다 |
| A12 | pull 한 editable 은 source (F12 · §2.2) | 가짜 `importlib.metadata.distribution` 의 버전이 `__version__` 과 다르다 | `install_kind() == "source"`, `resolve_setting` 기본이 `installed` |
| A13 | `rcm version` 한 줄 (F44 · §2.9 · 결정 166) | 기록 없음 · 설정 기본(wheel) | 한 줄, stderr 빔. `test_cli_m4.VERSION_RE` 가 그대로 맞는다. `rcm --version` 도 한 줄 |
| A14 | `rcm version` 둘째 줄 (F45 · §2.9) | 기본 서버의 기록 `NEW`, `checked_at = T-3M`, 캐시 `NEW` · `INSTALLED` | 둘째 줄 `follow: on (default) · server <주소> vNEW checked 3m ago · cached …`. `--json` 의 `follow` 키 집합이 `{mode, pin, source, install, server, server_version, checked_at, cached}`, `checked_at` 은 `Z` 로 끝나는 ISO. 서버 요청 0(FakeServer 카운터) |
| A15 | `rcm check` 의 꼬리 넷 (F46 · §2.9) | (가) 실제 exec 자식(A5) (나) sha 불일치로 실패 (다) `RCM_CLIENT_VERSION=INSTALLED` · 서버 `NEW` (라) editable · 같은 버전 | (가) `… · same as server · following (installed vINSTALLED)` (나) `… · older · not following: sha256 mismatch — pip install <url>` (다) `… · older · pinned by RCM_CLIENT_VERSION` (라) `… · same as server · not following (editable install)`. FAIL 은 `min_client_version` 아래일 때뿐 |
| A16 | `check` 는 기록을 안 쓴다 (F47 · §2.3) | 10분 안 기록 `INSTALLED` · 서버는 이제 `NEW` · `rcm check` | health 를 새로 읽어 `NEW` 로 따라간다. 기록의 `checked_at` 이 새 시각 |
| A17 | 훑기의 기본 (§2.1 · 결정 160) | `prescan(["jobs"])` · `(["--no-follow","run","gate","-f","a=1"])` · `(["jobs","--server","http://h:1","--token","T","--client-config","c.toml"])` · 같은 것을 `=` 꼴로 · `(["gc","--config","s.toml","--dry-run"])` · `(["run","-h"])` | 명령 `jobs` · `run`(no_follow True) · 세 값을 정확히 · 같음 · `has_gc_config True` · `wants_help True`. 예외는 없다 |
| A18 | 따라가는 명령 표 (§2.3 · 결정 160) | `follows(prescan(argv), server_configured=…)` 를 표의 줄마다 | `serve` `worker` `token` `init` `discover` `version` False. `gc --config` False, `gc` True. `skills` 는 `server_configured` 값 그대로. `check` · `run` · `wait` · `jobs` · `newcmd` True. `-h` · `--help` · `--version` 이 있으면 False |
| A19 | 설정 표 (§2.2 · 결정 154 · 156) | `resolve_setting` 에 (flag) · (env `follow` / `installed` / `OLD`) · (file 같은 셋) · (default × wheel · editable · source) | `Setting(installed, None, flag)` · `(follow, None, env)` · `(installed, None, env)` · `(pin, OLD, env)` · 파일은 `source="file"` · 기본은 `follow` · `installed` · `installed` |
| A20 | 한 줄 파일 읽기 (§2.2 · 결정 155) | `XDG_CONFIG_HOME=<tmp>/x` 와 없을 때, 내용 `"\n  # pinned\n\n  installed  \n"` | 경로가 `<tmp>/x/rcm/client-version` · `$HOME/.config/rcm/client-version`. 값 `"installed"`. 파일이 없으면 값 None |
| A21 | 설치 종류 셋 (§2.2) | 가짜 distribution: (가) `direct_url.json` 이 `{"dir_info": {"editable": true}}` (나) `direct_url.json` 없음, 버전 같음 (다) `PackageNotFoundError` (라) 버전 다름 | `editable` · `wheel` · `source` · `source` |
| A22 | health 해석 (§2.4 · 결정 153) | VersionedServer 의 실제 health 본문 · `client_wheel: null` + `client_wheel_error: "metadata_missing"` · 키 없음 | `ServerInfo` 필드가 본문과 같다. null 은 `wheel_path None` · `wheel_error "metadata_missing"`. 키 없음은 `wheel_error "old_server"`. 서버 주소 `HTTP://MacMini.Local:8787/` 은 `http://macmini.local:8787` 로, `127.0.0.1:8790` 은 `http://127.0.0.1:8790` 으로(`Client` 와 같은 규칙) |
| A23 | 목표 정하기 표 (§2.7 · 결정 150 · 158) | `plan` 에 설정 · `info` · `cached` 를 바꿔 가며. 시계 · 파일 · 네트워크는 없다 | installed 는 `stay`. follow: 같은 버전 `stay`, 버전 · sha 맞는 캐시 `exec_cached`, 같은 버전 다른 sha 캐시만 있으면 `fetch`, `info None` 은 `stay + unreachable`, wheel 없음 + 같은 버전 캐시 `exec_cached`(가장 최근), 없으면 `stay + no_wheel`. pin 은 A6 의 네 갈래 그대로 |
| A24 | Python 요구 (§2.5 · 결정 159) | `python_ok(None, (3,11,0))` · `(">=3.11", (3,11,0))` · `(">=3.11.4", (3,11,4))` · `(">=3.11", (3,13,0))` | 모두 True |
| A25 | 캐시 뿌리 (§2.5 · 결정 152 · 164) | `XDG_CACHE_HOME=<tmp>/c` · 없음 · `<tmp>/c` 가 다른 폴더로 가는 링크 | `<tmp>/c/rcm` · `$HOME/.cache/rcm` · 링크를 푼 실제 경로 |
| A26 | 받은 폴더의 모양 (§2.5 · 결정 151 · 164) | A2 뒤 | `clients/NEW-<sha8>/` 안에 `remote_ci_monitor/` · `remote_ci_monitor-NEW.dist-info/` · `.rcm-client.json`(`{version, sha256, server, fetched_at}`). 뿌리와 `clients/` 는 0700. `clients/` 에 `.tmp-*` 도 wheel 파일도 없다 |
| A27 | 기록의 모양 (§2.4) | A1 뒤 `follow.json` | 키는 정규화한 주소, 값은 `version` · `checked_at` · `failed_at: null` · `wheel{path, sha256, bytes}` · `wheel_error`. 뿌리에 임시 파일이 남지 않는다(`os.replace`) |
| A28 | exec 의 모양 (§2.5 · 결정 151 · 163) | 가짜 `execve`. 부른 순간에 stdout 버퍼와 표지 mtime 을 기록한다. 직전에 `print("x", end="")` | 인자는 A2 그대로, 환경은 `os.environ` 전부 + 셋. 부른 순간 stdout 이 비워져 있고(`x` 가 이미 나갔다) 표지 mtime 이 `now` |
| A29 | BOOT 의 정상 길 (§2.5 · 결정 161) | 실제 exec, `rcm probe`. 작업 폴더에 가짜 `remote_ci_monitor/__init__.py`(`raise SystemExit(99)`)를 심는다 | 종료 0. `file` 은 캐시 폴더 아래, `path0` 은 캐시 폴더, `boot` 는 `RCM_FOLLOWED*` 셋과 `loaded: True`, `env` 에 `RCM_FOLLOWED*` 가 없다. 심은 패키지는 import 되지 않는다(`-P`) |
| A30 | 청소의 기본 (§2.8 · 결정 163) | 표지 mtime 이 서로 다른 항목 다섯 + 새로 받은 하나, 잠금 없음. `shutil.rmtree` 를 감싸 지우는 경로를 기록 | 최근 셋과 새 것이 남는다(새 것을 셋에 세는지는 질문 16). 지운 것은 모두 `.trash-` 로 시작하는 이름으로 지워졌다 |
| A31 | 처음 받을 때의 한 줄 (§2.5 · 결정 166) | wheel 크기 707,673 바이트로 맞춘 가짜 | `fetched 707 KB`. 1000 으로 나눈 몫이다(반올림이면 708) |
| A32 | `--no-follow` 파서 (§2.2 · §5.2) | `build_parser().parse_args(["--no-follow","jobs"])` · `(["jobs","--no-follow"])` · `(["jobs"])` | `no_follow` 가 True · True · False. 하위 파서의 `SUPPRESS` 가 최상위 값을 덮지 않는다 |
| A33 | 고정의 「가장 최근」 (§2.7 순서 2) | pin `OLD`, 캐시 `OLD-aaaaaaaa`(mtime T) · `OLD-bbbbbbbb`(mtime T+M) | `bbbbbbbb` 로 exec, health 0 |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A94 | 인자 목록 호출의 `check` 는 오늘 글자 그대로 (§5.2 `_client_row(state=None)`) | `test_client_wheel.py:317~372` 의 시험들 | 고치지 않고 통과한다. `main(argv)` 는 따라가지 않으므로 `state` 가 None 이다 |
| A95 | e2e 시험의 `python -m remote_ci_monitor.cli` (§2.1 · 결정 156) | `test_e2e_loopback.py` · `test_e2e_worker.py` 그대로. 개발과 CI 의 venv 는 editable(`ci.yml:37`) | 고치지 않고 통과한다. 그 `HOME` 과 `XDG_CACHE_HOME` 아래에 `rcm/` 이 생기지 않는다(health 를 부르기 전에 끝난다) |
| A96 | smoke 의 wheel 설치 (§2.2 · 결정 149) | `scripts/smoke_install.sh` 그대로. 설치본이 wheel 이라 기본 `follow` 이고 서버도 같은 wheel | 통과한다. 따라가지 않고 stderr 에 따라가기 줄이 없다. `follow.json` 은 격리된 `$HOME/.cache/rcm` 에 생긴다. 이 스크립트는 `XDG_CACHE_HOME` 을 지우지 않으니 `unset` 한 줄을 더하는 것이 안전하다(제안) |
| A97 | `rcm logs N --follow` 는 `--no-follow` 가 아니다 | `prescan(["logs","412","--follow"])`, 서버 `NEW` | `no_follow False`, 따라간다 |
| A98 | `PYTHONPATH` 가 다른 체크아웃을 가리킨다 | 메타데이터 버전 `INSTALLED`, import 한 `__version__` 은 다른 값 | `install_kind() == "source"`, 따라가지 않는다 |
| A99 | 남은 따라가기 변수 | BOOT 를 거치지 않은 보통 실행의 환경에 `RCM_FOLLOWED=9.9.9` · `RCM_FOLLOWED_PATH=/nope` | 무시하고 평소대로 판단한다. exec 하면 세 값이 올바른 값으로 덮인다 |
| A100 | 따라가기와 명령이 같은 서버 · 토큰을 쓴다 (§2.1 4) | `client.toml` 에 서버 X · `token_env = "MY_TOKEN"`, `RCM_SERVER=Y`, `--server Z` | health 와 `/client/` 는 Z 로 가고 Bearer 는 `MY_TOKEN` 의 값이다. `load_client_config` 의 우선순위와 같다 |
| A101 | 시험 격리 (§6 conftest) | 셸에 `RCM_CLIENT_VERSION=installed` 를 두고 전체 시험 | 결과가 같다. `test_cli_m4` 의 `home` 픽스처처럼 `XDG_CONFIG_HOME` 을 지우는 기존 픽스처도 그대로 돈다 |

## 에러

### 명세에서

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A34 | 틀린 값 (F13 · §2.2 · 결정 154) | `RCM_CLIENT_VERSION=lates` · `=../x` · 65자 버전 · 한 줄 파일에 `lates` | 넷 다 종료 2, stderr 에 `ConfigError` 문장. 파일이면 그 경로가 들어간다. 서버 요청 0 |
| A35 | 서버 꺼짐 · 기록 없음 (F14 · §2.6 · 결정 157) | 닫힌 포트 · `rcm jobs` | stderr 첫 줄이 `rcm: not following server …: cannot reach the server — running installed vINSTALLED`, 그 뒤에 명령 자신의 `cannot reach` 줄. 종료 코드는 명령의 3 |
| A36 | 꺼짐 + 오래된 기록 (F15 · §2.4) | 기록 `NEW` · sha `SHA_A` · `checked_at = T-11M`. 캐시 `NEW-aaaaaaaa`(표지 sha `SHA_A`) · `NEW-bbbbbbbb`(`SHA_B`) | `aaaaaaaa` 로 exec, stderr 빔 |
| A37 | 503 이지만 본문이 있다 (F16) | health 503, 본문에 `version` 과 `client_wheel` | 따라간다 |
| A38 | 500 · 본문 없음 (F17) | | `unreachable` |
| A39 | wheel 이 없다 (F18 · §2.7) | `client_wheel: null` · `client_wheel_error: "metadata_missing"` | 경고가 `the server has no client wheel (metadata_missing)`. 캐시에 `NEW` 가 있으면 그것으로 exec(이때 경고를 쓰는지는 질문 19) |
| A40 | 옛 서버 (F19) | health 에 `client_wheel` 키 없음 | `old_server` |
| A41 | sha 불일치 (F20 · 결정 157) | health 의 sha 가 `SHA_A` | 경고 `sha256 mismatch`. `clients/` 에 대상 폴더도 `.tmp-*` 도 없다. exec 없음 |
| A42 | 선언보다 길다 (F21) | `/client/` 가 `bytes` + 1000 바이트를 보낸다 | 받기를 끊고 `unpack` 또는 `sha_mismatch`. 대상 폴더 없음 |
| A43 | 디스크 가득 (F22 · §2.5) | 풀기의 파일 쓰기에서 `OSError(ENOSPC)` 주입 | `cannot unpack: No space left on device`. 대상 폴더 없음, 임시 폴더 지워짐 |
| A44 | basic 서버 · 토큰 있음 (F23 · §2.4) | VersionedServer `read_auth = "basic"`, `RCM_TOKEN` | 따라간다. `/client/` 요청에 Bearer 가 있다 |
| A45 | basic 서버 · 토큰 없음 (F24) | | `auth` |
| A46 | Python 미달 (F25 · 결정 159) | `fake_wheel(NEW, requires_python=">=3.99")` | `needs Python >=3.99, this is 3.x.y`. 대상 폴더 없음 |
| A47 | 다른 꼴의 요구 (F26) | `~=3.11` · `>=3.11,<4` · `>3.11` · `==3.11.*` | 모두 `python`, 따라가지 않는다 |
| A48 | 캐시가 위험하다 (F27 · 결정 164) | `clients/` 0o777 · 0o770 · 남의 소유 | 모두 `cache_unsafe`, 경고에 경로가 들어간다. `/client/` 요청 0 |
| A49 | 나쁜 항목 (F28 · 결정 165) | `extra` 로 `../evil.py` · `/tmp/evil.py` · 심링크 항목(`external_attr = (S_IFLNK \| 0o777) << 16`) · `other_pkg/x.py` · `remote_ci_monitor\\x.py` | 각각 `unpack`. 대상 폴더 없음. `tmp_path` 전체를 훑어 캐시 밖에 새 파일이 없다 |
| A50 | 이름과 속 버전이 다르다 (F29 · 결정 161) | `fake_wheel(NEW, init_version=INSTALLED)` | `the wheel says vINSTALLED`, 대상 폴더 없음 |
| A51 | health 위조값 (R ② · §2.4) | `version` 이 `../x` · `0.8.1/../../x` · 65자. `path` 가 `/client@evil.example/x.whl` · `//evil/x.whl` · `/static/x.whl`. `sha256` 이 63자 · 65자 · 대문자 | 모두 `bad_health`, `/client/` 요청 0, `follow.json` 에 항목이 생기지 않는다 |
| A52 | 리다이렉트 (R ③ · §2.4) | health 가 302 · 301 · 307 · 308 로 두 번째 FakeServer 를 가리킨다. 따로 `/client/` 만 302 | 모두 `unreachable`. 두 번째 서버의 요청 수 0, 그러니 토큰이 새지 않는다 |
| A53 | 받는 중 Ctrl-C (R ④ · 결정 157) | 시험 안: `/client/` 응답을 읽는 중 `KeyboardInterrupt` 주입. 실제 프로세스: `/client/` 를 잡아 두고 `SIGINT` | 종료 130, stderr `interrupted`. `.tmp-*` 와 대상 폴더 없음 |
| A54 | path 와 version 이 다르다 (R ⑦) | version `NEW`, path 는 `INSTALLED` 의 파일 이름 | `bad_health` |
| A55 | 남의 폴더로 가는 링크 (F51 · 결정 164) | `$XDG_CACHE_HOME/rcm` 이 남 소유(주입) 폴더로 가는 링크 | `cache_unsafe`. 경고의 경로는 링크를 푼 경로 |
| A56 | 실패 문장 표 전부 (§2.6) | 코드 열두 개를 하나씩 일으킨다 | `<이유>` 가 표의 글자 그대로. 한 명령에 경고 줄은 많아야 하나. stdout 에는 아무것도 안 쓴다 |
| A57 | `bytes` 의 형식 (§2.4) | `True` · `0` · `-1` · `707673.0` · `"707673"` · `MAX_WHEEL_BYTES + 1` | 모두 `bad_health`. bool 이 int 로 새지 않는다 |
| A58 | 안 따라가는 명령은 설정을 안 읽는다 (§2.1 순서 · §2.3) | `RCM_CLIENT_VERSION=lates` 로 `rcm token list --config …` · `rcm init client --server …` · `rcm discover` · `rcm jobs --help` · `rcm serve` | 모두 종료 2 없이 오늘처럼 돈다. `serve` 는 명세가 따로 적었다 |
| A59 | 이름 허용 목록의 경계 (§2.5 · 결정 165) | `remote_ci_monitorX/evil.py` · `remote_ci_monitor-9.9.9.dist-info/METADATA`(목표 아님) · `Remote_CI_Monitor/x.py` | 모두 `unpack`. 앞 글자만 맞춰 보는 구현이면 첫 줄에서 빨갛다 |
| A60 | 3초 제한 (§2.4 · 결정 153) | 응답하지 않는 서버 · `rcm jobs` | 따라가기 판단이 3초 남짓에서 끝나고 `unreachable`. 전체 경과는 3초 + 명령 자신의 연결 실패 시간 |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A102 | 끝 줄바꿈 (§2.4 · §5.1) | `version = "99.0.1\n"`, `sha256 = SHA_A + "\n"`, `RCM_CLIENT_VERSION` 에 끝 `\n` | 모두 거절한다. `re.match` 의 `$` 는 끝 줄바꿈 앞에서도 맞으므로 명세의 정규식을 `match` 로 쓰면 첫 둘이 통과해 폴더 이름에 줄바꿈이 들어간다(질문 1) |
| A103 | health 본문이 이상하다 | 200 인데 HTML · JSON 배열 · `version` 없음 · `client_wheel` 이 문자열 | 예외 없이 끝난다. 앞 셋이 `bad_health` 인지 `unreachable` 인지는 질문 7. 넷째는 `bad_health` |
| A104 | `/client/` 의 다른 실패 | health 는 정상인데 `/client/` 가 404(그 사이 서버가 올라갔다) · 503 · 403 | 예외 없이 설치본으로 돈다. 코드는 질문 6 |
| A105 | 틀린 토큰 (§2.6 `auth`) | `read_auth = "basic"`, 토큰은 있지만 틀렸다 | `/client/` 401. 문장이 「needs a token」이라 토큰이 있는 사람을 헷갈리게 한다(질문 6) |
| A106 | `OSError` 아닌 zip 오류 | 깨진 zip(`BadZipFile`) · 암호 걸린 항목(`RuntimeError`) · 모르는 압축 방식(`NotImplementedError`) · 헤더 크기를 속인 항목(CRC 오류) | 모두 `unpack`, 트레이스백 없음, 대상 폴더 없음 |
| A107 | 이름의 나머지 가장자리 | `remote_ci_monitor/./x.py` · `remote_ci_monitor//x.py` · 같은 이름 두 번 · FIFO · 장치 파일(`external_attr`) | 제안: 모두 `unpack`. 명세는 `..` · 절대 경로 · 역슬래시 · 심링크만 적었다(질문 17) |
| A108 | `__init__.py` 가 없다 · 작은따옴표 | `drop=("__init__.py",)` · `__version__ = '99.0.1'` | 앞은 `version_mismatch` 또는 `unpack`. 뒤는 질문 17 |
| A109 | 실행이 실패한다 | 가짜 `execve` 가 `OSError(ENOENT)`(venv 의 python 이 지워졌다) | 트레이스백 없이 설치본으로 명령이 끝난다. §2.6 표에 코드가 없다(질문 5) |
| A110 | 표지가 사라졌다 | `exec_into` 직전에 다른 프로세스가 그 항목을 `.trash-*` 로 옮겼다(`os.utime` 이 `FileNotFoundError`) | 트레이스백 없음. 설치본으로 돌지 다시 받을지는 질문 25 |
| A111 | 기록 파일이 망가졌다 | `follow.json` 이 `"{"` · `[]` · 값이 숫자 · 읽기 권한 없음, 그리고 뿌리가 읽기 전용이라 쓰기 실패 | 예외 없이 health 를 부르고 따라간다. 쓰기 실패는 조용히 넘긴다(제안, 질문 22) |
| A112 | 설정 오류와 우선순위 (§2.2) | `RCM_CLIENT_VERSION=lates` + `--no-follow`, 틀린 한 줄 파일 + 맞는 환경 변수, `rcm version` + 틀린 환경 변수 | 질문 9 의 답을 하나씩 잠근다 |
| A113 | 깨진 `client.toml` 은 한 번만 말한다 (§2.1 4) | 모르는 키 · 없는 `--client-config` 경로 · 토큰을 담은 0644 파일 | 따라가기는 조용히 건너뛰고 명령이 오늘의 `ConfigError` 를 한 번 말한다. 종료 2 |
| A114 | 쓸 수 없는 서버 주소 | `--server ftp://x` · `--server "not a url"` · `--server --no-follow` | 네트워크 요청 0. 경고를 쓰는지는 질문 11. 설치본 파서가 오늘처럼 끝낸다 |
| A115 | 한 줄 파일을 못 읽는다 | 파일 자리에 폴더 · 권한 0 · UTF-8 이 아닌 바이트 | 제안: 종료 2, 문장에 경로(「조용히 멈추지 않는다」). 질문 10 |
| A116 | 고정했는데 서버를 못 본다 (§2.7 순서 3 · 4) | pin `NEW2`, 캐시 빔, 서버 꺼짐 | 설치본으로 돈다. 코드가 `pinned_unavailable` 인지 `unreachable` 인지는 질문 8 |
| A117 | health 를 기다리다 Ctrl-C | 응답하지 않는 서버, 1초 뒤 `SIGINT` | 종료 130, `follow.json` 에 아무것도 안 남는다 |
| A118 | 느리게 흘리는 서버 | health 가 2초마다 한 바이트 · `/client/` 가 반쯤 보내고 멈춘다 | health 는 전체 3초를 넘기지 않아야 한다. 받기에는 제한이 정해져 있지 않다(질문 18) |

## 엣지

### 명세에서

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A61 | 둘이 동시에 받는다 (F30 · §2.5 · 결정 163) | 실제 exec, `rcm probe` 둘. FakeServer `client_barrier = Barrier(2)` 로 두 `/client/` 요청이 모인 뒤에 보낸다. 같은 판을 열 개(`Barrier(10)`)로도 | `/client/` 2번(열 개면 10번). 대상 폴더 하나, 표지 하나. 모든 PROBE 의 `file` 이 같은 폴더 아래. `.tmp-*` 없음. 모두 종료 0 |
| A62 | 고리 막기 (F31 · 결정 161) | `remote_ci_monitor._follow_boot = {...}` 를 넣고 서버 `NEW2` | health 0, exec 없음 |
| A63 | 가리키는 폴더가 없다 (F32) | BOOT 직접, `RCM_FOLLOWED_PATH=<없는 폴더>`, 명령 `presets` | 종료 0, 설치본이 프리셋을 찍고 stderr 에 `the cached client did not load` 한 줄. health 는 BOOT 이후 0번(다시 안 따라간다) |
| A64 | 서버 둘 (F33 · §2.4) | FakeServer 둘: `OLD` 와 `NEW` | 기록 둘, 캐시 폴더 둘, 각 서버로 부를 때 그 버전으로 exec |
| A65 | 같은 버전 다른 sha (F34) | `NEW` 를 sha 가 다른 두 서버에서 | 폴더 `NEW-aaaaaaaa` · `NEW-bbbbbbbb` 둘 |
| A66 | 잠긴 항목을 지키는 청소 (F35 · §2.8) | 캐시에 다섯 버전. 그중 하나는 실제 exec 로 띄운 `rcm probe --sleep 30` 이 BOOT 의 공유 잠금을 쥐고 있다. 그 뒤 새 버전을 받는다 | 최근 셋 + 잠긴 것 + 새 것만 남는다(새 것을 셋에 세는지는 질문 16). 잠긴 프로세스는 끝까지 돌고 종료 0 |
| A67 | 안 따라가는 명령 (F36) | `RCM_CLIENT_VERSION=follow` 로 `init` · `token` · `discover` · `version` · `gc --config … --dry-run` | 각각 health 0 |
| A68 | `serve` (F37) | `RCM_CLIENT_VERSION=follow`, 캐시에 `NEW`. `follow.maybe_follow` 를 부르면 터지는 가짜로 | 가짜가 불리지 않는다. 띄운 서버의 `/api/health.version == INSTALLED` |
| A69 | Windows (F38 · 결정 172) | `os.name = "nt"` 주입 · 따로 `sys.executable = ""` | 둘 다 `platform`, 설치본으로 돈다 |
| A70 | 인자 목록 호출 (F39 · 결정 162) | `main(["jobs"])` · 서버 `NEW` | health 0, exec 없음 |
| A71 | M5q 이전 판으로 내려간다 (F40 · 결정 155) | 한 줄 파일 `follow`, 서버 `OLD` 의 wheel 은 `fake_wheel(OLD, drop=("follow.py",), cli=옛_클라이언트_설정을_읽는_PROBE)`. `client.toml` 은 `server` · `token` 만 | 자식이 돌고 `client.toml` 을 오늘의 엄격한 키 검사로 읽어 성공한다. BOOT 가 남긴 `_follow_boot` 는 옛 코드를 해치지 않는다 |
| A72 | 발견한 서버 (F41 · 결정 170) | `client.toml` 의 `server = "auto"`, `cli.discover` 를 하나를 돌려주는 가짜로(호출 수를 센다), 서버 `NEW` | `discover` 1번. exec 환경에 `RCM_SERVER=<찾은 주소>`. stderr 의 `server: found` 줄은 하나 |
| A73 | JSON 출력이 깨끗하다 (F42 · §2.6) | 서버 `NEW` 의 sha 를 속이고 `rcm run gate --no-wait` | stdout 은 JSON 한 줄로 파싱되고 경고는 stderr 에만 |
| A74 | 종료 코드와 PID (F43 · §2.5) | 실제 exec, VersionedServer `workers=True`. 실패하는 잡에 `rcm wait --job N`. 따로 `rcm probe --sleep 30` 에 `SIGINT` | `wait` 의 종료 1, PROBE 의 130. PROBE 가 찍은 `pid == proc.pid` |
| A75 | 모르는 명령과 플래그 (R ① · 결정 160) | 설치본이 모르는 `rcm probe` 와 `rcm run gate --new-flag`(가짜 wheel 의 `cli.py` 가 이 플래그를 안다) · 서버 `NEW`. 같은 것을 서버 없이 | 설치본 argparse 가 돌기 전에 exec 되고 자식이 해석한다. 서버가 없으면 설치본이 `invalid choice` · `unrecognized arguments` 로 종료 2 |
| A76 | 잠금 실패 (R ⑤) | BOOT 직접, `PREAMBLE` 이 `fcntl.flock` 을 `OSError(ENOLCK)` 를 내는 함수로 바꾼다. 따로 항목 폴더를 0500 으로 둬 `.lock` 을 못 만들게 | 둘 다 캐시 코드로 돈다(`loaded: True`). stderr 빔 |
| A77 | 망가진 캐시 (R ⑥) | 받아 둔 항목에서 `remote_ci_monitor/cli.py` 를 지운다. 다른 판은 문법 오류인 파이썬으로 바꾼다. `rcm presets` | 트레이스백 없이 `the cached client did not load` 한 줄과 설치본의 프리셋 목록. 종료 0, 다시 따라가지 않는다(`/client/` 0) |
| A78 | 도움말과 버전 (R9 · §2.1 2) | `rcm run --help` · `rcm -h` · `rcm --version` · 서버 `NEW` | health 0. 설치본의 도움말과 `rcm INSTALLED` |
| A79 | 훑기의 값 (R10) | `--server=URL` · `--server URL` · `--token=T` · `--client-config P` · `-- --no-follow` · `-f x=--server` · `--server -h` | 앞 넷은 값을 정확히 집는다. `--` 뒤와 다른 옵션의 값은 플래그로 보지 않는다. `--server -h` 는 `-h` 를 값으로 먹어 `wants_help False` |
| A80 | `skills` 세 갈래 (R11 · §2.3) | `rcm skills list` 를 (가) `RCM_SERVER` 있음 (나) `client.toml` 의 `server = "auto"` (다) 서버 꺼짐 | (가) 따라간다 (나) `discover` 0번, 설치본 (다) 설치본 + `unreachable` 한 줄 |
| A81 | 실패 기억 60초 (R12 · 결정 153) | 서버 꺼짐 · 기록 없음 · `rcm jobs` 를 `now = T`, `T+30`, `T+59`, 그리고 `T+61` | health 시도는 T 와 T+61 에 한 번씩. 경고는 네 번 모두 한 줄씩 |
| A82 | 따라가기 변수가 git 으로 새지 않는다 (R13 · 결정 161) | 실제 exec, `rcm run gate` 를 git 저장소에서. `PATH` 맨 앞의 `git` 이 환경을 파일로 쓴 뒤 진짜 git 을 exec 하는 래퍼다. PROBE 판은 `--spawn` | 래퍼가 남긴 환경과 PROBE 의 `env` 출력 어디에도 `RCM_FOLLOWED*` 가 없다 |
| A83 | 표지 utime (F49 · 결정 163) | A28 과 같은 기록, 표지 mtime 을 미리 `T - 30일` 로 | 부른 순간 mtime 이 `now` |
| A84 | 상대 `XDG_CACHE_HOME` (F50 · 결정 164) | `XDG_CACHE_HOME=relative/dir` · 빈 문자열 | 둘 다 `$HOME/.cache/rcm` |
| A85 | 토큰은 언제나 Bearer (F52 · §2.4) | `read_auth = "none"`, `RCM_TOKEN=t` | health 와 `/client/` 요청 모두 `Authorization: Bearer t` |
| A86 | 10분과 60초의 경계 (§2.4) | 기록 `checked_at = T`, now = `T+599` · `T+600`. 실패 `failed_at = T`, now = `T+59` · `T+60` | 599 는 health 0, 600 은 1(제안: `now - checked_at < 600` 이면 신선). 59 는 시도 0, 60 은 1 |
| A87 | 오래된 찌꺼기 (§2.8) | `clients/.tmp-x` · `.trash-y` 의 mtime 이 `T - 59분` · `T - 61분`, 새 버전을 받는다 | 61분 것만 지워진다 |
| A88 | 청소는 받은 직후에만 (§2.8) | 캐시 열 개 + `exec_cached` 한 번. 동시 받기에서 rename 에 진 쪽 | 둘 다 아무것도 지우지 않는다. 지운 프로세스는 rename 에 이긴 쪽 하나 |
| A89 | 버전 문자열의 경계 (§2.4 · §2.2) | 64자 · 65자, `0.7.0.dev0` · `99.0.1+local.1` · `1` · `0..1` · `0.8.0-` · `v0.8.1` | 64자와 가운데 셋은 통과, 나머지는 거절. `RCM_CLIENT_VERSION` 과 health 가 같은 판정이다 |
| A90 | 크기 상한의 경계 (§2.5 · 결정 165) | `MAX_UNPACKED_BYTES` 를 10,000 으로 주입, 푼 크기 합 10,000 · 10,001. `MAX_WHEEL_BYTES` 도 같은 방법으로 | 같으면 통과, 넘으면 `unpack` · `bad_health` |
| A91 | 실패가 기록을 지우지 않는다 (§2.4) | 기록 `NEW` · `SHA_A` 가 있는 채 health 실패 | 기록의 `version` · `wheel.sha256` 은 그대로이고 `failed_at` 만 바뀐다. 그래야 A36 이 다음 실행에도 된다 |
| A92 | 받은 뒤에 위험해진 캐시 (§2.5 · 결정 164) | 캐시에 `NEW` 를 받은 뒤 `clients/` 를 0o777 로 | 다음 실행이 캐시로 exec 하지 않고 `cache_unsafe` |
| A93 | 고정 버전과 같은 설치본은 서버를 안 부른다 (§2.7 순서 1) | pin `INSTALLED`, 서버 꺼짐 | 경고 없음, health 0 |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A119 | 줄임 옵션 (argparse `allow_abbrev`) | `rcm jobs --serv http://B`(환경은 `RCM_SERVER=http://A`) · `rcm jobs --tok T` · `rcm jobs --no-f` · `rcm jobs --he` · `rcm gc --conf s.toml --dry-run` | 지금 파서는 다섯 줄임을 다 받는다. 훑기는 정확한 이름만 보므로 첫 줄은 A 의 버전으로 따라가 놓고 명령은 B 와 말한다. `--no-f` 는 따라간다, `--he` 는 자식의 도움말이 나온다. 질문 2 의 답을 잠근다 |
| A120 | 명령이 없다 | `rcm` · `rcm --no-follow` | health 0, exec 없음. 설치본 파서가 `required: command` 로 종료 2 |
| A121 | 최상위에 둔 `--server` | `rcm --server http://B jobs` | 오늘도 사용법 오류다(최상위 파서에 `--server` 가 없다). 따라가든 말든 종료 2. 제안: 훑기는 명령 앞의 `--server` 를 값으로 쓰지 않는다 |
| A122 | `skills` 와 플래그 | `rcm skills list --server http://B` · `rcm skills --no-follow list` · `rcm --no-follow skills list` | `skills` 에는 `client_opts` 가 없어 앞의 둘은 설치본 파서에서 사용법 오류다. 셋째만 돈다. 훑기가 앞의 둘에서 서버를 「설정됐다」로 보는지는 질문 23 |
| A123 | 버전에 `-` 가 있다 (§2.4 `VERSION_RE`) | 캐시에 `0.8.0-rc1-<sha8>`(표지 version `0.8.0-rc1`), pin `0.8.0` | 그 항목을 쓰지 않는다. `scan_cache` 는 폴더 이름이 아니라 표지에서 버전을 읽는다 |
| A124 | sha 앞 8자가 같다 | `NEW` 의 sha 둘이 앞 8자만 같다. 하나를 받아 둔 뒤 다른 서버에서 받는다 | 같은 폴더 이름이다. rename 은 실패하고 표지 sha 가 달라 그 폴더를 쓰면 안 된다(질문 15) |
| A125 | 표지 없는 대상 폴더 | rename 하려는 자리에 표지 없는 비지 않은 폴더가 있다 | 트레이스백 없음. 기다리나 설치본으로 도나는 질문 15. 빈 폴더면 rename 이 덮어쓰고 정상으로 간다 |
| A126 | 따라간 자식의 설치 종류 | editable 부트로더 + `RCM_CLIENT_VERSION=follow` 로 따라간 자식에서 `rcm check` · `rcm version --json` | 자식의 `install_kind()` 는 캐시의 dist-info 를 읽어 `wheel` 이다. 표시가 부트로더의 `editable` 을 말해야 하는지는 질문 14 |
| A127 | 발견 꼬리표 | `server = "auto"`, 발견한 서버 `NEW`, 따라간 자식의 `rcm check` | 자식은 `RCM_SERVER` 로 주소를 받아 `cfg.discovered` 가 없다. `server` 행의 `(found on this network)` 가 사라진다(질문 12) |
| A128 | 발견이 두 번 | `server = "auto"`, (가) 같은 버전이라 exec 하지 않는다 (나) 찾은 서버가 없다 | 지금 모양이면 `discover` 가 두 번 불리고(1.5초 + 1.5초) `server: found` 줄도 두 번이다. 질문 12 의 답을 잠근다 |
| A129 | `check` 의 health 두 번 (§2.3) | `rcm check`, 같은 버전 | 따라가기의 새 읽기와 `cmd_check` 자신의 `client.health()` 로 2번이다. 실패 기억 60초 안이면 `fresh=True` 가 그것도 무시하는지는 질문 13 |
| A130 | 주소의 기본 포트와 경로 | `http://h` 와 `http://h:80`, `http://h:8787/rcm`(경로 접두. `Client` 는 지금 `server + path` 로 붙인다), `http://[::1]:8790` | 질문 11 의 답을 잠근다. 경로를 버리면 `/client/` 를 엉뚱한 곳에서 받는다 |
| A131 | 시계가 뒤로 | 기록 `checked_at = T + 1시간`, now = T | 제안: 신선하지 않은 것으로 보고 health 를 부른다(질문 21) |
| A132 | 청소가 링크를 따라가지 않는다 | `clients/` 안에 다른 폴더를 가리키는 링크 항목(표지 있음), 지울 차례 | 링크만 사라지고 가리킨 폴더의 파일은 그대로. 예외 없음 |
| A133 | 모르는 폴더 | `clients/notes/`(표지 없음), `clients/readme.txt` | 제안: 청소가 건드리지 않는다(질문 16) |
| A134 | 항목 폴더의 권한 | `clients/` 0o755(허용), 그 안 항목 하나가 0o777 | 질문 16. 명세는 뿌리와 `clients/` 만 본다 |
| A135 | `XDG_CACHE_HOME="~/c"` 와 상대 `XDG_CONFIG_HOME` | 따옴표로 막힌 `~`, `XDG_CONFIG_HOME=rel` | 캐시는 상대 경로로 보고 무시한다. `config.user_config_dir` 는 `expanduser` 하고 상대 경로도 받는다. 한 줄 파일이 어느 규칙을 따르는지는 질문 10 |
| A136 | 한 줄 파일의 꼴 | `0.6.1  # pinned` · CRLF 줄 끝 · UTF-8 BOM · 주석만 · 빈 파일 · `Follow` | CRLF 는 통과(공백 정리). 나머지는 질문 10 |
| A137 | 동시에 쓰는 기록 | 서로 다른 서버 주소로 프로세스 열 개가 동시에 `save_record` | `follow.json` 은 언제나 JSON 으로 읽힌다. 일부 항목이 빠질 수 있다(명세가 허용한다). 임시 파일이 남지 않는다 |
| A138 | 받다가 SIGTERM | 실제 프로세스, `/client/` 를 잡아 두고 `SIGTERM` | 기본 처리기로 죽고 `.tmp-*` 가 남는다. 1시간 뒤의 받기가 지운다(A87) |
| A139 | 표시의 `cached` | 캐시에 `NEW` 둘(sha 다름) · `OLD` 하나 | 순서(최근 사용 순인가)와 같은 버전을 두 번 쓰는지는 질문 20 |
| A140 | `server = "auto"` 의 `rcm version` | 기록은 있고 `client.toml` 은 auto | `discover` 0번(버전은 서버에 묻지 않는다). 둘째 줄을 쓰는지는 질문 20 |
| A141 | 설치본으로 돌아간 자식의 `check` | A77 상황에서 `rcm check` | `_follow_boot.loaded False`. `client` 행 꼬리는 질문 14 |
| A142 | 처음 받을 때의 줄은 이긴 쪽만? | A61 의 stderr | 각 프로세스에 `following server` 줄이 많아야 하나 |

## 명세에 묻는 것

명세를 고치지 않고 여기에 적는다. 각 질문 끝에 그 답이 잠글 시나리오 번호를 붙였다.

1. `VERSION_RE` 와 `SHA256_RE` 는 `$` 로 끝난다. `re.match` 나 `re.search` 로 쓰면 `$` 가 끝 줄바꿈 앞에서도 맞아서 `"99.0.1\n"` 이 통과하고 그 값이 폴더 이름이 된다. `fullmatch` 를 쓰거나 `\Z` 로 바꾸라고 명세에 적어야 하지 않나. (A102)
2. 지금 파서는 `allow_abbrev` 가 기본값이라 `--serv` · `--tok` · `--no-f` · `--he` · `--conf` 를 받는다. 훑기가 정확한 이름만 보면 따라가기는 A 서버의 버전으로 하고 명령은 B 서버와 말할 수 있다. 모든 파서를 `allow_abbrev=False` 로 바꾸나(기존 사용자의 줄임이 깨진다), 훑기가 argparse 와 같은 접두 규칙을 따르나. (A119)
3. F10 은 editable 기본에서 `rcm version` 둘째 줄 `follow: off (editable install)` 을 기대한다. §2.9 는 「설정이 기본이 아니거나 기록이 있을 때만」 둘째 줄을 쓴다고 적었고 editable 기본은 기본 설정이다. 개발 venv 는 editable 이라 F10 을 따르면 `test_cli_m4.py:357` 의 한 줄 시험이 빨개진다. 어느 쪽인가. (A10 · A13)
4. BOOT 가 §5.1 인터페이스에 없다. BOOT 를 직접 띄우는 시험(A63 · A76)과 exec 인자 비교(A2)에 `follow.BOOT` 공개 상수가 필요하다. 같은 이유로 `_main` 이 `sys.argv[1:]` 를 훑으므로 시험은 `sys.argv` 를 바꿔야 하고, 「설치본」은 `follow.install_kind` 를 바꿔 주입한다. 이 두 주입점을 계약으로 적어 두나. (A2 · A29 · A63 · A76)
5. CLI 쪽 `execve` 가 `OSError` 로 실패할 때(venv 의 python 이 지워졌다)의 코드가 §2.6 표에 없다. 워커는 W9 가 「로그 한 줄, 종료 1」이다. CLI 는 설치본으로 계속하고 경고 한 줄인가. (A109)
6. `/client/` 의 404(health 와 받기 사이에 서버가 올라갔다), 503(조립 실패), 403 은 어느 코드인가. `auth` 의 문장 「needs a token」은 토큰이 틀린 경우에도 맞나. (A104 · A105)
7. health 가 200 인데 JSON 이 아니거나 배열이거나 `version` 이 없으면 `bad_health` 인가 `unreachable` 인가. `bad_health` 도 60초 실패 기억에 드나. 그 서버의 옛 기록이 있으면 기록과 sha 가 맞는 캐시로 도나. (A51 · A103)
8. 고정 `X` 인데 설치본도 캐시도 아니고 서버도 못 볼 때 코드는 `pinned_unavailable` 인가 `unreachable` 인가. 실패 문장의 틀 `not following server v<X>` 는 서버 버전을 모를 때(`unreachable`) 무엇을 쓰나. (A35 · A56 · A116)
9. 검증과 우선순위의 순서. `--no-follow` 가 있으면 틀린 `RCM_CLIENT_VERSION` 을 보지 않고 넘어가나. 환경 변수가 맞으면 틀린 한 줄 파일은 읽지도 않나. 안 따라가는 `rcm version` 이 둘째 줄을 그리려고 틀린 값을 읽으면 종료 2 인가. (A112)
10. 한 줄 파일과 환경 변수의 꼴. 빈 `RCM_CLIENT_VERSION=` 은 없는 것인가. `Follow` · `INSTALLED` 처럼 대소문자가 다르면. 줄 끝 주석(`0.6.1  # pinned`), BOM, 주석만 있는 파일, 폴더 · 읽기 권한 없음 · UTF-8 아님. 「`$XDG_CONFIG_HOME/rcm/client-version`(없으면 `~/.config/rcm/client-version`)」의 「없으면」이 변수가 없을 때인가 파일이 없을 때인가(`client.toml` 은 둘 다 본다). 상대 `XDG_CONFIG_HOME` 은 캐시처럼 무시하나. (A20 · A115 · A135 · A136)
11. 주소 정규화의 나머지. `http://h` 와 `http://h:80` 은 같은 키인가. 경로 접두가 있는 주소(`http://h/rcm`)의 경로를 버리나. IPv6 표기. 쓸 수 없는 주소(`ftp://` · 공백 · `--no-follow` 를 값으로 먹은 것)는 경고 없이 건너뛰나. (A114 · A130)
12. 발견의 횟수. 같은 버전이라 exec 하지 않을 때와 찾은 서버가 없을 때 명령이 `discover` 를 다시 불러 1.5초를 두 번 쓰고 `server: found` 줄도 두 번 나온다. 찾은 결과를 명령에 넘기나(`os.environ["RCM_SERVER"]` 를 이 프로세스에도 둔다든지). 따라간 자식은 `RCM_SERVER` 로 주소를 받아 `rcm check` 의 `(found on this network)` 가 사라지는데 그대로 두나. (A72 · A127 · A128)
13. `rcm check` 는 따라가기의 새 health 와 자기 health 로 두 번 묻는다. 하나로 줄이나. `fresh=True` 는 60초 실패 기억도 무시하나. (A16 · A129)
14. 따라간 자식의 표시. 자식의 `install_kind()` 는 캐시의 dist-info 를 읽어 `wheel` 이다. `FollowState.install` 이 부트로더의 종류를 말해야 하면 BOOT 에 건넬 변수가 하나 더 필요하다. BOOT 가 설치본으로 돌아간 자식(`loaded False`)의 `client` 행 꼬리는 무엇인가. (A126 · A141)
15. 폴더 이름의 모호함. 버전에 `-` 가 들어갈 수 있어서 `X-*` 찾기가 `0.8.0-rc1-…` 을 `0.8.0` 으로 읽을 수 있다(표지로 읽으면 풀린다). sha 앞 8자가 같은 두 wheel 은 같은 폴더 이름이다. rename 에 진 쪽이 본 대상 폴더에 표지가 없거나 표지 sha 가 다르면 무엇을 하나. (A123 · A124 · A125)
16. 청소의 셈. 「최근 3개 + 지금 목표」에서 막 받은 목표를 셋에 세나(F35 의 「최근 사용 3개 + 잠긴 것 + 새 것」은 안 세는 쪽으로 읽힌다). 표지 없는 모르는 폴더와 파일은 남기나. `clients/` 가 0o755 일 때 항목 폴더의 권한도 보나. (A30 · A66 · A133 · A134)
17. 풀기의 나머지. `.` 조각과 빈 조각, 같은 이름 두 번, FIFO · 장치 파일 항목을 거부하나. `__init__.py` 가 없거나 `__version__` 이 작은따옴표면 `version_mismatch` 인가 `unpack` 인가. `Requires-Python: >= 3.11`(공백)과 그 줄이 둘인 METADATA 는. (A107 · A108)
18. 시간 제한. `HEALTH_TIMEOUT_SECONDS` 는 소켓 동작마다인가 전체인가(urllib 의 `timeout` 은 동작마다라서 조금씩 흘리는 서버는 3초를 넘긴다). `/client/` 받기에는 제한이 없다. 멈춘 서버 앞에서 `rcm` 이 끝없이 기다리나. (A118)
19. `client_wheel: null` 인데 캐시에 같은 버전이 있어 exec 할 때 `no_wheel` 경고를 쓰나. 옛 서버(`old_server`)에 같은 버전 캐시가 있을 때도 같은가. (A39)
20. 표시의 세부. `--json` 의 `cached` 와 둘째 줄의 `cached …` 는 최근 사용 순인가, 같은 버전이 sha 만 다르면 두 번 쓰나. `server = "auto"` 일 때 「기본 서버의 기록」은 무엇인가. `check` 꼬리는 `--no-follow` · 환경 변수 `installed` · 한 줄 파일 고정을 각각 어떻게 쓰나(§2.9 는 `pinned by RCM_CLIENT_VERSION` 만 보인다). 고정으로 서버보다 낮을 때 `older` 뒤의 `pip install` 힌트는 빠지나. (A14 · A15 · A139 · A140)
21. `checked_at` 이나 `failed_at` 이 지금보다 미래면(시계가 뒤로 갔다) 신선한 것으로 보나. (A131)
22. 기록 파일이 망가졌거나 쓸 수 없을 때 조용히 넘어가나. health 가 실패해 `failed_at` 을 쓸 때 기존 `version` · `wheel` 을 남기는 것이 계약인가(F15 는 그것에 기댄다). (A91 · A111)
23. 명령이 없는 호출(`rcm`, `rcm --no-follow`)은 따라가지 않는 것으로 읽어도 되나. `skills` 에는 `client_opts` 가 없어 `--server` 와 `--no-follow` 를 하위 명령 뒤에 못 쓴다. `skills` 에 `client_opts` 를 더하나, 훑기가 그 자리의 `--server` 를 무시하나. (A120 · A122)
24. §6 F 표의 번호가 F47 다음 F49 로 건너뛴다. F48 이 빠진 것인가.
25. exec 직전에 표지가 사라지면(다른 프로세스의 청소가 `.trash-*` 로 옮겼다) 설치본으로 도나, 다시 받나. (A110)
