<h1 align="center">remote_ci_monitor</h1>

<p align="center">
  <b>이미 있는 빌드 머신 한 대를 CI 큐로 만든다.</b><br>
  어느 컴퓨터에서든 프리셋을 제출하고, 큐를 보고, 종료 코드로 결과를 받는다.
</p>

<p align="center">
  <a href="https://github.com/monocsp/remote_ci_monitor/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/monocsp/remote_ci_monitor/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/monocsp/remote_ci_monitor/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/monocsp/remote_ci_monitor?sort=semver"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="Runtime dependencies: none" src="https://img.shields.io/badge/runtime%20deps-0-success">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/github/license/monocsp/remote_ci_monitor"></a>
</p>

<p align="center">
  <a href="#install">설치</a> ·
  <a href="#build-machine-3-commands">빠른 시작</a> ·
  <a href="docs/usage.ko.md">사용법</a> ·
  <a href="docs/configuration.md">설정</a> ·
  <a href="CHANGELOG.md">변경 이력</a><br>
  <a href="README.md">English</a> · <b>한국어</b>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/monocsp/remote_ci_monitor/main/docs/images/ui/hero-queue.png" alt="웹 큐 화면 — 스텝까지 보이는 실행 중 잡, ETA 가 붙은 대기 잡, 그 아래 빌드 머신의 CPU · 메모리 · 디스크 · GPU" width="820">
</p>

빌드 머신 **한 대**를 팀이 나눠 쓰면 결국 손으로 줄을 선다. 지금 누가 뭘 돌리는지, 앞 잡이 걸린
건지 느린 건지, 머신이 버거운지, 통과했는지. rcm 은 그걸 한 화면과 종료 코드 하나로 답한다.

- **따로 띄울 게 없다.** 패키지 하나, 런타임 의존성 0, Python 3.11+ 표준 라이브러리만. 서버와
  클라이언트가 같은 패키지다. GitHub 도, 브로커도, 별도 DB 서버도 없다.
- **마지막 push 가 아니라 지금 트리를 검사한다.** `rcm run` 은 작업 디렉터리를 있는 그대로(미커밋
  변경 포함) 올린다. 초록이면 *이* 트리가 통과한 것이다. 배포용 프리셋은 반대로 푸시된 ref 를 돈다.
- **모르면 모른다고 한다.** 종료 코드 3 은 *모름*(서버 재시작 · 안 닿음 · 시간 초과)이고 절대
  실패로 포장하지 않는다. ETA 에는 confidence 가 붙고, 모르는 값은 `0` 이 아니라 `—` 로 찍힌다.
- **파일이 돌아온다.** 프리셋이 무엇을 만드는지 적어 두면
  (`artifacts = ["test/**/goldens/*.png"]`) `rcm run --fetch-artifacts` 가 그 파일을 내 트리의
  같은 경로로 가져온다. 기다리는 동안 내가 고친 파일은 절대 덮어쓰지 않는다.
- **같은 내부망이면 스스로 찾는다.** 세션은 주소를 몰라도 된다. 서버가 `_rcm._tcp` 를 mDNS/DNS-SD
  로 알린다. 밖에서는 어떤 경로든(Tailscale, 터널) 쓰면 된다.
- **보는 사람을 위한 웹 화면.** 정적 파일 셋, 빌드 단계 없음, 외부 자산 없음, 폰에서도 읽힌다.

빌드 머신은 macOS(Apple Silicon · Intel)와 Linux. Windows 는 범위 밖이고, Windows 의 세션은 WSL
로 제출한다.

Status: **M0–M5 done (v0.2.3)**. GitHub 백엔드는 없고 계획도 없다. 계획서는 `PLAN.md`, 변경 이력은
`CHANGELOG.md`.

## Install

두 머신 모두 Python **3.11.4+**(안전한 `tarfile` 필터)가 필요하다. 패키지 하나, 런타임 의존성 없음:

```sh
pipx install git+https://github.com/monocsp/remote_ci_monitor   # git(main) 에서. dev 브랜치는 @dev
pipx install remote-ci-monitor                      # PyPI (아직 공개 전 — CONTRIBUTING.md 참고)
uvx --from remote-ci-monitor rcm version            # 설치 없이 uv 로 실행
```

`pipx` 가 없으면 `python3 -m pip install --user pipx && python3 -m pipx ensurepath` 뒤 새 셸을 연다.
설치 직후 `rcm` 이 "command not found" 면 `~/.local/bin` 이 아직 `PATH` 에 없는 것이다.
`pipx ensurepath` 뒤 새 셸을 열면 된다. GitHub Releases 의 wheel 도 `pipx install <wheel-url>` 로
설치된다.

## Build machine (3 commands)

