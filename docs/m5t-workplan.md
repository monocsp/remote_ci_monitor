# M5t-a 작업 명세: 머신 역할 · 첫 실행 설정 · 분배 전용 코디네이터 · 환경 진단 · 요청자 알림

> 2026-11-24 오너가 Codex 세션에서 rcm 을 쓰는 머신을 세 역할로 나누고 싶다고 했다. 원문을 그대로 옮긴다.
>
> 「맨 처음 시작하면, 이거를 rcm 빌드용으로 쓸건지 아닌지 확인하고, 빌드용으로 쓴다고 하면 설정을 알아서 완료하고,
> 다른 rcm 제어하는곳에서 이걸 연결하면 거기에서 알아서 분배하는걸 만들고싶어 즉 1. rcm 으로 작업 보내는 컴퓨터(유저가
> 사용) 2. rcm 으로 작업 받는 컴퓨터 + 작업을 다른 유휴중인 컴퓨터로 보내는 작업 3. rcm 작업을 진행해서 결과 전달하는
> 컴퓨터 이렇게 3가지로 나눈고싶은거야」
>
> 같은 날 오너는 두 마디를 더했다. 하나는 「이 컴퓨터에서는 4를 하고싶어」다. 여기서 4 는 2 와 3 을 함께 하는 머신이다.
> 다른 하나는 「Rcm워커 다 한 다음에 어떻게 알려주는거까지 해야지」다. Codex 는 그날 초안을 냈다(§10).
>
> 2026-11-25 오너는 「진행하도록 해 상세하게 계획하고」라고 지시했다. 같은 날 요구를 둘 더 했다.
>
> 1. 「RCM 설치하는 OS에 따라서 rcm worker가 어떻게 빌드하고 어떻게 테스트하고 등등을 체크해야해 그리고 어떤것들을
>    설치해야하는지 사용하기전에 그런것들을 알려주는것도 필요해」
> 2. 「해당 스킬 가져와서 이 프로젝트에 맞출때 민감데이터나 API 키 등 그런것들은 없어야해 … 이 프로젝트는 public
>    일거니까」
>
> 결정 번호는 239 부터다. M5q 가 149~174, M5r 이 175~200, M5s 가 201~238 을 쓴다(`docs/m5s-workplan.md`).
>
> 이 판은 여섯째 판이고 범위를 줄인 M5t-a 명세다. 첫째 판부터 셋째 판까지는 M5t 전체를 한 문서에 담았다. 격리 반박 두 명이
> 세 라운드에 걸쳐 그 판들을 읽었다. 반박 A 는 프로토콜과 정확성을, 반박 B 는 설치와 보안과 오너 의도를 봤다. 3라운드 뒤의
> 근본 원인 분석은 한 문서에 하위 시스템 일곱이 들어 있고 같은 사실이 5~7곳에 되풀이된다고 지적했다. 오너는 2026-11-25
> M5t 를 셋으로 나누라고 답했다. 이 문서는 그 가운데 M5t-a 만 담는다. 결과 고리 프로토콜(M5t-b)과 짝짓기 · 서버 신원
> 보안(M5t-c)은 따로 쓰는 명세이고 거기로 넘기는 것은 §12 에 모았다. 셋째 판 전문은 레포 밖에 보관했다. 라운드마다 고친
> 것은 §13 에 반박 id 별로 모았다. 넷째 판은 M5t-a 만 담은 첫 판이고 4라운드 반박을 받았다. 다섯째 판은 그 반박을
> 반영했고 5라운드 반박을 받았다. 5라운드에서도 `local_only` 를 거르는 경로가 또 빠졌다. 그래서 이 판은 오너의 답에 따라
> 그 거르기를 한 곳으로 모아 다시 세웠고, 고친 diff 만 검증자 둘이 본다(§13). 오너가 정한 것은 §1 에 적었고 나머지 결정은 추천값이다. 오너가 정해야 진행되는 것은
> §11 에 ⛔ 와 Q 번호로 표시했다.

## 0. 한 줄

새 머신에 rcm 을 깔고 `rcm` 을 치면 rcm 이 역할(요청 · 분배 · 실행 · 분배+실행)을 묻고 설정을 끝낸다. 실행 머신은
이 OS 에서 무엇을 빌드하고 시험할 수 있는지, 무엇을 더 깔아야 하는지 쓰기 전에 알려 준다. 분배만 하는 코디네이터를
허용하고 원격 워커에서 돌면 안 되는 프리셋에는 `local_only` 표식을 단다. 잡이 끝나면 요청자는 어느 머신에서 돌았는지와
산출물 상태를 본다.

## 1. 오너 요구와 이 명세

오너가 원한 것마다 Codex 초안이 적은 것과 이 명세가 정한 것을 나란히 둔다. Codex 초안은 참고 재료다.

| 원하는 것 | Codex 초안(2026-11-24) | 이 명세 |
|---|---|---|
| 1. 처음 시작하면 빌드용으로 쓸지 묻고 설정을 알아서 끝낸다 | 인자 없는 `rcm` 이 터미널에서만 묻고 `rcm setup` 이 기존 `init` 셋을 재사용한다 | 받는다. 묻는 조건을 stdin 과 stderr 가 모두 TTY 일 때로 명시하고 역할은 있는 설정 파일에서 읽어 낸다(결정 239·240·241) |
| 2. 다른 rcm 제어 머신에 연결하면 거기서 알아서 분배한다 | 토큰은 사람이 옮긴다. 일회용 짝짓기 코드는 별도 보안 명세로 미룬다 | M5t-a 는 지금 방식을 setup 이 안내한다. 사람이 코디네이터에서 `rcm token add --worker` 로 만든 토큰을 옮기고 워커에는 코디네이터의 URL 을 적는다. 짝짓기 창과 서버 신원은 M5t-c 로 넘겼다(결정 252). 프리셋에 `local_only` 표식이 없으면 지금처럼 원격 워커도 그 잡을 받는다(결정 254) |
| 3. 세 역할(보내는 컴퓨터 · 받아서 나누는 컴퓨터 · 실행하는 컴퓨터) | 역할 이름 넷(`client` · `coordinator` · `worker` · `coordinator-worker`), 분배 전용은 `lanes = 0` | 받는다(결정 239). 분배 전용에 따라오는 일을 모두 적는다(결정 243, ⛔ Q5) |
| 4. 이 컴퓨터는 2 와 3 을 함께 | `rcm serve` 하나로 충분하고 `rcm worker` 를 따로 띄우지 않는다 | 받는다. 이미 둘째 워커를 쓰는 구성은 허용하고 경고만 한다(결정 242) |
| 5. 워커가 끝낸 뒤 요청자에게 알리기 | 반환 경로와 시험 12개를 적었지만 이미 있는 기능을 새로 만들 것으로 적었다 | M5t-a 는 요청자 알림만 한다. 사람용 마지막 줄의 실행 머신, 새 키 `ran_on`, 이미 있는 산출물 상태의 사람용 줄과 경고, `RCM_MACHINE`, `rcm wait --notify` 다(결정 248 e·f, ⛔ Q3). 망이 끊겨도 결과가 돌아오게 하는 결과 고리는 M5t-b 다(결정 253·255) |
| 6. OS 에 따라 무엇을 빌드·시험할 수 있는지, 무엇을 깔아야 하는지 쓰기 전에 알리기 | 없음(11-25 요구) | 환경 진단 `rcm doctor` 를 새로 만든다(결정 246·247, ⛔ Q6·Q7·Q8). 기본 설치에서 알려 주는 범위는 표 아래에 적었다 |
| 7. 공개 레포라서 민감 데이터와 API 키가 없어야 한다 | 없음(11-25 요구) | §8 개인정보 점검을 이 명세와 이후 PR 모두에 건다 |

오너가 정한 것은 다섯이다. 2026-11-24 에는 운영 빌드 머신을 역할 4(`coordinator-worker`)로 쓰기로 했다. 2026-11-25 에는
셋째 판을 읽은 뒤 질문 넷에 답했다. 답은 원문 선택지 그대로 옮긴다. 자세한 내용은 §11 의 결정 252~255 에 있다.

| 질문 | 오너 답 | 이 명세 |
|---|---|---|
| 범위 | 「셋으로 나눈다」 | 이 문서는 M5t-a 만 담는다. M5t-b 와 M5t-c 는 상태 전이 표와 위협 모델부터 쓰는 별도 명세다(결정 252) |
| 끊김 정책 | 「되돌릴 수 없는 것만 멈춤」 | M5t-b 가 구현한다(결정 253) |
| 원격 금지 | 「프리셋에 명시 표식」 | M5t-a 가 작은 PR 로 구현한다(결정 254) |
| 동시 세션 | 「지원 안 함」 | M5t-b 가 구현한다(결정 255) |

이 설계가 오너의 말과 다른 곳이 다섯 있다.

첫째, 워커를 붙이는 데 사람 손이 두 번 든다. 오너는 「다른 rcm 제어하는곳에서 이걸 연결하면」이라고 했다. M5t-a 에서는
코디네이터에서 사람이 토큰을 만들고 워커에서 사람이 그 토큰과 서버 URL 을 넣는다. 자동 가입을 막은 기존 결정(M5n §13)은
그대로 둔다. 새 워커의 풀도 지금처럼 `rcm init worker --pool` 이 정하고 기본은 짧은 호스트 이름이다. 그대로 두면 그
워커가 받는 일은 `any-capable` 프리셋뿐이고 doctor 가 그 사실을 알린다(§4.4). 손을 줄이는 짝짓기 창과 창에서 풀을
정하는 일은 M5t-c 다.

둘째, 기본 설치에서 「무엇을 깔아야 하는지」 알려 주는 것은 rcm 자신의 의존성뿐이다. 오너의 11-25 첫째 요구에 비하면 범위가
좁다. 프리셋이 쓰는 도구(Flutter, Xcode, JDK 같은 것)의 설치 안내는 코디네이터 관리자가 서버 설정에 `[[tools]]` 를 써야
나온다. 서버 설정 템플릿에는 주석으로 단 예시 카탈로그만 있다(§4.5).

셋째, 요청 머신의 OS 알림은 `rcm wait --notify` 에만 붙는다. `rcm run` 도 기본으로 잡을 기다리지만(`cli.py:5065`) 이 명세는
거기에 `--notify` 를 더하지 않는다.

넷째, 망이 끊긴 동안의 결과 고리는 이번에 고치지 않는다. 원격 워커와 망이 60초 넘게 끊기면 지금처럼 잡이 `lost` 가
된다(§3 E10). 끊김 유예와 워커의 자기 멈춤은 M5t-b 다.

다섯째, setup 은 토큰을 만들지 않고 서비스도 올리지 않는다. setup 은 명령을 출력하고 사람이 두 가지를 실행한다. 하나는
코디네이터에서 토큰을 만드는 일이고 다른 하나는 서비스를 올리는 일이다(결정 241, ⛔ Q9).

Codex 초안을 사실과 대조했더니 「새로 만들 것」으로 적은 것 가운데 상당수가 이미 있었다. claim, heartbeat, 스텝 마커,
SSE, 종료 코드 0~4 와 5, 산출물 업로드, finish 의 403·409 가 그렇다. 그래서 §3 은 「이미 있는 것 / 고칠 것 / 새로 만들 것」을
표로 가른다.

## 2. 범위

### 하는 것

| 묶음 | 내용 | 결정 |
|---|---|---|
| 역할과 첫 실행 | 역할 넷, 있는 설정 파일에서 역할을 읽어 내기, 인자 없는 `rcm` 의 첫 실행 질문, `rcm setup` · `rcm setup --role <r> --yes`, 서버용 서비스 파일 생성, 역할마다 토큰을 얻는 길 안내 | 239~242 |
| 분배 전용 코디네이터 | `lanes = 0` 허용과 거기 따라오는 `@local` · 헬스 · 화면 손질 | 243 |
| 원격 금지 표식 | 프리셋 키 `local_only`, 그 프리셋의 후보 머신을 `@local` 하나로 두는 후보 함수와 그 결과를 쓰는 경로들(4.3), 후보가 빈 제출과 원격 recheck 의 409, 설정 검증, coordinator 진단의 「로컬 레인 필요」 | 254 |
| 환경 진단 | `rcm doctor`(역할 넷), `[[tools]]` 설치 안내, 호스트 사실 더 모으기, `rcm worker --check` 두 줄, 이 머신에 올 프리셋 수 알리기, 서버 설정 템플릿의 툴체인 카탈로그 | 246·247 |
| 요청자 알림 | `rcm wait` 사람용 마지막 줄과 `ran_on`, 이미 있는 산출물 상태의 경고 줄, `[[notify]]` 의 `RCM_MACHINE`, `rcm wait --notify` | 248 e·f |
| 범위 나누기 | M5t 를 M5t-a · M5t-b · M5t-c 로 나누고 뒤의 둘에 넘길 것을 적는다 | 252 |
| 개발 방식 | 시나리오 표와 시험 이름의 ID, 격리 검증 | 251 |

M5t-a 는 가능하면 DB 를 바꾸지 않는다. 지금 설계에는 DB 판을 올리는 항목이 없다.

### 안 하는 것

| 안 하는 것 | 근거 |
|---|---|
| 결과 고리 프로토콜(워커 세션, claim 표식, 로그 오프셋, 끊김 유예, 워커의 자기 멈춤, finish 큐) | M5t-b 로 넘겼다(결정 252 · 253 · 255, §12) |
| 짝짓기 창, 서버 신원 확인, 토큰에 풀 묶기, 인증 예외 라우트 | M5t-c 로 넘겼다(결정 252, §12) |
| 역할을 새 파일에 적기 | 상태를 둘로 두면 어긋난다(결정 239) |
| 비대화 실행에서 질문하기 | 결정 240 |
| 서비스 등록과 시작. setup 은 명령만 출력한다 | M5n §9 유지(결정 241, ⛔ Q9) |
| 역할 2·4 에서 bind 의 기본 선택지를 127.0.0.1 로 두기 | 127.0.0.1 이면 mDNS 광고가 꺼지고 워커가 닿지 못한다(결정 241, ⛔ Q10) |
| 사람이 만들지 않은 토큰으로 워커가 가입하기 | M5n §13 의 자동 발급 · 자동 가입 금지를 유지한다 |
| `init client` 가 `auto` 를 받게 고치기 | 클라이언트는 지금처럼 서버 URL 을 명시한다(결정 241) |
| 토큰으로 읽기를 막는 새 `read_auth` 모드 | 추천은 이 마일스톤 밖에서 다음에 하는 것이다(결정 241, ⛔ Q14) |
| 배치 기본 `pool`(결정 97)을 바꾸기 | 결정 97 유지. 원격 금지는 배치가 아니라 프리셋 표식으로 한다(결정 254) |
| 프리셋이 비밀을 쓰는지 서버가 스스로 판정해 원격을 막기 | 오너는 프리셋에 명시 표식을 두라고 답했다(결정 254) |
| 코어에 도구 이름이나 버전 비교 문법 넣기 | PLAN 이식성. 도구 예시는 서버 설정 템플릿의 주석 블록에, 버전 요구는 `checks` 점검 프리셋으로(결정 246) |
| 설치 안내 문구 실행하기, sudo 로 설치하기 | 신뢰는 코디네이터 관리자에게 있다. rcm 은 보여 주기만 한다(결정 246) |
| 진단 결과를 서버로 보내거나 자격 판정을 바꾸기 | 진단은 조언이다(결정 246) |
| `checks` 없는 프리셋에 「증명 뒤 받기」 강제 | 결정 91~93 유지(결정 247, ⛔ Q6) |
| 산출물 상태 칸을 새로 만들기 | 이미 있는 `artifacts.state` 를 쓴다(결정 248 e) |
| 잡 JSON 의 `worker` 칸 바꾸기, 실행 머신을 저장하는 새 칸 | 로컬 레인을 null 로 적는 관례를 지킨다. `ran_on` 은 계산한다(결정 248 f) |
| `rcm check` 와 `rcm wait` 의 종료 코드 바꾸기 | 결정 246 · 248 e |
| 서버 webhook 으로 요청자에게 알리기 | 요청 머신 알림은 클라이언트 쪽이다(결정 248 f) |
| Windows | PLAN 「반드시 지킬 것」 |

## 3. 지금 코드의 사실

기준은 origin/dev v0.10.0(`a4d48de`)이다. 모듈 파일은 `src/remote_ci_monitor/` 를 빼고 적었다(`cli.py:5048`).
`src/` 로 시작하는 경로도 `src/remote_ci_monitor/` 를 줄인 것이다. 지금 DB 는 v25(`store.py:88`)이고 CHANGELOG 의
`[Unreleased]` 는 비어 있다.

이 명세가 쓰는 내부 용어를 먼저 풀어 둔다. 코디네이터는 잡을 받아 큐에 두고 어느 머신이 돌릴지 정하는 서버(`rcm serve`)다.
워커는 서버에서 잡을 집어 가 실제로 돌리는 머신(`rcm worker`)이다. 레인은 한 머신이 잡을 동시에 돌리는 칸이고 `lanes` 가
그 수다. claim 은 빈 레인이 대기 잡을 하나 집어 가는 일이다. heartbeat 는 워커가 살아 있다고 서버에 주기적으로 보내는
보고다. `lost` 는 결과를 모르는 채로 닫힌 잡의 상태다. 프리셋의 `placement = "any-capable"` 은 해낼 수 있다고 판정된
아무 머신에서나 돈다는 뜻이고 기본값 `pool` 은 지정한 풀에서만 돈다는 뜻이다. `[[probes]]` 는 머신의 도구 지문을 재는
명령이고 `checks` 는 그 머신이 현재 지문으로 통과해야 하는 점검 프리셋이다. `@local` 은 서버 자신의 로컬 레인을 머신
목록에서 부르는 이름이다. mDNS 는 같은 네트워크에서 서버를 이름으로 찾는 방법이다.

### 3.1 첫 실행 · init · 서비스 파일

| # | 지금 | 근거 | 분류 | 이 명세 |
|---|---|---|---|---|
| A1 | 인자 없는 `rcm` 은 usage 오류로 종료 2 다. 역할을 묻는 흐름이 없다. `_follow_first` 는 command 가 None 이면 건너뛴다 | `cli.py:5048`(required=True), `cli.py:5544`, `follow.py:431-437` | 새로 만들 것 | 첫 실행 질문(결정 240) |
| A2 | 대화형 입력은 `_release_ask` 하나뿐이다. 질문은 stderr 로 내고 EOF 면 usage 오류다. 공용 마법사 함수와 비TTY 규칙이 없다 | `cli.py:3039` | 새로 만들 것 | 공용 질문 함수와 비TTY 규칙(결정 240) |
| A3 | `init server` 는 질문 없이 템플릿을 그대로 0644 로 쓴다. bind · 토큰 · 서비스 파일은 다루지 않는다 | `cli.py:3632-3665` | 이미 있음 | setup 이 조립한다(결정 241) |
| A4 | `init client --server URL` 은 0600 으로 쓴다. `auto` 는 거절한다. client.toml 자체는 `auto` 를 받는다. 토큰은 `RCM_TOKEN` 으로 주라고 안내한다 | `cli.py:5479-5480, 3637-3646, 3406-3414, 3661`, `config.py:365-368` | 이미 있음 | setup 이 조립한다. `init client` 의 `auto` 거절은 그대로 둔다. 클라이언트 토큰은 지금처럼 사람이 옮긴다(결정 241) |
| A5 | `init worker` 는 server 기본이 `auto`, pool 기본이 짧은 호스트 이름, lanes 1~64, 0600 이다. 토큰은 다루지 않고 `rcm token add <name> --worker` 로 받으라고 안내한다. pool 이 호스트 이름이라 받을 수 있는 일은 `any-capable` 프리셋뿐이다 | `cli.py:3588-3629`, `:3605`, `:3624`, `:3627` | 이미 있음 | setup 이 조립하고 토큰을 얻는 길을 안내한다. 풀은 `init worker` 규칙 그대로다. doctor 가 이 머신에 올 프리셋 수를 알린다(결정 241 · 246) |
| A6 | 서비스 파일은 워커용만 만든다. 워커 라벨은 `com.remote-ci-monitor.worker` 다. launchctl · systemctl 은 부르지 않는다(M5n §9). `examples/` 는 sdist 에만 있다 | `cli.py:3494-3585`, `:3437`, `pyproject.toml:50-60` | 고칠 것 | 서버용 서비스 파일을 더한다. 등록·시작은 여전히 안 한다(결정 241) |
| A7 | 있는 파일은 덮지 않는다. 쓰기는 원자적이다 | `cli.py:3624, 3647, 3561-3562`, `cli.py:3417-3429` | 이미 있음 | 그대로 쓴다. 파일마다 `--force` |
| A8 | 역할을 적는 곳이 없다. 설정 파일 셋으로 나뉘어 있을 뿐이다 | — | 새로 만들지 않음 | 있는 설정 파일에서 역할을 읽어 낸다(결정 239) |

### 3.2 bind · mDNS

| # | 지금 | 근거 | 분류 | 이 명세 |
|---|---|---|---|---|
| B1 | bind 기본은 127.0.0.1 이고 `init` 은 bind 를 묻지 않는다 | `templates/server.toml:10`, `cli.py:5412` | 이미 있음 | setup 이 역할 2·4 의 주소를 고르게 한다(결정 241) |
| B2 | 광고는 명시값이 없으면 bind 가 루프백이 아닐 때만 켠다. TXT 는 v · name · lanes 이고 레인이 0 이면 `lanes=0` 이다. 기본 설치에서는 꺼져 있다(결정 33) | `config.py:371-376`, `server.py:739-757` | 이미 있음 | 분배 전용에서도 그대로 쓴다(결정 243). TXT 는 바꾸지 않는다 |
| B3 | macOS 로컬 네트워크 권한은 사람이 한 번 허락해야 한다 | `docs/operating.md:80-88` | 이미 있음(문서) | 진단이 rcm 자신의 요구로 알린다(결정 246) |
| B4 | 워커가 서버를 찾는 기능이 있다. 서버가 여럿이고 `server_name` 이 없으면 usage 2 다. mDNS 로 다시 찾은 주소로는 조용히 옮겨 간다 | `cli.py:4390`, `remote_worker.py:71, 653-664`, `:653-661` | 이미 있음 | `rcm worker` 의 찾기는 그대로 둔다. 서버 URL 고정과 서버 신원 확인은 M5t-c 다. doctor 와 `worker --check` 는 mDNS 로 찾은 서버에 인증된 요청과 프로브를 보내기 전에 사람의 확인을 받는다(결정 246) |

### 3.3 레인 0

| # | 지금 | 근거 | 분류 | 이 명세 |
|---|---|---|---|---|
| C1 | 설정이 `lanes ≥ 1` 을 요구한다 | `config.py:1246-1247` | 고칠 것 | 0 을 허용한다(결정 243) |
| C2 | 로컬 레인 스레드는 0 개여도 문제가 없다 | `worker.py:731` | 이미 있음 | — |
| C3 | 풀 레인 수는 원격 워커가 없으면 `worker_down`, ETA null 이다. 정상 동작이다 | `server.py:931-955` | 이미 있음 | — |
| C4 | 아무것도 돌 수 없어도 헬스는 200 ok 다(fail-open) | `server.py:5363-5376` | 고칠 것 | 프리셋 단위의 `capacity` 정보를 싣는다. HTTP 상태는 그대로다(결정 243, ⛔ Q5) |
| C5 | `@local` 머신은 언제나 alive=True 다 | `capabilities.py:483-494` | 고칠 것 | lanes 0 이면 살아 있지 않다 |
| C6 | 레인이 없어도 로컬 프로브를 잰다 | `server.py:657-660`, `capabilities.py:294-330` | 고칠 것 | 생략한다 |
| C7 | `local_accept_any` 기본이 true 라서 제출 응답의 `eligible_now` 에 `@local` 이 찍힌다. 틀린 정보다 | `capabilities.py:740-770, 840-870`, `config.py:152` | 고칠 것 | `@local` 을 뺀다 |
| C8 | 자동 점검이 `@local` 을 겨냥하면 아무도 못 집는 점검 잡이 큐에 남는다 | `capabilities.py:872-915` | 고칠 것 | `@local` 을 겨냥하지 않는다 |
| C9 | 부하 게이트와 결정 39(M5f)에 「서버 레인 1」 문장이 있다 | `server.py:726` | 고칠 것 | 결정 39(M5f)의 문장을 고친다 |
| C10 | 화면이 `lanes 0/0 busy` 처럼 어색하게 그린다 | `core/render_text.py:1003-1009`, `web/app.js:185-194, 768` | 고칠 것 | 표기를 손본다 |
| C11 | 샘플러는 언제나 돌아서 코디네이터의 부하가 기본 풀 압력처럼 보인다 | — | 고칠 것 | 샘플러는 계속 돌고 호스트 카드에 「잡을 돌리지 않는 머신」을 표시한다 |
| C12 | `pools_without_workers` 는 lanes 와 상관없이 기본 풀을 늘 건너뛴다 | `remote_workers.py:366-374` | 고칠 것 | `lanes ≥ 1` 일 때만 기본 풀을 뺀다. TL13 이 이 수정을 잠근다. `capacity` 는 풀이 아니라 프리셋 단위로 센다(결정 243) |

### 3.4 워커 붙이기와 원격 배치

| # | 지금 | 근거 | 분류 | 이 명세 |
|---|---|---|---|---|
| D1 | 토큰은 서버 머신에서만 발급한다. `cmd_token` 이 DB 를 직접 열고 `/tokens` 라우트는 없다. 그래서 사람이 토큰을 옮긴다 | — | 이미 있음 | 그대로 쓰고 setup 이 안내한다(결정 241). 짝짓기 창은 M5t-c 다 |
| D2 | 자동 발급과 자동 가입이 없다. M5n §13 이 일부러 막았다 | `docs/m5n-workplan.md:647-656` | 이미 있음 | 유지한다. 개정할지(3판의 Q1)는 M5t-c 가 정한다 |
| D3 | 등록은 버전이 정확히 같아야 한다(409 + `server_version`). 다시 등록하면 그 토큰의 옛 잡을 먼저 `lost` 로 닫는다 | `remote_workers.py:377-435`, `:390-398`, `:400-408` | 이미 있음 | D4 의 버전 비교 행으로 등록 전에 알린다. 재시작 순서와 워커 프로토콜 판은 M5t-b 다 |
| D4 | `rcm worker --check` 에 버전 비교 행이 없다 | `cli.py:4641-4686` | 고칠 것 | 버전 비교 행을 더한다(결정 246) |
| D5 | 프로브와 능력 보고가 있다 | `server.py:4325, 4329`, `capabilities.py:332` | 이미 있음 | — |
| D6 | M5q 의 버전 따라가기는 v0.8.0 으로 출시됐다. PLAN.md 는 아직 「진행 중」이고 결정 표에 149~238 이 없다. editable 설치는 따라가지 않는다 | `CHANGELOG.md:343-362`, `remote_workers.py:389-397, 955-957`, `PLAN.md:624` | 고칠 것(문서) | PR0 이 PLAN.md 의 M5q 상태를 고친다 |
| D7 | 결정 97 로 배치 기본은 `pool` 이다. 붙은 워커에 `pool` 프리셋은 가지 않는다. `any-capable` 도 보고가 새롭고 도구 판정과 `checks` 를 지나야 간다 | `cli.py:3605`, `examples/server.toml:286`, `capabilities.py:758-760` | 이미 있음 | 결정 97 은 유지한다. 진단이 이 머신에 올 프리셋과 후보를 알린다(결정 246) |
| D8 | `git_ref` 잡은 워커 자신의 `[[repos]]` 가 있어야 한다. 없으면 `repo_missing` 이다 | `remote_worker.py:863-890` | 이미 있음 | 진단이 git 요구를 본다(결정 246). `[[repos]]` 는 자동으로 채우지 않는다. 원격 주소와 자격 증명은 그 머신 주인이 정할 일이라, doctor 가 `repo_missing` 이 날 프리셋과 넣어야 할 저장소 이름을 알려 주기만 한다 |
| D9 | `/worker/*` 라우트는 인증을 먼저 본다 | `server.py:6153-6155` | 이미 있음 | 그대로 둔다. M5t-a 는 인증 예외 라우트를 만들지 않는다 |
| D10 | 프리셋을 원격에서 빼는 방법이 없다. 원격 워커의 claim 은 `_claimable` 의 후보 조건으로 잡을 고르는데 그 조건 어디에도 프리셋을 원격에서 빼는 것이 없다. 릴리스 프로파일의 비밀은 로컬 레인에서만 주입되고 원격 워커 쪽에는 그 호출이 없다. 그래서 기본 풀에 붙인 원격 워커가 릴리스 프리셋을 집을 수 있다. 이때 비밀이 새지는 않는다. 그 잡이 비밀 없이, 또는 그 머신의 자격 증명으로 도는 배치 오류다 | `remote_workers.py:543-553`, `store.py:3371-3422`(후보 조건 `:3379-3398`), `worker.py:548-550` | 고칠 것 | 프리셋 표식 `local_only` 를 단 프리셋의 후보 머신을 `@local` 하나로 둔다. 원격 claim 도 그 후보 함수의 결과를 쓴다(결정 254, 4.3, PR1) |

### 3.5 결과가 요청자에게 가는 길

