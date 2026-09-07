# M5b-3 테스트 시나리오 A — `runner.run_job` 순수 규칙 · 로컬 워커 회귀 (2026-09-07)

`docs/m5b3-workplan.md` §1(`runner.py` — `RunSpec` · `RunObserver` · `RunResult` · `run_job`)과 §5 표의 A 행을
`tests/test_runner.py`(러너 순수 규칙 16건) · `tests/test_runner_local.py`(로컬 워커 회귀 12건)로 옮긴 것이다
(test-first, 역할 A). `src/` · 기존 테스트는 건드리지 않았다. `WorkerClient` · `load_worker_config` · `rcm worker`
CLI 는 B, 두 프로세스 e2e 는 C 의 몫이다.

공통: 실제 `sh` 를 돌린다(bash-ism 없음 — `sh -c` · `sleep` · `printf` · `cat` · `trap` · `$$`/`$!` 만).
시각은 실제 벽시계(펌프가 큐 타임아웃과 `now_fn` 을 섞어 쓰는 오늘의 구조 그대로). 기다림은 전부 마감 있는
폴링이고 맨 `time.sleep` 은 0.05초 간격뿐. 띄운 프로세스는 `reap` 픽스처(autouse — `subprocess.Popen` 을
추적해 테스트 끝에 그룹째 KILL)와 관찰자의 `deadline`(15초 뒤 `should_stop` True) 두 겹으로 거둔다.

`tests/test_runner.py` 의 도우미:
- `RecordingObserver` — `RunObserver` 를 구조적으로 만족. `events` 에 `("phase", …)` · `("output", bytes)` ·
  `("materialize", job_id)` 가 **순서대로** 남는다. `cancel_if` / `stop_if` 는 폴링 때 평가되는 조건이고
  처음 True 가 된 monotonic 시각(`cancel_seen_at`)을 기억한다. `stamps` 는 `output` 호출 시각.
- `FileMaterializer(files, observer=)` — 워크스페이스를 만들고 파일을 쓴다(기본 `hello.txt`). 받은 `spec` 을 기록.
- `make_spec(tmp_path, argv, …)` — 오늘의 워커 배치(`<data>/workspaces/<id>` · `<data>/jobs/<id>/log.txt`) 로 `RunSpec`.
  기본 `env_passthrough = ("PATH",)`, `grace_seconds = 5`.
- `run(spec, obs, materialize=, now_fn=, environ=)` — `run_job` 을 스레드에서 돌려 20초 마감을 건다(안 돌아오는
  버그가 세션을 막지 않게). `environ` 기본은 `BASE_ENVIRON`(PATH 는 실제, `HOME`·`LANG`·`SECRET` 은 고른 값).
- `is_dead(pid)`(없거나 zombie) · `group_gone(pgid)`(`killpg(pgid, 0)` 이 ESRCH) — test_e2e_m3 와 같은 판정.

`tests/test_runner_local.py` 는 `test_worker` 의 `enqueue` · `run_one` · `sh` 를 그대로 쓰고, 프리셋 목록에
`lastout`(`echo a; sleep 1.1; echo b`)만 더했다. 러너 모듈은 마지막 시나리오(28)에서만 들여온다 — 나머지 11건은
러너가 없어도 돌아 **오늘의 워커를 검증하는 회귀 잠금**이다.

