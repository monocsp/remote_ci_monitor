# M5l 검증 시나리오 (2026-09-13)

> 목적: `docs/m5l-review-supplement-workplan.md`(M5l v0.1) 의 보완 항목 **S1~S12**(열린 PR #109 #110 #108 #106 의
> 브랜치에서 머지 전에 고치는 것)와 **L1~L8**(머지된 PR 의 보완 — 새 브랜치)이 리뷰가 지적한 그 자리에서 실제로
> 닫혔는지를 본다. 행의 정본은 **리뷰의 「재현」**이다 — `docs/reviews/2026-09-13-impl-reviews/pr-<N>.md` 의 각 지적이
> 「재현 조건」으로 적어 둔 것을 그대로 첫 행으로 두고, 그 리뷰의 P2 목록이 암시하는 엣지를 뒤에 붙였다. 항목마다
> 근거를 `리뷰 #N B-k` · `명세 Sk/Lk` 로 적었다. 판정은 M5i·M5j 와 같다 — 「테스트가 있다」가 아니라 **「실제 모양의
> 입력으로 실제 경로를 지났다」**. 보고서는 `docs/acceptance/reports/<날짜>-m5l-verify-<영역>.md`.
>
> 실행 주체는 **격리 에이전트**다(§0). 발견한 오류는 보고만 하지 않고 **빨간 테스트 → 수정 → 같은 브랜치에 push** 까지 한다.

## 0. 실행 규칙 (격리 에이전트 공통)

