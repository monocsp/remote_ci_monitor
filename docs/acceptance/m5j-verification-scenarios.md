# M5j 검증 시나리오 (2026-09-10)

> 목적: `docs/gate-optimization-workplan.md`(M5j v0.2) 의 PR 1~4(G4 · G1 · G3 문서 · G5)가 **`dev` 에 들어간 뒤**, 명세가
> 약속한 것이 실제로 되는지를 명세를 근거로 다시 본다. G2 는 M5k 로 갔으므로 여기 없다. 판정은 M5i 와 같다 — 「테스트가
> 있다」가 아니라 **「실제 모양의 입력으로 실제 경로를 지났다」**. 엣지 케이스는 Codex 리뷰
> (`docs/reviews/2026-09-10-codex-gate-optimization-design.md`)가 초안에서 찾아낸 13개 충돌과 규칙 위반 표에서 하나씩
> 가져왔다 — 그 표가 「이 설계가 실제로 깨지는 자리」의 목록이다. 항목마다 근거를 `명세 §…` · `Codex #n` 으로 적었다.
> 보고서는 `docs/acceptance/reports/<날짜>-m5j-verify-<영역>.md`.
>
> 실행 주체는 **격리 에이전트**다(§0). 발견한 오류는 보고만 하지 않고 **테스트 → 수정 → PR** 까지 한다.

## 0. 실행 규칙 (격리 에이전트 공통)

