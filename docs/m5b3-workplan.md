# M5b-3 작업 명세 — `runner.py` 분리 · `rcm worker` · 원격 실행 e2e

> `docs/m5-workplan.md` 「M5b」의 세 번째 PR. M5b-2(서버 쪽 `/worker/*` 프로토콜, `docs/m5b2-workplan.md`)가 들어간 뒤. 이 PR 이 M5 완료 기준 ④「다른 머신의 `rcm worker` 가 붙어 그 풀의 잡을 돌리고, 워커가 사라지면 그 잡은 `lost` 로 남는다」를 닫는다.
>
> 바꾸지 않는 것: 서버 API(M5b-2 그대로 — 워커가 맞춘다) · 로컬 워커의 동작(같은 `runner` 를 쓰되 결과가 같아야 한다) · 런타임 의존성 0 · 워커 → 서버 단방향.

## 1. `runner.py` — 자재화·실행·펌프·신호를 한 곳에

`worker.py` 의 `Worker.execute/_pump/_env/_signal` 에서 **잡 실행 자체**를 `runner.py` 로 뗀다. 로컬 워커(같은 프로세스, DB 직접)와 원격 워커(HTTP 보고)가 같은 코드를 쓴다. 차이는 「관찰자」뿐이다.

```python
@dataclass
class RunSpec:            # 실행에 필요한 것 전부 — 서버 DB 든 claim 응답이든 여기로 정규화
    job_id: int
    preset_name: str
    argv: tuple[str, ...]
    env: dict[str, str]           # preset.env
    env_passthrough: tuple[str, ...]
    timeout_seconds: int | None
    inputs: dict[str, Any]
    requester_label: str
    source: Source                # mode · repo · ref · base_sha · dirty · sha
    workspace: Path
    log_path: Path
    grace_seconds: int

class RunObserver(Protocol):     # 로컬: DB 에 직접 · 원격: HTTP 로 서버에
    def phase(self, phase: str) -> None: ...
    def output(self, data: bytes) -> None: ...        # raw 바이트(줄 단위 배치). 마커 파싱은 관찰자 몫
    def should_cancel(self) -> bool: ...              # 로컬: DB state == cancelling · 원격: heartbeat 의 cancel 목록
    def should_stop(self) -> bool: ...                # 프로세스 종료 신호(SIGTERM/Ctrl-C)

@dataclass
class RunResult:
    rc: int | None
    cancelled: bool
    timed_out: bool
    lost: bool                    # should_stop 으로 끊었다
    started: datetime
    finished: datetime

def run_job(spec: RunSpec, observer: RunObserver, *, now_fn, environ, materialize) -> RunResult
```

- `materialize(spec) -> None` 은 호출자가 준다(로컬: tar/manifest/git_ref 자재화 그대로 · 원격: 받은 tar 를 `extract_tree`, git_ref 는 워커의 `[[repos]]` 로 `prepare_git_ref`). 실패는 `MaterializeError` → 호출자가 failed 로 보고.
- 로컬 워커: `Worker.execute` 는 `RunSpec` 을 만들고 `_LocalObserver`(store 에 phase/마커/last_output 기록 — **마커 파싱은 관찰자가** `parse_marker` 로)를 넘긴다. `outcome_for` 는 그대로. **로컬 워커의 테스트(test_worker · test_e2e_*)가 한 글자도 안 바뀌고 통과해야 한다.**
- 펌프 규칙은 오늘 그대로: 1초 폴링 · 줄 단위 · `MAX_LINE_BYTES` · 취소/타임아웃 → SIGTERM → grace → SIGKILL · 손자 프로세스(`start_new_session` + `killpg`) · EOF 뒤 `wait(grace+5)`.

## 2. `rcm worker` — 원격 워커 프로세스

```
rcm worker --server URL --pool NAME [--lanes N] [--name NAME] [--config worker.toml] [--data DIR]
```

- 토큰: `RCM_WORKER_TOKEN`(우선) 또는 `--config` 의 `token`. 워커 토큰(kind worker)이어야 한다 — 서버가 403 을 주면 `worker token required (rcm token add NAME --worker on the server)` 로 종료 2.
- `worker.toml`(선택): `server` · `token` · `pool` · `lanes` · `data_dir`(기본 `~/.local/share/rcm-worker`) · `[[repos]]`(git_ref 프리셋용 — 이름이 서버 것과 같아야 한다) · `[host] interval_seconds · gpu · top_processes · history_samples`(호스트 샘플러). CLI 플래그가 파일보다 우선. 파싱은 `config.py` 의 `load_worker_config(path)` → `WorkerConfig`.
- 루프(스레드):
  - **등록**: `POST /worker/register {pool, lanes, host_name, version}`. 409(버전 불일치 · 등록 거부)는 메시지 그대로 출력하고 종료 2. 서버에 못 닿으면 5초 간격 재시도(무한 — 워커는 서비스다), 로그 한 줄.
  - **heartbeat 스레드**: `heartbeat_seconds`(등록 응답)마다 `POST /worker/heartbeat {jobs: [실행 중인 잡 id…], host_sample}`. 응답 `cancel` 목록은 레인 스레드에 전달(`should_cancel`), `paused` 는 claim 을 쉬게. 3번 연속 실패면 로그 경고(`server unreachable for 15s`) — 잡은 계속 돈다(서버가 timeout 뒤 lost 로 닫으면 finish 가 409 를 받고 워커는 워크스페이스를 정리한다).
  - **레인 스레드 × lanes**: `claim {lane}`(long-poll · 204 면 바로 다시) → `RunSpec` 만들기(claim 응답의 `job` · `preset`) → tree 받기(`GET /worker/jobs/{id}/tree` → `jobs/<id>/tree.tar.gz` 로 흘려 받고 `extract_tree`; git_ref 면 `[[repos]]` 없을 때 finish failed `repo 'app' is not configured on this worker`) → `phase executing` → `run_job` (`_RemoteObserver`: `output` 은 1초 배치로 `POST …/log`(octet-stream) · `phase` 는 `POST …/phase`) → `POST …/finish {outcome, exit_code}`. 보고 실패(연결)는 3회 재시도(1·2·4초) 뒤 포기하고 로그. 409(잡이 이미 닫힘)는 포기·정리.
  - **신호**: SIGTERM/SIGINT → 모든 레인 `should_stop` → 도는 잡 SIGTERM → grace → KILL → `finish {outcome: "lost"}`(summary `worker stopped`) → heartbeat 중단 → 종료 0. 두 번째 신호는 즉시 종료.
