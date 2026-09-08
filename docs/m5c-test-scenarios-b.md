# M5c 테스트 시나리오 B — `rcm discover` · `_client` 결정 순서 · `advertise` 설정 · 문서 (2026-09-08)

`docs/m5c-workplan.md` §2(설정 키) · §3(클라이언트 결정 순서 · `rcm discover` · `rcm check`) · §4(문서 · 예시) · §5 표의
B 행 · §6 결정 33–35 를 `tests/test_cli_discover.py`(함수 18 · 실행 19건) · `tests/test_config.py` 끝의 M5c 블록(함수 13 ·
실행 27건) · `tests/test_docs_m5c.py`(함수 9 · 실행 18건)로 옮긴 것이다(test-first, 역할 B). `src/` · 기존 테스트는 건드리지
않았다. mDNS 패킷 규칙과 응답기·질의기의 실제 왕복은 A 의 몫(`tests/test_mdns_packets.py` · `tests/test_mdns_loopback.py`).

공통(cli): test_cli_m4 처럼 `main(argv)` 를 in-process 로 부른다. 실제 LAN 은 없다 — `remote_ci_monitor.cli.discover` 를
가짜(`FakeDiscover`: 호출 `(args, kwargs)` 기록 + 정해진 `Found` 목록)로 monkeypatch 한다. 그래서 cli 는 `discover` 를
**모듈 수준 이름**으로 import 해야 한다(`from remote_ci_monitor.mdns import discover`). `Found` 는 테스트 안의 대역
(`core.mdns.Found` 와 필드 `name, host, port, ips, version, lanes` 와 `address` 프로퍼티가 같다) — cli 가 속성 접근만 쓴다고
보고 실제 타입에 묶지 않았다(A 의 파일 상태와 무관하게 수집된다). 서버가 필요한 시나리오는 `test_server.Server`(진짜
HTTP · 워커 없음)를 127.0.0.1 에 띄우고 발견 결과의 `ips=("127.0.0.1",)` · `port=srv.port` 로 가리킨다. HOME 은 tmp,
`XDG_CONFIG_HOME` · `RCM_*` 는 지운다. `client.toml` 은 `<HOME>/.config/rcm/client.toml` 에 쓴다(로더의 기본 경로).

도우미: `run(capsys, argv)` → `(code, stdout, stderr)` · `found(name, ip, port, *, version, lanes, ips)` · `fake(*found)`
→ 기록기(`.calls` · `.called` · `.timeout_of()`) · `found_lines(err)`(`server: found …` 줄들) · `row(out, name)`
→ `rcm check` 행의 `(status, detail)` · `client_toml(home, text)`.

## 잠그는 문자열 · 시그니처

| 무엇 | 정확히 이것 |
|---|---|
| `rcm discover` 표 헤더 | `name  address  version  lanes` — 공백으로 나누면 네 단어, 이 순서 |
| 표 행 | `<name> http://<첫 ip>:<port> <version> <lanes>` — `lanes` 가 None 이면 `—` |
| `rcm discover --json` | 객체 목록, 키 집합이 정확히 `{name, host, port, ips, address, version, lanes}` · `ips` 는 배열 · `lanes` 없으면 `null` · `address = http://<첫 ip>:<port>` |
| `rcm discover` 0개 | stdout 비움 · 종료 1 · stderr 에 `no rcm server found on this network (is the server's advertise on? same Wi-Fi?)` |
| `--timeout N` | `discover(timeout=N)` (키워드 또는 첫 위치 인자 — 둘 다 받는다). 안 주면 안 넘기거나 1.5 |
| `rcm discover` 의 전제 | client.toml · 토큰 없어도 된다. `RCM_SERVER` 가 있어도 언제나 찾는다 |
| `_client` 1개 발견 | 그 서버 · stderr 한 줄 `server: found <name> (<ip>:<port>) on this network` (`ip` 는 첫 A 레코드) |
| `_client` 여러 개 | 종료 2 · stdout 비움 · stderr 에 이름 전부 · `--server` · `client.toml` |
| `_client` 0개 | 종료 2 · stderr 에 오늘의 `no server configured (use --server, RCM_SERVER or client.toml)` **와** 위 `no rcm server found …` 문장 |
| 결정 순서 | `--server` > `RCM_SERVER` > `client.toml server`(비어 있지 않고 `"auto"` 가 아닌 것) > 발견. 서버가 정해졌으면 `discover` 를 **부르지 않는다**(기록기 `.called == False`) |
| `server = "auto"` | 빈 것과 같다(발견). `RCM_SERVER` · `--server` 가 여전히 이긴다 |
| `rcm check` 발견 | `server` 행 ok · 상세가 `http://<ip>:<port>` 로 시작하고 `(found on this network)` 로 끝난다 · `discover` 는 한 번만 |
| `rcm check` 여러 개 · 0개 | **`server` 행** FAIL · 종료 1(오늘의 `("server", False, NO_SERVER_HINT)` 자리). 여러 개면 이름들이 stdout+stderr 어딘가에, 0개면 상세에 `no server configured …` 와 출력 어딘가에 `advertise` |
| 설정 | `ServerSection.advertise: bool \| None = None`(None = 자동) · `ServerSection.advertise_name: str = ""`(빈 값 = 짧은 호스트명) · `config.advertise_enabled(section) -> bool` · `ClientConfig.wants_discovery`(`""` · `"auto"` 에서 True) · `load_client_config` 는 `"auto"` 를 **그대로** 둔다 |
| `advertise_enabled` | `advertise=None`: bind `127.0.0.1` · `localhost` → False, `0.0.0.0` · `192.168.0.10` · `100.64.0.1` → True. `True` → 항상 True, `False` → 항상 False(bind 무관) |
| 설정 오류 | `[server] advertise`(불리언 아님 · env 값 `maybe`) · `[server] advertise_name`(`_NAME_RE` 위반 · 문자열 아님) |

