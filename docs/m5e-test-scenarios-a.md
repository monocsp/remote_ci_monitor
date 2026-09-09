# M5e 테스트 시나리오 A — 순수 규칙 · 수집기 · 번들 TTL (2026-09-08)

`docs/m5e-workplan.md` §3(상태·이유 코드) · §4(글롭·경로·걷기·여는 방법) · §7(삭제 규칙) ·
§8(회계·보존) · §11(적용 분류표) 과 §15 의 테스트 묶음 ①·⑤ 중 **순수 규칙과 수집기 부분**을
`tests/test_artifacts_rules.py`(81 함수 · 파라미터 포함 179건) ·
`tests/test_collect.py`(41 함수 · 53건) · `tests/test_retention_bundles.py`(18 함수 · 19건)
로 옮긴 것이다(test-first, 역할 A). **`src/` 는 한 글자도 건드리지 않았다.**

**늦은 import**: 아직 없는 모듈(`core/artifacts.py` · `collect.py`)과 아직 없는 이름
(`core/retention.py` 의 `BundleInfo`·`bundles_to_expire`)은 파일 맨 위가 아니라 **시험 안에서**
부른다 — `artifacts_core()` · `collector()` · `retention()`. 맨 위에서 import 하면 파일 하나가
통째로 **수집 오류**가 되어 `pytest` 가 `Interrupted` 로 멈추고 **기존 1885건까지 안 돈다.**
픽스처로 하면 시험마다 `ERROR at setup` 이 되므로(직접 확인) B 처럼 **시험 본문에서 부르는 평범한
함수**로 두어 `FAILED` 로 떨어지게 했다.

그래서 구현 전 지금은 시험 하나하나가 한 줄짜리 이유와 함께 빨갛다:

```
FAILED tests/test_artifacts_rules.py::test_reasons_holds_the_fourteen_codes
  remote_ci_monitor.core.artifacts is not implemented yet (docs/m5e-workplan.md §18): …
FAILED tests/test_collect.py::test_collect_result_defaults_and_frozen
  remote_ci_monitor.collect is not implemented yet (docs/m5e-workplan.md §18): …
FAILED tests/test_retention_bundles.py::test_empty_input_gives_an_empty_list
  remote_ci_monitor.core.retention has no BundleInfo · bundles_to_expire yet (…§18)
```

세 파일만: **250 failed, 1 passed**(251건). 하나 초록인 것은 모듈을 안 쓰는
`test_a_real_tar_is_padded_to_a_full_record` 다. 저장소 뿌리에서 그냥 `pytest`:
**250 failed, 1886 passed, 1 skipped** (3분 51초) — `Interrupted` 없이 끝까지 돈다.
기존 1885건은 그대로 초록이다. `ruff check .` · `ruff format --check .` 둘 다 통과.

공통: 주석·독스트링은 한국어, 식별자와 테스트 이름은 영어 · 표준 라이브러리만 · **sleep 없음**
(예산은 `now_fn` 을 끼워 시각을 뛰게 해서 본다) · 네트워크 없음 · 파일은 전부 `tmp_path` 안 ·
시각은 `jobfactory.NOW`(고정) 기준. 상태 낱말 13개는 `STATE_CONSTANTS` 에 (상수 이름, 공개 문자열)
짝으로 적어 두어 파라미터를 모듈 없이 만들 수 있게 했다 — 그 짝이 실제 상수와 같은지는
`test_state_constants_are_the_public_strings` 와 `test_artifact_states_holds_the_thirteen_values`
가 잠근다.

**작성 중에 확인한 자기검증**: 스크래치패드에 §18 표면을 그대로 구현한 일회용 참조 스텁을 만들어
`pytest -p stub_loader` 로 세 파일을 돌렸다 — **249 passed, 2 skipped**(스킵 둘은 아래 「파일
시스템」 항목). 늦은 import 로 바꾼 **뒤에도 같은 249/2** 라서 단정문은 하나도 안 바뀌었다.
그 스텁은 저장소 밖에 있었고 지금은 없다.

**파일 시스템**: 이 Mac 의 APFS 는 이름을 **접는다** — 대소문자뿐 아니라 NFC/NFD, 심지어 `ß`↔`ss`
와 `ﬀ`↔`ff` 까지 같은 이름으로 본다(직접 확인). 그래서 충돌하는 두 파일을 **만들 수가 없다.**
`collect` 의 충돌 배선 테스트 둘은 `folding_filesystem()` 프로브로 그런 파일 시스템에서 skip 하고,
CI 의 `ubuntu-latest` 에서는 실제로 돈다. 확인을 위해 대소문자 구분 APFS 이미지를 붙여 참조 스텁과
함께 돌렸고 **`test_collect.py` 53건 전부 초록**이었다(스킵 0). 충돌 **규칙** 자체는 `collisions()`
순수 테스트가 잠근다.

