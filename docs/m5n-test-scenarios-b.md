# M5n 테스트 시나리오 B — 저장소 · 서버 · 프로토콜 (2026-11-16)

`docs/m5n-workplan.md` 의 §4.2(보고 경로 · 세대) · §4.4(점검 · 자동 점검 스위퍼 · 1회 제한) ·
§4.5(지정 잡 · `--machine` 권한) · §5.2(claim 의 세 갈래 · `Claimant` · 풀 다시 쓰기) · §5.3
(`POST /jobs` 의 `placement`) · §6(그룹 범위) · §7.1(표본의 `host_key` · 옛 NULL 행) · §8.1(API
모양) · §11.1(DB v23) · §11.3 · §11.4(인터페이스 이름)를 테스트로 옮긴 것이다(test-first, 역할 B).
판정 표(§4.3) · 정규화 · 큐 전역 계산 · 설정 검증은 A 의 몫이고, CLI · 웹 · `rcm init worker` 는
C 의 몫이다 — 여기서는 「값이 저장되고, 프로토콜로 오가고, 문서로 나가는 자리」를 잠근다.
`src/` 와 기존 테스트, A · C 의 파일은 **하나도 안 건드렸다**.

| 파일 | 함수 | 케이스(파라미터 풀면) |
|---|---|---|
| `tests/test_store_m5n.py` | 40 | 40 |
| `tests/test_server_m5n.py` | 46 | 50 |
| `tests/test_checks_sweeper.py` | 18 | 20 |
| `tests/test_e2e_m5n.py` | 2 | 2 |

**오늘(구현 전) 112건 전부 빨갛다** — 이유는 셋 중 하나다.

- 저장소: `no such column: placement` · `Store has no attribute 'upsert_machine'` 류 ·
  `Claimant` import 실패 · `assert 22 == 23`.
- 서버 · 스위퍼: 설정 파일의 `[[probes]]` 를 못 읽어 픽스처가 `ConfigError: unknown
  section(s): probes` 로 선다. 하네스 자체를 따로 확인했다 — 새 키를 걷어 낸 설정으로 돌리면
  실패 이유가 `KeyError: 'generation'`(등록 응답) · `404 not found`(새 라우트) · `App has no
  attribute 'measure_capabilities'` · `Job has no attribute 'target_worker'` 로 바뀐다. 그때
  초록이 되는 셋은 오늘도 참인 대조군이다(`test_worker_spec_and_capabilities_need_a_worker_token`
  · `test_recheck_of_an_unknown_machine_is_404` · `test_a_pool_job_with_a_missing_tool_still_
  fails_tool_missing_on_a_local_lane`).
- e2e: `rcm token --config server.toml add …` 이 같은 `ConfigError` 로 종료 2. 새 키를 걷어 내면
  워커는 등록되지만 `/api/machines` 가 없어 `w-cap never reported` 로 서고, `capgate` 잡은 오늘의
  규칙대로 로컬 레인에서 `tool_missing` 으로 빨개진다 — 바로 이 마일스톤이 고칠 동작이다.

> 구현하면 깨질 **기존** 테스트: `tests/test_job_link.py` 의
> `test_this_build_is_database_version_22_and_jobs_end_with_the_link_columns`(DB 버전 22 ·
> 끝 두 열이 링크)와 `test_a_v21_database_gains_the_link_columns_after_a_backup`(끝 두 열).
> v23 이 열을 뒤에 붙이므로 구현 쪽이 그 단언을 고친다.

## 공통 — 픽스처와 시각

- **아직 없는 이름은 모듈 최상단에서 import 하지 않는다.** `store.Claimant` ·
  `core.capability.{ProbeResult, fingerprint_hash}` 는 테스트 **안에서** 만난다 — 파일 하나가
  통째로 수집 오류가 되면 어느 시나리오가 빨간지 안 보인다(M5h 의 관례).
- **저장소**(`test_store_m5n.py`): 자기 `store` 픽스처(`Store(tmp_path/rcm.sqlite3)`), 고정
  `NOW = 2026-11-16T09:00:00Z` 와 `at(seconds)` 뿐 — sleep · 스레드 · 벽시계 없음.
  - `add(store, preset, *, pool, placement, target, group, scope, priority, **cols)` 가 queued
    잡을 `create_job` 으로 만들고 새 열은 **SQL 로** 박는다(`set_cols`). claim 시나리오가
    `create_job` 의 새 키워드 이름에 묶이지 않게 하려는 것이다(키워드는 가정 B-1 하나가 잠근다).
  - claimant 셋 — `local()`(`@local` · 풀 default · `HOST_A`), `mac2()`(`mac2-worker` · 풀 mac2 ·
    **같은** `HOST_A` — 운영 모양), `mbp()`(풀 mbp · `HOST_B`). `take(store, c, lane)` 은 claim
    경로가 하는 그대로 `pool=c.pool`, 로컬이면 `worker_name=None`, 원격이면 그 이름을 넘긴다.
  - `downgrade_to_v22(path)` 는 이 빌드가 만든 DB 에서 v23 의 열 · 표 · 인덱스를 **있는 것만**
    지우고 `user_version=22` 로 둔다(구현 전에도 돈다).
