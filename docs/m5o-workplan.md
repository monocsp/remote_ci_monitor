# M5o 작업 명세 — 손으로 하던 세 가지를 rcm 이 맡는다

> 2026-11-18 오너 요청: 「1~3번 M5o로 계획서부터 진행해」. 같은 날 v0.5.0 기능 전체 정리와 사용성
> 논의(격리 에이전트가 운영 DB 30일치를 읽은 근거 + Codex 의 독립 제안)에서 오너가 고른 세 가지다.
> 결정 번호는 **109~124**. 전부 추천값으로 구현하고 오너 확인을 기다린다(⛔ 없음).
>
> 테스트 시나리오는 `docs/m5o-test-scenarios-{a,b,c}.md`(역할마다 하나), 구현 중 바뀐 것은 이 문서의
> §10 「구현하며 바뀐 것」에 남긴다.

## 0. 한 줄

**같은 브랜치의 옛 잡은 rcm 이 치우고, 실패는 이름으로 말하고, 프리셋은 파일을 붙여 넣지 않고 읽는다.**
셋 다 지금은 사람 또는 세션 쪽 스크립트가 손으로 하고 있는 일이다.

## 1. 실측 — 운영 DB 30일치 (2026-10-19 ~ 11-18, `sqlite3 -readonly`, 1,511건)

### 1.1 같은 브랜치에 새 잡을 낼 때

| 모양 | 건수 | 비용 |
|---|---|---|
| 세션이 옛 잡을 **손으로 취소하고 몇 초 안에** 같은 프리셋 · 같은 브랜치로 다시 냄 | **70** | 세션마다 「내 잡 찾기 → 취소 → 다시 내기」 세 단계. dolomood `remote_ci.sh --cancel` 이 이 일을 한다 |
| 같은 프리셋 · 같은 브랜치의 **새 잡이 이미 들어와 있는데** 옛 잡이 끝까지 돎 | **43** | 새 잡이 들어온 뒤에 옛 잡이 쓴 레인 시간 **606분(10시간)**. 그중 28건은 새 잡이 들어왔을 때 **아직 시작도 안 했다** |

취소 93건의 「새 push 때문」 49건이라는 앞선 숫자는 취소 **뒤에** 들어온 재제출을 세지 않은 것이다.
위 표가 바로잡은 값이다. 서버 쪽 supersede 가 줄이는 것은 두 줄 모두다 — 첫 줄은 세션의 손일을,
둘째 줄은 레인 시간을.

브랜치 이름은 tree 잡이면 `source.branch`, git_ref 잡이면 `source.ref` 에 이미 있다. 요청자 이름은
1,413건이 `macbook` 토큰 하나라 「같은 사람」을 가르는 힘이 약하다(§2.2 의 규칙이 그래서 브랜치에 기댄다).

### 1.2 실패 요약

| 프리셋 | 실패 | 실패 이름 기록됨 | `failed_step` 있음 | 요약 |
|---|---|---|---|---|
| gate | 176 | 173 | 171 | 거의 전부 `exit 1` |
| test-narrow | 52 | 51 | 51 | `exit 1` |
| game-web-check | 40 | 40 | 40 | `exit 1` |
| gate-fast | 39 | 39 | 39 | `exit 1` |
| scenario-qa | 20 | 0 | 0 | `exit 1` (스크립트가 `::rcm::fail` 을 안 찍는다 — 그쪽 레포의 일) |

이름과 스텝은 DB(`job_failures`, `jobs.failed_step`)에 있는데 **요약 · 알림 · `rcm wait` 한 줄은 `exit 1`**
만 말한다. 알림 훅이 받는 `RCM_URL` 은 `public_url` 이 비어 있어 **3,721건 전부
`http://0.0.0.0:8787/...`** — 다른 기기에서 열리지 않는다.

### 1.3 프리셋을 옮기는 길

- 프리셋의 정본은 dolomood-renew 레포의 `check_presets.py`(732줄)다. 레포 쪽 명세와 운영 `server.toml` 을
  비교해 「붙여 넣을 TOML」을 찍는다. 사람이 그것을 `server.toml`(47 KB)에 붙여 넣는다.