## 잠근 API (`docs/m5e-workplan.md` §18 그대로)

- `core/artifacts.py` — 상수 `MAX_COMPONENT_BYTES=255` · `TAR_HEADER_BYTES=512` ·
  `TAR_TRAILER_BYTES=1024`, 상태 13개와 `ARTIFACT_STATES`·`GONE_STATES`·`PROGRESS_STATES`·
  `NOTHING_STATES`, `REASONS`(14개), `ArtifactError(Exception)` · `PolicyError(ValueError)`,
  frozen dataclass `ArtifactPolicy(globs, max_bytes, max_files, timeout_seconds,
  cancel_timeout_seconds)` + `enabled()` · `BundleFile(path, size, sha256, mode)` ·
  `AckDecision(status, purge, record, reason_code)`, 함수 `validate_globs` · `compile_globs` ·
  `check_path` · `collision_key` · `collisions` · `select` · `archive_allowance` · `classify` ·
  `ack_decision`(키워드 전용) · `expires_at`.
- `core/retention.py` 추가 — frozen `BundleInfo(job_id, state, expires_at, bytes)` ·
  `bundles_to_expire(bundles, now)`.
- `collect.py` — frozen `CollectResult(state, files, total_bytes, bundle_bytes, skipped_count,
  bundle_sha256, bundle_path, reason_code, reason_args, detail)` ·
  `open_anchored(root, relpath) -> int` ·
  `collect(workspace, policy, staging, *, now_fn=…, budget_seconds=None) -> CollectResult`.

