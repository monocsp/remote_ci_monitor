# M5q 테스트 시나리오 B — 원격 워커가 서버 버전을 따라간다 (PR B1 · B2, 2026-11-19)

`docs/m5q-workplan.md` 의 §3(원격 워커) · §5.3(와이어) · §5.4(`remote_worker.py`) · §6 의 W 표와 R⑧ ·
§13(P0-3 · P1-5 · P2-7)을 표로 옮겼다. 중심 결정은 171 이고 154~158 · 161 · 162 를 함께 본다.
`remote_worker.py`(`run` · `_lane_loop` · `register` · `heartbeat_once` · `_RemoteObserver.should_stop`),
`remote_workers.py`(`worker_register` · `worker_claim` · `worker_heartbeat` · `worker_finish` ·
`mark_lost_workers`), `store.py`(`mark_lost_for_worker` · `forget_workers`), `cli.py`(`cmd_worker`)와
`examples/` 의 워커 서비스 파일 둘을 읽으며 찾은 경우는 근거 칸에 「코드」와 위치를 적었다. 구현보다 먼저
쓴다(test-first). 따라가기 자체의 규칙(훑기, 설정 우선순위, 받기 · 풀기 · BOOT)은 PR A 의 F · R 표가 잠근다.
여기서는 워커가 그 규칙을 언제 부르는지, 그리고 부른 뒤에 잡을 하나도 잃지 않는지를 본다.

| 약칭 | 파일 | 무엇을 잠그나 |
|---|---|---|
| srv | `tests/test_server_m5q_worker.py` | 등록 409 본문과 heartbeat 응답의 `server_version` · 버전 검사와 lost 처리의 순서 · claim 이 버전을 보지 않는 것 |
| start | `tests/test_cli_worker_m5q.py` | `cmd_worker` 의 기동 따라가기(①) · 기동 등록 409(②) · 고정과 installed 갈래 · 재실행 결정과 그 인자 |
| drain | `tests/test_worker_m5q_drain.py` | 같은 프로세스의 실제 `RemoteWorker` 와 실제 서버로 드레인(③ · ④) · 300초 상한 · 오락가락하는 버전 |
| e2e | `tests/test_e2e_worker_m5q.py` | 서브프로세스 워커의 진짜 `execve` · 같은 PID · 감독자 흉내 · 서버 업그레이드 |
| lock | 기존 `tests/test_worker_api.py` · `tests/test_cli_worker.py` · `tests/test_server_m5n.py` | 409 문장과 heartbeat 의 기존 키를 그대로 둔다 |
| 실기 | §7 의 10단계 | launchd · systemd 의 재시작 횟수 |

## 공통 준비

- **서버**: `test_cli_worker.make_server(tmp_path, worker_heartbeat_seconds=1, worker_claim_wait_seconds=2)`.
  `test_worker_api.WorkerServer` 에 CLI 프리셋을 더한 것이다. 주입 시계 `srv.clock` 은 T0 에 멈춰 있어서
  시계를 밀지 않으면 서버가 워커를 down 으로 보지 않는다. heartbeat 1초는 드레인 시험이 5초씩 기다리지 않게 한다.
- **서버 버전 올리기** `bump(srv, v)`: `srv.app.version = v` 로 바꾸고 `srv.app._wheel_done = False` ·
  `_wheel = None` · `_wheel_error = None` 으로 돌려 wheel 을 다시 조립하게 한다. `remote_ci_monitor.server.build_wheel`
  은 monkeypatch 로 `follow_helpers.fake_wheel`(§6)로 바꾼다. 진짜 `build_wheel` 은 패키지 파일을 그대로 싣기 때문에
  풀면 `__init__.py` 의 버전이 `v` 가 아니어서 `version_mismatch` 가 난다(코드 `clientwheel.py:149`).
  `test_cli_worker.py:424` 가 wheel 없이 `srv.app.version = "9.9.9"` 로 같은 일을 하는 선례다.
- **버전 이름**: 아래 표의 0.8.0 은 설치본의 `__version__` 이다. 0.8.1 · 0.8.2 는 그것과 다른 문자열이다.
- **설치 종류**: 시험 venv 는 editable 이라 기본값이 `installed` 다(결정 156). 따라가는 갈래는 `follow.install_kind`
  를 `"wheel"` 로 바꾸거나 `RCM_CLIENT_VERSION=follow` 를 준다. `tests/conftest.py`(§6)가 `XDG_CACHE_HOME` 과
  `XDG_CONFIG_HOME` 을 `tmp_path` 아래로 돌린다.
- **워커(같은 프로세스)**: `RemoteWorker(WorkerConfig(server=url, token=srv.tokens["build-02"], pool="linux",
  lanes=2, data_dir=str(tmp_path / "w")), log=logs.append)` 를 스레드로 `run()` 한다. `client` 를 넘기지 않으니
  진짜 `WorkerClient` 가 HTTP 로 말한다. `draining` 과 `stopping` 은 `set()` 시각을 적는 `threading.Event`
  하위 클래스로 바꿔 두고 순서를 본다.
- **잡 붙잡기**: 프리셋 `hold`(pool linux)의 스크립트는
  `trap 'touch $M/term' TERM; touch $M/started; while [ ! -e $M/go ]; do sleep 0.05; done` 이다. `$M` 자리에
  `tmp_path` 안의 절대 경로를 적는다. `go` 를 만들면 잡이 0 으로 끝난다. `term` 이 생겼으면 잡이 SIGTERM 을 받았다.
- **요청 세기**: `App.worker_register` · `worker_claim` · `worker_heartbeat` · `worker_finish` · `health` ·
  `client_wheel_bytes` 를 감싸 호출 시각과 그때 워커의 `draining` 값을 적는다.
