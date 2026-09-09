# M5e 테스트 시나리오 C — 클라이언트 적용 · CLI · 워커 순서 · 호환 (2026-09-08)

`docs/m5e-workplan.md` §11(기준선 · 분류표 · 절차 · 저널) · §12(CLI 표 · 결과 줄 · 종료 코드) ·
§5(워커 두 경로 · 예산) · §10(추가만 · 시간 필드) 과 §15 의 1·2·4·6번을
`tests/test_apply.py`(28 함수 · 파라미터 포함 34건) · `tests/test_cli_m5e.py`(26 함수) ·
`tests/test_worker_m5e.py`(14 함수 · 15건) · `tests/test_compat_m5e.py`(12 함수)로 옮긴 것이다
(test-first, 역할 C). **`src/` 는 한 글자도 건드리지 않았다.**

구현이 하나도 없으므로 87건 전부 빨갛다 — 그게 맞는 상태다. 빨간 이유는 세 가지뿐이고 전부
「그 명세가 아직 없다」다: `ModuleNotFoundError`(`apply.py` · `collect.py`) ·
`ConfigError: unknown key 'artifacts'`(프리셋 픽스처) · 없는 CLI 인자/필드/라우트.

공통 규칙: 주석·docstring 은 한국어, 식별자·시험 이름은 영어 · 표준 라이브러리 + pytest 만 ·
벽시계 `sleep` 없음(시간이 필요한 곳은 `WorkerServer` 의 주입 시계 `clock.advance()`) ·
모든 경로는 `tmp_path` 아래 · 공개망 접속 없음(in-process 루프백 서버만).

## 잠근 API (§18 을 그대로 따랐다)

- `remote_ci_monitor.apply`: `Entry(path, verdict, incoming_sha, baseline_sha, local_sha, reason)` ·
  `ApplyPlan(entries).counts()/.safe()` · `ApplyResult(wrote, skipped, conflicted, failed, complete)` ·
  `plan(files, baseline, root)` · `apply(plan, staging, root, *, force=False, journal=None)` ·
  `read_journal(path)` · `write_journal(path, doc)`.
- `remote_ci_monitor.core.artifacts.BundleFile(path, size, sha256, mode)`.
- `remote_ci_monitor.collect`: `collect(...)` · `CollectResult(state, files, total_bytes,
  bundle_bytes, skipped_count, bundle_sha256, bundle_path, reason_code, reason_args, detail)`.
- `Store.get_bundle(job_id)` · `Store.finish(..., bundle=CollectResult|None)`.
- `WorkerClient.upload_bundle(job_id, tar_path)` · `WorkerClient.bundle_status(job_id)`.
- 설정: `[[presets]].artifacts` · `[server] artifact_retention_hours ·
  artifact_timeout_seconds · artifact_cancel_timeout_seconds`.
- CLI: `rcm run … --fetch-artifacts [--force] [--dry-run]` ·
  `rcm artifacts JOB_ID [--fetch --output DIR] [--resume]` · 전달 실패 종료 코드 **5** ·
  JSON 의 `artifact_fetch`.
- 라우트: `GET /jobs/{id}/artifacts` · `POST /jobs/{id}/artifacts/ack`(간접) ·
  `PUT /worker/jobs/{id}/artifacts` · `POST /worker/jobs/{id}/finish` 의 `artifacts`·`finished_at`.

## `tests/test_apply.py` — 분류 · 안전 · 적용 · 저널

