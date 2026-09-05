# M3 테스트 시나리오 — 담당 C (설정 검증 · 서비스 파일 · 웹 Source 칸)

> `docs/m3-workplan.md` §7 의 담당 C. `src/` 는 건드리지 않았다. 구현이 없어 빨간 테스트는 그대로 둔다.
> 작성 시점 2026-09-05. 구현자가 `config.py` · `core/model.py` · `core/status.py` 를 동시에 고치고 있어
> 설정 테스트는 이미 초록이다(§4 참조).

## 1. 파일과 개수

| 파일 | 케이스 | 상태(작성 시점) |
|---|---|---|
| `tests/test_config.py` (추가분) | 23 (함수 21 · parametrize 2×2) | 39/39 초록 — 기존 16 + 추가 23 |
| `tests/test_examples.py` (신규) | 12 | 8 초록 · 4 빨강 — plist·unit 은 구현자가 방금 추가해 통과, README 절 3 개와 server.toml 의 `[[repos]]` 예시가 남았다 |
| `tests/web/source.test.js` (신규) | 20 | 5 초록 · 15 빨강 — 아래 §3 |

실행: `ruff check` · `ruff format --check` 통과. `node --test tests/web/source.test.js` 문법 오류 없음.

## 2. 시나리오

### 2.1 설정 — `[[repos]]` · 프리셋 `repo` (명세 §1.1)

| # | 시나리오 | 기대 |
|---|---|---|
| 1 | `source_modes = ["git_ref"]` + `repo = "app"`, repos 에 `app`·`lib` | 로드 · `Preset.repo == "app"` · `cfg.repo("app").url` · `cfg.repo("nope") is None` |
| 2 | git_ref 프리셋, `repo` 없음, repos 가 정확히 하나 | `repo` 가 그 이름으로 자동 채워진다 |
| 3 | git_ref 프리셋, `repo` 없음, repos 둘 | `ConfigError` — `preset 'deploy'` 와 `repo` 를 담는다 |
| 4 | `repo = "nope"` | `ConfigError` — `preset 'deploy'` 와 `'nope'` |
| 5 | tree 전용 프리셋에 `repo = "app"` (repos 있음) | `ConfigError` — `preset 'gate'` 와 `only valid with` |
| 6 | 5 와 같지만 repos 가 아예 없음 | 같은 오류 — `repo` 키 자체가 git_ref 전용 |
| 7 | `source_modes = ["tree", "git_ref"]` + `repo` | 허용 |
| 8 | tree 프리셋 | `not Preset.repo` (빈 문자열이든 None 이든) |
| 9 | `parse_preset(... repo=5)` | `ConfigError` — 프리셋 이름과 `repo` |
| 10 | `url = ""` | `ConfigError` 에 `url` |
| 11 | `url = "--upload-pack=evil"` | `ConfigError` 에 `url` (옵션 주입) |
| 12 | 같은 이름 `[[repos]]` 둘 | `ConfigError` 에 `duplicate`(대소문자 무관) 와 이름 |
| 13 | `name = "bad name"` | `ConfigError` 에 `name` |
| 14 | `[[repos]]` 있고 PATH 에 git 없음 | `ConfigError` 에 `git` |
| 15 | `[[repos]]` 없고 PATH 에 git 없음 | 정상 로드 — git 은 repos 가 있을 때만 필요 |
| 16 | `preset_json` | git_ref 프리셋 `"repo": "app"` · tree 프리셋 `"repo": None` |

### 2.2 설정 — 새 `[server]` 키 (명세 §1.1 · §2.2)

| # | 시나리오 | 기대 |
|---|---|---|
| 17 | 기본값 | `git_resolve_timeout_seconds 20` · `git_fetch_timeout_seconds 600` · `retention_sweep_interval_seconds 3600` |
| 18 | 파일에서 5 · 30 · 60 | 그대로 |
| 19 | `retention_sweep_interval_seconds = 59` | `ConfigError` 에 키 이름 |
| 20 | `retention_days_success/failure = -1` | `ConfigError` 에 키 이름 · `0` 은 허용(§2.1 「끝나자마자 다음 sweep」) |
| 21 | `git_*_timeout_seconds = 0` | `ConfigError` 에 키 이름 (가정 A1) |
| 22 | `RCM_SERVER_GIT_FETCH_TIMEOUT_SECONDS=30` | 30 · `"soon"` 이면 `[server] git_fetch_timeout_seconds` 를 담은 오류 |