## `tests/test_cli_discover.py`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 두 서버(lanes None · lanes 2, ips 둘) → 헤더 + 두 줄 · 주소는 **첫** ip · `—` | `test_discover_prints_a_table_with_one_row_per_server` | §3 「`rcm discover [--json] [--timeout N]`: 표 `name  address  version  lanes`」 · 결정 34(lanes) |
| 2 | `--json` → 키 집합 · `ips` 배열 · `address` · `lanes` null | `test_discover_json_lists_objects_with_the_locked_keys` | §3 · §1 「클라이언트는 IP 를 쓴다」 |
| 3 | `--timeout 0.3` → `discover(timeout=0.3)` 한 번 | `test_discover_passes_timeout_to_discover` | §3 `discover(timeout=1.5)` |
| 4 | 플래그 없음 → timeout 안 넘기거나 1.5 | `test_discover_default_timeout_is_the_querier_default` | §3 · 「매 실행 1.5초 비용」 |
| 5 | 0개 → stdout 비움 · 종료 1 · stderr 문장 | `test_discover_with_no_server_exits_1_with_the_network_hint` | §3(0개 문구) · §6 위험(advertise 꺼짐 · 다른 네트워크) |
| 6 | client.toml · 토큰 없음 + `RCM_SERVER` 있음 → 그래도 찾는다 | `test_discover_needs_no_config_and_ignores_a_configured_server` | §3(발견은 주소를 찾는 것까지) |
| 7 | `rcm discover --help` 에 `--json` · `--timeout` | `test_discover_help_lists_json_and_timeout` | §3 명령 시그니처 |
| 8 | 설정 없음 · 1개 발견 → `rcm presets` 가 그 서버에 붙는다 · stderr `server: found macmini (127.0.0.1:PORT) on this network` 정확히 한 줄 | `test_the_only_discovered_server_is_used_and_announced_on_stderr` | §3 「정확히 1개면 그것」 · 「stderr 한 줄 `server: found …`」 |
| 9 | `--server` 진짜 + `RCM_SERVER` 가짜 + toml 가짜 → `rcm check` server 행이 플래그 URL · `discover` 안 불림 · `found` 표기 없음 | `test_server_flag_wins_and_skips_discovery` | §3 결정 순서 · 「`client.toml` 에 `server` 를 적으면 0」 |
| 10 | `RCM_SERVER` 진짜 + toml 가짜 → env URL · `discover` 안 불림 | `test_env_wins_over_client_toml_and_skips_discovery` | §3 결정 순서 |
| 11 | toml 진짜 → toml URL · `discover` 안 불림 | `test_client_toml_server_skips_discovery` | §3 결정 순서 · §6 위험(발견 건너뛰기) |
| 12 | toml `server = "auto"` → 발견 · `rcm check` server 행 `… (found on this network)` | `test_client_toml_auto_forces_discovery` | §3 「`server = "auto"` 도 허용」 |
| 13 | toml `"auto"` + (`RCM_SERVER` \| `--server`) 진짜 → `discover` 안 불림 · `found` 줄 없음 | `test_env_and_flag_override_auto[env · flag]` | §3 결정 순서(auto 는 빈 것과 같다) |
| 14 | 2개 발견 → 종료 2 · stdout 비움 · 이름 둘 · `--server` · `client.toml` | `test_several_discovered_servers_is_a_usage_error_listing_their_names` | §3 「여러 개면 이름을 나열하고 `--server` 요구」 |
| 15 | 0개 발견 → 종료 2 · `no server configured …` **와** `no rcm server found …` | `test_no_discovered_server_keeps_the_no_server_hint_and_adds_the_network_sentence` | §3 「0개면 오늘의 안내에 한 줄」 |
| 16 | `rcm check` 1개 발견 → server 행 ok · 상세 끝 `(found on this network)` · token 행 그대로 · `discover` 한 번 | `test_check_shows_the_discovered_server_as_found_on_this_network` | §3 「`rcm check`: 발견으로 정해졌으면 그렇게 표시」 · §6 완료 기준 ① |
| 17 | `rcm check` 2개 발견 → server 행 FAIL · 종료 1 · 이름 둘 | `test_check_with_several_discovered_servers_fails_the_server_row_and_names_them` | §3(여러 개) · M4 §2 의 `rcm check` 표(행 이름) |
| 18 | `rcm check` 0개 → server 행 FAIL · 상세 `no server configured …` · `advertise` 언급 | `test_check_with_no_discovered_server_keeps_the_no_server_row` | §3(0개) · 오늘의 `("server", False, NO_SERVER_HINT)` |

