# M5n 작업 명세 — 잡을 해낼 수 있는 머신이 가져간다

> 2026-11-16 오너 요청: 「이 저장소를 쓰면 같은 내부망의 다른 컴퓨터를 찾아서, 그 컴퓨터에서 Flutter·Dart
> 빌드와 여러 테스트를 돌릴 수 있게 rcm 을 완성하고 싶다.」 같은 날 Codex 와 두 번 논의한 결과(합의 ·
> Codex 가 고친 것 · 이 문서가 더한 것)와 같은 날 운영에서 터진 Xcode 사고(§1.3)를 묶었다.
> 결정 번호는 **88~104**. 전부 추천값으로 구현하고 오너 확인을 기다린다(⛔ 없음).
>
> 이 문서는 인터페이스를 먼저 고정한다. 테스트 시나리오는 `docs/m5n-test-scenarios-{a,b,c}.md`,
> 구현 중 바뀐 것은 이 문서의 §19 「구현하며 바뀐 것」에 남긴다.

## 0. 한 줄

**작업이 「무엇이 필요한가」를 말하면, 그것을 해낼 수 있다고 스스로 증명한 머신 중 비는 곳이 가져간다.**
머신은 자기 환경을 재서 보고하고(도구 · 버전 지문 · 점검 결과), 환경이 바뀌면 다시 증명하기 전까지
그 일을 받지 않는다. 증명하지 못한 머신에 「아마 되겠지」로 보내지 않는다(fail-closed).

## 1. 실측 — 왜 지금 이것인가

### 1.1 운영 인스턴스(v0.4.4, 2026-11-16, `GET /api/status` 읽기 전용)

| 사실 | 값 |
|---|---|
| 서버 | `epeuem-ui-Macmini`, 로컬 레인 2 |
| 원격 워커 | `mac2-worker` (풀 `mac2`, 레인 1) |
| 프리셋 | 24개 중 23개가 `pool = "default"`, `mac2` 풀에는 `remote-demo` 하나 |
| `requires` | 다수가 `["fvm", …]` — 도구를 요구한다 |

**`mac2-worker` 는 다른 컴퓨터가 아니다.** 같은 Mac mini 위에서 두 번째 LaunchAgent
(`com.remote-ci-monitor.worker`, data `~/.local/share/rcm-worker`)로 도는 워커 프로세스다. 물리
머신은 한 대뿐이다. 그래서 이 명세는 두 개념을 가른다 — **워커**(잡을 집는 프로세스: 서버 자신의
로컬 레인 묶음 하나 + 원격 워커마다 하나)와 **호스트**(물리 컴퓨터). 같은 호스트 위의 워커 둘은
시뮬레이터 · 디스크 · CPU 를 나눠 쓴다(§6 그룹 범위, §7 추정).

### 1.2 오늘 코드가 하는 것과 안 하는 것

| 질문 | 오늘 | 근거 |
|---|---|---|
| 잡이 어느 머신에서 도나 | **제출할 때 풀 하나에 묶이고**, 그 풀의 레인만 집는다 | `store.claim` 의 `WHERE state=? AND pool=?` (`store.py:2179`) · `_requested_pool` (`server.py:3582`) |
| 머신을 붙이면 | 프리셋마다 `pool`/`pools` 를 사람이 고쳐야 그 머신으로 간다 | `config.py:697` |
| 워커가 자기 능력을 알리나 | 안 알린다. 등록은 `pool · lanes · host_name · version` 뿐 | `remote_workers.worker_register` |
| 도구 검사 | `requires` 는 **있는지만** 본다. 그것도 잡을 **집은 뒤** 작업 공간에서 | `runner.missing_tools` (`runner.py:130`) |
| 서버가 잡 환경을 재나 | 로컬만, 기동·reload 때 한 번(M5m `measure_preset_env`) | `server.py:4283` |
| 동시 실행 그룹 | **풀 안에서만** 배제한다 | `store.claim` docstring · `core/queue.py:387` |
| 추정 표본 | 풀별 key 중앙값. **단계 중앙값은 key 전역**(풀도 안 가른다) | `server._load_medians` · `_read_step_medians` · `_queue_rows` 주석 |
| 워커가 서버를 찾나 | 못 찾는다. `server` 가 필수 | `config.py:1312` |

### 1.3 Xcode 사고 (2026-11-06 ~ 11-16, 운영)

1. 11-06 16:54 Mac mini 의 `/Applications/Xcode.app` 이 26.5 → 27.2 로 다시 깔렸다(스토어 자동 업데이트로 보임). **rcm 은 아무것도 몰랐다.**
2. Xcode 27 은 SimulatorKit 을 `Contents/SharedFrameworks` 로 옮겼고, 깔려 있던 idb_companion 1.1.8 은 옛 경로만 찾아 `idb ui tap` 이 즉시 실패했다.
3. 열흘 뒤 iOS scenario-qa #3631 이 알림 권한 창 «허용» 을 못 눌러 막혔다. 프로젝트 하네스가 `TAP_ALLOW=hit` 를 거짓으로 찍어 원인이 가려졌다(그쪽 레포의 일).
4. 고침: idb-companion 1.7.8 · fb-idb 1.7.7 · Command Line Tools 27.2(sudo, 사람이 직접). 재실행 #3643.

rcm 쪽의 빈틈 셋:

- **환경이 바뀐 날을 모른다.** #3631 이 빨개졌을 때 코드 탓인지 머신 탓인지 가를 근거가 없었다.
- **`requires` 는 「있음」만 본다.** idb 는 있었으니 통과했다. 깨진 도구가 「된다」로 보였다.
- **배치를 자동으로 만들면 더 위험해진다.** 깨진 머신을 「능력 있음」으로 믿고 계속 보낸다.

## 2. 사용 시나리오 (이 마일스톤이 끝나면)

### S1 — 두 번째 컴퓨터를 붙인다 (예: 사무실 MacBook Pro)

```sh
# 빌드 머신(서버)
rcm token add mbp --worker            # 비밀은 한 번만. 다음 줄에 워커 쪽에서 칠 명령을 그대로 찍어 준다
# 새 컴퓨터
pipx install …remote_ci_monitor       # 서버와 같은 릴리스
rcm init worker                       # ~/.config/rcm/worker.toml — server = "auto"(같은 망이면 mDNS)
export RCM_WORKER_TOKEN=…
rcm worker --check                    # server · token · 도구 지문 · 프리셋 n/24 가능
rcm init worker --service-file ~/Library/LaunchAgents/com.remote-ci-monitor.worker.plist
```

등록되는 순간 워커는 자기 환경을 재서 보고한다. 서버의 `rcm machines` 는:

```
machine        host            pool     lanes  fingerprint                       presets
Mac mini       epeuem-…(a1f3)  default  2      xcode 27.2 · flutter(fvm) 3.47.8  21/24 ok · 3 need sim-check
mac2-worker    epeuem-…(a1f3)  mac2     1      xcode 27.2 · flutter(fvm) 3.47.8  21/24 ok
mbp            mbp(7c20)       mbp      1      xcode 26.5 · fvm missing          4/24 ok · 18 fvm missing
```

### S2 — Flutter 게이트를 어느 머신에서든 돌린다

```toml
[[presets]]
name = "gate"
placement = "any-capable"          # 풀 대신: 해낼 수 있는 머신 아무 데나
requires = ["fvm", "gitleaks"]
concurrency_group = "gate"
concurrency_scope = "host"         # 같은 컴퓨터에서만 하나씩 — 다른 컴퓨터와는 동시에
```

`rcm run gate` → Mac mini 두 칸이 바쁘고 `mbp` 가 fvm 을 깔고 다시 보고했으면 `mbp` 가 집는다.
fvm 이 없으면 `mbp` 는 이 잡을 **보지도 않는다**.

### S3 — 환경이 바뀐다 (Xcode 사고의 재연)

