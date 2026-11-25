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
> 이 명세의 출처는 넷이다. 위의 오너 발언, `docs/m5t-workplan.md` 의 결정 252~255 와 §12 「M5t-b 결과 고리 프로토콜」,
> 접두어 RL 로 쓴 시나리오 표, 그리고 오너가 맡긴 결정이다. 마지막 것은 2026-11-26 메인 세션이 Codex 와 논의해 두 차례
> 정했다. Codex 원문 파일과 오너 발언 원본 파일은 레포 밖에 있고 이 레포가 공개라서 경로를 적지 않는다.
>
> 결정 번호는 256 부터다. 범위는 256~279 이고 M5t-c 와 겹치지 않게 미리 나눴다.
>
> 이 판은 1판이다. M5t 전체를 한 문서에 담았다가 거절된 셋째 판(이 문서에서 「3판」)의 결과 고리 설계는 재료로만 썼다.

## 0. 한 줄

원격 워커와 서버 사이의 연결이 끊기거나 어느 한쪽이 재시작돼도 잡의 결과가 정확히 한 번, 거짓 없이 요청자에게 돌아오게
한다. 장치는 셋이다. 첫째는 워커 세션이다. 워커 프로세스마다 세션 id 를 하나 둔다. 같은 이름의 새 세션이 등록하면 옛
세션을 펜싱해 그 보고를 받지 않는다(결정 255). 둘째는 끊김 유예다. 기본값은 600초이고 잡마다 claim 때 그 값을 얼린다.
되돌릴 수 없음 표식 `irreversible = true` 를 단 프리셋만 워커가 유예가 끝나기 전에 스스로 멈춘다(결정 253). 셋째는 로그
오프셋과 순서 있는 보고 큐다. 워커는 phase, 로그, 산출물, finish 순서로 보내고 큐를 디스크에 남겨 재시작을 넘긴다. 첫
절은 상태 전이 표와 타이머별 시계(§4.1)이고 나머지 설계는 그 표의 칸에서 나온다. DB 는 v26, 워커 프로토콜은 2 가 된다.
구현 PR 은 넷이다. PR1 이 줄기이고 PR2 와 PR3 이 그 뒤에 병렬로 가며 PR4 가 실기 수용이다.

## 1. 출처와 오너 결정

### 1.1 요구와 이 명세

요구마다 그 요구가 말하는 것과 이 명세가 정하는 것을 나란히 둔다.

| 요구(출처) | 요구가 말하는 것 | 이 명세가 정하는 것 |
|---|---|---|
| 「3. rcm 작업을 진행해서 결과 전달하는 컴퓨터」(2026-11-24) | 실행 머신이 결과를 전달한다 | 결과 전달을 끊김 · 재시작 · 중복에 견디게 한다(§4.2~§4.6) |
| 「Rcm워커 다 한 다음에 어떻게 알려주는거까지 해야지」(2026-11-24) | 워커가 끝낸 뒤 알림까지 | 알림 자체(사람용 마지막 줄 · `ran_on` · `rcm wait --notify`)는 M5t-a 결정 248 e·f 다. 이 명세는 알릴 결과가 사라지거나 틀리지 않게 하는 쪽(결과 고리) |
| 결정 253 「되돌릴 수 없는 것만 멈춤」 | 표식 프리셋만 유예(약 10분) 뒤 워커가 스스로 멈추고 결과는 종료 3, 나머지는 계속 | 표식 키 `irreversible`(결정 257), 유예 600초(결정 263), 자기 멈춤(결정 267), 일반 프리셋은 계속 돌고 서버는 유예 뒤 `lost`(결정 256) |
| 결정 255 「지원 안 함」 | 워커 이름당 살아 있는 세션 하나, 새 세션이 옛 세션을 펜싱, 옛 세션은 409 받고 잡 멈춘 뒤 끝남 | 세션 표와 펜싱(결정 265), 펜싱된 잡의 자리(결정 261), 종료 코드 0(결정 258) |
| 결정 252 「셋으로 나눈다」 | M5t-b 는 상태 전이 표부터 | §4.1 이 첫 절 |
| 2026-11-26 「멀티에이전트로 나눠서 … dev batch로」 | 병렬로 나눌 수 있는 것은 나눈다 | §5: PR1 뒤 PR2 · PR3 이 겹치는 파일 없이 병렬 |
| 2026-11-26 「이후에 물어볼거 … 코덱스와 논의」 | 새 질문은 Codex 와 논의 | 위임 결정 256~279(§11). 새 질문도 메인 세션이 Codex 와 논의해 정한다 |
| 2026-11-25 「민감데이터나 API 키 … 없어야해 … public」 | 공개 레포 | §8 |

### 1.2 이미 확정된 것

아래는 이 명세가 바꾸지 않는 결정이다. 따옴표 안은 원문이다.

- 결정 253(`docs/m5t-workplan.md:1042`): 「배포 · 스토어처럼 두 번 돌면 안 되는 프리셋에만 표식을 두고, 그 잡은 유예(약
  10분)가 지나면 워커가 스스로 멈춘다. 결과는 「모름」(종료 3)이다. 게이트 · 시험 같은 나머지는 계속 돈다. M5t-b 가
  구현한다」