## `tests/test_runner.py` — `run_job` 순수 규칙

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 모양 — `RunSpec` 필드 이름·순서 12개 그대로 · `RunResult` ⊇ {rc, cancelled, timed_out, lost, started, finished} · `MAX_LINE_BYTES == 64 KiB` 가 러너 모듈에 · `RunnerError` 는 `MaterializeError` 와 무관한 `Exception` · `RunObserver.phase` 존재 | `test_runspec_and_runresult_have_the_fields_of_the_spec` | §1 L11-40 |
| 2 | 정상 경로 — 순서 materialize → `phase("executing")`(한 번) → output → 반환 · `rc == 0` · 플래그 전부 False · `started <= finished` 이고 둘 다 주입한 `now_fn` 이 돌려준 aware 값 · 마커 줄 `::rcm::summary::ok` 가 **raw 그대로** 관찰자에게 · 로그 파일 == 관찰자 바이트 · `materialize(spec)` 는 그 spec 으로 한 번 | `test_happy_path_materialize_then_executing_then_raw_output_then_result` | §1 L27-29 · L42 · L45 |
| 3 | env — `RCM_JOB_ID · RCM_PRESET · RCM_REQUESTER · RCM_SOURCE_MODE · RCM_REF · RCM_BASE_SHA · RCM_DIRTY · RCM_WORKSPACE · RCM_LOG_FILE`(git_ref 잡: REF 있음 · BASE 빈 값 · DIRTY 0) · `env_passthrough` 에 있는 키만 `environ` 에서(HOME·LANG 은 unset) · `spec.env` 가 통과값(SECRET)을 덮음 · `spec.env` 의 `RCM_JOB_ID` 는 진짜 값에 진다 · `env_for_inputs({"scope","dry-run": True})` == `RCM_INPUT_SCOPE=fast · RCM_INPUT_DRY_RUN=1` · cwd 는 워크스페이스(`pwd` + `cat hello.txt`) | `test_env_has_rcm_vars_passthrough_preset_env_and_inputs_and_cwd_is_the_workspace` | §1 L17-24 · worker.py `_env` |
| 4 | tree 잡의 env — `RCM_SOURCE_MODE=tree` · `RCM_REF` 빈 값 · `RCM_BASE_SHA=base_sha` · `RCM_DIRTY=1` | `test_tree_source_sets_base_sha_and_dirty_and_empty_ref` | §1 L22 · worker.py `_env` |
| 5 | 자재화 실패 — `materialize` 의 `MaterializeError("snapshot file is missing")` 가 **그대로 올라온다**(문구 보존) · 관찰자는 `executing` 도 output 도 못 봤다 · 워크스페이스 없음 | `test_materialize_error_propagates_and_executing_is_never_reported` | §1 L45 |
| 6 | 시작 실패 — argv `("nope", "--flag")` → `RunnerError`, 문구는 `cannot start 'nope'` 로 시작 · 트레이스백·tmp 경로 없음 · 자재화는 끝났고 output 없음 | `test_cannot_start_raises_runner_error_with_the_worker_wording` | 잠근 선택 · worker.py `cannot start` 문구 |
| 7 | 종료 코드는 결과 — `exit 3` → `rc == 3`, 예외 없음, 플래그 False, 출력 그대로, 로그 == 관찰자 | `test_nonzero_exit_is_a_result_not_an_exception` | §1 L35 |
| 8 | 줄 단위 배치 — 모든 `output` 조각은 비어 있지 않고 `\n` 으로 끝남 · 이어 붙이면 `one\ntwo\nthree\n`(개행 없는 꼬리 `three` 는 EOF 에 개행 붙여) · 로그 같음 | `test_output_is_line_batched_in_order_and_the_partial_tail_arrives_at_eof` | §1 L29 · L47 · test_worker `nomarker` |
| 9 | `MAX_LINE_BYTES` — 3×64 KiB+17 바이트 개행 없는 출력 뒤 `sleep 1.2; echo; echo done`: 첫 x 조각이 `done` 조각보다 **1초 이상 먼저** 도착(EOF 를 기다리지 않는다) · `\n` 을 빼면 바이트 손실 0 · 잘린 조각 ≤ 2×64 KiB+1 · 로그 같음 | `test_a_line_longer_than_max_line_bytes_is_delivered_without_waiting_for_eof` | §1 L47 |
| 10 | 로그 파일 — 러너가 `spec.log_path` 에 **직접 append**(호출자가 자재화 단계에 남긴 `fetching …` 줄 뒤) · 내용 == 관찰자 바이트(마커 줄 포함) | `test_log_file_is_appended_with_the_same_bytes_the_observer_gets` | 잠근 선택 · worker.py `_append_log` + `open("ab")` |
| 11 | 취소 — `should_cancel` 이 True(첫 줄 `start` 뒤) → SIGTERM → 죽으면 바로 반환(grace 5 를 안 기다림: 4초 안) · `cancelled=True` · timed_out/lost False · `rc == -SIGTERM` · `never` 없음 · `executing` 한 번 | `test_cancel_sends_term_and_returns_well_before_grace` | §1 L30 · L47 |
| 12 | TERM 무시 자식 — `trap '' TERM; sleep 30`, `grace_seconds=1` → 0.9초 이상 기다린 뒤 SIGKILL, 4초 안 · `cancelled=True` · `rc == -SIGKILL`(test_e2e_m3 의 -9) | `test_term_ignoring_child_is_killed_after_grace` | §1 L47 |
| 13 | 타임아웃 — `timeout_seconds=1`, `sleep 30` → 0.9~5초 사이에 `timed_out=True`(cancelled/lost False) · `rc == -SIGTERM` · `finished - started >= 1s` | `test_timeout_marks_timed_out_and_kills_the_process` | §1 L47 |
| 14 | 정지 — `should_stop` True(스크립트가 `$$` 를 쓴 뒤) → `lost=True`(cancelled/timed_out False) · `rc == -SIGTERM` · 세션 그룹(`$$` = pgid)에 산 프로세스 없음 | `test_should_stop_marks_lost_and_the_process_group_is_gone` | §1 L31 · L38 · §2 L61 |
| 15 | 우선순위 — 정지와 취소가 같이 True 면 `lost`(cancelled 아님) | `test_should_stop_wins_over_should_cancel_when_both_are_true` | 잠근 선택 · worker.py `_pump`(shutting_down 검사가 먼저) |
| 16 | 손자 — `sleep 60 &` 를 띄운 자식을 취소 → 손자 pid 가 죽고(없거나 zombie) 그룹이 비었다 · 5초 안 | `test_cancel_kills_the_grandchildren_of_the_job_process` | §1 L47(`start_new_session` + `killpg`) · test_e2e_m3 |