| 규칙 | 내용 |
|---|---|
| 기준 커밋 | S 항목은 **그 열린 PR 의 브랜치 HEAD**(보완 커밋이 올라간 뒤 — `origin/feat/preset-requires-tools-preflight`(#109) · `origin/feat/cancel-submission-capability`(#110) · `origin/feat/jobs-step-timeline-for-finished`(#108) · `origin/docs/admission-two-lane-experiment`(#106)). L 항목은 **그 수정 브랜치 HEAD**(명세 §1 표의 브랜치 이름 — `origin/fix/gc-offline-error-paths` 등); 이미 dev 에 머지됐으면 `origin/dev`. 보고서 머리에 검증한 커밋 sha 를 적는다 |
| 워크트리 | 기준 커밋에서 `docs/acceptance-m5l-verify-<영역>` 브랜치의 **자기 워크트리**(`git worktree add -b docs/acceptance-m5l-verify-<영역> ../remote_ci_monitor-acceptance-m5l-verify-<영역> <기준>`) + 자기 `.venv`(`python3.11 -m venv .venv && ./.venv/bin/python -m pip install -e '.[dev]'`). 다른 워크트리를 건드리지 않고 `git switch` 로 브랜치를 바꾸지 않는다 |
| 운영 | `~/Documents/GitHub/remote_ci_monitor` · `~/.config/rcm` · `~/.local/share/rcm` · `~/.local/state/rcm` 은 **읽지도 열지도 않는다**. 워크트리의 `rcm` 이 DB 를 열면 마이그레이션한다. `~/.local/bin/rcm`(운영 venv 의 심링크)을 부르지 않는다 — 언제나 `./.venv/bin/rcm` |
| 시험 서버 | 자기 설정 파일 · 포트(영역마다 아래 배정, **8811~8818**) · `data_dir` 은 `$HOME` 아래(`~/.local/share/rcm-verify-m5l-<영역>`), `/tmp` 아래 금지(dolomood sim_cleanup 이 `/private/tmp` 를 거절한다) · `advertise = false` · `[notify]` 없음 · `bind = "127.0.0.1"` · 끝나면 서버를 내리고 `data_dir` 을 지운다. 8788·8790·8801~8805 는 다른 세션의 것이다 — 쓰지 않는다 |
| `--config` 없는 명령 | `rcm check`·`rcm token`·`rcm serve` 를 `--config` **없이** 부를 일이 있으면 반드시 **`HOME=<data_dir>/home`** 으로 돌린다(`mkdir -p <data_dir>/home`) — 그래야 `~/.config/rcm/server.toml`(운영)을 집지 않는다. `XDG_CONFIG_HOME`·`RCM_CONFIG`·`RCM_SERVER_DATA_DIR` 이 셸에 남아 있지 않은지 먼저 `env \| grep -E 'RCM_\|XDG_'` 로 본다 |
| 상태 파일(#110) | capability 상태 파일은 `XDG_STATE_HOME=<data_dir>/state` 를 export 한 셸에서만. `~/.local/state/rcm` 에 쓰이면 그 자체가 오류 |
| 판정 | 기대와 다르면 **오류**다. 「테스트를 약하게 해서 통과」는 금지. 오류는 **빨간 테스트 + 수정을 같은 브랜치에**(S 는 그 PR 브랜치, L 은 그 fix 브랜치) 커밋·push 한다 — 새 PR 이 아니다(PR 은 이미 열려 있거나 그 브랜치의 것이다). 이미 dev 에 머지된 L 항목이면 `fix/<scope>-<what>` 브랜치에 PR(`gh pr create --base dev`). 보고서에 커밋 sha 또는 PR 번호 |
| 모르면 3 | 확인 절차 자체가 불완전하면(Chrome 없음 · 옛 wheel 못 받음 · 경쟁 창을 못 맞춤) 그 항목은 「확인 못 함」으로 적는다 — 통과로 적지 않는다 |
| 보고서 | 항목 ID 마다 통과 / 오류(커밋·PR) / 확인 못 함(이유) · 실측값(응답 본문 · 종료 코드 · stderr 줄 · 파일 권한 · `user_version`) · 명령 그대로. 비밀(토큰 · capability)은 보고서에 **앞 4자만**, 절대경로는 `<HOME>` 으로 바꿔 적는다 |
| 도구 | `/usr/bin/curl` · `/usr/bin/jq` · `/usr/bin/sqlite3`(3.51 — `ALTER TABLE … DROP COLUMN` 된다) · `/usr/bin/lockf` 가 이 Mac 에 있다. `flock(1)` 은 **없다**. JSON 은 `jq` 로 키를 집어 본다 |

포트 배정: **S1~S4(#109) 8811** · **S5~S10(#110) 8812** · **S11~S12(#108 · #106) 8813** · **L1·L5 8814**(L5 의
「점유된 포트」는 **8815**) · **L2 8816** · **L3·L6·L7 8817** · **L4 8818**(가드는 순수 함수라 서버가 없지만 포트는 비워
둔다). L1·L6·L7·L8 은 서버가 필요 없거나 자기 픽스처를 쓴다.

공통 준비(영역마다 반복): 설정 파일 `~/.local/share/rcm-verify-m5l-<영역>.toml` 을 아래 꼴로 만들고
`./.venv/bin/rcm serve --config <그 파일>` 을 별도 셸(또는 `nohup … > <data_dir>/server.log 2>&1 &`)로 띄운다. 토큰은
`rcm token --config <그 파일> add <이름> [--admin|--worker]` — **`--config` 는 하위 명령 앞**. 아래 표의 `$A`(alice) ·
`$B`(bob) · `$C`(charlie) 는 일반, `$R`(root) 는 admin, `$W`(wk) 는 worker 토큰이다. 제출은 파일 하나짜리 디렉터리로
`rcm run <preset> --server http://127.0.0.1:<port> --token $A --dir <디렉터리> --poll` — `--dir` 이 없으면 현재 디렉터리
(워크트리 전체)를 올린다. 서로 다른 잡을 여러 개 만들 때는 디렉터리마다 **내용이 다른 파일** 하나(`echo $i > f`) —
같은 트리는 합류(`joined: true`)가 된다. `--no-wait` 의 stdout 은 JSON 한 줄이고 `rcm wait` 의 마지막 stdout 줄도 JSON 이다.

```toml
[server]
bind = "127.0.0.1"
port = 881N
data_dir = "~/.local/share/rcm-verify-m5l-<영역>"
advertise = false
lanes = 1

[[presets]]
name = "ok"
argv = ["/bin/sh", "-c", "echo ::rcm::step::build; exit 0"]

[[presets]]
name = "sleepy"          # 취소용 — 스텝 하나 열어 두고 오래 잔다
argv = ["/bin/sh", "-c", "echo ::rcm::step::a; sleep 120"]
```

`<HOME>` 은 절대경로로 바꿔 쓴다(TOML 은 `~` 를 안 푼다 — `data_dir` 만 서버가 푼다). 아래 「자동」 행의 테스트 이름은
**제안**이다 — 수정 브랜치가 다른 이름으로 같은 것을 잠갔으면 그 이름을 보고서에 적는다. 없으면 오류(테스트 없이 고친 것).

## 1. 항목별 시나리오

### S1 — 상대 PATH 항목의 cwd fail-open (#109 · 리뷰 #109 B P1 · 명세 S1) · 8811

준비: 8811 설정에 아래 프리셋. 스크립트는 **파수꾼 파일**을 만든다 — 프로세스가 떴는지의 증거는 로그가 아니라 이 파일이다.
서버는 **cwd 를 `<data_dir>/srvcwd`** 로 두고 띄운다(`cd ~/.local/share/rcm-verify-m5l-s109/srvcwd &&
<워크트리>/.venv/bin/rcm serve --config …` — `rcm` 은 절대경로로). `srvcwd/tools/fvm` 은
`printf '#!/bin/sh\nexit 0\n' > … && chmod +x`.
제출 트리 `treeA/` 에는 파일 `f` 하나, `treeB/` 에는 `f` + `tools/fvm`(같은 스크립트).

```toml
[[presets]]
name = "needs-fvm-rel"       # PATH 의 첫 항목이 상대경로
argv = ["/bin/sh", "-c", "touch \"$RCM_WORKSPACE/../../sentinel-$RCM_JOB_ID\"; echo ::rcm::step::build; exit 0"]
requires = ["fvm"]
env_passthrough = ["HOME"]
[presets.env]
PATH = "tools:/usr/bin:/bin"

[[presets]]
name = "needs-fvm-empty"     # 빈 항목(앞 `:` · 뒤 `:`)
argv = ["/bin/sh", "-c", "touch \"$RCM_WORKSPACE/../../sentinel-$RCM_JOB_ID\"; exit 0"]
requires = ["fvm"]
env_passthrough = ["HOME"]
[presets.env]
PATH = ":/usr/bin:/bin:"

[[presets]]
name = "needs-fvm-rel-remote"  # 원격 풀 · 같은 PATH
argv = ["/bin/sh", "-c", "touch \"$RCM_WORKSPACE/../../sentinel-$RCM_JOB_ID\"; exit 0"]
pool = "linux"
requires = ["fvm"]
env_passthrough = ["HOME"]
[presets.env]
PATH = "tools:/usr/bin:/bin"
```

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S1.1 | `srvcwd/tools/fvm` 있음 · `treeA` 에 없음 | `rcm run needs-fvm-rel --dir treeA --poll` → `ls <data_dir>/sentinel-<id>` | exit **1** · 마지막 JSON `state: "failed"` · `summary_code: "tool_missing"` · `summary_args == {"tool": "fvm"}` · **파수꾼 없음**. (수정 전: `succeeded` — 서버 cwd 의 `tools/fvm` 을 찾았다) | 실기 (리뷰 #109 B P1 재현 1~3) |
| S1.2 | 반대 배치: `srvcwd/tools/` 를 비우고 `treeB`(안에 `tools/fvm`) | `rcm run needs-fvm-rel --dir treeB --poll` | 구현이 **A**(상대 항목을 워크스페이스 기준으로 푼다)면 `succeeded` + 파수꾼 **있음**; 구현이 **B**(상대·빈 항목은 무시)면 `failed`/`tool_missing` + 잡 로그에 그 항목을 무시했다는 줄 한 개(`[rcm] PATH entry ignored: tools (relative)` 꼴 — 항목 이름만, 서버 cwd 없음). 어느 쪽인지 보고서에 적고 `docs/configuration.md` 의 `requires` 절이 같은 말을 하는지 본다. 어느 쪽이든 **서버 cwd 의 파일을 본 적이 없다**(S1.1 과 함께 판정) | 실기 (리뷰 #109 「반대 배치에서는 거짓 실패」) |
| S1.3 | `srvcwd/fvm`(tools/ 가 아니라 cwd 바로 아래) 있음 | `rcm run needs-fvm-empty --dir treeA --poll` | `failed` · `tool_missing` · 파수꾼 없음 — 빈 PATH 항목(`""` = cwd)이 서버 cwd 로 풀리지 않는다 | 실기 (리뷰 #109 「빈 PATH 구성요소」) |
| S1.4 | `rcm token --config … add wk --worker` · 워커 데이터 `~/.local/share/rcm-verify-m5l-s109-worker` · 워커도 **cwd 에 `tools/fvm`** 을 두고 띄운다 | `rcm run needs-fvm-rel-remote --dir treeA --no-wait` → `cd <워커 cwd> && RCM_WORKER_TOKEN=$W rcm worker --server http://127.0.0.1:8811 --pool linux --lanes 1 --data <워커 데이터> --once` | 워커가 `failed` · `tool_missing` · `{"tool":"fvm"}` 로 보고 · 워커 데이터에 파수꾼 없음 · 워커 stdout/stderr 에 PATH 항목 없음 | 실기 (원격 경로도 같은 규칙 — 명세 S4 「원격도 같이」의 짝) |
| S1.5 | S1.1~S1.4 의 잡 | `GET /jobs/<id>` · `GET /api/status` · `GET /jobs/<id>/log` · 서버 로그 · 워커 로그를 **PATH 항목 하나씩** `grep -c -- "tools"`, `grep -c -- "srvcwd"`, 서버 셸 `echo $PATH` 의 각 항목 | 전부 0(`/usr/bin`·`/bin` 은 argv 의 `/bin/sh` 로 나올 수 있다 — 그 줄만 눈으로 제외) · 잡 로그의 판정 줄은 `[rcm] required tool fvm: missing` 꼴 | 실기 (리뷰 #109 C 「개별 PATH 구성요소 단언」) |
| S1.6 | — | `pytest tests/test_requires.py -q` · `python scripts/mutcheck.py --only <상대 PATH 를 서버 cwd 로 푸는 변이 이름>`(없으면 `requires-check-skipped` 만 돌리고 「상대 PATH 변이 없음」을 적는다) | 초록 · caught · 테스트에 「서버 cwd ≠ 워크스페이스 + 상대 PATH」 케이스가 있다 | 자동 |

### S2 — basename 이 빈 절대경로가 공개 args 에 (#109 · 리뷰 #109 B P1 둘째 · 명세 S2) · 8811

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S2.1 | 설정 넷: `requires = ["/opt/private-sdk/"]` · `["/"]` · `["/opt/private-sdk/."]` · `["/opt/private-sdk/.."]` | 각각 `rcm serve --config <그 파일>` | 넷 다 **exit 2** · stderr `rcm: config: …` 에 프리셋 이름 · `requires` · 그 값 · 이유(basename 없음) · 서버 안 뜸(`curl` 연결 거부). (수정 전: 서버가 뜨고 잡이 `tool_missing` `{"tool": "/opt/private-sdk/"}`) | 실기 (리뷰 #109 재현 `["/Users/build/private-sdk/"]` · `["/"]`) |
| S2.2 | `requires = ["/usr/local/rcm-verify-missing-9f3a"]`(없는 절대경로 · basename 있음) | `rcm run … --poll` → `GET /jobs/<id>` · `GET /api/status` 의 `recent[]` 그 행 | `tool_missing` · `summary_args == {"tool": "rcm-verify-missing-9f3a"}` **정확히 이 키 하나** · `summary` 문장에도 `/usr/local` 없음 · `jq -c . \| grep -c '/usr/local'` → 0 | 실기 (`public_name` 은 언제나 basename) |
| S2.3 | 원격(8811 · `$W`) | `curl -s -X POST -H "Authorization: Bearer $W" -H 'Content-Type: application/json' -d '{"outcome":"failed","exit_code":null,"summary_code":"tool_missing","summary_args":{"tool":"/opt/bin/"}}' …/worker/jobs/<claim 한 id>/finish` | **400** (이미 그랬다 — 회귀 확인) · `{"tool":"fvm"}` 은 200 | 실기 |
| S2.4 | — | `pytest tests/test_requires.py -q -k "basename or slash or root"` | `["/"]`·`["/opt/bin/"]` 설정 케이스가 `ConfigError` 를 단언한다(리뷰 C 「없다」던 것) | 자동 |

### S3 — 선언한 절대경로가 잡 로그에 (#109 · 리뷰 #109 A-1 · 명세 S3 · **오너 결정** §2) · 8811

오너가 정한 뒤에 돌린다. 어느 쪽이든 **한쪽만** 참이어야 한다 — 코드와 문서가 서로 다른 말을 하면 오류.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S3.1 | `requires = ["/usr/bin/true"]` 프리셋 | `rcm run … --poll` → `GET /jobs/<id>/log`(토큰) | **오너 = basename**: 로그 줄 `[rcm] required tools: true ok` — `grep -c '/usr/bin/true' <log>` → 0. **오너 = 유지**: `[rcm] required tools: /usr/bin/true ok` 그대로이고 `docs/configuration.md` `requires` 절이 「선언한 절대경로는 잡 로그(토큰 보호)에 그대로 남는다」를 말하며 CHANGELOG 의 「no path anywhere」가 「no path in status or errors」로 좁혀져 있다(리뷰 D 의 모순 해소) | 실기 + 문서 |
| S3.2 | 같은 설정 | `rcm check --config <8811 설정> --server http://127.0.0.1:8811 --token $A` 의 `local preset tools` 행 | S3.1 과 **같은 정책**(basename 이면 여기도 basename) · 행에 PATH 값 없음 · `tests/test_requires_check.py` 가 그 모양을 잠근다(리뷰 C 「`/bin/sh` 그대로를 정답으로」가 정책대로 바뀌었다) | 실기 + 자동 |

### S4 — preflight 전 취소 경쟁 (#109 · 리뷰 #109 B P2 · 명세 S4) · 8811

실기 창은 좁다(자재화가 끝난 직후 preflight 사이). 자재화를 길게 하려고 **큰 트리**를 올린다: `treeBig/` 에
`dd if=/dev/urandom of=big bs=1m count=300`(압축이 안 돼 풀기가 몇 초 걸린다). 창을 못 맞추면 실기 행은 「확인 못 함」이고
자동 행이 정본이다.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S4.1 | `requires = ["rcm-verify-no-such-tool-9f3a"]` 인 프리셋 `needs-missing` · `treeBig` | `rcm run needs-missing --dir treeBig --no-wait` → `/api/status` 에서 `phase == "materializing"` 이 보이는 순간 `rcm cancel <id> --token $A` → 종료 뒤 `GET /jobs/<id>` | `state: "cancelled"` · `summary_code` 가 `tool_missing` 이 **아니다** · `rcm wait --job <id>` exit **2**. (수정 전: `failed`/`tool_missing`/exit 1 이 `cancelling` 을 덮었다) | 실기 (리뷰 #109 재현 1~3) |
| S4.2 | `tests/test_requires.py`: 자재화 콜백을 `threading.Event` 로 막아 둔 워커 · 그 사이 `store.request_cancel(id)`(또는 `POST /jobs/<id>/cancel`) → Event set → preflight 가 도구 없음을 본다 | 테스트 실행 | 최종 `state == "cancelled"` · `summary_code is None or != "tool_missing"` · `exit_code is None` · `Store.finish(…, only_from=("running",))` 가 `cancelling` 행에 `False` 를 돌려준다(직접 단언) | 자동 (명세 S4 「`only_from`」) |
| S4.3 | 같은 픽스처 · 취소 대신 워커 **종료 요청**(`should_stop`) | 테스트 | 잡이 `failed/tool_missing` 로 닫히지 않고 종료 규칙대로(`lost` 또는 재큐 — 워커 재시작 계약)이다 · 보고서에 실제 상태 | 자동 (리뷰 「워커가 종료됨」) |
| S4.4 | 원격 워커(8811 · `$W`) · `needs-missing-remote`(pool) · `treeBig` | S4.1 과 같이 자재화 중 `rcm cancel` | 워커의 finish 가 `failed` 를 보내도 서버가 `cancelling` 을 안 덮는다(`GET /jobs/<id>` `cancelled`) — 또는 워커가 취소를 보고 finish 를 안 보낸다. 어느 쪽이든 최종 `cancelled` | 실기 (명세 S4 「원격도 같이」) |
| S4.5 | S4.1 의 잡 | `sqlite3 <data_dir>/rcm.sqlite3 "SELECT count(*) FROM job_failures WHERE job_id=<id>"` · `GET /jobs/<id>` | 0 · `failed_step`·`last_step` null · 잡 로그에 `[rcm] required tool …: missing` 줄이 있어도 무방(판정은 상태) | 실기 |

### S5 — 같은 토큰의 다른 세션이 잡 전체 취소 (#110 · 리뷰 #110 B P1 첫째 · 명세 S5) · 8812

준비: 8812 는 `cancel_requires_submission_token = true`. 셸 두 개 **A 와 B 가 같은 `XDG_STATE_HOME=<data_dir>/state` 와
같은 토큰 `$A`** 를 쓴다(리뷰의 재현 조건 그대로). 상태 파일은 `$XDG_STATE_HOME/rcm/submissions.json`.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S5.1 | 셸 A: `rcm run sleepy --dir tree1 --no-wait --token $A`(201, `submission.id` = SA) · 셸 B: 같은 트리로 `rcm run sleepy --dir tree1 --no-wait --token $A`(200 `joined: true`, `submission.id` = SB) | 셸 B: `rcm cancel <id> --token $A` | **200 `left: true`** · `GET /jobs/<id>.state == "running"` · `sqlite3 … "SELECT submission_id, role FROM submissions WHERE job_id=<id>"` 에 **SA `cancel_job` 만** 남는다(SB 행 삭제) · 상태 파일에서 SB 항목이 빠지고 SA 항목은 남는다. (수정 전: 잡 전체 `cancelling` — 지문 폴백이 SA 를 집었다) | 실기 (리뷰 #110 재현 · 명세 S5) |
| S5.2 | S5.1 뒤 | 셸 A: `rcm cancel <id> --token $A` | 200 `state: cancelling` → `cancelled` — 자기 `submission_id` 의 비밀을 썼다 | 실기 |
| S5.3 | 새 잡: A 제출 · B 합류(같은 토큰) | 셸 **A** 가 먼저 `rcm cancel <id>` | 200 `cancelling` — 요청자의 세션은 자기 `cancel_job` 항목을 집는다(합류자의 leave 를 집어 「나가기」로 끝나지 않는다) | 실기 (docstring 이 약속한 반대 방향) |
| S5.4 | 상태 파일에 **다른 서버 URL**(`http://127.0.0.1:8899`)로 같은 `job_id` 의 가짜 항목을 손으로 추가 | `rcm cancel <id> --token $A` | 그 항목을 안 쓴다(서버로 조회) — 결과는 S5.2 와 같고, 가짜 항목의 `cancel_token` 이 요청 본문에 실리지 않는다(`--debug` 또는 서버 로그의 403 없음) | 실기 |
| S5.5 | — | `python -c 'import remote_ci_monitor.submissions as s; help(s.lookup)'` · `grep -n fingerprint src/remote_ci_monitor/submissions.py` | `lookup` 의 선택 기준에 **토큰 지문 폴백이 없다**(`submission_id` 로만 — 항목은 `rcm run` 이 받은 `submission.id` 로 식별) · 지문이 파일에 남아 있어도 선택에 안 쓰인다 — 또는 지문 필드 자체가 사라졌다(보고서에) | 자동 + 코드 |
| S5.6 | — | `pytest tests/test_cancel_capability.py -q` · `git log -p -S'cancel_job' -- tests/test_cancel_capability.py` | 리뷰 C 가 지목한 「같은 토큰의 합류자보다 요청자 capability 가 이긴다」 테스트의 기대값이 **`left: true`** 로 바뀌었다 | 자동 |
| S5.7 | — | CHANGELOG `[Unreleased]` · `docs/configuration.md` · `docs/usage.md`/`usage.ko.md` 의 「같은 client token 의 다른 session 은 취소할 수 없다」 | 이제 사실이다(S5.1) — 문장 유지 · 「같은 상태 디렉터리라도」가 덧붙어 있으면 더 좋다 | 문서 |

### S6 — leave 비밀의 대상 바인딩 · 원자적 소비 (#110 · 리뷰 #110 B P1 둘째 · 명세 S6) · 8812

준비: alice 제출(`$A`) · bob 합류(`$B`, 비밀 `$CAP_B`) · charlie 합류(`$C`, 비밀 `$CAP_C`) — 각자 `--no-wait` JSON 의
`submission.cancel_token`. `POST /jobs/<id>/cancel` 본문은 `{"cancel_token": "…"}`(`docs/configuration.md` 「cancel token」).

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S6.1 | 위 셋 | `curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $C" -H 'Content-Type: application/json' -d "{\"cancel_token\":\"$CAP_B\"}" http://127.0.0.1:8812/jobs/<id>/cancel` | **403** · `GET /jobs/<id>.joiners` 에 bob·charlie **둘 다** 그대로 · `submissions` 행 셋 그대로. (수정 전: 200 — bob 의 행을 지우고 charlie 의 joiner 행을 뺐다) | 실기 (리뷰 #110 재현) |
| S6.2 | 같은 잡 | 같은 요청을 **bob 의 bearer** 로 | 200 `left: true` · joiners 에서 bob 만 빠짐 · `submissions` 에 bob 행만 삭제 · 잡 `running` | 실기 |
| S6.3 | 새 잡 · bob 합류(`$CAP_B`) | 같은 leave 요청 **두 개를 동시에**: `for i in 1 2; do curl … -w '%{http_code}\n' -o /dev/null … & done; wait` | 정확히 **하나만 200**, 다른 하나는 403 또는 409(500 아님) · joiners 에서 bob 이 한 번 빠짐 · 서버 로그에 트레이스백 없음. 20번 반복해도 같다 | 실기 (리뷰 「동시 재사용 경쟁」 · 명세 S6 「`BEGIN IMMEDIATE`」) |
| S6.4 | S6.2 뒤 | 같은 `$CAP_B` 를 다시 | 200 이 아니다(403/409) — 떠난 참여의 비밀은 죽는다 | 실기 |
| S6.5 | `tests/test_cancel_capability.py`: `Store.remove_submission` 이 `False` 를 돌려주도록 monkeypatch | leave 요청 | 200 이 아니고 joiner 행도 안 빠진다 — `remove_submission()` 결과를 본다 | 자동 (명세 S6 「결과 확인」) |
| S6.6 | — | `sqlite3 … "PRAGMA table_info(submissions)"` · `grep -n "_MIGRATIONS\[17\]" -A12 src/remote_ci_monitor/store.py` | 참여자 이름 열(`name` 또는 `participant`)이 있다 · `DB_VERSION` 은 여전히 **17**(PR 미머지 — DDL 을 제자리에서 고친다, 18 로 올리지 않는다) · `test_cancel_capability.py` 의 v16→v17 마이그레이션 테스트가 새 DDL 로 초록 | 자동 |
| S6.7 | — | `python scripts/mutcheck.py --only <leave 바인딩(이름 일치 검사) 제거 변이 이름>` | caught (명세 §4 mutcheck ④) | 자동 |

### S7 — 상태 파일 다중 프로세스 쓰기 (#110 · 리뷰 #110 B P1 셋째 · 명세 S7) · 8812

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S7.1 | `XDG_STATE_HOME` 셸 · 트리 40개(`tree$i/f` 에 `$i`) | `for r in $(seq 1 20); do rcm run ok --dir tree$((2*r-1)) --no-wait --token $A & rcm run ok --dir tree$((2*r)) --no-wait --token $A & wait; done` → `jq length $XDG_STATE_HOME/rcm/submissions.json` · `jq -r .job_id … \| sort -n \| uniq \| wc -l` | 파일이 유효한 JSON · 항목 **40** · 잡 ID 40개 전부(빠진 것 없음) · `ls $XDG_STATE_HOME/rcm/` 에 `.tmp` 잔재 없음 · 40개 `rcm run` 전부 exit 0. (수정 전: 항목 유실 또는 `os.replace` 실패) | 실기 (리뷰 #110 재현 「`rcm run` 두 개 동시」) |
| S7.2 | — | `grep -n "flock\|getpid\|\.tmp" src/remote_ci_monitor/submissions.py` | `fcntl.flock(…, LOCK_EX)` 뒤 read-modify-write · 임시 파일 이름에 pid(`submissions.json.<pid>.tmp` 꼴) | 코드 |
| S7.3 | 다른 프로세스가 락을 3초 쥔다: `python3 -c 'import fcntl,os,time; p=os.environ["XDG_STATE_HOME"]+"/rcm/submissions.json"; f=open(p,"a+"); fcntl.flock(f,fcntl.LOCK_EX); time.sleep(3)' &`(락 대상 파일이 `.json` 이 아니라 별도 `.lock` 이면 그 파일로) | 곧바로 `rcm run ok --dir tree41 --no-wait --token $A` | 3초쯤 기다렸다가 exit 0 · 항목이 파일에 있다 — 락을 못 얻어 조용히 건너뛰지 않았다(건너뛰는 구현이면 S9.4 의 경고 한 줄이 있어야 한다) | 실기 |
| S7.4 | S7.1 뒤 | `stat -f '%Lp' $XDG_STATE_HOME/rcm/submissions.json $XDG_STATE_HOME/rcm` | `600` · `700` — 동시 쓰기 뒤에도 권한 유지 | 실기 |
| S7.5 | `tests/test_cancel_capability.py`: `multiprocessing` 으로 두 프로세스가 `Barrier` 뒤 동시에 `remember()` | 테스트 | 두 항목 다 남는다 · 리뷰 C 의 「순차 쓰기뿐」 테스트가 실제 다중 프로세스로 바뀌었거나 추가됐다 | 자동 |

### S8 — 잡/합류와 capability 비원자 (#110 · 리뷰 #110 B P2 첫째 · 명세 S8) · 8812

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S8.1 | `tests/test_cancel_capability.py`: `Store.add_submission` 을 `sqlite3.OperationalError("disk I/O error")` 로 monkeypatch | `POST /jobs`(새 잡) | 응답 5xx · `SELECT count(*) FROM jobs` 가 요청 전과 **같다**(잡 행 없음) · `/api/status` 큐 길이 불변 | 자동 (명세 S8 「capability 삽입 실패 주입 → 잡 행 없음」) |
| S8.2 | 같은 패치 · 기존 잡에 합류 | `POST /jobs`(합류) | 5xx · `join_count`·`joiners` 불변 · `submissions` 에 새 행 없음 | 자동 |
| S8.3 | 8812 정상 | `rcm run ok --no-wait` 201 뒤 `sqlite3 … "SELECT count(*) FROM submissions WHERE job_id=<id>"` | 1 — 같은 트랜잭션의 정상 경로가 여전히 한 행 | 실기 |
| S8.4 | — | `grep -n "add_submission\|BEGIN IMMEDIATE" src/remote_ci_monitor/store.py src/remote_ci_monitor/server.py` | `create_job`/`join_or_bump` 가 capability 행을 **같은 트랜잭션** 안에서 만든다(store 메서드가 role 을 받거나 콜백을 받는다) — `server.py` 에서 커밋 뒤에 따로 부르는 `add_submission` 호출이 없다 | 코드 |

### S9 — 200개 상한 · 저장 실패 시 권한 유실 (#110 · 리뷰 #110 B P2 둘째 · 명세 S9) · 8812

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S9.1 | `rcm pause --token $R` · 트리 201개(`tree$i/f`) | `for i in $(seq 1 201); do rcm run ok --dir tree$i --no-wait --token $A; done` → `jq length …/submissions.json` | **201**(또는 그 이상) — 활성(queued) 잡의 항목은 하나도 안 밀렸다. (수정 전: 200) | 실기 (명세 S9 「201개 활성 → 전부 보존」) |
| S9.2 | S9.1 뒤 | `rcm cancel <첫 잡 id> --token $A` | 200 `cancelling` → `cancelled`(첫 항목의 비밀이 살아 있다) | 실기 |
| S9.3 | `rcm resume` · 잡이 다 끝날 때까지(`rcm jobs --state queued` 가 빔) · 새 잡 하나 제출 | `jq length …/submissions.json` | ≤ 200 — 정리는 **종료된 잡**만 뺀다(서버에 물어 활성이면 보존). 정리 뒤에도 아직 `running`/`queued` 인 잡의 항목은 남아 있다(`jq '.[].job_id'` 에 그 id) | 실기 |
| S9.4 | 서버를 **내린 채** 항목 201개인 상태 파일 | `rcm run ok --no-wait --token $A`(연결 실패 exit 3) 또는 정리 함수를 직접 | 항목 수 불변 — 활성 여부를 모르면 지우지 않는다(fail-closed) | 실기 + 자동 |
| S9.5 | `chmod 500 $XDG_STATE_HOME/rcm` | `rcm run ok --dir tree1 --no-wait --token $A` · 같은 것을 `--poll` 로 | 둘 다 stderr 한 줄 `warning: could not save the cancel token … rcm cancel <id> will need --cancel-token` · **비밀은 stderr 에 없다** · `--no-wait` 의 stdout JSON 에는 `submission.cancel_token` 있음(이미 있었다) · exit 는 잡 결과대로 | 실기 (명세 S9 「저장 실패면 stderr 에」) |
| S9.6 | S9.5 의 잡 · `chmod 700` 되돌린 뒤 | `rcm cancel <id> --token $A --cancel-token <JSON 의 값>` | 200 — JSON 의 비밀로 취소된다 | 실기 |
| S9.7 | — | `docs/configuration.md` 「cancel token」 절 | 상한(200)과 「종료된 잡의 항목만 정리」 · 「저장 실패면 `--cancel-token`」이 적혀 있다(리뷰 D 「문서에 없다」) | 문서 |

### S10 — 모르는 role 승격 · stderr 절대경로 · 옛 클라 check (#110 · 리뷰 #110 B P2 셋~다섯째 · 명세 S10) · 8812

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S10.1 | alice 잡 · `sqlite3 … "UPDATE submissions SET role='root_everything' WHERE job_id=<id>"` | `rcm cancel <id> --token $A`(상태 파일의 비밀로) | **403** · 잡 `running` · 서버 로그 트레이스백 없음. (수정 전: `cancel_job` 로 승격되어 취소) | 실기 (리뷰 #110 「알 수 없는 DB role」) |
| S10.2 | `rm $XDG_STATE_HOME/rcm/submissions.json` | `rcm cancel <새 잡 id> --token $A` 2>err.txt · `chmod 500 …/rcm` 뒤 `rcm run … --no-wait` 2>>err.txt | `grep -c "/Users/\|$HOME" err.txt` → **0** · 문장은 `~/.local/state/rcm/submissions.json` 또는 파일 이름만 | 실기 (명세 S10 「`~` 축약 또는 이름만」) |
| S10.3 | **옛 클라 venv**: dev 의 `release: 버전 0.2.6` 커밋(`git log --oneline origin/dev \| grep 'release: 버전 0.2.6'`)을 `git worktree add ../remote_ci_monitor-verify-old-s110 <sha>` 로 받아 자기 venv(`~/.local/share/rcm-verify-m5l-s110-oldvenv`)에 설치 — `__version__` 이 서버와 **같은 0.2.6** 인데 capability 를 모른다 | 옛 `rcm check --server http://127.0.0.1:8812 --token $A` · `curl -s …/api/health \| jq .min_client_version` | health 의 `min_client_version` 이 0.2.6 보다 크다(값을 적는다) · 옛 클라의 `client` 행이 **FAIL**(exit 1) — `same as server` 로 OK 를 내지 않는다(`_client_row` 가 `min_client_version` 을 먼저 본다). 새 클라의 `rcm check` 는 ok | 실기 (리뷰 #110 「옛 클라이언트의 `rcm check` 가 거짓 OK」) |
| S10.4 | 8812 를 `cancel_requires_submission_token = false` 로 재기동 | 옛 클라 `rcm check` | `client` 행 ok(`min_client_version` 0.2.0) — 키 off 는 호환 | 실기 |
| S10.5 | — | `pytest tests/test_cancel_capability.py -q -k "role or unknown"` · `-k "path or tilde or stderr"` · `-k "min_client"` | 각 1건 이상 초록 | 자동 (명세 S10 「각 1건」) |
| S10.6 | 8812(on) · Chrome(`tests/test_web_browser.py` 의 `Chrome` 재사용) · alice 의 잡 | `/?poll=1` 에 **root** 토큰으로 alice 의 잡 행 `[data-cancel="<id>"]` | admin 은 남의 잡 취소 버튼이 **활성**(리뷰 A-4 「웹 admin 예외 미완성」 — 명세 표 밖이지만 같은 PR) · 안 고쳤으면 「확인 못 함(보류)」이 아니라 **오류**로 적고 오너에게 — 문서 `configuration.md` 「admin 이면 웹 버튼 활성」이 거짓이기 때문 | 실기 |
| S10.7 | `rcm run sleepy --no-wait` 뒤 옛 클라 venv 없이 새 클라로 | `rcm wait --job <id>` 를 합류자 세션에서 띄우고 `kill -INT` | 리뷰 A-3(독립 `rcm wait` 의 Ctrl-C 가 참여를 빼지 않는다) — 고쳤으면 `left: true`, 안 고쳤으면 보고서 「보류(명세 밖)」에 실측 | 실기(기록) |

### S11 — `step_timeline` 의 CHANGELOG 위치 · 예외 폭 · 시계 폴백 (#108 · 리뷰 #108 D-1 · B-1 · B-2 · 명세 S11) · 8813

준비: 8813 에 프리셋 `steps3`(`echo ::rcm::steps::3; echo ::rcm::step::a; sleep 1; echo ::rcm::step::b; sleep 1;
echo ::rcm::step::c; exit 0`) 와 `midfail`(`echo ::rcm::step::lint; echo ::rcm::fail::lint; exit 1`).

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S11.1 | PR 브랜치 체크아웃 | `awk '/A finished job keeps its step times/{print "entry", NR} /^## \[0\./{print "release", NR; exit}' CHANGELOG.md` | `entry` 줄 번호 < `release` 줄 번호 — 항목이 `[Unreleased]` 안에 있다(수정 전: `[0.2.6]` 안). 항목을 `[0.2.6]` 아래로 옮겨 보면 `pytest tests/test_docs_step_timeline.py` 가 **빨갛다**(되돌린다) | 문서 + 자동 (리뷰 #108 C-1 「절 위치를 안 잠근다」) |
| S11.2 | `steps3` 잡 하나 끝난 뒤 | `sqlite3 <data_dir>/rcm.sqlite3 "UPDATE events SET payload='x' WHERE id=(SELECT min(id) FROM events WHERE job_id=<id> AND kind='marker')"` → `curl -s -w '\n%{http_code}' …/jobs/<id>` | **200** · `step_timeline: null` · `step_timeline_error_code` 가 비어 있지 않은 문자열(`internal_error` 류 — 경로·SQL·예외 문장 없음) · `state summary failed_step last_step failures artifacts` 그대로 · `rcm wait --job <id>` exit **0**(상태대로 · 3 아님). (수정 전: 500 `internal error`) | 실기 (리뷰 #108 B-1 재현) |
| S11.3 | `midfail` 잡 끝난 뒤 같은 손상 | `GET /jobs/<id>` | 200 · `failures` 가 **비어 있지 않다**(`lint` 항목) — 타임라인이 죽어도 대장은 산다 · `failures[0].step` 은 저장값 기준 | 실기 (리뷰 C-5) |
| S11.4 | `steps3` 잡 · `sqlite3 … "UPDATE jobs SET finished_at=NULL WHERE id=<id>"` | `GET /jobs/<id>` 두 번(1초 간격) → `jq -S .step_timeline` 비교 | 둘 다 `step_timeline: null` + `step_timeline_error_code: "no_finished_at"` · 두 응답이 **같다**(요청마다 커지는 `seconds` 없음). 되돌린다(`UPDATE jobs SET finished_at=…`) | 실기 (리뷰 #108 B-2 재현) |
| S11.5 | — | `pytest tests/test_step_timeline.py -q` · `python scripts/mutcheck.py --only step-timeline-db-error-empty` | 초록 · `ValueError` 짝 테스트(리뷰 C-2)와 `no_finished_at` 테스트(C-3)가 있다 · caught | 자동 |
| S11.6 | — | `docs/configuration.md` `step_timeline` 절의 `ok` 문장 | 「… is `null` unless the script declared it failed」류로 정확하다(리뷰 D-3 · P2) | 문서 |
| S11.7 | — | `grep -n "@ \|632e84c\|0ffb845" docs/acceptance/reports/2026-09-10-m5j-verify-g1.md` | 검증 커밋과 리베이스 커밋의 관계 한 줄(리뷰 D-2 · P2) — 없어도 오류는 아니다(기록) | 문서(기록) |

### S12 — 두 레인 문서: `flock` · `concurrent_at_start` · `step_timeline` 순서 (#106 · 리뷰 #106 B-1~B-3 · 명세 S12) · 8813

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| S12.1 | PR 브랜치의 `docs/configuration.md` 「Two lanes for a gate…」 | 셸 예시를 `<data_dir>/lock.sh` 로 그대로 복사(heavy 자리에 `sleep 3`) → `bash lock.sh & sleep 0.3; time bash lock.sh; wait` **이 Mac 에서** | 두 번째가 첫 번째를 기다린다(`real` ≥ 2.5 s) · `command not found` 없음 — 예시가 macOS·Linux 둘 다 되는 것(`python3 -c 'import fcntl…'` 또는 `mkdir` 락 또는 `lockf`/`flock` 분기) · 락을 못 잡으면 **중단**(`\|\| exit 3` 류)이 예시에 있다. (수정 전: `flock: command not found` 뒤 락 없이 진행) | 실기 (리뷰 #106 B-1 재현) |
| S12.2 | — | `grep -n concurrent_at_start docs/configuration.md src/remote_ci_monitor/server.py src/remote_ci_monitor/core/status.py` | **둘 중 하나**: (a) 문장이 없다(`docs` 0건 · `tests/test_docs_admission_two_lane.py` 가 그 키를 요구하지 않는다), 또는 (b) `GET /jobs/<id>` 가 그 키를 준다 — 8813 을 `lanes = 2` 로 재기동, `sleepy` 둘을 잇달아 제출 → 첫 잡 `concurrent_at_start == 0` · 둘째 `== 1`(정수 · null 아님) · `/api/status` 는 불변 · CHANGELOG 에 키 추가 한 줄 | 문서 + 실기 (리뷰 #106 B-2) |
| S12.3 | — | `gh pr view 108 --json mergedAt -q .mergedAt` · `gh pr view 106 --json mergedAt,state` | #106 이 아직 열려 있으면 「#108 뒤에 머지」가 순서에 있다(명세 §3) · 머지됐으면 `mergedAt(106) > mergedAt(108)` — 또는 문서의 `step_timeline` 문장이 없다 | 문서 |
| S12.4 | — | `gh pr diff 106 --name-only` | `docs/configuration.md` · `tests/test_docs_admission_two_lane.py` · `CHANGELOG.md` 뿐 — `docs/gate-optimization-workplan.md`·`docs/reviews/2026-09-10-codex-…md` 가 **없다**(리뷰 A 「dev 위로 rebase」) | 문서 |
| S12.5 | — | 같은 절 | admission 과 겹침의 조건 한 문장(「A 가 heavy 인 동안 B 는 CPU 상한 때문에 들어오지 못한다 — 얻는 겹침은 light 끼리」 — 리뷰 B-4) · `/tmp/gate-heavy.lock` 이 서버 레인과 워커 레인이 공유하는 머신 전체 락이라는 한 줄(B-5) · CHANGELOG `[Unreleased]` Added 한 줄(D) | 문서 (P2) |
| S12.6 | — | `pytest tests/test_docs_admission_two_lane.py -q` → 락 문단을 지우고 다시(되돌린다) | 초록 → 빨강 · `test_the_experiment_needs_a_lock_in_the_script` 가 `flock` 글자가 아니라 「락」의 뜻을 잠근다(`lockf`·`fcntl` 로 바꿔도 초록) | 자동 (리뷰 C) |

### L1 — 오프라인 dry-run 의 오류 경로 (`fix/gc-offline-error-paths` · 리뷰 #87 B P1 셋 + P2 · 명세 L1) · 서버 없음

준비: 설정 `~/.local/share/rcm-verify-m5l-l1.toml`(`data_dir = "~/.local/share/rcm-verify-m5l-l1"`, 포트는 8814 라 적되
서버는 안 띄운다). **옛 버전 DB 픽스처**: 현재 빌드로 한 번 열어 만든 뒤(`rcm token --config … list`) 서버 없이
`sqlite3 <data_dir>/rcm.sqlite3 "PRAGMA user_version=$((DB_VERSION-1))"` — `DB_VERSION` 은
`python -c 'from remote_ci_monitor.store import DB_VERSION; print(DB_VERSION)'`(dev 16 · #110 뒤 17). 아래 `V` = `DB_VERSION`.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L1.1 | 사본 마이그레이션이 **실제 `sqlite3.OperationalError`** 로 깨지는 DB: v(V-1) 픽스처에서 `sqlite3 … "ALTER TABLE jobs DROP COLUMN last_step"`(v16 의 `UPDATE jobs SET … last_step=NULL` 이 `no such column` 으로 죽는다 — V 가 17 이면 `user_version=15` 로 두어 v16 을 지나게 한다) | `rcm gc --dry-run --config <설정>; echo rc=$?` | **rc=3** · stderr 한 줄 `gc: the copy could not be migrated from schema v… — … Nothing was changed (exit 3: unknown)` · **`Traceback` 없음** · 원본 `PRAGMA user_version` 그대로 · `ls ${TMPDIR:-/tmp} \| grep -c rcm-gc-dryrun` → 0(사본 정리됨). (수정 전: 트레이스백 + rc=1) | 실기 (리뷰 #87 B P1 첫째 재현 · 명세 L1 「raw `sqlite3.OperationalError` 주입 → 3」) |
| L1.2 | `tests/test_offline_gc.py`: `store_mod._MIGRATIONS[V]` 에 `"THIS IS NOT SQL"` 을 끼우거나 `Store.migrate` 를 `sqlite3.IntegrityError` 로 monkeypatch | `main(["gc","--dry-run","--config",…])` | rc 3 · 같은 문장 · 리뷰 C 「`StoreError` 를 직접 던진다」 테스트가 실제 `sqlite3.Error` 로 바뀌었거나 짝이 있다 | 자동 |
| L1.3 | `tests/test_offline_gc.py`: `shutil.rmtree` 를 `OSError(errno.EBUSY, …)` 로 monkeypatch(`cli` 모듈이 참조하는 이름으로) | dry-run | **rc 3** · stderr 에 「사본이 남았다」는 경고와 그 임시 디렉터리(운영자 stderr — 경로 허용) · 계획 자체는 찍혀도 되지만 종료 코드는 3(「지웠다」는 거짓 성공 없음). (수정 전: rc 0) | 자동 (리뷰 #87 B P1 둘째 · 명세 L1 「rmtree 실패 주입 → 3」) |
| L1.4 | 실기로 삭제 실패: `TMPDIR=<data_dir>/tmp` 를 만들고 dry-run 을 돌리는 **동안** `chflags uchg` 를 사본 디렉터리 안 파일에 걸기는 창이 좁다 — `--timeout 600` 에 큰 DB 를 써도 못 맞추면 「확인 못 함(실기)」 | (선택) | L1.3 과 같다 | 실기(선택) |
| L1.5 | **v15.bak 만 있을 때**: 새 `data_dir` 에 DB 를 `user_version = V+1` 로 표시(마이그레이션 없이 `PRAGMA` 만) · `mkdir backup && cp rcm.sqlite3 backup/rcm.sqlite3.v15.bak && sqlite3 backup/rcm.sqlite3.v15.bak "PRAGMA user_version=15"` | `rcm gc --dry-run --config …` · `rcm serve --config …` · `rcm token --config … list` | 셋 다 거절(gc rc 3 · serve/token rc 2) · 문장이 **`backup/rcm.sqlite3.v15.bak`** 를 가리킨다(`v<V>.bak` 이 아니다 — 그 파일은 없다) | 실기 (리뷰 #87 B P1 셋째 재현 「v15 DB 를 v21 빌드로」 · 명세 L1 「실제로 있는 가장 높은 `.bak`」) |
| L1.6 | L1.5 에 `v14.bak` 도 추가 | 같은 셋 | `v15.bak`(가장 높은 것)을 가리킨다 | 실기 |
| L1.7 | L1.5 에서 `backup/` 을 지운다 | 같은 셋 | 문장이 `.bak` 파일 이름을 **안 만들어 내고** 「백업 없음」(`no migration backup found — …`)을 말한다 · `docs/operating.md` 「Going back to the old build」의 복원 예시가 고정 `v<old>.bak` 이 아니라 「메시지가 가리키는 파일」이다 | 실기 + 문서 |
| L1.8 | v(V-1) 픽스처 · `mkdir -p backup && chmod 300 backup`(쓰기·실행만, 읽기 없음) | `rcm token --config … list; echo rc=$?` → `chmod 700 backup; ls backup` | **rc 0** · 마이그레이션 됨(`user_version == V`) · `backup/rcm.sqlite3.v(V-1).bak` 생성 · stderr 에 백업 정리 실패 **경고 한 줄**(`warning: … backup …` — 트레이스백 아님). (수정 전: 조용히 무시) | 실기 (리뷰 #87 B P2 재현 「`iterdir()` 만 실패」 · 명세 L1 「`_prune_backups` 의 `iterdir` 실패 경고」) |
| L1.9 | — | `python scripts/mutcheck.py --only <`sqlite3.Error` 를 안 잡는 변이 이름>` · `grep -c "Mutant(" scripts/mutcheck.py` | caught · 개수를 적는다(명세 §4 mutcheck ①) · mutcheck 주석의 번호(㉔·㉕)가 목록과 맞다(리뷰 C 마지막) | 자동 |
| L1.10 | 회귀: v(V-1) 픽스처 정상 | `rcm gc --dry-run --config …; echo rc=$?` · `rcm gc --dry-run --config … --json \| jq .offline` | rc 0 · `note: planned on a temporary copy …` · 원본 `user_version` 그대로 · `-wal`/`-shm` 이 새로 생기지 않았다 · 사본 정리됨 · JSON `offline.copy == true` · `offline.database` 의 절대경로 여부는 §2 ③ 오너 결정대로(기록) | 실기 |
| L1.11 | — | CHANGELOG `[Unreleased]` · `docs/operating.md` 「Upgrade」 | 「어느 단계든 불완전하면 exit 3」과 「사본을 삭제한다(못 지우면 3 + 경고)」가 이제 참 · 사본 마이그레이션의 SQLite 오류도 3 이라는 말 · PR 링크 | 문서 |

### L2 — 회계: unknown 삭제 · 부분 삭제 · floor 분기 · `budget_unreachable` 나이 (`fix/janitor-unknown-freed-partial-delete` · 리뷰 #88 B 1~4 · 명세 L2) · 8816

준비: 8816 설정에 `workspace_retention_days = 0`(끝난 잡의 워크스페이스가 곧 만료) · 프리셋 `ok`. `rcm gc` 는 HTTP 다 —
`rcm gc --server http://127.0.0.1:8816 --token $R [--json]`. 잡 셋을 `ok` 로 돌려 두면 `workspaces/<id>` 셋과
`jobs/<id>/tree.tar.gz` 셋이 있다.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L2.1 | `tests/test_janitor_m5i.py`: 잡 하나의 `Janitor._measure_now` 가 `None` 을 돌려주도록(또는 `_measure_workspace` → `(None, now)`) monkeypatch — 나이 규칙으로 선택되고 삭제는 성공한다 | `Janitor._run_volume(now)` 의 본문 → `render_gc(body)` | 본문에 **`unknown_count: 1`**(또는 `deleted_unknown_count`) · 렌더 `freed ≥ <아는 합> B from N jobs (1 unknown)` 꼴 — `freed 0 B from 1 jobs` 로 **확정 표시하지 않는다** | 자동 (리뷰 #88 B1 재현 · 명세 L2 「`freed ≥ N B (M unknown)`」) |
| L2.2 | 실기 덤: 잡 하나의 `jobs/<id>` 를 **`chmod 000`**(tar 의 `lstat` 이 EACCES → 측정 불명) | `rcm gc --dry-run --server … --token $R` | `would free … · 1 of unknown size`(이미 있던 표시) · `storage_before.error_code == "measure_EACCES"` — 측정 불명이 0 으로 안 세어진다(회귀). 되돌린다 | 실기 |
| L2.3 | **부분 삭제**: 잡 하나(id P)의 `jobs/P` 를 `chmod 000` — `_purge_volume` 이 `workspaces/P` 는 지우고 `jobs/P/tree.tar.gz` 의 `lstat` 에서 `OSError` | `curl -s …/api/status \| jq .server.job_storage.volume_bytes`(전) → `rcm gc --server … --token $R --json` → 같은 것(후) · `ls <data_dir>/workspaces/` | `failed[]` 에 `{"job_id": P, "error_code": "EACCES"}` · `workspaces/P` **없음** · `storage_after.volume_bytes` **<** `storage_before.volume_bytes`(워크스페이스만큼 줄었다 — 재측정됨) · `/api/status … volume_bytes`(후) 도 줄었다(캐시 무효화). (수정 전: `storage_after == storage_before`) 되돌린다(`chmod 700`) | 실기 (리뷰 #88 B2 재현 「첫 삭제 성공 · 둘째 `OSError`」 · 명세 L2 「부분 삭제도 재측정·캐시 무효화」) |
| L2.4 | `tests/test_janitor_m5i.py`: `_remove_tree` 가 첫 호출(workspace) 성공 · 둘째(tar) `OSError` | `_run_volume` | `storage_after.volume_bytes < storage_before.volume_bytes` · `janitor._sizes` 에 P 없음 | 자동 |
| L2.5 | **floor 가 오류를 가림**: 8816 을 `min_free_bytes = 10995116277760`(10 TiB — 언제나 바닥 아래) 로 재기동 · 잡 하나의 `workspaces/<id>` 안에 `mkdir sub && chmod 000 sub`(측정 EACCES) | `rcm check --server … --token $A` 의 `storage` 행 · `rcm top` 의 storage 줄 | 문장이 **`a size could not be measured … (measure_EACCES)`**(warn) 이지 `… under the … floor, and nothing left to delete`(FAIL) 가 아니다 — `error_code` 가 floor 분기보다 먼저. 되돌린다 | 실기 (리뷰 #88 B3 재현 「`volume_bytes=None`, `error_code=measure_EACCES`, free 가 floor 아래」) |
| L2.6 | `budget_unreachable`: 8816 을 `workspace_storage_max_bytes = 1073741824`(1 GiB, 하한) · `min_free_bytes = 0` 로 재기동 · `sleepy` 잡 실행 중에 그 워크스페이스에 `dd if=/dev/zero of=$RCM_WORKSPACE/pad bs=1m count=1200`(프리셋 argv 에 넣어 잡이 스스로 만들게) | `rcm check` `storage` 행 · `rcm top` | `… over the 1.0 GiB budget, and … is held by running jobs … — nothing the sweep may delete brings it under` 뒤에 **측정 나이**(`· measured 5 s ago` 꼴 — OK 행과 같은 `_measured_ago` 형식) | 실기 (리뷰 #88 B4 · 명세 L2 「`budget_unreachable` 에도 나이」) |
| L2.7 | — | `pytest tests/test_janitor_m5i.py tests/test_render_gc_m5i.py tests/test_cli_m5g.py -q` · `python scripts/mutcheck.py --only <unknown 을 0 으로 세는 변이 이름>` | 초록 · caught(명세 §4 mutcheck ②) · `budget_unreachable` 의 나이 테스트가 있다(리뷰 C) | 자동 |
| L2.8 | — | `curl -s …/api/status \| jq .schema_version` · `jq '.server.job_storage \| keys'` | `schema_version` 이전과 같다(값) · 키 추가만 | 자동 |
| L2.9 | — | `docs/configuration.md` 「re-measures after deleting」 · 「`/api/health` … same numbers」 | 부분 삭제도 재측정한다는 말이 참 · health 문장은 구현에 맞게 좁혀졌거나 health 에 같은 키가 실린다(`curl …/api/health \| jq .storage`) | 문서 |

### L3 — 래퍼의 TOCTOU · 토큰 argv · health 503 · `/client/…/` (`fix/update-client-wrapper-token-and-tmp` · 리뷰 #90 B · 명세 L3) · 8817

준비: 8817 은 `read_auth = "basic"`(토큰 필요) · 새 venv `~/.local/share/rcm-verify-m5l-l3-venv`(`python3.11 -m venv`,
rcm 없음). 래퍼는 `examples/session/update-client.sh`. `WHEEL=$(curl -s -H "Authorization: Bearer $A" …/api/health | jq -r .client_wheel.path | xargs basename)`.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L3.1 | **공용 경로에 내 파일**: `TMPDIR=<data_dir>/tmp` · `echo MINE > $TMPDIR/$WHEEL && chmod 444 $TMPDIR/$WHEEL` | `RCM_SERVER=http://127.0.0.1:8817 RCM_TOKEN=$A RCM_VENV=<venv> TMPDIR=$TMPDIR bash examples/session/update-client.sh; echo rc=$?` → `cat $TMPDIR/$WHEEL` · `ls $TMPDIR` | rc 0 · `rcm <서버 버전>` 이 찍힘 · **`$TMPDIR/$WHEEL` 은 여전히 `MINE`**(덮어쓰지 않았다 — 검증·설치가 고유 임시 디렉터리 안에서) · 끝난 뒤 `$TMPDIR` 에 래퍼가 만든 파일·디렉터리 없음. (수정 전: `mv` 가 그 파일을 덮었다) | 실기 (리뷰 #90 B P1 첫째 「같은 이름의 기존 사용자 파일도 덮어쓴다」) |
| L3.2 | `tests/test_docs_client_wheel.py`: 가짜 서버 둘(같은 버전 · **다른 바이트**·다른 sha) · venv 둘 | 래퍼 둘을 동시에(`subprocess.Popen` ×2) | 각 venv 에 **자기 서버가 준 wheel** 이 설치됐다(`pip show -f` 의 RECORD 해시 또는 설치 파일의 sha256 비교) — 한쪽이 다른 쪽의 wheel 을 설치하지 않았다 | 자동 (리뷰 「동일 버전 서로 다른 dev 서버」) |
| L3.3 | **토큰이 argv 에**: 느린 health — 5초 자고 응답하는 파이썬 가짜 서버(`test_docs_client_wheel.py` 의 `FakeWheelServer` 를 딜레이 옵션으로 확장하거나 스크래치에 10줄) · `RCM_TOKEN=rcmverify$(head -c 12 /dev/urandom \| base64)` | 래퍼를 배경으로 띄우고 2초 뒤 `ps -axo args \| grep -c "$RCM_TOKEN"` · `ps -axo args \| grep -c "Authorization: Bearer"` | 둘 다 **0**(자기 grep 은 `grep -v grep`) · `grep -n 'Bearer' examples/session/update-client.sh` 에 `-H "Authorization: Bearer $RCM_TOKEN"` 꼴이 없고 `-H @<파일>` 또는 `--config`/`-K` 파일을 쓴다 · 그 파일은 0600 이고 끝나면 지워진다 | 실기 + 코드 (리뷰 #90 B P1 둘째 · 명세 L3 「ps 검사」) |
| L3.4 | **health 503 + `client_wheel` 정상**: 가짜 서버가 `/api/health` 를 503 으로(본문에 `version`·`client_wheel` 포함) + `/client/<name>` 정상 | ① 래퍼 ② `rcm check --server <가짜> --token x` ③ 옛 wheel(다른 버전)로 만든 venv 의 `rcm run ok --server <가짜> …` | ① 설치가 **된다**(503 이어도 본문으로 판정 — `curl -f` 로 죽지 않는다) 또는 stderr 가 「서버 health 가 503 이지만 wheel 은 정상 — 설치한다」를 말한다 ② `client` 행이 **있고** 버전 판정을 한다(생략하지 않는다) ③ 버전 경고 줄이 나온다. (수정 전: 셋 다 판정이 사라졌다) | 실기 (리뷰 #90 B P2 첫째 재현 · 명세 L3 「health 503 이어도」) |
| L3.5 | 8817 | `curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $A" http://127.0.0.1:8817/client/$WHEEL/` · 같은 것을 `…/client/$WHEEL` · `…/client/x.whl` | **404** · 200 · 404 (수정 전: 첫째가 200) · 404 본문의 `hint` 가 맞는 이름을 준다 | 실기 (리뷰 #90 A 「trailing-slash 별칭」 · 명세 L3) |
| L3.6 | — | `pytest tests/test_client_wheel.py tests/test_docs_client_wheel.py -q` · `python scripts/mutcheck.py --only client-wheel-any-name` | 초록 · 다른 이름 테스트에 `<정확한 이름>/` 케이스 · caught | 자동 |
| L3.7 | — | `grep -c '[가-힣]' examples/session/update-client.sh` | > 0 — 주석이 한국어(리뷰 #90 A 「주석 영어」 · 집안 규칙) | 문서 |
| L3.8 | — | `docs/operating.md` 「Keeping clients on the server's version」 | 인라인 블록과 스크립트가 같은 것을 하거나 문서가 스크립트를 가리킨다(리뷰 D) · 「어느 단계가 실패해도 아무것도 설치되지 않는다」가 pip 중간 실패 한정을 말한다 · `docs/images/ui/cli-check.png` 에 `client` 행(스크린샷 재생성은 `docs` 스킬 — 안 됐으면 「확인 못 함」이 아니라 오류) | 문서 |

### L4 — 가드: 서비스 venv 의 정체 · serve/worker 유효 `data_dir` · `env` 플래그 · `cd` · XDG (`fix/guard-service-venv-identity` · 리뷰 #89 B-1 ~ B-7 · #93 B-1 · 명세 L4) · 서버 없음(8818 예약)

가드는 `tools/guard_production.py` 다. 판정은 `decide()`(순수 함수 — `tests/test_guard_production.py` 의 `bash()` 도우미로
부른다), 발견은 `find_production()`(I/O). **운영 설치를 흉내 낸 가짜 홈** `F=~/.local/share/rcm-verify-m5l-l4/home` 을
만든다: `F/.local/share/rcm-venv`(venv · 이 워크트리와 **별개의** 체크아웃 `~/.local/share/rcm-verify-m5l-l4/prod-checkout`
— `git worktree add … origin/main` — 을 `pip install -e` 로 설치) · `F/.local/bin/rcm -> ../share/rcm-venv/bin/rcm`(심링크) ·
`F/.config/rcm/server.toml`(`data_dir = "~/.local/share/rcm"`) · `F/.local/share/rcm/`(빈 디렉터리). 훅은 **`HOME=$F`** 로
부른다 — 실제 운영 설치는 절대 안 본다. 훅 입력은 stdin JSON `{"tool_name":"Bash","tool_input":{"command":"…"},"cwd":"…"}`,
출력은 `hookSpecificOutput.permissionDecision`(`deny`/`ask`) 또는 아무것도 없음(허용).

```sh
guard() {  # $1 = 명령 · $2 = cwd · 나머지 = 환경(예: PATH=…)
  printf '{"tool_name":"Bash","tool_input":{"command":%s},"cwd":%s}' "$(jq -Rn --arg c "$1" '$c')" "$(jq -Rn --arg d "$2" '$d')" \
    | env -i HOME="$F" "${@:3}" python3 tools/guard_production.py | jq -r '.hookSpecificOutput.permissionDecision // "allow"'
}
```

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L4.1 | **활성화된 워크트리 venv**: `PATH=<워크트리>/.venv/bin:/usr/bin:/bin`(가짜 `F/.local/bin` 은 PATH 에 **없다**) | `guard "rcm token --config $F/.config/rcm/server.toml add x" <워크트리> PATH=<워크트리>/.venv/bin:/usr/bin:/bin` | **deny** · 사유에 「migrat」와 운영 `data_dir` · 사유의 「own rcm」이 `F/.local/share/rcm-venv/bin/rcm` 이다(워크트리 venv 가 아니다). (수정 전: 허용 — PATH 의 `rcm` 을 운영 venv 로 잘못 잡았다 = 2026-09-10 사고 모양) | 실기 (리뷰 #89 B-1 재현 · 명세 L4 「활성 venv 셸 시뮬레이션에서 deny」) |
| L4.2 | 같은 PATH | `python3 -c 'import os,sys; os.environ["HOME"]=…; sys.path.insert(0,"tools"); import guard_production as g; print(g.find_production())'` 를 `env -i HOME=$F PATH=… python3 …` 로 | `checkout == <prod-checkout>` · `venv == F/.local/share/rcm-venv` — 발견이 PATH 가 아니라 **editable 설치의 `direct_url.json` 이 운영 체크아웃(main)을 가리키는 venv** 로 정해진다 · 워크트리 venv 는 후보에서 빠진다(그 `direct_url.json` 은 워크트리를 가리킨다) · 보고서에 발견 규칙(어떤 후보를 어떤 순서로) | 실기 + 코드 |
| L4.3 | 정상 PATH(`F/.local/bin` 첫째) | `guard "$F/.local/share/rcm-venv/bin/rcm token --config $F/.config/rcm/server.toml list" …` · `guard "rcm token --config … list"` | 첫째 **allow**(서비스 자신의 `rcm`) · 둘째 allow(PATH 의 `rcm` 이 운영 심링크) — 면제가 살아 있다 | 실기 |
| L4.4 | **설정 사본**: `cp $F/.config/rcm/server.toml <data_dir>/copy.toml`(data_dir 만 운영) | `guard "rcm serve --config <copy.toml>"` · `guard "rcm worker --config <copy.toml>"` · `guard "RCM_SERVER_DATA_DIR=$F/.local/share/rcm rcm serve --config <시험 설정>"` · `guard "rcm worker --data $F/.local/share/rcm"` | 넷 다 **deny**(`serve`·`worker` 도 `_effective_data_dir`). (수정 전: 허용) | 실기 (리뷰 #89 A-1/B-2 재현) |
| L4.5 | — | `guard "rcm serve --config <시험 설정>"` · `guard "RCM_SERVER_DATA_DIR=<시험 data_dir> rcm serve --config <copy.toml>"` | 둘 다 allow — 오탐 없음(env 가 설정을 이긴다 · CLI 와 같은 순서) | 실기 |
| L4.6 | — | `guard "env -i PATH=/x rcm token --config $F/.config/rcm/server.toml list"` · `guard "env -u RCM_CONFIG rcm token --config … list"` · `guard "env RCM_CONFIG=$F/.config/rcm/server.toml -- rcm token list"` | 셋 다 **deny**. (수정 전: 셋 다 허용 — `argv[0]` 이 `-i`/`-u`/`--`) | 실기 (리뷰 #89 B-4 재현) |
| L4.7 | — | `guard "env -u RCM_SERVER_DATA_DIR rcm token --config <copy.toml> list"` | deny(설정 사본이 운영을 가리킨다) — `-u` 뒤 이름을 건너뛴다(#93 B-3) | 실기 |
| L4.8 | — | `guard "(rcm token --config $F/.config/rcm/server.toml list)"` · `"{ rcm token --config … list; }"` · `"exec rcm token --config … list"` · `"nohup rcm token --config … list"` · `"time rcm token --config … list"` · `"command rcm token --config … list"` | 전부 **deny**(리뷰 B-3 의 「에이전트가 흔히 쓰는 모양」). `bash -c '…'` · `xargs` · `uv run` · `$(…)` 는 **보류**(명세 §1 「보류」) — 실측만 적는다 | 실기 |
| L4.9 | **`cd` 가 cwd 를 바꾼다**: `mkdir <data_dir>/wt && printf '[server]\ndata_dir = "%s/wt/data"\n' <data_dir> > <data_dir>/wt/rcm.toml` | `guard "cd <data_dir>/wt && rcm token list" <워크트리>` · `guard "cd /usr && rcm token list" <data_dir>/wt` | 첫째 **allow**(그 폴더의 `./rcm.toml` 을 집는다 — 수정 전 deny 오탐) · 둘째 **deny**(세션 cwd 의 `rcm.toml` 은 거기서 안 통하고 운영 설정으로 내려간다 — 수정 전 허용 미탐) | 실기 (리뷰 #89 B-6 재현) |
| L4.10 | **XDG**: `X=<data_dir>/xdg` · `X/rcm/server.toml`(`data_dir = "<data_dir>/xdg-data"`) | `guard "XDG_CONFIG_HOME=$X rcm token list"` · 같은 것을 `env -i HOME=$F XDG_CONFIG_HOME=$X` 세션 환경으로 | **allow**(CLI 는 XDG 를 `~/.config/rcm` 보다 먼저 본다 — 수정 전 deny 오탐) | 실기 (리뷰 #89 B-7 재현) |
| L4.11 | **빈 `RCM_SERVER_DATA_DIR=`**: 워크트리 `.venv` 로 · 시험 설정 `<data_dir>/t.toml`(`data_dir = "<data_dir>/t"`) · `cd <data_dir>/cwdtest` | `RCM_SERVER_DATA_DIR= ./.venv/bin/rcm token --config <data_dir>/t.toml list; ls` · `guard "RCM_SERVER_DATA_DIR= rcm token --config <copy.toml> list"` | `docs/configuration.md` 에 **`RCM_<SECTION>_<KEY>` 환경 덮어쓰기 절**이 있고 빈 값의 뜻이 적혀 있다(#93 D) · 그 문서대로다: 「빈 값은 무시」면 `cwdtest/` 에 `rcm.sqlite3` 가 **생기지 않고** 설정의 `data_dir` 을 쓴다; 「빈 값 = 현재 디렉터리」로 두기로 했으면 문서가 그렇게 말하고 가드도 같은 뜻(사본 명령의 판정을 적는다). 어느 쪽이든 문서·CLI·가드 셋이 같다 | 실기 + 문서 (리뷰 #93 B-1 재현 · 명세 L4 「빈 `RCM_SERVER_DATA_DIR=` 문서」) |
| L4.12 | **살아남은 돌연변이 4종**(리뷰 #89 C 표): 스크래치에 `tools/guard_production.py` 사본을 만들어 하나씩 — ① `local_config` 일 때 `./rcm.toml` 을 안 읽고 `return None` ② `_effective_data_dir` 의 `or os.environ.get("RCM_CONFIG")` 제거 ③ `_config_data_dir` 가 `data_dir` 키 없는 설정에 기본값 대신 `None` ④ 마지막 기본값 `return … data_dir` → `None` | 각 변이마다 `pytest tests/test_guard_production.py -q`(사본을 `tools/` 에 잠깐 덮고 되돌리거나 `PYTHONPATH` 로 사본을 먼저 잡게) | 넷 다 **빨강** ≥ 1 (수정 전: 넷 다 54 passed). `scripts/mutcheck.py` 가 `tools/` 도 다룬다면 거기에 있고 `--only` 로 caught | 자동 (명세 L4 「11개 변이 중 살아남은 4개 잠금」) |
| L4.13 | 심링크: `ln -s $F/.local/share/rcm <data_dir>/link` · `cd <data_dir>` | `guard "rcm token --data-dir link list" <data_dir>` | 실측을 적는다(리뷰 B-5 · 명세 밖 — deny 면 좋고, allow 면 「보류」로 기록) | 실기(기록) |
| L4.14 | — | `pytest tests/test_guard_production.py -q` · `grep -n "RCM_CONFIG" tests/test_guard_production.py` · `grep -n "RCM_SERVER_DATA_DIR\|copy of the\|사본" docs/operating.md AGENTS.md CLAUDE.md` · CHANGELOG | 초록 · 세션 env `RCM_CONFIG` 테스트가 있다 · 가드 문단이 `RCM_SERVER_DATA_DIR` 과 설정 **사본**을 말한다 · CHANGELOG `[Unreleased]` 에 가드 한 줄(#89 D · #93 D) · 모듈 docstring 「순수 함수 — 시계도 파일도 안 본다」가 실제(`os.environ`·`config.open`·`_which`)와 맞게 고쳐졌다(#93 C) | 자동 + 문서 |

### L5 — `serve` 가 포트를 잡기 전에 마이그레이션 · `sqlite3.Error`/`OSError` 한 줄 (`fix/serve-bind-before-migrate` · 리뷰 #94 B-1 ~ B-3 · 명세 L5) · 8814 / 8815

준비: 8814 는 정상 서버(자기 `data_dir`, 현재 버전). **8815 는 점유용** — `python3 -m http.server 8815 --bind 127.0.0.1
>/dev/null 2>&1 &`. 옛 버전 DB 픽스처는 L1 과 같은 방법(`user_version = V-1`), 설정 `…-l5.toml` 은 `port = 8815`.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L5.1 | 8815 점유 · v(V-1) 픽스처 | `rcm serve --config …-l5.toml; echo rc=$?` → `sqlite3 <data_dir>/rcm.sqlite3 "PRAGMA user_version"` · `ls <data_dir>` | **rc 2** · stderr 한 줄 `rcm: cannot start server: Address already in use` · **`user_version` 이 V-1 그대로** · `backup/` **없음** · `rcm.sqlite3-wal` **없음**(DB 를 연 적이 없다). (수정 전: `user_version == V` + `backup/…v(V-1).bak` 생김 — 「도는 서비스에 새 빌드를 치면 DB 가 먼저 바뀐다」) | 실기 (리뷰 #94 B-1 재현 · 명세 L5 「포트 점유 상태에서 `serve` → `user_version` 불변」) |
| L5.2 | **도는 rcm 서버**에: 8814 가 자기 `data_dir` 로 도는 중 · 같은 설정 파일의 사본에 `data_dir` 만 v(V-1) 픽스처로 바꾼 것 | `rcm serve --config <사본>; echo rc=$?` → `curl -s …:8814/api/health \| jq .ok` | rc 2 · 같은 한 줄 · 픽스처 `user_version` 불변 · 8814 는 계속 200 | 실기 (리뷰 「시험 설정으로 서버를 띄운 채 같은 설정·다른 빌드로」) |
| L5.3 | 8815 를 풀고(`kill %1`) | `rcm serve --config …-l5.toml`(배경) → 로그 | 뜬다 · `backup/rcm.sqlite3.v(V-1).bak` 생성 · `user_version == V` · `listening on http://127.0.0.1:8815` — bind → Store → App 순서로도 정상 경로는 그대로 | 실기 |
| L5.4 | `bind = "10.255.255.1"`(이 Mac 에 없는 주소) · v(V-1) 픽스처 | `rcm serve --config …` | rc 2 · 한 줄 `Can't assign requested address`(또는 그 OS 문장) · `user_version` 불변 | 실기 |
| L5.5 | **깨진 파일**: `printf 'garbage\n' > <data_dir>/rcm.sqlite3` | `rcm serve --config …; echo rc=$?` · `rcm token --config … list; echo rc=$?` → `lsof -nP -iTCP:8815 -sTCP:LISTEN` | 둘 다 **rc 2** · 한 줄(`file is not a database` 포함) · `Traceback` 없음 · serve 가 잠깐 잡았던 8815 는 프로세스 종료로 풀렸다(`lsof` 빈 출력). (수정 전: 트레이스백 rc 1) | 실기 (리뷰 #94 B-2 실행 ①) |
| L5.6 | **읽기 전용 `data_dir`**: 현재 버전 DB · `chmod 500 <data_dir>` | 같은 둘 → `chmod 700` | rc 2 · 한 줄(`attempt to write a readonly database` 또는 `Permission denied`) · 트레이스백 없음 | 실기 (리뷰 B-2 ②) |
| L5.7 | **`data_dir` 이 파일**: `touch <data_dir2>` 를 설정의 `data_dir` 로 | `rcm token --config … list` · `rcm token --config … add x` · `rcm token --config … revoke x` · `rcm serve --config …` | 넷 다 rc 2 · 한 줄(`File exists`/`Not a directory`) · **`token` 도 `OSError` 를 잡는다**(수정 전: `token` 은 트레이스백) | 실기 (리뷰 #94 B-3 재현) |
| L5.8 | **쓰기 잠금**: `python3 -c 'import sqlite3,time; c=sqlite3.connect("<data_dir>/rcm.sqlite3"); c.execute("BEGIN IMMEDIATE"); time.sleep(12)' &` | 곧바로 `time rcm token --config … add locked; echo rc=$?` | 약 5초(`busy_timeout`) 뒤 rc 2 · 한 줄 `database is locked` · 트레이스백 없음 | 실기 (리뷰 특기 「확인 못 함」이던 것) |
| L5.9 | 프리셋 없는 설정 + v(V+1) DB | `rcm serve --config …` 2>err | stderr 두 줄(`warning: no [[presets]] …` + 거절) — 「한 줄」 테스트가 프리셋 있는 설정으로만 돈다는 사실을 보고서에(리뷰 B-5 · 오류 아님) | 실기(기록) |
| L5.10 | — | `pytest tests/test_cli_serve_refusal.py -q` | 포트 점유 테스트(소켓을 먼저 `bind` 해 두고 `main(["serve",…])` → `version_of(db)` 불변) · 깨진 파일 · 읽기 전용 · `data_dir` 파일 · `add`/`revoke` parametrize — 다 있다 | 자동 (리뷰 C 「B-1 테스트 없음」 · 명세 L5) |
| L5.11 | — | CHANGELOG `[Unreleased]` · `[0.2.6]` 의 #94 항목 | 「DB 를 못 열면 한 줄」이 `sqlite3.Error`·`OSError` 까지 참 · 「포트를 못 잡으면 DB 를 안 연다」 한 줄 · 종료 코드 2/3 의 차이(`gc --dry-run` 은 3)가 한 문장(B-4) | 문서 |

### L6 — 태그 워크플로: 조상이면 무동작 · 재실행 절차 · 권한 · smoke `ref` (`ci/tag-release-noop-when-tag-is-ancestor` · 리뷰 #85 B-1 ~ B-4 · D · 명세 L6) · 서버 없음

`main` 에는 push 할 수 없으니 **`run:` 블록을 꺼내 스크래치 레포에서 돌린다**: `python3 - <<'PY'` 로
`.github/workflows/tag-release.yml` 을 `yaml`(dev extra 에 있으면) 또는 정규식으로 읽어 `id: tag` 스텝의 `run` 을
`<data_dir>/tagstep.sh` 로 저장. 스크래치: `git init s && cd s && git commit --allow-empty -m a && git commit
--allow-empty -m b` · `git init --bare ../origin.git && git remote add origin ../origin.git && git push -u origin HEAD`.
환경: `TAG=v9.9.9` · `GITHUB_SHA=$(git rev-parse HEAD)` · `GITHUB_OUTPUT=<data_dir>/out`.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L6.1 | **태그가 조상 커밋에**: `git tag -a v9.9.9 -m x HEAD~1 && git push origin refs/tags/v9.9.9` | `bash tagstep.sh; echo rc=$?; cat $GITHUB_OUTPUT` | **rc 0** · `created=false` · 출력에 「already … ancestor … nothing to do」류 · `::error::` 없음 · origin 의 태그 불변. (수정 전: rc 1 「bump `__version__` instead of reusing it」) | 실기 (리뷰 #85 B-1 재현 「태그 `vX` 가 있는 상태에서 `__version__` 그대로인 커밋을 main 에 push」) |
| L6.2 | **태그가 조상이 아닌 커밋에**(버전 재사용): `git checkout -b other HEAD~1 && git commit --allow-empty -m c && git tag -f -a v9.9.9 -m x && git push -f origin refs/tags/v9.9.9 && git checkout -`(GITHUB_SHA 는 원래 HEAD) | `bash tagstep.sh; echo rc=$?` | **rc 1** · `::error::… already exists at … not an ancestor …` — 진짜 재사용만 거절 | 실기 |
| L6.3 | 태그가 **같은 커밋**에 | 같은 실행 | rc 0 · `created=false` · 「already points at」 | 실기 |
| L6.4 | 태그 없음(`git push origin :refs/tags/v9.9.9`) | 같은 실행 → `git ls-remote --tags origin` | rc 0 · `created=true` · origin 에 `v9.9.9` 가 `GITHUB_SHA` 에(주석 태그) · 브랜치 push 없음 | 실기 |
| L6.5 | — | `grep -n "fetch-depth\|merge-base --is-ancestor" .github/workflows/tag-release.yml` | `merge-base --is-ancestor` 사용 · `actions/checkout` 에 `fetch-depth: 0`(조상 판정에 이력이 필요하다) | 문서 |
| L6.6 | — | `python3 -c 'import re,sys; t=open(".github/workflows/tag-release.yml").read(); print(bool(re.search(r"^permissions:", t, re.M)))'` · `grep -n "secrets: inherit" .github/workflows/tag-release.yml` | 워크플로 수준 `permissions:` **없음**(잡 수준 `jobs.tag.permissions.contents: write`) · `secrets: inherit` **없음** · `tests/test_ci_release_workflow.py` 의 `…_at_workflow_level` 테스트가 잡 수준으로 바뀌었다 · `test_tag_release_caller_permissions_cover_every_called_job` 초록 | 자동 + 문서 (리뷰 B-3) |
| L6.7 | — | `grep -n "ref: refs/tags" .github/workflows/release.yml` | `build`·`smoke`·`github-release` **셋** 다(`smoke` 추가) · `test_release_checks_out_the_tag_it_was_given` 이 셋을 돈다 | 자동 (리뷰 B-4 · C-3) |
| L6.8 | — | `sed -n '/## Releasing/,/^## /p' CONTRIBUTING.md` | 「버전을 안 올린 머지는 무동작」이 구현과 같다 · 태그 뒤 실패의 재실행 절차가 **Actions Re-run** 또는 **태그 삭제 후 재푸시**(`git push origin :refs/tags/vX` → `git tag … && git push origin vX`)로 적혀 있고 「이미 있는 태그를 다시 push」가 아니다 | 문서 (리뷰 B-2 · D-2) |
| L6.9 | — | `pytest tests/test_ci_release_workflow.py -q` · `bash -n tagstep.sh` · CHANGELOG | 초록 · `merge-base --is-ancestor` 문구를 잠근 테스트(리뷰 C-1) · CHANGELOG `[Unreleased]` 에 릴리스 자동화 한 줄(D-3) | 자동 + 문서 |
| L6.10 | v0.2.6 main 머지가 이미 됐으면 | `gh run list --workflow tag-release.yml -L 5` · `gh release view v0.2.6` | 첫 실행 결과(태그 · Release · 노트)를 보고서에(리뷰 C-4) · 안 됐으면 「오너 대기」 | 기록 |

### L7 — Chrome 붙이기의 찬 기동 마감 · 수집기 부재 잠금 (`test/web-browser-attach-deadline` · 리뷰 #98 B-1 · #82 B-1 · 명세 L7) · 서버 없음(테스트가 자기 서버)

준비: 이 Mac 의 Chrome(`tests/test_web_browser.py` `find_chrome()` — 없으면 「확인 못 함」이지 skip 통과가 아니다).
**변이는 스크래치 사본**(`cp -R <워크트리> <scratch>/mut` — 레포 밖)에서 한다.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L7.1 | **`Target.getTargets` 가 느리다**: 사본의 `Chrome.call` 에서 `deadline = time.monotonic() + timeout` 줄 **뒤**, `_read_msg` 앞에 `if method == "Target.getTargets" and not getattr(self, "_slow_once", False): self._slow_once = True; time.sleep(20)` | `pytest tests/test_web_browser.py -q -k test_desktop_dom_shows_running_and_queued_jobs` (사본에서) | **통과**(20 s 지연에도 60 s 마감 안). (수정 전: `AssertionError: CDP: no reply before the deadline` — 프레임 `_attach_first_page` → `call`) | 실기 (리뷰 #98 B-1 재현 프레임 · 명세 L7) |
| L7.2 | 같은 지연을 `Target.attachToTarget` 에 | 같은 테스트 | 통과 | 실기 |
| L7.3 | 같은 지연(20 s)을 `Runtime.evaluate`(페이지가 붙은 뒤의 호출)에 | 같은 테스트 | 약 15 s 뒤 **실패**(`no reply before the deadline`) — 붙은 뒤의 호출은 15 s 그대로, 「늘어진 페이지를 숨기지 않는다」 | 실기 (리뷰 B-3 의 약속) |
| L7.4 | **수집기 제거**(리뷰 #82 mutant M2): 사본의 `Chrome.__init__` 에서 `Page.addScriptToEvaluateOnNewDocument` 호출을 지운다 | `pytest tests/test_web_browser.py -q -k "local_host_card or remote_worker_sample"` | **빨강** — 메시지가 수집기 부재를 말한다(`__rcmErrors is not installed` 류) · `page_errors()` 가 `[]` 를 돌려주지 않는다(None/예외). (수정 전: 2 passed) | 실기 (리뷰 #82 B-1 실측 · 명세 L7 「수집기 누락을 잠금」) |
| L7.5 | **B1 되돌리기**(mutant M1): 사본 `src/remote_ci_monitor/web/app.js` 의 `h.source === "local"` 을 `local` 로 | `-k local_host_card` | 빨강 `page raised while loading …: ['… local is not defined']` — 회귀 확인 | 실기 |
| L7.6 | — | `python scripts/mutcheck.py --only <수집기 제거 변이 이름>` · `grep -c "Mutant(" scripts/mutcheck.py` | caught(명세 §4 mutcheck ③) · 개수 | 자동 |
| L7.7 | — | `sed -n '150,175p' tests/test_web_browser.py` | 주석이 「`Target.getTargets`·`attachToTarget` 에도 찬 기동 마감」을 말하고 「3.13」 한정이 없다(3.11 에서도 났다) · `_attach_first_page` 가 남은 시간(`max(1.0, deadline - time.monotonic())`)이나 `COLD_START_SECONDS` 를 두 `call` 에 넘긴다 | 코드 (리뷰 B-2) |
| L7.8 | 머지 뒤 | `gh run list --branch dev --workflow ci.yml -L 5 --json databaseId,conclusion` → 각 run 의 `unit (ubuntu-latest, 3.13, google-chrome)` | 최근 5개 run 에 `test_desktop_dom_…` 의 `no reply before the deadline` 이 **0건**(`gh run view <id> --log-failed \| grep -c "no reply"`) | 기록 (리뷰 B-1 의 세 run) |
| L7.9 | — | `pytest tests/test_web_browser.py -q -k find_chrome` | `RCM_CHROME` 이 있으면 다른 후보로 안 넘어간다는 단위 테스트(리뷰 #82 C · P2) — 없으면 기록 | 자동(기록) |

### L8 — `hosts[].disk.path` 의 공개 노출 (`fix/status-disk-path-not-public` · 리뷰 #84 B-1 · 명세 L8 · **오너 결정** §2) · 8814 재사용

오너가 정한 뒤에 돌린다. 8814 설정은 `data_dir = "~/.local/share/rcm-verify-m5l-l1"`(`~` 표기 — #84 의 조건).

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| L8.1 | 8814 · `read_auth = "none"` | `curl -s http://127.0.0.1:8814/api/status \| jq '.pools[].hosts[].disk'` | **오너 = 뺀다**: `path` 키 **없음** · `used_bytes free_bytes total_bytes` 는 있음(`total_bytes > 0`) · `curl … \| grep -c "$HOME"` → 0. **오너 = `basic` 일 때만**: `none` 이면 없음, `read_auth = "basic"` + 자격으로 요청하면 있음 | 실기 (리뷰 #84 B-1 재현 `jq '.pools[0].hosts[0].disk.path'`) |
| L8.2 | 원격 워커(`$W` · 8814 · pool) 하나 등록 | 같은 `jq` 의 워커 호스트 카드 | heartbeat 표본도 같은 규칙(워커 `data_dir` 경로 없음) | 실기 |
| L8.3 | — | `pytest tests/test_hostsample_disk_path.py -q` · `grep -n '"path"' tests/test_hostsample_disk_path.py` | `sample.disk["path"] == sampler.disk_path` 단언이 사라지고 `total_bytes > 0` 으로(리뷰 B-2) · `sampler.disk_path` 가 절대경로라는 단언은 남는다(B5 의 수정은 유지) | 자동 |
| L8.4 | Chrome | `pytest tests/test_web_browser.py -q -k "local_host_card or remote_worker_sample"` | 디스크 막대가 그대로 그려진다(`disk_meters == 2`) — 소비자가 없었으니 화면 불변 | 자동 |
| L8.5 | — | `docs/gate-replay-fixes-workplan.md` I4 「서버는 보안상 `data_dir` 을 API 로 안 내리므로」 · PLAN 「보안」 · CHANGELOG | 코드와 같은 말(뺐으면 그대로 참 · 남겼으면 문장을 고쳤다) · 기동 배너 `data <경로>` 는 로그라 그대로 | 문서 |

### LX — 가로지르기 (전 항목 · 마지막 머지 뒤 `origin/dev`)

| ID | 절차 | 기대 | 방법 |
|---|---|---|---|
| LX.1 | `ruff check . && ruff format --check . && pytest` · `node --test tests/web/*.test.js` · `python scripts/mutcheck.py` | 전부 초록 · mutcheck 는 **전부 caught** 이고 돌린 개수 == `grep -c "Mutant(" scripts/mutcheck.py` == AGENTS.md·CONTRIBUTING.md 의 「N known mutations」(명세 §4 의 4종 ①②③④가 다 있다 — 이름을 적는다) | 자동 |
| LX.2 | `CHANGELOG.md` | `[Unreleased]` 에 #109(S1·S2 반영) · #110(S5~S10) · #108(S11 — `[0.2.6]` 에서 옮겨 옴) · #106(S12) · L1 · L2 · L3 · L4 · L5 · L6 · L8 항목이 각각 **PR 링크**와 함께 · `## [0.2.6]` 절에는 M5j·M5l 항목이 **없다**(`awk` 로 절 경계 확인) · `schema_version` 불변 명시 | 문서 |
| LX.3 | 검증에 쓴 모든 서버의 `curl …/api/status` · 모든 잡의 `GET /jobs/<id>` · `rcm jobs --json` · `<data_dir>/logs/` 전부 · `rcm run`/`cancel`/`check` 의 stderr 모음 | `grep -rn "$HOME\|/Users/\|/opt/homebrew" …` 에서 나오는 줄이 **기동 배너의 `data_dir` 한 줄**과 `rcm check` 의 `local data dir … from <config>`(로컬 터미널 — 허용)뿐 · S10.2 의 stderr 도 0 | 자동 |
| LX.4 | 8812 의 `strings <data_dir>/rcm.sqlite3 <data_dir>/rcm.sqlite3-wal \| grep -c "$CAP"` · `grep -c "$CAP" server.log` · `curl …/api/status \| grep -c "$CAP"` (아무 capability 하나) | 평문 **0** · `capability_hash` 는 64자 hex | 자동 |
| LX.5 | `curl …/api/status \| jq .schema_version` 을 8811~8817 마다 | M5i 릴리스(v0.2.6)와 같은 값 · 키 추가만 | 자동 |
| LX.6 | `scripts/smoke_install.sh dist/*.whl`(`python -m build` 뒤) | README 절차가 새 venv 에서 그대로 · `rcm --help` 에 `cancel … --cancel-token` | 자동 |
| LX.7 | `docs/m5l-review-supplement-workplan.md` §1 표 · PLAN.md | 항목마다 닫은 커밋/PR 이 적혔거나 §3 순서가 실제와 같다(기록) | 문서(기록) |

## 2. 오너만 할 수 있는 것

| # | 결정 | 이 문서의 어디 | 정해지기 전에는 |
|---|---|---|---|
| ① | **L8** — `hosts[].disk.path` 를 공개 상태에서 뺄지, `read_auth = basic` 일 때만 줄지, 아니면 규칙·명세 I4 를 고칠지 | L8.1~L8.5 | 「오너 대기」 — 실측(지금 `path` 가 나오는지)만 적는다 |
| ② | **S3** — 선언한 절대경로를 잡 로그(토큰 보호)에서도 basename 으로 할지 | S3.1·S3.2 | 「오너 대기」 — 현재 로그 줄을 그대로 적는다 |
| ③ | **#87 P2** — 오프라인 dry-run JSON 의 `offline.database` 절대경로(복원 안내엔 필요 · 상태 규칙엔 어긋남) | L1.10 | 실측만 적는다 — L1 의 다른 행은 이 결정과 무관하다 |
| ④ | **G6** — `rcm logs N --step <name>` 실패 스텝 발췌 채택 여부 | 없음(채택되면 M5j 시나리오에 G6 표가 붙는다) | 이 문서 밖 |
| ⑤ | **#110 의 `"yes"`·`"1"` bool 허용**(로더 공통 규칙) 유지 여부 | S10 의 서버 설정 — 지금은 `true`/`false` 만 쓴다 | M5j G5.23 의 실측(허용됨)을 그대로 두고 오너 표시를 기다린다 |

릴리스(v0.2.7 · M5j + M5l)와 이 Mac 운영 업그레이드(v17 마이그레이션 · `docs/operating.md` 「Upgrade」 · 결정 80 순서)는
검증 항목이 아니라 릴리스 절차다. 명세 §3 의 순서(v0.2.6 → main 이 먼저 · 그 전엔 dev 에 아무것도 머지하지 않는다)는 이
문서보다 **먼저** 지켜져야 한다 — S 항목의 push 는 PR 브랜치에만 간다(dev 머지가 아니다).

## 3. 에이전트 배치 (병렬 · 격리)

| 에이전트 | 영역 | 워크트리 · 브랜치 | 포트 | 보고서 |
|---|---|---|---|---|
| verify-s109 | S1.1~S4.5 · LX.3(8811) · LX.5(8811) | `../remote_ci_monitor-acceptance-m5l-verify-s109` · `docs/acceptance-m5l-verify-s109`(기준 `origin/feat/preset-requires-tools-preflight`) | 8811 | `docs/acceptance/reports/<날짜>-m5l-verify-s109.md` |
| verify-s110 | S5.1~S10.7 · LX.3(8812) · LX.4 · LX.5(8812) | `../remote_ci_monitor-acceptance-m5l-verify-s110` · `docs/acceptance-m5l-verify-s110`(기준 `origin/feat/cancel-submission-capability`) | 8812 (옛 클라 venv 는 S10.3 의 워크트리에서 따로) | `docs/acceptance/reports/<날짜>-m5l-verify-s110.md` |
| verify-s108-106 | S11.1~S11.7 · S12.1~S12.6 · LX.5(8813) | `../remote_ci_monitor-acceptance-m5l-verify-s108-106` · `docs/acceptance-m5l-verify-s108-106`(기준 `origin/feat/jobs-step-timeline-for-finished` — S12 는 `origin/docs/admission-two-lane-experiment` 를 같은 워크트리에서 `git show`/`git worktree add` 로 읽는다, 두 번째 워크트리 `…-verify-s106` 허용) | 8813 | `docs/acceptance/reports/<날짜>-m5l-verify-s108-106.md` |
| verify-l1-l5 | L1.1~L1.11 · L5.1~L5.11 · L8(오너 결정 뒤) · LX.5(8814) | `../remote_ci_monitor-acceptance-m5l-verify-l1-l5` · `docs/acceptance-m5l-verify-l1-l5`(기준 `origin/fix/gc-offline-error-paths` 와 `origin/fix/serve-bind-before-migrate` — 둘이 아직 따로면 워크트리 둘: `…-verify-l1` · `…-verify-l5`) | 8814 · 8815(점유용) | `docs/acceptance/reports/<날짜>-m5l-verify-l1-l5.md` |
| verify-l2 | L2.1~L2.9 · LX.5(8816) | `../remote_ci_monitor-acceptance-m5l-verify-l2` · `docs/acceptance-m5l-verify-l2`(기준 `origin/fix/janitor-unknown-freed-partial-delete`) | 8816 | `docs/acceptance/reports/<날짜>-m5l-verify-l2.md` |
| verify-l3-l6-l7 | L3.1~L3.8 · L6.1~L6.10 · L7.1~L7.9 · LX.5(8817) · LX.6 | `../remote_ci_monitor-acceptance-m5l-verify-l3-l6-l7` · `docs/acceptance-m5l-verify-l3-l6-l7`(기준: 셋이 따로면 워크트리 셋 `…-verify-l3` · `…-verify-l6` · `…-verify-l7`; 머지 뒤면 `origin/dev` 하나) | 8817 (L3 의 가짜 서버는 임시 포트 — 8811~8818 밖 · 8788/8790/8801~8805 도 피한다) | `docs/acceptance/reports/<날짜>-m5l-verify-l3-l6-l7.md` |
| verify-l4 | L4.1~L4.14 · LX.1 · LX.2 · LX.7(마지막 머지 뒤 `origin/dev` 에서) | `../remote_ci_monitor-acceptance-m5l-verify-l4` · `docs/acceptance-m5l-verify-l4`(기준 `origin/fix/guard-service-venv-identity`) | 8818(예약 — 서버 없음) | `docs/acceptance/reports/<날짜>-m5l-verify-l4.md` |

각 에이전트는 자기 영역의 표를 **위에서 아래로** 전부 돌리고, 오류마다 그 항목의 브랜치에 빨간 테스트 + 수정을 push 한 뒤
보고서를 쓴다. 보고서는 한 PR(`docs/m5l-verification-reports`)로 모아 올린다. 서로의 포트·`data_dir`·워크트리·`XDG_STATE_HOME`
을 쓰지 않는다. L4 의 가짜 홈(`rcm-verify-m5l-l4/home`)과 S10 의 옛 클라 venv 는 그 에이전트만 만든다. 순서는 명세 §3 —
S 넷의 push 가 끝나고 CI 초록이면 #107 → #109 → #108 → #106 → #110 순으로 dev 에 머지되고, 그 뒤 L7 → L1 → L5 → L2 → L4
→ L3 → L6 → L8 이다. L 에이전트는 자기 fix 브랜치가 생긴 뒤에 시작한다.
