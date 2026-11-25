# M5t-b 작업 명세: 결과 고리 · 워커 세션 · 로그 오프셋 · 끊김 유예

> 2026-11-24 오너가 Codex 세션에서 rcm 을 쓰는 머신을 세 역할로 나누고 싶다고 했다. 원문을 그대로 옮긴다.
>
> 「자 이제 rcm 이 프로젝트를 / 맨 처음 시작하면, 이거를 rcm 빌드용으로 쓸건지 아닌지 확인하고, 빌드용으로 쓴다고 하면
> 설정을 알아서 완료하고, 다른 rcm 제어하는곳에서 이걸 연결하면 거기에서 알아서 분배하는걸 만들고싶어 즉 / 1. rcm 으로
> 작업 보내는 컴퓨터(유저가 사용) / 2. rcm 으로 작업 받는 컴퓨터 + 작업을 다른 유휴중인 컴퓨터로 보내는 작업 / 3. rcm
> 작업을 진행해서 결과 전달하는 컴퓨터 / 이렇게 3가지로 나눈고싶은거야 이해했어? 이렇게 작업하려는데 2와 3이
> 필요해보이는데」
>
> 같은 날 오너는 두 마디를 더 했다. 「이 컴퓨터에서는 4를 하고싶어」의 4 는 2 와 3 을 함께 하는 머신이다. 다른 하나는
> 「Rcm워커 다 한 다음에 어떻게 알려주는거까지 해야지」다. 이 명세는 둘째 말 가운데 알릴 결과가 사라지거나 틀리지 않게
> 하는 쪽을 맡는다(§1).
>
> 2026-11-25 오너는 두 가지를 더 요구했다.
>
> 1. 「아 물론 RCM 설치하는 OS에 따라서 rcm worker가 어떻게 빌드하고 어떻게 테스트하고 등등을 체크해야해 그리고
>    어떤것들을 설치해야하는지 사용하기전에 그런것들을 알려주는것도 필요해」
> 2. 「당연하게도 … 민감데이터나 API 키 등 그런것들은 없어야해 그리고 PR올려야해 이 프로젝트는 public 일거니까」
>
> 같은 날 오너는 `docs/m5t-workplan.md` 의 질문 넷에 「셋으로 나눈다」 · 「되돌릴 수 없는 것만 멈춤」 · 「프리셋에 명시 표식」 ·
> 「지원 안 함」으로 답했다. 그 문서의 결정 252~255 이고 답의 원문은 `docs/m5t-workplan.md:1227-1230` 에 있다.
>
> 2026-11-26 오너는 「high안나올때까지 하고 …」, 「나의 지시없이 이 격리에이전트 테스트까지 해서 수정하고 배포하고
> 메인에올려서 버전업까지 하는게 목표야」라고 했다. 범위는 M5t-b 와 M5t-c 까지다. 이어서 「이후에 물어볼거 결정사항이 있다면
> 코덱스와 논의하고 완료보고서 작성할때 코덱스논의 한거 작성해둬」, 「멀티에이전트로 나눠서 작업가능한건 나눠서해 dev
> batch로」라고 지시했다.
>
> 이 명세의 출처는 위의 오너 발언, `docs/m5t-workplan.md` 의 결정 252~255 와 §12 「M5t-b 결과 고리 프로토콜」, 접두어 RL 로
> 쓴 시나리오 표, 오너가 맡긴 결정이다. 맡긴 결정은 2026-11-26 메인 세션이 Codex 와 논의해 두 차례(1차 · 2차) 정했다. 같은
> 날 1라운드 격리 반박이 끝난 뒤 메인 세션이 오너 위임으로 D-a~D-i 를 더 정했다. Codex 원문 파일과 오너 발언 원본 파일은
> 레포 밖에 있고 이 레포가 공개라서 경로를 적지 않는다.
>
> 결정 번호는 256 부터다. 256~279 는 M5t-c 와 겹치지 않게 미리 나눈 범위다. M5t-c 가 280~303 을 썼고 304 부터 이어 쓸 수
> 있어서 320~339 를 M5t-b 몫으로 두었다. 2판은 그 가운데 320~329 를 쓴다.
>
> 이 판은 2판이다. 1판(head `804c17d`)은 1라운드 격리 반박에서 HIGH 3건(V1-1 · V2-1 · V2-2)을 받았다. 2판은 그 라운드의
> 지적을 반영했고 고친 것과 고치지 않은 것을 §12.3 에 적었다. M5t 전체를 한 문서에 담았다가 거절된 셋째 판(이 문서에서
> 「3판」)의 결과 고리 설계는 재료로만 썼다.

## 0. 한 줄

원격 워커와 서버 사이의 연결이 끊기거나 어느 한쪽이 재시작돼도 잡의 결과가 정확히 한 번, 거짓 없이 요청자에게 돌아오게
한다. 장치는 셋이다. 첫째는 워커 세션이다. 워커 프로세스마다 세션 id 를 하나 두고 같은 이름의 새 세션이 등록하면 옛
세션을 펜싱한다(결정 255). 둘째는 끊김 유예다. 기본값은 600초이고 잡마다 claim 때 그 값을 얼린다. 되돌릴 수 없음 표식
`irreversible = true` 를 단 프리셋만 워커가 유예가 끝나기 전에 스스로 멈춘다(결정 253). 멈출지는 heartbeat 와 따로 도는
감시 스레드가 정한다. 마지막 2xx 응답 뒤의 경과가 G − m 이상이면 멈춘다. 여유 m 은 요청 시한과 SIGTERM 에서 SIGKILL 까지의
대기와 남김을 더한 값이다(결정 320). 셋째는 로그 오프셋과 순서 있는 보고 큐다. 워커는 phase, 로그, 산출물, finish 순서로
보내고 큐를 디스크에 남겨 재시작을 넘긴다. 서버는 잡마다 잠금 하나를 두고 그 안에서 오프셋 확인, 정해진 위치에 쓰기, 커밋을
한 단위로 처리한다(결정 323). 2판은 1라운드 지적의 뿌리를 고쳤다. 상태를 바꾸는 두 단계는 한 단위로 묶는다. 묶을 수 없으면
늘 안전한 쪽으로 어긋나게 한다(§4.1.4 점검표). 첫 절은 상태 전이 표와 타이머별 시계(§4.1)이고 나머지 설계는 그 표의 칸에서
나온다. DB 는 v26, 워커 프로토콜은 2 가 된다. 구현 PR 은 넷이다. PR1 이 줄기이고 PR2 와 PR3 이 그 뒤에 병렬로 가며 PR4 가
실기 수용이다. 구현 PR1 은 M5t-a 가 모두 머지된 뒤 시작한다(F54 · §5).

## 1. 출처와 오너 결정

### 1.1 요구와 이 명세

요구마다 그 요구가 말하는 것과 이 명세가 정하는 것을 나란히 둔다. 마지막 세 행은 2판에서 더했다(V1-8).