## `tests/test_artifacts_rules.py` — I/O 없는 순수 규칙

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 상태 상수 13개가 공개 문자열 그대로 | `test_state_constants_are_the_public_strings` | §3 |
| 2 | `ARTIFACT_STATES` 가 정확히 그 13개, frozenset | `test_artifact_states_holds_the_thirteen_values` | §18 |
| 3 | `GONE`/`PROGRESS`/`NOTHING` 이 명세 값 · 서로 겹치지 않음 · 전부 `ARTIFACT_STATES` 안 | `test_routed_state_subsets_are_disjoint_and_inside_artifact_states` | §18 · §6 |
| 4 | 세 부분집합 밖에 남는 넷은 `ready`·`dropped`·`failed`·`unavailable` | `test_the_states_outside_the_three_subsets_are_the_four_that_carry_a_body_or_a_fault` | §6 표 |
| 5 | `REASONS` 가 §3 의 14개 | `test_reasons_holds_the_fourteen_codes` | §3 |
| 6 | 크기 상수 255 · 512 · 1024 | `test_size_constants_are_the_spec_values` | §18 |
| 7 | `PolicyError` 는 ValueError · `ArtifactError` 는 Exception | `test_error_types` | §18 |
| 8 | 정책 기본값 = 설정 기본값(1 GiB · 10000 · 60 · 5) | `test_policy_defaults_are_the_configuration_defaults` | §8 표 |
| 9 | 정책 필드 순서(위치 인자로 만들어도 같다) | `test_policy_field_order_is_globs_bytes_files_timeout_cancel` | §18 |
| 10 | `enabled()` 는 글롭이 비었는지만 본다 | `test_policy_enabled_follows_the_globs` | §18 |
| 11 | 정책은 frozen | `test_policy_is_frozen` | §18 |
| 12 | `BundleFile` 필드·순서·frozen | `test_bundle_file_fields_order_and_frozen` | §18 |
| 13 | `AckDecision` 필드·순서·frozen | `test_ack_decision_fields_order_and_frozen` | §18 |
| 14 | 받는 글롭 10건(명세 예시 둘 · 확장자만 · 리터럴만 · 닫힌 문자 클래스 · `?` · 한글) | `test_accepted_globs` | §4 |
| 15 | 거부하는 글롭 23건(절대 · `..` · 빈/`.` 조각 · 백슬래시 · NUL · `.git` · 전체 포괄 3 · 닫히지 않은 클래스 2 · 리터럴도 확장자도 없음 3) · 문구는 한 줄 짧게 | `test_rejected_globs` | §4 |
| 16 | 문자열이 아닌 항목은 `PolicyError` (×5) | `test_non_string_globs_are_rejected` | §4 |
| 17 | 튜플로, 준 순서 그대로 | `test_validate_globs_returns_a_tuple_in_the_given_order` | §18 |
| 18 | 빈 목록은 `()` — 기능이 꺼진다 | `test_validate_globs_accepts_an_empty_sequence` | §4 |
| 19 | 하나만 나빠도 목록 전체를 거부 | `test_one_bad_entry_rejects_the_whole_list` | §4 |
| 20 | **앵커** — `goldens/*.png` 가 `deep/goldens/a.png` 에 `match` 도 `search` 도 안 걸린다 | `test_compiled_globs_are_anchored_at_both_ends` | §4 (`snapshot.py:113` 함정) |
| 21 | `*` 는 `/` 를 넘지 않는다 | `test_compiled_star_does_not_cross_a_slash` | §4 |
| 22 | `**/` 는 디렉터리 0개 이상 — `test/goldens/a.png` 도 맞는다 | `test_compiled_double_star_slash_spans_zero_or_more_directories` | §4 예시 |
| 23 | 컴파일 결과는 튜플 · 개수·순서 보존 · `re.Pattern` | `test_compiled_globs_keep_order_and_count` | §18 |
| 24 | 빈 입력은 `()` | `test_compile_globs_of_nothing_is_nothing` | §18 |
| 25 | 문자 클래스와 `?` 가 한 글자씩만 | `test_compiled_character_class_and_question_mark` | §4 |
| 26 | 글롭의 `.` 은 리터럴 점이다(정규식 `.` 아님) | `test_a_dot_in_a_glob_is_a_literal_dot` | §4 |
| 27 | 받는 경로 13건(한글 · 이모지 · 공백 · `.gitignore` · `a.git/` · `..y` · 255자) | `test_accepted_paths` | §4 · `core/manifest.py` |
| 28 | 거부하는 경로 22건(절대 · `..` · `.` · 빈 조각 · 끝 `/` · 백슬래시 · NUL · `.git` 조각) | `test_rejected_paths` | §4 |
| 29 | 문자열이 아닌 경로 (×4) | `test_non_string_paths_are_rejected` | §4 |
| 30 | 경로를 **정규화하지 않는다** — NFD 로 온 것은 NFD 로 돌아온다 | `test_check_path_returns_the_path_untouched` | §4 (manifest 가정 1) |
| 31 | 조각 255바이트는 통과(`"가"×85` = 255바이트) | `test_a_component_at_the_byte_limit_is_accepted` | §4 새 규칙 |
| 32 | 256바이트 · `"가"×86`(258바이트)는 거부 | `test_a_component_over_the_byte_limit_is_rejected` | §4 |
| 33 | **한글 100자 = 300바이트 → 거부** (글자 수가 아니라 UTF-8 바이트다) | `test_a_hundred_character_korean_component_is_over_the_byte_limit` | §4 |
| 34 | 마지막 조각만이 아니라 **모든 조각**을 잰다 | `test_every_component_is_measured_not_only_the_last` | §4 |
| 35 | 전체 4096자 상한은 그대로 물려받는다 | `test_the_whole_path_is_still_capped_at_4096` | §4 · `MAX_PATH_LEN` |
| 36 | 대소문자를 접는다 · `A/B` → `a/b` | `test_collision_key_folds_case` | §4 |
| 37 | `lower()` 가 아니라 `casefold()` — `straße` ≡ `STRASSE` | `test_collision_key_uses_casefold_not_lower` | §4 이식성 정책 |
| 38 | NFC 로 먼저 정규화 — 한글 NFC/NFD, `café`/`café` 가 같은 키 | `test_collision_key_normalises_to_nfc_first` | §4 |
| 39 | 진짜 다른 경로는 다른 키 | `test_collision_key_keeps_genuinely_different_paths_apart` | §4 |
| 40 | 평범한 경로들은 충돌 없음(거짓 양성 없음) | `test_plain_distinct_paths_do_not_collide` | §4 |
| 41 | 접두를 공유하는 형제 이름은 충돌이 아니다(`a`/`ab`, `dir`/`directory/x`) | `test_sibling_names_that_share_a_prefix_do_not_collide` | §4 |
| 42 | 대소문자만 다른 둘은 충돌 | `test_a_case_only_difference_collides` | §4 |
| 43 | 정규화만 다른 둘은 충돌 | `test_a_normalisation_only_difference_collides` | §4 |
| 44 | **디렉터리 접두** — `A` 와 `a/b.png` · `a` 와 `a/b.png` · `x/Y` 와 `X/y/z.png` · 깊은 것도 | `test_a_directory_prefix_collides` | §4 |
| 45 | 같은 경로가 두 번 오면 충돌 | `test_the_same_path_twice_collides` | §4 |
| 46 | 결과가 정렬돼 있다 | `test_the_result_is_sorted` | §18 |
| 47 | 어떤 이터러블이든 받고 **원래 경로**를 돌려준다(키가 아니라) | `test_collisions_take_any_iterable_and_report_the_original_paths` | §18 |
| 48 | 0개·1개는 충돌 없음 | `test_nothing_and_one_path_never_collide` | §4 |
| 49 | 선택은 정렬되고 글롭은 앵커다(`sub/c.png` 는 `*.png` 에 안 맞는다) | `test_select_is_sorted_and_anchored` | §18 |
| 50 | 글롭 여럿의 합집합, 중복 없음 | `test_select_unions_globs_without_duplicates` | §4 |
| 51 | **`.git` 조각은 글롭이 뭐라 하든 제외** · `.gitignore.png`·`a.git/x.png` 는 남는다 | `test_select_drops_dot_git_whatever_the_glob_says` | §4 |
| 52 | 글롭이 없으면 아무것도 안 고른다 | `test_select_with_no_globs_selects_nothing` | §4 |
| 53 | 입력 순서와 무관 · 어떤 이터러블이든 | `test_select_is_order_independent_and_takes_any_iterable` | §18 |
| 54 | 같은 경로가 두 번 와도 하나 | `test_select_deduplicates_a_repeated_path` | §18 |
| 55 | 빈 입력은 빈 목록 | `test_select_of_nothing_is_nothing` | §18 |
| 56 | 허용치 = `max_bytes + files×512 + 1024`(0·경계·기본값) | `test_archive_allowance_is_max_bytes_plus_a_header_per_file_plus_a_trailer` | §8 회계 |
| 57 | 잠근 상수로 계산한다(하드코딩된 512/1024 아님) | `test_archive_allowance_uses_the_locked_constants` | §18 |
| 58 | **원본 상한과 딱 같은 묶음**이 헤더 때문에 거절되지 않는다(헤더+데이터+트레일러가 허용치 안) | `test_a_bundle_at_the_raw_limit_is_not_refused_by_the_archive_side_check` | §8 · §15 ⑤ |
| 59 | 진짜 `tarfile` 은 레코드(10240)까지 패딩한다는 사실을 잠근다 | `test_a_real_tar_is_padded_to_a_full_record` | 아래 애매점 ③ |
| 60 | 허용치는 언제나 원본 상한보다 크다(아카이브 검사가 더 엄해지지 않는다) | `test_the_allowance_is_always_looser_than_the_raw_limit` | §8 |
| 61 | **§11 표 10줄 전부** — `new` · 기준선 없음+로컬 있음 → `conflicted` · `unchanged` · `changed` · 손댄 것 → `conflicted` · **로컬이 지워진 것 → `conflicted`** | `test_classify_table` | §11 표 |
| 62 | 판정은 네 값뿐이고 넷 다 나온다 | `test_classify_only_ever_returns_the_four_verdicts` | §11 |
| 63 | 바이트로만 비교한다(시각·픽셀 아님) | `test_classify_compares_bytes_only` | §11 |
| 64 | **`join_count == 0` + 요청자 → 200 · purge · record · `acked`** | `test_ack_by_the_requester_of_a_job_nobody_joined_purges_at_once` | §7 · 결정 40 |
| 65 | `join_count ≥ 1` → 200 · **안 지운다** · record (×3) | `test_ack_on_a_joined_job_is_accepted_but_keeps_the_bundle` | §7 · 결정 40 |
| 66 | **요청자도 합류자도 아닌 admin → no-op**(`record=False`, `purge=False`, 200), `join_count` 무관 (×3) | `test_an_admin_who_is_neither_requester_nor_joiner_is_a_no_op` | §7 |
| 67 | 해시 불일치 → 409, 기록도 삭제도 없음 (×2) | `test_a_hash_mismatch_is_409` | §6 표 |
| 68 | 저장된 해시가 없으면 맞을 수 없다 → 409 | `test_a_missing_stored_hash_cannot_match` | §6 표 |
| 69 | `ready`/`purged`/`expired` 가 아닌 상태 10개 → 409 | `test_a_state_that_is_neither_ready_nor_purged_is_409` | §6 표 |
| 70 | **재생** — `purged` 뒤 같은 해시 → 200, 다시 지우지 않고 묘비의 `acked_at` 도 안 덮는다 | `test_a_replay_after_purge_with_the_same_hash_is_200` | §7 재생 |
| 71 | 재생은 `join_count` 와 무관 (×2) | `test_a_replay_after_purge_is_200_whatever_the_join_count` | §7 |
| 72 | `purged` 뒤 다른 해시 → 409 | `test_a_replay_after_purge_with_a_different_hash_is_409` | §7 |
| 73 | 만료 → 410, 절대 안 지운다 (×4: owner × join_count) | `test_an_expired_bundle_is_410_and_never_purges` | §6 표 |
| 74 | **`expired` 상태도 410**(409 아님) | `test_the_expired_state_is_410_too_not_409` | 아래 애매점 ① |
| 75 | 상태는 200·409·410 뿐 · `purge` 면 반드시 `record` | `test_every_decision_is_one_of_the_three_statuses` | §7 |
| 76 | 수용·만료 분기의 `reason_code` 는 `REASONS` 안이거나 None | `test_the_reason_code_of_an_accepted_or_expired_decision_is_a_known_code` | 결정 37 |
| 77 | `ack_decision` 은 키워드 전용 | `test_ack_decision_is_keyword_only` | §18 |
| 78 | `expires_at(ready_at, 24)` = +24시간 = 하루 | `test_expires_at_adds_the_hours` | §8 |
| 79 | 0시간은 같은 순간 | `test_expires_at_of_zero_hours_is_the_same_instant` | §8 |
| 80 | 타임존이 그대로 남는다 | `test_expires_at_keeps_the_timezone` | §8 |
| 81 | **발행 시각 기준**이지 벽시계가 아니다(읽어도 안 늘어난다) | `test_expires_at_is_measured_from_ready_at_not_from_the_wall_clock` | §8 |

