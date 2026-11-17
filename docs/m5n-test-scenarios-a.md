# M5n 테스트 시나리오 A — 순수 계층: 능력 판정 · 전역 큐 · 설정 (2026-11-16)

`docs/m5n-workplan.md` 의 §4(지문 §4.1 · 판정 §4.3 · 점검 §4.4) · §6(그룹 범위) · §7(호스트별 추정 ·
전역 큐 · 새 이유) · §11.2(설정 키) · §11.4(인터페이스 이름)를 §14 표의 역할 A 행으로 옮긴 것이다
(test-first). `src/` 와 기존 테스트는 건드리지 않았다. **v23 마이그레이션 · `store.claim` 의 세 갈래 ·
보고 경로 · 세대 · 자동 점검 스위퍼 · `accept_any` 로 후보를 거르는 일 · `--machine` 권한**은 역할 B,
**CLI · 웹 · `rcm init worker`** 는 역할 C 가 맡는다.

여기 있는 것은 시계도 DB 도 소켓도 안 본다. 예외는 `runner.measure_tools` 하나로, tmp 폴더에 만든
실행 파일을 `shutil.which` 로 찾는다.

| 파일 | 테스트 | 지금 실패하는 이유 |
|---|---|---|
| `tests/test_capability.py` | 93 | `ImportError: cannot import name 'capability'`(수집 오류 — 모듈이 없다) |
| `tests/test_queue_m5n.py` | 50 | 47 실패: `expected_on`·`Placement` 속성 없음 · `Job`/`WorkerInfo` 에 `host_key`·`concurrency_scope`·`sample` 키워드 없음 · `ACTIONABLE_REASONS` 가 옛 순서 · 전역 호출이 풀을 섞어 골든과 1,293행이 다르다. **3 통과**: 골든 생성기 대조 · 오늘의 풀별 호출 = 골든 · 골든이 덮는 규칙 |
| `tests/test_config_m5n.py` | 68 | 전부 실패: `[[probes]]`·새 프리셋 키가 「unknown key/section」 으로 거절된다(헬퍼가 그 거절을 「검증」으로 치지 않는다) · `ServerSection` 에 새 키 없음 |
| `tests/fixtures/m5n_queue_golden.json` | — | 오늘의 코드로 만든 동치성 골든(216 KB, 픽스처 200개 · 1,962행) |

## 공통 — 픽스처와 도우미

시각은 `jobfactory.NOW` 하나에서만 온다. 설정은 `jobfactory.CFG`(기본 `QueueConfig`).

**`test_capability.py`**

```python
GEN = "1731740000:3"          # 현재 세대
MAX_AGE = 1800.0              # 3 × probe_interval_seconds — verdict 는 곱한 값을 받는다
XCODE_265 / XCODE_272 / IDB_118          # Xcode 사고의 앞 · 뒤 프로브 값
FP_265 / FP_272 = fingerprint_hash(...)  # 그 두 지문

def preset(name="scenario-qa", *, checks=(), requires=())   # Preset(checks=…)
def report(facts=None, *, preset_name=…, probes=…, fp_hash="auto", generation=GEN, measured_at=NOW)
def rec(job_id, state, fp=FP_272, name="sim-check")          # CheckRecord
def judge(preset, report, records=())                        # verdict(..., generation=GEN, now=NOW,
                                                             #         max_age_seconds=MAX_AGE)
def _tool(folder, name, *, executable=True)                  # tmp 실행 파일
```

**`test_queue_m5n.py`**

```python
def lane(worker, n=1, state="idle", *, pool="default", host="h1", job_id=None, since=None)
    # WorkerInfo + host_key. worker=None 은 로컬 레인
def mk(id, key="gate:full", state=QUEUED, **kw)
    # jobfactory.job + 새 필드(placement · display_pool · concurrency_scope · host_key · sample …)
def run(jobs, wk, **kw) -> {job_id: QueueRow}                # compute_queue 한 번, 결과를 id 로
def only(*workers)                                           # 잡마다 같은 후보 집합의 placement
def med(seconds, n=5)                                        # Median
```

**골든 생성기**(같은 파일 아래쪽 — 다시 만들 수 있게 테스트 파일 안에 둔다)

```python
golden_spec(seed) -> dict      # 원시값만의 픽스처 명세(결정적: random.Random(seed))
golden_build(spec)             # → 잡 · 워커 · 풀별 중앙값 · 단계 중앙값 · 진행 (오늘의 모델 필드만)
per_pool_rows(spec)            # 오늘의 server._queue_rows 모양 — 풀마다 compute_queue
global_rows_legacy(spec)       # 새 전역 호출 · placement=None · 새 kwarg 없음
global_rows_placed(spec)       # 새 전역 호출 · 같은 풀 규칙의 placement · host_medians · host_step_medians
```

다시 쓰기: `./.venv/bin/python tests/test_queue_m5n.py --write-golden`. **오늘의 규칙을 일부러 바꿀
때만**이다. 생성기가 흔들리면(파이썬 판 · 코드 수정) `test_golden_fixture_matches_its_generator` 가
픽스처마다 명세 digest 로 먼저 잡는다.

