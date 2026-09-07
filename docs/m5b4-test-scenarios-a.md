# M5b-4 테스트 시나리오 A — `rcm top` 풀 라벨·필 접기 · 웹 Host 카드 (2026-09-07)

`docs/m5b4-workplan.md` §1(`rcm top`) · §2(웹) 과 §5 표의 A 행을 `tests/test_render_m5b4.py`(터미널 렌더 18 함수 · 파라미터 포함 19건 — 오늘 10건 빨강 · 9건은 불변 잠금이라 초록) ·
`tests/web/hosts_pools.test.js`(순수 함수 `rcm.hostCards` 16건) · `tests/test_web_browser.py`(브라우저 회귀 1건 추가)
로 옮긴 것이다(test-first, 역할 A). `src/` · 기존 테스트 파일의 기존 줄은 건드리지 않았다(`test_web_browser.py` 는
끝에 테스트 하나만 덧붙였고 그 테스트 안에서만 쓰는 이름은 함수 안에서 들여온다). `rcm check` 의 `pools` 행은 B 의 몫이다.

공통: 순수 함수는 `test_render_m5.doc()`(스키마 v1 · 기본 풀 잡 2) 에 `test_render_m5b.linux_pool` 로 linux 풀을,
`test_render_m5b2.remote` 로 원격 워커 항목을 얹는다. 웹은 `tests/web/fixtures/status-*.json` 에 풀·표본을 붙인다.
브라우저는 진짜 서버(in-process · 로컬 워커 off · `FreshStubSampler` 로 로컬 표본) + 워커 토큰 `build-02` 로
`/worker/register`(pool linux) · `/worker/heartbeat`(`test_worker_api.SAMPLE`) 를 보낸 상태를 headless Chrome(CDP) 으로 연다.

잠근 문자열(구현이 그대로 내야 한다):
- 원격 빈 풀 헤더: `queue — empty (pool linux)` · `queue — empty (pool linux · no workers)` ·
  `queue — unavailable: database locked (pool linux)` · `queue — empty (pool linux) · paused`.
- 기본 풀(불변): `queue — empty (rcm run <preset> starts immediately)` · `queue — empty but paused/no worker — nothing will start` ·
  `queue — unavailable: database locked`.
- 머리줄 접기: `… · build-02/1 idle · build-03/1 idle · build-04/1 idle · build-05/1 idle · build-06/1 idle · +2 workers`(7개 idle) ·
  `+1 workers`(6개) · 5개 이하는 꼬리 없음 · down 필은 언제나 보인다.
- 웹 카드 제목: 기본 풀은 `macmini`(= host.name, 오늘 그대로) · 원격은 `build-02 · pool linux`.