- 손으로 만든 `server.toml.bak*` 이 24개다.
- 서버 재시작 49번, `rcm reload` 2번. TRACKING.md 는 아직 「재시작」이라고 쓴다.
- `reload` 는 바꾼 뒤에야 무엇이 바뀌었는지 말한다. 미리 볼 길이 없다.

## 2. A — 같은 브랜치의 옛 잡을 치운다 (`supersede`)

### 2.1 설정 (결정 109·110)

```toml
[[presets]]
name = "gate"
supersede = "all"        # "off"(기본) | "waiting" | "all"
```

| 값 | 새 잡이 큐에 들어올 때 옛 잡이 |
|---|---|
| `"off"` (기본) | 그대로다. 오늘과 같다 |
| `"waiting"` | 아직 기다리는 중(`WAITING_STATES` — 업로드 중 포함)이면 취소, 이미 돌면 끝까지 둔다 |
| `"all"` | 기다리든 돌든 취소한다. 도는 잡은 평소 취소와 같은 길(SIGTERM · 유예 · 원격 워커 cancel) |

- **기본은 `"off"`** (결정 109). 배포 · 업로드 · 스토어 프리셋에서 옛 잡이 몰래 취소되면 안 된다.
  켜는 것은 프리셋 주인의 선택이다.
- **값이 셋인 이유** (결정 110). 도는 잡을 죽이는 것은 기다리는 잡을 치우는 것보다 비싼 결정이다.
  게이트처럼 「최신 코드만 의미 있다」면 `all`, 긴 잡의 중간 결과도 보고 싶다면 `waiting`.

### 2.2 무엇이 「같은 브랜치의 옛 잡」인가 (결정 111·112·113)

새 잡 N 이 **`queued` 가 되는 순간**(tree 잡은 업로드가 끝났을 때, git_ref 잡은 제출 때) 아래를 **모두**
만족하는 옛 잡 O 를 고른다.

1. `O.preset == N.preset`, N 의 프리셋이 `supersede != "off"`.
2. `O.source.mode == N.source.mode`, `O.source.repo == N.source.repo`.
3. 브랜치가 같다 — tree 는 `source.branch`, git_ref 는 `source.ref`. **둘 중 하나라도 비었으면 대상이
   아니다**(detached HEAD 의 tree 잡은 서로 무관한 작업일 수 있다).
4. `O.inputs == N.inputs`. `test-narrow` 처럼 입력이 다르면 **다른 질문**이다(결정 112).
5. 신원이 다르다 — `O.source.identity != N.source.identity`. 같으면 애초에 합류(join)다.
6. `O.requester.name == N.requester.name`, 그리고 **O 의 합류자 전원도 같은 이름**이다. 다른 사람이
   기다리는 잡을 내 push 가 죽이지 않는다(결정 113).
7. `O.id < N.id`, O 는 활성 상태(`supersede` 값이 허락하는 상태).
8. `O.target_worker` 가 없다 — 점검 잡 · `--machine` 으로 낸 잡은 사람이 일부러 고른 것이다.

**업로드 중인 새 잡은 아직 아무것도 치우지 않는다**(결정 111). 업로드가 깨지면 옛 잡만 남아야 한다.

### 2.3 치운 잡이 남기는 것 (결정 114·115)

- 상태 `cancelled`, `cancelled_by = "superseded"`, `summary_code = "superseded"`,
  `summary_args = {"by": N}` → 요약 `superseded by #N`.
- 이벤트 · 알림은 평소 취소와 같다. 알림 규칙이 `on = ["cancelled"]` 을 고르면 받는다.
- `rcm wait O` · `rcm run` 의 기다림은 종료 코드 **2**(취소, 기존 표)로 끝나고 한 줄을 더 찍는다:
  `#O superseded by #N — rcm wait N`. JSON 출력은 `superseded_by: N`.
  **자동으로 N 을 따라가지 않는다**(결정 114) — 기다리던 쪽이 원한 것은 O 의 코드였다. 종료 코드가 말을
  바꾸면 스크립트가 오판한다.
