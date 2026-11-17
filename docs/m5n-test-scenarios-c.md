# M5n 테스트 시나리오 C — 사람에게 닿는 것 (2026-11-16)

`docs/m5n-workplan.md` 의 **§2**(S1~S4) · **§5.3**(제출 응답과 `rcm run` 의 한 줄) · **§7.3**(새 이유와
행동 가능한 순서) · **§8.2**(CLI) · **§8.3**(웹) · **§9**(워커 붙이기) · **§10**(예시) · **§12**(fail-open
금지)를 테스트로 옮긴 것이다. 한 줄 요약: **서버가 보낸 판정을 사람이 읽는 글자로 옮기되, 모르는 것은
지어내지 않는다.** 그리고 새 컴퓨터를 붙이는 길(`token add --worker` → `init worker` → `worker --check` →
`--service-file`)이 끝까지 이어진다.

이 문서가 **안 건드리는 것**: 판정 표 · 지문 정규화 · 큐 전역 계산 · 설정 검증(역할 A),
저장소 · claim · `/worker/*` 프로토콜 · 자동 점검 스위퍼 · `/api/machines` 를 **만드는** 일(역할 B).
여기서는 그 문서들이 **이미 왔다고 보고** 그리기만 잠근다. 문서 모양은 역할 B 의 가정(B-14 · B-15 ·
B-16 · B-18)에 맞췄다.

`src/` 는 한 글자도 안 건드렸고 다른 역할의 문서·테스트도 안 건드렸다.

## 테스트 파일

| 파일 | 수 | 무엇 |
|---|---|---|
| `tests/test_cli_m5n.py` | 50 | `rcm machines` · `rcm check` 의 `machines`·`placement` · `rcm recheck` · `rcm run --machine` 과 S4 한 줄 · `rcm jobs` |
| `tests/test_render_m5n.py` | 19 | `rcm top` 의 대기 행 이유 줄(순수 `render_text.render`) · 행동 가능한 순서 |
| `tests/test_cli_init_worker.py` | 31 | 템플릿 잠금 · `rcm init worker` · `--service-file` · 예시 서비스 파일 · `token add --worker` 안내 |
| `tests/test_worker_discovery.py` | 26 | 워커의 서버 찾기 · 5번 실패 뒤 다시 찾기 · `rcm worker --check` 의 `fingerprint`·`presets` |
| `tests/web/m5n.test.js` | 45 | 웹 순수 함수(이유 · 칩 · 머신 표 · 펼친 행) |
| `tests/test_web_layout_m5n.py` | 3 | 390px · 1240px × ko · en 가로 넘침 · 접힌 머신 표 · 칩과 새 이유가 화면에 |
| `tests/test_docs_m5n.py` | 27 | 문서 · 예시 문면 잠금 |
| `tests/fake_rcm_http.py` | — | 공용 도우미(가짜 서버). `test_` 로 안 시작해 수집되지 않는다 |
| `tests/web/fixtures/machines-m5n.json` | — | 웹 · 배치 시험이 같이 쓰는 `/api/machines` 문서 |

작성 시점(구현 전) 결과: 파이썬 156 중 **135 빨강 · 21 초록**, node 45 중 **43 빨강 · 2 초록**. 초록은
회귀 방어(옛 서버 · 고정 서버 · pool 행이 그대로인지)와, 리드가 먼저 넣은 `core/model.py` 의 새 이유 상수
셋이다. 빨강의 이유는 「모르는 부명령/플래그」(`machines` · `recheck` · `init worker` · `--machine` ·
`--service-file`), 「행이 없다」(`rcm check`·`worker --check`), 「이유 줄이 코드 그대로」(`no_capable_machine`),
「함수가 없다」(node), 「`#machines` 가 없어 page not ready」(배치), 「문서에 문구가 없다」(docs)다.
argparse 오류로 초록이 되지 않게 사용 오류 시험은 `invalid choice`·`unrecognized arguments` 가 없는지도 본다.

## 공통 — 픽스처와 규칙

**가짜 서버.** `fake_rcm_http.FakeRcm` 은 127.0.0.1 임시 포트의 stdlib HTTP 서버다. `(METHOD, 경로)` →
`(상태, JSON)` 을 정해 두고 모든 요청을 (method · path · body · headers) 로 남긴다. 정해 두지 않은 경로는
404(옛 서버 흉내가 기본). 경로는 퍼센트 디코딩 뒤 맞춘다(`/machines/%40local/recheck` == `@local`).
`static_dir` 을 주면 진짜 서버와 같은 배치(`/` → index.html, `/static/<f>`)로 웹 파일을 내준다. 운영
8787 과 무관하다.

**세 머신(§2 S1, 운영 모양).** `@local`(이름표 `Mac mini` · 풀 default · 레인 2) · `mac2-worker`(풀
mac2 · 레인 1 · **같은 호스트** `epeuem-…(a1f3)`) · `mbp`(풀 mbp · 호스트 `mbp (7c20)`). 프로브 둘:
`xcode`, `flutter`. Mac mini 는 한 시간 전에 `Xcode 26.5 · Build version 26F75` → `Xcode 27.2 · Build
version 27B52` 로 바뀌었고 `sim-check #3620` 이 실패했다(S3). mbp 는 `flutter` 가 missing, 도구 `fvm`
이 없다. 프리셋 넷: `gate`(any-capable) · `scenario-qa`(pool default · `checks = ["sim-check"]`) ·
`sim-check`(점검, `sample = false`) · `lint`. `checks[]` 는 점검 프리셋마다 하나(B-14):
failed #3620 · passed #3611 · needed.

**시각.** CLI 의 `rcm check` 는 진짜 시계로 24시간 창을 재므로 파이썬 픽스처는 「지금 − 1시간」으로 만든다.
웹 순수 함수는 `helpers.NOW`(2026-09-04 00:52:12Z) 기준이다. 배치 시험은 픽스처의 모든 UTC 시각을
「브라우저의 지금」으로 민다(`shifted`) — 안 밀면 「한 시간 전」이 두 달 전이 되어 칩이 안 뜬다.