- **가짜 execve**: 받은 `(path, argv, env)` 를 적고 `Execed` 예외를 던진다. `cli._main(argv, follow_enabled=True,
  execve=fake)` 로 `cmd_worker` 에 넣는다(§5.2). `main(argv)` 처럼 목록을 넘기면 결정 162 로 따라가지 않는다(Q3).
- **서브프로세스 워커(진짜 execve)**: `[sys.executable, "-m", "remote_ci_monitor.cli", "worker", "--server", URL,
  "--pool", "linux", "--lanes", "1", "--data", DIR]` 를 띄운다. 환경은 `RCM_WORKER_TOKEN`, `RCM_CLIENT_VERSION=follow`,
  `tmp_path` 아래 XDG 두 개, `PYTHONUNBUFFERED=1` 이고 stdout 과 stderr 는 한 파일로 받는다. `-m` 진입은 `main()` 을
  인자 없이 부르므로 따라가기가 켜진다. 서버는 pytest 프로세스 안의 `WorkerServer` 라서 `bump` 가 그대로 듣는다.
- **서버 업그레이드(e2e)**: `test_e2e_worker.make_stack` 이 있다(`tests/test_e2e_worker.py:611`). 그 `E2EServer` 는
  설치본 버전의 `rcm serve` 만 띄운다. 그래서 심 `tests/m5q_serve_as.py VERSION serve --config …` 를 둔다. 심은
  `remote_ci_monitor.__version__` 을 VERSION 으로 바꾸고 `remote_ci_monitor.server.build_wheel` 을 `fake_wheel` 로
  바꾼 뒤 `cli.main()` 을 부른다. `server.py` 가 `__version__` 을 import 할 때 값을 묶으므로 바꾸기가 그보다 먼저여야
  한다. `E2EServer` 에 `version=` 인자를 더해 이 심으로 띄운다. 같은 `data_dir` 과 포트로 다시 띄우면 §7 의 10-2
  단계와 같다.
- **감독자 흉내**: pytest 안의 스레드가 서브프로세스 워커를 띄우고 기다린다. 0 이 아닌 코드로 끝나면 0.2초 뒤 다시
  띄운다. launchd `KeepAlive.SuccessfulExit = false` 와 systemd `Restart=on-failure` 의 규칙이다(코드
  `examples/launchd/com.remote-ci-monitor.worker.plist` · `examples/systemd/rcm-worker.service`). 띄운 횟수와 PID 를
  적는다.
- **같은 PID 의 증거**: exec 는 명령줄을 바꾼다. 재실행 전 `ps -o command= -p PID` 는 `-m remote_ci_monitor.cli worker`
  이고 재실행 뒤에는 `-P -c` 다.
- **300초 시계**: `RCM_WORKER_RESTARTED_AT` 을 판정하는 지금 시각은 주입한다. 주입점의 이름은 명세에 없다(Q9).

