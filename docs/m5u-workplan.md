# M5u 작업 명세: 원격 워커 · 러너 · 감독자를 Rust 로

> 이 문서는 M5u 의 작업 명세 3판(2라운드 지적 반영)이다. 1판은 plan-lead 가 모은 재료(P1-1)로 썼다. 2판은 1라운드
> 지적으로, 3판은 2라운드 지적으로 고쳤다. 2라운드에서 검증자 1 은 HIGH 0 · MED 4 · LOW 7 · 미확인 2 를, 검증자 2 는
> HIGH 1 · MED 4 · LOW 5 · 미확인 1 을 냈다(§12). 다루는 것은 원격 워커 프로세스와 그 안에서 잡을 돌리는 러너, 되돌릴 수 없는 잡을 지키는
> 감독자를 Rust 로 다시 쓰는 일이다.
>
> 2026-11-27 오너는 이 세션에서 이렇게 말했다. 원문을 그대로 옮긴다.
>
> 「이 기능을 만드는데 어떤언어 어떤 프레임워크를 써서 만들고있는지도 알려주고 만약 RUST로 대체할 수 있다면 다 대체하도록
> 하고, 작업을 진행하도록 한다」
>
> 같은 날 범위를 묻는 질문이 갔다. 질문에는 지금 rcm 의 구성을 적었다. Python 3.11 표준 라이브러리만 쓴다. 서버 · 워커 ·
> CLI 가 약 7.7만 줄, 웹은 바닐라 JS 약 1만 줄, 저장소는 SQLite, bash 가 약 4.5천 줄, 시험은 pytest 14.3만 줄과 node 1.1만
> 줄이다. 오너는 「일부만 Rust 로」를 골랐다. 고른 선택지의 글은 이렇다.
>
> 「한 파일 바이너리 이점이 큰 워커와 러너, 감독자만 Rust 로 바꿉니다. 서버와 CLI 는 Python 으로 둡니다. 이것도 명세를 먼저
> 쓰고 진행합니다.」
>
> 같은 날 오너는 이어서 말했다.
>
> 「우선 이점이 큰거부터 진행하자 그리고 RUST로 변경하는 계획세우고 작업하는거는 아예 다른 멀티에이전트 워크트리
> 브랜치에서 작업해서 진행하도록 해 그리고 거기에서 지금 구현되어있는거랑 동일한 기능인지 테스트하고 머지하면되는거잖아」
>
> 「이 작업들은 먼저 스킬스를 가져와서 여기 프로젝트에 맞게 적용한 후에 그 스킬들을 활용해서 작업을 하는게 맞다고 생각해」
>
> 스킬 이식은 #287 로 머지됐고 이 명세는 그 스킬로 썼다. §1 의 표는 위의 말을 나온 순서대로 1~4 로 가리킨다.
>
> 결정 번호는 360 부터 393 까지 쓴다. 1판은 355 부터 썼다. 그런데 M5t-b 가 350~359 를 자기 범위로 두고 그 가운데
> 351~359 를 비워 두었다(`docs/m5tb-workplan.md:36` · `:2215` · `:2217`). M5t-c 는 280~319 와 340~349 를
> 쓰고(`docs/m5tc-workplan.md:29`), M5t 는 352~354 를 이미 썼다(`docs/m5t-workplan.md:1387-1425`). 핀 d07b253 에서
> `git grep -E "결정 ?(3[5-9][0-9]|4[0-2][0-9])"` 를 돌리면 이 명세 파일 밖에서는 350 · 352 · 353 · 354 만 나온다. 그래서
> 360 부터 쓴다. 1판의 결정 355~383 은 번호에 5 를 더해 360~388 로 옮겼다. 2판에서 새로 정한 결정은 389~393 이다(F57).
> 코드 줄 번호(`runner.py:357-373` 같은 것)와 M5t-b · M5t 의 결정 번호는 바꾸지 않았다.
>
> 핀은 `d07b253f23db7bf6ca572aedc67b5e1067dfe033` 이고 2026-11-27 의 origin/dev 다. v0.12.0 을 출시하고 운영에 반영한
> sha 다. 1판 핀 9c6de32 에서 바뀐 것은 §3 머리에 적었다. §3 의 `file:line` 은 모두 이 sha 의 것이다. 시나리오 접두어는
> `RW` 다. 이 핀에서도 `tools/scenario_trace.py --prefix-usage RW` 가 「no test uses it」를 냈다.

## 0. 한 줄

원격 워커 프로세스와 그 안의 러너, 감독자를 Rust 바이너리 하나로 다시 쓴다. 원격 워커 프로세스는 `rcm worker` 가 돌리는
것이다. 감독자는 되돌릴 수 없다는 표식을 단 잡(표식 잡)마다 따로 뜨는 프로세스다. 바이너리 이름은 `rcmw` 를 추천하고 Q13
에서 묻는다. 감독자는 같은 바이너리의 하위 명령이 된다. 서버, CLI, 웹, DB, 서버의 로컬 레인은 Python 그대로다.

Python 구현은 지우지 않는다. 워커 설정에 `implementation = "rust"` 를 준 머신만 Rust 로 돈다. 이번 마일스톤의 기본값은
`python` 이고 기본을 언제 바꿀지는 Q12 에서 묻는다.

「같은 기능」의 정본은 핀 sha 의 Python 이 실제로 하는 일이다. 그 동작을 골든 전사와 공유 벡터 파일로 얼린다. 골든 전사는
Python 워커가 보낸 요청과 디스크에 쓴 파일을 적어 둔 기록이다. 공유 벡터는 순수 판정의 입력과 기대 출력을 적은 파일이다.
두 구현이 같은 골든과 같은 벡터, 같은 e2e 시험을 통과해야 머지한다. Rust 를 고른 시험이 Python 으로 물러나 통과하는 길은
시험이 막는다.

순서는 시험 기반(골든 · 벡터 · 시험 선택기)부터다. 그다음은 Cargo 작업 공간과 감독자, 러너와 세션 폴더 파일, 워커
프로토콜 고리다. 이어서 git_ref · 산출물 · 호스트 사실 · 드레인을 옮긴다. 드레인은 서버 버전이 바뀌었을 때 새 잡을 받지
않고 도는 잡을 끝낸 뒤 같은 PID 로 다시 실행하는 동작이다. 그 뒤에 바이너리 배포, 진단 · 가드 · 문서, 실기가 온다.
되돌리기 어려운 것은 ⛔ 로 오너에게 묻는다. 배포 채널, 실행 형태, Linux 검증, 툴체인 설치, 크레이트 목록, PLAN 원칙
문장이 그렇다.

## 1. 출처와 오너 결정

### 1.1 요청과 이 명세가 정하는 것

| # | 요청(오너 말 번호) | 이 명세 |
|---|---|---|
| W1 | 1: 「어떤언어 어떤 프레임워크」를 알려 줌 | 이미 답함(2의 범위 질문 본문). 명세 대상 아님 |
| W2 | 1 → 2: 「RUST로 대체할 수 있다면 다 대체」 → 「일부만 Rust 로」 「워커와 러너, 감독자만」 | 대상은 `remote_worker.py` · `runner.py` · `job_supervisor.py` 와 워커가 쓰는 부분(F2). 서버 · CLI 는 Python. 서버 로컬 레인의 러너 · 감독자는 결정 380 ⛔ · Q17 로 묻는 중(기본은 Python, §1.2 의 5) |
| W3 | 2: 「한 파일 바이너리 이점」 | 바이너리 하나 `rcmw`(감독자는 같은 바이너리의 하위 명령). 파이썬이 없는 머신에서 도는가는 실행 형태(Q5)에 달림 — 기본 추천(형태 A)으로는 앞단이 Python 이라 아직 아님. workplan_diff 1 |
| W4 | 2: 「서버와 CLI 는 Python 으로 둡니다」 | 서버의 로컬 레인(`worker.py` 가 `runner.run_job` 을 씀, F5)을 Python 에 둘지는 결정 380 ⛔ · Q17 로 묻는 중(기본은 Python, §1.2 의 5). `rcm worker --check` · `rcm setup` · `rcm doctor` 는 Python |
| W5 | 2: 「명세를 먼저 쓰고 진행」 · 4: 스킬 먼저 | 이 명세(PR0)가 승인돼야 코드가 시작한다. 스킬 이식 #287 머지됨 |
| W6 | 3: 「우선 이점이 큰거부터」 | W2 의 세 조각이 「이점이 큰 것」이다(오너가 고른 선택지 글). 그 안의 순서는 머지 가능한 작은 조각부터(결정 361). 해석은 §4.2 |
| W7 | 3: 「아예 다른 멀티에이전트 워크트리 브랜치」 | PR 마다 자기 브랜치 · 워크트리(§5). 이 명세 브랜치도 따로(`docs/m5u-rust-worker-runner-workplan`) |
| W8 | 3: 「지금 구현되어있는거랑 동일한 기능인지 테스트하고 머지」 | 동치의 정본 · 골든 · 공유 벡터 · e2e 매트릭스(결정 360 · 362 · 373, §7). 머지는 PR 단위, 운영 전환은 별도(Q12) |

### 1.2 요청과 다른 곳

이 명세의 기본 추천대로 가면 오너 말과 다르게 되는 곳이 일곱 있다. 일곱 모두 오너가 아직 답하지 않았다. 이 문서의 표에
적힌 「§1.2 의 N」은 아래 목록의 N 번이다. §1.1 표의 「workplan_diff 1」도 아래 1 번이다.

§11 의 「요청과 다름」 칸은 이 일곱만 가리킨다. 1판에서는 그 칸에 PLAN 의존성 원칙, 운영 머신과 프리셋 변경, CLI 표면,
와이어에 더하는 키 하나도 적혀 있었다. 이것들은 요청과 다른 것이 아니라 이 명세가 얼린 것에 닿는다는 말이라서 2판에서
「정한 것」 칸으로 옮겼다.

1. 한 파일 워커 바이너리. 오너 말은 「한 파일 바이너리 이점이 큰 워커와 러너, 감독자만 Rust 로 바꿉니다」다. 형태 A 만으로는
   워커 머신에 Python 과 rcm 이 남는다. 앞단인 Python 이 `rcmw` 를 exec 하기 때문이다. 파이썬이 없는 머신은 Q5 의 (b) 인
   PR9 나 (c) 로만 된다. 형태 A 를 두는 이유는 기존 하네스로 동치를 증명하고 설정 오류 문장을 그대로 두려는 것이다(결정 363,
   Q5).
2. Rust 워커의 HTTP 범위. 오너 말은 「지금 구현되어있는거랑 동일한 기능」이다. Python 워커는 `https://` 를
   받고(`client.py:208`) 3xx 리다이렉트도 따른다(F55). Q11 의 기본대로면 Rust 워커는 `https://` 서버 앞에서 기동 전에 종료
   2 로 끝나고 3xx 를 따르지 않는다. 까닭은 둘이다. TLS 에는 큰 크레이트 묶음이 필요하다. 리다이렉트를 따르면 Bearer
   토큰이 다른 호스트로 간다(결정 377, Q11).
3. Linux 와 x86_64 워커. 오너 말은 「동일한 기능」이다. Q8 의 기본은 macOS arm64 용 `rcmw` 만 내고 다른 플랫폼은 Python
   워커를 계속 쓴다. 필수 체크가 도는 Mac 한 대에는 Linux 증거를 낼 길이 없다(F38 · F39, 결정 370, Q8).
4. 기본 워커 구현. 요청에 정의가 없다. M5u 에서는 `python` 이 기본이고 `rust` 는 머신마다 켠다. 요청에 이유가 없어서
   Q12 로 묻는다(결정 378).
5. 서버 로컬 레인의 러너와 감독자. 오너 말은 「워커와 러너, 감독자만 Rust 로」와 「서버와 CLI 는 Python 으로 둡니다」다. 로컬
   레인은 서버 프로세스 안에서 `runner.run_job` 을 부르므로(`runner.py:3-5`) 이 명세는 그 러너와 감독자를 Python 그대로
   둔다. 오너 말의 「러너, 감독자」에 로컬 레인이 드는지는 말에 없다. 1판은 이것을 「확정」으로 적었지만 해석이라서 2판은 ⛔
   로 묻는다(결정 380, Q17).
6. 순서. 오너 말은 「우선 이점이 큰거부터」다. 이 명세는 세 조각 안에서 위험이 작은 것부터 한다. 첫 PR 인 감독자는 이점이
   가장 작다(F6). Rust 사슬을 가장 작은 별도 프로세스로 먼저 검증하려는 순서다(결정 361, Q18).
7. 짝지은 머신과 `server = "auto"` 머신. 오너 말은 「동일한 기능」이다. Q2 의 기본이면 M5u 의 Rust 워커는 M5t-c 의 짝짓기
   확인 고리를 옮기지 않는다. 그래서 `pair_secret` 처럼 Rust 가 모르는 설정이 있거나 `server = "auto"` 면 기동 전에 종료 2
   로 끝난다. 이 거절은 서버 찾기(`_worker_config` 의 mDNS 갈래, `cli.py:4766-4769`)보다 먼저 돈다. 그래서 `--check` 와
   따라가기 ①보다도 먼저다. 그 머신은 Python 워커로 돈다. M5t-c 는 짝짓지 않은 `auto` 워커를 거절한다(`docs/m5tc-workplan.md` 결정 315).
   곧 M5t-c 뒤에는 `auto` 를 쓰는 워커가 모두 짝지은 워커다. 확인 고리를 옮기는 일은 그것만으로 마일스톤 하나 크기다.
   거절하면 확인을 거치지 않은 토큰을 보내는 일이 없다(결정 379 · 390, Q2).

### 1.3 이미 확정된 것

이 명세는 아래 결정에 기댄다.

- PLAN 결정 8 은 「Python 3.11+, 런타임 의존성 0, TOML 설정, 내부 UTC」다(`PLAN.md:645`). 의존성 원칙 문단은
  `PLAN.md:571` 이고 이식성 줄의 「런타임 의존성 0」은 `PLAN.md:35` 다. 이 문장을 바꿀지는 결정 371(Q10)이 묻는다.
- PLAN 결정 30 은 「GitHub 은 커밋·푸시·PR 머지에만 쓴다」다(`PLAN.md:668`). 그래서 워커나 서버가 실행 중에 GitHub 에서
  바이너리를 받는 안은 후보에서 뺐다(결정 382).
- PLAN 이식성 줄은 「macOS 와 Linux 빌드 머신을 둘 다 지원한다 … Windows 는 범위 밖」이다(`PLAN.md:34`). 결정 381 이 이를
  따른다.
- 결정 81(M5i I8-1)에서 서버는 자기 클라이언트 wheel 을 표준 라이브러리로 조립해 내준다. 「서버가 도는 코드」와 「클라가
  받을 수 있는 코드」가 갈라지지 않게 하려는 결정이다(`src/remote_ci_monitor/clientwheel.py:1-16`). 배포 채널(Q3)은 이
  기준을 따른다.
- 결정 79 에서 `RCM_CHROME` 을 주면 Chrome 시험은 건너뛸 수 없는 필수가 된다(`docs/m5r-workplan.md:596`).
  `RCM_REQUIRE_RUST` 가 같은 꼴이다(결정 369).
- 결정 196(M5r §13)에서 필수 체크는 하나로 정했다. 맥미니의 rcm 잡 `rcm-ci` 가 올리는 커밋 상태 `rcm/ci` 다. `ci.yml` 은
  `workflow_dispatch` 로만 돈다(CLAUDE.md · `docs/m5r-workplan.md:1260-1264`).
- M5t-b 의 결정 253 · 255 · 256 · 258 · 260 · 264 · 268 · 271 · 273 · 320~339 · 350 은 워커 프로토콜 2, 세션, 펜싱, 자기
  멈춤, 디스크 큐, 감독자, 홀드를 정했다. 펜싱은 같은 워커 이름으로 새 세션이 등록되면 옛 세션을 막는 것이다. Rust 는 이
  결정들을 바꾸지 않고 그대로 따른다(결정 385).
- M5q 의 결정 149~174 는 따라가기, 드레인, 같은 PID exec 를 정했다. Rust 도 같은 결과를 낸다(RW21).
- M5t-c 는 명세가 머지됐고 구현 전이다. 그 PR3 이 `config.py`(`pair_secret`), `client.py`(nonce 헤더와 거절 MAC 검증),
  `remote_worker.py`, `follow.py`, `cli.py`, `templates/worker.toml`, `examples/worker.toml` 과 새 파일 `pair_state.py` 를
  고친다(`docs/m5tc-workplan.md:996`). 등록 증명은 짝지은 토큰만 요구한다(`:539`). 그 PR3 의 owns 와 shared 에는
  `tests/golden/worker/` 가 없다. 그 명세를 쓸 때는 없던 폴더다. 순서와 이식 범위는 Q2 가 묻는다(결정 379).

### 1.4 이 명세가 하지 않는 것

- 서버(`server.py` · `remote_workers.py` · `store.py` 등), CLI(`rcm run` · `wait` · `top` · `setup` · `doctor`), 웹, DB
  스키마를 Rust 로 바꾸지 않는다. DB 버전은 26 그대로다(`src/remote_ci_monitor/store.py:88`).
- 기본 추천대로면 서버 로컬 레인의 러너와 감독자를 바꾸지 않는다. 다만 이것은 결정 380 ⛔ 이고 Q17 로 묻는 중이다(§1.2 의 5).
- 워커 프로토콜 2 를 바꾸지 않는다. 더하는 키는 Q9 가 정하는 것 하나뿐이다(결정 385).
- 기본 구현을 Rust 로 바꾸지 않고(Q12) Python 구현을 지우지 않는다.
- Windows 네이티브는 다루지 않는다(결정 381).
- 외부 변이 도구 `cargo-mutants` 를 들이지 않는다(결정 372).
- TLS(`https://` 서버)는 Q11 의 답을 따른다. 기본은 이번에 하지 않는 것이다.

## 2. 범위: 누가 무엇을 하나

| 누구 | 무엇 |
|---|---|
| rcm(이 레포, PR1~PR9) | 골든 · 벡터 · 선택기, `rust/` 작업 공간과 `rcmw`, Python 앞단의 `implementation` 선택과 설정 넘기기, 서버의 바이너리 서빙(Q3 이 (a) 일 때), `--check` · doctor · 가드 · 문서, 실기 하네스와 보고서 |
| 오너 | Q1~Q13 · Q16~Q18 답. 맥미니(rcm-ci 를 도는 `mac2` 워커의 사용자)에 Rust 툴체인 설치와 운영 프리셋 `rcm-ci` 의 env · `requires` 고치기(Q7 — 운영 설정은 오너 몫, `docs/m5r-workplan.md:573`). Linux 증거를 낼 머신(Q8). 운영 반영과 어느 워커를 Rust 로 돌릴지 |
| 메인 세션 | 운영 사실 읽기(Q14: 워커 머신의 OS · arch, `https://` · `auto` 를 쓰는 워커가 있나 — `test-server.md` 의 읽기 전용 길로), 결정 페이지, 커밋 · PR · 머지. M5t-c PR3 이 머지될 때 §4.10 의 세 경우 표대로 M5t-c 나 M5u 의 §13 에 갈 문장을 그 PR 에서 적는다 |
| downstream(dolomood-renew 의 워커) | 바꿀 것 없음. 기본 `implementation = "python"` 이라 그대로 돈다. Rust 를 켜는 것은 그 머신 주인의 선택 |

메인 세션의 운영 읽기(Q14)는 끝났다. 결과는 §3.5 에 있다.

## 3. 실측과 코드 제약

모든 `file:line` 은 핀 d07b253 의 것이다. 1판 핀 9c6de32 와 비교하면(`git diff --stat 9c6de32 d07b253`) 바뀐 파일은
`CHANGELOG.md`, `README.md`, `README.ko.md`, `docs/m5tb-workplan.md`, `src/remote_ci_monitor/__init__.py`,
`tests/test_acceptance_m5tb_live.py`, `tests/test_docs_m5tb.py` 다. `docs/m5tb-workplan.md` 에서는 §6 표의 상태 열 10줄이
바뀌었고 줄 수는 같다. `__init__.py` 에서는 버전 한 줄이 바뀌었다. 그래서 F1~F49 의 줄 번호는 이 핀에서도 맞다. 값이 바뀐
것은 F14 하나다.

3판에서 다시 확인했다. HEAD 87e4817 의 `src/` · `tests/` 는 핀과 같다. `git diff --stat d07b253 87e4817 -- src tests` 의
출력이 비어 있다. `src/remote_ci_monitor/__init__.py:7` 은 `__version__ = "0.12.0"` 이고 `store.py:88` 은
`DB_VERSION = 26` 이라서 F14 가 그대로 맞다. `git diff --stat 9c6de32 d07b253` 은 위 7개 파일에서 29줄을 더하고 23줄을 뺐다.
그 가운데 `docs/m5tb-workplan.md` 는 10줄이 바뀌고 10줄이 빠져 줄 수가 같다. `__init__.py` 는 1줄이 바뀌고 1줄이 빠졌다. 변이 수는
`grep -c` 로 다시 셌다. `^    Mutant($` 는 728개다. `path="src/remote_ci_monitor/remote_worker.py"` 가 104개, `runner.py`
가 19개, `job_supervisor.py` 가 5개라서 합은 128개이고 F33 그대로다. F1 의 `wc -l` 합 7,253 도 그대로다.

scenario-author 의 표는 37e7b98 스냅숏으로 쓰였다. 그 뒤 9c6de32 까지의 커밋은 `src/` · `tests/test_e2e_worker.py` ·
`docs/operating.md` · `PLAN.md` · `tools/guard_production.py` 를 바꾸지 않았다. `git diff --stat 37e7b98 9c6de32 -- <그
경로들>` 의 출력이 비어 있다.

근거 칸의 「plan-lead 가 잰 맥」은 plan-lead 가 측정을 돌린 맥이다. 다른 절은 이 표의 F 번호로 사실을 가리킨다.

### 3.1 옮길 코드의 크기와 경계

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F1 | 워커 경로 Python 줄 수(`wc -l`): `remote_worker.py` 3190 · `runner.py` 720 · `job_supervisor.py` 208 · `materialize.py` 256 · `gitops.py` 517 · `collect.py` 264 · `hostsample.py` 582 · `core/hostparse.py` 447 · `core/capability.py` 357 · `core/artifacts.py` 362 · `core/manifest.py` 181 · `core/inputs.py` 121 · `core/jobenv.py` 48 = 7,253줄. 여기에 `client.py`(1438) 의 `WorkerClient` 부분, `follow.py`(1524) 의 워커 부분, `core/outcome.py`(620) 의 요약 부분, `core/mdns.py`(469)+`mdns.py`(399) 의 질의 부분이 더해진다 | `wc -l` | 한 PR 에 다 못 담는다 → PR3 · PR4 · PR5 로 층을 나눈다(결정 361) |
| F2 | `remote_worker.py` 의 import 닫힘(모듈 단위 정적 분석, `ast` 로 `from remote_ci_monitor…` 를 따라감)은 31개 모듈 25,962줄 — `worker.format_limit` 하나가 `worker.py` → `store.py` 까지 끌고 온다(`remote_worker.py:71`) | 스크래치 스크립트(ast) | Rust 는 닫힘 전체가 아니라 실제로 부르는 함수만 옮긴다. `format_limit` 같은 문구 함수는 벡터로 묶는다 |
| F3 | 러너는 로컬 워커와 원격 워커가 **같은 함수** `run_job` 을 쓴다 | `runner.py:3-5`, `runner.py:394` | 러너만 따로 Rust 로 바꾸면 Python 워커와 프로세스 사이 경계(IPC)를 새로 만들어야 한다 → 러너 단독 교체는 버린다(결정 361). 서버 로컬 레인은 Python 러너 유지(결정 380) |
| F4 | 감독자는 별도 프로세스로 뜬다: `[sys.executable, "-I", SUPERVISOR_PATH, "--parent", <pid>, "--grace", <n>, "--", *argv]`. 표식 잡마다 인터프리터 하나 | `runner.py:349-373` | 감독자는 argv 계약이 닫혀 있어 가장 작은 첫 Rust 조각이다(PR2). Rust 워커는 Python 감독자를 부르지 않고 같은 바이너리의 `rcmw supervise` 를 부른다 — 파이썬 없이 도는 길의 전제 |
| F5 | 감독자 파일은 표준 라이브러리만 import 한다(시험이 import 목록을 잠근다). 미시작 종료 125, 표지 `[rcm] supervisor: not started: `, 부모 확인 0.25초 | `job_supervisor.py:9-10` · `:28-32` | Rust 감독자도 같은 종료 코드 · 같은 표지 바이트 · 같은 주기(RW6). 러너가 표지를 읽는 쪽(`runner.py:376-391`)은 Python 에도 남는다 |
| F6 | 감독자 기동 비용(잰 값): `/usr/bin/time -l python3.11 -I job_supervisor.py --parent <셸 pid> --grace 1 -- /usr/bin/true` 5회 — real 0.01~0.02초, 최대 RSS 14.5~14.9 MB. 부모 pid 가 달라 미시작(125)으로 끝난 길이라 기동+거절 비용이다 | plan-lead 가 잰 맥(arm64, macOS 27.2.1, Python 3.11.15) | 감독자 단독의 이점은 작다(잡마다 ~15 MB · ~20 ms). 감독자 PR 의 값은 이점보다 Rust 사슬(빌드 · CI · 변이 · 선택기)의 첫 검증이다 — §4.2 |
| F7 | 서버 쪽 `/worker/*` 라우트: `register` · `claim` · `heartbeat` · `capabilities`, 잡별 `tree` · `phase` · `log` · `finish` · `artifacts`, 그리고 `GET /worker/spec` | `server.py:276-277` · `:5996-6001` | Rust 가 말할 표면 전체. 바뀌지 않는다(결정 385) |
| F8 | 서버는 HTTP/1.1 keep-alive 를 받는다(`protocol_version = "HTTP/1.1"`) | `server.py:5741` | Python 워커는 `urllib.request` 로 요청마다 연결 하나(urllib 은 `Connection: close` 를 단다 — CPython 3.11 `urllib/request.py:1333`). Rust 도 요청마다 새 연결 + `Connection: close`(결정 387). 풀 연결 재사용의 거짓 실패(RW12 a)가 생기지 않는다 |
| F9 | `WorkerClient` 는 `urllib.request.urlopen` 을 쓴다 — `https://` 주소도 받는다. 설정 문서는 리버스 프록시 이름을 쓰는 경우를 말한다 | `client.py:208` · `:604` · `docs/configuration.md:1282` | 표준 라이브러리만의 Rust 는 TLS 가 없다. TLS 크레이트를 들이거나 `https://` 를 거절해야 한다 → Q11 |
| F10 | 세션 id 는 `secrets.token_hex(16)`(32자 소문자 hex). User-Agent `rcm/<__version__>`. 요청 기한 15초 | `client.py:105` · `:198` · `:61` | 와이어 골든의 허용 차이(값만 다름)로 둔다(결정 360) |
| F11 | 등록 본문 키: `pool` `lanes` `host_name` `version` `worker_protocol: 2` `previous_sessions` `unverified_sessions` (+ `host`, + 세션) | `client.py:134-146` | Rust 가 같은 키 · 같은 순서를 낸다(RW10) |
| F12 | 서버는 등록 `version` 이 자기 `__version__` 과 **문자열로 같을 때만** 받는다(아니면 409 + `server_version`). 이 검사는 세션 · 프로토콜 검사보다 앞 | `remote_workers.py:674-682` | Rust 바이너리의 버전은 `__init__.__version__` 과 늘 같아야 한다 → PR2 의 버전 잠금 시험(RW20 b). 서버를 올렸는데 그 버전의 `rcmw` 가 없으면 그 워커는 등록하지 못한다 → Q4 |
| F13 | M5t-c `pair_proof` 검사 자리가 버전 · 프로토콜 검사 뒤에 비어 있다 | `remote_workers.py:686-687` | M5t-c PR3 이 워커 쪽에 확인 고리를 더한다 → Q2(결정 379) |
| F14 | `__version__ = "0.12.0"`, DB_VERSION 26 | `__init__.py:7` · `store.py:88` | v0.12.0 은 출시 · 운영 반영이 끝났다(P1-2 입력 파일). 버전 잠금 시험(RW20 b)은 이 값을 읽는다 |

### 3.2 프로세스 · 디스크 · 시간의 규칙

Rust 가 똑같이 해야 하는 것들이다.

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F15 | 잡 env 는 `env_passthrough` → `preset.env` → `RCM_*` 아홉 → `RCM_INPUT_*` 순으로 만든 새 dict 다(워커 환경을 상속하지 않는다) | `runner.py:311-332` | Rust `Command` 는 기본이 상속이다 → `env_clear()` 뒤 같은 순서(RW7). 벡터 `build_env` |
| F16 | 러너 규칙: 셸 없이 argv, `cwd = workspace`, `start_new_session=True`, 1초 폴링, SIGTERM → grace → SIGKILL 을 그룹에, `MAX_LINE_BYTES` 64 KiB 에서 자름, `READ_CHUNK` 65536 | `runner.py:7-14` · `:38-40` · `:335-346` | `start_new_session` 은 `setsid` 다. Rust `CommandExt::process_group(0)` 은 그룹만 만들고 세션은 안 만든다고 문서가 말한다 — **미측정**(툴체인 없음, F38), PR2 첫 빌드의 RW7 시험이 잰다. 그래서 `pre_exec` 에서 `setsid`(libc)를 부른다. RW7 이 pid = pgid = sid 를 잠근다 |
| F17 | Python `subprocess` 는 3번 이상 fd 를 닫는다(`close_fds=True` 기본). Rust std 는 자기가 연 fd 에 `O_CLOEXEC` 를 달지만 상속 가능한 fd(설정 넘기기 fd 등)는 닫지 않는다고 문서가 말한다 — **미측정**, PR3 의 RW7 시험이 잰다 | CPython 3.11 `subprocess.py:809` | 넘기기 fd 는 읽은 뒤 닫는다. RW7 이 잡 프로세스의 fd 3+ 0개를 잠근다 |
| F18 | 프로세스 신원: Linux `/proc/<pid>/stat` 마지막 `)` 뒤 20번째 칸 → `linux:<n>`, 그 밖 `LC_ALL=C TZ=UTC ps -o lstart=` → `ps:<값>`(정규식 `LSTART_RE`). 못 읽으면 None(모름) | `remote_worker.py:1075-1132` | 벡터 `ident`(RW1 · RW31). 「모름」을 「확인됨」으로 읽는 길 0 |
| F19 | 세션 폴더 락은 `fcntl.flock(LOCK_EX \| LOCK_NB)` — POSIX `fcntl` 레코드 락이 아니다 | `remote_worker.py:1222-1235` | Rust 는 `libc::flock` 을 써야 Python 과 서로 보인다(RW2). 표준 라이브러리 버전의 락 API 에 기대지 않고 `libc::flock` 을 직접 부른다(결정 368 의 `libc`) |
| F20 | `owner.json` 은 `sha256("<server 끝 / 뗀 값>\n<server_name>")`(auto 면 `auto`)와 `token_name` | `remote_worker.py:1214-1219` | 벡터 `owner_sha`. Rust 는 `auto` 를 받지 않으므로(결정 379 기본) 자기 폴더의 해시는 늘 명시 URL 로 계산한다. Python `auto` 워커가 남긴 폴더(해시 `auto`)는 남의 폴더로 보고 잠그지도 지우지도 않는다 — Python 이 설정을 auto 에서 명시 URL 로 바꿨을 때와 같다 |
| F21 | 시간 상수: 감시 틱 1초 · heartbeat 기한 15초 · 매달린 도우미 상한 4 · 자기 멈춤 여유 10초 · `FINISH_HOLD_SECONDS` 300 · `RETRANSMIT_SECONDS` 86400 · `DEAD_SESSION_WAIT_SECONDS` 300 · 백오프 1→30초 · `OWED_FINISH_WAIT_SECONDS` = 30 + 2×15 · `STOP_REPORT_SECONDS` 7 · 드레인 큐 대기 60초 | `remote_worker.py:99-119` · `:169-189` | Rust 상수 표를 같은 이름으로 두고 시험이 두 값을 대조한다(벡터 `constants`) |
| F22 | 로그 조각: `LOG_BATCH_BYTES` 256 KiB, 줄 끝 없으면 `LOG_PIECE_MAX_BYTES` 4 MiB | `remote_worker.py:77-80` | 벡터 `chunk`. e2e RL18 은 이 상수를 200 바이트로 줄이는 감싸개를 쓴다(F31) |
| F23 | 워커 설정은 최상위 스칼라 키 + `[host]` + `[[repos]]`. 모르는 키는 `ConfigError`. 토큰은 플래그로 못 준다(「the worker token cannot be passed as a flag — use RCM_WORKER_TOKEN」) | `config.py:516-534` · `:1748` · `:1761` · `:1982-2041` · `:2017` | 형태 A(Q5)에서는 Python 이 지금 그대로 풀고 검사한다 — 오류 문장 · 종료 2 가 안 바뀐다. 새 키 `implementation` · `rust_binary` 는 `_WORKER_KEYS` 에 더하고(PR4) `WorkerConfig` 필드가 된다. 둘은 앞단만 쓰는 키라 `FRONT_KEYS` 에 든다(넘기지도 거절하지도 않음, 결정 390 · F62) |
| F24 | `rcm worker` 는 따라가기(①②) 뒤 `RemoteWorker.run()`, 드레인 뒤에는 설치본 부트로더로 같은 PID 에서 `execve`(`follow.restart_via_installed`). 첫 신호는 멈춤, 둘째는 `os._exit(1)` | `cli.py:4933-5009` · `:5024-5034` · `follow.py:1-15` | 형태 A 의 근거: 앞단이 이미 같은 PID exec 를 한다. Rust 도 드레인 뒤 설치본 `rcm worker` 로 exec 해 앞단이 버전을 따라가게 한다(§4.4) |
| F25 | 따라가기의 신뢰 모델: 서버가 준 코드를 실행한다. sha256 은 전송 깨짐용이지 위조 방지가 아니다. 리다이렉트를 따르지 않는다. 캐시 폴더 권한을 본다 | `follow.py:12-15` | 바이너리 따라가기(PR6)도 같은 세 규칙(RW22). 「서명 검증」을 약속하지 않는다 |
| F26 | 연속 연결 실패 5번마다 서버를 다시 찾는다(콜백은 `cli.py` 가 mDNS 로). `server = "auto"` 일 때만 | `remote_worker.py:83-84` · `cli.py:4813-4823` | 결정 379 기본(Q2 a)이면 Rust 는 `auto` 를 받지 않아 다시 찾기가 필요 없다 — Rust 에 mDNS 질의를 넣지 않는다(1판 PR5 의 `mdns.rs` 를 뺀다). Q2 (b) 면 M5t-c 의 다시 찾기 검증(결정 310)과 함께 옮긴다 |
| F27 | 호스트 사실 키 아홉(`os` … `python`). 서버는 `python` 이 문자열이 아니면 None 으로 받는다 | `remote_worker.py:86-97` · `capabilities.py:126-158` · `hostsample.py:208` | 형태 A 는 앞단이 `platform.python_version()` 을 넘긴다. 독립 실행은 `null`(결정 375). 화면은 값이 있을 때만 「Python X」를 그린다(`core/doctor.py:551`) |

