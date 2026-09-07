# M5b-2 테스트 시나리오 B — `/worker/*` 프로토콜 · down/lost 판정 (2026-09-07)

`docs/m5b2-workplan.md` §1(인증) · §2(등록) · §3(claim · 보고 · heartbeat · 취소 · 재시작) · §4(down · lost ·
`server.workers[]` · `pools[].lanes` · health) · §6(이름 고정 · 로그 · 호스트 표본 · 종료 규칙)을
`tests/test_worker_api.py` · `tests/test_worker_lost.py` 로 옮긴 것이다(test-first, 역할 B). `src/` · 기존
테스트는 건드리지 않았다. 저장소 함수 단위(A) · CLI/설정/화면(C)은 각자 문서에 있다.

공통: `test_worker_api.WorkerServer(tmp_path, lanes=1)` — in-process HTTP + **주입 시계**(`srv.clock.advance()`)
· 로컬 워커 스레드는 안 띄운다(로컬 레인 1 은 idle 로만 보인다) · 토큰 `alice`/`bob`(client) · `admin` ·
워커 `build-02`/`build-03`(기본 풀) · `lin-01`(linux) — 워커 이름 = 토큰 이름 · 프리셋 `ok` · `lin`(pool linux) ·
`qa`/`qal`(그룹 `devices`, 풀별) · `deploy`(git_ref, 40 hex ref 라 git 없이 queued) · 도우미
`queued_job` · `cache_job`(manifest+blob) · `git_ref_job` · `register/claim/heartbeat/phase/log/finish/tree` ·
`row(jid)` · `worker_lane(name, lane)` · `pools()`. janitor 루프는 기다리지 않고 `app.mark_lost_workers(now)` 를
직접 부른다(§6). 벽시계 sleep 은 long-poll 시험 하나(≈0.3초)뿐이다.

