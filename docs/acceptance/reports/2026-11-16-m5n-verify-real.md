# M5n 실프로세스 수용 시험 — 서버 하나 · 워커 셋 · 진짜 프로세스

- 날짜: 2026-11-16
- 대상: `feat/m5n-capable-worker-placement` HEAD **`84cde79`**(`feat(m5n): 머신과 배치를 사람에게 보인다`).
  고친 것은 같은 줄기 위 커밋 여섯(아래 「발견」).
- 명세: `docs/m5n-workplan.md` §2 S1~S4 · §4 · §5 · §6 · §8 · §9, `docs/configuration.md` 「Which machine
  runs a job」, `docs/usage.md` §11.
- 목적: 초록 단위 시험이 아니라 **사람이 보는 출력**을 진짜 `rcm serve` · `rcm worker` 프로세스로 읽는다.

## 0. 차림

운영(`~/.config/rcm` · `~/.local/share/rcm` · 8787 · LaunchAgent)은 건드리지 않았다. 모든 명령은
`HOME=$B/home` · `RCM_SERVER=http://127.0.0.1:8795` · `RCM_CONFIG=$B/server.toml` 셸에서 워크트리의
`.venv/bin/rcm` 으로 돌렸다(`B = ~/.local/share/rcm-devtest-m5n-real` — /tmp 아님).

| 프로세스 | 무엇 | PATH | 호스트 |
|---|---|---|---|
| `rcm serve` | 8795 · `lanes = 1` · `advertise = false` · `[[notify]]` 없음 · `probe_interval_seconds = 60` · `check_sweep_seconds = 5` · `worker_timeout_seconds = 30` | `bin-xc:/usr/bin:/bin:/usr/sbin:/sbin` | 이 Mac (`1e2c…`) |
| 워커 `mbp` (풀 mbp) | S1 대로 `rcm init worker` 가 쓴 파일 + `data_dir` · `[[repos]]` | `bin-ftool:bin-xc:…:/usr/sbin:/sbin` | 이 Mac |
| 워커 `w2` (풀 w2pool) | ftool 없음 | `bin-xc:…:/usr/sbin:/sbin` | 이 Mac |
| 워커 `w3` (풀 w3pool) | ftool 있음 · **`/usr/sbin` 없음 → `ioreg` 를 못 찾아 `host_key = worker:w3`** — 두 번째 컴퓨터 흉내 | `bin-ftool:bin-xc:/usr/bin:/bin` | `worker:w3` |

- 가짜 도구: `bin-ftool/ftool`(버전 한 줄) · `bin-xc/fakexc`(`$FAKEXC_FILE` 을 cat — 프로세스마다 다른
  파일이라 지문을 바꿀 수 있다). `[[probes]] xcode = ["fakexc", "-version"]`.
- 저장소: `repos/app.git`(bare) ← `app-src`(scripts/sim_check.sh 는 `SIM_FLAG` 파일이 없으면 exit 1,
  `${SIM_FLAG}.sleep` 이 있으면 그만큼 잔다 · scripts/work.sh 는 잠깐 자는 작업).
- 프리셋: `ok` · `capgate`(any-capable · `requires = ["ftool"]` · `env_passthrough = ["PATH"]`) ·
  `nocap`(any-capable · 아무도 없는 도구) · `sim-check`(git_ref · `check_ref` 기본) ·
  `scenario-qa`(pool mbp · `checks = ["sim-check"]`) · `hostgrp`(any-capable · group sim · scope host) ·
  `poolgrp`(any-capable · group sim2 · scope pool).

## 1. 판정표