| 규칙 | 내용 |
|---|---|
| 워크트리 | `origin/dev`(해당 PR 머지 뒤)에서 `docs/acceptance-m5j-verify-<영역>` 브랜치의 **자기 워크트리** + 자기 `.venv`(`python3.11 -m venv .venv && ./.venv/bin/python -m pip install -e '.[dev]'`). 다른 워크트리를 건드리지 않고 `git switch` 로 브랜치를 바꾸지 않는다 |
| 운영 | `~/Documents/GitHub/remote_ci_monitor` · `~/.config/rcm` · `~/.local/share/rcm` 은 **읽지도 열지도 않는다**. dev 의 `rcm` 이 DB 를 열면 마이그레이션한다(v17 은 실제 버전 상승이다). `~/.local/state/rcm`(G5 클라이언트 상태 파일의 기본 자리)도 건드리지 않는다 — §1 G5 의 `XDG_STATE_HOME` 규칙 |
| 시험 서버 | 자기 설정 파일 · 포트(영역마다 아래 배정) · `data_dir` 은 `$HOME` 아래(`~/.local/share/rcm-verify-<영역>`), `/tmp` 아래 금지 · `advertise = false` · `[notify]` 없음(알림 훅 없음) · `bind = "127.0.0.1"` · 끝나면 서버를 내리고 `data_dir` 을 지운다 |
| 판정 | 기대와 다르면 **오류**다. 「테스트를 약하게 해서 통과」는 금지. 오류는 `fix/<scope>-<what>` 브랜치에 빨간 테스트 + 수정으로 PR(`gh pr create --base dev`), CI 초록 뒤 REST 머지, 보고서에 PR 번호 |
| 모르면 3 | 확인 절차 자체가 불완전하면(Chrome 없음 · 옛 wheel 못 받음) 그 항목은 「확인 못 함」으로 적는다 — 통과로 적지 않는다 |
| 보고서 | 항목 ID 마다 통과 / 오류(PR #) / 확인 못 함(이유) · 실측값(응답 본문 · 종료 코드 · 로그 줄 · 파일 권한) · 명령 그대로. 비밀(토큰 · capability)은 보고서에 **앞 4자만** |
| 도구 | `/usr/bin/curl` · `/usr/bin/jq` · `/usr/bin/sqlite3` 가 이 Mac 에 있다. JSON 은 `jq` 로 키를 집어 본다 |

포트 배정: **G4 8801** · **G1 8802** · **G5 8803**(키 off) · **8804**(키 on) · **8805**(옛 0.2.6 서버 — 옛 클라 wheel 의 출처이자
v16 DB 의 출처) · G3 은 문서라 서버 없음.

공통 준비(영역마다 반복): 설정 파일 `~/.local/share/rcm-verify-<영역>.toml` 을 아래 꼴로 만들고
`rcm serve --config <그 파일>` 을 별도 셸(또는 `nohup … > ~/.local/share/rcm-verify-<영역>/server.log 2>&1 &`)로 띄운다. 토큰은
`rcm token --config <그 파일> add <이름> [--admin|--worker]` — **`--config` 는 하위 명령 앞**. 아래 표의 `$A`(alice · 일반) ·
`$B`(bob · 일반) · `$R`(root · admin) · `$W`(wk · worker) 는 그렇게 만든 토큰이다. 제출은 파일 하나짜리 디렉터리로
`rcm run <preset> --server http://127.0.0.1:<port> --token $A --dir <그 디렉터리> --poll` — `--dir` 이 없으면 현재 디렉터리
(워크트리 전체)를 올린다. `--no-wait` 의 stdout 은 JSON 한 줄이고, 기다리는 `run`·`wait` 의 마지막 stdout 줄도 JSON 이다
(`wait` 에 `--json` 플래그는 없다 — 항상 JSON 이다).

```toml
[server]
bind = "127.0.0.1"
port = 880N
data_dir = "~/.local/share/rcm-verify-<영역>"   # ~ 는 서버가 푼다(M5i V1.1)
advertise = false
lanes = 1
```

## 1. 영역별 시나리오

### G4 — 프리셋 `requires` (PR 1 · 명세 §2 G4 · 결정 85)

준비: 8801 설정에 프리셋 여섯. 스크립트는 **파수꾼 파일**을 만든다 — 프로세스가 떴는지의 증거는 로그가 아니라 이 파일이다.
`$RCM_WORKSPACE` 는 `<data_dir>/workspaces/<id>` 이므로 `../..` 이 `data_dir` 이다. `<HOME>` 은 절대경로로 바꿔 쓴다
(TOML 은 `~` 를 안 푼다).

```toml
[[presets]]
name = "needs-missing"
argv = ["/bin/sh", "-c", "touch \"$RCM_WORKSPACE/../../sentinel-$RCM_JOB_ID\"; echo ::rcm::step::build; exit 0"]
requires = ["rcm-verify-no-such-tool-9f3a"]

[[presets]]
name = "needs-git"                 # 같은 argv
requires = ["git"]

[[presets]]
name = "needs-abs"                 # 같은 argv
requires = ["/usr/bin/true"]

[[presets]]
name = "needs-preset-path"         # 같은 argv · 서버 PATH 에는 없는 도구를 프리셋 env.PATH 로만 찾는다
requires = ["fakectl"]
env_passthrough = ["HOME", "LANG"]
[presets.env]
PATH = "<HOME>/.local/share/rcm-verify-g4/bin:/usr/bin:/bin"

[[presets]]
name = "needs-no-path"             # 같은 argv · PATH 가 환경에 아예 없다
requires = ["git"]
env_passthrough = []

[[presets]]
name = "needs-missing-remote"      # 같은 argv · 원격 풀
pool = "linux"
requires = ["rcm-verify-no-such-tool-9f3a"]

[[presets]]
name = "ok-remote"                 # 같은 argv · 원격 풀
pool = "linux"
requires = ["git"]
```

`mkdir -p ~/.local/share/rcm-verify-g4/bin && printf '#!/bin/sh\nexit 0\n' > ~/.local/share/rcm-verify-g4/bin/fakectl && chmod +x …/fakectl`.
서버를 띄우는 셸의 PATH 에 그 `bin` 이 **없는지** 먼저 확인한다(`echo $PATH`). 원격 항목(G4.13~G4.17)은 워커 토큰 `$W` 와
워커 데이터 디렉터리 `~/.local/share/rcm-verify-g4-worker` 를 쓴다.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| G4.1 | 8801 · `needs-missing` | `rcm run needs-missing --dir … --poll` → 종료 뒤 `ls ~/.local/share/rcm-verify-g4/sentinel-<id>` | exit **1** · 마지막 JSON `state: "failed"` · `summary_code: "tool_missing"` · `summary_args == {"tool": "rcm-verify-no-such-tool-9f3a"}` **정확히 이 키 하나** · `exit_code: null` · **파수꾼 파일 없음**(프로세스가 뜨지 않았다) · `rcm wait` 종료 코드도 1(모른다가 아니다) | 실기 |
| G4.2 | G4.1 의 잡 | `GET /jobs/<id>` · `GET /api/status`(`recent[]` 의 그 행) · `rcm jobs --json` · `rcm wait --job <id>` 의 JSON · `GET /jobs/<id>/log`(토큰) · 서버 로그 | 다섯 출력 **어디에도** 서버 PATH 의 조각(`/opt/homebrew`·`/usr/local`·`.venv` 등 서버 셸 `echo $PATH` 의 각 항목)과 절대경로가 없다 — `jq -c . \| grep -c '/'` 로 세고, 나오는 `/` 는 `url`·`upload`·`::rcm::` 뿐임을 눈으로 확인 · 잡 로그의 관련 줄은 `[rcm] required tool rcm-verify-no-such-tool-9f3a: missing` 꼴(이름 + 판정만) | 실기 (Codex 규칙표 「비밀·경로 노출 금지」 · PLAN 「보안」) |
| G4.3 | G4.1 의 잡 | `GET /jobs/<id>` · `sqlite3 <data_dir>/rcm.sqlite3 "SELECT count(*) FROM job_failures WHERE job_id=<id>"` | `failed_step: null` · `last_step: null` · 대장 행 **0** · `failures` 키가 있으면 `[]`(M5h 대장 불변식 — `tool_missing` 은 서버 outcome 이지 스크립트 선언이 아니다) | 실기 (Codex 규칙표 「M5h 대장 불변식」) |
| G4.4 | G4.1 뒤 `needs-missing` 에서 `requires` 줄만 지우고 argv 를 `echo ::rcm::step::build; echo ::rcm::fail::build; exit 1` 로 바꿔 재기동(프리셋 이름이 같으니 key 가 같다) · 같은 트리로 세 번 실행 | 세 번째 잡의 `GET /jobs/<id>.failures[0]` | `window` 와 `window_unnamed` 가 **`tool_missing` 잡을 세지 않는다**(같은 key 의 창에서 빠진다 — 서버가 못 띄운 잡은 스크립트 실패의 표본이 아니다). 실측: 창에 이름 실패 셋만 → `window == 3` · `window_unnamed == 0` · `seen == 3`(`window == 4` 또는 `window_unnamed == 1` 이면 오류) | 실기 + 자동 (명세 §2 G4 「대장 행 없음」의 귀결 · 결정 68 의 분모 — `failure_stats` 는 지금 `succeeded/failed/timed_out` 을 다 센다) |
| G4.5 | `needs-git` · `needs-abs` | 둘 다 `rcm run … --poll` | 둘 다 `succeeded` · 파수꾼 파일 **있음** · 잡 로그에 `[rcm] required tools: git ok` 꼴 한 줄(경로 없음) | 실기 |
| G4.6 | `needs-preset-path` · 서버 셸 PATH 에 `bin` 없음 | `rcm run needs-preset-path … --poll` | `succeeded`(프리셋 `env.PATH` 로 바꾼 최종 환경에서 찾았다) · 같은 프리셋에서 `env.PATH` 줄을 지우고 재기동하면 `tool_missing` `{"tool": "fakectl"}` | 실기 (Codex G4 「정확한 최종 환경」 · `runner.build_env`) |
| G4.7 | `needs-no-path`(`env_passthrough = []`) · 서버 셸에서 `which git` 이 나온다 | `rcm run needs-no-path … --poll` | **`failed` · `tool_missing` · `{"tool": "git"}`** — 환경에 PATH 가 없으면 빈 PATH 로 검사하고 검사 프로세스의 PATH 로 물러나지 않는다. 파수꾼 없음 | 실기 (Codex G4 「빈 PATH 명시」 — `shutil.which` 의 기본이 `os.environ["PATH"]` 다) |
| G4.8 | 설정 셋: `requires = ["bin/fvm"]` · `requires = [""]` · `requires = ["git", "git"]` | 각각 `rcm serve --config <그 파일>` | 셋 다 **exit 2** · stderr `rcm: config: …` 에 프리셋 이름과 `requires`, 이유(relative / empty / duplicate) · 서버가 뜨지 않는다(`curl` 연결 거부). 덤: `requires = "git"`(문자열) 도 exit 2 | 실기 |
| G4.9 | `requires` 없는 프리셋(`ok`) | `rcm run ok … --poll` · `GET /jobs/<id>` | 이전과 같다 — `succeeded` · 로그에 `required tool` 줄 없음 · `summary_code: null` | 실기 |
| G4.10 | 8801 · `rcm check --config ~/.local/share/rcm-verify-g4.toml --server http://127.0.0.1:8801 --token $A` | 행 목록 | **`local preset tools`** 행이 있고, 행 텍스트가 셸 환경 검사임을 말한다(`this shell`·`local` 같은 말) · `needs-missing` 의 도구가 missing 으로 나오되 **행은 warn 이지 FAIL 이 아니거나**, FAIL 이면 문서가 그렇게 말한다(보고서에 적는다) · 행에 PATH 전체가 없다 · `rcm check` 종료 코드를 적는다 | 실기 (Codex 규칙표 「fail-open」 — 셸 검사가 서비스 환경을 대변하지 않는다) |
| G4.11 | 같은 셸 | `rcm check --server http://127.0.0.1:8801 --token $A`(`--config` 없음) | `local preset tools` 행 **없음**(`local data dir`·`git` 행도 없다 — 로컬 설정이 없으니) | 실기 |
| G4.12 | — | `docs/configuration.md` 의 `requires` 절 | 「정본은 잡 시작 전 검사」 · 「`rcm check --config` 의 행은 그 셸의 환경이고 launchd 서비스의 환경이 아니다」 · 이름 또는 절대경로만 · `env.PATH` 우회 예 · `tool_missing` 의 뜻 — 넷 다 있음 · `examples/server.toml` 에 `requires` 예(주석이어도) | 문서 |
| G4.13 | 8801 · `rcm token --config … add wk --worker` | `curl -s -X POST -H "Authorization: Bearer $W" -H 'Content-Type: application/json' -d '{"pool":"linux","lanes":1}' …/worker/register` → `rcm run needs-missing-remote --no-wait` → `curl … -d '{"lane":1,"wait_seconds":0}' …/worker/claim` | claim 응답 `preset.requires == ["rcm-verify-no-such-tool-9f3a"]` · `preset` 의 옛 키(`name argv timeout_seconds env env_passthrough source_modes repo`) 전부 그대로 · `ok-remote` 잡의 claim 은 `["git"]` | 실기 + 자동(`RemoteWorkers._claim_payload`) (Codex #10) |
| G4.14 | G4.13 에서 curl 로 claim 한 잡 | `curl … -d '{"outcome":"failed","exit_code":null,"summary_code":"tool_missing","summary_args":{"tool":"fvm"}}' …/worker/jobs/<id>/finish` → `GET /jobs/<id>` | 200 · `state: failed` · `summary_code: "tool_missing"` · `summary_args == {"tool":"fvm"}` · `summary` 가 서버의 문장(코드에서 그린 것 · 문자열 `summary` 우회가 아님) · `failed_step`·`last_step` null · 대장 0 | 실기 (Codex #10 「구조화된 키」) |
| G4.15 | 새 잡을 curl 로 claim | finish 본문 ① `summary_args: {"tool":"fvm","path":"/opt/x/fvm"}` ② `summary_code: "rm_rf_everything"`(모르는 코드) ③ `summary_code: "tool_missing"` 에 `summary_args` 없음 | ① **400** 이거나 저장된 args 가 `{"tool":"fvm"}` 뿐(`path` 가 어디에도 안 남는다 — `GET /jobs/<id>`·`/api/status.recent`·서버 로그) ② **400**(모르는 코드를 통과시키지 않는다 — fail-closed) ③ 400 이거나 문서가 정한 기본값 — 셋 다 실측을 적는다 | 실기 (Codex 규칙표 「경로 노출」·「fail-open」) |
| G4.16 | 새 워커(자기 `.venv`) · `RCM_WORKER_TOKEN=$W` | `rcm worker --server http://127.0.0.1:8801 --pool linux --lanes 1 --data ~/.local/share/rcm-verify-g4-worker --once` 를 `needs-missing-remote` 잡이 대기 중일 때 | 워커가 프리플라이트에서 잡을 **`failed` · `tool_missing` · `{"tool": …}`** 로 보고하고 프로세스를 띄우지 않는다(워커 데이터 디렉터리에 파수꾼 없음) · 서버 쪽 문서가 G4.1 과 같은 모양 · 워커 stdout/stderr 에 PATH 없음 | 실기 |
| G4.17 | **옛 워커**: 0.2.6 wheel 로 만든 venv(§1 G5 준비의 8805 가 주는 `/client/…0.2.6…whl`, 또는 `gh release download v0.2.6 -p '*.whl'`) | 옛 `rcm worker … --once` 를 `ok-remote` 잡, 그다음 `needs-missing-remote` 잡에 | 둘 다 워커가 claim 을 받아 **그대로 실행**(`requires` 를 모른다 → 무시) · `ok-remote` `succeeded` · `needs-missing-remote` 도 스크립트대로 `succeeded`(파수꾼 있음) · 서버가 옛 finish(문자열 `summary` 만)를 예전처럼 받는다 — 키 추가만이라 옛 워커는 깨지지 않는다는 것이 계약이고, 「옛 워커는 검사하지 않는다」를 보고서에 명시 | 실기 (명세 §2 G4 「옛 워커는 키를 무시한다」) |
| G4.18 | — | `pytest tests/test_requires.py -q` · `python scripts/mutcheck.py --only <requires 검사 건너뛰기 변이 이름>` | 전부 초록 · 변이 caught(명세 §5 ①) | 자동 |
| G4.19 | 8801 잡 여럿 뒤 | `GET /api/status` | `schema_version` 이 이전과 같다(값을 적는다) · 옛 키 전부 존재 · `presets[]` 항목에 `requires` 가 실린다면 이름만이지 경로가 아니다 | 자동 |

### G1 — 종료 잡의 `step_timeline` (PR 2 · 명세 §2 G1 · 결정 84)

준비: 8802 설정에 프리셋 여섯. `sleep` 으로 스텝 길이를 서로 다르게 둔다 — 「활성 때 값 == 종료 뒤 값」을 초 단위로 비교하려면
스텝이 1초 이상이어야 한다.

```toml
[[presets]]
name = "steps3"
argv = ["/bin/sh", "-c", "echo ::rcm::steps::3; echo ::rcm::step::a; sleep 2; echo ::rcm::step::b; sleep 1; echo ::rcm::step::c; sleep 1; exit 0"]

[[presets]]
name = "nomarkers"
argv = ["/bin/sh", "-c", "echo hello; sleep 1; exit 0"]

[[presets]]
name = "midfail"        # 실패 이름이 가운데 스텝이고 마지막 스텝이 아니다
argv = ["/bin/sh", "-c", "echo ::rcm::step::lint; sleep 1; echo ::rcm::step::test; sleep 1; echo ::rcm::step::package; sleep 1; echo ::rcm::fail::lint; echo ::rcm::fail::test; exit 1"]

[[presets]]
name = "sleepy"         # 취소용 — 스텝 하나 열어 두고 오래 잔다
argv = ["/bin/sh", "-c", "echo ::rcm::step::a; sleep 120"]

[[presets]]
name = "slow"           # 타임아웃용
argv = ["/bin/sh", "-c", "echo ::rcm::step::a; sleep 120"]
timeout_seconds = 5

[[presets]]
name = "many"           # 스텝 300 · 서로 다른 실패 이름 150
argv = ["/bin/sh", "-c", "i=1; while [ $i -le 300 ]; do echo ::rcm::step::s$i; i=$((i+1)); done; i=1; while [ $i -le 150 ]; do echo ::rcm::fail::f$i; i=$((i+1)); done; exit 1"]
```

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| G1.1 | 8802 · `steps3` | `rcm run steps3 --no-wait` 뒤 0.3초마다 `curl -s …/api/status \| jq -c '.pools[0].queue[] \| select(.id==<id>) \| .progress'` 를 종료까지 모아 **마지막 표본**을 남긴다 → 종료 뒤 `GET /jobs/<id>` | `step_timeline` 객체 하나: `timing == "as_received"` · `steps_total == 3` · `steps_total_partial == false` · `steps` 셋(`index name started_at ended_at seconds ok` 키) · 활성 표본에서 이미 닫혀 있던 `a`·`b` 의 `started_at`·`ended_at`·`seconds` 가 **문자열·숫자 그대로 같다** · `c` 의 `ended_at == finished_at` · 셋 다 `ok: true`(exit 0) · 활성 표본의 `progress` 에는 있던 `phase current_* steps_done job_seconds failed_step last_step` 는 `step_timeline` 에 **없어도** 된다(객체가 다르다 — 있으면 적는다) | 실기 (명세 §2 G1 「활성 때와 같다」) |
| G1.2 | `nomarkers` | `rcm run nomarkers … --poll` → `GET /jobs/<id>` | `step_timeline == {"timing":"as_received","steps_total":null,"steps_total_partial":true,"steps":[]}` — **`null` 이 아니고 `steps_total` 은 0 이 아니다** · `step_timeline_error_code` 키 없음(또는 null) | 실기 (Codex #1 — 「없음」과 「못 읽음」은 다르다) |
| G1.3 | `rcm pause --token $R` 로 큐를 멈춘 뒤 `rcm run sleepy --no-wait` → `rcm cancel <id>` → `rcm resume` | `GET /jobs/<id>` | `state: cancelled` · `started_at: null` · `step_timeline` 이 **빈 타임라인**(G1.2 와 같은 객체) — null 이 아니다 · `failed_step`·`last_step` null | 실기 (명세 §2 G1 「시작 전 실패 → 빈 타임라인」) |
| G1.4 | `rcm run ok --no-wait` 의 `upload` 경로에 `curl -X PUT -H "Authorization: Bearer $A" -H 'Content-Type: application/gzip' --data-binary 'garbage'` | 응답과 `GET /jobs/<id>` | 업로드 거절(4xx) 뒤 잡이 `failed`(PLAN 「큐에서 조용히 사라지는 잡은 없다」) · `started_at: null` · `step_timeline` 빈 타임라인(null 아님) | 실기 |
| G1.5 | `tests/test_step_timeline.py`: `Store.markers` 를 `sqlite3.OperationalError("disk I/O error")` 로 monkeypatch | 종료 잡 `GET /jobs/<id>` | **200** · `step_timeline: null` · `step_timeline_error_code` 가 비어 있지 않은 문자열이고 경로·SQL·예외 문장이 아니다(`OperationalError` 같은 형 이름은 허용) · 나머지 키(`state summary failed_step last_step failures artifacts`)는 그대로 · `rcm wait` 종료 코드는 상태대로(0/1/2)이지 3 이 아니다 — 타임라인은 부가 정보다 | 자동 (Codex 규칙표 「fail-open」 — `[]` 로 뭉개면 위반 · 명세 §5 mutcheck ③) |
| G1.6 | `midfail` 두 번(창을 만든다) | 두 번째 잡의 `GET /jobs/<id>` 를 `/api/status.recent` 의 같은 행과 나란히 | `failed_step == "lint"` · `last_step == "package"` — recent 행과 **같다**(타임라인이 저장값을 바꾸지 않는다) · `step_timeline.steps`: `lint ok:false` · `test ok:false` · `package ok:null`(exit≠0 인 마지막 열린 스텝은 모른다) · 문서에 `failed_step_guessed` 없음 · 최상위에 타임라인이 재구성한 다른 `failed_step` 없음 | 실기 (Codex #2 · #4 · M5h 결정 63) |
| G1.7 | G1.6 의 잡 | `GET /jobs/<id>.failures[]` | `test` 항목의 `step == true`(`{failed_step, last_step}` = `{lint, package}` 만 봤다면 false 였다 — 타임라인의 **모든 스텝 이름**으로 판정) · `lint` 도 `step: true` · 순서는 찍은 순서(`lint` 먼저) | 실기 (명세 §2 G1 `_with_failures` · Codex 권고 G1) |
| G1.8 | 옛 라벨 픽스처: 8802 를 내리고 `sqlite3 rcm.sqlite3 "UPDATE jobs SET failed_step='build web', last_step=NULL WHERE id=<midfail 잡>; DELETE FROM job_failures WHERE job_id=<그 잡>; PRAGMA user_version=15;"` | 재기동 → `GET /jobs/<id>` | 기동 전 `backup/rcm.sqlite3.v15.bak` 생성 · `user_version` 이 현재 `DB_VERSION`(G5 뒤면 17) · `failed_step: null` · `last_step: "build web"`(v16 이 옮긴 그대로) · `step_timeline.steps` 는 마커대로 `lint test package`(이벤트의 `fail` 마커가 남아 있으니 `lint`·`test` 는 `ok: false` · `package` 는 null) — 타임라인 자체는 「lint 가 깨졌다」고 계산하지만 최상위 `failed_step` 은 **null 그대로**(재구성 라벨을 되살리지 않는다) · `failures` 는 대장이 비었으니 `[]` | 실기 (Codex #4 · M5i 결정 78) |
| G1.9 | `steps3` | `rcm run steps3 … --poll` 의 마지막 stdout JSON · `rcm wait --job <id>` 의 마지막 stdout JSON · `GET /jobs/<id>` | 셋의 `step_timeline` 이 **같다**(`jq -S .step_timeline` 으로 비교) · `wait_exit_code: 0` · `docs/configuration.md`(또는 `usage.md`)가 `rcm wait` JSON 의 `step_timeline` 을 계약으로 적는다 | 실기 + 문서 (Codex #3 — wait 는 GET 문서를 그대로 복사한다) |
| G1.10 | `sleepy` 실행 중 | `GET /api/status` · `GET /jobs/<id>` | 실행 중 행에는 **`progress`** 가 그대로(`steps[0].state == "running"`) · 실행 중 `GET /jobs/<id>` 에 `step_timeline` 이 없거나 null(있으면 적는다 — 계약은 종료 잡) · `schema_version` 이 PR 전과 같다 | 실기 |
| G1.11 | `sleepy` 실행 중 3초 뒤 `rcm cancel <id> --token $A` | 종료 뒤 `GET /jobs/<id>` | `state: cancelled` · `step_timeline.steps[-1].ended_at == finished_at` · **`ok: null`**(False 로 추론하지 않는다 — 강제 종료에 exit 를 넣으면 물든다, #176) · `failed_step`·`last_step` **둘 다 null**(결정 64) · `seconds` ≈ 3(±1) | 실기 (Codex #2 · `outcome_for` 의 `exit_code=None if forced`) |
| G1.12 | `slow`(timeout 5) | `rcm run slow … --poll` → `GET /jobs/<id>` | `state: timed_out` · exit 2 · 마지막 스텝 `ended_at == finished_at` · `ok: null` · `last_step: "a"` · `failed_step: null`(선언 없음) | 실기 |
| G1.13 | `many` | `rcm run many … --poll` → `GET /jobs/<id>` · `sqlite3 … "SELECT count(*) FROM events WHERE job_id=<id> AND kind='marker'"` | 이벤트 450 · `step_timeline.steps` 길이 **300** · `steps_total == 300`(`partial: true`) · 상한을 두는 구현이면 잘린 사실을 키로 밝히고 그 키가 문서에 있다(`[]` 나 조용한 절단은 오류) · `failures_truncated: true`(150 > 100) · `failures[]` 100개 · 응답 시간 < 2초 · `rcm wait` 가 그 JSON 을 한 줄로 찍는다 | 실기 (PLAN 마커 표 「잡당 100개」 — 스텝 자체엔 상한이 없다) |
| G1.14 | — | `pytest tests/test_step_timeline.py -q` · `python scripts/mutcheck.py --only <DB 오류를 [] 로 뭉개는 변이 이름>` · `pytest tests/test_progress.py -q` | 전부 초록 · caught · 기존 progress 픽스처 불변 | 자동 |
| G1.15 | — | `PLAN.md` 「진행 — 스텝 마커 프로토콜」·「/api/status 스키마 v1」 | 종료 잡 `step_timeline` 의 모양(객체 · 없으면 `[]` · 못 읽으면 `null` + 코드)이 적혀 있고 결정 84 가 표에 | 문서 |

### G3 — 두 레인 실험 · 메모리 admission 기각 (PR 3 · 문서만 · 명세 §2 G3 · 결정 86)

서버 없음. 전부 `grep` 과 눈이다.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| G3.1 | dev 체크아웃 | `docs/configuration.md` 「Parallel lanes without overloading the machine」(Admission 절) | 실험 배치가 적혀 있다: `lanes = 2` + `admission = "load"` + 게이트 프리셋의 `concurrency_group` 제거는 **게이트 스크립트가 heavy 구간을 OS 락(`flock` 예)으로 직렬화하고 있을 때만** · 그 락이 없으면 `concurrency_group` 유지 | 문서 (Codex #9) |
| G3.2 | 같은 절 | `grep -n -i "not a .*lock\|구간 락\|section lock" docs/configuration.md` | 「CPU admission 은 잡 **시작** 제어이지 heavy 구간의 상호배제(락)가 아니다」가 명시돼 있다 — claim 직전 한 번 판단한다는 사실 포함 | 문서 (Codex 규칙표 「fail-open」 — 보장하는 것처럼 쓰면 위반) |
| G3.3 | 같은 절 | `grep -n concurrent_at_start docs/configuration.md` | 효과 측정이 G1 의 스텝 시간과 **`concurrent_at_start`** 로 한다고 적혀 있고, 그 값이 어디서 읽히는지(`GET /jobs/<id>` 또는 `rcm jobs --json` 의 키)가 코드와 맞다(`grep -rn concurrent_at_start src/remote_ci_monitor/core/status.py` 로 확인) | 문서 + 자동 |
| G3.4 | — | `grep -n -i "mem" src/remote_ci_monitor/config.py examples/server.toml` · `grep -n -i "memory" docs/configuration.md` | 설정에 **메모리 admission 키가 없다**(`config.py` 에 `mem`/`memory` 로 시작하는 `[server]` 키 0 · 예시 파일 0) · 문서의 `memory` 언급은 호스트 카드/샘플러 설명뿐이고 게이트가 아니다 · `PLAN.md` 결정 42 문구 불변 | 자동 (Codex #8 · PLAN 결정 42) |
| G3.5 | — | `docs/configuration.md` 에 heavy 구간 **선언**(마커) 기능이 없다: `grep -n "exclusive::" docs/ src/` | 0건(보류 — lease 없이는 안 한다) · PLAN 결정 86 이 표에 | 자동 + 문서 (Codex #9 · 권고 G3) |
| G3.6 | — | `pytest tests/test_docs_m5j.py -q` | 위 문구들을 잠근 테스트가 있고 초록 · 문구 하나를 지우면 빨강(임시로 지워 보고 되돌린다) | 자동 |

### G5 — 제출 참여자별 capability (PR 4 · 명세 §2 G5 · 결정 87 · v17)

준비: 서버 셋.
- **8803** `cancel_requires_submission_token = false`(명시) · **8804** `= true` · 둘 다 프리셋 `ok`(`exit 0`) 와 `sleepy`
  (`echo ::rcm::step::a; sleep 120`) · 토큰 `alice`($A) · `bob`($B) 일반 · `root`($R) admin.
- **8805** 옛 서버 0.2.6: `gh release download v0.2.6 -p '*.whl' -D ~/.local/share/rcm-verify-g5-old/` 가 되면 그 wheel 로,
  릴리스가 아직 없으면 `git worktree add ../remote_ci_monitor-verify-old 73584b5`(dev 의 `release: 버전 0.2.6`) 의 자기 venv 로
  8805 를 띄우고 `/client/remote_ci_monitor-0.2.6-py3-none-any.whl` 을 받는다. 그 wheel 로 **옛 클라 venv**(`python3.11 -m venv
  ~/.local/share/rcm-verify-g5-oldvenv`)를 만든다. 8805 의 `data_dir`(`~/.local/share/rcm-verify-g5-old`)에서 잡 하나를 돌려 두면
  그것이 **v16 DB 픽스처**다(G5.17·G5.18).
- 클라이언트 상태 파일: 새 클라이언트는 capability 를 로컬 상태 파일에 둔다(명세: `~/.local/state/rcm/submissions.json` 같은).
  검증은 **`XDG_STATE_HOME=$HOME/.local/share/rcm-verify-g5/state`** 를 export 한 셸에서 돌린다. 구현이 이 변수를 무시하고
  `~/.local/state/rcm` 에 쓰면 그 자체가 **오류**다(검증·CI 가 사용자의 실제 상태 파일을 건드리게 된다) — 실제 경로를
  보고서에 적는다. curl 로 capability 를 보내는 자리(본문 키 · 헤더)는 구현이 정하되 `docs/configuration.md` 에 적혀 있어야
  한다 — 아래 `<cap 자리>` 는 그 문서의 것을 쓴다. 문서에 없으면 그 항목은 오류다.

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| G5.1 | 8803(off) · alice | `rcm run sleepy --no-wait --token $A` 의 stdout JSON · 같은 것을 curl `POST /jobs` 로 | `submission.id`(문자열 또는 정수 · 잡마다 다름) · `submission.cancel_token`(32자 이상 · 잡마다 다름) 가 **201 응답에** 있다 · 옛 키(`job_id joined state priority pool cache upload url`) 전부 그대로 · **stderr 에 cancel_token 없음** · `--no-wait` JSON 에 실리는지는 적는다(래퍼 호환) | 실기 (명세 §2 G5 「응답에 `submission: {id, cancel_token}`」) |
| G5.2 | G5.1 의 잡이 실행 중 · bob 이 **같은 트리**로 | `rcm run sleepy --no-wait --token $B` | 200 `joined: true` · `submission.id` 가 alice 것과 다르고 `cancel_token` 도 다르다 · `GET /jobs/<id>.joiners` 에 bob | 실기 (Codex #12 — 참여자마다 다른 비밀) |
| G5.3 | 같은 잡 · **같은 토큰 이름** alice 로 두 번째 셸에서 | `rcm run sleepy --no-wait --token $A`(합류) → `sqlite3 … "SELECT submission_id, role FROM submissions WHERE job_id=<id> ORDER BY created_at"` | 합류 응답에 **세 번째** `submission`(id·비밀 모두 다름) · 표에 행 셋: alice `cancel_job`(요청자) · bob `leave_submission` · alice `leave_submission` — 같은 이름의 세션이 합쳐지지 않는다(`joiners` PK 와 별개) | 실기 (Codex #12 `joiners` PK `(job_id, name)`) |
| G5.4 | 8803(off) · G5.3 의 잡 | ① bob 이 capability 없이 `curl -X POST -H "Authorization: Bearer $B" …/jobs/<id>/cancel` ② alice(요청자) 가 capability 없이 옛 방식 `rcm cancel <id> --token $A`(옛 0.2.6 venv 의 `rcm`) | ① **200** `left: true` · 잡 계속 · ② **200** `state: cancelling` → `cancelled` — 키 off 는 0.2.x 호환이다 | 실기 (명세 §2 G5 「기본 off · 호환」) |
| G5.5 | 8803(off) | `GET /api/health` · 새 클라 `rcm check` | `min_client_version == "0.2.0"`(그대로) · `rcm check` 에 버전 경고 없음 · 옛 클라 `rcm check` 전부 ok | 실기 |
| G5.6 | 8804(on) · alice 가 `rcm run sleepy --no-wait --token $A` | 요청자 토큰으로 capability **없이** `curl -X POST -H "Authorization: Bearer $A" …/jobs/<id>/cancel` | **403** · 본문 `error` 에 이유(capability/`--cancel-token` 을 말한다 · 해시·경로 없음) · 잡 계속 `running` · 서버 로그에 비밀 없음 | 실기 (명세 §2 G5 「`true` 면 capability 또는 admin 만」) |
| G5.7 | 같은 잡 | `rcm cancel <id> --token $A --cancel-token <G5.6 제출의 cancel_token>` · 또는 curl 에 `<cap 자리>` | **200** `state: cancelling` → `cancelled` · `cancelled_by` 는 토큰 이름(alice) · `rcm cancel` 의 stdout JSON·stderr 에 비밀 없음 | 실기 |
| G5.8 | 8804 · alice 요청 + bob 합류(G5.2 처럼) | bob 의 `leave_submission` capability 로 `rcm cancel <id> --token $B --cancel-token <bob 것>` | **200** `left: true` · `GET /jobs/<id>.state == "running"` · joiners 에서 bob 만 빠지고 alice 의 제출 행은 남는다 · 합류자의 비밀로는 잡을 **취소할 수 없다**(응답이 `left` 지 `cancelling` 이 아니다) | 실기 (Codex 권고 G5 「합류 capability 는 `leave_submission` 만」) |
| G5.9 | 같은 잡 | ① 한 글자 바꾼 capability ② **다른 잡**의 유효한 capability ③ 이미 떠난 bob 의 capability 재사용 | ① **403** ② **403**(잡에 묶인다) ③ **200 이 아니다**(403 또는 409 — 떠난 참여의 비밀은 죽는다). 셋 다 잡 `running` 유지 · joiners 불변 · 응답에 어느 것이 틀렸는지 힌트(해시 조각 등) 없음 | 실기 |
| G5.10 | 같은 잡 · admin | `rcm cancel <id> --token $R`(capability 없음) | **200** `cancelling` — admin 은 키와 무관 | 실기 |
| G5.11 | 8804 · 새 잡 · 비-admin alice | `rcm cancel <id> --token $A --force`(플래그가 있으면) · curl 본문 `{"force": true}` | **403**(우회 없음) · `--force` 플래그가 아예 없으면 argparse 오류 exit 2 — 어느 쪽이든 200 이 아니다 · 문서가 「비-admin `--force` 없음」을 말한다 | 실기 + 문서 (Codex 규칙표 「fail-open」 — 공유 토큰 권한을 되살리면 경계가 열린다) |
| G5.12 | 8804 · `XDG_STATE_HOME` export 한 셸 · alice | `rcm run sleepy --no-wait --token $A` → `ls -l $XDG_STATE_HOME/rcm/` → `rcm cancel <id> --token $A`(`--cancel-token` 없이) | 상태 파일이 `$XDG_STATE_HOME` 아래에 생기고 **권한 0600**(`stat -f %Lp` → `600`) · 디렉터리 0700 · `rcm cancel` 이 파일의 비밀을 써서 **200** · 파일에는 잡 ID·submission id·비밀·서버 URL 정도만(토큰은 없다) | 실기 (명세 §2 G5 「0600 로컬 상태」) |
| G5.13 | G5.12 뒤 새 잡 · 상태 파일 삭제 | `rm $XDG_STATE_HOME/rcm/<파일>` → `rcm cancel <id> --token $A` | 종료 코드 ≠ 0 · stderr 한 줄이 **`--cancel-token`** 을 말한다(서버 403 을 그대로 옮겼든 클라가 먼저 막았든) · 잡 계속 | 실기 |
| G5.14 | 8804 · alice `rcm run sleepy --dir … --poll --token $A`(기다리는 중) | 그 프로세스에 `kill -INT <pid>` | stderr `detached from job #<id> — it keeps running` · exit **3** · 마지막 JSON `detached: true` · `GET /jobs/<id>.state == "running"` · 서버 로그에 cancel 요청 없음 · 상태 파일의 항목은 **남아 있다**(뒤에 `rcm cancel` 이 쓴다) | 실기 (Codex #11 · PLAN Ctrl-C detach 계약) |
| G5.15 | 같은 잡에 bob 이 `rcm run sleepy --dir <같은 트리> --poll --token $B`(합류해 기다리는 중) | bob 프로세스에 `kill -INT` | stderr `… (left the join list)` · exit 3 · `left: true` · 잡 `running` · joiners 에서 bob 만 빠짐 · alice 의 제출 행 그대로 | 실기 |
| G5.16 | 8804 · G5.1 처럼 잡 하나(비밀 `$CAP` 를 셸 변수에) | `strings <data_dir>/rcm.sqlite3 <data_dir>/rcm.sqlite3-wal 2>/dev/null \| grep -c "$CAP"` · `sqlite3 … "SELECT capability_hash FROM submissions"` · `grep -c "$CAP" server.log` · `curl -s …/api/status \| grep -c "$CAP"` · `GET /jobs/<id>` · `rcm jobs --json` · 웹 `GET /` · 서버 로그의 URL | 평문 **0건 everywhere** · `capability_hash` 는 64자 hex(SHA-256) · `GET /jobs/<id>` 에 `submission`·`capability` 계열 키가 있어도 비밀·해시는 없다 · URL 에 비밀이 실리지 않는다(`?cancel_token=` 같은 것 0건) | 실기 (Codex 규칙표 「비밀 노출」 — 취소 권한을 가진 비밀) |
| G5.17 | 8804(on) | `GET /api/health` · 새 클라 `rcm check --server http://127.0.0.1:8804 --token $A` | `min_client_version` 이 **G5 를 실은 빌드의 `__version__`**(예정 `0.2.7` — `0.2.0` 이 아니다) · 8803(off) 은 그대로 `0.2.0` · `rcm check` 가 `client` 행에서 같은 버전을 말한다(같은 빌드면 `same as server`) · 문서가 「키를 켜면 옛 클라의 취소가 403」을 말한다 | 실기 + 문서 (Codex 규칙표 「옛 클라이언트」 · M5i 결정 83) |
| G5.18 | 옛 클라 venv(0.2.6) → 8804 | `rcm check` · `rcm run ok --dir … --poll` · `rcm run sleepy --no-wait` → `rcm cancel <id>` | `rcm check` 의 `client` 행 **FAIL**(`v0.2.6 · server v0.2.7 · older — pip install http://127.0.0.1:8804/client/…whl`) exit 1 · `run ok` 는 **성공**(제출·대기는 그대로 된다 — 옛 클라는 `submission` 키를 무시) · `rcm cancel` → `rcm: cancel failed: <403 이유>` exit 2 · `--no-wait` JSON 의 `job_id joined state url` 넷 다 있음(dolomood 래퍼 키) | 실기 (Codex 규칙표 「옛 클라이언트 유지」) |
| G5.19 | 8805 의 v16 `data_dir` 을 `~/.local/share/rcm-verify-g5-mig` 로 복사(서버 내린 뒤 `cp -R`) | 새 빌드를 그 `data_dir` 로 8804 설정에서 기동 → 로그 · `ls backup/` · `sqlite3 … "PRAGMA user_version; SELECT name FROM sqlite_master WHERE name='submissions'"` | **기동 전** `backup/rcm.sqlite3.v16.bak`(`PRAGMA user_version` 16 · `PRAGMA integrity_check` ok · 원본과 잡 수 같음) · 기동 뒤 `user_version` 17 · `submissions` 표 존재 · 옛 잡들의 `GET /jobs/<id>` 정상(제출 행이 없어도 500 이 아니다) · 옛 잡을 admin 이 취소 가능 | 실기 (Codex #13 · M5i 결정 74) |
| G5.20 | 같은 v16 사본을 다시 · `mkdir -p backup && chmod 500 backup` | 기동 | **기동 실패** · 메시지 `migration backup failed … the database was not changed` · `user_version` **16 그대로** · `submissions` 표 **없음**(열·표 추가 0) · `backup/` 에 `.tmp` 잔재 없음 · `chmod 700 backup` 뒤 재기동하면 G5.19 대로 | 실기 (명세 §2 G5 「v16 백업 생성·검증 실패 시 열 추가 없음」) |
| G5.21 | `tests/test_cancel_capability.py`: `metadata_retention_days` 경계로 `delete_old_jobs` | ① 정상 삭제 ② 삭제 중간에 실패하도록 monkeypatch | ① 잡 행과 함께 `submissions` 행 0(`SELECT count(*) … WHERE job_id`) ② **롤백** — 잡 행도 제출 행도 남는다(같은 트랜잭션) | 자동 (Codex 권고 G5 「같은 트랜잭션」) |
| G5.22 | 8804 · Chrome(`tests/test_web_browser.py` 의 `Chrome` 클래스 재사용 · `RCM_CHROME`) · alice 의 잡 실행 중 | `/?poll=1&lang=ko` 에 alice 토큰 설정 → 그 잡 행의 `[data-cancel="<id>"]` · `lang=en` 도 · root 토큰으로 다시 | alice: 버튼 **disabled** + 이유 문구(버튼 `title` 또는 옆 텍스트 — 「이 서버는 admin 토큰만…」 류 · ko/en 둘 다 · 서버 문자열이면 번역 안 함) · root: 활성 · 8803(off) 의 alice: 활성 · `page_errors() == []` · 페이지가 키 상태를 어디서 읽는지(`/api/status.server.<키>` 또는 health) 보고서에 | 실기 (명세 §2 G5 「웹 취소 버튼」) |
| G5.23 | 설정 `cancel_requires_submission_token = "yes"` · `= 1` | `rcm serve --config` | 둘 다 exit 2 · 메시지에 키 이름 · `examples/server.toml` 에 키가 (주석으로) 있고 기본 `false` 라고 적혀 있다 | 실기 + 문서 |
| G5.24 | — | `pytest tests/test_cancel_capability.py -q` · `python scripts/mutcheck.py --only <강제 모드 capability 검사 제거 변이 이름>` · `pytest tests/test_store*.py -q` | 전부 초록 · caught(명세 §5 ②) · `DB_VERSION == 17` 이고 `_MIGRATIONS[17]` 이 표 생성 DDL 만(열 삭제·의미 변경 없음) | 자동 |

### GX — 가로지르기 (전 PR · 마지막 PR 머지 뒤)

| ID | 절차 | 기대 | 방법 |
|---|---|---|---|
| GX.1 | `ruff check . && ruff format --check . && pytest` · `node --test tests/web/*.test.js` · `python scripts/mutcheck.py` | 전부 초록 · mutcheck 는 26 + 3(명세 §5 ①②③) = **29** 전부 caught | 자동 |
| GX.2 | `CHANGELOG.md` `[Unreleased]` | PR 1(`requires`) · 2(`step_timeline`) · 3(문서) · 4(capability · v17 · 서버 키) 의 항목이 각각 PR 링크와 함께 · v17 과 「키를 켜면 옛 클라의 취소가 403」이 사용자 말로 · `schema_version` 불변이 명시 | 문서 |
| GX.3 | `GET /api/status` 셋(8801·8802·8804) | `schema_version` 이 M5i 릴리스(v0.2.6)와 같다 · 옛 키 전부 존재 — 키 추가만 | 자동 |
| GX.4 | 세 서버의 로그 · `data_dir` 의 잡 로그 전부 | `grep -rn "$HOME\|/Users/\|/opt/homebrew" <server.log> <data_dir>/logs/` 에서 나오는 줄이 **기동 배너의 `data_dir` 한 줄뿐**(M5i 와 같은 기준) · 토큰·capability 평문 0 | 자동 |
| GX.5 | `scripts/smoke_install.sh dist/*.whl`(새 wheel 빌드 뒤) | README 절차가 새 venv 에서 그대로 · `rcm --help` 에 `cancel … --cancel-token` | 자동 |
| GX.6 | `PLAN.md` | 결정 84~87 이 표에 · 결정 42 문구 불변 · 마일스톤 표에 M5j 와 M5k(G2) 분리 | 문서 |

## 2. 오너만 할 수 있는 것

**없음.** M5j 의 네 PR 은 전부 격리 에이전트가 시험 서버로 확인할 수 있다. 릴리스(v0.2.7 · main 머지가 태그를 만든다)와
이 Mac 운영 업그레이드(v17 마이그레이션 — `docs/operating.md` 「Upgrade」 · 결정 80 순서)는 검증 항목이 아니라 릴리스
절차이고, 오너의 결정 셋(명세 §7 — 순서 · G5 모델 · M5k)은 이 문서보다 **먼저** 끝나 있어야 한다. 검증 중 오너 결정이
필요한 것이 나오면 보고서 「오너 대기」에 적는다.

## 3. 에이전트 배치 (병렬 · 격리)

| 에이전트 | 영역 | 워크트리 · 브랜치 | 포트 | 보고서 |
|---|---|---|---|---|
| verify-g4 | G4.1~G4.19 · GX.3(8801) | `../remote_ci_monitor-acceptance-m5j-verify-g4` · `docs/acceptance-m5j-verify-g4` | 8801 | `docs/acceptance/reports/<날짜>-m5j-verify-g4.md` |
| verify-g1 | G1.1~G1.15 · GX.3(8802) | `../remote_ci_monitor-acceptance-m5j-verify-g1` · `docs/acceptance-m5j-verify-g1` | 8802 | `docs/acceptance/reports/<날짜>-m5j-verify-g1.md` |
| verify-g5 | G5.1~G5.24 · GX.3(8804) · GX.4 · GX.5 | `../remote_ci_monitor-acceptance-m5j-verify-g5` · `docs/acceptance-m5j-verify-g5` | 8803 · 8804 · 8805(옛 0.2.6 서버 — 옛 클라 venv 의 wheel 출처) | `docs/acceptance/reports/<날짜>-m5j-verify-g5.md` |
| verify-g3-docs | G3.1~G3.6 · GX.1 · GX.2 · GX.6 | `../remote_ci_monitor-acceptance-m5j-verify-g3-docs` · `docs/acceptance-m5j-verify-g3-docs` | — | `docs/acceptance/reports/<날짜>-m5j-verify-g3-docs.md` |

각 에이전트는 자기 영역의 표를 **위에서 아래로** 전부 돌리고, 오류마다 fix PR 을 낸 뒤 보고서를 쓴다. 보고서는 한 PR
(`docs/m5j-verification-reports`)로 모아 올린다. 서로의 포트·`data_dir`·워크트리를 쓰지 않는다. G4 의 옛 워커(G4.17)와 G5 의
옛 클라(G5.18)는 같은 0.2.6 wheel 을 쓰지만 venv 는 **각자** 만든다(같은 파일을 두 세션이 고치지 않게).