스테이징은 받은 파일을 **같은 상대 경로**로 담은 디렉터리(`staging/<path>`)로 읽었다. 「아무것도
안 썼다」는 뿌리 전체를 `os.walk(followlinks=False)` 로 찍어 통째로 비교한다(임시 파일이 남아도
걸린다). 모듈은 픽스처에서 늦게 import 한다 — 수집 오류로 파일이 통째로 죽지 않는다.

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 1행 — 기준선 없음 · 로컬 없음 → `new` | `test_a_path_that_was_not_in_the_submission_and_is_not_on_disk_is_new` | §11 표 |
| 2 | 2행 — 기준선 없음 · 로컬 있음 → `conflicted`. **내용이 같아도** 그렇다 (×2) | `test_an_existing_local_file_without_a_baseline_is_conflicted` | §11 표 (초고가 틀린 곳 ①) |
| 3 | 3행 — 로컬 = 받은 것 → `unchanged` | `test_a_local_file_equal_to_the_incoming_bytes_is_unchanged` | §11 표 |
| 4 | 4행 — 로컬 = 기준선 ≠ 받은 것 → `changed` | `test_an_untouched_local_file_that_differs_is_changed` | §11 표 |
| 5 | 5행 — 로컬 ≠ 기준선(손댔다) → `conflicted` | `test_a_locally_edited_file_is_conflicted` | §11 표 |
| 6 | 6행 — **로컬이 지워졌다** → `conflicted`, 계획은 파일을 만들지 않는다 | `test_a_file_the_user_deleted_while_waiting_is_conflicted_not_new` | §11 표 (초고가 틀린 곳 ②) |
| 7 | 3행이 5행보다 앞선다 — 손댔어도 결과 바이트가 같으면 `unchanged` | `test_local_bytes_equal_to_the_incoming_win_over_a_touched_baseline` | §11 표 |
| 8 | 여섯 줄을 한 계획에 → `counts()` new 1 · unchanged 1 · changed 1 · conflicted 3 | `test_counts_cover_every_row_of_the_table` | §18 `counts()` |
| 9 | `entries` 는 경로순(「41개까지 적용됨」이 말이 되려면 결정적이어야 한다) | `test_plan_entries_are_sorted_by_path` | §11 절차 5 |
| 10 | `Entry` 가 판정 근거 세 해시를 들고 있다 | `test_entries_carry_the_three_hashes_the_verdict_was_made_from` | §18 `Entry` |
| 11 | 부모 조각이 심링크 → unsafe, 아무것도 안 씀 | `test_a_symlink_in_the_parent_chain_makes_the_plan_unsafe` | §11 절차 2 · §15.1 |
| 12 | 목적지 자체가 심링크 → unsafe, 링크 대상도 그대로 | `test_a_destination_that_is_itself_a_symlink_makes_the_plan_unsafe` | §11 절차 2 |
| 13 | `../` · 안쪽 탈출 · 절대 경로 · `./..` → unsafe, 뿌리 밖에 파일 없음 (×4) | `test_a_path_that_escapes_the_root_makes_the_plan_unsafe` | §2 · §15.1 |
| 14 | casefold 충돌 · NFC/NFD 충돌 · **디렉터리 접두** 충돌 → unsafe (×3) | `test_paths_that_collide_after_normalisation_make_the_plan_unsafe` | §4 충돌 검사 |
| 15 | 안전하지 않으면 안전한 항목까지 **하나도** 안 쓴다(`--force` 여도) | `test_an_unsafe_plan_writes_nothing_at_all_not_even_the_safe_entries` | §15.1 |
| 16 | 기본은 `new`·`changed` 만. `unchanged` 는 그대로, 지운 파일은 안 되살린다 | `test_apply_writes_new_and_changed_only` | §11 표 기본 동작 |
| 17 | `--force` 는 `conflicted` 까지 → wrote 5 · conflicted 0 · `complete` True | `test_force_also_writes_the_conflicted_files` | §12 · §7 |
| 18 | 계획과 쓰기 **사이**에 바뀐 파일은 건너뛰고 conflicted 로 센다 | `test_a_file_changed_between_planning_and_writing_is_skipped_and_counted_conflicted` | §11 절차 3 |
| 19 | `--force` 여도 「방금 바뀐 것」은 건너뛴다 | `test_force_still_skips_a_file_that_changed_after_planning` | §11 절차 3 |
| 20 | 같은 디렉터리 임시 파일 + `os.replace`(이름은 다르고 부모는 같다) | `test_writes_go_through_a_temp_file_and_os_replace_in_the_same_directory` | §11 절차 4 |
| 21 | 뿌리에 남는 것은 매니페스트 경로뿐(`.part` 잔해 없음) | `test_no_temporary_files_are_left_in_the_tree` | §11 절차 4 |
| 22 | 매니페스트에 없는 로컬 파일은 지우지 않는다 | `test_local_files_missing_from_the_manifest_are_left_alone` | §17 |
| 23 | 충돌이 없을 때만 `complete` — ack 의 문지기 | `test_complete_is_true_only_when_no_conflict_remains` | §7 |
| 24 | 쓰기 실패(ENOSPC)도 `complete` False · 원본은 그대로 | `test_complete_is_false_when_a_write_fails` | §15.2 · 정직성 |
| 25 | `write_journal`/`read_journal` 왕복 · 사람이 읽을 수 있는 JSON | `test_write_and_read_journal_round_trip` | §11 절차 5 |
| 26 | 없는 저널 · 깨진 저널 → None | `test_read_journal_is_none_for_a_missing_or_corrupt_file` | §11 절차 5 |
| 27 | 중간에 죽으면(3번째 `os.replace` 에서 `BaseException`) 저널이 **디스크와 같은** 2개를 적는다 | `test_a_crash_partway_leaves_the_journal_and_the_files_written_so_far` | §11 절차 5 · §15.2 |
| 28 | 이어서 적용하면 나머지가 들어가고 그제야 `complete` · 잔해 없음 | `test_a_resumed_apply_finishes_the_rest` | §11 절차 5 · §12 `--resume` |