픽스처 규칙: 풀 2~3개(`default` + `mac2`/`lab` 중 1~2), **풀마다 호스트 하나**(`h-<pool>`), 풀마다
워커 1개 이상(down 일 수 있다). 기본 풀은 로컬 레인 1~3 + 가끔 원격 `dflt-w`(같은 호스트). key 는
풀마다 따로(`<pool>:<preset>:<n>`)라 다른 풀의 실측이 섞이는 `measured_other` 가 생기지 않는다 —
그건 새 규칙이지 동치성이 아니다. 도는 잡(running · cancelling · materializing · 조용함 · 단계 진행),
대기 잡(queued · uploading · 우선순위 셋 · 그룹 `sim`/`gate`), down · held 레인, paused 를 섞는다.
골든이 잠그는 칸: `id · position · reason · lane · ahead_job_id · blocked_by · wait_seconds ·
finish_at · expected_seconds · source · sample_count · remaining_seconds · shared · quiet · stuck_code`.

**`test_config_m5n.py`**

```python
REPO       # [[repos]] app
SIM_CHECK  # git_ref 를 받는 점검 프리셋 sim-check
def load(tmp_path, text)                       # load_server_config(..., environ={}, check_tools=False)
def fails(tmp_path, text, *, unknown_key=False) # ConfigError 메시지. 「unknown key/section」 은 거부
def probe(name="xcode", argv=…, extra="")       # [[probes]] 조각
def preset(name="gate", extra="")               # [[presets]] 조각
```

## 베스트 케이스