- **서버**(`test_server_m5n.py` · `test_checks_sweeper.py`): `M5nServer` 는 `tests/test_worker_api.
  py` 의 `WorkerServer` 를 물려받는다(in-process HTTP · 주입 시계 `T0 = 2026-09-06T12:00:00Z` ·
  로컬 워커 스레드 없음 · `admission = "always"`). 다른 점은 설정을 **TOML 파일**에서 읽는다는 것 —
  `[[probes]]` · 새 프리셋 키를 그대로 쓰고 `POST /reload` 로 세대를 올린다.
  - 프로브 `xc` = `python -c "print('Xc 1')"`. 프리셋 `ok` · `needtool`(풀 mac2 · requires 가짜
    도구) · `capgate`(any-capable · requires 가짜 도구) · `anywhere`(any-capable · 요구 없음) ·
    `simcheck`(git_ref · 점검 프리셋) · `scenario`(풀 mac2 · `checks = ["simcheck"]` · requires
    가짜 도구). 가짜 도구 이름 `rcm-m5n-faketool` 은 어느 PATH 에도 없다 — 그래서 **로컬은 언제나
    `capgate`·`scenario` 에 `tool_missing`** 이고, 로컬을 쟀든 안 쟀든 점검이 걸리는 워커는 보고한
    원격 하나다.
  - 워커 토큰 `build-02`(풀 mac2 · `HOST_B`) · `mac2-worker` · `mbp`(`HOST_C`).
  - `srv.reported(worker, presets={…}, probes={…}, accept_any=…)` = 등록 + 현재 세대로 보고(200).
    보고의 `presets` 는 설정의 모든 프리셋에 빈 사실을 깔고 덮는다(`facts()`).
  - 지정 잡 제출의 git_ref 는 40 hex — 서버가 원격을 안 부른다. 스위퍼 시험만 `gitrepo.
    build_remote` 의 tmp bare 레포를 쓴다(`check_ref = "HEAD"` 를 푸는 `ls-remote`).
  - 표본 시나리오는 종료 잡을 `create_job` + SQL 로 만들고 `app._mark_medians_dirty()` 를 부른다.
- **e2e**(`test_e2e_m5n.py`): `tests/test_e2e_worker.py` 의 도우미(`E2EServer` · `rcm` ·
  `clean_env` · `kill_group` · `wait_until`)를 그대로 쓴다. 서버 + `w-cap`(PATH 맨 앞에
  `fakebin/` — `rcm-m5n-faketool` 과 `xc-version.txt` 를 찍는 가짜 `rcm-m5n-fakexc`) +
  `w-nocap`(같은 PATH 에서 `fakebin/` 만 뺐다). 소스는 전부 git_ref. 잡은 `sleep 0.3` 으로 짧게.
  pytest 마커 관례는 이 레포에 없다(`pyproject.toml` · conftest 없음) — git 이 없으면 건너뛴다.

## 베스트 케이스 — 의도한 흐름이 끝까지 된다