## `tests/test_worker_api.py`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | `/worker/*` 7 라우트 — 토큰 없음·무효 401(Bearer 챌린지) · client·admin 403 `worker token required` · 인증이 본문·잡 존재보다 먼저 | `test_worker_routes_require_a_worker_token` (×7) | §1 L11 · §6 L66 |
| 2 | 워커 토큰으로 `POST /jobs` · cancel · priority · manifest · `PUT tree` · `/pause` · `/resume` · `/api/eta` → 403 `worker tokens cannot use the client API`, 아무것도 안 바뀐다 | `test_worker_token_cannot_use_the_client_api` (×8) | §1 L11 · §6 L66 |
| 3 | `read_auth = basic` 에서 워커 토큰(Bearer)으로 `/api/status` 200 · `/api/health` 는 늘 200 · whoami 의 name | `test_worker_token_still_reads_status_under_basic_read_auth` | §1 L11 · §6 L66 |
| 4 | 신규 등록 → 본문 정확히 `{name, pool, lanes, heartbeat_seconds, worker_timeout_seconds, claim_wait_seconds}` · 저장 행(`registered_at == last_seen_at == 서버 시각`) · `server.workers[]` 에 `<name>/<lane>` idle(`since = registered_at`, `lane` int) · 잡이 없어도 그 풀이 `pools[]` 에 lanes 와 함께 · `server.lanes` 는 로컬 수 | `test_register_new_worker_returns_the_protocol_parameters` | §2 L20 · §4 L43-44 · §6 L65 |
| 5 | 재등록(upsert) — 풀 변경도 200 · lanes 갱신 · `last_seen_at` 갱신 · 행 하나 · `pools[].lanes` 가 따라간다 | `test_register_again_updates_lanes_pool_and_last_seen` | §2 L20 · §3 L38 |
| 6 | `version` 불일치 → 409 `worker version X, server Y — install the same release` · 등록 안 됨 · claim 은 409 `worker <name> is not registered` | `test_register_rejects_a_different_release` | §2 L20 |
| 7 | 풀 이름 규칙 위반(`bad pool` · `""` · `-x` · 5) · lanes 0/65/`"2"`/true → 400, 등록 안 됨 | `test_register_rejects_bad_pool_names_and_lane_counts` (×8) | §2 L20 |
| 8 | 재등록 = 새 프로세스 — 그 워커의 running·cancelling 잡이 lost `worker <name> restarted without the job` · 다른 워커 잡은 그대로 · 레인이 비어 새 claim 이 된다 | `test_register_marks_the_workers_running_jobs_lost` | §3 L38 |
| 9 | 빈 풀 + `wait_seconds 0` → 204 · 잡이 있으면 200 `{job: <queue 행 JSON>, tree_url, preset{argv, timeout_seconds, env, env_passthrough, source_modes, repo?}}` · DB `worker_name`/`lane`/`started_at` · 큐 행 `materializing` · `server.workers[]` busy 항목 전체(`since = started_at`) · 같은 레인 재claim 409 | `test_claim_returns_204_when_empty_and_the_job_with_preset_when_not` | §3 L28 · §4 L43 · §6 L63 |
| 10 | 풀 격리 — linux 워커는 기본 풀 잡을, 기본 풀 워커는 linux 잡을 못 받는다(204) | `test_claim_is_isolated_per_pool` | §3 L28 |
| 11 | 그룹 배제는 풀 안에서 — 같은 `devices` 라도 다른 풀은 동시에 · 막힌 행은 `blocked_by_group` · 보유 잡이 끝나면 받는다 | `test_group_exclusion_applies_within_the_pool_only` | §3 L28 · m5 「프로토콜」 claim |
| 12 | `(worker, lane)` 에 running·cancelling 잡이 있으면 409 `lane N already has job #M` · 다른 레인은 된다 | `test_claim_refuses_a_lane_that_already_has_a_job` | §3 L28 · 리뷰 must-fix 3 |
| 13 | lane 0 · 등록 수 초과 · 음수 · 문자열 · bool → 400, 잡은 queued | `test_claim_validates_the_lane_against_the_registered_count` (×5) | §3 L28 |
| 14 | 등록 전 워커 토큰의 claim·heartbeat → 409 `worker <name> is not registered` | `test_claim_and_heartbeat_need_a_registered_worker` | §3 L28 · §6 L63 |
| 15 | claim 순서 = `(-priority, id)` — high → normal → low | `test_claim_hands_out_jobs_in_priority_then_id_order` | §3 L28 |
| 16 | 정지 중엔 claim 204 · heartbeat `paused true` · resume 뒤 받는다 | `test_claim_hands_out_nothing_while_the_server_is_paused` | §3 L33 (해석 ①) |
| 17 | long-poll — `wait_seconds 2` 로 기다리다 0.3초 뒤 업로드된 잡을 1.5초 안에 200 | `test_claim_long_polls_and_wakes_when_a_job_is_uploaded` | §3 L28 |
| 18 | `GET /worker/jobs/{id}/tree` → 올린 tar.gz 바이트 · `Content-Length` · `Content-Type: application/gzip` | `test_tree_returns_the_uploaded_tar_with_length_and_gzip_type` | §3 L29 |
| 19 | 캐시 잡 → manifest+blob 조립 tar(경로·크기·내용·실행 비트·심링크 = manifest) · `jobs/<id>/tree.tar.gz` 에 남아 두 번째는 같은 바이트 | `test_tree_of_a_cache_job_is_assembled_from_manifest_and_blobs` | §3 L29 · §6 L63 |
| 20 | git_ref 잡 → 404 `git_ref jobs are fetched by the worker` · claim 응답의 `source`(repo·ref·sha) · `preset.repo`/`source_modes` | `test_tree_of_a_git_ref_job_is_404` | §3 L29 |
| 21 | 다른 워커의 잡·아직 안 잡힌 잡 403 · 클라이언트 토큰 403 `worker token required` · 없는 잡 404 | `test_tree_is_only_for_the_worker_that_claimed_the_job` | §3 L35 |
| 22 | phase `materializing`/`executing` → 큐 행 `progress.phase`·`reason` · 모르는 phase 400 · 남의 잡 403 · 없는 잡 404 | `test_phase_is_reflected_in_status_and_validated` | §3 L30 |
| 23 | log raw 바이트 append(`GET /jobs/{id}/log` 로 같은 바이트) · 서버가 마커 파싱(steps·step) · 마커 시각 = 서버 수신 시각 · `last_output_at` 갱신 | `test_log_appends_raw_bytes_and_parses_markers_on_the_server` | §3 L31 · §6 L68 |
| 24 | 요청 경계에서 잘린 마커는 다음 요청과 이어 붙는다 — 개행 전엔 마커 아님 · 파일엔 조각도 그대로 | `test_log_joins_a_marker_split_across_two_requests` | §6 L68 |
| 25 | chunked · Content-Length 없음 → 411, 파일 그대로 | `test_log_requires_content_length_and_rejects_chunked` | §6 L68 |
| 26 | Content-Length > 4 MiB → 본문 읽기 전에 413(1바이트만 보내도 즉시) | `test_log_rejects_bodies_over_4_mib_before_reading_them` | §3 L31 · §6 L68 |
| 27 | `Content-Type` 이 octet-stream 이 아니면 415, 파일 그대로 | `test_log_needs_the_octet_stream_content_type` | §6 L68 (해석 ②) |
| 28 | log — 남의 잡·안 잡힌 잡 403 · 없는 잡 404 · 파일 안 생김 | `test_log_is_only_for_the_worker_that_claimed_the_job` | §3 L35 |
| 29 | finish succeeded — 마커 summary · failed_step None · `job_seconds` · 전이 · 응답 `{job_id, state}` · 레인 idle · recent | `test_finish_succeeded_takes_the_summary_from_markers` | §3 L32 · §6 L69 |
| 30 | finish failed — summary `exit N` · failed_step = step-end fail 스텝 또는 마지막 스텝 | `test_finish_failed_uses_exit_code_and_the_failing_step` | §3 L32 · §6 L69 |
| 31 | finish failed + `exit_code null` + `summary`(워커 쪽 자재화 실패) → 보낸 요약 그대로 | `test_finish_failed_with_a_summary_and_no_exit_code_keeps_the_summary` | §3 L32 (해석 ③) |
| 32 | finish timed_out — summary `format_limit`(`limit 1m`) · failed_step 마지막 스텝 · exit_code 그대로 | `test_finish_timed_out_uses_the_limit_summary` | §3 L32 · §6 L69 |
| 33 | cancel → cancelling → heartbeat `cancel` 목록 → finish cancelled → `cancelled by <이름>` · `cancelled_by` | `test_finish_cancelled_after_the_client_asked_names_the_canceller` | §3 L36 |
| 34 | finish lost + summary → lost, 요약은 워커 것 | `test_finish_lost_keeps_the_workers_summary` | §3 L32 |
| 35 | outcome 밖의 값 · exit_code 문자열/bool/실수 → 400, running 그대로 | `test_finish_validates_outcome_and_exit_code` | §3 L32 |
| 36 | 종료 뒤 finish·phase·log → 409 `job #N is succeeded` · 로그 파일·마커·상태 불변 | `test_late_reports_after_a_terminal_state_are_409_and_change_nothing` | §3 L35 · 리뷰 must-fix 4 |
| 37 | finish — 다른 워커 403 · 없는 잡 404 | `test_finish_is_only_for_the_worker_that_claimed_the_job` | §3 L35 |
| 38 | heartbeat 응답 정확히 `{cancel, paused, timeout_seconds}` · `cancel` 은 이 워커의 cancelling 만 · `paused` 는 `/pause` 를 비춘다 | `test_heartbeat_response_lists_only_this_workers_cancelling_jobs` | §3 L33 |
| 39 | `last_seen_at` 은 서버 수신 시각 — 정확히 timeout 은 idle, +1 은 down · heartbeat 이 오면 idle · lanes 복귀 · payload 시각 무시 | `test_heartbeat_updates_last_seen_and_brings_the_worker_back` | §2 L22 · §4 L42 |
| 40 | `jobs` 목록에 없는 이 워커의 running 잡 → lost `restarted without the job` · `jobs` 생략은 조정 없음 · 다른 워커 잡은 그대로 · recent 순서 | `test_heartbeat_without_the_running_job_marks_it_lost` | §3 L33 · §6 L63 |
| 41 | `host_sample` → 그 풀의 `hosts[]`(`name` = 워커, `source = "worker"`, `sampled_at` = 서버 시각, `age 0`, 값 보존) · default/linux 풀 분리 · 최신 표본으로 교체 | `test_heartbeat_host_sample_becomes_the_pools_hosts_entry` | §3 L33 · §6 L64 |
| 42 | bool 숫자 · `"NaN"` 문자열 · NaN/Infinity 리터럴 · 모르는 키만 · 중첩 dict 의 모르는 키 · dict 아님 → 표본만 버리고 200, `last_seen_at` 은 갱신 | `test_heartbeat_drops_an_invalid_host_sample_but_still_returns_200` (×8) | §6 L64 |
| 43 | `top` 12 개 → 앞 10 개만 남기고 표본은 받는다 | `test_heartbeat_truncates_an_oversized_top_list` | §6 L64 |

