# M5f 테스트 시나리오 A — 순수 판정(`core/admission.py`) · 설정 키 (2026-09-09)

`docs/m5f-workplan.md` §4.1(설정 키와 검증) · §4.3(8단계 판정) · §4.4(기본값)을 §8 표의
`tests/test_admission.py` · `tests/test_config.py` 두 행으로 옮긴 것이다(test-first, 역할 A).
`src/` 와 기존 테스트는 건드리지 않았다. **§4.5 의 서버 배선(락 · `_hold` · 슬롯 순서 · 시작 로그) ·
§5.2 의 큐 사유 · §5.3 의 ETA 레인 키 · §5.4 의 화면**은 역할 B 가 `tests/test_server_m5f.py` ·
`tests/test_queue.py` · `tests/test_render_m5f.py` 로, **PR 2a-0 의 선행 병목(§15.2)**은 역할 C 가 맡는다.
여기 있는 것은 전부 **시계도 I/O 도 없는 함수 하나(`decide`)와 설정 로딩**뿐이라 sleep·스레드·소켓·DB 가 없다.

## 공통 — 픽스처와 도우미

시각은 한 상수에서만 온다. `jobfactory.NOW` 는 잡 픽스처용이라 쓰지 않는다(호스트 표본은 잡과 무관하다).

```python
NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
CFG = AdmissionConfig()  # policy "load" · 80.0 · 3 · 30.0 · 15.0


def stamp(dt: datetime) -> str:  # ⚠ core.status.iso 는 microsecond 를 버린다 —
    return dt.isoformat().replace("+00:00", "Z")  # 0.1초 epsilon 을 재려면 이걸 쓴다


def entry(busy, *, at_=None, mem=1) -> dict[str, Any]:
    """로컬 샘플러가 만드는 모양(`hostsample.py:252-257`) — 네 키를 다 넣는다."""
    return {"at": stamp(at_), "cpu_busy": busy, "mem_used_bytes": mem, "gpu_util_pct": None}


def history(*busy, step=5.0, end=NOW) -> tuple[dict, ...]:
    """`busy` 를 오래된 것 → 새것 순으로. 마지막 항목이 `end`, 그 앞은 step 초씩 뒤로."""


def sample(*, hist=None, age=0.0, interval=5.0, cpu=..., memory=..., load=LOAD) -> HostSample:
    """`sampled_at = NOW - age`. cpu 기본 `{"user":…, "sys":…, "idle":…, "busy": 10.0}`,
    memory 기본 `{"total_bytes":…, "used_bytes": 17550622720, "compressed_bytes":…}`,
    hist 기본 `history(10.0, 10.0, 10.0, end=sampled_at)`."""


def d(**kw):  # decide 는 키워드 전용 — lane 2 · CFG · NOW 가 기본
    return decide(
        lane=kw.pop("lane", 2),
        cfg=kw.pop("cfg", CFG),
        now=kw.pop("now", NOW),
        sample=kw.pop("sample", sample()),
        last_admit_at=kw.pop("last_admit_at", None),
    )
```

**`HOLD_CASES`** — lane ≥ 2 · `policy = "load"` 에서 **반드시 닫히는** 아홉 가지. A·B·H 가 같이 쓴다.

| id | 설정 | 기대 코드 |
|---|---|---|
| `no_sample` | `sample=None` | `no_sample` |
| `stale` | `sample=sample(age=60.0)` | `no_sample` |
| `cpu_none` | `sample=sample(cpu=None)` | `no_sample` |
| `cpu_busy_none` | `sample=sample(cpu={"busy": None})` | `no_sample` |
| `cooldown` | `last_admit_at=NOW - 1s` | `cooldown` |
| `short_window` | `hist=history(10.0, 10.0)` (2개 < `samples` 3) | `no_sample` |
| `none_in_window` | `hist=history(10.0, None, 10.0)` | `no_sample` |
| `broken_window` | `hist=history(10.0, 10.0, 10.0, step=99.0)` | `no_sample` |
| `over_cap` | `hist=history(10.0, 10.0, 95.0)` | `cpu_busy` |

## 잠근 API (`src/remote_ci_monitor/core/admission.py`)

- `@dataclass(frozen=True) class AdmissionConfig` — `policy: str = "load"` · `cpu_max_percent: float = 80.0` ·
  `samples: int = 3` · `cooldown_seconds: float = 30.0` · `stale_seconds: float = 15.0` (§4.3).
- `@dataclass(frozen=True) class Hold` — `code: str` · `detail: dict | None = None`.
  코드는 `"cpu_busy"` · `"no_sample"` · `"cooldown"` 셋뿐이다(§5.1).
- `def decide(*, lane: int, sample: HostSample | None, now: datetime,
  last_admit_at: datetime | None, cfg: AdmissionConfig) -> Hold | None` — `None` 이 「열림」이다(§4.3).

---

## A. `lane == 1` 은 게이트를 안 지난다 (§4.3 1 · 결정 40·41)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| A1 | `HOLD_CASES` 아홉 개 전부를 `lane=1` 로 → **전부 `None`**(×9) | `test_lane_one_is_open_for_every_hold_case` | 레인 1 이 닫히면 **큐 전체가 선다**. 표본이 아직 없는 기동 직후에도 잡이 돌아야 한다 — 이 성질 하나가 「밸브 없이 계기만」(결정 40)을 성립시킨다 |
| A2 | 같은 아홉 개를 `lane=2` 로 → 전부 `Hold` 이고 코드가 위 표대로(×9) | `test_every_hold_case_actually_holds_for_lane_two` | **A1·B1 의 대조군.** 이게 없으면 「전부 열림」 버그가 A1·B1 을 통과한다. 코드까지 잠가 fail-open 도 같이 막는다 |
| A3 | `lane=1` · `sample=None` · `last_admit_at=NOW` · `policy="load"` (보류 조건 동시 성립) → `None` | `test_lane_one_is_open_when_every_hold_condition_is_true_at_once` | `lane == 1` 검사를 **첫 줄이 아닌 곳**에 둔 구현을 잡는다. 순서가 §4.3 그대로여야 한다 |

