# M5t-a 실기 확인 (README 처음 설정, TR16 · TL12 · TD24 · TD25 · TN8 · 결정 352)

- 날짜: 2026-11-26
- 대상: `dev` 의 `e4b37c9e1bba8f6f6f39dd17fb1f2e879f74b6b4`. M5t-a 의 PR1~PR6 과 #274 가 머지된 뒤의 판이다.
  실기 하네스는 PR #276 의 head `dd06ccd9106162362f298ac846a4a3aac85b3cc9` 를 썼다.
- 명세: `docs/m5t-workplan.md` §5 의 PR7 행, §7 의 실기 행 TR16 · TD24 · TD25 · TL12 · TN8, 결정 352. M5t-a 는
  명세 제목대로 머신 역할, 첫 실행 설정, 분배 전용 코디네이터, 환경 진단, 요청자 알림을 들인 작업이다.
- 다시 돌리는 법: 2부는 워크트리에서 `bash scripts/acceptance/m5t_live.sh` 를 돌린다. 포트는 `M5T_PORT` ·
  `M5T_PORT2`, 시험 폴더는 `M5T_BASE`, 컨테이너 이미지는 `M5T_DOCKER_IMAGE` 로 바꾸고, 결정 352 줄은
  `RCM_LIVE_PROD_CONFIG` 를 줄 때만 돈다. Docker 없이 5초쯤 걸린다. 1부는 스크립트가 없다 — 같은 지시를 새
  격리 에이전트에게 다시 준다.

이 보고서는 두 부분으로 나뉜다. 1부는 오너가 요구한 시험이다. 서로 모르는 격리 에이전트 둘이 README 만 읽고
이 Mac 에 rcm 을 처음 설정했다. 둘 다 잡을 끝까지 돌렸고 더 나아가지 못하게 막힌 곳(BLOCKER)은 없었다.
헷갈리거나 사소하게 걸린 곳은 여덟 건이고 OB1~OB8 로 적었다. 2부는 명세의 실기 행을 하네스와 실기 검증자
둘로 확인했다. TR16 · TL12 · TD24 와 실패 경로는 통과했다. TD25 · TN8 · 결정 352 는 이번에 판정하지 못했고
행마다 이유를 표에 적었다.

시험은 모두 Apple Silicon Mac 한 대(macOS 27.2)에서 했다. 이 Mac 에는 운영 rcm 이 서비스로 돌며 포트 8787 을
쓴다. 그래서 모든 시험은 그 옆에 따로 만든 시험 설치로 했다. 운영에 쓴 흔적은 없다. 도구가 운영 쪽 파일을
읽은 곳은 1부 끝에 적었다.

## 1부 README 만으로 처음 설정

오너의 요구는 이랬다.

> 「격리에이전트로 이 맥에 이걸 처음설정하고 진행한다 했을 때 잘 하는지 테스트 시켜보면 좋겠는데? 격리에이전트는
> 소넷 high로 두개에게 시켜보자 사용법은 네가 지금 빌드 다 하고 리드미 업데이트 한 다음에 그걸로 시키도록 해」

에이전트 둘은 Sonnet 에 effort high 로 띄웠고 서로의 존재를 몰랐다. A 는 영어 `README.md` 를, B 는 한국어
`README.ko.md` 를 읽었다. 각자 그 문서가 링크한 문서만 더 읽었고 코드와 시험과 계획서는 읽지 않게 했다.

README 와 별개로 오너 쪽 안전 규칙을 따로 지시했다.

- 각자 자기 폴더를 쓴다. 포트는 A 가 8792, B 가 8793 이고 bind 는 127.0.0.1 이다.
- 광고를 끄고 시험 라벨을 쓴다. launchd 서비스는 설치하지 않는다.
- 시작하기 전에 `RCM_*` 환경 변수를 비운다. 운영 경로는 쓰지 않는다.
- 코드는 README 가 말하는 pipx 대신 GitHub 의 `dev` 를 새로 클론해 자기 venv 에 설치한다.

둘 다 README 의 First run 절에서 「이 머신에 rcm 이 이미 서비스로 돌면」 문장을 보고 usage 문서의 「도는 설치
옆의 시험 설치」 절로 갔다. 그 뒤로 따라 한 순서와 결과는 두 에이전트가 같았다.

```sh
rcm setup --role coordinator-worker --bind 127.0.0.1 --port <포트> --no-advertise --label <시험 라벨> \
  --config <폴더>/server.toml --data-dir <폴더>/data --yes      # exit 0, plist 는 <폴더>/service/ 에
rcm serve --config …                                           # listening on http://127.0.0.1:<포트> · lanes 1
(umask 077 && rcm token … add test > token) && chmod 600 token # 44바이트, 권한 600
rcm check                                                      # 전부 ok, 경고 하나(public url 비어 있음)
rcm doctor --role coordinator-worker                           # 4 ready
rcm run ok                                                     # #1 succeeded
rcm run ok --no-wait
rcm wait --job 2 --notify                                      # #2 succeeded on <머신> · 0s, exit 0
```