### 3.3 시험 · 변이 · CI 의 사실

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F28 | e2e 워커는 `RCM = [sys.executable, "-m", "remote_ci_monitor.cli"]` 로 뜬다. 띄우는 자리: `tests/test_e2e_worker.py` 의 `WorkerProc`(:429) · `SessionProc`(:255), `tests/test_e2e_m5n.py:169`(같은 `RCM`), `tests/test_e2e_worker_m5q.py:118-131`(자기 argv), `tests/test_e2e_worker_outbox_m5tb.py:180-190`(감싸개) | 해당 줄 | 하위 프로세스 env 는 `clean_env` 가 `os.environ` 을 복사해 만든다(`tests/test_e2e_worker.py:195-212`). conftest 가 `os.environ` 에 제품 env(`RCM_WORKER_IMPLEMENTATION` · `RCM_WORKER_RUST_BINARY`)를 넣으면 시험 함수를 안 고치고 Rust 로 돈다(결정 362). 「진짜 Rust」 확인은 `WorkerProc.start` 의 `wait_registered` 뒤와 `WorkerProc.kill` · `M5nWorker.kill` 직전에 둔다(§4.1, F53) |
| F29 | `WorkerProc` 를 쓰는 파일은 `test_e2e_worker.py` · `test_e2e_worker_grace_m5tb.py` 둘(스크립트로 셈). 시험 함수 수: e2e_worker 7 · e2e_worker_m5q 6 · e2e_worker_grace 12 · e2e_worker_outbox 5 · e2e_m5n 2 | 스크래치 스크립트(정규식 `^\s*def test_`) | Rust 판에서 도는 기존 e2e 는 25개(7 + 12 + 4 + 2)다. 설치본 wheel 따라가기를 쓰는 7개(m5q 6 · outbox RL12-b)는 `RUST_REPLACED` 로 빼고 PR6 의 Rust 시험이 RW21 의 길로 대신한다 — §7.3 |
| F30 | `RemoteWorker(` 를 직접 만드는 시험 파일 14개(`grep -l "RemoteWorker(" tests/*.py`), 이름이 나오는 파일 16개. 시험 함수 수: worker_session 80 · worker_selfstop 57 · worker_outbox 92 · worker_log_offset 23 · job_supervisor 31 · runner 17 | 위 grep · 스크립트 | scenario-author 의 「18개 파일」은 14개로 고친다. 이 시험들은 바이너리로 못 돈다 → RL 대응표(§7.4)로 cargo 시험에 1:1 |
| F31 | RL18 e2e 는 `remote_worker.LOG_BATCH_BYTES` 를 200 으로 바꾸는 `runpy` 감싸개로 워커를 띄운다 | `tests/test_e2e_worker_outbox_m5tb.py:180-190` | Rust 에도 같은 조절이 필요하다 → `test-knobs` 기능으로 빌드한 시험용 바이너리만 읽는 env(결정 373). 릴리스 바이너리에는 없다 |
| F32 | 시험 하나의 예산: setup · call · teardown 합이 직렬 20초 · xdist 워커 2개 이상이면 60초, 넘으면 `@pytest.mark.slow` 가 없을 때 세션 실패 | `tests/conftest.py:15-16` · `:61` · `:82` | cargo 빌드를 픽스처에서 하면 첫 시험의 setup 에 들어간다 → 빌드 시험은 `slow` 표식, 나머지는 빌드된 바이너리를 쓴다 |
| F33 | `scripts/mutcheck.py` 의 변이 728개(MUTANTS 를 불러 셈). `path` 가 `remote_worker.py` 104 · `runner.py` 19 · `job_supervisor.py` 5 = 128. 주석 표지 `mutcheck:` 는 66 · 9 · 5 = 80줄(`grep -c`) — 표지 없이 코드 글로 찾는 변이가 있어 변이 수는 표지 수보다 많다 | `scripts/mutcheck.py:274-288` + 셈 | scenario-author 의 「80줄」은 「128개 변이」로 고친다(RW27). Rust 대응 변이 목록은 §7.5 |
| F34 | `Mutant.runner` 는 `"pytest"` \| `"node"` 둘. 복사본에 `src/` `tests/` `tools/` `pyproject.toml` 을 복사한다 | `scripts/mutcheck.py:2-5` · `:274-283` | `runner="cargo"` 와 `rust/` 복사를 더한다(결정 372) |
| F35 | `scenario_trace.py` 는 pytest 가 모으는 시험만 센다(`test_*.py`) | `tools/scenario_trace.py:22-25` | cargo 시험은 추적에 안 잡힌다 → RW 행마다 cargo 시험 묶음을 돌리고 결과를 단언하는 pytest 래퍼(`tests/test_rust_*_m5u.py`)에 태그를 단다 |
| F36 | `rcm/ci` 의 검사 단계는 레포 밖 운영 스크립트 `~/.config/rcm/scripts/rcm-ci.sh` 가 정한다(프리셋 `rcm-ci` 는 운영 서버 설정, `mac2` 풀) | `docs/m5r-workplan.md:1262-1264` · `.rcm/ci.toml` | cargo 단계를 그 스크립트에 넣는 것은 운영 파일 수정(오너). 대신 pytest 안에서 cargo 를 돌리면 스크립트는 그대로이고 프리셋 env 만 바뀐다(결정 369, Q7) |
| F37 | 한 판 시간: `rcm-ci` 약 16분(P1 입력 파일, 운영 수치는 메인 세션 제공) | P1 입력 파일 | cargo 빌드 · 시험 · Rust e2e 매트릭스 시간: **—**(툴체인이 없어 못 잼, F38). PR2 에서 잰다 |
| F38 | plan-lead 가 잰 맥에 Rust 툴체인 없음: `cargo` · `rustc` · `rustup` 「command not found」, `~/.cargo/bin` 없음, `/opt/homebrew/bin` 에 cargo 없음. 컨테이너 · VM 도구 없음: `docker` · `podman` · `colima` · `lima` · `limactl` · `orb` 「not found」, `/Applications` 에 Docker · OrbStack · UTM · Parallels · VMware 없음. 교차 링커 `zig` · `cross` 없음. 머신: arm64 Apple M4 10코어, macOS 27.2.1 | `cargo --version` 등, `which`, `ls /Applications`, `sw_vers`, `sysctl` | 설치는 오너 일(Q7). 이 맥에서 Linux 바이너리를 만들 길도, Linux 로 시험할 길도 지금은 없다(Q8) |
| F39 | M5t-b 실기 보고서: 「남은 것은 Linux 머신에서의 `/proc` 신원 확인과 그 머신의 `ps` 꼴 확인이다. 이 머신에는 Linux 가 없다」, RL15-proc 은 SKIP | `docs/acceptance/reports/2026-11-27-m5tb-acceptance.md:26-27` · `:164` | Linux 증거가 이미 한 번 빈 채로 머지됐다 → Q8 은 ⛔ |
| F40 | 패키징 시험: `dependencies == []`, wheel 이름이 `-py3-none-any.whl` | `tests/test_packaging.py:106` · `:186` | Rust 바이너리를 wheel 에 넣지 않는다. 결정 371 의 문장 「wheel 은 0 그대로, 워커 바이너리는 별도」 |
| F41 | 실기 하네스 `scripts/acceptance/m5tb_live.sh`(3580줄)는 `RCM="$M5TB_ROOT/.venv/bin/rcm"` 으로 `rcm worker` 를 띄운다 | `scripts/acceptance/m5tb_live.sh:34` | 같은 하네스를 `implementation = "rust"` 워커로 다시 돌릴 수 있다(PR8). 제품 env 를 하네스가 넘기면 된다 |

### 3.4 운영 표면의 사실

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F42 | 서비스 파일 예시는 `rcm worker --config …` 를 띄운다(launchd `ProgramArguments`, systemd `ExecStart`) | `examples/launchd/com.remote-ci-monitor.worker.plist:39-45` · `examples/systemd/rcm-worker.service:31` | 형태 A 는 서비스 파일을 안 바꾼다 |
| F43 | doctor 는 실행 파일이 `rcm` 이거나 `python -m remote_ci_monitor.cli` 일 때만 인자를 푼다 | `doctor.py:453-467` | 독립 `rcmw` 서비스(Q5 b · c)면 doctor 가 그 서비스를 못 알아본다 → PR9 가 고친다 |
| F44 | 운영 가드: 운영 data_dir 을 여는 하위 명령 `serve` · `worker` · `token`(`MIGRATING`), 서비스 단어 `com.remote-ci-monitor` · `rcm-server` · `rcm-worker`(부분 문자열로 셈) | `tools/guard_production.py:108` · `:116` · `:847-848` | 바이너리 이름을 `rcm-worker` 로 하면 서비스 단어와 겹친다 → 이름은 `rcmw` 추천(Q13). 가드는 `rcmw` 를 운영 설정 · data_dir 과 함께 부르는 명령도 거절해야 한다(PR7, RW24) |
| F45 | macOS Local Network: 권한 없는 프로세스는 LAN 으로 unicast 도 `EHOSTUNREACH`, launchd 서비스는 기본 거부, 권한 항목은 「서비스 파일에 적힌 인터프리터 또는 바이너리」 | `docs/operating.md:80-87` | 형태 A 에서 exec 뒤 권한이 누구 이름으로 매겨지는지: **—**(못 잼 — 시험 launchd 라벨로 PR8 의 RW29 에서 잰다). 형태 C 는 `rcmw` 항목이 새로 생긴다 |
| F46 | TCC: launchd 서비스는 `~/Documents` · `Desktop` · `Downloads` 를 못 읽는다 | `docs/operating.md:63-66` | 바이너리 캐시 · data_dir 자리는 `~/.local/share` 아래 |

### 3.5 운영 서버에서 읽은 사실 (Q14)

메인 세션이 운영 서버를 읽기 전용으로 보고 잰 값이다.

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F47 | 운영 서버에 붙은 머신은 둘이다. 서버 자신(로컬 레인 2)과 원격 워커 `mac2`(풀 `mac2`, 레인 1)다. 둘은 같은 물리 Mac 에 있다(host key 가 같다) | 2026-11-27, 메인 세션이 운영 서버에 읽기 전용으로 `rcm machines` · `rcm machines --machine mac2` 와 워커 설정의 server 줄 꼴을 봄 | 운영에서 `implementation = "rust"` 를 줄 수 있는 원격 워커는 지금 `mac2` 하나다. 서버의 로컬 레인 2 는 Python 그대로다(결정 380). Q4 · Q7 · Q12 가 말하는 `mac2` 가 이 워커다 |
| F48 | 그 호스트는 macOS 27.2.1, arm64, Python 3.11.15(Homebrew)다 | 같음 | 지금 운영에서 Rust 바이너리가 필요한 target 은 macOS arm64 하나다(Q8) |
| F49 | `mac2` 워커 설정의 `server` 는 명시한 `http://` URL 이다. `https://` 도 아니고 `auto` 도 아니다 | 같음 | Q11 의 기본 (a) 가 붙인 조건, 곧 운영에 `https://` 워커가 없다는 조건이 지금 운영에서 성립한다. `auto` 를 쓰는 워커도 없다. Q11 의 답은 여전히 오너가 정한다 |

이 사실이 플랫폼 질문(Q8)에 주는 것은 이렇다. 오늘 운영에서 의미가 있는 플랫폼은 macOS arm64 하나다. 그래서 Q8 의 기본
(d) 로 macOS arm64 용 바이너리만 내도, 오늘 운영 서버에 붙은 원격 워커는 Rust 를 고를 수 있다. 운영 서버에 Linux 머신이
붙어 있지 않으므로 (a) 에 쓸 Linux 머신도 운영 목록에는 없다.

다만 Q8 은 운영 서버에 붙은 머신만 보는 질문이 아니다. PLAN 의 이식성 줄은 macOS 와 Linux 빌드 머신을 둘 다 지원한다고
적었다(`PLAN.md:34`). 운영 서버 밖에서 Rust 를 켜려는 머신의 플랫폼은 잰 값이 없다(—, §3.6). 그래서 이 명세는 F47~F49 로
Q8 을 정하지 않고 결정 페이지에 둔다.

F47~F49 는 메인 세션이 2026-11-27 에 읽었다. v0.12.0 을 반영한 뒤에도 값이 같다고 메인 세션이 보증했다(P1-2 입력 파일).

### 3.6 재지 못한 값 (—)

| 값 | 무엇이 잴 것인가 |
|---|---|
| `cargo build --release --locked` 시간(차가운 · 데운 캐시) · `rcmw` 크기 — 1판 부록 Q15 를 질문이 아니라 여기로 옮김 | PR2, 맥미니에 툴체인 설치 뒤 rcm-ci 잡 로그 |
| Rust e2e 매트릭스가 `rcm-ci` 에 더하는 시간 | PR4, rcm-ci 잡 로그(구현별 통과 수와 소요) |
| Rust 변이 한 개의 비용(복사본 증분 빌드 + 시험) | PR2, `mutcheck.py --only <rs 변이>` |
| Python 워커 프로세스 RSS · CPU, Rust 워커의 같은 값 | PR4 실기(같은 잡 묶음) |
| exec 뒤 Local Network 권한의 주체 | PR8, RW29 |
| 운영 워커들의 OS · arch, `https://` · `auto` 사용 여부 | 메인 세션이 2026-11-27 에 읽었다(§3.5, F47~F49) |
| 운영 서버 밖에서 Rust 를 켜려는 머신의 OS · arch | 오너가 Q8 에 답할 때 |
| Rust `process_group` · `pre_exec(setsid)` · fd 상속 · 자식의 SIGPIPE 처분(F16 · F17 · §4.7 의 문서상 동작) | PR2 · PR3, RW7 의 cargo 시험(첫 빌드) |
| 크레이트 전이 의존성 수(Q6 (a) · Q16 (a)) | PR2 · PR9 의 `Cargo.lock` |
| GitHub `macos-latest` 러너의 arch(RW33 의 스모크가 도는 곳) | PR6 뒤 첫 태그 빌드 로그의 `uname -m` |
| 태그 빌드와 rcm-ci 빌드가 같은 바이트인가(재현 빌드) | 재지 않는다 — RW33 이 「스모크한 파일 = 올린 파일」로 대신한다 |
| `test-knobs` 빌드 두 벌(`RCM_TEST_BUILD_VERSION`)이 rcm-ci 에 더하는 시간 | PR6, rcm-ci 잡 로그 |
| `Cargo.toml` 의 `version` 만 올리고 `rust/Cargo.lock` 의 `rcmw` 항목을 그대로 두면 `cargo build --locked` 가 실패하는가, `rcmw` 항목이 lock 에 `version` 줄로 적히는가(cargo 문서상 그렇다 — 미측정) | PR2 첫 빌드, `tests/test_rcmw_version_lock_m5u.py` 를 쓰면서 |

### 3.7 1라운드에서 더한 사실

1라운드 지적을 닫으려고 핀 d07b253 에서 새로 읽은 사실이다. F50 부터 이어 매긴다.

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F50 | 산출물 처분: 묶음이 `uploaded` 면 finish 의 처분에 `bundle_sha256` 을 싣고, 아니면 처분을 `failed` + `upload_failed`(`bundle_sha256` null)로 바꿔 보낸다. 수집 · 업로드 예외도 `failed` + `upload_failed`. 수집기는 `ready` · `empty` · `skipped` · `dropped` 를 낸다 | `remote_worker.py:831-846` · `:2966-2968` · `collect.py:139-160` | RW18 의 「못 올린 묶음은 `ready` 로 위장하지 않는다」의 Python 쪽 정본. 벡터에 처분 표를 둔다(PR5) |
| F51 | `tests/test_job_supervisor_m5tb.py`(728줄, 시험 31개)는 `job_supervisor` · `runner` 를 프로세스 안에서 import 한다(`:25-30`). 감독자 argv 계약 시험은 `run_sup`(`[sys.executable, "-I", SUP, …]`, `:119-128`)과 `spawn_parent`(`:221-238`)로 띄우는 7개(`:130` · `:145` · `:156` · `:164` · `:174` · `:242` · `:264`), 훅 시험 2개는 감독자 파일을 `importlib` 로 읽어 `main(before_check=…)` · `main(before_popen=…)` 을 부르는 래퍼(`:179-201`, 시험 `:204` · `:212`), 모듈 시험 2개(`:101` import 목록, `:113` 상수 대조), 나머지는 `run_job` 을 프로세스 안에서 부르는 러너 시험이고 `runner.SUPERVISOR_PATH` · `runner.supervised_argv` 를 monkeypatch 한다(`:494` · `:617` · `:642`). 러너는 감독자를 `[sys.executable, "-I", SUPERVISOR_PATH, …]` 로만 띄운다 | 그 파일 · `runner.py:357-373` | 「띄우는 줄만 바꿔 두 구현으로」는 안 된다. 그 파일은 0줄 바꾼다. 새 `tests/test_rust_supervisor_m5u.py` 가 argv 계약 7개를 같은 단언으로 `rcmw supervise` 에 돌리고, 훅 2개는 cargo `#[cfg(test)]` 시험, 러너 쪽은 Rust 러너(PR3)의 cargo 시험이 맡는다. `runner.py` 는 어느 PR 도 고치지 않는다(Python 러너는 Python 감독자를 그대로 부르고, Rust 러너는 같은 바이너리의 `rcmw supervise` 를 부른다) |
| F52 | 호스트 사실 키는 워커 `HOST_KEYS` 와 서버 `HOST_FACT_KEYS` 가 같은 아홉이고 시험이 둘을 잠근다. 서버 등록은 아는 키만 읽고 모르는 최상위 키는 버린다. 등록마다 `remember_host(…, replace=True)`. 호스트 사실은 토큰 있는 머신 문서에만 실린다. 토큰 없는 머신 문서의 키 집합은 `MACHINE_KEYS` 로 잠겨 있다 | `remote_worker.py:86-96` · `capabilities.py:127-137` · `tests/test_doctor_cli.py:2412-2417` · `remote_workers.py:657-687` · `:703` · `:755` · `capabilities.py:446-454` · `:889-891` · `tests/test_server_m5n.py:457-478` | 구현 표식을 호스트 사실에 넣으면 Python 워커(`remote_worker.py`)와 잠금 시험을 고쳐야 하고, Python 이 안 보내면 null 이라 python 으로 읽을 수 없다(1라운드 HIGH). 그래서 표식은 Rust 만 보내는 등록 최상위 키로 두고 서버는 따로 기억한다(Q9 a, RW35). 옛 서버는 그 키를 버린다 |
| F53 | e2e 하네스: `WorkerProc.start` 는 `SessionProc` 로 띄운 뒤 `wait_registered` 로 등록을 기다린다(`tests/test_e2e_worker.py:462-470`). `GWorker` 는 `wait_registered` 만 덮어쓴다(`tests/test_e2e_worker_grace_m5tb.py:130-157`). `M5nWorker.start` 는 기다리지 않는다(`tests/test_e2e_m5n.py:186-193`, `kill` 은 `:198`). `SessionProc` 는 서버도 띄운다(`tests/test_e2e_worker.py:363`). `Stack.teardown` 은 `pgrep -f "worker --server http://127.0.0.1:<port>"` 로 남은 워커를 찾는다(`:296-300` · `:623-624`). RL18 은 `remote_worker.LOG_BATCH_BYTES` 를 바꾸는 `WRAPPER` 로 띄운다(`tests/test_e2e_worker_outbox_m5tb.py:182-190` · `:295`). RL12-b 는 m5q 의 `Stack` · `Supervisor`(설치본 부트로더)로 띄운다(같은 파일 `:34` · `:325-393`, `tests/test_e2e_worker_m5q.py:115-132`) | 그 줄들 | 「진짜 Rust」 확인은 `SessionProc` 가 아니라 등록 뒤(`WorkerProc.start` 의 `wait_registered` 다음)와 `kill` 직전에 둔다 — `SessionProc` 는 서버도 띄우고, Popen 직후는 아직 exec 전 앞단이다. exec argv 에 원래 argv 를 꼬리로 남겨야 teardown 의 `pgrep` 이 Rust 워커도 찾는다. `WRAPPER` 는 상수 한 줄을 고쳐야 한다. m5q 6개와 RL12-b 는 설치본 wheel 따라가기 시험이라 Rust 선택기로 못 돈다 → 대체 시험(PR6) |
| F54 | e2e 가 워커 로그 글을 단언한다: 5개 e2e 파일에서 `log_text()` · `in log` · `log.read_text()` 를 39곳 읽는다(`grep -c` 합). 예: `orphan #{jid}: stopped process group`(`tests/test_e2e_worker.py:834`), `#{x}: no answer from the server for`(`tests/test_e2e_worker_grace_m5tb.py:584-585`), `DRAINING = "draining: no new claims, restarting when running jobs finish"`(`tests/test_e2e_worker_m5q.py:274`), `job #{jid} still not reported — restarting anyway`(`tests/test_e2e_worker_outbox_m5tb.py:373`). `"Traceback" not in` 단언이 넷(`tests/test_e2e_worker_outbox_m5tb.py:169`, `tests/test_e2e_worker_m5q.py:226` · `:266` · `:397`) | 그 줄들 · `grep` | 워커 stderr 글이 동치 계약이다 → 벡터 `log_contract`(결정 392). `Traceback` 단언은 Rust 에서 공허하게 통과한다 → Rust 모드 하네스가 teardown 에서 패닉 줄(`panicked at`) 0 을 본다 |
| F55 | Python `WorkerClient` 는 `Client(server, token)` 을 기본 `follow_redirects=True` 로 만든다 — 리다이렉트를 따른다. 스냅샷 GET 은 맨 `urllib.request.urlopen` 이다. CPython 3.11 의 `HTTPRedirectHandler.redirect_request` 는 GET 의 301~308, POST 의 301~303(본문 없는 GET 으로)을 따르고 내용 헤더만 빼고 `Authorization` 을 새 주소로 옮긴다. M5t-c 결정 309 는 **짝지은** 머신의 hello · 토큰 요청만 3xx 를 끊고 옛 머신은 그대로 둔다 | `client.py:97-98` · `:582-604` · `:208`, CPython 3.11 `urllib/request.py`(`redirect_request`), `docs/m5tc-workplan.md:1350` | Rust 가 3xx 를 따르지 않는 것은 짝짓지 않은 머신에서 Python 과 다르다 → §1.2 의 2, Q11 에 묶는다. 따르지 않는 이유: 따르면 Bearer 토큰이 다른 호스트로 간다 |
| F56 | 다시 실행: 드레인 끝과 따라가기 409 의 재실행은 `follow.restart_via_installed` — env 에서 `BOOT_ENV` 다섯을 지우고 `RCM_WORKER_RESTARTED_AT` = 지금(epoch 초)을 넣어 `execve(sys.executable, [exe, "-P", "-c", RESTART, *argv], env)`. `RESTART` 는 `cli.main()` 을 부르는 세 줄 문자열이다. 기동 등록의 버전 409 는 `WorkerFollow`(값이 형식 검사를 지나고 지금 버전과 다를 때만)이고, 따라가지 않으면 그 메시지와 종료 2, 따라가면 `_worker_follow_409` | `follow.py:61` · `:149` · `:1350-1369`, `remote_worker.py:146-155` · `:2145-2158`, `cli.py:4957` · `:4992-4996` · `:5002-5009` · `:5024-5034` | Rust 는 인터프리터 경로도 `RESTART` 도 모른다 → 앞단이 넘기기 문서의 `restart` 에 argv · 지울 env 이름 · 시각 env 이름을 실어 주고 Rust 는 그대로 exec 한다(결정 393). 409 처리와 다시 실행은 등록과 같은 PR4 에 둔다 |
| F57 | 결정 번호: M5t-b 는 256~279 · 320~339 · 350~359 를 쓰고 351~359 를 비워 둔다. M5t-c 는 280~319 · 340~349. M5t 는 352~354 를 썼다. 360 이상은 d07b253 의 어디에도 없다 | `docs/m5tb-workplan.md:36` · `:2215` · `:2217`, `docs/m5tc-workplan.md:29`, `docs/m5t-workplan.md:1387-1425`, `git grep` | 이 명세는 360~393 을 쓴다(머리 인용) |
| F58 | 의존성 문장이 PLAN 밖에도 있다: `AGENTS.md:8` 「**Zero runtime dependencies.** Standard library only, server and client.」, `CONTRIBUTING.md:37` 「Runtime dependencies stay at zero. The standard library only, on the server and the client.」, `README.md:34` 「One package, zero runtime dependencies, Python 3.11+ standard library」, `PLAN.md:537`(pyproject 줄). `PLAN.md:605` 는 M5c mDNS 의 「표준 라이브러리만」이라 의존성 원칙 문장이 아니다 | 그 줄들 | Q10 (a) 면 `AGENTS.md:8` · `CONTRIBUTING.md:37` 을 PR2 에서 고친다(E6 · E7) — Rust 코드가 들어오는 PR 이 그 규칙을 어기지 않게. `README.md:34` 와 `PLAN.md:537` 은 패키지(wheel) 이야기라 그대로 참이다 → 안 고침 |
| F59 | 예시 잠금: `templates/worker.toml` 과 `examples/worker.toml` 은 바이트가 같아야 한다. 서버 키는 `examples/server.toml` 과 `templates/server.toml` 둘에 주석으로 둔다(시험이 둘을 같은 문장으로 찾음) | `tests/test_cli_init_worker.py:34-35` · `:102-103`, `tests/test_docs_m5p.py:128-135` | PR4 의 worker 키는 두 파일, PR6 의 `worker_binaries_dir` 도 두 파일(shared) |
| F60 | 릴리스 워크플로: 태그마다 ubuntu 에서 sdist + wheel 을 빌드하고 ubuntu · macos 두 OS 에서 설치 스모크를 돈 뒤 GitHub Release 를 만든다 | `.github/workflows/release.yml:1-4` · `:24-78` · `:79-90` | 바이너리 잡을 더하는 자리. 지금 rcm-ci(맥미니)가 시험한 빌드와 태그 빌드는 다른 머신이다 → 태그 빌드가 그 **같은 파일**로 스모크를 돈 뒤에만 자산을 올린다(결정 391, RW33) |
| F61 | M5t-c PR3 의 워커 쪽: `pair_secret` 설정 키, 요청마다 `X-RCM-Pair-Nonce`, 거절의 `pair_reject_mac` 검증(HMAC, 요청 본문 sha256 포함), 등록 `pair_proof`(짝지은 토큰만), 판정 표 J1~J14 · auto 표 A1~A10, 상태 파일 `pair_state.py`, 다시 찾기 검증(310). 짝짓지 않은 `auto` 워커는 거절(315). 코드 키 scrypt 는 `rcm setup --pair` · doctor 쪽(Python CLI) | `docs/m5tc-workplan.md:539` · `:541` · `:996` · `:1350`, 같은 문서 머리의 결정 315 | Rust 로 옮기면 PR 하나 이상의 일이다(Q2 b). 기본(Q2 a)은 옮기지 않고 그 설정을 기동 전에 거절한다 |
| F62 | 워커 설정은 `WorkerConfig` 데이터클래스 한 벌이다. 핀의 필드는 14개(`server` `token` `pool` `lanes` `name` `data_dir` `grace_seconds` `keep_workspace_on_failure` `git_fetch_timeout_seconds` `server_name` `accept_any` `host` `repos` `path`). `wants_discovery` 는 `server` 가 비었거나 `auto` 일 때 참이고 필드가 아니라 속성이다. `discovered_server` 는 필드가 아니라 `_worker_config` 가 발견 뒤에 다는 동적 속성이다(F64) | `config.py:516-539`, `cli.py:4769` | 넘기기 문서의 키를 필드 이름 집합으로 잠근다: 필드 = `HANDOFF_KEYS` ∪ `FRONT_KEYS` ∪ `RUST_REFUSED_KEYS`, 셋은 서로 겹치지 않음(결정 390). PR4 시점: `HANDOFF_KEYS` = 위 14개 가운데 `path` 를 뺀 13개, `FRONT_KEYS` = `implementation` · `rust_binary` · `path`, `RUST_REFUSED_KEYS` = 빈 집합. 필드가 늘면(M5t-c 의 `pair_secret`) 잠금이 빨개져 셋 중 하나에 넣게 된다. `fields()` 는 동적 속성을 못 보므로 발견 여부는 잠금이 아니라 관문의 자리(F64)로 막는다 |
| F63 | 시나리오 추적: 조건이 없거나 참 상수인 `skip*` · `xfail` 만 도구가 잡고, 식을 조건으로 둔 `skipif` 는 증명으로 센다. 본문 안의 런타임 `pytest.skip()` 은 도구가 못 보고 검증자가 `WEAK` 로 본다 | `tools/scenario_trace.py:240-261` · `.claude/skills/_shared/scenario-trace.md:196-198` | Rust 시험의 건너뛰기는 모듈 `pytestmark = pytest.mark.skipif(<식>)` 한 자리로만 하고, `RCM_REQUIRE_RUST=1` 이면 그 식 대신 수집 오류로 끝낸다(§7.2) |

### 3.8 2라운드에서 더한 사실

2라운드 지적을 닫으려고 핀 d07b253 에서 새로 읽은 사실이다. F64 부터 이어 매긴다.

| # | 사실 | 근거 | 설계에 주는 것 |
|---|---|---|---|
| F64 | `cmd_worker` 의 순서: `_worker_config(args)`(`:4944`) → 토큰 확인(`:4947`) → `--check` 면 확인 질문 · `_worker_check`(`:4953-4956`) → 따라가기 ① `_worker_follow_start`(`:4957-4963`) → `RemoteWorker`. `_worker_config` 안에서는 `load_worker_config`(`:4765`) 바로 뒤에 `if cfg.wants_discovery:` 갈래가 부모가 넘긴 `RCM_SERVER`(`_found_server_from_boot`) 나 mDNS(`_pick_worker_server` → `discover`)로 `cfg.server` 를 찾은 URL 로 덮고 `cfg.discovered_server = True` 를 단다(`:4766-4769`). 그 뒤 `cfg.wants_discovery` 는 거짓이다. 서버를 못 찾으면 `ConfigError("worker needs a server — …")` 로 종료 2. `worker` 는 `follow.NO_FOLLOW_COMMANDS` 에 들어 `_follow_first` 가 따라가지 않는다 — 워커의 따라가기 ① 은 `cmd_worker` 안이다 | `cli.py:4745-4770` · `:4773-4781` · `:4933-4963` · `:6147-6158`, `follow.py:83-85`, `config.py:537-539`, `cli.py:138`(`discover` import) | 관문이 `cmd_worker` 나 따라가기 ① 자리에서 `cfg.wants_discovery` 를 보면 auto 머신은 이미 발견된 URL 을 가져 관문을 지나간다(2라운드 HIGH). 그래서 관문은 `_worker_config` 안, `load_worker_config` 뒤 · 발견 갈래 앞(`:4765` 과 `:4766` 사이)에 한 줄로 둔다. `--check` 도 같은 관문을 지난다. env `RCM_WORKER_IMPLEMENTATION` 은 `load_worker_config` 가 이미 적용했으므로 그 자리에서 구현이 정해져 있다 |
| F65 | `tests/tcp_relay.py` 의 `TcpRelay` 는 바이트를 넘기고 고장(분단 · 거절 · 붙잡기 · 늦추기)만 만든다. 요청을 적거나 헤더 · 본문을 따로 꺼내는 기능은 없다. 요청 전체(머리 + `Content-Length` 만큼의 본문)를 읽는 `_read_request` 는 있으나 붙잡는 연결에만 쓴다 | `tests/tcp_relay.py:1-22` · `:89-121` · `:123-141` | 골든 녹화기는 새 부품이다. `TcpRelay` 에 덧붙이기만 하는 기록 옵션을 둔다(§4.1). PR1 shared 에 `tests/tcp_relay.py` |
| F66 | CONTRIBUTING 「Releasing」 1단계는 `__version__` 만 올린다. 태그 워크플로가 `__version__` 을 읽는다. Cargo 는 작업 공간 멤버 패키지의 이름과 버전을 `Cargo.lock` 에 적고, `--locked` 는 lock 을 고쳐야 하면 실패한다(cargo 문서 — 미측정, §3.6) | `CONTRIBUTING.md:84-90` | `rust/rcmw/Cargo.toml` 만 올리는 규칙은 `--locked` 빌드를 깬다. 버전 잠금이 세 값(`__version__` · `Cargo.toml` · `Cargo.lock` 의 `rcmw` 항목)을 cargo 없이 대조하고 1단계 문장이 셋을 함께 올리게 한다(§4.6) |
| F67 | M5t-c 의 PR 별 파일(그 명세 §5 표): PR1 owns `store.py` · `server.py`(`_send_error` · `/pair/*`), shared `docs/configuration.md`. PR2 owns `remote_workers.py`(등록에서 `check_register_proof`) · `capabilities.py`, shared `server.py`(`worker_spec_for`). PR3 owns `config.py` · `client.py` · `remote_worker.py` · `follow.py` · `doctor.py` · `core/doctor.py` · `templates/worker.toml` · `examples/worker.toml`, shared `cli.py`(`_worker_config` · `_pick_worker_server` · `_confirm_found_server` · `_worker_follow_start` · `_worker_check` 등). PR4 owns `cli.py`, shared `README.md` · `docs/configuration.md`. PR5 owns `docs/operating.md`, shared `README.md` · `docs/configuration.md` | `docs/m5tc-workplan.md:987-991` | M5u PR4 · PR6 · PR7 · PR9 가 이 파일들과 겹친다 → §4.10 의 겹침 표와 순서 규칙 |
| F68 | 기계 · 빌드에 따라 값이 다른 와이어 값: 등록 본문의 `version`(= `__version__`), `host_name`, `host` 의 아홉 값(`HOST_KEYS` 순서 — `os` `os_release` `os_like` `arch` `rosetta` `container` `root` `package_manager` `python`), heartbeat 의 `host_sample`, `POST /worker/capabilities` 본문의 잰 값. 헤더 `User-Agent` 는 `rcm/<__version__>` | `client.py:134-146` · `:159-161` · `:174-190` · `:198`, `remote_worker.py:86-96` · `:2090` | 골든이 이 값을 그대로 담으면 버전을 올릴 때마다, 다른 머신에서 빨개진다 → 값은 무시하고 타입과 키만 대조(§4.1). `version` 은 따로 「도는 빌드의 버전과 같다」를 단언 |
| F69 | `release.yml` 의 잡은 `build` → `smoke`(`needs: build`) → `github-release`(`needs: smoke`) · `pypi`(`needs: smoke`, `vars.PYPI_PUBLISH` 일 때). 워크플로 문면은 `tests/test_ci_release_workflow.py` 가 정규식으로 잠근다 | `.github/workflows/release.yml:25` · `:79-81` · `:104-106` · `:141-144`, `tests/test_ci_release_workflow.py:1-26` | 바이너리 스모크가 빨갈 때 wheel 릴리스를 멈추려면 `github-release` · `pypi` 의 `needs` 에 그 잡을 더한다(Q3 (i) 의 기본, §4.5) |
| F70 | `README.ko.md` 는 `README.md` 의 거울이고 같은 PR 에서 바뀐다. 두 파일을 같은 문장으로 찾는 시험이 있다 | `.claude/skills/docs/SKILL.md:22-23`, `tests/test_docs_m5p.py:118-125` | PR7 이 `README.md` 를 고치면 `README.ko.md` 도 shared |

## 4. 설계

### 4.1 동치의 정본과 그 장치 (결정 360 · 373)

정본은 핀 sha 의 Python 이 실제로 하는 일이다. 명세 문장과 코드가 다른 곳은 m5tb §13 이 이미 기록했다. 거기서는 코드가
이긴다. workplan 스킬 §1 은 머지 뒤의 이탈을 §13 에만 적게 한다(「After the merge the plan is frozen: a departure goes in
§13 only」). 그래서 코드와 §13 을 합친 것이 승인된 동작이다. scenario-author 의 질문 1 은 이것으로 닫았다(R1).

장치는 셋이다.