## 베스트

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| B1 | 기동하면 health 를 새로 읽고 따라간다 | 설치 종류 `wheel`, `bump(srv, "0.8.1")`. `follow.json` 에 그 서버의 1분 전 기록(버전 0.8.0)을 둔다. `_main(worker_argv(srv), follow_enabled=True, execve=fake)` | 기록을 쓰지 않고 health 를 1번 부른다. `/client/` 1번, 등록 0번(따라가기가 등록보다 먼저다). exec 인자는 `[python, "-P", "-c", BOOT, "worker", "--server", URL, "--pool", "linux"]` 이고 환경에 `RCM_FOLLOWED=0.8.1` 과 `RCM_WORKER_TOKEN` 이 있다. 캐시에 `0.8.1-<sha8>` 폴더와 표지가 생긴다 | §3.2 ① · §13 P2-7 · W1 | start |
| B2 | 진짜 exec 뒤 0.8.1 로 등록한다 | B1 을 서브프로세스 워커로 | 자식의 등록 본문 `version` 이 0.8.1 이고 서버의 워커 행 `version` 도 0.8.1 이다. 자식은 health 를 다시 부르지 않는다(전체 1번). PID 가 처음 띄운 것과 같다 | W1 · §2.5 BOOT · 결정 161 | e2e |
| B3 | 버전이 같으면 아무 일도 없다 | 서버 = 설치본, 설치 종류 `wheel` | health 1번, `/client/` 0번, exec 0번, 등록 1번. stderr 에 따라가기 줄이 없다 | §3.2 ① · §2.1 의 6 | start |
| B4 | 기동 등록 409 에서 따라간다 | 설치 종류 `wheel`. `follow.server_info` 를 감싸 health 를 읽은 직후 `bump(srv, "0.8.1")` 한다(① 은 같은 버전을 봤고 등록 전에 서버가 바뀐 경우) | 등록 1번이 409 이고 본문 `server_version = "0.8.1"`. 이어서 health 1번, `/client/` 1번, exec 1번. 종료 2 가 없고 stderr 에 409 문장이 찍히지 않는다. 서버에 워커 행이 없다 | §3.2 ② · W2 | start |
| B5 | 서버가 늦게 떠도 ② 로 따라간다 | `make_stack` 의 서버를 심으로 0.8.1 에 두되 서브프로세스 워커보다 3초 늦게 띄운다 | ① 은 `cannot reach the server` 경고 한 줄을 쓰고 설치본으로 계속한다. 등록은 5초마다 다시 시도하고(코드 `remote_worker.py:303`) 서버가 뜨면 409 를 받아 따라간다. 결국 0.8.1 로 등록되고 PID 가 같다 | §3.2 ② · §2.6 · 결정 157 | e2e |
| B6 | 고정 버전이 서버와 같으면 받는다 | `RCM_CLIENT_VERSION=0.8.1`, 서버 0.8.1, 캐시 빔 | §2.7 의 3 갈래로 받아 exec 한다. 등록 409 가 없다 | §3.2 · §2.7 · 결정 158 | start |
| B7 | ③ 드레인 · 도는 잡이 없다 | 같은 프로세스 워커, 레인 둘 다 long-poll 중. `bump(srv, "0.8.1")` | 다음 heartbeat 응답의 `server_version` 을 보고 `draining` 이 켜진다. 그 뒤에 시작한 claim 이 0번이다. 두 레인이 long-poll 을 마치고 빠진 뒤에야 `stopping` 이 켜진다(`draining.set` < 마지막 레인 종료 < `stopping.set`). `run()` 이 0 을 돌려주고 `restart_for = "0.8.1"` 이다. 드레인 시작부터 `claim_wait_seconds + 2` 초 안에 끝난다(mutcheck: 레인 맨 위의 `draining` 검사를 지운다) | §3.3 의 1~4 · §5.4 · W5 | drain |
| B8 | 설치본 부트로더로 재실행한다 | B7 을 `_main(..., execve=fake)` 로 | fake 가 한 번 불린다. `argv[:3] == [sys.executable, "-P", "-c"]` 이고 코드 문자열에 캐시 경로가 없으며 나머지 인자는 원래 인자와 글자까지 같다. 환경에 `RCM_FOLLOWED` · `RCM_FOLLOWED_PATH` · `RCM_FOLLOWED_FROM` 이 없고 `PYTHONPATH` 를 더하지 않았다. `RCM_WORKER_RESTARTED_AT` 은 지금 epoch 초(±2)다. `RCM_WORKER_TOKEN` · `RCM_CLIENT_VERSION` · `PYTHONUNBUFFERED` 는 그대로다(mutcheck: 따라가기 변수 지우기를 뺀다) | §3.3 의 5 · W5 | start |
| B9 | ③ 드레인 · 한 레인이 잡을 도는 중 | 레인 1 이 `hold` 잡 #1 을 돈다(`started` 확인). `bump`. 드레인이 켜진 뒤 `ok` 잡 #2 를 큐에 넣는다 | #2 는 이 워커가 집지 않고 queued 로 남는다. 드레인 동안 `_RemoteObserver.should_stop()` 이 거짓이고 `term` 이 생기지 않는다(코드 `remote_worker.py:163`). `go` 를 만들면 #1 의 finish 가 200 이고 `succeeded` · `exit_code 0` 이며 요약이 `worker stopped` 가 아니다. finish 보고가 끝난 뒤에 `stopping` 이 켜지고 `restart_for` 가 남는다(mutcheck: 드레인이 `stopping` 을 켠다) | §3.3 의 2 · §13 P0-3 · W6 | drain |
| B10 | 재실행 뒤 등록이 남은 잡을 lost 로 만들지 않는다 | B9 를 서브프로세스 워커로. `store.mark_lost_for_worker` 를 감싸 돌려준 목록을 적는다 | 재등록 때 그 목록이 비어 있다. #1 은 `succeeded` 그대로이고 서버 로그에 `re-registered: lost=` 가 없다. #2 는 재실행한 같은 PID 의 프로세스가 집어 `succeeded` 가 된다 | §3.3 · 코드 `remote_workers.py:400`(등록은 그 워커의 running · cancelling 잡을 전부 lost 로 닫는다) | e2e |
| B11 | ④ 레인 하나만 409 | 레인 1 이 `hold` 를 돈다. 서버 DB 에서 워커 행을 SQL 로 지운다. `forget_workers` 는 바쁜 워커를 지우지 않으므로 직접 지운다(실제로는 DB 를 백업으로 되돌린 경우다). `bump` 한 뒤 레인 2 의 다음 claim | 레인 2 가 409 `not registered` 를 받고 다시 등록하다가 버전 409(본문 `server_version`)를 받아 `draining` 을 켠다. `WorkerExit` 로그가 없고 `stopping` 도 꺼져 있다. 레인 1 의 잡은 `term` 없이 계속 돌고 `go` 뒤 finish 가 200 이다(`_owned_active` 는 등록을 보지 않는다). 그 뒤 `restart_for = "0.8.1"` | §3.2 ④ · §13 P1-5 · W7 · R⑧ · 코드 `remote_workers.py:579` | drain |
| B12 | 서버 업그레이드를 그대로 흉내 낸다 | `make_stack` 과 심으로 서버 0.8.1. 서브프로세스 워커가 ① 로 0.8.1 이 된다. `slowl` 잡이 도는 중에 서버를 SIGTERM 으로 멈추고 같은 `data_dir` · 포트로 0.8.2 심을 띄운다 | 잡은 lost 가 아니라 `succeeded` 다(`recover_on_start` 는 원격 잡을 건너뛴다. 기존 `test_server_restart_keeps_the_remote_job_running…` 와 같다). 워커는 다시 붙은 heartbeat 에서 0.8.2 를 보고 드레인한다. 잡 보고가 끝난 뒤 재실행해 0.8.2 로 등록한다. PID 가 같고 감독자 흉내가 띄운 횟수는 1 이다 | §7 의 10-1~10-3 · W6 · W15 | e2e |
| B13 | 서버가 내려가면 따라 내려간다 | 설치본 0.8.0, 따라간 자식이 0.8.1 로 돈다(서브프로세스). `bump(srv, "0.8.0")` | heartbeat 의 0.8.0 이 지금 도는 0.8.1 과 달라 드레인한다. 재실행한 설치본이 ① 에서 서버와 같음을 보고 exec 없이 0.8.0 으로 등록한다. 그동안 `/client/` 요청은 0번이다 | 결정 150 · §3.3 「다음 판정은 언제나 설치본 부트로더가 한다」 | e2e |
| B14 | 같은 PID 이고 감독자가 다시 띄우지 않는다 | 감독자 흉내 아래 서브프로세스 워커에 ③ 을 일으킨다 | 재실행 전후 PID 가 같다. `ps -o command=` 가 `-m remote_ci_monitor.cli worker` 에서 `-P -c` 로 바뀐다. 띄운 횟수가 1 이다. 한 로그 파일에 드레인 줄과 두 번째 기동 줄(`rcm worker build-02 · pool linux …`)이 순서대로 있다 | §3.4 · W15 | e2e |
| B15 | 등록 409 본문의 `server_version` | `srv.register("build-02", version="0.0.1")` | 409 이고 `error` 는 오늘과 글자까지 같다(`worker version 0.0.1, server <V> — install the same release`). 본문에 `server_version = srv.app.version` 이 더해진다. `bump` 뒤에는 새 값이다 | §5.3 · 기존 `test_register_rejects_a_different_release` | srv · lock |
| B16 | heartbeat 응답의 `server_version` | 등록된 워커의 heartbeat | 200 이고 `server_version` 이 `/api/health` 의 `version` 과 같다. 기존 키 `cancel` · `paused` · `timeout_seconds` · `generation` 이 그대로 있다. `bump` 하면 다음 응답부터 새 값이다 | §5.3 · §3.2 | srv · lock |
| B17 | 버전 409 는 도는 잡을 닫지 않는다 | `running_job(srv)` 로 build-02 가 잡 #1 을 집었다. `bump` 한 뒤 같은 토큰이 옛 버전으로 등록한다 | 409 이고 #1 은 `running` 그대로이며 이벤트도 없다. 버전 검사(389행)가 `mark_lost_for_worker`(400행)보다 먼저이기 때문이다. ④ 드레인이 이 순서에 기댄다(mutcheck: 두 줄의 순서를 바꾼다) | §3.3 · 코드 `remote_workers.py:389·400` | srv |
| B18 | claim 은 버전을 보지 않는다 | 0.8.0 으로 등록한 뒤 `bump`, queued 잡 하나 | 옛 버전 워커의 claim 이 200 으로 잡을 받는다. 드레인 전과 300초 억제 동안 잡이 계속 흐르는 근거다 | §3.1 · 코드 `remote_workers.py:494` | srv |