A 는 `--in 30s` 로 시작을 늦춘 잡 #3 도 돌렸고 결과는 같았다. 정리한 뒤에는 두 포트 모두 LISTEN 이 없었다.
`launchctl list | grep <시험 라벨>` 은 비어 있었고 `~/Library/LaunchAgents` 에 시험 파일이 없었다.

찾은 것을 표로 적었다. 「A·B」는 두 에이전트가 따로 같은 곳에 걸렸다는 뜻이다.

| ID | 등급 | 누가 | 내용 |
|---|---|---|---|
| OB1 | CONFUSING | A·B | 시험 setup 이 「this machine already has worker.toml」 경고를 낸다. 운영 설치의 파일을 시험 설치의 것처럼 말한다 |
| OB2 | CONFUSING | A·B | `rcm doctor --config <시험>` 의 서비스 줄이 운영 서비스를 보여 주고 「measured with the file given」 이라고 적는다 |
| OB3 | NIT | A | setup 이 찍는 `next —` 줄과 `to remove:` 줄에 `$(id -u)` 가 들어 있다. 이 Mac 의 에이전트 안전 훅이 막는 꼴이다 |
| OB4 | A 는 CONFUSING, B 는 NIT | A·B | `rcm version` 이 운영 클라이언트 설정과 캐시를 보여 준다. 시험 클라이언트를 시험 서버에 묶는 것이 `RCM_SERVER` 라는 말이 문서에 없다 |
| OB5 | CONFUSING | A | `rcm run` 이 지금 폴더를 올린다는 말이 시험 설치 블록에 없다 |
| OB6 | CONFUSING | B | 문서가 `rcm run` 의 짧은 줄과 `rcm wait` 의 끝 줄을 섞어 적는다 |
| OB7 | A 는 CONFUSING, B 는 NIT | A·B | `--notify` 는 성공했을 때 아무것도 찍지 않는다. `ok` 잡은 너무 빨리 끝나서 알림을 볼 틈이 없다 |
| OB8 | NIT | A·B | `export` 와 `unset` 은 한 셸 안에서만 유지된다 |

이와 따로 A 에서 훅 거절이 한 번 있었다. A 가 `cd … && rcm setup … ; echo` 꼴로 명령을 이어 붙였을 때다.
문서에 적힌 줄을 그대로 치면 평범한 한 명령이라 훅을 통과한다.

운영에 쓴 흔적은 없다. 도구가 운영 쪽을 읽은 곳은 셋이다. `rcm version` 은 클라이언트 캐시를, `rcm doctor` 는
서비스 파일을 읽었다. `rcm setup` 은 worker.toml 이 있는지 봤다. 이 셋은 OB4, OB2, OB1 과 같은 곳이다.

OB1~OB8 은 별도 PR `fix/setup-test-install-onboarding-findings` 로 고친다. 이 보고서를 쓰는 지금 그 PR 이
진행 중이다.

## 2부 명세의 실기 행

하네스는 `scripts/acceptance/m5t_live.sh` 이고 PR #276 이 들였다. 워크트리의 `.venv/bin/rcm` 만 쓴다. 시작할 때
HOME 과 XDG 폴더 넷을 시험 폴더로 바꾸고 `RCM_*` 를 비운다. 시험 라벨은 `com.example.rcm-m5t-live-<무작위>`
꼴이다. plist 는 시험 폴더에 두고 그 라벨만 bootstrap 하고 bootout 한다. 하네스가 실패하거나 TERM 을 받아도
trap 이 그 라벨을 내린다. 운영 경로인 `~/.config/rcm`, `~/.local/share/rcm`, `~/Library/LaunchAgents`,
`~/Library/Logs/rcm` 을 가리키는 값은 검사 함수가 거절한다.

실기 검증자 둘이 각자 시험 서버를 띄워 차례로 돌렸고 서로를 몰랐다. 검증자 1 은 포트 8796 과 8797 을 썼다.
검증자 2 는 포트 8798 과 8799 를 썼고 8798 을 다른 프로세스가 미리 잡고 있을 때의 실패 경로도 봤다.

명세 §7 이 정한 행의 뜻은 이렇다.

- TR16: 빈 환경에서 `rcm setup --role coordinator-worker` 로 설정하고 출력된 명령으로 띄운 뒤 `rcm check` 가 FAIL
  없이 끝나는지 본다.
- TL12: 로컬 레인이 없는 시험 서버, 곧 잡을 스스로 돌리지 않고 나눠 주기만 하는 서버에 일회용 워커를 붙인다.
  기본 풀 프리셋을 끝까지 돌려 결과가 요청자에게 돌아오는지 본다.
- TD24: 실제 Mac 의 워커 서비스 환경에서 `rcm doctor --role worker` 의 사람용 표와 `--json` 이 같은 판정을 내고
  측정한 PATH 를 적는지 본다.