- 결정 254(`docs/m5t-workplan.md:1043`): `local_only = true` 다. dev 에 머지됐다(#266, CHANGELOG `[Unreleased]`).
- 결정 255(`docs/m5t-workplan.md:1044`): 「워커 이름당 살아 있는 세션은 하나다. 새 세션이 등록하면 옛 세션은 409 를 받고
  잡을 멈춘 뒤 끝난다. M5t-b 가 구현한다」
- M5n §13(`docs/m5n-workplan.md:652`)의 안 하는 것: 「유실된 잡을 다른 머신에서 자동 재실행하기. 배포·업로드 프리셋은 두 번
  돌면 안 된다.」 이 명세도 그대로 둔다.
- PLAN 「fail-open 금지」(`PLAN.md:516-528`): 종료 3 은 「모른다」이고 `lost` 를 조용히 되돌리지 않는다. 이것도 그대로 둔다.
- 오너가 맡긴 결정 1차(2026-11-26, 메인 세션이 Codex 와 논의해 정함): 질문 Q1~Q7 과 기본값 둘이다. §11 의 결정 256~264 가
  되었다.
- 오너가 맡긴 결정 2차(같은 날): 레인 질문 Q-A, 펜싱된 세션의 늦은 finish(Q-B), 서버 재시작 뒤의 유예(Q-C), Linux 에서
  고아를 확인하지 못할 때(Q-D), 추천이던 결정 265~278, M5t-c 와의 맞물림이다. 결정 265~279 가 확정되었고 Q-B 는 §4.2 ·
  §4.6 에, Q-C 는 §4.3 에, Q-D 는 §4.4 와 §9 에, M5t-c 와의 순서는 §4.2 · §4.7 · §5 · §9 에 반영했다.

### 1.3 이 명세가 하지 않는 것

- 짝짓기, 서버 신원, `tokens.pool`, 평문 LAN 위협 모델은 M5t-c 가 한다(DB v27).
- 요청 머신의 OS 알림, `ran_on`, `RCM_MACHINE`, 사람용 마지막 줄의 실행 머신은 M5t-a 결정 248 e·f 가 한다.
- `lost` 잡을 자동으로 다시 돌리지 않는다(M5n §13 유지). 늦게 온 결과로 `lost` 를 뒤집지도 않는다(결정 256).
- 프로토콜 1 워커의 새 claim 은 받지 않는다. 결정 262 의 레거시 lease 만 받는다. 버전이 섞인 운영 경로를 새로 만들지 않는다.
- 워커 쪽에서 비밀을 가리지 않는다. 서버가 가린다(F14). 부분 업로드 재개(`PLAN.md:119`)와 Windows 도 다루지 않는다.
- 웹에 `late_result` 를 그리지 않는다. 잡 문서, `rcm wait` JSON, 사람용 줄에만 둔다(결정 274).

## 2. 범위: 누가 무엇을 하나

rcm 이 이 명세의 PR1~PR4 로 대부분을 만든다. 이 레포를 쓰는 앱 프로젝트는 프리셋에 표식을 단다. 오너는 승인과 레인 답과
운영 반영을 맡는다.

| 누가 | 무엇 |
|---|---|
| rcm (이 명세, PR1~PR4) | 세션 · 펜싱 · 유예 · 자기 멈춤 · 재시작 순서 · 고아 정리 · 프로토콜 2 · 로그 오프셋과 보고 큐 · `late_result` · `log_complete` · 화면 사유 · DB v26 · 문서 |
| 이 레포를 쓰는 앱 프로젝트(그쪽 세션, 오너 지시 뒤) | 원격 워커에서 돌 수 있는 배포 계열 프리셋에 `irreversible = true` 를 단다. 모르는 프리셋 키는 기동 실패라(F23) 그 프리셋을 읽는 서버가 모두 이 기능이 든 버전 이상일 때만 단다. `local_only = true` 인 프리셋은 원격에 안 가므로 `irreversible` 이 효과가 없다(F24) |
| 오너 | 명세 승인(머지). 레인 답(결정 279). 운영 반영 승인. 2026-11-26 지시로 그 밖의 질문은 메인 세션이 Codex 와 정하고 완료 보고서에 적는다 |
| 메인 세션 | 결정 페이지 · Codex 논의 기록 · dev-batch 운용 · 릴리스(M5t-a · M5t-c 와 함께 묶을지는 메인 세션의 릴리스 계획, 이 명세 밖) |

## 3. 실측과 코드 제약

기준은 고정 sha `fdf196d4ef682612857fb12df9162f67545b63f0` 이다. 이 명세를 시작한 origin/dev 커밋과 같다. 모듈 파일은
`src/remote_ci_monitor/` 를 빼고 적었다. 각 사실이 설계에 미치는 영향은 표의 마지막 열에 적었다. 운영 수치는 받지 않았으므로
운영 빈도는 `—` 로 둔다. 같은 사실은 이 절에 한 번만 두고 다른 절은 F 번호와 결정 번호로 가리킨다.

이 명세가 쓰는 내부 용어를 먼저 풀어 둔다. 워커는 서버에서 잡을 집어 가 실제로 돌리는 머신(`rcm worker`)이다. 레인은 한
머신이 잡을 동시에 돌리는 칸이다. claim 은 빈 레인이 대기 잡을 하나 집어 가는 일이고 heartbeat 는 워커가 살아 있다고
서버에 주기적으로 보내는 보고다. janitor 는 서버가 5초마다 돌리는 정리 루프다. `lost` 는 결과를 모르는 채로 닫힌 잡의
상태이고 `rcm wait` 는 이때 종료 3 을 낸다. 펜싱은 같은 이름의 새 세션이 등록했을 때 옛 세션의 요청을 서버가 409 로
거절하는 일이다. lease 는 잡과 그 잡을 claim 한 세션의 쌍이다. supersede 는 같은 그룹에 새 제출이 오면 앞 잡을 취소하는
설정이다. M5q 의 따라가기는 워커가 서버 버전에 맞춰 설치본으로 자신을 다시 실행(exec)하는 일이다.

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
| F11 | 그룹 배제는 `BUSY_STATES`(running · cancelling) 잡으로 센다 | `store.py:3316-3323`, `core/model.py:37` | 잡이 `lost` 가 되는 순간 그룹 자리가 풀린다(3판 A-H7). 자리를 지키려면 잡을 busy 로 둔다(결정 261 · 267) |
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
| F29 | 행동 가능한 사유 목록 `ACTIONABLE_REASONS` 와 웹 `ACTIONABLE` 이 같은 목록이고 순서를 시험이 잠근다 | `core/model.py:165-176`, `web/app.js:20`, `tests/test_action_marker_m5s.py:139-141` | 새 사유를 더하면 두 목록과 그 시험을 같이 고친다(PR3) |
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
| F46 | `rcm worker --check` 는 health · whoami · spec 만 부르고 등록 · capabilities 를 안 한다(주석 「등록도 capabilities 도 없다」) | `cli.py:4645-4700`, `cli.py:4703-4715` | 세션을 만들지 않는다 → 도는 서비스 워커를 펜싱하지 않는다(RL12 c). M5t-a PR2(#268)가 머지되면 `rcm doctor` 도 같은 규칙(그 PR 의 동작은 아직 사실로 인용하지 않음) |

### 3.4 시험 · 문서에 걸린 것

| # | 사실 | 근거 | → 설계 |
|---|---|---|---|
| F47 | 오늘 계약을 잠근 시험: 60초 뒤 `lost`(`test_worker_past_timeout_loses_its_running_job_and_shows_down`), 돌아온 heartbeat 가 lost 잡을 안 살림, 닿지 않는 워커의 cancelling 은 lost, kill -9 뒤 timeout 에 lost · 새 워커가 이어 받음 | `tests/test_worker_lost.py:58-148`, `tests/test_e2e_worker.py:775-805` | 결정 253 이 바꾼 계약이라 기대값을 유예 기준으로 고쳐 쓴다(약화 아님, §7.3) |
| F48 | 서버 재시작 e2e(원격 잡이 running 으로 남고 finish 로 succeeded), SIGTERM e2e(`worker stopped`, 종료 0) | `tests/test_e2e_worker.py:810-829, 857-880` | 유지 — 회귀 잠금 |
| F49 | 문서가 「60초 뒤 lost」 를 말한다 | `docs/configuration.md:1185, 1386`, `docs/operating.md:786`, `templates/server.toml:42` = `examples/server.toml:42`(바이트 같음을 `tests/test_examples.py` 가 잠금) | PR1 이 고친다 |
| F50 | `DB_VERSION = 25`, 마이그레이션 전 백업(결정 74), 더 새 DB 는 거절 | `store.py:88, 480, 661-677, 1208-1248, 778-785` | v26 은 PR1 하나가 올린다. M5t-c 는 v27 |
| F51 | 높은 엔트로피 문자열 리터럴은 gitleaks `secrets` 잡을 빨갛게 하고 나중에 고쳐도 커밋 이력에 남는다 | `AGENTS.md:137-139` | 시험의 세션 id 는 실행 중 만든다(리터럴 32-hex 금지) — §8 |

## 4. 설계

### 4.1 상태 전이 표와 타이머

이 절을 먼저 쓴다. 나머지 설계는 이 표에서 나온다(결정 252). 아래 용어는 이 절에서만 쓴다.

- 세션 S 는 워커 프로세스 한 번이다(결정 265). 잡 J 는 claim 때 S 와 묶인다(`jobs.worker_session_id`). lease 는 (J, S) 쌍이고 별도
  lease id 는 두지 않는다(결정 265).
- 잡 J 의 얼린 값은 둘이다. `G_J` 는 `jobs.disconnect_grace_seconds` 이고 `X_J` 는 `jobs.irreversible` 이다(결정 257).
- 서버의 들음 시각 `heard(S)` 는 서버가 S 의 인증된 요청을 마지막으로 받은 단조 시각이다. 메모리에만 두고 바닥은 서버 기동
  단조 시각이다(결정 266). 펜싱된 뒤 S 가 보낸 요청은 `heard` 를 올리지 않는다.
- 서버 시각 기준 경과는 `gap(J) = mono_now − max(heard(S_J), 기동)` 이다.
- 워커의 마지막 확인 `ok_at` 은 2xx 를 받은 heartbeat 를 **보낸** 시각이다. 벽시계와 단조 시계 둘 다 적는다. 워커 경과는
  `wgap = max(벽 경과, 단조 경과)` 다.
- 여유 `m` 은 `워커 grace_seconds + heartbeat_seconds + SELF_STOP_SLACK_SECONDS(5)` 다(결정 267).
- 네트워크 상태는 셋이다. N 은 정상, D 는 서버 정지(연결 거부), P 는 분단이다. 분단은 서버가 살아 있는데 워커 요청에만
  응답이 없는 상태다.

#### 4.1.1 서버가 보는 전이 (세션 상태 × 잡 상태 × 네트워크)

| # | 세션 | 잡(서버 DB) | N 정상 | D 서버 정지 | P 분단 | 행 |
|---|---|---|---|---|---|---|
| T1 | 현재 · 들림(gap ≤ T=60s) | running(executing) | heartbeat · phase · log · finish 받음. finish 가 상태를 정함(첫 보고가 이김) | 판정 없음(서버가 없다). 워커는 보고를 큐에 쌓고 잡은 계속. 서버가 뜨면 `heard` 바닥 = 기동 시각 | 서버: `heard` 가 멈춤 → T 뒤 T2 | RL6 RL7 RL16 |
| T2 | 현재 · 조용함(T < gap ≤ G_J) | running | (다시 들리면 T1, 같은 세션 · 같은 lease 로 이어 보고, 프로세스 안 죽음) | 기동 뒤 T 안에 heartbeat 가 오면 T1 | 잡은 `running` 유지, 사유 `worker_unreachable_grace`, ETA null, 레인 `down`(헬스 503 그대로). 서버는 닫지 않는다 | RL1 RL5 RL6 RL26 RL28 |
| T3 | 현재 · 조용함(gap > G_J) | running | — | 서버가 꺼져 있던 시간은 세지 않는다(바닥 = 기동). 기동 + G_J 에 판정 | `lost`(`worker_unreachable`, `seconds` = gap), 그룹 자리 풀림, `job_finished` 1회. 그 뒤 같은 세션의 finish → `late_result` 만(결정 256) | RL1 RL2 RL3 RL4 RL5 RL7 |
| T4 | 현재 · 들림 | cancelling | heartbeat `cancel` → 워커가 끝냄 → `cancelled`(supersede 면 `superseded by #N` 유지). 워커가 확인 안 하면 `kill_at + 2H` 에 `cancelled`(`cancel_unconfirmed`) — 현재 · 들리는 세션의 잡에만 | 기동 뒤 heartbeat 가 오면 `cancel` 전달 | 조용해지면 `kill_at` 규칙 멈춤. gap > G_J 면 `lost`(`worker_unreachable`). `cancelled`(2)로 위장하지 않는다 | RL8 RL33 |
| T5 | 현재 · 들림 | running, 워커가 모름(claim 응답 유실) | 다음 heartbeat 한 번 봐줌(`_fresh_claims`), 그다음 heartbeat 에 목록에 없으면 `lost`(`worker_restarted_without_job`) | 해당 없음 | 재연결 뒤 같은 규칙. 끝내 안 오면 T3 | RL9 |
| T6 | 현재 | running(materializing) | 정상 진행 | 워커: 스냅샷 내려받기 연결 실패 → finish `lost`(`snapshot_unreachable`) 큐 → 서버가 뜨면 전달 | 같음. 전달 전 gap > G_J 면 T3, 늦은 finish 는 `late_result` | RL23 |
| T7 | 현재 | running, 워커는 끝냄(보고 큐에 finish) | 큐 순서대로 phase → 로그 → 산출물 → finish. 확정 | 서버가 뜨면 이어서 | 재연결이 G_J 안이면 실제 결과, 밖이면 T3 뒤 `late_result` | RL14 RL19 RL21 RL22 |
| T8 | 펜싱됨(같은 이름의 새 세션 S′ 가 등록) | running · cancelling | 옛 프로세스의 다음 요청(무엇이든) → 409 `session_fenced` → 그룹 멈춤 → 좁은 확인 finish(`lost` + `worker_replaced`) 만 받음 → 잡 `lost`. 옛 프로세스 종료 0. 확인 전까지 잡은 busy(그룹 · 레인 잡힘), 새 세션의 그 레인 claim 은 204 | 판정 없음(펜싱은 DB 에 남는다) | 확인이 없으면 `heard(S) + G_J` 에 `lost`(`worker_unreachable`). 그 전에는 안 푼다(결정 261) | RL10 RL11 |
| T9 | 이전 세션(새 프로세스가 `previous_sessions` 로 알림 = 그 프로세스가 죽었음을 세션 폴더 잠금으로 확인) | running · cancelling | 새 프로세스가 등록 **전에** 옛 세션의 남은 큐를 옛 세션 id 로 보냄 → 고아 그룹 정리 → 등록 때 서버가 그 세션의 남은 busy 잡을 `lost`(`worker_restarted_without_job`)로 닫고 펜싱 | 등록이 5초마다 다시 시도 | 같음 | RL12 RL14 RL15 |
| T10 | 같은 세션 id 의 재등록(레인이 `not_registered` 409 를 받음) | running | 멱등: 펜싱 없음 · 잡 안 닫음 | — | — | RL12 |
| T11 | 레거시(세션 칸 NULL — v26 이전에 claim 된 잡) | running · cancelling | 세션 없는 heartbeat · phase · log · finish 를 **그 이름에 레거시 busy 잡이 있을 때만** 받음(이름 단위 「잊힌 잡」 규칙도 그 잡에만). 새 claim 은 409 `protocol_required` | 기동 바닥 | `heard(이름) + 서버 기본 G` 에 `lost`(`worker_unreachable`). 레거시 잡이 다 끝나면 호환은 저절로 거둔다 | RL24 |
| T12 | 세션 없는 요청(옛 워커), 레거시 잡 없음 | — | register · claim · heartbeat · capabilities → 409 `protocol_required`(+`worker_protocol: 2`). 등록 409 는 버전 검사 **뒤**(버전이 다르면 오늘의 409 + `server_version`) | — | — | RL13 RL24 |
| T13 | 새 워커 → 프로토콜 1 서버 | — | `/worker/spec` 에 `worker_protocol` 이 없고 서버 버전이 같으면 등록하지 않고 종료 3. 버전이 다르면 오늘처럼 등록 409 → 따라가기(M5q) 또는 종료 2 | 등록 전 연결 실패는 오늘처럼 5초 재시도 | — | RL25 RL32 |
| T14 | 끝난 잡(J terminal)에 늦은 보고 | terminal | 같은 세션의 finish 이고 J 가 `lost` → `late_result` 1회 기록, 409 `job_closed`(+`state`). log · phase · 산출물 → 409 `job_closed`, 반영 0. 펜싱된 세션 → 409 `session_fenced`(기록 없음) | — | — | RL11 RL21 |

#### 4.1.2 워커가 보는 전이 (워커 프로세스 한 개, 잡 하나)

| # | 워커 상태 | 표식 잡(X_J) | 일반 잡 | 행 |
|---|---|---|---|---|
| W1 | 연결됨(heartbeat 2xx) | 그대로 | 그대로 | RL6 |
| W2 | 끊김, `wgap < G_J − m` | 계속 돌고 보고는 큐에 | 계속 | RL1 RL5 RL6 |
| W3 | 끊김, `wgap ≥ G_J − m` (heartbeat 루프 매 회차의 **보내기 전** 판정. 잠에서 깬 첫 회차 포함) | 그룹 SIGTERM → `grace_seconds` → SIGKILL, finish `lost`(`worker_self_stopped`, `seconds` = wgap) 큐 | 계속(결정 253) | RL1 RL2 RL4 RL26 |
| W4 | 보고에 409 `job_closed` | `state` 가 무엇이든 멈춤(이미 W3 으로 멈췄을 것) | `state=cancelled`·`timed_out` 이면 멈춤, `lost` 면 계속 돌고 그 잡의 로그 전송만 멈춤(로컬 스풀은 남김). 끝나면 finish → 서버가 `late_result` | RL5 RL21 |
| W5 | 어느 요청이든 409 `session_fenced` | 모든 잡 멈춤 → 잡마다 좁은 확인 finish → 종료 0 | 같음(표식 무관, 결정 255 · 261) | RL10 RL11 |
| W6 | 409 `protocol_required` | 등록 전이면 종료 3. 도는 중이면(서버가 다운그레이드됨) 모든 잡 멈춤 → 종료 3 | 같음 | RL25 |
| W7 | 프로세스가 `kill -9` 로 죽음 | 잡 그룹은 고아로 남음 → 다음 프로세스가 시작 때 정리(T9) | 같음 | RL14 RL15 RL31 |
| W8 | SIGTERM/SIGINT | 오늘처럼 멈춤, finish `lost`(`worker stopped` 문장) 큐 → 종료 전 한 번 보내고 못 보내면 디스크에 남김 → 다음 시작이 보냄 | 같음 | RL14 |

#### 4.1.3 타이머와 시계

| 타이머 | 누가 | 시계 | 시작점 | 끝나면 | 값 |
|---|---|---|---|---|---|
| heartbeat 간격 H | 워커 | `stopping.wait` | 직전 회차 | 다음 heartbeat (보내기 전에 W3 판정) | 서버가 알려 줌 `worker_heartbeat_seconds` 5 |
| 워커 down T | 서버 | 벽시계 `workers.last_seen_at`(F1, 그대로) | 현재 세션의 마지막 요청 | 레인 `down` · 대기 잡 `worker_down` · 헬스 503. **잡은 닫지 않는다** | `worker_timeout_seconds` 60 |
| 끊김 유예 G_J | 서버 | 단조 `mono_fn`, `heard(S)` 의 바닥 = 기동 | `max(heard(S_J), 기동)` | `lost`(`worker_unreachable`) | 잡 행에 얼린 값, 기본 `disconnect_grace_seconds` 600 |
| 자기 멈춤 | 워커 | `max(벽 경과, 단조 경과)` | 2xx heartbeat 의 보낸 시각 | 표식 잡 그룹 멈춤 | `G_J − m`, 0 이하면 첫 실패 회차에 |
| 취소 미확인 | 서버 | 벽시계(오늘 그대로) | `cancel.kill_at` | `cancelled`(`cancel_unconfirmed`) — 현재 · 들리는 세션만 | `kill_at + 2H` |
| SIGTERM→SIGKILL | 워커 러너 | 러너 `now_fn`(오늘 그대로) | SIGTERM | SIGKILL | 워커 `grace_seconds` 10 |
| 재전송 창 | 워커 | 벽시계(디스크의 `ended_at` ISO) | 잡 끝 | 로그 · 산출물 포기 → finish 에 `log_bytes` 실어 보냄. finish 도 못 보냈으면 큐 항목 버림 + 로그 | `RETRANSMIT_SECONDS` 86400(결정 264) |
| 스풀 보관 | 워커 | 파일 mtime(시작 청소, F41) | 마지막 쓰기 | 폴더 삭제(잠긴 폴더 제외) | `WORKSPACE_KEEP_DAYS` 7(결정 264) |
| janitor 틱 | 서버 | `stop.wait` | — | 유예 판정 · 취소 미확인 | `JANITOR_SECONDS` 5 |
| 등록 재시도 | 워커 | `stopping.wait` | 연결 실패 | 다시 등록 | `REGISTER_RETRY_SECONDS` 5 |

서버보다 워커가 먼저 멈추는 이유가 있다(RL1 · RL4, 결정 267). 워커의 `ok_at` 은 보낸 시각이므로 실제 시간으로 서버의
`heard` 보다 앞이거나 같다. 워커는 `G_J − m` 에 멈추고 서버는 `G_J` 에 닫는다. 그래서 서버가 그룹 자리를 푸는 T3 에는 표식
잡의 프로세스가 이미 없다. `m` 은 SIGTERM 에서 SIGKILL 까지의 간격(`grace_seconds`)과 판정 간격(H)에 5초를 더한 값이다.

### 4.2 워커 세션과 펜싱 (결정 255 · 260 · 261 · 265)

세션 id 는 워커 프로세스가 시작할 때 `secrets.token_hex(16)` 으로 하나 만든다. 모든 레인이 같은 값을 쓴다(결정 260). M5q 의
따라가기로 exec 하면 새 이미지가 새 세션을 만든다. 서버는 세션을 새 표 `worker_sessions`(§4.9)에 적는다. 그 이름에서
`fenced_at IS NULL` 인 행이 현재 세션이다. 등록 트랜잭션이 이런 행을 언제나 많아야 하나로 지킨다.

register 는 한 `BEGIN IMMEDIATE` 안에서 아래 순서로 처리한다(RL13 · 3라운드 M7).

1. 본문 모양이 틀리면 400 이다.
2. 버전이 다르면 오늘처럼 409 와 `server_version` 을 돌려준다.
3. 세션 칸이 없으면 409 `protocol_required` 다.
4. S 가 이미 펜싱된 세션이면 409 `session_fenced` 다. 펜싱된 프로세스가 재등록해서 새 세션을 거꾸로 펜싱하는 핑퐁을 이것이
   막는다.
5. S 가 현재 세션이면 멱등이다. 워커 행만 upsert 하고 잡을 닫지 않으며 능력 보고도 지우지 않는다.
6. S 가 처음이면 `previous_sessions` 에 든 같은 이름의 세션들에서 busy 잡을 `lost`(`worker_restarted_without_job`)로 닫는다.
   그 세션들과 S 가 아닌 현재 세션을 펜싱한다(`fenced_at`, `fenced_by = S`). 그 뒤 S 행을 넣고 워커 행을 upsert 하고 능력
   보고를 지운다. 능력 보고를 지우는 것은 오늘의 규칙(F5)이다.

M5t-c 는 등록에 `pair_proof` 검사를 더한다. 2차 위임 결정은 그 검사를 세션 넣기, 펜싱, 능력 보고 지우기, 잡 닫기보다 먼저
하도록 정했다. 위 순서에서 이 네 가지는 6 에서 일어난다. 버전 검사와 프로토콜 검사는 부작용이 없으면 `pair_proof` 보다 앞서도 된다. Codex
와 논의한 결과 두 명세 사이에 충돌은 없다(§5 · §9).

오늘은 등록이 그 이름의 활성 잡을 전부 닫는다(F5). 이 규칙은 없앤다. 등록이 닫는 것은 `previous_sessions` 로 명시한 세션의
잡뿐이다(3판 r1 A-H4).

spec 을 뺀 모든 `/worker/*` 요청이 세션을 싣는다(§4.10). 서버는 세 갈래로 판정한다. 표에 없는 세션이면 409 `not_registered`
이고 워커는 같은 S 로 재등록한다(T10). 펜싱된 세션이면 409 `session_fenced` 다. 현재 세션이면 요청을 처리하고 `heard(S)` 를
갱신한다.

펜싱된 세션에 허용하는 요청은 하나뿐이다. finish 본문이 `outcome = lost` 와 `summary_code = worker_replaced` 이고 그 잡이
busy 이며 잡의 세션이 S 일 때다. 이 명세는 이것을 좁은 확인이라 부른다(결정 261). 응답은 200 이다. 그 밖의 요청은 모두 409
`session_fenced` 이고 서버에 한 바이트도 반영하지 않는다(RL11). 잡이 이미 `lost` 로 닫힌 뒤 펜싱된 세션이 보낸 늦은 finish
도 같다. 2차 위임 결정 Q-B 는 이 finish 를 `late_result` 로도 남기지 않기로 했다. 펜싱된 세션은 권한을 잃었고 새 세션과
겹쳐 돌았을 수 있기 때문이다(§4.6).

펜싱된 세션의 잡은 좁은 확인이 오거나 `heard(S) + G_J` 가 지날 때까지 busy 로 남는다. 그동안 그룹과 레인이 잡혀 있다(F11).
펜싱 시각부터 유예를 다시 세지는 않는다. 옛 프로세스가 펜싱 전부터 조용했다면 그만큼 빨리 풀린다.

새 세션의 claim 이 펜싱된 세션의 잡이 잡고 있는 레인에 오면 서버는 `LaneBusy` 를 409 대신 204(잡 없음)로 답한다. 409 가
쏟아지지 않게 하려는 것이다(F10). 자기 세션의 잡과 겹치면 오늘처럼 409 다.

세션 행은 `forget_workers`(F21)가 워커 행을 지울 때만 그 이름의 것을 함께 지운다. 일찍 지우면 늦게 돌아온 펜싱된 프로세스가
`not_registered` 를 받고 재등록해 살아 있는 세션을 펜싱한다. 그러면 핑퐁이 생긴다.

「잊힌 잡」 규칙(F6)과 heartbeat 의 `cancel` 목록도 세션 단위로 바꾼다. 둘 다 그 세션의 busy 잡만 본다.

같은 토큰으로 두 프로세스가 같은 순간에 등록해도(RL11) 등록이 직렬화되므로 나중 등록이 이긴다. 먼저 등록한 쪽은 다음
요청에서 409 `session_fenced` 를 받고 종료 0 으로 끝난다. 살아남는 세션은 정확히 하나다.

거절한 대안은 셋이다.

- 세션을 `workers` 행의 칸으로 두기. 이름당 한 행이라 판정이 이름 단위로 돌아간다. 3판 r2 의 근본 원인이 이것이었다.
- 세션 표 없이 메모리에만 두기. 서버가 재시작하면 누가 현재 세션인지 잊는다.
- 별도 `lease_id` 두기. 잡은 한 번만 claim 되므로 (J, S) 로 충분하다. 이름이 늘면 3판 R1 같은 충돌이 는다.

### 4.3 끊김 유예와 자기 멈춤 (결정 253 · 256 · 257 · 263 · 266 · 267)

서버의 janitor 는 5초마다 `mark_lost_workers` 대신 유예를 판정한다. busy 원격 잡마다 `gap(J) > G_J` 면 그 잡을
`lost`(`worker_unreachable`, `name`, `seconds = int(gap)`)로 닫는다. 판정과 닫기는 `heard` 를 갱신하는 요청 처리와 같은
락(`_session_lock`) 아래에서 한다. 닫기는 `only_from = BUSY_STATES` 와 같은 세션 조건을 건 UPDATE 다(RL4).

서버는 단조 시계로 유예를 센다. 서버 벽시계가 앞으로 뛰어도 heartbeat 가 오던 잡을 닫지 않으려는 것이다(RL27 a). 서버가
재시작하면 메모리가 비므로 바닥은 기동 시각이다(RL7 b). 서버가 잠들면 단조 시계가 덜 가서 늦게 닫는다. 늦게 닫는 것은 안전한
쪽이다.

서버 재시작 뒤 도는 원격 잡의 유예는 서버 기동 시각부터 다시 센다. 2차 위임 결정 Q-C 가 이 답을 확정했다. 벽시계
`last_seen_at` 은 시계 조정과 서버가 멈춰 있던 시간에 안전하지 않다. 기동 시각부터 세면 닫힘이 느려질 뿐 잡이 두 번 돌지
않는다.

거절한 대안은 둘이다. 닫는 트랜잭션에서 벽시계 `last_seen_at` 을 다시 읽는 방법(3판 r1 A-M4)은 시계 점프를 못 막는다.
벽시계에 기동 바닥만 더하는 방법도 점프를 못 막는다.

레인 down 판정(T)은 그대로 벽시계 `workers.last_seen_at` 으로 한다(F1). 레인 표시는 지금 일을 받는지를 묻고 유예는 잡을
버리는지를 묻는다. 둘은 다른 질문이다.

취소 미확인 닫기(F7)는 그 잡의 세션이 현재이고 들릴 때만 한다(RL33). 조용하거나 펜싱된 세션의 cancelling 잡은 G_J 뒤
`lost`(`worker_unreachable`)로 닫는다. 3판에 있던 새 코드 `cancel_unreachable` 은 만들지 않는다(결정 278).

일반 프리셋이면(결정 256) 서버는 T3 에서 잡을 `lost`(종료 3)로 닫는다. 워커는 잡을 계속 돌리고 끝나면 finish 를 보낸다.
서버는 상태를 바꾸지 않고 `late_result` 만 남긴다(§4.6). `rcm wait` 는 T3 에서 3 으로 끝나므로 영영 기다리지 않는다.

표식 프리셋이면 워커가 W3 에서 멈춘다. 결과는 `lost`(3)다. 자기 멈춤 보고가 G_J 안에 서버에 도착하면 요약 코드가
`worker_self_stopped` 다. 도착하지 못하면 서버의 `worker_unreachable` 가 먼저 닫고 뒤의 보고는 `late_result` 가 된다.

값은 claim 때 얼린다(결정 257). claim 트랜잭션 안에서 `jobs.irreversible` 에는 그 프리셋이 표식 프리셋 집합에 드는지를 적고
`jobs.disconnect_grace_seconds` 에는 서버 설정값을 적는다. claim 응답에 둘 다 싣고 워커는 응답의 값만 쓴다. 로컬 레인 잡은
둘 다 NULL 이다. 로컬 잡은 서버가 재시작하면 `lost` 가 되기 때문이다(F4). `store.claim` 은 프리셋을 고른 뒤에야 어느
프리셋인지 안다. 그래서 호출자가 표식 프리셋 이름 집합과 기본 G 를 넘기고 store 가 같은 트랜잭션에서 적는다.

워커는 heartbeat 루프의 매 회차에 heartbeat 를 보내기 **전에** 판정한다. 머신이 잠에서 깨면 `stopping.wait` 가 돌아오고 그
회차의 첫 일이 판정이므로 heartbeat 를 다시 보내기 전에 멈춘다(RL26). `wgap` 은 벽 경과와 단조 경과 가운데 큰 쪽이다.
단조 시계가 잠을 세지 않는 OS 에서도 벽 경과는 실제 경과 이상이다. 벽시계가 뒤로 가면 단조 경과가 실제 경과 이상이다.

워커 쪽에서 거절한 대안은 셋이다. 단조 시계만 쓰면 잠을 못 센다. 벽시계만 쓰면 시계가 뒤로 갈 때 못 멈춘다. 로그와 보고의
응답도 `ok_at` 으로 세는 방법도 버렸다. 서버의 `heard` 는 모든 요청으로 오르므로 워커가 heartbeat 만 세면 워커 쪽 판정이 늘
더 이르다. 이 명세는 그 안전한 쪽을 골랐다.

비대칭 위험이 하나 있다. 워커 벽시계가 앞으로 `G_J − m` 이상 뛰면 표식 잡이 일찍 멈춘다(§9).

### 4.4 재시작 순서 · 이어받기 · 고아 정리 (결정 268)

워커 데이터 폴더에 `<data>/worker-sessions/<S>/` 를 새로 둔다. 이름을 `sessions` 로 하지 않은 것은 문서에서 그 낱말이
「요청 세션」을 뜻하기 때문이다(grep `sessions` 27건). 폴더에는 아래 파일을 둔다.

- `lock`: 프로세스가 살아 있는 동안 `fcntl.flock(LOCK_EX)` 로 쥔다(F43).
- `jobs/<id>/proc.json`: `{pgid, lstart}` 다. 러너가 Popen 한 뒤 관찰자 `spawned(pid)` 로 알리면 `ps -o lstart= -p <pid>` 를 한
  번 부른다(F44 · F45).
- `jobs/<id>/stream.log`: 서버로 가는 스트림 바이트다(결정 271).
- `jobs/<id>/outbox.json`: 보낼 phase(마지막 하나), finish 기록(`outcome · exit_code · summary · summary_code · summary_args ·
  finished_at · ended_at · log_bytes · bundle`), `done` 이다.

워커는 아래 순서로 시작한다(RL14 · RL15 · RL12 b).

1. 데이터 폴더를 열고 시작 청소를 한다(F41). 잠긴 세션 폴더는 건너뛴다.
2. 새 S 를 만들고 그 폴더를 만든 뒤 `lock` 을 쥔다.
3. 프로토콜을 확인한다(§4.7, T13).
4. 다른 세션 폴더마다 `LOCK_NB` 로 잠가 본다. 잠기지 않으면 그 세션은 살아 있으므로 건너뛴다. 잠금이 EWOULDBLOCK 이 아닌
   OSError 를 내면 판정할 수 없으므로 건너뛴다. 네트워크 파일 시스템이 그런 예다.
5. 잠근 세션, 곧 죽은 세션 D 의 잡마다 `proc.json` 의 pgid 가 살아 있고 `ps -o lstart=` 가 기록과 같은지 본다. 같으면
   SIGTERM 을 보내고 워커 `grace_seconds` 를 기다린 뒤 SIGKILL 을 보내며 워커 로그에 한 줄 남긴다. 다르면 pid 가 재사용된
   것이므로 손대지 않는다. `ps` 가 없거나 실패하면 손대지 않고 경고를 남긴 뒤 D 를 「불확실」로 표시한다(RL31).
6. D 의 보고 큐를 D 의 세션 id 로 보낸다. 규칙은 §4.5 와 같다. 연결이 실패하면 등록과 같은 5초 간격으로 다시 시도한다.
7. 등록한다. `previous_sessions` 에는 5 에서 불확실로 표시하지 않은 D 들만 넣는다. 서버는 그 세션들의 남은 busy 잡을
   `worker_restarted_without_job` 으로 닫고 펜싱한다(§4.2). 불확실한 D 의 잡은 서버가 T8 처럼 `heard + G_J` 까지 잡아 둔다.
8. D 폴더는 7일 청소에 맡긴다. 이미 보낸 것은 `done` 으로 표시돼 있다.

Linux 에서 `ps -o lstart=` 는 아직 재지 않았다(F44). 2차 위임 결정 Q-D 는 신원을 확인할 수 없을 때의 대체 동작을 그대로
확정했다. 워커는 신호를 보내지 않는다. 그 세션을 `previous_sessions` 로 알리지도 않는다. 그래서 서버는 그 잡을 유예 만료까지
busy 로 두고 그룹 자리를 지킨다. Codex 는 이 대체 동작이 안전하다고 봤고 메인 세션은 fail-closed 로 받아들였다.

오늘의 e2e 계약(F47 `test_killed_worker…`)은 유지된다. 잡이 이미 `lost` 면 등록이 요약을 덮지 않는다. 등록이 닫는 것은 busy
잡뿐이기 때문이다(RL14 b).

M5q 의 exec 는 레인이 다 놀고 보고 큐가 비었을 때 일어난다. 60초(`DRAIN_REPORT_SECONDS`) 안에 큐가 비지 않으면 오늘처럼 exec
하지만 큐는 디스크에 남고 새 이미지가 시작 순서 6 에서 보낸다. 그래서 F42 의 「restarting anyway」 가 이제 보고를 잃지
않는다.

거절한 대안은 셋이다.

- 등록 본문의 「버린 lease 목록」만 믿기(3판). 그 목록을 만든 프로세스가 죽었는지 서버가 모른다. 잠금이 그 증거다.
- 고아를 pgid 만 보고 죽이기. pid 가 재사용됐으면 남의 프로세스를 죽인다. 이것은 되돌릴 수 없다.
- 데이터 폴더 전체를 잠그기. 같은 폴더를 쓰는 둘째 프로세스를 막으면 결정 255 의 「새 세션이 옛 세션을 펜싱」을 못 한다.

### 4.5 로그 오프셋과 순서 있는 보고 큐 (결정 264 · 271 · 272 · 273)

오프셋의 단위는 워커가 서버로 보내는 스트림의 바이트이고 가리기 전 값이다(F14). 워커의 `stream.log` 길이가 곧 그 잡의
스트림 길이다.

워커 관찰자의 `output(data)` 는 `stream.log` 에 덧붙이기만 하고 펌프를 막지 않는다. 쓰기가 실패하면 그 잡을 「스풀 깨짐」으로
표시하고 그 뒤의 바이트는 세기만 한다(§4.6 `log_bytes`). 네트워크 일은 워커당 하나인 보내는 스레드가 맡는다.

보내는 순서는 잡마다 같다. phase 가 있으면 마지막 값을 먼저 보낸다. 다음으로 `stream.log[acked:]` 를 256 KiB 이하 조각으로
보내는데 조각은 **마지막 `\n` 에서** 자른다. 한 줄은 늘 조각 하나에 든다(F34 · F35). 잡이 끝났으면 산출물을 올리고(§4.6)
마지막에 finish 를 보낸다. 한 잡 안의 순서는 지키고 잡 사이는 서로 기다리지 않는다.

요청은 `POST /worker/jobs/{id}/log` 다. 헤더 `X-RCM-Worker-Session: <S>` 와 `X-RCM-Log-Offset: <n>` 을 싣는다. `<n>` 은 조각의
첫 바이트 오프셋이다. 빈 본문도 받는다. 빈 본문은 서버의 오프셋을 묻는 용도이고 이어받은 세션이 처음에 보낸다.

서버는 `offset > log_offset` 이면 409 `log_gap` 과 `expected_offset` 을 돌려준다. `offset ≤ log_offset` 이면 앞의
`log_offset − offset` 바이트를 버리고 나머지만 받는다. 이미 받은 범위를 무시하므로 멱등이다. 받은 나머지는 가리고
(`mask_bytes`) `log.txt` 에 덧붙여 flush 한다. 그다음 한 트랜잭션에서 `log_offset` 에 원본 길이를 더하고 `log_file_bytes` 에
파일 길이를 적고 마커 이벤트를 남긴다. 응답은 `{job_id, bytes, markers, offset}` 이고 `offset` 은 새 `log_offset` 이다. 서버가
파일을 못 쓰면 503 `log_write_failed` 를 돌려주고 워커는 다시 시도한다.

서버가 재시작하면 기동 정리에서 busy 원격 잡 가운데 `log_file_bytes` 가 있는 잡을 본다. 그 잡의 `log.txt` 가 더 길면 그
길이로 자른다. 덧붙였지만 커밋 전에 서버가 죽은 조각이고 다음 재전송이 다시 채운다. 같은 단위끼리만 비교한다(3판 r1 A-M1
의 단위 섞임을 고쳤다).

워커의 `acked` 는 2xx 응답의 `offset` 으로만 오른다. 그래서 워커 `acked ≤ 서버 log_offset` 이 늘 성립하고 빈틈은 버그일
때만 생긴다.

워커는 응답마다 아래처럼 처리한다(3판 r2 A-M5 · r1 A-H5).

| 응답 | phase | log | 산출물 | finish | heartbeat · claim |
|---|---|---|---|---|---|
| 2xx | 지움 | `acked = offset` | 다음 단계 | 큐 끝(`done`), 레인 풀림 | — |
| 409 `log_gap` | — | `expected_offset` 부터 다시(프로세스 안 죽임) | — | — | — |
| 409 `job_closed` | 지움 | 전송만 멈춤(스풀 남김). 잡 처리는 W4 | 건너뜀 | 지움(`late_result` 여부는 서버) | — |
| 409 `session_fenced` | W5 | W5 | W5 | W5(좁은 확인만 보냄) | W5 |
| 409 `not_registered` | 같은 S 로 재등록 뒤 다시 | 같음 | 같음 | 같음 | 같은 S 로 재등록(T10) |
| 409 `protocol_required` | W6 | W6 | W6 | W6 | W6 |
| `code` 없는 409(옛 서버 문구) | 지움 | 전송 멈춤, 프로세스 안 죽임 | 건너뜀 | 지움 | 오늘대로 |
| 400 · 404 · 413 | 지우고 로그 | 전송 멈춤 + 그 잡 `log_bytes` 가 `acked` 보다 크게 남음 | 처분 `failed`(`upload_failed`) | 지우고 로그 | 오늘대로 |
| 401 · 403 | 지우고 로그 | 같음 | 같음 | 같음 | 오늘대로 |
| 503 · 5xx · 연결 실패 · 시간 초과 | 백오프 재시도 | 같음 | 같음 | 같음 | 오늘대로 |

백오프는 1초에서 두 배씩 30초까지 늘고 워커당 하나다. 잡이 도는 동안은 횟수와 시간 상한이 없다. 3판의 「연속 실패 20번」은
버렸다(3판 r3 A-H6 · R1). 잡이 끝난 뒤에는 `RETRANSMIT_SECONDS`(24시간, 결정 264)가 지나면 로그와 산출물을 포기하고 finish 를
`log_bytes` 와 함께 보낸다. finish 마저 24시간 안에 못 보내면 큐 항목을 버리고 로그를 한 줄 남긴다.

레인은 자기 잡의 큐가 끝날 때까지 다음 claim 을 하지 않는다. 서버는 finish 전까지 그 레인을 busy 로 본다(F10). heartbeat 의
`jobs` 목록에는 큐가 끝나지 않은 잡이 남는다. 「잊힌 잡」 규칙에 걸리지 않게 하려는 것이다. 오늘 `_finish` 가 「보고 뒤에
뺀다」(`remote_worker.py:1059-1062`)와 같은 이유다.

오늘의 메모리 버퍼(`LOG_KEEP_BYTES`), `_unreported`, `REPORT_RETRIES` 경로는 이 큐로 바뀐다(F32 · F33).

`\r` 진행 표시가 줄 끝 없이 이어지면 러너가 64 KiB 마다 끊어 보낸다. 그래서 실시간 tail 에는 그만큼 늦게 보인다. 이것은 문서에
적는다(RL17 c).

거절한 대안은 넷이다.

- 3판의 `log.pending`. F34 때문에 필요 없다.
- 조각마다 줄 끝 없는 꼬리를 서버 메모리에 두기. 재시작하면 사라진다.
- 워커 로컬 `log.txt` 를 재전송 원본으로 쓰기. 그 파일은 서버로 가는 스트림과 다르다(F36).
- 쿼리 문자열 `?offset=`. 헤더 관례가 이미 있고 헤더를 쓰면 경로 정규식 `_WORKER_JOB_RE`(`server.py:263`)를 건드리지 않는다.

### 4.6 finish · 늦은 결과 · 로그 완전성 · 산출물 (결정 256 · 259 · 264 · 273~275 · 277 · 278)

finish 본문에 `worker_session_id` 와 `log_bytes` 를 더한다. `log_bytes` 는 그 잡이 낸 스트림 전체의 바이트이고 스풀이
깨졌으면 센 값이다. `summary_code` 는 오늘의 `PREFLIGHT_CODES` 이거나 새 `LOST_CODES` 다. `LOST_CODES` 는 `outcome = lost` 일
때만 받는다. 인자는 코드마다 정한 키만 받는다. 오늘 `_preflight_summary` 와 같은 엄격함이다.

`LOST_CODES` 는 셋이다. `worker_self_stopped` 는 인자 `seconds` 를 받는다. `worker_replaced` 는 인자 `name` 을 받고 서버가 토큰
이름으로 채운다. `snapshot_unreachable` 은 인자가 없다.

서버는 finish 를 받을 때 `log_complete = (log_offset ≥ log_bytes)` 를 잡 행에 적는다. `log_bytes` 가 없는 레거시 finish 면 NULL
이다. 로컬 레인 잡은 서버가 직접 쓰므로 NULL 이 아닌 참이다. 그렇게 하지 않으면 로컬 잡 전부에 「모름」이 붙는다. `log_complete`
(참, 거짓, null)는 `recent_json`, 잡 문서, `rcm wait` JSON 에 싣는다. 거짓이면 `rcm wait` 사람용 줄, `rcm logs` 끝, 웹 로그
보기에 「로그가 불완전함」을 보인다(PR3).

늦은 결과(결정 256 · 274)는 잡이 `lost` 이고 요청한 세션이 잡의 세션이며 아직 기록이 없을 때 남긴다. events 에 kind
`late_result` 한 건을 `{outcome, exit_code, finished_at, received_at, log_complete}` 로 적는다. `finished_at` 은 워커 값에 아래
자르기를 적용한 것이다. 응답은 409 `job_closed` 에 `state: lost` 와 `late_result_recorded: true` 를 싣는다. 잡의 상태, 요약,
`finished_at`, 로그, 마커, 산출물, 알림, `job_finished` 는 모두 그대로 둔다. `GET /jobs/{id}` 의 종료 잡 문서에 `late_result`
(객체 또는 null)를 싣고 `rcm wait` JSON 도 같은 키를 싣는다. wait JSON 은 잡 문서의 복사다(`PLAN.md:257`).

펜싱된 세션의 늦은 finish 는 기록하지 않는다(RL11 · 결정 255). 2차 위임 결정 Q-B 가 이렇게 정했다. 서버는 409
`session_fenced` 를 돌려주고 아무것도 남기지 않는다. 펜싱된 세션은 권한을 잃었고 새 세션과 겹쳐 돌았을 수 있다. RL11 의
「반영 0 바이트」도 그대로 지킨다.

같은 세션의 finish 가 두 번 오는 경우(RL21 a)에 잡이 첫 finish 로 이미 끝났다면 둘째는 409 `job_closed` 이고 기록은 없다.
워커는 그 항목을 큐에서 지운다.

워커가 보낸 `finished_at` 이 `[started_at, 수신 시각]` 밖이면 가까운 끝으로 자른다(결정 277). 그래서 `job_seconds` 가 음수가
되지 않는다(RL27 b).

준비 단계(결정 259)에서 스냅샷 내려받기가 status 0 이나 503 으로 실패하면 `lost` 와 `snapshot_unreachable` 이다. 404 와
409(스냅샷 없음, 조립 실패), 저장소 없음, 깨진 tar 는 오늘처럼 `failed` 다. git_ref fetch 실패는 rcm 서버와의 끊김이 아니므로
`failed` 그대로다.

산출물(결정 275, RL22) 업로드는 보고 큐에서 finish 앞 단계다. 연결 실패와 503 이면 재전송 창 안에서 다시 올린다. 같은 해시를
다시 올리면 서버가 200 을 준다(F17). 묶음 스풀은 지금도 지우지 않는다(`remote_worker.py:1025-1027`). 처분은 finish 를 보내기
직전에 정한다. 서버는 처분 `ready` 를 그 잡의 묶음 행이 있고 해시가 같을 때만 받는다. 행이 없으면 `failed` 와 `upload_failed`
로, 해시가 다르면 `failed` 와 `hash_mismatch` 로 적는다. 두 코드는 이미 `REASONS` 에 있다(`core/artifacts.py:76-94`). 잡 결과는
오늘처럼 산출물과 무관하다.

### 4.7 프로토콜 판과 호환 (결정 258 · 262 · 270)

워커 프로토콜은 `worker_protocol = 2` 다. 1 은 이 명세 이전, 곧 세션이 없는 프로토콜이다. `/worker/spec` 응답과 register 응답에
`worker_protocol: 2` 를 싣는다.

새 워커는 등록 전에 프로토콜을 확인한다(T13). `GET /worker/spec` 의 `worker_protocol` 이 2 이상이면 진행한다. 키가 없거나 404
면 `GET /api/health` 의 `version` 을 본다. 자기 버전과 다르면 오늘의 등록 길로 간다. 서버의 버전 409 는 어떤 부작용보다
먼저이므로(F5 · `remote_workers.py:390-398`) 워커는 따라가기(M5q)를 하거나 종료 2 로 끝난다. 버전이 같으면 「서버가 워커
프로토콜 1 — 서버를 올려라」 한 줄을 내고 종료 3 으로 끝난다. 이때 register 와 claim 은 0건이다(RL25 · RL32).

종료 코드(결정 258)는 펜싱이 0, 프로토콜 불일치가 3 이다. 나머지는 오늘대로 0 이 멈춤, 2 가 등록 막힘, 1 이 exec 실패다.
launchd `SuccessfulExit=false` 와 systemd `Restart=on-failure` 는 0 을 되살리지 않고 3 을 되살린다(F39). 서비스 파일은 바꾸지
않는다.

M5t-c 도 워커 종료 3 을 쓴다. 2차 위임 결정에 따라 종료 3 은 두 경우를 뜻한다. 하나는 이 명세의 프로토콜 불일치이고 다른
하나는 M5t-c 의 서버 신원 미확인이다. 두 경우 모두 워커는 일을 시작하지 않고 감독자가 다시 시도한다.

옛 워커(프로토콜 1)가 새 서버에 붙으면(결정 262) 서버는 레거시 busy 잡이 있는 이름의 세션 없는 heartbeat, phase, log, finish,
artifacts 만 받는다. log 는 오프셋 없이 오늘처럼 덧붙인다. claim, register, capabilities 는 409 `protocol_required` 다. 옛
워커가 어떻게 반응하는지는 F40 에 있다.

펜싱 핑퐁은 생기지 않는다(RL10). 펜싱된 프로세스는 종료 0 으로 끝나 감독자가 되살리지 않는다. 펜싱된 세션의 재등록은 409
`session_fenced` 다(§4.2).

`rcm worker --check` 는 세션을 만들지 않는다(F46). `--once` 는 보통 워커와 같다.

### 4.8 요청자에게 보이는 것 (결정 276 · 278 · 273 · 274)

유예 중(T2)인 도는 잡의 사유는 새 `reason` 값 `worker_unreachable_grace` 다. 값을 더하기만 하므로 `schema_version` 은 1
그대로다(`PLAN.md:140, 450`). `compute_queue` 가 그 잡의 `worker_name` 이 down 레인의 워커일 때 이 값을 정한다.
`_busy_reason` 순서에서 `worker_unreachable_grace` 를 맨 앞에 둔다. 잡이 취소 중이어도 그렇다. 취소가 워커에 전달되지 못한다는
사실이 더 행동할 만하기 때문이다. `estimate.finish_at` 은 null 이다.

`ACTIONABLE_REASONS` 와 웹 `ACTIONABLE` 에는 `worker_down` 바로 뒤에 더한다(F29). 레인 `down` 과 헬스 `workers_down` · 503 은
그대로 둔다(F22). 3판 r1 의 새 레인 상태 `unreachable` 은 만들지 않는다(3판 r2 A-M1).

웹, `rcm top`, `rcm wait` stderr 의 사람용 문구는 「워커와 연락이 끊김 — 유예 안에 돌아오면 이어 감」 같은 뜻이다. 원문 코드는
보이지 않는다. 문구는 PR3 에서 korean-writer 와 i18n 으로 확정한다.

`lost` 로 끝나는 네 경우(RL29)는 `summary_code` 로 구별한다. 유예 만료는 오늘 코드 `worker_unreachable`, 자기 멈춤은
`worker_self_stopped`, 펜싱은 `worker_replaced`, 재시작 이어받기는 오늘 코드 `worker_restarted_without_job` 이다. 넷 다 종료
3 이고 `failed_step` 과 `last_step` 이 없다(`PLAN.md:220`). 번역은 PR1 이 더한다(F28).

`late_result` 는 JSON 과 `rcm wait` · `rcm status` 의 사람용 줄 한 줄로 보인다. 문구는 「늦게 온 결과: succeeded — 이 잡의
결과로 세지 않음」 같은 뜻이다. 웹은 그리지 않는다(§1.3). `log_complete = false` 는 `rcm wait` 사람용 줄, `rcm logs` stderr
끝, 웹 로그 보기에 한 줄로 보인다.

### 4.9 DB v26 (결정 265 · 271 · 273, PR1 이 올린다)

| 표 | 칸 | 뜻 | 쓰는 PR |
|---|---|---|---|
| `worker_sessions`(새) | `id TEXT PRIMARY KEY` | 세션 id | PR1 |
| | `worker_name TEXT NOT NULL` | 토큰 이름 | PR1 |
| | `registered_at REAL NOT NULL` | 첫 등록 | PR1 |
| | `fenced_at REAL` | 펜싱된 시각(NULL = 현재) | PR1 |
| | `fenced_by TEXT` | 펜싱한 세션 id | PR1 |
| | 색인 `(worker_name, fenced_at)` | 현재 세션 찾기 | PR1 |
| `jobs` | `worker_session_id TEXT` | claim 한 세션(NULL = 로컬 또는 레거시) | PR1 |
| `jobs` | `irreversible INTEGER` | claim 때 얼린 표식(NULL = 로컬 · 레거시) | PR1 |
| `jobs` | `disconnect_grace_seconds INTEGER` | claim 때 얼린 유예 | PR1 |
| `jobs` | `log_offset INTEGER` | 받은 원본 스트림 바이트(오프셋 기준) | PR2 가 씀 |
| `jobs` | `log_file_bytes INTEGER` | 가린 뒤 `log.txt` 길이 | PR2 가 씀 |
| `jobs` | `log_complete INTEGER` | 1/0/NULL | PR2 가 씀 |
| `events` | kind `late_result`(스키마 변경 없음) | 늦은 결과 | PR2 |

마이그레이션은 `_MIGRATIONS[26]` 에 위 DDL 을 둔다. 옛 행은 전부 NULL 이고 이것은 로컬 잡이거나 레거시 잡이라는 뜻이다(T11).
백업은 기존 경계를 따른다(F50). 옛 빌드는 v26 DB 를 거절한다(F50). M5t-c 는 v27 이다(§9).

3판에서 뺀 것도 적어 둔다. `log_raw_acked` 는 이름을 `log_offset` 으로 바꿨다. `log_bytes_total` 은 finish 본문의 `log_bytes`
로만 받고 저장은 `log_complete` 판정 하나로 끝낸다. `log_incomplete` 는 긍정형 `log_complete` 와 「모름」을 뜻하는 null 로
바꿨다. `lease_id`, `worker_identity`(M5t-c 몫), `workers` 의 세션 칸은 두지 않는다.

### 4.10 얼어붙는 계약

명세 PR 의 머지가 곧 동결이다(workplan 스킬 §6). 아래가 얼어붙는다.

| 계약 | 바뀌는 것 |
|---|---|
| `schema_version`(`/api/status`) | 1 그대로. 더하기만: 도는 행 `reason` 값 `worker_unreachable_grace`, 최근 행 · 잡 문서 `log_complete`, 종료 잡 문서 · wait JSON `late_result` |
| DB | v25 → v26(§4.9) |
| 워커 프로토콜(아래 표) | 2 |
| 종료 코드 | `rcm worker`: 0 에 「펜싱됨」, 새 3 「프로토콜 불일치」. `rcm wait` 0~5 그대로 |
| CLI 플래그 | 없음 |
| 설정 키 | `[server] disconnect_grace_seconds`(정수, 기본 600, `worker_timeout_seconds < 값 ≤ 86400`, 위반은 기동 실패 종료 2 + 키 이름 문장, `[server]` 라 reload 불가 F20). 프리셋 `irreversible`(bool, 기본 false, reload 가능, claim 때 얼림) |
| 헬스 기능 키 | 없음 |
| 요약 코드 | `worker_self_stopped` · `worker_replaced` · `snapshot_unreachable`(새) |

워커 프로토콜 2 는 아래와 같다.

| 라우트 | 요청에 더함 | 응답에 더함 | 새 오류(`code`) |
|---|---|---|---|
| `POST /worker/register` | `worker_session_id`(필수, 32 hex), `worker_protocol`(2), `previous_sessions`(세션 id 목록, 없으면 []) | `worker_protocol: 2` | 409 `protocol_required`(+`worker_protocol: 2`), 409 `session_fenced` |
| `GET /worker/spec` | — | `worker_protocol: 2` | — |
| `POST /worker/claim` | `worker_session_id` | `job.irreversible`, `job.disconnect_grace_seconds` | 409 `protocol_required` · `session_fenced` · `not_registered`(오늘 문구 유지 + `code`). 펜싱된 세션의 잡이 그 레인을 잡고 있으면 204 |
| `POST /worker/heartbeat` | `worker_session_id`(레거시 busy 잡이 있는 이름은 없어도 받음) | — | 409 `protocol_required` · `session_fenced` · `not_registered` |
| `POST /worker/capabilities` | `worker_session_id` | — | 같음 |
| `POST /worker/jobs/{id}/phase` | `worker_session_id` | — | 409 `session_fenced` · `job_closed`(+`state`) |
| `POST /worker/jobs/{id}/log` | 헤더 `X-RCM-Worker-Session`, `X-RCM-Log-Offset` | `offset` | 409 `log_gap`(+`expected_offset`) · `job_closed` · `session_fenced`, 503 `log_write_failed` |
| `GET /worker/jobs/{id}/tree` · `PUT`/`GET …/artifacts` | 헤더 `X-RCM-Worker-Session` | — | 409 `session_fenced` · `job_closed` |
| `POST /worker/jobs/{id}/finish` | `worker_session_id`, `log_bytes`, `summary_code ∈ LOST_CODES`(outcome lost) | — | 409 `job_closed`(+`state`, `late_result_recorded`) · `session_fenced` |

모든 409 는 오늘의 `error` 문구를 남기고 `code` 를 더한다. 옛 워커가 문구로 맞추는 `not registered` 도 그대로 둔다
(`remote_worker.py:773`).

### 4.11 기존 결정과의 관계

| 기존 | 이 명세 |
|---|---|
| PLAN 「잡 모델」 `lost(서버 재시작 중 죽음)`(`PLAN.md:89`)·「잡은 서버 재시작을 넘어 살아남는다」(`PLAN.md:121`)·저장소 시작 정리(`PLAN.md:293`) | 원격 잡의 `lost` 는 끊김 유예 만료 · 자기 멈춤 · 펜싱 · 재시작 이어받기로 생긴다고 PR0 이 PLAN 문장을 더한다 |
| PLAN M5b-2 「timeout → down · 잡 lost · 재등록 = 옛 잡 lost」(`PLAN.md:600`) | 바꾼다: timeout → down 만, 잡은 유예, 재등록은 명시한 이전 세션만. PR0 이 그 줄에 「M5t-b 가 바꿈」 을 단다 |
| `rcm wait` 3 = 모름(`PLAN.md:118`) | 그대로. 네 끝 모두 3 |
| `cancelled`·`lost` 에 스텝 없음(결정 64, `PLAN.md:220`) | 그대로 |
| M5n §13 자동 재실행 안 함(`docs/m5n-workplan.md:652`) | 그대로. `late_result` 도 다시 돌리지 않는다 |
| M5q 드레인 「도는 잡은 하나도 잃지 않는다」(`remote_worker.py:10`) | 강화: 보고 큐가 exec 를 넘는다 |
| 결정 254 `local_only` | 별개 키(결정 257). 둘 다 달 수 있고 `local_only` 면 `irreversible` 은 효과 없음 |
| M5e 산출물 「결과와 무관」 | 그대로. `ready` 조건만 좁힘(결정 275) |

## 5. 작업 분할과 병렬

PR 하나에 브랜치 하나, 워크트리 하나다. 같은 물결의 PR 은 동시에 만든다. 공유 파일(shared)은 더하기만 한다. 뒤에 머지하는
PR 이 dev 를 합친 뒤 맞춘다.

| PR | 브랜치 | 내용 | 시작 조건 | 물결 | 같이 도는 것 | owns | shared | 겹치는 파일과 머지 순서 | lane |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5tb-result-loop-workplan` | 이 명세 + PLAN.md(v2.14 머리, `PLAN.md:89 · 121 · 293 · 600` 문장, M5t-b 항목 `PLAN.md:628`, 결정 색인 256~279) | — | 0 | M5t-c 명세 | `docs/m5tb-workplan.md` | `PLAN.md`(M5t-c 명세 PR 과 다른 줄) | M5t-c 명세와 PLAN 머리 · 색인 줄이 겹침 → 먼저 머지한 쪽 뒤에 다시 맞춤 | short(문서만) |
| PR1 | `feat/worker-session-fencing-grace` | §4.1~§4.4 · §4.7: DB v26 전부, 세션 · 펜싱 · 좁은 확인, 서버 유예(단조) · 취소 미확인 범위, 얼림, 자기 멈춤, 재시작 이어받기 · 고아 정리, 프로토콜 2 · 등록 전 확인 · 종료 0/3, 레거시 호환, `LOST_CODES` 와 그 번역, 설정 키 둘, 문서(`docs/configuration.md` 1185 · 1386 근처, `docs/operating.md:786`, 템플릿 둘), 오늘 계약 시험 고쳐 쓰기(F47). 로그는 오늘처럼 덧붙이되 세션 헤더만 | PR0 | 1 | — | `store.py` `remote_workers.py` `server.py` `capabilities.py` `config.py` `core/model.py` `core/outcome.py` `client.py` `remote_worker.py` `runner.py` `cli.py`(cmd_worker) `web/i18n.js`(outcome 항목) `templates/server.toml` `examples/server.toml` `docs/configuration.md` `docs/operating.md` `tests/tcp_relay.py`(새) `tests/test_worker_session_m5tb.py`(새) `tests/test_e2e_worker_grace_m5tb.py`(새) `tests/test_worker_lost.py` `tests/test_e2e_worker.py` + 세션을 싣게 고치는 워커 시험 도우미 | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py`(새, PR1 이 만들고 뒤 PR 이 더함) `tests/test_examples.py` | M5t-a PR2(#268)와 `remote_workers.py`(register) · `capabilities.py`(`/worker/spec`) · `config.py` · `cli.py` 가 겹친다 → 먼저 머지된 쪽 뒤에 dev 를 합쳐 맞춤. M5t-c 의 register · DB v27 은 PR1 뒤 | full |
| PR2 | `feat/worker-log-offset-outbox` | §4.5 · §4.6: 스트림 스풀 · 보내는 스레드 · 순서 있는 큐 · 응답표 · 재전송 창, 서버 오프셋 · 파일 길이 · 기동 자르기 · 503 `log_write_failed`, `log_complete` · `late_result` · `finished_at` 자르기 · 산출물 재업로드와 `ready` 조건, 준비 단계 `snapshot_unreachable`, 문서(`docs/configuration.md` 의 원격 워커 로그 절) | PR1 머지 | 2 | PR3 | `store.py` `remote_workers.py` `remote_worker.py` `client.py` `server.py`(log 라우트 · `_terminal_view` · 기동 자르기) `core/status.py`(`recent_json`) `docs/configuration.md` `tests/test_worker_log_offset_m5tb.py`(새) `tests/test_e2e_worker_outbox_m5tb.py`(새) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py` | PR3 과 src 파일 안 겹침. PR2 먼저 머지 | full |
| PR3 | `feat/queue-show-unreachable-worker` | §4.8: 사유 `worker_unreachable_grace` · ETA null · `ACTIONABLE` 두 목록, 문구(웹 · `rcm top` · `rcm wait`), `log_complete`=false 와 `late_result` 의 사람용 줄(`rcm wait` · `rcm status` · `rcm logs`) · 웹 로그 보기 줄, 문서(`docs/operating.md` 의 「워커와 연락이 끊겼을 때 화면」) | PR1 머지 | 2 | PR2 | `core/model.py`(사유 상수 · `ACTIONABLE_REASONS`) `core/queue.py` `core/render_text.py` `cli.py`(wait · logs 사람용 줄) `web/app.js` `web/i18n.js`(reason · 로그 불완전 문구) `docs/operating.md` `tests/test_queue_unreachable_m5tb.py`(새) `tests/web/unreachable_m5tb.test.js`(새) `tests/test_action_marker_m5s.py`(목록 잠금 갱신) | `CHANGELOG.md` `scripts/mutcheck.py` `tests/test_docs_m5tb.py` | PR2 뒤 머지(RL28 · RL29 의 e2e 가 PR2 키를 읽는다. 빌드는 병렬, 머지 전 dev 합쳐 다시 돌림) | full(web UI) |
| PR4 | `test/acceptance-worker-result-loop-live` | 실기 수용: 하네스 `scripts/acceptance/m5tb_live.sh`(시험 서버 · 가짜 `sh -c` 표식 프리셋 · 표준 라이브러리 TCP 중계), §7 `(live)` 행과 e2e 행의 실기 확인, 보고서 `docs/acceptance/reports/<날짜>-m5tb-acceptance.md` | PR3 머지 | 3 | — | `scripts/acceptance/m5tb_live.sh`(새) `docs/acceptance/reports/…`(새) | `CHANGELOG.md` | — | short(src 변경 없음) |

PR1 을 더 쪼개지 않는다. dev 의 e2e 는 같은 트리의 서버와 워커를 함께 띄운다. 서버만 세션을 요구하면 워커 쪽이 없는 동안
e2e 가 빨갛다. 거꾸로 하면 선택을 필수로 뒤집는 셋째 PR 이 필요하다. 이 대안은 거절했다.

dev-batch 로 묶는 것은 물결 2 의 PR2 와 PR3 이다. `tools/dev_batch.py` 로 owns 가 겹치지 않는지 확인한다. 공유 파일 셋은
더하기만 한다.

M5t-c 의 register 변경과 DB v27 은 PR1 뒤에 온다(위 표). 2차 위임 결정은 M5t-c 가 등록에 넣는 `pair_proof` 검사를 세션
넣기, 펜싱, 능력 보고 지우기, 잡 닫기보다 먼저 두도록 정했다(§4.2). 워커 종료 3 의 두 뜻은 §4.7 에 있다.

## 6. 진행 체크리스트

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 이 명세 작성(1판) | PR0 | — | [ ] |
| 1 | 격리 검증 라운드(HIGH 0 까지), §12 기록 | PR0 | 0 | [ ] |
| 2 | 결정 페이지: 레인(결정 279) · 위임 질문(메인 세션 + Codex, 오너 위임) | — | 1 | [ ] |
| 3 | PR0 머지(PLAN.md 반영 포함) | PR0 | 1 · 2 | [ ] |
| 4 | PR1 세션 · 펜싱 · 유예 · 자기 멈춤 · 재시작 · 프로토콜 · DB v26 | PR1 | 3 | [ ] |
| 5 | M5t-a PR2(#268)와 겹친 파일 맞추기(먼저 머지된 쪽 뒤) | PR1 | 4 | [ ] |
| 6 | PR2 로그 오프셋 · 보고 큐 · 늦은 결과 · 산출물 | PR2 | 4 | [ ] |
| 7 | PR3 화면 사유 · 사람용 줄 | PR3 | 4 | [ ] |
| 8 | PR2 머지 → PR3 dev 합쳐 RL28 · RL29 다시 돌린 뒤 머지 | PR2 · PR3 | 6 · 7 | [ ] |
| 9 | 새 변이 11개(§7.4)가 `scripts/mutcheck.py` 에 들어갔는지 | PR1 · PR2 · PR3 | 4 · 6 · 7 | [ ] |
| 10 | PR4 실기 수용과 보고서 | PR4 | 8 | [ ] |
| 11 | M5t-c 명세의 DB v27 이 v26 위인지 확인 | — | 4 | [ ] |
| 12 | CHANGELOG `[Unreleased]` 정리 | PR4 | 10 | [ ] |
| 13 | 앱 프로젝트에 `irreversible` 달기 — 그 프리셋을 읽는 서버가 모두 이 기능 버전 이상인지 확인한 뒤(F23) | — | 운영 반영 | [ ] |

## 7. 시험 계획

### 7.1 시나리오 표

아래 용어는 표에서만 쓴다. G 는 `disconnect_grace_seconds` 다. 시험 서버는 이 값을 `worker_timeout_seconds` 보다 조금 큰 값으로
줄여 쓴다. 표식 프리셋은 `irreversible = true` 인 프리셋이고 일반 프리셋은 표식이 없는 프리셋이다. 분단은 서버가 살아 있는데
워커 요청에만 응답이 없는 상태이고 시험에서는 TCP 중계를 멈춰 만든다. 서버 정지는 연결 거부다. 세션은 워커 프로세스
한 번이다.

<!-- scenario-prefix: RL -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| RL1 | [전이 T2·T3·W3 · 253] 표식 프리셋(가짜 `sh -c sleep`)의 잡이 원격 워커에서 `running`. 분단이 G 를 넘겨 이어진다 | 워커가 `G − m` 에 잡의 프로세스 그룹을 끝낸다(pid 소멸). 서버가 `lost` 로 닫기 전에 이미 죽어 있다(워커 로그의 멈춤 시각 ≤ DB `finished_at`). 연결이 돌아온 뒤 최종 상태는 `lost` 하나, `rcm wait` 종료 3, `failed_step` · `last_step` 없음, `summary_code` 는 `worker_unreachable`(서버가 먼저) 또는 `worker_self_stopped`(보고가 먼저). 근거: 결정 253 · `PLAN.md:118, 220` | e2e: 워커 프로세스 + TCP 중계 정지, `os.kill(pid,0)`, DB `jobs`, 종료 코드 | PR1 |
| RL2 | [전이 T3·W3 · 253] RL1 과 같은 잡. 이번에는 서버 정지(재배포)가 G 를 넘겨 이어진 뒤 서버가 다시 뜬다 | 워커는 서버 정지도 끊김으로 보고 `G − m` 에 스스로 멈춘다(서버가 꺼진 채로 pid 소멸). 서버가 뜬 뒤 그 잡은 `lost` 한 번, 종료 3. 서버가 꺼져 있던 동안은 유예로 세지 않으므로 기동 직후 틱이 닫지 않고, 워커가 돌아와 보낸 `worker_self_stopped` 가 닫거나 기동 + G 에 `worker_unreachable` 로 닫힌다. 같은 잡을 두 번 닫거나 사유를 덮어쓰지 않는다. 근거: 결정 253 · 266 | e2e: 서버 stop/start, pid, DB, 종료 코드 | PR1 |
| RL3 | [전이 T2·T3 · 253] RL1 의 분단 중(G 안과 밖 각각) 같은 `concurrency_group` 으로 새 제출을 낸다(supersede 켜진 경우 포함) | 첫 잡이 `lost` 로 닫히기 전에는 둘째 잡이 `running` 이 되지 않는다(`queued`, `blocked_by_group`). supersede 면 첫 잡은 `cancelling` 으로 남아 그룹을 잡는다. 둘째가 시작하는 시각에 첫 잡의 프로세스는 이미 없다. 두 프로세스가 동시에 살아 있던 구간 0. 근거: 결정 253 · `PLAN.md:127` | e2e: 두 잡의 pid 생존 구간 비교, `jobs.state` | PR1 |
| RL4 | [전이 T2/T3 경계 · 253] G 만료 경계(주입 시계로 ±1 틱, 여러 번 반복)에서 분단이 풀려 서버 janitor 의 만료 판정과 워커의 첫 heartbeat · 자기 멈춤이 겹친다. 표식 · 일반 각각 | 잡의 최종 상태가 정확히 하나다. 표식은 (결과 확정 또는 `lost`, 프로세스 종료), 일반은 (결과 확정 또는 `lost`, 프로세스는 계속). `lost` 가 된 뒤 같은 잡이 `succeeded` 등으로 뒤집히지 않는다(늦은 finish 는 `late_result`). 표식 프리셋에서 (`lost`, 프로세스 생존) 조합이 경계 어느 쪽에서도 나오지 않는다. 근거: 결정 253 · 256 · 266 · `PLAN.md:118` | 단위: 서버 `mono_fn` · 워커 시계 주입, 경계 반복, DB + pid 상태 | PR1 |
| RL5 | [전이 T3·W4 · 253 · 256] 일반 프리셋의 원격 잡이 `running`, 연결이 G 를 넘겨 끊긴다 | 서버는 G 에 `lost`(`worker_unreachable`), `rcm wait` 종료 3(1 아님). 워커는 잡을 죽이지 않고 계속 돌린다(pid 생존, 워커 로컬 `log.txt` 증가). 재연결 뒤 로그 조각은 409 `job_closed` 이고 프로세스를 죽이지 않는다. 끝나면 finish 를 보내고 서버는 잡 상태를 바꾸지 않은 채 `late_result`(outcome · exit_code · finished_at · received_at · log_complete)를 잡 문서에 한 번 남긴다. 근거: 결정 253 · 256 | e2e: pid 생존, 워커 로컬 로그, DB, 종료 코드, `GET /jobs/{id}` 의 `late_result` | PR1 (유예 · 계속 돎) · PR2 (`late_result`) |
| RL6 | [전이 T1·T2 · 기준선] (a) 끊김 없는 원격 잡의 성공 · 실패 · 시간초과 · 취소 각 한 번 (b) 분단을 `worker_timeout_seconds` 보다 길고 G 보다 짧게 둔 뒤 푼다(표식 · 일반 둘 다) | (a) 종료 코드 계약 그대로: 성공 0 · 실패 1 · 시간초과 2 · 취소 2, wait JSON 의 기존 키 불변(더한 키만 있음). (b) 잡은 끊긴 내내 `running` 이고 `worker_timeout_seconds` 에 `lost` 가 되지 않는다. 같은 세션으로 이어 보고하고 프로세스는 안 죽는다. 서버 `jobs/N/log.txt` 의 sha256 이 워커 스트림 스풀과 같다(중복 · 빈틈 없음). 근거: 결정 253 · `PLAN.md:118` | e2e: TCP 중계 정지/재개, sha256, 종료 코드, wait JSON | PR1 (상태) · PR2 (로그 sha) |
| RL7 | [서버 정지 T1·T3] 원격 잡과 로컬 레인 잡이 같이 `running`. 서버를 (a) `worker_timeout_seconds` 초과 G 미만 (b) G 초과로 정지했다가 올린다. 그동안 `rcm wait` 하나가 대기 중 | (a) 재기동 후 첫 janitor 틱(5초)과 그 뒤에도 원격 잡은 `running` 이고 워커의 finish 가 확정되어 종료 코드가 실제 결과다. (b) 유예는 서버 기동 시각부터 다시 센다. 기동 직후 틱에서 원격 잡이 한꺼번에 `lost` 가 되지 않고, 워커가 끝내 안 돌아오면 기동 + G 에 `lost`. 로컬 레인 잡만 `lost`(`server restarted`)인 것은 오늘과 같다. 정지 중이던 `rcm wait` 은 오늘 계약대로 연결 실패 60초 뒤 종료 3이고, 복귀 뒤 `rcm wait --job N` 은 실제 결과 코드를 낸다. 근거: `store.py:3878-3909` · `PLAN.md:121, 457` · 결정 266 | e2e: 서버 stop/start(`tests/test_e2e_worker.py:857` 확장) + 단위: `mono_fn` 기동 바닥, DB | PR1 |
| RL8 | [취소 T4 · 유예] 분단 중 요청자가 `rcm cancel N`(또는 같은 그룹 제출로 supersede)을 한다. (a) G 안에 연결 복귀 (b) 복귀 없이 G 경과. 표식 · 일반 각각 | 요청은 접수되어 `cancelling`. (a) 첫 heartbeat 응답의 `cancel` 로 프로세스가 끝나고 `cancelled`(종료 2, supersede 면 `superseded by #N` 유지). (b) `lost`(`worker_unreachable`, 종료 3)이고 `cancelled`(2)나 `cancel_unconfirmed` 로 위장하지 않는다. `kill_at + 2 × heartbeat` 규칙은 조용한 세션에는 걸리지 않는다. 표식 프리셋은 G 전에 워커가 스스로 멈춘다. 일반 프리셋을 취소했는데 워커가 못 들은 채 계속 돌면 서버는 G 에 `lost`, 워커의 늦은 finish 는 `late_result`. 근거: `tests/test_worker_lost.py:137` · 결정 253 · 256 | e2e + 단위: DB `state` · `cancel`, 종료 코드, pid | PR1 |
| RL9 | [전이 T5 · claim] 서버가 claim 을 확정했는데 응답이 유실된다(분단 · 워커 크래시 직전). 워커는 그 잡을 모른다 | 프로세스가 뜨지 않는다(pid 없음). 같은 세션이 재연결하면 두 번째 heartbeat 에 그 잡을 `lost`(`worker_restarted_without_job`, 종료 3)로 닫고 레인을 비운다(G 가 길어도 같다). 연결이 끝내 안 돌아오면 G 뒤 `lost`. 프로세스가 죽고 다시 시작했으면 이어받기 등록이 닫는다. 자동 재실행 없음. 근거: `remote_workers.py:916-942` · CHANGELOG.md:580-584 · `docs/m5n-workplan.md:652` | 단위: claim 직후 응답 유실 흉내 + heartbeat 두 번, DB | PR1 |
| RL10 | [펜싱 T8·W5 · 255 · 258 · 261] 프로세스 A(토큰 w1)가 잡 X 를 도는 중, 같은 토큰으로 프로세스 B 를 띄운다(A 는 서비스, B 는 수동). X 는 표식 · 일반 각각 | B 의 등록은 성공하고 B 가 그 이름의 유일한 현재 세션이다. A 의 다음 요청(heartbeat · log · phase · finish 어느 것이든)은 409 `code: session_fenced`(잡이 닫혔다는 `job_closed` 와 구별). A 는 X 의 프로세스 그룹을 SIGTERM→KILL 로 멈추고 좁은 확인 finish 를 보낸 뒤 종료 0. X 는 `lost`(`worker_replaced`, 종료 3)이고 A 가 `failed` · `cancelled` 로 보고하지 않는다. 확인 전까지 X 는 busy 라 그룹 자리가 안 풀리고 B 의 그 레인 claim 은 204(409 아님). 감독자(launchd `KeepAlive.SuccessfulExit=false`, systemd `Restart=on-failure`)가 A 를 되살려 B 를 되펜싱하는 핑퐁이 없다(1분 안 `register` 호출 ≤ 2). 확인이 없으면(A 분단) X 는 A 의 마지막 들음 + G 에 `lost`. 근거: 결정 255 · 258 · 261 · `cli.py:3507, 3535` | e2e: 두 프로세스 + 가짜 감독자, pid, DB, 등록 횟수 | PR1 |
| RL11 | [펜싱 T8·T14 · 255] 펜싱된 A(또는 표에 없는 세션)가 늦게 보낸 log · phase · finish(좁은 확인이 아닌 것) · 산출물 업로드 · capabilities. 별도로 같은 토큰의 프로세스 둘 이상을 같은 순간에 시작한다 | 늦은 보고는 펜싱이면 409 `session_fenced`, 표에 없으면 409 `not_registered`(남의 토큰 403 과 구별)이고 잡 상태 · `log.txt` · 마커 · 산출물 · 능력 보고 · `late_result` 에 한 바이트도 반영되지 않는다. 동시 시작에서는 살아남는 세션이 정확히 하나다(둘 다 죽거나 둘 다 살지 않는다). 펜싱된 세션의 재등록은 409 `session_fenced`. 근거: 결정 255 · `docs/m5b2-workplan.md` 「종료 잡의 늦은 보고」 | 단위(HTTP): 응답 코드 · `code`, `log.txt` sha256, DB | PR1 |
| RL12 | [세션이 아닌 것 T10·T9] (a) 같은 프로세스의 다른 레인이 `not_registered` 409 로 재등록 (b) M5q 버전 따라가기: 드레인 뒤 같은 PID 로 exec (c) 서비스 워커가 잡을 도는 중 같은 토큰으로 `rcm worker --check` | (a) 같은 세션 id 재등록은 멱등: 다른 레인의 도는 잡을 닫거나 멈추지 않는다(결정 260). (b) 드레인 전에 보고한 잡은 그대로이고 못 보고한 잡은 디스크 큐에 남아 exec 뒤 새 이미지가 등록 전에 보낸다. exec 뒤 새 세션이 옛 세션을 `previous_sessions` 로 넘겨도 끝난 잡의 상태는 안 바뀐다. (c) 세션을 만들지 않아 도는 잡이 안 멈추고 서비스 워커의 heartbeat 가 409 를 받지 않는다(M5t-a PR2 #268 이 머지되면 `rcm doctor` 도 같은 시험에 넣는다). 근거: 결정 255 · 260 · `remote_worker.py:1-17` · `cli.py:4645` | e2e + 단위: DB, heartbeat 응답, 잡 pid | PR1 |
| RL13 | [호환 T12 · 순서] 서버와 버전이 다른 프로세스(따라가기 전의 옛 설치)가 같은 토큰으로 `register` 한다. 현재 세션 A 가 잡을 도는 중 | 오늘 계약대로 409 + `server_version` 만 돌려준다. 세션 표 · 펜싱 · `lost` 어느 것도 일어나지 않는다. A 의 heartbeat 와 잡이 그대로 이어지고 `rcm wait` 결과가 안 바뀐다. 버전 검사가 프로토콜 · 세션 검사보다 먼저다. 근거: `remote_workers.py:390-398` | 단위(HTTP): 응답 본문, DB `jobs` · `worker_sessions` | PR1 |
| RL14 | [재시작 T9·W7·W8] 워커가 (a) 잡 X 를 끝내고 finish 를 디스크 큐에 썼으나 서버에 못 보낸 채 죽는다 (b) 잡 Y 를 도는 중 `kill -9` 로 죽는다. 같은 데이터 폴더 · 같은 토큰으로 다시 시작한다 | (a) 다시 시작한 워커가 등록 전에 X 의 finish 를 옛 세션 id 로 보내고 X 는 실제 결과로 확정된다. 등록이 X 를 먼저 `lost` 로 닫지 않는다. (b) Y 의 고아 그룹이 정리되고 Y 는 `lost`(`worker_restarted_without_job`, 종료 3). Y 가 이미 유예 만료로 `lost` 였으면 등록이 그 요약을 덮지 않는다(`tests/test_e2e_worker.py:797` 계약). 둘 다 자동 재실행 없음. 근거: 결정 268 · `docs/m5n-workplan.md:652` | e2e: kill -9 후 재시작, DB, 종료 코드 | PR1 |
| RL15 | [고아 T9] 표식 프리셋의 잡 프로세스 그룹이 도는 중 워커를 `kill -9` 한다(자식은 고아). 같은 데이터 폴더로 새 프로세스를 시작한다. 별도로, 기록된 pid 가 이미 다른 프로세스에 재사용된 경우 | 새 프로세스는 claim 을 시작하기 전에 죽은 세션의 프로세스 그룹을 정리한다(고아 pid 소멸). `lstart` 가 다른(재사용된) pid 는 건드리지 않는다(무관한 프로세스가 살아 있다). 정리 결과가 워커 로그에 남는다. 근거: 결정 268 · 253 | e2e: pid 생존 확인, 워커 로그 | PR1 |
| RL16 | [로그 · 오프셋] (a) 서버는 조각을 받았는데 응답이 유실돼 워커가 같은 조각을 다시 보낸다 (b) 요청이 유실돼 워커의 다음 조각 오프셋이 서버보다 앞선다(흉내) (c) 서버 재시작 직후, 덧붙였지만 커밋 전에 죽은 조각 | 서버 `log.txt` 가 워커 스트림 스풀과 바이트 단위로 같다(가릴 값이 없을 때 sha256 일치, 중복 · 빈틈 · 순서 뒤바뀜 없음). 이미 받은 범위는 무시한다. 빈틈이면 409 `log_gap` + `expected_offset` 이고 워커는 프로세스를 죽이지 않고 그 오프셋부터 다시 보낸다. (c) 기동 때 `log.txt` 를 `log_file_bytes` 로 자르고 재전송이 다시 채운다. 근거: 결정 271 · `remote_workers.py:630-659` · `remote_worker.py:157-160` | 단위 + e2e: sha256 비교, 워커 로그, pid 생존 | PR2 |
| RL17 | [로그 · 줄 끝] (a) 마지막 줄에 `\n` 이 없는 채로 잡이 끝난다 (b) `::rcm::step::` 마커 줄 직후 서버가 재시작된다 (c) `\r` 진행 표시가 줄 끝 없이 수백 KiB 이어진다 | (a) 그 바이트가 잡 종료 뒤 `rcm logs N` 에 남는다(유실 0, 러너가 `\n` 을 붙인다). (b) 워커가 보낸 조각은 모두 `\n` 으로 끝나 마커가 한 줄로 파싱되고 `steps` · `failed_step` 이 정확하다. (c) 서버 메모리 · 조각이 줄 끝 없이 늘지 않고(한 줄 ≤ 128 KiB) 바이트는 결국 모두 도착한다. 실시간 tail 에 늦게 보이는 정도는 문서에 적는다. 근거: `runner.py:345-356, 379-385` · F35 실측 | 단위 + e2e: `rcm logs`, wait JSON `steps`, 조각 끝 바이트 | PR2 |
| RL18 | [로그 · 비밀] 서버가 아는 릴리스 비밀이 로그에 섞인다. (a) 한 조각 안 (b) 조각 경계 근처 (c) 재전송으로 같은 조각이 두 번 (d) 가리기로 길이가 달라진 뒤의 오프셋 | 서버 `log.txt` · `rcm logs` · SSE · 웹 로그 어디에도 비밀 원문이 없다(검색 0건). 조각은 줄 경계에서 잘려 한 값이 두 요청에 걸치지 않는다. 오프셋은 가리기 전 원본 바이트 기준이라 (d) 이후의 재전송도 중복 · 빈틈 없이 맞는다(`log_offset` 과 `log_file_bytes` 가 다르다). 근거: 되돌릴 수 없는 결과(비밀 노출) · `remote_workers.py:635-637` · 결정 271 | 단위 + e2e: 비밀 표지 문자열 검색(log.txt · SSE · `rcm logs`) | PR2 |
| RL19 | [로그 · 재전송 상한] 서버가 로그와 finish 를 계속 못 받는다(5xx · 서버 디스크 가득 503 `log_write_failed` · 분단). (a) 잡이 도는 중 (b) 잡이 끝난 뒤 | (a) 도는 중에는 횟수로 포기하지 않는다. 분단 5분 동안 재시도가 수십 번 쌓여도 재연결 뒤 로그가 완전하다. (b) 잡이 끝난 뒤에만 상한이 걸리고 상한은 시간(24시간)으로만 센다. 상한을 넘으면 `log_bytes` 를 실은 finish 가 가서 잡이 결과대로 확정되고 `log_complete = false`(finish 가 영원히 막히지 않는다). 근거: 결정 264 · 272 | 단위: 주입 시계 + 실패 N 회, e2e: 서버 로그 쓰기 실패 주입, DB | PR2 |
| RL20 | [로그 · 유실 표시] 분단 중 잡이 오늘의 `LOG_KEEP_BYTES`(4 MiB)보다 많은 로그(12 MiB)를 낸다. 한 번은 워커 디스크가 가득 차 스풀을 못 쓴다 | 표시 없이 버려지지 않는다. 재연결 뒤 12 MiB 가 모두 서버에 들어간다(바이트 일치). 스풀을 못 쓴 경우는 잡 문서 · `rcm wait` JSON 에 `log_complete = false` 가 서고 `rcm logs` · 웹 로그 보기에도 한 줄로 보인다. 앞쪽이 조용히 잘린 로그가 `log_complete` 참과 함께 나오지 않는다. 근거: 결정 271 · 273 · `remote_worker.py:144-172` | 단위 + e2e: 서버 로그 바이트 수, wait JSON `log_complete`, 웹 | PR2 (값) · PR3 (`rcm logs` · 웹 줄) |
| RL21 | [finish 중복 · 지각 T7·T14] (a) finish 응답이 유실돼 같은 finish 가 두 번 온다(재시작 뒤 큐에서 한 번 더 포함) (b) `lost` 로 닫힌 뒤 같은 세션의 finish · log · phase 가 늦게 온다(자기 멈춤의 `lost` 보고 포함) | (a) 잡은 한 번만 확정되고 첫 보고가 이긴다(`finished_at` · 요약 불변). 둘째는 409 `job_closed` 로 무해이고 워커는 전달된 것으로 읽어 큐에서 지운다(오류 반복 없음). `job_finished` 이벤트 1회, `[[notify]]` 훅 호출 1회. (b) 잡은 `lost` 그대로, 종료 3, 요약은 서버가 먼저 닫은 사유 그대로다. finish 만 `late_result` 로 한 번 남고 로그 · 마커 · 산출물에 반영 없음. 워커는 그 보고를 큐에서 지운다(영원히 재전송 안 함). 자동 재실행 · retry 사슬 없음. 근거: `tests/test_worker_lost.py:128-131` · 결정 256 · 274 | 단위 + e2e: DB, 이벤트 버스, notify 가짜 훅 호출 수, 워커 큐 폴더 | PR2 |
| RL22 | [산출물 T7] 잡이 끝났고 산출물 번들 업로드가 (a) 끊긴다 (b) 서버는 받았는데 응답이 유실돼 다시 올린다 (c) 워커가 업로드와 finish 사이에 재시작한다 (d) 워커가 업로드 없이 처분 `ready` 를 보낸다 | 잡 결과는 산출물과 무관하다(`succeeded` 면 `succeeded`, wait 종료 0). `artifacts.state` 는 번들이 서버에 실제로 있을 때만 `ready` 이고 아니면 `failed` + `upload_failed`(또는 `hash_mismatch`). 재업로드는 멱등이다(번들 1개, sha256 동일, 보관 행 1개). finish 는 업로드 뒤에만 간다. 남겨 둔 묶음 스풀이 재시작 뒤에도 쓰인다. `rcm wait --fetch-artifacts` 는 받으면 0, 못 받으면 5. 근거: 결정 275 · `remote_workers.py:696-750, 866-891` · `PLAN.md:118` | e2e: `artifacts.state`, 번들 sha256, 종료 코드 | PR2 |
| RL23 | [전이 T6 · 준비 단계 · 259] 워커가 `materializing`(스냅샷 `GET …/tree` 내려받는 중)일 때 분단 · 서버 정지가 난다. 별도로 스냅샷이 서버에 없다(404) | 연결 실패는 `lost` + `snapshot_unreachable`(종료 3), 확정 결함(404)은 오늘처럼 `failed` + `snapshot_download_failed`(종료 1). 잡이 `running` 으로 영원히 남지 않고 레인이 풀린다. `.part` 가 남지 않는다. 프로세스는 뜬 적이 없다. 자동 재실행 없음. 근거: 결정 259 · `remote_worker.py:892-899` · `PLAN.md:118` | e2e: 중계 정지 중 큰 스냅샷, DB, 워크스페이스 | PR2 |
| RL24 | [업그레이드 T11·T12 · 262] v25 DB 에서 v26 으로 마이그레이션(필수 시험). 열기 전 DB 에 `running` 원격 잡 · `queued` · `lost` · `cancelling` 이 있다. 세션을 모르는 옛 워커(같은 버전 문자열)가 새 서버에 heartbeat · claim · register 한다 | 마이그레이션 뒤 모든 잡의 상태 · 요약 · 로그 경로가 그대로이고(건수 · 상태 표 일치) `PRAGMA user_version` 이 26. 옛 빌드는 v26 DB 를 열기를 거절한다(newer-schema). 옛 워커가 이미 들고 있던 `running` 원격 잡(레거시)은 heartbeat · log · finish 가 받아져 끝까지 확정되고, 레거시 잡이 끝나면 세션 없는 heartbeat 도 409 `protocol_required`. 옛 워커의 claim · register 는 409 `protocol_required` 이고 claim 은 한 건도 안 나간다. 도는 옛 워커는 claim 을 2~4초 간격으로만 다시 묻고(옛 코드 `remote_worker.py:796-800`, 1초 미만의 뜨거운 루프 아님), 다시 시작한 옛 워커는 등록 409 에 종료 2 → 감독자 간격(5~10초)으로만 되살아난다. 조용한 레거시 잡은 들음 + G 에 `lost`. 근거: 결정 262 · `store.py:1208-1248, 778-785` · F40 | 단위: v25 DB 고정 파일을 새 빌드로 열기, 읽기 전용 사본 + 옛 빌드 거절, 세션 없는 HTTP 요청 | PR1 |
| RL25 | [호환 T13 · 258 · 270] 프로토콜 키가 없는 옛 서버(같은 버전 문자열)에 새 워커가 붙는다 | 등록 전에 알아채고 register 하지 않는다(서버에 `workers` 행이 안 생기고 옛 서버의 도는 잡에 영향 없음). claim 하지 않는다. 한 줄 안내를 내고 종료 3 — 감독자가 되살리는 값이다(launchd · systemd 표, F39). 서버로 간 요청은 `GET /worker/spec`(와 `GET /api/health`)뿐, register · claim 0건. 근거: 결정 258 · 270 | 단위: 가짜 옛 서버 응답, 종료 코드, 서버 쪽 요청 목록 | PR1 |
| RL26 | [시계 · 잠자기 W3] 워커 머신이 잠들었다가 G 보다 오래 뒤에 깬다(주입: 벽시계 +G+α, 단조 시계는 거의 정지). 표식 · 일반 각각 | 표식 프리셋은 깬 직후 heartbeat 루프의 첫 판정에서(heartbeat 를 다시 보내기 전에) 자기 멈춤이 걸려 프로세스 그룹이 끝난다. 단조 시계만 보고 「몇 초 안 지났다」며 이어 돌리지 않는다. 일반 프리셋은 죽이지 않는다 — 서버가 이미 `lost` 로 닫았어도 계속 돌고 끝나면 `late_result`(결정 256). 근거: 결정 253 · 256 · 267 | 단위: 워커 시계 주입(벽시계 · 단조 따로) | PR1 |
| RL27 | [시계 · 점프와 어긋남] (a) 서버 벽시계가 앞으로 G 이상 뛴다(NTP 단계 보정), 워커는 계속 heartbeat (b) 워커 시계가 서버보다 ±1시간 어긋난다 | (a) heartbeat 가 계속 오던 워커의 잡이 점프 한 번으로 `lost` 되지 않는다(유예는 서버 단조 시계). (b) 유예 · `lost` 판정은 서버 시계로만, 워커의 자기 멈춤은 워커 시계로만 한다. 워커가 보낸 `finished_at` 이 서버 수신 시각보다 미래이거나 `started_at` 보다 과거면 그 끝으로 잘라 `job_seconds` 가 음수가 되지 않는다. 근거: 결정 266 · 277 · `remote_workers.py:769-771` | 단위: 서버 `now_fn` 점프 · `mono_fn`, 워커 시계 오프셋, DB `finished_at` · `job_seconds` | PR1 (a) · PR2 (b 자르기) |
| RL28 | [요청자 · 유예 중 T2] 원격 잡이 `running` 인데 워커와 연락이 끊겨 `worker_timeout_seconds`~G 사이. 요청자는 `rcm wait --job N`, `rcm top`, 웹 큐 화면을 본다 | 잡은 `running`. 사유가 `worker_unreachable_grace` 이고 대기 줄 · top · 웹이 「워커와 연락이 끊김」을 정상 진행과 구별해 사람 말로 보이고 원문 코드가 안 나온다. `estimate.finish_at` 은 null(`—`). 「확인이 필요한 작업」 요약에 `worker_down` 바로 뒤 순서로 오른다. 레인 상태와 `/api/health`(`workers_down` · 503)는 유예 동안 한 값으로 고정되어 깜빡이지 않는다. `rcm wait` 은 이 동안 끝내지 않는다. `--timeout` 을 줬으면 그때 3. 근거: 결정 276 · `PLAN.md:118` | e2e: `/api/status` 행의 `reason` · `estimate.finish_at`, `rcm wait` stderr + 웹 node 시험(사유 문구 · `ACTIONABLE` 순서). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR3 |
| RL29 | [요청자 · 끝] 연락 두절의 끝 네 가지(서버가 G 뒤 `lost` · 표식 프리셋 자기 멈춤 · 펜싱 · 워커 재시작의 버린 잡)를 `rcm wait` · `rcm status` · 웹 · `[[notify]]` 훅으로 본다. 새 키를 모르는 옛 `rcm wait` 클라이언트도 같이 | 넷 모두 `state=lost`, 종료 3, `failed_step` · `last_step` 없음. `summary_code` 가 원인마다 다르고(`worker_unreachable` · `worker_self_stopped` · `worker_replaced` · `worker_restarted_without_job`) 웹 · 사람용 마지막 줄에 번역된 문장이 나온다(i18n 에 없는 코드가 있으면 시험이 빨개진다). 마지막 줄이 `rcm logs N` 과 URL 을 안내한다. `[[notify]]` 훅은 잡당 1회. 옛 클라이언트는 새 키를 무시하고 같은 종료 코드를 낸다. 근거: `PLAN.md:118, 220` · 결정 70 · 278 · `tests/test_outcome.py:543` | e2e: wait JSON `summary_code`, 종료 코드, 가짜 notify 훅 + 웹 node 시험(번역 문장). 화면 모양은 실기(PR4, CDP 스크린샷 + 에러 수집기 비어 있음) | PR3 |
| RL30 | [설정 · 257 · 263] 서버 설정의 `disconnect_grace_seconds`: 기본값 / `worker_timeout_seconds` 이하 / 86400 초과 / 정수 아님 · 음수 / 서버 설정을 바꾼 reload. 프리셋 `irreversible` 을 잡이 도는 중 reload 로 바꾼다. 서버를 다른 G 로 재시작한다. 워커 설정의 기존 `grace_seconds`(SIGTERM→KILL)도 같이 | 기본은 600. `worker_timeout_seconds` 이하 · 86400 초과 · 비정수는 기동 실패(종료 2)와 키 이름을 말하는 문장. `[server]` 를 바꾼 reload 는 오늘처럼 통째로 거절되고 옛 값이 남는다. 도는 잡은 claim 때 얼린 `irreversible` · G 를 쓴다 — reload 나 재시작으로 바뀐 값은 다음 claim 부터. 새 키가 `grace_seconds` 와 겹치지 않고 후자의 뜻(SIGTERM→KILL)은 그대로다. 근거: 결정 257 · 263 · `config.py:1323-1328` · `server.py:5090` | 단위: 설정 로드 · reload, 종료 코드, 에러 문구, claim 응답 | PR1 |
| RL31 | [고아 · 신원 모름 T9] 죽은 세션의 잡 그룹이 살아 있는데 `ps` 가 없거나 실패해 `lstart` 를 확인할 수 없다 | 새 프로세스는 그 그룹을 죽이지 않고 워커 로그에 경고를 남긴다. 그 세션을 `previous_sessions` 에 넣지 않아 서버가 그 잡을 즉시 닫지 않고 마지막 들음 + G 까지 busy 로 둔다(그룹 자리 유지). 근거: 결정 268 · 되돌릴 수 없는 결과(남의 프로세스를 죽임 · 두 번 실행) | 단위: `ps` 실패 주입, 등록 본문, DB | PR1 |
| RL32 | [호환 · 순서 T13] 새 워커가 버전이 다른 서버(옛 릴리스)에 붙는다. 따라가기 설정 켬/끔 | 프로토콜 확인이 먼저 종료 3 으로 끝내지 않는다: 버전이 다르면 오늘의 등록 길로 가서 서버의 버전 409 를 받고 따라가거나(M5q) 종료 2. 서버에 `workers` 행 · 세션 행이 생기지 않는다. 근거: 결정 270 · `remote_workers.py:390-398` · `remote_worker.py:381-394` | 단위: 가짜 서버(버전 다름 · spec 키 없음), 종료 코드, 요청 목록 | PR1 |
| RL33 | [취소 · 펜싱 T4·T8] 펜싱된 세션 A 의 `cancelling` 잡이 있고 같은 이름의 새 세션 B 는 살아서 heartbeat 를 보낸다. `kill_at + 2 × heartbeat` 가 지난다 | 서버는 A 의 잡을 `cancelled`(`cancel_unconfirmed`)로 닫지 않는다 — 취소 미확인 닫기는 현재 · 들리는 세션의 잡에만. 잡은 A 의 확인이나 A 의 마지막 들음 + G 까지 busy, 그 뒤 `lost`. 근거: 결정 261 · 266 · `remote_workers.py:965-999` | 단위: 두 세션, 주입 시계, DB | PR1 |

LIVE_FORBIDDEN: 운영 서버, 운영 워커, 실제 deploy · store 프리셋을 쓰는 실행은 어느 행에도 쓰지 않는다. 대역은 `test-server.md`
규칙을 따르는 시험 서버(자기 설정, `port` 8790~8899, `data_dir` `~/.local/share/rcm-devtest-<topic>`)와 가짜 `sh -c` 표식
프리셋, 표준 라이브러리 TCP 중계(`tests/tcp_relay.py`, 끊었다 잇기)다. RL28 과 RL29 의 화면 확인도 실기에서 시험 서버와 CDP 로
한다. 두 행은 pytest 와 node 시험이 JSON 과 문구를 잠그므로 `(live)` 표시를 달지 않았다. `(live)` 행은 pytest 누락으로 세지
않아서 잠금이 빠지기 때문이다.

Excluded (rare): 펜싱된 세션이 끝나는 도중 서버가 재시작된다 · ① · 되돌릴 수 있다(A 가 다음 요청에서 같은 409 를 받는다). RL10 과 RL7 이 각각 잠근다.
Excluded (rare): 워커의 스풀 · 큐 파일이 디스크 오류로 손상된다 · ③ · 로그 · finish 지연뿐이고 서버가 `lost` 나 `log_complete = false` 로 닫는다. CHANGELOG · `docs/acceptance/reports/` 검색 0건.
Excluded (rare): 워커가 7일(`WORKSPACE_KEEP_DAYS`) 넘게 꺼져 있다가 큐에 남은 finish 를 들고 시작한다 · ① · 서버가 오래전에 `lost` 로 닫았고 결과는 RL21(b)와 같다.
Excluded (rare): 워커 데이터 폴더가 `flock` 을 지원하지 않는 파일 시스템에 있다 · ③ · 되돌릴 수 있다(이어받기를 건너뛰어 서버가 G 까지 기다린 뒤 닫는다 — 늦을 뿐 두 번 돌지 않는다). CHANGELOG · reports 검색 0건.

### 7.2 시나리오 표를 고친 자리

여기 적은 것은 §12 에도 옮긴다.

- 결정이 정한 「undecided」를 채웠다. RL5(256), RL8(256), RL10(258 · 261), RL12(a)(260), RL21(256 · 274), RL23(259),
  RL24(258 · 262), RL25(258), RL30(257 · 263)이다.
- RL26 의 마지막 문장 「서버가 이미 lost 로 닫은 잡은 계속 돌지 않는다」를 표식 프리셋에만 걸었다. 일반 프리셋은 결정 256
  대로 계속 돈다.
- RL17(b)의 「마커 줄이 두 조각에 걸침」을 「마커 줄 직후 재시작」으로 바꿨다. 조각은 줄 경계에서만 잘리므로(F34 · F35)
  걸칠 수 없다. 확인할 것은 「조각이 모두 `\n` 으로 끝남」이다.
- RL18(b)의 「두 조각 경계에 걸침」을 같은 이유로 「경계 근처」로 바꿨다.
- RL24 의 「5초 이상 간격」을 옛 워커 코드가 정한 간격(F40)으로 고쳐 적었다. 옛 워커는 고칠 수 없다.
- RL25 의 「서버 쪽 요청 수 0건」을 「register · claim 0건」으로 바꿨다. 등록 전 확인의 `GET` 은 있다.
- RL16(b)의 「뒤 조각이 먼저 닿는다」는 워커가 한 잡 안에서 순서대로 보내므로 실제로는 생기지 않는다. 빈틈 응답을 흉내 내어
  시험한다.
- RL11 에 `not_registered` 갈래를, RL22 에 (d)를, RL10 에 204 와 확인이 없는 경우를 더했다.
- RL28 과 RL29 의 `(live)` 표시를 뗐다. JSON, 종료 코드, 문구는 pytest 와 node 시험으로 잠그고 화면 모양만 PR4 실기로 본다.
  `tools/scenario_trace.py` 는 `(live)` 행을 누락으로 세지 않아 시험 잠금이 빠진다.
- 새 행은 셋이다. RL31 은 고아의 신원을 모르는 경우로, 결과를 되돌릴 수 없다. RL32 는 버전이 섞인 경우로, 흔하므로 버전
  검사를 먼저 한다. RL33 은 펜싱된 세션의 취소 중 잡이 두 번 실행으로 이어지는 경우로, 결과를 되돌릴 수 없다.
- 뺀 행은 없다.

### 7.3 오늘 계약이 바뀌는 시험

아래 시험은 기대값을 고쳐 쓴다. 결정 253 이 계약을 바꿨으므로 시험을 약하게 만드는 것이 아니다.

| 시험 | 오늘 | 바뀐 뒤 | PR |
|---|---|---|---|
| `tests/test_worker_lost.py:58` `test_worker_past_timeout_loses_its_running_job_and_shows_down` | 80초 뒤 `lost` | 80초 뒤 레인 `down`, 잡 `running` · 사유는 PR3. G 뒤 `lost`(`worker_unreachable`, 초는 단조 gap) | PR1 |
| `tests/test_worker_lost.py:107` `test_heartbeat_brings_a_down_worker_back_up_without_resuming_the_lost_job` | timeout+30 에 lost, 돌아와도 안 살아남 | G 뒤 lost 로 같은 내용(늦은 finish 는 409 + `late_result` 는 PR2) | PR1 |
| `tests/test_worker_lost.py:137` `test_cancelling_job_of_a_down_worker_becomes_lost_not_cancelled` | timeout+30 에 lost | G 뒤 lost, 같은 결론 | PR1 |
| `tests/test_e2e_worker.py:775` `test_killed_worker_leaves_the_job_lost_after_the_timeout_and_a_new_worker_takes_over` | timeout 에 lost, 고아는 시험이 치움 | 시험 서버 G 를 짧게. G 에 lost. 다시 띄운 워커가 같은 데이터 폴더면 고아를 치운다 — 시험이 고아를 치우는 줄은 RL15 로 바뀜 | PR1 |
| 세션 없이 register · claim 하는 시험 도우미(`tests/test_worker_*.py`, `tests/test_server_m5q_worker.py` 등) | 프로토콜 1 | 도우미가 세션 id 를 실행 중 만들어 싣는다(§8 gitleaks) | PR1 |

### 7.4 mutcheck 변이

모두 새 이름이다. `scripts/mutcheck.py` 에서 grep 하면 0건이다.

| 이름 | 파일 · 바꿈 | 빨개져야 할 행 | PR |
|---|---|---|---|
| `session-fence-by-name` | `remote_workers.py` 세션 판정을 이름 비교로 | RL10 RL11 RL12 | PR1 |
| `reregister-closes-own-jobs` | register 가 같은 세션 재등록에도 그 이름의 busy 잡을 닫음 | RL12 | PR1 |
| `grace-wall-clock` | 유예 gap 을 벽시계 `last_seen_at` 으로 | RL27 RL7 | PR1 |
| `grace-no-start-floor` | `heard` 바닥(기동 시각)을 지움 | RL7 RL2 | PR1 |
| `self-stop-monotonic-only` | 워커 `wgap` 을 단조 시계만으로 | RL26 | PR1 |
| `orphan-kill-no-lstart` | 고아 정리에서 `lstart` 비교를 건너뜀 | RL15 RL31 | PR1 |
| `job-closed-kills-running` | 409 `job_closed`(`state=lost`)에 일반 잡을 죽임 | RL5 | PR1 |
| `log-offset-ignored` | 서버가 오프셋을 보지 않고 덧붙임 | RL16 RL6 | PR2 |
| `late-result-changes-state` | 늦은 finish 가 `lost` 를 결과로 바꿈 | RL21 RL4 | PR2 |
| `artifact-ready-without-bundle` | 묶음 행이 없어도 `ready` 를 받음 | RL22 | PR2 |
| `unreachable-reason-dropped` | `compute_queue` 가 down 워커의 도는 잡에 `running` 을 줌 | RL28 | PR3 |

### 7.5 시험 도구

- `tests/tcp_relay.py`(새, PR1)는 표준 라이브러리만 쓴다. `pause()` 는 새 연결 수락을 멈추고 기존 연결의 응답을 붙잡아 분단을
  만든다. `stop()` 은 연결을 거부한다. 서버 정지를 흉내 내는 것이 아니며 서버 정지는 실제 서버를 멈춰서 만든다. `resume()` 은
  다시 잇는다.
- 시계는 서버 쪽이 `now_fn` 과 새 `mono_fn` 이다. 워커 쪽은 `now_fn` 에 단조 시계 주입(새 `mono_fn` 인자)을 더한다.
  `RemoteWorker.__init__` 에는 이미 `now_fn` 과 `rand_fn` 을 주입한 선례가 있다(`remote_worker.py:245-258`).
- 20초가 넘는 시험은 `@pytest.mark.slow` 를 단다(AGENTS.md:130-133). e2e 의 G 는 몇 초다.

## 8. 개인정보 점검

dev 에서 main 으로 가는 릴리스 PR 때 본다.

- 오너 발언 가운데 다른 프로젝트의 이름, 경로, 식별자는 명세에 옮기지 않는다. 머리 인용에는 이름 없는 문장만 둔다. Codex
  원문과 오너 발언 원본 파일은 레포 밖에 있으므로 경로를 적지 않는다.
- 시험의 세션 id 와 토큰은 실행 중에 만든다. 높은 엔트로피 리터럴은 gitleaks 이력에 남으므로 쓰지 않는다(F51).
- 실기 보고서(PR4)의 스크린샷과 로그에 홈 경로, 호스트 이름, 사설 IP 가 없는지 본다. 시험 서버 `data_dir` 은 `~` 로 적는다.
- 고아 정리, 펜싱, 자기 멈춤의 워커 로그 줄에 경로, 토큰, 서버 URL 의 자격 정보가 없는지 본다. 상태 코드만 공개한다는
  오늘의 규칙(`remote_worker.py:896-899` 주석)을 따른다.
- 릴리스 PR 에서는 메인 세션이 레포 밖의 패턴 파일로 점검한다(오너 지시, 2026-11-23).

## 9. 위험

| 위험 | 막는 것 |
|---|---|
| 일반 프리셋 잡이 G 뒤 `lost` 가 된 뒤에도 워커에서 계속 돌아 같은 그룹의 다음 잡과 겹친다 | 결정 253 이 받아들인 맞교환(일반 프리셋은 두 번 돌아도 된다). 결과는 `late_result` 로만 남는다. 문서에 적는다 |
| 워커 벽시계가 앞으로 `G − m`(기본 약 9분 40초) 이상 뛰면 표식 잡이 일찍 멈춘다 | 결과는 `lost`(3) — 모름이지 실패가 아님. 문서에 「표식 잡을 도는 워커는 NTP 단계 보정을 피하라」. 벽시계를 빼면 잠자기를 못 센다(결정 267) |
| 서버 재시작 뒤 원격 잡이 최대 G 동안 더 `running` 으로 남아 그룹 자리를 잡는다(워커가 재시작 전에 죽었어도) | 보수적인 쪽(두 번 실행보다 늦음). 워커가 돌아오면 바로 닫힌다 |
| 펜싱된 세션이 분단이면 그 잡이 G 까지 레인 · 그룹을 잡고 화면 사유는 `running`(그 이름의 현재 세션이 살아 있어 레인이 down 이 아니다) | 표시 한계로 문서에 적는다. 잡은 G 에 닫힌다 |
| `kill -9` 된 워커가 다시 시작되지 않으면 고아 표식 잡을 아무도 멈추지 않고 G 뒤 그룹이 풀린다 | 서비스 파일의 감독자(launchd `KeepAlive`, systemd `Restart`)가 다시 띄운다(F39). 수동 실행 워커는 문서 경고 |
| 고아 신원 확인에 쓰는 `ps -o lstart=` 가 Linux 에서 실측 안 됨(F44) | 없거나 실패하면 죽이지 않고 G 까지 잡는다(RL31). 2차 위임 결정 Q-D 가 이 대체 동작을 확정했다: 신호를 보내지 않고 `previous_sessions` 에 넣지 않으며 유예 만료까지 잡과 그룹을 잡아 둔다. Linux 실측은 아직 없다(측정 수단은 F44) |
| 같은 토큰의 두 번째 프로세스가 서비스를 펜싱하면 서비스는 종료 0 이라 다시 안 뜬다(결정 255 · 258) | 문서: `launchctl kickstart -k` / `systemctl --user restart` 로 다시 올린다. 두 번째 프로세스의 시작 줄이 「서비스 워커를 대신했다」를 말한다 |
| M5t-a PR2(#268)와 `remote_workers.py` register · `capabilities.py` spec · `config.py` · `cli.py` 가 겹친다 | §5 머지 순서. 먼저 머지된 쪽 뒤에 다른 쪽이 dev 를 합친다 |
| M5t-c 가 register(풀 묶기)와 DB v27 을 쓴다 | M5t-b PR1 이 v26 을 먼저 머지한다는 전제. M5t-c 가 먼저 머지되면 v 번호를 서로 바꿔야 한다 → 메인 세션이 M5t-c 명세 §9 와 함께 확인(§6 #11) |
| `irreversible` 낱말이 릴리스 쪽의 「되돌릴 수 없는 입력 값」(F26)과 뜻이 다르다(프리셋 전체) | 문서에 구별을 적는다. 입력이 rehearsal 이어도 자기 멈춤이 걸리는 것은 안전한 쪽 |
| 보고 큐가 디스크를 쓴다(로그가 두 벌: 로컬 `log.txt` + 스트림 스풀) | 7일 청소(F41). 디스크가 가득 차면 `log_complete = false` 로 정직하게 표시(RL20) |
| 옛 워커가 섞인 dev(같은 버전 문자열)에서 옛 워커가 2~4초마다 claim 409 로그를 찍는다 | 잡은 안 집는다(RL24). 출시 뒤에는 M5q 따라가기가 버전을 맞춘다 |
| M5t-c 가 등록에 `pair_proof` 검사를 더하고 워커 종료 3 을 함께 쓴다 | 2차 위임 결정에서 Codex 와 논의해 충돌이 없음을 확인했다. `pair_proof` 검사는 등록에서 세션 넣기 · 펜싱 · 능력 보고 지우기 · 잡 닫기보다 먼저 한다. 버전 · 프로토콜 검사는 부작용이 없으면 그보다 앞서도 된다. 종료 3 은 프로토콜 불일치와 서버 신원 미확인 두 경우 모두 「일을 시작하지 말고 감독자가 다시 시도」를 뜻한다(§4.2 · §4.7 · §5) |

## 10. 요청서에 돌려줄 것

요청서가 없으므로 해당 없다.

## 11. 결정 표

256~264 는 1차 위임 결정이고 265~279 는 2차 위임 결정이다. 오너가 2026-11-26 질문을 메인 세션에 맡겼고 메인 세션이 Codex 와
논의해 정했다. Codex 와 논의한 내용은 완료 보고서에 적는다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다른 점 |
|---|---|---|---|---|
| 256 | 일반 프리셋 유예 만료와 늦은 결과(Q1) | 유예 만료에 서버가 `lost`(`rcm wait` 3). 같은 세션의 늦은 finish 는 `late_result`(outcome · 프로세스 종료 코드 · 워커 finish 시각 · 수신 시각 · 로그 완전성)로만 남기고 `lost` 를 바꾸지 않으며 알림 · 로그 · 마커 · 산출물을 받지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 253 의 「계속 돈다」는 워커에서 계속 돈다는 뜻이고 요청자의 결과는 3 |
| 257 | 표식 키와 얼림(Q2) | 별도 프리셋 키 `irreversible = true`(`local_only` 는 배치, `irreversible` 은 끊김 안전). `irreversible` 과 `disconnect_grace_seconds` 는 claim 때 잡 행에 얼린다(reload · 재시작은 다음 claim 부터) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 키 이름이 정해짐(253 은 「표식」) |
| 258 | 워커 종료 코드(Q3) | 펜싱 0(감독자가 되살려 핑퐁하지 않게), 프로토콜 불일치 3(감독자가 다시 시도). 그 밖은 오늘대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 259 | 준비 단계 끊김(Q4) | 명령이 돈 적 없고 실패 결과도 없으니 `lost`(3) + `snapshot_unreachable`. 저장소 없음 · 거절된 스냅샷 같은 확정 결함은 `failed`(1) 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 260 | 같은 프로세스 재등록(Q5) | 프로세스가 만든 세션 id 하나를 모든 레인이 쓴다. 같은 id 재등록은 멱등(펜싱 · 잡 닫기 없음). 다른 id 만 펜싱 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 261 | 펜싱된 잡의 그룹 자리(Q6) | 표식 여부와 상관없이 멈춤 확인(옛 세션이 409 를 받고 그룹을 죽인 뒤 좁은 범위의 「펜싱 멈춤 확인」 finish 를 보냄) 또는 얼린 유예 만료 전에는 풀지 않는다. 그동안 새 세션의 그 레인 claim 은 204 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 255 의 「409 를 받고 잡을 멈춘 뒤 끝난다」를 분단 때까지 넓힘 |
| 262 | 마이그레이션 때 옛 워커의 도는 잡(Q7) | 옛 lease 호환: 이미 도는 레거시 잡의 heartbeat · log · finish(와 그 잡의 phase · 산출물)만 받고 새 claim 은 모두 거절. 그 잡들이 끝나거나 유예에 닿으면 호환은 저절로 거둔다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 263 | 유예 기본값과 검증 | `disconnect_grace_seconds = 600`(결정 253 의 「약 10분」). 정수, `worker_timeout_seconds` 보다 크고 86400 이하, 위반은 기동 실패. `[server]` 라 reload 불가 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 「약 10분」 → 600초 |
| 264 | 재전송 창 · 보관 | finish · 로그 재전송은 잡이 끝난 뒤 24시간(시간으로만, 3판 r3 A-H6). 잡이 도는 동안은 상한 없음. 늦은 결과 · 스풀 보관은 워크스페이스와 같은 7일(`WORKSPACE_KEEP_DAYS`) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 265 | 세션 모델 | 세션 id = 프로세스당 `token_hex(16)`. 새 표 `worker_sessions`(현재 = `fenced_at IS NULL`), `jobs.worker_session_id`. lease = (잡, 세션), 별도 `lease_id` 없음. 펜싱된 세션 행은 워커 행과 함께만 지움. 모든 보고가 세션을 싣고, 표에 없으면 409 `not_registered`, 펜싱이면 409 `session_fenced`, 펜싱된 세션의 재등록도 409 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 266 | 서버 유예 판정 | 잡마다 `mono_now − max(heard(세션), 서버 기동) > G_J` 면 `lost`(`worker_unreachable`). `heard` 는 메모리 단조 시각, 펜싱 뒤 요청은 안 올림. 판정과 닫기는 같은 락 아래. 레인 down 은 벽시계 그대로. 취소 미확인 닫기는 현재 · 들리는 세션의 잡에만. `App` 에 `mono_fn` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(서버 기동 바닥은 2차 Q-C) | — |
| 267 | 워커 자기 멈춤 | 표식 잡만. `max(벽, 단조)` 경과가 2xx heartbeat 보낸 시각부터 `G_J − (grace_seconds + heartbeat_seconds + 5)` 이상이면 그룹 SIGTERM→KILL, finish `lost` + `worker_self_stopped`. 판정은 heartbeat 루프의 매 회차 보내기 전 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 268 | 재시작 순서 · 이어받기 · 고아 | 세션 폴더 `<data>/worker-sessions/<id>/` + `flock`. 시작 때 잠글 수 있는(죽은) 세션의 고아 그룹을 `ps -o lstart=` 신원이 같을 때만 정리, 남은 큐를 옛 세션 id 로 보낸 뒤 `previous_sessions` 로 등록. 서버는 그 세션들의 busy 잡을 `worker_restarted_without_job` 으로 닫음. 신원을 못 확인한 세션은 넘기지 않음. 관찰자에 `spawned(pid)` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(신원을 확인하지 못할 때의 대체 동작은 2차 Q-D) | — |
| 269 | 409 의 기계 판독 키 | 409 응답에 `code` ∈ {`session_fenced`, `protocol_required`, `not_registered`, `job_closed`(+`state`), `log_gap`(+`expected_offset`)}, 503 `log_write_failed`. 오늘의 `error` 문구는 유지. 워커의 응답별 처리는 §4.5 표 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 270 | 프로토콜 판 | `worker_protocol = 2` 를 `/worker/spec` · register 응답에. 새 워커는 등록 전 spec 확인, 키가 없으면 버전이 같을 때만 종료 3(다르면 오늘의 등록 길 — 버전 먼저) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 271 | 로그 오프셋 · 스풀 | 오프셋 = 가리기 전 스트림 바이트, 헤더 `X-RCM-Worker-Session` · `X-RCM-Log-Offset`, 응답 `offset`. 서버 `jobs.log_offset` · `log_file_bytes`, 기동 때 길면 자름. 조각은 256 KiB 이하 · `\n` 에서 자름. 워커는 스트림만 담는 `stream.log`. 3판의 `log.pending` 없음 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 272 | 순서 있는 보고 큐 | 잡마다 phase → 로그 → 산출물 → finish, 디스크(`outbox.json`), 보내는 스레드 하나, 백오프 1→30초, 도는 동안 상한 없음, 끝난 뒤 24시간. 레인은 자기 큐가 끝나야 다음 claim | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 273 | 로그 완전성 | finish 의 `log_bytes` 와 `log_offset` 으로 `jobs.log_complete`(1/0/NULL). 키 `log_complete` 를 최근 행 · 잡 문서 · wait JSON 에(더하기만) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 3판의 `log_incomplete` 를 긍정형 + null 로 |
| 274 | 늦은 결과 기록 | events kind `late_result` 1건, 종료 잡 문서 · wait JSON 키 `late_result`(객체/null), 409 본문 `late_result_recorded`. 펜싱된 세션의 늦은 finish 는 기록 안 함. 웹은 그리지 않음 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(펜싱된 세션 제외는 2차 Q-B) | — |
| 275 | 산출물 재업로드 · ready 조건 | 업로드는 큐의 finish 앞 단계, 재전송 창 안에서 다시. 처분 `ready` 는 그 잡의 묶음 행과 해시가 맞을 때만, 아니면 `failed` + `upload_failed` / `hash_mismatch` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 276 | 유예 중 표시 | 도는 잡의 `reason` 새 값 `worker_unreachable_grace`(`_busy_reason` 맨 앞), `estimate.finish_at` null, `ACTIONABLE_REASONS` · 웹 `ACTIONABLE` 의 `worker_down` 바로 뒤. 레인 `down` · 헬스 그대로. `schema_version` 1 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 277 | 워커 `finished_at` 자르기 | `[started_at, 수신 시각]` 으로 자른다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 278 | `lost` 요약 코드 | 새 `LOST_CODES` = `worker_self_stopped` · `worker_replaced` · `snapshot_unreachable`(finish 의 `outcome = lost` 일 때만). 유예 만료는 `worker_unreachable`(cancelling 포함, `cancel_unreachable` 만들지 않음). 재시작 이어받기는 `worker_restarted_without_job`. 번역은 같은 PR | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 279 | PR 별 레인 | PR0 short · PR1 full · PR2 full · PR3 full · PR4 short(§5) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(2차 Q-A) | — |

## 12. 격리 반박에서 바뀐 것

### 12.1 `docs/m5t-workplan.md` §12 가 넘긴 지적과 답한 자리

라운드 열의 1~3 은 `docs/m5t-workplan.md` 가 받은 격리 반박 1~3라운드다. 본문의 「3판 r1」 같은 표기도 같은 라운드를 가리킨다.

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
| 3 | M14 | 잠자기를 넘는 시계 | §4.1.3 · §4.3, 결정 266 · 267, RL26 · RL27 |
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
| 2 | A-L2 · A-L6 | 설정 키 이름, 결과 고리 변이 | 결정 263, §7.4 변이 11개 |
| 1 | A-H1 | `attempt` 이름 중복 | 새 이름 없음: lease = (잡, 세션), `lease_id` 도 안 둠(결정 265) |
| 1 | A-H3 | 같은 토큰의 두 프로세스 | 결정 255 · 265, RL10 · RL11 |
| 1 | A-H4 | 재시작한 워커의 등록이 먼저 `lost` 로 닫음 | 등록 전 큐 전달 + `previous_sessions`(결정 268), RL14 |
| 1 | A-H5 | 409 를 `closed_by_server` 로 읽음 | `code` 로 가름, `job_closed` 에도 일반 잡은 안 죽임(결정 256 · 269), RL5 · RL16 |
| 1 | A-H6 | `log_incomplete` 판별 수단 | finish `log_bytes` vs `log_offset` → `log_complete`(결정 273) |
| 1 | A-H7 | `lost` 뒤 그룹 자리가 풀림 | 표식 잡은 서버보다 먼저 워커가 멈춤(결정 267, §4.1.3 의 근거), 펜싱 잡은 확인 · 유예까지 busy(결정 261), RL1 · RL3 |
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

§7.2 와 같다. 뺀 행은 없고 새 행은 RL31, RL32, RL33 이다.

### 12.3 이 판의 격리 반박 라운드

라운드가 끝나면 메인 세션이 반박 id, 등급, 1판, 2판, 결정을 채운다.

## 13. 구현하며 바뀐 것 (Changed while implementing)
