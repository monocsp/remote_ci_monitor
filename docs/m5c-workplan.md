# M5c 작업 명세 — 내부망 자동 발견 (mDNS/DNS-SD, 표준 라이브러리만)

> 실배치 요청(2026-09-08, 오너): 「같은 로컬인데 VPN 없이, 서버 주소를 몰라도 되게」. 노트북이 Mac mini 와 같은
> Wi-Fi 에 있으면 `rcm run gate` 가 서버를 스스로 찾는다. 밖(다른 네트워크)은 범위 밖 — Tailscale.
>
> 바꾸지 않는 것: 런타임 의존성 0(외부 도구 `dns-sd`·`avahi` 에도 기대지 않는다 — 응답기와 질의기를 직접 든다) ·
> 쓰기는 토큰 · 스키마 v1 · 발견은 **주소를 찾는 것까지**만(토큰은 여전히 머신마다).

## 1. 모델

- 서비스 이름 `_rcm._tcp.local.` (DNS-SD). 인스턴스 `<name>._rcm._tcp.local.` — `name` = `[server] advertise_name`(기본 = 짧은 호스트명).
- 서버가 답하는 레코드: `PTR _rcm._tcp.local → <inst>` · `SRV <inst> → port, target <host>.local.` · `TXT <inst> → ["v=<version>", "name=<name>", "lanes=<n>"]` · `A <host>.local. → 서버의 LAN IPv4 들`(클라이언트가 `.local` 해석기 없이도 IP 로 바로 붙게).
- 클라이언트는 **IP 를 쓴다**(`http://<A 레코드>:<port>`), `.local` 이름은 표시용.

## 2. 서버 (`mdns.py` — 응답기 스레드)

- `[server] advertise = true|false`(기본: `bind` 가 루프백이 아니면 true). `advertise_name`(이름 규칙).
- UDP 소켓 `0.0.0.0:5353`, `SO_REUSEADDR`(+`SO_REUSEPORT` 있으면) 로 macOS 의 mDNSResponder·avahi 와 **공존**(둘 다 5353 을 같이 듣는다 — 표준 동작). 그룹 `224.0.0.251` 가입. IPv4 만(v1).
- 받은 패킷이 `QR=0` 이고 질문에 `PTR _rcm._tcp.local`(또는 `ANY`, 또는 우리 인스턴스/호스트 이름의 SRV/TXT/A)이 있으면 **멀티캐스트로** 답한다(응답 4개 레코드, TTL 120/4500). 그 외는 무시. 초당 응답 상한 20(폭주 방지).
- 시작 시 announce 2회(1초 간격), 종료 시 goodbye(TTL 0) 1회.
- 실패(소켓 못 열음 · 멀티캐스트 불가)는 로그 한 줄 + `server.last_error` 에 남기지 않는다(발견은 부가 기능). `rcm check` 서버 행에 `advertise: on|off(사유)`.
- 보안: 응답에는 이름·포트·버전·LAN IP 만. 토큰·경로·프리셋 없음. 질의 출처를 믿지 않는다(패킷 파싱은 길이 검사 · 압축 포인터 루프 방지 · 최대 512 바이트).

## 3. 클라이언트 (`mdns.py` — 질의기 · `client.py` · `cli.py`)

- `discover(timeout=1.5) -> list[Found]`(`name, host, port, ips, version`): UDP 소켓(임의 포트, TTL 255)으로 `PTR _rcm._tcp.local` 질의를 멀티캐스트하고 timeout 동안 응답을 모은다(같은 인스턴스 중복 제거). 응답의 SRV/TXT/A 를 쓰고, A 가 없으면 `socket.getaddrinfo("<host>.local")` 시도.
- 서버 결정 순서(`load_client_config` 뒤, `cli._client`): `--server` > `RCM_SERVER` > `client.toml server` > **발견**(정확히 1개면 그것, 여러 개면 이름을 나열하고 `--server` 요구, 0개면 오늘의 `no server configured` 안내에 「같은 네트워크에 rcm 서버가 없거나 advertise 가 꺼져 있다」 한 줄).
- 발견으로 정한 서버는 stderr 한 줄 `server: found macmini (192.168.0.10:8787) on this network`. 매 실행 1.5초 비용 — `client.toml` 에 `server` 를 적으면 0.
- `rcm discover [--json] [--timeout N]`: 표 `name  address  version  lanes`. `rcm check`: 서버가 발견으로 정해졌으면 그렇게 표시.
- `client.toml` 에 `server = "auto"` 도 허용(명시적으로 발견을 원할 때).

