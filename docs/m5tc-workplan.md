# M5t-c 작업 명세: 짝짓기 · 서버 신원 보안

> 이 문서는 M5t-c 의 작업 명세 2판이다. 1판을 두 격리 검증자가 반박한 1라운드 지적을 모두 반영했다(§12). 다루는 것은 새
> 머신을 코디네이터에 붙이는 짝짓기와 서버 신원 보안이다. 출처는 넷이다. 아래에 그대로 옮기는 오너의 말,
> `docs/m5t-workplan.md` 의 결정 252~255, 같은 문서 §12 의 「M5t-c 짝짓기 · 서버 신원 보안」 표, 그리고 2026-11-26 의 오너
> 위임 결정 1~4차다. 위임 결정은 메인 세션이 Codex 와 논의해 정했다. Codex 원문은 레포 밖에 두었다.
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
> 결정 번호는 280 부터 쓰고 이 판은 312 까지 쓴다. M5t-b 는 256~279 를 쓴다(`docs/m5tb-workplan.md` 머리 인용). 1판은
> 「280~299 를 M5t-b 와 겹치지 않게 미리 나눠 두었다」고 적었는데 틀린 문장이었다. 300 이상도 M5t-c 의 번호다(§12 V1-15 ·
> V2-16). 3차 위임 결정 301~303 은 1라운드 지적에 대한 답이다. 교환 때 코드를 보내지 않는다(301). 통째로
> 복사한 `worker.toml` 은 남는 위험으로 둔다(302). 확실한 신원 거절은 종료 0 으로 끝낸다(303). 4차 위임 결정은 이 판의
> 304~312 를 확정했다. 그 가운데 304 와 312 는 Codex 의견대로 바꿨다.
>
> DB 판은 v27 이고 M5t-b 의 v26 뒤에 온다. 핀 sha 는 `fdf196d4ef682612857fb12df9162f67545b63f0`(1판과 같다)이고 이 문서의
> `file:line` 은 모두 이 sha 의 것이다. 명세 브랜치 head `fb73ed67b1ad1ddb9af805d0d388008867cd7c9c` 는 핀에서
> `docs/m5tc-workplan.md` 하나만 더했으므로 `src/` · `PLAN.md` · `docs/` 의 `file:line` 이 두 sha 에서 같다. M5t-b 의 사실은
> PR #270 의 `docs/m5tb-workplan.md`(그 PR head `804c17d95e644e82ad0a3351b4e01a5d50b9413a`)를 절 번호로 인용한다. 같은
> 사실은 §3 에 한 번만 적고 다른 절은 F 번호로 가리킨다. M5t 전체를 담았던 3판의 근본 원인 R5 를 되풀이하지 않으려는
> 것이다.

## 0. 한 줄

코디네이터는 잡을 받아 다른 머신에 나눠 주는 rcm 서버다. 거기서 사람이 `rcm pair --worker`(또는 `--client`)로 짧은
짝짓기 창을 열면 화면에 코드와 URL 이 찍힌다. 새 머신은 `rcm setup --role worker --pair` 에 그 URL 과 코드를 넣는다.
코드는 망으로 나가지 않는다. 새 머신은 코드로 만든 증명만 보낸다. 서버의 응답이 같은 코드로 만든 MAC 을 실었을 때만
토큰과 풀, 서버 신원 비밀 `pair_secret` 을 저장한다(301). 짝지은 머신은 토큰을 보내기 전에 매번 `POST /pair/hello` 로
상대가 자기와 짝지은 그 서버인지 확인한다. 기동할 때와 연결이 끊긴 뒤, 다시 찾은 주소로 옮길 때가 모두 그렇다. 확인이
안 되면 토큰도 요청도 보내지 않는다. 불일치가 확실하면 워커는 「다시 짝지으라」를 남기고 종료 0 으로 끝나며 서비스는
워커를 되살리지 않는다. 확실한 불일치는 MAC 이 틀린 경우, 서버가 그 주소를 자기 것이 아니라고 답한 경우, 확인된 서버가
토큰을 모른다고 답한 경우다. 연결 실패와 5xx, 503 은 신원 판단이 아니므로 워커는 프로세스 안에서 물러서며 다시
시도한다(303). 워커 토큰에는 풀을 묶어 등록과 `/worker/spec` 을 그 풀로 제한한다. 짝지은 워커가 등록해 세션을 열려면
신원 증명이 있어야 한다. 설계는 §4.1 의 위협 모델에서 나온다. 이 설계가 막지 않는 것은 LAN 엿보기와 이름 URL 중계, 같은
IP 가로채기, `worker.toml` 통째 복사다. 넷은 남는 위험으로 적는다(282 · 302).

## 1. 출처와 오너 결정

### 요구와 이 명세가 정하는 것

| 요구(출처) | 이 명세가 정하는 것 | 결정 |
|---|---|---|
| 「다른 rcm 제어하는곳에서 이걸 연결하면 거기에서 알아서 분배」(2026-11-24) | 연결 = 사람이 연 짝짓기 창 + 코드 한 번. 창에서 정한 풀(기본은 기본 풀)로 바로 분배가 일어난다. 코드 입력 뒤 두 번째 승인은 없다 | 280 · 286 · 294 |
| 「Rcm워커 다 한 다음에 어떻게 알려주는거까지」(2026-11-24) | 짝짓기가 끝나면 양쪽이 한 줄씩 말한다. 코디네이터: 누가 · 어디서 · 어느 풀로 붙었는지와 폐기 명령. 워커: 붙었다 · 풀 · 받을 프리셋 수 · 결과는 코디네이터로 가고 요청자의 `rcm wait` 가 받는다. 결과 알림 자체는 M5t-a PR5 · M5t-b 범위 | 286 · 296 |
| 「민감데이터나 API 키 등 그런것들은 없어야해 … public」(2026-11-25) | 코드 · 토큰 · `pair_secret` · `pair.key` 를 어떤 출력 · 로그 · 응답에도 싣지 않는다(PI17). 코드는 망으로도 나가지 않는다(301 · PI21 · PI34). 시험 값은 실행 중에 만든다. §8 | 291 · 296 · 301 |
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

### 이 명세가 뒤집거나 좁히는 것

1라운드 지적(V1-5 · V2-6)으로 더한 소절이다. 이미 적힌 문장 가운데 이 명세가 뒤집거나 좁히는 것과, 그 문장을 언제
고치는지 적는다.

