# M5t-c 작업 명세: 짝짓기 · 서버 신원 보안

> 이 문서는 M5t-c 의 작업 명세다. 다루는 것은 새 머신을 코디네이터에 붙이는 짝짓기와 서버 신원 보안이다. 출처는 넷이다.
> 아래에 그대로 옮기는 오너의 말, `docs/m5t-workplan.md` 의 결정 252~255, 같은 문서 §12 의 「M5t-c 짝짓기 · 서버 신원
> 보안」 표, 그리고 2026-11-26 의 오너 위임 결정이다. 위임 결정은 메인 세션이 Codex 와 논의해 정했다. Codex 원문은 레포
> 밖에 두었다.
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
> 결정 번호는 280 부터다. 280~299 는 M5t-b 와 겹치지 않게 미리 나눠 둔 범위다. 이 판은 1판이다. DB 판은 v27 이고 M5t-b 의
> v26 뒤에 온다. 핀 sha 는 `fdf196d4ef682612857fb12df9162f67545b63f0`(origin/dev)이고 이 문서의 `file:line` 은 모두 이
> sha 의 것이다. 같은 사실은 §3 에 한 번만 적고 다른 절은 F 번호로 가리킨다. M5t 전체를 담았던 3판의 근본 원인 R5 를
> 되풀이하지 않으려는 것이다.

## 0. 한 줄

코디네이터는 잡을 받아 다른 머신에 나눠 주는 rcm 서버다. 거기서 사람이 `rcm pair --worker`(또는 `--client`)로 짧은
짝짓기 창을 열면 화면에 코드와 URL 이 찍힌다. 새 머신은 `rcm setup --role worker --pair` 에 그 URL 과 코드를 넣고 토큰과
풀, 서버 신원 비밀 `pair_secret` 을 받는다. 짝지은 머신은 토큰을 보내기 전에 매번 `POST /pair/hello` 로 상대가 자기와
짝지은 그 서버인지 확인한다. 기동할 때와 연결이 끊긴 뒤, 다시 찾은 주소로 옮길 때가 모두 그렇다. 확인이 안 되면 토큰도
요청도 보내지 않는다. 워커 토큰에는 풀을 묶어 등록과 `/worker/spec` 을 그 풀로 제한한다. 짝지은 워커가 다시 등록하려면
신원 증명이 있어야 한다. 설계는 §4.1 의 위협 모델에서 나온다. 이 설계가 막지 않는 것은 LAN 엿보기와 이름 URL 중계, 같은
IP 가로채기다. 셋은 남는 위험으로 적는다.

## 1. 출처와 오너 결정

### 요구와 이 명세가 정하는 것

| 요구(출처) | 이 명세가 정하는 것 | 결정 |
|---|---|---|
| 「다른 rcm 제어하는곳에서 이걸 연결하면 거기에서 알아서 분배」(2026-11-24) | 연결 = 사람이 연 짝짓기 창 + 코드 한 번. 창에서 정한 풀(기본은 기본 풀)로 바로 분배가 일어난다. 코드 입력 뒤 두 번째 승인은 없다 | 280 · 286 · 294 |
| 「Rcm워커 다 한 다음에 어떻게 알려주는거까지」(2026-11-24) | 짝짓기가 끝나면 양쪽이 한 줄씩 말한다. 코디네이터: 누가 · 어디서 · 어느 풀로 붙었는지와 폐기 명령. 워커: 붙었다 · 풀 · 받을 프리셋 수 · 결과는 코디네이터로 가고 요청자의 `rcm wait` 가 받는다. 결과 알림 자체는 M5t-a PR5 · M5t-b 범위 | 286 · 296 |
| 「민감데이터나 API 키 등 그런것들은 없어야해 … public」(2026-11-25) | 코드 · 토큰 · `pair_secret` · `pair.key` 를 어떤 출력 · 로그 · 응답에도 싣지 않는다(PI17). 시험 값은 실행 중에 만든다. §8 | 291 · 296 |
| 결정 252(M5t-c 는 위협 모델부터) | §4.1 이 첫 절이다. 나머지 설계 칸마다 위협 행 id 와 시나리오 id 를 단다 | — |
| 결정 255(같은 이름 세션 하나, 새 세션이 옛 세션을 펜싱) | 짝지은 워커는 펜싱하는 새 세션(재등록)에도 신원 증명이 필요하다. 탈취 토큰만으로 정당한 워커를 펜싱하지 못한다 | 287 |

### 이미 정해진 것

인용한 결정은 바꾸지 않는다.

- 결정 4(`PLAN.md:639`)는 「**클라이언트별 bearer 토큰**(서버엔 sha256 만)」이다. 유지한다.
- 결정 5(`PLAN.md:640`)는 「TLS 는 서버가 안 한다」이다. 유지하고 남는 위험은 결정 282 에 적는다.
- 결정 33(`PLAN.md:669`)과 34(`PLAN.md:670`)는 「TXT 에 `v`·`name`·`lanes` 만 넣는다」이다. 유지한다. mDNS 광고의 TXT 에
  `server_id` 를 더하지 않는다. 3판과 다른 점이며 §12 에 적었다.
- 결정 169(`follow.py:12-16`)는 따라가기의 sha256 이 「위조를 막는 장치가 아니다」라고 했다. 따라가기는 클라이언트가
  서버 버전에 맞는 client wheel 을 내려받아 실행하는 동작이다. 결정 169 는 유지하고 짝지은 머신은 hello 뒤에만 따라간다.
- 결정 254(`local_only`)는 #266 으로 dev 에 머지됐다. 짝짓기는 그 후보 함수를 우회하지 않는다(PI15).
- M5n §13(`docs/m5n-workplan.md:650`)은 「토큰 자동 발급 · 자동 가입. 토큰은 사람이 옮긴다(보안 결정 유지).」라고 정했다.
  이 줄은 결정 280 으로 개정한다. 같은 절의 「유실된 잡을 다른 머신에서 자동 재실행하기」 금지는 유지한다.

### 오너 위임 결정(2026-11-26)

오너가 위임한 결정을 메인 세션이 Codex 와 논의해 먼저 조치했다. 1차 결정은 §11 의 280~289 로 그대로 간다.

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

2차 결정은 이 명세의 재료가 남긴 질문에 대한 답이다. 재료는 세 질문을 Q-A · Q-B · Q-C 로, 위임 결정 파일은 P1-Q1 ·
P1-Q2 · P1-Q3 으로 부른다. 이 문서는 Q-A · Q-B · Q-C 를 쓴다.

| 질문 | 결정 |
|---|---|
| Q-A(P1-Q1) 토큰 없는 클라이언트가 `server = "auto"`(또는 빈 값)로 mDNS 에서 찾은 서버의 버전을 따라가 그 서버의 client wheel 을 내려받아 실행한다 | 바꾼다. 찾기는 그대로 두고, 내려받기와 실행은 검증된 `pair_secret` 이 있을 때만 한다. 명시 URL 은 사용자의 신뢰 결정으로 남긴다. Codex 는 클라이언트에서의 임의 코드 실행이 남는 위험으로 받아들이기엔 무겁다고 봤다 |
| Q-B(P1-Q2) 운영에서 옛 토큰 + `server = "auto"` 를 쓰는 머신 | 메인 세션이 이 Mac 의 운영 설정을 읽기 전용으로 확인했다. 워커 · 클라이언트 설정 모두 명시 URL 을 쓰고 `auto` 가 없다. 다른 머신은 운영 반영 전에 §6 15 로 확인한다 |
| Q-C(P1-Q3) M5t-b 와 맞추기 | M5t-b 의 결정(펜싱 종료 0, 프로토콜 불일치 종료 3, 프로세스당 세션 id, DB v26)을 전제로 적는다. 세션을 여는 호출의 이름은 「M5t-b 의 세션 PR 이 정한 이름」으로 의존을 적는다. 종료 3 의 두 경우(프로토콜 불일치 · 서버 신원 미확인)는 모두 「안전한 세션을 못 열었으니 감독자가 다시 시도」라는 뜻이고 진단 문구만 따로 둔다 |
| 290~297 · 299 | 추천값(TXT 그대로 · 창은 메모리 · `rcm pair` 는 admin 토큰 · 신원 미확인 워커 종료 3 · `rcm token add` 출력 그대로 · `POST /pair/hello` · v27 은 M5t-b v26 뒤 · 그 밖)에 Codex 가 모두 동의했다. 확정한다 |
| 298 | lane(PR1~PR4 full, PR5~PR6 short)에 Codex 가 동의했다. 확정한다 |

### 이 명세가 하지 않는 것

- 요청마다 서명하지 않고 TLS 도 넣지 않는다(결정 5 · 282). 같은 IP 가로채기와 평문 엿보기는 남는 위험이다.
- 읽기 토큰 모드는 만들지 않는다. `read_auth` 의 새 값이며 M5t-a Q14 에서 마일스톤 밖으로 뺐다.
- `rcm token add --worker` 에 신원 비밀이나 `--pool` 을 더하지 않는다. 수동 토큰은 오늘 그대로이고 옛 토큰 규칙 N1 · N2 를
  따른다.
- TXT 에 `server_id` 를 더하지 않는다. 서버를 고르는 일은 hello 가 한다.
- 웹 UI 에는 짝짓기 화면을 두지 않는다. CLI 만 둔다.
- 워커 세션과 로그 오프셋, 끊김 유예는 M5t-b 의 일이다. 이 명세는 「세션을 여는 호출」에 증명 검사를 거는 한 점에서만
  M5t-b 와 만난다.
- 키 재설정 명령은 만들지 않는다. 재설정은 문서화된 절차를 따른다. 서비스를 멈추고 `<data_dir>/pair.key` 를 지운 뒤
  다시 시작하고 다시 짝짓는다(결정 289).

## 2. 범위