```toml
[[probes]]
name = "xcode"
argv = ["xcodebuild", "-version"]
[[probes]]
name = "idb"
argv = ["idb_companion", "--version"]

[[presets]]
name = "sim-check"                 # 프로젝트가 만든 점검: 시뮬레이터 부팅 + 탭 한 번
source_modes = ["git_ref"]
repo = "app"
argv = ["bash", "scripts/qa/sim_check.sh"]

[[presets]]
name = "scenario-qa"
checks = ["sim-check"]             # 이 머신에서 sim-check 가 「지금 환경으로」 통과해야 받는다
```

11-06 에 Mac mini 의 지문이 `xcode 26.5 → 27.2` 로 바뀐다 → 그 머신의 `scenario-qa` 능력이 `unknown`
(`check_needed`)으로 내려가고 서버가 `sim-check` 를 **그 머신을 지정해** 한 번 건다 → 탭 실패로
빨갛게 끝난다 → `scenario-qa: check_failed (sim-check #3620 · xcode 26.5 → 27.2)`. 그날부터 웹의
「확인이 필요한 작업」과 `rcm check` 에 그 줄이 뜨고, iOS QA 는 다른 맞는 머신으로 가거나 `—` 로
기다린다. 열흘 동안 조용히 깨져 있지 않는다. 사람이 idb 를 고치고 `rcm recheck mac-mini sim-check`
(또는 웹의 **다시 점검**)를 누르면 초록으로 돌아온다.

### S4 — 맞는 머신이 하나도 없다

`rcm run gate` 가 제출 직후 한 줄을 찍는다: `no machine can run gate now — mbp: fvm missing ·
Mac mini: busy` 가 아니라, 바쁜 것은 정상이므로 **바쁜 머신이 있으면 평소처럼 기다린다**. 모든 후보가
불능이면 `gate: no capable machine — mbp: fvm missing · mac2-worker: down` 을 찍고 대기열에 선다
(나중에 머신이 고쳐지면 그때 돈다). 대기 행의 이유는 `no_capable_machine`, ETA 는 `—`.

## 3. 용어 (결정 88)

| 말 | 뜻 | 식별자 |
|---|---|---|
| **워커** | 잡을 집는 주체. 서버의 로컬 레인 묶음이 하나, 원격 워커 프로세스마다 하나 | 로컬은 `@local`, 원격은 워커 토큰 이름(`_NAME_RE` 는 `@` 를 허용하지 않아 충돌이 없다) |
| **호스트** | 물리 컴퓨터. 같은 호스트의 워커들은 자원과 속도를 공유한다 | `host_key` = 하드웨어 UUID 의 sha256 앞 16자(macOS `ioreg -rd1 -c IOPlatformExpertDevice` 의 `IOPlatformUUID`, Linux `/etc/machine-id`). 못 읽으면 `worker:<워커 id>` |
| **지문** | 워커가 자기 환경에서 돌린 `[[probes]]` 출력의 정규화 묶음과 그 해시 | `fingerprint_hash` = sha256(정렬된 JSON) 앞 16자 |
| **능력 판정** | (워커, 프리셋) → `eligible` · `ineligible` · `unknown` + 사유 코드 | §4.3 |
| **점검 프리셋** | 다른 프리셋의 `checks` 에 이름이 오른 프리셋 | §4.4 |

화면에 나가는 이름은 워커 id 가 아니라 `display_name`(로컬은 서버의 호스트명, 원격은 워커가 보낸
`host_name` 또는 토큰 이름)이다. UUID 원문은 어디에도 싣지 않는다 — 해시만.

## 4. 능력 — 재고, 보고하고, 증명한다

### 4.1 환경 지문 `[[probes]]` (결정 89·90)

```toml
[[probes]]
name = "xcode"                       # 이름: 소문자·숫자·-·_ , 32자
argv = ["xcodebuild", "-version"]    # 셸 없음. 각 워커가 **자기 서비스 환경**(잡과 같은 PATH 규칙)에서
timeout_seconds = 10                 # 기본 10, 상한 60
```

- 설정 검증: 이름 중복 · 빈 argv · 64개 초과는 기동 실패(키 이름과 함께).
- **정규화**(순수 `core/capability.py: normalize_probe_output`): stdout+stderr 를 합쳐 비어 있지 않은 줄
  앞 2개를 ` · ` 로 잇고, 제어문자를 지우고, 120자에서 자른다. 결과는 하나의 상태를 가진다:
  `ok`(exit 0) · `error`(exit ≠ 0 → 값은 `exit N`) · `missing`(실행 파일 없음) · `timeout`.
- **지문 해시에 들어가는 것**: `ok`·`error`·`missing` 의 (이름, 상태, 값). **`timeout` 은 들어가지
  않는다** — 그 회차엔 앞 값을 그대로 쓰고 `stale: true` 로 표시한다(결정 90). 한 번 느린 것만으로
  지문이 출렁여 점검이 다시 도는 것을 막는다. 앞 값도 없으면(첫 회차) 지문은 `unknown` 이다.
- 주기: 워커 등록 직후 · 서버 기동/reload 직후 · `probe_interval_seconds`(기본 600, 하한 60)마다.
- 값은 읽기 라우트(`/api/status`·`/api/machines`)에 실린다. `read_auth = "none"` 이면 같은 망의 누구나
  본다 — **버전 출력만 내는 명령을 고르라**고 설정 문서에 적는다(경로를 찍는 `which` 류 금지 권고).

### 4.2 보고 경로

| 워커 | 누가 재나 | 언제 |
|---|---|---|
| `@local` | 서버 프로세스(`App.measure_capabilities`, M5m `measure_preset_env` 를 확장) | 기동 · reload · 주기 |
| 원격 | 워커 프로세스가 재고 `POST /worker/capabilities` 로 보낸다 | 등록 직후(레인 스레드를 **띄우기 전에**) · 세대가 바뀌면 · 주기 |

**세대(generation)**: 서버가 `"<기동 epoch>:<reload 횟수>"` 를 갖는다. 등록 응답과 heartbeat 응답에
싣고, 워커는 자기가 가진 것과 다르면 `GET /worker/spec` 으로 새 명세를 받아 다시 잰다. 명세는
`{generation, probe_interval_seconds, probes[], presets[{name, requires, env_passthrough, env}]}` —
claim payload 에 이미 실리던 것과 같은 범위라 새로 새는 것이 없다.

보고 본문:

```json
{"generation": "1731740000:3",
 "host_key": "a1f3c9…",
 "accept_any": true,
 "fingerprint": {"xcode": {"state": "ok", "value": "Xcode 27.2 · Build version 27B52"},
                 "idb": {"state": "missing", "value": null}},
 "presets": {"gate": {"missing_tools": [], "workspace_tools": [], "missing_passthrough": []}}}
```

서버는 `generation` 이 현재와 다르면 409(`generation_mismatch`)로 거절하고 워커는 명세를 다시 받는다.
검증: 프리셋·프로브 이름은 현재 설정에 있는 것만, 값 길이 상한, 알 수 없는 키 무시.

### 4.3 능력 판정 (순수 · `core/capability.py: verdict`) (결정 91·92·93)

입력: 프리셋, 그 워커의 최신 보고(없을 수 있음), 현재 세대, 점검 상태(§4.4), now.

판정 순서 — 첫 번째로 걸리는 것이 답이다:

| # | 조건 | 상태 | 코드 |
|---|---|---|---|
| 1 | 보고가 없다 | unknown | `report_missing` |
| 2 | 보고의 세대 ≠ 현재 | unknown | `report_stale` |
| 3 | 보고가 `3 × probe_interval_seconds` 보다 오래됐다 | unknown | `report_stale` |
| 4 | `missing_tools` 가 비어 있지 않다 | ineligible | `tool_missing` (detail: 이름들) |
| 5 | `workspace_tools` 가 비어 있지 않다 — PATH 의 상대 항목에만 있을 수 있는 도구 | unknown | `workspace_tool` |
| 6 | `checks` 중 이 워커·현재 지문으로 실패한 것 | ineligible | `check_failed` (detail: 점검 이름 · 잡 id · 바뀐 지문 항목) |
| 7 | `checks` 중 이 워커·현재 지문으로 돌고 있거나 기다리는 것 | unknown | `check_running` |
| 8 | `checks` 중 이 워커·현재 지문으로 통과한 기록이 없는 것 | unknown | `check_needed` |
| 9 | 지문이 `unknown` 인데 `checks` 가 있다 | unknown | `fingerprint_unknown` |
| — | 그 밖 | eligible | `ok` |