| id | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| B1 | 새 DB | jobs 끝 일곱 열이 §11.1 차례 · `machines`·`fingerprints`·`machine_checks` 열 목록 · `jobs_check(worker_name, preset, fingerprint_hash, id)` | `test_a_fresh_database_has_the_v23_columns_tables_and_index` · `test_this_build_is_database_version_23` |
| B2 | v22 파일을 연다 | `user_version 23` · `backup/rcm.sqlite3.v22.bak`(v22 · 새 열 없음) · 옛 잡은 `pool`·`pool` 범위·`sample` True·나머지 None | `test_a_v22_database_migrates_after_a_v22_backup_and_old_rows_get_defaults` |
| B3 | `Claimant` | 필드 차례 · 기본값 `()`·`()`·`True` · frozen | `test_claimant_has_the_spec_fields_and_defaults` |
| B4 | 지정 잡 | 그 워커만 집는다 · `worker_name`·`host_key`·`fingerprint_hash` 가 박힌다 | `test_a_targeted_job_goes_only_to_its_worker` · `test_a_job_targeted_at_local_is_taken_by_a_local_lane_and_leaves_worker_name_null` |
| B5 | any-capable claim | `capable` 에 이름이 있는 워커만 · `pool` → 집은 워커의 풀 · `display_pool` = 대기 풀 | `test_a_capable_job_is_taken_only_by_a_worker_that_lists_its_preset` · `test_a_capable_claim_rewrites_pool_and_keeps_the_waiting_pool_for_display` · `test_a_capable_job_waiting_in_another_pool_moves_to_the_local_pool` |
| B6 | 세 갈래의 차례 | 우선순위 내림 · id 오름 — 갈래와 무관 | `test_priority_then_id_order_holds_across_the_three_kinds` |
| B7 | pool 잡 claim | 풀 그대로 · `host_key`·지문이 박힌다 | `test_a_pool_job_claimed_with_a_claimant_records_host_and_fingerprint` · `test_a_pool_job_stays_in_its_pool` |
| B8 | 머신 표 | 첫 보고는 바뀜 아님 · 다른 지문이면 `previous_json`·`changed_at` · 같은 지문이면 그대로 | `test_the_first_report_of_a_machine_is_not_a_change` · `test_the_same_fingerprint_again_is_not_a_change` · `test_a_different_fingerprint_keeps_the_previous_one_and_the_change_time` · `test_list_machines_is_ordered_by_worker_and_keeps_accept_any` · `test_record_fingerprint_keeps_the_first_sighting` |
| B9 | 점검 기록 | 그 워커 · 그 점검 프리셋의 잡 → `CheckRecord(preset, job_id, job_state, fingerprint_hash)` | `test_check_records_are_the_check_jobs_of_that_worker` · `test_check_records_for_local_read_null_worker_name` |
| B10 | 1회 기록 | `record_machine_check(auto=True)` 뒤 `auto_check_done` True — 그 지문만 | `test_auto_check_done_counts_only_automatic_submissions_per_fingerprint` · `test_a_check_can_be_recorded_twice_for_the_same_fingerprint_at_different_times` |
| B11 | 세대 | 등록 · heartbeat 응답에 `"<epoch>:0"` | `test_register_and_heartbeat_carry_the_generation` |
| B12 | `GET /worker/spec` | `{generation, probe_interval_seconds: 600, probes[], presets[{name, requires, env_passthrough, env}]}` | `test_worker_spec_is_the_probes_and_the_preset_requirements` |
| B13 | `POST /worker/capabilities` | 현재 세대면 200 · 지문 해시가 `/api/machines` 에 | `test_capabilities_with_the_current_generation_are_accepted` · `test_unknown_keys_in_a_report_are_ignored` |
| B14 | reload | 세대 `:N` → `:N+1` · 명세가 새 프리셋을 싣는다 · 옛 세대 보고 409 | `test_reload_bumps_the_generation_and_the_spec_follows` |
| B15 | 로컬 측정 | `App.measure_capabilities()` · 기동 때 `@local` 에 지문 · `missing` 프로브 · 로컬 판정 | `test_the_local_machine_is_measured_by_the_server_process` · `test_the_local_machine_is_measured_at_start` |
| B16 | `GET /api/machines` | §8.1 키 집합 · 지문 블록 · 프리셋 판정 · `checks[]` | `test_a_reported_remote_machine_has_the_spec_shape` · `test_machines_are_readable_without_a_token_under_the_default_read_auth` · `test_a_changed_fingerprint_shows_the_previous_value_and_the_time` |
| B17 | `/api/status` | `server.generation` · `server.machines[]` 요약 · 대기 행 `placement`·`display_pool`·`target_worker`·`concurrency_scope`·`host_key`·`placement_detail` · `presets[]` 의 새 키 | `test_status_carries_the_generation_and_a_summary_per_machine` · `test_a_waiting_capable_job_row_says_any_machine_and_why_none_fits` · `test_a_capable_job_row_lists_an_eligible_worker_once_it_reports` · `test_presets_in_status_carry_placement_checks_scope_and_sample` |
| B18 | `POST /jobs` 의 `placement` | `{mode, eligible_now, blocked}` · 맞는 머신이 없어도 201 로 큐에 선다(결정 99) | `test_a_pool_submission_says_mode_pool` · `test_a_capable_submission_with_no_capable_machine_still_queues_and_says_why` · `test_a_capable_submission_names_the_machines_that_can_take_it_now` |
| B19 | `machine` 지정 | 점검 프리셋은 클라이언트 토큰 · 일반은 admin · `@local` 도 · 그 워커만 집는다 | `test_machine_on_a_check_preset_needs_only_a_client_token` · `test_machine_local_targets_the_local_lanes` · `test_a_targeted_job_is_claimed_only_by_its_machine` |
| B20 | 점검이 pool 잡을 연다 | `check_needed` 면 기다리고, 점검이 통과하면 집는다 | `test_a_pool_job_whose_check_is_needed_waits_until_the_check_passes` |
| B21 | 자동 점검 | 한 회차에 그 워커를 지정한 잡 하나 · 요청자 `rcm:check` · 라벨 `rcm (machine check)` · 우선순위 high · `sample` False · `check_ref` 로 푼 sha · 대기 중엔 `check_running` | `test_a_needed_check_is_submitted_once_targeted_at_that_worker` · `test_a_passed_check_opens_the_preset` · `test_check_ref_picks_the_ref_the_sweeper_resolves` |
| B22 | 다시 점검 | admin · 자동 1회 제한과 무관 · `{jobs:[{preset, job_id}]}` · `machine_checks.auto = 0` | `test_recheck_submits_again_even_after_the_automatic_one_failed` · `test_recheck_without_names_reruns_every_check_of_the_config` |
| B23 | 호스트별 중앙값 | 로컬 표본으로 `measured` | `test_legacy_local_rows_count_as_the_local_host` |
| B24 | e2e 완료 기준 1 | any-capable 잡 셋이 도구 있는 `w-cap` 에서만 · 끝난 잡은 `capp` 풀 · `display_pool` default · 그 호스트 | `test_an_any_capable_job_runs_only_on_the_worker_that_has_the_tool` |
| B25 | e2e 완료 기준 2 | 가짜 xc 출력이 바뀌면 → 자동 점검 1회 → 실패가 남는다 → 다시 점검으로 풀린다 | `test_a_changed_probe_triggers_one_automatic_check_and_a_failure_sticks` |