| 요구(출처) | 요구가 말하는 것 | 이 명세가 정하는 것 |
|---|---|---|
| 「3. rcm 작업을 진행해서 결과 전달하는 컴퓨터」(2026-11-24) | 실행 머신이 결과를 전달한다 | 결과 전달을 끊김 · 재시작 · 중복에 견디게 한다(§4.2~§4.6) |
| 「Rcm워커 다 한 다음에 어떻게 알려주는거까지 해야지」(2026-11-24) | 워커가 끝낸 뒤 알림까지 | 알림 자체(사람용 마지막 줄 · `ran_on` · `rcm wait --notify`)는 M5t-a 결정 248 e·f 다. 이 명세는 알릴 결과가 사라지거나 틀리지 않게 하는 쪽(결과 고리) |
| 결정 253 「되돌릴 수 없는 것만 멈춤」 | 표식 프리셋만 유예(약 10분) 뒤 워커가 스스로 멈추고 결과는 종료 3, 나머지는 계속 | 표식 키 `irreversible`(결정 257), 유예 600초(결정 263), 자기 멈춤(결정 320 — 1판 267 을 대신), 일반 프리셋은 계속 돌고 서버는 유예 뒤 `lost`(결정 256) |
| 결정 255 「지원 안 함」 | 워커 이름당 살아 있는 세션 하나, 새 세션이 옛 세션을 펜싱, 옛 세션은 409 받고 잡 멈춘 뒤 끝남 | 세션 표와 펜싱(결정 265), 펜싱된 잡의 자리(결정 261), 종료 코드 0(결정 258) |
| 결정 252 「셋으로 나눈다」 | M5t-b 는 상태 전이 표부터 | §4.1 이 첫 절 |
| 2026-11-26 「멀티에이전트로 나눠서 … dev batch로」 | 병렬로 나눌 수 있는 것은 나눈다 | §5: PR1 뒤 PR2 · PR3 이 겹치는 파일 없이 병렬 |
| 2026-11-26 「이후에 물어볼거 … 코덱스와 논의」 | 새 질문은 Codex 와 논의 | 위임 결정 256~279 · 320~329(§11). 새 질문도 메인 세션이 Codex 와 논의해 정한다 |
| 2026-11-25 「민감데이터나 API 키 … 없어야해 … public」 | 공개 레포 | §8 |
| 2026-11-25 「아 물론 RCM 설치하는 OS에 따라서 rcm worker가 어떻게 빌드하고 어떻게 테스트하고 등등을 체크해야해 그리고 어떤것들을 설치해야하는지 사용하기전에 그런것들을 알려주는것도 필요해」 | 실행 머신이 OS 별로 무엇을 빌드 · 시험할 수 있고 무엇을 깔아야 하는지 쓰기 전에 안다 | M5t-a 결정 246(`rcm doctor`)이 다룬다 — dev 에 머지됨(#268, F54). 이 명세는 `rcm doctor` · `rcm worker --check` 가 세션을 만들지 않아 도는 서비스 워커를 펜싱하지 않게만 한다(F46, RL12 c) |
| 2026-11-24 「이 컴퓨터에서는 4를 하고싶어」(4 = 2 와 3 을 함께 하는 머신) | 받는 머신이 자기 레인으로도 돌린다 | 역할 자체는 M5t-a 결정 242(`coordinator-worker`). 이 명세에서 그 머신의 자기 레인 잡은 로컬 잡이라 세션 · 유예 칸이 NULL 이고(§4.3 얼림) 서버 재시작 규칙도 오늘대로(F4). 결과 고리는 그 머신에 붙은 원격 워커에 걸린다 |
| 2026-11-26 「나의 지시없이 이 격리에이전트 테스트까지 해서 수정하고 배포하고 메인에올려서 버전업까지 하는게 목표야」 | 격리 검증 · 수정 · 배포 · main 반영 · 버전업까지 지시 없이 | 명세 절차(격리 라운드 HIGH 0 · 결정은 Codex 와)는 §6 #1~#3, 구현 · 격리 검증은 §6 #4~#10, main 릴리스 · 버전업 · 운영 반영은 메인 세션의 릴리스 계획(§6 #14 · #15, 운영 반영은 큐가 빌 때). 이 명세의 PR 은 dev 까지 |

### 1.2 이미 확정된 것

아래는 이 명세가 바꾸지 않는 결정과 오너가 맡겨 이미 정한 결정이다. 따옴표 안은 원문이다.

- 결정 253(`docs/m5t-workplan.md:1042`): 「배포 · 스토어처럼 두 번 돌면 안 되는 프리셋에만 표식을 두고, 그 잡은 유예(약
  10분)가 지나면 워커가 스스로 멈춘다. 결과는 「모름」(종료 3)이다. 게이트 · 시험 같은 나머지는 계속 돈다. M5t-b 가
  구현한다」
- 결정 254(`docs/m5t-workplan.md:1043`): `local_only = true` 다. dev 에 머지됐다(#266, CHANGELOG `[Unreleased]`).
- 결정 255(`docs/m5t-workplan.md:1044`): 「워커 이름당 살아 있는 세션은 하나다. 새 세션이 등록하면 옛 세션은 409 를 받고
  잡을 멈춘 뒤 끝난다. M5t-b 가 구현한다」
- M5n §13(`docs/m5n-workplan.md:652`)의 안 하는 것: 「유실된 잡을 다른 머신에서 자동 재실행하기. 배포·업로드 프리셋은 두 번
  돌면 안 된다.」 이 명세도 그대로 둔다.
- PLAN 「fail-open 금지」(`PLAN.md:516-528`): 종료 3 은 「모른다」이고 `lost` 를 조용히 되돌리지 않는다. 이것도 그대로 둔다.
- 오너가 맡긴 결정 1차(2026-11-26, 메인 세션이 Codex 와 논의해 정함)는 질문 Q1~Q7 과 기본값 둘이다. §11 의 결정 256~264 가
  되었다.
- 오너가 맡긴 결정 2차(같은 날)는 다음과 같다. 레인 질문 Q-A 는 결정 279 가 되었다. Q-B 는 잡이 이미 `lost` 인 뒤 펜싱된
  세션이 보낸 finish 를 409 `session_fenced` 로 거절하고 아무것도 남기지 않는다(274 · §4.2 · §4.6). Q-C 는 서버가 재시작한
  뒤의 유예를 기동 시각부터 다시 센다(266 · §4.3). Q-D 는 고아의 신원을 확인하지 못하면 신호를 보내지 않고
  `previous_sessions` 에 넣지 않으며 유예가 끝날 때까지 잡아 둔다(268 · §4.4). 모를 때 위험한 동작을 하지 않는 쪽(fail-closed)이다.
  추천이던 265~278 도 이때 확정됐다. M5t-c 와 맞물리는 순서도 정했다. `pair_proof` 검사는 세션 넣기, 펜싱, 능력 보고 지우기,
  잡 닫기보다 먼저 한다. 버전과 프로토콜 검사는 부작용이 없으면 그보다 앞서도 된다(§4.2 · §9).
- 1라운드 뒤 메인 세션 결정 D-a~D-i(오너 위임, 2026-11-26)는 §11 의 결정 320~327 과 §1.1, §2, §11.1 대응표가 되었다. D-a 는
  자기 멈춤 감시 스레드, m 공식, G 하한, `ok_at` 의 처음 값, heartbeat 응답에 싣는 닫힌 잡을 정한다. D-b 는 잡별 로그 잠금,
  정해진 위치에 쓰기, 되자르기, 짧은 파일 처리를 정한다. D-c 는 finish 상한이다. D-d 는 취소된 잡이 유예로 `lost` 가 되면 닫힌
  잡 목록에 넣는 것이다. D-e 는 세션 폴더의 주인이고 D-f 는 죽은 세션 큐의 응답을 그 큐에만 적용하는 것이다. D-g 는 Linux
  `/proc` 신원, 재확인, 경고다. D-h 는 오너 발언 셋과 §2 문구이고 D-i 는 결정 번호 대응표와 PR0 의 PLAN 편집이다. 재료가 D-*
  문구와 다르게 정한 넷(D-a · D-b · D-c · D-g)과 결정 328 · 329 도 같은 날 위임으로 확정됐다(§11).
- M5t-c 명세 PR #269 의 결정 301~303(F52): 확실한 신원 불일치는 종료 0 이고 서비스가 되살리지 않는다. 연결 실패와 5xx 는
  프로세스 안에서 다시 시도한다. 프로토콜 불일치는 M5t-b 대로 종료 3 이다. 부모 명세의 M8 은 「펜싱 · 확실한 신원 거절」로
  좁힌다.

### 1.3 이 명세가 하지 않는 것

- 짝짓기, 서버 신원, `tokens.pool`, 평문 LAN 위협 모델은 M5t-c 가 한다(DB v27).
- 요청 머신의 OS 알림, `ran_on`, `RCM_MACHINE`, 사람용 마지막 줄의 실행 머신은 M5t-a 결정 248 e·f 가 한다.
- `lost` 잡을 자동으로 다시 돌리지 않는다(M5n §13 유지). 늦게 온 결과로 `lost` 를 뒤집지도 않는다(결정 256).
- 프로토콜 1 워커의 새 claim 은 받지 않는다. 결정 262 의 레거시 lease 만 받는다. 버전이 섞인 운영 경로를 새로 만들지 않는다.
- 워커 쪽에서 비밀을 가리지 않는다. 서버가 가린다(F14). 부분 업로드 재개(`PLAN.md:119`)와 Windows 도 다루지 않는다.
- 웹에 `late_result` 를 그리지 않는다. 잡 문서, `rcm wait` JSON, 사람용 줄에만 둔다(결정 274).
- `irreversible` 이 없는 원격 가능 배포 · 스토어 프리셋을 `rcm check` 나 `rcm doctor` 가 이름으로 추측해 경고하지 않는다.
  1라운드에서 V1 이 제안했지만 이름으로 추측한 경고는 거짓 경보를 낸다. §9 의 위험 행과 문서 경고만 둔다(§12).
- 리더가 죽고 멤버만 남은 고아 프로세스 그룹을 멤버로 알아보지 않는다. 그런 그룹은 「모름」으로 두고 다시 확인하며
  경고한다(결정 327, §9).

## 2. 범위: 누가 무엇을 하나

rcm 이 이 명세의 PR1~PR4 로 대부분을 만든다. 이 레포를 쓰는 앱 프로젝트는 프리셋에 표식을 단다. 오너는 명세를 승인한다.
운영 반영은 2026-11-26 목표에 넣어 승인했다. 메인 세션은 결정 기록과 조율, main 릴리스와 운영 반영을 맡는다.

| 누가 | 무엇 |
|---|---|
| rcm (이 명세, PR1~PR4) | 세션 · 펜싱 · 유예 · 자기 멈춤 · 재시작 순서 · 고아 정리 · 프로토콜 2 · 로그 오프셋과 보고 큐 · `late_result` · `log_complete` · 화면 사유 · DB v26 · 문서 |
| 이 레포를 쓰는 앱 프로젝트(그쪽 세션, 오너 지시 뒤) | 원격 워커에서 돌 수 있는 배포 계열 프리셋에 `irreversible = true` 를 단다. 모르는 프리셋 키는 기동 실패라(F23) 그 프리셋을 읽는 서버가 모두 이 기능이 든 버전 이상일 때만 단다. `local_only = true` 인 프리셋은 원격에 안 가므로 `irreversible` 이 효과가 없다(F24) |
| 오너 | 명세 승인(머지). 레인 답(결정 279, 위임으로 확정). 운영 반영은 오너가 목표에 넣어 승인함(2026-11-26), 큐가 빌 때까지 기다림. 2026-11-26 지시로 그 밖의 질문은 메인 세션이 Codex 와 정하고 완료 보고서에 적는다 |
| 메인 세션 | 결정 페이지 · Codex 논의 기록 · dev-batch 운용 · M5t-c 세션과 결정 번호 · DB 판 조율(§6 #11 · #16) · main 릴리스 · 버전업 · 운영 반영(큐가 빌 때, M5t-a · M5t-c 와 함께 묶을지는 메인 세션의 릴리스 계획) |

## 3. 실측과 코드 제약

기준은 고정 sha `fdf196d4ef682612857fb12df9162f67545b63f0` 이다. 1판을 시작한 커밋이고 이 명세 브랜치의 밑이다(1판 head
`804c17d95e644e82ad0a3351b4e01a5d50b9413a`). origin/dev 는 그 뒤 `96a8bd25fa0b0457f6bc75a336c43aeb2a4724ec`(M5t-a PR2 #268
머지)로 갔다. 그 사이에 바뀐 곳은 F54 에 따로 적었다. 구현 PR 은 dev 에서 줄 번호를 다시 찾는다. 모듈 파일은
`src/remote_ci_monitor/` 를 빼고 적었다. 각 사실이 설계에 미치는 영향은 표의 마지막 열에 적었다. 운영 수치는 받지 않았으므로
운영 빈도는 `—` 로 둔다. 같은 사실은 이 절에 한 번만 두고 다른 절은 F 번호와 결정 번호로 가리킨다.

이 명세가 쓰는 내부 용어를 먼저 풀어 둔다. 워커는 서버에서 잡을 집어 가 실제로 돌리는 머신(`rcm worker`)이다. 레인은 한
머신이 잡을 동시에 돌리는 칸이다. claim 은 빈 레인이 대기 잡을 하나 집어 가는 일이고 heartbeat 는 워커가 살아 있다고
서버에 주기적으로 보내는 보고다. janitor 는 서버가 5초마다 돌리는 정리 루프다. `lost` 는 결과를 모르는 채로 닫힌 잡의
상태이고 `rcm wait` 는 이때 종료 3 을 낸다. 펜싱은 같은 이름의 새 세션이 등록했을 때 서버가 옛 세션의 요청을 409 로
거절하는 일이다. lease 는 잡과 그 잡을 claim 한 세션의 쌍이다. M5q 의 따라가기는 워커가 서버 버전에 맞춰 설치본으로 자신을
다시 실행(exec)하는 일이다.

### 3.1 서버 쪽 워커 프로토콜

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F1 | 워커 상태는 이름 단위 `workers.last_seen_at`(벽시계, 서버 수신 시각)로 판정. `now − last_seen_at > worker_timeout_seconds(60)` 이면 down | `remote_workers.py:279-281`, `store.py:340-348`, `config.py:132` | 레인 down 판정은 그대로 둔다. 유예 판정은 이것과 다른 시계(F31)로 |
| F2 | janitor 5초 루프가 `mark_lost_workers` 를 부르고, down 워커의 running·cancelling 잡을 **이름 단위로** 전부 `lost`(`worker_unreachable`, 인자 `seconds`) | `server.py:246`, `server.py:782-800`, `remote_workers.py:994-1017`, `store.py:4710-4746` | 60초 분단 = 잡 소멸. 결정 253 과 맞지 않는다 → 잡마다 유예로 바꾼다(결정 266) |
| F3 | `mark_lost_for_worker` 는 닫는 트랜잭션 안에서 `last_seen_at` 을 다시 읽지 않는다 | `store.py:4724-4741` | 만료와 재연결 경쟁(RL4) → 판정과 닫기를 같은 락 아래에서(결정 266) |
| F4 | 서버 재시작 정리는 로컬 잡만 `lost`. 원격 잡은 그대로 두고 heartbeat 시각으로 닫힌다는 docstring | `store.py:3878-3909`(`worker_name IS NULL` 3898) | 재기동 직후 첫 틱이 꺼져 있던 동안 낡은 `last_seen_at` 으로 원격 잡을 닫을 수 있다(RL7) → 유예 기준에 서버 기동 시각 바닥(결정 266) |
| F5 | `register` = 새 프로세스로 보고 그 이름의 활성 잡을 **먼저** `lost`(`worker_restarted_without_job`). 버전 검사는 그보다 앞(주석: 따라가기 전 409 가 도는 잡을 닫으면 안 된다) | `remote_workers.py:377-434`(버전 390-398, 닫기 400-411) | 같은 토큰 두 프로세스가 서로의 잡을 닫는다, 재시작한 워커의 등록이 보고보다 먼저 닫는다 → 세션 단위(결정 265) · 재시작 순서(결정 268). 버전 먼저는 유지(RL13) |
| F6 | heartbeat 의 「잊힌 잡」: 서버가 아는 그 이름의 활성 잡 중 워커 목록에 없고 직전 claim(`_fresh_claims`)도 아니면 `lost`. 이름 단위 | `remote_workers.py:916-942`, `:261-266` | 세션 단위로 바꾼다. v0.5.1 에서 이미 한 번 보고된 경쟁(CHANGELOG.md:580-584) → RL9 는 흔한 경우 |
| F7 | 살아 있는 워커가 취소를 확인 안 하면 `kill_at + 2 × heartbeat` 뒤 `cancelled`(`cancel_unconfirmed`). 닿지 않는 워커는 `lost` 가 이긴다. 판정은 이름 단위 `worker_alive` | `remote_workers.py:965-990`, `:997-999`, `tests/test_worker_lost.py:137-148` | 펜싱된 세션의 취소 중 잡을 같은 이름의 살아 있는 새 세션 때문에 `cancelled` 로 닫으면 그룹 자리가 풀린다 → 현재 · 살아 있는 세션의 잡에만(결정 266, RL33) |
| F8 | 보고 라우트는 `_owned_active`: 남의 잡 403, 끝난 잡 409(`error` 문구 + `state`) | `remote_workers.py:588-597` | 409 를 뜻별로 가를 기계 판독 칸이 없다 → `code`(결정 269) |
| F9 | `ApiError(status, message, **extra)` 의 extra 가 응답 JSON 키가 된다. 이미 쓰는 기계 판독 키 이름은 `code`(예 `database_busy`) | `server.py:441-451`, `remote_workers.py:567-573`, `server.py:4363` | 409 사유 키는 `code`. 3판의 `reason` 은 큐의 `reason`(PLAN `PLAN.md:129`)과 겹쳐 버린다 |
| F10 | claim 은 같은 트랜잭션에서 `(worker_name, lane)` 에 활성 잡이 있으면 `LaneBusy`(→ 409) | `store.py:3307-3315`, `remote_workers.py:560-561` | 펜싱된 세션의 잡이 레인을 잡고 있는 동안 새 세션의 claim 은 409 폭주 대신 204(결정 261) |
| F11 | 그룹 배제는 `BUSY_STATES`(running · cancelling) 잡으로 센다 | `store.py:3316-3323`, `core/model.py:37` | 잡이 `lost` 가 되는 순간 그룹 자리가 풀린다(3판 A-H7). 자리를 지키려면 잡을 busy 로 두거나 표식 잡은 서버가 닫기 전에 워커가 멈춘다(결정 261 · 320) |
| F12 | 로그 라우트는 받은 대로 `log.txt` 에 덧붙인다. 오프셋 · 중복 제거 없음. 잘린 마지막 줄 `_log_partial` 은 메모리뿐. 비밀 가리기는 요청 하나 안에서(주석: 두 요청에 걸친 값은 못 지움) | `remote_workers.py:630-659`(가리기 635-637, 조각 642-646) | 재전송이 중복을 만든다 → 원본 바이트 오프셋(결정 271) |
| F13 | 로그 본문은 `application/octet-stream`, 상한 4 MiB(`MAX_WORKER_LOG_BODY`) | `server.py:6262-6277`, `remote_workers.py:75` | 오프셋 · 세션은 본문이 아니라 헤더로. 헤더 관례 `X-RCM-*` 가 이미 있다(`X-RCM-Tree` `server.py:5903`, `X-RCM-Next-Offset` `client.py:859`) |
| F14 | 비밀 가리기 값은 서버의 비밀 폴더에서(`mask_for_preset`), `kind = "value"` 이고 8자 이상. 원격 워커의 `RunSpec.mask` 는 없음(서버가 가린다) | `server.py:3156-3162`, `release_secrets.py:183-198, 383-389`, `remote_worker.py:833-847` | 오프셋은 가리기 **전** 바이트로 센다(워커가 아는 단위). 파일 길이는 따로 적는다 |
| F15 | finish 는 이미 끝난 잡이면 409, 첫 보고가 이긴다. 워커가 보낸 `finished_at` 은 이상하면 수신 시각으로 물러서지만 미래 · `started_at` 이전은 막지 않는다 | `remote_workers.py:752-855`, `:769-771`, `tests/test_worker_lost.py:128-131` | 멱등은 유지. 시각은 `[started_at, 수신 시각]` 으로 자른다(결정 277, RL27) |
| F16 | finish 의 `summary_code` 는 `PREFLIGHT_CODES` + `failed` + `exit_code null` 일 때만. `lost` 보고는 워커 문장(`given`) 아니면 `worker_stopped_while_running` | `remote_workers.py:121-165`, `:776-778`, `core/outcome.py:238` | `lost` 용 코드 허용 목록이 필요(3판 M3) → `LOST_CODES`(결정 278) |
| F17 | 산출물 업로드는 `_owned_active`(끝난 잡 409), 같은 해시 재업로드는 200 멱등. finish 의 처분이 `ready` 인데 올라온 행이 없으면 워커 말을 그대로 적는다(파일이 없으면 내려받기 503 · 재시작 화해가 `unavailable`) | `remote_workers.py:696-750`(멱등 717-721), `:866-891` | 「번들이 서버에 있을 때만 ready」(RL22) → 처분 규칙(결정 275) |
| F18 | `/worker/spec` 은 워커 토큰이면 등록 전에도 읽힌다(부작용 없음) | `server.py:5806-5810`, `server.py:4353-4355` | 새 워커가 등록 **전에** 프로토콜을 확인하는 자리(결정 270, RL25) |
| F19 | `/worker/capabilities` 는 등록 확인(`_registered`) 뒤 보고를 받는다 | `server.py:4357-4366` | 세션을 싣고 펜싱 검사를 받는다(RL11) |
| F20 | `[server]` 절은 reload 대상이 아니다(`RELOADABLE_NOT`). 프리셋은 reload 로 바뀌고 claim 마다 새로 찾는다 | `server.py:5090-5130` | `disconnect_grace_seconds` 는 재시작으로만 바뀐다. 그래도 재시작을 넘는 잡이 있으니 유예 · 표식은 claim 때 잡 행에 얼린다(결정 257) |
| F21 | `forget_workers` 는 활성 잡이 있는 워커는 안 잊는다. 7일(`WORKER_FORGET_DAYS`) | `store.py:4659-4692`, `janitor.py:49, 759` | 세션 행은 워커 행과 함께만 지운다(결정 265) |
| F22 | 헬스는 down 레인이 있으면 `ok=false`(503) | `server.py:5395-5417` | 유예 동안 레인은 `down` 한 값으로 고정 — 깜빡이지 않는다(결정 276, RL28). 바꾸지 않는다 |

### 3.2 설정 · 프리셋 · 출력

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F23 | 모르는 프리셋 키는 기동 실패. `local_only` 는 bool 검증 | `config.py:632-661`, `:819`, `:930-937` | `irreversible` 도 같은 꼴(bool). 앱 프로젝트가 키를 다는 시점은 §2 |
| F24 | `local_only` 프리셋은 원격 워커가 받지 못한다(`_claimable` 의 `excluded`) | `store.py:3419-3423`, CHANGELOG `[Unreleased]` #266 | `irreversible` 은 원격에서 돌 수 있는 프리셋에만 뜻이 있다 |
| F25 | 이름 `grace_seconds` 는 이미 SIGTERM→SIGKILL 간격(서버 `[server] grace_seconds` 10, 워커 `grace_seconds`) | `config.py:104, 510, 1272-1280, 1845-1846`, `runner.py:121, 334` | 새 키는 `disconnect_grace_seconds`(3판 A-H2) |
| F26 | 「irreversible」 이라는 낱말은 이미 릴리스 쪽에서 「되돌릴 수 없는 입력 값」(`RELEASE_IRREVERSIBLE_MODE = {"upload": "upload", "review": "submit", "golive": "release"}`)과 웹 문구 `driver.item.irreversible` 로 쓰인다. 프리셋 키 `irreversible` 은 없다(grep 0) | `cli.py:3956, 4012-4017`, `web/i18n.js:1178, 2377` | 키 이름은 위임 결정 257 대로. 뜻이 「프리셋 전체」라 입력이 rehearsal 이어도 자기 멈춤이 걸린다(안전한 쪽) — §9 |
| F27 | `worker_timeout_seconds ≥ 10`, `heartbeat < timeout` 검증 | `config.py:1323-1328` | 새 키 검증도 같은 자리 · 같은 꼴(결정 263) |
| F28 | 요약 코드 표 `CODES` 와 웹 번역의 짝을 시험이 잠근다(로케일마다 정의된 `outcome.*`) | `core/outcome.py:147, 207-212`, `tests/test_outcome.py:543-569`, `web/i18n.js:770-779, 1970-1979` | 새 코드를 더하는 PR 이 번역도 더한다(PR1) |
| F29 | 행동 가능한 사유 목록 `ACTIONABLE_REASONS` 와 웹 `ACTIONABLE` 이 같은 목록이고 순서를 시험이 잠근다(잠근 시험 전부는 F58) | `core/model.py:165-176`, `src/remote_ci_monitor/web/app.js:20`, `tests/test_action_marker_m5s.py:139-141` | 새 사유를 더하면 두 목록과 F58 의 시험을 같이 고친다(PR3) |
| F30 | 도는 잡의 사유는 `_busy_reason`(cancelling → materializing → needs_action → stuck → overdue → quiet → running). 워커 생존을 보지 않는다. `compute_queue` 는 `workers`(down 포함)를 받고 down 레인은 살아 있는 레인에서 뺀다 | `core/queue.py:336-357`, `:423`, `:464`, `:634-635` | 유예 중 잡의 사유와 ETA null 은 `compute_queue` 안에서 그 잡의 워커가 down 인지로(결정 276) |

### 3.3 워커 쪽

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F31 | 서버에는 단조 시계 주입이 없다(`now_fn` 만). 주입형 `mono_fn` 은 스킬 템플릿에 선례가 있다 | `server.py:518-532`, `skills/rcm-ci-connect/templates/rcm_ci_watch.py:958, 989` | `App` 에 `mono_fn` 을 더한다(결정 266) |
| F32 | 워커 로그는 메모리 버퍼 4 MiB(`LOG_KEEP_BYTES`), 넘치면 오래된 쪽을 표시 없이 버린다. 503 아닌 오류면 조각을 버린다. 409 면 `closed_by_server` → `should_cancel` → 프로세스를 죽인다 | `remote_worker.py:67, 144-172`(409 157-160, 버림 161-163, 자름 166-167), `:187-188` | 디스크 스풀(결정 271). 409 를 뜻별로(결정 269). 일반 프리셋은 서버가 닫아도 죽이지 않는다(결정 256) |
| F33 | 보고는 1·2·4초 재시도 뒤 포기. finish 만 미뤄 두었다가 60초(`DRAIN_REPORT_SECONDS`) 뒤 버린다. phase 는 포기하면 끝 | `remote_worker.py:64, 316-341, 573-635, 74`, `:129-132` | 순서 있는 보고 큐, 잡이 도는 동안 횟수 상한 없음, 끝난 뒤 24시간(결정 264 · 272) |
| F34 | 러너는 관찰자에게 **줄 끝(`\n`)으로 끝나는 묶음만** 준다. 개행 없는 줄은 `MAX_LINE_BYTES`(64 KiB) 넘으면 잘라 `\n` 을 붙여 내보내고, EOF 의 남은 조각에도 `\n` 을 붙인다 | `runner.py:36-38, 345-356, 379-385` | 3판의 `log.pending`(줄 끝 없는 꼬리)은 필요 없다. 조각을 줄 경계에서 자르면 비밀 값이 두 요청에 걸치지 않는다(RL17 · RL18) |
| F35 | (실측) 위 F34 를 실제로 돌려 확인: 개행 없이 100,000 바이트 + `\r`×10 + 꼬리 15 바이트를 쓰는 프로세스 → 관찰자 묶음 1개 100,026 바이트, `\n` 으로 끝남, 워커 로컬 `log.txt` 와 바이트 같음. 한 줄의 최대는 `MAX_LINE_BYTES + READ_CHUNK`(128 KiB) | 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS)에서 `runner.run_job` 에 기록용 관찰자를 붙여 실행(스크래치 스크립트, 레포 밖) | 워커 조각 상한 256 KiB(`LOG_BATCH_BYTES`, `remote_worker.py:66`) 안에 한 줄이 늘 들어간다 |
| F36 | 워커 로컬 `jobs/<id>/log.txt` 는 러너가 쓴 줄 + git_ref 준비의 `[git] …` 줄(서버로 안 감). 거꾸로 준비 실패의 `[rcm] <log>` 줄은 관찰자로만 간다 | `remote_worker.py:872-890`, `:962-963` | 로컬 `log.txt` ≠ 서버로 가는 스트림 → 스트림만 담는 별도 스풀 파일(결정 271) |
| F37 | 스냅샷 내려받기의 연결 실패(status 0)는 `snapshot_download_failed` → `failed`(종료 1) | `remote_worker.py:892-899`, `client.py:126-167`(`.part` 는 실패 때 지움) | 위임 결정 259: 연결 실패는 `lost`(`snapshot_unreachable`), 확정 결함은 `failed` 그대로 |
| F38 | heartbeat 실패는 경고만, 잡은 계속. 레인 claim 이 409 `not registered` 면 그 자리에서 재등록 → 오늘은 재등록이 그 이름의 도는 잡을 닫는다 | `remote_worker.py:418-464`, `:771-795` | 같은 세션 id 재등록은 멱등(결정 260) |
| F39 | 워커 프로세스 종료 코드: 0 평범한 멈춤, 2 등록이 막힘(감독자가 다시 띄우게), 1 exec 실패. 서비스 파일은 launchd `KeepAlive.SuccessfulExit=false` · `ThrottleInterval 10`, systemd `Restart=on-failure` · `RestartSec=5` | `remote_worker.py:785-794`, `cli.py:4576-4579, 3507-3508, 3535-3536` | 펜싱 0(되살리지 않음), 프로토콜 불일치 3(되살림) — 결정 258 |
| F40 | 옛 워커(이 명세 이전 코드)의 반응: 등록 409 + 같은 `server_version` → 종료 2; claim 409(`not registered` 아닌 것) → 로그 한 줄 + 2~4초 뒤 다시(`_backoff(RETRY_WAIT_SECONDS)`); heartbeat 409 → 3·12·60번째에만 로그 | `remote_worker.py:361-364, 381-394, 796-800, 63, 749-757, 424-431` | 옛 워커를 고칠 수 없다. 서버가 409 `protocol_required` 를 주면 옛 워커는 잡을 집지 못하고, 다시 시작하면 종료 2 → 감독자가 5~10초 간격으로 되살린다. RL24 의 「5초 이상」은 이 사실로 고쳐 적는다(§12) |
| F41 | 시작 청소는 `workspaces/` · `jobs/` · `artifacts/` 의 7일(`WORKSPACE_KEEP_DAYS`) 지난 항목을 mtime 으로 지운다 | `remote_worker.py:68, 1096-1111` | 세션 폴더도 같은 7일. 단 잠금을 쥔(살아 있는) 세션 폴더는 건너뛴다 |
| F42 | M5q 드레인: 모든 레인이 잡 없이 기다리면 같은 PID 로 설치본을 exec. 못 보고한 잡은 60초까지 다시 보고하고 그래도 안 되면 「restarting anyway」 | `remote_worker.py:558-635`, `cli.py:4580-4614` | 보고 큐가 디스크에 있으니 exec 뒤 새 이미지가 이어 보낸다(결정 268, RL12 b) |
| F43 | (실측) `fcntl.flock` 을 쥔 파일은 Python 이 기본으로 상속 안 함(PEP 446)이라 `os.execv` 뒤 새 이미지에서 같은 파일을 바로 잠글 수 있었다 | 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS)에서 잠금 → execv → 자식이 `LOCK_EX \| LOCK_NB` 성공 | 세션 폴더 잠금으로 「그 세션 프로세스가 살아 있나」를 판정할 수 있다. exec 한 새 이미지는 옛 세션을 「죽음」으로 본다(맞다) |
| F44 | (실측) `ps -o lstart= -p <pid>` 가 macOS 에서 `Thu Nov 26 01:49:14 2026` 꼴 한 줄. Linux 는 `—`(측정 안 함. 측정 수단: ubuntu 머신에서 같은 명령, rcm-ci 는 맥미니뿐) | 측정: 이 셸에서 실행 | 고아 프로세스 그룹의 신원 확인(pid 재사용 방지, RL15 · RL31) |
| F45 | runner 는 `start_new_session=True` → 잡 프로세스 그룹 id = 자식 pid. 신호는 `os.killpg` | `runner.py:289-297, 231-241` | 고아 정리는 pgid 로. 관찰자 규약 `RunObserver` 에 pid 를 알리는 갈래가 없다 → 더한다(결정 268) |
| F46 | `rcm worker --check` 는 health · whoami · spec 만 부르고 등록 · capabilities 를 안 한다(주석 「등록도 capabilities 도 없다」) | `cli.py:4645-4700`, `cli.py:4703-4715` | 세션을 만들지 않는다 → 도는 서비스 워커를 펜싱하지 않는다(RL12 c). `rcm doctor` 는 dev 에 머지됐고(#268, F54) 「서버에 읽기 요청만 보내고 DB 를 열지 않는다」(origin/dev `CHANGELOG.md:40`) — 같은 규칙, RL12 c 에 넣는다 |

### 3.4 시험 · 문서에 걸린 것

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F47 | 오늘 계약을 잠근 시험: 60초 뒤 `lost`(`test_worker_past_timeout_loses_its_running_job_and_shows_down`), 돌아온 heartbeat 가 lost 잡을 안 살림, 닿지 않는 워커의 cancelling 은 lost, kill -9 뒤 timeout 에 lost · 새 워커가 이어 받음 | `tests/test_worker_lost.py:58-148`, `tests/test_e2e_worker.py:775-805` | 결정 253 이 바꾼 계약이라 기대값을 유예 기준으로 고쳐 쓴다(약화 아님, §7.3) |
| F48 | 서버 재시작 e2e(원격 잡이 running 으로 남고 finish 로 succeeded), SIGTERM e2e(`worker stopped`, 종료 0) | `tests/test_e2e_worker.py:810-829, 857-880` | 유지 — 회귀 잠금 |
| F49 | 문서가 「60초 뒤 lost」 를 말한다 | `docs/configuration.md:1185, 1386`, `docs/operating.md:786`, `templates/server.toml:42` = `examples/server.toml:42`(바이트 같음을 `tests/test_examples.py` 가 잠금) | PR1 이 고친다 |
| F50 | `DB_VERSION = 25`, 마이그레이션 전 백업(결정 74), 더 새 DB 는 거절 | `store.py:88, 480, 661-677, 1208-1248, 778-785` | v26 은 PR1 하나가 올린다. M5t-c 는 v27 |
| F51 | 높은 엔트로피 문자열 리터럴은 gitleaks `secrets` 잡을 빨갛게 하고 나중에 고쳐도 커밋 이력에 남는다 | `AGENTS.md:137-139` | 시험의 세션 id 는 실행 중 만든다(리터럴 32-hex 금지) — §8 |

### 3.5 2판에서 더한 사실

1라운드 지적을 확인하면서 새로 읽은 사실이다.

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F52 | M5t-c 명세 PR #269 의 결정 301~303: 301 짝짓기 요청은 코드 대신 nonce + HMAC, 302 복제된 worker.toml 은 로컬 침해로 남는 위험, **303 확실한 신원 불일치(MAC 틀림 · 서버가 짝지은 적 없다고 답함)는 종료 0 + 「다시 짝지으라」(서비스가 되살리지 않음), 연결 실패 · 5xx · 503 은 신원 판단이 아니라 프로세스 안에서 물러서며 다시 시도(종료 안 함), 프로토콜 불일치는 M5t-b 대로 3(되살림), 부모 명세 M8 은 「펜싱 · 확실한 신원 거절」로 좁힌다**. 그 명세의 `pair_proof` 검사 자리는 「버전 409 다음, 풀 검사 · `mark_lost_for_worker` 앞」 | PR #269 head `fb73ed67b1ad1ddb9af805d0d388008867cd7c9c` 의 `docs/m5tc-workplan.md:216` · 결정 287(`:523`) · 결정 295(`:531`), 결정 301~303 은 PR #269 의 위임 결정 3차 표(레포 밖 handoff, 메인 세션 기록) | 1판 §4.7 · §9 의 「M5t-c 도 종료 3(서버 신원 미확인)」은 틀린 말이 됐다 → 고침. register 순서에서 `pair_proof` 는 버전(2) · 프로토콜(3) 검사 뒤, 펜싱된 세션 검사 · 부작용(세션 넣기 · 펜싱 · 능력 지우기 · 호스트 사실 교체 · 잡 닫기) 앞(§4.2, V2-14) |
| F53 | 1판의 자기 멈춤 판정 자리인 heartbeat 루프는 `stopping.wait(H)` 뒤 `heartbeat_once` 를 부른다. heartbeat 요청 시한은 `WorkerClient` 기본 15초. 성공한 heartbeat 은 그 자리에서 `_retry_unreported()` 를 동기로 부르고 그것은 보고마다 1·2·4초 재시도 + 요청 시한을 쓴다 | `remote_worker.py:740-745`, `client.py:83, 118-123`, `remote_worker.py:447, 596`, `:316-341` | 끊긴 동안 판정 한 바퀴가 H + 15초, 서버가 돌아온 첫 회차는 못 한 보고 재시도 뒤로 몇 분까지 밀린다 → 1판 m(H 마다 판정 가정)이 깨진다(V1-1 · V2-1). 판정을 heartbeat 에서 떼어 감시 스레드로(결정 320) |
| F54 | origin/dev 가 `96a8bd25fa0b0457f6bc75a336c43aeb2a4724ec` 로 갔다(M5t-a PR2 #268 `rcm doctor` 머지). 그 diff 는 register 본문에 `host` 를 더하고 등록이 호스트 사실을 바꾼다(`remember_host(…, replace=True)`), `client.py` register 서명, `remote_worker.py`(+26) · `runner.py`(+100) · `config.py` · `capabilities.py` · `cli.py` · `server.py` 를 바꿨다. `DB_VERSION` 은 25 그대로. M5t-a 의 남은 PR3~PR7 은 `config.py` · `capabilities.py`(`@local`) · 서버 헬스 · 갈 곳 잃은 잡 reason · render · web(PR3), `cli.py` setup · guard(PR4), wait 사람용 줄 · `ran_on`(PR5), `docs/configuration.md` · `docs/operating.md` · README(PR6), 실기(PR7)를 고친다 | `git diff --stat fdf196d…96a8bd2 -- src`, origin/dev `remote_workers.py:424-425`, origin/dev `store.py:88`, `docs/m5t-workplan.md:690-697` | 구현 PR1 의 시작 조건을 「M5t-a PR1~PR7 전체 머지(v0.11.0 에 들 내용 전부)」로(V1-6). 이 명세의 `file:line` 은 고정 sha 기준이고 구현 PR 이 dev 에서 다시 찾는다. 호스트 사실 교체도 register 의 부작용 목록에 든다(§4.2) |
| F55 | 요청 시한: heartbeat · phase · finish · capabilities 15초(`_post` 기본), claim 은 `15 + wait_seconds + 5`(wait 기본 20, 상한 60), log `max(15, 60)` = 60, 스냅샷 내려받기 ≥ 60, 산출물 PUT ≥ 300 | `client.py:83, 89-93, 110-115, 172-181, 140, 211-225`, `config.py:134, 1351-1352` | 응답은 보낸 뒤 최대 80초(claim) · 300초(산출물)에 올 수 있다 → 워커의 `ok_at` 은 응답 받은 시각이 아니라 **그 요청을 보낸 시각**(결정 320). m 의 요청 시한 항은 heartbeat 의 15초 |
| F56 | (실측) 러너의 멈춤 시간. 러너는 `POLL_SECONDS` 1 마다 `should_cancel` 을 보고 SIGTERM, SIGTERM 뒤 `grace_seconds` 가 지난 첫 점검에 SIGKILL 한다(점검은 묶음을 1초까지 기다린 뒤 직전 점검에서 1초가 지났을 때) — 이론 상한은 플래그→SIGTERM ≤ 2초, SIGTERM→SIGKILL ≤ grace + 2초. 측정: SIGTERM 을 받는 `sh -c 'sleep 60'` 은 플래그 뒤 0.02~0.03초에 죽음, SIGTERM 을 무시하는 조용한 자식(grace 3) 3.04~3.06초, SIGTERM 을 무시하고 50ms 마다 찍는 자식(grace 3) 3.25~3.34초 | `runner.py:37, 334-376`, 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS)에서 `run_job` 에 「3초 뒤 should_cancel 참」 관찰자를 붙여 각 3회(스크래치 스크립트, 레포 밖) | 「플래그 → 그룹 소멸」 ≤ grace + 4초(이론), 측정 ≤ grace + 0.34초. 여유 `SELF_STOP_SLACK_SECONDS` 10 = 감시 틱 1 + 러너 점검 4 + 남김 5(결정 320) |
| F57 | 오늘 있는 잠금은 `_remote_lock`(`_log_partial` · 표본) · `_fresh_claims_lock` 둘. 로그 라우트는 쓰기와 마커 커밋을 묶는 잠금 없이 `log.txt` 에 덧붙인다(덧붙임 `:640-641`, `_remote_lock` 은 `_log_partial` 만 `:642-646`, 마커 트랜잭션 `:652`). `_session_lock` 은 없다(grep 0). `mark_lost_workers` 도 잠금 없이 판정 · 닫기. 잠금 주인은 믹스인 `RemoteWorkersMixin`(`_remote_init`), `App` 이 기동 때 부르고 janitor 는 `App._janitor_loop`. DB 는 `busy_timeout=5000`, 바쁨은 503 `database_busy` | `remote_workers.py:236, 256-266, 630-659, 994-1017, 562-573`, `server.py:614, 782-800`, `store.py:1191` | 같은 잡의 두 요청(서버에서 아직 도는 시한 초과 요청 + 재시도)이 같은 `log_offset` 을 읽고 둘 다 붙인다 · 커밋 실패 뒤 재시도가 같은 바이트를 또 붙인다(V2-2) → 잡별 로그 잠금(결정 323). 1판이 「같은 락(`_session_lock`)」이라 쓴 것은 새 잠금 `_heard_lock` 으로 이름 · 범위 · 주인을 정한다(결정 328, V2-5) |
| F58 | 계약을 잠근 시험(1판 F29 · PR3 owns 가 빠뜨린 것 포함): `ACTIONABLE_REASONS` 튜플 `tests/test_queue_m5n.py:135` · `tests/test_render_m5n.py:199` · `tests/test_action_marker_m5s.py:139-141`, 웹 = 모델 `tests/test_web_schedule_m5s.py:43`, node `tests/web/stall.test.js:192-194` · `tests/web/m5n.test.js:38-41`. 최근 행 키 정확한 집합 `tests/test_status_schema.py:91`(대조 `:303`), 더한 키 허용 목록 `tests/test_status_outcome.py:162`(대조 `:366-368`). register 응답 정확한 dict `tests/test_worker_api.py:542-549` · `tests/test_worker_client.py:183-190`. heartbeat 응답 정확한 dict `tests/test_worker_api.py:1204`(부분 집합 `tests/test_server_m5q_worker.py:90`). claim 최상위 키 `tests/test_worker_api.py:667` · `tests/test_worker_client.py:219-220`(새 값은 `job` 안이라 안 바뀜). `[server]` 워커 키 `tests/test_config.py:905`. 400 응답은 `{"error"}` 하나(`tests/test_worker_api.py:618`) | 각 줄 | §7.3 과 §5 shared 에 전부 넣는다(V1-7). `log_complete` 를 최근 행에 더하는 PR2 가 F58 의 두 최근 행 잠금을, `closed_jobs` · `worker_protocol` 을 더하는 PR1 이 heartbeat · register 잠금을 고친다 |
| F59 | 프로세스 신원: Linux 는 `/proc/<pid>/stat` 22번째 칸 `starttime`(부팅 뒤 clock tick, proc(5)). 이 머신에서 재지 않았다(`—`, 측정 수단: Linux 머신에서 같은 읽기 — rcm-ci 는 맥미니뿐). macOS 는 `ps -o lstart=`(F44 실측). 프로세스 그룹 id 는 그룹이 살아 있는 동안 다시 쓰이지 않지만(POSIX), 리더(pid = pgid)가 죽고 멤버만 살면 `ps -p <pgid>` · `/proc/<pgid>` 는 아무것도 못 찾고 `killpg(pgid, 0)` 은 성공한다 | proc(5), F44, F45 | 리더가 살아 있고 시작 시각이 같으면 「같은 그룹」, 리더가 없고 그룹이 살면 「모름」(V2-7) → 재확인 · 경고(결정 327) |
| F60 | events 의 kind 는 오늘 `state` · `marker` 둘이고 읽는 곳은 전이 목록 · 마커뿐이다. 임의의 kind 를 사람에게 보이는 길이 없다 | `store.py:132-133, 1324, 3968-4009` | D-g 의 「경고를 사건으로」는 `lost` 요약 코드 `orphan_unverified` 로 낸다 — 요약 코드는 `rcm wait` · 웹 · `[[notify]]` 가 번역해 보인다(F28). 새 events kind 는 만들지 않는다(결정 327) |
| F61 | heartbeat 응답은 오늘 `{cancel, paused, timeout_seconds, generation, server_version}`. `cancel` 은 그 이름의 활성 잡 중 CANCELLING 만이라 취소 중이던 잡이 `lost` 로 닫히면 목록에서 빠진다. 잡 행의 `cancel_requested_at` 은 닫힌 뒤에도 남는다 | `remote_workers.py:952-962`, `store.py:203, 1329-1334` | 다시 붙은 워커가 그 잡을 계속 돌린다(V1-4) → heartbeat 응답 `closed_jobs` 에 `had_cancel` 을 싣는다(결정 321 · 322) |
| F62 | 워커는 `heartbeat_seconds` · `worker_timeout_seconds` 를 register 응답에서, `timeout_seconds` 를 heartbeat 응답에서 받는다 | `remote_workers.py:426-434, 957` | 워커가 하한 L 을 계산할 수 있다(결정 320) |
| F63 | 서버의 묶음 파일은 `artifacts_dir(job)/bundle.tar`. 재시작 화해도 같은 파일 존재를 본다. finish 처분 `ready` 는 오늘 행이 없으면 워커 말을 그대로 적는다 | `server.py:3166, 3294`, `store.py:2107`, `remote_workers.py:866-891` | `ready` 는 행 · 해시 · 파일 셋이 맞을 때만(V1-12, 결정 275 고침) |
| F64 | `GET /api/whoami` 는 워커 토큰에도 `{name, admin, kind}` 를 준다(부작용 없음) | `server.py:5767-5770`, F46 | 등록 전에 토큰 이름을 알아 세션 폴더 주인을 적는다(결정 325) |

## 4. 설계

첫 절은 상태 전이 표다(결정 252). 뒤의 절들은 이 표의 칸을 장치별로 풀어 쓴다. 2판에서 더한 두 단계 점검표는 §4.1.4 에
있다.

### 4.1 상태 전이 표와 타이머

이 절을 가장 먼저 쓴다. 나머지 설계는 이 표에서 나온다. 아래 용어는 이 절에서만 쓴다.

- 세션 S 는 워커 프로세스 한 번이다(결정 265). 잡 J 는 claim 때 S 와 묶인다(`jobs.worker_session_id`). lease 는 (J, S) 쌍이고
  따로 lease id 를 두지 않는다(결정 265).
- 잡 J 의 얼린 값은 `G_J`(`jobs.disconnect_grace_seconds`)와 `X_J`(`jobs.irreversible`)다(결정 257).
- 서버의 들음 시각 `heard(S)` 는 서버가 S 의 인증된 현재 세션 요청을 받은 단조 시각이다. 요청 처리를 시작할 때
  `_heard_lock` 안에서 갱신한다(결정 328). 메모리에만 두고 바닥은 서버 기동 단조 시각이다(결정 266). 펜싱된 세션이나 표에
  없는 세션의 요청은 `heard` 를 올리지 않는다.
- 서버 경과는 `gap(J) = mono_now − max(heard(S_J), 기동)` 이다. 「들림」은 `gap ≤ T`, 「조용함」은 `gap > T` 이고 T 는
  `worker_timeout_seconds` 다. 둘 다 단조 시계로 잰다. 레인 `down` 표시는 오늘처럼 벽시계 `workers.last_seen_at`(F1)으로
  정하므로, 서버 벽시계가 뛰면 둘이 잠깐 다를 수 있다(V1-11).
- 워커의 `ok_at(S)` 는 S 의 요청 가운데 2xx 를 받은 마지막 요청을 보낸 시각이고 벽시계와 단조 시계로 둘 다 적는다. 요청은
  register, claim, heartbeat, phase, log, 산출물, finish, capabilities 어느 것이든 된다. 잡 J 의 첫 값은 J 를 돌려준 claim
  요청을 보낸 시각이며 claim 응답 시각보다 이르거나 같다(F55). 서버는 그 요청을 받은 뒤에 `heard` 를 올리므로 늘
  `ok_at ≤ heard` 다.
- 워커 경과 `wgap` 은 `ok_at` 부터 잰 벽시계 경과와 단조 경과 가운데 큰 쪽이다.
- `T_req` 는 heartbeat 요청 시한 15초다(F55). `SELF_STOP_SLACK_SECONDS` 는 10초이고 감시 틱 1초, 러너 점검 4초, 남김 5초를
  더한 값이다(F56). `grace_J` 는 J 를 멈출 때 SIGTERM 과 SIGKILL 사이에 기다리는 시간이다. 워커 `grace_seconds` 에 §4.3 의
  상한을 적용한다.
- 여유는 `m_J = T_req + grace_J + SELF_STOP_SLACK_SECONDS` 이고 기본값은 15 + 10 + 10 = 35초다(결정 320).
- 하한은 `L = max(T, 2 × H + T_req)` 이고 H 는 `worker_heartbeat_seconds` 다. 기본값으로는 max(60, 25) = 60 이다. 설정
  검증과 grace 상한이 늘 `G_J − m_J ≥ L` 을 보장한다(§4.3).
- 네트워크 상태는 셋이다. N 은 정상이다. D 는 서버 정지로 연결이 거부된다. P 는 분단으로, 서버는 살아 있는데 워커 요청에만
  응답이 없다.

#### 4.1.1 서버가 보는 전이 (세션 상태 × 잡 상태 × 네트워크)

| # | 세션 | 잡(서버 DB) | N 정상 | D 서버 정지 | P 분단 | 행 |
|---|---|---|---|---|---|---|
| T1 | 현재 · 들림(gap ≤ T) | running(executing) | heartbeat · phase · log · finish 받음. finish 가 상태를 정함(첫 보고가 이김). heartbeat 응답에 그 세션의 닫힌 잡 `closed_jobs`(결정 321) | 판정 없음(서버가 없다). 워커는 보고를 큐에 쌓고 잡은 계속. 서버가 뜨면 `heard` 바닥 = 기동 시각 | 서버: `heard` 가 멈춤 → T 뒤 T2 | RL6 RL7 RL16 |
| T2 | 현재 · 조용함(T < gap ≤ G_J) | running | (다시 들리면 T1, 같은 세션 · 같은 lease 로 이어 보고, 프로세스 안 죽음) | 기동 뒤 T 안에 heartbeat 가 오면 T1 | 잡은 `running` 유지, 사유 `worker_unreachable_grace`, ETA null, 레인 `down`(헬스 503 그대로). 서버는 닫지 않는다 | RL1 RL5 RL6 RL26 RL28 |
| T3 | 현재 · 조용함(gap > G_J) | running | — | 서버가 꺼져 있던 시간은 세지 않는다(바닥 = 기동, 2차 Q-C). 기동 + G_J 에 판정 | `_heard_lock` 안에서 판정 · 조건부 닫기 → `lost`(`worker_unreachable`, `seconds` = int(gap)), 그룹 자리 풀림, `job_finished` 1회. 그 뒤 같은 세션의 finish → `late_result` 만(결정 256). 다시 붙으면 heartbeat `closed_jobs` 로 알림 | RL1 RL2 RL3 RL4 RL5 RL7 RL35 |
| T4 | 현재 · 들림 | cancelling | heartbeat `cancel` → 워커가 끝냄 → `cancelled`(supersede 면 `superseded by #N` 유지). 워커가 확인 안 하면 `kill_at + 2H` 에 `cancelled`(`cancel_unconfirmed`) — 현재 · 들리는 세션의 잡에만 | 기동 뒤 heartbeat 가 오면 `cancel` 전달 | 조용해지면 `kill_at` 규칙 멈춤. gap > G_J 면 `lost`(`worker_unreachable`). `cancelled`(2)로 위장하지 않는다. 다시 붙은 워커는 `closed_jobs` 의 `had_cancel: true` 로 그 잡을 멈춘다(결정 322) | RL8 RL33 |
| T5 | 현재 · 들림 | running · cancelling, 워커가 모름(claim 응답 유실) | 다음 heartbeat 한 번 봐줌(`_fresh_claims`), 그다음 heartbeat 에 목록에 없으면 `lost`(`worker_restarted_without_job`) — busy 잡 전부(`store.py:4702-4708`, V1-11) | 해당 없음 | 재연결 뒤 같은 규칙. 끝내 안 오면 T3 | RL9 |
| T6 | 현재 | running(materializing) | 정상 진행 | 워커: 스냅샷 내려받기 연결 실패 → finish `lost`(`snapshot_unreachable`) 큐 → 서버가 뜨면 전달 | 같음. 전달 전 gap > G_J 면 T3, 늦은 finish 는 `late_result` | RL23 |
| T7 | 현재 | running, 워커는 끝냄(보고 큐에 finish) | 큐 순서대로 phase → 로그 → 산출물 → finish. 확정. 로그 · 산출물 단계가 5xx 를 300초 넘게 받으면 finish 먼저(결정 324) | 서버가 뜨면 이어서 | 재연결이 G_J 안이면 실제 결과, 밖이면 T3 뒤 `late_result` | RL14 RL19 RL21 RL22 RL40 |
| T8 | 펜싱됨(같은 이름의 새 세션 S′ 가 등록) | running · cancelling | 옛 프로세스의 다음 요청(무엇이든) → 409 `session_fenced` → 그룹 멈춤 → 좁은 확인 finish(`lost` + `worker_replaced`) 만 받음 → 잡 `lost`. 옛 프로세스 종료 0. 확인 전까지 잡은 busy(그룹 · 레인 잡힘), 새 세션의 그 레인 claim 은 204 | 판정 없음(펜싱은 DB 에 남는다) | 확인이 없으면 `heard(S) + G_J` 에 `lost`(`worker_unreachable`). S 가 `unverified_sessions` 로 알려졌고 X_J 면 요약 코드 `orphan_unverified`(결정 327). 그 전에는 안 푼다(결정 261) | RL10 RL11 RL31 |
| T9 | 이전 세션 D(새 프로세스가 D 의 폴더 잠금으로 죽었음을 확인, 폴더 주인이 같음) | running · cancelling | 새 프로세스가 등록 **전에** D 의 고아 그룹 정리 → D 의 남은 큐를 D 의 id 로 보냄(응답은 그 큐에만, 결정 326) → 등록 `previous_sessions`(신원 확인된 D) · `unverified_sessions`(모름인 D) → 서버가 `previous_sessions` 의 남은 busy 잡을 `lost`(`worker_restarted_without_job`)로 닫고 펜싱 | 등록이 5초마다 다시 시도 | 같음 | RL12 RL14 RL15 RL31 RL38 RL39 |
| T10 | 같은 세션 id 의 재등록(레인이 `not_registered` 409 를 받음) | running | 멱등: 펜싱 없음 · 잡 안 닫음 | — | — | RL12 |
| T11 | 레거시(세션 칸 NULL — v26 이전에 claim 된 잡) | running · cancelling | 세션 없는 heartbeat · phase · log · finish 를 **그 이름에 레거시 busy 잡이 있을 때만** 받음(이름 단위 「잊힌 잡」 규칙도 그 잡에만). 새 claim 은 409 `protocol_required` | 기동 바닥 | `heard(이름) + 서버 기본 G` 에 `lost`(`worker_unreachable`). 레거시 잡이 다 끝나면 호환은 저절로 거둔다 | RL24 |
| T12 | 세션 없는 요청(옛 워커), 레거시 잡 없음 | — | register · claim · heartbeat · capabilities → 409 `protocol_required`(+`worker_protocol: 2`, `error` 에 「not registered」 낱말 없음 — F40 · 결정 329). 등록 409 는 버전 검사 **뒤**(버전이 다르면 오늘의 409 + `server_version`) | — | — | RL13 RL24 |
| T13 | 새 워커 → 프로토콜 1 서버 | — | `/worker/spec` 에 `worker_protocol` 이 없고 서버 버전이 같으면 등록하지 않고 종료 3(세션 폴더도 안 만듦). 버전이 다르면 오늘처럼 등록 409 → 따라가기(M5q) 또는 종료 2 | 등록 전 연결 실패는 오늘처럼 5초 재시도 | — | RL25 RL32 |
| T14 | 끝난 잡(J terminal)에 늦은 보고 | terminal | 같은 세션의 finish 이고 J 가 `lost` → 한 트랜잭션에서 `late_result` 1건(없을 때만) + `log_complete` 쓰기, 409 `job_closed`(+`state`, `late_result_recorded`). log · phase · 산출물 → 409 `job_closed`, 반영 0. 펜싱된 세션 → 409 `session_fenced`(기록 없음, 2차 Q-B) | — | — | RL11 RL21 |
| T15 | 현재 | running · cancelling, 로그 요청이 서버에서 실패 | 파일 쓰기 OSError(ENOSPC 등) → 503 `log_write_failed`, 커밋이 바쁨 → 503 `database_busy`, 커밋 0행(잡이 그새 닫힘 · 펜싱) → 409 `job_closed` · `session_fenced`. 셋 다 `log.txt` 를 `log_file_bytes` 로 되자르고 `log_offset` 은 그대로. 워커는 같은 오프셋부터 다시(같은 잡의 동시 요청은 잡별 잠금으로 줄 섬, 결정 323) | — | — | RL36 |
| T16 | 현재 | running · cancelling, 서버의 `log.txt` 가 `log_file_bytes` 보다 짧음(OS 충돌, fsync 없음) | 다음 쓰기(또는 기동 정리)가 알아챔 → `jobs.log_complete = 0` 고정 + 서버 로그 한 줄(「log gap」), 쓰기는 파일 끝에서 이어감(빈 바이트 구멍 없음) | — | — | RL37 |

#### 4.1.2 워커가 보는 전이 (워커 프로세스 한 개, 잡 하나)

| # | 워커 상태 | 표식 잡(X_J) | 일반 잡 | 행 |
|---|---|---|---|---|
| W1 | 연결됨(2xx) | 그대로. `ok_at` 갱신 | 그대로 | RL6 |
| W2 | 끊김, `wgap < G_J − m_J` | 계속 돌고 보고는 큐에 | 계속 | RL1 RL5 RL6 |
| W3 | 끊김, `wgap ≥ G_J − m_J` — **감시 스레드**의 1초 틱 판정(heartbeat 루프와 무관, 잠에서 깬 첫 틱 포함) | 그룹 SIGTERM → `grace_J` → SIGKILL, finish `lost`(`worker_self_stopped`, `seconds` = int(wgap)) 큐. 한 번 정하면 되돌리지 않는다 | 계속(결정 253) | RL1 RL2 RL4 RL26 RL34 |
| W4 | 서버가 닫은 잡을 앎: 보고의 409 `job_closed`(+`state`) 또는 heartbeat 2xx 의 `closed_jobs` `{id, state, had_cancel}` | `state` 가 무엇이든 멈춤 | `state` 가 `cancelled` · `timed_out` 이면 멈춤. `lost` + `had_cancel: true` 면 멈춤(결정 322). `lost` + 취소 없음이면 계속 돌고 그 잡의 로그 전송만 멈춤(로컬 스풀은 남김). 끝나면 finish → 서버가 `late_result` | RL5 RL8 RL21 RL35 |
| W5 | 살아 있는 이 프로세스의 요청이 409 `session_fenced` | 모든 잡 멈춤 → 잡마다 좁은 확인 finish → 종료 0 | 같음(표식 무관, 결정 255 · 261) | RL10 RL11 |
| W6 | 등록 전 409 `protocol_required` | 종료 3 | 같음 | RL25 |
| W7 | 프로세스가 `kill -9` 로 죽음 | 잡 그룹은 고아로 남음 → 다음 프로세스가 시작 때 정리(T9) | 같음 | RL14 RL15 RL31 |
| W8 | SIGTERM/SIGINT | 오늘처럼 멈춤, finish `lost`(`worker stopped` 문장) 큐 → 종료 전 한 번 보내고 못 보내면 디스크에 남김 → 다음 시작이 보냄 | 같음 | RL14 |
| W9 | 시작 때 다른 세션 폴더의 주인(`owner.json`)이 나와 다르거나 없음 | 건드리지 않음: 잠가 보기 · 고아 정리 · 큐 보내기 · 지우기 모두 안 함(7일 지나고 잠글 수 있을 때만 청소, 결정 325) | 같음 | RL38 |
| W10 | 죽은 세션 D 의 큐를 D 의 id 로 보내다 받은 응답 | §4.4 의 표대로 **그 큐에만** 적용. 이 프로세스를 멈추거나 D 의 id 로 재등록하지 않는다(결정 326) | 같음 | RL39 |
| W11 | 잡이 끝난 뒤 로그 · 산출물 단계가 HTTP 5xx 를 `FINISH_HOLD_SECONDS`(300) 넘게 받음 | finish 먼저(산출물 처분 `failed` + `upload_failed`, `log_complete` 는 서버가 `log_offset < log_bytes` 로 false), 2xx 뒤 남은 로그 · 산출물은 버림(결정 324) | 같음 | RL40 |
| W12 | 죽은 세션 D 의 고아 신원이 「모름」(§4.4 step 6) | `D.ok_at + G_J − m_J` 에 다시 확인 → 확인되면(같은 그룹이면 멈춤, 없거나 다른 프로세스면 그대로) D 의 id 로 좁은 확인 finish. 그래도 모름이면 아무것도 안 함 → 서버가 유예 만료에 `orphan_unverified` 로 닫음(결정 327) | 고아 신원 확인 자체는 같다. 일반 잡이면 서버가 닫을 때 요약 코드는 `worker_unreachable`(일반 잡은 두 번 돌아도 된다, 결정 253) | RL31 |
| (지움) | 1판 W6 의 「도는 중 409 `protocol_required` → 모든 잡 멈춤 → 종료 3」 | 닿지 않는다: 새 서버만 `protocol_required` 를 내고, 서버를 옛 빌드로 내리면 옛 빌드가 v26 DB 를 열지 않는다(F50). 닿는다면 일반 잡을 죽여 결정 253 에 어긋난다(V2-9). 도는 중 받으면 그 요청 하나의 실패로 로그만 남기고 잡은 건드리지 않는다 | | |

#### 4.1.3 타이머와 시계

| 타이머 | 누가 | 시계 | 시작점 | 끝나면 | 값 |
|---|---|---|---|---|---|
| heartbeat 간격 H | 워커 heartbeat 스레드 | `stopping.wait` | 직전 회차 | 다음 heartbeat. **자기 멈춤 판정은 여기서 하지 않는다** | 서버가 알려 줌 `worker_heartbeat_seconds` 5 |
| 자기 멈춤 감시 틱 | 워커 감시 스레드(새, 표식 잡이 있을 때만 일함) | `stopping.wait(WATCHDOG_TICK_SECONDS)`, 경과는 `max(벽, 단조)` | `ok_at`(2xx 받은 요청의 보낸 시각. 잡의 첫 값은 그 claim 요청을 보낸 시각) | `wgap ≥ G_J − m_J` 인 표식 잡 그룹 멈춤 | 틱 1초. 문턱 `G_J − m_J`(기본 600 − 35 = 565초, 늘 ≥ L) |
| 워커 down T | 서버 | 벽시계 `workers.last_seen_at`(F1, 그대로) | 현재 세션의 마지막 요청 | 레인 `down` · 대기 잡 `worker_down` · 헬스 503. **잡은 닫지 않는다** | `worker_timeout_seconds` 60 |
| 끊김 유예 G_J | 서버 janitor | 단조 `mono_fn`, `heard(S)` 의 바닥 = 기동 | `max(heard(S_J), 기동)` | `_heard_lock` 안에서 조건부 닫기 `lost`(`worker_unreachable` 또는 `orphan_unverified`) | 잡 행에 얼린 값, 기본 `disconnect_grace_seconds` 600 |
| 취소 미확인 | 서버 | 벽시계(오늘 그대로) | `cancel.kill_at` | `cancelled`(`cancel_unconfirmed`) — 현재 · 들리는 세션만 | `kill_at + 2H` |
| SIGTERM→SIGKILL | 워커 러너 | 러너 `now_fn`(오늘 그대로) | SIGTERM | SIGKILL | `grace_J`(워커 `grace_seconds` 10, 표식 잡은 §4.3 상한) |
| finish 상한 | 워커 보내는 스레드 | 벽시계(디스크의 `ended_at` ISO) | 잡 끝 | 맨 앞 로그 · 산출물 항목의 마지막 시도가 HTTP 5xx 였으면 finish 먼저 | `FINISH_HOLD_SECONDS` 300(결정 324) |
| 재전송 창 | 워커 | 벽시계(디스크의 `ended_at` ISO) | 잡 끝 | 로그 · 산출물 포기 → finish 에 `log_bytes` 실어 보냄. finish 도 못 보냈으면 큐 항목 버림 + 로그 | `RETRANSMIT_SECONDS` 86400(결정 264) |
| D 재확인 | 새 워커 | 벽시계(D 의 `ok_at.json`) | `D.ok_at` | 「모름」 고아 신원 다시 확인 | `G_J − m_J`(결정 327) |
| `ok_at.json` 쓰기 | 워커 | 벽시계 | 2xx | 세션 폴더에 `{wall}` 기록 | H 마다 많아야 한 번 |
| 스풀 보관 | 워커 | 파일 mtime(시작 청소, F41) | 마지막 쓰기 | 폴더 삭제(잠긴 폴더 제외, 남의 폴더는 7일 · 잠글 수 있을 때만) | `WORKSPACE_KEEP_DAYS` 7(결정 264 · 325) |
| janitor 틱 | 서버 | `stop.wait` | — | 유예 판정 · 취소 미확인 | `JANITOR_SECONDS` 5 |
| 등록 재시도 | 워커 | `stopping.wait` | 연결 실패 | 다시 등록 | `REGISTER_RETRY_SECONDS` 5 |

표식 잡의 프로세스가 서버의 닫기보다 먼저 사라진다는 것을 계산으로 보인다(RL1 · RL4 · RL34, 결정 320).

1. 서버는 J 를 `heard(S) + G_J` 이후에만 닫는다. janitor 틱은 닫는 시각을 더 늦출 뿐이다. `ok_at` 은 서버가 받아 처리한
   요청을 보낸 시각이므로 실제 시간으로 `heard(S) ≥ ok_at` 이다. 서버가 잠들면 단조 시계가 덜 가서 더 늦게 닫는다.
2. 워커는 `ok_at + G_J − m_J` 뒤의 첫 감시 틱에 플래그를 세운다. 그 틱은 1초 안에 온다. 러너가 플래그를 본 뒤 그룹이
   사라지기까지는 이론상 `grace_J` + 4초 이하다. 측정값은 grace + 0.34초 이하였다(F56).
3. 그래서 그룹은 `ok_at + G_J − (T_req + grace_J + 10) + 1 + grace_J + 4 = ok_at + G_J − T_req − 5` 까지 사라진다. 이 시각은
   `heard + G_J` 보다 이르고 남는 여유는 `T_req + 5` = 20초다.
4. `ok_at` 을 보낸 시각으로 재는 한 이 계산에 `T_req` 항은 필요 없다. 그래도 넣는다(D-a). 구현이 `ok_at` 을 응답 받은
   시각으로 잘못 재더라도 heartbeat 는 15초 안에 응답하므로 여전히 안전하다. 두 머신 단조 시계의 속도 차와 서버가
   `_heard_lock` 을 기다린 시간도 이 항이 덮는다.
5. 연결된 동안에는 멈추지 않는다(V2-6). 연결된 워커의 `ok_at` 나이는 H + T_req 이하다. 한 간격에 느리지만 성공한 heartbeat
   하나를 더한 값이다. `2H + T_req ≤ L ≤ G_J − m_J` 이므로 문턱에 닿지 않는다. 2H 는 2xx 응답을 잃은 heartbeat 하나를 견디는
   몫이다.

#### 4.1.4 상태를 바꾸는 두 단계를 한 단위로 (2판의 뿌리 점검)

1라운드 HIGH 둘은 뿌리가 같다. 상태를 바꾸는 두 단계를 한 단위로 묶지 않았다. 자기 멈춤 판단과 실제 경과 시간이 그랬고
파일 쓰기와 오프셋 커밋이 그랬다. 2판은 같은 뿌리가 있는 곳을 설계 전체에서 찾았다. 규칙은 이렇다. 두 단계를 한 잠금이나
한 트랜잭션에 넣는다. 넣을 수 없으면 어긋남이 늘 안전한 쪽으로 나게 한다. 안전한 쪽은 워커가 먼저 멈추는 것, 서버가 늦게
닫는 것, 다시 보내도 결과가 같은 것이다.

| # | 두 단계 | 1판 | 2판에서 묶은 방법 | 결정 · 행 |
|---|---|---|---|---|
| R1 | 워커의 자기 멈춤 판단 ↔ 실제 경과 시간 | heartbeat 루프 안 판정(F53). m 이 「H 마다 판정」 가정 | heartbeat 와 따로 도는 감시 스레드(1초 틱), `ok_at` = 2xx 받은 요청의 **보낸** 시각, m = 요청 시한 + SIGTERM→SIGKILL + 여유, G 하한 L | 320 · RL1 · RL34 |
| R2 | 서버 `log.txt` 쓰기 ↔ `log_offset` 커밋 | 덧붙인 뒤 따로 커밋, 잡별 직렬화 없음(F57) | 잡별 로그 잠금 안에서 확인 → `log_file_bytes` 자리에 쓰기 · 뒤 자르기 → 조건부 커밋, 실패면 되자르기 | 323 · RL36 · RL37 |
| R3 | 서버 유예 판정 ↔ `heard` 갱신 | 「같은 락(`_session_lock`)」 이름만, 없는 잠금(F57) | 새 잠금 `_heard_lock`: 요청의 세션 판정 + `heard` 갱신 / janitor 의 판정 + 조건부 닫기 | 328 · RL4 |
| R4 | `late_result` 「아직 없나」 확인 ↔ 기록 | 순서 없음(V2-11) | 한 `BEGIN IMMEDIATE` 에서 확인 · 넣기 · `log_complete` 쓰기 | 274(2판 문장) · RL21 |
| R5 | 처분 `ready` ↔ 서버에 실제 파일 | 행 · 해시만(V1-12) | 행 · 해시 · 파일 셋(F63) | 275(2판 문장) · RL22 |
| R6 | 워커의 `acked` 기록 ↔ 서버 2xx | `acked` 를 디스크에 안 둠(V2-13) | `outbox.json` 에 `acked`(하한), 그 프로세스가 잡의 로그를 처음 보낼 때 빈 본문 질의, 서버는 접두 버림으로 멱등 | 329 · RL16 |
| R7 | 서버가 잡을 닫음 ↔ 워커가 그것을 앎 | log · phase · finish 응답으로만(조용한 잡은 끝내 모름) | heartbeat 2xx 응답 `closed_jobs` | 321 · 322 · RL35 · RL8 |
| R8 | 죽은 세션 D 의 큐 전달 ↔ 새 세션 등록(등록이 D 를 펜싱) | 응답 처리 없음(V2-4) | 등록 전에 D 의 큐, 응답은 그 큐에만, 등록은 D 의 큐가 끝난 뒤 | 326 · RL39 · RL14 |
| R9 | 세션 폴더 잠금 ↔ 폴더의 주인 | 주인 없음(V1-5) | 폴더 → `lock` → `owner.json`(임시 파일 + rename). `owner.json` 이 없거나 다르면 「내 것 아님」 | 325 · RL38 |
| R10 | 잡 프로세스 띄우기 ↔ 신원 기록(`proc.json`) | `spawned(pid)` 뒤 기록, 그 사이 죽으면 정의 없음 | `claim.json`(claim 때) 있고 `proc.json` 없으면 「모름」 | 327 · RL31 |
| R11 | finish 보내기 ↔ 로그 · 산출물 전달 | finish 가 끝없이 줄 섬(V2-3) | 잡이 끝나고 300초 넘게 그 단계가 5xx 면 finish 먼저 | 324 · RL40 |
| R12 | 고아 신원 확인 ↔ 서버의 유예 만료 | 시작 때 한 번(V2-7) | `D.ok_at + G_J − m_J` 에 다시 확인, 그래도 모르면 서버가 `orphan_unverified` 로 닫음 | 327 · RL31 |
| 원래 묶여 있던 것 | register(이전 세션 잡 닫기 · 펜싱 · 세션 넣기) · claim(얼림) · 좁은 확인(조건부 UPDATE) · 닫기와 finish(`only_from`) | 한 트랜잭션 | 유지 | 265 · 257 · 261 · 266 |

### 4.2 워커 세션과 펜싱 (결정 255 · 260 · 261 · 265 · 328)

- 세션 id 는 워커 프로세스가 시작할 때 만드는 `secrets.token_hex(16)` 하나다. 모든 레인이 같은 값을 쓴다(결정 260). M5q
  따라가기로 exec 하면 새 이미지가 새 세션이 된다.
- 서버는 새 표 `worker_sessions`(§4.9)에 세션을 적는다. 현재 세션은 그 이름에서 `fenced_at IS NULL` 인 행이다. 그런 행은
  언제나 많아야 하나이고 등록 트랜잭션이 이를 지킨다.
- register 는 아래 순서로 처리한다(RL13 · 3판 M7 · F52 · F54).
  1. 본문 모양이 틀리면 400 이다.
  2. 버전을 본다. 오늘처럼 409 와 `server_version` 을 주고 부작용은 없다.
  3. 세션 칸이 없으면 409 `protocol_required` 이고 부작용은 없다.
  4. M5t-c 가 `pair_proof` 검사를 넣을 곳이다. 아래 5~7 의 어떤 부작용보다 먼저 한다(F52, M5t-c 결정 287 · 2차 위임 결정).
  5. S 가 이미 펜싱된 세션이면 409 `session_fenced` 다. 펜싱된 프로세스가 재등록해 새 세션을 거꾸로 펜싱하는 핑퐁을
     막는다.
  6. S 가 현재 세션이면 멱등이다. 워커 행 upsert 와 호스트 사실 교체(F54)만 하며 같은 프로세스라 값도 같다. 잡을 닫지
     않고 능력 보고도 지우지 않는다.
  7. S 가 처음이면 `BEGIN IMMEDIATE` 하나 안에서 다음을 차례로 한다. `previous_sessions` 가운데 같은 이름인 세션의 busy
     잡을 `lost`(`worker_restarted_without_job`)로 닫는다. `unverified_sessions` 가운데 같은 이름인 세션 행에
     `orphan_unverified = 1` 을 적는다. 그 세션들과 다른 현재 세션을 펜싱한다(`fenced_at`, `fenced_by = S`). S 행을 넣고
     워커 행을 upsert 한다. 커밋한 뒤에는 능력 보고를 지우고(F5 의 오늘 규칙) 호스트 사실을 바꾼다(F54).
- 오늘의 등록은 그 이름의 활성 잡을 전부 닫는다(F5). 이 동작을 없앤다. 닫는 것은 `previous_sessions` 로 명시한 세션의
  잡뿐이다(3판 r1 A-H4).
- spec 을 뺀 모든 `/worker/*` 요청이 세션을 싣는다(§4.10). 서버는 `_heard_lock` 안에서 판정한다. S 가 표에 없으면 409
  `not_registered` 이고 워커는 같은 S 로 재등록한다(T10). S 가 펜싱됐으면 409 `session_fenced` 다. S 가 현재 세션이면
  `heard(S)` 를 갱신하고 잠금을 놓은 뒤 요청을 처리한다.
- `_heard_lock` 은 새 잠금이다(결정 328). 주인은 `RemoteWorkersMixin` 이고 `_remote_init` 에서 만든다(F57). 이 잠금은 메모리
  `_heard_at: dict[세션 id, 단조 시각]` 과 두 임계 구역을 지킨다. 하나는 요청의 세션 판정과 `heard` 갱신이다. 다른 하나는
  janitor 의 잡별 유예 판정과 조건부 닫기 UPDATE 다. 로그 요청 전체를 감싸지는 않는다. 로그는 잡별 잠금 `_job_log_locks` 가
  맡는다(§4.5). `_heard_lock` 을 쥔 채 잡별 로그 잠금을 잡지 않고 그 반대도 하지 않으므로 교착이 없다. `_remote_lock` 과
  `_fresh_claims_lock`(F57)은 하던 일을 그대로 한다.
- 펜싱된 세션에 허용하는 요청은 하나뿐이다. finish 본문이 `outcome = lost` 와 `summary_code = worker_replaced` 이고 그 잡이
  busy 이며 잡의 세션이 S 인 경우다. 이 좁은 확인은 조건부 UPDATE 로 잡을 닫고 200 을 준다(결정 261). 그 밖의 요청은 모두
  409 `session_fenced` 이고 아무것도 반영하지 않는다(RL11). 잡이 이미 `lost` 가 된 뒤 펜싱된 세션이 보낸 늦은 finish 도 409
  `session_fenced` 이고 `late_result` 로도 남기지 않는다. 펜싱된 세션은 권한을 잃었고 새 세션과 겹쳐 돌았을 수 있기
  때문이다(2차 Q-B).
- 펜싱된 세션의 잡은 확인이 오거나 `heard(S) + G_J` 가 지날 때까지 busy 로 남아 그룹과 레인을 잡는다(F11). 펜싱 시각부터
  유예를 다시 세지 않는다. 옛 프로세스가 펜싱 전부터 조용했다면 그만큼 빨리 풀린다.
- 새 세션의 claim 이 펜싱된 세션의 잡이 잡고 있는 레인에 오면 `LaneBusy` 를 409 대신 204(잡 없음)로 답한다. 409 가 쏟아지지
  않게 하려는 것이다(F10). 자기 세션의 잡과 겹치면 오늘처럼 409 다.
- 세션 행은 `forget_workers`(F21)가 워커 행을 지울 때 그 이름의 것을 함께 지운다. 그 밖에는 지우지 않는다. 빨리 지우면 늦게
  돌아온 펜싱된 프로세스가 `not_registered` 를 받고 재등록해 살아 있는 세션을 펜싱하는 핑퐁이 생긴다.
- 「잊힌 잡」(F6)과 heartbeat 의 `cancel` 목록은 세션 단위, 곧 그 세션의 busy 잡으로 센다. heartbeat 응답에는
  `closed_jobs` 를 더한다(결정 321). 요청의 `jobs` 목록에 있는 잡 가운데 서버에서 이미 terminal 이고 잡의 세션이 S 인 것마다
  `{id, state, had_cancel}` 을 싣는다. `had_cancel` 은 잡 행에 `cancel_requested_at` 이 있다는 뜻이다(F61).
- 같은 토큰으로 같은 순간에 두 프로세스가 등록해도 살아남는 세션은 정확히 하나다(RL11). 등록이 직렬로 처리되므로 나중
  등록이 이긴다. 먼저 등록한 쪽은 다음 요청에서 409 `session_fenced` 를 받고 종료 0 으로 끝난다.
- 거절한 대안:
  - 세션을 `workers` 행의 칸으로 두기. 이름당 한 행이라 판정이 다시 이름 단위가 된다(3판 r2 근본 원인).
  - 세션 표 없이 메모리에만 두기. 서버가 재시작하면 누가 현재 세션인지 잊는다.
  - 따로 `lease_id` 두기. 잡은 한 번만 claim 되므로 (J, S) 로 충분하다. 이름이 늘면 3판 R1 같은 충돌이 는다.
  - 로그 요청 전체를 `_heard_lock` 하나로 감싸기. 4 MiB 본문을 쓰는 동안 모든 워커의 heartbeat 가 줄을 선다.

### 4.3 끊김 유예와 자기 멈춤 (결정 253 · 256 · 257 · 263 · 266 · 320 · 321 · 322)

서버 쪽은 다음과 같다.

- 판정은 janitor 가 5초마다 하고 `mark_lost_workers` 를 대신한다. `_heard_lock` 안에서 busy 원격 잡마다 `gap(J) > G_J` 이면
  `lost` 로 닫는다. 요약 코드는 `worker_unreachable` 이고 인자는 `name` 과 `seconds = int(gap)` 이다. 세션 행이
  `orphan_unverified = 1` 이고 X_J 이면 요약 코드는 `orphan_unverified` 다(결정 327). 닫기는 `only_from = BUSY_STATES` 와
  `worker_session_id = S` 를 조건으로 건 UPDATE 다(RL4).
- 단조 시계를 쓰면 서버 벽시계가 앞으로 뛰어도 heartbeat 가 오던 잡을 닫지 않는다(RL27 a). 서버 재시작은 메모리를 비우므로
  바닥이 기동 시각이 된다(RL7 b, 2차 Q-C). 닫힘이 느려질 뿐 두 번 돌지는 않는다. 서버가 잠들면 단조 시계가 덜 가서 늦게
  닫는데 이것도 안전한 쪽이다.
- 거절한 대안은 둘이다. 닫는 트랜잭션에서 벽시계 `last_seen_at` 을 다시 읽는 안(3판 r1 A-M4)은 시계 점프를 막지 못한다.
  벽시계에 기동 바닥만 더하는 안도 점프를 막지 못한다.
- 레인 down(T)은 그대로 벽시계 `workers.last_seen_at`(F1)으로 정한다. 유예와 레인 표시는 다른 질문에 답한다. 레인은 지금
  일을 받는지를 말하고 유예는 잡을 버릴지를 정한다.
- 취소 미확인 닫기(F7)는 그 잡의 세션이 현재 세션이고 들릴 때만 하며 판정은 `_heard_lock` 안에서 한다(RL33). 조용하거나
  펜싱된 세션의 cancelling 잡은 G_J 뒤 `lost`(`worker_unreachable`)가 된다. 3판의 새 코드 `cancel_unreachable` 은 만들지
  않는다(결정 278).
- 닫힌 잡은 heartbeat 2xx 응답의 `closed_jobs`(§4.2)로 알린다(결정 321 · 322). 로그와 phase 가 없어 409 를 받을 일이 없는
  조용한 잡도 다음 heartbeat 에서 알게 된다. 취소 요청이 있던 잡이 유예로 `lost` 가 되면 `had_cancel: true` 가 실리므로
  다시 붙은 워커가 그 잡을 멈춘다(V1-4, 1판 RL8 b 를 고침).

얼림(결정 257):

- claim 트랜잭션 안에서 `jobs.irreversible` 에는 그 프리셋이 표식 프리셋 집합에 드는지를, `jobs.disconnect_grace_seconds`
  에는 서버 설정값을 적는다. claim 응답의 `job` 안에 둘 다 싣고 최상위 키는 늘리지 않는다(F58 의 claim 키 잠금). 워커는
  응답의 값만 쓴다. 로컬 레인 잡은 둘 다 NULL 이다. 로컬 잡은 서버가 재시작하면 `lost` 가 되기 때문이다(F4).
- `store.claim` 은 프리셋을 고른 뒤에야 그 프리셋을 안다. 그래서 호출자가 표식 프리셋 이름 집합과 기본 G 를 넘기면 store
  가 같은 트랜잭션에서 적는다.

설정 검증(결정 263 고침 · 320):

- `disconnect_grace_seconds` 는 정수이고 `L + DISCONNECT_GRACE_MARGIN_SECONDS` 이상, 86400 이하여야 한다. 여기서
  `L = max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 15)` 이고 `DISCONNECT_GRACE_MARGIN_SECONDS = 35` 다. 35 는
  기본 워커 grace 가 10 일 때의 m(15 + 10 + 10)이다. 기본 설정에서 하한은 95다. 어기면 기동 실패(종료 2)이고 키 이름과 하한
  값을 말하는 문장을 낸다. 1판의 「`worker_timeout_seconds` 보다 큼」을 이 규칙이 대신한다(V2-6).
- config 의 `DISCONNECT_GRACE_MARGIN_SECONDS` 와 워커의 `T_req + 10 + SELF_STOP_SLACK_SECONDS` 가 같다는 것을 시험이
  잠근다. 두 상수가 따로 움직이지 않게 하려는 것이다.

워커 자기 멈춤(결정 320, 1판 267 을 대신):

- 표식 잡만 멈춘다. heartbeat 와 따로 도는 새 감시 스레드 `rcm-worker-watchdog` 가 `WATCHDOG_TICK_SECONDS`(1초)마다 도는 표식
  잡마다 `wgap ≥ G_J − m_J` 인지 본다. 넘으면 그 잡의 관찰자에 멈춤 플래그를 세워 `should_cancel` 이 참이 되게 한다. 러너는
  그룹에 SIGTERM 을 보내고 `grace_J` 를 기다린 뒤 SIGKILL 한다. 그다음 finish `lost` + `worker_self_stopped`(`seconds` =
  int(wgap))를 큐에 넣는다. 플래그는 한 방향이라 되돌리지 않는다.
- `ok_at` 은 감시 스레드, 보내는 스레드, heartbeat 스레드, 레인이 함께 쓰는 값이다. 2xx 를 받은 요청을 보낸 시각의
  최댓값이고 벽시계와 단조 시계로 둘 다 적는다. 처음 값은 등록 2xx 다. 잡 J 의 첫 값은 J 를 돌려준 claim 요청을 보낸
  시각이다. 이 값은 D-a 가 말한 「claim 응답 시각」보다 이르거나 같다(§12 V2-6 행).
- 표식 잡의 `grace_J` 에는 상한을 둔다. `grace_J = max(1, min(워커 grace_seconds, G_J − L − 15 − SELF_STOP_SLACK_SECONDS))`
  다. 설정 검증이 `G_J ≥ L + 35` 를 보장하므로 기본 grace 10 은 줄지 않는다. 줄였으면 워커 로그에 「grace_seconds N 을 잡 #J
  에서 M 으로 줄임 — disconnect_grace_seconds 에 맞춤」 한 줄을 남긴다. L 은 register 응답의 `heartbeat_seconds` 와
  `worker_timeout_seconds`, heartbeat 응답의 `timeout_seconds`(F62)로 계산한다.
- 서버가 이미 닫은 표식 잡은 감시가 늦더라도 다음 2xx 의 `closed_jobs` 나 보고에 돌아온 409 `job_closed` 를 받고 멈춘다(W4).
- `wgap` 은 벽시계와 단조 시계 가운데 큰 쪽이다. 단조 시계가 잠든 시간을 세지 않는 OS 가 있고 벽시계는 뒤로 갈 수 있다. 그래도
  둘 중 하나는 실제 경과 이상이다. 잠에서 깨면 감시 스레드의 첫 틱이 판정하므로 heartbeat 를 다시 보내기 전이든 뒤든
  멈춘다(RL26).
- 거절한 대안:
  - heartbeat 루프 안에서 판정하기(1판). 한 바퀴가 H + 15초다. 서버가 돌아온 회차의 판정은 보고 재시도 뒤로 밀린다(F53).
  - `ok_at` 을 응답 받은 시각으로 재기. claim 은 보낸 뒤 80초까지 응답이 오므로(F55) m 에 80초를 넣어야 한다.
  - 단조 시계만 쓰기는 잠든 시간을 세지 못한다. 벽시계만 쓰면 시계가 뒤로 갈 때 멈추지 못한다.
  - 서버 검증 없이 워커가 문턱을 L 로 올리기. 서버가 먼저 닫아 잡이 두 번 돌고 이것은 되돌릴 수 없다.
  - 워커 `grace_seconds` 에 상한 두기. 이미 설치된 설정이 기동에 실패한다. 그래서 grace 는 표식 잡에서만 그 잡에 맞춰
    줄인다.

일반 프리셋과 표식 프리셋의 결과는 다르다.

- 일반 프리셋(결정 256): 서버는 T3 에 `lost`(3)로 닫는다. 워커는 계속 돌고 끝나면 finish 를 보낸다. 서버는 상태를 바꾸지
  않고 `late_result` 만 남긴다(§4.6). `rcm wait` 는 T3 에서 3 으로 끝나므로 영영 기다리지 않는다. 취소 요청이 있던 잡은 다시
  붙은 워커가 멈춘다(결정 322).
- 표식 프리셋: 워커가 W3 으로 멈추고 결과는 `lost`(3)다. 자기 멈춤 보고가 G_J 안에 닿으면 요약 코드는
  `worker_self_stopped` 다. 닿지 않으면 서버의 `worker_unreachable` 이 먼저 닫고 뒤에 온 보고는 `late_result` 가 된다.
- 비대칭 위험이 둘 있다. 워커 벽시계가 앞으로 `G_J − m_J` 이상 뛰면 표식 잡이 일찍 멈춘다. 서버가 heartbeat 에 5xx 를
  `G_J − m_J` 동안 계속 내면 서버는 요청을 받으므로 닫지 않지만 표식 잡은 멈춘다. 둘 다 결과는 `lost`(3)이고 두 번 돌지
  않는 쪽이다(§9).

### 4.4 재시작 순서 · 이어받기 · 고아 정리 (결정 268 · 325 · 326 · 327)

워커 데이터 폴더에 `<data>/worker-sessions/<S>/` 를 새로 둔다. 이름에 `sessions` 만 쓰지 않은 것은 문서에서 그 낱말이
「요청 세션」을 뜻하기 때문이다(grep `sessions` 27건). 폴더에 두는 파일은 다음과 같다.

- `lock`: 프로세스가 살아 있는 동안 `fcntl.flock(LOCK_EX)` 로 잡는다(F43). PR1 이 만든다.
- `owner.json`: `{server_sha256, token_name}` 이다(결정 325, D-e). `server_sha256` 은 워커 설정에 적힌 `server` 값에서 끝의
  `/` 를 떼고 `server_name` 과 `\n` 으로 이은 문자열의 sha256 hex 다. `auto` 면 해석한 주소가 아니라 `auto` 와 광고 이름을
  그대로 쓴다. 주소가 바뀌어도 주인이 같게 하려는 것이다. `token_name` 은 `GET /api/whoami` 의 `name` 이다(F64). 토큰 값은
  적지 않는다. 임시 파일에 쓴 뒤 rename 한다. PR1 이 만든다.
- `ok_at.json`: `{wall}`(ISO)이다. 2xx 를 받은 뒤 H 마다 많아야 한 번 덮어쓴다. D 를 다시 확인할 때 쓴다. PR1 이 만든다.
- `jobs/<id>/claim.json`: `{irreversible, disconnect_grace_seconds}` 이고 claim 직후에 쓴다. PR1 이 만든다.
- `jobs/<id>/proc.json`: `{pgid, start}` 다. 러너가 Popen 한 뒤 관찰자 `spawned(pid)` 로 알리면 한 번 기록한다(F45).
  `start` 는 Linux 에서 `"linux:<starttime>"` 이고 `/proc/<pid>/stat` 의 22번째 칸을 마지막 `)` 뒤에서 센다(F59). 그 밖의
  OS 에서는 `"ps:<lstart>"`(`ps -o lstart= -p <pid>`, F44)이다. 읽지 못하면 `null` 이다. PR1 이 만든다.
- `jobs/<id>/stream.log`: 서버로 가는 스트림 바이트다(결정 271). PR2 가 만든다.
- `jobs/<id>/outbox.json`: 보낼 phase(마지막 하나), `acked`, finish 기록, `done` 을 담는다. `acked` 는 서버가 2xx 로 확인한
  오프셋이고 하한이다. finish 기록은 `outcome · exit_code · summary · summary_code · summary_args · finished_at · ended_at ·
  log_bytes · bundle` 이다. PR2 가 만든다.

시작 순서는 다음과 같다(RL14 · RL15 · RL12 b · RL25 · RL38). 2판은 V2-12 에 따라 폴더를 검사 뒤에 만든다.

1. 데이터 폴더를 열고 시작 청소를 한다(F41). `worker-sessions/` 아래에서는 잠긴 폴더를 건너뛴다. 주인이 다른 폴더는 7일이
   지났고 잠글 수 있을 때만 지운다. 그런 폴더의 결과는 이미 24시간 재전송 창을 넘었다.
2. 프로토콜을 확인한다(§4.7, T13). 서버에는 `GET` 만 보낸다. 결과가 종료 3, 따라가기, 종료 2 가운데 하나면 폴더를 만들지
   않는다. 재시작 루프가 빈 폴더를 쌓지 않게 하려는 것이다(V2-12).
3. `GET /api/whoami` 로 토큰 이름을 얻는다(F64). 연결에 실패하면 등록과 같이 5초 뒤 다시 한다. 401 · 403 은 오늘 등록의
   인증 실패와 같게 다룬다(종료 2).
4. 새 S 를 정한다. 메모리에서는 2 전에 만들어 둔다. 그다음 폴더, `lock`, `owner.json` 을 만든다.
5. 다른 세션 폴더마다 확인한다. `owner.json` 이 없거나 나와 다르면 건너뛴다(W9). 같으면 `LOCK_NB` 로 잠가 본다. 잠기지
   않으면 살아 있는 세션이므로 건너뛴다. EWOULDBLOCK 이 아닌 OSError 가 나면(예: 네트워크 파일 시스템) 판정할 수 없으므로
   건너뛴다.
6. 잠근 세션 D 는 죽은 세션이다. D 의 잡 폴더마다 고아를 판정한다.
   - `proc.json` 이 있고 리더 pid 가 살아 있고 `start` 가 같으면 같은 그룹이다. SIGTERM 을 보내고 워커 `grace_seconds` 를
     기다린 뒤 SIGKILL 하고 워커 로그에 한 줄을 남긴다. 결과는 「확인됨」이다.
   - 리더 pid 가 없고 `killpg(pgid, 0)` 도 실패하면 그룹이 없다. 「확인됨」이다.
   - 리더 pid 는 살아 있는데 `start` 가 다르면 pid 가 재사용된 것이고 원래 그룹은 없다. 손대지 않고 「확인됨」으로 둔다.
   - 다음 셋은 「모름」이다. 리더 pid 가 없는데 `killpg(pgid, 0)` 이 성공하면 리더만 죽고 멤버가 살아 있는 것이다(F59).
     `start` 를 못 읽은 경우는 `ps` 가 없거나 실패했거나 `/proc` 를 못 읽은 경우다. `claim.json` 은 있는데 `proc.json` 이
     없으면 띄운 순간 죽었을 수 있다(R10). 「모름」이면 신호를 보내지 않고 경고 한 줄을 남긴다(RL31).
7. D 의 보고 큐를 D 의 세션 id 로 보낸다. 규칙은 §4.5 와 같고 PR2 가 만든다. 응답은 그 큐에만 적용한다(아래 표, 결정 326).
   연결 실패나 5xx 면 백오프로 다시 한다. 등록은 D 의 큐가 끝날 때까지, 또는 그 잡의 24시간 재전송 창이 끝날 때까지
   기다린다. 등록이 D 를 펜싱하면 D 의 finish 가 2차 Q-B 에 따라 버려지기 때문이다. 로그와 산출물 단계의 5xx 는
   `FINISH_HOLD_SECONDS` 규칙(결정 324)을 따른다.
8. 등록한다. `previous_sessions` 에는 6 에서 잡이 모두 「확인됨」인 D 를, `unverified_sessions` 에는 「모름」 잡이 하나라도
   있는 D 를 넣는다. 서버는 앞의 것의 busy 잡을 `worker_restarted_without_job` 으로 닫고 둘 다 펜싱한다(§4.2). 「모름」 D 의
   잡은 서버가 T8 처럼 `heard(D) + G_J` 까지 잡아 둔다.
9. 「모름」 잡이 있는 D 마다 재확인 타이머를 건다. 시각은 `D.ok_at + G_J − m_J` 다. `D.ok_at` 은 D 의 `ok_at.json` 에서
   읽는다. 파일이 없으면 D 폴더 `lock` 의 mtime 을 쓴다. G_J 와 X_J 는 `claim.json` 에서 읽는다. 그 시각에 6 의 판정을 다시 한다.
   「확인됨」이 되면 D 의 id 로 좁은 확인 finish(`lost` + `worker_replaced`)를 보내 그룹 자리를 일찍 푼다. D 는 8 에서
   펜싱됐으므로 이것이 D 에 허용되는 단 하나의 요청이다(§4.2). 그래도 「모름」이면 아무것도 하지 않는다. 그러면 서버가 유예
   만료에 X_J 인 잡을 `orphan_unverified` 로 닫고 사람에게 경고를 보인다(결정 327). 재확인 시각이 이미 지났으면 바로 한 번
   한다.
10. D 폴더는 7일 청소에 맡긴다. 이미 보낸 것에는 `done` 이 적혀 있다.

죽은 세션 D 의 큐를 D 의 id 로 보낼 때의 응답은 아래 표와 같다(결정 326, D-f). 살아 있는 이 프로세스의 W5 · T10 과 다르다.

| 응답 | 그 큐 항목 | 이 프로세스 | 이유 |
|---|---|---|---|
| 2xx | 오늘처럼(acked · 다음 단계 · `done`) | 계속 | — |
| 409 `job_closed` | 그 잡의 남은 항목 지움(`late_result` 여부는 서버) | 계속 | 서버가 이미 닫았다 |
| 409 `log_gap` | `expected_offset` 부터 다시 | 계속 | §4.5 와 같다 |
| 409 `session_fenced` | 그 잡의 로그 · phase · 산출물 지움. finish 는 잡의 고아 판정이 「확인됨」이면 좁은 확인 finish(`lost` + `worker_replaced`)로 바꿔 한 번 보냄, 「모름」이면 보내지 않음(재확인 9 에 맡김). 원래 결과는 버려진다(2차 Q-B) | 계속. **W5 를 부르지 않는다**(종료 0 하지 않음) | D 는 다른 살아 있는 프로세스가 이미 펜싱했다 — 이 프로세스는 펜싱되지 않았다 |
| 409 `not_registered` | D 의 큐 전부 지움, 워커 로그 한 줄 | 계속. **D 의 id 로 재등록하지 않는다** | 서버가 D 를 모른다(잊었거나 DB 를 되돌렸다). D 로 재등록하면 죽은 세션을 되살려 살아 있는 세션을 펜싱한다 |
| 409 `protocol_required` | 지우지 않음, 보내기 멈춤 | 등록으로 넘어감(등록이 W6 로 종료 3) | 2 의 확인 뒤라 닿지 않는 갈래 |
| 401 · 403 | 지우지 않음(파일 그대로), 보내기 멈춤, 로그 한 줄 | 계속 | 주인 확인(5)을 지나 남의 큐일 리 없지만, 그래도 진짜 결과를 지우지 않는다(V1-5) |
| 400 · 404 · 413 | 그 항목 지우고 로그(§4.5 표와 같음) | 계속 | — |
| 503 · 5xx · 연결 실패 · 시간 초과 | 백오프 재시도(등록은 기다림) | 계속 | 서버 문제 |

- 오늘의 e2e 계약(F47 `test_killed_worker…`)은 잡이 이미 `lost` 면 등록이 요약을 덮지 않는다는 것이다. 닫는 것은 busy
  잡뿐이므로 이 계약은 그대로다(RL14 b).
- M5q exec 의 드레인 조건은 「레인이 다 놀고 보고 큐가 비었을 때」가 된다. 60초(`DRAIN_REPORT_SECONDS`) 안에 큐가 비지
  않으면 오늘처럼 exec 한다. 큐는 디스크에 남아 새 이미지가 7 에서 보낸다. 그래서 F42 의 「restarting anyway」가 더는 보고를
  잃지 않는다(PR2).
- PR 나눔(V1-2): 폴더, `lock`, `owner.json`, `ok_at.json`, `claim.json`, `proc.json`, 고아 판정, 재확인,
  `previous_sessions`, `unverified_sessions`(1~6 · 8~10)는 PR1 이 만든다. D 의 큐 보내기(7)와 그 응답 표는 디스크 큐가 생기는
  PR2 가 만든다. PR1 만 머지된 상태에서는 죽은 워커가 못 보낸 finish 가 오늘처럼 메모리와 함께 사라지고 등록이 그 잡을
  `worker_restarted_without_job` 으로 닫는다. 오늘 동작과 같다.
- 거절한 대안:
  - 등록 본문의 「버린 lease 목록」만 믿기(3판). 그 목록을 만든 프로세스가 죽었는지 서버는 모른다. 잠금이 증거다.
  - 고아를 pgid 만 보고 죽이기. pid 가 재사용됐으면 남의 프로세스를 죽이고 이것은 되돌릴 수 없다.
  - 데이터 폴더 전체를 잠그기. 같은 폴더를 쓰는 두 번째 프로세스를 막으면 결정 255 의 「새 세션이 옛 세션을 펜싱」을 할
    수 없다.
  - 리더 없는 그룹을 멤버로 알아보기. 멤버의 시작 시각을 기록하지 않았다. 「모름」으로 두고 다시 확인하며 경고한다.
  - 서버 URL 을 해석한 주소로 해시하기. `auto` 가 주소를 바꾸면 자기 폴더를 남의 것으로 본다.
  - D 의 큐에 401 · 403 이 오면 지우기(1판). 진짜 결과를 지운다(V1-5).

### 4.5 로그 오프셋과 순서 있는 보고 큐 (결정 264 · 271 · 272 · 273 · 323 · 324 · 329)

워커 쪽은 다음과 같다.

- 단위는 워커가 서버로 보내는 스트림의 바이트이고 가리기 전의 값이다(F14). 워커의 `stream.log` 길이가 곧 그 잡의 스트림
  길이다.
- 워커 관찰자 `output(data)` 는 `stream.log` 에 덧붙이고 펌프를 막지 않는다. 쓰기에 실패하면 그 잡을 「스풀 깨짐」으로
  표시하고 그 뒤 바이트는 세기만 한다(§4.6 `log_bytes`). 네트워크는 워커당 하나인 보내는 스레드가 맡는다.
- 잡마다 보내는 순서는 이렇다. phase 가 있으면 마지막 값을 먼저 보낸다. 다음으로 `stream.log[acked:]` 를 256 KiB 이하로,
  마지막 `\n` 에서 자른 조각으로 보낸다. 한 줄은 늘 조각 안에 든다(F34 · F35). 잡이 끝났으면 산출물을 올리고(§4.6) 마지막으로
  finish 를 보낸다. 한 잡 안의 순서는 지키고 잡끼리는 서로 기다리지 않는다.
- `acked` 는 2xx 응답의 `offset` 으로만 오르고 `outbox.json` 에 적는다(V2-13). 1판은 이 값을 저장하지 않아 재시작한 워커가
  0 부터 다시 보냈다. 디스크의 `acked` 는 하한일 뿐이다.
- 빈 본문 오프셋 질의는 보내는 스레드가 한다(V2-13). 이 프로세스에서 그 잡의 로그를 처음 보낼 때 본문 없이
  `X-RCM-Log-Offset: <acked>` 로 한 번 보내 서버의 `offset` 을 받고 거기서부터 이어 보낸다. 이어받은 D 의 잡, M5q exec 뒤의
  새 이미지처럼 디스크 `acked` 로 시작하는 경우가 모두 여기에 든다. 같은 프로세스가 claim 한 잡은 0 부터 보내므로 묻지
  않는다.
- 요청은 `POST /worker/jobs/{id}/log` 이고 헤더는 `X-RCM-Worker-Session: <S>` 와 `X-RCM-Log-Offset: <n>` 이다. n 은 조각의
  첫 바이트 오프셋이다.

서버의 로그 쓰기 절차는 아래와 같다(결정 323, D-b). 1판의 「덧붙이고 flush 한 뒤 커밋」을 대신한다.

1. 헤더를 검사한다. `X-RCM-Worker-Session` 이 32자 소문자 hex 가 아니면 400 이다. 400 응답은 `{"error"}` 꼴이고 `code` 가
   없다(F58 `tests/test_worker_api.py:618`). `X-RCM-Log-Offset` 이 0 이상의 정수가 아니거나 세션은 있는데 오프셋이 없어도
   400 이다. 세션 헤더가 없으면 그 이름에 레거시 busy 잡이 있는지 본다. 있으면 오프셋 없이 오늘처럼 덧붙이는 레거시 길로
   가고(T11), 없으면 409 `protocol_required` 다. 400 과 409 는 아무것도 반영하지 않는다.
2. 세션을 판정하고(`_heard_lock`, §4.2) 잡의 소유와 상태를 본다(403 · 404 · 409 `job_closed` · `session_fenced`).
3. 잡별 로그 잠금 `_job_log_locks[job_id]` 를 잡는다. 새 잠금이고 `RemoteWorkersMixin` 이 가진다. 사전 접근은
   `_remote_lock` 아래에서 한다. 잡이 끝나면 `_drop_log_partial` 과 같은 곳에서 지운다. 같은 잡에 동시에 온 요청은 여기서
   줄을 선다. 서버에서 아직 도는 시한 초과 요청과 워커의 재시도가 겹치는 경우가 그렇다.
4. 잠금 안에서 잡 행을 다시 읽는다. state, 세션, `L = log_offset`, `F = log_file_bytes`(NULL 은 0)를 읽는다.
5. `offset > L` 이면 409 `log_gap` 과 `expected_offset = L` 을 준다. `offset ≤ L` 이면 앞의 `L − offset` 바이트를 버린
   나머지가 `rest` 다. `rest` 가 비면 200 `{job_id, bytes: 0, markers: 0, offset: L}` 을 준다. 오프셋 질의와 중복 재전송이
   이 경우다.
6. `masked = mask_bytes(rest)` 로 가린다(F14). `log.txt` 를 열고(없으면 만든다) 크기 `size` 를 잰다. `size ≥ F` 면 쓰는
   위치 `pos` 는 F 다. `size < F` 는 OS 충돌로 fsync 되지 않은 꼬리를 잃은 경우다(T16, V2-10). 이때는 작은 트랜잭션 하나로
   `jobs.log_complete = 0` 을 고정하고 서버 로그에 「log gap: job #N log.txt is size bytes, committed F」 한 줄을 남긴 뒤
   `pos = size` 로 둔다. 빈 바이트 구멍을 만들지 않으려는 것이다.
7. `seek(pos)`, `write(masked)`, `truncate(pos + len(masked))`, flush 순서로 쓴다. 덧붙이지 않는다. 매 요청이 커밋된 길이
   뒤를 덮어쓰고 자르므로 앞 요청이 남긴 미커밋 꼬리나 부분 쓰기가 남지 않는다(V1-3). OSError(ENOSPC 등)가 나면
   `truncate(pos)` 를 시도하고 503 `log_write_failed` 를 준다(T15).
8. `BEGIN IMMEDIATE` 트랜잭션 하나에서 아래 UPDATE 를 하고 마커 이벤트를 넣는다. 마커는 `masked` 의 줄에서 찾는다. 조각이
   `\n` 으로 끝나므로 마커는 온전하다. 바뀐 행이 0 이면 롤백하고 `truncate(pos)` 한 뒤, 잡을 다시 읽어 409
   `job_closed`(+`state`)나 `session_fenced` 를 준다. SQLite 가 바쁘면(`busy_timeout` 5초, F57) `truncate(pos)` 하고 오늘의
   `code` 인 503 `database_busy` 를 준다.

   ```sql
   UPDATE jobs SET log_offset = L + len(rest), log_file_bytes = pos + len(masked), last_output_at = …
   WHERE id = ? AND log_offset = L AND state IN BUSY_STATES AND worker_session_id = S
   ```
9. 잠금을 놓고 커밋한 뒤 오늘처럼 마커를 발행한다. 응답은 `{job_id, bytes, markers, offset}` 이고 `offset` 은 새
   `log_offset` 이다.

- 서버가 기동할 때 정리한다. busy 원격 잡 가운데 `log_file_bytes` 가 있는 잡의 `log.txt` 가 그 값보다 길면 그 길이로
  자른다. 쓴 뒤 커밋하기 전에 서버가 죽어서 남은 조각이다. 더 짧으면 6 과 같이 `log_complete = 0` 을 고정하고 로그 한 줄을
  남긴다. 같은 단위끼리만 비교한다(3판 r1 A-M1).
- 레거시(프로토콜 1) 잡의 로그는 오늘 길(덧붙임 + `_log_partial`)을 그대로 쓴다. 그 잡은 `log_offset` 이 NULL 이고
  `log_complete` 도 NULL 이라 「모름」이다.
- 워커의 `acked` 는 2xx 응답의 `offset` 으로만 오르므로 `acked ≤ 서버 log_offset` 이 늘 성립한다. 빈틈은 버그일 때만 생긴다.

응답별 처리는 아래 표와 같다. 살아 있는 이 프로세스의 큐에 적용한다(3판 r2 A-M5 · r1 A-H5). 죽은 세션 D 의 큐는 §4.4 의
표를 따른다.

| 응답 | phase | log | 산출물 | finish | heartbeat · claim |
|---|---|---|---|---|---|
| 2xx | 지움 | `acked = offset`(디스크에) | 다음 단계 | 큐 끝(`done`), 레인 풀림 | — (heartbeat 의 `closed_jobs` 는 W4) |
| 409 `log_gap` | — | `expected_offset` 부터 다시(프로세스 안 죽임) | — | — | — |
| 409 `job_closed` | 지움 | 전송만 멈춤(스풀 남김). 잡 처리는 W4 | 건너뜀 | 지움(`late_result` 여부는 서버) | — |
| 409 `session_fenced` | W5 | W5 | W5 | W5(좁은 확인만 보냄) | W5 |
| 409 `not_registered` | 같은 S 로 재등록 뒤 다시 | 같음 | 같음 | 같음 | 같은 S 로 재등록(T10) |
| 409 `protocol_required` | 지우지 않음 · 로그 한 줄(도는 중에는 닿지 않는 갈래, §4.1.2) | 같음 | 같음 | 같음 | 등록 전이면 W6(종료 3) |
| `code` 없는 409(옛 서버 문구) | 지움 | 전송 멈춤, 프로세스 안 죽임 | 건너뜀 | 지움 | 오늘대로 |
| 400 · 404 · 413 | 지우고 로그 | 전송 멈춤 + 그 잡 `log_bytes` 가 `acked` 보다 크게 남음 | 처분 `failed`(`upload_failed`) | 지우고 로그 | 오늘대로 |
| 401 · 403 | 지우고 로그 | 같음 | 같음 | 같음 | 오늘대로 |
| 503(`log_write_failed` · `database_busy` 포함) · 5xx · 연결 실패 · 시간 초과 | 백오프 재시도 | 같음(같은 오프셋부터 — 서버가 되잘랐다, T15) | 같음 | 같음 | 오늘대로 |

- 백오프는 1초에서 시작해 두 배씩 30초까지 늘리고 워커당 하나다. 잡이 도는 동안에는 횟수와 시간에 상한이 없다. 3판 r3
  A-H6 · R1 의 「연속 실패 20번」은 버렸다.
- finish 상한(결정 324, D-c, V2-3): 세 조건이 모두 맞으면 finish 를 먼저 보낸다. 잡이 끝난 뒤 `FINISH_HOLD_SECONDS`(300초)가
  지났고 큐 맨 앞이 로그나 산출물 항목이며 그 항목의 마지막 시도가 HTTP 5xx 응답이었을 때다. 5xx 는 서버에 닿았다는
  뜻이며 `log_write_failed`, `database_busy`, 그 밖의 5xx 가 여기에 든다. 이때 산출물이 아직 안 올라갔으면 처분은 `failed`
  + `upload_failed` 이고 `log_bytes` 는 그대로 보낸다. 서버는 `log_offset < log_bytes` 를 보고 `log_complete = false` 를
  적는다. finish 본문에 `log_complete` 칸을 따로 두지 않는다. finish 가 2xx 를 받으면 남은 로그와 산출물 항목은 버린다.
  잡이 terminal 이라 어차피 409 를 받는다. 마지막 시도가 연결 실패였으면 서버에 닿지 않은 것이므로 순서를 바꾸지 않는다.
  그때는 finish 도 갈 수 없다. 서버가 돌아오면 로그부터 이어 보낼 수 있다.
- 300초로 정한 근거는 셋이다. 첫째, 백오프 상한 30초로 열 번 넘게 다시 해 볼 수 있다. 1+2+4+8+16 = 31초 뒤로는 30초마다
  하므로 300초면 14번이다. 둘째, 끝난 잡이 그룹 자리와 `rcm wait` 를 붙잡는 시간을 끊긴 잡의 기본 유예 600초의 절반
  이하로 둔다. 셋째, 일시적인 DB 바쁨(`busy_timeout` 5초, F57)은 그 안에 지나간다.
- 재전송 창: 잡이 끝난 뒤 `RETRANSMIT_SECONDS`(24시간, 결정 264)가 지나면 로그와 산출물을 포기하고 finish 를 `log_bytes` 와
  함께 보낸다. finish 마저 24시간 안에 못 보내면 큐 항목을 버리고 로그 한 줄을 남긴다.
- 레인은 자기 잡의 큐가 끝날 때까지 다음 claim 을 하지 않는다. 서버는 finish 전까지 그 레인을 busy 로 본다(F10). heartbeat
  의 `jobs` 목록에는 큐가 안 끝난 잡이 남는다. 「잊힌 잡」을 막으려는 것이다. 오늘 `_finish` 가 잡을 보고 뒤에 빼는 것과
  같은 이유다(`remote_worker.py:1059-1062`).
- 오늘의 메모리 버퍼(`LOG_KEEP_BYTES`), `_unreported`, `REPORT_RETRIES`, heartbeat 안의 `_retry_unreported`(F53) 길은 이
  큐로 바뀐다(F32 · F33). heartbeat 스레드는 보고를 다시 보내지 않는다.
- `\r` 진행 표시가 줄 끝 없이 이어지면 러너가 64 KiB 마다 끊어 보낸다. 그래서 실시간 tail 에는 그만큼 늦게 보인다. 이 점은
  문서에 적는다(RL17 c).
- 거절한 대안:
  - 3판의 `log.pending`. F34 때문에 필요 없다.
  - 조각마다 서버가 줄 끝 없는 꼬리를 메모리에 두기. 재시작하면 사라진다.
  - 워커 로컬 `log.txt` 를 재전송 원본으로 쓰기. 그 파일은 스트림과 내용이 다르다(F36).
  - 쿼리 문자열 `?offset=`. 경로 정규식 `_WORKER_JOB_RE`(`server.py:263`)를 건드리게 되고 헤더 관례가 이미 있다.
  - 덧붙이고 기동 때만 자르기(1판). 커밋 실패나 쓰기 실패 뒤의 재전송이 남은 바이트 뒤에 붙는다(V2-2 · V1-3).
  - 쓰기마다 fsync. 4 MiB 본문과 수십 개의 잡에서 디스크를 묶어 둔다. 짧은 파일은 드물고 `log_complete = false` 로
    정직하게 알린다.
  - 짧은 파일에서도 `log_file_bytes` 위치에 그대로 쓰기. 빈 바이트 구멍이 생긴다.
  - 짧은 파일을 워커에게 다시 보내게 하기. 서버는 가린 바이트와 원본 오프셋의 대응을 갖고 있지 않다.
  - finish 가 끝없이 로그 뒤에 줄 서기(1판). 끝난 잡이 24시간 running 으로 남는다(V2-3).
  - 연결 실패에도 finish 먼저 보내기. 서버에 닿지 않으면 finish 도 못 가고 로그만 잃는다.

### 4.6 finish · 늦은 결과 · 로그 완전성 · 산출물 (결정 256 · 259 · 264 · 273~275 · 277 · 278 · 324)

- finish 본문에 `worker_session_id` 와 `log_bytes` 를 더한다. `log_bytes` 는 그 잡이 낸 스트림 전체 바이트이고 스풀이
  깨졌으면 센 값이다. `summary_code` 는 오늘의 `PREFLIGHT_CODES` 나 새 `LOST_CODES` 가운데 하나다. `LOST_CODES` 는
  `outcome = lost` 일 때만 받고 인자는 코드마다 정한 키만 받는다. 오늘 `_preflight_summary` 와 같은 엄격함이다.
- `LOST_CODES` 는 `worker_self_stopped`(인자 `seconds`), `worker_replaced`(인자 `name`, 서버가 토큰 이름으로 채움),
  `snapshot_unreachable`(인자 없음)이다. 서버만 쓰는 `lost` 코드는 오늘의 `worker_unreachable` 과
  `worker_restarted_without_job`, 그리고 새 `orphan_unverified`(인자 `name` · `seconds`, 결정 327)다.
- 로그 완전성: finish 를 받을 때 `log_complete = 0` 이 이미 고정돼 있으면(T16) 0 으로 둔다. 아니면 `(log_offset ≥
  log_bytes)` 를 잡 행에 적는다. `log_bytes` 가 없는 레거시 잡은 NULL 이다. 로컬 레인 잡은 서버가 직접 쓰므로 NULL 이 아니라
  참이다. 그러지 않으면 로컬 잡 전부에 「모름」이 붙는다. `recent_json`, 잡 문서, `rcm wait` JSON 은 `log_complete`(참,
  거짓, null)를 보인다. 거짓이면 `rcm wait` 사람용 줄, `rcm logs` 끝, 웹 로그 보기에 「로그가 불완전함」을 보인다(PR3).
- 늦은 결과(결정 256 · 274): 2판은 V2-11 에 따라 트랜잭션을 단계로 적는다. `BEGIN IMMEDIATE` 하나 안에서 다음 넷을 한다.
  1. 잡을 다시 읽어 `state = lost` 이고 `worker_session_id` 가 요청 세션이며 요청 세션이 펜싱되지 않았는지 확인한다.
  2. `SELECT 1 FROM events WHERE job_id = ? AND kind = 'late_result'` 가 없을 때만 다음으로 간다.
  3. events 에 kind `late_result` 를 한 건 넣는다. 내용은 `{outcome, exit_code, finished_at, received_at, log_complete}` 이고
     `finished_at` 은 워커 값에 아래 자르기를 적용한 것이다.
  4. `jobs.log_complete` 를 위 규칙으로 쓴다. 유예로 닫힌 잡은 이 칸이 NULL 이었다.

  SQLite 는 `BEGIN IMMEDIATE` 로 쓰는 쪽을 하나씩 줄 세우므로 동시에 온 늦은 finish 둘 가운데 하나만 들어간다. events 에
  유일 키를 새로 두지 않는다. 응답은 409 `job_closed` + `state: lost` + `late_result_recorded` 다. 넣었으면 true, 이미
  있었으면 false 다. 잡 상태, 요약, `finished_at`, 로그, 마커, 산출물, 알림, `job_finished` 는 모두 그대로 둔다.
  `GET /jobs/{id}` 의 종료 잡 문서에 `late_result`(객체 또는 null)를 싣는다. `rcm wait` JSON 은 잡 문서를 복사하므로 같은
  키를 싣는다(`PLAN.md:257`). 펜싱된 세션의 늦은 finish 는 기록하지 않는다(2차 Q-B, RL11).
- 중복 finish(RL21 a): 같은 세션이 보냈고 잡이 그 finish 로 이미 끝났으면 409 `job_closed` 이고 기록하지 않는다. 워커는 그
  항목을 큐에서 지운다.
- `finished_at` 자르기(결정 277): 값이 `[started_at, 수신 시각]` 밖이면 가까운 끝으로 자른다. 그래서 `job_seconds` 가 음수가
  되지 않는다(RL27 b).
- 준비 단계(결정 259): 스냅샷 내려받기가 status 0 이나 503 이면 `lost` + `snapshot_unreachable` 이다. 404 · 409(스냅샷 없음,
  조립 실패), 저장소 없음, 깨진 tar 는 오늘처럼 `failed` 다. git_ref fetch 실패는 rcm 서버와의 끊김이 아니므로 `failed`
  그대로다.
- 산출물(결정 275, RL22): 2판은 파일 조건을 더했다(V1-12). 업로드는 보고 큐에서 finish 바로 앞 단계다. 연결 실패와 503 은
  재전송 창 안에서 다시 한다. 같은 해시를 다시 올리면 서버가 200 을 준다(F17). 5xx 가 300초를 넘으면 결정 324 를 따른다.
  묶음 스풀은 지금도 지우지 않는다(`remote_worker.py:1025-1027`). 처분은 finish 를 보내기 직전에 정한다. 서버는 그 잡의 묶음
  행이 있고 해시가 같고 `artifacts_dir(job)/bundle.tar` 파일이 있을 때만 처분 `ready` 를 받는다(F63). 행이나 파일이 없으면
  `failed` + `upload_failed`, 해시가 다르면 `failed` + `hash_mismatch` 다. 둘 다 이미 있는 `REASONS` 다
  (`core/artifacts.py:76-94`). 잡 결과는 오늘처럼 산출물과 무관하다.

### 4.7 프로토콜 판과 호환 (결정 258 · 262 · 270 · 329)

- `worker_protocol = 2` 다. 1 은 이 명세 이전의 세션 없는 판이다. `/worker/spec` 응답과 register 응답에
  `worker_protocol: 2` 를 싣는다.
- 새 워커는 등록 전에 확인한다(T13). `GET /worker/spec` 을 불러 `worker_protocol ≥ 2` 면 진행한다. 키가 없거나 404 면
  `GET /api/health` 의 `version` 을 본다. 자기 버전과 다르면 오늘의 등록 길로 가서 따라가기(M5q)를 하거나 종료 2 로 끝난다.
  서버의 버전 409 는 어떤 부작용보다 먼저 나온다(F5 · `remote_workers.py:390-398`). 버전이 같으면 「서버가 워커 프로토콜 1 —
  서버를 올려라」 한 줄을 남기고 종료 3 으로 끝난다. 이때 등록과 claim 은 0건이고 세션 폴더도 0개다(RL25 · RL32, V2-12).
- 종료 코드(결정 258): 펜싱은 0, 프로토콜 불일치는 3 이다. 나머지는 오늘대로 0 은 멈춤, 2 는 등록 막힘, 1 은 exec 실패다.
  launchd `SuccessfulExit=false` 와 systemd `Restart=on-failure` 는 0 을 되살리지 않고 3 을 되살린다(F39). 서비스 파일은
  바꾸지 않는다.
- M5t-c 와의 관계(F52): 2판은 이 문장을 1판에서 고쳤다. M5t-c 는 확실한 신원 불일치에 종료 0 을 써서 서비스가 되살리지
  않게 한다. 연결 실패, 5xx, 503 은 종료하지 않고 프로세스 안에서 다시 시도한다(결정 303). 종료 3 은 이 명세의 프로토콜
  불일치 하나다. 그 뜻은 「일을 시작하지 말고 감독자가 다시 시도하라」다. 재시작이 버전 따라가기를 부르기 때문이다. 부모
  명세 M8 의 「서비스가 되살리지 않는 종료 코드」는 「펜싱 · 확실한 신원 거절」로 좁혀졌다. 이 명세의 0 은 펜싱이다.
- 옛 워커(프로토콜 1)가 새 서버에 붙으면(결정 262) 서버는 레거시 busy 잡이 있는 이름에서 온 세션 없는 heartbeat, phase,
  log, finish, artifacts 만 받는다. log 는 오프셋 없이 오늘처럼 덧붙인다. claim, register, capabilities 는 409
  `protocol_required` 다. 옛 워커의 반응은 F40 에 있다.
- `protocol_required` 의 `error` 문구는 `"worker protocol 2 required: upgrade rcm on this worker"` 다(결정 329, V2-13).
  「not registered」를 담지 않는다. 옛 워커는 claim 409 의 `error` 에 그 부분 문자열이 있으면 재등록 루프를 돌기
  때문이다(`remote_worker.py:773`, F40).
- 1판의 「도는 중에 409 `protocol_required` 를 받으면 모든 잡을 멈추고 종료 3」은 지웠다(§4.1.2, V2-9).
- 펜싱 핑퐁은 없다(RL10). 펜싱된 프로세스는 종료 0 으로 끝나고 감독자가 되살리지 않는다. 펜싱된 세션의 재등록은 409
  `session_fenced` 다(§4.2).
- `rcm worker --check` 와 `rcm doctor` 는 세션을 만들지 않는다(F46). `--once` 는 보통 워커와 같다.

### 4.8 요청자에게 보이는 것 (결정 276 · 278 · 273 · 274 · 327)

- 유예 중(T2)에 도는 잡의 사유는 새 `reason` 값 `worker_unreachable_grace` 다. 값을 더하기만 하므로 `schema_version` 은 1 을
  유지한다(`PLAN.md:140, 450`). `compute_queue` 가 그 잡의 `worker_name` 이 down 레인의 워커일 때 이 사유를 정한다.
  `_busy_reason` 순서에서 `worker_unreachable_grace` 를 맨 앞에 둔다. 취소 중이어도 그렇다. 취소가 닿지 못한다는 사실이 더
  행동에 쓸모 있기 때문이다. `estimate.finish_at` 은 null 이다.
- `ACTIONABLE_REASONS` 와 웹 `ACTIONABLE` 에는 `worker_down` 바로 뒤에 더한다. F29 · F58 의 잠근 시험은 같은 PR 에서 모두
  고친다.
- 레인 `down` 과 헬스의 `workers_down`/503 은 그대로다(F22). 3판 r1 의 새 레인 상태 `unreachable` 은 만들지 않는다(3판 r2
  A-M1).
- 사람용 문구는 웹, `rcm top`, `rcm wait` stderr 에 「워커와 연락이 끊김 — 유예 안에 돌아오면 이어 감」 같은 꼴로 쓴다. 원문
  코드는 드러내지 않는다. 문구는 PR3 에서 korean-writer 와 i18n 으로 확정한다.
- `lost` 의 다섯 끝(RL29)은 `summary_code` 로 구별한다. `worker_unreachable` 은 유예 만료이고 오늘 있는 코드다.
  `worker_self_stopped` 와 `worker_replaced` 가 있다. `worker_restarted_without_job` 은 재시작 뒤 이어받기이고 오늘 있는
  코드다. `orphan_unverified` 는 유예가 끝났는데 그 잡이 되돌릴 수 없음 표식이고 고아 프로세스를 확인하지 못한 경우다.
  이때는 「같은 일이 아직 돌고 있을 수 있다, 그 머신을 확인하라」 같은 경고 문장을 보인다(결정 327). 다섯 모두 종료 3 이고
  `failed_step` · `last_step` 이 없다(`PLAN.md:220`). 번역은 PR1 이 더한다(F28).
- `late_result` 는 JSON 과 `rcm wait` · `rcm status` 사람용 줄 한 줄로 보인다. 문구는 「늦게 온 결과: succeeded — 이 잡의
  결과로 세지 않음」 같은 꼴이다. 웹은 그리지 않는다(§1.3).
- `log_complete = false` 는 `rcm wait` 사람용 줄, `rcm logs` stderr 끝, 웹 로그 보기에 한 줄로 보인다.

### 4.9 DB v26 (결정 265 · 271 · 273 · 327, PR1 이 올린다)

| 표 | 칸 | 뜻 | 쓰는 PR |
|---|---|---|---|
| `worker_sessions`(새) | `id TEXT PRIMARY KEY` | 세션 id | PR1 |
| | `worker_name TEXT NOT NULL` | 토큰 이름 | PR1 |
| | `registered_at REAL NOT NULL` | 첫 등록 | PR1 |
| | `fenced_at REAL` | 펜싱된 시각(NULL = 현재) | PR1 |
| | `fenced_by TEXT` | 펜싱한 세션 id | PR1 |
| | `orphan_unverified INTEGER NOT NULL DEFAULT 0` | 다음 프로세스가 이 세션의 고아를 확인하지 못했다고 알림(`unverified_sessions`) | PR1 |
| | 색인 `(worker_name, fenced_at)` | 현재 세션 찾기 | PR1 |
| `jobs` | `worker_session_id TEXT` | claim 한 세션(NULL = 로컬 또는 레거시) | PR1 |
| `jobs` | `irreversible INTEGER` | claim 때 얼린 표식(NULL = 로컬 · 레거시) | PR1 |
| `jobs` | `disconnect_grace_seconds INTEGER` | claim 때 얼린 유예 | PR1 |
| `jobs` | `log_offset INTEGER` | 받은 원본 스트림 바이트(오프셋 기준) | PR2 가 씀 |
| `jobs` | `log_file_bytes INTEGER` | 가린 뒤 커밋된 `log.txt` 길이 | PR2 가 씀 |
| `jobs` | `log_complete INTEGER` | 1/0/NULL(0 은 짧은 파일에서 미리 고정될 수 있다, T16) | PR2 가 씀 |
| `events` | kind `late_result`(스키마 변경 없음) | 늦은 결과 | PR2 |

- 마이그레이션은 `_MIGRATIONS[26]` 에 위 DDL 을 둔다. 옛 행은 전부 NULL 이다. 로컬 잡이거나 레거시 잡이라는 뜻이다(T11).
  백업은 기존 경계에서 한다(F50). 옛 빌드는 v26 DB 를 거절한다(F50). M5t-c 는 v27 이다(§9, M5t-c 결정 299).
- 3판에서 뺀 것: `log_raw_acked` 는 이름을 `log_offset` 으로 바꿨다. `log_bytes_total` 은 finish 본문의 `log_bytes` 로만
  받고 저장은 `log_complete` 판정 하나로 한다. `log_incomplete` 는 긍정형 `log_complete` 로 바꾸고 null 을 「모름」으로
  둔다. `lease_id`, `worker_identity`(M5t-c 몫), `workers` 의 세션 칸도 뺐다.

### 4.10 얼어붙는 계약 (머지 = 동결, workplan §6)

명세가 머지되면 아래 계약이 얼어붙는다.

| 계약 | 바뀌는 것 |
|---|---|
| `schema_version`(`/api/status`) | 1 그대로. 더하기만: 도는 행 `reason` 값 `worker_unreachable_grace`, 최근 행 · 잡 문서 `log_complete`, 종료 잡 문서 · wait JSON `late_result` |
| DB | v25 → v26(§4.9) |
| 워커 프로토콜(아래 표) | 2 |
| 종료 코드 | `rcm worker`: 0 에 「펜싱됨」, 새 3 「프로토콜 불일치」. `rcm wait` 0~5 그대로 |
| CLI 플래그 | 없음 |
| 설정 키 | `[server] disconnect_grace_seconds`(정수, 기본 600, `max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 15) + 35 ≤ 값 ≤ 86400`, 위반은 기동 실패 종료 2 + 키 이름 · 하한 문장, `[server]` 라 reload 불가 F20). 프리셋 `irreversible`(bool, 기본 false, reload 가능, claim 때 얼림). 워커 `grace_seconds` 는 키 · 검증 그대로(표식 잡에서만 그 잡에 맞춰 줄여 씀, §4.3) |
| 헬스 기능 키 | 없음 |
| 요약 코드 | `worker_self_stopped` · `worker_replaced` · `snapshot_unreachable` · `orphan_unverified`(새) |
| 워커 데이터 폴더 | `worker-sessions/<S>/` 의 `lock` · `owner.json` · `ok_at.json` · `jobs/<id>/claim.json` · `proc.json`(PR1) · `stream.log` · `outbox.json`(PR2) — 다음 판 워커가 읽으므로 모양을 얼린다 |

워커 프로토콜 2 는 아래와 같다.

| 라우트 | 요청에 더함 | 응답에 더함 | 새 오류(`code`) |
|---|---|---|---|
| `POST /worker/register` | `worker_session_id`(필수, 32자 소문자 hex), `worker_protocol`(2), `previous_sessions`(세션 id 목록, 없으면 []), `unverified_sessions`(세션 id 목록, 없으면 [], 결정 327) | `worker_protocol: 2` | 409 `protocol_required`(+`worker_protocol: 2`), 409 `session_fenced`. 모양이 틀리면 400 |
| `GET /worker/spec` | — | `worker_protocol: 2` | — |
| `POST /worker/claim` | `worker_session_id` | `job.irreversible`, `job.disconnect_grace_seconds`(최상위 키는 그대로) | 409 `protocol_required` · `session_fenced` · `not_registered`(오늘 문구 유지 + `code`). 펜싱된 세션의 잡이 그 레인을 잡고 있으면 204 |
| `POST /worker/heartbeat` | `worker_session_id`(레거시 busy 잡이 있는 이름은 없어도 받음) | `closed_jobs: [{id, state, had_cancel}]`(결정 321 · 322) | 409 `protocol_required` · `session_fenced` · `not_registered` |
| `POST /worker/capabilities` | `worker_session_id` | — | 같음 |
| `POST /worker/jobs/{id}/phase` | `worker_session_id` | — | 409 `session_fenced` · `job_closed`(+`state`) |
| `POST /worker/jobs/{id}/log` | 헤더 `X-RCM-Worker-Session`, `X-RCM-Log-Offset`. 빈 본문 = 오프셋 질의 | `offset` | 409 `log_gap`(+`expected_offset`) · `job_closed` · `session_fenced`, 503 `log_write_failed` · `database_busy`(오늘 코드). 헤더 형식 오류 400(`error` 만) |
| `GET /worker/jobs/{id}/tree` · `PUT`/`GET …/artifacts` | 헤더 `X-RCM-Worker-Session` | — | 409 `session_fenced` · `job_closed`. 헤더 형식 오류 400 |
| `POST /worker/jobs/{id}/finish` | `worker_session_id`, `log_bytes`, `summary_code ∈ LOST_CODES`(outcome lost) | — | 409 `job_closed`(+`state`, `late_result_recorded`) · `session_fenced` |

모든 409 는 오늘의 `error` 문구를 남기고 `code` 를 더한다. 옛 워커가 문구로 맞추는 `not registered` 를 지키려는
것이다(`remote_worker.py:773`). `protocol_required` 의 문구에는 「not registered」가 들어가지 않는다(§4.7).

### 4.11 기존 결정과의 관계

| 기존 | 이 명세 |
|---|---|
| PLAN 「잡 모델」 `lost(서버 재시작 중 죽음)`(`PLAN.md:89`)·「잡은 서버 재시작을 넘어 살아남는다」(`PLAN.md:121`)·저장소 시작 정리(`PLAN.md:293`) | 원격 잡의 `lost` 는 끊김 유예 만료 · 자기 멈춤 · 펜싱 · 재시작 이어받기로 생긴다고 PR0 이 PLAN 문장을 더한다(§5 PR0 행) |
| PLAN M5b-2 「timeout → down · 잡 lost · 재등록 = 옛 잡 lost」(`PLAN.md:600`) | 바꾼다: timeout → down 만, 잡은 유예, 재등록은 명시한 이전 세션만. PR0 이 그 줄에 「M5t-b 가 바꿈」 을 단다 |
| `rcm wait` 3 = 모름(`PLAN.md:118`) | 그대로. 다섯 끝 모두 3 |
| `cancelled`·`lost` 에 스텝 없음(결정 64, `PLAN.md:220`) | 그대로 |
| M5n §13 자동 재실행 안 함(`docs/m5n-workplan.md:652`) | 그대로. `late_result` 도 다시 돌리지 않는다 |
| M5q 드레인 「도는 잡은 하나도 잃지 않는다」(`remote_worker.py:10`) | 강화: 보고 큐가 exec 를 넘는다 |
| 결정 254 `local_only` | 별개 키(결정 257). 둘 다 달 수 있고 `local_only` 면 `irreversible` 은 효과 없음 |
| M5e 산출물 「결과와 무관」 | 그대로. `ready` 조건만 좁힘(결정 275) |
| PLAN 「fail-open 금지」 「순수 계산 모듈은 … `scripts/mutcheck.py` 로 확인」(`PLAN.md:528`) | 2판이 더한 규칙마다 변이(§7.4, V1-10) |
| 부모 명세 3판 M8 「서비스가 되살리지 않는 종료 코드」 | M5t-c 결정 303 이 「펜싱 · 확실한 신원 거절」로 좁혔다(F52). 이 명세의 0 은 펜싱 |

## 5. 작업 분할과 병렬

PR 하나에 브랜치 하나, 워크트리 하나다. 같은 물결의 PR 은 동시에 진행한다. 공유 파일(`shared`)에는 더하기만 한다. 뒤에
머지하는 PR 이 dev 를 합친 뒤 맞춘다. 레인 추천의 근거는 workplan 스킬의 「Lane」 조건(DB, 와이어, 동시성, 웹 UI)이다.

| PR | 브랜치 | 내용 | 시작 조건 | 물결 | 같이 도는 것 | owns | shared | 겹치는 파일과 머지 순서 | lane |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5tb-result-loop-workplan` | 이 명세 + PLAN.md 편집 일곱 자리: ① `PLAN.md:1` 머리 판 `(v2.13, 2026-11-25)` → `(v2.14, <머지 날짜>)` ② `PLAN.md:14` 뒤에 `> **v2.14**` 문단(M5t-b 결과 고리 · 결정 256~279 · 320~329 · DB v26 · 워커 프로토콜 2 · 명세 `docs/m5tb-workplan.md`) ③ `PLAN.md:89` 잡 모델 그림 `lost(서버 재시작 중 죽음)` 에 원격 잡의 `lost` 원인(유예 만료 · 자기 멈춤 · 펜싱 · 재시작 이어받기) ④ `PLAN.md:121` 「잡은 서버 재시작을 넘어 살아남는다(… `running`·`cancelling` 이던 것은 `lost`)」 를 로컬 레인 잡으로 한정, 원격 잡은 끊김 유예 ⑤ `PLAN.md:293` 시작 정리 문장에 같은 한정 ⑥ `PLAN.md:600` M5b-2 줄의 「timeout → down · 잡 lost · 재등록 = 옛 잡 lost」 에 「M5t-b 가 바꿈: timeout → down 만 · 잡은 유예 · 재등록은 명시한 이전 세션만」 ⑦ `PLAN.md:628` M5t-b 항목 「(명세 예정)」 → 명세 경로 · 결정 번호 · DB v26 · 한 문장, `PLAN.md:753` 결정 색인 「248 a~d · g 와 250 은 M5t-b 명세에 … 249 는 두 명세에 나눠 실을 예정」 → 「256~279 · 320~329 는 `docs/m5tb-workplan.md` §11 에 있다. 248 a~d · g · 249 의 v26 부분 · 250 은 그 §11 대응표가 이어받았다」(M5t-c 몫 문장은 M5t-c PR 이 고침) | — | 0 | M5t-c 명세 PR #269 | `docs/m5tb-workplan.md` | `PLAN.md`(위 일곱 자리. M5t-c 명세 PR 도 `:1` · `:629` · `:753` 를 고친다) | M5t-c 명세와 PLAN 머리 · 색인 줄이 겹침 → 먼저 머지한 쪽 뒤에 다른 쪽이 dev 를 합쳐 다시 맞춤(판 번호는 뒤에 머지하는 쪽이 v2.15) | short — 문서만, `src/` 변경 0 |
| PR1 | `feat/worker-session-fencing-grace` | §4.1~§4.4(7 의 큐 보내기 빼고) · §4.7: DB v26 전부(`orphan_unverified` 칸 포함), 세션 · 펜싱 · 좁은 확인 · register 순서(`pair_proof` 자리 비워 둠), `_heard_lock` · `heard` · 서버 유예(단조) · 취소 미확인 범위, 얼림, heartbeat `closed_jobs`(`had_cancel`), 감시 스레드 · m · G 하한 검증 · 표식 잡 grace 상한, 세션 폴더(`lock` · `owner.json` · `ok_at.json` · `claim.json` · `proc.json`), Linux `/proc` · `ps` 신원, 고아 정리 · 재확인 · `previous_sessions` · `unverified_sessions` · `orphan_unverified`, 프로토콜 2 · 등록 전 확인(폴더는 검사 뒤) · 종료 0/3 · `protocol_required` 문구, 레거시 호환, `LOST_CODES` · `orphan_unverified` 와 그 번역, 설정 키 둘, 문서(`docs/configuration.md` 1185 · 1386 근처, `docs/operating.md:786`, 템플릿 둘), 오늘 계약 시험 고쳐 쓰기(§7.3). 로그는 오늘처럼 덧붙이되 세션 헤더만, 못 보낸 보고는 오늘처럼 메모리 | PR0 머지 **그리고** M5t-a PR1~PR7 전부 dev 머지(= v0.11.0 에 들 내용 전부, F54 — PR3 · PR4 · PR6 이 `config.py` · `capabilities.py` · `server.py` · `cli.py` · `docs/configuration.md` · `docs/operating.md` 를 고친다) | 1 | — | `store.py` `remote_workers.py` `server.py` `capabilities.py` `config.py` `core/model.py` `core/outcome.py` `client.py` `remote_worker.py` `runner.py`(관찰자 `spawned`) `cli.py`(cmd_worker) `web/i18n.js`(outcome 항목) `templates/server.toml` `examples/server.toml` `docs/configuration.md` `docs/operating.md` `tests/tcp_relay.py`(새) `tests/test_worker_session_m5tb.py`(새) `tests/test_worker_selfstop_m5tb.py`(새) `tests/test_e2e_worker_grace_m5tb.py`(새) `tests/test_worker_lost.py` `tests/test_e2e_worker.py` + 세션을 싣게 고치는 워커 시험 도우미 | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py`(새, PR1 이 만들고 뒤 PR 이 더함) `tests/test_examples.py` `tests/test_worker_api.py`(register `:542-549` · heartbeat `:1204` 잠금에 키 더하기) `tests/test_worker_client.py`(`:183-190`) `tests/test_config.py`(`:905`) `tests/test_outcome.py`(코드 ↔ 번역 잠금 `:543-569`) | M5t-a 는 시작 전에 다 머지돼 있어 겹침 없음. M5t-c 의 register(`pair_proof`) · DB v27 은 이 PR 뒤(M5t-c 결정 299) | full — DB v26 · 워커 프로토콜 · 동시성(heartbeat · claim · janitor · 신호) |
| PR2 | `feat/worker-log-offset-outbox` | §4.4 의 7(죽은 세션 D 의 큐 · 응답 표) · §4.5 · §4.6: 스트림 스풀 · `outbox.json`(`acked`) · 보내는 스레드 · 순서 있는 큐 · 빈 본문 질의 · 응답표 · finish 상한 · 재전송 창, 서버 로그 쓰기 절차(잡별 잠금 · 자리 쓰기 · 되자르기 · 짧은 파일 · 헤더 400) · 기동 자르기, `log_complete` · `late_result`(트랜잭션) · `finished_at` 자르기 · 산출물 재업로드와 `ready` 조건(파일까지), 준비 단계 `snapshot_unreachable`, M5q exec 뒤 큐 이어 보내기, 문서(`docs/configuration.md` 의 원격 워커 로그 절) | PR1 머지 | 2 | PR3 | `store.py` `remote_workers.py` `remote_worker.py` `client.py` `server.py`(log 라우트 · `_terminal_view` · 기동 자르기) `core/status.py`(`recent_json`) `docs/configuration.md` `tests/test_worker_log_offset_m5tb.py`(새) `tests/test_worker_outbox_m5tb.py`(새) `tests/test_e2e_worker_outbox_m5tb.py`(새) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py` `tests/test_status_schema.py`(`RECENT_KEYS` `:91`) `tests/test_status_outcome.py`(`NEW_RECENT` `:162`) | PR3 과 src 파일 안 겹침. PR2 먼저 머지 | full — 와이어(log 라우트 · finish) · 동시성(잡별 잠금 · 보내는 스레드) · `/api/status` 키 |
| PR3 | `feat/queue-show-unreachable-worker` | §4.8: 사유 `worker_unreachable_grace` · ETA null · `ACTIONABLE` 두 목록과 잠근 시험 전부(F58), 문구(웹 · `rcm top` · `rcm wait`), `log_complete`=false 와 `late_result` 의 사람용 줄(`rcm wait` · `rcm status` · `rcm logs`) · 웹 로그 보기 줄, 문서(`docs/operating.md` 의 「워커와 연락이 끊겼을 때 화면」) | PR1 머지 | 2 | PR2 | `core/model.py`(사유 상수 · `ACTIONABLE_REASONS`) `core/queue.py` `core/render_text.py` `cli.py`(wait · logs 사람용 줄) `src/remote_ci_monitor/web/app.js` `web/i18n.js`(reason · 로그 불완전 문구) `docs/operating.md` `tests/test_queue_unreachable_m5tb.py`(새) `tests/web/unreachable_m5tb.test.js`(새) `tests/test_action_marker_m5s.py`(`:139-141`) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py` `tests/test_queue_m5n.py`(`:135`) `tests/test_render_m5n.py`(`:199`) `tests/web/stall.test.js`(`:192-194`) `tests/web/m5n.test.js`(`:38-41`). `tests/test_web_schedule_m5s.py:43` 은 웹 = 모델 대조라 고칠 것 없이 같이 돈다 | PR2 뒤 머지(RL28 · RL29 의 e2e 가 PR2 키를 읽는다. 빌드는 병렬, 머지 전 dev 합쳐 다시 돌림) | full — 웹 UI |
| PR4 | `test/acceptance-worker-result-loop-live` | 실기 수용: 하네스 `scripts/acceptance/m5tb_live.sh`(시험 서버 · 가짜 `sh -c` 표식 프리셋 · 표준 라이브러리 TCP 중계), §7 e2e 행의 실기 확인(RL1 · RL2 · RL10 · RL15 · RL34 · RL40 우선), 보고서 `docs/acceptance/reports/<날짜>-m5tb-acceptance.md`. Linux 머신이 있으면 RL15 · RL31 의 `/proc` 신원을 그 머신에서 한 번 | PR3 머지 | 3 | — | `scripts/acceptance/m5tb_live.sh`(새) `docs/acceptance/reports/…`(새) | `CHANGELOG.md` | — | short — `src/` 변경 0 |

- PR1 을 더 쪼개지 않는다. dev 의 e2e 는 같은 트리의 서버와 워커를 함께 띄운다. 서버만 세션을 요구하면 워커 쪽이 없는 동안
  e2e 가 빨갛다. 거꾸로 하면 「선택」을 「필수」로 뒤집는 셋째 PR 이 필요하다. 그래서 이 안은 거절했다.
- 디스크 큐를 PR1 로 당기지 않는다. PR1 이 이미 DB, 프로토콜, 감시 스레드를 담아 가장 크다. V1-2 에는 다른 답을 냈다.
  큐가 필요한 행(RL12 b · RL14 a · RL39 · §4.4 의 7)을 PR2 로 옮겼다(§7.2).
- dev-batch 로 묶는 것은 물결 2 의 {PR2, PR3} 이다. `tools/dev_batch.py` 로 owns 겹침이 0 인지 확인한다. 공유 파일에는
  더하기만 한다.
- M5t-c 와의 순서: M5t-c 의 register `pair_proof` 검사는 §4.2 의 4 번에 들어간다. 버전과 프로토콜 검사 뒤, 부작용
  앞이다(F52). DB v27 은 PR1 의 v26 뒤에 온다(M5t-c 결정 299).

## 6. 진행 체크리스트

상태 칸은 PR 이 머지될 때마다 메인 세션이 채운다.

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 이 명세 작성(1판 · 2판) | PR0 | — | [ ] |
| 1 | 격리 검증 라운드(HIGH 0 까지), §12 기록 | PR0 | 0 | [ ] |
| 2 | 결정 페이지: 위임 질문(메인 세션 + Codex, 오너 위임) | — | 1 | [ ] |
| 3 | PR0 머지(PLAN.md 일곱 자리 반영 포함) | PR0 | 1 · 2 | [ ] |
| 4 | M5t-a PR1~PR7 전부 dev 머지 확인(v0.11.0 에 들 내용) | — | — | [ ] |
| 5 | PR1 세션 · 펜싱 · 유예 · 감시 스레드 · 닫힌 잡 통보 · 세션 폴더 · 고아 · 프로토콜 · DB v26 | PR1 | 3 · 4 | [ ] |
| 6 | PR2 로그 쓰기 절차 · 보고 큐 · 죽은 세션 큐 · finish 상한 · 늦은 결과 · 산출물 | PR2 | 5 | [ ] |
| 7 | PR3 화면 사유 · 사람용 줄 · 잠근 시험 목록 | PR3 | 5 | [ ] |
| 8 | PR2 머지 → PR3 dev 합쳐 RL28 · RL29 다시 돌린 뒤 머지 | PR2 · PR3 | 6 · 7 | [ ] |
| 9 | 새 변이 26개(§7.4)가 `scripts/mutcheck.py` 에 들어갔는지 | PR1 · PR2 · PR3 | 5 · 6 · 7 | [ ] |
| 10 | PR4 실기 수용과 보고서 | PR4 | 8 | [ ] |
| 11 | M5t-c 명세의 DB v27 이 v26 위인지 확인 | — | 5 | [ ] |
| 12 | 결정 번호 320~339 를 M5t-c 세션과 조율(M5t-c 는 304 부터) | — | 0 | [ ] |
| 13 | CHANGELOG `[Unreleased]` 정리 | PR4 | 10 | [ ] |
| 14 | main 릴리스 · 버전업(메인 세션, 오너가 목표에 넣어 승인 2026-11-26) | — | 13 | [ ] |
| 15 | 운영 반영(큐가 빌 때, 메인 세션) | — | 14 | [ ] |
| 16 | 앱 프로젝트에 `irreversible` 달기 — 그 프리셋을 읽는 서버가 모두 이 기능 버전 이상인지 확인한 뒤(F23) | — | 15 | [ ] |

## 7. 시험 계획

### 7.1 시나리오 표

아래 용어는 표에서만 쓴다. G 는 `disconnect_grace_seconds` 이고 시험 서버는 `worker_timeout_seconds` 보다 조금 큰 값으로
줄여 쓴다. 표식 프리셋은 `irreversible = true` 를 단 프리셋이고 일반 프리셋은 표식이 없는 프리셋이다. 분단은 서버는 살아
있고 워커 요청만 응답이 없는 상태이며 TCP 중계를 멈춰 만든다. 서버 정지는 연결 거부다. 세션은 워커 프로세스 한 번이다.

<!-- scenario-prefix: RL -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| RL1 | [전이 T2·T3·W3 · 253 · 320] 표식 프리셋(가짜 `sh -c sleep`)의 잡이 원격 워커에서 `running`. 분단(중계가 응답을 붙잡아 heartbeat 이 매번 요청 시한까지 매달림)이 G 를 넘겨 이어진다 | 워커의 감시 스레드가 `ok_at + G − m` 뒤 첫 틱에 잡의 프로세스 그룹을 끝낸다(pid 소멸). 서버가 `lost` 로 닫기 전에 이미 죽어 있다(워커 로그의 멈춤 시각 ≤ DB `finished_at`, 차이 ≥ 0). 연결이 돌아온 뒤 최종 상태는 `lost` 하나, `rcm wait` 종료 3, `failed_step` · `last_step` 없음, `summary_code` 는 `worker_unreachable`(서버가 먼저) 또는 `worker_self_stopped`(보고가 먼저). 근거: 결정 253 · 320 · `PLAN.md:118, 220` | e2e: 워커 프로세스 + TCP 중계 정지, `os.kill(pid,0)`, DB `jobs`, 종료 코드 | PR1 |
| RL2 | [전이 T3·W3 · 253] RL1 과 같은 잡. 이번에는 서버 정지(재배포)가 G 를 넘겨 이어진 뒤 서버가 다시 뜬다 | 워커는 서버 정지도 끊김으로 보고 `G − m` 에 스스로 멈춘다(서버가 꺼진 채로 pid 소멸). 서버가 뜬 뒤 그 잡은 `lost` 한 번, 종료 3. 서버가 꺼져 있던 동안은 유예로 세지 않으므로 기동 직후 틱이 닫지 않고, 워커가 돌아와 보낸 `worker_self_stopped` 가 닫거나 기동 + G 에 `worker_unreachable` 로 닫힌다. 같은 잡을 두 번 닫거나 사유를 덮어쓰지 않는다. 근거: 결정 253 · 266 | e2e: 서버 stop/start, pid, DB, 종료 코드 | PR1 |
| RL3 | [전이 T2·T3 · 253] RL1 의 분단 중(G 안과 밖 각각) 같은 `concurrency_group` 으로 새 제출을 낸다(supersede 켜진 경우 포함) | 첫 잡이 `lost` 로 닫히기 전에는 둘째 잡이 `running` 이 되지 않는다(`queued`, `blocked_by_group`). supersede 면 첫 잡은 `cancelling` 으로 남아 그룹을 잡는다. 둘째가 시작하는 시각에 첫 잡의 프로세스는 이미 없다. 두 프로세스가 동시에 살아 있던 구간 0. 근거: 결정 253 · `PLAN.md:127` | e2e: 두 잡의 pid 생존 구간 비교, `jobs.state` | PR1 |
| RL4 | [전이 T2/T3 경계 · 253 · 266 · 328] G 만료 경계(주입 시계로 ±1 틱, 여러 번 반복)에서 분단이 풀려 서버 janitor 의 만료 판정과 워커의 첫 heartbeat · 자기 멈춤이 겹친다. 표식 · 일반 각각 | 잡의 최종 상태가 정확히 하나다. 표식은 (결과 확정 또는 `lost`, 프로세스 종료), 일반은 (결과 확정 또는 `lost`, 프로세스는 계속). `lost` 가 된 뒤 같은 잡이 `succeeded` 등으로 뒤집히지 않는다(늦은 finish 는 409 `job_closed`, PR2 부터 `late_result` 1건). 표식 프리셋에서 (`lost`, 프로세스 생존) 조합이 경계 어느 쪽에서도 나오지 않는다. 근거: 결정 253 · 256 · 266 · `PLAN.md:118` | 단위: 서버 `mono_fn` · 워커 시계 주입, 경계 반복, DB + pid 상태 | PR1 (상태 하나 · 뒤집히지 않음) · PR2 (`late_result`) |
| RL5 | [전이 T3·W4 · 253 · 256] 일반 프리셋의 원격 잡이 `running`, 연결이 G 를 넘겨 끊긴다 | 서버는 G 에 `lost`(`worker_unreachable`), `rcm wait` 종료 3(1 아님). 워커는 잡을 죽이지 않고 계속 돌린다(pid 생존, 워커 로컬 `log.txt` 증가). 재연결 뒤 로그 조각은 409 `job_closed` 이고 프로세스를 죽이지 않는다. 끝나면 finish 를 보내고 서버는 잡 상태를 바꾸지 않은 채 `late_result`(outcome · exit_code · finished_at · received_at · log_complete)를 잡 문서에 한 번 남긴다. 근거: 결정 253 · 256 | e2e: pid 생존, 워커 로컬 로그, DB, 종료 코드, `GET /jobs/{id}` 의 `late_result` | PR1 (유예 · 계속 돎) · PR2 (`late_result`) |
| RL6 | [전이 T1·T2 · 기준선] (a) 끊김 없는 원격 잡의 성공 · 실패 · 시간초과 · 취소 각 한 번 (b) 분단을 `worker_timeout_seconds` 보다 길고 G 보다 짧게 둔 뒤 푼다(표식 · 일반 둘 다) | (a) 종료 코드 계약 그대로: 성공 0 · 실패 1 · 시간초과 2 · 취소 2, wait JSON 의 기존 키 불변(더한 키만 있음). (b) 잡은 끊긴 내내 `running` 이고 `worker_timeout_seconds` 에 `lost` 가 되지 않는다. 같은 세션으로 이어 보고하고 프로세스는 안 죽는다. 서버 `jobs/N/log.txt` 의 sha256 이 워커 스트림 스풀과 같다(중복 · 빈틈 없음). 근거: 결정 253 · `PLAN.md:118` | e2e: TCP 중계 정지/재개, sha256, 종료 코드, wait JSON | PR1 (상태) · PR2 (로그 sha) |
| RL7 | [서버 정지 T1·T3] 원격 잡과 로컬 레인 잡이 같이 `running`. 서버를 (a) `worker_timeout_seconds` 초과 G 미만 (b) G 초과로 정지했다가 올린다. 그동안 `rcm wait` 하나가 대기 중 | (a) 재기동 후 첫 janitor 틱(5초)과 그 뒤에도 원격 잡은 `running` 이고 워커의 finish 가 확정되어 종료 코드가 실제 결과다. (b) 유예는 서버 기동 시각부터 다시 센다. 기동 직후 틱에서 원격 잡이 한꺼번에 `lost` 가 되지 않고, 워커가 끝내 안 돌아오면 기동 + G 에 `lost`. 로컬 레인 잡만 `lost`(`server restarted`)인 것은 오늘과 같다. 정지 중이던 `rcm wait` 은 오늘 계약대로 연결 실패 60초 뒤 종료 3이고, 복귀 뒤 `rcm wait --job N` 은 실제 결과 코드를 낸다. 근거: `store.py:3878-3909` · `PLAN.md:121, 457` · 결정 266 | e2e: 서버 stop/start(`tests/test_e2e_worker.py:857` 확장) + 단위: `mono_fn` 기동 바닥, DB | PR1 |
| RL8 | [취소 T4 · W4 · 유예 · 322] 분단 중 요청자가 `rcm cancel N`(또는 같은 그룹 제출로 supersede)을 한다. (a) G 안에 연결 복귀 (b) 복귀 없이 G 경과, 그 뒤 연결 복귀. 표식 · 일반 각각 | 요청은 접수되어 `cancelling`. (a) 첫 heartbeat 응답의 `cancel` 로 프로세스가 끝나고 `cancelled`(종료 2, supersede 면 `superseded by #N` 유지). (b) `lost`(`worker_unreachable`, 종료 3)이고 `cancelled`(2)나 `cancel_unconfirmed` 로 위장하지 않는다. `kill_at + 2 × heartbeat` 규칙은 조용한 세션에는 걸리지 않는다. 표식 프리셋은 G 전에 워커가 스스로 멈춘다. 일반 프리셋은 복귀 뒤 첫 heartbeat 2xx 의 `closed_jobs` 에 `had_cancel: true` 가 실려 워커가 그 잡의 프로세스 그룹을 멈춘다(pid 소멸) — 취소한 잡이 계속 돌지 않는다. 그 뒤 워커의 finish 는 `late_result` 로만 남고 상태는 `lost` 그대로. 근거: `tests/test_worker_lost.py:137` · 결정 253 · 256 · 322 · F61 | e2e + 단위: DB `state` · `cancel`, heartbeat 응답, 종료 코드, pid | PR1 (상태 · 멈춤) · PR2 (`late_result`) |
| RL9 | [전이 T5 · claim] 서버가 claim 을 확정했는데 응답이 유실된다(분단 · 워커 크래시 직전). 워커는 그 잡을 모른다 | 프로세스가 뜨지 않는다(pid 없음). 같은 세션이 재연결하면 두 번째 heartbeat 에 그 잡을 `lost`(`worker_restarted_without_job`, 종료 3)로 닫고 레인을 비운다(G 가 길어도 같다). 연결이 끝내 안 돌아오면 G 뒤 `lost`. 프로세스가 죽고 다시 시작했으면 이어받기 등록이 닫는다. 자동 재실행 없음. 근거: `remote_workers.py:916-942` · CHANGELOG.md:580-584 · `docs/m5n-workplan.md:652` | 단위: claim 직후 응답 유실 흉내 + heartbeat 두 번, DB | PR1 |
| RL10 | [펜싱 T8·W5 · 255 · 258 · 261] 프로세스 A(토큰 w1)가 잡 X 를 도는 중, 같은 토큰으로 프로세스 B 를 띄운다(A 는 서비스, B 는 수동). X 는 표식 · 일반 각각 | B 의 등록은 성공하고 B 가 그 이름의 유일한 현재 세션이다. A 의 다음 요청(heartbeat · log · phase · finish 어느 것이든)은 409 `code: session_fenced`(잡이 닫혔다는 `job_closed` 와 구별). A 는 X 의 프로세스 그룹을 SIGTERM→KILL 로 멈추고 좁은 확인 finish 를 보낸 뒤 종료 0. X 는 `lost`(`worker_replaced`, 종료 3)이고 A 가 `failed` · `cancelled` 로 보고하지 않는다. 확인 전까지 X 는 busy 라 그룹 자리가 안 풀리고 B 의 그 레인 claim 은 204(409 아님). 감독자(launchd `KeepAlive.SuccessfulExit=false`, systemd `Restart=on-failure`)가 A 를 되살려 B 를 되펜싱하는 핑퐁이 없다(1분 안 `register` 호출 ≤ 2). 확인이 없으면(A 분단) X 는 A 의 마지막 들음 + G 에 `lost`. 근거: 결정 255 · 258 · 261 · `cli.py:3507, 3535` | e2e: 두 프로세스 + 가짜 감독자, pid, DB, 등록 횟수 | PR1 |
| RL11 | [펜싱 T8·T14 · 255 · 329] 펜싱된 A(또는 표에 없는 세션)가 늦게 보낸 log · phase · finish(좁은 확인이 아닌 것) · 산출물 업로드 · capabilities. 별도로 같은 토큰의 프로세스 둘 이상을 같은 순간에 시작한다. 별도로 세션 헤더 · 오프셋 헤더의 형식이 틀린 요청 | 늦은 보고는 펜싱이면 409 `session_fenced`, 표에 없으면 409 `not_registered`(남의 토큰 403 과 구별)이고 잡 상태 · `log.txt` · 마커 · 산출물 · 능력 보고 · `late_result` 에 한 바이트도 반영되지 않는다. 동시 시작에서는 살아남는 세션이 정확히 하나다(둘 다 죽거나 둘 다 살지 않는다). 펜싱된 세션의 재등록은 409 `session_fenced`. 형식이 틀린 헤더는 400 이고 본문 키는 `error` 하나, 반영 0. 근거: 결정 255 · 329 · `docs/m5b2-workplan.md` 「종료 잡의 늦은 보고」 · 2차 Q-B | 단위(HTTP): 응답 코드 · `code`, `log.txt` sha256, DB | PR1 |
| RL12 | [세션이 아닌 것 T10·T9] (a) 같은 프로세스의 다른 레인이 `not_registered` 409 로 재등록 (b) M5q 버전 따라가기: 드레인 뒤 같은 PID 로 exec (c) 서비스 워커가 잡을 도는 중 같은 토큰으로 `rcm worker --check` · `rcm doctor --role worker` | (a) 같은 세션 id 재등록은 멱등: 다른 레인의 도는 잡을 닫거나 멈추지 않는다(결정 260). (b) 드레인 전에 보고한 잡은 그대로이고 못 보고한 잡은 디스크 큐에 남아 exec 뒤 새 이미지가 등록 전에 보낸다. exec 뒤 새 세션이 옛 세션을 `previous_sessions` 로 넘겨도 끝난 잡의 상태는 안 바뀐다. (c) 세션을 만들지 않아 도는 잡이 안 멈추고 서비스 워커의 heartbeat 가 409 를 받지 않는다. 근거: 결정 255 · 260 · `remote_worker.py:1-17` · `cli.py:4645` · F46 | e2e + 단위: DB, heartbeat 응답, 잡 pid | PR1 (a · c) · PR2 (b — 디스크 큐) |
| RL13 | [호환 T12 · 순서] 서버와 버전이 다른 프로세스(따라가기 전의 옛 설치)가 같은 토큰으로 `register` 한다. 현재 세션 A 가 잡을 도는 중 | 오늘 계약대로 409 + `server_version` 만 돌려준다. 세션 표 · 펜싱 · `lost` 어느 것도 일어나지 않는다. A 의 heartbeat 와 잡이 그대로 이어지고 `rcm wait` 결과가 안 바뀐다. 버전 검사가 프로토콜 · 세션 검사보다 먼저다. 근거: `remote_workers.py:390-398` | 단위(HTTP): 응답 본문, DB `jobs` · `worker_sessions` | PR1 |
| RL14 | [재시작 T9·W7·W8] 워커가 (a) 잡 X 를 끝내고 finish 를 디스크 큐에 썼으나 서버에 못 보낸 채 죽는다 (b) 잡 Y 를 도는 중 `kill -9` 로 죽는다. 같은 데이터 폴더 · 같은 토큰으로 다시 시작한다 | (a) 다시 시작한 워커가 등록 전에 X 의 finish 를 옛 세션 id 로 보내고 X 는 실제 결과로 확정된다. 등록이 X 를 먼저 `lost` 로 닫지 않는다(등록은 옛 세션의 큐가 끝난 뒤). (b) Y 의 고아 그룹이 정리되고 Y 는 `lost`(`worker_restarted_without_job`, 종료 3). Y 가 이미 유예 만료로 `lost` 였으면 등록이 그 요약을 덮지 않는다(`tests/test_e2e_worker.py:797` 계약). 둘 다 자동 재실행 없음. 근거: 결정 268 · 326 · `docs/m5n-workplan.md:652` | e2e: kill -9 후 재시작, DB, 종료 코드 | PR1 (b) · PR2 (a — 디스크 큐) |
| RL15 | [고아 T9 · 268 · 327] 표식 프리셋의 잡 프로세스 그룹이 도는 중 워커를 `kill -9` 한다(자식은 고아). 같은 데이터 폴더로 새 프로세스를 시작한다. 별도로, 기록된 pid 가 이미 다른 프로세스에 재사용된 경우. 신원은 Linux 의 `/proc/<pid>/stat` 시작 시각과 그 밖의 `ps -o lstart=` 둘 다 | 새 프로세스는 claim 을 시작하기 전에 죽은 세션의 프로세스 그룹을 정리한다(고아 pid 소멸). 시작 시각이 다른(재사용된) pid 는 건드리지 않는다(무관한 프로세스가 살아 있다). 정리 결과가 워커 로그에 남는다. `/proc` 읽기는 가짜 stat 파일(괄호 · 공백이 든 이름 포함)로 22번째 칸을 맞게 읽는다. 근거: 결정 268 · 327 · 253 · F59 | e2e: pid 생존 확인, 워커 로그 + 단위: 가짜 `/proc` stat | PR1 |
| RL16 | [로그 · 오프셋 · 329] (a) 서버는 조각을 받았는데 응답이 유실돼 워커가 같은 조각을 다시 보낸다 (b) 요청이 유실돼 워커의 다음 조각 오프셋이 서버보다 앞선다(흉내) (c) 서버 재시작 직후, 썼지만 커밋 전에 죽은 조각 (d) 워커가 재시작해 디스크의 `acked` 로 이어 보낸다 | 서버 `log.txt` 가 워커 스트림 스풀과 바이트 단위로 같다(가릴 값이 없을 때 sha256 일치, 중복 · 빈틈 · 순서 뒤바뀜 없음). 이미 받은 범위는 무시한다. 빈틈이면 409 `log_gap` + `expected_offset` 이고 워커는 프로세스를 죽이지 않고 그 오프셋부터 다시 보낸다. (c) 기동 때 `log.txt` 를 `log_file_bytes` 로 자르고 재전송이 다시 채운다. (d) 그 프로세스의 첫 로그 요청이 빈 본문 오프셋 질의이고 0 부터 다시 보내지 않는다(요청 본문 바이트 합 = 남은 바이트). 근거: 결정 271 · 329 · `remote_workers.py:630-659` · `remote_worker.py:157-160` | 단위 + e2e: sha256 비교, 워커 로그, pid 생존, 보낸 바이트 수 | PR2 |
| RL17 | [로그 · 줄 끝] (a) 마지막 줄에 `\n` 이 없는 채로 잡이 끝난다 (b) `::rcm::step::` 마커 줄 직후 서버가 재시작된다 (c) `\r` 진행 표시가 줄 끝 없이 수백 KiB 이어진다 | (a) 그 바이트가 잡 종료 뒤 `rcm logs N` 에 남는다(유실 0, 러너가 `\n` 을 붙인다). (b) 워커가 보낸 조각은 모두 `\n` 으로 끝나 마커가 한 줄로 파싱되고 `steps` · `failed_step` 이 정확하다. (c) 서버 메모리 · 조각이 줄 끝 없이 늘지 않고(한 줄 ≤ 128 KiB) 바이트는 결국 모두 도착한다. 실시간 tail 에 늦게 보이는 정도는 문서에 적는다. 근거: `runner.py:345-356, 379-385` · F35 실측 | 단위 + e2e: `rcm logs`, wait JSON `steps`, 조각 끝 바이트 | PR2 |
| RL18 | [로그 · 비밀] 서버가 아는 릴리스 비밀이 로그에 섞인다. (a) 한 조각 안 (b) 조각 경계 근처 (c) 재전송으로 같은 조각이 두 번 (d) 가리기로 길이가 달라진 뒤의 오프셋 | 서버 `log.txt` · `rcm logs` · SSE · 웹 로그 어디에도 비밀 원문이 없다(검색 0건). 조각은 줄 경계에서 잘려 한 값이 두 요청에 걸치지 않는다. 오프셋은 가리기 전 원본 바이트 기준이라 (d) 이후의 재전송도 중복 · 빈틈 없이 맞는다(`log_offset` 과 `log_file_bytes` 가 다르다). 근거: 되돌릴 수 없는 결과(비밀 노출) · `remote_workers.py:635-637` · 결정 271 | 단위 + e2e: 비밀 표지 문자열 검색(log.txt · SSE · `rcm logs`) | PR2 |
| RL19 | [로그 · 재전송 상한 · 324] 서버가 로그와 finish 를 계속 못 받는다(5xx · 서버 디스크 가득 503 `log_write_failed` · 분단). (a) 잡이 도는 중 (b) 잡이 끝난 뒤 | (a) 도는 중에는 횟수로 포기하지 않는다. 분단 5분 동안 재시도가 수십 번 쌓여도 재연결 뒤 로그가 완전하다. (b) 잡이 끝난 뒤에만 상한이 걸린다: 로그 · 산출물 단계가 HTTP 5xx 를 받는 중이면 300초 뒤 finish 가 먼저 가고(RL40), 연결 실패(분단)면 순서를 지키며 24시간까지 다시 보낸다. 24시간을 넘으면 `log_bytes` 를 실은 finish 가 가서 잡이 결과대로 확정되고 `log_complete = false`(finish 가 영원히 막히지 않는다). 근거: 결정 264 · 272 · 324 | 단위: 주입 시계 + 실패 N 회, e2e: 서버 로그 쓰기 실패 주입, DB | PR2 |
| RL20 | [로그 · 유실 표시] 분단 중 잡이 오늘의 `LOG_KEEP_BYTES`(4 MiB)보다 많은 로그(12 MiB)를 낸다. 한 번은 워커 디스크가 가득 차 스풀을 못 쓴다 | 표시 없이 버려지지 않는다. 재연결 뒤 12 MiB 가 모두 서버에 들어간다(바이트 일치). 스풀을 못 쓴 경우는 잡 문서 · `rcm wait` JSON 에 `log_complete = false` 가 서고 `rcm logs` · 웹 로그 보기에도 한 줄로 보인다. 앞쪽이 조용히 잘린 로그가 `log_complete` 참과 함께 나오지 않는다. 근거: 결정 271 · 273 · `remote_worker.py:144-172` | 단위 + e2e: 서버 로그 바이트 수, wait JSON `log_complete`, 웹 | PR2 (값) · PR3 (`rcm logs` · 웹 줄) |
| RL21 | [finish 중복 · 지각 T7·T14] (a) finish 응답이 유실돼 같은 finish 가 두 번 온다(재시작 뒤 큐에서 한 번 더 포함) (b) `lost` 로 닫힌 뒤 같은 세션의 finish · log · phase 가 늦게 온다(자기 멈춤의 `lost` 보고 포함) (c) (b) 의 늦은 finish 둘이 동시에 온다(두 스레드) | (a) 잡은 한 번만 확정되고 첫 보고가 이긴다(`finished_at` · 요약 불변). 둘째는 409 `job_closed` 로 무해이고 워커는 전달된 것으로 읽어 큐에서 지운다(오류 반복 없음). `job_finished` 이벤트 1회, `[[notify]]` 훅 호출 1회. (b) 잡은 `lost` 그대로, 종료 3, 요약은 서버가 먼저 닫은 사유 그대로다. finish 만 `late_result` 로 한 번 남고 로그 · 마커 · 산출물에 반영 없음. 워커는 그 보고를 큐에서 지운다(영원히 재전송 안 함). (c) events 의 `late_result` 는 정확히 1건, 한쪽만 `late_result_recorded: true`. 자동 재실행 · retry 사슬 없음. 근거: `tests/test_worker_lost.py:128-131` · 결정 256 · 274 | 단위 + e2e: DB, 이벤트 버스, notify 가짜 훅 호출 수, 워커 큐 폴더, 두 스레드 동시 요청 | PR2 |
| RL22 | [산출물 T7 · 275] 잡이 끝났고 산출물 번들 업로드가 (a) 끊긴다 (b) 서버는 받았는데 응답이 유실돼 다시 올린다 (c) 워커가 업로드와 finish 사이에 재시작한다 (d) 워커가 업로드 없이 처분 `ready` 를 보낸다 (e) 묶음 행은 있는데 서버의 `bundle.tar` 파일이 없다 | 잡 결과는 산출물과 무관하다(`succeeded` 면 `succeeded`, wait 종료 0). `artifacts.state` 는 번들이 서버에 실제로 있을 때(행 · 해시 · 파일)만 `ready` 이고 아니면 `failed` + `upload_failed`(또는 `hash_mismatch`). 재업로드는 멱등이다(번들 1개, sha256 동일, 보관 행 1개). finish 는 업로드 뒤에만 간다. 남겨 둔 묶음 스풀이 재시작 뒤에도 쓰인다. `rcm wait --fetch-artifacts` 는 받으면 0, 못 받으면 5. 근거: 결정 275 · `remote_workers.py:696-750, 866-891` · F63 · `PLAN.md:118` | e2e: `artifacts.state`, 번들 sha256, 종료 코드 + 단위: 파일 지운 뒤 finish | PR2 |
| RL23 | [전이 T6 · 준비 단계 · 259] 워커가 `materializing`(스냅샷 `GET …/tree` 내려받는 중)일 때 분단 · 서버 정지가 난다. 별도로 스냅샷이 서버에 없다(404) | 연결 실패는 `lost` + `snapshot_unreachable`(종료 3), 확정 결함(404)은 오늘처럼 `failed` + `snapshot_download_failed`(종료 1). 잡이 `running` 으로 영원히 남지 않고 레인이 풀린다. `.part` 가 남지 않는다. 프로세스는 뜬 적이 없다. 자동 재실행 없음. 근거: 결정 259 · `remote_worker.py:892-899` · `PLAN.md:118` | e2e: 중계 정지 중 큰 스냅샷, DB, 워크스페이스 | PR2 |
| RL24 | [업그레이드 T11·T12 · 262 · 329] v25 DB 에서 v26 으로 마이그레이션(필수 시험). 열기 전 DB 에 `running` 원격 잡 · `queued` · `lost` · `cancelling` 이 있다. 세션을 모르는 옛 워커(같은 버전 문자열)가 새 서버에 heartbeat · claim · register 한다 | 마이그레이션 뒤 모든 잡의 상태 · 요약 · 로그 경로가 그대로이고(건수 · 상태 표 일치) `PRAGMA user_version` 이 26. 옛 빌드는 v26 DB 를 열기를 거절한다(newer-schema). 옛 워커가 이미 들고 있던 `running` 원격 잡(레거시)은 heartbeat · log · finish 가 받아져 끝까지 확정되고, 레거시 잡이 끝나면 세션 없는 heartbeat 도 409 `protocol_required`. 옛 워커의 claim · register 는 409 `protocol_required` 이고 claim 은 한 건도 안 나간다. 그 409 의 `error` 에는 「not registered」 가 없어 옛 워커가 재등록 루프를 돌지 않는다. 도는 옛 워커는 claim 을 2~4초 간격으로만 다시 묻고(옛 코드 `remote_worker.py:796-800`, 1초 미만의 뜨거운 루프 아님), 다시 시작한 옛 워커는 등록 409 에 종료 2 → 감독자 간격(5~10초)으로만 되살아난다. 조용한 레거시 잡은 들음 + G 에 `lost`. 근거: 결정 262 · 329 · `store.py:1208-1248, 778-785` · F40 | 단위: v25 DB 고정 파일을 새 빌드로 열기, 읽기 전용 사본 + 옛 빌드 거절, 세션 없는 HTTP 요청, 응답 `error` 문자열 | PR1 |
| RL25 | [호환 T13 · 258 · 270] 프로토콜 키가 없는 옛 서버(같은 버전 문자열)에 새 워커가 붙는다. 감독자가 되살려 여러 번 반복한다 | 등록 전에 알아채고 register 하지 않는다(서버에 `workers` 행이 안 생기고 옛 서버의 도는 잡에 영향 없음). claim 하지 않는다. 한 줄 안내를 내고 종료 3 — 감독자가 되살리는 값이다(launchd · systemd 표, F39). 서버로 간 요청은 `GET /worker/spec`(와 `GET /api/health`)뿐, register · claim 0건. 반복해도 `worker-sessions/` 아래 폴더가 늘지 않는다(V2-12). 근거: 결정 258 · 270 | 단위: 가짜 옛 서버 응답, 종료 코드, 서버 쪽 요청 목록, 데이터 폴더 목록 | PR1 |
| RL26 | [시계 · 잠자기 W3 · 320] 워커 머신이 잠들었다가 G 보다 오래 뒤에 깬다(주입: 벽시계 +G+α, 단조 시계는 거의 정지). 표식 · 일반 각각 | 표식 프리셋은 깬 직후 감시 스레드의 첫 틱 판정에서 자기 멈춤이 걸려 프로세스 그룹이 끝난다(heartbeat 루프가 어디에 있든). 단조 시계만 보고 「몇 초 안 지났다」며 이어 돌리지 않는다. 일반 프리셋은 죽이지 않는다 — 서버가 이미 `lost` 로 닫았어도 계속 돌고 끝나면 `late_result`(결정 256). 근거: 결정 253 · 256 · 320 | 단위: 워커 시계 주입(벽시계 · 단조 따로) | PR1 |
| RL27 | [시계 · 점프와 어긋남] (a) 서버 벽시계가 앞으로 G 이상 뛴다(NTP 단계 보정), 워커는 계속 heartbeat (b) 워커 시계가 서버보다 ±1시간 어긋난다 | (a) heartbeat 가 계속 오던 워커의 잡이 점프 한 번으로 `lost` 되지 않는다(유예는 서버 단조 시계). (b) 유예 · `lost` 판정은 서버 시계로만, 워커의 자기 멈춤은 워커 시계로만 한다. 워커가 보낸 `finished_at` 이 서버 수신 시각보다 미래이거나 `started_at` 보다 과거면 그 끝으로 잘라 `job_seconds` 가 음수가 되지 않는다. 근거: 결정 266 · 277 · `remote_workers.py:769-771` | 단위: 서버 `now_fn` 점프 · `mono_fn`, 워커 시계 오프셋, DB `finished_at` · `job_seconds` | PR1 (a) · PR2 (b 자르기) |
| RL28 | [요청자 · 유예 중 T2] 원격 잡이 `running` 인데 워커와 연락이 끊겨 `worker_timeout_seconds`~G 사이. 요청자는 `rcm wait --job N`, `rcm top`, 웹 큐 화면을 본다 | 잡은 `running`. 사유가 `worker_unreachable_grace` 이고 대기 줄 · top · 웹이 「워커와 연락이 끊김」을 정상 진행과 구별해 사람 말로 보이고 원문 코드가 안 나온다. `estimate.finish_at` 은 null(`—`). 「확인이 필요한 작업」 요약에 `worker_down` 바로 뒤 순서로 오른다. 레인 상태와 `/api/health`(`workers_down` · 503)는 유예 동안 한 값으로 고정되어 깜빡이지 않는다. `rcm wait` 은 이 동안 끝내지 않는다. `--timeout` 을 줬으면 그때 3. 근거: 결정 276 · `PLAN.md:118` | e2e: `/api/status` 행의 `reason` · `estimate.finish_at`, `rcm wait` stderr + 웹 node 시험(사유 문구 · `ACTIONABLE` 순서). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR3 |
| RL29 | [요청자 · 끝] 연락 두절의 끝 다섯 가지(서버가 G 뒤 `lost` · 표식 프리셋 자기 멈춤 · 펜싱 · 워커 재시작의 버린 잡 · 고아를 확인하지 못한 표식 잡)를 `rcm wait` · `rcm status` · 웹 · `[[notify]]` 훅으로 본다. 새 키를 모르는 옛 `rcm wait` 클라이언트도 같이 | 다섯 모두 `state=lost`, 종료 3, `failed_step` · `last_step` 없음. `summary_code` 가 원인마다 다르고(`worker_unreachable` · `worker_self_stopped` · `worker_replaced` · `worker_restarted_without_job` · `orphan_unverified`) 웹 · 사람용 마지막 줄에 번역된 문장이 나온다(i18n 에 없는 코드가 있으면 시험이 빨개진다). `orphan_unverified` 의 문장은 「같은 일이 그 머신에서 아직 돌 수 있다」를 말한다. 마지막 줄이 `rcm logs N` 과 URL 을 안내한다. `[[notify]]` 훅은 잡당 1회. 옛 클라이언트는 새 키를 무시하고 같은 종료 코드를 낸다. 근거: `PLAN.md:118, 220` · 결정 70 · 278 · 327 · `tests/test_outcome.py:543` | e2e: wait JSON `summary_code`, 종료 코드, 가짜 notify 훅 + 웹 node 시험(번역 문장). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR3 |
| RL30 | [설정 · 257 · 263 · 320] 서버 설정의 `disconnect_grace_seconds`: 기본값 / 하한 `max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 15) + 35` 바로 아래와 바로 위 / 86400 초과 / 정수 아님 · 음수 / 서버 설정을 바꾼 reload. 프리셋 `irreversible` 을 잡이 도는 중 reload 로 바꾼다. 서버를 다른 G 로 재시작한다. 워커 설정의 기존 `grace_seconds`(SIGTERM→KILL)를 G 에 견줘 크게 둔다 | 기본은 600. 하한 아래 · 86400 초과 · 비정수는 기동 실패(종료 2)와 키 이름 · 하한 값을 말하는 문장, 하한 그 값은 통과. `[server]` 를 바꾼 reload 는 오늘처럼 통째로 거절되고 옛 값이 남는다. 도는 잡은 claim 때 얼린 `irreversible` · G 를 쓴다 — reload 나 재시작으로 바뀐 값은 다음 claim 부터. 새 키가 `grace_seconds` 와 겹치지 않고 후자의 뜻(SIGTERM→KILL)은 그대로다. 워커 `grace_seconds` 가 커서 `G − m < L` 이 되면 그 표식 잡에서만 grace 를 줄여 쓰고 로그 한 줄(일반 잡 · 설정 파일은 그대로). config 의 하한 상수와 워커의 m(기본 grace) 이 같다. 근거: 결정 257 · 263 · 320 · `config.py:1323-1328, 1845-1846` · `server.py:5090` | 단위: 설정 로드 · reload, 종료 코드, 에러 문구, claim 응답, 워커 grace 계산 | PR1 |
| RL31 | [고아 · 신원 모름 T8·T9·W12 · 268 · 327] 죽은 세션 D 의 표식 잡 그룹이 살아 있는데 신원을 확인할 수 없다: (a) `ps` 가 없거나 실패 · `/proc` 를 못 읽음 (b) 리더(pid = pgid)가 죽고 멤버만 살아 있음 (c) `claim.json` 은 있는데 `proc.json` 이 없음. 그 뒤 재확인 시각에 (가) 신원이 확인된다 (나) 여전히 모른다 | 새 프로세스는 그 그룹을 죽이지 않고 워커 로그에 경고를 남긴다. D 를 `previous_sessions` 가 아니라 `unverified_sessions` 에 넣어 서버가 그 잡을 즉시 닫지 않고 D 의 마지막 들음 + G 까지 busy 로 둔다(그룹 자리 유지). `D.ok_at + G − m` 에 다시 확인해 (가) 같은 그룹이면 멈추고(없거나 다른 프로세스면 그대로) D 의 id 로 좁은 확인 finish → 잡이 G 전에 `lost`(`worker_replaced`), (나) 아무것도 보내지 않고 서버가 G 에 `lost` + `orphan_unverified` 로 닫는다(일반 프리셋이면 `worker_unreachable`). 근거: 결정 268 · 327 · 2차 Q-D · 되돌릴 수 없는 결과(남의 프로세스를 죽임 · 두 번 실행) | 단위: `ps` · `/proc` 실패 주입, 리더만 죽인 그룹, 등록 본문, 주입 시계, DB `summary_code` | PR1 |
| RL32 | [호환 · 순서 T13] 새 워커가 버전이 다른 서버(옛 릴리스)에 붙는다. 따라가기 설정 켬/끔 | 프로토콜 확인이 먼저 종료 3 으로 끝내지 않는다: 버전이 다르면 오늘의 등록 길로 가서 서버의 버전 409 를 받고 따라가거나(M5q) 종료 2. 서버에 `workers` 행 · 세션 행이 생기지 않는다. 근거: 결정 270 · `remote_workers.py:390-398` · `remote_worker.py:381-394` | 단위: 가짜 서버(버전 다름 · spec 키 없음), 종료 코드, 요청 목록 | PR1 |
| RL33 | [취소 · 펜싱 T4·T8] 펜싱된 세션 A 의 `cancelling` 잡이 있고 같은 이름의 새 세션 B 는 살아서 heartbeat 를 보낸다. `kill_at + 2 × heartbeat` 가 지난다 | 서버는 A 의 잡을 `cancelled`(`cancel_unconfirmed`)로 닫지 않는다 — 취소 미확인 닫기는 현재 · 들리는 세션의 잡에만. 잡은 A 의 확인이나 A 의 마지막 들음 + G 까지 busy, 그 뒤 `lost`. 근거: 결정 261 · 266 · `remote_workers.py:965-999` | 단위: 두 세션, 주입 시계, DB | PR1 |
| RL34 | [자기 멈춤 시간 경계 W3 · 320] 표식 잡이 도는 중 (a) heartbeat 요청이 매번 시한(15초)까지 매달리다 실패한다 (b) 서버가 돌아온 첫 heartbeat 뒤 못 한 보고가 쌓여 있다 (c) 연결은 되는데 heartbeat 마다 14초 걸려 2xx 가 온다 (d) 연결은 되는데 heartbeat 가 5xx 를 계속 받는다. 워커 grace 기본 · 큰 값 각각 | (a)(b) 마지막 2xx 의 보낸 시각에서 `G − m` + 감시 틱 + 러너 멈춤 안에 그룹이 사라진다 — 서버가 닫을 수 있는 가장 이른 시각(`heard + G`)보다 늘 앞(주입 시계로 모든 조합, 1판의 「G + 10초 뒤에 죽음」이 나오지 않는다). heartbeat 루프가 막혀 있어도 감시 스레드가 판정한다. (c) 연결된 동안 자기 멈춤이 걸리지 않는다(`ok_at` 나이 ≤ H + 15 < L). (d) 서버는 요청을 받으므로 닫지 않고, 워커는 `G − m` 에 표식 잡을 멈춰 `lost`(`worker_self_stopped`, 3) — 두 번 돌지 않는 쪽. 근거: 결정 320 · F53 · F55 · F56 | 단위: 주입 시계(벽 · 단조) + 매달리는 가짜 클라이언트 + 러너 대역, 멈춤 시각과 서버 닫기 가능 시각 비교 | PR1 |
| RL35 | [닫힌 잡 통보 T3·W4 · 321] 로그도 phase 도 내지 않는 조용한 잡이 분단으로 G 를 넘겨 서버가 `lost` 로 닫은 뒤 워커가 다시 붙는다. 표식 · 일반 · 취소 요청이 있던 일반 각각. 별도로 서버가 잡을 `cancelled`(취소 미확인) · `timed_out` 으로 닫은 경우 | 첫 heartbeat 2xx 응답의 `closed_jobs` 에 그 잡이 `{id, state, had_cancel}` 로 실린다(그 세션의 잡만, 목록에 없는 잡 · 다른 세션의 잡은 안 실림). 워커는 표식 · `had_cancel` · `cancelled` · `timed_out` 이면 그룹을 멈추고(pid 소멸), 취소 없는 일반 `lost` 면 계속 돌린다(로그 전송만 멈춤). 로그 · phase 요청을 한 번도 안 보낸 잡도 같다. 근거: 결정 321 · 322 · 256 · F61 | 단위(HTTP): heartbeat 응답 본문 + 워커 반응(가짜 러너), e2e: pid | PR1 |
| RL36 | [로그 쓰기 실패 · 동시 요청 T15 · 323] (a) 서버가 조각을 파일에 쓴 뒤 커밋이 `database_busy` 로 실패하고 워커가 같은 조각을 다시 보낸다 (b) 같은 잡의 같은 오프셋 요청 둘이 동시에 든다(서버에서 아직 도는 시한 초과 요청 + 재시도) (c) 쓰기 중 ENOSPC(OSError 주입)로 503 `log_write_failed`, 공간이 돌아온 뒤 재전송 (d) 쓰기 뒤 커밋 전에 잡이 닫혀 0행 | 서버 `log.txt` 가 워커 스트림 스풀과 바이트 단위로 같다(sha256, 중복 0 · 미커밋 꼬리 0). (a)(c)(d) 실패 응답 뒤 파일 길이 = `log_file_bytes`(되자름), `log_offset` 불변. (b) 두 요청이 줄 서고 둘째는 접두를 버려 0바이트를 쓴다. (d) 409 `job_closed` 이고 파일에 남는 바이트 0. 잡이 끝난 뒤 `log_complete` 는 참. 근거: 결정 323 · F57 · V2-2 · V1-3 | 단위: DB 바쁨 · OSError 주입, 두 스레드 동시 요청, 파일 길이 · sha256, DB `log_offset` · `log_file_bytes` | PR2 |
| RL37 | [짧은 파일 T16 · 323] 서버의 `log.txt` 가 `log_file_bytes` 보다 짧다(서버 OS 충돌 · fsync 없음 흉내: 커밋 뒤 파일을 잘라 둠). (a) 다음 로그 요청 (b) 서버 기동 정리 | 다음 쓰기는 파일 끝에서 이어 써 빈 바이트(NUL) 구멍이 없다. `jobs.log_complete` 가 0 으로 고정되어 finish 뒤에도 false(`log_offset ≥ log_bytes` 여도). 서버 로그에 「log gap」 한 줄. `rcm wait` JSON · 잡 문서의 `log_complete` 가 false. 근거: 결정 323 · V2-10 · 되돌릴 수 없는 결과(완전하다고 말하는 잘린 로그) | 단위: 파일 자르기 주입, NUL 바이트 검색, DB `log_complete` | PR2 |
| RL38 | [세션 폴더 주인 W9 · 325] 같은 사용자의 기본 데이터 폴더를 두 워커 설정이 같이 쓴다: 운영 서버를 가리킨 워커 A(죽은 세션 폴더와 못 보낸 결과가 남아 있음)와 시험 서버(다른 `server` · 다른 토큰 이름)를 가리킨 워커 B. B 를 시작한다. 별도로 `owner.json` 이 없는 폴더 | B 는 A 의 폴더를 잠가 보지도, 고아를 죽이지도, 큐를 보내지도, 지우지도 않는다(A 의 고아 pid 생존 · A 의 파일 바이트 그대로 · B 서버로 간 요청에 A 의 세션 id 0건). A 가 다시 시작하면 자기 폴더를 이어받는다. `owner.json` 없는 폴더도 남의 것으로 본다. 7일 지나고 잠글 수 있는 남의 폴더만 청소가 지운다. 근거: 결정 325 · V1-5 · 되돌릴 수 없는 결과(남의 결과를 지움 · 남의 프로세스를 죽임) | 단위: 두 설정 · 한 데이터 폴더, 파일 sha256, 요청 목록, pid | PR1 |
| RL39 | [죽은 세션 큐 응답 W10 · 326] 다시 시작한 워커가 죽은 세션 D 의 큐를 D 의 id 로 보내다 (a) 409 `session_fenced`(같은 이름의 다른 살아 있는 프로세스가 이미 D 를 펜싱) (b) 409 `not_registered`(서버가 D 를 모름) (c) 401 · 403 (d) 5xx 가 이어짐을 받는다 | (a) 그 잡의 원래 결과는 보내지 않고(남지 않음), 고아가 「확인됨」이면 좁은 확인 finish 한 번. 이 프로세스는 계속 돌고 종료 0 하지 않는다. (b) D 의 큐를 지우고 D 의 id 로 register 를 보내지 않는다(요청 목록에 D 의 register 0건). (c) D 의 큐 파일이 바이트 그대로 남는다. (d) 등록이 D 의 큐가 끝날 때까지 기다리고(그동안 register 0건), 로그 · 산출물 5xx 는 300초 뒤 finish 먼저. 어느 경우든 이 프로세스의 새 세션은 펜싱되지 않고 정상 등록된다. 근거: 결정 326 · V2-4 · 2차 Q-B | 단위: 가짜 서버 응답 순서, 요청 목록, 종료 코드, 큐 파일 sha256 | PR2 |
| RL40 | [finish 상한 W11 · 324] 잡이 성공으로 끝났는데 로그 라우트가 503 `log_write_failed`(서버 디스크) · 산출물 PUT 이 5xx 를 계속 내고 finish 라우트는 된다. 별도로 같은 기간 서버가 아예 안 닿는다(연결 실패) | 잡이 끝나고 300초(주입 시계) 뒤 finish 가 먼저 가서 잡이 `succeeded` 로 확정되고 `rcm wait` 종료 0, 그룹 자리가 풀린다. `artifacts.state` 는 `failed` + `upload_failed`, `log_complete` 는 false. 그 뒤 남은 로그 · 산출물은 보내지 않는다. 연결 실패 기간에는 순서를 바꾸지 않고 서버가 돌아오면 로그부터 이어 보낸다. 근거: 결정 324 · V2-3 · `PLAN.md:118` | 단위: 주입 시계 + 라우트별 응답 주입, DB, 종료 코드 | PR2 |

LIVE_FORBIDDEN: 운영 서버 · 운영 워커 · 실제 deploy/store 프리셋을 쓰는 실행은 어느 행에도 쓰지 않는다. 대역은 `test-server.md` 규칙의 시험 서버(자기 설정 · `port` 8790~8899 · `data_dir` `~/.local/share/rcm-devtest-<topic>`) + 가짜 `sh -c` 표식 프리셋 + 표준 라이브러리 TCP 중계(`tests/tcp_relay.py`, 끊었다 잇기)다. RL28 · RL29 의 화면 확인(실기)도 시험 서버와 CDP 로 한다. 두 행은 pytest · node 시험이 JSON · 문구를 잠그므로 `(live)` 표시를 달지 않았다(`(live)` 행은 pytest 누락으로 세지 않아 잠금이 빠진다).

Excluded (rare): 펜싱된 세션이 끝나는 도중 서버가 재시작된다 · ① · 되돌릴 수 있다(A 가 다음 요청에서 같은 409 를 받는다). RL10 과 RL7 이 각각 잠근다.
Excluded (rare): 워커의 스풀 · 큐 파일이 디스크 오류로 손상된다 · ③ · 로그 · finish 지연뿐이고 서버가 `lost` 나 `log_complete = false` 로 닫는다. CHANGELOG · `docs/acceptance/reports/` 검색 0건.
Excluded (rare): 워커가 7일(`WORKSPACE_KEEP_DAYS`) 넘게 꺼져 있다가 큐에 남은 finish 를 들고 시작한다 · ① · 서버가 오래전에 `lost` 로 닫았고 결과는 RL21(b)와 같다.
Excluded (rare): 워커 데이터 폴더가 `flock` 을 지원하지 않는 파일 시스템에 있다 · ③ · 되돌릴 수 있다(이어받기를 건너뛰어 서버가 G 까지 기다린 뒤 닫는다 — 늦을 뿐 두 번 돌지 않는다). CHANGELOG 의 `flock` 1건(`CHANGELOG.md:1562`, macOS · Linux 에서 동작한다는 예시) · reports 검색에 지원 안 함 사례 0건.
Excluded (rare): 서버를 다른 `worker_timeout_seconds` · `worker_heartbeat_seconds` 로 재시작했는데 그 전에 claim 된 표식 잡의 얼린 G 가 새 하한 L + m 보다 작다 · ①(설정 변경 재시작과 표식 잡이 도는 중이 겹침) · 되돌릴 수 있다(연결된 채 표식 잡이 일찍 멈춰 `lost`(3) — 두 번 돌지 않는다). CHANGELOG · reports 에 `worker_timeout_seconds` 를 도는 중 바꾼 사례 0건(나온 것은 키 소개 `CHANGELOG.md:2164-2168`).
Excluded (rare): 고아 그룹의 리더가 죽은 뒤 그 pgid 와 같은 번호로 새 프로세스가 새 그룹을 만들고 그 리더도 죽는다 · ②(pid 가 한 바퀴 도는 시간 안에 두 번) · RL31(b)와 같은 「모름」 길로 가서 신호를 보내지 않는다 — 되돌릴 수 없는 쪽(남의 그룹을 죽임)으로 가지 않는다. 검색: CHANGELOG · reports · git log 「pid reuse」 0건.

### 7.2 시나리오 표를 고친 곳

§12.2 도 이 목록을 가리킨다. 1판에서 고친 것은 그대로 둔다.

- 결정이 정한 「undecided」 칸을 채웠다. RL5(256), RL8(256), RL10(258 · 261), RL12(a)(260), RL21(256 · 274), RL23(259),
  RL24(258 · 262), RL25(258), RL30(257 · 263)이다.
- RL26 의 마지막 문장 「서버가 이미 lost 로 닫은 잡은 계속 돌지 않는다」는 표식 프리셋에만 해당하게 고쳤다. 일반 프리셋은
  결정 256 대로 계속 돈다.
- RL17(b)의 「마커 줄이 두 조각에 걸침」은 「마커 줄 직후 재시작」으로 바꿨다. 조각이 줄 경계에서만 잘리므로 걸칠 수
  없다(F34 · F35). 확인할 결과는 「조각이 모두 `\n` 으로 끝남」이다.
- RL18(b)의 「두 조각 경계에 걸침」도 같은 이유로 「경계 근처」로 바꿨다.
- RL24 의 「5초 이상 간격」은 옛 워커 코드가 정한 간격(F40)으로 고쳐 적었다. 옛 워커는 고칠 수 없다.
- RL25 의 「서버 쪽 요청 수 0건」은 register · claim 0건으로 바꿨다. 등록 전 확인의 `GET` 은 있다.
- RL16(b)의 「뒤 조각이 먼저 닿는다」는 워커가 한 잡 안에서 순서대로 보내므로 실제로는 생기지 않는다. 빈틈 응답을 흉내 내어
  시험한다.
- RL11 에 `not_registered` 갈래를, RL22 에 (d)를, RL10 에 204 와 확인이 없는 경우를 더했다.
- RL28 · RL29 의 `(live)` 표시를 뗐다. JSON, 종료 코드, 문구는 pytest · node 시험으로 잠그고 화면 모양만 PR4 실기로 본다.
  `tools/scenario_trace.py` 는 `(live)` 행을 누락으로 세지 않아 시험 잠금이 빠지기 때문이다.
- 새 행 셋을 더했다. RL31 은 고아 신원을 모르는 경우로, 결과를 되돌릴 수 없다. RL32 는 혼합 버전에서 버전을 먼저 보는지를
  보며 흔한 경우다. RL33 은 펜싱된 세션의 취소 중 잡이 두 번 실행으로 이어지는 경우로, 결과를 되돌릴 수 없다.

2판에서 1라운드 지적에 따라 고친 것은 다음과 같다.

- PR 배치(V1-2): RL4 는 PR1(상태 하나)과 PR2(`late_result`)로, RL8 도 PR1 과 PR2(`late_result`)로 나눴다. RL12 는 PR1(a ·
  c)과 PR2(b, 디스크 큐)로, RL14 는 PR1(b)과 PR2(a, 디스크 큐)로 나눴다. 변이 `late-result-changes-state` 는 PR2 그대로다(RL4
  의 PR2 부분 · RL21).
- RL1: 분단을 「응답을 붙잡아 heartbeat 가 시한까지 매달림」으로 적고 멈추는 주체를 감시 스레드로 바꿨다(결정 320).
- RL8(b): 1판의 「일반 프리셋을 취소했는데 워커가 못 들은 채 계속 돌면 … late_result」를 고쳤다. 워커는 복귀한 뒤
  `closed_jobs` 의 `had_cancel` 을 보고 멈춘다(D-d, 결정 322).
- RL11: 헤더 형식 오류 400 갈래를 더했다(V2-13).
- RL12(c): `rcm doctor` 를 넣었다(#268 머지, F46 · F54).
- RL15: Linux `/proc` 신원을 넣었다(D-g, F59).
- RL16(d): 디스크 `acked` 와 빈 본문 질의를 넣었다(V2-13).
- RL19(b): 5xx 는 300초 뒤 finish 를 먼저 보내고(D-c) 연결 실패는 24시간까지 기다린다.
- RL21(c): 동시에 온 늦은 finish 둘에서 `late_result` 가 1건만 남는지 본다(V2-11).
- RL22(e): 행은 있는데 파일이 없는 경우를 더했다(V1-12).
- RL24: `protocol_required` 문구에 「not registered」가 없는지 본다(V2-13).
- RL25: 반복해도 세션 폴더가 늘지 않는지 본다(V2-12).
- RL26: 판정 주체를 감시 스레드로 바꿨다.
- RL29: `lost` 의 끝을 넷에서 다섯으로 늘렸다(`orphan_unverified`, D-g).
- RL30: 하한을 새 공식으로 바꾸고 큰 워커 grace 를 더했다(D-a · V2-6).
- RL31: (b) 리더만 죽은 그룹, (c) `proc.json` 없음, 재확인 (가)(나), `orphan_unverified` 를 더했다(D-g · V2-7).
- 새 행 일곱을 더했다.
  - RL34 는 자기 멈춤의 시간 경계다. HIGH V1-1 · V2-1 을 재현하고 두 번 실행이라는 되돌릴 수 없는 결과를 다룬다.
  - RL35 는 닫힌 잡 통보다. 조용한 잡과 분단 때문에 흔하다.
  - RL36 은 로그 쓰기 실패와 동시 요청이다. HIGH V2-2 · V1-3 을 다루고 DB 바쁨과 재시도 때문에 흔하다.
  - RL37 은 짧은 파일이다. 완전하다고 말하는 잘린 로그가 되돌릴 수 없는 결과다.
  - RL38 은 세션 폴더의 주인이다. 남의 결과 삭제와 남의 프로세스 종료가 되돌릴 수 없는 결과다.
  - RL39 는 죽은 세션 큐의 응답이다. 재시작 때문에 흔하다.
  - RL40 은 finish 상한이다. 서버 디스크가 가득 차는 경우로 흔하다.
- 뺀 행은 없다.

### 7.3 오늘 계약이 바뀌는 시험과 키를 더해 고치는 잠금 시험

계약을 약하게 하는 것이 아니다. 결정 253 이 계약을 바꿨다.

| 시험 | 오늘 | 바뀐 뒤 | PR |
|---|---|---|---|
| `tests/test_worker_lost.py:58` `test_worker_past_timeout_loses_its_running_job_and_shows_down` | 80초 뒤 `lost` | 80초 뒤 레인 `down`, 잡 `running` · 사유는 PR3. G 뒤 `lost`(`worker_unreachable`, 초는 단조 gap) | PR1 |
| `tests/test_worker_lost.py:107` `test_heartbeat_brings_a_down_worker_back_up_without_resuming_the_lost_job` | timeout+30 에 lost, 돌아와도 안 살아남 | G 뒤 lost 로 같은 내용(늦은 finish 는 409 + `late_result` 는 PR2) | PR1 |
| `tests/test_worker_lost.py:137` `test_cancelling_job_of_a_down_worker_becomes_lost_not_cancelled` | timeout+30 에 lost | G 뒤 lost, 같은 결론 | PR1 |
| `tests/test_e2e_worker.py:775` `test_killed_worker_leaves_the_job_lost_after_the_timeout_and_a_new_worker_takes_over` | timeout 에 lost, 고아는 시험이 치움 | 시험 서버 G 를 짧게(하한 이상). G 에 lost. 다시 띄운 워커가 같은 데이터 폴더면 고아를 치운다 — 시험이 고아를 치우는 줄은 RL15 로 바뀜 | PR1 |
| 세션 없이 register · claim 하는 시험 도우미(`tests/test_worker_*.py`, `tests/test_server_m5q_worker.py` 등) | 프로토콜 1 | 도우미가 세션 id 를 실행 중 만들어 싣는다(§8 gitleaks) | PR1 |
| `tests/test_worker_api.py:542-549` · `tests/test_worker_client.py:183-190`(register 응답 정확한 dict) | 키 6개 + generation | `worker_protocol: 2` 를 더함 | PR1 |
| `tests/test_worker_api.py:1204`(heartbeat 응답 정확한 dict) | `{cancel, paused, timeout_seconds}` + pop 둘 | `closed_jobs: []` 를 더함(`tests/test_server_m5q_worker.py:90` 은 부분 집합이라 그대로) | PR1 |
| `tests/test_config.py:905` `WORKER_KEYS` | 워커 키 셋 | `disconnect_grace_seconds` 를 더하고 하한 검증 시험 | PR1 |
| `tests/test_outcome.py:543-569`(코드 ↔ 번역) | 오늘 코드 | `LOST_CODES` 셋 + `orphan_unverified` 의 번역이 있어야 초록 | PR1 |
| `tests/test_status_schema.py:91` `RECENT_KEYS`(대조 `:303`) | 정확한 집합 | `log_complete` 를 더함 | PR2 |
| `tests/test_status_outcome.py:162` `NEW_RECENT`(대조 `:366-368`) | 허용 목록 | `log_complete` 를 더함 | PR2 |
| `tests/test_queue_m5n.py:135` · `tests/test_render_m5n.py:199` · `tests/test_action_marker_m5s.py:139-141`(`ACTIONABLE_REASONS` 튜플) | `worker_down` 뒤 `no_capable_machine` | `worker_down` 바로 뒤에 `worker_unreachable_grace` | PR3 |
| `tests/web/stall.test.js:192-194` · `tests/web/m5n.test.js:38-41`(웹 `ACTIONABLE`) | 같은 목록 | 같은 자리에 더함. `tests/test_web_schedule_m5s.py:43`(웹 = 모델)은 고칠 것 없이 대조 | PR3 |

### 7.4 mutcheck 변이

모두 새 이름이다(`scripts/mutcheck.py` grep 0).

| 이름 | 파일 · 바꿈 | 빨개져야 할 행 | PR |
|---|---|---|---|
| `session-fence-by-name` | `remote_workers.py` 세션 판정을 이름 비교로 | RL10 RL11 RL12 | PR1 |
| `reregister-closes-own-jobs` | register 가 같은 세션 재등록에도 그 이름의 busy 잡을 닫음 | RL12 | PR1 |
| `grace-wall-clock` | 유예 gap 을 벽시계 `last_seen_at` 으로 | RL27 RL7 | PR1 |
| `grace-no-start-floor` | `heard` 바닥(기동 시각)을 지움 | RL7 RL2 | PR1 |
| `self-stop-monotonic-only` | 워커 `wgap` 을 단조 시계만으로 | RL26 | PR1 |
| `orphan-kill-no-lstart` | 고아 정리에서 시작 시각 비교를 건너뜀 | RL15 RL31 | PR1 |
| `job-closed-kills-running` | 409 `job_closed`(`state=lost`)에 일반 잡을 죽임 | RL5 | PR1 |
| `self-stop-in-heartbeat-loop` | 감시 스레드를 없애고 판정을 heartbeat 루프의 `heartbeat_once` 앞으로(1판 꼴) | RL34 RL1 | PR1 |
| `self-stop-ignores-request-timeout` | m 에서 요청 시한 항과 여유를 빼고 `grace + H` 로(1판 공식) | RL34 | PR1 |
| `self-stop-all-jobs` | 자기 멈춤이 표식 없는 잡에도 걸림 | RL5 RL26 | PR1 |
| `grace-read-at-judgement` | 유예 판정 · 자기 멈춤이 얼린 `jobs.disconnect_grace_seconds` 대신 지금 설정값을 읽음 | RL30 | PR1 |
| `grace-lower-bound-dropped` | 설정 검증의 하한을 1판의 「`worker_timeout_seconds` 보다 큼」으로 | RL30 RL34 | PR1 |
| `cancel-unconfirmed-any-session` | 취소 미확인 닫기를 현재 · 들리는 세션 조건 없이 | RL33 | PR1 |
| `register-session-before-version` | register 에서 세션 · 펜싱 처리를 버전 검사 앞으로 | RL13 | PR1 |
| `closed-jobs-omitted` | heartbeat 응답에서 `closed_jobs` 를 뺌 | RL35 RL8 | PR1 |
| `lost-cancel-keeps-running` | 워커가 `had_cancel` 을 무시하고 일반 `lost` 잡을 계속 돌림 | RL8 RL35 | PR1 |
| `adopt-foreign-session` | 시작 때 `owner.json` 비교를 건너뜀 | RL38 | PR1 |
| `log-offset-ignored` | 서버가 오프셋을 보지 않고 덧붙임 | RL16 RL6 | PR2 |
| `log-write-unlocked` | 로그 라우트에서 잡별 잠금을 뺌 | RL36 | PR2 |
| `log-append-not-positioned` | `log_file_bytes` 자리 쓰기 · 뒤 자르기 대신 덧붙임(1판 꼴) | RL36 RL37 | PR2 |
| `late-result-changes-state` | 늦은 finish 가 `lost` 를 결과로 바꿈 | RL21 RL4 | PR2 |
| `fenced-late-result-recorded` | 펜싱된 세션의 늦은 finish 를 `late_result` 로 남김 | RL11 | PR2 |
| `dead-session-fenced-exits` | 죽은 세션 큐의 409 `session_fenced` 에 W5(모든 잡 멈춤 · 종료 0)를 부름 | RL39 | PR2 |
| `finish-waits-forever` | finish 상한(300초)을 지움 | RL40 RL19 | PR2 |
| `artifact-ready-without-bundle` | 묶음 행 · 파일이 없어도 `ready` 를 받음 | RL22 | PR2 |
| `unreachable-reason-dropped` | `compute_queue` 가 down 워커의 도는 잡에 `running` 을 줌 | RL28 | PR3 |

변이는 1판 11개와 2판 15개를 더해 26개다. `fenced-late-result-recorded` 는 펜싱된 세션의 늦은 finish 가 PR2 의 `late_result`
길을 지나므로 PR2 에 둔다. RL11 은 PR1 행이지만 PR2 가 그 행의 시험에 `late_result` 0건 단언을 더한다.

### 7.5 시험 도구

- `tests/tcp_relay.py` 는 PR1 이 새로 만들고 표준 라이브러리만 쓴다. `pause()` 는 새 연결 수락을 멈추고 기존 연결의 응답을
  보류해 분단을 만든다. 이때 워커 요청은 시한까지 매달린다. `stop()` 은 연결을 거부한다. 서버 정지를 흉내 내는 함수는
  아니다. 정지는 실제 서버를 멈춰서 만든다. `resume()` 은 중계를 되살린다.
- 시계: 서버에는 `now_fn` 과 새 `mono_fn` 을 주입한다. 워커에는 `now_fn` 과 새 `mono_fn` 인자로 단조 시계를 주입한다.
  `RemoteWorker.__init__` 에는 이미 `now_fn` · `rand_fn` 을 주입한 선례가 있다(`remote_worker.py:245-258`). 감시 스레드는 틱
  한 번을 부르는 함수를 따로 두어 단위 시험이 스레드 없이 부를 수 있게 한다.
- 러너 대역: 플래그에서 그룹 소멸까지의 시간을 주입할 수 있는 가짜 러너로 RL34 의 경계를 계산한다. 실제 러너는 e2e 에서
  쓴다.
- 신원: `/proc` 뿌리 경로를 주입해 가짜 stat 파일을 쓰고 `ps` 실행 함수를 주입해 실패와 다른 시작 시각을 흉내 낸다.
- 서버 로그 쓰기 고장 주입: 파일 쓰기 OSError, 커밋 때 SQLite 바쁨, 커밋 0행, 쓰기와 커밋 사이 멈춤을 주입한다. 마지막
  것으로 두 스레드가 줄을 서는지 확인한다.
- 20초가 넘는 시험은 `@pytest.mark.slow` 를 단다(AGENTS.md:130-133). e2e 의 G 에는 하한이 있고 기본 설정이면 95초다. 그래서
  e2e 서버는 `worker_timeout_seconds` 를 최소값 10(`config.py:1323`)으로, `worker_heartbeat_seconds` 를 1 로 낮춰 하한을
  max(10, 2 + 15) + 35 = 52초로 만든다. 그 행들은 `@pytest.mark.slow` 다. 그보다 짧아야 하는 경계는 주입 시계를 쓴 단위
  시험으로 본다.

## 8. 개인정보 점검 (dev 에서 main 으로 가는 릴리스 PR 때)

- 오너 발언 가운데 다른 프로젝트의 이름, 경로, 식별자는 명세에 옮기지 않는다. 머리 인용에는 이름이 없는 문장만 둔다. Codex
  원문과 오너 발언 원본 파일은 레포 밖에 있으므로 경로를 적지 않는다.
- 시험의 세션 id 와 토큰은 실행 중에 만든다(F51). 높은 엔트로피 리터럴은 gitleaks 이력 문제를 만들므로 쓰지 않는다.
- 실기 보고서(PR4)의 스크린샷과 로그에 홈 경로, 호스트 이름, 사설 IP 가 없는지 본다. 시험 서버 `data_dir` 은 `~` 로
  적는다.
- 워커 로그 줄(고아 정리, 펜싱, 자기 멈춤, grace 줄임, 남의 세션 폴더 건너뜀)에 경로, 토큰, 서버 URL 의 자격 정보가 없는지
  본다. 오늘 규칙은 상태 코드만 공개하는 것이다(`remote_worker.py:896-899` 주석).
- 세션 폴더의 `owner.json` 에는 토큰 값과 서버 URL 원문을 적지 않는다. 서버는 sha256 으로, 토큰은 이름으로만 적는다(결정
  325).
- 릴리스 PR 에서는 메인 세션이 레포 밖의 패턴 파일로 점검한다(오너 지시 2026-11-23).

## 9. 위험

| 위험 | 막는 것 |
|---|---|
| 일반 프리셋 잡이 G 뒤 `lost` 가 된 뒤에도 워커에서 계속 돌아 같은 그룹의 다음 잡과 겹친다 | 결정 253 이 받아들인 맞교환(일반 프리셋은 두 번 돌아도 된다). 결과는 `late_result` 로만 남는다. 취소 요청이 있던 잡은 다시 붙은 워커가 멈춘다(결정 322). 문서에 적는다 |
| 워커 벽시계가 앞으로 `G − m`(기본 565초) 이상 뛰면 표식 잡이 일찍 멈춘다 | 결과는 `lost`(3) — 모름이지 실패가 아님. 문서에 「표식 잡을 도는 워커는 NTP 단계 보정을 피하라」. 벽시계를 빼면 잠자기를 못 센다(결정 320) |
| 서버가 heartbeat 에 5xx 를 `G − m` 동안 계속 내면(요청은 받으므로 서버는 안 닫는다) 표식 잡이 멈춘다 | 결과는 `lost`(3), 두 번 돌지 않는 쪽(RL34 d). 워커가 「서버가 받았는지」를 5xx 로는 알 수 없다 |
| 서버 재시작 뒤 원격 잡이 최대 G 동안 더 `running` 으로 남아 그룹 자리를 잡는다(워커가 재시작 전에 죽었어도) | 보수적인 쪽(두 번 실행보다 늦음, 2차 Q-C). 워커가 돌아오면 바로 닫힌다 |
| 펜싱된 세션이 분단이면 그 잡이 G 까지 레인 · 그룹을 잡고 화면 사유는 `running`(그 이름의 현재 세션이 살아 있어 레인이 down 이 아니다) | 표시 한계로 문서에 적는다. 잡은 G 에 닫힌다 |
| `kill -9` 된 워커가 다시 시작되지 않으면 고아 표식 잡을 아무도 멈추지 않고 G 뒤 그룹이 풀린다 | 서비스 파일의 감독자(launchd `KeepAlive`, systemd `Restart`)가 다시 띄운다(F39). 수동 실행 워커는 문서 경고 |
| 고아 신원을 끝내 확인하지 못한 표식 잡(리더만 죽은 그룹 · `ps`/`/proc` 실패 · 띄운 순간 죽음)은 서버가 G 에 `lost` 로 닫아 그룹이 풀리고, 고아가 실제로 살아 있으면 같은 그룹의 다음 잡과 두 번 돌 수 있다 — **남는 위험** | 조용히 두지 않는다: 시작 때 신호를 보내지 않고(남의 프로세스를 죽이지 않음), `D.ok_at + G − m` 에 다시 확인하고, 그래도 모르면 그 잡의 요약 코드가 `orphan_unverified` 라 `rcm wait` · 웹 · `[[notify]]` 가 「같은 일이 그 머신에서 아직 돌 수 있다」 를 사람에게 보인다(결정 327, 2차 Q-D 의 fail-closed 에 경고를 더함). 그룹을 G 넘어 붙잡지는 않는다(영원히 막힘). Linux `/proc` 는 이 머신에서 재지 않았다(F59) |
| 같은 토큰의 두 번째 프로세스가 서비스를 펜싱하면 서비스는 종료 0 이라 다시 안 뜬다(결정 255 · 258) | 문서: `launchctl kickstart -k` / `systemctl --user restart` 로 다시 올린다. 두 번째 프로세스의 시작 줄이 「서비스 워커를 대신했다」를 말한다 |
| 워커 설정의 `server` 문자열이나 `server_name` 을 바꾸면 자기 옛 세션 폴더를 남의 것으로 본다(결정 325) | 그 폴더의 잡은 서버가 G 뒤 `lost` 로 닫는다(늦을 뿐 두 번 돌지 않음). 고아는 정리되지 않으므로 문서에 「설정을 바꾸기 전에 워커를 멈추고 잡이 끝나기를 기다려라」 |
| 로그 · 산출물 라우트의 5xx 가 300초를 넘으면 finish 가 먼저 가고 남은 로그 · 산출물을 버린다(결정 324) | 정직하게 표시: `log_complete = false`, 산출물 `failed` + `upload_failed`. 잡 결과 · 그룹 자리는 제때 풀린다 |
| 서버 OS 충돌로 `log.txt` 가 커밋된 길이보다 짧아진다(fsync 없음) | 빈 구멍 없이 이어 쓰고 `log_complete = false` 고정(결정 323, RL37). 잃은 바이트는 되찾지 않는다 |
| 표식 잡의 워커 `grace_seconds` 가 G 에 견줘 크면 그 잡에서만 grace 를 줄여 쓴다 | 설정 검증이 기본 grace(10)는 줄지 않게 보장. 줄였으면 워커 로그 한 줄(결정 320) |
| M5t-a 의 PR3~PR7 이 `config.py` · `capabilities.py` · `server.py` · `cli.py` · `docs/configuration.md` · `docs/operating.md` 를 고친다(F54) | 구현 PR1 은 M5t-a 전체가 dev 에 머지된 뒤 시작(§5). 이 명세의 줄 번호는 고정 sha 기준이라 구현 PR 이 다시 찾는다 |
| M5t-c 가 register(`pair_proof` · 풀 묶기)와 DB v27 을 쓴다 | `pair_proof` 는 §4.2 의 4 자리(버전 · 프로토콜 뒤, 부작용 앞, F52). v27 은 PR1 의 v26 뒤(M5t-c 결정 299) → 메인 세션이 M5t-c 명세와 함께 확인(§6 #11) |
| M5t-c 의 종료 코드(확실한 신원 불일치 0 · 연결 실패는 프로세스 안 재시도)와 이 명세의 0(펜싱) · 3(프로토콜 불일치)이 섞여 읽힌다 | 0 은 「되살리지 않음」 하나의 뜻(펜싱 · 확실한 신원 거절), 3 은 「일을 시작하지 말고 감독자가 다시 시도」 하나의 뜻(프로토콜 불일치)으로 문서에 함께 적는다(F52, 부모 명세 M8 을 좁힘) |
| 결정 번호: M5t-c 가 280~303 을 썼고 304 부터 이어 쓸 수 있다 | 이 명세의 2판 결정은 320~329, 320~339 를 M5t-b 몫으로 메인 세션이 M5t-c 세션과 맞춘다(§6 #12) |
| `irreversible` 낱말이 릴리스 쪽의 「되돌릴 수 없는 입력 값」(F26)과 뜻이 다르다(프리셋 전체) | 문서에 구별을 적는다. 입력이 rehearsal 이어도 자기 멈춤이 걸리는 것은 안전한 쪽 |
| 앱 프로젝트가 원격에서 돌 수 있는 배포 · 스토어 프리셋에 `irreversible` 을 달지 않으면 그 잡은 일반 잡처럼 끊겨도 계속 돈다 | `rcm check` · `rcm doctor` 가 이름으로 추측해 경고하지 않는다(거짓 경보, §1.3 · §12 V1 제안). 문서와 §6 #16 의 앱 쪽 작업으로 막는다 |
| 보고 큐가 디스크를 쓴다(로그가 두 벌: 로컬 `log.txt` + 스트림 스풀) | 7일 청소(F41). 디스크가 가득 차면 `log_complete = false` 로 정직하게 표시(RL20) |
| 옛 워커가 섞인 dev(같은 버전 문자열)에서 옛 워커가 2~4초마다 claim 409 로그를 찍는다 | 잡은 안 집는다(RL24). 출시 뒤에는 M5q 따라가기가 버전을 맞춘다 |

## 10. 요청서에 돌려줄 것

해당 없다. 요청서가 없다.

## 11. 결정 표

256~264 는 1차 위임 결정이고 265~279 는 2차 위임 결정이다. 2차에서 추천이던 것을 확정했다. 320~327 은 1라운드 격리 반박
뒤에 메인 세션이 오너 위임으로 정한 D-a~D-g 다(2026-11-26). 328 · 329 는 1라운드 지적을 고치며 재료가 정했고 같은 날
위임으로 확정됐다. 재료가 D-* 문구와 다르게 정한 곳이 넷 있다. D-a 의 `ok_at` 처음 값은 claim 요청을 보낸 시각으로 했다.
D-b 의 「log gap」 표시는 409 `log_gap` 과 뜻이 겹치지 않게 `log_complete = 0` 고정과 서버 로그 줄로 했다. D-c 의
`log_complete` 는 서버가 바이트 수로 계산한다. D-g 의 경고는 요약 코드 `orphan_unverified` 로 낸다. 메인 세션은 넷 모두 더
안전하거나 기존 계약을 덜 바꾼다고 보고 받아들였다. 1판 행 가운데 2판이 문장을 바꾼 행은 「2판:」으로 적었다. 결정을
뒤집은 것은 267 하나이고 320 이 대신한다. 결정 번호는 M5t-b 가 320~339 를, M5t-c 가 280~312 를 쓰므로 겹치지 않는다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다른 점 |
|---|---|---|---|---|
| 256 | 일반 프리셋 유예 만료와 늦은 결과(Q1) | 유예 만료에 서버가 `lost`(`rcm wait` 3). 같은 세션의 늦은 finish 는 `late_result`(outcome · 프로세스 종료 코드 · 워커 finish 시각 · 수신 시각 · 로그 완전성)로만 남기고 `lost` 를 바꾸지 않으며 알림 · 로그 · 마커 · 산출물을 받지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 253 의 「계속 돈다」는 워커에서 계속 돈다는 뜻이고 요청자의 결과는 3 |
| 257 | 표식 키와 얼림(Q2) | 별도 프리셋 키 `irreversible = true`(`local_only` 는 배치, `irreversible` 은 끊김 안전). `irreversible` 과 `disconnect_grace_seconds` 는 claim 때 잡 행에 얼린다(reload · 재시작은 다음 claim 부터) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 키 이름이 정해짐(253 은 「표식」) |
| 258 | 워커 종료 코드(Q3) | 펜싱 0(감독자가 되살려 핑퐁하지 않게), 프로토콜 불일치 3(감독자가 다시 시도). 그 밖은 오늘대로. 2판: 프로토콜 불일치 3 은 등록 전에만(도는 중 갈래는 닿지 않아 지움, V2-9). M5t-c 결정 303 과 함께 0 = 「되살리지 않음」(펜싱 · 확실한 신원 거절), 3 = 「감독자가 다시 시도」(F52) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 259 | 준비 단계 끊김(Q4) | 명령이 돈 적 없고 실패 결과도 없으니 `lost`(3) + `snapshot_unreachable`. 저장소 없음 · 거절된 스냅샷 같은 확정 결함은 `failed`(1) 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 260 | 같은 프로세스 재등록(Q5) | 프로세스가 만든 세션 id 하나를 모든 레인이 쓴다. 같은 id 재등록은 멱등(펜싱 · 잡 닫기 없음). 다른 id 만 펜싱 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 261 | 펜싱된 잡의 그룹 자리(Q6) | 표식 여부와 상관없이 멈춤 확인(옛 세션이 409 를 받고 그룹을 죽인 뒤 좁은 범위의 「펜싱 멈춤 확인」 finish 를 보냄) 또는 얼린 유예 만료 전에는 풀지 않는다. 그동안 새 세션의 그 레인 claim 은 204 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 255 의 「409 를 받고 잡을 멈춘 뒤 끝난다」를 분단 때까지 넓힘 |
| 262 | 마이그레이션 때 옛 워커의 도는 잡(Q7) | 옛 lease 호환: 이미 도는 레거시 잡의 heartbeat · log · finish(와 그 잡의 phase · 산출물)만 받고 새 claim 은 모두 거절. 그 잡들이 끝나거나 유예에 닿으면 호환은 저절로 거둔다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 3판 250 의 「혼합 운영 경로는 만들지 않는다」를 레거시 lease 만큼 넓힘 |
| 263 | 유예 기본값과 검증 | `disconnect_grace_seconds = 600`(결정 253 의 「약 10분」). 정수, 86400 이하, 위반은 기동 실패. `[server]` 라 reload 불가. 2판: 하한은 1판의 「`worker_timeout_seconds` 보다 큼」 대신 결정 320 의 `L + 35`(기본 설정에서 95) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 하한 공식은 결정 320(위임) | 「약 10분」 → 600초 |
| 264 | 재전송 창 · 보관 | finish · 로그 재전송은 잡이 끝난 뒤 24시간(시간으로만, 3판 r3 A-H6). 잡이 도는 동안은 상한 없음. 늦은 결과 · 스풀 보관은 워크스페이스와 같은 7일(`WORKSPACE_KEEP_DAYS`). 2판: 서버가 닿는데 로그 · 산출물만 5xx 면 300초 뒤 finish 먼저(결정 324) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 265 | 세션 모델 | 세션 id = 프로세스당 `token_hex(16)`. 새 표 `worker_sessions`(현재 = `fenced_at IS NULL`), `jobs.worker_session_id`. lease = (잡, 세션), 별도 `lease_id` 없음. 펜싱된 세션 행은 워커 행과 함께만 지움. 모든 보고가 세션을 싣고, 표에 없으면 409 `not_registered`, 펜싱이면 409 `session_fenced`, 펜싱된 세션의 재등록도 409 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 266 | 서버 유예 판정 | 잡마다 `mono_now − max(heard(세션), 서버 기동) > G_J` 면 `lost`(`worker_unreachable`). `heard` 는 메모리 단조 시각, 펜싱 뒤 요청은 안 올림. 레인 down 은 벽시계 그대로. 취소 미확인 닫기는 현재 · 들리는 세션의 잡에만. `App` 에 `mono_fn`. 2판: 「같은 락」은 결정 328 의 `_heard_lock`. 서버 재시작 뒤 유예는 기동 시각부터 다시 셈(2차 Q-C) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-C) | — |
| 267 | 워커 자기 멈춤 | **2판에서 결정 320 이 대신한다**(1판: heartbeat 루프의 매 회차 보내기 전 판정, `m = grace_seconds + heartbeat_seconds + 5` — F53 으로 깨짐, V1-1 · V2-1). 「표식 잡만 · `max(벽, 단조)` 경과 · 그룹 SIGTERM→KILL · finish `lost` + `worker_self_stopped`」는 320 이 이어받는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차). 2판에서 320 으로 바뀜 | — |
| 268 | 재시작 순서 · 이어받기 · 고아 | 세션 폴더 `<data>/worker-sessions/<id>/` + `flock`. 시작 때 잠글 수 있는(죽은) 세션의 고아 그룹을 신원(시작 시각)이 같을 때만 정리, 남은 큐를 옛 세션 id 로 보낸 뒤 `previous_sessions` 로 등록. 서버는 그 세션들의 busy 잡을 `worker_restarted_without_job` 으로 닫음. 신원을 못 확인한 세션은 넘기지 않음(2차 Q-D, fail-closed). 관찰자에 `spawned(pid)`. 2판: 폴더는 프로토콜 검사 뒤에 만들고(V2-12) 주인 · 신원 · 재확인 · 응답 표는 결정 325~327 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-D) | — |
| 269 | 409 의 기계 판독 키 | 409 응답에 `code` ∈ {`session_fenced`, `protocol_required`, `not_registered`, `job_closed`(+`state`), `log_gap`(+`expected_offset`)}, 503 `log_write_failed`. 오늘의 `error` 문구는 유지. 워커의 응답별 처리는 §4.5 표. 2판: 로그 라우트의 커밋 바쁨은 오늘 코드 503 `database_busy`, 헤더 형식 오류는 400(`error` 만) — 결정 329 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 270 | 프로토콜 판 | `worker_protocol = 2` 를 `/worker/spec` · register 응답에. 새 워커는 등록 전 spec 확인, 키가 없으면 버전이 같을 때만 종료 3(다르면 오늘의 등록 길 — 버전 먼저) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 271 | 로그 오프셋 · 스풀 | 오프셋 = 가리기 전 스트림 바이트, 헤더 `X-RCM-Worker-Session` · `X-RCM-Log-Offset`, 응답 `offset`. 서버 `jobs.log_offset` · `log_file_bytes`, 기동 때 길면 자름. 조각은 256 KiB 이하 · `\n` 에서 자름. 워커는 스트림만 담는 `stream.log`. 3판의 `log.pending` 없음. 2판: 서버 쓰기는 덧붙이기가 아니라 결정 323 의 절차 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 272 | 순서 있는 보고 큐 | 잡마다 phase → 로그 → 산출물 → finish, 디스크(`outbox.json`), 보내는 스레드 하나, 백오프 1→30초, 도는 동안 상한 없음, 끝난 뒤 24시간. 레인은 자기 큐가 끝나야 다음 claim. 2판: `outbox.json` 에 `acked`, 그 프로세스의 첫 로그 전송 전 빈 본문 질의(결정 329), finish 상한(결정 324) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 273 | 로그 완전성 | finish 의 `log_bytes` 와 `log_offset` 으로 `jobs.log_complete`(1/0/NULL). 키 `log_complete` 를 최근 행 · 잡 문서 · wait JSON 에(더하기만). 2판: 짧은 파일이면 미리 0 고정(결정 323) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | 3판의 `log_incomplete` 를 긍정형 + null 로 |
| 274 | 늦은 결과 기록 | events kind `late_result` 1건, 종료 잡 문서 · wait JSON 키 `late_result`(객체/null), 409 본문 `late_result_recorded`. 펜싱된 세션의 늦은 finish 는 기록 안 함(2차 Q-B). 웹은 그리지 않음. 2판: 한 `BEGIN IMMEDIATE` 에서 확인 · 넣기 · `log_complete` 쓰기(V2-11, §4.6) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-B) | — |
| 275 | 산출물 재업로드 · ready 조건 | 업로드는 큐의 finish 앞 단계, 재전송 창 안에서 다시. 처분 `ready` 는 그 잡의 묶음 행 · 해시 · 서버의 `bundle.tar` 파일이 맞을 때만(2판에서 파일을 더함, V1-12), 아니면 `failed` + `upload_failed` / `hash_mismatch` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 276 | 유예 중 표시 | 도는 잡의 `reason` 새 값 `worker_unreachable_grace`(`_busy_reason` 맨 앞), `estimate.finish_at` null, `ACTIONABLE_REASONS` · 웹 `ACTIONABLE` 의 `worker_down` 바로 뒤. 레인 `down` · 헬스 그대로. `schema_version` 1 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 277 | 워커 `finished_at` 자르기 | `[started_at, 수신 시각]` 으로 자른다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 278 | `lost` 요약 코드 | 새 `LOST_CODES` = `worker_self_stopped` · `worker_replaced` · `snapshot_unreachable`(finish 의 `outcome = lost` 일 때만). 유예 만료는 `worker_unreachable`(cancelling 포함, `cancel_unreachable` 만들지 않음). 재시작 이어받기는 `worker_restarted_without_job`. 번역은 같은 PR. 2판: 서버만 쓰는 `orphan_unverified` 를 더함(결정 327) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 279 | PR 별 레인 | PR0 short · PR1 full · PR2 full · PR3 full · PR4 short(§5) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-A) | — |
| 320 | 자기 멈춤 감시 스레드와 여유 m(D-a, 267 을 대신) | 표식 잡만. heartbeat 와 따로 도는 감시 스레드가 `WATCHDOG_TICK_SECONDS`(1초)마다 `wgap ≥ G_J − m_J` 를 본다. `ok_at` = 2xx 를 받은 요청을 **보낸** 시각(벽 · 단조), 잡의 첫 값은 그 claim 요청을 보낸 시각. `m_J = 15(heartbeat 요청 시한, client.py:83) + grace_J + SELF_STOP_SLACK_SECONDS(10 = 틱 1 + 러너 점검 4 + 남김 5, F56)`. 하한 `L = max(T, 2H + 15)`, 설정 검증 `G ≥ L + 35`(`DISCONNECT_GRACE_MARGIN_SECONDS`), 표식 잡의 `grace_J = max(1, min(grace_seconds, G_J − L − 25))`. 넘으면 그룹 SIGTERM→`grace_J`→SIGKILL, finish `lost` + `worker_self_stopped`. 서버가 먼저 닫은 잡은 다음 2xx 의 `closed_jobs` 로 멈춘다 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-a. `ok_at` 의 처음 값만 D-a 의 「claim 응답 시각」에서 「claim 요청을 보낸 시각」(더 이르다)으로 — §12 V2-6. 메인 세션이 받아들임 | 253 의 「유예(약 10분)가 지나면」 → 서버 유예 G 보다 m(기본 35초) 먼저 |
| 321 | 닫힌 잡 통보(D-a) | heartbeat 2xx 응답 `closed_jobs: [{id, state, had_cancel}]` — 그 요청의 `jobs` 목록 가운데 서버에서 terminal 이고 그 세션의 잡. 워커 반응은 W4 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-a(V2 제안) | — |
| 322 | 취소된 잡이 유예로 lost(D-d) | `had_cancel` = 잡 행의 `cancel_requested_at` 있음. 워커는 `lost` + `had_cancel` 인 일반 잡을 멈춘다(1판 RL8 b 를 고침) | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-d | — |
| 323 | 서버 로그 쓰기 절차(D-b) | 잡별 로그 잠금 `_job_log_locks` 안에서 행 다시 읽기 → 오프셋 확인 → `log_file_bytes` 자리에 쓰기 · 뒤 자르기 → 조건부 커밋(`log_offset` 이 그대로 · busy · 같은 세션). 커밋 실패 · 쓰기 실패 · 0행이면 `log_file_bytes` 로 되자름. 파일이 `log_file_bytes` 보다 짧으면 `log_complete = 0` 고정 + 서버 로그 「log gap」, 파일 끝에서 이어 씀. 기동 정리도 같은 비교 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-b. 「`log_gap` 으로 표시」는 409 코드 `log_gap`(워커 오프셋이 앞섬)과 이름이 겹치지 않게 `log_complete = 0` 고정 + 서버 로그 줄로 — §12 V2-10. 메인 세션이 받아들임 | — |
| 324 | finish 상한(D-c) | 잡이 끝나고 `FINISH_HOLD_SECONDS`(300초)가 지났고 맨 앞 로그 · 산출물 항목의 마지막 시도가 HTTP 5xx 면 finish 먼저(산출물 `failed` + `upload_failed`, `log_complete` 는 서버가 false). 연결 실패면 순서 유지. 300 의 근거는 §4.5(백오프 상한 30초 × 10회 넘게 · 기본 유예 600초의 절반 · DB 바쁨 5초) | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-c(수치는 재료). finish 에 `log_complete` 칸을 따로 싣지 않고 서버가 `log_bytes` 로 계산 — §12 V2-3. 메인 세션이 받아들임 | — |
| 325 | 세션 폴더의 주인(D-e) | `owner.json` = `{server_sha256, token_name}`(설정의 `server` 문자열 + `server_name` 의 sha256, `whoami` 의 이름. 토큰 값 없음). 주인이 다르거나 없는 폴더는 잠가 보기 · 고아 정리 · 큐 보내기 · 지우기를 안 한다(7일 지나고 잠글 수 있을 때만 청소). 폴더는 프로토콜 검사 · `whoami` 뒤에 만든다 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-e | — |
| 326 | 죽은 세션 큐의 응답(D-f) | §4.4 의 표: 409 `session_fenced` · `not_registered` · 401/403 은 그 큐에만 적용, 이 프로세스를 멈추거나 D 의 id 로 재등록하지 않는다. 401/403 은 파일을 지우지 않는다. 등록은 D 의 큐가 끝난 뒤 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-f | — |
| 327 | 고아 식별 · 재확인 · 경고(D-g) | 신원 = 리더 pid 의 시작 시각(Linux `/proc/<pid>/stat` 22번째 칸, 그 밖 `ps -o lstart=`). 리더 없는 살아 있는 그룹 · 읽기 실패 · `proc.json` 없음은 「모름」 → `unverified_sessions` 로 등록, `D.ok_at + G_J − m_J` 에 재확인(확인되면 좁은 확인 finish), 그래도 모르면 서버가 유예 만료에 표식 잡을 요약 코드 `orphan_unverified` 로 닫는다(사람에게 보이는 경고 — events 에는 읽는 곳이 없어 요약 코드로, F60). DB `worker_sessions.orphan_unverified` | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-g. 「경고를 사건으로」를 요약 코드로 낸 것은 재료 — §12 V2-7. 메인 세션이 받아들임 | — |
| 328 | 서버 잠금 둘의 이름 · 범위 · 주인(V2-5) | `_heard_lock`(+ `_heard_at`): 요청의 세션 판정 + `heard` 갱신, janitor 의 유예 · 취소 미확인 판정 + 조건부 닫기만. `_job_log_locks`: 잡 하나의 로그 쓰기 절차만. 둘 다 `RemoteWorkersMixin` 이 가진다. 한쪽을 쥔 채 다른 쪽을 잡지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 1라운드 V2-5 를 고치며 재료가 정함 | — |
| 329 | 와이어 세부(V2-13) | `outbox.json` 의 `acked`, 빈 본문 질의는 그 프로세스가 그 잡의 로그를 처음 보낼 때 보내는 스레드가, `protocol_required` 문구 `"worker protocol 2 required: upgrade rcm on this worker"`(「not registered」 없음), 형식이 틀린 `X-RCM-Worker-Session` · `X-RCM-Log-Offset` 은 400(`error` 만), 로그 커밋 바쁨은 503 `database_busy` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 1라운드 V2-13 을 고치며 재료가 정함 | — |