| id | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| A-B1 | 상수가 선 위의 문자열이다 | `@local` · `ok/error/missing/timeout` · `eligible/ineligible/unknown` · `passed/running/failed/needed` | `test_constants_are_the_wire_strings` · `test_probe_result_is_frozen_and_not_stale_by_default` |
| A-B2 | `xcodebuild -version` 출력 정규화 | `ok`, `"Xcode 27.2 · Build version 27B52"`(앞 두 줄을 ` · ` 로) | `test_normalize_joins_the_first_two_nonempty_lines` · `test_normalize_accepts_str_and_bytes_alike` |
| A-B3 | 지문 해시 | 16자 소문자 hex, 순서 무관, 값이 바뀌면 바뀐다 | `test_hash_is_16_lowercase_hex` · `test_hash_ignores_mapping_order` · `test_hash_changes_with_a_value` |
| A-B4 | timeout 없는 회차의 지문 | 측정값 그대로, `hash == fingerprint_hash(current)`, `stale False` | `test_settle_without_timeouts_is_the_measurement_itself` |
| A-B5 | Xcode 26.5 → 27.2 의 바뀐 항목 | `{"xcode": (26.5 값, 27.2 값)}` | `test_changed_probes_reports_old_and_new_values` |
| A-B6 | 현재 지문으로 통과한 점검 | `(passed, 그 기록)` | `test_check_state_success_under_current_fingerprint_passes` |
| A-B7 | 점검이 모두 통과 · 도구 다 있음 | `eligible` · `ok` · detail `{}` | `test_verdict_ok_when_every_check_passed` |
| A-B8 | 하드웨어 UUID → 호스트 키 | `sha256(raw)[:16]`, 원문은 안 남는다. 같은 UUID 의 두 워커는 같은 호스트 | `test_host_key_is_sha256_prefix_of_the_hardware_id` · `test_host_key_never_contains_the_raw_id` · `test_same_hardware_gives_the_same_host_for_two_workers` |
| A-B9 | PATH 의 절대 항목에 도구가 있다 | `measure_tools` → `([], [])` | `test_measure_tools_finds_tools_on_absolute_entries` · `test_measure_tools_nothing_required` |
| A-B10 | 새 상수 · 기본값 | 이유 셋 · `measured_other` · `pool/capable` · `pool/host/global`. `Job`/`Preset`/`WorkerInfo` 새 필드 기본값이 오늘 동작 | `test_new_reason_and_source_constants` · `test_job_preset_worker_defaults_keep_todays_behaviour` · `test_queue_row_placement_detail_defaults_to_empty` |
| A-B11 | 행동 가능한 이유 순서 | `worker_down → no_capable_machine → check_failed → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused`, `check_pending` 없음 | `test_actionable_reasons_order` |
| A-B12 | 그 호스트의 실측 | `(100.0, "measured", 5)` | `test_expected_on_uses_the_hosts_own_median` |
| A-B13 | 실측이 없으면 preset → default | `(480, "preset", 0)` · `(600, "default", 0)` | `test_expected_on_without_any_sample_uses_preset_then_default` |
| A-B14 | 전역 계산 · placement=None | 잡은 자기 풀의 레인만 본다(남의 풀의 빈 레인을 안 가로챈다). 순번은 풀마다 1부터 | `test_global_without_placement_keeps_jobs_on_their_own_pools_lanes` · `test_global_positions_are_numbered_per_pool` |
| A-B15 | 가장 일찍 끝나는 레인(완료 기준 1) | 빠른 호스트가 300초 뒤 비면(400 < 1000) 거기서 기다린다: wait 300 · finish +400 · expected 100 · ahead #1. 1500초 뒤면 느린 빈 호스트: wait 0 · finish +1000. 두 번째 잡도 빠른 쪽(500 < 1000) | `test_earliest_finish_waits_for_the_fast_host` · `test_earliest_finish_takes_the_free_slow_host_when_fast_is_long_busy` · `test_earliest_finish_chains_on_the_fast_host` |
| A-B16 | 도는 잡은 자기 호스트의 표 | expected 600 · measured · remaining 300 | `test_busy_job_estimate_uses_its_own_host` |
| A-B17 | 전역 순서 하나 | 로컬 레인 하나를 기본 풀 잡과 `any-capable` 잡이 (우선순위, id) 순으로: wait 0 · 900 · 1800. 순번은 표시 풀별 | `test_global_order_is_one_queue_across_display_pools` · `test_display_pool_decides_the_position_of_a_waiting_capable_job` |
| A-B18 | `scope = "host"` 그룹(엣지 3) | 같은 호스트의 다른 풀 잡을 막는다: `blocked_by_group` · blocked_by #1 · wait 300 · finish +840. 대기 잡끼리도 사슬(wait 540) | `test_host_scope_blocks_across_pools_on_the_same_host` · `test_host_scope_chains_waiting_jobs_across_pools` |
| A-B19 | `scope = "global"` | 다른 호스트 · 다른 풀도 막는다 | `test_global_scope_blocks_another_host_and_pool` |
| A-B20 | 맞는 머신 없음(S4) | `no_capable_machine` · wait/finish/ahead None · 순번은 있다 · `placement_detail` 이 넘긴 노트 그대로 | `test_no_capable_machine_row_has_no_eta_and_carries_the_notes` |
| A-B21 | 점검 이유 둘 | `check_failed` · `check_pending` 이 그대로 행에 · ETA 없음 | `test_check_failed_reason_passes_through` · `test_check_pending_reason_passes_through` |
| A-B22 | 같은 호스트의 단계 중앙값 | 오늘처럼 `stuck`(`over_step`, step_expected 100) | `test_step_median_of_the_same_host_still_judges_stuck` |
| A-B23 | `[[probes]]` 읽기 | 선언 순서, 기본 timeout 10. 없으면 `()` | `test_probes_parse_in_declared_order_with_default_timeout` · `test_no_probes_is_an_empty_tuple` |
| A-B24 | 프리셋 `placement` | 기본 `pool`, `"any-capable"` → `PLACEMENT_CAPABLE`, `pool` 은 표시 위치로 남는다 | `test_placement_defaults_to_pool` · `test_placement_any_capable` · `test_placement_explicit_pool` |
| A-B25 | 프리셋 `concurrency_scope` | 기본 `pool`, `host` · `global` 허용 | `test_concurrency_scope_defaults_to_pool` · `test_concurrency_scope_values` |
| A-B26 | `checks` · `check_ref` | `("sim-check",)`, 점검 프리셋 자신은 `()`. `check_ref` 기본 `"HEAD"`, 바꿀 수 있다. tree+git_ref 둘 다 받는 점검도 된다 | `test_checks_name_a_git_ref_preset` · `test_checks_default_is_empty` · `test_check_ref_defaults_to_head` · `test_check_ref_custom_value` · `test_check_preset_accepting_both_modes_is_fine` |
| A-B27 | `sample` | 보통 프리셋 True, 점검 프리셋 False, 명시하면 그 값 | `test_sample_defaults_true_for_ordinary_presets` · `test_sample_defaults_false_for_check_presets` · `test_sample_explicit_true_on_a_check_preset_wins` · `test_sample_explicit_false_on_an_ordinary_preset` |
| A-B28 | `[server]` 새 키 | 600 · true · true · 30, 파일과 env 로 바뀐다 | `test_server_defaults` · `test_server_keys_from_file` · `test_probe_interval_env_override` |
| A-B29 | 오늘의 풀별 호출 = 골든 | 200 픽스처 전부 같은 행(지금도 초록, 뒤에도 초록) | `test_golden_equals_per_pool_calls` · `test_golden_fixture_matches_its_generator` · `test_golden_covers_the_rules_it_locks` |

## 엣지 케이스