### 2.3 서비스 파일 · 예시 · README (명세 §4 · §5)

| # | 대상 | 기대 |
|---|---|---|
| 23 | `examples/launchd/com.remote-ci-monitor.server.plist` | `plistlib.loads` 파싱 · `Label` · `ProgramArguments[0]` 절대경로이며 `rcm` 으로 끝남 · `[1] == "serve"` · `--config` 다음 인자가 `server.toml` 로 끝남 |
| 24 | 〃 | `RunAtLoad is True` · `KeepAlive` 가 dict 면 `SuccessfulExit false`, 아니면 `true` · `ThrottleInterval` 정수 ≥ 1 |
| 25 | 〃 | `StandardOutPath`/`StandardErrorPath` 가 `.log` · `EnvironmentVariables.PATH` 에 `/opt/homebrew/bin` 과 `/usr/bin` (`:` 분리 원소로) |
| 26 | 〃 원문 | `launchctl bootstrap` · `caffeinate` 또는 `pmset` 이 주석에 있다 |
| 27 | `examples/systemd/rcm-server.service` | `ConfigParser(strict=False, interpolation=None)` 로 파싱 · `[Unit] After` 에 `network-online.target` · `[Install] WantedBy` 비어 있지 않음 |
| 28 | 〃 `[Service]` | `ExecStart` 가 `/` 로 시작(systemd 는 절대경로만) 하고 `rcm serve` · `--config` 포함 · `User` · `Restart ∈ {on-failure, always}` · `RestartSec` · `KillSignal == SIGTERM` · `TimeoutStopSec` |
| 29 | 〃 | 원문에 `PYTHONUNBUFFERED=1` · `NoNewPrivileges=true` |
| 30 | `examples/server.toml` | `load_server_config` 로 읽힌다(git 필요 → 없으면 skip). `deploy` 프리셋이 살아 있으면 git_ref 이고 `repo` 가 차 있다; 주석 처리면 None 이라 통과 |
| 31 | 〃 원문 | `[[repos]]` · `name = "deploy"` · `source_modes = ["git_ref"]` (앞에 `# ` 허용) · `git_fetch_timeout_seconds` · `retention_sweep_interval_seconds` 언급 (가정 A4) |
| 32 | README 「Run as a service」 절 | 절 안에 `launchctl` · `systemctl` · `SIGTERM` · `lost` · `caffeinate`/`pmset` |
| 33 | README | `read_auth = "basic"` · `username` · `token name` · `TLS` |
| 34 | README | `--ref` · `[[repos]]` · `git submodule` (가정 A5) |

### 2.4 웹 — `tests/web/source.test.js`

| # | 함수 | 시나리오 | 기대 |
|---|---|---|---|
| 35 | 계약 | `rcm.DASH === "—"` · `typeof rcm.sourceHtml === "function"` | export 필요(가정 A2) |
| 36 | `sourceHtml` git_ref | `{sha: 40hex, repo: "app", ref: "main"}` | `0123456` 포함, 전체 sha 미포함, `app`, `ref main`, `data-src="7"`, `class="sha"` |
| 37 | 〃 | `ref = "<b>x</b>"`, `repo = 'a"b'` | `ref &lt;b&gt;x&lt;/b&gt;` · 원문 `<b>` 없음 · `a&quot;b` |
| 38 | 〃 | `sha null` | 버튼 안이 `—`, `ref main` 은 유지 |
| 39 | 〃 | `sha = "abc"` (7자 미만) | 그대로 · 예외 없음 |
| 40 | 〃 | `ref null` | `ref —` |
| 41 | 〃 | git_ref 행 | `uncommitted` · `tree ` · `not received yet` 없음 |
| 42 | `sourceHtml` tree (회귀) | 픽스처 #412 (dirty) | `abc123f` · `uncommitted` · `title="tree 9f8e…"` · `org/app` · `ref ` 없음 |
| 43 | 〃 | #413 (clean) | `def4567` · `uncommitted` 없음 |
| 44 | 〃 | #415 uploading, `base_sha null` | `not received yet` |
| 45 | 〃 | #415 uploading, base_sha 있음 | `77aa88b`, `not received yet` 없음 |
| 46 | 〃 | `tree_hash null` + `base_sha null` (queued) | `title="tree —"` · `>—</button>` |
| 47 | 〃 | `source` 없음 | 예외 없이 `—` |
| 48 | `reasonText` | materializing git_ref, `ref main` | `preparing workspace · fetching main` · actionable false · links [] |
| 49 | 〃 | `ref null` (bytes 가 있어도) | `preparing workspace` — git_ref 는 `unpacking` 을 붙이지 않는다 |
| 50 | 〃 | `ref = "<b>"` | 텍스트 그대로 `fetching <b>` — 이스케이프는 렌더러의 몫 |
| 51 | `rerunCommand` | 실패한 git_ref 잡 `ref v1.2.3` | `rcm run deploy --ref v1.2.3` (가정 A3) |
| 52 | 〃 | 입력 + ref | `rcm run deploy` 로 시작 · ` -f env=prod` · ` --ref main` 포함 (순서 무관) |
| 53 | 〃 | tree 잡 | `rcm run gate -f scope=full` (변화 없음) |