| 누가 | 하는 일 |
|---|---|
| rcm(이 명세 PR1~PR6) | 서버: `pair.key` · 짝짓기 창(메모리) · `/pair*` 라우트 · DB v27(토큰 칸) · 등록의 풀 · 증명 검사 · `/worker/spec` 풀 거르기. 워커 · 클라이언트: `pair_secret` 설정 키 · 토큰 전 hello · 옛 토큰 + `auto` 거절 · `rcm pair` · `rcm setup --pair` · `rcm token list` 열 · 문서 · 실기 |
| M5t-a(다른 명세) | `rcm setup`(M5t-a PR4)이 있어야 PR4 가 `--pair` 를 더한다. `rcm doctor`(M5t-a PR2, #268)는 검증 중이라 dev 에 없다 — 머지되면 PR3 의 신원 확인 함수를 doctor 의 worker 역할에 같은 함수로 건다(#268 동작을 사실로 인용하지 않는다) |
| M5t-b(동시에 쓰는 명세) | DB v26 과 세션을 여는 호출. 이 명세의 PR1 은 v26 PR 이 dev 에 머지된 뒤, PR2 는 M5t-b 의 등록 · 세션 PR 이 머지된 뒤 시작한다 |
| 하류 앱 프로젝트 | 바꿀 것 없음. 명시 URL 의 옛 워커는 그대로 돈다 |
| 메인 세션 | 운영 설정을 읽기 전용으로 확인(운영 워커 · 클라이언트에 `server = "auto"` + 옛 토큰이 있는지, 질문 Q-B. 이 Mac 은 2026-11-26 에 확인했고 다른 머신은 운영 반영 전에 §6 15 로 확인한다) · 결정 페이지 · 릴리스 · 운영 반영 |
| 오너 | lane 행(298)은 2026-11-26 위임 결정으로 확정됐다. 운영 반영은 현행 규칙 |

## 3. 실측과 코드 제약

핀 sha 는 `fdf196d4ef682612857fb12df9162f67545b63f0` 이다. 다른 절은 이 표의 F 번호로 사실을 가리킨다.

| # | 사실 | 근거 | 설계에 미치는 것 |
|---|---|---|---|
| F1 | `tokens(name TEXT PRIMARY KEY, sha256 UNIQUE, admin, created_at, revoked_at, kind)`. 폐기된 이름도 행이 남아 이름을 점유한다 | `store.py:314-321` | `--replace` 는 새 행이 아니라 같은 행을 한 문장 UPDATE 로 바꾼다(293). 이미 있는 이름(폐기 포함)은 `--replace` 없이 거절(PI12) |
| F2 | `add_token` 은 32바이트 `token_urlsafe` 를 한 번 돌려주고 sha256 만 저장. `verify_token` 은 폐기 안 된 행을 돌며 `hmac.compare_digest` | `store.py:4045-4065`, `:4067-4082` | 짝짓기 발급도 같은 함수 · 같은 저장 규칙. 폐기 즉시 401(PI19) |
| F3 | `DB_VERSION = 25`, 마이그레이션은 `_MIGRATIONS[n]` 문장 묶음과 `user_version`, 더 새 DB 는 `newer_database_message` 로 거절 | `store.py:88`, `:661-673`, `:1209-1248`, `:778-797` | v27 은 열 넷 + 고유 색인 하나. 새 표 없음. M5t-b 가 v26 을 먼저 쓴다(전제) |
| F4 | `rcm token` 은 Store 를 직접 열고(열면서 마이그레이션), `add` 는 토큰을 stdout 한 줄로, 안내는 stderr. `list` 는 `name kind created revoked` 네 열 | `cli.py:4736-4748`, `:4752-4765`, `:4766-4770` | `rcm token add` 는 바꾸지 않는다(PI26). `list` 는 열을 뒤에만 붙인다(PI27) |
| F5 | 가드의 DB 를 여는 명령은 `token` 뿐, 마이그레이션 명령은 `serve` · `worker` · `token` | `tools/guard_production.py:85`, `:89` | `rcm pair` 는 DB 를 열지 않고 HTTP + admin 토큰으로 서버에 묻는다 → 가드 목록을 바꾸지 않는다(292, PI28) |
| F6 | Bearer 인증 `authenticate`, 읽기 `authenticate_read`(Basic 은 `read_auth = basic` 일 때만), 클라이언트 API 는 worker 토큰 403, admin 은 `require_admin` | `server.py:1076-1105`, `:1111-1122` | `POST /pair` · `GET /pair` · `POST /pair/cancel` 은 `require_admin`. 짝짓기로 admin 이 나오는 길 없음(PI7) |
| F7 | 라우터는 if 사슬. `/worker/spec` 다음 `/worker/` 로 시작하면 `_worker_route` 가 **인증을 먼저** 해 라우트 존재를 익명에 알리지 않는다 | `server.py:5745-5813`, `:6185-6187`, `_WORKER_RE` `:262`, `_WORKER_JOB_RE` `:263` | 새 익명 라우트는 `/worker/` 밖(`/pair/hello` · `/pair/exchange`)에 둔다 → `_worker_route` 의 규칙이 그대로 남는다(B-M11). 라우트 표 자료구조가 없으므로 PI20 은 PLAN 「서버 API」 표를 파싱해 순회한다 |
| F8 | `/api/health` 는 익명. 본문에 `version` · `client_wheel` 이 있다 | `server.py:5762-5766`, `:5395-5430` | 위장 서버가 health 로 따라가기를 유도할 수 있다(PI2) — 짝지은 머신은 hello 뒤에만 health 를 읽는다 |
| F9 | `/worker/spec` 은 worker 토큰이면 누구나, 프리셋마다 `env` 를 통째로 준다. `local_only` 만 뺀다. 토큰별 거르기 없음 | `capabilities.py:274-295`, `server.py:4353-4355` | 풀로 거른다(294, PI14 · PI16) |
| F10 | 등록: `pool` 은 본문에서(없으면 `default`), 버전 409 가 먼저, 그다음 `mark_lost_for_worker` 로 그 이름의 도는 잡을 `lost`, 워커 이름 = 토큰 이름 | `remote_workers.py:377-434` | 풀 검사 · 증명 검사를 **`mark_lost_for_worker` 앞**에 둔다 — 실패한 등록이 정당한 워커의 잡을 닫지 않게(287, PI13 · PI23) |
| F11 | `_owned_active`: 없는 잡 404 · 남의 잡 403 `not your job` · 끝난 잡 409 | `remote_workers.py:588-597` | 탈취 토큰의 남의 잡 접근은 오늘 그대로 막힌다(PI23 d). 시나리오 표가 적은 「409/404」를 「404 · 403 · 409」로 고쳤다 |
| F12 | mDNS TXT 는 `v` · `name` · `lanes`. 광고 호스트 이름은 `socket.gethostname().split(".")[0]` + `.local.` | `server.py:742-751`, `:712`, `:745` | TXT 를 바꾸지 않는다 → README 발견 문장(`README.md:412`) · `docs/operating.md:638` 그대로. 광고 이름 `<host>.local` 은 hello 의 자기 주소에 든다(290) |
| F13 | `base_url(host)`: `public_url` > 요청 Host(`_HOST_RE` 모양 · `0.0.0.0`/`::` 아님) > `server_base_url` | `server.py:1060-1072`, `:247`, `:249` | 화면 URL 계산에 재사용. 인증 판단에는 쓰지 않는다 |
| F14 | 보관함 Host 허용 목록은 `localhost` · `*.local` · `*.ts.net` · **모든 사설 IP** · `public_url` · hostname · bind 를 받는다(DNS 재바인딩 방지용) | `core/shelf.py:461-491`, `shelf_server.py:323-334` | 같은 망의 다른 사설 IP 를 받으므로 중계 방어에 못 쓴다(3라운드 근본 원인 R1 의 예). hello 는 이것을 재사용하지 않는다(290) |
| F15 | `local_ipv4s()` 는 `getaddrinfo(gethostname())` + 기본 경로 주소 — 인터페이스를 정확히 세지 못한다 | `mdns.py:105-118` | 「정확한 자기 주소 목록」(A-H3 · B-H2)을 만들지 않는다. 요청이 **도착한 소켓의 로컬 주소**(`self.connection.getsockname()`)로 판정한다. `local_ipv4s()` 는 화면에 찍을 URL 후보로만 쓴다 |
| F16 | 원격 워커: 연결 실패가 연속 `REDISCOVER_AFTER_FAILURES`(5)번이면 다시 찾은 주소로 **조용히** 옮긴다. 다시 찾기는 `server` 가 비었거나 `auto` 일 때만 켜진다 | `remote_worker.py:71`, `:653-662`, `cli.py:4544`, `:4330-4355`, `:4369-4404` | 짝지은 워커는 hello 를 통과한 후보로만 옮긴다(295). 옛 토큰 + `auto` 는 거절(283) |
| F17 | 워커 기동 따라가기 ①은 **등록 전에** 토큰을 실은 health 를 읽는다 | `cli.py:4435-4456`, `follow.py:659-663`, `:692-696` | 짝지은 워커는 hello 를 ① 보다 먼저 한다(PI2) |
| F18 | CLI 따라가기 `_follow_first` 는 argparse 전에 설정을 읽고 mDNS 로 서버를 찾은 뒤 따라간다(토큰을 실은 health · wheel 실행) | `cli.py:5548-5594`, `:420-442` | 짝지은 클라이언트는 hello 뒤에만 따라간다. 옛 토큰 + `auto` 클라이언트 거절은 `_resolve_server` 한 곳에 건다(285) |
| F19 | `Authorization` 을 붙이는 자리가 여섯이다: `WorkerClient.download_tree`, `Client._request`, `download_bundle`, `download`, `events`(client.py), `follow._headers` | `client.py:133`, `:531`, `:582`, `:629`, `:905`, `follow.py:663` | 신원 확인 관문은 이 여섯 자리 모두를 지나야 한다. 하나라도 빠지면 위장 서버가 토큰을 받는다(변이 `pair-gate-skipped-in-download`) |
| F20 | 설정: `_NAME_RE = ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. `ClientConfig.wants_discovery` · `WorkerConfig.wants_discovery` 는 빈 값이나 `auto`. 토큰을 담은 파일은 `_check_private`(그룹 · 기타 비트 있으면 거절). 클라이언트 키 `{server, token_env, token, label}`, 워커 키 11개. 워커 토큰은 플래그로 못 준다 | `config.py:79`, `:365-367`, `:522-524`, `:1509-1513`, `:1494`, `:1564-1576`, `:1832-1833` | 새 키 `pair_secret` 을 둘 다에 더하고 `_check_private` 를 그 키에도 건다(PI17). 이름 정리는 `_NAME_RE` 에 맞춘다(PI12) |
| F21 | 워커 서비스 파일: launchd `KeepAlive = {SuccessfulExit: false}`, systemd `Restart=on-failure` — 0 이 아닌 종료는 다시 띄운다 | `cli.py:3507`, `:3535` | hello 실패 종료 3 은 서비스가 다시 띄워 다시 hello 한다(진짜 서버가 돌아오면 붙는다) |
| F22 | 서버 JSON 본문 상한 64 KiB, 동시 요청 `max_concurrent_requests` 세마포어, 없는 경로는 404 + `not_found_hint(path)` | `server.py:238`, `:5706-5716`, `:6490`, `:5975` | 새 익명 라우트는 본문 1 KiB 상한. 교환의 404 본문은 경로 힌트 없이 고정 바이트(PI10). 옛 서버의 `/pair/hello` 는 404 → 짝지은 머신은 실패로 본다(fail-closed, PI26) |
| F23 | `rcm serve` 의 Store 오류는 `cannot start server: …` 와 종료 2 | `cli.py:3376-3394` | `pair.key` 를 못 만들거나 권한이 느슨하면 같은 꼴로 기동 거절(281) |
| F24 | 릴리스 드라이버가 실행마다 client 토큰 `store-driver:<repo>:<id>` 를 발급 · 폐기한다(이름에 `:` — `_NAME_RE` 밖) | `release_driver.py:261`, `:328` | 같은 `tokens` 표를 쓰는 길 — 회귀 그물(PI27). 짝짓기 이름은 `_NAME_RE` 라 겹치지 않는다 |
| F25 | `Source.identity`(잡 소스의 정체)라는 이름이 이미 있다 | `core/model.py:267` | 새 이름은 `identity` 를 쓰지 않고 `pair_` 접두어로 짓는다(`pair_secret` · `pair_id` · `pair.key` · `/pair/hello`). grep 결과 0건(§11 의 noted) |
| F26 | `rcm setup` 은 이 sha 에 없다(`cmd_setup` 0건). M5t-a PR4 가 만든다 | `grep -n cmd_setup src/remote_ci_monitor/cli.py` → 0 | PR4 의 시작 조건 |
| F27 | `TOKEN_KINDS = (client, admin, worker)`, `DEFAULT_POOL = "default"`. 프리셋의 풀은 `pool` + `pools`, `placement` 는 `pool` · `capable` | `core/model.py:79`, `:84-85`, `:202-203`, `:217` | 풀 거르기 규칙(294)은 `pool`/`pools` 와 `any-capable` 을 본다 |
| F28 | PLAN 「보안」 절에 익명 예외 문장이 없다(M5t-a 4판이 지웠다) | `PLAN.md:241-249`, `docs/m5t-workplan.md` §13 3라운드 B-M5 | PR1 이 다시 넣는다(297) |
| F29 | M5n §13 이 자동 발급 · 자동 가입을 막았다 | `docs/m5n-workplan.md:650` | PR0 이 그 줄에 개정 날짜와 결정 280 을 적는다 |

측정한 값은 따로 둔다. M1 은 개발 머신에서 잰 값이고 운영 머신의 값이 아니다. M3 의 다른 머신 칸은 아직 재지 않았다.

| # | 값 | 어떻게 쟀나 | 설계에 미치는 것 |
|---|---|---|---|
| M1 | 워커 비밀 파생(HMAC-SHA256) + 응답 MAC 한 번 = 1.88 µs | 개발 머신(arm64, Python 3.11.15)에서 `timeit` 100,000회. 운영 머신 값 아님 | 익명 hello 를 DB 없이 매번 계산해도 비용이 작다. 동시 요청 상한(F22)이 과부하를 막는다 |
| M2 | 코드 공간 32^10 = 2^50, 창당 시도 5번 → 한 창의 추측 성공 확률 ≈ 4.4e-15 | 계산 | 시도 한도 5 와 10자 코드(291) |
| M3 | 운영 워커 · 클라이언트 중 `server = "auto"` + 옛 토큰의 수. 이 Mac: 0(워커 · 클라이언트 설정 모두 명시 URL, `auto` 없음). 다른 머신: — | 메인 세션이 이 Mac 의 운영 설정을 읽기 전용으로 확인했다(2026-11-26, Q-B). 다른 머신은 운영 반영 전에 §6 15 로 확인한다 | 있으면 운영 반영 전에 다시 짝짓거나 명시 URL 로 바꾼다(§9) |

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

| # | 공격자 위치 | 노리는 것 | 오늘(F) | M5t-c 가 하는 것 | 남는 위험 | 결정 | 시나리오 |
|---|---|---|---|---|---|---|---|
| T1 | 같은 망, mDNS 에 먼저 답하는 위장 서버 | S1 · S2 · S3 | `auto` 워커 · 클라이언트가 찾은 서버에 토큰을 보내고 argv · wheel 을 실행한다(F16 · F17 · F18) | 짝지은 머신은 토큰 전에 hello, 실패면 토큰 0. 옛 토큰 + `auto` 는 거절. 토큰 없는 클라이언트는 검증된 `pair_secret` 이 없으면 찾은 서버의 wheel 을 내려받지도 실행하지도 않는다(Q-A) | 명시 URL 의 옛 토큰 머신은 그 URL 을 믿는다(오늘과 같다). 토큰 없이 명시 URL 을 따라가는 클라이언트도 같다(사용자의 신뢰 결정, Q-A) | 283 · 285 · 290 · 295 · Q-A | PI1 · PI2 · PI3 · PI24 |
| T2 | 같은 망, 다른 IP 에서 hello 를 진짜 서버로 중계 | S1 · S2 | — | MAC 에 Host 를 넣고, 진짜 서버는 요청이 도착한 소켓 주소 · `public_url` · 광고 이름만 자기 주소로 인정 | 이름 URL(`<host>.local` · `public_url` 이름)은 이름을 가로챈 중계가 통과 | 282 · 290 | PI5 · PI6 |
| T3 | 같은 IP 를 차지(ARP 스푸핑, DHCP 로 옛 주소를 받은 장치) | S1 · S2 | 끊긴 뒤 같은 URL 로 바로 토큰 | 연결 실패 뒤 첫 요청은 hello. DHCP 로 주소를 받은 장치는 MAC 을 못 만든다 | ARP 스푸핑이 연결 실패 없이 요청 사이에 끼어들면 못 막는다(요청 서명 없음) | 282 · 295 | PI4 |
| T4 | 같은 망의 수동 엿보기 | S1 · S5 · S6 | 평문(결정 5) | 코드는 한 번 · 짧은 수명. hello nonce 와 챌린지는 한 번 | 교환 응답의 토큰 · `pair_secret` 과 요청의 토큰을 엿보면 사칭 가능. LAN · `0.0.0.0` 경고와 Tailscale 권고 | 282 | PI21 |
| T5 | 같은 망의 익명 클라이언트(코드 추측 · 정찰 · 방해 · 과부하) | S5 | 창 없음 | 50비트 코드 · 창당 5번 · 모든 실패에 같은 404 · 읽기 라우트와 TXT 에 창 표시 없음 · 본문 1 KiB · 동시 상한 · hello 는 Store 를 부르지 않는다 | 틀린 코드 5번으로 창을 닫는 방해 | 288 · 291 | PI9 · PI10 · PI20 |
| T6 | 탈취한 worker 토큰(로그 · `ps` · 복제된 worker.toml) | 정당한 워커의 잡, 다른 권한 | 같은 이름 재등록이 정당한 워커의 잡을 `lost` 로 닫는다(F10) | 짝지은 워커의 재등록은 챌린지 증명. 다른 주소 재등록을 서버 로그에 남긴다. 클라이언트 API 403 · 남의 잡 403(오늘) | 등록 없이 claim · heartbeat 는 토큰만으로 된다. 옛 토큰은 토큰만 | 287 | PI23 |
| T7 | 한 창이 주는 권한을 넘기기 | admin · client 권한 | — | 창의 kind 만 준다. admin 창은 없다 | 없음 | 280 · 291 | PI7 |
| T8 | 사람 실수: 코드를 엉뚱한 머신에 넣음 | S2 · S4 | — | 양쪽 완료 줄 · `rcm token list` 의 `paired from` · 폐기 한 번 | 폐기 전까지 그 머신이 받은 잡 | 286 | PI25 · PI19 |
| T9 | DB 사본 · 백업을 가진 사람 | S6 | — | DB 에는 `pair_id` 와 토큰 sha256 만. `pair.key` 는 DB 밖 0600 | `data_dir` 을 키와 함께 통째로 복사하면 사칭 가능 — 문서화된 신원 재설정 | 281 · 289 | PI18 |
| T10 | 붙은 워커가 다른 풀 프리셋 env 를 읽기 | S4 | 모든 비-`local_only` 프리셋 env(F9) | 토큰 풀 · 등록 풀로 거른다 | `any-capable` 프리셋 env 는 붙은 워커 모두가 받는다(그런 프리셋에 비밀을 두지 않는다 — M5n §13 · 결정 254) | 284 · 294 | PI14 · PI16 |
| T11 | 다른 빌드가 운영 DB 를 엶 | S7 | `rcm token` 은 DB 를 연다(F5) | `rcm pair` 는 HTTP 로만 | 없음 | 292 | PI28 |

### 4.2 서버 신원 `pair.key` · `pair_secret` · `POST /pair/hello` (결정 281 · 289 · 290)

서버는 DB 밖의 키 하나로 자기 신원을 증명한다. 짝지은 머신마다 그 키에서 파생한 비밀을 나눠 준다.

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 서버 키 | `<data_dir>/pair.key`, 32 무작위 바이트. `rcm serve` 가 기동 때 없으면 `O_CREAT` + `O_EXCL` · 0600 으로 만든다. 있으면 읽고, 그룹 · 기타 권한 비트가 있거나 길이가 32 가 아니면 `cannot start server: …` 종료 2(F23). 값은 어디에도 찍지 않는다 | T9 |
| 워커 · 클라이언트 비밀 | 발급마다 `pair_id` = 16 무작위 바이트(hex 32자). `K = HMAC-SHA256(pair.key, enc("rcm-pair-secret-v1", pair_id))`. 머신이 받는 값 `pair_secret = "<pair_id>:<K hex 64자>"`. DB 에는 `tokens.pair_id` 만 남는다(Q12 → 281) | T9 |
| 인코딩(L2) | `enc(f1, …, fn)` = 각 필드의 UTF-8 바이트 앞에 4바이트 big-endian 길이. 첫 필드는 용도 라벨(`rcm-pair-secret-v1` · `rcm-hello-v1` · `rcm-challenge-v1` · `rcm-register-v1`). 순수 함수 `pair_mac(key, label, *fields) -> hex` 는 `core/pairing.py` 에 하나 | T2 |
| hello 요청 | `POST /pair/hello`, 익명, 본문 ≤ 1 KiB `{"pair_id": <hex32>, "nonce": <hex64>}`. nonce 는 호출마다 `secrets.token_hex(32)` | T1 · T2 · T3 |
| Host 판정 | 정규화(소문자 · 끝의 점 제거 · 포트 없으면 `:80` · IPv6 괄호)한 Host 가 다음 셋 가운데 하나일 때만 자기 주소다: ① 이 요청이 도착한 소켓의 로컬 주소 `getsockname()` 의 `ip:port` ② `public_url` 의 `host:port` ③ 광고가 켜져 있으면 `<gethostname 첫 조각>.local:<port>`(F12). 아니면 421 `{"error": "this server does not answer to that address — set public_url if it is reached through a name or a proxy", "code": "host_not_this_server"}` — `mac` · 챌린지 없음. 보관함 허용 목록(F14)과 `local_ipv4s()`(F15)는 쓰지 않는다 | T2 |
| hello 응답 | 200 `{"v": 1, "mac": pair_mac(K, "rcm-hello-v1", pair_id, nonce, host), "challenge": <c>}`. K 는 `pair_id` 로 매번 파생(DB 조회 없음). 없는 · 폐기된 · 옛 `pair_id` 도 같은 계산이라 응답 모양 · 시간이 같다 — 이름 · 존재 오라클 없음. Store 를 부르지 않는다(PI5 · PI20) | T5 |
| 챌린지 | `c = "<unix초>.<rand hex16>.<pair_mac(pair.key, "rcm-challenge-v1", pair_id, unix초, rand)>"`. 서버 메모리 상태 없이 검증한다. 수명 `PAIR_CHALLENGE_SECONDS = 120`(서버 시계). 한 번 쓴 챌린지는 메모리 집합에 120초 두어 두 번째를 거절(재시작하면 집합은 비지만 120초 창이 묶는다) | T4 · T6 |
| 워커의 검증 | 자기가 접속한 URL 의 netloc 을 같은 규칙으로 정규화해 `mac` 을 다시 계산하고 `hmac.compare_digest`. 200 이 아니거나 · 본문이 깨졌거나 · `v` 가 1 이 아니거나 · 어긋나면 실패 | T1 · T2 |
| 재등록 증명(N5 → 287) | 짝지은 토큰(`tokens.pair_id` 있음)의 세션을 여는 호출(오늘 `POST /worker/register`, M5t-b 가 세션 여는 호출을 바꾸면 그 호출)은 본문 `pair_proof: {"challenge": c, "mac": pair_mac(K, "rcm-register-v1", pair_id, c, 토큰 이름)}` 이 있어야 한다. 없으면 403 `pair_proof_required`, 틀리거나 · 만료 · 재사용 · 다른 `pair_id` 의 챌린지면 403 `pair_proof_invalid`. 검사는 버전 409 다음, 풀 검사 · `mark_lost_for_worker` 앞(F10). 옛 토큰(`pair_id` NULL)은 검사하지 않는다 | T6 |
| 키 수명 주기(289) | 키를 잃거나 지우면 기동 때 새 키 → 모든 `pair_secret` 이 무효 → 짝지은 머신은 hello 실패로 토큰을 보내지 않는다 → 코디네이터에서 폐기 · 다시 짝짓기. DB 만 다른 곳에 복원하면 그곳의 새 키로는 기존 머신이 붙지 않는다. DB 와 키를 함께 두 살아 있는 서버에 두는 것은 지원하지 않는다(문서) | T9 |

### 4.3 워커와 클라이언트가 확인하는 때 (결정 283 · 285 · 295)

짝지은 머신, 곧 설정에 `pair_secret` 이 있는 머신의 관문 상태를 전이 표로 적는다. 관문은 F19 의 여섯 자리를 모두 지난다.

| 지금 | 사건 | 다음 | 하는 일 |
|---|---|---|---|
| 확인 전 | 토큰을 실을 요청을 보내려 한다 | 확인 중 | 그 URL 로 hello |
| 확인 중 | hello 200 · `mac` 일치 | 확인됨 | 챌린지를 등록용으로 들고 요청을 보낸다 |
| 확인 중 | 연결 실패(status 0) | 확인 전 | 토큰 0. 워커는 오늘의 재시도(등록 5초)와 연속 실패 세기(F16)를 그대로 쓴다 |
| 확인 중 (기동, 아직 등록 전) | hello 실패(421 · 404 · 4xx · 5xx · 깨진 본문 · `mac` 어긋남) | 끝 | 워커: 토큰 0, 종료 3, 로그 「server at <URL> did not prove it is the server this worker was paired with — it may be another machine, or the server's data_dir was reset or this token revoked; pair again: rcm pair --worker on the coordinator, then rcm setup --role worker --pair」. 클라이언트: 같은 뜻의 문장, 종료 2(아무것도 하지 않았다) |
| 확인 중 (등록 뒤 도는 중) | hello 실패 | 확인 전 | 토큰 0. 같은 로그를 주소마다 프로세스당 한 번. 5초 뒤 다시 hello. 도는 잡의 프로세스는 계속 돌고 보고는 미룬다(미룬 보고의 처리는 M5t-b) |
| 확인됨 | 요청이 연결 실패(status 0) | 확인 전 | 다음 토큰 요청 전에 다시 hello(PI4 b) |
| 확인됨 | HTTP 응답(어느 상태 코드든) | 확인됨 | 그대로 |
| 확인 전 (워커) | 연속 연결 실패 5번 | 확인 전 | mDNS 후보(최대 8)마다 hello. 통과한 첫 주소로 옮기고 로그 `server moved: A → B`. 통과한 후보가 없으면 옮기지 않는다. 설정 파일은 고치지 않는다(다음 기동도 같은 규칙으로 찾는다) |
| 확인됨 (워커) | 등록 · 레인 재등록 | 확인됨 | 직전 hello 의 챌린지로 `pair_proof` 를 싣는다. 챌린지는 한 번만 쓰이므로 등록마다 hello 를 새로 한다 |

옛 토큰 머신, 곧 `pair_secret` 이 없는 머신은 설정에 따라 아래처럼 동작한다.

| 설정 | 동작 | 결정 |
|---|---|---|
| 워커, `server` 명시 URL | 오늘 그대로(hello 없음, 다시 찾기 없음, F16) | 283 |
| 워커, `server` 가 빈 값 · `auto` | `rcm worker` · `rcm worker --check` 가 mDNS 질의 전에 종료 2: 「server = "auto" needs a paired worker — pair it (rcm pair --worker on the coordinator, then rcm setup --role worker --pair) or set server to the coordinator's URL」 | 283 |
| 클라이언트, 토큰 있음 + 명시 URL | 오늘 그대로 | 285 |
| 클라이언트, 토큰 있음 + 빈 값 · `auto` | `_resolve_server` 가 `ConfigError`(종료 2)로 같은 뜻의 문장. `_follow_first` 는 이 경우 따라가지 않고 명령이 그 문장으로 끝난다 | 285 |
| 클라이언트, 토큰 없음 + `auto` | mDNS 찾기는 오늘대로 한다. 찾은 서버의 client wheel 을 내려받아 실행하는 따라가기는 검증된 `pair_secret` 이 있을 때만 하므로 이 설정은 따라가지 않는다. 따라가지 않은 뒤에도 명령은 이어 가고, stderr 에 「검증되지 않은 서버라 따라가지 않았다」 한 줄과 짝짓는 법을 남긴다(결정 300). 토큰이 없으니 이어 가는 명령은 읽기뿐이다. 명시 URL 은 사용자의 신뢰 결정으로 남아 오늘처럼 따라간다 | Q-A |

짝지은 머신의 `server` 를 사람이 `auto` 로 고친 경우에는 후보마다 hello 해서 통과한 하나를 쓴다. 통과한 후보가 없으면
첫 표의 「확인 중 (기동)」 행대로 한다.

### 4.4 짝짓기 창 (결정 280 · 286 · 288 · 291 · 292)

짝짓기 창은 사람이 코디네이터에서 여는 짧은 발급 기간이다. 창이 열려 있는 동안 코드 한 번으로 토큰이 나온다.

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 창을 두는 곳 | 서버 프로세스 메모리. DB 표를 두지 않는다(3판의 `pair_windows` 를 버림). 재시작하면 열린 창은 사라진다(수명을 늘리지 않음). 익명 실패가 DB 를 쓰지 않는다 | T5 |
| 창 수 | kind(`worker` · `client`)마다 하나. 같은 kind 가 열려 있으면 `POST /pair` 는 409 `pair_window_open` + `expires_at`. 두 kind 는 함께 열 수 있다 | T7 |
| 코드 | 10자, RFC 4648 base32 알파벳(A–Z, 2–7) `secrets.choice`. 화면에 `ABCDE-FGHJK` 꼴. 입력은 대문자로 바꾸고 `-` · 공백을 뗀다. 서버는 sha256 만 들고 `hmac.compare_digest` 로 비교 | T5 |
| 수명 | 기본 10분, 상한 60분(`rcm pair --ttl <분>`, 1~60, 밖이면 종료 2 · 서버 400). 서버 시계로만 판정. reload 는 바꾸지 않는다 | T5 |
| 실패 세기 | 그 kind 의 열린 창에 센다(출처 무관, 288). 5번째 실패에 창을 닫고 이유 `too_many_wrong_codes`. 서버 로그 한 줄 「pair: worker window closed after 5 wrong codes — open a new one with rcm pair」. 기다리던 `rcm pair` 가 같은 문장과 종료 1 | T5 |
| 같은 404 | 틀린 코드 · 닫힌 창 · 연 적 없는 창 · 만료 · 이미 쓴 코드 · 다른 kind · 본문 `kind` 가 창과 다름 모두 404 `{"error": "no open pairing window for that code"}`(고정 바이트, `not_found_hint` 없음) | T5 · T7 |
| 교환 | `POST /pair/exchange`, 익명, 본문 ≤ 1 KiB `{"kind", "code", "host_name"}`. 창 잠금 안에서: 열림 · 만료 · 코드 확인 → 이름 결정 → 이름 충돌이면 409 `name_taken`(창은 열린 채, 실패로 세지 않는다 — 맞는 코드를 가진 쪽만 본다) → 창을 `used` 로 닫음 → 토큰 행 쓰기(`add_token` 과 같은 저장 규칙, 한 트랜잭션) → 200 `{"name", "kind", "token", "pair_secret", "pool", "presets_count"}`(`pool` · `presets_count` 는 worker 만, client 는 null). 행 쓰기가 실패하면 503 `pair_failed`(창은 닫힌 채, 코디네이터에 이유) | T5 · T7 |
| 동시 교환 | 창마다 잠금 하나. 같은 코드 N개 동시 → 200 정확히 1 | T5 |
| 이름 | `rcm pair --name N` 이면 N(`_NAME_RE`, 창을 열 때 이미 있으면 409 `name_taken`). 없으면 교환 본문의 `host_name` 을 정리: `_NAME_RE` 밖 글자 → `-`, 앞의 영숫자 아닌 글자 제거, 64자 자르기, 남는 글자가 없으면 `worker-<hex6>`(client 창이면 `client-<hex6>`) | T8 |
| `--replace` | `--name` 과 함께만. 그 이름의 행이 같은 kind 여야 한다(아니면 400 `replace_needs_same_kind`). 교환 때 그 행을 한 UPDATE 로 바꾼다: 새 `sha256` · `pair_id` · `pool` · `paired_host` · `paired_addr` · `created_at`, `revoked_at = NULL`. 옛 토큰은 그 순간부터 401(F1 · F2) | T6 · T8 |
| 풀 | worker 창만. `--pool P`(없으면 `default`). 열 때 그 풀로 갈 프리셋 수를 계산해(4.5 의 같은 함수) 0 이면 응답 `warnings[]` 에 「0 presets reach pool P」 — 거절하지 않는다 | T10 |
| 화면 URL | `POST /pair` 응답 `urls[]`: `public_url` 이 있으면 그것 하나. 없고 bind 가 특정 비루프백 주소면 `http://<bind>:<port>`. `0.0.0.0` 이면 `local_ipv4s()` 의 주소마다 + 광고가 켜져 있으면 `http://<host>.local:<port>`. 루프백 bind 에 `public_url` 이 없으면 409 `pair_unreachable` 「this server listens on <bind> only — other machines cannot reach it; set bind (and public_url) first」 — 창을 열지 않는다 | T2 |
| 평문 경고 | `rcm pair` 와 `rcm setup --pair` 는 URL 의 호스트가 루프백도 Tailscale 대역(100.64.0.0/10)도 아니면 stderr 에 「the pairing code and the token cross this network in plain text — prefer the server's Tailscale address」, 이름이면 「a name URL cannot stop a relay on this network — use an IP or Tailscale address」 | T4 · T2 |
| 남기는 것(N4 → 286) | 교환 성공 때 서버 로그 「pair: <kind> '<name>' paired from <addr> (host <host_name>, pool <p>)」, `tokens.paired_host` · `paired_addr` 에 저장, 기다리던 `rcm pair` 완료 줄. 두 번째 승인 없음 | T8 |
| 코드 노출 | 코드는 `POST /pair` 의 201 응답에만 있고 `rcm pair` 가 stdout 에 한 번 찍는다. `GET /pair` · 로그 · 이벤트 · `/api/*` 에 없다 | T5 |
| 관리 라우트 | `POST /pair`(admin) 본문 `{kind, pool?, name?, replace?, ttl_minutes?}` → 201 `{kind, code, urls[], expires_at, pool, name, presets_count, warnings[]}`. `GET /pair`(admin) → `{windows: [{kind, pool, name, opened_by, opened_at, expires_at, state, reason, paired}]}` — 최근 1시간 · 20개, 코드 없음. `state` ∈ `open` · `used` · `expired` · `closed` · `cancelled`, `paired` = `{name, host_name, addr, at}` 또는 null. `POST /pair/cancel`(admin) `{kind}` → 200 `{cancelled: <bool>}` | T7 |

### 4.5 토큰에 묶인 풀 (결정 284 · 294)

짝짓기로 받은 worker 토큰에는 풀을 묶는다. 등록과 `/worker/spec` 이 그 풀을 본다.

| 항목 | 정한 것 | 위협 |
|---|---|---|
| 저장 | `tokens.pool`. 짝짓기 worker 토큰은 창의 풀. client · admin · 수동 토큰 · v27 전 토큰은 NULL | T10 |
| 등록 검사 | `tokens.pool` 이 있고 본문 `pool`(없으면 `default`)과 다르면 409 `{"error": "this worker token is bound to pool '<P>' — set pool = \"<P>\" in worker.toml or pair again", "code": "pool_mismatch", "bound_pool": "<P>"}`. 버전 409 · 증명 403 다음, `mark_lost_for_worker` 앞 → `workers` 행 불변, 도는 잡 불변 | T10 |
| 워커의 처리 | 409 `pool_mismatch` 는 로그에 묶인 풀과 고치는 법을 적고 종료 2 | — |
| `/worker/spec` 거르기 | 그 토큰의 풀 = `tokens.pool` → (NULL 이면) 그 이름의 `workers` 행 풀 → (등록 전이면) 질의 `?pool=<p>`(`_NAME_RE`, 없으면 `default`). 프리셋은 `placement == "capable"` 이거나 그 풀이 프리셋의 `pool` · `pools` 에 있을 때만, 그리고 후보 함수 `machines_for` 가 원격을 빼지 않을 때(`local_only`)만 싣는다. 거르지 않은 전체를 주는 길은 없다(N2) | T10 |
| 질의 `pool` | `WorkerClient.spec()` 이 자기 `pool` 을 싣는다. 묶인 토큰은 질의를 무시한다. 묶이지 않은 토큰이 질의로 다른 풀을 읽는 것은 그 풀로 등록하는 것과 같은 권한이다(오늘 N2 가 허용) | — |
| 한 함수 | 거르기 규칙은 `spec_presets_for_pool(pool)` 하나. `/worker/spec`, 창을 열 때의 `presets_count`, 교환 응답이 같은 함수를 부른다 | — |
| 표시 | `rcm token list` 의 `pool` 열, NULL 은 「—」 | — |

### 4.6 CLI (결정 292 · 296)

새로 생기거나 바뀌는 명령과 그 종료 코드다.

| 명령 | 정한 것 | 종료 코드 |
|---|---|---|
| `rcm pair --worker [--pool P] [--name N] [--replace] [--ttl 분]` · `rcm pair --client [--name N] [--replace] [--ttl 분]` | 클라이언트 설정(`server` · `RCM_TOKEN`)의 **admin** 토큰으로 `POST /pair`. stdout: 코드 · URL(들) · 만료 시각(로컬 시간)과 남은 분 · kind · 풀 · 이름 · 갈 프리셋 수. 경고는 stderr. 그 뒤 2초마다 `GET /pair` 로 기다린다. 쓰이면 완료 줄(이름 · kind · 풀 · `host_name` · 주소 · 시각 · 다음 할 일 `rcm machines`, `rcm token revoke <name>`). Ctrl-C 는 `POST /pair/cancel` 뒤 130. DB 를 열지 않는다. admin 토큰이 없으면 「pairing needs an admin token: rcm token add <name> --admin on the server」 | 0 짝지어짐 · 1 창이 쓰이지 않고 닫힘(만료 · 틀린 코드 5번 · 취소 · 서버 재시작으로 창이 사라짐) 또는 서버가 열기를 거절(409) · 2 사용법(`--ttl` 범위 밖, `--replace` 에 `--name` 없음, `--worker`/`--client` 둘 다 없음, 401/403) · 3 기다리는 중 서버를 잃음 |
| `rcm pair --cancel --worker` · `rcm pair --cancel --client` | `POST /pair/cancel` | 0 닫음 · 1 열린 창 없음 · 2 사용법 |
| `rcm setup --role worker --server URL --pair` | M5t-a 의 setup 에 `--pair` 를 더한다. 코드는 TTY 면 `getpass` 프롬프트, 아니면 stdin 한 줄. 인자로 받지 않는다(`--code` 는 argparse 가 모르는 인자로 종료 2). 순서: 쓸 파일이 이미 있으면 `--force` 없이 거절(교환 전에 — 코드를 쓰지 않는다) → 교환 → `worker.toml` 을 `O_CREAT` + `O_EXCL` 0600 으로(`server` = 넣은 URL, `token`, `pair_secret`, `pool`, `name` = 토큰 이름) → hello 한 번 → 완료 줄 「paired with <URL> as '<name>' · pool <p> · <n> presets can come here · results go back to the coordinator and the requester's rcm wait gets them」. `--pair` 면 `--yes` 에서 `--pool` 이 필요 없다(창이 정한다). 주면 창의 풀과 달라야 할 때 종료 2 | 0 · 1(교환 404 「the code was wrong or used, or the window is closed — ask for a new code」, 연결 실패, 파일을 못 씀 — 토큰이 발급됐으면 「issued as '<name>' but not saved — on the coordinator: rcm token revoke <name>, then open a new window」) · 2 |
| `rcm setup --role client --server URL --pair` | 같은 흐름으로 `client.toml`(`server`, `token`, `pair_secret`) 0600 | 같다 |
| `rcm token list` | 열 뒤에 `pool`(NULL 「—」)과 `paired from`(`<paired_host> @ <paired_addr>` 또는 「—」) | 오늘 그대로 |
| `rcm worker --check` | 맨 앞(python 행 다음)에 `server identity` 행. 짝지은 설정: hello 성공 `ok`, 실패 `FAIL` 이고 그 뒤 토큰을 쓰는 행(token · fingerprint · presets)은 「— skipped: server not verified」로 두고 토큰을 보내지 않는다. 옛 토큰: `warn` 「not paired — this worker cannot tell this server from another machine; pair it with rcm pair」 | 오늘 규칙(FAIL 이 있으면 1) |
| `rcm check` | 짝지은 클라이언트만 `server identity` 행. 옛 클라이언트는 행을 더하지 않는다 | 오늘 규칙 |
| `NO_FOLLOW_COMMANDS` | `pair` 를 더한다(`follow.py:80-82`). setup 은 M5t-a PR4 가 넣는다 | — |
| `rcm token add` | 바꾸지 않는다 | — |

### 4.7 얼리는 계약

이 명세 PR 의 머지가 아래 계약을 얼린다. 얼린 뒤에 바꾸려면 오너가 승인하는 새 명세 PR 이 있어야 한다.

| 계약 | 바뀌는 것 |
|---|---|
| `/api/status` `schema_version` | 그대로(새 키 없음) |
| `/api/health` · mDNS TXT | 그대로 |
| DB | v27(M5t-b v26 뒤). `ALTER TABLE tokens ADD COLUMN pool TEXT` · `… pair_id TEXT` · `… paired_host TEXT` · `… paired_addr TEXT` · `CREATE UNIQUE INDEX IF NOT EXISTS tokens_pair_id ON tokens(pair_id)`. 새 DB 스키마(`_SCHEMA_V1`)도 같은 칸. 새 표 없음 |
| 와이어 — 새 라우트 | `POST /pair/hello`(익명) · `POST /pair/exchange`(익명) · `POST /pair` · `GET /pair` · `POST /pair/cancel`(admin). 본문 · 응답은 4.2 · 4.4 |
| 와이어 — 워커 프로토콜 | `POST /worker/register` 본문에 선택 키 `pair_proof`. 새 응답 409 `pool_mismatch`(+ `bound_pool`) · 403 `pair_proof_required` · 403 `pair_proof_invalid`. `GET /worker/spec` 이 풀로 걸러지고 질의 `pool` 을 받는다. 옛 서버는 `pair_proof` 를 무시한다(본문의 모르는 키) |
| 종료 코드 | `rcm worker` 3 = 기동 때 서버 신원을 확인하지 못함(새). `rcm pair` 0/1/2/3, `rcm setup --pair` 0/1/2, 클라이언트 명령의 신원 실패 2, 옛 토큰 + `auto` 2 |
| CLI | `rcm pair` 와 플래그(4.6), `rcm setup --pair`, `rcm token list` 의 뒤 두 열, `rcm worker --check` 의 `server identity` 행 |
| 설정 키 | `worker.toml` · `client.toml` 의 `pair_secret`(문자열 `"<hex32>:<hex64>"`, 파일 0600 필수). 서버 설정 키는 더하지 않는다(`pair.key` 자리는 `<data_dir>/pair.key` 고정) |
| 상수 | 코드 10자 base32 · 창당 실패 5 · TTL 기본 10분 / 상한 60분 · 챌린지 120초 · 익명 본문 1 KiB · 다시 찾기 후보 8 |

### 4.8 호환

옛 판과 새 판이 섞였을 때의 결과를 조합마다 적는다.

| 조합 | 결과 |
|---|---|
| 옛 워커(명시 URL) ↔ v27 서버 | 오늘 그대로. spec 은 등록한 풀로 걸러진다 |
| 옛 워커(`auto`) ↔ 어느 서버 | 새 빌드면 거절(283). 옛 빌드 워커는 오늘대로(새 서버가 막지 못한다 — 옛 워커는 hello 를 모른다) |
| 짝지은 워커 ↔ 옛 서버(hello 404) | 토큰 0, 종료 3(fail-closed) |
| 옛 빌드가 `pair_secret` 있는 `worker.toml` 을 읽음 | `unknown key(s): pair_secret` 로 거절(F20 의 오늘 규칙) |
| v26 빌드가 v27 DB 를 엶 | `newer_database_message` 로 거절(F3) |
| 옛 클라이언트(`rcm pair` 없음) | 짝짓기를 쓰지 않는다. 오늘 그대로 |

M5t-b 와는 펜싱에서 만난다. 결정 255 의 펜싱, 곧 새 세션이 옛 세션을 밀어내는 일은 짝지은 워커면 증명이 있어야
일어난다. 이 명세는 M5t-b 의 결정(펜싱 종료 0, 프로토콜 불일치 종료 3, 프로세스당 세션 id, DB v26)을 전제로 한다. 세션을
여는 호출의 이름은 M5t-b 의 세션 PR 이 정한 이름을 따르고 PR2 · PR3 이 그 이름을 쓴다. `rcm worker` 의 종료 3 은 프로토콜
불일치와 서버 신원 미확인 두 경우에 쓰인다. 두 경우 모두 「안전한 세션을 못 열었으니 감독자가 다시 시도」라는 뜻이고
진단 문구만 다르다(Q-C).

## 5. 작업 분할과 병렬

PR 하나에 브랜치 하나, 워크트리 하나를 쓴다. 브랜치 이름은 시작할 때 `branch` 스킬로 확정하며 아래 이름은 제안이다.
`shared` 열의 파일에는 덧붙이기만 한다(registry).

| PR | 브랜치(제안) | 내용 | 시작 조건 | 물결 | 함께 도는 PR | owns | shared | 머지 순서 | lane(추천 · 이유) |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5tc-pairing-identity-workplan` | 이 명세, PLAN.md(머리 판 줄 · M5t-c 항목 「계획」 → 명세 · 결정 280~299 · DB v27), `docs/m5n-workplan.md:650` 에 개정 날짜와 결정 280 | — | 0 | — | `docs/m5tc-workplan.md`, `docs/m5n-workplan.md` | `PLAN.md` | 먼저 | — (명세 PR) |
| PR1 | `feat/server-pairing-window-and-hello` | DB v27 · `pair.key` · 짝짓기 창(메모리) · `/pair/hello` · `/pair/exchange` · `/pair` 관리 라우트 · 순수 `core/pairing.py`(인코딩 · MAC · 챌린지 · Host 정규화 · 코드 · 이름 정리) · 서버 믹스인 `pairing.py` · PLAN 「보안」 익명 예외 문장 · 「서버 API」 행 · 「저장소」 tokens 칸 · 문서 잠금 | PR0 머지, M5t-b 의 v26 PR 이 dev 에 머지 | 1 | — | `src/remote_ci_monitor/core/pairing.py`(새), `src/remote_ci_monitor/pairing.py`(새), `src/remote_ci_monitor/store.py`, `src/remote_ci_monitor/server.py`, `tests/test_docs_m5tc.py`(새), `tests/test_pairing_server.py`(새) | `PLAN.md`, `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md` | — | full · DB 판이 오르고 새 익명 라우트(보안 · 와이어) |
| PR2 | `feat/worker-token-bound-pool` | 등록의 풀 검사 · 증명 검사 · 다른 주소 재등록 로그 · `/worker/spec` 풀 거르기(`spec_presets_for_pool`) | PR1 머지, M5t-b 의 등록 · 세션 PR 이 dev 에 머지 | 2 | PR3 | `src/remote_ci_monitor/remote_workers.py`, `src/remote_ci_monitor/capabilities.py`, `tests/test_pairing_pool.py`(새) | `src/remote_ci_monitor/server.py`(`worker_spec_for` 한 곳), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `tests/test_docs_m5tc.py` | PR2 먼저 | full · 워커 프로토콜(와이어)과 등록 · 펜싱(동시성) |
| PR3 | `feat/client-verify-server-before-token` | `pair_secret` 설정 키 · `_check_private` · 관문(F19 여섯 자리) · 워커 기동 hello · 다시 찾기 검증 · 등록 증명 보내기 · 옛 토큰 + `auto` 거절(워커 · 클라이언트) · `_follow_first` · `_worker_follow_start` 순서 · `rcm worker --check` · `rcm check` 행 | PR1 머지 | 2 | PR2 | `src/remote_ci_monitor/config.py`, `src/remote_ci_monitor/client.py`, `src/remote_ci_monitor/remote_worker.py`, `src/remote_ci_monitor/follow.py`(`_headers` 관문만), `tests/test_pairing_client.py`(새), `tests/test_pairing_worker_e2e.py`(새) | `src/remote_ci_monitor/cli.py`(`_resolve_server` · `_follow_first` · `_worker_config` · `_worker_follow_start` · `_worker_check` · `cmd_check`), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `tests/test_docs_m5tc.py` | PR2 뒤, dev 를 합친 뒤 | full · 보안(토큰이 어디로 가나)과 신호 · 재시작 경로(재연결 · 다시 찾기) |
| PR4 | `feat/cli-pair-command-and-setup-pairing` | `rcm pair`(열기 · 기다리기 · 취소) · `rcm setup --role worker --pair` · `--role client --pair` · `rcm token list` 두 열 · `NO_FOLLOW_COMMANDS` 에 `pair` · 평문 · 이름 URL 경고 | PR3 머지(`cli.py`), M5t-a PR4(setup) 머지 | 3 | — | `src/remote_ci_monitor/cli.py`, `tests/test_cli_pair.py`(새), `tests/test_setup_pair.py`(새) | `src/remote_ci_monitor/follow.py`(`NO_FOLLOW_COMMANDS` 한 줄), `CHANGELOG.md`, `scripts/mutcheck.py`, `docs/configuration.md`, `README.md`, `tests/test_docs_m5tc.py` | — | full · 토큰 · 코드를 찍고 받는 CLI(보안) |
| PR5 | `docs/pairing-and-server-identity-guide` | `docs/operating.md`(`pair.key` · 신원 재설정 · `data_dir` 초기화 뒤 다시 짝짓기 · Docker 와 프록시의 `public_url`) · `docs/usage.ko.md` · README 「워커 붙이기」 · 예시 주석 · CHANGELOG 정리 | PR4 머지 | 4 | — | `docs/operating.md`, `docs/usage.ko.md`, `examples/worker.toml`, `examples/client.toml` | `README.md`, `CHANGELOG.md`, `docs/configuration.md`, `tests/test_docs_m5tc.py`, `tests/test_examples.py` | — | short · `src/` 를 바꾸지 않는다 |
| PR6 | `test/m5tc-live-pairing-acceptance` | 실기 하네스 `scripts/acceptance/m5tc_live.sh` · PI31 · PI32 · 보고서 `docs/acceptance/reports/<날짜>-m5tc-acceptance.md` | PR5 머지 | 5 | — | `scripts/acceptance/m5tc_live.sh`(새), `docs/acceptance/reports/`(새 파일) | — | — | short · 실기 그 자체가 증명이고 `src/` 를 바꾸지 않는다 |
| 릴리스 | — | 버전은 M5t-a · M5t-b 와 함께 메인 세션이 정한다 → dev → main(§8 개인정보 점검) → 태그 | PR6 | — | — | — | — | — | — |

lane 열의 값은 결정 298 로 확정됐다(위임, 2026-11-26). 물결 2 의 PR2 와 PR3 은 `src/` 파일이 겹치지 않는다. PR2 는
`remote_workers.py` · `capabilities.py` 와 `server.py` 한 곳을 고치고 PR3 은 `config.py` · `client.py` · `remote_worker.py` ·
`follow.py` · `cli.py` 를 고친다. `server.py` 와 `cli.py` 는 각각 한 PR 만 고친다. 묶음 경로가 겹치지 않는지는
`tools/dev_batch.py` 로 확인한다.

## 6. 진행 체크리스트

상태 칸은 PR 이 머지될 때 메인 세션이 표시한다.

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 명세 작성(1판) · 시나리오 추적 | PR0 | — | [ ] |
| 1 | 격리 반박 라운드(HIGH 없을 때까지) · §12 반영 | PR0 | 0 | [ ] |
| 2 | 결정 페이지: lane 행 298 · Q-A · 메인 세션 확인 Q-B · Q-C. 2026-11-26 위임 결정으로 모두 답이 났다(§1 · §11) | — | 1 | [x] |
| 3 | PR0 머지(명세 · PLAN · M5n §13 개정 날짜) | PR0 | 2 | [ ] |
| 4 | M5t-b 의 DB v26 PR 머지 확인 | — | — | [ ] |
| 5 | PR1 서버 신원 · 짝짓기 창 · DB v27 | PR1 | 3 · 4 | [ ] |
| 6 | M5t-b 의 등록 · 세션 PR 머지 확인 | — | — | [ ] |
| 7 | PR2 토큰 풀 · 재등록 증명 · spec 거르기 | PR2 | 5 · 6 | [ ] |
| 8 | PR3 토큰 전 확인 · 옛 토큰 + `auto` 거절 | PR3 | 5 (머지는 7 뒤) | [ ] |
| 9 | M5t-a PR4(setup) 머지 확인 | — | — | [ ] |
| 10 | PR4 `rcm pair` · `setup --pair` · `token list` | PR4 | 8 · 9 | [ ] |
| 11 | mutcheck 변이(§7)가 PR 마다 들어갔는지 | PR1~PR4 | — | [ ] |
| 12 | PR5 문서 | PR5 | 10 | [ ] |
| 13 | PR6 실기 수용 · 보고서 | PR6 | 12 | [ ] |
| 14 | CHANGELOG `[Unreleased]` 정리 | PR5 · 릴리스 | — | [ ] |
| 15 | 운영 설정 확인(Q-B): `auto` + 옛 토큰 머신이 있으면 반영 전에 다시 짝짓거나 명시 URL. 이 Mac 은 명시 URL 임을 확인했다(2026-11-26), 다른 머신이 남았다 | — | 13 | [ ] |
| 16 | 릴리스 PR dev→main 개인정보 점검(§8) · 태그 | 릴리스 | 13 · 14 | [ ] |
| 17 | 운영 반영(현행 규칙, 큐가 빌 때 재시작) | 운영 반영 | 15 · 16 | [ ] |

## 7. 시험 계획

시나리오 표의 접두어는 PI 다. 「어디서」 칸 끝의 `(live)` 는 실기로만 증명하는 행이다. `(live)` 를 칸 가운데에 두면 추적
도구가 실기 행으로 세지 않으므로 실기는 PI31 · PI32 두 행으로 모았다(§12).

<!-- scenario-prefix: PI -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| PI1 | 〔LAN 위장 · 불가역〕 짝지은 워커(`pair_secret` 있음). (a) 기동할 때 설정 URL 의 주인이 위장 서버다 (b) 도는 중에 연속 연결 실패 5번 뒤 mDNS 로 다시 찾는데 위장 서버만 답한다. 위장 서버는 TXT 를 베꼈고 hello 에 엉터리 `mac` 을 주며 `/worker/spec` 의 `probes[].argv` 와 claim argv 에 표식 파일을 만드는 명령을 싣는다 | 위장 서버가 받은 요청은 `POST /pair/hello` 뿐이다. `Authorization` 헤더 0건, `/worker/spec` · register · claim · health 0건, 표식 파일 없음. (a) 워커는 종료 3. (b) 옮기지 않고(`server moved` 줄 없음) 설정 URL 로 계속 시도한다. 로그에 이유와 「다른 머신이거나 서버의 `data_dir` 초기화 · 토큰 폐기일 수 있다 — 다시 짝짓는 법」이 있고 토큰 · `pair_secret` 값은 없다 | e2e · 가짜 mDNS 응답기 + 요청 기록 서버(루프백), 종료 코드 · stderr · 표식 파일 | PR3 |
| PI2 | 〔LAN 위장 · 따라가기〕 같은 위장 서버의 `/api/health` 가 더 높은 `version` 과 표식을 만드는 wheel 의 `client_wheel`(sha256 일치)을 싣는다. 짝지은 워커가 따라가기를 켠 채 기동하고, 짝지은 클라이언트가 같은 서버로 `rcm jobs` 를 친다 | hello 가 실패하므로 워커 기동 ①의 health 도 `/client/<wheel>` 받기도 없다. 주입한 가짜 `execve` 호출 0, `$XDG_CACHE_HOME/rcm/clients` 에 새 폴더 0, 워커 종료 3. 클라이언트도 health · wheel 요청 0, 종료 2 | e2e · 요청 기록 서버 + 가짜 `execve`, 캐시 폴더 목록 | PR3 |
| PI3 | 〔옛 토큰 · auto〕 (a) `pair_secret` 없는 워커 설정의 `server` 가 `auto` 또는 빈 값이다 (b) `pair_secret` 없는 클라이언트가 토큰을 가진 채 `server = "auto"` 다 (c) 옛 워커가 명시 URL 이고 서버 주소가 바뀌었다 | (a) `rcm worker` · `rcm worker --check` 가 mDNS 질의를 하나도 보내기 전에 종료 2 로 끝나고 다시 짝짓는 법과 명시 URL 을 쓰는 법을 말한다. (b) 토큰을 보내는 명령이 같은 이유로 종료 2 이고 위장 서버의 요청 기록이 비어 있다. (c) 오늘처럼 그 URL 로만 다시 시도하고 다른 주소로 옮기지 않는다 | e2e · 요청 기록 서버 + 가짜 mDNS, 종료 코드 · 질의 수 | PR3 |
| PI4 | 〔DHCP · 짝지은 워커〕 (a) 진짜 서버가 새 주소로 옮겼다 (b) 옛 주소를 다른 장치(위장)가 받았고 워커는 연결이 끊겼다 돌아와 같은 URL 로 첫 요청을 보낸다 | (a) 연속 연결 실패 5번 뒤 후보마다 hello 를 하고 통과한 새 주소로만 옮긴다(로그 `server moved`). 재등록은 새 챌린지 증명으로 되고 claim 이 이어진다. (b) 돌아온 뒤 첫 요청이 hello 이고 위장 장치가 받은 `Authorization` 은 0건이다. hello 를 「mDNS 로 찾았을 때만」 거는 구현은 (b) 에서 실패한다 | e2e · 기록 서버 + 연결 끊김 주입, 워커 로그 | PR3 |
| PI5 | 〔LAN 중계 · 재생〕 (a) 위장 서버(다른 IP)가 hello 를 Host 그대로 진짜 서버에 중계한다 (b) Host 를 진짜 서버 주소로 고쳐 중계한다 (c) 엿들은 hello 응답을 다음 접속에 재생한다 | (a) 진짜 서버가 421 `host_not_this_server` 로 거절하고 본문에 `mac` · 챌린지가 없다. (b) 진짜 서버 주소로 계산된 `mac` 이라 워커가 자기가 접속한 Host 로 검증하면 어긋난다. (c) 호출마다 새 nonce 라 어긋난다(1000번 부른 nonce 가 모두 다르다). 셋 모두 워커의 토큰 전송 0건이고 hello 처리 중 Store 호출 0(spy) | 단위 · 서버 hello · `core/pairing.py` 검증 함수, e2e · 중계기 | PR1 |
| PI6 | 〔LAN 중계 · 이름 URL〕 워커 URL 이 이름(`http://<광고 이름>.local:<port>`)이다. 위장 서버가 그 이름을 가로채고 hello 를 진짜 서버 IP 로 보내되 Host 는 그 이름 그대로 중계한다 | 중계가 통과한다(결정 282 가 받아들인 남는 위험). 시험은 이 중계를 실제로 돌려 통과함을 고정한다. §9 남는 위험 표에 이 줄이 있고, `rcm pair` 가 이름 URL 을 찍을 때와 `rcm setup --pair` 가 이름 URL 을 받을 때 stderr 에 이름 URL 경고가 나온다 | e2e · 중계기 + 이름 해석 주입, stderr | PR4 |
| PI7 | 〔권한 상승 · 불가역〕 worker 창의 코드로 본문 `kind` 를 `client` · `admin` · 모르는 값 · 없음으로 바꿔 교환한다. `POST /pair` 에 `kind: "admin"` 을 보낸다 | 넷 모두 틀린 코드와 같은 404 이고 `tokens` 행이 늘지 않는다. `POST /pair` 의 `admin` 은 400 이다. worker 창으로 받은 토큰으로 클라이언트 API 는 403 `worker tokens cannot use the client API` 다 | 단위 · 교환 응답 바이트 + 읽기 전용 DB 사본의 `tokens` 행 수 | PR1 |
| PI8 | 〔동시 · 응답 유실〕 (a) 같은 유효 코드로 교환 16개를 동시에 보낸다 (b) 서버가 토큰 행을 쓴 직후 응답이 닿기 전에 연결이 끊긴다 | (a) 200 은 정확히 1건, `tokens` 행 +1, 나머지는 같은 404. (b) 창은 쓰였고 워커는 토큰이 없다. `rcm token list` 에 그 이름이 `paired from` 과 함께 보이고 폐기하면 그 토큰은 401 이다. `rcm setup --pair` 는 「발급됐지만 저장하지 못했다 — 코디네이터에서 `rcm token revoke <이름>` 뒤 새 창」을 말하고 종료 1 이다 | 단위 · 스레드 16개 + DB 사본, e2e · 응답 직전 연결 끊기 | PR1 · PR4 |
| PI9 | 〔무차별 대입 · 방해〕 열린 창에 틀린 코드를 보낸다. 한 출처에서 5번, 다섯 출처에서 한 번씩, 그 뒤 맞는 코드 | 다섯 번째 실패에서 창이 닫히고(출처와 무관하게 창에 센다) 그 뒤 맞는 코드도 같은 404 다. 기다리던 `rcm pair` 는 「틀린 코드 5번으로 창이 닫혔다 — 새 창을 연다」를 찍고 종료 1, 서버 로그에 같은 이유 한 줄. 코드 비교가 `hmac.compare_digest` 를 부른다(spy). 시험이 5 ÷ 32^10 을 계산해 명세 숫자(≈ 4.4e-15)와 대조한다 | 단위 · 교환 응답 · spy, e2e · `rcm pair` stdout · 종료 코드 | PR1 · PR4 |
| PI10 | 〔정찰〕 익명으로 틀린 코드 · 닫힌 창 · 연 적 없는 창 · 만료된 창 · 이미 쓴 코드 · 다른 kind 로 교환한다. 창이 열린 동안과 닫힌 동안 `/api/status` · `/events` · `/api/machines` · `/api/health` · mDNS 응답을 읽는다 | 여섯 경우의 상태 코드 · 헤더 이름 · 본문 바이트가 같다. 같은 시각 · 같은 표본을 주입하면 읽기 라우트 응답과 `Responder.response()` 바이트가 창이 열렸을 때와 닫혔을 때 같다. TXT 키는 `v` · `name` · `lanes` 셋뿐이다 | 단위 · 응답 바이트 비교 | PR1 |
| PI11 | 〔열어 둔 창 · 시계〕 창을 열고 둔다. TTL 이 지난다. 서버를 재시작한다. 코디네이터가 reload 한다. `--ttl 61`. 워커 시계가 1시간 어긋나 있다 | 만료는 서버 시계로만 판정한다(워커 시계와 무관하게 TTL 안 성공 · 뒤 404). 재시작하면 창이 사라져 교환이 404 이고, 기다리던 `rcm pair` 는 「서버가 다시 시작해 창이 사라졌다」로 종료 1. reload 뒤 `GET /pair` 의 `expires_at` 이 같다. `--ttl 61` 은 `rcm pair` 종료 2(서버는 400). 열려 있는 동안 `rcm pair` 가 만료 시각과 남은 분을 찍고 `rcm pair --cancel --worker` 로 바로 닫힌다 | 단위 · 주입 시계, e2e · 재시작 | PR4 |
| PI12 | 〔창 둘 · 이름〕 (a) 같은 kind 창을 연 채 하나 더 연다 (b) worker 창과 client 창을 함께 연다 (c) 이미 있는 이름(폐기된 이름 포함)으로 연다. `--name` 없이 호스트 이름이 `Dev's MacBook Pro` · 65자 · 한글뿐 · 제어문자 포함이다 | (a) 두 번째 `rcm pair` 는 409 로 종료 1 이고 남은 시간과 `rcm pair --cancel` 을 말한다. 첫 창의 코드는 그대로 유효하다. (b) 둘 다 열리고 한 kind 의 코드는 다른 kind 창에 404 다. (c) `--name` 의 이름이 있으면 창을 열 때 409 `name_taken`, 호스트 이름에서 나온 이름이 있으면 교환이 409 `name_taken` 이고 창은 열린 채다. 호스트 이름은 `Dev-s-MacBook-Pro` · 64자 · `worker-<hex6>`(남는 글자가 없을 때)처럼 `_NAME_RE` 에 맞게 바뀐다 | 단위 · 이름 정리 함수, e2e · `rcm pair` 종료 코드 · `rcm token list` | PR4 |
| PI13 | 〔틀린 풀〕 풀이 묶인 워커가 (a) 등록 본문의 `pool` 을 다른 풀로 보낸다 (b) worker.toml 의 `pool` 을 고치고 다시 시작한다 (c) 묶인 풀로 등록한다 | (a) (b) 등록이 409 `pool_mismatch` 와 `bound_pool` 이고 `workers` 행이 생기거나 바뀌지 않으며 그 이름의 도는 잡이 `lost` 가 되지 않는다. 워커는 묶인 풀과 고치는 법을 로그에 적고 종료 2. (c) 지금처럼 등록된다 | 단위 · `POST /worker/register` + DB 사본, e2e · 워커 종료 코드 | PR2 · PR3 |
| PI14 | 〔다른 풀 비밀〕 풀 X 에 묶인 worker 토큰과 기본 풀 worker 토큰이 각각 `GET /worker/spec` 을 읽는다. 기본 풀 프리셋의 `env` 에 값이 있다 | X 토큰의 `presets` 에는 `pool`/`pools` 에 X 가 있는 프리셋과 `any-capable` 프리셋만 있고, 기본 풀 프리셋의 이름 · `env` 값은 응답 전문에 없다(grep). `local_only` 프리셋은 어느 토큰에도 없다 | 단위 · `/worker/spec` JSON 전문 | PR2 |
| PI15 | 〔기본 풀 · 불가역〕 `--pool` 없이 연 창으로 짝지은 워커가 기본 풀에 들어온다. 서버에는 `local_only = true` 인 배포 흉내 프리셋의 대기 잡과 기본 풀의 평범한 게이트 잡이 있다 | 그 워커는 게이트 잡을 받고, `local_only` 잡에는 claim 이 204 이며 그 잡은 `queued` 로 남는다. 짝짓기가 결정 254 의 후보 함수를 우회하는 길이 claim · spec 어디에도 없다 | e2e · 시험 서버 설정의 가짜 `sh -c` 프리셋 둘, 잡 상태 | PR2 |
| PI16 | 〔풀 NULL〕 v27 전에 만든 명시 토큰(`tokens.pool` NULL)으로 (a) 등록 · claim · finish 한다 (b) 등록한 뒤와 등록 전에 `/worker/spec` 을 읽는다 (c) `rcm token list` 를 본다 | (a) 마이그레이션 직후에도 오늘과 같다. (b) 등록한 뒤에는 그 워커가 등록한 풀로, 등록 전에는 질의 `pool`(없으면 `default`)로 거른다. 거르지 않은 전체가 나오는 길은 없다. (c) `pool` 열이 「—」다 | 단위 · 마이그레이션한 DB 사본 + `/worker/spec` · `rcm token list` | PR2 · PR4 |
| PI17 | 〔비밀 · 공개 레포〕 짝짓기의 모든 길(성공 · 틀린 코드 · 닫힌 창 · hello 실패 · `rcm pair` · `rcm setup --role worker --pair` · `--role client --pair`)을 돈다 | 코드 · 토큰 · `pair_secret` · `pair.key` 값이 stdout · stderr · 서버 로그 · 워커 로그 · `events` 표 · `/api/*` 응답 어디에도 0건이다(코드는 `rcm pair` 가 창을 열 때 stdout 에 한 번만). 코드는 프롬프트 · stdin 으로만 받고 `--code` 는 종료 2 다. 토큰 · `pair_secret` 을 담은 파일과 `pair.key` 는 umask 가 느슨해도 만들 때부터 0600 이고, 0644 로 바꾼 `worker.toml` · `client.toml` 은 로드가 거절되며 0644 `pair.key` 는 서버 기동이 종료 2 로 거절된다 | 단위 · 전체 grep · `stat` 모드 | PR4 |
| PI18 | 〔DB 사본〕 서버 DB 사본(마이그레이션 백업 · 읽기 전용 열기)으로 서버 신원을 만들려 한다 | 사본에는 `tokens.pair_id` 와 토큰 sha256 만 있고 `pair_secret` 도 `pair.key` 도 없다. 사본과 새 `pair.key` 로 띄운 서버의 hello 는 짝지은 워커의 검증을 통과하지 못한다. `data_dir` 을 초기화한 서버는 새 `pair.key` 를 만들고 다시 짝지으면 복구된다 | 단위 · 읽기 전용 사본 · 파일 모드, e2e · 키를 바꾼 서버 | PR1 |
| PI19 | 〔폐기 · 다시 짝짓기〕 (a) `rcm token revoke <worker>` 를 도는 워커에 건다 (b) `rcm pair --worker --name <그 이름> --replace` 로 다시 짝짓는다 (c) `data_dir` 을 초기화한 서버에 옛 워커가 붙는다 | (a) 폐기한 순간부터 `/worker/*` 호출이 모두 401 이고 그 워커의 도는 잡은 `worker_timeout_seconds` 뒤 `lost` 로 닫힌다. (b) 교환이 그 행을 한 문장으로 바꿔(새 sha256 · `pair_id`, `revoked_at` NULL) 옛 토큰은 401, 활성 토큰은 정확히 1개이고 둘 다 유효하거나 둘 다 무효인 순간이 없다. (c) hello 가 실패해 붙지 않고(PI1) 로그가 위장 · 초기화 두 가능성을 말한다 | 단위 · 호출별 상태 + DB 사본, e2e | PR4 |
| PI20 | 〔익명 라우트〕 PLAN 「서버 API」 표의 라우트 전체를 `Authorization` 없이 모든 메서드로 친다. 새 익명 라우트 둘에 1 KiB 를 넘는 본문 · 깨진 JSON · 동시 요청 상한 초과를 보낸다 | 익명 요청에 401 · 403 이 아닌 답을 하는 라우트 집합이 PLAN 「보안」 절이 적은 집합(원래 익명인 라우트 · `read_auth = none` 읽기 라우트 · `POST /pair/hello` · `POST /pair/exchange`)과 정확히 같다. 집합은 PLAN 표를 파싱해 얻는다. `/worker/*` · `POST /pair` · `GET /pair` · `POST /pair/cancel` 은 401 이다. 새 라우트는 413 · 400 · 503 으로 답하고 hello 는 Store 를 부르지 않는다 | 단위 · PLAN 표 순회 + 문서 잠금, e2e · 동시 연결 | PR1 |
| PI21 | 〔엿보기 · TLS 없음〕 같은 망의 수동 관찰자가 (a) 짝짓기 교환(코드와 응답의 토큰 · `pair_secret`) (b) 그 뒤의 `Authorization` 요청을 평문으로 본다. 엿본 코드를 정당한 교환 뒤에 쓴다 | 엿본 코드는 404 다(한 번만 쓰인다). 엿본 토큰 · `pair_secret` 으로 사칭할 수 있다는 것은 결정 282 의 남는 위험이고 §9 표에 이름으로 있다. `rcm pair` 가 찍는 URL 의 호스트가 루프백도 100.64.0.0/10 도 아니면 stderr 에 평문 경고가 나오고 `rcm setup --pair` 도 같은 경고를 낸다 | 단위 · 경고 함수, e2e · 캡처한 교환 재사용 | PR4 |
| PI22 | 〔bind · 주소〕 서버 bind 가 (a) `127.0.0.1`(`public_url` 없음) (b) 특정 LAN IP (c) `0.0.0.0` (d) Tailscale 대역 IP + 이름 `public_url` (e) `0.0.0.0` + 광고 켬일 때 `rcm pair` 로 창을 열고 워커가 화면의 URL 로 hello · 교환한다 | (a) `POST /pair` 가 409 `pair_unreachable` 이고 `rcm pair` 는 「다른 머신이 닿지 못한다 — bind 를 바꾸라」로 종료 1, 창은 열리지 않는다. (b)~(e) 찍힌 URL 마다 hello 가 통과한다. `0.0.0.0` · `::` 는 URL 에 나오지 않는다. 찍히지 않은 이름 Host(`public_url` 없는 MagicDNS 이름 등)는 421 이고 워커 메시지가 `public_url` 로 고치는 법을 말한다 | 단위 · 주입한 소켓 주소 · Host · 인터페이스 목록. 대역: 별칭 루프백 127.0.0.2 + 주입한 `public_url`(실제 tailnet 은 쓰지 않는다) | PR4 |
| PI23 | 〔탈취 토큰 · 재등록〕 로그 · `ps` · 복제된 설정에서 샌 worker 토큰을 다른 머신이 쓴다. (a) 짝지은 워커의 이름으로 증명 없이 · 틀린 증명 · 이미 쓴 챌린지 · 120초 지난 챌린지 · 다른 `pair_id` 의 챌린지로 등록 (b) 옛 명시 토큰으로 등록 (c) 클라이언트 API · `/pair` 관리 라우트 (d) 다른 워커의 잡 트리 · 로그 · finish | (a) 403 `pair_proof_required` 또는 `pair_proof_invalid` 이고 정당한 워커의 도는 잡은 `running` 그대로다(`mark_lost_for_worker` 를 부르지 않는다). (b) 오늘처럼 등록되고 옛 프로세스의 잡은 `lost` 다. 앞 등록과 다른 주소면 서버 로그에 두 주소가 한 줄 남는다. (c) 403. (d) 오늘처럼 404 · 403 `not your job` · 409 | 단위 · 호출별 상태 + DB 사본, e2e · 워커 프로세스 둘 | PR2 |
| PI24 | 〔클라이언트〕 (a) `rcm pair --client` 창 + `rcm setup --role client --pair` (b) 받은 `client.toml` (c) 짝지은 클라이언트의 서버 URL 을 위장 서버가 차지했다 (d) 토큰 없는 클라이언트가 `server = "auto"` 로 따라가기를 켠 채 위장 서버를 찾는다 | (a) kind 는 `client`(admin 아님), `rcm run` 이 되고 `/worker/*` 는 403, 풀은 NULL. (b) 0600 이고 `server` · `token` · `pair_secret` 이 있다. (c) 위장 서버가 받은 요청은 hello 뿐이고 명령은 종료 2(트리 업로드 · wheel 실행 0). (d) mDNS 찾기는 하지만 검증된 `pair_secret` 이 없으므로 위장 서버가 받은 `/client/<wheel>` 요청이 0건이고 주입한 가짜 `execve` 호출도 0이다(Q-A) | 단위 · 토큰 행 kind, e2e · 기록 서버 + 가짜 `execve` | PR3 · PR4 |
| PI25 | 〔코디네이터 화면〕 (a) 창을 열 때 (b) 워커가 붙었을 때 (c) 엉뚱한 머신이 붙었을 때 (d) `--pool` 에 오타를 썼을 때 | (a) 코드(이 화면에서만) · URL · 만료 시각 · kind · 풀 · 이름 · 그 풀로 갈 프리셋 수를 찍는다. (b) 완료 줄에 이름 · kind · 풀 · 워커가 말한 `host_name` · 서버가 본 접속 주소 · 시각과 다음 할 일(`rcm machines`, `rcm token revoke <이름>`)이 있다. 워커 쪽 `rcm setup --pair` 는 붙었다 · 풀 · 받을 프리셋 수 · 결과는 코디네이터로 가고 요청자의 `rcm wait` 가 받는다를 찍는다. 코드 입력 뒤 두 번째 승인은 없다. (c) 그 줄의 `host_name` · 주소와 `rcm token list` 의 `paired from` 으로 알아보고 `rcm token revoke` 한 번으로 끝난다. (d) 갈 프리셋이 0개면 창을 열 때 stderr 에 「0 presets reach pool …」 경고를 내고 창은 연다 | e2e · `rcm pair` stdout · stderr · `rcm token list` | PR4 |
| PI26 | 〔짝짓기를 쓰지 않음〕 v27 서버에서 `rcm token add NAME --worker` → `RCM_WORKER_TOKEN` → `rcm worker --check` → `rcm worker`(명시 URL). 옛 워커 ↔ 새 서버, 새 빌드의 옛 토큰 워커 ↔ 옛 서버, 짝지은 워커 ↔ 옛 서버(hello 404) | stdout 은 토큰 한 줄뿐이고 안내는 stderr, 종료 0. 등록 · claim · heartbeat · finish · 산출물 · 로그가 그대로다. `rcm worker --check` 의 기존 행 이름 · 종료 코드가 같고 더해진 `server identity` 행은 `warn` 「not paired」다. 짝지은 워커는 hello 가 404 인 옛 서버에 토큰을 보내지 않고 종료 3 이다 | e2e · 두 프로세스 · 종료 코드 · stdout 과 stderr 분리 | PR3 |
| PI27 | 〔같은 코드를 지나는 다른 길〕 `authenticate` · `verify_token` · `tokens` 표를 쓰는 길 전부: client 토큰 `rcm run · wait · cancel · jobs · top · machines`, admin 토큰 `rcm pause · resume · reload · gc · recheck`, `read_auth = basic` 의 Basic 읽기, `/api/whoami`, SSE `/events`, 보관함, 릴리스 드라이버 토큰 발급 · 폐기, `rcm token list`, `rcm discover`, `rcm check` | 길마다 상태 코드 · 종료 코드 · JSON 키가 변경 전과 같다. `rcm token list` 의 앞 네 열(name · kind · created · revoked)의 자리와 문구가 같고 `pool` · `paired from` 열은 뒤에 붙는다. `/api/whoami` · `rcm discover --json` 의 키가 그대로다. worker 토큰으로 읽기 라우트는 403, Basic 은 쓰기에서 거절된다 | 단위 · 길마다 한 호출 | PR1 · PR4 |
| PI28 | 〔마이그레이션 · 운영 DB〕 v25 · v26 사본(폐기된 토큰 · 등록된 워커 행 · 드라이버 토큰)을 새 빌드로 연다. v26 빌드로 v27 DB 를 연다. `rcm pair` 를 DB 없는 `data_dir` 의 서버 설정 + 서버 URL + admin 토큰으로 돌린다 | 마이그레이션 뒤 기존 토큰이 같은 이름 · kind · 폐기 상태로 검증되고 새 칸은 NULL, `workers` 행이 그대로다. 옛 빌드는 `newer_database_message` 로 거절하고 DB 를 바꾸지 않는다. `rcm pair` 는 DB 를 열지 않고 그 `data_dir` 에 파일이 생기지 않는다. 가드의 `DB_OPENING` 은 `token` 하나 그대로다 | 단위 · 사본의 `user_version` · 읽기 전용 열기 | PR1 · PR4 |
| PI29 | 〔문서 · 공개 레포〕 PLAN 「보안」 · 「서버 API」 · README · `docs/configuration.md` · `docs/operating.md` · 예시 · 시험 fixture | PLAN 「보안」 절의 익명 예외 문장이 PI20 의 집합과 같다(문서 잠금). README 발견 문장은 TXT 가 그대로라 그대로다. `docs/operating.md` 에 `pair.key` · 신원 재설정 · `data_dir` 초기화 뒤 다시 짝짓기 · Docker 와 프록시의 `public_url` 이 있다. 예시 · fixture 에 실제 토큰 · 코드 · `pair_secret` · 사설 IP · 호스트 이름이 0건이다(시험 값은 실행 중에 만들고 주소는 127.0.0.x · 192.0.2.x 만) | 단위 · 문서 잠금 · gitleaks | PR1 · PR5 |
| PI30 | 〔변이〕 §7 변이 목록의 변이를 하나씩 넣는다 | 변이마다 표에 적은 시험이 빨개진다. 하나라도 초록이면 그 행의 시험이 약하다 | 변이 · `scripts/mutcheck.py` | PR1~PR4 |
| PI31 | 〔실기 · 짝짓기 끝까지〕 시험 서버(자기 설정 · 포트 · `data_dir`, `advertise = false`)에서 `rcm pair --worker` → 다른 `HOME` · `data_dir` 의 일회용 워커가 `rcm setup --role worker --pair` → `rcm worker` → 기본 풀 가짜 프리셋 잡 → `rcm token revoke` | 워커가 붙어 잡을 끝내고 요청자의 `rcm wait` 가 0 으로 끝난다. 코디네이터와 워커의 완료 줄이 PI25 대로다. 폐기 뒤 워커 호출이 401 이다. 모든 로그에 코드 · 토큰 · `pair_secret` 이 0건이다 | 실기 하네스 (live) | PR6 |
| PI32 | 〔실기 · 위장과 웹〕 같은 시험 서버에서 별칭 루프백 주소에 위장 서버를 띄워 짝지은 워커의 URL 을 그리로 돌린다. 웹 UI 토큰 입력 · SSE 를 CDP 하니스로 본다 | 위장 서버 요청 기록에 hello 만 있고 워커는 종료 3 이다. 웹의 토큰 입력 · 큐 갱신이 변경 전과 같고 CDP 오류 수집기가 비어 있다 | 실기 하네스 · CDP (live) | PR6 |

드물어서 표에 넣지 않은 경우는 아래 Excluded 줄로 남긴다.

Excluded (rare): 같은 `pair.key` 를 가진 `data_dir` 복제 서버 둘이 한 망에서 동시에 답함 · ① 백업 복원 + 원본 가동 + 같은 망 · 결정 289 가 지원하지 않는다고 문서화하고 다시 짝짓기로 되돌린다 · reports searched, 0 hits
Excluded (rare): 교환 도중 SIGKILL 로 창의 실패 수가 사라짐 · ② · 창은 메모리라 재시작이 창을 닫는다(PI11), 되돌릴 수 있다 · reports searched, 0 hits
Excluded (rare): `--replace` 교환 둘이 같은 순간에 겹침 · ①②(같은 이름 창은 kind 마다 하나라 두 창이 동시에 있을 수 없고, 겹치려면 창 둘 + 동시 교환) · PI19 가 활성 토큰 1개를 본다 · reports searched, 0 hits
Excluded (rare): 챌린지 120초 안에 서버 벽시계가 뒤로 감 · ③ OS 시계 계약 · 결과는 등록 403 한 번 뒤 다시 hello, 되돌릴 수 있다 · reports searched, 0 hits

(history search: `git log -i --grep` 와 `CHANGELOG.md` · `docs/acceptance/reports/` 를 `pair` · `spoof` · `impostor` · `pair.key` · `replace` · `SIGKILL` 로 찾음 — 관련 0건. `replace` · `SIGKILL` 의 맞은 줄은 다른 기능의 것)

### 변이

`scripts/mutcheck.py` 에 더한다. 변이마다 적은 행의 시험이 빨개져야 한다(PI30).

| 변이 이름 | 바꾸는 것 | 빨개질 시험(행) | PR |
|---|---|---|---|
| `pair-hello-mac-not-checked` | 워커 검증 함수가 `mac` 비교 없이 참 | PI1 · PI5 | PR1 |
| `pair-hello-fixed-nonce` | nonce 를 상수로 | PI5 | PR1 |
| `pair-hello-host-check-skipped` | 서버 Host 판정을 건너뜀 | PI5 · PI22 | PR1 |
| `pair-code-reusable` | 성공한 교환이 창을 닫지 않음 | PI8 · PI21 | PR1 |
| `pair-exchange-trusts-body-kind` | 창의 kind 대신 본문 `kind` 로 발급 | PI7 | PR1 |
| `pair-wrong-code-not-counted` | 실패 세기를 지움 | PI9 | PR1 |
| `pair-code-compare-not-constant-time` | `compare_digest` → `==` | PI9(spy) | PR1 |
| `pair-open-without-admin` | `POST /pair` 의 `require_admin` 을 지움 | PI20 | PR1 |
| `register-pool-check-skipped` | 등록의 풀 검사를 지움 | PI13 | PR2 |
| `spec-null-pool-unfiltered` | 풀 NULL 이면 거르지 않음 | PI16 · PI14 | PR2 |
| `register-proof-skipped` | 증명 검사를 지움 | PI23 | PR2 |
| `pair-challenge-reusable` | 쓴 챌린지 집합을 지움 | PI23 | PR2 |
| `pair-gate-skipped-in-download` | F19 의 `download` 자리에서 관문을 건너뜀 | PI1 · PI2 | PR3 |
| `rediscover-unverified-move` | 다시 찾은 주소로 hello 없이 옮김 | PI1 · PI4 | PR3 |
| `legacy-auto-not-refused` | 옛 토큰 + `auto` 거절을 지움 | PI3 | PR3 |
| `pair-secret-logged` | 교환 로그 줄에 토큰을 넣음 | PI17 | PR4 |

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

## 9. 위험

이 설계가 막는 위험과 막는 법이다.

| 위험 | 막는 법 | 결정 · 시나리오 |
|---|---|---|
| 위장 서버가 짝지은 워커의 토큰을 받고 argv 를 돌린다 | 관문이 F19 여섯 자리 모두에 있고 hello 실패면 토큰 0 | 290 · 295 · PI1 · PI2 · 변이 `pair-gate-skipped-in-download` |
| 옛 토큰 + `auto` 워커 · 클라이언트가 업그레이드 뒤 멈춘다(의도된 거절이지만 운영이 멈춤) | 운영 설정을 반영 전에 확인(Q-B · §6 15). CHANGELOG `Changed` 에 적고 고치는 법을 문구에 둔다 | 283 · 285 · PI3 |
| hello 실패 종료 3 을 서비스가 계속 다시 띄워 로그가 쌓인다 | launchd `ThrottleInterval` · systemd 재시작 간격 안에서 한 줄씩. 진짜 서버가 돌아오면 붙는다 | F21 |
| 익명 hello · 교환이 서버를 바쁘게 한다 | 본문 1 KiB · 동시 요청 상한 · hello 는 Store 를 부르지 않는다 · 계산 1.88 µs(M1) · 교환 실패는 메모리만 | PI20 |
| 창 상태가 읽기 라우트 · TXT 로 드러난다 | 창은 메모리에만 있고 `/api/status` · TXT 에 칸이 없다 | PI10 |
| `pair.key` 가 느슨한 권한으로 남는다 | 만들 때 0600 `O_EXCL`, 읽을 때 권한 검사로 기동 거절 | 281 · PI17 |
| 교환은 됐는데 응답을 잃어 발급만 된 토큰이 남는다 | `rcm token list` 의 `paired from` 으로 보이고 setup 이 폐기 명령을 말한다 | PI8 |
| `--replace` 가 도는 워커의 잡을 끊는다 | 옛 토큰이 401 이 되어 `worker_timeout_seconds` 뒤 `lost`(M5n §13 의 자동 재실행 금지 유지). 사람이 고른 동작이라 경고는 `rcm pair` 가 「the old token stops working now」로 | PI19 |
| M5t-b 가 v26 을 늦게 내거나 번호를 바꾼다 | PR1 의 시작 조건. 늦으면 PR1 은 기다리고, 번호가 바뀌면 v27 을 그다음 번호로 바꾸는 것은 명세 개정(§13) | 299 · Q-C |
| M5t-b 가 워커 종료 코드나 세션 여는 호출을 바꾼다 | M5t-b 의 결정(펜싱 종료 0 · 프로토콜 불일치 종료 3 · 프로세스당 세션 id · DB v26)을 전제로 한다. 세션 여는 호출은 M5t-b 의 세션 PR 이 정한 이름을 PR2 · PR3 이 따른다. 종료 3 의 두 경우는 같은 뜻(안전한 세션을 못 열었으니 감독자가 다시 시도)이고 진단 문구만 다르다 | Q-C |
| Docker · 프록시 뒤 서버의 hello 가 421 | `public_url` 을 쓰라고 메시지와 `docs/operating.md` 가 말한다 | PI22 · PI29 |
| 운영 DB 를 다른 빌드가 연다 | `rcm pair` 는 HTTP, setup 은 Store 를 열지 않는다(M5t-a) | PI28 |
| 토큰 없는 `auto` 클라이언트가 찾은 위장 서버의 wheel 을 실행한다 | mDNS 찾기는 그대로 두고 wheel 을 내려받아 실행하는 것은 검증된 `pair_secret` 이 있을 때만 한다 | Q-A · PI24 |

### 남는 위험

이 설계로 막지 않는 위험이다. 행마다 이 명세가 대신 하는 일을 적었다.

| 남는 위험 | 이 명세가 하는 것 | 결정 |
|---|---|---|
| 같은 망의 평문 엿보기: 교환 응답의 토큰 · `pair_secret`, 요청의 토큰 | 막지 않는다. 코드는 한 번 · 짧은 수명. LAN · `0.0.0.0` URL 경고, Tailscale 권고 | 282 · PI21 |
| 이름 URL(`<host>.local` · 이름 `public_url`)을 가로챈 중계 | 막지 않는다. 이름 URL 경고, IP · Tailscale 주소 권고 | 282 · PI6 |
| 같은 IP 를 가로채 요청 사이에 끼어들기(ARP 스푸핑) | 막지 않는다(요청 서명 없음). 연결 실패 뒤에는 다시 hello | 282 · PI4 |
| 탈취 토큰으로 등록 없이 claim · heartbeat | 막지 않는다. 재등록(펜싱)만 증명으로 막는다 | 287 · PI23 |
| 틀린 코드 5번으로 창을 닫는 방해 | 막지 않는다. 코디네이터에 즉시 이유 | 288 · PI9 |
| `data_dir` 을 키와 함께 통째로 복사한 사람의 사칭 | 지원하지 않는 구성으로 문서화, 신원 재설정 절차 | 289 · PI18 |
| `any-capable` 프리셋 env 를 붙은 워커 모두가 받는다 | 비밀이 필요한 프리셋은 `local_only`(결정 254) | 284 · PI14 |
| 토큰 없이 명시 URL 로 따라가는 클라이언트는 그 URL 의 wheel 을 실행한다 | 막지 않는다. 명시 URL 은 사용자의 신뢰 결정이다. `auto` 로 찾은 서버는 검증된 `pair_secret` 없이 따라가지 않는다(위 표) | Q-A · PI24 |
| 명시 URL 의 옛 토큰 머신은 그 URL 을 믿는다 | 오늘과 같다. `rcm worker --check` 의 `warn` 이 다시 짝짓기를 권한다 | 283 · PI26 |

## 10. 요청자 문서에 돌려줄 것

없다. 하류 문서가 요청한 것이 없다.

## 11. 결정 표

「오너 확인」 열의 「확정(위임, 2026-11-26) · Codex 와 논의해 선 조치」는 오너가 위임한 결정을 메인 세션이 Codex 와
논의해 먼저 조치했다는 뜻이다. 오너 말대로 완료 보고서에 Codex 와 논의한 내용을 적는다. Q-A 는 2차 위임 결정이다.
280~299 가 모두 쓰여 번호를 매기지 못했다. Q-B 와 Q-C 는 결정 번호 없이 §1 의 2차 결정 표에 두었다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다름 |
|---|---|---|---|---|
| 280 | 자동 발급 · 자동 가입(M5n §13, Q1) | 사람이 `rcm pair` 로 연 짧은 수명(기본 10분 · 상한 60분) · 한 번 쓰는 창 안에서만, 그 창의 kind · 풀 · 이름으로 발급하고 가입한다. M5n §13 해당 줄(`docs/m5n-workplan.md:650`)에 개정 날짜와 이 번호를 적는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 「연결하면」이 사람 손 두 번(창 열기 · 코드 넣기)이 된다 |
| 281 | 신원 비밀 저장(Q12) | DB 밖 `<data_dir>/pair.key`(0600) 하나에서 `pair_id` 마다 파생. DB 에는 `pair_id` 만. 키를 잃거나 바꾸면 다시 짝짓기 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 282 | TLS 없는 LAN(Q13) | 엿보기 · 이름 URL 중계 · 같은 IP 가로채기를 받아들인다. 요청마다 서명하지 않는다. 남는 위험 표에 적고 LAN · 이름 URL 은 경고, Tailscale 을 권한다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 283 | 옛 토큰 + `server = "auto"` 워커(N1) | 거절하고 다시 짝짓는 법을 안내한다. 명시 URL 의 옛 토큰 워커는 그대로 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 오늘 되던 설정이 멈춘다 |
| 284 | `tokens.pool` NULL(N2) | 묶이지 않은 옛 토큰. 등록 때 말한 풀로 등록, `/worker/spec` 은 등록한 풀로 거른다. 「—」로 보인다. 기본 풀이나 무제한으로 보지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 285 | 클라이언트(N3) | 짝지은 클라이언트도 URL 과 `pair_secret` 을 저장하고 health · 따라가기 · 인증 요청 전에 hello, 조용한 재발견 없음. 옛 명시 토큰 클라이언트는 그대로, 토큰 + `auto` 는 283 규칙 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 오늘 되던 설정(토큰 + `auto`)이 멈춘다 |
| 286 | 두 번째 승인(N4) | 없음. 즉시 발급하고 붙은 머신 · 주소 · kind · 풀 · 시각 · 폐기 명령을 `rcm pair` 완료 줄 · 서버 로그 · `rcm token list` 에 남긴다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 287 | 탈취 토큰 재등록(N5) | 짝지은 워커의 재등록(세션 여는 호출)은 hello 챌린지로 만든 `pair_proof` 가 있어야 한다. 옛 명시 토큰은 토큰만. 검사는 `mark_lost_for_worker` 앞 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 288 | 창 닫기 방해(N6) | 받아들인다. 실패는 창에 센다(5번). 닫히면 서버 로그와 기다리던 `rcm pair` 에 이유와 새 창이 필요하다는 줄 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 289 | 신원 키 수명 주기 | 키를 잃거나 바꾸면 모든 신원 증명이 무효이고 다시 짝짓기. DB 만 복원 · 복제해서는 두 번째 신뢰 서버가 생기지 않는다. DB 와 키를 함께 두 살아 있는 코디네이터에 복원하는 것은 지원하지 않고 문서화된 신원 재설정(정지 → `pair.key` 삭제 → 시작 → 다시 짝짓기) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 290 | hello 의 꼴(설계) | `POST /pair/hello` 익명 · 상태 없음 · Store 를 부르지 않음. 자기 주소 = 요청이 도착한 소켓 주소 · `public_url` · 광고 이름. 길이 접두 인코딩 + 용도 라벨. TXT 에 `server_id` 를 더하지 않는다. 고른 안: 소켓 주소 판정. 버린 안: 인터페이스 주소 목록(F15 가 정확하지 않다), 보관함 허용 목록 재사용(F14 · 근본 원인 R1), `server_id` 대조(베낄 수 있다, 1라운드 A-M14) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 291 | 짝짓기 창(설계) | 메모리, kind 마다 하나, 10자 base32, 창당 실패 5, 모든 실패에 같은 404, 교환은 창 잠금 안에서. 버린 안: DB 표 `pair_windows`(익명 실패가 DB 를 쓰고, 재시작이 창을 살린다) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | 3판과 다름(§12) |
| 292 | `rcm pair`(설계) | HTTP + admin 토큰, DB 를 열지 않는다, 기다리며 완료 줄, Ctrl-C 는 창 취소. 버린 안: DB 를 직접 여는 꼴(가드 · 마이그레이션, 1라운드 B-H1) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | admin 토큰이 필요하다(M5t-a setup 은 client 토큰 명령만 찍는다) |
| 293 | DB v27 과 `--replace`(설계) | `tokens` 에 `pool` · `pair_id` · `paired_host` · `paired_addr` 와 `pair_id` 고유 색인. `--replace` 는 같은 행 한 UPDATE | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 294 | 풀 묶기 · spec 거르기(설계) | 등록 409 `pool_mismatch`. 거르기 규칙 하나(`spec_presets_for_pool`): 토큰 풀 → 등록 풀 → 질의 `pool` → `default` | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 295 | 확인 시점(설계) | 4.3 의 상태 전이 표. 기동 실패는 종료 3. 연결 실패 뒤 다시 hello. 다시 찾기는 hello 를 통과한 후보로만 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | `rcm worker` 에 종료 3 이 새로 생긴다 |
| 296 | 받는 쪽 CLI(설계) | `rcm setup --role worker --pair` · `--role client --pair`, 코드는 프롬프트 · stdin, 파일은 처음부터 0600, 완료 줄. 수동 `rcm token add` 는 바꾸지 않는다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 297 | 익명 예외(설계) | 인증 없이 받는 새 라우트는 정확히 둘: `POST /pair/hello` · `POST /pair/exchange`. `/worker/*` 는 인증을 먼저 보는 규칙 그대로. PR1 이 PLAN 「보안」 절에 적고 문서 잠금이 PI20 집합과 대조 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | §12 의 핸드오프 표가 적은 `POST /worker/hello` 와 경로가 다르다 |
| 298 | lane | PR1 full · PR2 full · PR3 full · PR4 full · PR5 short · PR6 short(이유는 §5) | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 299 | DB 판 순서 | v27 은 M5t-b 의 v26 뒤. PR1 은 v26 PR 머지 뒤 시작 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |
| 300 | 토큰 없는 클라이언트의 따라가기(P1-Q1) | 바꾼다. mDNS 찾기는 그대로 두고, 찾은 서버의 client wheel 을 내려받아 실행하는 것은 검증된 `pair_secret` 이 있을 때만 한다. 명시 URL 은 사용자의 신뢰 결정으로 남긴다. 결정 300(280~299 가 모두 쓰여 다음 빈 번호). 따라가지 않은 뒤에도 명령은 이어 가고 stderr 에 한 줄 남긴다 | 확정(위임, 2026-11-26) · Codex 와 논의해 선 조치 | — |

## 12. 격리 반박에서 바뀐 것

1판은 아직 반박 라운드를 거치지 않았다. 이 절에는 넘겨받은 지적에 답한 자리와, 시나리오 표를 옮기며 바꾼 곳을 적는다.

### 넘겨받은 지적

`docs/m5t-workplan.md` §12 의 「M5t-c 가 반드시 다룰 지적」이다. 답하지 못한 id 는 없다.

| 라운드 | id | 요지 | 이 판이 답한 자리 |
|---|---|---|---|
| 3 | A-H3 · B-H2 | 정확한 자기 주소 목록 | 목록을 만들지 않고 요청이 도착한 소켓 주소로 판정(§4.2 Host 판정 · F15 · 결정 290 · PI5 · PI22) |
| 3 | 근본 원인 R1 의 예 | 보관함 Host 허용 목록을 서버 자기 주소 목록에 재사용 | 재사용하지 않는다 — 모든 사설 IP 를 받기 때문(F14 · 결정 290) |
| 3 | B-M3 | DHCP, `auto` 와 옛 토큰 | 옛 토큰 + `auto` 거절(283 · 285 · PI3), 짝지은 워커는 hello 를 통과한 주소로만 옮김(4.3 · 295 · PI4) |
| 3 | B-M4 | `tokens.pool` NULL | 284 · 4.5 · PI16 |
| 3 | B-M8 | 엿들은 교환 | 코드 한 번(PI21), hello nonce 매번 · 챌린지 한 번 · 120초(4.2 · PI5 · PI23), 교환 응답 엿보기는 남는 위험(282) |
| 3 | L2 | HMAC 인코딩 | 길이 접두 + 용도 라벨, 함수 하나 `pair_mac`(4.2) |
| 2 | A-H8 · B-H1 · B-H2 · B-M1 | hello 의 중계, 기존 토큰, 짝짓기 주소 | 중계: Host 를 MAC 에 넣고 소켓 주소로 판정(PI5), 이름 URL 은 남는 위험(PI6). 기존 토큰: 283 · 285. 짝짓기 주소: `rcm pair` 가 찍은 URL 로만, mDNS 를 쓰지 않는다(4.4 화면 URL · PI22) |
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

### 시나리오 표를 옮기며 바꾼 행

시나리오 표는 설계를 보기 전에 따로 썼다. 그 표를 §7 로 옮기며 바꾼 행이다.

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

## 13. 구현하며 바뀐 것