1. **와이어 골든**은 `tests/golden/worker/*.jsonl` 이다. 시나리오는 성공, 실패, 취소, 시간초과, 산출물, 분단, 서버
   재시작, 펜싱, 죽은 세션 큐, git_ref 다. 녹화기는 `tests/tcp_relay.py` 의 `TcpRelay` 에 새 키워드 `record_requests` 를
   더해 만든다. `TcpRelay(upstream_port, record_requests=True)` 로 켜고 기본값은 `False` 라서 기존 시험의 동작은 하나도
   바뀌지 않는다. 켜면 중계는 연결마다 `_read_request` 로 요청 전체를 읽어 `TcpRelay.recorded_requests` 목록에 넣은 뒤
   넘긴다. 본문이 64 KiB(서버의 JSON 상한)를 넘으면 본문 대신 길이와 sha256 만 둔다. 산출물 PUT 을 메모리에 올리지 않으려는
   것이다. 기록은 분단이나 붙잡기 같은 고장 기능과 함께 쓸 수 있다(F65). `tests/worker_golden.py` 는 그 목록을 한 줄마다
   `method`, `path`, 정렬한 `header_names`, 순서를 지킨 `body_keys`, 정규화한 `body` 로 바꾼다.

   정규화와 허용 차이는 `tests/golden/worker/allowed.json` 에 두 갈래로 적는다.

   - `typed_values` 는 값을 보지 않고 타입만 보는 자리의 JSON 경로 목록이다. `User-Agent` 의 `rcm/<버전>` 꼬리, 세션 id
     값, 시각, 등록의 `version`, `host_name`, `host` 의 아홉 값, heartbeat 의 `host_sample` 안 값, `/worker/capabilities`
     본문의 잰 값, 홈 경로 · 호스트 이름 · 사용자 이름(§8)이 여기 든다. 키 이름과 순서는 계속 대조한다. `host` 는
     `HOST_KEYS` 순서로 본다(F68).
   - `rust_only_keys` 에는 Rust 만 보내는 등록 키 `worker_impl` 을 둔다(Q9 (a) 일 때).

   `version` 은 타입만 보는 대신 골든 시험이 따로 단언한다. Python 으로 녹화하거나 대조할 때는 `__version__` 과 같아야
   한다. Rust 를 대조할 때는 `rcmw --version` 과 같아야 한다. 그래서 버전을 올리거나 다른 머신에서 돌려도 Python 골든
   시험은 초록이고 버전 문자열이 틀리면 빨갛다. 다시 녹화하는 명령은 `tools/worker_golden.py --record` 다. 시험은 Python
   도 골든과 같은지 본다. 그래서 Python 쪽이 바뀌어도 골든 diff 로 드러나므로 Q2 의 규칙을 기계가 지킨다.
2. **디스크 골든** `tests/golden/worker/disk/<상태>/` 에는 Python 세션 폴더의 `outbox.json` · `stream.log` ·
   `claim.json` · `proc.json` · `owner.json` · `ok_at.json` 을 둔다. Rust 는 이 파일을 읽어 이어 보내고 같은 상태에서 같은
   바이트를 쓴다. 바이트 꼴은 `\uXXXX` 이스케이프를 포함한 `json.dumps(sort_keys=True)` 다. Rust 가 쓰고 Python 이 읽는
   반대 방향은 e2e 교차 시험(RW3)이 잡는다.
3. **공유 벡터** `tests/vectors/worker/*.json` 은 순수 판정의 입력과 기대 출력을 적은 표다. 벡터는 `build_env` ·
   `find_tool` · `missing_tools` · CLT shim · `ident`(stat · lstart) · `owner_sha` · `chunk` · `tar_filter` · `exit_rules` ·
   `refused`(RW11 의 응답 표) · `constants` · `error_class`(결정 376) · `log_contract`(결정 392)다. Q5 가 (b) 면
   `config_errors` 를 더한다. Python 시험 `tests/test_worker_vectors_m5u.py` 는 Python 함수로, cargo 시험은 Rust 함수로 같은
   파일을 먹는다. `log_contract` 에는 e2e 가 단언하는 워커 로그 줄(F54)마다 id, 글 틀, Python 출처 `file:line` 을 적는다.
   Python 시험은 출처 함수를 불러 그 틀대로 찍는지 보고 cargo 시험은 Rust 가 같은 바이트로 찍는지 본다. `refused` 에는
   3xx 행이 있다. 이 행만 기대를 구현별로 따로 적는다. Python 은 리다이렉트를 따르고 Rust 는 따르지 않는다(Q11).

「Python 으로 몰래 물러나 통과」는 conftest 의 선택기가 막는다(RW26).

- 선택기 `RCM_TEST_WORKER_IMPL=rust` 면 conftest 가 `os.environ` 에 `RCM_WORKER_IMPLEMENTATION=rust` 와
  `RCM_WORKER_RUST_BINARY=<릴리스 프로필 빌드>` 를 넣는다. `clean_env` 가 `os.environ` 을 복사하므로 이 값은 하위 프로세스까지
  간다(`tests/test_e2e_worker.py:195-212`). 바이너리가 없거나 아키텍처가 틀리면 skip 하지 않고 세션 오류로 끝낸다.
- 선택기에는 예외가 하나 있다. 서버가 내준 바이너리로 넘어가는 시험은 워커 env 에서 `RCM_WORKER_RUST_BINARY` 를 뺀다. PR6
  의 `tests/test_e2e_rust_follow_m5u.py` 에 든 RW21 e2e 와 `RUST_REPLACED` 대체 시험이 그렇다. env 가 따라가기 캐시를
  이기므로(§4.3) 그대로 두면 둘째 버전 서버 앞에서 옛 바이너리의 `--version` 이 달라 종료 2 로 끝난다. 그 시험에서 「진짜
  Rust」 확인의 기대 경로는 서버가 내준 캐시 파일 `$XDG_CACHE_HOME/rcm/workers/<sha256 앞 16자>/rcmw` 다. sha256 은 서버가
  내준 값과 같아야 한다. 시험은 `XDG_CACHE_HOME` 을 tmp 로 준다. `RCM_WORKER_IMPLEMENTATION=rust` 는 그대로 둔다.
- conftest 의 노드 이름 목록 `RUST_KNOB_TESTS` 에 든 시험만 `test-knobs` 빌드로 바꿔 낀다. 지금은 RL18 하나다. 나머지는
  배포와 같은 릴리스 프로필로 돈다.
- conftest 의 사전 `RUST_REPLACED` 는 python 전용 시험을 그것을 대신하는 Rust 시험의 이름에 잇는다. 여기 든 시험은 Rust
  판에서 빠지고 요약에 「replaced N」이 찍힌다. 사전의 값이 실제 시험 이름인지는 conftest 가 잠근다. m5q 6개와 RL12-b(F53)가
  여기 든다(PR6).
- 진짜 Rust 인지는 `WorkerProc.start` 의 `wait_registered` 다음과, 워커가 살아 있으면 `WorkerProc.kill` · `M5nWorker.kill`
  직전에 확인한다. 워커 pid 의 실행 파일 realpath 가 선택한 바이너리와 같아야 한다. 실행 파일은 Linux 에서
  `readlink /proc/<pid>/exe` 로, macOS 에서 `lsof -a -p <pid> -d txt -Fn` 의 첫 `n` 줄로 본다. 등록은 Rust 가 하므로 이
  시점은 exec 뒤다. 1판의 「자식 python 0」 단언은 뺐다. 잡이 python 일 수 있기 때문이다(RL18 의 `GEN`). macOS 의
  `lsof -d txt` 첫 줄이 실행 파일인지는 아직 재지 않았고(미측정) PR4 의 첫 실행에서 확인한다.
- Rust 모드의 teardown 은 워커 로그에 `panicked at` 이 0건인지 단언한다. Python 쪽 `Traceback` 단언(F54)의 짝이다.
- 요약 줄에는 구현별 통과 수, `rust_pending.txt` 의 수, replaced 수를 찍는다.

시험 훅은 결정 373 이 정한다. 시계와 가짜 서버를 주입하는 코드는 `#[cfg(test)]` 안에만 둔다. e2e 가 상수를 줄이는
자리(F31)는 cargo 기능 `test-knobs` 로 빌드한 시험용 바이너리만 읽는 env 로 둔다. 그 env 이름은
`RCM_TEST_LOG_BATCH_BYTES` 이고 지금 grep 0건이다. 릴리스 바이너리에 그 글자가 없는지는 pytest 가 바이너리 바이트를 읽어
검사한다(RW26 d). `test-knobs` 빌드는 컴파일할 때 env `RCM_TEST_BUILD_VERSION` 이 있으면 `--version` 과 등록 `version` 을
그 값으로 낸다. RW21 e2e 의 둘째 버전에 쓰는 장치다(PR6). 릴리스 프로필에는 그 코드가 없다. 바이트 검사가 `RCM_TEST_`
0건을 본다.

### 4.2 범위와 순서 (결정 361)

오너가 고른 범위인 워커 · 러너 · 감독자가 「이점이 큰 것」이다. 그 안의 순서는 머지할 수 있는 가장 작은 조각부터이고,
조각마다 동치 시험을 통과해 머지한다.

이 순서는 위험이 작은 것부터다. 「우선 이점이 큰거부터」를 세 조각 사이의 순서가 아니라 범위로 읽은 것이다. 그런데 첫
PR 의 이점이 가장 작다(F6). 그래서 결정 361 은 ⛔ 이고 Q18 로 묻는다(§1.2 의 6).

감독자가 먼저다(PR2). 감독자는 argv 계약이 닫힌 별도 프로세스라서(F4 · F5) Python 워커를 건드리지 않고도 Rust 사슬
전체를 처음 검증할 수 있다. 사슬은 작업 공간, 툴체인 고정, rcm-ci, 변이, 벡터, 버전 잠금이다. 감독자 하나만 바꿔서 얻는
이점은 작다(F6). 감독자를 먼저 두는 이유는 이점이 아니라 위험이다.

러너만 따로 바꾸는 안은 버린다(F3). 러너는 Python 워커 안에서 부르는 함수라서 따로 바꾸려면 나중에 버릴 프로세스 사이
통신(IPC)을 새로 만들어야 한다. 러너는 PR3 에서 라이브러리로 만들고 PR4 의 워커가 그것을 쓴다.

버린 안이 셋 더 있다.

- 워커를 통째로 한 PR 에 담는 안. 7천 줄이 넘어(F1) 검증할 수 없는 크기다.
- 러너, 감독자, 워커 순서를 그대로 따르는 안. 러너 단독 교체가 IPC 를 요구한다(F3).
- 워커를 먼저 하는 안. 감독자를 Python 으로 부르면 파이썬 의존이 남고 사슬을 검증하기 전에 가장 큰 조각을 건다.

PR4 머지부터 PR6 머지까지 dev 의 Rust 워커는 기능이 덜 찼다. PR4 와 PR5 사이에는 git_ref, 산출물, 호스트 사실, 드레인이
없다. PR6 전에는 서버가 바이너리를 내주지 않아서 둘째 버전으로 넘어가는 e2e 가 없다. 그동안 Rust 로 못 도는 e2e 는
`tests/rust_pending.txt` 에 이유와 함께 적는다. PR5 가 git_ref · 산출물 · 호스트 줄을 지운다. PR6 은 남은 줄을 지우고(m5q
6개와 RL12-b 는 `RUST_REPLACED` 로 옮긴다) 파일이 비었는지 잠근다. 릴리스는 PR8 뒤다.

### 4.3 구현 선택과 되돌리기 (결정 362 · 383)

제품 표면에 새 이름을 넷 더한다. 모두 지금 grep 0건이다. 워커 설정 최상위 키 `implementation` 은 `"python"` 이나
`"rust"` 이고 기본은 `python` 이다. `rust_binary` 는 바이너리 경로이고 주지 않아도 된다. env 인
`RCM_WORKER_IMPLEMENTATION` 과 `RCM_WORKER_RUST_BINARY` 는 파일보다 우선한다. 토큰 `RCM_WORKER_TOKEN` 과 같은 우선순위다
(F23). 플래그는 더하지 않는다. 얼릴 CLI 표면을 줄이려는 것이다. 이 네 이름은 머지 뒤 얼리는 설정 키와 env 이름이라서
바이너리 이름과 함께 Q13 으로 묻는다(결정 362 · 384 ⛔).

바이너리를 찾을 때는 `rust_binary` 를 먼저 보고(env 가 파일보다 먼저), 다음으로 따라가기 캐시를 본다. 따라가기 캐시는
PR6 에서 생기고 Q3 이 (a) 일 때만 있다. `PATH` 는 보지 않는다. 서비스 PATH 에 따라 다른 파일이 뜨는 길을 막으려는 것이다.

`implementation = "rust"` 일 때 기동 전에 종료 2 로 끝나고 한 줄을 남기는 경우는 이렇다. 설정이 가리킨
바이너리(`rust_binary`)가 없거나, 실행 비트가 없거나, 아키텍처가 틀리거나, `--version` 이 `__version__` 과 다를 때다.
서버가 내준 바이너리(PR6)의 sha256 이 틀리거나, 크기 상한을 넘거나, 캐시 폴더가 0700 이 아니거나 내 소유가 아닐 때도 같다.
Rust 가 모르는 설정(결정 390, §4.4)도 여기에 든다. 이 검사는 서버 찾기보다 먼저 돈다(§4.4, F64). 어느 경우든 Python 으로 물러나지 않는다(결정 362 · 365).

Python 으로 도는 길은 하나뿐이고 Q4 가 (a) 일 때만 있다. `rust_binary` 를 주지 않았고 서버가
`GET /worker/binary/<target>` 에 404 `no_worker_binary` 로 답했을 때다. 이 응답은 서버에 자기 버전의 그 target 바이너리가
없다는 뜻이다. 이때 앞단은 아래 한 줄을 남기고 Python 워커로 돈다. 처음 기동이든 드레인 뒤 재실행이든 같다. Q4 가 (b) 면
이때도 종료 2 다.

```
no rcmw <버전> for <target> on the server — running the Python worker
```

시험 선택기 `RCM_TEST_WORKER_IMPL=python|rust` 는 `tests/conftest.py` 만 읽는다. 제품 코드에는 없다.

되돌리는 방법은 `implementation = "python"` 으로 바꾸거나 env 를 지운 뒤 서비스를 재시작하는 것이다. 두 구현이 같은
data_dir 를 이어 쓰므로 큐와 고아 판정도 이어진다(RW1 · RW3 교차). 자동 폴백은 없고 예외는 Q4 의 답뿐이다(결정 383).
`rcm worker --check` 는 지금 고른 구현과 바이너리를 한 줄로 말한다(PR7).

### 4.4 실행 형태와 설정 넘기기 (결정 363 · 388 · 390 · 393, Q5 ⛔)

추천하는 형태 A 가 이번 마일스톤에서 동치를 증명하는 길이다. Python `rcm worker` 가 지금처럼 설정을 풀고 검사한다. 그래서
오류 문장과 종료 2 가 그대로다(F23). `implementation = "rust"` 면 앞단은 서버를 찾기 전에, 그래서 `--check` 와 따라가기
①보다도 먼저 Rust 가 모르는 설정과 `server = "auto"` 를 거절한다(결정 390, 아래). 걸리는 것이 없으면 M5q 의 따라가기 ①②
로 서버 버전을 따라간다. 그다음 아래 호출로 같은 PID 를 Rust 로 바꾼다.

```python
os.execve(<rcmw>, [<rcmw>, "run", "--handoff-fd", "<n>", "--", *sys.argv[1:]], env)
```

`--` 뒤의 꼬리는 원래 `rcm worker …` 인자다. Rust 는 이 꼬리를 읽지 않는다. Rust 의 입력은 넘기기 문서 하나다. 꼬리를 두는
까닭은 `ps` 와 `pgrep -f "worker --server …"` 에 이 프로세스가 워커로 보여야 하기 때문이다. 그래야 e2e teardown(F53)과
사람이 워커를 찾는 길이 그대로다. 토큰은 플래그로 줄 수 없으므로(`config.py:2017`) 꼬리에 없다. `env` 는 앞단의
`os.environ` 그대로다. 드레인 뒤 다시 뜬 앞단이 `RCM_WORKER_TOKEN` 을 다시 읽어야 하기 때문이다. 잡 env 는 Rust 가 비우고
새로 만들므로(F15) 토큰이 잡으로 새지 않는다.

설정은 이렇게 넘긴다. 앞단은 `tempfile.TemporaryFile()` 로 만든 파일에 JSON 한 문서를 쓰고 그 fd 만 상속시킨다. 이 파일은
0600 이고 만들자마자 이름이 없어진다. 문서에는 풀린 설정 가운데 `HANDOFF_KEYS` 에 든 필드(토큰 · `server_name` 포함
13개, F62), 앞단이 잰 `python` 버전, `--once`, 따라가기 상태, `restart` 가 들어간다. 따라가기 상태는 `following`, 드레인
여부, 따라가지 않는 이유 꼬리다. `restart` 는 다시 실행할 방법이다(F56). 그 꼴은 `{"argv": [sys.executable, "-P", "-c", RESTART, *sys.argv[1:]],
"drop_env": BOOT_ENV, "stamp_env": "RCM_WORKER_RESTARTED_AT"}` 다. `discovered_server` 는 뺀다. Rust 는 `auto` 를 받지
않기 때문이다. Rust 는 문서를 읽고 곧바로 닫는다. 토큰은 argv,
`/proc/<pid>/cmdline`, 디스크 경로 어디에도 나오지 않는다(RW23). 파이프는 쓰지 않는다. exec 전에 버퍼(수십 KiB)를 넘으면
앞단이 막히기 때문이다.

Rust 가 모르는 설정은 앞단이 거절한다(결정 390). 거절은 `worker_launch.refuse_unsupported(cfg)` 한 줄이 맡는다. 이 줄은
`cli._worker_config` 안에서 `load_worker_config(…)`(`cli.py:4765`) 바로 뒤, `if cfg.wants_discovery:`(`:4766`) 앞에 둔다.
`cfg.implementation` 이 `"rust"` 가 아니면 이 함수는 아무것도 하지 않는다. 그 자리에서 `cfg.server` 는 아직 설정 파일이나
플래그가 준 원래 값이다(F64). 그래서 관문은 서버 찾기보다 먼저 돈다. `--check` 와 따라가기 ①보다도 먼저다.

거절 조건은 둘이다. 하나는 `cfg.wants_discovery` 다. `server` 가 비었거나 `auto` 면 참이다. 다른 하나는
`RUST_REFUSED_KEYS` 에 든 필드가 기본값이 아닌 경우다. 둘 중 하나라도 걸리면 관문이 `ConfigError` 를 낸다. `cmd_worker` 의
`except ConfigError` 가 그 오류를 받아 `_usage` 로 종료 2 를 낸다(`cli.py:4944-4946`). 남는 줄은 아래 한 줄이다.

```
rcm: <key> is not supported by the Rust worker yet — use implementation = "python"
```

`<key>` 는 첫째 조건이면 `server = "<원래 값>"`, 곧 `server = "auto"` 나 `server = ""` 이다. 둘째 조건이면 그 필드
이름이다.

걸렸을 때 mDNS 질의는 0회다. 부모가 넘긴 `RCM_SERVER` 도 읽지 않는다. 확인 질문은 0회, 서버로 가는 요청은 0건이고 토큰을
쓰지 않는다. `rcm worker --check` 도 같은 줄과 종료 2 로 끝난다. 그 설정은 돌 수 없는 설정이기 때문이다. PR7 의 `--check`
구현 행은 관문을 지난 설정에서만 나온다.

시험은 손으로 만든 `WorkerConfig` 를 쓰지 않는다. `cli._main(["worker", "--config", <toml>], follow_enabled=True,
execve=<가짜>)` 로 실제 `cmd_worker` 흐름을 돈다. `cli.discover` 는 부른 횟수를 세는 가짜로 바꾸고 그 수가 0 인지
단언한다. 가짜 `execve` 도 0회다(RW32, 변이 `m5u-gate-after-discovery`).

잠금 시험은 `dataclasses.fields(WorkerConfig)` 의 이름 집합이 `HANDOFF_KEYS` ∪ `FRONT_KEYS` ∪ `RUST_REFUSED_KEYS` 와
같은지, 셋이 서로 겹치지 않는지 본다. `FRONT_KEYS` 는 앞단만 쓰고 넘기지도 거절하지도 않는 필드이고
`implementation` · `rust_binary` · `path` 가 든다. 세 목록은 `worker_launch.py` 에 둔다. Rust 가 아는 넘기기 키 목록은 벡터
`tests/vectors/worker/handoff_keys.json` 에 둔다. Python 시험은 그 목록이 `HANDOFF_KEYS` 와 설정 밖 키(`python` ·
`--once` · 따라가기 상태 · `restart`)를 합친 것과 같은지 본다. cargo 시험은 그 목록이 Rust 의 목록과 같은지 본다. Rust 는
넘기기 문서에 모르는 키가 있으면 무시하지 않고 종료 2 로 끝낸다. M5t-c PR3 이 `pair_secret` 을 더하면 잠금이 빨개진다. 그
필드는 `RUST_REFUSED_KEYS` 에 넣는다. 누가 넣는지는 §4.10 에 있다.

버린 안은 관문이 `getattr(cfg, "discovered_server", False)` 를 읽는 것이다. 그 자리는 이미 발견(mDNS 질의, `RCM_SERVER`
읽기)이 돈 뒤다. 서버를 못 찾으면 거절 줄 대신 「worker needs a server」가 나간다.

다시 실행은 결정 393 이 정한다. 드레인이 끝났을 때, 그리고 기동 등록이 버전 409 를 받았고 `following` 일 때 Rust 는 넘기기
문서의 `restart.argv` 로 exec 한다. 버전 409 는 형식이 맞고 `server_version` 이 다른 응답이다. exec 할 env 는 자기 env 에서
`drop_env` 의 이름을 지우고 `stamp_env` 에 지금 시각(epoch 초)을 넣은 것이다. `following` 이 아니면 409 메시지를 내고 종료
2 로 끝난다. Python 과 같다(`cli.py:4993-4995`). exec 가 실패하면 `restart failed: <이유>` 를 쓰고 종료 1 로 끝난다. exec
가 되면 앞단이 새 버전 wheel 을 따라가고(①) 새 `rcmw` 를 찾아 다시 exec 한다. Rust 는 자기 자신을 내려받지 않는다. 그
바이너리가 서버에 없으면 §4.3 의 Q4 길로 간다.

형태 A 에서는 서비스 파일, 서비스 단어, doctor 의 서비스 해석이 바뀌지 않는다(F42 · F43 · F44). 한계도 있다. 워커 머신에
여전히 Python 과 rcm 이 있어야 해서 오너가 고른 이유인 「한 파일 바이너리」 이점(W3)이 아직 오지 않는다(§1.2 의 1).

형태 B 는 앞단이 Rust 를 자식으로 띄우고 감시하는 안이다. 이 안은 버린다. 프로세스가 둘이 되고 신호를 중계해야 하며,
launchd 가 보는 PID 와 일하는 PID 가 갈린다.

형태 C 는 독립 실행인 `rcmw run --config worker.toml` 이다. 파이썬이 없는 머신에서 돈다. 대신 Rust 가 TOML, 검사 문장,
자기 따라가기를 다 해야 하고 doctor 와 가드가 새 실행 파일을 알아봐야 한다(F43 · F44). `server = "auto"` 는 형태 C(PR9)
에서도 거절한다(결정 390). `--check`,
`setup`, `doctor` 는 CLI 라서 Python 에 남으므로 그 머신에서는 쓸 수 없다.

Q5 의 선택지는 셋이다. (a) 는 형태 A 만 하고 C 는 다음 마일스톤으로 미룬다. (b) 는 A 로 동치를 증명한 뒤 같은 마일스톤의
PR9 에서 C 의 `run` 만 더한다. (c) 는 C 만 한다. 기본 추천은 (b) 다. 오너가 고른 이유인 한 파일 바이너리를 이번에 채우되,
동치 증명은 A 의 기존 하네스로 한다.

### 4.5 배포 채널 · 버전 따라가기 · 서명 (결정 364 · 365 · 366 · 382 · 391, Q3 · Q4 ⛔)

실행 중에는 GitHub 에서 받지 않는다(결정 30, 결정 382).

Q3 은 바이너리를 워커 머신에 어떻게 보낼지 묻는다. 선택지는 셋이다.

- (a) 추천. 서버가 `[server] worker_binaries_dir`(새 키, grep 0건) 아래의 `<버전>/<target>/rcmw` 와 `SHA256SUMS` 를 새
  라우트 `GET /worker/binary/<target>` 로 내준다. 이 라우트는 워커 토큰을 요구한다. 앞단은 서버 버전과 다른 `rcmw` 를
  가졌거나 바이너리가 없으면 받아서 `$XDG_CACHE_HOME/rcm/workers/<sha256 앞 16자>/rcmw` 에 둔다. 받는 규칙은 F25 와 같다.
  캐시 폴더가 0700 이고 내 소유일 때만 쓴다. 리다이렉트는 따르지 않는다. 크기 상한을 두고 sha256 을 대조한다. 그 폴더는
  운영자가 채운다. 아래의 릴리스 자산에서 받거나 `cargo build` 로 만든다. 서버는 자기 버전의 바이너리만 내준다. 결정 81 의
  「서버가 도는 것과 받는 것이 같다」를 지키려는 것이다.
- (b) 머신마다 사람이 바이너리를 놓는다(`rust_binary`). 따라가기가 없어서 서버를 올릴 때마다 모든 Rust 워커를 손으로
  바꿔야 한다.
- (c) 머신마다 `cargo build` 한다. 그 머신에 툴체인이 있어야 해서 W3 와 맞지 않는다.

빌드 자리는 Q3 에 딸린 질문이다(결정 391). 선택지는 셋이다.

- (i) 추천. 태그 때 도는 `.github/workflows/release.yml` 에 잡 둘을 더한다. `rcmw-build` 는 macOS 에서
  `rust/rust-toolchain.toml` 의 버전으로, `--locked` 와 `--remap-path-prefix` 를 주어 빌드하고 결과를 actions 산출물로
  올린다. `rcmw-smoke` 는 `needs: rcmw-build` 이고 그 산출물을 내려받아 `tests/test_rust_smoke_m5u.py` 를 돈다. 스모크는
  넷을 본다. `--version` 이 태그와 같은지, `supervise` 가 미시작일 때 125 와 표지를 내는지, 진짜 서버에 등록하고 성공 잡
  하나를 돌려 finish 하는지, 와이어 골든과 같은지다. `github-release` 와 `pypi` 의 `needs` 에는 `rcmw-smoke` 를 더한다.
  그래서 바이너리 스모크가 빨가면 wheel 도 바이너리도 나가지 않는다(PLAN 「fail-open 금지」). `github-release` 는 같은
  산출물을 내려받아 자산과 `SHA256SUMS` 로 올린다. `rcmw-smoke` 와 `github-release` 는 그 파일의 sha256 을 각각 찍는다.
  순서와 `needs` 는 `tests/test_ci_release_workflow.py` 가 잠근다(F69, RW33, 변이 `m5u-release-ignores-rcmw-smoke`).
- (i′) 잡 둘은 (i) 와 같지만 `github-release` · `pypi` 의 `needs` 에 넣지 않는다. wheel 은 나가고 바이너리 자산만 0개이며
  워크플로 run 은 빨갛다. 그 버전의 `rcmw` 가 없으면 Q4 의 길로 간다.
- (ii) 맥미니 rcm 잡이 빌드하고 메인 세션이 그 파일을 올린다. 시험한 머신과 같은 머신에서 빌드하지만 수작업 단계가 는다.

(i) 과 (i′) 는 런타임이 아니라 설치 때 GitHub 을 쓰는 것이라서 결정 30 과 맞는지는 오너가 본다.

Q4 는 버전이 도는 도중에 바뀌었는데 서버에 그 버전의 `rcmw` 가 없을 때를 정한다.

- (a) 추천. 드레인 뒤 앞단으로 exec 한다. 앞단은 wheel 을 따라가고 서버가 그 바이너리가 없다고 답하면 Python 워커로
  돈다. 바이너리가 없다는 판정은 서버의 404 `no_worker_binary` 하나뿐이다(§4.3). 이때 아래 한 줄을 남긴다. Python 워커는
  구현 표식 키를 보내지 않으므로 서버에는 「—」로 보인다(Q9 (a)). 서버가 python 으로 지어내지 않는다. 서버의 `rcm check`
  는 `worker_binaries_dir` 에 도는 버전이 없으면 경고한다.

  ```
  no rcmw <버전> for <target> on the server — running the Python worker
  ```
- (b) 드레인 뒤 종료 2 로 끝난다. 감독자가 되살려도 등록 전에 끝나므로 등록 루프는 생기지 않는다. 그러나 그 풀은
  `worker_down` 이 된다. rcm-ci 가 `mac2` 에서 돌면 필수 체크가 멈춘다.

어느 쪽이든 도는 잡은 드레인으로 끝까지 돈다(RW20).

서명은 결정 366 으로 추천한다. 링커가 거는 ad-hoc 서명(arm64 Mach-O)만 쓰고 공증은 하지 않는다. 워커나 앞단이 받은 파일은
브라우저가 받은 것이 아니므로 격리 속성이 붙지 않는다. 다만 이 값은 아직 재지 않았고(—) PR8 에서 `xattr -l` 로 확인한다.
브라우저로 받은 경우에 쓸 `xattr -d com.apple.quarantine` 은 문서에 적는다. Local Network 권한은 F45 와 RW29 가 다룬다.

### 4.6 빌드 · 시험 체계 (결정 367 · 368 · 369 · 371 · 372 · 389, Q6 · Q7 ⛔)

Rust 코드는 `rust/` 에 둔다. 작업 공간은 `rust/Cargo.toml` 이고 크레이트는 `rust/rcmw/` 다. `rust/rust-toolchain.toml` 에
버전 하나를 고정한다. 값은 PR2 시점의 stable 로 고른다. 지금은 툴체인이 없어서 고르지 못했다(—). `rust/Cargo.lock` 을
커밋하고 모든 cargo 명령에 `--locked` 를 준다. `.gitignore` 에는 `rust/target/` 을 더한다.

버전 잠금은 둘이다. 하나는 글 잠금 `tests/test_rcmw_version_lock_m5u.py` 이고 PR2 가 owns 로 새로 만든다. 이 시험은
`src/remote_ci_monitor/__init__.py` 의 `__version__`, `rust/rcmw/Cargo.toml` 의 `[package] version`, `rust/Cargo.lock` 에서
`name = "rcmw"` 항목의 `version` 을 `tomllib` 와 정규식으로 읽어 셋이 같은지 본다. cargo 가 필요 없으므로
`tests/test_rust_*_m5u.py` 의 `skipif` 밖에 있고 cargo 없는 머신에서도 돈다. 대조 함수 `version_mismatches(root)` 는 같은
파일에 있다. 시험은 두 트리를 본다. 실제 트리에서는 빈 목록이 나와야 한다. 셋 중 하나를 틀리게 쓴 tmp 트리에서는 그 이름이
목록에 나와야 한다(변이 `m5u-version-lock-skips-cargo-lock`). 다른 하나는 빌드 잠금이다. `tests/test_rust_build_m5u.py` 가
`rcmw --version` 이 `__version__` 과 같은지 본다. 이 시험에는 cargo 가 필요하다(RW20 b).

릴리스 절차는 CONTRIBUTING 「Releasing」 1단계(`CONTRIBUTING.md:88-90`)의 문장을 고쳐 셋을 함께 올리게 한다. 이 문장은
PR2 가 shared 로 고친다. 바뀐 앞부분은 아래와 같고 뒤는 그대로다.

> Bump `__version__` in `src/remote_ci_monitor/__init__.py`, `version` in `rust/rcmw/Cargo.toml` and the `rcmw` entry's
> `version` in `rust/Cargo.lock` — all three, or `tests/test_rcmw_version_lock_m5u.py` fails and every `--locked` cargo
> command refuses to run — and turn the changelog's …

이 수정은 `tests/test_ci_release_workflow.py:341-357` 의 「Releasing」 단언(태그 · no-op · Re-run 등)에는 닿지 않는다.

크레이트 허용 목록은 Q6 으로 묻는다. 추천 목록은 다섯이다. `libc` 는 flock · killpg · setsid · sigprocmask · getpgid 에
쓴다. `serde` 와 `serde_json` 은 JSON 에, `sha2` 는 sha256 에 쓴다. `miniz_oxide` 는 순수 Rust 로 된 gzip 이다. tar 읽기 ·
쓰기 · 거절 필터와 HTTP/1.1 클라이언트는 직접 쓴다. 보안 판정이 우리 코드여야 하고 크레이트 면적을 줄이려는 것이다. 1판은
mDNS 질의도 직접 쓴다고 적었는데 2판에서 뺐다. Q2 가 (a) 면 Rust 에 mDNS 가 없기 때문이다. Q2 가 (b) 면 HMAC-SHA256 이 더
필요하다. `sha2` 위에 RFC 2104 대로 직접 쓰거나 `hmac` 크레이트를 들이는데, 이것도 Q6 에서 함께 묻는다. scrypt 는 필요
없다(F61). TOML 은 Q16(결정 389)이 따로 정한다. `tests/test_rust_deps_m5u.py` 는 `Cargo.lock` 의 패키지 집합을 허용
목록으로 잠근다. 목록은 PR 마다 그 PR 의 답만큼만 는다. PR9 의 `toml` 은 PR9 에서 더한다. 전이 의존성의 정확한 수는 아직
모르고(—) PR2 의 `Cargo.lock` 에서 나온다.

rcm/ci 에서 cargo 를 어떻게 돌릴지는 Q7 로 묻는다. 추천은 pytest 안에서 cargo 를 돌리는 것이다. `tests/rust_tool.py` 는
cargo 를 찾고 잠금을 잡은 뒤 세션당 한 번 빌드한다. `tests/test_rust_build_m5u.py` 는 `slow` 표식을 달고 아래 세 명령을 돈
뒤 「test result: ok. N passed」 의 N 이 0 보다 큰지 단언한다.

```sh
cargo build --release --locked
cargo build --features test-knobs
cargo test --locked
```

env `RCM_REQUIRE_RUST=1`(이름 grep 0건)이 있으면 cargo 가 없을 때 실패한다. 그 env 가 없으면 Rust 시험을 skip 하고 요약에
빨간 줄을 찍는다. 결정 79 의 `RCM_CHROME` 과 같은 꼴이다.

건너뛰기 규칙은 결정 369 와 F63 을 따른다. Rust 가 필요한 시험은 `tests/test_rust_*_m5u.py`,
`tests/test_e2e_worker_impl_m5u.py`, `tests/test_e2e_rust_follow_m5u.py`, `tests/test_rust_smoke_m5u.py` 다. 이 모듈들은 맨
위에 아래 한 줄만 둔다. 본문 안의 `pytest.skip()` 은 0건이다.

```python
pytestmark = pytest.mark.skipif(not rust_tool.AVAILABLE, reason=…)
```

`RCM_REQUIRE_RUST=1` 이면 `rust_tool` 이 import 될 때 cargo 가 없으면 예외를 낸다. 그러면 skip 이 아니라 수집 오류로 끝난다.
그 밖의 Python 시험은 Rust 때문에 건너뛰지 않는다.

오너가 할 일은 둘이다. 맥미니의 `mac2` 워커 사용자에 rustup 과 고정 버전을 깐다. 운영 프리셋 `rcm-ci` 에는
`RCM_REQUIRE_RUST = "1"`, 데운 캐시용 `CARGO_TARGET_DIR = "~/.cache/rcm-ci/cargo-target"`, cargo 가 든 PATH 를 넣고
`requires` 에 `cargo` 를 더한다. PR2 의 머지 조건은 rcm-ci 잡 로그에 찍힌 cargo 시험 수가 0 보다 큰 것이다.