## 3. 지금 빨간 것과 구현자가 할 일

- **`app.js` — `sourceHtml` 을 `rcm` 객체에 export** (`DASH: DASH, esc: esc, sourceHtml: sourceHtml, …`). 스크래치 복사본에 이 한 줄만 넣고 돌리면 36–50 이 전부 초록이 되는 것을 확인했다. 즉 Source 칸 로직 자체는 이미 맞고, 테스트 가능성만 없다.
- **`app.js` — `rerunCommand` 에 `--ref <ref>`** (51·52). 지금은 `rcm run deploy` 만 만든다 → 명세 §1.5 대로면 복사해 붙이는 순간 usage 2 「preset 'deploy' needs --ref」. 명세에 없던 항목이라 가정 A3 로 적었다.
- `examples/launchd/*.plist` · `examples/systemd/*.service` (23–29) — 작성 중에 추가돼 이미 초록.
- `examples/server.toml` 에 `[[repos]]` + `deploy` 예시(주석 가능) + 새 `[server]` 키 (30·31).
- README 「Run as a service」 · Basic 인증 문단 · `--ref`/`[[repos]]`/`git submodule` (32–34).
- 설정(1–22)은 작성 시점의 `config.py` 로 이미 초록 — 구현자의 동시 작업이 명세와 일치한다.

## 4. 가정

- **A1** `git_resolve_timeout_seconds`·`git_fetch_timeout_seconds` 는 `>= 1`. 명세는 하한을 안 적었지만 0 타임아웃은 곧 실패이고 다른 `*_seconds` 키가 모두 `>= 1` 이다. (현 구현도 그렇게 검사한다.)
- **A2** `sourceHtml` 은 `rcm` 에 export 된다. 명세 §1.5 「app.js sourceHtml … 테스트로 잠근다」를 하려면 필수 — `rowHtml` 은 DOM 쪽이라 node 에서 못 부른다. `format.test.js` 의 계약 목록은 건드리지 않고 `source.test.js` 안에서 계약을 잠갔다.
- **A3** 최근 실패/타임아웃 행의 재실행 명령은 git_ref 잡이면 `--ref <ref>` 를 싣는다. `-f` 와 `--ref` 의 순서는 정하지 않았다(첫 케이스만 입력이 없어 정확 비교).
- **A4** `examples/server.toml` 은 새 `[server]` 키 둘(`git_fetch_timeout_seconds`·`retention_sweep_interval_seconds`)을 (주석으로라도) 보여준다. 운영자가 knob 를 예시에서 발견한다는 기존 관행(다른 키들도 그렇다).
- **A5** README 는 `rcm run PRESET --ref` 와 `[[repos]]` 를 언급한다(세션 명령 표·설정 예시). 명세 §4·§5 는 서비스·Basic·submodule 만 못 박았지만 git_ref 가 M3 의 완료 기준이라 문서에 없으면 이상하다.
- **A6** `[[repos]]` 가 든 설정이 성공적으로 로드돼야 하는 케이스와, 오류 메시지가 특정 검증에서 나와야 하는 케이스(3–5·10–13·16·30)는 `git` 이 없으면 `skip` 한다(§7 규칙). 검증 순서(git 확인 vs 프리셋 검증)는 구현 자유이므로 git 없는 머신에서 어느 오류가 먼저 나올지 못 박지 않았다.
- **A7** git 없음 테스트(14·15)는 `PATH` 를 빈 tmp 디렉터리로 바꾼다. `load_server_config(environ=…)` 가 env 단계에서 `os.environ` 을 통째로 갈아끼우므로 `environ` 에도 같은 `PATH` 를 넣었다 — 안 넣으면 `shutil.which` 가 `os.defpath`(`/usr/bin`) 로 새서 git 을 찾는다.
- **A8** launchd `KeepAlive` 는 dict(`SuccessfulExit false`) 를 우선으로 보되 `true` 도 받는다. `EnvironmentVariables.PATH` 는 `:` 로 나눈 원소로 검사한다(부분 문자열 우연 일치 방지).
- **A9** systemd unit 은 `Environment=` 반복을 위해 `strict=False`; 반복 시 마지막 값만 남으므로 `PYTHONUNBUFFERED=1` 은 원문으로 검사한다. `ExecStart` 는 절대경로 시작 — 명세엔 없지만 systemd 가 요구한다.
- **A10** README 절 추출은 `## `/`### ` 「Run as a service」 제목부터 다음 `## ` 까지. 절 밖의 우연한 `SIGTERM`·`lost`(이미 README 에 있다)로 통과하지 않게 절 안에서만 본다.

