# M1 작업 명세 — 「보이는 것」 (2026-09-05)

PLAN.md v2.1 「마일스톤과 완료 기준」의 M1 을 구현 단위로 쪼갠 것이다. 인터페이스(함수 시그니처 · 스키마 키 · 라우트 · CLI 출력)를 여기서 확정하고, 테스트 시나리오(`docs/m1-test-scenarios.md`)와 구현이 이 문서를 같이 본다. 정본은 PLAN.md 이고 여기서 어긋나면 PLAN 이 이긴다.

M0 에서 이미 있는 것: `core/queue.eta_for_new` · `core/queue.confidence` · `core/render_text.render` · `store.markers_for` · `GET /jobs/{id}/log` 의 `X-RCM-Next-Offset`/`X-RCM-More` · `App.status()`(요청마다 DB 재구성) · `hosts: []`.

## 0. 먼저 정한 것 (Codex 크로스리뷰 → 추천값으로 진행, 오너가 바꾸면 여기서 고친다)

| # | 항목 | 결정 |
|---|---|---|
| A | 이벤트 갱신 모델 | `EventBus`(단조 증가 id · 링 버퍼 2048 · 구독자별 bounded queue, 같은 크기). 워커·업로드·취소·정지·janitor·샘플러가 `publish(kind, data)`. App 은 **DB 스냅샷 캐시**(활성 잡 · 마커 · 최근 · 표본 · paused)를 잡 이벤트 때 다시 읽고(0.2초 디바운스), `/api/status` 는 캐시 + `now` 로 순수 계산만 한다. SSE 는 버스를 구독한다 |
| B | 신뢰도 배지 | `estimate.confidence ∈ high·med·low·group wait·overdue` 를 **서버가 싣는다**(키 추가 — `schema_version` 1 유지). `rcm top`·웹은 그 값을 그대로 쓴다 |
| C | `hosts[].history[]` | **서버 메모리**(`collections.deque(maxlen=history_samples)`). 재시작하면 비운다 |
| D | GPU 픽스처 | 이 Mac(Apple Silicon, macOS 26)의 실제 `ioreg -r -d 1 -w 0 -c IOAccelerator` · `top -l 2 -n 0 -s 1` · `vm_stat` · `ps -Aro %cpu=,rss=,comm=` · `sysctl -n hw.memsize` 를 `tests/fixtures/host/macos/` 에 넣었다(사용자 경로 제거). Linux 는 `/proc/loadavg` · `/proc/meminfo` · `/proc/stat` 두 표본 · `ps -eo %cpu=,rss=,comm= --sort=-%cpu` · `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` 형식대로 합성 픽스처 + CI 의 ubuntu 러너에서 실제 `/proc` 을 읽는 스모크 테스트 |
| E | SSE 상한 초과 | 17번째 연결은 `503` + `Retry-After: 10` + `{"error": "too many event streams", "fallback": "poll", "poll_seconds": 10}`. **웹 UI** 는 503 뒤에도 `Retry-After` 와 2→30s 백오프로 SSE 를 계속 재시도하고 그 사이 10초 폴링(4절 그대로). **CLI `rcm wait`** 는 별도로 2초 폴링으로 떨어진다 |
| F | `rcm wait` 갱신 | `GET /jobs/{id}/events` SSE 를 먼저 열고, 이벤트가 올 때마다 `GET /jobs/{id}?tail=0` 을 다시 읽어 한 줄을 갱신 — **재조회는 초당 1회로 합친다**(`job_finished` 는 즉시). SSE 가 503 이거나 안 열리면 2초 폴링(M0 그대로). 5초 동안 조용하면 한 번 재조회하고 다시 연다(`--timeout` 도 이 틱에서 본다) |
| G | `rcm eta` 계산 위치 | 서버 `POST /api/eta {preset, inputs}` → `core/queue.eta_for_new` 결과(가상 잡의 큐 행 + `ahead`)를 돌려준다. 클라이언트는 그리기만 |

## 1. `core/hostparse.py` — 순수 파서 (I/O 없음)

값이 없으면 `None`(0 아님). 어느 파서든 입력이 이상하면 `None` 을 돌려주고 예외를 던지지 않는다(단, 타입이 아예 틀리면 `TypeError` 허용).