## `tests/test_runner_local.py` — 로컬 워커 회귀 (Worker → store)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 17 | 성공 — succeeded · exit 0 · summary `all green` · failed_step/lane/phase None · DB 마커 4개 순서 그대로(`at` 은 aware, `started_at` 이후) · 로그 = 스크립트 출력 전부(마커 줄 포함) · 워크스페이스 삭제 · 전이 queued→running→succeeded | `test_success_stores_markers_summary_and_removes_the_workspace` | §1 L46 · test_worker |
| 18 | 실패 — failed · exit 3 · summary 마커 · `failed_step == "test"` · 워크스페이스 보존 | `test_failure_keeps_the_workspace_and_blames_the_last_step` | §1 L46 · test_worker |
| 19 | `keep_workspace_on_failure = false` 면 실패해도 삭제(정리는 워커의 몫) | `test_failure_without_keep_workspace_removes_it` | worker.py `execute` 정리 |
| 20 | phase · last_output — 도는 동안 `phase == executing` · `lane == 1` · `last_output_at >= started_at`; `sleep 1.1` 뒤 출력으로 `last_output_at - started_at >= 1s` · `<= finished_at` · 끝나면 phase None 이고 last_output_at 은 남는다 · 로그 `a\nb\n` | `test_phase_is_executing_while_running_and_last_output_advances` | §1 L46(`_LocalObserver`: phase · last_output) |
| 21 | 취소 — `request_cancel` → cancelling → cancelled · summary `cancelled by alice-laptop` · `cancelled_by` · 전이에 cancelling · 15초 안 · 로그에 `never` 없음 | `test_cancel_goes_through_cancelling_and_ends_cancelled_by_the_requester` | §1 L30 · L46 · test_worker |
| 22 | 타임아웃 — timed_out · summary `format_limit(1) == "limit 1s"` · `cancelled_by None` · `never` 없음 | `test_timeout_ends_timed_out_with_the_limit_summary` | §1 L46 · `outcome_for` |
| 23 | 정지 — `Worker.shutdown()` → 잡 `lost` · summary `server stopped while running` · lane/phase None · 스레드 종료 · 5초 안 | `test_shutdown_marks_the_running_job_lost` | §1 L31 · L46 · test_server `test_shutdown_marks_running_job_lost` |
| 24 | 개행 없는 마지막 줄 → 로그 `no newline at end\n` · 마커 없음 | `test_partial_last_line_is_logged_with_a_newline_and_no_markers` | test_worker |
| 25 | env·cwd — `scope=fast job=<id> preset=env mode=tree ci=1 ws=` · `hello` | `test_env_passes_inputs_and_rcm_vars_and_runs_in_the_workspace` | test_worker |
| 26 | 시작 실패 — failed · exit None · phase None · summary `cannot start '/nonexistent/binary-xyz'…` · 워크스페이스 남음 | `test_missing_binary_fails_with_null_exit_code_and_the_cannot_start_summary` | 잠근 선택 6 의 호출자 쪽 |
| 27 | 자재화 실패 — tar 없음 → failed · exit None · summary `snapshot file is missing` · 전이 queued→running→failed · 로그 출력 없음 · 마커 없음 | `test_missing_snapshot_fails_without_executing` | §1 L45 |
| 28 | `Worker.execute` 가 `RunSpec` 을 만들어 `run_job` 을 **한 번** 부른다 — job_id · preset_name · argv · env · env_passthrough · timeout_seconds(잡) · inputs · requester_label · source · workspace · log_path · grace_seconds(설정) 전부 잡/프리셋/설정에서 · 관찰자는 네 메서드 · 끝난 잡의 `should_cancel/should_stop` 은 False(`runner.run_job` 과, 있으면 `worker.run_job` 도 spy 로 갈아 끼움) | `test_worker_builds_the_runspec_from_job_preset_and_config_and_calls_run_job` | §1 L46 |