- 큐 행 · 잡 상세의 요약은 `superseded by #N` 이고 웹은 `#N` 을 그 잡으로 가는 링크로 그린다.
- **N 은 O 의 우선순위를 물려받는다**(결정 115) — O 가 `bump` 로 `high` 였다면 N 도 `high`.
  사람이 「이 브랜치 급하다」고 한 말은 브랜치에 대한 말이다. 낮아지지는 않는다(max).

### 2.4 제출 쪽에서 끄기 (결정 116)

`rcm run PRESET --no-supersede` · API `"supersede": false` — 이번 제출은 아무것도 치우지 않는다.
흔들리는 테스트를 두 판 나란히 보고 싶을 때. 프리셋이 `off` 면 켤 수는 없다(프리셋 주인이 정한다).

### 2.5 인터페이스

| 이름 | 자리 | 하는 일 |
|---|---|---|
| `Preset.supersede: str = "off"` | `config.py` | 위 세 값만 받는다. 그 밖의 값은 `ConfigError` |
| `core/supersede.py` `superseded_by(new: Job, active: Iterable[Job], mode: str) -> list[int]` | 순수 | §2.2 의 1~8 규칙. 시계 · DB 를 안 본다 |
| `Store.supersede(new_id, old_ids, now) -> list[int]` | `store.py` | 한 트랜잭션: 옛 잡마다 기다리면 바로 `cancelled`, 돌면 `request_cancel(by="superseded")`, N 의 우선순위 올리기. 실제로 처리한 id 를 돌려준다 |
| `App._supersede_after_queue(job)` | `server.py` | 잡이 `queued` 가 되는 두 자리(tree 업로드 끝 · git_ref 제출)에서 부른다 |
| 요약 코드 `superseded` | `core/outcome.py` `CODES` | `superseded by #{by}` |
| `/api/status` 큐 행 · `GET /jobs/{id}` | `superseded_by: int \| null` | 키 추가만 — `schema_version` 그대로 |

DB 는 바꾸지 않는다. `summary_args` 가 이미 JSON 이다(DB v23 그대로).

## 3. B — 실패를 이름으로 말한다 · 알림 링크를 바로잡는다

### 3.1 요약 (결정 117·118)

`exit_code` 요약이 실패 이름과 스텝을 알면 그것을 먼저 말한다.

```
오늘:  exit 1
M5o :  test: home_dudi_bubble_gap_golden_test.dart +1 more (exit 1)
       analyze failed (exit 1)                       # 스텝만 있고 단위 이름이 없을 때
       exit 1                                        # 둘 다 없을 때 — 오늘과 같다
```

- 코드는 **`exit_code` 그대로**다(결정 117). 인자만 늘린다: `{"code", "step"?, "first"?, "more"?}`.
  `summary_code == "exit_code"` 로 거르던 쪽이 깨지지 않는다.
- `first` 는 스텝이 아닌 **첫 실패 이름**(잡이 찍은 순서 — `failures_json` 과 같은 규칙), 경로면
  **마지막 조각**(`test/feature/home/x_test.dart` → `x_test.dart`), 80자에서 자른다. `more` 는 나머지 단위
  이름의 수. 스텝 이름 자체는 `step` 으로 간다(결정 118). 전체 이름은 지금처럼 `failures[]` 에 있다.
- 잡이 `::rcm::summary::` 로 찍은 문장은 지금처럼 이긴다 — 팀이 쓴 말이다.
- 로컬 레인(`worker.py`)과 원격 워커 완료(`remote_workers.py`) **두 자리 모두** 같은 함수로 만든다:
  `outcome.exit_summary(code, failed_step, failure_names, steps) -> (text, code, args)`.
- `timed_out` 도 스텝을 알면 붙인다: `timed out after 40m (in test)` — `last_step` 기준, 인과를 주장하지
  않는 말(「in」)이다.

### 3.2 알림 환경 변수 (결정 119)

훅이 받는 env 에 둘을 더한다.