## `tests/test_cli_m5e.py` — §12 명령 표 · 결과 줄 · 종료 코드

`main(argv)` 를 in-process 로 부르고 `RCM_SERVER`/`RCM_TOKEN` 으로만 서버를 가리킨다. 서버는
**진짜 로컬 워커가 도는** `test_server.Server(workers=True)` — 잡이 실제로 `out/*.txt` 를 만들고
그것이 세션 트리로 돌아오는 데까지가 한 시험이다(완료 기준 ①).

**결정적으로 충돌을 만드는 법**: 트리의 `out/c.txt` 를 `--exclude out/c.txt` 로 스냅샷에서 빼면
그 경로는 **기준선이 없고 로컬 파일은 있다** — §11 표 2행이다. 잡이 도는 동안 사람이 파일을
고치는 경주를 흉내 낼 필요가 없다(프로브로 확인: `snapshot: 3 files`).

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | `run` 이 `--fetch-artifacts`·`--force`·`--dry-run` 을 받는다 | `test_run_takes_fetch_artifacts_force_and_dry_run` | §12 표 |
| 2 | 셋 다 기본은 꺼짐 | `test_run_defaults_have_no_fetch_no_force_no_dry_run` | §12 표 |
| 3 | `artifacts JOB_ID --fetch --output DIR --resume` 인자 모양 | `test_artifacts_command_takes_a_job_id_fetch_output_and_resume` | §12 · §18 |
| 4 | `rcm artifacts --help` 은 영어 | `test_artifacts_command_help_is_english` | §12 「도움말은 영어」 |
| 5 | `--no-wait --fetch-artifacts` → **스냅샷도 제출도 안 하고** 2 | `test_no_wait_with_fetch_artifacts_is_refused_before_submission` | §12 |
| 6 | git_ref 잡 + `--fetch-artifacts` → `--output` 요구, 제출 전에 2 | `test_a_git_ref_job_needs_an_output_directory` | §12 |
| 7 | `rcm artifacts N --fetch` 에 `--output` 없으면 2 | `test_a_standalone_fetch_needs_an_output_directory` | §12 |
| 8 | 그냥 `--no-wait` 면 `rcm artifacts <id> … --fetch` 한 줄을 찍는다 | `test_plain_no_wait_prints_the_command_that_fetches_later` | §12 |
| 9 | 제출한 그 트리의 같은 경로에 받아 쓴다. 매니페스트 밖은 그대로 | `test_fetch_artifacts_writes_the_goldens_back_into_the_submitted_tree` | 완료 기준 ① |
| 10 | 결과 줄 `wrote 1, unchanged 1, conflicted 1` + `3 files` | `test_the_result_line_says_how_many_were_written_not_just_compared` | §12 「낱말로 구분」 |
| 11 | 기준선 없는 로컬 파일은 `--force` 없이는 안 덮는다 | `test_a_local_file_without_a_baseline_is_not_overwritten` | §11 표 2행 |
| 12 | `--force` → wrote 2 · conflicted 0 · 종료 0 | `test_force_overwrites_the_conflicted_file_and_the_run_ends_clean` | §12 |
| 13 | `--dry-run` → wrote 0 · 파일 그대로 · 묶음은 여전히 `ready`(ack 없음) | `test_dry_run_prints_the_table_and_writes_nothing` | §12 · §7 |
| 14 | 충돌 없이 끝나면 ack → `join_count == 0` 잡은 즉시 `purged` | `test_a_complete_apply_acks_and_the_unjoined_bundle_disappears` | §7 · 완료 기준 ③ |
| 15 | 충돌이 남으면 ack 를 **안 보낸다** — 묶음은 `ready` 로 남는다 | `test_an_incomplete_apply_never_acks` | §7 |
| 16 | `rcm artifacts N` 이 상태와 매니페스트 경로를 보여 준다 | `test_artifacts_shows_the_state_and_the_manifest` | §12 |
| 17 | `--fetch --output DIR` 이 그 디렉터리에 쓴다 | `test_a_standalone_fetch_writes_into_the_output_directory` | §12 |
| 18 | 그 디렉터리에 이미 있는 파일은 `--force` 여야 덮는다 | `test_a_standalone_fetch_does_not_overwrite_an_existing_file_without_force` | §11 「기준선 없음」 |
| 19 | `--resume` 은 파일 없이 0 을 내지 않는다 | `test_resume_never_reports_success_without_the_files` | §12 · 정직성 |
| 20 | 산출물을 안 받으면 `wait_exit_code` 0/1 그대로 · `artifact_fetch` 키 없음 | `test_wait_exit_codes_and_the_json_field_are_unchanged_without_fetching` | §12 |
| 21 | 전달 실패 → 종료 **5** · `wait_exit_code` 0 · `artifact_fetch.complete` False | `test_a_delivery_failure_uses_its_own_code_and_json_field` | §12 · §18 |
| 22 | 실행 성공 + 전달 실패는 0 이 아니다 | `test_a_successful_run_whose_delivery_failed_does_not_exit_zero` | §12 |
| 23 | 실행 실패가 전달 실패보다 우선한다 → 1 (처분은 계속 보고) | `test_an_execution_failure_outranks_a_delivery_failure` | §12 |
| 24 | 실패한 잡의 diff 도 트리로 돌아온다(종료는 1) | `test_failed_jobs_still_deliver_their_diff_images` | §5 · 완료 기준 ⑧ |
| 25 | 글롭 없는 프리셋(`disabled`)에 5 를 내지 않는다 | `test_a_preset_without_globs_is_not_a_delivery_failure` | §3 · 정직성 |
| 26 | stdout 은 여전히 JSON 한 줄(표는 stderr) | `test_the_json_line_is_still_one_line_of_json` | `cli.py` 머리 규칙 |

