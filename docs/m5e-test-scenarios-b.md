# M5e 테스트 시나리오 B — 저장소 · 서버 라우트 · 보존 (2026-09-08)

`docs/m5e-workplan.md` §6(라우트 표) · §7(삭제 규칙) · §8(상한·회계·M3 경계) · §9(스키마 7) ·
§10(추가만 하는 JSON) · §18(잠근 API 표면)을 `tests/test_store_m5e.py`(56 함수) ·
`tests/test_server_m5e.py`(46 함수) · `tests/test_janitor_m5e.py`(14 함수)로 옮긴 것이다
(test-first, 역할 B, 합계 **116 함수**). **`src/` 는 한 글자도 건드리지 않았다.** 기존 테스트 파일 ·
`pyproject.toml` · `PLAN.md` · `docs/m5e-workplan.md` 도 그대로다.

구현이 아직 없으므로 **빨간 것이 정상이다.** 오늘 결과: `112 failed, 4 passed`(세 파일 합계, 5초).
초록 4개는 전부 「바뀌면 안 되는 것」을 잡아 둔 회귀 못이다 —
`test_delete_old_jobs_still_removes_a_job_that_never_had_a_bundle`(M3 삭제 조건이 세지기만 하면
안 된다) · `test_the_status_route_is_404_for_a_job_that_does_not_exist`(지금은 라우트가 없어서
우연히 404 지만 구현 뒤에도 404 여야 한다) ·
`test_the_bundle_sweep_uses_the_existing_retention_sweep_interval`(새 주기 키를 만들지 않는다) ·
`test_existing_log_and_workspace_retention_is_unchanged`(M3 보존 불변).
빨강의 내역은 `core/artifacts.py` 없음 76건 · `jobs.join_count` 없음 9건 · `Store` 새 메서드 없음
8건 · 새 라우트가 404 8건 · `ServerSection` 새 키 없음 · 잡 JSON 에 `artifacts` 키 없음 ·
원격 `finished_at` 이 서버 수신 시각으로 밀림 1건이다.

공통: 시각은 **주입한 시계**(store·janitor 는 `now` 인자, 서버는 `WorkerServer.clock`)로만 민다 —
`sleep` 도 벽시계 의존도 없다. 파일 배치는 진짜와 같게 `tmp_path` 아래에 두고
(`<data_dir>/artifacts/<job_id>/bundle.tar` · `manifest.json` · `<data_dir>/artifacts/.staging/`),
번들은 `start_collect` → 파일 설치 → `publish_bundle` 순서로 만든다. 표준 라이브러리 + `pytest`
말고는 아무것도 안 쓴다. `ruff check` · `ruff format --check` 초록(line-length 100).

아직 없는 모듈(`core/artifacts.py` · `collect.py`)은 **테스트 안에서 늦게 import** 한다 — 파일
하나가 통째로 수집 오류가 되는 대신 테스트마다 따로 빨개져서 「무엇이 없는가」가 이름으로 보인다.

## 잠근 API (§18 그대로 · 이 테스트가 쓰는 이름)

- `core/artifacts.py`: 상태 상수 13개(`DISABLED`·`PENDING`·`COLLECTING`·`UPLOADING`·`READY`·
  `EMPTY`·`DROPPED`·`FAILED`·`SKIPPED`·`PURGED`·`EXPIRED`·`UNAVAILABLE`·`UNKNOWN`) ·
  `ArtifactPolicy(globs, max_bytes, max_files, timeout_seconds, cancel_timeout_seconds)` ·
  `BundleFile(path, size, sha256, mode)` · `AckDecision(status, purge, record, reason_code)` ·
  `expires_at(ready_at, hours)`.
- `collect.py`: `CollectResult(state, files, total_bytes, bundle_bytes, skipped_count,
  bundle_sha256, bundle_path, reason_code, reason_args, detail)`.
- `core/retention.py`: `BundleInfo(job_id, state, expires_at, bytes)`.
- `store.py`: `DB_VERSION = 7` · `_SCHEMA_V1` 에 `join_count`·`job_artifacts` · `_MIGRATIONS[7]` ·
  `get_bundle` · `start_collect` · `reserve_bundle_bytes` · `release_bundle_bytes` ·
  `bundle_storage_totals` · `publish_bundle` · `set_bundle_failed` · `ack_bundle` · `bundles_due` ·
  `mark_bundles_purged` · `reconcile_bundles_on_start` · `finish(..., bundle=None)` ·
  `join_or_bump` 이 `jobs.join_count` 를 1 늘린다.