## B. `policy = "always"` (§4.3 2 · 완료 기준 5)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| B1 | `cfg=replace(CFG, policy="always")` · `lane=2` · `HOLD_CASES` 아홉 개 → **전부 `None`**(×9) | `test_policy_always_is_open_for_every_hold_case` | 완료 기준 5 — 「끄면 오늘의 동작」. 표본 검사 **뒤에** policy 를 보는 구현은 기동 직후 `always` 인데도 닫힌다 |
| B2 | `policy="always"` · `last_admit_at=NOW` · `lane=4` → `None` | `test_policy_always_is_open_even_during_the_cooldown` | 2 단계가 5 단계보다 **앞**이다. `always` 인데 30초에 하나로 묶이면 그건 「오늘의 동작」이 아니다 |
| B3 | `AdmissionConfig().policy == "load"` 이고 그 기본값에서 `over_cap` 이 닫힌다 | `test_policy_load_is_the_default_and_still_gates` | 결정 39 — 게이트는 **기본으로 켠다**. 기본이 `always` 로 흘러가면 기능 전체가 조용히 무효가 된다 |
| B4 | `policy="off"` · `policy=""` · `policy="Always"` → `load` 처럼 동작(닫힌다) (×3) | `test_an_unknown_policy_behaves_like_load` | fail-closed. 설정 검증이 앞에서 막으니 도달 불가지만, `decide` 만 따로 부르는 곳(서버 테스트·프로브)이 열려 버리면 안 된다. **가정 1** |

## C. 표본 없음 · 낡음 (§4.3 3 · §4.5 · §14-A4)

`stale_seconds` 는 **서버가 표본 종류에 맞춰 계산해 넣는 인자**다(로컬 `3 × [host] interval_seconds`,
원격도 기본 15초). `decide` 는 그것을 그대로 쓰고 `sample.interval_seconds` 로 다시 계산하지 않는다.

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| C1 | `sample=None` → `Hold("no_sample")`. 서버의 두 상황(**`App.sampler` 가 `start()` 전이라 `None`** · **샘플러는 있는데 `latest()` 가 `([], error)`**)이 순수 계층에서는 같은 한 호출로 모인다 | `test_no_sample_holds_no_sample` | **mutcheck ⑪ 의 표적**(「표본 없음 → hold」를 「→ 열림」으로). 이 기능의 안전 성질 그 자체 |
| C2 | `age = 15.0 - 1e-6`(`stale_seconds=15.0`) · 나머지 정상 → `None` | `test_a_sample_one_microsecond_under_the_stale_limit_is_fresh` | `>=` 로 쓴 구현이 아직 신선한 표본을 버린다 |
| C3 | `age = 15.0` 정확히 → `None` (**경계는 열림**) | `test_a_sample_exactly_at_the_stale_limit_is_not_stale` | 실측(§14-A4·§4.5): heartbeat 이 두 번 밀린 원격 표본이 **정확히 15.0초**에 앉는다. 등호를 닫으면 한가한 원격 머신의 레인 ≥ 2 가 주기적으로 죽는다 |
| C4 | `age = 15.000001` → `Hold("no_sample")` | `test_a_sample_just_over_the_stale_limit_holds_no_sample` | 경계의 반대편. `>` 를 `>=` 로 바꿔도 이건 초록이라 **C2·C3 와 셋이 한 벌**이다 |
| C5 | `cfg.stale_seconds=60` · `age=30` · `sample.interval_seconds=5.0` → `None` (`hostparse.stale()` 기준이면 15초라 낡았을 값) | `test_stale_seconds_comes_from_the_caller_not_the_sample_interval` | §4.5 — 예산은 **표본 종류마다 서버가 정한다**. `decide` 안에서 `3 × interval` 을 다시 계산하면 결정 47 로 예산을 늘릴 길이 막힌다 |
| C6 | `age = -5.0`(표본 시각이 미래) → 낡지 않음, 정상 판정 | `test_a_sample_stamped_in_the_future_is_not_stale` | `abs(age)` 로 쓴 구현이 작은 시계 어긋남에 레인을 닫는다. 명세는 `now − sampled_at > limit` 이라고만 했다 |
| C7 | age ∈ {14.9, 15.0, 15.000001, 20.0} 에서 `decide(stale_seconds=3×interval)` 의 닫힘 여부가 `hostparse.stale(sampled_at, now, interval)` 과 **한 칸도 안 어긋난다**(×4) | `test_the_stale_boundary_matches_hostparse_stale` | `/api/status` 는 `hostparse.stale()` 로 `stale` 필드를 찍고 게이트는 `decide` 로 닫는다. 둘이 어긋나면 **화면은 fresh 인데 레인은 `no_sample`** 이라 아무도 원인을 못 찾는다 |

## D. CPU 를 모르는 표본 (§4.3 4 · §14-B3)

표본은 cpu·`memory["used_bytes"]`·load 중 **하나만** 읽혀도 만들어진다(`hostsample.py:245-248`).
기존 테스트가 이미 증인이다 — `tests/test_hostsample.py:164`(`top` 만 실패 → `sample.cpu is None`,
`hosts_error` 는 없음) · `:170`(`vm_stat` 만 실패 → `used_bytes: None`).

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| D1 | `cpu=None` · **history 는 정상**(10,10,10) → `Hold("no_sample")`, 예외 없음 | `test_a_sample_whose_cpu_is_none_holds_no_sample_instead_of_raising` | 실측 §14-B3: `top` 하나만 죽어도 이 표본이 실제로 생긴다. 4단계를 빼면 6·7단계에서 `None <= 80` 이 **`TypeError`** 로 터져 claim 루프가 죽는다. history 를 정상으로 둬서 **4단계만** 시험한다 |
| D2 | `cpu={"user":…, "sys":…, "idle":…, "busy": None}` · `cpu={"user": 5.0}`(busy 키 자체가 없음) → 둘 다 `Hold("no_sample")` (×2) | `test_a_cpu_dict_without_a_usable_busy_holds_no_sample` | 원격 표본은 화이트리스트의 **부분집합**만 실어도 통과한다(`hostparse.py:316`) — `busy` 키가 아예 없는 `cpu` dict 가 실재한다. `sample.cpu is None` 만 보는 구현은 `cpu["busy"]` 에서 `KeyError` |
| D3 | 샘플러가 `vm_stat`·`sysctl` 만 성공한 모양(`cpu=None` · `load=None` · `memory={"used_bytes": 17550622720, …}`) → `Hold("no_sample")` | `test_a_memory_only_sample_reaches_the_gate_and_is_held` | 「표본이 있다 ⇒ CPU 를 안다」는 **거짓**이다. 가드가 보는 것은 memory dict 전체가 아니라 `memory.get("used_bytes")` 라 memory 하나로 표본이 살아 게이트까지 온다 |