변이는 결정 372 로 정한다. mutcheck 에 `Mutant.runner = "cargo"`(F34)를 더하고 복사본에 `rust/` 를 넣는다. Python 변이
128개(F33)는 Python 구현이 남으니 그대로 둔다. Rust 에는 같은 뜻의 변이를 `rs-<python 이름>` 으로 단다(§7.5). 각 변이에서
cargo 시험이나 Rust 를 고른 pytest 가 빨개져야 한다. 대응할 Rust 코드가 없는 변이는 `n/a` 와 이유를 적는다.
`cargo-mutants` 는 들이지 않는다. 크레이트와 도구 면적이 늘고 「이 변이가 빨개야 한다」는 지금 장치와 같은 보증을 원하기
때문이다.

다른 규칙 문서도 같은 의존성 원칙을 적고 있다(F58). Q10 이 (a) 면 PR2 가 `AGENTS.md:8` 과 `CONTRIBUTING.md:37` 을 같은 뜻으로
고친다(E6 · E7). 추천 문장은 영어이고 두 파일에서 뜻이 같다.

> **Zero runtime dependencies in the Python package.** Standard library only, server and client. The optional worker
> binary `rcmw` (`rust/`) links only the allow-listed crates pinned in `rust/Cargo.lock` (PLAN 「의존성 원칙」).

`README.md:34` 와 `PLAN.md:537` 은 패키지 이야기라서 고치지 않는다.

Linux 는 §4.8 에서 다룬다.

### 4.7 와이어 · 디스크 · 프로세스 동치 (결정 385 · 387 · 375 · 376 · 374)

프로토콜 2 는 핀의 꼴 그대로다(결정 385, F7 · F11). M5t-c 가 더하는 부분은 결정 379 의 답을 따른다. 이 명세가 더하는 키는
결정 374 의 답뿐이다. 요청 순서, 키, 헤더 이름은 골든(§4.1)이 잠근다.

HTTP 규칙은 결정 387 이다. 요청마다 새 TCP 연결을 열고 `Connection: close` 를 단다. 본문은 `Content-Length` 로만 보낸다.
서버가 chunked 에 411 을 돌려주기 때문이다. 리다이렉트는 따르지 않는다. 기한은 Python 과 같아서 일반 요청 15초, 로그
60초, 산출물 300초이고 claim 은 `wait_seconds` 를 더한다. 본문은 메모리에 통째로 올리지 않고 흘려 보낸다. 2xx 인데 JSON 이
아니면 502 로 읽는다(RW11).

3xx 는 오류로 읽는다. 5xx 처럼 백오프하고 그 항목은 버리지 않는다. 그리고 1분에 한 번 아래 한 줄을 쓴다. Python 은
리다이렉트를 따르므로(F55) 이 차이는 Q11 에 묶인다(§1.2 의 2).

```
server answered <code> — rcmw does not follow redirects; set server to the final URL
```

`host.python` 은 결정 375 다. 형태 A 에서는 앞단이 잰 값을 보내고 독립 실행에서는 `null` 을 보낸다. 서버는 `null` 을
받는다(F27).

공개 요약의 `error` 인자는 결정 376 이다. Python 예외 클래스 이름을 errno 대응표로 그대로 낸다. `PermissionError`,
`FileNotFoundError`, `IsADirectoryError`, `OSError` 같은 이름이다. 벡터는 `error_class` 이고 요약 문구는 바뀌지 않는다.
scenario-author 의 질문 11 은 이 추천으로 닫았다.

구현 표식은 Q9 로 묻는다.

- (a) 추천. Rust 워커만 등록 본문 최상위에 `"worker_impl": "rust"` 를 싣는다(PR4). 서버(PR7)는 등록 때 그 값을 읽는다.
  문자열 `"rust"` 와 `"python"` 만 받고 그 밖의 값은 없음으로 본다. 받은 값은 호스트 사실과 따로 메모리에 둔다. 등록마다
  값을 바꾸므로 앞 프로세스의 값을 물려받지 않는다. 토큰이 있는 머신 문서에는 키 `worker_impl` 로, `rcm machines` 에는
  「rust」나 「—」로 보인다. Python 워커는 바뀌지 않아 이 키를 보내지 않으므로 「—」다. 서버가 python 으로 지어내지 않는다.
  호스트 사실 아홉 키, `HOST_KEYS == HOST_FACT_KEYS` 잠금, 토큰 없는 머신 문서의 `MACHINE_KEYS` 는 그대로다(F52). 옛
  서버는 이 키를 버린다. 와이어 골든은 이 키를 `allowed.json` 의 `rust_only_keys` 에 이름으로 둔다. 고칠 파일은
  `remote_workers.py`(등록 두 갈래 `:703` · `:755` 곁), `capabilities.py`(메모리와 문서 키), `cli.py`(`_machine_detail`)다.
- (b) 등록 키, DB 칸, `/api/status` `workers[]` 키를 더한다. DB 27 이 M5t-c 와 겹친다.
- (c) 더하지 않는다. 증거는 실행 파일 검사와 워커 로그 첫 줄 `implementation: rust` 뿐이다.

프로세스 규칙은 이렇다. env 를 비우고 시작한다(F15). `setsid` 를 부른다(F16). 신호 마스크를 비우고 SIGPIPE 를 기본
처분으로 되돌린다. Rust 런타임은 SIGPIPE 를 무시로 두므로 자식에서 되돌려야 한다. RW7 이 `SigIgn` 과 `SigBlk` 로 이것을
잠근다. 3번 이상의 fd 는 0개다(F17). stdout 과 stderr 는 한 파이프로 받고 stdin 은 `/dev/null` 이다.

### 4.8 플랫폼과 Linux 증거 (결정 370, Q8 ⛔)

필수 체크는 arm64 macOS 한 대에서 돈다(F36 · F38). Linux 의 `/proc` · `flock` · `killpg` 와 x86_64 는 거기서 돌지 않는다.
M5t-b 는 Linux 증거 없이 머지됐다(F39). 운영 서버에 붙은 머신도 지금은 macOS arm64 한 대뿐이다(F47 · F48).

Q8 의 선택지는 넷이다.

- (a) 오너의 Linux 머신을 rcm 원격 워커로 붙이고 프리셋(예 `rcm-ci-linux`)이 Rust 시험 묶음을 돈다. 머지 전 증거 표에 그
  잡 번호를 적는다.
- (b) 맥미니에 VM 이나 컨테이너 도구를 깐다. 운영 머신을 바꾸는 일이다.
- (c) `ci.yml` 의 ubuntu 잡을 머지 전에 손으로 돌린다. 2026-11-20 오너 결정 「GitHub CI 는 오너 지시 때만」과 부딪힌다.
- (d) 이번 마일스톤은 macOS arm64 만 배포하고 Linux 와 x86_64 워커는 Python 그대로 둔다. 지원을 줄이는 것은 아니다.
  Python 워커가 계속 지원한다.

기본 추천은 (d) 이고 (a) 를 할 수 있으면 더한다. 증거가 없는 target 은 `SHA256SUMS` 와 `worker_binaries_dir` 목록에서
뺀다(RW30). 운영 사실이 이 질문에 주는 것은 §3.5 에 적었다.

### 4.9 서버 쪽과 공유 코드 (결정 380, Q17 ⛔)

서버 로컬 레인을 Python 에 두는 것은 Q17 의 기본 (a) 다(§1.2 의 5). (b) 는 로컬 레인의 표식 잡만 서버가 찾은 같은 버전의
`rcmw supervise` 로 감싸는 안이다. 감독자는 이미 따로 뜨는 프로세스라서(F4) `runner.supervised_argv` 가 고르는 argv 만
바뀐다. (c) 는 로컬 레인 잡을 `rcmw` 로 돌리는 안이다. 서버와 `rcmw` 사이에 IPC 가 필요해서(F3) 결정 361 이 버린 길이다.

서버 로컬 레인은 Python `runner.run_job` 과 `job_supervisor.py` 를 그대로 쓴다. `runner.find_tool` · `missing_tools` ·
`measure_tools` · `tool_blocked`(`runner.py:151-273`)는 서버와 `rcm check`, doctor 가 쓰므로 Python 에 남는다. Rust 는 같은
판정을 벡터로 맞춘다(RW28).

서버 변경은 Q3 (a) 의 라우트와 키, 그리고 Q9 (a) 의 등록 키 읽기와 메모리, 머신 문서 키다. 옛 Python 워커는 그대로
돈다.

### 4.10 M5t-c 와의 순서 (결정 379 · 390, Q2 ⛔)

M5t-c PR3 이 워커 쪽에 짝짓기 확인 고리를 더한다(F61). 1판의 Q2 (a) 는 Rust 가 그 고리까지 옮긴다고 하면서 결정 385(1판
380)에서는 프로토콜 2 를 그대로 둔다고 했다. 두 말이 맞지 않았고 옮길 PR, RW 행, 크레이트, 변이도 적혀 있지 않았다(1라운드
HIGH). 또 1판 PR1 의 골든 시험은 Python 도 골든과 같은지 본다. 그래서 M5t-c PR3 이 Python 와이어를 바꾸면 그 PR 의 rcm/ci
가 빨갛게 되는데, 골든 재녹화는 그 PR 의 owns 와 shared 에 없어서 머지할 수 없었다(교착).

Q2 의 선택지는 셋이다.

- (a) 추천. M5u 의 Rust 워커는 M5t-c 의 워커 쪽을 옮기지 않는다. Rust 가 모르는 설정(`pair_secret` 등)과
  `server = "auto"` 는 앞단이 기동 전에 종료 2 로 거절한다(결정 390, RW32). 짝지은 머신과 `auto` 머신은 Python 워커로
  돈다(§1.2 의 7). 결정 385 의 「프로토콜 2 그대로(핀 기준)」가 그대로 맞는다. M5u 와 M5t-c 의 순서는 서로 묶지 않는다.
  Rust 로 옮기는 일은 다음 마일스톤에서 한다.
- (b) M5u 가 옮긴다. M5t-c PR3 과 M5u PR5 가 머지된 뒤 새 PR10 을 둔다. 그 PR 은 hello, `X-RCM-Pair-Nonce`,
  `pair_reject_mac` 검증(HMAC-SHA256, Q6), `pair_proof`, 상태 파일, 다시 찾기 검증과 mDNS 를 옮긴다. 상태 파일은 Python
  `pair_state.py` 와 같은 바이트로 쓰고 J1~J14 와 A1~A10 은 벡터로 둔다. 새 RW 행과 M5t-c 변이의 `rs-` 대응도 그 PR 에
  든다. 지금은 적지 않는다. (b) 를 고르면 그 PR 의 명세 보충이 먼저다. 결정 385 는 「프로토콜 2 + M5t-c 추가분」으로
  넓어진다. M5u 는 M5t-c PR3 을 기다린다. (b) 의 PR10 은 지금 §5 · §6 · RW · 변이가 없는 자리표시자다. (b) 를 고르면
  이 명세로는 지을 수 없다. PR10 의 명세 보충 PR 과 그 plan 라운드가 먼저다.
- (c) M5t-c 를 M5u 뒤로 미룬다. 짝짓기 보안이 늦어진다.

교착은 어느 선택지든 같은 방법으로 푼다. 순서는 묶지 않는다. 장치가 생긴 뒤에 머지하는 쪽이 맞춘다. 장치는 둘이고 생기는
때가 다르다.

- **골든**(`tests/golden/worker/`)은 M5u PR1 이 머지될 때 생긴다. 그 뒤로 Python 워커의 와이어나 세션 폴더 파일을 바꾸는
  PR 은 M5t-c PR3 을 포함해 같은 PR 에서 `tools/worker_golden.py --record` 로 다시 녹화하고 그 폴더를 shared 에 더한다.
  녹화 diff 는 리뷰 대상이다. PR1 보다 먼저 머지된 변경은 PR1 이 그 sha 에서 녹화하므로 따로 할 일이 없다.
- **키 잠금**(`worker_launch.py` 의 세 집합과 그 잠금 시험)은 M5u PR4 가 머지될 때 생긴다. PR4 는 자기 base 의
  `WorkerConfig` 로 잠금을 쓴다. base 에 `pair_secret` 이 이미 있으면 PR4 가 그것을 `RUST_REFUSED_KEYS` 에 넣는다. PR4
  뒤로 `WorkerConfig` 에 필드를 더하는 PR 은 같은 PR 에서 그 필드를 셋 중 하나에 넣는다. 잠금이 빨개서 넣지 않고는 머지할
  수 없다.
- Rust 쪽 와이어를 바꾸는 PR 은 Python 과 Rust 를 같은 PR 에서 고친다. 이 규칙은 1판 그대로다.

그래서 M5t-c PR3 이 언제 머지되느냐에 따라 할 일이 세 경우로 갈린다.

| M5t-c PR3 머지 때 | M5t-c PR3 이 할 일 | M5u 쪽이 할 일 | §13 문장이 가는 곳 |
|---|---|---|---|
| M5u PR1 앞 | 없음 | PR1 이 그 sha 에서 녹화. PR4 가 `pair_secret` 을 `RUST_REFUSED_KEYS` 에 | M5u §13(PR4) |
| M5u PR1 뒤 · PR4 앞 | 골든 재녹화(와이어가 바뀌었으면) | PR4 가 `pair_secret` 을 `RUST_REFUSED_KEYS` 에 | 녹화 몫은 M5t-c §13, 거절 목록 몫은 M5u §13(PR4) |
| M5u PR4 뒤 | 골든 재녹화 · `pair_secret` 을 `RUST_REFUSED_KEYS` 에 | 없음 | M5t-c §13 |

§13 문장은 메인 세션이 위 표의 마지막 칸이 가리키는 PR 에서 적는다. 그 경우에 해당하는 몫만 적는다.

> 명세에 없음 · 워커 골든과 Rust 거절 목록 · 정의 없음 → 이 PR 이 `tools/worker_golden.py --record` 로
> `tests/golden/worker/` 를 다시 녹화했다(shared 에 더함) / `pair_secret` 을 `worker_launch.RUST_REFUSED_KEYS` 에 넣었다 ·
> M5u 의 골든(PR1) · 키 잠금(PR4)이 이 PR 의 와이어 · 설정 변경을 빨갛게 했다 · M5u 결정 379 · 390

**파일 겹침과 머지 순서.** M5u 와 M5t-c 는 아래 파일을 함께 고친다(F67). 고치는 자리는 다르다.

| M5u PR | M5t-c PR | 파일 | M5u 의 자리 | M5t-c 의 자리 |
|---|---|---|---|---|
| PR4 | PR3 | `cli.py` | `_worker_config` 의 관문 한 줄 · `cmd_worker` 분기 한 곳 | `_worker_config` · `_pick_worker_server` · `_confirm_found_server` · `_worker_follow_start` · `_worker_check` 등 |
| PR4 | PR3 | `config.py` | `_WORKER_KEYS` 키 둘 · `WorkerConfig` 필드 둘 · env 둘 | `pair_secret` 키 · 꼴 검사 |
| PR4 | PR3 | `templates/worker.toml` · `examples/worker.toml` | 키 둘 주석 | `pair_secret` 주석 |
| PR6 | PR1 · PR2 | `server.py` | 라우트 `GET /worker/binary/<target>` 한 곳 | PR1 `_send_error` · `/pair/*`, PR2 `worker_spec_for` |
| PR6 | PR3 | `config.py` | 서버 키 `worker_binaries_dir` | 워커 키 `pair_secret` |
| PR6 · PR7 | PR1 · PR2 · PR3 · PR4 · PR5 | `docs/configuration.md` | 키 절 | 짝짓기 키 · 절 |
| PR7 | PR2 | `remote_workers.py` · `capabilities.py` | 등록 두 갈래(`:703` · `:755`) 곁의 `worker_impl` 읽기 · 메모리 · 머신 문서 키 | 등록의 `check_register_proof`(`:686-687` 자리) · 풀 검사 |
| PR7 | PR3 | `cli.py` · `doctor.py` | `_worker_check` 의 구현 행 · `_machine_detail` · doctor 구현 행 | `_worker_check` · doctor 의 짝짓기 행 |
| PR7 | PR4 · PR5 | `README.md` · `README.ko.md` | 워커 절 한 줄 | 처음 설정 · 워커 붙이기 |
| PR7 · PR9 | PR5 | `docs/operating.md` | 전환 · 되돌리기 · 서비스 | 짝짓기 운영 |
| PR9 | PR3 | `doctor.py` | `rcm_launch` 의 `rcmw` 해석 | 짝짓기 행 |

순서 규칙은 이렇다. 표의 두 PR 이 함께 열려 있으면 M5t-c 쪽을 먼저 머지한다. M5u 쪽은 dev 를 합쳐 다시 맞춘 뒤 자기
잠금(골든 · 키 잠금 · doc-lock)을 다시 돈다. 하나가 이미 머지됐으면 나중 쪽이 맞춘다. 까닭은 둘이다. M5t-c 는 명세가 먼저
승인된 보안 기능이다. M5u PR4~PR9 는 Q2 (a) 에서 M5t-c 의 내용에 기대지 않는다. M5t-c 는 M5u 를 기다리지 않으므로 이 규칙
때문에 서로 기다리는 일은 없다. 두 PR 의 P7 머지는 같은 때 돌리지 않는다. 묶음 경로가 겹치는지는 `tools/dev_batch.py` 로
본다.

## 5. 작업 분할과 병렬

PR 하나에 브랜치 하나, 워크트리 하나다. 브랜치 이름은 제안이고 `branch` 스킬로 확정한다. `shared` 파일은 덧붙이기만
한다. 레지스트리 파일인 `CHANGELOG.md` · `scripts/mutcheck.py` · doc-lock 시험은 늘 `shared` 다.

lane 열은 PR 마다 추천하는 검증 경로다. full 은 빌드, 리뷰, 실기(필요할 때), 엣지 감사, 빈틈 고치기를 거쳐 머지하고
short 는 빌드와 리뷰 뒤 머지한다(workplan 스킬 §3). 어느 쪽으로 갈지는 오너가 정한다(Q1).

PR0 의 마지막 커밋은 PLAN.md 를 아래처럼 고친다(E1~E5). 집 관례다(`docs/m5tc-workplan.md:971-975`). E6 과 E7 은 PLAN 밖의
규칙 문서라서 Rust 코드가 들어오는 PR2 가 shared 로 고친다.

| # | 파일:줄 | 지금 | 바꿀 것 |
|---|---|---|---|
| E1 | `PLAN.md:1` · 머리 판 줄(`:16` 다음) | 「(v2.15, 2026-11-26)」 | 다음 판 번호 · 날짜, 머리 인용에 M5u 한 줄(명세 `docs/m5u-workplan.md` · 결정 360~393) |
| E2 | `PLAN.md:571` 의존성 원칙 | 「런타임 의존성 0(…)」 | Q10 의 답 문장(추천: 「Python 패키지(wheel)는 런타임 의존성 0 그대로다. 원격 워커의 Rust 바이너리 `rcmw` 는 별도 산출물이고 `rust/Cargo.lock` 에 고정한 허용 목록의 크레이트만 정적으로 넣는다(M5u 결정 368 · 371)」) |
| E3 | `PLAN.md:35` 이식성 줄 | 「런타임 의존성 0.」 | 「(wheel 기준 — 워커 바이너리는 PLAN 「의존성 원칙」)」 덧붙임 |
| E4 | `PLAN.md` 「마일스톤과 완료 기준」 M5t-c 줄 다음 | 없음 | M5u 줄(계획 날짜 · 명세 · 결정 번호 360~393 · 한 줄 요약) |
| E5 | `PLAN.md:645` 결정 8 행 | 「Python 3.11+, 런타임 의존성 0 …」 | 행 끝에 「— 원격 워커 바이너리는 Rust(M5u 결정 371)」 |
| E6 | `AGENTS.md:8` | 「**Zero runtime dependencies.** Standard library only, server and client. …」 | Q10 (a) 면 §4.6 의 추천 문장(PR2) |
| E7 | `CONTRIBUTING.md:37` | 「**Runtime dependencies stay at zero.** The standard library only, on the server and the client.」 | Q10 (a) 면 같은 뜻의 문장(PR2) |

| PR | 브랜치(제안) | 내용 | 시작 조건 | 물결 | 함께 도는 PR | owns | shared | 머지 순서 | lane(추천 · 이유) |
|---|---|---|---|---|---|---|---|---|---|
| PR0 | `docs/m5u-rust-worker-runner-workplan` | 이 명세 + E1~E5(마지막 커밋) | — | 0 | — | `docs/m5u-workplan.md` | `PLAN.md` | 먼저 | — (명세 PR) |
| PR1 | `test/worker-golden-transcripts-and-vectors` | 와이어 · 디스크 골든과 녹화 도구(`TcpRelay` 의 `record_requests` 옵션 · `tests/worker_golden.py` 정규화 · `allowed.json` 의 `typed_values` · `rust_only_keys`, F65 · F68), 공유 벡터(`log_contract` 포함)와 그 Python 시험, 시험 선택기(`RCM_TEST_WORKER_IMPL`, 이 PR 에선 `python` 만 — `rust` 면 「PR4 전」 세션 오류), 골든의 개인정보 검사 시험, 변이 `m5u-golden-allows-any-key` | PR0 머지 | 1 | PR2 | `tests/golden/worker/`(새), `tests/vectors/worker/`(새), `tests/worker_golden.py`(새), `tests/test_worker_golden_m5u.py`(새), `tests/test_worker_vectors_m5u.py`(새), `tools/worker_golden.py`(새) | `tests/tcp_relay.py`(기록 옵션 — 덧붙이기만, 기본 꺼짐), `tests/conftest.py`(선택기 픽스처), `scripts/mutcheck.py`, `CHANGELOG.md` | PR2 와 순서 무관 | short · `src/` 0줄, 시험과 도구만. 다섯 조건 중 해당 없음 |
| PR2 | `feat/rust-workspace-and-job-supervisor` | `rust/` 작업 공간 · 툴체인 고정 · `Cargo.lock`, `rcmw supervise` 와 `rcmw --version`, pytest 래퍼(빌드 · cargo 시험 · 크레이트 허용 목록 · 빌드 잠금 `rcmw --version` · 릴리스 바이너리에 `RCM_TEST_` 0), 버전 글 잠금(세 값, cargo 없이 — `tests/test_rcmw_version_lock_m5u.py`, F66), `tests/test_rust_supervisor_m5u.py`(감독자 argv 계약 7개를 같은 단언으로 `rcmw supervise` 에 + 훅 2개의 cargo 시험 래퍼, F51 — `test_job_supervisor_m5tb.py` 는 0줄), 건너뛰기 규칙(§4.6), mutcheck `runner="cargo"` + `rs-supervisor-*` 5개 + `m5u-version-lock-skips-cargo-lock`, E6 · E7(Q10 a), `CONTRIBUTING.md` 「Releasing」 1단계 문장(세 값) | PR0 머지 · Q6 · Q7 · Q10 답 · 맥미니 툴체인 설치(오너) | 1 | PR1 | `rust/Cargo.toml`, `rust/Cargo.lock`, `rust/rust-toolchain.toml`, `rust/rcmw/`(새 — `Cargo.toml` · `src/lib.rs` · `src/main.rs` · `src/supervise.rs`), `tests/rust_tool.py`(새), `tests/test_rust_build_m5u.py`(새), `tests/test_rust_deps_m5u.py`(새), `tests/test_rust_supervisor_m5u.py`(새), `tests/test_rcmw_version_lock_m5u.py`(새) | `scripts/mutcheck.py`, `.gitignore`, `AGENTS.md`(`:8` · 검사 명령 줄), `CONTRIBUTING.md`(`:37` · 「Releasing」 1단계), `CHANGELOG.md` | PR1 과 순서 무관 | full · 신호(SIGTERM · SIGKILL · 부모 죽음 감시)와 프로세스 그룹 |
| PR3 | `feat/rust-runner-and-session-files` | Rust 러너(env · setsid · 신호 마스크 · fd · 펌프 · 64 KiB 자름 · 시간초과 · 취소 · 그룹 거두기 · `rcmw supervise` 로 감싸기 — `test_job_supervisor_m5tb.py` 의 러너 시험에 대응하는 cargo 시험), 도구 찾기 · CLT shim, 프로세스 신원, 세션 폴더 파일(flock · owner · claim · proc · ok_at · outbox · stream, fsync · rename), 로그 조각 — 라이브러리와 cargo 시험, 벡터 · 디스크 골든 대조 | PR1 · PR2 머지 | 2 | — | `rust/rcmw/src/runner.rs`, `tools.rs`, `ident.rs`, `session.rs`, `outbox.rs`, `chunk.rs`(새), `tests/test_rust_runner_m5u.py`(새), `tests/test_rust_session_m5u.py`(새) | `rust/rcmw/src/lib.rs`(`mod` 줄), `tests/vectors/worker/`(벡터 추가), `scripts/mutcheck.py`, `CHANGELOG.md` | PR2 뒤 | full · 신호 · 프로세스 그룹 · 동시성(락) |
| PR4 | `feat/rust-worker-protocol-loop` | `rcmw run`(넘기기 fd · 모르는 키 종료 2), HTTP 클라이언트(3xx 안 따름 · Q11), 등록 · 세션 · 펜싱 · heartbeat(`seq` · `claims_settled`) · claim · tree 자재화(tar.gz 거절 필터) · 러너 연결 · 로그 오프셋 보내기 · 디스크 큐 · finish · 감시 틱 자기 멈춤 · 닫힌 잡 · 죽은 세션 큐 · 신호, 기동 등록의 버전 409(따라가면 `restart` 로 앞단 exec, 아니면 종료 2 — RL13 · RL32 의 cargo), 다시 실행 장치(결정 393), Q9 (a) 의 `worker_impl` 보내기, Python 앞단(`implementation` · `rust_binary` 키와 env, 찾기 · 검사 · 넘기기 · exec argv 꼬리), 거절 관문(`_worker_config` 안 · 서버 찾기 앞, F64) · 세 집합 키 잠금 · 벡터 `handoff_keys`(결정 390), e2e 매트릭스 켜기(선택기 `rust` · `RUST_KNOB_TESTS` · `rust_pending.txt`), 「진짜 Rust」 확인 | PR3 머지 · Q2 · Q5 · Q9 · Q11 · Q13 답. M5t-c 와는 §4.10 의 표와 규칙 | 3 | — | `rust/rcmw/src/http.rs`, `client.rs`, `worker.rs`, `sender.rs`, `watchdog.rs`, `tree.rs`, `tar.rs`, `handoff.rs`, `restart.rs`(새), `src/remote_ci_monitor/worker_launch.py`(새), `tests/test_worker_launch_m5u.py`(새), `tests/test_e2e_worker_impl_m5u.py`(새), `tests/test_rust_worker_m5u.py`(새), `tests/rust_pending.txt`(새) | `rust/rcmw/src/lib.rs` · `main.rs`(`mod` · 하위 명령 줄), `src/remote_ci_monitor/cli.py`(`_worker_config` 의 관문 한 줄 · `cmd_worker` 분기 한 곳), `src/remote_ci_monitor/config.py`(키 둘 · env), `src/remote_ci_monitor/templates/worker.toml` · `examples/worker.toml`(같은 바이트, F59), `tests/conftest.py`(선택기 `rust` · `RUST_KNOB_TESTS`), `tests/test_e2e_worker.py`(`WorkerProc.start` · `kill` 의 확인), `tests/test_e2e_m5n.py`(`M5nWorker.kill` 의 확인), `tests/test_e2e_worker_outbox_m5tb.py`(`WRAPPER` 상수 한 줄), `tests/golden/worker/`(`allowed.json` 의 `rust_only_keys` · 필요하면 재녹화), `tests/vectors/worker/`(`handoff_keys.json` 등), `docs/configuration.md`(키 — 실험 표시), `scripts/mutcheck.py`, `CHANGELOG.md` | PR3 뒤. M5t-c PR3 과 함께 열려 있으면 M5t-c PR3 먼저(§4.10) | full · 와이어 · 동시성 · 재시작 · 신호 · 토큰 |
| PR5 | `feat/rust-worker-gitref-artifacts-host-drain` | git_ref(미러 · fetch · checkout · `token_file` · 그룹째 시한), 산출물 수집 · 번들 tar · 업로드 · 처분(F50), 호스트 사실 · 능력 보고 · 프로브 · 표본, 드레인 고리(새 claim 0 · 60초 큐 대기 · 300초 창 · `--once` · 고정 · 신호 — 다시 실행 자체는 PR4 의 장치), `rust_pending.txt` 에서 git_ref · 산출물 · 호스트 줄 지움 | PR4 머지 | 4 | — | `rust/rcmw/src/git.rs`, `collect.rs`, `host.rs`, `probe.rs`, `drain.rs`(새), `tests/test_rust_extras_m5u.py`(새) | `rust/rcmw/src/lib.rs` · `worker.rs` · `client.rs`(PR4 owns — 부르는 자리), `tests/rust_pending.txt`(줄 지움), `tests/vectors/worker/`, `scripts/mutcheck.py`, `CHANGELOG.md` | PR4 뒤 | full · 와이어(능력 · 표본) · 재시작(드레인) · 토큰(git 자격) |
| PR6 | `feat/server-serves-worker-binaries` | (Q3 (a) 일 때) `[server] worker_binaries_dir` · `GET /worker/binary/<target>`(없으면 404 `no_worker_binary`) · `SHA256SUMS`, 앞단의 바이너리 따라가기(캐시 0700 · 리다이렉트 안 따름 · 크기 상한 · sha256), Q4 의 길, 서버 `rcm check` 경고, RW21 · RL32 의 e2e(둘째 · 셋째 `test-knobs` 빌드를 `RCM_TEST_BUILD_VERSION` 으로, 워커 env 에서 `RCM_WORKER_RUST_BINARY` 를 뺌 — §4.1), m5q 6개 · RL12-b 의 Rust 대체 시험과 `RUST_REPLACED`, `rust_pending.txt` 비우기와 그 잠금, `release.yml` 의 `rcmw-build` · `rcmw-smoke` 잡과 `github-release` · `pypi` 의 `needs`(결정 391, Q3 의 빌드 자리 답에 따라), 변이 `m5u-release-ignores-rcmw-smoke`. Q3 이 (a) 가 아니면 이 PR 은 RW21 e2e(`rust_binary` 를 바꿔 끼움) · 대체 시험 · 비우기 · 빌드 잡만 남는다 | PR5 머지 · Q3 · Q4 · Q8 답 | 5 | PR7 | `src/remote_ci_monitor/worker_binaries.py`(새), `tests/test_worker_binaries_m5u.py`(새), `tests/test_e2e_rust_follow_m5u.py`(새), `tests/test_rust_smoke_m5u.py`(새) | `src/remote_ci_monitor/server.py`(라우트 한 곳), `src/remote_ci_monitor/config.py`(서버 키), `src/remote_ci_monitor/worker_launch.py`, `examples/server.toml` · `src/remote_ci_monitor/templates/server.toml`(주석 키, F59), `tests/conftest.py`(`RUST_REPLACED`), `tests/rust_pending.txt`(비움), `tests/test_e2e_worker_impl_m5u.py`, `tests/test_ci_release_workflow.py`, `.github/workflows/release.yml`, `docs/configuration.md`, `scripts/mutcheck.py`, `CHANGELOG.md` | PR7 보다 먼저. M5t-c PR1 · PR2 · PR3 과 함께 열려 있으면 그쪽 먼저(§4.10) | full · 와이어(새 라우트) · 보안(실행 파일을 내줌 · 받음) · 재시작(드레인 e2e) |
| PR7 | `feat/worker-impl-check-doctor-guard-docs` | `rcm worker --check` 의 구현 · 바이너리 행(경로 · 버전 · target · sha256 · 격리 속성 · 실행 비트 — 관문을 지난 설정에서만), doctor 의 구현 행, `tools/guard_production.py` 가 `rcmw` 를 알아봄, Q9 (a) 의 서버 쪽(등록 키 읽기 · 메모리 · 머신 문서 키 · `rcm machines` 의 「rust」/「—」), 문서(전환 · 되돌리기 · Local Network · 서명 · Rust 가 거절하는 설정 · 3xx), doc-lock 시험 | PR5 머지 | 5 | PR6 | `tests/test_docs_m5u.py`(새), `tests/test_guard_production_rcmw.py`(새), `tests/test_worker_impl_marker_m5u.py`(새) | `src/remote_ci_monitor/cli.py`(`_worker_check` · `_machine_detail`), `src/remote_ci_monitor/doctor.py`, `src/remote_ci_monitor/remote_workers.py`(등록 키 하나 읽기), `src/remote_ci_monitor/capabilities.py`(표식 메모리 · 문서 키), `tools/guard_production.py`, `docs/operating.md`, `docs/configuration.md`, `README.md` · `README.ko.md`(워커 절 한 줄, 같은 PR — F70), `scripts/mutcheck.py`, `CHANGELOG.md` | PR6 뒤. M5t-c PR2 · PR3 · PR4 · PR5 와 함께 열려 있으면 그쪽 먼저(§4.10) | full · 보안(가드 훅) · 와이어(머신 문서 키) |
| PR8 | `test/m5u-live-acceptance` | 실기: `m5tb_live.sh` 를 Rust 워커로(제품 env), RW29 의 launchd 전환(시험 라벨 · 시험 서버), 보고서 | PR6 · PR7 (· PR9) 머지 | 6 | — | `scripts/acceptance/m5u_live.sh`(새), `docs/acceptance/reports/<날짜>-m5u-acceptance.md`(새) | `scripts/acceptance/m5tb_live.sh`(구현 변수 한 곳) | 마지막 | short · `src/` 0줄, 실기 자체가 증명 |
| PR9 | `feat/rcmw-standalone-run` | (Q5 (b) 일 때) `rcmw run --config worker.toml`: Rust 가 설정을 읽고 같은 오류 문장(벡터 `config_errors` — TOML 문법 오류는 종료 2 와 `<경로>: ` 머리만 같고 글은 다를 수 있음을 허용 차이로) · env 토큰 · 거절 목록(결정 390, `auto` 포함) · 자기 따라가기, 서비스 예시, doctor 의 `rcmw` 서비스 해석, TOML(Q16) | PR5 · PR7 머지 · Q16 답 | 5′ | — | `rust/rcmw/src/config.rs`, `follow.rs`(새), `examples/launchd/com.remote-ci-monitor.rcmw.plist`(새), `examples/systemd/rcmw.service`(새), `tests/test_rust_standalone_m5u.py`(새) | `rust/rcmw/src/lib.rs` · `main.rs`, `rust/rcmw/Cargo.toml` · `rust/Cargo.lock`(Q16 (a) 의 `toml`), `tests/test_rust_deps_m5u.py`(허용 목록), `src/remote_ci_monitor/doctor.py`(`rcm_launch`), `tools/guard_production.py`, `tests/vectors/worker/`, `docs/operating.md`, `scripts/mutcheck.py`, `CHANGELOG.md` | PR7 뒤, PR8 앞. M5t-c PR3 · PR5 와 함께 열려 있으면 그쪽 먼저(§4.10) | full · 보안(토큰 · 설정 파일 권한) · 재시작(자기 따라가기) |
| 릴리스 | — | 버전은 메인 세션이 정한다 → dev → main(§8) → 태그(RW33 의 스모크). 기본 구현 전환 없음(Q12) | PR8 | — | — | — | — | — | — |

같은 물결 안에서 같은 파일을 owns 로 가진 PR 은 없다. 물결 5 의 PR6 과 PR7 은 `docs/configuration.md` ·
`scripts/mutcheck.py` · `CHANGELOG.md` 를 shared 로 나눠 쓰고 PR6, PR7 순서로 머지한다. 묶음 경로가 겹치지 않는지는
`tools/dev_batch.py` 로 본다.