## 에러

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| E1 | ② 에서 sha256 이 다르다 | B4 의 준비에 `App.client_wheel_bytes` 를 감싸 한 바이트를 뒤집는다 | 종료 2 이고 stderr 의 문장에 `sha256 mismatch` 가 있다. `clients/` 아래 대상 폴더도 `.tmp-*` 도 없다. exec 0번. `/client/` 요청 수는 Q6 | §3.2 ② · W3 · 결정 157 | start |
| E2 | ① 이 실패해도 설치본으로 계속한다 | 서버 = 설치본. health 처리를 4초 붙잡아 3초 제한을 넘긴다 | `not following … cannot reach the server — running installed` 한 줄을 쓰고 등록한다. 등록은 200 이고 레인이 잡을 받는다 | §2.6 · §3.2 ① | start |
| E3 | ② · 고정 워커 | `RCM_CLIENT_VERSION=0.8.0`(설치본과 같다), 서버 0.8.1 | ① 은 §2.7 의 1 갈래라 서버에 묻지 않는다(health 0번). 등록 409 뒤 종료 2 이고 문장에 서버의 409 문장과 `pinned` 가 있다. `/client/` 0번 | §3.2 「고정한 워커」 · W4 | start |
| E4 | ② · installed · editable 워커 | (가) `RCM_CLIENT_VERSION=installed` (나) 한 줄 파일 `installed` (다) `--no-follow` (라) 설정 없는 editable | 넷 다 health 0번, `/client/` 0번이고 종료 2 와 오늘의 문장이다. 기존 `test_cli_worker.py:422` 가 고치지 않고 통과한다. editable 이유를 문장에 붙이나는 Q19 | §3.2 · 결정 156 | start · lock |
| E5 | ② 본문에 `server_version` 이 없다 | B1 이전 서버 흉내. `App.worker_register` 를 감싸 409 의 `server_version` 을 지운다. ① 은 같은 버전을 보게 한다(B4 의 감싸기) | 따라가지 않는다. 종료 2 와 오늘의 문장, `/client/` 0번 | §3.2 「옛 워커는 무시한다」 | start |
| E6 | `server_version` 위조 | 409 본문과 heartbeat 응답의 값이 `../x` · `0.8.1/../../x` · 65자 · 정수 7 · `null` | 등록 409 는 따라가지 않고 종료 2 다. heartbeat 는 드레인을 켜지 않는다. 캐시 경로를 만들거나 `/client/` 를 부르지 않는다. 형식 검사의 자리는 Q10 | §2.4 `VERSION_RE` · 결정 153 | start · drain |
| E7 | 300초 안에 다시 온 ③ | `RCM_WORKER_RESTARTED_AT = now − 60`, `bump`, heartbeat 열 번 | 드레인하지 않는다. 억제 로그는 한 번이다. claim 은 계속된다(B18). 주입 시계를 `now + 241`(재실행 뒤 301초)로 밀면 다음 heartbeat 에서 드레인한다(Q9) | §3.3 고리 막기 · W8 | drain |
| E8 | `RCM_WORKER_RESTARTED_AT` 이 이상하다 | `abc` · 빈 문자열 · `-1` · `now + 3600` | 앞의 셋은 값이 없는 것처럼 보고 드레인한다. 미래 값은 Q9 | §3.3 | drain |
| E9 | ④ 는 상한과 상관없이 드레인한다 | `RCM_WORKER_RESTARTED_AT = now − 10`, B11 의 준비 | 드레인한다. 등록이 막힌 레인은 어차피 일을 못 한다 | §3.3 「④ 는 상한과 상관없이」 | drain |
| E10 | 재실행 exec 가 `OSError` | (가) B8 에서 fake 가 `OSError(ENOENT)` 를 던진다. (나) 서브프로세스 워커를 임시 심링크 인터프리터로 띄우고 ③ 전에 심링크를 지운다(Homebrew 가 옛 인터프리터를 지운 경우) | (가) 로그 한 줄에 이유가 있고 종료 1 이다. 잡은 이미 다 보고됐다. (나) 감독자 흉내의 띄운 횟수가 2 가 되고 새 프로세스가 ① 부터 다시 해 등록한다 | §3.4 끝 · W9 | start · e2e |
| E11 | 기동 exec(캐시로)가 `OSError` | B1 에서 fake 가 `OSError(E2BIG)` 를 던진다 | Q6 의 답대로. 적어도 트레이스백 없이 이유를 한 줄로 쓴다 | §2.6 표에 없는 경우 | start |
| E12 | 드레인 중 SIGTERM | B9 에서 드레인이 켜진 뒤 `worker.stop()`(서브프로세스면 SIGTERM) | 오늘의 정지 경로다. #1 에 `term` 이 생기고 lost `worker stopped` 다. `restart_for` 가 없고 execve 0번, 종료 0 | §3.3 끝 · W13 | drain · e2e |
| E13 | 드레인이 끝난 뒤 exec 직전의 SIGTERM | `RemoteWorker.run` 을 감싸 원래 `run()` 이 `restart_for` 를 남기고 돌아온 직후 `signal.raise_signal(SIGTERM)` | exec 하지 않는다. 다만 `run()` 이 드레인 끝에 `stopping` 을 켜므로 오늘의 처리기는 이 신호를 두 번째 신호로 보고 `signal 15 again: exiting now` 와 `os._exit(1)` 로 간다. 문장과 종료 코드는 Q18 | §3.3 · 코드 `cli.py:2834~2842` | start |
| E14 | ④ · 고정이나 installed 워커 | B11 의 준비에 `RCM_CLIENT_VERSION=0.8.0` 또는 `installed` | 드레인하지 않는다. 로그에 서버의 409 문장이 있고 고정이면 `pinned` 가 붙는다. 오늘처럼 `stopping` 이 켜져 레인 1 의 잡이 `term` 을 받고 lost `worker stopped` 가 된다. `run()` 은 0 을 돌려준다(861행). 종료 0 이면 감독자가 다시 띄우지 않는다(Q1) | §3.2 · W12 · R⑧ · 코드 `remote_worker.py:497~505` | drain |
| E15 | 드레인 중 finish 보고가 실패한다 | B9 에서 `App.worker_finish` 가 503 을 네 번 낸다 | `report()` 가 1 · 2 · 4초 재시도 뒤 포기한다(코드 `remote_worker.py:261`). 드레인은 그대로 끝나 재실행하고 재등록이 #1 을 lost `worker restarted without job` 으로 닫는다. 보고될 때까지 재실행을 미룰지는 Q11 | §3.3 의 2 · 코드 `remote_workers.py:400` | drain |
| E16 | ④ 동안 heartbeat 가 409 | B11 그대로 | 워커 행이 없으니 heartbeat 가 409 `not registered` 를 받는다. 오늘 코드는 이것을 실패로 세어 `heartbeat: server unreachable for 15s` 를 쓴다(문구는 Q12). 서버의 `mark_lost_workers` 는 `_workers()` 의 행만 보므로 잡은 lost 가 되지 않는다 | §3.3 의 3 · 코드 `remote_worker.py:339` · `remote_workers.py:980` | drain |
| E17 | ④ 의 잡을 새 서버가 모른다 | B11 에서 잡 행까지 지운다(새 `data_dir` 로 바꾼 서버) | finish 가 404 이고 로그 한 줄이다. 드레인은 끝나 재실행한다. 레인 스레드가 예외로 죽지 않는다 | §3.3 · 코드 `remote_workers.py:582` | drain |
| E18 | 깨진 캐시와 감독자 | 캐시 `0.8.1-<sha8>` 에 표지는 두고 `remote_ci_monitor/cli.py` 를 지운다. 서버 0.8.1, 감독자 흉내 아래 서브프로세스 워커를 10초 | 실행마다 `the cached client did not load` 한 줄이고 트레이스백이 없다. 설치본이 등록 409 를 받지만 `_follow_boot` 가 있어 다시 따라가지 않고 종료 2 다. 감독자가 다시 띄우면 같은 폴더로 들어가 같은 일이 되풀이된다. 깨진 폴더를 치울지는 Q14 | §2.5 BOOT · 결정 161 | e2e |
| E19 | 따라간 자식이 기동 등록 409 를 받는다 | ① 로 0.8.1 에 exec 한 자식이 등록하기 직전에 `bump(srv, "0.8.2")`(등록 래퍼 앞에서) | 자식에는 `_follow_boot` 가 있어 다시 따라가지 않는다. 종료 2 로 끝나고 감독자가 다시 띄우면 설치본이 ① 부터 해서 0.8.2 가 된다. 설치본 재실행으로 바로 이을지는 Q7 | 결정 161 · §3.2 ② | e2e |