- `missing_passthrough` 는 **판정에 안 들어간다**(정보) — 결정 92. M5m 이 정한 대로 빈 값이 의도일 수
  있다. 화면에는 그대로 보인다.
- 5번(`workspace_tool`): 워커는 PATH 의 **절대 항목**과 절대경로 선언만으로 찾는다. 못 찾았는데 PATH 에
  상대 항목이 있으면 `workspace_tools` 로, 없으면 `missing_tools` 로 보낸다. 작업 공간이 오기 전엔 알 수
  없는 것을 「없다」로도 「있다」로도 부르지 않는다(Codex 지적).
- 판정은 서버가 claim 할 때마다 메모리의 보고로 계산한다. 비싸지 않다(프리셋 수 × 워커 수).

### 4.4 점검 프리셋 `checks` (결정 94·95·96)

```toml
[[presets]]
name = "scenario-qa"
checks = ["sim-check"]       # 이 워커에서 sim-check 가 현재 지문으로 성공한 적이 있어야 받는다
```

- `checks` 의 이름은 설정에 있는 프리셋이어야 하고, **그 프리셋은 `git_ref` 를 받아야 한다**(rcm 이
  스스로 걸 때 쓸 소스가 필요하다 — `check_ref`, 기본 `"HEAD"` = 그 저장소의 기본 브랜치). 점검 프리셋은
  자기 자신이 `checks` 를 가질 수 없다(순환 금지). 어기면 기동 실패.
- **점검 상태는 잡 기록에서 나온다**: 그 워커에서 돈(`worker_name`), 그 점검 프리셋의, 잡 행에 박힌
  `fingerprint_hash` 가 **현재 지문과 같은** 가장 최근 잡. succeeded → 통과, 활성 → `check_running`,
  failed·timed_out → `check_failed`, 없음 · cancelled · lost → `check_needed`.
- **자동 점검**(`[server] auto_checks = true`, 기본 켬): 서버의 능력 스위퍼(janitor 루프와 같은 스레드가
  아니라 새 스레드, 30초 주기)가 `check_needed` 인 (워커, 점검) 쌍마다 **그 워커를 지정한** 점검 잡을
  건다. 요청자 `rcm:check`, 라벨 `rcm (machine check)`, 우선순위 `high`. 같은 (워커, 점검, 지문)에는
  **한 번만** 자동으로 건다 — `machine_checks` 표가 기억한다(결정 95). 그래서 실패하거나 취소·유실돼도
  자동으로 다시 걸지 않는다(무한 루프 금지). 다시 거는 것은 사람이다:
  `rcm recheck <워커> [<점검>]`(admin) · 웹의 **다시 점검** · 또는 지문이 또 바뀔 때.
- 점검이 필요한 워커가 down 이면 걸지 않는다(살아 있는 워커만).
- 점검 잡의 ref 해석(`ls-remote`)은 스위퍼 스레드에서 한다 — claim 경로가 네트워크에 묶이지 않는다.
  해석이 실패하면 그 회차는 건너뛰고 `machines[].checks[].error_code = "ref_unresolved"` 로 보인다.
- 점검 잡도 평범한 잡이다: 큐에 보이고, 로그가 남고, 알림 훅을 탄다. `sample = false`(§7.4)가 기본이다.

### 4.5 머신을 지정한 잡

- 점검 잡은 `target_worker` 를 갖는다. 그 워커의 레인만 집는다(풀 · placement · 능력 판정 무시 —
  점검이 자기를 막으면 영원히 못 돈다). 도구가 없으면 `pool` 배치처럼 집어서 즉시 `tool_missing` 으로
  끝난다 — 그것이 곧 점검 실패다.
- 사람이 직접: `rcm run sim-check --machine mbp`. **점검 프리셋은 토큰만 있으면**, 그 밖의 프리셋은
  **admin 만** `--machine` 을 쓸 수 있다(결정 96). 머신 이름이 없거나 down 이면 400 · 409.

## 5. 배치 — `placement = "any-capable"` (결정 97·98·99)

### 5.1 설정

| 키 | 값 | 뜻 |
|---|---|---|
| `placement` | `"pool"`(기본) · `"any-capable"` | `pool` 은 오늘 그대로. `any-capable` 은 `pool` 을 무시하고 판정이 `eligible` 인 모든 워커가 후보 |
| `pool` | 풀 이름 | `any-capable` 에서는 **대기 중 표시 위치**만 정한다(어느 풀 절에 줄 서 보이나) |
| `pools` | 풀 목록 | 오늘 그대로. `any-capable` 프리셋에 `--pool X` 를 주면 그 잡은 **풀 X 에 고정**된다(사람이 고른 것이 이긴다) |

워커 쪽 거부: `worker.toml` 의 `accept_any = false`(기본 true) 면 그 워커는 `any-capable` 잡을 안 집는다
(장치 실험실처럼 전용으로 둘 머신). 로컬은 `[server] local_accept_any`(기본 true).

### 5.2 claim 규칙 (`store.claim` 확장)

claim 하는 쪽(로컬 레인 · 원격 레인)이 자기 신원을 넘긴다:

```python
@dataclass(frozen=True)
class Claimant:
    worker: str  # "@local" 또는 워커 이름
    pool: str
    host_key: str
    fingerprint_hash: str | None
    capable: tuple[str, ...]  # 이 워커가 지금 eligible 인 any-capable 프리셋 이름들
    gated: tuple[str, ...]  # pool 잡 중 checks 판정으로 막힌 프리셋 이름들
```

후보 잡(대기, 우선순위 내림차순 · id 오름차순 — 오늘과 같은 순서):

```
(target_worker = :worker)                                                       -- 지정 잡
OR (target_worker IS NULL AND placement = 'pool' AND pool = :pool AND preset NOT IN :gated)
OR (target_worker IS NULL AND placement = 'capable' AND preset IN :capable)
```

- **`pool` 배치의 `requires` 불능은 오늘처럼 집어서 즉시 `tool_missing` 으로 끝낸다**(M5j G4 를 안
  바꾼다 — 결정 98). 사람이 그 풀에 묶었으니 조용히 기다리는 것보다 빨리 빨개지는 게 낫다. `checks` 는
  새 기능이라 opt-in 이고, `checks` 가 막으면 pool 잡도 **기다린다**(이유 `check_failed` 등).
- `any-capable` 잡을 집는 순간 같은 UPDATE 에서 `pool` 을 **집은 워커의 풀**로, `host_key` ·
  `fingerprint_hash` · `worker_name` 을 박는다. 대기 중 표시 풀은 `display_pool` 로 따로 남긴다.
  그래서 끝난 잡은 「실제로 돈 곳」으로 기록되고, 풀별 표시·알림·`rcm jobs --pool` 이 맞는다.
- 지정 잡에도 `fingerprint_hash` 를 박는다(점검 판정의 근거).
- 판정 근거(`capable`·`gated`)는 claim 직전에 메모리 보고로 계산한다. SQL 에 이름 목록을 넘긴다.

### 5.3 제출 응답과 `rcm run`

`POST /jobs` 응답에 `placement: {mode, eligible_now[], blocked[{worker, code, detail}]}` 를 더한다.
`rcm run` 은 `eligible_now` 가 비면 stderr 에 한 줄을 찍는다(§2 S4). 잡은 그래도 큐에 선다 — 나중에
고쳐질 수 있다(결정 99). 합류 판정은 오늘 그대로다(같은 프리셋·입력·소스면 합류 — 배치는 프리셋에
속하므로 같다).

## 6. 동시 실행 그룹의 범위 `concurrency_scope` (결정 100)

