# M5b-4 테스트 시나리오 B — `rcm check` 의 `pools` 행 (2026-09-07)

`docs/m5b4-workplan.md` §3(`rcm check` — 행 `pools`)과 §5 표의 B 행을 `tests/test_cli_m5b4.py`(함수 7 · 실행 8건)로
옮긴 것이다(test-first, 역할 B). `src/` · 기존 테스트는 건드리지 않았다. `rcm top` 의 풀 헤더 · 워커 접기와 웹 Host
카드는 A 의 몫이다.

공통: test_cli_m4 처럼 `main(["check"])` 를 in-process 로 부르고 `RCM_SERVER`/`RCM_TOKEN`(alice) 만으로 서버를
가리킨다. 서버는 `test_worker_api.WorkerServer`(진짜 `/worker/*` + 주입 시계 · 로컬 워커 스레드 없음 · janitor 없음).
워커의 down 은 `srv.clock.advance(TIMEOUT + 1)` 로 만든다 — down 은 읽을 때 `last_seen_at` 로 계산되므로 janitor 가
없어도 된다. HOME 은 tmp, `XDG_CONFIG_HOME` · `RCM_*` 는 지운다 — 서버 설정이 없으니 `data dir` · `git` 행은 안 나온다.

도우미:
- `rows(out)` — 출력을 `(status, name, detail)` 목록으로 파싱한다. cmd_check 의
  `{'ok ' if ok else 'FAIL'}  {name:<13} {detail}` 고정폭 그대로이고, 행이 아닌 줄이 stdout 에 있으면 실패한다.
- `row(out, name)` — 그 행의 `(status, detail)`, 없으면 None. 같은 이름이 둘이면 실패.
- `names(out)` — 행 이름 순서. `not_ok(out)` — FAIL 행 이름 목록(「다른 행은 그대로」를 한 번에 잠근다).
- `check(capsys)` — `rcm check` 를 부르고 stderr 가 비었음을 확인한다.

각 시나리오는 CLI 를 부르기 전에 서버 쪽 사실(`server.workers[]` 의 레인 상태 · `/api/health` 의
`pools_without_workers`)을 먼저 단언한다 — 빨간 이유가 픽스처가 아니라 「없는 행」임을 분명히 하려고.

## 잠그는 문자열

| 상황 | `pools` 행 상세(정확히 이 문자열) | 상태 · 종료 코드 |
|---|---|---|
| 원격 워커 없음 · 로컬 1 레인 | `default (1 lane)` | ok · 0 |
| 원격 워커 없음 · 로컬 2 레인 | `default (2 lanes)` | ok · 0 |
| build-02(linux · 1 레인) idle | `default (1 lane) · linux (build-02/1 idle)` | ok · 0 |
| build-02 가 잡 #N 실행 중 | `default (1 lane) · linux (build-02/1 busy #N)` | ok · 0 |
| build-02 down(heartbeat 가 `worker_timeout_seconds` 초과) | `default (1 lane) · linux (build-02 down)` | **FAIL · 1** |
| build-02 down · build-03 idle(같은 풀) | `default (1 lane) · linux (build-02 down · build-03/1 idle)` | ok · 0 |
| 서버에 못 닿음 | (행 없음) | `server` · `token` · `presets` FAIL · 1 |

행 자리: `names(out).index("pools") == names(out).index("presets") + 1`, `timezone` 은 그 뒤.

## `tests/test_cli_m5b4.py`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 원격 워커 없음 → `default (1 lane)` · 로컬 2 레인(`WorkerServer(lanes=2)`) → `default (2 lanes)` — N 은 `server.lanes` | `test_pools_row_shows_only_the_default_pool_without_remote_workers[1-lane · 2-lanes]` | §3 「원격 워커가 하나도 없으면 행은 `default (1 lane)` 만」 |
| 2 | 행 순서 — `pools` 는 `presets` 바로 다음 · `timezone` 은 그 뒤 · 앞 4행(`python server token presets`)은 오늘 그대로 | `test_pools_row_comes_right_after_presets` | §3(행 자리) · M4 §2 의 `rcm check` 표 |
| 3 | linux 풀 워커 idle → ` · linux (build-02/1 idle)` · 자리도 다시 확인 | `test_pools_row_lists_an_idle_remote_worker` | §3 「풀마다 ` · linux (build-02/1 idle)`」 |
| 4 | `lin` 잡을 claim 해 실행 중 → ` · linux (build-02/1 busy #N)` | `test_pools_row_shows_the_running_job_of_a_busy_remote_worker` | §3 · §1 머리줄 필 표기(M5b-2 `build-02/1 busy #511`) |
| 5 | 풀의 워커 전부 down → ` · linux (build-02 down)` · FAIL · 종료 1 · 다른 행은 전부 ok(`/api/health.ok` 는 true 그대로 · `pools_without_workers == ["linux"]`) | `test_pools_row_fails_when_every_worker_of_a_pool_is_down` | §3 「FAIL 조건: 어떤 풀의 워커가 **전부** down(`pools_without_workers`)」 |
| 6 | 같은 풀에 down 하나 · idle 하나 → `linux (build-02 down · build-03/1 idle)` · ok(살아 있는 워커가 있다) · down 은 접지 않고 이름순 | `test_pools_row_is_ok_while_one_worker_of_the_pool_is_still_alive` | §3 FAIL 조건의 역 · §1 「down 은 접지 않는다 — 항상 보인다」 |
| 7 | 서버에 못 닿음(닫힌 포트) → 행 없음 · 행 목록은 `python server token presets` · 종료 1 | `test_pools_row_is_absent_when_the_server_is_unreachable` | §3(status 를 못 읽으면 행이 없다) · M4 §2(오늘 동작) |

## 열어 둔 것(명세에 없어 잠그지 않음)

- 레인이 둘 이상인 원격 워커의 표기 — `build-02/1 idle · build-02/2 busy #N` 인지 워커 단위로 접는지.
- 기본 풀에 등록한 원격 워커(`pool = default`)가 `default (1 lane)` 뒤에 어떻게 붙는지.
- 워커가 5개를 넘을 때의 접기(`+N workers`) — §1 은 `rcm top` 머리줄만 말한다.
- `/api/status` 는 되는데 `/api/health` 만 실패할 때 행의 상태.

실행: `python -m pytest -p no:cacheprovider tests/test_cli_m5b4.py` — 구현 전 8건 중 7건 빨강(시나리오 7 은 오늘
동작이라 초록). 빨간 이유는 모두 「`pools` 행이 없다 / 종료 코드 0」(단언 메시지에 출력 전체가 찍힌다) — 서버 쪽
전제 단언은 전부 통과했다.