## 엣지

| # | 경우 | 준비 | 기대 | 근거 | 파일 |
|---|---|---|---|---|---|
| G1 | claim long-poll 응답이 드레인 중에 온다 | 레인 1, `worker_claim_wait_seconds = 5`. claim 래퍼로 레인이 long-poll 에 들어간 것을 확인한 뒤 `bump` 하고 `heartbeat_once()` 를 직접 부른다. 이어 `hold` 잡을 넣는다 | 서버가 기다리던 long-poll 에 잡을 준다. 레인은 그 잡을 `term` 없이 끝까지 돌리고 `go` 뒤 finish 를 보낸 다음 루프 맨 위에서 빠진다. 드레인 뒤에 시작한 claim 은 0번이다. 잡은 `succeeded` 이고 그 뒤에 재실행한다 | §3.3 의 2 · W14 | drain |
| G2 | long-poll 이 204 로 끝난다 | G1 에서 잡을 넣지 않는다 | 레인이 204 를 받고 다음 claim 없이 빠진다. 드레인 시작부터 `run()` 반환까지 6초 안이다 | §3.3 의 2 · 코드 `remote_worker.py:512` | drain |
| G3 | 드레인 동안 heartbeat 는 계속 돈다 | B9 를 5초 붙잡는다 | 그동안 heartbeat 요청이 네 번 이상이고 본문 `jobs` 에 #1 이 있다. `worker_heartbeat` 의 잊힌 잡 처리(921행)가 #1 을 닫지 않는다 | §3.3 의 3 · 코드 `remote_workers.py:921` | drain |
| G4 | 따라간 자식은 자기 버전과 비교한다 | 설치본 0.8.0, 0.8.1 로 따라간 자식(B2 뒤). 서버 0.8.1 | heartbeat 를 열 번 받아도 드레인하지 않는다. 설치본 버전과 비교하면 300초마다 재실행이 되풀이된다(mutcheck: 비교 대상을 설치본으로) | §3.2 ③ 「자기와 다르다」 | e2e |
| G5 | 첫 heartbeat 에서 ③ | `App.worker_register` 를 감싸 200 을 돌려준 직후 `bump` | `run()` 의 동기 `heartbeat_once()` 가 드레인을 켠다. 레인은 claim 0번으로 바로 빠지고 재실행한다 | §3.3 · 코드 `remote_worker.py:840` | drain |
| G6 | 드레인 중 서버가 되돌아온다 | B9 에서 드레인이 켜진 뒤 `bump(srv, "0.8.0")`(지금 도는 버전) | Q8 의 답대로. 지금 명세대로면 드레인은 그대로 끝나고 재실행한 설치본이 ① 에서 그때의 서버 버전을 본다. 잡은 하나도 잃지 않고 끝난 버전은 서버와 같다 | §3.3 · 결정 150 | drain |
| G7 | 드레인 중 세 번째 버전 | 0.8.1 로 드레인을 시작했고 다음 heartbeat 가 0.8.2 | `follow_to` 를 바꾸나는 Q8. 재실행한 설치본은 health 를 새로 읽어 0.8.2 로 간다 | §3.3 · §5.4 `follow_to` | drain |
| G8 | 300초 창 안에서 서버가 되돌아온다 | 0.8.1 로 재실행된 자식(`RCM_WORKER_RESTARTED_AT = t0`). t0+60 에 서버가 0.8.0 으로 내려간다 | 드레인 없이 억제 로그를 한 번 쓴다. 그동안 잡은 0.8.1 코드로 계속 돈다(B18). t0+301 의 heartbeat 에서 드레인하고 설치본이 0.8.0 으로 등록한다(Q9) | §3.3 고리 막기 | drain |
| G9 | 버전이 10초마다 바뀐다 | 주입 시계로 30분 동안 heartbeat 마다 0.8.1 과 0.8.0 을 번갈아 준다. 재실행은 fake 로 세고 그때마다 `RCM_WORKER_RESTARTED_AT` 을 그 시각으로 둔다 | 재실행 간격이 언제나 300초 이상이고 횟수가 7번 이하다(mutcheck: 상한을 지운다) | 결정 171 「300초 상한」 | drain |
| G10 | editable 워커와 ③ | 설정 없는 editable, `bump`, heartbeat 다섯 번 | 드레인하지 않는다. `server v0.8.1 — not following (editable install)` 가 프로세스에서 한 번만 나온다 | §3.2 · W11 · 결정 156 | drain |
| G11 | installed 를 명시한 워커와 ③ | `RCM_CLIENT_VERSION=installed`, `bump` | 드레인 없음, 재실행 없음. 로그를 쓰나는 Q19 | W17 · R⑧ | drain |
| G12 | 고정 워커와 ③ | `RCM_CLIENT_VERSION=0.8.0`, `bump` | 드레인하지 않는다 | §3.2 · W12 | drain |
| G13 | `--once` 와 ③ | `once=True`, `hold` 잡이 도는 중에 `bump` | 잡이 끝나면 프로세스도 끝난다. `restart_for` 없음, execve 0번, 종료 0 | §3.2 `--once` · W18 · R⑧ | drain · start |
| G14 | `--once` 와 ④ | `once=True`, 레인 2, B11 의 준비 | 드레인하고 레인 1 의 잡을 보고한 뒤 재실행 없이 끝난다. 종료 코드는 Q4 | W18 · R⑧ | drain |
| G15 | `--once` 의 ① · ② | `once=True`, 서버 0.8.1 | Q4 의 답대로 | §3.2 | start |
| G16 | 드레인 중 취소 | B9 에서 드레인이 켜진 뒤 alice 가 #1 을 취소한다 | heartbeat 가 취소를 전하고 #1 이 `cancelled` 로 보고된 뒤에 재실행한다 | §3.3 의 3 | drain |
| G17 | 서버가 일시정지된 채 드레인 | 서버를 pause 한 채 `bump` | `paused` 갈래(489행)에 있던 레인도 2초 안에 빠지고 재실행한다 | §3.3 의 2 · 코드 `remote_worker.py:489` | drain |
| G18 | 두 레인이 동시에 ④ | 레인 둘 다 idle. 행 삭제와 `bump` 를 함께 | 재등록 409 가 두 번 와도 드레인은 한 번이고 로그도 한 번이다. `restart_for` 는 하나 | §3.2 ④ | drain |
| G19 | 프리셋 잡의 환경 | 재실행으로 들어간 자식에서 `env` 를 찍는 잡 | `RCM_FOLLOWED*` 와 `RCM_WORKER_RESTARTED_AT` 이 없다. 잡 환경은 `env_passthrough` 의 이름만 받는다(코드 `runner.py:207`) | W16 · §2.5 끝 | e2e |
| G20 | `RCM_WORKER_RESTARTED_AT` 은 ① exec 를 넘는다 | 재실행한 설치본이 ① 로 캐시에 exec 한다 | 자식 환경에 `RCM_WORKER_RESTARTED_AT` 이 그대로 있다. BOOT 는 `RCM_FOLLOWED*` 셋만 꺼낸다. 그래서 자식에서 온 ③ 에도 E7 의 억제가 걸린다 | §2.5 BOOT · §3.3 | e2e |
| G21 | 재실행한 프로세스의 ① 은 기록을 쓰지 않는다 | 재실행 직전 `follow.json` 에 1분 전 기록이 있다 | health 를 새로 1번 읽는다 | §13 P2-7 | e2e |
| G22 | 측정이 도는 중에 재실행한다 | 프로브 하나가 `sleep 3` 인 서버 설정. 세대를 바꿔 측정 스레드를 띄운 직후 ③ | Q13 의 답대로. 적어도 exec 뒤에 `sleep 3` 프로브가 부모 없는 프로세스로 남지 않는다 | 코드 `remote_worker.py:370`(daemon 측정 스레드) · 841행 | e2e |
| G23 | `server = "auto"` 워커의 ① | 발견을 가짜로 한 대 돌려준다(`test_worker_discovery.py` 의 요령) | 자식이 같은 주소에 등록한다. 발견을 몇 번 하는지와 주소를 넘기는 방법은 Q16 | §2.5 mDNS 줄 · 코드 `cli.py:2768` | start |
| G24 | 같은 머신의 워커 둘이 같은 캐시를 쓴다 | build-02 와 build-03 서브프로세스 워커를 같은 `XDG_CACHE_HOME` 으로 동시에 | 캐시 폴더 하나, 표지 하나, `.tmp-*` 없음. 둘 다 0.8.1 로 등록한다 | §2.5 「완성은 rename 하나로」 · F30 | e2e |
| G25 | 내려간 버전이 `worker.toml` 의 키를 모른다 | `worker.toml` 에 `accept_any = false`(M5n 키). 서버 버전의 가짜 wheel 에서 `config.py` 의 `_WORKER_KEYS` 에서 `accept_any` 를 뺀다 | 자식이 `unknown key(s): accept_any` 로 종료 2 다. 감독자 아래에서는 되풀이된다(Q15) | §1.5 와 같은 문제 · 코드 `config.py:1674` | e2e |
| G26 | `rcm worker --check` | 서버 0.8.1 | Q5 의 답대로 | §2.3 · §3.2 | start |
| G27 | 재실행 직후 ① 도중의 SIGTERM | 재실행한 설치본이 `/client/` 를 받는 중(받기를 붙잡는다)에 SIGTERM | 프로세스가 끝나고 잃은 잡이 없다(도는 잡이 없다). `.tmp-*` 가 남으면 다음 청소가 한 시간 기준으로 지운다 | §2.8 · §3.4 「신호 처리기는 `exec` 로 초기화」 | e2e |
| G28 | 버전 문자열은 같고 sha 만 다르다 | 서버가 같은 0.8.1 로 wheel 만 다시 조립한다 | heartbeat 의 `server_version` 이 같으니 드레인하지 않는다(문자열만 본다) | 결정 150 · §2.7 | drain |
| G29 | B1 만 있는 빌드의 레인 재등록 409 | B2 를 넣기 전 빌드, B11 의 준비 | 오늘처럼 로그와 `stopping` 으로 끝난다. `WorkerFollow` 가 레인 스레드 밖으로 새어 `threading.excepthook` 이 불리면 안 된다(Q2) | §5.4 `WorkerFollow` · 코드 `remote_worker.py:501` | drain |
| G30 | launchd 에서 재시작 횟수 | §7 의 10단계 워커를 `examples/launchd` plist 로 띄운다(시험용 HOME, 포트 8791) | 10-3 의 드레인 재실행 뒤 `launchctl print gui/$(id -u)/<label>` 의 `runs` 가 1 이고 `pid` 가 같다 | §3.4 · §7 의 10 | 실기 |
| G31 | systemd 에서 재시작 횟수 | Linux 머신에서 `examples/systemd/rcm-worker.service` | 재실행 전후 `systemctl --user show -p NRestarts,MainPID rcm-worker` 가 `NRestarts=0` 이고 `MainPID` 가 같다 | §3.4 | 실기 |