- 서버: `GET /jobs/{id}/artifacts` · `GET`·`HEAD /jobs/{id}/artifacts/archive` ·
  `POST /jobs/{id}/artifacts/ack` · `PUT`·`GET /worker/jobs/{id}/artifacts` ·
  잡 JSON 의 `artifacts` 객체 · `server.artifact_storage` · 이벤트 종류 `artifacts_changed` ·
  `POST /worker/jobs/{id}/finish` 의 선택 `artifacts`·`finished_at`.
- 설정: `Preset.artifacts` · `ServerSection` 8개 키(`artifact_retention_hours`=24 ·
  `max_artifact_bytes`=1 GiB · `max_artifact_files`=10000 · `artifact_storage_max_bytes`=10 GiB ·
  `artifact_timeout_seconds`=60 · `artifact_cancel_timeout_seconds`=5 ·
  `artifact_transfer_timeout_seconds`=300 · `max_concurrent_artifact_transfers`=2).

## `tests/test_store_m5e.py` — 스키마 7 · join_count · 회계 · ack · 만료 · 화해

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | **새 DB** 가 7 · `PRAGMA user_version` 7 · `jobs.join_count`(NOT NULL DEFAULT 0) · `job_artifacts` 18열 · `job_artifacts_expiry` 인덱스 · `_SCHEMA_V1` 문면 | `test_fresh_database_is_schema_7_with_join_count_and_job_artifacts` | §9 (`store.py:294`) |
| 2 | `_MIGRATIONS[7]` 은 문장 하나씩의 튜플 | `test_migration_7_is_one_statement_per_tuple_entry` | §9 (`store.py:173`) |
| 3 | 6→7: 기존 잡·합류자 살아남고 `join_count` 는 0 · 옛 잡은 번들 행 없음(= `unknown`) · 만료 대상 아님 | `test_migration_from_6_to_7_keeps_rows_and_defaults_join_count_to_zero` | §9 |
| 4 | 두 번 다시 열어도 버전·행·해시 그대로 | `test_reopening_twice_changes_nothing` | §9 |
| 5 | `jobs.artifacts_purged_at`(M3) 과 `job_artifacts.purged_at`(M5e) 은 서로를 안 건드린다 | `test_m3_artifacts_purged_at_and_the_bundle_purge_are_different_things` | §1 |
| 6 | `add_joiner` 만으로는 카운트가 안 는다(픽스처로 `join_count == 0` 을 만들 수 있다) | `test_join_count_starts_at_zero_and_add_joiner_alone_does_not_bump_it` | §7 (`store.py:782`) |
| 7 | 다른 토큰 합류 → 줄 하나 + 카운트 1 | `test_a_different_token_joining_bumps_the_count_and_adds_the_row` | §7 |
| 8 | **요청자 재제출** → 합류자 줄은 안 생기고 카운트만 1·2 | `test_the_requester_resubmitting_bumps_the_count_without_a_joiner_row` | §7 (`store.py:619`) |
| 9 | **같은 합류자 재제출** ×3 → 줄 하나, 카운트 3 | `test_the_same_joiner_resubmitting_bumps_the_count_without_a_second_row` | §7 (`INSERT OR IGNORE`) |
| 10 | 아무도 안 붙은 잡은 0 | `test_a_job_nobody_joined_keeps_join_count_zero` | 결정 40 |
| 11 | 끝난 잡에는 합류가 안 붙고 카운트도 안 는다 | `test_join_or_bump_on_a_terminal_job_does_not_bump` | §2 `ACTIVE_STATES` |
| 12 | 합류자가 떠나도 카운트는 안 돌아온다 | `test_a_joiner_leaving_never_gives_the_count_back` | §7 (`server.py:1304`) |
| 13 | 8 스레드 동시 합류 → 줄 8 · 카운트 정확히 8(갱신이 사라지지 않는다 = 한 트랜잭션) | `test_concurrent_joins_are_all_counted_exactly_once` | §7 (`store.py:606`) |
| 14 | 예약 → `(0, 100)` → 반납 → `(0, 0)`, 두 번째 반납도 조용 | `test_reserve_and_release_move_the_totals` | §8 회계 |
| 15 | 상한 초과 예약은 False 고 **아무것도 안 바뀐다** · 같은 잡이 다시 잡으면 더하지 않고 바꾼다 | `test_a_refused_reservation_changes_nothing` | §8 |
| 16 | 8 스레드가 400 씩 · 상한 1000 → 정확히 둘만 성공, 합계 ≤ 상한 | `test_concurrent_reservations_never_oversubscribe_the_limit` | §8 |
| 17 | 발행이 예약을 실측 바이트로 바꾼다(예약 0, 저장 실측) | `test_publishing_turns_the_reservation_into_measured_stored_bytes` | §8 |
| 18 | 저장 바이트와 예약 바이트를 따로 센다 | `test_stored_and_reserved_are_counted_separately` | §8 |
| 19 | 발행이 해시·파일 수·원본/아카이브 바이트·`ready_at`·`expires_at` 을 기록 | `test_publish_bundle_records_the_hash_counts_and_expiry` | §8 · §9 |
| 20 | 읽어도 만료가 연장되지 않는다 | `test_reading_a_bundle_does_not_extend_its_expiry` | §8 |
| 21 | `set_bundle_failed` 는 상태·이유·인자만 쓰고 해시는 안 쓴다 · **잡 요약은 안 건드린다** | `test_set_bundle_failed_records_the_state_and_reason_without_a_hash` | §3 |
| 22 | `reason_args` 에 경로가 없다(수치만) | `test_reason_args_never_carry_a_path` | §3 |
| 23 | 번들이 없던 잡·없는 잡은 `None` | `test_get_bundle_returns_none_for_a_job_that_never_had_one` | §18 |
| 24 | `join_count == 0` + 요청자 ack → 200 · purge · record, `acked_at` 기록 | `test_ack_of_an_unjoined_job_purges_immediately` | 결정 40 |
| 25 | 합류가 있었던 잡 → 200 · **purge 안 함** · `expires_at` 그대로 | `test_ack_of_a_joined_job_records_but_does_not_purge` | 결정 40 |
| 26 | `owner=False`(admin) → 200 · purge 없음 · **record 없음**(`acked_at` 미기록) | `test_an_admin_who_is_neither_requester_nor_joiner_acks_as_a_no_op` | §7 |
| 27 | 해시 불일치 → 409, 아무것도 안 바뀐다 | `test_ack_with_a_different_hash_is_409_and_changes_nothing` | §6 |
| 28 | `purged` 뒤 **같은 해시 재생 → 200**, 묘비 해시와 첫 `acked_at` 이 남는다 | `test_replaying_an_ack_on_a_purged_bundle_with_the_same_hash_is_200` | §7 재생 |
| 29 | `purged` 뒤 다른 해시 → 409 | `test_replaying_a_purged_bundle_with_a_different_hash_is_409` | §7 |
| 30 | 만료 시각을 지나 온 ack → 410 | `test_ack_after_the_expiry_moment_is_410` | §6 |
| 31 | 이미 `expired` 인 번들 → 410 | `test_ack_of_an_expired_bundle_is_410` | §6 |
| 32 | `collecting`·`failed`·`empty` → 409 (×3) | `test_ack_of_a_bundle_that_is_neither_ready_nor_purged_is_409` | §6 |
| 33 | 번들 행이 없는 잡 → 409 | `test_ack_of_a_job_without_a_bundle_is_409` | §6 |
| 34 | 동시 ack 둘 → 둘 다 200 이지만 **지우라는 답은 하나뿐** | `test_two_concurrent_final_acks_schedule_exactly_one_deletion` | §7 |
| 35 | `bundles_due` 는 만료가 지난 것만 · `BundleInfo` 의 `state`·`bytes`·`expires_at` | `test_bundles_due_returns_only_rows_whose_expiry_has_passed` | §18 |
| 36 | 경계는 `<=` (1초 전은 아니고 정각은 대상) | `test_the_expiry_boundary_is_inclusive` | §18 |
| 37 | **만료 없는 행은 절대 대상이 아니다** | `test_a_row_with_no_expiry_is_never_due` | §18 |
| 38 | 이미 `purged`·`expired` 인 행은 다시 안 나온다 | `test_bundles_due_skips_rows_that_are_already_gone` | §8 |
| 39 | `limit` 을 지키고 `job_id` 오름차순 | `test_bundles_due_honours_the_limit_and_orders_by_job_id` | §18 |
| 40 | `mark_bundles_purged` 가 상태·시각을 쓰고 바이트를 반납 · idempotent · 빈 목록 0 | `test_mark_bundles_purged_sets_the_state_and_time_and_releases_the_bytes` | §8 |
| 41 | 화해 ①: 고아 스테이징 삭제, 설치된 것은 그대로 | `test_reconcile_removes_orphan_staging_directories` | §9 ① |
| 42 | 화해 ②: `collecting`·`uploading` → `failed`+`interrupted`, 예약 반납 | `test_reconcile_closes_collecting_and_uploading_rows_and_releases_reservations` | §9 ② |
| 43 | 화해 ③: `ready` 인데 파일 없음 → `unavailable`(조용히 `empty` 로 안 만든다) | `test_reconcile_marks_ready_rows_with_missing_files_unavailable` | §9 ③ · §3 |
| 44 | 화해 ④: **행 없는 `artifacts/<id>/` 삭제**, 행 있는 것은 보존 | `test_reconcile_removes_an_installed_directory_that_has_no_row` | §9 ④ |
| 45 | 번들이 있어도 `lost` 잡은 `lost` 그대로(exit_code 도 없음) | `test_a_bundle_never_turns_a_lost_job_into_a_success` | §5 · §9 |
| 46 | 화해 ⑤: finish 없는 영수증도 `expires_at` 이 박혀 있어 sweep 이 가져간다 | `test_a_receipt_without_a_finish_already_has_an_expiry_so_the_sweep_takes_it` | §9 ⑤ |
| 47 | 화해는 idempotent | `test_reconcile_is_idempotent` | §9 |
| 48 | `finish(bundle=…)` 가 종료 상태와 묶음을 **한 트랜잭션**으로 커밋 | `test_finish_commits_the_terminal_state_and_the_bundle_together` | §5 |
| 49 | 상한 초과로 버려도 **잡은 그대로 성공**하고 요약은 안 바뀐다 | `test_finish_records_a_dropped_bundle_next_to_a_successful_job` | 결정 41 · §3 |
| 50 | 수집 중 취소가 이긴다 — `only_from` 이 막은 finish 는 **묶음도 안 남긴다**, 다시 판정한 `cancelled` 와는 같이 커밋 | `test_a_cancellation_accepted_during_collection_wins` | §5 |
| 51 | `bundle` 을 안 주면 행이 안 생긴다(M5e 이전과 동일) | `test_finish_without_a_bundle_leaves_no_row` | §18 |
| 52 | 두 번째 finish 는 상태도 묶음도 못 바꾼다(묶음은 불변) | `test_a_second_finish_neither_changes_the_state_nor_the_bundle` | §3 |
| 53 | 번들 **파일이 남아 있으면** 메타데이터를 안 지운다 | `test_delete_old_jobs_keeps_a_job_whose_bundle_files_still_exist` | §8 (`store.py:475`) |
| 54 | 번들 **예약이 남아 있으면** 안 지운다 | `test_delete_old_jobs_keeps_a_job_whose_bundle_still_holds_a_reservation` | §8 |
| 55 | 둘 다 사라진 뒤에야 잡 행과 `job_artifacts` 행이 같이 지워진다 | `test_delete_old_jobs_removes_the_job_and_its_bundle_row_once_both_are_gone` | §8 |
| 56 | 번들이 없던 잡은 예전 규칙 그대로 지워진다 (**지금 초록**) | `test_delete_old_jobs_still_removes_a_job_that_never_had_a_bundle` | M3 회귀 |