| id | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| A-E1 | 빈 줄 · 공백만 있는 줄 | 건너뛴다, 앞뒤 공백은 지운다 | `test_normalize_skips_blank_and_whitespace_only_lines` |
| A-E2 | ANSI · 벨 · NUL · DEL | 지운다 → `"Xcode 27.2"` | `test_normalize_strips_control_characters` |
| A-E3 | 120자 | 이은 **뒤에** 120자에서 자른다(말줄임 없음) | `test_normalize_cuts_at_120_characters` · `test_normalize_cut_applies_after_joining` |
| A-E4 | 깨진 UTF-8 | `�` 로 바꿔 읽는다 | `test_normalize_decodes_invalid_utf8_with_replacement` |
| A-E5 | 출력 없는 성공 | `ok` · `""` (None 이 아니다) | `test_normalize_ok_with_no_output_is_an_empty_value` |
| A-E6 | 0 아닌 종료 · 시그널 | `error` · `"exit 1"` / `"exit -9"`, 출력은 버린다 | `test_normalize_nonzero_exit_is_error_with_exit_code_value` · `test_normalize_signal_exit_keeps_the_negative_code` |
| A-E7 | 실행 파일 없음 · 시간 초과 | `missing`/`timeout`, 값 None | `test_normalize_missing_executable` · `test_normalize_timeout_has_no_value` |
| A-E8 | 해시에 timeout · stale 이 섞임(엣지 2 · 뮤테이션 ③) | 해시가 안 바뀐다 | `test_hash_excludes_timeout_probes` · `test_hash_ignores_the_stale_flag` |
| A-E9 | 상태만 다르고 값이 없는 프로브 | `missing` ≠ `error` ≠ `ok` | `test_hash_changes_with_state_even_without_value` |
| A-E10 | 프로브 없음 | 빈 지문도 아는 지문(16 hex) | `test_hash_of_no_probes_is_still_a_known_fingerprint` |
| A-E11 | 이름·값 경계 모호 | `ab=c` 와 `a=bc` 가 다른 해시 | `test_hash_does_not_depend_on_probe_name_prefixes` |
| A-E12 | timeout(엣지 2) | 앞 값을 `stale=True` 로 잇고 해시 그대로. 앞 값이 `missing` 이어도, 두 번 연속이어도 | `test_settle_timeout_keeps_previous_value_and_marks_stale` · `test_settle_timeout_carries_a_previous_missing_state` · `test_settle_timeout_twice_in_a_row_keeps_carrying` |
| A-E13 | 첫 회차 timeout · 새로 더한 프로브의 timeout(엣지 2) | `hash is None` | `test_settle_first_round_timeout_makes_the_fingerprint_unknown` · `test_settle_timeout_of_a_newly_added_probe_is_unknown` |
| A-E14 | 설정에서 뺀 프로브 | 앞 값을 되살리지 않는다 | `test_settle_drops_probes_that_are_no_longer_measured` |
| A-E15 | 바뀜 비교의 모서리 | 첫 보고는 `{}` · 더함/뺌은 None 쪽 · `missing` 으로 바뀜 · `exit 1 → exit 2` · stale 이음은 바뀜이 아니다 | `test_changed_probes_is_empty_when_nothing_changed` · `test_changed_probes_without_previous_report_is_empty` · `test_changed_probes_added_and_removed` · `test_changed_probes_state_change_to_missing` · `test_changed_probes_error_code_change` · `test_changed_probes_ignores_a_stale_carry` |
| A-E16 | 점검 기록의 상태 | 없음 → needed · 앞 지문의 통과 → needed(엣지 1) · 활성 넷 → running · failed/timed_out → failed · cancelled/lost → needed(그 기록과 함께) | `test_check_state_without_records_is_needed` · `test_check_state_success_under_old_fingerprint_does_not_count` · `test_check_state_active_job_is_running` · `test_check_state_failure_is_failed` · `test_check_state_cancelled_or_lost_is_needed_with_that_record` |
| A-E17 | 기록 여럿 | 같은 지문 안에서 **가장 큰 job_id**(목록 순서 무관). 다른 지문의 더 큰 id 는 무시 | `test_check_state_uses_the_largest_job_id_not_list_order` · `test_check_state_largest_id_is_taken_within_the_current_fingerprint_only` |
| A-E18 | 지문 모름 | None 끼리 맞추지 않는다 → needed | `test_check_state_with_unknown_fingerprint_is_needed` |
| A-E19 | 판정 표 1~3(엣지 5) | 보고 없음 · 그 프리셋 사실 없음 → `report_missing`. 세대 다름 · `max_age` 초과 → `report_stale`. 정확히 `max_age` 는 신선 | `test_verdict_row1_no_report_is_unknown_report_missing` · `test_verdict_row1_preset_absent_from_report_is_report_missing` · `test_verdict_row2_generation_mismatch_is_report_stale` · `test_verdict_row3_old_report_is_report_stale` · `test_verdict_row3_boundary_exactly_max_age_is_fresh` |
| A-E20 | 판정 표 4~5(엣지 6) | 없는 도구 → `ineligible tool_missing {tools}` · 작업 공간에만 있을 도구 → `unknown workspace_tool {tools}` | `test_verdict_row4_missing_tool_is_ineligible_with_tool_names` · `test_verdict_row5_workspace_only_tool_is_unknown_not_ineligible` |
| A-E21 | 판정 표 6~9(엣지 1 · 2) | 점검 실패 → `ineligible check_failed {check, job_id}` · 도는 중 → `check_running` · 현재 지문 통과 없음 → `check_needed` · 지문 모름 + checks → `fingerprint_unknown` | `test_verdict_row6_failed_check_is_ineligible_with_check_and_job` · `test_verdict_row7_running_check_is_unknown_check_running` · `test_verdict_row8_no_pass_under_current_fingerprint_is_check_needed` · `test_verdict_row9_unknown_fingerprint_with_checks` |
| A-E22 | 판정이 막지 않는 것 | checks 없는 프리셋의 지문 모름 → ok · `missing_passthrough`(결정 92) → ok · stale 이음 지문의 통과 → ok · 다른 점검 프리셋의 기록은 안 센다 | `test_verdict_unknown_fingerprint_without_checks_is_ok` · `test_verdict_missing_passthrough_does_not_block` · `test_verdict_stale_fingerprint_probe_still_counts_as_the_same_fingerprint` · `test_verdict_ignores_records_of_other_check_presets` |
| A-E23 | 첫 번째가 이긴다(뮤테이션 ①) | 세대 > 없는 도구 · 없는 도구 > 작업 공간 도구 > 점검 실패 · 실패 > 도는 중(점검 순서 무관) > 필요 · needed 는 선언 순서 첫 점검 · 낡은 보고 > 점검 실패 | `test_precedence_generation_beats_missing_tool` · `test_precedence_missing_beats_workspace_tool` · `test_precedence_missing_tool_beats_failed_check` · `test_precedence_workspace_tool_beats_failed_check` · `test_precedence_failed_check_beats_running_check_across_checks` · `test_precedence_running_check_beats_needed_check` · `test_precedence_needed_names_the_first_check_in_declared_order` · `test_precedence_stale_report_beats_failed_check` |
| A-E24 | 하드웨어 id 가 없다 · 줄바꿈 | `worker:<w>`(None · `""` · 공백) · 앞뒤 공백은 같은 호스트 | `test_host_key_falls_back_to_the_worker` · `test_host_key_ignores_surrounding_whitespace` |
| A-E25 | 도구를 못 찾았다 | PATH 가 전부 절대 → missing · 상대 항목(`tools` `.` 빈 항목 `./node_modules/.bin`) → workspace_only · 측정 프로세스 cwd 로 풀지 않는다 · 절대경로 선언은 그 자리만 · PATH 없음 → missing(프로세스 PATH 로 안 물러남) · 실행 권한 없음 → missing · 선언 순서 | `test_measure_tools_absent_with_only_absolute_entries_is_missing` · `test_measure_tools_absent_with_a_relative_entry_is_workspace_only` · `test_measure_tools_does_not_resolve_relative_entries_against_its_own_cwd` · `test_measure_tools_absolute_declaration_is_checked_in_place` · `test_measure_tools_without_path_does_not_fall_back_to_the_process_path` · `test_measure_tools_non_executable_file_is_missing` · `test_measure_tools_keeps_declared_order_in_both_lists` |
| A-E26 | 동치성(엣지 4 · §18) | 모든 잡 · 모든 레인을 한 번에 넘겨도 풀별 계산과 같은 행 — placement=None 과 같은 풀 규칙 placement + 호스트별 표 둘 다 | `test_global_call_without_placement_matches_golden` · `test_global_call_with_same_pool_placement_matches_golden` |
| A-E27 | 다른 호스트의 실측만(엣지 12) | `measured_other` · 그 호스트의 표본 수 · 신뢰도 low. 자기 호스트가 min 미만이어도 물러난다. 다른 호스트도 min 미만이면 preset. 셋이면 그 중앙값 | `test_expected_on_falls_back_to_another_host_as_measured_other` · `test_expected_on_own_host_below_min_samples_falls_back_to_other_host` · `test_expected_on_other_hosts_below_min_samples_do_not_count` · `test_expected_on_takes_the_median_across_other_hosts` · `test_confidence_of_measured_other_is_low_even_with_many_samples` · `test_busy_job_on_a_host_without_samples_is_measured_other` · `test_waiting_job_landing_on_a_host_without_samples_is_measured_other` |
| A-E28 | `host_medians=None` | `expected_for(flat)` 와 같다 | `test_expected_on_without_host_medians_is_expected_for` |
| A-E29 | 단계 중앙값은 안 물러난다(엣지 12 · 뮤테이션 ⑤) | 다른 호스트의 표 · 옛 `step_medians` 만 있으면 stuck 아님 · `quiet` | `test_step_median_of_another_host_does_not_make_stuck` · `test_legacy_step_medians_are_ignored_when_host_tables_are_given` |
| A-E30 | `sample = false`(§7.4) | `medians_from` 이 뺀다. 점검 잡뿐이면 중앙값 없음 | `test_medians_from_ignores_jobs_marked_not_a_sample` · `test_medians_from_only_check_jobs_yields_no_median` |
| A-E31 | 후보가 아닌 빈 레인(엣지 5 큐 쪽) | 그 레인으로 ETA 를 짓지 않는다 — wait 300 · ahead #1 | `test_placement_excludes_an_idle_worker_that_is_not_a_candidate` |
| A-E32 | 후보 없는 잡의 뒤 | 앞 잡이 레인을 「차지」 하지 않는다: wait 0 · `not_scheduled` | `test_job_without_candidates_does_not_take_a_lane_from_the_job_behind` |
| A-E33 | 이유의 우선 | paused > no_capable_machine(노트는 그대로) · uploading > no_capable_machine · 후보가 전부 down → worker_down | `test_paused_beats_no_capable_machine` · `test_uploading_beats_no_capable_machine` · `test_candidates_all_down_is_worker_down` |
| A-E34 | `placement_detail` | 최대 8개 · 도는 행은 `()` | `test_placement_detail_is_capped_at_eight` · `test_busy_rows_have_no_placement_detail` |
| A-E35 | `scope = "pool"` 은 오늘 그대로(엣지 3) | 같은 호스트라도 다른 풀은 안 막는다 — 도는 잡도 대기 잡끼리도 | `test_pool_scope_does_not_block_across_pools_even_on_the_same_host` · `test_pool_scope_does_not_chain_waiting_jobs_across_pools` |
| A-E36 | `scope = "host"` 다른 호스트 | 안 막는다 | `test_host_scope_does_not_block_another_host` |
| A-E37 | 범위가 섞인 같은 그룹 | **대기 잡의 범위**로 판정(claim 과 같은 답) | `test_scope_of_the_waiting_job_decides_like_claim_does` |
| A-E38 | `shared` 배지 | 다른 풀 · 다른 호스트에서 도는 잡끼리는 shared 가 아니다 | `test_global_shared_flag_does_not_cross_pools_on_different_hosts` |
| A-E39 | `[[probes]]` 검증 | 이름 중복 · 빈/없는/문자열 아닌 argv · 64 허용 65 실패 · timeout 1~60 정수(0 · 61 · 음수 · bool · 문자열 · 소수 실패) · 이름 규칙(32자 허용) · 모르는 키 · 배열 아님 | `test_probe_duplicate_name_fails_naming_it` · `test_probe_empty_argv_fails_naming_probe_and_key` · `test_probe_argv_must_be_strings` · `test_probe_without_argv_fails` · `test_sixty_four_probes_are_allowed` · `test_sixty_five_probes_fail` · `test_probe_timeout_bounds_inclusive` · `test_probe_timeout_out_of_bounds_fails` · `test_probe_name_rule` · `test_probe_name_of_32_characters_is_allowed` · `test_probe_unknown_key_fails` · `test_probes_must_be_an_array_of_tables` |
| A-E40 | `placement`/`concurrency_scope` 잘못된 값 | 프리셋 이름 + 키 이름. 설정에 DB 값 `"capable"` 은 거절 | `test_placement_bad_value_fails_naming_preset_and_key` · `test_concurrency_scope_bad_value_fails` |
| A-E41 | `checks` 검증(엣지 10) | 모르는 프리셋 · git_ref 안 받는 점검 · 점검의 점검(A→B→C) · 자기 점검 · 중복 · 목록 아님 → 기동 실패, 프리셋 · 키 · 문제 이름을 댄다 | `test_checks_unknown_preset_fails_naming_both` · `test_check_preset_must_accept_git_ref` · `test_check_preset_with_its_own_checks_fails` · `test_preset_cannot_check_itself` · `test_checks_duplicate_entry_fails` · `test_checks_must_be_a_list_of_strings` |
| A-E42 | `check_ref` · `sample` 타입 | 빈 문자열 · 숫자 · 목록 실패 / bool 아님 실패 | `test_check_ref_must_be_a_nonempty_string` · `test_sample_must_be_a_bool` |
| A-E43 | 점검 프리셋이 아닌 git_ref 프리셋 | `sample` 은 True 그대로 | `test_a_git_ref_preset_nobody_checks_is_still_a_sample` |
| A-E44 | `[server]` 하한 | `probe_interval_seconds` 59 실패(60 허용) · `check_sweep_seconds` 0 · -1 실패 · `auto_checks` 문자열 실패 | `test_probe_interval_below_60_fails` · `test_check_sweep_seconds_must_be_positive` · `test_auto_checks_must_be_a_bool` |