## 명세에 묻는 것

1. **④ 의 종료 코드와 감독자.** §3.1 은 409 를 받은 워커가 종료 2 로 끝난다고 적었다. 그것은 기동 등록만 그렇다.
   레인 재등록이 409 면 `_lane_loop` 가 `WorkerExit` 를 잡아 `stopping` 을 켜고 `run()` 은 0 을 돌려준다
   (`remote_worker.py:497~505` · 861행). 예시 plist 는 `SuccessfulExit = false`, unit 은 `Restart=on-failure` 라서
   종료 0 이면 다시 띄우지 않는다. 고정 · installed 워커의 ④ 「오늘처럼」은 다른 레인의 잡을 lost 로 만들고 조용히
   멈추는 것이다. 종료 2 로 바꿀지, 그런 워커도 드레인 뒤에 끝낼지 정해야 한다(E14).
2. **`WorkerFollow` 와 `WorkerExit` 의 관계.** 레인은 `WorkerExit` 만 잡는다. B1 이 먼저 머지되고 `WorkerFollow` 가
   그 하위 클래스가 아니면 B1 과 B2 사이의 빌드에서 레인 재등록 409 가 레인 스레드를 예외로 죽인다(G29).
3. **결정 162 가 `cmd_worker` 에도 걸리나.** `main(argv)` 처럼 목록을 넘긴 호출에서 ① · ② · 재실행을 모두 끄나.
   기존 `test_cli_worker.py` 는 전부 `main(argv)` 로 부르므로 그대로 두면 새 시험은 `_main(..., follow_enabled=True)`
   를 써야 한다.