| 값 | 같은 그룹을 막는 범위 | 쓰는 곳 |
|---|---|---|
| `"pool"` (기본) | 같은 풀에서 도는 잡 — **오늘 그대로** | 기존 설정 전부 |
| `"host"` | 같은 **호스트**(물리 컴퓨터)에서 도는 잡 | 시뮬레이터 · 에뮬레이터 · 기기 |
| `"global"` | 어디서 돌든 | 공용 외부 자원(스테이징 DB, 스토어 계정) |

- 제출 때 잡에 박는다(`concurrency_group` 과 같이 — 설정이 바뀌어도 큐의 잡은 안 바뀐다).
- claim 의 배제 조건은 **후보 잡의 범위**로 판정한다:
  `NOT EXISTS (busy b WHERE b.group = c.group AND (c.scope='global' OR (c.scope='pool' AND b.pool=:pool) OR (c.scope='host' AND b.host_key=:host_key)))`.
- 오늘의 운영은 Mac mini 의 로컬 레인과 `mac2-worker` 가 **같은 호스트**다. `scope = "host"` 를 쓰면
  두 워커가 같은 시뮬레이터를 동시에 잡는 일을 처음으로 막을 수 있다(오늘의 `pool` 범위로는 못 막는다).

## 7. 추정 — 머신마다 다르게 잰다 (결정 101·102·103)

### 7.1 표본에 호스트를 단다

- `jobs.host_key` 를 claim 때 박는다. 옛 행(NULL)은 읽을 때: `worker_name` NULL → 현재 로컬 호스트,
  아니면 `worker:<이름>`.
- 잡 중앙값: `(key, host_key)` 별. 기대 시간 출처 순서:
  `measured`(그 호스트, n ≥ `min_samples`) → `measured_other`(다른 호스트들의 중앙값, 신뢰도 **low**)
  → `preset` → `default`. `source` 에 새 값 `measured_other` 를 더한다(키 추가, 스키마 v1 유지).
- 단계 중앙값(멈춤 판정): `(key, host_key)` 별. **다른 호스트로 물러나지 않는다** — 느린 호스트에서
  「멈춘 듯」을 지어내지 않기 위해서다. 그 호스트의 표본이 모자라면 단계 판정 없이 침묵 규칙(`quiet`)만.
- `sample = false` 잡(점검 · 예열)은 두 중앙값 어디에도 안 들어간다.

### 7.2 큐 계산을 한 번에 (`core/queue.compute_queue`)

풀마다 따로 계산하던 것을 **모든 잡 · 모든 레인을 한 번에** 계산한다. 레인마다 (워커, 번호, 풀,
호스트, 상태)를 갖고, 잡마다 **들어갈 수 있는 레인 집합**을 콜백으로 받는다:

- 지정 잡 → 그 워커의 레인 · `pool` 잡 → 그 풀의 레인(그 중 `gated` 가 아닌 워커) · `any-capable`
  잡 → eligible 인 워커의 레인(`accept_any` 인 것만).
- 대기 잡마다 들어갈 수 있는 열린 레인 중 **끝나는 시각이 가장 이른** 레인을 고른다
  (`max(레인이 비는 시각, 그룹이 풀리는 시각) + 그 호스트의 기대 시간`). 호스트가 하나뿐인 풀에서는
  오늘의 「가장 빨리 비는 레인」과 같은 답이 나온다 — 테스트가 잠근다(§12 동치성).
- 그룹이 풀리는 시각은 범위별 키로 센다: `pool` → (그룹, 풀) · `host` → (그룹, 호스트) · `global` → 그룹.
- 결과 행은 표시 풀로 나눠 싣는다: 도는 잡은 돌고 있는 풀, 대기 잡은 `display_pool`.
- `eta_for_new`(`rcm eta`)도 같은 계산에 가상 잡을 넣는다.

### 7.3 새 이유 코드

| reason | 언제 | 「확인이 필요한 작업」 |
|---|---|---|
| `no_capable_machine` | `any-capable` 잡인데 살아 있는 후보가 전부 ineligible · unknown | **올린다** — `worker_down` 바로 뒤 |
| `check_failed` | 후보 워커가 전부 점검 실패로 막혔다(pool 잡 포함) | 올린다 — `no_capable_machine` 과 같은 자리 |
| `check_pending` | 후보 중 점검이 돌고 있거나 기다리는 것이 있고 다른 후보는 없다 | 안 올린다(스스로 풀린다, `held_by_load` 와 같은 종류) |

행동 가능한 이유 순서는 `worker_down → no_capable_machine → check_failed → stuck → upload_stalled →
not_scheduled → blocked_by_group → overdue → paused` 가 된다. 대기 행에는 `placement_detail` 로
워커별 사유를 싣는다(`[{worker, display_name, state, code, detail}]`, 최대 8개).

### 7.4 예열 · 표본 제외 `sample = false` (결정 103)

프리셋 키 `sample = false` 는 그 프리셋 잡을 추정 표본에서 뺀다. 점검 프리셋은 기본이 `false` 다.
새 머신의 첫 잡이 캐시를 데우느라 느린 문제는 **점검 프리셋이 예열도 겸하도록** 쓰는 것으로 푼다
(pub get · Gradle · CocoaPods 를 점검 스크립트 앞에 넣으면 그 머신의 첫 진짜 잡은 데워진 캐시로 돈다).
따로 「예열」 개념을 만들지 않는다 — 지문이 바뀐 날 자동으로 한 번 도는 잡이 이미 그 자리다.

## 8. 보이게 하기

### 8.1 API

| 라우트 | 인증 | 내용 |
|---|---|---|
| `GET /api/machines` | `read_auth` | `{generation, machines[]}` — 아래 |
| `GET /api/status` | 그대로 | `server.machines[]` 요약(`id · display_name · kind · pool · host_label · alive · fingerprint_hash · fingerprint_changed_at · eligible_count · preset_count · problems`) · 대기 행의 `placement`·`placement_detail` · 잡의 `target_worker`·`display_pool` |
| `POST /machines/{id}/recheck` | admin | 본문 `{checks?: [...]}` — 그 워커의 점검을 다시 건다(자동 1회 제한과 무관) → `{jobs: [...]}` |
| `GET /worker/spec` · `POST /worker/capabilities` | 워커 토큰 | §4.2 |

`machines[]` 항목:

```json
{"id": "mbp", "display_name": "mbp", "kind": "remote", "pool": "mbp", "alive": true,
 "host_label": "mbp (7c20)", "host_key": "7c20…", "accept_any": true,
 "fingerprint": {"hash": "…", "measured_at": "…", "stale": false, "changed_at": "…",
                 "probes": {"xcode": {"state": "ok", "value": "Xcode 26.5 · Build version 26F75",
                                      "previous": "Xcode 26.4 · …"}}},
 "presets": {"gate": {"state": "ineligible", "code": "tool_missing", "detail": {"tools": ["fvm"]}}},
 "checks": [{"preset": "sim-check", "state": "failed", "job_id": 3620, "auto_submitted": true,
             "error_code": null}]}
```

### 8.2 CLI

- `rcm machines [--json]` — §2 S1 의 표. `--machine ID` 로 한 머신의 프리셋별 판정 전체.
- `rcm check` 의 새 행: `machines` — `3 machines · 1 environment changed (Mac mini: xcode 26.5 → 27.2) ·
  1 check failed (Mac mini: sim-check #3620)`. 실패가 있으면 FAIL, 바뀌었는데 점검이 통과했으면 ok.
  `placement` — `gate: any machine (2 eligible) · scenario-qa: pool default (check failed)`.
- `rcm recheck <machine> [<check>…]` (admin).
- `rcm run … --machine ID` (§4.5).
- `rcm worker --check` 에 행 둘: `fingerprint`(프로브 값 요약)과 `presets`(이 워커가 eligible 인 수
  / 전체, 불능 사유 상위 3개). 서버에 보고하지 않고 그 자리에서 잰다.
- `rcm top`·`rcm jobs` 의 대기 행: `any machine` 표시와 `no capable machine — mbp: fvm missing` 사유.

### 8.3 웹