## E. 쿨다운 (§4.3 5 · §4.4 · 결정 41)

**경계의 답은 하나다: `now − last_admit_at < cfg.cooldown_seconds` 면 닫힘.** 정확히 `cooldown_seconds`
면 `30 < 30` 이 거짓이라 **열림**이다.

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| E1 | `last_admit_at=None` · 나머지 정상 → `None` | `test_no_last_admit_at_skips_the_cooldown` | 아직 아무것도 안 집은 머신이 없는 기록 때문에 닫히면 안 된다 |
| E2 | `last_admit_at = NOW - 29s` · `cooldown_seconds=30` → `Hold("cooldown")` | `test_cooldown_holds_at_twenty_nine_seconds` | 쿨다운이 실제로 닫는다는 것 |
| E3 | `last_admit_at = NOW - 30s` · `cooldown_seconds=30` → **`None`(열림)** | `test_cooldown_is_open_at_exactly_thirty_seconds` | `<=` 로 쓰면 창이 30초가 아니라 31초가 되고, 머신마다 매 사이클 1초가 죽는다. 구현자는 답이 **하나** 필요하다 — 이것이 그 답 |
| E4 | `last_admit_at = NOW - 31s` → `None` | `test_cooldown_is_open_at_thirty_one_seconds` | 경계 바깥. E2·E3 와 셋이 한 벌 |
| E5 | `cooldown_seconds=0` · `last_admit_at=NOW` → `None` | `test_cooldown_zero_never_holds` | §4.1 이 `>= 0` 을 허용한다. 0 은 「쿨다운 없음」이지 「항상 닫힘」이 아니다 — `<=` 구현이면 0 이 영구 보류가 된다 |
| E6 | `last_admit_at = NOW - 1s` · **동시에** history 가 짧고(2개) `cpu_busy` 도 95 → `Hold("cooldown")` | `test_the_cooldown_beats_the_history_walk` | 5단계가 6·7단계보다 앞이라는 §4.3 순서. 화면이 「CPU 가 바빠서」라고 말하는데 실제로는 방금 잡은 것뿐이면 오너가 상한을 잘못 조정한다 |
| E7 | `last_admit_at = NOW + 5s`(미래) → `Hold("cooldown")` | `test_a_last_admit_at_in_the_future_holds` | `-5 < 30` 이라 문자 그대로 닫힌다 = fail-closed. 서버가 자기 시계로 찍으니 안 나야 하지만, 나면 닫히는 쪽이 맞다 |

## F. history 창 — 개수 · `None` · 연속성 (§4.3 6 · §13-G · §14-B4)