**표 칸.** `rcm machines` 표는 두 칸 이상의 공백으로 나눈다. `rcm check` 행은 기존 모양
`{ok |FAIL|warn}  {name:<13} {detail}`. stderr 줄의 `rcm: ` 접두는 잠그지 않는다.

**워커 쪽.** `cli.discover` 는 `test_cli_discover.FakeDiscover` 로 갈아 끼운다. `RemoteWorker` 의 재시도
간격은 `REGISTER_RETRY_SECONDS = 0` 으로 줄이고, 클라이언트는 「처음 n 번 연결 실패」 대역(`Flaky`)이다.
`cmd_worker` 배선은 `remote_worker.RemoteWorker` 를 기록기로 바꿔 생성 인자만 본다(시그널 처리기는
시험 뒤 되돌린다). `--check` 의 측정은 tmp/bin 의 가짜 도구 · 버전 스크립트와 절대 항목만 있는 PATH 로 한다.

**init worker.** HOME 을 tmp 로 옮기고 `socket.gethostname` 과 `platform.node` 를 둘 다 `mbp.local` 로
바꾼다. `--service-file` 은 `sys.platform` 을 darwin · linux · win32 로 바꿔 잰다. `subprocess.Popen` ·
`os.system` 을 감시해 `launchctl` · `systemctl` 을 부르면 실패다.

## 베스트 케이스