## `tests/test_server_m5e.py` — §6 라우트 표 전수 · §10 추가 키 · 이벤트

`test_worker_api.WorkerServer`(in-process HTTP · 주입 시계 · client/admin/worker 토큰)를 쓰고
프리셋만 갈아 끼운다: `goldens`(글롭 있음) · `ok`(글롭 없음 → `disabled`) · `slow`.
잡당 상한을 작게(1000 바이트 · 4 파일) 잡아 아카이브 허용치 경계 **4072**를 눈으로 본다.

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 요청자·합류자·admin 셋 다 상태 문서와 아카이브를 받는다 | `test_requester_joiner_and_admin_all_read_the_status_and_the_archive` | §6 |
| 2 | **워커 토큰은 거부**(상태·아카이브·ack 전부 403) | `test_a_worker_token_is_refused_on_the_artifact_reads` | §6 |
| 3 | 남은 403, 토큰 없으면 401 (세 라우트) | `test_a_stranger_is_403_and_no_token_is_401` | §6 |
| 4 | **`read_auth = none` 이어도** 산출물 라우트는 401 (다른 읽기는 200) | `test_protected_reads_require_a_token_even_when_read_auth_is_none` | §6 |
| 5 | 없는 잡은 404 (**지금 초록** — 라우트가 없어서 우연히 맞다) | `test_the_status_route_is_404_for_a_job_that_does_not_exist` | §6 |
| 6 | `read_auth = basic` 에서 Basic 은 **읽기만**, ack 는 401 | `test_basic_credentials_may_read_but_never_ack` | §6 CSRF |
| 7 | 글롭 없는 프리셋 → `disabled` | `test_a_preset_without_globs_reads_disabled` | §3 |
| 8 | 안 끝난 잡 → `pending` | `test_an_unfinished_job_reads_pending` | §3 |
| 9 | 끝났는데 보고가 없다 → **`unknown`**(`empty` 아님) | `test_a_finished_job_that_reported_nothing_reads_unknown_not_empty` | §3 · §5 |
| 10 | `empty`·`dropped`·`failed`·`skipped`·`unavailable` 은 `state`+`reason_code` 만(파일 목록 없음) | `test_non_ready_states_carry_only_the_state_and_reason_code` | §6 |
| 11 | `ready` 는 매니페스트(경로·크기·sha256·mode)와 `detail` 을 준다 | `test_the_ready_document_carries_the_manifest_and_detail` | §6 |
| 12 | 아카이브 **409**: `pending`·`collecting`·`uploading` | `test_archive_is_409_while_the_bundle_is_still_in_progress` | §6 |
| 13 | 아카이브 **404**: `disabled`·`unknown`·`empty`·`skipped` | `test_archive_is_404_when_there_is_nothing_to_download` | §6 |
| 14 | 아카이브 **410**: `purged`·`expired`·**만료 시각 지남**(행은 아직 `ready`) | `test_archive_is_410_after_purge_expiry_or_past_the_expiry_moment` | §6 |
| 15 | 아카이브 **503**: 메타는 있고 파일이 없다(`unavailable`) | `test_archive_is_503_when_the_metadata_is_there_but_the_file_is_not` | §6 |
| 16 | 200 스트리밍: `application/x-tar` · `Content-Length` · `Cache-Control: no-store` · `Content-Disposition: attachment` · 본문 일치 | `test_archive_streams_the_tar_with_no_store_and_attachment_headers` | §6 |
| 17 | `HEAD` 는 같은 상태·헤더 · 본문 없음 · **부수 효과 없음**(만료 연장·ack·파일 변화 없음) | `test_head_archive_has_the_same_status_and_headers_with_no_body_or_side_effect` | §6 |
| 18 | 전송 슬롯이 차면 **기다리지 않고 즉시 503 + `Retry-After`**, 일반 요청은 굶지 않는다 | `test_the_transfer_slot_is_taken_without_blocking` | §6 (`server.py:1502`) |
| 19 | `join_count == 0` ack → `{"purged": true}` · 파일 삭제 · 이후 아카이브 410 | `test_ack_of_an_unjoined_job_purges_the_bundle_immediately` | 결정 40 |
| 20 | 합류 있었던 잡 ack → `{"purged": false}` · **둘 다 받아 간다** | `test_ack_of_a_joined_job_keeps_the_bundle_until_the_ttl` | 결정 40 |
| 21 | admin ack 은 no-op(`acked_at` 미기록 · 여전히 받을 수 있다) | `test_an_admin_ack_is_a_no_op` | §7 |
| 22 | 틀린 해시 409 → 맞는 해시 200 → 재생 200 → 다른 해시 409 | `test_ack_with_a_wrong_hash_is_409_and_the_replay_of_the_same_hash_is_200` | §6 · §7 |
| 23 | 만료 뒤 ack → 410 | `test_ack_after_the_expiry_moment_is_410` | §6 |
| 24 | `ready`·`purged` 가 아니면 409 | `test_ack_of_a_bundle_that_is_not_ready_is_409` | §6 |
| 25 | 본문 이상(빈 객체 · 정수 · 해시 모양 아님 · 깨진 JSON) → 400 (×4) | `test_ack_with_a_bad_body_is_400` | §6 |
| 26 | ack 은 GET 이 아니다 → 405 | `test_ack_is_not_a_get` | §6 |
| 27 | 업로드 201 + **검증된 영수증**(`bundle_sha256`) | `test_worker_upload_returns_201_with_a_verified_receipt` | §6 |
| 28 | 같은 바이트 재시도 → 200(멱등), 같은 해시 | `test_uploading_the_same_bytes_again_is_idempotent_200` | §6 |
| 29 | 다른 묶음 → 409, 먼저 것이 남는다(불변) | `test_uploading_a_different_bundle_is_409` | §3 · §6 |
| 30 | 종료된 잡(`succeeded`·`lost`)에 늦게 온 업로드 → 409 (×2) | `test_uploading_to_a_terminal_job_is_409` | §5 |
| 31 | `Content-Length` 없이(chunked) → 411 | `test_uploading_without_content_length_is_411` | §6 |
| 32 | 원본 상한 정각(1000)은 **통과**, 아카이브 허용치+1(4073)은 413 · 잡은 안 죽는다 | `test_uploading_over_the_archive_allowance_is_413_but_the_boundary_fits` | §8 회계 |
| 33 | `Content-Type` 틀리면 415 | `test_uploading_with_the_wrong_content_type_is_415` | §6 |
| 34 | 남의 워커 403 · 클라이언트 토큰 403 | `test_another_workers_job_is_403_and_a_client_token_is_403` | §6 |
| 35 | 복구 GET 이 **종료된 잡에도 답한다**(영수증 해시 + 잡 상태) | `test_the_worker_recovery_get_answers_for_a_terminal_job` | §6 (`remote_workers.py:364`) |
| 36 | 영수증이 없으면 `bundle_sha256: null` — 다시 올릴 수 있다 | `test_the_worker_recovery_get_reports_a_missing_receipt_so_it_can_be_sent_again` | §9 |
| 37 | 복구 GET 도 남의 잡·클라이언트 토큰은 403 | `test_the_worker_recovery_get_is_403_for_another_workers_job` | §6 |
| 38 | 잡 JSON 에 `artifacts` 객체(활성·종료 둘 다) · §10 의 10개 키 · 기존 키 불변 | `test_job_json_carries_the_additive_artifacts_object` | §10 |
| 39 | 모르는 수는 `null`, `0` 은 「모았는데 없었다」일 때만 | `test_unknown_counts_are_null_and_zero_only_means_a_completed_empty_collection` | §10 |
| 40 | `/api/status` 의 `queue[]`·`recent[]` 에도 `artifacts` · `server.artifact_storage` 5개 키 | `test_status_document_rows_carry_the_artifacts_object_and_the_storage_block` | §10 |
| 41 | **공개 문서·`reason_args` 에 경로가 없다**(파일명·data_dir 문자열 부재) · 경로는 보호 라우트에만 | `test_no_file_path_ever_reaches_the_public_document_or_reason_args` | §3 · §10 |
| 42 | 이벤트 종류는 `artifacts_changed` · `job_finished` 재발행 없음 · 경로 없음 · **상태 캐시 무효화** | `test_the_new_event_kind_is_artifacts_changed_and_it_invalidates_the_status_cache` | §10 (`server.py:367`) |
| 43 | 원격 워커가 실은 실행 종료 시각을 서버가 쓴다 — 수집 때문에 `finished_at`·`job_seconds` 가 안 밀린다 | `test_a_collection_never_delays_the_v1_time_fields` | §10 |
| 44 | `artifacts` 필드가 통째로 없는 옛 워커 → `unknown`(`empty` 아님) | `test_an_old_worker_that_reports_no_artifacts_field_is_unknown_not_empty` | §5 |
| 45 | `[[presets]].artifacts` 가 설정 키, 기본값 `()` | `test_preset_artifacts_globs_are_a_config_key` | §4 · §18 |
| 46 | `ServerSection` 새 키 8개의 기본값 | `test_the_new_server_keys_have_the_spec_defaults` | §8 · §18 |