```python
PAGE_SIZE_DEFAULT = 4096

def parse_vm_stat(text: str) -> dict[str, int] | None
    # "Mach Virtual Memory Statistics: (page size of 16384 bytes)" 의 page size 를 "page_size" 키로,
    # "Pages active: 409210." → {"active": 409210, "wired": ..., "compressor": ..., "free": ..., "inactive": ..., "speculative": ...}
    # 키 이름은 소문자 + 공백→_ ("Pages wired down" → "wired", "Pages occupied by compressor" → "compressor")

def mac_memory(vm: dict[str, int] | None, total_bytes: int | None) -> dict[str, int | None]
    # {"total_bytes": total, "used_bytes": (active + wired + compressor) * page_size, "compressed_bytes": compressor * page_size}
    # vm 이 None 이면 used/compressed 는 None. total 은 그대로.

def parse_top_cpu(text: str) -> dict[str, float | None] | None
    # `top -l 2 -n 0 -s 1` 출력에서 **마지막** "CPU usage: 46.75% user, 21.8% sys, 32.16% idle" 만 쓴다(첫 표본은 부팅 이후 누적이라 버린다).
    # {"user": 46.75, "sys": 21.8, "idle": 32.16, "busy": 68.55}  busy = 100 − idle (반올림 2자리)

def parse_top_load(text: str) -> tuple[float, float, float] | None
    # "Load Avg: 5.42, 5.06, 3.45" 마지막 것

def parse_ps(text: str, limit: int = 5) -> list[dict[str, Any]]
    # 각 줄 "%cpu rss comm" (macOS `ps -Aro %cpu=,rss=,comm=`, Linux `ps -eo %cpu=,rss=,comm= --sort=-%cpu`).
    # comm 은 basename 만("/opt/flutter/.../flutter_tester" → "flutter_tester"). rss 는 KiB → rss_mb 정수.
    # cpu 내림차순 limit 개. 파싱 안 되는 줄은 건너뛴다. 결과 [{"comm": "dart", "cpu": 74.6, "rss_mb": 2881}]

def parse_ioreg_gpu(text: str) -> tuple[dict[str, Any] | None, str | None]
    # `ioreg -r -d 1 -w 0 -c IOAccelerator` 의 "PerformanceStatistics" = {...} 에서
    # "Device Utilization %" → util_pct(int), "In use system memory" → mem_used_bytes. mem_total_bytes 는 None(통합 메모리).
    # 반환 ({"util_pct": 1, "mem_used_bytes": 27443200, "mem_total_bytes": None, "source": "ioreg"}, None)
    # IOAccelerator 가 없거나 PerformanceStatistics 가 없으면 (None, "no IOAccelerator PerformanceStatistics")
    # 가속기가 여럿이면 PerformanceStatistics 가 있는 것만 모아 util 은 max, 메모리는 합(Codex M1 리뷰 8).
    # 키가 일부만 있으면 있는 것만(없는 값은 None).

def parse_sysctl_int(text: str) -> int | None

def parse_proc_loadavg(text: str) -> tuple[float, float, float] | None
def parse_proc_meminfo(text: str) -> dict[str, int | None]
    # {"total_bytes": MemTotal*1024, "used_bytes": (MemTotal − MemAvailable)*1024, "compressed_bytes": None}
    # MemAvailable 이 없으면(옛 커널) used 는 None
def parse_proc_stat_cpu(first: str, second: str) -> dict[str, float | None] | None
    # 두 /proc/stat 의 "cpu " 줄 차분. user = (user+nice)/total*100, sys = (system+irq+softirq)/total*100,
    # idle = (idle+iowait)/total*100, busy = 100 − idle. total 이 0 이면 None.
def parse_nvidia_smi(text: str) -> dict[str, Any] | None
    # "13, 594, 8192" (util %, mem.used MiB, mem.total MiB) → {"util_pct": 13, "mem_used_bytes": 594*2**20,
    #   "mem_total_bytes": 8192*2**20, "source": "nvidia-smi"}. 여러 GPU 면 첫 줄. 빈 문자열/오류 문구면 None.

def stale(sampled_at: datetime, now: datetime, interval_seconds: float) -> bool
    # (now − sampled_at) > 3 × interval  ← mutcheck ④ 표적
```

## 2. `hostsample.py` — 샘플러 스레드 (I/O)

```python
class HostSampler(threading.Thread):
    def __init__(self, config: HostSection, *, name: str, publish: Callable[[str, dict], None] | None,
                 stop: threading.Event, now_fn=..., runner: Callable[[list[str]], str | None] = _run_command,
                 read_file: Callable[[str], str | None] = _read_file, platform: str = sys.platform,
                 cpu_count: int | None = os.cpu_count(), loadavg: Callable[[], tuple] | None = os.getloadavg)
    def sample_once(self) -> HostSample | None      # 한 번 수집. 전부 실패면 None 을 돌려주고 self.error 에 사유
    def latest(self) -> tuple[list[HostSample], str | None]   # (hosts, hosts_error) — hosts 는 0 또는 1개
    def run(self)                                  # interval 마다 sample_once; publish("host_sample", {...})
```