## `tests/test_collect.py` — 워커 쪽 I/O (`tmp_path`, sleep 없음)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 평범한 파일이 열리고 내용이 읽힌다 | `test_open_anchored_opens_a_plain_file` | §18 |
| 2 | 뿌리의 파일도 열린다 | `test_open_anchored_opens_a_file_at_the_root` | §18 |
| 3 | **부모 조각이 심링크면 거부** — 평범하게 열면 열리는 것을 확인한 뒤 거부를 본다 | `test_open_anchored_refuses_a_symlinked_parent_component` | §4 「여는 방법」 |
| 4 | 두 단계 위 부모가 심링크여도 거부 | `test_open_anchored_refuses_a_symlinked_parent_two_levels_up` | §4 |
| 5 | 마지막 조각이 심링크면 거부 | `test_open_anchored_refuses_a_symlinked_final_component` | §4 |
| 6 | **안을 가리키는 심링크도 거부** — 경계는 「링크가 없다」지 「안 나간다」가 아니다 | `test_open_anchored_refuses_a_symlink_even_when_it_stays_inside` | §4 |
| 7 | 밖을 가리키는 심링크 거부 | `test_open_anchored_refuses_a_symlink_pointing_outside` | §4 |
| 8 | 끊어진 심링크 거부 | `test_open_anchored_refuses_a_dangling_symlink` | §4 |
| 9 | 뿌리를 벗어나거나 정규형이 아닌 경로 11건(`..` · 절대 · 빈 · `.` · `//` · 백슬래시 · NUL) | `test_open_anchored_refuses_a_path_that_leaves_the_root_or_is_not_normal` | §4 (예외 종류는 애매점 ⑤) |
| 10 | 디렉터리는 일반 파일이 아니다 → 거부 | `test_open_anchored_refuses_a_directory` | §18 |
| 11 | 없는 파일은 열리지 않는다 | `test_open_anchored_on_a_missing_file_does_not_succeed` | §18 |
| 12 | 글롭에 맞는 것만 모은다 · 정렬 · `total_bytes` · `skipped_count == 0` | `test_collect_takes_only_the_glob_matches` | §4 · §18 |
| 13 | 크기·sha256·모드(0o600 → 644, 0o700 → 755) | `test_collect_records_size_hash_and_normalised_mode` | §18 |
| 14 | 묶음은 **스테이징 안에만** 쓴다 · 워크스페이스는 그대로 · `bundle_sha256` = 아카이브 바이트의 해시 | `test_collect_writes_into_staging_and_leaves_the_workspace_alone` | §3 · §5 |
| 15 | **`.git` 은 글롭이 뭐라 하든 안 모은다** | `test_dot_git_is_never_collected_whatever_the_glob_says` | §4 |
| 16 | 심링크된 디렉터리 안으로 내려가지 않는다(밖의 PNG 가 안 새어 들어온다) | `test_a_symlinked_directory_is_not_followed` | §4 `followlinks=False` |
| 17 | **심링크 · 하드링크(`st_nlink > 1`) · FIFO 는 건너뛰고 센다** → `skipped_count == 3` | `test_symlinks_hardlinks_and_fifos_are_skipped_and_counted` | §4 |
| 18 | `skipped_count` 는 **글롭에 맞은** 것만 센다(안 맞은 링크·FIFO 는 안 센다) | `test_skipped_count_only_counts_entries_that_matched_a_glob` | §4 |
| 19 | 전부 건너뛰었으면 `empty`, `ready` 가 아니다 · 센 수는 남는다 | `test_a_bundle_of_only_skipped_entries_is_empty_not_ready` | §3 (애매점 ④) |
| 20 | tar 에는 **일반 파일만** · 이름 정렬 · 모드 644/755 · 디렉터리·링크 멤버 없음 | `test_the_written_tar_holds_regular_files_only_in_sorted_order` | §3 · §18 |
| 21 | tar 멤버가 매니페스트와 정확히 같다(이름·크기) | `test_the_tar_members_match_the_manifest_exactly` | §3 |
| 22 | **같은 입력이면 같은 해시** — 스테이징을 바꿔 두 번 돌려도 바이트까지 같다 | `test_bundle_sha256_is_stable_across_two_runs_of_the_same_input` | §3 불변 묶음 |
| 23 | 내용이 바뀌면 해시가 바뀐다 | `test_different_content_gives_a_different_bundle_hash` | §3 |
| 24 | 바이트 상한 초과 → `dropped`+`over_bytes` · `{limit, seen}` 수치만 · 묶음 안 씀 | `test_over_max_bytes_is_dropped_with_over_bytes` | §3 · §8 |
| 25 | 상한과 **정확히 같으면** 통과 | `test_max_bytes_exactly_at_the_limit_is_not_over` | §8 경계 |
| 26 | 파일 수 초과 → `dropped`+`over_files` | `test_over_max_files_is_dropped_with_over_files` | §8 |
| 27 | 파일 수가 상한과 같으면 통과 | `test_max_files_exactly_at_the_limit_is_not_over` | §8 경계 |
| 28 | 두 상한은 따로 건다(파일 수는 안 넘고 바이트만 넘으면 `over_bytes`) | `test_the_two_limits_are_counted_separately` | §8 |
| 29 | **예산 초과 → `dropped`+`timed_out`** · 시계를 걷는 도중에 다시 본다(`calls >= 2`) | `test_a_blown_budget_is_dropped_and_timed_out` | §5 |
| 30 | `budget_seconds` 가 `policy.timeout_seconds` 를 이긴다 | `test_budget_seconds_overrides_the_policy_timeout` | §18 |
| 31 | 예산 안이면 평소대로 모은다 | `test_a_budget_that_is_not_blown_collects_normally` | §5 |
| 32 | 인자가 없으면 예산은 `policy.timeout_seconds`(1·5·60초 ×3) — 60 이 박혀 있으면 안 된다 | `test_the_policy_timeout_is_the_budget_when_none_is_passed` | §8 |
| 33 | 워크스페이스가 없으면 `skipped`+`not_run` | `test_a_missing_workspace_is_skipped_and_not_run` | §5 자재화 실패 |
| 34 | 맞는 것이 없으면 `empty`+`no_match`(`ready` 도 `unknown` 도 아니다) | `test_no_match_is_empty_not_ready` | §3 |
| 35 | 빈 워크스페이스는 `empty` 지 `skipped` 가 아니다 | `test_an_empty_workspace_is_empty_not_skipped` | §3 |
| 36 | 정규화 충돌 → `dropped`+`path_conflict`, **묶음째** 버린다 · 경로가 `reason_args` 에 없다 | `test_a_normalisation_collision_drops_the_whole_bundle` | §4 · §3 |
| 37 | 디렉터리 접두 충돌도 묶음째 버린다 | `test_a_directory_prefix_collision_drops_the_whole_bundle` | §4 |
| 38 | **`reason_args` 에 경로가 절대 안 들어간다** · 값은 전부 수치 | `test_reason_args_never_carry_a_path` | §3 결정 37 |
| 39 | `detail` 은 경로를 담아도 되고, 둘 다 JSON 으로 직렬화된다(`detail_json`) | `test_detail_may_carry_paths_and_both_fields_survive_json` | §3 · §9 |
| 40 | `CollectResult` 기본값과 frozen | `test_collect_result_defaults_and_frozen` | §18 |
| 41 | 돌려주는 것은 `CollectResult` · `files` 는 `BundleFile` 튜플 · `bundle_path` 는 `Path` | `test_collect_returns_a_collect_result_with_a_tuple_of_bundle_files` | §18 |