- 호스트 절 위 머리줄의 워커 필 옆: 지문이 최근 24시간 안에 바뀐 머신에 `환경 바뀜` 칩
  (누르면 바뀐 프로브의 앞 → 뒤 값). 점검 실패가 있으면 칩이 빨갛다.
- 「확인이 필요한 작업」: `no_capable_machine` · `check_failed` 행. 문구는 서버 코드 → 카탈로그(결정 37).
- 대기 행의 이유 칸: `맞는 머신 없음 · mbp: fvm 없음` · `점검 중 (sim-check)` · `어느 머신이든`.
- 펼친 행: 머신별 판정 목록(placement_detail).
- 호스트 절 아래 **머신** 표(접힌 채): 머신 · 호스트 · 지문 · 프리셋 가능 수 · 점검 · **다시 점검**
  (admin 토큰일 때만).
- 한국어 기본 · 영어 전환(결정 36), 390px 폰 배치는 `tests/test_web_layout.py` 가 잠근다.

## 9. 워커 붙이기 (E)

- `rcm init worker [--server URL|auto] [--pool P] [--lanes N] [--name N] [--force]` — 패키지된
  `templates/worker.toml`(= `examples/worker.toml`, 바이트 동일)로 `~/.config/rcm/worker.toml` (0600).
  기본 `server = "auto"`, `pool` 기본은 호스트명 짧은 이름.
- `rcm init worker --service-file PATH` — launchd plist(macOS) 또는 systemd unit(Linux)을 쓴다.
  실행 파일은 **지금 이 `rcm` 의 절대경로**, `PATH` 는 `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:
  /usr/sbin:/sbin`(venv bin 을 앞에 두지 않는다 — 운영 함정, `docs/operating.md`), 로그 경로. 파일만 쓰고
  `launchctl`·`systemctl` 은 부르지 않는다(다음에 칠 명령을 찍는다). `examples/launchd/
  com.remote-ci-monitor.worker.plist` · `examples/systemd/rcm-worker.service` 를 추가한다.
- 워커의 서버 찾기: `server` 가 비었거나 `"auto"` 면 mDNS(M5c `discover`)로 찾는다. 정확히 하나면
  그것, 여럿이면 `server_name = "…"`(광고 이름)과 맞는 것, 그래도 여럿이거나 없으면 이름들을 찍고
  usage 2 로 멈춘다. 등록·heartbeat 가 **연속 5번** 연결 실패하면 다시 찾는다(DHCP 로 주소가 바뀐
  경우) — `auto` 일 때만.
- `rcm token add NAME --worker` 는 비밀 다음 줄에 워커 쪽 명령 한 줄을 찍는다
  (`RCM_WORKER_TOKEN=<위 값> rcm worker --check`). 토큰을 옮기는 것은 여전히 사람이다(README 결정).

## 10. Flutter · 프로젝트 쪽 (F)

- `examples/server.toml` 에 주석 처리된 Flutter 블록: `[[probes]]`(flutter via fvm · xcode · java ·
  idb · android sdk) · `sim-check` 점검 프리셋 · `gate` any-capable 예시. **코드에는 Flutter 를 안 박는다**
  (이식성 — 예시 파일에만).
- `rcm-gate-connect` 스킬: 「`rcm-store-connect` 뒤에」라는 전제를 떼고 단독으로도 쓰게 한다 — 스토어
  프로파일이 없으면 프로파일 줄을 건너뛰고 `presets.gate.toml` 조각만 낸다. 그리고 `placement`·`checks`·
  `concurrency_scope` 를 물어 조각에 넣는다.

## 11. DB v23 · 설정 · 스키마

### 11.1 DB v23 (`_MIGRATIONS[23]`, 백업 `rcm.sqlite3.v22.bak` — 기존 규칙)

```sql
ALTER TABLE jobs ADD COLUMN placement TEXT NOT NULL DEFAULT 'pool';    -- 'pool' | 'capable'
ALTER TABLE jobs ADD COLUMN display_pool TEXT;                         -- 대기 표시 풀(NULL = pool)
ALTER TABLE jobs ADD COLUMN target_worker TEXT;                        -- 지정 잡
ALTER TABLE jobs ADD COLUMN concurrency_scope TEXT NOT NULL DEFAULT 'pool';
ALTER TABLE jobs ADD COLUMN host_key TEXT;
ALTER TABLE jobs ADD COLUMN fingerprint_hash TEXT;
ALTER TABLE jobs ADD COLUMN sample INTEGER NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS machines (
  worker TEXT PRIMARY KEY, host_key TEXT, display_name TEXT, accept_any INTEGER NOT NULL DEFAULT 1,
  fingerprint_hash TEXT, fingerprint_json TEXT, previous_json TEXT, changed_at REAL,
  measured_at REAL, generation TEXT, report_json TEXT);
CREATE TABLE IF NOT EXISTS fingerprints (hash TEXT PRIMARY KEY, json TEXT NOT NULL, first_seen_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS machine_checks (
  worker TEXT NOT NULL, preset TEXT NOT NULL, fingerprint_hash TEXT NOT NULL,
  job_id INTEGER, submitted_at REAL NOT NULL, auto INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS machine_checks_key ON machine_checks(worker, preset, fingerprint_hash);
CREATE INDEX IF NOT EXISTS jobs_check ON jobs(worker_name, preset, fingerprint_hash, id);
```

- `machines` 는 **지문이 바뀐 것을 재시작 너머로 기억**하려고 둔다(앞 지문과 비교해야 한다). 보고 자체는
  메모리에 있고 DB 는 마지막 것을 덮어 쓴다.
- `fingerprints` 는 잡 상세에서 「이 잡이 돈 환경」을 보이려고 둔다(`GET /jobs/{id}.machine` =
  `{worker, display_name, host_label, fingerprint: {probes…}}`). 보존: 어떤 잡도 가리키지 않는
  지문은 `metadata_retention_days` 뒤 janitor 가 지운다.
- 인덱스 마이그레이션은 이미 있는 열·표를 견딘다(M5h 의 교훈 — 번호가 밀려도 안전하게).
- 옛 빌드는 v23 파일을 거부하고 백업을 가리킨다(기존 규칙).

### 11.2 설정 키

| 위치 | 키 | 기본 |
|---|---|---|
| 서버 `[[probes]]` | `name` · `argv` · `timeout_seconds` | — · — · 10 |
| 서버 `[server]` | `probe_interval_seconds` · `auto_checks` · `local_accept_any` · `check_sweep_seconds` | 600 · true · true · 30 |
| 프리셋 | `placement` · `concurrency_scope` · `checks` · `check_ref` · `sample` | `"pool"` · `"pool"` · `[]` · `"HEAD"` · true(점검 프리셋은 false) |
| `worker.toml` | `server = "auto"` · `server_name` · `accept_any` | — · `""` · true |

`rcm reload` 는 `[[probes]]` 와 프리셋의 새 키를 다시 읽는다(세대 증가). `[server]` 의 새 키는
재시작이 필요하다(M5m 규칙 그대로 — 바뀌었으면 reload 가 거절하고 이름을 댄다).

### 11.3 `/api/status` 스키마

`schema_version` 은 1 그대로 — 키만 더한다. 더하는 키: `server.machines[]` · `server.generation` ·
대기·도는 행의 `placement` · `display_pool` · `target_worker` · `concurrency_scope` · `host_key` ·
`placement_detail` · `estimate.source` 의 새 값 `measured_other` · `reason` 의 새 값 셋.
`presets[]` 에 `placement` · `checks` · `concurrency_scope` · `sample`.

### 11.4 인터페이스 (테스트가 기대는 이름 — 구현은 이 이름을 지킨다)

**`core/capability.py`** (순수 — 시계는 인자, I/O 없음)