4. **`--once` 의 나머지 계기.** §3.2 는 `--once` 의 ③ 과 ④ 만 정했다. 기동 ① 과 ② 에서 따라가나. ④ 드레인 뒤의
   종료 코드는 0 인가(G14 · G15).
5. **`rcm worker --check` 는 따라가나.** 진단 명령이라 설치본으로 돌며 서버와의 차이를 보이는 쪽이 맞아 보이지만
   명세에 없다(G26).
6. **① 이 실패한 뒤의 ②.** ① 에서 `sha_mismatch` 로 설치본에 남은 워커가 등록 409 를 받으면 ② 가 같은 wheel 을 한 번
   더 받나(`/client/` 1번인가 2번인가). 그리고 ① 의 exec 자체가 `OSError` 일 때의 경고 코드가 §2.6 표에 없다(E1 · E11).
7. **따라간 자식이 ② 를 만날 때.** ① 과 등록 사이에 서버가 또 바뀌면 자식은 고리 막기 때문에 다시 따라가지 못하고
   종료 2 다. `restart_via_installed` 로 바로 이을지, 감독자에게 맡길지(E19).
8. **드레인 중에 서버 버전이 또 바뀔 때.** 지금 도는 버전으로 되돌아오면 드레인을 취소하나. 세 번째 버전이면
   `follow_to` 를 갈아 끼우나. 재실행이 health 를 새로 읽으므로 결과는 같지만 로그와 `restart_for` 값이 달라진다(G6 · G7).