## `tests/test_render_m5b4.py` — `rcm top`

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 1 | 잡 없음 + lanes 1·2 인 원격 풀 → 헤더 `queue — empty (pool linux)`, 기본 문구(`starts immediately`) 없음 | `test_empty_remote_pool_with_workers_names_the_pool` | §1 L9 |
| 2 | lanes 0 → `queue — empty (pool linux · no workers)` 그대로(M5b-1) | `test_empty_remote_pool_without_workers_is_unchanged` | §1 L9 |
| 3 | queue null → `queue — unavailable: database locked (pool linux)`; lanes 0 이어도 `(pool linux` 로 시작 | `test_unavailable_remote_queue_names_the_pool` | §1 L9 |
| 4 | 서버 paused + 원격 빈 풀(lanes 1) → `queue — empty (pool linux) · paused`, 머리줄 `PAUSED by` 는 M5b-2 그대로 | `test_paused_server_marks_the_empty_remote_pool_with_a_paused_suffix` | §1 L10 |
| 5 | paused + lanes 0 → `(pool linux · no workers)` 로 시작하고 `nothing will start` 를 빌리지 않는다(정확한 꼬리는 잠그지 않음) | `test_paused_server_with_no_workers_keeps_the_no_workers_label` | §1 L10 |
| 6 | 로컬 레인 down + 두 풀 모두 빈 큐 → 기본 `…nothing will start` · 원격 `queue — empty (pool linux)`(로컬 down 은 원격 사정이 아니다) | `test_local_lanes_down_do_not_change_the_remote_pool_header` | §1 L9 (파생) |
| 7 | 원격 블록의 나머지 줄 — `recent — no completed jobs yet` · `medians: …` · `host — no sample yet` 은 라벨 없이 그대로 | `test_remote_pool_header_is_the_only_line_that_changes` | §1 L11 |
| 8 | 잡 있는 원격 풀은 `queue — 3 (pool linux)`(M5b-1) | `test_remote_pool_with_jobs_keeps_the_m5b1_header` | §1 L9 |
| 9 | 기본 풀 빈 큐·paused·로컬 down·조회 실패 네 줄이 오늘과 바이트 단위로 같다 | `test_default_pool_empty_lines_are_byte_identical_to_today` | 머리말 「바꾸지 않는 것」 |
| 10 | 빈 원격 풀이 옆에 있어도 기본 풀 절(첫 `queue — ` 줄부터 두 번째 `queue — ` 줄 앞까지 — `split_sections`, 풀 라벨에 기대지 않는다)은 풀 하나일 때와 같다(잡 있음·없음 둘 다) | `test_default_pool_section_is_unchanged_next_to_an_empty_remote_pool` | 머리말 |
| 11 | 풀 하나 GOLDEN 불변(`doc()` · `local_doc()`) | `test_one_pool_golden_is_unchanged` | 머리말 |
| 12 | 원격 idle 7개 → 머리줄 전체가 `… worker busy #412 · build-02/1 idle · … · build-06/1 idle · +2 workers`, build-07·08 없음, 본문 GOLDEN 과 같음 | `test_seven_idle_remote_workers_fold_after_the_first_five` | §1 L12 |
| 13 | 5개 → 전부 보이고 `workers`·`+` 없음 | `test_five_remote_workers_are_not_folded` | §1 L12 |
| 14 | 6개 → `build-06/1 idle · +1 workers` 로 끝난다 | `test_six_remote_workers_fold_exactly_one` | §1 L12 |
| 15 | 앞 5개 안의 busy 필은 `build-02/1 busy #511` 그대로, 꼬리 `+2 workers` | `test_busy_pills_inside_the_first_five_keep_their_job_number` | §1 L12 |
| 16 | 7번째가 down → `build-08/1 down` 보이고 idle build-07 이 접혀 `+1 workers`, 필 6개, `DOWN:` 없음 | `test_down_remote_worker_is_never_folded` | §1 L12 |
| 17 | idle 5 + down 2 → down 둘 다 보이고 접힌 게 없어 꼬리 없음 | `test_down_pills_are_all_shown_even_when_several_are_past_the_fifth` | §1 L12 |
| 18 | 꼬리 `+2 workers` 다음이 `PAUSED by …` → `cache 12 blobs …` → `pools 2` 순서 | `test_folded_tail_comes_before_paused_cache_and_pool_totals` | §1 L12 · M5b-2 머리줄 순서 |

## `tests/web/hosts_pools.test.js` — `rcm.hostCards(status)`