- `runner(argv)` 는 `subprocess.run(argv, capture_output=True, text=True, timeout=8)` 의 stdout, 실패면 None. 테스트는 가짜 runner 로 픽스처를 넣는다.
- macOS: `vm_stat` · `sysctl -n hw.memsize` · `top -l 2 -n 0 -s 1` · `ps -Aro %cpu=,rss=,comm=` · `ioreg -r -d 1 -w 0 -c IOAccelerator`(gpu == "auto" 일 때만). `top` 은 1초 걸리므로 interval 하한 2 를 config 가 보장한다.
- Linux: `/proc/loadavg` · `/proc/meminfo` · `/proc/stat` 두 번(1초 간격) · `ps -eo %cpu=,rss=,comm= --sort=-%cpu` · `nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits`(있을 때만, 없으면 `gpu: null` + `gpu_note: "nvidia-smi not found"`).
- 부분 실패는 그 칸만 `None`. **cpu·memory·load 가 전부 None 이면** 표본을 만들지 않고 `hosts_error = "sampler: all collectors failed (<마지막 오류>)"`.
- `history`: deque(maxlen=history_samples) of `{"at": iso, "cpu_busy": float|None, "mem_used_bytes": int|None, "gpu_util_pct": int|None}`. 표본이 실패한 주기는 항목을 넣지 않는다(UI 가 점선으로 끊는다).
- `gpu = "off"` 면 `gpu: null`, `gpu_note: "disabled"`.
- `HostSample.interval_seconds` = 설정값. `stale` 은 `core/status.host_json` 이 `now` 로 계산한다(이미 있음: `age > 3×interval`) — `hostparse.stale()` 로 바꿔 한 곳에서만 계산.

## 3. 이벤트 버스 · 상태 캐시 · SSE (`events.py` + `server.py`)

```python
@dataclass(frozen=True)
class Event: id: int; kind: str; data: dict[str, Any]; at: datetime

class EventBus:
    def __init__(self, history: int = 2048)   # DEFAULT_HISTORY. Codex M1 리뷰(좋음 1): marker burst 대비
    def publish(self, kind: str, data: dict[str, Any], *, at: datetime) -> Event
    def subscribe(self, *, last_id: int | None = None, maxsize: int | None = None) -> Subscription
        # maxsize 기본은 링 버퍼 크기(2048)와 같다(Codex M1 리뷰 좋음 2)
        # last_id 가 링 버퍼 안이면 그 뒤 이벤트를 먼저 큐에 채운다(재연결 재생). 밖이면 "reset" 이벤트 하나를 넣는다.
    def unsubscribe(self, sub: Subscription) -> None
    @property
    def subscriber_count(self) -> int
    @property
    def last_id(self) -> int

class Subscription:
    def get(self, timeout: float) -> Event | None      # 타임아웃이면 None
    # 큐가 가득 차면 가장 오래된 것을 버리고 "lag" 이벤트를 넣는다(구독자가 재조회하게)
```

**발행 지점(고정)**: `App` 만 발행한다(`store` 는 모른다).

| 언제 | 이벤트 |
|---|---|
| `POST /jobs` 새 잡 · 합류자 추가 | `job_changed` |
| `PUT tree` 완료(queued) · 413 · 수신 중 끊김 · janitor 포기 | `job_changed` / `job_finished` |
| `POST cancel`(취소·cancelling·합류자 이탈) | `job_changed` / `job_finished` |
| `POST /pause`·`/resume` · 워커 상태 변화 | `server` |
| 워커 claim(running) · materializing→executing · 종료 | `job_changed` / `job_finished` (워커 `on_change`) |
| 마커 수신 | `marker` (워커 `on_marker`) |
| 서버 시작 정리(lost · cancelled) | `job_finished` |
| 샘플러 표본 | `host_sample` |
| 업로드 진행(`received_bytes`) · `last_output_at` | 이벤트 없음 — 캐시 TTL(0.2초)이 잡는다 |

