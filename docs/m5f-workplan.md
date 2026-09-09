# M5f 작업 명세 — CPU 가 무리하지 않는 선의 병렬 레인

> 오너 요청(2026-09-08): 「여기서 작업하는 게 CPU 를 너무 잡아먹지 않도록 설정하는 값을 주고 **기본값 80%**.
> 그거에 맞게 설정되면 병렬도 돌릴 수 있게 — CPU 사용량이 적으면 여러 개를 병렬로.」
>
> 한 줄 요약: **`lanes` 를 올려도 안전하게** 만든다. 레인 2 부터는 호스트가 한가할 때만 잡을 집어 든다.
>
> 이 문서는 크로스리뷰(`docs/reviews/2026-09-08-m5f-design-review.md`)를 **반영한 2판**이다. 초안이 틀렸던
> 곳과 왜 바꿨는지는 §13 에 모아 두었다.
>
> 바꾸지 않는 것: 런타임 의존성 0 · 스키마 v1(**키를 더하지 값을 바꾸지 않는다**) · `/worker/*` 프로토콜 ·
> 등록된 프리셋만 실행 · 「모르는 값은 null, 화면은 `—`」 · 순수 계층은 I/O 도 시계도 안 본다.

## 1. 이 기능이 답해야 하는 것

| 질문 | 오늘 | M5f |
|---|---|---|
| 레인을 2 로 올려도 되나? | 모른다. 무거운 잡 둘이 겹치면 빌드 머신이 기어간다 | CPU 가 `cpu_max_percent` 아래일 때만 두 번째 레인이 집는다 |
| 왜 레인이 놀고 있나? | 화면에 `idle` — 거짓말이다 | `held` + 사유 코드(`cpu_busy` · `no_sample` · `cooldown`) + `held_since` |
| 내 잡은 왜 안 움직이나? | `waiting for lane` | 보류된 레인이 있으면 `held_by_load` |
| 게이트가 너무 오래 닫혀 있나? | — | `held_since` 로 재고, 길면 `rcm check` 가 **경고**한다(FAIL 아님) |

## 2. 지금 상태 (전수 조사)

**레인은 이미 있다. 없는 것은 「부하를 보고 미루는 것」뿐이다.**

| 무엇 | 어디 | 오늘 |
|---|---|---|
| 로컬 레인 수 | `config.py:79` `ServerSection.lanes = 1`, 검증 `:574`, 스레드 생성 `worker.py:448` | **로컬 레인만** 센다 |
| 원격 레인 수 | `config.py:210` `WorkerConfig.lanes = 1`, 검증 `:899`, 등록 `remote_workers.py:249-256`, 표시 `:153` | 워커의 `worker.toml` 이 정한다. 풀의 레인 = 로컬 + 원격(`server.py:359-361`) |
| 로컬 레인 스레드 | `worker.py:239` `Worker.run` → `:247` `store.claim(self.lane, …)` | `paused` 만 보고 바로 claim. 없으면 0.5초 대기 |
| 원격 claim | `remote_workers.py:310` `worker_claim` → `:343` `_try_claim` → `store.claim(…, worker_name=)` | long-poll `worker_claim_wait_seconds`(20), 슬롯 `CLAIM_WAIT_SLOTS = 8`(`:61`) |
| claim SQL | `store.py:869` | `state=queued AND pool=? AND 그룹 안 겹침 ORDER BY priority DESC, id LIMIT 1`. 레인 과할당은 `worker_name`+`lane` 검사(`:861`) |
| 로컬 표본 | `server.py:256` `HostSampler`. 접근자는 `App._hosts()`(`server.py:631-639`) — `App.sampler` 는 `start()` 전엔 `None`(`:209`) | `cpu.busy` = `100 − idle`, **머신 전체 백분율**(macOS `hostparse.py:101`, Linux `:249`) |
| 원격 표본 | 워커가 heartbeat 에 실어 보냄(`remote_worker.py:242`) → 서버가 파싱 `remote_workers.py:520` → 보관 `:525` | `history` 도 온다(`hostparse.py:390`). **`sampled_at` 은 서버가 받은 시각으로 다시 찍는다**(`:520`) |
| 표본 낡음 | `hostparse.py:282` `stale()` — `age > 3 × interval` | mutcheck ④ 가 지킨다 |
| 살아 있는 레인 | `core/queue.py:233` `live = [w for w in workers if w.state != WORKER_DOWN]` | `down` 만 뺀다 |
| ETA 그리디 | `core/queue.py:235-237` `lane_free`·`lane_last_job`·`idle_since` 가 **`dict[int]`(레인 번호)**, 잡 귀속은 `job.lane` 만(`:249-252`) | ⚠️ **기존 버그** — §5.3 |
| `idle_since` | `core/queue.py:238-239` | `state == WORKER_IDLE` 인 레인만 넣는다 |
| 워커 상태 값 | `core/model.py:74` `idle` · `busy` · `down` | 셋뿐 |
| 대기 사유 | `core/model.py:79~90` 12종 | 부하 사유 없음 |
| 「Not moving」 순서 | `core/model.py:93` `ACTIONABLE_REASONS` 는 **파이썬 어디서도 안 쓴다**. 살아 있는 사본은 `web/app.js:16` | 웹만 쓴다 |
| 신뢰도 | `core/queue.py:145` `confidence()` — `compute_queue` 가 아니라 **렌더 시점**에 세 곳에서 따로 계산(`core/status.py:176` · `render_text.py:193` · `web/app.js:263`) | 동시 실행을 모른다 |
| 중앙값 | 풀별(`server.py:623`) × 잡 키별. `min_samples = 2`, `sample_days = 45` | 동시 실행 축이 없다 |
| mutcheck | `scripts/mutcheck.py:45-127` — **10종** | `AGENTS.md:40`(「8 known mutations」)과 mutcheck docstring(「변이 6종」)이 낡았다 |

### 2.1 설계를 정하는 발견

**두 claim 경로가 모두 서버 프로세스 안에서 돈다.** 로컬 레인 스레드는 `store.claim` 을 직접 부르고
(`worker.py:247`), 원격 워커는 HTTP 로 서버의 `_try_claim`(`remote_workers.py:343`)을 부른다. 그리고
**서버는 두 머신의 표본을 모두 들고 있다.** 그래서 판정을 claim **직전** 한 곳에 두면 끝난다.

## 3. 부하는 **서버**가 판단한다

- **`rcm worker` 는 한 줄만 바뀐다**(§3.1). **프로토콜은 그대로다** — 보류된 레인은 오늘 「큐가 비었을
  때」와 똑같이 204 를 받고 다시 long-poll 한다.
- **`store.py` 는 claim 규칙이 안 바뀐다.** 게이트가 SQL **앞**에 있다. (열 하나는 는다 — §6.)
- **규칙이 한 벌이다.** 워커에 두면 로컬용·원격용 두 벌이 되고, `/api/status` 에 `held` 를 그리려면 워커가
  자기 상태를 다시 보고해야 한다.

### 3.1 고쳐야 할 fail-open 구멍

서버는 원격 표본을 받을 때 `sampled_at = now`(서버 시각)로 **다시 찍는다**(`remote_workers.py:520`).
그래서 heartbeat 지연은 `stale` 판정에 **아예 안 잡힌다** — 워커의 샘플러가 죽어도 heartbeat 만 살아
있으면 서버는 굳은 표본을 영원히 「새것」으로 본다.

고치는 법(프로토콜 변경 없음, 한 줄): `remote_worker.py:242` `_host_sample()` 은 `host_json` 이 계산한
`stale` 을 이미 손에 들고 버린다(`:254` 에서 `pop`). **`stale` 이면 표본을 아예 보내지 않는다.** 그러면
서버가 든 표본이 늙어 게이트가 닫힌다.

**⚠️ 이 성질은 워커까지 새 버전일 때만 성립한다.** `host_sample` 은 선택 필드라 옛 워커가 굳은 표본을 계속
보내면 서버는 구분할 수 없다. `WorkerRow.version` 이 이미 있으니 `rcm check` 가 **레인 ≥ 2 인 워커의
버전이 서버보다 낮으면 경고**한다(§5.4).

## 4. 설계

### 4.1 설정 키 (`[server]`)

