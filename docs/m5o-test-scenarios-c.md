# M5o 테스트 시나리오 C — 프리셋을 파일에서 읽는다 · `rcm reload --dry-run` (2026-11-18)

`docs/m5o-workplan.md` 의 **§4**(include · reload --dry-run)와 **§5 C 표**(C1~C15), 결정 **121~124** 를
테스트로 옮긴 것이다. 한 줄 요약: **프리셋은 server.toml 옆의 파일에서 읽되, 그 파일은 프리셋만 쓸 수
있고 서버 사용자만 고칠 수 있다. 바꾸기 전에 무엇이 바뀔지 키 이름으로 미리 본다.**

이 문서가 **안 건드리는 것**: supersede(역할 A), 실패 요약 · 알림 링크(역할 B).

## 테스트 파일

| 파일 | 수 | 무엇 |
|---|---|---|
| `tests/test_config_include.py` | 47 | `load_server_config` 의 `include` — 경로 · 권한 · 허용 구역 · 이름 겹침 · 출처 기록 |
| `tests/test_reload_dry_run.py` | 27 | `App.reload_config(dry_run=…)` · `POST /reload {"dry_run": true}` · `rcm reload --dry-run` · `rcm check` 의 `local presets` 행 |
| `tests/test_docs_m5o_include.py` | 7 | 문서 잠금 — 경고 문단 · `--dry-run` · `include` 키 |

작성 시점(구현 전) 결과: `test_config_include.py` 는 「모르는 구역 `include`」로, `test_reload_dry_run.py` 는
픽스처의 설정 로드 실패와 `changed_keys` 가 없어서 빨갰다. 초록이던 것은 `include` 원소 모양 검사(C17 —
옛 코드도 모르는 구역으로 거절했다)와 server.toml 권한 무시(C6 마지막) 같은 회귀 방어뿐이다.

## 공통 — 픽스처와 규칙

- 들인 파일은 테스트가 **직접 `chmod 0o644`** 한다. CI 의 umask 가 002 면 그냥 만든 파일이 그룹 쓰기
  가능이라 권한 검사가 엉뚱한 데서 빨개진다.
- 「남의 소유」는 root 없이 못 만든다 — `os.geteuid` 를 다른 숫자로 바꿔 소유자 불일치를 만든다.
- 서버 시험은 `tests/test_reload.py` 의 `live` 픽스처와 같은 꼴(설정 파일 + 그 위의 App)이다.
- CLI 시험은 `test_server.Server` 의 진짜 HTTP 서버에 `cli.main([...])` 를 in-process 로 부른다.

## §5 C 표 (최소 집합)

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| C1 | 베스트: 들인 파일 22개 + server.toml 2개 | `include = ["presets.dolomood.toml"]` | 프리셋 24개. 순서는 server.toml 의 것 먼저, 그다음 include 순서대로 각 파일 안의 순서 |
| C2 | 상대 · 절대 · `~` 경로 | 상대 경로는 다른 cwd 에서 로드, 절대 경로, `HOME` 을 tmp 로 두고 `~/x.toml` | 상대는 **server.toml 폴더** 기준(cwd 아님), 절대는 그대로, `~` 는 홈 |
| C3 | 들인 파일에 `[server]` · `[[repos]]` · `[[notify]]` · `[[probes]]` · `include` | 각각 한 파일씩 | 각각 `ConfigError`, 메시지에 들인 파일 이름과 그 구역 이름 |
| C4 | 이름 겹침 | server.toml 과 들인 파일에 `gate` | `ConfigError: preset 'gate' is defined in both server.toml and presets.dolomood.toml` |
| C4b | 들인 파일 둘 사이의 겹침 | `a.toml` · `b.toml` 둘 다 `gate` | 두 파일 이름 |
| C5 | 파일 없음 | 없는 경로 | `ConfigError`(경로가 들어간다). reload 는 400 `config unchanged`, 프리셋 그대로 |
| C6 | 그룹 쓰기 가능 · 남이 쓰기 가능 · 남의 소유 | `0o664` · `0o646` · `geteuid` 바꿈 | 셋 다 `ConfigError`. `0o644` · `0o600` · `0o444` 는 통과 |
| C7 | 들인 파일이 깨진 TOML | `===` | 기동 거절(`ConfigError`, 들인 파일 경로) · reload 400 · 아무것도 안 바뀜 |
| C8 | `include` 가 문자열 하나 · 빈 목록 | `include = "a.toml"` · `include = []` | 문자열은 `ConfigError`(목록으로 쓰라는 안내), 빈 목록은 없는 것과 같다 |
| C9 | reload 로 들인 파일만 바뀜 | server.toml 은 그대로, 들인 파일의 `timeout_seconds` 만 | `presets_changed == ["gate"]`, `presets_changed_keys == {"gate": ["timeout_seconds"]}` |
| C10 | `--dry-run` | 들인 파일 수정 뒤 `reload_config(dry_run=True)` | `app.config` 가 같은 객체(`is`), 워커 · janitor 설정도 그대로, 세대(generation) 안 오름, 응답에 바뀔 키 이름 |
| C11 | `--dry-run` 인데 `[server]` 가 바뀜 | `lanes = 3` | 409, 진짜 reload 와 **같은 문장**, 아무것도 안 바뀜 |
| C12 | `--dry-run` 응답에 값이 없다 | `env = {SECRET = "hunter2-…"}` · argv 에 표지 문자열 | 응답 JSON 어디에도 그 값이 없다. `env` · `argv` 라는 **키 이름**만 |
| C13 | 들인 프리셋의 `checks` 가 server.toml 의 프리셋을 가리킴 · 그 반대 | git_ref 점검 프리셋을 한쪽에 | 둘 다 통과(합친 목록 위에서 검증). 없는 이름을 가리키면 여전히 `ConfigError` |
| C14 | `rcm check` | server.toml 2 · 들인 파일 22 | `ok  local presets  24 (server.toml 2 · presets.dolomood.toml 22)` |
| C15 | `include` 에 server.toml 자신 · 같은 파일 두 번 | `include = ["server.toml"]` · `["a.toml", "./a.toml"]` | `ConfigError` — 자기 자신 / 중복 경로 |

