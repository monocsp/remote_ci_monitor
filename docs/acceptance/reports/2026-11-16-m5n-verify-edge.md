# M5n 검증 보고 — 엣지 · 2026-11-16 · `feat/m5n-capable-worker-placement` @ 84cde79 → 수정 @ HEAD

- 실행 주체: 격리 에이전트(verify-edge). 기준: `feat/m5n-capable-worker-placement` @ 84cde79 을
  자기 워크트리로 가져와 자기 `.venv`. 비교 기준 dev 는 `1c1f8fc`(v0.4.4)를 `git archive` 로
  스크래치에 풀어 같은 venv 에서 `PYTHONPATH` 로 돌렸다.
- 운영(8787 · `~/.config/rcm` · `~/.local/share/rcm` · LaunchAgents)은 건드리지 않았다. 진짜 서버를
  띄우지 않았다 — 전부 in-process(`M5nServer` · `WorkerServer` · `Store`)와 스크래치의 잴 스크립트다.
- 결함마다 **고치기 전에 빨간 시험**을 먼저 커밋했다(`tests/test_m5n_edge.py`). 고친 커밋은 아래 표.

## 결과 요약

| # | 심각도 | 결함 | 증거 | 수정 |
|---|---|---|---|---|
| E1 | **높음** | `/api/status` · claim 이 보존된 잡 수에 끌려간다(M5f 결정 49 재발) | 완료 잡 1 / 1만 / 5만에서 status 13 / 125 / 628 ms(dev 5만 6.7 ms), claim 0.31 → 4.6 ms. 점검 기록 읽기가 `SCAN jobs` 이고 대기 잡 × 머신만큼 돌았다 | 4dd8ba1 |
| E2 | 중간 | 호스트를 모르는 claimant 가 `concurrency_scope = "host"` 그룹을 지나친다(fail-open) | 보고 없는 워커(`host_key None`)가 같은 컴퓨터의 시뮬레이터 잡과 함께 집는다. 그렇게 집힌 잡은 `host_key NULL` 이라 다른 워커도 못 본다 | cb4486b |
| E3 | 중간 | 느린 프로브가 원격 워커의 하트비트를 끊어 **도는 잡이 lost** | 측정이 하트비트 스레드에서, 프로브는 차례로(64 × 60 s 가능). 하트비트가 `worker_timeout_seconds`(60) 를 넘으면 `mark_lost_workers` | 73b0cf9 |
| E4 | 중간 | 출력이 많은 프로브 하나가 서버 메모리를 채운다 | `yes` 프로브(시한 3 s): 서버 RSS 10.1 GB · 6.4 s. 50 MB 출력 → tracemalloc 200 MB | e36e102 |
| E5 | 중간 | reload 가 프로브를 기다린다 — `rcm reload` 가 성공을 실패로, SIGHUP 동안 연결을 안 받는다 | 3 s 프로브에 `POST /reload` 3.05 s. 견본 flutter 프로브는 시한 30 s · 게이트가 flutter 를 돌리면 startup lock 대기 | 8e81892 |
| E6 | 낮음~중간 | 시한을 넘긴 프로브의 자식이 남는다 | 자식 pid 가 시한 3 s 뒤에도 살아 있다(`subprocess.run` 은 우두머리만 죽인다) | 73b0cf9 |
| E7 | 낮음~중간 | 견본 `flutter --version` 이 상대 시각을 찍어 지문을 흔든다 | 이 Mac 실측 둘째 줄 `revision 058e0af2c2 (4 months ago)` → 정규화 값에 그대로 | f00800d |
| E8 | 낮음~중간 | 못 걸린 다시 점검을 걸린 것처럼 말한다 | `check_ref` 못 풂 → 응답 `{"job_id": null}`, `rcm recheck` 는 `#None  simcheck  on mbp` · 종료 0 | aa6a40c |
| E9 | 낮음~중간 | 프리셋이 사라진 any-capable 잡이 영원히 대기 | reload 로 지운 뒤 `waiting_for_lane` → `not_scheduled`(+1 h 도), 아무도 못 집는다. pool 잡은 `preset_missing` 으로 바로 끝난다 | 87f41ab |
| E10 | 낮음 | 원격 보고 문자열에 제어문자가 그대로 — 로그 줄 위조 · 터미널 조작 | 프로브 값 `Xc 1\x1b]0;pwn\x07\n[rcm] error: forged` 가 `/api/machines` 에 그대로. 도구 이름 · host_key 도 | 4283cc1 |
| E11 | 낮음 | 능력 스레드의 오류가 공개 `last_error` 에 경로 · 줄바꿈째 | 형제 루프는 `_safe` 로 씻는다 | e472d8d |

mutcheck: 패턴이 바뀐 `m5n-host-scope-wrong-host` 를 옮기고 둘을 더했다(980aba2) — 51 → 53.