```toml
[server]
lanes = 2                          # 이미 있는 키 — 로컬 레인 수
admission = "load"                 # "load"(기본) | "always" — "always" 는 오늘의 동작
cpu_max_percent = 80               # 이 위면 레인 2 부터 잡지 않는다  ← 오너가 정한 값
admission_samples = 3              # 연속으로 이만큼의 표본이 전부 기준 아래여야 연다
admission_cooldown_seconds = 30    # 한 머신에서 게이트를 지나 잡을 집으면 이만큼 쉰다
```

메모리 게이트는 **넣지 않는다**(결정 42, §13-F).

검증 — 오류 문구에 섹션·키 이름이 들어가는 오늘의 방식(`config.py:574`), 교차 검증은 `config.py:609-613`
스타일:

| 키 | 규칙 | 문구 |
|---|---|---|
| `admission` | `"load"` \| `"always"` | `[server] admission must be "load" or "always"` |
| `cpu_max_percent` | `1 <= x <= 100` | `[server] cpu_max_percent must be between 1 and 100` |
| `admission_samples` | `>= 1` | `[server] admission_samples must be >= 1` |
| `admission_samples` | `<= [host] history_samples` | `[server] admission_samples must be <= [host] history_samples` |
| `admission_samples × [host] interval_seconds` | `<= admission_cooldown_seconds` | `[server] admission_samples × [host] interval_seconds must not exceed admission_cooldown_seconds` |
| `admission_cooldown_seconds` | `>= 0` | `[server] admission_cooldown_seconds must be >= 0` |

`worker.toml` 에는 **아무것도 더하지 않는다** — 판정은 서버가 한다. 워커의 `[host] interval_seconds` 는
그 머신 표본의 신선도를 정하므로 그대로 쓰인다.

⚠️ 위 교차 검증은 **서버 자신의 `[host]`** 만 본다. 원격 표본의 history 길이는 그 워커의
`worker.toml` `[host] history_samples` 가 정하고, 그건 다른 함수에서 `>= 1` 로만 검사된다
(`config.py:911-912`). 워커가 `history_samples = 2` 인데 서버가 `admission_samples = 3` 이면 그 워커의
레인 ≥ 2 는 **영영 안 열리고** 서버는 이유를 `no_sample` 로만 본다(실측 §14-A5). `rcm check` 경고를
하나 더 둔다(§5.4) — 「워커 `<name>` 의 history_samples 가 admission_samples 보다 작다」.

### 4.2 왜 `load` 가 아니라 `cpu.busy` 인가

기획 초안의 `load_ratio`(코어 대비 load1) 대신 **`cpu.busy` 를 쓴다**.

- 오너가 말한 「80%」는 CPU 사용률이지 런큐 길이가 아니다. `cpu.busy` 는 두 OS 모두 **머신 전체의
  백분율**(`100 − idle`)이라 80 이 곧 80% 다.
- Linux 의 load 는 uninterruptible I/O 를 세고 macOS 는 다르게 센다 — 같은 숫자가 두 OS 에서 다른 뜻이다.
  **이 이유가 결정적이다.**
- 시간 해상도 차이는 초안이 과장했다: load1 은 60초 평균이고, 우리 창은 15초에 걸친 **1초 관측 3번**이다
  (§4.4). load1 보다 낫지만 「연속 15초 관측」은 아니다.

### 4.3 판정 — `core/admission.py` (순수 함수, I/O·시계 없음)

```python
@dataclass(frozen=True)
class AdmissionConfig:   # core/queue.QueueConfig 와 같은 방식 — config.py 를 import 하지 않는다
    policy: str = "load"
    cpu_max_percent: float = 80.0
    samples: int = 3
    cooldown_seconds: float = 30.0
    stale_seconds: float = 15.0     # 서버가 표본 종류에 맞춰 계산해 넣는다(§4.5)

def decide(*, lane, sample, now, last_admit_at, cfg) -> Hold | None
```

`now` 는 인자다. `sample` 은 `HostSample | None`(그 안에 `history`), `last_admit_at` 은 그 **머신**이
마지막으로 claim 한 시각. 반환은 `None`(열림) 또는 `Hold(code, detail)`.

순서대로:

1. `lane == 1` → **언제나 열림.** 머신마다 레인 하나는 게이트를 안 지난다. **쿨다운 판정은 건너뛰지만
   쿨다운 기록은 남긴다**(§4.5, 결정 41).
2. `cfg.policy == "always"` → 열림.
3. 표본이 없거나 `now − sample.sampled_at > cfg.stale_seconds` → `Hold("no_sample")`.
4. `sample.cpu` 가 `None` 이거나 `sample.cpu.get("busy")` 가 `None` → `Hold("no_sample")`.
   (표본은 cpu·memory·load 중 **하나만** 읽혀도 만들어진다 — `hostsample.py:245-248`. 가드가 보는 것은
   `memory` 전체가 아니라 **`memory.get("used_bytes")`** 다: `vm_stat` 이 실패해 `used_bytes` 가 None 인
   memory dict 는 「읽힌 것」으로 안 친다.)
5. `last_admit_at` 이 있고 `now − last_admit_at < cfg.cooldown_seconds` → `Hold("cooldown")`.
6. `history` 의 마지막 `cfg.samples` 개를 본다. 개수가 모자라거나, **어느 하나라도 `cpu_busy` 가 `None`
   이거나**(`hostsample.py:252-259` — `entry` 를 만들고 `:259` 에서 **조건 없이** append 한다), 항목 사이 간격이 끊겼으면
   → `Hold("no_sample")`.
   - **간격 판정은 항목끼리만 비교한다.** `history[].at` 은 표본을 만든 머신의 시계이고 `sampled_at` 은
     서버 시계다(`hostparse.py:360-366`) — 둘을 빼면 시계 차가 섞인다. 이웃한 `at` 차이가
     `2 × sample.interval_seconds` 를 넘으면 끊긴 것으로 본다. `at` 은 ISO 문자열이라
     `core/status.parse_iso`(`:41`)로 읽는다.
7. 그 표본 전부가 `cpu_busy <= cpu_max_percent` 가 **아니면** → `Hold("cpu_busy", detail=최신값)`.
8. 그 외 → 열림.

**fail-open 금지**: 3·4·6 은 전부 「모르면 닫는다」다. **모르는 채로 여는 분기는 하나도 없다.**

**`hold_max_seconds`(무한 보류 방지)는 두지 않는다.** 레인 1 이 게이트를 안 지나므로 큐는 계속 움직인다.
다만 「게이트가 제 일을 하는 중」과 「누가 두 시간째 이 머신을 잡고 있음」을 구분할 수단이 필요하므로
**밸브 대신 계기**를 단다 — `held_since`(잰 값) + `rcm check` 경고(§5.4). (결정 40)

### 4.4 기본값의 근거

| 값 | 왜 |
|---|---|
| `cpu_max_percent = 80` | 오너가 정했다. 20% 여유는 자재화(tar 풀기 · git fetch)와 서버 자신의 몫이다 |
| `admission_samples = 3` | 15초에 걸친 **1초 관측 3번**. macOS 는 `top -l 2 -n 0 -s 1` 의 둘째 표본(1초 구간, `hostsample.py:47`), Linux 는 1초 `/proc/stat` 차분(`:203-206`) — 창의 나머지 12초는 못 본다. 한 번 꺼진 표본에 속아 여는 것을 막는 값이지 「연속 15초 여유」가 아니다 |
| `admission_cooldown_seconds = 30` | 잡의 CPU 부하가 표본에 나타나려면 최소 한 주기가 필요하다. 15초의 두 배로 잡아 **한 머신에서 30초에 하나**로 묶는다(§4.5 의 락이 있어야 실제로 그렇게 된다) |

여파: 한가한 머신에서 `lanes = 4` 를 다 채우는 데 약 90초. **서버를 새로 띄운 직후 15초 동안은 레인 ≥ 2 가
표본이 모자라 닫혀 있다**(설계상 fail-closed) — 문서에 적는다. 짧은 잡이 많으면
`admission_cooldown_seconds = 10` 으로 내린다(그러면 `[host] interval_seconds` 도 같이 봐야 한다 — §4.1 의
교차 검증).

