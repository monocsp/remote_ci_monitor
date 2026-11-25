# M5t-b 작업 명세: 결과 고리 · 워커 세션 · 로그 오프셋 · 끊김 유예 · 되돌릴 수 없는 잡의 그룹 홀드

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
> 이 명세의 출처는 위의 오너 발언, `docs/m5t-workplan.md` 의 결정 252~255 와 §12 「M5t-b 결과 고리 프로토콜」, 접두어 RL 로 쓴
> 시나리오 표, 오너가 맡긴 결정, M5t-c 명세(PR #269)의 결정 301~303 · 317 · 343 이다. 맡긴 결정은 모두 2026-11-26 에 정했다. 1차 ·
> 2차 · 4차 · 5차 · 6차는 메인 세션이 Codex 와 논의해 정했다. 3차(D-a~D-i)는 1라운드 격리 반박 뒤 메인 세션이 오너 위임으로 정했고
> 2라운드 뒤 Codex 와 다시 논의했다(4차). 3라운드 뒤에는 메인 세션이 오너 위임으로 결정 339-a~g 를 정했고 6차에서 Codex 와 논의해
> 확정했다. 339-e 는 그때 Codex 의견에 따라 바꿨다. M5t-c 결정 343 은 같은 날 M5t-c 명세(PR #269)에서 메인 세션이 정했고 메인 세션
> 지시로 이 명세에 들어왔다. Codex 원문 파일과 오너 발언 원본 파일은 레포 밖에 있고 이 레포가 공개라서 경로를 적지 않는다.
>
> 결정 번호는 256 부터다. M5t-b 는 256~279, 320~339, 350~359 를 쓰고 M5t-c 는 280~319 와 340~349 를 쓴다. 두 범위는 겹치지 않는다.
> 2판은 M5t-c 의 범위를 머리 인용에서는 「280~303」, §11 에서는 「280~312」로 달리 적었고 3판은 한 줄로 맞췄다(V1-12). 3판의 새
> 번호는 330~338 이었다. 4판의 새 번호는 339(세부 a~g, 메인 세션 결정)와 350(재료가 정함)이다.
>
> 이 판은 4판이다. 1판(head `804c17d`)은 1라운드 격리 반박에서 HIGH 3건(V1-1 · V2-1 · V2-2)을 받았다. 2판(head `1b45b7f`)은
> 2라운드에서 HIGH 1건과 그와 뿌리가 같은 MED 셋을 받았다. HIGH 는 V2-1 로, 감시가 Popen 전에 발동하면 멈춤 결정 뒤에 명령이
> 시작된다는 지적이다. 3판(head `572bd1d`)은 3라운드에서 HIGH 1건과 MED 6건, LOW 7건을 받았다. HIGH 는 두 반박자가 함께 짚었다. T5
> 의 「heartbeat `jobs` 에 없음 = 띄운 적 없음」이 순서가 보장되지 않는 메시지를 인증된 확인으로 썼다는 지적이다. 결정 335 가
> 버려진 heartbeat 도우미 스레드를 허용하면서 「heartbeat 는 한 스레드에서 차례로 간다」는 가정(`remote_workers.py:933-935`)이
> 깨졌다. 4판은 3라운드 지적을 반영했고 고친 것을 §12.5 에 적었다.
>
> 부모 명세의 셋째 판은 M5t 전체를 한 문서에 담았다가 거절됐고 그 판의 결과 고리 설계는 재료로만 썼다. 이 명세에도 3판이 있어
> 이름이 겹친다. 이 명세의 3판에서 바뀐 곳은 「3판:」, 「3판에서 더한」처럼 적었다. 「3판 r1 A-H4」, 「3판 M7」, 「3판의
> `log.pending`」처럼 부모 명세의 라운드, 지적 id, 설계를 가리키는 「3판」과 §11.1 의 「3판 결정」은 부모 명세의 셋째 판이다.
> 4판에서 바뀐 곳은 「4판:」, 「4판에서 더한」처럼 적었다.

## 0. 한 줄

원격 워커와 서버 사이의 연결이 끊기거나 어느 한쪽이 재시작돼도 잡의 결과가 정확히 한 번, 거짓 없이 요청자에게 돌아오게
한다. 장치는 넷이다. 첫째는 워커 세션이다. 워커 프로세스마다 세션 id 를 하나 두고 같은 이름의 새 세션이 등록하면 옛
세션을 펜싱한다(결정 255). 둘째는 끊김 유예다. 기본값은 600초이고 잡마다 claim 때 그 값을 얼린다. 되돌릴 수 없음 표식
`irreversible = true` 를 단 프리셋만 워커가 유예가 끝나기 전에 스스로 멈춘다(결정 253). 멈출지는 heartbeat 와 따로 도는
감시 스레드가 정한다. 마지막 2xx 응답 뒤의 경과가 G − m 이상이면 멈춘다. 여유 m 은 요청 시한과 SIGTERM 에서 SIGKILL 까지의
대기와 남김을 더한 값이다(결정 320). 셋째는 로그 오프셋과 순서 있는 보고 큐다. 워커는 phase, 로그, 산출물, finish 순서로
보내고 큐를 디스크에 남겨 재시작을 넘긴다. 서버는 잡마다 잠금 하나를 두고 그 안에서 오프셋 확인, 정해진 위치에 쓰기, 커밋을
한 단위로 처리한다(결정 323). 넷째는 3판에서 더한 감독자와 그룹 홀드다. 표식 잡은 워커와 같은 파이썬으로 띄운 작은 감독자
아래에서 돈다. 워커나 서버의 로컬 레인이 죽으면 감독자가 명령 그룹을 끝낸다(결정 331). 그래도 프로세스가 죽었다는 확인
없이 그 잡이 닫히면 서버는 그 잡의 동시성 그룹을 영속 홀드로 잡아 둔다. 홀드는 인증된 워커의 확인이나 admin 의
`rcm release-hold <job>` 으로만 풀린다(결정 330). 그래서 kill -9 뒤 아무도 워커를 다시 띄우지 않는 경우, 고아의 신원을 끝내
모르는 경우, 감독자 자체가 죽는 경우 가운데 어느 것도 그룹을 조용히 풀지 않는다. §9 도 같은 주장을 한다. 2판은 상태를
바꾸는 두 단계를 한 단위로 묶었다. 묶을 수 없으면 늘 안전한 쪽으로 어긋나게 했다(§4.1.6 점검표). 3판은 그 뿌리를 한 번 더
고쳤다. 2판의 멈춤 증명은 이미 도는 프로세스 하나와 HTTP 왕복 하나만 가정했다. 3판은 그 증명을 잡의 모든 단계(claim, 준비,
Popen 직전, 실행, SIGTERM 에서 SIGKILL 까지, 거두기)와 워커의 모든 죽음(정상 멈춤, 감시 발동, SIGKILL, 다시 안 뜸)에 대해
다시 썼다. 단계마다 멈춤 판정을 누가 언제 보는지는 표로 두었다(§4.1.4 · §4.1.5). 첫 절은 상태 전이 표와 타이머별 시계(§4.1)
이고 나머지 설계는 그 표의 칸에서 나온다. DB 는 v26, 워커 프로토콜은 2 가 된다. 구현 PR 은 넷이다. PR1 이 줄기이고 PR2 와
PR3 이 그 뒤에 병렬로 가며 PR4 가 실기 수용이다. 구현 PR1 은 M5t-a 가 모두 머지된 뒤 시작한다(F54 · §5).

4판은 뿌리를 한 번 더 고쳤다. 서버가 「확인」으로 쓰는 메시지마다 무엇이 그 메시지의 순서와 신선도를 보장하는지 적었고(§4.12 표)
보장이 없는 메시지는 확인으로 쓰지 않는다. 확인이 될 수 있는 것은 한 번 참이 되면 다시 거짓이 될 수 없는 사실을 그 사실이 선 뒤에
본 쪽이 보낸 메시지뿐이다. 「이 그룹은 사라졌다」와 「이 프로세스는 죽었다」가 그런 사실이다. 「지금 목록에 없다」 같은 지금
상태는 늦게 오거나 순서가 뒤바뀌어 오면 거짓이 된다. 그래서 heartbeat 에 순번(`seq`)과 「끝난 claim 까지」(`claims_settled`)를
싣는다. 그래도 되돌릴 수 없는 잡이 그 메시지로 닫히면 홀드를 건다(결정 339).

## 1. 출처와 오너 결정

### 1.1 요구와 이 명세

요구마다 그 요구가 말하는 것과 이 명세가 정하는 것을 나란히 둔다.

| 요구(출처) | 요구가 말하는 것 | 이 명세가 정하는 것 |
|---|---|---|
| 「3. rcm 작업을 진행해서 결과 전달하는 컴퓨터」(2026-11-24) | 실행 머신이 결과를 전달한다 | 결과 전달을 끊김 · 재시작 · 중복에 견디게 한다(§4.2~§4.6) |
| 「Rcm워커 다 한 다음에 어떻게 알려주는거까지 해야지」(2026-11-24) | 워커가 끝낸 뒤 알림까지 | 알림 자체(사람용 마지막 줄 · `ran_on` · `rcm wait --notify`)는 M5t-a 결정 248 e·f 다. 이 명세는 알릴 결과가 사라지거나 틀리지 않게 하는 쪽(결과 고리) |
| 결정 253 「되돌릴 수 없는 것만 멈춤」 | 표식 프리셋만 유예(약 10분) 뒤 워커가 스스로 멈추고 결과는 종료 3, 나머지는 계속 | 표식 키 `irreversible`(결정 257), 유예 600초(결정 263), 자기 멈춤(결정 320 — 1판 267 을 대신), 일반 프리셋은 계속 돌고 서버는 유예 뒤 `lost`(결정 256). 3판: 워커가 죽어도 감독자가 명령을 끊고(결정 331), 프로세스 죽음 확인 없이 닫히면 그 그룹을 사람이 풀 때까지 잡아 둔다(결정 330) |
| 결정 255 「지원 안 함」 | 워커 이름당 살아 있는 세션 하나, 새 세션이 옛 세션을 펜싱, 옛 세션은 409 받고 잡 멈춘 뒤 끝남 | 세션 표와 펜싱(결정 265), 펜싱된 잡의 자리(결정 261), 종료 코드 0(결정 258) |
| 결정 252 「셋으로 나눈다」 | M5t-b 는 상태 전이 표부터 | §4.1 이 첫 절 |
| 2026-11-26 「멀티에이전트로 나눠서 … dev batch로」 | 병렬로 나눌 수 있는 것은 나눈다 | §5: PR1 뒤 PR2 · PR3 이 겹치는 파일 없이 병렬 |
| 2026-11-26 「이후에 물어볼거 … 코덱스와 논의」 | 새 질문은 Codex 와 논의 | 위임 결정 256~279 · 320~339 · 350(§11). 새 질문도 메인 세션이 Codex 와 논의해 정한다 |
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
  `/proc` 신원, 재확인, 경고다. D-h 는 오너 발언 셋과 §2 문구이고 D-i 는 결정 번호 대응표와 PR0 의 PLAN 편집이다. 1라운드 결정
  320~327 은 2라운드 뒤 Codex 와 논의했다(4차). 320~326 은 동의로 확정됐고 327 은 330 으로 바뀌었다.
- 4차 위임 결정(2026-11-26, 메인 세션이 Codex 와 논의해 정함)은 셋이다. 아래 따옴표는 원문 요지이고 정본은 레포 밖의 결정
  표다.
  - 330(327 을 바꿈): 「irreversible 잡이 lost/orphan_unverified 이거나 프로세스 죽음 · 자기 멈춤 확인 없이 닫히면 영속하는
    그룹 홀드. 풀리는 길은 인증된 워커 확인 또는 admin `rcm release-hold <job>`(감사 이력 · 사건), 어디서나
    needs_action(rcm check · doctor · 웹 · 대기열 이유). irreversible 프리셋은 비지 않은 동시성 그룹이 있어야 한다(없으면
    프리셋 이름의 고정 합성 그룹 — 설정 검증 또는 자동, 재료가 정하고 근거를 적는다). 새 CLI · 라우트 · DB 열(또는 표)은 v26
    안에서.」 Codex 는 「식별 · 보고는 맞지만 irreversible 잡의 그룹을 G 에 푸는 것은 결정 253 에 대한 fail-open」이라고 했다.
  - 331: 「irreversible 명령은 같은 파이썬의 작은 감독자 아래 띄운다(setsid). 감독자는 기대한 워커 pid 를 기록하고 시작 때
    다르면 닫힌 쪽(명령을 띄우지 않음), 명령 그룹을 소유 · 수거, getppid() 가 바뀌면 그룹을 SIGTERM→SIGKILL. 폴링 간격 · 종료
    대기는 재료가 실측해 정한다(이 Mac 에서). 감독자 자체가 죽거나 명령이 세션을 빠져나가는 경우는 330 이 막는다. 일반(되돌릴
    수 있는) 잡에는 쓰지 않는다(결정 253: 일반 잡은 계속 돈다).」
  - 332: 「lanes = 0 의 0.5초 DB 폴링은 이번에 바꾸지 않는다. §1.3 에 「받아들임, 재지 않음 — 막힌 claim 하나에 초당 DB 폴링
    둘」.」 Codex 는 「무시할 만하다고 쟀다」고 쓰지 말라고 했다.
- 5차 위임 결정(2026-11-26, 메인 세션이 Codex 와 논의해 정함)은 3판 재료가 고른 선택을 확정했다. (a) 그룹 없는 표식 프리셋은
  자동 그룹 `irreversible:<이름>`(전역)을 쓰고 claim 때 유효 그룹을 저장한다. (b) 330 과 331 을 서버 자기 레인의 표식 잡에도
  적용한다. (c) 홀드는 프로세스 그룹이 사라졌다는 확인 없이 닫힐 때만 건다. (d) `rcm check` 는 홀드를 경고로 보이고
  `rcm doctor` 는 종료 코드를 바꾸지 않는다. (e) 세션을 빠져나가는 명령은 남는 위험이다. Codex 는 이것이 fail-open 임을
  밝히라고 했다. RL49 와 문서가 그 경우에 이름을 붙이고 `irreversible` 프리셋에 그런 명령을 쓰지 않는다고 적는다(§9). (f)
  추천이던 333~338, 감독자 폴링 0.25초, `ps -o lstart=` 를 `LC_ALL=C TZ=UTC` 로 읽고 못 읽으면 「모름」으로 두는 것을 확정했다.
- M5t-c 결정 317(PR #269 의 `docs/m5tc-workplan.md`, 메인 세션이 Codex 와 정함)은 2026-11-26 메인 세션 지시의 문구로 옮긴다.
  틀린 MAC 의 hello, 421, 인증되지 않은 어떤 응답도 워커를 종료 0 으로 끝내지 않는다. 워커는 토큰 없이 계속 다시 시도하고
  10분마다 경고하며 FAIL/모름 상태를 보인다. 종료 0(되살리지 않음)은 M5t-b 의 펜싱과 M5t-c 의 인증된 두 경우에만 남는다. 두
  경우는 확인된 서버가 401 로 토큰을 거절하고 새 hello 는 여전히 통과하는 경우와 등록 증명 `bad_mac` 이 세 번 이어지는
  경우다. 이 결정은 `docs/m5tc-workplan.md` §11 의 317 행(PR #269)에 있다.
- M5t-c 명세 PR #269 의 결정 301~303(F52): 확실한 신원 불일치는 종료 0 이고 서비스가 되살리지 않는다. 연결 실패와 5xx 는
  프로세스 안에서 다시 시도한다. 프로토콜 불일치는 M5t-b 대로 종료 3 이다. 부모 명세의 M8 은 「펜싱 · 확실한 신원 거절」로
  좁힌다. 3판: 303 의 종료 0 범위는 위의 M5t-c 결정 317 이 「인증된 두 경우」로 더 좁혔다.
- 3라운드 뒤 메인 세션 결정 339-a~g(오너 위임, 2026-11-26)는 아래와 같다. 따옴표 안은 원문 요지이고 정본은 레포 밖의 결정 표다.
  6차 위임 결정(같은 날, 메인 세션이 Codex 와 논의해 정함)이 339-a~d, 339-f, 339-g 를 재료가 고른 방법대로 확정했고 339-e 는
  바꿨다.
  - 339-a: 「하트비트 순번: 세션마다 단조 증가 `seq`(정수, 하트비트 본문). 서버는 세션별 마지막으로 받아들인 seq 보다 작거나 같은
    하트비트의 jobs 목록을 무시(heard 는 올리되 「없음 = 시작 안 함」 판정에 쓰지 않는다). 또 jobs 목록은 그 잡의 claim 커밋
    이후에 워커가 만든 것이어야 「없음」을 센다(하트비트에 `claimed_seen` 또는 워커가 아는 마지막 claim id — 재료가 가장 작은
    방법을 정한다).」
  - 339-b: 「X 잡(irreversible)은 T5 로 닫힐 때 늘 홀드를 만든다. 같은 세션의 확실한 확인(previous_sessions 의 group_gone: true
    등)이 뒤에 오면 그 홀드를 푼다.」
  - 339-c: 「감독자: (1) Popen 창에서 받은 SIGTERM 은 Popen 직후 그룹에 한 번 전달한다; (2) 명령이 끝났는데 그룹에 남은 멤버가
    있고 부모가 사라졌거나 SIGTERM 을 받았으면 SIGKILL 로 그룹을 쓸고 끝난다 — S7 의 두 번째 grace 가 증명 상한을 넘지 않게(첫
    SIGTERM 부터 grace_J 시계); (3) 감독자 자신의 시작 실패는 명령의 종료 코드와 겹치지 않는 고유 종료 코드 + 로그 표식; (4) `-I`
    대신 PYTHONPATH 를 지키는 방식(또는 -I 를 유지하고 그 이유와 소스 트리 실행의 한계를 적는다 — 재료가 정한다).」
  - 339-d: 「홀드의 그룹은 claim 때 잡 행에 저장한 유효 그룹 · 범위 · 풀에서 가져온다(5차 (a) 의 「claim 때 유효 그룹을 저장」).
    프리셋이 사라졌거나 표식이 빠져도 claim 때 X 였으면 홀드. 옛 잡(세션 NULL · 저장값 없음)이면 합성 그룹 `unknown:<job>` 이
    아니라 「그 잡의 프리셋 이름의 irreversible 합성 그룹」 또는 재료가 정한 닫힌 쪽 규칙 — 열리는 쪽은 금지.」
  - 339-e: 「홀드가 생기면 `[[notify]]` 를 한 번 부른다(잡 끝 알림과 별개의 사건 종류, 「그룹이 잡혔다, rcm release-hold」). PR
    배치: 홀드 표시와 알림이 홀드를 만드는 PR 과 같은 릴리스에 들어가게 §6 을 고친다.」 6차에서 이 결정을 바꿨다. Codex 는 새
    사건을 기본으로 켜면 기존 훅이 같은 잡에 두 번 불려 호출과 부작용이 겹친다고 했다. 그래서 `[[notify]]` 에 새 기본 사건을
    더하지 않는다. `irreversible_hold` 는 규칙이 `on` 에 적을 때만 부르는 opt-in 값이다. 대신 홀드를 만든 잡의 잡 끝 알림(이미
    가는 것)에 환경 변수 `RCM_HOLD=1` 과 `RCM_HOLD_GROUP=<그룹>`, 본문 한 줄 「group held — run rcm release-hold <job>」을 싣는다.
    그러면 기존 규칙도 호출이 늘지 않은 채 홀드를 안다. `RCM_EVENT` 는 모든 알림에 싣는다(§4.12).
  - 339-f: 「로그 읽는 쪽(rcm logs, SSE, 웹)은 커밋된 log_file_bytes 까지만 읽는다.」
  - 339-g: 「`_heard_lock` 은 잡 단위(또는 짧은 판정만, 커밋은 밖) — 한 틱에 닫는 잡 수 상한, 증명 2 에 서버 잠금 대기를 넣거나
    잠금이 커밋을 감싸지 않게.」
- 6차는 결정 350 도 확정했다. 홀드의 기계 판독 표면은 PR1 이, 사람용 화면은 PR3 이 만든다(§5).
- M5t-c 결정 343(PR #269 의 메인 세션 결정, 2026-11-26 메인 세션 지시로 이 명세에 들어옴)의 요지는 이렇다. 토큰 행에 `pair_id` 가
  있는 워커, 곧 M5t-c 로 짝지은 워커에게는 M5t-b 의 409 `session_fenced` 와 409 `protocol_required` 에도 M5t-c 의
  `pair_reject_mac` 을 싣는다. MAC 규칙은 M5t-c 결정 340 · 342 와 같아서 요청마다 다른 nonce 헤더 `X-RCM-Pair-Nonce` 와 요청
  본문의 sha256 을 쓴다. 짝지은 워커는 그 MAC 이 검증될 때만 펜싱에 종료 0 한다. 검증되지 않은 `session_fenced` 를 받으면 새 hello
  뒤에 다시 시도한다(M5t-c J12′). `protocol_required` 는 MAC 이 검증되면 M5t-b 의 종료 3 으로 간다(M5t-c J12). 검증되지 않으면
  종료 3 으로 끝내지 않고 새 hello 뒤 등록 백오프로 프로세스 안에서 다시 시도하며 상태 사유는 `registration_refused` 다(M5t-c
  J13). 진짜 서버는 짝지은 토큰의 키를 늘 갖고 있으므로 검증되지 않은 응답은 위조이거나 키를 잃은 서버에서 온 것이고, 재시작은
  5~10초 간격의 재시작 되풀이만 만든다. 이 `protocol_required` 규칙은 메인 세션 결정으로 6차 표의 「짝지은 워커의 검증 안 된
  `protocol_required` 는 종료 3 유지」를 대신한다. 짝짓지 않은 옛 토큰의 워커는 M5t-b 동작 그대로다. MAC 을 다는 일은 M5t-c PR1 의
  `_send_error`(`server.py:5624`)가 한다. M5t-b 는 그 409 들을 같은 오류 길(`ApiError` 에서 `_send_error` 로)로만 내고 우회하지
  않는다. 이 결정은 `docs/m5tc-workplan.md` 의 343 행(PR #269)에 있다. 340 · 342 는 M5t-c 명세(`4614c3b`)의
  `docs/m5tc-workplan.md:309, 311, 518-519` 에 있다.

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
  경고한다(결정 327). 3판에서는 그 잡의 그룹을 홀드로 잡아 둔다(결정 330, §9).
- lanes = 0(분배 전용 코디네이터)의 0.5초 DB 폴링은 부모 명세가 넘긴 것이고 이번에 바꾸지 않는다. 결정 332 의 문구는
  「받아들임, 재지 않음 — 막힌 claim 하나에 초당 DB 폴링 둘」이다. 프로파일에서 부담이 보이면 그때 다룬다.
- 명령이 프로세스 그룹과 세션을 빠져나가 남긴 자손(`setsid`, 이중 fork 데몬)을 찾지 않는다. 감독자도 워커도 그 프로세스를
  볼 수 없다. macOS 에는 자손을 거두는 subreaper 가 없다. 문서에 「irreversible 프리셋의 명령은 데몬을 남기지 않는다」를
  적는다. 그 경우는 확인 없는 닫힘에 거는 홀드(330)만 덮는다(§9).
- 홀드를 시간으로 저절로 풀지 않는다. 결정 330 이 홀드를 영속으로 정했다.
- 짝지은 워커의 거절 MAC 을 만들고 검증하는 일은 하지 않는다. M5t-c 결정 340 · 342 · 343 에 따라 M5t-c PR1 이 한다. 이 명세는 409
  를 `_send_error` 길로만 낸다(§4.2).

## 2. 범위: 누가 무엇을 하나

rcm 이 이 명세의 PR1~PR4 로 대부분을 만든다. 이 레포를 쓰는 앱 프로젝트는 프리셋에 표식을 단다. 오너는 명세를 승인하고
홀드가 걸리면 그 머신을 확인한 뒤 admin 토큰으로 `rcm release-hold` 를 실행한다. 운영 반영은 2026-11-26 목표에 넣어
승인했다. 메인 세션은 결정 기록과 조율, main 릴리스와 운영 반영을 맡는다.

| 누가 | 무엇 |
|---|---|
| rcm (이 명세, PR1~PR4) | 세션 · 펜싱 · 유예 · 자기 멈춤 · 재시작 순서 · 고아 정리 · 프로토콜 2 · 로그 오프셋과 보고 큐 · `late_result` · `log_complete` · 화면 사유 · DB v26 · 문서 · 3판: 감독자 · 그룹 홀드 · `rcm release-hold` |
| 이 레포를 쓰는 앱 프로젝트(그쪽 세션, 오너 지시 뒤) | 두 번 돌면 안 되는 배포 계열 프리셋에 `irreversible = true` 를 단다. 모르는 프리셋 키는 기동 실패라(F23) 그 프리셋을 읽는 서버가 모두 이 기능이 든 버전 이상일 때만 단다. 3판: `local_only = true` 프리셋에도 효과가 있다 — 서버의 로컬 레인에서도 감독자 아래 돌고(331), 서버가 그 잡을 확인 없이 닫으면(서버 강제 종료 뒤 재기동) 그룹 홀드가 걸린다(330). `concurrency_group` 이 없으면 서버가 합성 그룹 `irreversible:<프리셋 이름>`(범위 `global`)을 쓴다(결정 330) |
| 오너 | 명세 승인(머지). 레인 답(결정 279, 위임으로 확정). 홀드가 걸리면 그 머신을 확인한 뒤 admin 토큰으로 `rcm release-hold <job>`(결정 330 — 사람의 일). 운영 반영은 오너가 목표에 넣어 승인함(2026-11-26), 큐가 빌 때까지 기다림. 2026-11-26 지시로 그 밖의 질문은 메인 세션이 Codex 와 정하고 완료 보고서에 적는다 |
| 메인 세션 | 결정 페이지 · Codex 논의 기록 · dev-batch 운용 · M5t-c 세션과 결정 번호 · DB 판 조율(§6 #11 · #16) · main 릴리스 · 버전업 · 운영 반영(큐가 빌 때, M5t-a · M5t-c 와 함께 묶을지는 메인 세션의 릴리스 계획) |

## 3. 실측과 코드 제약

기준은 고정 sha `c8590dd79c60dde572f717454af588359b27da64` 다. 지금의 `origin/dev` 이고 M5t-a PR3 #271 까지 들어 있다(2026-11-26
`git fetch` 뒤 재확인). 이 명세 브랜치의 head `572bd1d` 와 `src/` · `tests/` · `PLAN.md` 가 같다(`git diff --stat c8590dd 572bd1d
-- src tests PLAN.md` 는 0 파일). 3판까지의 기준 `fdf196d` 로 적은 줄은 §3.0 의 핀 이동 표로 옮겼고 그 표를 문서 전체에 적용했다.
`DB_VERSION` 은 25 다(F54). 구현 PR 은 dev 에서 줄 번호를 다시 찾는다. 2판 명세의 줄은 「WP2:<줄>」로(`docs/m5tb-workplan.md` @
`1b45b7f`), 3판 명세의 줄은 「WP3:<줄>」로(같은 파일 @ `572bd1d`) 적는다. 모듈 파일은 `src/remote_ci_monitor/` 를 빼고 적었다. 각
사실이 설계에 미치는 영향은 표의 마지막 열에 적었다. 운영 수치는 받지 않았으므로 운영 빈도는 `—` 로 둔다. 같은 사실은 이 절에 한
번만 두고 다른 절은 F 번호와 결정 번호로 가리킨다.

이 명세가 쓰는 내부 용어를 먼저 풀어 둔다. 워커는 서버에서 잡을 집어 가 실제로 돌리는 머신(`rcm worker`)이다. 레인은 한
머신이 잡을 동시에 돌리는 칸이다. claim 은 빈 레인이 대기 잡을 하나 집어 가는 일이고 heartbeat 는 워커가 살아 있다고
서버에 주기적으로 보내는 보고다. janitor 는 서버가 5초마다 돌리는 정리 루프다. `lost` 는 결과를 모르는 채로 닫힌 잡의
상태이고 `rcm wait` 는 이때 종료 3 을 낸다. 펜싱은 같은 이름의 새 세션이 등록했을 때 서버가 옛 세션의 요청을 409 로
거절하는 일이다. lease 는 잡과 그 잡을 claim 한 세션의 쌍이다. M5q 의 따라가기는 워커가 서버 버전에 맞춰 설치본으로 자신을
다시 실행(exec)하는 일이다. 표식 잡은 `irreversible = true` 프리셋의 잡이다. 그룹 홀드는 표식 잡이 프로세스가 죽었다는
확인 없이 닫혔을 때 그 잡의 동시성 그룹을 잡아 두는 기록이다(§4.12). 감독자는 표식 잡의 명령을 띄우고 워커가 죽으면 그
명령 그룹을 끝내는 작은 프로세스다(§4.13).

### 3.0 핀 이동 표 (4판, V1-7 · V2-6)

4판은 고정 sha 를 `fdf196d` 에서 `c8590dd` 로 옮겼다. 옮긴 방법은 이렇다. `git diff -U0 fdf196d c8590dd -- <파일>` 의 덩어리로 옛
줄을 새 줄로 옮기는 스크래치 스크립트(레포 밖)를 3판에 나온 모든 `<파일>:<줄>` 에 돌렸다. 바뀐 줄에 걸린 것과 3판이
`origin/dev`(`96a8bd2`) 기준으로 적은 것은 손으로 내용을 다시 찾았다(표 끝의 「손」 행). 바뀌지 않은 파일의 줄은 그대로다.
`store.py`, `core/*.py`, `worker.py`, `janitor.py`, `release_secrets.py`, `core/artifacts.py`, `AGENTS.md`, 표에 없는 `tests/*` 가
그렇다. `PLAN.md` 는 `:671` 한 줄이 바뀌었지만 줄 번호는 그대로다.

견본으로 몇 줄을 확인했다. `server.py:256-260` 은 `_JOB_RE`, `:267` 은 `_WORKER_JOB_RE`, `:250` 은 `JANITOR_SECONDS` 다.
`runner.py:360-369` 는 requires 안의 확인, `:383-391` 은 Popen, `:427` 은 `last_check = started` 다. `remote_worker.py:285` 는
`self.running`, `:432-434` 는 `heartbeat_once` 의 목록 찍기, `:199-200` 은 `should_cancel` 이다. `cli.py:3564-3565` 는 launchd
`KeepAlive`, `:3592-3593` 은 systemd `Restart` 다.

| 3판(WP3) | 4판 |
|---|---|
| `CHANGELOG.md:1562` | `CHANGELOG.md:1694` |
| `CHANGELOG.md:2164-2168` | `CHANGELOG.md:2296-2300` |
| `CHANGELOG.md:580-584` | `CHANGELOG.md:712-716` |
| `cli.py:3507, 3535` | `cli.py:3564, 3592` |
| `cli.py:3956, 4012-4017` | `cli.py:4074, 4130-4135` |
| `cli.py:4174-4228` | `cli.py:4292-4346` |
| `cli.py:4576-4579, 3507-3508, 3535-3536` | `cli.py:4702-4705, 3564-3565, 3592-3593` |
| `cli.py:4580-4614` | `cli.py:4706-4740` |
| `cli.py:4645` | `cli.py:4833` |
| `cli.py:5345-5348` | `cli.py:5621-5624` |
| `client.py:126-167` | `client.py:145-186` |
| `client.py:83, 118-123` | `client.py:83, 137-142` |
| `client.py:83, 89-93, 110-115, 172-181, 140, 211-225` | `client.py:83, 89-93, 129-134, 191-200, 159, 230-244` |
| `client.py:83, 89-93, 508-512, 547` | `client.py:83, 89-93, 527-531, 566` |
| `client.py:83, 89-93, 511, 547` | `client.py:83, 89-93, 530, 566` |
| `client.py:859` | `client.py:878` |
| `config.py:104, 510, 1272-1280, 1845-1846` | `config.py:103, 521, 1378-1386, 1960-1961` |
| `config.py:125-126` | `config.py:124-125` |
| `config.py:132` | `config.py:131` |
| `config.py:1323` | `config.py:1429` |
| `config.py:1323-1328` | `config.py:1429-1434` |
| `config.py:1323-1328, 1845-1846` | `config.py:1429-1434, 1960-1961` |
| `config.py:134, 1351-1352` | `config.py:133, 1457-1458` |
| `config.py:509` | `config.py:520` |
| `config.py:632-661` | `config.py:646-675` |
| `config.py:839-841, 896-900` | `config.py:853-855, 910-914` |
| `docs/configuration.md:1185, 1386` | `docs/configuration.md:1213, 1416` |
| `docs/operating.md:786` | `docs/operating.md:1004` |
| `remote_worker.py:1025-1027` | `remote_worker.py:1051-1053` |
| `remote_worker.py:1059-1062` | `remote_worker.py:1085-1088` |
| `remote_worker.py:129-132` | `remote_worker.py:141-144` |
| `remote_worker.py:144-172` | `remote_worker.py:156-184` |
| `remote_worker.py:157-160` | `remote_worker.py:169-172` |
| `remote_worker.py:187-188` | `remote_worker.py:199-200` |
| `remote_worker.py:245-258` | `remote_worker.py:257-270` |
| `remote_worker.py:272, 418-420, 1086-1089` | `remote_worker.py:285, 432-434, 1112-1115` |
| `remote_worker.py:361-364, 381-394, 796-800, 63, 749-757, 424-431` | `remote_worker.py:375-378, 395-408, 822-826, 63, 775-783, 438-445` |
| `remote_worker.py:381-394` | `remote_worker.py:395-408` |
| `remote_worker.py:418-464` | `remote_worker.py:432-478` |
| `remote_worker.py:447, 596` | `remote_worker.py:461, 610` |
| `remote_worker.py:558-635` | `remote_worker.py:572-649` |
| `remote_worker.py:64, 316-341, 573-635, 74` | `remote_worker.py:64, 329-354, 587-649, 86` |
| `remote_worker.py:67, 144-172` | `remote_worker.py:67, 156-184` |
| `remote_worker.py:68, 1096-1111` | `remote_worker.py:68, 1122-1137` |
| `remote_worker.py:69, 187-191, 157-160, 180-185, 934-935, 970-971, 997-1000` | `remote_worker.py:69, 199-203, 169-172, 192-197, 960-961, 996-997, 1023-1026` |
| `remote_worker.py:740-745` | `remote_worker.py:766-771` |
| `remote_worker.py:773` | `remote_worker.py:799` |
| `remote_worker.py:785-794` | `remote_worker.py:811-820` |
| `remote_worker.py:796-800` | `remote_worker.py:822-826` |
| `remote_worker.py:833-847` | `remote_worker.py:859-873` |
| `remote_worker.py:872-890` | `remote_worker.py:898-916` |
| `remote_worker.py:892-899` | `remote_worker.py:918-925` |
| `remote_worker.py:896-899` | `remote_worker.py:922-925` |
| `remote_worker.py:934-935, 970-971, 997-998` | `remote_worker.py:960-961, 996-997, 1023-1024` |
| `remote_worker.py:997-1000, 934, 970` | `remote_worker.py:1023-1026, 960, 996` |
| `remote_workers.py:236, 256-266, 630-659, 994-1017, 562-573` | `remote_workers.py:236, 260-270, 639-668, 1003-1026, 571-582` |
| `remote_workers.py:279-281` | `remote_workers.py:283-285` |
| `remote_workers.py:377-434` | `remote_workers.py:384-443` |
| `remote_workers.py:390-398` | `remote_workers.py:397-405` |
| `remote_workers.py:399-411` | `remote_workers.py:406-418` |
| `remote_workers.py:404-410, 1006-1010` | `remote_workers.py:411-417, 1015-1019` |
| `remote_workers.py:404-411` | `remote_workers.py:411-418` |
| `remote_workers.py:426-434, 957` | `remote_workers.py:435-443, 966` |
| `remote_workers.py:560-561` | `remote_workers.py:569-570` |
| `remote_workers.py:567-573` | `remote_workers.py:576-582` |
| `remote_workers.py:588-597` | `remote_workers.py:597-606` |
| `remote_workers.py:630-659` | `remote_workers.py:639-668` |
| `remote_workers.py:635-637` | `remote_workers.py:644-646` |
| `remote_workers.py:696-750` | `remote_workers.py:705-759` |
| `remote_workers.py:696-750, 866-891` | `remote_workers.py:705-759, 875-900` |
| `remote_workers.py:752-855` | `remote_workers.py:761-864` |
| `remote_workers.py:769-771` | `remote_workers.py:778-780` |
| `remote_workers.py:78, 789` | `remote_workers.py:78, 798` |
| `remote_workers.py:866-891` | `remote_workers.py:875-900` |
| `remote_workers.py:916-942` | `remote_workers.py:925-951` |
| `remote_workers.py:952` | `remote_workers.py:961` |
| `remote_workers.py:952-962` | `remote_workers.py:961-971` |
| `remote_workers.py:965-990` | `remote_workers.py:974-999` |
| `remote_workers.py:965-999` | `remote_workers.py:974-1008` |
| `remote_workers.py:994-1017` | `remote_workers.py:1003-1026` |
| `runner.py:121, 334` | `runner.py:123, 428` |
| `runner.py:202, 361-368` | `runner.py:296, 455-462` |
| `runner.py:255-256` | `runner.py:349-350` |
| `runner.py:255-296` | `runner.py:349-390` |
| `runner.py:289-297, 231-241` | `runner.py:383-391, 325-335` |
| `runner.py:296` | `runner.py:390` |
| `runner.py:333` | `runner.py:427` |
| `runner.py:341, 358-376` | `runner.py:435, 452-470` |
| `runner.py:345-356, 379-385` | `runner.py:439-450, 473-479` |
| `runner.py:36-38, 345-356, 379-385` | `runner.py:38-40, 439-450, 473-479` |
| `runner.py:361-367` | `runner.py:455-461` |
| `runner.py:37, 334-376` | `runner.py:39, 428-470` |
| `runner.py:42-43, 244-249, 333-334, 341, 358, 369, 373-376` | `runner.py:44-45, 338-343, 427-428, 435, 452, 463, 467-470` |
| `server.py:246` | `server.py:250` |
| `server.py:252-256` | `server.py:256-260` |
| `server.py:263` | `server.py:267` |
| `server.py:3156-3162` | `server.py:3164-3170` |
| `server.py:3166, 3294` | `server.py:3174, 3302` |
| `server.py:3991, 4177, 4414, 4796` | `server.py:3999, 4185, 4438, 4820` |
| `server.py:4353-4355` | `server.py:4369-4371` |
| `server.py:4357-4366` | `server.py:4373-4382` |
| `server.py:4363` | `server.py:4379` |
| `server.py:441-451` | `server.py:445-455` |
| `server.py:5090` | `server.py:5114` |
| `server.py:5090-5130` | `server.py:5114-5154` |
| `server.py:518-532` | `server.py:522-536` |
| `server.py:5395-5417` | `server.py:5425-5462` |
| `server.py:5767-5770` | `server.py:5815-5818` |
| `server.py:5806-5810` | `server.py:5854-5858` |
| `server.py:5903` | `server.py:5956` |
| `server.py:614, 782-800` | `server.py:620, 790-808` |
| `server.py:6262-6277` | `server.py:6315-6330` |
| `server.py:782-800` | `server.py:790-808` |
| `server.py:833` | `server.py:841` |
| `server.py:897-906` | `server.py:905-914` |
| `cli.py:4645-4700`(손) | `cli.py:4833-4911` |
| `cli.py:4703-4715`(손) | `cli.py:4941-4949`(「등록도 capabilities 도 없다」 = `:4946`) |
| origin/dev `remote_workers.py:424-425`(손) | `remote_workers.py:432`(`remember_host(…, replace=True)`) |
| origin/dev `CHANGELOG.md:40`(손) | `CHANGELOG.md:53`(「only read requests and opens no database」) |
| dev `tests/test_doctor_cli.py:993-1017`(손) | 그대로 `tests/test_doctor_cli.py:993-1017`(`TOP_KEYS`) |
| `web/i18n.js:770-779, 1970-1979`(손, `git diff -U0` 덩어리 여덟 줄 더함) | `web/i18n.js:774-783, 1978-1987` |
| `web/i18n.js:1178, 2377`(손) | `web/i18n.js:1182, 2385` |
| `web/app.js:20` | 그대로(`ACTIONABLE`) |

### 3.1 서버 쪽 워커 프로토콜

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F1 | 워커 상태는 이름 단위 `workers.last_seen_at`(벽시계, 서버 수신 시각)로 판정. `now − last_seen_at > worker_timeout_seconds(60)` 이면 down | `remote_workers.py:283-285`, `store.py:340-348`, `config.py:131` | 레인 down 판정은 그대로 둔다. 유예 판정은 이것과 다른 시계(F31)로 |
| F2 | janitor 5초 루프가 `mark_lost_workers` 를 부르고, down 워커의 running·cancelling 잡을 **이름 단위로** 전부 `lost`(`worker_unreachable`, 인자 `seconds`) | `server.py:250`, `server.py:790-808`, `remote_workers.py:1003-1026`, `store.py:4710-4746` | 60초 분단 = 잡 소멸. 결정 253 과 맞지 않는다 → 잡마다 유예로 바꾼다(결정 266) |
| F3 | `mark_lost_for_worker` 는 닫는 트랜잭션 안에서 `last_seen_at` 을 다시 읽지 않는다 | `store.py:4724-4741` | 만료와 재연결 경쟁(RL4) → 판정과 닫기를 같은 락 아래에서(결정 266) |
| F4 | 서버 재시작 정리는 로컬 잡만 `lost`. 원격 잡은 그대로 두고 heartbeat 시각으로 닫힌다는 docstring | `store.py:3878-3909`(`worker_name IS NULL` 3898) | 재기동 직후 첫 틱이 꺼져 있던 동안 낡은 `last_seen_at` 으로 원격 잡을 닫을 수 있다(RL7) → 유예 기준에 서버 기동 시각 바닥(결정 266) |
| F5 | `register` = 새 프로세스로 보고 그 이름의 활성 잡을 **먼저** `lost`(`worker_restarted_without_job`). 버전 검사는 그보다 앞(주석: 따라가기 전 409 가 도는 잡을 닫으면 안 된다) | `remote_workers.py:384-443`(버전 390-398, 닫기 400-411) | 같은 토큰 두 프로세스가 서로의 잡을 닫는다, 재시작한 워커의 등록이 보고보다 먼저 닫는다 → 세션 단위(결정 265) · 재시작 순서(결정 268). 버전 먼저는 유지(RL13) |
| F6 | heartbeat 의 「잊힌 잡」: 서버가 아는 그 이름의 활성 잡 중 워커 목록에 없고 직전 claim(`_fresh_claims`)도 아니면 `lost`. 이름 단위 | `remote_workers.py:925-951`, `:261-266` | 세션 단위로 바꾼다. v0.5.1 에서 이미 한 번 보고된 경쟁(CHANGELOG.md:712-716) → RL9 는 흔한 경우 |
| F7 | 살아 있는 워커가 취소를 확인 안 하면 `kill_at + 2 × heartbeat` 뒤 `cancelled`(`cancel_unconfirmed`). 닿지 않는 워커는 `lost` 가 이긴다. 판정은 이름 단위 `worker_alive` | `remote_workers.py:974-999`, `:997-999`, `tests/test_worker_lost.py:137-148` | 펜싱된 세션의 취소 중 잡을 같은 이름의 살아 있는 새 세션 때문에 `cancelled` 로 닫으면 그룹 자리가 풀린다 → 현재 · 살아 있는 세션의 잡에만(결정 266, RL33) |
| F8 | 보고 라우트는 `_owned_active`: 남의 잡 403, 끝난 잡 409(`error` 문구 + `state`) | `remote_workers.py:597-606` | 409 를 뜻별로 가를 기계 판독 칸이 없다 → `code`(결정 269) |
| F9 | `ApiError(status, message, **extra)` 의 extra 가 응답 JSON 키가 된다. 이미 쓰는 기계 판독 키 이름은 `code`(예 `database_busy`) | `server.py:445-455`, `remote_workers.py:576-582`, `server.py:4379` | 409 사유 키는 `code`. 3판의 `reason` 은 큐의 `reason`(PLAN `PLAN.md:129`)과 겹쳐 버린다 |
| F10 | claim 은 같은 트랜잭션에서 `(worker_name, lane)` 에 활성 잡이 있으면 `LaneBusy`(→ 409) | `store.py:3307-3315`, `remote_workers.py:569-570` | 펜싱된 세션의 잡이 레인을 잡고 있는 동안 새 세션의 claim 은 409 폭주 대신 204(결정 261) |
| F11 | 그룹 배제는 `BUSY_STATES`(running · cancelling) 잡으로 센다 | `store.py:3316-3323`, `core/model.py:37` | 잡이 `lost` 가 되는 순간 그룹 자리가 풀린다(3판 A-H7). 자리를 지키려면 잡을 busy 로 두거나 표식 잡은 서버가 닫기 전에 워커가 멈춘다(결정 261 · 320) |
| F12 | 로그 라우트는 받은 대로 `log.txt` 에 덧붙인다. 오프셋 · 중복 제거 없음. 잘린 마지막 줄 `_log_partial` 은 메모리뿐. 비밀 가리기는 요청 하나 안에서(주석: 두 요청에 걸친 값은 못 지움) | `remote_workers.py:639-668`(가리기 635-637, 조각 642-646) | 재전송이 중복을 만든다 → 원본 바이트 오프셋(결정 271) |
| F13 | 로그 본문은 `application/octet-stream`, 상한 4 MiB(`MAX_WORKER_LOG_BODY`) | `server.py:6315-6330`, `remote_workers.py:75` | 오프셋 · 세션은 본문이 아니라 헤더로. 헤더 관례 `X-RCM-*` 가 이미 있다(`X-RCM-Tree` `server.py:5956`, `X-RCM-Next-Offset` `client.py:878`) |
| F14 | 비밀 가리기 값은 서버의 비밀 폴더에서(`mask_for_preset`), `kind = "value"` 이고 8자 이상. 원격 워커의 `RunSpec.mask` 는 없음(서버가 가린다) | `server.py:3164-3170`, `release_secrets.py:183-198, 383-389`, `remote_worker.py:859-873` | 오프셋은 가리기 **전** 바이트로 센다(워커가 아는 단위). 파일 길이는 따로 적는다 |
| F15 | finish 는 이미 끝난 잡이면 409, 첫 보고가 이긴다. 워커가 보낸 `finished_at` 은 이상하면 수신 시각으로 물러서지만 미래 · `started_at` 이전은 막지 않는다 | `remote_workers.py:761-864`, `:769-771`, `tests/test_worker_lost.py:128-131` | 멱등은 유지. 시각은 `[started_at, 수신 시각]` 으로 자른다(결정 277, RL27) |
| F16 | finish 의 `summary_code` 는 `PREFLIGHT_CODES` + `failed` + `exit_code null` 일 때만. `lost` 보고는 워커 문장(`given`) 아니면 `worker_stopped_while_running` | `remote_workers.py:121-165`, `:776-778`, `core/outcome.py:238` | `lost` 용 코드 허용 목록이 필요(3판 M3) → `LOST_CODES`(결정 278) |
| F17 | 산출물 업로드는 `_owned_active`(끝난 잡 409), 같은 해시 재업로드는 200 멱등. finish 의 처분이 `ready` 인데 올라온 행이 없으면 워커 말을 그대로 적는다(파일이 없으면 내려받기 503 · 재시작 화해가 `unavailable`) | `remote_workers.py:705-759`(멱등 717-721), `:866-891` | 「번들이 서버에 있을 때만 ready」(RL22) → 처분 규칙(결정 275) |
| F18 | `/worker/spec` 은 워커 토큰이면 등록 전에도 읽힌다(부작용 없음) | `server.py:5854-5858`, `server.py:4369-4371` | 새 워커가 등록 **전에** 프로토콜을 확인하는 자리(결정 270, RL25) |
| F19 | `/worker/capabilities` 는 등록 확인(`_registered`) 뒤 보고를 받는다 | `server.py:4373-4382` | 세션을 싣고 펜싱 검사를 받는다(RL11) |
| F20 | `[server]` 절은 reload 대상이 아니다(`RELOADABLE_NOT`). 프리셋은 reload 로 바뀌고 claim 마다 새로 찾는다 | `server.py:5114-5154` | `disconnect_grace_seconds` 는 재시작으로만 바뀐다. 그래도 재시작을 넘는 잡이 있으니 유예 · 표식은 claim 때 잡 행에 얼린다(결정 257) |
| F21 | `forget_workers` 는 활성 잡이 있는 워커는 안 잊는다. 7일(`WORKER_FORGET_DAYS`) | `store.py:4659-4692`, `janitor.py:49, 759` | 세션 행은 워커 행과 함께만 지운다(결정 265) |
| F22 | 헬스는 down 레인이 있으면 `ok=false`(503) | `server.py:5425-5462` | 유예 동안 레인은 `down` 한 값으로 고정 — 깜빡이지 않는다(결정 276, RL28). 바꾸지 않는다 |

### 3.2 설정 · 프리셋 · 출력

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F23 | 모르는 프리셋 키는 기동 실패. `local_only` 는 bool 검증 | `config.py:646-675`, `:819`, `:930-937` | `irreversible` 도 같은 꼴(bool). 앱 프로젝트가 키를 다는 시점은 §2 |
| F24 | `local_only` 프리셋은 원격 워커가 받지 못한다(`_claimable` 의 `excluded`) | `store.py:3419-3423`, CHANGELOG `[Unreleased]` #266 | `irreversible` 은 원격에서 돌 수 있는 프리셋에만 뜻이 있다 |
| F25 | 이름 `grace_seconds` 는 이미 SIGTERM→SIGKILL 간격(서버 `[server] grace_seconds` 10, 워커 `grace_seconds`) | `config.py:103, 521, 1378-1386, 1960-1961`, `runner.py:123, 428` | 새 키는 `disconnect_grace_seconds`(3판 A-H2) |
| F26 | 「irreversible」 이라는 낱말은 이미 릴리스 쪽에서 「되돌릴 수 없는 입력 값」(`RELEASE_IRREVERSIBLE_MODE = {"upload": "upload", "review": "submit", "golive": "release"}`)과 웹 문구 `driver.item.irreversible` 로 쓰인다. 프리셋 키 `irreversible` 은 없다(grep 0) | `cli.py:4074, 4130-4135`, `web/i18n.js:1182, 2385` | 키 이름은 위임 결정 257 대로. 뜻이 「프리셋 전체」라 입력이 rehearsal 이어도 자기 멈춤이 걸린다(안전한 쪽) — §9 |
| F27 | `worker_timeout_seconds ≥ 10`, `heartbeat < timeout` 검증 | `config.py:1429-1434` | 새 키 검증도 같은 자리 · 같은 꼴(결정 263) |
| F28 | 요약 코드 표 `CODES` 와 웹 번역의 짝을 시험이 잠근다(로케일마다 정의된 `outcome.*`) | `core/outcome.py:147, 207-212`, `tests/test_outcome.py:543-569`, `web/i18n.js:774-783, 1978-1987` | 새 코드를 더하는 PR 이 번역도 더한다(PR1) |
| F29 | 행동 가능한 사유 목록 `ACTIONABLE_REASONS` 와 웹 `ACTIONABLE` 이 같은 목록이고 순서를 시험이 잠근다(잠근 시험 전부는 F58) | `core/model.py:165-176`, `src/remote_ci_monitor/web/app.js:20`, `tests/test_action_marker_m5s.py:139-141` | 새 사유를 더하면 두 목록과 F58 의 시험을 같이 고친다(PR3) |
| F30 | 도는 잡의 사유는 `_busy_reason`(cancelling → materializing → needs_action → stuck → overdue → quiet → running). 워커 생존을 보지 않는다. `compute_queue` 는 `workers`(down 포함)를 받고 down 레인은 살아 있는 레인에서 뺀다 | `core/queue.py:336-357`, `:423`, `:464`, `:634-635` | 유예 중 잡의 사유와 ETA null 은 `compute_queue` 안에서 그 잡의 워커가 down 인지로(결정 276) |

### 3.3 워커 쪽

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F31 | 서버에는 단조 시계 주입이 없다(`now_fn` 만). 주입형 `mono_fn` 은 스킬 템플릿에 선례가 있다 | `server.py:522-536`, `skills/rcm-ci-connect/templates/rcm_ci_watch.py:958, 989` | `App` 에 `mono_fn` 을 더한다(결정 266) |
| F32 | 워커 로그는 메모리 버퍼 4 MiB(`LOG_KEEP_BYTES`), 넘치면 오래된 쪽을 표시 없이 버린다. 503 아닌 오류면 조각을 버린다. 409 면 `closed_by_server` → `should_cancel` → 프로세스를 죽인다 | `remote_worker.py:67, 156-184`(409 157-160, 버림 161-163, 자름 166-167), `:187-188` | 디스크 스풀(결정 271). 409 를 뜻별로(결정 269). 일반 프리셋은 서버가 닫아도 죽이지 않는다(결정 256) |
| F33 | 보고는 1·2·4초 재시도 뒤 포기. finish 만 미뤄 두었다가 60초(`DRAIN_REPORT_SECONDS`) 뒤 버린다. phase 는 포기하면 끝 | `remote_worker.py:64, 329-354, 587-649, 86`, `:129-132` | 순서 있는 보고 큐, 잡이 도는 동안 횟수 상한 없음, 끝난 뒤 24시간(결정 264 · 272) |
| F34 | 러너는 관찰자에게 **줄 끝(`\n`)으로 끝나는 묶음만** 준다. 개행 없는 줄은 `MAX_LINE_BYTES`(64 KiB) 넘으면 잘라 `\n` 을 붙여 내보내고, EOF 의 남은 조각에도 `\n` 을 붙인다 | `runner.py:38-40, 439-450, 473-479` | 3판의 `log.pending`(줄 끝 없는 꼬리)은 필요 없다. 조각을 줄 경계에서 자르면 비밀 값이 두 요청에 걸치지 않는다(RL17 · RL18) |
| F35 | (실측) 위 F34 를 실제로 돌려 확인: 개행 없이 100,000 바이트 + `\r`×10 + 꼬리 15 바이트를 쓰는 프로세스 → 관찰자 묶음 1개 100,026 바이트, `\n` 으로 끝남, 워커 로컬 `log.txt` 와 바이트 같음. 한 줄의 최대는 `MAX_LINE_BYTES + READ_CHUNK`(128 KiB) | 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS)에서 `runner.run_job` 에 기록용 관찰자를 붙여 실행(스크래치 스크립트, 레포 밖) | 워커 조각 상한 256 KiB(`LOG_BATCH_BYTES`, `remote_worker.py:66`) 안에 한 줄이 늘 들어간다 |
| F36 | 워커 로컬 `jobs/<id>/log.txt` 는 러너가 쓴 줄 + git_ref 준비의 `[git] …` 줄(서버로 안 감). 거꾸로 준비 실패의 `[rcm] <log>` 줄은 관찰자로만 간다 | `remote_worker.py:898-916`, `:962-963` | 로컬 `log.txt` ≠ 서버로 가는 스트림 → 스트림만 담는 별도 스풀 파일(결정 271) |
| F37 | 스냅샷 내려받기의 연결 실패(status 0)는 `snapshot_download_failed` → `failed`(종료 1) | `remote_worker.py:918-925`, `client.py:145-186`(`.part` 는 실패 때 지움) | 위임 결정 259: 연결 실패는 `lost`(`snapshot_unreachable`), 확정 결함은 `failed` 그대로 |
| F38 | heartbeat 실패는 경고만, 잡은 계속. 레인 claim 이 409 `not registered` 면 그 자리에서 재등록 → 오늘은 재등록이 그 이름의 도는 잡을 닫는다 | `remote_worker.py:432-478`, `:771-795` | 같은 세션 id 재등록은 멱등(결정 260) |
| F39 | 워커 프로세스 종료 코드: 0 평범한 멈춤, 2 등록이 막힘(감독자가 다시 띄우게), 1 exec 실패. 서비스 파일은 launchd `KeepAlive.SuccessfulExit=false` · `ThrottleInterval 10`, systemd `Restart=on-failure` · `RestartSec=5` | `remote_worker.py:811-820`, `cli.py:4702-4705, 3564-3565, 3592-3593` | 펜싱 0(되살리지 않음), 프로토콜 불일치 3(되살림) — 결정 258 |
| F40 | 옛 워커(이 명세 이전 코드)의 반응: 등록 409 + 같은 `server_version` → 종료 2; claim 409(`not registered` 아닌 것) → 로그 한 줄 + 2~4초 뒤 다시(`_backoff(RETRY_WAIT_SECONDS)`); heartbeat 409 → 3·12·60번째에만 로그 | `remote_worker.py:375-378, 395-408, 822-826, 63, 775-783, 438-445` | 옛 워커를 고칠 수 없다. 서버가 409 `protocol_required` 를 주면 옛 워커는 잡을 집지 못하고, 다시 시작하면 종료 2 → 감독자가 5~10초 간격으로 되살린다. RL24 의 「5초 이상」은 이 사실로 고쳐 적는다(§12) |
| F41 | 시작 청소는 `workspaces/` · `jobs/` · `artifacts/` 의 7일(`WORKSPACE_KEEP_DAYS`) 지난 항목을 mtime 으로 지운다 | `remote_worker.py:68, 1122-1137` | 세션 폴더도 같은 7일. 단 잠금을 쥔(살아 있는) 세션 폴더는 건너뛴다 |
| F42 | M5q 드레인: 모든 레인이 잡 없이 기다리면 같은 PID 로 설치본을 exec. 못 보고한 잡은 60초까지 다시 보고하고 그래도 안 되면 「restarting anyway」 | `remote_worker.py:572-649`, `cli.py:4706-4740` | 보고 큐가 디스크에 있으니 exec 뒤 새 이미지가 이어 보낸다(결정 268, RL12 b) |
| F43 | (실측) `fcntl.flock` 을 쥔 파일은 Python 이 기본으로 상속 안 함(PEP 446)이라 `os.execv` 뒤 새 이미지에서 같은 파일을 바로 잠글 수 있었다 | 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS)에서 잠금 → execv → 자식이 `LOCK_EX \| LOCK_NB` 성공 | 세션 폴더 잠금으로 「그 세션 프로세스가 살아 있나」를 판정할 수 있다. exec 한 새 이미지는 옛 세션을 「죽음」으로 본다(맞다) |
| F44 | (실측) `ps -o lstart= -p <pid>` 가 macOS 에서 `Thu Nov 26 01:49:14 2026` 꼴 한 줄. 3판: 이 출력은 TZ · 로캘에 따라 바뀐다(F71) — 그대로 비교하면 「pid 재사용 → 확인됨」으로 잘못 간다(V1-7). Linux 는 `—`(측정 안 함. 측정 수단: ubuntu 머신에서 같은 명령, rcm-ci 는 맥미니뿐) | 측정: 이 셸에서 실행 | 고아 프로세스 그룹의 신원 확인(pid 재사용 방지, RL15 · RL31). 3판: `LC_ALL=C TZ=UTC` 로 고정해 읽고 형식이 다르면 「모름」(결정 327 의 3판 문장) |
| F45 | runner 는 `start_new_session=True` → 잡 프로세스 그룹 id = 자식 pid. 신호는 `os.killpg` | `runner.py:383-391, 325-335` | 고아 정리는 pgid 로. 관찰자 규약 `RunObserver` 에 pid 를 알리는 갈래가 없다 → 더한다(결정 268) |
| F46 | `rcm worker --check` 는 health · whoami · spec 만 부르고 등록 · capabilities 를 안 한다(주석 「등록도 capabilities 도 없다」) | `cli.py:4833-4911`, `cli.py:4941-4949` | 세션을 만들지 않는다 → 도는 서비스 워커를 펜싱하지 않는다(RL12 c). `rcm doctor` 는 dev 에 머지됐고(#268, F54) 「서버에 읽기 요청만 보내고 DB 를 열지 않는다」(`CHANGELOG.md:53`) — 같은 규칙, RL12 c 에 넣는다 |

### 3.4 시험 · 문서에 걸린 것

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F47 | 오늘 계약을 잠근 시험: 60초 뒤 `lost`(`test_worker_past_timeout_loses_its_running_job_and_shows_down`), 돌아온 heartbeat 가 lost 잡을 안 살림, 닿지 않는 워커의 cancelling 은 lost, kill -9 뒤 timeout 에 lost · 새 워커가 이어 받음 | `tests/test_worker_lost.py:58-148`, `tests/test_e2e_worker.py:775-805` | 결정 253 이 바꾼 계약이라 기대값을 유예 기준으로 고쳐 쓴다(약화 아님, §7.3) |
| F48 | 서버 재시작 e2e(원격 잡이 running 으로 남고 finish 로 succeeded), SIGTERM e2e(`worker stopped`, 종료 0) | `tests/test_e2e_worker.py:810-829, 857-880` | 유지 — 회귀 잠금 |
| F49 | 문서가 「60초 뒤 lost」 를 말한다 | `docs/configuration.md:1213, 1416`, `docs/operating.md:1004`, `templates/server.toml:42` = `examples/server.toml:42`(바이트 같음을 `tests/test_examples.py` 가 잠금) | PR1 이 고친다 |
| F50 | `DB_VERSION = 25`, 마이그레이션 전 백업(결정 74), 더 새 DB 는 거절 | `store.py:88, 480, 661-677, 1208-1248, 778-785` | v26 은 PR1 하나가 올린다. M5t-c 는 v27 |
| F51 | 높은 엔트로피 문자열 리터럴은 gitleaks `secrets` 잡을 빨갛게 하고 나중에 고쳐도 커밋 이력에 남는다 | `AGENTS.md:137-139` | 시험의 세션 id 는 실행 중 만든다(리터럴 32-hex 금지) — §8 |

### 3.5 2판에서 더한 사실

1라운드 지적을 확인하면서 새로 읽은 사실이다.

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F52 | M5t-c 명세 PR #269 의 결정 301~303: 301 짝짓기 요청은 코드 대신 nonce + HMAC, 302 복제된 worker.toml 은 로컬 침해로 남는 위험, **303 확실한 신원 불일치(MAC 틀림 · 서버가 짝지은 적 없다고 답함)는 종료 0 + 「다시 짝지으라」(서비스가 되살리지 않음), 연결 실패 · 5xx · 503 은 신원 판단이 아니라 프로세스 안에서 물러서며 다시 시도(종료 안 함), 프로토콜 불일치는 M5t-b 대로 3(되살림), 부모 명세 M8 은 「펜싱 · 확실한 신원 거절」로 좁힌다**. 그 명세의 `pair_proof` 검사 자리는 「버전 409 다음, 풀 검사 · `mark_lost_for_worker` 앞」 | PR #269 head `fb73ed67b1ad1ddb9af805d0d388008867cd7c9c` 의 `docs/m5tc-workplan.md:216` · 결정 287(`:523`) · 결정 295(`:531`), 결정 301~303 은 PR #269 의 위임 결정 3차 표(레포 밖 handoff, 메인 세션 기록) | 1판 §4.7 · §9 의 「M5t-c 도 종료 3(서버 신원 미확인)」은 틀린 말이 됐다 → 고침. 3판: M5t-c 결정 317(§1.2)이 303 을 더 좁혔다 — 틀린 MAC 의 hello · 421 · 인증되지 않은 응답은 종료 0 이 아니라 토큰 없이 계속 다시 시도(10분마다 경고, FAIL/모름 상태). 종료 0 은 펜싱(이 명세)과 M5t-c 의 인증된 두 경우뿐 → §4.7 · §4.11 · §9 · 결정 258 의 「확실한 신원 거절」 문구를 317 로 고침. register 순서에서 `pair_proof` 는 버전(2) · 프로토콜(3) 검사 뒤, 펜싱된 세션 검사 · 부작용(세션 넣기 · 펜싱 · 능력 지우기 · 호스트 사실 교체 · 잡 닫기) 앞(§4.2, V2-14) |
| F53 | 1판의 자기 멈춤 판정 자리인 heartbeat 루프는 `stopping.wait(H)` 뒤 `heartbeat_once` 를 부른다. heartbeat 요청 시한은 `WorkerClient` 기본 15초. 성공한 heartbeat 은 그 자리에서 `_retry_unreported()` 를 동기로 부르고 그것은 보고마다 1·2·4초 재시도 + 요청 시한을 쓴다 | `remote_worker.py:766-771`, `client.py:83, 137-142`, `remote_worker.py:461, 610`, `:316-341` | 끊긴 동안 판정 한 바퀴가 H + 15초, 서버가 돌아온 첫 회차는 못 한 보고 재시도 뒤로 몇 분까지 밀린다 → 1판 m(H 마다 판정 가정)이 깨진다(V1-1 · V2-1). 판정을 heartbeat 에서 떼어 감시 스레드로(결정 320) |
| F54 | `origin/dev` 는 `c8590dd`(M5t-a PR2 #268 `rcm doctor` · PR3 #271 `lanes = 0` 분배 전용, 그 앞 #266 · #267). `DB_VERSION` 25 그대로(`store.py:88`). register 의 호스트 사실 교체 `remote_workers.py:432`. M5t-a 의 남은 PR4~PR7(`cli.py` setup · guard, wait 사람용 줄 · `ran_on`, 문서, 실기, `docs/m5t-workplan.md:690-697`) | `git log --first-parent 96a8bd2..c8590dd`(#271 하나), `git diff --stat fdf196d c8590dd -- src` | 구현 PR1 의 시작 조건 「M5t-a PR1~PR7 전체 머지」 그대로 |
| F55 | 요청 시한: heartbeat · phase · finish · capabilities 15초(`_post` 기본), claim 은 `15 + wait_seconds + 5`(wait 기본 20, 상한 60), log `max(15, 60)` = 60, 스냅샷 내려받기 ≥ 60, 산출물 PUT ≥ 300 | `client.py:83, 89-93, 129-134, 191-200, 159, 230-244`, `config.py:133, 1457-1458` | 응답은 보낸 뒤 최대 80초(claim) · 300초(산출물)에 올 수 있다 → 워커의 `ok_at` 은 응답 받은 시각이 아니라 **그 요청을 보낸 시각**(결정 320). m 의 요청 시한 항은 heartbeat 의 15초 |
| F56 | (실측) 러너의 멈춤 시간. 러너는 `POLL_SECONDS` 1 마다 `should_cancel` 을 보고 SIGTERM, SIGTERM 뒤 `grace_seconds` 가 지난 첫 점검에 SIGKILL 한다(점검은 묶음을 1초까지 기다린 뒤 직전 점검에서 1초가 지났을 때) — 이론 상한은 플래그→SIGTERM ≤ 2초, SIGTERM→SIGKILL ≤ grace + 2초. 측정: SIGTERM 을 받는 `sh -c 'sleep 60'` 은 플래그 뒤 0.02~0.03초에 죽음, SIGTERM 을 무시하는 조용한 자식(grace 3) 3.04~3.06초, SIGTERM 을 무시하고 50ms 마다 찍는 자식(grace 3) 3.25~3.34초 | `runner.py:39, 428-470`, 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS)에서 `run_job` 에 「3초 뒤 should_cancel 참」 관찰자를 붙여 각 3회(스크래치 스크립트, 레포 밖) | 「플래그 → 그룹 소멸」 ≤ grace + 4초(이론), 측정 ≤ grace + 0.34초. 여유 `SELF_STOP_SLACK_SECONDS` 10 = 감시 틱 1 + 러너 점검 4 + 남김 5(결정 320) |
| F57 | 오늘 있는 잠금은 `_remote_lock`(`_log_partial` · 표본) · `_fresh_claims_lock` 둘. 로그 라우트는 쓰기와 마커 커밋을 묶는 잠금 없이 `log.txt` 에 덧붙인다(덧붙임 `:640-641`, `_remote_lock` 은 `_log_partial` 만 `:642-646`, 마커 트랜잭션 `:652`). `_session_lock` 은 없다(grep 0). `mark_lost_workers` 도 잠금 없이 판정 · 닫기. 잠금 주인은 믹스인 `RemoteWorkersMixin`(`_remote_init`), `App` 이 기동 때 부르고 janitor 는 `App._janitor_loop`. DB 는 `busy_timeout=5000`, 바쁨은 503 `database_busy` | `remote_workers.py:236, 260-270, 639-668, 1003-1026, 571-582`, `server.py:620, 790-808`, `store.py:1191` | 같은 잡의 두 요청(서버에서 아직 도는 시한 초과 요청 + 재시도)이 같은 `log_offset` 을 읽고 둘 다 붙인다 · 커밋 실패 뒤 재시도가 같은 바이트를 또 붙인다(V2-2) → 잡별 로그 잠금(결정 323). 1판이 「같은 락(`_session_lock`)」이라 쓴 것은 새 잠금 `_heard_lock` 으로 이름 · 범위 · 주인을 정한다(결정 328, V2-5) |
| F58 | 계약을 잠근 시험(1판 F29 · PR3 owns 가 빠뜨린 것 포함): `ACTIONABLE_REASONS` 튜플 `tests/test_queue_m5n.py:135` · `tests/test_render_m5n.py:199` · `tests/test_action_marker_m5s.py:139-141`, 웹 = 모델 `tests/test_web_schedule_m5s.py:43`, node `tests/web/stall.test.js:192-194` · `tests/web/m5n.test.js:38-41`. 최근 행 키 정확한 집합 `tests/test_status_schema.py:91`(대조 `:303`), 더한 키 허용 목록 `tests/test_status_outcome.py:162`(대조 `:366-368`). register 응답 정확한 dict `tests/test_worker_api.py:542-549` · `tests/test_worker_client.py:183-190`. heartbeat 응답 정확한 dict `tests/test_worker_api.py:1204`(부분 집합 `tests/test_server_m5q_worker.py:90`). claim 최상위 키 `tests/test_worker_api.py:667` · `tests/test_worker_client.py:219-220`(새 값은 `job` 안이라 안 바뀜). `[server]` 워커 키 `tests/test_config.py:905`. 400 응답은 `{"error"}` 하나(`tests/test_worker_api.py:618`) | 각 줄 | §7.3 과 §5 shared 에 전부 넣는다(V1-7). `log_complete` 를 최근 행에 더하는 PR2 가 F58 의 두 최근 행 잠금을, `closed_jobs` · `worker_protocol` 을 더하는 PR1 이 heartbeat · register 잠금을 고친다 |
| F59 | 프로세스 신원: Linux 는 `/proc/<pid>/stat` 22번째 칸 `starttime`(부팅 뒤 clock tick, proc(5)). 이 머신에서 재지 않았다(`—`, 측정 수단: Linux 머신에서 같은 읽기 — rcm-ci 는 맥미니뿐). macOS 는 `ps -o lstart=`(F44 실측). 프로세스 그룹 id 는 그룹이 살아 있는 동안 다시 쓰이지 않지만(POSIX), 리더(pid = pgid)가 죽고 멤버만 살면 `ps -p <pgid>` · `/proc/<pgid>` 는 아무것도 못 찾고 `killpg(pgid, 0)` 은 성공한다 | proc(5), F44, F45 | 리더가 살아 있고 시작 시각이 같으면 「같은 그룹」, 리더가 없고 그룹이 살면 「모름」(V2-7) → 재확인 · 경고(결정 327) |
| F60 | events 의 kind 는 오늘 `state` · `marker` 둘이고 읽는 곳은 전이 목록 · 마커뿐이다. 임의의 kind 를 사람에게 보이는 길이 없다 | `store.py:132-133, 1324, 3968-4009` | D-g 의 「경고를 사건으로」는 `lost` 요약 코드 `orphan_unverified` 로 낸다 — 요약 코드는 `rcm wait` · 웹 · `[[notify]]` 가 번역해 보인다(F28). 새 events kind 는 만들지 않는다(결정 327) |
| F61 | heartbeat 응답은 오늘 `{cancel, paused, timeout_seconds, generation, server_version}`. `cancel` 은 그 이름의 활성 잡 중 CANCELLING 만이라 취소 중이던 잡이 `lost` 로 닫히면 목록에서 빠진다. 잡 행의 `cancel_requested_at` 은 닫힌 뒤에도 남는다 | `remote_workers.py:961-971`, `store.py:203, 1329-1334` | 다시 붙은 워커가 그 잡을 계속 돌린다(V1-4) → heartbeat 응답 `closed_jobs` 에 `had_cancel` 을 싣는다(결정 321 · 322) |
| F62 | 워커는 `heartbeat_seconds` · `worker_timeout_seconds` 를 register 응답에서, `timeout_seconds` 를 heartbeat 응답에서 받는다 | `remote_workers.py:435-443, 966` | 워커가 하한 L 을 계산할 수 있다(결정 320) |
| F63 | 서버의 묶음 파일은 `artifacts_dir(job)/bundle.tar`. 재시작 화해도 같은 파일 존재를 본다. finish 처분 `ready` 는 오늘 행이 없으면 워커 말을 그대로 적는다 | `server.py:3174, 3302`, `store.py:2107`, `remote_workers.py:875-900` | `ready` 는 행 · 해시 · 파일 셋이 맞을 때만(V1-12, 결정 275 고침) |
| F64 | `GET /api/whoami` 는 워커 토큰에도 `{name, admin, kind}` 를 준다(부작용 없음) | `server.py:5815-5818`, F46 | 등록 전에 토큰 이름을 알아 세션 폴더 주인을 적는다(결정 325) |

### 3.6 3판에서 더한 사실

2라운드 지적과 4차 결정을 확인하면서 읽은 사실과 이 Mac 에서 잰 값이다.

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F65 | 러너는 `should_stop` · `should_cancel` 을 Popen 전에 **`if spec.requires:` 안에서만** 본다. 자재화(git_ref fetch · 스냅샷 내려받기)는 그 앞, 그 확인과 Popen 사이에는 `observer.phase(PHASE_EXECUTING)` 가 있고 원격 관찰자의 phase 는 재시도가 붙은 막는 HTTP 보고다 | `runner.py:349-350`(자재화), `:266-275`(requires 안의 확인), `:286-287`(phase), `:289-297`(Popen), `remote_worker.py:141-144` | 감시가 자재화 중에 발동하면(requires 없는 프리셋) 멈춤 결정 뒤에 명령이 시작된다 → 서버가 `heard + G` 에 그룹을 풀면 두 번 돈다(V2-1 HIGH · V1-1). → Popen 직전 확인을 requires 와 상관없이, phase 뒤 · Popen 바로 앞(사이에 I/O 없음)에(결정 333) |
| F66 | 러너의 `lost` 는 `should_stop` 으로만 선다. 워커는 `lost` 를 `STOP_SUMMARY`(「worker stopped」, `summary_code` 없음)로 보낸다 — 실행 뒤 · requires 실패 뒤 · 준비 실패 뒤 세 곳. `should_cancel` 은 `cancel_requested or closed_by_server` 이고 로그 409 하나가 `closed_by_server` 를 세운다 → `cancelled` | `runner.py:296, 455-462`, `remote_worker.py:69, 199-203, 169-172, 192-197, 960-961, 996-997, 1023-1026` | 자기 멈춤을 `should_cancel` 로 걸면 종료 2(`cancelled`)로 끝나 결정 253 의 3 과 어긋나고, 오늘의 `closed_by_server` 는 W4(일반 `lost` 잡은 계속)와 어긋난다(V1-2 · V2-2) → 자기 멈춤 = `should_stop` 길 + `worker_self_stopped`, `closed_by_server` 를 `should_cancel` 에서 뗌(결정 334) |
| F67 | 러너의 1초 점검 박자와 SIGTERM→SIGKILL 타이머가 벽시계 `now_fn`(`_utcnow`)이다. 잡 시한도 같은 시계 | `runner.py:44-45, 338-343, 427-428, 435, 452, 463, 467-470` | 시계가 뒤로 가면 SIGKILL 이 늦어 「플래그 → 그룹 소멸 ≤ grace + 4」(F56)가 깨진다(V2-3) → 박자와 grace 타이머는 단조 시계 주입 `mono_fn`, 잡 시한은 오늘대로 벽시계(결정 333) |
| F68 | 워커 요청 시한 15초는 `urlopen(timeout=)` 이라 **소켓 연산마다**의 시한이지 요청 전체 기한이 아니다(연결 · 읽기 각각, 조금씩 흘리는 응답은 끝이 없다). heartbeat 은 `stopping.wait(H)` 뒤 `heartbeat_once` — 다음 heartbeat 은 앞 heartbeat 이 끝나고 H 뒤에 간다 | `client.py:83, 89-93, 527-531, 566`, `remote_worker.py:766-771` | 연결된 워커의 `ok_at` 나이는 다음 2xx 직전에 `d_prev + H + d_cur`, 2xx 하나를 잃으면 `+ H + d` — 약 `2H + 3·T_req`. 2판의 L = max(T, 2H + 15) 로는 §7.5 e2e(T 10 · H 1 · G 52 · 문턱 17)에서 연결된 잡이 멈춘다(V1-3 · V2-11) → heartbeat 에 요청 전체 기한, L = max(T, 2H + 3·T_req + 5)(결정 335) |
| F69 | 오늘 register 는 그 이름의 활성 잡을 **모두**(세션 개념 없음 — 레거시 포함) `worker_restarted_without_job` 으로 닫는다. 2판은 `previous_sessions` 의 잡만 닫아 v26 이전에 claim 된(세션 NULL) busy 잡이 프로세스 없이 `running` 으로 남고 새 세션의 claim 이 `LaneBusy` 409 를 받는다. 2판 `_heard_at` 은 세션 id 키라 레거시 잡의 들음이 정의되지 않았다 | `remote_workers.py:406-418`, `store.py:3307-3315`, WP2 §4.2 | 첫 등록이 그 이름의 세션 NULL busy 잡을 오늘처럼 닫는다, 레거시 들음은 이름 키(V1-5 · V2-14, 결정 336) |
| F70 | PLAN.md 의 이유 목록과 행동 가능 순서(`needs_action → worker_down → no_capable_machine → …`)가 `:129`(잡 모델)와 `:505`(웹 「Needs a look」) 두 곳에 있다 | `PLAN.md:129, 505`, `core/model.py:165-176`, `ACTIONABLE` | PR0 의 PLAN.md 편집에 두 자리를 더한다(V1-6) |
| F71 | (실측) `ps -o lstart=` 출력은 TZ · 로캘을 따른다: 기본 셸 `Thu Nov 26 03:32:46 2026`, `LC_ALL=C TZ=UTC` → `Wed Nov 25 18:32:46 2026`, `TZ=Asia/Seoul LC_ALL=ko_KR.UTF-8` → `2026년 11월 26일 목요일 03시 32분 46초`. 없는 pid 는 빈 출력 · 종료 1 | 측정: 이 Mac(macOS 27.2) 셸에서 같은 프로세스에 세 번 | 서비스(launchd 환경)와 손으로 띄운 셸이 다른 문자열을 적고 읽을 수 있다(RL10 이 바로 그 경우) → `LC_ALL=C TZ=UTC` 고정, 정규식 `^(Mon\|Tue\|Wed\|Thu\|Fri\|Sat\|Sun) (Jan\|…\|Dec) [ 0-9][0-9] [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}$`(앞뒤 공백 제거 뒤)에 안 맞으면 「모름」(V1-7) |
| F72 | `tests/test_outcome.py` 는 코드 표를 세 군데서 전수로 잠근다: `PLAUSIBLE`(`:48-85`)이 `CODES` 와 같아야 하고(`:220-222`), `PINNED`(`:88-214`)가 `CODES` 전부를 덮어야 하며(`:225-231`), 코드마다 도는 시험(`:233-` · `:243-`), 필수 인자 표(`:511`), 웹 번역(`:553`) | 각 줄 | 새 요약 코드(`worker_self_stopped` · `worker_replaced` · `snapshot_unreachable` · `orphan_unverified`)를 더하는 PR1 이 `PLAUSIBLE` · `PINNED` · 번역을 같이 고친다(V1-13) |
| F73 | `concurrency_group` 은 아무 문자열(형식 검사 없음), 제출 때 잡 행에 박힌다. `concurrency_scope` 기본 `pool`. claim 의 그룹 배제는 같은 그룹의 `BUSY_STATES` 잡을 범위 규칙으로 센다 | `config.py:853-855, 910-914`, `server.py:3999, 4185, 4438, 4820`, `store.py:3316-3323, 3403-3407` | 그룹이 없는 표식 프리셋은 홀드할 그룹이 없다 → 합성 그룹. 홀드는 배제 SQL 에 「열린 홀드」 조건을 더해 같은 범위 규칙으로 막는다(결정 330) |
| F74 | 「hold」 는 이미 셋이 쓴다: 레인 `held`(`WORKER_HELD`, 부하 게이트 M5f), 서버의 `hold_of` · `_hold`(레인 보류 사유), ETA 모의의 `_GroupHold`, 보관함 · 산출물의 `_holds`. `rcm release …` 는 스토어 버전 명령이다. `release-hold` · `release_hold` · `irreversible_holds` · `irreversible_hold` · `no_hold` 는 grep 0 | `core/model.py:107`, `server.py:905-914`, `core/queue.py:401-516`, `store.py:1169`, `shelf_server.py:104`, `cli.py:5621-5624` | DB 표는 `irreversible_holds`(레인 hold · ETA `_GroupHold` 와 이름이 갈림), CLI 는 오너 문구 그대로 `rcm release-hold`(`rcm release` 의 하위 명령이 아니라 따로 선 명령. 도움말이 「not a store release」 를 말함), 라우트 `POST /jobs/{id}/release-hold`(결정 330) |
| F75 | 대기 행 JSON 에 `blocked_by {job_id, group, remaining_seconds}` 와 `action {code, note, at}` 가 이미 있다. `action.code` 는 `[a-z0-9_-]{1,32}`. `needs_action` 은 행동 가능 목록의 맨 앞. `rcm check` 는 `/api/health` 를 읽고 경고 줄(ok = None)을 낼 수 있다. `/api/status` 는 기본 `read_auth = none` | `core/status.py:185-189, 257-264, 298`, `core/model.py:139-141, 165-176, 510-519`, `cli.py:4292-4346`, `server.py:841` | 홀드에 막힌 대기 잡 = 사유 `needs_action` + `blocked_by`(남은 시간 null) + `action.code` `irreversible_hold`. 새 사유 값은 만들지 않는다. 문구에는 공개돼도 되는 것(잡 번호 · 프리셋 · 워커 이름)만(결정 330) |
| F76 | 로컬 레인도 같은 러너(`start_new_session=True`)다. 서버 기동 정리가 로컬 `running` · `cancelling` 을 `lost`(`server_restarted`)로 닫고, 정상 종료는 `should_stop` 으로 그룹을 끊어 `lost`(`server_stopped_while_running`) | `runner.py:390`, `store.py:3878-3909`, `worker.py:150-157, 282-287, 702-703` | 서버가 강제로 죽으면 로컬 표식 잡의 명령이 살아 있는 채로 재기동이 잡을 닫고 그룹을 푼다 — 같은 두 번 실행 → 330 · 331 을 로컬 레인 잡에도(결정 330 · 331 의 적용 범위) |
| F77 | 오래된 잡 지우기는 `metadata_retention_days`(180) 뒤 잡 행을 지운다. 이미 산출물 · 사슬로 지우지 않는 조건이 있다 | `store.py:1725-1756`, `config.py:124-125` | 열린 홀드가 있는 잡 행은 지우지 않는다(지우면 홀드가 근거를 잃는다 — fail-open, 결정 330) |
| F78 | (실측) 감독자 원형(같은 파이썬, `start_new_session`, 명령은 같은 그룹, ppid 폴링): 부모 SIGKILL → 그룹 소멸, 폴링 0.1 / 0.25 / 0.5 / 1.0 초에서 SIGTERM 을 받는 명령 0.09~0.10 / 0.07~0.31 / 0.54 / 1.03~1.05 초, SIGTERM 을 무시하는 명령(grace 3) 3.06~3.11 / 3.02~3.30 / 3.51~3.55 / 3.99~4.01 초. 30초 잡 동안 감독자 CPU 0.026 / 0.021 / 0.021 / 0.021 초(≈ 파이썬 기동). `python -I` 기동 + 표준 라이브러리 import 0.0165 초(중앙값). 폴링 sleep 으로 자식 끝을 기다리면 잡 끝이 폴링만큼 늦다(`/usr/bin/true` 0.29 초 vs 직접 0.0015 초) | 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS 27.2)에서 스크래치 원형 · 부하 스크립트(레포 밖), 조합마다 3회 | `SUPERVISOR_POLL_SECONDS` = 0.25(CPU 는 1초와 같고 감지는 ≤ 0.31초), 자식 기다리기는 막는 `waitpid` 스레드(끝 감지 지연 0), 종료 대기 = 잡의 `grace_J`(측정 넘침 ≤ 폴링 + 0.05초)(결정 331) |
| F79 | (실측) 로그 파일 1만 개의 `os.stat` 0.017~0.022 초(3회). 운영의 남은 원격 잡 수는 `—`(메인 세션이 운영을 읽는다) | 측정: 이 Mac, 스크래치 폴더 | 서버 기동 때 끝난 원격 잡도 `log.txt` 길이를 본다 — 비용은 잡 1만 개에 0.02초 꼴(결정 338, V2-10) |
| F80 | `group_gone` 은 시험 도우미 이름으로만 있다(`killpg(pgid, 0)` 이 ESRCH 면 참 — 같은 뜻) | `tests/test_runner.py:241, 592, 625` | finish 의 새 칸 `group_gone` 의 뜻을 그 도우미와 같게 정한다(결정 330) |
| F81 | heartbeat 의 `jobs` 는 `self.running`(잡 → 레인)이고 잡은 finish 를 보고한 뒤에 빠진다 | `remote_worker.py:285, 432-434, 1112-1115` | 4판: 서버가 「이 세션은 그 잡을 모른다」를 알 수 있는 것은 그 목록이 그 잡의 claim 이 워커에서 끝난 **뒤에** 찍혔을 때뿐이다(F82). 그래도 「없음」은 지금 상태라 확인으로 쓰지 않는다(결정 339-b) |

### 3.7 4판에서 더한 사실

3라운드 지적을 확인하면서 읽은 사실과 이 Mac 에서 잰 값이다.

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F82 | 서버의 「잊힌 잡」은 claim 커밋 때 `_fresh_claims[이름]` 에 잡을 넣고(`:565`) 다음 heartbeat 가 무엇이든 그것을 **꺼내며**(`:935`) 봐준다. 근거 주석은 「워커의 heartbeat 은 한 스레드에서 차례로 돌아서, 다음 목록은 이 응답을 받은 뒤에 찍힌다」(`:933-934`). 워커는 claim **응답을 받은 뒤** `run_claimed` 에서 `self.running[job.id] = lane`(`remote_worker.py:931, 934`), heartbeat 는 보내기 전에 `sorted(self.running)` 을 찍는다(`:432-434`). 3판 결정 335 는 기한을 넘긴 heartbeat 도우미를 버리고 다음 박자로 간다(WP3:671-675) — 버려진 요청이 나중에 닿으면 claim 전에 찍은 목록이 「봐주기」를 이미 쓴 뒤에 도착한다. 거꾸로 claim 응답이 늦게(중계에 붙잡혀) 닿으면 워커는 그 잡을 나중에 띄운다 | `remote_workers.py:565, 925-951`(`:933-935`), `remote_worker.py:432-434, 931-934`, `client.py:129-143` | 「다음 heartbeat 하나 봐줌」은 순서 가정이고 4판에서는 거짓 → 순번 `seq` 와 「끝난 claim 까지」 `claims_settled`(claim 요청마다 `claim_seq`)로 바꾼다. 그래도 「없음」은 지금 상태라 표식 잡은 홀드(결정 339-a · 339-b, HIGH) |
| F83 | (실측) 파이썬 `-I` 로 **파일 경로**를 실행하면 `sys.path` 에 그 파일의 폴더 · `PYTHONPATH` · 현재 폴더가 없다: 워크스페이스에 가짜 `signal.py` 를 두고 `PYTHONPATH` 로 가리켰을 때 `-I` 는 표준 `signal` 을 읽고 종료 0, `-I` 없이는 가짜를 읽어 종료 1. `-I -m <모듈>` 은 `PYTHONPATH` 를 버리므로 설치되지 않은 소스 트리의 모듈을 못 찾는다(같은 셸에서 `No module named …`). `-I <경로>` 기동은 `/usr/bin/time` 실측 0.02초(5회 모두) | 측정: 이 워크트리 `.venv`(Python 3.11.15, macOS 27.2.1), 스크래치 폴더의 표준 라이브러리만 쓰는 스크립트 | 감독자는 `-I` 를 지키고 `-m` 대신 모듈 파일의 경로로 띄운다 — 잡 env · 워크스페이스가 감독자를 바꿀 수 없고 소스 트리 실행도 된다. 감독자 파일은 표준 라이브러리만 import 해야 한다(시험이 잠금)(결정 339-c (4)) |
| F84 | (실측) `LC_ALL=C ps -A -o pid= -o pgid=` 한 번 0.0125~0.0131초(중앙값), 최대 0.0148초(20회 × 3). 출력에는 부른 `ps` 자신이 같은 그룹으로 나온다. macOS `pgrep -g <pgid>` 는 부른 프로세스의 조상을 빼고 낸다(같은 그룹의 감독자 자신이 안 나옴 — Linux procps 는 자신만 뺀다, 이 머신에서 재지 않음 `—`) | 측정: 위와 같은 `.venv`, setsid 그룹 + SIGTERM 무시 자식 | 감독자의 「그룹에 남은 멤버」는 `ps -A -o pid= -o pgid=` 를 읽고 자기 pid 와 그 `ps` 의 pid 를 뺀 것 — 두 OS 가 같은 꼴. `ps` 를 못 돌리면 「남았다」로 본다(닫힌 쪽)(결정 339-c (2)) |
| F85 | 로그를 읽는 쪽은 파일 길이까지 읽는다: `App.log_bytes` 는 `offset` 부터 4 MiB 를 읽고 `fh.tell()` 을 다음 오프셋으로(`server.py:5237-5256`, 라우트 `:5973-5987` — `rcm logs` 와 웹 로그 보기가 쓴다), 상태 · 잡 문서의 로그 꼬리는 `tail_lines`(`worker.py:90`)를 `server.py:3789, 3851` 에서. SSE(`:6440-`)는 버스 사건만 흘리고 마커 사건은 로그 커밋 뒤에 나간다(§4.5 step 9) | 각 줄 | 쓰기와 커밋 사이 · 커밋 실패 뒤 되자르기 전의 바이트를 읽는 쪽이 본다 → `log_file_bytes` 가 있으면 그 길이까지만(결정 339-f) |
| F86 | `[[notify]]` 는 잡 끝 사건만 안다: 규칙 `on` 은 종료 상태만 받고(`config.py:793-795`), 알림 스레드는 `KIND_JOB_FINISHED` 만 본다(`notify.py:305`). 중복 막기는 `(job_id, notify_name)` 행 선점(`store.py:3049-3063`), 기동 스캔은 알림 행이 **하나도 없는** 종료 잡만(`store.py:3081-3088`). 규칙 이름은 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`(`config.py:78`) — `:` 가 없다. 훅 env 의 키 집합을 시험이 정확히 잠근다(`tests/test_notify_rules.py:59-88`, 대조 `:154, :207`) | 각 줄 | 홀드 알림은 opt-in 사건 종류 · `on` 의 새 값 `irreversible_hold`(규칙이 적을 때만, 6차), 중복 막기 키 `hold:<규칙 이름>`(규칙 이름과 겹칠 수 없다), 기동 스캔은 `hold:` 행을 잡 끝 알림 행으로 세지 않는다(결정 339-e) |
| F87 | `tools/dev_batch.py` 는 한 묶음의 owns 가 아직 머지 안 된 다른 묶음의 owns · shared 와 겹치면 거절한다(shared 끼리는 허용) | `tools/dev_batch.py:229-233` | 3판 §5 의 PR2 · PR3 은 둘 다 `core/status.py` 를 owns 로 가져 같은 물결에 못 든다(V1-4) → 홀드의 JSON 을 PR1 로(결정 350) |
| F88 | 러너의 펌프는 stdout 파이프가 EOF 일 때까지 돌고 SIGTERM→SIGKILL 타이머는 그 루프 안에서만 간다(`runner.py:430-471`). 파이프를 쥔 멤버가 남으면 러너의 SIGKILL 이 닿고, 파이프를 닫은 멤버는 루프가 끝난 뒤 남는다 | `runner.py:430-471` | 3판 S7 의 「리더 끝 뒤 새 grace」는 파이프를 닫은 멤버에만 일하고, 그 시계가 리더 끝부터라 상한이 2·grace_J + 4 였다(V1-2 · V2-3) → S7 의 SIGKILL 시한은 첫 SIGTERM + grace_J(결정 339-c (2)) |
| F89 | 잡 행에 제출 때의 `concurrency_group`(NULL 가능) · `concurrency_scope`(NOT NULL, 기본 `pool`) · `pool` · `host_key` 가 있다 | `store.py:186, 214, 232-233` | 홀드 행은 이 칸을 닫는 때 복사한다 — 3판의 「그룹 NULL 이면 프리셋의 지금 그룹」(WP3:1101-1102, :1214-1215)은 닫는 때의 설정을 읽어 프리셋이 사라지면 열리는 쪽이 된다(V1-3) → claim 때 유효 그룹을 잡 행에 적고 홀드는 잡 행만 읽는다(결정 339-d) |
| F90 | claim 응답의 프리셋이 사라졌으면(`null`) 워커는 프로세스 없이 `preset_missing` 으로 끝낸다 | `remote_worker.py:942-946` | 그때의 X 는 claim 때 0 으로 얼어도 아무것도 돌지 않았다 — 홀드가 필요 없다(V2-8) |
| F91 | 오류 응답 본문은 `_send_error` 가 `{"error": …, **e.extra}` 로 쓴다 | `server.py:5624-5640` | M5t-c 343 의 `pair_reject_mac` 은 이 자리에서 붙는다 → M5t-b 의 409 는 모두 `ApiError` 로 내고 따로 응답을 쓰지 않는다 |

## 4. 설계

> 읽는 법(4판): 「3판 그대로」 로 옮긴 절에 남은 전역 `_heard_lock` 은 4판에서 세션별 잠금 `_session_locks[S]`(결정 339-g)로
> 읽는다. 레거시 잡의 X 를 「닫는 때의 프리셋 값」 이라고 적은 곳은 4판 규칙 「표식이거나 설정에 없음」(결정 339-d)으로 읽는다.
> 두 규칙의 정본은 §4.1 의 `heard` 불릿과 §4.12 다.

첫 절은 상태 전이 표다(결정 252). 뒤의 절들은 이 표의 칸을 장치별로 풀어 쓴다. 3판에서 더한 단계 × 죽음 표와 증명은
§4.1.4 · §4.1.5 에 있고 2판과 3판의 두 단계 점검표는 §4.1.6 에 있다. 그룹 홀드와 감독자는 §4.12 · §4.13 에 새로 두었다.

### 4.1 상태 전이 표와 타이머

이 절을 가장 먼저 쓴다. 나머지 설계는 이 표에서 나온다. 아래 용어는 이 절에서만 쓴다.

- 세션 S 는 워커 프로세스 한 번이다(결정 265). 잡 J 는 claim 때 S 와 묶인다(`jobs.worker_session_id`). lease 는 (J, S) 쌍이고
  따로 lease id 를 두지 않는다(결정 265).
- 잡 J 의 얼린 값은 `G_J`(`jobs.disconnect_grace_seconds`)와 `X_J`(`jobs.irreversible`)다(결정 257).
- 서버의 들음 시각 `heard(S)` 는 서버가 S 의 인증된 현재 세션 요청을 받은 단조 시각이다. 요청 처리를 시작할 때
  `_heard_lock` 안에서 갱신한다(결정 328). 메모리에만 두고 바닥은 서버 기동 단조 시각이다(결정 266). 펜싱된 세션, 표에 없는
  세션, 다른 이름의 세션(결정 336, V1-8)이 보낸 요청은 `heard` 를 올리지 않는다. 세션 없는 레거시 요청은 같은 `_heard_lock`
  아래에서 이름을 키로 하는 `heard_legacy(이름)` 을 올리고 세션 요청은 그 값을 올리지 않는다(결정 336, V1-5).
- 서버 경과는 `gap(J) = mono_now − max(heard(S_J), 기동)` 이다. 「들림」은 `gap ≤ T`, 「조용함」은 `gap > T` 이고 T 는
  `worker_timeout_seconds` 다. 둘 다 단조 시계로 잰다. 레인 `down` 표시는 오늘처럼 벽시계 `workers.last_seen_at`(F1)으로
  정하므로, 서버 벽시계가 뛰면 둘이 잠깐 다를 수 있다(V1-11).
- 워커의 `ok_at(S)` 는 S 의 요청 가운데 2xx 를 받은 마지막 요청을 보낸 시각이고 벽시계와 단조 시계로 둘 다 적는다. 요청은
  register, claim, heartbeat, phase, log, 산출물, finish, capabilities 어느 것이든 된다. 잡 J 의 첫 값은 J 를 돌려준 claim
  요청을 보낸 시각이며 claim 응답 시각보다 이르거나 같다(F55). 서버는 그 요청을 받은 뒤에 `heard` 를 올리므로 늘
  `ok_at ≤ heard` 다. 3판: 전체 기한을 넘겨 버린 heartbeat 가 늦게 2xx 를 받아도 그 요청을 보낸 시각으로 `ok_at` 을 올린다.
  이 값도 `heard` 이하다. 죽은 세션 D 의 큐를 D 의 id 로 보낸 요청이 받은 2xx 는 새 세션의 `ok_at` 을 올리지 않는다. 그
  요청은 D 의 것이다(V2-8).
- 워커 경과 `wgap` 은 `ok_at` 부터 잰 벽시계 경과와 단조 경과 가운데 큰 쪽이다.
- `T_req` 는 15초다. 3판에서는 이 값을 heartbeat 요청의 전체 기한 `HEARTBEAT_DEADLINE_SECONDS` 로 정했다. 보낸 순간부터 재는
  기한이고 소켓 연산마다의 시한이 아니다(F68, 결정 335). 기한을 넘기면 heartbeat 스레드는 그 요청을 실패로 세고 다음 박자로
  간다. `SELF_STOP_SLACK_SECONDS` 는 10초이고 감시 틱 1초, 러너 점검 4초, 남김 5초를 더한 값이다(F56). `grace_J` 는 J 를
  멈출 때 SIGTERM 과 SIGKILL 사이에 기다리는 시간이다. 워커 `grace_seconds` 에 §4.3 의 상한을 적용한다.
- 여유는 `m_J = T_req + grace_J + SELF_STOP_SLACK_SECONDS` 이고 기본값은 15 + 10 + 10 = 35초다(결정 320).
- 하한은 `L = max(T, 2 × H + 3 × T_req + 5)` 이고 H 는 `worker_heartbeat_seconds` 다(3판, 결정 335). 기본값으로는
  max(60, 10 + 45 + 5) = 60 이고 2판과 같은 값이다. 설정 검증과 grace 상한이 늘 `G_J − m_J ≥ L` 을 보장한다(§4.3).
- 연결된 워커의 `ok_at` 나이에는 상한이 있다(3판, V1-3 · V2-11). 마지막 2xx 를 보낸 뒤 다음 2xx 가 오기 직전까지의 나이는
  `d₁ + H + d₂` 다. 그 사이에 heartbeat 2xx 하나를 잃으면 `d₁ + H + d₂ + H + d₃` 가 된다. heartbeat 마다 `d ≤ T_req`(전체
  기한)이므로 나이는 `2H + 3·T_req` 이하다. L 의 `+ 5` 는 `stopping.wait(H)` 와 기한 `join` 의 깨어남 지연, 감시 틱을 덮는다.
  2xx 를 둘 잃으면 멈출 수 있다. 그 결과는 `lost`(3)이고 두 번 돌지 않는 쪽이다(§9).
- 네트워크 상태는 셋이다. N 은 정상이다. D 는 서버 정지로 연결이 거부된다. P 는 분단으로, 서버는 살아 있는데 워커 요청에만
  응답이 없다.
- 4판(결정 339-a): 워커의 heartbeat 순번 `seq` 는 세션마다 1 부터 하나씩 느는 정수다. 워커는 heartbeat 목록을 찍는 잠금 안에서 이
  값을 받는다. 서버는 세션마다 「받아들인 마지막 seq」를 메모리에 두고 세션별 잠금 안에서 고친다.
- 4판: `claim_seq` 는 워커가 claim 요청마다 붙이는 세션별 정수이고 1 부터 하나씩 는다. claim 이 잡을 돌려주면 서버가 그 값을
  `jobs.claim_seq` 에 적는다.
- 4판: `claims_settled` 는 워커가 heartbeat 목록을 찍는 순간의 값이다. `claim_seq` 가 이 값 이하인 claim 요청이 모두 끝났다는
  뜻이고 그런 값 가운데 가장 크다. 끝났다는 것은 잡을 받아 `running` 에 넣었거나 204, 오류, 시한 가운데 하나로 돌아왔다는 것이다.
  열린 claim 이 있으면 `min(열린 것) − 1` 이고 없으면 마지막으로 낸 `claim_seq` 다.
- 4판(결정 339-g): `heard(S)` 의 갱신과 판정은 전역 `_heard_lock` 이 아니라 세션별 잠금 `_session_locks[S]` 안에서 한다. 레거시
  요청은 `legacy:<이름>` 키를 쓴다. `_heard_lock` 은 그 사전에 접근하는 짧은 메모리 잠금으로만 남는다.

#### 4.1.1 서버가 보는 전이 (세션 상태 × 잡 상태 × 네트워크)

| # | 세션 | 잡(서버 DB) | N 정상 | D 서버 정지 | P 분단 | 행 |
|---|---|---|---|---|---|---|
| T1 | 현재 · 들림(gap ≤ T) | running(executing) | heartbeat · phase · log · finish 받음. finish 가 상태를 정함(첫 보고가 이김). heartbeat 응답에 그 세션의 닫힌 잡 `closed_jobs`(결정 321) | 판정 없음(서버가 없다). 워커는 보고를 큐에 쌓고 잡은 계속. 서버가 뜨면 `heard` 바닥 = 기동 시각 | 서버: `heard` 가 멈춤 → T 뒤 T2 | RL6 RL7 RL16 |
| T2 | 현재 · 조용함(T < gap ≤ G_J) | running | (다시 들리면 T1, 같은 세션 · 같은 lease 로 이어 보고, 프로세스 안 죽음) | 기동 뒤 T 안에 heartbeat 가 오면 T1 | 잡은 `running` 유지, 사유 `worker_unreachable_grace`, ETA null, 레인 `down`(헬스 503 그대로). 서버는 닫지 않는다 | RL1 RL5 RL6 RL26 RL28 |
| T3 | 현재 · 조용함(gap > G_J) | running | — | 서버가 꺼져 있던 시간은 세지 않는다(바닥 = 기동, 2차 Q-C). 기동 + G_J 에 판정 | `_heard_lock` 안에서 판정 · 조건부 닫기 → `lost`(`worker_unreachable`, `seconds` = int(gap)), `job_finished` 1회. 일반 잡은 그룹 자리가 풀린다. **X_J 면 같은 트랜잭션에서 그룹 홀드(T18, 결정 330)** — 확인 없는 닫힘이다. 그 뒤 같은 세션의 finish → `late_result` 만(결정 256). 다시 붙으면 heartbeat `closed_jobs` 로 알림 | RL1 RL2 RL3 RL4 RL5 RL7 RL35 |
| T4 | 현재 · 들림 | cancelling | heartbeat `cancel` → 워커가 끝냄 → `cancelled`(supersede 면 `superseded by #N` 유지). 워커가 확인 안 하면 `kill_at + 2H` 에 `cancelled`(`cancel_unconfirmed`) — 현재 · 들리는 세션의 잡에만. 3판: X_J 면 그 닫힘에 그룹 홀드(확인 없는 닫힘, 결정 330) | 기동 뒤 heartbeat 가 오면 `cancel` 전달 | 조용해지면 `kill_at` 규칙 멈춤. gap > G_J 면 `lost`(`worker_unreachable`). `cancelled`(2)로 위장하지 않는다. 다시 붙은 워커는 `closed_jobs` 의 `had_cancel: true` 로 그 잡을 멈춘다(결정 322) | RL8 RL33 |
| T5 | 현재 · 들림 | running · cancelling, 워커가 모름(claim 응답 유실) | 현재 세션의 heartbeat 가 `seq` > 받아들인 seq 이고 `claims_settled ≥ J.claim_seq` 인데 `jobs` 에 J 가 없으면 `lost`(`worker_restarted_without_job`) — busy 잡 전부. 둘 중 하나라도 아니면 그 heartbeat 의 목록으로는 J 를 판정하지 않는다(`heard` 는 오른다). 4판: 이 닫힘은 **확인이 아니다** — X_J 면 같은 트랜잭션에서 홀드(`cause = worker_restarted_without_job`, 결정 339-b). 그 홀드는 §4.12 표의 확인 1~4 가 뒤에 오면 풀린다. 레거시 잡(T11)은 오늘의 `_fresh_claims` 규칙 그대로(옛 워커는 heartbeat 를 차례로 보낸다) | 해당 없음 | 재연결 뒤 같은 규칙. 끝내 안 오면 T3 | RL9 · RL50 |
| T6 | 현재 | running(materializing) | 정상 진행 | 워커: 스냅샷 내려받기 연결 실패 → finish `lost`(`snapshot_unreachable`) 큐 → 서버가 뜨면 전달 | 같음. 전달 전 gap > G_J 면 T3, 늦은 finish 는 `late_result` | RL23 |
| T7 | 현재 | running, 워커는 끝냄(보고 큐에 finish) | 큐 순서대로 phase → 로그 → 산출물 → finish. 확정. 로그 · 산출물 단계가 5xx 를 300초 넘게 받으면 finish 먼저(결정 324) | 서버가 뜨면 이어서 | 재연결이 G_J 안이면 실제 결과, 밖이면 T3 뒤 `late_result` | RL14 RL19 RL21 RL22 RL40 |
| T8 | 펜싱됨(같은 이름의 새 세션 S′ 가 등록) | running · cancelling | 옛 프로세스의 다음 요청(무엇이든) → 409 `session_fenced` → 그룹 멈춤 → 좁은 확인 finish(`lost` + `worker_replaced`) 만 받음 → 잡 `lost`. 옛 프로세스 종료 0. 확인 전까지 잡은 busy(그룹 · 레인 잡힘), 새 세션의 그 레인 claim 은 204 | 판정 없음(펜싱은 DB 에 남는다) | 확인이 없으면 `heard(S) + G_J` 에 `lost`(`worker_unreachable`). S 가 `unverified_sessions` 로 알려졌고 X_J 면 요약 코드 `orphan_unverified`(결정 327). 그 전에는 안 푼다(결정 261). 3판: X_J 면 이 닫힘에 그룹 홀드. 좁은 확인이 `group_gone: false` 를 실어 오면 잡은 닫되 홀드(결정 330) | RL10 RL11 RL31 RL42 |
| T9 | 이전 세션 D(새 프로세스가 D 의 폴더 잠금으로 죽었음을 확인, 폴더 주인이 같음) | running · cancelling | 새 프로세스가 등록 **전에** D 의 고아 그룹 정리 → D 의 남은 큐를 D 의 id 로 보냄(응답은 그 큐에만, 결정 326) → 등록 `previous_sessions`(신원 확인된 D) · `unverified_sessions`(모름인 D) → 서버가 `previous_sessions` 의 남은 busy 잡을 `lost`(`worker_restarted_without_job`)로 닫고 펜싱. 3판: `previous_sessions` 는 그 세션의 그룹이 모두 사라졌다는 **인증된 확인**이다 — 닫는 잡에 홀드 없음, 그 세션의 이미 닫힌 잡에 열린 홀드가 있으면 같은 트랜잭션에서 푼다(`release_kind = worker`, 결정 330). 등록은 D 의 큐를 많아야 `DEAD_SESSION_WAIT_SECONDS`(300) 기다린다(결정 337) | 등록이 5초마다 다시 시도 | 같음 | RL12 RL14 RL15 RL31 RL38 RL39 RL44 RL46 |
| T10 | 같은 세션 id 의 재등록(레인이 `not_registered` 409 를 받음) | running | 멱등: 펜싱 없음 · 잡 안 닫음 | — | — | RL12 |
| T11 | 레거시(세션 칸 NULL — v26 이전에 claim 된 잡) | running · cancelling | 세션 없는 heartbeat · phase · log · finish 를 **그 이름에 레거시 busy 잡이 있을 때만** 받음(이름 단위 「잊힌 잡」 규칙도 그 잡에만). 새 claim 은 409 `protocol_required`. 3판: 그 요청은 `heard_legacy(이름)` 을 올린다(결정 336) | 기동 바닥 | `heard_legacy(이름) + 서버 기본 G` 에 `lost`(`worker_unreachable`). 4판: 레거시 잡의 X 는 「닫는 때 그 프리셋이 표식이거나 설정에 없다」(모름은 닫힌 쪽, 결정 339-d), 홀드의 그룹은 잡 행의 그룹, NULL 이면 `irreversible:<프리셋 이름>` · `global` | RL24 |
| T12 | 세션 없는 요청(옛 워커), 레거시 잡 없음 | — | register · claim · heartbeat · capabilities → 409 `protocol_required`(+`worker_protocol: 2`, `error` 에 「not registered」 낱말 없음 — F40 · 결정 329). 등록 409 는 버전 검사 **뒤**(버전이 다르면 오늘의 409 + `server_version`) | — | — | RL13 RL24 |
| T13 | 새 워커 → 프로토콜 1 서버 | — | `/worker/spec` 에 `worker_protocol` 이 없고 서버 버전이 같으면 등록하지 않고 종료 3(세션 폴더도 안 만듦). 버전이 다르면 오늘처럼 등록 409 → 따라가기(M5q) 또는 종료 2 | 등록 전 연결 실패는 오늘처럼 5초 재시도 | — | RL25 RL32 |
| T14 | 끝난 잡(J terminal)에 늦은 보고 | terminal | 같은 세션의 finish 이고 J 가 `lost` → 한 트랜잭션에서 `late_result` 1건(없을 때만) + `log_complete` 쓰기, 409 `job_closed`(+`state`, `late_result_recorded`). log · phase · 산출물 → 409 `job_closed`, 반영 0. 펜싱된 세션 → 409 `session_fenced`(기록 없음, 2차 Q-B) | — | — | RL11 RL21 |
| T15 | 현재 | running · cancelling, 로그 요청이 서버에서 실패 | 파일 쓰기 OSError(ENOSPC 등) → 503 `log_write_failed`, 커밋이 바쁨 → 503 `database_busy`, 커밋 0행(잡이 그새 닫힘 · 펜싱) → 409 `job_closed` · `session_fenced`. 셋 다 `log.txt` 를 `log_file_bytes` 로 되자르고 `log_offset` 은 그대로. 워커는 같은 오프셋부터 다시(같은 잡의 동시 요청은 잡별 잠금으로 줄 섬, 결정 323) | — | — | RL36 |
| T16 | 현재 | running · cancelling, 서버의 `log.txt` 가 `log_file_bytes` 보다 짧음(OS 충돌, fsync 없음) | 다음 쓰기(또는 기동 정리)가 알아챔 → `jobs.log_complete = 0` 고정 + 서버 로그 한 줄(「log gap」), 쓰기는 파일 끝에서 이어감(빈 바이트 구멍 없음). 3판: 기동 정리는 **끝난** 원격 잡도 본다(`log_complete = 1` 인 잡의 파일이 짧으면 0 으로 고침, 결정 338, V2-10) | — | — | RL37 |
| T17 | 새 세션 S 의 첫 등록, 그 이름에 세션 NULL(레거시) busy 잡이 있음 | running · cancelling | 같은 등록 트랜잭션에서 그 잡을 오늘처럼 `lost`(`worker_restarted_without_job`)로 닫는다 — 새 프로토콜 프로세스가 같은 이름으로 섰으니 그 잡은 옛 프로세스의 것이다(결정 255 · 336). X(T11 과 같은 레거시 규칙)면 그룹 홀드(옛 프로세스의 신원을 확인할 길이 없다 — 세션 폴더도 `proc.json` 도 없다) | — | — | RL24 RL47 |
| T18 | (잡 J 가 terminal, 열린 그룹 홀드 — 결정 330) | terminal | 그 홀드의 그룹(범위 규칙 그대로, F73)의 대기 잡은 claim 되지 않고 사유 `needs_action` · `action.code` `irreversible_hold`. 풀림: (가) J 의 세션(현재 또는 펜싱된 좁은 확인)이 `group_gone: true` finish (나) 새 세션의 등록 `previous_sessions` 에 J 의 세션 (다) admin `POST /jobs/{J}/release-hold`. 풀면 감사 행 · 사건 · 대기 잡 다시 계산 | 서버가 꺼져도 DB 에 남는다(영속) | 같음 | RL42 RL44 RL45 RL48 |
| T19 | 다른 이름의 세션 id(토큰 이름 ≠ `worker_sessions.worker_name`) | — | 그 세션 id 를 실은 모든 요청(register 포함) → 403(`error` 「session belongs to another worker」, `code` 없음), `heard` 안 올림, 반영 0(결정 336, V1-8) | — | — | RL11 |

#### 4.1.2 워커가 보는 전이 (워커 프로세스 한 개, 잡 하나)

| # | 워커 상태 | 표식 잡(X_J) | 일반 잡 | 행 |
|---|---|---|---|---|
| W1 | 연결됨(2xx) | 그대로. `ok_at` 갱신 | 그대로 | RL6 |
| W2 | 끊김, `wgap < G_J − m_J` | 계속 돌고 보고는 큐에 | 계속 | RL1 RL5 RL6 |
| W3 | 끊김, `wgap ≥ G_J − m_J` — **감시 스레드**의 1초 틱 판정(heartbeat 루프와 무관, 잠에서 깬 첫 틱 포함) | 그 잡의 관찰자 `self_stop` 을 세움 → `should_stop` 이 참(결정 334). 프로세스가 있으면 러너가 그룹 SIGTERM → `grace_J` → SIGKILL, 아직 없으면(준비 중) Popen 직전 확인이 띄우지 않음(결정 333). finish `lost` + `worker_self_stopped`(`seconds` = int(wgap)) + `group_gone` 큐. 한 번 정하면 되돌리지 않는다 | 계속(결정 253) | RL1 RL2 RL4 RL26 RL34 RL41 |
| W4 | 서버가 닫은 잡을 앎: 보고의 409 `job_closed`(+`state`) 또는 heartbeat 2xx 의 `closed_jobs` `{id, state, had_cancel}`. 3판: 모든 끝 상태를 아래 W4 표로(V2-6). 멈춤은 관찰자 `closed_stop` → `should_cancel` 길(결정 334) — 서버 상태는 이미 정해졌고 워커의 finish 는 확인 · `late_result` 로만 쓰인다 | 아래 W4 표 | 아래 W4 표 | RL5 RL8 RL21 RL35 |
| W5 | 살아 있는 이 프로세스의 요청이 409 `session_fenced` | 모든 잡 멈춤 → 잡마다 좁은 확인 finish → 종료 0. 4판(M5t-c 343): 짝지은 워커는 그 409 의 `pair_reject_mac` 이 검증될 때만 이 길(종료 0). 검증되지 않으면 잡을 건드리지 않고 새 hello 뒤 다시 시도(M5t-c J12′). 짝짓지 않은 워커는 그대로 | 같음(표식 무관, 결정 255 · 261) | RL10 RL11 |
| W6 | 등록 전 409 `protocol_required` | 종료 3. 4판(M5t-c 343): 짝지은 워커는 그 409 의 `pair_reject_mac` 이 검증될 때만 종료 3(M5t-c J12). 검증되지 않으면 종료하지 않고 새 hello 뒤 등록 백오프로 프로세스 안에서 다시 시도, 상태 사유 `registration_refused`(M5t-c J13, `docs/m5tc-workplan.md` 의 343 행, PR #269). 짝짓지 않은 워커는 그대로 | 같음 | RL25 |
| W7 | 프로세스가 `kill -9` 로 죽음 | 3판: 감독자가 `getppid()` 바뀜을 ≤ 0.25초(실측 ≤ 0.31초, F78)에 알아채고 그룹 SIGTERM → `grace_J` → SIGKILL(결정 331). 서버는 확인을 못 받아 `heard + G_J` 에 `lost` + 그룹 홀드(T3 · T18). 다음 프로세스가 뜨면 D 의 그룹이 없음을 확인해 `previous_sessions` 로 홀드를 푼다(T9). 아무도 다시 안 띄우면 홀드가 남아 admin 이 푼다 | 고아로 계속(결정 253) → 다음 프로세스가 시작 때 정리(T9). 아무도 안 띄우면 서버가 G 뒤 `lost`, 홀드 없음 | RL14 RL15 RL31 RL43 |
| W8 | SIGTERM/SIGINT | 오늘처럼 멈춤, finish `lost`(`worker stopped` 문장) 큐 → 종료 전 한 번 보내고 못 보내면 디스크에 남김 → 다음 시작이 보냄 | 같음 | RL14 |
| W9 | 시작 때 다른 세션 폴더의 주인(`owner.json`)이 나와 다르거나 없음 | 건드리지 않음: 잠가 보기 · 고아 정리 · 큐 보내기 · 지우기 모두 안 함(7일 지나고 잠글 수 있을 때만 청소, 결정 325) | 같음 | RL38 |
| W10 | 죽은 세션 D 의 큐를 D 의 id 로 보내다 받은 응답 | §4.4 의 표대로 **그 큐에만** 적용. 이 프로세스를 멈추거나 D 의 id 로 재등록하지 않는다(결정 326) | 같음 | RL39 |
| W11 | 잡이 끝난 뒤 로그 · 산출물 단계가 HTTP 5xx 를 `FINISH_HOLD_SECONDS`(300) 넘게 받음 | finish 먼저(산출물 처분 `failed` + `upload_failed`, `log_complete` 는 서버가 `log_offset < log_bytes` 로 false), 2xx 뒤 남은 로그 · 산출물은 버림(결정 324) | 같음 | RL40 |
| W13 | 감독자 자신이 죽음(OOM · 누가 SIGKILL) | 명령은 같은 그룹에 남는다(pgid = 감독자 pid 는 멤버가 있는 동안 산다). 4판(V1-5): 워커가 살아 있으면 러너가 리더(감독자) 끝을 보고 S7 로 남은 그룹을 거둔다 — 그룹 소멸 ≤ 리더 끝 + `grace_J` + 2초, 잡은 `failed`(종료 코드 −9, 리더의 것), finish `group_gone: true` → 홀드 0. 그 뒤의 취소는 끝난 잡이라 효과가 없다. 워커까지 죽으면 아무도 못 본다 → 서버가 확인 없이 닫음 → 홀드(T18). 다시 뜬 워커는 D 의 그룹이 살아 있고 리더가 없음 = 「모름」(T9 · W12) | 해당 없음(감독자 없음) | RL43 |
| W14 | 감독자가 시작할 때 부모가 기대한 pid 가 아님(Popen 직후 워커가 죽음) · 시작 전에 SIGTERM 을 받음 | 명령을 띄우지 않고 종료 `SUPERVISOR_NOT_STARTED_EXIT`(125) + 잡 로그 첫 줄 `[rcm] supervisor: not started: parent_gone`(또는 `terminated`)(결정 339-c (3)). 서버 쪽은 W7 과 같다 | 해당 없음 | RL43 |
| W12 | 죽은 세션 D 의 고아 신원이 「모름」(§4.4 step 6) | `D.ok_at + G_J − m_J` 에 다시 확인 → 확인되면(같은 그룹이면 멈춤, 없거나 다른 프로세스면 그대로) D 의 id 로 좁은 확인 finish. 그래도 모름이면 아무것도 안 함 → 서버가 유예 만료에 `orphan_unverified` 로 닫음(결정 327) | 고아 신원 확인 자체는 같다. 일반 잡이면 서버가 닫을 때 요약 코드는 `worker_unreachable`(일반 잡은 두 번 돌아도 된다, 결정 253) | RL31 |
| (지움) | 1판 W6 의 「도는 중 409 `protocol_required` → 모든 잡 멈춤 → 종료 3」 | 닿지 않는다: 새 서버만 `protocol_required` 를 내고, 서버를 옛 빌드로 내리면 옛 빌드가 v26 DB 를 열지 않는다(F50). 닿는다면 일반 잡을 죽여 결정 253 에 어긋난다(V2-9). 도는 중 받으면 그 요청 하나의 실패로 로그만 남기고 잡은 건드리지 않는다 | | |

W4 의 반응은 서버가 알린 끝 상태마다 아래 표로 정한다(3판, V2-6). 「아직 돌면」은 그 잡의 러너가 아직 돌아오지 않은
경우다.

| 서버 상태 | 어떻게 생기나 | 표식 잡 | 일반 잡 | 워커의 finish |
|---|---|---|---|---|
| `succeeded` · `failed` · `timed_out` · `retried` · `exhausted` | 이 세션의 finish 가 이미 기록됨(2xx 만 잃음). 서버는 원격 잡을 스스로 `timed_out` 으로 닫지 않는다(`remote_workers.py:78, 798`) | 프로세스는 이미 끝났다. 큐의 그 잡 항목을 「전달됨」으로 지운다 | 같음 | 보내지 않음 |
| `cancelled` | 취소 확인 · `cancel_unconfirmed` · supersede | 아직 돌면 멈춤(`closed_stop`) | 아직 돌면 멈춤 | `cancelled` + `group_gone` → 409 `job_closed`. 표식이고 열린 홀드면 `group_gone: true` 가 홀드를 푼다 |
| `lost` | 유예 만료 · `orphan_unverified` · 펜싱 확인 없음 · T5 · T17 | 아직 돌면 멈춤 | `had_cancel` 이면 멈춤, 아니면 계속(로그 전송만 멈춤, 결정 253 · 322) | 끝나면 finish → 같은 세션이면 `late_result`, 표식이고 `group_gone: true` 면 홀드를 푼다 |
| `expired` · `scheduled` · `queued` · `uploading` | 원격 claim 된 잡이 될 수 없는 상태 | 워커 로그 한 줄, 아무것도 안 함(서버 버그 신호) | 같음 | — |

#### 4.1.3 타이머와 시계

| 타이머 | 누가 | 시계 | 시작점 | 끝나면 | 값 |
|---|---|---|---|---|---|
| heartbeat 간격 H | 워커 heartbeat 스레드 | `stopping.wait` | 직전 회차가 끝난 때(F68) | 다음 heartbeat. **자기 멈춤 판정은 여기서 하지 않는다** | 서버가 알려 줌 `worker_heartbeat_seconds` 5 |
| heartbeat 전체 기한(3판) | 워커 heartbeat 스레드 | 단조 | 요청을 보낸 순간 | 그 요청을 실패로 세고 다음 박자. 늦은 2xx 는 보낸 시각으로 `ok_at` 을 올림 | `HEARTBEAT_DEADLINE_SECONDS` 15(= T_req, 결정 335) |
| 자기 멈춤 감시 틱 | 워커 감시 스레드(새, 표식 잡이 있을 때만 일함) | `stopping.wait(WATCHDOG_TICK_SECONDS)`, 경과는 `max(벽, 단조)` | `ok_at`(2xx 받은 요청의 보낸 시각. 잡의 첫 값은 그 claim 요청을 보낸 시각) | `wgap ≥ G_J − m_J` 인 표식 잡 그룹 멈춤 | 틱 1초. 문턱 `G_J − m_J`(기본 600 − 35 = 565초, 늘 ≥ L) |
| 워커 down T | 서버 | 벽시계 `workers.last_seen_at`(F1, 그대로) | 현재 세션의 마지막 요청 | 레인 `down` · 대기 잡 `worker_down` · 헬스 503. **잡은 닫지 않는다** | `worker_timeout_seconds` 60 |
| 끊김 유예 G_J(고침) | 서버 janitor | 단조 `mono_fn`, `heard(S)` 의 바닥 = 기동 | `max(heard(S_J), 기동)` | 세션별 잠금 안에서 다시 보기 + 조건부 닫기, X_J 면 같은 트랜잭션에서 홀드. 한 틱에 많아야 `GRACE_CLOSES_PER_TICK` 개, 닫기가 `database_busy` 면 이 틱의 닫기를 멈춤 | 잡 행에 얼린 값, 틱 상한 20 |
| 그룹 홀드(3판) | 서버 DB | 없음 — 시간으로 풀리지 않는다 | 확인 없는 닫힘 | 확인 · admin 풀기 | 영속(결정 330) |
| 취소 미확인 | 서버 | 벽시계(오늘 그대로) | `cancel.kill_at` | `cancelled`(`cancel_unconfirmed`) — 현재 · 들리는 세션만 | `kill_at + 2H` |
| SIGTERM→SIGKILL | 워커 러너(로컬 레인도 같은 러너) | 3판: 러너 `mono_fn`(단조, 결정 333 — 2판은 벽 `now_fn`, F67). 잡 시한은 벽 그대로 | SIGTERM | SIGKILL | `grace_J`(워커 `grace_seconds` 10, 표식 잡은 §4.3 상한) |
| 러너 점검 박자 | 러너 | 단조. 첫 점검은 Popen 직후 곧바로(1초 기다리지 않음, 결정 333) | 직전 점검 | 취소 · 정지 · 시한 판정 | `POLL_SECONDS` 1 |
| 감독자 부모 감시(3판) | 감독자(표식 잡만) | `Event.wait(0.25)` | 감독자 시작 | `getppid()` 가 기대와 다르면 그룹 SIGTERM | `SUPERVISOR_POLL_SECONDS` 0.25(실측 F78, 결정 331) |
| 감독자 종료 대기(고침) | 감독자 | 단조 | 감독자가 본 첫 SIGTERM(러너 것이든 자기 것이든) | 그룹 SIGKILL(자신 포함) | `grace_J` |
| 감독자 명령 끝 뒤 쓸기(새) | 감독자 | 단조 | 명령이 끝난 때, SIGTERM 을 받았거나 부모가 바뀌었을 때만 | 남은 멤버(F84)가 있으면 첫 SIGTERM + `grace_J` 까지 `SUPERVISOR_POLL_SECONDS` 마다 다시 보고 그때도 남으면 그룹 SIGKILL. 없으면 명령의 종료 상태로 끝 | 첫 SIGTERM + `grace_J` |
| 표식 잡 끝 거두기(고침) | 러너 | 단조 | 리더(감독자)가 끝난 때 | 러너가 이미 SIGTERM 을 보냈으면 SIGTERM 을 다시 보내지 않고 **그 첫 SIGTERM + `grace_J`** 에 SIGKILL, 안 보냈으면 지금 SIGTERM → `grace_J` → SIGKILL. 그 뒤 2초 안에 `killpg(pgid, 0)` 이 ESRCH 면 `group_gone` | 첫 SIGTERM(없으면 리더 끝) + `grace_J` + 2 |
| D 큐 등록 대기(3판) | 새 워커 | 단조 | D 의 큐를 보내기 시작 | 남은 D 큐를 디스크에 두고 등록(워커 로그 한 줄) | `DEAD_SESSION_WAIT_SECONDS` 300(결정 337) |
| finish 상한 | 워커 보내는 스레드 | 벽시계(디스크의 `ended_at` ISO) | 잡 끝 | 맨 앞 로그 · 산출물 항목의 마지막 시도가 HTTP 5xx 였으면 finish 먼저 | `FINISH_HOLD_SECONDS` 300(결정 324) |
| 재전송 창 | 워커 | 벽시계(디스크의 `ended_at` ISO) | 잡 끝 | 로그 · 산출물 포기 → finish 에 `log_bytes` 실어 보냄. finish 도 못 보냈으면 큐 항목 버림 + 로그 | `RETRANSMIT_SECONDS` 86400(결정 264) |
| D 재확인 | 새 워커 | 벽시계(D 의 `ok_at.json`) | `D.ok_at` | 「모름」 고아 신원 다시 확인 | `G_J − m_J`(결정 327) |
| `ok_at.json` 쓰기 | 워커 | 벽시계 | 2xx | 세션 폴더에 `{wall}` 기록 | H 마다 많아야 한 번 |
| 스풀 보관 | 워커 | 파일 mtime(시작 청소, F41) | 마지막 쓰기 | 폴더 삭제(잠긴 폴더 제외, 남의 폴더는 7일 · 잠글 수 있을 때만) | `WORKSPACE_KEEP_DAYS` 7(결정 264 · 325) |
| janitor 틱 | 서버 | `stop.wait` | — | 유예 판정 · 취소 미확인 | `JANITOR_SECONDS` 5 |
| 등록 재시도 | 워커 | `stopping.wait` | 연결 실패 | 다시 등록 | `REGISTER_RETRY_SECONDS` 5 |

#### 4.1.4 단계 × 죽음 표 (멈춤 판정을 누가 언제 보나, 3판 근본 원인)

이 표는 표식 잡 J 만 다룬다. 일반 잡에는 감독자, 자기 멈춤, 홀드가 없고 결정 253 대로 계속 돈다. 표에서 「확인」은 §4.12 표의 확인
1~4 를 뜻한다. J 의 세션이 보낸 `group_gone: true` finish 와 좁은 확인, 새 세션의 `previous_sessions`, 로컬 레인 러너가 본
`group_gone` 이 여기에 든다. 4판에서는 T5 의 「없음」을 확인에서 뺐다(결정 339-b). 「홀드」는 확인 없이 닫혀 그룹이 잡힌
상태다(T18).

| 단계(워커 쪽, 순서대로) | 프로세스 | 감시 발동(워커 산 채) | 워커 정상 멈춤(SIGTERM) | 워커 SIGKILL · 아무도 다시 안 띄움 | 워커 SIGKILL · 같은 데이터 폴더로 다시 뜸 |
|---|---|---|---|---|---|
| S0 claim 요청 ~ 응답 | 없음 | 잡이 아직 워커에 없다. 응답을 받으면 S4 의 확인이 플래그를 본다(첫 `ok_at` = claim 을 보낸 시각). 4판: claim 응답을 잃으면 T5 가 닫고(claim 이 워커에서 끝난 뒤의 heartbeat) 표식이면 홀드 | 응답 뒤 S4 의 확인이 `should_stop` → `lost`(STOP_SUMMARY) · `group_gone: true` | 서버: T5 가 못 닿음 → `heard + G` 에 `lost` + **홀드** | 다음 프로세스: D 폴더에 그 잡 폴더 없음 → 「띄운 적 없음」 확인 → `previous_sessions` → 닫힘(또는 이미 닫혔으면 홀드 풂) |
| S1 `claim.json` 기록 → phase `materializing`(큐) | 없음 | S4 에서 봄 | S4 에서 봄 | 같음(홀드) | `proc.json` 없음 = 「띄운 적 없음」 → 확인(3판, 결정 333) |
| S2 준비(git_ref fetch · 스냅샷 내려받기) — 판정을 안 본다 | 없음 | **S4 의 Popen 직전 확인이 띄우지 않는다**(F65 의 구멍, 결정 333). finish `lost` + `worker_self_stopped` + `group_gone: true` | 같은 확인 → `lost`(STOP_SUMMARY) | 같음(홀드) | 같음(확인) |
| S3 requires 검사(있으면) | 없음 | 오늘의 requires 안 확인 + S4 | 같음 | 같음 | 같음 |
| S4 phase `executing` → **Popen 직전 확인**(무조건, 결정 333) → `proc.json` 에 `{pgid: null}` 표시 → Popen(표식 잡은 감독자) → `proc.json` 에 `{pgid, start}` | 확인 뒤 몇 ms 안에 생김 | 확인이 놓친 플래그는 펌프의 첫 점검(Popen 직후 곧바로)이 본다 | 같음 | 감독자: 시작 때 부모가 기대와 다르면 명령을 안 띄움(W14), 띄웠으면 ≤ 0.25초에 알아채 그룹 끝냄(W7). 서버: 홀드 | `pgid: null` 이면 「모름」(띄우는 순간 죽음 — 감독자가 끝냈어도 확인할 길이 없다) → `unverified_sessions` → 재확인 → 그래도 모름이면 서버가 `orphan_unverified` + 홀드 |
| S5 실행(펌프) | 있음 | 점검 ≤ 1초 → 그룹 SIGTERM | 같음 | 감독자 ≤ 0.25초 + `grace_J` 에 그룹 끝(F78). 서버: 홀드 | `proc.json` 신원 → 같은 그룹이면 끊고 확인, 없거나 재사용이면 확인 → `previous_sessions` 가 홀드를 풂 |
| S6 SIGTERM → SIGKILL | 줄어드는 중 | 단조 타이머, SIGKILL ≤ `grace_J` + 2(F56 · F67) | 같음 | 감독자는 이미 본 첫 SIGTERM 부터 `grace_J` 뒤 SIGKILL. 4판: 명령이 그 전에 끝나도 남은 멤버가 있으면 같은 시한에 쓴다(결정 339-c (2)) | 같음 |
| S7 리더 끝 → 표식 잡 거두기 | 남은 멤버만 | 4판: 러너가 남은 멤버를 거둔다 — SIGKILL 시한은 러너의 첫 SIGTERM + `grace_J`(없으면 리더 끝 + `grace_J`), ESRCH 면 `group_gone: true` | 같음 | 4판: 감독자가 명령 끝 뒤 쓸기로 끝냄 | 같음 |
| S8 finish 를 큐에 → 보냄 | 없음 | finish `lost` + `worker_self_stopped` + `group_gone` → 서버가 G 안에 받으면 확인된 닫힘(홀드 없음), G 뒤면 이미 홀드 → 이 finish 가 홀드를 푼다 | finish `lost`(STOP_SUMMARY) + `group_gone`(종료 전 한 번 보내고 못 보내면 디스크) | 디스크 큐(PR2)가 남음. 서버: G 뒤 홀드 | 다음 프로세스가 등록 전에 D 의 id 로 보냄 → 확인 |

서버 쪽 규칙은 한 줄로 줄어든다(결정 330). 표식 잡의 그룹이 풀리는 길은 셋뿐이다. 확인과 함께 닫히는 길, 확인 없이 닫혔다가 뒤에
확인이 오는 길, admin 이 `rcm release-hold` 로 푸는 길이다. 그 밖의 닫힘은 모두 홀드를 만든다. 유예 만료, `orphan_unverified`,
`cancel_unconfirmed`, 펜싱 확인이 없는 만료, T17 의 레거시 닫기, 4판에서 더한 T5 의 「없음」 닫기(표식 잡), 로컬 레인 잡이 서버
재기동으로 `lost` 가 되는 경우가 그렇다.

로컬 레인에서는 서버 프로세스가 워커 노릇을 한다(F76). 서버가 정상 종료하면 S5~S7 의 「정상 멈춤」 길을 따라 `group_gone`
을 갖고 잡을 닫는다. 서버가 SIGKILL 로 죽으면 감독자가 명령을 끝내고 재기동한 서버의 `recover_on_start` 가 그 잡을
`lost`(`server_restarted`)로 닫으면서 홀드를 건다. 로컬 잡에는 `proc.json` 을 두지 않으므로 서버는 확인할 길이 없다. 이
비용은 받아들였다. 서버 강제 종료와 표식 로컬 잡이 겹치는 일은 드물고 그때는 사람이 그 머신에서 확인하고
`rcm release-hold` 로 푼다.

#### 4.1.5 증명 (3판, 결정 320 · 330 · 331 · 333 · 335)

정리 1(안전): 표식 잡의 그룹은 확인 없이 다음 잡에 열리지 않는다.

1. 서버는 J 를 `heard(S) + G_J` 뒤에만 스스로 닫는다. janitor 틱은 닫는 시각을 늦출 뿐이다. `heard(S) ≥ ok_at` 이다(F55 ·
   §4.1 용어). 서버가 잠들면 단조 시계가 덜 가서 더 늦게 닫는다.
2. 서버가 확인 없이 닫으면 같은 트랜잭션에서 홀드를 만든다(결정 330). 홀드는 영속이고 claim SQL 이 그 그룹을 막는다(F73).
   그래서 서버가 먼저 닫아도 이제 그룹은 열리지 않는다. 그룹이 열리는 길은 확인이나 사람뿐이다.
3. 그래도 「프로세스가 G 전에 사라진다」는 성질은 지킨다. 홀드가 사람을 부르는 일을 줄이려는 것이다. 워커가 살아 있으면 플래그는
   `t_f ≤ ok_at + G_J − m_J + 1`(감시 틱)에 선다. S0~S3 에서는 S4 의 확인이 명령을 띄우지 않는다. S4 의 틈(확인에서 Popen 까지 몇
   ms)은 펌프의 첫 점검이 곧바로 본다. S5~S7 은 F56 의 「플래그에서 그룹 소멸까지 `grace_J + 4` 이하」를 따른다(단조 타이머, F67).
   4판: S7 의 SIGKILL 시한도 첫 SIGTERM 에 `grace_J` 를 더한 때다(결정 339-c (2), F88). 그래서 리더가 먼저 끝나도 이 상한은
   그대로다. 3판은 리더가 끝난 때부터 grace 를 다시 세어 `2·grace_J + 4` 까지 갔다(V1-2 · V2-3). 따라서 그룹이 사라지는 시각은
   `ok_at + G_J − (T_req + grace_J + 10) + 1 + grace_J + 4 = ok_at + G_J − T_req − 5 < heard + G_J` 이하이고 남는 여유는 20초다.
4. 워커가 `t_d` 에 SIGKILL 로 죽으면 감독자가 `t_d + 0.25 + ε` 에 알아챈다(F78 실측 ε ≤ 0.06초). 이미 받은 SIGTERM 이 있으면 그
   시각부터, 없으면 지금부터 `grace_J` 뒤에 SIGKILL 한다. `t_d < t_f` 면 그룹 소멸은 `t_f + 0.31 + grace_J ≤ ok_at + G_J − T_req −
   8.69` 이하다. `t_d ≥ t_f` 면 러너가 이미 보낸 SIGTERM 시각이 기준이라 3 의 상한에 0.31초를 더한 값이다. 두 경우 모두 `heard +
   G_J` 앞이다. 4판: 워커가 `t_d` 에 죽고 명령이 SIGTERM 에 일찍 끝나도 감독자가 남은 멤버를 첫 SIGTERM + `grace_J` 에 쓴다.
   여기에 폴링 0.25초와 `ps` 0.015초가 더해진다(F84). 상한은 3판의 식에 0.27초를 더한 값이고 여전히 `heard + G_J` 앞이다.
5. 감독자까지 죽거나(W13) 명령이 세션을 빠져나가면(§1.3) 4 가 성립하지 않는다. 그때는 2 가 남는다. 확인이 올 수 없으니
   홀드가 걸린다. 남는 위험은 확인이 거짓인 경우 하나다. 명령이 세션을 빠져나간 자손을 남기고 그룹은 사라지면 워커가
   `group_gone: true` 를 보낸다(§9).
6. `T_req` 항은 `ok_at` 을 보낸 시각으로 재는 한 3 에 필요하지 않다. 그래도 넣는다(D-a). 구현이 `ok_at` 을 응답 받은
   시각으로 잘못 재도 heartbeat 는 기한(15초) 안에 응답하거나 실패하므로 여전히 안전하다. 두 머신 단조 시계의 속도 차와
   서버가 `_heard_lock` 을 기다린 시간도 이 항이 덮는다.

정리 2(연결된 동안 멈추지 않음, V2-6 · V1-3 · V2-11): heartbeat 는 앞 heartbeat 가 끝나고 H 뒤에 간다(F68). 하나가 걸리는 시간은
전체 기한 `T_req` 이하다(결정 335). 마지막 2xx 를 보낸 뒤 다음 2xx 직전까지의 나이는 `d₁ + H + d₂ ≤ 2·T_req + H` 다. 그 사이에 2xx
하나를 잃으면 `3·T_req + 2H` 이하다. 깨어남 지연과 감시 틱을 5초로 덮으면 `2H + 3·T_req + 5 ≤ L ≤ G_J − m_J` 다. 문턱에 닿으려면
heartbeat 2xx 를 둘 이상 잇달아 잃어야 한다. 그때의 멈춤은 연결된 채 멈춘 것으로 볼 수 없다. 서버가 그 워커를 못 들은 경우와
구별할 수 없는 경우다. 결과는 `lost`(3)이고 두 번 돌지 않는 쪽이다. 4판: 서버 쪽 대기도 센다(결정 339-g). heartbeat 처리가
기다리는 잠금은 자기 세션의 잠금 하나다. 그 안에는 메모리 갱신과, 그 세션이 조용할 때만 생기는 닫기 트랜잭션 하나가 있다. 닫기는
보통 ms 이고 상한은 `busy_timeout` 5초다. 다른 세션의 닫기는 기다리지 않는다. 이 대기와 DB 쓰기 대기는 `d` 안에 든다. `d` 가 전체
기한 `T_req` 를 넘으면 그 회차는 실패로 세고(「잃은 2xx」) 정리 2 의 문장 그대로다.

정리 3(e2e 수치, §7.5): T 10 · H 1 이면 L = max(10, 2 + 45 + 5) = 52, m = 35 이고 G 의 하한은 87 이다. §7.5 의 e2e 서버는
G = 90 을 쓰므로 문턱은 55 다. RL34(c) 의 「heartbeat 마다 14초」 경우에 나이는 14 + 1 + 14 + 1 + 15 = 45 이하이고 55 보다
작으므로 멈추지 않는다. 2판의 G 52 · 문턱 17 은 이 경우에 멈췄다(V1-3).

정리 4(4판, 확인의 순서): 서버가 홀드를 만들지 않거나 푸는 메시지는 §4.12 표의 확인 1~4 뿐이다. 넷 모두 「그룹이 사라졌다」나 「그
세션 프로세스가 죽었다」라는 되돌아오지 않는 사실을, 그 사실이 선 뒤에 본 쪽이 보낸다. 그런 메시지는 늦게 와도, 순서가 바뀌어도
참이다. J 는 한 번만 claim 되고 러너는 J 를 한 번만 띄우므로 사라진 그룹이 J 로 다시 생기지 않는다. T5 의 「없음」은 이 꼴이
아니라서 확인에서 뺐다. 그래서 정리 1 의 2(확인 없는 닫힘은 홀드)는 메시지 순서와 상관없이 선다.

#### 4.1.6 상태를 바꾸는 두 단계를 한 단위로 (2판의 뿌리 점검, 3판과 4판의 뿌리)

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

2라운드의 HIGH V2-1 과 MED V1-1 · V1-2 · V2-2 · V1-3 · V2-11 · V2-3 · V2-4 · V2-5 도 뿌리가 하나다. 「멈춤」의 증명이 이미 도는
프로세스 하나와 HTTP 왕복 하나만 가정했다. 증명에 넣지 않은 것은 넷이다. 프로세스가 아직 없는 단계(준비, Popen 직전),
프로세스를 거둘 쪽이 없는 죽음(워커 SIGKILL, 다시 안 뜸, 감독자의 죽음), 요청 하나가 시한보다 오래 걸리는 경우, 멈춘 결과가
어느 길로 보고되는지다. 3판은 아래 R13~R19 를 더하고 §4.1.4 에 단계 × 죽음 표를, §4.1.5 에 증명을 두었다.

| # | 두 단계 | 2판 | 3판에서 묶은 방법 | 결정 · 행 |
|---|---|---|---|---|
| R13 | 감시의 멈춤 결정 ↔ 명령 시작(Popen) | Popen 전 확인이 `requires` 안에만, 확인과 Popen 사이에 막는 phase 보고(F65) | 확인을 requires 와 상관없이 phase 뒤 · Popen 바로 앞(사이에 I/O 없음). 확인과 Popen 사이의 틈(fork 몇 ms)은 펌프의 첫 점검이 곧바로 덮는다(첫 점검을 1초 기다리지 않음) | 333 · RL41 |
| R14 | 워커(또는 서버)의 죽음 ↔ 표식 잡 그룹의 끝 | 다음 워커 프로세스가 시작 때 정리, 다시 안 뜨면 아무도 안 끊음(2판 §9 「감독자가 다시 띄운다」) | 감독자가 `getppid()` 를 0.25초마다 보고 바뀌면 그룹 SIGTERM → `grace_J` → SIGKILL, 시작 때 부모가 기대와 다르면 명령을 띄우지 않음 | 331 · RL43 |
| R15 | 서버가 표식 잡을 닫음 ↔ 프로세스가 죽었다는 확인 | 확인 못 한 고아도 G 에 그룹을 풂(327) | 확인 없이 닫히면 영속 그룹 홀드, 인증된 워커 확인 · admin `rcm release-hold` 로만 풂 | 330 · RL42 · RL44 · RL45 |
| R16 | 연결된 워커의 `ok_at` 나이 ↔ 자기 멈춤 문턱 | 소켓 연산 시한(15초)을 요청 전체 기한으로 봄, 2xx 하나를 잃는 경우를 안 셈(F68) | heartbeat 요청에 전체 기한 `HEARTBEAT_DEADLINE_SECONDS`(15), L = max(T, 2H + 3·T_req + 5) | 335 · RL34 |
| R17 | 로그 커밋 ↔ 세션이 아직 현재 | 커밋 UPDATE 가 잡의 세션 칸만 봄 — 펜스 커밋 뒤에도 같아 step 2 를 지난 요청이 커밋됨(V2-7) | 같은 UPDATE 에 `EXISTS (SELECT 1 FROM worker_sessions WHERE id = S AND fenced_at IS NULL)` | 338 · RL36 |
| R18 | 러너의 SIGTERM→SIGKILL 타이머 ↔ 실제 경과 | 벽시계(F67) | 단조 시계 `mono_fn` | 333 · RL34 |
| R19 | 자기 멈춤 ↔ 그 결과가 보고되는 길 | `should_cancel` 로 걸면 `cancelled`(2), `closed_by_server` 가 일반 `lost` 잡을 죽임(F66) | 자기 멈춤 = `should_stop` 길 + `worker_self_stopped`, W4 의 멈춤 = `should_cancel` 길, `closed_by_server` 는 멈춤을 뜻하지 않음 | 334 · RL1 · RL5 · RL35 |

3라운드의 HIGH(V1-1 · V2-1)도 뿌리가 하나다. 서버가 확인으로 쓴 메시지 하나(T5)가 「지금 상태」를 말했는데 그 메시지의 순서와
신선도를 아무것도 보장하지 않았다. 확인은 한 번 참이 되면 거짓이 될 수 없는 사실을, 그 사실이 선 뒤에 본 쪽이 보낸 것이어야
한다(§4.12 표). 4판은 아래 R20~R23 을 더했다.

| # | 두 단계 | 3판 | 4판에서 묶은 방법 | 결정 · 행 |
|---|---|---|---|---|
| R20 | heartbeat `jobs` 목록 찍기 ↔ claim 응답 받기(`running` 넣기) | 「다음 heartbeat 하나 봐줌」 — heartbeat 이 차례로 간다는 가정(F82) | 워커가 `running[J]` 넣기와 그 claim 의 「끝남」을 같은 `self._lock` 구간에서, heartbeat 는 같은 잠금에서 `seq` · 목록 · `claims_settled` 를 함께 찍는다. 서버는 `seq` 가 받아들인 것보다 크고 `claims_settled ≥ J.claim_seq` 일 때만 「없음」을 센다. 표식 잡은 그래도 홀드 | 339-a · 339-b · RL9 · RL50 |
| R21 | 감독자의 SIGTERM 받기 ↔ 명령 Popen | 시작 확인 뒤 · Popen 전에 온 SIGTERM 은 적히기만 하고 명령에 안 감(V2-2) | Popen 이 돌아온 뒤 SIGTERM 이 적혀 있으면 그룹에 한 번 보낸다 | 339-c (1) · RL51 |
| R22 | 첫 SIGTERM ↔ 남은 멤버의 SIGKILL | 리더 끝부터 새 grace(F88) | 감독자 · 러너 모두 첫 SIGTERM + grace_J 를 SIGKILL 시한으로 | 339-c (2) · RL51 |
| R23 | janitor 의 유예 판정 ↔ 그 세션의 heard 갱신 | 전역 `_heard_lock` 이 판정과 커밋(바쁨이면 5초)을 감쌈 — 모든 heartbeat 가 줄 섬(V2-5) | 세션별 잠금: 그 세션의 heard 갱신과 그 세션 잡 하나의 「다시 보기 + 닫기 커밋」만 묶는다 | 339-g · RL56 |

### 4.2 워커 세션과 펜싱 (결정 255 · 260 · 261 · 265 · 328 · 330 · 336 · 339-a · 339-b · 339-g, M5t-c 343)

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
  7. S 가 처음이면 `BEGIN IMMEDIATE` 하나 안에서 다음을 차례로 한다.
     - `previous_sessions` 가운데 같은 이름인 세션의 busy 잡을 `lost`(`worker_restarted_without_job`)로 닫는다. 확인된
       닫힘이라 홀드가 없다.
     - 그 세션들의 이미 닫힌 잡에 열린 홀드가 있으면 푼다(`release_kind = worker`, `released_by = <토큰 이름>`, 결정 330).
     - 3판: 그 이름의 `worker_session_id IS NULL` 인 레거시 busy 잡을 오늘처럼 `lost`(`worker_restarted_without_job`)로
       닫는다. X(닫는 때의 프리셋 값)면 홀드를 건다(T17, 결정 336).
     - `unverified_sessions` 가운데 같은 이름인 세션 행에 `orphan_unverified = 1` 을 적는다.
     - 그 세션들과 다른 현재 세션을 펜싱한다(`fenced_at`, `fenced_by = S`).
     - S 행을 넣고 워커 행을 upsert 한다.

     커밋한 뒤에는 능력 보고를 지우고(F5 의 오늘 규칙) 호스트 사실을 바꾼다(F54). `previous_sessions` 나
     `unverified_sessions` 에 다른 이름의 세션 id 가 있으면 403 이다(T19). 남의 이름에 걸린 홀드를 풀 수 없게 하려는 것이다.
- 오늘의 등록은 그 이름의 활성 잡을 전부 닫는다(F5). 이 동작을 없앤다. 닫는 것은 `previous_sessions` 로 명시한 세션의
  잡(3판 r1 A-H4)과 세션 NULL 레거시 busy 잡뿐이다. 레거시 잡을 닫는 것은 3판에서 더했다(F69). 2판은 레거시 잡을 프로세스
  없이 `running` 으로 남겨 두었다. 그래서 새 세션의 claim 이 `heard(이름) + G` 까지 `LaneBusy` 409 를 받았다(V1-5 · V2-14).
- spec 을 뺀 모든 `/worker/*` 요청이 세션을 싣는다(§4.10). 서버는 `_heard_lock` 안에서 판정한다. S 가 표에 없으면 409
  `not_registered` 이고 워커는 같은 S 로 재등록한다(T10). 3판: S 행의 `worker_name` 이 토큰 이름과 다르면 403 이고 본문에는
  `error` 만 싣는다(T19). 남의 현재 세션 id 로 `heard` 를 올려 표식 잡이 닫히지 않게 하는 길을 막는다(V1-8). S 가
  펜싱됐으면 409 `session_fenced` 다. S 가 현재 세션이면 `heard(S)` 를 갱신하고 잠금을 놓은 뒤 요청을 처리한다. 세션 없는
  레거시 요청은 그 이름에 레거시 busy 잡이 있을 때만 받고 `heard_legacy(이름)` 을 올린다(결정 336).
- 4판에서는 판정 잠금을 세션별로 나눴다(결정 339-g, V2-5). 3판은 새 잠금 `_heard_lock` 하나가 요청의 세션 판정 · `heard` 갱신과
  janitor 의 유예 판정 · 조건부 닫기를 함께 지켰다(결정 328). 4판의 판정 잠금은 세션마다 하나인 `_session_locks[S]` 이고 레거시
  요청은 `legacy:<이름>` 키를 쓴다. 전역 `_heard_lock` 은 이 사전에 접근할 때만 짧게 쥐는 메모리 잠금이고 그 안에서 DB I/O 를 하지
  않는다.
- 세션별 잠금이 지키는 것은 둘이다. 하나는 요청이 `heard(S)` 와 받아들인 `seq` 를 고치는 일이다. 다른 하나는 janitor 가 S 의 잡
  하나를 닫을 때 `heard(S)` 를 다시 보고 조건부 닫기 트랜잭션(홀드 행 포함)을 커밋하는 일이다. 세션 판정에 쓰는 DB
  읽기(`worker_sessions` 행)는 잠금 밖에서 한다. WAL 이라 읽기는 쓰기를 기다리지 않는다(`store.py:1190`). 로그 요청 전체는 감싸지
  않는다. 로그는 잡별 잠금 `_job_log_locks` 가 맡는다(§4.5).
- `_publish_job`, `[[notify]]` 훅, `_drop_log_partial`, 홀드 뒤의 큐 재계산 알림, 서버 로그 쓰기는 잠금을 놓은 뒤에 한다(3판,
  V2-13). 오늘의 `remote_workers.py:411-417, 1015-1019` 도 닫은 다음에 알리는 같은 순서다. 세션별 잠금을 쥔 채 잡별 로그 잠금을
  잡지 않고 그 반대도 하지 않으므로 교착이 없다. `_remote_lock` 과 `_fresh_claims_lock`(F57)은 하던 일을 그대로 한다.
- 4판에서 버린 안은 셋이다.
  - 전역 잠금 하나가 판정과 커밋을 감싸는 안. 3판의 꼴이고 바쁜 DB 에서 모든 세션의 heartbeat 가 5초씩 줄을 선다.
  - 잠금 없이 판정한 뒤 커밋하는 안. 판정과 커밋 사이에 온 heartbeat 를 무시하고 닫는다. 홀드는 생기지만 연결된 세션의 잡을 닫는
    일이 는다.
  - 잡 단위 잠금. `heard` 는 세션 값이라 같은 세션 잡 여럿의 판정이 서로 다른 잠금을 쥐게 된다. 세션 단위가 같은 일을 더 적은
    잠금으로 한다.
- 펜싱된 세션에 허용하는 요청은 하나뿐이다. finish 본문이 `outcome = lost` 와 `summary_code = worker_replaced` 이고 잡의
  세션이 S 인 경우다(좁은 확인, 결정 261). 잡이 busy 면 조건부 UPDATE 로 닫고 200 을 준다. 3판: 본문의 `group_gone` 이 참이
  아니면 같은 트랜잭션에서 홀드를 건다(결정 330). 잡이 이미 terminal 이고 열린 홀드가 있으면 `group_gone: true` 일 때만
  홀드를 풀고 200 `{hold_released: true}` 를 준다. 그렇지 않으면 409 `job_closed` 이고 아무것도 반영하지 않는다. 그 밖의
  요청은 모두 409 `session_fenced` 이고 아무것도 반영하지 않는다(RL11). 잡이 이미 `lost` 가 된 뒤 펜싱된 세션이 보낸 늦은
  finish 도 409 `session_fenced` 이고 `late_result` 로도 남기지 않는다. 펜싱된 세션은 권한을 잃었고 새 세션과 겹쳐 돌았을
  수 있기 때문이다(2차 Q-B).
- 펜싱된 세션의 잡은 확인이 오거나 `heard(S) + G_J` 가 지날 때까지 busy 로 남아 그룹과 레인을 잡는다(F11). 펜싱 시각부터
  유예를 다시 세지 않는다. 옛 프로세스가 펜싱 전부터 조용했다면 그만큼 빨리 풀린다.
- 새 세션의 claim 이 펜싱된 세션의 잡이 잡고 있는 레인에 오면 `LaneBusy` 를 409 대신 204(잡 없음)로 답한다. 409 가 쏟아지지
  않게 하려는 것이다(F10). 자기 세션의 잡과 겹치면 오늘처럼 409 다.
- 세션 행은 `forget_workers`(F21)가 워커 행을 지울 때 그 이름의 것을 함께 지운다. 그 밖에는 지우지 않는다. 빨리 지우면 늦게
  돌아온 펜싱된 프로세스가 `not_registered` 를 받고 재등록해 살아 있는 세션을 펜싱하는 핑퐁이 생긴다.
- 「잊힌 잡」(F6)과 heartbeat 의 `cancel` 목록은 세션 단위, 곧 그 세션의 busy 잡으로 센다. heartbeat 응답에는
  `closed_jobs` 를 더한다(결정 321). 요청의 `jobs` 목록에 있는 잡 가운데 서버에서 이미 terminal 이고 잡의 세션이 S 인 것마다
  `{id, state, had_cancel}` 을 싣는다. `had_cancel` 은 잡 행에 `cancel_requested_at` 이 있다는 뜻이다(F61).
- 4판에서는 「잊힌 잡」을 순서에 기대지 않고 판정한다(결정 339-a · 339-b, HIGH).
  - 워커는 claim 요청 본문에 `claim_seq` 를 싣는다. 레인 스레드는 claim 을 보내기 전에 `self._lock` 안에서 번호를 받아 「열린
    claim」에 넣는다. 돌아오면 같은 잠금 구간에서, 잡을 받았으면 `self.running[J] = lane` 을 넣고 그 번호를 닫는다. 응답, 204,
    오류, 시한 어느 경우든 번호를 닫는다(`finally`). heartbeat 스레드는 `self._lock` 안에서 `seq` 를 받고 `jobs =
    sorted(self.running)` 과 `claims_settled` 를 함께 찍는다.
  - 서버는 heartbeat 의 `seq` 가 S 의 받아들인 seq 보다 크면 받아들여 값을 고치고 그 목록으로 「없음」을 판정한다. 작거나 같으면
    `heard` 만 올리고 목록과 판정은 버린다. 응답은 2xx 이고 `closed_jobs` 는 그 목록으로 그대로 만든다. 닫힌 상태는 되돌아오지
    않으므로 늦게 와도 맞다. 「없음」은 `J.claim_seq ≤ claims_settled` 인 J 에만 센다.
  - 서버가 재시작하면 받아들인 seq 가 비므로 처음 온 heartbeat 를 받아들인다. 그것이 늦게 온 옛 heartbeat 여도 `claims_settled`
    규칙이 「없음」의 참을 지킨다. 그 claim 이 워커에서 끝난 뒤 찍은 목록에 J 가 없으면 워커는 J 를 받지 않았고 앞으로도 받을 수
    없다(F82).
  - `_fresh_claims` 의 「봐주기」(`remote_workers.py:565, 935`)는 세션 없는 레거시 요청에만 남긴다.
  - 워커는 응답의 `cancel` 과 `paused` 를 그 요청의 `seq` 가 이미 적용한 seq 보다 클 때만 적용한다. 늦게 온 도우미 응답이 새
    취소를 지우지 않게 하려는 것이다. `closed_jobs` 는 늘 적용한다.
  - 「없음」 닫기(T5)는 X_J 면 홀드를 건다(결정 339-b). 홀드는 §4.12 표의 확인으로만 풀린다. 살아 있는 워커가 그 잡을 정말 못
    받았으면 그 세션은 J 의 finish 를 보내지 않는다. 그래서 그 홀드는 다음 프로세스의 `previous_sessions` 나 admin 의 `rcm
    release-hold` 로 풀린다(§9).
  - 버린 안은 셋이다. 순번만 두는 안은 claim 전에 찍혀 늦게 출발한 목록을 거르지 못한다. 버려진 도우미 넷이 한꺼번에 떠 있을 수
    있다. 「워커가 아는 마지막 claim 잡 id」를 싣는 안은 claim 응답을 잃은 경우, 곧 T5 자체에서 워커가 그 잡 id 를 모른다. 그러면
    레인이 영영 busy 로 남는다. `seq` 를 DB 에 두는 안은 필요 없다. 늦은 목록의 참은 `claims_settled` 가 지키므로 메모리로
    충분하다.
- 같은 토큰으로 같은 순간에 두 프로세스가 등록해도 살아남는 세션은 정확히 하나다(RL11). 등록이 직렬로 처리되므로 나중
  등록이 이긴다. 먼저 등록한 쪽은 다음 요청에서 409 `session_fenced` 를 받고 종료 0 으로 끝난다.
- 4판(M5t-c 결정 343, 교차 명세 의존): `session_fenced`, `protocol_required`, `not_registered`, `job_closed`, `log_gap` 을 포함한
  M5t-b 의 모든 오류는 `ApiError` 로 올리고 `_send_error`(F91)가 응답을 쓴다. 따로 `send_response` 하는 길을 만들지 않는다. 그래야
  M5t-c PR1 이 그 자리에서 짝지은 토큰의 `session_fenced` 와 `protocol_required` 에 `pair_reject_mac` 을 붙일 수 있다. M5t-b 는 그
  키를 만들지도 읽지도 않는다. 워커 쪽 검증은 M5t-c 의 `verify_pair_reject` 가 한다. PR 순서는 M5t-b PR1 이 먼저이고 M5t-c PR1 이
  그 409 들을 MAC 을 다는 거절 목록에 더한다(§9).
- 거절한 대안:
  - 세션을 `workers` 행의 칸으로 두기. 이름당 한 행이라 판정이 다시 이름 단위가 된다(3판 r2 근본 원인).
  - 세션 표 없이 메모리에만 두기. 서버가 재시작하면 누가 현재 세션인지 잊는다.
  - 따로 `lease_id` 두기. 잡은 한 번만 claim 되므로 (J, S) 로 충분하다. 이름이 늘면 3판 R1 같은 충돌이 는다.
  - 로그 요청 전체를 `_heard_lock` 하나로 감싸기. 4 MiB 본문을 쓰는 동안 모든 워커의 heartbeat 가 줄을 선다.

### 4.3 끊김 유예와 자기 멈춤 (결정 253 · 256 · 257 · 263 · 266 · 320 · 321 · 322 · 330 · 333 · 334 · 335)

서버 쪽은 다음과 같다.

- 판정은 janitor 가 5초마다 하고 `mark_lost_workers` 를 대신한다. 4판에서는 판정과 닫기를 세션별 잠금으로 나눴다(결정 339-g).
  janitor 는 busy 원격 잡을 세션별로 모으고 메모리의 `heard` 를 짧게 읽어 `gap(J) > G_J` 인 후보를 고른다. 후보마다 그 세션의 잠금
  안에서 `heard(S)` 를 다시 보고, 아직 넘었으면 조건부 닫기 트랜잭션을 커밋한다. 닫기는 `only_from = BUSY_STATES` 와
  `worker_session_id = S` 를 조건으로 건 UPDATE 다(RL4). 요약 코드는 `worker_unreachable` 이고 인자는 `name` 과 `seconds =
  int(gap)` 이다. 세션 행이 `orphan_unverified = 1` 이고 X_J 이면 요약 코드는 `orphan_unverified` 다(결정 327). X_J 면 같은
  트랜잭션에서 그룹 홀드 행을 넣는다(결정 330). 레거시 잡(세션 NULL)은 `heard_legacy(이름)` 을 기준으로 판정한다(T11).
- 4판: 한 틱에 닫는 잡은 많아야 `GRACE_CLOSES_PER_TICK`(20)개다. 늦게 닫는 것은 안전한 쪽이라 이 값은 판단이고 재지 않았다. 정상
  닫기는 짧은 트랜잭션 하나라 20개는 한 틱(5초) 안에 끝나고, 서버 재시작 뒤 한꺼번에 넘는 경우에도 다른 janitor 일을 막지 않는다.
  닫기가 `database_busy` 면 이 틱의 닫기를 멈추고 다음 틱에 다시 한다.
- 단조 시계를 쓰면 서버 벽시계가 앞으로 뛰어도 heartbeat 가 오던 잡을 닫지 않는다(RL27 a). 서버 재시작은 메모리를 비우므로
  바닥이 기동 시각이 된다(RL7 b, 2차 Q-C). 닫힘이 느려질 뿐 두 번 돌지는 않는다. 서버가 잠들면 단조 시계가 덜 가서 늦게
  닫는데 이것도 안전한 쪽이다.
- 거절한 대안은 둘이다. 닫는 트랜잭션에서 벽시계 `last_seen_at` 을 다시 읽는 안(3판 r1 A-M4)은 시계 점프를 막지 못한다.
  벽시계에 기동 바닥만 더하는 안도 점프를 막지 못한다.
- 레인 down(T)은 그대로 벽시계 `workers.last_seen_at`(F1)으로 정한다. 유예와 레인 표시는 다른 질문에 답한다. 레인은 지금
  일을 받는지를 말하고 유예는 잡을 버릴지를 정한다.
- 취소 미확인 닫기(F7)는 그 잡의 세션이 현재 세션이고 들릴 때만 하며 판정은 `_heard_lock` 안에서 한다(RL33). 3판: X_J 면 그
  닫힘에 홀드를 건다(결정 330). 들리는 워커가 취소를 확인하지 않았다면 프로세스가 아직 돌 수 있기 때문이다. 조용하거나
  펜싱된 세션의 cancelling 잡은 G_J 뒤 `lost`(`worker_unreachable`)가 된다. 부모 명세 3판의 새 코드 `cancel_unreachable` 은
  만들지 않는다(결정 278).
- 닫힌 잡은 heartbeat 2xx 응답의 `closed_jobs`(§4.2)로 알린다(결정 321 · 322). 로그와 phase 가 없어 409 를 받을 일이 없는
  조용한 잡도 다음 heartbeat 에서 알게 된다. 취소 요청이 있던 잡이 유예로 `lost` 가 되면 `had_cancel: true` 가 실리므로
  다시 붙은 워커가 그 잡을 멈춘다(V1-4, 1판 RL8 b 를 고침).

얼림(결정 257):

- claim 트랜잭션 안에서 `jobs.irreversible` 에는 그 프리셋이 표식 프리셋 집합에 드는지를, `jobs.disconnect_grace_seconds`
  에는 서버 설정값을 적는다. claim 응답의 `job` 안에 둘 다 싣고 최상위 키는 늘리지 않는다(F58 의 claim 키 잠금). 워커는
  응답의 값만 쓴다. 3판: 로컬 레인 claim 도 `irreversible` 을 얼린다. 감독자와 재기동 홀드에 쓰기 때문이다(F76 · 결정 330 ·
  331). 로컬 잡의 `disconnect_grace_seconds` 는 유예가 없으므로 NULL 그대로다.
- 레거시 잡(세션 NULL, `irreversible` NULL)의 X 는 닫는 때의 프리셋 값이다(T11 · T17). 얼린 값이 없을 때는 이쪽이 안전하다.
- `store.claim` 은 프리셋을 고른 뒤에야 그 프리셋을 안다. 그래서 호출자가 두 가지를 넘기고 store 가 같은 트랜잭션에서
  적는다. 하나는 「표식 프리셋 이름에서 유효 그룹과 범위로 가는 표」다(3판, 결정 330, 그룹 NULL 인 옛 대기 잡용). 다른
  하나는 기본 G 다.
- 4판(결정 339-a): 레인의 claim 요청은 `claim_seq` 를 싣는다(§4.2). 서버는 claim 트랜잭션에서 잡 행에 `claim_seq` 를 적는다.
  세션이 있는 claim 에 `claim_seq` 가 없거나 1 이상의 정수가 아니면 400 이고 본문에는 `error` 만 싣는다.

설정 검증(결정 263 고침 · 320):

- `disconnect_grace_seconds` 는 정수이고 `L + DISCONNECT_GRACE_MARGIN_SECONDS` 이상, 86400 이하여야 한다. 3판:
  `L = max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 3 × 15 + 5)` 다(결정 335). 2판의 `2H + 15` 는 2xx 하나를
  잃는 경우와 요청 전체 길이를 세지 않았다(F68). `DISCONNECT_GRACE_MARGIN_SECONDS = 35` 는 기본 워커 grace 가 10 일 때의
  m(15 + 10 + 10)이다. 기본 설정(T 60 · H 5)에서 하한은 95 로 2판과 같다. 어기면 기동 실패(종료 2)이고 키 이름과 하한 값을
  말하는 문장을 낸다. 1판의 「`worker_timeout_seconds` 보다 큼」을 이 규칙이 대신한다(V2-6).
- 3판 기본값(V1-9): 키를 적지 않았으면 값은 `max(600, L + 35)` 다. `worker_timeout_seconds` 에는 상한이 없어서(F27) 큰 값을
  쓰던 설정이 업그레이드 뒤 기동에 실패할 수 있기 때문이다. 적은 값이 하한 아래면 기동 실패 문장이 하한 값과 「키를 지우면
  그 값을 쓴다」를 함께 말한다.
- config 의 `DISCONNECT_GRACE_MARGIN_SECONDS` 와 워커의 `T_req + 10 + SELF_STOP_SLACK_SECONDS` 가 같다는 것을 시험이
  잠근다. 두 상수가 따로 움직이지 않게 하려는 것이다.

워커 자기 멈춤(결정 320, 1판 267 을 대신):

- 표식 잡만 멈춘다. heartbeat 와 따로 도는 새 감시 스레드 `rcm-worker-watchdog` 가 `WATCHDOG_TICK_SECONDS`(1초)마다 claim 한
  표식 잡마다 `wgap ≥ G_J − m_J` 인지 본다. claim 응답을 받은 순간부터 보며 준비 단계도 포함한다. 넘으면 그 잡의 관찰자
  `self_stop`(새 `threading.Event`)을 세운다.
- 3판에서는 멈춤의 길을 이름으로 정했다(결정 334, F66 · V1-2 · V2-2). 관찰자 `should_stop()` 은 `proc.stopping` 이나
  `self_stop` 이 서면 참이다. `should_cancel()` 은 `job_id ∈ cancel_requested` 이거나 `closed_stop`(W4 표가 멈춤으로 정한
  잡)이면 참이다. `closed_by_server`(로그 409)는 더는 `should_cancel` 을 세우지 않는다. 409 `job_closed` 는 W4 표로 가고 일반
  `lost` 잡은 그 409 로 죽지 않는다. 러너의 `lost`(곧 `should_stop`)는 워커가 `self_stop` 이면 `lost` +
  `worker_self_stopped`(`seconds` = int(wgap))로, 아니면 오늘의 `STOP_SUMMARY` 로 보고한다. 실행 뒤, requires 실패 뒤, 준비
  실패 뒤 세 곳이 모두 그렇다(`remote_worker.py:960-961, 996-997, 1023-1024`). 플래그는 한 방향이라 되돌리지 않는다.
- 3판에서는 Popen 직전에 한 번 더 확인한다(결정 333, F65 · V2-1 HIGH). 러너는 requires 와 상관없이 phase `executing` 보고
  뒤, Popen 바로 앞에서 `should_stop()` 과 `should_cancel()` 을 본다. 참이면 프로세스 없이 `RunResult(lost=…/cancelled=…)` 를
  돌려준다. 오늘 requires 안의 꼴 그대로다. 확인과 Popen 사이에는 네트워크가 없고 로컬 파일 `proc.json` 에 `{pgid: null}` 을
  한 번 표시할 뿐이다(§4.4). 관찰자 규약에는 `spawning()`(표시)과 `spawned(pid)`(2판) 두 갈래가 있고 로컬 관찰자는 아무것도
  하지 않는다. 펌프의 첫 점검은 Popen 직후 곧바로 한다. 오늘은 `last_check = started` 라 1초 뒤에 한다(`runner.py:427`).
- 3판에서는 러너에 단조 시계를 준다(결정 333, F67 · V2-3). `run_job(…, mono_fn=time.monotonic)` 이다. 점검 박자와 SIGTERM
  에서 SIGKILL 까지의 타이머는 `mono_fn` 으로 재고 `started`, `finished`, 잡 시한은 오늘대로 `now_fn` 으로 잰다. 잡 시한까지
  단조 시계로 바꾸면 macOS 잠자기가 시한에서 빠진다. 오늘 계약을 바꾸는 일이라 하지 않는다.
- 3판에서 표식 잡은 감독자 아래에서 돈다(결정 331, §4.13). 러너는 `spec.irreversible` 이면 argv 를 감독자로 감싸 Popen
  하고(같은 `start_new_session=True`), 리더인 감독자가 끝나면 남은 그룹 멤버를 거두고 `group_gone` 을 결과에 싣는다.
- `ok_at` 은 감시 스레드, 보내는 스레드, heartbeat 스레드, 레인이 함께 쓰는 값이다. 2xx 를 받은 요청을 보낸 시각의
  최댓값이고 벽시계와 단조 시계로 둘 다 적는다. 처음 값은 등록 2xx 다. 잡 J 의 첫 값은 J 를 돌려준 claim 요청을 보낸
  시각이다. 이 값은 D-a 가 말한 「claim 응답 시각」보다 이르거나 같다(§12 V2-6 행). 죽은 세션 D 의 큐 요청은 새 세션의
  `ok_at` 을 올리지 않는다(V2-8).
- 3판에서는 heartbeat 에 전체 기한을 둔다(결정 335, F68 · V1-3 · V2-11). heartbeat 스레드는 요청을 도우미 스레드에서 보내고
  `HEARTBEAT_DEADLINE_SECONDS`(15)까지만 기다린다. 넘으면 그 회차를 실패로 세고 H 뒤 다음 회차로 간다. 남은 도우미 스레드는
  소켓 시한으로 끝난다. 그 도우미가 늦게 2xx 를 받으면 그 요청을 보낸 시각으로 `ok_at` 을 올리며 이 값은 늘 `heard`
  이하다. 동시에 매달린 도우미는 많아야 `HEARTBEAT_INFLIGHT_MAX`(4)개다. 넘으면 그 회차를 보내지 않고 실패로 세며 워커
  로그에 속도를 제한해 남긴다. 조금씩 흘리는 서버만 있는 경우인데 결과는 자기 멈춤 `lost`(3)이고 두 번 돌지 않는 쪽이다.
  다른 요청(claim, 로그, finish)은 오늘의 소켓 시한을 그대로 쓴다. L 의 계산은 heartbeat 만 쓴다.
- 표식 잡의 `grace_J` 에는 상한을 둔다. `grace_J = max(1, min(워커 grace_seconds, G_J − L − 15 − SELF_STOP_SLACK_SECONDS))`
  다. 설정 검증이 `G_J ≥ L + 35` 를 보장하므로 기본 grace 10 은 줄지 않는다. 줄였으면 워커 로그에 「grace_seconds N 을 잡 #J
  에서 M 으로 줄임 — disconnect_grace_seconds 에 맞춤」 한 줄을 남긴다. L 은 register 응답의 `heartbeat_seconds` 와
  `worker_timeout_seconds`, heartbeat 응답의 `timeout_seconds`(F62)로 계산한다.
- 서버가 이미 닫은 표식 잡은 감시가 늦더라도 다음 2xx 의 `closed_jobs` 나 보고에 돌아온 409 `job_closed` 를 받고 멈춘다(W4).
- `wgap` 은 벽시계와 단조 시계 가운데 큰 쪽이다. 단조 시계가 잠든 시간을 세지 않는 OS 가 있고 벽시계는 뒤로 갈 수 있다. 그래도
  둘 중 하나는 실제 경과 이상이다. 잠에서 깨면 감시 스레드의 첫 틱이 판정하므로 heartbeat 를 다시 보내기 전이든 뒤든
  멈춘다(RL26).
- 3판에서 거절한 대안:
  - 자기 멈춤을 `should_cancel` 로 걸기. 종료 2 `cancelled` 로 끝난다(F66).
  - Popen 전 확인을 requires 안에만 두기(F65).
  - L 만 늘리고 요청 기한은 두지 않기. 조금씩 흘리는 응답에는 상한이 없다.
  - 모든 워커 요청에 전체 기한 두기. 로그 4 MiB 요청과 300초짜리 산출물 요청을 잘라 재전송만 늘린다. L 은 heartbeat 만
    본다.
  - 잡 시한도 단조 시계로 재기. 잠든 시간이 시한에서 빠진다.
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
  `worker_self_stopped` 이고 확인된 닫힘이라 홀드가 없다. 닿지 않으면 서버의 `worker_unreachable` 이 먼저 닫으며 홀드를
  건다. 뒤에 온 보고는 `late_result` 가 되고 `group_gone: true` 면 그 홀드를 푼다(결정 330).
- 비대칭 위험이 둘 있다. 워커 벽시계가 앞으로 `G_J − m_J` 이상 뛰면 표식 잡이 일찍 멈춘다. 서버가 heartbeat 에 5xx 를
  `G_J − m_J` 동안 계속 내면 서버는 요청을 받으므로 닫지 않지만 표식 잡은 멈춘다. 둘 다 결과는 `lost`(3)이고 두 번 돌지
  않는 쪽이다(§9).

### 4.4 재시작 순서 · 이어받기 · 고아 정리 (결정 268 · 325 · 326 · 327 · 330 · 331 · 333 · 337)

워커 데이터 폴더에 `<data>/worker-sessions/<S>/` 를 새로 둔다. 이름에 `sessions` 만 쓰지 않은 것은 문서에서 그 낱말이
「요청 세션」을 뜻하기 때문이다(grep `sessions` 27건). 폴더에 두는 파일은 다음과 같다.

- `lock`: 프로세스가 살아 있는 동안 `fcntl.flock(LOCK_EX)` 로 잡는다(F43). PR1 이 만든다.
- `owner.json`: `{server_sha256, token_name}` 이다(결정 325, D-e). `server_sha256` 은 워커 설정에 적힌 `server` 값에서 끝의
  `/` 를 떼고 `server_name` 과 `\n` 으로 이은 문자열의 sha256 hex 다. `auto` 면 해석한 주소가 아니라 `auto` 와 광고 이름을
  그대로 쓴다. 주소가 바뀌어도 주인이 같게 하려는 것이다. `token_name` 은 `GET /api/whoami` 의 `name` 이다(F64). 토큰 값은
  적지 않는다. 임시 파일에 쓴 뒤 rename 한다. PR1 이 만든다.
- `ok_at.json`: `{wall}`(ISO)이다. 2xx 를 받은 뒤 H 마다 많아야 한 번 덮어쓴다. D 를 다시 확인할 때 쓴다. PR1 이 만든다.
- `jobs/<id>/claim.json`: `{irreversible, disconnect_grace_seconds}` 이고 claim 직후에 쓴다. PR1 이 만든다.
- `jobs/<id>/proc.json`: 3판에서는 두 번 쓴다(결정 333, R10 을 고침). Popen 직전 확인을 지나면 관찰자 `spawning()` 이
  「띄우는 중」을 뜻하는 `{pgid: null, start: null}` 을 쓴다. Popen 뒤에는 `spawned(pid)` 가 `{pgid, start}` 를 쓴다(F45). 둘
  다 임시 파일에 쓴 뒤 rename 한다. 표식 잡의 `pgid` 는 감독자의 pid 다. 감독자가 그룹 리더다(§4.13). `start` 는
  Linux 에서 `"linux:<starttime>"` 이고 `/proc/<pid>/stat` 의 22번째 칸을 마지막 `)` 뒤에서 센다(F59). 그 밖의 OS 에서는
  `"ps:<lstart>"` 다. 3판: `ps` 는 환경 `LC_ALL=C TZ=UTC` 로 부르고 앞뒤 공백을 지운 값이 F71 의 정규식에 맞을 때만 쓴다
  (V1-7). 읽지 못하거나 형식이 다르면 `null` 이다. PR1 이 만든다.
- `jobs/<id>/stream.log`: 서버로 가는 스트림 바이트다(결정 271). PR2 가 만든다.
- `jobs/<id>/outbox.json`: 보낼 phase(마지막 하나), `acked`, finish 기록, `done` 을 담는다. `acked` 는 서버가 2xx 로 확인한
  오프셋이고 하한이다. finish 기록은 `outcome · exit_code · summary · summary_code · summary_args · finished_at · ended_at ·
  log_bytes · bundle` 이다. PR2 가 만든다.

시작 순서는 다음과 같다(RL14 · RL15 · RL12 b · RL25 · RL38). V2-12 에 따라 폴더는 검사 뒤에 만든다.

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
   - 3판: `proc.json` 이 없으면 Popen 직전 확인 전에 죽은 것이다. 「띄운 적 없음」이고 「확인됨」이다(결정 333). 2판은
     `claim.json` 만 있어도 「모름」으로 두어서 긴 git fetch 도중의 kill -9 가 매번 경고와 홀드로 이어졌다.
   - 3판: `proc.json` 의 `pgid` 가 null 이면 띄우는 순간 죽었을 수 있다. 「모름」이다.
   - `proc.json` 이 있고 리더 pid 가 살아 있으며 `start` 가 같고 두 값이 모두 형식에 맞으면 같은 그룹이다. SIGTERM 을 보내고
     워커 `grace_seconds` 를 기다린 뒤 SIGKILL 하고 워커 로그에 한 줄을 남긴다. 3판: 그 뒤 2초 안에 `killpg(pgid, 0)` 이
     ESRCH 면 「확인됨」이고 아니면 「모름」이다. D 상태처럼 죽지 않는 프로세스가 있기 때문이다.
   - 리더 pid 가 없고 `killpg(pgid, 0)` 도 실패하면 그룹이 없다. 「확인됨」이다.
   - 리더 pid 는 살아 있는데 `start` 가 다르면 pid 가 재사용된 것이다. 이 판정은 두 값이 모두 형식에 맞을 때만 한다(3판,
     V1-7). POSIX 는 살아 있는 그룹의 id 를 새 프로세스에 주지 않으므로 원래 그룹은 없다. 손대지 않고 「확인됨」으로 둔다.
     한쪽이라도 형식이 다르거나 null 이면 「모름」이다.
   - 다음은 「모름」이다. 리더 pid 가 없는데 `killpg(pgid, 0)` 이 성공하면 리더만 죽고 멤버가 살아 있는 것이다(F59). 감독자만
     죽은 W13 도 이 꼴이다. `start` 를 못 읽은 경우(`ps` 가 없거나 실패했거나 `/proc` 를 못 읽음)와 형식이 다른 경우도
     「모름」이다. 「모름」이면 신호를 보내지 않고 경고 한 줄을 남긴다(RL31).
7. D 의 보고 큐를 D 의 세션 id 로 보낸다. 규칙은 §4.5 와 같고 PR2 가 만든다. 응답은 그 큐에만 적용한다(아래 표, 결정 326).
   이 요청들이 받은 2xx 는 새 세션의 `ok_at` 을 올리지 않는다(V2-8). 연결 실패나 5xx 면 백오프로 다시 한다. 등록은 D 의
   큐가 끝날 때까지 기다린다. 등록이 D 를 펜싱하면 D 의 finish 가 2차 Q-B 에 따라 버려지기 때문이다. 로그와 산출물 단계의
   5xx 는 `FINISH_HOLD_SECONDS` 규칙(결정 324)을 따른다. 3판에서는 이 기다림에 상한을 둔다(결정 337, V2-9). 2판은 24시간까지
   기다려서 5xx 가 이어지는 항목 하나가 이 머신의 모든 claim 을 하루 동안 막았다. 서버가 응답하는(HTTP 상태가 오는) 시간으로
   `DEAD_SESSION_WAIT_SECONDS`(300)가 지나면 남은 D 큐를 디스크에 둔 채 등록한다. 신호는 워커 로그 한 줄이다.

   ```text
   dead session <앞 8자> queue not delivered after 300s of server errors — registering; job #N will close as restarted-without-job
   ```

   그 잡은 등록이 `lost`(`worker_restarted_without_job`)로 닫는다. D 의 고아가 「확인됨」이면 확인된 닫힘이라 홀드가 없다.
   연결 실패 시간은 세지 않는다. 그동안은 등록도 할 수 없다. 300 의 근거는 결정 324 의 `FINISH_HOLD_SECONDS` 와 같다. 백오프
   상한 30초로 열 번 넘게 다시 해 볼 수 있고 기본 유예의 절반이다.
8. 등록한다. `previous_sessions` 에는 6 에서 잡이 모두 「확인됨」인 D 를, `unverified_sessions` 에는 「모름」 잡이 하나라도
   있는 D 를 넣는다. 서버는 앞의 것의 busy 잡을 `worker_restarted_without_job` 으로 닫고 둘 다 펜싱한다(§4.2). 「모름」 D 의
   잡은 서버가 T8 처럼 `heard(D) + G_J` 까지 잡아 둔다.
9. 「모름」 잡이 있는 D 마다 재확인 타이머를 건다. 시각은 `D.ok_at + G_J − m_J` 다. `D.ok_at` 은 D 의 `ok_at.json` 에서
   읽는다. 파일이 없으면 D 폴더 `lock` 의 mtime 을 쓴다. G_J 와 X_J 는 `claim.json` 에서 읽는다. 그 시각에 6 의 판정을 다시 한다.
   「확인됨」이 되면 D 의 id 로 좁은 확인 finish(`lost` + `worker_replaced` + `group_gone: true`)를 보내 그룹 자리를 일찍
   푼다. D 는 8 에서 펜싱됐으므로 이것이 D 에 허용되는 단 하나의 요청이다(§4.2). 잡이 이미 닫혀 홀드가 있으면 이 finish 가 그
   홀드를 푼다(결정 330). 그래도 「모름」이면 아무것도 하지 않는다. 그러면 서버가 유예 만료에 X_J 인 잡을 `orphan_unverified`
   로 닫고 그룹 홀드를 건다. 결정 327 은 3판에서 경고만 하던 것을 그룹을 잡아 두는 것으로 바꿨다(결정 330). 그 뒤에 다시
   확인하는 타이머는 두지 않는다. 사람이 그 머신을 보고 `rcm release-hold` 한다(330 의 「영속」). 그 D 를 다음에 다시 시작한
   워커가 「확인됨」으로 보면 `previous_sessions` 가 홀드를 푼다(T9). 재확인 시각이 이미 지났으면 바로 한 번 한다.
10. D 폴더는 7일 청소에 맡긴다. 이미 보낸 것에는 `done` 이 적혀 있다.

죽은 세션 D 의 큐를 D 의 id 로 보낼 때의 응답은 아래 표와 같다(결정 326, D-f). 살아 있는 이 프로세스의 W5 · T10 과 다르다.

| 응답 | 그 큐 항목 | 이 프로세스 | 이유 |
|---|---|---|---|
| 2xx | 오늘처럼(acked · 다음 단계 · `done`) | 계속 | — |
| 409 `job_closed` | 그 잡의 남은 항목 지움(`late_result` 여부는 서버) | 계속 | 서버가 이미 닫았다 |
| 409 `log_gap` | `expected_offset` 부터 다시 | 계속 | §4.5 와 같다 |
| 409 `session_fenced` | 그 잡의 로그 · phase · 산출물 지움. finish 는 잡의 고아 판정이 「확인됨」이면 좁은 확인 finish(`lost` + `worker_replaced` + `group_gone: true`)로 바꿔 한 번 보냄, 「모름」이면 보내지 않음(재확인 9 에 맡김). 원래 결과는 버려진다(2차 Q-B) | 계속. **W5 를 부르지 않는다**(종료 0 하지 않음) | D 는 다른 살아 있는 프로세스가 이미 펜싱했다 — 이 프로세스는 펜싱되지 않았다 |
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
- 3판에서 거절한 대안:
  - `ps -o lstart=` 를 서비스 환경 그대로 읽기. TZ 나 로캘이 다르면 다른 문자열이 나와 「재사용, 확인됨」으로 가고 모름이
    확인으로 바뀐다(F71).
  - `ps -o etime=`(경과 초)로 신원 보기. 두 번 읽는 사이의 시간 때문에 값이 흔들려 같은 프로세스를 다르다고 본다.
  - 등록이 D 의 큐를 끝없이 기다리기(V2-9).
  - `claim.json` 만 있으면 「모름」으로 두기(2판). 긴 준비 중의 kill -9 가 매번 홀드가 된다.
- 거절한 대안:
  - 등록 본문의 「버린 lease 목록」만 믿기(부모 명세 3판). 그 목록을 만든 프로세스가 죽었는지 서버는 모른다. 잠금이
    증거다.
  - 고아를 pgid 만 보고 죽이기. pid 가 재사용됐으면 남의 프로세스를 죽이고 이것은 되돌릴 수 없다.
  - 데이터 폴더 전체를 잠그기. 같은 폴더를 쓰는 두 번째 프로세스를 막으면 결정 255 의 「새 세션이 옛 세션을 펜싱」을 할
    수 없다.
  - 리더 없는 그룹을 멤버로 알아보기. 멤버의 시작 시각을 기록하지 않았다. 「모름」으로 두고 다시 확인하며 경고한다.
  - 서버 URL 을 해석한 주소로 해시하기. `auto` 가 주소를 바꾸면 자기 폴더를 남의 것으로 본다.
  - D 의 큐에 401 · 403 이 오면 지우기(1판). 진짜 결과를 지운다(V1-5).

### 4.5 로그 오프셋과 순서 있는 보고 큐 (결정 264 · 271 · 272 · 273 · 323 · 324 · 329 · 337 · 338)

워커 쪽은 다음과 같다.

- 단위는 워커가 서버로 보내는 스트림의 바이트이고 가리기 전의 값이다(F14). 워커의 `stream.log` 길이가 곧 그 잡의 스트림
  길이다.
- 워커 관찰자 `output(data)` 는 `stream.log` 에 덧붙이고 펌프를 막지 않는다. 쓰기에 실패하면 그 잡을 「스풀 깨짐」으로
  표시하고 그 뒤 바이트는 세기만 한다(§4.6 `log_bytes`). 네트워크는 보내는 스레드가 맡는다. 3판에서는 이 스레드를 잡마다
  하나 둔다(결정 337, V1-10). 막는 `urlopen` 하나가 300초짜리 산출물 PUT 에 묶이면, 워커당 하나인 스레드는 그동안 다른 잡의
  로그와 finish 를 막는다(F55). 잡의 스레드는 claim 뒤에 시작해 그 잡의 큐가 끝나면 끝나므로 많아야 `lanes` 개다. 죽은 세션
  D 의 잡에도 잡마다 하나씩 두고 등록 전에 돈다(§4.4 의 7). 백오프 상태도 잡마다 따로 둔다.
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

PR 경계는 3판에서 다시 정했다(V1-4). 2판은 PR1 을 「세션 헤더만」으로 적고 이 절차의 1 은 「세션이 있는데 오프셋이 없으면
400」으로 적었다. 그래서 PR1 워커의 로그 POST 가 모두 400 을 받았다. PR1 의 로그 라우트는 세션 헤더만 검사한다. 형식 400,
세션 판정(T19), 409 가 그것이고 `X-RCM-Log-Offset` 은 읽지 않는다. 덧붙이기는 오늘 길(`remote_workers.py:639-668`)을 그대로
쓴다. 아래 1~9 전부와 「세션이 있는데 오프셋이 없으면 400」은 PR2 가 켠다. PR1 과 PR2 는 같은 릴리스로만 나간다. dev 에서는
같은 트리의 서버와 워커만 만나기 때문이다(§5).

1. 헤더를 검사한다. `X-RCM-Worker-Session` 이 32자 소문자 hex 가 아니면 400 이다. 400 응답은 `{"error"}` 꼴이고 `code` 가
   없다(F58 `tests/test_worker_api.py:618`). 이 검사는 PR1 이 한다. `X-RCM-Log-Offset` 이 0 이상의 정수가 아니거나 세션은
   있는데 오프셋이 없어도 400 이고 이 검사는 PR2 가 한다. 세션 헤더가 없으면 그 이름에 레거시 busy 잡이 있는지 본다. 있으면
   오프셋 없이 오늘처럼 덧붙이는 레거시 길로 가고(T11), 없으면 409 `protocol_required` 다. 400 과 409 는 아무것도 반영하지
   않는다.
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
   `\n` 으로 끝나므로 마커는 온전하다. 3판은 「세션이 아직 현재」라는 조건을 같은 UPDATE 에 넣었다(결정 338, V2-7 · R17).
   2판은 잡의 세션 칸만 봤다. 그 칸은 펜싱이 커밋된 뒤에도 같으므로 step 2 를 지난 요청이 펜스 뒤에 커밋할 수 있었다. 바뀐
   행이 0 이면 롤백하고 `truncate(pos)` 한 뒤, 잡을 다시 읽어 409 `job_closed`(+`state`)나 `session_fenced` 를 준다. SQLite
   가 바쁘면(`busy_timeout` 5초, F57) `truncate(pos)` 하고 오늘의 `code` 인 503 `database_busy` 를 준다.

   ```sql
   UPDATE jobs SET log_offset = L + len(rest), log_file_bytes = pos + len(masked), last_output_at = …
   WHERE id = ? AND log_offset = L AND state IN BUSY_STATES AND worker_session_id = S
     AND EXISTS (SELECT 1 FROM worker_sessions WHERE id = S AND fenced_at IS NULL)
   ```
9. 8 의 커밋은 잡별 로그 잠금 안에서 끝난다. 그 뒤 잠금을 놓고 오늘처럼 마커를 발행한다. 응답은
   `{job_id, bytes, markers, offset}` 이고 `offset` 은 새 `log_offset` 이다. 이 문구는 3판에서 고쳤다(V2-12). 2판은 「잠금을
   놓고 커밋한 뒤」라고 적어 8 의 잠금 안 커밋과 어긋나게 읽혔다.

- 서버가 기동할 때 정리한다. busy 원격 잡 가운데 `log_file_bytes` 가 있는 잡의 `log.txt` 가 그 값보다 길면 그 길이로
  자른다. 쓴 뒤 커밋하기 전에 서버가 죽어서 남은 조각이다. 더 짧으면 6 과 같이 `log_complete = 0` 을 고정하고 로그 한 줄을
  남긴다. 같은 단위끼리만 비교한다(3판 r1 A-M1).
- 3판에서는 기동 정리가 끝난 원격 잡도 본다(결정 338, V2-10). `log_file_bytes IS NOT NULL AND log_complete = 1` 인 terminal
  잡의 `log.txt` 가 있고 그 길이가 `log_file_bytes` 보다 짧으면 `log_complete = 0` 으로 고치고 로그 한 줄을 남긴다. OS 충돌이
  끝난 잡의 꼬리를 자른 경우이고 RL37 이 「되돌릴 수 없는 결과」라고 한 바로 그 경우다. 파일이 아예 없으면 건드리지 않는다.
  gc 가 지웠을 수 있다. 더 길면 자르지 않는다. 끝난 잡의 파일에는 커밋 뒤 쓰기가 없다. 비용은 `os.stat` 1만 개에 0.02초
  꼴이다(F79). 운영에서 대상이 되는 잡 수는 `—` 이고 메인 세션이 운영에서 읽는다.
- 레거시(프로토콜 1) 잡의 로그는 오늘 길(덧붙임 + `_log_partial`)을 그대로 쓴다. 그 잡은 `log_offset` 이 NULL 이고
  `log_complete` 도 NULL 이라 「모름」이다.
- 워커의 `acked` 는 2xx 응답의 `offset` 으로만 오르므로 `acked ≤ 서버 log_offset` 이 늘 성립한다. 빈틈은 버그일 때만 생긴다.
- 4판에서는 읽는 쪽도 커밋된 길이까지만 읽는다(PR2, 결정 339-f, F85). 잡 행에 `log_file_bytes` 가 있으면, 곧 프로토콜 2 원격
  잡이면 `GET /jobs/{id}/log` 는 `min(4 MiB, log_file_bytes − offset)` 만 읽고 `X-RCM-Next-Offset` 은 `log_file_bytes` 를 넘지
  않는다. 상태와 잡 문서의 로그 꼬리도 그 길이에서 끝난다. 이를 위해 `tail_lines` 에 끝 길이 인자를 더한다. `log_file_bytes` 는
  같은 요청에서 잡 행을 읽어 쓰고 잠금은 없다. 커밋된 값이면 그 길이까지의 바이트는 이미 파일에 있고 바뀌지 않는다. 쓰기는 늘
  `log_file_bytes` 뒤에서만 하기 때문이다(step 6 · 7). 마커 SSE 는 step 9 대로 커밋 뒤에 나가므로 바꿀 것이 없다. 로컬 레인 잡과
  레거시 잡은 `log_file_bytes` 가 NULL 이라 오늘처럼 파일 길이까지 읽는다. 파일이 더 짧으면(T16) 파일 길이까지 읽는다.

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

- 백오프는 1초에서 시작해 두 배씩 30초까지 늘린다. 3판에서는 잡마다 하나다(결정 337). 잡이 도는 동안에는 횟수와 시간에
  상한이 없다. 부모 명세 3판 r3 A-H6 · R1 의 「연속 실패 20번」은 버렸다.
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
  같은 이유다(`remote_worker.py:1085-1088`).
- 오늘의 메모리 버퍼(`LOG_KEEP_BYTES`), `_unreported`, `REPORT_RETRIES`, heartbeat 안의 `_retry_unreported`(F53) 길은 이
  큐로 바뀐다(F32 · F33). heartbeat 스레드는 보고를 다시 보내지 않는다.
- `\r` 진행 표시가 줄 끝 없이 이어지면 러너가 64 KiB 마다 끊어 보낸다. 그래서 실시간 tail 에는 그만큼 늦게 보인다. 이 점은
  문서에 적는다(RL17 c).
- 3판에서 거절한 대안:
  - 보내기 스레드 하나에 비동기 I/O 쓰기. 표준 라이브러리 `urllib` 은 막는 호출이라 새 의존성 없이 하려면 스레드가 필요하다.
  - 보내기 스레드 하나에 항목마다 짧은 시한 두기. 산출물 PUT 은 큰 파일이라 300초가 필요하다(F55).
  - PR1 에서 오프셋까지 하기. PR1 이 이미 가장 크고 디스크 큐 없이는 오프셋 재전송에 뜻이 없다.
  - 기동 때 끝난 잡을 보지 않기(2판). 잘린 꼬리가 `log_complete = 1` 로 남는다.
- 거절한 대안:
  - 부모 명세 3판의 `log.pending`. F34 때문에 필요 없다.
  - 조각마다 서버가 줄 끝 없는 꼬리를 메모리에 두기. 재시작하면 사라진다.
  - 워커 로컬 `log.txt` 를 재전송 원본으로 쓰기. 그 파일은 스트림과 내용이 다르다(F36).
  - 쿼리 문자열 `?offset=`. 경로 정규식 `_WORKER_JOB_RE`(`server.py:267`)를 건드리게 되고 헤더 관례가 이미 있다.
  - 덧붙이고 기동 때만 자르기(1판). 커밋 실패나 쓰기 실패 뒤의 재전송이 남은 바이트 뒤에 붙는다(V2-2 · V1-3).
  - 쓰기마다 fsync. 4 MiB 본문과 수십 개의 잡에서 디스크를 묶어 둔다. 짧은 파일은 드물고 `log_complete = false` 로
    정직하게 알린다.
  - 짧은 파일에서도 `log_file_bytes` 위치에 그대로 쓰기. 빈 바이트 구멍이 생긴다.
  - 짧은 파일을 워커에게 다시 보내게 하기. 서버는 가린 바이트와 원본 오프셋의 대응을 갖고 있지 않다.
  - finish 가 끝없이 로그 뒤에 줄 서기(1판). 끝난 잡이 24시간 running 으로 남는다(V2-3).
  - 연결 실패에도 finish 먼저 보내기. 서버에 닿지 않으면 finish 도 못 가고 로그만 잃는다.

### 4.6 finish · 늦은 결과 · 로그 완전성 · 산출물 · 홀드 확인 (결정 256 · 259 · 264 · 273~275 · 277 · 278 · 324 · 330)

- finish 본문에 `worker_session_id`, `log_bytes`, `group_gone` 을 더한다. `log_bytes` 는 그 잡이 낸 스트림 전체 바이트이고
  스풀이 깨졌으면 센 값이다. `group_gone` 은 3판에서 더한 bool 이다. 러너가 돌아온 뒤 `killpg(pgid, 0)` 이 ESRCH 였거나
  프로세스를 띄운 적이 없으면 참이다. F80 의 시험 도우미와 뜻이 같다. 표식 잡이 아니어도 싣고 서버는 표식 잡에만 쓴다(결정
  330). `summary_code` 는 오늘의 `PREFLIGHT_CODES` 나 새 `LOST_CODES` 가운데 하나다. `LOST_CODES` 는 `outcome = lost` 일 때만
  받고 인자는 코드마다 정한 키만 받는다. 오늘 `_preflight_summary` 와 같은 엄격함이다.
- `LOST_CODES` 는 `worker_self_stopped`(인자 `seconds`), `worker_replaced`(인자 `name`, 서버가 토큰 이름으로 채움),
  `snapshot_unreachable`(인자 없음)이다. 서버만 쓰는 `lost` 코드는 오늘의 `worker_unreachable` 과
  `worker_restarted_without_job`, 그리고 새 `orphan_unverified`(인자 `name` · `seconds`, 결정 327)다.
- 로그 완전성: finish 를 받을 때 `log_complete = 0` 이 이미 고정돼 있으면(T16) 0 으로 둔다. 아니면 `(log_offset ≥
  log_bytes)` 를 잡 행에 적는다. `log_bytes` 가 없는 레거시 잡은 NULL 이다. 로컬 레인 잡은 서버가 직접 쓰므로 NULL 이 아니라
  참이다. 그러지 않으면 로컬 잡 전부에 「모름」이 붙는다. `recent_json`, 잡 문서, `rcm wait` JSON 은 `log_complete`(참,
  거짓, null)를 보인다. 거짓이면 `rcm wait` 사람용 줄, `rcm logs` 끝, 웹 로그 보기에 「로그가 불완전함」을 보인다(PR3).
- 늦은 결과(결정 256 · 274): V2-11 에 따라 트랜잭션을 단계로 적는다. `BEGIN IMMEDIATE` 하나 안에서 다음을 한다.
  1. 잡을 다시 읽어 `state = lost` 이고 `worker_session_id` 가 요청 세션이며 요청 세션이 펜싱되지 않았는지 확인한다.
  2. `SELECT 1 FROM events WHERE job_id = ? AND kind = 'late_result'` 가 없을 때만 다음으로 간다.
  3. events 에 kind `late_result` 를 한 건 넣는다. 내용은 `{outcome, exit_code, finished_at, received_at, log_complete}` 이고
     `finished_at` 은 워커 값에 아래 자르기를 적용한 것이다.
  4. `jobs.log_complete` 를 위 규칙으로 쓴다. 유예로 닫힌 잡은 이 칸이 NULL 이었다.
  5. 3판: 그 잡에 열린 홀드가 있고 본문의 `group_gone` 이 참이면 홀드를 푼다(`release_kind = worker`, 결정 330).

  SQLite 는 `BEGIN IMMEDIATE` 로 쓰는 쪽을 하나씩 줄 세우므로 동시에 온 늦은 finish 둘 가운데 하나만 들어간다. events 에
  유일 키를 새로 두지 않는다. 응답은 409 `job_closed` + `state: lost` + `late_result_recorded` 다. 넣었으면 true, 이미
  있었으면 false 다. 3판에서는 응답에 `hold_released`(bool)를 더한다. 잡 상태, 요약, `finished_at`, 로그, 마커, 산출물,
  알림, `job_finished` 는 모두 그대로 둔다. `GET /jobs/{id}` 의 종료 잡 문서에 `late_result`(객체 또는 null)를 싣는다.
  `rcm wait` JSON 은 잡 문서를 복사하므로 같은 키를 싣는다(`PLAN.md:257`). 펜싱된 세션의 늦은 finish 는 기록하지 않는다(2차
  Q-B, RL11).
- 3판에서 finish 와 홀드의 관계를 정했다(결정 330). busy 표식 잡을 닫는 finish 의 `group_gone` 이 거짓이거나 없으면 같은
  트랜잭션에서 홀드를 만든다. 옛 워커와 레거시 잡이 `group_gone` 이 없는 경우다. 결과는 그대로이고 프로세스가 끝났다는
  확인만 없는 것이다. 잡이 이미 terminal 이고 `cancelled` 처럼 `lost` 가 아닌 상태면 `late_result` 대상이 아니다. 이때는 409
  `job_closed` 를 주기 전에 같은 홀드 풀기만 한다. 같은 세션이고 `group_gone: true` 일 때다. 로컬 레인 잡은 서버가 러너의
  `group_gone` 을 직접 본다.
- 중복 finish(RL21 a): 같은 세션이 보냈고 잡이 그 finish 로 이미 끝났으면 409 `job_closed` 이고 기록하지 않는다. 워커는 그
  항목을 큐에서 지운다.
- `finished_at` 자르기(결정 277): 값이 `[started_at, 수신 시각]` 밖이면 가까운 끝으로 자른다. 그래서 `job_seconds` 가 음수가
  되지 않는다(RL27 b).
- 준비 단계(결정 259): 스냅샷 내려받기가 status 0 이나 503 이면 `lost` + `snapshot_unreachable` 이다. 404 · 409(스냅샷 없음,
  조립 실패), 저장소 없음, 깨진 tar 는 오늘처럼 `failed` 다. git_ref fetch 실패는 rcm 서버와의 끊김이 아니므로 `failed`
  그대로다.
- 산출물(결정 275, RL22): 2판은 파일 조건을 더했다(V1-12). 업로드는 보고 큐에서 finish 바로 앞 단계다. 연결 실패와 503 은
  재전송 창 안에서 다시 한다. 같은 해시를 다시 올리면 서버가 200 을 준다(F17). 5xx 가 300초를 넘으면 결정 324 를 따른다.
  묶음 스풀은 지금도 지우지 않는다(`remote_worker.py:1051-1053`). 처분은 finish 를 보내기 직전에 정한다. 서버는 그 잡의 묶음
  행이 있고 해시가 같고 `artifacts_dir(job)/bundle.tar` 파일이 있을 때만 처분 `ready` 를 받는다(F63). 행이나 파일이 없으면
  `failed` + `upload_failed`, 해시가 다르면 `failed` + `hash_mismatch` 다. 둘 다 이미 있는 `REASONS` 다
  (`core/artifacts.py:76-94`). 잡 결과는 오늘처럼 산출물과 무관하다.

### 4.7 프로토콜 판과 호환 (결정 258 · 262 · 270 · 329, M5t-c 303 · 317)

- `worker_protocol = 2` 다. 1 은 이 명세 이전의 세션 없는 판이다. `/worker/spec` 응답과 register 응답에
  `worker_protocol: 2` 를 싣는다.
- 새 워커는 등록 전에 확인한다(T13). `GET /worker/spec` 을 불러 `worker_protocol ≥ 2` 면 진행한다. 키가 없거나 404 면
  `GET /api/health` 의 `version` 을 본다. 자기 버전과 다르면 오늘의 등록 길로 가서 따라가기(M5q)를 하거나 종료 2 로 끝난다.
  서버의 버전 409 는 어떤 부작용보다 먼저 나온다(F5 · `remote_workers.py:397-405`). 버전이 같으면 「서버가 워커 프로토콜 1 —
  서버를 올려라」 한 줄을 남기고 종료 3 으로 끝난다. 이때 등록과 claim 은 0건이고 세션 폴더도 0개다(RL25 · RL32, V2-12).
- 종료 코드(결정 258): 펜싱은 0, 프로토콜 불일치는 3 이다. 나머지는 오늘대로 0 은 멈춤, 2 는 등록 막힘, 1 은 exec 실패다.
  launchd `SuccessfulExit=false` 와 systemd `Restart=on-failure` 는 0 을 되살리지 않고 3 을 되살린다(F39). 서비스 파일은
  바꾸지 않는다.
- M5t-c 와의 관계(F52)는 1판과 2판의 문장을 3판에서 M5t-c 결정 317 대로 고쳤다. 틀린 MAC 의 hello, 421, 인증되지 않은 어떤
  응답도 워커를 종료 0 으로 끝내지 않는다. 워커는 토큰 없이 계속 다시 시도하고 10분마다 경고하며 FAIL/모름 상태를 보인다.
  종료 0(되살리지 않음)은 이 명세의 펜싱과 M5t-c 의 인증된 두 경우뿐이다. 두 경우는 확인된 서버가 401 로 토큰을 거절하고 새
  hello 는 여전히 통과하는 경우와 등록 증명 `bad_mac` 이 세 번 이어지는 경우다. 연결 실패, 5xx, 503 은 종료하지 않고
  프로세스 안에서 다시 시도한다(결정 303). 종료 3 은 이 명세의 프로토콜 불일치 하나다. 그 뜻은 「일을 시작하지 말고 감독자가
  다시 시도하라」다. 재시작이 버전 따라가기를 부르기 때문이다. 부모 명세 M8 의 「서비스가 되살리지 않는 종료 코드」는
  「펜싱 · 인증된 신원 거절(317)」로 좁혀졌다. 이 명세의 0 은 펜싱이다. 2판의 「확실한 신원 불일치(MAC 틀림)는 종료 0」
  문장은 지운다. 인증되지 않은 hello 응답은 사칭자도 만들 수 있다. 그것으로 워커를 멈추게 두면 같은 망의 누구나 워커를
  영구히 끌 수 있다.
- 옛 워커(프로토콜 1)가 새 서버에 붙으면(결정 262) 서버는 레거시 busy 잡이 있는 이름에서 온 세션 없는 heartbeat, phase,
  log, finish, artifacts 만 받는다. log 는 오프셋 없이 오늘처럼 덧붙인다. claim, register, capabilities 는 409
  `protocol_required` 다. 옛 워커의 반응은 F40 에 있다.
- `protocol_required` 의 `error` 문구는 `"worker protocol 2 required: upgrade rcm on this worker"` 다(결정 329, V2-13).
  「not registered」를 담지 않는다. 옛 워커는 claim 409 의 `error` 에 그 부분 문자열이 있으면 재등록 루프를 돌기
  때문이다(`remote_worker.py:799`, F40).
- 1판의 「도는 중에 409 `protocol_required` 를 받으면 모든 잡을 멈추고 종료 3」은 지웠다(§4.1.2, V2-9).
- 펜싱 핑퐁은 없다(RL10). 펜싱된 프로세스는 종료 0 으로 끝나고 감독자가 되살리지 않는다. 펜싱된 세션의 재등록은 409
  `session_fenced` 다(§4.2).
- `rcm worker --check` 와 `rcm doctor` 는 세션을 만들지 않는다(F46). `--once` 는 보통 워커와 같다.
- 4판(M5t-c 결정 343, 교차 명세): 짝지은 워커는 토큰 행에 `pair_id` 가 있는 워커다. 이 워커가 받은 409 `session_fenced` 는
  `pair_reject_mac` 이 그 요청의 nonce 와 본문 sha256 으로 검증될 때만 종료 0 으로 이어진다(W5). 검증되지 않으면 종료하지 않고 새
  hello 뒤에 다시 시도한다(M5t-c J12′). 같은 망의 위장자가 워커를 영구히 끄지 못하게 하는 M5t-c 317 의 규칙과 같은 꼴이다.
- 409 `protocol_required` 도 짝지은 워커는 MAC 이 검증될 때만 이 명세의 종료 3 으로 간다(W6, M5t-c J12). 재시작이 버전 따라가기를
  부른다. 검증되지 않은 `protocol_required` 에는 종료 3 으로 끝내지 않는다. 새 hello 뒤 등록 백오프로 프로세스 안에서 다시
  시도하고 상태 사유는 `registration_refused` 다(M5t-c J13). 진짜 서버는 짝지은 토큰의 키를 늘 갖고 있으므로 검증되지 않은 응답은
  위조이거나 키를 잃은 서버에서 온 것이다. 그때 재시작하면 5~10초 간격의 재시작 되풀이만 생긴다. 이 규칙은 메인 세션 결정으로
  정했다(`docs/m5tc-workplan.md` 의 343 행, PR #269).
- 짝짓지 않은 옛 토큰의 워커는 M5t-b 동작 그대로다. 펜싱에 종료 0, 프로토콜 불일치에 종료 3 이다. 그래서 이 명세에서 펜싱에 종료 0
  한다는 문장은 「짝짓지 않은 워커의 펜싱과 짝지은 워커의 검증된 펜싱」으로, 프로토콜 불일치에 종료 3 한다는 문장은 「짝짓지 않은
  워커와 짝지은 워커의 검증된 `protocol_required`」로 읽는다.

### 4.8 요청자에게 보이는 것 (결정 276 · 278 · 273 · 274 · 327)

- 유예 중(T2)에 도는 잡의 사유는 새 `reason` 값 `worker_unreachable_grace` 다. 값을 더하기만 하므로 `schema_version` 은 1 을
  유지한다(`PLAN.md:140, 450`). `compute_queue` 가 그 잡의 `worker_name` 이 down 레인의 워커일 때 이 사유를 정한다.
  `_busy_reason` 순서에서 `worker_unreachable_grace` 를 맨 앞에 둔다. 취소 중이어도 그렇다. 취소가 닿지 못한다는 사실이 더
  행동에 쓸모 있기 때문이다. `estimate.finish_at` 은 null 이다.
- `ACTIONABLE_REASONS` 와 웹 `ACTIONABLE` 에는 `worker_down` 바로 뒤에 더한다. F29 · F58 의 잠근 시험은 같은 PR 에서 모두
  고친다.
- 레인 `down` 과 헬스의 `workers_down`/503 은 그대로다(F22). 부모 명세 3판 r1 의 새 레인 상태 `unreachable` 은 만들지
  않는다(3판 r2 A-M1).
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
- 3판에서는 그룹 홀드가 보이는 곳을 정했다. 결정 330 의 「어디서나 needs_action」을 따른다(F75).
  - 대기열 사유: 열린 홀드의 그룹(범위 규칙)에 막힌 대기 잡의 사유는 `needs_action` 이고 새 값을 만들지 않는다.
    `blocked_by {job_id: <홀드 잡>, group, remaining_seconds: null}` 과
    `action {code: "irreversible_hold", note: "<한 줄>", at: <홀드 만든 시각>}` 을 싣고 `estimate.finish_at` 과
    `wait_seconds` 는 null 이다. `compute_queue` 는 열린 홀드 목록을 입력으로 받아 남은 시간이 무한한 그룹 점유로 다룬다.
    언제 풀릴지 모르므로 `_GroupHold` 모의에는 넣지 않는다. 홀드가 `blocked_by_group` 보다 먼저 온다. `needs_action` 은 행동
    가능 목록의 맨 앞에 있다.
  - `/api/status` 최상위 키 `irreversible_holds: [{job_id, preset, group, scope, worker, cause, since}]` 를 더한다. 홀드가
    없으면 `[]` 이고 키만 더하므로 `schema_version` 은 1 이다. `/api/health` 에도 같은 키를 정보로 싣는다. 503 조건은 아니다.
    홀드는 서버가 아프다는 뜻이 아니다.
  - 웹: 「Needs a look」에 홀드마다 한 항목을 둔다. 대기 잡이 없어도 둔다. 문구는 영어 UI 로 「Group G is held: job #N
    (preset) on W closed without a stop confirmation. Check W for a leftover process, then run rcm release-hold N.」 같은
    꼴이고 원문 코드는 드러내지 않는다. 홀드 잡의 행에는 칩 하나를 단다.
  - `rcm check`: 홀드마다 경고 줄(ok = None) `irreversible hold` 를 낸다. FAIL(종료 1)이 아니라 경고로 둔다. `rcm check` 는
    환경 검사이고 운영 상태는 경고로 보이는 선례가 있다(`held_by_load` 오래 닫힘, `core/model.py:123-125`). FAIL 로 두면 다른
    프로젝트 세션의 연결 점검이 홀드와 무관한 프리셋까지 막는다.
  - `rcm doctor`(dev, M5t-a #268): 역할이 `worker` 나 `coordinator-worker` 일 때 `/api/health` 의 홀드 가운데 `worker` 가 이
    머신의 워커 이름인 것을 `needs action` 줄로 보인다. 문구는 「a job that ran here was closed without a stop confirmation
    — check for a leftover process, then ask an admin to run rcm release-hold N」이다. `--json` 에 키 `irreversible_holds` 를
    더하고 `doctor_version` 은 1 이다. 종료 코드 규칙은 바꾸지 않는다. M5t-a 가 정한 0/1/2/3 은 「이 머신이 무엇을 할 수
    있나」를 말하고 홀드는 그 질문이 아니다.
  - `rcm wait`: 기다리는 잡이 홀드에 막히면 stderr 에 한 줄을 낸다. `--return-on-action` 이면 4 로 끝난다. M5s 의 「사람이
    할 일」 계약 그대로다. 잡 문서(`GET /jobs/{id}`)에는 `hold` 를 싣는다. 객체 또는 null 이고 칸은
    `cause, created_at, released_at, release_kind, released_by, release_reason, history[]` 다.

4판에서는 홀드가 보이는 곳의 PR 배치와 알림을 바꿨다(결정 350 · 339-e).

- 기계가 읽는 것은 PR1 이 만든다. 대기 잡 사유 `needs_action` 과 `blocked_by`, `action.code` `irreversible_hold`(`compute_queue`
  가 열린 홀드 목록을 받는다), `/api/status` 와 `/api/health` 의 `irreversible_holds`, 잡 문서 `hold`, `rcm release-hold` 다.
- 사람이 보는 것은 PR3 이 만든다. 웹 「Needs a look」 항목과 칩, `rcm check` 경고 줄, `rcm doctor` 줄과 `--json` 키, `rcm wait`
  stderr 줄과 `--return-on-action` 의 4 다.
- 알림도 PR1 이 만든다(§4.12). 홀드를 만든 잡의 잡 끝 알림에 `RCM_HOLD` · `RCM_HOLD_GROUP` 과 본문 한 줄을 싣는다. 규칙이 `on` 에
  `irreversible_hold` 를 적었으면 그 규칙은 홀드마다 따로 한 번 부른다.

### 4.9 DB v26 (결정 265 · 271 · 273 · 327 · 339-a · 339-d, PR1 이 올린다)

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
| `jobs` | `claim_seq INTEGER` | 그 잡을 돌려준 claim 요청의 워커 순번(NULL = 로컬 · 레거시) | PR1 |
| `jobs` | `log_offset INTEGER` | 받은 원본 스트림 바이트(오프셋 기준) | PR2 가 씀 |
| `jobs` | `log_file_bytes INTEGER` | 가린 뒤 커밋된 `log.txt` 길이 | PR2 가 씀 |
| `jobs` | `log_complete INTEGER` | 1/0/NULL(0 은 짧은 파일에서 미리 고정될 수 있다, T16) | PR2 가 씀 |
| `events` | kind `late_result`(스키마 변경 없음) | 늦은 결과 | PR2 |
| `irreversible_holds`(새, 3판) | `job_id INTEGER PRIMARY KEY` | 홀드를 만든 잡(잡 행은 열린 홀드가 있는 동안 지우지 않음) | PR1 |
| `irreversible_holds`(고침) | `concurrency_group TEXT NOT NULL` · `concurrency_scope TEXT NOT NULL` · `pool TEXT NOT NULL` · `host_key TEXT` | 막는 그룹과 범위 — 4판: 닫는 트랜잭션에서 **잡 행**의 값을 복사한다. 표식 잡의 그룹 NULL 은 claim 때 유효 그룹 · 범위로 이미 채워져 있다. 레거시 잡(그룹 NULL)은 `irreversible:<프리셋 이름>` · `global`(결정 339-d). 지금 설정은 읽지 않는다 | PR1 |
| | `worker_name TEXT` | 그 잡이 돈 워커(NULL = 로컬 레인) | PR1 |
| | `cause TEXT NOT NULL` | 닫힘의 요약 코드(`worker_unreachable` · `orphan_unverified` · `cancel_unconfirmed` · `worker_restarted_without_job` · `server_restarted`) 또는 `group_not_gone`(finish 의 `group_gone` 이 거짓 · 없음) | PR1 |
| | `created_at REAL NOT NULL` · `released_at REAL` · `release_kind TEXT`(`worker`/`admin`) · `released_by TEXT`(토큰 이름) · `release_reason TEXT` | 감사 이력(풀어도 행은 남는다) | PR1 |
| | 부분 색인 `(concurrency_group) WHERE released_at IS NULL` | claim · 대기열의 열린 홀드 찾기 | PR1 |
| `events` | kind `hold`(스키마 변경 없음, 3판) | 홀드 만들기 · 풀기 사건 | PR1 |
| `jobs` | `irreversible` — 3판: 로컬 레인 claim 에서도 얼림 | 감독자 · 재기동 홀드(F76) | PR1 |

- 마이그레이션은 `_MIGRATIONS[26]` 에 위 DDL 을 둔다. 옛 행은 전부 NULL 이다. 로컬 잡이거나 레거시 잡이라는 뜻이다(T11).
  백업은 기존 경계에서 한다(F50). 옛 빌드는 v26 DB 를 거절한다(F50). M5t-c 는 v27 이다(§9, M5t-c 결정 299).
- 부모 명세 3판에서 뺀 것: `log_raw_acked` 는 이름을 `log_offset` 으로 바꿨다. `log_bytes_total` 은 finish 본문의
  `log_bytes` 로만 받고 저장은 `log_complete` 판정 하나로 한다. `log_incomplete` 는 긍정형 `log_complete` 로 바꾸고 null 을
  「모름」으로 둔다. `lease_id`, `worker_identity`(M5t-c 몫), `workers` 의 세션 칸도 뺐다.

### 4.10 얼어붙는 계약 (머지 = 동결, workplan §6)

명세가 머지되면 아래 계약이 얼어붙는다.

| 계약 | 바뀌는 것 |
|---|---|
| `schema_version`(`/api/status`) | 1 그대로. 더하기만: 도는 행 `reason` 값 `worker_unreachable_grace`, 최근 행 · 잡 문서 `log_complete`, 종료 잡 문서 · wait JSON `late_result` |
| DB | v25 → v26(§4.9) |
| 워커 프로토콜(아래 표) | 2 |
| 종료 코드 | `rcm worker`: 0 에 「펜싱됨」, 새 3 「프로토콜 불일치」. `rcm wait` 0~5 그대로 |
| CLI 플래그 | 2판까지 없음. 3판: 새 명령 `rcm release-hold <job>` 의 `--reason` · `--json`(아래 「새 CLI(3판)」 행) |
| 설정 키 | `[server] disconnect_grace_seconds`(정수, 기본 `max(600, L + 35)`, `L + 35 ≤ 값 ≤ 86400`, L = `max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 3 × 15 + 5)`(결정 335), 위반은 기동 실패 종료 2 + 키 이름 · 하한 문장, `[server]` 라 reload 불가 F20). 프리셋 `irreversible`(bool, 기본 false, reload 가능, claim 때 얼림). 워커 `grace_seconds` 는 키 · 검증 그대로(표식 잡에서만 그 잡에 맞춰 줄여 씀, §4.3) |
| 헬스 기능 키 | 3판: `irreversible_holds`(목록, 정보 — 503 조건 아님) |
| 상태(`/api/status`) 최상위 | 3판: `irreversible_holds`(목록), 대기 행 `action.code` 새 값 `irreversible_hold`(사유 값은 기존 `needs_action`) |
| 새 라우트(3판) | `POST /jobs/{id}/release-hold`(admin) — 200 · 403 · 404 · 409 `no_hold` |
| 새 CLI(3판) | `rcm release-hold <job> [--reason TEXT] [--json]` — 종료 0 풀었다 · 1 열린 홀드 없음 · 2 사용법 · 401/403/404 · 3 못 닿음 |
| `rcm doctor --json`(3판) | 키 `irreversible_holds` 더함(`doctor_version` 1 그대로) — dev 의 `tests/test_doctor_cli.py:993-1017` `TOP_KEYS` 잠금을 같이 고침 |
| 감독자(3판) | 모듈 이름 `remote_ci_monitor.job_supervisor` 와 인자는 내부 — 얼리지 않는다. 얼리는 것은 「표식 잡의 `proc.json` `pgid` = 감독자 pid」 하나(다음 판 워커가 읽는다) |
| 설정 해석(3판) | 표식 프리셋에 그룹이 없으면 `irreversible:<이름>` · `global`(결정 330). `disconnect_grace_seconds` 를 안 적으면 `max(600, L + 35)`(결정 335) |
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
| `POST /worker/jobs/{id}/finish` | `worker_session_id`, `log_bytes`, `summary_code ∈ LOST_CODES`(outcome lost), 3판 `group_gone`(bool) | 3판: 좁은 확인 · 늦은 finish 가 홀드를 풀면 `hold_released: true` | 409 `job_closed`(+`state`, `late_result_recorded`, 3판 `hold_released`) · `session_fenced`. 3판: 다른 이름의 세션 403 |

3판에서는 모든 세션 헤더와 본문 세션 칸의 이름을 대조한다(T19). 다른 이름의 세션이면 403 이고 본문에는 `error` 만 싣는다.
PR1 의 로그 라우트는 `X-RCM-Log-Offset` 을 읽지 않는다(§4.5 PR 경계).

모든 409 는 오늘의 `error` 문구를 남기고 `code` 를 더한다. 옛 워커가 문구로 맞추는 `not registered` 를 지키려는
것이다(`remote_worker.py:799`). `protocol_required` 의 문구에는 「not registered」가 들어가지 않는다(§4.7).

4판에서 더한 계약은 아래와 같다.

| 계약 | 4판에서 더한 것 |
|---|---|
| 워커 프로토콜 2 · `POST /worker/heartbeat` 요청 | `seq`(정수 ≥ 1, 세션이 있으면 필수), `claims_settled`(정수 ≥ 0, 세션이 있으면 필수). 형식이 틀리면 400(`error` 만) |
| 워커 프로토콜 2 · `POST /worker/claim` 요청 | `claim_seq`(정수 ≥ 1, 세션이 있으면 필수) |
| DB | `jobs.claim_seq` |
| 설정 키 | `[[notify]] on` 이 종료 상태와 함께 `irreversible_hold` 를 받는다(opt-in). `on` 을 안 적은 규칙의 기본은 그대로다 — `irreversible_hold` 는 기본에 들지 않는다(6차) |
| 알림 훅 env | 모든 알림에 `RCM_EVENT`(`job_finished` · `irreversible_hold`). 홀드를 만든 잡의 잡 끝 알림에 `RCM_HOLD=1` · `RCM_HOLD_GROUP=<그룹>`. `irreversible_hold` 알림(opt-in)에 `RCM_HOLD_GROUP` · `RCM_HOLD_CAUSE` · `RCM_HOLD_WORKER`(로컬 레인이면 빈 값) |
| 알림 URL 본문 | 모든 알림에 `event`. 홀드를 만든 잡의 잡 끝 알림 본문에 한 줄 `group held — run rcm release-hold <job>`. `irreversible_hold` 알림에 `hold {group, scope, cause, worker, since, release_command}` |
| 감독자(내부, 얼리지 않음) | 실행 꼴 `[sys.executable, "-I", <job_supervisor.py 경로>, …]`, 종료 `SUPERVISOR_NOT_STARTED_EXIT` 125 와 첫 줄 표지 `[rcm] supervisor: not started: <code>` — 같은 판의 러너만 읽는다 |
| M5t-c 343 | 짝지은 토큰의 `session_fenced` · `protocol_required` 에 `pair_reject_mac`(M5t-c 가 정의 · 얼림). 짝지은 워커의 종료 코드는 검증된 펜싱 0 · 검증된 `protocol_required` 3, 검증 안 된 것은 프로세스 안 재시도(§4.7) |

### 4.11 기존 결정과의 관계

| 기존 | 이 명세 |
|---|---|
| PLAN 「잡 모델」 `lost(서버 재시작 중 죽음)`(`PLAN.md:89`)·「잡은 서버 재시작을 넘어 살아남는다」(`PLAN.md:121`)·저장소 시작 정리(`PLAN.md:293`) | 원격 잡의 `lost` 는 끊김 유예 만료 · 자기 멈춤 · 펜싱 · 재시작 이어받기로 생긴다고 PR0 이 PLAN 문장을 더한다(§5 PR0 행) |
| PLAN M5b-2 「timeout → down · 잡 lost · 재등록 = 옛 잡 lost」(`PLAN.md:600`) | 바꾼다: timeout → down 만, 잡은 유예, 재등록은 명시한 이전 세션만. PR0 이 그 줄에 「M5t-b 가 바꿈」 을 단다 |
| `rcm wait` 3 = 모름(`PLAN.md:118`) | 그대로. 다섯 끝 모두 3 |
| `cancelled`·`lost` 에 스텝 없음(결정 64, `PLAN.md:220`) | 그대로 |
| M5n §13 자동 재실행 안 함(`docs/m5n-workplan.md:652`) | 그대로. `late_result` 도 다시 돌리지 않는다 |
| M5q 드레인 「도는 잡은 하나도 잃지 않는다」(`remote_worker.py:10`) | 강화: 보고 큐가 exec 를 넘는다 |
| 결정 254 `local_only` | 별개 키(결정 257). 둘 다 달 수 있다. 3판: `local_only` 여도 `irreversible` 은 로컬 레인에서 감독자 · 재기동 홀드로 효과가 있다(결정 330 · 331, F76) |
| 결정 327(2판, 확인 못 한 고아 → 유예 만료에 `orphan_unverified` 로 닫고 그룹을 풂) | 3판: 4차 위임 결정 330 이 바꿨다 — 닫기 · 코드는 그대로, 그룹은 홀드 |
| PLAN 「레인 · concurrency 그룹」(`PLAN.md:127`) 「같은 그룹의 잡은 동시에 하나만 돈다」 | 3판: 표식 잡이 확인 없이 닫히면 그 그룹은 사람이 풀 때까지 하나도 안 돈다 — PR0 이 그 줄에 「M5t-b 결정 330 홀드」 를 단다 |
| M5e 산출물 「결과와 무관」 | 그대로. `ready` 조건만 좁힘(결정 275) |
| PLAN 「fail-open 금지」 「순수 계산 모듈은 … `scripts/mutcheck.py` 로 확인」(`PLAN.md:528`) | 2판이 더한 규칙마다 변이(§7.4, V1-10) |
| 부모 명세 3판 M8 「서비스가 되살리지 않는 종료 코드」 | M5t-c 결정 303 이 「펜싱 · 확실한 신원 거절」로, 317 이 「펜싱 · 인증된 두 경우」로 좁혔다(F52). 틀린 MAC hello · 421 · 인증 안 된 응답은 종료 0 이 아니다. 이 명세의 0 은 펜싱 |
| M5o 「알림」(결정 119 · 120, `[[notify]]` 는 잡 끝 상태) | 4판: 기본 사건은 그대로 잡 끝 하나다. 홀드를 만든 잡의 잡 끝 알림에 `RCM_HOLD` · `RCM_HOLD_GROUP` · 본문 한 줄을 싣고, 규칙이 `on` 에 적을 때만 부르는 opt-in 사건 `irreversible_hold` 를 더한다(결정 339-e, 6차). 잡 끝 알림의 조건 · 중복 막기는 그대로 |

### 4.12 되돌릴 수 없는 잡의 그룹 홀드 (결정 330 · 339, 3판에서 327 을 바꿈)

표식 잡 J 가 「프로세스가 죽었다는 확인」 없이 terminal 이 되면 서버는 J 의 동시성 그룹을 영속 홀드로 잡는다. 홀드가 열려
있는 동안 그 그룹(같은 범위 규칙)의 잡은 claim 되지 않는다. 홀드는 시간으로 풀리지 않는다.

4판에서는 확인을 표로 둔다(결정 339-a · 339-b). 확인은 한 번 참이면 다시 거짓이 될 수 없는 사실을, 그 사실이 선 뒤에 본 쪽이 보낸
메시지뿐이다. 그 꼴이 아니면 확인이 아니다. 표의 1~4 와 함께 닫히면 홀드를 만들지 않고, 닫힌 뒤에 오면 열린 홀드를 푼다. 3판의
확인 4(T5)는 뺐다.

| # | 확인 | 보내는 쪽 | 무엇이 순서 · 신선도를 보장하나 | 함께 닫힘 / 뒤에 옴 |
|---|---|---|---|---|
| 1 | J 의 현재 세션 finish 의 `group_gone: true`(러너가 돌아온 뒤 S7 의 ESRCH, 또는 Popen 직전 확인이 띄우지 않음) | 그 세션의 J 보내기 스레드 | 사실이 되돌아오지 않는다: J 는 한 번만 claim 되고 러너는 J 를 한 번만 띄운다 — 사라진 J 의 그룹은 다시 생기지 않는다. ESRCH 는 보내기 전에 봤다. 같은 잡 큐에서 finish 는 마지막이다(phase → 로그 → 산출물 → finish) | 홀드 안 만듦 / 풂 |
| 2 | J 의 펜싱된 세션의 좁은 확인 finish `group_gone: true` — 옛 프로세스(W5)가 그룹을 끊은 뒤, 또는 D 를 이어받은 새 프로세스가 재확인(W12)에서 | 옛 프로세스 · D 의 폴더 잠금을 쥔 새 프로세스 | 1 과 같은 되돌아오지 않는 사실. W12 는 D 폴더 `flock` 을 쥐어 D 가 죽었음을 알고(F43) 신원(F71)으로 같은 그룹을 끊은 뒤 ESRCH 를 봤다 | 같음 |
| 3 | 새 세션 등록의 `previous_sessions` ∋ S | S 의 폴더 잠금을 쥔 새 프로세스 | S 프로세스가 죽었다(`flock`, F43) — 죽은 프로세스는 더 보낼 것도 · 늦게 닿는 claim 응답을 받을 것도 없다. S 의 잡 폴더마다 「확인됨」(그룹 없음 · 띄운 적 없음, §4.4 의 6) — 되돌아오지 않는 사실 | 같음(그 세션의 busy 잡 닫기 · 이미 닫힌 잡의 홀드 풀기) |
| 4 | 로컬 레인: 러너가 돌아온 뒤 `group_gone` | 서버 자신 | 같은 프로세스 안, 러너가 돌아온 뒤에 본다 | 같음 |
| (확인 아님) | T5: heartbeat `jobs` 에 J 가 없음 | 현재 세션 heartbeat | 4판: `seq`(339-a) 와 `claims_settled ≥ J.claim_seq` 가 있어야 「없음」을 센다 — 그래도 「지금 목록에 없다」는 지금 상태다 | **늘 홀드**(339-b). 3판의 확인 4 를 뺐다 |

모든 확인은 J 의 `worker_name` 과 같은 이름의 토큰으로만 온다(T19 · 결정 336). 워커 경로는 워커 토큰의 경로이므로 admin
토큰으로도 이 길로는 풀 수 없다.

홀드를 만드는 닫힘은 모두 확인이 없는 닫힘이다. 유예 만료의 `worker_unreachable` 과 `orphan_unverified`(T3 · T8),
`cancel_unconfirmed`(T4), 레거시 만료(T11), 첫 등록의 레거시 닫기(T17), 4판에서 더한 T5 의 「없음」 닫기(표식 잡), `group_gone` 이
거짓이거나 없는 finish 와 좁은 확인, 로컬 레인의 `recover_on_start` `lost`(F76)가 그렇다. 어느 경우든 닫는 트랜잭션 안에서 홀드
행을 넣는다. 그래서 닫힘과 홀드 사이에 claim 이 끼어들 수 없다(R15).

합성 그룹은 재료가 정했다. 오너 결정 330 의 「설정 검증 또는 자동」 가운데 자동을 골랐고 5차 위임 결정 (a)에서 확정했다.

- 프리셋이 `irreversible = true` 이고 `concurrency_group` 이 비었으면 설정을 읽을 때 `concurrency_group` 을
  `"irreversible:" + 프리셋 이름` 으로, `concurrency_scope` 를 `"global"` 로 채운다. 서버 기동 로그에 한 줄을 남긴다.

  ```text
  preset X: irreversible without concurrency_group — using group irreversible:X (global)
  ```

  `rcm presets` 와 `/api/status` 의 `concurrency_group` 에도 그 이름이 보인다.
- 근거는 넷이다. 첫째, 안전이 설정의 빈칸에 기대지 않는다. 검증 실패로 두면 기동이 막힌 사람이 표식을 지워 우회하기 쉽다.
  앱 프로젝트가 표식을 다는 일은 §2 의 그쪽 작업이므로 rcm 이 고르는 기본이 곧 안전선이다. 둘째, 표식은 새 키라 어느 쪽이든
  오늘의 설정을 깨지 않는다. 그렇다면 사람 손을 덜 타는 자동이 낫다. 셋째, 그룹 이름은 형식 검사가 없는 문자열이라(F73)
  예약 접두어를 둘 수 없다. 사용자가 같은 글자의 그룹을 이미 쓰면 두 집합이 합쳐져 더 많이 막을 뿐이고 이것은 안전한 쪽이다.
  넷째, 범위 `global` 은 가장 넓다. 다른 범위를 원하면 사람이 그룹을 직접 적는다.
- 버린 안은 셋이다. 설정 검증은 기동 실패를 부른다(첫째 근거). 그룹 없는 표식 잡을 홀드 없이 두는 안은 fail-open 이고 결정
  330 이 막으려던 것이다. 프리셋 이름 그대로를 그룹으로 쓰는 안은 사용자 그룹과 우연히 겹치기 쉽다.
- 표식을 달기 전에 제출된 대기 잡은 그룹이 NULL 이다. 그룹은 제출 때 잡 행에 적힌다(F73). 이런 잡은 claim 때 X 를 1 로
  얼리면서 그 잡의 유효 그룹, 곧 프리셋의 지금 그룹(합성 포함)을 같은 트랜잭션에서 잡 행에 적는다. 후보를 고를 때의 그룹
  배제도 그 유효 그룹으로 한다. 호출자가 「표식 프리셋에서 유효 그룹과 범위로 가는 표」를 넘기고 SQL 은
  `COALESCE(c.concurrency_group, CASE c.preset WHEN ? THEN ? … END)` 로 본다. 표식 프리셋은 몇 개뿐이다. 그래서 표식을 단
  직후의 reload 에서도 그룹 없는 옛 대기 잡과 합성 그룹의 새 잡이 함께 돌지 않는다(RL48). 일반 잡은 제출 때의 그룹을 그대로
  쓴다(오늘 계약).

4판에서는 홀드의 그룹을 잡 행에서만 읽는다(결정 339-d, V1-3 · V2-8). 홀드 행의 그룹, 범위, 풀, `host_key` 는 닫는 트랜잭션에서 잡
행을 복사한다. 표식 잡(claim 때 `jobs.irreversible = 1`)의 그룹이 NULL 이었으면 claim 트랜잭션이 이미 유효 그룹(합성 포함)과
범위를 잡 행에 적었다(위 항목). 그래서 닫는 때 프리셋이 사라졌거나 표식이 빠졌거나 그룹 이름이 바뀌었어도 홀드는 claim 때 X 였던
잡의 그 그룹을 막는다.

레거시 잡(`jobs.irreversible` 이 NULL, 곧 v26 이전 claim)은 따로 다룬다. X 는 닫는 때 그 프리셋이 표식이거나 설정에 없으면 참이다.
모름을 닫힌 쪽으로 보는 것이다. 그룹은 잡 행의 그룹이고 NULL 이면 `irreversible:<프리셋 이름>` 과 `global` 이다. 같은 프리셋이
표식을 달고 다시 들어오면 그 합성 그룹과 이름이 같아 막힌다. claim 때 프리셋이 이미 없던 잡은 X 가 0 으로 얼지만 프로세스 없이
`preset_missing` 으로 끝나므로 홀드가 필요 없다(F90).

버린 안은 둘이다. `unknown:<job>` 합성 그룹은 아무 잡도 그 그룹이 아니라서 아무것도 막지 않는다. 열리는 쪽이다. 닫는 때 설정을
읽는 안(3판)은 프리셋이 사라지면 X 가 0 이 된다.

저장과 막기는 다음과 같다.

- 표 `irreversible_holds`(§4.9)는 잡 하나에 행 하나다. 4판: 그룹, 범위, 풀, `host_key` 는 만들 때 잡 행에서 복사하고 지금 설정은
  읽지 않는다(결정 339-d). 풀어도 지우지 않고 감사 이력으로 남긴다.
- claim SQL(`store.py:3316-3323` · `:3403-3407`)의 그룹 배제에 「열린 홀드」 조건을 같은 범위 규칙으로 더한다.

  ```sql
  NOT EXISTS (SELECT 1 FROM irreversible_holds h
              WHERE h.released_at IS NULL AND h.concurrency_group = c.concurrency_group
                AND <범위: h 의 scope/pool/host_key 로>)
  ```
- 오래된 잡 지우기(F77)는 열린 홀드가 있는 잡을 지우지 않는다.
  `AND id NOT IN (SELECT job_id FROM irreversible_holds WHERE released_at IS NULL)` 를 더한다.
- 사건: 만들 때와 풀 때 events 에 kind `hold` 를 한 건씩 넣는다. 내용은 `{action: "created"|"released", cause, release_kind,
  released_by, reason}` 이고 잡 문서가 읽는다(§4.8). 서버 로그에도 한 줄을 남긴다. 4판: 홀드를 만든 커밋 뒤에, 잠금 밖에서 버스
  사건 `irreversible_hold`(새 kind, `{job_id}`)를 낸다.
- 알림(4판, 결정 339-e): 3판의 「`[[notify]]` 는 부르지 않는다」를 바꿨다. 6차에서 Codex 와 논의해 기본 사건은 늘리지 않는다.
  - 홀드를 만든 잡의 잡 끝 알림은 이미 간다. 그 알림의 env 에 `RCM_HOLD=1` 과 `RCM_HOLD_GROUP=<그룹>` 을 싣고 본문에 한 줄 `group
    held — run rcm release-hold <job>` 을 싣는다. 이 한 줄은 훅 env `RCM_HOLD_NOTE` 와 URL 본문의 객체 `hold`(`group` · `note`)에 싣는다. `RCM_HOLD` 는 모든
    알림에 늘 있다(홀드가 없으면 `0`, `RCM_HOLD_GROUP` · `RCM_HOLD_NOTE` 는 빈 값 — `ENV_KEYS` 의 정확한 키 집합이 늘 같다).
    opt-in `irreversible_hold` 알림도 같은 세 키를 싣는다(`RCM_HOLD=1`). 메인 세션이 정했다. 기존 규칙도 이것으로 호출이 늘지 않은 채 홀드를 안다.
  - 모든 알림의 env 에 `RCM_EVENT`(`job_finished` 또는 `irreversible_hold`)를, URL 본문에 `event` 를 싣는다. 훅이 두 사건을 가를
    수 있게 하려는 것이다.
  - `irreversible_hold` 는 opt-in `on` 값이다. 규칙이 `on` 에 이 값을 적었고 `presets` 가 맞을 때만 그 규칙을 홀드마다 한 번
    부른다. 중복 막기 행은 `(job_id, "hold:" + 규칙 이름)` 이다. 규칙 이름에 `:` 가 없으므로 잡 끝 행과 겹치지 않는다(F86). 기동
    스캔에서 잡 끝 알림은 `hold:` 행을 세지 않는다(`list_unnotified_finished` 의 하위 질의에 `notify_name NOT LIKE 'hold:%'`).
    열린 홀드 가운데 그 규칙의 `hold:` 행이 없는 것은 보존 기간 안이면 보낸다.
  - opt-in 알림의 env 는 그 잡의 알림 env(잡 상태, 요약 등 오늘 키)에 `RCM_EVENT=irreversible_hold`, `RCM_HOLD_GROUP`,
    `RCM_HOLD_CAUSE`, `RCM_HOLD_WORKER` 를 더한 것이다. `RCM_HOLD_WORKER` 는 로컬 레인이면 빈 값이다. `RCM_SUMMARY` 는 「group G
    is held: job #N (preset) on W closed without a stop confirmation — check W, then rcm release-hold N」이다. 영어이고 경로가
    없다. URL 본문은 `{notify, event: "irreversible_hold", job, hold: {group, scope, cause, worker, since, release_command: "rcm
    release-hold N"}}` 다.
  - `on` 을 적지 않은 규칙의 기본은 그대로이고 `irreversible_hold` 는 기본에 들지 않는다. `on` 값 검증은 종료 상태와
    `irreversible_hold` 를 받는다.
  - 풀 때는 알리지 않는다. 사람이 풀었거나 워커가 확인한 것이고 사건과 감사 이력으로 남는다.
  - 버린 안은 둘이다. 새 설정 절 `[[notify_hold]]` 는 규칙과 훅을 두 벌 두게 한다. `irreversible_hold` 를 `on` 을 적지 않은 규칙의
    기본에 넣는 안은 4판 재료가 골랐다가 6차에서 버렸다. Codex 는 기존 훅이 같은 잡에 두 번 불려 호출과 부작용이 겹친다고 했다.

admin 이 푸는 길은 다음과 같다.

- `POST /jobs/{id}/release-hold` 를 더한다. 라우트 정규식 `_JOB_RE`(`server.py:256-260`)의 갈래에 `/release-hold` 를 넣는다.
  admin 토큰이 필요하고 본문 `{"reason": "<200자까지, 제어 문자 지움>"}` 은 선택이다. 성공하면 200
  `{job_id, group, released_at, released_by, release_kind: "admin"}` 이다. 열린 홀드가 없으면 409 `code: no_hold`(+`error`),
  잡이 없으면 404, admin 이 아니면 403 이다. 행 갱신과 사건은 한 트랜잭션에서 한다.
- CLI 는 `rcm release-hold <job> [--reason TEXT] [--json]` 이다. 새 최상위 명령이고 `rcm release` 의 하위 명령이 아니다(F74).
  종료 코드는 0 풀었다, 1 열린 홀드가 없다(409 `no_hold`), 2 사용법 · 401/403/404, 3 서버에 못 닿음이다. 사람용 줄은
  「released the hold on group G (job #N) — queued jobs in that group can start」이다. 도움말 한 줄은 「release an
  irreversible job's group hold after you checked the machine (admin token; not a store release)」이다.
- 풀기 전에 확인하는 일은 사람의 몫이다. rcm 은 그 머신에 프로세스가 남았는지 모른다. 그래서 홀드가 걸렸다.
  `docs/operating.md` 에 「그 워커 머신에서 `ps -g <pgid>` / 잡 로그의 마지막 줄을 보고 푼다」를 적는다.

버린 안은 다섯이다.

- 홀드 대신 G 를 길게 두기(327). 그래도 언젠가 풀린다.
- 홀드를 시간으로 풀기. 영속이 아니다.
- 그룹 대신 레인을 잡기. 다른 머신에서 같은 그룹이 돈다.
- 홀드를 `jobs` 의 칸으로 두기. 풀어도 남는 감사 이력이 없고 claim SQL 이 terminal 잡을 다시 훑는다.
- 새 사유 값 `group_held`. 오너가 `needs_action` 으로 정했고 그 값은 이미 행동 가능 목록의 맨 앞이다(F75).

### 4.13 되돌릴 수 없는 명령의 감독자 (결정 331 · 339-c)

- 모듈은 `remote_ci_monitor/job_supervisor.py` 이고 새로 만들며 표준 라이브러리만 쓴다. 러너는 `spec.irreversible` 이면 아래 argv
  를 오늘과 같은 `start_new_session=True`(setsid), 같은 `cwd` 와 `env`, 파이프로 띄운다. 4판에서는 `-m` 대신 모듈 파일의 절대
  경로로 띄운다(결정 339-c (4), F83). 명령은 받은 `env` 를 그대로 상속한다.

  ```text
  [sys.executable, "-I", <remote_ci_monitor/job_supervisor.py 의 절대 경로>, "--parent", <러너 pid>, "--grace", <grace_J>, "--", *argv]
  ```
- `-I` 를 지키는 이유는 감독자가 잡의 `env` 와 `cwd` = 워크스페이스에서 뜨기 때문이다. 잡 env 에는 프리셋 env, 입력, passthrough
  의 `PYTHONPATH` 등이 들고 워크스페이스는 요청자의 코드다. `-I` 가 없으면 워크스페이스나 `PYTHONPATH` 의 가짜 `signal.py` ·
  `subprocess.py` 가 안전 장치 자체를 바꾼다(F83 실측).
- `-m` 대신 파일 경로를 쓰는 이유는 `-I` 가 `PYTHONPATH` 를 버리기 때문이다. 설치하지 않고 소스 트리에서 `PYTHONPATH` 로 띄운 rcm
  에서는 `-m remote_ci_monitor.job_supervisor` 가 모듈을 못 찾는다. 파일 경로로 띄우면 패키지 import 가 필요 없다. 그래서 감독자
  파일은 표준 라이브러리만 import 하고 시험이 그 파일의 import 를 잠근다. 러너는 띄우기 전에 그 파일이 있는지 보고 없으면
  `RunnerError("launch_failed", error="SupervisorMissing")` 로 끝낸다. 깨진 설치이기 때문이다. 남는 한계는 zip 으로만 깔린 설치다.
  파일 경로가 없어 지원하지 않는다. rcm 은 wheel 을 풀거나 editable 로 설치하므로 파일이 있다.
- 감독자는 새 세션과 새 그룹의 리더다(pgid = 감독자 pid). 명령은 같은 그룹 안의 자식이다. 그래서 러너의 `killpg`(취소,
  시한, 정지, 자기 멈춤)가 오늘처럼 명령에 닿는다.
- 감독자는 아래 순서로 움직인다.
  1. SIGTERM 에 파이썬 처리기를 건다. 처리기는 받은 첫 시각을 단조 시계로 적는다. 처리기는 exec 에서 기본으로 돌아가므로
     명령에는 상속되지 않는다. `SIG_IGN` 은 상속되므로 쓰지 않는다.
  2. `getppid()` 가 `--parent` 와 다르거나 이미 SIGTERM 을 받았으면 명령을 띄우지 않는다. 4판에서는 표준 출력에 한 줄 `[rcm]
     supervisor: not started: parent_gone`(또는 `terminated`)을 쓰고 종료 `SUPERVISOR_NOT_STARTED_EXIT` = 125 로 끝난다(결정 339-c
     (3)). 닫힌 쪽이다. `Popen` 이 `OSError` 를 내면 같은 꼴로 `launch_executable_missing`, `launch_permission_denied`,
     `launch_failed` 가운데 하나를 쓰고 125 로 끝난다. 이 분류는 러너의 `_launch_error` 와 같다(`runner.py:71-82`).
     - 러너는 리더가 125 로 끝났고 **그리고** 리더의 첫 출력 줄이 그 표지일 때만 감독자가 명령을 띄우지 않았다고 읽는다. 그때 러너
       플래그(lost, cancelled, timed_out)가 있으면 그 결과다. 표지가 `launch_*` 면 그 코드의 `RunnerError` 로 끝나 감독자가 없을
       때와 같은 요약 코드가 된다. `parent_gone` 이나 `terminated` 인데 플래그가 없으면 `RunnerError("launch_failed",
       error="SupervisorNotStarted")` 다.
     - 명령이 스스로 125 로 끝나면 첫 줄이 표지가 아니므로 명령의 125 그대로다. 명령이 뜨지 않았을 때만 표지 줄이 나오므로 표지
       줄이 명령 출력보다 먼저 나올 수는 없다.
     - 125 를 고른 근거는 POSIX `env` 와 `nohup` 이 도구 자체의 실패에 쓰는 값이라는 점이다. 명령이 고르는 일이 드물고, 드물게
       겹쳐도 표지 줄이 가른다.
     - 버린 안은 둘이다. 종료 1(3판)은 명령의 실패와 같다. 별도 파이프 핸드셰이크(`pass_fds` 로 준비 바이트를 보냄)는 더
       정확하지만 러너가 다룰 fd 가 하나 더 생긴다. 표지 줄로 같은 것을 가를 수 있다.
  3. `Popen(argv)` 로 명령을 띄우고 표준 입출력을 상속한다. 4판: `Popen` 이 돌아오면, 곧 명령의 exec 가 성공하면 SIGTERM 이 이미
     적혀 있는지 본다(결정 339-c (1), V2-2). 적혀 있으면 그룹에 SIGTERM 을 한 번 보낸다. 감독자 자신은 처리기를 걸어 두었으므로
     적기만 한다. 그래서 시작 확인 뒤, Popen 전의 창에 온 SIGTERM 도 명령에 닿는다. 그 창의 SIGTERM 은 fork 와 exec 사이의
     자식에게 가면 사라지므로 Popen 뒤에 다시 보내는 것이 유일한 길이다. 처리기를 걸기 전, 곧 인터프리터가 뜨는 중에 온 SIGTERM 은
     기본 동작으로 감독자를 끝낸다. 명령이 없으니 안전하다.
  4. 막는 `waitpid` 를 도는 스레드를 둔다. 자식이 끝나면 곧바로 깨운다. 폴링 sleep 으로 기다리면 잡 끝이 폴링만큼
     늦는다(F78).
  5. 본 루프는 `Event.wait(SUPERVISOR_POLL_SECONDS)` 마다 `getppid()` 를 본다. 부모가 죽으면 launchd, init, subreaper 아래로
     옮겨지므로 「1 이 됨」이 아니라 「기대와 다름」으로 판정한다. 바뀌었으면 그룹에 SIGTERM 을 보낸다. 이미 받은 SIGTERM 이
     있으면 보내지 않는다. 첫 SIGTERM 시각에 `--grace` 를 더한 때에 그룹에 SIGKILL 을 보내고 감독자 자신도 함께 끝난다.
  6. 자식, 곧 명령이 끝나면 아래와 같이 한다(4판, 결정 339-c (2), V1-2 · V2-3).
     - SIGTERM 을 받은 적도 부모가 바뀐 적도 없으면 오늘처럼 같은 종료 상태로 끝난다. 종료 코드가 c 면 `exit(c)` 하고, 신호 s 로
       죽었으면 s 를 기본 처리로 되돌리고 자기에게 보내서 러너가 오늘과 같은 `-s` 를 보게 한다. 남은 멤버는 러너의 S7 이 거둔다.
     - 받았거나 바뀌었으면 남은 멤버를 본다. 남은 멤버는 `LC_ALL=C ps -A -o pid= -o pgid=` 에서 pgid 가 자기 pgid 인 것 가운데
       자기와 그 `ps` 를 뺀 것이다(F84). 없으면 같은 종료 상태로 끝난다.
     - 있으면 첫 SIGTERM 에 `grace_J` 를 더한 때까지 `SUPERVISOR_POLL_SECONDS` 마다 다시 보고, 그때도 남으면 그룹에 SIGKILL 을
       보낸다. 감독자도 함께 끝나고 리더 종료는 −9 다. 부모가 바뀌었는데 SIGTERM 을 보낸 적이 없으면 지금 그룹에 SIGTERM 을 보내고
       그 시각을 첫 SIGTERM 으로 둔다.
     - `ps` 를 못 돌리면 「남았다」로 본다. 한 번 쓸 때 `ps` 는 많아야 `grace_J / 0.25` 번 돈다. 기본값이면 40번이고 한 번에
       0.013초다(F84).
- 값은 이 Mac 에서 쟀다(F78). `SUPERVISOR_POLL_SECONDS` 는 0.25 다. 감지는 0.31초 이하이고 30초 잡의 CPU 는 0.021초로 1초
  폴링과 같다. 0.1초로 줄이면 CPU 가 0.026초로 늘고 감지 이득은 0.2초다. 종료 대기는 잡의 `grace_J` 다. 러너의 취소와 같은
  계약이라 명령이 SIGTERM 을 받고 정리할 시간을 같게 받는다. 측정한 넘침은 폴링 + 0.05초 이하다. 기동 비용은
  `python -I` 와 표준 라이브러리를 합쳐 약 0.017초다.
- 러너 쪽에서는 Popen 뒤 `proc.json` 의 `pgid` 가 감독자 pid 다. 리더인 감독자가 끝나면 S7 거두기를 한다(4판, 결정 339-c (2)).
  러너가 이미 SIGTERM 을 보냈으면 SIGTERM 을 다시 보내지 않고 그 시각에 `grace_J` 를 더한 때 SIGKILL 한다. 보내지 않았으면 지금
  SIGTERM 을 보내고 `grace_J` 뒤 SIGKILL 한다. 2초 안에 ESRCH 를 확인하면 `group_gone` 이다. 감독자가 SIGKILL 로 끝나 리더 종료가
  −9 여도 결과는 러너 플래그(취소, 정지, 자기 멈춤, 시한)가 정한다.
- 일반 잡에는 쓰지 않는다. 결정 253 에 따라 일반 잡은 워커가 죽어도 계속 돈다. 로컬 레인 표식 잡에는 쓴다(F76). 이때 부모는
  서버 프로세스다.
- 감독자가 막지 못하는 것은 셋이고 330 의 홀드가 덮는다(§9). 감독자 자체가 죽는 경우(W13), 명령이 세션을 빠져나가는
  경우(§1.3), 부모가 죽은 뒤 감독자가 SIGKILL 을 보내지 못하는 경우다. 같은 사용자의 프로세스라 권한 문제는 없다. 남는
  것은 OOM 등으로 감독자가 같이 죽는 경우다.
- 버린 안:
  - Linux `PR_SET_PDEATHSIG`. Linux 에만 있고 부모 스레드가 끝날 때 발동하므로 레인 스레드가 바뀌면 잘못 쏜다.
  - macOS `kqueue` `NOTE_EXIT`. macOS 에만 있어 코드가 두 갈래가 된다.
  - 부모가 쥔 파이프의 EOF. 이식성은 좋지만 M5q `execv` 가 비상속 fd 를 닫아 EOF 를 낸다. 오너가 정한 `getppid()` 와도 다른
    길이다.
  - 감독자가 명령을 다른 그룹에 두기. 러너의 `killpg` 가 명령에 닿지 않아 러너를 바꿔야 한다.
  - 감독자가 폴링 sleep 으로 자식 끝을 기다리기. 잡 끝이 0.25초까지 늦는다(F78).
  - 일반 잡에도 감독자를 쓰기. 결정 253 에 어긋난다. 워커가 죽으면 일반 잡까지 끝난다.
  - 4판: 쓸기를 멤버마다 `os.kill(pid)` 로 하기. 목록을 읽고 kill 하는 사이에 pid 가 재사용되면 남의 프로세스를 죽인다. `killpg`
    는 감독자가 사는 동안 pgid 가 재사용되지 않아 안전하다.
  - 4판: macOS `pgrep -g`. 부른 프로세스의 조상을 빼고 내서 두 OS 의 뜻이 다르다(F84).
  - 4판: 쓸기 시한을 명령이 끝난 때에 `grace_J` 를 더한 때로 두기. 3판 S7 과 같은 2·grace 문제가 생긴다.

## 5. 작업 분할과 병렬

PR 하나에 브랜치 하나, 워크트리 하나다. 같은 물결의 PR 은 동시에 진행한다. 공유 파일(`shared`)에는 더하기만 한다. 뒤에
머지하는 PR 이 dev 를 합친 뒤 맞춘다. 레인 추천의 근거는 workplan 스킬의 「Lane」 조건(DB, 와이어, 동시성, 웹 UI)이다.

| PR | 브랜치 | 내용 | 시작 조건 | 물결 | 같이 도는 것 | owns | shared | 겹치는 파일과 머지 순서 | lane |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5tb-result-loop-workplan` | 이 명세 + PLAN.md 편집 열 자리: ① `PLAN.md:1` 머리 판 `(v2.13, 2026-11-25)` → `(v2.14, <머지 날짜>)` ② `PLAN.md:14` 뒤에 `> **v2.14**` 문단(M5t-b 결과 고리 · 결정 256~279 · 320~339 · 350 · DB v26 · 워커 프로토콜 2 · 그룹 홀드 · 감독자 · 명세 `docs/m5tb-workplan.md`) ③ `PLAN.md:89` 잡 모델 그림 `lost(서버 재시작 중 죽음)` 에 원격 잡의 `lost` 원인(유예 만료 · 자기 멈춤 · 펜싱 · 재시작 이어받기) ④ `PLAN.md:121` 「잡은 서버 재시작을 넘어 살아남는다(… `running`·`cancelling` 이던 것은 `lost`)」 를 로컬 레인 잡으로 한정, 원격 잡은 끊김 유예. 표식 로컬 잡은 그 `lost` 에 그룹 홀드 ⑤ `PLAN.md:293` 시작 정리 문장에 같은 한정 ⑥ `PLAN.md:600` M5b-2 줄의 「timeout → down · 잡 lost · 재등록 = 옛 잡 lost」 에 「M5t-b 가 바꿈: timeout → down 만 · 잡은 유예 · 재등록은 명시한 이전 세션과 레거시 잡만」 ⑦ `PLAN.md:628` M5t-b 항목 「(명세 예정)」 → 명세 경로 · 결정 번호 · DB v26 · 한 문장, `PLAN.md:753` 결정 색인 「248 a~d · g 와 250 은 M5t-b 명세에 … 249 는 두 명세에 나눠 실을 예정」 → 「256~279 · 320~339 · 350 은 `docs/m5tb-workplan.md` §11 에 있다. 248 a~d · g · 249 의 v26 부분 · 250 은 그 §11 대응표가 이어받았다」(M5t-c 몫 문장은 M5t-c PR 이 고침) ⑧ 3판 `PLAN.md:129` 이유 목록에 `worker_unreachable_grace`(원격 잡의 워커와 연락이 끊겨 유예 중 — M5t-b 결정 276)를 더하고, 행동 가능 순서를 `needs_action → worker_down → worker_unreachable_grace → no_capable_machine → …` 로, `needs_action` 뜻에 「또는 그 잡의 그룹이 되돌릴 수 없는 잡의 확인 없는 닫힘으로 잡혀 있다(M5t-b 결정 330)」 ⑨ 3판 `PLAN.md:505` 웹 「Needs a look」 순서에 같은 자리(F70, V1-6) ⑩ 3판 `PLAN.md:127` 레인 · 그룹 줄에 「표식 잡이 확인 없이 닫히면 그 그룹은 `rcm release-hold` 까지 막힌다(M5t-b 결정 330)」 | — | 0 | M5t-c 명세 PR #269 | `docs/m5tb-workplan.md` | `PLAN.md`(위 열 자리. M5t-c 명세 PR 도 `:1` · `:629` · `:753` 를 고친다) | M5t-c 명세와 PLAN 머리 · 색인 줄이 겹침 → 먼저 머지한 쪽 뒤에 다른 쪽이 dev 를 합쳐 다시 맞춤(판 번호는 뒤에 머지하는 쪽이 v2.15) | short — 문서만, `src/` 변경 0 |
| PR1 | `feat/worker-session-fencing-grace` | §4.1~§4.4(7 의 큐 보내기 빼고) · §4.7 · §4.12(서버 쪽) · §4.13: DB v26 전부(`orphan_unverified` 칸 · `irreversible_holds` 표 포함), 세션 · 펜싱 · 좁은 확인 · 이름 대조 403 · register 순서(`pair_proof` 자리 비워 둠) · 첫 등록의 레거시 닫기, `_heard_lock` · `heard` · `heard_legacy` · 서버 유예(단조) · 취소 미확인 범위, 얼림(로컬 레인 `irreversible` 포함), heartbeat `closed_jobs`(`had_cancel`) · 전체 기한, 감시 스레드 · m · L(3판 공식) · G 하한 · 기본 G = max(600, L + 35) · 표식 잡 grace 상한, 관찰자 `self_stop` · `closed_stop` · `spawning` · `spawned`, 러너의 Popen 직전 확인 · 단조 시계 · 감독자 감싸기 · 표식 잡 거두기 · `group_gone`, 감독자 모듈, 세션 폴더(`lock` · `owner.json` · `ok_at.json` · `claim.json` · `proc.json` 두 번 쓰기), Linux `/proc` · `LC_ALL=C TZ=UTC ps` 신원, 고아 정리 · 재확인 · `previous_sessions` · `unverified_sessions` · `orphan_unverified`, 그룹 홀드(만들기 · 확인으로 풀기 · claim 배제 · 오래된 잡 지우기 제외 · 사건 · 합성 그룹 · `POST /jobs/{id}/release-hold` · `rcm release-hold`), 로컬 `recover_on_start` 홀드, 프로토콜 2 · 등록 전 확인(폴더는 검사 뒤) · 종료 0/3 · `protocol_required` 문구, 레거시 호환, `LOST_CODES` · `orphan_unverified` 와 그 번역, 설정 키 둘, 문서(`docs/configuration.md` 1185 · 1386 근처, `docs/operating.md:1004` · 홀드 풀기 절, 템플릿 둘), 오늘 계약 시험 고쳐 쓰기(§7.3). 로그 라우트는 세션 헤더만 검사하고 오늘처럼 덧붙임(오프셋 헤더는 읽지 않음, §4.5 PR 경계), 못 보낸 보고는 오늘처럼 메모리. 4판: heartbeat `seq` · `claims_settled` · claim `claim_seq`(워커 · 서버 · `jobs.claim_seq`), T5 의 새 규칙과 표식 잡 홀드, `_fresh_claims` 는 레거시만, 세션별 잠금 · janitor 틱 상한, 감독자 (1)~(4)(Popen 창 전달 · 명령 끝 뒤 쓸기 · S7 시계 · 종료 125 + 표지 · `-I` 파일 경로), 홀드 그룹은 잡 행에서 · 레거시 규칙, 홀드 알림(`[[notify]] on` 값 · 사건 · env · 본문 · 기동 스캔), 홀드의 기계 판독 표면(`compute_queue` 의 `needs_action` · `action.code` `irreversible_hold` · `blocked_by`, `/api/status` · `/api/health` `irreversible_holds`, 잡 문서 `hold`) | PR0 머지 **그리고** M5t-a PR1~PR7 전부 dev 머지(= v0.11.0 에 들 내용 전부, F54 — PR3 · PR4 · PR6 이 `config.py` · `capabilities.py` · `server.py` · `cli.py` · `docs/configuration.md` · `docs/operating.md` 를 고친다) | 1 | — | `store.py` `remote_workers.py` `server.py` `capabilities.py` `config.py` `core/model.py` `core/outcome.py` `client.py` `remote_worker.py` `runner.py`(Popen 직전 확인 · `mono_fn` · 감독자 · `spawning` · `spawned` · 거두기) `worker.py`(로컬 관찰자의 두 갈래 · 로컬 claim 얼림) `job_supervisor.py`(새) `cli.py`(cmd_worker · `release-hold`) `web/i18n.js`(outcome 항목) `templates/server.toml` `examples/server.toml` `docs/configuration.md` `docs/operating.md` `tests/tcp_relay.py`(새) `tests/test_worker_session_m5tb.py`(새) `tests/test_worker_selfstop_m5tb.py`(새) `tests/test_job_supervisor_m5tb.py`(새) `tests/test_irreversible_hold_m5tb.py`(새) `tests/test_e2e_worker_grace_m5tb.py`(새) `tests/test_worker_lost.py` `tests/test_e2e_worker.py` + 세션을 싣게 고치는 워커 시험 도우미 `core/queue.py`(홀드 사유) `core/status.py`(최상위 키 · 대기 행 `action`) `notify.py` `core/notify.py` `events.py` `tests/test_hold_notify_m5tb.py`(새) `tests/test_hb_order_m5tb.py`(새) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py`(새, PR1 이 만들고 뒤 PR 이 더함) `tests/test_examples.py` `tests/test_worker_api.py`(register `:542-549` · heartbeat `:1204` 잠금에 키 더하기) `tests/test_worker_client.py`(`:183-190`) `tests/test_config.py`(`:905`) `tests/test_outcome.py`(`PLAUSIBLE` `:48-85` · `PINNED` `:88-214` · 전수 잠금 `:220-231` · 필수 인자 `:511` · 번역 `:553`, F72) `tests/test_runner.py`(시험 도우미 `group_gone` `:241` 을 같이 씀) `tests/test_status_schema.py`(최상위 키 `:261-268`) `tests/test_notify_rules.py`(ENV_KEYS `:59-88`) | M5t-a 는 시작 전에 다 머지돼 있어 겹침 없음. M5t-c 의 register(`pair_proof`) · DB v27 은 이 PR 뒤(M5t-c 결정 299) | full — DB v26 · 워커 프로토콜 · 동시성(heartbeat · claim · janitor · 신호 · 감독자) · 새 admin 라우트 · 알림 · 상태 키 |
| PR2 | `feat/worker-log-offset-outbox` | §4.4 의 7(죽은 세션 D 의 큐 · 응답 표 · 등록 대기 300초) · §4.5 · §4.6: 스트림 스풀 · `outbox.json`(`acked`) · **잡마다** 보내는 스레드 · 순서 있는 큐 · 빈 본문 질의 · 응답표 · finish 상한 · 재전송 창, 서버 로그 쓰기 절차(잡별 잠금 · 자리 쓰기 · 되자르기 · 짧은 파일 · 「세션이 아직 현재」 커밋 조건 · 오프셋 헤더 400) · 기동 자르기 · 끝난 잡의 짧은 파일 점검, `log_complete` · `late_result`(트랜잭션 + 홀드 풀기) · `finished_at` 자르기 · 산출물 재업로드와 `ready` 조건(파일까지), 준비 단계 `snapshot_unreachable`, M5q exec 뒤 큐 이어 보내기, 문서(`docs/configuration.md` 의 원격 워커 로그 절). 4판: 로그 읽는 쪽 `log_file_bytes` 상한(`log_bytes` · `tail_lines` 끝 길이 인자) | PR1 머지 | 2 | PR3 | `store.py` `remote_workers.py` `remote_worker.py` `client.py` `server.py`(log 라우트 · `_terminal_view` · 기동 자르기 · 끝난 잡 점검) `core/status.py`(`recent_json`) `docs/configuration.md` `tests/test_worker_log_offset_m5tb.py`(새) `tests/test_worker_outbox_m5tb.py`(새) `tests/test_e2e_worker_outbox_m5tb.py`(새) `worker.py`(`tail_lines` 끝 길이 인자) `tests/test_log_read_cap_m5tb.py`(새) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py` `tests/test_status_schema.py`(`RECENT_KEYS` `:91`) `tests/test_status_outcome.py`(`NEW_RECENT` `:162`) | PR3 와 owns 겹침 0(F87 · `tools/dev_batch.py` 로 확인). 머지 순서 PR2 → PR3(RL28 · RL29 의 e2e 가 PR2 키를 읽는다) | full |
| PR3 | `feat/queue-show-unreachable-worker` | §4.8 의 사람용 화면: 사유 `worker_unreachable_grace` · ETA null · `ACTIONABLE` 두 목록과 잠근 시험(F58), 문구(웹 · `rcm top` · `rcm wait`), `log_complete`=false · `late_result` 사람용 줄, 홀드의 사람용 화면(웹 「Needs a look」 · 칩, `rcm check` 경고 줄, `rcm doctor` 줄 · `--json` 키, `rcm wait` 줄), 문서(`docs/operating.md` 두 절) | PR1 머지 | 2 | PR2 | `core/model.py` `core/queue.py`(`worker_unreachable_grace`) `core/render_text.py` `cli.py`(wait · logs · `rcm check` 줄) `doctor.py` `core/doctor.py` `src/remote_ci_monitor/web/app.js` `web/i18n.js` `docs/operating.md` `tests/test_queue_unreachable_m5tb.py`(새) `tests/test_hold_display_m5tb.py`(새) `tests/web/unreachable_m5tb.test.js`(새) `tests/test_action_marker_m5s.py`(`:139-141`) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py` `tests/test_queue_m5n.py`(`:135`) `tests/test_render_m5n.py`(`:199`) `tests/web/stall.test.js`(`:192-194`) `tests/web/m5n.test.js`(`:38-41`) `tests/test_doctor_cli.py`(`TOP_KEYS` `:993-1017`) | PR2 뒤 머지 | full — 웹 UI |
| PR4 | `test/acceptance-worker-result-loop-live` | 실기 수용: 하네스 `scripts/acceptance/m5tb_live.sh`(시험 서버 · 가짜 `sh -c` 표식 프리셋 · 표준 라이브러리 TCP 중계), §7 e2e 행의 실기 확인(RL1 · RL2 · RL10 · RL15 · RL34 · RL40 · RL42 · RL43 · RL50 · RL51 우선 — RL43 은 워커를 실제로 `kill -9` 하고 감독자가 명령을 끝내는지 · 홀드가 걸리는지 · `rcm release-hold` 로 풀리는지), 감독자 값(F78)을 이 하네스로 한 번 더 재서 보고서에, 보고서 `docs/acceptance/reports/<날짜>-m5tb-acceptance.md`. Linux 머신이 있으면 RL15 · RL31 의 `/proc` 신원과 RL43 을 그 머신에서 한 번 | PR3 머지 | 3 | — | `scripts/acceptance/m5tb_live.sh`(새) `docs/acceptance/reports/…`(새) | `CHANGELOG.md` | — | short — `src/` 변경 0 |

- PR1 을 더 쪼개지 않는다. dev 의 e2e 는 같은 트리의 서버와 워커를 함께 띄운다. 서버만 세션을 요구하면 워커 쪽이 없는 동안
  e2e 가 빨갛다. 거꾸로 하면 「선택」을 「필수」로 뒤집는 셋째 PR 이 필요하다. 그래서 이 안은 거절했다.
- 3판에서 로그 라우트의 PR 경계를 정했다(V1-4). 「세션이 있는데 오프셋이 없으면 400」 규칙은 PR2 가 켠다. PR1 의 워커는
  세션 헤더만 싣고 PR1 의 서버는 오프셋 헤더를 읽지 않는다. PR1 과 PR2 는 같은 릴리스로만 나간다(§6 #13 · #14).
- 3판의 그룹 홀드는 둘로 나눈다. 서버 쪽은 PR1 이 만들고 보이는 쪽은 PR3 이 만든다. 4판에서는 PR1 만 머지된 dev 에서도 대기 잡
  사유가 `needs_action` 이고 알림이 간다. 웹, `rcm check`, `rcm doctor`, `rcm wait` 의 사람용 줄만 PR3 을 기다린다. 막히는 것
  자체는 PR1 의 claim SQL 이 지킨다.
- 4판(V1-4, 결정 350): 3판은 PR2 와 PR3 이 둘 다 `core/status.py` 를 owns 로 가져 `tools/dev_batch.py` 가 같은 물결을
  거절했다(F87). 또 PR3 의 홀드 화면은 `server.py` · `store.py` 연결이 필요했는데 그 파일은 PR2(물결 2)와 PR1 이 가졌다. 4판은
  홀드의 기계 판독 표면, 곧 store 질의, server 연결, `/api/status` · `/api/health` 키, 대기 행 사유, 잡 문서를 홀드를 만드는 PR1
  로 옮겼다. PR3 에는 사람용 그리기만 남아 `server.py`, `store.py`, `core/status.py` 가 필요 없다. PR1 은 물결 1 에 혼자라 그
  파일들을 가져도 겹침이 없다.
  - 버린 안은 둘이다. PR2 다음에 PR3 을 차례로 하는 안은 물결이 하나 늘고 오너의 「dev batch 로 나눠서」와 어긋난다.
    `core/status.py` 를 둘 다 shared 로 두는 안은 맞지 않는다. 더하기만 하는 shared 는 등록부용인데 이 경우는 두 PR 이 함수 몸통을
    고친다.
- 4판(339-e): 홀드와 그 알림, 기계 판독 표면은 PR1 한 PR 에 들고 사람용 화면(PR3)은 같은 릴리스에만 든다(§6 #13).
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
| 0 | 이 명세 작성(1판 · 2판 · 3판 · 4판) | PR0 | — | [ ] |
| 1 | 격리 검증 라운드(HIGH 0 까지), §12 기록 | PR0 | 0 | [ ] |
| 2 | 결정 페이지: 위임 질문(메인 세션 + Codex, 오너 위임) | — | 1 | [ ] |
| 3 | PR0 머지(PLAN.md 열 자리 반영 포함) | PR0 | 1 · 2 | [ ] |
| 4 | M5t-a PR1~PR7 전부 dev 머지 확인(v0.11.0 에 들 내용) | — | — | [ ] |
| 5 | PR1 세션 · 펜싱 · 유예 · 감시 스레드 · 닫힌 잡 통보 · 세션 폴더 · 고아 · 프로토콜 · DB v26 · Popen 직전 확인 · 감독자 · 그룹 홀드(서버 쪽) · `rcm release-hold` · 4판: heartbeat 순번 · `claims_settled` · T5 홀드 · 세션별 잠금 · 감독자 (1)~(4) · 홀드 알림 · 홀드의 상태/헬스 키 · 대기 사유 | PR1 | 3 · 4 | [ ] |
| 6 | PR2 로그 쓰기 절차 · 잡마다 보내는 스레드 · 죽은 세션 큐(300초 상한) · finish 상한 · 늦은 결과 · 산출물 · 끝난 잡 짧은 파일 점검 · 4판: 로그 읽는 쪽 상한 | PR2 | 5 | [ ] |
| 7 | PR3 화면 사유 · 사람용 줄 · 잠근 시험 목록 · 홀드의 사람용 화면(웹 · `rcm check` · `rcm doctor` · `rcm wait`) | PR3 | 5 | [ ] |
| 8 | PR2 머지 → PR3 dev 합쳐 RL28 · RL29 · RL45 다시 돌린 뒤 머지 | PR2 · PR3 | 6 · 7 | [ ] |
| 9 | 새 변이 68개(§7.4)가 `scripts/mutcheck.py` 에 들어갔는지 | PR1 · PR2 · PR3 | 5 · 6 · 7 | [ ] |
| 10 | PR4 실기 수용과 보고서(감독자 값 재측정 포함) | PR4 | 8 | [ ] |
| 11 | M5t-c 명세의 DB v27 이 v26 위인지 확인 | — | 5 | [ ] |
| 12 | 결정 번호: M5t-b 256~279 · 320~339 · 350~359, M5t-c 280~319 · 340~349 — M5t-c 결정 317 · 343 은 `docs/m5tc-workplan.md` 의 317 행 · 343 행(PR #269)으로 가리킴(§1.2) | — | 0 | [ ] |
| 13 | 릴리스 묶음: PR1 · PR2 · PR3 이 **모두** dev 에 머지되기 전에는 릴리스(버전 올림 · main PR)를 만들지 않는다 — 홀드(PR1)와 그 사람용 화면(PR3), 로그 라우트 경계(PR1 · PR2). CHANGELOG `[Unreleased]` 정리 | PR4 | 10 | [ ] |
| 14 | main 릴리스 · 버전업(메인 세션, 오너가 목표에 넣어 승인 2026-11-26) | — | 13 | [ ] |
| 15 | 운영 반영(큐가 빌 때, 메인 세션) | — | 14 | [ ] |
| 16 | 앱 프로젝트에 `irreversible` 달기 — 그 프리셋을 읽는 서버가 모두 이 기능 버전 이상인지 확인한 뒤(F23). 그룹이 없으면 합성 그룹이 생김을 그쪽에 알림(결정 330) | — | 15 | [ ] |
| 17 | 운영 서버의 남은 원격 잡 수(끝난 잡 짧은 파일 점검 비용, F79) — 메인 세션이 운영 읽기 경로로 잰다 | — | 6 | [ ] |
| 18 | 문서: `docs/configuration.md` 의 `irreversible` 절에 「`irreversible` 프리셋에는 세션을 빠져나가는 명령(`setsid` · 이중 fork 데몬)을 쓰지 않는다 — rcm 은 그 프로세스를 볼 수 없어 이 경우는 fail-open 이다」를 적고 `tests/test_docs_m5tb.py` 가 잠근다(5차 위임 결정 (e), §9, RL49) | PR1 | 3 · 4 | [ ] |
| 19 | (새) 감독자 4판 값 재측정 — `-I` 파일 경로 기동 · `ps` 쓸기 비용(F83 · F84)을 실기 하네스로 한 번 더, Linux 머신이 있으면 `ps -A -o pid= -o pgid=` 꼴 확인 | PR4 | 10 | [ ] |
| 20 | (새) M5t-c 343 맞물림: M5t-b PR1 머지 뒤 M5t-c PR1 이 `session_fenced` · `protocol_required` 를 MAC 다는 거절 목록에 더했는지, 짝지은 워커의 W5 가 검증 때만 종료 0 이고 W6 이 검증 때만 종료 3(검증 안 되면 M5t-c J13)인지(M5t-c 쪽 시험) — 메인 세션 확인 | — | 5 | [ ] |

## 7. 시험 계획

### 7.1 시나리오 표

아래 용어는 표에서만 쓴다. G 는 `disconnect_grace_seconds` 이고 시험 서버는 `worker_timeout_seconds` 보다 조금 큰 값으로
줄여 쓴다. 표식 프리셋은 `irreversible = true` 를 단 프리셋이고 일반 프리셋은 표식이 없는 프리셋이다. 분단은 서버는 살아
있고 워커 요청만 응답이 없는 상태이며 TCP 중계를 멈춰 만든다. 서버 정지는 연결 거부다. 세션은 워커 프로세스 한 번이다.

<!-- scenario-prefix: RL -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| RL1 | [전이 T2·T3·W3 · 253 · 320] 표식 프리셋(가짜 `sh -c sleep`)의 잡이 원격 워커에서 `running`. 분단(중계가 응답을 붙잡아 heartbeat 이 매번 요청 시한까지 매달림)이 G 를 넘겨 이어진다 | 워커의 감시 스레드가 `ok_at + G − m` 뒤 첫 틱에 잡의 프로세스 그룹을 끝낸다(pid 소멸). 서버가 `lost` 로 닫기 전에 이미 죽어 있다(워커 로그의 멈춤 시각 ≤ DB `finished_at`, 차이 ≥ 0). 연결이 돌아온 뒤 최종 상태는 `lost` 하나, `rcm wait` 종료 3, `failed_step` · `last_step` 없음, `summary_code` 는 `worker_unreachable`(서버가 먼저) 또는 `worker_self_stopped`(보고가 먼저) — `cancelled`(2)가 아니다(F66). 서버가 먼저면 그 닫힘에 홀드가 걸리고 돌아온 워커의 finish(`group_gone: true`)가 그 홀드를 푼다. 근거: 결정 253 · 320 · 330 · 334 · `PLAN.md:118, 220` | e2e: 워커 프로세스 + TCP 중계 정지, `os.kill(pid,0)`, DB `jobs`, 종료 코드 | PR1 |
| RL2 | [전이 T3·W3 · 253] RL1 과 같은 잡. 이번에는 서버 정지(재배포)가 G 를 넘겨 이어진 뒤 서버가 다시 뜬다 | 워커는 서버 정지도 끊김으로 보고 `G − m` 에 스스로 멈춘다(서버가 꺼진 채로 pid 소멸). 서버가 뜬 뒤 그 잡은 `lost` 한 번, 종료 3. 서버가 꺼져 있던 동안은 유예로 세지 않으므로 기동 직후 틱이 닫지 않고, 워커가 돌아와 보낸 `worker_self_stopped` 가 닫거나 기동 + G 에 `worker_unreachable` 로 닫힌다. 같은 잡을 두 번 닫거나 사유를 덮어쓰지 않는다. `worker_unreachable` 로 닫히면 그 닫힘에 홀드가 걸리고, 돌아온 워커의 finish(`group_gone: true`)가 푼다(결정 330). 근거: 결정 253 · 266 · 330 | e2e: 서버 stop/start, pid, DB, 종료 코드 | PR1 |
| RL3 | [전이 T2·T3 · 253] RL1 의 분단 중(G 안과 밖 각각) 같은 `concurrency_group` 으로 새 제출을 낸다(supersede 켜진 경우 포함) | 첫 잡이 `lost` 로 닫히기 전에는 둘째 잡이 `running` 이 되지 않는다(`queued`, `blocked_by_group`). supersede 면 첫 잡은 `cancelling` 으로 남아 그룹을 잡는다. 둘째가 시작하는 시각에 첫 잡의 프로세스는 이미 없다. 두 프로세스가 동시에 살아 있던 구간 0. 근거: 결정 253 · `PLAN.md:127` | e2e: 두 잡의 pid 생존 구간 비교, `jobs.state` | PR1 |
| RL4 | [전이 T2/T3 경계 · 253 · 266 · 328] G 만료 경계(주입 시계로 ±1 틱, 여러 번 반복)에서 분단이 풀려 서버 janitor 의 만료 판정과 워커의 첫 heartbeat · 자기 멈춤이 겹친다. 표식 · 일반 각각 | 잡의 최종 상태가 정확히 하나다. 표식은 (결과 확정 또는 `lost`, 프로세스 종료), 일반은 (결과 확정 또는 `lost`, 프로세스는 계속). `lost` 가 된 뒤 같은 잡이 `succeeded` 등으로 뒤집히지 않는다(늦은 finish 는 409 `job_closed`, PR2 부터 `late_result` 1건). 표식 프리셋에서 (`lost`, 프로세스 생존) 조합이 경계 어느 쪽에서도 나오지 않는다. 근거: 결정 253 · 256 · 266 · `PLAN.md:118` | 단위: 서버 `mono_fn` · 워커 시계 주입, 경계 반복, DB + pid 상태 | PR1 (상태 하나 · 뒤집히지 않음) · PR2 (`late_result`) |
| RL5 | [전이 T3·W4 · 253 · 256] 일반 프리셋의 원격 잡이 `running`, 연결이 G 를 넘겨 끊긴다 | 서버는 G 에 `lost`(`worker_unreachable`), `rcm wait` 종료 3(1 아님). 워커는 잡을 죽이지 않고 계속 돌린다(pid 생존, 워커 로컬 `log.txt` 증가). 재연결 뒤 로그 조각은 409 `job_closed` 이고 프로세스를 죽이지 않는다(3판: `closed_by_server` 가 `should_cancel` 을 세우지 않는다 — F66, 결정 334). 홀드 0(일반 잡). 끝나면 finish 를 보내고 서버는 잡 상태를 바꾸지 않은 채 `late_result`(outcome · exit_code · finished_at · received_at · log_complete)를 잡 문서에 한 번 남긴다. 근거: 결정 253 · 256 | e2e: pid 생존, 워커 로컬 로그, DB, 종료 코드, `GET /jobs/{id}` 의 `late_result` | PR1 (유예 · 계속 돎) · PR2 (`late_result`) |
| RL6 | [전이 T1·T2 · 기준선] (a) 끊김 없는 원격 잡의 성공 · 실패 · 시간초과 · 취소 각 한 번 (b) 분단을 `worker_timeout_seconds` 보다 길고 G 보다 짧게 둔 뒤 푼다(표식 · 일반 둘 다) | (a) 종료 코드 계약 그대로: 성공 0 · 실패 1 · 시간초과 2 · 취소 2, wait JSON 의 기존 키 불변(더한 키만 있음). (b) 잡은 끊긴 내내 `running` 이고 `worker_timeout_seconds` 에 `lost` 가 되지 않는다. 같은 세션으로 이어 보고하고 프로세스는 안 죽는다. 서버 `jobs/N/log.txt` 의 sha256 이 워커 스트림 스풀과 같다(중복 · 빈틈 없음). 근거: 결정 253 · `PLAN.md:118` | e2e: TCP 중계 정지/재개, sha256, 종료 코드, wait JSON | PR1 (상태) · PR2 (로그 sha) |
| RL7 | [서버 정지 T1·T3] 원격 잡과 로컬 레인 잡이 같이 `running`. 서버를 (a) `worker_timeout_seconds` 초과 G 미만 (b) G 초과로 정지했다가 올린다. 그동안 `rcm wait` 하나가 대기 중 | (a) 재기동 후 첫 janitor 틱(5초)과 그 뒤에도 원격 잡은 `running` 이고 워커의 finish 가 확정되어 종료 코드가 실제 결과다. (b) 유예는 서버 기동 시각부터 다시 센다. 기동 직후 틱에서 원격 잡이 한꺼번에 `lost` 가 되지 않고, 워커가 끝내 안 돌아오면 기동 + G 에 `lost`. 로컬 레인 잡만 `lost`(`server restarted`)인 것은 오늘과 같다. 정지 중이던 `rcm wait` 은 오늘 계약대로 연결 실패 60초 뒤 종료 3이고, 복귀 뒤 `rcm wait --job N` 은 실제 결과 코드를 낸다. 근거: `store.py:3878-3909` · `PLAN.md:121, 457` · 결정 266 | e2e: 서버 stop/start(`tests/test_e2e_worker.py:857` 확장) + 단위: `mono_fn` 기동 바닥, DB | PR1 |
| RL8 | [취소 T4 · W4 · 유예 · 322] 분단 중 요청자가 `rcm cancel N`(또는 같은 그룹 제출로 supersede)을 한다. (a) G 안에 연결 복귀 (b) 복귀 없이 G 경과, 그 뒤 연결 복귀. 표식 · 일반 각각 | 요청은 접수되어 `cancelling`. (a) 첫 heartbeat 응답의 `cancel` 로 프로세스가 끝나고 `cancelled`(종료 2, supersede 면 `superseded by #N` 유지). (b) `lost`(`worker_unreachable`, 종료 3)이고 `cancelled`(2)나 `cancel_unconfirmed` 로 위장하지 않는다. `kill_at + 2 × heartbeat` 규칙은 조용한 세션에는 걸리지 않는다. 표식 프리셋은 G 전에 워커가 스스로 멈춘다. 일반 프리셋은 복귀 뒤 첫 heartbeat 2xx 의 `closed_jobs` 에 `had_cancel: true` 가 실려 워커가 그 잡의 프로세스 그룹을 멈춘다(pid 소멸, `should_cancel` 길 — 결정 334) — 취소한 잡이 계속 돌지 않는다. 표식 프리셋이 서버 쪽 `cancel_unconfirmed` 로 닫히면 그 닫힘에 홀드(결정 330). 그 뒤 워커의 finish 는 `late_result` 로만 남고 상태는 `lost` 그대로. 근거: `tests/test_worker_lost.py:137` · 결정 253 · 256 · 322 · F61 | e2e + 단위: DB `state` · `cancel`, heartbeat 응답, 종료 코드, pid | PR1 (상태 · 멈춤) · PR2 (`late_result`) |
| RL9 | [전이 T5 · claim · 339-a · 339-b] 서버가 claim 을 확정했는데 응답이 유실된다(분단 · 워커 크래시 직전). 워커는 그 잡을 모른다. 일반 · 표식 각각 | 프로세스가 뜨지 않는다(pid 없음). 같은 세션이 살아 있으면 그 claim 요청이 워커에서 끝난(응답 · 204 · 오류 · 시한) 뒤 찍은 heartbeat(`claims_settled` ≥ 그 잡의 `claim_seq`, `seq` 가 받아들인 것보다 큼)에 그 잡이 없을 때 `lost`(`worker_restarted_without_job`, 종료 3)로 닫고 레인을 비운다(G 가 길어도 같다). 표식 잡이면 그 닫힘에 홀드 1(`cause = worker_restarted_without_job`) — 「없음」은 확인이 아니다. 연결이 끝내 안 돌아오면 G 뒤 `lost`(표식이면 홀드). 프로세스가 죽고 다시 시작했으면 이어받기 등록이 닫고(확인, 홀드 0) 이미 닫혀 홀드가 있으면 푼다. 자동 재실행 없음. 근거: `remote_workers.py:925-951` · CHANGELOG.md:712-716 · `docs/m5n-workplan.md:652` · 결정 339-a · 339-b | 단위: claim 직후 응답 유실 흉내 + heartbeat 두 번, DB `jobs` · `irreversible_holds` | PR1 |
| RL10 | [펜싱 T8·W5 · 255 · 258 · 261] 프로세스 A(토큰 w1)가 잡 X 를 도는 중, 같은 토큰으로 프로세스 B 를 띄운다(A 는 서비스, B 는 수동). X 는 표식 · 일반 각각 | B 의 등록은 성공하고 B 가 그 이름의 유일한 현재 세션이다. A 의 다음 요청(heartbeat · log · phase · finish 어느 것이든)은 409 `code: session_fenced`(잡이 닫혔다는 `job_closed` 와 구별). A 는 X 의 프로세스 그룹을 SIGTERM→KILL 로 멈추고 좁은 확인 finish 를 보낸 뒤 종료 0. X 는 `lost`(`worker_replaced`, 종료 3)이고 A 가 `failed` · `cancelled` 로 보고하지 않는다. 좁은 확인의 `group_gone: true` 라 홀드 0(표식이어도). 확인 전까지 X 는 busy 라 그룹 자리가 안 풀리고 B 의 그 레인 claim 은 204(409 아님). 감독자(launchd `KeepAlive.SuccessfulExit=false`, systemd `Restart=on-failure`)가 A 를 되살려 B 를 되펜싱하는 핑퐁이 없다(1분 안 `register` 호출 ≤ 2). 확인이 없으면(A 분단) X 는 A 의 마지막 들음 + G 에 `lost`. 근거: 결정 255 · 258 · 261 · `cli.py:3564, 3592` | e2e: 두 프로세스 + 가짜 감독자, pid, DB, 등록 횟수 | PR1 |
| RL11 | [펜싱 T8·T14 · 255 · 329] 펜싱된 A(또는 표에 없는 세션)가 늦게 보낸 log · phase · finish(좁은 확인이 아닌 것) · 산출물 업로드 · capabilities. 별도로 같은 토큰의 프로세스 둘 이상을 같은 순간에 시작한다. 별도로 세션 헤더 · 오프셋 헤더의 형식이 틀린 요청 | 늦은 보고는 펜싱이면 409 `session_fenced`, 표에 없으면 409 `not_registered`(남의 토큰 403 과 구별)이고 잡 상태 · `log.txt` · 마커 · 산출물 · 능력 보고 · `late_result` 에 한 바이트도 반영되지 않는다. 동시 시작에서는 살아남는 세션이 정확히 하나다(둘 다 죽거나 둘 다 살지 않는다). 펜싱된 세션의 재등록은 409 `session_fenced`. 형식이 틀린 세션 헤더는 400 이고 본문 키는 `error` 하나, 반영 0(오프셋 헤더의 형식 오류는 PR2 의 RL16). 3판: 다른 이름의 토큰이 남의 현재 세션 id 를 실은 heartbeat · log · finish · register 는 403 이고 그 세션의 `heard` 가 오르지 않는다(그 세션의 표식 잡은 제때 닫힌다 — 주입 시계로 확인). 근거: 결정 255 · 329 · 336 · `docs/m5b2-workplan.md` 「종료 잡의 늦은 보고」 · 2차 Q-B | 단위(HTTP): 응답 코드 · `code`, `log.txt` sha256, DB | PR1 |
| RL12 | [세션이 아닌 것 T10·T9] (a) 같은 프로세스의 다른 레인이 `not_registered` 409 로 재등록 (b) M5q 버전 따라가기: 드레인 뒤 같은 PID 로 exec (c) 서비스 워커가 잡을 도는 중 같은 토큰으로 `rcm worker --check` · `rcm doctor --role worker` | (a) 같은 세션 id 재등록은 멱등: 다른 레인의 도는 잡을 닫거나 멈추지 않는다(결정 260). (b) 드레인 전에 보고한 잡은 그대로이고 못 보고한 잡은 디스크 큐에 남아 exec 뒤 새 이미지가 등록 전에 보낸다. exec 뒤 새 세션이 옛 세션을 `previous_sessions` 로 넘겨도 끝난 잡의 상태는 안 바뀐다. (c) 세션을 만들지 않아 도는 잡이 안 멈추고 서비스 워커의 heartbeat 가 409 를 받지 않는다. 근거: 결정 255 · 260 · `remote_worker.py:1-17` · `cli.py:4833` · F46 | e2e + 단위: DB, heartbeat 응답, 잡 pid | PR1 (a · c) · PR2 (b — 디스크 큐) |
| RL13 | [호환 T12 · 순서] 서버와 버전이 다른 프로세스(따라가기 전의 옛 설치)가 같은 토큰으로 `register` 한다. 현재 세션 A 가 잡을 도는 중 | 오늘 계약대로 409 + `server_version` 만 돌려준다. 세션 표 · 펜싱 · `lost` 어느 것도 일어나지 않는다. A 의 heartbeat 와 잡이 그대로 이어지고 `rcm wait` 결과가 안 바뀐다. 버전 검사가 프로토콜 · 세션 검사보다 먼저다. 근거: `remote_workers.py:397-405` | 단위(HTTP): 응답 본문, DB `jobs` · `worker_sessions` | PR1 |
| RL14 | [재시작 T9·W7·W8] 워커가 (a) 잡 X 를 끝내고 finish 를 디스크 큐에 썼으나 서버에 못 보낸 채 죽는다 (b) 잡 Y 를 도는 중 `kill -9` 로 죽는다. 같은 데이터 폴더 · 같은 토큰으로 다시 시작한다 | (a) 다시 시작한 워커가 등록 전에 X 의 finish 를 옛 세션 id 로 보내고 X 는 실제 결과로 확정된다. 등록이 X 를 먼저 `lost` 로 닫지 않는다(등록은 옛 세션의 큐가 끝난 뒤). (b) Y 의 고아 그룹이 정리되고 Y 는 `lost`(`worker_restarted_without_job`, 종료 3). Y 가 이미 유예 만료로 `lost` 였으면 등록이 그 요약을 덮지 않는다(`tests/test_e2e_worker.py:797` 계약). 둘 다 자동 재실행 없음. 근거: 결정 268 · 326 · `docs/m5n-workplan.md:652` | e2e: kill -9 후 재시작, DB, 종료 코드 | PR1 (b) · PR2 (a — 디스크 큐) |
| RL15 | [고아 T9 · 268 · 327] 표식 프리셋의 잡 프로세스 그룹이 도는 중 워커를 `kill -9` 한다(자식은 고아). 같은 데이터 폴더로 새 프로세스를 시작한다. 별도로, 기록된 pid 가 이미 다른 프로세스에 재사용된 경우. 신원은 Linux 의 `/proc/<pid>/stat` 시작 시각과 그 밖의 `ps -o lstart=` 둘 다 | 새 프로세스는 claim 을 시작하기 전에 죽은 세션의 프로세스 그룹을 정리한다(고아 pid 소멸). 시작 시각이 다른(재사용된) pid 는 건드리지 않는다(무관한 프로세스가 살아 있다). 정리 결과가 워커 로그에 남는다. `/proc` 읽기는 가짜 stat 파일(괄호 · 공백이 든 이름 포함)로 22번째 칸을 맞게 읽는다. 3판: 기록한 쪽과 읽는 쪽의 TZ · 로캘이 달라도(서비스 대 손으로 띄운 셸) 같은 프로세스를 같다고 본다 — `ps` 는 `LC_ALL=C TZ=UTC` 로 읽고, 형식이 다른 값(로캘 출력 주입)이면 「모름」이지 「재사용 → 확인됨」이 아니다. 근거: 결정 268 · 327 · 253 · F59 · F71 | e2e: pid 생존 확인, 워커 로그 + 단위: 가짜 `/proc` stat | PR1 |
| RL16 | [로그 · 오프셋 · 329] (a) 서버는 조각을 받았는데 응답이 유실돼 워커가 같은 조각을 다시 보낸다 (b) 요청이 유실돼 워커의 다음 조각 오프셋이 서버보다 앞선다(흉내) (c) 서버 재시작 직후, 썼지만 커밋 전에 죽은 조각 (d) 워커가 재시작해 디스크의 `acked` 로 이어 보낸다 | 서버 `log.txt` 가 워커 스트림 스풀과 바이트 단위로 같다(가릴 값이 없을 때 sha256 일치, 중복 · 빈틈 · 순서 뒤바뀜 없음). 이미 받은 범위는 무시한다. 빈틈이면 409 `log_gap` + `expected_offset` 이고 워커는 프로세스를 죽이지 않고 그 오프셋부터 다시 보낸다. (c) 기동 때 `log.txt` 를 `log_file_bytes` 로 자르고 재전송이 다시 채운다. (d) 그 프로세스의 첫 로그 요청이 빈 본문 오프셋 질의이고 0 부터 다시 보내지 않는다(요청 본문 바이트 합 = 남은 바이트). 3판: 세션은 있는데 `X-RCM-Log-Offset` 이 없거나 0 이상 정수가 아니면 400(`error` 만), 반영 0. 근거: 결정 271 · 329 · `remote_workers.py:639-668` · `remote_worker.py:169-172` | 단위 + e2e: sha256 비교, 워커 로그, pid 생존, 보낸 바이트 수 | PR2 |
| RL17 | [로그 · 줄 끝] (a) 마지막 줄에 `\n` 이 없는 채로 잡이 끝난다 (b) `::rcm::step::` 마커 줄 직후 서버가 재시작된다 (c) `\r` 진행 표시가 줄 끝 없이 수백 KiB 이어진다 | (a) 그 바이트가 잡 종료 뒤 `rcm logs N` 에 남는다(유실 0, 러너가 `\n` 을 붙인다). (b) 워커가 보낸 조각은 모두 `\n` 으로 끝나 마커가 한 줄로 파싱되고 `steps` · `failed_step` 이 정확하다. (c) 서버 메모리 · 조각이 줄 끝 없이 늘지 않고(한 줄 ≤ 128 KiB) 바이트는 결국 모두 도착한다. 실시간 tail 에 늦게 보이는 정도는 문서에 적는다. 근거: `runner.py:439-450, 473-479` · F35 실측 | 단위 + e2e: `rcm logs`, wait JSON `steps`, 조각 끝 바이트 | PR2 |
| RL18 | [로그 · 비밀] 서버가 아는 릴리스 비밀이 로그에 섞인다. (a) 한 조각 안 (b) 조각 경계 근처 (c) 재전송으로 같은 조각이 두 번 (d) 가리기로 길이가 달라진 뒤의 오프셋 | 서버 `log.txt` · `rcm logs` · SSE · 웹 로그 어디에도 비밀 원문이 없다(검색 0건). 조각은 줄 경계에서 잘려 한 값이 두 요청에 걸치지 않는다. 오프셋은 가리기 전 원본 바이트 기준이라 (d) 이후의 재전송도 중복 · 빈틈 없이 맞는다(`log_offset` 과 `log_file_bytes` 가 다르다). 근거: 되돌릴 수 없는 결과(비밀 노출) · `remote_workers.py:644-646` · 결정 271 | 단위 + e2e: 비밀 표지 문자열 검색(log.txt · SSE · `rcm logs`) | PR2 |
| RL19 | [로그 · 재전송 상한 · 324] 서버가 로그와 finish 를 계속 못 받는다(5xx · 서버 디스크 가득 503 `log_write_failed` · 분단). (a) 잡이 도는 중 (b) 잡이 끝난 뒤 | (a) 도는 중에는 횟수로 포기하지 않는다. 분단 5분 동안 재시도가 수십 번 쌓여도 재연결 뒤 로그가 완전하다. (b) 잡이 끝난 뒤에만 상한이 걸린다: 로그 · 산출물 단계가 HTTP 5xx 를 받는 중이면 300초 뒤 finish 가 먼저 가고(RL40), 연결 실패(분단)면 순서를 지키며 24시간까지 다시 보낸다. 24시간을 넘으면 `log_bytes` 를 실은 finish 가 가서 잡이 결과대로 확정되고 `log_complete = false`(finish 가 영원히 막히지 않는다). 근거: 결정 264 · 272 · 324 | 단위: 주입 시계 + 실패 N 회, e2e: 서버 로그 쓰기 실패 주입, DB | PR2 |
| RL20 | [로그 · 유실 표시] 분단 중 잡이 오늘의 `LOG_KEEP_BYTES`(4 MiB)보다 많은 로그(12 MiB)를 낸다. 한 번은 워커 디스크가 가득 차 스풀을 못 쓴다 | 표시 없이 버려지지 않는다. 재연결 뒤 12 MiB 가 모두 서버에 들어간다(바이트 일치). 스풀을 못 쓴 경우는 잡 문서 · `rcm wait` JSON 에 `log_complete = false` 가 서고 `rcm logs` · 웹 로그 보기에도 한 줄로 보인다. 앞쪽이 조용히 잘린 로그가 `log_complete` 참과 함께 나오지 않는다. 근거: 결정 271 · 273 · `remote_worker.py:156-184` | 단위 + e2e: 서버 로그 바이트 수, wait JSON `log_complete`, 웹 | PR2 (값) · PR3 (`rcm logs` · 웹 줄) |
| RL21 | [finish 중복 · 지각 T7·T14] (a) finish 응답이 유실돼 같은 finish 가 두 번 온다(재시작 뒤 큐에서 한 번 더 포함) (b) `lost` 로 닫힌 뒤 같은 세션의 finish · log · phase 가 늦게 온다(자기 멈춤의 `lost` 보고 포함) (c) (b) 의 늦은 finish 둘이 동시에 온다(두 스레드) | (a) 잡은 한 번만 확정되고 첫 보고가 이긴다(`finished_at` · 요약 불변). 둘째는 409 `job_closed` 로 무해이고 워커는 전달된 것으로 읽어 큐에서 지운다(오류 반복 없음). `job_finished` 이벤트 1회, `[[notify]]` 훅 호출 1회. (b) 잡은 `lost` 그대로, 종료 3, 요약은 서버가 먼저 닫은 사유 그대로다. finish 만 `late_result` 로 한 번 남고 로그 · 마커 · 산출물에 반영 없음. 워커는 그 보고를 큐에서 지운다(영원히 재전송 안 함). (c) events 의 `late_result` 는 정확히 1건, 한쪽만 `late_result_recorded: true`. 자동 재실행 · retry 사슬 없음. 근거: `tests/test_worker_lost.py:128-131` · 결정 256 · 274 | 단위 + e2e: DB, 이벤트 버스, notify 가짜 훅 호출 수, 워커 큐 폴더, 두 스레드 동시 요청 | PR2 |
| RL22 | [산출물 T7 · 275] 잡이 끝났고 산출물 번들 업로드가 (a) 끊긴다 (b) 서버는 받았는데 응답이 유실돼 다시 올린다 (c) 워커가 업로드와 finish 사이에 재시작한다 (d) 워커가 업로드 없이 처분 `ready` 를 보낸다 (e) 묶음 행은 있는데 서버의 `bundle.tar` 파일이 없다 | 잡 결과는 산출물과 무관하다(`succeeded` 면 `succeeded`, wait 종료 0). `artifacts.state` 는 번들이 서버에 실제로 있을 때(행 · 해시 · 파일)만 `ready` 이고 아니면 `failed` + `upload_failed`(또는 `hash_mismatch`). 재업로드는 멱등이다(번들 1개, sha256 동일, 보관 행 1개). finish 는 업로드 뒤에만 간다. 남겨 둔 묶음 스풀이 재시작 뒤에도 쓰인다. `rcm wait --fetch-artifacts` 는 받으면 0, 못 받으면 5. 근거: 결정 275 · `remote_workers.py:705-759, 875-900` · F63 · `PLAN.md:118` | e2e: `artifacts.state`, 번들 sha256, 종료 코드 + 단위: 파일 지운 뒤 finish | PR2 |
| RL23 | [전이 T6 · 준비 단계 · 259] 워커가 `materializing`(스냅샷 `GET …/tree` 내려받는 중)일 때 분단 · 서버 정지가 난다. 별도로 스냅샷이 서버에 없다(404) | 연결 실패는 `lost` + `snapshot_unreachable`(종료 3), 확정 결함(404)은 오늘처럼 `failed` + `snapshot_download_failed`(종료 1). 잡이 `running` 으로 영원히 남지 않고 레인이 풀린다. `.part` 가 남지 않는다. 프로세스는 뜬 적이 없다. 자동 재실행 없음. 근거: 결정 259 · `remote_worker.py:918-925` · `PLAN.md:118` | e2e: 중계 정지 중 큰 스냅샷, DB, 워크스페이스 | PR2 |
| RL24 | [업그레이드 T11·T12 · 262 · 329] v25 DB 에서 v26 으로 마이그레이션(필수 시험). 열기 전 DB 에 `running` 원격 잡 · `queued` · `lost` · `cancelling` 이 있다. 세션을 모르는 옛 워커(같은 버전 문자열)가 새 서버에 heartbeat · claim · register 한다 | 마이그레이션 뒤 모든 잡의 상태 · 요약 · 로그 경로가 그대로이고(건수 · 상태 표 일치) `PRAGMA user_version` 이 26. 옛 빌드는 v26 DB 를 열기를 거절한다(newer-schema). 옛 워커가 이미 들고 있던 `running` 원격 잡(레거시)은 heartbeat · log · finish 가 받아져 끝까지 확정되고, 레거시 잡이 끝나면 세션 없는 heartbeat 도 409 `protocol_required`. 옛 워커의 claim · register 는 409 `protocol_required` 이고 claim 은 한 건도 안 나간다. 그 409 의 `error` 에는 「not registered」 가 없어 옛 워커가 재등록 루프를 돌지 않는다. 도는 옛 워커는 claim 을 2~4초 간격으로만 다시 묻고(옛 코드 `remote_worker.py:822-826`, 1초 미만의 뜨거운 루프 아님), 다시 시작한 옛 워커는 등록 409 에 종료 2 → 감독자 간격(5~10초)으로만 되살아난다. 조용한 레거시 잡은 `heard_legacy(이름)` + G 에 `lost` — 그 프리셋이 닫는 때 표식이거나 설정에 없으면 홀드(그룹은 잡 행의 그룹, 없으면 `irreversible:<프리셋>` · `global` — 결정 330 · 336 · 339-d). 근거: 결정 262 · 329 · `store.py:1208-1248, 778-785` · F40 | 단위: v25 DB 고정 파일을 새 빌드로 열기, 읽기 전용 사본 + 옛 빌드 거절, 세션 없는 HTTP 요청, 응답 `error` 문자열 | PR1 |
| RL25 | [호환 T13 · 258 · 270] 프로토콜 키가 없는 옛 서버(같은 버전 문자열)에 새 워커가 붙는다. 감독자가 되살려 여러 번 반복한다 | 등록 전에 알아채고 register 하지 않는다(서버에 `workers` 행이 안 생기고 옛 서버의 도는 잡에 영향 없음). claim 하지 않는다. 한 줄 안내를 내고 종료 3 — 감독자가 되살리는 값이다(launchd · systemd 표, F39). 서버로 간 요청은 `GET /worker/spec`(와 `GET /api/health`)뿐, register · claim 0건. 반복해도 `worker-sessions/` 아래 폴더가 늘지 않는다(V2-12). 근거: 결정 258 · 270 | 단위: 가짜 옛 서버 응답, 종료 코드, 서버 쪽 요청 목록, 데이터 폴더 목록 | PR1 |
| RL26 | [시계 · 잠자기 W3 · 320] 워커 머신이 잠들었다가 G 보다 오래 뒤에 깬다(주입: 벽시계 +G+α, 단조 시계는 거의 정지). 표식 · 일반 각각 | 표식 프리셋은 깬 직후 감시 스레드의 첫 틱 판정에서 자기 멈춤이 걸려 프로세스 그룹이 끝난다(heartbeat 루프가 어디에 있든). 단조 시계만 보고 「몇 초 안 지났다」며 이어 돌리지 않는다. 일반 프리셋은 죽이지 않는다 — 서버가 이미 `lost` 로 닫았어도 계속 돌고 끝나면 `late_result`(결정 256). 근거: 결정 253 · 256 · 320 | 단위: 워커 시계 주입(벽시계 · 단조 따로) | PR1 |
| RL27 | [시계 · 점프와 어긋남] (a) 서버 벽시계가 앞으로 G 이상 뛴다(NTP 단계 보정), 워커는 계속 heartbeat (b) 워커 시계가 서버보다 ±1시간 어긋난다 | (a) heartbeat 가 계속 오던 워커의 잡이 점프 한 번으로 `lost` 되지 않는다(유예는 서버 단조 시계). (b) 유예 · `lost` 판정은 서버 시계로만, 워커의 자기 멈춤은 워커 시계로만 한다. 워커가 보낸 `finished_at` 이 서버 수신 시각보다 미래이거나 `started_at` 보다 과거면 그 끝으로 잘라 `job_seconds` 가 음수가 되지 않는다. 근거: 결정 266 · 277 · `remote_workers.py:778-780` | 단위: 서버 `now_fn` 점프 · `mono_fn`, 워커 시계 오프셋, DB `finished_at` · `job_seconds` | PR1 (a) · PR2 (b 자르기) |
| RL28 | [요청자 · 유예 중 T2] 원격 잡이 `running` 인데 워커와 연락이 끊겨 `worker_timeout_seconds`~G 사이. 요청자는 `rcm wait --job N`, `rcm top`, 웹 큐 화면을 본다 | 잡은 `running`. 사유가 `worker_unreachable_grace` 이고 대기 줄 · top · 웹이 「워커와 연락이 끊김」을 정상 진행과 구별해 사람 말로 보이고 원문 코드가 안 나온다. `estimate.finish_at` 은 null(`—`). 「확인이 필요한 작업」 요약에 `worker_down` 바로 뒤 순서로 오른다. 레인 상태와 `/api/health`(`workers_down` · 503)는 유예 동안 한 값으로 고정되어 깜빡이지 않는다. `rcm wait` 은 이 동안 끝내지 않는다. `--timeout` 을 줬으면 그때 3. 근거: 결정 276 · `PLAN.md:118` | e2e: `/api/status` 행의 `reason` · `estimate.finish_at`, `rcm wait` stderr + 웹 node 시험(사유 문구 · `ACTIONABLE` 순서). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR3 |
| RL29 | [요청자 · 끝] 연락 두절의 끝 다섯 가지(서버가 G 뒤 `lost` · 표식 프리셋 자기 멈춤 · 펜싱 · 워커 재시작의 버린 잡 · 고아를 확인하지 못한 표식 잡)를 `rcm wait` · `rcm status` · 웹 · `[[notify]]` 훅으로 본다. 새 키를 모르는 옛 `rcm wait` 클라이언트도 같이 | 다섯 모두 `state=lost`, 종료 3, `failed_step` · `last_step` 없음. `summary_code` 가 원인마다 다르고(`worker_unreachable` · `worker_self_stopped` · `worker_replaced` · `worker_restarted_without_job` · `orphan_unverified`) 웹 · 사람용 마지막 줄에 번역된 문장이 나온다(i18n 에 없는 코드가 있으면 시험이 빨개진다). `orphan_unverified` 의 문장은 「같은 일이 그 머신에서 아직 돌 수 있다」를 말한다. 마지막 줄이 `rcm logs N` 과 URL 을 안내한다. `[[notify]]` 훅은 잡당 1회. 옛 클라이언트는 새 키를 무시하고 같은 종료 코드를 낸다. 근거: `PLAN.md:118, 220` · 결정 70 · 278 · 327 · `tests/test_outcome.py:543` | e2e: wait JSON `summary_code`, 종료 코드, 가짜 notify 훅 + 웹 node 시험(번역 문장). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR3 |
| RL30 | [설정 · 257 · 263 · 320 · 335] 서버 설정의 `disconnect_grace_seconds`: 기본값 / 3판 하한 `max(worker_timeout_seconds, 2 × worker_heartbeat_seconds + 50) + 35` 바로 아래와 바로 위 / 키를 안 적었고 `worker_timeout_seconds` 가 900 인 설정(업그레이드) / 86400 초과 / 정수 아님 · 음수 / 서버 설정을 바꾼 reload. 프리셋 `irreversible` 을 잡이 도는 중 reload 로 바꾼다. 서버를 다른 G 로 재시작한다. 워커 설정의 기존 `grace_seconds`(SIGTERM→KILL)를 G 에 견줘 크게 둔다 | 기본은 600. 3판: 키를 안 적은 큰 `worker_timeout_seconds`(900) 설정은 기동 실패 없이 `max(600, L + 35)` = 935 를 쓴다(V1-9). 하한 아래 · 86400 초과 · 비정수는 기동 실패(종료 2)와 키 이름 · 하한 값 · 「키를 지우면 그 값을 쓴다」를 말하는 문장, 하한 그 값은 통과. `[server]` 를 바꾼 reload 는 오늘처럼 통째로 거절되고 옛 값이 남는다. 도는 잡은 claim 때 얼린 `irreversible` · G 를 쓴다 — reload 나 재시작으로 바뀐 값은 다음 claim 부터. 새 키가 `grace_seconds` 와 겹치지 않고 후자의 뜻(SIGTERM→KILL)은 그대로다. 워커 `grace_seconds` 가 커서 `G − m < L` 이 되면 그 표식 잡에서만 grace 를 줄여 쓰고 로그 한 줄(일반 잡 · 설정 파일은 그대로). config 의 하한 상수와 워커의 m(기본 grace) 이 같다. 근거: 결정 257 · 263 · 320 · `config.py:1429-1434, 1960-1961` · `server.py:5114` | 단위: 설정 로드 · reload, 종료 코드, 에러 문구, claim 응답, 워커 grace 계산 | PR1 |
| RL31 | [고아 · 신원 모름 T8·T9·W12 · 268 · 327] 죽은 세션 D 의 표식 잡 그룹이 살아 있는데 신원을 확인할 수 없다: (a) `ps` 가 없거나 실패 · `/proc` 를 못 읽음 (b) 리더(pid = pgid)가 죽고 멤버만 살아 있음 (c) 3판: `proc.json` 의 `pgid` 가 null(띄우는 순간 죽음 — `proc.json` 이 아예 없으면 「띄운 적 없음」 = 확인됨이라 이 행이 아니다). 그 뒤 재확인 시각에 (가) 신원이 확인된다 (나) 여전히 모른다 | 새 프로세스는 그 그룹을 죽이지 않고 워커 로그에 경고를 남긴다. D 를 `previous_sessions` 가 아니라 `unverified_sessions` 에 넣어 서버가 그 잡을 즉시 닫지 않고 D 의 마지막 들음 + G 까지 busy 로 둔다(그룹 자리 유지). `D.ok_at + G − m` 에 다시 확인해 (가) 같은 그룹이면 멈추고(없거나 다른 프로세스면 그대로) D 의 id 로 좁은 확인 finish → 잡이 G 전에 `lost`(`worker_replaced`), (나) 아무것도 보내지 않고 서버가 G 에 `lost` + `orphan_unverified` 로 닫고 **그룹 홀드**를 건다(3판, 결정 330 — 2판은 그룹을 풀었다). 일반 프리셋이면 `worker_unreachable`, 홀드 0. 근거: 결정 268 · 327 · 2차 Q-D · 되돌릴 수 없는 결과(남의 프로세스를 죽임 · 두 번 실행) | 단위: `ps` · `/proc` 실패 주입, 리더만 죽인 그룹, 등록 본문, 주입 시계, DB `summary_code` | PR1 |
| RL32 | [호환 · 순서 T13] 새 워커가 버전이 다른 서버(옛 릴리스)에 붙는다. 따라가기 설정 켬/끔 | 프로토콜 확인이 먼저 종료 3 으로 끝내지 않는다: 버전이 다르면 오늘의 등록 길로 가서 서버의 버전 409 를 받고 따라가거나(M5q) 종료 2. 서버에 `workers` 행 · 세션 행이 생기지 않는다. 근거: 결정 270 · `remote_workers.py:397-405` · `remote_worker.py:395-408` | 단위: 가짜 서버(버전 다름 · spec 키 없음), 종료 코드, 요청 목록 | PR1 |
| RL33 | [취소 · 펜싱 T4·T8] 펜싱된 세션 A 의 `cancelling` 잡이 있고 같은 이름의 새 세션 B 는 살아서 heartbeat 를 보낸다. `kill_at + 2 × heartbeat` 가 지난다 | 서버는 A 의 잡을 `cancelled`(`cancel_unconfirmed`)로 닫지 않는다 — 취소 미확인 닫기는 현재 · 들리는 세션의 잡에만. 잡은 A 의 확인이나 A 의 마지막 들음 + G 까지 busy, 그 뒤 `lost`. 근거: 결정 261 · 266 · `remote_workers.py:974-1008` | 단위: 두 세션, 주입 시계, DB | PR1 |
| RL34 | [자기 멈춤 시간 경계 W3 · 320] 표식 잡이 도는 중 (a) heartbeat 요청이 매번 시한(15초)까지 매달리다 실패한다 (b) 서버가 돌아온 첫 heartbeat 뒤 못 한 보고가 쌓여 있다 (c) 연결은 되는데 heartbeat 마다 14초 걸려 2xx 가 온다 (d) 연결은 되는데 heartbeat 가 5xx 를 계속 받는다. 워커 grace 기본 · 큰 값 각각 | (a)(b) 마지막 2xx 의 보낸 시각에서 `G − m` + 감시 틱 + 러너 멈춤 안에 그룹이 사라진다 — 서버가 닫을 수 있는 가장 이른 시각(`heard + G`)보다 늘 앞(주입 시계로 모든 조합, 1판의 「G + 10초 뒤에 죽음」이 나오지 않는다). heartbeat 루프가 막혀 있어도 감시 스레드가 판정한다. (c) 연결된 동안 자기 멈춤이 걸리지 않는다 — 3판: 그 사이 heartbeat 2xx 하나를 잃어도(`ok_at` 나이 ≤ 14 + H + 14 + H + 15 < L, §4.1.5 정리 2). 기본 설정과 e2e 설정(T 10 · H 1 · G 90) 둘 다. (e) 3판: 서버가 응답 바이트를 조금씩 흘려(소켓 시한 안쪽) heartbeat 하나가 15초를 넘긴다 → 전체 기한이 그 회차를 끊고 다음 박자가 간다(박자가 막히지 않는다). (f) 3판: SIGTERM 뒤 워커 벽시계가 1시간 뒤로 간다 → SIGKILL 이 단조 시계로 `grace_J` + 2초 안(F67). (d) 서버는 요청을 받으므로 닫지 않고, 워커는 `G − m` 에 표식 잡을 멈춰 `lost`(`worker_self_stopped`, 3) — 두 번 돌지 않는 쪽. 근거: 결정 320 · 333 · 335 · F53 · F55 · F56 · F67 · F68 | 단위: 주입 시계(벽 · 단조) + 매달리는 · 조금씩 흘리는 가짜 클라이언트 + 러너 대역, 멈춤 시각과 서버 닫기 가능 시각 비교 | PR1 |
| RL35 | [닫힌 잡 통보 T3·W4 · 321] 로그도 phase 도 내지 않는 조용한 잡이 분단으로 G 를 넘겨 서버가 `lost` 로 닫은 뒤 워커가 다시 붙는다. 표식 · 일반 · 취소 요청이 있던 일반 각각. 별도로 서버가 잡을 `cancelled`(취소 미확인) · `timed_out` 으로 닫은 경우 | 첫 heartbeat 2xx 응답의 `closed_jobs` 에 그 잡이 `{id, state, had_cancel}` 로 실린다(그 세션의 잡만, 목록에 없는 잡 · 다른 세션의 잡은 안 실림). 워커는 표식 · `had_cancel` · `cancelled` · `timed_out` 이면 그룹을 멈추고(pid 소멸), 취소 없는 일반 `lost` 면 계속 돌린다(로그 전송만 멈춤). 로그 · phase 요청을 한 번도 안 보낸 잡도 같다. 3판: 서버 상태가 `succeeded` · `failed` · `timed_out` · `retried` · `exhausted` 이면(이 세션의 finish 가 기록됐고 2xx 만 잃음) 워커가 그 잡의 큐 항목을 전달됨으로 지우고 finish 를 다시 보내지 않는다(W4 표, V2-6). 근거: 결정 321 · 322 · 334 · 256 · F61 | 단위(HTTP): heartbeat 응답 본문 + 워커 반응(가짜 러너), e2e: pid | PR1 |
| RL36 | [로그 쓰기 실패 · 동시 요청 T15 · 323] (a) 서버가 조각을 파일에 쓴 뒤 커밋이 `database_busy` 로 실패하고 워커가 같은 조각을 다시 보낸다 (b) 같은 잡의 같은 오프셋 요청 둘이 동시에 든다(서버에서 아직 도는 시한 초과 요청 + 재시도) (c) 쓰기 중 ENOSPC(OSError 주입)로 503 `log_write_failed`, 공간이 돌아온 뒤 재전송 (d) 쓰기 뒤 커밋 전에 잡이 닫혀 0행 (e) 3판: 세션 판정(step 2)을 지난 요청이 쓰는 사이 같은 이름의 새 세션이 등록해 그 세션을 펜싱한다(주입: 쓰기와 커밋 사이 멈춤) | 서버 `log.txt` 가 워커 스트림 스풀과 바이트 단위로 같다(sha256, 중복 0 · 미커밋 꼬리 0). (a)(c)(d) 실패 응답 뒤 파일 길이 = `log_file_bytes`(되자름), `log_offset` 불변. (b) 두 요청이 줄 서고 둘째는 접두를 버려 0바이트를 쓴다. (d) 409 `job_closed` 이고 파일에 남는 바이트 0. (e) 커밋 0행 → 409 `session_fenced`, 파일 되자름, `log_offset` 불변(펜스 뒤 반영 0, R17). 잡이 끝난 뒤 `log_complete` 는 참. 근거: 결정 323 · 338 · F57 · V2-2 · V1-3 · V2-7 | 단위: DB 바쁨 · OSError 주입, 두 스레드 동시 요청, 파일 길이 · sha256, DB `log_offset` · `log_file_bytes` | PR2 |
| RL37 | [짧은 파일 T16 · 323] 서버의 `log.txt` 가 `log_file_bytes` 보다 짧다(서버 OS 충돌 · fsync 없음 흉내: 커밋 뒤 파일을 잘라 둠). (a) 다음 로그 요청 (b) 서버 기동 정리 (c) 3판: 이미 끝난(`log_complete = 1`) 잡의 파일이 짧아진 채 서버가 기동한다 | 다음 쓰기는 파일 끝에서 이어 써 빈 바이트(NUL) 구멍이 없다. `jobs.log_complete` 가 0 으로 고정되어 finish 뒤에도 false(`log_offset ≥ log_bytes` 여도). 서버 로그에 「log gap」 한 줄. `rcm wait` JSON · 잡 문서의 `log_complete` 가 false. (c) 기동 정리가 끝난 잡의 `log_complete` 를 0 으로 고치고 로그 한 줄, 파일이 없는 잡은 건드리지 않는다. 근거: 결정 323 · 338 · V2-10 · 되돌릴 수 없는 결과(완전하다고 말하는 잘린 로그) | 단위: 파일 자르기 주입, NUL 바이트 검색, DB `log_complete` | PR2 |
| RL38 | [세션 폴더 주인 W9 · 325] 같은 사용자의 기본 데이터 폴더를 두 워커 설정이 같이 쓴다: 운영 서버를 가리킨 워커 A(죽은 세션 폴더와 못 보낸 결과가 남아 있음)와 시험 서버(다른 `server` · 다른 토큰 이름)를 가리킨 워커 B. B 를 시작한다. 별도로 `owner.json` 이 없는 폴더 | B 는 A 의 폴더를 잠가 보지도, 고아를 죽이지도, 큐를 보내지도, 지우지도 않는다(A 의 고아 pid 생존 · A 의 파일 바이트 그대로 · B 서버로 간 요청에 A 의 세션 id 0건). A 가 다시 시작하면 자기 폴더를 이어받는다. `owner.json` 없는 폴더도 남의 것으로 본다. 7일 지나고 잠글 수 있는 남의 폴더만 청소가 지운다. 근거: 결정 325 · V1-5 · 되돌릴 수 없는 결과(남의 결과를 지움 · 남의 프로세스를 죽임) | 단위: 두 설정 · 한 데이터 폴더, 파일 sha256, 요청 목록, pid | PR1 |
| RL39 | [죽은 세션 큐 응답 W10 · 326] 다시 시작한 워커가 죽은 세션 D 의 큐를 D 의 id 로 보내다 (a) 409 `session_fenced`(같은 이름의 다른 살아 있는 프로세스가 이미 D 를 펜싱) (b) 409 `not_registered`(서버가 D 를 모름) (c) 401 · 403 (d) 5xx 가 이어짐을 받는다 | (a) 그 잡의 원래 결과는 보내지 않고(남지 않음), 고아가 「확인됨」이면 좁은 확인 finish 한 번. 이 프로세스는 계속 돌고 종료 0 하지 않는다. (b) D 의 큐를 지우고 D 의 id 로 register 를 보내지 않는다(요청 목록에 D 의 register 0건). (c) D 의 큐 파일이 바이트 그대로 남는다. (d) 등록이 D 의 큐가 끝날 때까지 기다리고(그동안 register 0건), 로그 · 산출물 5xx 는 300초 뒤 finish 먼저. 어느 경우든 이 프로세스의 새 세션은 펜싱되지 않고 정상 등록된다. 근거: 결정 326 · V2-4 · 2차 Q-B | 단위: 가짜 서버 응답 순서, 요청 목록, 종료 코드, 큐 파일 sha256 | PR2 |
| RL40 | [finish 상한 W11 · 324] 잡이 성공으로 끝났는데 로그 라우트가 503 `log_write_failed`(서버 디스크) · 산출물 PUT 이 5xx 를 계속 내고 finish 라우트는 된다. 별도로 같은 기간 서버가 아예 안 닿는다(연결 실패) | 잡이 끝나고 300초(주입 시계) 뒤 finish 가 먼저 가서 잡이 `succeeded` 로 확정되고 `rcm wait` 종료 0, 그룹 자리가 풀린다. `artifacts.state` 는 `failed` + `upload_failed`, `log_complete` 는 false. 그 뒤 남은 로그 · 산출물은 보내지 않는다. 연결 실패 기간에는 순서를 바꾸지 않고 서버가 돌아오면 로그부터 이어 보낸다. 근거: 결정 324 · V2-3 · `PLAN.md:118` | 단위: 주입 시계 + 라우트별 응답 주입, DB, 종료 코드 | PR2 |
| RL41 | [Popen 전 발동 S2~S4 · W3 · 333 · V2-1] 표식 프리셋에 `requires` 가 없고 준비가 길다(git_ref fetch · 큰 스냅샷 — 주입한 느린 자재화). 그 사이 분단이 `G − m` 을 넘긴다. (a) 자재화 중에 문턱 (b) Popen 직전 확인과 Popen 사이에 문턱(주입 훅) (c) `requires` 가 있는 프리셋에서 (a) | (a)(c) 명령 프로세스가 한 번도 시작되지 않는다(명령이 만드는 표지 파일 0개, 감독자 · 명령 pid 0). 잡은 `lost` + `worker_self_stopped`(종료 3 — `cancelled` 2 가 아님), `failed_step` 없음, finish 의 `group_gone: true`. (b) 프로세스가 생겨도 펌프의 첫 점검(Popen 직후 곧바로)이 끊어 그룹 소멸이 플래그 + `grace_J` + 4초 안 — 서버가 닫을 수 있는 가장 이른 시각(`heard + G`)보다 앞. 셋 다 자기 멈춤 보고가 G 안에 닿으면 홀드 0. 근거: 결정 253 · 320 · 333 · F65 · 되돌릴 수 없는 결과(두 번 실행) | 단위: 실제 러너 + 느린 자재화 대역 + 주입 시계 + Popen 훅, 표지 파일 · pid · finish 본문 + e2e: 느린 스냅샷을 중계가 붙잡음 | PR1 |
| RL42 | [홀드 T3 · T18 · 330] 표식 잡이 서버 유예 만료로 `lost`(`worker_unreachable`) — 워커는 끝내 돌아오지 않는다. 같은 그룹 제출 둘(하나는 다른 풀 — 합성 그룹은 `global`) · 다른 그룹 잡 하나. 그동안 서버를 재시작하고 `metadata_retention_days` 를 넘긴 정리를 돌린다. 일반 프리셋으로 같은 경우 | 닫는 트랜잭션에서 `irreversible_holds` 행 1개(`cause = worker_unreachable`)와 사건 kind `hold` 1건. 같은 그룹 대기 잡은 몇 시간 · 서버 재시작 뒤에도 `queued` 이고 claim 0건. 다른 그룹 잡은 돈다. 홀드 잡의 행은 정리가 지우지 않는다. 일반 프리셋이면 홀드 0 이고 다음 잡이 곧바로 돈다(결정 253). 근거: 결정 330 · F73 · F77 · 되돌릴 수 없는 결과(확인 없이 그룹이 열려 두 번 실행) | 단위: store 주입 시계 · DB 다시 열기 · 정리 호출 + e2e: 시험 서버 재시작 | PR1 |
| RL43 | [감독자 W7 · W13 · W14 · 331] 표식 잡이 도는 중 워커를 `kill -9` 한다. (a) 명령이 SIGTERM 을 따른다 (b) 무시한다(grace 3) (c) 워커가 Popen 직후, 감독자가 부모를 확인하기 전에 죽는다(주입: 감독자 시작 지연) (d) 감독자만 `kill -9`(워커는 산 채, 그 뒤 잡 취소) (e) 같은 경우의 일반 잡. 별도로 감독자 아래 명령이 종료 코드 7 로 끝남 · SIGKILL 로 죽음 | (a) 명령 그룹이 0.25초 + ε 안에(F78) 사라진다 (b) 0.31초 + `grace_J` 안 (c) 명령이 시작되지 않는다(표지 파일 0) (d) 러너가 리더(감독자) 끝을 보고 S7 로 남은 명령 그룹을 거둔다 — 명령 pid 가 리더 끝 + `grace_J` + 2초 안에 사라지고, 잡은 `failed`(종료 코드 −9, 리더의 것), finish `group_gone: true` 라 홀드 0. 그 뒤의 `rcm cancel` 은 끝난 잡이라 상태를 바꾸지 않는다. 워커까지 죽으면 서버가 G 뒤 `lost` + 홀드 (e) 감독자가 없고 명령이 워커 `kill -9` 뒤에도 산다(결정 253). 종료 코드 7 → 잡 종료 코드 7, SIGKILL → 오늘과 같은 결과(`-9`). 근거: 결정 331 · 253 · F78 · 되돌릴 수 없는 결과(아무도 끊지 않는 고아) | e2e: 실제 워커 프로세스 · 감독자 · `kill -9`, pid 생존, DB + 단위: 감독자 모듈(부모 pid 주입) | PR1 |
| RL44 | [홀드 풀기 · 인증된 확인 T9 · T18 · 330 · 336] 홀드가 걸린 뒤 (a) 같은 데이터 폴더로 워커가 다시 떠 D 의 그룹이 없음을 확인한다(`previous_sessions`) (b) W12 재확인이 「확인됨」 → 좁은 확인 `group_gone: true` (c) 같은 세션의 늦은 finish 가 `group_gone: true` (d) 다른 이름의 워커 토큰이 그 세션 id 를 `previous_sessions` 로 보낸다 (e) 좁은 확인이 `group_gone: false` | (a)(b)(c) 홀드의 `released_at` · `release_kind = worker` · `released_by` = 토큰 이름, 사건 kind `hold`(released) 1건, 그 뒤의 claim 에 대기 잡이 시작. (c) 는 `late_result` 도 1건, 응답 `hold_released: true`. (d) 403, 홀드 그대로, 사건 0. (e) 홀드 그대로. 근거: 결정 330 · 336 · V1-8 | 단위(HTTP): 응답 · DB `irreversible_holds` · events · claim | PR1 (a · b · d · e) · PR2 (c — `late_result` 트랜잭션) |
| RL45 | [홀드가 보임 · admin 풀기 · 330] 열린 홀드 둘(하나는 그 그룹에 대기 잡 하나, 하나는 대기 잡 없음). 요청자가 그 대기 잡을 `rcm wait --job` · `rcm wait --return-on-action` 으로 기다리고, `rcm check` · 그 워커 머신의 `rcm doctor --role worker` · 웹 · `/api/status` · `/api/health` 를 본다. 그 뒤 admin 이 `rcm release-hold N --reason …`, client 토큰으로 `rcm release-hold`, 열린 홀드가 없는 잡에 `rcm release-hold` | 대기 잡 사유 `needs_action`, `action.code = irreversible_hold`, `blocked_by.job_id = N`, `remaining_seconds` · `estimate.finish_at` null. 「Needs a look」 에 홀드 둘 다(대기 잡 없는 것도) 사람 말로, 원문 코드 노출 0. `/api/status` · `/api/health` 의 `irreversible_holds` 2 항목, `ok` 는 홀드로 바뀌지 않음. `rcm check` 경고 줄 · 다른 FAIL 이 없으면 종료 0. doctor 의 `needs action` 줄 · `--json` 키 · 종료 코드 그대로. `--return-on-action` 은 4. admin 풀기 → 종료 0, 잡 문서 `hold.released_by` · `release_reason`, 사건 1, 대기 잡이 다음 claim 에 시작. client 토큰 → 403 · 종료 2. 홀드 없음 → 409 `no_hold` · 종료 1. 근거: 결정 330 「어디서나 needs_action」 · F75 | e2e: 상태 JSON · CLI 종료 코드 · 출력 + 웹 node 시험(항목 · 문구). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR1 (라우트 · `rcm release-hold` 종료 코드 · claim · 대기 잡 사유 · `/api/status` · `/api/health` 키 · 잡 문서 `hold`) · PR3 (웹 · `rcm check` · doctor · `rcm wait` 줄) |
| RL46 | [죽은 세션 큐 대기 상한 · 337 · V2-9] 다시 시작한 워커가 D 의 큐를 보내는데 그 finish 가 5xx 를 계속 받는다. (a) 응답이 오는 시간으로 300초(주입 시계) (b) 그 사이에 연결 실패 구간이 섞임 | (a) 300초 뒤 등록하고 claim 을 시작한다(그 전 register 0건). 워커 로그에 한 줄. 그 잡은 `lost`(`worker_restarted_without_job`), D 의 고아가 「확인됨」이라 홀드 0. (b) 연결 실패 시간은 300초에 세지 않는다. 24시간 막힘이 없다. 근거: 결정 337 · 326 | 단위: 가짜 서버 응답 순서 · 주입 시계, 요청 목록 | PR2 |
| RL47 | [첫 등록 · 레거시 T17 · 336 · V1-5] v26 마이그레이션 직후 그 이름에 세션 NULL `running` 잡(옛 워커의 것)이 있고, 같은 이름의 새 프로토콜 워커가 처음 등록한다. 그 프리셋이 지금 표식인 경우와 아닌 경우. 별도로 옛 워커의 레거시 heartbeat 와 새 세션 heartbeat 가 섞여 온다 | 그 잡은 등록 트랜잭션에서 `lost`(`worker_restarted_without_job`)이고 새 세션의 그 레인 claim 이 409 없이 다음 잡을 받는다. 표식이거나 그 프리셋이 설정에 없으면 홀드 1(`cause = worker_restarted_without_job`, 그룹은 잡 행 또는 `irreversible:<프리셋>`), 표식 없이 남아 있으면 0. `heard_legacy(이름)` 는 세션 요청으로 오르지 않고 세션의 `heard` 는 레거시 요청으로 오르지 않는다. 근거: 결정 336 · 339-d · 255 · F69 | 단위(HTTP · DB): v25 고정 DB 를 새 빌드로 열고 요청 | PR1 |
| RL48 | [합성 그룹 · 330] `irreversible = true` 이고 `concurrency_group` 이 없는 프리셋 · 사용자 그룹을 적은 표식 프리셋 · 표식을 달기 전에 그 프리셋으로 제출된 대기 잡 | 설정 로드 뒤 앞의 것은 그룹 `irreversible:<이름>` · 범위 `global` 이고 기동 로그에 한 줄. 두 풀에 낸 그 프리셋 잡 둘이 동시에 돌지 않는다. 사용자 그룹을 적은 프리셋은 그 그룹 · 범위 그대로. 표식 전 대기 잡(그룹 NULL)은 claim 때 잡 행에 합성 그룹이 적히고, 그 잡과 그 프리셋의 새 제출(합성 그룹)이 동시에 돌지 않는다 — 어느 쪽이 먼저 claim 되든. 홀드가 생기면 홀드 행의 그룹도 합성 그룹. 근거: 결정 330 · F73 · 되돌릴 수 없는 결과(두 번 실행) | 단위: 설정 로드 · store claim | PR1 |
| RL49 | [남는 위험 · 331 · §1.3] 표식 프리셋의 명령이 `setsid` 로 세션을 빠져나간 자손(표지 파일을 계속 쓰는 프로세스)을 남기고 끝난다 · 또는 자기 멈춤으로 끊긴다 | rcm 은 그 자손을 끊지 않는다(그 pid 가 산다 — 지금 할 수 있는 일이 아님을 시험이 고정한다). 그룹은 사라져 finish 의 `group_gone` 이 참이고 홀드가 없다. `docs/configuration.md` 의 `irreversible` 절이 「명령은 데몬 · 세션 밖 프로세스를 남기지 않는다 — rcm 은 그것을 볼 수 없다」 를 말한다(`tests/test_docs_m5tb.py` 가 잠금). 근거: 결정 331 「명령이 세션을 빠져나가는 경우」 · §1.3 · §9 · 되돌릴 수 없는 결과(두 번 실행 — 막지 못함을 숨기지 않는다) | e2e: 실제 러너 · 감독자 + `setsid` 자손, pid 생존 + 문서 잠금 | PR1 |
| RL50 | [순서 · T5 · 339-a · 339-b · 3라운드 HIGH] 표식 잡 X 가 claim 된 직후, 그 claim 응답을 받기 전에 찍은 heartbeat A(X 없음)를 중계가 붙잡아 A 는 전체 기한(15초)을 넘겨 워커에서 버려진다. 그 사이 다음 heartbeat B(X 있음)가 닿고, 그 뒤 A 가 서버에 늦게 닿는다. 별도로 heartbeat 를 `seq` 3 → 5 → 4 순서로 보내고, `claims_settled` 가 X 의 `claim_seq` 보다 작은 heartbeat 를 보낸다 | X 는 `running` 그대로이고 프로세스가 산다. A 의 `jobs` 는 `seq` 가 받아들인 것 이하라 「없음」 판정에 쓰이지 않는다(`heard` 는 오른다). `claims_settled` 가 작은 heartbeat 도 X 를 닫지 않는다. 같은 그룹의 둘째 잡은 claim 되지 않는다(두 프로세스가 같이 산 구간 0). 워커는 `seq` 가 이미 적용한 것보다 작은 응답의 `cancel` · `paused` 를 적용하지 않는다. 근거: 결정 335 · 339-a · 339-b · `remote_workers.py:933-935` · 되돌릴 수 없는 결과(두 번 실행) | 단위(HTTP): `seq` · `claims_settled` 조합, DB `jobs.state` · claim, 워커의 응답 적용 + e2e: 중계가 요청 하나를 붙잡았다 놓음, pid | PR1 |
| RL51 | [감독자 창 · 쓸기 S6 · S7 · 339-c] 표식 잡에서 (a) 감독자가 시작 확인을 지난 뒤 명령 Popen 전에 SIGTERM 을 받는다(주입 훅) (b) 명령이 SIGTERM 에 곧 끝나고 SIGTERM 을 무시하는 자식이 남는다 — 워커가 산 채 · 워커가 `kill -9` 된 채 각각 (c) 명령이 SIGTERM 을 받고 `grace_J` 를 거의 다 쓴 뒤 끝나고 무시하는 자식이 남는다 | (a) 명령이 뜬 직후 SIGTERM 을 한 번 받는다(명령의 TERM 처리기가 쓰는 표지 파일 1개). (b)(c) 남은 자식이 첫 SIGTERM + `grace_J` + 0.5초 안에 사라진다 — 워커가 죽은 경우는 감독자가 쓸고(감독자도 −9 로 끝남), 산 경우는 러너 S7 의 SIGKILL 시한이 리더 끝이 아니라 첫 SIGTERM 부터다(2·`grace_J` 가 나오지 않는다). 셋 다 플래그 → 그룹 소멸 ≤ `grace_J` + 4초. 근거: 결정 331 · 339-c · F56 · F84 · F88 · 되돌릴 수 없는 결과(아무도 끊지 않는 고아) | e2e: 실제 러너 · 감독자, pid 생존 시각, 표지 파일 + 단위: 감독자 모듈(훅 · 부모 pid 주입) | PR1 |
| RL52 | [감독자 시작 실패 · 환경 · 339-c] (a) 감독자가 시작할 때 부모가 기대한 pid 가 아니다 · 이미 SIGTERM 을 받았다 (b) 명령의 argv[0] 이 없다 · 실행 권한이 없다 (c) 명령이 스스로 종료 코드 125 로 끝난다 (d) 잡 env 의 `PYTHONPATH` 와 워크스페이스 맨 위에 가짜 `signal.py` 가 있다 (e) rcm 을 설치하지 않고 소스 트리에서 `PYTHONPATH` 로 띄운 워커 | (a) 명령 0회, 감독자 종료 125 + 잡 로그 첫 줄 `[rcm] supervisor: not started: parent_gone`(또는 `terminated`), 결과는 러너 플래그대로(lost · cancelled) (b) 잡은 감독자 없을 때와 같은 요약 코드(`launch_executable_missing` · `launch_permission_denied`, `failed`) — 감독자의 실패가 명령의 종료 1 로 보이지 않는다 (c) 잡은 `failed`, 종료 코드 125(명령의 것) — 감독자 실패로 바뀌지 않는다 (d) 감독자는 가짜 모듈을 읽지 않고 명령은 받은 env 그대로 (e) 감독자가 뜨고 명령이 돈다. 근거: 결정 331 · 339-c · F83 · `runner.py:71-82` | 단위: 감독자 모듈(부모 pid · 신호 주입), 실제 러너 + 가짜 argv, env · 워크스페이스 표지 | PR1 |
| RL53 | [홀드 그룹 · 339-d] 표식 잡이 도는 중 (a) 그 프리셋을 설정에서 지우고 reload (b) 표식만 지우고 reload (c) 사용자 그룹 이름을 바꾸고 reload — 그 뒤 잡이 확인 없이 닫힌다. 별도로 v26 이전에 claim 된 레거시 잡(세션 · 저장값 없음, 그룹 NULL)이 닫히고 그 프리셋이 (d) 지금 표식 (e) 설정에 없음 (f) 표식 없이 있음 | (a)(b)(c) 홀드 1, 홀드의 그룹 · 범위 · 풀은 claim 때 잡 행에 남은 값(합성이면 `irreversible:<이름>` · `global`) — 지금 설정 값이 아니다. 같은 그룹의 대기 잡 claim 0건. (d)(e) 홀드 1, 그룹 `irreversible:<프리셋>` · `global`. (f) 홀드 0(일반 잡). 아무 잡도 막지 않는 그룹(`unknown:<job>` 류)이 생기지 않는다. 근거: 결정 330 · 339-d · F73 · F89 · 되돌릴 수 없는 결과(두 번 실행) | 단위: 설정 reload · store claim · 닫기, DB `irreversible_holds` | PR1 |
| RL54 | [홀드 알림 · 339-e] 표식 잡이 확인 없이 닫혀 홀드가 생긴다. `[[notify]]` 규칙 셋: `on` 을 안 적은 것 · `on = ["lost"]` · `on = ["irreversible_hold"]`. 알림을 보내기 전에 서버를 강제로 죽이고 다시 띄운다. 별도로 보낸 뒤 다시 띄운다 | `on` 을 안 적은 규칙과 `on = ["lost"]` 규칙은 그 잡의 잡 끝 알림을 한 번 받고(`RCM_EVENT=job_finished`) 그 알림에 `RCM_HOLD=1` · `RCM_HOLD_GROUP` 과 본문 한 줄 `group held — run rcm release-hold <job>` 이 실린다 — 훅 호출은 규칙마다 1회이고 홀드 알림이 따로 오지 않는다(6차). `irreversible_hold` 규칙은 홀드 알림을 정확히 한 번 받는다(`RCM_EVENT=irreversible_hold`, `RCM_HOLD_GROUP` · `RCM_HOLD_CAUSE` · `RCM_HOLD_WORKER`, URL 본문 `event` · `hold`). 못 보낸 알림은 기동 스캔이 보내고, 보낸 것은 다시 보내지 않는다. 홀드 알림 행이 잡 끝 알림의 기동 스캔을 막지 않는다. 근거: 결정 339-e(6차) · `store.py:3049-3063, 3081-3088` · `notify.py:305` | 단위: 가짜 훅 호출 수 · env · 본문, store 다시 열기 | PR1 |
| RL55 | [로그 읽기 · 339-f] 원격 잡의 로그 조각이 서버 파일에 써졌지만 커밋 전이다(쓰기와 커밋 사이 멈춤 주입) · 커밋이 실패해 되자르기 전이다. 그동안 `rcm logs N` · 웹 로그 보기(`GET /jobs/N/log?offset=`) · 상태의 로그 꼬리 · 잡 문서 꼬리를 읽는다 | 읽는 쪽은 `log_file_bytes` 까지만 받는다 — `X-RCM-Next-Offset` ≤ `log_file_bytes`, 꼬리에 커밋 안 된 줄 0. 커밋 뒤 다음 읽기에 그 바이트가 온다(빠짐 · 중복 0). 마커 사건(SSE)은 커밋 뒤에만 나간다. 로컬 레인 잡 · 레거시 잡(`log_file_bytes` NULL)은 오늘처럼 파일 길이까지. 근거: 결정 323 · 339-f · `server.py:5237-5256, 3789, 3851` | 단위(HTTP): 멈춤 주입, 응답 바이트 · 헤더 | PR2 |
| RL56 | [유예 닫기와 잠금 · 339-g] 죽은 세션 하나의 busy 잡 30개가 같은 틱에 유예를 넘는다. 그 닫기 트랜잭션 하나를 멈춰 둔 동안(주입) 다른 세션의 heartbeat 가 온다. 별도로 그 세션의 heartbeat 가 닫기 판정 뒤 · 커밋 전에 온다 | 다른 세션의 heartbeat 는 멈춘 닫기가 풀리기 전에 2xx 를 받는다. 한 틱에 닫히는 잡은 많아야 20개, 나머지는 다음 틱. 그 세션의 heartbeat 는 그 잡 하나의 닫기가 끝난 뒤 처리되고, 닫기 직전 다시 본 `heard` 가 판정 뒤에 올랐으면 그 잡은 닫히지 않는다. 닫힌 잡마다 상태 하나, 홀드 행은 표식 잡에만. 근거: 결정 328 · 339-g | 단위: store 트랜잭션 멈춤 주입, 두 스레드, 주입 시계 | PR1 |

LIVE_FORBIDDEN: 운영 서버 · 운영 워커 · 실제 deploy/store 프리셋을 쓰는 실행은 어느 행에도 쓰지 않는다. 대역은 `test-server.md` 규칙의 시험 서버(자기 설정 · `port` 8790~8899 · `data_dir` `~/.local/share/rcm-devtest-<topic>`) + 가짜 `sh -c` 표식 프리셋 + 표준 라이브러리 TCP 중계(`tests/tcp_relay.py`, 끊었다 잇기)다. RL28 · RL29 의 화면 확인(실기)도 시험 서버와 CDP 로 한다. 두 행은 pytest · node 시험이 JSON · 문구를 잠그므로 `(live)` 표시를 달지 않았다(`(live)` 행은 pytest 누락으로 세지 않아 잠금이 빠진다).

Excluded (rare): 펜싱된 세션이 끝나는 도중 서버가 재시작된다 · ① · 되돌릴 수 있다(A 가 다음 요청에서 같은 409 를 받는다). RL10 과 RL7 이 각각 잠근다.
Excluded (rare): 워커의 스풀 · 큐 파일이 디스크 오류로 손상된다 · ③ · 로그 · finish 지연뿐이고 서버가 `lost` 나 `log_complete = false` 로 닫는다. CHANGELOG · `docs/acceptance/reports/` 검색 0건.
Excluded (rare): 워커가 7일(`WORKSPACE_KEEP_DAYS`) 넘게 꺼져 있다가 큐에 남은 finish 를 들고 시작한다 · ① · 서버가 오래전에 `lost` 로 닫았고 결과는 RL21(b)와 같다.
Excluded (rare): 워커 데이터 폴더가 `flock` 을 지원하지 않는 파일 시스템에 있다 · ③ · 되돌릴 수 있다(이어받기를 건너뛰어 서버가 G 까지 기다린 뒤 닫는다 — 늦을 뿐 두 번 돌지 않는다). CHANGELOG 의 `flock` 1건(`CHANGELOG.md:1694`, macOS · Linux 에서 동작한다는 예시) · reports 검색에 지원 안 함 사례 0건.
Excluded (rare): 서버를 다른 `worker_timeout_seconds` · `worker_heartbeat_seconds` 로 재시작했는데 그 전에 claim 된 표식 잡의 얼린 G 가 새 하한 L + m 보다 작다 · ①(설정 변경 재시작과 표식 잡이 도는 중이 겹침) · 되돌릴 수 있다(연결된 채 표식 잡이 일찍 멈춰 `lost`(3) — 두 번 돌지 않는다). CHANGELOG · reports 에 `worker_timeout_seconds` 를 도는 중 바꾼 사례 0건(나온 것은 키 소개 `CHANGELOG.md:2296-2300`).
Excluded (rare): 러너의 거두기 SIGKILL 뒤 2초 안에 그룹이 사라지지 않는다(끊을 수 없는 대기 상태 · 거두지 못한 좀비) · ③(OS 가 SIGKILL 을 늦게 처리) · 되돌릴 수 있다(`group_gone: false` → 홀드, 사람이 확인하고 푼다 — 두 번 돌지 않는 쪽). 검색: CHANGELOG 「uninterruptible」 0건(「state」 낱말 3건은 무관), `git log --grep=setsid` 0건, reports 0건.
Excluded (rare): 워커가 S4 의 「확인 → `proc.json` 표시 → Popen」 몇 ms 사이에 `kill -9` 된다 · ②(사람도 rcm 의 고리도 그 창을 겨냥하지 않는다) · 되돌릴 수 있다(`pgid: null` → 「모름」 → 홀드, 사람이 푼다 — RL31(c) 가 그 판정을, RL43(c) 가 감독자의 닫힌 쪽을 잠근다).
Excluded (rare): heartbeat 2xx 둘을 잇달아 잃는 동안 서버는 그 요청을 받았다(응답만 사라짐) · ②(응답만 두 번 잇달아 사라지는 분단) · 되돌릴 수 있다(표식 잡이 연결된 채 멈춰 `lost`(3) — 두 번 돌지 않는 쪽, §4.1.5 정리 2).
Excluded (rare): 고아 그룹의 리더가 죽은 뒤 그 pgid 와 같은 번호로 새 프로세스가 새 그룹을 만들고 그 리더도 죽는다 · ②(pid 가 한 바퀴 도는 시간 안에 두 번) · RL31(b)와 같은 「모름」 길로 가서 신호를 보내지 않는다 — 되돌릴 수 없는 쪽(남의 그룹을 죽임)으로 가지 않는다. 검색: CHANGELOG · reports · git log 「pid reuse」 0건.
Excluded (rare): 감독자가 명령 끝 뒤 쓸기를 하려는데 `ps` 가 없거나 실패한다 · ③(OS 의 `ps` 가 계약 밖으로 답함) · 되돌릴 수 있다(「남았다」로 보고 첫 SIGTERM + `grace_J` 에 그룹 SIGKILL — 일찍 끝날 수 있던 잡이 `grace_J` 만큼 늦을 뿐, 남기지 않는다). 검색: CHANGELOG · reports · `git log --grep=pgid` 에 `ps` 실패 사례 0건.

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

3판에서 2라운드 지적과 4차 결정에 따라 고친 것은 다음과 같다.

- RL1: 자기 멈춤의 결과가 `cancelled` 가 아님을 적었다. 서버가 먼저 닫으면 홀드가 걸리고 돌아온 finish 가 그 홀드를
  푼다(V1-2 · V2-2, 결정 330 · 334).
- RL2: `worker_unreachable` 닫힘에 걸리는 홀드와 그 풀림을 더했다.
- RL5: 409 `job_closed` 가 일반 잡을 죽이지 않는 근거를 `closed_by_server` 분리로 바꿨다(F66).
- RL8: `had_cancel` 멈춤이 `should_cancel` 길임을 적고 표식 잡이 `cancel_unconfirmed` 로 닫히면 홀드가 걸리게 했다.
- RL10: 좁은 확인의 `group_gone` 과 홀드 0 을 더했다.
- RL11: 세션 헤더 형식 오류만 PR1 에 두고 오프셋 헤더는 RL16 으로 옮겼다(V1-4). 다른 이름의 세션 403 을 더했다(V1-8).
- RL15: TZ 와 로캘이 다른 `ps` 출력을 더했다(V1-7, F71).
- RL16: 오프셋 헤더 400 을 PR2 에 더했다(V1-4).
- RL24: 레거시 잡의 `heard_legacy` 와 홀드를 더했다(V1-5).
- RL30: 3판 L 공식과, 키를 적지 않은 채 큰 `worker_timeout_seconds` 를 쓰는 경우를 더했다(V1-9).
- RL31: (c) 를 `pgid: null` 로 바꿨다. `proc.json` 이 없으면 「띄운 적 없음」이다. (나)에 홀드를 더했다(330).
- RL34: (c) 에 2xx 하나를 잃는 경우와 e2e 설정을, (e) 에 조금씩 흘리는 응답과 전체 기한을, (f) 에 러너 단조 시계를
  더했다(V1-3 · V2-11 · V2-3).
- RL35: W4 의 모든 끝 상태를 다룬다(V2-6).
- RL36: (e) 펜스 뒤 커밋을 더했다(V2-7).
- RL37: (c) 끝난 잡의 짧은 파일을 더했다(V2-10).
- 새 행 아홉을 더했다.
  - RL41 은 Popen 전 발동이다. HIGH V2-1 을 재현하고 두 번 실행이라는 되돌릴 수 없는 결과를 다룬다.
  - RL42 는 그룹 홀드다. 되돌릴 수 없는 결과를 다룬다.
  - RL43 은 감독자다. 아무도 끊지 않는 고아라는 되돌릴 수 없는 결과를 다루고 워커 재시작 때문에 흔하다.
  - RL44 는 인증된 확인으로 푸는 경우다. 워커 재시작 때문에 흔하다.
  - RL45 는 홀드가 보이는지와 admin 풀기다. 오너 결정 330 의 「어디서나」를 확인한다.
  - RL46 은 죽은 세션 큐 대기의 상한이다. 서버 디스크가 가득 차거나 재시작할 때 흔하다.
  - RL47 은 첫 등록과 레거시 잡이다. 업그레이드 때문에 흔하다.
  - RL48 은 합성 그룹이다.
  - RL49 는 세션을 빠져나간 자손이다. 막지 못하는 되돌릴 수 없는 결과를 숨기지 않는다.
- 제외 줄 셋을 더했다. 거두기 SIGKILL 뒤에도 남는 프로세스, S4 의 몇 ms 창, 2xx 를 두 번 잇달아 잃는 경우다. reload 직후의
  그룹 없는 대기 잡은 되돌릴 수 없는 결과라 제외하지 않고 RL48 에 넣었다.
- 뺀 행은 없다.

4판에서 3라운드 지적과 결정 339 · 350 에 따라 고친 것은 다음과 같다.

- RL9: T5 의 「없음」은 `seq` 와 `claims_settled` 가 맞을 때만 세고 표식 잡이면 홀드를 건다(339-a · 339-b).
- RL24 · RL47: 레거시 잡의 X 는 「표식이거나 프리셋이 없음」이고 그룹은 잡 행 또는 `irreversible:<프리셋>` 이다(339-d).
- RL43(d): 감독자만 죽으면 러너가 S7 로 명령을 거두고 잡은 `failed`(−9), 홀드 0 이다. 3판의 「뒤의 취소가 `cancelled`」는 닿지
  않는 단언이었다(V1-5).
- RL45: PR 칸을 기계 판독(PR1)과 사람용(PR3)으로 나눴다(결정 350).
- 새 행 일곱을 더했다.
  - RL50 은 HIGH 의 재현이다. 두 번 실행이라는 되돌릴 수 없는 결과를 다룬다.
  - RL51 은 감독자의 창과 쓸기다. 아무도 끊지 않는 고아라는 되돌릴 수 없는 결과를 다룬다.
  - RL52 는 감독자 시작 실패와 환경이다. 소스 트리 실행과 잡 env 의 `PYTHONPATH` 때문에 흔하다.
  - RL53 은 홀드의 그룹이다. 되돌릴 수 없는 결과를 다룬다.
  - RL54 는 홀드 알림이다. 결정 339-e 를 확인하고 6차에서 바뀐 꼴(잡 끝 알림의 `RCM_HOLD` 와 opt-in `irreversible_hold`)로 적었다.
  - RL55 는 로그 읽기 상한이다. 도는 잡의 `rcm logs` 때문에 흔하다.
  - RL56 은 유예 닫기와 잠금이다. rcm 의 고리끼리 겨루는 일은 흔하다.
- 제외 줄 하나(`ps` 실패)를 더했다. 뺀 행은 없다.

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
| 3판(V1-13, F72): `tests/test_outcome.py` `PLAUSIBLE`(`:48-85`) · `PINNED`(`:88-214`) 와 그 전수 잠금(`:220-222` · `:225-231`), 필수 인자 표 시험(`:511`) | 오늘 코드 전수 | 새 코드 넷(`worker_self_stopped {seconds}` · `worker_replaced {name}` · `snapshot_unreachable {}` · `orphan_unverified {name, seconds}`)의 그럴듯한 인자와 박힌 문장을 더함 — 문장은 PR1 이 정하고 그 뒤 바꾸면 사용자 글자가 바뀐다는 뜻 | PR1 |
| `tests/test_notify_rules.py:59-88` `ENV_KEYS`(대조 `:154, :207`) | 잡 알림 env 키 집합 | `RCM_EVENT` 를 더함. 홀드를 만든 잡의 잡 끝 알림(+`RCM_HOLD` · `RCM_HOLD_GROUP`)과 `irreversible_hold` 알림(+`RCM_HOLD_GROUP` · `RCM_HOLD_CAUSE` · `RCM_HOLD_WORKER`)의 키 집합은 새 시험이 따로 잠근다 | PR1 |
| `tests/test_status_schema.py:261-268`(최상위 키) | 키 여섯 | `irreversible_holds` 를 더함 | PR1(3판은 PR3) |
| 3판: dev `tests/test_doctor_cli.py:993-1017`(`TOP_KEYS`) | doctor JSON 최상위 키 | `irreversible_holds` 를 더함(`doctor_version` 1) | PR3 |
| 3판: `tests/test_runner.py`(러너 계약 — 취소 · 시한 · 정지가 1초 점검 박자 · 벽시계 `now_fn` 을 가정한 시험이 있으면) | 벽시계 주입 | 점검 박자 · grace 는 `mono_fn` 주입으로 같은 결론(잡 시한은 그대로 `now_fn`). 시험 도우미 `group_gone`(`:241`)은 그대로 쓴다 | PR1 |
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

3판에서 더한 변이는 아래와 같다(V1-15 · 330 · 331 · 2라운드 지적).

| 이름 | 파일 · 바꿈 | 빨개져야 할 행 | PR |
|---|---|---|---|
| `popen-check-only-with-requires` | `runner.py` Popen 직전 확인을 `if spec.requires:` 안으로 되돌림(2판 꼴) | RL41 | PR1 |
| `self-stop-reports-cancelled` | 감시가 `self_stop` 대신 `should_cancel` 을 세움 | RL1 RL41 | PR1 |
| `closed-by-server-cancels` | 관찰자 `should_cancel` 에 `closed_by_server` 를 되돌림 | RL5 | PR1 |
| `runner-grace-wall-clock` | SIGTERM→SIGKILL 타이머를 `now_fn` 으로 | RL34 | PR1 |
| `heartbeat-no-deadline` | heartbeat 전체 기한을 지움(소켓 시한만) | RL34 | PR1 |
| `lower-bound-one-timeout` | L 을 2판 공식 `2H + 15` 로 | RL34 RL30 | PR1 |
| `session-name-unchecked` | 세션 판정에서 이름 대조를 지움 | RL11 RL44 | PR1 |
| `first-register-keeps-legacy` | 첫 등록이 세션 NULL busy 잡을 안 닫음 | RL47 | PR1 |
| `hold-not-created` | 확인 없는 닫힘에 홀드를 안 만듦 | RL42 RL31 RL2 | PR1 |
| `hold-released-without-group-gone` | `group_gone` 이 거짓이어도 홀드를 풂 | RL44 | PR1 |
| `hold-release-any-name` | `previous_sessions` 의 이름 대조 없이 홀드를 풂 | RL44 | PR1 |
| `hold-row-deleted-by-gc` | 오래된 잡 지우기가 열린 홀드의 잡을 지움 | RL42 | PR1 |
| `synthetic-group-missing` | 그룹 없는 표식 프리셋에 합성 그룹을 안 만듦 | RL48 | PR1 |
| `irreversible-null-group-unbound` | 그룹 NULL 인 표식 잡의 claim 이 유효 그룹을 적지도 배제에 쓰지도 않음 | RL48 | PR1 |
| `orphan-unverified-code-dropped` | 유예 만료 닫기가 `orphan_unverified` 대신 늘 `worker_unreachable` | RL31 RL29 | PR1 |
| `supervisor-no-parent-check` | 감독자 시작 때 부모 확인을 지움 | RL43 | PR1 |
| `supervisor-ignores-parent-death` | 감독자의 `getppid()` 감시를 지움 | RL43 | PR1 |
| `ps-lstart-locale` | `ps` 호출에서 `LC_ALL=C TZ=UTC` 를 지움 | RL15 | PR1 |
| `log-commit-ignores-fence` | 로그 커밋 UPDATE 에서 「세션이 아직 현재」를 지움 | RL36 | PR2 |
| `dead-session-wait-forever` | 등록 대기 300초 상한을 지움 | RL46 | PR2 |
| `finished-log-not-checked` | 기동 정리가 끝난 잡의 파일 길이를 안 봄 | RL37 | PR2 |
| `finished-at-unclamped` | 워커 `finished_at` 자르기(결정 277)를 지움 | RL27 | PR2 |
| `late-result-twice` | `late_result` 의 「아직 없으면」 확인을 지움(R4) | RL21 | PR2 |
| `hold-reason-not-needs-action` | 홀드에 막힌 대기 잡의 사유를 `blocked_by_group` 으로 | RL45 | PR1 |

변이는 1판 11개, 2판 15개, 3판 24개를 더해 50개다. `fenced-late-result-recorded` 는 펜싱된 세션의 늦은 finish 가 PR2 의
`late_result` 길을 지나므로 PR2 에 둔다. RL11 은 PR1 행이지만 PR2 가 그 행의 시험에 `late_result` 0건 단언을 더한다.

4판에서 더한 변이는 아래와 같다. 이름은 모두 `scripts/mutcheck.py` 에서 grep 0 이다. 3판 표의 `hold-reason-not-needs-action` 은
결정 350 에 따라 PR1 로 옮겼다. `hold-notify-default-on` 과 `job-end-hold-env-missing` 은 6차에서 바뀐 339-e 를 지키려고 더했다.

| 이름 | 파일 · 바꿈 | 빨개져야 할 행 | PR |
|---|---|---|---|
| `hb-stale-seq-judged` | `remote_workers.py` 받아들인 seq 이하인 heartbeat 의 `jobs` 로도 「없음」을 판정 | RL50 | PR1 |
| `t5-ignores-claims-settled` | 「없음」 판정에서 `claims_settled ≥ claim_seq` 조건을 지움 | RL50 RL9 | PR1 |
| `t5-confirms-irreversible` | T5 닫기에서 표식 잡의 홀드를 만들지 않음 | RL9 RL50 | PR1 |
| `claim-settled-before-running` | 워커가 claim 번호를 닫은 뒤 다른 잠금 구간에서 `running` 을 넣음 | RL50 | PR1 |
| `stale-hb-response-applied` | 워커가 늦게 온 heartbeat 응답의 `cancel` 을 seq 와 상관없이 적용 | RL50 | PR1 |
| `supervisor-popen-window-term-dropped` | Popen 뒤 적힌 SIGTERM 을 그룹에 보내지 않음 | RL51 | PR1 |
| `supervisor-no-post-command-sweep` | 명령 끝 뒤 남은 멤버를 안 보고 바로 끝남 | RL51 | PR1 |
| `reap-grace-from-leader-end` | 러너 S7 의 SIGKILL 시한을 리더 끝 + `grace_J` 로(3판 꼴) | RL51 | PR1 |
| `supervisor-failure-exit-one` | 감독자 미시작을 종료 1 · 표지 없이(3판 꼴) | RL52 | PR1 |
| `supervisor-not-isolated` | 감독자 실행에서 `-I` 를 지움 | RL52 | PR1 |
| `hold-group-from-current-preset` | 홀드의 그룹을 잡 행 대신 지금 프리셋 설정에서 | RL53 | PR1 |
| `legacy-hold-open-when-preset-gone` | 레거시 잡의 X 를 「프리셋 없음 → 0」으로 | RL53 | PR1 |
| `hold-notify-missing` | 홀드 사건을 버스에 안 냄 | RL54 | PR1 |
| `hold-notify-blocks-job-scan` | 기동 스캔이 `hold:` 행을 잡 끝 알림 행으로 셈 | RL54 | PR1 |
| `hold-notify-default-on` | `config.py` `on` 을 안 적은 규칙의 기본에 `irreversible_hold` 를 넣음(4판 재료 꼴 — 6차에서 버림) | RL54 | PR1 |
| `job-end-hold-env-missing` | 홀드를 만든 잡의 잡 끝 알림에 `RCM_HOLD` · `RCM_HOLD_GROUP` · 본문 한 줄을 싣지 않음 | RL54 | PR1 |
| `log-read-past-committed` | `log_bytes` 가 `log_file_bytes` 를 안 봄 | RL55 | PR2 |
| `grace-close-global-lock` | 유예 닫기를 전역 잠금 하나로(3판 꼴) | RL56 | PR1 |

변이는 3판까지 50개에 4판 18개를 더해 68개다.

### 7.5 시험 도구

- `tests/tcp_relay.py` 는 PR1 이 새로 만들고 표준 라이브러리만 쓴다. `pause()` 는 새 연결 수락을 멈추고 기존 연결의 응답을
  보류해 분단을 만든다. 이때 워커 요청은 시한까지 매달린다. `stop()` 은 연결을 거부한다. 서버 정지를 흉내 내는 함수는
  아니다. 정지는 실제 서버를 멈춰서 만든다. `resume()` 은 중계를 되살린다.
- 시계: 서버에는 `now_fn` 과 새 `mono_fn` 을 주입한다. 워커에는 `now_fn` 과 새 `mono_fn` 인자로 단조 시계를 주입한다.
  `RemoteWorker.__init__` 에는 이미 `now_fn` · `rand_fn` 을 주입한 선례가 있다(`remote_worker.py:257-270`). 감시 스레드는 틱
  한 번을 부르는 함수를 따로 두어 단위 시험이 스레드 없이 부를 수 있게 한다.
- 러너 대역: 플래그에서 그룹 소멸까지의 시간을 주입할 수 있는 가짜 러너로 RL34 의 경계를 계산한다. 실제 러너는 e2e 에서
  쓴다.
- 신원: `/proc` 뿌리 경로를 주입해 가짜 stat 파일을 쓰고 `ps` 실행 함수를 주입해 실패와 다른 시작 시각을 흉내 낸다.
- 서버 로그 쓰기 고장 주입: 파일 쓰기 OSError, 커밋 때 SQLite 바쁨, 커밋 0행, 쓰기와 커밋 사이 멈춤을 주입한다. 마지막
  것으로 두 스레드가 줄을 서는지 확인한다.
- 20초가 넘는 시험은 `@pytest.mark.slow` 를 단다(AGENTS.md:130-133). e2e 의 G 에는 하한이 있고 기본 설정이면 95초다. 그래서
  e2e 서버는 `worker_timeout_seconds` 를 최소값 10(`config.py:1429`)으로, `worker_heartbeat_seconds` 를 1 로 낮춘다. 3판의
  하한은 max(10, 2 + 45 + 5) + 35 = 87초다. 2판의 52초는 연결된 잡을 멈췄다(V1-3). e2e 서버는 G = 90 을 쓰고 문턱은 55 다
  (§4.1.5 정리 3). G 만료를 실제로 기다리는 e2e 는 RL1 · RL2 · RL5 · RL7(b) · RL10(확인 없음) · RL42 뿐이고
  `@pytest.mark.slow` 를 단다. 그 밖의 경계는 주입 시계를 쓴 단위 시험으로 본다.
- 3판 감독자 시험: 모듈 단위 시험은 `--parent` 에 시험 프로세스가 아닌 pid 를 주어 「시작 때 다름」을 본다. 짧게 사는 부모
  프로세스를 띄워 `kill -9` 해서 「바뀜」도 본다. F78 의 원형과 같은 꼴이다. 폴링 간격과 grace 는 인자로 줄여 1초 안에
  끝낸다. `getppid()` 는 실제 값을 쓰고 가짜 대역은 두지 않는다. 이 기능의 전부가 OS 동작이기 때문이다.
- 3판 heartbeat 기한 시험: 응답 바이트를 0.5초마다 하나씩 흘리는 가짜 서버를 표준 라이브러리 소켓으로 만든다. 소켓 시한
  안쪽이라 `urlopen` 은 끊지 않고 기한이 끊는다.
- 4판: `tests/tcp_relay.py` 에 `hold_next(path)` 와 `release_held()` 를 더한다. 다음 요청 하나(예 `/worker/heartbeat`)를
  붙잡았다가 나중에 서버로 넘긴다. RL50 의 순서 뒤바꿈을 만든다. 나머지 요청은 그대로 흐른다.
- 4판: 감독자 모듈에 시험용 훅을 둔다. 잡 env 로 켤 수 없도록 환경 변수가 아니라 인자로 준다. 「시작 확인 뒤, Popen 전 멈춤」이
  RL51(a)에 쓰인다. 「Popen 실패 흉내」는 없는 argv 로 만든다.
- 4판: 서버 잠금 시험(RL56)은 store 의 닫기 트랜잭션 안에 멈춤 훅을 두고 다른 스레드의 heartbeat 응답 시각을 잰다.

## 8. 개인정보 점검 (dev 에서 main 으로 가는 릴리스 PR 때)

- 오너 발언 가운데 다른 프로젝트의 이름, 경로, 식별자는 명세에 옮기지 않는다. 머리 인용에는 이름이 없는 문장만 둔다. Codex
  원문과 오너 발언 원본 파일은 레포 밖에 있으므로 경로를 적지 않는다.
- 시험의 세션 id 와 토큰은 실행 중에 만든다(F51). 높은 엔트로피 리터럴은 gitleaks 이력 문제를 만들므로 쓰지 않는다.
- 실기 보고서(PR4)의 스크린샷과 로그에 홈 경로, 호스트 이름, 사설 IP 가 없는지 본다. 시험 서버 `data_dir` 은 `~` 로
  적는다.
- 워커 로그 줄(고아 정리, 펜싱, 자기 멈춤, grace 줄임, 남의 세션 폴더 건너뜀)에 경로, 토큰, 서버 URL 의 자격 정보가 없는지
  본다. 오늘 규칙은 상태 코드만 공개하는 것이다(`remote_worker.py:922-925` 주석).
- 세션 폴더의 `owner.json` 에는 토큰 값과 서버 URL 원문을 적지 않는다. 서버는 sha256 으로, 토큰은 이름으로만 적는다(결정
  325).
- 릴리스 PR 에서는 메인 세션이 레포 밖의 패턴 파일로 점검한다(오너 지시 2026-11-23).
- 3판: 감독자의 잡 로그 줄, 홀드의 `action.note`, 「Needs a look」 문구, `rcm check` 와 `rcm doctor` 줄에는 잡 번호, 프리셋,
  워커 이름, 그룹만 싣는다. `/api/status` 가 이미 토큰 없이 보이는 것들이다(F75). 경로, 명령 인자, 토큰, 서버 URL 은 싣지
  않는다. `release_reason` 은 admin 이 쓴 글이므로 토큰 없이 읽히는 `/api/status` 와 `/api/health` 에는 싣지 않는다. 토큰이
  있어야 읽는 잡 문서에만 싣는다.
- 3판: F71 의 실측 줄 가운데 호스트 이름이 든 `uname` 출력은 옮기지 않았다. 명세에는 OS 판만 적는다.
- 4판: 홀드 알림에는 잡 번호, 프리셋, 그룹, 원인 코드, 워커 이름, 명령 이름 `rcm release-hold N` 만 싣는다. 잡 끝 알림에 더하는
  `RCM_HOLD` · `RCM_HOLD_GROUP` · 본문 한 줄과 opt-in `irreversible_hold` 알림의 env · URL 본문이 모두 그렇다.
  `release_reason`(admin 이 쓴 글), 경로, 서버 URL 의 자격 정보는 싣지 않는다. `RCM_URL` 은 오늘 키 그대로다.

## 9. 위험

위험마다 무엇이 그 위험을 막는지 적는다. 막지 못하는 것은 「남는 위험」이라고 적었다.

| 위험 | 막는 것 |
|---|---|
| 일반 프리셋 잡이 G 뒤 `lost` 가 된 뒤에도 워커에서 계속 돌아 같은 그룹의 다음 잡과 겹친다 | 결정 253 이 받아들인 맞교환(일반 프리셋은 두 번 돌아도 된다). 결과는 `late_result` 로만 남는다. 취소 요청이 있던 잡은 다시 붙은 워커가 멈춘다(결정 322). 문서에 적는다 |
| 워커 벽시계가 앞으로 `G − m`(기본 565초) 이상 뛰면 표식 잡이 일찍 멈춘다 | 결과는 `lost`(3) — 모름이지 실패가 아님. 문서에 「표식 잡을 도는 워커는 NTP 단계 보정을 피하라」. 벽시계를 빼면 잠자기를 못 센다(결정 320) |
| 서버가 heartbeat 에 5xx 를 `G − m` 동안 계속 내면(요청은 받으므로 서버는 안 닫는다) 표식 잡이 멈춘다 | 결과는 `lost`(3), 두 번 돌지 않는 쪽(RL34 d). 워커가 「서버가 받았는지」를 5xx 로는 알 수 없다 |
| 서버 재시작 뒤 원격 잡이 최대 G 동안 더 `running` 으로 남아 그룹 자리를 잡는다(워커가 재시작 전에 죽었어도) | 보수적인 쪽(두 번 실행보다 늦음, 2차 Q-C). 워커가 돌아오면 바로 닫힌다 |
| 펜싱된 세션이 분단이면 그 잡이 G 까지 레인 · 그룹을 잡고 화면 사유는 `running`(그 이름의 현재 세션이 살아 있어 레인이 down 이 아니다) | 표시 한계로 문서에 적는다. 잡은 G 에 닫힌다 |
| `kill -9` 된 워커가 다시 시작되지 않으면 고아 표식 잡을 아무도 멈추지 않는다(2판: G 뒤 그룹이 풀린다 — V2-5) | 3판: 감독자가 0.25초 + `grace_J` 안에 명령 그룹을 끊는다(결정 331, F78). 서버는 프로세스 죽음 확인 없이 닫으므로 그룹 홀드(결정 330) — 아무도 다시 띄우지 않으면 홀드가 남아 사람이 그 머신을 보고 `rcm release-hold` 한다. 다시 뜨면 그 워커의 「그룹 없음」 확인이 홀드를 푼다 |
| 고아 신원을 끝내 확인하지 못한 표식 잡(리더만 죽은 그룹 · `ps`/`/proc` 실패 · 띄우는 순간 죽음 · 감독자만 죽음) | 3판: 서버가 G 에 `lost` + `orphan_unverified` 로 닫고 **그룹 홀드**를 건다(결정 330 — 2판의 「G 에 그룹이 풀린다, 남는 위험」은 더는 없다). 신호는 보내지 않는다(남의 프로세스를 죽이지 않음). 사람이 그 머신을 보고 푼다. Linux `/proc` 는 이 머신에서 재지 않았다(F59) |
| 명령이 `setsid` · 이중 fork 로 세션을 빠져나간 자손을 남긴다 — **남는 위험** | 감독자도 워커도 그 프로세스를 볼 수 없다(macOS 에는 자손을 거두는 subreaper 가 없다). 그룹은 사라지므로 워커의 확인(`group_gone: true`)이 서고 홀드가 없다. 막지 못함을 숨기지 않는다: 문서 「`irreversible` 프리셋의 명령은 데몬 · 세션 밖 프로세스를 남기지 않는다」 + RL49 가 그 동작을 고정한다. 오너 결정 331 의 「명령이 세션을 빠져나가는 경우는 330 이 막는다」 는 확인이 오지 않는 경우(워커 · 감독자가 함께 죽음)에만 맞다. 5차 위임 결정 (e): 이 경우를 fail-open 이라고 이름 붙여 RL49 와 문서가 그렇게 적고, `irreversible` 프리셋에는 세션을 빠져나가는 명령을 쓰지 않는다(§6 #18) |
| 감독자 자체가 죽는다(OOM · 누가 SIGKILL) | 워커가 살아 있으면 러너의 신호가 그대로 닿는다(같은 그룹). 워커까지 죽으면 확인이 없어 홀드(W13) |
| 홀드는 사람을 부른다 — 확인 없는 닫힘마다 admin 이 풀어야 한다 | 확인 길이 넷(§4.12)이라 흔한 경우(워커 재시작 · 돌아온 워커의 늦은 finish)는 사람 없이 풀린다. 남는 것은 워커가 다시 안 뜸 · 신원 모름 · 서버 강제 종료 중의 로컬 표식 잡. 홀드는 대기열 · 웹 · `rcm check` · `rcm doctor` · `rcm wait` 에 보인다 |
| admin 이 머신을 확인하지 않고 `rcm release-hold` 한다 | 사람의 결정이다 — 명령 도움말 · 문서가 「그 머신을 먼저 본다」 를 말하고, 풀기는 감사 이력(누가 · 언제 · 왜)과 사건으로 남는다 |
| 서버가 강제로 죽으면 로컬 레인의 표식 잡이 재기동 뒤 홀드가 된다(F76) | 감독자가 명령을 끊었어도 서버는 확인할 길이 없다(로컬 `proc.json` 없음). 드문 겹침이고 사람이 푼다. 정상 종료(`launchctl` · `systemctl` stop)는 그룹을 끊고 `group_gone` 을 보므로 홀드가 없다 |
| 그룹 없는 표식 프리셋이 합성 그룹으로 직렬이 된다(오늘은 그룹이 없어 동시에 돌 수 있었다) | 표식은 새 키라 오늘의 설정은 그대로다. 표식을 다는 순간의 동작 변화 — 기동 로그 한 줄 · `rcm presets` 의 그룹 이름 · 문서로 보인다(결정 330) |
| heartbeat 2xx 둘을 잇달아 잃으면 연결된 채 표식 잡이 멈출 수 있다 | 결과는 `lost`(3), 두 번 돌지 않는 쪽(§4.1.5 정리 2) |
| 같은 토큰의 두 번째 프로세스가 서비스를 펜싱하면 서비스는 종료 0 이라 다시 안 뜬다(결정 255 · 258) | 문서: `launchctl kickstart -k` / `systemctl --user restart` 로 다시 올린다. 두 번째 프로세스의 시작 줄이 「서비스 워커를 대신했다」를 말한다 |
| 워커 설정의 `server` 문자열이나 `server_name` 을 바꾸면 자기 옛 세션 폴더를 남의 것으로 본다(결정 325) | 그 폴더의 잡은 서버가 G 뒤 `lost` 로 닫는다(늦을 뿐 두 번 돌지 않음). 고아는 정리되지 않으므로 문서에 「설정을 바꾸기 전에 워커를 멈추고 잡이 끝나기를 기다려라」 |
| 로그 · 산출물 라우트의 5xx 가 300초를 넘으면 finish 가 먼저 가고 남은 로그 · 산출물을 버린다(결정 324) | 정직하게 표시: `log_complete = false`, 산출물 `failed` + `upload_failed`. 잡 결과 · 그룹 자리는 제때 풀린다 |
| 서버 OS 충돌로 `log.txt` 가 커밋된 길이보다 짧아진다(fsync 없음) | 빈 구멍 없이 이어 쓰고 `log_complete = false` 고정(결정 323, RL37). 잃은 바이트는 되찾지 않는다 |
| 표식 잡의 워커 `grace_seconds` 가 G 에 견줘 크면 그 잡에서만 grace 를 줄여 쓴다 | 설정 검증이 기본 grace(10)는 줄지 않게 보장. 줄였으면 워커 로그 한 줄(결정 320) |
| M5t-a 의 PR3~PR7 이 `config.py` · `capabilities.py` · `server.py` · `cli.py` · `docs/configuration.md` · `docs/operating.md` 를 고친다(F54) | 구현 PR1 은 M5t-a 전체가 dev 에 머지된 뒤 시작(§5). 이 명세의 줄 번호는 고정 sha 기준이라 구현 PR 이 다시 찾는다 |
| M5t-c 가 register(`pair_proof` · 풀 묶기)와 DB v27 을 쓴다 | `pair_proof` 는 §4.2 의 4 자리(버전 · 프로토콜 뒤, 부작용 앞, F52). v27 은 PR1 의 v26 뒤(M5t-c 결정 299) → 메인 세션이 M5t-c 명세와 함께 확인(§6 #11) |
| M5t-c 의 종료 코드(인증된 두 경우만 0 · 틀린 MAC hello · 421 · 인증 안 된 응답 · 연결 실패는 프로세스 안 재시도, 결정 303 · 317)와 이 명세의 0(펜싱) · 3(프로토콜 불일치)이 섞여 읽힌다 | 0 은 「되살리지 않음」 하나의 뜻(펜싱 · M5t-c 317 의 인증된 두 경우), 3 은 「일을 시작하지 말고 감독자가 다시 시도」 하나의 뜻(프로토콜 불일치)으로 문서에 함께 적는다(F52, 부모 명세 M8 을 좁힘) |
| (고침) 결정 번호가 두 명세에 걸친다 | M5t-b 256~279 · 320~339 · 350~359, M5t-c 280~319 · 340~349(317 · 343 이 이 명세의 §4.7 · §4.2 를 고친다) — 겹치지 않는다(§6 #12) |
| `irreversible` 낱말이 릴리스 쪽의 「되돌릴 수 없는 입력 값」(F26)과 뜻이 다르다(프리셋 전체) | 문서에 구별을 적는다. 입력이 rehearsal 이어도 자기 멈춤이 걸리는 것은 안전한 쪽 |
| 앱 프로젝트가 원격에서 돌 수 있는 배포 · 스토어 프리셋에 `irreversible` 을 달지 않으면 그 잡은 일반 잡처럼 끊겨도 계속 돈다 | `rcm check` · `rcm doctor` 가 이름으로 추측해 경고하지 않는다(거짓 경보, §1.3 · §12 V1 제안). 문서와 §6 #16 의 앱 쪽 작업으로 막는다 |
| 보고 큐가 디스크를 쓴다(로그가 두 벌: 로컬 `log.txt` + 스트림 스풀) | 7일 청소(F41). 디스크가 가득 차면 `log_complete = false` 로 정직하게 표시(RL20) |
| 옛 워커가 섞인 dev(같은 버전 문자열)에서 옛 워커가 2~4초마다 claim 409 로그를 찍는다 | 잡은 안 집는다(RL24). 출시 뒤에는 M5q 따라가기가 버전을 맞춘다 |
| (새) claim 응답을 잃은 표식 잡은 T5 로 닫히며 홀드가 걸리고, 살아 있는 워커는 그 잡의 finish 를 보내지 않으므로 사람이 풀거나 워커를 다시 띄워야 풀린다 | 결정 339-b 가 받아들인 값 — 「없음」은 확인이 아니다. claim 응답을 잃는 일은 claim 요청 하나의 응답만 사라지는 경우라 드물고, 홀드는 대기열 · 웹 · `rcm check` · 알림(339-e)으로 바로 보인다. 4판의 `claims_settled` 로 「없음」 자체는 순서와 상관없이 참이 됐다(F82) — 홀드를 더 줄이는 것은 다음 명세의 몫(§12.5 노트) |
| (새) 감독자가 명령 끝 뒤 쓸기로 그룹을 SIGKILL 하면 리더 종료가 −9 라 명령 자신의 종료 코드가 잡 결과에 남지 않는다 | 쓸기는 SIGTERM 을 받았거나 부모가 바뀐 뒤에만 — 그때 결과는 러너 플래그(취소 · 정지 · 자기 멈춤 · 시한)가 정하고 종료 코드는 정보일 뿐이다. 부모가 바뀐 경우는 결과를 받을 러너가 없다 |
| (새) 레거시 잡이 닫힐 때 그 프리셋이 설정에서 사라졌으면 홀드가 걸린다(업그레이드 직후의 드문 겹침) | 모름은 닫힌 쪽(결정 339-d). 그룹이 사용자 그룹이면 같은 그룹의 다른 프리셋이 막히고, 합성 그룹이면 같은 이름의 프리셋이 다시 들어올 때만 막힌다. 화면 · 알림에 보이고 admin 이 푼다 |
| (새) 홀드를 알림으로 아는 기본 길은 그 잡의 잡 끝 알림에 실린 `RCM_HOLD` · `RCM_HOLD_GROUP` · 본문 한 줄뿐이다 — 그 잡의 끝 상태를 `on` 에 고르지 않은 규칙은 홀드를 알림으로 모른다 | 홀드를 따로 받으려면 규칙의 `on` 에 `irreversible_hold` 를 적는다(opt-in). 새 사건을 기본으로 켜지 않은 것은 기존 훅이 같은 잡에 두 번 불려 호출 · 부작용이 겹치기 때문이다(6차, 결정 339-e). 홀드는 대기열 · 웹 · `rcm check` 에도 보인다. CHANGELOG 에 `RCM_EVENT` · `RCM_HOLD` 와 opt-in 값을 적는다 |
| (새) M5t-c 343: 짝지은 워커의 펜싱 409 가 MAC 없이(위장 · 경로 위 변조) 오면 워커는 끝나지 않고 다시 시도한다 — 진짜 펜싱이었다면 옛 프로세스가 그만큼 더 산다 | 진짜 서버는 짝지은 토큰에 늘 MAC 을 단다(M5t-c PR1 `_send_error`) — 검증 실패는 위장 · 변조뿐이다. 그동안 옛 세션은 서버에서 펜싱돼 있어 아무것도 반영되지 않고(§4.2), 표식 잡은 그 세션의 자기 멈춤 · 서버 홀드가 지킨다. 교차 명세 의존: M5t-b 의 409 가 `_send_error` 를 우회하면 MAC 이 안 붙어 짝지은 워커가 펜싱에 끝나지 않는다 → M5t-b 의 모든 오류는 `ApiError` 길(§4.2, §6 #20). 검증 안 된 `protocol_required` 도 짝지은 워커는 종료 3 대신 프로세스 안에서 다시 시도한다(M5t-c J13) — 재시작 되풀이를 만들지 않는다(§4.7) |
| (새) 감독자를 `-I` 파일 경로로 띄우므로 zip 으로만 깔린 rcm 은 감독자를 못 띄운다 | 러너가 파일이 없으면 `launch_failed`(`SupervisorMissing`)로 표식 잡을 띄우지 않는다(닫힌 쪽). rcm 설치(wheel · editable)는 파일이 있다(`scripts/smoke_install.sh` 가 감독자 파일 존재를 본다 — PR1) |

## 10. 요청서에 돌려줄 것

해당 없다. 요청서가 없다.

## 11. 결정 표

256~264 는 1차 위임 결정이고 265~279 는 2차 위임 결정이다. 2차에서 추천이던 것을 확정했다. 320~327 은 1라운드 격리 반박 뒤에 메인
세션이 오너 위임으로 정한 D-a~D-g 다(2026-11-26). 재료가 D-* 문구와 다르게 정한 곳이 넷 있다. D-a 의 `ok_at` 처음 값은 claim
요청을 보낸 시각으로 했다. D-b 의 「log gap」 표시는 409 `log_gap` 과 뜻이 겹치지 않게 `log_complete = 0` 고정과 서버 로그 줄로
했다. D-c 의 `log_complete` 는 서버가 바이트 수로 계산한다. D-g 의 경고는 요약 코드 `orphan_unverified` 로 낸다. 메인 세션은 넷
모두 더 안전하거나 기존 계약을 덜 바꾼다고 보고 받아들였다. 328 · 329 는 1라운드 지적을 고치며 재료가 정했고 같은 날 위임으로
확정됐다. 1라운드 결정 320~327 은 2라운드 뒤 Codex 와 논의했다(4차). 320~326 은 동의로 확정됐고 327 은 330 으로 바뀌었다. 330~332
는 4차 위임 결정이다. 333~338 은 2라운드 지적을 고치며 재료가 정했고 5차에서 Codex 와 논의해 확정했다. 339(세부 a~g)는 3라운드 뒤
메인 세션이 오너 위임으로 정했고 350 은 3라운드 지적을 고치며 재료가 정했다. 둘 다 6차에서 Codex 와 논의해 확정했고 339-e 는 그때
Codex 의견으로 바꿨다. 351~359 는 비워 둔다. 1판 행 가운데 2판이 문장을 바꾼 행은 「2판:」으로, 3판이 바꾼 행은 「3판:」으로,
4판이 더한 문장은 「4판:」으로 적었다. 결정을 뒤집은 것은 둘이다. 267 은 2판에서 320 이 대신했고 327 의 그룹 처리는 3판에서 330 이
바꿨다. 결정 번호는 M5t-b 가 256~279, 320~339, 350~359 를, M5t-c 가 280~319 와 340~349 를 쓰므로 겹치지 않는다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다른 점 |
|---|---|---|---|---|
| 256 | 일반 프리셋 유예 만료와 늦은 결과(Q1) | 유예 만료에 서버가 `lost`(`rcm wait` 3). 같은 세션의 늦은 finish 는 `late_result`(outcome · 프로세스 종료 코드 · 워커 finish 시각 · 수신 시각 · 로그 완전성)로만 남기고 `lost` 를 바꾸지 않으며 알림 · 로그 · 마커 · 산출물을 받지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 253 의 「계속 돈다」는 워커에서 계속 돈다는 뜻이고 요청자의 결과는 3 |
| 257 | 표식 키와 얼림(Q2) | 별도 프리셋 키 `irreversible = true`(`local_only` 는 배치, `irreversible` 은 끊김 안전). `irreversible` 과 `disconnect_grace_seconds` 는 claim 때 잡 행에 얼린다(reload · 재시작은 다음 claim 부터) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 키 이름이 정해짐(253 은 「표식」) |
| 258 | 워커 종료 코드(Q3) | 펜싱 0(감독자가 되살려 핑퐁하지 않게), 프로토콜 불일치 3(감독자가 다시 시도). 그 밖은 오늘대로. 2판: 프로토콜 불일치 3 은 등록 전에만(도는 중 갈래는 닿지 않아 지움, V2-9). M5t-c 결정 303 과 함께 0 = 「되살리지 않음」(펜싱 · 확실한 신원 거절), 3 = 「감독자가 다시 시도」(F52) 3판: M5t-c 결정 317 대로 0 은 펜싱과 M5t-c 의 인증된 두 경우뿐 — 틀린 MAC hello · 421 · 인증 안 된 응답은 종료 0 이 아니다(§4.7). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 259 | 준비 단계 끊김(Q4) | 명령이 돈 적 없고 실패 결과도 없으니 `lost`(3) + `snapshot_unreachable`. 저장소 없음 · 거절된 스냅샷 같은 확정 결함은 `failed`(1) 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 260 | 같은 프로세스 재등록(Q5) | 프로세스가 만든 세션 id 하나를 모든 레인이 쓴다. 같은 id 재등록은 멱등(펜싱 · 잡 닫기 없음). 다른 id 만 펜싱 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 261 | 펜싱된 잡의 그룹 자리(Q6) | 표식 여부와 상관없이 멈춤 확인(옛 세션이 409 를 받고 그룹을 죽인 뒤 좁은 범위의 「펜싱 멈춤 확인」 finish 를 보냄) 또는 얼린 유예 만료 전에는 풀지 않는다. 그동안 새 세션의 그 레인 claim 은 204 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 255 의 「409 를 받고 잡을 멈춘 뒤 끝난다」를 분단 때까지 넓힘 |
| 262 | 마이그레이션 때 옛 워커의 도는 잡(Q7) | 옛 lease 호환: 이미 도는 레거시 잡의 heartbeat · log · finish(와 그 잡의 phase · 산출물)만 받고 새 claim 은 모두 거절. 그 잡들이 끝나거나 유예에 닿으면 호환은 저절로 거둔다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 3판 250 의 「혼합 운영 경로는 만들지 않는다」를 레거시 lease 만큼 넓힘 |
| 263 | 유예 기본값과 검증 | `disconnect_grace_seconds = 600`(결정 253 의 「약 10분」). 정수, 86400 이하, 위반은 기동 실패. `[server]` 라 reload 불가. 2판: 하한은 1판의 「`worker_timeout_seconds` 보다 큼」 대신 결정 320 의 `L + 35`(기본 설정에서 95) 3판: 하한의 L 은 결정 335 의 `max(T, 2H + 3·T_req + 5)`(기본 설정에서 하한 95 그대로), 키를 안 적으면 `max(600, L + 35)`. | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 하한 공식은 결정 320 · 335(위임) | 「약 10분」 → 600초 |
| 264 | 재전송 창 · 보관 | finish · 로그 재전송은 잡이 끝난 뒤 24시간(시간으로만, 3판 r3 A-H6). 잡이 도는 동안은 상한 없음. 늦은 결과 · 스풀 보관은 워크스페이스와 같은 7일(`WORKSPACE_KEEP_DAYS`). 2판: 서버가 닿는데 로그 · 산출물만 5xx 면 300초 뒤 finish 먼저(결정 324) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 265 | 세션 모델 | 세션 id = 프로세스당 `token_hex(16)`. 새 표 `worker_sessions`(현재 = `fenced_at IS NULL`), `jobs.worker_session_id`. lease = (잡, 세션), 별도 `lease_id` 없음. 펜싱된 세션 행은 워커 행과 함께만 지움. 모든 보고가 세션을 싣고, 표에 없으면 409 `not_registered`, 펜싱이면 409 `session_fenced`, 펜싱된 세션의 재등록도 409 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 266 | 서버 유예 판정 | 잡마다 `mono_now − max(heard(세션), 서버 기동) > G_J` 면 `lost`(`worker_unreachable`). `heard` 는 메모리 단조 시각, 펜싱 뒤 요청은 안 올림. 레인 down 은 벽시계 그대로. 취소 미확인 닫기는 현재 · 들리는 세션의 잡에만. `App` 에 `mono_fn`. 2판: 「같은 락」은 결정 328 의 `_heard_lock`. 서버 재시작 뒤 유예는 기동 시각부터 다시 셈(2차 Q-C) 3판: 세션 판정에 이름 대조(T19), 레거시 요청은 `heard_legacy(이름)`(결정 336), 표식 잡의 유예 만료는 홀드와 한 트랜잭션(결정 330). 4판: 판정 잠금은 세션별, 닫기 커밋도 그 세션 잠금 안 · 틱 상한 20(결정 339-g). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-C) | — |
| 267 | 워커 자기 멈춤 | **2판에서 결정 320 이 대신한다**(1판: heartbeat 루프의 매 회차 보내기 전 판정, `m = grace_seconds + heartbeat_seconds + 5` — F53 으로 깨짐, V1-1 · V2-1). 「표식 잡만 · `max(벽, 단조)` 경과 · 그룹 SIGTERM→KILL · finish `lost` + `worker_self_stopped`」는 320 이 이어받는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차). 2판에서 320 으로 바뀜 | — |
| 268 | 재시작 순서 · 이어받기 · 고아 | 세션 폴더 `<data>/worker-sessions/<id>/` + `flock`. 시작 때 잠글 수 있는(죽은) 세션의 고아 그룹을 신원(시작 시각)이 같을 때만 정리, 남은 큐를 옛 세션 id 로 보낸 뒤 `previous_sessions` 로 등록. 서버는 그 세션들의 busy 잡을 `worker_restarted_without_job` 으로 닫음. 신원을 못 확인한 세션은 넘기지 않음(2차 Q-D, fail-closed). 관찰자에 `spawned(pid)`. 2판: 폴더는 프로토콜 검사 뒤에 만들고(V2-12) 주인 · 신원 · 재확인 · 응답 표는 결정 325~327 3판: `proc.json` 을 Popen 직전 · 뒤 두 번 쓰고(없음 = 띄운 적 없음, `pgid: null` = 모름), `ps` 는 `LC_ALL=C TZ=UTC` · 형식이 다르면 모름, 「확인됨」은 그룹 소멸까지 확인, `previous_sessions` 가 그 세션의 홀드를 푼다(결정 330 · 333). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-D) | — |
| 269 | 409 의 기계 판독 키 | 409 응답에 `code` ∈ {`session_fenced`, `protocol_required`, `not_registered`, `job_closed`(+`state`), `log_gap`(+`expected_offset`)}, 503 `log_write_failed`. 오늘의 `error` 문구는 유지. 워커의 응답별 처리는 §4.5 표. 2판: 로그 라우트의 커밋 바쁨은 오늘 코드 503 `database_busy`, 헤더 형식 오류는 400(`error` 만) — 결정 329 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 270 | 프로토콜 판 | `worker_protocol = 2` 를 `/worker/spec` · register 응답에. 새 워커는 등록 전 spec 확인, 키가 없으면 버전이 같을 때만 종료 3(다르면 오늘의 등록 길 — 버전 먼저) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 271 | 로그 오프셋 · 스풀 | 오프셋 = 가리기 전 스트림 바이트, 헤더 `X-RCM-Worker-Session` · `X-RCM-Log-Offset`, 응답 `offset`. 서버 `jobs.log_offset` · `log_file_bytes`, 기동 때 길면 자름. 조각은 256 KiB 이하 · `\n` 에서 자름. 워커는 스트림만 담는 `stream.log`. 3판의 `log.pending` 없음. 2판: 서버 쓰기는 덧붙이기가 아니라 결정 323 의 절차 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 272 | 순서 있는 보고 큐 | 잡마다 phase → 로그 → 산출물 → finish, 디스크(`outbox.json`), 보내는 스레드 하나, 백오프 1→30초, 도는 동안 상한 없음, 끝난 뒤 24시간. 레인은 자기 큐가 끝나야 다음 claim. 2판: `outbox.json` 에 `acked`, 그 프로세스의 첫 로그 전송 전 빈 본문 질의(결정 329), finish 상한(결정 324) 3판: 보내는 스레드 · 백오프는 잡마다(결정 337). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 273 | 로그 완전성 | finish 의 `log_bytes` 와 `log_offset` 으로 `jobs.log_complete`(1/0/NULL). 키 `log_complete` 를 최근 행 · 잡 문서 · wait JSON 에(더하기만). 2판: 짧은 파일이면 미리 0 고정(결정 323) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | 3판의 `log_incomplete` 를 긍정형 + null 로 |
| 274 | 늦은 결과 기록 | events kind `late_result` 1건, 종료 잡 문서 · wait JSON 키 `late_result`(객체/null), 409 본문 `late_result_recorded`. 펜싱된 세션의 늦은 finish 는 기록 안 함(2차 Q-B). 웹은 그리지 않음. 2판: 한 `BEGIN IMMEDIATE` 에서 확인 · 넣기 · `log_complete` 쓰기(V2-11, §4.6) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-B) | — |
| 275 | 산출물 재업로드 · ready 조건 | 업로드는 큐의 finish 앞 단계, 재전송 창 안에서 다시. 처분 `ready` 는 그 잡의 묶음 행 · 해시 · 서버의 `bundle.tar` 파일이 맞을 때만(2판에서 파일을 더함, V1-12), 아니면 `failed` + `upload_failed` / `hash_mismatch` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 276 | 유예 중 표시 | 도는 잡의 `reason` 새 값 `worker_unreachable_grace`(`_busy_reason` 맨 앞), `estimate.finish_at` null, `ACTIONABLE_REASONS` · 웹 `ACTIONABLE` 의 `worker_down` 바로 뒤. 레인 `down` · 헬스 그대로. `schema_version` 1 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 277 | 워커 `finished_at` 자르기 | `[started_at, 수신 시각]` 으로 자른다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 278 | `lost` 요약 코드 | 새 `LOST_CODES` = `worker_self_stopped` · `worker_replaced` · `snapshot_unreachable`(finish 의 `outcome = lost` 일 때만). 유예 만료는 `worker_unreachable`(cancelling 포함, `cancel_unreachable` 만들지 않음). 재시작 이어받기는 `worker_restarted_without_job`. 번역은 같은 PR. 2판: 서버만 쓰는 `orphan_unverified` 를 더함(결정 327) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차) | — |
| 279 | PR 별 레인 | PR0 short · PR1 full · PR2 full · PR3 full · PR4 short(§5) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-A) | — |
| 320 | 자기 멈춤 감시 스레드와 여유 m(D-a, 267 을 대신) | 표식 잡만. heartbeat 와 따로 도는 감시 스레드가 `WATCHDOG_TICK_SECONDS`(1초)마다 `wgap ≥ G_J − m_J` 를 본다. `ok_at` = 2xx 를 받은 요청을 **보낸** 시각(벽 · 단조), 잡의 첫 값은 그 claim 요청을 보낸 시각. `m_J = 15(heartbeat 요청 시한, client.py:83) + grace_J + SELF_STOP_SLACK_SECONDS(10 = 틱 1 + 러너 점검 4 + 남김 5, F56)`. 하한 `L = max(T, 2H + 15)`(2판 — 3판은 결정 335 의 `max(T, 2H + 3·T_req + 5)`), 설정 검증 `G ≥ L + 35`(`DISCONNECT_GRACE_MARGIN_SECONDS`), 표식 잡의 `grace_J = max(1, min(grace_seconds, G_J − L − 25))`. 넘으면 그룹 SIGTERM→`grace_J`→SIGKILL, finish `lost` + `worker_self_stopped`. 서버가 먼저 닫은 잡은 다음 2xx 의 `closed_jobs` 로 멈춘다 3판: 자기 멈춤의 길은 `should_stop` + `worker_self_stopped`(결정 334), Popen 직전 확인 · 러너 단조 시계(결정 333), L 은 결정 335. | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-a. `ok_at` 의 처음 값만 D-a 의 「claim 응답 시각」에서 「claim 요청을 보낸 시각」(더 이르다)으로 — §12 V2-6. 메인 세션이 받아들임. 2라운드 뒤 Codex 와 논의(4차) | 253 의 「유예(약 10분)가 지나면」 → 서버 유예 G 보다 m(기본 35초) 먼저 |
| 321 | 닫힌 잡 통보(D-a) | heartbeat 2xx 응답 `closed_jobs: [{id, state, had_cancel}]` — 그 요청의 `jobs` 목록 가운데 서버에서 terminal 이고 그 세션의 잡. 워커 반응은 W4 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-a(V2 제안). 2라운드 뒤 Codex 와 논의(4차) | — |
| 322 | 취소된 잡이 유예로 lost(D-d) | `had_cancel` = 잡 행의 `cancel_requested_at` 있음. 워커는 `lost` + `had_cancel` 인 일반 잡을 멈춘다(1판 RL8 b 를 고침) | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-d. 2라운드 뒤 Codex 와 논의(4차) | — |
| 323 | 서버 로그 쓰기 절차(D-b) | 잡별 로그 잠금 `_job_log_locks` 안에서 행 다시 읽기 → 오프셋 확인 → `log_file_bytes` 자리에 쓰기 · 뒤 자르기 → 조건부 커밋(`log_offset` 이 그대로 · busy · 같은 세션). 커밋 실패 · 쓰기 실패 · 0행이면 `log_file_bytes` 로 되자름. 파일이 `log_file_bytes` 보다 짧으면 `log_complete = 0` 고정 + 서버 로그 「log gap」, 파일 끝에서 이어 씀. 기동 정리도 같은 비교 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-b. 「`log_gap` 으로 표시」는 409 코드 `log_gap`(워커 오프셋이 앞섬)과 이름이 겹치지 않게 `log_complete = 0` 고정 + 서버 로그 줄로 — §12 V2-10. 메인 세션이 받아들임. 2라운드 뒤 Codex 와 논의(4차) | — |
| 324 | finish 상한(D-c) | 잡이 끝나고 `FINISH_HOLD_SECONDS`(300초)가 지났고 맨 앞 로그 · 산출물 항목의 마지막 시도가 HTTP 5xx 면 finish 먼저(산출물 `failed` + `upload_failed`, `log_complete` 는 서버가 false). 연결 실패면 순서 유지. 300 의 근거는 §4.5(백오프 상한 30초 × 10회 넘게 · 기본 유예 600초의 절반 · DB 바쁨 5초) | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-c(수치는 재료). finish 에 `log_complete` 칸을 따로 싣지 않고 서버가 `log_bytes` 로 계산 — §12 V2-3. 메인 세션이 받아들임. 2라운드 뒤 Codex 와 논의(4차) | — |
| 325 | 세션 폴더의 주인(D-e) | `owner.json` = `{server_sha256, token_name}`(설정의 `server` 문자열 + `server_name` 의 sha256, `whoami` 의 이름. 토큰 값 없음). 주인이 다르거나 없는 폴더는 잠가 보기 · 고아 정리 · 큐 보내기 · 지우기를 안 한다(7일 지나고 잠글 수 있을 때만 청소). 폴더는 프로토콜 검사 · `whoami` 뒤에 만든다 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-e. 2라운드 뒤 Codex 와 논의(4차) | — |
| 326 | 죽은 세션 큐의 응답(D-f) | §4.4 의 표: 409 `session_fenced` · `not_registered` · 401/403 은 그 큐에만 적용, 이 프로세스를 멈추거나 D 의 id 로 재등록하지 않는다. 401/403 은 파일을 지우지 않는다. 등록은 D 의 큐가 끝난 뒤 | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-f. 2라운드 뒤 Codex 와 논의(4차) | — |
| 327 | 고아 식별 · 재확인 · 경고(D-g) | 신원 = 리더 pid 의 시작 시각(Linux `/proc/<pid>/stat` 22번째 칸, 그 밖 `ps -o lstart=`). 리더 없는 살아 있는 그룹 · 읽기 실패 · `proc.json` 없음은 「모름」 → `unverified_sessions` 로 등록, `D.ok_at + G_J − m_J` 에 재확인(확인되면 좁은 확인 finish), 그래도 모르면 서버가 유예 만료에 표식 잡을 요약 코드 `orphan_unverified` 로 닫는다(사람에게 보이는 경고 — events 에는 읽는 곳이 없어 요약 코드로, F60). DB `worker_sessions.orphan_unverified` **3판: 4차 위임 결정 330 이 바꿨다** — 닫기와 코드 `orphan_unverified` 는 그대로, 그룹은 G 에 풀지 않고 홀드. `ps` 신원은 `LC_ALL=C TZ=UTC` 로 읽고 형식이 다르면 「모름」(V1-7). | 확정(위임, 2026-11-26) · 메인 세션 결정(위임) · D-g. 「경고를 사건으로」를 요약 코드로 낸 것은 재료 — §12 V2-7. 메인 세션이 받아들임. 2라운드 뒤 Codex 와 논의(4차)해 그룹 처리는 330 으로 바뀜. `ps` 를 `LC_ALL=C TZ=UTC` 로 읽는 것은 5차 (f)에서 확정 | — |
| 328 | 서버 잠금 둘의 이름 · 범위 · 주인(V2-5) | `_heard_lock`(+ `_heard_at`): 요청의 세션 판정 + `heard` 갱신, janitor 의 유예 · 취소 미확인 판정 + 조건부 닫기만. `_job_log_locks`: 잡 하나의 로그 쓰기 절차만. 둘 다 `RemoteWorkersMixin` 이 가진다. 한쪽을 쥔 채 다른 쪽을 잡지 않는다. 4판: `_heard_lock` 은 세션별 잠금 사전에 접근하는 메모리 잠금으로 줄고, 판정 · 닫기는 `_session_locks[S]`(결정 339-g). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 1라운드 V2-5 를 고치며 재료가 정함 | — |
| 329 | 와이어 세부(V2-13) | `outbox.json` 의 `acked`, 빈 본문 질의는 그 프로세스가 그 잡의 로그를 처음 보낼 때 보내는 스레드가, `protocol_required` 문구 `"worker protocol 2 required: upgrade rcm on this worker"`(「not registered」 없음), 형식이 틀린 `X-RCM-Worker-Session` · `X-RCM-Log-Offset` 은 400(`error` 만), 로그 커밋 바쁨은 503 `database_busy` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 1라운드 V2-13 을 고치며 재료가 정함 | — |
| 330 | 되돌릴 수 없는 잡의 그룹 홀드(327 을 바꿈) | 표식 잡이 프로세스 죽음 확인 없이 닫히면(유예 만료 · `orphan_unverified` · `cancel_unconfirmed` · 레거시 만료 · 첫 등록의 레거시 닫기 · `group_gone` 이 거짓인 finish · 로컬 레인 재기동 `lost`) 닫는 트랜잭션에서 영속 그룹 홀드(표 `irreversible_holds`). 확인 = 그 잡 세션의 `group_gone: true` finish · 좁은 확인, 새 세션의 `previous_sessions`, T5 의 「띄운 적 없음」, 로컬 러너의 `group_gone` — 같은 이름의 토큰으로만. admin `POST /jobs/{id}/release-hold` · `rcm release-hold <job>`(감사 이력 · 사건 kind `hold`). 보이는 곳: 대기 잡 사유 `needs_action` + `action.code` `irreversible_hold`, `/api/status` · `/api/health` `irreversible_holds`, 웹 「Needs a look」, `rcm check` 경고 줄, `rcm doctor` 줄 · `--json` 키, `rcm wait`. 그룹이 없는 표식 프리셋은 합성 그룹 `irreversible:<이름>` · `global`(자동 — 근거 §4.12), 그룹 NULL 인 옛 대기 잡은 claim 때 유효 그룹을 적고 배제에 쓴다. 열린 홀드의 잡 행은 정리가 지우지 않는다. 로컬 레인 표식 잡에도 적용(F76). 4판: T5 의 「없음」은 확인이 아니다 — 표식 잡이면 홀드(339-b). 확인 표에 순서 · 신선도 칸(§4.12). 홀드 그룹은 잡 행에서 · 레거시 규칙(339-d). 홀드를 만든 잡의 잡 끝 알림에 `RCM_HOLD` · `RCM_HOLD_GROUP` · 한 줄, opt-in `on` 값 `irreversible_hold`(339-e, 6차). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(4차). 재료가 정한 합성 그룹 자동 · 로컬 레인 적용 · 확인 없는 닫힘에만 홀드 · `rcm check` 경고 줄은 5차 (a)~(d)에서 확정(§12.4) | 253 의 「유예가 지나면 워커가 멈춘다」에 「확인 없이 닫히면 사람이 풀 때까지 그 그룹은 안 돈다」 가 더해짐 |
| 331 | 되돌릴 수 없는 명령의 감독자 | 표식 잡의 명령을 같은 파이썬의 `remote_ci_monitor.job_supervisor` 아래 띄운다(setsid, 명령은 같은 그룹). 시작 때 `getppid()` ≠ 기대 pid 이거나 SIGTERM 을 이미 받았으면 명령을 안 띄움. `SUPERVISOR_POLL_SECONDS` 0.25 마다 `getppid()` 를 보고 바뀌면 그룹 SIGTERM → 첫 SIGTERM + `grace_J` 에 SIGKILL. 자식은 막는 `waitpid` 스레드로 거두고 같은 종료 상태로 끝남. 러너가 리더 끝 뒤 남은 멤버를 거두고 `group_gone`. 일반 잡에는 안 씀. 로컬 레인 표식 잡에도 씀. 4판: Popen 창의 SIGTERM 전달, 명령 끝 뒤 쓸기 · S7 시계는 첫 SIGTERM 부터, 시작 실패 종료 125 + 표지, `-I` + 파일 경로(339-c). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(4차). 폴링 0.25초 · 종료 대기 `grace_J` 는 재료가 이 Mac 에서 잼(F78). 폴링 값과 로컬 레인 적용은 5차 (b) · (f)에서 확정 | — |
| 332 | lanes = 0 의 DB 폴링 | 바꾸지 않는다. 「받아들임, 재지 않음 — 막힌 claim 하나에 초당 DB 폴링 둘」(§1.3) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(4차) | — |
| 333 | Popen 직전 확인 · 러너 단조 시계 · `proc.json` 두 번 쓰기(V2-1 HIGH · V2-3) | 러너가 requires 와 상관없이 phase 뒤 · Popen 바로 앞에서 `should_stop` · `should_cancel` 을 본다(사이에 네트워크 없음). 관찰자 `spawning()` 이 `{pgid: null}`, `spawned(pid)` 가 `{pgid, start}` 를 적는다. 펌프의 첫 점검은 Popen 직후 곧바로. 점검 박자 · SIGTERM→SIGKILL 은 `mono_fn`, 잡 시한은 벽 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(5차 (f)). 2라운드 V2-1 · V1-1 · V2-3 을 고치며 재료가 정함 | — |
| 334 | 멈춤의 길 이름(V1-2 · V2-2 · V2-6) | 자기 멈춤 = 관찰자 `self_stop` → `should_stop` → `lost` + `worker_self_stopped`. W4 의 멈춤 = `closed_stop` → `should_cancel`. `closed_by_server` 는 `should_cancel` 을 세우지 않는다. W4 는 모든 끝 상태의 표(§4.1.2) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(5차 (f)). 2라운드 지적을 고치며 재료가 정함 | — |
| 335 | 연결된 워커의 `ok_at` 나이 · heartbeat 전체 기한 · 기본 G(V1-3 · V2-11 · V1-9) | heartbeat 요청에 전체 기한 `HEARTBEAT_DEADLINE_SECONDS` 15(도우미 스레드 + 기다림, 매달린 도우미 많아야 `HEARTBEAT_INFLIGHT_MAX` 4). `L = max(T, 2H + 3·T_req + 5)`. `disconnect_grace_seconds` 를 안 적으면 `max(600, L + 35)`. e2e 는 T 10 · H 1 · G 90. 4판: 버려진 도우미의 늦은 heartbeat 는 `seq` 로 거른다(339-a). | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(5차 (f)). 2라운드 지적을 고치며 재료가 정함 | — |
| 336 | 세션 이름 대조 · 첫 등록의 레거시 잡 · 레거시 들음(V1-8 · V1-5 · V2-14) | 세션 행의 이름 ≠ 토큰 이름이면 403(`heard` 안 올림, `previous_sessions` 도 같음). 새 세션의 첫 등록이 그 이름의 세션 NULL busy 잡을 `worker_restarted_without_job` 으로 닫음(표식이면 홀드). 레거시 요청은 `heard_legacy(이름)`, 레거시 잡의 X 는 닫는 때의 프리셋 값 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(5차 (f)). 2라운드 지적을 고치며 재료가 정함 | 결정 255 의 「새 세션이 옛 세션을 펜싱」 을 세션 없는 옛 프로세스의 잡까지 넓힘 |
| 337 | 보내는 스레드 · 죽은 세션 큐 대기(V1-10 · V2-9) | 보내는 스레드 · 백오프는 잡마다 하나(죽은 세션 D 의 잡도). 등록은 D 의 큐를 서버가 응답하는 시간으로 `DEAD_SESSION_WAIT_SECONDS` 300 까지만 기다리고, 넘으면 워커 로그 한 줄과 함께 등록(그 잡은 `worker_restarted_without_job`) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(5차 (f)). 2라운드 지적을 고치며 재료가 정함 | — |
| 338 | 로그 경계 · 커밋 조건 · 끝난 잡 점검(V1-4 · V2-7 · V2-10 · V2-12) | PR1 로그 라우트는 세션 헤더만, 오프셋 규칙은 PR2. 커밋 UPDATE 에 `EXISTS (… worker_sessions … fenced_at IS NULL)`. 서버 기동 정리가 끝난 원격 잡의 `log.txt` 가 짧으면 `log_complete = 0`. step 9 문구 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(5차 (f)). 2라운드 지적을 고치며 재료가 정함 | — |
| 339-a | heartbeat 순서와 T5 의 「없음」 | heartbeat 에 세션별 `seq` 와 `claims_settled`, claim 에 `claim_seq`(잡 행 `jobs.claim_seq`). 서버는 `seq` 가 받아들인 것보다 크고 `claims_settled ≥ J.claim_seq` 일 때만 「없음」을 센다(`heard` 는 늘 올림). 워커는 `running` 넣기와 claim 번호 닫기를 한 잠금 구간에서, 목록 · `seq` · `claims_settled` 를 한 잠금 구간에서 찍는다. 늦은 응답의 `cancel` · `paused` 는 적용하지 않는다. `_fresh_claims` 는 레거시만 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 메인 세션 결정 339-a. 「claim 커밋 뒤에 찍힌 목록」의 방법(`claims_settled`)은 재료가 고름(339-a 가 맡김) | 메인 세션 결정의 예시 `claimed_seen` · 「마지막 claim id」 대신 `claims_settled` — claim 응답을 잃은 경우(T5 자체)에 워커가 그 잡 id 를 모르기 때문 |
| 339-b | T5 와 표식 잡 | T5 닫기는 확인이 아니다 — 표식 잡이면 같은 트랜잭션에서 홀드. 풀림은 §4.12 확인 1~4 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 메인 세션 결정 339-b | — |
| 339-c | 감독자 네 가지 | (1) Popen 뒤 적힌 SIGTERM 을 그룹에 한 번 (2) 명령 끝 뒤, SIGTERM 을 받았거나 부모가 바뀌었으면 남은 멤버(`ps -A -o pid= -o pgid=`)를 첫 SIGTERM + `grace_J` 에 그룹 SIGKILL · 러너 S7 도 첫 SIGTERM 시계 (3) 미시작은 종료 125 + 첫 줄 `[rcm] supervisor: not started: <code>`, 러너는 둘 다 맞을 때만 미시작으로 읽고 `launch_*` 는 감독자 없을 때와 같은 코드 (4) `-I` 유지 + 모듈 파일 경로 실행(감독자는 표준 라이브러리만) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 메인 세션 결정 339-c. 125 · 표지 줄 · 파일 경로 · `ps` 는 재료가 정함(F83 · F84 실측) | — |
| 339-d | 홀드의 그룹 | 홀드 행은 잡 행의 그룹 · 범위 · 풀 · `host_key` 를 복사(지금 설정을 읽지 않음). 레거시 잡: X = 프리셋이 표식이거나 설정에 없음, 그룹 NULL 이면 `irreversible:<프리셋>` · `global` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 메인 세션 결정 339-d. 레거시의 「설정에 없음 → X」는 재료가 정한 닫힌 쪽 규칙 | — |
| 339-e | 홀드 알림 | `[[notify]]` 에 새 기본 사건을 더하지 않는다. 홀드를 만든 잡의 잡 끝 알림(이미 가는 것)에 env `RCM_HOLD=1` · `RCM_HOLD_GROUP=<그룹>` 과 본문 한 줄 `group held — run rcm release-hold <job>`. `irreversible_hold` 는 규칙이 `on` 에 적을 때만 부르는 opt-in 값(사건 kind `irreversible_hold`, 중복 막기 `hold:<규칙>`, env `RCM_HOLD_GROUP` · `RCM_HOLD_CAUSE` · `RCM_HOLD_WORKER`, 본문 `event` · `hold`). `RCM_EVENT` 는 모든 알림에(`job_finished` · `irreversible_hold`), URL 본문 `event`. 홀드 · 알림 · 기계 판독 표면은 PR1, 사람용 화면 PR3, 릴리스는 셋이 다 머지된 뒤(§6 #13) | 확정(위임, 2026-11-26) · Codex 의견으로 바꿈(6차). 4판 재료는 `irreversible_hold` 를 `on` 을 안 적은 규칙의 기본에 넣었으나 Codex 가 「기존 훅이 두 번 불려 호출 · 부작용이 겹친다」고 해 메인 세션이 opt-in 과 잡 끝 알림의 env · 한 줄로 바꿈 | 메인 세션 결정 원문의 「홀드가 생기면 `[[notify]]` 를 한 번 부른다(잡 끝 알림과 별개의 사건 종류)」를 6차에서 바꿈 — 기본 규칙은 별개 사건 대신 잡 끝 알림으로 홀드를 안다. 잡 끝 알림의 env 에 `RCM_EVENT` 가 늘고, 홀드를 만든 잡이면 `RCM_HOLD` · `RCM_HOLD_GROUP` 도 는다 |
| 339-f | 로그 읽는 쪽 상한 | `log_file_bytes` 가 있으면 `GET /jobs/{id}/log` · 로그 꼬리는 그 길이까지. 마커 SSE 는 이미 커밋 뒤 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 메인 세션 결정 339-f | — |
| 339-g | 유예 판정 잠금 | 세션별 잠금 `_session_locks[S]`: heard · seq 갱신, 그 세션 잡 하나의 다시 보기 + 닫기 커밋. 전역 `_heard_lock` 은 사전 접근만. 틱 상한 `GRACE_CLOSES_PER_TICK` 20, 바쁨이면 다음 틱. 증명 2 에 잠금 대기를 넣음 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 메인 세션 결정 339-g(「잡 단위」 대신 세션 단위는 재료 — heard 가 세션 값) | — |
| 350 | PR 나눔(V1-4) | 홀드의 기계 판독 표면(store 질의 · server 연결 · 상태/헬스 키 · 대기 사유 · 잡 문서)을 PR1 로, PR3 는 사람용 그리기만 — PR2 · PR3 owns 겹침 0(F87) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(6차). 3라운드 V1-4 를 고치며 재료가 정함 | 3판 §5 의 PR 배치를 바꿈 |

### 11.1 부모 명세가 넘긴 결정의 대응표 (D-i, V1-9 · V2-8)

`docs/m5t-workplan.md:1045` 의 결정 행 「244 · 245 · 248 a~d · g · 249 · 250 — 옮긴 결정」과 `:1072` 의 M5t-b 핸드오프 표가
넘긴 부모 명세 3판의 결정을 이 명세의 결정에 맞춘다. 3판 원문은 레포 밖의 3판 전문이고 메인 세션이 보관한다.

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
| 1 | A-H7 | `lost` 뒤 그룹 자리가 풀림 | 표식 잡은 서버보다 먼저 워커가 멈춤(결정 320, §4.1.5 정리 1), 펜싱 잡은 확인 · 유예까지 busy(결정 261), 3판: 확인 없이 닫히면 그룹 홀드(결정 330), RL1 · RL3 · RL42 |
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
3판에서 새로 둔 행은 RL41~RL49 이고 3판에서 고친 행은 RL1 · RL2 · RL5 · RL8 · RL10 · RL11 · RL15 · RL16 · RL24 · RL30 · RL31 ·
RL34 · RL35 · RL36 · RL37 이다.

### 12.3 1라운드 격리 반박 (1판 head `804c17d` 에서 2판으로)

V1 은 반박자 1, V2 는 반박자 2 다. 「1판」 열의 WP 줄 번호는 `docs/m5tb-workplan.md` 1판의 것이다.

| id | 등급 | 1판 | 2판 | 결정 |
|---|---|---|---|---|
| V1-1 · V2-1 | HIGH | 자기 멈춤 판정이 heartbeat 루프(`remote_worker.py:766-771`) 안이고 m = grace + H + 5 가 「H 마다 판정」을 가정. 끊긴 동안 한 바퀴 H + 15초(요청 시한 `client.py:83`), 서버가 돌아온 회차는 못 한 보고 재시도 뒤(F53) → 마지막 정상 heartbeat 뒤 약 G + 10초에 죽어 서버의 `heard + G` 닫기보다 늦음(RL1 · RL3 · RL4 깨짐). heartbeat 응답이 닫힌 잡을 알리지 않음(WP:231-234, 245, 253-255) | 감시 스레드(1초 틱), `ok_at` = 2xx 받은 요청의 보낸 시각, m = 15 + grace + 10(F56 실측), 하한 L · 설정 검증 `G ≥ L + 35`, 표식 잡 grace 상한, heartbeat `closed_jobs`. §4.1.6 R1 · R7, §4.1 계산 1~5, §4.3, RL1 · RL26 · RL34 · RL35, 변이 `self-stop-in-heartbeat-loop` · `self-stop-ignores-request-timeout` · `closed-jobs-omitted` | 고침 — 결정 320 · 321(D-a) |
| V2-2 | HIGH | 로그를 덧붙인 뒤 따로 커밋, 잡별 직렬화 없음(`remote_workers.py:639-668`, `_remote_lock` 은 `_log_partial` 만). 커밋 실패(503 `database_busy`) 뒤 재시도가 같은 바이트를 또 붙이고 `log_complete` 는 참. 같은 잡의 동시 요청 둘이 같은 `log_offset` 을 읽고 둘 다 붙임(WP:415-419) | 잡별 로그 잠금 안에서 행 다시 읽기 → `log_file_bytes` 자리에 쓰기 · 뒤 자르기 → 조건부 커밋, 실패 · 0행이면 되자르기. §4.1.6 R2, §4.5 절차 1~9, T15, RL36, 변이 `log-write-unlocked` · `log-append-not-positioned` | 고침 — 결정 323(D-b) |
| (뿌리) | HIGH 둘의 공통 | 상태를 바꾸는 두 단계를 한 단위로 묶지 않음 | 같은 뿌리를 표 전체에서 찾아 R1~R12 로 고침(§4.1.6): 유예 판정 ↔ heard(R3), late_result 확인 ↔ 기록(R4), ready ↔ 파일(R5), acked ↔ 2xx(R6), 서버 닫음 ↔ 워커 앎(R7), D 큐 ↔ 등록(R8), 폴더 잠금 ↔ 주인(R9), 띄우기 ↔ 신원 기록(R10), finish ↔ 로그(R11), 고아 확인 ↔ 유예 만료(R12) | 고침 — 메인 세션 지시 |
| V1-3 | MED | 런타임 쓰기 실패(503 `log_write_failed`, ENOSPC) 뒤 재전송이 남은 바이트 뒤에 붙음 — 자르기는 기동 때만(WP:421-423) | 매 요청이 `log_file_bytes` 자리에 쓰고 뒤를 자름, 쓰기 실패면 되자르기. RL36(c) | 고침 — 결정 323(D-b) |
| V2-3 | MED | finish 가 로그 · 산출물 뒤에 줄 서서 그 경로의 5xx 가 이어지면 끝난 잡이 24시간 running · 그룹 슬롯 · `rcm wait` 멈춤(WP:408-410, 443-445, 497) | 잡이 끝나고 300초 넘게 그 단계가 HTTP 5xx 면 finish 먼저(산출물 `failed` + `upload_failed`, `log_complete` false). 연결 실패면 순서 유지. 300초 근거 §4.5. RL40 · RL19(b), 변이 `finish-waits-forever`. D-c 의 「`log_complete=false` 를 그 finish 에 싣는다」는 서버가 `log_bytes` 로 계산하므로 칸을 따로 두지 않음 | 고침 — 결정 324(D-c) |
| V2-4 | MED | 죽은 세션 D 의 큐를 D 의 id 로 보낼 때 409 `session_fenced` 가 W5(종료 0)를 불러 새 프로세스가 0 으로 끝남, `not_registered` 면 D 의 id 로 재등록(WP:378, 435-436, 233) | D 의 큐 응답 표(§4.4): 그 큐에만 적용, W5 · 재등록 없음, 401/403 은 파일 보존, 등록은 D 큐 뒤. W10 · RL39, 변이 `dead-session-fenced-exits` | 고침 — 결정 326(D-f) |
| V2-5 | MED | 「같은 락(`_session_lock`)」 — grep 0, 범위 · 주인 없음(WP:316) | 새 이름 `_heard_lock`(+ `_heard_at`), 범위 = 세션 판정 + heard 갱신 / janitor 판정 + 조건부 닫기, 로그 요청 전체는 아님(그건 `_job_log_locks`), 주인 `RemoteWorkersMixin`, 잠금 순서. §4.2 · F57 | 고침 — 결정 328 |
| V2-6 | MED | 문턱 `G − m` 이 0 이하 · 아주 작을 수 있음. m 은 워커 grace 를 쓰는데 G 는 `worker_timeout_seconds` 하고만 견줌(WP:586). 「0 이하면 첫 실패 회차」(WP:245)는 실패 조건 없는 규칙과 모순. `ok_at` 처음 값 없음 | 하한 L = max(T, 2H + 15), 설정 검증 G ≥ L + 35, 표식 잡 grace 상한으로 늘 `G − m ≥ L`, 「0 이하」 문장 지움, 연결된 동안 멈추지 않음(§4.1 계산 5). `ok_at` 처음 값 = claim 요청을 보낸 시각 — D-a 는 「claim 응답 시각」이라 했으나 long-poll claim 은 보낸 뒤 80초까지 응답이 올 수 있어(F55) 응답 시각을 쓰면 m 에 그만큼을 더해야 한다. 보낸 시각은 응답 시각보다 이르거나 같아 더 보수적. RL30 · RL34(c), 변이 `grace-lower-bound-dropped` | 고침 — 결정 320(D-a), 처음 값은 D-a 에서 더 보수적으로(메인 세션 확인 바람 — notes) |
| V2-7 | MED | 확인할 수 없는 고아(`ps` 실패, 리더가 죽고 멤버만 산 그룹)는 G 까지만 막히고 서버가 슬롯을 풀면 표식 잡이 두 번 돌 수 있음(WP:377, 383-385, 703, 800) | Linux `/proc` 시작 시각, 리더 없는 그룹 · `proc.json` 없음도 「모름」, `unverified_sessions` · 재확인 `D.ok_at + G − m` · 그래도 모르면 요약 코드 `orphan_unverified`(사람에게 보임). 남는 위험은 §9. W12 · RL15 · RL29 · RL31 | 고침 — 결정 327(D-g). 「사건으로」를 events 행이 아니라 요약 코드로 낸 것은 events 를 읽는 곳이 없어서(F60) |
| V2-8 · V1-9 | MED | 부모 명세가 넘긴 248 a~d · g, 250, 249 의 v26 이 추적 안 됨(`docs/m5t-workplan.md:1045, 1072`, `PLAN.md:753`). PR0 의 PLAN 편집이 스냅숏에 없음(`PLAN.md:1, 628, 753`) | §11.1 대응표(대응 없는 항목 0), §5 PR0 행에 PLAN.md 일곱 자리 열거 | 고침 — D-i |
| V1-2 | MED | RL12(b) · RL14(a) · §4.4 의 T9 재전송은 디스크 큐(PR2)가 필요한데 PR1, RL4 는 PR1 인데 `late_result`(PR2) 단언, 변이 `late-result-changes-state` 는 RL4 를 들지만 PR2(WP:629, 628, 684, 686, 676, 365-366, 766) | RL4 · RL8 · RL12 · RL14 의 PR 칸을 나눔, §4.4 의 7 을 PR2 로, PR1 만 머지된 동안의 동작을 적음. 변이는 PR2 그대로(RL4 의 PR2 부분) | 고침 |
| V1-4 | MED | 유예 동안 cancelling 인 일반 잡이 G 에 lost 가 되면 heartbeat `cancel` 목록(`remote_workers.py:961`, 활성 잡만)에 없어 다시 붙은 워커가 계속 돌림 | `closed_jobs` 의 `had_cancel` 로 멈춤. RL8(b) · RL35, 변이 `lost-cancel-keeps-running` | 고침 — 결정 322(D-d) |
| V1-5 | MED | 세션 폴더에 서버 · 토큰 신원이 없음. 기본 워커 `data_dir`(`config.py:520`)을 같은 사용자의 워커가 같이 씀. 시험 서버를 가리킨 둘째 워커가 운영 워커의 죽은 세션을 입양, 401/403 이면 진짜 결과를 지움 | `owner.json`(서버 sha256 · 토큰 이름), 주인이 다르거나 없으면 손대지 않음, 401/403 은 파일 보존. W9 · RL38, 변이 `adopt-foreign-session` | 고침 — 결정 325(D-e) |
| V1-6 | MED | 머지 순서를 M5t-a PR2(#268)하고만 적음. M5t-a PR3 · PR4 · PR6 도 겹침 | 구현 PR1 의 시작 조건 = M5t-a PR1~PR7 전부 dev 머지(F54, `docs/m5t-workplan.md:690-697`). #268 은 이미 dev(96a8bd2) | 고침 |
| V1-7 | MED | 계약 잠금 시험 목록이 모자람(`ACTIONABLE_REASONS` 튜플 `tests/test_queue_m5n.py:135` · `tests/test_render_m5n.py:199` · `tests/test_web_schedule_m5s.py:43`, `RECENT_KEYS` `tests/test_status_schema.py:91`) | F58 에 전부(그 밖에 node 두 곳 · `NEW_RECENT` · register · heartbeat 응답 잠금도 찾음), §7.3 · §5 shared | 고침 |
| V1-8 | MED | §1.1 에 11-25 「OS 마다 무엇을 설치…」 · 「4」 · 11-26 「나의 지시없이 … 버전업」 이 없음. §2 「운영 반영 승인」 | §1.1 세 행(M5t-a 246 · 242 / §6 #14 · #15), §2 오너 행 고침 | 고침 — D-h |
| V2-9 | LOW | W6 「running 인데 서버가 내려갔다 → 모든 잡 멈춤 → 종료 3」 은 닿지 않고, 닿는다면 일반 잡을 죽여 253 에 어긋남(WP:234, 520-522) | 그 갈래를 지움(옛 빌드는 v26 DB 를 열지 않음, F50). 도는 중 받으면 그 요청만 실패로 | 고침 |
| V2-10 | LOW | 파일이 `log_file_bytes` 보다 짧은 경우(flush ≠ fsync) 처리 없음 | `log_complete = 0` 고정 + 서버 로그 「log gap」, 파일 끝에서 이어 씀. T16 · RL37. D-b 의 「`log_gap` 으로 표시」는 409 코드 `log_gap` 과 같은 이름을 두 뜻으로 쓰지 않으려고(M5s §3 #7 의 교훈) 칸 · 로그 줄로 냄 | 고침 — 결정 323(D-b) |
| V2-11 | LOW | 「`late_result` 기록이 아직 없으면」이 동시성에서 유일하지 않음, 트랜잭션 내용 열거 없음 | 한 `BEGIN IMMEDIATE` 의 네 단계 열거(§4.6), RL21(c) | 고침 — 274 문장 |
| V2-12 | LOW | 세션 폴더를 만든 뒤 프로토콜 검사 — 종료 3 재시작마다 빈 폴더가 쌓임 | 폴더는 프로토콜 검사 · `whoami` 뒤(§4.4 의 2~4), RL25 | 고침 |
| V2-13 | LOW | `outbox.json` 이 `acked` 를 저장 안 함, 빈 본문 질의의 주체 · 때 없음, `protocol_required` 문구 없음(옛 워커는 「not registered」 부분 문자열을 맞춤 `remote_worker.py:799`), 형식 틀린 헤더의 답 없음 | `acked` 저장, 빈 본문 질의 = 그 프로세스의 첫 로그 전송, 문구 확정(「not registered」 없음), 400. RL11 · RL16(d) · RL24 | 고침 — 결정 329 |
| V2-14 | LOW | M5t-c 순서 주장(`pair_proof` 를 등록 부작용 앞에)이 스냅숏에서 확인 안 됨 | F52 로 인용(PR #269 `docs/m5tc-workplan.md:216` · 결정 301~303), §4.2 register 순서 4 | 고침 |
| V1-10 | LOW | 변이 없는 규칙: 표식 잡만 자기 멈춤, claim 때 G · X 얼림, 현재 세션만 취소 미확인(RL33), 세션 검사 전 버전 검사(RL13), 펜싱된 세션은 `late_result` 없음(Q-B) — `PLAN.md:528` | 변이 `self-stop-all-jobs` · `grace-read-at-judgement` · `cancel-unconfirmed-any-session` · `register-session-before-version` · `fenced-late-result-recorded`, 2판 규칙의 변이도 더해 26개(§7.4) | 고침 |
| V1-11 | LOW | 표의 열린 칸: `ok_at` 처음 값, 워커가 모르는 cancelling 잡이 T5 에 running 만, 「들림」 이 벽인지 단조인지 | §4.1 용어(`ok_at` 처음 값, 「들림」 = 단조 gap), T5 의 잡 칸 running · cancelling(`store.py:4702-4708`) | 고침 |
| V1-12 | LOW | RL22 는 번들이 서버에 있어야 `ready` 라는데 §4.6 은 행 + 해시만(F17) | `ready` 는 행 · 해시 · `bundle.tar` 파일(F63), RL22(e) | 고침 — 275 문장 |
| V1 제안 | — | `irreversible` 없는 원격 가능 배포 · 스토어 프리셋을 `rcm check` · doctor 가 경고 | 넣지 않음. 이름으로 추측한 경고는 거짓 경보를 낸다. §1.3 · §9 행과 문서 경고만 | 안 받음 — 메인 세션이 정한 기본값 |
| V2 제안 | — | heartbeat 응답에 닫힌 잡을 싣기 | `closed_jobs` | 받음 — 결정 321 |

### 12.4 2라운드 격리 반박 (2판 head `1b45b7f` 에서 3판으로)

V1 은 반박자 1, V2 는 반박자 2 다. 「2판」 열의 WP2 줄 번호는 `docs/m5tb-workplan.md` @ `1b45b7f` 의 것이다. 1라운드 결정
320~327 은 2라운드 뒤 Codex 와 논의했다(4차 위임 결정). 320~326 은 확정됐고 327 은 330 으로 바뀌었다. 재료가 4차 결정을
펼친 곳은 5차 위임 결정에서 Codex 와 논의해 확정했다.

| id | 등급 | 2판 | 3판 | 결정 |
|---|---|---|---|---|
| (뿌리) | HIGH 1 · MED 3 의 공통 | 「멈춤」의 증명이 이미 도는 프로세스 하나와 HTTP 왕복 하나만 가정 | 증명을 잡의 모든 단계 × 워커의 모든 죽음으로 다시 씀(§4.1.4 표 · §4.1.5 정리 1~3), §4.1.6 에 R13~R19 | 고침 — 메인 세션 지시 |
| V2-1 / V1-1 | HIGH / MED | 감시가 Popen 전에(자재화 · git_ref fetch · 긴 준비, requires 없는 잡) 발동하면 러너가 `if spec.requires:` 안에서만 멈춤을 봐(`runner.py:349-390`, `:266-275`) 멈춤 결정 뒤에 명령이 시작 → 서버가 `heard + G` 에 그룹을 풀면 두 번 돈다 | requires 와 상관없이 phase 뒤 · Popen 바로 앞 확인, 펌프 첫 점검 즉시, `proc.json` 두 번 쓰기. W3 · §4.1.4 의 S2~S4, RL41, 변이 `popen-check-only-with-requires` | 고침 — 결정 333 |
| V1-2 / V2-2 | MED | 자기 멈춤을 `should_cancel` 로 걸어 러너가 `cancelled`(종료 2)로 끝남(`runner.py:455-461`, `remote_worker.py:1023-1026, 960, 996`). `lost` 길은 `should_stop`(STOP_SUMMARY, 코드 없음). `should_cancel = cancel_requested or closed_by_server`(`remote_worker.py:199-200`)라 W4(일반 `lost` 잡은 계속)와 어긋남 | 자기 멈춤 = `self_stop` → `should_stop` → `lost` + `worker_self_stopped`, W4 멈춤 = `closed_stop` → `should_cancel`, `closed_by_server` 분리(F66). RL1 · RL5 · RL8 · RL41, 변이 `self-stop-reports-cancelled` · `closed-by-server-cancels` | 고침 — 결정 334 |
| V1-3 / V2-11 | MED | 연결된 워커의 `ok_at` 나이 상한이 틀림: 다음 2xx 전 `d_prev + H + d_cur`, 2xx 하나를 잃으면 `+ H + 15` — 약 `2H + 3·T_req`. 15초는 소켓 연산 시한이지 전체 기한이 아님(`client.py:83, 89-93, 530, 566`). §7.5 e2e(T 10 · H 1 · G 52 · 문턱 17)에서 RL34(c) 가 연결된 잡을 멈춤 | heartbeat 전체 기한 15초(도우미 스레드), `L = max(T, 2H + 3·T_req + 5)`(기본 설정의 하한 95 는 그대로), e2e G 90 · 문턱 55, RL34 (c) 고침 · (e) 더함, §4.1.5 정리 2 · 3, 변이 `heartbeat-no-deadline` · `lower-bound-one-timeout` | 고침 — 결정 335 |
| V2-3 | MED | SIGTERM→SIGKILL 타이머가 러너의 벽시계(`now_fn`, ≥ 1초 점검, `runner.py:435, 452-470`)라 시계가 뒤로 가면 `grace_J + 4` 상한이 깨짐 | 점검 박자 · grace 타이머를 `mono_fn`(잡 시한은 벽 그대로), RL34(f), 변이 `runner-grace-wall-clock` | 고침 — 결정 333 |
| V2-4 | MED | 확인 못 한 고아는 G 에 그룹이 풀림(327) | 그룹 홀드(T8 · T18 · §4.12), RL31(나) · RL42, 변이 `hold-not-created` | 고침 — 4차 위임 결정 330(Codex: 「G 에 푸는 것은 253 에 대한 fail-open」) |
| V2-5 | MED | kill -9 · 재시작 없음이면 고아를 아무도 안 끊고 G 뒤 그룹이 풀림 — §0 의 주장과 §9 가 어긋남 | 감독자(§4.13) + 확인 없는 닫힘의 홀드. §0 · §9 를 같은 주장으로. RL43, 변이 `supervisor-no-parent-check` · `supervisor-ignores-parent-death` | 고침 — 4차 위임 결정 331 · 330 |
| V1-4 | MED | PR1 은 「오늘처럼 덧붙이되 세션 헤더만」, §4.5 step 1 은 「세션 헤더가 있는데 오프셋이 없으면 400」, RL11(PR1)은 나쁜 헤더 400 → PR1 워커가 모든 로그 POST 에 400(WP2:853 대 :599-602, :823, :912) | PR1 은 세션 헤더 검사만 · 오프셋 헤더를 읽지 않음, 오프셋 규칙은 PR2(§4.5 「PR 경계」 · §5). RL11 은 세션 헤더만, 오프셋 헤더 400 은 RL16(PR2) | 고침 — 결정 338 |
| V1-5 / V2-14 | MED | 첫 등록(새 세션)인데 그 이름에 세션 NULL busy 잡이 있을 때의 T 행이 없음. 그 잡이 프로세스 없이 `running`, 새 워커의 claim 이 `LaneBusy` 409 를 `heard(이름) + G` 까지 받음. 오늘 register 는 바로 닫음(`remote_workers.py:411-418`). `_heard_at` 은 세션 id 키라 레거시 요청의 들음이 정의되지 않음 | T17, 첫 등록이 세션 NULL busy 잡을 오늘처럼 닫고 표식(닫는 때의 프리셋 값)이면 홀드, `heard_legacy(이름)`. RL24 · RL47, 변이 `first-register-keeps-legacy` | 고침 — 결정 336 |
| V1-6 | MED | PR0 의 PLAN.md 편집에 `PLAN.md:129` 와 `:505`(이유 목록, 행동 가능 순서) 가 빠짐 — §4.8 이 `worker_unreachable_grace` 를 `worker_down` 뒤에 넣음(`core/model.py:165`, `ACTIONABLE`) | PR0 의 PLAN 편집을 열 자리로(⑧ `:129` ⑨ `:505` ⑩ `:127` 홀드), F70 | 고침 |
| V1-7 | MED | 고아 식별의 `start` 가 `ps -o lstart=` 라 TZ · 로캘에 따라 다름(서비스와 손으로 띄운 셸이 다를 수 있다 — RL10 이 그 경우). 형식이 다르면 「pid 재사용 → 확인됨」이 되어 모름이 확인으로 바뀜(WP2:526, :203, :230) | `LC_ALL=C TZ=UTC` 고정, 정규식에 안 맞으면 「모름」, 재사용 판정은 두 값이 모두 형식에 맞을 때만. F71 실측(세 환경 세 문자열), RL15, 변이 `ps-lstart-locale` | 고침 — 결정 327 의 3판 문장 |
| V2-6 | LOW | W4 의 `closed_jobs` 반응이 `cancelled` · `timed_out` · `lost` 만 — `failed` · `succeeded` 등은 행이 없음 | W4 표(모든 끝 상태 — 워커 자신의 finish 가 기록된 상태 · `cancelled` · `lost` · 원격 잡이 될 수 없는 상태), RL35 | 고침 — 결정 334 |
| V2-7 | LOW | 로그 커밋 UPDATE 가 잡의 세션 칸만 봐 S 가 펜싱된 뒤에도 같음 — step 2 를 지난 요청이 펜스 커밋 뒤에 커밋할 수 있음 | 같은 UPDATE 에 `EXISTS (worker_sessions … fenced_at IS NULL)`(R17), RL36(e), 변이 `log-commit-ignores-fence` | 고침 — 결정 338 |
| V2-8 | LOW | D 의 큐를 D 의 id 로 재전송할 때 새 세션의 `ok_at` 을 올리는지 정의 없음(`ok_at ≤ heard` 불변식) | 올리지 않는다고 §4.1 용어 · §4.3 · §4.4 의 7 에 명시 | 고침 |
| V2-9 | LOW | 등록이 D 의 큐 전체(최대 24시간)를 기다려 5xx 가 이어지는 항목 하나가 이 머신의 모든 claim 을 24시간 막음 | 서버가 응답하는 시간으로 300초 상한 + 워커 로그 한 줄, 그 잡은 `worker_restarted_without_job`. RL46, 변이 `dead-session-wait-forever` | 고침 — 결정 337 |
| V2-10 | LOW | 짧은 파일 탐지(T16)가 busy 잡에서만 — 끝난 잡의 꼬리가 OS 충돌로 잘리면 `log_complete = 1` 로 남음(RL37 이 되돌릴 수 없다고 한 그것) | 서버 기동 정리가 끝난 원격 잡도 본다, 비용 실측(F79: `os.stat` 1만 개 0.02초). RL37(c), 변이 `finished-log-not-checked` | 고침 — 결정 338 |
| V2-12 | LOW | 로그 step 9 「잠금을 놓고 커밋한 뒤」 와 step 8 의 잠금 안 커밋이 어긋나 읽힘 | step 9 문구: 「8 의 커밋은 잠금 안에서 끝난다. 그 뒤 잠금을 놓고 마커 발행」 | 고침 |
| V2-13 | LOW | `_heard_lock` 임계 구역에 `_publish_job` · notify 훅 · `_drop_log_partial` 이 들어가지 않는다는 명시가 없음(모든 heartbeat 가 이 잠금을 잡는다) | §4.2 에 명시(메모리 갱신 · 조건부 UPDATE 커밋만, 알림 · 로그 · 지우기는 잠금 뒤) | 고침 |
| V1-8 | LOW | 세션 판정이 id 만 — `worker_sessions.worker_name == token.name` 확인 없음(다른 이름의 현재 세션 id 로 `heard` 를 올려 표식 잡이 닫히지 않게 할 수 있다) | 이름 대조 403(T19), `previous_sessions` · `unverified_sessions` 도. RL11 · RL44(d), 변이 `session-name-unchecked` · `hold-release-any-name` | 고침 — 결정 336 |
| V1-9 | LOW | `disconnect_grace_seconds` 기본 600 이 하한 `L + 35` 보다 작아질 수 있음(`worker_timeout_seconds` 상한 없음 — 큰 값의 기존 설정이 업그레이드 뒤 기동 실패) | 키를 안 적으면 `max(600, L + 35)`, 적은 값이 하한 아래면 문장이 「키를 지우면 그 값을 쓴다」를 말함. RL30 | 고침 — 결정 335 |
| V1-10 | LOW | 보내기 스레드 하나 대 「잡끼리 서로 기다리지 않는다」 — 막는 `urlopen` 이라 300초 산출물 PUT 이 다른 잡의 로그 · finish 를 막음 | 보내는 스레드 · 백오프는 잡마다 하나 | 고침 — 결정 337 |
| V1-11 | LOW | lanes = 0 의 0.5초 DB 폴링 | 바꾸지 않음 — §1.3 「받아들임, 재지 않음 — 막힌 claim 하나에 초당 DB 폴링 둘」 | 안 고침 — 4차 위임 결정 332 |
| V1-12 | LOW | 결정 번호 범위 표기 「280~303」(WP2:34) 대 「280~312」(WP2:1128) | 머리 인용 · §9 · §6 #12 를 「M5t-b 256~279 · 320~339, M5t-c 280~319」 로 하나로 | 고침 |
| V1-13 | LOW | §7.3 이 `tests/test_outcome.py:543-569` 만 — `:60-85`(PLAUSIBLE 가 CODES 전부), `:88-214` · `:225+`(PINNED)도 새 코드 넷으로 빨개짐 | §7.3 · §5 PR1 shared 에 `PLAUSIBLE :48-85` · `PINNED :88-214` · 전수 잠금 `:220-231` · 필수 인자 `:511`(F72 — 지적의 `:60-85` 는 `PLAUSIBLE` 의 일부 줄이고 표 전체는 `:48-85`) | 고침 |
| V1-15 | LOW | 변이 없는 규칙: `finished_at` 자르기(277), `late_result` 한 행(R4), `orphan_unverified` 닫기, 이름 교차 세션 확인(V1-8), 330 · 331 의 새 규칙 | 변이 `finished-at-unclamped` · `late-result-twice` · `orphan-unverified-code-dropped` · `session-name-unchecked` 와 330 · 331 · 333~338 의 변이 — 3판 24개, 모두 50개(§7.4) | 고침 |
| 메인 세션 지시(M5t-c 317) | — | §4.7 · F52 · §4.11 · §9 · 결정 258 이 M5t-c 303 대로 「확실한 신원 불일치(MAC 틀림)는 종료 0」 이라 적음 | M5t-c 결정 317 대로: 틀린 MAC 의 hello · 421 · 인증 안 된 응답은 종료 0 이 아니라 토큰 없이 계속 재시도(10분마다 경고, FAIL/모름 상태). 종료 0 은 펜싱과 M5t-c 의 인증된 두 경우(확인된 서버의 401 + 새 hello 통과 · 등록 증명 `bad_mac` 세 번 이어짐)뿐. §1.2 · F52 · §4.7 · §4.11 · §9 · 258 고침 | 고침 — M5t-c 결정 317(메인 세션이 Codex 와 정함, PR #269) |
| (재료가 4차 결정을 펼친 자리) | — | — | 330 의 「설정 검증 또는 자동」 → **자동**(합성 그룹 `irreversible:<이름>` · `global`, 근거 §4.12), 330 의 「어디서나 needs_action」 의 `rcm check` 는 경고 줄(FAIL 아님 — 근거 §4.8), 330 · 331 을 **로컬 레인 표식 잡에도**(F76), 330 의 문장은 「확인 없이 닫히면」으로 읽음(자기 멈춤 확인과 함께 닫힌 `lost` 는 홀드 없음 — §12.4 아래 줄), 331 의 「세션을 빠져나가는 경우는 330 이 막는다」 는 확인이 오지 않을 때만 맞음(§9) | 확정(위임, 2026-11-26) — 5차 위임 결정 (a)~(e), Codex 와 논의 |

330 의 문장은 이렇게 읽었다. 오너 문장은 「irreversible 잡이 lost/orphan_unverified 이거나 프로세스 죽음 · 자기 멈춤 확인
없이 닫히면」이다. 이것을 「(lost/orphan_unverified 를 포함해) 프로세스 죽음이나 자기 멈춤의 확인 없이 닫히면」으로 읽었다.
「lost 이면 늘 홀드」로 읽으면 확인과 함께 닫힌 자기 멈춤(`worker_self_stopped`)과 펜싱 확인(`worker_replaced`)까지 매번
admin 을 부르게 된다. 그렇게 읽으면 같은 문장의 「자기 멈춤 확인」이 풀림의 근거라는 뜻과도 어긋난다. 5차 위임 결정 (c)가
이 읽기를 확정했다.

### 12.5 3라운드 격리 반박 (3판 head `572bd1d` 에서 4판으로)

V1 은 반박자 1, V2 는 반박자 2 다. 「3판」 열의 줄은 WP3 이다. 3라운드 뒤 메인 세션이 오너 위임으로 결정 339-a~g 를 정했고 재료가
그 세부와 350 을 정했다. 6차 위임 결정에서 Codex 와 논의해 모두 확정했고 339-e 는 Codex 의견으로 바꿨다.

| id | 등급 | 3판 | 4판 | 결정 |
|---|---|---|---|---|
| (뿌리) | HIGH 의 공통 | 확인 목록(§4.12)의 T5 가 순서가 보장되지 않는 메시지를 인증된 확인으로 썼다 | 확인 표에 「무엇이 순서 · 신선도를 보장하나」 칸, 보장이 없으면 확인이 아니다(§4.12 · §4.1.5 정리 4 · §4.1.6 R20~R23) | 고침 — 메인 세션 지시 |
| V1-1 / V2-1 | HIGH | T5 「heartbeat `jobs` 에 없음 = 띄운 적 없음」을 확인으로 씀(WP3:358, :1186). 결정 335 가 버린 도우미의 늦은 heartbeat(WP3:671-675, :1738)가 「한 번 봐주기」(`remote_workers.py:931-942, 264-268` → 4판 핀 `:925-951, 260-270`)를 쓴 뒤 도착하면 claim 전에 찍은 목록(`remote_worker.py:434, 934`)으로 도는 표식 잡을 닫고 홀드 없이 그룹을 연다 | heartbeat `seq` · `claims_settled`, claim `claim_seq`, T5 는 표식 잡이면 홀드, `_fresh_claims` 는 레거시만, 워커 응답 적용 순서. RL9 · RL50, 변이 다섯 | 고침 — 339-a · 339-b |
| V1-2 / V2-3 | MED | S7 의 두 번째 grace 가 증명 상한 밖(최대 2·grace_J + 4), 명령이 SIGTERM 에 끝나고 무시하는 멤버가 남으면 워커가 죽은 경우 아무도 안 거둠(WP3:444, :419, :1281, :1467-1476, :384, :472-475, :758) | 감독자의 명령 끝 뒤 쓸기, 러너 S7 의 SIGKILL 시한을 첫 SIGTERM + grace_J 로(F84 · F88), §4.1.3 · §4.1.4 · 정리 1 의 3 · 4. RL51, 변이 둘 | 고침 — 339-c (2) |
| V2-2 | MED | Popen 창의 SIGTERM 이 명령에 안 닿음(WP3:1271-1280 대 :441, :523) | Popen 뒤 적힌 SIGTERM 을 그룹에 한 번. RL51(a), 변이 `supervisor-popen-window-term-dropped` | 고침 — 339-c (1) |
| V1-3 · V2-8 | MED · LOW | 홀드의 그룹을 「그룹 NULL 이면 프리셋의 지금 그룹」, 레거시 X 를 「닫는 때의 프리셋 값」으로 — 프리셋이 사라지면 열리는 쪽(WP3:1101-1102, :1214-1215, :364, :370, :628; `remote_worker.py:943` → 4판 `:942-946`) | 홀드는 잡 행만 읽음, 레거시 X = 표식 또는 설정에 없음, 그룹은 잡 행 또는 `irreversible:<프리셋>`. claim 때 프리셋이 없으면 프로세스가 없다(F90). RL53 · RL24 · RL47, 변이 둘 | 고침 — 339-d |
| V1-4 | MED | PR2 · PR3 이 둘 다 `core/status.py` 를 owns(`tools/dev_batch.py:229-233` 가 거절), PR3 의 연결에 `server.py` · `store.py` 가 필요한데 owns · shared 에 없음(WP3:1312-1313, :1326) | 홀드의 기계 판독 표면을 PR1 로, PR3 는 사람용 그리기만 — owns 겹침 0(§5, F87) | 고침 — 350 |
| V1-5 | MED | RL43(d) 닿지 않음 — 감독자만 죽이면 러너가 리더 끝을 보고 바로 S7 로 명령을 거두므로 뒤의 취소가 `cancelled` 를 못 만듦(WP3:1410, :389, :1287) | RL43(d) 와 W13 을 「러너가 S7 로 거둠 → `failed`(−9) · `group_gone: true` · 홀드 0」 으로 | 고침 |
| V1-6 | LOW | 홀드에 `[[notify]]` 를 부르지 않음(WP3:1236, :1321-1323, :1350) | 홀드를 만든 잡의 잡 끝 알림에 `RCM_HOLD` · `RCM_HOLD_GROUP` · 본문 한 줄, opt-in `on` 값 `irreversible_hold`(§4.12), 릴리스 묶음(§6 #13). RL54, 변이 넷 | 고침 — 339-e(6차에서 Codex 의견으로 바꿈) |
| V2-4 | LOW | 감독자 시작 실패가 종료 1 로 명령의 실패와 겹침, `-I` 가 `PYTHONPATH` 를 버려 소스 트리 실행에서 못 뜸(WP3:1262-1266, :1273-1274) | 종료 125 + 표지 줄, `-I` + 파일 경로(F83). RL52, 변이 둘 | 고침 — 339-c (3)(4) |
| V2-5 | LOW | `_heard_lock` 이 판정과 닫기 커밋을 감싸 바쁜 DB 에서 모든 heartbeat 가 줄 섬, 증명 2 에 서버 잠금 대기가 없음(WP3:561-571, :483-487, :277) | 세션별 잠금 · 틱 상한 · 바쁨이면 다음 틱, 정리 2 에 잠금 대기. RL56, 변이 `grace-close-global-lock` | 고침 — 339-g |
| V2-7 | LOW | 로그 읽는 쪽이 커밋 안 된 바이트를 읽음(WP3:871-882, :1098) | `log_file_bytes` 까지(F85). RL55, 변이 `log-read-past-committed` | 고침 — 339-f |
| V1-7 · V2-6 | LOW | §3 의 `file:line` 이 dev 와 어긋남(`remote_worker.py:272`, `:418-420`, `runner.py:255-296`, `:333`, `server.py:252-256`, `:263`) | 고정 sha 를 `c8590dd`(지금 dev)로, §3.0 핀 이동 표를 문서 전체에 적용(→ `remote_worker.py:285`, `:432-434`, `runner.py:349-390`(requires 안 확인 `:360-369`), `:427`, `server.py:256-260`, `:267`) | 고침 |
| 메인 세션 지시(M5t-c 343) | — | M5t-b 의 409 와 M5t-c 거절 MAC 의 관계가 없음 | 교차 명세 의존: 짝지은 토큰의 `session_fenced` · `protocol_required` 에 M5t-c 가 `_send_error` 에서 `pair_reject_mac` 을 단다, M5t-b 는 그 길을 우회하지 않는다, 짝지은 워커는 검증된 펜싱에만 종료 0, 검증된 `protocol_required` 에만 종료 3 이고 검증 안 된 것은 프로세스 안 재시도(§1.2 · §4.2 · §4.7 · W5 · W6 · §9 · §6 #20) | 반영 — M5t-c 결정 343(메인 세션) |

339-b 의 홀드는 4판의 `claims_settled` 로 「없음」이 순서와 상관없이 참이 된 뒤에도 남는다. 결정이 그렇게 정했다. 살아 있는
워커에서 claim 응답을 잃은 표식 잡은 사람이나 재시작이 풀어야 한다(§9). 이를 줄이려면 다음 명세가 「그 세션이 그 잡을 받지
않았다」는 명시 확인을 정할 수 있다. 그 잡 번호를 단 좁은 확인이 한 예다. 라운드 중에 새 설계를 넣지 않는다는 규칙에 따라 이번
판에는 넣지 않았다.

## 13. 구현하며 바뀐 것 (Changed while implementing)