```python
LOCAL_WORKER = "@local"
PROBE_OK, PROBE_ERROR, PROBE_MISSING, PROBE_TIMEOUT = "ok", "error", "missing", "timeout"
ELIGIBLE, INELIGIBLE, UNKNOWN = "eligible", "ineligible", "unknown"
CHECK_PASSED, CHECK_RUNNING, CHECK_FAILED, CHECK_NEEDED = "passed", "running", "failed", "needed"
# 판정 코드: "report_missing" "report_stale" "tool_missing" "workspace_tool" "check_failed"
#           "check_running" "check_needed" "fingerprint_unknown" "ok"

@dataclass(frozen=True)
class ProbeResult:
    state: str                  # PROBE_*
    value: str | None           # ok → 정규화 문자열 · error → "exit N" · missing/timeout → None
    stale: bool = False         # timeout 이라 앞 값을 이어 쓴 것

def normalize_probe_output(output: bytes | str | None, returncode: int | None, *,
                           missing: bool = False, timed_out: bool = False) -> ProbeResult
def settle_fingerprint(current: Mapping[str, ProbeResult],
                       previous: Mapping[str, ProbeResult] | None) -> "Fingerprint"
def fingerprint_hash(probes: Mapping[str, ProbeResult]) -> str   # timeout 아닌 것만, 16 hex

@dataclass(frozen=True)
class Fingerprint:
    probes: Mapping[str, ProbeResult]   # timeout 은 앞 값(stale=True)으로 채워진다
    hash: str | None                    # 앞 값 없는 timeout 이 하나라도 있으면 None(unknown)
    stale: bool

def changed_probes(old: Mapping[str, ProbeResult] | None,
                   new: Mapping[str, ProbeResult]) -> dict[str, tuple[str | None, str | None]]

@dataclass(frozen=True)
class PresetFacts:
    missing_tools: tuple[str, ...] = ()
    workspace_tools: tuple[str, ...] = ()
    missing_passthrough: tuple[str, ...] = ()

@dataclass(frozen=True)
class CapabilityReport:
    worker: str
    generation: str
    host_key: str
    accept_any: bool
    fingerprint: Fingerprint
    presets: Mapping[str, PresetFacts]
    measured_at: datetime

@dataclass(frozen=True)
class CheckRecord:
    preset: str
    job_id: int
    job_state: str              # 잡 상태 그대로(queued·running·succeeded·failed·…)
    fingerprint_hash: str | None

def check_state(records: Sequence[CheckRecord], fingerprint_hash: str | None
                ) -> tuple[str, CheckRecord | None]   # 같은 지문의 가장 큰 job_id 기준 → CHECK_*

@dataclass(frozen=True)
class Verdict:
    state: str                  # ELIGIBLE · INELIGIBLE · UNKNOWN
    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)

def verdict(preset: Preset, report: CapabilityReport | None, *, generation: str, now: datetime,
            max_age_seconds: float,
            check_records: Sequence[CheckRecord] = ()) -> Verdict
def host_key_from(raw_hardware_id: str | None, worker: str) -> str   # sha256 앞 16 · 없으면 "worker:<w>"
```

**`runner.py`** (I/O): `measure_tools(requires, env) -> tuple[list[str], list[str]]` — (missing,
workspace_only). PATH 의 절대 항목과 절대경로 선언만 본다. `missing_tools` 는 그대로 둔다(잡 직전 검사).
**`hostsample.py`**: `hardware_id() -> str | None`, `run_probes(probes, env) -> dict[str, ProbeResult]`.

**`core/model.py`**: `Job` 에 `placement: str = "pool"` · `display_pool: str | None = None` ·
`target_worker: str | None = None` · `concurrency_scope: str = "pool"` · `host_key: str | None = None` ·
`fingerprint_hash: str | None = None` · `sample: bool = True`. `Preset` 에 `placement = "pool"` ·
`concurrency_scope = "pool"` · `checks: tuple[str, ...] = ()` · `check_ref = "HEAD"` ·
`sample: bool | None = None`(None → 점검 프리셋이면 False, 아니면 True). `WorkerInfo` 에
`host_key: str | None = None`. 상수 `PLACEMENT_POOL = "pool"` · `PLACEMENT_CAPABLE = "capable"`(DB 값.
설정 문자열은 `"any-capable"`) · `SCOPE_POOL/HOST/GLOBAL` · `REASON_NO_CAPABLE_MACHINE` ·
`REASON_CHECK_FAILED` · `REASON_CHECK_PENDING` · `SOURCE_MEASURED_OTHER = "measured_other"`.
`ServerConfig.probes: tuple[Probe, ...]` (`Probe(name, argv, timeout_seconds)`).

**`core/queue.py`**

```python
@dataclass(frozen=True)
class Placement:
    workers: frozenset[str | None]          # 이 잡이 들어갈 수 있는 워커(None = 로컬 레인)
    reason: str | None = None               # workers 가 비었을 때의 이유 코드(REASON_*)
    notes: tuple[PlacementNote, ...] = ()

@dataclass(frozen=True)
class PlacementNote:
    worker: str
    display_name: str
    state: str                              # ELIGIBLE · INELIGIBLE · UNKNOWN · "down"
    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)

def expected_on(key, preset, host_key, medians, host_medians, cfg) -> tuple[float, str, int]
    # host_medians: host_key → key → Median. 그 호스트 → (다른 호스트들 → measured_other) → preset → default

compute_queue(jobs, *, workers, paused, medians, presets, cfg, now, progress=None, step_medians=None,
              placement: Callable[[Job], Placement] | None = None,
              host_medians: Mapping[str, Mapping[str, Median]] | None = None,
              host_step_medians: Mapping[tuple[str, str], Mapping[str, StepMedian]] | None = None)
```

- `placement=None` 이면 옛 규칙: 잡의 `pool` 과 같은 풀의 레인(`WorkerInfo.pool`). 그래서 기존 호출과
  기존 테스트는 그대로 돈다.
- `host_step_medians` 가 주어지면 도는 잡은 `(job.host_key, job.key)` 의 표만 쓰고, 없으면 단계 판정
  없음(다른 호스트로 안 물러난다). 주어지지 않으면 옛 `step_medians`.
- `QueueRow` 에 `placement_detail: tuple[PlacementNote, ...] = ()`.

**`store.py`**

```python
@dataclass(frozen=True)
class Claimant:
    worker: str                     # LOCAL_WORKER 또는 원격 이름
    pool: str
    host_key: str | None
    fingerprint_hash: str | None
    capable: tuple[str, ...] = ()   # eligible 인 any-capable 프리셋
    gated: tuple[str, ...] = ()     # checks 로 막힌 프리셋(pool 잡)
    accept_any: bool = True

def claim(self, lane, now, pool=DEFAULT_POOL, worker_name=None, *, claimant: Claimant | None = None)
    # claimant 가 None 이면 옛 SQL 그대로. 로컬 claimant 의 worker_name 은 DB 에 NULL 로 남는다.
def upsert_machine(self, worker, *, host_key, display_name, accept_any, fingerprint_hash,
                   fingerprint_json, measured_at, generation, report_json) -> MachineRow
    # 앞 지문과 다르면 previous_json · changed_at 을 갱신한다. 반환 행에 changed(bool)
def list_machines(self) -> list[MachineRow]
def record_fingerprint(self, hash, json_text, now) -> None
def check_records(self, worker: str, presets: Sequence[str]) -> list[CheckRecord]
def auto_check_done(self, worker, preset, fingerprint_hash) -> bool
def record_machine_check(self, worker, preset, fingerprint_hash, job_id, now, *, auto: bool) -> None
```

**HTTP**: `GET /worker/spec` · `POST /worker/capabilities` · `GET /api/machines` ·
`POST /machines/{id}/recheck`. `POST /jobs` 본문에 `machine`(선택). 응답에 `placement`.

**CLI**: `rcm machines [--json] [--machine ID]` · `rcm recheck MACHINE [CHECK ...]` ·
`rcm run … --machine ID` · `rcm init worker [--server] [--pool] [--lanes] [--name] [--force]
[--service-file PATH]`.

## 12. fail-open 금지 — 이 마일스톤에서 지키는 것