## 엣지 케이스 — 경계 · 실패 · 경쟁 · 옛 데이터

§14.1 의 필수 번호를 괄호에 단다(이 역할: 1 · 3 · 5 · 7 · 8 · 9 · 11 · 13 · 14).

| id | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| E1 (1) | 지문 바뀜 → 점검 필요 → 자동 점검 | **정확히 한 번** — 두 번째 회차는 빈 목록 | `test_a_second_sweep_does_not_submit_again` |
| E2 (1) | 자동 점검 실패 | `check_failed` · 그 뒤 몇 회차든 다시 안 건다 | `test_a_failed_check_is_not_submitted_again` · `test_a_failed_check_blocks_the_pool_job_with_check_failed` |
| E3 (1) | 취소 · 유실된 자동 점검 | 상태는 `needed` 로 돌아가지만 자동으로 다시 안 건다 | `test_a_cancelled_check_is_not_submitted_again_automatically` · `test_a_lost_check_is_not_submitted_again_automatically` |
| E4 (1) | 지문이 또 바뀐다 | 새 지문으로 한 번 더 — 그 잡에 새 해시 | `test_a_changed_fingerprint_gets_exactly_one_new_check` · `test_the_xcode_incident_replayed_end_to_end_in_process` |
| E5 | down 워커 | 점검을 안 건다 · heartbeat 이 돌아오면 건다 | `test_a_down_worker_gets_no_check_until_it_is_back` |
| E6 | `auto_checks = false` | 아무것도 안 건다(판정은 그대로 `check_needed`) | `test_auto_checks_off_submits_nothing` |
| E7 | ref 해석 실패 | 그 회차 건너뜀 · `checks[].error_code = "ref_unresolved"` · 1회 기록을 쓰지 않는다 | `test_an_unresolvable_ref_skips_the_round_and_says_ref_unresolved` |
| E8 | 점검 잡의 표본 | 끝나도 `list_sample_rows` 에 없다 | `test_a_finished_check_is_not_a_duration_sample` |
| E9 (3) | 같은 호스트의 워커 둘 · `scope = "host"` | 로컬 레인이 mac2-worker 의 도는 잡에 막힌다 | `test_host_scope_blocks_the_other_worker_on_the_same_host` |
| E10 (3) | 같은 호스트 · `scope = "pool"` | 안 막는다(오늘의 동작) | `test_pool_scope_does_not_block_across_pools_even_on_the_same_host` |
| E11 | `host` 범위 · 다른 호스트 | 같은 그룹도 돈다 | `test_host_scope_lets_a_different_host_run_the_same_group` |
| E12 | `global` 범위 | 어디서든 막는다 | `test_global_scope_blocks_everywhere` |
| E13 | 범위는 후보 잡의 것 | 도는 잡이 `pool` 이어도 기다리는 잡이 `host` 면 막힌다 | `test_the_scope_of_the_waiting_job_decides_not_the_busy_one` |
| E14 | `pool` 범위 · 같은 풀 | 오늘처럼 막힌다(회귀) | `test_pool_scope_still_blocks_inside_the_same_pool` |
| E15 (5) | 보고 없는 워커 | any-capable 잡 204 · 보고하면 집는다 | `test_a_worker_without_a_report_does_not_take_a_capable_job` |
| E16 (5) | 세대 어긋난 보고(reload 뒤) | 204 · 판정 `report_stale` | `test_a_report_from_an_older_generation_does_not_take_a_capable_job` |
| E17 (5) | `3 × probe_interval` 보다 낡은 보고 | heartbeat 은 살아도 204 · `report_stale` · 다시 보고하면 집는다 | `test_a_report_older_than_three_probe_intervals_does_not_take_a_capable_job` |
| E18 | 다른 세대로 보낸 보고 | 409 `code: generation_mismatch` · 보고가 남지 않는다(`report_missing`) | `test_capabilities_from_another_generation_are_409_generation_mismatch` |
| E19 | 설정에 없는 프리셋 · 프로브 이름 | 400 · 이름을 댄다 | `test_capabilities_naming_what_the_config_does_not_have_are_400` |
| E20 | 프로브 값 121자 | 400(120자는 200) | `test_a_probe_value_longer_than_120_characters_is_400` |
| E21 | 등록 안 한 워커의 보고 | 409 `worker mbp is not registered` | `test_capabilities_from_an_unregistered_worker_are_409` |
| E22 | `/worker/spec` · `/worker/capabilities` 인증 | 토큰 없음 401 · 클라이언트 · admin 403 | `test_worker_spec_and_capabilities_need_a_worker_token` |
| E23 | `workspace_tools` 보고 | `unknown/workspace_tool` — ineligible 아님(edge 6 의 서버 쪽) | `test_a_workspace_only_tool_is_unknown_not_ineligible` |
| E24 | `missing_passthrough` | 판정에 안 들어간다 | `test_missing_passthrough_is_shown_but_does_not_block` |
| E25 (7) | pool 잡 · 원격 워커에 도구 없음 | 그래도 집힌다 | `test_a_pool_job_with_a_missing_tool_is_still_claimed_by_its_pool` |
| E26 (7) | pool 잡 · 로컬 레인에 도구 없음 | `failed` · `summary_code = tool_missing` (오늘 그대로) | `test_a_pool_job_with_a_missing_tool_still_fails_tool_missing_on_a_local_lane` |
| E27 | pool 잡 · `gated` | 기다리고 다음 잡을 집는다 · 풀리면 집는다 | `test_a_pool_job_whose_preset_is_gated_waits_and_the_next_one_is_taken` |
| E28 (8) | any-capable 잡을 원격이 집고 끝낸다 | `pool` = mac2 · `display_pool` default · `host_key` · 최근 목록은 mac2 풀에 | `test_a_capable_claim_moves_the_job_to_the_workers_pool` |
| E29 (9) | 저장소를 닫고 다시 연다 | `machines` 가 앞 지문을 기억 — 다음 보고가 `changed` | `test_the_machine_table_remembers_the_fingerprint_across_a_restart` |
| E30 (9) | 서버 재시작(같은 data dir) | 재시작 뒤 첫 보고의 `changed_at` · `previous` | `test_the_fingerprint_change_is_not_lost_across_a_server_restart` |
| E31 (11) | 비-admin 이 일반 프리셋에 `machine` | 403 · admin 은 201 | `test_machine_on_a_normal_preset_needs_an_admin_token` |
| E32 | 없는 · 빈 · 숫자 · 목록 `machine` | 400 | `test_an_unknown_or_malformed_machine_is_400` |
| E33 | down 머신 지정 | 409 | `test_a_down_machine_is_409` |
| E34 (13) | 옛 원격 행(host NULL · `worker_name` mbp) | `worker:mbp` 호스트 — 로컬 중앙값에 안 섞인다(오늘은 같은 풀이라 200 으로 섞였다) | `test_legacy_remote_rows_do_not_mix_into_the_local_median` |
| E35 (13) | 다른 호스트 표본만 | `measured_other` · 신뢰도 `low` | `test_only_other_hosts_samples_give_measured_other_with_low_confidence` |
| E36 | `sample = 0` 행 | 잡 · 단계 표본 어디에도 없다 | `test_rows_marked_sample_false_are_not_samples` · `test_sample_rows_leave_out_jobs_marked_sample_false` · `test_step_sample_ids_leave_out_jobs_marked_sample_false` |
| E37 | 표본 행의 호스트 | 날 값(`host_key`) + `worker_name` · 단계 표본은 호스트로 거른다 | `test_sample_rows_carry_the_raw_host_key_and_the_worker_name` · `test_step_sample_ids_can_be_read_for_one_host` |
| E38 (14) | `accept_any = false` | any-capable 은 안 집고 지정 점검은 집는다 | `test_a_worker_that_does_not_accept_any_never_takes_a_capable_job` · `test_a_targeted_job_ignores_gated_capable_and_accept_any` · `test_a_worker_that_does_not_accept_any_takes_targeted_checks_only` |
| E39 | 원격 claimant · 바쁜 레인 | `LaneBusy` 그대로 | `test_a_remote_claimant_still_refuses_a_lane_that_has_a_job` |
| E40 | `claimant=None` | 옛 SQL — 풀 FIFO · 풀 안 그룹 배제 · 호스트 안 박음 | `test_claim_without_a_claimant_is_fifo_within_the_pool_and_records_no_host` · `test_claim_without_a_claimant_keeps_the_in_pool_group_exclusion` |
| E41 | 마이그레이션 · 열이 이미 있다 | 번호가 밀린 DB(`placement`·`host_key`·`machines`·모양 다른 `jobs_check`)도 끝까지 | `test_the_migration_survives_columns_and_tables_that_are_already_there` |
| E42 | heartbeat 끊긴 머신 | `alive: false` · 대기 행의 그 워커 메모 `state: down` | `test_a_machine_without_heartbeat_is_not_alive` · `test_a_down_worker_is_a_down_note` |
| E43 | 다시 점검의 권한 · 대상 · 이름 | 토큰 없음 401 · 클라이언트 403 · 없는 머신 404 · 점검이 아닌 이름 · 목록 아님 400 | `test_recheck_needs_an_admin_token` · `test_recheck_of_an_unknown_machine_is_404` · `test_recheck_names_only_check_presets` |
| E44 (1 · e2e) | 실패 뒤 3.5초(스위퍼 세 바퀴 넘게) | simcheck 잡은 그대로 둘 | `test_a_changed_probe_triggers_one_automatic_check_and_a_failure_sticks` |
| E45 (3 · e2e) | 같은 Mac 의 세 워커 | `host_key` 가 하나(하드웨어 id 를 못 읽는 곳은 셋 다 `worker:` 대체값) | `test_an_any_capable_job_runs_only_on_the_worker_that_has_the_tool` |
| E46 | `create_job` 의 새 키워드 | 지정 · 범위 · 지문 · `sample=False` 가 왕복한다(DB 엔 0) | `test_create_job_takes_the_new_columns_as_keywords` |