| 변수 | 값 |
|---|---|
| `RCM_FAILURES` | 실패 이름, 잡이 찍은 순서, 줄바꿈으로 묶고 최대 20개. `sanitize_text` 를 거친다 |
| `RCM_FAILURE_COUNT` | 이름의 총수(잘린 것 포함 — `fail_truncated` 면 `20+`) |

웹훅(`url`) JSON 본문에는 `failures: [..]` 를 같은 상한으로 더한다.

### 3.3 링크의 앞부분 (결정 120)

`public_url` 이 비었을 때 알림 · `rcm run` 이 찍는 잡 주소의 앞부분:

1. `public_url` 이 있으면 그것(오늘과 같다).
2. `bind` 가 특정 주소(예: `100.x.y.z`)면 `http://<bind>:<port>`(오늘과 같다).
3. `bind` 가 `0.0.0.0` · `::` · 빈 값이고 mDNS 광고가 켜져 있으면 `http://<광고 호스트>.local:<port>`.
4. 그 밖이면 `http://<hostname>:<port>` — `0.0.0.0` 은 **절대** 링크에 넣지 않는다.

한 함수 `server_base_url(section, *, hostname)` 를 `notify.py` 와 `server.py` 가 같이 쓴다(요청이 있는
`server.py` 의 Host 우선 규칙은 그 위에 그대로 남는다). `rcm check` 에 행 하나:
`public url  warn  empty — links use http://epeuem-ui-Macmini.local:8787 (set [server] public_url)`.

## 4. C — 프리셋을 파일에서 읽는다

### 4.1 `include` (결정 121·122·123)

```toml
# ~/.config/rcm/server.toml
include = ["presets.dolomood.toml"]      # server.toml 이 있는 폴더 기준, 또는 절대 경로 · ~ 허용

[server]
...
```

```toml
# ~/.config/rcm/presets.dolomood.toml — 프리셋만
[[presets]]
name = "gate"
...
```

- **들인 파일에는 `[[presets]]` 만** 쓸 수 있다(결정 121). `[server]` · `[[repos]]` · `[[notify]]` ·
  `[[probes]]` · `include` 는 `ConfigError` — 저장소 토큰 · 비밀 · 바인드 주소는 server.toml 에만 있다.
  들인 파일이 또 들이는 일은 없다(한 단계).
- 프리셋 이름이 겹치면 `ConfigError: preset 'gate' is defined in both server.toml and presets.dolomood.toml`.
- **파일 권한**(결정 122): 들인 파일은 서버를 돌리는 사용자 소유이고 그룹 · 남이 쓸 수 없어야 한다
  (`mode & 0o022 == 0`). 아니면 `ConfigError`. 프리셋은 서버가 실행할 argv 다 — 아무나 쓸 수 있는 파일이
  그것을 정하면 「프리셋만 실행」이라는 약속이 무너진다.
- **rcm 은 파일을 가져오지 않는다**(결정 123). git pull 도, URL 도 없다. 레포의 파일을 쓰고 싶으면 그
  체크아웃의 경로를 적는다 — 그 체크아웃을 누가 언제 바꾸는지는 오너가 정한다. 문서가 이 위험을 한
  문단으로 말한다(그 레포에 push 할 수 있는 사람은 다음 `reload` 때 서버가 돌릴 argv 를 정한다).
- `rcm reload` 는 들인 파일도 다시 읽는다. 파일이 없거나 깨졌으면 지금처럼 **아무것도 바꾸지 않는다**.
- `rcm check` 에 행: `presets   ok   24 (server.toml 2 · presets.dolomood.toml 22)`.

### 4.2 `rcm reload --dry-run` (결정 124)

```
$ rcm reload --dry-run
would reload ~/.config/rcm/server.toml (+ presets.dolomood.toml)
  presets changed: gate, test-narrow
  presets added:   game-web-smoke
  nothing applied (--dry-run)
```

- 서버가 같은 `load_server_config` 와 같은 거절 규칙(`RELOADABLE_NOT`)을 돌리고 **대입만 하지 않는다**.
  `POST /admin/reload` 의 `{"dry_run": true}`. admin 토큰.