- 보고가 없거나 낡은 워커는 `unknown` 이고 `any-capable` 잡을 **안 받는다**. ETA 계산도 그 레인을 안 센다.
- 지문 측정 실패(`timeout`)를 「바뀜」으로도 「그대로」로도 꾸미지 않는다 — 앞 값 + `stale`.
- 점검 실패를 자동으로 덮지 않는다. 자동 점검은 같은 지문에 한 번뿐.
- 다른 호스트의 단계 중앙값으로 멈춤을 판정하지 않는다.
- 대기 잡의 머신을 모르면 ETA 는 `—`. 「가장 빨리 비는 맞는 레인」이 없으면 시각을 만들지 않는다.
- `placement_detail` 이 비어 있으면 「맞는 머신 없음」의 이유를 지어내지 않고 `unknown` 이라고 쓴다.

## 13. 안 만드는 것

- 내부망을 훑어 아무 컴퓨터에나 SSH 로 접속하거나 설치하기. 워커는 사람이 깐다.
- 토큰 자동 발급 · 자동 가입. 토큰은 사람이 옮긴다(보안 결정 유지).
- Xcode · Flutter · idb 를 rcm 이 설치·업데이트하기. rcm 은 **무엇이 바뀌었고 사람이 손봐야 한다**까지.
- 유실된 잡을 다른 머신에서 자동 재실행하기. 배포·업로드 프리셋은 두 번 돌면 안 된다.
- 버전 비교 문법(`xcode >= 27`). 지문은 같다/다르다만 본다. 버전의 의미는 점검 프리셋이 판정한다.
- 비밀 파일을 워커로 나르기. 비밀이 필요한 프리셋(배포·업로드)은 `pool` 배치로 남긴다 — 설정 문서가
  `any-capable` 은 비밀 없는 게이트 · 테스트용이라고 적는다.
- GitHub 연동(결정 30).

## 14. 테스트 배치 · 역할 (test-first)

| 역할 | 문서 | 범위 |
|---|---|---|
| A | `docs/m5n-test-scenarios-a.md` | 순수: `core/capability.py`(정규화 · 해시 · 판정 표 §4.3 · 점검 상태) · `core/queue.py` 전역 계산(§7.2 · 동치성 · 그룹 범위 · 이유 코드 · `measured_other`) · 설정 검증(§11.2) |
| B | `docs/m5n-test-scenarios-b.md` | 저장소 · 서버 · 프로토콜: v23 마이그레이션 · `store.claim` 의 세 갈래와 범위 배제 · `/worker/spec`·`/worker/capabilities` · 세대 · 자동 점검 스위퍼(1회 제한 · down 워커 제외 · ref 실패) · `POST /jobs` 의 `placement` · `--machine` 권한 · `GET /api/machines` · 두 프로세스 e2e(로컬 + 원격 워커, PATH 를 달리해 도구 유무를 만든다) |
| C | `docs/m5n-test-scenarios-c.md` | 사람에게 닿는 것: `rcm machines` · `rcm check` 행 · `rcm recheck` · `rcm run` 의 한 줄 · `rcm top`/`jobs` · 웹(node 순수 함수 · 카탈로그 · 390px 배치) · `rcm init worker`(+ `--service-file`) · 워커 mDNS 찾기 · `token add --worker` 안내 |

각 역할 문서는 **베스트 케이스**(의도한 흐름이 끝까지 되는 것)와 **엣지 케이스**(경계 · 실패 · 경쟁 ·
옛 데이터)를 표로 나누고, 케이스마다 테스트 함수 이름을 단다. 테스트 에이전트는 `src/` 를 건드리지
않는다(역할 문서에 가정을 남긴다).

### 14.1 반드시 들어갈 엣지 케이스 (역할 문서의 최소 집합)

1. Xcode 재연: 지문이 바뀐 워커의 `checks` 프리셋이 `check_needed` 로 내려가고 자동 점검이 **정확히
   한 번** 걸리고, 실패하면 그 뒤로 안 걸리며, 지문이 또 바뀌면 다시 걸린다.
2. `timeout` 프로브는 지문을 안 바꾼다. 첫 회차 `timeout` 은 `fingerprint_unknown`.
3. 같은 호스트의 워커 둘(운영 모양 그대로): `scope = "host"` 그룹이 둘 사이를 막고, `scope = "pool"`
   은 안 막는다.
4. 호스트 하나짜리 풀만 있는 큐에서 새 전역 계산이 옛 풀별 계산과 **같은 행**을 낸다(동치성).
5. 보고 없는 워커 · 세대 어긋난 보고 · 3주기보다 낡은 보고는 `any-capable` 잡을 못 집는다.
6. `workspace_tool` 은 unknown 이지 ineligible 이 아니다.
7. `pool` 배치의 `requires` 불능은 오늘처럼 집혀서 `tool_missing` 으로 끝난다(회귀 금지).
8. `any-capable` 잡을 집으면 `pool` 이 집은 워커의 풀로 바뀌고 끝난 뒤 표본은 그 호스트로 간다.
9. 서버 재시작: `machines` 표가 앞 지문을 기억해 재시작 뒤 첫 보고에서 「바뀜」을 놓치지 않는다.
10. 점검 프리셋이 `checks` 를 가지면 · `git_ref` 를 안 받으면 기동 실패.
11. `--machine` 을 비-admin 이 일반 프리셋에 쓰면 403, 점검 프리셋엔 허용.
12. 다른 호스트 중앙값만 있을 때 `measured_other` · 신뢰도 low, 단계 판정은 없음(`quiet` 만).
13. 옛 DB(v22)의 잡: `host_key` NULL 이 로컬/`worker:` 로 풀리고 중앙값이 깨지지 않는다.
14. 워커가 `accept_any = false` 면 `any-capable` 잡을 안 집지만 지정 잡(점검)은 집는다.

## 15. mutcheck 추가 (기존 + 6)

1. 판정 표에서 `report_stale`(세대 비교) 제거 → 낡은 보고로 집는다.
2. 자동 점검의 1회 제한 제거 → 실패한 점검이 무한히 다시 걸린다.
3. `timeout` 을 지문 해시에 넣음 → 느린 프로브가 지문을 바꾼다.
4. `scope = "host"` 를 `pool` 처럼 판정 → 같은 호스트의 두 워커가 동시에 잡는다.
5. 단계 중앙값을 다른 호스트로 물러남 → 느린 호스트에서 `stuck`.
6. claim 에서 `any-capable` 잡의 `pool` 갱신 제거 → 표본이 집지 않은 풀로 간다.

## 16. PR 순서 · 완료 기준

PR 하나(`feat/m5n-capable-worker-placement`), 단계 다섯 — 각 단계가 커밋 하나 이상이다.

| 단계 | 내용 |
|---|---|
| 1 | 이 명세 · 시나리오 문서 셋 · 빨간 테스트(test-first) |
| 2 | 순수 계층: `core/capability.py` · `core/queue.py` 전역 계산 · 설정 · 모델 |
| 3 | 저장소 · 서버 · 워커: DB v23 · claim · 보고 경로 · 자동 점검 · API |
| 4 | 사람에게 닿는 것: CLI · 웹 · 워커 붙이기(§9) · 예시와 스킬(§10) · 문서 · CHANGELOG |
| 5 | 격리 검증 셋(계약 대조 · 실기 · 엣지)과 그 수정, 개발 인스턴스 실기 |

완료 기준:

1. 같은 Mac 에서 서버 + 원격 워커 둘(서로 다른 PATH)로: `any-capable` 게이트가 도구가 있는 워커로만 가고,
   셋 다 바쁘면 가장 일찍 끝날 곳을 ETA 가 고른다.
2. 프로브 출력을 바꾸면(가짜 `xcodebuild` 스크립트) 그 워커가 `check_needed` → 자동 점검 1회 →
   실패면 `check_failed` 가 웹 · `rcm check` · `rcm machines` 에 이름과 함께 뜨고, **다시 점검**으로 풀린다.
3. `scope = "host"` 그룹이 같은 호스트의 두 워커를 직렬화한다.
4. 기존 테스트 전부 · 새 테스트 · node · ruff · mutcheck(+6) · 설치 스모크가 초록.
5. 개발 인스턴스(포트 8790+, `~/.local/share/rcm-devtest-m5n`, 알림 훅 없음)에서 각 규칙이 한 번씩
   발동하는 것을 사람 눈으로 읽었다(「돌려 봐야 마지막 버그가 나온다」).