이 표는 M5t-a 가 쓰는 사실만 남겼다. 결과 고리 프로토콜에 쓰는 사실(claim, phase 보고, 로그 업로드, 버퍼, finish)은
M5t-b 의 재료다(§12).

| # | 지금 | 근거 | 분류 | 이 명세 |
|---|---|---|---|---|
| E6 | 워커는 finish 전에 산출물을 올린다. 산출물이 실패해도 잡은 `succeeded` 다. 산출물 상태는 이미 `job_artifacts.state` 와 잡 문서의 `artifacts.state` 에 13값(pending · ready · empty · failed · unknown 등)으로 있고 wait JSON 에도 보인다. 사람용 줄에는 없다. 종료 5 는 `--fetch-artifacts` 를 줄 때만 난다 | `remote_worker.py:1000-1044`, `remote_workers.py:696-750`, `server.py:646-649`, `core/artifacts.py:37-49`, `server.py:3168-3189`, `core/status.py:573-598` | 고칠 것 | 새 칸을 만들지 않는다. 이미 있는 상태를 사람용 줄과 경고 줄에 쓴다(결정 248 e). 재시작을 넘긴 묶음 재업로드는 M5t-b 다 |
| E10 | 서버 재시작 때 `lost` 가 되는 것은 로컬 잡뿐이다. 망이 60초 넘게 끊기면 `lost` 가 되고 요약 코드가 `worker_unreachable` 이다. 워커는 로그 · phase · finish 의 409 를 모두 서버가 닫은 것(`closed_by_server`)으로 읽고 프로세스를 죽인다 | `remote_workers.py:994-1019`, `:1002`, `core/outcome.py:211`, `web/i18n.js:776`, `remote_worker.py:151-154, 187-188` | 이미 있음 | M5t-a 는 바꾸지 않는다. 끊김 정책은 M5t-b 가 구현한다(결정 253) |
| E11 | `rcm wait` 의 JSON 은 잡 문서와 `wait_exit_code` 다. 종료 코드 5(전달 실패)가 따로 있다 | `PLAN.md:117`, `cli.py:108-113, 1244-1370` | 이미 있음 | 계약 0~5 를 바꾸지 않는다 |
| E12 | SSE 가 `/jobs/{id}/events` 와 `/events` 에 있다. 연결이 16개를 넘으면 폴링한다. 요청자가 연결을 들고 있어야 듣는다 | — | 이미 있음 | — |
| E13 | `[[notify]]` 는 관리자가 상태와 프리셋으로만 고른다. 요청자별 전달이 없고 `RCM_WORKER` 도 없다. `RCM_REQUESTER` 는 이미 있다 | `notify.py:133-237`, `core/notify.py:36-45, 150-178`, `core/notify.py:154`, `runner.py:218` | 고칠 것 | `RCM_MACHINE` 하나를 더한다(결정 248 f) |
| E14 | 누가 돌렸는지는 잡 JSON 의 `worker` · `host_key` 에만 있다. stderr · top · 웹에는 없고 로컬 레인은 `worker=null` 이다 | `core/status.py:356-360` | 고칠 것 | 사람용 마지막 줄과 새 키 `ran_on` 에 실행 머신을 적는다. `ran_on` 은 저장 칸 없이 서버가 잡 문서를 만들 때 계산한다(4.6). `host_key` 는 sha256 앞 16자라서 그것만으로는 이름을 계산할 수 없다. `machine` 은 제출 본문의 대상 머신과 겹치므로 쓰지 않는다. `worker` 칸의 null 관례는 그대로 둔다(결정 248 f) |

### 3.6 환경 진단에 쓰는 사실

M5n 이 「재고를 보고하고 판정하는」 길을 이미 깔았다. 워커는 등록하기 전에도 `GET /worker/spec` 으로 모든 프리셋의
`requires` 와 `[[probes]]` 를 받는다. `rcm worker --check` 가 그 명세를 받아 바로 잰다. 아래 표는 빠진 여섯(G1~G6)과
doctor 가 그대로 읽을 서버 쪽 측정값(G7)이다.

| # | 지금 | 근거 | 분류 | 이 명세 |
|---|---|---|---|---|
| G1 | 설치 안내 칸이 없다. 잡 로그는 `required tool fvm: missing` 까지만 찍고 판정 문구도 `fvm missing` 까지다 | `src/runner.py:177-183`, `src/core/render_text.py:534-560` | 새로 만들 것 | `[[tools]]`(결정 246) |
| G2 | `/worker/spec` 의 프리셋 항목에 `placement` · `pool` · `checks` · `description` 이 없다. 워커는 자기에게 올 프리셋을 모르고 다른 풀 전용까지 「n/24 ok」로 센다. 프리셋 env 도 통째로 준다 | `src/capabilities.py:254-272`, `src/server.py:5774-5778, 4324-4326`, `src/cli.py:4696-4728` | 고칠 것 | 키를 더한다. 옛 워커는 무시한다. 후보 함수가 원격 워커를 빼므로 `local_only` 프리셋은 원격 워커에 보내지 않는다(결정 254, 4.3). 토큰에 묶인 풀로 거르는 일은 M5t-c 다(결정 246) |
| G3 | `worker --check` 는 이 셸 환경으로 잰다. 서비스 PATH(`WORKER_SERVICE_PATH`)와 다를 수 있다. 서버 쪽에는 `service preset tools` 가 있지만 워커 쪽 짝은 없다 | `src/remote_worker.py:672`, `src/cli.py:3436`, `docs/configuration.md:1378`, `docs/configuration.md:949-952`, `src/core/capability.py:276`, `src/cli.py:3546` | 고칠 것 | 깨끗한 환경에 서비스 파일의 환경 변수만 더해 잰다(결정 246) |
| G4 | 버전 요구를 적을 방법이 없다. `requires` 는 이름이나 절대 경로뿐이고 버전 문법이 없다. 지금은 `checks` 점검 프리셋이 유일한 길이다 | `src/config.py:670-690` | 이미 있음 | 코어에 버전 문법을 넣지 않고 `checks` 로 지킨다(결정 246) |
| G5 | OS 버전과 arch 를 모으지 않는다. 서버로 보내는 호스트 사실은 OS 계열(darwin·linux) · 코어 수 · 부하 · GPU · 디스크 · `hardware_id` 다. Python 버전과 arch 는 로컬 `rcm version` 에만 있다. rcm 자신의 의존성(샘플러 명령, linger, Local Network, fd, git 버전)도 검사하지 않는다. `read_auth = none` 이면 `/api/machines` 가 토큰 없이 읽힌다 | `src/hostsample.py:339-345, 322, 85-101`, `src/cli.py:3665-3672`, `src/server.py:5781-5785`, `src/config.py:100` | 고칠 것 | 호스트 사실을 더 모은다. 새 호스트 사실은 토큰 있는 응답에만 싣는다(결정 246) |
| G6 | `worker --check` 의 presets 줄은 절대 FAIL 이 되지 않는다. 「다 갖춤」과 「아무것도 못 돌림」이 같은 0 이다 | `src/cli.py:4696-4728` | 새로 만들 것 | `rcm doctor` 의 종료 코드(결정 246) |
| G7 | 서버는 로컬 레인의 도구를 자기 서비스 환경에서 잰다. 그 값은 `/api/health` 의 `preset_env` 키와 `@local` 판정으로 보이고 `rcm check` 는 그 키를 service preset tools 줄로 그린다. `preset_env` 에는 `requires` 나 `env_passthrough` 가 있는 프리셋만 실린다. doctor 에는 역할 4 가 없다 | `src/cli.py:3901-3930`, `docs/configuration.md:940-952`, `src/server.py:5409, 5316-5317` | 이미 있음 | doctor 의 `coordinator-worker` 역할이 이 값을 읽는다(결정 246) |

판정 순서는 M5n §4.3 그대로다. 보고가 없거나 낡으면 unknown, 도구가 없으면 ineligible, 워크스페이스 안 도구면 unknown,
`checks` 가 실패하면 ineligible, 통과하면 eligible 이다. `checks` 없는 프리셋은 도구만 있으면 바로 eligible 이다
(`src/core/capability.py:291-293`). `[[probes]]` 는 지문일 뿐 요구 조건이 아니다(`src/core/capability.py:264-316`).

### 3.7 실제 빈 곳

막는 정도 순으로 적는다.

1. 레인 0 은 설정에서 거절된다. 허용하면 거짓 후보, 영영 안 도는 점검 잡, 거짓 건강이 생긴다(C1 · C4 · C7 · C8).
2. 붙은 워커에 일이 저절로 가지 않고 그 이유도 보이지 않는다(D7 · D8 · G6).
3. 원격 워커에서 돌면 안 되는 프리셋을 적을 방법이 없다(D10).
4. 첫 실행 흐름이 없고 역할을 적는 곳도 없다(A1 · A3 · A8).
5. 요청자에게 미는 알림이 없고 누가 돌렸는지는 JSON 에만 있다(E12 · E13 · E14).
6. 서버용 서비스 파일을 만들지 않는다(A6).
7. `worker --check` 에 버전 비교가 없다(D4).
8. `lanes = 0` 이어도 `pools_without_workers` 가 기본 풀을 건너뛴다(C12).
9. 문서 부채가 있다. M5q 상태와 결정 149~238 이 PLAN 에 반영되지 않았고 `PLAN.md:749` 색인에 M5r 175~200 이 빠졌다.

망이 끊기면 결과 고리가 끊어지는 것(E10)과 토큰을 사람이 옮기는 것(D1 · D2)은 이 명세가 고치지 않는다. 앞의 것은
M5t-b, 뒤의 것은 M5t-c 다.

## 4. 설계

소절마다 결정 번호를 단다. 결정의 내용과 오너 확인 여부는 §11 에 모았다.

### 4.1 역할 · 첫 실행 · `rcm setup` (결정 239~242)

역할은 넷이다. 이름은 숫자가 아니라 식별자로 고정한다.

| 역할 | 오너가 말한 번호 | 하는 일 | 있는 명령 |
|---|---|---|---|
| `client` | 1 | 잡을 보내고 결과를 받는다 | `rcm init client`, `rcm run`, `rcm wait` |
| `coordinator` | 2 | 잡을 받아 큐에 두고 놀고 있는 머신에 나눈다. 자기는 돌리지 않는다 | `rcm serve`(`lanes = 0`) |
| `worker` | 3 | 잡을 받아 돌리고 결과를 돌려준다 | `rcm init worker`, `rcm worker` |
| `coordinator-worker` | 4 | 2 와 3 을 한 머신에서 한다 | `rcm serve`(`lanes ≥ 1`) |

역할은 새 파일에 적지 않는다. 있는 설정 파일에서 읽어 낸다(결정 239). 상태를 두 곳에 두면 언젠가 어긋나기 때문이다.

| 있는 설정 | 역할 |
|---|---|
| `server.toml`, `lanes ≥ 1` | `coordinator-worker` |
| `server.toml`, `lanes = 0` | `coordinator` |
| `worker.toml`, 또는 환경변수만 쓰는 워커(`RCM_WORKER_TOKEN`, 서버는 `--server`) | `worker` |
| `client.toml`, 또는 환경변수만 쓰는 클라이언트 | `client` |

한 머신이 여러 역할을 가질 수 있다. 운영 빌드 머신은 `coordinator-worker` 이면서 잡을 보내는 `client` 이기도 하다.

역할을 읽어 낼 때 설정은 아래 순서로 찾는다. `$RCM_CONFIG` 와 `./rcm.toml` 은 지금 코드에서 서버 설정을 찾는
후보다(`config.py:49, 611-627`). 그래서 역할을 읽을 때도 그 둘은 서버 설정으로 읽고 `lanes` 로 역할 2 와 4 를 가른다.
환경변수만 있는 클라이언트와 워커도 「설정 있음」으로 본다. 그런 머신에서는 첫 실행 질문을 띄우지 않는다.
`$RCM_CONFIG` 가 가리키는 파일이 없으면 역할을 읽지 않고 usage 오류로 종료 2 다.

첫 실행 판정은 탐색을 새로 짜지 않고 기존 탐색 함수를 그대로 부른다. 서버 설정은 `find_server_config`
(`config.py:610-627`), 클라이언트 설정은 `find_client_config`(`config.py:1485-1494`)를 부른다. 워커 설정은 `rcm worker`
처럼 `user_config_dir()` 아래의 `worker.toml` 을 본다(`cli.py:4342`).

| 순서 | 찾는 곳 | 읽는 법 |
|---|---|---|
| 1 | `$RCM_CONFIG` | 서버 설정 |
| 2 | `./rcm.toml` | 서버 설정 |
| 3 | 사용자 설정 폴더. `server.toml` 과 `client.toml` 은 `$XDG_CONFIG_HOME/rcm` 을 먼저 보고 `~/.config/rcm` 도 본다(`config.py:61-76`). `worker.toml` 은 `user_config_dir()` 하나만 본다. XDG 가 있으면 그쪽이고 없으면 `~/.config/rcm` 이다(`config.py:54-58`) | 파일 이름대로(`server.toml` · `worker.toml` · `client.toml`) |
| 4 | 환경변수만 쓰는 클라이언트(`RCM_SERVER` · `RCM_TOKEN`) | `client` |
| 5 | 환경변수만 쓰는 워커(`RCM_WORKER_TOKEN`, 서버는 `--server`) | `worker` |

첫 실행 규칙은 이렇다(결정 240). 인자 없는 `rcm` 은 stdin 과 stderr 가 모두 TTY 이고 위 순서로 찾은 설정이 하나도 없을
때만 「설정을 시작할까요?」를 묻는다. 그 밖에는 지금처럼 usage 오류로 종료 2 다. 비대화 실행에서는 절대 묻지 않는다.
`rcm setup` 은 언제든 부를 수 있고 `rcm setup --role <r> --yes` 는 묻지 않고 돈다. 질문과 안내는 CLI 문구라서 영어로
쓴다(AGENTS.md).

`rcm setup` 은 기존 `init server|client|worker` 를 조립한다(결정 241).

| 단계 | 정한 것 |
|---|---|
| 역할 고르기 | 넷 가운데 하나. `--role` 이면 묻지 않는다 |
| 포트 · 데이터 폴더 · 광고 · 라벨 | `--port`, `--data-dir`, `--advertise`/`--no-advertise`, `--label` 을 받는다. 대화형에서도 묻는다. 기본값은 템플릿대로이고 라벨의 기본은 표준 라벨이다. `data_dir` 은 절대 경로로 쓴다. 고른 `data_dir` 에 `rcm.sqlite3` 가 이미 있으면 거절하고 종료 1 이다. 그때는 이미 있는 데이터 폴더라고 알리고, 새 폴더를 고르거나 그대로 쓰려면 `--reuse-data-dir` 를 주라고 안내한다. 고른 포트를 지금 쓰고 있으면 경고한다 |
| 설정 파일 쓰기 | 기존 `init` 을 부른다. 있는 파일은 덮지 않고 파일마다 `--force` 를 받는다. 중간에 실패하면 반쪽 설정을 남기지 않는다 |
| bind(역할 2·4) | 아래 bind 표대로 고른다(⛔ Q10). `--yes` 로 역할 2·4 를 고르면 `--bind` 가 필수이고 없으면 usage 오류로 종료 2 다. `0.0.0.0` 이 저절로 고르는 값이 되지 않게 하려는 것이다. 설치하자마자 LAN 에 열리지 않는다는 결정 33 의 뜻을 그대로 지킨다 |
| 토큰 | 역할마다 아래 토큰 표의 길을 안내한다 |
| 서비스 파일 | 서버용 서비스 파일 생성을 새로 더한다. 지금은 워커용만 있다(A6). 서버용 파일에 반드시 넣는 키는 아래 서비스 파일 표에 있다. setup 은 서비스 파일을 `~/Library/LaunchAgents/` 에 바로 쓰지 않는다. 거기 쓰면 다음 로그인 때 launchd 가 `RunAtLoad` 로 저절로 올려서 Q9(등록과 시작은 사람이 한다)와 부딪힌다. 그래서 설정 폴더 아래 `<config dir>/service/<label>.plist`(systemd 는 `.service`)에 쓰고, 설치할 자리로 복사하는 명령과 bootstrap 명령을 출력한다. `--service-file <경로>` 를 주면 그 경로가 먼저다. 라벨은 표준 라벨(`com.remote-ci-monitor.server` · `com.remote-ci-monitor.worker`)이 기본이고 `--label` 로 바꿀 수 있다. 있는 파일은 덮지 않는다. 같은 라벨이 이미 로드돼 있으면 경고만 한다. 로드됐는지는 읽기 전용 조회(`launchctl print`, `systemctl --user is-enabled`)로만 본다. 출력하는 복사 명령은 있는 파일을 덮지 않는 꼴(`cp -n`)이다. 운영 라벨을 거절하는 일은 제품이 하지 않고 실기 하네스와 `tools/guard_production.py` 가 한다 |
| 서비스 등록 | 하지 않는다. 등록하고 시작하는 명령만 출력한다(M5n §9 유지, ⛔ Q9). 사람이 실행해야 하는 까닭이 셋 있다. launchd 의 `bootstrap gui/<uid>` 는 로그인 세션이 있어야 한다. macOS 의 Local Network 권한 창은 사람이 눌러야 한다. systemd 의 `enable-linger` 는 sudo 가 필요하다 |
| 마지막 | setup 은 서비스를 시작하지 않고 토큰도 만들지 않는다. 그래서 진단 가운데 서버 없이 도는 부분만 돌린다. rcm 자신의 요구, 호스트 사실, 설정 파일 검증이다. 그 결과는 조언으로 보인다. 끝에 「서비스를 띄우고 토큰을 넣은 뒤 `rcm doctor --role <r>`」을 출력한다 |
| 종료 코드 | 설정을 썼으면 0, 쓰지 못했으면 1, 사용법 오류는 2 다 |

역할마다 `--yes` 일 때 꼭 줘야 하는 인자는 아래와 같다. 역할 2·4 의 `--bind` 처럼 빠지면 usage 오류로 종료 2 다.

| 역할 | `--yes` 일 때 꼭 줄 인자 | 선택 인자 |
|---|---|---|
| `client` | `--server` | `--by`(요청자 이름), `--config`(쓸 자리) |
| `worker` | `--server`, `--pool`, `--lanes` | `--label`, `--service-file`, `--config` |
| `coordinator` · `coordinator-worker` | `--bind` | `--port` · `--data-dir` · `--label` · `--advertise`/`--no-advertise` |

서버 서비스 파일에는 아래 키를 반드시 넣는다.

| 키 | 정한 것 |
|---|---|
| 파일 디스크립터 상한 | launchd 는 `SoftResourceLimits` 의 `NumberOfFiles` 4096, systemd 는 `LimitNOFILE=4096` 이다. `examples/` 의 서버 서비스 파일과 같은 값이다 |
| `HOME` | 넣는다 |
| `WorkingDirectory` | 넣는다 |
| 로그 경로 | 넣는다. 로그 파일 이름은 라벨에서 만든다 |
| PATH | 워커용과 같은 규칙이다. `/usr/sbin` 을 넣고 venv 의 bin 을 앞에 두지 않는다 |

역할 2·4 의 bind 는 아래에서 고른다. 결정 33 은 템플릿의 기본값만 정했다. PLAN 「보안」과 `README.md:83` 은 이미
`0.0.0.0` 을 선택지로 안내한다. 127.0.0.1 에서는 mDNS 광고가 꺼지고 워커가 서버에 닿지 못하므로, 역할 2·4 에서는
127.0.0.1 이 기본 선택지가 될 수 없다.

| 선택지 | 언제 | 따라오는 것 |
|---|---|---|
| Tailscale 주소 | 이 머신에 Tailscale 주소가 있을 때. 추천 | mDNS 응답기가 LAN 워커에게 LAN 주소를 알려 주므로 LAN 워커의 연결은 거절된다. 그래서 워커는 mDNS 로 찾지 않고 서버 URL 을 명시해 붙는다. setup 은 `advertise = false` 를 쓰고 tailnet 전체에 읽기가 열린다고 경고한다 |
| LAN 인터페이스 주소 | Tailscale 주소가 없을 때 | DHCP 로 주소가 바뀌면 서버가 뜨지 못한다. 그래서 공유기에서 DHCP 예약을 하라고 권한다. setup 은 읽기(큐 · 보관함 · 머신 목록)를 같은 망 누구나 볼 수 있다고 경고한다 |
| `0.0.0.0`(모든 인터페이스) | Tailscale 주소가 없을 때 | LAN 인터페이스 주소와 같은 경고를 낸다 |
| 직접 입력 | 사람이 주소를 적을 때 | 이 머신의 인터페이스에 실제로 있는 주소만 받는다 |

지금 있는 읽기 인증은 `none` 과 `basic` 이고 `basic` 은 결정 23 에 따라 TLS 프록시 뒤에서만 쓴다. 그래서 setup 은 읽기
인증을 제안하지 않고 경고만 낸다. 토큰으로 읽기를 막는 모드를 새로 만들지는 ⛔ Q14 다. 추천은 이 마일스톤 밖에서 다음에
하는 것이다.

토큰은 역할마다 이렇게 얻는다. M5t-a 의 setup 은 토큰을 만들지도 받지도 않고 얻는 길을 안내한다.

| 역할 | 토큰을 얻는 길 |
|---|---|
| `coordinator` · `coordinator-worker` | 서버를 띄운 뒤 서비스 venv 에서 실행할 `rcm token --config <server.toml> add <이름>` 을 출력한다. 이 토큰은 client 권한이고 `--admin` 을 붙이지 않는다. 워커를 붙일 때 쓸 `rcm token add <이름> --worker` 도 같은 꼴로 출력한다 |
| `client` | 서버 URL 을 받아 `init client` 를 부른다. 코디네이터에서 사람이 만든 client 토큰을 `RCM_TOKEN` 으로 주라고 안내한다. 지금 `init client` 의 안내와 같다(`cli.py:3661`) |
| `worker` | 코디네이터의 URL 을 받아 `init worker` 를 부른다. setup 은 `server = "auto"` 를 쓰지 않고 그 URL 을 명시해 쓴다(§9). 코디네이터에서 사람이 `rcm token add <이름> --worker` 로 토큰을 만들어 `RCM_WORKER_TOKEN` 이나 파일 `token` 으로 주라고 안내한다(`cli.py:3624`, `config.py:502`). 서비스로 띄우는 워커는 파일의 `token` 만 쓴다. 셸의 `RCM_WORKER_TOKEN` 은 서비스에 닿지 않는다(`cli.py:3571-3574`). 워커 토큰은 지금도 플래그로 줄 수 없다(`config.py:1794-1795`) |

setup 은 어떤 Store 도 열지 않는다. 다른 빌드가 운영 DB 를 열면 그 자리에서 마이그레이션하기 때문이다. 그래서
coordinator 역할의 토큰도 setup 이 만들지 않는다. setup 은 서버 설정과 서비스 파일까지만 쓰고 마지막에 위 표의 명령을
출력한다. 그런데 setup 이 쓰는 서비스 파일은 setup 을 실행한 빌드를 가리킨다. 지금 워커용 서비스 파일도
`_this_rcm()`(`cli.py:3477`)으로 지금 도는 `rcm` 을 찾아 적는다(`cli.py:3561`). 그래서 워크트리 빌드로 setup 을 돌리면
그 서비스도 워크트리 빌드다. 운영 DB 를 막는 장치는 둘이다. 하나는 고른 `data_dir` 에 `rcm.sqlite3` 가 이미 있으면
setup 이 거절하는 규칙(위 단계 표)이고, 다른 하나는 아래 guard 의 운영 `data_dir` 규칙이다.

`tools/guard_production.py` 는 새 빌드가 운영의 옛 DB 를 열어 마이그레이션하는 것을 막는 훅이다. setup 과 doctor 는
DB 를 열지 않으므로 그 훅의 DB 를 여는 명령 목록(`DB_OPENING` · `MIGRATING`)에 넣지 않는다. 대신 그 훅에 규칙 셋을
더한다(PR4). 첫째는 setup 이 운영 설정 경로에 쓰려 하면 거절하는 규칙이다. 훅은 setup 이 쓸 경로를 setup 과 같은 규칙
(`XDG_CONFIG_HOME` 과 `HOME`, `config.py:54-58`)으로 계산해서 운영 설정 경로와 비교한다. 훅이 아는 운영 설정 경로는
`~/.config/rcm` 이다(`tools/guard_production.py:869-877`). 둘째는 setup 이 운영 라벨의 서비스 파일을 쓰려 하면
거절하는 규칙이다. 셋째는 플래그로 준 `data_dir`, 또는 그 명령의 `HOME` 으로 푼 템플릿 기본값이 운영 `data_dir` 이면
`rcm setup` 을 거절하는 규칙이다.

setup 이 지킬 것이 둘 더 있다. 우선 토큰을 stdout 과 stderr 에 되풀이해 찍지 않는다. 다음으로 setup 과 doctor 를 버전
따라가기 대상에서 뺀다(`NO_FOLLOW_COMMANDS`, `follow.py:80-82`). 설정이 없을 때 따라가기는 의미가 없다. doctor 는 PR2,
setup 은 PR4 에서 그 목록에 넣는다.

`coordinator-worker` 는 지금의 `rcm serve`(`lanes ≥ 1`)다(결정 242). 같은 머신에 `rcm worker` 를 또 띄우는 것은 기본이
아니다. 그러면 같은 호스트에 레인이 겹친다. 다만 같은 Mac 의 둘째 워커처럼 이미 그렇게 쓰는 운영 구성은 `host_key` 가
머신을 갈라 주므로 그대로 허용한다. setup 은 이 경우에 경고만 한다.

### 4.2 분배 전용 코디네이터, `lanes = 0` (결정 243)

`lanes = 0` 을 허용한다. 그러면 따라오는 일이 여럿이다.

`capacity` 는 「어느 풀이든 살아 있는 레인이 있는가」가 아니다. 「지금 갈 수 있는 머신이 하나라도 있는 프리셋 수 / 전체
프리셋 수」다. 갈 수 있는 머신은 그 프리셋의 후보 함수 결과(4.3) 가운데 살아 있는 머신이다. `checks` 가 없는 `pool`
프리셋은 판정과 상관없이 그 풀의 살아 있는 레인을 센다. 지금 `placement_for` 의 pool 갈래가 그렇게 고른다
(`capabilities.py:771-772`). 프리셋이 0개면 `capacity` 행을 그리지 않는다. `pools_without_workers` 는 지금 lanes 와 상관없이 기본
풀을 늘 건너뛴다. `lanes ≥ 1` 일 때만 건너뛰도록 고친다(C12).

| 위치 | 바꾸는 것 | 근거 |
|---|---|---|
| 설정 | `lanes = 0` 을 받는다 | C1 |
| `@local` 머신 | lanes 0 이면 살아 있지 않다 | C5 |
| `--machine @local` 제출 | 409 와 「이 서버에는 로컬 레인이 없다」 | C5 |
| `local_only` 프리셋 제출 | 후보가 `@local` 하나인데 `lanes = 0` 이라는 정적 조건 때문에 후보가 빈다. 409 와 사람용 이유 문장(4.3) | C5 |
| 로컬 프로브 | 생략한다 | C6 |
| `eligible_now` | `@local` 을 뺀다 | C7 |
| 자동 점검 | `@local` 을 겨냥하지 않는다. `local_only` 점검 프리셋은 후보가 비므로(4.3) 「갈 곳 없음」으로 보이고 잡을 만들지 않는다 | C8 |
| 기동 · reload | `lanes = 0` 이면 이미 대기 중인 `local_only` 잡과 `@local` 지정 잡이 갈 곳을 잃는다. lanes 를 1 이상에서 0 으로 바꿔 재시작했거나 reload 로 `local_only` 를 새로 단 경우다. 서버는 기동과 reload 때 그런 잡에 reason `no_capable_machine` 과 ETA null 을 단다. 실패로 닫지 않는다. `local_only` 잡은 후보 함수로 다시 계산되므로 기동 · reload 전용 분기는 따로 두지 않는다(빈 후보의 분기는 4.3 의 것 하나다) | C5 |
| `/api/health` | 프리셋 단위의 `capacity` 정보를 싣는다. HTTP 상태는 바꾸지 않는다. 다른 정보 항목처럼 503 조건이 아니다(⛔ Q5) | C4 |
| `rcm check` | 갈 수 있는 프리셋이 하나도 없으면 FAIL 줄 `capacity` 를 낸다. `capacity` 가 0 인 프리셋 목록을 보인다. 일부 프리셋만 0 이면 FAIL 이 아니라 warn 으로 그 목록을 보인다(어느 머신도 못 돌리는 배포 프리셋은 정상일 수 있다). 프리셋이 0개면 `capacity` 행을 그리지 않는다 | C4 |
| `rcm check` 의 `local preset tools` · `service preset tools` | 「이 머신은 잡을 돌리지 않는다」로 그린다. FAIL 이 아니다 | — |
| `rcm doctor --role coordinator` | `capacity` 가 0 인 프리셋 목록을 보인다 | C4 |
| `pools_without_workers` | `lanes ≥ 1` 일 때만 기본 풀을 뺀다. `lanes = 0` 이면 기본 풀도 센다 | C12 |
| mDNS TXT | `lanes=0` 을 싣는다 | B2 |
| 결정 39(M5f) | 「서버 레인 1」 문장을 PR3 이 고친다 | C9 |
| 화면 | `lanes 0/0` 표기를 손본다 | C10 |
| 샘플러 | 계속 돈다. 호스트 카드에 「잡을 돌리지 않는 머신」을 표시한다 | C11 |