## 시도와 결과

### 1. 성능 — `/api/status` 와 claim (E1)

잴 스크립트(스크래치 `perf_status.py`): 원격 워커 8(풀 4개 · 레인 4) · 프리셋 30(pool 10 ·
any-capable 10 · `checks` 붙은 pool 8 · 점검 2) · 대기 200(세 종류 고르게) · 완료 잡 N. 15회 중앙값,
claim 은 claimant 계산 + `store.claim` 한 번(되돌리며 30회).

| 빌드 | 완료 잡 | `GET /api/status` | `GET /api/machines` | claim |
|---|---|---|---|---|
| dev 1c1f8fc | 1만 | 5.7 ms | — | 0.20 ms |
| dev 1c1f8fc | 5만 | 6.7 ms | — | 0.21 ms |
| M5n 84cde79 | 1 | 13.1 ms | 1.2 ms | 0.31 ms |
| M5n 84cde79 | 1만 | **125.1 ms** | 8.2 ms | 1.21 ms |
| M5n 84cde79 | 5만 | **628.1 ms** | 43.3 ms | 4.64 ms |
| 수정 후 | 1 | 16.8 ms | 1.4 ms | 0.31 ms |
| 수정 후 | 1만 | 12.7 ms | 1.7 ms | 0.34 ms |
| 수정 후 | 5만 | 12.5 ms | 1.4 ms | 0.33 ms |
| 수정 후 | 1만 + 점검 잡 1.6만(워커마다 2천, 옛 지문) | 13.1 ms | 1.3 ms | 0.34 ms |
| HEAD 87f41ab(모든 수정 뒤) | 1 | 10.7 ms | 1.1 ms | 0.32 ms |
| HEAD 87f41ab(모든 수정 뒤) | 5만 | 13.8 ms | 1.2 ms | 0.32 ms |
| dev 1c1f8fc(같은 때 다시) | 5만 | 10.9 ms | — | 0.23 ms |

원인 둘:

1. `store.check_records` 의 `(target_worker = ? OR worker_name = ?)` — `EXPLAIN QUERY PLAN` 이
   `SCAN jobs`. `jobs_check` 인덱스는 `worker_name` 이 앞인데 `target_worker` 와의 OR 가 막는다.
2. 그 읽기를 `_queue_rows → placement_for → _placement_notes` 가 **대기 잡마다 · 머신마다** 불렀다.
   대기 잡 6 → 30 에서 status 한 번의 SELECT 가 35 → 95(산출물 행 읽기는 M5n 전부터라 뺀 수로도
   29 → 53).

수정(4dd8ba1): `check_records(…, fingerprint_hash=)` — 판정은 현재 지문의 기록만 보므로 뜻이
같다. 원격은 `worker_name = ?` 와 「아직 안 집힌 지정 잡」(`worker_name IS NULL AND target_worker
= ?`)의 `UNION ALL`, 로컬은 `worker_name IS NULL` — 둘 다 `jobs_check` 를 탄다. 지문을 모르면 읽지
않는다. `_queue_rows` 가 판정 캐시를 넘겨 (프리셋, 머신)마다 한 번.

남는 것: status 는 dev 의 1.3~2배(측정 때마다 dev 도 6.7~10.9 ms 로 흔들린다) — 전역 큐 계산 ·
머신 요약의 몫이고 보존 잡 수와는 무관하다. claim 1.4~1.6배. 명세 §18 의 「2배 넘게 느려지면 멈춘다」 안쪽이다.

### 2. 호스트 범위 (E2)

- `_claimable` 의 `c.concurrency_scope = 'host' AND b.host_key IS NOT NULL AND b.host_key = ?` 에
  claimant 의 `host_key` 가 None 이면 늘 거짓이다. claimant 의 host 가 None 인 때: 원격 워커의
  보고가 없을 때(옛 판 워커 · 보고 실패 · **재등록 직후**(결정 91 이 보고를 지운다) · 서버 재시작
  직후 첫 보고 전).
- 빨간 시험 둘: 모르는 호스트의 claimant 가 같은 그룹을 집는다 · 모르는 호스트로 집힌 잡이 그룹을
  안 쥔다.
- 수정(cb4486b): host 범위에서 어느 쪽이든 모르면 같은 호스트로 본다(claim · ETA 같은 규칙).
  `host_of(worker)` = 보고 → `machines` 표가 기억하는 호스트 → (로컬) 이 컴퓨터. 재등록 뒤에도
  호스트는 안다(any-capable 은 여전히 보고가 있어야 받는다). 표는 처음 한 번만 읽는다 — 기존
  `test_worker_lanes_are_read_in_one_statement` 가 이것을 잡아 줬다(처음엔 워커마다 `get_machine`).
