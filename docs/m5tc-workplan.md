# M5t-c 작업 명세: 짝짓기 · 서버 신원 보안

> 이 문서는 M5t-c 의 작업 명세 3판이다. 2판을 두 격리 검증자가 다시 반박한 2라운드 지적(HIGH 4 · MED 7 · LOW 13)을 모두
> 반영했다(§12). 다루는 것은 새 머신을 코디네이터에 붙이는 짝짓기와 서버 신원 보안이다. 출처는 넷이다. 아래에 그대로 옮기는
> 오너의 말, `docs/m5t-workplan.md` 의 결정 252~255, 같은 문서 §12 의 「M5t-c 짝짓기 · 서버 신원 보안」 표, 그리고
> 2026-11-26 의 오너 위임 결정 1~6차다. 위임 결정은 메인 세션이 Codex 와 논의해 정했다. Codex 원문은 레포 밖에 두었다.
>
> 2026-11-24 오너는 Codex 세션에서 rcm 을 쓰는 머신을 역할로 나누고 싶다고 했다. 원문을 그대로 옮긴다.
>
> 「자 이제 rcm 이 프로젝트를 / 맨 처음 시작하면, 이거를 rcm 빌드용으로 쓸건지 아닌지 확인하고, 빌드용으로 쓴다고 하면
> 설정을 알아서 완료하고, 다른 rcm 제어하는곳에서 이걸 연결하면 거기에서 알아서 분배하는걸 만들고싶어 즉 / 1. rcm 으로
> 작업 보내는 컴퓨터(유저가 사용) / 2. rcm 으로 작업 받는 컴퓨터 + 작업을 다른 유휴중인 컴퓨터로 보내는 작업 / 3. rcm
> 작업을 진행해서 결과 전달하는 컴퓨터 / 이렇게 3가지로 나눈고싶은거야 이해했어? 이렇게 작업하려는데 2와 3이
> 필요해보이는데」
>
> 같은 날 오너는 「이 컴퓨터에서는 4를 하고싶어」라고 했다. 4 는 코디네이터와 로컬 빌드 워커를 함께 하는 역할이다. 또
> 「Rcm워커 다 한 다음에 어떻게 알려주는거까지 해야지」라고도 했다.
>
> 2026-11-25 오너는 「당연하게도 … 민감데이터나 API 키 등 그런것들은 없어야해 그리고 PR올려야해 이 프로젝트는 public
> 일거니까」라고 했다. 같은 날 결정 252 에서 M5t 를 「셋으로 나눈다」고 정했다. M5t-c 는 그 가운데 위협 모델부터 쓰는
> 별도 명세다. 결정 255 에서는 같은 토큰의 동시 세션을 「지원 안 함」으로 정했다. 워커 이름당 살아 있는 세션은 하나이고
> 새 세션이 옛 세션을 펜싱한다. 펜싱당한 옛 세션은 409 를 받고 잡을 멈춘 뒤 끝난다.
>
> 2026-11-26 오너는 「high안나올때까지 하고 …」, 「나의 지시없이 이 격리에이전트 테스트까지 해서 수정하고 배포하고
> 메인에올려서 버전업까지 하는게 목표야」라고 했다. 범위는 M5t-b 와 M5t-c 까지다. 이어서 「이후에 물어볼거 결정사항이
> 있다면 코덱스와 논의하고 완료보고서 작성할때 코덱스논의 한거 작성해둬」, 「멀티에이전트로 나눠서 작업가능한건 나눠서해
> dev batch로」라고 했다.
>
> 결정 번호는 M5t-c 가 280~319 를, M5t-b 가 256~279 와 320~339 를 쓴다. 이 판은 319 까지 쓴다. 1판은 「280~299 를 M5t-b 와
> 겹치지 않게 미리 나눠 두었다」고 적었는데 틀린 문장이었다(§12 V1-15 · V2-16). 3차 위임 결정 301~303 은 1라운드 지적에 대한
> 답이다. 교환 때 코드를 보내지 않는다(301). 통째로 복사한 `worker.toml` 은 남는 위험으로 둔다(302). 신원을 거절당한 워커를
> 종료 0 으로 끝내는 범위는 303 이 처음 정했고 3판에서 317 이 좁혔다. 4차 위임 결정은 304~312 를 확정했다.
>
> 5차 위임 결정 313~318 은 2라운드 HIGH 에 대한 답이다. 만들 수 없는 `pair_mismatch` 를 지웠다(313). 워커는 상태 파일을 쓰고
> `rcm worker --check` 와 `rcm doctor` 가 그 파일을 읽는다(314). 짝지은 `auto` 는 hello 통과가 사람 확인을 대신하고 짝짓지
> 않은 `auto` 워커는 거절한다(315). 처음 설정의 정본은 짝짓기다(316). 인증되지 않은 답으로는 워커를 끝내지 않는다(317). 신원
> 정지 동안 취소가 닿지 않는 것은 결정 253 의 상속으로 적었다(318). 319 는 2라운드 MED · LOW 의 세부를 묶은 결정이다. 6차
> 위임 결정이 319 와, 5차가 명세에 맡긴 314 · 315 · 317 의 세부 값을 이 판에 적은 대로 확정했다.
>
> 2판의 결함 몇 가지는 원인이 같았다. 「확실하다」고 판정할 증거가 무엇으로 인증되는지 따지지 않고 종료와 상태를 정했다.
> 그래서 3판은 §4.3 에 판정 표 하나(J1~J12)를 두고 다른 절은 J 번호로만 가리킨다. 다른 결함은 dev 에 이미 있거나 M5t-a 가
> 정한 동작과 대조하지 않아서 생겼다. 3판은 이 명세가 바꾸는 기존 동작을 다시 훑어 §1 의 「뒤집거나 좁히는 것」 표에 모두
> 넣었다(§12 「3판의 근본 원인」).
>
> 핀은 둘이다. 코드 핀 A 는 `fdf196d4ef682612857fb12df9162f67545b63f0` 이고 1판 · 2판과 같다. F1~F44 와 M1~M6 의 `file:line`
> 은 이 sha 의 것이다. 2판의 명세 브랜치 head `361cdad73a95023ebb48873561ba2b7e019a1809` 는 핀 A 에서
> `docs/m5tc-workplan.md` 하나만 더했다. dev 핀 D 는 `96a8bd25fa0b0457f6bc75a336c43aeb2a4724ec` 이고 origin/dev 에 #268
> `rcm doctor` 가 머지된 sha 다. F45~F60 가운데 근거 칸에 「D」를 적은 사실은 이 sha 의 것이다. `docs/m5t-workplan.md` 와
> 템플릿 · 예시, `tests/test_cli_init_worker.py` 는 두 핀에서 같다. `src/` 는 두 핀에서 다르므로 D 의 줄 번호는 D 에서만
> 맞다. M5t-b 의 사실은 `docs/m5tb-workplan.md` 2판의 절 번호와 결정 번호로 인용한다. DB 판은 v27 이고 M5t-b 의 v26 뒤에
> 온다. 같은 사실은 §3 에 한 번만 적고 다른 절은 F 번호로 가리킨다. M5t 전체를 담았던 3판의 근본 원인 R5 를 되풀이하지
> 않으려는 것이다.

## 0. 한 줄

코디네이터는 잡을 받아 다른 머신에 나눠 주는 rcm 서버다. 거기서 사람이 `rcm pair --worker`(또는 `--client`)로 짧은 짝짓기
창을 열면 화면에 코드와 URL 이 찍힌다. 새 머신은 `rcm setup --role worker --pair --server <URL>` 에 그 코드를 넣는다. 이 두
명령이 처음 설정의 정본이다. `rcm init worker` 는 미완성 틀로 남고 먼저 짝지으라고 안내한다(316).

워커의 교환 요청에는 코드가 없다. 코드를 scrypt 로 늘린 키로 만든 증명만 보낸다(301 · 319 a). 서버 응답이 같은 키로 만든 MAC
을 실었을 때만 토큰과 풀, 서버 신원 비밀 `pair_secret` 을 저장한다. 코드가 망을 지나는 곳은 하나뿐이다. 코디네이터 밖에서 친
`rcm pair` 가 받는 201 응답이다. 그때 `rcm pair` 는 평문 경고를 낸다(§9 남는 위험).

짝지은 머신은 토큰을 보내기 전에 `POST /pair/hello` 로 서버를 확인한다. 인증된 답은 MAC 이 맞는 hello 하나뿐이다(J1). MAC 이
틀린 hello, 421, 분류되지 않은 답, 연결 실패, 5xx 는 어느 것도 「확실」로 보지 않는다. 이런 답을 받은 워커는 토큰을 보내지
않고 계속 다시 시도하며 10분마다 경고한다. 상태 파일과 `rcm worker --check` · `rcm doctor` 에는 FAIL 이나 unknown 으로
드러난다(J2~J5, 317 · 314). 위장 서버는 일을 막을 수는 있어도 워커를 영구히 멈추지는 못한다.

워커가 신원 때문에 종료 0 으로 끝나는 경우는 둘뿐이다. 종료 0 이면 서비스가 워커를 되살리지 않는다. 하나는 hello 를 통과한
서버가 토큰을 거절한 경우다(401, J6). 다른 하나는 그런 서버가 등록 증명을 `bad_mac` 으로 세 번 이어서 거절한 경우다(J7).
이때도 워커는 도는 잡을 끝까지 돌린 뒤에 끝난다.

짝지은 `server = "auto"` 에서는 hello 통과가 결정 246 의 사람 확인을 대신한다. 짝짓지 않은 `auto` 워커는 mDNS 질의를 보내기
전에 거절된다(315 · 283). 신원이 확인되지 않은 동안에는 취소가 워커에 닿지 않는다. 서버는 유예 뒤 잡을 `lost` 로 닫고
`irreversible` 잡은 워커가 스스로 멈춘다. 되돌릴 수 있는 잡은 끝까지 돈다(318, 결정 253 상속).

워커 토큰에는 풀을 묶어 등록과 `/worker/spec` 을 그 풀로 제한한다. 짝지은 워커가 등록해 세션을 열려면 신원 증명이 있어야
한다. 이 설계가 막지 않는 것도 있다. LAN 엿보기, 이름 URL 중계, 같은 IP 가로채기(통과한 hello 뒤의 401 위조 포함),
`worker.toml` 통째 복사, 엿본 교환 증명의 재생, 다른 머신에서 친 `rcm pair` 의 평문이 그렇다. 모두 남는 위험으로 적는다(282
· 302 · 319).

## 1. 출처와 오너 결정

### 요구와 이 명세가 정하는 것

| 요구(출처) | 이 명세가 정하는 것 | 결정 |
|---|---|---|
| 「다른 rcm 제어하는곳에서 이걸 연결하면 거기에서 알아서 분배」(2026-11-24) | 연결 = 사람이 연 짝짓기 창 + 코드 한 번. 창에서 정한 풀(기본은 기본 풀)로 바로 분배가 일어난다. 코드 입력 뒤 두 번째 승인은 없다 | 280 · 286 · 294 |
| 「Rcm워커 다 한 다음에 어떻게 알려주는거까지」(2026-11-24) | 짝짓기가 끝나면 양쪽이 한 줄씩 말한다. 코디네이터: 누가 · 어디서 · 어느 풀로 붙었는지와 폐기 명령. 워커: 붙었다 · 풀 · 받을 프리셋 수 · 결과는 코디네이터로 가고 요청자의 `rcm wait` 가 받는다. 결과 알림 자체는 M5t-a PR5 · M5t-b 범위 | 286 · 296 |
| 「민감데이터나 API 키 등 그런것들은 없어야해 … public」(2026-11-25) | 코드 · 코드 키 · 토큰 · `pair_secret` · `pair.key` · 세션 id 를 어떤 출력 · 로그 · 응답 · 상태 파일에도 싣지 않는다(PI17 · PI39). 워커의 교환 요청에 코드가 없다(301 · PI21 · PI34). 코드를 망으로 내는 것은 `rcm pair` 의 201 응답 하나이고, 코디네이터 밖에서 치면 평문 경고를 낸다(V2-7). 시험 값은 실행 중에 만든다. §8 | 291 · 296 · 301 · 314 · 319 |
| 결정 252(M5t-c 는 위협 모델부터) | §4.1 이 첫 절이다. 나머지 설계 칸마다 위협 행 id 와 시나리오 id 를 단다 | — |
| 결정 255(같은 이름 세션 하나, 새 세션이 옛 세션을 펜싱) | 짝지은 워커는 펜싱하는 새 세션(등록)에도 신원 증명이 필요하다. 탈취 토큰만으로 정당한 워커를 펜싱하지 못한다. 토큰과 `pair_secret` 을 함께 복사한 경우는 막지 않는다(302) | 287 · 302 |

### 이미 정해진 것

인용한 결정은 바꾸지 않는다.

- 결정 4(`PLAN.md:639`)는 「**클라이언트별 bearer 토큰**(서버엔 sha256 만)」이다. 유지한다.
- 결정 5(`PLAN.md:640`)는 「TLS 는 서버가 안 한다」이다. 유지하고 남는 위험은 결정 282 에 적는다.
- 결정 33(`PLAN.md:669`)과 34(`PLAN.md:670`)는 「TXT 에 `v`·`name`·`lanes` 만 넣는다」이다. 유지한다. mDNS 광고의 TXT 에
  `server_id` 를 더하지 않는다. 3판과 다른 점이며 §12 에 적었다.
- 결정 169(`follow.py:12-16`)는 따라가기의 sha256 이 「위조를 막는 장치가 아니다」라고 했다. 따라가기는 클라이언트가
  서버 버전에 맞는 client wheel 을 내려받아 실행하는 동작이다. 결정 169 는 유지하고 짝지은 머신은 hello 뒤에만 따라간다.
- 결정 254(`local_only`)는 #266 으로 dev 에 머지됐다. 짝짓기는 그 후보 함수를 우회하지 않는다(PI15).
- M5t-b 의 결정 258(펜싱은 종료 0, 프로토콜 불일치는 종료 3), 260(프로세스당 세션 id), 265(세션 모델)와 DB v26 을
  전제로 한다. 근거는 F30~F37 이다.
- 결정 253(`docs/m5t-workplan.md:1042`)은 「배포 · 스토어처럼 두 번 돌면 안 되는 프리셋에만 표식을 두고 … 게이트 · 시험 같은
  나머지는 계속 돈다」이다. 신원 정지 동안 취소가 닿지 않을 때도 이 결정을 그대로 상속한다(318, F53 · F54).
- M5t-b 2판의 등록(register) 순서를 전제로 한다(F30). 증명 검사는 그 순서의 4 자리다.

### 이 명세가 뒤집거나 좁히는 것

1라운드 지적(V1-5 · V2-6)으로 더한 소절이고 3판에서 표를 전부 다시 썼다(V1-1 · V1-2). 이미 적힌 문장 가운데 이 명세가
뒤집거나 좁히는 것과 그 문장을 언제 고치는지 적는다. 3판은 아래 자리를 다시 훑었다.

- `docs/m5t-workplan.md` 의 결정 239~255, TR · TD 표, §4.5 · §4.7, 기존 결정 표
- `docs/m5n-workplan.md` §9 · §13 과 `docs/m5q-workplan.md` 의 B질문16
- `docs/configuration.md` · README · `docs/usage.ko.md` · `docs/operating.md` 의 처음 설정과 서버 찾기
- `templates/worker.toml` · `examples/worker.toml` 과 그 잠금 시험 `tests/test_cli_init_worker.py`
- dev(핀 D)의 `rcm worker --check` · `rcm doctor` 와 CHANGELOG `[Unreleased]`
- 이 명세 1판 · 2판의 결정

「이 명세」 칸은 §4.3 의 J 번호와 A 번호로 가리킨다.

| # | 무엇 | 지금 문장(출처) | 이 명세 | 언제 고치나 | 결정 |
|---|---|---|---|---|---|
| R1 | PLAN 「보안」 쓰기 인증 | 「**쓰기(`POST /jobs`·업로드·취소)는 인증 필수.** `none` 은 없다.」(PLAN.md:243, 2026-09-04 오너 결정) | 익명 POST 둘을 더한다: `POST /pair/hello` · `POST /pair/exchange`. 오늘 이미 익명인 POST(`read_auth = none` 의 `POST /api/eta` · `POST /shelf/<n>/restore`, F57)는 그대로. 익명 쓰기 전체는 §4.7 「익명 쓰기」 표 | PR0 이 PLAN.md:243 에 E2 문장. PR1 의 PI20 이 §4.7 표와 대조(V1-11) | 280 · 297 |
| R2 | M5n §13 「안 만드는 것」 | 「토큰 자동 발급 · 자동 가입. 토큰은 사람이 옮긴다(보안 결정 유지).」(`docs/m5n-workplan.md:650`) | 사람이 연 짧은 수명 · 한 번 쓰는 창 안에서만 | PR0(E5) | 280 |
| R3 | 부모 명세 M8 | 「등록 전 프로토콜 확인, 서비스가 되살리지 않는 종료 코드」(`docs/m5t-workplan.md:1088`) | 되살리지 않는 종료(0)는 펜싱(M5t-b)과 J6 · J7 뿐. J2~J5 · J8 은 종료가 아니라 프로세스 안 재시도 | 고치지 않는다(얼었다). §9 · §12 | 303 · 317 |
| R4 | M5t-b 명세의 M5t-c 종료 문장 | 1판 §4.7 「M5t-c 도 워커 종료 3 을 쓴다」, 2판 F52 · §4.7 · §9 「확실한 신원 불일치(MAC 틀림 · 서버가 짝지은 적 없다고 답함)는 종료 0」 | M5t-c 는 종료 3 을 쓰지 않는다. 종료 0 은 J6 · J7 뿐 — MAC 이 틀린 hello 는 재시도(317) | 메인 세션이 M5t-b 다음 판에서 맞춘다. #270 이 먼저 머지되면 M5t-c PR0 이 §12 에 대체를 적는다(Q-E · Q-E′) | 303 · 317 |
| R5 | 1판 결정 295 「기동 실패는 종료 3」 | 1판 §11 295 | 303 · 317 로 개정 | 이 판 | 295 · 303 · 317 |
| R6 | 2판의 303 해석 · 305 (a) | 2판 §1 · §4.3 · §11 305: MAC 이 틀린 hello · hello 421 · 확인된 서버의 401 · 등록 `pair_mismatch` 는 「확실」 → 종료 0 | 확실은 J6 · J7 뿐. MAC 이 틀린 hello(J2) · 421(J3)은 인증되지 않은 답이라 재시도 + 경고 + FAIL. `pair_mismatch` 는 없다(313) | 이 판 | 305 · 313 · 317 |
| R7 | M5t-a 결정 246 · 「첫 실행 안전」 · TD10 · TD28 · B-M1 | 「mDNS 로 찾은 서버는 이름과 주소를 먼저 보이고 인증된 요청과 프로브를 보내기 전에 사람의 확인을 받는다 … 비TTY 이고 확인을 받지 못했으면 종료 3 이다. `rcm worker --check` 에도 같은 규칙을 건다」(`docs/m5t-workplan.md:513`, 결정 246 `:1037`, TD10 `:837`, TD28 `:855`, B-M1 `:1162`, §4.7 `:660-662`). dev 가 구현(F45 · F46 · F47) · CHANGELOG `[Unreleased]` Breaking(F49) | 짝지은 `auto`: hello 통과(J1, 후보가 정확히 하나)가 사람 확인을 대신한다 — 묻지 않는다(A2 · A4 · A6 · A9). 짝짓지 않은 `auto` 워커: `rcm worker` · `--check` 는 mDNS 질의 전 종료 2(A1 · A3), `rcm doctor` 는 mDNS · 요청 없이 FAIL(A5). 토큰 있는 짝짓지 않은 `auto` 클라이언트: 285 대로 종료 2, doctor FAIL(A7). 토큰 없는 `auto` 클라이언트: 246 그대로(A8) | PR3 이 `tests/test_doctor_cli.py` 의 TD10 · TD28 · TD48 시험과 `tests/test_worker_discovery.py` 의 확인 fixture 를 고친다(F47). CHANGELOG 는 `Changed` 한 항목이 #268 Breaking 의 「asks before it uses a server found on the network」를 이것으로 바꾼다고 적는다 | 315 · 283 · 285 |
| R8 | M5t-a TD48 | 「worker.toml 에 `server = "auto"` → doctor 가 warn 한다」(`docs/m5t-workplan.md:874`, dev `doctor.py:1920-1924`) | 짝짓지 않은 `auto` 워커는 warn 이 아니라 FAIL(A5). 짝지은 `auto` 는 warn 없음(A6) | PR3 | 315 |
| R9 | M5t-a 기존 결정 표 「`rcm worker` 의 찾기는 바꾸지 않는다」 · M5n §9 워커의 서버 찾기 | `docs/m5t-workplan.md:1003`, `docs/m5n-workplan.md:422-425`, `docs/configuration.md:1369-1376` 「Finding the server」 | 서버 찾기는 짝지은 워커에만(A1 · A2). 다시 찾기는 hello 를 통과한 후보가 정확히 하나일 때만(310) | PR3(동작) · PR4(`docs/configuration.md` 문단) | 283 · 310 · 315 |
| R10 | M5q B질문16 | 「`server = "auto"` 워커도 찾은 주소를 `RCM_SERVER` 로, 찾았다는 표시를 `RCM_FOLLOW_FOUND=1` 로 자식에게 넘긴다」(`docs/m5q-workplan.md:1394`, dev `cli.py:4406-4414`, `docs/operating.md:492-494`) | 짝지은 `auto` 워커는 그대로 넘기되 자식도 첫 토큰 요청 전에 hello 한다(새 프로세스는 「기동」, J 표). 짝짓지 않은 `auto` 는 찾기 전에 거절되므로 넘길 것이 없다 | PR3(동작) · PR5(`docs/operating.md:492-494`) | 283 · 315 |
| R11 | 처음 설정 흐름 · `rcm init worker` 기본 `auto` | `docs/configuration.md:1332-1357`(token add → `rcm init worker`(auto) → `--check` → 서비스), README.md:96-98, `docs/usage.ko.md:553-561`, `docs/m5n-workplan.md:414-416`, `templates/worker.toml:1-9`(= `examples/worker.toml`), `cmd_init_worker` 출력(`cli.py:3627-3633` A), 잠금 `tests/test_cli_init_worker.py:101-109` · `:133-142` · `:155-158`, 문서 잠금 `tests/test_docs_m5n.py:80-85` | 정본은 `rcm pair --worker`(코디네이터) + `rcm setup --role worker --pair --server URL`(워커). `rcm init worker` 는 미완성 틀: 템플릿 그대로(`server = "auto"`) 쓰고 주석 · stderr 가 「먼저 짝지으라」와 두 명령을 어느 머신에서 치는지 말한다(4.6). 수동 길(명시 URL + `rcm token add --worker`)은 「짝짓지 않는 길」로 남긴다 | PR3: 템플릿 · 예시 · `cmd_init_worker` 출력 · `tests/test_cli_init_worker.py`(바이트 같음 잠금이 둘을 함께 요구). PR4: `docs/configuration.md` 「Second build machine」 · 「Finding the server」 · README. PR5: `docs/usage.ko.md` · `docs/operating.md`. `tests/test_docs_m5n.py:80-85` 의 낱말(`rcm init worker` · `--service-file` · `server_name` · `server = "auto"`)은 문서 묶음에 남긴다 | 316 |
| R12 | M5t-a TR27 | 「`coordinator` 나 `coordinator-worker` 의 setup 이 출력하는 토큰 명령 — client 토큰 명령과 함께 `rcm token add <이름> --worker` 도 출력한다」(`docs/m5t-workplan.md:808`) | 워커를 붙이는 명령으로 `rcm pair --worker` 를 먼저 찍고, `rcm token add <이름> --worker` 는 「짝짓지 않는 길(명시 URL)」로 함께 찍는다 | PR4(M5t-a PR4 의 setup 이 머지된 뒤) | 316 |
| R13 | M5t-a TR13 | setup 의 마지막 줄 「서비스를 띄우고 토큰을 넣은 뒤 `rcm doctor --role <r>`」(`:794`) | `--pair` 로 쓴 설정에는 토큰이 이미 있다 — 「서비스를 띄운 뒤 `rcm doctor --role worker`」 | PR4 | 316 · 296 |
| R14 | M5t-a TR35 | worker `--yes` 는 `--server` · `--pool` · `--lanes` 필수(`:816`) | `--pair` 면 `--pool` 이 필요 없다(창이 정한다), 주면 창의 풀과 다를 때 종료 2(2판 그대로) | PR4 | 296 |
| R15 | M5t-a TR21 · TR28 | TR21 「client 토큰 명령 … `--admin` 이 없다」(`:802`), TR28 setup 종료 0/1/2(`:809`) | 그대로. `rcm pair` 의 admin 토큰은 `rcm pair` 의 오류 문장이 얻는 법을 말한다(292). setup `--pair` 의 모든 교환 오류는 TR28 의 1 에 든다(4.6 교환 오류 표) | — | 292 · 296 |
| R16 | 결정 301 의 공식 | 「`HMAC(SHA256(code), enc("rcm-pair-request-v1", kind, host_name, worker_nonce))`」(decisions 3차) | 코드 키를 `SHA256(code)` 대신 `scrypt(code, …)`(M7) | 이 판(319, 6차 위임 결정이 확정) | 301 · 319 |
| R17 | 2판 §4.4 `host_name` 상한 | 「문자열 255자 이하」 | 0~64 코드 포인트, 홀로 선 서러게이트 없음(1 KiB 본문 상한 안에 든다) | 이 판 | 319 |
| R18 | 2판 §4.6 doctor | 「워커 프로세스의 상태를 읽지 않는다(따로 도는 프로세스라 볼 수 없다)」 | 상태 파일을 읽는다(4.3 읽는 표) | 이 판 | 314 |
| R19 | 2판 §4.3 · 305 (b) | 「도는 중 확실한 불일치면 잡을 비우고 … 그 사이 hello 가 `ok` 가 되면 돌아간다」 | 끝내는 중은 J6 · J7 에서만 생기고 hello 가 통과해도 돌아가지 않는다(토큰 · 증명 문제라 hello 로 풀리지 않는다) | 이 판 | 305 · 317 |
| R20 | 결정 253 의 범위 | 「게이트 · 시험 같은 나머지는 계속 돈다」 | 신원 정지 동안 취소가 닿지 않는 되돌릴 수 있는 잡도 끝까지 돈다 — 새 결정이 아니라 상속으로 적는다 | 이 판 | 318 |

### 오너 위임 결정 1차

오너가 위임한 결정을 메인 세션이 Codex 와 논의해 먼저 조치했다(2026-11-26). 1차 결정은 §11 의 280~289 로 그대로 간다.

| 질문 | 결정 원문(decisions-codex.md) |
|---|---|
| Q1 | 개정하되 사람이 연 짧은 수명 · 한 번 쓰는 짝짓기 창 안에서만. M5n §13 해당 줄에 개정 날짜를 적는다 |
| Q12 | DB 밖 0600 코디네이터 신원 키 하나에서 워커마다 파생. DB 사본 · 백업으로 서버를 사칭할 수 없다. 키를 잃거나 바꾸면 다시 짝짓기 |
| Q13 | 받아들인다. 요청마다 서명은 하지 않는다. 남는 위험 표에 적고 LAN · 이름 URL 은 경고, Tailscale 을 권한다 |
| N1 | 신원이 없는 옛 워커의 `server = "auto"` 는 거절하고 다시 짝짓는 법을 안내한다(첫 URL 고정은 이미 속았다면 공격자를 고정할 뿐). 명시 URL 의 옛 명시 토큰 워커는 그대로 동작 |
| N2 | 묶이지 않은 옛 토큰: 등록 때 말한 풀로 등록, 그 뒤 `/worker/spec` 은 그 워커가 등록한 풀로 거른다. 「—」로 보인다. 기본 풀이나 무제한으로 보지 않는다 |
| N3 | 짝지은 클라이언트도 URL 과 신원 비밀을 저장하고, health · 따라가기 · 인증 요청 전에 신원을 확인하며 조용한 재발견을 하지 않는다. 옛 명시 토큰 클라이언트는 그대로이되 `auto` 면 N1 규칙 |
| N4 | 없음. 즉시 발급하고, 붙은 머신 · 주소 · kind · 풀 · 시각 · 폐기 명령을 눈에 띄게 남긴다 |
| N5 | 짝지은 워커의 재등록은 새 도전값으로 신원 비밀 증명을 요구한다(토큰만으로 정당한 워커를 펜싱 · `lost` 시키지 못하게). 옛 명시 토큰은 호환상 토큰만 |
| N6 | 받아들인다. 실패는 출처가 아니라 창에 센다(분산 · NAT). 닫히면 코디네이터에 즉시 이유와 새 창이 필요하다는 이벤트 |
| + 키 수명 주기 | 키를 잃거나 일부러 바꾸면 모든 신원 증명이 무효이고 다시 짝짓기. DB 만 복원 · 복제해 두 번째 신뢰 서버가 생기면 안 된다. DB 와 키를 함께 두 살아 있는 코디네이터에 복원하는 것은 지원하지 않고 문서화된 신원 재설정을 요구한다 |

### 오너 위임 결정 2차

2차 결정은 이 명세의 재료가 남긴 질문에 대한 답이다. 재료는 세 질문을 Q-A · Q-B · Q-C 로, 위임 결정 파일은 P1-Q1 ·
P1-Q2 · P1-Q3 으로 부른다. 이 문서는 Q-A · Q-B · Q-C 를 쓴다.

| 질문 | 결정 |
|---|---|
| Q-A(P1-Q1) 토큰 없는 클라이언트가 `server = "auto"`(또는 빈 값)로 mDNS 에서 찾은 서버의 버전을 따라가 그 서버의 client wheel 을 내려받아 실행한다 | 바꾼다. 찾기는 그대로 두고, 내려받기와 실행은 검증된 `pair_secret` 이 있을 때만 한다. 명시 URL 은 사용자의 신뢰 결정으로 남긴다. Codex 는 클라이언트에서의 임의 코드 실행이 남는 위험으로 받아들이기엔 무겁다고 봤다 |
| Q-B(P1-Q2) 운영에서 옛 토큰 + `server = "auto"` 를 쓰는 머신 | 메인 세션이 이 Mac 의 운영 설정을 읽기 전용으로 확인했다. 워커 · 클라이언트 설정 모두 명시 URL 을 쓰고 `auto` 가 없다. 다른 머신은 운영 반영 전에 §6 17 로 확인한다 |
| Q-C(P1-Q3) M5t-b 와 맞추기 | M5t-b 의 결정(펜싱 종료 0, 프로토콜 불일치 종료 3, 프로세스당 세션 id, DB v26)을 전제로 적는다. 세션을 여는 호출의 이름은 「M5t-b 의 세션 PR 이 정한 이름」으로 의존을 적는다. 종료 3 의 두 경우(프로토콜 불일치 · 서버 신원 미확인)는 모두 「안전한 세션을 못 열었으니 감독자가 다시 시도」라는 뜻이고 진단 문구만 따로 둔다 |
| 290~297 · 299 | 추천값(TXT 그대로 · 창은 메모리 · `rcm pair` 는 admin 토큰 · 신원 미확인 워커 종료 3 · `rcm token add` 출력 그대로 · `POST /pair/hello` · v27 은 M5t-b v26 뒤 · 그 밖)에 Codex 가 모두 동의했다. 확정한다 |
| 298 | lane(PR1~PR4 full, PR5~PR6 short)에 Codex 가 동의했다. 확정한다 |

Q-C 의 「종료 3 의 두 경우(프로토콜 불일치 · 서버 신원 미확인)」 문장과 290~297 행의 「신원 미확인 워커 종료 3」은 3차
결정 303 이 대체한다. 결정 295 도 그에 맞춰 개정했다(§11).

### 오너 위임 결정 3차

명세 1라운드 지적에 대한 결정이다(2026-11-26). §11 의 301~303 이 된다.

| # | 질문 | 결정 원문(decisions-codex.md 3차, 그대로) |
|---|---|---|
| 301 | 첫 접촉(교환 URL)이 사칭 서버면 코드를 보낸 워커가 그 서버를 영구히 믿는다 | 바꾼다. 응답에 코드 키 MAC 을 붙이는 것만으로는 소용없다(요청이 코드를 먼저 드러낸다). 요청은 코드 대신 `worker_nonce` 와 `HMAC(SHA256(code), enc("rcm-pair-request-v1", kind, host_name, worker_nonce))` 를 보내고, 응답은 두 nonce 와 전체 페이로드에 대한 코드 키 MAC 을 싣는다. 경로 위의 능동 중계 · 평문 자격 증명 탈취는 T4 로 남긴다. 5회 제한은 온라인 추측만 막고, 관찰한 증명으로 짧은 창 동안 오프라인 추측이 가능함을 적는다 |
| 302 | 복제된 worker.toml(토큰 + pair_secret) | 받아들인다. T6 문구를 고친다: 토큰만 샌 경우는 막고, 파일 복사는 워커 신원 복제 = 로컬 침해로 남는 위험. 0600 은 다른 보통 사용자만 막는다(소유자 · root · 악성코드 · 백업 도난은 못 막는다) |
| 303 | 종료 코드와 부모 명세 M8(「서비스가 되살리지 않는 종료 코드」) | 메인 세션 선택: 확실한 신원 불일치(MAC 이 틀림 · 서버가 짝지은 적 없다고 답함)는 종료 0 + 「다시 짝지으라」 안내(서비스가 되살리지 않는다 — launchd 는 코드별 제어가 없어 0 만 되살리지 않는다). 연결 실패 · 5xx · 503 은 신원 판단이 아니므로 프로세스 안에서 물러서며 다시 시도하고 종료하지 않는다. 프로토콜 불일치는 M5t-b 대로 3(되살림 — 재시작이 버전 따라가기를 부른다). M8 은 「펜싱 · 확실한 신원 거절」로 좁힌다고 §9 · §12 에 적는다 |

2판은 303 의 「MAC 이 틀림」을 hello 200 의 `mac` 어긋남으로 읽었다. 「서버가 짝지은 적 없다고 답함」은 확인된 서버의 401 과
등록 `pair_mismatch` 로 읽었고 hello 421 도 확실한 거절로 봤다(305). 5차 위임 결정 313 · 317 이 이 해석을 바꿨다.
`pair_mismatch` 는 만들 수 없는 값이라 지웠다. MAC 이 틀린 hello 와 421 은 누구나 보낼 수 있는 답이라 확실이 아니다. 확실한
것은 hello 를 통과한 서버의 401 과 이어지는 `bad_mac` 뿐이다(§4.3 J6 · J7).

### 오너 위임 결정 4차

2판 재료가 남긴 질문에 대한 결정이다(2026-11-26). §11 의 304~312 를 확정했다. 304 와 312 는 Codex 의견대로 바꿨다.
바뀐 내용은 §4.3 · §4.6 · §4.7 · §4.8 · §7 · §9 · §11 에 반영했다.

| # | 질문 | 결정 |
|---|---|---|
| 304 | 303 이 분류하지 않은 hello 응답(3xx · 404 · 405 · 다른 4xx · 깨진 200 · `v` ≠ 1) | 바꾼다. 프로세스 안 재시도(토큰 · 일 0)는 둔다. 「주소 · 종류마다 로그 한 줄」은 영원히 호환되지 않는 서버를 숨기므로 속도를 제한한 경고를 주기적으로 되풀이하고 `rcm worker --check` 와 서비스 상태(doctor)에 「서버 신원 미확인」 상태를 드러낸다 |
| 305 (a)~(d) | 확실한 불일치의 범위, 도는 잡 처리, 짝지은 워커의 401 은 종료 0 · 옛 워커는 2, 클라이언트 종료 코드 | 모두 그대로 둔다. 다만 (b) 의 잡 비우기 동안 기존 시한 · 취소 집행은 그대로이고 신원 때문에 멈춘 상태를 계속 눈에 띄게 로그에 남긴다. 3판: (a) 는 317 이 좁혔다 |
| 306 | `pair.key` 가 없고 짝지은 활성 토큰이 있으면 기동 거절, 재설정은 `rcm token revoke` | 그대로 확정 |
| 307~311 | 코드 알파벳 사전 검사 · MAC 에 넣는 `dialed` 필드 · 3xx 를 따르지 않음 · 다시 찾기 후보는 정확히 하나 · 순수 `core` | 그대로 확정 |
| 312 | `rcm pair` 종료 코드 | 바꾼다. 재시작 뒤 창이 없다는 것은 쓰이지 않았다는 증거가 아니다(재시작 직전에 끝났을 수 있다). 창 id 와 이어진 영속 상태가 완료나 미사용을 증명하지 않으면 3 이다. 그 경쟁을 1 로 보고하지 않는다 |
| Q-E | M5t-b §4.7 의 「M5t-c 도 종료 3」 문장 | M5t-b 2판에서 고친다. #270 이 먼저 머지되면 M5t-c §12 에 303 이 그 문장을 대체한다고 적는다 |

### 오너 위임 결정 5차

명세 2라운드 HIGH 에 대한 결정이다(2026-11-26). §11 의 313~318 이 된다. 결정 칸은 decisions-codex.md 5차 표의 원문 그대로다.

| # | 질문 | 결정 원문(decisions-codex.md 5차, 그대로) |
|---|---|---|
| 313 | `pair_mismatch` 는 만들 수 없다(challenge 에 pair_id 가 없다) | `pair_mismatch` 를 지운다. 서버는 토큰의 pair_id 로 증명을 보고 틀리면 `bad_mac`. 토큰과 pair_secret 이 어긋난 워커는 `bad_mac` 을 계속 받는다(314 로 드러난다) |
| 314 | hello 는 통과하는데 등록 재시도가 끝없이 돌면 아무도 모른다 | Codex 대로. 워커가 data_dir 에 신원 · 등록 상태 파일을 쓰고 `rcm worker --check` · `rcm doctor` 가 읽는다. 등록 실패도 10분 반복 경고 |
| 315 | M5t-a 결정 246(mDNS 로 찾은 서버는 사람 확인, 비 TTY 종료 3)을 조용히 뒤집었다 | 짝지은 `auto` 는 hello 통과가 사람 확인을 대신한다. 짝짓지 않은 `auto` 워커는 mDNS 질의 전에 종료 2. 둘 다 §1 뒤집기 표에 적고 M5t-a TD10 · TD28 시험을 PR3 파일에 넣는다 |
| 316 | `rcm init worker` 가 `auto` 를 써 문서의 처음 설정 흐름이 깨진다 | 처음 설정의 정본은 `rcm pair`(코디네이터) + `rcm setup --role worker --pair`(워커). `rcm init worker` 는 미완성 틀로 남기고 주석 · 출력이 「먼저 짝지으라」. 템플릿 · 예시 · tests/test_cli_init_worker.py 를 동작을 바꾸는 PR 파일에 |
| 317 | 인증되지 않은 답 하나(나쁜 hello mac, 421)로 워커가 종료 0 으로 영구 정지 | Codex 대로. 305 (a) 를 좁힌다: 종료 0 은 「hello 를 통과한 서버가 401 · 등록 `bad_mac` 을 계속 준다」 뿐 — 그것도 314 상태로 드러낸다 |
| 318 | 신원 정지 동안 취소가 워커에 닿지 않는다 | Codex 대로, PI36 이 관찰 |