## `tests/test_worker_m5e.py` — §5 두 경로 · 예산

로컬은 `tests/test_worker.py` 의 `enqueue`·`run_one`·`sh` 로 진짜 `sh` 프로세스를 돌리고, 순서는
`Store.finish` 를 감싸 **불린 순간**의 사실(실린 묶음 · 워크스페이스 유무)을 찍어 확인한다.
원격은 `RemoteWorker` 에 가짜 `WorkerClient` 를 끼워 `run_claimed` 를 직접 부른다 — 호출 순서와
`finish` 순간의 `self.running` 을 그대로 적는다. 서버 규칙은 `WorkerServer`(주입 시계).

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 수집이 `finish` 에 `bundle=` 로 실려 오고, 그때 워크스페이스가 아직 있다 · 정리는 그 뒤 | `test_collection_is_committed_with_finish_while_the_workspace_still_exists` | §5 로컬 1~6 |
| 2 | 실패한 잡도 모은다(golden diff 가 이 기능의 이유다) | `test_a_failed_job_is_collected_too` | §5 「종료 상태별」 |
| 3 | 수집 예외는 잡을 실패시키지도, 레인을 `down` 으로 만들지도 않는다 → `failed`+`collect_failed` | `test_a_collection_error_does_not_fail_the_job_or_take_the_lane_down` | §5 「자기 자리에서」 |
| 4 | 수집 중 수용된 취소가 이미 계산된 종료 상태를 **이긴다** | `test_a_cancel_accepted_during_collection_wins_over_the_computed_outcome` | §5 로컬 4 |
| 5 | 수집 예산 초과는 `dropped`+`timed_out`. 잡은 그대로 `succeeded` | `test_a_collection_budget_overrun_drops_the_bundle_but_not_the_job` | §5 예산 |
| 6 | 원격: `upload_bundle` 이 `finish` 보다 먼저 | `test_the_remote_worker_uploads_before_finishing` | §5 원격 2~3 |
| 7 | `finish` 를 보고하는 **동안에도** `self.running` 에 있다(오늘은 반대다) | `test_the_remote_worker_leaves_running_only_after_the_finish_is_reported` | §5 원격 4 |
| 8 | `finish` 에 구조화된 처분(state · 해시 · 수 · skipped · reason) | `test_the_remote_finish_carries_a_structured_disposition` | §5 원격 3 |
| 9 | 자재화 실패 경로도 처분을 남긴다 → `skipped`+`not_run`, 업로드 없음 | `test_a_materialisation_failure_still_leaves_a_disposition` | §5 원격 · §3 |
| 10 | `artifacts` 필드가 **통째로 없는** finish → `unknown` | `test_a_finish_without_an_artifacts_field_reads_back_as_unknown` | §5 · §3 |
| 11 | `{"state": "empty"}` → `empty`(둘은 다른 사실이다) | `test_a_finish_that_says_empty_reads_back_as_empty` | §3 |
| 12 | `lost` 잡의 늦은 업로드 → **409**, 잡은 `lost` 그대로 · 묶음은 `ready` 아님 | `test_a_late_upload_for_a_lost_job_is_refused_and_does_not_revive_it` | §5 · §6 |
| 13 | 기본값이 실린다: cancel 5 · timeout 60 · TTL 24h · 프리셋 글롭 | `test_the_default_cancel_budget_fits_under_two_heartbeats` | §8 표 |
| 14 | `artifact_cancel_timeout_seconds ≥ 2 × worker_heartbeat_seconds` 는 거절 (×2) | `test_a_cancel_budget_that_reaches_two_heartbeats_is_refused` | §5 · §8 |