**간격 판정은 항목끼리만 비교한다.** `history[].at` 은 표본을 만든 **머신의 시계**이고 `sampled_at` 은
**서버 시계**다(원격은 `remote_workers.py:520` 에서 다시 찍힌다). `at` 은 ISO **문자열**이라
`core/status.parse_iso` 로 읽는다.

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| F1 | history 2개 · `samples=3` → `Hold("no_sample")` | `test_a_window_shorter_than_samples_holds_no_sample` | §4.4 — 서버 기동 직후 15초는 레인 ≥ 2 가 닫혀 있어야 한다(설계상 fail-closed). 모자란 창으로 여는 구현은 그 15초에 무거운 잡을 다 들여보낸다 |
| F2 | history **정확히 3개**(전부 10.0, 5초 간격) · `samples=3` → `None` | `test_exactly_samples_entries_are_evaluated` | off-by-one. `len(history) > samples` 를 요구하면 `[host] history_samples == admission_samples` 인 설치에서 **영영 안 열린다** |
| F3 | `history=()` → `Hold("no_sample")` | `test_an_empty_history_holds_no_sample` | 빈 튜플에서 `history[-1]` 은 `IndexError` 다. 원격 표본은 `history` 없이도 만들어진다(`hostparse.py:386`) |
| F4 | 6개 창: 앞 3개 99.0 + 뒤 3개 10.0 → `None` / 뒤집으면(앞 10.0 + 뒤 99.0) → `Hold("cpu_busy")` (×2) | `test_only_the_last_samples_entries_are_read` | 창은 **꼬리** `history[-samples:]` 다. 전체를 훑으면 한 시간 전에 바빴던 머신이 영영 안 열리고, 머리를 보면 지금 바쁜 머신이 열린다 |
| F5 | 3칸 창의 0·1·2 번 자리에 각각 `cpu_busy=None`, 그리고 `cpu_busy` 키가 아예 없는 항목 → 전부 `Hold("no_sample")` (×4) | `test_a_none_cpu_busy_anywhere_in_the_window_holds_no_sample` | `hostsample.py:252-259` 가 항목을 **조건 없이** append 하므로 `top` 이 한 번 실패한 주기가 창 한가운데에 `None` 으로 남는다(`tests/test_hostsample.py:302` 가 이미 증인). `all(... is not None)` 을 빼면 `TypeError`, `any` 로 쓰면 fail-open |
| F6 | 4개 중 **가장 오래된** 항목만 `cpu_busy=None` · `samples=3` → `None` | `test_a_none_cpu_busy_outside_the_window_is_ignored` | F5 의 반대. 전체를 훑는 구현은 회복된 머신을 `history_samples` 주기 동안(기본 60개 = 5분) 묶어 둔다 |
| F7 | `interval_seconds=5.0` · 이웃 간격이 **정확히 10.0초** → `None` | `test_neighbouring_entries_exactly_two_intervals_apart_are_contiguous` | `2 × interval` 은 「넘으면」이다(`stale()` 과 같은 배타적 경계). `>=` 면 샘플러가 조금 느린 정상 머신이 닫힌다 |
| F8 | 같은 설정에서 이웃 간격 **10.000001초** → `Hold("no_sample")` | `test_a_gap_over_two_intervals_breaks_the_window` | F7 과 한 벌. epsilon 은 `stamp()` 로 만든다 — `core.status.iso` 를 쓰면 microsecond 가 잘려 두 케이스가 같아진다 |
| F9 | `interval_seconds=2.0` · `samples=3` · `at` = NOW−604 · NOW−602 · NOW−600 · **(600초 공백)** · NOW → `Hold("no_sample")`. 꼬리 3개 `(NOW−602, NOW−600, NOW)` 는 리스트에서 붙어 있지만 이웃 간격이 600초(> 2×2)이고 창이 602초에 걸쳐 있다(전체는 §14-B4 의 604초) | `test_three_entries_a_ten_minute_outage_and_one_more_break_the_window` | **이 규칙이 존재하는 이유 그 자체.** 실측 §14-B4 — history 는 공백을 남기지 않으므로, 정전 전에 한가했던 머신이 「연속 3표본 한가함」으로 보인다. 개수만 세는 구현은 여기서 무거운 잡을 들여보낸다 |
| F10 | 이웃 간격은 완벽(5초)인데 `history[-1].at = NOW - 300s`, `sampled_at = NOW`(즉 표본 자체는 fresh) → `None` | `test_the_gap_rule_never_compares_an_entry_to_now` | **항목 대 `now` 를 비교하면 안 된다**(§4.3 6 · §13-G). 원격 워커의 시계가 5분 어긋나 있으면 그 워커의 레인 ≥ 2 가 전부 영구히 닫힌다 — 그런데 부하와는 아무 상관이 없다 |
| F11 | 같은 `at` 배치(8초 간격)로 `interval_seconds=2.0` → `Hold("no_sample")`(8 > 4) / `interval_seconds=5.0` → `None`(8 ≤ 10) (×2) | `test_the_gap_rule_uses_the_samples_own_interval_seconds` | 임계는 상수도 `cfg` 값도 아니고 **그 표본의** `interval_seconds` 다. 워커마다 샘플러 주기가 다르다(`worker.toml` 이 정한다, §4.1) |
| F12 | 창 항목 하나에 `at` 키가 없음(`{"cpu_busy": 10.0}`) → `Hold("no_sample")`, 예외 없음 | `test_a_missing_at_holds_no_sample` | 원격 `_known_dict` 는 payload 에 있던 키만 남긴다. `parse_iso(None)` 은 `None` 이고 `None - datetime` 은 `TypeError` |
| F13 | `at` = `"not-a-time"` · `""` · `"2026-09-08T99:99:99Z"` → 전부 `Hold("no_sample")`, 예외 없음 (×3) | `test_an_unparseable_at_holds_no_sample_instead_of_raising` | `datetime.fromisoformat` 은 `ValueError` 를 던진다. §4.3 의 「모르면 닫는다」를 예외가 아니라 **판정**으로 돌려주는지 |
| F14 | 창의 한 항목만 timezone 없는 `"2026-09-08T11:59:55"`, 나머지는 `Z` → 예외 없이 `Hold("no_sample")` | `test_a_naive_at_next_to_an_aware_one_holds_no_sample_instead_of_raising` | naive − aware 는 `TypeError` 다. 창 **전체**가 naive 면 빼기가 성립해 답이 달라지므로 두 경우를 갈라 둔다. **가정 2** |
| F15 | 창 항목 하나가 빈 dict `{}` → `Hold("no_sample")`, 예외 없음 | `test_an_empty_history_entry_holds_no_sample` | 원격 표본은 `{}` 를 실제로 싣는다 — `sample_from_json` 이 `_known_dict(None, …) or {}` 로 만든다(`hostparse.py:390`). `entry["cpu_busy"]` 는 `KeyError` |
| F16 | `samples=1` · history 1개(70.0) → `None` | `test_samples_one_needs_no_contiguity` | 항목이 하나면 이웃 쌍이 없어 연속성은 **참**이다. `window[i+1]` 을 무조건 도는 구현이나 「2개 이상」을 요구하는 구현은 §4.1 이 허용한 `admission_samples = 1` 에서 영구 보류가 된다 |
| F17 | 창의 `at` 이 뒤로 간다(시계가 되감김): NOW−5 · NOW−600 · NOW → `Hold("no_sample")` | `test_entries_that_go_backwards_in_time_hold_no_sample` | 부호를 안 보고 `(b - a).total_seconds() > 2*interval` 만 쓰면 **음수 간격이 조용히 통과**한다 = fail-open 구멍. **가정 3** |