### 4.5 서버 배선

**머신 키.** `_last_admit`·`_hold` 는 **워커 등록 단위**로 센다 — 로컬은 `None`, 원격은 워커 이름.
`WorkerRow.host_name` 은 워커의 **표시 이름**(`remote_worker.py:184` 의 `config.name or hostname`)이라
코로케이션을 자동으로 알아낼 수 없다. 그러므로 자동 병합을 **하지 않고 문서로 못 박는다**:

> 한 머신에서 `rcm serve` 와 `rcm worker` 를 같이 돌리면 그 머신에는 **게이트를 안 지나는 레인이 둘**
> 생기고 쿨다운도 따로 센다. 이 저장소 오너의 Mac 이 정확히 그 배치다(서버 `lanes = 1` pool `default`,
> 워커 `lanes = 1` pool `mac2`). 보류 판정 자체는 둘 다 같은 Mac 의 CPU 를 보므로 맞게 동작하고, 어긋나는
> 것은 「30초에 하나」가 「30초에 둘」이 되는 것뿐이다. 합치려면 `/worker/register` 에 머신 식별자를
> 더해야 한다 — 결정 46 으로 남긴다.

**원자성.** `App` 에 `_admit_lock: threading.Lock` **하나(전역)**. **게이트를 지나는 레인(≥ 2)만** 잡고,
`decide → store.claim → 쿨다운 기록` 을 락 안에서 한다. 그래야 「머신당 30초에 하나」가 참이 된다.

실측(§15-C2)으로 **전역 락 하나가 머신별 락보다 낫다**: 게이트 레인 9개에서 대기 p50 0.000 ms ·
p99 0.001 ms, 8배인 72개에서도 p99 0.106 ms 다. 머신별로 쪼개면 burst 에서 9레인 3.6배 · 36레인
**22배 더 느리다** — 전역 락이 `BEGIN IMMEDIATE` 를 줄 세우는 유일한 장치라, 없애면 스레드들이 SQLite
writer 락으로 몰려 1/2/5/10/25/50/100 ms 백오프에 걸린다(로그 폭주 중 `database is locked` 가 전역 0건 vs
머신별 1·10건).
레인 1 은 락을 안 잡고(기다리지 않는다) claim 뒤 `_last_admit[machine] = now` 만 기록한다(dict 대입은
GIL 아래 원자적). 큐가 비어 claim 이 `None` 이면 기록하지 않는다 — 헛돈 것은 쿨다운을 쓰지 않는다.

**한 번만 판정한다.** `_hold: dict[(machine, lane), tuple[Hold | None, datetime]]` 에 claim 경로가 쓴 결과를
남기고, 상태 경로는 **다시 판정하지 않고 그것을 읽는다**(기록이 claim 주기보다 오래됐으면 fail-closed).
두 번 부르면 화면과 실제가 어긋나고, `Worker._set` 이 상태가 바뀔 때마다 `_since` 를 되감아
(`worker.py:222-228`) `not_scheduled` 알람이 죽는다.

**표본 읽기.** 로컬은 `App._hosts()`(`server.py:631-639`) — `App.sampler` 는 `start()` 전엔 `None` 이고
`latest()` 는 `(list, error)` 를 준다. 원격은 `_worker_samples[worker]`.

**`stale_seconds`.** 로컬은 `3 × [host] interval_seconds`. 원격 표본은 서버가 받은 시각으로 다시 찍히므로
(`remote_workers.py:520`) 나이가 곧 **마지막 heartbeat 이후 시간**이고, 예산은 `3 × interval = 15초` 뿐이다.
실측(§14-A4): heartbeat 이 두 번 밀리면 나이가 **정확히 15.0초로 경계에 딱 걸려 아직 fresh 다**
(`stale` 은 `age > 3 × interval`, 등호 제외). 거기서 조금이라도 늦거나 세 번 밀리면(20초) 닫힌다.

⚠️ 초안이 제안한 `max(3 × sample.interval_seconds, 3 × worker_heartbeat_seconds)` 는 **기본값에서 아무 일도
안 한다** — 둘 다 5초라 `max(15, 15) = 15` 로 로컬과 같다. 워커의 `[host] interval_seconds` 가 heartbeat
주기보다 **짧을 때만**(검증 하한 2초) 하한을 올려 준다. 예산을 진짜로 늘리려면 배수를 3 이 아닌 값으로
두거나 `stale_seconds` 를 설정 키로 빼야 한다 — **결정 47** 로 오너에게 묻는다. 그 전까지는 기본 15초를
쓰고, 한가한 머신의 레인이 heartbeat 지연으로 닫히는 것은 fail-closed 로 받아들인다.

**로컬 레인.** `Worker.__init__` 에 `admit: Callable[[int], Hold | None] | None` 을 더하고
**`start_workers()`(`worker.py:427-452`)에도 같이 뚫는다** — `App` 이 레인을 만드는 유일한 곳이
`server.py:225` 다. `run` 루프의 `paused` 검사 **다음**에 부르고, 보류면 `held` 로 두고 대기한다.

**원격 레인.** `worker_claim` 에서 **`_claim_slots.acquire` 보다 먼저** 판정한다(`remote_workers.py:324`).
보류면 **바로 204**, 슬롯을 안 잡는다. 루프 안 재확인은 들어올 때 열려 있던 레인에만 한다.

실측(§15-C3): long-poll 레인 **12개면 열린 레인이 굶는다** — claim 까지 0.003초 → **0.394초**(130배).
슬롯은 정확히 8개에서 포화한다. 이 고침으로 p50 이 0.004초로 돌아오고 보류 레인 128개까지 버틴다.

⚠️ **하지만 이 고침이 새 문제를 만든다.** 보류 레인은 이제 전부 「슬롯 없는 1 Hz 폴러」가 되는데,
`CLAIM_MIN_INTERVAL = 1.0`(`remote_worker.py:314-315`)에 **지터가 없어** 레인들이 같은 1초 격자에 묶여
영영 안 흩어진다. 실측(§15-C4): 같은 격자에 **32레인이면 요청 세마포어(`max_concurrent_requests = 32`)가
정확히 가득 차고**, 48레인에서 첫 `/api/status` 503 이 나며 워커 claim 46건이 거절된다. 128레인이면
406건이다. 흩어져 있으면 같은 128레인이 최대 12개만 쓰고 503 이 0건이다.
**M5f 는 이 lockstep 을 정상 상태로 만든다** — 보류 레인이 곧 슬롯 없는 레인이니까. 그래서
`CLAIM_MIN_INTERVAL` 에 0~1초 지터를 넣는 것이 **PR 2a 의 일부**다(503 406건 → 36건, 91% 감소).

**시작 로그.** 레인 ≥ 2 인데 `admission = "load"` 면 기동 배너 옆(`server.py:1997`)에 한 줄:
`admission: load (cpu <= 80%, lanes >= 2)`. 안 그러면 첫 증상이 「두 번째 레인이 갑자기 멈췄다」다.

## 5. 보이게 하기 (결정 37 — 서버는 문장이 아니라 코드를 보낸다)

### 5.1 워커 상태

`core/model.py` 에 `WORKER_HELD = "held"`, `WorkerInfo` 에 `hold_code: str | None`
(`cpu_busy` · `no_sample` · `cooldown`) · `hold_detail: dict | None`(예 `{"cpu_busy": 92.4}`) ·
`held_since: datetime | None`. `state` 에 **값이 하나 느는 것**이지 키가 바뀌는 게 아니다 —
`schema_version` 그대로.

⚠️ 키를 더하면 **테스트 셋이 빨개진다**(실측 §14-A3): `tests/test_status_schema.py:224`(키 집합 완전
일치) · `tests/test_server.py:400`(`assert doc["server"]["workers"] == [{…}]`, dict **전체** 비교) ·
`tests/test_worker_api.py:678`(`assert lane == {…}`, 원격 레인 dict 전체 비교). §8 의 「추가」가 아니라
기존 단언의 수정이다.

### 5.2 큐 사유 `held_by_load`

`core/queue.py` 에서:

- `open = [w for w in live if w.state != WORKER_HELD]`. `live`(= `down` 아님)는 `worker_down` 판정에
  그대로 쓰고, **ETA 그리디와 `can_start` 는 `open` 으로** 바꾼다.