- 바뀐 프리셋마다 **어느 키가** 바뀌었는지(`argv`, `timeout_seconds`, …) 이름만 보여 준다. 값은 안
  보여 준다 — env 값 · argv 에 무엇이 있을지 모른다.
- `--json` 이면 `{"dry_run": true, "presets_changed": {"gate": ["argv", "timeout_seconds"]}, ...}`.

dolomood 의 `check_presets.py` 가 하던 「레포 명세 ↔ 운영」 비교는 이것으로 대신할 수 있다. 그쪽 레포를
바꾸는 일은 이 마일스톤이 아니다(그쪽 세션의 일 — §8).

## 5. 테스트 — 반드시 들어갈 경우

역할 문서(`m5o-test-scenarios-{a,b,c}.md`)는 아래를 최소 집합으로 갖고, 테스트를 먼저 쓴다(test-first).

### A — supersede

| # | 경우 | 기대 |
|---|---|---|
| A1 | 베스트: `all` · 같은 브랜치 · 옛 잡 queued | 옛 잡 `cancelled`, `superseded by #N`, N queued |
| A2 | 베스트: `all` · 옛 잡 running(로컬 레인) | 평소 취소 길로 끝나고 요약이 `superseded by #N` |
| A3 | `waiting` · 옛 잡 running | 그대로 돈다 |
| A4 | 옛 잡 running 이 **원격 워커** | 워커가 cancel 을 받는다(heartbeat 응답) |
| A5 | 입력이 다르다(`test-narrow` 대상 파일) | 둘 다 남는다 |
| A6 | 브랜치가 비었다(detached tree) | 둘 다 남는다 |
| A7 | tree 와 git_ref, 같은 이름의 브랜치 | 둘 다 남는다(모드가 다르다) |
| A8 | 같은 신원 | 합류 — supersede 가 끼어들지 않는다 |
| A9 | 옛 잡에 다른 요청자의 합류자 | 남는다 |
| A10 | 새 잡의 업로드가 실패(`snapshot_rejected`) | 옛 잡이 남는다 |
| A11 | 새 잡 업로드 중 서버 재시작 | 옛 잡이 남는다 |
| A12 | `--no-supersede` | 남는다 |
| A13 | 프리셋 `off`(기본) | 남는다 — 오늘과 같다 |
| A14 | 옛 잡이 셋(queued 둘 + running 하나) | 셋 다 처리, 반환 id 셋 |
| A15 | 옛 잡 `high` · 새 잡 `normal` | 새 잡 `high`. 반대면 그대로 |
| A16 | 옛 잡이 `target_worker` 점검 잡 | 남는다 |
| A17 | 같은 순간에 두 새 잡이 queued (경쟁) | 더 큰 id 하나만 남고 작은 쪽이 치워진다. 서로 치우지 않는다 |
| A18 | 옛 잡이 이미 `cancelling` | 다시 취소 요청하지 않는다, 요약도 바꾸지 않는다 |
| A19 | `rcm wait O` | 종료 2, `superseded by #N — rcm wait N` 한 줄, `--json` 에 `superseded_by` |
| A20 | reload 로 프리셋이 `off` → `all` | 다음 제출부터 적용, 이미 들어온 잡을 소급하지 않는다 |
| A21 | 설정 `supersede = "yes"` | `ConfigError` |

### B — 실패 요약 · 알림

