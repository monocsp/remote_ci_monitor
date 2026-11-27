# M5t-b 실기 확인 (끊김 유예 · 펜싱 · 감독자 · 그룹 홀드 · 로그 오프셋)

- 날짜: 2026-11-27
- 대상: 브랜치 `test/acceptance-worker-result-loop-live` 의 `341c5d3b0774baef52a23afcd53ad32173fe19f2`.
  이 커밋의 `src/` 와 `web/` 는 `dev` 의 `37e7b98c22dfa0ebad73b4ef6c04181c0a374aa8` 과 같다. M5t-b 의
  PR1(#284), PR2(#286), PR3(#285)이 모두 머지된 판이다. 하네스는 시험 폴더에 도우미
  `bin/m5tb_helper.py` 를 써서 부르는데, 세 번째 실기의 시험 폴더에 남은 도우미는 이 커밋의 하네스가
  쓰는 것과 바이트 단위로 같았다. 두 번째 실기는 판정을 고치기 전의 `02b6bc3` 에서 돌았다.
- 명세: `docs/m5tb-workplan.md` 의 §5 PR4 행, §6 의 10 · 13 · 19, §7.1 의 실기 대상 행, 측정값 F78 ·
  F83 · F84. M5t-b 는 원격 잡의 결과가 연락이 끊겨도 한 번만, 맞게 돌아오게 하는 작업이다.
- 다시 돌리는 법: 워크트리에서 `M5TB_PORT=8797 bash scripts/acceptance/m5tb_live.sh` 를 돌린다. G 만료를
  실제로 기다리므로 전체는 20분쯤 걸린다(세 번째 실기 18분 46초). 단계 하나만 다시 돌릴 때는
  `--only RL15,RL43`, 단계 표는 `--list`, 잡 없이 가드와 정리만 볼 때는 `--self-test` 다.
- 종료 코드: FAIL 이 하나라도 있으면 1, 시작 전 검사에서 멈추면 2, UNKNOWN 이 하나라도 있으면 3 이다.
  판정 줄 없이 끝난 단계도 끝에 `UNKNOWN … no verdict` 줄을 받으므로 3 이 된다. 모두 PASS 나 SKIP
  이어야 0 이다.

이번 실기는 세 번 돌렸다. 첫 실기는 하네스 결함 둘 때문에 뒤쪽 단계가 돌지 못했다. 하네스를 고친 뒤
돌린 두 번째 실기는 FAIL 과 UNKNOWN 이 없었지만, 그 뒤 판정 함수에서 fail-open 을 찾아 판정을 고쳤다.
고친 판정으로 돌린 세 번째 실기에서도 FAIL 과 UNKNOWN 이 없었고 종료 코드는 0 이었다. 실기 판정
22개가 PASS 였고, 하네스가 일부러 돌리지 않는 몫 8개는 SKIP 이며 SKIP 마다 그 경우를 덮는 시험을
적었다. 아래 표와 증거는 모두 세 번째 실기의 것이다. 남은 것은 Linux 머신에서의 `/proc` 신원 확인과
그 머신의 `ps` 꼴 확인이다. 이 머신에는 Linux 가 없다.

## 판정을 고친 뒤 다시 돌린 실기

두 번째 실기 뒤 격리 확인 1라운드가 판정 함수에서 「못 읽은 것」을 「없음」으로 읽는 곳을 찾았다
(PLAN.md 「fail-open 금지」). 하네스를 고친 뒤 세 번째 실기를 돌렸다. 판정이 바뀐 단계는 다음과 같다.

- RL1: 그 잡의 lost 사건 수가 1 인지 센다. 명령이 죽은 시각은 DB 의 REAL `finished_at` 과 허용치 없이
  견준다(명세의 「차이 ≥ 0」).
- RL15: 다음 잡이 고아가 끝난 뒤에 시작했는지를 시각이 아니라 워커 로그의 줄 순서로 본다.
- RL18: 다섯 곳마다 읽었다는 증거(명령 종료 0, HTTP 200, 가린 값이나 알려진 글 하나 이상)가 없으면
  원문 0 번을 「비밀 없음」으로 보지 않고 UNKNOWN 으로 본다.
- RL29-web: i18n.js 가 그리는 두 언어의 문장을 각 언어 화면에서 찾는다. 찾을 끝이나 문장이 없으면
  UNKNOWN 이다.
- RL34: 워커가 적은 값이 문턱보다 작아도 FAIL 이다.
- RL43 (d): 상한을 명세대로 grace_J + 2초(+ 측정 0.04초)로 조였다.
- RL51: (b) 워커 `kill -9` 와 (c) 의 상한을 조였다. 아래 RL51 행에 근거가 있다. (a) 는 SKIP 줄을 더했다.
- 판정 경계의 15 · 10 · 1 · 0.25 는 `remote_worker.py` · `job_supervisor.py` 의 상수와 같고 시험이 그
  둘을 견준다. 판정 함수가 예외로 끝나거나 판정 줄 없이 끝난 단계는 UNKNOWN 이다.

세 번째 실기는 2026-11-27 11:51(KST)에 시작해 18분 46초 걸렸다. PASS 22 · SKIP 8 · FAIL 0 · UNKNOWN 0
이고 종료 코드는 0 이다. SKIP 이 하나 늘어난 것은 새 RL51-a 줄이고, 판정 함수의 오류 기록(`judge.err`)은
비었다. 판정이 바뀐 행은 새 판정으로도 PASS 였고, 그 값은 이렇다.

- RL1: 명령은 끊은 뒤 62.577초에 사라졌고, DB 의 `finished_at` 보다 28.076초 앞이었다. lost 사건은 1 번이다.
- RL15: 다시 띄운 워커의 로그에서 `orphan #18: stopped process group` 줄이 `claimed #20` 줄보다 앞이다.
- RL18: `rcm logs` 종료 0, 웹 로그와 잡 문서 HTTP 200 이다. 가린 값은 서버 `log.txt` · `rcm logs` · 웹
  로그에 각 200번 있었고 잡 문서에 프리셋 이름이 2번, SSE 에 그 잡의 사건이 6번 있었다. 원문은 다섯 곳
  모두 0번이다.
- RL29-web: 다섯 끝의 문장을 영어 화면과 한국어 화면에서 모두 찾았다(바늘 10개, 빈 바늘 없음).
- RL34: 워커가 로그에 적은 값은 기본 grace 62, 큰 grace 52 로 각 문턱과 같아 새 하한과 상한 안이다.
- RL43 (d): 감독자만 `kill -9` 한 뒤 0.888초에 명령이 사라졌다(상한 5.04초).
- RL51: 워커가 산 채 취소 3.751초, 워커 `kill -9` 3.234초(상한 3.85초), grace 를 거의 다 쓰고 끝남
  3.804초다.

이 실기는 다른 프로젝트의 게이트 잡과 겹쳐 이 Mac 의 부하가 약 7 이었다. 두 번째 실기(옛 판정,
10:43 시작, 18분 47초, PASS 22 · SKIP 7, 종료 0)는 이력으로 아래 실기 표에만 남긴다.

## 무엇을 어디서 어떻게 돌렸나

시험은 모두 Apple Silicon Mac 한 대(macOS 27.2)에서 했다. 이 Mac 에서는 운영 rcm 이 서비스로 돌며 포트
8787 을 쓴다. 하네스는 그 옆에 시험 서버를 따로 띄운다.

- 시험 서버: 워크트리의 `.venv/bin/rcm serve`, bind `127.0.0.1`, 포트 8797, `advertise = false`.
- 시험 폴더(`data_dir` 포함): `<HOME>/.local/share/rcm-devtest-m5tb/live-ed0c12f2`. 하네스가 실행마다
  새로 만들고 지우지 않는다. `/tmp` 아래와 운영 경로는 시작하기 전에 거절한다.
- 환경: 모든 rcm 명령은 `HOME` 과 XDG 폴더 넷을 시험 폴더 안으로 바꾸고 셸의 `RCM_*` 를 비운 채 돈다.
  launchd 는 쓰지 않는다.
- 서버 값(§7.5 의 e2e 값과 같다): `disconnect_grace_seconds` G = 90, `worker_timeout_seconds` T = 10,
  `worker_heartbeat_seconds` 1, 워커 `grace_seconds` 3(그래서 표식 잡의 grace_J = 3), `admission =
  "always"`, `recent_count = 100`.
- 워커: 단계마다 새 `rcm worker` 프로세스를 띄운다. 분단은 표준 라이브러리 TCP 중계
  (`tests/tcp_relay.py`)를 멈추거나 끊어서 만들고, 서버 정지는 시험 서버 프로세스를 멈춰서 만든다.
- 프리셋: 표지 파일과 종료 코드만 내는 가짜 `sh -c` 프리셋이다. `irreversible = true` 를 단 것과 달지
  않은 것을 함께 쓴다. 알림 훅은 받은 환경을 시험 폴더의 파일에 적는 가짜 하나뿐이다.
- 웹: 저장소의 CDP 도우미(`tests/test_web_browser.py`)로 헤드리스 Chrome 을 390px 폭, 영어와 한국어로
  열어 화면 글과 띠 높이, 페이지 오류를 읽는다.

| 실기 | 시작(KST) | 걸린 시간 | 같이 돈 것 | 결과 |
|---|---|---|---|---|
| 첫째 | 2026-11-27 09:57 | 10분 8초 | 다른 프로젝트의 게이트 잡(이 Mac 의 부하 12~19) | PASS 9 · SKIP 2 · UNKNOWN 6 · FAIL 1, 뒤쪽 단계가 돌지 못함 |
| 둘째 | 2026-11-27 10:43 | 18분 47초 | 없음(부하 약 3) | 옛 판정. PASS 22 · SKIP 7 · FAIL 0 · UNKNOWN 0, 종료 0 |
| 셋째 | 2026-11-27 11:51 | 18분 46초 | 다른 프로젝트의 게이트 잡(부하 약 7) | PASS 22 · SKIP 8 · FAIL 0 · UNKNOWN 0, 종료 0 |

하네스 출력의 시각은 UTC 다(셋째 실기 `02:51:33` 이 KST 11:51:33).

## 단계별 판정 (셋째 실기)

판정 낱말은 하네스의 것을 그대로 옮겼다. 하네스는 모르는 것을 PASS 로 적지 않고 UNKNOWN 으로 적는다
(PLAN.md 「fail-open 금지」). 상한은 하네스의 판정 함수(`scripts/acceptance/m5tb_live.sh` 의 `judge_*`)가
쓰는 값이다.

| ID | 판정 | 잰 값과 판정 근거 |
|---|---|---|
| RL50 | PASS | 중계가 claim 응답 전의 heartbeat 를 붙잡았다 늦게 놓았다. 표식 잡은 `running` 이고 프로세스가 살아 있으며 홀드 0, 같은 그룹 둘째 잡은 `queued` |
| RL10 | PASS | 같은 토큰으로 둘째 프로세스를 띄우자 첫 프로세스가 0.986초 뒤 종료 0 으로 끝났다. 잡 프로세스가 사라졌고 `lost`(`worker_replaced`), `rcm wait` 종료 3, 홀드 0, 그 이름의 세션 2 |
| RL51 | PASS | SIGTERM 을 무시하는 자식이 사라진 시간: 워커가 산 채 취소 3.751초, 워커 `kill -9` 3.234초, 명령이 grace 를 거의 다 쓰고 끝남 3.804초. 명세는 (b)(c) 모두 「첫 SIGTERM + grace_J + 0.5초」다. 워커 `kill -9` 는 그 SIGTERM 이 감독자가 부모의 죽음을 알아챈 뒤라 감지 상한(0.25 + 0.06 = 0.31초, F78)과 측정 0.04초를 더해 grace_J + 0.85 = 3.85초가 상한이다. 워커가 산 경우는 하네스가 첫 SIGTERM 을 볼 수 없어 `rcm cancel` 이 돌아온 때부터 잰다. 그래서 취소를 워커에 싣는 다음 heartbeat(HB 1초)와 그 왕복 0.5초를 더해 grace_J + 1 + 0.5 + 0.5 = 5초가 상한이고 (c) 도 같다. (c) 의 옛 상한 grace_J + 4 + 1 = 8초는 다른 몫(「셋 다 플래그 → 그룹 소멸 ≤ grace_J + 4초」)이었다. 이 실기 값은 세 상한 안이다 |
| RL51-a | SKIP | (a) 감독자가 시작 확인을 지난 뒤 명령 Popen 전에 SIGTERM 을 받는 창은 주입 훅이 있어야 한다. `test_rl51_a_sigterm_in_the_popen_window_reaches_the_command`(`tests/test_job_supervisor_m5tb.py`). 이 SKIP 줄은 격리 확인 1라운드 뒤 하네스에 더했고 셋째 실기의 요약에 처음 나왔다 |
| RL18 | PASS | 릴리스 비밀 원문이 서버 `log.txt`, `rcm logs`, SSE(984바이트 받음), 웹 로그 라우트, 잡 문서 어디에도 0번. 서버 `log.txt` 는 가린 출력과 바이트 단위로 같음, 중계를 끊은 뒤 워커가 조각을 다시 보냄, `log_complete` true. 판정은 다섯 곳마다 읽었다는 증거를 요구하고 이 실기에서 모두 있었다. 가린 값이 서버 `log.txt` · `rcm logs`(종료 0) · 웹 로그(HTTP 200)에 각 200번, 잡 문서(HTTP 200)에 프리셋 이름 2번, SSE 에 그 잡의 사건 6번이다. 두 번째 실기의 옛 판정은 SSE 바이트 수만 보았다 |
| RL28 | PASS | 유예 중인 잡: `running`, 사유 `worker_unreachable_grace`, `estimate.finish_at` null, `/api/health` 다섯 번 같은 값. `rcm wait` 의 끊김 줄 13.117초(상한 T + 15 = 25), 돌아옴 줄 2.759초(상한 15), `--timeout 8` 은 8.235초에 종료 3 |
| RL28-web | PASS | 390px, 영어 · 한국어, 페이지 오류 0, 띠 높이 75px / 76px(상한 80). 끊긴 행 둘은 회색 레인이고 시간을 세지 않으며 진행 막대에 시간 눈금이 없다. 대조군 행(중계를 거치지 않는 워커)은 시간을 센다 |
| RL28-cancel | PASS | 유예 중 `rcm cancel` 뒤 `cancelling`, `rcm top` · `rcm eta --job` · 새 `rcm wait` 가 모두 「돌아오면 취소된다」고 말하고 「이어진다」고 말하지 않음, 돌아온 뒤 `cancelled` |
| RL34 | PASS | heartbeat 가 매번 요청 시한까지 매달림. 기본 grace: 끊은 뒤 62.004초에 명령이 사라짐(워커의 문턱 62초), 큰 grace: 51.945초(워커가 grace 30 을 13 으로 줄였다는 줄, 문턱 52초). 둘 다 `worker_self_stopped`, 종료 3, 홀드 0. 아래 「RL34 의 값이 문턱에 붙어 있는 이유」 |
| RL34-bf | SKIP | (b)~(f)는 주입 시계와 가짜 클라이언트가 있어야 한다. `test_rl34_a_b_the_group_is_gone_before_the_server_may_close`, `test_rl34_c_fourteen_second_heartbeats_keep_the_job_even_losing_one_2xx`, `test_rl34_d_heartbeats_that_keep_getting_5xx_stop_the_job_at_g_minus_m`, `test_rl34_e_a_dribbling_server_is_cut_by_the_deadline_and_the_next_beat_goes`, `test_rl34_f_the_worker_hands_its_monotonic_clock_to_the_runner`(모두 `tests/test_worker_selfstop_m5tb.py`)와 `test_rl34_f_sigkill_follows_the_monotonic_clock_when_the_wall_clock_goes_back`(`tests/test_job_supervisor_m5tb.py`) |
| RL40 | PASS | 중계를 끊어 연결을 거부한 38.318초 동안 잡이 끝났다. 그동안 `running`, 돌아온 뒤 `succeeded`, 종료 0, 서버 `log.txt` 의 sha256 이 워커 로컬 사본과 같음, `log_complete` true, 홀드 0 |
| RL40-5xx | SKIP | 503 `log_write_failed` 와 300초 동안의 5xx 는 서버 오류 주입이 있어야 한다. `test_rl40_finish_goes_first_after_300s_of_5xx_on_the_log`(`tests/test_worker_outbox_m5tb.py`), `test_rl36_c_write_error_is_503_log_write_failed_and_truncates`(`tests/test_worker_log_offset_m5tb.py`) |
| RL15 | PASS | 신원은 macOS `ps` 로 확인. 워커 `kill -9` 뒤 일반 잡 고아가 살아 있었고, 같은 폴더로 다시 띄운 워커가 첫 claim 전에 그 그룹을 끝냈다(로그 `orphan #N: stopped process group`). 판정은 이것을 같은 워커 로그의 줄 순서로 본다. 고아 줄이 다음 잡의 `claimed #N` 줄보다 앞이어야 하고 허용치는 없다. 워커는 stderr 한 줄기에 줄마다 flush 하므로 줄 순서가 쓴 순서다. 이 실기의 워커 로그는 `orphan #18` 줄 다음에 `claimed #20` 이었다. 두 번째 실기의 옛 판정은 초 단위로 잘린 `started_at` 과 −1.0초 허용으로 견줬다. 감독자 아래 표식 잡은 그룹이 사라진 것이 확인되어 홀드 0. 워커와 감독자를 함께 죽여 리더 없는 그룹을 남기면 손대지 않고 홀드 1 |
| RL15-proc | SKIP | Linux 머신이 아니라 `/proc` 신원을 실기로 보지 못했다(`ps -o lstart=` 길은 봤다). 단위 시험 `test_rl15_linux_start_reads_field_22_after_the_last_parenthesis`(`tests/test_worker_session_m5tb.py`)가 가짜 `/proc` stat 을 읽는다 |
| RL15-reuse | SKIP | 재사용된 pid 는 신원 주입이 있어야 한다. `test_rl15_a_reused_pid_is_left_alone_and_confirmed`(`tests/test_worker_session_m5tb.py`) |
| RL1 | PASS | 표식 잡 + 분단이 G 를 넘김. 끊은 뒤 62.577초에 명령이 사라졌고(상한 G − 5 = 85) 그때 서버는 아직 `running`, `lost`(`worker_unreachable`) 사건 1번, `failed_step` · `last_step` null, 종료 3, 닫힐 때 홀드 1 → 돌아온 워커의 finish 가 홀드를 풂. 판정은 명령이 죽은 시각을 허용치 없이(차이 ≥ 0) DB 의 REAL `finished_at` 과 견주고, 이 실기에서는 죽은 시각이 28.076초 앞이었다. 두 번째 실기의 옛 판정은 잡 문서의 `finished_at`(초 단위로 잘림)에 1.0초를 봐주고 견줬고 lost 사건 수는 세지 않았다 |
| RL5 | PASS | 일반 잡 + 분단이 G 를 넘김. `lost`(`worker_unreachable`), 종료 3, 닫힌 뒤에도 프로세스가 살아 워커 로컬 로그가 늘어남, 홀드 0, 끝난 뒤 `late_result` 한 번(`succeeded`), 상태는 `lost` 그대로, `rcm wait` 의 늦은 결과 줄 |
| RL42 | PASS | 표식 잡이 유예 만료로 닫힘. 홀드 행 1(cause `worker_unreachable`), 홀드 사건 1, 같은 그룹 대기 잡은 서버 재시작 뒤에도 `queued` · `needs_action` 이고 claim 0, 다른 그룹 잡은 돎, 일반 잡 홀드 0, 돌아온 워커가 푼 뒤 대기 잡 시작 |
| RL42-gc | SKIP | `metadata_retention_days` 정리는 오래된 store 가 있어야 한다. `test_rl42_an_unconfirmed_close_holds_the_group_across_pools_restarts_and_gc`(`tests/test_irreversible_hold_m5tb.py`) |
| RL8 | PASS | 분단 중 `rcm cancel` 뒤 G 경과. 두 잡 다 `cancelling` 이었다가 `lost`(`worker_unreachable`), 종료 3(`cancelled` 도 `cancel_unconfirmed` 도 아님), 표식 명령은 끊은 뒤 62.047초에 사라짐, 돌아온 워커가 일반 프로세스를 멈추고 홀드를 풂, `late_result` 1 이하, 끝 상태 `lost` |
| RL8-a | SKIP | (a) G 안에 돌아오는 경우는 pytest e2e 몫이라 되풀이하지 않는다. `test_rl8_a_a_cancel_during_the_partition_ends_both_jobs_cancelled_after_the_return`(`tests/test_e2e_worker_grace_m5tb.py`), `test_rl8_a_*`(`tests/test_worker_selfstop_m5tb.py`) |
| RL2 | PASS | 서버 정지가 G 를 넘김. 서버가 꺼진 동안 표식 명령이 사라졌고 일반 잡 프로세스는 살아 있음, 기동 직후 일반 잡은 `running`(기동 직후 틱이 닫지 않음), 표식 잡 `lost`(`worker_self_stopped`) 한 번, 종료 3, 홀드 0 |
| RL43 | PASS | 워커 `kill -9`: (a) SIGTERM 을 따르는 명령 0.224초(상한 0.35), (b) 무시하는 명령 3.124초(grace_J 3, 범위 2.5~3.35), (d) 감독자만 `kill -9` 하면 0.888초에 명령이 사라지고(상한: 명세의 grace_J + 2초 + 측정 0.04초 = 5.04초) `failed` · 종료 −9 · 홀드 0 · 취소해도 그대로, (e) 일반 잡은 살아 있음. G 뒤 (a)(b) `lost` + 홀드, (e) 홀드 0. 감독자 아래 종료 코드 7 은 7 그대로(`rcm wait` 1), SIGKILL 은 −9. `rcm release-hold` 종료 0 뒤 막혔던 대기 잡 시작 |
| RL43-c | SKIP | (c) 감독자가 부모를 확인하기 전에 워커가 죽는 경우는 시작 지연 주입이 있어야 한다. `test_rl52_a_wrong_parent_starts_nothing_and_exits_125_with_the_marker`, `test_rl52_a_sigterm_before_the_check_starts_nothing`(`tests/test_job_supervisor_m5tb.py`) |
| RL45 | PASS | 열린 홀드 둘(대기 잡 있음 · 없음). 대기 잡은 `needs_action`, `action.code` `irreversible_hold`, `blocked_by` 가 홀드의 잡, ETA null. `/api/status` · `/api/health` 에 두 항목, `/api/health` 의 ok 는 그대로. `rcm wait --return-on-action` 종료 4, `--timeout` 종료 3, `rcm check` 경고 2줄, `rcm doctor` 줄 1 과 `--json` 의 홀드, `rcm check` · `rcm doctor` 종료 코드는 그대로. `rcm release-hold`: client 토큰 2, admin 토큰 0, 홀드 없는 잡 1 |
| RL45-web | PASS | 「Needs a look」이 두 홀드를 보이고 세 줄 + `and 1 more` 로 네 줄을 모두 센다. 다 그리지 않았으므로 「nothing else needs a look」은 없다. 영어 · 한국어 페이지 오류 0 |
| RL45-empty | PASS | 대기열이 비고 홀드가 열려 있을 때 웹은 「바로 시작」을 약속하지 않고 막힌 그룹이 시작하지 않는다고 말한다 |
| F78 | PASS | 감독자 값. 아래 「감독자 값(F78 · F83 · F84)」 |
| RL29 | PASS | 다섯 `lost` 끝: `worker_unreachable`(#22), `worker_self_stopped`(#14), `worker_replaced`(#3), `worker_restarted_without_job`(#17), `orphan_unverified`(#21). 다섯 모두 `rcm wait` 종료 3, `failed_step` null, 자기 문장 한 줄, 알림 훅 한 번 |
| RL29-web | PASS | 390px, 영어 · 한국어 페이지 오류 0, 최근 목록을 편 두 화면에 다섯 끝이 모두 사람 말로 보이고 두 화면 모두 원문 코드는 없다. 판정은 i18n.js 가 그리는 두 언어의 문장(이 실기에서 10개, 빈 문장 없음)을 각 언어 화면에서 찾고, 찾을 끝이나 문장이 없으면 UNKNOWN 이다. 두 번째 실기의 옛 판정은 영어 화면에서만 찾았다. 띠 높이 75px / 76px 는 적기만 하고 이 판정에서는 상한을 보지 않는다(상한 판정은 RL28-web) |

§7.1 의 나머지 40행(RL3 · RL4 · RL6 · RL7 · RL9 · RL11~RL14 · RL16 · RL17 · RL19~RL27 · RL30~RL33 ·
RL35~RL39 · RL41 · RL44 · RL46~RL49 · RL52~RL56)은 단위 시험이나 pytest e2e 의 몫이다. 하네스 요약에는
`SKIP e2e row, not live` 나 `SKIP unit row, not live` 로 덮는 시험 파일과 함께 나온다.

### RL34 의 값이 문턱에 붙어 있는 이유

세 번째 실기의 RL34 기본 grace 는 「gone 62.004s after the cut (threshold 62s)」였다. 첫 실기에서는
61.944초, 두 번째 실기에서는 62.167초였다. 셋 다 62초 근처라 아슬아슬하게 통과한 것처럼 보이지만,
62초는 PASS 의 경계가 아니다.

62초는 워커가 스스로 멈추는 문턱이다. 하네스는 그 값을 워커 로그 줄
`#14: no answer from the server for 62s — stopping this irreversible job before the server gives it up`
에서 읽는다. 명세의 `G − m_J` 와 같은 값이다. 여유 `m_J = T_req + grace_J + SELF_STOP_SLACK_SECONDS` 가
15 + 3 + 10 = 28 이고 90 − 28 = 62 다(§4.1, 결정 320). 명령은 이 문턱을 넘긴 첫 감시 틱에 SIGTERM 을
받으므로 문턱 근처에서 사라지는 것이 기대한 동작이다.

하네스 도우미의 판정 함수 `judge_rl34` 가 PASS 로 보는 조건은 셋이다.

1. 워커가 로그에 적은 값(`int(wgap)`)이 문턱 `G − (15 + grace_J + 10)` 이상이고 문턱 + 감시 틱 1초
   이하다. 15 · 10 · 1 은 `remote_worker.py` 의 `HEARTBEAT_DEADLINE_SECONDS` ·
   `SELF_STOP_SLACK_SECONDS` · `WATCHDOG_TICK_SECONDS` 이고 시험이 같은지 견준다. 기본 grace 면 62~63
   이고, 이번 값은 62 였다. 문턱보다 작은 값을 FAIL 로 보는 하한은 격리 확인 1라운드 뒤 더했다.
2. 중계를 멈춘 때부터 명령 프로세스가 사라질 때까지가 `문턱 + grace_J + 3` 이하다. 기본 grace 면
   68초이고 62.004초는 그보다 5.996초 앞이다.
3. 같은 시간이 `G − 5` = 85초보다 짧다. 서버가 그 잡을 닫을 수 있게 되기 전에 명령이 끝났다는 뜻이다.

그 밖에 서버 상태가 그때 `running` 이고, `summary_code` 가 `worker_self_stopped`, `rcm wait` 종료 3,
홀드 0 이어야 한다. 사실은 하네스의 단계 함수 `run_self_stop_timing` 이 모은다.

멈출 때까지 걸린 시간에는 하한을 두지 않았다. 워커는 중계를 멈춘 시각이 아니라 마지막으로 2xx 를 받은 요청을 보낸 시각
(`ok_at`)부터 센다. heartbeat 간격이 1초라서 그 시각은 중계를 멈추기 최대 1초쯤 앞일 수 있다. 그래서
중계를 멈춘 때부터 재면 문턱보다 조금 짧게 나올 수 있고, 첫 실기의 61.944초가 그 경우다. 「너무 일찍
멈춤」은 이 하네스가 판정하지 않는다. 그 경계는 주입 시계로
`test_rl34_the_watchdog_stops_an_irreversible_job_exactly_at_g_minus_m`(`tests/test_worker_selfstop_m5tb.py`)이
잠근다.

큰 grace 도 같은 식이다. 워커가 `grace_seconds 30 lowered to 13` 줄을 남겼고 문턱은 90 − (15 + 13 + 10)
= 52 다. 세 번째 실기의 51.945초는 상한 52 + 13 + 3 = 68초 안이다. 문턱 52초보다 0.055초 짧은 것은 위의
`ok_at` 때문이다. 워커가 로그에 적은 값은 52 로 하한과 같다. 두 번째 실기의 값은 52.112초였다.

### 감독자 값(F78 · F83 · F84)

§5 의 PR4 행과 §6 의 19 는 감독자 값을 이 하네스로 한 번 더 재라고 한다. 감지와 grace 는 RL43 의
워커 `kill -9` 에서 한 번씩 쟀고, 기동 · `ps` · `/usr/bin/true` 는 서버 없이 이 머신에서 쟀다(기동과
`/usr/bin/true` 는 5회, `ps` 는 20회의 중앙값). 감독자 CPU 는 RL43 (d)의 30초 잡에서 쟀다.

| 값 | 명세의 실측 | 이번 실기 | 하네스 상한 |
|---|---|---|---|
| 부모 SIGKILL → SIGTERM 을 따르는 명령 소멸(폴링 0.25초) | F78: 0.07~0.31초 | 0.224초 | 0.35초 |
| SIGTERM 을 무시하는 명령 소멸 − grace_J(grace 3) | F78: 3.02~3.30초 | 3.124초 − 3 = 0.124초 | 0.36초 |
| 30초 잡 동안 감독자 CPU | F78: 0.021초 | 0.040초 | 0.1초 |
| `python -I <파일 경로>` 기동 | F83: 0.02초 | 0.0227초 | 0.1초 |
| `LC_ALL=C ps -A -o pid= -o pgid=` 한 번 | F84: 중앙값 0.0125~0.0131초, 최대 0.0148초 | 중앙값 0.0144초, 최대 0.0170초 | 0.05초 |
| 감독 아래 `/usr/bin/true` 의 끝 지연(기동 비용을 뺀 값) | F78: waitpid 스레드라 0 에 가까움 | 0.0222 − 0.0020 − 0.0227 = −0.0025초 | 0.1초 |

세 번째 실기는 부하 약 7 에서 돌았다. 감지와 grace 는 명세가 잰 범위 안이었다. 감독자 CPU, 기동,
`ps` 는 명세 값보다 조금 느렸고(CPU 는 두 배) 모두 하네스 상한 안이다. 부하가 약 3 이던 두 번째
실기에서는 CPU 0.020초, 기동 0.0194초, `ps` 중앙값 0.0116초로 명세 값과 같거나 조금 빨랐다.
Linux 에서 `ps -A -o pid= -o pgid=` 의 꼴을 확인하는 일(§6 의 19 끝 문장)은 Linux 머신이 없어 하지 못했다.

## 첫 실기의 하네스 결함 둘과 고침

첫 실기에서 RL50 · RL10 · RL51 · RL18 · RL28 · RL28-web · RL28-cancel · RL34 · RL40 은 PASS 였다.
그 뒤 claim-lost 단계 다음의 RL15 · RL1 · RL8 · RL2 · RL43 이 모두 `UNKNOWN jobs did not start` 였고
RL29 는 UNKNOWN, RL29-web 은 FAIL 이었다. 원인은 둘 다 하네스에 있었고 제품 결함은 아니었다.

첫째 결함은 부하 게이트였다. RL15 는 레인이 둘인 워커 하나에 잡 둘을 넣는다. 원격 워커의 둘째 레인은
서버의 부하 게이트(M5f, `admission`)를 지나야 하는데, 하네스의 `server.toml` 은 `admission` 을 기본값
`"load"` 로 두었다. 그때 이 Mac 은 다른 프로젝트의 게이트를 돌리고 있었고(부하 12~19), 둘째 레인은
내내 `held` 였다. 그래서 둘째 잡이 시작하지 않았다. 고친 뒤 하네스는 `admission = "always"` 를 쓴다.
하네스는 부하 게이트를 시험하지 않는다. 게이트 자체는 의도한 제품 동작이다.

둘째 결함은 단계 사이의 정리였다. 단계 사이 정리 함수 `reset_world` 는 대기열에 남은 잡을 취소하지
않았다. RL15 에서 시작하지 못한 잡이 남았고 다음 단계의 첫 워커가 그 잡을 집었다. 그 뒤 단계마다 잡이
하나씩 밀려 RL1 · RL8 · RL2 · RL43 도 「jobs did not start」가 되었다. 고친 뒤 `reset_world` 는 워커를
멈춘 다음 `/api/status` 의 대기 · 예약 잡을 모두 취소하고, 끝날 때까지 기다린 뒤 `reset.log` 에 적는다.
열린 홀드를 admin 토큰으로 푸는 일은 전부터 했다. 잡이 시작하지 않은 단계는 이제 빈 UNKNOWN 대신 잡마다 상태, 돈 워커,
대기 사유, 그 단계 워커의 레인 상태를 한 줄로 적는다. 원인이 하네스 쪽이면(다른 단계의 워커가 잡을
집었거나, 하네스가 끄는 부하 게이트에 레인이 막힘) FAIL 이다.

RL29-web 의 FAIL 은 두 결함의 결과이기도 했지만 원인이 하나 더 있었다. 웹의 최근 목록은 서버의
`recent_count`(기본 8)만큼만 담는데, 전체 실기는 잡을 서른 개 남짓 끝내므로 앞 단계의 `lost` 잡이
화면에 없었다. 고친 뒤 하네스는 `recent_count = 100` 을 쓰고, 화면 글을 읽기 전에 「show N more」를
편다.

이 고침은 커밋 `06a764a`(빨간 시험 7개), `c0f1be3`(빨간 시험 1개), `2f3c435`(고침)에 있다. 고친 뒤
`--only` 로 다시 돌리자 결함 셋이 더 드러났고 `8c308d5` · `144cca0`(빨간 시험)과 `02b6bc3`(고침)으로
고쳤다.

- RL15: 하네스가 고아가 끝난 시각을 자기 폴링으로 재서, 고아가 다음 잡보다 먼저 끝났는데도 「0」으로
  보았다. 이제 워커 로그 줄의 시각을 쓴다.
- RL45-web: macOS 의 `/bin/bash` 3.2 는 `$( )` 안 `case` 패턴의 `)` 를 치환의 끝으로 읽는다. 사실 셋이
  비었다. `[[ == ]]` 로 바꾸고 정적 시험이 그 꼴을 막는다.
- F78: `--only RL43` 일 때 이 머신 값을 재는 함수가 불리지 않아 UNKNOWN 이었다.

두 번째와 세 번째 실기의 `reset.log` 에는 「cancel leftover」 줄이 없다. 단계 사이에 남은 잡이 없었다는 뜻이다.
홀드 풀기 줄은 둘이다(RL15 의 리더 없는 그룹 #21, RL43 의 무시하는 명령 #31). 둘 다 판정을 낸 뒤
다음 단계를 위해 하네스가 푼 것이다.

## 명세 체크리스트와 시험 행 대조

§6 진행 체크리스트에서 PR4 가 맡은 행이다.

| §6 | 할 일 | 이 보고서가 덮는 곳 | 상태 |
|---|---|---|---|
| 10 | PR4 실기 수용과 보고서(감독자 값 재측정 포함) | 「단계별 판정」, 「감독자 값」 | 고친 판정으로 돌린 셋째 실기 PASS 22 · FAIL 0 · UNKNOWN 0 |
| 13 | 릴리스 묶음: PR1 · PR2 · PR3 이 모두 dev 에 머지되기 전에는 릴리스를 만들지 않는다. CHANGELOG `[Unreleased]` 정리 | 대상 판이 PR1 · PR2 · PR3 이 모두 머지된 `37e7b98` 위다. CHANGELOG `[Unreleased]` 에 하네스 줄을 더했다 | 조건은 채워졌다. 버전 올림과 main PR 은 §6 의 14(메인 세션) |
| 19 | 감독자 4판 값 재측정: `-I` 파일 경로 기동 · `ps` 쓸기 비용, Linux 면 `ps` 꼴 | 「감독자 값」의 F83 · F84 행 | macOS 는 쟀다. Linux 는 머신이 없어 남음 |

§5 의 PR4 행과 PR2 · PR3 검증이 PR4 로 넘긴 행, 곧 이 PR 몫의 §7.1 행이다. §7.1 에는 `(live)` 표시를 단
행이 없다. RL28 · RL29 의 화면은 pytest 와 node 시험이 JSON 과 문구를 잠그므로 일부러 달지 않았다고
명세의 `LIVE_FORBIDDEN` 줄이 적는다. 그래서 이 표는 하네스 단계 표에서 종류가 `live` 인 행을 모두 든다.

| 행 | 어디서 왔나 | 하네스 단계 | 판정 |
|---|---|---|---|
| RL1 | §5 PR4 우선 | `run_partition_past_g` | PASS |
| RL2 | §5 PR4 우선 | `run_server_stop_past_g` | PASS |
| RL10 | §5 PR4 우선 | `run_fence` | PASS |
| RL15 | §5 PR4 우선(Linux 는 있으면) | `run_orphan` | PASS, `/proc` 는 SKIP |
| RL34 | §5 PR4 우선 | `run_self_stop_timing` | PASS, (b)~(f) SKIP |
| RL40 | §5 PR4 우선 | `run_finish_offline` | PASS, 5xx 몫 SKIP |
| RL42 | §5 PR4 우선 | `run_partition_past_g` | PASS, 보존 기간 정리 SKIP |
| RL43 | §5 PR4 우선(`kill -9` · 감독자 · `rcm release-hold`) | `run_supervisor_only`, `run_kill_worker` | PASS, (c) SKIP |
| RL50 | §5 PR4 우선 | `run_hb_order` | PASS |
| RL51 | §5 PR4 우선 | `run_sweep` | PASS |
| RL5 | PR2 검증이 넘김(늦은 finish 가 `late_result` 한 번) | `run_partition_past_g` | PASS |
| RL8 | PR2 검증이 넘김 | `run_cancel_in_partition` | PASS, (a) SKIP |
| RL18 | PR2 검증이 넘김(비밀이 로그 · SSE · 웹에 없음) | `run_secret` | PASS |
| RL28 | PR3 검증이 넘김(화면, `rcm wait` 줄, `--timeout`) | `run_grace_view` | PASS, 웹 PASS, 취소 PASS |
| RL29 | PR3 검증이 넘김(다섯 끝, 화면) | `run_claim_lost`, `run_lost_ends` | PASS, 웹 PASS |
| RL45 | PR3 검증이 넘김(doctor, 「확인이 필요한 작업」 네 줄 이상) | `run_kill_worker` | PASS, 웹 PASS, 빈 대기열 PASS |
| F78 | §5 PR4(감독자 값) · §6 의 19 | `run_supervisor_only`, `run_kill_worker` | PASS |

## 남은 것

- Linux 머신에서 한 번 돌리는 몫이 남았다. §5 의 PR4 행은 Linux 머신이 있으면 RL15 · RL31 의 `/proc`
  신원과 RL43 을 그 머신에서 돌리라고 한다. §6 의 19 는 그 머신의 `ps -A -o pid= -o pgid=` 꼴을 보라고
  한다. 이 Mac 에서는 RL15 가 macOS `ps` 길로만 PASS 였다.
- RL34 의 측정값은 워커의 멈춤 문턱에 붙어 있지만 PASS 상한(68초, 85초)과는 넉넉히 떨어져 있다. 「너무
  일찍 멈춤」은 하네스가 판정하지 않고 단위 시험이 잠근다. 자세한 이유는 위 RL34 절에 있다.
- RL29-web 은 띠 높이를 적기만 하고 상한을 보지 않는다. 상한 80px 판정은 RL28-web 이 한다.
- 웹 화면 스크린샷과 화면 글은 증거 폴더에만 두고 저장소에는 넣지 않았다. 웹 머리에 이 머신의 호스트
  이름이 찍히기 때문이다.
- `lost` 이면서 `summary_code` 가 없는 잡이 둘 있다(#1 `rl50-ship`, #29 `rl2-plain`, 둘 다 「worker
  stopped」, 종료 −15). 단계 뒤 정리에서 하네스가 워커에 SIGTERM 을 보내 생긴 것이고, 명세 W8 이 바꾸지
  않기로 한 기존 동작(「오늘처럼 멈춤, finish `lost`(`worker stopped` 문장)」)이다. 판정에는 들지 않는다.
- 하네스를 지으며 명세에 없던 것을 정한 곳(도우미 파이썬의 자리, RL29 의 다섯 끝을 만드는 법, 단위
  시험 몫의 SKIP 줄, 시험 서버의 `admission` · `recent_count`, 시작하지 않은 잡의 판정)은 각 라운드의
  결과에 적었고 오너 확인을 기다린다.

## 증거 파일

세 번째 실기의 증거 폴더는 `<HOME>/.local/share/rcm-devtest-m5tb/live-ed0c12f2/out` 이다. 아래 이름은
그 폴더 기준의 상대 경로다. 맨 아래 세 줄은 메인 세션이 받아 둔 세 실기의 콘솔 출력이고
`<HOME>/.local/state/rcm-dev-batch/` 에 있다. 하네스는 출력에서 홈 폴더를 `<HOME>` 으로 바꿔 적는다.
값은 `shasum -a 256` 이다. `judge.err` 와 `rl18-logs.err` 는 비어 있다. 판정 함수가 예외 없이 끝났고
`rcm logs` 가 stderr 에 아무것도 쓰지 않았다는 뜻이다. 두 번째 실기의 증거 폴더는 `live-73c8823e` 로
남아 있다.

```text
5c3598f90a2b709e294c661342ee96f18e41bd9a169754993e93d3658f3cccc5  summary.txt
e446880b472ef1b009810c913026619e2ba15daff672e37dd69093469fd91858  timeline.txt
373b0fa695eb83bcb1d77abefe740882f08479b813a2104e82fc6326dda91952  jobs.json
e844ee9873e40815c6e00a20a80b5c1f30fc94b7f08414d027a09b1ad270855e  lost.tsv
fdd539aa77bf669b1d056963b981195d00245d38bfb309b35adc919e8b8a1f37  reset.log
f89a49bdc82e2f16c4b87cbfa5c14c33a4c455a49f4be7d49770f150ec5d74ef  relay.log
44f5fcd6dbfb028596cb6e2559e57e0f68e0d9b7562bf73f17d1d629d889439d  env.txt
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  judge.err
bb5b72849c8cae1c883b714213a8c0e7f41bbcb2920a51cd1b98b4b339baa500  facts/F78.txt
96886a63da2460f0c11654d24619e77c8c7af77ab400808381f9d02c5056cdf2  facts/RL1.txt
6f41837bcb679345ca31588fa3915a0254988f49ac99dae746304eebe3c8567a  facts/RL10.txt
da975e2d5ffc9c8460100b7cfa2d1367c33e104cddf614f27f1f3c5dc15d7d4b  facts/RL15.txt
6d645bd6dbd53f786d9b6e181a9844c5ac76b68bb8069b5d2653c16cef1abd2d  facts/RL18.txt
db39bc2f8382c3d4900fcff54bd47335c1ea396d31fa799d74788ef24b6c1e2a  facts/RL2.txt
f3befdb87bedbe53a49b0ee5c888056f5e735478f495f64ce67d5ddc26227971  facts/RL28-cancel.txt
263a457c8d35b58af0d5f1c65d228a60fe52d8bfe95d14802e479c4d59d0afc2  facts/RL28-web.txt
955697ef40058d7a195839d8c30971a640bc83595a416c01943435f17b48b473  facts/RL28.txt
14bac4682edbeda5027a8c6211951771c7f5ffe5e4573e97e7380aa90e219958  facts/RL29-web.txt
2fbfe5ee4e8af60750d414bdbb35b6dd31463054b66c411589a94aec79168244  facts/RL29.txt
cf4e26026104b120e651244e376cf574194a9254a3d767737c8f1d4ad6c21499  facts/RL34.txt
1af73d30892292118b99e9ac1fe7f89779e16a565b7e16f23860d63d9b4ab65f  facts/RL40.txt
3aa681b9d58f1c67c55c71a7b766a0a1af69198d921ddf5c3cfcedeafa82e6c9  facts/RL42.txt
30a64ef1b409f8e9745a31792ee8811bca8c9bc40f85d607f419c94c0e102c3b  facts/RL43.txt
4950959da7892a91b220d596820711769eef72765ad20533e7eb04eec5747bb9  facts/RL45-empty.txt
0ec9bfb5b3add91f1c908ecc6c2313c137d530989800f57d150bae03cff32e51  facts/RL45-web.txt
3dcf8a5410212f1918aa9774cbe1e22f27fe4e3e358aa587f5db02287bab221a  facts/RL45.txt
7b0552671c9dc38d4b355b11ffcc948b66992253b0f3a01144a527cd23192649  facts/RL5.txt
de1159791667006e3659ddfed4c72cc4cb4b5c82c8d5f20bc57d86eda14d1449  facts/RL50.txt
f96a6466cbcd520c5664ecc1df4403720d5393731f8d695698b1d6c5f945f027  facts/RL51.txt
7358bb479a60c73db888dbb3a71a9081b3e8e618c0d73e3c0c4991b9df3d4ab8  facts/RL8.txt
095fadebd0e4fb1af5c37c81151de34262e20e025a407fab5bb46e8c2a9657c0  web/rl28-en.png
251ed5144f64dac23f265ec2196f50072ef7cf4935d2a80a0b83cacab64bcc87  web/rl28-ko.png
0c1c00415a357f727589274a8418312de5e7c6c7e7ccb92c8898de140de9f151  web/rl29-en.png
af5fba0c6734c8f756841a13260b204561f37374ce13212b7caf7978f2483142  web/rl29-ko.png
5ab08d52eacb7fb96f99231779ebffa66aa807120a9300a9a7743e3aae87790e  web/rl45-empty-en.png
0c9f5909dfb0bc6a5fad67c2b0c2cb6f7043575ff27aea35c392345bf47f09db  web/rl45-empty-ko.png
b116e728fd0d9a4197c3e01678691c444e1e70b9924de3591391e5883027bd0b  web/rl45-en.png
bc6c6f673099130000b0d67170624ca47ab7859a05114972fc86c266cc0a46b4  web/rl45-ko.png
097ff3e8f5b71bd3276611781089fa696cbf21831a2f8d639e5668cd9b37c017  web/rl28-en.txt
1309adde01260f6f151c36ea86924b88e519080b5b1ac7290d4ce4e64bdbd6b3  web/rl28-ko.txt
6181b43819fe01e06cc38bcfea083931dfd6821dcc8454d1db3a32d6c1f9410b  web/rl29-en.txt
54abe65aa4ea57fb40a468d62cd026830b67696e01821f91b29de957ed24d143  web/rl29-ko.txt
84a9f6dcc07e5d779bd2d7490f6192b72352d8aa12e5080363235204f391cf5d  web/rl45-empty-en.txt
8a5cb4c23a29bbdaf58fc657907c9432cb8b0f4edb206f21b88d89162b0544d5  web/rl45-empty-ko.txt
79b45b52dfb75f5f9f224f3791807908603ddeaed6f538f669047a13388ebc0d  web/rl45-en.txt
c7c4e6621f18e5cedc0d900432c8644ad4c6ec4d423b5c563e0461aa9277d105  web/rl45-ko.txt
6196c73c87be9f221c398a08bccf437abe1408ff9ea0cfea1bc6053c28c4b193  rl18-logs.txt
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  rl18-logs.err
6196c73c87be9f221c398a08bccf437abe1408ff9ea0cfea1bc6053c28c4b193  rl18-weblog.bin
abec46bef6f788cd650a493a95177cb7849e04a8d91a0beb1f044db548cdaf5f  rl18-job.json
a1cbba3896c4507bdaec659f912dc2e836943090373c8bae9c57e98d101bc87e  rl18-sse.bin
0105e1c714a9cdc7dca0a6f7d34cb28ee96ac1f8d36ca8ea0d6ea7ffe95fa960  m5tb-live-20261127-1151.txt
4945b9916b48b46f78c7ed2f3002cde5f0872370b2dd9bd6d552dd36a2828ffb  m5tb-live-20261127-1043.txt
b27be5cb0d5855de8575eca9d88174e42c67ffd9b9bed1858faf7aa2e0595522  m5tb-live-20261127-0957.txt
```

폴더에는 이 밖에 단계별 `rcm wait` 출력(`rl*-wait*.json`, `.err`), 워커 로그(`worker-<이름>-<n>.log`),
서버 로그(`server.log`), `rcm top` · `rcm eta` · `rcm check` · `rcm doctor` 출력이 있다.