```sh
rcm init server            # ~/.config/rcm/server.toml 을 쓴다 — 프리셋과 bind 주소를 고친다
rcm token add laptop       # 토큰을 한 번만 보여 준다. 그 세션 머신에 넘긴다
rcm serve                  # http://127.0.0.1:8787 · Ctrl-C 나 SIGTERM 으로 깨끗이 멈춘다
```

생성된 설정에는 무해한 `ok` 프리셋이 들어 있다. 내 프리셋을 쓰기 전에 경로부터 끝까지 증명해 볼 수
있다. 다른 컴퓨터의 세션을 받으려면 `bind` 를 그 머신의 LAN·Tailscale 주소(또는 `0.0.0.0`)로 두고,
macOS 면 방화벽 허용 창에서 Python 을 허용한다. 다른 컴퓨터에서
`curl http://<빌드머신>:8787/api/health` 로 확인한다. 루프백이 아닌 주소로 열린 서버는 자기를
**광고**한다(`_rcm._tcp`, mDNS/DNS-SD — `advertise = false` 로 끄고 `advertise_name` 으로 이름을
바꾼다). 그래서 같은 네트워크의 세션은 주소가 아예 필요 없다.

토큰: `rcm token add ops --admin` 은 관리자 토큰(pause·resume·남의 잡 취소·아무 로그 읽기).
`rcm token list` 는 비밀을 보여 주지 않는다. `rcm token revoke NAME`. 포트를 바꾸려면
`rcm serve --port 8790`(또는 설정의 `port`). `0.0.0.0` 으로 열 때는 `public_url` 을 적어야
`rcm run` 이 찍는 잡 URL 이 다른 컴퓨터에서 열린다.

