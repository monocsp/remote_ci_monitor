# M5b-3 테스트 시나리오 — C (`tests/test_e2e_worker.py`)

`docs/m5b3-workplan.md` §4 「e2e (같은 머신에서 두 프로세스)」 1–7 을 진짜 `rcm serve` + 진짜
`rcm worker` 프로세스로 잠근다. 워커 쪽 규칙은 §2, 서버 쪽 규칙은 `docs/m5b2-workplan.md` §3–§4.
워커가 등록 전에 죽으면(`rcm worker` 가 없을 때의 argparse 종료 2 포함) 픽스처가 종료 코드와 로그째로
실패한다. 서버 쪽 픽스처는 단독으로 검증했고(설정 파싱 · health · 토큰 두 종류 · linux 풀 queued ·
git_ref 해석 · 같은 포트 재시작 0.6초), `1e42efd feat(m5b-3)` 의 구현으로는 7개 전부 통과한다
(파일 전체 약 30초 · 가장 긴 시나리오 4 약 16초).

## 배치 (모든 시나리오 공통)

| 항목 | 값 |
|---|---|
| 서버 | `rcm serve --config <tmp>/server.toml` · `bind 127.0.0.1` · 빈 포트 · `data_dir = <tmp>/server-data` · `lanes = 1` · `grace_seconds = 2` · `snapshot_cache = true` · `worker_timeout_seconds = 10` · `worker_heartbeat_seconds = 1` · `worker_claim_wait_seconds = 2` |
| 프리셋(전부 `pool = "linux"`) | `lin`(§4 마커 스크립트 — `::rcm::steps::2 · step::build · step-end::ok · step::test · step-end::ok · summary::built ok`) · `qal`(`concurrency_group = "devices"`, `sleep 3`) · `slowl`(pid 파일 + `exec sleep 30`) · `slow4`(pid 파일 + `sleep 4` + `lin` 마커) · `cachedl`(`sh scripts/cached.sh`: `wc -c < assets/blob.bin` + `summary::cached ok`) · `deploy`(git_ref, `repo = "app"`, 시나리오 6 에서만) |
| 토큰 | `rcm token --config server.toml add laptop`(클라이언트) · `rcm token --config server.toml add build-02 --worker`(워커) |
| 워커 | `rcm worker --server http://127.0.0.1:<port> --pool linux --lanes 1 --data <tmp>/worker-data [--config worker.toml]` · 환경 `RCM_WORKER_TOKEN=<build-02 토큰>` · `start_new_session` · stdout/stderr → `<tmp>/worker-N.log` |
| 워커 이름 | 토큰 이름 `build-02` → `server.workers[].display_name == "build-02/1"` |
| 클라이언트 | `RCM_SERVER` · `RCM_TOKEN`(laptop) · `HOME` 은 tmp(개발자 설정·gitconfig 격리) |
| 폴링 | `/api/status` · `/jobs/{id}` 를 urllib 로 50 ms 간격(프로세스 스폰 없음) · `StatusWatcher` 스레드가 `server.workers[]` 의 (worker, display_name, state, job_id) 를 모은다 |
| 잡 프로세스 | `slowl`·`slow4` 는 `$$` 를 `RCM_MARK_DIR/<job_id>.pid` 에 적는다(프리셋 `[presets.env]`) — 취소·정지 뒤 죽었는지 `ps` 로 확인, `kill -9` 고아는 teardown 이 `killpg` |
| teardown | 워커·서버 `killpg(SIGKILL)` → pid 파일의 잡 그룹 kill → `pgrep -f "worker --server http://127.0.0.1:<port>"` 가 비어 있어야 한다 |
| 시간 | pytest-timeout 없음 — 기다림은 전부 마감 있는 폴링. 시나리오 하나 < 40초, 파일 전체 < 3분 |

## 시나리오 → 테스트 → 명세