### 11.1 부모 명세가 넘긴 결정의 대응표 (D-i, V1-9 · V2-8)

`docs/m5t-workplan.md:1045` 의 결정 행 「244 · 245 · 248 a~d · g · 249 · 250 — 옮긴 결정」과 `:1072` 의 M5t-b 핸드오프 표가
넘긴 3판 결정을 이 명세의 결정에 맞춘다. 3판 원문은 레포 밖의 3판 전문이고 메인 세션이 보관한다.

| 3판 결정 · 부분 | 3판 내용(요지) | 이 명세 | 어떻게 다루나 |
|---|---|---|---|
| 248 a | `worker_sessions` 표 · `worker_session_id`(유예 · 잊힌 잡 · 대체 판정을 세션 행으로) | 265 · 266 · 260 | 그대로 받음 |
| 248 a | claim 표식 `lease_id` | 265 | 두지 않음 — (잡, 세션)으로 충분(3판 r1 A-H1 이름 중복) |
| 248 a | 409 `reason`(`job_closed` · `stale_lease` · `log_gap`) | 269 · 329 | 키 이름을 `code` 로(F9). `job_closed` · `log_gap` 그대로, `stale_lease` 는 `session_fenced` · `not_registered` 로 나눔 |
| 248 b | 마스킹 전 바이트 오프셋 · 스풀 파일 · 두 길이 `log_raw_acked` · `log_file_bytes` | 271 · 323 | `log_offset` · `log_file_bytes`, `stream.log`. 쓰기 절차는 323 |
| 248 b | `log.pending`(64 KiB 상한) | 271 | 두지 않음 — 러너가 모든 묶음을 `\n` 으로 끝낸다(F34 · F35) |
| 248 b | `log_bytes_total` · `log_incomplete` | 273 | finish 본문 `log_bytes` 로만 받고 저장은 긍정형 `log_complete`(1/0/NULL) |
| 248 b | 재전송 상한 · 503 `log_write_failed` | 264 · 272 · 269 · 324 | 상한은 잡이 끝난 뒤 시간으로만(24시간), 5xx 면 300초 뒤 finish 먼저. 503 `log_write_failed` 그대로 + 되자름(323) |
| 248 b | 디스크 가득 때 메모리로 | 271 · 273 | 메모리로 물러서지 않음 — 그 길이 오늘의 4 MiB 조용한 버림(F32). 스풀 쓰기 실패는 세기만 하고 `log_complete = false` 로 정직하게(RL20) |
| 248 c | 끊김 유예 `worker_disconnect_grace_seconds`(기본 10분, `worker_timeout_seconds` 보다 큼) | 263 · 320 | 키 `disconnect_grace_seconds`(F25 · 3판 A-H2), 600초, 하한은 320 의 `L + 35` |
| 248 c | 기준 `max(세션 행 last_seen_at, 서버 기동)` 이나 `replaced_at`, 트랜잭션 안 재확인 | 266 · 261 · 328 | 벽시계 대신 메모리 단조 `heard`(F31), 기동 바닥(2차 Q-C), 펜싱은 시각을 다시 세지 않음(T8), 재확인 대신 `_heard_lock` + 조건부 UPDATE |
| 248 c | 잡 `reason` `worker_unreachable_grace` · 레인 `down` 유지 · 유예 중 ETA null | 276 | 그대로 받음 |
| 248 c | 응답의 `grace_seconds` 와 워커 자기 멈춤(`grace − (2 × heartbeat + kill 시한)`, `lost` + `worker_self_stopped`) | 257 · 320 | 값은 claim 응답 `job.disconnect_grace_seconds`(얼림). 판정은 감시 스레드, m = 요청 시한 + grace + 여유 |
| 248 c | 유예 중 취소는 `lost` 와 `cancel_unreachable` | 278 · 322 | `lost` + `worker_unreachable`(새 코드 없음), 다시 붙은 워커는 `had_cancel` 로 멈춤 |
| 248 d | finish 디스크 보관과 재전송 | 272 · 324 | `outbox.json`, 보내는 스레드 |
| 248 d | 재시작 순서(phase → 로그 → finish → register)와 버린 lease 의 `lost`(`worker_restarted`) | 268 · 325 · 326 · 327 | 순서 그대로(프로토콜 검사 · 폴더 주인 · 고아 판정을 앞에), 버린 세션은 `previous_sessions`, 코드는 오늘의 `worker_restarted_without_job` |
| 248 d | 응답별 큐 처리 | 269 · 326 · §4.5 표 | 살아 있는 프로세스의 표(§4.5)와 죽은 세션 큐의 표(§4.4)를 나눔 |
| 248 d | `late_result` | 256 · 274 | 같은 세션 · `lost` 잡의 finish 만 1건, 펜싱 세션 제외(2차 Q-B) |
| 248 d | 산출물 재업로드 · 멱등 확정 | 275 · §4.6 | 같은 해시 200, 첫 finish 가 이김, `ready` 는 파일까지 |
| 248 g | `lost` 자동 재실행 안 함 | §1.2 · §1.3 · §4.11 | 새 번호 없이 그대로 둠(M5n §13, `docs/m5n-workplan.md:652`) |
| 249 의 v26 | `worker_sessions` 표, `jobs` 의 `worker_session_id` · `lease_id` · `log_raw_acked` · `log_file_bytes` · `log_bytes_total` · `log_incomplete`, `worker_identity` 표 | §4.9(265 · 271 · 273 · 327) | `lease_id` · `log_raw_acked` · `log_bytes_total` · `log_incomplete` 는 위처럼 바꾸거나 뺌, `worker_identity` 는 M5t-c(v27). 더한 칸: `irreversible` · `disconnect_grace_seconds` · `log_complete` · `worker_sessions.orphan_unverified` |
| 249 의 v26 | 옛 빌드 거절 근거 newer-schema · 마이그레이션 시험 필수 · 되돌리기 지원 안 함 · `artifacts_state` · `unreachable_since` 안 둠 | F50 · RL24 | 그대로 받음 |
| 250 | register 와 claim 이 `worker_session_id` 를 요구, 없으면 409 와 `worker_protocol: 2`. 같은 0.10.0 의 옛 워커는 claim 전에 막힘 | 270 · 262 · 329 | 그대로 받되 이미 도는 레거시 잡의 보고는 받음(262), 문구에 「not registered」 없음(329) |
| 250 | 새 워커는 `worker_protocol ≥ 2` 없는 서버에 등록하지 않고 종료 3. 혼합 운영 경로 없음 | 270 · 258 | 버전이 다르면 버전 409 길을 먼저(RL32), 같으면 종료 3. 혼합 운영은 레거시 lease 만(262) |