- host_key 위장(다른 워커의 호스트를 사칭): 워커 토큰을 가진 쪽이 자기를 **더** 막을 뿐이고, 다른
  호스트를 사칭해 막힘을 피하는 것은 자기 컴퓨터의 자원을 해칠 뿐이다. `global` 은 호스트를 안 본다.
  워커 토큰은 자기 서술을 믿는 설계라 고치지 않았다(기록).

### 3. 프로브 (E3 · E4 · E5 · E6 · E7)

- **하트비트(E3).** `heartbeat_once` → 세대가 바뀌었거나 주기면 그 스레드에서 `measure_and_report`.
  레인이 다 바쁘면 claim long-poll 도 없어 `last_seen_at` 은 하트비트만 채운다. 60 s 를 넘기면
  `mark_lost_workers` 가 도는 잡을 lost 로 닫는다. 수정: 측정을 별도 스레드로(락이 겹침을 막는다).
  시험: 측정이 막혀 있어도 `heartbeat_once` 가 1 s 안에 돌아온다.
- **차례로 돎(E3 · E5).** `run_probes` 가 차례로 → 64 × 60 s = 64분이 서버 기동(포트는 묶였지만
  serve 전) · reload 요청 · SIGHUP 루프를 막을 수 있었다. 함께 돌게 했다 — 1 s 프로브 넷이
  4.14 s → 1 s 남짓. 시험 상한 2.5 s.
- **reload(E5).** 함께 돌아도 가장 긴 시한(≤ 60 s)만큼은 남는다 — 견본 flutter 시한 30 s 는
  `rcm reload` 의 15 s 시한을 넘는다. reload 뒤 로컬 다시 재기를 뒤에서 하게 했다(8e81892). 그동안
  로컬 보고는 옛 세대 → `report_stale`(any-capable 을 안 받을 뿐, fail-closed). 시험: 3 s 프로브에도
  reload 가 2 s 안에 답하고 곧 새 세대 보고가 선다. 기동 때 측정은 명세(§4.2)대로 레인 전 동기.
- **자식(E6).** 프로브를 자기 프로세스 그룹에서 돌리고 시한을 넘기면 `killpg`. 시험: 손자 pid 가
  3 s 안에 사라진다.
- **메모리(E4).** `communicate` 가 다 담았다. `yes`(시한 3 s) → RSS 10.1 GB · 6.4 s. 수정 뒤
  같은 프로브 3.0 s · RSS 26 MB(스트림마다 앞 64 KB 만 지니고 나머지는 읽어 버린다 — 안 읽으면
  큰 목록을 찍는 정상 프로브가 시한까지 멈춘다).
- **견본 flutter(E7).** 이 Mac 실측(Flutter 3.44.8):
  `Flutter 3.44.8 • channel stable • … · Framework • revision 058e0af2c2 (4 months ago` — 정규화가
  120자에서 자른 값에 상대 시각이 들어간다. `--machine` 앞 두 줄은 `{` · `"frameworkVersion":
  "3.44.8",` 로 판이 같으면 같다. 견본 둘 · CHANGELOG · `docs/configuration.md` 를 고쳤다.
  같은 실측에서 `xcodebuild -version` → `Xcode 27.2 · Build version 27B52`, `java -version`(stderr)
  → 정상, `idb_companion --version` → 빌드 날짜 JSON(판이 같으면 같다).
- 명세 §4.1 은 「stdout+stderr 를 합쳐」인데 구현은 「stdout 이 비면 stderr」다. flutter 의 「새 판이
  있다」 상자가 stderr 로 나오므로 오히려 지문이 덜 흔들린다 — 기록만(명세 쪽을 고칠 일).

### 4. 보고 본문 (E10)

- 큰 `presets` 목록 · 모르는 프리셋/프로브 이름 → 400(이름을 댄다). 문자열 아닌 도구 이름 → 400.
  `accept_any: "true"` → 400. 값 121자 → 400. 본문 상한은 기존 `MAX_JSON_BODY`(413). 통과.
- 제어문자: 서버가 길이만 쟀다. 수정 — 프로브 값 · 도구 이름은 `strip_control`, host_key 는
  `[A-Za-z0-9:._-]{1,80}` 전체 일치(`re.match` + `$` 는 끝 줄바꿈을 통과시켜 `fullmatch`)만,
  아니면 `worker:<이름>`.
- 세대 깜빡임 · 매번 다른 지문을 보내는 워커: (워커, 점검, 지문)마다 자동 점검 한 번이라 지문을
  바꿀 때마다 점검 잡이 하나씩 선다. 악의 있는 워커 토큰이어야 하고 사람에게 보인다 — 기록만.

### 5. claim · 큐

- `gated` 에 any-capable 이면서 `checks` 인 프리셋: pool 갈래만 거르고 capable 갈래는 `eligible` 일
  때만 — 맞다. `--pool` 을 준 any-capable 은 pool 배치로 고정 — 코드 확인.