## G. CPU 상한 경계와 `Hold.detail` (§4.3 7 · §5.1 · §5.4)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| G1 | `cpu_max_percent=80` · 창 `[80.0, 80.0, 80.0]` → **`None`(열림)** | `test_a_window_of_exactly_the_cap_is_open` | 규칙은 `cpu_busy <= cpu_max_percent`. `<` 로 쓰면 오너가 정한 값에 **정확히** 앉은 머신이 닫힌다 |
| G2 | 창 `[80.0, 80.0, 80.1]` → `Hold("cpu_busy")` | `test_one_tenth_over_the_cap_holds_cpu_busy` | G1 과 한 벌. 경계 양쪽을 같이 잠가야 `<`/`<=` 가 갈린다 |
| G3 | 창 `[70.0, 70.0, 85.0]` → `Hold("cpu_busy", detail 85.0)` | `test_one_busy_entry_at_the_end_of_the_window_holds` | **mutcheck ⑫ 의 표적**(`all` → `any`). 한 번 꺼진 표본에 속아 여는 버그 |
| G4 | 창 `[70.0, 70.0, 79.0]` → `None` | `test_a_window_entirely_under_the_cap_is_open` | G3 의 대조군. `any` 로 바꿔도 초록이라 **G3 와 짝**이어야 뮤테이션이 잡힌다 |
| G5 | 창 `[85.0, 70.0, 70.0]` → `Hold("cpu_busy")` | `test_the_oldest_entry_over_the_cap_still_holds` | 규칙은 「창의 **전부**」다. 「가장 새 항목만」으로 읽으면 `admission_samples` 가 존재할 이유가 사라진다 |
| G6 | 창 `[70.0, 70.0, 92.4]` → `Hold(code="cpu_busy", detail={"cpu_busy": 92.4})` | `test_hold_detail_carries_the_newest_cpu_busy` | `rcm top` 이 `held (cpu 92%)` 를, 웹이 「CPU 가 바빠서 안 집는 중 (92%)」을 찍는다(§5.1·§5.4). 최댓값이나 첫 위반값을 넣으면 화면이 최신 표본과 다른 숫자를 말한다 |
| G7 | 창 `[85.0, 70.0, 70.0]` → `detail == {"cpu_busy": 70.0}`(상한 **아래** 값이 실린다) | `test_hold_detail_is_the_newest_entry_even_when_it_is_under_the_cap` | §4.3 7 의 「detail=최신값」을 문자 그대로. 화면에 `held (cpu 70%)` 가 뜨는 게 맞는지 확인이 필요하다. **가정 4 · 질문 3** |
| G8 | `sample.cpu={"busy": 5.0}` + 창 `[95,95,95]` → `Hold("cpu_busy")` / `sample.cpu={"busy": 95.0}` + 창 `[5,5,5]` → `None` (×2) | `test_the_threshold_reads_the_history_not_sample_cpu` | 4단계의 `sample.cpu` 는 **생존 확인**이고 7단계는 **창**을 본다. `sample.cpu["busy"]` 로 지름길을 내면 「한 표본에 속지 않는다」는 성질이 통째로 사라진다 |
| G9 | `cpu_max_percent=100` · 창 `[100.0, 100.0, 100.0]` → `None` | `test_cpu_max_percent_one_hundred_never_holds_on_cpu` | §4.1 이 100 을 허용한다 — 100 은 「CPU 게이트 끔」이어야 한다. `<` 면 꽉 찬 머신에서 닫혀 의미가 뒤집힌다 |
| G10 | `cpu_max_percent=1` · 창 `[1.0,1.0,1.0]` → `None` / `[1.0,1.0,1.1]` → `Hold` (×2) | `test_cpu_max_percent_one_is_the_other_legal_end` | 허용 범위의 반대 끝. 상한을 int 로 잘라 쓰는 구현(`int(1.1) == 1`)을 잡는다 |
| G11 | 창 `[150.0, 150.0, 150.0]` → `Hold` / `[-3.0, -3.0, -3.0]` → `None` (×2) | `test_an_out_of_range_cpu_busy_is_compared_not_clamped` | 원격 `cpu_busy` 는 유한한 수인지만 검사된다(`hostparse.py:326-334`). 이상한 값이 와도 **비교만** 하고 죽거나 clamp 하지 않는다 |

## H. 판정 순서 (§4.3 — 순서가 규범이다)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| H1 | 조건을 **둘씩 동시에** 켜고 나오는 코드를 표로 잠근다(×10): `lane1+전부`→열림 · `always+전부`→열림 · `stale+cpu_none`→`no_sample` · `stale+cooldown`→`no_sample` · `stale+over_cap`→`no_sample` · `cpu_none+cooldown`→`no_sample` · `cpu_none+over_cap`→`no_sample` · `cooldown+short_window`→`cooldown` · `cooldown+over_cap`→`cooldown` · `short_window+over_cap`→`no_sample` | `test_the_reported_code_follows_the_spec_order` | 코드 하나가 **화면의 문장**이 된다(`hold.cpu_busy` · `hold.no_sample` · `hold.cooldown`, §5.4). 순서가 바뀌면 「CPU 가 바쁘다」와 「표본이 없다」가 뒤바뀌어 오너가 엉뚱한 곳을 고친다 |
| H2 | `age=60`(낡음) **그리고** 창 `[99,99,99]`(상한 초과) → `Hold("no_sample")` | `test_a_stale_and_overloaded_sample_reports_no_sample` | 과제가 콕 집은 경우. 낡은 표본의 CPU 숫자는 **믿을 수 없는 숫자**라 그걸 이유로 대면 안 된다 |
| H3 | `age=60` **그리고** `last_admit_at=NOW` → `Hold("no_sample")` | `test_a_stale_sample_beats_the_cooldown` | 3단계가 5단계보다 앞. 샘플러가 죽은 것을 「방금 집었을 뿐」로 감추면 §3.1 의 fail-open 구멍이 화면에서 사라진다 |