## 가정 — 명세가 답을 안 준 것 (구현이 그대로 따르거나, 다르면 §19 에 남긴다)

**저장소**

- **B-1** `Store.create_job(...)` 의 새 키워드는 `Job` 필드 이름 그대로다 — `placement` ·
  `display_pool` · `target_worker` · `concurrency_scope` · `fingerprint_hash` · `sample`(bool).
- **B-2** `MachineRow` 의 속성은 `machines` 열 이름 그대로 + `changed: bool`. 시각
  (`measured_at` · `changed_at`)은 UTC `datetime`, `accept_any` 는 bool. 첫 보고는 `changed=False`
  · `previous_json`·`changed_at` None. 바뀐 보고의 `changed_at` = 그 보고의 `measured_at`
  (저장소는 시계를 따로 받지 않는다). `list_machines()` 는 `worker` 순.
- **B-3** `check_records(worker, presets)` 는 `target_worker = worker` **또는** 그 워커에서 돈 잡
  (`worker_name = worker`)을 돌려준다. `@local` 은 `target_worker = '@local'` 또는 (`worker_name
  IS NULL` 이고 `started_at` 이 있는) 잡 — 아무도 안 집은 비지정 대기 잡은 기록이 아니다.
  `presets` 가 비면 `[]`. 차례는 잠그지 않는다(`check_state` 가 job_id 최댓값으로 고른다).