- 지정 잡의 워커가 down: `rcm recheck` 는 받아서 잡을 세우고 상태는 `worker_down`(정직).
  `rcm run --machine` 은 409 — 둘이 다르다(기록, 명세가 recheck 의 down 을 말하지 않는다).
- 지정 잡이 기다리는 워커는 `forget_workers` 가 안 지운다(대기 잡이 있으면 남긴다) — 잡은
  `worker_down` 으로 남고 머신 목록에도 남는다. 갇히지만 이유가 맞다(기록).
- **프리셋이 사라진 any-capable 잡(E9)**: 수정 — `Claimant.known`(지금 설정의 프리셋)을 주면
  `placement = capable AND preset NOT IN (:known)` 갈래를 열어 아무 레인이 집고 기존
  `preset_missing` 경로로 끝낸다.
- 하나뿐인 eligible 워커가 부하로 보류: 대기 행 `held_by_load`, 시각 없음(§12) — 코드 확인.

### 6. 점검

- 점검이 필요한 워커가 지문을 모름(모든 프로브 첫 회차 timeout): 스위퍼가 `fp is None` 이면 건너뛴다
  — 스팸 없음. 판정은 `fingerprint_unknown`.
- `check_ref` 해석 실패: 스위퍼가 30 s 마다 다시 시도하고 그때마다 로그 한 줄(명세 「그 회차는
  건너뛴다」대로). 소음이지만 사람이 고칠 것을 말한다 — 기록.
- **다시 점검이 못 걸림(E8)**: 수정 — 응답 항목에 `error_code`(`ref_unresolved`), CLI 는 stderr 에
  이유 · 종료 1.

### 7. 마이그레이션

| 잡 수 | v22 → v23 | 파일 |
|---|---|---|
| 1만 | 0.03 s | 4.7 MB |
| 10만 | 0.33 s | 46.7 MB |

`ALTER TABLE ADD COLUMN` 일곱 · 표 셋 · 인덱스 둘. 옛 빌드(1c1f8fc)로 v23 파일을 열면
`StoreError: database schema version 23 is newer than this build (22) — … restore a copy … or
upgrade`(기존 규칙 그대로).

### 8. 추정

- `measured_other` 는 다른 호스트 하나만 있어도 그 호스트의 중앙값(n ≥ min_samples) — 코드 확인.
- `sample = false` 잡은 `medians_from` · `list_step_sample_ids` 에서 빠진다. 실패 통계
  (`failure_stats`)에는 들어간다 — 점검 실패도 실패라 맞다고 본다(기록).
- 단계 중앙값의 `worker:<이름>` 호스트: `list_step_sample_ids(host_key="worker:x")` 가 옛 행을
  `worker_name = x` 로 푼다 — 코드 확인. 보고 전에 집힌 잡은 `worker:<이름>`, 보고 뒤는 해시라 같은
  워커의 표본이 둘로 갈릴 수 있다(E2 의 `host_of` 가 재등록 틈은 줄인다) — 기록.

## 검사

- `ruff check . && ruff format --check .` 통과.
- `tests/test_m5n_edge.py` 시험 함수 20개 · 케이스 21개(고치기 전 전부 빨강 → 초록).
- 영향 받는 파일: `test_store_m5n` · `test_server_m5n` · `test_queue_m5n` · `test_capability` ·
  `test_checks_sweeper` · `test_e2e_m5n` · `test_status_perf` · `test_cli_m5n` · `test_reload` ·
  `test_worker_discovery` · 문서 잠금들 — 초록.
- 전체 `pytest -q tests`(HEAD 87f41ab): 실패 0 · 오류 0.
- mutcheck: `--only` 로 `m5n-*` 여덟(기존 여섯 + 새 둘) 전부 control green · mutant red.
  전체 53개는 돌리지 않았다(M5n 밖 변이의 패턴은 이 수정들이 안 건드린다).

## 고치지 않은 것(기록)

- 원격 워커의 host_key 위장 · 매번 다른 지문 — 워커 토큰은 자기 서술을 믿는 설계.
- `rcm recheck` 는 down 머신을 받고 `rcm run --machine` 은 409 — 명세가 정할 일.
- 지정 잡이 영영 안 오는 워커를 기다리며 남음(`worker_down`) — 이유는 정직하다. 사람이 취소한다.
- `check_ref` 해석 실패의 30초마다 로그 한 줄.
- 기동 때 측정은 여전히 레인 전 동기 — 함께 돌아 가장 긴 시한(≤ 60 s)만큼 포트가 묶인 채
  serve 전이다(명세 §4.2 의 선택).
- 명세 §4.1 「stdout+stderr 합침」 ↔ 구현 「stdout, 비면 stderr」.