## `tests/test_retention_bundles.py` — `bundles_to_expire`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | **`expires_at is None` 인 행은 절대 안 나온다** | `test_rows_without_an_expiry_are_never_returned` | §18 |
| 2 | 10년을 밀어도 안 나온다 — 24시간 약속의 유일한 보루 | `test_rows_without_an_expiry_stay_out_even_a_decade_later` | §8 M3 경계 |
| 3 | 만료 없는 행 옆의 만료된 행은 나온다 | `test_an_expiring_row_next_to_a_row_without_one` | §18 |
| 4 | `ready` 인데 만료가 지났으면 나온다 · **같은 객체**를 돌려준다 | `test_a_ready_row_past_its_expiry_is_returned` | §8 |
| 5 | 경계 `<=` — 정확히 만료 시각이면 나온다 | `test_exactly_at_the_expiry_is_due` | §18 |
| 6 | 1초 지났으면 나온다 | `test_one_second_past_the_expiry_is_due` | §18 |
| 7 | 1초 전이면 안 나온다 | `test_one_second_before_the_expiry_is_not_due` | §18 |
| 8 | TTL 안(23시간)이면 안 나온다 | `test_a_bundle_inside_its_ttl_is_not_due` | §8 |
| 9 | 이미 `purged`·`expired` 인 행은 다시 안 가져간다 (×2) | `test_already_gone_rows_are_not_returned_again` | §7 · §8 |
| 10 | 사라진 행을 건너뛰고 옆의 것은 가져간다 | `test_gone_rows_are_skipped_while_their_neighbours_are_taken` | §8 |
| 11 | finish 없는 업로드 영수증도 sweep 이 가져간다 | `test_an_upload_receipt_without_a_finish_is_swept` | §9 ⑤ |
| 12 | `job_id` 오름차순 | `test_output_is_sorted_by_job_id` | §18 |
| 13 | 만료 시각순이 아니라 `job_id` 순 | `test_output_is_sorted_by_job_id_not_by_expiry` | §18 |
| 14 | `now` 가 기준이지 벽시계가 아니다 | `test_now_is_the_reference_not_the_wall_clock` | §18 |
| 15 | 빈 입력은 빈 목록 | `test_empty_input_gives_an_empty_list` | §18 |
| 16 | 어떤 이터러블이든 받는다 | `test_any_iterable_is_accepted` | §18 |
| 17 | `BundleInfo` 필드·순서·frozen(`bytes` 이름 그대로) | `test_bundle_info_fields_order_and_frozen` | §18 |
| 18 | `expires_at` 은 `None` 일 수 있다 | `test_bundle_info_expiry_may_be_none` | §18 |