## `tests/test_config.py` — M5c 블록(파일 끝)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 기본값 `advertise is None` · `advertise_name == ""` (dataclass · 파일에 키 없음) | `test_advertise_keys_have_defaults` | §2 「기본: bind 가 루프백이 아니면 true」 · 「advertise_name(기본 = 짧은 호스트명)」 |
| 2 | 파일 `advertise = true/false` | `test_advertise_from_file[true · false]` | §2 |
| 3 | 파일 `advertise_name = "macmini"` | `test_advertise_name_from_file` | §1 「`name` = `[server] advertise_name`」 |
| 4 | env `RCM_SERVER_ADVERTISE` · `RCM_SERVER_ADVERTISE_NAME` · 잘못된 값은 `[server] advertise` | `test_advertise_keys_from_env` | PLAN 「설정」 우선순위(env) |
| 5 | `advertise = 1` · `[true]` → `[server] advertise` | `test_advertise_must_be_a_boolean[int · list]` | §2(bool) |
| 6 | `advertise_name` 이 공백 · `-` 시작 · `/` · 65자 · 정수 → `[server] advertise_name` | `test_advertise_name_must_be_a_short_identifier[6]` | §2 「이름 규칙」(`_NAME_RE`) |
| 7 | `mac-mini.lab` 허용 · `""` 허용 | `test_advertise_name_accepts_dots_dashes_and_empty` | §2 |
| 8 | `advertise_enabled(ServerSection(bind, advertise))` 8 조합 | `test_advertise_enabled[8]` | §2 · 결정 33 |
| 9 | 로드한 설정으로: 기본 → False · `bind = "0.0.0.0"` → True · + `advertise = false` → False | `test_advertise_enabled_follows_the_loaded_bind` | 결정 33 · `rcm init server` 기본 bind 유지 |
| 10 | `examples/server.toml` 에 키가 있으면 검증 통과(필수 아님) | `test_example_server_toml_accepts_the_advertise_keys_if_present` | §4 「`examples/server.toml`: `advertise = true` 주석」 |
| 11 | `server = "auto"` → `cfg.server == "auto"` · `wants_discovery` | `test_client_server_auto_is_kept_and_wants_discovery` | §3 「`server = "auto"` 도 허용」 |
| 12 | `ClientConfig()` · server 줄 없는 파일 → `wants_discovery` True · URL 이면 False · env 로 덮으면 False | `test_client_wants_discovery_when_server_is_empty` | §3 결정 순서 |
| 13 | `"auto"` + `RCM_SERVER` → env · + `server=` 플래그 → 플래그 | `test_client_env_and_flag_override_auto` | §3 결정 순서(플래그 > env > 파일) |