## `tests/test_janitor_m5e.py` — TTL sweep 과 M3 경계

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | `expires_at <= now` → 파일·디렉터리 삭제 · `expired` · `purged_at` · 회계 0 · 잡 행은 남는다 | `test_the_expiry_sweep_deletes_at_the_expiry_moment_and_marks_expired` | §8 |
| 2 | 경계는 `<=`, 23시간짜리는 그대로 | `test_the_expiry_boundary_is_inclusive_and_a_young_bundle_is_untouched` | §8 |
| 3 | 만료 없는 행(`dropped`)은 400일 뒤에도 안 건드린다 | `test_a_bundle_with_no_expiry_is_never_swept` | §18 |
| 4 | 이미 `purged` 면 만료가 확인을 덮어쓰지 않는다 | `test_an_already_purged_bundle_is_not_swept_again` | §7 |
| 5 | 주기는 기존 `retention_sweep_interval_seconds` (**지금 초록**) | `test_the_bundle_sweep_uses_the_existing_retention_sweep_interval` | §8 |
| 6 | **`retention_days_success = 0` 에서도 24시간이 지켜진다** — M3 는 로그·워크스페이스만 가져가고 번들은 TTL 이 찰 때 사라진다 | `test_retention_days_success_zero_does_not_destroy_a_bundle_before_its_ttl` | §8 · 결정 40 |
| 7 | 살아 있는 번들의 `artifacts/<id>/` 는 M3 가 절대 안 지운다, TTL 뒤에는 간다 | `test_m3_cleanup_never_removes_the_artifacts_directory_of_a_live_bundle` | §8 |
| 8 | 메타데이터 삭제는 번들이 진짜 사라진 뒤 | `test_metadata_deletion_waits_until_the_bundle_is_really_gone` | §8 |
| 9 | 기존 로그·워크스페이스 보존 불변 (**지금 초록**) | `test_existing_log_and_workspace_retention_is_unchanged` | M3 회귀 |
| 10 | 활성 잡은 번들이 있어도 절대 대상이 아니다 | `test_an_active_job_with_a_bundle_is_never_touched` | `core/retention.py` |
| 11 | 삭제 실패 → `purged` 로 표시 안 함 · 바이트 회계 유지 · 오류에 경로 없음 · 스레드 안 죽음 · **다음 sweep 에 재시도** | `test_a_failed_unlink_leaves_the_row_unpurged_keeps_the_bytes_and_retries` | §7 |
| 12 | 파일이 이미 없어도 조용히 행을 닫는다 | `test_a_missing_bundle_file_still_closes_the_row_without_error` | §8 |
| 13 | **만료를 가로지르는 다운로드**는 끝까지 가고, 마지막 독자가 닫을 때까지 바이트가 회계에 남는다. 새 다운로드는 그동안 410 | `test_a_download_in_flight_across_the_expiry_finishes_and_the_bytes_stay_accounted` | §6 |
| 14 | 번들이 만료돼도 잡의 상태·요약은 그대로 | `test_a_finished_job_keeps_its_summary_when_the_bundle_expires` | §3 |