| 무엇 | 지금 문장(출처) | 이 명세 | 언제 고치나 | 결정 |
|---|---|---|---|---|
| PLAN 「보안」 쓰기 인증 | 「**쓰기(`POST /jobs`·업로드·취소)는 인증 필수.** `none` 은 없다.」(PLAN.md:243, 2026-09-04 오너 결정) | 익명 POST 둘을 예외로 둔다: `POST /pair/hello`(상태를 바꾸지 않는 신원 증명, Store 를 부르지 않는다)와 `POST /pair/exchange`(사람이 연 창의 한 번 쓰는 코드가 인증을 대신한다). 그 밖의 쓰기는 그대로 인증 필수 | PR0(이 명세 PR 의 마지막 커밋)이 PLAN.md:243 에 예외 문장을 더한다 — PR0 와 PR1 사이에 PLAN 과 명세가 어긋나지 않게. PR1 의 문서 잠금이 PI20 집합과 대조 | 280 · 297 |
| M5n §13 「안 만드는 것」 | 「토큰 자동 발급 · 자동 가입. 토큰은 사람이 옮긴다(보안 결정 유지).」(`docs/m5n-workplan.md:650`) | 사람이 연 짧은 수명 · 한 번 쓰는 창 안에서만 허용 | PR0 이 그 줄 끝에 「2026-11-26 M5t-c 결정 280 으로 개정 — 사람이 연 짝짓기 창 안에서만」을 단다. 같은 절의 자동 재실행 금지(`:652`)는 유지 | 280 |
| 부모 명세 M8 | 「등록 전 프로토콜 확인, 서비스가 되살리지 않는 종료 코드」(`docs/m5t-workplan.md:1088`, M5t-b 로 넘긴 지적) | 「되살리지 않는 종료(0)」는 펜싱(M5t-b)과 확실한 신원 거절(이 명세)로 좁힌다. 연결 실패는 종료가 아니라 프로세스 안 재시도. 프로토콜 불일치는 M5t-b 대로 3(되살림) | 부모 명세는 고치지 않는다(머지돼 얼었다). 이 명세 §9 · §12 에 적는다 | 303 |
| M5t-b 명세 §4.7 의 종료 3 문장 | 「M5t-c 도 워커 종료 3 을 쓴다 … 종료 3 은 두 경우를 뜻한다. 하나는 이 명세의 프로토콜 불일치이고 다른 하나는 M5t-c 의 서버 신원 미확인이다」(`docs/m5tb-workplan.md` §4.7) | M5t-c 는 종료 3 을 쓰지 않는다. 신원 미확인은 종료 0(확실) 또는 재시도(그 밖) | 메인 세션이 M5t-b 2판(PR #270)에서 그 문장을 고친다. #270 이 먼저 머지되면 M5t-c PR0 이 §12 에 「M5t-b §4.7 의 그 문장은 303 으로 대체」를 적는다(Q-E, 4차 위임 결정) | 303 |
| 1판의 결정 295 「기동 실패는 종료 3」 | 1판 §11 295 | 303 으로 개정 | 이 판 | 295 · 303 |

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

이 판은 303 을 §4.3 의 응답 분류 표로 구체화했다. 「MAC 이 틀림」은 hello 200 의 형식이 맞는데 `mac` 이 어긋난 경우다.
「서버가 짝지은 적 없다고 답함」은 hello 를 통과한 서버가 토큰에 401 을 준 경우(폐기나 교체)이거나 등록 증명이
`pair_mismatch` 로 거절된 경우다. hello 자체는 상태가 없어서 `pair_id` 가 있는지 말하지 않는다(4.2). hello 421, 곧 그
주소는 이 서버가 아니라는 답도 확실한 거절로 본다. 이 세 해석은 결정 305 로, 「그 밖의 응답」의 처리는 결정 304 로 두었고
4차 위임 결정이 둘을 확정했다.

### 오너 위임 결정 4차

2판 재료가 남긴 질문에 대한 결정이다(2026-11-26). §11 의 304~312 를 확정했다. 304 와 312 는 Codex 의견대로 바꿨다.
바뀐 내용은 §4.3 · §4.6 · §4.7 · §4.8 · §7 · §9 · §11 에 반영했다.

| # | 질문 | 결정 |
|---|---|---|
| 304 | 303 이 분류하지 않은 hello 응답(3xx · 404 · 405 · 다른 4xx · 깨진 200 · `v` ≠ 1) | 바꾼다. 프로세스 안 재시도(토큰 · 일 0)는 둔다. 「주소 · 종류마다 로그 한 줄」은 영원히 호환되지 않는 서버를 숨기므로 속도를 제한한 경고를 주기적으로 되풀이하고 `rcm worker --check` 와 서비스 상태(doctor)에 「서버 신원 미확인」 상태를 드러낸다 |
| 305 (a)~(d) | 확실한 불일치의 범위, 도는 잡 처리, 짝지은 워커의 401 은 종료 0 · 옛 워커는 2, 클라이언트 종료 코드 | 모두 그대로 둔다. 다만 (b) 의 잡 비우기 동안 기존 시한 · 취소 집행은 그대로이고 신원 때문에 멈춘 상태를 계속 눈에 띄게 로그에 남긴다 |
| 306 | `pair.key` 가 없고 짝지은 활성 토큰이 있으면 기동 거절, 재설정은 `rcm token revoke` | 그대로 확정 |
| 307~311 | 코드 알파벳 사전 검사 · MAC 에 넣는 `dialed` 필드 · 3xx 를 따르지 않음 · 다시 찾기 후보는 정확히 하나 · 순수 `core` | 그대로 확정 |
| 312 | `rcm pair` 종료 코드 | 바꾼다. 재시작 뒤 창이 없다는 것은 쓰이지 않았다는 증거가 아니다(재시작 직전에 끝났을 수 있다). 창 id 와 이어진 영속 상태가 완료나 미사용을 증명하지 않으면 3 이다. 그 경쟁을 1 로 보고하지 않는다 |
| Q-E | M5t-b §4.7 의 「M5t-c 도 종료 3」 문장 | M5t-b 2판에서 고친다. #270 이 먼저 머지되면 M5t-c §12 에 303 이 그 문장을 대체한다고 적는다 |

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

## 2. 범위

| 누가 | 하는 일 |
|---|---|
| rcm(이 명세 PR0~PR6) | PR0: 명세 + PLAN.md · M5n §13 편집. 서버: `pair.key` · 짝짓기 창(메모리) · `/pair*` 라우트 · DB v27(토큰 칸) · 등록의 풀 · 증명 검사 · `/worker/spec` 풀 거르기. 워커 · 클라이언트: `pair_secret` 설정 키 · 토큰 전 hello · 응답 분류와 종료 코드 · 리디렉트 안 따름 · 옛 토큰 + `auto` 거절 · `rcm pair` · `rcm setup --pair` · `rcm token list` 열 · 문서 · 실기 |
| M5t-a(다른 명세, `docs/m5t-workplan.md`) | **M5t-a 전체(PR1~PR7, v0.11.0)가 dev 에 머지된 뒤** 이 명세의 구현 PR 이 시작한다(V1-8). 이유: M5t-a PR2(#268, `rcm doctor` · `worker --check` 두 줄 · `/worker/spec` 키 · `NO_FOLLOW_COMMANDS` 의 `doctor`)와 PR3(`cli.py` · `capabilities.py`, `rcm check` · `lanes = 0`)이 이 명세 PR2 · PR3 이 고치는 `worker_spec` · `_worker_check` · `cmd_check` 와 같은 함수를 고친다(`docs/m5t-workplan.md:692-693`). PR4(setup)는 이 명세 PR4 의 바탕이다(F26). #268 의 동작은 사실로 인용하지 않는다 |
| M5t-b(PR #270, `docs/m5tb-workplan.md`) | DB v26 · 세션 · 펜싱 · 프로토콜 2 · 종료 0/3(M5t-b PR1), 로그 오프셋 · 보고 큐(M5t-b PR2, `remote_worker.py` · `client.py`), 화면 사유(M5t-b PR3). 이 명세 PR1 · PR2 는 M5t-b PR1 머지 뒤, PR3 은 M5t-b PR2 머지 뒤 시작한다(F35) |
| 하류 앱 프로젝트 | 바꿀 것 없음. 명시 URL 의 옛 워커는 그대로 돈다 |
| 메인 세션 | 운영 설정 읽기 전용 확인(Q-B, 이 Mac 은 2026-11-26 확인 끝, 다른 머신은 §6) · M5t-b 명세 §4.7 의 종료 3 문장 맞추기(Q-E) · 결정 페이지 · PR0 마지막 커밋(PLAN 편집) · 릴리스 · 운영 반영 |
| 오너 | 위임(2026-11-26). 운영 반영은 현행 규칙 |

## 3. 실측과 코드 제약

핀 sha 는 `fdf196d4ef682612857fb12df9162f67545b63f0` 이다. 다른 절은 이 표의 F 번호로 사실을 가리킨다. F1~F29 는 1판의
사실이고 그 가운데 F2 · F4 · F9 · F10 · F16 · F19 · F21 · F22 · F23 은 이 판에서 고쳤다. F30~F37 은 M5t-b 명세에서
옮긴 사실이고 F38~F44 는 이 판에서 새로 확인한 사실이다.

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
| F10 | 오늘의 등록: `pool` 은 본문에서(없으면 `default`), 버전 409 가 먼저, 그다음 `mark_lost_for_worker` 로 그 이름의 도는 잡을 `lost`. **M5t-b 가 이 순서를 바꾼다**(F30): 「등록이 그 이름의 활성 잡을 전부 닫는다」는 없어지고 `previous_sessions` 의 잡만 닫는다 | `remote_workers.py:377-434`(`mark_lost_for_worker` 호출 `:404`), `docs/m5tb-workplan.md` §4.2 | 증명 · 풀 검사 자리는 M5t-b 순서로 적는다(F30). `mark_lost_for_worker` 는 자리 표시로 쓰지 않는다 |
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
| F30 | M5t-b 의 register 순서(한 `BEGIN IMMEDIATE`): 1 본문 모양 400 → 2 버전 409(`server_version`) → 3 세션 칸 없음 409 `protocol_required` → 4 이미 펜싱된 세션 409 `session_fenced` → 5 현재 세션이면 멱등(워커 행 upsert 만) → 6 새 세션이면 `previous_sessions` 의 busy 잡을 `lost` 로 닫고 현재 세션을 펜싱하고 행을 넣고 능력 보고를 지운다. 「M5t-c 는 등록에 `pair_proof` 검사를 더한다 … 세션 넣기, 펜싱, 능력 보고 지우기, 잡 닫기보다 먼저 … 버전 검사와 프로토콜 검사는 부작용이 없으면 `pair_proof` 보다 앞서도 된다」 | `docs/m5tb-workplan.md` §4.2 | 증명 검사 · 풀 검사는 3 다음, 4 앞(287 · 294). 증명 실패는 펜싱 · 잡 닫기 · 행 쓰기 0 |
| F31 | M5t-b 세션: 워커 프로세스가 시작할 때 `secrets.token_hex(16)` 하나, 모든 레인이 같은 값. spec 을 뺀 모든 `/worker/*` 요청이 세션을 싣는다(본문 `worker_session_id`, tree · artifacts · log 는 헤더 `X-RCM-Worker-Session`). 표에 없는 세션은 409 `not_registered`, 펜싱된 세션은 409 `session_fenced` | `docs/m5tb-workplan.md` §4.2 · §4.10, 결정 260 · 265 | 등록 증명이 `worker_session_id` 를 묶는다(엿본 증명을 다른 세션으로 못 쓴다). 토큰만 훔친 쪽은 등록(증명)을 못 하므로 claim · heartbeat 도 못 한다 — 1판 T6 의 「등록 없이 claim · heartbeat」 남는 위험이 없어진다(현재 세션 id 를 엿본 T4 는 남는다). `/worker/spec` 은 세션이 없어 토큰만으로 읽힌다(묶인 풀만, 294) |
| F32 | M5t-b 종료 코드(결정 258): 펜싱 0, 프로토콜 불일치 3, 그 밖은 오늘대로(0 멈춤 · 2 등록 막힘 · 1 exec 실패) | `docs/m5tb-workplan.md` §4.7 · §4.10, §11 258 | 303 의 종료 0 은 같은 「되살리지 않음」 뜻. 3 은 M5t-b 몫으로만 남는다 |
| F33 | M5t-b §4.7 은 「M5t-c 도 워커 종료 3 을 쓴다 … 서버 신원 미확인」이라고 적었다(2차 Q-C 를 따른 문장) | `docs/m5tb-workplan.md` §4.7 | 303 이 대체한다 → 질문 Q-E(§1 뒤집는 것 표) |
| F34 | M5t-b DB v26(PR1): 새 표 `worker_sessions`, `jobs` 칸 여섯. 「M5t-c 는 v27 이다」 | `docs/m5tb-workplan.md` §4.9 | v27 은 v26 위(299) |
| F35 | M5t-b PR 표: PR1 owns `store.py` · `remote_workers.py` · `server.py` · `capabilities.py` · `config.py` · `client.py` · `remote_worker.py` · `cli.py`(cmd_worker) 외, PR2 owns `store.py` · `remote_workers.py` · `remote_worker.py` · `client.py` · `server.py`(log 라우트 등), PR3 owns `cli.py`(wait · logs 줄) 외. 「M5t-c 의 register 변경과 DB v27 은 PR1 뒤에 온다」 | `docs/m5tb-workplan.md` §5 | 이 명세 PR1 · PR2 의 시작 조건 = M5t-b PR1 머지. PR3(`remote_worker.py` · `client.py`)의 시작 조건 = M5t-b PR2 머지(V1-11). PR1(`store.py` · `server.py`)이 M5t-b PR2 와 동시에 돌면 M5t-b PR2 먼저 머지 |
| F36 | M5t-b 의 새 워커는 등록 전에 `GET /worker/spec` 의 `worker_protocol` 을 보고, 없으면 `/api/health` 의 `version` 을 본다(프로토콜 확인) | `docs/m5tb-workplan.md` §4.7, 결정 270 | 짝지은 워커는 hello 가 그 둘보다 먼저다(둘 다 토큰을 싣는다 — F17 · F19) |
| F37 | M5t-b: heartbeat 이 끊겨 `worker_timeout_seconds` 가 지나면 레인은 down 만, 도는 잡은 `disconnect_grace_seconds`(기본 600) 동안 유예 뒤 `lost`. 보고 큐는 디스크(`outbox.json`) | `docs/m5tb-workplan.md` §4.3 · §4.10, 결정 263 · 266 · 272 | hello 가 503 으로 막혀 heartbeat 이 멈춰도 잡은 `worker_timeout` 에 `lost` 가 되지 않고 유예를 받는다(V2-10). 확인되지 않은 서버에 보고를 보내지 않아도 보고는 큐에 남는다 |
| F38 | 서버는 IPv4 전용: `RcmHTTPServer` 는 `ThreadingHTTPServer` 의 기본 `address_family`(AF_INET)를 그대로 쓴다. `LOOPBACK_BINDS` 에는 `"::1"` 이 있지만 그 bind 는 열리지 않는다 | `server.py:6468-6486`, `config.py:51`, 측정 M5 | 소켓 로컬 주소는 언제나 `a.b.c.d` — IPv4-mapped(`::ffff:…`) · scope id 는 오늘 나오지 않는다. 그래도 정규화 함수는 둘 다 처리한다(장래 dual-stack 대비, PI33). IPv6 주소로 닿는 길은 `public_url` 뿐 |
| F39 | 동시 처리 상한: SSE 를 뺀 모든 요청이 `self.server.slots`(BoundedSemaphore, `max_concurrent_requests`)를 비차단으로 잡고, 못 잡으면 503 `too many concurrent requests` | `server.py:5639-5641`, `:6490` | hello 도 같은 상한을 지난다. 503 은 신원 판단이 아니라 재시도(303, V2-10) |
| F40 | 리디렉트: `Client` 는 기본 `follow_redirects=True` 로 `urllib.request.urlopen` 을 쓴다. `download_tree` · `download_bundle` · `events` 는 언제나 `urlopen`. CPython `HTTPRedirectHandler.redirect_request` 는 `content-length` · `content-type` 만 빼고 헤더를 새 요청에 옮긴다 — `Authorization` 이 리디렉트 대상 호스트로 간다. 따르지 않는 오프너는 이미 있다(`_NO_REDIRECT`, `follow.no_redirect_opener`) | `client.py:476-483`, `:486-511`, `:140`, `:589`, `:910`, `follow.py:648-656`, 측정 M6 | 짝지은 머신은 hello 와 토큰을 싣는 모든 요청에서 리디렉트를 따르지 않는다(309, V2-12). 옛 머신은 그대로 |
| F41 | 오늘 워커는 등록 401 · 403 을 `WorkerExit(2)` 로 끝낸다. 주석: 종료 0 이면 launchd · systemd 가 되살리지 않아 조용히 멈추므로 2 를 쓴다 | `remote_worker.py:354-363`, `:780-784` | 옛 토큰 워커는 그대로 2. 짝지은 워커의 「확인된 서버의 401」만 0(305) — 조용히 멈추지 않게 종료 전에 「다시 짝지으라」 한 줄 |
| F42 | PLAN: 「core/ ── 순수: I/O 도 시계도 안 본다. now 는 인자」 · 「순수 계층(core/)은 I/O 도 시계도 안 본다」 | `PLAN.md:539`, `:828` | `core/pairing.py` 는 `now` · 난수 바이트를 인자로 받는다. `secrets` · `time` · 파일 · 창 상태는 `src/remote_ci_monitor/pairing.py`(V1-6 · V2-4) |
| F43 | PLAN 「보안」 쓰기 인증 문장 | `PLAN.md:243` | §1 뒤집는 것 표 · PR0 |
| F44 | PLAN 의 M5t-c 항목은 「(명세 예정)」, 결정 색인은 「244 · 245 는 M5t-c 명세에 실을 예정이다. 249 는 두 명세에 나눠 실을 예정이다」. 부모 명세는 244(짝짓기 창과 서버 신원) · 245 의 풀 묶기 · 249 의 신원 · 창 · `tokens.pool` 부분을 M5t-c 로 넘겼다 | `PLAN.md:629`, `:753`, `docs/m5t-workplan.md:1045`(「옮긴 결정」 행), `:1109`(§12 M5t-c 표 「다루는 것」) | PR0 편집 목록(§5). 244 → 280 · 281 · 290 · 291, 245 의 풀 묶기 → 284 · 294, 249 의 M5t-c 부분 → 293. 옛 번호는 다시 쓰지 않는다 |

측정한 값은 따로 둔다. M1 과 M4 는 개발 머신에서 잰 값이고 운영 머신의 값이 아니다. M3 의 다른 머신 칸은 아직 재지
않았다. M4 의 GPU 속도는 잴 장비가 없어 재지 않았다.

| # | 값 | 어떻게 쟀나 | 설계에 미치는 것 |
|---|---|---|---|
| M1 | 워커 비밀 파생(HMAC-SHA256) + 응답 MAC 한 번 = 1.88 µs | 개발 머신(arm64, Python 3.11.15)에서 `timeit` 100,000회. 운영 머신 값 아님 | 익명 hello 를 DB 없이 매번 계산해도 비용이 작다 |
| M2 | 코드 공간 32^10 = 2^50, 창당 온라인 시도 5번 → 한 창의 온라인 추측 성공 확률 = 5/32^10 ≈ 4.4e-15 | 계산(`5/32**10` = 4.440892098500626e-15) | 시도 한도 5 와 10자 코드(291) |
| M3 | 운영 워커 · 클라이언트 중 `server = "auto"` + 옛 토큰의 수. 이 Mac: 0. 다른 머신: — | 메인 세션이 이 Mac 의 운영 설정을 읽기 전용으로 확인(2026-11-26, Q-B). 다른 머신은 §6 | 있으면 반영 전에 다시 짝짓거나 명시 URL |
| M4 | 관찰한 교환 증명으로 하는 오프라인 추측: 한 번(SHA256(code) + HMAC-SHA256) = 1.08 µs, 초당 923,211번(한 코어). 전체 공간 2^50 = 38.7년. 창 수명 10분 안에 공간 절반을 훑으려면 초당 약 9.4e11번(이 속도의 코어 약 100만 개). GPU 속도 — (재지 않음, 잴 장비 없음) | 개발 머신 arm64, Python 3.11.15, `timeit` 200,000회 | 301 의 남는 위험 문장의 숫자. 창이 닫히면(쓰임 · 만료 · 5번 실패) 맞힌 코드는 쓸모없다 |
| M5 | `RcmHTTPServer.address_family` = 2(AF_INET). `RcmHTTPServer(("::1", 0))` → `socket.gaierror` [Errno 8]. `0.0.0.0` 에 묶은 서버가 받은 연결의 `getsockname()` = 클라이언트가 접속한 로컬 IPv4(`127.0.0.1`, 서버 포트) | 개발 머신 `.venv` 에서 직접 만들어 확인(포트 0, 루프백) | F38 |
| M6 | `HTTPRedirectHandler.redirect_request` 소스: `CONTENT_HEADERS = ("content-length", "content-type")`, `newheaders = {k: v for k, v in req.headers.items() if k.lower() not in CONTENT_HEADERS}` | `.venv` Python 3.11.15 에서 `inspect.getsource` | F40 |

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

2판은 T1 · T4 · T6 을 고치고 T12 · T13 · T14 를 더했다. T5 의 「M5t-c 가 하는 것」 칸에는 코드가 망으로 나가지
않는다는 것(301)을 더했다. T2 의 Host 판정은 이 판의 `dialed` 판정(308)으로 고쳐 적었다.

| # | 공격자 위치 | 노리는 것 | 오늘(F) | M5t-c 가 하는 것 | 남는 위험 | 결정 | 시나리오 |
|---|---|---|---|---|---|---|---|
| T1 | 같은 망, mDNS 에 먼저 답하거나 설정 URL 을 차지한 위장 서버 | S1 · S2 · S3 | `auto` 워커 · 클라이언트가 찾은 서버에 토큰을 보내고 argv · wheel 을 실행한다(F16 · F17 · F18) | 짝지은 머신은 토큰 전에 hello. MAC 이 틀리면 토큰 0 · 워커 종료 0(기동) · 클라이언트 종료 2. 연결 실패 · 5xx 는 토큰 0 으로 다시 시도. 옛 토큰 + `auto` 는 거절 | 명시 URL 의 옛 토큰 머신은 그 URL 을 믿는다(오늘과 같다). 위장 서버가 계속 있으면 워커는 멈춰 있다(서비스 거부) | 283 · 285 · 290 · 295 · 303 | PI1 · PI2 · PI3 · PI24 · PI36 |
| T2 | 같은 망, 다른 IP 에서 hello 를 진짜 서버로 중계 | S1 · S2 | — | `dialed` 를 MAC 에 넣고, 진짜 서버는 소켓 주소 · `public_url` · 광고 이름 집합으로 판정(308) | 이름 URL(`<host>.local` · `public_url` 이름)은 이름을 가로챈 중계가 통과 | 282 · 290 · 308 | PI5 · PI6 |
| T3 | 같은 IP 를 차지(ARP 스푸핑, DHCP 로 옛 주소를 받은 장치) | S1 · S2 | 끊긴 뒤 같은 URL 로 바로 토큰 | 연결 실패 뒤 첫 요청은 hello. DHCP 로 주소를 받은 장치는 MAC 을 못 만든다 | ARP 스푸핑이 연결 실패 없이 요청 사이에 끼어들면 못 막는다(요청 서명 없음) | 282 · 295 | PI4 |
| T4 | 같은 망의 수동 엿보기 | S1 · S5 · S6 | 평문(결정 5) | 코드는 망으로 나가지 않는다 — 요청은 코드 키 증명뿐(301). hello nonce · 챌린지는 한 번 | (a) 교환 응답의 토큰 · `pair_secret` 과 요청의 토큰 · 세션 id 를 엿보면 사칭 가능 (b) 엿본 교환 증명으로 창이 열린 동안 오프라인 추측 가능(M4: 한 코어 38.7년, 10분 안에 절반은 약 100만 코어 — GPU 는 재지 않음). 맞히면 그 창이 아직 열려 있을 때만 쓸모 있다. LAN · `0.0.0.0` 경고와 Tailscale 권고 | 282 · 301 | PI21 |
| T5 | 같은 망의 익명 클라이언트(코드 추측 · 정찰 · 방해 · 과부하) | S5 | 창 없음 | 50비트 코드(코드는 망으로 나가지 않는다, 301) · 창당 5번 · 모든 실패에 같은 404 · 읽기 라우트와 TXT 에 창 표시 없음 · 본문 1 KiB · 동시 상한 · hello 는 Store 를 부르지 않는다 | 틀린 코드 5번으로 창을 닫는 방해 | 288 · 291 | PI9 · PI10 · PI20 |
| T6 | 탈취한 worker 토큰(로그 · `ps` · 환경 변수 유출) | 정당한 워커의 잡과 세션, 다른 권한 | 같은 이름 재등록이 정당한 워커의 잡을 `lost` 로 닫는다(F10) | 짝지은 워커의 등록(세션 열기)은 챌린지 증명(세션 id 를 묶음). 증명 없이는 세션이 없어 claim · heartbeat 도 409 `not_registered`(M5t-b, F31). 다른 주소 재등록을 서버 로그에. 클라이언트 API 403 · 남의 잡 403(오늘) | 토큰만으로 `/worker/spec`(묶인 풀의 프리셋 · env)을 읽는다. 옛 명시 토큰은 토큰만(증명 없음). **`worker.toml` 통째 복사(토큰 + `pair_secret`)는 워커 신원 복제 = 로컬 침해로 보고 막지 않는다** — 복사한 쪽은 증명을 만들어 등록하고 정당한 세션을 펜싱한다. 0600 은 다른 보통 사용자만 막는다(소유자 · root · 악성코드 · 백업 도난은 못 막는다) | 287 · 302 | PI23 |
| T7 | 한 창이 주는 권한을 넘기기 | admin · client 권한 | — | 창의 kind 만 준다. admin 창은 없다 | 없음 | 280 · 291 | PI7 |
| T8 | 사람 실수: 코드를 엉뚱한 머신에 넣음 | S2 · S4 | — | 양쪽 완료 줄 · `rcm token list` 의 `paired from` · 폐기 한 번 | 폐기 전까지 그 머신이 받은 잡 | 286 | PI25 · PI19 |
| T9 | DB 사본 · 백업을 가진 사람 | S6 | — | DB 에는 `pair_id` 와 토큰 sha256 만. `pair.key` 는 DB 밖 0600 | `data_dir` 을 키와 함께 통째로 복사하면 사칭 가능 — 문서화된 신원 재설정 | 281 · 289 | PI18 |
| T10 | 붙은 워커가 다른 풀 프리셋 env 를 읽기 | S4 | 모든 비-`local_only` 프리셋 env(F9) | 토큰 풀 · 등록 풀로 거른다 | `any-capable` 프리셋 env 는 붙은 워커 모두가 받는다(그런 프리셋에 비밀을 두지 않는다 — M5n §13 · 결정 254) | 284 · 294 | PI14 · PI16 |
| T11 | 다른 빌드가 운영 DB 를 엶 | S7 | `rcm token` 은 DB 를 연다(F5) | `rcm pair` 는 HTTP 로만 | 없음 | 292 | PI28 |
| T12 | 교환 때의 능동 사칭자: 사람이 넣은 교환 URL(잘못된 URL · 가로챈 이름 URL · 그 주소를 차지한 장치)에 답하는 서버 | S1 · S2 · S5 | — (1판: 코드를 그대로 보내 사칭자가 코드를 얻고 자기 토큰 · `pair_secret` 을 주면 워커가 영구히 믿고 argv 를 실행) | 요청은 코드 대신 `worker_nonce` 와 코드 키 증명. 응답은 두 nonce 와 전체 페이로드에 대한 코드 키 MAC — 코드를 모르는 사칭자는 응답을 만들 수 없고 워커는 아무것도 저장하지 않는다(종료 1) | 사칭자가 받은 증명으로 창이 열린 동안 오프라인 추측(T4 b). 진짜 서버로 가는 경로 위의 능동 중계는 응답을 그대로 넘기므로 통과하고 평문 자격 증명을 본다(T4 a) | 301 | PI34 · PI21 |
| T13 | 3xx 로 다른 호스트에 보내는 서버(위장 · 잘못 설정한 프록시) | S1 | `urlopen` 이 따라가며 `Authorization` 을 옮긴다(F40) | 짝지은 머신의 hello 와 토큰 요청은 리디렉트를 따르지 않는다. hello 3xx 는 「그 밖의 응답」(304) | 옛 머신의 `download_tree` · `download_bundle` · `events` 는 오늘처럼 따라간다 | 309 | PI37 |
| T14 | 같은 망의 mDNS 홍수(가짜 인스턴스 여럿) | 가용성 | `_resolve_server` 는 여럿이면 거절, 워커 다시 찾기는 첫 결과 | 후보 최대 8개에 hello, 통과가 정확히 하나일 때만 옮긴다. 둘 이상 통과(복제 서버, 289 가 지원 안 함)면 옮기지 않고 로그 | 8개 넘게 띄워 진짜 서버를 후보 밖으로 밀면 옮기지 못한다(서비스 거부만, 토큰 0) | 310 | PI4 |

### 4.2 서버 신원 `pair.key` · `pair_secret` · `POST /pair/hello` (결정 281 · 289 · 290 · 306 · 308)

서버는 DB 밖의 키 하나로 자기 신원을 증명한다. 짝지은 머신마다 그 키에서 파생한 비밀을 나눠 준다. 계산은 순수 계층과
I/O 계층으로 나눈다(F42, V1-6 · V2-4). PLAN 이 `core/` 를 시계와 I/O 를 보지 않는 순수 계층으로 정했기 때문이다.

| 자리 | 담는 것 | 담지 않는 것 |
|---|---|---|
| `src/remote_ci_monitor/core/pairing.py`(순수, PR1) | `enc` · `pair_mac` · `derive_pair_secret(key, pair_id)` · 챌린지 만들기(`make_challenge(key, pair_id, now: int, rand: bytes)`) · 챌린지 검증(`check_challenge(key, pair_id, c, now: int)`) · `canonical_dialed` · `own_dialed_set` · `verify_hello` · 응답 분류(`classify_hello`) · 코드 정규화 · 형식 검사 · 이름 정리(`clean_pair_label`) · 교환 증명 · 응답 MAC | `secrets` · `time` · `os` · 파일 · 소켓 · 전역 상태 |
| `src/remote_ci_monitor/pairing.py`(I/O, PR1) | `pair.key` 읽기 · 만들기(`load_or_create_pair_key`, `PairKeyError`) · `secrets` 로 nonce · 코드 · `pair_id` · 챌린지 난수 만들기 · 서버 시계 · 창 상태와 잠금 · 쓴 챌린지 집합 · 서버 믹스인(라우트 처리) | — |

나머지 항목은 아래 표에 적는다.

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 서버 키 | `<data_dir>/pair.key`, 32 무작위 바이트(원바이트, hex 아님). 값은 어디에도 찍지 않는다 | T9 |
| 키 읽기 | `serve()` 가 Store 를 연 뒤 부른다. `O_RDONLY` · `O_NOFOLLOW` 로 열고 `fstat`: 보통 파일이 아니거나 · 모드에 그룹 · 기타 비트가 있거나 · 길이가 32 가 아니면 `PairKeyError("<data_dir>/pair.key: …")` → `cannot start server: …` 종료 2(F23). 문장은 무엇이 틀렸는지와 고치는 법(`chmod 600` · 신원 재설정 절차)만, 값은 없다 | T9 |
| 키가 없을 때(306, V2-5 a) | 폐기되지 않은 토큰 가운데 `pair_id` 가 있는 행이 하나라도 있으면 **기동 거절**: `cannot start server: <data_dir>/pair.key is missing but N paired tokens are still active — restore pair.key from the backup taken with this data_dir, or reset pairing: revoke the paired tokens (rcm token list shows them in "paired from"; rcm token revoke <name>), then start again and pair again`. 조용히 새 키를 만들지 않는다(fail-open 금지). 그런 행이 없으면 새 키를 만든다 | T9 |
| 키 만들기(V2-5 b) | ① 같은 폴더에 임시 파일 `pair.key.tmp-<pid>-<hex8>` 을 `O_CREAT` · `O_EXCL` · `O_WRONLY` · 모드 0600 으로 연다 ② 32 바이트를 쓰고 `fsync` ③ `os.link(tmp, pair.key)` — 이미 있으면 `FileExistsError` ④ 임시 파일을 지운다 ⑤ `link` 가 실패했으면 이미 있는 `pair.key` 를 「키 읽기」 규칙으로 읽는다 ⑥ 폴더를 `fsync`. `link` 는 원자적이고 덮어쓰지 않으므로 다른 프로세스가 0바이트나 일부만 쓴 `pair.key` 를 읽는 순간이 없다. umask 와 무관하게 0600(만들 때 모드, PI17) | T9 |
| 키 수명 주기(289) | 키를 바꾸면(재설정) 모든 `pair_secret` 이 무효 → 짝지은 머신은 hello 가 MAC 불일치(확실) → 종료 0 · 「다시 짝지으라」. DB 만 다른 곳에 복원하면 「키가 없을 때」 규칙이 기동을 막는다(살아 있는 짝 토큰이 있으므로). DB 와 키를 함께 두 살아 있는 서버에 두는 것은 지원하지 않는다(문서, PI35) | T9 |
| 워커 · 클라이언트 비밀 | 발급마다 `pair_id` = 16 무작위 바이트의 소문자 hex 32자. `K = HMAC-SHA256(key = pair.key 32바이트, msg = enc("rcm-pair-secret-v1", pair_id))` = 32바이트. 머신이 받는 값 `pair_secret = "<pair_id>:<K 의 소문자 hex 64자>"`. 머신이 MAC 에 쓰는 키는 `bytes.fromhex(K hex)`(32바이트), hex 문자열이 아니다. DB 에는 `tokens.pair_id` 만(281) | T9 |
| 인코딩(L2 · V2-3) | `enc(f1, …, fn)`: 필드는 모두 `str`. 각 필드의 UTF-8 바이트 앞에 그 바이트 수를 4바이트 big-endian 부호 없는 정수로 붙여 이어 붙인다. 정수(유닉스 초, 프리셋 수)는 부호 · 앞의 0 없는 10진 ASCII 로 바꾼 뒤 넣는다. 값이 없는 칸(null)은 빈 문자열. 첫 필드는 용도 라벨: `rcm-pair-secret-v1` · `rcm-hello-v1` · `rcm-challenge-v1` · `rcm-register-v1` · `rcm-pair-request-v1` · `rcm-pair-response-v1`. `pair_mac(key: bytes, label, *fields) -> str` = `hmac.new(key, enc(label, *fields), sha256).hexdigest()`(소문자 hex 64자) | T2 · T12 |
| 형식 검사 | 받은 쪽은 비교 전에 정규식으로 본다. `pair_id` `^[0-9a-f]{32}$`, nonce(`nonce` · `worker_nonce` · `server_nonce`) `^[0-9a-f]{64}$`, MAC(`mac` · `proof`) `^[0-9a-f]{64}$`, 챌린지 `^([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})$`, `pair_secret` `^[0-9a-f]{32}:[0-9a-f]{64}$`. 대문자 hex 는 틀린 값이다(대소문자를 접지 않는다). 비교는 `hmac.compare_digest` | T2 |
| hello 요청 | `POST /pair/hello`, 익명, 본문 ≤ 1 KiB `{"v": 1, "pair_id": <hex32>, "nonce": <hex64>, "dialed": <canonical_dialed(머신이 접속한 URL)>}`. nonce 는 호출마다 `secrets.token_hex(32)`. 형식이 틀리면 400 `{"error": "bad hello request", "code": "bad_hello"}`(존재 여부와 무관한 형식 오류라 오라클이 아니다) | T1 · T2 · T3 |
| `dialed` 규칙(308, V1-3 · V2-2) | 1판의 Host 헤더 판정을 버린다(스킴을 몰라 `https://name` 의 443 을 `:80` 으로 봤다). `canonical_dialed(url) -> str 또는 None`(양쪽이 같은 함수): 스킴 소문자 `http` · `https` 만 · userinfo 가 있으면 None · 호스트 소문자 · 끝의 점 제거 · IPv6 리터럴은 zone id(`%…`) 를 떼고 IPv4-mapped(`::ffff:a.b.c.d`)는 `a.b.c.d` 로, 나머지 IPv6 는 `ipaddress` 의 압축형을 대괄호로 · IPv4 는 `ipaddress` 로 검사(앞의 0 은 None) · 포트가 없으면 `http` 80, `https` 443, 1~65535 밖이면 None · 경로 · 질의 · 조각은 버린다 · 결과 `"<scheme>://<host>:<port>"`. Host 헤더는 판정에 쓰지 않는다(중계가 마음대로 쓸 수 있다 — 보안은 `dialed` 가 MAC 에 들어가는 데서 나온다) | T2 |
| 서버의 자기 주소 | `own_dialed_set(sock_ip, sock_port, public_url, advertise_host)`: ① `http://<canonical(이 요청이 도착한 소켓의 getsockname() IP)>:<그 포트>` ② 소켓 IP 가 루프백(127.0.0.0/8)이면 `http://localhost:<그 포트>` 도 ③ `public_url` 이 있으면 `canonical_dialed(public_url)`(스킴 그대로 — `https://name` → `https://name:443`, V1-3 의 PI22 칸) ④ 광고가 켜져 있으면 `http://<gethostname 첫 조각 소문자>.local:<그 포트>`(F12). `dialed` 가 이 집합에 없으면 421 `{"error": "this server does not answer to that address — set public_url if it is reached through a name or a proxy", "code": "host_not_this_server"}` — `mac` · 챌린지 없음. 보관함 허용 목록(F14) · `local_ipv4s()`(F15)는 쓰지 않는다 | T2 |
| hello 응답 | 200 `{"v": 1, "challenge": <c>, "mac": pair_mac(K, "rcm-hello-v1", pair_id, nonce, dialed, c)}` — **MAC 이 챌린지를 덮는다**(V1-13: 중계가 챌린지를 바꿔치기해 등록을 망치는 것을 MAC 이 잡는다). K 는 `pair_id` 로 매번 파생(DB 조회 없음). 없는 · 폐기된 · 옛 `pair_id` 도 같은 계산이라 응답 모양 · 시간이 같다. Store 를 부르지 않는다(PI5 · PI20) | T5 |
| 챌린지 | `c = "<t>.<r>.<pair_mac(pair.key, "rcm-challenge-v1", pair_id, t, r)>"`, `t` = 서버 벽시계 유닉스 초(정수, 10진), `r` = 8 무작위 바이트의 소문자 hex 16자. 서버는 메모리 상태 없이 MAC 으로 검증한다. 유효 = `now − 120 ≤ t ≤ now + 5`(`PAIR_CHALLENGE_SECONDS = 120`, 미래 쪽 5초는 같은 서버 안의 시계 되돌림 여유, 그보다 미래면 무효). 한 번 쓴 챌린지는 메모리 집합에 만료까지 두어 두 번째를 거절. 재시작하면 집합은 비지만 120초 창이 묶는다 | T4 · T6 |
| 머신의 검증 | `verify_hello(K, pair_id, nonce, dialed, body) -> bool`: 본문이 객체 · `v == 1` · `challenge` · `mac` 형식이 맞고 `compare_digest(mac, pair_mac(K, "rcm-hello-v1", pair_id, nonce, dialed, challenge))`. 응답의 분류는 4.3 | T1 · T2 |
| 등록 증명(N5 → 287) | 짝지은 토큰(`tokens.pair_id` 있음)의 `POST /worker/register` 는 본문 `pair_proof: {"challenge": c, "mac": pair_mac(K, "rcm-register-v1", pair_id, c, worker_session_id)}` 이 있어야 한다(세션 id 는 M5t-b 의 `worker_session_id`, F31). 없으면 403 `{"code": "pair_proof_required"}`. 틀리면 403 `{"code": "pair_proof_invalid", "reason": <r>}`, `r` ∈ `expired`(t 범위 밖) · `reused`(쓴 챌린지) · `bad_mac`(챌린지 MAC 이나 증명 MAC 이 틀림) · `pair_mismatch`(챌린지는 유효한데 다른 `pair_id` 의 것, 또는 토큰의 `pair_id` 로 만든 K 가 아님 — 토큰과 `pair_secret` 이 짝이 아님). 검사는 F30 의 3 다음, 4 앞 — 실패는 세션 행 · 펜싱 · 잡 닫기 · 워커 행 · 능력 보고 0. 옛 토큰(`pair_id` NULL)은 검사하지 않는다. `reason` 은 토큰을 가진 쪽에게만 보이는 응답이라 오라클 걱정이 없다 | T6 |

#### 4.2.1 wire 예시

이 바이트대로 구현하고 PI33 이 잠근다(V2-3). 값은 설명용 자리 표시다. 커밋하지 않는 실제 값과 구별하려고 일부러
엔트로피를 낮췄다. `<…>` 는 계산 결과다.

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

POST /pair/exchange
  {"v": 1, "kind": "worker", "host_name": "build-01", "worker_nonce": "<hex64>",
   "proof": pair_mac(SHA256("ABCDEFGHJK"), "rcm-pair-request-v1", "worker", "build-01", worker_nonce)}
200
  {"v": 1, "name": "build-01", "kind": "worker", "pool": "default", "presets_count": 3,
   "token": "<token>", "pair_secret": "<hex32>:<hex64>", "server_nonce": "<hex64>",
   "mac": pair_mac(SHA256("ABCDEFGHJK"), "rcm-pair-response-v1", "worker", "build-01",
                   worker_nonce, server_nonce, "build-01", "default", "3", token, pair_secret)}
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

### 4.3 워커와 클라이언트가 확인하는 때 (결정 283 · 285 · 295 · 303 · 304 · 305 · 309 · 310)

짝지은 머신, 곧 설정에 `pair_secret` 이 있는 머신은 F19 의 여섯 자리 모두에서 「확인됨」일 때만 토큰을 싣는다. hello
와 토큰 요청은 리디렉트를 따르지 않는다(309). 이미 있는 `_NO_REDIRECT` 와 `follow.no_redirect_opener` 를 쓴다(F40).
3xx 는 토큰 요청에서는 HTTP 응답(오류)이고 hello 에서는 「그 밖의 응답」이다.

hello 응답은 순수 함수 `classify_hello` 가 다섯으로 나눈다.

| 분류 | 무엇 | 뜻 | 결정 |
|---|---|---|---|
| `ok` | 200 · 본문 형식 맞음 · `v == 1` · `mac` 일치 | 확인됨 | 290 |
| `mismatch` | 200 · 본문 형식 맞음 · `v == 1` · `mac` 어긋남 | 확실한 신원 불일치 | 303 |
| `refused_address` | 421 · `code == "host_not_this_server"` | 확실한 거절(그 주소는 이 서버가 아니다) | 303 · 305 |
| `unavailable` | 연결 실패 · 시간 초과(status 0) · 5xx(503 포함) · 429 | 신원 판단 아님 → 재시도 | 303 |
| `other` | 그 밖: 3xx · 404 · 405 · 다른 4xx · 200 인데 본문이 깨짐 · `v` 가 1 아님 | 신원을 증명하지 못한 응답(옛 rcm 서버 · 다른 프로그램 · 위장) → `unavailable` 과 같이 재시도 | 304 |

분류 이름(`ok` · `mismatch` · `refused_address` · `unavailable` · `other`)은 `classify_hello` 안에서만 쓰는 값이다.
`unavailable` 과 `mismatch` 는 이미 다른 뜻으로 쓰이는 문자열이다(`store.py:1115` 의 보관함 상태 `"unavailable"`,
`server.py:628` 등). 그래서 화면과 JSON, 로그에 그대로 내보내지 않는다. 사람에게는 아래 로그 문장을 보이고,
`rcm worker --check` 의 `FAIL` 칸도 그 문장을 쓴다.

토큰 요청의 응답 가운데 확인된 서버가 신원과 엮어 답한 것은 이렇게 읽는다.

| 응답 | 짝지은 머신의 뜻 | 결정 |
|---|---|---|
| 401(어느 토큰 요청이든) | 확인된 서버가 이 토큰을 모른다(폐기 · `--replace` 로 교체) = 「서버가 짝지은 적 없다고 답함」 → 확실 | 303 · 305 |
| 403 `pair_proof_invalid` `reason = pair_mismatch` | 토큰과 `pair_secret` 이 짝이 아님 → 확실 | 305 |
| 403 `pair_proof_invalid` `reason ∈ {expired, reused, bad_mac}` | 시간 지남 · 중계의 바꿔치기 → 새 hello 뒤 한 번 더 등록, 또 이 응답이면 `unavailable` 처럼 물러서며 재시도 | 287 |
| 403 `pair_proof_required` | 짝지은 머신은 늘 증명을 싣는다 — 옛 설정(`pair_secret` 없음)에 짝지은 토큰을 넣은 경우만 생긴다 → 옛 토큰 규칙(오늘의 등록 막힘, 종료 2) + 「this token was paired — its config needs pair_secret; pair again」 | 287 |

짝지은 설정으로 도는 `rcm worker` 의 상태 전이다.

| 지금 | 사건 | 다음 | 하는 일 |
|---|---|---|---|
| 기동(등록 전) | `ok` | 확인됨 | 따라가기 ①(F17) · 프로토콜 확인(F36) · 등록(직전에 hello 를 새로 해 그 챌린지로 증명) 순서 |
| 기동 | `mismatch` · `refused_address`, 또는 확인 뒤 401 · `pair_mismatch` | 끝 | 토큰 0(401 · `pair_mismatch` 는 이미 확인된 서버에만 보낸 것). 로그 한 줄(분류별 문장, 아래) · **종료 0**(되살리지 않음) |
| 기동 | `unavailable` · `other` | 기동 | 토큰 0. 같은 주소 · 분류의 경고는 속도를 제한해 주기적으로 되풀이한다(304). 물러서며 다시 hello: 5 → 10 → 20 → 40 → 60초 상한(오늘의 `REGISTER_RETRY_SECONDS` 5초에서 시작). 종료하지 않는다. `server = "auto"` 면 연결 실패(status 0)만 다시 찾기 세기에 더한다 |
| 확인됨 | 토큰 요청이 연결 실패(status 0) | 확인 전 | 다음 토큰 요청 전에 다시 hello(PI4 b) |
| 확인됨 | HTTP 응답(503 · 5xx 포함) | 확인됨 | 그대로(다시 hello 하지 않는다 — 503 이 heartbeat 을 멈추게 하지 않는다, V2-10) |
| 확인 전(도는 중) | `ok` | 확인됨 | 미룬 heartbeat · 보고를 이어 간다 |
| 확인 전(도는 중) | `unavailable` · `other` | 확인 전 | 토큰 0, 물러서며 다시 hello(경고는 기동 행처럼 되풀이한다, 304). 도는 잡은 계속 돈다. 보고는 M5t-b 의 보고 큐에 남는다(F37). 서버 쪽에서는 레인 down · 잡은 유예(M5t-b) |
| 확인 전(도는 중) | `mismatch` · `refused_address`, 또는 확인 뒤 401 · `pair_mismatch` | 끝내는 중 | 새 claim 0, 토큰 0. 도는 잡은 끝까지 돌리고(보고는 보고 큐에 남김) 도는 잡이 0 이 되면 종료 0. 그동안 기존 시한 · 취소 집행은 그대로이고 신원 때문에 멈춘 상태를 계속 눈에 띄게 로그에 남긴다(305). 그동안 hello 를 물러서며 다시 해 `ok` 가 되면 「확인됨」으로 돌아간다(305) |
| 확인 전(워커, `server = "auto"`) | 연속 연결 실패 5번 | 확인 전 | mDNS 후보 최대 8개(`discover` 결과 순서)에 hello. **`ok` 가 정확히 하나**면 그 주소로 옮기고 로그 `server moved: A → B`. 0개면 옮기지 않는다. 둘 이상이면 옮기지 않고 로그 「several servers prove this worker's identity — a copied data_dir is not supported (decision 289)」. 설정 파일은 고치지 않는다(310) |
| 확인됨 | 등록 · 레인 재등록(M5t-b `not_registered` 뒤 같은 세션 재등록 포함) | 확인됨 | 등록마다 hello 를 새로 하고 그 챌린지로 `pair_proof` |
| 아무 때 | 프로토콜 불일치(M5t-b) | 끝 | 종료 3(M5t-b 결정 258 — 이 명세는 바꾸지 않는다) |
| 아무 때 | 펜싱(M5t-b) | 끝 | 종료 0(M5t-b) |

`unavailable` 과 `other` 는 로그 한 줄로 끝내지 않는다. 그런 응답만 돌려주는 서버가 숨지 않도록 속도를 제한한 경고를
주기적으로 되풀이하며 `rcm worker --check` 와 `rcm doctor` 에 「서버 신원 미확인」 상태를 드러낸다(304, 4차 위임
결정). 첫 경고는 바로, 그 뒤 같은 주소 · 종류의 경고는 10분마다 한 줄씩 되풀이한다(304, 메인 세션이 정한 값).

워커 로그 문장은 분류마다 하나다. 값은 싣지 않고 주소는 `dialed` 로 적는다.

| 분류 | 문장 |
|---|---|
| `mismatch` | `server at <dialed> could not prove it is the coordinator this worker was paired with — it may be another machine, or the coordinator's pair.key was reset; pair again: rcm pair --worker on the coordinator, then rcm setup --role worker --pair (stopping; the service will not restart it)` |
| `refused_address` | `the coordinator does not answer to <dialed> — use the URL rcm pair printed, or set public_url on the coordinator (stopping; the service will not restart it)` |
| 확인 뒤 401 | `the coordinator no longer knows this worker's token (revoked or replaced) — pair again: … (stopping; …)` |
| `pair_mismatch` | `this worker's token and pair_secret do not belong together — pair again: … (stopping; …)` |
| `unavailable` · `other` | `cannot verify the server at <dialed> yet (<분류 · 상태>) — not sending the token; retrying` |

짝지은 클라이언트(`client.toml` 에 `pair_secret` 이 있다)는 명령에 따라 이렇게 끝난다.

| 명령 | `ok` | `mismatch` · `refused_address` · 확인 뒤 401 | `unavailable` · `other` |
|---|---|---|---|
| 아무것도 하기 전(`rcm run` · `jobs` · `top` · `machines` · `cancel` · `check` · 따라가기) | 진행 | 종료 2, 같은 뜻의 문장(아무것도 하지 않았다) | 오늘의 「서버에 닿지 못함」 경로와 같은 처리(토큰 0) |
| `rcm wait`(잡을 낸 뒤, 다시 연결할 때) | 진행 | 종료 3(결과를 모른다, 오늘의 3 뜻) | 오늘의 재연결 · 기다림과 같다(토큰 0) |

옛 토큰 머신, 곧 `pair_secret` 이 없는 머신은 1판과 같다. 리디렉트 동작도 오늘 그대로다. 토큰 없는 클라이언트의 `auto`
행은 결정 300 이다.

| 설정 | 동작 | 결정 |
|---|---|---|
| 워커, `server` 명시 URL | 오늘 그대로(hello 없음, 다시 찾기 없음, F16) | 283 |
| 워커, `server` 가 빈 값 · `auto` | `rcm worker` · `rcm worker --check` 가 mDNS 질의 전에 종료 2: 「server = "auto" needs a paired worker — pair it (rcm pair --worker on the coordinator, then rcm setup --role worker --pair) or set server to the coordinator's URL」 | 283 |
| 클라이언트, 토큰 있음 + 명시 URL | 오늘 그대로 | 285 |
| 클라이언트, 토큰 있음 + 빈 값 · `auto` | `_resolve_server` 가 `ConfigError`(종료 2)로 같은 뜻의 문장. `_follow_first` 는 이 경우 따라가지 않고 명령이 그 문장으로 끝난다 | 285 |
| 클라이언트, 토큰 없음 + `auto` | mDNS 찾기는 오늘대로 한다. 찾은 서버의 client wheel 을 내려받아 실행하는 따라가기는 검증된 `pair_secret` 이 있을 때만 하므로 이 설정은 따라가지 않는다. 따라가지 않은 뒤에도 명령은 이어 가고, stderr 에 「검증되지 않은 서버라 따라가지 않았다」 한 줄과 짝짓는 법을 남긴다(결정 300). 토큰이 없으니 이어 가는 명령은 읽기뿐이다. 명시 URL 은 사용자의 신뢰 결정으로 남아 오늘처럼 따라간다 | Q-A |

사람이 짝지은 워커의 `server` 를 `auto` 로 고쳤으면 후보 최대 8개에 hello 한다. `ok` 가 정확히 하나일 때 그 주소를
쓴다. 0개이거나 둘 이상이면 종료하지 않고 기동 표의 `unavailable` 처럼 물러서며 다시 찾는다. `auto` 에서 후보 하나가
`mismatch` 인 것은 같은 망의 다른 서버일 뿐이라 확실한 거절로 보지 않는다. 종료 0 은 설정에 적힌 명시 URL 에서 받은
`mismatch` · 421 과, 확인된 서버의 401 · `pair_mismatch` 에만 쓴다(305).

짝지은 클라이언트의 `server` 를 사람이 `auto` 로 고쳤을 때도 후보 최대 8개에 hello 한다. `ok` 가 정확히 하나일 때만
그것을 쓴다. F16 의 `_resolve_server` 가 쓰는 「정확히 하나」 규칙과 같은 꼴이다. 0개이거나 둘 이상이면 `ConfigError`
로 종료 2 다.

### 4.4 짝짓기 창 (결정 280 · 286 · 288 · 291 · 292 · 301 · 307)

짝짓기 창은 사람이 코디네이터에서 여는 짧은 발급 기간이다. 창이 열려 있는 동안 코드 한 번으로 토큰이 나온다. 2판은
교환이 코드를 보내지 않도록 바꿨다(301). 코드 입력 규칙과 `host_name` 정리, 창 id 도 더했다.

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 창을 두는 곳 | 서버 프로세스 메모리. DB 표를 두지 않는다(3판의 `pair_windows` 를 버림). 재시작하면 열린 창은 사라진다(수명을 늘리지 않음). 익명 실패가 DB 를 쓰지 않는다 | T5 |
| 창 수 | kind(`worker` · `client`)마다 하나. 같은 kind 가 열려 있으면 `POST /pair` 는 409 `pair_window_open` + `expires_at`. 두 kind 는 함께 열 수 있다 | T7 |
| 코드 | 10자, RFC 4648 base32 알파벳(A–Z, 2–7)을 `secrets.choice` 로. 화면에 `ABCDE-FGHJK` 꼴. 서버는 `SHA256(정규화한 코드의 ASCII 바이트)` 32바이트(= 코드 키)만 든다 | T5 |
| 코드 입력(307, V2-8) | `rcm setup --pair` 가 정규화(대문자로 · `-` 와 공백 제거) 뒤 형식을 먼저 본다: 10자가 아니거나 알파벳 밖 글자(0 · 1 · 8 · 9 · 기호 등)가 있으면 **보내기 전에** 종료 2 「pairing codes are 10 characters of A–Z and 2–7 (no 0, 1, 8 or 9) — check the code」. 요청이 나가지 않으므로 창의 실패로 세지 않는다. 0→O · 1→I · 8→B 같은 바꿈은 하지 않는다(모호하다). 서버도 형식이 틀린 증명 요청은 같은 404 로 답하되 세지 않는다(맞을 수 없는 요청이다) | T5 |
| 수명 | 기본 10분, 상한 60분(`rcm pair --ttl <분>`, 1~60, 밖이면 종료 2 · 서버 400). 서버 시계로만 판정. reload 는 바꾸지 않는다 | T5 |
| 실패 세기 | 그 kind 의 열린 창에 센다(출처 무관, 288). 5번째 실패에 창을 닫고 이유 `too_many_wrong_codes`. 서버 로그 한 줄 「pair: worker window closed after 5 wrong codes — open a new one with rcm pair」. 기다리던 `rcm pair` 가 같은 문장과 종료 1 | T5 |
| 교환 요청(301) | `POST /pair/exchange`, 익명, 본문 ≤ 1 KiB `{"v": 1, "kind", "host_name", "worker_nonce": <hex64>, "proof": pair_mac(코드 키, "rcm-pair-request-v1", kind, host_name, worker_nonce)}`. **코드는 본문 · 헤더 · URL 어디에도 없다.** `host_name` 은 문자열 255자 이하(원문 그대로 MAC 에 들어간다) | T12 · T4 |
| 교환 처리 | 창 잠금 안에서: 본문 `kind` 의 창이 열림 · 만료 전인지 → `proof` 형식 → `compare_digest(proof, 그 창의 코드 키로 계산한 증명)` → 틀리면 실패 1 · 같은 404 → 맞으면 이름 결정 → 이름 충돌이면 409 `name_taken`(창은 열린 채, 실패로 세지 않는다) → 창을 `used` 로 닫음 → 토큰 행 쓰기(한 트랜잭션) → `server_nonce = secrets.token_hex(32)` → 200(4.2.1 의 모양). 행 쓰기가 실패하면 503 `pair_failed`(창은 닫힌 채) | T5 · T7 · T12 |
| 교환 응답 MAC(301) | `mac = pair_mac(코드 키, "rcm-pair-response-v1", kind, host_name, worker_nonce, server_nonce, name, pool 또는 "", presets_count 10진 또는 "", token, pair_secret)` — 두 nonce 와 전체 페이로드를 덮는다. 워커는 **아무것도 저장하기 전에** 형식과 MAC 을 본다. 틀리면 저장 0 · 종료 1 「the server at <URL> answered but does not know this code — it is not the coordinator that showed it; nothing was saved」. 응답의 `kind` 가 요청한 kind 와 다르면 같은 처리 | T12 |
| 같은 404 | 틀린 증명 · 형식이 틀린 증명 · 닫힌 창 · 연 적 없는 창 · 만료 · 이미 쓴 코드 · 그 kind 의 창 없음 · 모르는 kind 모두 404 `{"error": "no open pairing window for that code"}`(고정 바이트, `not_found_hint` 없음). 셈은 「열린 창에 대한 형식 맞는 틀린 증명」만 | T5 · T7 |
| 오프라인 추측(301 의 남는 위험) | 증명을 관찰한 쪽(엿보기 · 교환 URL 의 사칭자)은 창이 열린 동안 오프라인으로 코드를 추측할 수 있다. 5회 제한은 온라인 추측만 막는다. 숫자는 M4. 창이 닫히면 맞힌 코드는 쓸모없다 | T4 · T12 |
| 동시 교환 | 창마다 잠금 하나. 같은 코드 N개 동시 → 200 정확히 1 | T5 |
| 이름 | `rcm pair --name N` 이면 N(`_NAME_RE`, 열 때 이미 있으면 409 `name_taken`). 없으면 `clean_pair_label(host_name)`: `_NAME_RE` 밖 글자 → `-`, 앞의 영숫자 아닌 글자 제거, 64자 자르기, 남는 글자가 없으면 `worker-<hex6>`(client 창이면 `client-<hex6>`) | T8 |
| `host_name` 보이기(V2-14) | 서버 로그 · `GET /pair` 의 `paired.host_name` · `rcm pair` 완료 줄 · `tokens.paired_host` · `rcm token list` 에는 원문이 아니라 `clean_pair_label(host_name)`(대체 이름 없이, 비면 「—」)만 싣는다 — 제어문자 · 줄바꿈이 로그 줄을 만들지 못한다. 원문은 MAC 계산에만 쓰고 저장하지 않는다 | T8 |
| `--replace` | `--name` 과 함께만. 그 이름의 행이 같은 kind 여야 한다(아니면 400 `replace_needs_same_kind`). 교환 때 그 행을 한 UPDATE 로 바꾼다: 새 `sha256` · `pair_id` · `pool` · `paired_host` · `paired_addr` · `created_at`, `revoked_at = NULL`. 옛 토큰은 그 순간부터 401(F1 · F2) | T6 · T8 |
| 풀 | worker 창만. `--pool P`(없으면 `default`). 열 때 그 풀로 갈 프리셋 수를 계산해(4.5 의 같은 함수) 0 이면 응답 `warnings[]` 에 「0 presets reach pool P」 — 거절하지 않는다 | T10 |
| 화면 URL | `POST /pair` 응답 `urls[]`: `public_url` 이 있으면 그것 하나. 없고 bind 가 특정 비루프백 주소면 `http://<bind>:<port>`. `0.0.0.0` 이면 `local_ipv4s()` 의 주소마다 + 광고가 켜져 있으면 `http://<host>.local:<port>`. 루프백 bind 에 `public_url` 이 없으면 409 `pair_unreachable` 「this server listens on <bind> only — other machines cannot reach it; set bind (and public_url) first」 — 창을 열지 않는다 | T2 |
| 평문 경고 | `rcm pair` 와 `rcm setup --pair` 는 URL 의 호스트가 루프백도 Tailscale 대역(100.64.0.0/10)도 아니면 stderr 에 「the token and the pairing secret cross this network in plain text — prefer the server's Tailscale address」(301 뒤로 코드는 망으로 나가지 않는다), 이름이면 「a name URL cannot stop a relay on this network — use an IP or Tailscale address」 | T4 · T2 |
| 창 id(V2-9) | `POST /pair` 201 응답에 `id`(8 무작위 바이트 hex 16자), `GET /pair` 의 창마다 `id`. `rcm pair` 는 이 id 로 자기 창을 따라간다(재시작으로 사라진 창을 알아본다) | — |
| 남기는 것(N4 → 286) | 교환 성공 때 서버 로그 「pair: <kind> '<name>' paired from <addr> (host <정리한 host_name>, pool <p>)」(`<addr>` = 교환 요청의 접속 주소, 프록시 뒤면 프록시 주소), `tokens.paired_host` · `paired_addr`, 기다리던 `rcm pair` 완료 줄. 두 번째 승인 없음 | T8 |
| 코드 노출 | 코드는 `POST /pair` 의 201 응답에만 있고 `rcm pair` 가 stdout 에 한 번 찍는다. `GET /pair` · 로그 · 이벤트 · `/api/*` 에 없다 | T5 |
| 관리 라우트 | `POST /pair`(admin) 본문 `{kind, pool?, name?, replace?, ttl_minutes?}` → 201 `{id, kind, code, urls[], expires_at, pool, name, presets_count, warnings[]}`. `GET /pair`(admin) → `{windows: [{id, kind, pool, name, opened_by, opened_at, expires_at, state, reason, paired}]}` — 최근 1시간 · 20개, 코드 · 코드 키 없음. `state` ∈ `open` · `used` · `expired` · `closed` · `cancelled`, `paired` = `{name, host_name(정리), addr, at}` 또는 null. `POST /pair/cancel`(admin) `{kind}` → 200 `{cancelled: <bool>}` | T7 |

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
| 등록 검사 자리 | F30 의 3 다음, 4 앞(증명 검사 다음). 실패는 세션 · 펜싱 · 워커 행 · 능력 보고 · 잡 0 |
| `worker_spec` 자리(V1-8) | F9 — M5t-a · M5t-b 뒤의 자리를 구현 PR 이 다시 찾는다. 거르기 함수 이름 `spec_presets_for_pool(pool)` 은 그대로 |

### 4.6 CLI (결정 292 · 296 · 303 · 304 · 305 · 307 · 312)

새로 생기거나 바뀌는 명령과 그 종료 코드다.

| 명령 | 정한 것 | 종료 코드 |
|---|---|---|
| `rcm pair --worker [--pool P] [--name N] [--replace] [--ttl 분]` · `rcm pair --client [--name N] [--replace] [--ttl 분]` | 클라이언트 설정(`server` · `RCM_TOKEN`)의 **admin** 토큰으로 `POST /pair`. stdout: 코드 · URL(들) · 만료 시각(로컬 시간)과 남은 분 · kind · 풀 · 이름 · 갈 프리셋 수. 경고는 stderr. 그 뒤 2초마다 `GET /pair` 로 자기 창 `id` 를 기다린다. 쓰이면 완료 줄(이름 · kind · 풀 · 정리한 `host_name` · 주소 · 시각 · 다음 할 일 `rcm machines`, `rcm token revoke <name>`). DB 를 열지 않는다. admin 토큰이 없으면 「pairing needs an admin token: rcm token add <name> --admin on the server」. 기다리는 동안: `GET /pair` 연결 실패는 계속 시도한다. 다시 닿았을 때 그 `id` 의 창이 없으면 종료 3 이다(결과를 모른다, 312). 재시작 직전에 창이 쓰였을 수 있으므로 창 `id` 와 이어진 영속 상태가 완료나 미사용을 증명하지 않는 한 1 로 보고하지 않는다. 창은 메모리에만 있으므로(291) 그런 영속 상태는 없다. 그래서 재시작 뒤에는 늘 「the server restarted — whether the window was used is unknown; check rcm token list」 를 찍고 종료 3 이다. `expires_at` + 30초까지 한 번도 닿지 못하면 「lost the server while waiting — the window closed at <time> unless it was used; check rcm token list」 종료 3(결과를 모른다). Ctrl-C 는 `POST /pair/cancel` 을 한 번 시도하고(실패하면 「could not cancel — the window closes at <time>」) 130 | **0** 짝지어짐 · **1** 창이 쓰이지 않고 닫힘(만료 · 틀린 코드 5번 · 취소됨) 또는 서버가 열기를 거절(409 `pair_window_open` · `pair_unreachable` · `name_taken`) · **2** 사용법(`--ttl` 1~60 밖, `--replace` 에 `--name` 없음, `--worker`/`--client` 둘 다 없음 · 둘 다 있음, 서버의 400 · 401 · 403, admin 토큰 없음) · **3** 결과를 모름: 기다리다 서버를 잃고 만료 + 30초까지 돌아오지 않음, 또는 재시작으로 창이 사라졌는데 영속 상태가 완료나 미사용을 증명하지 못함 · **130** Ctrl-C(312, V2-9) |
| `rcm pair --cancel --worker` · `--cancel --client` | `POST /pair/cancel` | 0 닫음 · 1 열린 창 없음 · 2 사용법 |
| `rcm setup --role worker --server URL --pair` | M5t-a 의 setup 에 `--pair` 를 더한다. 코드는 TTY 면 `getpass` 프롬프트, 아니면 stdin 한 줄. 인자로 받지 않는다(`--code` 는 argparse 가 모르는 인자로 종료 2). 순서: ① 쓸 파일이 이미 있으면 `--force` 없이 거절(교환 전에) ② 코드 형식 검사(307, 보내기 전) ③ 교환(301 — 코드는 보내지 않는다) ④ 응답 MAC 검사, 틀리면 저장 0 · 종료 1 ⑤ `worker.toml` 을 임시 파일 `O_CREAT` · `O_EXCL` 0600 → 쓰기 · `fsync` → 이름 바꾸기로(`server` = 넣은 URL, `token`, `pair_secret`, `pool`, `name` = 토큰 이름) ⑥ **주소 확인**(V1-2): 저장한 URL 로 hello 한 번 — 신원 검사가 아니다(교환 MAC 이 이미 신원을 확인했다). 그 URL 이 hello 의 `dialed` 규칙을 통과하는지(예: `public_url` 없는 이름 URL 의 421)를 지금 알려 주려는 것이다. `ok` 면 완료 줄 「paired with <URL> as '<name>' · pool <p> · <n> presets can come here · results go back to the coordinator and the requester's rcm wait gets them」 종료 0. `refused_address` · `mismatch` · `other` · `unavailable` 이면 파일은 둔 채 stderr 에 이유와 고치는 법(「set public_url on the coordinator, then rcm worker --check」) 종료 1. `--pair` 면 `--yes` 에서 `--pool` 이 필요 없다(창이 정한다). 주면 창의 풀과 다를 때 종료 2 | 0 · 1(교환 404 「the code was wrong or used, or the window is closed — ask for a new code」 · 응답 MAC 틀림 · 연결 실패 · 파일을 못 씀(토큰이 발급됐으면 「issued as '<name>' but not saved — on the coordinator: rcm token revoke <name>, then open a new window」) · 주소 확인 실패(파일은 저장됨)) · 2(사용법 · 코드 형식 · 이미 있는 파일) |
| `rcm setup --role client --server URL --pair` | 같은 흐름으로 `client.toml`(`server`, `token`, `pair_secret`) 0600 | 같다 |
| `rcm worker`(짝지은 설정) | 4.3 의 표 | **0** 멈춤 · 펜싱(M5t-b) · 확실한 신원 거절(303 · 305 — 되살리지 않음) · **1** exec 실패(오늘) · **2** 등록 막힘(오늘 · `pool_mismatch` · 옛 토큰 + `auto` · `pair_proof_required`) · **3** 프로토콜 불일치(M5t-b만). `unavailable` · `other` 로는 종료하지 않는다 |
| `rcm token list` | 열 뒤에 `pool`(NULL 「—」)과 `paired from`(`<정리한 paired_host> @ <paired_addr>` 또는 「—」) | 오늘 그대로 |
| `rcm worker --check` | 맨 앞(python 행 다음)에 `server identity` 행. 짝지은 설정: hello 가 확인되면 `ok`, 아니면(`unavailable` · `other` 포함) `FAIL` 과 4.3 의 로그 문장(분류 이름이 아니라)으로 서버 신원 미확인 상태를 드러내고(304) 그 뒤 토큰을 쓰는 행(token · fingerprint · presets)은 「— skipped: server not verified」로 두고 토큰을 보내지 않는다. 옛 토큰: `warn` 「not paired — this worker cannot tell this server from another machine; pair it with rcm pair」 | 오늘 규칙(FAIL 이 있으면 1) |
| `rcm check` | 짝지은 클라이언트만 `server identity` 행 | 오늘 규칙 |
| `rcm doctor` | 짝지은 워커 설정이면 `server identity` 행을 더한다. doctor 가 설정의 서버에 직접 hello 를 한 번 보내 판정한다: 통과면 ok, 확실한 불일치(305 a)면 FAIL(종료 1), 닿지 않음 · 분류되지 않은 답(304)이면 「서버 신원 미확인」 unknown(종료 3). 워커 프로세스의 상태를 읽지 않는다(따로 도는 프로세스라 볼 수 없다). PR3(워커 게이트)이 맡는다(304, 메인 세션이 정함) | PR3 |
| `NO_FOLLOW_COMMANDS` | `pair` 를 더한다(`follow.py:80-82`). setup 은 M5t-a PR4 가 넣는다 | — |
| `rcm token add` | 바꾸지 않는다 | — |

### 4.7 얼리는 계약

이 명세 PR 의 머지가 아래 계약을 얼린다. 얼린 뒤에 바꾸려면 오너가 승인하는 새 명세 PR 이 있어야 한다.

| 계약 | 바뀌는 것 |
|---|---|
| `/api/status` `schema_version` | 그대로(새 키 없음) |
| `/api/health` · mDNS TXT | 그대로 |
| DB | v27(M5t-b v26 뒤). `ALTER TABLE tokens ADD COLUMN pool TEXT` · `… pair_id TEXT` · `… paired_host TEXT` · `… paired_addr TEXT` · `CREATE UNIQUE INDEX IF NOT EXISTS tokens_pair_id ON tokens(pair_id)`. 새 DB 스키마도 같은 칸. 새 표 없음 |
| 서버 파일 | `<data_dir>/pair.key`(32바이트, 0600). 없고 살아 있는 짝 토큰이 있으면 기동 거절(306) |
| 와이어 — 새 라우트 | `POST /pair/hello`(익명) · `POST /pair/exchange`(익명) · `POST /pair` · `GET /pair` · `POST /pair/cancel`(admin). 본문 · 응답 · 바이트 인코딩은 4.2 · 4.2.1 · 4.4. 새 오류 코드 `bad_hello`(400) · `host_not_this_server`(421) · `pair_window_open` · `name_taken` · `pair_unreachable`(409) · `pair_failed`(503) · `replace_needs_same_kind`(400) |
| 와이어 — 워커 프로토콜 | `POST /worker/register` 본문에 선택 키 `pair_proof{challenge, mac}`(MAC 이 M5t-b 의 `worker_session_id` 를 묶는다). 새 응답 409 `pool_mismatch`(+ `bound_pool`) · 403 `pair_proof_required` · 403 `pair_proof_invalid`(+ `reason` ∈ `expired` · `reused` · `bad_mac` · `pair_mismatch`). `GET /worker/spec` 이 풀로 걸러지고 질의 `pool` 을 받는다. 옛 서버는 `pair_proof` 를 무시한다 |
| 종료 코드 | `rcm worker`: 0 에 「확실한 신원 거절」(새, 303 · 305), 3 은 M5t-b 의 프로토콜 불일치만(M5t-c 는 쓰지 않는다). `rcm pair` 0/1/2/3/130, `rcm setup --pair` 0/1/2, 클라이언트 명령의 확실한 신원 거절 2(`rcm wait` 은 3), 옛 토큰 + `auto` 2 |
| CLI | `rcm pair` 와 플래그, `rcm setup --pair`, `rcm token list` 의 뒤 두 열, `rcm worker --check` · `rcm check` 의 `server identity` 행, 로그 문장(4.3), `rcm doctor` 의 `server identity` 행(304) |
| 설정 키 | `worker.toml` · `client.toml` 의 `pair_secret`(`"<hex32>:<hex64>"`, 파일 0600 필수). 서버 설정 키는 더하지 않는다 |
| 상수 | 코드 10자 base32(A–Z · 2–7) · 창당 실패 5 · TTL 기본 10분 / 상한 60분 · 챌린지 120초(미래 여유 5초) · 익명 본문 1 KiB · 다시 찾기 후보 8 · hello 재시도 5→60초 · `rcm pair` 의 잃음 판정 만료 + 30초 · 신원 미확인 경고를 되풀이하는 간격 10분(304) |
| 리디렉트 | 짝지은 머신의 hello 와 토큰 요청은 3xx 를 따르지 않는다(309) |

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
| M5t-b 와 | 등록 증명 자리는 F30 의 3 다음 · 4 앞. 증명은 `worker_session_id` 를 묶는다. 종료 3 은 M5t-b 몫만(303, F33 · Q-E) |

## 5. 작업 분할과 병렬

PR 하나는 브랜치 하나, 워크트리 하나다. 브랜치 이름은 `branch` 스킬로 확정하며 아래 이름은 제안이다. `shared` 열의
파일은 덧붙이기만 한다.

PR0 은 이 명세 PR 자신이다(브랜치 `docs/m5tc-pairing-identity-workplan`). PLAN.md 와 M5n §13 의 편집은 마지막 격리
라운드가 끝나고 머지하기 직전의 마지막 커밋으로 같은 PR 에 넣는다. 집 관례가 그렇다. `docs/m5t-workplan.md:690` 의 PR0
행 「이 명세와 PLAN.md 반영」과 `docs/m5tb-workplan.md` §5 의 PR0 행이 같은 방식이다(V1-9). PR0 이 고칠 줄은 이렇다.

| # | 파일:줄 | 지금 | 바꿀 것 |
|---|---|---|---|
| E1 | `PLAN.md:1` · 머리 판 줄(`:14` 다음) | 「(v2.13, 2026-11-25)」 | 다음 판 번호와 날짜, 머리 인용에 M5t-c 한 줄(명세 · 결정 280~312 · DB v27). M5t-b 명세 PR(#270)도 같은 줄을 고치므로 나중에 머지하는 쪽이 판 번호를 하나 더 올려 맞춘다 |
| E2 | `PLAN.md:243` | 「**쓰기(`POST /jobs`·업로드·취소)는 인증 필수.** `none` 은 없다.」 | 그 문장 뒤에 예외 문장: 「예외는 둘이다(M5t-c 결정 297, 2026-11-26): `POST /pair/hello` 는 상태를 바꾸지 않는 서버 신원 증명이고, `POST /pair/exchange` 는 사람이 `rcm pair` 로 연 짝짓기 창의 한 번 쓰는 코드(증명)가 인증을 대신한다.」 |
| E3 | `PLAN.md:629` | 「**M5t-c — 짝짓기 · 서버 신원 보안**(명세 예정)」 | 「(계획 2026-11-26, 명세 `docs/m5tc-workplan.md` · 결정 280~312 · DB v27)」과 한 줄 요약 |
| E4 | `PLAN.md:753` | 「… 244 · 245 는 M5t-c 명세에 실을 예정이다. 249 는 두 명세에 나눠 실을 예정이다.」 | 「280~312 는 M5t-c 명세 `docs/m5tc-workplan.md` §11 에 있다. 244(짝짓기 창과 서버 신원)는 280 · 281 · 290 · 291 이, 245 의 풀 묶기는 284 · 294 가, 249 의 M5t-c 부분(신원 · 창 · `tokens.pool`)은 293 이 대신한다. 옛 번호는 다시 쓰지 않는다.」(M5t-b 부분은 M5t-b PR0 이 같은 줄에 적는다 — 나중 머지 쪽이 맞춤) |
| E5 | `docs/m5n-workplan.md:650` | 「토큰 자동 발급 · 자동 가입. 토큰은 사람이 옮긴다(보안 결정 유지).」 | 줄 끝에 「— 2026-11-26 M5t-c 결정 280 으로 개정: 사람이 `rcm pair` 로 연 짧은 수명 · 한 번 쓰는 짝짓기 창 안에서만 발급 · 가입한다(`docs/m5tc-workplan.md`).」 |

| PR | 브랜치(제안) | 내용 | 시작 조건 | 물결 | 함께 도는 PR | owns | shared | 머지 순서 | lane(추천 · 이유) |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5tc-pairing-identity-workplan`(이 명세 PR) | 이 명세 + 위 E1~E5(마지막 커밋) | — | 0 | M5t-b 명세 PR(#270) | `docs/m5tc-workplan.md`, `docs/m5n-workplan.md`(`:650` 한 줄) | `PLAN.md`(E1 · E4 는 M5t-b 명세 PR 과 같은 줄) | 먼저. M5t-b 명세 PR 과는 나중 머지 쪽이 E1 · E4 를 다시 맞춤 | — (명세 PR) |
| PR1 | `feat/server-pairing-window-and-hello` | DB v27 · `TokenInfo` 칸 · `pair.key` 수명 주기(`PairKeyError`) · 짝짓기 창(메모리) · `/pair/hello`(`dialed` 규칙) · `/pair/exchange`(301) · `/pair` 관리 라우트(창 `id`) · 순수 `core/pairing.py` · I/O `pairing.py`(믹스인) · 교환 로그 줄(정리한 `host_name`) · PLAN 「서버 API」 행 · 「저장소」 tokens 칸 · 문서 잠금(PLAN.md:243 예외 문장과 PI20 집합 대조) | PR0 머지 · M5t-a 전체(v0.11.0) 머지 · M5t-b PR1(DB v26 · 세션 등록) 머지 | 1 | — | `src/remote_ci_monitor/core/pairing.py`(새), `src/remote_ci_monitor/pairing.py`(새), `src/remote_ci_monitor/store.py`, `src/remote_ci_monitor/server.py`, `tests/test_docs_m5tc.py`(새), `tests/test_pairing_server.py`(새), `tests/test_pairing_wire.py`(새) | `PLAN.md`, `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md` | M5t-b PR2(`store.py` · `server.py`)가 같은 때 돌면 M5t-b PR2 먼저, PR1 은 dev 를 합친 뒤 | full · DB 판이 오르고 새 익명 라우트(보안 · 와이어) |
| PR2 | `feat/worker-token-bound-pool` | 등록의 증명 검사(세션 id 묶음, `reason`) · 풀 검사(F30 의 3 다음 · 4 앞) · 다른 주소 재등록 로그 · `/worker/spec` 풀 거르기(`spec_presets_for_pool`) | PR1 머지(M5t-b PR1 은 PR1 의 조건으로 이미 머지) | 2 | PR3 | `src/remote_ci_monitor/remote_workers.py`, `src/remote_ci_monitor/capabilities.py`, `tests/test_pairing_pool.py`(새) | `src/remote_ci_monitor/server.py`(`worker_spec_for` 한 곳), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `tests/test_docs_m5tc.py` | PR2 먼저 | full · 워커 프로토콜(와이어)과 등록 · 펜싱(동시성) |
| PR3 | `feat/client-verify-server-before-token` | `pair_secret` 설정 키 · `_check_private` · 관문(F19 여섯 자리) · 리디렉트 안 따름(309) · 응답 분류와 종료 · 재시도(303 · 304 · 305) · 워커 기동 hello · 다시 찾기 검증(310) · 등록 증명 보내기 · 옛 토큰 + `auto` 거절 · `_follow_first` · `_worker_follow_start` 순서 · `rcm worker --check` · `rcm check` 행 · 워커 로그 줄의 비밀 0 | PR1 머지 · M5t-b PR2(`remote_worker.py` · `client.py`) 머지(V1-11) | 2 | PR2 | `src/remote_ci_monitor/config.py`, `src/remote_ci_monitor/client.py`, `src/remote_ci_monitor/remote_worker.py`, `src/remote_ci_monitor/follow.py`(`_headers` 관문 · 오프너만), `tests/test_pairing_client.py`(새), `tests/test_pairing_worker_e2e.py`(새) | `src/remote_ci_monitor/cli.py`(`_resolve_server` · `_follow_first` · `_worker_config` · `_worker_follow_start` · `_worker_check` · `cmd_check`), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `tests/test_docs_m5tc.py` | PR2 뒤, dev 를 합친 뒤 | full · 보안(토큰이 어디로 가나)과 재시작 · 재연결 경로 |
| PR4 | `feat/cli-pair-command-and-setup-pairing` | `rcm pair`(열기 · 기다리기 · 창 id · 취소 · 0/1/2/3/130) · `rcm setup --role worker --pair` · `--role client --pair`(코드 형식 검사 · 증명 교환 · 응답 MAC · 0600 원자 쓰기 · 주소 확인) · `rcm token list` 두 열 · `NO_FOLLOW_COMMANDS` 에 `pair` · 평문 · 이름 URL 경고 | PR3 머지(`cli.py`) | 3 | — | `src/remote_ci_monitor/cli.py`, `tests/test_cli_pair.py`(새), `tests/test_setup_pair.py`(새) | `src/remote_ci_monitor/follow.py`(`NO_FOLLOW_COMMANDS` 한 줄), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `README.md`, `tests/test_docs_m5tc.py` | — | full · 코드 · 토큰을 받고 찍는 CLI(보안) |
| PR5 | `docs/pairing-and-server-identity-guide` | `docs/operating.md`(`pair.key` · 키가 없을 때의 기동 거절과 신원 재설정 절차 · `data_dir` 초기화 뒤 다시 짝짓기 · Docker 와 프록시의 `public_url` · 확실한 거절 뒤 서비스가 되살리지 않는다는 것) · `docs/usage.ko.md` · README 「워커 붙이기」 · 예시 주석 · CHANGELOG 정리 | PR4 머지 | 4 | — | `docs/operating.md`, `docs/usage.ko.md`, `examples/worker.toml`, `examples/client.toml` | `README.md`, `CHANGELOG.md`, `docs/configuration.md`, `tests/test_docs_m5tc.py`, `tests/test_examples.py` | — | short · `src/` 를 바꾸지 않는다 |
| PR6 | `test/m5tc-live-pairing-acceptance` | 실기 하네스 `scripts/acceptance/m5tc_live.sh` · PI31 · PI32 · 보고서 `docs/acceptance/reports/<날짜>-m5tc-acceptance.md` | PR5 머지 | 5 | — | `scripts/acceptance/m5tc_live.sh`(새), `docs/acceptance/reports/`(새 파일) | — | — | short · 실기 그 자체가 증명이고 `src/` 를 바꾸지 않는다 |
| 릴리스 | — | 버전은 M5t-b 와 함께 메인 세션이 정한다 → dev → main(§8) → 태그 | PR6 | — | — | — | — | — | — |

1판은 「`server.py` 와 `cli.py` 는 각각 한 PR 만 고친다」고 적었는데 PR 표와 맞지 않았다(V1-10). 이 판의 규칙은 같은
물결 안에서 같은 `src/` 파일을 고치는 PR 이 없다는 것이다. `server.py` 는 물결 1 의 PR1 이 맡아 고친다. 물결 2 의 PR2
가 `worker_spec_for` 한 곳만 shared 로 고친다. PR2 는 PR1 머지 뒤에 시작한다. `cli.py` 는 물결 2 의 PR3 이 함수 여섯을
shared 로 고치고 물결 3 의 PR4 가 맡는다. PR4 는 PR3 머지 뒤에 시작한다. 물결 2 의 PR2 와 PR3 은 `src/` 파일이 겹치지
않는다. PR2 는 `remote_workers.py` · `capabilities.py` 와 `server.py` 한 곳을, PR3 은 `config.py` · `client.py` ·
`remote_worker.py` · `follow.py` · `cli.py` 를 고친다. 묶음 경로가 겹치지 않는지는 `tools/dev_batch.py` 로 확인한다.

M5t-b 와 겹치는 파일도 있다(F35). PR1 은 M5t-b PR2 와 `store.py` · `server.py` 가 겹치지만 고치는 함수가 다르다. PR3 은
M5t-b PR3 과 `cli.py` 가 겹친다. M5t-b PR3 은 wait · logs 줄을 고치고 PR3 은 다른 함수를 고친다. 나중에 머지하는 쪽이
dev 를 합친 뒤 맞춘다. M5t-a 와는 전체 머지를 시작 조건으로 두었으므로 겹치지 않는다.

## 6. 진행 체크리스트

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 명세 작성(1판 · 2판) · 시나리오 추적 | PR0 | — | [ ] |
| 1 | 격리 반박 라운드(HIGH 없을 때까지) · §12 반영 | PR0 | 0 | [ ] |
| 2 | 결정 페이지(위임): 1 · 2 · 3차 답 반영, 2판의 304~312 확인(메인 세션 + Codex) | — | 1 | [ ] |
| 3 | M5t-b 명세 §4.7 의 종료 3 문장 맞추기(Q-E) | — | 2 | [ ] |
| 4 | PR0 마지막 커밋: PLAN.md · M5n §13 편집 E1~E5 | PR0 | 1 · 2 | [ ] |
| 5 | PR0 머지 | PR0 | 4 | [ ] |
| 6 | M5t-a 전체(PR1~PR7, v0.11.0) 머지 확인 | — | — | [ ] |
| 7 | M5t-b PR1(DB v26 · 세션 · 등록) 머지 확인 | — | — | [ ] |
| 8 | PR1 서버 신원 · 짝짓기 창 · DB v27 | PR1 | 5 · 6 · 7 | [ ] |
| 9 | PR2 증명 · 풀 검사 · spec 거르기 | PR2 | 8 | [ ] |
| 10 | M5t-b PR2(로그 오프셋 · 보고 큐) 머지 확인 | — | — | [ ] |
| 11 | PR3 토큰 전 확인 · 응답 분류 · 리디렉트 · 옛 토큰 + `auto` 거절 | PR3 | 8 · 10 (머지는 9 뒤) | [ ] |
| 12 | PR4 `rcm pair` · `setup --pair` · `token list` | PR4 | 11 | [ ] |
| 13 | mutcheck 변이(§7)가 PR 마다 들어갔는지 | PR1~PR4 | — | [ ] |
| 14 | PR5 문서 | PR5 | 12 | [ ] |
| 15 | PR6 실기 수용 · 보고서 | PR6 | 14 | [ ] |
| 16 | CHANGELOG `[Unreleased]` 정리 | PR5 · 릴리스 | — | [ ] |
| 17 | 운영 설정 확인(Q-B): 다른 머신의 `auto` + 옛 토큰 | — | 15 | [ ] |
| 18 | 릴리스 PR dev→main 개인정보 점검(§8) · 태그 | 릴리스 | 15 · 16 | [ ] |
| 19 | 운영 반영(현행 규칙, 큐가 빌 때 재시작). 반영 전 운영 서버에 `pair.key` 가 생기는 것과 그 백업 자리 확인 | 운영 반영 | 17 · 18 | [ ] |

## 7. 시험 계획

시나리오 표는 설계를 보기 전에 따로 썼다. 1라운드 지적에 맞춰 행을 고치고 더했다. 바꾼 행은 §12 에 있다. 4차 위임
결정에 맞춰 PI11 · PI26 · PI36 의 확인할 결과를 한 번 더 고쳤다(304 · 305 · 312).

<!-- scenario-prefix: PI -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| PI1 | 〔LAN 위장 · 불가역〕 짝지은 워커(`pair_secret` 있음). (a) 기동할 때 설정 URL 의 주인이 위장 서버다 (b) `server = "auto"` 인 짝지은 워커가 도는 중에 연속 연결 실패 5번 뒤 mDNS 로 다시 찾는데 위장 서버만 답한다. 위장 서버는 TXT 를 베꼈고 hello 에 형식은 맞지만 엉터리인 `mac` 을 주며 `/worker/spec` 의 `probes[].argv` 와 claim argv 에 표식 파일을 만드는 명령을 싣는다 | 위장 서버가 받은 요청은 `POST /pair/hello` 뿐이다. `Authorization` 헤더 0건, `/worker/spec` · register · claim · health 0건, 표식 파일 없음. (a) 워커는 `mismatch` 문장(다른 머신이거나 `pair.key` 재설정 — 다시 짝짓는 법)을 남기고 종료 0. (b) 옮기지 않고(`server moved` 줄 없음) 새 claim 없이 다시 시도한다. 로그에 토큰 · `pair_secret` 값은 없다 | e2e · 가짜 mDNS 응답기 + 요청 기록 서버(루프백), 종료 코드 · stderr · 표식 파일 | PR3 |
| PI2 | 〔LAN 위장 · 따라가기〕 같은 위장 서버의 `/api/health` 가 더 높은 `version` 과 표식을 만드는 wheel 의 `client_wheel`(sha256 일치)을 싣는다. 짝지은 워커가 따라가기를 켠 채 기동하고, 짝지은 클라이언트가 같은 서버로 `rcm jobs` 를 친다 | hello 가 `mismatch` 이므로 워커 기동 ①의 health 도 `/client/<wheel>` 받기도 없다. 주입한 가짜 `execve` 호출 0, `$XDG_CACHE_HOME/rcm/clients` 에 새 폴더 0, 워커 종료 0. 클라이언트도 health · wheel 요청 0, 종료 2 | e2e · 요청 기록 서버 + 가짜 `execve`, 캐시 폴더 목록 | PR3 |
| PI3 | 〔옛 토큰 · auto〕 (a) `pair_secret` 없는 워커 설정의 `server` 가 `auto` 또는 빈 값이다 (b) `pair_secret` 없는 클라이언트가 토큰을 가진 채 `server = "auto"` 다 (c) 옛 워커가 명시 URL 이고 서버 주소가 바뀌었다 | (a) `rcm worker` · `rcm worker --check` 가 mDNS 질의를 하나도 보내기 전에 종료 2 로 끝나고 다시 짝짓는 법과 명시 URL 을 쓰는 법을 말한다. (b) 토큰을 보내는 명령이 같은 이유로 종료 2 이고 위장 서버의 요청 기록이 비어 있다. (c) 오늘처럼 그 URL 로만 다시 시도하고 다른 주소로 옮기지 않는다 | e2e · 요청 기록 서버 + 가짜 mDNS, 종료 코드 · 질의 수 | PR3 |
| PI4 | 〔DHCP · 다시 찾기〕 (a) `server = "auto"` 인 짝지은 워커의 진짜 서버가 새 주소로 옮겼다 (b) 옛 주소를 다른 장치(위장)가 받았고 워커는 연결이 끊겼다 돌아와 같은 URL 로 첫 요청을 보낸다 (c) 다시 찾기 후보 가운데 둘이 hello 를 통과한다(같은 `pair.key` 를 가진 복제 서버) · 후보가 9개다 | (a) 연속 연결 실패 5번 뒤 후보마다 hello 를 하고 통과한 새 주소로만 옮긴다(로그 `server moved`). 등록은 새 챌린지 증명으로 되고 claim 이 이어진다. (b) 돌아온 뒤 첫 요청이 hello 이고 위장 장치가 받은 `Authorization` 은 0건이다. hello 를 「mDNS 로 찾았을 때만」 거는 구현은 (b) 에서 실패한다. (c) 둘이 통과하면 옮기지 않고 「복제된 data_dir 은 지원하지 않는다」 로그. 9개면 앞의 8개에만 hello 한다(요청 기록) | e2e · 기록 서버 + 연결 끊김 주입 + 가짜 mDNS, 워커 로그 | PR3 |
| PI5 | 〔LAN 중계 · 재생 — 서버 쪽〕 (a) 위장 서버(다른 IP)가 hello 본문을 그대로 진짜 서버에 중계한다(`dialed` = 위장 서버 주소) (b) 본문의 `dialed` 를 진짜 서버 주소로 고쳐 중계한다 (c) 엿들은 hello 응답을 다음 접속에 재생한다 | (a) 진짜 서버가 421 `host_not_this_server` 로 거절하고 본문에 `mac` · 챌린지가 없다. (b) 응답 `mac` 은 진짜 서버 주소로 계산되어 `verify_hello` 를 머신이 접속한 `dialed` 로 부르면 거짓이다. (c) nonce 생성기를 1000번 부른 값이 모두 다르고, 옛 응답은 새 nonce 로 `verify_hello` 가 거짓이다. hello 처리 중 Store 호출 0(spy). (워커가 토큰을 보내지 않는 것은 PI1 — PR3) | 단위 · 서버 hello 라우트 · `core/pairing.py` `verify_hello`, e2e · 루프백 중계기(서버만) | PR1 |
| PI6 | 〔LAN 중계 · 이름 URL〕 워커 URL 이 이름(`http://<광고 이름>.local:<port>`)이다. 위장 서버가 그 이름을 가로채고 hello 를 진짜 서버 IP 로 본문 그대로 중계한다 | 중계가 통과한다(결정 282 가 받아들인 남는 위험). 시험은 이 중계를 실제로 돌려 통과함을 고정한다. §9 남는 위험 표에 이 줄이 있고, `rcm pair` 가 이름 URL 을 찍을 때와 `rcm setup --pair` 가 이름 URL 을 받을 때 stderr 에 이름 URL 경고가 나온다 | e2e · 중계기 + 이름 해석 주입, stderr | PR4 |
| PI7 | 〔권한 상승 · 불가역〕 worker 창의 코드로 증명을 만들되 본문 `kind` 를 `client` · `admin` · 모르는 값 · 없음으로 바꿔 교환한다. `POST /pair` 에 `kind: "admin"` 을 보낸다 | 넷 모두 같은 404 이고 `tokens` 행이 늘지 않는다(`kind` 가 증명에 들어가므로 바꾸면 증명이 틀린다). `POST /pair` 의 `admin` 은 400 이다. worker 창으로 받은 토큰으로 클라이언트 API 는 403 `worker tokens cannot use the client API` 다 | 단위 · 교환 응답 바이트 + 읽기 전용 DB 사본의 `tokens` 행 수 | PR1 |
| PI8 | 〔동시 · 응답 유실〕 (a) 같은 유효 증명으로 교환 16개를 동시에 보낸다 (b) 서버가 토큰 행을 쓴 직후 응답이 닿기 전에 연결이 끊긴다 | (a) 200 은 정확히 1건, `tokens` 행 +1, 나머지는 같은 404. (b) 창은 쓰였고 워커는 토큰이 없다. `rcm token list` 에 그 이름이 `paired from` 과 함께 보이고 폐기하면 그 토큰은 401 이다. `rcm setup --pair` 는 「발급됐지만 저장하지 못했다 — 코디네이터에서 `rcm token revoke <이름>` 뒤 새 창」을 말하고 종료 1 이다 | 단위 · 스레드 16개 + DB 사본, e2e · 응답 직전 연결 끊기 | PR1 · PR4 |
| PI9 | 〔온라인 무차별 대입 · 방해〕 열린 창에 형식은 맞지만 틀린 증명을 보낸다. 한 출처에서 5번, 다섯 출처에서 한 번씩, 그 뒤 맞는 증명 | 다섯 번째 실패에서 창이 닫히고(출처와 무관하게 창에 센다) 그 뒤 맞는 증명도 같은 404 다. 기다리던 `rcm pair` 는 「틀린 코드 5번으로 창이 닫혔다 — 새 창을 연다」를 찍고 종료 1, 서버 로그에 같은 이유 한 줄. 증명 비교가 `hmac.compare_digest` 를 부른다(spy). 시험이 5 ÷ 32^10 을 계산해 명세 숫자(≈ 4.4e-15)와 대조한다 | 단위 · 교환 응답 · spy, e2e · `rcm pair` stdout · 종료 코드 | PR1 · PR4 |
| PI10 | 〔정찰〕 익명으로 틀린 증명 · 형식이 틀린 증명 · 닫힌 창 · 연 적 없는 창 · 만료된 창 · 이미 쓴 코드 · 다른 kind 로 교환한다. 창이 열린 동안과 닫힌 동안 `/api/status` · `/events` · `/api/machines` · `/api/health` · mDNS 응답을 읽는다 | 일곱 경우의 상태 코드 · 헤더 이름 · 본문 바이트가 같다. 같은 시각 · 같은 표본을 주입하면 읽기 라우트 응답과 `Responder.response()` 바이트가 창이 열렸을 때와 닫혔을 때 같다. TXT 키는 `v` · `name` · `lanes` 셋뿐이다 | 단위 · 응답 바이트 비교 | PR1 |
| PI11 | 〔열어 둔 창 · 시계 · 서버를 잃음〕 창을 열고 둔다. TTL 이 지난다. 서버를 재시작한다. 서버를 멈추고 만료 + 30초가 지나도록 띄우지 않는다. 코디네이터가 reload 한다. `--ttl 61`. 기다리는 `rcm pair` 에 Ctrl-C(서버가 살아 있을 때와 죽었을 때). 워커 시계가 1시간 어긋나 있다 | 만료는 서버 시계로만 판정한다(TTL 안 성공 · 뒤 404). 재시작하면 창이 사라져 교환이 404 이고, 기다리던 `rcm pair` 는 다시 닿았을 때 자기 `id` 가 없음을 보고 종료 3 이다(창이 쓰이지 않았다는 증명이 없으면 1 로 보고하지 않는다, 312). 서버가 만료 + 30초까지 돌아오지 않으면 종료 3. Ctrl-C 는 `POST /pair/cancel` 을 보내고 130(서버가 죽었으면 「취소하지 못했다 — <시각>에 닫힌다」 줄과 130). reload 뒤 `GET /pair` 의 `expires_at` 이 같다. `--ttl 61` 은 `rcm pair` 종료 2(서버는 400). `rcm pair --cancel --worker` 로 바로 닫힌다 | 단위 · 주입 시계, e2e · 재시작 · 정지 · SIGINT | PR4 |
| PI12 | 〔창 둘 · 이름〕 (a) 같은 kind 창을 연 채 하나 더 연다 (b) worker 창과 client 창을 함께 연다 (c) 이미 있는 이름(폐기된 이름 포함)으로 연다. `--name` 없이 `host_name` 이 `Dev's MacBook Pro` · 65자 · 한글뿐 · 제어문자와 줄바꿈 포함이다 | (a) 두 번째 `rcm pair` 는 409 로 종료 1 이고 남은 시간과 `rcm pair --cancel` 을 말한다. 첫 창의 코드는 그대로 유효하다. (b) 둘 다 열리고 한 kind 의 코드는 다른 kind 창에 404 다. (c) `--name` 의 이름이 있으면 창을 열 때 409 `name_taken`, `host_name` 에서 나온 이름이 있으면 교환이 409 `name_taken` 이고 창은 열린 채다. 이름은 `Dev-s-MacBook-Pro` · 64자 · `worker-<hex6>` 처럼 `_NAME_RE` 에 맞게 바뀐다. 서버 로그 · `GET /pair` · `rcm pair` 완료 줄 · `rcm token list` 에 제어문자 · 줄바꿈이 0개다(정리한 값만) | 단위 · `clean_pair_label`, e2e · `rcm pair` 종료 코드 · 로그 줄 수 · `rcm token list` | PR1 · PR4 |
| PI13 | 〔틀린 풀〕 풀이 묶인 워커가 (a) 등록 본문의 `pool` 을 다른 풀로 보낸다 (b) worker.toml 의 `pool` 을 고치고 다시 시작한다 (c) 묶인 풀로 등록한다 | (a) (b) 등록이 409 `pool_mismatch` 와 `bound_pool` 이고 세션 행 · 펜싱 · `workers` 행 · 그 이름의 도는 잡이 바뀌지 않는다. 워커는 묶인 풀과 고치는 법을 로그에 적고 종료 2. (c) 지금처럼 등록된다 | 단위 · `POST /worker/register` + DB 사본, e2e · 워커 종료 코드 | PR2 · PR3 |
| PI14 | 〔다른 풀 비밀〕 풀 X 에 묶인 worker 토큰과 기본 풀 worker 토큰이 각각 `GET /worker/spec` 을 읽는다. 기본 풀 프리셋의 `env` 에 값이 있다 | X 토큰의 `presets` 에는 `pool`/`pools` 에 X 가 있는 프리셋과 `any-capable` 프리셋만 있고, 기본 풀 프리셋의 이름 · `env` 값은 응답 전문에 없다(grep). `local_only` 프리셋은 어느 토큰에도 없다 | 단위 · `/worker/spec` JSON 전문 | PR2 |
| PI15 | 〔기본 풀 · 불가역〕 `--pool` 없이 연 창으로 짝지은 워커가 기본 풀에 들어온다. 서버에는 `local_only = true` 인 배포 흉내 프리셋의 대기 잡과 기본 풀의 평범한 게이트 잡이 있다 | 그 워커는 게이트 잡을 받고, `local_only` 잡에는 claim 이 204 이며 그 잡은 `queued` 로 남는다. 짝짓기가 결정 254 의 후보 함수를 우회하는 길이 claim · spec 어디에도 없다 | e2e · 시험 서버 설정의 가짜 `sh -c` 프리셋 둘, 잡 상태 | PR2 |
| PI16 | 〔풀 NULL〕 v27 전에 만든 명시 토큰(`tokens.pool` NULL)으로 (a) 등록 · claim · finish 한다 (b) 등록한 뒤와 등록 전에 `/worker/spec` 을 읽는다 (c) `rcm token list` 를 본다 (d) `verify_token` 이 돌려준 `TokenInfo` 를 본다 | (a) 마이그레이션 직후에도 오늘과 같다. (b) 등록한 뒤에는 그 워커가 등록한 풀로, 등록 전에는 질의 `pool`(없으면 `default`)로 거른다. 거르지 않은 전체가 나오는 길은 없다. (c) `pool` 열이 「—」다. (d) `pool` · `pair_id` 가 None 이고 짝지은 토큰이면 DB 값과 같다 | 단위 · 마이그레이션한 DB 사본 + `/worker/spec` · `rcm token list` · `verify_token` | PR2 · PR4 |
| PI17 | 〔비밀 · 공개 레포〕 짝짓기의 모든 길(성공 · 틀린 증명 · 닫힌 창 · hello 의 다섯 분류 · `rcm pair` · `rcm setup --role worker --pair` · `--role client --pair`)을 돈다 | 코드 · 코드 키 · 토큰 · `pair_secret` · `pair.key` 값이 stdout · stderr · 서버 로그 · 워커 로그 · `events` 표 · `/api/*` 응답 어디에도 0건이다(코드는 `rcm pair` 가 창을 열 때 stdout 에 한 번만). PR1: 서버 로그 · 응답 · `pair.key` 가 umask 가 느슨해도 만들 때부터 0600, 0644 `pair.key` 는 기동이 종료 2. PR3: 워커 로그, 0644 로 바꾼 `worker.toml` · `client.toml` 의 `pair_secret` 로드 거절. PR4: `rcm pair` · setup 출력, 코드는 프롬프트 · stdin 으로만 받고 `--code` 는 종료 2, 만든 `worker.toml` · `client.toml` 이 0600 | 단위 · 전체 grep · `stat` 모드 | PR1 · PR3 · PR4 |
| PI18 | 〔DB 사본〕 서버 DB 사본(마이그레이션 백업 · 읽기 전용 열기)으로 서버 신원을 만들려 한다 | 사본에는 `tokens.pair_id` 와 토큰 sha256 만 있고 `pair_secret` 도 `pair.key` 도 없다. 사본만 둔 `data_dir` 의 서버는 살아 있는 짝 토큰이 있어 기동을 거절한다(PI35). 사본의 짝 토큰을 폐기하고 새 `pair.key` 로 띄운 서버의 hello 는 짝지은 워커의 검증을 통과하지 못한다(`mismatch`) | 단위 · 읽기 전용 사본 · 파일 모드, e2e · 키를 바꾼 서버 | PR1 |
| PI19 | 〔폐기 · 다시 짝짓기〕 (a) `rcm token revoke <worker>` 를 도는 짝지은 워커에 건다 (b) `rcm pair --worker --name <그 이름> --replace` 로 다시 짝짓는다 (c) `pair.key` 를 재설정한 서버에 옛 짝지은 워커가 붙는다 | (a) 폐기한 순간부터 `/worker/*` 호출이 모두 401 이고, 워커는 새 claim 을 멈추고 도는 잡이 끝나면 「토큰이 폐기 · 교체됨 — 다시 짝짓기」를 남기고 종료 0. 보고하지 못한 잡은 M5t-b 의 유예(`disconnect_grace_seconds`) 뒤 `lost`. (b) 교환이 그 행을 한 문장으로 바꿔(새 sha256 · `pair_id`, `revoked_at` NULL) 옛 토큰은 401, 활성 토큰은 정확히 1개이고 둘 다 유효하거나 둘 다 무효인 순간이 없다. (c) hello 가 `mismatch` 라 붙지 않고 종료 0(PI1 의 문장) | 단위 · 호출별 상태 + DB 사본, e2e | PR4 |
| PI20 | 〔익명 라우트〕 PLAN 「서버 API」 표의 라우트 전체를 `Authorization` 없이 모든 메서드로 친다. 새 익명 라우트 둘에 1 KiB 를 넘는 본문 · 깨진 JSON · 동시 요청 상한 초과를 보낸다 | 익명 요청에 401 · 403 이 아닌 답을 하는 라우트 집합이 PLAN 「보안」 절이 적은 집합(원래 익명인 라우트 · `read_auth = none` 읽기 라우트 · `POST /pair/hello` · `POST /pair/exchange`)과 정확히 같다(PLAN.md:243 의 예외 문장은 PR0 이 쓰고 이 시험이 잠근다). 집합은 PLAN 표를 파싱해 얻는다. `/worker/*` · `POST /pair` · `GET /pair` · `POST /pair/cancel` 은 401 이다. 새 라우트는 413 · 400 · 503 으로 답하고 hello 는 Store 를 부르지 않는다 | 단위 · PLAN 표 순회 + 문서 잠금, e2e · 동시 연결 | PR1 |
| PI21 | 〔엿보기 · TLS 없음〕 같은 망의 수동 관찰자가 (a) 짝짓기 교환(요청의 증명 · 응답의 토큰 · `pair_secret`) (b) 그 뒤의 `Authorization` 요청을 평문으로 본다. 캡처한 교환 요청을 정당한 교환 뒤에 다시 보낸다 | `rcm setup --pair` 가 보낸 요청 바이트에 코드가 0번 나온다(입력한 꼴 · 대문자 · `-` 없는 꼴 모두). 다시 보낸 요청은 404 다(창은 쓰였다). 엿본 토큰 · `pair_secret` 으로 사칭할 수 있다는 것과 엿본 증명으로 창이 열린 동안 오프라인 추측이 된다는 것(M4)은 결정 282 · 301 의 남는 위험이고 §9 표에 이름으로 있다. `rcm pair` 가 찍는 URL 의 호스트가 루프백도 100.64.0.0/10 도 아니면 stderr 에 평문 경고가 나오고 `rcm setup --pair` 도 같은 경고를 낸다 | 단위 · 경고 함수, e2e · 캡처한 요청 바이트 · 재전송 | PR4 |
| PI22 | 〔bind · 주소 · 스킴〕 서버 bind 가 (a) `127.0.0.1`(`public_url` 없음) (b) 특정 LAN IP (c) `0.0.0.0` (d) Tailscale 대역 IP + 이름 `public_url` (e) `0.0.0.0` + 광고 켬 (f) 루프백 bind + `public_url = https://<이름>`(TLS 프록시 뒤) 일 때 창을 열고 머신이 찍힌 URL 로 hello · 교환한다 | (a) `POST /pair` 가 409 `pair_unreachable` 이고 `rcm pair` 는 「다른 머신이 닿지 못한다 — bind 를 바꾸라」로 종료 1, 창은 열리지 않는다. (b)~(f) 찍힌 URL 마다 hello 가 `ok` 다. (f) 는 `dialed = https://<이름>:443` 이 `public_url` 과 맞는다(1판의 `:80` 판정이면 421). `0.0.0.0` 은 URL 에 나오지 않는다. 찍히지 않은 이름(`public_url` 없는 MagicDNS 이름 등)은 421 이고 머신 메시지가 `public_url` 로 고치는 법을 말한다 | 단위 · `own_dialed_set` 에 주입한 소켓 주소 · `public_url`(PR1), e2e · `rcm pair` 의 URL 목록 · 별칭 루프백 127.0.0.2 + 주입한 `public_url`(실제 tailnet 은 쓰지 않는다, PR4) | PR1 · PR4 |
| PI23 | 〔탈취 토큰 · 등록〕 (a) 로그 · `ps` 에서 샌 짝지은 worker 토큰만 가진 다른 머신이 그 이름으로 증명 없이 · 틀린 증명 · 이미 쓴 챌린지 · 120초 지난 챌린지 · 5초 넘게 미래인 챌린지 · 다른 `pair_id` 의 챌린지 · 다른 세션 id 로 만든 증명으로 등록한다 (b) 같은 토큰으로 등록 없이 claim · heartbeat · `/worker/spec` (c) 옛 명시 토큰(`pair_id` NULL)으로 다른 머신이 등록 (d) 클라이언트 API · `/pair` 관리 라우트 (e) 다른 워커의 잡 트리 · 로그 · finish (f) `worker.toml` 을 통째로 복사(토큰 + `pair_secret`)한 머신이 등록 | (a) 403 `pair_proof_required` 또는 `pair_proof_invalid`(`reason` 이 경우마다 맞다)이고 세션 행 · 펜싱 · 능력 보고 · 잡이 그대로다 — 정당한 워커의 세션과 도는 잡은 `running` 그대로. (b) claim · heartbeat 은 409 `not_registered`(M5t-b), `/worker/spec` 은 묶인 풀만 준다. (c) 오늘처럼 등록되고 정당한 세션이 펜싱된다(옛 토큰은 증명이 없다). 앞 등록과 다른 주소면 서버 로그에 두 주소가 한 줄 남는다. (d) 403. (e) 오늘처럼 404 · 403 `not your job` · 409. (f) 등록이 성공하고 정당한 세션이 펜싱된다 — 결정 302 의 남는 위험으로 고정하는 시험이다 | 단위 · 호출별 상태 + DB 사본, e2e · 워커 프로세스 둘 | PR2 |
| PI24 | 〔클라이언트〕 (a) `rcm pair --client` 창 + `rcm setup --role client --pair` (b) 받은 `client.toml` (c) 짝지은 클라이언트의 서버 URL 을 위장 서버가 차지했다(`rcm run` 과, 잡을 낸 뒤 다시 연결하는 `rcm wait`) (d) 토큰 없는 클라이언트가 `server = "auto"` 로 따라가기를 켠 채 위장 서버를 찾는다 | (a) kind 는 `client`(admin 아님), `rcm run` 이 되고 `/worker/*` 는 403, 풀은 NULL. (b) 0600 이고 `server` · `token` · `pair_secret` 이 있다. (c) 위장 서버가 받은 요청은 hello 뿐이고 `rcm run` 은 종료 2(트리 업로드 · wheel 실행 0), `rcm wait` 은 종료 3. (d) mDNS 찾기는 하지만 검증된 `pair_secret` 이 없으므로 위장 서버가 받은 `/client/<wheel>` 요청이 0건이고 주입한 가짜 `execve` 호출도 0이다(Q-A · 결정 300) | 단위 · 토큰 행 kind, e2e · 기록 서버 + 가짜 `execve` | PR3 · PR4 |
| PI25 | 〔코디네이터 화면〕 (a) 창을 열 때 (b) 워커가 붙었을 때 (c) 엉뚱한 머신이 붙었을 때 (d) `--pool` 에 오타를 썼을 때 | (a) 코드(이 화면에서만) · URL · 만료 시각 · kind · 풀 · 이름 · 그 풀로 갈 프리셋 수를 찍는다. (b) 완료 줄에 이름 · kind · 풀 · 정리한 `host_name` · 서버가 본 접속 주소 · 시각과 다음 할 일(`rcm machines`, `rcm token revoke <이름>`)이 있다. 워커 쪽 `rcm setup --pair` 는 붙었다 · 풀 · 받을 프리셋 수 · 결과는 코디네이터로 가고 요청자의 `rcm wait` 가 받는다를 찍는다. 코드 입력 뒤 두 번째 승인은 없다. (c) 그 줄의 `host_name` · 주소와 `rcm token list` 의 `paired from` 으로 알아보고 `rcm token revoke` 한 번으로 끝난다. (d) 갈 프리셋이 0개면 창을 열 때 stderr 에 「0 presets reach pool …」 경고를 내고 창은 연다 | e2e · `rcm pair` stdout · stderr · `rcm token list` | PR4 |
| PI26 | 〔짝짓기를 쓰지 않음 · 섞인 판〕 v27 서버에서 `rcm token add NAME --worker` → `RCM_WORKER_TOKEN` → `rcm worker --check` → `rcm worker`(명시 URL). 옛 워커 ↔ 새 서버, 옛 빌드의 `rcm worker --check`(질의 `pool` 없음, 기본 풀이 아닌 워커) ↔ 새 서버, 새 빌드의 옛 토큰 워커 ↔ 옛 서버, 짝지은 워커 ↔ 옛 서버(hello 404) | stdout 은 토큰 한 줄뿐이고 안내는 stderr, 종료 0. 등록 · claim · heartbeat · finish · 산출물 · 로그가 그대로다. 새 빌드 `rcm worker --check` 의 기존 행 이름 · 종료 코드가 같고 더해진 `server identity` 행은 `warn` 「not paired」다. 옛 `--check` 는 등록 전이면 `default` 풀 프리셋을, 등록 뒤면 등록 풀 프리셋을 받는다. 짝지은 워커는 hello 가 404 인 옛 서버에 토큰을 보내지 않고, 종료하지 않고 물러서며 다시 시도한다(304). 같은 주소의 경고는 속도를 제한한 간격으로 되풀이되고 `rcm worker --check` 에 서버 신원 미확인이 보인다 | e2e · 두 프로세스 · 종료 코드 · stdout 과 stderr 분리 · 요청 기록 | PR3 |
| PI27 | 〔같은 코드를 지나는 다른 길〕 `authenticate` · `verify_token` · `tokens` 표를 쓰는 길 전부: client 토큰 `rcm run · wait · cancel · jobs · top · machines`, admin 토큰 `rcm pause · resume · reload · gc · recheck`, `read_auth = basic` 의 Basic 읽기, `/api/whoami`, SSE `/events`, 보관함, 릴리스 드라이버 토큰 발급 · 폐기, `rcm token list`, `rcm discover`, `rcm check` | 길마다 상태 코드 · 종료 코드 · JSON 키가 변경 전과 같다(`TokenInfo` 칸이 늘어도). `rcm token list` 의 앞 네 열(name · kind · created · revoked)의 자리와 문구가 같고 `pool` · `paired from` 열은 뒤에 붙는다. `/api/whoami` · `rcm discover --json` 의 키가 그대로다. worker 토큰으로 읽기 라우트는 403, Basic 은 쓰기에서 거절된다 | 단위 · 길마다 한 호출 | PR1 · PR4 |
| PI28 | 〔마이그레이션 · 운영 DB〕 v25 · v26 사본(폐기된 토큰 · 등록된 워커 행 · 드라이버 토큰)을 새 빌드로 연다. v26 빌드로 v27 DB 를 연다. `rcm pair` 를 DB 없는 `data_dir` 의 서버 설정 + 서버 URL + admin 토큰으로 돌린다 | 마이그레이션 뒤 기존 토큰이 같은 이름 · kind · 폐기 상태로 검증되고 새 칸은 NULL, `workers` 행이 그대로다. 옛 빌드는 `newer_database_message` 로 거절하고 DB 를 바꾸지 않는다. `rcm pair` 는 DB 를 열지 않고 그 `data_dir` 에 파일이 생기지 않는다. 가드의 `DB_OPENING` 은 `token` 하나 그대로다 | 단위 · 사본의 `user_version` · 읽기 전용 열기 | PR1 · PR4 |
| PI29 | 〔문서 · 공개 레포〕 PLAN 「보안」 · 「서버 API」 · README · `docs/configuration.md` · `docs/operating.md` · 예시 · 시험 fixture | PLAN 「보안」 절의 익명 예외 문장이 PI20 의 집합과 같다(문서 잠금). README 발견 문장은 TXT 가 그대로라 그대로다. `docs/operating.md` 에 `pair.key` · 키가 없을 때의 기동 거절 · 신원 재설정(폐기 → 키 삭제 → 시작 → 다시 짝짓기) · `data_dir` 초기화 뒤 다시 짝짓기 · Docker 와 프록시의 `public_url` · 확실한 거절 뒤 서비스가 되살리지 않는다는 것이 있다. 예시 · fixture 에 실제 토큰 · 코드 · `pair_secret` · 사설 IP · 호스트 이름 · 64자 hex 리터럴이 0건이다(시험 값은 실행 중에 만들고 주소는 127.0.0.x · 192.0.2.x 만) | 단위 · 문서 잠금 · gitleaks | PR1 · PR5 |
| PI30 | 〔변이〕 §7 변이 목록의 변이를 하나씩 넣는다 | 변이마다 표에 적은 그 PR 의 시험이 빨개진다. 하나라도 초록이면 그 행의 시험이 약하다 | 변이 · `scripts/mutcheck.py` | PR1 · PR2 · PR3 · PR4 |
| PI31 | 〔실기 · 짝짓기 끝까지〕 시험 서버(자기 설정 · 포트 · `data_dir`, `advertise = false`)에서 `rcm pair --worker` → 다른 `HOME` · `data_dir` 의 일회용 워커가 `rcm setup --role worker --pair` → `rcm worker` → 기본 풀 가짜 프리셋 잡 → `rcm token revoke` | 워커가 붙어 잡을 끝내고 요청자의 `rcm wait` 가 0 으로 끝난다. 코디네이터와 워커의 완료 줄이 PI25 대로다. 폐기 뒤 워커 호출이 401 이고 워커는 종료 0 이다. 모든 로그와 교환 요청 캡처에 코드 · 토큰 · `pair_secret` 이 0건이다 | 실기 하네스 (live) | PR6 |
| PI32 | 〔실기 · 위장과 웹〕 같은 시험 서버에서 별칭 루프백 주소에 위장 서버를 띄워 짝지은 워커의 URL 을 그리로 돌린다. 웹 UI 토큰 입력 · SSE 를 CDP 하니스로 본다 | 위장 서버 요청 기록에 hello 만 있고 워커는 `mismatch` 문장과 함께 종료 0 이다. 웹의 토큰 입력 · 큐 갱신이 변경 전과 같고 CDP 오류 수집기가 비어 있다 | 실기 하네스 · CDP (live) | PR6 |
| PI33 | 〔wire 바이트 · 골든〕 4.2.1 의 인코딩 · 형식 · 주소 정규화를 잠근다: `enc` 바이트 · `pair_mac` · `derive_pair_secret` · 챌린지 만들기와 검증(120초 지남 · 5초 넘게 미래 · 한 글자 바꿈) · 형식 검사(대문자 hex 의 `mac` · 31자 `pair_id` · 63자 nonce) · `canonical_dialed` 표 · hello MAC 이 챌린지를 덮음 | `enc("rcm-hello-v1", "ab")` 가 4.2.1 의 22바이트와 같다(리터럴은 이 낮은 엔트로피 바이트뿐). `pair_mac` 결과가 시험 안의 독립 구현(`struct.pack(">I", …)` + `hmac.new`)과 같다. 챌린지는 경계 안에서만 참. 형식이 틀린 값은 모두 거짓이고 서버 hello 는 400 `bad_hello`. `canonical_dialed` 가 4.2.1 표의 행마다 그 결과다(`https` 443 · IPv4-mapped · zone id 포함). 응답의 챌린지를 다른 유효 챌린지로 바꾸면 `verify_hello` 가 거짓이다(V1-13) | 단위 · `core/pairing.py` 순수 함수(시계 · 난수는 인자) | PR1 |
| PI34 | 〔교환의 능동 사칭자 · 불가역〕 `rcm setup --role worker --pair` 에 넣은 URL 의 주인이 (a) 가짜 교환 서버: 형식은 맞지만 코드를 모르고 만든 `mac` 의 200 을 준다 (b) 응답의 `kind` 를 `admin` 으로 바꾼 200 을 준다 (c) 진짜 서버 | (a) (b) `worker.toml` 이 생기지 않고(파일 0) 종료 1 「answered but does not know this code — nothing was saved」. 가짜 서버가 받은 요청 바이트에 코드가 0번 나온다. (c) 진짜 서버는 증명이 맞으면 응답 `mac` 이 워커 검증을 통과하고, 형식은 맞는데 틀린 증명은 창의 실패 1 로 센다 | 단위 · 교환 증명 · 응답 MAC(PR1), e2e · 가짜 교환 서버 + 파일 목록(PR4) | PR1 · PR4 |
| PI35 | 〔`pair.key` 수명 주기 · 불가역〕 (a) `pair.key` 를 지웠는데 폐기되지 않은 짝 토큰이 있는 DB 로 `rcm serve` (b) 짝 토큰을 모두 폐기한 뒤 같은 `rcm serve` (c) 같은 `data_dir` 에서 키 만들기를 여러 스레드 · 프로세스가 동시에 하고, 쓰기 도중에 다른 쪽이 읽는다(쓰기 사이에 멈춤 주입) (d) `pair.key` 가 심볼릭 링크 · 0644 · 31바이트 | (a) 종료 2 `cannot start server: … pair.key is missing but N paired tokens are still active — restore … or reset …` 이고 `pair.key` 가 생기지 않는다(조용히 새 키 0). (b) 새 키(32바이트 · 0600)를 만들고 뜬다. (c) 모두 같은 32바이트를 읽고, 0바이트나 일부만 쓴 `pair.key` 를 읽는 순간이 없으며 임시 파일이 남지 않는다. (d) 셋 모두 종료 2 이고 문장에 값이 없다 | 단위 · `load_or_create_pair_key` + DB 사본 + 멈춤 주입, e2e · `rcm serve` 종료 코드 | PR1 |
| PI36 | 〔응답 분류 · 종료 코드〕 짝지은 워커가 기동 때와 도는 중에 hello · 토큰 요청에서 (a) `mismatch` (b) 421 `host_not_this_server` (c) 확인 뒤 401 (d) `pair_proof_invalid` `pair_mismatch` (e) 연결 실패 · 503(동시 처리 상한) · 500 · 429 (f) 404 · 307 · 깨진 200 · `v` 2 (g) `pair_proof_invalid` `expired` 를 받는다. 도는 중에는 잡 하나가 돌고 있다 | 기동: (a)~(d) 분류별 문장 한 줄과 종료 0. (e) (f) 종료하지 않고 5 → 10 → 20 → 40 → 60초로 다시 hello 하며 토큰 0 이다. 같은 주소 · 분류의 경고는 한 줄로 끝나지 않고 속도를 제한한 간격으로 되풀이되며 `rcm worker --check` 와 `rcm doctor` 에 서버 신원 미확인 상태가 보인다(304). (g) 새 hello 뒤 한 번 더 등록한다. 도는 중: (a)~(d) 새 claim 0 · 토큰 0, 도는 잡은 끝까지 돌고 잡이 0 이 되면 종료 0. 그동안 신원 때문에 멈춘 상태가 로그에 계속 보인다(305). (e) (f) 도는 잡이 계속 돌고 종료하지 않는다. 확인된 뒤 heartbeat 이 503 을 받아도 다시 hello 하지 않고 heartbeat 이 이어진다(V2-10). 프로토콜 불일치(M5t-b)는 여전히 종료 3 | e2e · 응답을 주입하는 기록 서버 + 가짜 시계, 종료 코드 · 로그 줄 수 · 요청 기록 | PR3 |
| PI37 | 〔리디렉트〕 짝지은 머신의 (a) hello (b) 토큰 요청(`Client._request` · `download_tree` · `download_bundle` · `events`)에 서버가 다른 호스트(루프백 다른 포트)로 307 · 302 를 준다. 같은 시험을 옛 토큰 머신으로 | (a) hello 는 리디렉트를 따르지 않고 `other` 로 분류된다. (b) 리디렉트 대상 호스트가 받은 요청 0건, 그쪽이 받은 `Authorization` 0건. 옛 토큰 머신은 오늘 동작 그대로(결과를 기록해 고정) | e2e · 루프백 서버 둘 · 요청 기록 | PR3 |
| PI38 | 〔코드 입력〕 `rcm setup --pair` 에 `abcde-fghjk`(소문자 · 대시) · `ABCDE FGHJK` · `ABCDE-FGH0K`(0) · `ABCDE-FGH1K` · 9자 · 11자를 넣는다. 서버에는 그 kind 의 창이 열려 있다 | 소문자 · 대시 · 공백은 정규화되어 교환이 된다. 0 · 1 · 8 · 9 가 든 코드와 길이가 틀린 코드는 요청을 보내지 않고(서버 요청 기록 0) 종료 2 와 「A–Z 와 2–7 의 10자 — 0 · 1 · 8 · 9 는 쓰지 않는다」 문장이고, 창의 실패 수가 그대로다(그 뒤 맞는 코드 5번째 시도까지 창이 열려 있다) | e2e · `rcm setup --pair` + 요청 기록 서버 · `GET /pair` | PR4 |

Excluded (rare): 같은 `pair.key` 를 가진 `data_dir` 복제 서버 둘이 한 망에서 동시에 답함 · ① 백업 복원 + 원본 가동 + 같은 망 · 결정 289 가 지원하지 않는다고 문서화하고, 다시 찾기에서는 PI4 (c) 가 「옮기지 않음」을 본다 · reports searched, 0 hits
Excluded (rare): 교환 도중 SIGKILL 로 창의 실패 수가 사라짐 · ② · 창은 메모리라 재시작이 창을 닫는다(PI11), 되돌릴 수 있다 · reports searched, 0 hits
Excluded (rare): `--replace` 교환 둘이 같은 순간에 겹침 · ①②(같은 이름 창은 kind 마다 하나라 두 창이 동시에 있을 수 없고, 겹치려면 창 둘 + 동시 교환) · PI19 가 활성 토큰 1개를 본다 · reports searched, 0 hits
Excluded (rare): 챌린지 120초 안에 서버 벽시계가 5초 넘게 뒤로 감 · ③ OS 시계 계약 · 결과는 등록 403 `expired` 한 번 뒤 다시 hello(PI36 g), 되돌릴 수 있다 · reports searched, 0 hits
Excluded (rare): 9개 넘는 가짜 mDNS 인스턴스로 진짜 서버를 후보 밖으로 밀어내는 동시에 진짜 서버의 주소가 바뀜 · ① · 결과는 옮기지 않음(토큰 0, 서비스 거부만, 되돌릴 수 있다), 후보 상한 자체는 PI4 (c) · reports searched, 0 hits

이력 검색은 `git log -i --grep` 와 `CHANGELOG.md` · `docs/acceptance/reports/` 를 `pair` · `spoof` · `impostor` ·
`pair.key` · `replace` · `SIGKILL` · `redirect` 로 찾았다. `redirect` 는 M5q · M5p 가 이미 다룬 함정이다.
`follow.py:648-656` 의 주석이 「토큰을 다른 호스트로 실어 나르지 않는다」고 적었고 `docs/m5q-workplan.md:643` 도 같은
함정을 다뤘다. 이미 보고된 함정은 흔한 것으로 보고 PI37 행으로 넣었다. 나머지 맞은 줄은 다른 기능의 것이다.

### 변이

`scripts/mutcheck.py` 에 아래 변이를 더한다(PI30). 「빨개질 시험」은 그 PR 안의 시험이다(V1-7).

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
| `register-pool-check-skipped` | 등록의 풀 검사를 지움 | PI13 | PR2 |
| `spec-null-pool-unfiltered` | 풀 NULL 이면 거르지 않음 | PI16 · PI14 | PR2 |
| `register-proof-skipped` | 증명 검사를 지움 | PI23 | PR2 |
| `pair-challenge-reusable` | 쓴 챌린지 집합을 지움 | PI23 | PR2 |
| `register-proof-unbound-session` | 증명 MAC 에서 세션 id 를 뺌 | PI23 | PR2 |
| `pair-gate-skipped-in-download` | F19 의 `download` 자리에서 관문을 건너뜀 | PI1 · PI2 | PR3 |
| `pair-gate-ignores-verify-result` | 관문이 `verify_hello` 결과를 보지 않고 「확인됨」 | PI1 | PR3 |
| `rediscover-unverified-move` | 다시 찾은 주소로 hello 없이 옮김 | PI1 · PI4 | PR3 |
| `legacy-auto-not-refused` | 옛 토큰 + `auto` 거절을 지움 | PI3 | PR3 |
| `paired-follows-redirect` | 짝지은 머신이 따르는 오프너를 씀 | PI37 | PR3 |
| `identity-mismatch-retried` | `mismatch` 를 재시도로 분류(종료 0 대신) | PI36 | PR3 |
| `transient-hello-exits` | `unavailable` 에서 종료 | PI36 | PR3 |
| `exchange-response-mac-not-checked` | setup 이 응답 MAC 을 보지 않고 저장 | PI34 | PR4 |
| `exchange-sends-code` | 교환 본문에 코드를 실음 | PI21 · PI34 | PR4 |
| `code-format-not-prechecked` | 형식 검사 없이 보냄 | PI38 | PR4 |

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

## 9. 위험

이 설계가 막는 위험과 막는 법이다.

| 위험 | 막는 법 | 결정 · 시나리오 |
|---|---|---|
| 위장 서버가 짝지은 워커의 토큰을 받고 argv 를 돌린다 | 관문이 F19 여섯 자리 모두에 있고 hello 가 `ok` 가 아니면 토큰 0. 리디렉트를 따르지 않는다 | 290 · 295 · 309 · PI1 · PI2 · PI37 |
| 교환 URL 의 사칭자가 워커를 영구히 속인다 | 코드를 보내지 않는 교환과 코드 키 응답 MAC(저장 전 검사) | 301 · PI34 |
| 확실한 거절의 종료 0 을 서비스가 되살리지 않아 워커가 멈춰 있다(일부러) | 문장이 이유와 다시 짝짓는 법을 말한다. `rcm machines` 에서 레인이 down. 운영 문서(PR5) | 303 · 305 · PI36 |
| 신원을 증명하지 못하는 응답(`other`)이 계속되면 워커가 끝없이 다시 시도한다 | 토큰 0, 60초 상한. 경고를 속도 제한해 주기적으로 되풀이하고 `rcm worker --check` · `rcm doctor` 에 서버 신원 미확인 상태를 드러낸다. 진짜 서버가 돌아오면 붙는다 | 304 · PI26 · PI36 |
| 부모 명세 M8 「되살리지 않는 종료 코드」와 어긋남(V1-4) | M8 을 「펜싱(M5t-b) · 확실한 신원 거절(이 명세)」로 좁힌다. 연결 실패 · 5xx 는 종료가 아니라 프로세스 안 재시도라 되살림 루프가 없다. 프로토콜 불일치는 M5t-b 대로 3 | 303 |
| hello 가 동시 처리 상한을 지나 503 을 받고 heartbeat 이 멈춘다(V2-10) | 503 은 `unavailable`(재시도, 종료 없음). 확인된 뒤의 503 은 다시 hello 를 부르지 않는다 — hello 는 연결 실패 뒤에만. heartbeat 이 멈춰도 M5t-b 에서 `worker_timeout` 은 레인 down 만이고 잡은 `disconnect_grace_seconds`(600) 유예 뒤에야 `lost`(F37) | 303 · F39 · PI36 |
| 옛 토큰 + `auto` 워커 · 클라이언트가 업그레이드 뒤 멈춘다(의도된 거절) | 운영 설정을 반영 전에 확인(Q-B · §6). CHANGELOG `Changed` | 283 · 285 · PI3 |
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

### 남는 위험

이 설계로 막지 않는 위험이다. 행마다 이 명세가 대신 하는 일을 적었다.

| 남는 위험 | 이 명세가 하는 것 | 결정 |
|---|---|---|
| 같은 망의 평문 엿보기: 교환 응답의 토큰 · `pair_secret`, 요청의 토큰 · 세션 id | 막지 않는다. 코드는 망으로 나가지 않고 한 번 · 짧은 수명. LAN · `0.0.0.0` URL 경고, Tailscale 권고 | 282 · PI21 |
| 엿본(또는 교환 URL 의 사칭자가 받은) 교환 증명으로 하는 오프라인 코드 추측 | 막지 않는다. 5회 제한은 온라인만. 숫자 M4(한 코어 38.7년, 10분 안에 절반은 약 100만 코어, GPU 는 재지 않음). 맞힌 코드는 그 창이 열려 있는 동안만 쓸모 있다 | 301 · PI21 · PI34 |
| 경로 위의 능동 중계(교환 · hello 를 진짜 서버로 그대로 넘김) | 막지 않는다. 이름 URL 경고, IP · Tailscale 주소 권고 | 282 · 301 · PI6 |
| 같은 IP 를 가로채 요청 사이에 끼어들기(ARP 스푸핑) | 막지 않는다(요청 서명 없음). 연결 실패 뒤에는 다시 hello | 282 · PI4 |
| `worker.toml` 통째 복사(토큰 + `pair_secret`) = 워커 신원 복제 | 막지 않는다(로컬 침해). 0600 은 다른 보통 사용자만 막고 소유자 · root · 악성코드 · 백업 도난은 못 막는다. 알아보는 길: 다른 주소 재등록 로그 · 펜싱(M5t-b) · `rcm token revoke` | 302 · PI23 |
| 토큰만 샌 경우의 `/worker/spec` 읽기 | 막지 않는다. 묶인 풀의 프리셋 · env 만 보인다. 비밀이 필요한 프리셋은 `local_only` | 284 · 294 · PI14 · PI23 |
| 틀린 코드 5번으로 창을 닫는 방해 | 막지 않는다. 코디네이터에 즉시 이유 | 288 · PI9 |
| `data_dir` 을 키와 함께 통째로 복사한 사람의 사칭 | 지원하지 않는 구성으로 문서화, 신원 재설정 절차 | 289 · PI18 |
| mDNS 홍수로 다시 찾기를 막기 | 막지 않는다(서비스 거부만, 토큰 0) | 310 · PI4 |
| 위장 서버가 계속 그 주소에 있으면 짝지은 워커가 멈춰 있다 | 막지 않는다(토큰은 지킨다). 사람이 원인을 고치고 다시 띄우거나 다시 짝짓는다 | 303 · PI1 |
| `any-capable` 프리셋 env 를 붙은 워커 모두가 받는다 | 비밀이 필요한 프리셋은 `local_only`(결정 254) | 284 · PI14 |
| 토큰 없이 명시 URL 로 따라가는 클라이언트는 그 URL 의 wheel 을 실행한다 | 막지 않는다. 명시 URL 은 사용자의 신뢰 결정 | 300 · PI24 |
| 명시 URL 의 옛 토큰 머신은 그 URL 을 믿는다(리디렉트도 오늘대로) | 오늘과 같다. `rcm worker --check` 의 `warn` 이 다시 짝짓기를 권한다 | 283 · 309 · PI26 · PI37 |

## 10. 요청자 문서에 돌려줄 것

없다. 하류 문서가 요청한 것이 없다.

## 11. 결정 표

「오너 확인」 열의 「확정(위임, 2026-11-26) · Codex 와 논의해 선 조치」는 오너가 위임한 결정을 메인 세션이 Codex 와
논의해 먼저 조치했다는 뜻이다. 오너 말대로 완료 보고서에 Codex 와 논의한 내용을 적는다. 280~289 는 1차, 290~300 은
2차, 301~303 은 3차, 304~312 는 4차 위임 결정이다. 2판은 287 과 295 를 고쳤고 301~312 를 더했다. 295 의 「기동 실패는
종료 3」은 303 으로 개정했다. Q-B 와 Q-C 는 결정 번호 없이 §1 의 2차 결정 표에 두었다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다름 |
|---|---|---|---|---|
| 280 | 자동 발급 · 자동 가입(M5n §13, Q1) | 사람이 `rcm pair` 로 연 짧은 수명(기본 10분 · 상한 60분) · 한 번 쓰는 창 안에서만, 그 창의 kind · 풀 · 이름으로 발급하고 가입한다. M5n §13 해당 줄(`docs/m5n-workplan.md:650`)에 개정 날짜와 이 번호를 적는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 「연결하면」이 사람 손 두 번(창 열기 · 코드 넣기)이 된다 |
| 281 | 신원 비밀 저장(Q12) | DB 밖 `<data_dir>/pair.key`(0600) 하나에서 `pair_id` 마다 파생. DB 에는 `pair_id` 만. 키를 잃거나 바꾸면 다시 짝짓기 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 282 | TLS 없는 LAN(Q13) | 엿보기 · 이름 URL 중계 · 같은 IP 가로채기를 받아들인다. 요청마다 서명하지 않는다. 남는 위험 표에 적고 LAN · 이름 URL 은 경고, Tailscale 을 권한다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 283 | 옛 토큰 + `server = "auto"` 워커(N1) | 거절하고 다시 짝짓는 법을 안내한다. 명시 URL 의 옛 토큰 워커는 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 오늘 되던 설정이 멈춘다 |
| 284 | `tokens.pool` NULL(N2) | 묶이지 않은 옛 토큰. 등록 때 말한 풀로 등록, `/worker/spec` 은 등록한 풀로 거른다. 「—」로 보인다. 기본 풀이나 무제한으로 보지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 285 | 클라이언트(N3) | 짝지은 클라이언트도 URL 과 `pair_secret` 을 저장하고 health · 따라가기 · 인증 요청 전에 hello, 조용한 재발견 없음. 옛 명시 토큰 클라이언트는 그대로, 토큰 + `auto` 는 283 규칙 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 오늘 되던 설정(토큰 + `auto`)이 멈춘다 |
| 286 | 두 번째 승인(N4) | 없음. 즉시 발급하고 붙은 머신 · 주소 · kind · 풀 · 시각 · 폐기 명령을 `rcm pair` 완료 줄 · 서버 로그 · `rcm token list` 에 남긴다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 287 | 탈취 토큰 재등록(N5) | 짝지은 워커의 등록(M5t-b 의 세션 여는 `POST /worker/register`)은 hello 챌린지로 만든 `pair_proof` 가 있어야 한다. 증명은 `worker_session_id` 를 묶는다. 옛 명시 토큰은 토큰만. 검사 자리는 M5t-b register 순서의 3 다음 · 4 앞(1판의 「`mark_lost_for_worker` 앞」을 M5t-b 순서로 바꿔 적음) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 288 | 창 닫기 방해(N6) | 받아들인다. 실패는 창에 센다(5번). 닫히면 서버 로그와 기다리던 `rcm pair` 에 이유와 새 창이 필요하다는 줄 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 289 | 신원 키 수명 주기 | 키를 잃거나 바꾸면 모든 신원 증명이 무효이고 다시 짝짓기. DB 만 복원 · 복제해서는 두 번째 신뢰 서버가 생기지 않는다. DB 와 키를 함께 두 살아 있는 코디네이터에 복원하는 것은 지원하지 않고 문서화된 신원 재설정(정지 → 짝지은 토큰을 `rcm token revoke` → `pair.key` 삭제 → 시작 → 다시 짝짓기, 306) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 290 | hello 의 꼴(설계) | `POST /pair/hello` 익명 · 상태 없음 · Store 를 부르지 않음. 자기 주소 = 요청이 도착한 소켓 주소 · `public_url` · 광고 이름. 길이 접두 인코딩 + 용도 라벨. TXT 에 `server_id` 를 더하지 않는다. 고른 안: 소켓 주소 판정. 버린 안: 인터페이스 주소 목록(F15 가 정확하지 않다), 보관함 허용 목록 재사용(F14 · 근본 원인 R1), `server_id` 대조(베낄 수 있다, 1라운드 A-M14) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 291 | 짝짓기 창(설계) | 메모리, kind 마다 하나, 10자 base32, 창당 실패 5, 모든 실패에 같은 404, 교환은 창 잠금 안에서. 버린 안: DB 표 `pair_windows`(익명 실패가 DB 를 쓰고, 재시작이 창을 살린다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 3판과 다름(§12) |
| 292 | `rcm pair`(설계) | HTTP + admin 토큰, DB 를 열지 않는다, 기다리며 완료 줄, Ctrl-C 는 창 취소. 버린 안: DB 를 직접 여는 꼴(가드 · 마이그레이션, 1라운드 B-H1) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | admin 토큰이 필요하다(M5t-a setup 은 client 토큰 명령만 찍는다) |
| 293 | DB v27 과 `--replace`(설계) | `tokens` 에 `pool` · `pair_id` · `paired_host` · `paired_addr` 와 `pair_id` 고유 색인. `--replace` 는 같은 행 한 UPDATE | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 294 | 풀 묶기 · spec 거르기(설계) | 등록 409 `pool_mismatch`. 거르기 규칙 하나(`spec_presets_for_pool`): 토큰 풀 → 등록 풀 → 질의 `pool` → `default` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 295 | 확인 시점(설계) | 4.3 의 상태 전이 표. 연결 실패 뒤 다시 hello. 다시 찾기는 hello 를 통과한 후보로만. **2판: 「기동 실패는 종료 3」은 303 으로 개정** — 확실한 거절은 종료 0, 그 밖은 재시도 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치. 종료 코드 부분은 303(2026-11-26)으로 개정 | — |
| 296 | 받는 쪽 CLI(설계) | `rcm setup --role worker --pair` · `--role client --pair`, 코드는 프롬프트 · stdin, 파일은 처음부터 0600, 완료 줄. 수동 `rcm token add` 는 바꾸지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 297 | 익명 예외(설계) | 인증 없이 받는 새 라우트는 정확히 둘: `POST /pair/hello` · `POST /pair/exchange`. `/worker/*` 는 인증을 먼저 보는 규칙 그대로. PR0 이 PLAN 「보안」 절에 적고(재료 E2) PR1 의 문서 잠금이 PI20 집합과 대조 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | §12 의 핸드오프 표가 적은 `POST /worker/hello` 와 경로가 다르다 |
| 298 | lane | PR1 full · PR2 full · PR3 full · PR4 full · PR5 short · PR6 short(이유는 §5) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 299 | DB 판 순서 | v27 은 M5t-b 의 v26 뒤. PR1 은 v26 PR 머지 뒤 시작 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 300 | 토큰 없는 클라이언트의 따라가기(P1-Q1) | 바꾼다. mDNS 찾기는 그대로 두고, 찾은 서버의 client wheel 을 내려받아 실행하는 것은 검증된 `pair_secret` 이 있을 때만 한다. 명시 URL 은 사용자의 신뢰 결정으로 남긴다. 결정 300(280~299 가 모두 쓰여 다음 빈 번호). 따라가지 않은 뒤에도 명령은 이어 가고 stderr 에 한 줄 남긴다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 301 | 교환의 능동 사칭자(3차) | 요청은 코드 대신 `worker_nonce` 와 코드 키 증명, 응답은 두 nonce 와 전체 페이로드에 대한 코드 키 MAC. 워커는 MAC 이 맞을 때만 저장. 경로 위 능동 중계 · 평문 자격 증명은 남는 위험(T4). 관찰한 증명으로 창이 열린 동안 오프라인 추측이 가능함을 적는다(M4) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 302 | 복제된 `worker.toml`(3차) | 받아들인다. 토큰만 샌 경우는 막고, 토큰 + `pair_secret` 파일 복사는 워커 신원 복제 = 로컬 침해로 남는 위험. 0600 은 다른 보통 사용자만 막는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 303 | 종료 코드와 부모 명세 M8(3차) | 확실한 신원 불일치는 종료 0 + 「다시 짝지으라」(되살리지 않음). 연결 실패 · 5xx · 503 은 프로세스 안에서 물러서며 다시 시도(종료 없음). 프로토콜 불일치는 M5t-b 대로 3. M8 은 「펜싱 · 확실한 신원 거절」로 좁힌다. M5t-c 는 종료 3 을 쓰지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치(메인 세션 선택) | 1판 295 의 종료 3 을 바꿈 |
| 304 | 303 이 분류하지 않은 hello 응답 | 3xx · 404 · 405 · 다른 4xx · 깨진 200 · `v` ≠ 1 은 「신원을 증명하지 못한 응답」(`other`) — 재시도(토큰 0, 종료 없음, 60초 상한). 경고는 속도를 제한해 주기적으로 되풀이하고 `rcm worker --check` 와 doctor 에 「서버 신원 미확인」 상태를 드러낸다(4차: 주소 · 분류당 로그 한 줄은 영원히 호환되지 않는 서버를 숨긴다). 버린 안: 종료 0(옛 서버로 잠깐 내려간 코디네이터 · DHCP 로 잠깐 그 주소를 받은 장치 때문에 사람이 손댈 때까지 멈춤), 종료 3(되살림 루프가 진짜 서버가 돌아올 때까지 10초마다 돈다 — 303 이 피하려던 것) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | Codex 의견으로 바꿈(2판 재료는 주소 · 분류당 로그 한 줄) |
| 305 | 303 의 적용 범위 | 「확실한 신원 불일치」= 설정의 명시 URL 에서 받은 hello `mismatch` · hello 421 `host_not_this_server`, 확인된 서버의 401 · 등록 403 `pair_mismatch`(`auto` 의 후보 하나가 틀린 것은 다른 서버일 뿐이라 재시도). 기동이면 바로 종료 0. 도는 중이면 새 claim 을 멈추고 도는 잡을 끝까지 돌린 뒤(보고는 M5t-b 보고 큐) 잡이 0 이 되면 종료 0 — 그 사이 hello 가 `ok` 가 되면 돌아간다. 옛 토큰 워커의 401 은 오늘대로 2. 클라이언트는 종료 2(`rcm wait` 은 3). 4차: 잡을 비우는 동안 기존 시한 · 취소 집행은 그대로이고 신원 때문에 멈춘 상태를 계속 눈에 띄게 로그에 남긴다. 버린 안: 도는 중 바로 종료(도는 잡을 버린다), 도는 중에는 끝없이 재시도(303 의 「되살리지 않는 종료」와 어긋나고 사람이 원인을 모른다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 짝지은 워커는 토큰 폐기에 종료 0(옛 워커는 2) |
| 306 | `pair.key` 수명 주기 세부 | 없을 때: 폐기되지 않은 짝 토큰이 있으면 기동 거절(`cannot start server: …` 종료 2, 복구 · 재설정 안내), 없으면 새로 만든다. 만들기: 임시 파일 0600 + `fsync` + `os.link` + 폴더 `fsync`. 읽기: `O_NOFOLLOW` · 보통 파일 · 모드 · 길이. 재설정: 새 명령 없이 `rcm token revoke`(짝 토큰) → `pair.key` 삭제 → 시작 → 다시 짝짓기. 버린 안: 조용히 새 키(fail-open), `rcm pair --reset`(`rcm pair` 는 HTTP 만 쓰는데(292) 키가 없으면 서버가 뜨지 못한다 — 오프라인 명령이 되어 DB 를 열어야 하고 가드 목록이 바뀐다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | in 파일 기본값의 `rcm pair --reset` 대신 `rcm token revoke` |
| 307 | 코드 입력 | 알파벳은 그대로(A–Z, 2–7). 정규화(대문자 · `-` · 공백 제거) 뒤 10자 · 알파벳 검사를 보내기 전에 하고 틀리면 종료 2(창의 실패로 세지 않음). 0→O · 1→I · 8→B 바꿈은 하지 않는다(모호). 서버도 형식이 틀린 증명은 세지 않는다. 버린 안: Crockford base32 + 바꿈(1판 계약과 화면이 바뀌고 바꿈 규칙이 하나 더 생긴다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 308 | hello 의 주소 판정 | 본문 `dialed`(스킴 포함 정규형)를 MAC 에 넣고 서버의 자기 주소 집합(소켓 주소 · 루프백이면 localhost · `public_url` · 광고 이름)과 대조. Host 헤더는 쓰지 않는다. 버린 안: 1판의 Host 헤더 + 「포트 없으면 :80」(스킴을 몰라 `https` public_url 을 421 로 만든다, V1-3 · V2-2) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 309 | 리디렉트 | 짝지은 머신의 hello 와 토큰 요청은 3xx 를 따르지 않는다. 옛 머신은 그대로. 버린 안: 모든 머신에서 끊기(옛 동작을 바꾸는 범위 밖 변경) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 310 | 다시 찾기 | 후보 최대 8개에 hello, `ok` 가 정확히 하나일 때만 옮긴다. 둘 이상이면 옮기지 않고 로그 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 311 | 순수 계층 | `core/pairing.py` 는 시계 · 난수 · 파일을 보지 않는다(`now` · 난수 바이트는 인자). `secrets` · 시계 · 키 파일 · 창 상태는 `pairing.py`(PLAN.md:539) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 312 | `rcm pair` 종료 코드 | 0 짝지어짐 · 1 쓰이지 않고 닫힘(만료 · 틀린 코드 5번 · 취소) · 열기 거절 · 2 사용법 · 3 결과 모름(기다리다 서버를 잃고 만료 + 30초까지 못 돌아옴, 또는 재시작 뒤 창이 없는데 창 `id` 와 이어진 영속 상태가 완료나 미사용을 증명하지 못함) · 130 Ctrl-C(취소를 한 번 시도). 창 `id` 로 재시작을 알아본다. 재시작 뒤 창이 없다는 것은 쓰이지 않았다는 증거가 아니다(재시작 직전에 끝났을 수 있다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | Codex 의견으로 바꿈(2판 재료는 재시작으로 사라진 창을 1 로 봤다). 1판의 「3 = 기다리는 중 서버를 잃음」과 PI11 의 「재시작이면 1」의 모순을 풀었다 |

## 12. 격리 반박에서 바뀐 것

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