이벤트 종류와 data:
- `job_changed` `{job_id, state}` — 생성 · uploading→queued · claim(running) · cancelling · 합류자 변경 · phase 변경
- `job_finished` `{job_id, state, exit_code}` — 종료 상태로 들어갈 때(취소·포기·lost 포함)
- `marker` `{job_id, kind, value}` — 스텝 마커 수신
- `host_sample` `{name, sampled_at}` — 샘플러가 표본을 만들 때
- `server` `{paused: {...}|null, workers: [...]}` — 정지/재개 · 워커 상태 변화
- `reset` `{}` — Last-Event-ID 가 링 버퍼 밖: 현재 `last_id` 부터 새 이벤트만 구독되고, 클라이언트는 즉시 전체 재조회 · `lag` `{}` — 구독 큐가 넘쳐 **큐를 비우고** lag 하나 + 방금 이벤트만 남김: 클라이언트는 전체 재조회. 링 버퍼 기본 2048, 구독 큐도 같은 크기

워커·서버·store 가 발행하는 지점: `store` 는 모른다(순수 저장). `App` 이 `store` 호출 뒤에 발행한다. 워커는 `on_change(job_id)` 콜백(M0 에 이미 있음)을 통해 `App` 이 잡을 다시 읽고 `job_changed`/`job_finished`/`marker` 를 발행한다 — 워커에 `on_marker(job_id, kind, value)` 콜백을 추가한다.

App 의 상태 캐시:
```python
class App:
    self.bus: EventBus
    self.sampler: HostSampler | None
    self._snapshot: _DbSnapshot | None     # jobs(active) · markers · recent · samples(medians) · paused · loaded_at
    self._dirty: bool                      # 잡 이벤트가 오면 True
    def _load_snapshot(self) -> _DbSnapshot # DB 4 쿼리
    def status(self, token) -> dict        # dirty 이거나 0.2초 넘게 지났으면 다시 읽고, 아니면 캐시로 순수 계산
```
`/api/status` 의 나머지 규칙(null + *_error · log_tail 토큰 조건)은 그대로. `hosts` 는 `sampler.latest()`.

라우트 추가:
| 라우트 | 인증 | 동작 |
|---|---|---|
| `GET /events` | `read_auth` | SSE. `event: <kind>` · `id: <n>` · `data: <json>`. 처음에 `event: hello` `{last_id, generated_at, server: {version, uptime_seconds}}`. 15초마다 `: keep-alive`. `Last-Event-ID` 헤더 재생. 동시 연결 상한 `sse_max_connections`(기본 16, `[server]` 설정 추가) 초과는 503 + `Retry-After: 10` + `{"error":"too many event streams","fallback":"poll","poll_seconds":10}` |
| `GET /jobs/{id}/events` | 없음 | 같은 SSE 인데 `job_changed`·`job_finished`·`marker` 중 그 잡 것만 + `hello`. 잡이 이미 종료 상태면 `hello` 뒤에 곧바로 `job_finished` 하나를 보내고 닫는다 |
| `POST /api/eta` | `read_auth` | `{preset, inputs}` → 검증 → `core/queue.eta_for_new` → `{"job": <가상 잡의 큐 행 json — id 는 null, position 은 대기 잡 수+1>, "ahead": N, "generated_at"}`. 모르는 프리셋·입력은 400 |

SSE 핸들러: 소켓 타임아웃을 없애고(`settimeout(None)`), 요청 세마포어는 잡지 않는다(대신 `sse_max_connections`). 쓰기 실패(BrokenPipe/ConnectionReset)면 조용히 구독 해제. `HEAD` 는 405.

## 4. 스키마 v1 추가 키 (삭제·의미 변경 없음)

- `queue[].estimate.confidence`: `"high" | "med" | "low" | "group wait" | "overdue"` — `core/queue.confidence(source, n, group_wait=reason=="blocked_by_group", overdue=overdue or stuck)`.
- `hosts[]` 가 실제 표본으로 채워진다(모양은 PLAN 예시 그대로). `hosts[].history[]` 항목 `{at, cpu_busy, mem_used_bytes, gpu_util_pct}`.
- `server.sse_connections`(정수) — 진단용.
- 설정 `[server] sse_max_connections = 16` · `sse_keepalive_seconds = 15`.

## 5. CLI (`cli.py` · `client.py`)