## 5. 명세에서 이상하거나 빠진 것

1. **`rerunCommand` 의 `--ref`** (A3) — §1.5 가 CLI 규칙을 바꾸면서 웹 「다시 실행」 명령을 언급하지 않았다. 안 고치면 최근 실패한 deploy 의 명령이 복사 즉시 usage 오류.
2. **재실행 명령의 셸 인용** — `validate_ref` 는 공백·제어문자·`-` 시작 등만 막고 `$`·`;`·`&`·`(` 는 통과시킨다(git 도 허용). `rerunCommand` 는 입력값도 인용하지 않으므로(기존) `--ref` 도 같은 결이지만, 이 값은 다른 사용자가 제출한 것이라 복사-붙여넣기 위험이 조금 더 크다. 테스트로 못 박지는 않았고 기록만 한다 — 결정 필요.
3. **`Preset.repo` 의 타입** — §1.1 은 `str = ""`, JSON 은 「없으면 null」. 데이터클래스는 `""`, JSON 은 `None` 으로 두 표현이 갈린다. 테스트는 `not p.repo` 와 `json["repo"] is None` 으로 둘 다 허용했다.
4. **`sourceHtml` export** (A2) — 명세가 「이미 있음 — 테스트로 잠근다」 라고만 해서 export 가 빠질 수 있다.
5. **`render_text._source_text`** — §1.5 가 같이 잠그라고 한 터미널 쪽 Source 표기(`app @a1b2c3d ref main`)는 §7 표에서 누구에게도 배정되지 않았다. C 의 몫이 「웹 source 셀」이라 여기서는 안 썼다. B(`test_cli_m3`) 나 A 가 맡아야 한다.
6. **git_ref 잡의 `sourceHtml` 에 `title` 이 없다** — tree 버튼은 `title="tree <hash>"` 로 전체 해시를 보여주는데 git_ref 버튼은 전체 sha 를 어디에도 안 싣는다(클릭 토스트는 `base_sha` 를 본다 — git_ref 는 `base_sha == sha` 라 서버 JSON 에 `base_sha` 가 없으면 `—`). `source_json` 의 git_ref 분기는 `base_sha` 를 안 싣는다. 사소하지만 「전체 sha 를 어디서 보나」가 비어 있다. 테스트로 못 박지 않았다.
7. **`examples/server.toml` 의 `[[repos]]` 가 살아 있으면** 그 파일을 읽는 모든 곳(README 5-minute setup 의 `cp examples/server.toml …`)에서 git 이 없을 때 시작이 막힌다. 주석 처리 예시가 맞다(명세도 그렇게 적었다). 테스트 30 은 두 경우 다 통과한다.
8. **`retention_sweep_interval_seconds` 의 env 이름** `RCM_SERVER_RETENTION_SWEEP_INTERVAL_SECONDS` 는 자동으로 생기지만(dataclass 필드 순회) 명세에 언급이 없다 — 문서화만 하면 된다.