| # | 시나리오 | 테스트 | 명세 |
|---|---|---|---|
| 19 | `rcm.hostCards` 가 함수다(없으면 여기서 떨어진다 — 예상된 빨강) | module contract | §2 L17 · §5 표 |
| 20 | main 픽스처 → 카드 1 · `title === "macmini"`(= host.name) · `pool === "default"` · `host` 는 그 표본 | default pool: main fixture | §2 L17 「기본 풀 로컬 카드는 오늘 그대로」 |
| 21 | 기본 풀에 source worker 표본이 들어와도 제목은 이름뿐(`· pool default` 없음) | default pool: no suffix | §2 L17 |
| 22 | hosts [] (empty · single-lane · paused-down) → [] | default pool: hosts [] | §2 L17 |
| 23 | hosts null (errors) → [] · 예외 없음(띠는 DOM 층) | default pool: hosts null | §2 L17 |
| 24 | linux 표본 하나 → `["macmini", "build-02 · pool linux"]`, pool linux, host 는 그 표본(source worker) | remote: linux worker sample | §2 L17 |
| 25 | 같은 풀 표본 둘 → 배열 순서대로 두 카드 | remote: two samples | §2 L17 |
| 26 | 원격 풀 둘(windows → linux) → 풀 순서, 로컬 먼저 | remote: two pools | §2 L17 |
| 27 | 원격 풀 hosts [] → 카드 없음 | remote: hosts [] | §2 L17 「표본이 없는 원격 풀은 카드를 만들지 않는다」 |
| 28 | 원격 풀 hosts null · 키 없음 → 카드 없음 · 예외 없음 | remote: hosts null | §2 L17 |
| 29 | 기본 풀 hosts null + linux 표본 → linux 카드만 | remote: default null + linux | §2 L17 |
| 30 | 기본 풀 표본 없음 + linux 표본 → linux 카드만 | remote: empty + linux | §2 L17 |
| 31 | lanes 0 이어도 표본이 있으면 카드(표본이 기준) | remote: lanes 0 with a sample | §2 L17 |
| 32 | 제목은 평문 — 풀 이름 `a<b` 가 escape 되지 않는다 | remote: plain text | 웹 순수 함수 관례(pools.test.js) |
| 33 | status null·{}·pools []·pools null → [] | robustness: no status | 관례 |
| 34 | 입력 불변 | robustness: no mutation | 관례 |

## `tests/test_web_browser.py` — 추가 1건 (Chrome 없으면 skip)

| # | 시나리오 | 테스트 함수 | 명세 |
|---|---|---|---|
| 35 | 로컬 표본(`macmini`) + linux 워커 `build-02` heartbeat 표본 → `/api/status` 에 두 풀·`source: "worker"` 확인 → Chrome: `#host .hostcard .hn`(`.age` 제외) 제목 목록에 `macmini` 가 `build-02 · pool linux` 보다 앞 · Host 절 글자에 SAMPLE 의 CPU(12%/13%) · `#queue` 밖 `.pool-h` 0개 · `#recent` innerText 에 `POOL LINUX` 없음 · textContent 에 `pool linux`·`no host sample` 없음 · 화면 어디에도 `NO HOST SAMPLE`·`NO WORKERS`·`undefined`·`NaN` 없음 | `test_remote_worker_sample_is_a_host_card_and_recent_has_no_pool_host_header` | §2 L17 |

## 명세와 코드 사이의 메모

- §1 L12 의 예시 `build-02/1 busy #511 · build-03/1 idle · +4 workers` 는 필 2개 + 4 = 6개라 「5개를 넘으면 접는다」와
  안 맞는다. 코디네이터 지시대로 **앞 5개 + `+N workers`(N = 접힌 수)** 로 잠갔다(#12–#15).
- down 필의 **위치**는 잠그지 않았다 — 7번째에 두면 「앞 5 슬롯에 down 포함」·「idle 5 + down 전부」 두 해석이 같은
  결과(build-07 접힘 · `+1 workers`)를 낸다(#16).
- paused + lanes 0 의 정확한 꼬리(`· paused` 를 붙이는지)는 §1 L10 이 정하지 않아 접두만 잠갔다(#5).
- 오늘 `render_pool` 은 원격 빈 풀에서 로컬 레인 down 을 보고 `…nothing will start` 를 낸다 — §1 L9 「언제나 풀 이름」에서
  파생해 원격 헤더는 로컬 down 과 무관하다고 잠갔다(#6).
- 기본 풀에 들어온 원격 default 워커 표본(server.py 가 로컬 + 원격 default 표본을 합친다)의 카드 제목은 §2 가 말하지
  않는다 — 「기본 풀 로컬 카드는 오늘 그대로」를 기본 풀 전체로 읽어 이름만 잠갔다(#21).
- 브라우저 제목 추출은 오늘 카드 구조(`.hostcard .hn` + 부제 `.age`)에 기댄다 — 구현이 제목 요소를 바꾸면 #35 의
  `card_titles` JS 를 같이 고친다.