| 명령 | 동작 · 출력 |
|---|---|
| `rcm eta (--job ID \| PRESET [-f K=V…]) [--json]` | `--job`: `/api/status` 에서 그 행. 아니면 `POST /api/eta`. stdout 한 줄: `#413 · 2nd in line · 1 ahead · wait 2m 40s · expected 9m 00s · eta 10:04 · low · preset`. `--json` 이면 행 JSON. 모르는 값은 `—`. 정지·레인 0 이면 `eta —` 와 이유 |
| `rcm top [--watch N] [--json]` | `render_text.render(status, tz=로컬)`. `--watch N` 은 N 초마다 화면을 지우고 다시(Ctrl-C 로 종료 0). `--json` 은 `/api/status` 그대로 한 번 |
| `rcm jobs [--mine] [--state S] [--json]` | 큐 + 최근을 표로: `#id state key requester elapsed/wait eta summary`. `--mine` 은 토큰 필요, `requester.name == me` 또는 `joiners[].name == me`. `--state` 는 running·queued·… 필터 |
| `rcm logs ID [--follow]` | `GET /jobs/{id}/log?offset=` 증분을 stdout 에 그대로. `--follow` 는 `X-RCM-More: 1` 인 동안 1초 간격. 토큰 필수. 종료 코드: 정상 0 · 서버가 거부(401/403/404) 2 · 네트워크 불명 3 · Ctrl-C 130 |
| `rcm presets` | 이름 · 설명 · 입력 스키마 · timeout · expected · 그룹 표 |
| `rcm wait` | 결정 F: SSE 우선 → 폴링 폴백. 종료 코드는 M0 그대로. `--poll` 플래그로 SSE 끄기 |
| `rcm run` | 변화 없음(내부에서 `_wait` 가 SSE 를 쓴다) |

`client.py` 추가: `Client.events(path) -> Iterator[Event]`(urllib 스트리밍, `readline` 으로 파싱, `Last-Event-ID` 지원) · `Client.eta(preset, inputs)` · `Client.log_follow(job_id)`.

## 6. mutcheck 추가

④ `hostparse.stale`: `> 3 * interval` → `> 0 * interval`(항상 stale) — test_hostparse 가 잡는다.
⑤ `hostparse.parse_top_cpu`: 마지막 표본 대신 첫 표본을 쓰게 — 픽스처의 두 표본이 다르므로 잡힌다.

## 7. 테스트 (서브에이전트가 `docs/m1-test-scenarios.md` 와 함께 만든다)

- `tests/test_hostparse.py`: 실제 macOS 픽스처 5종 + Linux 합성 픽스처(`tests/fixtures/host/linux/`) · 빈 입력 · 깨진 줄 · GPU 없음 · `nvidia-smi` 오류 문구 · stale 경계(정확히 3×interval 은 stale 아님).
- `tests/test_hostsample.py`: 가짜 runner/read_file 로 macOS·Linux 경로 · 부분 실패 → 그 칸만 None · 전부 실패 → hosts_error · gpu off · history 길이·건너뜀 · publish 호출.
- `tests/test_events.py`: 버스 발행/구독 · Last-Event-ID 재생 · 링 버퍼 밖 → reset · 큐 넘침 → lag · 구독 해제.
- `tests/test_server.py` 추가: `GET /events` 로 이벤트 한 개 받기(제출하면 `job_changed`) · `hello` · keep-alive · 상한 초과 503 폴백 · `GET /jobs/{id}/events` 종료 잡 즉시 `job_finished` · `POST /api/eta` · `estimate.confidence` · `hosts[]` 가 샘플러 값(가짜 runner 주입).
- `tests/test_cli_m1.py`: `rcm eta`/`top`/`jobs`/`logs --follow`/`presets` 출력 · `rcm wait` SSE 경로와 폴백(서버가 SSE 를 503 으로 거부할 때).
- `tests/test_e2e_loopback.py` 확장: `rcm top` 에 호스트 표본과 위치·ETA·스텝이 보인다 · 두 세션 합류.
- Linux 스모크: `sys.platform.startswith("linux")` 일 때만 실제 `/proc` 을 읽어 `sample_once()` 가 None 이 아니다.

## 7b. 오너 확인 대기 (Codex 리뷰가 사람 결정이라고 본 것 — 추천값으로 구현, PLAN 결정 항목 19·20)

- macOS 메모리 「used」 = `active + wired + compressor`(Activity Monitor 「Memory Used」) — `top` 의 PhysMem used 와 다르다.
- GPU 를 못 읽는 머신은 `gpu: null` + `gpu_note` 로 M1 완료 기준을 통과한 것으로 본다.

## 8. 완료 기준 (PLAN M1)

다른 컴퓨터에서 Tailscale 로 `rcm run` 을 넣고 `rcm top` 에 위치·ETA·스텝·GPU 가 보인다 · 같은 트리를 두 세션이 넣으면 두 번째는 합류한다. 루프백 e2e 로 대체 확인하고, 실기 확인 절차는 README 「Verify on the real build machine」에 적는다(오너가 실행).