- `idle_since` 는 **§5.3 의 키 고침 뒤에는** 손댈 필요가 없다 — `core/queue.py:239-240` 이 이미
  `state == WORKER_IDLE` 인 레인만 넣으므로 `held` 는 저절로 빠진다. **키를 고치기 전에는 `lane_free` 와
  똑같이 뭉개진다**: 실측(§14-B2)에서 진짜 idle 레인 3개가 `idle_since` 키 2개로 줄었고, held 레인을
  빼도 사유가 하나도 안 바뀌었다(세 번째 대기 잡이 `not_scheduled` 여야 하는데 `waiting_for_lane` 이었다).
- 사유 순서(대기 잡):

  ```
  uploading / upload_stalled → paused → live 없음: worker_down → blocked_by_group
    → idle·open 레인이 10초 넘게 놀았다: not_scheduled
    → held 레인이 있다: held_by_load          ← 새로 추가
    → waiting_for_lane
  ```

  「너를 집었을 레인이 부하로 막혀 있다」는 뜻이므로, 오늘의 `idle_since` 처리와 같은 방식으로 **대기 잡
  하나가 보류 레인 하나를 소비**한다. 뒤 잡은 정직하게 `waiting_for_lane` 이다.
- **`open` 이 비면 ETA 는 null 이다**(시작할 수 없는 잡에 시각을 주지 않는다 — PLAN 「큐 규칙」).
  `live` 는 안 비었는데 `open` 이 빈 경우가 새로 생긴다: **레인 1 이 down 이고 레인 2 가 held**. 그러면
  사유는 `held_by_load` 이고 ETA 는 `—` 인데, 진짜 원인인 죽은 레인 1 은 `workers[]` 의 `down` 필과
  머리줄 `DOWN: lane 1` 에만 보인다. 이 조합을 **테스트로 못 박아 의도된 동작으로 남긴다**(§8).
- **`ACTIONABLE_REASONS` 에는 넣지 않는다**(결정 45 를 뒤집었다 — §13-H). 대신 `web/app.js:388` 의
  「정상 대기」 목록에 넣어 「Not moving」이 **정직하게 조용하도록** 한다. 안 그러면 그 목록에도 없고
  `ACTIONABLE` 에도 없어서 `notMoving()` 이 그냥 버리고(`web/app.js:389`) 잡이 보류 중인데 화면은
  「막힌 것 없음」이라고 말한다.

### 5.3 ETA — 먼저 레인 키를 고쳐야 한다

⚠️ **기존 버그**: `lane_free`·`lane_last_job`·`idle_since` 가 `dict[int]`(레인 **번호**)로 키를 잡고
(`core/queue.py:235-237`) 잡 귀속도 `job.lane` 만 본다(`:249-252`). 그런데 기본 풀에는 로컬 레인 1..N 과
**모든 원격 `default` 워커의 레인 1..M** 이 함께 들어오고(`server.py:348-352`), `rcm worker` 의 `pool`
기본값이 `default`(`config.py:209`)다. 그래서 로컬 레인 2 와 `build-02/2` 가 같은 키로 뭉개진다 — 오늘도
용량을 적게 세고 있고, 이걸 안 고치면 **보류 레인을 빼는 §5.2 가 아무 효과가 없다.**

고침: 세 dict 를 `(w.worker, w.lane)` 로 키를 잡고 잡 귀속을 `(job.worker_name, job.lane)` 로 한다.
`Job.worker_name` 은 이미 있다(`core/model.py:226`). ETA 숫자가 바뀌므로 **자기 테스트와 CHANGELOG 한 줄**을
붙인다.

그 위에서 `open` 레인만 그리디에 넣는다. 보류 레인이 언제 열릴지는 모르는 값이라, 열릴 것처럼 계산하면
PLAN 이 금지한 「자신있는 틀린 시각」이 된다. 빼면 시각이 늦게 나오고 레인이 열리면 앞당겨진다 — 늦는
쪽보다 낫다. (결정 43)

### 5.4 `rcm top` · `rcm check` · 웹

- `rcm top` 머리줄(`core/render_text.py:256~`): `lanes 1/2 busy · 1 held (cpu 92%)`.
  ⚠️ 오늘은 `lanes >= 2` 면 `busy` 수와 `down` 목록만 찍으므로(`:260`·`:261`·`:267`·`:268`) **보류 레인이
  아예 안 보인다** — 세는 자리를 새로 넣어야 한다. 레인 1 이면 오늘처럼 필 하나로 접는다(결정 12).
- 원격 필(`:281`)은 상태 문자열을 그대로 찍으므로 `build-02/2 held` 가 저절로 나온다.
- 큐 행 사유(`:123~`): `held by load · cpu 92%`.
- `rcm check` pools 행(`cli.py:879`): `default (2 lanes · 1 held) · linux (build-02/1 idle)`. **FAIL 이
  아니다.** 여기에 경고 두 가지를 더한다(`ok=None` = warn, FAIL 아님):
  - 어떤 레인이 `10 × admission_cooldown_seconds` 넘게 held → 「게이트가 오래 닫혀 있다」.
  - 레인 ≥ 2 인 등록 워커의 `version` 이 서버보다 낮음 → §3.1 의 구멍이 남아 있다.
- SSE: `_publish_server`(`server.py:405-410`)가 `{lane, state, job_id, worker}` 만 보낸다. `hold_code` 를
  같이 실어야 필이 다음 전체 폴링(10초)까지 이유 없는 `held` 로 남지 않는다.
- 웹: `i18n.js` 두 언어에 `state.held` · `reason.held_by_load` · `hold.cpu_busy`/`hold.no_sample`/
  `hold.cooldown`. `app.js` 는 `reasonText` 분기 + `:388` 정상 대기 목록. `workerState()`(`:510-514`)가
  `I18N.has` 로 막고 있어 옛 페이지도 안 깨진다.

한국어 문구(초안): 「부하로 대기」 · 「CPU 가 바빠서 안 집는 중 (92%)」 · 「표본 없음 — 안전하게 멈춤」.

## 6. ETA·중앙값과 동시 실행

초안은 「신뢰도만 한 칸 내리고 DB 는 안 건드린다」였다. **그건 문제의 겉만 고친 것이다** — `expected` 는
`overdue = elapsed > expected` 와 `stuck = elapsed > 3 × expected` 를 함께 낳고(`core/queue.py:178-184`)
둘 다 `finish_at` 을 null 로 만든다. 혼자 잰 중앙값으로 둘이 나눠 쓰면 **매 실행이 일찍 `overdue` 가 되고
ETA 가 사라진다.** 배지 한 칸보다 큰 회귀다. 그리고 혼자 실행·같이 실행이 섞인 중앙값은 쌍봉이라, 섞임
비율이 50% 를 넘는 순간 값이 껑충 뛴다 — 꾸준한 편향보다 나쁘다.

그래서 **계층 대체(hierarchical fallback)** 로 간다. 표본을 잃지 않는다:

```
expected = 같이-실행 중앙값(sample_count >= min_samples 일 때)
         → 아니면 전체 중앙값(오늘 그대로)
         → 아니면 preset.expected_seconds → default_seconds
```

이걸 하려면 **끝난 잡이 몇 개와 함께 돌았는지**를 알아야 한다. 초안은 「`events` 로 사후 복원하면 되니
마이그레이션이 필요 없다」고 했지만, 45일치 중앙값을 낼 때마다 이벤트를 훑는 것은 비싸고 깨지기 쉽다.

- **PR 2a**: `jobs.concurrent_at_start INTEGER`(**DB v7**)를 더하고 `store.claim` 의 **같은 트랜잭션**에서
  쓴다(그 풀의 busy 잡 수 + 1). 15줄이고, **지금 안 모으면 나중에 소급해서 못 얻는다.**
- **PR 2c**(표본이 쌓인 뒤): 위 계층 대체 + `Estimate` 에 `shared: bool` **새 필드**를 실어 신뢰도까지
  잇는다. ⚠️ `confidence()` 는 `compute_queue` 가 부르는 게 아니라 **렌더 시점에 세 곳에서 따로**
  계산된다(`core/status.py:176` · `render_text.py:193` · `web/app.js:263`) — 셋을 같이 고치지 않으면
  `rcm top` 과 웹이 서로 다른 배지를 찍는다. `low` 는 더 내려갈 곳이 없으므로 `low` 로 둔다.