PR4 와 PR6 은 `worker_launch.py` 와 `tests/test_e2e_worker_impl_m5u.py` 를 나눠 쓴다. PR4 가 owns 로, PR6 이 shared 로
가진다. PR4 와 PR5 는 `worker.rs` 와 `client.rs` 를 나눠 쓴다. PR4 가 owns 로, PR5 가 shared 로 가진다. 두 짝 모두 물결이
다르다. M5t-c 와의 겹침(PR4 · PR6 · PR7 · PR9)과 순서는 §4.10 의 표에 있다.

어느 PR 도 `remote_worker.py` · `runner.py` · `job_supervisor.py` · `hostsample.py` 의 동작을 바꾸지 않는다. 「Python 구현을
남긴 채」가 이 뜻이다. 구현 표식도 Python 워커를 고치지 않는다(F51 · F52). PR4 의 `cli.py` 분기는
`implementation = "rust"` 일 때만 탄다.

## 6. 진행 체크리스트

| # | 할 일 | PR | 의존 | 상태 |
|---|---|---|---|---|
| 0 | 명세 작성(1판 · 2판) · 시나리오 추적 | PR0 | — | [ ] |
| 1 | 격리 반박 라운드(HIGH 없을 때까지) · §12 반영 | PR0 | 0 | [ ] |
| 2 | 결정 페이지: Q1~Q13 · Q16~Q18 답(Q14 는 답함, F47~F49) | — | 1 | [ ] |
| 3 | PR0 마지막 커밋 E1~E5 · 머지 | PR0 | 2 | [ ] |
| 4 | 맥미니 툴체인 설치 · 운영 프리셋 `rcm-ci` env(`RCM_REQUIRE_RUST` · `CARGO_TARGET_DIR` · PATH · `requires`)(오너) | — | 2 | [ ] |
| 5 | PR1 골든 · 벡터(`log_contract` 포함) · 선택기 | PR1 | 3 | [ ] |
| 6 | PR2 작업 공간 · 감독자 · cargo 래퍼 · 변이 러너 · E6 · E7. rcm-ci 잡 로그에서 cargo 시험 수 > 0 · 빌드 시간 · 크기 기록(§3.6) | PR2 | 3 · 4 | [ ] |
| 7 | PR3 러너 · 세션 파일 | PR3 | 5 · 6 | [ ] |
| 8 | M5t-c 와의 겹침(§4.10 표): 함께 열린 짝은 M5t-c 먼저 머지했는지, 나중 쪽이 골든 재녹화 · `RUST_REFUSED_KEYS` 를 맞췄는지(§4.10 의 세 경우), §13 문장을 해당 PR 에 적었는지(메인 세션) | — | 5 | [ ] |
| 9 | PR4 워커 고리 · 409 · 다시 실행 · 앞단 · 거절 목록 · e2e 매트릭스. Rust e2e 시간 기록 | PR4 | 7 | [ ] |
| 10 | PR5 git_ref · 산출물 · 호스트 · 드레인 고리 | PR5 | 9 | [ ] |
| 11 | PR6 바이너리 서빙 · 따라가기 · 드레인 e2e · 대체 시험 · `rust_pending.txt` 0 · `release.yml` 빌드와 스모크 | PR6 | 10 | [ ] |
| 12 | PR7 진단 · 가드 · 표식 · 문서 | PR7 | 10 | [ ] |
| 13 | (Q5 (b)) PR9 독립 실행 | PR9 | 10 · 12 | [ ] |
| 14 | RL 대응표(§7.4) 빈 칸 0 · Rust 변이 대응표(§7.5) 생존 0 확인 | PR2~PR6 | — | [ ] |
| 15 | Linux 증거 표(Q8 의 답대로) — 증거 없는 target 은 배포 목록에서 뺌 | PR6 · PR8 | 11 | [ ] |
| 16 | PR8 실기 · 보고서 | PR8 | 11 · 12 (· 13) | [ ] |
| 17 | CHANGELOG `[Unreleased]` 정리 | 각 PR · 릴리스 | — | [ ] |
| 18 | 릴리스 PR dev→main 개인정보 점검(§8) · 태그 · 태그 잡 로그의 스모크 sha256 = 자산 sha256 확인(RW33) | 릴리스 | 16 · 17 | [ ] |
| 19 | 운영 반영(큐가 빌 때 재시작). 어느 워커를 `implementation = "rust"` 로 돌릴지는 오너 | 운영 반영 | 18 | [ ] |

## 7. 시험 계획

### 7.1 시나리오 표

scenario-author 가 37e7b98 스냅숏으로 쓴 표(`scenarios-RW.md`)에서 지었다. 줄 번호는 d07b253 에서 다시 맞췄고 바뀐 곳은
없었다(§3 머리). PR 칸을 채웠고 미정 칸은 이 명세의 질문 번호(Q)로 바꿨다. 1판에서 RW26 과 RW27 을 잰 사실로 고쳤고 RW31
을 더했다(§12 의 S 표). 1라운드에서는 RW32~RW35 를 더했다(§12 의 1라운드 표). 2라운드에서는 RW10 · RW20 · RW21 · RW26 ·
RW32 · RW33 의 글을 고치고 RW12 의 PR 칸을 나눴다(§12 의 2라운드 표). 새 행은 없다.

근거 칸의 F 번호에 문서 이름이 없으면 이 명세 §3 의 사실이다. 「m5tb F59」처럼 이름이 붙은 것은 `docs/m5tb-workplan.md`
§3 의 사실이다. scenario-author 가 그 문서에서 옮겨 왔다.

한 행에 (a)(b) 같은 결과가 여럿인 행은 scenario-author 의 꼴을 지켰다. 그런 행의 시험은 `# RW4` 처럼 태그를 달고 하위
글자를 주석으로 함께 적는다(§12 S-1).