워커가 하나도 없을 때 잡은 실패가 아니라 대기다. ETA 는 `—` 다. 워커가 붙으면 바로 배치된다. 서버를 재시작해도 큐는
남는다. 이 넷은 §7 의 TL 표가 잠근다.

### 4.3 원격 금지 표식 `local_only` (결정 254)

오너는 2026-11-25 원격 금지를 「프리셋에 명시 표식」으로 답했다. 프리셋 설정에 `local_only = true` 를 두고 기본은 지금처럼
원격 허용이다. 이 레포를 쓰는 앱 프로젝트의 release-* · deploy-* 프리셋에 이 표식을 단다. DB 는 바꾸지 않고 작은 PR
하나(PR1)로 넣는다.

지금 무엇이 문제인지는 §3 D10 에 적었다. 기본 풀에 붙인 원격 워커가 릴리스 프리셋을 집을 수 있다. 비밀은 로컬 레인에만
주입되므로 비밀이 새는 문제가 아니다. 그 잡이 비밀 없이, 또는 그 머신의 자격 증명으로 도는 배치 오류다.

다섯째 판까지는 `local_only` 를 경로마다 따로 거르는 문장을 적었다. 원격 claim, 대기 계산, 제출 판정, 자동 점검,
`/worker/spec` 에 하나씩이다. 그랬더니 라운드마다 빠진 경로가 나왔다. 4라운드 HIGH 2건과 5라운드 HIGH 3건이 모두 그런
경우다(§13). 그래서 이 판은 거르는 곳을 한 곳으로 모은다.

정의는 한 문장이다(결정 254). `local_only = true` 인 프리셋에게 이 서버의 머신 목록은 `@local` 하나뿐이다. 서버는
프리셋마다 후보 머신 목록을 만드는 함수 하나를 둔다. 이름은 이를테면 `machines_for(preset)` 이다. 이 후보 함수는 그
프리셋이 `local_only` 면 원격 머신을 모두 뺀다. 아래 경로는 모두 이 후보 함수의 결과를 쓰고 따로 거르지 않는다.

| 경로 | 지금 코드 |
|---|---|
| 배치. `placement_for` 의 지정 · capable · pool 세 갈래 전부 | `capabilities.py:729-789` |
| 제출 판정. `submission_placement` 와 제출 응답의 `eligible_now` | `capabilities.py:841-868` |
| 수신 가능 판정 `_could_receive` | `capabilities.py:940-947` |
| `capacity` 집계(4.2) | 새로 만든다 |
| 점검 정체 판정 `_stalled_checks` · `_why_none` | `capabilities.py:791-839` |
| 자동 점검과 recheck 의 대상 | `capabilities.py:872-917, 949-970` |
| `/worker/spec` 의 프리셋 목록 | `capabilities.py:254-272` |
| 원격 claim 의 WHERE. 네 갈래(지정 · `pool` · `capable` · `known`) 전체에 건다 | `store.py:3371-3422`(갈래 `:3379-3398`, WHERE `:3416-3420`) |
| 새 잡의 ETA `/api/eta`(`rcm eta`). pool 프리셋은 지금 `placement_for` 를 거치지 않고 풀의 전체 레인으로 센다. 이 경로도 후보 함수의 결과로 레인을 고른다 | `server.py:3943-3956`, `core/queue.py:719-727` |
| 머신 판정 문서 `/api/machines`(`machine_json`, `machines_summary` 의 `eligible_count`) — 웹 머신 탭과 `rcm check` 의 `placement` 행이 읽는다 | `capabilities.py:572-582, 646-648` |
| 원격 claim 에 넘기는 값. SQL 은 Python 함수를 부를 수 없으므로 `Claimant` 에 후보 함수로 계산한 「이 워커가 집지 못할 프리셋」 목록을 싣고 네 갈래에 `preset NOT IN` 으로 건다 | `store.py:846-863`, `capabilities.py:429-452` |

새 경로가 생겨도 이 후보 함수를 거치지 않으면 시험이 빨개져야 한다. 그래서 시험 하나가 여러 진입점(배치 · ETA ·
`capacity` · 점검 · `/worker/spec` · claim)에 걸쳐 「원격 머신이 `local_only` 프리셋의 후보에 나타나는 곳이 없다」를
확인한다. 경로마다 흩어져 있던 TO 행은 이 시험 하나로 묶었다(§7). `capacity` 는 PR3 이 넣으므로 그 진입점은 PR3 이 같은
시험에 더한다. 변이 검사는 이 후보 함수에서 원격 제외를 지운다. 그러면 그 시험이 빨개져야 한다.

후보 함수는 정적인 조건만 본다. `local_only`, 프리셋의 풀, 서버의 `lanes`, `local_accept_any` 다. 살아 있음과 도구 판정은
보지 않는다(그것은 지금처럼 갈래 안에서 본다). 후보가 비는 경우는 넷이다. `lanes = 0` 일 때, `local_accept_any = false`
인데 프리셋이 `any-capable` 일 때, 프리셋이 기본 풀이 아닌 풀을 쓸 때, 원격 워커를 지정했을 때다. 지금 코드에서
`no_capable_machine` 을 내는 곳은 capable 갈래(`capabilities.py:747-766`)와 checks 가 있는 pool 갈래(`_why_none`,
`:785-789`)다. 지정 갈래와, 풀에 머신이 없거나 checks 가 없는 pool 갈래는 빈 결과를 reason 없이 돌려줘(`:783-784`) 큐가
`worker_down` 으로 그린다(`core/model.py:619-622`). 그래서 **새 분기를 둔다**. 범위는 `local_only` 프리셋에서 **후보 함수가
머신을 뺐기 때문에** 비었을 때뿐이다. 그때 세 갈래 모두 `Placement.reason = no_capable_machine` 이고 ETA 는 null 이다.
`local_only` 가 아닌 프리셋의 동작(머신이 없는 풀의 pool 잡은 `worker_down`, C3)은 바꾸지 않는다. 판정 때문에 비는 경우(`@local` 이 도구
부족이나 점검 실패로 ineligible)는 지금의 `_why_none`(`check_failed` · `check_pending`)을 그대로 둔다. 제출 409 는 `local_only`
프리셋에서 위의 정적인 넷과 capable 갈래의 `tool_missing` 에만 건다(`local_only` 가 아닌 프리셋의 제출 동작은 바꾸지 않는다). `eligible_now` 가 비었다는 것만으로는 409 가 아니다(reload 직후 보고가
낡은 동안에도 비기 때문이다). 409 는 사람용 문장과 기존 꼴의 이유 코드를 쓴다. `--machine` 으로 원격 워커를 지정하면 그 워커가
후보에 없으므로 409 다. 기동이나 reload 때 이미 대기 중인 잡도 같은 후보 함수로 다시 계산된다. 그래서 원격 워커를 지정한
잡, 기본 풀이 아닌 풀의 잡이 `no_capable_machine` 으로 보인다. 예약(`scheduled`) 잡은 큐 계산에 들지 않으므로(`core/model.py:35-45`) 시각이 되어 queued 가 된 뒤에 같은 규칙을 탄다.

`local_only` 가 아닌 프리셋도 이 후보 함수의 영향을 받는다. 프리셋 P 의 `checks` 에 `local_only` 점검 프리셋 C 가 있으면
원격 머신에서는 C 의 후보가 빈다. 그래서 P 는 그 원격 머신에서 「오지 않음(점검이 로컬 전용)」이고 check_pending 이
아니다. doctor 도 같은 후보 함수로 판정하므로 「점검 필요」 문장을 찍지 않는다. `rcm recheck <원격 머신> <C>` 는 409 다.

후보 함수 밖에 남는 규칙은 아래 표뿐이다.

| 위치 | 정한 것 | 근거 |
|---|---|---|
| 설정 | 프리셋 키 `local_only`(참 거짓, 기본 false)를 받는다. 프리셋 키가 `_PRESET_KEYS`(`config.py:632`)에 없으면 기동 실패라서(`config.py:818`) 그 목록에 더한다 | — |
| 설정 검증 | 하나만 남긴다. `local_only` 이고 placement 가 `pool` 이면 `pool` 이 기본 풀이어야 하고 아니면 기동 실패다. 로컬 레인은 기본 풀만 집기 때문이다(`worker.py:317-318`, `store.py:3265`, `capabilities.py:475-476`). `pools` 에 든 기본 풀이 아닌 항목은 기동 실패가 아니다. 후보 함수가 그 풀의 후보를 비우므로 그 풀로 낸 제출이 409 로 막힌다 | — |
| 로컬 레인 | 지금처럼 집는다 | — |
| 409 검사 순서 | 제출의 409 검사는 supersede 와 합류보다 먼저 한다 | — |
| `rcm doctor --role coordinator` | `local_only` 프리셋을 「로컬 레인 필요」로 표시한다 | — |

`lanes = 0` 서버에 낸 `local_only` 제출의 409 는 넷째 판이 정했고, 원격 워커를 지정한 제출의 409 는 4라운드 결정 파일이
정했다. 이 판에서는 둘 다 「후보가 빈 제출은 409」라는 같은 규칙에서 나온다.

### 4.4 붙은 워커가 받는 일

결정 97(배치 기본 `pool`)은 유지한다. M5t-a 에서 새 워커의 풀은 지금처럼 `rcm init worker --pool` 이 정한다. 기본은 짧은
호스트 이름이라(`cli.py:3605`) 그대로 두면 그 워커가 받는 일은 `any-capable` 프리셋뿐이다(`cli.py:3627`). 창에서 풀을
정해 토큰에 묶는 일은 M5t-c 로 넘겼다.

그래서 doctor 가 받을 일을 알린다(결정 246). 이 머신에 올 프리셋이 0개면 doctor 는 0 이 아니라 1 로 끝나고 「이 머신에는
잡이 오지 않는다」와 그 이유를 찍는다. `rcm doctor --role coordinator` 는 세 가지를 알려 준다. 첫째는 어느 머신도 돌리지
못하는 프리셋이다. 둘째는 놀고 있는 능력 있는 워커가 있는데 `pool` 전용이라 그리로 가지 못하는 프리셋이다. `any-capable`
로 바꿀지는 사람이 정한다. 셋째는 `local_only` 프리셋이고 「로컬 레인 필요」로 보인다. `local_only` 프리셋은 사람이 배치를
바꿔도 원격 워커에 가지 않는다(4.3). 첫째와 둘째는 `capacity` 를 넣는 PR3 이 구현한다(TD20).

TL12 실기는 분배 전용 코디네이터에 일회용 워커를 붙여서 한다. 사람이 옮긴 토큰과 명시 URL 로 붙이고 `--pool` 에
코디네이터의 기본 풀을 준다. 그다음 배치를 바꾸지 않은 기본 풀 프리셋을 보낸다. 실기는 운영 프리셋을 쓰지 않고 시험 서버
설정에 둔 가짜 `sh -c` 프리셋 하나(`m5t-echo`, 기본 풀)를 쓴다.

### 4.5 환경 진단 `rcm doctor` (결정 246 · 247)

오너의 11-25 요구에 답하는 명령이다. 이 머신에서 무엇을 빌드하고 시험할 수 있는지, 무엇을 더 깔아야 하는지 쓰기 전에
알려 준다.

```sh
rcm doctor [--role client|worker|coordinator|coordinator-worker] [--server <url>] [--config <path>] [--json] [--offline] [--shell-path] [--service-file <path>]
```

| 항목 | 정한 것 |
|---|---|
| 역할 | 넷이다. `--role` 을 생략하면 설정에서 읽어 낸 역할이 하나일 때 그 역할을 쓰고, 여럿이면 usage 오류 2 로 끝내고 고르게 한다. `coordinator-worker` 는 로컬 레인 쪽을 서버가 자기 서비스 환경에서 잰 값(`/api/health` 의 `preset_env`, `@local` 판정, G7)으로 읽고 원격 워커가 있으면 그쪽도 함께 보인다 |
| 서버 쪽 값 | `coordinator` 와 `coordinator-worker` 진단은 로컬 `server.toml` 을 읽는다. DB 는 열지 않는다. 서버에는 그 설정의 bind 와 port 로 `/api/health` 와 `/api/machines` 를 읽는다. bind 가 `0.0.0.0` 이면 127.0.0.1 로 묻는다. 토큰은 client 토큰(client.toml 이나 `RCM_TOKEN`)을 쓴다. 토큰이 없으면 서버 쪽 칸은 `—` 로 두고 종료 3 이다 |
| 측정 환경 | PATH 만 바꾸지 않는다. 깨끗한 환경에서 잰다. 깨끗한 환경은 `env -i` 에 서비스 관리자가 넣는 기본 변수와 서비스 파일의 환경 변수만 더한 것이다. 기본 변수 목록은 launchd 와 systemd 를 따로 적는다. launchd 는 `HOME` · `USER` · `LOGNAME` · `SHELL` · `TMPDIR` · `PATH` 를 넣고, systemd 사용자 유닛은 `HOME` · `USER` · `LOGNAME` · `SHELL` · `XDG_RUNTIME_DIR` · `PATH` 를 넣는다(`TMPDIR` 없음). 이 목록은 두 관리자의 기본 동작에서 가져온 것이고 PR2 가 이 머신과 Linux 에서 한 번 재어 확인한다. 그래서 셸의 `JAVA_HOME` 같은 값이 측정에 새지 않는다. `env_passthrough` 에서 빠진 변수는 warn 이다. `--shell-path` 결과와 다르면 다르다고 표시한다. 서비스 PATH 와 셸 PATH 를 찍을 때 둘 다 홈을 `~` 로 가린다. 서비스 파일의 환경 변수는 이름만 찍고 값은 찍지 않는다 |
| 서비스 파일 찾기 | `--service-file` 로 명시한 파일을 먼저 본다. 없으면 setup 이 쓰는 자리 `<config dir>/service/*` 를 본다. 그다음 표준 라벨의 설치 자리(`~/Library/LaunchAgents/com.remote-ci-monitor.*.plist`, `~/.config/systemd/user/rcm-*.service`)를 본다. 그것도 없으면 `WORKER_SERVICE_PATH` 로 재고 「서비스 파일 없음」을 표시한다. 찾은 파일이 둘 이상이면 사람이 고르게 하고 비TTY 면 종료 2 다 |
| 대상 프리셋 | 이 머신에 올 수 있는 것만 본다. 그러려고 `/worker/spec` 에 `pool` · `pools` · `placement` · `checks` · `tools` 를 더한다. 더하기만 하므로 옛 워커는 무시한다. `pools` 는 프리셋의 `pools` 키를 그대로 싣는다. `/worker/spec` 의 프리셋 목록은 후보 함수의 결과를 쓰므로 원격 워커에게 `local_only` 프리셋이 실리지 않는다(4.3) |
| 프리셋 상태 | `ready` · `needs_check` · `missing` · `unknown` 넷이다. `needs_check` 는 도구는 다 있고 `checks` 가 남은 상태이며 ok 라 부르지 않는다. `unknown` 은 명세를 못 받음, 프로브 시한 초과다. 워크스페이스 안 도구는 `pool` 프리셋이면 `unknown` 이 아니라 warn 으로 적고 종료 0 을 지킨다. `any-capable` 프리셋이면 서버 판정이 unknown 이라 그 잡이 이 머신에 오지 않으므로 「오지 않음」으로 센다(§3.6). 도구가 빠진 `any-capable` 프리셋은 「이 머신에는 오지 않음」과 설치 안내를 warn 으로 적고 종료 코드에 넣지 않는다. `checks` 에 `local_only` 점검이 걸린 프리셋은 원격 머신에서 「오지 않음(점검이 로컬 전용)」이고 `needs_check` 가 아니다(4.3) |
| 도구 상태 | `ok` · `missing` · `broken` · `unknown` 넷이다. `broken` 은 있지만 실행이 실패한 경우다. 코어는 프리셋 도구의 버전을 비교하지 않으므로 `outdated` 는 두지 않는다. 버전 요구는 `checks` 점검 프리셋이 지키고, 진단에서는 그 프리셋이 `needs_check` 로 보인다. rcm 자신의 요구(Python 3.11.4, token_file 일 때 git 2.31)만 코어가 버전을 보고 미달이면 FAIL 로 낸다 |
| 설치 안내 | 코디네이터 설정 최상위 `[[tools]]` 에서 온다(아래) |
| 호스트 사실 | OS 버전(`sw_vers` · `/etc/os-release`), arch, Rosetta 여부, 컨테이너 여부, root 여부, 패키지 관리자(brew · apt · dnf · pacman), Python 버전을 더 모은다. 워커 등록 본문에 더하고(더하기만) `rcm machines` 에 보인다. 서버는 등록 본문을 `remote_workers.py` 의 메모리에 두고 DB 에는 넣지 않는다. 서버를 재시작해도 사라지지 않도록 주기적인 `/worker/capabilities` 보고에도 싣는다. 받지 못한 동안은 `—` 다. 새 호스트 사실은 토큰 있는 응답에만 싣는다. `read_auth = none` 일 때 `/api/machines` 의 익명 응답에는 싣지 않는다. 구현은 `machines_doc()`(`capabilities.py:605`)에 토큰이 있는지를 넘기는 방식이고 `read_auth = basic` 일 때도 토큰이 있는 쪽에만 싣는다. root 면 sudo 를 빼는데 이것은 rcm 자신의 안내에만 한다 |
| rcm 자신의 요구 | rcm 이 직접 안다(아래 표, ⛔ Q7) |
| macOS shim | 도구 이름 목록으로 판별하지 않는다. macOS 에서 실행 파일이 `/usr/bin/` 아래에 있고 그 바이트에 `libxcselect` 가 들어 있으면 Command Line Tools shim 으로 본다. 파일은 표준 라이브러리로 읽는다. 그때 `xcode-select -p` 가 실패하면 실행하지 않고 `missing (needs Command Line Tools)` 로 적는다. 헤드리스 서비스에서 설치 대화상자가 되풀이되는 것을 막는다. Java 스텁은 이 방법으로 판별되지 않는다. Apple Silicon Mac 한 대의 실측에서 `/usr/bin/java` · `javac` · `ruby` · `perl` 에는 `libxcselect` 가 없었고 `xcode-select` · `xcrun` 에는 있었다. 이 한계는 문서에 적는다. 판별은 함수 하나로 둔다. doctor 와 프로브만이 아니라 서버가 `requires` 를 판정할 때 쓰는 `measure_tools`(`runner.py:150`), 잡 직전의 `missing_tools`(`runner.py:129`), 서버의 `preset_env` 측정(`server.py:5300`), `rcm check` 의 도구 행이 모두 그 함수를 쓴다. 그래야 doctor 와 서버 판정이 갈리지 않는다. `xcode-select` 자신은 판별 대상에서 뺀다 |
| 프로브 공통 | 시한을 둔다. `LANG=C` 는 doctor 자신이 호스트 사실을 재는 명령에만 걸고 서버 · 워커의 `[[probes]]` 에는 걸지 않는다. stderr 로 출력하는 도구와 배너가 섞인 JSON 을 다룬다. shim 규칙은 doctor 만이 아니라 워커의 프로브 실행에도 건다 |
| 첫 실행 안전 | mDNS 로 찾은 서버는 이름과 주소를 먼저 보이고 인증된 요청과 프로브를 보내기 전에 사람의 확인을 받는다. URL 을 명시한 서버(`--server` 나 설정 파일의 URL)는 mDNS 로 찾은 것이 아니므로 이 확인이 없다. 비TTY 이고 확인을 받지 못했으면 종료 3 이다. `rcm worker --check` 에도 같은 규칙을 건다. 프로브 argv 는 코디네이터 설정에서 오므로 확인하지 않은 서버의 명령을 돌리지 않으려는 것이다. doctor 는 worker.toml 의 `server = "auto"` 를 warn 한다(§9) |
| 비밀 | 프리셋 env 값을 찍지 않는다. `/worker/spec` 은 프리셋 env 를 워커에 준다(`src/capabilities.py:254-272`). `local_only` 프리셋은 원격 워커에 보내지 않는다(4.3). 토큰에 묶인 풀로 거르는 일은 M5t-c 다 |
| 원격 접속 | `--offline` 은 `git ls-remote` 같은 원격 git 접속만 끈다. 코디네이터 접속은 끄지 않는다. 토큰이 없거나 401 이면 종료 3 과 「토큰이 필요하다」를 낸다 |
| 출력 경로 | 홈 경로를 찍지 않는다. 경로는 basename 규칙을 따른다(`src/runner.py:177-179`) |
| 조언 | 진단 결과는 서버에 보내지 않는다. `/worker/capabilities` 도 부르지 않는다. 자격은 지금처럼 서버의 판정이 정한다 |
| 문구 | 「준비됨(서비스가 보고하면 받기 시작)」, 「점검 필요(등록 뒤 rcm 이 점검을 한 번 겁니다)」, 「로컬 레인 필요」, 「오지 않음(점검이 로컬 전용)」, 대상이 0개면 「이 머신에는 잡이 오지 않는다」와 이유 |

종료 코드는 아래와 같다(⛔ Q8). `rcm check` 의 종료 코드는 바꾸지 않는다. `rcm check` 는 서버가 불통이면 1 이다.

| 코드 | 뜻 |
|---|---|
| 0 | 대상 `pool` 프리셋이 모두 `ready` 나 `needs_check` 이고 rcm 자신의 요구가 ok 다. warn 은 허용한다. `pool` 프리셋의 워크스페이스 안 도구와 도구가 빠진 `any-capable` 프리셋이 warn 이다 |
| 1 | 이 머신이 든 풀의 `pool` 프리셋에 `missing` · `broken` 이 있다. 또는 rcm 자신의 요구가 FAIL 이거나 아래 역할별 규칙에 걸렸다. 빠진 것과 설치 안내를 찍는다 |
| 2 | 사용법 오류 |
| 3 | 판정하지 못했다. 서버 불통, 시한 초과, 옛 서버(`/worker/spec` 에 새 키 없음), 확인을 받지 못함, 토큰 없음이나 401 일 때만이다 |

우선순위는 1, 3, 0 순이다. 그래서 `missing` 과 판정 못 함이 함께 있으면 1 이다.

역할마다 종료 규칙이 다르다.

| 역할 | 규칙 |
|---|---|
| `worker` · `coordinator-worker` | 이 머신에 올 프리셋이 0개면 1 이다. 도구가 빠진 `any-capable` 프리셋의 warn 과 올 프리셋 0개가 함께 있어도 1 이다 |
| `coordinator`(`lanes = 0`) | 위 규칙 대신 `capacity` 로 본다. 모든 프리셋에 갈 수 있는 머신이 없으면 1 이고, 일부 프리셋만 그렇다면 warn 으로 그 목록을 보인다 |
| `client` | 서버 도달, 버전, 토큰만 본다. 서버에 닿지 못하면 3 이다 |

`--json` 의 모양은 이렇다. 사람용 표와 같은 내용을 싣는다.

```json
{"doctor_version": 1, "role": "worker", "measured_in": "clean_service_env",
 "host": {"os", "os_release", "arch", "python", "rcm"},
 "server": {"url", "found_by", "state", "generation"},
 "rcm_requirements": [{"name", "state", "detail", "needed_for", "install"}],
 "presets": [{"name", "applies", "state", "missing": [{"tool", "install", "docs"}], "checks": [], "code", "tools"}],
 "probes": {}, "summary": {"ready", "needs_check", "missing", "unknown"}, "exit"}
```

#### 설치 안내 `[[tools]]`

도구 이름에 묶는 코디네이터 설정의 최상위 표다. `config.py:1073` 의 `_TOP_KEYS` 에 `tools` 를 더한다.

```toml
[[tools]]                       # 표시 전용. rcm 은 이 문자열을 실행하지 않는다
name = "fvm"                    # requires 항목의 basename 이나 probe argv[0] 과 맞춘다
docs = "https://…"
[tools.install]                 # 키: darwin · linux · any(대체값). 한 줄 문자열, 300자 이하
darwin = "brew install …"
linux  = "see docs"
```

검증 규칙은 이렇다. 같은 이름은 두 번 쓸 수 없다. 모르는 OS 키와 제어문자는 기동 실패다. `requires` 항목에도 프로브
argv[0] 에도 없는 이름은 warn 이다. `/api/status` 와 `/api/machines` 에는 싣지 않는다. 토큰 없이 읽히는 범위를 넓히지 않으려는 것이다.
패키지 관리자별 키는 지금 만들지 않는다. rcm 은 이 문구를 실행하지 않고 sudo 로 설치하지도 않는다. 문구를 믿을지는
코디네이터 관리자의 책임이다. 워커는 서버가 보낸 설치 문구를 찍기 전에 제어문자를 한 번 더 정리한다. 방식은 `follow.clean`
과 같다(`cli.py:4649`).

#### rcm 자신의 요구

rcm 이 스스로 알아도 되는 것은 rcm 자신의 의존성뿐이다. 이식성 규칙에 어긋나지 않는다. 안내는 일반 문구만 쓴다. macOS
의 git 은 `xcode-select --install`, Linux 는 배포판 패키지 관리자다(⛔ Q7).

| 요구 | 역할 | 근거 | 지금 검사 |
|---|---|---|---|
| Python 3.11.4 이상과 `tarfile.data_filter` | 전부 | `src/cli.py:3395, 3776-3782`, `README.md:56` | `rcm check` 의 python 줄. 그런데 `pyproject.toml:13` 은 `>=3.11` 이라서 3.11.0~3.11.3 에도 깔리고 check 에서 FAIL 이 난다. pyproject 하한을 3.11.4 로 올릴지 함께 정한다(⛔ Q7) |
| SQLite UPSERT(3.24 이상) · WAL | 코디네이터 | `src/store.py:1008, 1187` | 안 함. 표준 빌드면 충분하다 |
| 파일 디스크립터 4096 | 코디네이터 서비스 | `docs/operating.md:22-27`, `examples/systemd/rcm-server.service:29` | 안 함 |
| git. `[[repos]]` 나 `git_ref` 를 쓰면 필수이고 `token_file` 을 쓰면 2.31 이상 | 코디네이터 · 워커 | `src/config.py:1386-1387`, `src/gitops.py:107`, `docs/configuration.md:250` | 유무만 본다(`src/cli.py:4283, 4669`) |
| git(선택) | 클라이언트 | `src/client.py:287-315, 402` | 안 함. 없으면 디렉터리 전체와 무시 파일까지 보낸다 |
| `ps`, macOS 의 `vm_stat` · `sysctl` · `top` · `ioreg`(`/usr/sbin` 이 PATH 에 있어야 한다) | 샘플러 | `src/hostsample.py:46-61`, `docs/operating.md:59-62` | 안 함. 실패하면 null 과 `hosts_error` |
| Linux `/proc/{loadavg,meminfo,stat}`, `/etc/machine-id` | 코디네이터 · 워커 | `src/hostsample.py:82, 373-404` | 안 함 |
| `nvidia-smi`(선택) | Linux | `src/hostsample.py:57-61` | `no_sampler` |
| 프로세스 그룹(`start_new_session`, `killpg`) | 워커 | `src/runner.py:234, 296` | POSIX 전제. Windows 거부 가드는 샘플러와 `--service-file` 에만 있다 |
| launchd(`~/Library/Logs/rcm` 을 미리 만든다), systemd 사용자 유닛과 `loginctl enable-linger` | 서비스 | `src/cli.py:3494-3543`, `docs/operating.md:10-21`, `examples/systemd/rcm-worker.service:11` | 안 함. `--service-file` 이 찍는 다음 명령에 linger 안내가 빠져 있다(`src/cli.py:3585`) |
| macOS 15 이상의 Local Network 권한(launchd 기본 거부) | 광고 · `server = "auto"` | `src/mdns.py:43-48`, `docs/operating.md:80-87` | 코디네이터 advertise 의 warn 만 있다. 워커의 발견 실패는 「서버 없음」과 구분되지 않는다. doctor 는 이 권한을 판정할 수 없으므로 안내만 한다 |
| macOS TCC(서비스는 `~/Documents` 를 못 읽는다) | 서비스 | `docs/operating.md:63-66` | 안 함 |
| 잠자기 끄기 | 코디네이터 · 워커 | `docs/operating.md:49` | 안 함 |
| 서비스 PATH 앞에 venv 의 bin 을 두지 않는다 | 서비스 | `docs/operating.md:56-62` | 생성기는 지키지만 검사는 안 한다 |