- **그 사이(PR 2a·2b)**: 배수를 지어내지 않는다. 「레인을 올리면 같이 도는 동안 `overdue` 가 일찍 뜬다」를
  문서에 적고, PR 2c 의 완료 기준으로 넘긴다.

## 7. 프리셋 무게(`heavy = true`)는 만들지 않는다

초안의 논거(「`concurrency_group` 이 이미 말한다」)는 **틀렸다**. 그룹은 라벨로 거는 상호 배제라, 무거운
프리셋 둘에 같은 그룹을 주면 **각 프리셋이 자기 자신과도 직렬화된다**(`build` 잡 둘을 나란히 못 돌린다 —
M3 e2e 가 잠가 놓았다). 그룹은 「무겁지만 자기들끼리는 병행」을 표현할 수 없다.

그래도 결론은 같다, 논거만 바꾼다: **게이트는 재고, 무게는 짐작한다.** 재는 쪽이 있는데 짐작하는 장치를
같이 두지 않는다. 게이트가 못 보는 유일한 구간(이제 막 시작해 아직 CPU 에 안 나타난 잡)은 무게가 아니라
`admission_cooldown_seconds` 가 막는 자리이고, **§4.5 의 락이 들어와야 그 말이 참이 된다.** (결정 44)

## 8. 테스트 배치

**새로 쓰는 것**

| 파일 | 무엇 |
|---|---|
| `tests/test_admission.py` | 순수 판정 전수: 표본 없음 · stale 경계 · `sample.cpu is None` · `history[].cpu_busy is None` · **정확히 80.0 은 열림, 80.1 은 hold**(경계) · history 개수 부족 · **항목 간격이 끊긴 창**(오래된 2 + 새것 1 → hold) · 쿨다운 29/31초 · `policy = "always"` 는 전부 열림 · **`lane == 1` 은 전부 열림** |
| `tests/test_server_m5f.py` | 원격 claim 보류 시 204 이고 **슬롯을 안 잡는다** · 쿨다운은 잡을 실제로 집었을 때만 찍힌다 · **레인 4개가 동시에 깨도 게이트를 지나는 admission 은 쿨다운 창당 하나**(락) · 레인 1 은 락을 안 기다린다 · `_hold` 를 상태 경로가 재판정 없이 읽는다 |
| `tests/test_e2e_m5f.py` | `lanes = 2`, `srv.app.sampler = StubSampler([...])`(`tests/test_server_m1.py:208` 방식). CPU 95 를 먹이면 잡 둘 중 **하나만** 돈다 → CPU 10 + 쿨다운 경과 → 두 번째가 뜬다 |
| `tests/test_render_m5f.py` | `rcm top` 머리줄이 `lanes >= 2` 에서 held 를 **센다** · 큐 행 `held by load · cpu 92%` · `rcm check` 가 warn 이고 FAIL 이 아님 · 오래 held → 경고 · 옛 워커 버전 → 경고 |
| `tests/web/admission.test.js` | `reasonText("held_by_load")` 두 언어 · 워커 필 `held` · `notMoving()` 이 held 를 **정상**으로 보고 조용함 · `hold_code` 없는 옛 문서에서 안 깨짐 |
| `tests/test_docs_m5f.py` | `examples/server.toml` 키 4줄 · `docs/configuration.md` 절 · `usage.md`/`usage.ko.md` · CHANGELOG `[Unreleased]` |

**고쳐야 하는 기존 테스트** (완료 기준 5 가 「전부 초록」이라고 쓰면 거짓말이 된다)

| 파일 | 왜 |
|---|---|
| `tests/test_e2e_m3.py:84` | `lanes=2` 로 실제 `App` 을 띄우고 레인 2 가 `solo` 를 집는 것을 20초 안에 단언한다(`:99`). 기본 `admission = "load"` 면 표본이 없어 못 집는다 → 그 테스트에 `admission = "always"` 를 준다(그 테스트의 주제는 **그룹 직렬화**지 admission 이 아니다) |
| `tests/test_status_schema.py:224` | 워커 객체 키 집합 완전 일치 — `hold_code`·`hold_detail`·`held_since` 를 더한다 |
| `tests/test_server.py:400` | `assert doc["server"]["workers"] == [{…}]` — 워커 dict **전체** 비교. 같은 키에 걸린다 |
| `tests/test_worker_api.py:678` | `assert lane == {…}` — 원격 레인 dict 전체 비교. 같은 이유 |
| `tests/test_queue.py` | 레인 키를 `(worker, lane)` 로 바꾸는 §5.3 이 ETA 숫자를 바꾼다. 추가: held 는 그리디에서 빠진다 · held 는 `not_scheduled` 를 안 울린다 · 대기 잡 1은 `held_by_load`, 2는 `waiting_for_lane` · 그룹이 이긴다 · **레인 1 down + 레인 2 held → `held_by_load` + ETA null** |
| `tests/test_config.py` | 새 키 4개의 기본값 · 범위 밖 · 교차 검증 두 가지 |
| `tests/test_worker.py` | 보류된 로컬 레인은 `store.claim` 을 **안 부른다**(가짜 admit) · 상태가 `held` · 열리면 집는다 |
| `tests/test_store.py` | `concurrent_at_start` 가 claim 트랜잭션에서 쓰인다 · DB v7 마이그레이션 |
| `AGENTS.md:40` · `scripts/mutcheck.py` docstring | 낡은 「8 known mutations」·「변이 6종」 |

규칙은 오늘과 같다: 테스트를 먼저 써서 **빨간 것을 보고** 구현한다. 실제 부하를 만들지 않는다(표본 주입).

## 9. mutcheck (2종 추가 — **10 → 12**)

기존 10종은 `scripts/mutcheck.py:45-127` 에 있고 ⑦⑧은 이미 `retention-active-guard`·`gitref-leading-dash`
가 쓰고 있다(`docs/m3-test-scenarios-a.md:63`). 새 것은 **⑪ ⑫** 다.

| 이름 | 파일 | 변이 | 무엇을 지키나 |
|---|---|---|---|
| ⑪ `admission-fail-open` | `core/admission.py` | 「표본 없음 → hold」를 「→ 열림」으로 | **fail-open 금지.** 이 기능의 안전 성질 그 자체 |
| ⑫ `admission-samples-any` | `core/admission.py` | `all(...)` → `any(...)` | 한 번 꺼진 표본에 속아 여는 버그 |

두 변이로 `pytest` 가 **실제로 빨개지는 것**을 확인한 뒤에만 「검증됨」이라고 쓴다.

## 10. PR 순서 · 완료 기준

- **PR 1(이 문서)**: `docs/m5f-workplan.md` + `docs/reviews/2026-09-08-m5f-design-review.md` +
  `PLAN.md`(마일스톤 M5f · 설정 키 · reason·워커 상태 목록 · 결정 39~46). 코드 없음.
- **PR 2a-0(선행 병목)**: 게이트를 다는 자리 자체가 느리면 게이트가 아니라 그게 병목이 된다(§15).
  ① `store.add_markers()` 배치(마커 줄마다 트랜잭션 하나 → 한 번) ② janitor 주기 sweep 에 `ANALYZE`
  ③ `_try_claim` 이 `sqlite3.OperationalError` 를 500 이 아니라 503 으로 ④ `CLAIM_MIN_INTERVAL` 지터
  ⑤ §5.3 의 레인 키 버그. 넷 다 M5f 없이도 옳고, M5f 가 있으면 필수다.
- **PR 2a**: `core/admission.py` + 설정 키·검증 + 서버 배선(락 · `_hold` ·
  슬롯 순서 · 시작 로그) + `remote_worker._host_sample` 의 stale 구멍(§3.1) + `core/queue.py` 사유·ETA +
  상태 JSON + SSE + `jobs.concurrent_at_start`(DB v7). 테스트-퍼스트.