대응이 없는 항목은 없다.

## 12. 격리 반박에서 바뀐 것

### 12.1 `docs/m5t-workplan.md` §12 가 넘긴 지적과 답한 곳

| 라운드 | id | 요지 | 이 판이 답한 자리 |
|---|---|---|---|
| 3 | A-H2 | `grace_seconds` 이름 충돌 | 키 `disconnect_grace_seconds`(F25, 결정 263, RL30) |
| 3 | A-H5 | 세션 펜싱 | §4.2, 결정 255 · 260 · 261 · 265, RL10 · RL11 |
| 3 | A-H6 | 재전송 상한은 시간만, 잡이 끝난 뒤에만 | §4.5, 결정 264 · 272, RL19 |
| 3 | M1 | 산출물 재업로드 순서 | §4.6, 결정 275, RL22 |
| 3 | M3 | finish 의 `lost` 허용 목록 | `LOST_CODES`(결정 278), §4.6 |
| 3 | M7 | 버전 검사를 먼저 | §4.2 register 순서, 결정 270, RL13 · RL32 |
| 3 | M8 | 등록 전 프로토콜 확인, 되살리지 않는 종료 코드 | §4.7, 결정 258 · 270, RL25 |
| 3 | M13 | 버린 lease 의 프로세스 그룹 정리 | §4.4, 결정 268, RL15 · RL31 |
| 3 | M14 | 잠자기를 넘는 시계 | §4.1.3 · §4.3, 결정 266 · 320(2판, 267 을 대신), RL26 · RL27 |
| 3 | R1 의 예(`grace_seconds` 충돌, 「연속 실패 20번」) | 코드를 읽기 전 처방 | 둘 다 버림(결정 263 · 264). 이 판은 §3 사실 F1~F51 을 먼저 세우고 설계마다 F 를 단다 |
| 3 | R2 | 상태 전이 표 없는 규칙 목록 | §4.1 이 첫 절, 칸마다 행 id |
| 3 | R5 | 같은 사실 5~7곳 반복 | 사실은 §3 한 곳, 다른 절은 F · 결정 번호 |
| 2 | A-H1 · A-H2 · A-H3 · A-M8 · B-M6 · B-M7 · A-L3 | 데이터 모델 없는 세션 · 유예 · 재시작 규칙 | §4.1 표 → §4.2 세션 표 · §4.3 · §4.4 · §4.9, 결정 265 · 266 · 268 |
| 2 | A-H4 | 유예 중 취소 | T4, §4.3(취소 미확인은 현재 · 들리는 세션만, 만료는 `lost`), RL8 · RL33 |
| 2 | A-M1 | 유예 중 레인의 상태 | `down` 유지, 새 레인 상태 없음(결정 276, F22) |
| 2 | A-H5 · B-H7 · A-H7 · A-M5 · B-M8 | 로그 길이 단위, 줄 끝 없는 꼬리, 끝나지 않는 재전송, 응답별 처리 | 단위 둘(`log_offset` · `log_file_bytes`), 꼬리는 러너가 없앰(F34 · F35), 재전송은 시간 · 끝난 뒤만, 응답표 §4.5(결정 271 · 272) |
| 2 | A-M6 | 산출물 재업로드 | 결정 275 |
| 2 | A-L5 | 늦은 결과로 남기는 범위 | finish 만, 같은 세션만, 1회(결정 256 · 274) |
| 2 | A-M4 | 옛 워커가 잡을 집는 것 | claim 409 `protocol_required`, 레거시 잡만 받음(결정 262, RL24) |
| 2 | A-M7 · B-M2 | DB v26 목록(신원 비밀 표는 M5t-c) | §4.9, `worker_identity` 없음 |
| 2 | A-L2 · A-L6 | 설정 키 이름, 결과 고리 변이 | 결정 263, §7.4 변이 26개(1판 11 + 2판 15) |
| 1 | A-H1 | `attempt` 이름 중복 | 새 이름 없음: lease = (잡, 세션), `lease_id` 도 안 둠(결정 265) |
| 1 | A-H3 | 같은 토큰의 두 프로세스 | 결정 255 · 265, RL10 · RL11 |
| 1 | A-H4 | 재시작한 워커의 등록이 먼저 `lost` 로 닫음 | 등록 전 큐 전달 + `previous_sessions`(결정 268), RL14 |
| 1 | A-H5 | 409 를 `closed_by_server` 로 읽음 | `code` 로 가름, `job_closed` 에도 일반 잡은 안 죽임(결정 256 · 269), RL5 · RL16 |
| 1 | A-H6 | `log_incomplete` 판별 수단 | finish `log_bytes` vs `log_offset` → `log_complete`(결정 273) |
| 1 | A-H7 | `lost` 뒤 그룹 자리가 풀림 | 표식 잡은 서버보다 먼저 워커가 멈춤(결정 320, §4.1.3 의 계산 1~5), 펜싱 잡은 확인 · 유예까지 busy(결정 261), RL1 · RL3 |
| 1 | A-M1 | 오프셋의 바이트 공간 | 가리기 전 스트림 바이트(결정 271, F14) |
| 1 | A-M2 | 서버 재시작과 유예 | 기동 바닥(결정 266), RL7 |
| 1 | A-M3 | 유예 중 취소 | 2라운드 A-H4 와 같은 답 |
| 1 | A-M4 | 유예 만료와 재연결 경쟁 | 같은 락 + 조건부 UPDATE(결정 266), RL4 |
| 1 | A-M5 | `lost` 뒤 도착한 finish | `late_result`(결정 256 · 274), RL21 |
| 1 | A-M6 | 워커 디스크 | 스풀 쓰기 실패 → `log_complete = false`, 시작 청소는 잠긴 세션 폴더를 안 지움(§4.4 · §4.5), RL20 |
| 1 | A-M7 | 버전 문자열 호환 | 프로토콜 판 2(결정 270) |
| 1 | A-M8 | DB 판 나누기와 guard 근거 | v26 은 M5t-b, v27 은 M5t-c. 근거는 `store.py` newer-schema(F50) |
| 1 | A-M9 | 유예 중 ETA | null(결정 276) |
| 1 | A-M12 | `worker_unreachable` 이름 중복 | 사유는 `worker_unreachable_grace`, 요약 코드 `worker_unreachable` 은 유예 만료에 그대로(결정 276 · 278) |
| 1 | A-L3 | 유예 값 검증 | 결정 263 |