- TD25: Docker 가 있을 때만 Linux 컨테이너 워커에서 doctor 를 돌린다.
- TN8: macOS 에서 `rcm wait --notify` 가 OS 알림을 띄우는지 본다.
- D352: 결정 352 다. `rcm setup` 이 이 머신의 rcm 서비스의 config, data_dir, venv, checkout 에 쓰려 하면 종료 1 로
  거절한다.

| ID | 검증자 1 | 검증자 2 | 근거(검증자가 직접 연 것) |
|---|---|---|---|
| TR16 | PASS | PASS | plist 를 `plutil -p` 로: Label 시험 라벨, ProgramArguments 는 워크트리 rcm serve --config <시험>/server.toml, HOME · 로그 · WorkingDirectory 모두 시험 폴더. server.toml: 시험 포트, bind 127.0.0.1, advertise false, data_dir 시험 폴더. launchd state running. rcm check ok 14~16행 · warn 1(public url) · FAIL 0. 끝난 뒤 `launchctl list \| grep m5t-live` 0, `~/Library/LaunchAgents` 에 m5t 파일 없음 |
| TL12 | PASS | PASS | lanes = 0 서버(로컬 레인 없음)의 잡 #1: succeeded, exit 0, worker = ran_on = m5t-tl12-worker; `rcm wait` exit 0, 끝 줄 `#1 succeeded on m5t-tl12-worker · 0s` |
| TD24 | PASS | PASS | doctor 사람용 표와 `--json` 을 손으로 행마다 대조 — 같은 판정, 측정 PATH `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` 양쪽, exit 0 양쪽. 검증자 2 메모: 시험 워커에 서비스 파일이 없어 「no service file found — measured with the default worker PATH」 — 명세 행의 「실제 워커 서비스 환경」 은 재지 않았다 |
| TD25 | UNKNOWN | UNKNOWN | 이 Mac 에 Docker 가 없다 |
| TN8 | UNKNOWN | UNKNOWN | 잡 #2 succeeded, `rcm wait --notify` exit 0, `warning: --notify` 줄 없음, osascript 감싸기 로그 exit 0. 배너는 사람이 화면에서 봐야 한다 — 기계는 볼 수 없다 |
| D352 | UNKNOWN | UNKNOWN | 하네스는 운영 설정 경로를 스스로 찾지 않고 `RCM_LIVE_PROD_CONFIG` 로만 받는다. 운영을 건드리지 않으려고 일부러 주지 않았다. 같은 규칙은 단위 시험(PR4)이 잠근다 |
| 실패 경로 | — | PASS | 8798 을 `python3 -m http.server` 가 잡은 채 하네스 실행 → exit 2 `port 8798 is in use`, 시험 폴더를 만들지 않음, 라벨 없음, 남은 프로세스 없음 |

TD24 는 두 검증자 모두 통과로 판정했다. 다만 검증자 2 의 메모대로 시험 워커에는 서비스 파일이 없었다. 그래서
명세 행이 말하는 「실제 워커 서비스 환경」은 이번에 재지 않았다.

검증자 1 은 하네스 스크립트를 읽고 운영에 닿을 수 있는 경로를 찾았다. 기본 경로, 비어 있는 변수가 진짜 경로로
펼쳐지는 경우, 시험 라벨이 아닌 bootout, HOME 을 바꾸지 않은 rcm 호출을 봤고 그런 경로는 없다고 판정했다.
부작용은 하나다. TN8 이 실제 macOS 알림을 한 번 띄운다. 그 알림을 띄우는 것이 TN8 의 목적이다.

검증 기록인 verify-round 와 verified 표식은 PR #276 의 댓글에 있다.

## 발견과 남은 일

리드가 처음 돌렸을 때 TL12 가 FAIL 이었다. 하네스가 `#1 queued` 줄을 끝 줄로 읽었기 때문이다. rcm 의 결함이
아니라 하네스의 결함이었고 커밋하기 전에 고쳤다.

하네스는 결함인지 판정을 보류한 것을 하나 찾았다. 가짜 프리셋을 두면 doctor 가 `LANG not set in the service
environment` 경고를 낸다. 그 프리셋은 env_passthrough 를 선언하지 않았다.

이 보고서 밖에 남은 일은 여섯이다.

- TN8 은 사람이 화면에서 배너를 보면 PASS 다.
- TD25 는 Docker 가 있는 머신에서 하네스를 다시 돌린다.
- D352 의 실기, 곧 하네스에 운영 경로를 주고 돌리는 일은 오너가 판단한다.
- TD24 를 서비스 파일이 있는 워커 환경에서 재는 변형을 검증자 2 가 제안했다.
- 온보딩에서 찾은 OB1~OB8 은 고침 PR 이 진행 중이다.
- doctor 의 LANG 경고가 결함인지는 아직 정하지 않았다.