- **PR 2b**: `rcm top`/`check`(경고 둘 포함) · 웹 두 언어 · `examples/server.toml` ·
  `docs/configuration.md`(코로케이션 규칙 · 기동 직후 15초 · 기본값 근거) · `usage.md`/`usage.ko.md` ·
  CHANGELOG(**레인 ≥ 2 동작 변경**으로 표시) · mutcheck ⑪⑫ · `AGENTS.md:40` 갱신. `docs` 스킬 체크리스트대로.
- **PR 2c**(표본이 쌓인 뒤): §6 의 계층 대체 + `Estimate.shared` + 신뢰도 세 곳.

**완료 기준**

1. `lanes = 1` **이고 등록된 워커가 전부 레인 1 일 때** 동작·화면이 오늘과 같다. (`[server] lanes` 는
   로컬 레인만 센다 — 원격 레인은 `worker.toml` 이 정한다.)
2. `lanes = 2` · CPU 95% 표본 → 두 번째 레인이 `held(cpu_busy)`, 대기 잡 사유 `held_by_load`,
   `rcm top`·웹 두 언어에 보인다. CPU 가 내려가고 쿨다운이 지나면 잡이 뜬다(e2e).
3. 표본이 없거나 낡거나 **창이 끊겨 있으면** 레인 ≥ 2 는 닫힌다. 워커의 샘플러만 죽여도 닫힌다 —
   **단 워커도 새 버전일 때만**(옛 워커는 `rcm check` 경고로 드러난다).
4. **레인 4개가 동시에 깨어도 게이트를 지나는 admission 은 쿨다운 창당 하나**다(락). 락이 없으면
   실측에서 **20회 중 20회 전부** 세 레인이 다 통과했다(§14-A1) — 이건 가끔 나는 경합이 아니라
   `admission_cooldown_seconds` 를 의미 있게 만드는 유일한 장치다.
5. `admission = "always"` 로 오늘의 동작이 그대로 나온다.
6. `/worker/*` 프로토콜과 `schema_version` 이 그대로다. 기존 테스트는 §8 의 표에 적은 것만 바뀌고
   **나머지는 전부 초록**이다.
7. mutcheck **12종**이 전부 빨개진다. `ruff` · `pytest` · `node --test` · `smoke_install.sh` 통과.
8. 실기(오너): 이 Mac 에서 `lanes = 2` 로 무거운 잡 둘을 던져 두 번째가 보류됐다가 도는 것을 화면으로 본다.

## 11. 오너 결정 (기본값으로 구현하고 확인 대기)

| # | 결정 | 기본값(⛔ 이대로 구현) |
|---|---|---|
| 39 | CPU 상한과 기본 동작 | `cpu_max_percent = 80`, `admission = "load"` 를 **기본으로 켠다**. 영향 범위는 「`lanes = 1`」이 아니라 **「서버와 등록된 모든 워커가 각각 레인 1」**이다. 레인 ≥ 2 인 설치는 업그레이드만으로 동작이 바뀌므로 CHANGELOG 에 동작 변경으로 적고 기동 로그 한 줄을 남긴다. **끄려면 `admission = "always"`** |
| 40 | 레인 1 은 게이트를 안 지난다 · `hold_max_seconds` 없음 | 대신 **계기**를 단다 — `held_since` 와 `rcm check` 경고. 밸브는 「CPU 95% 인데 두 번째 잡을 밀어 넣는」 장치라 두지 않는다 |
| 41 | 레인 1 의 claim 도 쿨다운을 **기록**한다 | 판정은 건너뛰되 기록은 남긴다. 안 그러면 레인 1 이 무거운 잡을 집은 0.5초 뒤 레인 2 가 「잡 시작 전」 표본을 보고 통과한다 |
| 42 | 메모리 게이트는 **넣지 않는다** | 두 OS 의 `used` 정의가 달라(macOS 는 inactive/파일 캐시 제외, Linux 는 `total − MemAvailable`) 같은 10% 가 다른 뜻이다 — 같은 픽스처에서 macOS 는 free 31.9%, Linux 는 60.4%(같은 파일의 `MemFree` 로 재면 14.8%)로 나온다(§14-B5). 스왑 압력 신호도 없다(`grep -riE '\bswap' src/` → 0건). 다시 볼 때의 신호는 macOS `memory.compressed_bytes`. ⚠️ `memory.free_bytes` 는 원격 표본 화이트리스트(`core/hostparse.py:317`)에만 있고 **어떤 수집기도 안 낸다** — grep 하면 헛다리다. `free_bytes` 를 진짜로 내는 건 `disk` 다 |
| 43 | ETA 는 보류 레인을 뺀다 | 늦게 잡고, 레인이 열리면 앞당겨진다. 전제로 §5.3 의 레인 키 버그를 먼저 고친다 |
| 44 | 프리셋 무게(`heavy`)는 안 만든다 | 게이트는 재고 무게는 짐작한다. (초안의 「그룹이 이미 말한다」 논거는 폐기 — 그룹은 자기 자신과도 직렬화한다) |
| 45 | `held_by_load` 는 「Not moving」에 **안 올린다** | 의도된·자가 치유되는 상태라 `paused` 와 같은 종류다. 늘 켜져 있으면 사람들이 패널을 무시하게 되고 `worker_down`·`stuck` 이 묻힌다. 대신 행 사유로 보이고, 오래 닫히면 `rcm check` 가 경고한다 |
| 46 | 코로케이션은 자동 병합하지 않는다 | 한 머신의 `rcm serve` + `rcm worker` 는 게이트 없는 레인 **둘**을 갖는다. 합치려면 `/worker/register` 에 머신 식별자를 더해야 한다 — 필요해지면 그때 |
| 47 | 원격 표본의 낡음 예산 | 기본 15초(`3 × interval`) 그대로 둔다. heartbeat 이 두 번 밀리면 경계에 걸리고 세 번이면 닫힌다 — 한가한 머신인데도 닫히는 게 거슬리면 `stale_seconds` 를 설정 키로 뺀다. 지금은 fail-closed 를 택했다 |
| 48 | 선행 병목을 M5f 안에서 고친다 | PR 2a-0 의 다섯 가지. 근거는 §15 의 실측 — `ANALYZE` 하나로 claim 이 16.5 ms → 0.004 ms(3727배), 마커 배치로 로그 폭주 중 claim 이 275.9 ms → 0.03 ms |
| 49 | M5f **밖**의 병목은 별도 PR | `/api/status` 의 `list_samples`(요청의 91~93%, 1만 행 245 ms) · `remote_worker_infos` N+1(마커 줄당 SQL 555개) · `workers` 표 미정리 · `PRAGMA synchronous=NORMAL`(쓰기 5.6배, WAL 에서 표준·안전하지만 내구성 변경이라 오너가 정한다). 전부 M5f 전에도 후에도 참인 문제다 |
| 50 | 같은 레포의 병렬 레인은 직렬화된다 — 고치지 않고 적는다 | `gitops.py` 의 미러 락은 공유 객체를 지키는 것이라 함부로 풀 수 없다. 「같은 레포를 쓰는 프리셋은 레인을 늘려도 자재화가 겹치지 않는다」를 `docs/configuration.md` 에 적는다 |

## 12. 위험