## 명세에서 애매했던 것 — 안전한 쪽으로 골랐다

> **①②③④⑥⑨⑩⑪⑬ 은 명세에 반영됐다**(`docs/m5e-workplan.md`, 2026-09-08). 아카이브 허용치는
> `max_bytes + files×1024 + 10240` 으로, 앵커는 `re.fullmatch` 로, 예산은 monotonic `clock` 으로,
> 「맞았지만 전부 건너뜀」은 `empty` + `reason_code None` 으로, ack 의 409 는 `hash_mismatch`·
> `not_ready` 로, 검사 순서는 만료 → 자격 → 해시 → 상태로, `open_anchored` 는 `O_NONBLOCK` 으로.
> 이 문서의 아래 기록은 **그때 무엇이 비어 있었는지**의 기록이고, 정본은 명세다.

명세가 정하지 않은 곳은 **데이터를 잃지 않고 경로를 드러내지 않는 쪽**으로 잡았다. 오너가
`docs/m5e-workplan.md` 에 접어 넣을 것들이다.

① **`state == "expired"` 인 ack 는 409 인가 410 인가.** §6 표는 「409 … `ready`/`purged` 가 아님 ·
410 만료」라고만 한다. `expired` **상태**도 「만료」이므로 **410** 으로 잡았다(`expired` 인자와
같은 답). 409 로 답하면 클라이언트가 「해시가 틀렸나」로 오해한다.
→ 잠근 곳: `test_the_expired_state_is_410_too_not_409`, 409 파라미터에서 `EXPIRED` 를 뺐다.