13번은 유일하게 진짜 소켓을 쓴다. 기다림 대신 **같은 연결의 다음 응답**(`GET /api/health`)을
배리어로 삼는다 — 그 응답이 오면 앞 핸들러는 이미 정리를 마쳤다. sleep 도 폴링도 없다.

## 명세에서 애매했던 것 (안전한 쪽으로 골랐고, 구현이 다르면 여기부터 본다)

> **1·2·3·4·6·7·8·10·11·14·15 는 명세에 반영됐다**(`docs/m5e-workplan.md`, 2026-09-08) —
> `get_bundle` 의 시각·`reason_args` 모양, 행이 없을 때의 파생 상태, 예약은 더하지 않고 바꾼다,
> `BundleInfo.bytes` 는 아카이브 바이트, 취소는 `only_from` 이 막는다, 만료 sweep 은 디렉터리째,
> `Content-Disposition` 의 파일 이름, `ready` 문서의 최상위 `files`, 워커 GET 의 응답 키,
> `finish` 의 `finished_at`. 정본은 명세다.

1. **`get_bundle` 이 돌려주는 시각의 타입.** §18 은 `dict[str, Any]` 라고만 한다. `Store` 의 다른
   공개 값(`Job.finished_at` · `TokenInfo.created_at`)이 전부 **UTC aware `datetime`** 이라 그쪽에
   맞췄다(`ready_at`·`expires_at`·`acked_at`·`purged_at`). epoch float 로 내면 이 테스트들이
   빨갛다 — 그때는 `Store` 안팎의 시각 표현이 갈리는 것이므로 구현을 바꾸는 쪽이 맞다.