같은 표의 Codex 칸에는 조건이 붙어 있었고 이 명세는 그 조건을 따른다. 314 에는 「상태 파일은 원자적 0600 쓰기, 비밀 없음,
`updated_at` + 세션 · 프로세스 신원, 오래된 파일 처리. `expired` · `reused` 재시도 중은 unknown, 계속되는 `bad_mac` 은
FAIL」이 붙었다. 316 에는 「어느 명령을 코디네이터에서 · 워커에서 치는지 보이고, 역할 추론이 보장되지 않으면
`rcm setup --role worker --pair` 로 쓴다」가 붙었다. 317 에는 「반복 · 경과 시간은 인증이 아니다. 나쁜 hello mac · 421 은
절대 「확실」로 올리지 않고 계속 재시도 + 반복 경고 + FAIL 상태. 종료 0 은 인증된 신원 확인 뒤에 받은 증거(검증된 서버가
토큰을 거절)에서만」이 붙었다. 318 에는 「서버 유예가 잡을 lost 로 닫고 irreversible 잡은 스스로 멈춰 unknown. 되돌릴 수
있는 잡은 취소 없이 계속 도는 것을 결정 253 의 상속으로 명시」가 붙었다.

5차 결정은 세부 몇 가지를 명세에 맡겼다. 314 의 파일 자리와 키, 오래됨 기준(60초 · 180초), 「계속」을 3번으로 보는 것(J7)이
하나다. 315 의 doctor · 클라이언트 칸(A5 · A7 · A8)과 316 의 템플릿 문구도 그렇다. 6차 위임 결정이 이 세부를 이 명세에 적은
값대로 확정했다.

### 오너 위임 결정 6차

3판 재료가 남긴 질문에 대한 결정이다(2026-11-26). 질문은 모두 이 명세에 적은 값대로 확정됐다. §11 의 319 와 314 · 315 의
오너 확인 칸이 여기서 나온다. 결정 칸은 decisions-codex.md 6차 표의 원문 그대로다. 같은 표의 Q-E′ 는 §12 에 적었다.

| # | 질문 | 결정 원문(decisions-codex.md 6차, 그대로) |
|---|---|---|
| 319 | 2라운드 MED · LOW 세부 묶음(코드 키 scrypt n=2^15 r=8 p=1 — 301 의 SHA256 을 바꿈, 고정 400 bad_pair_request 미계수, setup 오류 표, 쓴 challenge 는 모든 검사 뒤 기록, 클라이언트 창 null, GET /pair 1시간 · 20개, os.link 실패는 기동 거절 등) | 확정(위임) |
| 314 · 317 값 | 상태 파일 pair-state/<pair_id>.json(60초 갱신, 180초 지나거나 60초 넘게 미래면 오래됨), 경고 즉시 · 그 뒤 600초, bad_mac 연속 3회(각각 직전 hello 통과) = J7 종료 0, 401 은 바로 뒤 hello 가 통과할 때만 J6 | 확정(위임) |
| 315 칸 | 짝짓지 않은 auto 워커 → doctor FAIL 종료 1(M5t-a 는 warn), 토큰 든 짝짓지 않은 auto 클라이언트는 mDNS 전에 종료 2, 토큰 없는 auto 클라이언트는 doctor 에서 246 유지, 짝지은 auto 의 통과 후보 0 · 2+ → 워커 재시도 · --check FAIL · doctor 3 · 클라이언트 2 | 확정(위임). 옛 워커 루프는 §9 와 운영 반영 체크리스트(§6 17)에 |

315 칸에서 Codex 는 상태가 드러나지 않는 경우 하나를 지적했다. 「새로 짝지은 토큰을 든 옛 워커가 pair_proof_required 로
끝나고 5~10초마다 다시 뜨며 상태 파일을 쓰지 않는다 — 배포 때 찾아 올리거나 다시 짝지어야 한다」. 이 명세는 그 루프를 §9 의
남는 위험과 §6 의 17 에 넣었다.

### 이 명세가 하지 않는 것

- 요청마다 서명하지 않고 TLS 도 넣지 않는다(결정 5 · 282). 같은 IP 가로채기와 평문 엿보기, 경로 위의 능동 중계는 남는
  위험이다.
- 읽기 토큰 모드는 만들지 않는다. `read_auth` 의 새 값이며 M5t-a Q14 에서 마일스톤 밖으로 뺐다.
- `rcm token add --worker` 에 신원 비밀이나 `--pool` 을 더하지 않는다. 수동 토큰은 오늘 그대로이고 옛 토큰 규칙 N1 · N2 를
  따른다.
- TXT 에 `server_id` 를 더하지 않는다. 서버를 고르는 일은 hello 가 한다.
- 웹 UI 에는 짝짓기 화면을 두지 않는다. CLI 만 둔다.
- 워커 세션과 로그 오프셋, 끊김 유예, 보고 큐는 M5t-b 의 일이다. 이 명세는 두 점에서만 M5t-b 와 만난다. 세션을 여는
  호출(`POST /worker/register`)에 증명 검사를 거는 것과, 확인되지 않은 서버에 보고를 보내지 않는 것이다.
- 키 재설정 전용 명령(`rcm pair --reset` 같은 것)은 만들지 않는다. 재설정은 문서화된 절차를 따른다. 서비스를 멈춘 뒤
  짝지은 토큰을 `rcm token revoke` 로 폐기한다. 폐기할 토큰은 `rcm token list` 의 `paired from` 열로 찾는다. 그다음
  `<data_dir>/pair.key` 를 지우고 다시 시작해 다시 짝짓는다(289 · 306).
- `worker.toml` 을 통째로 복사하는 것은 막지 않는다(302). 파일 모드 0600 은 다른 보통 사용자만 막는다.
- 신원 정지 동안 취소를 워커에 닿게 하는 새 길은 만들지 않는다. hello 에 취소 목록을 싣는 것 같은 길이다. hello 는 상태 없는
  익명 라우트로 남는다(290 · 318).

## 2. 범위