## `tests/test_compat_m5e.py` — §10 추가만 · 정직성

키 집합은 `tests/test_status_schema.py` 의 `ROW_KEYS`·`RECENT_KEYS` 를 **가져와** 합집합으로
비교한다 — 나중에 그 목록에 `artifacts` 가 더해져도 그대로 통과하고, 잠그는 것은 「`artifacts`
말고는 아무 키도 늘거나 이름이 바뀌지 않았다」다.

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 큐 행 = 오늘의 키 + `artifacts` 하나 | `test_a_queue_row_gains_only_the_artifacts_object` | §10 |
| 2 | 최근 행 = 오늘의 키 + `artifacts` 하나 | `test_a_recent_row_gains_only_the_artifacts_object` | §10 |
| 3 | `artifacts` 객체는 §10 의 10개 키 정확히 — 파일 목록·해시·detail 없음 | `test_the_artifacts_object_has_exactly_the_documented_keys` | §10 · §3 |
| 4 | `server` = 오늘의 키 + `artifact_storage`(5개 키) | `test_the_server_block_gains_only_artifact_storage` | §10 |
| 5 | `schema_version` 은 여전히 1 | `test_the_schema_version_is_still_one` | 완료 기준 ⑨ |
| 6 | 글롭 없는 프리셋 → `disabled`(수는 `null`). `empty` 가 아니다 | `test_a_preset_without_globs_is_disabled_not_empty` | §3 |
| 7 | `job_artifacts` 행이 없는 옛 잡 → `unknown`, 수는 `null`, 새로 안 모은다 | `test_a_job_that_predates_the_feature_reads_as_unknown_and_is_not_collected` | §9 |
| 8 | M3 의 `jobs.artifacts_purged_at` 표시는 묶음의 `purged_at` 을 건드리지 않는다 | `test_the_m3_artifacts_purged_at_is_a_different_thing` | §1 · §8 경계 |
| 9 | 수집·업로드 20초를 태워도 `finished_at` 은 **실행 종료 시각** | `test_a_remote_finish_reports_the_execution_end_not_the_upload_end` | §10 |
| 10 | 그래서 `job_seconds` 는 30 이지 50 이 아니다(`core/status.py:188`) | `test_job_seconds_is_not_inflated_by_collection_and_upload` | §10 · §15.6 |
| 11 | `unknown` 이면 `recent —` 절에 `—` 가 있고 「0 files/0 MB」 꼴이 없다 | `test_unknown_artifact_numbers_render_as_a_dash` | §10 · §15.6 |
| 12 | `empty` 는 0 으로 그리고 `unknown` 과 블록이 서로 다르다 | `test_a_collection_that_found_nothing_renders_zero_not_a_dash` | §3 · §10 |