| id | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| B1 | `rcm machines` (S1) | 머리줄 `machine · host · pool · lanes · fingerprint · presets`, 행 `Mac mini · epeuem-…(a1f3) · default · 2 · xcode 27.2 · flutter 3.47.8 · 3/4 ok · 1 sim-check failed #3620` / `mac2-worker … · 3/4 ok · 1 need sim-check` / `mbp · mbp (7c20) · mbp · 1 · xcode 26.5 · flutter missing · 1/4 ok · 3 fvm missing` | `test_machines_prints_the_s1_table_with_one_row_per_machine` |
| B2 | 같은 호스트의 워커 둘 | 호스트 칸이 같다 | `test_two_workers_on_one_host_show_the_same_host_label` |
| B3 | 읽기 명령 | `GET /api/machines` 만, POST 없음 · 토큰 없이도 | `test_machines_reads_only_the_machines_route` · `test_machines_works_without_a_token` |
| B4 | `--json` | 서버 문서 그대로 | `test_machines_json_passes_the_server_document_through` |
| B5 | `--machine mbp` | 프리셋마다 `preset  state  code  짧은 사유` 한 줄 | `test_machine_flag_lists_every_preset_verdict_with_code_and_detail` |
| B6 | `--machine @local` | 프로브 값 전부와 앞 값 · 점검 #3620 | `test_machine_flag_shows_full_probe_values_and_the_previous_one` |
| B7 | `--machine mbp --json` | 그 머신 항목 하나 | `test_machine_flag_json_prints_only_that_machine` |
| C1 | `rcm check` (S3) | `FAIL  machines  3 machines · 1 environment changed (Mac mini: xcode 26.5 → 27.2) · 1 check failed (Mac mini: sim-check #3620)` · 종료 1 | `test_check_machines_row_fails_naming_the_change_and_the_failed_check` |
| C2 | 바뀌었지만 점검 통과 | `ok  machines  3 machines · 1 environment changed (Mac mini: xcode 26.5 → 27.2)` | `test_check_machines_row_is_ok_when_the_change_passed_its_check` |
| C3 | `placement` 행 | `warn  placement  gate: any machine (2 eligible) · scenario-qa: pool default (check failed)` | `test_check_placement_row_names_each_placed_preset` |
| C4 | 모두 맞는 머신이 있다 | `ok … scenario-qa: pool default (1 eligible)` | `test_check_placement_row_is_ok_when_every_placed_preset_has_a_machine` |
| D1 | `rcm recheck @local sim-check` | `POST /machines/@local/recheck` 본문 `{"checks": ["sim-check"]}` · stdout 에 `#3650` · `sim-check` | `test_recheck_posts_the_machine_and_named_checks_and_prints_the_jobs` |
| D2 | `rcm recheck mbp` | 본문에 `checks` 가 없거나 `[]` | `test_recheck_without_check_names_asks_for_every_check` |
| E1 | S4 — 맞는 머신 없음 | stderr 한 줄 `gate: no capable machine — mbp: fvm missing · mac2-worker: down` (정확히 한 번) · 잡은 섰고 종료 0 | `test_run_prints_one_line_when_no_machine_can_take_the_job` |
| E2 | 기다리는 `rcm run` | 제출 직후 같은 줄, 그 뒤 대기 | `test_run_line_also_prints_when_waiting_without_no_wait` |
| E3 | `rcm run sim-check --machine mbp` | `POST /jobs` 본문 `machine: "mbp"` | `test_run_machine_flag_sends_the_machine_in_the_body` · `test_run_help_lists_machine` |
| F1 | `rcm jobs` · any-capable 대기 | 행에 `any machine` | `test_jobs_shows_any_machine_on_an_any_capable_waiting_row` |
| F2 | `rcm jobs` · 맞는 머신 없음 | 행에 `no capable machine — mbp: fvm missing · mac2-worker: down` | `test_jobs_shows_the_no_capable_machine_reason_with_each_machine` |
| T1 | `rcm top` · any-capable 대기 | 이유 줄 `waiting for lane · any machine` | `test_an_any_capable_waiting_row_says_any_machine` |
| T2 | `rcm top` · S4 | 이유 줄 `no capable machine — mbp: fvm missing · mac2-worker: down` | `test_no_capable_machine_lists_each_machine_with_its_reason` |
| T3 | `rcm top` · 점검 | `check failed — Mac mini: sim-check failed #3620` · `check pending — Mac mini: sim-check running` · `check pending — mbp: need sim-check` | `test_check_reasons_name_the_machine_and_the_check` |
| T4 | 지정 잡 | 이유 줄 `waiting for lane · on mbp` | `test_a_targeted_check_job_names_its_machine` |
| T5 | 행동 가능한 순서 | `worker_down → no_capable_machine → check_failed → stuck → upload_stalled → not_scheduled → blocked_by_group → overdue → paused` | `test_actionable_order_puts_the_new_reasons_right_after_worker_down` |
| G1 | 템플릿 | `templates/worker.toml` == `examples/worker.toml` 바이트 · `server = "auto"` 한 줄 · `server_name` · `accept_any` 가 보인다 · 읽으면 `wants_discovery` | `test_the_packaged_worker_template_is_byte_identical_to_the_example` · `test_the_worker_example_defaults_to_discovery` · `test_the_worker_example_documents_server_name_and_accept_any` · `test_the_worker_example_loads_and_wants_discovery` |
| H1 | `rcm init worker` | `~/.config/rcm/worker.toml` 0600 · stdout 에 그 경로 · `server = "auto"` · `pool = "mbp"` · `lanes = 1` | `test_init_worker_writes_a_private_worker_toml_with_auto_and_the_short_hostname` · `test_init_worker_file_loads_as_a_worker_config` |
| H2 | 템플릿에서 바뀌는 줄 | `server`·`pool`·`lanes`·`name` 줄만, 줄 수 같음 | `test_init_worker_output_is_the_template_with_only_the_chosen_lines_changed` |
| H3 | 플래그 | `--server URL --pool ios --lanes 2 --name mbp-office` 가 파일에 · `--server auto` 도 | `test_init_worker_flags_fill_server_pool_lanes_and_name` · `test_init_worker_accepts_server_auto_explicitly` |
| H4 | 다음 단계 | stderr 에 `RCM_WORKER_TOKEN` · `rcm worker --check` | `test_init_worker_prints_the_next_step` · `test_init_help_lists_worker` |
| I1 | `--service-file` macOS | plist `Label com.remote-ci-monitor.worker` · `[<지금 rcm 절대경로>, worker, --config, <worker.toml>]` · PATH `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` · HOME · RunAtLoad · 로그 `…worker.log` · stderr 에 `launchctl bootstrap gui/… <경로>` · launchctl 안 부름 | `test_service_file_on_macos_is_a_launchd_plist_with_the_service_path` |
| I2 | `--service-file` Linux | `ExecStart=<rcm> worker --config <worker.toml>` · `Environment=PATH=…` · stderr 에 `systemctl --user enable --now` | `test_service_file_on_linux_is_a_systemd_user_unit` |
| I3 | 예시 서비스 파일 | `examples/launchd/com.remote-ci-monitor.worker.plist` · `examples/systemd/rcm-worker.service` 가 `rcm worker` 를 같은 PATH 로 | `test_the_example_worker_plist_runs_rcm_worker_with_the_service_path` · `test_the_example_worker_unit_runs_rcm_worker_with_the_service_path` |
| J1 | `rcm token add mbp --worker` | stdout 은 비밀 한 줄 · stderr 에 `…RCM_WORKER_TOKEN=<token above> rcm worker --check` 로 끝나는 줄 · 비밀은 stderr 에 없다 | `test_token_add_worker_prints_the_worker_side_command_after_the_secret` |
| K1 | `server = "auto"` · 하나 발견 | 그 서버 · `server` 행 ok | `test_auto_with_one_discovered_server_uses_it` |
| K2 | 여럿 · `server_name` 맞음 | 이름이 맞는 서버 | `test_several_servers_pick_the_one_named_by_server_name` |
| K3 | auto 워커 배선 | `RemoteWorker(..., rediscover=콜백)`, 콜백은 다시 찾아 주소(없으면 None) · `server_name` 을 따른다 | `test_cmd_worker_gives_an_auto_worker_a_rediscover_callback` · `test_cmd_worker_rediscover_honours_server_name` |
| L1 | 등록 연속 5번 실패 | 다섯 번째 실패 직후 콜백 한 번 · 새 주소의 `WorkerClient` 로 등록 | `test_the_threshold_is_five` · `test_register_rediscovers_after_five_connection_failures` |
| L2 | heartbeat 연속 5번 실패 | 다섯 번째에 콜백 · 클라이언트 교체 | `test_heartbeat_rediscovers_after_five_consecutive_failures` |
| M1 | `rcm worker --check` 의 새 행 | `fingerprint  fake 1.2.3 · gone missing` · `presets  1/2 ok · 1 rcm-m5n-missing-tool missing` · 종료 0 | `test_worker_check_measures_the_fingerprint_locally` · `test_worker_check_counts_the_presets_this_worker_can_run` |
| M2 | 보고하지 않는다 | `GET /worker/spec` 만 · `/worker/capabilities`·`/worker/register` 없음 | `test_worker_check_reports_nothing_to_the_server` |
| N1 | 웹 이유 · S4 | `맞는 머신 없음 · mbp: fvm 없음 · mac2-worker: 꺼짐` / `no capable machine · mbp: fvm missing · mac2-worker: down` · actionable | `reasonText — no_capable_machine lists every machine (S4) and is actionable` |
| N2 | 웹 이유 · 점검 | `점검 실패 · Mac mini: sim-check 실패 #3620`(링크 #3620, actionable) · `점검 중 (sim-check)`(actionable 아님) | `check_failed names …` · `check_failed links …` · `check_pending → 점검 중 (sim-check) …` |
| N3 | 웹 이유 · any-capable | 대기 이유 끝에 ` · 어느 머신이든` / ` · any machine` | `an any-capable row waiting for a lane ends with 어느 머신이든` · `placementLabel` 셋 |
| N4 | `환경 바뀜` 칩 | 24시간 안에 바뀜 → `{text: "환경 바뀜", red, lines: ["xcode: Xcode 26.5 · … → Xcode 27.2 · …"]}`, 점검 실패면 red | `envChangedChip — changed an hour ago …` · `changed with checks passing → not red` |
| N5 | 머신 표 | 행마다 id · 이름 · 호스트 · 지문 요약 · `3/4 가능` · 점검(`sim-check 실패 #3620` · `sim-check 통과` · `sim-check 점검 필요`) · 다시 점검은 admin 일 때만 | `machineRows — …` 다섯 |
| N6 | 펼친 행 | `placementDetailLines` → `["Mac mini: 가능", "mbp: fvm 없음", "mac2-worker: 꺼짐"]` | `placementDetailLines — one line per machine in server order` |
| N7 | 확인이 필요한 작업 | `no_capable_machine` 이 `worker_down` 바로 뒤, `check_pending` 은 안 오른다 | `notMoving — no_capable_machine sorts right after worker_down …` |
| W1 | 390 · 1240 × ko · en | 머신 표를 연 채로 가로 넘침 없음 | `test_machines_table_and_new_reasons_never_scroll_sideways` |
| W2 | 접힌 머신 표 | 시작은 접힘 · 토큰 없으면 다시 점검 버튼 0 · 행 셋 · `sim-check 실패 #3620` | `test_machines_table_is_collapsed_and_has_no_recheck_without_admin` |
| W3 | 칩과 이유가 화면에 | `환경 바뀜` · `맞는 머신 없음 · mbp: fvm 없음 · mac2-worker: 꺼짐` | `test_the_env_changed_chip_and_the_no_capable_reason_are_on_the_page` |
| X1 | 문서 | configuration.md 가 `placement` · `"any-capable"` · `concurrency_scope` · `"host"` · `"global"` · `checks` · `check_ref` · `[[probes]]` · `sample` · `accept_any` · `local_accept_any` · `probe_interval_seconds` · `auto_checks` · `rcm machines` · `rcm recheck` 를 말한다 | `test_configuration_documents_each_m5n_key_and_command` |
| X2 | 문서 · 워커 붙이기 | 문서 묶음에 `rcm init worker` · `--service-file` · `server_name` · `server = "auto"` · README 둘에 `rcm machines` | `test_the_doc_set_covers_worker_onboarding` · `test_readmes_mention_rcm_machines` |
| X3 | 예시 server.toml | 주석 처리된 Flutter 블록에 `[[probes]]` · `checks = ["…"]` · `placement = "any-capable"` · `concurrency_scope = "host"`, 주석 아닌 프로브 없음, 여전히 읽힌다 | `test_server_example_has_a_commented_flutter_block_with_probes_and_checks` · `test_server_example_still_loads` |
| X4 | CHANGELOG | `[Unreleased]` 에 `rcm machines` · `placement` · `checks` · `rcm init worker` | `test_changelog_unreleased_mentions_the_m5n_surface` |

## 엣지 케이스

명세 §14.1 의 2 · 11 · 12 · 14 를 사람 쪽에서 본 것은 E-2 · E-11 · E-12 · E-14 로 표시했다.

| id | 시나리오 | 기대 | 테스트 |
|---|---|---|---|
| CE1 | 모르는 머신 id | 종료 2 · `unknown machine 'nope'` · id 셋 나열 | `test_machine_flag_with_an_unknown_id_is_a_usage_error_naming_the_machines` |
| CE2 | down 머신 | 첫 칸 `mac2-worker (down)` | `test_a_machine_that_is_down_says_so_in_its_row` |
| CE3 | 사유가 넷 이상 | 많은 순 → 글자 순으로 셋: `1/6 ok · 2 fvm missing · 1 melos workspace only · 1 need sim-check` | `test_only_the_top_three_reasons_are_listed` |
| CE4 | 프로브 상태 넷 | `xcode exit 72 · java 17.0.12 · idb missing · adb Android` | `test_probe_states_render_as_name_plus_version_missing_or_exit` |
| CE5 | 서버에 못 닿음 | `machines` · `recheck` 종료 3 · `cannot reach` | `test_machines_on_an_unreachable_server_exits_3` · `test_recheck_on_an_unreachable_server_exits_3` |
| CE6 | 옛 서버(`/api/machines` 404) | `rcm machines` 종료 2(트레이스백 없음) · `rcm check` 에는 두 행이 **없다**(FAIL 아님) | `test_machines_on_an_old_server_is_a_usage_error_not_a_traceback` · `test_check_on_an_old_server_has_neither_row` |
| CE7 | `read_auth` 거부 | 종료 2 · `read access denied` | `test_machines_with_read_auth_denied_says_so` |
| CE8 | 머신 0 · 모르는 값 | 머리줄만 · 레인·지문 `—` · `0/1 ok · 1 no report` (0 을 지어내지 않는다) | `test_machines_with_no_machines_prints_the_header_only` · `test_a_missing_value_is_a_dash_not_a_zero` |
| CE9 | 25시간 전에 바뀜 | `ok  machines  3 machines` | `test_check_machines_row_ignores_changes_older_than_a_day` |
| CE10 | 한 머신의 프로브 둘이 바뀜 | `(Mac mini: xcode 26.5 → 27.2, idb 1.1.8 → 1.7.8)` | `test_check_machines_row_lists_every_changed_probe_of_a_machine` |
| CE11 | 바뀐 것 없이 점검 실패만 | `FAIL … 3 machines · 1 check failed (Mac mini: sim-check #3620)` | `test_check_machines_row_counts_a_failed_check_even_without_a_change` |
| CE12 | any-capable 에 맞는 머신 0 | `warn … gate: any machine (no capable machine)` | `test_check_placement_row_says_no_capable_machine_for_an_any_capable_preset` |
| CE13 | 배치·점검 프리셋 없음 | `placement` 행 없음 | `test_check_without_placed_presets_has_no_placement_row` |
| DE1 (E-11) | 클라이언트 토큰으로 recheck | 403 → 종료 2 · stderr 에 `admin` · 도움말에도 `admin` | `test_recheck_with_a_client_token_is_refused_and_says_admin` · `test_recheck_help_says_admin` |
| DE2 | 없는 머신 recheck | 404 → 종료 2 · 서버 문구 그대로 | `test_recheck_of_an_unknown_machine_is_a_usage_error_with_the_server_message` |
| DE3 | 걸 점검이 없다 | `{jobs: []}` → 종료 0 · `nothing to recheck on mbp` | `test_recheck_with_nothing_to_run_says_so_and_exits_0` |
| DE4 | 토큰 없음 | 보내기 전에 종료 2 · `token` | `test_recheck_needs_a_token` |
| EE1 | 바쁜 맞는 머신이 있다 | 아무 줄도 없다(S4 — 바쁜 건 정상) | `test_run_says_nothing_when_some_machine_is_eligible_even_if_busy` |
| EE2 | 옛 서버(응답에 `placement` 없음) | 아무 줄도 없다 | `test_run_against_an_old_server_without_placement_says_nothing` |
| EE3 | `blocked` 가 빔 | `gate: no capable machine — unknown` (§12) | `test_run_with_an_empty_blocked_list_says_unknown_not_an_invented_reason` |
| EE4 | `blocked` 에 이름표 없음(B-16) · 점검 코드 | `@local: sim-check failed #3620 · mbp: sim-check running` | `test_run_line_uses_worker_ids_and_check_wording` |
| EE5 (E-11) | `--machine` 거부(403) | 종료 2 · 서버 문구 그대로 | `test_run_machine_flag_refused_for_a_client_token_exits_2_with_the_message` |
| EE6 | `--machine` 이 down 머신(409) | 종료 2 · 서버 문구 | `test_run_machine_flag_on_a_down_machine_exits_2` |
| EE7 | `--machine` 없음 | 본문에 `machine` 키 없음 | `test_run_without_machine_flag_sends_no_machine_key` |
| FE1 | pool 대기 행 | `any machine` 없음 · `--json` 은 키를 그대로 | `test_jobs_does_not_say_any_machine_for_a_pool_row` · `test_jobs_json_keeps_the_placement_keys` · `test_a_pool_row_does_not_say_any_machine` |
| TE1 | `placement_detail` 이 빔 | `no capable machine — unknown` · ETA `—` | `test_no_capable_machine_without_detail_says_unknown` · `test_no_capable_machine_has_no_eta` |
| TE2 | 판정 코드마다 짧은 문구, 모르는 코드는 그대로 | `fvm, gitleaks missing` · `melos workspace only` · `sim-check failed` · `no report` · `stale report` · `fingerprint unknown` · `some_future_code` | `test_each_verdict_code_has_one_short_text` |
| TE3 | `check_pending` 은 행동 가능한 이유가 아니다 | 상수 값과 순서 | `test_check_pending_is_not_actionable` · `test_reason_constants_are_the_wire_values` |
| GE1 | 예시에 토큰 값이 없다 | `token = …` 줄 없음 | `test_the_worker_example_has_no_token_value` |
| HE1 | 잘못된 값 | `--server macmini:8787` · 따옴표 · `--pool "bad pool"` · `--lanes 0`/`65` → 종료 2 · 파일 없음 | `test_init_worker_rejects_bad_values_before_writing` |
| HE2 | 이미 있다 | `--force` 없이 종료 2 · `refusing to overwrite` · 그대로 / `--force` 면 덮는다 | `test_init_worker_refuses_to_overwrite_without_force` · `test_init_worker_force_overwrites` |
| HE3 | 호스트명이 이름이 아니다 | `Bob's MacBook` → `pool = "default"` | `test_init_worker_falls_back_to_default_pool_when_the_hostname_is_not_a_name` |
| IE1 | PATH 함정 | PATH 가 rcm 의 bin 으로 시작하지 않는다 | `test_service_file_path_does_not_start_with_the_venv_bin` |
| IE2 | 비밀이 셸에 있다 | 서비스 파일 · 출력 어디에도 `RCM_WORKER_TOKEN` 값이 없다 | `test_service_file_never_carries_the_worker_token` |
| IE3 | worker.toml 이 이미 있다 | `--service-file` 은 그 파일을 안 건드리고 성공 | `test_service_file_does_not_touch_an_existing_worker_toml` |
| IE4 | 서비스 파일이 이미 있다 · 다른 OS | 종료 2 · 그대로 / `win32` → 종료 2 · `launchd`·`systemd` 를 말한다 | `test_service_file_refuses_to_overwrite_without_force` · `test_service_file_on_another_platform_is_a_usage_error` |
| JE1 | 클라이언트 토큰 | 워커 안내 줄 없음 | `test_token_add_for_a_client_has_no_worker_line` |
| KE1 | `server = ""` | auto 와 같다 | `test_an_empty_server_also_discovers` |
| KE2 | 여럿 · 이름이 없거나 안 맞음 | 종료 2 · 이름 둘 · `server_name` 안내 | `test_several_servers_without_a_match_is_a_usage_error_listing_them` |
| KE3 | 하나도 없음 | 종료 2 · `no rcm server found on this network` | `test_no_discovered_server_is_a_usage_error_with_the_network_hint` |
| KE4 | 고정 서버 · `--server` | 찾지 않는다 · 다시 찾기 콜백 없음 | `test_a_pinned_server_never_discovers` · `test_the_server_flag_beats_auto` · `test_cmd_worker_gives_a_pinned_worker_no_rediscover` |
| LE1 | 고정 서버 연결 실패 | 같은 클라이언트로 계속 재시도 | `test_register_keeps_retrying_a_pinned_server` · `test_a_pinned_worker_heartbeat_never_rediscovers` |
| LE2 | 4번 실패 뒤 성공 | 다시 찾지 않는다 · heartbeat 카운터는 성공에 0 | `test_register_does_not_rediscover_before_five_failures` · `test_heartbeat_failures_must_be_consecutive` |
| LE3 | 다시 찾아도 없음 · 같은 주소 | 클라이언트 그대로 · **매 실패마다가 아니라 5번마다** 다시 묻는다(5 · 10) | `test_register_keeps_the_client_when_rediscovery_finds_nothing_and_tries_again_later` · `test_register_keeps_the_client_when_rediscovery_returns_the_same_address` |
| LE4 | 403 은 주소 문제가 아니다 | 다시 찾지 않고 `WorkerExit(2)` | `test_a_refusal_is_not_a_connection_failure` |
| ME1 | PATH 에 상대 항목(결정 93) | `1/2 ok · 1 rcm-m5n-missing-tool workspace only` | `test_worker_check_says_workspace_only_when_path_has_a_relative_entry` |
| ME2 | 옛 서버(`/worker/spec` 404) | 두 행이 `—` 로 시작 · FAIL 아님 · 종료 0 | `test_worker_check_on_an_old_server_without_spec_does_not_fail` |
| NE1 (E-14) | down · 이름표 없음 · 모르는 코드 · 언어 없음 | `꺼짐`/`down`(코드와 무관) · 워커 id · 코드 그대로 · 영어 | `placementNoteText — …` 열다섯 |
| NE2 | `placement_detail` 없음 | `맞는 머신 없음 · 알 수 없음` · 펼친 행 `[]` | `no_capable_machine without detail …` · `no detail → no lines …` |
| NE3 | 점검 이름 여럿 · 없음 | `점검 중 (sim-check, emu-check)` · `점검 중` | `check_pending with two different checks …` · `check_pending without names → 점검 중` |
| NE4 | 24시간 경계 | 23h59m 칩 · 24h01m 없음 · 바뀐 적 없음 · 5일 전 · 읽을 수 없는 시각 → null | `24h window …` · `never changed, five days ago, or an unreadable time → no chip` |
| NE5 | 사라진 프로브 | `idb: idb_companion 1.1.8 → 없음`, 이름 순 | `a probe that went missing reads 없음 / missing …` |
| NE6 | 머신 표의 모르는 값 · 망가진 문서 | down 표시 · 프리셋 없음 `—` · `null`/`{machines: "x"}` → `[]` | `a down machine says so; unknown numbers are —` · `a broken document → no rows, not a throw` |
| NE7 | 도는 점검 · 점검 없는 머신 | `sim-check 점검 중 #3651` · `—` · 다시 점검은 점검이 있을 때만 | `a running check and a machine without checks` · `다시 점검 only with an admin token …` |
| WE1 | 긴 프리셋 이름의 새 이유 행 | 390px 에서도 안 넘친다 | `test_machines_table_and_new_reasons_never_scroll_sideways` |
| XE1 | 프로브 출력이 읽기 라우트에 보인다(§4.1) | configuration.md 의 한 문단이 probe · `read_auth` · `/api/machines`(또는 status) · version 을 함께 말한다 | `test_configuration_warns_that_probe_output_is_visible_on_read_routes` |
| XE2 | any-capable 은 비밀 없는 프리셋용(§13) · 점검 프리셋은 git_ref | 그 문단들이 있다 | `test_configuration_says_any_capable_is_for_presets_without_secrets` · `test_configuration_says_a_check_preset_needs_git_ref` |

명세 §14.1 의 2(첫 회차 `timeout` → `fingerprint_unknown`)와 12(`measured_other` · low)는 판정·추정 자체가
역할 A 의 것이라, 여기서는 그 결과가 **글자로** 나올 때만 잡는다 — `fingerprint unknown`(TE2) ·
`지문 모름`(NE1). `measured_other` 신뢰도의 표시는 기존 `confidence` 배지가 `low` 를 그대로 그린다.

## 가정 — 명세가 글자를 정하지 않아 시험이 고른 것

구현이 달리 정하면 **이 문서와 테스트를 같이 고친다.** 역할 B 의 가정과 겹치는 곳은 그쪽 번호를 단다.

**CLI 문구**

- **C1** `rcm machines` 표는 칸 사이를 두 칸 이상 띄우고, 칸 안에는 두 칸 공백이 없다. 머리줄 단어는
  `machine host pool lanes fingerprint presets`. 순서는 서버 문서 그대로. down 머신은 첫 칸이
  `<이름> (down)`. 첫 칸은 `display_name`(없으면 id), 호스트는 `host_label` 그대로, 레인은 항목의
  **`lanes` 키**(명세 §8.1 에 없다 — 더해야 한다, 없으면 `—`).
- **C2** 지문 요약 = 프로브마다 `<이름> <값>` 을 ` · ` 로. 값: `ok` 면 값의 첫 `\d+(\.\d+)+`, 없으면
  첫 낱말 · `error` 면 값 그대로(`exit 72`) · `missing` 이면 `missing`. 프로브가 없으면 `—`. 웹
  `probeSummary` 도 같은 규칙이고 한국어에서 `missing` 만 `없음`.
- **C3** 판정 하나의 짧은 사유(CLI 영어 / 웹 한국어): `tool_missing` → `<도구들, 로 이음> missing` /
  `없음` · `workspace_tool` → `<도구> workspace only` / `작업 공간에만` · `check_failed` →
  `<점검> failed #<id>` / `<점검> 실패 #<id>`(id 없으면 `#` 조각 없이) · `check_running` →
  `<점검> running` / `<점검> 점검 중` · `check_needed` → `need <점검>` / `<점검> 점검 필요` ·
  `report_missing` → `no report` / `보고 없음` · `report_stale` → `stale report` / `보고 낡음` ·
  `fingerprint_unknown` → `fingerprint unknown` / `지문 모름` · 상태 `down` 또는 코드 `worker_down` →
  `down` / `꺼짐` · `eligible` → `eligible` / `가능` · 모르는 코드 → 코드 그대로. detail 키는 역할 A 가정
  13(`tools` · `check` · `job_id`)을 따른다.
- **C4** `presets` 칸 = `<eligible 수>/<전체> ok` 뒤에 사유를 짧은 사유 글자별로 세어 많은 순 → 글자 순으로
  최대 셋, 각 `<수> <짧은 사유>`. 판정이 하나도 없으면 `—`(웹), CLI 는 `0/N ok …`.
- **C5** 이유 줄과 S4 줄은 eligible 이 **아닌** 메모만 늘어놓는다(`placement_detail` 에는 eligible
  워커도 있다 — B-15). 이름은 `display_name`, 없으면 워커 id(`blocked[]` 에는 이름표가 없다 — B-16,
  그래서 `@local` 이 그대로 보인다). down 워커의 `blocked` 코드는 `worker_down` 으로 본다.
- **C6** `rcm top`/`rcm jobs` 이유 줄: `no capable machine — <메모들>` · `check failed — <메모들>` ·
  `check pending — <메모들>` (메모는 ` · ` 로 이음, 없으면 `unknown`). any-capable 대기
  (`placement == "capable"`, 행은 DB 값 — B-15)는 기존 이유 뒤에 ` · any machine`, 지정 잡은
  ` · on <target_worker>`. `rcm jobs` 는 같은 글자를 행 끝(요약 칸)에 싣는다.
- **C7** `rcm check` 의 `machines` 행: `<N> machines` 뒤에 `<n> environment changed (<이름>: <프로브> <앞 버전> → <뒤 버전>[, …])`
  (24시간 안에 `fingerprint.changed_at` · `previous` 가 있는 프로브, 버전은 C2 의 토큰, 머신 여럿이면
  ` · ` 가 아니라 `; ` 로 — 시험은 하나만 잰다)와 `<n> check failed (<이름>: <점검> #<id>)`(`checks[]` 의
  `failed`). 실패가 있으면 FAIL, 아니면 ok. `/api/machines` 가 404 면 행이 없다.
- **C8** `placement` 행: 프리셋 중 `placement == "any-capable"` 이거나 `checks` 가 빈 목록이 아닌 것만,
  `/api/status.presets[]` 차례로. any-capable → `<프리셋>: any machine (<n> eligible)`, pool →
  `<프리셋>: pool <풀> (<n> eligible)`(그 풀의 살아 있는 머신만 센다). 0 이면 괄호 안이 사유:
  `check failed` > `check pending` > `no capable machine`. 하나라도 0 이면 **warn**, 아니면 ok. 해당
  프리셋이 없으면 행이 없다.
- **C9** `rcm run` 의 S4 줄은 응답의 `placement.mode == "any-capable"` 이고 `eligible_now` 가 빌 때만
  `<프리셋>: no capable machine — <blocked 메모들 | unknown>` 을 stderr 에 **한 번**. `--no-wait` 든
  기다리든 제출 직후. 종료 코드는 그대로. `rcm: ` 접두는 잠그지 않는다.
- **C10** `rcm recheck MACHINE [CHECK…]` 는 `POST /machines/{MACHINE}/recheck` 에 `{"checks": [...]}`
  (이름이 없으면 키를 빼거나 `[]`). 응답 `{"jobs": [{"preset", "job_id"}]}`(B-18) 을 stdout 에
  잡마다 `#<job_id>` 와 프리셋 이름이 든 줄로. 빈 목록이면 `nothing to recheck on <MACHINE>`, 종료 0.
  4xx → 종료 2 + 서버 문구, 연결 실패 → 3. 토큰이 없으면 보내기 전에 2. 도움말 한 줄에 `admin`.
- **C11** `rcm run --machine ID` 는 본문에 `"machine": ID`(없으면 키 없음). 409(down)는 **잡이 안
  생겼으니 종료 2**(오늘 `_run_git_ref` 는 409 를 3 으로 낸다 — 바꿔야 한다). 403 은 오늘처럼 2.
- **C12** `rcm machines --machine ID` 는 판정마다 `<프리셋>  <state>  <code>  <짧은 사유>` 한 줄 +
  프로브 값 전부(앞 값 포함) + 점검. 모르는 ID 는 종료 2 · `unknown machine '<ID>'` + id 목록.
  `--json` 은 서버 문서(또는 그 머신 항목) 그대로. 옛 서버 404 → 2(`_client_fail`), 401 → 2
  (`read access denied`), 연결 실패 → 3.
- **C13** `rcm worker --check` 의 새 행 둘은 FAIL 이 되지 않는다(사실이지 검사 실패가 아니다).
  `fingerprint` 상세는 C2, `presets` 상세는 C4(`ok` 낱말 포함). 측정은 `GET /worker/spec` 의
  `probes` · `presets[].requires` 로, 이 프로세스의 PATH 에서. 그 라우트가 404 면 두 행 상세는 `—` 로
  시작한다. 등록 · capabilities 보고는 하지 않는다.

**워커 붙이기**

- **C14** `rcm init worker [--server URL|auto] [--pool P] [--lanes N] [--name N] [--force]
  [--service-file PATH]`. 기본 경로 `user_config_dir()/worker.toml`, 0600, stdout 에 그 경로.
  `pool` 기본은 `socket.gethostname()`(또는 `platform.node()`)의 첫 `.` 앞 — 대소문자 그대로 —
  이 `_NAME_RE` 에 안 맞으면 `default`. 템플릿에서 `server`·`pool`·`lanes`·`name` 줄만 바꾸고(주석
  `# name = …` 줄을 바꿔 쓴다) 줄 수를 지킨다. `--server` 는 `auto` 또는 `http(s)://` 이고 따옴표 ·
  역슬래시 · 공백 금지(`init client` 와 같은 규칙). `--lanes` 1~64. 다음 단계는 stderr 에
  `RCM_WORKER_TOKEN` 과 `rcm worker --check` 를 말한다. `init --help` 에 `worker`.
- **C15** `--service-file PATH` 는 서비스 파일**만** 쓴다(worker.toml 을 안 건드린다 — 이미 있어도 성공).
  PATH 가 있으면 `--force` 없이 거절. darwin → plist `Label com.remote-ci-monitor.worker`,
  `ProgramArguments = [<rcm>, "worker", "--config", <worker.toml 절대경로>]`, `RunAtLoad`, `KeepAlive`,
  `EnvironmentVariables = {PATH, HOME}`, 로그 `~/Library/Logs/rcm/worker.log`(절대), stderr 에
  `launchctl bootstrap gui/$(id -u) <PATH>`. linux → `[Service] ExecStart=<rcm> worker --config <…>`
  · `Environment=PATH=…`, stderr 에 `systemctl --user enable --now <unit>`. 그 밖 → 종료 2,
  `launchd`·`systemd` 를 말한다. `<rcm>` 은 **실재하는 절대경로**이고 이름이 `rcm` — 시험 환경에서는
  `Path(sys.executable).parent / "rcm"`(= `.venv/bin/rcm`)이 그 답이다. PATH 는 정확히
  `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`. 토큰은 어디에도 안 쓴다.
- **C16** 예시 `examples/launchd/com.remote-ci-monitor.worker.plist` · `examples/systemd/rcm-worker.service`
  는 같은 Label · `rcm worker` · 같은 PATH 다. 템플릿 `templates/worker.toml` == `examples/worker.toml`,
  `server = "auto"` 한 줄, `server_name` · `accept_any` 줄(주석이어도 된다), `token =` 줄 없음.
  `WorkerConfig` 에 `wants_discovery`(빈 값 · `auto`) · `server_name = ""` · `accept_any = True`.
- **C17** 워커 찾기는 `cli.discover`(모듈 수준 이름 — `test_cli_discover` 와 같은 대역 자리)를 부른다.
  하나 → 그것 · 여럿 → `Found.name == server_name` 인 것이 하나면 그것 · 아니면 종료 2, 이름들과
  `server_name` 을 말한다 · 없음 → 종료 2, `no rcm server found on this network`. `--server` 와 고정
  주소는 찾지 않는다. `--check` 도 같은 규칙.
- **C18** 다시 찾기: `RemoteWorker.__init__(…, rediscover: Callable[[], str | None] | None = None)`,
  상수 `remote_worker.REDISCOVER_AFTER_FAILURES = 5`. 등록 · heartbeat 의 **연결 실패(status 0)** 가
  연속 5번이면(다섯 번째 실패 직후) 한 번 부른다. 새 주소가 지금과 다르면 `self.client` 를
  `WorkerClient(새 주소, 토큰)` 으로 바꾸고, None · 같은 주소면 그대로 두고 카운터를 0 으로(다음 5번 뒤
  다시). 4xx 는 세지 않는다. `cmd_worker` 는 `cfg.wants_discovery` 일 때만 콜백을 넘기고, 콜백은 C17
  규칙(모호 · 없음이면 None)으로 주소를 돌려준다.
- **C19** `rcm token add NAME --worker`: stdout 은 비밀 한 줄 그대로, stderr 에
  `… RCM_WORKER_TOKEN=<token above> rcm worker --check` 로 **끝나는** 줄(앞말은 자유). 비밀은 stderr 에
  없다. 클라이언트 토큰에는 그 줄이 없다.

**웹**

- **C20** `window.rcm` / `module.exports` 에 새 키: `placementNoteText(note, lang)` →
  `<이름>: <짧은 사유>`(C3) · `placementLabel(row, lang)` → `어느 머신이든`/`any machine` · `<w> 지정`/
  `on <w>` · `""` · `placementDetailLines(row, lang)` → 메모마다 `placementNoteText`(eligible 포함, 없으면
  `[]`) · `envChangedChip(machine, nowMs, lang)` → `null` 또는 `{text: "환경 바뀜"|"env changed", red,
  lines}`(`fingerprint.changed_at` 이 `nowMs` 기준 24시간 이하 · `red` = `checks[]` 에 failed ·
  `lines` = `previous` 키가 있는 프로브를 이름 순으로 `<이름>: <앞> → <뒤>`, 뒤가 null 이면 상태 낱말
  `없음`/`missing`) · `probeSummary(fingerprint, lang)`(C2) · `machineRows(doc, admin, lang)` → 행
  `{id, name, alive, host, fingerprint, presets, checks, recheck}`(`presets` = `n/N 가능`|`n/N ok`, 판정
  없으면 `—` · `checks` = 점검마다 `<점검> 실패 #id`|`통과`|`점검 중 #id`|`점검 필요`(영어 `failed #id`|
  `passed`|`running #id`|`need <점검>`)를 ` · ` 로, 없으면 `—` · `recheck` = admin 이고 `checks[]` 가
  비지 않음 · 망가진 문서는 `[]`). `reasonText` 의 새 갈래: `no_capable_machine` → `맞는 머신 없음 · <메모들>`
  (메모 없으면 `· 알 수 없음`, actionable) · `check_failed` → `점검 실패 · <메모들>`(actionable, 링크 =
  메모의 `job_id`) · `check_pending` → `점검 중 (<점검 이름들, 처음 나온 차례 · 중복 없이>)`(이름 없으면
  `점검 중`, actionable 아님). 영어는 `no capable machine` · `check failed` · `checking`. any-capable
  `waiting_for_lane` 은 끝에 ` · 어느 머신이든`. `ACTIONABLE` 은 §7.3 의 순서. `lang` 이 없으면 영어(기존
  순수 함수 계약).
- **C21** DOM: 머신 표는 `<details id="machines">`(처음엔 닫힘) 안, 머신마다 `tr[data-machine="<id>"]`,
  다시 점검 버튼은 `button.recheck`(admin 일 때만). 페이지는 `/api/machines` 를 읽는다(토큰 없이도).
  칩 글자 `환경 바뀜` 과 새 이유 글자는 화면 글자(`innerText`)에 나온다.

**문서**

- **C22** 문서 잠금은 `docs/configuration.md` 의 문구(키 이름은 백틱으로) · 문서 묶음(README ·
  configuration · operating) · README 둘 · `examples/server.toml` 의 **주석** Flutter 블록 · CHANGELOG
  `[Unreleased]` 를 본다. 「프로브 출력이 보인다」 경고는 한 문단 안에 probe · `read_auth` ·
  `/api/machines`(또는 `/api/status`) · version 이 함께 있어야 한다.