| # | 시나리오 | 판정 | 요지 |
|---|---|---|---|
| S1 | 붙이기 | **통과(고친 뒤)** | `rcm worker --check` 가 init 이 쓴 파일을 안 읽었다 → **F1** · 토큰 안내 변수 → **F3** |
| S2 | any-capable | **통과** | 9잡 전부 ftool 있는 mbp · w3 에만. 대기 행 키 · 사유 · ETA 정상 |
| S3 | Xcode 재연 | **통과** | 지문 바뀜 → 자동 점검 정확히 1번 → 실패 → `check_failed` → 2분 넘게 재제출 없음 → recheck → 초록 · 잡 돎 |
| S3′ | 점검 도중 워커 재시작 | **실패 → 고침** | 유실된 자동 점검이 다시 안 걸리는데 「check pending」으로 조용히 영원히 대기 → **F2** |
| S4 | 맞는 머신 없음 | **통과(고친 뒤)** | `rcm run` stderr 한 줄 · 이유 · ETA `—` 정상. `rcm eta` 가 이유를 안 말함 → **F5** |
| 범위 | host / pool | **통과** | host: 같은 호스트 셋 중 하나 + worker:w3 하나씩만 · pool: 넷 동시 |
| 재시작 | 서버 SIGTERM → 재기동 | **통과** | 지문 바뀐 기억(앞 값 · 시각) 유지 · 워커 재등록 · any-capable 계속 집힘 |
| 웹 | 헤드리스 Chrome | **통과** | `환경 바뀜` 칩(빨강) · 확인 목록 · 머신 표 · **다시 점검** 버튼(admin) · 390px 가로 스크롤 없음 · 콘솔 예외 0 |
| `rcm check` | 행들 | **통과(고친 뒤)** | any-capable 프리셋 도구가 서버에 없다고 FAIL → **F6** |
| 로그 | 서버 로그 | **통과(고친 뒤)** | ref 해석 실패가 회차마다 워커마다 찍힘 → **F4** |

## 2. 시나리오별 실측

### S1 — 두 번째 컴퓨터를 붙인다

```text
$ rcm token --config $B/server.toml add mbp --worker
token 'mbp' created — shown once, store it as RCM_TOKEN on the worker machine:     ← F3
6dvK…
on the worker machine: RCM_WORKER_TOKEN=<token above> rcm worker --check

$ rcm init worker --server http://127.0.0.1:8795 --pool mbp
…/home/.config/rcm/worker.toml          (mode 600)
next: … `RCM_WORKER_TOKEN=<token> rcm worker --check --config …/worker.toml`

$ RCM_WORKER_TOKEN=… rcm worker --check            ← 문서(usage §11 · configuration)의 꼴
server: found macmini (192.168.10.125:8787) on this network                        ← F1: 운영 서버!
FAIL  token         a valid token is required
ok   pool          default · lanes 1                                               ← init 은 mbp 라 썼다
```

고친 뒤(`8cf5647`):

```text
config: …/home/.config/rcm/worker.toml
ok   server        http://127.0.0.1:8795 · v0.4.4
ok   token         mbp (worker)
ok   pool          mbp · lanes 1
ok   fingerprint   xcode 27.4
ok   presets       6/7 ok · 1 nosuchtool-xyz missing
```

`rcm init worker --service-file $B/svc/….plist`: 실행 파일 = 워크트리 rcm 절대경로, `--config <worker.toml>`,
PATH `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`(venv 가 앞에 없다), `KeepAlive.SuccessfulExit = false`,
로그 `~/Library/Logs/rcm/worker.log`. 안내 「the service reads its token from … (token = "…", mode 600)」 · 다음 명령
`launchctl bootstrap …` 만 찍고 부르지 않았다(로드하지 않았다).

워커 셋을 띄운 뒤:

```text
$ rcm machines
machine            host                      pool     lanes  fingerprint  presets
epeuem-ui-Macmini  epeuem-ui-Macmini (1e2c)  default  1      xcode 26.5   5/7 ok · 1 ftool missing · 1 nosuchtool-xyz missing
mbp                mbp (1e2c)                mbp      1      xcode 26.5   6/7 ok · 1 nosuchtool-xyz missing
w2                 w2 (1e2c)                 w2pool   1      xcode 26.5   5/7 ok · 1 ftool missing · 1 nosuchtool-xyz missing
w3                 w3                        w3pool   1      xcode 26.4   6/7 ok · 1 nosuchtool-xyz missing
```

같은 Mac 의 셋은 `(1e2c)` 로 한 호스트, w3 는 따로다. 처음엔 bare 레포의 HEAD 가 없는 가지(master)를 가리켜
`checks: sim-check needed ref_unresolved` 가 보였고 서버 로그에 같은 줄이 15초에 20줄 쌓였다(**F4**). HEAD 를
고치자 다음 회차에 `machine checks submitted: [1, 2, 3, 4]` — 살아 있는 네 머신에 하나씩, 모두 통과.

### S2 — any-capable

`capgate` 를 `--no-wait --no-join` 으로 5 + 4 + 4개 냈다. 집은 곳: `#5 #7 #9 #10 #27 #30 → mbp`,
`#6 #8 #11 #28 #29 → w3`. ftool 없는 @local · w2 는 한 번도 안 집었다. 끝난 잡의 `pool` 은 집은 워커의 풀,
`display_pool` 은 `default`.