## 명세에서 애매했던 것 (안전한 쪽으로 읽었다 — 구현이 다르게 가려면 명세를 먼저 고친다)

> **1·9·10·12·13 은 명세에 반영됐다**(`docs/m5e-workplan.md`, 2026-09-08). 특히 1번은 반영하면서
> **뒤집혔다** — 기준선 없는 파일이 받은 것과 **바이트가 같으면 `unchanged`** 다. 전부
> `conflicted` 로 두면 gitignore 되는 실패 diff 때문에 ack 가 영영 안 나가고 결정 40 이 죽는다.
> 해당 시험은 `test_an_existing_local_file_without_a_baseline_is_judged_by_content` 로 바뀌었다.
> 나머지: `--no-wait` 안내 문면, `finish` 의 `finished_at`, `disabled` + `--fetch-artifacts` 는
> 종료 코드 0, `--dry-run` 은 충돌이 있어도 0. 정본은 명세다.

1. **기준선 없음 + 로컬이 받은 것과 바이트가 같을 때.** §11 표는 「있음(내용 무관) → conflicted」로
   못 박았고, 그대로 잠갔다(#2 의 `identical` 파라미터). 결과로 그 잡은 `--force` 없이는
   `complete` 가 서지 않아 ack 도 안 간다 — 데이터를 잃지 않는 쪽이다. 「같으면 unchanged」로
   완화하려면 표를 먼저 고쳐야 한다.
2. **`ApplyPlan.counts()` 와 unsafe 항목.** §18 주석이 네 통(new·unchanged·changed·conflicted)만
   적어서 unsafe 가 어디에 세어지는지 없다. 그래서 counts 는 **네 키 이상**만 요구하고, unsafe
   항목의 통은 잠그지 않았다. 대신 `safe() is False` 와 「아무것도 안 쓴다」를 잠갔다.
   unsafe 판정의 낱말(`verdict == "unsafe"`)도 직접 비교하지 않고 `plan.safe()` 로만 본다.
3. **안전하지 않은 경로를 `plan` 이 거부(예외)해도 되는가.** 명세는 「계획이 unsafe」만 말한다.
   예외로 거절하는 것도 「아무것도 안 쓴다」를 지키므로 도우미 `unsafe_plan` 이 둘 다 받는다.
4. **`apply` 의 `staging` 모양.** §18 서명이 디렉터리를 받으므로 「받은 파일이 같은 상대 경로로
   풀려 있는 디렉터리」로 읽었다(`staging/<path>`). `bundle.tar` 를 그대로 넘기는 설계라면 서명이
   달라져야 한다.
5. **`--force` 뒤 `ApplyResult.conflicted`.** 「끝에 남은 충돌 수」로 읽어 0 을 기대한다(#17).
   `complete` 가 「충돌이 하나도 남지 않았다」이므로 같은 뜻으로 세는 것이 일관된다.
   「원래 충돌이었던 수」로 세면 `complete` 와 어긋난다.
6. **저널의 파일별 상태 모양.** §11 은 「파일별 적용 상태」라고만 한다. `files` 가 `{경로: 상태}`
   든 `[{"path":…, "state":…}]` 든 읽는 도우미(`applied_in`)를 두고, 「적용됨」 낱말은
   `written`·`wrote`·`applied` 중 아무거나 받는다. 잠근 것은 **저널이 말하는 집합 = 디스크에 실제로
   들어간 집합**이다.
7. **결과 줄이 stdout 인가 stderr 인가.** `cli.py` 는 사람용 진행을 stderr 로 보내므로 stderr 로
   읽었지만, 도우미 `counts()` 는 두 스트림을 합쳐서 찾는다(#10). 대신 「stdout 은 JSON 한 줄」은
   따로 잠갔다(#26).
8. **`artifact_fetch` 의 모양.** §12·§18 은 이름만 준다. `ApplyResult` 의 JSON 으로 읽어
   `wrote`·`conflicted`·`complete` 키가 있을 것만 요구한다(그 이상은 자유).
9. **`--no-wait` 가 찍는 안내 줄의 정확한 문면.** `rcm artifacts <id>` 와 `--fetch` 가 들어 있는
   한 줄만 요구했다. `--output` 을 함께 찍을지는 잠그지 않았다(기준선 없는 받기라 필요하지만,
   무엇을 기본 디렉터리로 제안할지는 명세에 없다).
10. **원격 워커가 실행 종료 시각을 어떤 이름으로 보내는가.** §10 은 「실어 보낸다」까지만 말한다.
    `finish` 본문의 `finished_at`(ISO 8601, `core/status.iso`)으로 잠갔다.
11. **claim 응답의 언 정책 자리.** §9 는 「워커 claim 응답에 실어 보낸다」까지다. 픽스처는
    `preset["artifacts"]`(글롭)와 최상위 `artifacts`(정책 전체)를 **둘 다** 실어, 구현이 어느 쪽을
    읽어도 통과하게 뒀다.
12. **`disabled` 인 프리셋에 `--fetch-artifacts` 를 쓴 경우.** 실패가 아니라고 읽어 종료 0 을
    잠갔다(#25) — 없는 것을 못 받은 것은 전달 실패가 아니다. 사용 오류로 볼 수도 있다.
13. **`--dry-run` 이 충돌을 봤을 때의 종료 코드.** 아무것도 쓰지 않았으니 전달 실패(5)인지
    「보여 주기만 했으니 0」인지 명세에 없다. 0 과 5 **둘 다** 받고(#13), 대신 「쓰지 않았다」와
    「ack 를 안 보냈다」만 잠갔다.
14. **`--resume` 인데 저널이 없을 때.** 새로 받아 적용해도 되고 거절해도 된다. 잠근 것은
    「0 으로 끝났으면 파일이 실제로 거기 있다」와 「0 이 아니면 이유를 stderr 에 적는다」뿐이다(#19).
15. **`--resume` 의 저널 위치.** 명세에 없다. 그래서 CLI 쪽에서는 「저널이 없을 때 0 으로
    끝내지 않는다」만 잠갔고(#19), 이어 하기의 알맹이는 `apply` 층에서 본다(apply #27·#28).
16. **`artifact_cancel_timeout_seconds` 검증 오류 문면.** 두 키 이름이 모두 들어 있을 것을
    요구한다 — 「무엇을 무엇과 비교했는지」를 사람이 읽어야 고칠 수 있다.
17. **화면의 산출물 줄 자리와 문면.** M5d-2/3 가 `web/`·렌더를 바꾸는 중이라 줄의 자리도 낱말
    순서도 잠그지 않았다. `render` 출력의 **`recent —` 절 블록**만 잘라(§13 「최근 결과 행에 산출물
    한 줄」) ① `unknown` 이면 그 블록에 `—` 가 있고 「0 files/0 MB」 꼴이 **없다**, ② `empty` 면
    있고, ③ 두 블록이 서로 다르다 — 이 셋만 본다(#11·#12). 「모르면 줄을 아예 안 그린다」는
    설계라면 #11 이 계속 빨갛다. 그때는 §15.6 의 「`null` 을 `—` 로」를 먼저 정해야 한다.

## 열어 둔 것 (역할 C 밖이라 안 잠갔다)

- 서버 라우트의 상태 코드 표 전체(§6) · ack 자격과 `join_count` 규칙(§7) · 상한·회계·청소(§8) ·
  스키마 7 마이그레이션(§9) · 수집기 자체의 걷기·`open_anchored`(§4·§18 `collect.py`).
- `reason_args` 에 경로가 새지 않는지(§3) — 역할 A/B 의 §15.3.
- 브라우저 화면(§13)과 i18n 문자열.

## 실행

```
python -m pytest tests/test_apply.py tests/test_cli_m5e.py tests/test_worker_m5e.py \
                 tests/test_compat_m5e.py
```

구현 전(이 브랜치 `2900e57`) 결과 — 세 번 연속 같다(시간 의존 없음):

| 파일 | 수집 | 결과 | 빨간 이유 |
|---|---|---|---|
| `tests/test_apply.py` | 34 | 34 errors | `ModuleNotFoundError: remote_ci_monitor.apply` (픽스처) |
| `tests/test_cli_m5e.py` | 26 | 4 failed · 22 errors | 파서에 `--fetch-artifacts`/`artifacts` 없음(4) · `ConfigError: preset 'gold': unknown key(s): artifacts`(22) |
| `tests/test_worker_m5e.py` | 15 | 7 failed · 8 errors | `finish` 에 처분 없음 · `running` 을 먼저 pop · `unknown key 'artifact_cancel_timeout_seconds'` · 없는 `/worker/jobs/{id}/artifacts`(7) · 프리셋 키(8) |
| `tests/test_compat_m5e.py` | 12 | 2 failed · 10 errors | 렌더에 산출물 줄 없음(2) · 프리셋 키(10) |
| 합계 | **87** | **13 failed · 74 errors** | |

기존 스위트는 그대로다:
`python -m pytest --ignore=tests/test_apply.py --ignore=tests/test_cli_m5e.py
--ignore=tests/test_worker_m5e.py --ignore=tests/test_compat_m5e.py` → **1885 passed, 1 skipped**.

`ruff check .` 는 통과한다. `ruff format --check .` 는 **이 다섯 파일과 무관하게** 이미 실패한다 —
`docs/m5e-workplan.md` §18 의 python 코드 블록(한 줄에 `;` 로 붙인 상수·필드)을 포매터가 다시
쓰려 한다. 이 커밋(`2900e57`)에서 들어온 것이고 명세 파일은 손대지 말라는 지시라 그대로 뒀다.
새로 더한 다섯 파일만 보면 `ruff check`·`ruff format --check` 둘 다 통과한다.

### 시험이 스스로 모순되지 않는지 확인한 방법(프로브)

`src/` 를 건드리지 않기 위해, 스크래치에 `git archive HEAD` 사본을 풀고 거기에만
① `[[presets]].artifacts` 키를 뚫는 세 줄과 ② `apply.py`·`core/artifacts.py` 의 **최소 참조 구현**을
얹어 돌려 봤다. 결과: `tests/test_apply.py` **34/34 초록**(명세를 곧이곧대로 옮긴 구현으로 전부
만족된다 — 시험 쪽 오타·모순 없음), CLI 픽스처는 `snapshot: 3 files` 로 `--exclude` 가 의도대로
동작하고 `test_wait_exit_codes_and_the_json_field_are_unchanged_without_fetching` 가 초록
(잡이 실제로 돌고 `goldfail` 이 1 로 끝난다), 로컬 워커 시험은 `seen["workspace"] is True` 를
지나 「`finish` 에 묶음이 실리지 않았다」에서 멈춘다(오늘 코드가 이미 finish → rmtree 순서라는 뜻).