## `tests/test_worker_lost.py`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | timeout 넘긴 워커 — `mark_lost_workers(now)` 가 id 목록 · lost + `worker <name> unreachable for <N>s`(N = 마지막 요청 이후 초) · `job_finished` 이벤트 · 워커 down · `pools[].lanes` 로컬만 · queued 행 `worker_down` · 재호출 [] · 살아 있을 땐 busy `since = started_at`, idle `since = registered_at` | `test_worker_past_timeout_loses_its_running_job_and_shows_down` | §4 L42-44 · §6 L63 |
| 2 | 다시 heartbeat → idle · lanes · hosts 복귀 · lost 잡은 이어받지 않음 · down 동안 hosts 비어 있음 · 늦은 finish 409 `job #N is lost` | `test_heartbeat_brings_a_down_worker_back_up_without_resuming_the_lost_job` | §4 L42 · §6 L64 · m5 「서버 쪽 lost」 |
| 3 | down 워커의 cancelling 잡은 lost (cancelled 아님) — kill_at + 2×heartbeat 를 넘겨도 | `test_cancelling_job_of_a_down_worker_becomes_lost_not_cancelled` | §3 L36 · 리뷰 must-fix 5 |
| 4 | 살아 있는 워커가 finish 를 안 하면 kill_at + 2×heartbeat 뒤 cancelled `worker did not confirm the cancel` · `cancelled_by` · 이벤트 · 그 전엔 cancelling · 레인 복귀 · 늦은 finish 409 | `test_unconfirmed_cancel_of_a_live_worker_is_closed_as_cancelled` | §3 L36 |
| 5 | 같은 저장소로 `App.start()` — 로컬 잡(`worker_name NULL`)만 lost `server restarted…` · 원격 running 잡은 그대로(레인 busy 로 재계산) · timeout 뒤 `mark_lost_workers` 가 닫는다 | `test_server_restart_keeps_remote_jobs_and_loses_local_ones` | §3 L37 · §6 L61 · 리뷰 must-fix 1 |
| 6 | 로컬 2 레인 + default 워커 3+1 + linux 4 → `pools[].lanes` 6/4 · `server.workers[]` 로컬 먼저, 원격 이름순·레인순 · `server.lanes` 2 · 일부만 살아남으면 그 합만 | `test_pool_lanes_are_local_plus_live_remote_lanes_and_workers_are_ordered` | §4 L43-44 · §6 L65 |
| 7 | `/api/health` — 등록 워커가 전부 down 인 풀 → 200 · `ok true` · `pools_without_workers: ["linux"]` · `workers_down []` · 하나라도 살면 빠진다 · 기본 풀은 안 오른다 | `test_health_lists_pools_whose_registered_workers_are_all_down` | §4 L45 |