## 코드에서 찾은 경우 (추가)

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| C16 | `include` 를 `[server]` 아래에 씀 | TOML 에서는 `[server] include` 가 된다 | `ConfigError` 가 「파일 맨 위, 첫 `[section]` 앞에」라고 안내 |
| C17 | `include` 원소가 빈 문자열 · 숫자 | `include = [""]` · `[1]` | `ConfigError` |
| C18 | 들인 파일에 프리셋이 없다 | 빈 파일 | 통과, `rcm check` 에 `name 0` |
| C19 | 심볼릭 링크로 자기 자신 | `self.toml -> server.toml` | 자기 자신으로 거절(realpath 비교) |
| C20 | 들인 파일의 프리셋이 깨짐 | `argv = []` | `ConfigError` 메시지에 그 파일 경로와 프리셋 이름 |
| C21 | 출처는 동등성에 안 끼어든다 | 같은 정의의 프리셋을 server.toml → 들인 파일로 옮기고 reload | `presets_changed` 비었음(`Preset` 의 `==` 는 출처를 모른다) |
| C22 | 옛 서버에 `--dry-run` | health 에 `reload_dry_run` 이 없다 | CLI 가 **보내지 않고** 거절(exit 2) — 옛 서버는 본문을 무시하고 진짜 reload 를 해 버린다 |
| C23 | `dry_run` 이 bool 이 아님 | `{"dry_run": "yes"}` | 400, 아무것도 안 바뀜 |
| C24 | dry-run 도 admin 만 | 토큰 없음 · 일반 토큰 | 401 · 403 |
| C25 | 아무것도 안 바뀐 dry-run | 그대로 | `nothing would change` |
| C26 | 진짜 reload 응답의 새 키 | include 하나 | `includes: [경로]`, `presets_changed_keys` |
| C27 | `rcm reload --dry-run` 글자 | 들인 파일 수정 | `would reload <path> (+ presets.dolomood.toml)` · `presets changed: gate (timeout_seconds)` · `nothing applied (--dry-run)` |
| C28 | `rcm reload --dry-run --json` | 같음 | `dry_run: true`, `presets_changed_keys` |
| C29 | `rcm check` 에서 들인 파일이 깨짐 | 없는 경로 | `FAIL  server config` 에 그 경로 |
| C30 | 같은 basename 의 두 파일이 겹침 | `a/p.toml` · `b/p.toml` | 메시지가 basename 이 아니라 경로로 둘을 가른다 |

## 변이(mutcheck)

| 이름 | 변이 | 잡는 시험 |
|---|---|---|
| `include-permission-check-removed` | 권한 검사 `mode & 0o022` 를 0 으로 | C6 |
| `include-allows-repos` | 들인 파일의 허용 구역에 `repos` 추가 | C3 |
| `reload-dry-run-assigns` | dry-run 분기의 `return` 을 지나 대입까지 감 | C10 |