6. 격리 에이전트 검증(계약 대조 · 실기 · 엣지)이 찾은 것을 §19 에 남기고 고쳤다.

## 17. 오너 결정 (88~104 · 추천값으로 구현, 확인 대기)

| # | 결정 | 추천 |
|---|---|---|
| 88 | 워커와 호스트를 가른다. 호스트 키는 하드웨어 UUID 의 해시 | 그렇게 |
| 89 | 환경 지문은 설정의 `[[probes]]`(명령은 운영자가 고른다) | 그렇게 — 코드에 Xcode 를 안 박는다 |
| 90 | 프로브 `timeout` 은 지문을 안 바꾼다(앞 값 + stale) | 그렇게 |
| 91 | 보고 없음 · 낡음은 `unknown` 이고 `any-capable` 을 못 받는다 | 그렇게(fail-closed) |
| 92 | `missing_passthrough` 는 판정에 안 넣는다(정보) | 그렇게(M5m 과 일관) |
| 93 | 작업 공간에만 있을 수 있는 도구는 `unknown` | 그렇게 |
| 94 | 점검 프리셋은 `git_ref` 를 받아야 하고 순환 금지 | 그렇게 |
| 95 | 자동 점검은 (워커, 점검, 지문)마다 한 번 | 그렇게 |
| 96 | `--machine` 은 점검 프리셋이면 토큰, 그 밖은 admin | 그렇게 |
| 97 | `placement` 기본은 `pool` — 기존 설정의 동작이 안 바뀐다 | 그렇게 |
| 98 | `pool` 배치의 `requires` 불능은 오늘처럼 집어서 즉시 실패, `checks` 는 막고 기다린다 | 그렇게 |
| 99 | 맞는 머신이 없어도 `any-capable` 잡은 큐에 선다(제출 때 한 줄 경고) | 그렇게 |
| 100 | `concurrency_scope` 에 `pool`(기본) · `host` · `global` | 그렇게 |
| 101 | 잡 · 단계 표본을 호스트별로 | 그렇게 |
| 102 | 잡 중앙값만 다른 호스트로 물러난다(`measured_other`, low), 단계 중앙값은 안 물러난다 | 그렇게 |
| 103 | 예열은 따로 안 만들고 점검 프리셋이 겸한다, `sample = false` 만 더한다 | 그렇게 |
| 104 | 큐 계산을 풀별에서 전역 한 번으로(동치성 테스트로 잠근다) | 그렇게 |

## 18. 위험

- **큐 계산 전역화(§7.2)가 가장 크다.** 2,900여 개 테스트가 풀별 계산을 잠그고 있다. 동치성 테스트를
  먼저 쓰고(호스트 하나짜리 풀 무작위 픽스처 200개 — 옛 함수의 출력을 골든으로 얼린다) 그 다음 바꾼다.
- `store.claim` SQL 이 길어진다. `INDEXED BY` 와 M5f 결정 49 의 성능 함정(풀 인덱스 · `ANALYZE`)을
  다시 잰다 — 대기 잡 1,000 · 워커 8 · 레인 48 에서 claim 이 오늘보다 2배 넘게 느려지면 멈추고 다시 설계.
- 원격 워커의 프로브가 서비스 환경에서 GUI 권한(TCC)에 막힐 수 있다(`xcodebuild` 는 괜찮다고 알려져
  있지만 실측 전). 막히면 `error` 상태로 지문에 들어가고 그것이 곧 사실이다.
- 운영의 `mac2-worker` 는 같은 호스트다. 배포 뒤 `rcm machines` 가 두 워커를 같은 호스트로 묶어 보이는지
  운영에서 확인한다(배포는 오너 몫).

## 19. 구현하며 바뀐 것

명세와 다르게 간 곳, 명세가 말하지 않아 정한 곳. 시나리오 문서 셋의 「가정」이 나머지를 적는다.

1. **`compute_queue(placement=None)` 의 옛 호출 계약.** 잡의 풀을 단 레인이 하나도 없으면 넘겨받은
   레인 전부를 그 잡의 레인으로 본다. 레인에 풀 표시가 없던 시절의 호출(기존 테스트 ·
   `eta_for_new`)이 「풀 하나의 잡 + 그 풀의 레인」을 넘겼기 때문이다. 서버는 언제나 `placement` 를
   넘기므로 워커 없는 풀이 남의 레인을 빌리는 일은 없다. 동치성 골든 200개는 그대로 초록이다.
2. **`worker_hosts`** 를 `compute_queue` 에 더했다. 레인이 아예 없는 후보(로컬 레인을 안 띄운 서버 ·
   down 워커)의 기대 시간도 그 호스트의 표본으로 잰다.
3. **`machine_checks` 에 기본 키가 없다**(인덱스만). 같은 초에 자동 점검과 사람의 다시 점검이 함께
   기록될 수 있어야 한다(시험 시계는 멈춰 있다). `auto_submitted` 는 「지금 보이는 그 점검 잡을 rcm
   이 걸었나」다 — 사람이 다시 걸면 False 가 된다.
4. **지정 점검 잡은 제출 때 대상의 지문을 미리 박는다.** 그래야 기다리는 동안에도 `check_running`
   이고, claim 때 실제 지문으로 덮인다.
5. **재등록은 그 워커의 보고를 지운다**(새 프로세스는 새로 잴 때까지 「보고 없음」 — 결정 91).
   워커는 등록 직후, 레인을 띄우기 전에 재서 보낸다. 세대를 안 보내는 옛 서버에는 보고하지 않는다.
6. **`worker.toml` 의 `server` 가 비어도 로더가 막지 않는다.** `rcm worker` 가 같은 망에서 찾고
   못 찾으면 usage 2 다. 기존 테스트 둘(`test_worker_config` · `test_cli_worker`)을 이 규칙으로
   옮겼고, 후자는 진짜 LAN 을 훑지 않게 발견을 빈 결과로 바꿨다.
7. **`rcm eta` 의 any-capable 가상 잡**은 전역 계산 위에서 낸다(`_eta_capable`). `--pool` 을 준 eta 는
   옛 풀 규칙 그대로다.
8. 시험 쪽에서 고친 가정 셋: `auto_checks = "yes"` 는 다른 `[server]` 불리언처럼 받는다(`"maybe"` 로
   바꿨다) · v23 은 `jobs` 의 끝에 일곱 열을 더하므로 v22 의 「링크 열이 맨 끝」 자물쇠를 「v23 열
   바로 앞」으로 옮겼다 · `rcm worker --check` 의 시험 명세가 `env_passthrough = []` 로 PATH 없이
   도구를 찾던 것을 `["PATH"]` 로(잡은 넘긴 변수만 받는다 — `runner.build_env`).
9. **스스로 안 풀리는 `check_needed` 는 `check_pending` 이 아니다**(실프로세스 수용 시험
   2026-11-16). 자동 점검이 유실 · 취소로 판정 없이 끝나면 결정 95 로 다시 안 걸리는데, §7.3 의
   분류는 그것을 「점검 중 · 스스로 풀림」으로 불러 잡이 확인 목록에도 안 오르고 영원히 기다렸다.
   이제 `auto_checks` 가 꺼졌거나 그 (워커, 점검, 지문)에 자동 점검을 이미 썼으면 판정
   `detail.recheck = true` 를 달고(코드는 `check_needed` 그대로), 대기 이유는 `check_failed`,
   `machines[].checks[].recheck_needed = true`, `rcm check` 의 `machines` 행은 FAIL 이다.
   결정 95(다시 안 건다)는 그대로다.
10. **`rcm worker` 는 `--config` 도 `--server` 도 없으면 `~/.config/rcm/worker.toml` 을 읽는다**
    (같은 수용 시험). S1 의 순서(`rcm init worker` → `rcm worker --check`)가 그 파일을 안 읽어
    풀이 `default` 로 나오고 서버를 같은 망에서 찾아 다른 서버에 토큰을 보냈다.