| # | 경우 | 기대 |
|---|---|---|
| B1 | 베스트: 스텝 `test` + 단위 이름 둘 | `test: x_test.dart +1 more (exit 1)` |
| B2 | 스텝만 | `analyze failed (exit 1)` |
| B3 | 아무것도 없다 | `exit 1` (오늘과 같은 글자) |
| B4 | `::rcm::summary::` 를 찍었다 | 그 문장 |
| B5 | 이름이 120자 경로 | 마지막 조각, 80자 자름 |
| B6 | `fail_truncated` | `more` 가 `+N more` 뒤에 `+` (예: `+19+ more`) — 정확한 수를 주장하지 않는다 |
| B7 | 원격 워커 완료 | 로컬과 **같은 글자** |
| B8 | 이름에 제어 문자 · 줄바꿈 | 요약 · env 에서 지워진다 |
| B9 | 타임아웃 + `last_step` | `timed out after 40m (in test)` |
| B10 | 알림 env | `RCM_FAILURES` 줄바꿈 · 20개 상한 · `RCM_FAILURE_COUNT` |
| B11 | 웹훅 본문 | `failures` 배열 |
| B12 | 링크: `public_url` 있음 / bind 특정 / 0.0.0.0+mDNS / 0.0.0.0+광고 끔 / `::` | §3.3 의 네 갈래. `0.0.0.0` 은 어떤 경우에도 안 나온다 |
| B13 | `rcm check` | 빈 `public_url` 은 warn(실패 아님), 쓰일 주소를 보여 준다 |
| B14 | 성공 잡 | 요약에 아무것도 붙지 않는다 |

### C — include · reload --dry-run

| # | 경우 | 기대 |
|---|---|---|
| C1 | 베스트: 들인 파일의 프리셋 22개 + server.toml 2개 | 24개, 순서는 server.toml 먼저 그다음 include 순서 |
| C2 | 상대 경로 · 절대 경로 · `~` | 셋 다 server.toml 폴더 / 그 경로 / 홈 기준 |
| C3 | 들인 파일에 `[server]` · `[[repos]]` · `[[notify]]` · `[[probes]]` · `include` | 각각 `ConfigError`, 파일 이름이 들어간다 |
| C4 | 이름 겹침 | `ConfigError`, 두 파일 이름 |
| C5 | 파일 없음 | `ConfigError` — 기동 거절 · reload 는 아무것도 안 바꿈 |
| C6 | 그룹 쓰기 가능 · 남의 소유 | `ConfigError` |
| C7 | 들인 파일이 깨진 TOML | 기동 거절 · reload 는 아무것도 안 바꿈 |
| C8 | `include` 가 문자열 하나(목록 아님) · 빈 목록 | 문자열은 `ConfigError`, 빈 목록은 없는 것과 같다 |
| C9 | reload 로 들인 파일만 바뀜 | `presets_changed` 에 나온다 |
| C10 | `--dry-run` | 서버 설정 객체가 그대로(같은 `id()`), 응답에 바뀔 키 이름 |
| C11 | `--dry-run` 인데 `[server]` 가 바뀜 | 409 와 같은 문장, 아무것도 안 바뀜 |
| C12 | `--dry-run` 응답에 env 값 · argv 값이 없다 | 키 이름만 |
| C13 | 들인 프리셋의 `checks` 가 server.toml 의 프리셋을 가리킴(그 반대도) | 검증이 합친 목록 위에서 돈다 |
| C14 | `rcm check` | 파일별 프리셋 수 |
| C15 | `include` 에 server.toml 자신 | `ConfigError`(자기 자신 · 중복 경로) |

## 6. 안 만드는 것

- **자동 재실행 · 잃은 잡 다시 돌리기.** 배포가 두 번 나갈 수 있다(Codex 와 같은 의견).
- **supersede 의 자동 따라가기**(`rcm wait O` 가 N 의 결과를 돌려주기) — §2.3 결정 114.
- **include 의 원격 가져오기 · 감시**(파일이 바뀌면 저절로 reload). reload 는 사람이 부른다.
- **들인 파일의 `[[repos]]`** — 토큰 파일 경로가 거기 있다.
- **프리셋 값 내보내기 API**(`argv` · `env` 를 HTTP 로). 필요하면 서버 머신에서 파일을 본다.

## 7. PR 순서 · 완료 기준

세 기능은 서로 다른 코드를 만진다. 역할마다 브랜치 하나, 워크트리 하나.