```text
$ rcm top                                  (대기 행)
   1. · queued     #12 capgate  …  eta 19:16 · in 38s  (low · preset)
        waiting for lane · behind #10 · any machine
   3. · queued     #14 nocap    …  eta —  (low · default)
        no capable machine — epeuem-ui-Macmini: nosuchtool-xyz missing · mbp: … · w2: … · w3: …
$ rcm jobs
#12    queued     capgate  …  1st in line · waiting 6s       19:16  any machine
```

`/api/status` 대기 행(줄임): `{"id":12,"placement":"capable","display_pool":"default","target_worker":null,
"concurrency_scope":"pool","host_key":null,"reason":"waiting_for_lane","placement_detail":[{"worker":"@local",
"state":"ineligible","code":"tool_missing","detail":{"tools":["ftool"]}},{"worker":"mbp","state":"eligible","code":"ok"},…]}`.
도는 행에는 `host_key` 가 박힌다(`1e2c775881a73eba` · `worker:w3`).

`rcm eta capgate` → `4th in line · 5 ahead · wait 38s · expected 8s · eta 19:16 · low · preset`.

### S3 — 환경이 바뀐다

```text
$ rm $B/flags/ok; echo 'Xcode 27.2…' > xc-mbp.txt; rcm reload
nothing changed                                              ← 그래도 세대가 오르고 워커가 다시 잰다(F9)
[rcm] machine mbp: environment changed — xcode Xcode 26.5 · Build version 26F75 → Xcode 27.2 · Build version 27B52
[rcm] machine checks submitted: [16]                         ← mbp 에만, 정확히 하나
$ rcm machines --machine mbp
  scenario-qa  ineligible  check_failed  sim-check failed #16
fingerprint 3485aa9242b8ce8b · changed 2026-11-16T10:15:51Z
  xcode  ok  Xcode 27.2 · Build version 27B52  (was Xcode 26.5 · Build version 26F75)
checks
  sim-check  failed  #16  (automatic)
$ rcm check
FAIL  machines      4 machines · 1 environment changed (mbp: xcode 26.5 → 27.2) · 1 check failed (mbp: sim-check #16)
warn  placement     … scenario-qa: pool mbp (check failed) …
$ rcm run scenario-qa --no-wait
submitted job #17 queued · 1st in line · check failed
```

75초(스위퍼 15회 · 60초 재측정 1회) 동안 sim-check 재제출 없음. `touch flags/ok; rcm recheck mbp` →
`#18 sim-check on mbp` → 통과 → `#17 scenario-qa succeeded` · `rcm check` 의 machines 행은
`ok … 1 environment changed (mbp: xcode 26.5 → 27.2)`. 웹의 **다시 점검**(admin 토큰)도 눌러 봤다:
토스트 「다시 점검을 넣었습니다: #33 sim-check」 → 통과 → 대기하던 #32 가 돌았다.

### S3′ — 자동 점검 도중 워커가 재시작된다 (F2)

xcode 27.3 → 27.4 · `rcm reload` → 자동 점검 #37 이 30초 자는 동안 `kill -TERM <mbp>` → `#37 lost in 6s`
→ 워커 재기동. 고치기 전:

```text
$ rcm machines --machine mbp
checks
  sim-check  needed  #37  (automatic)
$ rcm run scenario-qa --no-wait
submitted job #38 queued · 1st in line · check pending
$ rcm check
ok   machines      4 machines · 1 environment changed (mbp: xcode 27.3 → 27.4)
warn  placement     … scenario-qa: pool mbp (check pending) …
```

결정 95 로 같은 지문에는 다시 안 걸리므로 #38 은 영원히 기다린다. 그런데 이유는 「점검 중 · 스스로 풀림」이라
확인 목록에도 안 오르고 `rcm check` 는 ok — 명세 §1.3 이 막으려던 「조용히 열흘」의 모양이다. 9분 기다려도
그대로였다. 고친 뒤(`ea27b9a`, 서버 재기동):

```text
#38    queued     scenario-qa  …  —  check failed — mbp: sim-check #37 did not finish — recheck
FAIL  machines      4 machines · … · 2 check needs rcm recheck (mbp: sim-check #37; w3: sim-check #35)
warn  placement     … scenario-qa: pool mbp (check needs rcm recheck) …
웹: 확인이 필요한 작업 「#38 점검 실패 · mbp: sim-check 안 끝남 — 다시 점검 #37」 · 머신 표 같은 문구
```