9. **300초 창의 세부.** 창 안에서 억제한 ③ 은 창이 끝난 뒤 다음 heartbeat 에서 드레인하나, 그 프로세스에서는 영영
   안 하나. 억제 로그 「한 번」은 프로세스당인가 창당인가. `RCM_WORKER_RESTARTED_AT` 이 숫자가 아니거나 미래(시계가
   뒤로 간 경우)일 때 어떻게 보나. 시험이 쓸 시계 주입점의 이름(E7 · E8 · G8).
10. **`server_version` 의 형식 검사.** §2.4 는 health 값만 검사한다. 등록 409 본문과 heartbeat 응답의 값도
    `VERSION_RE` 로 거르나. 거르지 않으면 그 값이 로그와 `follow_to` 로 들어간다(E6).
11. **보고가 실패한 채 끝난 드레인.** finish 보고가 재시도 끝에 실패하면 재실행 뒤 등록이 그 잡을 lost 로 닫는다.
    보고가 될 때까지 재실행을 미룰지, 오늘의 보고 실패와 같게 둘지(E15).
12. **④ 동안의 heartbeat 409.** 워커 행이 없으면 heartbeat 가 409 를 받고 `server unreachable for 15s` 를 쓴다. 서버는
    닿고 있으므로 문구가 틀렸다. 드레인 중에는 이 줄을 바꾸거나 막을지(E16).
13. **측정 스레드와 샘플러의 하위 프로세스.** `run()` 은 레인과 heartbeat 만 기다린다. 측정 스레드가 프로브를 돌리는
    중에 exec 하면 그 프로브는 부모가 바뀐 채 남는다. `_measure_lock` 을 기다린 뒤 exec 할지(G22).
14. **깨진 캐시 폴더.** 표지는 있고 코드가 깨진 폴더는 sha 가 맞아 계속 골라진다. CLI 는 매번 경고 한 줄로 끝나지만
    워커는 감독자 아래에서 10초 간격으로 죽고 다시 뜬다. BOOT 가 설치본으로 돌아갈 때 그 폴더를 치우거나 표지를
    지울지(E18).
15. **내려간 버전과 `worker.toml`.** `load_worker_config` 도 모르는 키에서 `ConfigError` 다(`config.py:1674`). §1.5 가
    `client.toml` 에서 본 문제가 워커 설정에 그대로 있다. 내려가서 설정을 못 읽으면 설치본으로 되돌아갈지(G25).
16. **`server = "auto"` 워커의 주소 넘기기.** CLI 는 찾은 주소를 `RCM_SERVER` 로 넘긴다(§2.5). `load_worker_config` 는
    `RCM_SERVER` 를 읽지 않으므로 자식이 1.5초 발견을 다시 하고, 같은 망에 서버가 여럿이면 다른 서버를 고를 수도 있다(G23).
17. **로그 문구.** W 표는 editable 줄만 문구를 정했다. 드레인 시작, 300초 억제, 재실행, ② 의 `pinned` 거절, ① 의
    실패 경고(워커 로그는 `[rcm worker]` 머리를 쓴다)의 문구를 정해야 시험이 문자열로 잠근다.
18. **exec 직전의 SIGTERM.** `run()` 이 드레인 끝에 `stopping` 을 켜므로 그 뒤의 첫 신호를 처리기가 두 번째 신호로
    본다(`cli.py:2835`). 문장이 `again` 이고 종료 1 이다. 드레인 끝을 `stopping` 과 다른 표시로 남길지(E13).
19. **installed 와 editable 의 문장.** editable 워커는 ③ 에서 로그를 한 번 쓰는데(W11) installed 를 명시한 워커는 아무것도
    쓰지 않나(W17). ② 에서 editable 워커의 종료 문장에 이유(`not following (editable install)`)를 붙이나(E4 · G11).