| PR | 브랜치 | 만지는 곳 |
|---|---|---|
| 0 | `docs/m5o-usability-workplan` | 이 문서 |
| A | `feat/m5o-supersede-older-branch-jobs` | `core/supersede.py` · `store.py` · `server.py` · `cli.py`(run/wait) · `config.py`(키 하나) · 웹(요약 링크) |
| B | `feat/m5o-failure-name-summaries` | `core/outcome.py` · `worker.py` · `remote_workers.py` · `core/notify.py` · `notify.py` · `server.py`(base url) · `cli.py`(check) |
| C | `feat/m5o-preset-include-files` | `config.py` · `server.py`(reload) · `cli.py`(reload · check) |

완료 기준 — PR 마다:

1. 역할 문서 → 실패하는 테스트 → 구현 → `ruff check . && ruff format --check . && pytest` ·
   `node --test tests/web/*.test.js`(웹을 만지면) · `python scripts/mutcheck.py` 전부 통과.
2. mutcheck 에 그 기능의 핵심 변이를 둘 이상 더한다(A: 합류자 이름 검사 제거 · 업로드 중 치움.
   B: 원격 경로가 옛 요약 · `0.0.0.0` 링크. C: 권한 검사 제거 · 들인 파일의 `[[repos]]` 허용).
3. 문서: `docs/configuration.md`(키 · include · 링크) · `docs/usage.md` · `docs/usage.ko.md`(supersede ·
   `--no-supersede` · `reload --dry-run`) · CHANGELOG [Unreleased].
4. **격리 에이전트 검증** — 구현한 에이전트가 아닌 새 에이전트가 이 문서와 diff 만 보고 §5 의 표를
   실제로 돌려 확인한다(포트 8790 · `~/.local/share/rcm-devtest-m5o-*`, 운영 설정 · 데이터 금지).
5. dev 머지 뒤 세 PR 을 묶어 **v0.6.0**(새 설정 키 · 새 요약 문장 — 마이너). 운영 반영은 기존 절차.

## 8. 이 마일스톤 밖 — 다른 쪽의 일

- 운영 `server.toml` 에 `supersede = "all"`(gate · gate-fast · test-narrow) · `include` 를 켜는 일 —
  **오너가 고른다**. 이 마일스톤은 기능만 넣고 운영 설정은 바꾸지 않는다. 켜는 문단을 문서에 둔다.
- dolomood-renew 의 `check_presets.py` · `remote_ci.sh --cancel` 을 줄이는 일 — 그쪽 세션.
- scenario-qa 가 `::rcm::fail` 을 찍게 하는 일 — 그쪽 레포.

## 9. 결정 (109~124 · 추천값으로 구현, 확인 대기)

| # | 결정 | 추천 |
|---|---|---|
| 109 | supersede 기본값 | `off` — 켜는 것은 프리셋 주인 |
| 110 | 값 | `off` · `waiting` · `all` |
| 111 | 언제 치우나 | 새 잡이 `queued` 가 될 때. 업로드 중엔 안 치운다 |
| 112 | 입력이 다르면 | 다른 질문 — 안 치운다 |
| 113 | 합류자가 다른 사람이면 | 안 치운다 |
| 114 | `rcm wait O` | 종료 2 + 안내 한 줄, 따라가지 않는다 |
| 115 | 우선순위 | 새 잡이 max 를 물려받는다 |
| 116 | 제출 쪽 끄기 | `--no-supersede`, 켜기는 없다 |
| 117 | 요약 코드 | `exit_code` 그대로, 인자만 는다 |
| 118 | 요약에 실을 이름 | 첫 단위 이름의 마지막 조각 80자 + 나머지 수 |
| 119 | 알림 | `RCM_FAILURES`(20개) · `RCM_FAILURE_COUNT` · 웹훅 `failures` |
| 120 | 링크 | public_url > 특정 bind > mDNS 이름 > hostname, `0.0.0.0` 금지 |
| 121 | 들인 파일 | 프리셋만, 한 단계 |
| 122 | 들인 파일 권한 | 서버 사용자 소유 · 그룹/남 쓰기 불가 |
| 123 | 가져오기 | 안 한다 — 로컬 경로만 |
| 124 | 미리 보기 | `rcm reload --dry-run`, 바뀐 키 이름만 |

## 10. 구현하며 바뀐 것

(구현 중에 채운다)