## 4. 문서 · 예시

- README/README.ko 「Session machine」: 같은 네트워크면 `client.toml` 의 `server` 를 비워도 된다(자동 발견) · 다른 네트워크는 Tailscale. 「Build machine」: `bind = "0.0.0.0"` + `advertise`. 「Security notes」: 발견 응답에는 비밀이 없고, 읽기 API 가 내부망에 열린다는 것(`read_auth = basic` 안내).
- `examples/server.toml`(=템플릿): `advertise = true` 주석.
- CHANGELOG [Unreleased]. dolomood `scripts/remote_ci.sh`: 기본 서버를 「발견 → Tailscale 주소 → 안내」로.

## 5. 테스트 배치

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_mdns_packets.py` | A | 순수 함수: 질의 인코딩 · 응답 인코딩(PTR/SRV/TXT/A, 이름 압축 없이) · 디코딩(압축 포인터 · 루프 · 잘림 · 512 초과 거부) · `should_answer(question)` 규칙 · 응답 상한 |
| `tests/test_mdns_loopback.py` | A | 응답기와 질의기를 **루프백 멀티캐스트**(`IP_MULTICAST_LOOP=1`, 소켓 `127.0.0.1` 바인드 가능하면)로 실제 왕복. 멀티캐스트가 안 되는 CI 면 skip(이유 문구). 서버 두 개 이름이 다르면 둘 다 나온다 · goodbye 뒤 사라짐은 v1 범위 밖 |
| `tests/test_cli_discover.py` · `tests/test_config.py`(추가) | B | `rcm discover` 표/JSON(가짜 `discover`) · `_client` 결정 순서(플래그 > env > toml > 발견 · 여러 개면 exit 2 문구 · 0개 문구) · `advertise`/`advertise_name` 검증 · `server = "auto"` |
| `tests/test_docs_m5c.py` | B | README 두 언어의 문구 · examples/server.toml 키 |

규칙: `src/` 금지 · 실제 LAN 없음 · 각자 `docs/m5c-test-scenarios-<담당>.md`.

## 6. 순서 · 완료 기준 · 결정

- **PR 순서**: M5c-1 `core/mdns.py`(패킷 인코딩·디코딩 순수 함수 + 규칙, 테스트-퍼스트 A) → M5c-2 응답기·질의기·설정·`rcm discover`·`_client` 결정 순서(테스트-퍼스트 B) → M5c-3 문서·예시·dolomood 래퍼 기본값. 각 PR 은 테스트-퍼스트 → 구현 → 격리 검증(실제 LAN 왕복은 이 Mac 의 두 프로세스로: 서버 `bind = 0.0.0.0` + 새 HOME 의 클라이언트가 `server` 없이 `rcm check` 로 찾는다).
- **완료 기준**: ① 같은 Wi-Fi 의 노트북(새 HOME, `client.toml` 에 토큰만)에서 `rcm check` 가 서버를 찾아 `ok server … (found on this network)` 를 찍고 `rcm run demo` 가 돈다 ② `rcm discover` 가 서버 이름·주소·버전을 1.5초 안에 보여 준다 ③ `advertise = false` 면 아무것도 응답하지 않는다(패킷 캡처 대신 질의기 테스트로) ④ mDNSResponder 가 켜진 macOS 에서 5353 공존이 깨지지 않는다(`dns-sd -B _rcm._tcp` 로도 보인다 — 수동 확인) ⑤ 기존 테스트 전부 초록 · 스키마 v1 그대로.
- **오너 결정(제안값으로 구현, 확인 대기)**: 33 `rcm init server` 템플릿 기본 `bind` 를 `127.0.0.1` 로 유지(광고는 bind 가 루프백이 아닐 때만) — "설치하자마자 LAN 에 열리는" 것은 피한다. 34 발견 응답 TXT 에 `lanes` 를 넣는다(비밀 아님). 35 IPv6 는 v1 범위 밖.
- **위험**: 회사망이 멀티캐스트를 막으면 발견이 안 된다 → 그때는 `.local` 이름 직접 입력(`server = "http://macmini.local:8787"`)이 차선이고 안내 문구에 넣는다. 클라이언트 실행마다 1.5초 지연 → `client.toml` 에 주소가 있으면 발견을 건너뛴다.