<!-- scenario-prefix: RW -->
| ID | 시나리오 | 확인할 결과 | 어디서 | PR |
|---|---|---|---|---|
| RW1 | [축4·3 · 고아 신원 · RL15 · RL31] 표식 프리셋 잡이 도는 중 Python 워커를 `kill -9` 하고 같은 data_dir 로 Rust 워커를 띄운다(반대 방향도). macOS(`ps -o lstart=`, 한 자리 날짜는 공백 두 칸 `Nov  6`, 서비스 `LC_ALL` 이 한국어)와 Linux(`/proc/<pid>/stat` 의 comm 에 `)` 와 공백)에서 각각. pid 재사용(같은 pid, 다른 시작 시각)과 형식이 틀린 start 문자열도 넣는다 | 양쪽이 서로의 `proc.json` 의 `start`(`linux:<n>` 또는 `ps:<lstart>`)를 같은 프로세스로 읽는다. 산 그룹은 SIGTERM, grace, SIGKILL 로 pid 가 사라지고 `previous_sessions` 에 오른다. 재사용된 pid(시작 시각이 다름)는 건드리지 않는다. 형식이 틀린 값, `ps` 실패, 리더만 죽고 멤버가 산 그룹, `pgid` null 은 「모름」(`unverified_sessions`)이고 「재사용이라 확인됨」이 아니다. 산 그룹이 확인됨으로 읽혀 홀드가 풀리면 같은 일이 두 번 도는 길이라 그 경로는 0건이어야 한다. Linux 실행 증거는 Q8 의 답을 따른다. 근거: 결정 268 · 327 · m5tb F59 · m5tb F71 · F18 · `remote_worker.py:1075-1132` | 단위(벡터 `ident` 를 두 구현이 같은 입력으로 먹고 같은 판정) + e2e(macOS 교차 kill -9: pid 생존, 등록 본문, 워커 로그) | PR3 (단위) · PR4 (e2e) |
| RW2 | [축4·3 · 세션 폴더 락 · 주인 · RL38 · RL15] (a) Python 워커 A 가 산 채(세션 폴더 `lock` 을 쥠) 같은 data_dir 로 Rust 워커 B 를 시작한다(반대도). (b) A 를 `kill -9` 했는데 A 잡의 자손(감독자·명령·그 자식)이 산 채다. (c) 서버 주소·`server_name`·토큰 이름이 다른 두 설정이 한 data_dir 를 쓴다 | (a) B 는 `flock` 으로 A 폴더를 잠그지 못해 건너뛴다: A 그룹 pid 생존, A 폴더 파일 바이트 그대로, B 서버로 간 요청에 A 세션 id 0건. POSIX 락(`fcntl` F_SETLK)을 쓰면 Python 의 `flock` 과 서로 안 보여 산 세션을 죽은 것으로 읽고 도는 잡의 그룹을 죽이므로 `flock` 호환을 시험으로 못 박는다. (b) `lock`·`outbox.json`·`stream.log` 의 fd 는 `O_CLOEXEC` 라 자손의 fd 목록(macOS `lsof -p`, Linux `/proc/<pid>/fd`)에 없고, 다음 워커가 D 를 잠가 고아를 판정한다(락이 새서 영영 「산 세션」으로 남는 일 0). (c) `owner.json` 의 `server_sha256`(`<server 의 끝 / 와 공백을 뺀 값>\n<server_name>` 의 sha256, 자동 발견이면 `auto`)와 `token_name` 을 양쪽이 같게 계산하고, 주인이 다른 폴더는 잠가 보지도 지우지도 않는다. 근거: `remote_worker.py:1214-1235` | 단위(벡터 `owner_sha`, 두 설정 한 폴더, Python 이 쥔 flock 을 Rust 가 못 잡음) + e2e(구현 교차, 자손 fd 검사) | PR3 (단위) · PR4 (e2e) |
| RW3 | [축4 · 디스크 큐와 세션 폴더 파일 호환 · RL14 · RL16 · RL19 · RL22] Python 이 `outbox.json` + `stream.log` 를 남기고 죽은 뒤 Rust 가 이어 보낸다(반대도). 상태: `acked`>0, finish 를 썼으나 서버가 못 받음, 산출물 묶음 `pending`, `broken` true, `ended_at` 실수, 비ASCII summary, 끝난 지 24시간 넘음, 빈 파일·잘린 JSON, 스풀이 `written` 보다 짧음. `claim.json`·`proc.json`·`owner.json`·`ok_at.json` 도 같은 방식으로 | `outbox.json` 아홉 키(`acked` `phase` `finish` `ended_at` `bundle` `log_stopped` `done` `broken` `length`)의 뜻과 타입을 양쪽이 같게 읽고 쓴다(Python `json.dumps(sort_keys=True)` 의 `\uXXXX` 이스케이프와 실수 `ended_at` 를 Rust 가 읽고, 같은 상태에서 같은 바이트를 쓴다 — 디스크 골든). `ok_at.json` 의 `wall`(Python `isoformat()` 의 `+00:00` 과 마이크로초)을 읽고 없으면 `lock` 의 mtime 으로 `previous_sessions` 순서를 정한다. 이어 보내기는 첫 로그 요청이 빈 본문 오프셋 질의, 요청 본문 바이트 합이 남은 바이트, 서버 `log.txt` sha256 이 스풀과 같고(중복·빈틈 0), finish 는 한 번(`job_finished` 이벤트 1회, 알림 훅 1회)이다. 쓰기는 임시 파일, fsync, rename, 폴더 fsync 라 쓰는 도중 `kill -9` 를 되풀이해도 반쯤 쓴 파일이 안 보인다. 못 읽는 큐는 지우지 않고 `unreadable-<D>-<id>/` 로 옮기며 로그 한 줄, 잡은 `worker_restarted_without_job`. 근거: 결정 271 · 326 · 337, m5tb §4.4 | 단위(디스크 골든 표) + e2e(구현 교차 × 쓰는 도중 kill -9) | PR3 (단위) · PR4 (e2e) |
| RW4 | [축2·3 · 표식 잡 시작 · RL41 · RL9 · 결정 333] 표식 프리셋에서 (a) 준비(큰 스냅샷·git_ref)가 길고 분단이 `G − m` 을 넘긴다 (b) Popen 직전 확인과 Popen 사이에 문턱 (c) `proc.json` 을 쓸 수 없다(읽기 전용·디스크 가득) (d) argv[0] 없음·실행 권한 없음 (e) 그룹이 사라졌는지 확인하지 못한 채 끝남. 일반 프리셋은 (c)만 비교 | (a) 명령 프로세스가 한 번도 뜨지 않고(표지 파일 0, pid 0) `lost` + `worker_self_stopped`(종료 3), finish 의 `group_gone: true`. (b) Popen 직후 첫 점검이 곧바로 끊어 그룹 소멸이 플래그 + `grace_J` + 4초 안이다. (c) 표식 잡은 띄우지 않고 `launch_failed`, `group_gone: true`. 일반 잡은 로그 한 줄 뒤 띄운다. `proc.json` 은 띄우기 직전 `{pgid: null}`, 띄운 뒤 `{pgid, start}` 로 두 번 쓰고 파일이 없으면 「띄운 적 없음」이다. (d) 감독자가 있든 없든 같은 코드(`launch_executable_missing`·`launch_permission_denied`). (e) finish 에 `group_gone` 키를 보내지 않는다(거짓을 보내지 않음). 서버가 홀드하고 그 폴더를 남겨 다음 프로세스가 다시 판정한다. 근거: 결정 330 · 333 · m5tb F65 · F16 · 되돌릴 수 없는 결과(두 번 실행) | 단위(느린 자재화 대역, Popen 훅 — `#[cfg(test)]`) + e2e(느린 스냅샷을 중계가 붙잡음: 표지 파일, pid, finish 본문) | PR4 |
| RW5 | [축2·3 · 자기 멈춤 시계 · RL1 · RL2 · RL26 · RL34] 표식 잡이 도는 중 (a) heartbeat 가 요청 기한 15초까지 매달리는 분단 (b) 서버 정지 후 재기동 (c) 머신이 잠들었다 G 보다 오래 뒤에 깸(벽시계 +G+α, 단조 시계는 거의 정지) (d) 연결은 되는데 heartbeat 가 14초 걸림 또는 5xx (e) SIGTERM 뒤 벽시계가 1시간 뒤로 감. 일반 프리셋 동일 | 판정은 감시 스레드가 1초 틱으로 하고 `wgap = max(벽 경과, 단조 경과)` 를 쓴다(heartbeat 루프가 막혀도). 표식 잡은 `ok_at + G − m`(`m` = 15 + `grace_J` + 10) 뒤 첫 틱에 그룹이 사라지고, 서버가 닫을 수 있는 가장 이른 시각(`heard + G`)보다 앞이며, `lost` + `worker_self_stopped`(종료 3, `cancelled` 아님)이다. (c) 깬 직후 첫 틱에 걸린다(Rust `Instant` 만 쓰면 잠든 시간을 못 세므로 벽시계도 쓴다). (d) 연결된 동안은 안 걸리고 5xx 는 `G − m` 에 멈춘다. (e) SIGKILL 은 단조 시계로 `grace_J` + 2초. `ok_at` 은 요청을 보낸 시각의 최댓값이다. heartbeat 한 회차는 전체 15초 기한이고 매달린 도우미가 4개면 그 회차를 건너뛰고 실패로 센다. 일반 잡은 죽이지 않는다. 근거: 결정 253 · 320 · 335 · `remote_worker.py:113-119` | 단위(벽·단조 시계를 따로 주입하는 Rust 시계 추상화, `#[cfg(test)]`) + e2e(TCP 중계 정지, `os.kill(pid,0)`, DB, 종료 코드) | PR4 |
| RW6 | [축3 · 감독자 · RL43 · RL51 · RL52] 표식 잡에서 (a) 워커를 `kill -9`(명령이 SIGTERM 을 따름·무시함·Popen 직후 감독자가 부모를 확인하기 전) (b) 감독자만 `kill -9` (c) 시작 확인에서 부모 pid 불일치·이미 SIGTERM (d) 명령 종료 코드 7·125·SIGKILL (e) 명령은 SIGTERM 에 끝나고 SIGTERM 을 무시하는 자식이 남음 (f) 잡 env 가 감독자의 동작을 바꾸려 함(로더 환경 변수 `DYLD_*`·`LD_*`, `RCM_*`) | (a) 그룹이 0.25초 + ε 안에(무시하면 0.31초 + `grace_J` 안에) 사라지고, Popen 직후라면 명령 0회. (b) 러너가 리더 끝을 보고 남은 그룹을 거둔다: `failed`, exit −9, `group_gone: true`, 홀드 0. (c) 명령 0회, 종료 125 와 잡 로그 첫 줄 `[rcm] supervisor: not started: parent_gone`(또는 `terminated`). (d) 종료 코드는 그대로 7·125(감독자 실패로 둔갑 안 함), SIGKILL 이면 −9. (e) 남은 자식이 첫 SIGTERM + `grace_J` + 0.5초 안에 사라진다(리더 끝부터 재서 두 배가 되지 않는다). (f) 감독자는 잡 env 를 명령에 그대로 넘기되 자기 동작은 바꾸지 못하고, 시작 실패는 표지 + 125 로만 말한다. 시험 훅은 명령줄로 닿지 않는다. `tests/test_job_supervisor_m5tb.py` 는 0줄 바뀐다. 근거: 결정 331 · 339-c · m5tb F78 · m5tb F83 · m5tb F84 · F4 · F5 · F51 · `job_supervisor.py:28-32` · 되돌릴 수 없는 결과(아무도 끊지 않는 고아) | pytest(새 `tests/test_rust_supervisor_m5u.py` 가 `test_job_supervisor_m5tb.py` 의 argv 계약 시험 7개 — `run_sup` · `spawn_parent` 꼴, `:130` · `:145` · `:156` · `:164` · `:174` · `:242` · `:264` — 를 같은 단언으로 `[rcmw, "supervise", …]` 에 돌림: pid 생존 시각, 표지 바이트, 종료 코드) + cargo(훅 2개 `before_check` · `before_popen` 은 `#[cfg(test)]` 의 `rl52_a_…` · `rl51_a_…`, 래퍼가 N > 0 단언) + (b) 와 러너가 감싸는 쪽은 Rust 러너의 cargo 시험 | PR2 · PR3 ((b) · 러너 쪽) |
| RW7 | [축3 · 잡 프로세스가 시작하는 상태 · 비밀] Rust 워커가 띄운 잡 프로세스(일반·표식). 워커는 `RCM_WORKER_TOKEN` 과 서비스 PATH 를 환경으로 받았고 프리셋은 `env_passthrough = ["PATH","HOME"]` | env 는 `build_env` 의 순서 그대로다: passthrough, `preset.env`, `RCM_JOB_ID` `RCM_PRESET` `RCM_REQUESTER` `RCM_SOURCE_MODE` `RCM_REF` `RCM_BASE_SHA` `RCM_DIRTY` `RCM_WORKSPACE` `RCM_LOG_FILE`, `RCM_INPUT_*`. 그 밖의 변수는 0개다(`RCM_WORKER_TOKEN`·`RCM_SERVER`·`RCM_WORKER_IMPLEMENTATION`·워커의 환경이 새지 않는다 — 환경을 비우고 시작해야 하며 Rust `Command` 의 상속 기본값에 기대지 않는다). cwd 는 워크스페이스, stdin 은 `/dev/null`, stdout·stderr 는 한 파이프, 새 세션·새 그룹의 리더(pid = pgid = sid), 신호 마스크는 비어 있고 SIGPIPE·SIGINT·SIGTERM 은 기본 처분(`SigBlk`·`SigIgn` 0), 3번 이상의 fd 는 0개다(넘기기 fd 포함). 잡이 `env` 를 찍으면 토큰이 서버 `log.txt`·`rcm logs` 에 0건이다. 근거: `runner.py:311-332` · `start_new_session` · 비밀 노출은 되돌릴 수 없는 결과 | 단위(Rust 러너가 띄운 프로세스가 자기 상태를 찍음, cargo) + e2e(`env`·`ps -o pid,pgid,sess`·fd 목록을 찍는 프리셋) | PR3 (단위) · PR4 (e2e) |
| RW8 | [축3 · 러너의 종료 규칙 · RL6 · RL43] 원격 잡 (a) 성공·실패(exit 3)·시간초과·취소·자기 멈춤·시그널로 죽음(SIGKILL·SIGSEGV) (b) 취소 요청이 SIGTERM 을 무시하는 명령, 손자가 파이프를 쥔 채 남음 (c) 개행 없는 출력으로 읽기가 막힌 명령의 시간초과 (d) 빈 argv·없는 실행 파일 | 종료 코드 계약 그대로: 성공 0, 실패 1, 시간초과 2, 취소 2, lost 3. finish 의 `exit_code` 는 시그널이면 음수(−9·−11 — Rust 의 `code()` 가 `None` 인 경우를 그대로 두지 않는다). 우선순위는 lost > cancelled > timed_out > rc. 첫 점검은 Popen 직후 곧바로, 이후 1초 박자. 시간초과는 벽시계, SIGTERM 에서 SIGKILL 까지의 간격은 단조 시계다. 신호는 프로세스 그룹 전체(손자까지)에 가고, KILL 뒤 손자가 파이프를 쥐고 있어도 더 기다리지 않는다. 시간초과 요약은 `format_limit` 문구, 빈 argv 는 `preset_missing`. 근거: `runner.py` · `remote_worker.py:2779` `_result_outcome` · 종료 코드 계약 | 단위(벡터 `exit_rules`, Rust 러너) + e2e(wait JSON, 종료 코드, finish 본문 `exit_code`) | PR3 (단위) · PR4 (e2e) |
| RW9 | [축1·2 · 로그 바이트와 조각 · RL16 · RL17 · RL18 · RL20] 잡이 (a) 잘못된 UTF-8·NUL·`\r` 진행 표시 (b) 개행 없는 마지막 줄 (c) 개행 없이 64 KiB 를 넘는 한 줄(수백 KiB) (d) 분단 중 12 MiB (e) 서버가 아는 릴리스 비밀이 조각 경계 근처 (f) 워커 스풀 디스크 가득 | 서버 `log.txt` sha256 이 같은 잡을 Python 워커가 보냈을 때와 같다(가릴 값이 없을 때). 바이트로만 다루고 문자열로 바꾸지 않는다. 오늘 동작 그대로: 마지막 줄에 `\n` 을 붙이고, 개행 없이 64 KiB 를 넘으면 그 자리에 `\n` 을 끼워 흘린다. 조각은 `acked` 부터 256 KiB 이하이며 마지막 `\n` 에서 자르고(줄 끝이 없으면 4 MiB 까지, 잡이 끝났거나 D 큐면 꼬리 그대로) 오프셋은 가리기 전 원본 바이트 기준이다. 한 값이 두 요청에 걸치지 않아 비밀 원문이 `log.txt`·`rcm logs`·SSE·웹에 0건이다(워커는 가리지 않고 서버가 가린다). 스풀을 못 쓰면 `log_complete=false`(finish 의 `log_bytes` 는 스트림 전체 바이트)이고 앞이 잘린 로그를 완전하다고 말하지 않는다. 근거: 결정 271 · 273 · m5tb F14 · m5tb F34 · m5tb F35 · F22 · F16 · `remote_worker.py:77-80` · 되돌릴 수 없는 결과(비밀 노출) | 단위(벡터 `chunk`) + e2e(sha256, 비밀 표지 검색. RL18 은 `RUST_KNOB_TESTS` 에 들어 `test-knobs` 바이너리로 조각 200바이트 — `WRAPPER` 가 `RCM_TEST_LOG_BATCH_BYTES` 를 넘김) | PR3 (단위) · PR4 (e2e) |
| RW10 | [축1 · 와이어 전사 비교] 같은 스크립트 잡 묶음(성공·실패·취소·시간초과·산출물·분단·서버 재시작·펜싱·죽은 세션 큐·git_ref)을 Python 워커와 Rust 워커가 같은 서버 코드에 돌리고 기록 중계(`TcpRelay(…, record_requests=True)`)가 요청을 모두 적는다. 골든을 녹화한 머신 · 버전과 다른 머신 · 다음 버전에서도 대조한다 | 요청마다 메서드·경로·헤더 이름 집합·JSON 키 집합·순서가 커밋된 골든(`tests/golden/worker/`)과 같고 허용 차이는 `allowed.json` 으로 못 박는다. 값을 안 보고 타입만 보는 자리(`typed_values`): `User-Agent` 의 `rcm/<버전>` 꼬리, 세션 id 값, 시각, 등록의 `version` · `host_name` · `host` 아홉 값(키 이름 · 순서는 `HOST_KEYS` 로 대조), heartbeat 의 `host_sample` 값, `/worker/capabilities` 의 잰 값. 대신 등록 `version` 은 도는 빌드의 버전과 같다고 따로 단언한다(Python `__version__`, Rust `rcmw --version`). Rust 만 보내는 등록 키 `worker_impl` 은 `rust_only_keys` 에 이름으로(Q9 (a) 일 때). 못 박을 것: 세션이 있는 모든 POST 본문의 `worker_session_id`(32자 소문자 hex, CSPRNG 로 만듦), 본문 없는 라우트(`GET …/tree`·`PUT/GET …/artifacts`·로그)의 `X-RCM-Worker-Session` 헤더(`GET /worker/spec`·`/api/health`·`/api/whoami` 는 세션 없음), 로그의 `X-RCM-Log-Offset`, 등록의 `worker_protocol: 2`·`previous_sessions`·`unverified_sessions`(각 64개 이하, 오래된 `ok_at` 순)·`host`, heartbeat 의 `jobs`(정렬)·`seq`·`claims_settled`·`host_sample`(낡으면 키째 안 보냄), claim 의 `lane`·`wait_seconds`·`claim_seq`, finish 의 `outcome`·`exit_code`·`log_bytes`·`group_gone`(모르면 키 없음)·`summary_code`/`summary_args`·`artifacts`·`finished_at`(`…Z`). Python 워커도 같은 골든과 같다(골든이 낡으면 빨강, 버전을 올리거나 다른 머신에서 돌려도 초록). Python 워커의 와이어를 바꾸는 PR(M5t-c PR3 포함)은 같은 PR 에서 다시 녹화한다(결정 379 · §4.10). 서버의 `workers`·`worker_sessions` 행과 `/api/status` 의 워커 카드가 같다. 근거: `client.py:91-146` `WorkerClient` · `remote_workers.py` · F65 · F68 | e2e + 전사 비교(골든은 PR1 이 Python 으로 녹화, PR4 · PR5 가 Rust 를 대조) | PR1 (골든) · PR4 · PR5 (Rust 대조) |
| RW11 | [축1·2 · 응답 표 · RL10 · RL11 · RL16 · RL21 · RL25] 가짜 서버가 요청마다 응답을 바꾼다: 401·403·400·411·413·503(`log_write_failed`)·502, 409 의 여러 코드(`session_fenced`·`not_registered`·`log_gap` + `expected_offset`·`protocol_required`·`job_closed` + `late_result_recorded`/`hold_released`·코드 없는 옛 409·버전 409 + `server_version`), 3xx(301·302·307·308, 다른 호스트의 `Location`), 2xx 인데 JSON 이 아님, 연결 거부, 시한 초과 | 같은 표(벡터 `refused`)를 Python 워커와 Rust 워커에 먹여 (종료 코드, 다음 요청, 큐 파일, 로그 한 줄)이 같다. `session_fenced` 는 종료 0 + 좁은 확인 finish(`lost` + `worker_replaced`)이고 되살림 핑퐁이 없다. `not_registered` 는 같은 세션 id 로 재등록(멱등)이고 죽은 세션 D 의 큐면 D 로 재등록하지 않고 그 큐를 지운다. `log_gap` 은 `expected_offset` 부터 다시 보낸다(프로세스 유지). 등록의 `protocol_required` 는 종료 3, 다른 요청의 것은 큐를 남기고 보내기만 멈춘다. 401·403 은 종료 2 와 토큰 안내 문장. 그 밖의 4xx 는 그 항목을 버리고 한 줄. 5xx·연결 실패는 1초부터 두 배씩 30초까지 백오프(도는 잡은 횟수로 포기하지 않음). 2xx 인데 JSON 이 아니면 502 로 보고 다시 시도한다(스레드가 죽지 않음). 3xx 만 두 구현이 다르고 벡터에 기대를 따로 적는다: Python 은 따르고(F55), Rust 는 따르지 않아 `Location` 의 호스트로 요청 0건(토큰이 새지 않음)이며 5xx 처럼 백오프하고 1분에 한 번 `server answered <code> — rcmw does not follow redirects; set server to the final URL`(Q11 · 결정 377). 근거: `remote_worker.py:873` `_refused` · F55 | 단위(표 구동 가짜 서버, 두 구현이 같은 표) | PR4 |
| RW12 | [축1 · HTTP 연결 규칙] 서버는 HTTP/1.1, 요청에 `Content-Length` 필수(chunked 는 411), JSON 본문 64 KiB 상한, 소켓 시한 일반 10초·업로드 60초, 동시 요청 상한 초과는 503 이다. Rust 클라이언트가 (a) 30초 넘게 쉰 뒤 첫 heartbeat·claim·로그 POST (b) 수 GiB 산출물 PUT·스냅샷 GET (c) 레인 48개가 빈 204 를 되풀이 (d) 소켓 시한 안쪽으로 조금씩 흘리는 서버 | (a) 요청마다 새 연결 + `Connection: close` 라(결정 387) 서버가 닫은 연결을 다시 쓰는 거짓 실패 0건: 워커 로그의 `server unreachable` 0줄, 표식 잡 자기 멈춤 0건, 서버가 받은 요청 수와 워커가 보낸 수가 같다(중복 전송 0). (b) 본문은 `Content-Length` 로 보내(411 없음) 메모리에 통째로 올리지 않고, 스냅샷은 `.part` 에 받아 길이를 확인한 뒤 바꾼다(잘리면 `snapshot_download_failed`, `.part` 0). (c) 빈 204 사이 쉬는 시간이 1초 이상 2초 미만의 지터라 같은 격자에 묶이지 않고 48레인에서 `/api/status` 503 이 안 난다. (d) 요청 기한이 Python 과 같다(일반 15초, 로그 60초, 산출물 300초, claim 은 `wait_seconds` 를 더함). 토큰은 `Authorization: Bearer` 로만. 근거: `server.py:5741` · `client.py:61` · M5f §15-C4 | e2e(중계로 요청 수, RSS) + 단위 | PR4 ((a)(c)(d) · (b) 의 스냅샷 GET) · PR5 ((b) 의 산출물 PUT) |
| RW13 | [축2 · heartbeat 순서 · RL50 · RL9 · 결정 339-a] 표식 잡 X 가 claim 된 직후 응답을 받기 전에 찍은 heartbeat A(X 없음)를 중계가 15초 넘게 붙잡고 B(X 있음)가 먼저 닿는다. 별도로 `seq` 를 3, 5, 4 순으로 보내고, `claims_settled` 가 X 의 `claim_seq` 보다 작은 heartbeat 와 claim 응답 유실도 낸다 | X 는 `running` 이고 프로세스가 산다(「잊힌 잡」 `lost` 0). 같은 그룹의 둘째 잡은 claim 되지 않아 두 프로세스가 같이 산 구간 0. 워커는 이미 적용한 `seq` 이하 응답의 `cancel`·`paused` 를 적용하지 않는다. `jobs` 목록·`seq`·`claims_settled` 는 한 잠금 구간에서 찍고, claim 이 끝난(응답·204·오류·시한) 뒤에야 `claims_settled` 에 반영한다. 근거: 결정 335 · 339-a · 339-b · 되돌릴 수 없는 결과(두 번 실행) | 단위(HTTP `seq`·`claims_settled` 조합) + e2e(중계가 요청 하나를 붙잡았다 놓음, pid) | PR4 |
| RW14 | [축2 · 죽은 세션 큐와 재전송 창 · RL14 · RL19 · RL40 · RL46] 재시작한 워커가 등록 전에 D 의 큐를 D 의 세션 id 로 보내는데 (a) 서버가 계속 5xx (b) 5xx 와 연결 실패가 섞임 (c) 끝난 지 300초 뒤에도 로그 단계가 5xx (d) 24시간 넘게 분단 (e) 벽시계가 뒤로 감 | (a) 서버가 응답한 시간이 단조 시계로 300초가 되면 큐를 디스크에 둔 채 등록하고 claim 한다(그 전에 register 0건). 로그 한 줄, 그 잡은 `worker_restarted_without_job`(D 고아가 확인됨이라 홀드 0). 빚진 finish 를 한 번도 못 보냈으면 최대 60초(30 + 2×15)만 더 기다린다. (b) 연결 실패 시간은 세지 않는다. (c) 끝난 지 300초 뒤 finish 가 먼저 가서 잡이 결과대로 확정되고 `log_complete=false`, 남은 로그·산출물은 안 간다. (d) 24시간 뒤 `log_bytes` 를 실은 finish 만 가고 확정된다. (e) 벽시계가 뒤로 가도 대기가 끝난다. 도는 잡에는 횟수 상한이 없다. D 큐의 2xx 가 새 세션의 `ok_at` 을 올리지 않는다. 근거: 결정 264 · 324 · 337 · `remote_worker.py:169-186` | 단위(시계 주입 + 라우트별 응답 주입, `#[cfg(test)]`), e2e(서버 로그 쓰기 실패 주입) | PR4 |
| RW15 | [축2 · 펜싱과 닫힌 잡 · RL5 · RL8 · RL10 · RL12 · RL21 · RL35] (a) 같은 토큰의 프로세스 B 가 뜨자 A 가 `session_fenced`(A·B 의 구현을 Rust·Python 으로 모두 섞음) (b) 일반 프리셋 원격 잡이 G 를 넘겨 서버가 `lost` 로 닫음 (c) 취소 요청이 있던 일반 잡·표식 잡 (d) 서버가 `succeeded` 등을 이미 기록했고 2xx 만 잃음 (e) 같은 프로세스의 다른 레인이 `not_registered` | (a) A 는 그룹을 TERM, KILL 로 멈추고 좁은 확인 finish(`lost` + `worker_replaced` + `group_gone`) 뒤 종료 0 이고, launchd `SuccessfulExit=false`·systemd `Restart=on-failure` 가 되살려도 1분 안 `register` 가 2번 이하(핑퐁 0). (b) 일반 잡은 죽지 않고 계속 돌며(로그 전송만 멈춤, 409 `job_closed` 로 죽지 않음) 끝나면 `late_result` 한 건이 남고 워커는 `late_result_recorded`·`hold_released` 를 읽어 로그 한 줄을 쓴다. (c) `closed_jobs` 의 `had_cancel`·표식·`cancelled`·`timed_out` 이면 그룹을 멈춘다. (d) 그 잡의 큐 항목을 전달됨으로 지우고 finish 를 다시 보내지 않는다. (e) 같은 세션 재등록은 멱등이라 다른 레인의 잡을 닫지 않는다. 한 번 `lost` 가 된 잡은 `succeeded` 로 뒤집히지 않는다. 근거: 결정 255 · 256 · 258 · 261 · 322 · 334 | e2e(두 프로세스, 구현 섞음, pid, DB) + 단위 | PR4 |
| RW16 | [축2 · 자재화 실패와 공개 요약 · 결정 259] (a) 스냅샷 tar.gz: 절대 경로·`..`·바깥을 가리키는 링크·링크를 지나 쓰는 멤버·절대 링크·장치/FIFO·setuid 비트·잘린 gz·bz2·비ASCII 이름 (b) 스냅샷 404·연결 끊김·`Content-Length` 불일치 (c) argv[0] 없음·권한 없음·로그 파일 못 엶·`[[repos]]` 에 없는 repo | 워크스페이스 밖에 생긴 파일 0개(해시 대조). 거절은 닫힌 코드만 공개한다: `snapshot_rejected` + `kind`(`absolute_path` `escapes_workspace` `link_outside` `absolute_link` `special_file` `not_a_tarball` `unsupported_compression` `truncated` `unreadable`) + 정리된 멤버 이름. 정상 파일의 모드는 Python `filter="data"` 와 같다(실행 비트 유지, setuid·setgid·sticky 제거, 그룹·기타 쓰기 제거). 끊김은 `lost` + `snapshot_unreachable`, 404·불일치는 `failed` + `snapshot_download_failed`, `.part` 0. 토큰 없이 읽히는 `/api/status` 와 공개 요약에 argv[0]·절대 경로·PATH 값·git stderr 가 0건이고 원문은 잡 로그(`[rcm] …`)로만 간다. `launch_executable_missing`·`launch_permission_denied`·`launch_failed`·`log_unavailable`·`repo_missing`(where=worker)·`workspace_failed` 코드와 인자 모양이 같다(`error` 인자는 Python 예외 클래스 이름 그대로 — 결정 376, 벡터 `error_class`). 근거: 결정 246 · 259 · `materialize.py` · 비밀 노출·워크스페이스 밖 덮어쓰기는 되돌릴 수 없는 결과 | 단위(벡터 `tar_filter` — 악성 아카이브 모음을 두 구현에 같은 파일·같은 기대로) + e2e(공개 상태 검색) | PR4 ((c) 의 `repo_missing` 은 PR5) |
| RW17 | [축2 · git_ref 자재화] Rust 워커가 git_ref 잡을 받는다: (a) 같은 data_dir 에 Python 이 만든 `mirrors/<repo>/` 가 있다(반대도) (b) 제출 때 확정한 sha 가 force-push 로 사라짐 (c) `[[repos]]` 의 `token_file` 자격, URL 에 박힌 자격 (d) fetch 가 시한을 넘김(`git-remote-https`·자격 헬퍼 같은 손자가 남음) (e) CLT 가 없는 Mac 의 `/usr/bin/git` shim (f) PATH 에 git 이 없음 | (a) 미러를 그대로 이어 쓰고(재클론 0) 제출 때 확정한 sha 의 분리 헤드로 체크아웃한다. (b) `commit_missing`(짧은 sha). (c) 토큰은 서버로 가지 않고 argv·`.git/config`·잡 로그·공개 요약 어디에도 0건, `GIT_TERMINAL_PROMPT=0`. (d) git 을 자기 프로세스 그룹에서 돌려 시한 뒤 그룹째 거둔다(손자 0). `git_failed` 는 구조 인자만 싣는다. (e)(f) shim 은 실행하지 않고 「없음(CLT 필요)」으로 센다 — 설치 대화상자가 안 뜨고 `rcm worker --check` 의 git 행과 같은 판정이다. 근거: `materialize.py` `prepare_git_ref` · `gitops.py` · `runner.py:151-216` · 결정 246 | e2e(로컬 git 원격, `ps`, 파일·로그 검색) + 단위(벡터 `find_tool`) | PR5 |
| RW18 | [축1·2 · 산출물 · RL22] 프리셋 `artifacts`(glob·한도·`collect_on`)가 있는 잡이 (a) 성공·실패·취소·시간초과 (b) glob 이 한도(max_bytes·max_files)를 넘김·심볼릭 링크·한글 긴 파일명·빈 수집 (c) 업로드가 끊김, 응답이 유실돼 다시 올림, 업로드와 finish 사이에 워커 재시작 (d) 번들 파일이 지워짐 | 모은 파일 집합·`skipped_count`·`reason_code`·처분 `state` 가 Python 과 같고, 번들은 `rcm wait --fetch-artifacts` 가 풀 수 있는 tar(긴 이름·비ASCII·링크 포함, 종료 0)이다. 서버 영수증의 `bundle_sha256` 이 보낸 바이트의 sha256 이고, 재업로드는 멱등(번들 1개)이며, finish 는 업로드 뒤에만 간다. 못 올린 번들은 `failed` + `upload_failed` 이고 `ready` 로 위장하지 않는다(처분은 보내기 직전에 정함). 잡 결과는 산출물과 무관하고 스풀은 재시작 뒤에도 쓰인다. PUT 은 `Content-Length` 로 보낸다. 취소·시간초과는 `cancel_timeout_seconds` 예산 안에서 수집한다. 근거: 결정 275 · m5tb F63 · F50 · `collect.py` | e2e(`artifacts.state`, sha256, 종료 코드) + 단위(수집 · 처분 표) | PR5 |
| RW19 | [축1 · 호스트 신원·능력·표본] Rust 워커의 등록·능력 보고·heartbeat `host_sample` (a) 같은 머신에서 Python 워커와 번갈아 (b) macOS(`ioreg` 의 `IOPlatformUUID`)·Linux(`/etc/machine-id`, 없으면 `/var/lib/dbus/machine-id`)·둘 다 못 읽음 (c) `requires` 도구·프로브(argv·시한·그룹째 거둠)·`env_passthrough` 누락 (d) 표본이 낡음 | `host_key` = 하드웨어 id 의 sha256 앞 16자(못 읽으면 `worker:<이름>`)가 두 구현에서 같다. 달라지면 같은 머신이 두 호스트로 갈라져 호스트 단위 배치·부하 게이트가 어긋난다. `host` 사실의 키 아홉 개(`os` `os_release` `os_like` `arch` `rosetta` `container` `root` `package_manager` `python`) 중 `python` 은 형태 A 면 앞단이 잰 값, 독립 실행이면 `null`(결정 375)이며, 값이 없어도 서버·`rcm doctor`·웹 카드가 깨지지 않는다. 능력 보고(`fingerprint`, `presets[*].missing_tools`·`workspace_tools`·`missing_passthrough`, `accept_any`)가 같은 머신의 Python 보고와 같아 `any-capable` 배치와 `/api/status` 의 사유 코드가 안 바뀐다. `host_sample` 의 키·단위가 같고 낡은 표본은 보내지 않는다. 프로브는 `grace_seconds` 안에 끝나고 exec 뒤에 남지 않는다. 근거: 결정 93 · M5f §3.1 · `capability.py` · `capabilities.py:126-158` | 단위(같은 입력, 같은 JSON 대조) + e2e(`/api/status`) | PR5 |
| RW20 | [축5 · 버전 결합과 서버 올림] 서버는 등록의 `version` 이 자기 `__version__` 과 문자열로 같을 때만 받는다(아니면 409 + `server_version`). 운영은 체크아웃을 `git pull` 하고 서비스를 재시작해 올린다. (a) 서버를 새 버전으로 올렸는데 그 버전의 Rust 바이너리가 없다 / 있다 (b) 바이너리의 `--version` 과 `__version__`, 그리고 릴리스 때 버전을 적는 세 곳 (c) 같은 서버에 Python 워커와 Rust 워커가 섞여 붙는다 | (a) 바이너리가 없을 때 워커가 하는 일은 Q4 의 답이되 어느 쪽이든: 도는 잡은 하나도 `lost` 가 안 되고(드레인), 조용히 멈추지 않으며(종료 2 와 무엇을 어디서 받을지 한 줄, 또는 서버가 404 `no_worker_binary` 로 답했을 때만 Python 구현으로 같은 서버 버전을 따라가며 한 줄 — 서버의 구현 표식은 「—」), 감독자가 되살려도 등록 루프가 안 생기고, `/api/status` 에 워커 down 이 사람 말로 보이고 큐의 잡이 사라지지 않는다. 있을 때는 같은 PID 로 새 바이너리가 이어받고 이전 이미지의 디스크 큐를 등록 전에 보낸다. (b) `__init__.py` 의 `__version__`, `rust/rcmw/Cargo.toml` 의 `version`, `rust/Cargo.lock` 의 `rcmw` 항목 `version` 이 늘 같다 — 셋 중 하나가 어긋난 커밋은 cargo 없이 도는 `tests/test_rcmw_version_lock_m5u.py` 가 막는다(`--locked` 빌드가 실패하기 전에). 빌드한 바이너리의 `--version` 도 같고 등록 `version` 에 그대로 실린다. (c) 구현이 달라도 같은 풀·같은 판정이다. 근거: `remote_workers.py:674-682` · M5q · F56 · F66 | 단위(버전 글 잠금 · 빌드 잠금 — PR2) + e2e(가짜 새 버전 서버: 종료 코드, stderr, 큐 — PR6) | PR2 ((b)) · PR6 ((a) · (c)) |
| RW21 | [축5·2 · 드레인과 다시 실행 · M5q ③④ · RL12 · RL13 · RL32] 도는 Rust 워커가 heartbeat 의 `server_version` 이 달라진 것을 듣는다(또는 레인의 재등록이 버전 409, 또는 기동 등록이 버전 409). (a) 잡이 도는 레인이 여럿 (b) 끝난 잡의 보고 큐가 60초 안에 안 빔 (c) 300초 안에 이미 재실행했음 (d) 서버가 원래 버전으로 돌아옴 (e) `--once`·고정(pin)·installed 모드·신호 (f) 기동 409 인데 따라가기가 꺼짐 | 새 claim 0, 받은 잡은 끝까지 돌려 보고하고, 모든 레인이 잡 없이 기다리면 같은 PID 로 넘기기 문서의 `restart` 를 exec 한다: argv `[sys.executable, "-P", "-c", RESTART, *원래 argv]`, env 는 `BOOT_ENV` 다섯을 지우고 `RCM_WORKER_RESTARTED_AT` = 지금(F56, Python 과 같은 바이트). 앞단이 따라간 뒤 서버가 내준 새 바이너리가 새 세션, 이전 세션은 `previous_sessions`. 도는 잡 `lost` 0. (b) 60초 뒤 못 보낸 큐를 디스크에 두고 재실행해 새 이미지가 등록 전에 보낸다. (c) 고리를 막는다(드레인 안 함, 로그 한 줄). (d) 드레인 취소 로그 한 줄 후 레인 재개. (e) `--once` 는 무시하고, 고정·installed 는 드레인 없이 이유 꼬리만 붙이며, 신호가 드레인을 이긴다. (f) 409 메시지와 종료 2(`cli.py:4993-4995` 와 같음). 재실행 실패는 `restart failed` + 종료 1(감독자가 되살림). 측정 프로브가 exec 뒤에 남지 않는다. e2e 는 워커 env 에 `RCM_WORKER_RUST_BINARY` 를 두지 않는다(env 가 따라가기 캐시를 이기므로, §4.3) — 첫 · 둘째 바이너리 모두 서버가 내준 캐시 파일에서 뜨고, 「진짜 Rust」 확인의 기대 경로와 sha256 은 그 캐시 파일의 것이다. 근거: M5q §3.3 · §14 · `remote_worker.py:2390` `_drained` · `cli.py:5024-5034` · F56 | 단위(cargo: 409 · 다시 실행 → PR4, 드레인 상태 기계 · 60초 · 300초 창 · `--once` · 고정 · 신호 → PR5) + e2e(PID 동일, 잡 pid, DB, 재실행 env — 서버가 둘째 버전의 `rcmw` 를 내줘야 해 `RCM_TEST_BUILD_VERSION` 으로 만든 `test-knobs` 빌드 두 벌 → PR6) | PR4 (409 · 다시 실행) · PR5 (드레인 단위) · PR6 (e2e) |
| RW22 | [축5 · 배포·플랫폼·무결성] 바이너리가 머신에 닿는 길(Q3 의 답과 무관하게) (a) Rust 도구가 없는 머신에서 설치·실행 (b) macOS arm64·macOS x86_64·Linux x86_64·Linux aarch64·그 밖 (c) sha256 불일치·잘린 다운로드·3xx 리다이렉트·남이 쓸 수 있는 캐시 폴더 (d) 틀린 아키텍처·실행 비트 없음 | (a) Python wheel 은 계속 `dependencies = []`, `py3-none-any` 이고 Rust 없이 설치되며 서버는 표준 라이브러리만으로 결정적 wheel 을 조립한다(`test_packaging`·client-wheel 시험 그대로 통과). `implementation` 을 안 준 머신은 Python 워커로 계속 돈다. (b) 플랫폼 판정이 `uname`·Rosetta 와 맞고 맞는 바이너리만 고른다. 증거가 없는 target 은 목록에 없다(Q8). (c)(d) 지금의 따라가기와 같은 안전 규칙: sha256 이 틀리면 캐시에 아무것도 안 남고, 리다이렉트를 따르지 않아 토큰이 다른 호스트로 안 새며, 캐시는 `0700`·내 소유일 때만 쓰고, 실패는 사유 한 줄과 기동 전 종료 2 다(Python 으로 물러나지 않음 — Q4 의 길은 서버가 404 `no_worker_binary` 로 답한 때뿐, 결정 362 · 365). 워커가 traceback 으로 죽지 않는다. 무결성(sha256·크기 상한)과 신뢰 모델(서버가 준 코드를 실행)을 문서가 말하고, PLAN 결정 8 의 「런타임 의존성 0」 이 Q10 의 문장으로 갱신된다. 근거: `PLAN.md:35` · `:571` · `:645`, `follow.py:12-15` · `clientwheel.py:1-16` · `tests/test_packaging.py:106` · `:186` | 단위(가짜 서버·가짜 캐시) + e2e(이 머신 target) + 문서 잠금 시험 | PR6 |
| RW23 | [축6 · `rcm worker` 가 Rust 를 띄움 · 설정 넘기기] Python `rcm worker` 가 설정(`worker.toml`, `RCM_WORKER_TOKEN`, `--server --pool --lanes --name --data --once --check --no-follow`, `[[repos]]`, `[host]` 프로브, `implementation`·`rust_binary`)을 읽고 형태 A 로 바이너리에 넘긴다(Q5) | 토큰이 argv·`ps`·`/proc/<pid>/cmdline`·디스크 경로에 0건이다(`the worker token cannot be passed as a flag` 그대로, 토큰은 환경이나 이름 없는 0600 임시 파일의 상속 fd 로만, Rust 가 읽고 곧바로 닫음). exec argv 는 `[rcmw, "run", "--handoff-fd", "<n>", "--", <원래 argv>]` 라 `ps`·`pgrep -f "worker --server …"` 에 워커로 보인다(F53). `worker.toml` 의 모르는 키·틀린 값은 오늘과 같은 `ConfigError` 문장과 종료 2 다. 첫 SIGTERM·SIGINT 는 도는 잡을 `lost` + `worker stopped` 로 보고하고 종료 0, 두 번째는 즉시 종료 1. 서비스가 보는 PID 가 `rcm worker` 실행 전후로 같다. 설정이 없거나 토큰이 없을 때의 사용법 문장과 종료 2 가 같다. `rcm --version`·`rcm worker --help` 는 바이너리가 없어도 뜬다. `implementation = "rust"` 인데 바이너리가 없음·실행 비트 없음·아키텍처 틀림·버전 다름이면 기동 전 종료 2 와 한 줄이고 Python 으로 물러나지 않는다(결정 362). `server = "auto"` 는 RW32. 근거: `cli.py:4933-5009` `cmd_worker` · `config.py:1982-2041` `load_worker_config` | e2e(`ps`·fd 목록·종료 코드) + 단위(설정 표) | PR4 |
| RW24 | [축6 · rcm 으로 인식되는 자리 · 운영 보호] 워커를 알아보는 자리: `rcm setup --role worker` 가 쓰는 launchd/systemd 파일(라벨 `com.remote-ci-monitor.worker`·`rcm-worker`, 서비스 `PATH`·`HOME`·`WorkingDirectory`), `rcm doctor` 의 서비스 해석(`rcm_launch` 은 실행 파일 이름이 `rcm` 이거나 `python -m remote_ci_monitor.cli` 일 때만 인자를 푼다), `rcm worker --check` 의 행, `tools/guard_production.py`(`rcm serve`·`worker`·`token` 과 서비스 단어로 운영 `data_dir` 열기를 막음) | 서비스 파일이 `rcm worker` 이고 설정이 `implementation = "rust"` 여도 doctor 가 서비스를 찾고 인자를 풀며 역할·설정 경로·`data_dir` 행이 오늘과 같다(형태 A). 새 구현 행(구현·바이너리 경로·버전·target·sha256)이 더해지고 기존 행의 키·순서는 그대로다. `--check` 가 바이너리 없음·아키텍처 불일치·실행 권한 없음·macOS 격리 속성을 한 줄과 고치는 법으로 말한다. setup 이 쓴 서비스 파일은 `PATH` 앞에 rcm venv 를 두지 않는다. guard 훅은 `rcmw`(절대 경로, `env -S` 포장 포함)가 운영 설정·`data_dir`·넘기기 대상으로 불려도 같은 거부를 낸다. 훅을 비켜 개발 빌드가 운영 `worker-sessions` 의 고아를 죽이거나 큐를 보내는 길은 0(주인 해시가 다른 폴더는 안 건드림). `LIVE_FORBIDDEN`: 운영 워커에 대한 시험은 하지 않고 가짜 운영 설정과 훅 시험 상자로 대신한다. 근거: `doctor.py:453-467` · `guard_production.py:108` · `:116` · `:847-848` · 결정 325 · RL38 | 단위(기존 `test_doctor*`·`test_guard_production*` 의 Rust 입력 변형) + e2e(`rcm worker --check` 표) | PR7 (형태 C 의 서비스 해석은 PR9) |
| RW25 | [축8 · 롤백 Rust → Python] 한 머신을 Rust 에서 Python 워커로 되돌린다: (a) 잡이 도는 중에 드레인 뒤 전환 (b) Rust 워커를 `kill -9` 한 뒤 Python 워커가 같은 data_dir 로 시작 (c) Rust 바이너리가 시작 실패로 감독자 재시작 루프 (d) 전환 설정을 두 번 뒤집음 | 되돌리는 방법은 하나(`implementation = "python"` 또는 env 를 지우고 재시작 — 결정 383)이고 `docs/operating.md` 에 있으며 `rcm worker --check` 가 현재 구현을 말한다. (a) 드레인 경로라 `lost` 0. (b) RW1·RW3 의 역방향이 그대로 성립한다: 고아 정리, 디스크 큐 이어 보내기, 홀드 0, `rcm wait` 가 잡의 실제 결과 코드를 낸다. (c) 두 구현이 번갈아 같은 data_dir 를 도는 자동 폴백은 없다(Q4 (a) 에서 서버가 404 `no_worker_binary` 로 답한 때만, 한 번에 한 구현, 로그 한 줄). 실패한 시작은 세션 폴더를 남기지 않아 재시작 루프가 등록 목록을 키우지 않는다. (d) 두 번 뒤집어도 data_dir 의 상태가 사라지지 않는다. 서버 쪽 변경 없이 되돌려진다. 근거: 결정 268 · 326 · P6 GAP1 · 되돌릴 수 없는 결과(결과 위조·두 번 실행) | e2e(구현 교차 × kill -9, DB `jobs`·`worker_sessions`, 세션 폴더 수) | PR4 (e2e) · PR7 (문서 · `--check`) |
| RW26 | [축7 · 기존 e2e 를 Rust 로 · 진짜 Rust 였다는 증거] 기존 e2e — `tests/test_e2e_worker.py` 7 · `test_e2e_worker_grace_m5tb.py` 12 · `test_e2e_worker_outbox_m5tb.py` 5 · `test_e2e_m5n.py` 2 · `test_e2e_worker_m5q.py` 6 — 를 (a) 선택기 `RCM_TEST_WORKER_IMPL=rust` 로 돌린다 (b) 바이너리가 없거나 아키텍처가 틀리다 (c) 선택한 구현이 조용히 Python 으로 물러난다 (d) 시험이 모듈 상수를 바꾸는 감싸개를 쓴다(RL18 은 조각 200바이트) (e) 시험이 설치본 wheel 따라가기를 쓴다(m5q 6 · outbox RL12-b) | (a) 25개(7 + 12 + 4 + 2)가 시험 함수 본문 수정 0 으로 두 구현에서 통과한다. 바뀌는 곳은 도우미뿐이다: `tests/conftest.py`(선택기 · `RUST_KNOB_TESTS` · `RUST_REPLACED`), `WorkerProc.start`(등록 확인 뒤) · `WorkerProc.kill` · `M5nWorker.kill` 의 실행 파일 확인, `tests/test_e2e_worker_outbox_m5tb.py` 의 `WRAPPER` 상수 한 줄. 진짜 Rust 였음: 등록 뒤 워커 pid 의 실행 파일(Linux `readlink /proc/<pid>/exe`, macOS `lsof -a -p <pid> -d txt -Fn` 의 첫 `n` 줄)의 realpath 가 선택한 바이너리와 같다. 서버 쪽 구현 표식은 이 행이 아니라 RW35(PR7)가 잠근다 — PR4~PR6 의 서버는 그 키를 읽지 않는다. Rust 판 teardown 에서 워커 로그의 `panicked at` 0(Python 의 `"Traceback" not in` 단언 짝, F54). 요약 줄에 구현별 통과 수 · `rust_pending.txt` 수 · replaced 수가 따로 적힌다. (b)(c) 선택이 Rust 인데 못 찾거나 실행 파일이 그 바이너리가 아니면 skip 이 아니라 실패다. (d) 기본은 릴리스 프로필 바이너리이고 `RUST_KNOB_TESTS` 의 시험만 `test-knobs` 빌드다. 릴리스 바이너리 바이트에 `RCM_TEST_` 0. (e) 7개는 `RUST_REPLACED` 가 Rust 판에서 빼고 각각 대신하는 Rust 시험 이름을 적으며, 그 이름이 실제 시험인지 conftest 가 잠근다(대체 시험은 PR6, 그 전에는 `rust_pending.txt`). Rust 빌드 시간이 시험 상한(직렬 20초 · 병렬 60초, `slow` 제외)과 전체 예산에 들어가는지 숫자로 적는다. 근거: F28 · F29 · F31 · F53 · F54 · `tests/conftest.py:15-16` · `m5tb-workplan.md` §13 PR2 10번 | pytest 선택기 + 실행 파일 검사 + 구현 매트릭스 요약 | PR4 · PR5 · PR6 (pending 0 · 대체) |
| RW27 | [축7 · 단위 시험과 변이 시험 이식] Python 단위 시험 14개 파일이 `RemoteWorker(...)` 를 직접 만든다(`test_worker_session_m5tb` 80 · `test_worker_selfstop_m5tb` 57 · `test_worker_outbox_m5tb` 92 · `test_worker_log_offset_m5tb` 23 시험 등) — 바이너리로는 못 돌린다. `scripts/mutcheck.py` 에는 `remote_worker.py` 104 · `runner.py` 19 · `job_supervisor.py` 5 = 128개 변이가 있다 | RL1~RL56 중 워커 몫 행마다 Rust 시험(시계·서버 응답을 주입하는 cargo 단위 또는 Rust 매트릭스 e2e)이 대응하는 표(§7.4)가 있고 빈 칸 0이다(서버 몫은 사유와 함께 `n/a`). `cargo test` 이름에 RL ID 가 들어간다(`rl12_…`). 128개 각각에 같은 뜻의 Rust 변이(`rs-<이름>`, §7.5)가 있어 시험이 빨개진다(생존 0) — 대응할 Rust 코드가 없는 것은 `n/a` + 이유. Python 변이 128개는 Python 구현이 남으므로 그대로다. 시계 주입·가짜 서버는 `#[cfg(test)]` 안에만 있고 릴리스 바이너리는 시험용 진입점을 갖지 않는다. 근거: AGENTS.md(`scripts/mutcheck.py`: 모든 변이에서 시험이 빨개야 함) · `scripts/mutcheck.py:274-288` | 대응 표(문서) + pytest 래퍼가 cargo 시험 수 > 0 단언 + `mutcheck.py --only rs-…` 결과(생존 0) | PR2 · PR3 · PR4 · PR5 |
| RW28 | [비회귀 · 같은 코드를 쓰는 서버 쪽] 서버(Python)가 계속 쓰는 것: 서버의 로컬 레인 `worker.py` 가 쓰는 `runner.run_job` 과 `job_supervisor.py`, 서버의 `preset_env`·`rcm check`·`rcm doctor`·config 의 git 행·gitops 가 쓰는 `runner.find_tool`·`missing_tools`·`measure_tools`·`tool_blocked`(macOS CLT shim 은 `libxcselect` 바이트로 판별)와 `hostsample` | Python 쪽 코드와 시험이 그대로 통과한다(`test_worker.py`·`test_tool_shim.py`·`test_requires_m5l.py` 변경 0, `test_job_supervisor_m5tb.py` 는 0줄 바뀌고 Python 판 그대로 돈다(F51)). 도구 판정은 두 구현이 같은 벡터 파일로 같은 답을 낸다: PATH 의 상대 항목은 워크스페이스 기준, 빈 PATH 는 빈 PATH, 절대 경로, shim 은 「없음(CLT 필요)」, `xcode-select -p` 1분 캐시. `rcm check`·`rcm doctor`·`/api/status` 출력 diff 0. 로컬 레인 잡과 원격(Rust) 잡이 같은 입력에 같은 `summary_code` 를 낸다(어느 레인이 집었나가 요약을 안 바꾼다). 서버 `/worker/*` 라우트는 바뀌지 않거나 키를 더하기만 하고 옛 Python 워커가 무시한다. 서버 로컬 레인은 Python 그대로다(결정 380). 근거: `runner.py:3-5` · `:137-273` · M5t-a 결정 246 | 기존 pytest 전체(변경 0) + 공유 벡터 대조 시험 | PR1 · PR3 (벡터) · 전 PR (비회귀) |
| RW29 | [축5·8 · macOS 서비스 전환] Mac 의 launchd 워커 서비스를 Python 에서 Rust 로 바꾼다(형태 A: 서비스 파일은 그대로 `rcm worker`, 설정만 `implementation = "rust"`; 형태 C 면 서비스 파일이 `rcmw`). 바이너리는 다운로드(`com.apple.quarantine` 속성)·ad-hoc 서명·서명 없음·새 경로 각각 | (a) 「Local Network」 권한이 exec 뒤에 누구 이름으로 매겨지는지(F45, 지금 —)를 재서 보고서에 적는다. 권한이 없으면 LAN 의 서버로 unicast 가 `EHOSTUNREACH` 이고 서버 큐는 정상으로 보이므로, 전환 절차 문서·`rcm doctor`·첫 등록 실패 로그가 이를 사람 말로 말하고, 한 번 허용 후 재시작하면 등록된다. (b) 격리 속성·서명 없음으로 실행이 거부돼도 같은 방식으로 드러난다(조용한 중단 0). (c) TCC 규칙(`~/Documents`·`Desktop`·`Downloads` 밖의 data_dir)이 그대로다. (d) 전환 전 마지막 heartbeat 와 전환 후 첫 등록 사이에 도는 잡이 `lost` 가 아니다(드레인 뒤 전환). `LIVE_FORBIDDEN`: 운영 워커에서는 시험하지 않는다 — 대역은 별도 launchd 라벨 + 시험 서버(운영 포트 아님) + 시험용 `data_dir`. 근거: `docs/operating.md:63-66` · `:80-87` | 실기 · 시험 서버 + 별도 launchd 라벨(`launchctl` 상태, 서버 로그, `rcm doctor`) (live) | PR8 |
| RW30 | [축7·3 · 필수 체크가 못 보는 플랫폼] 필수 체크 `rcm/ci` 는 맥미니(arm64 macOS) 한 대에서 돌고 GitHub Actions `ci.yml` 은 머지 조건이 아니다. Linux 의 `/proc/<pid>/stat` 신원·`flock`·`killpg`, x86_64, Rosetta 는 거기서 안 돈다 | 머지 단계 증거 표에 target 마다 「빌드 · RW1 · RW2 · RW8 · RW10 의 실제 실행 증거(잡 번호 또는 로그)」 또는 「미검증」이 적힌다(방법은 Q8). 미검증 target 의 바이너리는 `SHA256SUMS`·`worker_binaries_dir` 목록에 없다(앞단이 그 target 에서 `implementation = "rust"` 를 받으면 종료 2 와 한 줄). x86_64 macOS 는 Rosetta 아래의 arm64 빌드로 갈음하지 않고 별도로 적는다. cargo 빌드·시험 시간이 `rcm-ci` 예산(전체 16분 안팎, 시험 하나 20초 상한)에 들어가는지 숫자로 적는다. `RCM_REQUIRE_RUST=1` 인 rcm-ci 에서 cargo 가 없으면 Rust 시험이 skip 이 아니라 실패다. 근거: CLAUDE.md 「필수 체크는 `rcm/ci`」 · `docs/acceptance/reports/2026-11-27-m5tb-acceptance.md:26-27` · `:164` | 단위(`RCM_REQUIRE_RUST` 판정, 목록에서 빠진 target 의 종료 2) + `rcm-ci` 잡 로그 + 머지 전 증거 표 | PR2 (판정) · PR6 (목록) · PR8 (증거 표) |
| RW31 | [축4 · 신원 한계 · 되돌릴 수 없음] 죽은 세션 D 의 `proc.json` 에 적힌 pid 가 재사용됐고 새 프로세스의 시작 문자열이 우연히 같다(초 단위 `lstart` · 같은 jiffies) — 만들 수 없는 순간이라 벡터로만 | 두 구현이 같은 판정을 낸다: 시작 문자열이 같으면 「같은 프로세스」로 읽는다(Python 과 같은 한계 — Rust 가 더 느슨하지도 더 엄하지도 않다). 문서(`docs/operating.md` 워커 절)가 이 한계를 한 줄로 말한다. 시작 문자열이 다르면 신호 0(RW1). 근거: `remote_worker.py:1127-1132` `_start_valid` · edge-scope 「되돌릴 수 없는 결과는 드물어도 행」 | 단위(벡터 `ident` 의 「같은 시작 문자열」 행을 두 구현이 같게) | PR3 |
| RW32 | [축6 · Rust 가 아직 모르는 설정 · 결정 379 · 390] `implementation = "rust"` 인데 (a) 설정에 Rust 가 모르는 필드가 기본값이 아닌 채 있다(M5t-c PR3 이 더할 `pair_secret` 이 첫 예) (b) `server` 가 비었거나 `"auto"`(파일 · `--server auto` 플래그 둘 다) — `--check` 와 함께 부른 경우 포함 (c) 누가 `WorkerConfig` 에 필드를 더했는데 세 목록 어디에도 넣지 않았다 (d) 넘기기 문서에 Rust 가 모르는 키가 있다 | (a)(b) 관문이 `_worker_config` 안, 서버 찾기(mDNS · 부모가 넘긴 `RCM_SERVER`, `cli.py:4766-4769`)보다 먼저 돈다 — 그래서 `--check` 의 확인 질문과 따라가기 ①보다도 먼저. 종료 2 와 한 줄 `rcm: <key> is not supported by the Rust worker yet — use implementation = "python"`(`<key>` 는 `server = "auto"` · `server = ""` 또는 필드 이름). 시험은 손으로 만든 `WorkerConfig` 가 아니라 `cli._main(["worker", "--config", …], follow_enabled=True, execve=<가짜>)` 로 실제 `cmd_worker` 흐름을 돌고(동적 속성 `discovered_server` 가 달리는 흐름, F64) `discover` 를 부른 횟수 0 · 가짜 `execve` 0회 · 서버로 요청 0건(중계 요청 수 0, 토큰을 쓰지 않음)을 단언한다. 같은 설정에 `implementation = "python"` 이면 오늘대로 돈다(발견 갈래를 탐). (c) 잠금 시험이 빨갛다: `dataclasses.fields(WorkerConfig)` 의 이름 집합 = `HANDOFF_KEYS` ∪ `FRONT_KEYS` ∪ `RUST_REFUSED_KEYS`, 셋은 서로 겹치지 않음. `implementation` · `rust_binary` · `path` 는 `FRONT_KEYS` 라 거절되지 않는다. (d) Rust 가 기동 전 종료 2(조용히 무시 0). 벡터 `handoff_keys` 를 Python 과 cargo 가 같이 대조. 근거: F61 · F62 · F64 · `config.py:516-539` · 짝짓기 검증을 건너뛴 채 토큰을 보내는 길은 되돌릴 수 없는 결과(비밀 노출) | 단위(`cli._main` 흐름 · 앞단 표 · 잠금) + cargo(모르는 키) + e2e(종료 코드 · 중계 요청 수) | PR4 |
| RW33 | [축5 · 배포 바이트 = 시험한 바이트 · 결정 391] 운영자가 받는 `rcmw`(Q3 (a) (i) 이면 태그 빌드의 Release 자산 → `worker_binaries_dir`)는 rcm-ci 가 시험한 빌드와 다른 머신(GitHub Actions)에서 빌드된다. 스모크가 빨갛다 / 초록이다 | 태그 빌드의 `rcmw-build` 잡이 `rust/rust-toolchain.toml` 의 같은 버전 · `--locked` · `--remap-path-prefix` 로 빌드해 산출물로 올리고, `rcmw-smoke` 잡이 **그 같은 파일**로 `tests/test_rust_smoke_m5u.py`(`--version` = 태그, `supervise` 미시작 125 + 표지, 진짜 서버에 등록 · 성공 잡 하나 · finish, 와이어 골든 대조)를 통과한 뒤에만 `github-release` 가 그 파일을 자산과 `SHA256SUMS` 로 올린다. `rcmw-smoke` 와 `github-release` 의 로그에 그 파일의 sha256 이 있고 같다. 스모크가 빨가면(Q3 (i)) `github-release` · `pypi` 가 돌지 않는다 — Release 도 wheel 도 0(PLAN 「fail-open 금지」). (i′) 면 wheel 만 나가고 바이너리 자산 0, run 은 빨강. 바이트에 `/Users/` · `/home/` 0(§8). 근거: F60 · F69 · `.github/workflows/release.yml` | 워크플로 잠금 시험(`tests/test_ci_release_workflow.py` 꼴 — `rcmw-build` → `rcmw-smoke` → `github-release` 순서 · 같은 산출물 이름 · `github-release` 와 `pypi` 의 `needs` 에 `rcmw-smoke`) + 태그 잡 로그(릴리스 때) | PR6 |
| RW34 | [축1 · 워커 로그 줄 · 결정 392] e2e 가 워커 로그의 글을 단언한다(`orphan #<id>: stopped process group` · `#<id>: no answer from the server for` · `draining: no new claims, restarting when running jobs finish` · `job #<id> still not reported — restarting anyway` · `#<id> log: … retrying` 등, F54) | 그 줄들이 벡터 `log_contract`(id · 글 틀 · Python 출처 `file:line`)에 있다. Python 시험이 출처 함수를 불러 그 틀대로 찍는지, cargo 시험이 Rust 가 같은 바이트로 찍는지 본다. Rust 판 e2e 매트릭스가 같은 단언을 그대로 통과한다(RW26). 근거: F54 | 단위(벡터, 두 구현) + e2e 매트릭스 | PR1 (벡터) · PR4 · PR5 (Rust) |
| RW35 | [축1 · 서버가 아는 구현 · 결정 374] (Q9 (a) 일 때) Rust 워커와 Python 워커가 같은 서버에 붙는다. 옛 서버(키를 모름)에 Rust 워커가 붙는다. 같은 이름으로 Rust 프로세스 뒤에 Python 프로세스가 등록한다(반대도). 값이 `"rust"` · `"python"` 밖의 문자열 · 숫자 · 64자 넘음 | 토큰 있는 머신 문서의 그 머신 항목과 `rcm machines --machine <id>` 에 Rust 워커는 `worker_impl: "rust"` · 「rust」, Python 워커는 키 없음 · 「—」(python 으로 지어내지 않음). 등록마다 값을 바꿔 앞 프로세스 값을 물려받지 않는다. 틀린 값은 없음. 토큰 없는 머신 문서의 키 집합(`MACHINE_KEYS`)과 호스트 사실 아홉 키 · `HOST_KEYS == HOST_FACT_KEYS` 잠금은 그대로. 옛 서버는 키를 버리고 등록은 그대로 된다. 근거: F52 | 단위(서버 등록 본문 표) + e2e(`rcm machines --machine build-02 --json`) | PR4 (보냄) · PR7 (서버) |