- **B-5** `auto_check_done` 은 `auto = 1` 행만 센다. 사람이 건 다시 점검(`auto = 0`)은 1회
  제한을 쓰지 않는다.
- **B-8** `SampleRow` 에 `host_key`(날 값, 옛 행은 None)와 `worker_name` 이 붙는다. 옛 NULL 을
  로컬 · `worker:<이름>` 으로 푸는 것은 서버(`_load_medians`)의 일이다 — 저장소는 지금의 로컬
  호스트 키를 모른다. `sample = 0` 행은 `list_sample_rows` · `list_step_sample_ids` 가 아예 안
  준다.
- **B-9** `list_step_sample_ids(key, *, since, limit, host_key=None)` — `host_key` 를 주면 그
  호스트의 행만(정확히 같은 값), None 이면 오늘처럼 전부. 옛 NULL 행을 단계 표본에서 어떻게 풀지는
  B 가 잠그지 않는다(잡 중앙값의 풀이는 E34 가 잠근다).
- 새 DB 의 jobs 열 차례는 마이그레이션과 같다 — 끝 일곱 개가 §11.1 차례(`placement` …
  `sample`). `machine_checks` 의 `submitted_at` · `fingerprints.first_seen_at` 은 epoch 초(REAL).
- `claimant` 를 넘긴 claim 은 모든 갈래에서 `host_key` · `fingerprint_hash` 를 박는다(§7.1).
  `claimant=None` 은 둘 다 NULL 로 둔다.