2. **`reason_args` 는 파싱된 dict.** DB 열은 TEXT(`reason_args`)지만 `get_bundle` 은 JSON 을 푼
   dict 를 준다고 봤다(`summary_args` 가 `load_args` 로 그렇게 한다).
3. **`disabled`·`pending`·`unknown` 은 행 없이 파생된다.** `create_job` 에 정책을 얼리는 자리가
   §18 에 없다. 그래서 번들 행이 없을 때 서버가 이렇게 읽는다고 잠갔다 — 프리셋에 글롭이 없으면
   `disabled` · 잡이 활성이면 `pending` · 잡이 끝났으면 `unknown`. 「보고하지 않은 옛 워커는
   `unknown` 이지 `empty` 가 아니다」(§3)와 정확히 맞는다. 프리셋이 설정에서 사라진 잡은
   잠그지 않았다(무엇이었는지 알 수 없다 — 지어내지 않는다).
4. **`reserve_bundle_bytes` 는 잡당 하나다.** 같은 잡이 다시 부르면 **더하지 않고 바꾼다**.
   더하면 재시도 한 번에 회계가 두 배가 된다.
5. **`BundleInfo.bytes` 는 디스크가 실제로 쥔 바이트**(아카이브 바이트)로 봤다. 회계와 청소가
   보는 수이므로 원본 바이트가 아니다.