Python 지원 기한도 안내에 쓴다. 3.11 은 2027-10 까지 보안 수정만 받고 3.12 는 2028-10, 3.13 은 2029-10 까지다
(https://devguide.python.org/versions/). 그래서 새 설치에는 3.12 이상을 권한다. macOS 의 `/usr/bin/python3` 은 3.9.6
이라서 하한에 못 미치고 shim 이기도 하다.

#### `rcm worker --check` 에 더하는 두 줄

지금 줄은 그대로 둔다. 끝에 「빠진 것 N개 · `rcm doctor --role worker`」 한 줄을 더한다. 그리고 버전 비교 행을 더한다.
지금은 버전이 다르면 등록할 때 409 를 받아야 안다(D3 · D4). 서버와 버전이 다를 때 M5q 의 버전 따라가기로 맞출 수 있으면
warn 이고 「등록하면 서버 버전으로 따라간다」를 찍는다. editable 설치나 버전을 고정한 설치처럼 따라갈 수 없으면 FAIL 이다.
위 「첫 실행 안전」의 확인 규칙도 여기에 건다. 그래서 비TTY 에서 `server = "auto"` 로 찾은 서버를 확인받지 못하면
`worker --check` 가 종료 3 으로 끝난다(4.7).

#### `checks` 없는 프리셋 (결정 247)

`checks` 없는 프리셋은 지금처럼 도구가 있으면 eligible 이다(결정 91~93 유지, ⛔ Q6). 「증명 뒤 받기」를 모든 프리셋에
강제하면 기존 동작이 바뀐다.

#### 툴체인 카탈로그(서버 설정 템플릿의 주석 블록)

코어에는 rcm 자신이 쓰지 않는 도구의 이름을 넣지 않는다(PLAN 이식성). 예외는 호스트 사실용 허용 목록이다. rcm 자신이
호스트를 알려고 부르는 `sw_vers`, `brew`, `apt-get`, `dnf`, `pacman`, `systemd-detect-virt`, `xcode-select`, `nvidia-smi`,
`ioreg` 같은 이름이 여기에 든다. 그 목록은 코드 한 곳에 둔다. Flutter · Android · iOS(Xcode) · Node · Python
툴체인용 `[[tools]]` 와 `[[probes]]` 예시는 휠에 실리는 서버 설정 템플릿의 주석 블록에 둔다. 그 템플릿은
`examples/server.toml` 과 바이트가 같다. `examples/` 자체는 sdist 에만 실린다(A6). 버전 요구(예: Xcode 27 이상)는
`checks` 점검 프리셋으로 지킨다. 조사 메모는 코어에 버전 정규식과 최소 버전을 가진 검사 종류를 두자고 제안했지만 이 명세는
받지 않는다. 결정 246 이 코어에 버전 비교 문법을 넣지 않기로 했다.

아래는 2026-11-25 조사에서 나온 OS 별 점검과 설치 사실이다. 실측은 Apple Silicon Mac 한 대에서만 했다. Intel Mac 과
Linux 는 실측하지 못했다. `[미확인]` 은 조사가 확인하지 못한 것이다.

| 툴체인 | 점검 | 설치 안내의 예 | 함정 |
|---|---|---|---|
| 서비스 PATH | 워커 서비스와 같은 환경에서 잰다. 결과에 그 PATH 를 같이 적는다 | — | launchd 기본 PATH 에는 `/opt/homebrew/bin` 이 없다(정확한 값은 [미확인]). systemd 시스템 서비스의 기본은 `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin` 이다(https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html). nvm · fnm · pyenv · rbenv · sdkman 은 셸 함수나 shim 이라 서비스에서 안 보인다 |
| 호스트 사실 | macOS `sw_vers -productVersion`, Linux `/etc/os-release` 의 ID · ID_LIKE · VERSION_ID. arch 는 `uname -m`. Rosetta 아래서는 `sysctl -n hw.optional.arm64` 와 `sysctl -n sysctl.proc_translated` 로 본다. 컨테이너는 `/.dockerenv` · `/run/.containerenv` · `systemd-detect-virt -c`. 권한은 `id -u` | Homebrew 는 Apple Silicon macOS 15 이상이 Tier 1, Intel 이 Tier 3 이다(https://docs.brew.sh/Installation) | Rosetta 아래서는 `uname` 과 `platform.machine()` 이 x86_64 다. 컨테이너는 보통 root 이고 sudo 가 없다. root 면 안내에서 sudo 를 뺀다 |
| rcm 본체 Python | `python3.14` … `python3.11`, `python3` 순으로 `import sys,sqlite3,ssl,venv` 와 버전을 본다 | macOS `brew install python@3.13`. Ubuntu 22.04 는 deadsnakes PPA. Debian · Ubuntu 는 `python3.X-venv` 를 따로 깐다. RHEL 9.2 이상은 `sudo dnf install python3.11 python3.11-pip`. Arch `pacman -S python`. uv 로 `uv python install 3.12` | `_sqlite3` 나 `_ssl` 이 없으면 broken 이다(pyenv 소스 빌드). Ubuntu 22.04 apt 의 python3.11 은 3.11.0~rc1 이다. Homebrew Python 은 PEP 668 로 pip 를 거부한다 |
| C 툴체인 · git · make | `command -v` | macOS `xcode-select --install`(GUI). Debian `build-essential`. Fedora `dnf group install development-tools`. Arch `base-devel` | 헤드리스 CLT 설치는 softwareupdate 와 sudo 가 필요하므로 안내만 한다. macOS `/bin/bash` 는 3.2 다. slim 컨테이너 이미지에는 git · curl · ca-certificates · procps 가 없다 |
| Python 프로젝트 | 프로젝트 venv 기준 `<venv>/bin/python -m pytest --version`, `<venv>/bin/ruff --version` | 설치는 프로젝트 몫이다 | 전역 도구는 다른 인터프리터일 수 있다 |
| Node · 헤드리스 Chrome | `node --version`, `npm --version`. Chrome 은 `"$CHROME" --headless=new --disable-gpu --dump-dom about:blank` | brew node, fnm, NodeSource, 배포판 기본. Playwright `npx playwright install --with-deps chromium` | nvm 의 node 는 서비스 PATH 에 없다. `nvm which <ver>` 로 절대 경로를 프리셋 env 에 둔다. Ubuntu 의 chromium-browser 는 snap 빈 패키지다. 컨테이너 root 면 `--no-sandbox` |
| Flutter · Dart | `flutter --version --machine`, `flutter devices --machine`. 대상별로 따로 프로브한다 | https://docs.flutter.dev/install/manual | `flutter doctor` 는 기계용 출력이 없고 문제가 있어도 종료 0 이다. 그래서 종료 코드로 판정하지 않는다. stdout 에 업데이트 배너가 섞이므로 JSON 은 첫 `{` 부터 읽는다. 새 SDK 는 첫 실행에 내려받기를 하므로 시한을 길게 둔다. Homebrew cask 는 `brew upgrade` 때 조용히 올라간다 |
| Xcode | `xcode-select -p`, `xcodebuild -version`, `xcodebuild -checkFirstLaunchStatus`, `xcodebuild -license check`, `xcrun simctl list runtimes -j`, `xcodebuild -showsdks` | 첫 실행 `sudo xcodebuild -runFirstLaunch`, 라이선스 `sudo xcodebuild -license accept`, 런타임 `xcodebuild -downloadPlatform iOS`. CocoaPods 는 `brew install cocoapods` | CLT 만 있으면 `xcodebuild -version` 이 종료 1 이다. App Store 가 Xcode.app 을 그 자리에서 올려 툴체인과 SDK 가 조용히 바뀐다. M5n 의 지문이 이 구조를 이미 잡는다. 잡 단위로는 `DEVELOPER_DIR` 을 env 로 준다. CocoaPods 는 launchd 아래서 `LANG=en_US.UTF-8` 이 필요하다. iOS 서명 워커는 LaunchDaemon 이 아니라 사용자 LaunchAgent 여야 한다 [미확인] |
| Android | `java -version`(stderr), `javac -version`, macOS `/usr/libexec/java_home -v 17`, `JAVA_HOME`, `ANDROID_HOME`, `$ANDROID_HOME/licenses/` | JDK 17 이상(https://developer.android.com/build/releases/gradle-plugin). macOS `brew install --cask temurin@17`, Debian `openjdk-17-jdk-headless`, Arch `jdk17-openjdk`. SDK 는 `sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"`, 라이선스 `yes \| sdkmanager --licenses` | `/usr/bin/java` 도 shim 이다. Linux arm64 용 빌드 도구는 공식 배포가 x86_64 만이다 [미확인]. 에뮬레이터는 Linux 에서 `/dev/kvm` 이 필요하고 VM · Docker 안은 공식 미지원이다 |
| GPU | macOS `system_profiler SPDisplaysDataType -json`(1~3초라 캐시한다). Linux `nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader,nounits` | — | 컨테이너는 `--gpus all` 이 없으면 GPU 가 안 보인다 |

조사가 확인하지 못한 것은 이렇다. `xcode-select` 가 없을 때의 종료 코드, `xcodebuild -license check` 미동의 때의 종료 코드,
App Store 자동 업데이트 defaults 키, Fedora · Arch 의 Flutter Linux 패키지 이름, 최신 Fedora 의 java-17, Debian 13 ·
Ubuntu 26.04 의 NodeSource, Linux arm64 의 Chrome 과 Android 빌드 도구, `nvidia-smi` 의 현행 종료 코드, `ioreg` GPU 키,
Buildkite · GitLab · Xcode Cloud 세부, python.org 3.11 의 마지막 바이너리, launchd 기본 PATH 의 정확한 값이다. 예시
카탈로그는 이 항목을 확인된 사실처럼 쓰지 않는다.

비슷한 도구에서 가져올 원칙도 조사에 있다. 항목마다 상태 기호와 고칠 명령 한 줄을 둔다. 사람용 표와 `--json` 을 같이
낸다. 문제가 있으면 0 이 아닌 종료 코드를 낸다. sudo 로 자동 설치하지 않는다. 서비스 환경에서 점검하고 그 PATH 를
보여 준다. `flutter doctor` 에서는 문제가 있어도 0 인 종료 코드, 기계용 출력이 없는 것, stdout 배너를 가져오지 않는다.

### 4.6 요청자에게 알리기 (결정 248 e · f)

| 항목 | 정한 것 |
|---|---|
| `rcm wait` 사람용 마지막 줄 | 실행 머신을 적는다. 예: `#381 succeeded on build-02 · 6m 31s`. 실패면 실패 스텝을 적는다. 로컬 레인도 머신 이름을 적는다. 잡 JSON 의 `worker` 칸은 바꾸지 않고(null 이 로컬이라는 관례 유지) 새 키 `ran_on` 에 적는다. `ran_on` 은 저장 칸 없이 서버가 잡 문서를 만들 때 계산한다. 원격 워커면 `worker` 이고 로컬 레인이면 로컬 머신의 `display_name` 이다. 그 이름은 machines 표나 `gethostname` 에서 온다(`capabilities.py:480-481`). 모르면 null 이다. `ran_on` 은 잡에 `started_at` 이 있을 때만 계산하고 시작하지 못한 잡은 null 이다. `host_key` 는 sha256 앞 16자라서 그것만으로는 이름을 계산할 수 없다. `machine` 은 제출 본문의 대상 머신과 이름이 겹치므로 쓰지 않는다 |
| 산출물 | 새 칸을 만들지 않는다. 이미 있는 `artifacts.state` 를 사람용 줄에 적는다. wait JSON 에는 이미 실린다 |
| 산출물 경고 | `--fetch-artifacts` 없는 `rcm wait` 도 같은 상태를 읽어 산출물 실패를 경고 줄로 말한다. 경고로 보는 값은 `failed` · `dropped` · `unavailable` · `unknown` 이다. wait 가 끝날 때 아직 `collecting` 이나 `uploading` 이면 「수집 중」이라고 안내한다. 종료 코드는 바꾸지 않는다 |
| `[[notify]]` 환경변수 | `RCM_MACHINE` 하나를 더한다. `ran_on` 과 같이 `started_at` 이 있을 때만 계산하고 시작하지 못한 잡이면 null 이다. `RCM_REQUESTER` 는 이미 있다(E13) |
| 요청 머신의 OS 알림 | 클라이언트 쪽 `rcm wait --notify` 다. macOS 는 osascript, Linux 는 notify-send 를 쓴다. 도구가 없으면 조용히 건너뛰고 경고 한 줄을 찍는다. 기본은 꺼져 있다(⛔ Q3). `rcm run` 에는 더하지 않는다(§1) |
| 결정 6 | 서버 webhook 이 아니므로 결정 6 과 부딪히지 않는다. 결정 6 행은 M5a 의 notify url 과 M5r 의 `rcm/ci` 로 이미 비켜 갔으므로 문장을 PR5 가 사실대로 고친다 |

### 4.7 호환과 기존 결정

M5t-a 는 워커 프로토콜을 바꾸지 않는다. `/worker/spec` 에 키를 더하고 등록 본문에 호스트 사실을 더하는 것은 더하기만
하므로 옛 워커와 옛 서버는 새 키를 무시한다. 옛 서버에 붙은 새 doctor 는 새 키가 없으면 종료 3 이다(4.5).

종료 코드가 바뀌는 명령이 하나 있다. `rcm worker --check` 는 비TTY 에서 `server = "auto"` 로 찾은 서버를 확인받지 못하면
종료 3 으로 끝난다. 지금까지 이 명령에는 종료 3 이 없었다. 이 변화는 CHANGELOG 에도 적는다. `rcm worker --check` 에는
FAIL 이 될 수 있는 버전 비교 행도 새로 생긴다. 서버와 버전이 다른데 따라갈 수 없는 설치면 그 행이 FAIL 이다(4.5).

| 기존 결정 | 이 명세 |
|---|---|
| 결정 3(프리셋만 실행) | 유지한다. 설치 안내 문구는 보여 주기만 하고 실행하지 않는다 |
| 결정 4·5·33·34(클라이언트별 토큰, TLS 없음, 기본 127.0.0.1, TXT 에 비밀 금지) | 결정 4·5·34 는 유지한다. 결정 33 은 템플릿의 기본값만 정했으므로 템플릿은 그대로 두고 setup 은 역할 2·4 에서 Tailscale 주소를 먼저 권한다(⛔ Q10). TXT 는 바꾸지 않는다. `lanes=0` 은 이미 싣는다. LAN 이나 `0.0.0.0` 을 고르면 읽기가 열려 있다고 경고한다(⛔ Q14). `--yes` 로 역할 2·4 를 고를 때 `--bind` 를 필수로 두어 설치하자마자 LAN 에 열리지 않는다는 결정 33 의 뜻을 지킨다. TLS 없이 남는 위험은 M5t-c 의 위협 모델에서 다룬다 |
| 결정 6(webhook · 커밋 status · PR 댓글은 범위 밖) | PR5 가 문장을 사실대로 고친다. `rcm wait --notify` 는 클라이언트 쪽이다 |
| 결정 39(M5f, 「서버 레인 1」) | PR3 이 문장을 고친다 |
| 결정 214(잡 중간의 알림은 rcm 이 아니라 프로젝트 스크립트) | 유지한다. 이 명세가 더하는 것은 잡이 끝난 뒤의 알림뿐이다(`[[notify]]` 의 `RCM_MACHINE`, 클라이언트 쪽 `rcm wait --notify`) |
| 결정 91~93(측정 전에는 일을 받지 않음, `checks` 없는 프리셋) | 유지한다(⛔ Q6) |
| 결정 97(배치 기본 `pool`) | 유지한다. 원격 금지는 배치가 아니라 프리셋 표식으로 한다(결정 254) |
| 결정 171(버전 따라가기) | setup · doctor 는 따라가지 않는다(`NO_FOLLOW_COMMANDS`) |
| M5n §9(서비스는 파일만) | 유지한다(⛔ Q9) |
| M5n §13(자동 발급 · 자동 가입 금지, `lost` 자동 재실행 안 함) | 유지한다. M5t-a 는 토큰 발급과 가입을 바꾸지 않는다. 개정할지는 M5t-c 가 정한다 |
| PLAN 이식성 | 유지한다. rcm 자신이 쓰지 않는 도구의 이름은 서버 설정 템플릿의 카탈로그 주석에만 둔다 |

## 5. PR 나누기와 물결

PR 하나에 브랜치 하나, 워크트리 하나를 쓴다. 워크트리는 `../remote_ci_monitor-<what-it-does>` 이고 자기 `.venv` 를 둔다.
브랜치 이름은 시작할 때 `branch` 스킬로 정한다. 같은 물결에 든 PR 은 동시에 띄운다. 같은 물결의 PR 이 함께 고치는 파일과
머지 순서를 표에 적는다. 뒤에 머지하는 PR 은 앞 PR 이 머지된 dev 를 합친 뒤 머지한다. 앞 물결이 dev 에 머지되면 다음
물결로 넘어간다.

PR 번호는 4판에서 다시 매겼다. 3판의 PR1(결과 고리)과 PR5(짝짓기)는 M5t-b 와 M5t-c 로 갔다. PR1 에는 원격 금지
표식을 두었고 요청자 알림부터 뒤의 PR 은 하나씩 당겼다. PR2 · PR3 · PR4 는 3판과 같은 일이다.

| PR | 내용 | 결정 | 물결 | 같은 물결에서 겹치는 파일 | 머지 순서 |
|---|---|---|---|---|---|
| PR0 | 이 명세와 PLAN.md 반영(v2.13 머리, M5t-a 항목과 M5t-b · M5t-c 예정 줄, M5q 를 「완료, v0.8.0」으로, 결정 색인). 브랜치 `docs/m5t-machine-role-setup-plan`. 문서만이라 `--no-verify` 를 쓸 수 있다. 그래도 격리 반박 2명은 받는다 | 전부 | 0 | — | 먼저 |
| PR1 | 원격 금지 표식. 프리셋 키 `local_only` 와 설정 검증, 후보 머신 함수 하나와 그 결과를 쓰는 경로들(4.3), 후보가 빈 제출과 원격 recheck 의 409, 여러 진입점을 한 번에 보는 시험(TO15)과 그 변이. DB 를 바꾸지 않는다 | 254 | 1 | PR2 와 `config.py`(프리셋 키 / `[[tools]]`), `capabilities.py`(`/worker/spec`) | PR1 먼저 |
| PR2 | 환경 진단. 새 모듈 `doctor.py` 와 순수 `core/doctor.py`, cli `doctor`, `hostsample` 의 호스트 사실과 그 서버 저장(등록 본문을 `remote_workers.py` 메모리에, DB 아님), config `[[tools]]`, `/worker/spec` 에 키 더하기, `worker --check` 두 줄, coordinator 진단의 「로컬 레인 필요」, shim 판별 함수 하나(`measure_tools` · `missing_tools` · `preset_env` · `rcm check` 도구 행이 함께 쓴다), doctor 의 `--server` · `--config`. `NO_FOLLOW_COMMANDS` 에 `doctor` 를 넣는다 | 246, 247 | 1 | PR1 과 같다 | PR1 뒤. dev 를 합친 뒤 머지 |
| PR3 | 분배 전용 `lanes = 0`. config · capabilities(`@local`) · server health · `rcm check` 의 `capacity` 와 도구 행 표기 · 기동과 reload 때 갈 곳 잃은 잡의 reason · `local_only` 제출 409 · 후보 함수 결과로 세는 `capacity` 와 TO15 의 `capacity` 진입점 · coordinator 진단의 `capacity`(TD20 · TD38) · 결정 39(M5f)의 문장 · render · web 표기 | 243, 254 | 2 | PR4 와 `cli.py`(check capacity / setup), doctor 모듈 | PR3 먼저 |
| PR4 | 첫 실행과 setup. cli setup · 첫 실행 질문 · 설정 탐색 순서 · 포트 · `data_dir` · 광고 · 라벨 인자 · bind 선택과 `--yes` 의 `--bind` 필수 · 읽기 공개 경고 · 역할별 토큰 안내 · 서버 서비스 파일(필수 키, 설정 폴더 아래 자리, 표준 라벨) · `NO_FOLLOW_COMMANDS` 에 `setup` · 서버 없이 도는 진단 부르기 · 이미 있는 `data_dir` 거절과 `--reuse-data-dir` · 쓰는 중인 포트 경고 · 역할별 `--yes` 필수 인자 · `tools/guard_production.py` 의 세 규칙(운영 설정 경로, 운영 라벨, 운영 `data_dir`) | 239~242 | 2 | PR3 과 같다 | PR3 뒤 |
| PR5 | 요청자 알림. wait 사람용 줄과 `ran_on` 키 · 이미 있는 `artifacts.state` 의 사람용 줄과 경고 · `RCM_MACHINE` · `wait --notify` · 결정 6 문장 | 248 e·f | 3 | — | — |
| PR6 | 문서와 스크린샷(docs 스킬). README · Java 스텁이 shim 규칙으로 판별되지 않는다는 한계 · `local_only` · `docs/configuration.md` · `docs/operating.md` · `docs/usage.ko.md` · 서버 설정 템플릿의 툴체인 카탈로그 · 설치 스모크 | 246, 254 | 3 뒤 | — | — |
| PR7 | 실기 수용. 실기 하네스 `scripts/acceptance/m5t_live.sh`, §7 의 실기 행 전부, 보고서 `docs/acceptance/reports/` | — | PR6 뒤 | — | — |
| 릴리스 | v0.11.0 → dev → main(§8 개인정보 점검) → 태그 | — | PR7 뒤 | — | — |
| 운영 반영 | 오너 승인 뒤. 현행 규칙대로 | — | 릴리스 뒤 | — | — |

DB 판을 올리는 PR 은 없다.

여러 PR 이 같이 고치는 파일도 있다. `scripts/mutcheck.py`, `CHANGELOG.md`, 문서 잠금 시험이 그렇다. 각 PR 은 자기 항목만
더하고 앞 PR 이 머지되면 다시 맞춘다.

이 마일스톤은 새로 들인 개발 루프 스킬로 돈다(결정 251). 스킬은 `dev-loop` · `workplan` · `build-pr` · `live-verify` ·
`edge-audit` · `dev-batch` 다. 스킬 PR 이 먼저 머지된다는 전제다. 그렇지 않으면 같은 원칙을 손으로 지킨다. 이 워크트리에는 아직
그 스킬도 `tools/scenario_trace.py` 도 없다.

| 단계 | 하는 일 |
|---|---|
| 1 | `branch` 스킬로 이름을 확인하고 `git worktree add -b <브랜치> ../remote_ci_monitor-<…> origin/dev` 뒤 `.venv` 를 만든다 |
| 2 | 시험부터 쓴다. §7 의 시나리오 ID 를 시험 이름에 넣는다 |
| 3 | 구현한 뒤 바뀐 파일의 시험과 `./.venv/bin/ruff` 를 돌리고(웹이면 `node --test` 해당 파일, 새 변이는 `./.venv/bin/python scripts/mutcheck.py --only <이름>`), 전체 pytest · mutcheck · 설치 스모크는 `rcm/ci` 가 판정한다(개발 루프 스킬의 규칙) |
| 4 | `tools/scenario_trace.py` 로 시나리오 표와 시험을 대조한다 |
| 5 | 같은 PR 에서 문서를 고친다(docs 스킬 체크리스트) |
| 6 | `commit` 스킬과 `korean-writing` 스킬로 커밋과 PR 본문을 쓴다. 임시 파일 이름은 고유하게 짓는다(`pr-body-<브랜치>.md`) |
| 7 | 라운드마다 격리 검증 2명을 띄운다. 라운드 결과는 `verify-round:` 댓글로, 통과하면 `verified: <head sha>` 댓글을 단다 |
| 8 | `rcm/ci` 가 초록이면 `python3 tools/merge_pr.py <N>` 으로 머지하고 워크트리를 치운다 |

## 6. 진행 체크리스트

진행하면서 상태 칸을 채운다. 처음에는 모두 `[ ]` 다. M5t-b 와 M5t-c 의 명세는 이 체크리스트 밖이다.

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 이 명세 작성(4판, 5판, 6판) | PR0 | — | [ ] |
| 1 | 4라운드 격리 반박 2명 받기와 반영(§13), 5라운드 격리 반박 2명 받기와 반영, 6판 diff 검증 2명 받기 | PR0 | 0 | [ ] |
| 2 | 오너 질문 Q3 · Q5~Q10 · Q14 답 받기(§11) | — | 0 | [ ] |
| 3 | PR0 머지 | PR0 | 1 | [ ] |
| 4 | PR1 원격 금지 표식 | PR1 | 3 | [ ] |
| 5 | PR2 환경 진단 | PR2 | 3, Q6~Q8 | [ ] |
| 6 | PR3 분배 전용 `lanes = 0` | PR3 | 4·5, Q5 | [ ] |
| 7 | PR4 첫 실행과 setup | PR4 | 5·6, Q9 · Q10 · Q14 | [ ] |
| 8 | PR5 요청자 알림 | PR5 | 6·7, Q3 | [ ] |
| 9 | mutcheck 새 변이 셋(§7)이 들어갔는지 확인 | PR1 · PR2 · PR3 | — | [ ] |
| 10 | 이 레포를 쓰는 앱 프로젝트의 release-* · deploy-* 프리셋에 `local_only` 달기(결정 254) | — | 16, 17 | [ ] |
| 11 | PR6 문서 · 스크린샷 · 서버 설정 템플릿의 툴체인 카탈로그 | PR6 | 8 | [ ] |
| 12 | PR7 실기 수용과 보고서 | PR7 | 11 | [ ] |
| 13 | CHANGELOG `[Unreleased]` 정리 | PR7 · 릴리스 | — | [ ] |
| 14 | 릴리스 PR dev→main 개인정보 점검(§8) | 릴리스 | 12·13 | [ ] |
| 15 | 태그 v0.11.0 · 릴리스 확인 | 릴리스 | 14 | [ ] |
| 16 | 운영 반영 오너 승인 · 큐가 빌 때 재시작 | 운영 반영 | 15 | [ ] |
| 17 | `local_only` 를 달기 전에 그 프리셋을 읽는 서버 목록 확인 | — | 16 | [ ] |

10 번은 운영 반영 뒤에 한다. 모르는 프리셋 키는 기동 실패이고(`config.py:818-820`) include 로 들인 파일도 프리셋을
싣는다(`config.py:1076`). 그래서 `local_only` 를 단 프리셋을 읽는 서버는 모두 v0.11.0 이상이어야 한다. 그 서버 목록은
표식을 달기 전에 17 번에서 확인한다.

## 7. 시험 계획

실기는 운영 빌드 머신에서 운영과 다른 시험 포트와 `data_dir` 로 띄운 `coordinator-worker` 하나로 한다. 여기에 같은 Mac 에서
다른 `data_dir` 로 띄운 일회용 워커 하나를 붙인다. TL12 는 `lanes = 0` 인 시험 서버를 하나 더 띄워서 한다. 그 서버도
운영과 앞의 시험 서버와 다른 포트와 `data_dir` 을 쓴다. Docker 가 있으면 Linux 컨테이너 워커를 하나 더한다. 운영 설정과
운영 `data_dir` 과 서비스는 건드리지 않는다.

실기를 운영 머신에서 하므로 아래를 지킨다. TR16 은 인자 없는 `rcm` 이 아니라
`rcm setup --role … --port <시험 포트> --data-dir <시험 경로> --label <시험 라벨> --bind 127.0.0.1 --no-advertise --yes` 로 설정한다. 같은 Mac 의 일회용 워커는 루프백으로 붙는다. Linux 컨테이너 워커를 붙일 때만 `--bind` 를 이 Mac 의 구체 LAN 주소로 바꾸고, 운영 서버의 포트와 겹치지 않는 시험 포트를 쓴다.
실기 하네스는 PR7 이 `scripts/acceptance/m5t_live.sh` 로 만든다. 하네스는 bootstrap 하기 전에 쓰인 server.toml 의
`port` · `advertise` · `data_dir` 이 시험 값인지 확인한다. bootstrap 할 plist 의 `--config` 경로 · `HOME` · 로그 경로 ·
라벨도 확인한다. 시험 서버는
`advertise = false` 로 띄운다. 시험 워커와 클라이언트는
`--server <URL>` 로 서버를 명시한다. 시작하기 전에 셸의 `RCM_*` 환경 변수를 모두 지운다. `HOME` 과 `XDG_CONFIG_HOME` ·
`XDG_DATA_HOME` · `XDG_STATE_HOME` · `XDG_CACHE_HOME` 넷을 모두 운영과 따로 둔다. 서비스 파일 라벨은 `--label` 로 시험 전용
값을 주고 운영 라벨이면 실기 하네스가 거절한다. 하네스가 운영 라벨로 보는 것은 설치된 유닛의 라벨과 표준 라벨 둘이다. 실기가 끝나면 시험 라벨의 launchd 에이전트만 내리고 지운다. M5s 처럼 `data_dir`
을 `/tmp` 아래에 두지 않고 알림 훅은 가짜만 쓴다. 보고서는 `docs/acceptance/reports/` 에 둔다.

운영 서버가 `0.0.0.0` 에 bind 한 포트와 같은 포트로 시험 서버가 구체 주소에 bind 하면 macOS 가 연결을 가로채는지는 확인하지
못했다. 시험 포트를 명시하고 하네스가 확인하는 위 절차로 이 문제는 해소된다.

표의 「어디서」 칸은 단위 · e2e · 브라우저 · 실기를 가른다. 시나리오 끝의 `(live)` 는 실기로만 증명하는 행이다. 시험
이름에는 ID 를 넣는다(결정 251). 어느 행을 단위로, 어느 행을 실기로 증명할지는 첫째 판이 나누고 뒤의 판들이 격리 반박에
맞춰 고쳤다. PR 에서 바뀔 수 있다. 4판은 M5t-a 범위 밖의 행을 지웠고 남은 행의 번호는 바꾸지 않았다. 새 행은 표마다
이어지는 번호를 쓴다. 짝짓기 표(TJ)와 결과 고리 표(TF)는 이 문서에서 뺐다. 6판은 경로마다 흩어져 있던 원격 금지 행을
TO15 하나로 묶었다. 묶인 행은 지우지 않고 「TO15 로 묶었다」로 남겼다.

### TR 역할 · 첫 실행 · setup

<!-- scenario-prefix: TR -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| TR1 | stdin 이나 stderr 가 TTY 가 아닐 때 인자 없는 `rcm` | 묻지 않고 usage 오류로 종료 2 | 단위 | PR4 |
| TR2 | TTY 이지만 설정 파일이 하나라도 있을 때 인자 없는 `rcm` | 묻지 않고 종료 2 | 단위 | PR4 |
| TR3 | TTY 이고 설정 파일이 하나도 없을 때 인자 없는 `rcm` | 설정을 시작할지 묻는다 | 단위 | PR4 |
| TR4 | 설정 파일 조합마다 역할 읽어 내기 | `lanes ≥ 1` 이면 `coordinator-worker`, `lanes = 0` 이면 `coordinator`, `worker.toml` 이면 `worker`, `client.toml` 이면 `client`, 여럿이면 여럿. `$RCM_CONFIG` 와 `./rcm.toml` 은 서버 설정으로 읽는다 | 단위 | PR4 |
| TR5 | `rcm setup --role coordinator-worker --yes` | 묻지 않고 `server.toml`(`lanes ≥ 1`)을 쓴다. `worker.toml` 은 만들지 않는다 | 단위 | PR4 |
| TR6 | 이미 있는 설정 파일 | 덮지 않는다. `--force` 를 준 파일만 덮는다 | 단위 | PR4 |
| TR7 | 여러 파일을 쓰다가 중간에 실패 | 반쪽 설정이 남지 않는다 | 단위 | PR4 |
| TR8 | 토큰 | stdout 과 stderr 에 되풀이되지 않는다 | 단위 | PR4 |
| TR9 | 운영 `data_dir` 이 있는 환경에서 setup | setup 은 어떤 Store 도 열지 않는다 | 단위 | PR4 |
| TR10 | 역할 2·4 의 bind 선택지 | Tailscale 주소가 있으면 그것이 추천이다. 없으면 LAN 인터페이스 주소(DHCP 예약 권고)나 `0.0.0.0` 을 고르게 한다. 둘 가운데 하나를 고르면 읽기를 같은 망 누구나 볼 수 있다고 경고한다. 없는 `read_auth` 모드를 제안하지 않는다. 직접 입력은 이 머신의 인터페이스에 있는 주소만 받는다 | 단위 | PR4 |
| TR11 | 서버 서비스 파일의 필수 키 | 파일 디스크립터 상한(launchd `NumberOfFiles` 4096, systemd `LimitNOFILE=4096`), `HOME`, `WorkingDirectory`, 로그 경로가 있다. PATH 에 `/usr/sbin` 이 들어 있고 venv 의 bin 이 앞에 오지 않는다 | 단위 | PR4 |
| TR12 | setup 의 서비스 단계 | 서비스를 등록하거나 시작하지 않고 명령만 출력한다 | 단위 | PR4 |
| TR13 | setup 의 마지막 단계 | 서버 없이 도는 진단(rcm 자신의 요구, 호스트 사실, 설정 파일 검증)만 돌려 조언으로 보인다. 끝에 「서비스를 띄우고 토큰을 넣은 뒤 `rcm doctor --role <r>`」을 출력한다 | 단위 | PR4 |
| TR14 | setup 과 버전 따라가기 | setup 은 따라가지 않는다(`NO_FOLLOW_COMMANDS`) | 단위 | PR4 |
| TR15 | 같은 머신에 `worker.toml` 이 이미 있는 상태에서 `coordinator-worker` setup | 막지 않고 경고만 한다 | 단위 | PR4 |
| TR16 | 새 `data_dir` 의 빈 환경에서 `rcm setup --role coordinator-worker --port <시험 포트> --data-dir <시험 경로> --label <시험 라벨> --bind 127.0.0.1 --no-advertise --yes` 로 설정하고 출력된 명령으로 띄운다. 출력된 `rcm token --config <시험 server.toml> add` 를 시험 빌드로 실행하고, 셸의 `RCM_*` 를 지운 뒤 시험 토큰과 `--server` 로 `rcm check` (live) | bootstrap 전에 하네스가 server.toml 의 `port` · `advertise` · `data_dir` 과 plist 의 `--config` 경로 · `HOME` · 로그 경로 · 라벨이 시험 값인지 확인한다. 서버가 뜨고 `rcm check` 가 FAIL 없이 끝난다. 정리할 때는 시험 라벨만 내린다 | 실기 | PR7 |
| TR17 | doctor 와 버전 따라가기 | doctor 는 따라가지 않는다(`NO_FOLLOW_COMMANDS`) | 단위 | PR2 |
| TR18 | `--label` 없는 setup 의 서비스 파일, 같은 라벨의 파일이 이미 있음, 같은 라벨이 이미 로드돼 있음 | 표준 라벨을 쓴다. 있는 파일은 `--force` 없이 덮지 않는다. 로드돼 있으면 경고만 한다. 제품은 운영 라벨이라는 이유로 거절하지 않는다 | 단위 | PR4 |
| TR19 | 설정 파일 없이 환경변수(`RCM_SERVER` · `RCM_TOKEN`)만 있는 TTY 에서 인자 없는 `rcm` | 「설정 있음」으로 보고 묻지 않는다 | 단위 | PR4 |
| TR20 | setup 이 운영 설정 경로(`~/.config/rcm/*.toml`)에 쓰거나 운영 라벨의 서비스 파일을 쓰려 함 | `tools/guard_production.py` 가 거절한다. 훅은 setup 과 같은 규칙(`XDG_CONFIG_HOME`, `HOME`)으로 쓸 경로를 계산한다 | 단위 | PR4 |
| TR21 | `coordinator` 나 `coordinator-worker` 의 setup 이 끝남 | 토큰을 만들지 않고 `rcm token --config <server.toml> add <이름>` 을 출력한다. 그 명령은 client 권한이고 `--admin` 이 없다 | 단위 | PR4 |
| TR22 | `--port` · `--data-dir` · `--advertise`/`--no-advertise` · `--label` 을 준 setup, 그리고 같은 값을 대화형으로 고르는 setup | 고른 값이 server.toml 과 서비스 파일에 쓰인다. 대화형에서도 묻고 기본값은 템플릿대로, 라벨 기본은 표준 라벨이다. `data_dir` 은 절대 경로로 쓰인다 | 단위 | PR4 |
| TR23 | `--yes` 로 역할 2·4 를 고르고 `--bind` 를 주지 않음 | usage 오류로 종료 2. `0.0.0.0` 을 저절로 고르지 않는다 | 단위 | PR4 |
| TR24 | setup 이 서비스 파일을 쓰는 자리 | `<config dir>/service/<label>.plist`(systemd 는 `.service`)에 쓰고 `~/Library/LaunchAgents/` 에는 쓰지 않는다. 설치할 자리로 복사하는 명령과 bootstrap 명령을 출력한다. `--service-file <경로>` 를 주면 그 경로에 쓴다 | 단위 | PR4 |
| TR25 | 역할 2·4 에서 Tailscale 주소를 고름 | server.toml 에 `advertise = false` 가 쓰이고 tailnet 전체에 읽기가 열린다고 경고한다 | 단위 | PR4 |
| TR26 | `worker` 역할의 setup | worker.toml 에 `server = "auto"` 가 아니라 코디네이터의 명시 URL 이 쓰인다 | 단위 | PR4 |
| TR27 | `coordinator` 나 `coordinator-worker` 의 setup 이 출력하는 토큰 명령 | client 토큰 명령과 함께 `rcm token add <이름> --worker` 도 출력한다 | 단위 | PR4 |
| TR28 | setup 의 종료 코드 | 설정을 썼으면 0, 쓰지 못했으면 1, 사용법 오류는 2 | 단위 | PR4 |
| TR29 | 설정 파일 없이 `RCM_WORKER_TOKEN` 만 있고 서버는 `--server` 로 주는 워커 환경의 TTY 에서 인자 없는 `rcm` | 「설정 있음」으로 보고 묻지 않는다 | 단위 | PR4 |
| TR30 | `XDG_CONFIG_HOME` 이 있을 때 첫 실행 판정 | 기존 탐색 함수를 그대로 부른다. `server.toml` 과 `client.toml` 은 XDG 쪽과 `~/.config/rcm` 을 둘 다 보고 `worker.toml` 은 `user_config_dir()` 하나만 본다 | 단위 | PR4 |
| TR31 | 고른 `data_dir` 에 `rcm.sqlite3` 가 이미 있는 setup, 그리고 같은 상황에 `--reuse-data-dir` 를 준 setup | 앞의 것은 거절하고 종료 1 이며 새 폴더를 고르거나 `--reuse-data-dir` 를 주라고 안내한다. 뒤의 것은 그 폴더를 그대로 쓴다 | 단위 | PR4 |
| TR32 | 고른 포트를 지금 쓰고 있는 setup | 경고한다 | 단위 | PR4 |
| TR33 | 플래그로 준 `data_dir`, 또는 그 명령의 `HOME` 으로 푼 템플릿 기본값이 운영 `data_dir` 인 `rcm setup` | `tools/guard_production.py` 가 거절한다 | 단위 | PR4 |
| TR34 | 워크트리 빌드로 돌린 setup 이 쓴 서버 서비스 파일 | setup 을 실행한 빌드(`_this_rcm()`)를 가리킨다 | 단위 | PR4 |
| TR35 | `--yes` 로 `client` 를 고르고 `--server` 를 주지 않음, `worker` 를 고르고 `--server` · `--pool` · `--lanes` 가운데 하나를 주지 않음 | usage 오류로 종료 2 | 단위 | PR4 |
| TR36 | 같은 라벨이 로드돼 있는지 보는 setup 과 setup 이 출력하는 복사 명령 | 로드 여부는 `launchctl print` 나 `systemctl --user is-enabled` 같은 읽기 전용 조회로만 본다. 복사 명령은 있는 파일을 덮지 않는 `cp -n` 꼴이다 | 단위 | PR4 |
| TR37 | 서버 서비스 파일의 로그 경로 | 로그 파일 이름이 라벨에서 만들어진다 | 단위 | PR4 |
| TR38 | `$RCM_CONFIG` 가 없는 파일을 가리킬 때 역할 읽어 내기 | usage 오류로 종료 2 | 단위 | PR4 |

### TD 환경 진단

TD36(토큰에 저장된 풀로 `/worker/spec` 거르기)은 M5t-c 로 넘겨서 지웠다.

<!-- scenario-prefix: TD -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| TD1 | CLT 없는 macOS 에서 `/usr/bin` shim 인 도구 | 실행하지 않고 `missing (needs Command Line Tools)` | 단위(`xcode-select` 가짜) | PR2 |
| TD2 | 서비스 PATH 와 셸 PATH 의 결과가 다름 | 둘 다 보이고 다르다고 표시한다 | 단위 | PR2 |
| TD3 | 프로브 시한 초과 | `unknown`, 종료 3 | 단위 | PR2 |
| TD4 | 서버 불통 | 종료 3(0 이 아니다) | 단위 | PR2 |
| TD5 | 옛 서버(`/worker/spec` 에 새 키가 없음) | 다른 풀 프리셋을 셀 수 없다고 말하고 종료 3 | 단위 | PR2 |
| TD6 | Rosetta 아래에서 실행 | 실제 arch 를 바로 안다 | 단위 | PR2 |
| TD7 | 컨테이너 안의 root | rcm 자신의 설치 안내에서 sudo 를 뺀다. `[[tools]]` 에서 온 문구에서는 빼지 않는다 | 단위 | PR2 |
| TD8 | env 가 있는 프리셋 | 사람용 출력과 JSON 어디에도 env 값이 없다 | 단위 | PR2 |
| TD9 | `[[tools]]` 문구에 제어문자, 300자 초과, 모르는 OS 키, 같은 이름 두 번 | 기동 실패. `requires` 항목에도 프로브 argv[0] 에도 없는 이름은 warn. 프로브 argv[0] 과 맞는 이름은 warn 이 아니다 | 단위 | PR2 |
| TD10 | mDNS 로 찾은 서버 | 사람이 확인하기 전에는 인증된 요청도 프로브도 보내지 않는다. URL 을 명시한 서버는 이 확인이 없다 | 단위 | PR2 |
| TD11 | 도구는 다 있고 `checks` 가 남은 프리셋 | `needs_check` 로 적고 ok 라 부르지 않는다 | 단위 | PR2 |
| TD12 | 진단을 돌린 뒤 서버의 판정 | 진단은 서버에 아무것도 보내지 않고 판정이 바뀌지 않는다 | 단위 | PR2 |
| TD13 | 전부 `ready`, 이 머신이 든 풀의 `pool` 프리셋에 `missing` 하나, 사용법 오류 | 각각 종료 0, 1(빠진 것과 설치 안내), 2 | 단위 | PR2 |
| TD14 | Python 3.11.0~3.11.3 | rcm 자신의 요구가 FAIL, 종료 1 | 단위 | PR2 |
| TD15 | `/worker/spec` 의 새 키 | `pool` · `pools` · `placement` · `checks` · `tools` 가 실리고 옛 워커는 무시한다 | 단위 | PR2 |
| TD16 | 다른 풀 전용 프리셋 | 이 워커의 대상에서 빠진다 | 단위 | PR2 |
| TD17 | `--offline` | `git ls-remote` 를 부르지 않는다. 코디네이터 접속은 그대로 한다 | 단위 | PR2 |
| TD18 | `rcm worker --check` | 기존 줄은 그대로이고 끝에 「빠진 것 N개」 줄이 붙는다. 서버와 버전이 다를 때 따라갈 수 있으면 버전 행이 warn(「등록하면 서버 버전으로 따라간다」)이고, editable 설치나 고정 설치라 따라갈 수 없으면 FAIL | 단위 | PR2 |
| TD19 | `/api/status` 와 `/api/machines` | `tools` 가 실리지 않는다 | 단위 | PR2 |
| TD20 | `rcm doctor --role coordinator` | 어느 머신도 못 돌리는 프리셋과 `pool` 전용이라 못 가는 프리셋을 알린다. 프리셋 설정은 스스로 바꾸지 않는다 | 단위 | PR3 |
| TD21 | 워커 등록 본문의 호스트 사실 | OS 버전 · arch · Rosetta · 컨테이너 · root · 패키지 관리자 · Python 이 실리고 `rcm machines` 에 보인다 | e2e | PR2 |
| TD22 | `checks` 없는 프리셋 | 도구가 있으면 지금처럼 eligible(결정 247) | 단위 | PR2 |
| TD23 | 서버 설정 템플릿 주석 블록의 툴체인 카탈로그를 설정으로 읽기 | 기동 검사를 통과한다. 코어 코드에는 호스트 사실용 허용 목록 밖의 도구 이름이 없다. 그 목록은 코드 한 곳에 있다 | 단위 | PR6 |
| TD24 | 실제 Mac 의 워커 서비스 환경에서 `rcm doctor --role worker` (live) | 사람용 표와 `--json` 이 같은 판정을 내고 측정 PATH 를 적는다 | 실기 | PR7 |
| TD25 | Linux 컨테이너 워커에서 `rcm doctor --role worker`, Docker 가 있을 때만 (live) | 패키지 관리자와 root 를 알아보고 sudo 없는 안내를 낸다 | 실기 | PR7 |
| TD26 | `worker` 나 `coordinator-worker` 인 이 머신에 올 프리셋이 0개 | 종료 1 과 「이 머신에는 잡이 오지 않는다」, 그 이유 | 단위 | PR2 |
| TD27 | 워크스페이스 안 도구를 요구하는 프리셋 | `pool` 프리셋이면 `unknown` 이 아니라 warn 이고 종료 0. `any-capable` 프리셋이면 「오지 않음」으로 센다 | 단위 | PR2 |
| TD28 | 비TTY 에서 mDNS 로 찾은 서버, URL 을 명시하지 않음 | 확인을 받지 못했으므로 종료 3. `rcm worker --check` 도 같다 | 단위 | PR2 |
| TD29 | 셸에 `JAVA_HOME` 이 있고 서비스 파일에는 없음 | 깨끗한 환경으로 재므로 셸 값이 측정에 새지 않는다. `env_passthrough` 에서 빠진 변수는 warn | 단위 | PR2 |
| TD30 | `rcm doctor --role coordinator-worker` | 로컬 레인은 서버가 잰 값(`/api/health` 의 service preset tools, `@local` 판정)으로 보이고 원격 워커가 있으면 그쪽도 함께 보인다 | 단위 | PR2 |
| TD31 | `read_auth = none` 인 서버의 `/api/machines` 익명 응답 | 새 호스트 사실이 없다. 토큰 있는 응답에만 있다 | 단위 | PR2 |
| TD32 | 서버가 보낸 설치 문구에 제어문자 | 워커가 찍기 전에 정리한다 | 단위 | PR2 |
| TD33 | `/usr/bin/` 아래이고 바이트에 `libxcselect` 가 있는 실행 파일 | CLT shim 으로 판별한다. 도구 이름 목록을 쓰지 않는다. 같은 규칙이 워커의 프로브 실행에도 걸린다 | 단위 | PR2 |
| TD34 | `missing` 과 판정 못 함이 함께 있음 | 종료 1(우선순위 1, 3, 0) | 단위 | PR2 |
| TD35 | 토큰이 없거나 401 | 종료 3 과 「토큰이 필요하다」. 출력 경로에 홈 경로가 없다 | 단위 | PR2 |
| TD37 | 깨끗한 환경에서 도는 프로브가 보는 환경 변수, launchd 와 systemd 각각 | 그 서비스 관리자의 기본 변수 목록과 서비스 파일의 환경 변수만 있다 | 단위 | PR2 |
| TD38 | `lanes = 0` 인 `coordinator` 의 doctor | 「올 프리셋이 0개면 1」 대신 `capacity` 규칙으로 끝난다. 모든 프리셋에 갈 수 있는 머신이 없으면 1, 일부 프리셋만 그렇다면 warn | 단위 | PR3 |
| TD39 | `client` 의 doctor | 서버 도달 · 버전 · 토큰만 본다. 서버에 닿지 못하면 종료 3 | 단위 | PR2 |
| TD40 | 도구가 빠진 `any-capable` 프리셋 | 「이 머신에는 오지 않음」과 설치 안내를 warn 으로 찍고 종료 코드에 넣지 않는다 | 단위 | PR2 |
| TD41 | `coordinator` · `coordinator-worker` 진단, client 토큰이 있을 때와 없을 때 | 로컬 server.toml 을 읽고 DB 는 열지 않는다. bind(`0.0.0.0` 이면 127.0.0.1)와 port 로 `/api/health` · `/api/machines` 를 client 토큰으로 읽는다. 토큰이 없으면 서버 쪽 칸이 `—` 이고 종료 3 | 단위 | PR2 |
| TD42 | 서버를 재시작한 뒤 원격 워커의 새 호스트 사실 | 주기적인 `/worker/capabilities` 보고로 다시 채워진다. 받기 전에는 `—` | e2e | PR2 |
| TD43 | CLT 없는 macOS 에서 `requires` 에 `/usr/bin` shim 도구가 있는 프리셋의 서버 판정 | 서버의 `measure_tools` 에도 shim 규칙이 걸려 doctor 와 판정이 같다 | 단위 | PR2 |
| TD44 | `read_auth = basic` 인 서버의 `/api/machines` | 새 호스트 사실은 토큰이 있는 쪽에만 실린다 | 단위 | PR2 |
| TD45 | 서비스 파일 찾는 순서, 찾은 파일이 둘 이상 | `--service-file` 로 명시한 파일, `<config dir>/service/*`, 표준 라벨의 설치 자리, 「서비스 파일 없음」 표시 순이다. 둘 이상이면 고르게 하고 비TTY 면 종료 2 | 단위 | PR2 |
| TD46 | 서비스 PATH 와 셸 PATH 를 찍음 | 둘 다 홈 경로가 `~` 로 가려진다 | 단위 | PR2 |
| TD47 | `--role` 없이 doctor | 설정에서 읽어 낸 역할이 하나면 그 역할로 돈다. 여럿이면 usage 오류로 종료 2 | 단위 | PR2 |
| TD48 | worker.toml 에 `server = "auto"` | doctor 가 warn 한다 | 단위 | PR2 |
| TD49 | 설정 파일 없이 `RCM_WORKER_TOKEN` 과 `--server` 만 쓰는 워커의 doctor | 「설정 있음」으로 본다 | 단위 | PR2 |
| TD50 | macOS 의 Local Network 권한 | 판정하지 않고 안내만 한다 | 단위 | PR2 |
| TD51 | `/worker/spec` 의 `pools` | 프리셋의 `pools` 키가 그대로 실린다 | 단위 | PR2 |
| TD52 | CLT 없는 macOS 에서 `/usr/bin` shim 도구를 `measure_tools`, 잡 직전의 `missing_tools`, `preset_env` 측정, `rcm check` 의 도구 행이 각각 판정 | 넷이 같은 판별 함수를 써서 같은 결과를 낸다. `xcode-select` 자신은 shim 으로 판별하지 않는다 | 단위 | PR2 |
| TD53 | `worker` 역할에서 도구가 빠진 `any-capable` 프리셋이 있고 이 머신에 올 프리셋이 0개 | 종료 1 | 단위 | PR2 |
| TD54 | doctor 의 호스트 사실 명령과 서버 · 워커의 `[[probes]]` | 앞의 것은 `LANG=C` 로 돌고 뒤의 것에는 `LANG=C` 가 걸리지 않는다 | 단위 | PR2 |
| TD55 | 서비스 파일에 환경 변수가 있음 | doctor 가 변수 이름만 찍고 값은 찍지 않는다 | 단위 | PR2 |

### TL 분배 전용 `lanes = 0`

<!-- scenario-prefix: TL -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| TL1 | `lanes = 0` 설정 | 서버가 뜬다 | 단위 | PR3 |
| TL2 | `lanes = 0` 이고 워커가 없을 때 잡 제출 | 잡은 대기(실패 아님), ETA `—`. `--machine @local` 로 내면 409 와 「이 서버에는 로컬 레인이 없다」 | e2e | PR3 |
| TL3 | 같은 상황의 헬스와 `rcm check` | `/api/health` 의 `capacity` 가 「갈 수 있는 머신이 있는 프리셋 수 / 전체 프리셋 수」로 0 이고 HTTP 상태는 그대로다. `rcm check` 에 `capacity` FAIL | 단위 | PR3 |
| TL4 | 워커가 붙음 | 대기 잡이 바로 배치된다 | e2e | PR3 |
| TL5 | 제출 응답의 `eligible_now` | `@local` 이 없다 | 단위 | PR3 |
| TL6 | 자동 점검 | `@local` 을 겨냥한 점검 잡이 생기지 않는다 | 단위 | PR3 |
| TL7 | `@local` 머신 | 살아 있지 않다 | 단위 | PR3 |
| TL8 | 로컬 프로브 | 돌지 않는다 | 단위 | PR3 |
| TL9 | 서버 재시작 | 큐가 남는다 | e2e | PR3 |
| TL10 | mDNS TXT | `lanes=0` | 단위 | PR3 |
| TL11 | 터미널과 웹의 표기 | `lanes 0/0 busy` 대신 손본 표기, 호스트 카드에 「잡을 돌리지 않는 머신」 | 단위 · 브라우저 | PR3 |
| TL12 | `lanes = 0` 인 시험 서버(§7)에 일회용 워커를 사람이 옮긴 토큰과 명시 URL, 코디네이터의 기본 풀로 붙이고 배치를 바꾸지 않은 기본 풀 프리셋을 끝까지 (live) | 워커가 돌리고 결과가 요청자에게 돌아온다 | 실기 | PR7 |
| TL13 | `pools_without_workers` 의 기본 풀 | `lanes = 0` 이면 기본 풀도 든다. `lanes ≥ 1` 이면 기본 풀이 빠진다 | 단위 | PR3 |
| TL14 | `capacity` 가 0 인 프리셋이 있음 | 그 프리셋 목록이 `rcm check` 와 `rcm doctor --role coordinator` 에 보인다 | 단위 | PR3 |
| TL15 | lanes 를 1 이상에서 0 으로 바꿔 재시작하거나 `lanes = 0` 서버에서 reload 로 `local_only` 를 새로 담. 대기 중인 `local_only` 잡과 `@local` 지정 잡이 있음 | 기동이나 reload 때 그 잡에 reason `no_capable_machine` 과 ETA null 이 달린다. 실패로 닫히지 않는다 | 단위 | PR3 |
| TL16 | `lanes = 0` 서버의 `rcm check` | `local preset tools` · `service preset tools` 행이 FAIL 이 아니라 「이 머신은 잡을 돌리지 않는다」로 그려진다 | 단위 | PR3 |
| TL17 | `capacity` 를 세는 법, 프리셋이 0개인 설정 | 후보 함수 결과 가운데 살아 있는 머신만 센다. `checks` 가 없는 `pool` 프리셋은 판정과 상관없이 그 풀의 살아 있는 레인을 센다. 프리셋이 0개면 `capacity` 행을 그리지 않는다 | 단위 | PR3 |

### TO 원격 금지 표식 `local_only`

<!-- scenario-prefix: TO -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| TO1 | 프리셋에 `local_only = true`, `local_only = true` 이고 `pools = []` 인 프리셋, 그리고 키가 없는 프리셋 | 셋 다 설정이 받는다. 키가 없으면 false 다 | 단위 | PR1 |
| TO2 | TO15 · TO16 으로 묶었다 | — | — | — |
| TO3 | `local_only` 프리셋의 대기 잡과 로컬 레인 | 로컬 레인이 집는다 | 단위 | PR1 |
| TO4 | `lanes = 0` 서버에 `local_only` 프리셋 제출 | 409 와 사람용 이유 문장. 잡이 큐에 들지 않는다 | 단위 | PR3 |
| TO5 | TO15 로 묶었다 | — | — | — |
| TO6 | `rcm doctor --role coordinator` | `local_only` 프리셋을 「로컬 레인 필요」로 보인다 | 단위 | PR2 |
| TO7 | `local_only` 가 없는 프리셋의 대기 잡과 그 풀의 원격 워커 | 지금처럼 원격 워커가 집는다 | 단위 | PR1 |
| TO8 | TO15 로 묶었다 | — | — | — |
| TO9 | `local_only` 이면서 placement 가 `pool` 이고 `pool` 이 기본 풀이 아닌 프리셋, 그리고 `pool` 은 기본 풀이고 `pools` 에 기본 풀이 아닌 항목이 있는 프리셋 | 앞의 것은 기동 실패다. 뒤의 것은 기동하고 그 풀로 낸 제출이 409 다(TO10) | 단위 | PR1 |
| TO10 | `local_only` 프리셋을 기본 풀이 아닌 `--pool` 로 제출 | 409 | 단위 | PR1 |
| TO11 | TO15 로 묶었다 | — | — | — |
| TO12 | `checks` 에 쓰인 `local_only` 점검 프리셋과 `lanes = 0` 서버 | 「갈 곳 없음」으로 보이고 점검 잡을 만들지 않는다 | 단위 | PR3 |
| TO13 | supersede 나 합류할 대상이 있는데 409 가 날 `local_only` 제출 | 409 검사가 supersede 와 합류보다 먼저 돈다 | 단위 | PR1 |
| TO14 | TO15 로 묶었다 | — | — | — |
| TO15 | `local_only` 프리셋과 그 풀의 살아 있는 원격 워커를 두고 한 시험에서 여러 진입점을 부른다. 배치(`placement_for` 의 지정 · capable · pool 갈래)와 ETA, 제출 응답의 `eligible_now`, `capacity`, 자동 점검과 recheck 의 대상과 점검 정체 판정, `/worker/spec`, 원격 claim 의 네 갈래(지정 · `pool` · `capable` · `known`)와 claim 에 넘기는 제외 목록, `/api/eta` 의 pool 경로, `/api/machines` 의 판정과 `eligible_count` | 어느 진입점에서도 원격 머신이 그 프리셋의 후보에 나타나지 않는다. `/worker/spec` 에서는 프리셋이 env 와 함께 빠진다. 로컬 레인은 지금처럼 집는다. 후보 함수의 원격 제외를 지운 변이에서는 이 시험이 빨개진다 | 단위 | PR1(`capacity` 진입점은 PR3) |
| TO16 | 후보가 빈 `local_only` 제출. `--machine` 으로 원격 워커를 지정, `local_accept_any = false` 인데 `any-capable`, capable 갈래에서 `@local` 이 `tool_missing`. 비교 행: `@local` 이 점검 판정(`check_failed` · `check_pending`)으로 ineligible | 앞의 셋은 제출 때 409 와 사람용 문장으로 거절하고 이유 코드는 기존 꼴이다. 점검 판정 행은 409 가 아니라 큐에 들고 `_why_none` 의 이유를 보인다. 원격 워커를 지정한 제출의 문장은 「이 프리셋은 로컬 레인에서만 돈다」다. 잡이 큐에 들지 않는다 | 단위 | PR1 |
| TO17 | 원격 워커를 지정한 잡, 기본 풀이 아닌 풀의 잡이 대기 중이고 예약 잡이 시각을 기다릴 때 reload 로 그 프리셋에 `local_only` 를 새로 달거나 서버를 재시작 | 같은 후보 함수로 다시 계산되어 대기 잡은 reason `no_capable_machine` 과 ETA null 로 보인다. 예약 잡은 큐 계산 밖이라 시각이 되어 queued 가 된 뒤 같은 규칙을 탄다. 실패로 닫히지 않는다 | 단위 | PR1 |
| TO18 | `local_only` 가 아닌 프리셋 P 의 `checks` 에 `local_only` 점검 프리셋 C 가 있고 원격 머신이 P 를 받을 자리에 있음 | 그 원격 머신에서 P 는 「오지 않음(점검이 로컬 전용)」이고 check_pending 이 아니다. doctor 는 「점검 필요」를 찍지 않는다 | 단위 | PR1 · PR2 |
| TO19 | `rcm recheck <원격 머신> <C>`, C 는 `local_only` 점검 프리셋 | 409 | 단위 | PR1 |

### TN 요청자에게 알리기

<!-- scenario-prefix: TN -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| TN1 | 원격 워커가 끝낸 잡의 `rcm wait` | 사람용 마지막 줄에 실행 머신, 실패면 실패 스텝 | 단위 | PR5 |
| TN2 | 로컬 레인이 끝낸 잡 | 사람용 줄과 새 키 `ran_on` 에 머신 이름이 있다. 잡 JSON 의 `worker` 칸은 지금처럼 null 이다 | 단위 | PR5 |
| TN3 | 산출물 실패, `--fetch-artifacts` 없음 | 종료 코드는 그대로이고 경고 줄이 나온다 | 단위 | PR5 |
| TN4 | 이미 있는 산출물 상태 | `artifacts.state` 의 값이 사람용 줄에 보인다. wait JSON 의 산출물 상태는 지금 그대로다 | 단위 | PR5 |
| TN5 | `[[notify]]` 훅 | `RCM_MACHINE` 을 받는다. `RCM_REQUESTER` 는 지금처럼 받는다 | 단위 | PR5 |
| TN6 | `rcm wait --notify` 인데 알림 도구가 없음 | 경고 한 줄을 찍고 정상 종료 | 단위 | PR5 |
| TN7 | `--notify` 를 주지 않은 `rcm wait` | OS 알림을 부르지 않는다 | 단위 | PR5 |
| TN8 | macOS 에서 `rcm wait --notify` (live) | OS 알림이 뜬다 | 실기 | PR7 |
| TN9 | `ran_on` 계산 | 원격이면 `worker`, 로컬 레인이면 로컬 머신의 `display_name` 이다. 이름을 모르면 null 이고 `host_key` 로 이름을 만들지 않는다 | 단위 | PR5 |
| TN10 | 산출물 상태가 `failed` · `dropped` · `unavailable` · `unknown` 인 잡, wait 가 끝날 때 아직 `collecting` · `uploading` 인 잡 | 앞의 넷은 경고 줄이 나오고 뒤의 둘은 「수집 중」 안내가 나온다. 종료 코드는 그대로다 | 단위 | PR5 |
| TN11 | 시작하지 못한 잡(`started_at` 없음)의 `rcm wait` 와 `[[notify]]` 훅 | `ran_on` 과 `RCM_MACHINE` 이 null 이다. 둘 다 `started_at` 이 있는 잡에서만 계산된다 | 단위 | PR5 |

### 변이 검사

`scripts/mutcheck.py` 에 새 변이 셋을 더한다. 셋 다 시험을 빨갛게 만들어야 한다. 3판의 나머지 여섯은 결과 고리와 짝짓기의
것이라 M5t-b 와 M5t-c 로 갔다.

- doctor 가 `unknown` 을 0 으로 끝낸다
- `lanes = 0` 에서 `@local` 을 살아 있다고 본다
- 후보 머신 함수에서 `local_only` 의 원격 제외를 지운다(PR1, TO15)

## 8. 개인정보 점검

레포는 public 이다. 이 점검은 이 명세와 이후 모든 PR 에 건다. dev 에서 main 으로 가는 릴리스 PR 에서는 M5s §8 의 범위 전체로 한다.

| 단계 | 하는 일 | 통과 조건 |
|---|---|---|
| 1 | 개인 패턴을 검사한다. 패턴 목록은 레포 밖 파일 `~/.config/rcm-privacy/patterns.txt` 에 둔다. 이 명세와 PR 에는 파일 이름만 적고 내용은 적지 않는다 | 0건 |
| 2 | 일반 패턴을 검사한다. 이메일, `/Users/<이름>` 경로, 사설 IP, 웹훅 도메인, 고엔트로피 문자열을 본다 | 0건, 또는 하나씩 확인해 무해함을 적는다 |
| 3 | `gitleaks git` 을 브랜치 범위에 돌린다 | 0건 |
| 4 | 커밋 작성자와 커미터 이메일이 noreply 주소인지 본다 | 전부 noreply |
| 5 | 바뀐 스크린샷을 눈으로 본다 | 0건 |
| 6 | 격리 에이전트가 같은 범위를 따로 훑는다 | 0건 |

툴체인 카탈로그에는 팀 이름, 계정, 번들 ID 를 넣지 않는다. 계획서 본문에도 운영 머신의 호스트 이름과 사용자
경로를 쓰지 않는다. 「운영 빌드 머신」, 「같은 Mac 의 둘째 워커」, 「이 레포를 쓰는 앱 프로젝트」처럼 쓴다. `[[tools]]`
예시의 설치 문구와 `docs` 주소도 공개 문서만 가리킨다.

## 9. 위험

| 위험 | 막는 법 |
|---|---|
| 원격 워커가 되돌릴 수 없는 프리셋(배포 · 스토어)의 잡을 집는다. 기본 풀에 붙인 원격 워커라면 지금도 그렇다(D10) | `local_only` 표식을 단 프리셋은 후보 함수가 원격 머신을 빼고 원격 claim 도 그 결과를 쓴다(254, TO15). 표식이 없는 프리셋은 지금처럼 원격에서 돌 수 있으므로 앱 프로젝트의 release-* · deploy-* 프리셋에 표식을 단다(§6 의 10) |
| 원격 워커를 지정한 `local_only` 제출이 영원히 대기한다 | 후보가 비므로 제출 때 409 로 거절한다(254, TO16) |
| 새로 생긴 경로가 후보 함수를 거치지 않아 `local_only` 잡이 원격 머신에 간다 | 한 시험이 여러 진입점에서 후보를 확인하고, 후보 함수의 원격 제외를 지운 변이에서 그 시험이 빨개진다(254, TO15) |
| setup 이 `~/Library/LaunchAgents/` 에 쓴 서비스 파일이 다음 로그인 때 저절로 올라온다 | 설정 폴더 아래에 쓰고 복사와 bootstrap 명령만 출력한다(241, TR24) |
| `--yes` 로 고른 역할 2·4 가 설치하자마자 LAN 에 열린다 | `--bind` 를 필수로 둔다(241, TR23) |
| mDNS 로 찾은 위장 서버가 doctor 나 `worker --check` 에 프로브 argv 로 임의 명령을 돌리게 한다 | 진단은 찾은 서버에 인증된 요청과 프로브를 보내기 전에 사람의 확인을 받는다(246, TD10 · TD28) |
| `rcm token add` 의 stdout 이 서비스 로그에 남는다 | setup 은 토큰을 만들지 않고 되풀이해 찍지 않는다(TR8 · TR21) |
| 토큰을 인자로 넘겨 `ps` 에 보인다 | setup 은 토큰을 받지 않고 얻는 길만 안내한다. 워커 토큰은 지금도 플래그로 줄 수 없다(`config.py:1794-1795`) |
| 마법사가 운영 DB 를 열어 마이그레이션하거나 운영 설정을 덮는다. setup 이 쓴 서비스 파일은 setup 을 실행한 빌드를 가리키므로 워크트리 빌드로 돌린 setup 이 운영 `data_dir` 을 고르면 그 빌드가 운영 DB 를 연다 | setup 은 Store 를 열지 않는다(TR9). 고른 `data_dir` 에 DB 가 이미 있으면 setup 이 거절한다(TR31). setup · doctor 는 DB 를 열지 않으므로 guard 의 DB 명령 목록에 넣지 않는다. 대신 setup 이 운영 설정 경로에 쓰거나 운영 라벨의 서비스 파일을 쓰거나 운영 `data_dir` 을 고르면 guard 가 거절한다(PR4, TR20 · TR33 · TR34) |
| 실기를 운영 머신에서 한다 | 시험 서버는 `advertise = false` 로 띄우고 시험 워커와 클라이언트는 URL 을 명시한다. 셸의 `RCM_*` 환경 변수를 모두 지우고 `HOME` 과 XDG 폴더 넷을 따로 둔다. 운영 라벨과 같은 `--label` 은 실기 하네스가 거절하고 실기 뒤에는 시험 라벨의 launchd 에이전트만 내리고 지운다. setup 은 시험 포트 · 데이터 폴더 · 라벨을 명시하고 `--no-advertise` 로 돌린다. 하네스는 bootstrap 전에 server.toml 의 값이 시험 값인지 확인한다(§7, TR16) |
| macOS 로컬 네트워크 권한 때문에 「다 됐다」 뒤에도 서버를 못 찾는다 | doctor 는 이 권한을 판정할 수 없으므로 rcm 자신의 요구에서 안내만 한다(246, TD50) |
| 비TTY 에서 질문을 띄워 멈춘다 | 첫 실행 질문은 TTY 에서만(240, TR1) |
| 사람이 설치 안내 문구(`curl \| bash` 포함)를 붙여 넣는다 | rcm 은 실행하지 않는다. 문구를 믿을지는 코디네이터 관리자 몫이다 |
| 서버가 보낸 설치 문구에 제어문자가 섞여 있다 | 워커가 찍기 전에 제어문자를 정리한다(TD32) |
| 헤드리스 서비스에서 CLT 설치 대화상자가 되풀이된다 | `libxcselect` 로 판별하는 macOS shim 규칙(246, TD1 · TD33). Java 스텁은 이 방법으로 판별되지 않는다(§4.5) |
| 셸에서는 ok 인데 서비스에서는 도구가 없다 | `env -i` 와 서비스 관리자의 기본 변수에 서비스 파일의 환경 변수만 더해 잰다. 셸 결과와 다르면 표시한다(246, TD2 · TD29 · TD37) |
| `/worker/spec` 이 프리셋 env 를 워커에 준다 | `local_only` 프리셋은 후보 함수가 원격 워커를 빼므로 보내지 않는다(254, TO15). 진단은 env 값을 찍지 않고 결과를 올리지 않는다(TD8 · TD12) |
| `git ls-remote` 가 자격 증명으로 실제 접속한다 | `--offline`(TD17) |

아래는 이 명세로 막지 못하고 남는 위험이다.

| 남는 위험 | 이 명세가 하는 것 |
|---|---|
| `read_auth = none` 에서 큐 · 보관함 · 머신 목록을 같은 망 누구나 읽기 | 막지 않는다. LAN 이나 `0.0.0.0` 을 고르면 setup 이 경고한다. 새 호스트 사실은 토큰 있는 응답에만 싣는다(246). 토큰으로 읽기를 막는 모드는 ⛔ Q14 다 |
| 망이 60초 넘게 끊기면 원격 잡이 `lost` 가 되고 돌아온 워커가 프로세스를 죽인다(E10) | 바꾸지 않는다. M5t-b 가 결정 253 으로 구현한다 |
| `server = "auto"` 인 `rcm worker` 가 mDNS 로 찾은 위장 서버에 붙는다. 토큰이 새는 데서 그치지 않는다. claim 으로 받은 프리셋 argv 와 프로브로 그 머신에서 임의 명령이 돈다 | `rcm worker` 의 찾기는 바꾸지 않는다. setup 의 워커 경로는 `auto` 를 쓰지 않고 명시 URL 을 쓴다(TR26). doctor 는 worker.toml 의 `server = "auto"` 를 warn 하고(TD48) 찾은 서버에 요청과 프로브를 보내기 전에 확인을 받는다. 서버 신원 확인은 M5t-c 의 위협 모델에서 다룬다 |
| 같은 망에서 평문으로 오가는 토큰 엿보기(TLS 없음, 결정 5) | 바꾸지 않는다. M5t-c 의 위협 모델에서 다룬다 |
| `local_only` 를 단 프리셋이 있는 채로 운영을 v0.11.0 미만으로 되돌리면 그 키를 모르는 서버가 기동하지 못한다(§6 의 10 아래 문단) | 롤백 함정이다. 운영을 v0.11.0 아래로 되돌리기 전에 앱 프로젝트 프리셋의 `local_only` 줄을 먼저 빼고, 그 프리셋을 읽는 모든 서버가 옛 판으로 기동하는지 확인한다(§6 의 17) |
| `lanes = 0` 서버에서 원격 claim 의 롱폴이 0.5초마다 DB 를 묻는 폴링이 된다 | 위험으로 적어 둔다. M5t-a 에서는 고치지 않는다. 롱폴이 DB 를 묻는 간격을 어떻게 할지는 결과 고리를 다시 짜는 M5t-b 가 정한다 |

## 10. Codex 초안과 달라진 점

| Codex 초안(2026-11-24) | 이 명세 | 결정 |
|---|---|---|
| claim · heartbeat · 스텝 마커 · SSE · 산출물 업로드 · finish 검증을 새로 만들 것처럼 적었다 | 이미 있다(§3). M5t-a 는 요청자 알림의 빈 곳만 고친다 | 248 e·f |
| 종료 코드를 0~4 로 적었다 | 5(전달 실패)도 있다. 0~5 계약을 바꾸지 않는다 | 248 e |
| setup 이 사용자가 확인하면 launchd · systemd 서비스를 시작한다 | 등록도 시작도 하지 않고 명령만 출력한다 | 241, ⛔ Q9 |
| bind 를 127.0.0.1 · LAN 주소 · Tailscale 주소 · 직접 입력한 주소 가운데 고르게 한다 | 역할 2·4 에서는 Tailscale 주소를 추천하고 없으면 LAN 주소(DHCP 예약 권고)나 `0.0.0.0` 을 고르게 한다. LAN 주소나 `0.0.0.0` 이면 읽기가 같은 망에 열려 있다고 경고한다. 직접 입력은 이 머신의 인터페이스에 실제로 있는 주소일 때만 받는다 | 241, ⛔ Q10 · Q14 |
| 첫 구현은 토큰을 사람이 옮기고 짝짓기 코드는 별도 보안 명세로 미룬다 | M5t-a 는 Codex 초안과 같다. 토큰을 사람이 옮기고 setup 이 그 길을 안내한다. 짝짓기와 서버 신원은 위협 모델부터 쓰는 M5t-c 다 | 252 |
| 워커 연결이 끊기면 실행 중 잡은 `lost` | M5t-a 는 바꾸지 않는다. M5t-b 는 되돌릴 수 없는 프리셋만 유예 뒤 워커가 스스로 멈추게 하고 나머지는 계속 돌게 한다 | 253 |
| 산출물 전송이 실패하면 단순 성공으로 보고하지 않는다 | 잡 결과는 그대로 두고 이미 있는 `artifacts.state` 를 사람용 줄과 경고 줄로 알린다 | 248 e |
| 서버가 최종 상태를 확정한 뒤 OS 알림이나 webhook 을 보낼 수 있다 | 요청 머신 알림은 클라이언트 쪽 `rcm wait --notify` 다. 서버 webhook 은 만들지 않는다 | 248 f, ⛔ Q3 |
| `coordinator` 의 `lanes = 0` 은 로컬 워커 스레드를 안 띄우고 원격이 없으면 대기 | 같다. `@local` · `eligible_now` · 자동 점검 · 헬스 `capacity` · TXT · 결정 39(M5f) · 화면까지 따라오는 일을 모두 적었다 | 243, ⛔ Q5 |
| 역할 4 에서는 `rcm worker` 를 띄우지 않는다 | 기본은 같다. 이미 둘째 워커를 쓰는 운영 구성은 허용하고 경고만 한다 | 242 |
| 환경 진단이 없다. 워커 setup 이 도구와 probe 를 검사한다고만 적었다 | `rcm doctor`, `[[tools]]`, rcm 자신의 요구, 서버 설정 템플릿의 툴체인 카탈로그 | 246·247 |
| 원격 금지 표식이 없다 | 프리셋 `local_only` 와 그 프리셋의 후보를 `@local` 하나로 두는 후보 함수 | 254 |
| PR 다섯 | 명세 PR 과 PR 일곱을 물결로 나눴다 | §5 |

## 11. 결정 표

「오너 확인」 열의 ⛔ 는 오너가 정해야 진행되는 항목이고 옆의 Q 번호가 아래 질문 표의 번호다.

| # | 항목 | 정한 것 | 오너 확인 | Codex 초안과 다른 점 |
|---|---|---|---|---|
| 239 | 역할 | `client` · `coordinator` · `worker` · `coordinator-worker`. 새 파일에 적지 않고 있는 설정 파일에서 읽어 낸다. 설정 탐색 순서는 `$RCM_CONFIG`, `./rcm.toml`, 사용자 설정 폴더, 환경변수만 쓰는 클라이언트와 워커이고 환경변수만 있어도 「설정 있음」이다. `$RCM_CONFIG` 가 없는 파일이면 종료 2. `$RCM_CONFIG` 와 `./rcm.toml` 은 서버 설정으로 읽는다. 첫 실행 판정은 기존 탐색 함수를 그대로 부른다. server · client 는 XDG 를 먼저 보고 둘 다 보며 worker 는 `user_config_dir()` 하나다 | 추천 | — |
| 240 | 첫 실행 | 인자 없는 `rcm` 은 stdin · stderr 가 TTY 이고 설정이 하나도 없을 때만 묻는다. 그 밖에는 종료 2. `rcm setup`, `rcm setup --role <r> --yes` | 추천 | stdin 과 stderr 가 모두 TTY 일 때로 명시 |
| 241 | setup | 기존 `init` 조립, 덮지 않음(`--force`), `--port` · `--data-dir`(절대 경로) · `--advertise`/`--no-advertise` · `--label` 을 받고 대화형에서도 묻기, 고른 `data_dir` 에 `rcm.sqlite3` 가 있으면 거절(종료 1, 그대로 쓰려면 `--reuse-data-dir`), 쓰는 중인 포트는 경고, 역할별 `--yes` 필수 인자(client `--server`, worker `--server` · `--pool` · `--lanes`, coordinator 계열 `--bind`), 서버 서비스 파일 추가(필수 키는 fd 상한 4096 · `HOME` · `WorkingDirectory` · 로그 경로 · PATH 규칙, `<config dir>/service/<label>` 에 쓰고 복사와 bootstrap 명령을 출력, `--service-file` 이 먼저, 표준 라벨이 기본, `--label`, 있는 파일은 덮지 않음, 같은 라벨이 로드돼 있으면 경고만이고 로드 여부는 읽기 전용 조회로만, 복사 명령은 `cp -n` 꼴, 로그 파일 이름은 라벨에서, 서비스 파일은 setup 을 실행한 빌드를 가리킴), 등록·시작은 안 하고 명령만, 역할 2·4 의 bind 는 Tailscale 추천(고르면 `advertise = false` 와 tailnet 읽기 공개 경고) · 없으면 LAN 주소(DHCP 예약 권고)나 `0.0.0.0` · 그때 읽기 공개 경고 · 직접 입력은 이 머신의 인터페이스 주소만 · `--yes` 면 `--bind` 필수(없으면 종료 2, 결정 33 의 뜻 유지), 역할별 토큰 안내(coordinator 는 client 권한 토큰 명령과 `--worker` 토큰 명령을 출력, client 는 `RCM_TOKEN`, worker 는 `rcm token add --worker` 와 명시 URL 이고 `auto` 는 쓰지 않음, 서비스로 띄운 워커는 파일의 `token` 만 씀, `init client` 의 `auto` 거절 유지), 마지막에는 서버 없이 도는 진단만 조언으로 보이고 `rcm doctor --role <r>` 를 안내, 종료 0(썼음) · 1(못 씀) · 2(사용법), 어떤 Store 도 열지 않음, 운영 설정 경로 · 운영 라벨 · 운영 `data_dir`(플래그로 준 값이나 그 명령의 `HOME` 으로 푼 템플릿 기본값)은 guard 가 거절(guard 는 setup 과 같은 규칙으로 경로를 계산), `NO_FOLLOW_COMMANDS` 에 setup(PR4) · doctor(PR2) | ⛔ Q9 · Q10 · Q14 | 서비스 시작 안 함 |
| 242 | `coordinator-worker` | 지금의 `rcm serve`(`lanes ≥ 1`). 같은 머신의 `rcm worker` 는 기본이 아니며 있으면 경고만 | 추천(운영 빌드 머신을 역할 4 로 쓰는 것은 오너가 2026-11-24 에 정했다) | 둘째 워커 허용 |
| 243 | 분배 전용 `lanes = 0` | 허용. `@local` 살아 있지 않음 · `--machine @local` 은 409 와 「이 서버에는 로컬 레인이 없다」 · 로컬 프로브 생략 · `eligible_now` 제외 · 자동 점검 제외 · 헬스 `capacity` 는 「갈 수 있는 머신이 있는 프리셋 수 / 전체 프리셋 수」(HTTP 상태 그대로) · `rcm check` FAIL 과 0 인 프리셋 목록 · TXT `lanes=0` · 결정 39(M5f) 개정(PR3) · 화면 손질 · 호스트 카드 표시 · 기동과 reload 때 갈 곳 잃은 `local_only` · `@local` 지정 잡에 `no_capable_machine` 과 ETA null(실패로 닫지 않음) · `lanes = 0` 이면 `rcm check` 의 local · service preset tools 행은 「이 머신은 잡을 돌리지 않는다」 · 갈 수 있는 머신은 후보 함수(254) 결과 가운데 살아 있는 머신(`checks` 없는 pool 프리셋은 판정과 상관없이 그 풀의 살아 있는 레인) · 프리셋 0개면 `capacity` 행 없음 | ⛔ Q5 | 따라오는 일 목록 |
| 246 | 환경 진단 | `rcm doctor`(역할 넷, `--server` · `--config`), 깨끗한 환경(`env -i` + launchd · systemd 별 기본 변수 + 서비스 파일의 환경 변수)으로 측정, 서비스 파일 찾는 순서(`--service-file`, `<config dir>/service/*`, 표준 라벨의 설치 자리, 없음 표시. 둘 이상이면 고르게 하고 비TTY 면 종료 2), `--role` 생략 규칙(읽어 낸 역할이 하나면 그것, 여럿이면 종료 2), coordinator 계열의 서버 쪽 값은 로컬 server.toml 과 client 토큰으로 `/api/health` · `/api/machines` 를 읽어 얻음(DB 안 엶, 토큰 없으면 `—` 와 종료 3), 서비스 PATH 와 셸 PATH 의 홈은 모두 `~` 로, 서비스 파일 env 는 이름만, `/worker/spec` 키 추가(`pools` 는 프리셋 키 그대로), 상태 넷과 넷, `[[tools]]`(이름은 `requires` 나 프로브 argv[0] 과 맞춤), 호스트 사실(토큰 있는 응답에만, `read_auth = basic` 에서도 같음, 주기 보고에도 실음), rcm 자신의 요구(sudo 빼기는 이 안내에만, Local Network 는 안내만), `libxcselect` 로 판별하는 shim 규칙(판별 함수 하나를 워커 프로브와 `measure_tools` · `missing_tools` · `preset_env` · `rcm check` 도구 행이 함께 씀, `xcode-select` 자신은 판별에서 뺌, Java 스텁은 판별 안 됨), `LANG=C` 는 doctor 의 호스트 사실 명령에만, 종료 0·1·2·3 과 우선순위 1 > 3 > 0, 종료 1 은 이 머신이 든 풀의 `pool` 프리셋에 빠진 도구 · rcm 자신의 요구 FAIL · 역할별 규칙에만, 도구가 빠진 `any-capable` 프리셋은 warn, 역할별 종료 규칙(worker 계열은 대상 0개면 1 이고 도구 빠진 `any-capable` warn 과 함께여도 1, coordinator 는 `capacity`, client 는 도달 · 버전 · 토큰만), 워크스페이스 안 도구는 `pool` 프리셋이면 warn 이고 `any-capable` 이면 「오지 않음」, coordinator 진단은 못 가는 프리셋과 `local_only` 프리셋(「로컬 레인 필요」)을 알림, `local_only` 점검이 걸린 프리셋은 원격 머신에서 「점검 필요」가 아니라 「오지 않음(점검이 로컬 전용)」, 조언일 뿐, mDNS 로 찾은 서버는 인증된 요청과 프로브 전에 확인, worker.toml 의 `server = "auto"` 는 warn, `worker --check` 두 줄(버전 행은 따라갈 수 있으면 warn 이고 없으면 FAIL, 비TTY `auto` 에서 새 종료 3), 서버 설정 템플릿 주석 블록의 카탈로그, 코어의 도구 이름은 호스트 사실용 허용 목록만. 3판 결정 245 의 진단 부분을 여기에 합쳤다 | ⛔ Q7 · Q8 | 신설 |
| 247 | `checks` 없는 프리셋 | 지금처럼 도구가 있으면 eligible | ⛔ Q6 | — |
| 248 e · f | 요청자 알림 | e 이미 있는 `artifacts.state` 를 사람용 줄과 경고에(경고는 failed · dropped · unavailable · unknown, 끝날 때 collecting · uploading 이면 「수집 중」) · f 사람용 줄과 `ran_on` 키(저장 칸 없이 서버가 원격은 `worker`, 로컬은 `display_name` 으로 계산하고 모르면 null, `started_at` 이 없으면 null), `RCM_MACHINE`(`started_at` 이 있을 때만), `rcm wait --notify`(`rcm run` 에는 없음), 결정 6 문장 정정(PR5) | ⛔ Q3 | 클라이언트 쪽 알림 |
| 251 | 개발 방식 | 개발 루프 스킬로 돈다. 시나리오 표마다 `scenario-prefix` 표지(접두어 TR · TD · TL · TN · TO), 시험 이름에 ID, `tools/scenario_trace.py` 대조, PR 마다 라운드당 격리 검증 2명, `verify-round:` 댓글. 스킬 PR 이 먼저 머지된다는 전제이고 아니면 손으로 | 추천 | — |
| 252 | 범위 나누기 | M5t 를 셋으로 나눈다. 이번 명세 PR 은 M5t-a(역할 · 첫 실행 setup · `lanes = 0` 분배 전용 · `rcm doctor` OS 진단 · 요청자 알림)만 담고 4라운드 검증을 받는다. M5t-b(결과 고리 프로토콜: 세션 · 로그 오프셋 · 끊김 유예)와 M5t-c(짝짓기 · 서버 신원 보안)는 상태 전이 표와 위협 모델부터 쓰는 별도 명세로 한다 | 오너가 정함(2026-11-25, 「셋으로 나눈다」) | — |
| 253 | 끊김 정책 | 배포 · 스토어처럼 두 번 돌면 안 되는 프리셋에만 표식을 두고, 그 잡은 유예(약 10분)가 지나면 워커가 스스로 멈춘다. 결과는 「모름」(종료 3)이다. 게이트 · 시험 같은 나머지는 계속 돈다. M5t-b 가 구현한다 | 오너가 정함(2026-11-25, 「되돌릴 수 없는 것만 멈춤」) | — |
| 254 | 원격 금지 표식 | 프리셋 설정에 `local_only = true` 를 두고 기본은 지금처럼 원격 허용. 이 레포를 쓰는 앱 프로젝트의 release-* · deploy-* 프리셋에 표식을 단다. DB 변경 없음. 정의는 한 문장이다. 「`local_only = true` 인 프리셋에게 이 서버의 머신 목록은 `@local` 하나뿐이다.」 서버는 프리셋별 후보 머신 함수 하나(예: `machines_for(preset)`)를 두고, 배치(`placement_for` 의 지정 · capable · pool 세 갈래) · 제출 판정(`submission_placement`, `eligible_now`) · `_could_receive` · `capacity` · 점검 정체 판정(`_stalled_checks` · `_why_none`) · 자동 점검과 recheck 의 대상 · `/worker/spec` 의 프리셋 목록 · 원격 claim 의 WHERE 가 모두 그 결과를 쓴다. 한 시험이 여러 진입점에서 이를 확인하고(TO15) 원격 제외를 지운 변이에서 빨개진다. 후보 함수는 정적인 조건(`local_only` · 풀 · `lanes` · `local_accept_any`)만 본다. 후보 함수가 머신을 뺐기 때문에 비면 세 갈래 모두 새 분기로 `no_capable_machine` 과 ETA null 을 내고, 판정 때문에 비는 경우는 지금의 `_why_none` 을 둔다(§4.3). 제출 409 조건은 §4.3 의 것이다. 제출 때 후보가 비면 409 와 사람용 문장(이유 코드는 기존 꼴)이고 원격 워커 지정도 409. 기동 · reload 때 대기 잡도 같은 함수로 다시 계산. 설정 검증은 하나만 남긴다. `local_only` 인 `pool` 프리셋의 `pool` 은 기본 풀이어야 하고 `pools` 의 기본 풀이 아닌 항목은 제출 409 로 막는다. `local_only` 점검이 걸린 프리셋은 원격 머신에서 「오지 않음(점검이 로컬 전용)」이고 원격 recheck 는 409. 제출의 409 검사는 supersede · 합류보다 먼저. coordinator 진단은 「로컬 레인 필요」로 표시. M5t-a 의 PR1 이 구현하고 `lanes = 0` 쪽은 PR3 이 구현한다. 이 표식을 단 프리셋을 읽는 서버는 모두 v0.11.0 이상이어야 하므로 앱 프로젝트에 다는 일은 운영 반영 뒤이고, 달기 전에 그 서버 목록을 확인한다 | 오너가 정함(2026-11-25, 「프리셋에 명시 표식」). `lanes = 0` 제출의 409 는 결정 파일의 추천을 넷째 판이 정했다. 원격 워커 지정 제출의 409 와 나머지 세부는 4라운드 결정 파일이 정했다. 후보 함수 하나로 다시 세운 것은 5라운드 뒤 오너 답(「한 곳 필터로 재설계 후 diff 검증」)에 따른 6판의 결정이다 | 신설 |
| 255 | 동시 세션 | 워커 이름당 살아 있는 세션은 하나다. 새 세션이 등록하면 옛 세션은 409 를 받고 잡을 멈춘 뒤 끝난다. M5t-b 가 구현한다 | 오너가 정함(2026-11-25, 「지원 안 함」) | — |
| 244 · 245 · 248 a~d · g · 249 · 250 | 옮긴 결정 | 244(짝짓기 창과 서버 신원)와 245 의 풀 묶기(`tokens.pool`)는 M5t-c 로 간다. 248 a~d · g(결과 고리)와 250(프로토콜 호환)은 M5t-b 로 간다. 249(DB v26 · v27)는 세션 · 로그 칸은 M5t-b, 신원 비밀과 짝짓기 창과 `tokens.pool` 은 M5t-c 로 나뉜다. 245 의 「비밀이 필요한 프리셋은 원격에 주지 않는다」는 254 가 대신하고 245 의 진단 부분은 246 에 합쳤다 | — | — |

오너 질문이다. 추천값을 첫째로 적었다. 3판의 Q1 · Q12 · Q13 은 M5t-c 로 옮겼다. Q4 · Q11 은 결정 253 으로, Q2 는 결정
254 로 답을 받았다. 남은 질문은 아래 여덟이다.

| Q | 질문 | 추천 | 결정 |
|---|---|---|---|
| Q3 | 요청 머신 OS 알림 `rcm wait --notify`(클라이언트 쪽)를 넣나 | 넣는다, 기본 꺼짐 | 248 f |
| Q5 | `lanes = 0` 에 살아 있는 레인이 없을 때 헬스는 정보(`capacity` 0)와 `rcm check` FAIL 로 알리고 HTTP 상태는 그대로 두나 | 예 | 243 |
| Q6 | `checks` 없는 프리셋은 도구만 있으면 eligible 로 두나(지금대로) | 유지 | 247 |
| Q7 | rcm 자신의 의존성에 한해 OS 별 일반 설치 안내를 코어에 넣나. pyproject 하한을 3.11.4 로 올리나 | 예, 3.11.4 | 246 |
| Q8 | doctor 의 판정 못 함은 3, `rcm check` 는 그대로 두나 | 예 | 246 |
| Q9 | setup 이 서비스를 직접 등록·시작하지 않고 명령만 출력하나(M5n §9 유지) | 예 | 241 |
| Q10 | 역할 2·4 의 bind 를 어떻게 권하나. 결정 33 은 템플릿 기본값만 정했고 PLAN 「보안」과 `README.md:83` 은 `0.0.0.0` 을 선택지로 안내한다. 127.0.0.1 이면 mDNS 광고가 꺼지고 워커가 닿지 못하므로 역할 2·4 의 기본 선택지가 될 수 없다. 대가도 있다. Tailscale 주소에 bind 하면 mDNS 응답기가 LAN 워커에게 LAN 주소를 알려 주어 연결이 거절된다. 그래서 Tailscale 을 고르면 워커는 서버 URL 을 명시해 붙는다 | Tailscale 주소를 추천하고 없으면 LAN 인터페이스 주소(DHCP 예약 권고)나 `0.0.0.0`. LAN 이나 `0.0.0.0` 이면 읽기가 같은 망에 열려 있다고 경고한다 | 241 |
| Q14 | 토큰으로 읽기를 막는 `read_auth` 모드를 새로 만드나 | 이 마일스톤 밖에서 다음에 만든다 | 241 |

## 12. 후속 명세 M5t-b · M5t-c 에 넘기는 것

오너는 2026-11-25 M5t 를 셋으로 나누라고 답했다(결정 252). M5t-b 와 M5t-c 는 이 문서와 따로 쓴다. 3라운드 뒤의 근본 원인
분석은 결과 고리 프로토콜을 상태 전이 표 없이 규칙 목록으로 설계했고 짝짓기 · 서버 신원 보안을 위협 모델 없이 덧대기로
고쳤다고 지적했다(§13). 그래서 M5t-b 는 상태 전이 표부터, M5t-c 는 위협 모델부터 쓴다. 셋째 판 전문은 레포 밖에 보관했고
두 명세는 그 판의 설계를 재료로 쓸 수 있다.

### M5t-b 결과 고리 프로토콜

| 항목 | 내용 |
|---|---|
| 다루는 것 | 워커 세션, 로그 오프셋, 끊김 유예. 3판의 결정 248 a~d · g, 249 의 v26, 250 이 여기로 간다 |
| 먼저 쓸 것 | 세션 상태 × 잡 상태 × {서버 정상, 서버 정지, 분단} 의 상태 전이 표, 타이머마다 어느 시계로 재는지 |
| 오너가 정한 것 | 결정 253(되돌릴 수 없는 프리셋만 유예 뒤 워커가 스스로 멈추고 결과는 종료 3, 나머지는 계속 돈다), 결정 255(워커 이름당 살아 있는 세션 하나, 옛 세션은 409 를 받고 잡을 멈춘 뒤 끝난다). 3판의 Q4 · Q11 은 253 으로 답을 받았다 |
| 3판의 재료 | 워커 세션 `worker_session_id` 와 `worker_sessions` 표, claim 표식 `lease_id`, 재시작 순서와 버린 lease, 409 사유, 마스킹 전 바이트 오프셋과 스풀, `log.pending`, 재전송 상한, 끊김 유예, 워커의 자기 멈춤, finish 큐, `worker_protocol` 2, DB v26. `lease_id` 라는 이름은 M5s 의 반복 사슬이 이미 `attempt` 와 「시도」를 써서 골랐다 |

M5t-b 가 반드시 다룰 지적이다. 3라운드 id 는 결정 파일이 적은 대로 옮겼다. 결정 파일이 반박 쪽(A · B)을 적지 않은 id 는
접두어 없이 두었다.

| 라운드 | id | 요지 |
|---|---|---|
| 3 | A-H2 | `grace_seconds` 이름이 충돌한다. `disconnect_grace_seconds` 로 바꾼다 |
| 3 | A-H5 | 세션 펜싱 |
| 3 | A-H6 | 재전송 상한은 시간만 두고 잡이 끝난 뒤에만 건다. 2라운드 결정 파일의 「연속 실패 20번」이 근본 원인 분석의 예로 꼽혔다 |
| 3 | M1 | 산출물 재업로드 순서 |
| 3 | M3 | finish 의 `lost` 허용 목록 |
| 3 | M7 | 버전 검사를 먼저 한다 |
| 3 | M8 | 등록 전 프로토콜 확인, 서비스가 되살리지 않는 종료 코드 |
| 3 | M13 | 버린 lease 의 프로세스 그룹 정리 |
| 3 | M14 | 잠자기를 넘는 시계 |
| 2 | A-H1 · A-H2 · A-H3 · A-M8 · B-M6 · B-M7 · A-L3 | 데이터 모델 없이 늘어놓은 세션 · 유예 · 재시작 규칙 |
| 2 | A-H4 | 유예 중 취소 |
| 2 | A-M1 | 유예 중 레인의 상태 |
| 2 | A-H5 · B-H7 · A-H7 · A-M5 · B-M8 | 로그 길이의 단위, 줄 끝이 없는 꼬리, 끝나지 않는 재전송, 응답별 처리 |
| 2 | A-M6 | 산출물 재업로드 |
| 2 | A-L5 | 늦은 결과로 남기는 범위 |
| 2 | A-M4 | 옛 워커가 잡을 집는 것 |
| 2 | A-M7 · B-M2 | DB v26 목록(신원 비밀 표는 M5t-c) |
| 2 | A-L2 · A-L6 | 설정 키 이름, 결과 고리 변이 |
| 1 | A-H1 · A-H3 · A-H4 · A-H5 · A-H6 · A-H7 | `attempt` 이름 중복, 같은 토큰의 두 프로세스, 재시작한 워커의 등록이 먼저 `lost` 로 닫음, 409 를 `closed_by_server` 로 읽음, `log_incomplete` 판별 수단, `lost` 뒤 그룹 자리가 풀림 |
| 1 | A-M1~A-M9 · A-M12 · A-L3 | 오프셋의 바이트 공간, 서버 재시작과 유예, 유예 중 취소, 유예 만료와 재연결 경쟁, `lost` 뒤 도착한 finish, 워커 디스크, 버전 문자열 호환, DB 판 나누기와 guard 근거, 유예 중 ETA, `worker_unreachable` 이름 중복, 유예 값 검증 |

1라운드의 A-H3 · A-H4 · A-M3 · A-M1 은 둘째 판이 반영했다고 적었지만 풀리지 않아 2라운드에서 다시 나왔다.

### M5t-c 짝짓기 · 서버 신원 보안

| 항목 | 내용 |
|---|---|
| 다루는 것 | 짝짓기 창, `POST /worker/hello` 와 신원 비밀, `tokens.pool`, `POST /pair/exchange`, PLAN 「보안」 절의 인증 예외 문장. 그 문장은 넷째 판에서 지웠고 M5t-c 가 다시 넣는다. 3판의 결정 244, 245 의 풀 묶기, 249 의 `worker_identity` 와 v27 이 여기로 간다 |
| 먼저 쓸 것 | 위협 모델. 공격자의 위치, 지킬 자산, 남는 위험 |
| 오너가 정할 것 | 3판의 Q1(짝짓기 창으로 M5n §13 의 자동 발급 · 자동 가입 금지를 개정하나), Q12(신원 비밀을 서버 DB 에 원값으로 두나), Q13(TLS 없는 LAN 에서 남는 위험을 받아들이고 Tailscale 을 권하는 데 그치나, 요청마다 서명을 넣나) |
| 3판의 재료 | 3판 §4.3 의 짝짓기 창과 서버 신원, §9 의 남는 위험 표 |

M5t-c 가 반드시 다룰 지적이다.

| 라운드 | id | 요지 |
|---|---|---|
| 3 | A-H3 · B-H2 | 정확한 자기 주소 목록. 결정 파일이 두 id 를 묶어 M5t-c 로 넘겼다 |
| 3 | 근본 원인 R1 의 예 | 보관함 Host 허용 목록을 서버 자기 주소 목록에 다시 쓰는 처방 |
| 3 | B-M3 | DHCP, `auto` 와 옛 토큰 |
| 3 | B-M4 | `tokens.pool` 이 NULL 인 경우 |
| 3 | B-M8 | 엿들은 교환 |
| 3 | L2 | HMAC 인코딩 |
| 2 | A-H8 · B-H1 · B-H2 · B-M1 | hello 의 중계, 기존 토큰, 짝짓기 주소 |
| 2 | B-M11 | 인증 예외 라우트 |
| 2 | B-H8 · B-M3 | 클라이언트 토큰 |
| 2 | B-L5 · B-L7 | 창이 열렸는지 드러나는 응답, 워커의 기본 이름 |
| 2 | B-H3 · B-H4 | 풀과 비밀이 필요한 프리셋. 비밀 프리셋 부분은 결정 254 가 대신한다 |
| 2 | A-M7 · B-M2 | 신원 비밀 표와 hello 를 넣는 PR |
| 1 | A-M14 · B-H1 | 베낄 수 있는 `server_id`, 확인이 곧 토큰 유출, `rcm pair` 가 DB 를 여는지 |
| 1 | B-H2 · B-L3 | 짝짓기 코드의 무차별 대입과 스키마, kind 마다 열린 창 수 |
| 1 | A-L2 · A-L6 · B-M11 | TXT 에 더하는 것, 워커 이름 · 폐기 · 발급 범위 |
| 1 | B-M2 · B-M4 · B-L4 | 물결 의존, 평문 전송과 `/worker/spec` 의 env, README Discovery 문장과 `data_dir` 초기화 뒤 복구 |
| 1 | A-M11 · B-H3 | 창에서 워커의 풀을 정해 기본값에서 분배가 일어나게 하기 |

1라운드의 B-M4 는 둘째 판이 반영했다고 적었지만 거르는 기준이 될 풀을 토큰에 묶지 않아 풀리지 않았다.

## 13. 격리 반박에서 바뀐 것

1라운드와 2라운드 표에는 M5t-a 범위의 행만 남겼다. M5t-b · M5t-c 로 간 행은 id 와 요지만 §12 로 옮겼다. 남긴 행의
「절」 열과 본문에 나오는 PR 번호는 그 판의 것이다.

### 1라운드: 첫째 판에서 둘째 판으로

1라운드에서 반박 두 명이 첫째 판을 읽었다. 반박 A(프로토콜 · 정확성)는 HIGH 7 · MED 14 · LOW 7 을, 반박 B(설치 · 보안 ·
오너 의도)는 HIGH 5 · MED 13 · LOW 6 을 냈다. 메인 세션이 지적마다 정한 결정으로 둘째 판을 고쳤다. 두 반박이 같은 곳을
다룬 것은 한 행에 묶었다. 「다룬 것」 열에는 반박 원문 요약의 주제를 옮겼다. 요약에 주제가 없는 LOW 항목은 결정이
다룬 대상을 적었다.

| 반박 | 다룬 것 | 바뀐 것 | 절 |
|---|---|---|---|
| A-H2 | 이미 있는 `artifacts.state` | `artifacts_state` 칸을 없애고 이미 있는 `artifacts.state` 를 사람용 줄과 경고에 쓴다. E6 · TN4 를 고쳤다 | §3 E6, §4.7, §4.8 |
| A-M10 | `lanes = 0` 의 기본 풀 | `capacity` 를 프리셋 단위로 정의하고 0 인 프리셋 목록을 보인다 | §3 C12, §4.2, TL3 · TL13 · TL14 |
| A-M11 · B-H3 | 기본값에서 분배가 일어나는지, 대상 0개 | doctor 는 대상 0개를 1 로 낸다. 창에서 풀을 정하는 부분은 M5t-c 로 갔다 | §4.4, TD26 |
| A-M13 | 이미 있는 `RCM_REQUESTER` | 더하는 것은 `RCM_MACHINE` 하나다. TN5 를 고쳤다 | §3 E13, §4.7 |
| A-L1 | 틀린 인용 | `cli.py:3605`, `PLAN.md` 의 종료 코드 줄, `server.py:646-649` 로 고쳤다 | §3 |
| A-L4 · B-M7 | `workspace_tool` 판정 | warn 으로 두고 0 을 지킨다. 3 은 판정 못 한 경우에만. 우선순위 1 > 3 > 0 | §4.5, TD27 · TD34 |
| A-L5 | 잡 JSON 의 `worker` 칸 | `worker` 칸은 그대로 두고 사람용 줄과 새 키에만 적는다 | §4.7, TN2 |
| A-L7 | `--machine @local` 의 409 문구 | 409 와 「이 서버에는 로컬 레인이 없다」 | §4.2, TL2 |
| B-H4 | doctor 의 역할 4 | `coordinator-worker` 역할을 더해 서버가 잰 로컬 레인 값을 읽는다 | §3 G7, §4.5, TD30 |
| B-H5 | 운영 머신에서 하는 실기의 격리 | `HOME` · `XDG_CONFIG_HOME` 분리, `--label` 과 운영 라벨 거절, guard 의 DB 명령 목록에 setup · pair · doctor | §4.1, §7, TR18 |
| B-M1 | 확인 시점 | 인증된 요청을 보내기 전에 확인한다. `rcm worker --check` 에도 걸고 비TTY 에서 확인을 못 받으면 종료 3 | §4.5, TD10 · TD28 |
| B-M3 | 물결 안의 파일 겹침 | 겹치는 파일과 머지 순서 열을 더했다 | §5 |
| B-M5 | 익명 응답의 호스트 사실 | 토큰 있는 응답에만 싣는다 | §3 G5, §4.5, TD31 |
| B-M6 | 측정 환경 | 깨끗한 환경 + 서비스 파일의 환경 변수로 재고 서비스 파일 찾는 규칙을 정했다 | §3 G3, §4.5, TD29 |
| B-M8 | Q10 의 근거 | 결정 33 은 템플릿 기본값만 정한다고 고치고 역할 2·4 의 선택지와 추천을 적었다 | §4.1, §11 Q10, TR10 |
| B-M9 | Q9 의 이유, 토큰 경로, TR9, guard 근거 | Q9 의 이유 셋을 적고 토큰 경로 표를 더했다. TR9 를 「Store 를 열지 않는다」로 고치고 guard 근거를 바로잡았다 | §4.1, §4.8, TR9 |
| B-M10 | 따라가기 제외 | `NO_FOLLOW_COMMANDS` 에 setup · doctor · pair 를 넣었다 | §4.1, TR17 |
| B-M12 | 카탈로그 위치, 워커 쪽 문구 정리 | 카탈로그를 휠에 실리는 템플릿의 주석 블록에 둔다. 워커가 찍기 전에 제어문자를 정리한다 | §4.5, TD23 · TD32 |
| B-M13 | shim 판별 | `/usr/bin/` 과 `libxcselect` 바이트로 판별하고 워커 프로브에도 건다. `/usr/bin/java` 는 PR2 가 실측한다 | §4.5, TD33 |
| B-L1 | 설정 탐색 순서 | 탐색 순서 표를 두고 환경변수만 있는 클라이언트를 「설정 있음」으로 본다 | §4.1, TR19 |
| B-L2 | 직접 입력 주소 | 이 머신의 인터페이스 주소만 받는다고 적었다 | §4.1, TR10 |
| B-L5 | `--offline` 범위, 토큰 없음, 출력 경로 | 원격 git 접속만 끈다. 토큰이 없거나 401 이면 종료 3. 출력 경로는 basename | §4.5, TD17 · TD35 |
| B-L6 | 운영 머신 버전 문구, PR2 의존 | 「Apple Silicon Mac 한 대」로 줄였다. 체크리스트 PR2 의존을 고쳤다 | §4.5, §6 |

### 2라운드: 둘째 판에서 셋째 판으로

2라운드에서 반박 두 명이 둘째 판을 읽었다. 반박 A 는 HIGH 8 · MED 9 · LOW 6 을, 반박 B 는 HIGH 9 · MED 12 · LOW 7 을 냈다.
id 는 라운드마다 새로 매겼으므로 1라운드의 같은 id 와는 다른 지적이다. 같은 근본 원인이 두 라운드 연속 나왔다. 결과 고리
절과 DB 절이 기존 코드와 맞물린 데이터 모델 없이 규칙을 늘어놓았다는 것이다. 그래서 셋째 판은 두 절을 세션 모델 중심으로
다시 세웠다. 반박 id 는 메인 세션의 결정 파일이 묶은 대로 옮겼다.

| 반박 | 다룬 것 | 바뀐 것 | 절 |
|---|---|---|---|
| B-H5 · B-M4 | 없는 `read_auth` 모드, Tailscale bind 의 대가 | `read_auth = "token"` 제안을 지우고 읽기 공개 경고로 바꿨다. Q14 를 더했다. Q10 에 Tailscale bind 의 대가를 적었다 | §4.1, §9, §11 Q10 · Q14, TR10 |
| B-H6 · A-L4 | coordinator 토큰 | setup 은 토큰을 만들지 않고 client 권한 명령을 출력한다. 토큰 표를 본문에 맞췄다 | §4.1, TR21 |
| B-M5 · B-M9 · B-M10 | 측정 환경, 역할별 종료, 도구 이름 금지의 범위 | 깨끗한 환경의 기본 변수를 정했다. 역할마다 종료 규칙을 두었다. 호스트 사실용 허용 목록을 예외로 두었다 | §4.5, TD23 · TD37~TD39 |
| B-M12 | 기본 설치에서 나오는 설치 안내의 범위 | §1 에 한계를 적었다 | §1 |
| B-L2 | Java 스텁 판별 | 실측대로 `libxcselect` 로 판별되지 않는다고 고쳤다 | §4.5 |
| B-L3 | `NO_FOLLOW_COMMANDS` 에 넣는 시점 | doctor 는 PR2, setup 은 PR4, pair 는 PR5 | §4.1, §5, TR17 |
| B-H9 | 실기 격리 | `advertise = false`, URL 명시, `RCM_*` 환경 변수 지우기, XDG 폴더 넷 분리, 시험 launchd 에이전트 정리 | §7, §9 |
| B-L4 | guard 규칙 | setup · pair · doctor 는 DB 명령 목록에 넣지 않는다. setup 이 운영 설정 경로에 쓰는 것을 거절한다 | §4.1, §5 PR4, §9, TR20 |
| A-H6 | `pools_without_workers` 의 기본 풀(C12) | 「그대로 둔다」를 「고칠 것」으로 바꿨다. `lanes ≥ 1` 일 때만 기본 풀을 뺀다 | §3 C12, §4.2, TL13 |
| A-M9 | 실행 머신을 적는 키 이름 | `machine` 을 `ran_on` 으로 바꿨다. `machine` 은 제출 본문의 대상 머신과 겹친다 | §3 E14, §4.7, TN2 |
| A-L1 · B-L1 | 틀린 인용 | claim 은 `store.py:3261` 이다. `PLAN.md` 의 종료 코드 줄 번호를 고쳤다 | §3, §12 |
| B-L6 | 사람 손의 수 | §1 에 두 번 든다고 적었다 | §1 |

### 둘째 판이 반영했다고 적었지만 풀리지 않았던 것

1라운드 표가 「바뀐 것」으로 적은 행 가운데 일곱은 둘째 판에서 실제로 풀리지 않았다. 그 가운데 M5t-a 범위의 셋을 남겼다.
나머지 넷(A-H3 · A-H4 · A-M3 · A-M1)은 M5t-b 의 몫이다.

| 1라운드 반박 | 둘째 판이 적은 것 | 풀리지 않은 곳 | 셋째 판 |
|---|---|---|---|
| A-M10 | `capacity` 를 프리셋 단위로 정의하고 C12 는 그대로 둔다 | C12 의 사실이 틀렸다. 코드는 lanes 와 상관없이 기본 풀을 늘 건너뛴다 | C12 를 고칠 것으로 바꿨다 |
| B-M4 | `/worker/spec` 을 워커별로 거르고 평문 위험을 §9 에 적는다 | 거르는 기준이 될 워커의 풀을 서버가 토큰에 묶어 두지 않았다. LAN 에서는 없는 모드 `read_auth = "token"` 을 제안했다 | 읽기 공개 경고와 Q14. 풀로 거르기와 평문 위험은 M5t-c 로 갔다 |
| B-H5 | `HOME` · `XDG_CONFIG_HOME` 분리, `--label`, guard 의 DB 명령 목록에 setup · pair · doctor 추가 | XDG 폴더 넷 가운데 하나만 갈랐다. 셸의 `RCM_*` 환경 변수, 시험 서버의 mDNS 광고, 실기 뒤 에이전트 정리를 정하지 않았다. guard 목록에 넣은 셋은 DB 를 열지 않는다 | §7 의 격리 규칙, guard 의 운영 설정 경로 규칙 |

### 3라운드와 근본 원인 분석, 오너 답

3라운드에서 반박 두 명이 셋째 판을 읽었고 HIGH 9건을 냈다(MED · LOW 건수는 재료에 없음). 격리 에이전트 하나가 그 HIGH 를
두고 근본 원인을 분석했다. 결론은 policy-conflict 였다. 오너가 정할 정책이 설계 선택 속에 섞여 있었다는 뜻이다. 원인은
다섯이다.

| # | 근본 원인 |
|---|---|
| R1 | 결정 파일이 코드를 읽기 전에 메커니즘을 처방했다. 3라운드 HIGH 9건 가운데 7건이 2라운드 결정 파일이 새로 넣은 문장에서 나왔다. `grace_seconds` 이름 충돌, 보관함 Host 허용 목록 재사용, 저장소 단위 비밀 판정, 「연속 실패 20번」, 운영 라벨 거절이 그렇다 |
| R2 | 결과 고리 프로토콜을 상태 전이 표 없이 규칙 목록으로 설계했다 |
| R3 | 오너가 정할 맞교환을 설계 선택처럼 적었다 |
| R4 | 짝짓기와 서버 신원 보안을 위협 모델 없이 덧대기로 고쳤다 |
| R5 | 한 문서에 하위 시스템이 일곱이고 같은 사실이 5~7곳에 되풀이된다 |

오너는 2026-11-25 원인 분석을 본 뒤 질문 넷에 답했다. 원문 선택지와 그 설명을 그대로 옮긴다.

| 질문 | 오너 답 | 결정 |
|---|---|---|
| 범위 | 「셋으로 나눈다」. 이번 명세 PR 은 M5t-a(역할 · 첫 실행 setup · lanes=0 분배 전용 · rcm doctor OS 진단 · 요청자 알림)만 담고 4라운드 검증. M5t-b(결과 고리 프로토콜: 세션 · 로그 오프셋 · 끊김 유예)와 M5t-c(짝짓기 · 서버 신원 보안)는 상태 전이 표 · 위협 모델부터 쓰는 별도 명세로 | 252 |
| 끊김 정책 | 「되돌릴 수 없는 것만 멈춤」. 배포 · 스토어처럼 두 번 돌면 안 되는 프리셋에만 표식을 두고, 그 잡은 유예(약 10분)가 지나면 워커가 스스로 멈춘다(결과는 '모름' 종료 3). 게이트 · 시험 같은 나머지는 계속 돈다 | 253 |
| 원격 금지 | 「프리셋에 명시 표식」. 프리셋 설정에 `local_only = true` 같은 표식을 두고 기본은 지금처럼 원격 허용. 앱 레포의 release-* · deploy-* 프리셋에 표식을 단다 | 254 |
| 동시 세션 | 「지원 안 함」. 워커 이름당 살아 있는 세션은 하나. 새 세션이 등록하면 옛 세션은 409 를 받고 잡을 멈춘 뒤 끝난다 | 255 |

R1 이 든 다섯 예 가운데 셋은 넷째 판에서 정리했다. 저장소 단위 비밀 판정은 결정 254 의 명시 표식이 대신한다. 운영 라벨
거절은 아래 B-H3 로 고쳤다. 보관함 Host 허용 목록 재사용은 M5t-c 로 넘겼다. 나머지 둘(`grace_seconds` 이름 충돌, 「연속
실패 20번」)은 M5t-b 로 넘겼다(§12).

3라운드 지적 가운데 M5t-a 범위에 남는 것은 이렇게 고쳤다.

| 반박 | 다룬 것 | 바뀐 것 | 절 |
|---|---|---|---|
| B-H3 | 운영 라벨 거절 | 제품은 표준 라벨을 기본으로 두고 있는 파일은 덮지 않으며 같은 라벨이 이미 로드돼 있으면 경고만 한다. 운영 라벨 거절은 실기 하네스와 `tools/guard_production.py` 로 옮겼다 | §4.1, §7, TR18 · TR20 |
| A-H3 · B-H2 | 서버 자기 주소 목록 | M5t-c 로 넘겼다. doctor 의 「mDNS 로 찾은 서버는 사람이 확인한 뒤 프로브」 규칙은 남겼다 | §4.5, §12, TD10 · TD28 |
| B-M5 | 인증 없이 받는 라우트 문장 | M5t-a 에는 인증 예외 라우트가 없으므로 문장을 지웠다. PLAN 「보안」 절에 3판이 더한 문장도 지웠다 | §3 D9 |
| B-M9 | `rcm run` 에 `--notify` 가 없음 | §1 에 한계로 적었다 | §1, §4.6 |
| B-M11 | 실기 격리와 guard | 셸의 `RCM_*` 환경 변수를 모두 지운다. `HOME` 과 XDG 폴더 넷을 따로 둔다. guard 는 setup 과 같은 규칙으로 쓸 경로를 계산한다 | §4.1, §7, TR20 |
| B-L6 | `PLAN.md` 인용 | origin/dev 기준 `PLAN.md:117` 로 되돌렸다 | §3 E11 |
| B-L7 | PLAN 의 M5t 항목 문구 | §1 의 한계에 맞게 좁혔다 | PLAN.md |
| B-L8 | TL12 의 실기 구성 | `lanes = 0` 인 시험 서버 구성을 §7 에 더했다 | §7, TL12 |
| B-L9 | 역할 판별에서 `$RCM_CONFIG` 와 `./rcm.toml` | 둘은 서버 설정으로 읽는다(`config.py:49, 611-627`) | §4.1, TR4 |
| A-M12 | 워크스페이스 안 도구의 warn/0 | `pool` 프리셋에서만 warn/0 이고 `any-capable` 프리셋은 「오지 않음」으로 센다 | §4.5, TD27 |
| A-L2 | `artifacts.state` 의 값 수 | 12 를 13 으로 고쳤다(`core/artifacts.py:37-49`) | §3 E6 |
| A-L3 | 틀린 인용 | 결정 파일은 「인용 고침」만 적었다. 넷째 판은 D10 근거를 고쳤다. 3판의 `store.py:3285-3288` 은 `LaneBusy` 를 설명하는 docstring 이라서 claim SQL 의 실제 줄(`store.py:3379-3398`)로 바꿨다. 다른 인용의 대상은 재료에 없음 | §3 D10 |

결정 파일은 「그 밖에 M5t-a 절에 걸린 MED · LOW」도 반영하라고 적었지만 그 목록은 넷째 판의 재료에 들어 있지 않다(재료에 없음).

### 4라운드: 넷째 판에서 다섯째 판으로

4라운드에서 반박 두 명이 넷째 판을 읽었다. 반박 A 는 HIGH 3 · MED 10 · LOW 12 를 냈다. 반박 B 는 HIGH 2 · MED 10 · LOW 10
과 등급을 정하지 못한 것(unknown) 1 을 냈다. 메인 세션이 지적마다 결정을 정했고 다섯째 판은 그 결정대로 고쳤다. 두 반박이 같은
곳을 다룬 것은 결정 파일이 묶은 대로 한 행에 묶었다. 넷째 판 §4.3 이 「재료에 없음」으로 비워 둔 원격 워커 지정 제출의 응답은
A-H1 · B-M2 로 정해졌다.

| 반박 | 바뀐 것 | 절 |
|---|---|---|
| A-H1 · B-M2 | `local_only` 프리셋을 원격 워커를 지정해 내면 제출 때 409 와 사람용 문장으로 거절한다. 받아 두면 잡이 영원히 대기한다 | §4.3, §9, §11 254, TO2 |
| A-H2 · B-M3 · A-M10 | 대기 계산, 제출 판정, `_could_receive`, 제출 응답의 `eligible_now` 에서도 원격 머신을 뺀다. 기본 풀이 아닌 `pool` 프리셋에 `local_only` 를 달면 기동 실패이고 기본 풀이 아닌 `--pool` 제출은 409 다. `local_only` 점검 프리셋은 `@local` 만 겨냥하고 `lanes = 0` 이면 잡을 만들지 않는다 | §2, §4.2, §4.3, §5, §11 254, TO8~TO12 |
| A-H3 · B-M4 | setup 의 마지막 단계는 서버 없이 도는 진단만 조언으로 보이고 `rcm doctor --role <r>` 을 안내한다. setup 의 종료 코드를 정했다 | §4.1, §11 241, TR13 · TR28 |
| B-H1 | setup 에 포트 · 데이터 폴더 · 광고 · 라벨 인자를 넣고 대화형에서도 묻는다. TR16 은 그 인자로 설정하고 하네스가 시험 값을 확인한다. 정리할 때는 시험 라벨만 내린다 | §4.1, §7, §9, TR16 · TR22 |
| B-H2 | 앱 프로젝트 프리셋에 `local_only` 를 다는 일의 의존을 운영 반영으로 바꿨다. 그 프리셋을 읽는 서버가 모두 v0.11.0 이상이어야 한다고 적었다 | §6, §11 254 |
| A-M1 | 기동과 reload 때 갈 곳을 잃은 대기 잡에 `no_capable_machine` 과 ETA null 을 단다. 실패로 닫지 않는다 | §4.2, §11 243, TL15 |
| A-M2 | `ran_on` 은 서버가 원격이면 `worker`, 로컬 레인이면 `display_name` 으로 계산하고 모르면 null 이다 | §3 E14, §4.6, §11 248, TN9 |
| A-M3 | `worker --check` 의 버전 행은 따라갈 수 있으면 warn, 따라갈 수 없으면 FAIL 이다 | §4.5, TD18 |
| A-M4 · B-M8 | 도구가 빠진 `any-capable` 프리셋은 warn 이고 종료 1 의 조건을 `pool` 프리셋, rcm 자신의 요구, 대상 0개로 좁혔다 | §4.5, §11 246, TD13 · TD40 |
| A-M5 | coordinator 계열 진단이 서버 값을 어디서 읽는지 정했다 | §4.5, §11 246, TD41 |
| A-M6 | TD20 을 PR3 으로 옮겼다 | §4.4, §5, TD20 |
| A-M7 | `lanes = 0` 이면 `rcm check` 의 도구 행을 「이 머신은 잡을 돌리지 않는다」로 그린다 | §4.2, §11 243, TL16 |
| A-M8 | 새 호스트 사실을 주기적인 `/worker/capabilities` 보고에도 싣는다 | §4.5, TD42 |
| A-M9 | shim 규칙을 서버의 `measure_tools` 에도 건다 | §4.5, TD43 |
| B-M1 | `--yes` 로 역할 2·4 를 고르면 `--bind` 가 필수다 | §4.1, §4.7, §9, TR23 |
| B-M5 · A-L1 | 첫 실행 판정은 기존 탐색 함수를 부른다. 탐색 표의 사용자 설정 폴더 행을 실제 규칙대로 고쳤다 | §4.1, §11 239, TR30 |
| B-M6 | 서버 서비스 파일의 필수 키 표를 더했다 | §4.1, TR11 |
| B-M7 | 서비스 파일을 설정 폴더 아래에 쓰고 복사와 bootstrap 명령을 출력한다 | §4.1, §9, TR24 |
| B-M9 · A-L11 | 위장 서버의 위험을 임의 명령 실행까지로 고쳤다. setup 의 워커 경로는 명시 URL 을 쓰고 doctor 는 `server = "auto"` 를 warn 한다 | §4.1, §4.5, §9, TR26 · TD48 |
| B-M10 | §1 에 다섯째 차이를 더했다 | §1 |
| A-L2 | doctor 가 읽는 `/api/health` 의 키 이름을 `preset_env` 로 적었다 | §3 G7, §4.5 |
| A-L3 | `_claimable` 의 갈래는 `known` 을 더해 넷이고 거르기는 WHERE 전체에 건다 | §4.3, TO14 |
| A-L4 | `machines_doc()` 에 토큰이 있는지를 넘기고 `read_auth = basic` 에서도 같은 기준을 쓴다 | §4.5, TD44 |
| A-L5 | 경고로 보는 산출물 상태와 「수집 중」 안내를 정했다 | §4.6, TN10 |
| A-L6 | 제출의 409 검사를 supersede · 합류보다 먼저 한다 | §4.3, TO13 |
| A-L7 · B-L7 | `worker --check` 의 새 종료 3 을 호환 문장과 CHANGELOG 에 적는다 | §4.5, §4.7 |
| A-L8 | PLAN 의 결정 색인 문장을 M5t-a 와 후속 명세로 나눴다 | PLAN.md |
| A-L9 | `capacity` 의 「갈 수 있는 머신」을 정의하고 프리셋 0개면 행을 그리지 않는다 | §4.2, TL17 |
| A-L10 · B-L6 | Tailscale 을 고르면 `advertise = false` 를 쓰고 tailnet 읽기 공개를 경고한다 | §4.1, TR25 |
| A-L12 · B-L10 | 서비스 파일 찾는 순서를 명시 파일, setup 이 쓴 자리, 없음 표시로 정했다. 환경변수만 쓰는 워커는 「설정 있음」이다 | §4.1, §4.5, TD45 · TD49 · TR29 |
| B-L1 | 측정 PATH 를 찍을 때 홈을 `~` 로 가린다 | §4.5, TD46 |
| B-L2 | coordinator 토큰 안내에 `--worker` 토큰 명령을 더했다 | §4.1, TR27 |
| B-L3 | 서비스로 띄운 워커는 파일의 `token` 만 쓴다고 적었다 | §4.1 |
| B-L4 | PR4 의존에 PR3 을 더하고 겹치는 파일 칸에 doctor 를 더했다 | §5, §6 |
| B-L5 | 결정 39 문장은 PR3, 결정 6 문장은 PR5 가 고친다 | §4.2, §4.6, §4.7, §5 |
| B-L8 | root 일 때 sudo 를 빼는 것은 rcm 자신의 안내에만 한다 | §4.5, TD7 |
| B-L9 | `--role` 생략 규칙과 `/worker/spec` 의 `pools` 를 정했다 | §4.5, TD47 · TD51 |
| B-L10 | Local Network 권한은 판정하지 않고 안내만 한다 | §4.5, §9, TD50 |
| B-U1 | 운영 서버와 같은 포트로 시험 서버가 구체 주소에 bind 할 때 macOS 가 가로채는지는 확인하지 못했다. B-H1 의 포트 명시와 확인으로 해소된다고 적었다 | §7 |

오너는 4라운드 뒤 「고치고 5라운드 한 번 더」라고 답했다. 이번 한 번만의 예외다. 그래서 다섯째 판은 5라운드 격리 반박을
한 번 더 받았다(§6 의 1).

### 5라운드와 재설계

5라운드에서 반박 두 명이 다섯째 판을 읽었다. 반박 A 는 HIGH 3 · MED 5 · LOW 7 과 등급을 정하지 못한 것(unknown) 1 을 냈다.
반박 B 는 HIGH 2 · MED 4 · LOW 8 과 unknown 1 을 냈다. 메인 세션은 원인을 하나로 봤다. `local_only` 를 경로마다 따로
거르게 설계해서 라운드마다 빠진 경로가 나왔다는 것이다. 4라운드 HIGH 2건과 5라운드 HIGH 3건이 모두 그런 경우였다.

오너는 5라운드 뒤 「한 곳 필터로 재설계 후 diff 검증」이라고 답했다. 그래서 이 판은 `local_only` 를 후보 머신 함수
하나로 다시 세웠다(§4.3). 6라운드 전체 반박은 하지 않고 고친 diff 만 검증자 둘이 본다(§6 의 1). 아래 표의 반박 id 는 메인
세션의 결정 파일이 묶은 대로 옮겼다.

| 반박 | 바뀐 것 | 절 |
|---|---|---|
| A-H1 · A-H2 | `local_only` 를 「그 프리셋에게 머신 목록은 `@local` 하나뿐」이라는 후보 머신 함수 하나로 다시 세웠다. 배치 · 제출 판정 · 수신 가능 판정 · `capacity` · 점검 정체 판정 · 자동 점검과 recheck · `/worker/spec` · 원격 claim 이 모두 그 결과를 쓴다. 경로별 TO 행을 TO15 하나로 묶고 변이를 그 함수에 걸었다. 후보가 빈 잡은 기존 「갈 곳 없음」 규칙을 따르고 후보가 빈 제출은 409 다 | §2, §3 D10 · G2, §4.2, §4.3, §4.5, §5, §7, §9, §11 254, TO2 · TO5 · TO8 · TO11 · TO14 · TO15 · TO16 |
| A-H3 | `local_only` 가 아닌 프리셋의 `checks` 에 `local_only` 점검이 있으면 원격 머신에서 그 프리셋은 「오지 않음(점검이 로컬 전용)」이고 check_pending 이 아니다. doctor 도 「점검 필요」를 찍지 않는다 | §4.3, §4.5, §11 246 · 254, TO18 |
| A-L1 | 원격 머신에 `local_only` 점검을 recheck 하면 409 다 | §4.3, TO19 |
| A-M3 | `capacity` 는 후보 함수 결과 가운데 살아 있는 머신으로 센다. `checks` 없는 `pool` 프리셋은 판정과 상관없이 그 풀의 살아 있는 레인을 센다 | §4.2, §11 243, TL17 |
| B-H2 · A-L3 | 설정 검증을 하나로 줄였다. `local_only` 인 `pool` 프리셋은 `pool` 이 기본 풀이어야 한다. `pools` 의 기본 풀이 아닌 항목은 제출 409 로 막고, 「OR/AND」가 모호하던 문장은 지웠다 | §4.3, TO1 · TO9 |
| B-M1 | 기동 · reload 때 대기 잡도 후보 함수로 다시 계산되어 원격 지정 잡 · 기본 풀이 아닌 풀의 잡이 `no_capable_machine` 으로 보인다(예약 잡은 6판 diff 검증에서 「queued 가 된 뒤」로 고쳤다) | §4.2, §4.3, TO17 |
| B-H1 | setup 은 고른 `data_dir` 에 DB 가 있으면 거절하고(`--reuse-data-dir`) 쓰는 중인 포트를 경고한다. guard 는 운영 `data_dir` 을 거절한다. 「Store 를 여는 일은 서비스와 같은 빌드가 한다」는 문장은 서비스 파일이 setup 을 실행한 빌드를 가리킨다는 사실로 고쳤다 | §4.1, §5 PR4, §9, §11 241, TR31~TR34 |
| A-M1 · B-L2 · A-U1 | shim 판별을 함수 하나로 두고 `measure_tools` · `missing_tools` · `preset_env` · `rcm check` 도구 행이 함께 쓴다. `xcode-select` 자신은 판별에서 뺀다 | §4.5, §5 PR2, §11 246, TD52 |
| A-M2 · B-L5 | `ran_on` 과 `RCM_MACHINE` 은 `started_at` 이 있을 때만 계산하고 시작하지 못한 잡은 null 이다 | §4.6, §11 248, TN11 |
| A-M4 | `worker` 역할에서 도구 빠진 `any-capable` 의 warn 과 대상 0개가 함께면 1 이다 | §4.5, §11 246, TD53 |
| A-M5 | `LANG=C` 는 doctor 자신의 호스트 사실 명령에만 걸고 `[[probes]]` 에는 걸지 않는다 | §4.5, §11 246, TD54 |
| B-M2 | 역할마다 `--yes` 필수 인자 표를 더했다. doctor 에 `--server` · `--config` 를 더했다 | §4.1, §4.5, §11 241 · 246, TR35 |
| B-M3 | 서비스 파일 찾는 순서에 `<config dir>/service/*` 와 표준 라벨의 설치 자리를 넣었다. 둘 이상이면 고르게 하고 비TTY 면 종료 2 다 | §4.5, §11 246, TD45 |
| B-M4 | 실기 하네스 `scripts/acceptance/m5t_live.sh` 는 PR7 이 만든다. 운영 라벨은 설치된 유닛의 라벨과 표준 라벨 둘이다. 하네스는 bootstrap 할 plist 의 `--config` 경로 · `HOME` · 로그 경로 · 라벨도 확인한다 | §5 PR7, §7, TR16 |
| B-L1 | TR16 에 토큰 단계를 넣었다 | §7, TR16 |
| B-L3 | 같은 라벨의 로드 여부는 읽기 전용 조회로만 보고 복사 명령은 `cp -n` 꼴이다 | §4.1, §11 241, TR36 |
| B-L4 | 로그 파일 이름은 라벨에서 만든다 | §4.1, §11 241, TR37 |
| B-L6 | 운영을 v0.11.0 미만으로 되돌리는 롤백 함정을 적었다 | §9 |
| B-L7 | 서비스 파일 env 는 이름만 찍고 두 PATH 모두 홈을 `~` 로 가린다. 기본 변수 목록은 launchd · systemd 별로 적는다 | §4.5, §11 246, TD37 · TD46 · TD55 |
| B-L8 · A-L6 | PLAN 색인에 248 a~d · g(M5t-b)를 더했다. 머리 인용의 「세 라운드」를 「다섯 라운드와 6판 diff 검증」으로 바꿨다. 결정 39 는 「결정 39(M5f)」로 적었다 | PLAN.md, §3 C9, §4.2, §4.7, §5, §10, §11 243 |
| B-U1 | `local_only` 를 달기 전에 그 프리셋을 읽는 서버 목록을 확인하는 단계를 더했다 | §6 의 17 |
| A-L2 | `lanes = 0` 에서 원격 claim 롱폴이 0.5초 DB 폴링이 되는 것을 위험에 적었다 | §9 |
| A-L4 | `[[tools]]` 의 이름은 프로브 argv[0] 과 맞춰도 된다 | §4.5, §11 246, TD9 |
| A-L5 | 호환 문장에 `worker --check` 버전 행의 FAIL 을 적었다 | §4.7 |
| A-L7 | `$RCM_CONFIG` 가 없는 파일이면 usage 오류로 종료 2 다 | §4.1, §11 239, TR38 |

### 6판 diff 검증과 구현 PR 에 넘기는 것

오너가 고른 대로 6판은 바뀐 diff 만 검증자 둘이 봤다. 검증자 B 는 HIGH 0(MED 4 · LOW 11 · unknown 2)이었고, 검증자 A 는
HIGH 2(MED 5 · LOW 5 · unknown 1)였다. A 의 HIGH 둘은 이 판에서 고쳤다. 하나는 후보가 비는 경우의 새 분기를 명시한 것이고
(§4.3), 다른 하나는 `/api/eta` 의 pool 경로와 `/api/machines`, claim 에 넘기는 값을 후보 함수 표에 더한 것이다(§4.3 표).
오너는 「두 건 고치고 PR 올림」을 골랐다. 남은 MED · LOW 는 코드를 보면서 판정해야 하는 것이라 아래 PR 이 시험과 함께 다룬다.
각 PR 의 격리 검증이 이 목록을 확인한다.

| 지적 | 다룰 PR | 요지 |
|---|---|---|
| 6-A-M1 | PR1 | `check_comes_by_itself` · `_flag_recheck` · `_check_rows` 도 후보 함수를 거친다. 로컬 전용 점검에 걸린 원격 프리셋의 이유 코드를 정한다 |
| 6-A-M2 | 이 판 | `/api/machines` 를 후보 함수 표와 TO15 에 더했다 |
| 6-A-M3 | PR1 | TO15 를 진입점별로 파라미터화해 변이 하나에 진입점마다 빨개지는지 본다 |
| 6-A-M4 | 이 판 | 예약 잡은 큐 계산 밖이라 queued 가 된 뒤 규칙을 탄다고 §4.3 · TO17 을 고쳤다 |
| 6-A-M5 | PR1 | 409 조건은 위 §4.3 의 정적인 넷과 `tool_missing` 뿐이다 — 시험으로 잠근다 |
| 6-A-L2 · 6-B-V14 | PR3 | `capacity` 의 「pool 프리셋은 판정을 보지 않는다」는 후보 함수 결과 안에서만이다 |
| 6-A-L3 | PR1 | 점검 잡 생성(`submit_internal_check`) 앞에서 후보를 확인한다(`recheck()` · `sweep_checks`) |
| 6-A-L4 · 6-B-V5 | PR4 | guard 규칙 셋째는 `_effective_data_dir` 과 같은 순서로 경로를 풀고, 역할 2 · 4 와 「그 안」까지 본다 |
| 6-A-L5 | PR1 | 점검 프리셋의 「갈 곳 없음」을 그릴 자리와 값을 정한다 |
| 6-B-V1 | PR4 | systemd 유닛 이름 규칙 하나(표준 라벨은 `rcm-server` · `rcm-worker`, 다른 라벨은 `<label>.service`)를 setup · doctor · guard 에 같이 쓴다 |
| 6-B-V2 | PR2 | doctor 는 설치 자리를 먼저 보고, 후보를 역할의 라벨로 거른다 |
| 6-B-V3 | PR4 | `--reuse-data-dir` 이면 DB 헤더의 `user_version` 과 이 빌드의 `DB_VERSION` 을 비교해 경고한다 |
| 6-B-V4 | PR7 | 롤백 순서: 원격 워커를 pause 한 뒤 `local_only` 를 빼고 옛 판으로 기동을 확인한다 |
| 6-B-V6 · V7 · V8 · V9 · V10 | PR4 | `_this_rcm()` 이 PATH 로 떨어진 경우의 표시, 표준 라벨의 로그 이름 유지, `is-active` 확인과 덮지 않는 복사, 포트 사용 판정법, `--yes` 표의 빠진 플래그 |
| 6-B-V11 · V12 · V13 | PR2 | doctor `--server` 우선순위와 역할별 `--config`, PATH 만 값을 가려 찍기, `LANG=C` 범위 |
| 6-B-V15 · 6-A-L1 | 이 판 | PLAN 문장을 맞췄다 |
| 6-B-U1 · U2 · 6-A-U1 | PR2 · PR4 · PR1 | 서비스 관리자별 기본 변수 실측, `cp -n` 종료 코드, doctor 가 같은 후보 함수로 판정하는지 |

고친 줄만 본 마지막 검증(검증자 한 명)은 254 행 · TO16 · TO17 · B-M1 이 새 §4.3 과 어긋난다는 것을 짚었다(HIGH 2 · MED 4 · LOW 3).
같은 사실이 여러 곳에 적혀 있다는 근본 원인(§13 3라운드 R5)이 그대로 드러난 것이라, 그 자리를 모두 §4.3 에 맞췄다. 새 분기와
제출 409 의 범위는 `local_only` 프리셋으로 한정했고(`local_only` 가 아닌 프리셋의 동작은 바꾸지 않는다), 인용을 `capabilities.py:747-766`
과 `:785-789` 로 바로잡았으며, TO15 에 새 진입점 셋을 더했다.

## 14. 구현하며 바뀐 것 (Changed while implementing)

명세를 머지한 뒤 구현 PR 이 명세와 다르게 짓거나 명세가 정하지 않은 자리를 정한 것을 적는다. §4~§7 은 고치지 않고 여기에만 적는다.
「구현 결정」은 리드가 정하고 격리 검증이 본 것이고, 결정 번호가 있는 것은 오너 위임(2026-11-26)으로 메인 세션이 Codex 와 논의해 정했다.

### PR4 `rcm setup` (#272)

| 표시 | 무엇 | 명세 | 지은 것 | 이유 | 정한 곳 |
|---|---|---|---|---|---|
| 명세와 다름 | 운영 보호의 자리 | §4.1 서비스 파일 행: 「운영 라벨을 거절하는 일은 제품이 하지 않고 실기 하네스와 `tools/guard_production.py` 가 한다」 | `rcm setup` 자신이 쓰기 직전(두 번)에 이 머신에서 찾을 수 있는 rcm 서비스(launchd 사용자 · 시스템 폴더, systemd 사용자 · XDG · 시스템 폴더)를 읽고, 대상이 그 서비스의 config · data_dir · venv · checkout 이거나 그 안이면 종료 1 로 거절한다. 우회 플래그는 없다. rcm 처럼 보이는데 읽지 못하는 유닛 · 띄우는 명령 줄에 rcm 이 보이는데 풀 수 없는 유닛은 닫는 쪽. `$HOME` 과 계정의 홈(passwd)을 둘 다 보고, 계정 홈을 못 찾으면 거절한다 | 훅은 셸 명령 글을 판정하므로 닫힌 목록마다 바깥이 있었다(P3 다섯 라운드 · P6 재확인 두 라운드의 HIGH) | 결정 352 |
| 명세와 다름 | 훅의 역할 | TR20 · TR33: 훅이 setup 과 같은 규칙으로 경로를 계산해 막는다 | 훅은 1차 방어. 운영 설치가 있는 머신에서 setup 에 닿을 수 있는 명령은 「평범한 setup 명령 하나」(메타 문자 · 역슬래시 · 글롭 없음, 절대 · `~` 경로만)일 때만 setup 의 해석기(`core.where`)로 판정하고, 그 밖은 거절한다. 인자를 넘기는 런처 · pty 런처와 rcm 낱말이 함께 있으면 거절. 셸 난독화(변수 명령, `sed … \| sh`, 인터프리터가 만든 명령)는 문서화된 남는 위험 | 352 가 쓰기를 막는다 | 결정 353 · 354 |
| 명세에 없음 | 운영 머신에서 rcm 과 setup 을 함께 담은 명령 글 | 정의 없음 | 훅이 거절한다(커밋 메시지 · PR 제목도). 파일로 넘긴다(`git commit -F <파일>`, `--title "$(cat <파일>)"`) | 글만으로는 setup 호출과 언급을 가를 수 없다 | 결정 353 · commit · pr 스킬 |
| 명세에 없음 | 시작 명령의 꼴 | `cp -n` 뒤 bootstrap/enable | `cp -n` 한 줄, 그다음 `cmp -s <쓴 것> <설치된 것> && <시작> \|\| echo "not started: …"`. 다시 쓸 때는 bootout/disable → cp → bootstrap/enable 순서와 「to remove:」 줄을 낸다. Linux 는 linger 줄이 시작보다 앞, 워커는 토큰 → 시작 순서 | macOS 와 GNU 의 `cp -n` 종료 코드가 다르다(6-B-U2) | 구현 결정 |
| 명세에 없음 | `--force` 꼴 | 파일마다 `--force` | `--force config\|service\|all`. setup 이 설치한 서비스도 그 config 는 지킨다(먼저 서비스를 지우라는 안내) | TR6 · 결정 352 | 구현 결정 |
| 명세에 없음 | 종료 코드 세부 | 0 · 1 · 2 | 파일을 다 쓴 뒤 보고가 실패하거나 Ctrl-C 면 0(쓴 파일을 알린다), 그 전 Ctrl-C 는 130, 예상 못 한 예외는 한 줄 + 1, 빈 경로 값 · 줄인 옵션(`allow_abbrev=False`) · 이름으로 준 `--bind` 는 2 | §4.1 「설정을 썼으면 0」 | 구현 결정 |
| 명세에 없음 | systemd 유닛 값 | 정의 없음 | systemd.syntax 대로 따옴표(`%`→`%%`, ExecStart 의 `$`→`$$`), 제어 문자 거절. 서버 유닛과 setup 이 쓰는 워커 유닛은 라벨 로그 `~/.local/state/rcm/<라벨>.log` 와 HOME 을 싣는다 | 공백 · `%` 가 든 경로가 유닛을 깨뜨렸다 | 구현 결정 |
| 명세에 없음 | 서비스 관리자 답의 「모름」 | 정의 없음 | launchd 는 「Could not find service」, systemd 는 `is-active` 3 + `inactive`/`unknown` 일 때만 「올라가지 않음」. 그 밖(버스 없음 · 도메인 없음 · 시간 초과)은 「could not tell」 경고 | 「모름」을 「없음」으로 보이지 않는다 | 구현 결정 |
| 명세에 없음 | 첫 실행 질문 문구 | 「설정을 시작할까요?」 | `No rcm config on this machine yet. Set it up now? (y/n) [y]`(stderr). 인자가 하나도 없을 때만. n · EOF 는 종료 2 | CLI 문구는 영어 | 구현 결정 |
| 명세에 없음 | 읽지 못하는 기존 설정 | 정의 없음 | 역할 찾기가 setup 을 막지 않는다(경고). 덮는 것은 여전히 `--force config` 일 때만 | 깨진 설정을 setup 으로 고칠 수 있게 | 구현 결정 |
| 명세와 다름 | 설정 탐색 순서 | 「config.py 에 구현」 | config.py 의 기존 탐색 함수를 그대로 부른다(doctor.infer_roles) | 이 명세 :279 가 기존 함수를 부르라고 한다 | RESOLVED |

남은 한계는 docs/operating.md 의 setup 절에 있다(손으로 띄운 서버, 다른 사용자의 서비스, 스캔하지 않는 유닛 폴더, `launchctl bootstrap <경로>` · `systemctl link`, 이름이 rcm 이 아닌 래퍼 스크립트, 읽지 못하는데 이름이 rcm 과 무관한 파일).

### PR5 요청자 알림 (#273)

| 표시 | 무엇 | 명세 | 지은 것 | 이유 | 정한 곳 |
|---|---|---|---|---|---|
| 명세와 다름 | 끝 줄의 자리와 이름 | §4.6 · TN 행: 「사람용 마지막 줄」 | 「끝 줄」. 끝 줄 뒤에 실패 줄 · 산출물 경고나 「수집 중」 줄 · 보관함 줄 · `--notify` 경고가 올 수 있고, stdout 의 JSON 은 늘 마지막이다 | 산출물 · 경고 줄이 결과 뒤에 와야 읽힌다 | 구현 결정 |
| 명세에 없음 | `rcm run` 의 기다림 | §4.6 은 `rcm wait` 만 이름 붙였다 | `rcm run` 은 끝 줄과 산출물 줄을 찍지 않는다(그대로) | 골든 시험 `test_nowait_resilience` 가 `rcm run` 의 마지막 출력 줄을 잠근다 | 구현 결정 |
| 명세에 없음 | 산출물을 모으지 않는 프리셋 | TN4: 산출물 상태를 사람용 줄에 보인다 | `disabled` 는 끝 줄에 적지 않는다 | 모으지 않는 프리셋마다 「artifacts disabled」 가 붙는다 | 구현 결정 |
| 명세에 없음 | 시작하지 못한 잡의 산출물 | TN10: `unknown` 은 경고 | `started_at` 이 null 인 잡은 산출물을 말하지 않는다(경고도 끝 줄 꼬리도 없음) | 돌지 않은 잡에 올릴 것이 없다 — 「unknown」 경고는 소음이다 | 구현 결정(P5) |
| 명세에 없음 | `--notify` 의 순서와 문구 | 정의 없음 | 알림은 JSON 줄 뒤에 뜨고, 알림 중 Ctrl-C 는 알림만 건너뛰고 잡의 종료 코드로 끝난다. 산출물 전달 실패(종료 5)면 알림이 그 실패를 먼저 말한다. `notify-send` 인자 앞에 `--` | 알림은 덤이다 — 결과와 종료 코드를 붙잡지 않는다 | 구현 결정(P3 · P5) |
| 명세에 없음 | 로컬 레인의 `ran_on` | 「machines 표나 gethostname」 | 서버의 지금 호스트 이름을 따른다(저장하지 않는다). 원격 잡은 claim 때의 워커 이름 그대로 | 저장 칸을 두지 않는다는 명세대로 | 구현 결정 |