| # | 시나리오 | 테스트 | 잠그는 것 | 명세 |
|---|---|---|---|---|
| 1 | `rcm run lin` 이 원격 워커에서 돈다 | `test_lin_job_runs_on_the_remote_worker_with_steps_summary_and_the_whole_log` | 종료 0 · `succeeded` · summary `built ok` · `pool linux` · 도는 동안 `build-02/1 busy #N` 이 관찰되고 끝나면 `idle` · 로컬 레인은 그 잡을 만진 적 없음 · 서버 DB 마커 → 스텝 2/2(`build`·`test`, partial 아님) · `rcm logs N` == 서버 `jobs/N/log.txt`(마커 줄 전부) · `rcm top --json` linux 풀 recent 행 · `pools[linux].lanes == 1` · `last_error null` · `rcm top` 텍스트에 `build-02/1` | m5b3 §4.1 · §2(레인 스레드: claim → tree → executing → log → finish) · m5b2 §3(`/worker/jobs/{id}/log` 마커는 서버가 파싱) · §4(`server.workers[]` display_name/state) |
| 2 | 캐시 잡 — 서버가 조립한 tar 를 워커가 받는다 | `test_cached_tree_job_runs_on_the_worker_from_the_server_assembled_tar` | 첫 `rcm run cachedl` 은 전체 업로드(`uploaded_bytes ≥ 1 MB`, `cached_bytes 0`) → succeeded · 둘째(`--no-join`)는 `cache` 진행줄 · `uploaded_bytes ≤ 4 KB` · `cached_bytes ≥ 1 MB` → succeeded · 두 잡 다 `build-02/1 busy` · 서버 `jobs/<id>/manifest.json` + 조립된 `tree.tar.gz` 존재 · 두 로그 모두 `1000000`(워크스페이스에 blob 이 있었다) · `snapshot_cache.blobs ≥ 1` | m5b3 §4.2 · m5b2 §3(`GET /worker/jobs/{id}/tree` — 캐시 잡은 manifest+blob 으로 조립) · §6(조립 tar 는 `jobs/<id>/tree.tar.gz`) |
| 3 | 취소가 heartbeat 으로 워커에 닿는다 | `test_cancel_reaches_the_worker_through_the_heartbeat_within_seconds` | `slowl` running · pid 살아 있음 · 레인 `busy #N` · `rcm cancel N` → `cancelling` · 5초 안에 `cancelled` · summary `cancelled by laptop`(워커가 확인한 취소 — 서버 대신 닫기면 `worker did not confirm the cancel`) · `cancelled_by laptop` · transitions `… running → cancelling → cancelled` · 잡 프로세스 죽음 · 레인 `idle` · `rcm wait` 종료 2 | m5b3 §4.3 · §2(heartbeat 응답 `cancel` → `should_cancel` → SIGTERM → grace → KILL → finish cancelled) · m5b2 §3(취소 규칙 · `kill_at + 2 × heartbeat` 미확인 시 서버가 닫음) |
| 4 | `kill -9` → timeout 뒤 lost · down · lanes 0 · 새 워커가 이어받는다 | `test_killed_worker_leaves_the_job_lost_after_the_timeout_and_a_new_worker_takes_over` | `slowl` running → 워커 프로세스만 SIGKILL(잡은 고아) → 20초 안에 `lost` · summary `worker build-02 unreachable for \d+s` · 걸린 시간 8~20초(timeout 전엔 lost 아님) · `server.workers[] == [build-02/1 down]` · `pools[linux].lanes == 0` · `/api/health.pools_without_workers == ["linux"]` → 고아 잡 killpg → 워커 재기동(같은 data dir) → 등록 idle · lanes 1 · `pools_without_workers []` · 옛 잡 summary 그대로(`restarted without the job` 로 안 바뀜) → `rcm run lin` succeeded | m5b3 §4.4 · m5b2 §4(janitor 5초: `now − last_seen_at > timeout` → lost + summary · 워커 행은 down 으로 남음 · `pools[].lanes` 는 살아 있는 레인 합 · health 본문) · §3(register 는 활성 잡만 lost — 이미 lost 인 잡은 무관) |
| 5 | SIGTERM → 도는 잡 lost(`worker stopped`) · 종료 0 | `test_sigterm_stops_the_worker_gracefully_and_reports_the_job_lost` | `slowl` running → 워커 SIGTERM → `grace + 5`(7초) 안에 종료 코드 0 · 잡 `lost` summary `worker stopped`(종료 전에 finish 를 보냈다) · 잡 프로세스 죽음 · 레인 `idle`(DB 정본 — 잡 없음) | m5b3 §2 「신호」(SIGTERM → should_stop → 잡 TERM → grace → KILL → `finish {lost}` summary `worker stopped` → heartbeat 중단 → 종료 0) · §4.5 |
| 6 | git_ref 잡 — 워커가 자기 `[[repos]]` 로 fetch | `test_git_ref_job_is_fetched_by_the_worker_from_its_own_repos`(git 없으면 skip) | `gitrepo.build_remote` 의 bare 레포 · 서버 `[[repos]] app` + `deploy` 프리셋 · 워커 `worker.toml` 에 같은 `[[repos]] app` → `rcm worker … --config worker.toml` → `rcm run deploy --ref main` 종료 0 · succeeded · `source.mode git_ref` · `source.sha == main` · `build-02/1 busy` · 로그에 `hello from main` · `HEAD=<main sha>` · `RCM_REF=main` · 서버 `workspaces/<id>` 없음(서버는 ls-remote 만) · 워커 `workspaces/<id>` 없음(성공 → 삭제) | m5b3 §4.6 · §2(git_ref 는 워커의 `[[repos]]` 로 `prepare_git_ref` · 워크스페이스 `<data_dir>/workspaces/<job_id>` 성공하면 삭제) · m5b2 §3(tree 는 404 `git_ref jobs are fetched by the worker`) |
| 7 | 서버 재시작 중에도 원격 잡은 running · finish 가 들어가면 succeeded | `test_server_restart_keeps_the_remote_job_running_until_the_worker_finishes` | `slow4` running + 스크립트 시작(pid 파일) → 서버 SIGTERM → 같은 data dir·포트로 재기동, 공백 < 5초(단독 측정 0.6초) → 재시작 직후 `state == running`(lost 아님) → 25초 안에 `succeeded` · summary `built ok` · 스텝 2/2 → 레인 `idle` → `rcm run lin` succeeded(워커가 재시작을 넘겨 살아 있다) | m5b3 §4.7 · §2(보고 실패 3회 재시도 1·2·4초 · heartbeat 실패는 경고만) · m5b2 §3(`recover_on_start` 는 로컬 잡만 · 원격 running 은 그대로) |