| 위험 | 대응 |
|---|---|
| 표본이 늦어 잡을 여러 개 들여보낸다 | §4.5 의 **락**이 한 머신의 게이트 통과를 쿨다운 창당 하나로 묶는다. 도는 잡은 절대 죽이지 않는다 — 게이트는 **입장**만 통제한다 |
| 상한 근처에서 열렸다 닫혔다 한다 | 연속 `admission_samples` 요구가 진동을 먹는다. 필이 깜박이는 정도는 남고, 그건 사실 그대로다. 다만 그 사이 `not_scheduled` 알람이 약해진다(§4.5) |
| 다른 사람이 CPU 를 먹어 레인이 안 열린다 | 의도한 동작. 화면이 `held (cpu 92%)` 로 이유를 말하고, 오래가면 `rcm check` 가 경고한다. 급하면 `admission = "always"` |
| 옛 CLI 가 held 를 못 그린다 | 깨지지는 않지만 `lanes >= 2` 머리줄에서 **로컬 held 레인이 아예 안 보인다**(`render_text.py:267`). 그래서 §5.4 에서 세는 자리를 새로 넣는다. 원격 필과 웹은 그대로 안전하다 |
| 레인을 올린 뒤 `overdue` 가 자주 뜬다 | 혼자 잰 중앙값 탓이다. PR 2c 의 계층 대체가 고친다. 그 전까지는 문서로 알린다 |
| 기동 직후 15초는 레인 ≥ 2 가 닫혀 있다 | fail-closed 설계 그대로. 문서에 적는다 |
| **같은 레포의 병렬 레인이 git 미러 락에서 직렬화된다** | `gitops.py:158`·`:210` 이 미러마다 `threading.Lock` 을 **`git` 서브프로세스를 안고** 잡는다(상한 `git_fetch_timeout_seconds = 600`). 실측(§15-C5): 같은 미러 4레인은 직렬(21 ms → 86 ms), 다른 미러 4레인은 병행. 게이트 잘못이 아닌데 「레인 2인데 안 빨라졌다」로 오해되기 딱 좋다 → 문서에 적는다(결정 50) |
| 레인을 늘리면 claim 경합이 는다 | 오늘의 claim 은 20만 행에서 16.5 ms 이고 로그 폭주 중에는 275 ms 다 — 게이트를 그 앞에 다는 것은 병목 위에 문을 다는 것이다. 그래서 §15 의 선행 작업을 PR 2a-0 으로 먼저 한다 |

## 13. 리뷰 반영 (`docs/reviews/2026-09-08-m5f-design-review.md`)

Codex 는 사용 한도에 걸려 findings 를 못 냈고, 격리 에이전트 둘이 대신 봤다. 초안에서 **바뀐 것**:

- **A. 쿨다운이 정작 막아야 할 버스트를 못 막았다.** 판정과 기록 사이에 아무것도 없어 레인 4개가 동시에
  통과했다 → §4.5 의 락. 완료 기준 4 로 못 박았다.
- **B. 레인 1 이 쿨다운을 기록하는지 안 하는지가 비어 있었다** → 결정 41(기록한다).
- **C. 「머신」이 실은 「워커 등록」이었다.** 이 저장소 오너의 Mac 이 서버 + `mac2` 워커를 같이 돌린다
  → §4.5 의 문서화 + 결정 46.
- **D. `can_start` 를 `open` 으로 바꾸면 새 null-ETA 경우가 생긴다**(레인 1 down + 레인 2 held) → §5.2 에
  명시하고 테스트로 못 박았다.
- **E. ETA 그리디가 레인 *번호* 로 키를 잡아 보류 레인을 빼도 소용없었다** → §5.3 을 선행 작업으로.
- **F. 메모리 게이트를 뺐다.** 두 OS 의 `used` 가 다른 뜻이고 스왑 신호가 없다 — §4.2 가 load1 에 걸었던
  바로 그 반론이 자기에게 돌아왔다 → 결정 42.
- **G. `admission_samples` 가 시간이 아니라 항목 수였다** → §4.3 의 창 연속성 규칙(항목끼리만 비교).
  `history[].cpu_busy` 가 `None` 일 수 있는 것도 같이.
- **H. `held_by_load` 를 「Not moving」에서 뺐다** → 결정 45 를 뒤집었다. 대신 `web/app.js:388` 의 정상
  대기 목록에 넣어 패널이 정직하게 조용하도록.
- **I. §6 이 문제의 겉만 고쳤다** — `overdue`·`stuck` 이 같은 중앙값에서 나온다 → 계층 대체 + DB v7 열 +
  PR 2c. 「마이그레이션 불필요」를 철회했다.
- **J. §7 의 논거가 틀렸다**(그룹은 자기 자신과도 직렬화한다) → 결론은 유지, 논거 교체.
- **K. 기존 테스트가 실제로 깨진다** — `tests/test_e2e_m3.py:84`(기본 게이트가 켜지면 레인 2 가 못 집는다)
  와 워커 dict 를 비교하는 **셋**(`tests/test_status_schema.py:224` · `tests/test_server.py:400` ·
  `tests/test_worker_api.py:678`) → §8 에 「고쳐야 하는 기존 테스트」 표를 만들고 완료 기준 6 을 고쳤다.
  넷 다 §14 에서 실제로 빨개지는 것을 확인했다.
- **L. mutcheck 은 이미 10종이었다**(⑦⑧은 사용 중) → 10 → 12, ⑪⑫. `AGENTS.md:40` 도 낡았다.
- **M. 잔가지**: `App._hosts()`(샘플러는 `start()` 전엔 None) · `start_workers()` 시그니처 · 원격 표본의
  `stale` 상한을 heartbeat 까지 보게 · long-poll 슬롯보다 먼저 판정 · SSE 에 `hold_code` ·
  `admission_samples ≤ history_samples` 검증 · `AdmissionConfig` 는 `config.py` 를 import 하지 않는다 ·
  `idle_since` 는 손댈 필요 없다(이미 idle 만 넣는다) · `remote_workers.py` 인용을 `:520`/`:525` 로.

## 14. 실측 (프로브, 2026-09-08)

명세는 코드가 아니라 주장이다. 그래서 무게가 실린 주장마다 **실행 가능한 프로브**를 만들어 격리 에이전트
둘에게 돌리게 했다. 프로브는 전부 스크래치패드에 있고 저장소는 건드리지 않았다 — 끝나고
`git status` 가 이 문서 둘만 보였고 `pytest` 는 1954 passed · 1 skipped 로 돌아왔다.

기준선(`origin/dev` 2f65bab 머지 뒤): `ruff` 통과 · `pytest` 1954 passed 1 skipped ·
`node --test` 371 pass.

| # | 주장 | 결과 | 잰 값 |
|---|---|---|---|
| A1 | 락이 없으면 레인 여럿이 같은 쿨다운 창을 통과한다 | **참** | 4레인 동시 기상 20회에서 **20회 전부** 게이트 레인 3개가 다 통과(잡 4/4). 락을 걸면 20회 전부 정확히 1개. 인위적 sleep 없이 `BEGIN IMMEDIATE` 사이의 틈만으로 |
| A2 | 게이트가 기본으로 켜지면 `test_e2e_m3` 가 깨진다 | **참** | `tests/test_e2e_m3.py:99` 에서 실패. 세 잡이 레인 1 에서 직렬로만 돌았고(4.5초, 20초 안), 타임아웃이 아니라 「다른 레인이 안 논다」 단언이 깨졌다 |
| A3 | 워커 dict 에 키를 더하면 기존 테스트가 깨진다 | **참, 하나가 아니라 셋** | `test_status_schema.py:224` · `test_server.py:400` · `test_worker_api.py:678` |
| A4 | 원격 표본의 낡음 예산은 heartbeat 세 번뿐이다 | **참, 경계는 배타적** | 15.0초는 아직 fresh, 15.000001초부터 stale. heartbeat 2번 밀림 = 정확히 15.0초. 그리고 초안의 `max(3×interval, 3×heartbeat)` 는 기본값에서 `max(15,15)` = **무효** |
| A5 | `admission_samples > history_samples` 면 영영 안 열린다 | **참** | `history_samples=1·2` 는 창이 3에 절대 못 닿는다. 서버 검증은 `>= 1` 뿐이고 **워커의 것은 다른 함수**(`config.py:911`)라 서버가 못 본다 |
| B1 | ETA 그리디가 레인 번호로 뭉갠다 | **참** | 로컬 1,2 + `build-02/1,2` = 4레인인데 `lane_free` 키가 `[1, 2]`. 대기 잡 셋째·넷째가 **0초가 아니라 400초**를 받는다 — 2레인 풀과 바이트 단위로 같은 결과 |
| B2 | 그 아래서 보류 레인 제외는 무효다 | **참** | held 레인을 빼도 결과가 완전히 동일. 레인 번호가 진짜로 다른 대조군에서는 0 → 400 으로 움직인다. **`idle_since` 도 같이 뭉개진다**(idle 3개 → 키 2개) |
| B3 | CPU 를 모르는 표본이 존재한다 | **참** | `top` 만 실패시키면 `sample.cpu is None` · `sampler.error is None` · `history[-1].cpu_busy is None`. `entry["cpu_busy"] <= 80` → `TypeError` |
| B4 | history 는 공백을 안 남긴다 | **참** | 2초 간격 3개 + 600초 공백 + 1개 → 마지막 3개가 리스트에서 붙어 있고 벽시계로 **604초**(간격의 302배)에 걸쳐 있다 |
| B5 | 메모리는 free 필드가 없고 두 OS 의 `used` 가 다르다 | **참** | 같은 픽스처에서 macOS free 31.9% vs Linux 60.4%(`MemFree` 로 재면 14.8%) |
| B6 | `ACTIONABLE_REASONS` 는 파이썬에서 죽은 코드다 | **참** | `src/`·`tests/`·`tools/`·`scripts/` 전체에서 정의 줄 말고 참조 0. `import *` 도 `__all__` 도 없다. 살아 있는 것은 `web/app.js:16`, `:389` 가 쓴다 |
| B7 | 웹은 `held` 를 안전하게 그리고 터미널은 숨긴다 | **참** | `workerState("held")` → `"held"`(두 언어, 예외 없음). `lanes >= 2` 머리줄은 held 레인이 idle 일 때와 **글자 하나까지 같다**. 4레인 중 2개가 held 여도 `lanes 1/4 busy` |