6. **`publish_bundle` 은 `start_collect` 가 연 행을 채운다**고 보고 헬퍼가 항상 그 순서로 부른다.
   `publish_bundle` 이 스스로 INSERT 해도 이 테스트는 통과한다(양쪽 다 안전).
7. **수집 중 취소.** §5 는 「그 트랜잭션 안에서 취소 상태를 다시 읽는다」면서 동시에
   「`Store.finish` 도 이걸 자동으로 해 주지 않는다」고 적는다. `finish` 가 조용히 상태를
   바꿔치기하면 기존 호출자의 뜻이 달라지므로, **`only_from` 으로 막히는 쪽**을 잠갔다:
   `cancelling` 잡에 `finish(succeeded, bundle=…, only_from=(running,))` 는 False 고
   **묶음도 안 남긴다**(원자성). 다시 판정한 `cancelled` 와 함께 부르면 묶음이 같이 커밋된다 —
   모은 것을 버리지 않는다.
8. **만료 sweep 은 `artifacts/<id>/` 를 디렉터리째 지운다**(파일만 지우고 빈 디렉터리를 남기지
   않는다). 남겨 두면 「행 없는 설치 디렉터리」(§9 ④)와 구분이 안 된다.
9. **M3 가 남은 `artifacts/<id>/` 를 치우는 경로는 잠그지 않았다.** `list_unpurged_finished` 는
   이미 표시된 잡을 다시 안 주므로 M3 는 그 잡을 두 번 보지 않는다. 실제로 디렉터리를 지우는
   것은 ack 경로와 만료 sweep 이고, 그래도 남은 것은 **재시작 화해 ④** 가 가져간다. 그래서
   잠근 것은 안전한 방향 하나뿐이다: **살아 있는 번들의 디렉터리를 M3 가 지우지 않는다.**