## 명세 대비 메모 (테스트하지 않은 것 · 바꿔 읽은 것)

- **스텝 2/2 는 서버 DB 마커로 본다.** 종료된 잡의 `GET /jobs/{id}` · `rcm top --json` recent 행에는
  `progress` 가 없다(`recent_json`). `Store.markers(job_id)` → `progress_from_markers` 로
  `steps_total 2 · steps_done 2 · summary built ok` 를 확인한다(서버가
  `POST /worker/jobs/{id}/log` 에서 파싱해 둔 것이 정본이라 같은 뜻이다).
- **`lin` 스크립트의 스텝 끝 마커.** 명세 §4 본문의 `::rcm::step_end::1 ok` 는 프로토콜
  (`core/progress.py`: `::rcm::step-end::<ok|fail>`) 에 없는 표기라 마커로 안 읽힌다.
  프로토콜 표기 `::rcm::step-end::ok` 와 스텝 이름 `build`·`test` 를 썼다. 어느 쪽이든 2/2 다.
- **`pgrep -f "rcm worker"`.** 테스트는 `python -m remote_ci_monitor.cli worker …` 로 띄우므로
  그 문자열은 어떤 프로세스에도 없다. 이 서버 포트로 좁힌 `worker --server http://127.0.0.1:<port>`
  패턴을 쓴다(`rcm worker …` 콘솔 스크립트로 띄워도 잡힌다).
- **`qal`(그룹 devices)** 은 명세 §4 프리셋 목록에 있어 설정에 넣었지만 1–7 중 쓰는 시나리오가
  없다(원격 워커 하나 · 레인 1 이라 그룹 직렬화는 자명하다). 서버 쪽 그룹 배제는
  `test_worker_api` 가 잠근다.
- **시나리오 4 의 걸린 시간 8~20초.** heartbeat 1초 · timeout 10초 · janitor 5초 → lost 는 kill 뒤
  9~15초. 하한 8 은 「timeout 전엔 lost 가 아니다」를 잠그기 위한 것.
- **시나리오 7 은 `succeeded` 를 고정한다.** 서버 재시작 공백은 단독 측정 0.6초, 테스트 상한 5초 —
  워커의 보고 재시도 창(1+2+4 = 7초) 안이다. 재시도 창을 넘기는 경우(공백 > 7초)는 이 파일이 다루지
  않는다(그때는 timeout 뒤 lost 가 명세 §2 의 동작).
- **워커 로컬 로그 `<data_dir>/jobs/<id>/log.txt` · 실패 워크스페이스 7일 보존 · `--check` 표 ·
  `[host]` 표본 · 409 뒤 정리 · 등록 재시도(서버 없을 때 5초 간격) · 버전 불일치 409** 는
  두 프로세스 e2e 로 안 본다 — `test_cli_worker`(B) · `test_worker_client`(B) 몫이거나 시간이 든다.
- **자재화 로그(`[rcm] fetching main from app`)가 서버 로그로 가는지**는 명세가 정하지 않아
  단정하지 않는다(시나리오 6 은 스크립트 출력만 본다).
- **취소 뒤 `exit_code`**(TERM 으로 죽은 `sleep` 의 -15) 는 워커 구현이 무엇을 보고할지 명세에
  없어 잠그지 않는다.