프로브가 새로 잡아낸 것(초안·1판에 없던 것): A3 의 테스트 **셋**, A4 의 배타적 경계와 무효한 remedy,
A5 의 워커 쪽 검증 사각, B2 의 `idle_since` 동반 붕괴. 전부 위 본문에 반영했다.

## 15. 병목 (실측, 2026-09-09)

게이트를 `store.claim` 앞에 다는 설계라, **claim 이 있는 자리가 얼마나 붐비는지**를 재야 했다. 격리
에이전트 둘이 실제 `Store`·실제 in-process 서버·실제 HTTP 로 재고, Codex 가 같은 코드를 정적으로 봤다.
프로브는 전부 스크래치패드에 있고 저장소는 건드리지 않았다(끝나고 `pytest` 1954 passed · 1 skipped).

### 15.1 좋은 소식 — 게이트 자체는 공짜다

| 무엇 | 잰 값 |
|---|---|
| `decide()` 최악 경로 | **1.29 µs** (레인 1 조기 반환 0.07 · 쿨다운 조기 반환 0.38 · `no_sample` 0.24) |
| 보류 레인 48개 × 초당 2회 | 코어의 **0.012%** |
| 오늘 idle 레인 한 번 깨는 비용 | 5.49 µs (`get_paused` 1.24 + 헛친 `claim` 4.24) |

게이트는 보류할 때 **claim 을 안 하므로** 순증이 아니라 **순감**이다. `history[].at` 을 미리 파싱해 두는
최적화는 0.465 µs 를 아낀다 — `core/admission.py` 를 복잡하게 할 값이 아니다. **§4.3 을 그대로 쓴다.**

`compute_queue` 도 문제없다: O(N·L) 이고 대기 1000 × 레인 16 이 3.2 ms, 대기 100 × 레인 16 이 0.31 ms 다.

### 15.2 게이트 바로 밑이 느리다 — PR 2a-0

| # | 병목 | 잰 값 | 고침 |
|---|---|---|---|
| C1 | **`worker_log` 이 마커 줄마다 트랜잭션 하나**(`remote_workers.py:425`) | 11.5k~31k txn/s — claim 이 굶기 시작하는 2000 txn/s 를 5~15배 넘는다. **256 KB flush 한 번에 다른 레인의 claim 이 0.03 ms → 275.9 ms**. 4 MB 본문이면 `busy_timeout` 이 터져 워커에 **5.43초 뒤 HTTP 500** | `Store.add_markers()`(`BEGIN IMMEDIATE` + `executemany`) 하나로. 4 MB 12.14초 → 0.38초(**32배**), `database is locked` 1 → 0. SSE 는 커밋 뒤 두 번째 루프에서 발행 |
| C1b | `_try_claim` 이 `LaneBusy` 만 잡는다(`remote_workers.py:345`) | `sqlite3.OperationalError` 가 일반 500 으로 새고, 워커는 2초 잔다 → 레인 7.4초 정지 + 뜻 모를 메시지 | 503 + `Retry-After` 로 |
| D6 | **`store.claim` 이 `jobs_pool` 을 탄다** — `jobs_state(state, id)` 가 **이미 있는데** 통계가 없어 플래너가 안 쓴다 | 20만 행에서 **16.5 ms**, 레인마다 초당 2회, `BEGIN IMMEDIATE` 안 | janitor sweep 에 `ANALYZE` 한 줄 → **0.004 ms(3727배)**. `list_recent` 102배, `list_pools` 3.875 → 0.002 ms |
| C4 | `CLAIM_MIN_INTERVAL = 1.0` 에 지터가 없다(`remote_worker.py:315`) | 같은 격자 32레인 = `max_concurrent_requests` 가 정확히 만석. 48레인에서 첫 `/api/status` 503, 128레인이면 워커 claim 406건 거절 | 0~1초 지터 → 503 **406 → 36건** |
| §5.3 | ETA 그리디의 레인 번호 충돌 | 4레인이 2레인처럼 — 대기 잡이 0초 대신 400초 | `(worker, lane)` 키 |

### 15.3 M5f 밖이지만 같이 봐야 할 것 — 결정 49

| 병목 | 잰 값 |
|---|---|
| `/api/status` 가 45일치 완료 잡 수에 선형 | 행당 24.5 µs — 1만 행 **245 ms**, 5만 행 **1.22초**. 그중 **91~93%가 `store.list_samples()`** (중앙값은 스칼라 6개만 쓰는데 완전한 `Job` 객체를 만든다). `_snapshot` 캐시는 TTL 0.2초(`server.py:126`)에 마커 줄마다 `_mark_dirty`(`:395`)라 실질적으로 안 듣는다. 중앙값만 별도 무효화하면 226 ms → 8 ms(28배) |
| `remote_worker_infos` N+1(`remote_workers.py:140`) | 워커 50 · 실행 250 에서 **마커 한 줄에 SQL 555개, 5.25 ms**. 이미 있는 `snap.jobs` 를 넘기면 61~167배 |
| `workers` 표를 아무도 안 지운다 | `DELETE FROM workers` 0건. 은퇴 워커 200대 = `down` 레인 401개가 매 요청에 3.61 ms |
| `PRAGMA synchronous` 미설정 → FULL | 쓰기 11,040 → **62,267 txn/s**(5.6배). WAL 에서 NORMAL 은 표준이고 손상은 없다(OS 크래시 시 마지막 커밋만 잃는다) — 내구성 변경이라 오너 결정 |
| 샘플러가 코어의 8.6%를 계속 쓴다 | macOS `top -l 2 -n 0 -s 1` 이 벽시계 1.39초 · CPU 381 ms 를 5초마다(`sample_once` 전체의 97%·89%). 게이트가 판단하는 15초 창 중 **실제 CPU 관측은 3초뿐**이고 샘플러가 그 창의 29%를 차지한다. 다행히 `stale()` 는 안전 — 10코어를 다 태워도 표본 나이가 5.0초(예산 15초). 명령이 **두 개** 걸려야(각 8초 타임아웃) 16.55초로 넘어간다 |
| `_resolve_sem` 이 요청 슬롯을 20초까지 잡는다(`server.py:1165`) | long-poll 이 아닌데 슬롯을 오래 잡는 유일한 경로. 상한이 있어 위험하진 않다 |

### 15.4 재 보고 문제가 아니었던 것

- 전역 `_admit_lock` (§4.5) — 머신별보다 낫다. 위 참조.
- SSE — 구독자별 재계산 없음(`_publish_server` 가 구독자 0명 1026 µs, 128명 1084 µs). `EventBus` 링은 2048 로 묶여 있고, `_log_partial`·`_worker_samples` 도 유계다.
- long-poll 의 요청 슬롯 점유 — `CLAIM_WAIT_SLOTS = 8` 이 구조적으로 묶어 32슬롯 중 8을 넘지 않는다.
- 실제 부하(워커 3 · 레인 11 · 로그 200줄/초)에서 쓰기 21 txn/s, claim p50 0.12 ms, lock 오류 0건.