로그인·재부팅을 넘겨 계속 돌리는 방법은
[빌드 머신 운영](docs/operating.md#run-as-a-service)의 launchd·systemd 예시에 있다.

## Session machine (3 commands)

세션에 필요한 건 두 가지다. 서버가 **어디** 있는지, 그리고 **내가 누구**인지. 자동이 되는 건
앞의 하나뿐이다.

빌드 머신과 **같은 Wi-Fi·LAN** 이면 `server` 를 비워 두면 된다(또는 `server = "auto"`). rcm 이
알아서 찾고, `rcm discover` 로 무엇이 보이는지 확인할 수 있다. 단 **양쪽 다 rcm 0.2.2 이상**
이어야 한다. 그 아래 클라이언트에는 발견 기능이 아예 없어서 `no server configured` 로 멈추고,
`rcm worker` 와 달리 클라이언트는 서버와 버전을 맞춰 보지도 않는다. 다른 네트워크에서는 경로를
직접 마련해야 한다 — Tailscale 이나 터널 — 그 주소를 `client.toml` 에 적는다.

나머지 반쪽인 토큰은 **일부러 손으로 옮긴다**. `rcm token add` 는 **빌드 머신에서** 돌고 서버의
DB 에 바로 쓴다. 토큰을 내주는 API 는 없고 앞으로도 두지 않는다. 그래서 서버를 막 찾은 머신에서
`rcm check` 의 **server** 는 초록인데 **token** 만 빨간 건 고장이 아니다. 토큰을 옮기는 것이
남은 유일한 단계다.

```sh
rcm init client --server http://<빌드머신>:8787   # ~/.config/rcm/client.toml (모드 600)
export RCM_TOKEN=<rcm token add 로 받은 토큰>      # 또는 그 파일에 token = "…" 로 적는다
rcm check                  # python · server · token · presets · timezone 이 전부 ok 여야 한다
cd ~/src/app               # 아무 프로젝트 디렉터리 — rcm run 은 *현재 디렉터리*를 올린다
rcm run ok                 # 첫 잡: 종료 0 과 JSON 한 줄이면 전부 통한 것이다
rcm top                    # 큐 · ETA · 최근 결과 · 호스트 부하
```

그다음은 프로젝트 디렉터리에서 진짜 작업을 돌리고(`rcm run gate -f scope=full`) `$?` 로 분기한다.
0 성공 · 1 실패 · 2 취소나 시간 초과 · 3 모름. `examples/session/ci-gate.sh` 가 그대로 쓰는
래퍼다(`jq` 필요).

`rcm run` 은 **현재 디렉터리**를 스냅샷한다(git 추적 파일 + 무시되지 않은 미추적 파일, `.rcmignore`
패턴 제외). 서버에 없는 파일만 올리고, 기다리고, stdout 에 JSON 한 줄을 찍는다. 진행 표시는
stderr 로 간다. Ctrl-C 는 떼어 놓기다. 잡은 계속 돈다. `rcm wait --job N` 으로 다시 붙고
`rcm cancel N` 으로 멈춘다. 빌드 머신의 작업 공간에는 **`.git` 이 없다**. git 이력이 필요한
스크립트는 `git_ref` 프리셋으로 돌린다.

**처음이라면 [사용법 가이드](docs/usage.ko.md)를 보자.** 주석을 단 화면으로 첫 잡까지 따라간다.

## Session commands

| 명령 | 무엇을 보여 주나 |
|---|---|
| `rcm run PRESET [-f k=v] [--ref REF] [--priority P] [--pool NAME] [--no-cache] [--by LABEL] [--no-join] [--no-wait] [--exclude PATTERN] [--dir DIR] [--timeout S] [--poll]` | 스냅샷 → 제출(같은 잡이 이미 돌면 합류) → 업로드(캐시가 켜져 있으면 바뀐 파일만) → 대기. `--ref` 는 `git_ref` 프리셋용이다. 스냅샷 없이 서버가 ref 를 받아 온다. `--priority low\|normal\|high`, `--no-cache` 는 전체 tarball, `--no-join` 은 절대 합류하지 않기, `--exclude` 는 `.rcmignore` 패턴 하나 추가, `--dir` 은 다른 디렉터리 스냅샷 |
| `rcm wait --job N [--timeout S] [--poll]` | 이벤트 스트림으로 따라간다. 스트림이 막히면 2초 폴링 |
| `rcm eta PRESET [-f k=v] [--priority P] [--pool NAME] [--json]` / `rcm eta --job N` | 대기 순번 · 앞 잡 수 · 대기 시간 · 예상 소요 · 끝나는 시각 · 그 추정의 confidence. 이미 도는 잡은 대기 대신 상태와 경과 |
| `rcm top [--watch N] [--json]` | 한 화면: 이유와 ETA 가 붙은 큐 · 최근 결과 · 중앙값 · 호스트 부하(CPU · 메모리 · 디스크 · GPU · top 프로세스) |
| `rcm jobs [--mine] [--state S] [--pool NAME] [--json]` | 대기 · 실행 · 최근 잡. `--mine` 은 토큰이 필요하고 합류한 잡도 포함한다 |
| `rcm logs N [--follow]` | 잡 로그(내 잡 · 내가 합류한 잡, 관리자 토큰이면 아무 잡) |
| `rcm artifacts N [--fetch --output DIR] [--force] [--resume]` | 잡이 만든 파일을 보고, 받는다. `rcm run --fetch-artifacts` 는 제출한 트리로 한 번에 가져온다 |
| `rcm presets [--json]` | 서버가 제공하는 프리셋과 입력 |
| `rcm discover [--json] [--timeout S]` | 이 네트워크의 rcm 서버들(mDNS). 발견으로 정해지면 `rcm check` 가 `(found on this network)` 를 붙인다 |
| `rcm cancel N` · `rcm pause` · `rcm resume` | 취소(합류자는 합류만 취소된다) · 큐 일시정지·재개(관리자) |
| `rcm bump N [--priority high]` | 대기 중인 잡의 우선순위 변경(관리자) |

모든 추정에는 `confidence` 가 붙는다. `high`(실제 5회 이상의 중앙값) · `med`(그보다 적음) ·
`low`(프리셋이나 기본 추측) · `group wait`(동시성 그룹에 막힘) · `overdue`. 모르는 값은 `0` 이
아니라 `—` 로 찍힌다.

## Presets and step markers

서버는 설정에 적힌 **프리셋**만 돌린다. 세션은 프리셋 이름과 입력을 보내고, 입력은
`RCM_INPUT_<NAME>` 환경 변수로 들어간다. 명령줄에 끼워 넣지 않는다.

```toml
[[presets]]
name = "gate"
argv = ["bash", "scripts/gate.sh"]      # 올라온 작업 공간 루트에서 돈다
pool = "default"                        # 이 프리셋의 잡이 도는 워커 풀(기본 "default")
pools = []                              # 세션이 --pool 로 고를 수 있는 추가 풀
timeout_seconds = 1200
expected_seconds = 480                  # 실제 표본이 쌓이기 전까지 쓰는 값
[[presets.inputs]]
name = "scope"
type = "choice"
choices = ["full", "commit", "fast"]
default = "full"
```

스크립트는 줄 머리에 마커를 찍어 진행을 알린다:

```
::rcm::steps::3            # 선택: 전체 스텝 수
::rcm::step::analyze       # 새 스텝 시작(앞 스텝은 끝난다)
::rcm::step-end::ok        # 선택: "ok" 또는 "fail"
::rcm::summary::all green  # 선택: 큐에 보이는 한 줄 결과
```

세션은 `--pool` 로 다른 풀에 보낼 수 있지만, 프리셋의 `pools` 에 적힌 풀만 고를 수 있다. 설정으로
할 수 있는 나머지는 — 푸시된 ref 를 받는 배포 프리셋 · 우선순위 · 스냅샷 캐시 · 원격 워커 · 알림 ·
보존 — [설정 문서](docs/configuration.md)에 있다.

## Web UI

`http://<빌드머신>:8787/` 을 브라우저로 연다(폰 포함). `rcm serve` 가 내주는 정적 파일 셋이고,
빌드 단계도 외부 자산도 없다. 첫 화면이 세 가지를 한눈에 답한다. **Your jobs**(🔑 로 토큰을 넣는다),
**Not moving**(손쓸 수 있는 원인만, 나쁜 것부터), **Host pressure**. 그 아래에 이유와 ETA
confidence 가 붙은 큐, 호스트 카드, 최근 결과, 추정 근거가 있다.

갱신은 이벤트 스트림으로 온다. 끊기면 10초마다 폴링하고, 30초 동안 응답이 없으면 **Lost
connection** 띠가 뜨고 나이만 계속 센다. 화면은 최신인 척하지 않는다. `#/jobs/N` 은 잡 하나로
바로 간다.

## Exit codes

| `rcm wait` 종료 코드 | 뜻 |
|---|---|
| 0 | 성공 |
| 1 | 실패(JSON 의 `failed_step` · `summary` 를 본다) |
| 2 | 취소되었거나 시간 초과 |
| 3 | **모름**: 서버 재시작으로 잡을 잃음 · 서버에 못 닿음 · `--timeout` 경과. 절대 실패로 치지 않는다 |

서버까지 가지도 못한 사용법 오류·검증 실패도 2 다(제출 전에 서버에 못 닿은 `rcm run` 포함).
`rcm wait` 은 서버가 안 닿으면 60초 동안 기다렸다가(`reconnecting…`) 3 으로 나간다. `rcm eta` ·
`rcm jobs` · `rcm top` 은 곧바로 `cannot reach <url>` 과 함께 3 이다.

## Security notes

- 쓰기(제출 · 업로드 · 취소)에는 전부 토큰이 필요하다. 서버는 SHA-256 해시만 저장한다. 토큰에는
  종류가 있다. `client` · `admin`(남의 잡 취소 · pause · bump) · `worker`(`/worker/*` 전용).
- 설정된 프리셋만 돈다. 셸 보간은 없다. 업로드는 Python `tarfile` data 필터로 푼다(절대 경로 · `..`
  · 작업 공간 밖 링크 금지).
- 발견 응답에는 서버 이름 · 포트 · 버전 · lane 수 · LAN 주소만 들어간다. 토큰 · 프리셋 · 경로는
  없다. 같은 LAN 의 누구나 「빌드 서버가 있다」는 사실은 알 수 있다.
- 서버는 따로 정하지 않으면 `127.0.0.1` 에만 열리고 **TLS 는 하지 않는다**. Tailscale 이나 TLS
  프록시 뒤에 둔다. 읽기는 그 네트워크에 기본으로 열려 있고(`read_auth = "basic"` 으로 잠근다),
  잡 로그는 언제나 그 잡의 토큰이나 관리자 토큰이 필요하다.
- sudo 없는 전용 OS 사용자로 돌린다. 빌드 비밀은 빌드 머신의 파일에 두고 프리셋이 읽게 한다.
  잡으로 보내지 않는다.

웹 UI 가 `localStorage` 에 무엇을 두는지까지 전부는
[빌드 머신 운영](docs/operating.md#security-notes)에 있다.

## Documentation

| 문서 | 내용 |
|---|---|
| [사용법 가이드](docs/usage.ko.md) · [English](docs/usage.md) | 첫 잡까지 한 단계씩, 주석 단 화면과 함께 |
| [Configuration](docs/configuration.md) | 프리셋 · 입력 · 마커 · 배포 프리셋 · 우선순위 · 스냅샷 캐시 · 풀과 원격 워커 · 알림 · 클라이언트와 워커 파일 |
| [Operating the build machine](docs/operating.md) | 서비스로 돌리기 · Docker · 업그레이드 · 보안 · 숫자가 틀릴 수 있는 이유 · 실제 머신에서의 수동 점검 |
| [CHANGELOG.md](CHANGELOG.md) | 사용자에게 보이는 모든 변경, 최신순 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 개발 · 규칙 · 브랜치 · 릴리스 |
| `PLAN.md` | 계획서 정본: 범위 · 결정 · 마일스톤 |

## Contributing

이슈와 PR 을 환영한다. `main` 과 `dev` 는 보호되어 있다. `dev` 에서 브랜치를 파고 `dev` 로 PR 을
보낸다. push 전에 `ruff check . && pytest` 를 돌리고, 런타임 의존성은 0 을 지키고, 문서는 같은 PR
에서 함께 고친다. 자세한 건 [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