| 누가 | 하는 일 |
|---|---|
| rcm(이 명세 PR0~PR6) | PR0: 명세 + PLAN.md · M5n §13 편집. 서버: `pair.key` · 짝짓기 창(메모리) · `/pair*` 라우트 · DB v27(토큰 칸) · 등록의 풀 · 증명 검사 · `/worker/spec` 풀 거르기. 워커 · 클라이언트: `pair_secret` 설정 키 · 토큰 전 hello · 응답 분류와 종료 코드 · 리디렉트 안 따름 · 옛 토큰 + `auto` 거절 · `rcm pair` · `rcm setup --pair` · `rcm token list` 열 · 문서 · 실기 |
| M5t-a(다른 명세, `docs/m5t-workplan.md`) | **M5t-a 전체(PR1~PR7, v0.11.0)가 dev 에 머지된 뒤** 이 명세의 구현 PR 이 시작한다(V1-8). 이유: M5t-a PR2(#268, `rcm doctor` · `worker --check` 두 줄 · `/worker/spec` 키 · `NO_FOLLOW_COMMANDS` 의 `doctor`)와 PR3(`cli.py` · `capabilities.py`, `rcm check` · `lanes = 0`)이 이 명세 PR2 · PR3 이 고치는 `worker_spec` · `_worker_check` · `cmd_check` 와 같은 함수를 고친다(`docs/m5t-workplan.md:692-693`). PR4(setup)는 이 명세 PR4 의 바탕이다(F26). #268 은 이제 dev 에 머지됐으므로(96a8bd2) 그 동작은 F45~F49 가 그 커밋에서 인용한다 |
| M5t-b(PR #270, `docs/m5tb-workplan.md` 2판) | DB v26 · 세션 · 펜싱 · 프로토콜 2 · 종료 0/3(M5t-b PR1), 로그 오프셋 · 보고 큐(M5t-b PR2), 화면 사유 · `rcm wait` · `rcm logs` 줄(M5t-b PR3). 이 명세 PR1 · PR2 는 M5t-b PR1 머지 뒤, PR3 은 M5t-b PR2 · PR3 머지 뒤 시작한다(F35, V1-10) |
| 하류 앱 프로젝트 | 바꿀 것 없음. 명시 URL 의 옛 워커는 그대로 돈다 |
| 메인 세션 | 운영 설정 읽기 전용 확인(Q-B: 이 Mac 은 끝. 다른 머신은 §6 17 — **반영을 막는다**, M3 · F50) · M5t-b 명세 문장 맞추기(Q-E · Q-E′) · 결정 페이지(5차 + 319 확인) · 검증자에게 핀 D 의 스냅숏도 준다(F45~F60) · PR0 마지막 커밋(PLAN 편집) · 릴리스 · 운영 반영 |
| 오너 | 위임(2026-11-26). 운영 반영은 현행 규칙 |

## 3. 실측과 코드 제약

핀은 둘이다. 코드 핀 A 는 `fdf196d4ef682612857fb12df9162f67545b63f0` 이고 dev 핀 D 는
`96a8bd25fa0b0457f6bc75a336c43aeb2a4724ec` 이다. 근거 칸에 「D」가 있는 행은 핀 D 의 줄 번호이고 「A = D」는 두 핀에서 같은
줄이다. 그 밖의 행은 핀 A 의 줄 번호다. 다른 절은 이 표의 F 번호로 사실을 가리킨다. F1~F29 는 1판의 사실이다. F30~F37 은
M5t-b 명세에서 옮긴 사실이고 F38~F44 는 2판에서 확인한 사실이다. 3판은 F10 · F30 · F35 · F41 을 고치고 F45~F60 을 새로
더했다. F30 · F35 · F54 · F55 는 `docs/m5tb-workplan.md` 2판을 절 번호로 인용한다.

| # | 사실 | 근거 | 설계에 미치는 것 |
|---|---|---|---|
| F1 | `tokens(name TEXT PRIMARY KEY, sha256 UNIQUE, admin, created_at, revoked_at, kind)`. 폐기된 이름도 행이 남아 이름을 점유한다 | `store.py:314-321` | `--replace` 는 같은 행을 한 문장 UPDATE 로 바꾼다(293). 이미 있는 이름(폐기 포함)은 `--replace` 없이 거절(PI12) |
| F2 | `add_token` 은 32바이트 `token_urlsafe` 를 한 번 돌려주고 sha256 만 저장. `verify_token` 은 `SELECT name, sha256, admin, created_at, kind FROM tokens WHERE revoked_at IS NULL` 을 돌며 `hmac.compare_digest`. `TokenInfo` 칸은 `name · admin · created_at · revoked_at · kind` 다섯 | `store.py:4045-4065`, `:4067-4082`, `TokenInfo` `:897-902` | 짝짓기 발급도 같은 저장 규칙. 폐기 즉시 401(PI19). `TokenInfo` 에 `pool` · `pair_id` · `paired_host` · `paired_addr`(기본 None)를 더하고 `verify_token` · `list_tokens` 의 SELECT 에 넣는다 — 안 그러면 등록 검사 · spec 거르기 · 증명 검사가 칸을 못 본다(V2-13, PR1) |
| F3 | `DB_VERSION = 25`, 마이그레이션은 `_MIGRATIONS[n]` 과 `user_version`, 더 새 DB 는 `newer_database_message` 로 거절 | `store.py:88`, `:661-673`, `:1209-1248`, `:778-797` | v27 은 열 넷 + 고유 색인 하나. M5t-b 가 v26 을 먼저 쓴다(F34) |
| F4 | `rcm token` 은 Store 를 직접 열고(열면서 마이그레이션), `add` 는 토큰을 stdout 한 줄로, 안내는 stderr. `list` 는 `name kind created revoked` 네 열 | `cli.py:4736-4748`, `:4752-4765`, `:4766-4770` | `rcm token add` 는 바꾸지 않는다(PI26). `list` 는 열을 뒤에만 붙인다(PI27). 신원 재설정의 폐기도 이 명령(306) |
| F5 | 가드의 DB 를 여는 명령은 `token` 뿐, 마이그레이션 명령은 `serve` · `worker` · `token` | `tools/guard_production.py:85`, `:89` | `rcm pair` 는 DB 를 열지 않고 HTTP + admin 토큰 → 가드 목록 그대로(292, PI28) |
| F6 | Bearer 인증 `authenticate`, 읽기 `authenticate_read`, 클라이언트 API 는 worker 토큰 403, admin 은 `require_admin` | `server.py:1076-1105`, `:1111-1122` | `POST /pair` · `GET /pair` · `POST /pair/cancel` 은 `require_admin`(PI7) |
| F7 | 라우터는 if 사슬. `/worker/` 로 시작하면 `_worker_route` 가 인증을 먼저 한다 | `server.py:5745-5813`, `:6185-6187`, `:262`, `:263` | 새 익명 라우트는 `/worker/` 밖. PI20 은 PLAN 「서버 API」 표를 파싱해 순회 |
| F8 | `/api/health` 는 익명. 본문에 `version` · `client_wheel` | `server.py:5762-5766`, `:5395-5430` | 짝지은 머신은 hello 뒤에만 health 를 읽는다(PI2) |
| F9 | `/worker/spec` 은 worker 토큰이면 누구나, 프리셋마다 `env` 를 통째로 준다. `local_only` 만 뺀다(`machines_for` 로). 토큰별 거르기 없음. **M5t-a PR2 · PR3 이 이 함수를 고치고 M5t-b 가 응답에 `worker_protocol` 을 더하므로 구현 PR 이 시작할 때 자리가 옮겨져 있을 수 있다** — 구현 PR 은 그때의 `worker_spec` 을 다시 찾는다 | `capabilities.py:274-295`(`worker_spec`), `server.py:4353-4355`(`worker_spec_for`), `machines_for` `capabilities.py:484` | 풀로 거른다(294, PI14 · PI16) |
| F10 | 오늘의 등록: `pool` 은 본문에서(없으면 `default`), 버전 409 가 먼저, 그다음 `mark_lost_for_worker` 로 그 이름의 도는 잡을 `lost`. M5t-b 가 이 순서를 바꾼다(F30) | `remote_workers.py:377-434`(`:404`) A | 증명 · 풀 검사 자리는 F30 |
| F11 | `_owned_active`: 없는 잡 404 · 남의 잡 403 `not your job` · 끝난 잡 409 | `remote_workers.py:588-597` | 탈취 토큰의 남의 잡 접근은 오늘 그대로 막힌다(PI23 d). 시나리오 표가 적은 「409/404」를 「404 · 403 · 409」로 고쳤다 |
| F12 | mDNS TXT 는 `v` · `name` · `lanes`. 광고 호스트 이름은 `socket.gethostname().split(".")[0]` + `.local.` | `server.py:742-751`, `:712`, `:745` | TXT 를 바꾸지 않는다 → README 발견 문장(`README.md:412`) · `docs/operating.md:638` 그대로. 광고 이름 `<host>.local` 은 hello 의 자기 주소에 든다(290) |
| F13 | `base_url(host)`: `public_url` > 요청 Host(`_HOST_RE` 모양 · `0.0.0.0`/`::` 아님) > `server_base_url` | `server.py:1060-1072`, `:247`, `:249` | 화면 URL 계산에 재사용. 인증 판단에는 쓰지 않는다 |
| F14 | 보관함 Host 허용 목록은 `localhost` · `*.local` · `*.ts.net` · **모든 사설 IP** · `public_url` · hostname · bind 를 받는다(DNS 재바인딩 방지용) | `core/shelf.py:461-491`, `shelf_server.py:323-334` | 같은 망의 다른 사설 IP 를 받으므로 중계 방어에 못 쓴다(3라운드 근본 원인 R1 의 예). hello 는 이것을 재사용하지 않는다(290) |
| F15 | `local_ipv4s()` 는 `getaddrinfo(gethostname())` + 기본 경로 주소 — 인터페이스를 정확히 세지 못한다 | `mdns.py:105-118` | 「정확한 자기 주소 목록」(A-H3 · B-H2)을 만들지 않는다. 요청이 **도착한 소켓의 로컬 주소**(`self.connection.getsockname()`)로 판정한다. `local_ipv4s()` 는 화면에 찍을 URL 후보로만 쓴다 |
| F16 | 원격 워커: 연결 실패가 연속 `REDISCOVER_AFTER_FAILURES`(5)번이면 다시 찾은 주소로 **조용히** 옮긴다(`_connection_failed`). 다시 찾기는 설정이 발견으로 서버를 얻었을 때(`server` 가 비었거나 `auto`)만 켜진다. CLI 의 `_resolve_server` 는 찾은 서버가 **정확히 하나**일 때만 고른다 | `remote_worker.py:71`, `:653-662`, `cli.py:4544`, `:4330-4355`, `:4369-4404`, `_resolve_server` `cli.py:420-442`(하나 규칙 `:428-441`) | 짝지은 워커의 다시 찾기는 hello 를 통과한 후보가 정확히 하나일 때만 옮긴다(310, V2-11). 옛 토큰 + `auto` 는 거절(283) |
| F17 | 워커 기동 따라가기 ①은 **등록 전에** 토큰을 실은 health 를 읽는다 | `cli.py:4435-4456`, `follow.py:659-663`, `:692-696` | 짝지은 워커는 hello 를 ① 보다 먼저 한다(PI2) |
| F18 | CLI 따라가기 `_follow_first` 는 argparse 전에 설정을 읽고 mDNS 로 서버를 찾은 뒤 따라간다(토큰을 실은 health · wheel 실행) | `cli.py:5548-5594`, `:420-442` | 짝지은 클라이언트는 hello 뒤에만 따라간다. 옛 토큰 + `auto` 클라이언트 거절은 `_resolve_server` 한 곳에 건다(285) |
| F19 | `Authorization` 을 붙이는 자리가 여섯: `WorkerClient.download_tree`, `Client._request`, `download_bundle`, `download`, `events`(client.py), `follow._headers` | `client.py:133`, `:531`, `:582`, `:629`, `:905`, `follow.py:663` | 신원 확인 관문은 여섯 자리 모두를 지난다(변이 `pair-gate-skipped-in-download`) |
| F20 | 설정: `_NAME_RE = ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. `ClientConfig.wants_discovery` · `WorkerConfig.wants_discovery` 는 빈 값이나 `auto`. 토큰을 담은 파일은 `_check_private`(그룹 · 기타 비트 있으면 거절). 클라이언트 키 `{server, token_env, token, label}`, 워커 키 11개. 워커 토큰은 플래그로 못 준다 | `config.py:79`, `:365-367`, `:522-524`, `:1509-1513`, `:1494`, `:1564-1576`, `:1832-1833` | 새 키 `pair_secret` 을 둘 다에 더하고 `_check_private` 를 그 키에도 건다(PI17). 이름 정리는 `_NAME_RE` 에 맞춘다(PI12) |
| F21 | 워커 서비스 파일: launchd `KeepAlive = {SuccessfulExit: false}` · `ThrottleInterval = 10`, systemd `Restart=on-failure` · `RestartSec=5` — **0 은 되살리지 않고 0 이 아닌 종료는 다시 띄운다**. 코드별 제어는 없다 | `cli.py:3507-3508`, `:3535-3536` | 확실한 신원 거절은 종료 0(되살리지 않음), 재시도할 일은 종료하지 않고 프로세스 안에서(303). 서비스 파일은 바꾸지 않는다 |
| F22 | 서버 JSON 본문 상한 64 KiB, 동시 요청 `max_concurrent_requests` 세마포어(F39), 없는 경로는 404 + `not_found_hint(path)` | `server.py:238`, `:5706-5716`, `:6490`, `:5975` | 새 익명 라우트는 본문 1 KiB 상한. 교환의 404 본문은 경로 힌트 없이 고정 바이트(PI10). 옛 서버의 `/pair/hello` 는 404 → 짝지은 머신은 `other`(토큰 0, 재시도, 304 — 1판의 「실패로 보고 종료」를 바꿈, PI26) |
| F23 | `rcm serve` 는 `OSError` 와 `StoreError` · `sqlite3.Error` 를 잡아 `cannot start server: …` 와 종료 2(`_usage` → `USAGE_EXIT = 2`) | `cli.py:3376-3394`, `:456-458`, `:108` | `pair.key` 오류는 `StoreError` 의 하위 클래스 `PairKeyError` 로 `serve()` 안에서 올린다 → PR1 이 `cli.py` 를 고치지 않고 같은 줄 · 같은 종료 2 가 된다(V2-5 c) |
| F24 | 릴리스 드라이버가 실행마다 client 토큰 `store-driver:<repo>:<id>` 를 발급 · 폐기한다(이름에 `:` — `_NAME_RE` 밖) | `release_driver.py:261`, `:328` | 같은 `tokens` 표를 쓰는 길 — 회귀 그물(PI27). 짝짓기 이름은 `_NAME_RE` 라 겹치지 않는다 |
| F25 | `Source.identity`(잡 소스의 정체)라는 이름이 이미 있다 | `core/model.py:267` | 새 이름은 `identity` 를 쓰지 않고 `pair_` 접두어로 짓는다(`pair_secret` · `pair_id` · `pair.key` · `/pair/hello`). grep 결과 0건(§11 의 noted) |
| F26 | `rcm setup` 은 이 sha 에 없다(`cmd_setup` 0건). M5t-a PR4 가 만든다 | `grep -n cmd_setup src/remote_ci_monitor/cli.py` → 0 | PR4 의 시작 조건 |
| F27 | `TOKEN_KINDS = (client, admin, worker)`, `DEFAULT_POOL = "default"`. 프리셋의 풀은 `pool` + `pools`, `placement` 는 `pool` · `capable` | `core/model.py:79`, `:84-85`, `:202-203`, `:217` | 풀 거르기 규칙(294)은 `pool`/`pools` 와 `any-capable` 을 본다 |
| F28 | PLAN 「보안」 절에 익명 예외 문장이 없다(M5t-a 4판이 지웠다) | `PLAN.md:241-249`, `docs/m5t-workplan.md` §13 3라운드 B-M5 | PR0(이 명세 PR 의 마지막 커밋)이 다시 넣는다(297, 재료 E2) |
| F29 | M5n §13 이 자동 발급 · 자동 가입을 막았다 | `docs/m5n-workplan.md:650` | PR0 이 그 줄에 개정 날짜와 결정 280 을 적는다 |
| F30 | M5t-b 2판 register 순서: 1 본문 모양 400 → 2 버전 409 → 3 세션 칸 없음 409 `protocol_required` → 4 「(M5t-c 가 넣을 자리) `pair_proof` 검사 — 아래 5~7 의 어떤 부작용보다 먼저」 → 5 펜싱된 세션 409 `session_fenced` → 6 현재 세션이면 멱등 → 7 새 세션이면 한 `BEGIN IMMEDIATE` 안에서 `previous_sessions` 잡 닫기 · 펜싱 · 행 삽입 | M5t-b 2판 §4.2 | 증명 검사(313 순서) → 풀 검사(294)가 4 자리. 실패는 5~7 의 부작용 0 |
| F31 | M5t-b 세션: 워커 프로세스가 시작할 때 `secrets.token_hex(16)` 하나, 모든 레인이 같은 값. spec 을 뺀 모든 `/worker/*` 요청이 세션을 싣는다(본문 `worker_session_id`, tree · artifacts · log 는 헤더 `X-RCM-Worker-Session`). 표에 없는 세션은 409 `not_registered`, 펜싱된 세션은 409 `session_fenced` | `docs/m5tb-workplan.md` §4.2 · §4.10, 결정 260 · 265 | 등록 증명이 `worker_session_id` 를 묶는다(엿본 증명을 다른 세션으로 못 쓴다). 토큰만 훔친 쪽은 등록(증명)을 못 하므로 claim · heartbeat 도 못 한다 — 1판 T6 의 「등록 없이 claim · heartbeat」 남는 위험이 없어진다(현재 세션 id 를 엿본 T4 는 남는다). `/worker/spec` 은 세션이 없어 토큰만으로 읽힌다(묶인 풀만, 294) |
| F32 | M5t-b 종료 코드(결정 258): 펜싱 0, 프로토콜 불일치 3, 그 밖은 오늘대로(0 멈춤 · 2 등록 막힘 · 1 exec 실패) | `docs/m5tb-workplan.md` §4.7 · §4.10, §11 258 | 303 의 종료 0 은 같은 「되살리지 않음」 뜻. 3 은 M5t-b 몫으로만 남는다 |
| F33 | M5t-b §4.7 은 「M5t-c 도 워커 종료 3 을 쓴다 … 서버 신원 미확인」이라고 적었다(2차 Q-C 를 따른 문장) | `docs/m5tb-workplan.md` §4.7 | 303 이 대체한다 → 질문 Q-E(§1 뒤집는 것 표) |
| F34 | M5t-b DB v26(PR1): 새 표 `worker_sessions`, `jobs` 칸 여섯. 「M5t-c 는 v27 이다」 | `docs/m5tb-workplan.md` §4.9 | v27 은 v26 위(299) |
| F35 | M5t-b 2판 PR 표: PR1 owns `store.py` · `remote_workers.py` · `server.py` · `capabilities.py` · `config.py` · `core/model.py` · `core/outcome.py` · `client.py` · `remote_worker.py` · `runner.py` · `cli.py`(cmd_worker) 외(시작: M5t-a PR1~PR7 전부 머지), PR2 owns `store.py` · `remote_workers.py` · `remote_worker.py` · `client.py` · `server.py` · `core/status.py`, PR3 owns `core/model.py` · `core/queue.py` · `core/render_text.py` · `cli.py`(wait · logs 사람용 줄) · `web/app.js` · `web/i18n.js` · `docs/operating.md`, PR2 와 PR3 은 같은 물결, PR4 는 실기 | M5t-b 2판 §5 | 이 명세 PR1 · PR2 시작 = M5t-b PR1 머지. PR3 시작 = M5t-b PR2 · PR3 머지(PR3 은 `cli.py` 의 `_wait` · `cmd_top` 을 고친다 — V1-10). PR4 는 PR3 뒤라 M5t-b PR3 과 겹치지 않는다 |
| F36 | M5t-b 의 새 워커는 등록 전에 `GET /worker/spec` 의 `worker_protocol` 을 보고, 없으면 `/api/health` 의 `version` 을 본다(프로토콜 확인) | `docs/m5tb-workplan.md` §4.7, 결정 270 | 짝지은 워커는 hello 가 그 둘보다 먼저다(둘 다 토큰을 싣는다 — F17 · F19) |
| F37 | M5t-b: heartbeat 이 끊겨 `worker_timeout_seconds` 가 지나면 레인은 down 만, 도는 잡은 `disconnect_grace_seconds`(기본 600) 동안 유예 뒤 `lost`. 보고 큐는 디스크(`outbox.json`) | `docs/m5tb-workplan.md` §4.3 · §4.10, 결정 263 · 266 · 272 | hello 가 503 으로 막혀 heartbeat 이 멈춰도 잡은 `worker_timeout` 에 `lost` 가 되지 않고 유예를 받는다(V2-10). 확인되지 않은 서버에 보고를 보내지 않아도 보고는 큐에 남는다 |
| F38 | 서버는 IPv4 전용: `RcmHTTPServer` 는 `ThreadingHTTPServer` 의 기본 `address_family`(AF_INET)를 그대로 쓴다. `LOOPBACK_BINDS` 에는 `"::1"` 이 있지만 그 bind 는 열리지 않는다 | `server.py:6468-6486`, `config.py:51`, 측정 M5 | 소켓 로컬 주소는 언제나 `a.b.c.d` — IPv4-mapped(`::ffff:…`) · scope id 는 오늘 나오지 않는다. 그래도 정규화 함수는 둘 다 처리한다(장래 dual-stack 대비, PI33). IPv6 주소로 닿는 길은 `public_url` 뿐 |
| F39 | 동시 처리 상한: SSE 를 뺀 모든 요청이 `self.server.slots`(BoundedSemaphore, `max_concurrent_requests`)를 비차단으로 잡고, 못 잡으면 503 `too many concurrent requests` | `server.py:5639-5641`, `:6490` | hello 도 같은 상한을 지난다. 503 은 신원 판단이 아니라 재시도(303, V2-10) |
| F40 | 리디렉트: `Client` 는 기본 `follow_redirects=True` 로 `urllib.request.urlopen` 을 쓴다. `download_tree` · `download_bundle` · `events` 는 언제나 `urlopen`. CPython `HTTPRedirectHandler.redirect_request` 는 `content-length` · `content-type` 만 빼고 헤더를 새 요청에 옮긴다 — `Authorization` 이 리디렉트 대상 호스트로 간다. 따르지 않는 오프너는 이미 있다(`_NO_REDIRECT`, `follow.no_redirect_opener`) | `client.py:476-483`, `:486-511`, `:140`, `:589`, `:910`, `follow.py:648-656`, 측정 M6 | 짝지은 머신은 hello 와 토큰을 싣는 모든 요청에서 리디렉트를 따르지 않는다(309, V2-12). 옛 머신은 그대로 |
| F41 | 오늘 워커는 등록 403 · 401 을 `WorkerExit(2)` 로 끝낸다(403 은 `e.message` 에 「(rcm token add NAME --worker on the server)」를 붙여 찍는다). 레인 재등록이 막혀도 2: 주석 「종료 0 이면 launchd `SuccessfulExit = false` 와 systemd `Restart=on-failure` 가 다시 띄우지 않아 조용히 멈춘다」 | `remote_worker.py:354-360`, 주석 `:785-787` A(V1 drift: 2판은 `:780-784`) | 옛 토큰 워커는 그대로 2. 짝지은 워커의 종료 0 은 J6 · J7 뿐이고 종료 전에 이유 · 다시 짝짓는 법 · 상태 파일 `exited`(조용히 멈추지 않게) |
| F42 | PLAN: 「core/ ── 순수: I/O 도 시계도 안 본다. now 는 인자」 · 「순수 계층(core/)은 I/O 도 시계도 안 본다」 | `PLAN.md:539`, `:828` | `core/pairing.py` 는 `now` · 난수 바이트를 인자로 받는다. `secrets` · `time` · 파일 · 창 상태는 `src/remote_ci_monitor/pairing.py`(V1-6 · V2-4) |
| F43 | PLAN 「보안」 쓰기 인증 문장 | `PLAN.md:243` | §1 뒤집는 것 표 · PR0 |
| F44 | PLAN 의 M5t-c 항목은 「(명세 예정)」, 결정 색인은 「244 · 245 는 M5t-c 명세에 실을 예정이다. 249 는 두 명세에 나눠 실을 예정이다」. 부모 명세는 244(짝짓기 창과 서버 신원) · 245 의 풀 묶기 · 249 의 신원 · 창 · `tokens.pool` 부분을 M5t-c 로 넘겼다 | `PLAN.md:629`, `:753`, `docs/m5t-workplan.md:1045`(「옮긴 결정」 행), `:1109`(§12 M5t-c 표 「다루는 것」) | PR0 편집 목록(§5). 244 → 280 · 281 · 290 · 291, 245 의 풀 묶기 → 284 · 294, 249 의 M5t-c 부분 → 293. 옛 번호는 다시 쓰지 않는다 |
| F45 | dev 의 `rcm worker` · `--check` 서버 찾기: `_worker_config` 가 빈 값 · `auto` 면 `_pick_worker_server` 로 찾고 `discovered_server` 표시(`cli.py:4399-4402`, `:4421-4443`). `--check` 는 `_confirm_found_server` 가 TTY 면 묻고 비TTY · 거절이면 `EXIT_UNKNOWN`(`:4586-4589`, `:4670-4689`). `rcm worker`(점검 아님)는 묻지 않고 찾은 서버를 쓴다(`:4598`) | D | A1 · A3 · A4. 짝짓지 않은 `auto` 거절은 `_pick_worker_server` 를 부르기 전 |
| F46 | dev 의 doctor: `_Run.confirm`(TTY 면 묻고 비TTY 면 undecided — `doctor.py:1366-1378`), `_Run.discover`(여럿이면 `DoctorUsage` 2 — `:1380-1390`), `worker_server`(`auto` warn `:1920-1924`, 찾기 `:1928-1933`, 토큰 요청 health · whoami · spec `:1941-1959`), `client`(`:2323-2363`). 종료 `decide_exit` 1 > 3 > 0(`core/doctor.py:102-108`, 상수 `:34`). 문서 `server` = `{url, found_by, state, generation, version}`(`doctor.py:1401-1409`) | D | A5 · A6 · A7 · A8. 신원 FAIL 은 `problems`(1), 모름은 `undecided`(3). doctor 가 토큰을 보내는 첫 자리(`:1941`) 앞에 hello |
| F47 | TD 시험: `tests/test_doctor_cli.py:585-595`(TD48 warn), `:608-634`(TD10 확인 전 요청 0), `:637-641`(명시 URL 은 묻지 않음), `:644-655`(TD28 비TTY 3), `:890-906` · `:909-920`(TD28 `worker --check`); `tests/test_worker_discovery.py:104-127`(확인 fixture · auto 하나). `auto`/빈 `server` 를 쓰는 시험 줄 수: `test_worker_discovery.py` 31 · `test_config.py` 15 · `test_follow_cli.py` 5 · `test_worker_config.py` 5 · `test_cli_init_worker.py` 4 · `test_cli_discover.py` 3 · `test_doctor_cli.py` 3 · `test_cli_worker_m5q.py` 1 · `test_docs_m5n.py` 1 | D, `git grep -c` | PR3 이 빨개지는 시험을 자기 파일로 고친다: 짝짓지 않은 auto 워커 · 토큰 있는 auto 클라이언트 · 따라가기를 쓰는 것(`test_worker_discovery.py` · `test_doctor_cli.py` · `test_cli_worker_m5q.py` 「server = "auto" (§14 B질문16)」 절 `:568` · `test_cli_discover.py:319-345` · `test_follow_cli.py:1123-1160` · `:1243`). `test_config.py` · `test_worker_config.py` 는 로더의 `wants_discovery` 만 보므로 그대로 |
| F48 | M5t-a 문장: 결정 246(`docs/m5t-workplan.md:1037`), 「첫 실행 안전」(`:513`), §4.5 `worker --check` 두 줄(`:600-601`), §4.7 종료 3(`:660-662`), 기존 결정 표 auto 행(`:1003`), TD10 `:837` · TD28 `:855` · TD48 `:874`, B-M1 `:1162` | A = D | §1 R7 · R8 · R9 |
| F49 | CHANGELOG `[Unreleased]` `### Breaking changes`: 「`rcm worker --check` asks before it uses a server found on the network. With `server = "auto"` it shows the server's name and address and waits for a yes in a terminal; without a terminal it exits 3 and sends nothing」(#268) | `CHANGELOG.md:10-16` D | v0.11.0 으로 나가면 M5t-c 는 `Changed` 새 항목으로 「짝짓지 않은 auto 는 mDNS 전 종료 2, 짝지은 auto 는 hello 가 확인을 대신」을 적는다. 그 전이면 같은 `[Unreleased]` 의 그 항목을 고친다(릴리스 노트가 나가지 않을 동작을 약속하지 않게) |
| F50 | `rcm init worker` 는 `--server` 가 없으면 `auto` 를 쓰고(`cli.py:3601-3605` A), stdout 에 경로, stderr 에 「next: get a token on the server with `rcm token add <name> --worker` …」(`:3627-3633` A). 템플릿 머리 주석 7줄과 `server = "auto"`(`templates/worker.toml:1-9`). 템플릿 = 예시 바이트 같음 잠금 `tests/test_cli_init_worker.py:101-103`, `auto` 한 줄 `:106-109`, 예시가 discovery `:118-123`, init 이 `auto` 를 씀 `:133-142` · `:155-158`, 토큰 값 없음 `:126-127` | A = D | 316: 템플릿 · 예시 · 출력 · 잠금 시험을 PR3 이 함께 고친다. `server = "auto"` 줄과 잠금 `:106-109` · `:133-142` 는 그대로 둔다(미완성 틀) |
| F51 | 처음 설정 · 서버 찾기 문서: `docs/configuration.md:1325-1357`(Second build machine), `:1369-1376`(Finding the server), README.md:96-98 · `:108-112`(클라이언트 auto), `docs/usage.ko.md:553-561`, `docs/operating.md:492-494`, `docs/m5n-workplan.md:414-425`. 문서 잠금 `tests/test_docs_m5n.py:80-85`(README · configuration · operating 묶음에 `rcm init worker` · `--service-file` · `server_name` · `server = "auto"`) | A(줄 번호는 A — D 에서 `docs/configuration.md` 는 +28줄) | R11. 고칠 PR 이 시작할 때 자리를 다시 찾는다 |
| F52 | M5t-a setup 의 TR: TR13(`:794`) · TR21(`:802`) · TR26(`:807`) · TR27(`:808`) · TR28(`:809`) · TR35(`:816`). setup 은 D 에도 없다(`cmd_setup` 0건 — F26) | A = D | R12~R15. PR4 는 M5t-a PR4 머지 뒤(그대로) |
| F53 | 취소는 heartbeat 응답의 `cancel` 로만 워커에 온다: `self.cancel_requested = {…cancel…}`(`remote_worker.py:449-451`), 러너는 `should_cancel()`(`:187-188`)로 본다. heartbeat 이 2xx 가 아니면 새 취소를 모른다 | A | 318: 신원 확인 전(J2~J5) · 끝내는 중(J6 · J7)에는 취소가 닿지 않는다 |
| F54 | M5t-b 2판: `irreversible` 표식 잡은 마지막 2xx 이후 `G − m` 에 감시 스레드가 스스로 멈추고 결과는 `lost`(3). 일반 잡은 계속 돌고 서버는 유예 G 뒤 `lost`. heartbeat 2xx 의 `closed_jobs` `had_cancel: true` 면 다시 붙은 워커가 그 잡을 멈춘다 | M5t-b 2판 §4.3, 결정 253 · 320 · 322 | 318: 신원 정지는 「2xx 없음」이라 표식 잡은 M5t-b 대로 멈춘다. 다시 J1 이 되면 322 가 취소됐던 잡을 멈춘다 |
| F55 | M5t-b 2판 세션 폴더 `<data>/worker-sessions/<S>/`(`lock` · `owner.json` · `ok_at.json` · `jobs/…`). 시작 순서: 프로토콜 확인 → `GET /api/whoami` → 폴더 · `lock` · `owner.json`(폴더는 토큰 요청 뒤에 만든다). `owner.json` 에 토큰 값 없음 | M5t-b 2판 §4.4 시작 순서 2~4, 결정 325 | 314 의 상태 파일을 세션 폴더에 두지 않는다: hello 는 폴더보다 먼저이고(기동 J2~J5 에는 폴더가 없다), 읽는 쪽(`--check` · doctor)은 설정의 `pair_id` 로 자리를 바로 안다 |
| F56 | 클라이언트 종료 관례: `_client_fail` 은 HTTP 상태가 있으면 2, 연결 실패면 3(`cli.py:460-474`). 제출 실패는 거절이면 2 아니면 3(`:930-934`, `:1088-1093`). `rcm top` 은 닿지 못하면 `--json` · 한 번 보기 3, `--watch` 는 「unreachable」 화면으로 계속(`:2484-2511`). `rcm wait` 3 = 모른다(PLAN.md:118 「3 은 「모른다」이지 「실패」가 아니다(fail-open 금지)」) | A | 4.3 클라이언트 표: 아무것도 하기 전 J2 · J3 은 2(아무것도 하지 않았다), 무엇인가 한 뒤 J2 · J3 은 3(결과를 모른다). J4 · J5 는 오늘의 「닿지 못함」 길 |
| F57 | 익명 쓰기(오늘): `POST /api/eta` 는 `_read_only_ok`(`read_auth = none` 이면 누구나)만 본다(`server.py:5800-5805`, `:5722-5725`), `POST /shelf/<n>/restore` 도 같은 `_read_only_ok`(`shelf_server.py:616`, `:671-674`). 그 밖의 POST · PUT · DELETE 는 토큰 · admin. PLAN 「서버 API」 표(`PLAN.md:253-277`)에 없는 라우터 갈래: `/api/machines` · `/machines/<m>/recheck` · `/worker/*` · `/client/*` · `/api/shelf*` · `/shelf*` · `/gc` · `/reload`(`server.py:5745-5975`) | A | PI20 의 대조 집합을 §4.7 「익명 쓰기」 표로 명세에 적는다(V1-11 — 2판은 PLAN 「보안」 절이 적은 집합이라 했는데 그런 집합은 없다) |
| F58 | `os.link` 는 하드 링크를 못 하는 파일 시스템에서 `FileExistsError` 가 아닌 `OSError`(EPERM · ENOTSUP · EMLINK 등)를 낸다 | Python 문서. 측정 — (이 Mac 에 그런 파일 시스템이 없다) | 4.2 키 만들기 ⑦: 기동 거절, rename 으로 물러서지 않는다 |
| F59 | 서버에 `data_dir` 단일 인스턴스 잠금이 없다 | `flock` · `LOCK_EX` 를 `server.py` · `store.py` · `cli.py` 에서 grep → 0건 A | 두 `rcm serve` 가 한 `data_dir` 에서 `pair.key` 를 함께 만들 수 있다 — `link` 순서(PI35 c)가 필요한 까닭 |
| F60 | 워커 등록의 `host_name` 은 설정 `name` 또는 `gethostname()` 첫 조각(`remote_worker.py:277`, `:351`). 템플릿의 `name` 은 「display name for the host sample」(`templates/worker.toml:14`) | A | setup `--pair` 의 교환 `host_name` 출처를 같은 규칙(`gethostname()` 첫 조각)으로 정한다(V2-4). setup 에 `--name` 은 없다 — 이름은 코디네이터의 `rcm pair --name` 이 정한다 |

측정한 값은 따로 둔다. M1 · M4 · M7 은 개발 머신에서 잰 값이고 운영 머신의 값이 아니다. M3 의 다른 머신 칸은 아직 재지
않았다(§6 17). GPU 속도는 잴 장비가 없어 재지 않았다. M4 는 2판의 SHA256 코드 키로 잰 값이다. 3판은 코드 키를 scrypt 로
바꾸며 M7 을 더했다(319 a).

| # | 값 | 어떻게 쟀나 | 설계에 미치는 것 |
|---|---|---|---|
| M1 | 워커 비밀 파생(HMAC-SHA256) + 응답 MAC 한 번 = 1.88 µs | 개발 머신(arm64, Python 3.11.15)에서 `timeit` 100,000회. 운영 머신 값 아님 | 익명 hello 를 DB 없이 매번 계산해도 비용이 작다 |
| M2 | 코드 공간 32^10 = 2^50, 창당 온라인 시도 5번 → 한 창의 온라인 추측 성공 확률 = 5/32^10 ≈ 4.4e-15 | 계산(`5/32**10` = 4.440892098500626e-15) | 시도 한도 5 와 10자 코드(291) |
| M3 | 운영 워커 · 클라이언트 중 `server = "auto"` + 옛 토큰의 수. 이 Mac: 0. 다른 머신: — | 메인 세션이 이 Mac 의 운영 설정을 읽기 전용으로 확인(2026-11-26, Q-B). 다른 머신은 §6 | 있으면 반영 전에 다시 짝짓거나 명시 URL |
| M4 | 관찰한 교환 증명으로 하는 오프라인 추측: 한 번(SHA256(code) + HMAC-SHA256) = 1.08 µs, 초당 923,211번(한 코어). 전체 공간 2^50 = 38.7년. 창 수명 10분 안에 공간 절반을 훑으려면 초당 약 9.4e11번(이 속도의 코어 약 100만 개). GPU 속도 — (재지 않음, 잴 장비 없음) | 개발 머신 arm64, Python 3.11.15, `timeit` 200,000회 | 2판의 SHA256 코드 키 기준 — 3판은 M7(scrypt) |
| M5 | `RcmHTTPServer.address_family` = 2(AF_INET). `RcmHTTPServer(("::1", 0))` → `socket.gaierror` [Errno 8]. `0.0.0.0` 에 묶은 서버가 받은 연결의 `getsockname()` = 클라이언트가 접속한 로컬 IPv4(`127.0.0.1`, 서버 포트) | 개발 머신 `.venv` 에서 직접 만들어 확인(포트 0, 루프백) | F38 |
| M6 | `HTTPRedirectHandler.redirect_request` 소스: `CONTENT_HEADERS = ("content-length", "content-type")`, `newheaders = {k: v for k, v in req.headers.items() if k.lower() not in CONTENT_HEADERS}` | `.venv` Python 3.11.15 에서 `inspect.getsource` | F40 |
| M7 | `hashlib.scrypt(code, salt=b"rcm-pair-code-v1", n=2**15, r=8, p=1, maxmem=64 MiB, dklen=32)` 한 번 = 0.0496초(32 MiB). 기본 `maxmem` 으로는 `ValueError: memory limit exceeded`(33 MiB 로는 됨). 비교: n=2^14 0.0245초 · 2^16 0.0998초 · 2^17 0.1998초, `pbkdf2_hmac('sha256', 600000)` 0.0490초. 오프라인 추측: 한 코어로 공간 전체 2^50 = 1.77e6 년. 10분 창 안에 절반을 훑으려면 동시 계산 4.7e10 개 · 메모리 약 1.6e18 바이트(32 MiB 씩), 60분 창이면 동시 계산 7.8e9 개. GPU — (재지 않음, 잴 장비 없음. scrypt 는 메모리 경계라 한도가 코어 수가 아니라 메모리다) | 개발 머신 arm64, Python 3.11.15, `.venv` 에서 `timeit` 3 × 20회 최소값(n=2^15), 다른 n 은 5회 중앙값. 운영 머신 값 아님 | 319 a: 코드 키 매개변수. 서버는 창을 열 때 한 번(admin), 워커는 setup 에서 한 번 계산한다 — 익명 교환은 scrypt 를 부르지 않는다(HMAC 비교만) |

## 4. 설계

설계는 위협 모델에서 시작한다. 4.2 부터의 표는 칸마다 4.1 의 T 번호를 단다.

### 4.1 위협 모델

이 절을 먼저 쓴다(결정 252). 지킬 자산에는 S 번호를, 공격자에는 T 번호를 붙였다.

#### 지킬 자산

| # | 자산 | 왜 |
|---|---|---|
| S1 | 토큰(worker · client · admin) | 잡 제출 · claim · 관리 권한 |
| S2 | 워커 머신 | 서버가 준 프로브 argv · 프리셋 argv 를 그 머신 사용자 권한으로 돌린다 |
| S3 | 클라이언트 머신과 작업 트리 | 따라가기가 서버가 준 wheel 을 실행한다(F18), `rcm run` 이 미커밋 트리를 올린다 |
| S4 | 프리셋 env | `/worker/spec` 과 claim 이 싣는다(F9) |
| S5 | 발급 권한(짝짓기 창 · 코드) | 열린 창은 토큰을 낳는다 |
| S6 | `pair.key` 와 `pair_secret` | 서버 신원 그 자체 |
| S7 | 운영 DB | 다른 빌드가 열면 마이그레이션(AGENTS) |

#### 공격자와 처리

2판은 T1 · T4 · T6 을 고치고 T12 · T13 · T14 를 더했다. 3판은 T1 · T4 · T5 를 고쳤다. T1 에는 위장 서버가 워커를 영구히
멈추지 못한다는 것을 적었다(317). T4 에는 scrypt 코드 키와 세 변형을 더했다. 변형은 엿본 교환 요청의 재생 (c), 코디네이터
밖에서 친 `rcm pair` 의 평문 (d), 통과한 hello 뒤의 401 · 등록 응답 위조 (e) 다. T5 에는 형식 오류의 400 과, 익명 교환이
scrypt 를 부르지 않는다는 것을 더했다(319).

| # | 공격자 위치 | 노리는 것 | 오늘(F) | M5t-c 가 하는 것 | 남는 위험 | 결정 | 시나리오 |
|---|---|---|---|---|---|---|---|
| T1 | 같은 망, mDNS 에 먼저 답하거나 설정 URL 을 차지한 위장 서버 | S1 · S2 · S3 | `auto` 워커 · 클라이언트가 찾은 서버에 토큰을 보내고 argv · wheel 을 실행한다(F16 · F17 · F18) | 짝지은 머신은 토큰 전에 hello. J1 이 아니면 토큰 0 · 계속 다시 시도 · 10분 경고 · 상태 FAIL/unknown(J2~J5). 클라이언트는 아무것도 하기 전이면 종료 2. 짝짓지 않은 `auto` 는 찾기 전에 거절 | 위장 서버가 그 주소에 있는 동안 짝지은 워커는 일을 받지 못한다(서비스 거부). 영구 정지는 없다 — 종료 0 은 hello 를 통과한 서버의 답에서만(J6 · J7). 명시 URL 의 옛 토큰 머신은 그 URL 을 믿는다(오늘과 같다) | 283 · 285 · 290 · 303 · 317 | PI1 · PI2 · PI3 · PI24 · PI36 · PI41 |
| T2 | 같은 망, 다른 IP 에서 hello 를 진짜 서버로 중계 | S1 · S2 | — | `dialed` 를 MAC 에 넣고, 진짜 서버는 소켓 주소 · `public_url` · 광고 이름 집합으로 판정(308) | 이름 URL(`<host>.local` · `public_url` 이름)은 이름을 가로챈 중계가 통과 | 282 · 290 · 308 | PI5 · PI6 |
| T3 | 같은 IP 를 차지(ARP 스푸핑, DHCP 로 옛 주소를 받은 장치) | S1 · S2 | 끊긴 뒤 같은 URL 로 바로 토큰 | 연결 실패 뒤 첫 요청은 hello. DHCP 로 주소를 받은 장치는 MAC 을 못 만든다 | ARP 스푸핑이 연결 실패 없이 요청 사이에 끼어들면 못 막는다(요청 서명 없음) | 282 · 295 | PI4 |
| T4 | 같은 망의 수동 엿보기와 경로 위 중계 | S1 · S5 · S6 | 평문(결정 5) | 워커의 교환 요청에 코드가 없다(301). 코드 키는 scrypt(319 a). hello nonce · 챌린지는 한 번 | (a) 교환 응답의 토큰 · `pair_secret` 과 요청의 토큰 · 세션 id 를 엿보면 사칭 가능 (b) 엿본 교환 증명으로 창이 열린 동안 오프라인 추측(M7: 한 코어 1.77e6 년, 10분 안에 절반은 동시 계산 4.7e10 개 · 약 1.6e18 바이트) (c) 엿본 교환 요청의 재생: 증명은 창이 닫힐 때까지 bearer — 정당한 요청보다 먼저 닿거나(정당한 요청을 붙잡은 중계) 정당한 요청이 창을 닫지 못한 채 끝나면(400 · 409 · 연결 실패) 재생한 쪽이 토큰 · `pair_secret` 을 받는다 (d) 코디네이터 밖에서 친 `rcm pair` 의 201 응답(코드)과 admin 토큰 (e) 통과한 hello 바로 뒤의 401 · 등록 응답 위조로 J6 · J7 종료 0. LAN · `0.0.0.0` 경고와 Tailscale 권고 | 282 · 301 · 319 | PI21 · PI36 |
| T5 | 같은 망의 익명 클라이언트(코드 추측 · 정찰 · 방해 · 과부하) | S5 | 창 없음 | 50비트 코드 · 창당 5번 · 실패는 같은 404 · 형식 오류는 창 조회 전 같은 400(창 상태와 무관) · 읽기 라우트와 TXT 에 창 표시 없음 · 본문 1 KiB · 동시 상한 · hello 는 Store 를 부르지 않는다 · 익명 교환은 scrypt 를 부르지 않는다 | 틀린 코드 5번으로 창을 닫는 방해 | 288 · 291 · 319 | PI9 · PI10 · PI20 · PI40 |
| T6 | 탈취한 worker 토큰(로그 · `ps` · 환경 변수 유출) | 정당한 워커의 잡과 세션, 다른 권한 | 같은 이름 재등록이 정당한 워커의 잡을 `lost` 로 닫는다(F10) | 짝지은 워커의 등록(세션 열기)은 챌린지 증명(세션 id 를 묶음). 증명 없이는 세션이 없어 claim · heartbeat 도 409 `not_registered`(M5t-b, F31). 다른 주소 재등록을 서버 로그에. 클라이언트 API 403 · 남의 잡 403(오늘) | 토큰만으로 `/worker/spec`(묶인 풀의 프리셋 · env)을 읽는다. 옛 명시 토큰은 토큰만(증명 없음). **`worker.toml` 통째 복사(토큰 + `pair_secret`)는 워커 신원 복제 = 로컬 침해로 보고 막지 않는다** — 복사한 쪽은 증명을 만들어 등록하고 정당한 세션을 펜싱한다. 0600 은 다른 보통 사용자만 막는다(소유자 · root · 악성코드 · 백업 도난은 못 막는다) | 287 · 302 | PI23 |
| T7 | 한 창이 주는 권한을 넘기기 | admin · client 권한 | — | 창의 kind 만 준다. admin 창은 없다 | 없음 | 280 · 291 | PI7 |
| T8 | 사람 실수: 코드를 엉뚱한 머신에 넣음 | S2 · S4 | — | 양쪽 완료 줄 · `rcm token list` 의 `paired from` · 폐기 한 번 | 폐기 전까지 그 머신이 받은 잡 | 286 | PI25 · PI19 |
| T9 | DB 사본 · 백업을 가진 사람 | S6 | — | DB 에는 `pair_id` 와 토큰 sha256 만. `pair.key` 는 DB 밖 0600 | `data_dir` 을 키와 함께 통째로 복사하면 사칭 가능 — 문서화된 신원 재설정 | 281 · 289 | PI18 |
| T10 | 붙은 워커가 다른 풀 프리셋 env 를 읽기 | S4 | 모든 비-`local_only` 프리셋 env(F9) | 토큰 풀 · 등록 풀로 거른다 | `any-capable` 프리셋 env 는 붙은 워커 모두가 받는다(그런 프리셋에 비밀을 두지 않는다 — M5n §13 · 결정 254) | 284 · 294 | PI14 · PI16 |
| T11 | 다른 빌드가 운영 DB 를 엶 | S7 | `rcm token` 은 DB 를 연다(F5) | `rcm pair` 는 HTTP 로만 | 없음 | 292 | PI28 |
| T12 | 교환 때의 능동 사칭자: 사람이 넣은 교환 URL(잘못된 URL · 가로챈 이름 URL · 그 주소를 차지한 장치)에 답하는 서버 | S1 · S2 · S5 | — (1판: 코드를 그대로 보내 사칭자가 코드를 얻고 자기 토큰 · `pair_secret` 을 주면 워커가 영구히 믿고 argv 를 실행) | 요청은 코드 대신 `worker_nonce` 와 코드 키 증명. 응답은 두 nonce 와 전체 페이로드에 대한 코드 키 MAC — 코드를 모르는 사칭자는 응답을 만들 수 없고 워커는 아무것도 저장하지 않는다(종료 1) | 사칭자가 받은 증명으로 창이 열린 동안 오프라인 추측(T4 b). 진짜 서버로 가는 경로 위의 능동 중계는 응답을 그대로 넘기므로 통과하고 평문 자격 증명을 본다(T4 a) | 301 | PI34 · PI21 |
| T13 | 3xx 로 다른 호스트에 보내는 서버(위장 · 잘못 설정한 프록시) | S1 | `urlopen` 이 따라가며 `Authorization` 을 옮긴다(F40) | 짝지은 머신의 hello 와 토큰 요청은 리디렉트를 따르지 않는다. hello 3xx 는 「그 밖의 응답」(304) | 옛 머신의 `download_tree` · `download_bundle` · `events` 는 오늘처럼 따라간다 | 309 | PI37 |
| T14 | 같은 망의 mDNS 홍수(가짜 인스턴스 여럿) | 가용성 | `_resolve_server` 는 여럿이면 거절, 워커 다시 찾기는 첫 결과 | 후보 최대 8개에 hello, 통과가 정확히 하나일 때만 옮긴다. 둘 이상 통과(복제 서버, 289 가 지원 안 함)면 옮기지 않고 로그 | 8개 넘게 띄워 진짜 서버를 후보 밖으로 밀면 옮기지 못한다(서비스 거부만, 토큰 0) | 310 | PI4 |

### 4.2 서버 신원 `pair.key` · `pair_secret` · `POST /pair/hello` (결정 281 · 289 · 290 · 306 · 308 · 313 · 319)

서버는 DB 밖의 키 하나로 자기 신원을 증명한다. 짝지은 머신마다 그 키에서 파생한 비밀을 나눠 준다. 계산은 순수 계층과
I/O 계층으로 나눈다(F42, V1-6 · V2-4). PLAN 이 `core/` 를 시계와 I/O 를 보지 않는 순수 계층으로 정했기 때문이다.

| 자리 | 담는 것 | 담지 않는 것 |
|---|---|---|
| `src/remote_ci_monitor/core/pairing.py`(순수, PR1) | `enc` · `pair_mac` · `derive_pair_secret(key, pair_id)` · 챌린지 만들기(`make_challenge(key, pair_id, now: int, rand: bytes)`) · 챌린지 검증(`check_challenge(key, pair_id, c, now: int)`) · `canonical_dialed` · `own_dialed_set` · `verify_hello` · 응답 분류(`classify_hello`) · 코드 정규화 · 형식 검사 · 이름 정리(`clean_pair_label`) · 교환 증명 · 응답 MAC · 코드 키(`pair_code_key` — scrypt, 표준 라이브러리 계산이라 순수) | `secrets` · `time` · `os` · 파일 · 소켓 · 전역 상태 |
| `src/remote_ci_monitor/pairing.py`(I/O, PR1) | `pair.key` 읽기 · 만들기(`load_or_create_pair_key`, `PairKeyError`) · `secrets` 로 nonce · 코드 · `pair_id` · 챌린지 난수 만들기 · 서버 시계 · 창 상태와 잠금 · 쓴 챌린지 집합 · 서버 믹스인(라우트 처리) · 등록 증명 검사(`check_register_proof` — 쓴 챌린지 집합을 쥔다) | — |

나머지 항목은 아래 표에 적는다. 3판은 키 만들기 행에 ⑦ 을 더했다(V1-12). 등록 증명 행은 다시 쓰고 `pair_mismatch` 는
지웠다(313 · V2-6).

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 서버 키 | `<data_dir>/pair.key`, 32 무작위 바이트(원바이트, hex 아님). 값은 어디에도 찍지 않는다 | T9 |
| 키 읽기 | `serve()` 가 Store 를 연 뒤 부른다. `O_RDONLY` · `O_NOFOLLOW` 로 열고 `fstat`: 보통 파일이 아니거나 · 모드에 그룹 · 기타 비트가 있거나 · 길이가 32 가 아니면 `PairKeyError("<data_dir>/pair.key: …")` → `cannot start server: …` 종료 2(F23). 문장은 무엇이 틀렸는지와 고치는 법(`chmod 600` · 신원 재설정 절차)만, 값은 없다 | T9 |
| 키가 없을 때(306, V2-5 a) | 폐기되지 않은 토큰 가운데 `pair_id` 가 있는 행이 하나라도 있으면 **기동 거절**: `cannot start server: <data_dir>/pair.key is missing but N paired tokens are still active — restore pair.key from the backup taken with this data_dir, or reset pairing: revoke the paired tokens (rcm token list shows them in "paired from"; rcm token revoke <name>), then start again and pair again`. 조용히 새 키를 만들지 않는다(fail-open 금지). 그런 행이 없으면 새 키를 만든다 | T9 |
| 키 만들기(V2-5 b · V1-12) | ① 같은 폴더에 임시 파일 `pair.key.tmp-<pid>-<hex8>` 을 `O_CREAT` · `O_EXCL` · `O_WRONLY` · 0600 으로 연다 ② 32 바이트를 쓰고 `fsync` ③ `os.link(tmp, pair.key)` ④ 임시 파일을 지운다 ⑤ `link` 가 `FileExistsError` 면 이미 있는 `pair.key` 를 「키 읽기」 규칙으로 읽는다 ⑥ 폴더를 `fsync` ⑦ `link` 가 그 밖의 `OSError`(F58)면 임시 파일을 지우고 `PairKeyError("<data_dir>/pair.key: cannot create it here — this filesystem does not support hard links (<errno 이름>); put data_dir on a local disk, or create the key yourself: (umask 077; head -c 32 /dev/urandom > <data_dir>/pair.key)")` → `cannot start server` 종료 2. 덮어쓰는 rename 으로 물러서지 않는다 — 두 서버가 서로 다른 키를 메모리에 쥔 채 뜰 수 있다(F59) | T9 |
| 키 수명 주기(289) | 키를 바꾸면(재설정) 모든 `pair_secret` 이 무효 → 짝지은 머신은 hello 가 MAC 불일치(J2) → 토큰 0 · 다시 시도, J2 문장이 「다시 짝지으라」를 말한다(317). DB 만 다른 곳에 복원하면 「키가 없을 때」 규칙이 기동을 막는다(살아 있는 짝 토큰이 있으므로). DB 와 키를 함께 두 살아 있는 서버에 두는 것은 지원하지 않는다(문서, PI35) | T9 |
| 워커 · 클라이언트 비밀 | 발급마다 `pair_id` = 16 무작위 바이트의 소문자 hex 32자. `K = HMAC-SHA256(key = pair.key 32바이트, msg = enc("rcm-pair-secret-v1", pair_id))` = 32바이트. 머신이 받는 값 `pair_secret = "<pair_id>:<K 의 소문자 hex 64자>"`. 머신이 MAC 에 쓰는 키는 `bytes.fromhex(K hex)`(32바이트), hex 문자열이 아니다. DB 에는 `tokens.pair_id` 만(281) | T9 |
| 인코딩(L2 · V2-3) | `enc(f1, …, fn)`: 필드는 모두 `str`. 각 필드의 UTF-8 바이트 앞에 그 바이트 수를 4바이트 big-endian 부호 없는 정수로 붙여 이어 붙인다. 정수(유닉스 초, 프리셋 수)는 부호 · 앞의 0 없는 10진 ASCII 로 바꾼 뒤 넣는다. 값이 없는 칸(null)은 빈 문자열. 첫 필드는 용도 라벨: `rcm-pair-secret-v1` · `rcm-hello-v1` · `rcm-challenge-v1` · `rcm-register-v1` · `rcm-pair-request-v1` · `rcm-pair-response-v1`. `pair_mac(key: bytes, label, *fields) -> str` = `hmac.new(key, enc(label, *fields), sha256).hexdigest()`(소문자 hex 64자) | T2 · T12 |
| 형식 검사 | 받은 쪽은 비교 전에 정규식으로 본다. `pair_id` `^[0-9a-f]{32}$`, nonce(`nonce` · `worker_nonce` · `server_nonce`) `^[0-9a-f]{64}$`, MAC(`mac` · `proof`) `^[0-9a-f]{64}$`, 챌린지 `^([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})$`, `pair_secret` `^[0-9a-f]{32}:[0-9a-f]{64}$`. 대문자 hex 는 틀린 값이다(대소문자를 접지 않는다). 비교는 `hmac.compare_digest` | T2 |
| hello 요청 | `POST /pair/hello`, 익명, 본문 ≤ 1 KiB `{"v": 1, "pair_id": <hex32>, "nonce": <hex64>, "dialed": <canonical_dialed(머신이 접속한 URL)>}`. nonce 는 호출마다 `secrets.token_hex(32)`. 형식이 틀리면 400 `{"error": "bad hello request", "code": "bad_hello"}`(존재 여부와 무관한 형식 오류라 오라클이 아니다) | T1 · T2 · T3 |
| `dialed` 규칙(308, V1-3 · V2-2) | 1판의 Host 헤더 판정을 버린다(스킴을 몰라 `https://name` 의 443 을 `:80` 으로 봤다). `canonical_dialed(url) -> str 또는 None`(양쪽이 같은 함수): 스킴 소문자 `http` · `https` 만 · userinfo 가 있으면 None · 호스트 소문자 · 끝의 점 제거 · IPv6 리터럴은 zone id(`%…`) 를 떼고 IPv4-mapped(`::ffff:a.b.c.d`)는 `a.b.c.d` 로, 나머지 IPv6 는 `ipaddress` 의 압축형을 대괄호로 · IPv4 는 `ipaddress` 로 검사(앞의 0 은 None) · 포트가 없으면 `http` 80, `https` 443, 1~65535 밖이면 None · 경로 · 질의 · 조각은 버린다 · 결과 `"<scheme>://<host>:<port>"`. Host 헤더는 판정에 쓰지 않는다(중계가 마음대로 쓸 수 있다 — 보안은 `dialed` 가 MAC 에 들어가는 데서 나온다) | T2 |
| 서버의 자기 주소 | `own_dialed_set(sock_ip, sock_port, public_url, advertise_host)`: ① `http://<canonical(이 요청이 도착한 소켓의 getsockname() IP)>:<그 포트>` ② 소켓 IP 가 루프백(127.0.0.0/8)이면 `http://localhost:<그 포트>` 도 ③ `public_url` 이 있으면 `canonical_dialed(public_url)`(스킴 그대로 — `https://name` → `https://name:443`, V1-3 의 PI22 칸) ④ 광고가 켜져 있으면 `http://<gethostname 첫 조각 소문자>.local:<그 포트>`(F12). `dialed` 가 이 집합에 없으면 421 `{"error": "this server does not answer to that address — set public_url if it is reached through a name or a proxy", "code": "host_not_this_server"}` — `mac` · 챌린지 없음. 보관함 허용 목록(F14) · `local_ipv4s()`(F15)는 쓰지 않는다 | T2 |
| hello 응답 | 200 `{"v": 1, "challenge": <c>, "mac": pair_mac(K, "rcm-hello-v1", pair_id, nonce, dialed, c)}` — **MAC 이 챌린지를 덮는다**(V1-13: 중계가 챌린지를 바꿔치기해 등록을 망치는 것을 MAC 이 잡는다). K 는 `pair_id` 로 매번 파생(DB 조회 없음). 없는 · 폐기된 · 옛 `pair_id` 도 같은 계산이라 응답 모양 · 시간이 같다. Store 를 부르지 않는다(PI5 · PI20) | T5 |
| 챌린지 | `c = "<t>.<r>.<pair_mac(pair.key, "rcm-challenge-v1", pair_id, t, r)>"`, `t` = 서버 벽시계 유닉스 초(정수, 10진), `r` = 8 무작위 바이트의 소문자 hex 16자. 서버는 메모리 상태 없이 MAC 으로 검증한다. 유효 = `now − 120 ≤ t ≤ now + 5`(`PAIR_CHALLENGE_SECONDS = 120`, 미래 쪽 5초는 같은 서버 안의 시계 되돌림 여유, 그보다 미래면 무효). 한 번 쓴 챌린지는 메모리 집합에 만료까지 두어 두 번째를 거절. 재시작하면 집합은 비지만 120초 창이 묶는다 | T4 · T6 |
| 머신의 검증 | `verify_hello(K, pair_id, nonce, dialed, body) -> bool`: 본문이 객체 · `v == 1` · `challenge` · `mac` 형식이 맞고 `compare_digest(mac, pair_mac(K, "rcm-hello-v1", pair_id, nonce, dialed, challenge))`. 응답의 분류는 4.3 | T1 · T2 |
| 등록 증명(287 · 313 · V2-6) | 짝지은 토큰(`tokens.pair_id` 있음)의 `POST /worker/register` 는 본문 `pair_proof: {"challenge": c, "mac": pair_mac(K, "rcm-register-v1", pair_id, c, worker_session_id)}` 가 있어야 한다. 없으면 403 `{"error": "this worker token was paired — its pairing proof is missing: upgrade rcm on this worker and pair it (rcm setup --role worker --pair)", "code": "pair_proof_required"}`(옛 빌드는 이 `error` 를 로그에 그대로 찍는다 — F41 · V2-11). 검사는 `check_register_proof(token_pair_id, challenge, mac, worker_session_id, now)` 하나가 이 순서로 한다. **K 는 토큰의 `pair_id` 로 만든다 — 본문의 어떤 값도 `pair_id` 로 쓰지 않는다(313):** ① 형식(챌린지 · mac 정규식) → `bad_mac` ② 챌린지 MAC 을 `pair.key` 와 토큰의 `pair_id` 로 검사 → 틀리면 `bad_mac`(다른 `pair_id` 의 챌린지도 여기서 걸린다) ③ `t` 범위(`now − 120 ≤ t ≤ now + 5`) → 밖이면 `expired` ④ 증명 MAC → 틀리면 `bad_mac`(토큰과 `pair_secret` 이 어긋난 워커도 여기) ⑤ 쓴 챌린지 집합에 있으면 `reused` ⑥ 통과하면 그때만 그 챌린지를 집합에 넣는다(⑤ · ⑥ 은 한 잠금 안). ①~⑤ 에서 거절된 요청은 집합을 바꾸지 않는다 — 엿본 챌린지에 틀린 MAC 을 실어 정당한 워커의 챌린지를 태우지 못한다(V2-6). 거절은 403 `{"error": "pairing proof rejected (<reason>)", "code": "pair_proof_invalid", "reason": <reason>}`, `reason` ∈ `expired` · `reused` · `bad_mac`. 자리는 F30 의 4, 풀 검사(294)보다 앞. 실패는 세션 행 · 펜싱 · 잡 닫기 · 워커 행 · 능력 보고 0. 옛 토큰(`pair_id` NULL)은 검사하지 않는다 | T6 |
| (지운 것) | 2판의 `reason` `pair_mismatch` 와 그 정의 「챌린지는 유효한데 다른 `pair_id` 의 것, 또는 토큰의 `pair_id` 로 만든 K 가 아님」은 지운다. 챌린지에 `pair_id` 가 없어 만들 수 없었다(313 · V2-1) | — |

#### 4.2.1 wire 예시

이 바이트대로 구현하고 PI33 이 잠근다(V2-3). 값은 설명용 자리 표시다. 커밋하지 않는 실제 값과 구별하려고 일부러 엔트로피를
낮췄다. `<…>` 는 계산 결과다. 3판은 교환 부분의 코드 키를 scrypt 로 바꾸고 client 창의 200 과 형식이 틀린 교환 본문의 400 을
더했다(319).

```
pair_id      = "00112233445566778899aabbccddeeff"
pair.key     = bytes(range(32))                         # 시험 입력만 — 실제 키는 os 난수
K            = HMAC-SHA256(pair.key, enc("rcm-pair-secret-v1", pair_id))     # 32바이트
pair_secret  = "00112233445566778899aabbccddeeff:" + K.hex()                  # 소문자 hex

enc("rcm-hello-v1", "ab") =
  00 00 00 0c 72 63 6d 2d 68 65 6c 6c 6f 2d 76 31 00 00 00 02 61 62            # 측정: .venv 에서 계산

POST /pair/hello
  {"v": 1, "pair_id": "00112233445566778899aabbccddeeff",
   "nonce": "<hex64>", "dialed": "http://192.0.2.10:8787"}
200
  {"v": 1, "challenge": "1764115200.0011223344556677.<hex64>",
   "mac": pair_mac(K, "rcm-hello-v1", pair_id, nonce, "http://192.0.2.10:8787", challenge)}

POST /worker/register  (M5t-b 본문 + pair_proof)
  {..., "worker_session_id": "<hex32>",
   "pair_proof": {"challenge": "<위 challenge>",
                  "mac": pair_mac(K, "rcm-register-v1", pair_id, challenge, worker_session_id)}}

code_key     = pair_code_key("ABCDEFGHJK")
             = hashlib.scrypt(b"ABCDEFGHJK", salt=b"rcm-pair-code-v1",
                              n=2**15, r=8, p=1, maxmem=64*1024*1024, dklen=32)   # 32바이트

POST /pair/exchange
  {"v": 1, "kind": "worker", "host_name": "build-01", "worker_nonce": "<hex64>",
   "proof": pair_mac(code_key, "rcm-pair-request-v1", "worker", "build-01", worker_nonce)}
200
  {"v": 1, "name": "build-01", "kind": "worker", "pool": "default", "presets_count": 3,
   "token": "<token>", "pair_secret": "<hex32>:<hex64>", "server_nonce": "<hex64>",
   "mac": pair_mac(code_key, "rcm-pair-response-v1", "worker", "build-01",
                   worker_nonce, server_nonce, "build-01", "default", "3", token, pair_secret)}

client 창의 200 (pool · presets_count 키는 있고 값은 null — MAC 에는 빈 문자열)
  {"v": 1, "name": "laptop-1", "kind": "client", "pool": null, "presets_count": null,
   "token": "<token>", "pair_secret": "<hex32>:<hex64>", "server_nonce": "<hex64>",
   "mac": pair_mac(code_key, "rcm-pair-response-v1", "client", "laptop-1",
                   worker_nonce, server_nonce, "laptop-1", "", "", token, pair_secret)}

형식이 틀린 교환 본문 (창 조회 전, 창 상태와 무관하게 같은 바이트, 세지 않음)
400
  {"error": "bad pairing request", "code": "bad_pair_request"}
```

`canonical_dialed` 는 아래 표대로 정규화한다. PI33 의 표 시험이 이 표를 쓴다.

| 입력 | 결과 |
|---|---|
| `http://192.0.2.10` | `http://192.0.2.10:80` |
| `HTTP://Build-01.Local.:8787/x?y` | `http://build-01.local:8787` |
| `https://rcm.example.ts.net` | `https://rcm.example.ts.net:443` |
| `http://[::ffff:192.0.2.10]:8787` | `http://192.0.2.10:8787` |
| `http://[fe80::1%en0]:8787` | `http://[fe80::1]:8787` |
| `http://192.0.2.010:8787` · `ftp://h` · `http://u:p@h` · `http://h:0` | None |

워커와 클라이언트는 교환 응답을 저장하기 전에 검사한다. 키 `v` · `name` · `kind` · `pool` · `presets_count` · `token` ·
`pair_secret` · `server_nonce` · `mac` 이 모두 있어야 한다. `v` 는 정수 1 이어야 하고 bool 이면 안 된다. `kind` 는 요청한
kind 와 같아야 한다. worker 창이면 `pool` 은 `_NAME_RE` 에 맞는 문자열이고 `presets_count` 는 0 이상의 정수(bool 아님)다.
client 창이면 둘 다 null 이다. `name` 은 `_NAME_RE` 에 맞아야 하고 `pair_secret` · `server_nonce` · `mac` 은 4.2 의 정규식에
맞아야 한다. 하나라도 어긋나면 MAC 이 틀린 것과 같이 처리한다. 저장하지 않고 종료 1 이다(4.6 교환 오류 표).

### 4.3 판정과 그 증거의 인증, 확인하는 때 (결정 283 · 285 · 295 · 303 · 304 · 305 · 309 · 310 · 313 · 314 · 315 · 317 · 318)

짝지은 머신, 곧 설정에 `pair_secret` 이 있는 머신은 F19 의 여섯 자리 모두에서 「확인됨」일 때만 토큰을 싣는다. hello
와 토큰 요청은 리디렉트를 따르지 않는다(309). 이미 있는 `_NO_REDIRECT` 와 `follow.no_redirect_opener` 를 쓴다(F40).
3xx 는 토큰 요청에서는 HTTP 응답(오류)이고 hello 에서는 「그 밖의 응답」이다.

hello 응답은 순수 함수 `classify_hello` 가 분류한다. 3판은 2판 4.3 의 분류 표 · 응답 표 · 상태 전이 표와 `auto` 예외 단락을
아래 판정 표 하나로 바꿨다. 이 표가 이 명세의 단 하나의 판정 출처다. §0 · §4.6 · §7 · §9 · §11 은 J 번호로만 가리킨다.

분류 이름(`ok` · `mismatch` · `refused_address` · `unavailable` · `other`)은 `classify_hello` 안에서만 쓰는 값이다.
`unavailable` 과 `mismatch` 는 이미 다른 뜻으로 쓰이는 문자열이다(`store.py:1115` 의 보관함 상태 `"unavailable"`,
`server.py:628` 등). 그래서 화면과 JSON, 로그에 그대로 내보내지 않는다. 사람에게는 아래 로그 문장을 보이고,
`rcm worker --check` 의 `FAIL` 칸도 그 문장을 쓴다.

#### 판정 표

짝지은 워커를 기준으로 적었다. 상태 파일 칸의 값은 아래 「상태 파일」 의 키다. 경고 칸의 「되풀이」는 처음에 한 줄을 내고 그
뒤로는 같은 (`dialed`, 판정)마다 `PAIR_WARN_REPEAT_SECONDS`(600초)에 한 줄을 낸다는 뜻이다.

| # | 받은 것 | 인증됐나 | 분류 | 워커가 하는 일 | 종료 | 상태 파일 | 경고 |
|---|---|---|---|---|---|---|---|
| J1 | hello 200 · 본문 객체 · `v == 1` · `challenge` · `mac` 형식 맞음 · `mac` 이 `pair_mac(K, "rcm-hello-v1", pair_id, nonce, dialed, challenge)` 와 같음 | 됨 — 이 호출의 nonce · 이 `dialed` · 이 챌린지를 K 로 묶은 MAC. K 는 `pair.key` 를 가진 서버만 만든다 | `ok` | 그 `dialed` 를 「확인됨」으로 두고 토큰 요청을 보낸다 | — | `server_identity = verified` | 앞서 경고가 있었으면 「verified again」 한 줄 |
| J2 | hello 200 · 형식 맞음 · `v == 1` · `mac` 어긋남 | 안 됨 — 누구나 보낼 수 있는 바이트다. 「짝지은 코디네이터가 아니거나 그 `pair.key` 가 재설정됐다」 이상을 말하지 않는다 | `mismatch` | 토큰 0. 5 → 10 → 20 → 40 → 60초로 물러서며 다시 hello. 끝없이 | 없음 | `failing` · `hello_bad_mac` | 되풀이 |
| J3 | hello 421 · `code == "host_not_this_server"` | 안 됨 — MAC 이 없다 | `refused_address` | J2 와 같다 | 없음 | `failing` · `hello_wrong_address` | 되풀이 |
| J4 | 연결 실패 · 시간 초과(상태 0) · 5xx(503 포함) · 429 | 안 됨 — 답이 아니다 | `unavailable` | J2 와 같다. 짝지은 `auto` 는 상태 0 만 다시 찾기 셈에 더한다(310) | 없음 | `unknown` · `hello_no_answer` | 되풀이 |
| J5 | 그 밖: 3xx(따르지 않는다) · 404 · 405 · 다른 4xx · 깨진 200 · `v` ≠ 1 | 안 됨 | `other` | J2 와 같다 | 없음 | `unknown` · `hello_unrecognized` | 되풀이 |
| J6 | 토큰 요청에 401, 그리고 같은 `dialed` 로 **바로 새로 한** hello 가 J1 | 부분 — 401 에는 MAC 이 없다. 증거는 같은 주소가 바로 앞 hello 를 통과했다는 것뿐(경로 위 능동 공격자는 그 사이에 401 을 끼울 수 있다 — 282 · T4 e) | 확실(317) | 새 claim · 토큰 0. 기동이면 바로 끝, 도는 중이면 「끝내는 중」 | 0 | `registration = failed` · `token_rejected`, 끝날 때 `exited` | 처음 + 끝내는 동안 되풀이 |
| J6′ | 토큰 요청에 401, 그런데 새 hello 가 J1 이 아님 | — | 그 hello 의 판정(J2~J5) | 401 은 버린다 | 없음 | 그 판정대로 | 그 판정대로 |
| J7 | 등록 403 `pair_proof_invalid` `reason = bad_mac`, 등록마다 그 직전 hello 가 J1 이고 `PAIR_BAD_MAC_LIMIT`(3)번 이어짐 | 부분(J6 과 같은 증거) | 확실(317) — 토큰과 `pair_secret` 이 짝이 아니다(313) | 1 · 2번째: 5초 뒤 새 hello · 새 챌린지로 다시 등록. 3번째: J6 처럼 | 0(3번째) | 1 · 2번째 `registration = retrying` · `proof_bad_mac`, 3번째 `failed` · `proof_bad_mac` | 매번 한 줄 |
| J8 | 등록 403 `pair_proof_invalid` `reason ∈ {expired, reused}` | 부분 | 재시도 | 새 hello 뒤 다시 등록, 5 → 60초로 물러서며 끝없이. J7 의 셈을 0 으로 | 없음 | `retrying` · `proof_expired` 또는 `proof_reused` | 되풀이 |
| J9 | 등록 409 `pool_mismatch`(J1 뒤) | 부분 | 설정 오류(294) | 묶인 풀과 고치는 법 한 줄 | 2 | `failed` · `pool_mismatch`, `exited` | 한 줄 |
| J10 | 확인된 뒤 토큰 요청의 HTTP 답(503 · 5xx · 위에 없는 4xx) | 신원과 무관 | 오늘 처리 | 다시 hello 하지 않는다(503 이 heartbeat 을 멈추게 하지 않는다 — V2-10) | 오늘대로 | 바꾸지 않음 | 오늘대로 |
| J11 | 확인된 뒤 토큰 요청의 연결 실패(상태 0) | — | 「확인 전」 | 다음 토큰 요청 전에 hello(PI4 b) | 없음 | 그 hello 의 판정 | — |
| J12 | 프로토콜 불일치 · 펜싱(M5t-b) | M5t-b | M5t-b | M5t-b | 3 · 0 | `exited` `protocol` · `fenced` | M5t-b |

「확실」은 J6 · J7 뿐이다. J2 · J3 은 사람이 손볼 일이 있을 가능성이 커서 FAIL 로 드러내고 J4 · J5 는 모름(unknown)으로
드러낸다. 어느 것도 종료하지 않는다. 위장 서버는 일을 막을 뿐 영구 정지를 만들지 못한다(317).

`auto` 에서 후보 하나가 J2 · J3 이면 그 후보가 아니라는 뜻일 뿐이다. 2판의 `auto` 예외 단락은 이 표가 대신한다(V2-9).

옛 토큰 워커, 곧 `pair_secret` 이 없는 워커는 이 표 밖이다. 오늘대로 401 · 403 을 받으면 종료 2 다(F41). 짝지은 토큰을 옛
설정에 넣어 받은 403 `pair_proof_required` 도 종료 2 다. 서버 문장이 업그레이드와 짝짓기를 안내한다.

#### 워커 상태 전이 (J 로만)

| 지금 | 판정 | 다음 · 하는 일 |
|---|---|---|
| 기동 | J1 | 확인됨 → 따라가기 ①(F17) → 프로토콜 확인(F36) → `whoami` → 세션 폴더(F55) → 등록(직전에 새 hello, 그 챌린지로 증명) |
| 기동 | J2~J5 | 기동 그대로(J 표의 하는 일) |
| 확인됨 | J10 | 확인됨 |
| 확인됨 | J11 | 확인 전 |
| 확인됨 | 등록(처음 · 레인 재등록 `not_registered`) | 등록마다 새 hello. J1 이면 그 챌린지로 증명, 아니면 그 판정 |
| 확인됨 | J8 | 확인됨(등록 재시도) |
| 확인됨 | J9 | 끝(2) |
| 확인됨 · 확인 전 | J6 · J7 | 기동이면 끝(0). 도는 중이면 끝내는 중 |
| 확인 전(도는 중) | J1 | 확인됨. 미룬 heartbeat · 보고를 이어 간다. 그 heartbeat 의 `closed_jobs` `had_cancel` 이 취소됐던 잡을 멈춘다(F54) |
| 확인 전(도는 중) | J2~J5 | 확인 전. 토큰 0, 도는 잡은 계속, 보고는 M5t-b 보고 큐. heartbeat 이 없으므로 **새 취소는 닿지 않는다**(F53, 318). 서버는 레인 down · 잡 유예 G 뒤 `lost`, `irreversible` 잡은 워커가 스스로 멈춘다(F54) |
| 확인 전(짝지은 `auto`) | 이어진 상태 0 실패 5번 | 후보 최대 8개에 hello, J1 이 정확히 하나면 옮기고 「server moved: A → B」, 0개면 그대로, 둘 이상이면 옮기지 않고 「several servers prove this worker's identity — a copied data_dir is not supported (decision 289)」(310). 설정 파일은 고치지 않는다 |
| 끝내는 중 | — | 새 claim 0, 토큰 0(heartbeat 없음 → 취소는 닿지 않는다, 318). 로컬 시한(`timeout_seconds`)은 그대로. 보고는 보고 큐. 도는 잡이 0 이면 `exited` 를 쓰고 종료 0. hello 가 통과해도 돌아가지 않는다(R19) |
| 아무 때 | J12 | M5t-b(3 · 0) |

#### 상태 파일 (결정 314)

| 항목 | 정한 것 |
|---|---|
| 누가 · 언제 | 짝지은 워커(`pair_secret` 있음)만. 판정이 바뀔 때마다, 그리고 바뀌지 않아도 `PAIR_STATE_REFRESH_SECONDS`(60초)마다 쓴다. 종료할 때 `exited` 를 쓴다(신호 멈춤 포함, SIGKILL 은 못 쓴다) |
| 자리 | `<워커 data_dir>/pair-state/<pair_id>.json`. 폴더는 0700. 한 `data_dir` 을 두 짝지은 설정이 써도 `pair_id` 가 달라 겹치지 않는다. M5t-b 세션 폴더에 두지 않는다(F55) |
| 쓰기 | 같은 폴더에 `.<pair_id>.json.tmp-<pid>-<hex8>` 을 `O_CREAT` · `O_EXCL` · `O_WRONLY` · 0600 → 쓰기 → `fsync` → `os.replace` → 폴더 `fsync`. 쓰기 실패는 워커 로그 한 줄(10분 되풀이)이고 일은 멈추지 않는다 |
| 키(v1) | `v`(1) · `pair_id` · `dialed`(쓰는 서버의 `canonical_dialed`, 아직 없으면 null) · `server_identity`(`verified` · `failing` · `unknown`) · `registration`(`none` · `registered` · `retrying` · `failed`) · `reason`(null · `hello_bad_mac` · `hello_wrong_address` · `hello_no_answer` · `hello_unrecognized` · `proof_expired` · `proof_reused` · `proof_bad_mac` · `token_rejected` · `pool_mismatch`) · `since`(이 조합이 시작된 UTC ISO) · `updated_at`(UTC ISO) · `pid` · `process_start`(M5t-b PR1 의 프로세스 시작 시각 형식 `linux:<starttime>` · `ps:<lstart>`, 못 읽으면 null) · `session_tag`(M5t-b 세션 id 의 sha256 소문자 hex 앞 12자, 세션 전이면 null) · `exited`(null 또는 `{code, reason, at}`, `reason` ∈ `stopped` · `fenced` · `protocol` · `token_rejected` · `proof_bad_mac` · `pool_mismatch`) |
| 비밀 없음 | 토큰 · `pair_secret` · K · 코드 · 세션 id 값을 싣지 않는다. `pair_id` 는 hello 가 평문으로 보내는 값이라 비밀이 아니다(PI39 · §8) |

읽는 쪽은 `rcm worker --check` 의 `worker state` 행과 `rcm doctor` 의 `pairing.worker_state` 다. 판정은 `pair_state.py` 의
순수 함수 `judge_pair_state(doc, now, alive)` 하나가 한다.

| 파일 | 판정 | `rcm worker --check` | `rcm doctor` |
|---|---|---|---|
| 없음 | 이 설정으로 돈 짝지은 워커가 없다 | warn 「no paired worker has run with this config yet」 | `warnings`(세지 않음) |
| 읽을 수 없음(링크 · 보통 파일 아님 · 0600 아님 · JSON 아님 · `v` ≠ 1 · `pair_id` 가 설정과 다름) | 모름 | warn 「cannot read the worker's state file」 | `undecided`(3) |
| `exited.reason` ∈ `token_rejected` · `proof_bad_mac` · `pool_mismatch` | FAIL | FAIL 「the worker stopped at <at>: <J6 · J7 · J9 문장 요지>」 | `problems`(1) |
| `exited` 가 그 밖(`stopped` · `fenced` · `protocol`) | 돌지 않음 | warn 「the worker is not running (stopped at <at>)」 | `warnings` |
| `exited` 없음 · 프로세스(`pid` + `process_start`)가 살아 있지 않음 | 모름 | warn 「the worker that wrote this at <updated_at> is not running」 | `undecided` |
| 살아 있음 · `updated_at` 이 `PAIR_STATE_STALE_SECONDS`(180초)보다 오래됨 또는 60초 넘게 미래 | 모름 | warn 「not updated for N min」 | `undecided` |
| 새로움 · `verified` + `registered` | ok | ok | — |
| 새로움 · `verified` + `none` · `retrying` | 모름 | warn(그 `reason` 의 문장) | `undecided` |
| 새로움 · `failing` | FAIL | FAIL | `problems` |
| 새로움 · `unknown` | 모름 | warn | `undecided` |

`rcm worker --check` 는 오늘 규칙대로 ok · warn · FAIL 을 보이고 FAIL 이 있으면 1 로 끝난다. 그래서 모름은 warn 으로 보인다.
자기 hello 결과를 보이는 `server identity` 행은 따로 있다(4.6).

#### 워커 로그 문장

판정마다 한 문장이다. 값은 싣지 않고 주소는 `dialed` 로 적는다.

| 판정 | 문장 |
|---|---|
| J2 | `server at <dialed> could not prove it is the coordinator this worker was paired with — another machine may hold this address, or the coordinator's pair.key was reset (then pair again: rcm pair --worker on the coordinator, then rcm setup --role worker --pair); not sending the token, retrying` |
| J3 | `the coordinator does not answer to <dialed> — use the URL rcm pair printed, or set public_url on the coordinator; not sending the token, retrying` |
| J4 · J5 | `cannot verify the server at <dialed> yet (<상태 코드 또는 no answer>) — not sending the token, retrying` |
| J1(경고 뒤) | `server at <dialed> verified again` |
| J6 | `the coordinator at <dialed> (verified just now) no longer knows this worker's token — it was revoked or replaced; pair again: rcm pair --worker on the coordinator, then rcm setup --role worker --pair (stopping after running jobs; the service will not restart it)` |
| J7 | `the coordinator at <dialed> rejected this worker's pairing proof 3 times — this worker's token and pair_secret do not belong together; pair again: … (stopping after running jobs; the service will not restart it)` |
| J8 | `the coordinator rejected the pairing proof (<expired 또는 reused>) — registering again with a new challenge` |
| 끝내는 중(되풀이) | `stopping: <J6 또는 J7 요지> — waiting for N running job(s); cancel requests cannot reach this worker now` |

#### 짝지은 클라이언트 (V1-4)

`client.toml` 에 `pair_secret` 이 있는 클라이언트는 명령이 어느 단계에 있느냐에 따라 이렇게 끝난다. 결과를 모르는 자리에서는
3 이다(PLAN.md:118).

| 명령 · 단계 | J1 | J2 · J3 | J4 · J5 | 401 |
|---|---|---|---|---|
| 서버에 아무것도 보내기 전: `rcm run`(제출 전) · `jobs` · `top`(첫 화면) · `machines` · `cancel`(보내기 전) · `check` · 따라가기 · `rcm pair`(창 열기 전) | 진행 | 종료 2 + 같은 뜻의 문장(아무것도 하지 않았다) | 오늘의 「닿지 못함」 길(토큰 0, F56 — 대개 3) | 새 hello 가 J1 이면 오늘의 401 처리(2), 아니면 그 판정 |
| 무엇인가 한 뒤: `rcm run` 의 업로드 · 기다림 단계 · `rcm wait` · `rcm top --watch` 의 다음 화면 · `rcm cancel` 보낸 뒤 · `rcm pair` 의 기다림 | 진행 | 종료 3 + 「the job (or window) stays on the server; the result is unknown here」(PLAN.md:118 — 2 는 fail-open) | 오늘의 재연결 · 기다림(토큰 0). `rcm top --watch` 는 「unreachable」 화면 | 새 hello 가 J1 이면 3, 아니면 그 판정 |

#### `auto` 표 (결정 315)

§1 의 R7~R10 이 가리키는 단 하나의 출처다.

| # | 설정 | 명령 | 동작 | dev 의 오늘(M5t-a) | 결정 |
|---|---|---|---|---|---|
| A1 | 워커 · 짝짓지 않음 · `auto`/빈 값 | `rcm worker` | mDNS 질의 전에 종료 2: 「server = "auto" needs a paired worker — pair it (rcm pair --worker on the coordinator, then rcm setup --role worker --pair) or set server to the coordinator's URL」 | 찾고 묻지 않고 쓴다(F45) | 283 · 315 |
| A2 | 워커 · 짝지음 · `auto` | `rcm worker` | 후보 최대 8개에 hello, J1 이 정확히 하나면 그 주소(묻지 않음 — hello 통과가 사람 확인을 대신). 0개 · 둘 이상이면 종료하지 않고 J4 처럼 물러서며 다시 찾는다(상태 `unknown`) | — | 310 · 315 |
| A3 | 워커 · 짝짓지 않음 · `auto` | `rcm worker --check` | A1 과 같은 문장 · 종료 2, mDNS 0 · 요청 0 | TTY 면 묻고 비TTY · 거절이면 3(TD28) | 315 |
| A4 | 워커 · 짝지음 · `auto` | `rcm worker --check` | A2 의 후보 규칙. 정확히 하나면 묻지 않고 그 서버로 점검. 0개 · 둘 이상이면 `server identity` FAIL(토큰 0), 종료 1 | 묻는다 | 315 |
| A5 | 워커 · 짝짓지 않음 · `auto` | `rcm doctor --role worker` | mDNS · 요청 0. `problems` 에 A1 문장(「rcm worker refuses this config」) → 종료 1. 서버 없이 재는 것(rcm 요구 · 호스트 사실 · 서비스 환경)은 그대로 잰다 | warn(TD48) + 묻기(TD10) · 비TTY 3(TD28) | 315 |
| A6 | 워커 · 짝지음 · `auto` | `rcm doctor --role worker` | A2 의 후보 규칙, 묻지 않음. 0개 · 둘 이상이면 `undecided` 「no server on this network proves this worker's identity」 · 「several servers prove …」(3). `auto` warn 없음 | warn + 묻기 | 315 |
| A7 | 클라이언트 · 짝짓지 않음 · 토큰 있음 · `auto`/빈 값 | 토큰을 쓰는 모든 명령 · `rcm doctor --role client` | 명령: `_resolve_server` 가 mDNS 전에 `ConfigError`(종료 2) 「server = "auto" with a token needs a paired client — pair it (rcm pair --client, then rcm setup --role client --pair) or set server」. doctor: mDNS · 요청 0, `problems`(1) | 찾고 토큰을 보낸다 · doctor 는 묻는다 | 285 · 315 |
| A8 | 클라이언트 · 짝짓지 않음 · 토큰 없음 · `auto` | 읽기 명령 · `rcm doctor --role client` | 명령: 찾기는 오늘대로, 따라가기 없음 + stderr 한 줄(300). doctor: 결정 246 그대로(찾고 TTY 면 묻고 비TTY 3, 토큰 없음 `undecided`) — 토큰을 보내지 않고 doctor 는 따라가지 않으므로(`NO_FOLLOW_COMMANDS`) 300 과 어긋나지 않는다 | 같다(따라가기만 다름) | 300 · 315 |
| A9 | 클라이언트 · 짝지음 · `auto` | 모든 명령 · doctor | 후보 최대 8개에 hello, 정확히 하나면 그것(묻지 않음). 0개 · 둘 이상이면 명령은 `ConfigError` 종료 2, doctor 는 `undecided`(3) | — | 285 · 310 · 315 |
| A10 | 짝지음 · 명시 URL(워커 · 클라이언트) | 모든 명령 · `--check` · doctor | 그 URL 에 hello 먼저, 묻지 않음(명시 URL 은 246 에서도 묻지 않았다). 판정은 J 표 · 클라이언트 표 | 묻지 않음 | 290 |

짝짓지 않은 명시 URL 은 오늘 그대로 hello 를 하지 않는다. `rcm worker --check` 와 doctor 에 warn 「not paired …」 한 줄만
더한다(2판).

### 4.4 짝짓기 창 (결정 280 · 286 · 288 · 291 · 292 · 301 · 307 · 319)

짝짓기 창은 사람이 코디네이터에서 여는 짧은 발급 기간이다. 창이 열려 있는 동안 코드 한 번으로 토큰이 나온다. 2판은 교환이
코드를 보내지 않도록 바꿨다(301). 코드 입력 규칙과 `host_name` 정리, 창 id 도 더했다. 3판은 코드 키를 scrypt 로 바꾸고
형식이 틀린 교환 본문을 창 조회 전의 400 으로 옮겼다. 닫힌 창은 목록에 1시간 남긴다(319). 코디네이터 밖에서 친 `rcm pair` 의
평문 경고도 더했다(V2-7).

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 창을 두는 곳 | 서버 프로세스 메모리. DB 표를 두지 않는다(3판의 `pair_windows` 를 버림). 재시작하면 열린 창은 사라진다(수명을 늘리지 않음). 익명 실패가 DB 를 쓰지 않는다 | T5 |
| 창 수 | kind(`worker` · `client`)마다 하나. 같은 kind 가 열려 있으면 `POST /pair` 는 409 `pair_window_open` + `expires_at`. 두 kind 는 함께 열 수 있다 | T7 |
| 코드 | 10자, RFC 4648 base32 알파벳(A–Z, 2–7)을 `secrets.choice` 로. 화면에 `ABCDE-FGHJK` 꼴. 서버는 코드 키 `pair_code_key(정규화한 코드)` 32바이트만 든다: `hashlib.scrypt(코드 ASCII, salt=b"rcm-pair-code-v1", n=2**15, r=8, p=1, maxmem=64 MiB, dklen=32)`(319 a, M7). 서버는 창을 열 때(`POST /pair`, admin) 한 번 계산한다. 익명 교환은 scrypt 를 부르지 않는다(HMAC 비교만 — 과부하 방지). 워커는 setup 에서 한 번 | T4 · T5 |
| 코드 입력(307, V2-8) | `rcm setup --pair` 가 정규화(대문자로 · `-` 와 공백 제거) 뒤 형식을 먼저 본다: 10자가 아니거나 알파벳 밖 글자(0 · 1 · 8 · 9 · 기호 등)가 있으면 **보내기 전에** 종료 2 「pairing codes are 10 characters of A–Z and 2–7 (no 0, 1, 8 or 9) — check the code」. 요청이 나가지 않으므로 창의 실패로 세지 않는다. 0→O · 1→I · 8→B 같은 바꿈은 하지 않는다(모호하다). 서버도 형식이 틀린 증명 요청은 같은 404 로 답하되 세지 않는다(맞을 수 없는 요청이다) | T5 |
| 수명 | 기본 10분, 상한 60분(`rcm pair --ttl <분>`, 1~60, 밖이면 종료 2 · 서버 400). 서버 시계로만 판정. reload 는 바꾸지 않는다 | T5 |
| 실패 세기 | 그 kind 의 열린 창에 센다(출처 무관, 288). 5번째 실패에 창을 닫고 이유 `too_many_wrong_codes`. 서버 로그 한 줄 「pair: worker window closed after 5 wrong codes — open a new one with rcm pair」. 기다리던 `rcm pair` 가 같은 문장과 종료 1 | T5 |
| 교환 요청(301 · 319 b) | `POST /pair/exchange`, 익명, 본문 ≤ 1 KiB `{"v": 1, "kind", "host_name", "worker_nonce": <hex64>, "proof": pair_mac(코드 키, "rcm-pair-request-v1", kind, host_name, worker_nonce)}`. 코드는 본문 · 헤더 · URL 어디에도 없다. 형식: 본문이 JSON 객체 · `v` 는 정수 1(bool 아님) · `kind` 문자열 · `host_name` 문자열이고 0~64 코드 포인트(`PAIR_HOST_NAME_MAX`)이며 엄격한 UTF-8 로 인코딩된다(홀로 선 서러게이트 없음) · `worker_nonce` · `proof` 는 `^[0-9a-f]{64}$`. 어긋나면 **창을 찾기 전에** 400 `{"error": "bad pairing request", "code": "bad_pair_request"}`(고정 바이트). 창 상태와 무관한 답이라 오라클이 아니고, 실패로 세지 않는다. 500 을 내지 않는다(V2-5: 서러게이트가 MAC 용 인코딩에서 터졌다). 형식이 맞고 `kind` 가 `worker` · `client` 밖이면 같은 404 | T12 · T4 · T5 |
| 교환 처리 | 창 잠금 안에서: 본문 `kind` 의 창이 열림 · 만료 전인지 → `proof` 형식 → `compare_digest(proof, 그 창의 코드 키로 계산한 증명)` → 틀리면 실패 1 · 같은 404 → 맞으면 이름 결정 → 이름 충돌이면 409 `{"error": "the coordinator already has a token named '<name>'", "code": "name_taken", "name": "<name>"}`(창은 열린 채, 실패로 세지 않는다. 증명이 맞은 뒤에만 나오는 답이라 코드를 가진 쪽에게만 보인다) → 창을 `used` 로 닫음 → 토큰 행 쓰기(한 트랜잭션) → `server_nonce = secrets.token_hex(32)` → 200(4.2.1 의 모양). 행 쓰기가 실패하면 503 `pair_failed`(창은 닫힌 채) | T5 · T7 · T12 |
| 교환 응답 MAC(301) | `mac = pair_mac(코드 키, "rcm-pair-response-v1", kind, host_name, worker_nonce, server_nonce, name, pool 또는 "", presets_count 10진 또는 "", token, pair_secret)` — 두 nonce 와 전체 페이로드를 덮는다. 워커는 **아무것도 저장하기 전에** 형식과 MAC 을 본다. 틀리면 저장 0 · 종료 1 「the server at <URL> answered but does not know this code — it is not the coordinator that showed it; nothing was saved」. 응답의 `kind` 가 요청한 kind 와 다르면 같은 처리 | T12 |
| 같은 404 | 틀린 증명 · 닫힌 창 · 연 적 없는 창 · 만료 · 이미 쓴 코드 · 그 kind 의 창 없음 · 모르는 kind 는 모두 404 `{"error": "no open pairing window for that code"}`(고정 바이트, `not_found_hint` 없음). 셈은 「열린 창에 대한 형식 맞는 틀린 증명」만. 2판의 「형식이 틀린 증명」은 400 으로 옮겼다 | T5 · T7 |
| 오프라인 추측(301 의 남는 위험) | 증명을 관찰한 쪽(엿보기 · 교환 URL 의 사칭자)은 창이 열린 동안 오프라인으로 코드를 추측할 수 있다. 5회 제한은 온라인만 막는다. 숫자는 M7(scrypt). 창이 닫히면 맞힌 코드는 쓸모없다 | T4 · T12 |
| 동시 교환 | 창마다 잠금 하나. 같은 코드 N개 동시 → 200 정확히 1 | T5 |
| 이름 | `rcm pair --name N` 이면 N(`_NAME_RE`, 열 때 이미 있으면 409 `name_taken`). 없으면 `clean_pair_label(host_name)`: `_NAME_RE` 밖 글자 → `-`, 앞의 영숫자 아닌 글자 제거, 64자 자르기, 남는 글자가 없으면 `worker-<hex6>`(client 창이면 `client-<hex6>`) | T8 |
| `host_name` 보이기(V2-14) | 서버 로그 · `GET /pair` 의 `paired.host_name` · `rcm pair` 완료 줄 · `tokens.paired_host` · `rcm token list` 에는 원문이 아니라 `clean_pair_label(host_name)`(대체 이름 없이, 비면 「—」)만 싣는다 — 제어문자 · 줄바꿈이 로그 줄을 만들지 못한다. 원문은 MAC 계산에만 쓰고 저장하지 않는다 | T8 |
| `--replace` | `--name` 과 함께만. 그 이름의 행이 같은 kind 여야 한다(아니면 400 `replace_needs_same_kind`). 교환 때 그 행을 한 UPDATE 로 바꾼다: 새 `sha256` · `pair_id` · `pool` · `paired_host` · `paired_addr` · `created_at`, `revoked_at = NULL`. 옛 토큰은 그 순간부터 401(F1 · F2) | T6 · T8 |
| 풀 | worker 창만. `--pool P`(없으면 `default`). 열 때 그 풀로 갈 프리셋 수를 계산해(4.5 의 같은 함수) 0 이면 응답 `warnings[]` 에 「0 presets reach pool P」 — 거절하지 않는다 | T10 |
| 화면 URL | `POST /pair` 응답 `urls[]`: `public_url` 이 있으면 그것 하나. 없고 bind 가 특정 비루프백 주소면 `http://<bind>:<port>`. `0.0.0.0` 이면 `local_ipv4s()` 의 주소마다 + 광고가 켜져 있으면 `http://<host>.local:<port>`. 루프백 bind 에 `public_url` 이 없으면 409 `pair_unreachable` 「this server listens on <bind> only — other machines cannot reach it; set bind (and public_url) first」 — 창을 열지 않는다 | T2 |
| 평문 경고 | `rcm pair` 와 `rcm setup --pair` 는 URL 의 호스트가 루프백도 Tailscale 대역(100.64.0.0/10)도 아니면 stderr 에 「the token and the pairing secret cross this network in plain text — prefer the server's Tailscale address」(301 뒤로 코드는 망으로 나가지 않는다), 이름이면 「a name URL cannot stop a relay on this network — use an IP or Tailscale address」 | T4 · T2 |
| 창 id(V2-9) | `POST /pair` 201 응답에 `id`(8 무작위 바이트 hex 16자), `GET /pair` 의 창마다 `id`. `rcm pair` 는 이 id 로 자기 창을 따라간다(재시작으로 사라진 창을 알아본다) | — |
| 남기는 것(N4 → 286) | 교환 성공 때 서버 로그 「pair: <kind> '<name>' paired from <addr> (host <정리한 host_name>, pool <p>)」(`<addr>` = 교환 요청의 접속 주소, 프록시 뒤면 프록시 주소), `tokens.paired_host` · `paired_addr`, 기다리던 `rcm pair` 완료 줄. 두 번째 승인 없음 | T8 |
| 코드 노출(V2-7) | 코드는 `POST /pair` 의 201 응답에만 있고 `rcm pair` 가 stdout 에 한 번 찍는다. 워커의 교환 요청에는 없다. `GET /pair` · 로그 · 이벤트 · `/api/*` 에 없다. 코디네이터 밖에서 친 `rcm pair` 는 201 응답(코드)과 admin 토큰이 그 길을 지난다 — `rcm pair` 는 서버 URL 의 호스트가 루프백도 100.64.0.0/10 도 아니면 stderr 에 「the pairing code and the admin token cross this network in plain text — run rcm pair on the coordinator or over Tailscale」 | T4 · T5 |
| 관리 라우트(V2-10) | `POST /pair` 그대로. `GET /pair`(admin) → `{windows: [{id, kind, pool, name, opened_by, opened_at, expires_at, closed_at, state, reason, paired}]}`. 열린 창 전부 + 닫힌 창 가운데 `closed_at` 이 `PAIR_LIST_KEEP_SECONDS`(3600초) 안인 것, 닫힌 것은 최근 닫힌 순으로 많아야 `PAIR_LIST_KEEP`(20)개. `closed_at` = 쓰임 · 실패 5번 · 취소 시각, 만료면 `expires_at`(열린 창은 null). 2판의 「최근 1시간 · 20개」(여는 시각 기준)는 막 끝난 60분 창을 빼서 기다리던 `rcm pair` 가 「재시작」으로 잘못 말할 수 있었다. `POST /pair/cancel` 그대로 | T7 |

### 4.5 토큰에 묶인 풀 (결정 284 · 294)

짝짓기로 받은 worker 토큰에는 풀을 묶는다. 등록과 `/worker/spec` 이 그 풀을 본다.

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 저장 | `tokens.pool`. 짝짓기 worker 토큰은 창의 풀. client · admin · 수동 토큰 · v27 전 토큰은 NULL | T10 |
| 등록 검사 | `tokens.pool` 이 있고 본문 `pool`(없으면 `default`)과 다르면 409 `{"error": "this worker token is bound to pool '<P>' — set pool = \"<P>\" in worker.toml or pair again", "code": "pool_mismatch", "bound_pool": "<P>"}`. 증명 403 다음, 자리는 아래 「등록 검사 자리」 행 → `workers` 행 불변, 도는 잡 불변 | T10 |
| 워커의 처리 | 409 `pool_mismatch` 는 로그에 묶인 풀과 고치는 법을 적고 종료 2 | — |
| `/worker/spec` 거르기 | 그 토큰의 풀 = `tokens.pool` → (NULL 이면) 그 이름의 `workers` 행 풀 → (등록 전이면) 질의 `?pool=<p>`(`_NAME_RE`, 없으면 `default`). 프리셋은 `placement == "capable"` 이거나 그 풀이 프리셋의 `pool` · `pools` 에 있을 때만, 그리고 후보 함수 `machines_for` 가 원격을 빼지 않을 때(`local_only`)만 싣는다. 거르지 않은 전체를 주는 길은 없다(N2) | T10 |
| 질의 `pool` | `WorkerClient.spec()` 이 자기 `pool` 을 싣는다. 묶인 토큰은 질의를 무시한다. 묶이지 않은 토큰이 질의로 다른 풀을 읽는 것은 그 풀로 등록하는 것과 같은 권한이다(오늘 N2 가 허용) | — |
| 한 함수 | 거르기 규칙은 `spec_presets_for_pool(pool)` 하나. `/worker/spec`, 창을 열 때의 `presets_count`, 교환 응답이 같은 함수를 부른다 | — |
| 표시 | `rcm token list` 의 `pool` 열, NULL 은 「—」 | — |

2판은 아래를 더했다. 등록 검사의 자리는 M5t-b 의 등록 순서로 다시 적었다(F30).

| 항목 | 정한 것 |
|---|---|
| `TokenInfo` · `verify_token`(V2-13) | `TokenInfo` 에 `pool: str 또는 None = None` · `pair_id: str 또는 None = None` · `paired_host` · `paired_addr`(기본 None). `verify_token` · `list_tokens` 의 SELECT 에 네 칸을 더한다(PR1). 등록 검사 · spec 거르기 · 증명 검사는 이 값을 쓴다 |
| 등록 검사 자리 | F30 의 4 자리, `check_register_proof` 다음. 실패는 세션 · 펜싱 · 워커 행 · 능력 보고 · 잡 0 |
| `worker_spec` 자리(V1-8) | F9 — M5t-a · M5t-b 뒤의 자리를 구현 PR 이 다시 찾는다. 거르기 함수 이름 `spec_presets_for_pool(pool)` 은 그대로 |

### 4.6 CLI (결정 292 · 296 · 303 · 304 · 305 · 307 · 312 · 314 · 315 · 316 · 317 · 319)

새로 생기거나 바뀌는 명령과 그 종료 코드다. 3판은 다섯 행을 고치고 처음 설정에 쓰는 세 행(`rcm init worker`, 템플릿 · 예시,
코디네이터 setup 이 찍는 명령)을 더했다.

| 명령 | 정한 것 | 종료 코드 |
|---|---|---|
| `rcm pair --worker [--pool P] [--name N] [--replace] [--ttl 분]` · `rcm pair --client [--name N] [--replace] [--ttl 분]` | 클라이언트 설정(`server` · `RCM_TOKEN`)의 **admin** 토큰으로 `POST /pair`. stdout: 코드 · URL(들) · 만료 시각(로컬 시간)과 남은 분 · kind · 풀 · 이름 · 갈 프리셋 수. 경고는 stderr. 그 뒤 2초마다 `GET /pair` 로 자기 창 `id` 를 기다린다. 쓰이면 완료 줄(이름 · kind · 풀 · 정리한 `host_name` · 주소 · 시각 · 다음 할 일 `rcm machines`, `rcm token revoke <name>`). DB 를 열지 않는다. admin 토큰이 없으면 「pairing needs an admin token: rcm token add <name> --admin on the server」. 기다리는 동안: `GET /pair` 연결 실패는 계속 시도한다. 다시 닿았을 때 그 `id` 의 창이 없으면 종료 3 이다(결과를 모른다, 312). 재시작 직전에 창이 쓰였을 수 있으므로 창 `id` 와 이어진 영속 상태가 완료나 미사용을 증명하지 않는 한 1 로 보고하지 않는다. 창은 메모리에만 있으므로(291) 그런 영속 상태는 없다. 그래서 재시작 뒤에는 늘 「the server restarted — whether the window was used is unknown; check rcm token list」 를 찍고 종료 3 이다. `expires_at` + 30초까지 한 번도 닿지 못하면 「lost the server while waiting — the window closed at <time> unless it was used; check rcm token list」 종료 3(결과를 모른다). Ctrl-C 는 `POST /pair/cancel` 을 한 번 시도하고(실패하면 「could not cancel — the window closes at <time>」) 130 3판에서 더한 것: 클라이언트 설정이 짝지은 설정이면 첫 요청 전에 hello 하고(285 · V1-13 — `rcm pair` 도 같은 `Client` 를 지난다) 판정은 4.3 클라이언트 표(창 열기 전 J2 · J3 은 2, 기다리는 중 J2 · J3 은 3). 코디네이터 밖 평문 경고(4.4 코드 노출). 기다리는 동안 자기 `id` 가 목록에 없으면 「the server restarted …」 3 — 닫힌 창은 1시간 목록에 남으므로(4.4) 막 끝난 창을 재시작으로 보지 않는다 | **0** 짝지어짐 · **1** 창이 쓰이지 않고 닫힘(만료 · 틀린 코드 5번 · 취소됨) 또는 서버가 열기를 거절(409 `pair_window_open` · `pair_unreachable` · `name_taken`) · **2** 사용법(`--ttl` 1~60 밖, `--replace` 에 `--name` 없음, `--worker`/`--client` 둘 다 없음 · 둘 다 있음, 서버의 400 · 401 · 403, admin 토큰 없음) · **3** 결과를 모름: 기다리다 서버를 잃고 만료 + 30초까지 돌아오지 않음, 또는 재시작으로 창이 사라졌는데 영속 상태가 완료나 미사용을 증명하지 못함 · **130** Ctrl-C(312, V2-9) |
| `rcm pair --cancel --worker` · `--cancel --client` | `POST /pair/cancel` | 0 닫음 · 1 열린 창 없음 · 2 사용법 |
| `rcm setup --role worker --server URL --pair` | M5t-a 의 setup 에 `--pair` 를 더한다. 코드는 TTY 면 `getpass` 프롬프트, 아니면 stdin 한 줄. 인자로 받지 않는다(`--code` 는 argparse 가 모르는 인자로 종료 2). 순서: ① 쓸 파일이 이미 있으면 `--force` 없이 거절(교환 전에) ② 코드 형식 검사(307, 보내기 전) ③ 교환(301 — 코드는 보내지 않는다) ④ 응답 MAC 검사, 틀리면 저장 0 · 종료 1 ⑤ `worker.toml` 을 임시 파일 `O_CREAT` · `O_EXCL` 0600 → 쓰기 · `fsync` → 이름 바꾸기로(`server` = 넣은 URL, `token`, `pair_secret`, `pool`, `name` = 토큰 이름) ⑥ **주소 확인**(V1-2): 저장한 URL 로 hello 한 번 — 신원 검사가 아니다(교환 MAC 이 이미 신원을 확인했다). 그 URL 이 hello 의 `dialed` 규칙을 통과하는지(예: `public_url` 없는 이름 URL 의 421)를 지금 알려 주려는 것이다. `ok` 면 완료 줄 「paired with <URL> as '<name>' · pool <p> · <n> presets can come here · results go back to the coordinator and the requester's rcm wait gets them」 종료 0. `refused_address` · `mismatch` · `other` · `unavailable` 이면 파일은 둔 채 stderr 에 이유와 고치는 법(「set public_url on the coordinator, then rcm worker --check」) 종료 1. `--pair` 면 `--yes` 에서 `--pool` 이 필요 없다(창이 정한다). 주면 창의 풀과 다를 때 종료 2 3판에서 더한 것: 교환의 `host_name` 은 `socket.gethostname()` 을 첫 `.` 앞에서 자른 값을 64 코드 포인트로 자른 것(F60, 서버에 이름을 맞추는 것은 코디네이터의 `--name`). setup 에 `--name` 은 없다(argparse 가 모르는 인자로 종료 2). 교환 답은 아래 「교환 오류 표」. 교환과 주소 확인은 리디렉트를 따르지 않는다(309). 끝줄은 TR13 개정(R13): 「start the service, then rcm doctor --role worker」 | TR28 그대로 0 · 1 · 2 |
| `rcm setup --role client --server URL --pair` | 같은 흐름으로 `client.toml`(`server`, `token`, `pair_secret`) 0600 | 같다 |
| `rcm worker`(짝지은 설정) | 4.3 판정 표 · 상태 전이 · 상태 파일 | **0** 멈춤 · 펜싱(M5t-b) · J6 · J7 · **1** exec 실패(오늘) · **2** 등록 막힘(오늘 · J9 · 옛 토큰 + `auto`(A1) · 짝짓지 않은 설정의 `pair_proof_required`) · **3** 프로토콜 불일치(M5t-b 만). J2~J5 · J8 로는 종료하지 않는다 |
| `rcm token list` | 열 뒤에 `pool`(NULL 「—」)과 `paired from`(`<정리한 paired_host> @ <paired_addr>` 또는 「—」) | 오늘 그대로 |
| `rcm worker --check` | python 행 다음에 `server identity` 행: 짝지은 설정이면 자기 hello 한 번(`auto` 는 A4) — J1 ok, J2 · J3 FAIL(그 문장), J4 · J5 FAIL(오늘의 「cannot reach」 server 행과 같은 무게). J1 이 아니면 토큰을 쓰는 행(token · fingerprint · presets)은 「— skipped: server not verified」이고 토큰을 보내지 않는다. 그다음 `worker state` 행(4.3 읽는 표). 짝짓지 않은 설정: `server identity` warn 「not paired — this worker cannot tell this server from another machine; pair it with rcm pair」, `auto` 면 A3 | 오늘 규칙(FAIL 이 있으면 1), A3 은 2 |
| `rcm check` | 짝지은 클라이언트만 `server identity` 행 | 오늘 규칙 |
| `rcm doctor` | 워커 · 클라이언트 역할의 서버 단계 앞에 hello(A5~A10). 짝지은 설정: J1 이면 오늘처럼 토큰 요청, J2 · J3 은 `problems`, J4 · J5 는 `undecided`, 토큰 요청 0. 워커 역할은 상태 파일을 읽는다(4.3 읽는 표). `--json` 에 `pairing` 을 더한다: `{"paired": bool, "server_identity": "verified" · "failing" · "unknown" · null, "reason": <4.3 reason 또는 null>, "worker_state": {"state": "ok" · "unknown" · "fail" · "none", "text", "updated_at"} 또는 null}`. 짝짓지 않은 설정은 `{"paired": false, …null}` | 오늘 규칙 1 > 3 > 0(F46) |
| `NO_FOLLOW_COMMANDS` | `pair` 를 더한다(`follow.py:80-82`). setup 은 M5t-a PR4 가 넣는다 | — |
| `rcm token add` | 바꾸지 않는다 | — |
| `rcm init worker`(316) | 쓰는 파일은 템플릿 그대로(`server` 는 `--server` 가 없으면 `auto` — 바꾸지 않는다). stderr 첫 줄: 「worker.toml is not finished: pair this machine first — on the coordinator: rcm pair --worker; here: rcm setup --role worker --pair --server <URL it printed> --force」. 둘째 줄(짝짓지 않는 길): 「or write the coordinator's URL in server and use a token from rcm token add <name> --worker (RCM_WORKER_TOKEN, or token in this file)」. 오늘의 `pool` 줄 · 서비스 줄은 그대로 | 오늘 그대로 |
| 템플릿 · 예시(316) | `templates/worker.toml` = `examples/worker.toml`(바이트 같음). 머리 주석을 다음으로: 「# rcm worker configuration — another computer that runs jobs for the build machine.」 / 「# Pair first — it writes this file with the token and pair_secret:」 / 「#   on the coordinator:  rcm pair --worker [--pool P]」 / 「#   on this machine:     rcm setup --role worker --pair --server <URL rcm pair printed>」 / 「# rcm init worker writes this template unfinished: without pair_secret, server = "auto" is refused.」 / 「# Without pairing: set server to the coordinator's URL and use a token from」 / 「#   rcm token add <name> --worker   (RCM_WORKER_TOKEN, or token = "…" below)」 / 「# Check:   rcm worker --check」 / 「# Service: rcm init worker --service-file PATH   (launchd plist or systemd unit, file only)」 / 「# Flags (--server, --pool, --lanes, --name, --data) override these values.」. `server = "auto"` 줄의 주석: 「# paired workers only (rcm setup --pair); otherwise the coordinator's URL」. 그 아래에 주석 줄 「# pair_secret = "…"               # written by rcm setup --pair — never by hand」 | — |
| 코디네이터 setup 이 찍는 워커 명령(R12) | 「add a worker: rcm pair --worker (then on the worker: rcm setup --role worker --pair --server <URL>)」 먼저, 그다음 「without pairing: rcm token add <name> --worker」 | TR28 그대로 |

아래는 `rcm setup --pair` 의 교환 오류 표다(V2-4). 「저장」은 `worker.toml` 이나 `client.toml` 을 쓰는 것이다. 어느 행에서도
토큰 · `pair_secret` · 코드 값은 찍지 않는다.

| 교환 답 | 저장 | 종료 | 문장 |
|---|---|---|---|
| 200 · 형식 맞음 · MAC 맞음 | 한다 → 주소 확인 | 0(주소 확인 실패면 1, 파일은 남는다 — 2판) | 2판의 완료 줄 |
| 200 · 형식 틀림 · 키 빠짐 · `kind` 다름 · MAC 틀림 | 안 함 | 1 | 「the server at <URL> answered but does not know this code — it is not the coordinator that showed it; nothing was saved」 |
| 404 | 안 함 | 1 | 「the code was wrong or used, or the window is closed — ask for a new code」 |
| 400 `bad_pair_request` | 안 함 | 1 | 「the coordinator refused the request as malformed — the two rcm versions may differ; nothing was saved」(setup 이 형식을 먼저 보므로 판 차이일 때만 나온다) |
| 409 `name_taken` | 안 함 | 1 | 「the coordinator already has a token named '<name>' — on the coordinator: rcm pair --cancel --worker, then rcm pair --worker --name <another name> (or --name <name> --replace); nothing was saved」(창은 열린 채, 세지 않음) |
| 503 `pair_failed` | 안 함 | 1 | 「the coordinator could not store the token and closed the window — open a new one (rcm pair) and run this again」 |
| 3xx(따르지 않음) · 413 · 429 · 그 밖의 4xx | 안 함 | 1 | 「the coordinator answered <status>: <정리한 error> — nothing was saved」 |
| 요청을 보내기 전 연결 실패(거부 · 이름 풀이 실패) | 안 함 | 1 | 「cannot reach <URL> — nothing was sent」 |
| 보낸 뒤 연결 끊김 · 시간 초과 · 503(`pair_failed` 아님) · 5xx | 안 함 | 1 | 「no answer after the request was sent — a token may have been issued: on the coordinator check rcm token list (paired from) and revoke it, then open a new window」(이름을 모르므로 이름을 지어 말하지 않는다 — 2판 PI8 b 를 고침) |
| 200 인데 파일을 쓰지 못함 | 안 함 | 1 | 2판 「issued as '<name>' but not saved — on the coordinator: rcm token revoke <name>, then open a new window」 |

### 4.7 얼리는 계약

이 명세 PR 의 머지가 아래 계약을 얼린다. 얼린 뒤에 바꾸려면 오너가 승인하는 새 명세 PR 이 있어야 한다.

| 계약 | 바뀌는 것 |
|---|---|
| `/api/status` `schema_version` | 그대로(새 키 없음) |
| `/api/health` · mDNS TXT | 그대로 |
| DB | v27(2판 그대로) |
| 서버 파일 | `<data_dir>/pair.key`(32바이트, 0600). 없고 살아 있는 짝 토큰이 있으면 기동 거절(306). 하드 링크를 못 하는 파일 시스템이면 기동 거절(4.2 ⑦) |
| 워커 파일(새) | `<워커 data_dir>/pair-state/<pair_id>.json` v1, 키와 값은 4.3 「상태 파일」. 0600, 폴더 0700. 다음 판 `rcm doctor` 가 읽으므로 모양을 얼린다 |
| 와이어 — 새 라우트 | `POST /pair/hello` · `POST /pair/exchange`(익명), `POST /pair` · `GET /pair` · `POST /pair/cancel`(admin). 본문 · 응답 · 바이트는 4.2 · 4.2.1 · 4.4. 오류 코드 `bad_hello` · `bad_pair_request`(400) · `host_not_this_server`(421) · `pair_window_open` · `name_taken`(+`name`) · `pair_unreachable`(409) · `pair_failed`(503) · `replace_needs_same_kind`(400). `GET /pair` 창의 `closed_at` 과 보존 규칙. client 창 교환 응답의 `pool` · `presets_count` null |
| 와이어 — 워커 프로토콜 | `POST /worker/register` 의 선택 키 `pair_proof{challenge, mac}`. 403 `pair_proof_required`(`error` 문장 고정 — 옛 빌드가 찍는다), 403 `pair_proof_invalid` + `reason` ∈ `expired` · `reused` · `bad_mac`(313 — `pair_mismatch` 없음), 409 `pool_mismatch`(+`bound_pool`). `GET /worker/spec` 풀 거르기 · 질의 `pool`. 옛 서버는 `pair_proof` 를 무시한다 |
| 코드 키 | scrypt 매개변수(salt `rcm-pair-code-v1`, n 2^15, r 8, p 1, dklen 32). 바꾸면 판이 다른 rcm 끼리 짝짓지 못한다 |
| 익명 쓰기(V1-11) | 아래 표 |
| 종료 코드 | `rcm worker`: 0 = 멈춤 · 펜싱(M5t-b) · J6 · J7, 1 = exec 실패, 2 = 등록 막힘(오늘 · J9 · A1 · 짝짓지 않은 설정의 `pair_proof_required`), 3 = 프로토콜 불일치(M5t-b). `rcm worker --check`: FAIL 이 있으면 1, A3 은 2. `rcm doctor`: 1 > 3 > 0, 신원 FAIL 1, 모름 3, A5 · A7 1. `rcm pair` 0/1/2/3/130. `rcm setup --pair` 0/1/2. 클라이언트: 아무것도 하기 전 J2 · J3 은 2, 무엇인가 한 뒤 J2 · J3 은 3, A7 · A9 의 설정 오류 2 |
| CLI | `rcm pair` 와 플래그, `rcm setup --pair`(`--name` 없음), `rcm token list` 의 뒤 두 열, `rcm worker --check` 의 `server identity` · `worker state` 행, `rcm check` 의 `server identity` 행, `rcm doctor --json` 의 `pairing`, `rcm init worker` 의 stderr 두 줄, 템플릿 머리 주석, 로그 문장(4.3) |
| 설정 키 | `worker.toml` · `client.toml` 의 `pair_secret`(2판 그대로) |
| 상수 | 2판 상수 + `PAIR_WARN_REPEAT_SECONDS = 600` · `PAIR_STATE_REFRESH_SECONDS = 60` · `PAIR_STATE_STALE_SECONDS = 180` · `PAIR_BAD_MAC_LIMIT = 3` · `PAIR_HOST_NAME_MAX = 64` · `PAIR_LIST_KEEP_SECONDS = 3600` · `PAIR_LIST_KEEP = 20` |
| 리디렉트 | 짝지은 머신의 hello · 토큰 요청 · setup 교환은 3xx 를 따르지 않는다(309) |

익명 쓰기 표에서 「받음」은 익명 요청(`Authorization` 없음)의 POST · PUT · DELETE 에 401 · 403 · 405 가 아닌 답을 준다는
뜻이다. 탐침 경로는 PLAN 「서버 API」 표의 모든 행과 그 표에 없는 갈래다(F57). 그 갈래는 `/api/machines` ·
`/machines/@local/recheck` · `/worker/spec` · `/worker/register` · `/worker/claim` · `/worker/heartbeat` ·
`/worker/capabilities` · `/worker/jobs/1/finish` · `/client/x` · `/api/shelf` · `/shelf/1` · `/shelf/1/restore` · `/gc` ·
`/reload` · `/pair` · `/pair/hello` · `/pair/exchange` · `/pair/cancel` 이고 경로 변수는 `1` · `x` 로 채운다. PR1 은 시작할
때 라우터 갈래를 다시 세어 M5t-a · M5t-b 가 더한 갈래를 목록에 더하고 §13 에 적는다.

| `read_auth` | 익명 쓰기를 받는 (메서드, 라우트) — 정확히 이것 | 근거 |
|---|---|---|
| `none` | `POST /api/eta`(읽기 계산, 상태 불변) · `POST /shelf/<n>/restore`(보관함 보존 연장, M5p) · `POST /pair/hello` · `POST /pair/exchange` | F57 · 297 |
| `basic` | `POST /pair/hello` · `POST /pair/exchange` | F57 · 297 |

### 4.8 호환

옛 판과 새 판이 섞였을 때의 결과를 조합마다 적는다.

| 조합 | 결과 |
|---|---|
| 옛 워커(명시 URL) ↔ v27 서버 | 오늘 그대로. spec 은 등록한 풀로 걸러진다 |
| 옛 워커(`auto`) ↔ 어느 서버 | 새 빌드면 거절(283). 옛 빌드 워커는 오늘대로 |
| **옛 빌드의 `rcm worker --check`(질의 `pool` 없이 `spec()`)** ↔ v27 서버(V2-15) | 서버는 토큰의 묶인 풀 → (NULL 이면) 그 이름의 등록 풀 → (등록 전이면) `default` 로 거른다. 기본 풀이 아닌 옛 워커가 등록 전에 `--check` 를 돌리면 `default` 풀 프리셋을 보고 판정한다 — 표시만 틀리고 받는 잡과는 무관(받는 잡은 등록 풀). 새 빌드는 질의에 자기 `pool` 을 싣는다 |
| 짝지은 워커 ↔ 옛 서버(hello 404) | 토큰 0. `other` → 종료하지 않고 물러서며 다시 시도한다. 경고는 속도를 제한해 되풀이한다(304). 1판의 「종료 3」을 바꿈 |
| 옛 빌드가 `pair_secret` 있는 `worker.toml` 을 읽음 | `unknown key(s): pair_secret` 로 거절(F20) |
| v26 빌드가 v27 DB 를 엶 | `newer_database_message` 로 거절(F3) |
| 옛 클라이언트(`rcm pair` 없음) | 짝짓기를 쓰지 않는다. 오늘 그대로 |
| M5t-b 와 | 등록 증명 자리는 F30 의 4. 증명은 `worker_session_id` 를 묶는다. 종료 3 은 M5t-b 몫만(303, F33 · Q-E) |
| M5t-b 만 아는 옛 빌드 워커(짝짓기 없음)가 복사한 짝지은 토큰을 든다 ↔ v27 서버(V2-11) | 등록이 403 `pair_proof_required` → 옛 빌드는 종료 2(F41) → launchd 10초 · systemd 5초마다 되살아난다(F21). 로그에는 서버의 `error`(업그레이드 · 짝짓기 안내) 뒤에 옛 빌드의 「(rcm token add NAME --worker on the server)」 꼬리가 붙는다. 서버는 이 고리를 끊지 못한다 — 사람이 그 머신을 올린다(§9 남는 위험) |
| M5t-a 빌드의 `rcm doctor` · `rcm worker --check` 가 `pair_secret` 있는 설정을 읽음 | `unknown key(s): pair_secret` 로 거절(F20). doctor 는 사용법 오류 2 |

## 5. 작업 분할과 병렬

PR 하나는 브랜치 하나, 워크트리 하나다. 브랜치 이름은 `branch` 스킬로 확정하며 아래 이름은 제안이다. `shared` 열의
파일은 덧붙이기만 한다.

PR0 은 이 명세 PR 자신이다(브랜치 `docs/m5tc-pairing-identity-workplan`). PLAN.md 와 M5n §13 의 편집은 마지막 격리
라운드가 끝나고 머지하기 직전의 마지막 커밋으로 같은 PR 에 넣는다. 집 관례가 그렇다. `docs/m5t-workplan.md:690` 의 PR0
행 「이 명세와 PLAN.md 반영」과 `docs/m5tb-workplan.md` §5 의 PR0 행이 같은 방식이다(V1-9). PR0 이 고칠 줄은 이렇다.

| # | 파일:줄 | 지금 | 바꿀 것 |
|---|---|---|---|
| E1 | `PLAN.md:1` · 머리 판 줄(`:14` 다음) | 「(v2.13, 2026-11-25)」 | 다음 판 번호와 날짜, 머리 인용에 M5t-c 한 줄(명세 · 결정 280~319 · DB v27). M5t-b 명세 PR(#270)도 같은 줄을 고치므로 나중에 머지하는 쪽이 판 번호를 하나 더 올려 맞춘다 |
| E2 | `PLAN.md:243` | 「**쓰기(`POST /jobs`·업로드·취소)는 인증 필수.** `none` 은 없다.」 | 그 문장 뒤에 예외 문장: 「M5t-c 가 더한 익명 쓰기는 둘이다(결정 297, 2026-11-26): `POST /pair/hello` 는 상태를 바꾸지 않는 서버 신원 증명이고, `POST /pair/exchange` 는 사람이 `rcm pair` 로 연 짝짓기 창의 한 번 쓰는 코드(증명)가 인증을 대신한다. 익명 쓰기 전체는 `docs/m5tc-workplan.md` §4.7 「익명 쓰기」 표에 있다(`read_auth = none` 의 `POST /api/eta` · `POST /shelf/<n>/restore` 포함).」 |
| E3 | `PLAN.md:629` | 「**M5t-c — 짝짓기 · 서버 신원 보안**(명세 예정)」 | 「(계획 2026-11-26, 명세 `docs/m5tc-workplan.md` · 결정 280~319 · DB v27)」과 한 줄 요약 |
| E4 | `PLAN.md:753` | 「… 244 · 245 는 M5t-c 명세에 실을 예정이다. 249 는 두 명세에 나눠 실을 예정이다.」 | 「280~319 는 M5t-c 명세 `docs/m5tc-workplan.md` §11 에 있다. 244(짝짓기 창과 서버 신원)는 280 · 281 · 290 · 291 이, 245 의 풀 묶기는 284 · 294 가, 249 의 M5t-c 부분(신원 · 창 · `tokens.pool`)은 293 이 대신한다. 옛 번호는 다시 쓰지 않는다.」(M5t-b 부분은 M5t-b PR0 이 같은 줄에 적는다 — 나중 머지 쪽이 맞춤) |
| E5 | `docs/m5n-workplan.md:650` | 「토큰 자동 발급 · 자동 가입. 토큰은 사람이 옮긴다(보안 결정 유지).」 | 줄 끝에 「— 2026-11-26 M5t-c 결정 280 으로 개정: 사람이 `rcm pair` 로 연 짧은 수명 · 한 번 쓰는 짝짓기 창 안에서만 발급 · 가입한다(`docs/m5tc-workplan.md`).」 |

| PR | 브랜치(제안) | 내용 | 시작 조건 | 물결 | 함께 도는 PR | owns | shared | 머지 순서 | lane(추천 · 이유) |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5tc-pairing-identity-workplan`(이 명세 PR) | 이 명세 + E1~E5(마지막 커밋) | — | 0 | M5t-b 명세 PR(#270) | `docs/m5tc-workplan.md`, `docs/m5n-workplan.md`(`:650` 한 줄) | `PLAN.md`(E1 · E4 는 M5t-b 명세 PR 과 같은 줄) | 먼저. 나중 머지 쪽이 E1 · E4 를 다시 맞춤 | — (명세 PR) |
| PR1 | `feat/server-pairing-window-and-hello` | DB v27 · `TokenInfo` 칸 · `pair.key` 수명 주기(`PairKeyError`, ⑦ 하드 링크 길) · 짝짓기 창(메모리, `closed_at` 보존) · `/pair/hello`(`dialed` 규칙) · `/pair/exchange`(301, scrypt 코드 키, 형식 400, `host_name` 64, 409 `name`) · `/pair` 관리 라우트(창 `id`) · 순수 `core/pairing.py`(`pair_code_key` 포함) · I/O `pairing.py`(믹스인, `check_register_proof` — 313 순서 · V2-6 넣는 때) · 교환 로그 줄 · PLAN 「서버 API」 행 · 「저장소」 tokens 칸 · 문서 잠금(PLAN.md:243 예외 문장과 §4.7 익명 쓰기 표) | PR0 머지 · M5t-a 전체(v0.11.0) 머지 · M5t-b PR1 머지 | 1 | — | `src/remote_ci_monitor/core/pairing.py`(새), `src/remote_ci_monitor/pairing.py`(새), `src/remote_ci_monitor/store.py`, `src/remote_ci_monitor/server.py`, `tests/test_docs_m5tc.py`(새), `tests/test_pairing_server.py`(새), `tests/test_pairing_wire.py`(새) | `PLAN.md`, `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md` | M5t-b PR2 가 같은 때 돌면 M5t-b PR2 먼저(`store.py` · `server.py`) | full · DB 판이 오르고 새 익명 라우트(보안 · 와이어) |
| PR2 | `feat/worker-token-bound-pool` | 등록에서 `check_register_proof` 를 F30 의 4 에서 부름(`reason` 셋, 313) · 풀 검사 · 다른 주소 재등록 로그 · `/worker/spec` 풀 거르기(`spec_presets_for_pool`) · PI15 · PI23 의 단위(HTTP 호출) | PR1 머지 | 2 | PR3 | `src/remote_ci_monitor/remote_workers.py`, `src/remote_ci_monitor/capabilities.py`, `tests/test_pairing_pool.py`(새) | `src/remote_ci_monitor/server.py`(`worker_spec_for` 한 곳), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `tests/test_docs_m5tc.py` | PR2 먼저 | full · 워커 프로토콜(와이어)과 등록 · 펜싱(동시성) |
| PR3 | `feat/client-verify-server-before-token` | `pair_secret` 설정 키 · `_check_private` · 관문(F19 여섯 자리) · 리디렉트 안 따름(309) · 판정 표 J1~J12 와 워커 상태 전이(물러서기 · 경고 되풀이 · J6 의 새 hello · J7 셈 · 끝내는 중) · 상태 파일 쓰기와 읽기(`pair_state.py`, 314) · 클라이언트 표(V1-4) · auto 표 A1~A10(315 — `rcm worker` · `--check` · `rcm doctor`) · 다시 찾기 검증(310) · 등록 증명 보내기 · `_follow_first` · `_worker_follow_start` 순서 · `rcm worker --check` · `rcm check` · `rcm doctor` 의 행 · `rcm init worker` 출력 · 템플릿 · 예시(316) · 워커 로그 · 상태 파일의 비밀 0 · M5t-a 시험 고쳐 쓰기(F47) | PR1 머지 · M5t-b PR2 · PR3 머지(V1-11 · V1-10) | 2 | PR2 | `src/remote_ci_monitor/config.py`, `src/remote_ci_monitor/client.py`, `src/remote_ci_monitor/remote_worker.py`, `src/remote_ci_monitor/follow.py`(`_headers` 관문 · 오프너), `src/remote_ci_monitor/pair_state.py`(새), `src/remote_ci_monitor/doctor.py`, `src/remote_ci_monitor/core/doctor.py`, `src/remote_ci_monitor/templates/worker.toml`, `examples/worker.toml`, `tests/test_pairing_client.py`(새), `tests/test_pairing_worker_e2e.py`(새), `tests/test_pair_state.py`(새), `tests/test_doctor_cli.py`, `tests/test_worker_discovery.py`, `tests/test_cli_init_worker.py`, `tests/test_cli_worker_m5q.py`, `tests/test_cli_discover.py`, `tests/test_follow_cli.py` | `src/remote_ci_monitor/cli.py`(`_resolve_server` · `_follow_first` · `_worker_config` · `_pick_worker_server` · `_confirm_found_server` · `_worker_follow_start` · `_worker_check` · `cmd_check` · `cmd_init_worker` · `_wait` · `cmd_top` · `_client_fail`), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `tests/test_docs_m5tc.py` | PR2 뒤, dev 를 합친 뒤 | full · 보안(토큰이 어디로 가나) · 재시작 · 재연결 경로 · 상태 파일 |
| PR4 | `feat/cli-pair-command-and-setup-pairing` | `rcm pair`(열기 · 기다리기 · 창 id · 취소 · 0/1/2/3/130 · 관문 · 평문 경고) · `rcm setup --role worker --pair` · `--role client --pair`(코드 형식 검사 · 증명 교환 · 응답 검사 · 교환 오류 표 · `host_name` 출처 · 0600 원자 쓰기 · 주소 확인) · 코디네이터 setup 이 찍는 명령과 끝줄(TR27 · TR13 개정) · `rcm token list` 두 열 · `NO_FOLLOW_COMMANDS` 에 `pair` · 처음 설정 흐름 문서(`docs/configuration.md` 「Second build machine」 · 「Finding the server」, README) | PR3 머지(`cli.py`) — 그러므로 M5t-b PR3 뒤 | 3 | — | `src/remote_ci_monitor/cli.py`, `tests/test_cli_pair.py`(새), `tests/test_setup_pair.py`(새) | `src/remote_ci_monitor/follow.py`(`NO_FOLLOW_COMMANDS` 한 줄), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `README.md`, `tests/test_docs_m5tc.py` | — | full · 코드 · 토큰을 받고 찍는 CLI(보안) |
| PR5 | `docs/pairing-and-server-identity-guide` | `docs/operating.md`(`pair.key` · 기동 거절 · 신원 재설정 · `data_dir` 초기화 뒤 다시 짝짓기 · Docker 와 프록시의 `public_url` · J6 · J7 뒤 서비스가 되살리지 않는다는 것 · 상태 파일과 `rcm doctor` 로 신원 정지를 보는 법 · 신원 정지 중 취소(318) · `:492-494` 의 auto 문단) · `docs/usage.ko.md`(`:553-561` 처음 설정) · README 「워커 붙이기」 · 클라이언트 예시 주석 · CHANGELOG 정리 | PR4 머지 | 4 | — | `docs/operating.md`, `docs/usage.ko.md`, `examples/client.toml` | `README.md`, `CHANGELOG.md`, `docs/configuration.md`, `tests/test_docs_m5tc.py`, `tests/test_examples.py` | — | short · `src/` 를 바꾸지 않는다 |
| PR6 | `test/m5tc-live-pairing-acceptance` | 실기 하네스 `scripts/acceptance/m5tc_live.sh` · PI31 · PI32 · 보고서 `docs/acceptance/reports/<날짜>-m5tc-acceptance.md` | PR5 머지 | 5 | — | `scripts/acceptance/m5tc_live.sh`(새), `docs/acceptance/reports/`(새 파일) | — | — | short · 실기 그 자체가 증명이고 `src/` 를 바꾸지 않는다 |
| 릴리스 | — | 버전은 M5t-b 와 함께 메인 세션이 정한다 → dev → main(§8) → 태그. §6 17 이 끝나기 전에는 운영에 반영하지 않는다 | PR6 | — | — | — | — | — | — |

같은 물결 안에서 같은 `src/` 파일을 고치는 PR 은 없다. 물결 2 의 PR2 는 `remote_workers.py` · `capabilities.py` 와
`server.py` 한 곳을 고친다. PR3 은 `config.py` · `client.py` · `remote_worker.py` · `follow.py` · `pair_state.py` ·
`doctor.py` · `core/doctor.py` · `cli.py` 를 고친다. 둘은 겹치지 않는다. PR2 의 변이가 PR1 의 `pairing.py` 코드를 바꾸는
것은 파일을 고치는 일이 아니다(변이 표). 묶음 경로가 겹치지 않는지는 `tools/dev_batch.py` 로 확인한다.

M5t-b 와 겹치는 파일도 있다. PR1 은 M5t-b PR2 와 `store.py` · `server.py` 가 겹치지만 고치는 함수가 다르고 M5t-b PR2 를 먼저
머지한다. PR3 은 M5t-b PR2 · PR3 이 모두 머지된 뒤에 시작한다. 그래서 `client.py` · `remote_worker.py` 와 `cli.py` 의
`_wait` · `cmd_top` 이 겹치지 않는다(V1-10). PR4 는 PR3 뒤다.

PR3 머지와 PR4 머지 사이의 dev 에서는 짝짓지 않은 `auto` 가 거절되는데 짝짓기 명령은 아직 없다. 릴리스는 PR6 뒤라 이 상태가
사용자에게 닿지 않는다. 그 사이에도 A1 문장과 `rcm init worker` 의 둘째 줄이 명시 URL 길을 안내한다.

M5t-a 와는 전체 머지를 시작 조건으로 두었으므로 겹치지 않는다. 다만 PR3 은 M5t-a 의 시험을 고쳐 쓴다(F47, R7 · R8).

## 6. 진행 체크리스트

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 명세 작성(1판 · 2판 · 3판) · 시나리오 추적 | PR0 | — | [ ] |
| 1 | 격리 반박 라운드(HIGH 없을 때까지) · §12 반영. 검증자에게 명세 브랜치 head 와 dev 핀 D 의 스냅숏을 함께 준다 | PR0 | 0 | [ ] |
| 2 | 결정 페이지(위임): 1~6차 답 반영(6차가 319 와 314 · 315 · 317 의 세부를 확정, 메인 세션 + Codex) | — | 1 | [ ] |
| 3 | M5t-b 명세 맞추기(Q-E · Q-E′): M5t-c 의 종료 0 범위(J6 · J7), register 순서 4 자리 | — | 2 | [ ] |
| 4 | PR0 마지막 커밋: PLAN.md · M5n §13 편집 E1~E5 | PR0 | 1 · 2 | [ ] |
| 5 | PR0 머지 | PR0 | 4 | [ ] |
| 6 | M5t-a 전체(PR1~PR7, v0.11.0) 머지 확인 | — | — | [ ] |
| 7 | M5t-b PR1(DB v26 · 세션 · 등록) 머지 확인 | — | — | [ ] |
| 8 | PR1 서버 신원 · 짝짓기 창 · DB v27 | PR1 | 5 · 6 · 7 | [ ] |
| 9 | PR2 증명 · 풀 검사 · spec 거르기 | PR2 | 8 | [ ] |
| 10 | M5t-b PR2 · PR3 머지 확인 | — | — | [ ] |
| 11 | PR3 판정 표 · 상태 파일 · auto 표 · 처음 설정 틀 · 클라이언트 종료 | PR3 | 8 · 10 (머지는 9 뒤) | [ ] |
| 12 | PR4 `rcm pair` · `setup --pair` · `token list` · 처음 설정 문서 | PR4 | 11 | [ ] |
| 13 | mutcheck 변이(§7)가 PR 마다 들어갔는지 | PR1~PR4 | — | [ ] |
| 14 | PR5 문서 | PR5 | 12 | [ ] |
| 15 | PR6 실기 수용 · 보고서 | PR6 | 14 | [ ] |
| 16 | CHANGELOG `[Unreleased]` 정리(F49 의 #268 항목과 315 를 맞춤) | PR5 · 릴리스 | — | [ ] |
| 17 | 운영 설정 확인(Q-B · M3): 다른 머신의 워커 · 클라이언트 설정에서 짝짓지 않은 `auto` · 토큰 있는 `auto` — 옛 `rcm init worker` 기본이 `auto` 였으므로(F50) 있을 가능성이 높다. 있으면 반영 전에 다시 짝짓거나 명시 URL. M5t-b 만 아는 옛 빌드 워커가 새로 짝지은 토큰을 들고 5~10초마다 다시 뜨는 루프(상태 파일 없음, §9 남는 위험, 6차 위임 결정)도 찾아서 그 머신을 올리거나 다시 짝짓는다. **이것이 끝나기 전에는 운영 반영을 하지 않는다** | — | 15 | [ ] |
| 18 | 릴리스 PR dev→main 개인정보 점검(§8) · 태그 | 릴리스 | 15 · 16 | [ ] |
| 19 | 운영 반영(현행 규칙, 큐가 빌 때 재시작). 반영 전 운영 서버에 `pair.key` 가 생기는 것과 그 백업 자리 확인 | 운영 반영 | 17 · 18 | [ ] |

## 7. 시험 계획

시나리오 표는 설계를 보기 전에 따로 썼다. 1 · 2라운드 지적에 맞춰 행을 고치고 더했다. 바뀐 행은 §12 에 있다. 판정은 §4.3 의
J 번호로만 적는다.

<!-- scenario-prefix: PI -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| PI1 | 〔LAN 위장 · 불가역〕 짝지은 워커(`pair_secret` 있음). (a) 기동할 때 설정 URL 의 주인이 위장 서버다 (b) `server = "auto"` 인 짝지은 워커가 도는 중에 연속 연결 실패 5번 뒤 mDNS 로 다시 찾는데 위장 서버만 답한다. 위장 서버는 TXT 를 베꼈고 hello 에 형식은 맞지만 엉터리인 `mac` 을 주며 `/worker/spec` 의 `probes[].argv` 와 claim argv 에 표식 파일을 만드는 명령을 싣는다. (a) 다음에 위장 서버를 내리고 같은 주소에 진짜 서버를 띄운다 | 위장 서버가 받은 요청은 `POST /pair/hello` 뿐이다. `Authorization` 헤더 0건, `/worker/spec` · register · claim · health 0건, 표식 파일 없음. (a) 워커는 종료하지 않는다(J2). hello 를 5 → 60초로 되풀이하고 J2 문장을 처음에 한 줄, 그 뒤 같은 주소에 10분마다 한 줄 낸다(가짜 시계). 상태 파일의 `server_identity` 는 `failing` 이고 `rcm worker --check` 의 `server identity` 행은 FAIL 이다. 진짜 서버가 그 주소에 뜨면 사람 손 없이 J1 을 거쳐 등록하고 claim 한다(영구 정지 없음, 317). (b) 옮기지 않고(`server moved` 줄 없음) 새 claim 없이 다시 시도한다. 로그와 상태 파일에 토큰 · `pair_secret` 값은 없다 | e2e · 가짜 mDNS 응답기 + 요청 기록 서버(루프백) + 가짜 시계, 종료 여부 · stderr · 상태 파일 · 표식 파일 | PR3 |
| PI2 | 〔LAN 위장 · 따라가기〕 같은 위장 서버의 `/api/health` 가 더 높은 `version` 과 표식을 만드는 wheel 의 `client_wheel`(sha256 일치)을 싣는다. 짝지은 워커가 따라가기를 켠 채 기동하고, 짝지은 클라이언트가 같은 서버로 `rcm jobs` 를 친다 | hello 가 J2 이므로 워커 기동 ①의 health 도 `/client/<wheel>` 받기도 없다. 주입한 가짜 `execve` 호출 0, `$XDG_CACHE_HOME/rcm/clients` 에 새 폴더 0. 워커는 종료하지 않고 hello 만 되풀이한다(PI1 a 와 같다). 클라이언트도 health · wheel 요청 0 이고 `rcm jobs` 는 아무것도 보내기 전이라 종료 2 다 | e2e · 요청 기록 서버 + 가짜 `execve`, 캐시 폴더 목록 | PR3 |
| PI3 | 〔옛 토큰 · auto〕 (a) `pair_secret` 없는 워커 설정의 `server` 가 `auto` 또는 빈 값이다 (b) `pair_secret` 없는 클라이언트가 토큰을 가진 채 `server = "auto"` 다 (c) 옛 워커가 명시 URL 이고 서버 주소가 바뀌었다 | (a) `rcm worker` · `rcm worker --check` 가 mDNS 질의를 하나도 보내기 전에 종료 2 로 끝나고 다시 짝짓는 법과 명시 URL 을 쓰는 법을 말한다. (b) 토큰을 보내는 명령이 같은 이유로 종료 2 이고 위장 서버의 요청 기록이 비어 있다. (c) 오늘처럼 그 URL 로만 다시 시도하고 다른 주소로 옮기지 않는다 | e2e · 요청 기록 서버 + 가짜 mDNS, 종료 코드 · 질의 수 | PR3 |
| PI4 | 〔DHCP · 다시 찾기〕 (a) `server = "auto"` 인 짝지은 워커의 진짜 서버가 새 주소로 옮겼다 (b) 옛 주소를 다른 장치(위장)가 받았고 워커는 연결이 끊겼다 돌아와 같은 URL 로 첫 요청을 보낸다 (c) 다시 찾기 후보 가운데 둘이 hello 를 통과한다(같은 `pair.key` 를 가진 복제 서버) · 후보가 9개다 | (a) 연속 연결 실패 5번 뒤 후보마다 hello 를 하고 통과한 새 주소로만 옮긴다(로그 `server moved`). 등록은 새 챌린지 증명으로 되고 claim 이 이어진다. (b) 돌아온 뒤 첫 요청이 hello 이고 위장 장치가 받은 `Authorization` 은 0건이다. hello 를 「mDNS 로 찾았을 때만」 거는 구현은 (b) 에서 실패한다. (c) 둘이 통과하면 옮기지 않고 「복제된 data_dir 은 지원하지 않는다」 로그. 9개면 앞의 8개에만 hello 한다(요청 기록) | e2e · 기록 서버 + 연결 끊김 주입 + 가짜 mDNS, 워커 로그 | PR3 |
| PI5 | 〔LAN 중계 · 재생 — 서버 쪽〕 (a) 위장 서버(다른 IP)가 hello 본문을 그대로 진짜 서버에 중계한다(`dialed` = 위장 서버 주소) (b) 본문의 `dialed` 를 진짜 서버 주소로 고쳐 중계한다 (c) 엿들은 hello 응답을 다음 접속에 재생한다 | (a) 진짜 서버가 421 `host_not_this_server` 로 거절하고 본문에 `mac` · 챌린지가 없다. (b) 응답 `mac` 은 진짜 서버 주소로 계산되어 `verify_hello` 를 머신이 접속한 `dialed` 로 부르면 거짓이다. (c) nonce 생성기를 1000번 부른 값이 모두 다르고, 옛 응답은 새 nonce 로 `verify_hello` 가 거짓이다. hello 처리 중 Store 호출 0(spy). (워커가 토큰을 보내지 않는 것은 PI1 — PR3) | 단위 · 서버 hello 라우트 · `core/pairing.py` `verify_hello`, e2e · 루프백 중계기(서버만) | PR1 |
| PI6 | 〔LAN 중계 · 이름 URL〕 워커 URL 이 이름(`http://<광고 이름>.local:<port>`)이다. 위장 서버가 그 이름을 가로채고 hello 를 진짜 서버 IP 로 본문 그대로 중계한다 | 중계가 통과한다(결정 282 가 받아들인 남는 위험). 시험은 이 중계를 실제로 돌려 통과함을 고정한다. §9 남는 위험 표에 이 줄이 있고, `rcm pair` 가 이름 URL 을 찍을 때와 `rcm setup --pair` 가 이름 URL 을 받을 때 stderr 에 이름 URL 경고가 나온다 | e2e · 중계기 + 이름 해석 주입, stderr | PR4 |
| PI7 | 〔권한 상승 · 불가역〕 worker 창의 코드로 증명을 만들되 본문 `kind` 를 `client` · `admin` · 모르는 값 · 없음으로 바꿔 교환한다. `POST /pair` 에 `kind: "admin"` 을 보낸다 | 넷 모두 같은 404 이고 `tokens` 행이 늘지 않는다(`kind` 가 증명에 들어가므로 바꾸면 증명이 틀린다). `POST /pair` 의 `admin` 은 400 이다. worker 창으로 받은 토큰으로 클라이언트 API 는 403 `worker tokens cannot use the client API` 다 | 단위 · 교환 응답 바이트 + 읽기 전용 DB 사본의 `tokens` 행 수 | PR1 |
| PI8 | 〔동시 · 응답 유실〕 (a) 같은 유효 증명으로 교환 16개를 동시에 보낸다 (b) 서버가 토큰 행을 쓴 직후 응답이 닿기 전에 연결이 끊긴다 | (a) 200 은 정확히 1건, `tokens` 행 +1, 나머지는 같은 404. (b) 창은 쓰였고 워커는 토큰이 없다. `rcm token list` 에 그 이름이 `paired from` 과 함께 보이고 폐기하면 그 토큰은 401 이다. `rcm setup --pair` 는 「요청을 보낸 뒤 답이 없다 — 토큰이 발급됐을 수 있으니 코디네이터의 `rcm token list` 에서 찾아 폐기하고 새 창」을 말하고 종료 1 이며 `worker.toml` 이 없다(이름을 모르므로 이름을 지어 말하지 않는다) | 단위 · 스레드 16개 + DB 사본, e2e · 응답 직전 연결 끊기 | PR1 · PR4 |
| PI9 | 〔온라인 무차별 대입 · 방해〕 열린 창에 형식은 맞지만 틀린 증명을 보낸다. 한 출처에서 5번, 다섯 출처에서 한 번씩, 그 뒤 맞는 증명 | 다섯 번째 실패에서 창이 닫히고(출처와 무관하게 창에 센다) 그 뒤 맞는 증명도 같은 404 다. 기다리던 `rcm pair` 는 「틀린 코드 5번으로 창이 닫혔다 — 새 창을 연다」를 찍고 종료 1, 서버 로그에 같은 이유 한 줄. 증명 비교가 `hmac.compare_digest` 를 부른다(spy). 시험이 5 ÷ 32^10 을 계산해 명세 숫자(≈ 4.4e-15)와 대조한다 | 단위 · 교환 응답 · spy, e2e · `rcm pair` stdout · 종료 코드 | PR1 · PR4 |
| PI10 | 〔정찰〕 익명으로 틀린 증명 · 닫힌 창 · 연 적 없는 창 · 만료된 창 · 이미 쓴 코드 · 다른 kind 로 교환한다. PI40 의 형식이 틀린 본문을 창이 열렸을 때와 닫혔을 때 보낸다. 창이 열린 동안과 닫힌 동안 `/api/status` · `/events` · `/api/machines` · `/api/health` · mDNS 응답을 읽는다 | 여섯 경우의 상태 코드 · 헤더 이름 · 본문 바이트가 같은 404 다. 형식이 틀린 본문은 창이 열렸든 닫혔든 같은 400 `bad_pair_request` 바이트다(창 상태와 무관 — 오라클이 아니다). 같은 시각 · 같은 표본을 주입하면 읽기 라우트 응답과 `Responder.response()` 바이트가 창이 열렸을 때와 닫혔을 때 같다. TXT 키는 `v` · `name` · `lanes` 셋뿐이다 | 단위 · 응답 바이트 비교 | PR1 |
| PI11 | 〔열어 둔 창 · 시계 · 서버를 잃음〕 창을 열고 둔다. TTL 이 지난다. 서버를 재시작한다. 서버를 멈추고 만료 + 30초가 지나도록 띄우지 않는다. 코디네이터가 reload 한다. `--ttl 61`. 기다리는 `rcm pair` 에 Ctrl-C(서버가 살아 있을 때와 죽었을 때). 워커 시계가 1시간 어긋나 있다. `--ttl 60` 창이 59분에 쓰이거나 끝까지 두어 만료된 뒤 30분이 더 지난다(주입 시계) | 만료는 서버 시계로만 판정한다(TTL 안 성공 · 뒤 404). 재시작하면 창이 사라져 교환이 404 이고, 기다리던 `rcm pair` 는 다시 닿았을 때 자기 `id` 가 없음을 보고 종료 3 이다(창이 쓰이지 않았다는 증명이 없으면 1 로 보고하지 않는다, 312). 서버가 만료 + 30초까지 돌아오지 않으면 종료 3. Ctrl-C 는 `POST /pair/cancel` 을 보내고 130(서버가 죽었으면 「취소하지 못했다 — <시각>에 닫힌다」 줄과 130). reload 뒤 `GET /pair` 의 `expires_at` 이 같다. `--ttl 61` 은 `rcm pair` 종료 2(서버는 400). `rcm pair --cancel --worker` 로 바로 닫힌다. 60분 창은 닫힌 뒤에도 `GET /pair` 에 `state` · `closed_at` 과 함께 1시간 남아, 기다리던 `rcm pair` 가 쓰였으면 0, 만료면 1 로 끝나고 「the server restarted」 를 내지 않는다(V2-10) | 단위 · 주입 시계(PR1 목록 보존 · PR4), e2e · 재시작 · 정지 · SIGINT | PR1 · PR4 |
| PI12 | 〔창 둘 · 이름〕 (a) 같은 kind 창을 연 채 하나 더 연다 (b) worker 창과 client 창을 함께 연다 (c) 이미 있는 이름(폐기된 이름 포함)으로 연다. `--name` 없이 `host_name` 이 `Dev's MacBook Pro` · 64자 · 한글뿐 · 제어문자와 줄바꿈 포함이다 | (a) 두 번째 `rcm pair` 는 409 로 종료 1 이고 남은 시간과 `rcm pair --cancel` 을 말한다. 첫 창의 코드는 그대로 유효하다. (b) 둘 다 열리고 한 kind 의 코드는 다른 kind 창에 404 다. (c) `--name` 의 이름이 있으면 창을 열 때 409 `name_taken`, `host_name` 에서 나온 이름이 있으면 교환이 409 `name_taken`(본문에 `name`)이고 창은 열린 채다. 이름은 `Dev-s-MacBook-Pro` · 64자 · `worker-<hex6>` 처럼 `_NAME_RE` 에 맞게 바뀐다. 서버 로그 · `GET /pair` · `rcm pair` 완료 줄 · `rcm token list` 에 제어문자 · 줄바꿈이 0개다(정리한 값만) | 단위 · `clean_pair_label`, e2e · `rcm pair` 종료 코드 · 로그 줄 수 · `rcm token list` | PR1 · PR4 |
| PI13 | 〔틀린 풀〕 풀이 묶인 워커가 (a) 등록 본문의 `pool` 을 다른 풀로 보낸다 (b) worker.toml 의 `pool` 을 고치고 다시 시작한다 (c) 묶인 풀로 등록한다 | (a) (b) 등록이 409 `pool_mismatch` 와 `bound_pool` 이고 세션 행 · 펜싱 · `workers` 행 · 그 이름의 도는 잡이 바뀌지 않는다. 워커는 묶인 풀과 고치는 법을 로그에 적고 종료 2. (c) 지금처럼 등록된다 | 단위 · `POST /worker/register` + DB 사본, e2e · 워커 종료 코드 | PR2 · PR3 |
| PI14 | 〔다른 풀 비밀〕 풀 X 에 묶인 worker 토큰과 기본 풀 worker 토큰이 각각 `GET /worker/spec` 을 읽는다. 기본 풀 프리셋의 `env` 에 값이 있다 | X 토큰의 `presets` 에는 `pool`/`pools` 에 X 가 있는 프리셋과 `any-capable` 프리셋만 있고, 기본 풀 프리셋의 이름 · `env` 값은 응답 전문에 없다(grep). `local_only` 프리셋은 어느 토큰에도 없다 | 단위 · `/worker/spec` JSON 전문 | PR2 |
| PI15 | 〔기본 풀 · 불가역〕 `--pool` 없이 연 창으로 짝지은 워커가 기본 풀에 들어온다. 서버에는 `local_only = true` 인 배포 흉내 프리셋의 대기 잡과 기본 풀의 평범한 게이트 잡이 있다 | 그 워커는 게이트 잡을 받고, `local_only` 잡에는 claim 이 204 이며 그 잡은 `queued` 로 남는다. 짝짓기가 결정 254 의 후보 함수를 우회하는 길이 claim · spec 어디에도 없다 | 단위 · HTTP 로 교환 · 증명 실은 등록 · claim(PR2), e2e · 짝지은 워커 프로세스 + 시험 서버 설정의 가짜 `sh -c` 프리셋 둘, 잡 상태(PR3) | PR2 · PR3 |
| PI16 | 〔풀 NULL〕 v27 전에 만든 명시 토큰(`tokens.pool` NULL)으로 (a) 등록 · claim · finish 한다 (b) 등록한 뒤와 등록 전에 `/worker/spec` 을 읽는다 (c) `rcm token list` 를 본다 (d) `verify_token` 이 돌려준 `TokenInfo` 를 본다 | (a) 마이그레이션 직후에도 오늘과 같다. (b) 등록한 뒤에는 그 워커가 등록한 풀로, 등록 전에는 질의 `pool`(없으면 `default`)로 거른다. 거르지 않은 전체가 나오는 길은 없다. (c) `pool` 열이 「—」다. (d) `pool` · `pair_id` 가 None 이고 짝지은 토큰이면 DB 값과 같다 | 단위 · 마이그레이션한 DB 사본 + `/worker/spec` · `rcm token list` · `verify_token` | PR2 · PR4 |
| PI17 | 〔비밀 · 공개 레포〕 짝짓기의 모든 길(성공 · 틀린 증명 · 닫힌 창 · 판정 J1~J12 · `rcm pair` · `rcm setup --role worker --pair` · `--role client --pair` · `rcm doctor --json`)을 돈다 | 코드 · 코드 키 · 토큰 · `pair_secret` · `pair.key` · 세션 id 값이 stdout · stderr · 서버 로그 · 워커 로그 · 상태 파일 · `events` 표 · `/api/*` 응답 어디에도 0건이다(코드는 `rcm pair` 가 창을 열 때 stdout 에 한 번만). PR1: 서버 로그 · 응답 · `pair.key` 가 umask 가 느슨해도 만들 때부터 0600, 0644 `pair.key` 는 기동이 종료 2. PR3: 워커 로그 · 상태 파일(PI39) · `rcm doctor --json`, 0644 로 바꾼 `worker.toml` · `client.toml` 의 `pair_secret` 로드 거절. PR4: `rcm pair` · setup 출력, 코드는 프롬프트 · stdin 으로만 받고 `--code` 는 종료 2, 만든 `worker.toml` · `client.toml` 이 0600 | 단위 · 전체 grep · `stat` 모드 | PR1 · PR3 · PR4 |
| PI18 | 〔DB 사본〕 서버 DB 사본(마이그레이션 백업 · 읽기 전용 열기)으로 서버 신원을 만들려 한다 | 사본에는 `tokens.pair_id` 와 토큰 sha256 만 있고 `pair_secret` 도 `pair.key` 도 없다. 사본만 둔 `data_dir` 의 서버는 살아 있는 짝 토큰이 있어 기동을 거절한다(PI35). 사본의 짝 토큰을 폐기하고 새 `pair.key` 로 띄운 서버의 hello 는 짝지은 워커의 검증을 통과하지 못한다(`mismatch`) | 단위 · 읽기 전용 사본 · 파일 모드, e2e · 키를 바꾼 서버 | PR1 |
| PI19 | 〔폐기 · 다시 짝짓기〕 (a) `rcm token revoke <worker>` 를 도는 짝지은 워커에 건다 (b) `rcm pair --worker --name <그 이름> --replace` 로 다시 짝짓는다 (c) `pair.key` 를 재설정한 서버에 옛 짝지은 워커가 붙는다 | (a) 폐기한 순간부터 `/worker/*` 호출이 모두 401 이다. 워커는 401 뒤 바로 hello 를 새로 해 J1 을 본 다음(J6) 새 claim 을 멈추고, 도는 잡이 끝나면 J6 문장과 상태 파일 `exited` 를 남기고 종료 0. 보고하지 못한 잡은 M5t-b 의 유예(`disconnect_grace_seconds`) 뒤 `lost`. (b) 교환이 그 행을 한 UPDATE 문장으로 바꿔(새 sha256 · `pair_id`, `revoked_at` NULL) 옛 토큰은 401, 활성 토큰은 정확히 1개이고 둘 다 유효하거나 둘 다 무효인 순간이 없다. (c) hello 가 J2 라 토큰을 보내지 않고 다시 시도하며 상태 파일 `failing` · `rcm worker --check` FAIL 이고 J2 문장이 「pair.key 가 재설정됐으면 다시 짝지으라」를 말한다 — 종료하지 않는다(317, 2판의 종료 0 을 바꿈) | 단위 · 호출별 상태 + DB 사본(PR1 b), e2e(PR3 a · c, PR4 b) | PR1 · PR3 · PR4 |
| PI20 | 〔익명 라우트〕 §4.7 「익명 쓰기」 표의 탐침 경로마다 `Authorization` 없이 POST · PUT · DELETE 를 보낸다. `read_auth = none` 서버와 `basic` 서버로 한 번씩. 새 익명 라우트 둘에 1 KiB 를 넘는 본문 · 깨진 JSON · 동시 요청 상한 초과를 보낸다 | 401 · 403 · 405 가 아닌 답을 받은 (메서드, 라우트) 집합이 `none` 에서 정확히 `POST /api/eta` · `POST /shelf/<n>/restore` · `POST /pair/hello` · `POST /pair/exchange`, `basic` 에서 정확히 `POST /pair/hello` · `POST /pair/exchange` 다(§4.7 표 — V1-11). `/worker/*` · `POST /pair` · `POST /pair/cancel` 은 401 이다. PLAN.md:243 의 예외 문장(PR0)이 pair 둘을 적고 §4.7 표를 가리킨다(문서 잠금). 새 라우트는 413 · 400 · 503 으로 답하고 hello 는 Store 를 부르지 않는다 | 단위 · 탐침 목록 순회 + 문서 잠금, e2e · 동시 연결 | PR1 |
| PI21 | 〔엿보기 · TLS 없음〕 같은 망의 수동 관찰자가 (a) 짝짓기 교환(요청의 증명 · 응답의 토큰 · `pair_secret`) (b) 그 뒤의 `Authorization` 요청을 평문으로 본다. 캡처한 교환 요청을 정당한 교환 뒤에 다시 보낸다. (c) 정당한 요청을 붙잡아 둔 중계가 캡처한 같은 요청을 먼저 보낸다 (d) 코디네이터 밖 머신(URL 호스트 192.0.2.x)에서 `rcm pair` 를 친다 | `rcm setup --pair` 가 보낸 요청 바이트에 코드가 0번 나온다(입력한 꼴 · 대문자 · `-` 없는 꼴 모두). 정당한 교환 뒤에 다시 보낸 요청은 404 다(창은 쓰였다). (c) 먼저 닿은 재생이 200 을 받고 정당한 요청은 404 이며 setup 은 「the code was wrong or used」 종료 1, 코디네이터 완료 줄의 주소는 중계의 주소다 — 증명은 창이 닫힐 때까지 bearer 라는 T4 (c) 남는 위험을 고정한다. 엿본 토큰 · `pair_secret` 으로 사칭할 수 있다는 것과 엿본 증명으로 창이 열린 동안 오프라인 추측이 된다는 것(M7)은 결정 282 · 301 의 남는 위험이고 §9 표에 이름으로 있다. `rcm pair` 가 찍는 URL 의 호스트가 루프백도 100.64.0.0/10 도 아니면 stderr 에 평문 경고가 나오고 `rcm setup --pair` 도 같은 경고를 낸다. (d) `rcm pair` stderr 에 「the pairing code and the admin token cross this network in plain text」 | 단위 · 경고 함수, e2e · 캡처한 요청 바이트 · 재전송 · 중계 | PR4 |
| PI22 | 〔bind · 주소 · 스킴〕 서버 bind 가 (a) `127.0.0.1`(`public_url` 없음) (b) 특정 LAN IP (c) `0.0.0.0` (d) Tailscale 대역 IP + 이름 `public_url` (e) `0.0.0.0` + 광고 켬 (f) 루프백 bind + `public_url = https://<이름>`(TLS 프록시 뒤) 일 때 창을 열고 머신이 찍힌 URL 로 hello · 교환한다 | (a) `POST /pair` 가 409 `pair_unreachable` 이고 `rcm pair` 는 「다른 머신이 닿지 못한다 — bind 를 바꾸라」로 종료 1, 창은 열리지 않는다. (b)~(f) 찍힌 URL 마다 hello 가 `ok` 다. (f) 는 `dialed = https://<이름>:443` 이 `public_url` 과 맞는다(1판의 `:80` 판정이면 421). `0.0.0.0` 은 URL 에 나오지 않는다. 찍히지 않은 이름(`public_url` 없는 MagicDNS 이름 등)은 421 이고 머신 메시지가 `public_url` 로 고치는 법을 말한다 | 단위 · `own_dialed_set` 에 주입한 소켓 주소 · `public_url`(PR1), e2e · `rcm pair` 의 URL 목록 · 별칭 루프백 127.0.0.2 + 주입한 `public_url`(실제 tailnet 은 쓰지 않는다, PR4) | PR1 · PR4 |
| PI23 | 〔탈취 토큰 · 등록〕 (a) 로그 · `ps` 에서 샌 짝지은 worker 토큰만 가진 다른 머신이 그 이름으로 증명 없이 · 틀린 증명 · 이미 쓴 챌린지 · 120초 지난 챌린지 · 5초 넘게 미래인 챌린지 · 다른 `pair_id` 의 챌린지 · 다른 세션 id 로 만든 증명으로 등록한다 (b) 같은 토큰으로 등록 없이 claim · heartbeat · `/worker/spec` (c) 옛 명시 토큰(`pair_id` NULL)으로 다른 머신이 등록 (d) 클라이언트 API · `/pair` 관리 라우트 (e) 다른 워커의 잡 트리 · 로그 · finish (f) `worker.toml` 을 통째로 복사(토큰 + `pair_secret`)한 머신이 등록 (g) 엿본 hello 응답의 유효한 챌린지에 틀린 MAC 을 실어 정당한 워커보다 먼저 등록한다 | (a) 403 `pair_proof_required` 또는 `pair_proof_invalid` 이고 `reason` 이 경우마다 맞다: 틀린 증명 · 다른 `pair_id` 의 챌린지 · 다른 세션 id 는 `bad_mac`, 120초 지남 · 5초 넘게 미래는 `expired`, 쓴 챌린지는 `reused`(313 — `pair_mismatch` 는 없다). 세션 행 · 펜싱 · 능력 보고 · 잡이 그대로다 — 정당한 워커의 세션과 도는 잡은 `running` 그대로. (b) claim · heartbeat 은 409 `not_registered`(M5t-b), `/worker/spec` 은 묶인 풀만 준다. (c) 오늘처럼 등록되고 정당한 세션이 펜싱된다(옛 토큰은 증명이 없다). 앞 등록과 다른 주소면 서버 로그에 두 주소가 한 줄 남는다. (d) 403. (e) 오늘처럼 404 · 403 `not your job` · 409. (f) 등록이 성공하고 정당한 세션이 펜싱된다 — 결정 302 의 남는 위험으로 고정하는 시험이다. (g) 그 요청은 `bad_mac` 이고 챌린지는 쓴 집합에 들어가지 않아 정당한 워커가 같은 챌린지로 한 등록이 성공한다(V2-6) | 단위 · HTTP 호출 + DB 사본(PR2 — a~e · g), e2e · 워커 프로세스 둘(PR3 — f) | PR2 · PR3 |
| PI24 | 〔클라이언트〕 (a) `rcm pair --client` 창 + `rcm setup --role client --pair` (b) 받은 `client.toml` (c) 짝지은 클라이언트의 서버 URL 을 위장 서버가 차지했다(`rcm run` 과, 잡을 낸 뒤 다시 연결하는 `rcm wait`) (d) 토큰 없는 클라이언트가 `server = "auto"` 로 따라가기를 켠 채 위장 서버를 찾는다 | (a) kind 는 `client`(admin 아님), `rcm run` 이 되고 `/worker/*` 는 403, 풀은 NULL. (b) 0600 이고 `server` · `token` · `pair_secret` 이 있다. (c) 위장 서버가 받은 요청은 hello 뿐이고 `rcm run` 은 종료 2(트리 업로드 · wheel 실행 0), `rcm wait` 은 종료 3. (d) mDNS 찾기는 하지만 검증된 `pair_secret` 이 없으므로 위장 서버가 받은 `/client/<wheel>` 요청이 0건이고 주입한 가짜 `execve` 호출도 0이다(Q-A · 결정 300) | 단위 · 토큰 행 kind, e2e · 기록 서버 + 가짜 `execve` | PR3 · PR4 |
| PI25 | 〔코디네이터 화면〕 (a) 창을 열 때 (b) 워커가 붙었을 때 (c) 엉뚱한 머신이 붙었을 때 (d) `--pool` 에 오타를 썼을 때 | (a) 코드(이 화면에서만) · URL · 만료 시각 · kind · 풀 · 이름 · 그 풀로 갈 프리셋 수를 찍는다. (b) 완료 줄에 이름 · kind · 풀 · 정리한 `host_name` · 서버가 본 접속 주소 · 시각과 다음 할 일(`rcm machines`, `rcm token revoke <이름>`)이 있다. 워커 쪽 `rcm setup --pair` 는 붙었다 · 풀 · 받을 프리셋 수 · 결과는 코디네이터로 가고 요청자의 `rcm wait` 가 받는다를 찍는다. 코드 입력 뒤 두 번째 승인은 없다. (c) 그 줄의 `host_name` · 주소와 `rcm token list` 의 `paired from` 으로 알아보고 `rcm token revoke` 한 번으로 끝난다. (d) 갈 프리셋이 0개면 창을 열 때 stderr 에 「0 presets reach pool …」 경고를 내고 창은 연다 | e2e · `rcm pair` stdout · stderr · `rcm token list` | PR4 |
| PI26 | 〔짝짓기를 쓰지 않음 · 섞인 판〕 v27 서버에서 `rcm token add NAME --worker` → `RCM_WORKER_TOKEN` → `rcm worker --check` → `rcm worker`(명시 URL). 옛 워커 ↔ 새 서버, 옛 빌드의 `rcm worker --check`(질의 `pool` 없음, 기본 풀이 아닌 워커) ↔ 새 서버, 새 빌드의 옛 토큰 워커 ↔ 옛 서버, 짝지은 워커 ↔ 옛 서버(hello 404) | stdout 은 토큰 한 줄뿐이고 안내는 stderr, 종료 0. 등록 · claim · heartbeat · finish · 산출물 · 로그가 그대로다. 새 빌드 `rcm worker --check` 의 기존 행 이름 · 종료 코드가 같고 더해진 `server identity` 행은 `warn` 「not paired」다. 옛 `--check` 는 등록 전이면 `default` 풀 프리셋을, 등록 뒤면 등록 풀 프리셋을 받는다. 짝지은 워커는 hello 가 404 인 옛 서버에 토큰을 보내지 않고, 종료하지 않고 물러서며 다시 시도한다(304). 같은 주소의 경고는 속도를 제한한 간격으로 되풀이되고 `rcm worker --check` 에 서버 신원 미확인이 보인다 | e2e · 두 프로세스 · 종료 코드 · stdout 과 stderr 분리 · 요청 기록 | PR3 |
| PI27 | 〔같은 코드를 지나는 다른 길〕 `authenticate` · `verify_token` · `tokens` 표를 쓰는 길 전부: client 토큰 `rcm run · wait · cancel · jobs · top · machines`, admin 토큰 `rcm pause · resume · reload · gc · recheck`, `read_auth = basic` 의 Basic 읽기, `/api/whoami`, SSE `/events`, 보관함, 릴리스 드라이버 토큰 발급 · 폐기, `rcm token list`, `rcm discover`, `rcm check` | 길마다 상태 코드 · 종료 코드 · JSON 키가 변경 전과 같다(`TokenInfo` 칸이 늘어도). `rcm token list` 의 앞 네 열(name · kind · created · revoked)의 자리와 문구가 같고 `pool` · `paired from` 열은 뒤에 붙는다. `/api/whoami` · `rcm discover --json` 의 키가 그대로다. worker 토큰으로 읽기 라우트는 403, Basic 은 쓰기에서 거절된다 | 단위 · 길마다 한 호출 | PR1 · PR4 |
| PI28 | 〔마이그레이션 · 운영 DB〕 v25 · v26 사본(폐기된 토큰 · 등록된 워커 행 · 드라이버 토큰)을 새 빌드로 연다. v26 빌드로 v27 DB 를 연다. `rcm pair` 를 DB 없는 `data_dir` 의 서버 설정 + 서버 URL + admin 토큰으로 돌린다 | 마이그레이션 뒤 기존 토큰이 같은 이름 · kind · 폐기 상태로 검증되고 새 칸은 NULL, `workers` 행이 그대로다. 옛 빌드는 `newer_database_message` 로 거절하고 DB 를 바꾸지 않는다. `rcm pair` 는 DB 를 열지 않고 그 `data_dir` 에 파일이 생기지 않는다. 가드의 `DB_OPENING` 은 `token` 하나 그대로다 | 단위 · 사본의 `user_version` · 읽기 전용 열기 | PR1 · PR4 |
| PI29 | 〔문서 · 공개 레포〕 PLAN 「보안」 · 「서버 API」 · README · `docs/configuration.md` · `docs/operating.md` · 예시 · 시험 fixture | PLAN 「보안」 절의 익명 예외 문장이 PI20 의 집합과 같다(문서 잠금). README 발견 문장은 TXT 가 그대로라 그대로다. `docs/operating.md` 에 `pair.key` · 키가 없을 때의 기동 거절 · 신원 재설정(폐기 → 키 삭제 → 시작 → 다시 짝짓기) · `data_dir` 초기화 뒤 다시 짝짓기 · Docker 와 프록시의 `public_url` · 확실한 거절 뒤 서비스가 되살리지 않는다는 것이 있다. 예시 · fixture 에 실제 토큰 · 코드 · `pair_secret` · 사설 IP · 호스트 이름 · 64자 hex 리터럴이 0건이다(시험 값은 실행 중에 만들고 주소는 127.0.0.x · 192.0.2.x 만) | 단위 · 문서 잠금 · gitleaks | PR1 · PR5 |
| PI30 | 〔변이〕 §7 변이 목록의 변이를 하나씩 넣는다 | 변이마다 표에 적은 그 PR 의 시험이 빨개진다. 하나라도 초록이면 그 행의 시험이 약하다 | 변이 · `scripts/mutcheck.py` | PR1 · PR2 · PR3 · PR4 |
| PI31 | 〔실기 · 짝짓기 끝까지〕 시험 서버(자기 설정 · 포트 · `data_dir`, `advertise = false`)에서 `rcm pair --worker` → 다른 `HOME` · `data_dir` 의 일회용 워커가 `rcm setup --role worker --pair` → `rcm worker` → 기본 풀 가짜 프리셋 잡 → `rcm token revoke` | 워커가 붙어 잡을 끝내고 요청자의 `rcm wait` 가 0 으로 끝난다. 코디네이터와 워커의 완료 줄이 PI25 대로다. 폐기 뒤 워커 호출이 401 이고 워커는 종료 0 이다. 모든 로그와 교환 요청 캡처에 코드 · 토큰 · `pair_secret` 이 0건이다 | 실기 하네스 (live) | PR6 |
| PI32 | 〔실기 · 위장과 웹〕 같은 시험 서버에서 별칭 루프백 주소에 위장 서버를 띄워 짝지은 워커의 URL 을 그리로 돌린다. 그 뒤 위장 서버를 내리고 URL 을 진짜 서버로 돌려 둔다. 웹 UI 토큰 입력 · SSE 를 CDP 하니스로 본다 | 위장 서버 요청 기록에 hello 만 있고 워커는 종료하지 않고 J2 문장을 내며 hello 를 되풀이한다. `rcm worker --check` 와 `rcm doctor --role worker` 가 서버 신원을 FAIL 로 보인다. 위장 서버를 내리면 워커가 사람 손 없이 다시 붙는다. 웹의 토큰 입력 · 큐 갱신이 변경 전과 같고 CDP 오류 수집기가 비어 있다 | 실기 하네스 · CDP (live) | PR6 |
| PI33 | 〔wire 바이트 · 골든〕 4.2.1 의 인코딩 · 형식 · 주소 정규화 · 코드 키 · 교환 응답을 잠근다: `enc` 바이트 · `pair_mac` · `derive_pair_secret` · `pair_code_key` · 챌린지 만들기와 검증(120초 지남 · 5초 넘게 미래 · 한 글자 바꿈) · 형식 검사(대문자 hex 의 `mac` · 31자 `pair_id` · 63자 nonce) · `canonical_dialed` 표 · hello MAC 이 챌린지를 덮음 · client 창 응답의 null 칸 | `enc("rcm-hello-v1", "ab")` 가 4.2.1 의 22바이트와 같다(리터럴은 이 낮은 엔트로피 바이트뿐). `pair_mac` 결과가 시험 안의 독립 구현(`struct.pack(">I", …)` + `hmac.new`)과 같다. `pair_code_key("ABCDEFGHJK")` 가 시험 안에서 `hashlib.scrypt(…, salt=b"rcm-pair-code-v1", n=2**15, r=8, p=1, maxmem=64 MiB, dklen=32)` 로 다시 계산한 값과 같고 `SHA256(code)` 와 다르다. 챌린지는 경계 안에서만 참. 형식이 틀린 값은 모두 거짓이고 서버 hello 는 400 `bad_hello`. `canonical_dialed` 가 4.2.1 표의 행마다 그 결과다(`https` 443 · IPv4-mapped · zone id 포함). 응답의 챌린지를 다른 유효 챌린지로 바꾸면 `verify_hello` 가 거짓이다(V1-13). client 창 응답은 `pool` · `presets_count` 가 null 이고 MAC 에는 빈 문자열로 들어가며, 키가 빠지거나 정수 칸에 bool · 문자열이 오면 응답 검사가 거짓이다(V2-12) | 단위 · `core/pairing.py` 순수 함수(시계 · 난수는 인자) | PR1 |
| PI34 | 〔교환의 능동 사칭자 · 불가역〕 `rcm setup --role worker --pair` 에 넣은 URL 의 주인이 (a) 가짜 교환 서버: 형식은 맞지만 코드를 모르고 만든 `mac` 의 200 을 준다 (b) 응답의 `kind` 를 `admin` 으로 바꾼 200 을 준다 (c) 진짜 서버 | (a) (b) `worker.toml` 이 생기지 않고(파일 0) 종료 1 「answered but does not know this code — nothing was saved」. 가짜 서버가 받은 요청 바이트에 코드가 0번 나온다. (c) 진짜 서버는 증명이 맞으면 응답 `mac` 이 워커 검증을 통과하고, 형식은 맞는데 틀린 증명은 창의 실패 1 로 센다 | 단위 · 교환 증명 · 응답 MAC(PR1), e2e · 가짜 교환 서버 + 파일 목록(PR4) | PR1 · PR4 |
| PI35 | 〔`pair.key` 수명 주기 · 불가역〕 (a) `pair.key` 를 지웠는데 폐기되지 않은 짝 토큰이 있는 DB 로 `rcm serve` (b) 짝 토큰을 모두 폐기한 뒤 같은 `rcm serve` (c) 같은 `data_dir` 에서 키 만들기를 여러 스레드 · 프로세스가 동시에 하고, 쓰기 도중에 다른 쪽이 읽는다(쓰기 사이에 멈춤 주입) (d) `pair.key` 가 심볼릭 링크 · 0644 · 31바이트 (e) `os.link` 가 `PermissionError`(EPERM)를 내는 파일 시스템(주입) | (a) 종료 2 `cannot start server: … pair.key is missing but N paired tokens are still active — restore … or reset …` 이고 `pair.key` 가 생기지 않는다(조용히 새 키 0). (b) 새 키(32바이트 · 0600)를 만들고 뜬다. (c) 모두 같은 32바이트를 읽고, 0바이트나 일부만 쓴 `pair.key` 를 읽는 순간이 없으며 임시 파일이 남지 않는다. (d) 셋 모두 종료 2 이고 문장에 값이 없다. (e) 종료 2 와 「does not support hard links」 문장, 임시 파일 · `pair.key` 가 남지 않고 rename 으로 물러서지 않는다(`os.replace` · `os.rename` 호출 0, spy) | 단위 · `load_or_create_pair_key` + DB 사본 + 멈춤 주입, e2e · `rcm serve` 종료 코드 | PR1 |
| PI36 | 〔판정 표 · 종료 코드 · 취소〕 짝지은 워커가 기동 때와 도는 중에 hello · 토큰 요청에서 (a) J2 (b) J3 (c) 401 뒤 새 hello 가 J1(J6) (d) 401 뒤 새 hello 가 J2(J6′) (e) 등록 `bad_mac` 한 번 · 이어서 세 번(매번 J1 뒤) (f) 연결 실패 · 503(동시 처리 상한) · 500 · 429(J4) (g) 404 · 307 · 깨진 200 · `v` 2(J5) (h) 등록 `expired` · `reused`(J8)를 받는다. 도는 중에는 되돌릴 수 있는 잡 하나와 `irreversible` 잡 하나가 돌고, J2 가 이어지는 동안 요청자가 두 잡을 취소한다 | 기동: (a) (b) (d) (f) (g) 는 종료하지 않고 5 → 10 → 20 → 40 → 60초로 hello 를 되풀이하며 토큰 0 이다. 같은 (주소, 판정)의 경고는 처음 한 줄 뒤 10분마다 한 줄(가짜 시계)이고 상태 파일은 J 표의 값이다(`failing` · `unknown`). (c) J6 문장과 종료 0. (e) 한 번이면 5초 뒤 새 hello 로 다시 등록하고 등록되면 셈이 0, 세 번 이어지면 J7 문장 · 종료 0. (h) 새 hello 뒤 다시 등록하고 경고가 10분마다 되풀이되며 상태 `retrying`. 도는 중: (a) (b) (d) (f) (g) 는 새 claim 0 · 토큰 0, 종료하지 않는다. 그동안 취소는 워커에 닿지 않아 되돌릴 수 있는 잡은 끝까지 돌고(318 — 결정 253 상속), 서버는 유예 뒤 두 잡을 `lost` 로 닫으며, `irreversible` 잡은 M5t-b 대로 워커가 스스로 멈춘다. 다시 J1 이 되면 heartbeat 의 `closed_jobs` `had_cancel` 로 아직 도는 잡이 멈춘다. (c) (e 세 번)은 끝내는 중: 도는 잡을 끝내고 종료 0, 그동안 취소는 닿지 않고 끝내는 중 줄이 10분마다 되풀이된다. 확인된 뒤 heartbeat 이 503 을 받아도 다시 hello 하지 않고 heartbeat 이 이어진다(J10, V2-10). 프로토콜 불일치(M5t-b)는 여전히 종료 3 | e2e · 응답을 주입하는 기록 서버 + 가짜 시계, 종료 코드 · 로그 줄 수 · 요청 기록 · 상태 파일 · 잡 상태 | PR3 |
| PI37 | 〔리디렉트〕 짝지은 머신의 (a) hello (b) 토큰 요청(`Client._request` · `download_tree` · `download_bundle` · `events`)에 서버가 다른 호스트(루프백 다른 포트)로 307 · 302 를 준다. 같은 시험을 옛 토큰 머신으로 | (a) hello 는 리디렉트를 따르지 않고 `other` 로 분류된다. (b) 리디렉트 대상 호스트가 받은 요청 0건, 그쪽이 받은 `Authorization` 0건. 옛 토큰 머신은 오늘 동작 그대로(결과를 기록해 고정) | e2e · 루프백 서버 둘 · 요청 기록 | PR3 |
| PI38 | 〔코드 입력〕 `rcm setup --pair` 에 `abcde-fghjk`(소문자 · 대시) · `ABCDE FGHJK` · `ABCDE-FGH0K`(0) · `ABCDE-FGH1K` · 9자 · 11자를 넣는다. 서버에는 그 kind 의 창이 열려 있다 | 소문자 · 대시 · 공백은 정규화되어 교환이 된다. 0 · 1 · 8 · 9 가 든 코드와 길이가 틀린 코드는 요청을 보내지 않고(서버 요청 기록 0) 종료 2 와 「A–Z 와 2–7 의 10자 — 0 · 1 · 8 · 9 는 쓰지 않는다」 문장이고, 창의 실패 수가 그대로다(그 뒤 맞는 코드 5번째 시도까지 창이 열려 있다) | e2e · `rcm setup --pair` + 요청 기록 서버 · `GET /pair` | PR4 |
| PI39 | 〔상태 파일 · 314〕 짝지은 워커가 (a) 기동해 J1 뒤 등록된다 (b) J2 와 J4 를 번갈아 받는다 (c) J8 이 이어진다 (d) J7 로 끝난다 (e) SIGKILL 로 죽는다 (f) 살아 있는데 3분 넘게 파일을 새로 쓰지 않는다(멈춤 주입) (g) 파일이 0644 · 심볼릭 링크 · 깨진 JSON · 다른 `pair_id` 로 바뀐다 (h) 두 짝지은 설정이 한 `data_dir` 을 쓴다. 매번 `rcm worker --check` 와 `rcm doctor --role worker --json` 을 돌린다 | 파일은 `<data_dir>/pair-state/<pair_id>.json`, 0600(폴더 0700)이고 쓰기 도중을 읽는 순간이 없다(임시 파일 + `os.replace`). 파일 · 두 명령 출력에 토큰 · `pair_secret` · 세션 id · 코드 값이 0건이다(grep). (a) `verified` · `registered` → `worker state` ok, doctor 0. (b) `failing` 일 때 FAIL · doctor 1, `unknown` 일 때 warn · doctor 3, 경고가 10분마다 되풀이된다. (c) `retrying` → warn · doctor 3. (d) `exited.reason = proof_bad_mac` → FAIL · doctor 1(프로세스가 끝난 뒤에도). (e) `exited` 없이 프로세스가 없다 → warn 「not running」 · doctor 3. (f) 오래됨 → warn · doctor 3. (g) 읽지 않고 warn · doctor 3. (h) 각자 자기 파일을 쓰고 읽는다 | 단위 · `judge_pair_state` + 주입 시계, e2e · 워커 프로세스 + 기록 서버 | PR3 |
| PI40 | 〔익명 교환 입력 · V2-5〕 창이 열렸을 때와 닫혔을 때 익명 `POST /pair/exchange` 의 본문이 `host_name` 숫자 · null · 65 코드 포인트 · 홀로 선 서러게이트(`"\ud800"`) · 빈 문자열, `worker_nonce` 63자 · 대문자 hex, `proof` 숫자, `v` 가 `true`, 본문이 배열이다 | 빈 문자열만 형식이 맞아 정상 처리되고(이름은 `worker-<hex6>`) 나머지는 모두 같은 바이트의 400 `bad_pair_request` 다. 500 은 0건, 서버 로그에 traceback 이 없다. 창이 열렸든 닫혔든 같은 답이고 창의 실패 수가 그대로다 | 단위 · 응답 바이트 · 실패 수 | PR1 |
| PI41 | 〔auto 표 · 315〕 §4.3 `auto` 표의 A1~A10 설정마다 그 명령을 TTY 와 비TTY 로 돌린다. 가짜 mDNS 는 진짜 서버 하나 · 위장 서버 하나 · 둘 다를 답한다 | 행마다 mDNS 질의 수 · 사람에게 물은 수 · 요청 기록 · 종료 코드가 표와 같다: A1 · A3 은 질의 0 · 요청 0 · 종료 2. A5 는 질의 0 · 요청 0 · doctor 1 이고 `problems` 에 A1 문장. A2 · A4 · A6 · A9 는 묻지 않고, 진짜 서버만 J1 이면 그 서버로 가며 위장 서버만 있거나 둘이 다 J1 이면 토큰 0 이다. A7 은 질의 0 · 종료 2(doctor 1). A8 은 결정 246 그대로(묻고 비TTY 3, `/client/<wheel>` 0). A10 은 묻지 않는다. M5t-a 의 TD10 · TD28 · TD48 시험이 이 표로 바뀌었다(F47) | 단위 · 가짜 mDNS + 기록 서버 + `is_tty` · `ask` 주입 | PR3 |
| PI42 | 〔처음 설정 · 316〕 (a) `rcm init worker` 를 친다 (b) 템플릿과 예시를 비교한다 (c) 그 파일 그대로 `rcm worker` 를 띄운다 (d) `docs/configuration.md` 「Second build machine」 · README 의 처음 설정 블록을 읽는다 (e) `rcm setup --role coordinator-worker --yes …` 가 찍는 워커 명령을 본다 | (a) stdout 은 경로 한 줄, stderr 첫 줄이 「pair this machine first」와 코디네이터 · 이 머신에서 칠 두 명령이고 둘째 줄이 짝짓지 않는 길이다. 파일은 0600 이고 `server = "auto"` 다. (b) 바이트가 같다. (c) A1 문장 · 종료 2 · mDNS 질의 0. (d) 첫 블록이 `rcm pair --worker` → `rcm setup --role worker --pair` 순서이고 `rcm init worker` 를 「미완성 틀」로 소개하며, 문서 묶음에 `rcm init worker` · `--service-file` · `server_name` · `server = "auto"` 가 남아 있다(`tests/test_docs_m5n.py:80-85`). (e) `rcm pair --worker` 가 먼저, `rcm token add <이름> --worker` 는 「without pairing」 으로 찍힌다 | 단위 · 파일 바이트 · stderr · 문서 잠금 | PR3 · PR4 |
| PI43 | 〔setup 교환 오류 · V2-4〕 `rcm setup --role worker --pair` 의 교환 답을 200 · MAC 틀림 / 404 / 400 `bad_pair_request` / 409 `name_taken` / 413 / 429 / 503 `pair_failed` / 503(동시 상한) / 500 / 307 / 보내기 전 연결 거부 / 보낸 뒤 끊김으로 바꿔 준다. `--name x` 를 붙여 친다. 호스트 이름이 70자다 | 행마다 §4.6 교환 오류 표의 종료 코드 · 문장이고 200 · MAC 맞음 밖에서는 `worker.toml` 이 생기지 않는다. 409 뒤 창은 열린 채이고 실패 수가 그대로다. 307 은 따라가지 않는다(대상 서버 요청 0). `--name` 은 argparse 종료 2. 보낸 `host_name` 은 호스트 이름 첫 조각을 64 코드 포인트로 자른 값이다 | e2e · 응답을 주입하는 기록 서버 · 파일 목록 | PR4 |
| PI44 | 〔클라이언트 종료 코드 · V1-4〕 짝지은 클라이언트에서 (a) `rcm run` 이 J1 뒤 제출하고, 업로드 · 기다림 단계에 위장 서버가 그 URL 을 차지한다(J2) (b) `rcm top --watch` 첫 화면 뒤 J2 (c) `rcm cancel` 을 보낸 뒤 다음 요청이 J3 (d) 창을 연 `rcm pair` 가 기다리는 중 J2 (e) (a)~(d) 의 같은 자리에서 J4 | (a)~(d) 모두 종료 3 과 「the job (or window) stays on the server; the result is unknown here」이고 위장 서버가 받은 `Authorization` 은 0건이다. 아무것도 보내기 전의 J2 는 종료 2(PI24 c). (e) 오늘의 재연결 · 기다림이 이어지고 종료하지 않는다(`rcm top --watch` 는 「unreachable」 화면) | e2e · 기록 서버 + 주입 | PR3 · PR4 |

Excluded (rare): 같은 `pair.key` 를 가진 `data_dir` 복제 서버 둘이 한 망에서 동시에 답함 · ① 백업 복원 + 원본 가동 + 같은 망 · 결정 289 가 지원하지 않는다고 문서화하고, 다시 찾기에서는 PI4 (c) 가 「옮기지 않음」을 본다 · reports searched, 0 hits
Excluded (rare): 교환 도중 SIGKILL 로 창의 실패 수가 사라짐 · ② · 창은 메모리라 재시작이 창을 닫는다(PI11), 되돌릴 수 있다 · reports searched, 0 hits
Excluded (rare): `--replace` 교환 둘이 같은 순간에 겹침 · ①②(같은 이름 창은 kind 마다 하나라 두 창이 동시에 있을 수 없고, 겹치려면 창 둘 + 동시 교환) · PI19 가 활성 토큰 1개를 본다 · reports searched, 0 hits
Excluded (rare): 챌린지 120초 안에 서버 벽시계가 5초 넘게 뒤로 감 · ③ OS 시계 계약 · 결과는 J8(PI36 h), 되돌릴 수 있다 · reports searched, 0 hits
Excluded (rare): 9개 넘는 가짜 mDNS 인스턴스로 진짜 서버를 후보 밖으로 밀어내는 동시에 진짜 서버의 주소가 바뀜 · ① · 결과는 옮기지 않음(토큰 0, 서비스 거부만, 되돌릴 수 있다), 후보 상한 자체는 PI4 (c) · reports searched, 0 hits
Excluded (rare): 상태 파일을 새로 쓰는 순간 디스크가 가득 차고 동시에 신원 판정이 바뀜 · ① · 결과는 옛 파일이 남아 180초 뒤 「오래됨」(모름)으로 보이고 워커 로그에 쓰기 실패 줄 — 되돌릴 수 있다 · reports searched, 0 hits

이력 검색은 `git log -i --grep` 와 `CHANGELOG.md` · `docs/acceptance/reports/` 를 `pair` · `spoof` · `impostor` ·
`pair.key` · `replace` · `SIGKILL` · `redirect` 로 찾았다. `redirect` 는 M5q · M5p 가 이미 다룬 함정이다.
`follow.py:648-656` 의 주석이 「토큰을 다른 호스트로 실어 나르지 않는다」고 적었고 `docs/m5q-workplan.md:643` 도 같은
함정을 다뤘다. 이미 보고된 함정은 흔한 것으로 보고 PI37 행으로 넣었다. 나머지 맞은 줄은 다른 기능의 것이다.
3판은 `surrogate` · `scrypt` · `os.link` · `hard link` · `state file` · `status file` · `disk full` 로 더 찾았다.
`hard link` 로 맞은 줄은 git 미러의 묶음 파일 공유(CHANGELOG 1617 · 1621,
`docs/acceptance/reports/2026-09-10-m5i-verify-accounting.md`)라 다른 기능이고 나머지는 0건이다.

### 변이

`scripts/mutcheck.py` 에 아래 변이를 더한다(PI30). 「빨개질 시험」은 그 PR 안의 시험이다(V1-7). 다른 PR 이 만든 코드를
바꾸는 변이도 있다. PR2 의 변이가 PR1 의 `pairing.py` 를 바꾸는 것이 그 예다. 변이는 파일을 고치지 않는다.

| 변이 이름 | 바꾸는 것 | 빨개질 시험(행) | PR |
|---|---|---|---|
| `pair-hello-mac-not-checked` | `verify_hello` 가 `mac` 비교 없이 참 | PI5 · PI33 | PR1 |
| `pair-hello-fixed-nonce` | nonce 를 상수로 | PI5 | PR1 |
| `pair-hello-dialed-check-skipped` | 서버가 `dialed` 를 자기 주소 집합과 대조하지 않음 | PI5 · PI22 | PR1 |
| `pair-hello-challenge-unbound` | hello MAC 에서 챌린지를 뺌 | PI33 | PR1 |
| `pair-code-reusable` | 성공한 교환이 창을 닫지 않음 | PI8 | PR1 |
| `pair-exchange-trusts-body-kind` | 증명이 맞는 아무 열린 창으로 본문 `kind` 대로 발급 | PI7 | PR1 |
| `pair-wrong-code-not-counted` | 실패 세기를 지움 | PI9 | PR1 |
| `pair-code-compare-not-constant-time` | `compare_digest` → `==` | PI9(spy) | PR1 |
| `pair-open-without-admin` | `POST /pair` 의 `require_admin` 을 지움 | PI20 | PR1 |
| `pair-secret-logged` | 교환 로그 줄에 토큰을 넣음 | PI17 | PR1 |
| `pair-key-silently-regenerated` | 키가 없으면 짝 토큰이 있어도 새로 만듦 | PI35 | PR1 |
| `pair-key-written-in-place` | 임시 파일 + `link` 대신 `pair.key` 에 바로 씀 | PI35 | PR1 |
| `pair-key-link-falls-back-to-rename` | `link` 의 다른 `OSError` 에서 `os.replace` 로 물러섬 | PI35 | PR1 |
| `pair-key-mode-not-checked` | 키 읽기의 모드 검사를 지움 | PI35 · PI17 | PR1 |
| `pair-code-key-unstretched` | `pair_code_key` 가 scrypt 대신 SHA256 | PI33 | PR1 |
| `pair-exchange-shape-not-checked` | 교환의 형식 검사를 지움(서러게이트가 500) | PI40 | PR1 |
| `pair-list-keeps-by-opened-at` | `GET /pair` 보존을 `opened_at` 기준으로 | PI11 | PR1 |
| `pair-label-not-cleaned` | 로그 · 목록에 `host_name` 원문을 씀 | PI12 | PR1 |
| `pair-replace-two-statements` | `--replace` 를 폐기 + 새 행 두 문장으로 | PI19 | PR1 |
| `register-pool-check-skipped` | 등록의 풀 검사를 지움 | PI13 | PR2 |
| `spec-null-pool-unfiltered` | 풀 NULL 이면 거르지 않음 | PI16 · PI14 | PR2 |
| `register-proof-skipped` | 증명 검사를 지움 | PI23 | PR2 |
| `pair-challenge-reusable` | 쓴 챌린지 집합을 지움 | PI23 | PR2 |
| `register-proof-unbound-session` | 증명 MAC 에서 세션 id 를 뺌 | PI23 | PR2 |
| `register-challenge-mac-skipped` | 챌린지 MAC 검사(토큰의 `pair_id`)를 건너뜀 | PI23 | PR2 |
| `register-challenge-burned-before-mac` | 증명 MAC 검사 전에 챌린지를 쓴 집합에 넣음 | PI23 | PR2 |
| `pair-gate-skipped-in-download` | F19 의 `download` 자리에서 관문을 건너뜀 | PI1 · PI2 | PR3 |
| `pair-gate-ignores-verify-result` | 관문이 `verify_hello` 결과를 보지 않고 「확인됨」 | PI1 | PR3 |
| `rediscover-unverified-move` | 다시 찾은 주소로 hello 없이 옮김 | PI1 · PI4 | PR3 |
| `legacy-auto-not-refused` | 옛 토큰 + `auto` 거절을 지움 | PI3 | PR3 |
| `paired-follows-redirect` | 짝지은 머신이 따르는 오프너를 씀 | PI37 | PR3 |
| `hello-mismatch-exits` | J2 에서 종료 0 | PI1 · PI36 | PR3 |
| `transient-hello-exits` | J4 에서 종료 | PI36 | PR3 |
| `token-reject-without-fresh-hello` | 401 에 새 hello 없이 J6 | PI36 | PR3 |
| `bad-mac-exits-first-time` | 첫 `bad_mac` 에서 J7 | PI36 | PR3 |
| `pair-warn-not-repeated` | 경고를 (주소, 판정)마다 한 번만 | PI36 · PI39 | PR3 |
| `pair-state-secret-written` | 상태 파일에 세션 id 원값을 씀 | PI39 | PR3 |
| `pair-state-stale-read-as-ok` | 읽는 쪽이 오래됨 · 죽은 프로세스를 보지 않음 | PI39 | PR3 |
| `pair-state-written-in-place` | 임시 파일 + `os.replace` 대신 바로 씀 | PI39 | PR3 |
| `paired-auto-still-asks` | 짝지은 `auto` 에서도 사람에게 물음 | PI41 | PR3 |
| `unpaired-auto-worker-discovers` | 짝짓지 않은 `auto` 워커 거절 전에 mDNS 질의 | PI41 · PI3 | PR3 |
| `unpaired-auto-doctor-confirms` | doctor 가 짝짓지 않은 `auto` 워커에 246 확인을 씀 | PI41 | PR3 |
| `tokenless-auto-client-follows` | 토큰 없는 `auto` 클라이언트가 따라감(300) | PI24 | PR3 |
| `client-after-submit-exits-2` | 제출 뒤 J2 를 종료 2 로 | PI44 | PR3 |
| `exchange-response-mac-not-checked` | setup 이 응답 MAC 을 보지 않고 저장 | PI34 | PR4 |
| `exchange-sends-code` | 교환 본문에 코드를 실음 | PI21 · PI34 | PR4 |
| `code-format-not-prechecked` | 형식 검사 없이 보냄 | PI38 | PR4 |
| `pair-restart-reported-unused` | 사라진 창 id 를 1 로(312) | PI11 | PR4 |
| `setup-saves-on-409` | 409 `name_taken` 에서 파일을 씀 | PI43 | PR4 |
| `pair-other-machine-no-warning` | 코디네이터 밖 `rcm pair` 의 평문 경고를 지움 | PI21 | PR4 |

2판의 `identity-mismatch-retried`(「`mismatch` 를 재시도로 분류」)는 3판에서 맞는 동작이 되었다. 그래서 그 변이를 지우고
`hello-mismatch-exits` 로 바꿨다(§12).

## 8. 개인정보 점검

이 마일스톤은 main 릴리스로 끝나므로 개인정보를 점검한다.

- 시험과 fixture 의 토큰 · 코드 · `pair_secret` · `pair.key` 는 실행 중에 만든다. 고엔트로피 문자열 리터럴은 커밋하지
  않는다. gitleaks 는 브랜치의 커밋을 훑기 때문에 나중에 고쳐도 소용없다(AGENTS 함정).
- 주소는 127.0.0.x(별칭 루프백)와 192.0.2.0/24(문서용), 100.64.0.0/10 의 문서 예시만 쓴다. 실제 사설 IP 와 호스트 이름,
  tailnet 이름은 쓰지 않는다.
- 머신 이름 예시는 `build-01` · `mini-2` 같은 일반 이름이다. 사내 레포 이름과 앱 이름은 쓰지 않는다.
- 문서와 명세에는 이름과 경로가 없는 오너 말만 인용한다. 이 명세의 인용이 그렇다. Codex 원문은 레포 밖에 있다고만 적는다.
- 이 마일스톤에는 새 웹 화면이 없다. PR6 의 CDP 캡처는 보고서에 넣기 전에 토큰 입력 칸을 가린다.
- 릴리스 PR 은 메인 세션의 현행 절차로 점검한다. 레포 밖 패턴 파일, gitleaks 범위, 커밋 이메일, 스크린샷을 본다.
- 골든 시험(PI33)은 낮은 엔트로피 입력(`bytes(range(32))`, `"00112233…eeff"`)과 `enc` 바이트 리터럴만 커밋한다. 64자
  hex 의 MAC 과 키 결과는 리터럴로 두지 않고 시험 안의 독립 구현으로 계산해 비교한다. gitleaks 의 고엔트로피 규칙
  때문이다(AGENTS 함정).
- 교환 요청 캡처(PI21 · PI31)는 보고서에 옮기기 전에 토큰과 `pair_secret`, 증명 값을 가린다.
- 상태 파일(`pair-state/*.json`)에는 토큰 · `pair_secret` · 세션 id · 코드가 없고 `pair_id` 와 서버 주소만 있다. 보고서에
  옮길 때는 주소를 문서용 대역으로 바꾼다(PI39).
- `pair_code_key` 의 결과(64자 hex)는 리터럴로 커밋하지 않는다. 시험 안에서 `hashlib.scrypt` 로 다시 계산해 비교한다(PI33).

## 9. 위험

이 설계가 막는 위험과 막는 법이다. 3판은 종료 0 과 재시도 행을 J 번호로 고치고 2라운드 지적의 행을 더했다. 2판의 「옛 토큰 +
`auto` 워커 · 클라이언트가 업그레이드 뒤 멈춘다」 행은 짝짓지 않은 `auto` 행으로 합쳤다.

| 위험 | 막는 법 | 결정 · 시나리오 |
|---|---|---|
| 위장 서버가 짝지은 워커의 토큰을 받고 argv 를 돌린다 | 관문이 F19 여섯 자리 모두에 있고 hello 가 `ok` 가 아니면 토큰 0. 리디렉트를 따르지 않는다 | 290 · 295 · 309 · PI1 · PI2 · PI37 |
| 교환 URL 의 사칭자가 워커를 영구히 속인다 | 코드를 보내지 않는 교환과 코드 키 응답 MAC(저장 전 검사) | 301 · PI34 |
| J6 · J7 의 종료 0 을 서비스가 되살리지 않아 워커가 멈춰 있다(일부러) | 문장이 이유와 다시 짝짓는 법을 말하고 상태 파일 `exited` 를 `rcm worker --check` · `rcm doctor` 가 FAIL 로 보인다. `rcm machines` 에서 레인이 down. 운영 문서(PR5) | 314 · 317 · PI36 · PI39 |
| 인증되지 않은 답(J2~J5)이 계속되면 워커가 끝없이 다시 시도한다 | 토큰 0, 60초 상한, 10분 반복 경고, 상태 파일, `rcm worker --check` · `rcm doctor` 의 FAIL · 모름. 진짜 서버가 돌아오면 사람 손 없이 붙는다 | 304 · 314 · 317 · PI36 · PI39 |
| 부모 명세 M8 「되살리지 않는 종료 코드」와 어긋남(V1-4 · R3) | M8 을 「펜싱(M5t-b) · J6 · J7(이 명세)」로 좁힌다. J2~J5 · J8 은 프로세스 안 재시도라 되살림 루프가 없다. 프로토콜 불일치는 M5t-b 대로 3 | 303 · 317 |
| hello 가 동시 처리 상한을 지나 503 을 받고 heartbeat 이 멈춘다(V2-10) | 503 은 `unavailable`(재시도, 종료 없음). 확인된 뒤의 503 은 다시 hello 를 부르지 않는다 — hello 는 연결 실패 뒤에만. heartbeat 이 멈춰도 M5t-b 에서 `worker_timeout` 은 레인 down 만이고 잡은 `disconnect_grace_seconds`(600) 유예 뒤에야 `lost`(F37) | 303 · F39 · PI36 |
| 짝짓지 않은 `auto` 워커 · 토큰 있는 `auto` 클라이언트(옛 `rcm init worker` 기본)가 업그레이드 뒤 멈춘다(의도된 거절, 다른 머신 수 모름 — M3) | 반영 전 확인이 반영을 막는다(§6 17), CHANGELOG `Changed`, A1 · A7 문장이 고치는 법을 말한다 | 283 · 285 · 316 · PI3 · PI41 · PI42 |
| 익명 hello · 교환이 서버를 바쁘게 한다 | 본문 1 KiB · 동시 요청 상한 · hello 는 Store 를 부르지 않는다 · 계산 1.88 µs(M1) · 교환 실패는 메모리만 | PI20 |
| 창 상태가 읽기 라우트 · TXT 로 드러난다 | 창은 메모리에만 있고 `/api/status` · TXT 에 칸이 없다 | PI10 |
| `pair.key` 가 사라져 서버가 조용히 새 키로 모든 짝을 끊는다(V2-5 a) | 살아 있는 짝 토큰이 있으면 기동 거절(fail-open 금지) | 306 · PI35 |
| 두 프로세스가 `pair.key` 를 만들다 한쪽이 빈 파일을 읽는다(V2-5 b) | 임시 파일 + `link`(원자, 덮어쓰지 않음) | 306 · PI35 |
| `pair.key` 가 느슨한 권한 · 링크로 남는다 | 만들 때 0600, 읽을 때 `O_NOFOLLOW` · 모드 · 길이 검사로 기동 거절 | 281 · PI17 · PI35 |
| 교환은 됐는데 응답을 잃어 발급만 된 토큰이 남는다 | `rcm token list` 의 `paired from` · setup 이 폐기 명령을 말한다 | PI8 |
| `--replace` 가 도는 워커의 잡을 끊는다 | 옛 토큰이 401 → 옛 워커는 도는 잡을 끝낸 뒤 종료 0. 보고하지 못한 잡은 M5t-b 유예 뒤 `lost`(M5n §13 의 자동 재실행 금지 유지). `rcm pair` 가 「the old token stops working now」 | 305 · PI19 |
| M5t-b 가 v26 · 세션 PR 을 늦게 내거나 바꾼다 | PR1 · PR2 · PR3 의 시작 조건(F35). 번호 · 이름이 바뀌면 명세 개정(§13) | 299 · F30~F37 |
| M5t-b 명세 §4.7 이 「M5t-c 도 종료 3」을 적은 채 머지된다 | 질문 Q-E — 메인 세션이 #270 에서 고치거나 이 명세 §12 에 대체를 적는다 | 303 · F33 |
| Docker · 프록시 뒤 서버의 hello 가 421 | `public_url` 을 쓰라고 메시지와 `docs/operating.md` 가 말한다. `https` public_url 은 443 으로 맞는다 | 308 · PI22 · PI29 |
| 운영 DB 를 다른 빌드가 연다 | `rcm pair` 는 HTTP, setup 은 Store 를 열지 않는다(M5t-a) | PI28 |
| 토큰 없는 `auto` 클라이언트가 찾은 위장 서버의 wheel 을 실행한다 | 찾기는 그대로, wheel 내려받기 · 실행은 검증된 `pair_secret` 이 있을 때만 | 300 · PI24 |
| 토큰과 `pair_secret` 이 어긋난 워커가 `bad_mac` 을 받으며 조용히 돈다(V2-2) | 상태 파일 · 경고, 세 번 이어지면 J7(종료 0) | 313 · 314 · 317 · PI36 · PI39 |
| 익명 교환 본문의 서러게이트 · 비문자열이 500 을 낸다(V2-5) | 창 조회 전 형식 검사, 같은 400 | 319 · PI40 |
| 막 끝난 창이 목록에서 빠져 `rcm pair` 가 재시작으로 말한다(V2-10) | 닫힌 창을 `closed_at` 부터 1시간 남긴다 | 319 · PI11 |
| 하드 링크를 못 하는 파일 시스템에서 `pair.key` 를 만들지 못한다(V1-12) | 기동 거절과 고치는 법, rename 으로 물러서지 않음 | 306 · 319 · PI35 |
| M5t-a 의 사람 확인(246)을 조용히 뒤집는다(V1-1) | §1 R7 · R8 과 `auto` 표, M5t-a 시험을 PR3 이 고친다 | 315 · PI41 |

### 남는 위험

이 설계로 막지 않는 위험이다. 행마다 이 명세가 대신 하는 일을 적었다. 2판의 「위장 서버가 계속 그 주소에 있으면 짝지은
워커가 멈춰 있다」 행은 서비스 거부 행으로 바꿨다(317).

| 남는 위험 | 이 명세가 하는 것 | 결정 |
|---|---|---|
| 같은 망의 평문 엿보기: 교환 응답의 토큰 · `pair_secret`, 요청의 토큰 · 세션 id | 막지 않는다. 코드는 망으로 나가지 않고 한 번 · 짧은 수명. LAN · `0.0.0.0` URL 경고, Tailscale 권고 | 282 · PI21 |
| 엿본(또는 교환 URL 의 사칭자가 받은) 교환 증명으로 하는 오프라인 코드 추측 | 막지 않는다. 5회 제한은 온라인만. 숫자 M7(scrypt: 한 코어 1.77e6 년, 10분 안에 절반은 동시 계산 4.7e10 개 · 약 1.6e18 바이트, GPU 는 재지 않음 — 메모리 경계). 맞힌 코드는 그 창이 열려 있는 동안만 쓸모 있다 | 301 · 319 · PI21 · PI34 |
| 경로 위의 능동 중계(교환 · hello 를 진짜 서버로 그대로 넘김) | 막지 않는다. 이름 URL 경고, IP · Tailscale 주소 권고 | 282 · 301 · PI6 |
| 같은 IP 를 가로채 요청 사이에 끼어들기(ARP 스푸핑) | 막지 않는다(요청 서명 없음). 연결 실패 뒤에는 다시 hello | 282 · PI4 |
| `worker.toml` 통째 복사(토큰 + `pair_secret`) = 워커 신원 복제 | 막지 않는다(로컬 침해). 0600 은 다른 보통 사용자만 막고 소유자 · root · 악성코드 · 백업 도난은 못 막는다. 알아보는 길: 다른 주소 재등록 로그 · 펜싱(M5t-b) · `rcm token revoke` | 302 · PI23 |
| 토큰만 샌 경우의 `/worker/spec` 읽기 | 막지 않는다. 묶인 풀의 프리셋 · env 만 보인다. 비밀이 필요한 프리셋은 `local_only` | 284 · 294 · PI14 · PI23 |
| 틀린 코드 5번으로 창을 닫는 방해 | 막지 않는다. 코디네이터에 즉시 이유 | 288 · PI9 |
| `data_dir` 을 키와 함께 통째로 복사한 사람의 사칭 | 지원하지 않는 구성으로 문서화, 신원 재설정 절차 | 289 · PI18 |
| mDNS 홍수로 다시 찾기를 막기 | 막지 않는다(서비스 거부만, 토큰 0) | 310 · PI4 |
| 위장 서버가 그 주소에 있는 동안 짝지은 워커가 일을 받지 못한다(서비스 거부) | 막지 않는다(토큰은 지킨다). 영구 정지는 아니다 — 위장 서버가 사라지면 스스로 다시 붙는다. 상태 파일 · 경고로 보인다 | 303 · 317 · PI1 |
| `any-capable` 프리셋 env 를 붙은 워커 모두가 받는다 | 비밀이 필요한 프리셋은 `local_only`(결정 254) | 284 · PI14 |
| 토큰 없이 명시 URL 로 따라가는 클라이언트는 그 URL 의 wheel 을 실행한다 | 막지 않는다. 명시 URL 은 사용자의 신뢰 결정 | 300 · PI24 |
| 명시 URL 의 옛 토큰 머신은 그 URL 을 믿는다(리디렉트도 오늘대로) | 오늘과 같다. `rcm worker --check` 의 `warn` 이 다시 짝짓기를 권한다 | 283 · 309 · PI26 · PI37 |
| 엿본 교환 요청의 재생(T4 c): 증명은 창이 닫힐 때까지 bearer | 막지 않는다. 한 번 쓰는 창 · 짧은 수명. 양쪽 완료 줄과 `rcm token list` 의 `paired from` 주소로 알아보고 폐기 | 282 · 301 · PI21 |
| 경로 위 능동 공격자가 통과한 hello 바로 뒤의 401 · 등록 응답을 위조해 J6 · J7 종료 0 을 만든다(T4 e) | 막지 않는다(요청 서명 없음). 문장 · 상태 파일로 드러나고 사람이 다시 띄운다 | 282 · 317 |
| 코디네이터 밖에서 친 `rcm pair` 의 201 응답(코드)과 admin 토큰이 평문 LAN 을 지난다(V2-7) | 막지 않는다. 평문 경고, 코디네이터에서 치거나 Tailscale | 282 · 292 · PI21 |
| 신원 정지(J2~J5)와 끝내는 중(J6 · J7) 동안 취소가 워커에 닿지 않아 되돌릴 수 있는 잡이 끝까지 돈다 | 막지 않는다(결정 253 상속). 서버는 유예 뒤 `lost`, `irreversible` 잡은 스스로 멈춘다(M5t-b). 다시 J1 이 되면 M5t-b 결정 322 가 멈춘다. 로컬 시한은 그대로 | 318 · PI36 |
| M5t-b 만 아는 옛 빌드 워커가 복사한 짝 토큰으로 서비스가 되살릴 때마다 403 을 받는다(V2-11) | 막지 않는다. 403 문장이 업그레이드 · 짝짓기를 말한다(4.8) | 287 |
| 새로 짝지은 토큰을 든 옛 빌드 워커(M5t-b 만 안다)가 403 `pair_proof_required` 로 끝나고 서비스가 5~10초마다 다시 띄운다. 옛 빌드는 상태 파일을 쓰지 않는다(6차 위임 결정) | 막지 않는다. 운영 반영 전에 그런 워커를 찾아 그 머신을 올리거나 다시 짝짓는다(§6 17). 403 문장이 업그레이드 · 짝짓기를 말한다(4.8) | 287 · 315 |

## 10. 요청자 문서에 돌려줄 것

없다. 하류 문서가 요청한 것이 없다.

## 11. 결정 표

「오너 확인」 열의 「확정(위임, 2026-11-26) · Codex 와 논의해 선 조치」는 오너가 위임한 결정을 메인 세션이 Codex 와 논의해
먼저 조치했다는 뜻이다. 오너 말대로 완료 보고서에 Codex 와 논의한 내용을 적는다. 280~289 는 1차, 290~300 은 2차, 301~303 은
3차, 304~312 는 4차, 313~318 은 5차 위임 결정이다. 319 는 3판의 세부 묶음이고 6차 위임 결정이 확정했다. 314 · 315 · 317 의
세부 값도 6차가 확정했다. 2판은 287 과 295 를 고쳤고 3판은 287 · 291 · 296 · 301 · 303 · 304 · 305 에 3판 문장을 달았다. Q-B
와 Q-C 는 결정 번호 없이 §1 의 2차 결정 표에 두었다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다름 |
|---|---|---|---|---|
| 280 | 자동 발급 · 자동 가입(M5n §13, Q1) | 사람이 `rcm pair` 로 연 짧은 수명(기본 10분 · 상한 60분) · 한 번 쓰는 창 안에서만, 그 창의 kind · 풀 · 이름으로 발급하고 가입한다. M5n §13 해당 줄(`docs/m5n-workplan.md:650`)에 개정 날짜와 이 번호를 적는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 「연결하면」이 사람 손 두 번(창 열기 · 코드 넣기)이 된다 |
| 281 | 신원 비밀 저장(Q12) | DB 밖 `<data_dir>/pair.key`(0600) 하나에서 `pair_id` 마다 파생. DB 에는 `pair_id` 만. 키를 잃거나 바꾸면 다시 짝짓기 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 282 | TLS 없는 LAN(Q13) | 엿보기 · 이름 URL 중계 · 같은 IP 가로채기를 받아들인다. 요청마다 서명하지 않는다. 남는 위험 표에 적고 LAN · 이름 URL 은 경고, Tailscale 을 권한다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 283 | 옛 토큰 + `server = "auto"` 워커(N1) | 거절하고 다시 짝짓는 법을 안내한다. 명시 URL 의 옛 토큰 워커는 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 오늘 되던 설정이 멈춘다 |
| 284 | `tokens.pool` NULL(N2) | 묶이지 않은 옛 토큰. 등록 때 말한 풀로 등록, `/worker/spec` 은 등록한 풀로 거른다. 「—」로 보인다. 기본 풀이나 무제한으로 보지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 285 | 클라이언트(N3) | 짝지은 클라이언트도 URL 과 `pair_secret` 을 저장하고 health · 따라가기 · 인증 요청 전에 hello, 조용한 재발견 없음. 옛 명시 토큰 클라이언트는 그대로, 토큰 + `auto` 는 283 규칙 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 오늘 되던 설정(토큰 + `auto`)이 멈춘다 |
| 286 | 두 번째 승인(N4) | 없음. 즉시 발급하고 붙은 머신 · 주소 · kind · 풀 · 시각 · 폐기 명령을 `rcm pair` 완료 줄 · 서버 로그 · `rcm token list` 에 남긴다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 287 | 탈취 토큰 재등록(N5) | 짝지은 워커의 등록(M5t-b 의 세션 여는 `POST /worker/register`)은 hello 챌린지로 만든 `pair_proof` 가 있어야 한다. 증명은 `worker_session_id` 를 묶는다. 옛 명시 토큰은 토큰만. 검사 자리는 M5t-b register 순서의 3 다음 · 4 앞(1판의 「`mark_lost_for_worker` 앞」을 M5t-b 순서로 바꿔 적음) 3판: `reason` 은 `expired` · `reused` · `bad_mac` 셋(313), 검사 자리는 M5t-b 2판 순서의 4(F30) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 288 | 창 닫기 방해(N6) | 받아들인다. 실패는 창에 센다(5번). 닫히면 서버 로그와 기다리던 `rcm pair` 에 이유와 새 창이 필요하다는 줄 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 289 | 신원 키 수명 주기 | 키를 잃거나 바꾸면 모든 신원 증명이 무효이고 다시 짝짓기. DB 만 복원 · 복제해서는 두 번째 신뢰 서버가 생기지 않는다. DB 와 키를 함께 두 살아 있는 코디네이터에 복원하는 것은 지원하지 않고 문서화된 신원 재설정(정지 → 짝지은 토큰을 `rcm token revoke` → `pair.key` 삭제 → 시작 → 다시 짝짓기, 306) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 290 | hello 의 꼴(설계) | `POST /pair/hello` 익명 · 상태 없음 · Store 를 부르지 않음. 자기 주소 = 요청이 도착한 소켓 주소 · `public_url` · 광고 이름. 길이 접두 인코딩 + 용도 라벨. TXT 에 `server_id` 를 더하지 않는다. 고른 안: 소켓 주소 판정. 버린 안: 인터페이스 주소 목록(F15 가 정확하지 않다), 보관함 허용 목록 재사용(F14 · 근본 원인 R1), `server_id` 대조(베낄 수 있다, 1라운드 A-M14) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 291 | 짝짓기 창(설계) | 메모리, kind 마다 하나, 10자 base32, 창당 실패 5, 모든 실패에 같은 404, 교환은 창 잠금 안에서. 버린 안: DB 표 `pair_windows`(익명 실패가 DB 를 쓰고, 재시작이 창을 살린다) 3판: 형식이 틀린 교환 본문은 404 가 아니라 창 조회 전 400(319 b) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 3판과 다름(§12) |
| 292 | `rcm pair`(설계) | HTTP + admin 토큰, DB 를 열지 않는다, 기다리며 완료 줄, Ctrl-C 는 창 취소. 버린 안: DB 를 직접 여는 꼴(가드 · 마이그레이션, 1라운드 B-H1) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | admin 토큰이 필요하다(M5t-a setup 은 client 토큰 명령만 찍는다) |
| 293 | DB v27 과 `--replace`(설계) | `tokens` 에 `pool` · `pair_id` · `paired_host` · `paired_addr` 와 `pair_id` 고유 색인. `--replace` 는 같은 행 한 UPDATE | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 294 | 풀 묶기 · spec 거르기(설계) | 등록 409 `pool_mismatch`. 거르기 규칙 하나(`spec_presets_for_pool`): 토큰 풀 → 등록 풀 → 질의 `pool` → `default` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 295 | 확인 시점(설계) | 4.3 의 상태 전이 표. 연결 실패 뒤 다시 hello. 다시 찾기는 hello 를 통과한 후보로만. **2판: 「기동 실패는 종료 3」은 303 으로 개정. 3판: 종료 0 은 317 로 J6 · J7 두 경우뿐(4.3 판정 표), 그 밖은 모두 재시도** | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 종료 코드 부분은 303(2026-11-26)으로 개정 | — |
| 296 | 받는 쪽 CLI(설계) | `rcm setup --role worker --pair` · `--role client --pair`, 코드는 프롬프트 · stdin, 파일은 처음부터 0600, 완료 줄. 수동 `rcm token add` 는 바꾸지 않는다 3판: 교환 오류 표 · `host_name` 출처 · `--name` 없음(319 c) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 297 | 익명 예외(설계) | 인증 없이 받는 새 라우트는 정확히 둘: `POST /pair/hello` · `POST /pair/exchange`. `/worker/*` 는 인증을 먼저 보는 규칙 그대로. PR0 이 PLAN 「보안」 절에 적고(재료 E2) PR1 의 문서 잠금이 PI20 집합과 대조 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | §12 의 핸드오프 표가 적은 `POST /worker/hello` 와 경로가 다르다 |
| 298 | lane | PR1 full · PR2 full · PR3 full · PR4 full · PR5 short · PR6 short(이유는 §5) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 299 | DB 판 순서 | v27 은 M5t-b 의 v26 뒤. PR1 은 v26 PR 머지 뒤 시작 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 300 | 토큰 없는 클라이언트의 따라가기(P1-Q1) | 바꾼다. mDNS 찾기는 그대로 두고, 찾은 서버의 client wheel 을 내려받아 실행하는 것은 검증된 `pair_secret` 이 있을 때만 한다. 명시 URL 은 사용자의 신뢰 결정으로 남긴다. 결정 300(280~299 가 모두 쓰여 다음 빈 번호). 따라가지 않은 뒤에도 명령은 이어 가고 stderr 에 한 줄 남긴다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 301 | 교환의 능동 사칭자(3차) | 요청은 코드 대신 `worker_nonce` 와 코드 키 증명, 응답은 두 nonce 와 전체 페이로드에 대한 코드 키 MAC. 워커는 MAC 이 맞을 때만 저장. 경로 위 능동 중계 · 평문 자격 증명은 남는 위험(T4). 관찰한 증명으로 창이 열린 동안 오프라인 추측이 가능함을 적는다(M4) 3판: 코드 키는 scrypt(319 a) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 302 | 복제된 `worker.toml`(3차) | 받아들인다. 토큰만 샌 경우는 막고, 토큰 + `pair_secret` 파일 복사는 워커 신원 복제 = 로컬 침해로 남는 위험. 0600 은 다른 보통 사용자만 막는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 303 | 종료 코드와 부모 명세 M8(3차) | 확실한 신원 불일치는 종료 0 + 「다시 짝지으라」(되살리지 않음). 연결 실패 · 5xx · 503 은 프로세스 안에서 물러서며 다시 시도(종료 없음). 프로토콜 불일치는 M5t-b 대로 3. M8 은 「펜싱 · 확실한 신원 거절」로 좁힌다. M5t-c 는 종료 3 을 쓰지 않는다 3판: 「확실한 신원 불일치」는 317 이 J6 · J7 로 좁혔다 — MAC 이 틀린 hello 와 421 은 재시도 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(메인 세션 선택) | 1판 295 의 종료 3 을 바꿈 |
| 304 | 303 이 분류하지 않은 hello 응답 | 3xx · 404 · 405 · 다른 4xx · 깨진 200 · `v` ≠ 1 은 「신원을 증명하지 못한 응답」(`other`) — 재시도(토큰 0, 종료 없음, 60초 상한). 경고는 속도를 제한해 주기적으로 되풀이하고 `rcm worker --check` 와 doctor 에 「서버 신원 미확인」 상태를 드러낸다(4차: 주소 · 분류당 로그 한 줄은 영원히 호환되지 않는 서버를 숨긴다). 버린 안: 종료 0(옛 서버로 잠깐 내려간 코디네이터 · DHCP 로 잠깐 그 주소를 받은 장치 때문에 사람이 손댈 때까지 멈춤), 종료 3(되살림 루프가 진짜 서버가 돌아올 때까지 10초마다 돈다 — 303 이 피하려던 것) 3판: J4 · J5, 10분 반복 경고와 상태 파일(314) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | Codex 의견으로 바꿈(2판 재료는 주소 · 분류당 로그 한 줄) |
| 305 | 303 의 적용 범위 | **3판: 이 행의 「확실」 목록은 317 이 대체한다 — 종료 0 은 4.3 판정 표의 J6 · J7 뿐이고 hello `mismatch` · 421 · `pair_mismatch`(313 으로 삭제)는 재시도다. 아래는 2판 원문이다.** 「확실한 신원 불일치」= 설정의 명시 URL 에서 받은 hello `mismatch` · hello 421 `host_not_this_server`, 확인된 서버의 401 · 등록 403 `pair_mismatch`(`auto` 의 후보 하나가 틀린 것은 다른 서버일 뿐이라 재시도). 기동이면 바로 종료 0. 도는 중이면 새 claim 을 멈추고 도는 잡을 끝까지 돌린 뒤(보고는 M5t-b 보고 큐) 잡이 0 이 되면 종료 0 — 그 사이 hello 가 `ok` 가 되면 돌아간다. 옛 토큰 워커의 401 은 오늘대로 2. 클라이언트는 종료 2(`rcm wait` 은 3). 4차: 잡을 비우는 동안 기존 시한 · 취소 집행은 그대로이고 신원 때문에 멈춘 상태를 계속 눈에 띄게 로그에 남긴다. 버린 안: 도는 중 바로 종료(도는 잡을 버린다), 도는 중에는 끝없이 재시도(303 의 「되살리지 않는 종료」와 어긋나고 사람이 원인을 모른다) 3판: (a) 는 317 이 J6 · J7 로 좁혔다. (b) 의 「hello 가 ok 가 되면 돌아간다」는 쓸 데가 없다(R19). (d) 는 4.3 클라이언트 표(319 h) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 짝지은 워커는 토큰 폐기에 종료 0(옛 워커는 2) |
| 306 | `pair.key` 수명 주기 세부 | 없을 때: 폐기되지 않은 짝 토큰이 있으면 기동 거절(`cannot start server: …` 종료 2, 복구 · 재설정 안내), 없으면 새로 만든다. 만들기: 임시 파일 0600 + `fsync` + `os.link` + 폴더 `fsync`. 읽기: `O_NOFOLLOW` · 보통 파일 · 모드 · 길이. 재설정: 새 명령 없이 `rcm token revoke`(짝 토큰) → `pair.key` 삭제 → 시작 → 다시 짝짓기. 버린 안: 조용히 새 키(fail-open), `rcm pair --reset`(`rcm pair` 는 HTTP 만 쓰는데(292) 키가 없으면 서버가 뜨지 못한다 — 오프라인 명령이 되어 DB 를 열어야 하고 가드 목록이 바뀐다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | in 파일 기본값의 `rcm pair --reset` 대신 `rcm token revoke` |
| 307 | 코드 입력 | 알파벳은 그대로(A–Z, 2–7). 정규화(대문자 · `-` · 공백 제거) 뒤 10자 · 알파벳 검사를 보내기 전에 하고 틀리면 종료 2(창의 실패로 세지 않음). 0→O · 1→I · 8→B 바꿈은 하지 않는다(모호). 서버도 형식이 틀린 증명은 세지 않는다. 버린 안: Crockford base32 + 바꿈(1판 계약과 화면이 바뀌고 바꿈 규칙이 하나 더 생긴다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 308 | hello 의 주소 판정 | 본문 `dialed`(스킴 포함 정규형)를 MAC 에 넣고 서버의 자기 주소 집합(소켓 주소 · 루프백이면 localhost · `public_url` · 광고 이름)과 대조. Host 헤더는 쓰지 않는다. 버린 안: 1판의 Host 헤더 + 「포트 없으면 :80」(스킴을 몰라 `https` public_url 을 421 로 만든다, V1-3 · V2-2) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 309 | 리디렉트 | 짝지은 머신의 hello 와 토큰 요청은 3xx 를 따르지 않는다. 옛 머신은 그대로. 버린 안: 모든 머신에서 끊기(옛 동작을 바꾸는 범위 밖 변경) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 310 | 다시 찾기 | 후보 최대 8개에 hello, `ok` 가 정확히 하나일 때만 옮긴다. 둘 이상이면 옮기지 않고 로그 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 311 | 순수 계층 | `core/pairing.py` 는 시계 · 난수 · 파일을 보지 않는다(`now` · 난수 바이트는 인자). `secrets` · 시계 · 키 파일 · 창 상태는 `pairing.py`(PLAN.md:539) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 312 | `rcm pair` 종료 코드 | 0 짝지어짐 · 1 쓰이지 않고 닫힘(만료 · 틀린 코드 5번 · 취소) · 열기 거절 · 2 사용법 · 3 결과 모름(기다리다 서버를 잃고 만료 + 30초까지 못 돌아옴, 또는 재시작 뒤 창이 없는데 창 `id` 와 이어진 영속 상태가 완료나 미사용을 증명하지 못함) · 130 Ctrl-C(취소를 한 번 시도). 창 `id` 로 재시작을 알아본다. 재시작 뒤 창이 없다는 것은 쓰이지 않았다는 증거가 아니다(재시작 직전에 끝났을 수 있다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | Codex 의견으로 바꿈(2판 재료는 재시작으로 사라진 창을 1 로 봤다). 1판의 「3 = 기다리는 중 서버를 잃음」과 PI11 의 「재시작이면 1」의 모순을 풀었다 |
| 313 | `pair_mismatch`(5차) | 지운다. 서버는 등록 증명을 토큰의 `pair_id` 로 만든 K 와 `pair.key` 로 본다(본문의 어떤 값도 `pair_id` 로 쓰지 않는다). 다른 `pair_id` 의 챌린지 · 어긋난 `pair_secret` 은 `bad_mac`. `reason` 셋. 어긋난 워커는 `bad_mac` 을 계속 받고 상태 파일 · 경고로 드러나며 세 번이면 J7 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 2판 287 · 305 의 `pair_mismatch` 를 지움 |
| 314 | 워커 상태 파일(5차) | 짝지은 워커가 `<data_dir>/pair-state/<pair_id>.json` 을 원자적 0600 으로 쓴다(비밀 없음, `updated_at` · `pid` · `process_start` · `session_tag`, 60초마다 새로, 180초 넘으면 오래됨, 종료 때 `exited`). `rcm worker --check` 의 `worker state` 행과 `rcm doctor` 의 `pairing.worker_state` 가 읽는다(4.3 읽는 표). J8 재시도 중은 모름, J2 · J3 · J7 은 FAIL. 신원 · 등록 경고는 10분 반복. M5t-b 세션 폴더에 두지 않는다(F55). 파일 이름 · 키 · 오래됨 기준 · 「계속」= 3번은 6차 위임 결정이 확정했다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 2판 4.6 「doctor 는 워커 상태를 읽지 않는다」를 바꿈 |
| 315 | `auto` 와 사람 확인(5차) | 4.3 `auto` 표 A1~A10. 짝지은 `auto` 는 hello 통과가 결정 246 의 사람 확인을 대신한다. 짝짓지 않은 `auto` 워커는 `rcm worker` · `--check` 가 mDNS 전에 종료 2, `rcm doctor` 는 mDNS · 요청 없이 FAIL. 토큰 있는 짝짓지 않은 `auto` 클라이언트는 종료 2 · doctor FAIL. 토큰 없는 `auto` 클라이언트는 246 그대로. M5t-a TD10 · TD28 · TD48 시험을 PR3 이 고친다(§1 R7 · R8) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | M5t-a 246 · TD10 · TD28 · TD48 을 좁힘 |
| 316 | 처음 설정의 정본(5차) | `rcm pair --worker`(코디네이터) + `rcm setup --role worker --pair --server URL`(워커). `rcm init worker` 는 미완성 틀(템플릿 그대로, 주석과 stderr 가 두 명령을 어느 머신에서 치는지 말한다). 코디네이터 setup 은 `rcm pair --worker` 를 먼저 찍는다(TR27 개정). 템플릿 · 예시 · `tests/test_cli_init_worker.py` 는 PR3, 문서 흐름은 PR4 · PR5(§1 R11~R13) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | M5t-a TR13 · TR27 문구, `docs/configuration.md` 흐름 |
| 317 | 판정과 증거의 인증(5차) | 4.3 판정 표 J1~J12. 인증되지 않은 답(J2~J5)은 어느 것도 확실이 아니다: 토큰 0, 계속 재시도, 10분 반복 경고, 상태 FAIL(J2 · J3) · 모름(J4 · J5). 종료 0 은 hello 를 통과한 서버가 토큰을 거절(401 — 바로 새로 한 hello 가 J1, J6)하거나 등록 증명을 `bad_mac` 으로 세 번 이어서 거절(J7)할 때뿐. 305 (a) 를 좁힌다. 위장 서버는 서비스 거부만 하고 영구 정지를 만들지 못한다(경로 위 능동 위조는 282 의 남는 위험) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 2판의 303 해석(mismatch · 421 → 종료 0)을 바꿈 |
| 318 | 신원 정지 동안의 취소(5차) | 취소는 heartbeat 응답으로만 오므로(F53) J2~J5 동안과 끝내는 중(J6 · J7)에는 닿지 않는다. 서버 유예(M5t-b)가 잡을 `lost` 로 닫고 `irreversible` 잡은 워커가 스스로 멈춘다(F54). 되돌릴 수 있는 잡이 취소 없이 끝까지 도는 것은 결정 253 의 상속이다(§9). 다시 J1 이 되면 M5t-b 결정 322 가 멈춘다. 로컬 시한은 그대로. PI36 이 관찰 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 319 | 2라운드 세부(MED · LOW) | (a) 코드 키 = `scrypt(코드, salt "rcm-pair-code-v1", n 2^15, r 8, p 1, maxmem 64 MiB, 32바이트)` — 301 의 `SHA256(code)` 를 바꿈(M7). 버린 안: pbkdf2(같은 시간에 메모리 경계가 없어 GPU 에 약함), SHA256 그대로 두고 남는 위험만 GPU 기준으로 다시 쓰기(재지 못한 숫자에 기대야 함) (b) 익명 교환의 형식 오류는 창 조회 전 400 `bad_pair_request`(고정 바이트, 세지 않음), `host_name` 0~64 코드 포인트 · 홀로 선 서러게이트 없음 (c) setup 교환 오류 표, `host_name` = `gethostname()` 첫 조각 64 코드 포인트, setup 에 `--name` 없음 (d) 쓴 챌린지는 모든 검사를 통과한 뒤에만 넣는다 (e) client 창 교환 응답의 `pool` · `presets_count` 는 null(MAC 에는 빈 문자열), 키가 빠지면 형식 오류 (f) `GET /pair` 는 닫힌 창을 `closed_at` 부터 1시간 · 20개 (g) `os.link` 의 다른 `OSError` 는 기동 거절, rename 으로 물러서지 않음 (h) 짝지은 클라이언트는 무엇인가 한 뒤 J2 · J3 이면 종료 3(PLAN.md:118) (i) 409 `name_taken` 본문에 `name` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | (a) 301 의 공식 |

## 12. 격리 반박에서 바뀐 것

### 3판의 근본 원인

2라운드 지적 가운데 HIGH V2-1, MED V2-3 · V1-6 · V1-5, LOW V2-9 는 원인이 같았다. 「확실하다」고 판정할 증거가 무엇으로
인증되는지 따지지 않고 종료와 상태를 정했다. 3판은 §4.3 에 판정 표 하나(J1~J12)를 두었다. 이 표는 받은 것, 인증됐는지, 분류,
워커가 하는 일, 종료, 상태 파일, 경고를 한 줄에 적는다. §0 · §4.6 · §7 · §9 · §11 은 J 번호로만 가리킨다. 같은 사실을 여러
곳에 따로 쓰지 않으려는 것이다(M5t 3판 근본 원인 R5).

HIGH V1-1 · V1-2 의 원인은 dev 에 이미 있거나 M5t-a 가 정한 동작과 대조하지 않은 것이다. 3판은 이 명세가 바꾸는 기존 동작을
다시 훑었다. `docs/m5t-workplan.md` 결정 239~255 와 TD · TR 행, `docs/configuration.md` 의 처음 설정 흐름, 템플릿 · 예시와
그 잠금 시험, dev 의 `rcm doctor` · `rcm worker --check` 까지 보고 §1 「뒤집거나 좁히는 것」 표에 모두 넣었다.

### 2라운드에서 2판을 고친 것

두 격리 검증자(V1 · V2)의 2라운드 지적과 3판이 고친 것이다. 지적은 요지만 적었다. 고치지 않은 지적은 없다.

| id | 등급 | 2판 | 3판 | 결정 |
|---|---|---|---|---|
| V2-1 | HIGH | 등록 `reason` 에 `pair_mismatch`(챌린지는 유효한데 다른 `pair_id` 의 것) — 챌린지 형식에 `pair_id` 가 없어 만들 수 없었고 PI23 (a) 가 그것을 기대했다 | 지웠다. 서버는 토큰의 `pair_id` 로 챌린지 · 증명을 보고 틀리면 `bad_mac`(4.2 등록 증명 ①~⑥). PI23 (a) 의 그 경우는 `bad_mac` | 313 |
| V2-2 | HIGH | J8 류(`expired` · `reused` · `bad_mac`) 재시도가 끝없고 아무 데도 보이지 않았다 | 상태 파일 · 읽는 표 · 10분 반복 경고, `bad_mac` 세 번이면 J7(4.3). PI39 | 314 · 317 |
| V1-1 | HIGH | M5t-a 결정 246 · TD10 · TD28 · TD48 을 조용히 뒤집었고(283 · PI3), 짝지은 `auto` 워커 · 클라이언트 · doctor 가 묻는지 정하지 않았다. TD28 시험이 빨개진다 | §1 R7~R10, 4.3 `auto` 표 A1~A10, PR3 파일에 M5t-a 시험(F47). PI41 | 315 |
| V1-2 | HIGH | `rcm init worker` 가 `auto` 를 쓰고 `docs/configuration.md:1338` · `:1347` 의 흐름이 그대로라 283 이 기본 경로를 깼다. 템플릿 · 예시는 `tests/test_cli_init_worker.py:101-109` · `:133-140` 이 바이트 같음으로 잠근다. M3 는 이 Mac 만 셌다 | 처음 설정 정본을 `rcm pair` + `rcm setup --pair` 로, `rcm init worker` 는 미완성 틀(§1 R11~R13, 4.6). 템플릿 · 예시 · 잠금 시험은 PR3, 문서 흐름은 PR4 · PR5. 다른 머신 확인이 반영을 막는다(§6 17). PI42 | 316 |
| V2-3 · V1-6 | MED | 인증되지 않은 바이트(MAC 이 틀린 hello 의 200, 421) 하나로 종료 0 영구 정지. T1 · §9 의 「계속 있으면」 문구가 틀렸다 | J2 · J3 은 재시도 + 경고 + FAIL, 종료 0 은 J6 · J7 뿐. T1 · §9 문구 고침(서비스 거부만, 영구 정지 없음) | 317 |
| V1-5 | MED | 신원 정지 중 취소가 닿지 않는 것을 적지 않았다(`remote_worker.py:449-451`, `:187-188`) | F53 · F54, 4.3 상태 전이, §9 남는 위험, PI36 | 318 |
| V1-3 | MED | PI15 · PI23 은 PR2 인데 e2e 가 PR3 의 워커 쪽 `pair_proof` 를 필요로 했다 | 두 행을 PR2(HTTP 단위) · PR3(e2e)로 나눔 | — |
| V1-4 | MED | `rcm run` 의 기다림 단계 · `rcm top` 재연결 중 사칭의 종료 코드가 없었다 | 4.3 클라이언트 표: 무엇인가 한 뒤 J2 · J3 은 3(PLAN.md:118). PI44 | 319 h |
| V1-7 | MED | doctor 의 `server identity` 행을 PR3 에 줬는데 PR3 파일에 `doctor.py` · `core/doctor.py` 가 없었다(이제 dev 에 있다 — #268) | PR3 owns 에 `doctor.py` · `core/doctor.py` · `tests/test_doctor_cli.py`(F46 · F47) | — |
| V2-4 | MED | setup `--pair` 의 교환 오류(409 · 400 · 503 · 429 · 5xx) 종료 코드 · 문구가 없었고 setup 에 `--name` 이 없으며 보내는 `host_name` 의 출처가 없었다 | 4.6 교환 오류 표, `host_name` 출처(F60), `--name` 없음. PI43 · PI8 (b) 고침 | 319 c |
| V2-5 | MED | 익명 교환의 비문자열 · 너무 긴 · 홀로 선 서러게이트 `host_name`, 나쁜 `worker_nonce` · `proof` 의 상태 코드와 세기가 없었다(서러게이트는 500) | 창 조회 전 400 `bad_pair_request`, 세지 않음, `host_name` 64(4.4). PI40 · PI10 | 319 b |
| V2-6 | LOW | 쓴 챌린지 집합에 넣는 때가 없었다 | 모든 검사를 통과한 뒤에만(4.2 ⑥). PI23 (g) | 319 d |
| V2-7 | LOW | 「코드는 망으로 나가지 않는다」는 워커 쪽만 맞다. 다른 머신에서 친 `rcm pair` 는 201 본문으로 코드를 평문으로 받는다 | §0 · §1 · T4 (d) · 4.4 코드 노출 문구, `rcm pair` 경고. PI21 (d) | — |
| V2-8 | LOW | 교환 증명의 재사용(창이 닫힐 때까지 bearer)이 이름 붙은 위험이 아니었다 | T4 (c), §9 남는 위험. PI21 (c) | — |
| V2-9 | LOW | `auto` 예외가 상태 표 밖에 따로 있었다 | 판정 표 하나(J)로 — `auto` 후보의 J2 · J3 은 「그 후보가 아니다」일 뿐 | 317 |
| V2-10 | LOW | `GET /pair` 의 목록 한도(1시간 · 20개)가 막 끝난 60분 창을 빼서 「재시작」으로 보고될 수 있었다 | `closed_at` 기준 1시간 보존(4.4). PI11 | 319 f |
| V2-11 | LOW | M5t-b 는 알지만 짝짓기는 모르는 옛 워커가 복사한 짝 토큰을 들면 403 → 종료 2 → 10초마다 재시작 | 4.8 호환 행, `pair_proof_required` 의 `error` 문장 고정, §9 남는 위험 | — |
| V2-12 | LOW | client 창의 MAC 입력(`pool` · 프리셋 수가 없음 — 키 생략인가 null 인가, 정수 → 문자열) | 4.2.1: 키는 있고 값은 null, MAC 에는 빈 문자열, 정수는 10진. PI33 | 319 e |
| V1-8 · V2 제안 3 | LOW | 오프라인 추측 수치가 SHA256 의 CPU 기준이고 60분 창에서 약했다 | 코드 키 scrypt(M7 — 이 Mac 에서 잼). 남는 위험 문장을 M7 로 | 319 a |
| V1-9 | LOW | 변이 없는 규칙: 300 · 312 · `pair.key` 모드 거절 · `host_name` 정리 · 한 UPDATE 의 `--replace` | 변이 `tokenless-auto-client-follows` · `pair-restart-reported-unused` · `pair-key-mode-not-checked` · `pair-label-not-cleaned` · `pair-replace-two-statements` | — |
| V1-10 | LOW | PR4 가 `cli.py` 를 갖는데 M5t-b PR3 과의 순서가 없었다 | PR3 시작 조건에 M5t-b PR3 머지 — PR4 는 그 뒤(F35, §5) | — |
| V1-11 | LOW | PI20 의 「PLAN 「보안」 절이 적은 집합」은 없다 | §4.7 「익명 쓰기」 표와 탐침 목록(F57). PI20 | — |
| V1-12 | LOW | `os.link` 가 안 되는 파일 시스템(`FileExistsError` 아닌 `OSError`)의 길이 없었다 | 4.2 ⑦ 기동 거절, rename 으로 물러서지 않음. PI35 (e) | 319 g |
| V1-13 | LOW | `rcm pair` 는 admin 토큰을 보낸다 — 짝지은 클라이언트의 hello 관문이 여기에도 걸리는지 | 걸린다(같은 `Client`, 285). 4.6 `rcm pair` 행 · 클라이언트 표 | 285 |
| V1 drift | LOW | F41 의 `remote_worker.py:780-784` | `:785-787`(주석), `:354-360`(401 · 403) | — |

M5t-b 명세와 맞출 문장도 하나 남았다(Q-E′). M5t-b 2판은 아직 「MAC 이 틀린 hello 는 종료 0」이라고 적었다. 이 문장은 M5t-b
3판에서 고친다. 메인 세션이 317 을 M5t-b 쪽에 넘겼다(6차 위임 결정).

### 3판에서 시나리오 표를 바꾼 행

| 행 | 바꾼 것 | 이유 |
|---|---|---|
| PI1 | (a) 종료 0 → 재시도 · 10분 경고 · 상태 FAIL, 진짜 서버가 돌아오면 다시 붙음 | 317 |
| PI2 | 워커 종료 0 → 재시도 | 317 |
| PI8 | (b) setup 문장 — 이름을 모르는 응답 유실 | V2-4 |
| PI10 | 형식 오류를 404 에서 빼 400 으로(여섯 경우 + 400) | 319 b |
| PI11 | 닫힌 창 보존 경우, PR1 더함 | V2-10 |
| PI12 | `host_name` 65자 → 64자, 409 의 `name` | 319 b · i |
| PI15 | PR2 · PR3 으로 나눔 | V1-3 |
| PI17 | 상태 파일 · `rcm doctor --json` · 세션 id | 314 |
| PI19 | (a) J6 의 새 hello, (c) 종료 0 → 재시도, PR1 · PR3 더함 | 317 · V1-9 |
| PI20 | 대조 집합을 §4.7 표로 | V1-11 |
| PI21 | (c) 재생 · (d) 코디네이터 밖 `rcm pair`, M4 → M7 | V2-7 · V2-8 · 319 a |
| PI23 | (a) `reason` 을 셋으로, (g) 챌린지 태우기, PR2 · PR3 | 313 · V2-6 · V1-3 |
| PI32 | 종료 0 → 재시도 · FAIL · 다시 붙음 | 317 |
| PI33 | `pair_code_key` · client 응답의 null | 319 a · e |
| PI35 | (e) 하드 링크 | V1-12 |
| PI36 | 판정 표 J 로 다시, 취소 관찰 | 317 · 318 |
| PI39~PI44 | 새로 | 314 · V2-5 · 315 · 316 · V2-4 · V1-4 |
| 변이 | `identity-mismatch-retried` 를 지우고 `hello-mismatch-exits` 로, 23개 더함 | 317 · V1-9 · 위 행들 |
| 뺀 행 | 없음 | — |

### 1라운드에서 1판을 고친 것

두 격리 검증자(V1 · V2)의 1라운드 지적과 2판이 고친 것이다. 지적은 요지만 적었다. 고치지 않은 것도 적는다.

| id | 등급 | 1판 | 2판 | 결정 |
|---|---|---|---|---|
| V1-1 | MED | T6 가 「복제된 worker.toml」을 공격자 입력으로 두고 챌린지 증명으로 막는다고 적었다. 파일에는 토큰과 `pair_secret` 이 함께 있다. PI23 은 토큰만 시험 | T6 를 「토큰만 샌 경우는 막고, 파일 통째 복사는 신원 복제 = 로컬 침해로 남는 위험」으로 고침(0600 의 한계 포함). PI23 (f) 가 복사한 파일로 등록이 성공하고 펜싱됨을 고정. 남는 위험 표에 행 | 302 |
| V1-2 · V2-1 | MED | 교환 때 능동 사칭자 행이 없었다. `rcm setup --pair` 가 코드를 그대로 보냈고, 마지막 「hello 한 번」은 같은 서버가 방금 준 K 로 검사해 실패할 수 없었다 | 코드를 보내지 않는 교환(증명 · 두 nonce · 응답 MAC, 저장 전 검사) — T12 · 4.4 · PI34 · PI21. 「hello 한 번」은 「주소 확인」으로 이름과 뜻을 바꿨다(신원 검사가 아니라 `dialed` 규칙 통과 여부를 지금 알려 줌) — 4.6 | 301 |
| V1-3 · V2-2 | MED | Host 규칙이 스킴을 몰랐다(포트 없으면 `:80`). IPv4-mapped · scope id · 클라이언트 정규화가 없었다 | Host 헤더 대신 본문 `dialed`(스킴 포함 정규형, 양쪽 같은 함수 `canonical_dialed`). `https` 는 443. IPv4-mapped · zone id 처리. 서버가 IPv4 전용임을 측정(F38 · M5). PI22 (f) 에 `https` public_url 칸, PI33 에 정규화 표 | 308 |
| V2-3 | MED | MAC 바이트 인코딩이 고정되지 않았다(K 의 꼴, 정수 · 난수 글자 형태, hex 대소문자, 길이 검사, 미래 챌린지) | 4.2 의 인코딩 · 형식 검사 행과 4.2.1 「wire 예시」 소절. K 는 32바이트(hex 는 전달 형식), 정수는 10진, 난수는 소문자 hex, MAC 은 소문자 hex 만, 정규식, 미래 5초 넘으면 무효. 골든 시험 PI33(64자 hex 리터럴 없이, §8) | 308 · 311 |
| V1-6 · V2-4 | MED | `core/pairing.py` 에 시계 · `secrets` 가 들어갔다 | 4.2 의 나눔 표: 순수 함수는 `now` · 난수 바이트를 인자로, `secrets` · 시계 · 파일 · 창은 `pairing.py`(F42) | 311 |
| V2-5 | MED | (a) 키가 없고 짝 토큰이 있으면 조용히 새 키 (b) `O_CREAT`·`O_EXCL` 뒤 쓰기를 다른 쪽이 0바이트로 읽음 (c) `cannot start server` 로 가는 예외 꼴과 PR1 이 `cli.py` 를 갖지 않는 문제 | (a) 살아 있는 짝 토큰이 있으면 기동 거절 + 복구 · 재설정 안내 (b) 임시 파일 + `fsync` + `link` (c) `PairKeyError`(`StoreError` 하위)를 `serve()` 안에서 → `cmd_serve` 의 기존 처리로 종료 2, `cli.py` 무변경(F23). PI35 | 306 |
| V1-5 · V2-6 | MED | 익명 `POST /pair/exchange` 가 PLAN.md:243 「쓰기는 인증 필수. none 은 없다」를 뒤집는데 §1 에 없었다 | §1 「뒤집거나 좁히는 것」 표에 넣고 PLAN.md:243 개정을 PR0(이 명세 PR 의 마지막 커밋, E2)에서 한다 | 297 |
| V2-7 | MED | M5t-b 결정을 전제로 썼지만 문서로 확인하지 않았다 | `docs/m5tb-workplan.md`(PR #270)의 §4.2 · §4.3 · §4.7 · §4.9 · §4.10 · §5 · §11 을 F30~F37 로 인용. M5t-b §4.7 의 「M5t-c 도 종료 3」 문장이 303 과 어긋남을 찾아 Q-E 로 | — |
| V2-8 | MED | 0 · 1 · 8 · 9 를 친 사람이 5회 가운데 1회를 잃었다 | 보내기 전에 형식 검사 · 종료 2 · 실패로 세지 않음 · 바꿈 없음. 서버도 형식 틀린 증명은 세지 않음. PI38 | 307 |
| V2-9 | MED | CLI 표는 「서버를 잃으면 3」, PI11 은 「재시작이면 1」 — 모순. Ctrl-C 130 이 계약에 없었다 | 창 `id` 로 재시작을 알아본다. 재시작 뒤 창이 없을 때는 4차 위임 결정(312)대로 영속 상태가 완료나 미사용을 증명하지 못하면 3 이다. 만료 + 30초까지 못 닿아도 3 이다. 130 을 계약에 넣었다(4.6 · 4.7 · PI11) | 312 |
| V1-4 | MED | 종료 3 이 부모 명세 M8 과 충돌 | 확실한 신원 불일치 종료 0, 연결 실패 · 5xx · 503 은 프로세스 안 재시도, 프로토콜 불일치는 M5t-b 3. M8 을 좁힌다고 §1 · §9 에 적음. 분류 표(4.3)와 PI36 | 303 · 304 · 305 |
| V1-12 · V2-10 | LOW(V2-10) | hello 가 같은 세마포어를 지나 503 이 「검증 안 됨」으로 세어지고 heartbeat 이 멈춰 잡이 `lost` 가 될 수 있었다 | 503 은 재시도(신원 판단 아님). 확인된 뒤 503 은 다시 hello 를 부르지 않는다. M5t-b 에서 `worker_timeout` 은 레인 down 만, 잡은 유예(F37) — §9 행 · PI36 | 303 |
| V1-7 | MED | PI5(PR1)가 워커의 토큰 전송 0건을 단언했지만 워커 관문은 PR3. 변이 `pair-hello-mac-not-checked` 는 PR1 에 두고 PR3 의 PI1 을 빨갛게 한다고 적었다 | PI5 는 서버 쪽 · 순수 `verify_hello` 만 단언(워커 쪽은 PI1). 변이 표의 「빨개질 시험」은 그 PR 안의 시험만: `pair-hello-mac-not-checked` → PI5 · PI33(PR1), PR3 에 `pair-gate-ignores-verify-result` → PI1 을 새로 | — |
| V1-8 | MED | §5 에 M5t-a PR2 · PR3 과의 머지 순서가 없었다 | 구현 PR 시작 조건에 「M5t-a 전체(v0.11.0) 머지」. F9 에 자리가 옮겨질 수 있다고 적음. §2 · §5 · §6 | — |
| V1-9 | MED | PR0 가 헤드에 없었다(PLAN.md:629 「(명세 예정)」, `docs/m5n-workplan.md:650`, PLAN.md:753 의 244/245) | PR0 = 이 명세 PR, 편집은 머지 직전 마지막 커밋(집 관례 `docs/m5t-workplan.md:690`). 편집 목록 E1~E5(§5) | — |
| V1-10 | LOW | 「`server.py` 와 `cli.py` 는 각각 한 PR 만 고친다」가 표와 모순 | 문장을 「같은 물결 안에서 같은 `src/` 파일을 고치는 PR 은 없다」로 바꾸고 파일별로 어느 물결 · PR 인지 적음(§5) | — |
| V1-11 | LOW | PR3 의 시작 조건이 PR1 뿐이었는데 `remote_worker.py` 등록 본문을 M5t-b 도 고친다 | PR3 시작 조건에 M5t-b PR2 머지(`remote_worker.py` · `client.py` 의 owns, F35). PR1 · PR2 는 M5t-b PR1 머지 뒤 | — |
| V1-13 | LOW | hello MAC 이 챌린지를 덮지 않았다 | 덮는다: `pair_mac(K, "rcm-hello-v1", pair_id, nonce, dialed, challenge)`. PI33 · 변이 `pair-hello-challenge-unbound` | 308 |
| V1-14 | LOW | PI17 이 PR4 에만 있었다 | PI17 을 PR1(서버 로그 · `pair.key` 모드) · PR3(설정 파일 모드 · 워커 로그) · PR4(CLI 출력 · 입력)로 나눔. 변이 `pair-secret-logged` 는 PR1 | — |
| V1-15 · V2-16 | LOW | 「280~299 로 미리 나눴다」 — 결정 300 이 범위 밖 | 「M5t-b 256~279, M5t-c 280~3xx」로 고침(머리 인용) | — |
| V2-11 | LOW | 다시 찾기 후보 8개 상한 · 둘이 통과하면 「통과한 하나」가 정의되지 않음 | 정확히 하나일 때만 옮김, 둘 이상이면 옮기지 않고 로그. 8개 넘는 홍수는 서비스 거부만 — 남는 위험 표. T14 · PI4 (c) | 310 |
| V2-12 | LOW | hello 와 토큰 요청이 `urlopen` 으로 3xx 를 따라가며 `Authorization` 을 넘김 | 짝지은 머신은 따르지 않는다(F40 · M6 측정). T13 · PI37 | 309 |
| V2-13 | LOW | `TokenInfo` · `verify_token` 이 `pool` · `pair_id` 를 읽지 않음 | 칸을 더하고 SELECT 를 바꾼다(F2 · 4.5 · PI16 d, PR1) | — |
| V2-14 | LOW | `host_name` 이 로그 · 완료 줄 · `GET /pair` 에 정리 없이 나옴 | 정리한 값만 보이고 저장(`clean_pair_label`), 원문은 MAC 에만. PI12 | — |
| V2-15 | LOW | 옛 `rcm worker --check` 가 `pool` 없이 `spec()` 을 불러 기본 풀만 봄 | 호환 표(4.8)와 PI26 에 적음. 표시만 틀리고 받는 잡과 무관 — 고치지 않는다(옛 빌드라 고칠 수 없다) | — |

고치지 않은 지적은 V2-15 의 동작 자체 하나다. 옛 빌드 쪽 동작이라 이 명세가 바꿀 수 없어서 문서에만 적었다.

### 1판이 답한 넘겨받은 지적

`docs/m5t-workplan.md` §12 의 「M5t-c 가 반드시 다룰 지적」이다. 1판의 표를 옮기되 2판 설계로 답이 바뀐 세 행을 고쳤다.
3라운드 A-H3 · B-H2 와 2라운드 A-H8 행은 Host 판정을 `dialed` 판정(308)으로 고쳤다. 3라운드 B-M8 행에는 코드가 망으로
나가지 않는다는 것(301)을 더했다. 답하지 못한 id 는 없다.

| 라운드 | id | 요지 | 이 판이 답한 자리 |
|---|---|---|---|
| 3 | A-H3 · B-H2 | 정확한 자기 주소 목록 | 목록을 만들지 않고 `dialed` 를 MAC 에 넣고 소켓 주소 · `public_url` · 광고 이름 집합으로 판정(결정 308 · 290, §4.2 `dialed` 규칙 · F15 · PI5 · PI22) |
| 3 | 근본 원인 R1 의 예 | 보관함 Host 허용 목록을 서버 자기 주소 목록에 재사용 | 재사용하지 않는다 — 모든 사설 IP 를 받기 때문(F14 · 결정 290) |
| 3 | B-M3 | DHCP, `auto` 와 옛 토큰 | 옛 토큰 + `auto` 거절(283 · 285 · PI3), 짝지은 워커는 hello 를 통과한 주소로만 옮김(4.3 · 295 · PI4) |
| 3 | B-M4 | `tokens.pool` NULL | 284 · 4.5 · PI16 |
| 3 | B-M8 | 엿들은 교환 | 코드 한 번 · 코드는 망으로 나가지 않는다(301, PI21), hello nonce 매번 · 챌린지 한 번 · 120초(4.2 · PI5 · PI23), 교환 응답 엿보기는 남는 위험(282) |
| 3 | L2 | HMAC 인코딩 | 길이 접두 + 용도 라벨, 함수 하나 `pair_mac`(4.2) |
| 2 | A-H8 · B-H1 · B-H2 · B-M1 | hello 의 중계, 기존 토큰, 짝짓기 주소 | 중계: `dialed` 를 MAC 에 넣고 소켓 주소 · `public_url` · 광고 이름 집합으로 판정(308, PI5), 이름 URL 은 남는 위험(PI6). 기존 토큰: 283 · 285. 짝짓기 주소: `rcm pair` 가 찍은 URL 로만, mDNS 를 쓰지 않는다(4.4 화면 URL · PI22) |
| 2 | B-M11 | 인증 예외 라우트 | 정확히 둘, `/worker/*` 밖(297 · F7 · PI20 · PI29) |
| 2 | B-H8 · B-M3 | 클라이언트 토큰 | `rcm pair --client` · `rcm setup --role client --pair` · 285(PI24) |
| 2 | B-L5 · B-L7 | 창이 열렸는지 드러나는 응답, 워커의 기본 이름 | 같은 404 · 읽기 라우트와 TXT 불변(PI10), 호스트 이름 정리(4.4 · PI12) |
| 2 | B-H3 · B-H4 | 풀과 비밀이 필요한 프리셋 | `tokens.pool` 과 등록 검사 · spec 거르기(4.5 · PI13 · PI14), 비밀 프리셋은 결정 254(PI15) |
| 2 | A-M7 · B-M2 | 신원 비밀 표와 hello 를 넣는 PR | 신원 비밀 표를 두지 않는다(281 — 파생). hello 는 PR1 에, DB v27 도 PR1(§5) |
| 1 | A-M14 · B-H1 | 베낄 수 있는 `server_id`, 확인이 곧 토큰 유출, `rcm pair` 가 DB 를 여는지 | `server_id` 없음(290), hello 는 토큰 전 · 익명(4.3), `rcm pair` 는 HTTP(292 · PI28) |
| 1 | B-H2 · B-L3 | 코드 무차별 대입과 스키마, kind 마다 열린 창 수 | 50비트 · 창당 5 · 메모리 창(291 · M2 · PI9), kind 마다 하나(PI12) |
| 1 | A-L2 · A-L6 · B-M11 | TXT 에 더하는 것, 워커 이름 · 폐기 · 발급 범위 | TXT 불변(F12), `--name` · 정리 · `--replace`(4.4 · PI12 · PI19), 발급 범위는 280 |
| 1 | B-M2 · B-M4 · B-L4 | 물결 의존, 평문 전송과 `/worker/spec` env, README 발견 문장과 `data_dir` 초기화 뒤 복구 | 물결(§5), 평문은 282 · spec 은 294, README 는 TXT 불변이라 그대로 · 초기화 뒤 다시 짝짓기는 `docs/operating.md`(PR5 · PI29) |
| 1 | A-M11 · B-H3 | 창에서 워커의 풀을 정해 기본값에서 분배 | `--pool`, 없으면 `default`(4.4 · PI15 · PI25) |

### 3판 재료와 다르게 고른 것

| 3판 | 이 판 | 이유 |
|---|---|---|
| `POST /worker/hello` | `POST /pair/hello` | 클라이언트도 부른다(285). `/worker/*` 의 인증 먼저 규칙(F7)을 그대로 두려고 |
| 신원 비밀 원값을 DB `worker_identity` 표에 | `pair.key` 에서 파생, DB 에는 `pair_id` | 281(Q12) |
| `pair_windows` DB 표 | 메모리 | 291 |
| TXT 에 `server_id` | 더하지 않음 | 290, 결정 34 유지 |
| `rcm token add --worker` 가 신원 비밀도 출력 | 바꾸지 않음 | PI26(캡처 값 불변), 수동 토큰은 옛 토큰 규칙 |
| 신원 비밀 없는 기존 토큰은 처음 URL 에 머문다 | `auto` 면 거절, 명시 URL 은 오늘대로 | 283(N1: 첫 URL 고정은 이미 속았다면 공격자를 고정할 뿐) |

### 1판에서 시나리오 표를 옮기며 바꾼 행

시나리오 표는 설계를 보기 전에 따로 썼다. 1판이 그 표를 §7 로 옮기며 바꾼 행이다.

| 행 | 바꾼 것 | 이유 |
|---|---|---|
| PI1 | (a) 기동 · (b) 도는 중 다시 찾기로 나누고 (b) 는 종료가 아니라 「옮기지 않고 계속 시도」 | 도는 중 종료는 도는 잡을 죽인다(4.3). `(live)` 표시는 칸 가운데라 추적 도구가 실기로 세지 않아 pytest 행으로 두고 실기는 PI31 · PI32 로 뺐다 |
| PI3 | 후보(머묾 · 사람 확인 · 지금대로)를 283 · 285 의 거절로 | 위임 결정 N1 · N3 |
| PI5 | (d) 챌린지 재사용은 PI23 으로 | 증명 검사는 PR2 |
| PI6 · PI21 · PI24(d) · PI16 · PI18 · PI23 · PI25 | 「undecided」를 결정 282 · 284 · 281 · 287 · 286 으로 채움. PI24(d) 는 새 질문 Q-A 로 남겼다가 2차 위임 결정(2026-11-26)으로 채움 | 위임 결정 |
| PI9 | `failures` 열(DB) 대신 `rcm pair` 출력 · 서버 로그 · spy | 창이 메모리(291) |
| PI11 | DB 사본의 `expires_at` 대신 「재시작하면 창이 사라진다」와 `GET /pair` | 291 |
| PI12 | (a) 「거절」로 정함 | 4.4 |
| PI20 | 「실제 라우트 표에서 뽑는다」→ PLAN 「서버 API」 표 파싱 | 라우터가 if 사슬이라 표 자료구조가 없다(F7) |
| PI22 | 「LIVE_FORBIDDEN(실제 tailnet)」을 어디서 칸의 대역 설명으로 | 같은 뜻, 형식 |
| PI23 | `_owned_active` 응답을 「409/404」에서 「404 · 403 `not your job` · 409」로 | F11 |
| PI27 | 웹 UI CDP 부분을 PI32 로 | `(live)` 를 칸 끝에 두려고 |
| PI28 | v25 사본에 v26 사본을 더함 | M5t-b 가 v26 을 먼저 낸다(299) |
| PI31 · PI32 | 새로 더함 | 실기 행을 칸 끝 `(live)` 로 |
| 뺀 행 | 없음 | — |

### 2판에서 시나리오 표를 바꾼 행

| 행 | 바꾼 것 | 이유 |
|---|---|---|
| PI1 | (a) 종료 3 → 종료 0, (b) 는 `auto` 짝지은 워커로 한정 · 「새 claim 없이 다시 시도」 | 303 · F16 |
| PI2 | 워커 종료 3 → 0 | 303 |
| PI4 | (a) `auto` 로 한정, (c) 둘 통과 · 후보 9개 | V2-11 · 310 |
| PI5 | 워커 쪽 단언을 빼고 서버 · 순수 함수만, Host → `dialed` | V1-7 · 308 |
| PI7 | 「코드로 kind 를 바꿔」 → 「증명은 kind 를 덮는다」 | 301 |
| PI9 | 틀린 코드 → 형식은 맞는 틀린 증명 | 301 · 307 |
| PI10 | 형식이 틀린 증명을 경우에 더함(일곱 경우) | 307 |
| PI11 | 서버를 잃음(3) · Ctrl-C(130) · 창 id · 재시작 뒤 창이 없어도 3(4차) | V2-9 · 312 |
| PI12 | 정리한 `host_name` 만 보임, PR1 더함 | V2-14 |
| PI16 | (d) `TokenInfo` | V2-13 |
| PI17 | PR 을 PR1 · PR3 · PR4 로 나눔 | V1-14 |
| PI18 | 사본만 둔 서버는 기동 거절(PI35) | 306 |
| PI19 | (a) 워커 종료 0 · M5t-b 유예, (c) 키 재설정 | 305 · F37 |
| PI20 | PLAN.md:243 예외 문장은 PR0 이 쓰고 PR1 이 잠금 | V1-5 |
| PI21 | 코드가 요청 바이트에 0번 · 오프라인 추측 남는 위험 | 301 |
| PI22 | (f) `https` public_url, 단위는 PR1 | V1-3 · 308 |
| PI23 | (a) 경우 넷 더함(미래 챌린지 · 세션 묶음), (b) 등록 없는 claim 은 M5t-b 409, (f) 파일 복사 | 302 · F31 |
| PI24 | (c) `rcm wait` 종료 3 | 305 |
| PI26 | 짝지은 워커 ↔ 옛 서버는 재시도, 옛 `--check`, 경고 되풀이(4차) | 304 · V2-15 |
| PI32 | 종료 3 → 0 | 303 |
| PI33~PI38 | 새로 | V2-3 · 301 · 306 · 303 · V2-12 · V2-8 |
| 뺀 행 | 없음 | — |

PI36 은 2판에서 새로 더한 행이다. 4차 위임 결정에 맞춰 경고 되풀이(304)와 잡 비우기 중 로그(305)를 확인할 결과에 넣었다.

## 13. 구현하며 바뀐 것