② **`ack_decision` 의 검사 순서(만료 + 해시 불일치가 동시에 성립할 때).** 아무 테스트도 걸지
않았다 — 명세가 정할 것이다. 「만료가 먼저」와 「해시가 먼저」 둘 다 말이 되고, 정하는 순간
`reason_code` 가 달라진다.

③ **아카이브 허용치가 tar 의 레코드 패딩을 안 덮는다.** 식은 `max_bytes + files×512 + 1024` 인데
파이썬 `tarfile` 은 파일을 닫을 때 **레코드(20 × 512 = 10240)** 까지 0 으로 채운다. 원본 3584
바이트짜리 묶음의 실제 아카이브는 10240 바이트여서 허용치(6144)를 훌쩍 넘는다. 파일마다 붙는
패딩(0~511바이트)도 헤더 몫 512 안에 같이 들어가 있어 작은 파일이 많으면 또 모자란다.
→ 식은 **손대지 않고** 명세대로 잠갔고(`…is_max_bytes_plus_a_header_per_file_plus_a_trailer`),
경계 테스트는 「헤더+데이터+트레일러」가 허용치 안이라는 것만 본다. 패딩 사실은
`test_a_real_tar_is_padded_to_a_full_record` 로 따로 잠가 뒀다. **§8 이 `+ RECORDSIZE` 를 더하든,
파일당 몫을 512 대신 1024 로 올리든 정해야 한다.**

④ **글롭에 맞았지만 일반 파일이 아니어서 전부 건너뛴 경우의 이유 코드.** 상태는 `empty` 가 맞다
(내보낼 것이 없다). 이유 코드는 `REASONS` 에 맞는 것이 `no_match` 뿐이라 그것으로 잡았다 —
「맞긴 맞았다」와는 어긋난다. 새 코드를 만들거나 §3 의 `empty` 설명을 넓히면 된다.
→ 잠근 곳: `test_a_bundle_of_only_skipped_entries_is_empty_not_ready`.

⑤ **`open_anchored` 가 나쁜 `relpath` 에 던지는 예외 종류.** §18 은 「심링크면 `ArtifactError` ·
일반 파일이 아니면 `ArtifactError`」만 못 박았다. `..`·절대 경로는 `check_path` 를 그냥 통과시키면
`PolicyError` 다. **거절 자체만** 잠그고 종류는 `(ArtifactError, PolicyError)` 로 열어 뒀다.