28 건. `ruff check` · `ruff format --check` 깨끗(line-length 100, CJK 2폭). 두 파일 합쳐 벽시계 약 13초
(가장 긴 것: TERM 무시 자식 2.0초 · 긴 줄 1.2초 · 나머지 ≤ 1.2초).

## 잠근 선택 (명세가 안 정한 것 — 구현이 달리 정하면 테스트를 고쳐야 한다)

1. **자재화 실패는 예외로** (5 · 27). `materialize` 의 `MaterializeError` 를 `run_job` 이 잡지 않고 그대로 올린다 —
   결과 객체에 `materialize_error` 필드를 두지 않는다. 호출자(로컬 워커 · 원격 워커)가 failed 로 보고한다.
   관찰자는 `executing` 을 보지 못한다(자재화 → phase 순서).
2. **시작 실패도 예외로** (6 · 26). `RunnerError`, 문구는 오늘의 워커와 같은 `cannot start '<argv0>'` 로 시작
   (뒤에 `: FileNotFoundError: …` 가 붙어도 된다 — `startswith` 만 잠갔다). `rc None + error` 결과 방식은 택하지 않았다.
   종료 코드 ≠ 0 은 예외가 아니다(7).
3. **로그 파일은 러너가 쓴다** (10). 관찰자만 output 을 받는 게 아니라 러너가 `spec.log_path` 에 append 로 직접 쓰고
   **같은 바이트**를 `observer.output` 에 준다. 원격 워커의 로컬 디버그 로그(§2 L62)와 로컬 워커의 정본 로그가
   같은 코드로 남는다. 호출자가 자재화 단계에 남긴 줄(`_append_log`)은 그 앞에 그대로 있다.
4. **output 조각은 줄 배치** (8 · 9). 모든 조각은 `\n` 으로 끝나고 비어 있지 않다. 개행 없는 꼬리는 EOF 에 `\n` 을
   붙여 준다(오늘 로그의 `no newline at end\n` 규칙과 같다). `MAX_LINE_BYTES` 를 넘긴 개행 없는 출력은 잘라서
   `\n` 을 붙여 흘리므로 로그·관찰자 바이트는 stdout 과 **개행 삽입만** 다르다 — 오늘의 `_pump` 그대로.
5. **정지가 취소보다 우선** (15). `should_stop` 과 `should_cancel` 이 같이 True 면 `lost`. 오늘의 `_pump` 가
   `_shutting_down` 을 먼저 보는 순서를 그대로 잠갔다(서버 종료 중 cancelling 잡은 lost).
6. **phase 는 `executing` 한 번, `materializing` 은 허용** (2 · 11). 러너가 `materializing` 을 관찰자에게 알리는지는
   잠그지 않았다(로컬은 claim 이 놓는다). `executing` 은 정확히 한 번이고 자재화 뒤 · 첫 output 앞이다.
7. **`rc` 는 신호 종료 코드** (11-14). TERM 으로 죽으면 `-15`, grace 뒤 KILL 이면 `-9`(test_e2e_m3 의 `exit_code == -9`
   와 같은 값). `Popen.returncode` 그대로.
8. **`now_fn` 이 결과의 시각** (2). `started`·`finished` 는 주입한 `now_fn` 이 돌려준 값이어야 한다 — 서버 테스트의
   주입 시계(`clock`)와 맞물린다.