## I. 순수성 · 모듈 경계 · 기본값 (§4.3 · §4.4 · §13-M)

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| I1 | `AdmissionConfig()` → `policy=="load"` · `cpu_max_percent==80.0` · `samples==3` · `cooldown_seconds==30.0` · `stale_seconds==15.0` | `test_admission_config_defaults_match_the_spec` | §4.4 — 80 은 **오너가 정한 값**이다. 기본값이 흘러가면 업그레이드만으로 모든 설치의 동작이 조용히 바뀐다 |
| I2 | `AdmissionConfig` 는 frozen dataclass — `replace()` 는 되고 대입은 `FrozenInstanceError` | `test_admission_config_is_a_frozen_dataclass` | `core/queue.QueueConfig` 와 같은 방식(§4.3 주석). 서버가 하나 만들어 모든 레인이 공유하므로 한 스레드가 고치면 전부 바뀐다 |
| I3 | `Hold("no_sample")` → `.code == "no_sample"` · `.detail is None`(기본) · `Hold("cpu_busy", {"cpu_busy": 92.4})` 끼리 `==` 로 같다 | `test_hold_carries_code_and_detail_and_compares_by_value` | `_hold` 가 판정을 **기록해 두고 상태 경로가 다시 안 판정한다**(§4.5). 값 비교가 안 되면 「판정이 바뀌었나?」가 늘 참이 되어 `Worker._set` 이 `held_since` 를 매번 되감고 §4.5 가 경고한 그대로 `not_scheduled` 알람이 죽는다 |
| I4 | `inspect.signature(decide)` 의 다섯 인자가 전부 KEYWORD_ONLY · 위치 인자로 부르면 `TypeError` | `test_decide_takes_every_argument_by_keyword` | `now` 와 `last_admit_at` 은 **둘 다 datetime** 이다. 호출자가 둘(`Worker.run` · `worker_claim`)이라 위치를 허용하면 바꿔 넣어도 조용히 돈다 |
| I5 | 같은 인자로 50번 → 결과가 매번 `==` 로 같다. 따로 만든 동등한 인자 두 벌 → 결과도 같다 | `test_decide_is_deterministic_for_the_same_inputs` | §4.5 「한 번만 판정한다」의 전제. 판정이 흔들리면 화면과 실제가 어긋난다 |
| I6 | ① `monkeypatch.setattr(admission, "datetime", Boom, raising=False)` · `"time"` 도 같이 — `Boom.now()`/`utcnow()`/`time()` 이 `AssertionError` 를 던지게 하고 `HOLD_CASES` 아홉 경로 + 열리는 경로를 전부 돈다 → 아무것도 안 터지고 결과가 그대로. ② 모듈 원문(`Path(admission.__file__).read_text()`)에 `datetime.now` · `utcnow` · `time.time` · `time.monotonic` · `open(` · `os.` · `subprocess` · `sqlite3` · `random` 이 **하나도 없다** | `test_decide_never_reads_the_clock_or_the_filesystem` | 순수 계층 규칙(PLAN · §4.3 제목). 시계를 몰래 읽으면 `now` 인자가 거짓말이 되고, 서버가 락 안에서 하는 `decide → claim → 기록`(§4.5)이 재현 불가능해져 완료 기준 4 를 검증할 수 없다 |
| I7 | `decide(now=NOW, …)` 와 `decide(now=NOW+1h, …)` 를 `sampled_at`·`last_admit_at`·`history[].at` 을 **똑같이 1시간 밀어서** 부르면 결과가 같다 | `test_shifting_now_and_the_sample_together_changes_nothing` | 판정이 **차이**에만 의존한다는 것. 절대 시각(자정 경계·날짜)에 반응하는 구현을 잡는다 |
| I8 | 호출 전후로 `sample`·`cfg`·`sample.history` 의 각 dict 가 **글자 하나까지** 같다(깊은 비교) | `test_decide_does_not_mutate_its_arguments` | ⚠️ `history` 의 dict 들은 **샘플러의 살아 있는 deque 와 같은 객체**다(`hostsample.py:259`·`:275` 는 튜플만 복사한다). `entry["at"] = parse_iso(...)` 같은 제자리 정규화가 들어가면 이후 모든 표본과 `/api/status` JSON 이 오염된다 |
| I9 | ① 모듈 원문에 `remote_ci_monitor.config` · `from remote_ci_monitor import config` 가 없다. ② 새 인터프리터(`subprocess`, `python -c`)에서 `import remote_ci_monitor.core.admission` 만 하면 `sys.modules` 에 `remote_ci_monitor.config` 가 **안 들어온다** | `test_the_admission_module_does_not_import_the_config_module` | §4.3 주석 · §13-M — `core/queue.QueueConfig` 와 같은 방식. 오늘 `core/` 전체가 `config` 를 하나도 안 쓴다(grep 0건). 끌어들이면 순수 계층이 TOML 로딩(`tomllib`·`zoneinfo`·`shutil`)에 얹히고 `AdmissionConfig` 기본값이 `ServerSection` 과 두 벌이 된다. ②가 없으면 pytest 가 이미 `config` 를 import 해 둬서 못 잡는다 |

## J. `tests/test_config.py` — 설정 키와 검증 (§4.1)

기존 도우미를 그대로 쓴다: `load(tmp_path, text)` · `server_toml(**keys)` · `msg(e, tmp_path)`(오류
문구에서 tmp 경로를 뺀다) · env 는 `load_server_config(p, environ={...})`.