## 명세가 열어 둔 곳을 테스트가 정한 값 (구현이 따를 것)

1. **정지 중 claim** — `/pause` 상태에선 claim 이 잡을 주지 않고 204(로컬 워커가 정지 중 claim 을 안 하는 것과 같다).
   heartbeat 의 `paused` 로 알린다.
2. **log 의 `Content-Type`** — `application/octet-stream` 이 아니면 **415**.
3. **finish 의 `summary?`** — 마커 summary 가 없고 본문에 `summary` 가 오면 그 값이 요약(워커 쪽 자재화 실패 `exit_code: null`
   보고용). 둘 다 있을 때의 우선순위는 잠그지 않았다.
4. **claim 응답 `job`** — 명세대로 `<queue 행 JSON>` 의 키(`requester{name,label}` · `concurrency_group` · `state` · `lane` ·
   `inputs` · `source` · `pool` · `priority`)를 잠갔다. 응답 최상위 키는 정확히 `{job, tree_url, preset}`.
5. **finish 응답** — `{job_id, state}`(cancel 과 같은 모양). phase/log 응답 본문은 잠그지 않았다.
6. **heartbeat 응답** — 정확히 `{cancel, paused, timeout_seconds}`(명세 §3 문면).
7. **lost 요약의 N** — 서버가 그 워커의 요청(register·claim·heartbeat)을 마지막으로 받은 뒤 지난 초(`int`).
8. **워커가 등록된 풀은 잡이 없어도 `pools[]` 에 보인다**(lanes 와 hosts 를 실을 자리).
9. **취소 미확인 종결의 위치** — heartbeat 처리 중이든 `mark_lost_workers` 든 상관없이, `kill_at + 2×heartbeat` 를 넘긴 heartbeat
   뒤에는 cancelled 여야 한다. `mark_lost_workers` 의 반환값은 lost 만 센다.
10. **lane 생략** — 잠그지 않았다(구현은 1 로 본다). 잘못된 값(0 · 초과 · 음수 · 문자열 · bool)만 400.
11. **`/api/health` 의 `pools_without_workers`** — 늘 있는 키(비면 `[]`).