- 워크스페이스: `<data_dir>/workspaces/<job_id>` · 로그는 워커 로컬에도 `<data_dir>/jobs/<id>/log.txt`(디버그용, 서버가 정본). 성공하면 지우고 실패면 `keep_workspace_on_failure`(기본 true, 7일 뒤 지움 — 간단한 정리: 시작 시 7일 넘은 워크스페이스 삭제).
- 출력(stderr): `rcm worker build-02 · pool linux · lanes 1 · server http://…` · `claimed #511 gate:full` · `#511 succeeded in 2m 10s` · `heartbeat: server unreachable (3 failures)` — 토큰·경로 없음.
- `rcm worker --check`: 서버 `/api/health` + 워커 토큰으로 `/api/whoami`(kind worker 확인) + `[[repos]]` 의 git 접근(ls-remote) → 표로 출력, 문제면 종료 1. `rcm check` 와 같은 모양.

## 3. 클라이언트 (`client.py`)

`WorkerClient(server, token)` — `register` · `claim(lane, wait_seconds)`(204 → None) · `download_tree(job_id, dest)`(스트리밍, Content-Length 검증) · `phase` · `log(job_id, data)` · `finish` · `heartbeat(jobs, sample)`. 기존 `Client._request` 를 재사용(재시도 없음 — 재시도는 워커 루프가 정책으로). 오류는 `ClientError(status, message)`.

## 4. e2e (같은 머신에서 두 프로세스)

`tests/test_e2e_worker.py`: 서버(`lanes = 1`, 프리셋 `lin`(`pool = "linux"`, `sh -c 'echo ::rcm::steps::2; echo ::rcm::step::1 build; sleep 0.2; echo ::rcm::step_end::1 ok; echo ::rcm::step::2 test; echo ::rcm::step_end::2 ok; echo ::rcm::summary::built ok'`) · `qal`(linux, group devices, sleep 3) · `slowl`(linux, sleep 30) · `cachedl`(linux, tree 캐시)) + `rcm worker --pool linux --lanes 1`(다른 data dir · `RCM_WORKER_TOKEN`) 실제 프로세스로:
1. `rcm run lin` → succeeded · 스텝 2/2 · summary `built ok` · `/api/status` 에 `build-02/1 busy #N` 이었다가 idle · 로그 전체가 서버에 있다(`rcm logs N`).
2. 캐시 잡(`rcm run cachedl` 두 번째 업로드) → 워커가 서버가 조립한 tar 를 받아 성공.
3. `rcm run slowl --no-wait` → running → `rcm cancel N` → 워커가 heartbeat 으로 알아 SIGTERM → `cancelled` · summary `cancelled by <name>` · 3초 안에.
4. `rcm run slowl --no-wait` → running → 워커 `kill -9` → `worker_timeout_seconds = 10` 으로 15초 안에 `lost` · summary `worker build-02 unreachable for Ns` · 워커 `down` · `pools[1].lanes == 0` → 워커 다시 띄움 → 다음 잡을 받는다(재등록이 옛 잡을 건드리지 않는다 — 이미 lost).
5. 워커 SIGTERM(도는 잡 있음) → 잡 `lost` summary `worker stopped` · 워커 종료 0.
6. git_ref 잡: 워커 `[[repos]]` 로 로컬 bare 레포 fetch → succeeded(test_worker_gitref 의 fixture 재사용).
7. 서버 재시작 중에도 워커의 잡은 running 으로 남고 finish 가 들어가면 succeeded(서버가 5초 안에 다시 뜨는 경우).

## 5. 테스트 배치

| 파일 | 담당 | 무엇 |
|---|---|---|
| `tests/test_runner.py` | A | `run_job` 순수 규칙: 관찰자 호출 순서(phase executing → output 배치 → 종료) · 마커는 관찰자에게 raw 로 · 취소(should_cancel → TERM → grace → KILL, `cancelled=True`) · 타임아웃 · should_stop → `lost=True` · 손자 프로세스 · `MAX_LINE_BYTES` · 로컬 워커 회귀(test_worker 전부 그대로) |
| `tests/test_worker_client.py` · `tests/test_worker_config.py` | B | `WorkerClient` 각 메서드(가짜 서버 핸들러) · 204 · 409 · 스트리밍 다운로드 길이 검증 · `load_worker_config`(키 검증 · repos · 플래그 우선) |
| `tests/test_cli_worker.py` | B | `rcm worker` 인자 · `--check` 표 · 토큰 없음 종료 2 · 서버 403 메시지 |
| `tests/test_e2e_worker.py` | C | §4 의 1–7(실제 두 프로세스, `worker_timeout_seconds = 10`) |

규칙: `src/` 금지 · 실제 원격 없음(같은 머신 두 프로세스) · 각자 `docs/m5b3-test-scenarios-<담당>.md`.