⑥ **`now_fn` 이 무엇을 돌려주는가.** `float`(monotonic)일 수도 `datetime` 일 수도 있다. 이
저장소에서 `now_fn` 은 언제나 `datetime` 이고(`server.py:188` · `notify.py:97` ·
`hostsample.py:104`), `clock` 이라는 이름일 때만 `time.monotonic` 이다(`client.py:800` ·
`cli.py:67`). 그래서 **`datetime` 을 돌려주는 함수**로 잡았다. 예산 재기에는 monotonic 이 더
맞으므로, monotonic 을 쓰려면 §18 의 인자 이름을 `clock` 으로 바꾸는 편이 낫다.

⑦ **버린 묶음(`dropped`)의 `CollectResult.files`.** 비운다(`()`)로 잡았다 — 그 목록이 곧 남의
트리 구조이고 매니페스트를 거쳐 공개 라우트로 흐른다. 경로가 필요하면 `detail` 에 담는다.
→ 잠근 곳: `over_bytes` · `over_files` · `path_conflict` · `timed_out` 테스트 전부.

⑧ **`classify(기준선 있음, 로컬 = 받은 것, 기준선 ≠ 로컬)`.** §11 표의 3번 줄(`unchanged`)과 5번
줄(`conflicted`)이 둘 다 맞는다. **표 순서대로 먼저 맞는 줄이 이긴다** → `unchanged`. 어느 쪽이든
쓰지 않으므로 데이터는 안 잃고, 「이미 그 내용이다」가 더 정직하다.
→ 잠근 곳: `test_classify_table` 의 `(SHA_BASE, SHA_IN, SHA_IN)` 줄.

⑨ **`ack_decision` 의 409 분기 `reason_code`.** `REASONS` 14개에 「해시 불일치」도 「상태가 틀림」도
없다. 그래서 409 의 `reason_code` 는 잠그지 않았고, 200·410 분기만 「`REASONS` 안이거나 None」으로
확인한다. 화면(§13)이 문장을 붙이려면 코드가 필요하다 — §3 에 둘을 더할지 정해야 한다.

⑩ **`bundles_to_expire` 가 `ready`·`uploading` 말고 어떤 상태를 가져가는가.** 명세는 「`expires_at`
이 있고 `<= now` 인 것만」이라 문면상 `collecting`·`failed`·`dropped`·`empty`·`unavailable` 도
대상이다. 근거가 있는 둘(`ready` — §8 sweep, `uploading` — §9 ⑤)만 잠갔고 나머지는 열어 뒀다.
`purged`·`expired` 는 제외로 잠갔다.

⑪ **컴파일한 글롭의 뒤 앵커가 `$` 다.** 파이썬의 `$` 는 **끝의 개행 앞**에서도 맞는다 —
`goldens/evil.png\n` 이라는 (POSIX 에서 가능한) 이름이 `goldens/*.png` 에 걸린다. §18 문면이
`^…$` 라 그대로 잠갔지만, `\Z` 나 `fullmatch` 가 더 안전하다.

⑫ **`build/outputs/**` 같은 「확장자 없는 리터럴 접두」 글롭.** §4 는 전체 포괄 세 개(`**`·`*`·
`**/*`)만 막고 「리터럴 조각이나 확장자가 최소 하나」를 요구하므로 **받는 쪽**으로 잡았다. 서브트리
하나를 통째로 돌려보내는 셈이라 막고 싶다면 §4 에 한 줄이 더 필요하다.
→ 잠근 곳: `GOOD_GLOBS` 의 `"build/outputs/**"`.

⑬ **`open_anchored` 가 FIFO 를 열 때 막힐 수 있다.** `os.open(fifo, O_RDONLY)` 는 쓰는 쪽이 열릴
때까지 **블록한다** — `O_NONBLOCK` 없이 열면 워커가 그 자리에 선다. 테스트 스위트를 멈추게 만들 수
없어서 `open_anchored` 쪽 FIFO 테스트는 **쓰지 않았고**, `collect` 쪽에서 `os.lstat` 으로 먼저
걸러 내는 경로만 확인한다(`test_symlinks_hardlinks_and_fifos_are_skipped_and_counted`).
**구현은 `O_NONBLOCK` 을 반드시 켜야 한다.**

## 범위 밖(역할 B 또는 뒤 PR)

라우트·상태 코드(§6) · 스토어와 스키마 7(§9) · 예약·전체 회계(§8) · 워커 두 경로(§5) ·
클라이언트 `apply.py`·저널·`--resume`(§11) · CLI(§12) · 화면(§13) · 설정 검증(`config.py` 가
`validate_globs` 를 부르는지) · `retention_days_success = 0` 에서도 24시간이 지켜지는지(§15 ⑤,
janitor 배선이라 통합 테스트다).