Excluded (rare): 서버를 재시작하는 순간에 같은 서버에서 `cargo build` 가 돌고 있음 · ① · 두 조건이 겹치고 다시 빌드하면 됨(되돌릴 수 있음) · `CHANGELOG.md` · `docs/acceptance/reports/` · `PLAN.md` · `git log --grep` 에서 「cargo」 0건
Excluded (rare): 사람이 도는 중인 워커 바이너리 파일을 `cp` 로 직접 덮어씀(`ETXTBSY`·한쪽 실패) · ① · 사람의 수동 조작과 도는 중이 겹치고 상태가 안 바뀌며 갱신은 캐시 새 폴더 + exec 만 쓴다(RW21·RW22 가 정상 경로를 잡음) · 같은 네 곳에서 「ETXTBSY」 · 「Text file busy」 0건

scenario-author 의 셋째 제외 줄은 같은 pid 가 같은 시작 문자열로 재사용되는 경우였다. 이 경우는 되돌릴 수 없는 결과라서
RW31 행으로 되돌렸다. edge-scope 의 「Rare still goes in when: The result cannot be undone」에 따른 것이다. 이력 검색 결과는
이렇다. 「pid 재사용」은 `git log --grep` 에서 1건(`137799c`)이 나왔는데 시험 서버 도구 `live_proc.py` 의 신원이고 워커
신원이 아니다. 「same pid」는 CHANGELOG 에서 1건(`:785`)이 나왔는데 M5q 의 같은 PID exec 이고 신원 재사용이 아니다. 나머지는
0건이다.

P5 의 실기 몫(live lane)으로는 아래 다섯 행을 추천한다. P5 가 같은 규칙으로 고르므로 행에 표식은 달지 않았다. 모두 단위와
e2e 로 먼저 잠그는 행이다.

- PR4: RW1(워커 `kill -9`, 구현 교차), RW15 (a)(같은 토큰 두 프로세스, 곧 워커 재시작), RW5 (b)(서버 SIGTERM 정지 후
  재기동), RW25 (b)(`kill -9` 후 다른 구현으로 시작).
- PR6: RW21(서버 버전을 바꿔 재기동, 곧 서버 재시작). 1판에서는 PR5 였는데 RW21 의 e2e 가 PR6 으로 옮겨서 함께 옮겼다.

### 7.2 시험의 자리 (pytest 래퍼와 cargo)

cargo 시험은 `rust/rcmw/` 안에 둔다. `#[cfg(test)]` 단위 시험과 `rust/rcmw/tests/` 의 통합 시험이다. 이름에는 RW 나 RL ID 를
넣는다(`rw6_…`, `rl12_…`).

cargo 시험은 시나리오 추적에 잡히지 않으므로(F35) 추적용 pytest 래퍼를 둔다. 래퍼는 PR2 의
`tests/test_rust_supervisor_m5u.py`, PR3 의 `tests/test_rust_runner_m5u.py` 와 `tests/test_rust_session_m5u.py`, PR4 의
`tests/test_rust_worker_m5u.py`, PR5 의 `tests/test_rust_extras_m5u.py`, PR6 의 `tests/test_e2e_rust_follow_m5u.py` 와
`tests/test_rust_smoke_m5u.py` 다. 각 래퍼 시험은 `cargo test --locked -- <이름
접두>` 를 돌려 종료 0 과 「N passed」 의 N 이 기대 수 이상인지 단언하고 RW 태그를 단다. N 이 0 이면 실패다. 필터가 아무것도
고르지 않은 것을 통과로 세지 않으려는 것이다.

건너뛰기 규칙은 §4.6 에 있다(F63). Rust 가 필요한 모듈은 `pytestmark = pytest.mark.skipif(not rust_tool.AVAILABLE, …)` 한
줄만 두고 `RCM_REQUIRE_RUST=1` 이면 수집 오류로 끝난다. 본문 안의 `pytest.skip()` 은 0건이다.

PR1 의 Python 쪽 벡터 시험 `tests/test_worker_vectors_m5u.py` 는 Python 함수가 벡터를 통과하는지 본다. 벡터가 Python 의 실제
동작이라는 보증이 이 시험이다.

### 7.3 e2e 매트릭스

`RCM_TEST_WORKER_IMPL` 이 없으면 python 으로 돈다. 오늘과 같다. rcm-ci 에서 전체 pytest 는 python 으로 돌고 워커 e2e
묶음(`test_e2e_worker*.py` · `test_e2e_m5n.py` · `test_e2e_worker_impl_m5u.py` · `test_e2e_rust_follow_m5u.py`)은 rust 로
한 번 더 돈다. 묶음을 두 번 부르는 일은 `tests/test_rust_build_m5u.py` 의 `slow` 시험이 하위 pytest 로 맡는다. 운영
스크립트를 고치지 않으려는 것이다(F36). 그 시간은 아직 모르고(—) PR4 에서 잰다.

Rust 판에서 도는 기존 시험은 25개다(7 + 12 + 4 + 2). 기본 바이너리는 릴리스 프로필이고 `RUST_KNOB_TESTS` 에 든 RL18 만
`test-knobs` 빌드로 돈다.

설치본 wheel 따라가기를 쓰는 7개(m5q 6개와 outbox 의 RL12-b, F53)는 python 판에서만 돈다. Rust 판에서는 `RUST_REPLACED`
가 이 7개를 빼고 `tests/test_e2e_rust_follow_m5u.py`(PR6)의 시험이 같은 시나리오를 RW21 의 길로 대신한다. 그 길은 드레인,
`restart` exec, 앞단 따라가기, 서버가 내준 새 `rcmw` 순서다. PR6 전에는 이 7개가 `rust_pending.txt` 에 있다.

### 7.4 RL 대응표 (RW27, 빈 칸 0)

잰 방법은 이렇다. `tests/test_*.py` 에서 `def test_rl<N>_…` 이름과 `# RL<N>` 주석을 모은 스크래치 스크립트 `rlmap.py` 를
돌렸다. 「Python 시험」 칸은 그 파일 이름에서 `test_` 와 `.py` 를 뗀 것이다. 「Rust 대응」 칸에서 e2e 는 Rust 매트릭스로 같은
e2e 를 다시 도는 것이다. cargo 는 `rl<N>_` 이름의 cargo 시험이다. n/a 는 서버 · CLI · 웹 · 문서 몫이라 이 마일스톤이 바꾸지
않는 것이다.

| RL | Python 시험 | Rust 대응 | PR |
|---|---|---|---|
| RL1 | acceptance_m5tb_live, e2e_worker_grace_m5tb, worker_selfstop_m5tb | e2e(grace) + cargo `rl1_`(selfstop) + 실기(PR8) | PR4 |
| RL2 | e2e_worker_grace_m5tb, worker_selfstop_m5tb | e2e + cargo | PR4 |
| RL3 | e2e_worker_grace_m5tb, worker_selfstop_m5tb | e2e + cargo | PR4 |
| RL4 | worker_outbox_m5tb, worker_selfstop_m5tb | cargo | PR4 |
| RL5 | e2e_worker_grace_m5tb, worker_selfstop_m5tb | e2e + cargo | PR4 |
| RL6 | e2e_worker_grace_m5tb, e2e_worker_outbox_m5tb, worker_session_m5tb | e2e + cargo | PR4 |
| RL7 | e2e_worker_grace_m5tb, worker_lost, worker_selfstop_m5tb | e2e + cargo | PR4 |
| RL8 | e2e_worker_grace_m5tb, worker_selfstop_m5tb | e2e + cargo | PR4 |
| RL9 | worker_session_m5tb | cargo | PR4 |
| RL10 | e2e_worker_grace_m5tb, worker_session_m5tb | e2e + cargo | PR4 |
| RL11 | worker_outbox_m5tb, worker_session_m5tb | cargo | PR4 |
| RL12 | cli_worker, doctor_cli, e2e_worker_outbox_m5tb, worker_outbox_m5tb, worker_session_m5tb | (a · c) cargo + e2e → PR4, (b 드레인 · 디스크 큐) cargo → PR5, e2e(RL12-b 의 Rust 대체) → PR6, cli_worker · doctor_cli 는 Python 앞단이라 그대로 | PR4 · PR5 · PR6 |
| RL13 | worker_session_m5tb | cargo(버전 409 를 등록 전에 — 따라가면 `restart` exec, 아니면 종료 2) | PR4 |
| RL14 | e2e_worker, e2e_worker_outbox_m5tb, worker_outbox_m5tb, worker_session_m5tb | e2e + cargo | PR4 |
| RL15 | acceptance_m5tb_live, worker_session_m5tb | cargo(벡터 `ident`) → PR3, e2e 교차 → PR4 | PR3 · PR4 |
| RL16 | worker_log_offset_m5tb, worker_outbox_m5tb | cargo | PR4 |
| RL17 | e2e_worker_outbox_m5tb, worker_log_offset_m5tb, worker_outbox_m5tb | e2e + cargo | PR4 |
| RL18 | acceptance_m5tb_live, e2e_worker_outbox_m5tb, worker_log_offset_m5tb | e2e(`test-knobs` 바이너리) + cargo | PR4 |
| RL19 | docs_m5tb, worker_outbox_m5tb | cargo(docs_m5tb 는 n/a — 문서) | PR4 |
| RL20 | queue_unreachable_m5tb, worker_outbox_m5tb | cargo(queue_unreachable 은 n/a — 서버 화면) | PR4 |
| RL21 | worker_outbox_m5tb | cargo | PR4 |
| RL22 | worker_outbox_m5tb | cargo | PR5 |
| RL23 | worker_outbox_m5tb | cargo(자재화 중 분단) | PR4 |
| RL24 | irreversible_hold_m5tb, worker_session_m5tb | cargo(워커 몫); 마이그레이션 · 홀드는 n/a — 서버 | PR4 |
| RL25 | docs_m5tb, worker_session_m5tb | cargo(옛 서버 `protocol_required` 종료 3) | PR4 |
| RL26 | worker_selfstop_m5tb | cargo(벽 · 단조 시계 따로) | PR4 |
| RL27 | worker_outbox_m5tb, worker_selfstop_m5tb | cargo | PR4 |
| RL28 | docs_m5tb, queue_unreachable_m5tb | n/a — 요청자 쪽 `rcm wait` · 서버 사유(Python 그대로) | — |
| RL29 | acceptance_m5tb_live, docs_m5tb, queue_unreachable_m5tb | n/a — 요청자 쪽(Python), 실기는 PR8 이 Rust 워커로 다시 | PR8 |
| RL30 | worker_selfstop_m5tb | cargo(`self_stop_threshold`) | PR4 |
| RL31 | worker_selfstop_m5tb, worker_session_m5tb | cargo(신원 모름) | PR3 · PR4 |
| RL32 | docs_m5tb, worker_session_m5tb | cargo(버전 다른 서버, 따라가기 켬 · 끔 — `restart` exec) → PR4, e2e(둘째 버전 바이너리) → PR6 | PR4 · PR6 |
| RL33 | worker_selfstop_m5tb | cargo | PR4 |
| RL34 | acceptance_m5tb_live, job_supervisor_m5tb, worker_selfstop_m5tb | job_supervisor 의 러너 시험(`run_job`, 단조 시계) → PR3 cargo, cargo selfstop → PR4 | PR3 · PR4 |
| RL35 | worker_selfstop_m5tb, worker_session_m5tb | cargo | PR4 |
| RL36 | worker_log_offset_m5tb | cargo | PR4 |
| RL37 | worker_log_offset_m5tb | cargo | PR4 |
| RL38 | worker_session_m5tb | cargo(벡터 `owner_sha`, flock) | PR3 |
| RL39 | worker_outbox_m5tb | cargo | PR4 |
| RL40 | worker_outbox_m5tb | cargo | PR4 |
| RL41 | e2e_worker_grace_m5tb, job_supervisor_m5tb, worker_selfstop_m5tb | job_supervisor 의 러너 시험 → PR3 cargo, e2e + cargo → PR4 | PR3 · PR4 |
| RL42 | e2e_worker_grace_m5tb, irreversible_hold_m5tb | e2e(워커 몫); 홀드는 n/a — 서버 | PR4 |
| RL43 | acceptance_m5tb_live, e2e_worker_grace_m5tb, irreversible_hold_m5tb, job_supervisor_m5tb, worker_session_m5tb | 감독자 argv 계약(`:164` · `:242`) → PR2 `test_rust_supervisor_m5u.py`, 러너 쪽 → PR3 cargo, e2e + cargo → PR4 | PR2 · PR3 · PR4 |
| RL44 | worker_outbox_m5tb, worker_session_m5tb | cargo | PR4 |
| RL45 | acceptance_m5tb_live, docs_m5tb, hold_display_m5tb, queue_unreachable_m5tb, worker_session_m5tb | cargo(워커 몫 — 홀드 뒤 같은 폴더 재판정); 화면 · `release-hold` 는 n/a | PR4 |
| RL46 | worker_outbox_m5tb | cargo | PR4 |
| RL47 | worker_session_m5tb | cargo(첫 등록 · 레거시 잡) | PR4 |
| RL48 | irreversible_hold_m5tb | n/a — 서버(합성 그룹) | — |
| RL49 | docs_m5tb, e2e_worker_grace_m5tb, job_supervisor_m5tb | job_supervisor 의 러너 시험(`:702`) → PR3 cargo, e2e → PR4 | PR3 · PR4 |
| RL50 | e2e_worker_grace_m5tb, hb_order_m5tb, worker_selfstop_m5tb, worker_session_m5tb | e2e + cargo(hb_order 는 서버 몫 n/a) | PR4 |
| RL51 | acceptance_m5tb_live, job_supervisor_m5tb | argv 계약(`:264`) → PR2 pytest, 훅(`:212`) → PR2 cargo, 러너 쪽(`:543` · `:570`) → PR3 cargo | PR2 · PR3 |
| RL52 | job_supervisor_m5tb | argv 계약(`:130` · `:145` · `:156` · `:174`) → PR2 pytest, 훅(`:204`) → PR2 cargo, 상수(`:113`) → PR2 cargo + 벡터 `constants`, 러너 쪽(`:470` ~ `:651`) → PR3 cargo. import 목록(`:101`)은 n/a — Rust 는 크레이트 허용 목록(`test_rust_deps_m5u.py`)이 대신 | PR2 · PR3 |
| RL53 | irreversible_hold_m5tb | n/a — 서버(reload 와 홀드 그룹) | — |
| RL54 | hold_notify_m5tb | n/a — 서버(알림) | — |
| RL55 | log_read_cap_m5tb | n/a — 서버(로그 읽기) | — |
| RL56 | worker_selfstop_m5tb | cargo | PR4 |

### 7.5 Rust 변이 (RW27 · 결정 372)

규칙은 이렇다. Python 변이 `<이름>` 마다 Rust 변이 `rs-<이름>` 을 둔다. 접두 `rs-` 를 쓰는 변이는 지금 없다(grep 0건). Rust
변이는 같은 뜻의 코드 글을 바꾼다. `tests=` 는 그 PR 의 pytest 래퍼와 cargo 시험이다. 대응할 Rust 코드가 없으면 표에 `n/a`
와 이유를 적는다. PR 마다 아래 명령으로 하나씩 확인한다(lead 규칙 6).

```sh
./.venv/bin/python scripts/mutcheck.py --only rs-<이름>
```

| 묶음 | Python 변이(MUTANTS 에서 그대로) | Rust 대응 | PR |
|---|---|---|---|
| 감독자 5 | supervisor-no-parent-check supervisor-ignores-parent-death supervisor-no-post-command-sweep supervisor-popen-window-term-dropped supervisor-failure-exit-one | `rs-` 5개 | PR2 |
| 러너 19 | requires-check-skipped requires-path-server-cwd requires-cancel-ignored popen-check-only-with-requires m5tb-first-check-waits-a-second runner-grace-wall-clock m5tb-no-supervisor-wrap reap-grace-from-leader-end m5tb-no-s7-reap supervisor-not-isolated supervisor-not-started-beats-the-flag requires-early-stop-group-unknown requires-early-cancel-group-unknown supervisor-permission-denied-unmapped runner-spawned-error-escapes runner-abandon-leaves-group popen-error-not-launch-failed spawned-error-not-logged runner-long-line-never-cut | `rs-` 18개. `supervisor-not-isolated`(`-I` 제거)는 n/a — Rust 감독자는 인터프리터가 아니다. 대신 `rs-supervisor-env-leaks`(감독자가 잡 env 의 `DYLD_*` · `RCM_*` 로 동작이 바뀜, RW6 f)를 단다 | PR3 |
| 세션 · 신원 · 디스크 파일 | adopt-foreign-session orphan-kill-no-lstart ps-lstart-locale m5tb-no-previous-sessions m5tb-leaderless-group-confirmed m5tb-sweep-deletes-locked orphan-broken-proc-confirmed orphan-unreadable-proc-confirmed unconfirmed-group-folder-dropped session-list-uncapped json-not-synced-before-rename json-folder-not-synced failed-save-leaves-temp dir-sync-error-swallowed outbox-save-out-of-order unreadable-outbox-silent outbox-save-failure-silent readable-outbox-set-aside long-line-cut-mid-line long-line-never-cut long-line-read-past-the-limit chunk-end-seen-after-read short-spool-read-sent log-chunk-splits-a-line | `rs-` 24개 | PR3 |
| 프로토콜 고리 | m5tb-recheck-never-sends closed-by-server-cancels job-closed-kills-running lost-cancel-keeps-running closed-jobs-ignored lower-bound-one-timeout self-stop-ignores-request-timeout self-stop-monotonic-only self-stop-all-jobs self-stop-reports-cancelled self-stop-in-heartbeat-loop heartbeat-no-deadline stale-hb-response-applied claim-settled-before-running log-sent-in-the-pump fenced-exit-nonzero irreversible-grace-not-lowered unverified-session-not-logged self-stop-at-g ok-at-before-the-answer stale-hb-paused-applied spawned-record-error-escapes spawning-record-missing-still-spawns run-claimed-error-leaves-job-running runner-own-monotonic-clock safety-net-drops-the-result safety-net-unlaunched-group-unknown failed-start-keeps-its-folder reported-dead-folder-kept fenced-finish-keeps-its-outcome replaced-service-silent replaced-unknown-silent unsent-finish-folder-dropped finish-kept-past-24h log-acked-own-count log-offset-query-skipped log-gap-not-followed finish-first-on-connection-failure retransmit-window-ignored dead-session-counts-connection-failures dead-queue-after-register recorded-finish-sent-again finish-waits-forever dead-session-fenced-exits dead-session-wait-forever queued-job-left-the-heartbeat unreadable-spool-sent-empty dead-answer-kept-after-success dead-answer-of-ended-queue-counted unreadable-spool-holds-finish lane-claims-over-its-open-queue halted-queue-left-the-heartbeat stopped-queue-kept-in-the-heartbeat ok-at-synced-on-heartbeat unexpected-error-kills-the-sender missing-bundle-kills-the-sender unexpected-error-holds-finish late-answer-keys-ignored local-error-kept-after-a-drop dead-save-unlabelled unexpected-finish-skips-the-24h-window phase-holds-finish dead-local-error-not-counted local-errors-blamed-on-the-server stuck-query-holds-finish local-answer-kept-on-connection-failure untried-finish-cut-off left-behind-unsaid future-end-owes-finish owed-finish-waits-forever phase-local-error-holds-finish left-log-unsaid dead-session-wall-clock | `rs-` 72개. 남은 1개 `missing-bundle-kills-the-sender` 는 산출물이 PR5 라 그 PR 에서 | PR4 |
| 드레인 · 산출물 | m5q-worker-lane-exit-zero m5q-worker-follow-not-a-worker-exit m5q-worker-drain-does-not-wait m5q-worker-lane-claims-while-draining m5q-worker-drain-window-removed drain-waits-for-the-queue-forever left-bundle-unsaid | `rs-` 7개 + `rs-missing-bundle-kills-the-sender`. `m5q-worker-lane-exit-zero` · `m5q-worker-follow-not-a-worker-exit` 의 `rs-` 둘은 PR4(409 와 레인 종료는 등록 고리), 나머지 `rs-` 5개 + `rs-missing-bundle-kills-the-sender` 는 PR5. `m5q-worker-follow-not-a-worker-exit` 는 Rust 에서 「버전 409 가 앞단 exec 가 아니라 종료로 끝남」의 뜻으로 | PR4 (m5q-worker 둘) · PR5 |

이름 수를 더하면 5 + 19 + 24 + 73 + 7 = 128 이다. 표의 이름은 `MUTANTS` 에서 세 파일을 겨누는 변이와 스크래치 스크립트
`mutgroups.py` 로 대조했다. 빠진 것, 남는 것, 겹친 것이 모두 0 이다. `runner-long-line-never-cut`(러너)과
`long-line-never-cut`(워커)은 다른 변이다. Rust 변이 수는 PR2 가 5, PR3 이 43(18 + `rs-supervisor-env-leaks` 1 + 24), PR4
가 74, PR5 가 6 이고 합은 128 이다. PR2 시점에 MUTANTS 를 다시 읽어 표를 맞춘다. 그사이 변이가 늘었으면 더한다.

대응표 밖에 더하는 Rust 변이는 셋이다. 이름은 지금 grep 0건이다.

| 이름 | 무엇을 바꿈 | 빨개질 시험 | PR |
|---|---|---|---|
| `rs-restart-keeps-follow-env` | 다시 실행 때 `drop_env` 를 지우지 않음(Python `m5q-worker-restart-keeps-follow-env` 의 짝, `follow.py` 쪽 변이라 128 밖) | `test_rust_worker_m5u.py` RW21 | PR4 |
| `rs-follows-redirect` | HTTP 클라이언트가 3xx 의 `Location` 으로 다시 보냄 | `test_rust_worker_m5u.py` RW11 | PR4 |
| `rs-handoff-unknown-key-ignored` | 넘기기 문서의 모르는 키를 무시 | `test_rust_worker_m5u.py` RW32 | PR4 |

### 7.6 더할 Python 변이 (선택기 · 앞단)

| 이름(제안, grep 0건) | 무엇을 바꿈 | 빨개질 시험 | PR |
|---|---|---|---|
| `m5u-impl-falls-back-to-python` | `implementation = "rust"` 인데 바이너리가 없으면 Python 으로 계속 | `test_worker_launch_m5u.py` RW23 | PR4 |
| `m5u-handoff-token-in-argv` | 넘기기를 fd 대신 argv 로 | RW23 | PR4 |
| `m5u-selector-skips-missing-binary` | 선택기가 바이너리 없음을 skip 으로 | `test_e2e_worker_impl_m5u.py` RW26 | PR4 |
| `m5u-require-rust-ignored` | `RCM_REQUIRE_RUST=1` 인데 cargo 없음을 skip 으로 | `test_rust_build_m5u.py` RW30 | PR2 |
| `m5u-binary-follow-takes-redirect` | 바이너리 받기가 리다이렉트를 따름 | `test_worker_binaries_m5u.py` RW22 | PR6 |
| `m5u-binary-sha-unchecked` | sha256 대조 생략 | RW22 | PR6 |
| `m5u-golden-allows-any-key` | 골든 대조가 키 집합을 안 봄 | `test_worker_golden_m5u.py` RW10 | PR1 |
| `m5u-guard-misses-rcmw` | 가드가 `rcmw` 를 운영 설정과 함께 불러도 허용 | `test_guard_production_rcmw.py` RW24 | PR7 |
| `m5u-paired-config-runs-rust` | 앞단이 `RUST_REFUSED_KEYS` · `auto` 를 보지 않고 exec | `test_worker_launch_m5u.py` RW32 | PR4 |
| `m5u-knob-binary-everywhere` | 선택기가 모든 시험에 `test-knobs` 빌드를 줌 | `test_e2e_worker_impl_m5u.py` RW26 | PR4 |
| `m5u-fallback-on-bad-binary` | 서버가 준 바이너리의 sha256 이 틀리면 Python 으로 물러남 | `test_worker_binaries_m5u.py` RW22 | PR6 |
| `m5u-impl-marker-defaults-python` | 서버가 키 없는 워커를 `python` 으로 채움 | `test_worker_impl_marker_m5u.py` RW35 | PR7 |
| `m5u-gate-after-discovery` | `_worker_config` 의 관문 한 줄을 발견 갈래(`if cfg.wants_discovery:`) 뒤로 옮김 | `test_worker_launch_m5u.py` RW32 (b) — `cli._main` 흐름에서 `discover` 호출 1회 | PR4 |
| `m5u-version-lock-skips-cargo-lock` | `version_mismatches` 가 `rust/Cargo.lock` 의 `rcmw` 항목을 안 봄 | `test_rcmw_version_lock_m5u.py` RW20 (b) — lock 만 틀린 tmp 트리 | PR2 |
| `m5u-release-ignores-rcmw-smoke` | `release.yml` 의 `github-release` `needs` 에서 `rcmw-smoke` 를 뺌 | `test_ci_release_workflow.py` RW33 | PR6 |

## 8. 개인정보 점검 (dev 에서 main 으로 가는 릴리스 PR 때)

- 골든 전사와 디스크 골든에는 녹화한 머신의 홈 경로, 호스트 이름, 사용자 이름, `host_key`, `IOPlatformUUID` 해시,
  `machine-id` 가 들어갈 수 있다. 녹화 도구가 이 값을 자리표로 바꾼다. `tests/test_worker_golden_m5u.py` 는 골든에
  `/Users/` · `/home/` · 녹화 머신의 `socket.gethostname()` · 세션 id 자리표 밖의 32자 이상 hex 가 0건인지 단언한다(PR1).
- 실기 보고서(PR8)는 운영 머신 이름과 계정을 쓰지 않는다. 기존 보고서의 관례다.
- 바이너리 자산과 `SHA256SUMS` 에는 빌드 머신 경로가 박힐 수 있다. Rust 가 패닉 메시지에 소스 경로를 넣기 때문이다. 빌드에서
  `--remap-path-prefix` 로 지우고 PR6 의 시험이 릴리스 바이너리 바이트에 `/Users/` · `/home/` 이 0건인지 단언한다.
- 그 밖은 늘 하던 main PR 점검이다. 레포 밖 패턴 파일, gitleaks, 커밋 이메일을 본다.

## 9. 위험

| 위험 | 막는 것 |
|---|---|
| 두 구현이 미묘하게 갈림(시간 · 순서) | 와이어 · 디스크 골든, 공유 벡터, e2e 매트릭스, 교차 kill -9 e2e, live lane(§7.1), RL 대응표 빈 칸 0 |
| 워커를 고칠 때 한쪽만 고침(두 구현 유지 비용) | 골든 · 벡터가 둘 다 대조 — 한쪽만 고치면 빨강(§4.10). 이 비용은 Python 구현을 남기는 한 계속된다 → Q12 에서 언제 Python 워커를 접을지 함께 묻는다 |
| rcm-ci 가 cargo 없이 Rust 시험을 조용히 건너뜀 | `RCM_REQUIRE_RUST=1`(결정 369), 래퍼의 N > 0 단언, PR2 머지 조건(잡 로그의 cargo 시험 수) |
| 시험이 Rust 를 골랐는데 Python 이 돌아 통과 | 선택기의 실행 파일 단언(RW26), 변이 `m5u-selector-skips-missing-binary` · `m5u-impl-falls-back-to-python` |
| 공급망(크레이트) | 허용 목록 + `Cargo.lock` + `--locked` + 잠금 시험(결정 368), tar · HTTP 는 직접 씀 |
| rcm-ci 시간 증가로 필수 체크가 느려짐 | 데운 `CARGO_TARGET_DIR`, 시간을 PR2 · PR4 에서 재서 §13 에 적음. 예산을 넘으면 그 PR 에서 오너에게(새 질문) |
| `flock` 과 `fcntl` 락이 서로 안 보여 산 세션을 죽임 | `libc::flock`(F19), RW2 |
| Rust `Command` 의 env 상속 · SIGPIPE 무시 · fd 상속 | `env_clear` · 자식에서 처분 되돌림 · fd 닫음(§4.7), RW7 |
| exec 뒤 macOS Local Network 권한 주체가 바뀜 | RW29 에서 잼, 문서 · doctor · 첫 등록 실패 로그(F45) |
| Linux 증거 없이 Linux 바이너리 배포 | Q8, 미검증 target 은 목록에서 뺌(RW30) |
| 서버를 올렸는데 바이너리가 없어 Rust 워커가 멈춤(rcm-ci 의 `mac2` 포함) | Q4, 서버 `rcm check` 경고, 드레인으로 잡은 안 잃음(RW20) |
| M5t-c PR3 과 M5u 가 워커 파일 · 와이어를 같이 바꿈 | 장치(골든 = PR1, 키 잠금 = PR4)가 생긴 뒤 머지하는 쪽이 맞춤(§4.10 의 세 경우), 파일 겹침은 §4.10 표 · 함께 열리면 M5t-c 먼저. 잠금 · 골든이 빨개서 잊을 수 없다. M5t-c §13 문장 |
| 토큰 노출(넘기기 · 잡 env · git 자격) | 이름 없는 0600 파일의 fd, env 비움, RW7 · RW17 · RW23, 변이 `m5u-handoff-token-in-argv` |
| 릴리스 바이너리에 시험 진입점 | `test-knobs` 는 따로 빌드, 바이트 검사(RW26 d) |
| 짝지은 머신에서 Rust 가 짝짓기 검증 없이 토큰을 보냄 | Rust 가 모르는 설정 · `auto` 는 앞단이 기동 전 거절, 요청 0건(RW32), 변이 `m5u-paired-config-runs-rust` |
| 배포 바이트가 시험한 바이트와 다름 | 태그 빌드가 같은 파일로 스모크한 뒤에만 올림, sha256 둘 다 로그(RW33) |
| Python 의 로그 · `Traceback` 단언이 Rust 에서 공허하게 통과 | 벡터 `log_contract`(RW34), Rust 판 teardown 의 `panicked at` 0(RW26) |
| 리다이렉트를 따르면 토큰이 다른 호스트로 | Rust 는 따르지 않음 · 한 줄(RW11, Q11), 변이 `rs-follows-redirect` |
| 「진짜 Rust」 확인이 exec 전 앞단을 봄 | 확인은 등록 뒤 · kill 직전(§4.1, F53) |
| auto · 빈 `server` 머신이 발견 뒤 명시 URL 을 가져 Rust 관문을 지나감(`discovered_server` 는 동적 속성이라 잠금이 못 봄) | 관문을 `_worker_config` 의 발견 갈래 앞에 둠(F64), RW32 (b) 를 실제 `cmd_worker` 흐름으로, 변이 `m5u-gate-after-discovery` |
| 골든이 버전 · 머신마다 빨개 사람들이 재녹화를 습관으로 함(골든이 아무것도 못 잡게 됨) | `typed_values` 로 빌드 · 머신 값은 타입만, `version` 은 빌드 버전과 따로 단언(F68, RW10) |
| 릴리스가 `Cargo.toml` 만 올려 `--locked` 빌드가 깨지거나 버전이 어긋남 | cargo 없이 도는 세 값 글 잠금 · CONTRIBUTING 1단계 문장(F66, RW20 b) |
| 바이너리 스모크가 빨간데 wheel 만 나가 Rust 워커가 다음 서버 버전에서 바이너리를 못 받음 | `github-release` · `pypi` 가 `rcmw-smoke` 를 기다림(Q3 (i), RW33) |

## 10. 요청서에 돌려줄 것

없다. 요청자는 오너 자신이고 이 세션에서 말했다.

## 11. 결정 표

「오너 확인」 칸은 확정(날짜), 추천, ⛔ 가운데 하나다. 추천은 이 명세 PR 의 머지로 승인된다. 오너가 따로 볼 것은 ⛔ 행이다.
⛔ 행은 「정한 것」 칸이 가리키는 질문(Q)으로 결정 페이지에 오른다. 질문은 §11.1 에 있다. 「요청과 다름」 칸에는 §1.2 의
번호만 적는다.