| # | 시나리오(설정 → 기대) | 테스트 함수 | 왜 — 잡는 버그 · 명세 |
|---|---|---|---|
| J1 | `load(tmp_path, GOOD)` → `admission == "load"` · `cpu_max_percent == 80` · `admission_samples == 3` · `admission_cooldown_seconds == 30` | `test_admission_keys_have_defaults` | 결정 39 — 게이트는 **기본으로 켠다**. 기본이 하나라도 어긋나면 「업그레이드만으로 레인 ≥ 2 의 동작이 바뀐다」는 CHANGELOG 문장이 거짓이 된다 |
| J2 | `[server] admission="always" · cpu_max_percent=50 · admission_samples=2 · admission_cooldown_seconds=10` + `[host] interval_seconds=5`(2×5=10 ≤ 10) → 그대로 읽힌다 | `test_admission_keys_from_file` | 네 키가 `ServerSection` 에 **실제로 선언**됐는지. 없으면 `[server] unknown key` 로 죽는다 |
| J3 | env `RCM_SERVER_ADMISSION=always` · `RCM_SERVER_CPU_MAX_PERCENT=50` · `RCM_SERVER_ADMISSION_SAMPLES=2` · `RCM_SERVER_ADMISSION_COOLDOWN_SECONDS=10` → 그대로. `RCM_SERVER_CPU_MAX_PERCENT=soon` → `ConfigError` 에 `[server] cpu_max_percent` | `test_admission_keys_from_env` | env 경로는 `_coerce_scalar` 라는 **다른 코드**를 탄다. 이 Mac 의 launchd 에이전트가 설정을 주는 길이 그쪽이다 |
| J4 | `admission` = `"off"` · `"Load"` · `""` · `"loads"` · `"none"` → `ConfigError`, 문구는 정확히 `[server] admission must be "load" or "always"` (×5) | `test_admission_must_be_load_or_always` | 오타를 그냥 받으면 오너는 껐다고 믿는데 게이트가 켜져 있다. 대소문자를 안 받는 것도 같이(`"Load"`) |
| J5 | `[server] admision = "load"` · `admission_policy = "load"` · `cpu_max = 80` → `ConfigError` 에 `[server] unknown key` 와 그 키 이름 (×3) | `test_an_admission_typo_is_an_unknown_key` | 기존 규칙(`test_unknown_key_fails_with_section_and_key`)이 새 키에도 그대로 적용되는지. 조용히 무시되면 **기본값으로 도는 게이트**가 남는다 |
| J6 | `cpu_max_percent` = `0` · `-1` · `101` · `1000` → `[server] cpu_max_percent must be between 1 and 100` (×4). `1` 과 `100` 은 로드된다 (×2) | `test_cpu_max_percent_must_be_between_1_and_100` | 0 이면 살아 있는 머신에서 `busy <= 0` 이 절대 참이 아니라 레인 ≥ 2 가 영구 보류다. 101 이면 절대 안 닫히는 게이트다 — 둘 다 조용한 오설정 |
| J7 | `admission_samples` = `0` · `-1` → `[server] admission_samples must be >= 1` (×2). `1` 은 로드된다 | `test_admission_samples_must_be_at_least_one` | 0 이면 창이 비어 「전부 상한 아래」가 공허하게 참 = **fail-open**. 명세가 하한을 둔 이유 |
| J8 | `admission_cooldown_seconds = -1` → `[server] admission_cooldown_seconds must be >= 0`. `0` 은 로드된다(= 쿨다운 없음, E5 와 짝) | `test_admission_cooldown_seconds_must_not_be_negative` | 음수 쿨다운은 뜻이 없다. 0 은 합법이므로 `>= 1` 로 잘못 쓰면 「쿨다운 끄기」가 막힌다 |
| J9 | 실패: `[host] history_samples=60`(기본) + `admission_samples=61` → `[server] admission_samples must be <= [host] history_samples`. 통과: `admission_samples=60`(**등호 허용**) · `history_samples=3` + `admission_samples=3` (×2+×2) | `test_admission_samples_must_not_exceed_host_history_samples` | 실측 §14-A5 — 창이 절대 안 차면 레인 ≥ 2 가 **영영 안 열리고** 증상은 `no_sample` 하나뿐이라 원인을 못 찾는다 |
| J10 | `[host] history_samples = 2` 만 적고 `admission_samples` 는 **안 적음**(기본 3) → `ConfigError` | `test_lowering_host_history_samples_below_admission_samples_is_rejected` | 오너가 admission 키를 하나도 안 건드려도 **업그레이드 순간 기동이 막힌다**. 의도된 동작이라면 CHANGELOG·`docs/configuration.md` 에 그 문장이 있어야 한다 — **질문 5** |
| J11 | 실패: `admission_samples=3` + `[host] interval_seconds=11` + `cooldown=30`(33 > 30) → `[server] admission_samples × [host] interval_seconds must not exceed admission_cooldown_seconds`. 통과: `interval_seconds=10`(30 ≤ 30, **등호 허용**) · 기본값(3×5=15 ≤ 30) | `test_the_cooldown_must_cover_one_full_sampling_window` | 쿨다운이 창보다 짧으면 「잡의 부하가 표본에 나타나기 전」에 다음 레인이 통과한다(§4.4) — 게이트가 있는데도 버스트가 난다 |
| J12 | `admission_cooldown_seconds = 10` 만 내리면(기본 interval 5 · samples 3 → 15 > 10) `ConfigError`. `[host] interval_seconds = 3` 을 같이 내리면(9 ≤ 10) 로드된다 | `test_lowering_the_cooldown_alone_is_rejected` | §4.4 가 「짧은 잡이 많으면 10 으로 내린다, 그러면 `[host] interval_seconds` 도 같이 봐야 한다」고 적은 그 문장을 **검증이 실제로 강제**하는지 |
| J13 | `admission = "always"` + `cpu_max_percent = 0` → 여전히 `ConfigError` | `test_admission_always_still_validates_the_other_keys` | 게이트를 꺼도 값 검사는 산다. 안 그러면 잘못된 설정이 조용히 남아 있다가 오너가 다시 `"load"` 로 돌리는 순간 서버가 안 뜬다 |
| J14 | 워커 설정(`load_worker_config`)에 `[host] history_samples = 2` → **정상 로드**(워커 쪽은 `>= 1` 만 본다, `config.py:911-912`). 같은 상황에서 서버 설정 `admission_samples = 3` 도 **정상 로드** | `test_a_worker_history_samples_below_admission_samples_still_loads` | §4.1 의 ⚠ — 서버는 워커의 `worker.toml` 을 **본 적이 없다**. 이 어긋남은 로드 시각이 아니라 **런타임**에만 드러나고(`Hold("no_sample")` 영구), 대응은 §5.4 의 `rcm check` 경고다. 「교차 검증을 여기에도 넣자」는 고침이 불가능하다는 것을 못 박는다 |
| J15 | `[host] history_samples = 0` + `admission_samples = 3` → 오류 문구가 `[host] history_samples` 를 가리킨다(교차 검증이 아니라) | `test_an_invalid_history_samples_reports_the_host_key` | 검증 순서. `[server] admission_samples must be <= [host] history_samples` 가 먼저 터지면 오너는 `[server]` 를 고치러 간다. **가정 5 · 질문 6** |

---

**79 건**(파라미터를 펼치면 약 120 건). `ruff check` · `ruff format --check` 기준(line-length 100, CJK 2폭)에
맞춰 쓴다 — 표 문구는 마크다운이라 무관하지만 테스트 파일의 주석은 걸린다.

## mutcheck ⑪ ⑫ 가 빨개지는 자리 (§9)

두 변이는 `core/admission.py` 를 표적으로 하고 `tests/test_admission.py` 만 돌린다.

| 변이 | 어디서 빨개지나 |
|---|---|
| ⑪ `admission-fail-open`(「표본 없음 → hold」를 「→ 열림」으로) | **C1** 이 정면으로. 곁들여 A2 · F1 · F3 · H1 |
| ⑫ `admission-samples-any`(`all(...)` → `any(...)`) | **G3**(닫혀야 하는데 열린다)과 **G5**. G4 는 대조군이라 초록으로 남아야 한다 — 둘이 짝이어야 변이가 잡힌다 |

## 가정 (명세가 안 정한 것 — 구현이 달리 정하면 테스트를 고쳐야 한다)

1. **모르는 `policy` 문자열은 `load` 처럼 동작한다** (B4). §4.3 2 는 `== "always"` 만 말한다. 설정 검증이
   앞에서 막으므로 실제로는 도달 불가지만, `decide` 를 직접 부르는 테스트·프로브에서 fail-closed 쪽을 골랐다.
2. **timezone 없는 `at` 은 「모르는 값」으로 본다** (F14). §4.3 6 은 `parse_iso` 로 읽으라고만 했다. naive 를
   UTC 로 보고 계속 가는 구현도 말이 되고, 그러면 **창 전체가 naive 인 경우**(정상 동작)와 **섞인 경우**
   (`TypeError`)가 갈린다. 「모르면 닫는다」를 따라 둘 다 `no_sample` 로 잠갔다.