10. **아카이브 GET 의 `Content-Disposition`** 은 `attachment` 로 시작하기만 요구했다. 파일 이름
    부분은 잠그지 않았다 — 거기에 무엇을 넣을지가 정해져 있지 않고, 잡 번호 말고는 넣을 것도
    없다.
11. **`ready` 문서의 매니페스트 자리.** 최상위 `files` 든 `manifest.files` 든 받는다 — §6 은
    「매니페스트 포함」이라고만 한다. 항목의 키(`path`·`size`·`sha256`·`mode`)와 순서는 잠갔다.
12. **`uploading` 상태는 DB 에 직접 놓는다.** §18 에 그 상태로 옮기는 store 메서드가 없다
    (업로드 라우트가 만든다). 라우트 상태 코드만 보는 자리라 `job_artifacts.state` 를 직접 썼다.
13. **`ack` 의 400 조건.** 「본문 이상」의 범위가 없어서 빈 객체 · 정수 · 64 hex 가 아닌 문자열 ·
    깨진 JSON 넷을 400 으로 잠갔다. 해시 모양 검사를 안 하면 이 중 하나가 409 로 떨어진다.
14. **`GET /worker/jobs/{id}/artifacts` 의 응답 키**는 `bundle_sha256`(없으면 `null`)과
    `job_state` 로 잠갔다. §6 은 「업로드 처리 상태와 잡 상태」라고만 한다.
15. **`finished_at` 을 워커가 실어 보낸다**(§10)는 요구를 `POST /worker/jobs/{id}/finish` 의
    선택 필드 `finished_at`(ISO Z)으로 잠갔다. 이름이 §18 에 없다.
16. **프리셋 파싱은 관대하게.** `parse_preset` 이 아직 `artifacts` 키를 모르면 글롭을 떼고
    파싱해 라우트 테스트가 계속 돈다. 키 자체는 46번 테스트가 따로 잠근다 — 파일 46개가
    설정 하나 때문에 같은 이유로 죽는 것을 막으려는 것이지, 규칙을 무르게 한 것이 아니다.

범위 밖(역할 A): 글롭 문법과 `core/artifacts.py` 순수 함수 · `collect.py` 수집기와
`open_anchored` · 클라이언트 `apply.py`/저널/CLI · 워커 두 경로의 생애 · 화면.