## 가정 — 명세가 말하지 않아 정한 것 (리드가 맞춰 볼 것)

**`core/capability.py`**

1. 정규화의 「비어 있지 않은 줄」은 앞뒤 공백을 지운 뒤 판단한다(공백 · 탭만 있는 줄은 빈 줄). 각 줄도 앞뒤 공백을 지운 채로 잇는다.
2. 「제어문자」는 유니코드 Cc 범주(NUL · BEL · ESC · DEL 포함)이고 **지운다**(공백으로 바꾸지 않는다). ANSI 시퀀스의 나머지(`[0m`)는 남는다 — 테스트는 ESC 하나만 본다.
3. 120자 자르기는 ` · ` 로 이은 **뒤**에 하고, 말줄임표를 붙이지 않는다.
4. bytes 는 UTF-8, `errors="replace"` 로 읽는다.
5. `ok` 인데 출력이 없으면 값은 `""`(None 아님). `output=None` 도 같다.
6. `error` 의 값은 출력과 무관하게 `"exit N"`(음수 시그널 코드 그대로). `missing`·`timed_out` 플래그가 `returncode` 보다 앞선다.
7. `fingerprint_hash` 는 (이름, 상태, 값)만 넣고 `stale` 은 안 넣는다. 빈 매핑도 정상 16-hex 해시다(프로브를 안 쓰는 설정에서도 `checks` 가 돈다). 이름·값 경계가 모호하지 않은 직렬화(JSON)여야 한다.
8. `settle_fingerprint`: 앞 보고에 그 프로브가 없으면(새로 더한 프로브의 timeout) 지문은 None. 첫 회차 timeout 은 `probes[name]` 에 `ProbeResult("timeout", None)` 을 그대로 둔다. 앞 값이 이미 stale 이어도 다시 잇는다. 이번 측정에 없는 프로브(설정에서 뺌)는 앞 값에서 되살리지 않는다.
9. `changed_probes(None, new)` 는 `{}` — 첫 보고는 「바뀜」이 아니다(재시작 너머 비교는 B 의 `machines` 표 몫, 엣지 9). 비교는 (상태, 값)이고 `stale` 만 다른 것은 안 바뀐 것이다. 튜플에는 값만(`missing` 은 None) 싣는다.
10. `check_state`: 활성 = `uploading · queued · running · cancelling`. `cancelled`·`lost` 는 `(needed, 그 기록)` — 판단 근거 기록을 돌려준다. 지문이 None 이면 기록과 맞추지 않고 `(needed, None)`.
11. `verdict` 의 `max_age_seconds` 는 호출자가 이미 3배 한 값이다. 경계는 엄격한 `>`(정확히 `max_age` 는 신선).
12. 세대는 맞는데 `report.presets` 에 그 프리셋이 없으면 `unknown report_missing`.
13. detail 키: 4·5번은 `{"tools": [...]}`(보고 순서 그대로 — 테스트는 `list()` 로 비교), 6·7번은 `{"check": 이름, "job_id": id}`, 8번은 `{"check": 이름}`. **6번의 「바뀐 지문 항목」은 `verdict` 입력에 앞 지문이 없어 잠그지 않았다** — 서버가 덧붙이거나 `verdict` 에 인자를 더할지 리드가 정한다.
14. 6~8번은 `checks` 전체를 훑은 뒤 행 단위로 정한다: 어느 점검이든 실패면 6번, 아니면 어느 것이든 도는 중이면 7번, 아니면 8번. 같은 행 안에서는 `checks` 선언 순서의 첫 점검을 detail 에 싣는다.
15. **지문이 None 이고 `checks` 가 있으면 6~8번을 건너뛰고 9번 `fingerprint_unknown`** — 그렇지 않으면 8번이 늘 먼저 걸려 9번에 닿을 수 없다.
16. `host_key_from` 은 앞뒤 공백을 지운 원문의 UTF-8 sha256 앞 16자. None · `""` · 공백뿐이면 `"worker:<w>"`.