**프로토콜**

- **B-4** `POST /worker/capabilities`: 성공 200(본문은 잠그지 않는다). 세대가 다르면 409 +
  `code: "generation_mismatch"`(그 보고는 저장하지 않는다). 등록 안 한 워커는 409 `worker <이름>
  is not registered`(claim 과 같은 문장). 설정에 없는 **프리셋 · 프로브 이름은 400**(그 이름이
  `error` 에 들어간다) — 「검증」으로 읽었다. 프로브 `value` 가 120자를 넘으면 400(프로브 이름을
  댄다). 알 수 없는 **최상위 키는 무시**한다.
- **B-12** 세대는 `^\d+:\d+$`, 기동 직후 `:0`, 성공한 reload 마다 `+1`. 테스트는 reload 할 때
  언제나 파일을 실제로 바꾼다(프리셋 하나를 더하거나 프로브 시한을 바꾼다).
- **B-13** `GET /worker/spec`: 키는 정확히 넷. `probes[]` 항목은 `{name, argv, timeout_seconds}`,
  `presets[]` 는 **설정의 모든 프리셋을 설정 차례로**, 항목 키는 정확히 `{name, requires,
  env_passthrough, env}`. 다른 `/worker/*` 와 같은 인증(401 · 403 `worker token required`). 등록을
  요구하지 않는다.
- 등록 응답과 heartbeat 응답에 `generation` 키가 붙는다(옛 키는 그대로).

**읽기 문서**

- **B-14** `GET /api/machines` = `{generation, machines[]}`. 항목 키는 §8.1 그대로 열한 개.
  `fingerprint` = `{hash, measured_at, stale, changed_at, probes}`, 시각은 `iso()` 형식(`…Z`),
  `probes[이름]` = `{state, value, previous}`. **등록했지만 보고가 없는 워커도 항목이 있다** —
  `fingerprint.hash` None · 판정 `report_missing`. `host_label` 은 `(<host_key 앞 넉 자>)` 로
  끝난다. 판정 항목은 `{state, code, detail}`, `ok` · `report_missing` 의 detail 은 `{}`,
  `tool_missing` 은 `{"tools": [...]}`(basename). `checks[]` 는 설정의 점검 프리셋마다 하나 —
  `{preset, state(passed|running|failed|needed), job_id, auto_submitted, error_code}`. 취소 ·
  유실된 점검은 `needed`. 원격 `kind` 는 `"remote"`, 로컬은 id `@local` · `kind "local"` ·
  풀 `default`. 기본 `read_auth = "none"` 에서 토큰 없이 읽힌다.
- **B-15** `/api/status`: `server.generation` · `server.machines[]`(§8.1 의 키를 **포함**).
  `eligible_count` 는 판정이 eligible 인 **모든** 프리셋 수(pool 프리셋 포함), `preset_count` 는
  설정의 프리셋 수. 큐 행의 `placement` 는 **DB 값**(`"capable"` · `"pool"`) — `pool` 처럼 잡 필드를
  그대로 싣는다. 대기 중인 any-capable 잡의 `display_pool` 은 **null 이 아니라 대기 풀 이름**이다
  (제출 때 `display_pool = pool` 로 박거나 JSON 이 풀어 쓴다). `placement_detail` 항목의 키는 정확히
  `{worker, display_name, state, code, detail}`, eligible 인 워커도 싣는다, down 워커는
  `state: "down"`. `presets[]` 의 `placement` 는 **설정 문자열**(`"any-capable"` · `"pool"`),
  `checks` 는 목록, `sample` 은 해석된 bool(점검 프리셋 기본 False).