| # | 항목 | 정한 것 | 오너 확인 | 요청과 다름 |
|---|---|---|---|---|
| 360 | 동치의 정본 | pinned sha 의 Python 동작(코드 + m5tb §13). 커밋된 와이어 · 디스크 골든 + 공유 벡터 + 녹화 도구(`TcpRelay` 의 기록 옵션, F65), Python 도 골든과 대조. 빌드 · 머신 값은 타입만(`typed_values`), `version` 은 빌드 버전과 따로 단언(F68). 버린 안: 명세 문장을 정본으로(코드와 다른 곳이 이미 승인된 §13 으로 기록됨), 시험 때만 두 구현을 비교(리뷰할 diff 가 안 남음), 값까지 골든에 담기(버전 · 머신마다 빨강) | 추천 | — |
| 361 | 범위 안의 순서와 PR 분할 | Q18. 추천: §5 의 PR1~PR9, 감독자 먼저(사슬 검증), 러너 단독 교체 버림(F3), 워커는 세 층(PR3 · PR4 · PR5) | ⛔ | §1.2 의 6 |
| 362 | 구현 선택 | `implementation`(기본 `python`) · `rust_binary` 키와 env `RCM_WORKER_IMPLEMENTATION` · `RCM_WORKER_RUST_BINARY`, PATH 안 봄. 바이너리 · 설정 문제는 기동 전 종료 2(§4.3 의 목록), Python 으로 도는 길은 365 의 답 하나. 시험 선택기 `RCM_TEST_WORKER_IMPL` 은 tests 만. 키 · env 이름은 머지 뒤 얼리므로 Q13 에서 바이너리 이름과 함께 묻는다 | ⛔ (Q13) | — |
| 363 | 실행 형태 | Q5 | ⛔ | §1.2 의 1 |
| 364 | 배포 채널 | Q3 | ⛔ | — |
| 365 | 도중 버전 변경에 바이너리 없음 | Q4. 「없음」은 서버의 404 `no_worker_binary` 하나로만 안다 | ⛔ | — |
| 366 | 서명 · 격리 | ad-hoc 서명, 공증 없음, 브라우저로 받은 경우의 `xattr` 안내. 격리 속성이 안 붙는지는 PR8 에서 잼 | 추천 | — |
| 367 | Cargo 자리와 도구 사슬 | `rust/` 작업 공간 · `rust/rcmw/` · `rust-toolchain.toml` 한 버전 · `Cargo.lock` 커밋 · `--locked`. 버전 잠금은 세 값(`__version__` · `rust/rcmw/Cargo.toml` · `Cargo.lock` 의 `rcmw` 항목)을 cargo 없이 대조하는 글 잠금 + `rcmw --version` 빌드 잠금, CONTRIBUTING 「Releasing」 1단계가 셋을 함께 올림(F66). 버린 안: build.rs 가 `__init__.py` 를 읽어 버전을 넣음(새 장치, `Cargo.toml` 버전이 거짓이 됨) | 추천 | — |
| 368 | 크레이트 허용 목록 | Q6 — PLAN 의존성 원칙에 닿는다 | ⛔ | — |
| 369 | rcm/ci 의 cargo | Q7 — 운영 머신 · 운영 프리셋을 바꾼다. 추천: pytest 래퍼 + `RCM_REQUIRE_RUST` + 데운 `CARGO_TARGET_DIR` + 맥미니 툴체인(오너). 건너뛰기는 모듈 `pytestmark` 의 `skipif(<식>)` 한 자리, `RCM_REQUIRE_RUST=1` 이면 수집 오류(§4.6) | ⛔ | — |
| 370 | Linux 증거 · 배포 target | Q8 | ⛔ | §1.2 의 3 |
| 371 | PLAN 의존성 원칙 문장 | Q10 — PLAN 결정 8 의 문장과 `AGENTS.md:8` · `CONTRIBUTING.md:37` 을 바꾼다(E2 · E3 · E5 · E6 · E7) | ⛔ | — |
| 372 | Rust 변이 | mutcheck `runner="cargo"` + `rs-<이름>` 대응(§7.5) + 대응표 밖 셋, Python 변이 유지, `cargo-mutants` 안 들임 | 추천 | — |
| 373 | 시험 훅 | 시계 · 가짜 서버는 `#[cfg(test)]`, e2e 조절은 `test-knobs` 기능 빌드만(`RCM_TEST_LOG_BATCH_BYTES`, 컴파일 때 `RCM_TEST_BUILD_VERSION`), 노브 시험은 `RUST_KNOB_TESTS` 에 이름으로, 릴리스 바이너리 바이트 검사 | 추천 | — |
| 374 | 구현 표식 | Q9 — (a) 면 와이어에 등록 키 하나(Rust 만 보냄) | ⛔ | — |
| 375 | `host.python` | 형태 A 는 앞단 값, 독립 실행은 `null`(서버가 받음 — `capabilities.py:146-158`) | 추천 | — |
| 376 | 공개 요약의 `error` 클래스 이름 | Python 예외 클래스 이름을 errno 대응표로 그대로(벡터 `error_class`) — 요약 문구 안 바뀜 | 추천 | — |
| 377 | Rust 워커의 HTTP 범위 | Q11(`https://` 와 3xx) | ⛔ | §1.2 의 2 |
| 378 | 기본 구현 전환 시점 | Q12 | ⛔ | §1.2 의 4 |
| 379 | M5t-c 와 순서 · 이식 범위 | Q2. 어느 답이든 장치(골든 = PR1, 키 잠금 = PR4)가 생긴 뒤 머지하는 쪽이 골든 · 거절 목록을 맞춘다. 파일이 겹치는 짝이 함께 열리면 M5t-c 먼저(§4.10) | ⛔ | §1.2 의 7 (기본 (a) 일 때) |
| 380 | 서버 로컬 레인 | Q17. 추천: Python `runner.run_job` · `job_supervisor.py` 그대로. 1판은 「확정」이었으나 오너 말의 해석이라 ⛔ 로 바꿈 | ⛔ | §1.2 의 5 |
| 381 | Windows 네이티브 | 범위 밖 | 확정(`PLAN.md:34`) | — |
| 382 | 실행 중 GitHub 에서 받기 | 안 함 | 확정(결정 30, `PLAN.md:668`) | — |
| 383 | 되돌리기 | 키 한 줄 + 재시작, 자동 폴백 없음(365 의 답만 예외) | 추천 | — |
| 384 | 바이너리 이름과 얼릴 이름들 | Q13 — 바이너리 이름, 설정 키 `implementation` · `rust_binary`, env `RCM_WORKER_IMPLEMENTATION` · `RCM_WORKER_RUST_BINARY`(결정 362) | ⛔ | — |
| 385 | 와이어 · 디스크 동결 | 프로토콜 2 는 핀의 꼴 그대로(M5t-c 추가분은 379 의 답), 디스크 파일 꼴 그대로, 더하는 키는 374 의 답뿐 | 추천 | — |
| 386 | PR 별 lane | Q1 | ⛔ | — |
| 387 | HTTP 연결 | 요청마다 새 연결 · `Connection: close` · `Content-Length` · 3xx 안 따름(377 의 답) · Python 과 같은 기한 | 추천 | — |
| 388 | 설정 넘기기(형태 A) | 이름 없는 0600 임시 파일의 상속 fd 에 JSON 한 문서(토큰 · `HANDOFF_KEYS` · `restart` 포함), argv 는 fd 번호와 원래 argv 꼬리(토큰 없음), Rust 가 읽고 닫음. 버린 안: 파이프(exec 전 막힘), argv · env 로 설정(노출 · 길이) | 추천 | — |
| 389 | 독립 실행의 TOML | Q16(Q5 가 (b) · (c) 일 때만) | ⛔ | — |
| 390 | 넘기기 키 잠금 · Rust 가 모르는 설정 | Q2 (a) 의 장치. `WorkerConfig` 필드 = `HANDOFF_KEYS` ∪ `FRONT_KEYS` ∪ `RUST_REFUSED_KEYS`(서로 겹치지 않음) 잠금, Rust 쪽 목록은 벡터 `handoff_keys`. 관문은 `_worker_config` 안 · 서버 찾기 앞(F64): 거절 목록의 필드가 기본값이 아니거나 `server` 가 비었거나 `auto` 면 종료 2 와 한 줄, mDNS 질의 0 · 요청 0건. Rust 는 모르는 넘기기 키에 종료 2. 버린 안: 모르는 키를 Rust 가 무시(짝짓기 검증 없이 토큰을 보냄), 관문이 `discovered_server` 를 읽음(발견이 이미 돔) | ⛔ (Q2) | §1.2 의 7 |
| 391 | 배포 바이트 = 스모크한 바이트 | Q3 에 딸림. 추천 (i): `rcmw-build` → `rcmw-smoke`(같은 산출물) → `github-release` 가 그 파일을 올림, `github-release` · `pypi` 의 `needs` 에 `rcmw-smoke`(스모크가 빨가면 wheel 도 안 나감 — fail-open 금지, F69). (i′) wheel 만 나가고 자산 0. 버린 안: 재현 빌드를 기대(macOS 빌드의 바이트 같음을 잰 적 없음) | ⛔ | — |
| 392 | 워커 로그 줄 계약 | e2e 가 단언하는 워커 로그 줄을 벡터 `log_contract` 로, 두 구현이 같은 바이트. Rust 판 teardown 은 `panicked at` 0 | 추천 | — |
| 393 | 409 · 드레인 뒤 다시 실행 | 앞단이 넘기기 문서 `restart` 에 argv(`[sys.executable, "-P", "-c", RESTART, *argv]`) · 지울 env · 시각 env 를 싣고 Rust 는 그대로 exec. 기동 409 는 따라가면 exec, 아니면 메시지와 종료 2. 버린 안: Rust 가 인터프리터를 찾음(서비스 PATH 에 따라 다른 Python), Rust 가 자기를 내려받음(결정 382 · 364) | 추천 | — |

### 11.1 결정 페이지에 올릴 질문

질문마다 선택지와 그 결과, 기본값 하나를 둔다. lane 질문(Q1)은 다른 질문이 없어도 늘 오른다.

| Q | 질문 | 선택지와 결과 | 기본(추천) |
|---|---|---|---|
| Q1 | PR 마다 full · short 중 무엇으로 도나(결정 386) | (a) §5 그대로 — PR1 · PR8 short, 나머지 full (b) 모두 full — PR1 · PR8 에도 실기 · 엣지 감사(시간이 늚, `src/` 0줄이라 찾을 것이 적음) (c) full 은 PR4 · PR5 · PR6 만, 나머지 short — 감독자(PR2) · 러너(PR3)의 신호 · 프로세스 그룹 엣지 감사가 빠짐 | (a) |
| Q2 | M5t-c 의 짝짓기 확인 고리를 M5u 의 Rust 가 옮기나(결정 379 · 390) | (a) 안 옮김 — Rust 가 모르는 설정(`pair_secret`)과 `server = "auto"` · 빈 `server` 는 서버 찾기보다 먼저 종료 2, 그 머신은 Python 워커. 순서는 묶지 않고 장치가 생긴 뒤 머지하는 쪽이 골든 · 거절 목록을 맞춤(§4.10 의 세 경우), 파일이 겹치는 짝이 함께 열리면 M5t-c 먼저. 결과: 짝지은 머신 · auto 머신은 M5u 에서 Rust 를 못 씀(§1.2 의 7), 프로토콜 2 동결이 맞음 (b) 옮김 — 새 PR10(M5t-c PR3 · M5u PR5 뒤): hello · nonce 헤더 · 거절 MAC 검증 · `pair_proof` · 상태 파일 · J1~J14 · A1~A10 · 다시 찾기 검증, HMAC 은 Q6. 결과: M5u 가 M5t-c 를 기다리고 커짐. PR10 은 지금 §5 · §6 · RW · 변이가 없는 자리표시자라 이 명세로는 지을 수 없다 — (b) 를 고르면 PR10 의 명세 보충 PR 과 그 plan 라운드가 먼저 (c) M5t-c 를 M5u 뒤로 — 짝짓기 보안이 늦어짐 | (a) |
| Q3 | Rust 바이너리를 워커 머신에 어떻게 보내나(결정 364 · 391) | (a) 서버가 `worker_binaries_dir` 의 자기 버전 바이너리를 `GET /worker/binary/<target>` 로 내주고 앞단이 따라감, 폴더는 운영자가 릴리스 자산 또는 `cargo build` 로 채움 (b) 머신마다 사람이 놓음, 따라가기 없음 (c) 머신마다 `cargo build`(그 머신에 툴체인 필요). 빌드 자리: (i) 태그 때 `release.yml` 의 `rcmw-build` 가 빌드하고 `rcmw-smoke` 가 같은 파일로 스모크한 뒤에만 `github-release` 가 올림. 스모크가 빨가면 `github-release` · `pypi` 도 멈춤 — wheel 도 안 나감 (i′) (i) 와 같되 wheel 은 나가고 바이너리 자산만 0, run 은 빨강 — 그 버전 `rcmw` 가 없으므로 Q4 의 길 (ii) 맥미니 rcm 잡이 빌드하고 메인 세션이 그 파일을 올림(수작업 단계). (i) · (i′) 는 설치 때의 GitHub 사용이 결정 30 과 맞는지도 함께 | (a) + (i) |
| Q4 | 서버를 올렸는데 서버가 그 버전 `rcmw` 를 갖고 있지 않으면 도는 Rust 워커는(결정 365) | (a) 서버가 404 `no_worker_binary` 로 답하면 드레인 뒤 앞단이 Python 워커로 계속, 한 줄 + 서버 `rcm check` 경고, 서버의 표식은 「—」. 다른 바이너리 문제는 모두 종료 2 (b) 그때도 드레인 뒤 종료 2 — 그 머신은 down(맥미니 `mac2` 면 rcm-ci 가 멈춤). 어느 쪽이든 도는 잡은 안 잃음 | (a) |
| Q5 | 실행 형태(결정 363) | (a) 형태 A 만(Python `rcm worker` 가 같은 PID 로 `rcmw` exec), 파이썬 없는 머신은 다음 마일스톤 (b) A 로 동치 증명 뒤 PR9 에서 독립 `rcmw run --config` 를 더함(`--check` · `setup` · `doctor` 는 Python 에 남아 그 머신에선 못 씀, TOML 은 Q16) (c) 독립만 | (b) |
| Q6 | Rust 크레이트 허용 목록(결정 368) | (a) `libc` · `serde` · `serde_json` · `sha2` · `miniz_oxide`(+ 그 전이 의존성), tar · HTTP 는 직접 씀 (b) (a) + `tar` · `flate2`(직접 쓰는 양이 줆, 면적 늚) (c) `libc` 만(JSON · sha256 · gzip 도 직접). Q2 가 (b) 면 HMAC 을 `sha2` 위에 직접 쓸지 `hmac` 크레이트를 더할지도 | (a), Q2 (b) 면 HMAC 직접 |
| Q7 | 맥미니에 Rust 툴체인을 깔고 운영 프리셋 `rcm-ci` 를 고쳐도 되나(결정 369) | (a) `mac2` 워커 사용자에 rustup + 고정 버전, 프리셋 env `RCM_REQUIRE_RUST = "1"` · `CARGO_TARGET_DIR` · PATH, `requires` 에 `cargo` — 오너가 함 (b) Rust 시험은 필수 체크 밖(빌드 머신을 안 바꿈, 대신 Rust 동치가 머지 조건이 아님) | (a) |
| Q8 | Linux(와 x86_64) 증거를 어디서 얻나(결정 370) | (a) Linux 머신을 원격 워커로 붙이고 Rust 시험 묶음 프리셋 (b) 맥미니에 VM · 컨테이너 도구 설치 (c) `ci.yml` ubuntu 를 머지 전 손으로(2026-11-20 결정과 부딪힘) (d) 이번엔 macOS arm64 만 배포, Linux · x86_64 워커는 Python 그대로 | (d), Linux 머신이 있으면 (a) 를 더함 |
| Q9 | 서버가 워커의 구현을 알게 하나(결정 374) | (a) Rust 만 보내는 등록 최상위 키 `worker_impl`, 서버 메모리 · 토큰 있는 머신 문서 · `rcm machines` 에 「rust」/「—」, Python 워커 · 호스트 사실 · 잠금 시험 · DB · `/api/status` 안 바뀜(PR4 보냄 · PR7 서버) (b) 등록 키 + DB 칸 + `/api/status`(DB 버전 올림, M5t-c 의 DB 27 과 겹침) (c) 안 함 — 증거는 실행 파일 검사와 워커 로그 첫 줄뿐 | (a) |
| Q10 | 의존성 원칙을 어떻게 고치나(결정 371) | (a) 「wheel 은 런타임 의존성 0 그대로, 워커 바이너리 `rcmw` 는 별도 산출물이고 `Cargo.lock` 에 고정한 허용 목록 크레이트만」 — `PLAN.md`(E2 · E3 · E5)와 `AGENTS.md:8` · `CONTRIBUTING.md:37`(E6 · E7, PR2). `README.md:34` 는 패키지 이야기라 그대로 (b) 원칙을 안 고치고 Rust 를 표준 라이브러리 + `libc` 만으로(Q6 c 와 같음) | (a) |
| Q11 | Rust 워커의 HTTP 범위: `https://` 와 리다이렉트(결정 377) | (a) 둘 다 이번엔 안 함 — `https://` 면 `implementation = "rust"` 기동 전 종료 2 와 한 줄, 3xx 는 따르지 않고 백오프 + 한 줄(Python 은 둘 다 됨, F55). 운영의 `mac2` 는 명시 `http://` 라(F49) 지금 닿는 것이 없음 (b) `rustls` 계열 크레이트를 들이고 3xx 는 urllib 처럼 따름(면적 큼, 토큰이 다른 호스트로 갈 수 있음) (c) 시스템 TLS(`Security.framework` · OpenSSL 동적) — 플랫폼마다 다름. 리다이렉트만 따로: (a1) 안 따름 (a2) 같은 호스트의 GET 만 따름 | (a) + (a1) |
| Q12 | 기본 구현을 언제 Rust 로 바꾸고 Python 워커를 언제 접나(결정 378) | (a) M5u 에서는 안 바꿈. 맥미니 `mac2` 를 Rust 로 일정 기간(예 2주) 돌린 뒤 따로 정함 (b) M5u 릴리스에서 기본을 rust 로(Python 은 되돌리기용) (c) 영영 선택으로 | (a) |
| Q13 | 바이너리 이름과 얼릴 설정 이름(결정 384 · 362) | (a) 바이너리 `rcmw`, 워커 설정 키 `implementation` · `rust_binary`, env `RCM_WORKER_IMPLEMENTATION` · `RCM_WORKER_RUST_BINARY`, 플래그 없음(얼릴 CLI 표면을 줄임) (b) 바이너리 `rcm-worker`(systemd 단위 이름 · 가드의 서비스 단어와 겹침, F44), 키 · env 는 (a) 와 같음 (c) 다른 이름(오너가 적음) | (a) |
| Q14 | (메인 세션, 운영 읽기) 운영에 붙은 워커들의 OS · arch, `https://` · `auto` 사용 | 읽기 전용 길 | 답함(§3.5 F47~F49) |
| Q16 | 독립 실행(PR9)의 TOML 을 무엇으로 읽나(결정 389, Q5 가 (b) · (c) 일 때만) | (a) `toml` 크레이트(+ 전이 의존성, 수는 —)를 PR9 에서 허용 목록에 더함. TOML 문법 오류의 글은 Python `tomllib` 와 다를 수 있어 종료 2 와 `<경로>: ` 머리만 같게(허용 차이) (b) `worker.toml` 이 쓰는 부분 문법만 직접 읽고 그 밖은 거절 — 크레이트 0, 직접 쓰는 양이 늚 (c) PR9 를 빼고 Q5 를 (a) 로 | (a) |
| Q17 | 서버 로컬 레인의 러너 · 감독자도 Rust 로 하나(결정 380) | (a) Python 그대로 — 로컬 레인은 서버 프로세스 안의 함수(`runner.py:3-5`), 오너 「서버와 CLI 는 Python 으로 둡니다」 (b) 로컬 레인의 표식 잡만 서버가 찾은 같은 버전 `rcmw supervise` 로 감쌈 — `runner.supervised_argv` 가 고르는 argv 만 바뀜, 새 PR 하나(서버에 `rcmw` 가 있어야 함) (c) 로컬 레인 잡을 `rcmw` 로 돌림 — 서버와 `rcmw` 사이 IPC(결정 361 이 버린 길) | (a) |
| Q18 | 세 조각 안의 순서(결정 361) | (a) 위험 순 — 감독자(PR2) → 러너 · 세션 파일(PR3) → 워커 고리(PR4~) — 첫 PR 의 이점이 가장 작음(F6), Rust 사슬을 가장 작은 프로세스로 먼저 검증 (b) PR2 와 PR3 을 한 PR 로 합쳐 워커 고리에 빨리 닿음 — 첫 PR 이 커지고 사슬 검증과 러너 검증이 한 번에 | (a) |

1판 부록의 Q15(cargo 빌드 시간과 `rcmw` 크기)는 질문이 아니라 측정이라서 §3.6 으로 옮겼다. 그래서 질문 번호는 Q14 다음이
Q16 이다.

## 12. 격리 반박에서 바뀐 것

3판은 두 격리 검증자의 2라운드 지적을 아래처럼 반영했다. id 규칙은 아래 1라운드 표와 같다. 「결정」 칸은 3판 번호다.

| id | 등급 | 2판 | 3판 | 결정 |
|---|---|---|---|---|
| V2-1 · V1-1 | HIGH · MED | 관문이 `cfg.wants_discovery` 를 보는데 `_worker_config` 가 먼저 mDNS 로 `server` 를 덮고 `discovered_server` 를 단다 — 실제 `cmd_worker` 흐름에서 auto 머신이 토큰과 함께 Rust 로 감, `fields()` 잠금은 동적 속성을 못 봄, RW32 (b) 를 통과할 수 없음 | 관문을 `_worker_config` 안 · `load_worker_config` 뒤 · 발견 갈래 앞(`cli.py:4765` 과 `:4766` 사이)에 한 줄로, `--check` 도 같은 관문. RW32 를 `cli._main` 흐름으로 돌려 `discover` 0회 · `execve` 0회 단언, 변이 `m5u-gate-after-discovery`(F64, §4.4) | 390 |
| V1-2 | MED | 골든 허용 차이에 `version` 과 `host` 블록이 없음 — 버전을 올릴 때마다, 다른 머신에서 빨강 | `typed_values`: `version` · `host_name` · `host` 아홉 값 · `host_sample` · 능력 보고 값은 타입만, `version` 은 빌드 버전과 따로 단언(F68, §4.1, RW10) | 360 |
| V1-3 | MED | 새 키 `implementation` · `rust_binary` 가 잠금의 어느 쪽인지 없음 | 셋째 집합 `FRONT_KEYS`(앞단만 씀 — `implementation` · `rust_binary` · `path`), 세 집합은 서로 겹치지 않음, Rust 쪽 목록은 벡터 `handoff_keys`(F23 · F62, §4.4, RW32) | 390 |
| V1-4 · V2-3 | MED | M5t-c 와의 머지 순서가 PR4 대 PR3 뿐 | §4.10 에 겹침 표(M5u PR4 · PR6 · PR7 · PR9 × M5t-c PR1~PR5), 함께 열리면 M5t-c 먼저 · 나중 쪽이 맞춤(F67), §5 머지 순서 칸 · §6 8 번 | 379 |
| V2-2 | MED | 골든을 「`tests/tcp_relay.py` 가 적는다」고 했으나 그 중계는 바이트만 넘김 | `TcpRelay` 에 덧붙이기만 하는 `record_requests=True`(기본 꺼짐, `recorded_requests`, 64 KiB 넘는 본문은 길이 · sha256 만), PR1 shared 에 `tests/tcp_relay.py`(F65) | 360 |
| V2-4 | MED | `Cargo.toml` 만 올리면 `--locked` 실패, `Cargo.lock` 을 함께 올린다는 말 없음 | 세 값 글 잠금 `tests/test_rcmw_version_lock_m5u.py`(cargo 없이), CONTRIBUTING 1단계 문장을 셋으로, 변이 `m5u-version-lock-skips-cargo-lock`(F66, §4.6, RW20) | 367 |
| V2-5 | MED | RW26 의 「서버 쪽 구현 표식」이 PR4~PR6 인데 서버가 읽는 것은 PR7 | RW26 에서 그 절을 빼고 RW35(PR7)로 넘김 | 374 |
| V1-5 · V2-6 | LOW | F1 이 옛 결정 356 | 361 | — |
| V1-6 | LOW | F57 의 「(R-1)」 정의 없음 | 지움(2판 재료의 규칙 번호였음) | — |
| V1-7 | LOW | RW12 (b) 산출물 PUT 이 PR4 | PR 칸을 나눔: 스냅샷 GET PR4, 산출물 PUT PR5 | — |
| V1-8 · V2-10 | LOW | 362 · 390 이 공개 표면을 얼리는데 「추천」, 390 의 요청과 다름 「—」 | 362 ⛔ — 이름은 Q13 을 넓혀 함께 물음. 390 ⛔(Q2 의 장치), 요청과 다름 §1.2 의 7 | 362 · 384 · 390 |
| V1-9 | LOW | 선택기가 모든 e2e 에 `RCM_WORKER_RUST_BINARY` 를 내보내 RW21 의 서버가 준 바이너리를 막음 | PR6 의 따라가기 e2e · 대체 시험은 그 env 를 빼고, 「진짜 Rust」 기대 경로 · sha256 은 캐시 파일(§4.1, RW21) | 373 |
| V1-10 | LOW | Q2 (b) 의 PR10 은 자리표시자 | Q2 (b) 결과 칸과 §4.10 에 「이 명세로는 지을 수 없고 명세 보충 PR 과 plan 라운드가 먼저」 | 379 |
| V1-11 | LOW | §3.6 에 빌드 시간 · 크기 행이 두 번 | 한 행으로 | — |
| V2-7 | LOW | §4.10 순서 규칙의 틈 — M5t-c PR3 이 PR1 과 PR4 사이에 머지되면 거절 목록 파일이 아직 없음 | 규칙을 PR 이 아니라 장치(골든 = PR1, 키 잠금 = PR4)마다 적고 §13 문장을 세 경우 표로 | 379 · 390 |
| V2-8 | LOW | PR7 이 `README.md` 를 고치는데 `README.ko.md` 없음 | PR7 shared 에 더함(F70) | — |
| V2-9 | LOW | 바이너리 스모크가 빨가면 wheel 릴리스도 멈추는지 미정 | Q3 (i) 의 기본은 `github-release` · `pypi` 의 `needs` 에 `rcmw-smoke`(fail-open 금지), (i′) 는 wheel 만 나감. 변이 `m5u-release-ignores-rcmw-smoke`(F69, §4.5, RW33) | 391 |
| V1-u · V2-u | 미확인 | 셸이 없어 scenario_trace · 변이 128 · `wc` 수치를 못 돌림, 스냅숏의 `__init__.py` 가 0.11.1 | HEAD 87e4817 의 `src/` · `tests/` 가 d07b253 과 같음(빈 diff), `__init__.py:7` 0.12.0 · `store.py:88` 26, `git diff --stat 9c6de32 d07b253` 7파일, 변이 728 · 104 + 19 + 5 = 128, `wc -l` 합 7,253 을 다시 잼(§3 머리). scenario_trace 는 plan-lead 가 돌림. Rust 쪽 미측정 동작은 그대로 「미측정」(§3.6) | — |

2라운드 검증자의 제안 가운데 받지 않은 것은 없다.

2판은 두 격리 검증자의 1라운드 지적을 아래처럼 반영했다. id 의 V1 은 검증자 1, V2 는 검증자 2 이고 뒤의 숫자는 그 검증자의
지적 번호다. u 는 미확인 항목이다. 두 검증자가 같은 것을 지적했으면 한 행으로 묶었다. 「결정」 칸은 2판 번호이고 「1판」 칸에 나오는 결정 번호는 1판 번호
그대로다.

| id | 등급 | 1판 | 2판 | 결정 |
|---|---|---|---|---|
| V1-1 · V2-10 | HIGH · MED | Q9 (a) 가 호스트 사실에 `worker_impl` 을 더함 — `HOST_KEYS == HOST_FACT_KEYS` 잠금과 「`remote_worker.py` 를 안 고침」이 모순, Python 은 안 보내 null | 표식은 Rust 만 보내는 등록 최상위 키, 서버가 따로 기억, 없으면 「—」. Python 워커 · 호스트 사실 · 잠금 그대로(F52, RW35) | 374 · Q9 |
| V1-2 · V2-1 · V2-3 | HIGH · HIGH · MED | Q2 (a) 「Rust 가 M5t-c 확인 고리까지」와 「프로토콜 2 그대로」 모순, 옮길 PR · RW · 크레이트 · 변이 없음, M5t-c PR3 과 골든 재녹화 교착 | Q2 기본을 「옮기지 않음 + 모르는 설정 · auto 기동 전 거절」로. 순서를 묶지 않고 나중 머지 쪽이 골든 · `RUST_REFUSED_KEYS` 를 맞춤, M5t-c §13 문장. (b) 에 PR10 범위 · HMAC · 크레이트 질문(F61 · F62, RW32, §4.10) | 379 · 390 · Q2 |
| V1-3 · V2-5 | MED | PR2 가 `test_job_supervisor_m5tb.py` 의 띄우는 줄만 바꿔 두 구현으로 | 그 파일은 0줄. 새 `test_rust_supervisor_m5u.py` 가 argv 계약 7개를 같은 단언으로, 훅 2개는 cargo, 러너 쪽은 PR3 cargo. `runner.py` 안 고침(F51, RW6, §7.4) | 361 |
| V1-4 · V2-12 | MED · LOW | RW21 e2e 가 PR5 인데 둘째 버전 `rcmw` 와 바이너리 배포는 PR6. RL13 · RL32 가 PR5 인데 등록 409 는 PR4 | 409 처리 · 다시 실행 장치를 PR4 로(RL13 · RL32 cargo), 드레인 고리는 PR5, RW21 · RL32 e2e 는 PR6(`RCM_TEST_BUILD_VERSION` 빌드) | 393 · 373 |
| V1-5 · V2-4 | MED | 근거 칸의 F 번호가 m5tb 의 것인데 이름이 없음, F2b 없음 | 「m5tb F…」로 이름 붙임, F2b 지움, 이 명세 F 를 함께 적음, F50(산출물 처분) 더함(§7.1) | — |
| V1-6 · V2-14 | MED · LOW | §1.2 넷인데 §11 칸이 더 있음, 결정 356 의 재해석, 결정 375 「확정」 근거가 해석, 로컬 레인을 Python 에 두는 것이 workplan_diff 없이 좁힘 | §1.2 를 일곱으로(5: 로컬 레인, 6: 순서, 7: 짝지은 · auto 머신), §11 칸은 §1.2 번호만, 380 을 ⛔(Q17), 361 을 ⛔(Q18) | 361 · 380 · Q17 · Q18 |
| V1-7 | MED | E1~E5 는 PLAN 만, `AGENTS.md:8` · `CONTRIBUTING.md:37` 의 「표준 라이브러리만」과 모순 | E6 · E7 을 PR2 shared 로(Q10 a), `README.md:34` · `PLAN.md:537` 은 패키지 이야기라 그대로(F58) | 371 · Q10 |
| V1-8 · V1-13 | MED · LOW | shared 누락: `examples/server.toml` · `examples/worker.toml` · PR1 의 `scripts/mutcheck.py` · `CHANGELOG.md`, PR3 의 `lib.rs`, PR5 의 `worker.rs` · `client.rs` | §5 표에 더함(F59) | — |
| V1-9 · V2-6 | MED | 기존 e2e diff 가 conftest 와 `SessionProc` 하나라고 함, `WRAPPER` 가 모듈 상수를 바꿈, 「진짜 Rust」 확인이 `SessionProc`(서버도 띄움 · exec 전), 32개에 m5q 6개가 듦, outbox RL12-b 는 설치본 부트로더 | 확인은 `WorkerProc.start` 등록 뒤 · `kill` 직전, `WRAPPER` 한 줄, exec argv 꼬리(teardown `pgrep`), Rust 판 25개 + `RUST_REPLACED` 7개(PR6 대체), `RUST_KNOB_TESTS`(F53, RW26, §7.3) | 373 · 388 |
| V1-9 · V1-11 | MED | 매트릭스가 릴리스 바이너리인지 노브 빌드인지 미정, 배포 바이트(GitHub)와 시험 바이트(맥미니)를 묶는 행 없음 | 매트릭스는 릴리스 프로필, 노브는 이름 목록만. 태그 빌드가 같은 파일로 스모크한 뒤에만 올림(F60, RW33) | 373 · 391 · Q3 |
| V1-10 · V2-2 | MED | Q5 (b) 의 PR9 는 TOML 을 읽는데 Q6 에 `toml` 없음, 번호 없이 미룸 | Q16 으로 따로(결정 389), PR9 shared 에 `Cargo.lock` · `test_rust_deps_m5u.py` | 389 · Q16 |
| V1-12 | LOW | 핀 9c6de32(v0.11.1), F14 「진행 중」 | 핀 d07b253(v0.12.0 출시 · 반영 끝), 줄 번호 다시 확인(바뀐 곳 없음), F14 고침 | — |
| V1-14 | LOW | 「재료에 없음」 · 「—(못 잼)」이 §3.6 밖, Q15 가 질문이 아니라 측정 | §3.6 에 행으로, Q15 를 §3.6 으로(§3.5 꼬리 · §7.1 머리) | — |
| V2-7 | MED | e2e 가 워커 로그 글을 단언하는데 동치 장치에 없음, `"Traceback" not in log` 가 Rust 에서 공허 | 벡터 `log_contract`(RW34), Rust 판 teardown 의 `panicked at` 0(F54) | 392 |
| V2-8 | MED | Python `WorkerClient` 는 리다이렉트를 따르는데 Rust 는 안 따름 — 요청과 다른 곳에 없음 | §1.2 의 2 에 넣고 Q11 에 묶음, RW11 에 3xx 행, 변이 `rs-follows-redirect`(F55) | 377 · 387 · Q11 |
| V2-9 | MED | 「최대 다음이 351」이라며 355 부터, M5t-b 가 351~359 를 비워 둠 | 360~393 으로(머리 인용, F57) | — |
| V2-11 | MED | 드레인 뒤 exec 의 인터프리터 경로 · `RESTART` · 지울 env 가 없음, 「바이너리 없음 종료 2」와 Q4 (a) 를 가르는 법 없음 | 넘기기 문서 `restart`(F56), Python 으로 도는 길은 서버의 404 `no_worker_binary` 하나(§4.3) | 362 · 365 · 393 |
| V2-13 | LOW | `pair_proof` 자리 `:685-686` | `:686-687`(F13) | — |
| V2-15 | LOW | cargo 없고 `RCM_REQUIRE_RUST` 없으면 런타임 skip 인데 trace 가 WEAK 로 봄, 어느 시험인지 미정 | 모듈 `pytestmark` 의 `skipif(<식>)` 한 자리, 대상 모듈을 이름으로, `RCM_REQUIRE_RUST=1` 이면 수집 오류(F63, §4.6) | 369 |
| V2-u1 | 미확인 | F47~F49(운영 사실) | 메인 세션이 v0.12.0 반영 뒤에도 같다고 보증(§3.5) | — |
| V2-u2 | 미확인 | Rust 동작 주장(`process_group` · fd 상속 · SIGPIPE · 크레이트 수) | 「미측정, PR2 · PR3 첫 빌드에서 잰다」로 표시(F16 · F17, §3.6) | — |

1라운드 검증자의 제안 가운데 받지 않은 것은 없다. 「Q2 를 Q6 · Q9 보다 먼저 정한다」는 제안은 결정 페이지의 순서 문제라서
메인 세션에 넘긴다.

아래는 1판에서 scenario-author 표를 바꾼 것이다.

| id | 무엇 | 표(원본) | 이 재료 | 이유 |
|---|---|---|---|---|
| S-1 | 행 하나에 결과 여럿 | (a)(b)… 여러 결과 | 꼴 유지 | 30행을 결과 하나씩 쪼개면 100행이 넘고 RL 처럼 묶음 단위로 읽힌다. 시험은 `# RW4` 에 하위 글자를 주석으로. 검증자가 WEAK 를 주면 그 행만 쪼갠다 |
| S-2 | PR 칸 | 모두 `?` | §5 의 PR 로 채움 | — |
| S-3 | 「undecided (owner, 질문 N)」 | 저자 질문 번호 | 이 재료의 Q·번호 · 결정 번호 | 질문을 다시 묶음(표 아래 대응) |
| S-4 | RW26 (a) 「시험 파일 수정 0(diff 0)」 | diff 0 | 「시험 함수 수정 0, diff 는 conftest 선택기 · `SessionProc` 확인 한 곳」 | 「진짜 Rust」 단언을 하려면 띄우는 자리 한 곳은 고쳐야 한다(F28). m5q 의 6개는 다른 길(F29) |
| S-5 | RW27 「18개 파일」 · 「변이 80줄」 | 18 · 80 | 14개 파일 · 128개 변이 | 잰 값(F30 · F33) |
| S-6 | RW16 · RW19 의 미정 | 소유자 질문 11 · 9 | 결정 376 · 375 추천으로 닫음 | 서버가 `python: null` 을 받고(F27) 클래스 이름은 대응표로 그대로 낼 수 있어 오너에게 물을 것이 아님 |
| S-7 | 셋째 제외 줄(같은 pid · 같은 시작 문자열) | 제외 | RW31 행 | 되돌릴 수 없는 결과는 드물어도 행(edge-scope) |
| S-8 | RW29 어디서 칸 | `live(...)` | `… (live)` 로 끝냄 | 추적 도구가 칸 끝의 ` (live)` 만 실기 행으로 읽는다(`tools/scenario_trace.py:17-18`) |
| S-9 | RW12 (a) | 「풀 연결 재사용 거짓 실패 0」 | 「요청마다 새 연결이라 0」 | Python 이 풀을 안 씀(F8) — 같은 규칙을 결정 387 로 |
| S-10 | 저자 질문 1 · 3 · 10 | 질문 | RESOLVED(R1 · R2 · R3) | 근거 `file:line` 이 있음(out.json questions) |

scenario-author 의 질문 13개는 이 명세에서 아래로 옮겼다. R 번호는 근거가 있어 질문 없이 닫은 것이다. R1 은 동치의 정본을
코드와 m5tb §13 으로 정했고(결정 360), R2 는 서버 로컬 레인을 Python 에 두었으며(결정 380), R3 은 Windows 네이티브를 범위
밖에 두었다(결정 381). 이 가운데 R2 는 2판에서 질문 Q17 로 바뀌었다. 오너 말을 해석한 것이라서다(§1.2 의 5).

| scenario-author 질문 | 이 명세 |
|---|---|
| 1 | R1 · 결정 360 |
| 2 | Q2 |
| 3 | Q17 · 결정 380 |
| 4 | Q3 · Q4 |
| 5 | Q5 |
| 6 | Q5(형태 A 는 Python 이 풂 · 결정 388) |
| 7 | 결정 373 |
| 8 | Q9 |
| 9 | 결정 375 |
| 10 | R3(Windows) · Q8(플랫폼) |
| 11 | 결정 376 |
| 12 | 결정 383 · Q4 |
| 13 | Q8 · 결정 366 |

## 13. 구현하며 바뀐 것 (Changed while implementing)

비어 있다. 승인 뒤 PR 마다 메인 세션만 채운다.