**`runner.measure_tools`**

17. 상대 항목은 `tools` · `.` · 빈 항목(`::`) · `./x` 다. 절대경로 선언은 PATH 와 무관하게 그 자리만 보고, 없으면 상대 항목이 있어도 `missing`. PATH 키가 없으면 전부 `missing`. 반환은 두 `list`, 선언 순서.

**`core/queue.py`**

18. `expected_on`: 자기 호스트 n ≥ `min_samples` → `measured`. 아니면 n ≥ `min_samples` 인 다른 호스트들의 **중앙값들의 중앙값**(셋이면 가운데), 표본 수는 그 호스트들의 합(하나면 그 호스트의 수) → `measured_other`. 그도 없으면 `preset`/`default` 이고 표본 수는 **자기 호스트의 수**(없으면 0 — `expected_for` 와 같은 뜻). `host_medians=None` 이면 `expected_for(key, preset, medians, cfg)` 그대로.
19. 대기 잡이 레인을 못 고를 때(paused · 후보 레인 전부 down/held) expected 는 **후보 워커들의 호스트가 하나면 그 호스트**로 낸다 — 골든의 「같은 풀 규칙」 변형이 이것에 기댄다.
20. 가장 일찍 끝나는 레인의 동점은 `(끝나는 시각, 레인이 비는 시각, 워커 이름 or "", 레인 번호)` 순으로 깬다. 호스트가 하나뿐이면 오늘의 `min(lane_free, worker, lane)` 과 같은 레인이 나와야 하고(그룹이 늦춘 경우 포함) 골든이 그것을 잠근다.
21. `position` 은 **표시 풀**(`display_pool or pool`)마다 1부터, 순서는 전역 `(−priority, id)`. 반환은 평평한 목록이고 테스트는 순서를 보지 않는다.
22. 대기 행의 이유 우선: `uploading/upload_stalled → paused → Placement.reason(후보 집합이 비고 reason 이 있을 때) → worker_down(살아 있는 후보 레인 없음) → blocked_by_group → waiting_for_lane/not_scheduled/held_by_load`. 후보 집합이 비었는데 reason 이 None 이면 `worker_down`.
23. 후보가 없는 잡은 레인도 「빈 레인 차지」(not_scheduled 셈)도 쓰지 않는다. 노트는 이유와 무관하게(paused 여도) `placement_detail` 에 싣고, 8개에서 자르는 것은 `compute_queue` 다.
24. 그룹 범위: **대기 잡의 범위**로 판정한다 — `global` 은 그 그룹의 모든 도는 잡, `host` 는 고른 레인의 호스트에서 도는 잡, `pool` 은 고른 레인의 풀에서 도는 잡. 대기 잡끼리의 사슬도 같은 키로 잇는다. `blocked_by` 는 오늘처럼 **도는** 잡에서만 나온다. (§7.2 의 「범위별 키」를 도는 잡의 범위로 세면 섞인 범위에서 ETA 가 claim 과 어긋난다 — A-E37.)
25. `shared` 를 풀 단위로 둘지 호스트 단위로 둘지는 잠그지 않았다(다른 풀 · 다른 호스트면 아니다만 잠갔다). 운영의 로컬 + `mac2-worker` 는 같은 호스트 다른 풀이라 리드가 정해야 한다.
26. `host_step_medians` 가 주어지면 옛 `step_medians` 는 아예 안 본다(같은 key 가 있어도).
27. `sample=False` 는 `medians_from` 이 직접 거른다(SQL 에서만 거르지 않는다).
28. `Placement.workers` 의 None 은 로컬 레인(`WorkerInfo.worker is None`)이다.
29. 골든 「같은 풀 규칙」 변형은 `medians=`(풀별 표를 합친 평평한 표)와 `host_medians=` 를 **같이** 넘긴다. 호스트 표가 있으면 그것이 이겨야 한다. 도는 잡은 `host_key` 가 있고 대기 잡은 None 이다. 풀마다 워커가 하나 이상 있다 — 워커가 하나도 없는 풀은 골든에 없다.
30. `eta_for_new` 의 placement 판은 §11.4 에 이름이 없어 여기서 안 다뤘다.