## `tests/test_docs_m5c.py`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | README · README.ko 에 `rcm discover` · `advertise` | `test_readme_mentions[en·ko × 2]` | §4 |
| 2 | 「Session machine」 절의 한 문단에 「같은 네트워크(Wi-Fi/LAN)」 + `server` + 「비워도/empty」 · 절에 `Tailscale` | `test_readme_session_machine_says_server_may_be_left_empty_on_the_same_network[en·ko]` | §4 「같은 네트워크면 `server` 를 비워도 된다 · 다른 네트워크는 Tailscale」 |
| 3 | 「Build machine」 절에 `advertise` | `test_readme_build_machine_mentions_advertise[en·ko]` | §4 「`bind = "0.0.0.0"` + `advertise`」 |
| 4 | 「Security notes」 의 발견/advertise 문단에 token/secret(토큰/비밀) | `test_readme_security_notes_say_the_discovery_response_carries_no_secret[en·ko]` | §4 · §2 보안 「응답에는 이름·포트·버전·LAN IP 만」 |
| 5 | 「Session commands」 표에 `rcm discover` 행(`--json` · `--timeout`) | `test_readme_session_commands_table_has_a_discover_row[en·ko]` | §3 명령 시그니처 |
| 6 | `examples/server.toml` 에 `advertise = true|false` 줄(주석 허용) | `test_example_server_toml_shows_advertise` | §4 · test_packaging 이 템플릿과 바이트 동일을 잠근다 |
| 7 | 예시가 여전히 로드된다 | `test_example_server_toml_still_loads` | 회귀 |
| 8 | CHANGELOG `[Unreleased]` 에 `rcm discover` · `advertise` · discover | `test_changelog_unreleased_mentions_discovery[3]` | §4 |
| 9 | 네 문서 파일이 있다 | `test_docs_paths_exist` | — |

## 열어 둔 것(명세에 없어 잠그지 않음)

- `rcm discover --json` 이 0개일 때 `[]` 를 찍는지와 그때의 종료 코드(표 모드만 1 로 잠갔다).
- 표의 정렬 — 테스트는 이름순으로 준 목록을 그 순서로 기대한다(`discover` 가 준 순서든 이름순이든 통과).
- `_client` 가 발견에 쓰는 timeout(1.5 인지) · `Found.ips` 가 비었을 때의 주소(`<host>.local`).
- `rcm check --config` 서버 쪽 행의 `advertise: on|off(사유)`(§2) — 응답기 상태는 서버 몫.
- `RCM_SERVER=auto` · `--server auto` 가 발견을 강제하는지(`wants_discovery` 는 값만 본다).
- `advertise = "yes"`(문자열) — 다른 bool 키처럼 받아들일지(`_coerce_scalar` 의 오늘 동작).
- `examples/client.toml` 의 `server = "auto"` 안내 주석 · 팀 래퍼 스크립트의 기본값(§4, 이 저장소 밖).

## 실행

`python -m pytest -p no:cacheprovider tests/test_cli_discover.py tests/test_docs_m5c.py tests/test_config.py`

- **구현 전(HEAD 스냅샷)** — `git archive HEAD` 를 스크래치에 풀고 세 파일을 얹어 실행: cli 19/19 빨강, 이유는 전부
  `AttributeError: module 'remote_ci_monitor.cli' has no attribute 'discover'`(픽스처 `fake` 의 monkeypatch, 18건) 와 `--help`
  1건의 argparse 오류(종료 2). config 블록 27/27 빨강 — `ImportError: advertise_enabled` · `AttributeError:
  'ServerSection' … 'advertise'` · `'ClientConfig' … 'wants_discovery'` · `[server] unknown key 'advertise'`. docs 18 중 16
  빨강 — 문서 문구 없음(초록 2건은 회귀 보호 #7 과 파일 존재 #9). 픽스처 오류는 없다.
- **작업 트리(같은 시각에 구현이 들어오고 있었다)** — cli 17/19 초록. 빨강 2건(#17 · #18)은 `rcm check` 가 여러 개/0개
  발견을 `client config` 행에 적는 것 — 잠근 모양은 오늘의 `no server configured` 자리인 **`server` 행**이다.
  config 27/27 초록(파일 전체 153건 초록 — 기존 블록 그대로). docs 15/18 초록 — 빨강은 「Build machine」 절의 `advertise`(en · ko) 와 ko 「Security notes」 의
  발견/비밀 문단.