3. **뒤로 가는 `at` 은 끊긴 것으로 본다** (F17). §4.3 6 은 「이웃한 `at` 차이가 `2 × interval` 을 넘으면」
   이라고만 했다 — 부호를 안 말한다. 음수를 통과시키면 fail-open 이라 `abs` 쪽으로 잠갔다.
4. **`Hold.detail` 은 `{"cpu_busy": <값>}` dict 이고 값은 창의 가장 새 항목** (G6·G7). §4.3 7 은
   「detail=최신값」, §5.1 은 `hold_detail: dict | None`(예 `{"cpu_busy": 92.4}`)이라 둘을 합쳤다.
   `no_sample`·`cooldown` 의 `detail` 은 `None` 으로 두었다(명세가 아무 말 없다).
5. **`[host]` 자체 검증이 교차 검증보다 먼저 돈다** (J15). 오늘의 `_validate_server` 는 `[server]` 블록을
   먼저 돌고 `[host]` 를 뒤에 본다(`config.py:639-645`). 교차 검증을 `[server]` 블록에 그냥 붙이면 J15 가
   빨개진다 — 그때는 교차 검증을 `[host]` 검사 **뒤**로 옮기는 쪽이 맞다고 보고 그렇게 잠갔다.
6. **`decide` 는 인자를 안 고친다** (I8). 명세에 없지만 `history` dict 가 샘플러의 살아 있는 객체라
   위반의 대가가 크다.

## 명세에 없는 것 — 답이 필요한 질문

1. **`ServerSection` 의 네 필드 타입이 `int` 인가 `float` 인가.** `_apply_section` 이 **기본값의 타입**으로
   TOML 값을 강제하므로(`config.py:274`), `cpu_max_percent: int = 80` 이면 `cpu_max_percent = 80.5` 가
   `expected an integer` 로 죽고 `float = 80.0` 이면 통과한다. `admission_cooldown_seconds` 도 같다
   (`= 0.5` 를 허용할 것인가). §4.1 의 TOML 예시는 `80` · `30` 이고 §4.3 의 `AdmissionConfig` 는
   `float = 80.0` · `float = 30.0` 이라 **두 문서가 서로 다른 답을 준다.** J1·J2·J3 의 단언 모양이 여기 달렸다.
2. **오류 문구의 `×` 가 문자 그대로인가.** §4.1 표는
   `[server] admission_samples × [host] interval_seconds must not exceed admission_cooldown_seconds` 라고
   적었다. `×`(U+00D7)를 사용자 문구에 그대로 쓸 것인지, `*` 나 `x` 로 쓸 것인지 — 테스트가 문자열을
   정확히 비교하므로 답이 하나여야 한다. 겸사겸사 `admission` 의 문구도 `must be "load" or "always"`
   (겹따옴표)인데 옆줄의 `read_auth` 는 `must be 'none' or 'basic'`(홑따옴표)이다. 둘 중 하나로 통일할지.
3. **`Hold.detail` 의 값이 「창의 가장 새 항목」인가 「상한을 넘은 값 중 가장 새것」인가** (G7). 창이
   `[85, 70, 70]` 이면 문자 그대로는 `70` 이 실려 화면에 **`held (cpu 70%)`** 가 뜬다 — 상한이 80 인데
   70% 라고 말하는 화면은 오너를 헷갈리게 한다. 「위반값 중 최신」이나 「창의 최댓값」이 의도였는지.
4. **`no_sample` 과 `cooldown` 의 `detail` 에 무엇이 들어가는가.** §5.1 은 `hold_detail: dict | None` 만
   말하고 예시는 `cpu_busy` 뿐이다. 예컨대 `cooldown` 에 남은 초를 실으면 화면이
   「12초 남음」을 찍을 수 있다(§5.4 의 문구는 그런 값을 안 쓴다). 지금은 `None` 으로 잠갔다.
5. **`[host] history_samples < admission_samples` 인 기존 설치가 업그레이드 때 기동에 실패해도 되는가**
   (J10). 오너가 admission 키를 **하나도 안 적어도** 기본 `admission_samples = 3` 이 교차 검증에 걸린다.
   의도라면 CHANGELOG 의 「동작 변경」에 「설정에 따라 기동이 막힐 수 있다」가 한 줄 더 필요하고, 아니라면
   기본값을 `min(3, history_samples)` 로 접거나 이 조합을 경고(warn)로 내려야 한다.
6. **교차 검증이 `[host] history_samples >= 1` 보다 앞인가 뒤인가** (J15). `history_samples = 0` 은 두
   규칙을 동시에 어긴다. 어느 문구가 나오는지가 오너가 어느 섹션을 고치러 갈지를 정한다.
7. **`lane` 이 1 도 2 도 아닌 값(0 · 음수)일 때.** §4.3 1 은 `lane == 1` 이라고 적었다. 레인 번호는
   1-based 라(`worker.py:247`) 안 나야 하지만, `lane <= 1` 로 구현하면 0 도 게이트를 안 지난다.
   테스트를 쓸지 말지의 문제라 지금은 안 썼다.
8. **`sample.interval_seconds` 가 0 이하일 때의 간격 임계.** `sample_from_json` 이 `<= 0` 을 5.0 으로
   바꾸고(`hostparse.py:371-372`) 로컬은 검증 하한이 2 라 실제로는 안 나지만, 임계가 0 이 되면
   **모든 창이 끊긴 것**이 되어 그 머신이 영구 보류가 된다. 하한을 둘 것인지.
9. **`decide` 가 예외를 삼키는 범위.** §4.3 은 「모르면 닫는다」라고만 했다. F12~F15 를 `Hold("no_sample")`
   로 잠갔지만, 구현이 `try/except (ValueError, TypeError, KeyError)` 로 감쌀지 각 자리에서 `.get()` 과
   `isinstance` 로 막을지는 정하지 않았다 — 후자면 예상 못 한 새 모양에서 여전히 터진다. 순수 함수가
   **절대 예외를 안 내는 것**이 계약인지 확인이 필요하다(claim 루프 안에서 도는 함수다).