9. **`MAX_LINE_BYTES` 는 러너 모듈에서 들여온다** (1 · 9). 펌프가 러너로 옮겨졌으니 상수도 거기 있다. `worker.py` 가
   재수출해도 된다.

## 명세와 `worker.py` 사이에서 어긋나 보이는 것 (구현자·B·C 가 정할 것)

1. **§2 L60 「→ `phase executing` → `run_job`」 vs §1 관찰자 프로토콜.** §1 은 `run_job` 이 `observer.phase("executing")`
   을 부르는 구조(관찰자에 `phase` 가 있고 자재화가 `run_job` 안에 있다)인데 §2 의 원격 흐름은 워커가 `phase executing`
   을 보낸 뒤 `run_job` 을 부르는 것처럼 읽힌다. 둘 다 하면 서버에 `executing` 이 두 번 간다(무해하지만 군더더기).
   잠근 것: **러너가 부른다**(2). 원격 워커는 tree 다운로드 전에 `materializing` 만 직접 보내고 `executing` 은
   `_RemoteObserver.phase` 에 맡기면 된다.
2. **§1 서명 `run_job(spec, observer, *, now_fn, environ, materialize)` 의 필수 여부.** 명세는 셋 다 키워드 전용으로만
   적었고 기본값을 말하지 않았다. 테스트는 늘 셋 다 넘긴다(기본값이 있어도 초록). 원격 워커가 tar 를 `run_job` 밖에서
   풀고 `materialize` 를 생략하는 설계도 이 테스트와 충돌하지 않는다.
3. **`RunResult` 의 필드.** 명세는 6개(L34-40)만 적었다. 테스트는 「⊇ 6개」로 잠가 구현이 `term_sent_at`·`kill_sent`
   같은 내부 필드를 더해도 된다. `RunSpec` 은 필드 이름·순서를 정확히 잠갔다(B 가 claim 응답 → `RunSpec` 을 만들 때
   같은 이름을 써야 하므로). `grace_seconds` 에 기본값이 있어도 된다.
4. **`MAX_LINE_BYTES` 의 실제 조각 크기.** 「MAX_LINE_BYTES 에서 자른다」고 읽히지만 오늘의 `_pump` 는 *개행 없는
   버퍼가 64 KiB 를 넘긴 시점* 에 통째로 흘리므로 한 조각이 READ_CHUNK(64 KiB) + 64 KiB 까지 될 수 있다. 테스트는
   `≤ 2×MAX_LINE_BYTES + 1` 로 잠갔다(오늘 동작). 서버 `/worker/jobs/{id}/log` 의 요청 상한 4 MiB(M5b-2 §3)와는
   충분히 멀다.
5. **`lost` 의 summary 는 호출자 몫.** §1 의 `RunResult.lost` 는 플래그뿐이고 문구(로컬 `server stopped while running`
   · 원격 `worker stopped`, §2 L61)는 `outcome_for(lost_summary=)` 가 정한다. 로컬 문구만 23 에서 잠갔다.
6. **`_LocalObserver.output` 의 마커 수신 시각.** 오늘의 `_pump` 는 큐에서 꺼낸 묶음마다 `now` 하나를 썼다. 관찰자가
   배치 하나에 `now` 하나를 쓰면 같다. 테스트 17 은 `at >= started_at` 과 순서만 잠갔다.

## 검증 방법 (구현 전 · 후)

- 러너가 없을 때: `tests/test_runner.py` 는 수집 단계에서 `ImportError`(모듈 없음)로 빨갛고, `tests/test_runner_local.py`
  는 28 번만 `ImportError` 이고 나머지 11건은 오늘의 워커로 초록이어야 한다(회귀 잠금의 검증).
- 픽스처 검증: §1 을 오늘의 `_pump` 에서 옮겨 적은 임시 러너(스크래치 디렉터리, `src/` 밖)를 `sys.modules` 에
  `remote_ci_monitor.runner` 로 끼워 돌려 28건 전부 초록을 확인했다 — 빨간 이유가 픽스처가 아니라 모듈 부재임을
  확인하는 절차다.
- 작성 중 `src/remote_ci_monitor/runner.py` 와 `worker.py` 리팩터가 같은 트리에 들어왔고, 그 구현으로도 28건 전부 초록이다.
  기존 잠금 `tests/test_worker.py` · `tests/test_e2e_m3.py` · `tests/test_worker_gitref.py`(25건)도 한 글자 안 바꾸고 초록.