답하지 못한 id 는 없다.

### 12.2 시나리오 표에서 바꾼 것

§7.2 가 전부다. 뺀 행은 없다. 1판에서 새로 둔 행은 RL31 · RL32 · RL33 이고 2판에서 새로 둔 행은 RL34~RL40 이다. 2판에서 고친
행은 RL1 · RL4 · RL8 · RL11 · RL12 · RL14 · RL15 · RL16 · RL19 · RL21 · RL22 · RL24 · RL25 · RL26 · RL29 · RL30 · RL31 이다.

### 12.3 1라운드 격리 반박 (1판 head `804c17d` 에서 2판으로)

V1 은 반박자 1, V2 는 반박자 2 다. 「1판」 열의 WP 줄 번호는 `docs/m5tb-workplan.md` 1판의 것이다.

| id | 등급 | 1판 | 2판 | 결정 |
|---|---|---|---|---|
| V1-1 · V2-1 | HIGH | 자기 멈춤 판정이 heartbeat 루프(`remote_worker.py:740-745`) 안이고 m = grace + H + 5 가 「H 마다 판정」을 가정. 끊긴 동안 한 바퀴 H + 15초(요청 시한 `client.py:83`), 서버가 돌아온 회차는 못 한 보고 재시도 뒤(F53) → 마지막 정상 heartbeat 뒤 약 G + 10초에 죽어 서버의 `heard + G` 닫기보다 늦음(RL1 · RL3 · RL4 깨짐). heartbeat 응답이 닫힌 잡을 알리지 않음(WP:231-234, 245, 253-255) | 감시 스레드(1초 틱), `ok_at` = 2xx 받은 요청의 보낸 시각, m = 15 + grace + 10(F56 실측), 하한 L · 설정 검증 `G ≥ L + 35`, 표식 잡 grace 상한, heartbeat `closed_jobs`. §4.1.4 R1 · R7, §4.1 계산 1~5, §4.3, RL1 · RL26 · RL34 · RL35, 변이 `self-stop-in-heartbeat-loop` · `self-stop-ignores-request-timeout` · `closed-jobs-omitted` | 고침 — 결정 320 · 321(D-a) |
| V2-2 | HIGH | 로그를 덧붙인 뒤 따로 커밋, 잡별 직렬화 없음(`remote_workers.py:630-659`, `_remote_lock` 은 `_log_partial` 만). 커밋 실패(503 `database_busy`) 뒤 재시도가 같은 바이트를 또 붙이고 `log_complete` 는 참. 같은 잡의 동시 요청 둘이 같은 `log_offset` 을 읽고 둘 다 붙임(WP:415-419) | 잡별 로그 잠금 안에서 행 다시 읽기 → `log_file_bytes` 자리에 쓰기 · 뒤 자르기 → 조건부 커밋, 실패 · 0행이면 되자르기. §4.1.4 R2, §4.5 절차 1~9, T15, RL36, 변이 `log-write-unlocked` · `log-append-not-positioned` | 고침 — 결정 323(D-b) |
| (뿌리) | HIGH 둘의 공통 | 상태를 바꾸는 두 단계를 한 단위로 묶지 않음 | 같은 뿌리를 표 전체에서 찾아 R1~R12 로 고침(§4.1.4): 유예 판정 ↔ heard(R3), late_result 확인 ↔ 기록(R4), ready ↔ 파일(R5), acked ↔ 2xx(R6), 서버 닫음 ↔ 워커 앎(R7), D 큐 ↔ 등록(R8), 폴더 잠금 ↔ 주인(R9), 띄우기 ↔ 신원 기록(R10), finish ↔ 로그(R11), 고아 확인 ↔ 유예 만료(R12) | 고침 — 메인 세션 지시 |
| V1-3 | MED | 런타임 쓰기 실패(503 `log_write_failed`, ENOSPC) 뒤 재전송이 남은 바이트 뒤에 붙음 — 자르기는 기동 때만(WP:421-423) | 매 요청이 `log_file_bytes` 자리에 쓰고 뒤를 자름, 쓰기 실패면 되자르기. RL36(c) | 고침 — 결정 323(D-b) |
| V2-3 | MED | finish 가 로그 · 산출물 뒤에 줄 서서 그 경로의 5xx 가 이어지면 끝난 잡이 24시간 running · 그룹 슬롯 · `rcm wait` 멈춤(WP:408-410, 443-445, 497) | 잡이 끝나고 300초 넘게 그 단계가 HTTP 5xx 면 finish 먼저(산출물 `failed` + `upload_failed`, `log_complete` false). 연결 실패면 순서 유지. 300초 근거 §4.5. RL40 · RL19(b), 변이 `finish-waits-forever`. D-c 의 「`log_complete=false` 를 그 finish 에 싣는다」는 서버가 `log_bytes` 로 계산하므로 칸을 따로 두지 않음 | 고침 — 결정 324(D-c) |
| V2-4 | MED | 죽은 세션 D 의 큐를 D 의 id 로 보낼 때 409 `session_fenced` 가 W5(종료 0)를 불러 새 프로세스가 0 으로 끝남, `not_registered` 면 D 의 id 로 재등록(WP:378, 435-436, 233) | D 의 큐 응답 표(§4.4): 그 큐에만 적용, W5 · 재등록 없음, 401/403 은 파일 보존, 등록은 D 큐 뒤. W10 · RL39, 변이 `dead-session-fenced-exits` | 고침 — 결정 326(D-f) |
| V2-5 | MED | 「같은 락(`_session_lock`)」 — grep 0, 범위 · 주인 없음(WP:316) | 새 이름 `_heard_lock`(+ `_heard_at`), 범위 = 세션 판정 + heard 갱신 / janitor 판정 + 조건부 닫기, 로그 요청 전체는 아님(그건 `_job_log_locks`), 주인 `RemoteWorkersMixin`, 잠금 순서. §4.2 · F57 | 고침 — 결정 328 |
| V2-6 | MED | 문턱 `G − m` 이 0 이하 · 아주 작을 수 있음. m 은 워커 grace 를 쓰는데 G 는 `worker_timeout_seconds` 하고만 견줌(WP:586). 「0 이하면 첫 실패 회차」(WP:245)는 실패 조건 없는 규칙과 모순. `ok_at` 처음 값 없음 | 하한 L = max(T, 2H + 15), 설정 검증 G ≥ L + 35, 표식 잡 grace 상한으로 늘 `G − m ≥ L`, 「0 이하」 문장 지움, 연결된 동안 멈추지 않음(§4.1 계산 5). `ok_at` 처음 값 = claim 요청을 보낸 시각 — D-a 는 「claim 응답 시각」이라 했으나 long-poll claim 은 보낸 뒤 80초까지 응답이 올 수 있어(F55) 응답 시각을 쓰면 m 에 그만큼을 더해야 한다. 보낸 시각은 응답 시각보다 이르거나 같아 더 보수적. RL30 · RL34(c), 변이 `grace-lower-bound-dropped` | 고침 — 결정 320(D-a), 처음 값은 D-a 에서 더 보수적으로(메인 세션 확인 바람 — notes) |
| V2-7 | MED | 확인할 수 없는 고아(`ps` 실패, 리더가 죽고 멤버만 산 그룹)는 G 까지만 막히고 서버가 슬롯을 풀면 표식 잡이 두 번 돌 수 있음(WP:377, 383-385, 703, 800) | Linux `/proc` 시작 시각, 리더 없는 그룹 · `proc.json` 없음도 「모름」, `unverified_sessions` · 재확인 `D.ok_at + G − m` · 그래도 모르면 요약 코드 `orphan_unverified`(사람에게 보임). 남는 위험은 §9. W12 · RL15 · RL29 · RL31 | 고침 — 결정 327(D-g). 「사건으로」를 events 행이 아니라 요약 코드로 낸 것은 events 를 읽는 곳이 없어서(F60) |
| V2-8 · V1-9 | MED | 부모 명세가 넘긴 248 a~d · g, 250, 249 의 v26 이 추적 안 됨(`docs/m5t-workplan.md:1045, 1072`, `PLAN.md:753`). PR0 의 PLAN 편집이 스냅숏에 없음(`PLAN.md:1, 628, 753`) | §11.1 대응표(대응 없는 항목 0), §5 PR0 행에 PLAN.md 일곱 자리 열거 | 고침 — D-i |
| V1-2 | MED | RL12(b) · RL14(a) · §4.4 의 T9 재전송은 디스크 큐(PR2)가 필요한데 PR1, RL4 는 PR1 인데 `late_result`(PR2) 단언, 변이 `late-result-changes-state` 는 RL4 를 들지만 PR2(WP:629, 628, 684, 686, 676, 365-366, 766) | RL4 · RL8 · RL12 · RL14 의 PR 칸을 나눔, §4.4 의 7 을 PR2 로, PR1 만 머지된 동안의 동작을 적음. 변이는 PR2 그대로(RL4 의 PR2 부분) | 고침 |
| V1-4 | MED | 유예 동안 cancelling 인 일반 잡이 G 에 lost 가 되면 heartbeat `cancel` 목록(`remote_workers.py:952`, 활성 잡만)에 없어 다시 붙은 워커가 계속 돌림 | `closed_jobs` 의 `had_cancel` 로 멈춤. RL8(b) · RL35, 변이 `lost-cancel-keeps-running` | 고침 — 결정 322(D-d) |
| V1-5 | MED | 세션 폴더에 서버 · 토큰 신원이 없음. 기본 워커 `data_dir`(`config.py:509`)을 같은 사용자의 워커가 같이 씀. 시험 서버를 가리킨 둘째 워커가 운영 워커의 죽은 세션을 입양, 401/403 이면 진짜 결과를 지움 | `owner.json`(서버 sha256 · 토큰 이름), 주인이 다르거나 없으면 손대지 않음, 401/403 은 파일 보존. W9 · RL38, 변이 `adopt-foreign-session` | 고침 — 결정 325(D-e) |
| V1-6 | MED | 머지 순서를 M5t-a PR2(#268)하고만 적음. M5t-a PR3 · PR4 · PR6 도 겹침 | 구현 PR1 의 시작 조건 = M5t-a PR1~PR7 전부 dev 머지(F54, `docs/m5t-workplan.md:690-697`). #268 은 이미 dev(96a8bd2) | 고침 |
| V1-7 | MED | 계약 잠금 시험 목록이 모자람(`ACTIONABLE_REASONS` 튜플 `tests/test_queue_m5n.py:135` · `tests/test_render_m5n.py:199` · `tests/test_web_schedule_m5s.py:43`, `RECENT_KEYS` `tests/test_status_schema.py:91`) | F58 에 전부(그 밖에 node 두 곳 · `NEW_RECENT` · register · heartbeat 응답 잠금도 찾음), §7.3 · §5 shared | 고침 |
| V1-8 | MED | §1.1 에 11-25 「OS 마다 무엇을 설치…」 · 「4」 · 11-26 「나의 지시없이 … 버전업」 이 없음. §2 「운영 반영 승인」 | §1.1 세 행(M5t-a 246 · 242 / §6 #14 · #15), §2 오너 행 고침 | 고침 — D-h |
| V2-9 | LOW | W6 「running 인데 서버가 내려갔다 → 모든 잡 멈춤 → 종료 3」 은 닿지 않고, 닿는다면 일반 잡을 죽여 253 에 어긋남(WP:234, 520-522) | 그 갈래를 지움(옛 빌드는 v26 DB 를 열지 않음, F50). 도는 중 받으면 그 요청만 실패로 | 고침 |
| V2-10 | LOW | 파일이 `log_file_bytes` 보다 짧은 경우(flush ≠ fsync) 처리 없음 | `log_complete = 0` 고정 + 서버 로그 「log gap」, 파일 끝에서 이어 씀. T16 · RL37. D-b 의 「`log_gap` 으로 표시」는 409 코드 `log_gap` 과 같은 이름을 두 뜻으로 쓰지 않으려고(M5s §3 #7 의 교훈) 칸 · 로그 줄로 냄 | 고침 — 결정 323(D-b) |
| V2-11 | LOW | 「`late_result` 기록이 아직 없으면」이 동시성에서 유일하지 않음, 트랜잭션 내용 열거 없음 | 한 `BEGIN IMMEDIATE` 의 네 단계 열거(§4.6), RL21(c) | 고침 — 274 문장 |
| V2-12 | LOW | 세션 폴더를 만든 뒤 프로토콜 검사 — 종료 3 재시작마다 빈 폴더가 쌓임 | 폴더는 프로토콜 검사 · `whoami` 뒤(§4.4 의 2~4), RL25 | 고침 |
| V2-13 | LOW | `outbox.json` 이 `acked` 를 저장 안 함, 빈 본문 질의의 주체 · 때 없음, `protocol_required` 문구 없음(옛 워커는 「not registered」 부분 문자열을 맞춤 `remote_worker.py:773`), 형식 틀린 헤더의 답 없음 | `acked` 저장, 빈 본문 질의 = 그 프로세스의 첫 로그 전송, 문구 확정(「not registered」 없음), 400. RL11 · RL16(d) · RL24 | 고침 — 결정 329 |
| V2-14 | LOW | M5t-c 순서 주장(`pair_proof` 를 등록 부작용 앞에)이 스냅숏에서 확인 안 됨 | F52 로 인용(PR #269 `docs/m5tc-workplan.md:216` · 결정 301~303), §4.2 register 순서 4 | 고침 |
| V1-10 | LOW | 변이 없는 규칙: 표식 잡만 자기 멈춤, claim 때 G · X 얼림, 현재 세션만 취소 미확인(RL33), 세션 검사 전 버전 검사(RL13), 펜싱된 세션은 `late_result` 없음(Q-B) — `PLAN.md:528` | 변이 `self-stop-all-jobs` · `grace-read-at-judgement` · `cancel-unconfirmed-any-session` · `register-session-before-version` · `fenced-late-result-recorded`, 2판 규칙의 변이도 더해 26개(§7.4) | 고침 |
| V1-11 | LOW | 표의 열린 칸: `ok_at` 처음 값, 워커가 모르는 cancelling 잡이 T5 에 running 만, 「들림」 이 벽인지 단조인지 | §4.1 용어(`ok_at` 처음 값, 「들림」 = 단조 gap), T5 의 잡 칸 running · cancelling(`store.py:4702-4708`) | 고침 |
| V1-12 | LOW | RL22 는 번들이 서버에 있어야 `ready` 라는데 §4.6 은 행 + 해시만(F17) | `ready` 는 행 · 해시 · `bundle.tar` 파일(F63), RL22(e) | 고침 — 275 문장 |
| V1 제안 | — | `irreversible` 없는 원격 가능 배포 · 스토어 프리셋을 `rcm check` · doctor 가 경고 | 넣지 않음. 이름으로 추측한 경고는 거짓 경보를 낸다. §1.3 · §9 행과 문서 경고만 | 안 받음 — 메인 세션이 정한 기본값 |
| V2 제안 | — | heartbeat 응답에 닫힌 잡을 싣기 | `closed_jobs` | 받음 — 결정 321 |

## 13. 구현하며 바뀐 것 (Changed while implementing)