- **B-16** `POST /jobs` 응답의 `placement` = 정확히 `{mode, eligible_now, blocked}`. `mode` 는
  `"pool"` · `"any-capable"`(설정 문자열 — 사람이 읽는 말이다). 지정 잡의 `mode` 는 잠그지 않는다.
  `eligible_now` 는 워커 id 목록, `blocked[]` 항목은 정확히 `{worker, code, detail}` — eligible 인
  워커는 `blocked` 에 없다.
- **B-23** 대기 잡의 기대 시간은 그 잡이 들어갈 레인의 호스트로 잰다. 기본 풀 잡 + 원격 워커 없음
  → 로컬 호스트. 옛 로컬 행은 로컬 호스트, 옛 원격 행은 `worker:<이름>`. 다른 호스트로만 물러난
  값의 `confidence` 는 `low`(오늘의 `confidence()` 가 `measured` 가 아니면 `low` 를 주는 그대로).
  표본이 없으면 `source: "default"`(프리셋 `ok` 에 `expected_seconds` 가 없다).

**지정 잡 · 점검**

- **B-6** 지정 잡은 제출 때 **그 워커의 풀**에 선다(`@local` → `default`). `--machine` 검사의
  차례: 형식 · 존재(400) → 권한(403) → down(409) 은 테스트가 섞어 부르지 않는다 — 각각 홀로 잠근다.
  `machine` 이 문자열이 아니거나 빈 문자열이면 400.
- **B-7** 자동 점검 잡은 **제출 때** 그 워커의 현재 지문 해시를 `fingerprint_hash` 에 박는다
  (claim 때 다시 박혀도 같은 값). 그래야 대기 중인 점검이 「이 지문으로 기다리는 것」(§4.3 의 7번
  `check_running`)으로 읽힌다 — 안 박으면 대기 중에 `check_needed` 로 보인다. 자동 점검 잡의 풀은
  B-6 과 같다. 요청자 이름 `rcm:check` · 라벨 `rcm (machine check)` · 우선순위 1 · `sample` False ·
  소스 `git_ref`(sha 는 스위퍼가 `check_ref` 로 푼 것).
- **B-10** 스위퍼 한 회차 = `App.sweep_checks()`. 반환값은 **이번 회차에 건 잡 id 목록**(iterable
  of int). `auto_checks = false` 면 빈 목록. 로컬 측정은 스위퍼가 하지 않는다.
- **B-18** `POST /machines/{id}/recheck`: admin(토큰 없음 401 · 클라이언트 403), 없는 머신 404.
  본문 `{checks?: [이름…]}` — 이름은 점검 프리셋이어야 하고(아니면 400) 목록이어야 한다(문자열이면
  400). 빼면 설정의 모든 점검 프리셋. 응답 `{"jobs": [{"preset", "job_id"}]}`. 자동 1회 기록과
  무관하게 걸고 `machine_checks` 에 `auto = 0` 으로 남긴다 — 그 점검의 `auto_submitted` 는 False.
- **B-20** ref 해석 실패는 `machine_checks` 를 쓰지 않는다(다음 회차가 다시 해 본다). 그동안
  `checks[]` 는 `{state: "needed", job_id: null, auto_submitted: false, error_code:
  "ref_unresolved"}`.
- **B-21** 로컬 측정의 공개 이름은 `App.measure_capabilities()`(§4.2)이고 `start()` 가 부른다.
  테스트는 기동 없이 직접 부르기도 한다.

**e2e**

- **B-11** `[server] check_sweep_seconds` 의 하한은 1 이하다(e2e 가 1 을 쓴다).
  `probe_interval_seconds` 는 하한 60 을 쓴다.
- **B-25** 원격 워커는 heartbeat 응답의 `generation` 이 자기 것과 다르면 `GET /worker/spec` →
  다시 재고 → `POST /worker/capabilities` 한다(§4.2) — e2e 는 reload 로 세대를 올려 재측정을
  일으킨다(프로브 주기 60초를 기다리지 않는다).
- 원격 워커의 프로브는 잡과 같은 PATH 규칙으로 돈다 — `w-cap` 의 가짜 `rcm-m5n-fakexc` 는 그
  워커 프로세스의 PATH 로 찾는다. 서버의 로컬 측정은 서버 프로세스의 PATH 로 찾는다(그래서 `missing`).
- 같은 Mac 의 워커들은 같은 `host_key` 를 보고한다(하드웨어 UUID 해시). 하드웨어 id 를 못 읽는
  환경(컨테이너 등)에서는 셋 다 `worker:` 대체값이면 통과로 본다.