`rcm recheck mbp` → #39 통과 → #38 succeeded → machines 행 ok.

### S4 — 맞는 머신이 하나도 없다

```text
$ rcm run nocap --no-wait
rcm: nocap: no capable machine — @local: nosuchtool-xyz missing · mbp: … · w2: … · w3: …
submitted job #14 queued · 3rd in line · no capable machine
$ rcm eta nocap
4th in line · 3 ahead · wait — · expected 10m 00s · eta — · low · default          ← 이유 없음(F5)
```

고친 뒤: `… · eta — · no capable machine · low · default`. w3 를 내리자 `placement_detail` 에
`{"worker":"w3","state":"down","code":"worker_down"}` 가 섞였다.

### 범위 — `concurrency_scope`

`hostgrp`(scope host) 4개: `#20 @local`(1e2c) 과 `#19 w3`(worker:w3) 가 10:18:10 에 함께 시작, `#21`·`#22` 는
`blocked_by_group`(「⛓ blocked by #19 · sim · frees in …」) — mbp · w2 가 비었는데도 같은 호스트라 안 집었다 —
10:18:20 에 시작. `poolgrp`(scope pool) 4개: @local · mbp · w2 · w3 네 곳에서 **동시에**.

### 서버 재시작

capgate 4개를 낸 직후 서버 SIGTERM(`[rcm] signal 15: shutting down`). 워커: `heartbeat: server unreachable for 9s`
→ 재기동 뒤 `server reachable again` → `claimed #30 capgate` … 네 잡 모두 succeeded. 재시작 뒤
`rcm machines --machine mbp` 가 `changed 2026-11-16T10:15:51Z` · `(was Xcode 26.5 …)` 를 그대로 보였고 점검은
`passed #18` — 자동 점검이 새로 걸리지 않았다.

### `--machine`

client 토큰: `rcm run sim-check --ref main --machine mbp` → 접수(점검 프리셋) · `rcm run capgate --machine mbp` →
`--machine on preset 'capgate' needs an admin token` · `--machine nosuch` → `unknown machine 'nosuch' — see rcm machines` ·
down 판정 뒤 `--machine w3` → `machine w3 is down`. 워커가 SIGTERM 직후(아직 alive)에 받은 지정 잡 #35 는
down 판정과 함께 `lost · worker stopped`.

### 웹 (헤드리스 Chrome · `tests/test_web_browser.py` 의 `Chrome` 을 빌려 씀)

데스크톱 1280 · 폰 390 · `?lang=en` 세 번 열어 콘솔 예외 0. 머리줄 워커 필 옆 `mbp 환경 바뀜`(점검 실패 때 빨강) —
누르면 `mbp · 환경 바뀜 — xcode: Xcode 27.2 · … → Xcode 27.3 · …`. 확인 목록에 `맞는 머신 없음` · `점검 실패` 행,
머신 표(접힘)에 머신 · 호스트 · 지문 · `n/7 가능` · 점검 · **다시 점검**(admin 일 때만). 390px 에서
`scrollWidth = innerWidth = 390`.

## 3. 발견

| # | 심각도 | 무엇 | 증거 | 처리 |
|---|---|---|---|---|
| F1 | 높음 | `rcm worker` 가 `--config` 없이는 `rcm init worker` 가 쓴 `~/.config/rcm/worker.toml` 을 안 읽는다. 문서의 S1 순서대로 하면 pool `default` · repos 없음 · server 는 같은 망에서 찾아 **다른 서버(운영 8787)에 워커 토큰을 보냈다** | §2 S1 | `8cf5647` — `--config` · `--server` 둘 다 없으면 기본 파일을 읽고 경로를 말한다. `--server` 긴 꼴은 그대로 |
| F2 | 높음 | 판정 없이 끝난(유실 · 취소) 자동 점검, 또는 `auto_checks = false` 의 `check_needed` 가 다시 안 걸리는데 대기 이유가 `check_pending`(스스로 풀림) — 잡이 확인 목록 밖에서 영원히 기다리고 `rcm check` 는 ok | §2 S3′ | `ea27b9a` — 판정 detail `recheck`, 이유 `check_failed`, `checks[].recheck_needed`, `rcm check` FAIL, 웹 문구. 결정 95 는 그대로 |
| F3 | 낮음 | `rcm token add --worker` 머리줄이 「store it as RCM_TOKEN」— 바로 아래 줄은 `RCM_WORKER_TOKEN` | §2 S1 | `0fcb60a` |
| F4 | 중간 | ref 해석 실패 로그가 회차마다 워커마다(15초에 20줄), 사유는 예외 이름뿐 | §2 S1 | `a785254` — 처음 한 번 + git 의 첫 줄, 풀리면 한 줄 |
| F5 | 낮음 | `rcm eta` 가 「eta —」일 때 새 이유 셋을 안 말한다 | §2 S4 | `f2c3cee` |
| F6 | 중간 | `rcm check` 의 `local preset tools` · `service preset tools` 가 any-capable 프리셋의 도구가 **서버에** 없다고 FAIL — any-capable 설정이면 늘 빨강 | `FAIL local preset tools capgate (ftool missing)` | `18b2ad6` — `any machine` 표시, 판정에서 뺌 |
| F7 | 낮음 | 실패한 점검 잡의 「⧉ 다시 실행」 명령이 `rcm run sim-check --ref HEAD` — 그 머신에 안 묶여 다른 곳에서 돈다. `rcm recheck mbp sim-check` 가 맞다 | 웹 최근 #31 | 안 고침 — 웹 복사 명령 규칙을 바꾸는 일이라 오너 판단 |
| F8 | 낮음 | `rcm machines` 의 `machine` 열이 로컬을 `epeuem-ui-Macmini` 로 찍는데 `--machine`/`recheck` 는 `@local` 만 받는다(오류 메시지가 `@local` 을 알려 주긴 한다) | `unknown machine 'epeuem-ui-Macmini' — server has: @local, …` | 안 고침 — 웹처럼 `@local` 을 곁에 찍는 안 |
| F9 | 정보 | 프로브만 바꾼 `rcm reload` 가 「nothing changed」라고 하지만 세대가 오르고 모든 워커가 다시 잰다 | §2 S3 | 안 고침 — 「machines re-measure」 한 마디가 있으면 좋다 |
| F10 | 정보 | 사람이 지정한 점검이 유실되면(워커가 내려가는 중에 받음) 이미 **통과**했던 머신도 `check_needed` 로 내려간다 — 명세 §4.4 「가장 최근 잡」 규칙 그대로. F2 덕에 이제는 보이지만, 통과 기록이 있으면 유실 · 취소를 건너뛰는 편이 맞을 수 있다 | w3 #35 | 오너 결정 거리(결정 95 · §4.4) |
| F11 | 정보 | `rcm worker --check` 의 `repos` 가 `none` 이어도 ok — 서버에 점검 프리셋(언제나 git_ref)이 있으면 이 워커로 오는 자동 점검은 `repo_missing` 으로 실패한다 | §2 S1 | 안 고침 — 서버 명세에 점검이 있으면 warn 하는 안 |

## 4. 시험

- 고친 커밋마다 먼저 빨간 시험을 썼고(`F1` 은 고치기 전 같은 망의 운영 서버를 찾아 빨갰다), 고친 뒤 초록.
- 전체: `pytest -q -p no:randomly tests/` 종료 0(약 4,900개, 실패 0) · `ruff check .` · `ruff format --check .` 초록.
  첫 전체 실행(`-x`)에서 `tests/test_e2e_m5n.py::test_a_changed_probe_triggers_one_automatic_check_and_a_failure_sticks`
  가 40초 대기 초과로 한 번 빨갰다 — 그때 이 Mac 의 load average 가 24(코어 10, 운영 게이트가 돌고 있었다)였고,
  그 파일만 다시 돌리면 초록, 두 번째 전체 실행에서도 초록이었다. 부하 탓 타이밍으로 본다.
- 못 한 것: 진짜 두 번째 컴퓨터(호스트는 `worker:w3` 로 흉내) · mDNS 의 `server = "auto"` 찾기(운영이 같은 망에
  광고 중이라 시험 서버는 `advertise = false`) · `accept_any = false` 워커 · 프로브 timeout · launchd 로드.

## 5. 치운 것

서버 · 워커 셋(이 시험이 띄운 pid 만) 종료, `~/.local/share/rcm-devtest-m5n-real` 삭제. 운영 8787 은
F1 재현 때 인증 실패 요청 한 번(읽기 · 거절) 외에는 닿지 않았다.