**설정**

31. `Probe` 의 자리(model/config)는 안 정했다 — 테스트는 `name · argv · timeout_seconds` 속성만 읽고 argv 는 `tuple()` 로 비교한다.
32. 메시지: 묶음 오류(중복 · 64개 초과 · 이름 규칙 · 배열 아님)는 `[[probes]]` 를, 한 프로브의 오류(argv · timeout · 모르는 키)는 `probe '<name>'` 을 담는다. 중복에는 `duplicate`, 초과에는 `64` 가 들어간다.
33. 프로브 이름 규칙: `^[a-z0-9][a-z0-9_-]{0,31}$`(소문자·숫자로 시작 — `-x` 거절, 32자까지).
34. 프로브 `timeout_seconds` 는 정수 1~60(bool · 소수 · 문자열 거절).
35. `Preset.placement` 는 DB 값으로 담는다 — 설정 `"any-capable"` → `PLACEMENT_CAPABLE("capable")`. 설정에 `"capable"` 을 쓰면 거절.
36. `concurrency_scope` 를 `concurrency_group` 없이 쓰는 것을 막을지(`artifacts_on` 선례)는 **잠그지 않았다** — 열린 질문.
37. `checks` 메시지: 모르는 이름은 `preset '<가진 쪽>'` + `checks` + 그 이름. git_ref 요구는 `preset '<가진 쪽>'` + 점검 이름 + `git_ref`. 점검의 점검 · 자기 점검은 `preset '<점검 프리셋>'` + `checks`. 중복은 `duplicate`.
38. `check_ref` 는 비지 않은 문자열이면 어느 프리셋에든 허용한다(점검이 아닌 프리셋에 써도 거절하지 않는다).
39. `sample` 은 **읽을 때 bool 로 풀린다** — `Preset` 기본값은 None 이지만 `load_server_config` 뒤에는 점검 프리셋(다른 프리셋의 `checks` 에 오른 것) False, 나머지 True, 명시하면 그 값.
40. `[server] check_sweep_seconds` 하한은 1, `probe_interval_seconds` 메시지는 `[server] probe_interval_seconds` 와 `60` 을 담는다. env 덮어쓰기(`RCM_SERVER_PROBE_INTERVAL_SECONDS`)는 기존 섹션 규칙 그대로 된다.
41. 테스트 헬퍼 `fails()` 는 `unknown key`/`unknown section` 메시지를 「검증한 것」으로 치지 않는다 — 그래야 지금의 「모르는 키」 거절로 우연히 초록이 되지 않는다.
