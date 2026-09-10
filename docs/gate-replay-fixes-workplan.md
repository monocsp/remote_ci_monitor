# 게이트 재현이 찾은 수정과 개선 — 작업 명세 (M5i · 확정본 v1.0, 2026-09-10)

> 출처: 2026-09-10 오전, `dev`(19ac760 · M5g+M5h 포함, 미출시)를 이 Mac 의 **별도 인스턴스**(포트 8790 ·
> `~/.local/share/rcm-devtest`)로 띄우고, 운영(main v0.2.5 · 8787)이 지난 이틀 동안 돌린 dolomood 게이트 커밋 11개
> (실패 7 · 취소 1 · 성공 3)를 `--ref <sha>` 로 다시 넣어 본 결과(§2). 운영 인스턴스는 읽기만 하려 했으나 한 번 실패했고
> (B2), 그 사고가 이 문서의 P0 둘을 만들었다.
>
> 초안(v0.1)을 Codex 크로스리뷰 두 라운드에 걸었다 — `docs/reviews/2026-09-10-codex-gate-replay-fixes-design.md`. 초안이 틀렸던 세 곳
> (「스키마가 다르면 계획하지 않는다」 · 「`user_version=7` 이면 duplicate column」 · B4 를 새 설계로 적은 것)을 고쳤고,
> 초안에 없던 P0 하나(v16)와 기존 문제 일곱(§4)이 더해졌다. 항목마다 **결정**을 적었다: 채택 · 수정 채택 · 보류 · 기각.
>
> v1.1 (2026-09-10 오후): 두 항목이 더해졌다 — **B5**(PR 1 의 실기 확인이 찾은 것: `data_dir = "~/…"` 이면 표본의 `disk` 가
> `null`)와 **I8**(다른 머신에서 온 보고: 클라이언트가 서버 버전을 못 따라온다 — 서버가 자기 wheel 을 주고, 릴리스는 빠짐없이
> 끊긴다). 결정 81~83 · PR 1b · 6 · 7. 게이트 재현이 아니라 그 뒤의 실측에서 왔다.
>
> 바꾸지 않는 것: 런타임 의존성 0 · 스키마 v1(키 추가만) · 순수 계층은 I/O 도 시계도 안 본다 · **서버는 임의 출력을 파싱하지
> 않는다**(마커만 믿는다) · fail-open 금지 · 옛 빌드가 새 DB 를 거절하는 것(맞는 동작이다).

## 0. 한 줄 요약

| # | 종류 | 무엇 | 심각도 | 결정 | PR |
|---|---|---|---|---|---|
| B1 | 버그 | 웹 `hostCardHtml` 이 정의 안 된 `local` 을 참조 → `disk` 가 있는 모든 로컬 배치에서 웹이 죽는다 | **P0** | 채택 + 기존 Chrome 테스트 강화(I5) | 1 |
| B2 | 버그 | 「서버 없이 읽기만 한다」는 `rcm gc --dry-run --config` 가 DB 를 **마이그레이션**한다 — 운영 DB 7→15, 옛 빌드 재시작 불가 | **P0** | 수정 채택: 임시 사본 위에서 실제 마이그레이션·계획 · 불완전하면 exit 3 · 마이그레이션 전 백업 · 가드 | 2·4 |
| B2-v16 | 버그 | 사고 뒤 옛 빌드가 쓴 추론 라벨은 v15 가 재실행되지 않아 남는다 | **P0** | 채택: 한 번짜리 v16 복구 마이그레이션 | 2 |
| B3 | 버그 | 온라인 `gc --dry-run` 「would remain」이 직전 측정값(기동 직후 `0 B`) · 실제 gc 의 `storage_after`·`freed_bytes` 도 사후 측정이 아니다 | P1 | 수정 채택 | 3 |
| B4 | 회계 | m5g §4.4 가 정한 `charged`/`reclaimable` 분리가 **구현에 없다** — 하드링크 pack 이 「would free」를 약 1/3 과장 · latch 가 `reason=="free"` 에만 걸린다 | P0-release | 수정 채택 | 3 |
| I1 | 설계 | 게이트 스크립트가 마커를 안 찍으면 M5h 대장이 빈다 | 설계 | 문서·검증된 래퍼 예시 채택 · `fail_patterns` **기각** | 5 |
| I2 | 개선 | `--ref <sha>` 잡의 목록 칸에 sha 가 두 번 | P3 | 수정 채택(40-hex 완전 일치만) | 5 |
| I3 | 개선 | 없는 잡에 `rcm wait` 하면 `log: rcm logs 999` | P3 | 수정 채택(구조화된 종료 사유, 확정 404 만 생략) | 5 |
| I4 | 개선 | `rcm check` 의 `data dir` 행이 서버가 아니라 로컬 설정의 경로 | P3 | 채택(`local data dir · from <config>`) | 5 |
| I5 | 개선 | 웹 렌더 경로 테스트 — Chrome 테스트가 **있는데** 표본에 `disk` 가 없었다 | P0 | 수정 채택: 표본·단언 강화, ubuntu 잡은 Chrome 없으면 실패 · ESLint 보류 | 1 |
| I6 | 개선 | 회계 나이 미표시 · `measured_at` 이 캐시 시각을 덮는다 | P2 | 수정 채택(가장 오래된 측정 시각 · `measured 57m ago`) | 3 |
| I7 | 문서 | PLAN 의 CLI 예시가 `failed_step_guessed` · `operating.md` 업그레이드 절차가 B2 를 유발 | P1 | 수정 채택(절차 순서 교체) | 2·5 |
| B5 | 버그 | `data_dir = "~/…"` 이면 서버 샘플러가 `~` 를 안 푼 경로로 `disk_usage` 를 불러 표본의 `disk` 가 `null` — 디스크 막대·회계 줄이 안 보인다(예시 설정의 기본값이 이 경우) | P1 | 채택(`cfg.data_dir` 프로퍼티를 쓴다) | 1b |
| I8 | 설계 | 클라이언트가 서버 버전을 못 따라온다 — 서버는 0.2.5(main 소스), 노트북 0.2.2, 릴리스 최신 v0.2.4(`v0.2.5` 태그 없음) | P1 | 수정 채택: 서버가 자기 wheel 을 준다(`/client/…whl` · 결정 81) · main 의 버전 상승이 태그를 만든다(결정 82) · `min_client_version`·`rcm check` 행, `self-update` 는 보류(결정 83) | 6·7 |
| O1 | 운영 | 이 Mac 의 운영 DB 가 스키마 15 인 채 v0.2.5 가 돌고 있다 | **지금** | 재시작 금지 → PR 1~3 릴리스(v0.2.6) → pause·drain·백업·정지·업그레이드 | — |

## 1. 방법

- dev 서버: 이 워크트리 `.venv` · `server.toml` 은 운영 프리셋 `gate` 를 그대로(`/bin/bash scripts/local_ci.sh` ·
  `env_passthrough` 다섯 · `concurrency_group = "gate"`), `[[repos]] dolomood` 는 GitHub URL, **알림 훅 없음**(운영의
  `gate-status.sh` 는 GitHub commit status 를 남긴다).
- 제출: `rcm run gate --ref <40-hex sha>` — 브랜치는 이미 옮겨 갔으므로 sha 로 고정. 미러 첫 fetch 는 sha 단일 fetch 가 안 되어
  전체 fetch 로 폴백(15초), 이후는 sha 단일 fetch.
- 대조: 운영의 `GET /jobs/<id>`·`jobs/<id>/log.txt`(읽기) vs dev 의 `rcm run` stdout/stderr · `GET /jobs/<id>` · `rcm jobs` ·
  `rcm top` · 웹(Chrome) · main 클라이언트↔dev 서버 교차.
- 첫 시도의 `data_dir` 은 세션 스크래치패드(`/private/tmp/…`)였고, dolomood 의 `sim_cleanup.py --selftest` 가 「저장소가
  허용 루트(`/tmp`·`/private/tmp`) 안에 있다」며 매 잡을 죽여 F4 를 다시 돌렸다(rcm 의 문제가 아니다).

## 2. 실측 — 케이스 표

| 케이스 | 운영(main) | dev 재실행 | 같은 지점? |
|---|---|---|---|
| F1 analyze | #190 failed · `failed_step: analyze` | #1 failed · `failed_step: null` · `last_step: analyze` | 예 |
| F2 format | #159 · `format (--set-exit-if-changed)` | #2 · 같은 `last_step` | 예 |
| F3 custom_lint | #163 · `custom_lint (경계 룰)` | #3 · 같은 `last_step` | 예 |
| F4 API 인벤토리 | #172 · `API 호출 인벤토리 …` | #6 · 같은 `last_step`(첫 시도 #4 는 §1 의 배치 실수) | 예 |
| F5 디바이스 QA | #152 · `디바이스 QA chunk …` | #7 · 같은 `last_step` · 같은 `FAIL:` 줄 | 예 |
| F6 취소(150초 뒤) | #176 cancelled · **`failed_step` 달림** | #8 cancelled · `failed_step`·`last_step` 둘 다 없음 | 결정 64 확인 |
| F7 #162 | failed · `build web`(오지목, 실제는 `test`) | #9 **succeeded** 9m 59s | 간헐 |
| F8 #181 | failed · `build web`(오지목) | #10 **succeeded** 13m 07s (#184 와 같음) | 간헐 |
| S1 #195 · S2 #192 | succeeded 9m 32s · 9m 21s | #11 · #12 succeeded 9m 13s · 9m 33s | 예 |
| S3 #189 | succeeded | 오너 요청으로 취소(운영이 바빠짐) | — |

- 간헐 실패의 정체: `test/core/feedback/just_audio_screen_music_port_test.dart: fadeIn — 0 에서 스며든다 ★ … 300ms`.
  운영 #162 · #181 · **#196(재현 중 macbook 이 넣은 잡)** 셋 다 이 테스트였고 main 은 셋 다 `build web` 이라고 했다.
  #196 은 이 재현이 CPU 를 같이 쓴 시간에 돌았다 — 부하에 민감한 테스트라 재현 자체가 운영 잡 하나를 빨갛게 만들었을
  가능성이 크다(GitHub status `failure` @928980b). dolomood 쪽 일이지만 적어 둔다.
- 호환: main 클라이언트(0.2.5) → dev 서버, dev 클라이언트 → main 서버 모두 동작. dolomood 의 `scripts/remote_ci.sh` 는
  `url`·`job_id`·`joined`·`state` 만 읽어 `failed_step` 변경의 영향이 없다.
- dev 가 약속대로 한 것(재확인): 취소 잡에 라벨 없음 · `exit 1 (last step …)` · 1·2·3 끝줄 `log: rcm logs N · URL` ·
  404/401 `hint` · `rcm jobs` 의 `<ref> @<sha>`·`--ref` · `--no-wait` 의 순번·ETA · 웹 진행 막대·접힌 행·잡 번호·재실행
  명령(B1 을 손으로 막았을 때) · `rcm check storage` · `/api/health.storage` · `server.job_storage` · `rcm gc --dry-run`.

## 3. 항목별

### B1 — 웹 `hostCardHtml` 의 `local` (P0 · 채택)

**현상.** dev 웹을 열면 큐 표까지만 그려지고 「최근」이 비어 있으며, 헤더가 `폴링` 이었다가 30초 뒤 「… 와 연결이 끊겼습니다」
띠가 뜬다. 서버는 정상. 콘솔: `ReferenceError: local is not defined at hostCardHtml (app.js:1722)` — `render()` 마다.

**원인.** `src/remote_ci_monitor/web/app.js:1722`
```js
var js = local ? jobStorageLine((state.status && state.status.server || {}).job_storage, …) : null;
```
`local` 은 어디에도 없다. M5g PR #76(3958f37)에서 들어왔다. `h.disk` 가 있을 때만 그 줄이 돌므로(디스크 수집이 실패하면
`disk: null`) 정확히는 「디스크 표본이 있는 로컬 배치」에서 — 정상 배치에서는 사실상 언제나 — 터진다. 예외가 `render()` 를
끊어 `renderRecent()` 가 안 돌고, 갱신 루프도 죽어 `마지막 갱신` 이 늘다가 끊김 띠가 된다.

**왜 지나쳤나.** `tests/test_web_browser.py`(headless Chrome, ubuntu CI 에서 실제로 돈다 — 3147 passed · 0 skipped)의 표본에
`disk` 도 `server.job_storage` 도 없어 그 줄이 실행되지 않았다. `node --test` 는 순수 함수(`jobStorageLine`)만 본다.

**고치는 법.** 로컬 표본은 `source: "local"`(`hostsample.py:262`), 원격 워커 표본은 `source: "worker"`
(`remote_workers.py:817`)다. 한 번만 쓰이므로 변수를 만들지 말고 조건을 그 자리에 쓴다:
```js
var js = h.source === "local" ? jobStorageLine(…) : null;
```

**영향 · 반대급부.** 릴리스 차단 버그. 반대급부 없음. 재발 방지는 I5.

### B2 — 「읽기 전용」 오프라인 dry-run 이 운영 DB 를 마이그레이션했다 (P0 · 수정 채택)

**현상(사고).** 08:50 `docs/operating.md` 의 업그레이드 절차대로 dev 워크트리의 `.venv/bin/rcm gc --dry-run --config
~/.config/rcm/server.toml` 을 돌렸다. 계획은 잘 나왔다(「51개 잡 34.6 GB」). 09:03 운영 DB 의 `PRAGMA user_version` 이 **7 → 15**
로 바뀌어 있었다. v14 가 취소·유실 잡의 `failed_step` 을 지웠고 v15 가 실패 잡 67건의 `failed_step` 을 `last_step` 으로 옮겼다.
운영 v0.2.5 는 계속 돌았고(#196~#204 정상 처리 · 알림 훅 동작 · `last_error` 없음 — 새 컬럼은 전부 DEFAULT 가 있다), 그러나:

- v0.2.5 는 `last_step` 을 모른다 → 운영 화면·`rcm top`·`GET /jobs/N` 에서 실패 잡의 스텝 라벨이 사라졌다.
- v0.2.5 의 `migrate()` 는 `version > DB_VERSION` 이면 `StoreError` → **서비스를 재시작하면 서버가 안 뜬다**(맞는 동작이다 —
  v15 는 데이터 마이그레이션을 포함하므로 「경고로 낮추고 아는 열만 쓰기」는 fail-open 이라 **기각**).
- `tools/guard_production.py` 는 `serve`·`worker` 만 본다 → 막지 못했다.
- **그 뒤에도 옛 빌드는 실패 잡에 추론 라벨을 `failed_step` 에 쓴다**(#196 · #199 · #200). v15 는 다시 돌지 않으므로 그냥
  올리면 새 빌드가 그 라벨을 「선언된 실패」로 보여 준다 → v16.

**원인.** `cli.py:652 _offline_gc()` 가 `Store(cfg.data_dir / "rcm.sqlite3")` 를 열고, `store.py:485 Store.__init__` 가 무조건
`self.migrate()` 를 부른다(부모 디렉터리도 만든다). docstring(「설정과 데이터 디렉터리만 읽고 아무것도 안 지운다」)·결정 61(「DB 는
읽기 전용」)·CHANGELOG(「reading only the config and the data directory」)가 전부 거짓이 됐다. 더 나쁜 것은 **문서의 절차 자체가
이 사고를 만든다**: `git pull --ff-only`(운영 체크아웃 = editable 설치라 그 순간 `rcm` 이 새 코드) → `rcm gc --dry-run --config`
(새 코드가 옛 서버가 도는 DB 를 마이그레이션) → 재시작.

또 하나: 마이그레이션을 건너뛰기만 해서는 v7 DB 에서 계획이 안 나온다 — `store.py:623 _row_to_job()` 이 `last_step`·
`fail_truncated` 를 무조건 읽는다. 그 예외는 `janitor.plan()` 이 `inventory_error` 로 삼키고 `_offline_gc` 는 **빈 계획 + exit 0**
을 낸다(§4-1, fail-open).

**고치는 법 — 다섯 (결정 73~75).**

1. **오프라인 dry-run 은 임시 사본 위에서 돈다** (Codex Q1: 「전용 최소 리더」보다 이쪽). 살아 있는 DB 에 `mode=ro` URI 연결
   (절대경로 · URL 인코딩 · 쓰기 PRAGMA 없음 · `immutable=1` 금지) → `Connection.backup()` 으로 0700 임시 디렉터리에 사본
   (페이지 단위 · 전체 마감, 못 끝내면 exit 3) → 사본을 **보통의 `Store`** 로 열어 실제 `migrate()`(사본만 바뀐다; 자동 백업도
   임시 디렉터리 안) → `Janitor.plan()` 을 사본 DB + 실제 `data_dir` 의 `workspaces/`·`jobs/` 로 → 사본·`-wal`·`-shm` 통째로 삭제.
   왜 이쪽인가: 행 변환기와 계획 함수를 그대로 써 결정 57(같은 계획 함수)이 자명하고, **마이그레이션 자체의 실패까지 재시작 전에
   드러나며**, v7 이든 v15 든 한 코드 경로다. 반대급부: 디스크가 없으면 사본이 실패한다 — 그때 게이트가 「모름」으로 멈추는 것이
   맞다. 어느 단계든 불완전하면 **exit 3**, 빈 계획 성공은 없다. 출력에 「DB schema v7 을 이 빌드(v16)로 올린 사본으로 계획했다」를
   적는다. `rcm check --config` 는 DB 를 안 열므로 손대지 않는다.
2. **마이그레이션 전 백업** — 위치는 `migrate()` 경계(모든 진입점을 덮는다). `1 ≤ version < DB_VERSION` 일 때 한 번,
   `journal_mode=WAL` 을 포함한 어떤 변경보다 먼저, `Connection.backup()` → 임시 파일 → 열어서 검증 → 원자적 rename
   `<data_dir>/backup/rcm.sqlite3.v<old>.bak`. **생성·검증 실패는 마이그레이션 중단**(fail-closed). 최근 3개 정리는 마이그레이션
   성공 뒤에 하고, 정리 실패는 경고만. 이것이 「옛 빌드가 새 DB 를 거절한다」의 복구 경로다.
3. 옛 빌드의 거절 메시지에 길을 붙인다: `database schema version 15 is newer than this build (7) — stop the service, restore
   <data_dir>/backup/rcm.sqlite3.v7.bak (and remove -wal/-shm), or upgrade`. 복원은 **DB 만의 다운그레이드**다 — 백업 뒤 생긴
   잡 행과 `jobs/`·`workspaces/` 디렉터리가 어긋나고 고아가 남는다는 손실 범위를 `operating.md` 에 적는다.
4. **v16 복구 마이그레이션**(한 번짜리). 새 코드는 선언 라벨을 쓸 때 언제나 `job_failures` 행을 같은 트랜잭션에 남기므로(로컬·원격
   워커 둘 다 `outcome_for()` 경로 · `timed_out` 도 `labelled=True` · 자재화 실패 같은 비마커 실패는 `failed_step` 을 안 쓴다),
   「라벨은 있는데 대장 행이 하나도 없다」= 옛 빌드의 추론 라벨이다(이름 일치 술어는 100개 상한 경계에서 틀리므로 쓰지 않는다):
   ```sql
   UPDATE jobs SET last_step = COALESCE(last_step, failed_step), failed_step = NULL
    WHERE state IN ('failed', 'timed_out') AND failed_step IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM job_failures WHERE job_failures.job_id = jobs.id);
   UPDATE jobs SET failed_step = NULL, last_step = NULL WHERE state IN ('cancelled', 'lost');
   ```
   매 기동 보정은 하지 않는다 — v16 뒤 옛 빌드는 버전 검사로 못 뜬다. 열려 있던 옛 프로세스는 버전을 다시 안 보므로
   **「옛 프로세스 정지 확인 → 새 서버 첫 기동에서 v16」이 배포 절차의 필수 조건**이다(§6).
5. **가드** (Codex Q2: 최소 구성이되 유효 `data_dir` 은 해석한다). 보는 하위 명령은 `serve`·`worker`(기존 규칙 유지) ·
   `token`(`list` 도 Store 를 열어 마이그레이션하므로 **전부 쓰기**) · `gc --dry-run --config`(1 뒤 **명시 허용**). 나머지 원격
   클라이언트 명령은 제외. 쓰기 명령은 CLI 와 같은 우선순위로 유효 `data_dir` 을 계산한다 — `--data-dir` → `RCM_SERVER_DATA_DIR`
   (명령 앞 `VAR=` 과 `env VAR=` 둘 다) → 선택된 설정(`--config` → `RCM_CONFIG` → `./rcm.toml`)의 `[server].data_dir` → 기본 —
   그것이 운영 데이터 안이고 실행 파일이 운영 venv 밖이면 **deny**. TOML 을 못 읽으면 CLI 도 먼저 실패하므로 deny 하지 않는다.
   `PYTHONPATH` 우회는 범위 밖 — 훅은 방어층이지 보안 경계가 아니고, 핵심 보장은 1·2 다.

**테스트.** v7 픽스처 DB(WAL 파일이 실제로 있는 상태)로 dry-run 뒤 원본 `user_version`·mtime 그대로(mutcheck 후보: 사본 대신
원본을 열면 빨개진다) · 사본이 남지 않는다 · backup 마감 초과 → exit 3 · 백업 파일 생성·검증·중단 · v16 픽스처(옛 라벨 셋 ·
새 코드 라벨 하나 · 100개 상한 경계) · 가드 `decide()` 에 `token --config <복사본 toml>`·`RCM_SERVER_DATA_DIR=` 케이스.

### B3 — 온라인 dry-run 의 「would remain」과 실제 gc 의 사후 값 (P1 · 수정 채택)

**현상.** 기동 직후 워크스페이스 2 GB 가 있는데 `rcm gc --dry-run` 이 `0 B would remain`, 두 번째 실행에서야 `2.0 GB`.
**원인.** `server.py:748` `before = self.retention.storage(now)` 를 `gc_report()` **앞에서** 읽는다 — 마지막 측정값이고, 측정은 그
뒤의 `plan()` 이 한다. 결정 57 과는 무관하다(두 요청 사이의 동일성을 보장 안 한다는 뜻이지 한 응답 안에서 묵은 값을 써도 된다는
뜻이 아니다). **Codex 가 더 찾은 것:** 실제 gc 도 삭제 뒤 인벤토리를 다시 안 만들어 `storage_after` 가 삭제 전 계획을 가리키고,
`freed_bytes` 는 unlink 에 성공한 항목의 **계획상** 크기다.

**고치는 법.** `gc_report()` 가 `plan` 이 만든 같은 스냅샷을 `storage_before` 로 돌려준다 · dry-run 문구는 `estimated to free … ·
projected to remain …` · 실제 gc 는 삭제 뒤 인벤토리와 여유를 다시 잰다 · 필드는 `deleted_charged_bytes` · `estimated_reclaimable_bytes` ·
`free_bytes_before/after` 로 뜻을 가른다(`freed_bytes` 는 남기되 문서에 「계획상 합」이라고 적는다). 온라인·오프라인의 「remain」
정의를 맞춘다(둘 다 인벤토리 전체).

### B4 — 하드링크: 명세가 정한 분리를 구현한다 (P0-release · 수정 채택)

**현상.** 운영 워크스페이스 77개를 `st_blocks` 로 재면 54.5 GB, 그중 **17.5 GB(32%)** 가 `nlink > 1` 인 git pack(미러
`mirrors/dolomood` 와 로컬 clone 이 공유). 오프라인 dry-run 의 「51개 잡 34.6 GB 회수」는 실제로는 약 2/3 만 풀린다.

**원인.** `janitor._measure_dir()` 이 링크마다 센다. **m5g 명세 §4.4 는 이미 `charged_bytes`(예산용, 전부 센다)와
`reclaimable_bytes`(바닥용, `nlink > 1` 을 뺀다)를 갈라 두었는데 코드에 그 분리가 없다** — 초안은 이것을 새 설계로 적었다(✗).
예산에서는 보수적이지만 바닥(`min_free_bytes`)에서는 거꾸로다: 「지우면 이만큼 는다」가 과장되어 결정 62 latch 가 더 자주 걸리고
그 전까지는 필요 이상 지운다. 그리고 latch 자체에 구멍: `janitor.py:465 floor_ran = any(i.reason == "free")` 라 나이·예산으로
먼저 뽑힌 항목이 바닥 부족분을 채우면 latch 가 안 선다.

**고치는 법 (결정 76 · Codex Q5).**
- `VolumeItem.shared_bytes` — **일반 파일(`S_ISREG`)의 `nlink > 1` 블록만**(디렉터리의 `nlink > 1` 은 하위 디렉터리 때문에 정상).
  스냅샷 tar 도 같은 규칙. 캐시 `_sizes = (bytes, shared, mtime, measured_at)`.
- 항목 표시값·예산 규칙 = charged(기존 `bytes`). 바닥 규칙·dry-run 의 「would free」·latch 분모 = `estimated_reclaimable_bytes =
  bytes − shared_bytes`(보수적 하한 — 같은 삭제 단위 안에 링크가 다 있어도 못 센다. 이름이 그래서 `estimated`).
- `PurgePlan.floor_attempted` = 「`free_bytes` 를 알았고 `min_free_bytes > 0` 이며 계획 시작 시 `free < min`」. latch 판정은 이것으로.
- latch 분모는 계획 전체가 아니라 **삭제에 성공한 항목**의 예상 회수량 — `apply()` 가 성공한 id·바이트를 돌려준다. 분모 0 이면
  판정하지 않고 `projected_short_free_bytes` 로 부족을 보고한다.
- `job_storage` 에 `shared_bytes`·`estimated_reclaimable_bytes` 키 추가(스키마 v1 유지). CLI 문구는 charged 총량과 예상 회수량을
  갈라 쓴다.

**반대급부.** 외부에서 링크가 생겨도 워크스페이스 최상위 mtime 은 안 바뀌어 캐시된 `shared` 가 최대 캐시 수명만큼 낡을 수 있다
(회수량 과대 → 첫 회차 뒤 latch 가 피해를 제한) · 필드가 `VolumeItem`·`PurgeItem`·JSON·렌더러·캐시·순수 테스트로 전파된다 ·
APFS 클론은 계속 범위 밖. 17.5 GB 규모의 실제 오차를 바로잡는 가치가 더 크다.

### B5 — `data_dir = "~/…"` 이면 호스트 표본의 `disk` 가 `null` (P1 · 채택 · PR 1 실기 확인에서)

**현상.** PR 1 의 실기 확인(2026-09-10 · 8790 · `data_dir = "~/.local/share/rcm-devtest-pr1"`)에서 `/api/status` 의 로컬 표본이
`disk: null` 이었다 — 디스크 막대도 「rcm 데이터 …」 줄도 안 그려진다(`rcm top` 도 같다). 절대경로로 바꾸니 나왔다.

**원인.** `server.py:342` 가 `HostSampler(disk_path=str(self.config.server.data_dir))` 로 **설정 파일의 원시 문자열**을 넘긴다.
`~` 가 안 풀린 채 `shutil.disk_usage("~/…")` 가 `FileNotFoundError` → `_disk_usage()` 가 None(부분 실패는 그 칸만 None — 설계대로다).
`ServerConfig.data_dir` 프로퍼티(`config.py:182`, `expanduser()`)가 있는데 여기서만 안 썼다. `examples/server.toml` 의 기본값
`data_dir = "~/.local/share/rcm"` 이 정확히 이 경우라, 예시대로 설치한 서버는 M4 이후 줄곧 디스크 칸이 비어 있었다. 원격 워커
(`remote_worker.py:638`)는 `config.data_path`(푼 값)를 써서 문제없다.

**왜 지나쳤나.** 이 Mac 의 운영 설정은 절대경로다(그래서 운영에서는 디스크가 보였고 B1 이 터졌다). 테스트의 `Server` 픽스처도
`tmp_path` 절대경로다. `~` 설정에서는 B1 도 안 났다 — 그 줄이 안 돌아서.

**고치는 법.** `disk_path=str(self.config.data_dir)`. 테스트: `data_dir = "~/x"` 로 만든 `App` 의 `sampler.disk_path` 가 절대경로이고
`_disk_usage(그 경로)` 가 dict 인 것 — 그리고 `Server` 픽스처와 별개로 `~` 설정 문자열이 `HostSampler` 까지 가는 경로 하나.

**영향 · 반대급부.** 없음. 릴리스 차단은 아니지만 10분짜리라 PR 1 바로 뒤에 **PR 1b**(`fix/hostsample-disk-path-expanduser`)로.
CHANGELOG Fixed 한 줄(「`~` 로 적은 `data_dir` 에서 디스크 칸이 비어 있던 것」).

### I1 — 스크립트가 이름을 안 찍으면 대장이 빈다 (설계 · 결정 77)

dolomood `scripts/local_ci.sh` 는 어느 커밋에서도 `::rcm::fail::`·`step-end::fail` 을 안 찍는다(`echo "FAIL: …"` 67곳). 그래서
dev 의 모든 실패 잡이 `failures: []` 이고, main 이 「맞게」 찍던 F1~F5 도 `failed_step: null` 이 됐다. M5h 가 다 죽은 것은 아니다
— 추론 제거 · `last_step` · 로그 길 안내 · 404 hint 는 마커 없이도 유효하다. 대장과 간헐 판정만 팀이 마커를 넣을 때까지 비활성이다.

- **`fail_patterns`(프리셋이 준 정규식으로 서버가 stdout 을 대 보기)는 기각.** 출력 형식이 바뀌면 거짓 이름이 저장되고(#162 의
  되풀이), 병적인 정규식이 로그 소비를 막아 자식까지 멈출 수 있고, 마커와 패턴의 우선순위·중복·상한이라는 계약이 하나 더 생긴다.
  「서버는 받아 적는다」는 경계를 지킨다.
- **채택:** ① `docs/configuration.md` 「Making a failure explain itself」에 「실패를 판정하는 그 함수에서 `::rcm::fail::` 을 함께
  찍어라」를 기본 예시로 ② 스크립트를 못 고칠 때만 쓰는 **래퍼 예시**를 `examples/session/` 에 두되 테스트로 잠근다: bash 전용
  shebang · 파이프 직후 `PIPESTATUS` 즉시 복사 · `set -e -o pipefail` 이 상태 저장 전에 래퍼를 안 끝내게 · 원 출력은 `tee` 로 그대로,
  마커는 파이프가 끝난 뒤 · 파이프로 대상이 block-buffering 될 수 있음을 문서화 · 그 팀이 소유한 정확한 형식만 파싱(느슨한
  `FAIL:` 전체 금지) · 제어문자·120자·중복 처리 · 임시 로그 정리 ③ 릴리스 노트에 「마커 없는 실패는 대장에 이름이 없다」.
- dolomood 에는 `fail()` 헬퍼 도입을 권한다(이 레포 밖).

### I2 — `--ref <sha>` 잡의 목록 칸 (P3 · 수정 채택)

`source_ident()`·`_source_text()` 가 ref 와 sha 를 따로 붙여 `092dc5854301a87eab47c0… @092dc58` 가 된다. `ref` 가 **40자리
hex 이고 정규화 후 `sha` 와 정확히 같을 때만** 생략한다(「접두면」은 우연히 sha 접두와 같은 브랜치·태그가 있을 수 있어 넓다):
최근 칸 `@092dc58`, 큐 칸 `dolomood @092dc58`.

### I3 — 없는 잡의 `rcm wait` (P3 · 수정 채택)

`failure_lines()` 가 성공이 아닌 모든 입력에 로그 줄을 만든다. `state is None` 만 보고 빼면 **첫 조회 전에 네트워크가 끊긴
exit 3** 에서도 로그 길을 잃는다(결정 70 이 지키려는 경우). → 대기 루프가 문자열 사유 대신 구조화된 종료 사유(`not_found` ·
`unreachable` · `timeout`)를 돌려주고, **확정 404 일 때만** 로그 줄을 생략한다.

### I4 — `rcm check` 의 `data dir` 행 (P3 · 채택)

서버는 보안상 `data_dir` 을 API 로 안 내리므로 원격 서버의 경로는 알 수 없다 — 이 행은 로컬 설정의 사실이라 버릴 필요는 없다.
이름을 `local data dir` 로, 상세에 `from <config path>` 를 붙여 `--server` 와 무관한 경로라는 오해만 없앤다.

### I5 — 웹 렌더 경로의 재발 방지 (P0 · 수정 채택 · 결정 79)

새 체계가 아니라 **있는 것을 넓힌다**: `tests/test_web_browser.py` 의 로컬 표본에 `disk`, 상태 문서에 `server.job_storage` 를 넣고
호스트 카드의 「rcm 데이터 …」 문구(두 언어)가 실제로 그려졌는지 단언한다 · 페이지 초기화 전에 `error`·`unhandledrejection`
수집기를 설치해 **0건**을 단언한다(타임아웃보다 원인이 선명하다) · **ubuntu 필수 잡은 Chrome 미발견을 skip 이 아니라 실패**로
(macOS 러너의 skip 은 허용). ESLint `no-undef` 는 실행 안 된 분기까지 잡지만 이 레포에 Node 패키지 체계가 없어 규칙 하나를
위해 `package.json`·lockfile·registry·공급망을 들이는 비용이 지금은 더 크다 → **보류**(정적 규칙이 두셋 필요해지면 pinned 으로).
무고정 `npx eslint` 는 기각.

### I6 — 회계의 나이 (P2 · 수정 채택)

`server.job_storage.measured_at` 은 있지만 `rcm check` 와 웹 카드는 「3 GB」만 보인다. 그런데 그대로 보이면 새 거짓말이 된다 —
`_measure_workspace()` 는 최대 24시간 된 캐시를 돌려주는데 `plan()` 은 `_measured_at = now` 로 덮는다(`janitor.py:426`).
→ 캐시 항목마다 측정 시각을 두고, 집계 `measured_at` 은 합계에 기여한 값 중 **가장 오래된** 측정 시각으로 · 필요하면
`inventory_checked_at` 을 따로 · 문구 `rcm data 3 GB · measured 57m ago` / `회계 3 GB · 57분 전 측정`. 잡 종료 시 개별 측정은
**보류**(워커 종료 경로를 막지 않도록 janitor 작업 큐와 중복 병합부터).

### I7 — 문서 (P1 · 수정 채택)

- `PLAN.md` 「CLI」의 세션 예시에서 `failed_step_guessed` 를 지우고 `examples/session/ci-gate.sh` 에 맞춘다.
- `docs/operating.md` 「Upgrading」은 한 줄이 아니라 **순서를 바꾼다**(§6 의 절차). 지금 절차는 도는 editable 체크아웃을 먼저
  `git pull` 한다 — 문서 스스로 옛 프로세스가 새 모듈을 늦게 import 할 수 있다고 경고하면서.
- `docs/configuration.md`: I1 의 예시 둘. CHANGELOG: B1·B2·v16 을 Fixed 에 — 기계·잡 번호가 아니라 「오프라인 dry-run 이 스키마를
  바꾸던 문제와 그 영향」으로 일반화해서.

### I8 — 클라이언트가 서버 버전을 따라온다 (P1 · 수정 채택 · 결정 81~83)

**현상(2026-09-10 · 다른 머신의 보고).** 노트북 여러 대가 이 Mac 의 게이트를 `rcm` 클라이언트로 돌린다. 클라이언트가 서버보다
뒤처진 채 오래 방치되는 일이 반복됐다. 실측: 서버 `/api/health.version` **0.2.5**(main 을 editable 로 실행) · 노트북 **0.2.2** ·
GitHub 릴리스 최신 **v0.2.4**(`v0.2.5` 태그·릴리스 없음). 호출 쪽 래퍼는 「서버 따라 올라가기」를 이미 한다 — health 로 서버
버전을 읽고, GitHub `releases/latest`(6시간 캐시)를 읽고, `min(서버, 최신 릴리스)` 까지
`releases/download/v<X>/remote_ci_monitor-<X>-py3-none-any.whl` 로 올린다. 0.2.5 의 wheel 이 없어 0.2.4 에서 멈춘다.

**원인 둘.**

1. 릴리스 절차의 마지막 걸음이 사람 손이다 — CONTRIBUTING 「Releasing」 3(`git tag vX.Y.Z && git push`)이 빠졌다. main 은
   `__version__ = "0.2.5"` 이고 CHANGELOG 는 `[0.2.5] - 2026-09-09` 인데 태그가 없어 `release.yml`(태그 push 트리거)이 돌지 않았다.
2. 구조: 배포 채널이 GitHub 릴리스뿐이라 「서버가 도는 코드」와 「클라가 받을 수 있는 코드」가 다른 물건이다. 미출시 dev 빌드로
   서버를 돌리는 동안(§1 의 재현이 그랬다)·오프라인 망·릴리스가 밀린 동안엔 따라올 길이 없다. 결정 30(GitHub 는 커밋·push·PR
   머지에만)에도 어긋난다 — 클라이언트의 런타임 경로에 GitHub 가 있다.

**고치는 법 — 셋.**

1. **서버가 자기 클라이언트 wheel 을 준다** (보고의 안 B · 근본 · 결정 81).
   - `GET /client/remote_ci_monitor-<X>-py3-none-any.whl` — `<X>` 가 도는 서버의 `__version__` 과 **정확히** 같을 때만 200. 다른
     이름은 404 + `hint` 에 맞는 파일명. 파일명이 URL 끝에 있어야 pip 가 wheel 로 알아본다 — 그래서
     `pip install http://<서버>/client/remote_ci_monitor-<X>-py3-none-any.whl` 이 그대로 된다(`/api/client-wheel` 같은 이름 없는
     별칭은 만들지 않는다 — pip 는 URL 의 마지막 마디로 파일 종류를 정한다).
   - `/api/health` 에 `client_wheel: {"path": "/client/remote_ci_monitor-0.2.6-py3-none-any.whl", "sha256": "…", "bytes": N}`.
     못 만들었으면 `client_wheel: null` + `client_wheel_error: "<code>"`. 래퍼는 health 한 번으로 버전·경로·해시를 얻는다.
   - 응답: `Content-Type: application/zip` · `Content-Disposition: attachment; filename="…"` · `ETag: "<sha256>"` ·
     `If-None-Match` 일치면 304 · `HEAD` 지원. 인증은 `/api/status` 와 같은 읽기 규칙(`read_auth = none` 이면 없음, `basic` 이면
     토큰) — 공개 저장소의 코드라 산출물(§6, 언제나 토큰)과 달리 읽기 규칙을 따른다.
   - **wheel 은 어디서 오나 — 기동할 때 설치된 패키지에서 표준 라이브러리로 조립한다**(`zipfile` + `hashlib` + `importlib.metadata`).
     내용: `importlib.resources.files("remote_ci_monitor")` 아래의 `.py` · `web/` · `templates/`(`__pycache__` 제외) +
     `remote_ci_monitor-<X>.dist-info/`(`METADATA` 와 `entry_points.txt` 는 설치된 메타데이터를 그대로, `WHEEL` 과 `RECORD` 는 새로
     쓴다). 빌드 도구·hatchling 은 안 들인다(런타임 의존성 0). 기동 때 한 번, 메모리에 든다(수백 KB). editable 설치(운영이 그렇다)에서
     `git pull` 뒤 재시작 전이면 디스크의 파일이 도는 코드와 다를 수 있으므로 **기동 시점에 고정**한다 — 「도는 것을 준다」.
     조립 실패(파일·메타데이터 없음)는 `client_wheel: null` · 엔드포인트 503 + 이유. 옛 wheel·빈 wheel 은 주지 않는다(fail-open 금지).
   - 테스트: 조립한 wheel 을 **새 venv 에 `pip install`** 해 `rcm version` 이 서버와 같고 `rcm --help` 가 뜬다(smoke 처럼 pip 가 없으면
     skip) · 파일 목록이 `release.yml` 의 산출물 검사와 같은 셋(`web/index.html` · `templates/server.toml` · `templates/client.toml`)을
     담는다 · RECORD 의 해시가 맞다 · 이름이 다른 요청은 404 · `If-None-Match` 304 · `read_auth = basic` 에서 토큰 없으면 401.
   - 왜 이쪽이 근본인가: 서버 버전이 어디서 왔든(릴리스·dev·오프라인) 클라는 서버에게 묻고 서버에게 받는다. GitHub 는 런타임 경로에서
     빠진다(결정 30). 반대급부: 서버 프로세스가 자기 코드를 배포한다 — `read_auth = none` + LAN 이면 누구나 받는다(공개 저장소라 새
     노출은 아니다).

2. **릴리스는 빠짐없이 끊긴다** (보고의 안 A · 수정 채택 · 결정 82). 태그를 손으로 미는 단계를 없앤다: `main` 에 push 가 오면
   워크플로(`tag-release.yml` · `contents: write` · `main` 만)가 `__version__` 을 읽어 그 태그 `v<X>` 가 없으면 만든다. 있으면
   아무것도 안 한다. 그러면 `release.yml` 이 지금처럼 wheel·sdist·GitHub Release 를 만든다 — 단 **`GITHUB_TOKEN` 이 만든 태그는 다른
   워크플로를 깨우지 않는다**(GitHub 의 규칙) → `release.yml` 의 잡을 `workflow_call` 로 재사용해 태그 잡이 같은 run 에서 이어 부르거나,
   `release.yml` 에 `workflow_dispatch` 를 두고 `gh workflow run` 으로 부른다. 어느 쪽이든 「태그 = main 위 · `__version__` 과 같다」
   검사는 그대로. CONTRIBUTING 「Releasing」 3 은 「main 에 머지되면 태그와 릴리스는 자동」으로. 밀려 있는 **v0.2.5 는 오너가 지금**
   `git tag v0.2.5 <main sha> && git push origin v0.2.5` 로 끊는다(main 이 그 코드다 — 노트북들이 오늘 0.2.5 로 올라온다).
   단점 인정: 서버를 미출시 dev 로 돌리는 동안엔 A 만으로 못 따라온다 — 그래서 1 이 근본이고 2 는 규율이다.

3. **불일치가 보인다** (보고의 부수 개선 · 일부 채택 · 결정 83).
   - `/api/health.min_client_version` — 서버가 받는 가장 오래된 클라이언트 버전(서버 코드의 상수, 와이어 계약이 깨질 때만 올린다.
     지금 값은 `"0.2.0"` — 0.2.x 클라는 전부 붙는다). 클라는 자기 버전이 그보다 낮으면 `rcm check` 의 `client` 행이 빨강,
     `rcm run` 은 stderr 한 줄 경고(막지 않는다 — 실제 거부는 서버의 400 이 하고, 거기엔 이미 `hint` 가 있다).
   - `rcm check` 의 `server` 행 옆에 `client` 행: `client   v0.2.2 · server v0.2.6 · older — pip install <서버>/client/…whl`.
     같으면 `v0.2.6 · same as server`. `rcm version --json` 에 서버 값은 넣지 않는다(서버 없이도 돌아야 한다).
   - `rcm self-update` 는 **보류**. pip 로 자기 venv 를 갈아끼우는 일은 설치 방식(venv · pipx · uv · editable)마다 다르고 도는 프로세스
     자신을 바꾼다. 대신 `docs/operating.md` 에 **검증된 래퍼 예시**(health → `client_wheel.path`·`sha256` → 임시 파일로 받아 해시
     확인 → `pip install --upgrade <file>` → `rcm version` 재확인)를 두고 I1 처럼 테스트로 잠근다.

**지켜야 할 것(보고에서).** 릴리스 자산 URL 패턴(`releases/download/v<X>/remote_ci_monitor-<X>-py3-none-any.whl`)은 그대로 —
이미 여러 머신의 스크립트가 조립해 쓴다 · 옛 클라(0.2.2~0.2.4)가 계속 붙는다(health 키 추가만 · 새 경로만 · 기존 응답 불변) ·
`schema_version` 을 올려야 하면 릴리스 노트 맨 위에.

**확인 방법.** 서버를 새 버전으로 올린 뒤 아무 클라 머신에서 래퍼 한 번 → `rcm version` == `/api/health.version`. 새 venv 에서
`pip install http://<서버>/client/remote_ci_monitor-<X>-py3-none-any.whl` 이 성공한다. main 에 버전을 올린 PR 이 머지되면 태그와
GitHub Release 가 손 없이 생긴다.

**PR.** 6(`feat/server-serves-client-wheel`: I8-1 + I8-3 의 health 키·`check` 행·래퍼 예시) · 7(`ci/tag-release-on-main-version-bump`:
I8-2 + CONTRIBUTING). 릴리스 차단은 아니지만 **6 은 v0.2.6 에 넣기를 권장** — 운영을 0.2.6 으로 올리는 순간 노트북들이 서버에게서
받을 수 있다. 7 은 v0.2.6 태그 전에 들어가면 그 태그가 첫 자동 릴리스가 된다.

## 4. 이 명세가 발견한 기존 문제 (Codex 가 더한 것 포함)

1. **오프라인 GC 의 오류가 성공으로 보인다** — DB·스키마 조회 실패가 `inventory_error` 로 바뀐 뒤 빈 계획처럼 렌더되고 exit 0.
   불완전한 게이트는 exit 3 이어야 한다(B2-1 에 포함).
2. 실제 gc 의 `storage_after` 가 사후 측정값이 아니다(B3).
3. `freed_bytes` 가 실제 회수 바이트가 아니다 — unlink 성공 항목의 계획상 크기(B3).
4. **측정 실패 코드가 유실된다** — `_measure_dir()` 의 개별 실패가 `None` 으로 뭉개져 `error_code`·서버 로그에 구체 원인이
   안 남는다. 결정 55 의 「error_code 와 로그로 알린다」가 미완이다 → PR 3 에서 `measure_<errname>` 을 `inventory_error` 로.
5. 결정 62 latch 가 「첫 사유」 모델 때문에 빠질 수 있다(B4).
6. m5g §4.4 의 `charged`/`reclaimable` 분리가 구현에 없다(B4).
7. 백업 복원은 DB 만의 다운그레이드 — 손실 범위와 고아 디렉터리 처리를 절차에 적어야 한다(B2-3).
8. 문서 세 곳(`_offline_gc` docstring · 결정 61 · CHANGELOG)이 「읽기 전용」을 약속하는데 코드는 아니었다 — 「약속은 테스트가
   잠근다」가 이 레포의 규칙이고 그 테스트가 없었다.

## 5. PR 순서 · 완료 기준

| PR | 내용 | 완료 기준 |
|---|---|---|
| 1 웹 | B1 + I5(표본 `disk`·`job_storage` · 문구 단언 · 예외 수집기 · ubuntu 는 Chrome 필수) | 실배치 상태 문서로 렌더 · 콘솔 예외 0 · B1 을 되돌리면 그 테스트가 빨개진다 |
| 2 DB 안전 | B2-1 임시 사본 dry-run(exit 3 규칙) · B2-2 백업 · B2-3 메시지·복원 절차 · v16 · I7 의 `operating.md` 절차 | v7 픽스처(WAL 포함)로 dry-run 뒤 원본 불변 · 사본 안 남음 · 마감 초과 exit 3 · 백업 생성·검증·중단 · v16 픽스처 셋 · mutcheck 1종(원본을 열면 빨개짐) |
| 3 회계 | B4(shared · `floor_attempted` · 성공분 분모) · B3(스냅샷 `storage_before` · 사후 재측정 · 필드 분리) · I6 · §4-4 측정 실패 코드 | 운영 데이터 사본에서 `shared_bytes ≈ 17.5 GB` · 「would free」= 예상 회수량 · latch 픽스처(age 로 뽑힌 항목이 바닥을 채운 경우) · mutcheck 1종(`S_ISREG` 조건 제거) |
| 4 가드 | B2-5(명령 분류 · 유효 `data_dir` · `RCM_SERVER_DATA_DIR` · 운영 venv 판별) | `decide()` 케이스: `token --config <복사본>` deny · `gc --dry-run --config <운영>` allow · `serve` 기존 케이스 그대로 |
| 5 문서·손질 | I1 예시(테스트로 잠근 래퍼) · I2 · I3 · I4 · I7 나머지 · PLAN 결정 73~80 | 문서 잠금 테스트 · `source_ident` 픽스처 · 래퍼 테스트(종료 코드 보존 · 순서) |
| 1b 디스크 경로 | B5(`disk_path` 에 푼 `data_dir`) | `~` 설정으로 만든 서버의 표본에 `disk` 가 있다 · CHANGELOG Fixed 한 줄 |
| 6 클라 wheel | I8-1(`/client/<정확한 파일명>` · 기동 시 조립 · health `client_wheel`) · I8-3(`min_client_version` · `rcm check` `client` 행 · 래퍼 예시) | 새 venv 에 `pip install <서버 URL>` 성공 · 이름 불일치 404 · 304 · `basic` 401 · 조립 실패 시 `null`+503 · 문서 잠금 · 래퍼 테스트 |
| 7 자동 태그 | I8-2(`main` 의 `__version__` 상승 → 태그 → 릴리스 잡 호출) · CONTRIBUTING 「Releasing」 | 버전을 올린 PR 머지 뒤 손 없이 태그·Release 가 생긴다 · 같은 버전 재푸시는 무동작 · 태그≠`__version__` 검사 유지 |
| 릴리스 | PR 1~3 필수, 1b·4·6 권장 → **v0.2.6** → 이 Mac 운영 업그레이드(§6) | 재시작 뒤 `rcm check` 초록 · 웹 정상 · `user_version = 16` · #196 등의 라벨이 `last step` 으로 |

## 6. 이 Mac 의 운영 조치 (O1 · 결정 80)

상태: 운영 DB 스키마 15, v0.2.5 프로세스 정상, 재시작 시 기동 실패. 옛 빌드가 새 잡을 계속 완료하며 추론 라벨을 쓴다.

1. **수정 릴리스(v0.2.6)가 나올 때까지 재시작하지 않는다.** `PRAGMA user_version=7` 로 내리는 것은 기각 — `_already_added()` 가
   있어 「duplicate column」은 초안의 오류였지만, 스키마와 버전 표식이 어긋나고 옛 빌드가 M5h 이전 의미로 데이터를 더 쓴다.
   `backup/rcm.sqlite3.pre-0.2.4.bak`(9/9 10:25) 복원도 기각 — 그 뒤 잡 40여 건이 사라진다.
2. v0.2.6 을 **별도 venv/워크트리**에서 검증하고, 그 빌드의 임시 사본 dry-run(B2-1)으로 운영 데이터를 프리뷰한다.
3. `rcm pause`(admin) → `running/cancelling/uploading == 0` 을 확인하고 **즉시** 정지(pause 는 제출·업로드를 안 막으므로 확인과
   정지 사이가 길면 `uploading` 잡이 cancelled 된다. 10분 주기 제출원을 잠시 멈출 수 있으면 더 좋다).
4. 정지 직후 현재 v15 DB 의 online backup 을 따로 보존한다.
5. 운영 체크아웃 `git pull --ff-only` → 서비스 기동 → 첫 기동에서 v16 → `rcm check` · 웹 · `user_version` 확인 → `rcm resume`.

pause 를 빼면: 빈 큐 확인과 정지 사이에 10분 주기 제출이 claim 되어 `lost` 가 된다(Codex Q4). v16 은 라벨을 복구할 뿐 이 경쟁을
못 막는다.

## 7. 오너 결정 (73~80 · 기본값으로 구현하고 확인 대기)

| # | 결정 | 내용 |
|---|---|---|
| 73 | 오프라인 dry-run | 살아 있는 DB 는 `mode=ro` 로만 연다. `backup()` 사본 위에서 **실제 마이그레이션과 실제 계획**을 돌리고 사본을 지운다. 어느 단계든 불완전하면 exit 3 — 빈 계획 성공은 없다. 옛 DB(v7)도 이렇게 프리뷰한다(초안의 「스키마가 다르면 계획하지 않는다」를 대체) |
| 74 | 마이그레이션 전 백업 | `migrate()` 경계에서 실제 버전 상승 때 한 번, WAL 변경보다 먼저, `backup()`→검증→원자적 rename. **실패면 마이그레이션 중단.** 3개 보존(정리 실패는 경고). 옛 빌드의 거절 메시지가 복원 절차를 가리키고, 복원의 손실 범위를 문서가 밝힌다 |
| 75 | 가드 | `serve`·`worker` 기존 규칙 + `token`(전부 쓰기) + `gc --dry-run --config`(허용). 쓰기 명령은 유효 `data_dir`(`--data-dir` → `RCM_SERVER_DATA_DIR` → 선택된 설정의 `[server].data_dir` → 기본)이 운영이고 실행 파일이 운영 venv 밖이면 deny. TOML 파싱 실패·`PYTHONPATH` 우회는 범위 밖 |
| 76 | 회계 눈금 둘 | `charged`(링크마다 · 항목 표시·예산)와 `estimated_reclaimable = charged − shared`(일반 파일의 `nlink > 1` 블록 · 바닥·would free·latch). `floor_attempted` 로 latch 를 걸고 분모는 **삭제 성공분** |
| 77 | 실패 이름 | 계속 마커로만. `fail_patterns` 는 만들지 않는다. 검증된 래퍼 예시와 「판정 함수에서 마커」 예시를 문서에 |
| 78 | v16 | 한 번짜리 복구 마이그레이션 — 대장 행이 없는 실패 라벨은 `last_step` 으로, 취소·유실은 비운다. 매 기동 보정은 안 한다 |
| 79 | 웹 회귀 | 기존 Chrome 테스트를 넓혀 렌더·예외 스모크로 삼고 ubuntu 잡에서 필수로. ESLint 는 보류 |
| 80 | 업그레이드 절차 | 별도 venv 검증 → 사본 dry-run → pause → drain 확인 → 백업 → 정지 → pull → 기동(마이그레이션) → 확인 → resume. 도는 editable 체크아웃을 먼저 pull 하지 않는다 |
| 81 | 클라 wheel | 서버가 기동할 때 설치된 패키지에서 표준 라이브러리로 자기 wheel 을 조립해 `/client/remote_ci_monitor-<X>-py3-none-any.whl`(정확한 이름만)로 준다. `/api/health.client_wheel`(path · sha256 · bytes). 인증은 읽기 규칙. 조립 실패는 `null` + 503 — 옛 것·빈 것을 주지 않는다 |
| 82 | 자동 태그 | `main` 에 push 된 `__version__` 의 태그가 없으면 워크플로가 만들고 릴리스 잡을 같은 run 에서 부른다(`GITHUB_TOKEN` 의 태그는 다른 워크플로를 안 깨운다). 손 태그 단계는 문서에서 뺀다. 밀린 v0.2.5 는 오너가 지금 끊는다 |
| 83 | 불일치 표시 | `/api/health.min_client_version`(상수 · 계약이 깨질 때만) · `rcm check` 에 `client` 행 · `rcm run` 은 경고만. `rcm self-update` 는 보류 — 검증된 래퍼 예시를 문서에 |

## 8. 테스트 배치 · mutcheck

- `tests/test_offline_gc.py`(새): v7·v15 픽스처(WAL 있는 상태) · 원본 불변 · 사본 정리 · backup 마감 → exit 3 · 인벤토리 오류 → exit 3.
- `tests/test_store_m5i.py`(새): 백업 생성·검증·실패 시 중단·3개 정리 · v16 셋(옛 라벨 · 새 라벨+대장 · 상한 경계) · 거절 메시지.
- `tests/test_janitor_m5i.py`(새): `shared_bytes` 는 일반 파일만 · 예산은 charged · 바닥은 reclaimable · `floor_attempted` · 성공분
  분모 · 측정 실패 코드 · `measured_at` 최솟값 · gc 사후 재측정.
- `tests/test_web_browser.py`: 표본 확장 · 예외 수집기 · ubuntu 필수.
- `tests/test_guard_production.py`: 명령 분류 · 유효 `data_dir` · 운영 venv 판별.
- `tests/test_render_m5i.py`: `source_ident` 40-hex · `failure_lines` 구조화 사유 · gc 문구.
- `tests/test_hostsample_disk_path.py`(새 · PR 1b): `~` 설정 → `sampler.disk_path` 절대경로 · 표본에 `disk`.
- `tests/test_client_wheel.py`(새 · PR 6): 조립 wheel 의 파일 목록·RECORD 해시 · 새 venv `pip install` → `rcm version` 일치(pip 없으면
  skip) · 정확한 이름만 200 · 304 · `basic` 401 · 조립 실패 → health `null` + 503 · `min_client_version` · `rcm check` `client` 행 ·
  래퍼 예시(종료 코드 보존 · sha256 불일치면 설치 안 함).
- `tests/test_ci_release_workflow.py`(새 · PR 7): `tag-release.yml` 이 `main` push 에만 걸리고 `contents: write` 이며 릴리스 잡을 부른다
  (YAML 은 표준 라이브러리로 못 읽으니 문자열 잠금 — 이 레포의 다른 워크플로 테스트와 같은 방식).
- mutcheck 3종 추가: ① 오프라인 dry-run 이 사본 대신 원본을 연다 ② `S_ISREG` 조건 제거 ③ v16 의 `NOT EXISTS` 제거. PR 6 에 ④ 「이름이
  달라도 200」(정확한 파일명 검사 제거)을 더한다.

## 9. 위험

- 임시 사본은 디스크 여유가 없을 때 실패한다 — 그때 「모름」으로 멈추는 것이 맞고, 문구가 이유(`ENOSPC`)를 말해야 한다.
- 백업 중단이 마이그레이션을 막으면 서버가 안 뜬다(fail-closed) — 메시지가 원인과 다음 행동을 말해야 한다.
- B4 의 예상 회수량은 하한이라 바닥 규칙이 필요보다 더 지울 수 있다(대신 「지워도 안 는다」는 거짓은 없어진다).
- 가드의 TOML 해석이 CLI 의 탐색 순서와 어긋나면 오탐·미탐이 생긴다 — 같은 함수를 쓰거나 픽스처로 둘을 맞춘다.
- 이 문서의 실측은 한 머신·한 팀의 게이트에서 나왔다. 하드링크 비율·간헐 테스트는 그 팀의 사정이다.
- I8-1 의 조립 wheel 은 릴리스 wheel 과 **바이트가 같지 않다**(RECORD·WHEEL 을 새로 쓴다) — 같아야 하는 것은 설치 결과다. 테스트가
  「새 venv 에 설치 → `rcm version`·`--help`」로 그것을 잠근다. editable 설치의 `dist-info` 에 `METADATA` 가 없는 환경(아주 옛 pip)은
  조립 실패 → `null` + 503 으로 드러난다.
- I8-2 는 `main` 에 쓰기 권한을 가진 워크플로다 — 태그만 만들고(브랜치는 안 건드린다), 룰셋의 태그 보호와 맞춰야 한다.

## 10. 리뷰 반영

`docs/reviews/2026-09-10-codex-gate-replay-fixes-design.md` — 1라운드(항목별 판정 · 초안의 ✗ 세 곳 · 추가 발견 7) · 2라운드(쟁점 여섯의
결정). 본문의 「Codex Qn」 표기가 그 항목이다.

## 11. 세션 시작 프롬프트 (복사해서 붙여 넣기 — PR 1 부터)

```
이 레포(remote_ci_monitor)는 빌드 머신 한 대의 로컬 잡 서버다. Python 3.11+, 런타임 의존성 0, 표준 라이브러리만.

정본 — 이 순서로 먼저 끝까지 읽어라:
  1. CLAUDE.md · AGENTS.md — 브랜치 정책(브랜치 하나에 워크트리 하나 · main·dev 직접 push 금지 · 운영 체크아웃은
     절대 고치지 않는다) · 집안 규칙 · 검사 명령 · 이미 값을 치른 함정.
  2. docs/gate-replay-fixes-workplan.md — 이 세션이 구현할 작업 명세(확정본). §0 표 · §3 항목별 · §5 PR 순서 ·
     §7 결정 73~80 · §8 테스트 배치. 리뷰 근거는 docs/reviews/2026-09-10-codex-gate-replay-fixes-design.md.
  3. PLAN.md 의 「fail-open 금지」·「저장소」·「설정」·결정 51~72 — M5g·M5h 가 정한 것을 이 작업이 잇는다.
  4. .claude/skills/branch · commit · pr — 브랜치를 만들거나 커밋하거나 PR 을 열기 **전에** 반드시 부른다.
     tools/guard_naming.py 훅이 규약에 안 맞는 이름·제목을 거부한다(정본 CONTRIBUTING.md 「Names」).

지금 상태(2026-09-10):
  - dev 는 M5g·M5h 를 담고 있고 아직 미출시다. 실배치 재현에서 릴리스 차단 버그 둘이 나왔다 — 웹이 통째로 죽는
    ReferenceError(B1)와 「읽기 전용」 오프라인 dry-run 이 운영 DB 를 마이그레이션한 것(B2).
  - ⚠️ 이 Mac 의 운영 인스턴스(~/Documents/GitHub/remote_ci_monitor · 8787 · ~/.local/share/rcm)는 DB 스키마가
    15 인 채 v0.2.5 로 돌고 있다. **재시작하면 안 뜬다.** 절대 손대지 말고, 명세 §6 의 절차는 오너가 한다.
  - 시험용 서버는 자기 설정·포트(8790 이상 · 8788 은 다른 세션)·data_dir 로 띄운다. data_dir 은 /tmp 아래에 두지
    마라(dolomood 게이트의 selftest 가 거부한다) — ~/.local/share/rcm-devtest 처럼 $HOME 아래에.
  - 이 워크트리는 PR 1 을 위한 것이다: 브랜치 fix/web-host-card-undefined-local. PR 마다 새 워크트리를 판다.

이번 세션 목표 — 명세 §5 의 PR 1 → 2 → 3 (릴리스 전제), 그 다음 4 · 5.
  PR 1 (이 워크트리): B1 + I5.
    - web/app.js hostCardHtml 의 `local` → `h.source === "local"` 을 그 자리에.
    - tests/test_web_browser.py: 로컬 표본에 disk, 상태 문서에 server.job_storage, 카드 문구(두 언어) 단언,
      페이지 초기화 전에 error·unhandledrejection 수집기 → 0건 단언. 고치기 전에 빨간 것을 먼저 본다.
    - ci.yml: ubuntu 잡에서 Chrome 미발견을 skip 이 아니라 실패로(macOS 는 skip 허용). RCM_CHROME 이 그 열쇠다.
    - CHANGELOG [Unreleased] Fixed 한 줄(사용자에게 보이는 변화: 웹이 뜬다).
  PR 2 (새 워크트리 예: fix/gc-offline-dry-run-on-db-copy): B2 — 임시 사본 dry-run(exit 3 규칙) · migrate() 백업 ·
    v16 · 거절 메시지 · operating.md 절차(결정 80). 테스트는 명세 §8. mutcheck 에 「사본 대신 원본을 연다」 변이.
  PR 3 (예: fix/janitor-shared-bytes-and-latch): B4 · B3 · I6 · §4-4. mutcheck 에 「S_ISREG 조건 제거」 변이.
  PR 4 (예: feat/guard-deny-writes-to-production-data): 결정 75.
  PR 5 (예: docs/fail-marker-examples-and-list-polish): I1 · I2 · I3 · I4 · I7 나머지 · PLAN 결정 73~83.
  PR 1b (예: fix/hostsample-disk-path-expanduser): B5 — 서버 샘플러의 disk_path 에 푼 data_dir. 10분짜리, PR 1 바로 뒤.
  PR 6 (예: feat/server-serves-client-wheel): I8-1 · I8-3 — 기동 시 조립한 자기 wheel 을 /client/<정확한 파일명> 으로,
    health 에 client_wheel · min_client_version, rcm check 의 client 행, operating.md 의 래퍼 예시(테스트로 잠근다). v0.2.6 권장.
  PR 7 (예: ci/tag-release-on-main-version-bump): I8-2 — main 의 __version__ 상승이 태그와 릴리스를 만든다(GITHUB_TOKEN 의
    태그는 워크플로를 안 깨우니 릴리스 잡을 같은 run 에서 부른다) · CONTRIBUTING 「Releasing」.
  릴리스: PR 1~3 이 dev 에 들어간 뒤 v0.2.6(1b·6 권장) — CONTRIBUTING 「Releasing」. 운영 업그레이드는 오너.
  오너가 지금 할 것: 밀린 v0.2.5 태그(`git tag v0.2.5 <main sha> && git push origin v0.2.5`) — 노트북들이 오늘 0.2.5 로 올라온다.

지킬 것:
  - 테스트 먼저. 고치기 전에 빨간 테스트를 보고, 고친 뒤 초록을 본다. 테스트를 약하게 만들어 통과시키지 않는다.
  - 순수 계층(core/)은 I/O 도 시계도 안 본다. 스키마 v1 은 키 추가만. 모르는 값은 null, 「모른다」는 exit 3.
  - 식별자·CLI 도움말·문서는 영어, 주석·docstring·커밋 본문은 한국어. 커밋 제목은 `<type>(<scope>): 한국어 요약`.
  - 사용자에게 보이는 변화는 docs 스킬의 체크리스트(CHANGELOG · docs · 미러).
  - PR 전 검사: ruff check . && ruff format --check . && pytest · node --test tests/web/*.test.js ·
    python scripts/mutcheck.py. gh 는 토큰을 고정한다: export GH_TOKEN=$(gh auth token --user monocsp).
    머지는 CI 초록 뒤 REST(`gh api -X PUT repos/monocsp/remote_ci_monitor/pulls/N/merge -f merge_method=merge
    -f commit_title="<제목> (#N)"`), 그 뒤 원격 브랜치 삭제 · git worktree remove. pr 스킬이 이 절차다.
  - Codex 크로스리뷰는 오너가 시키기 전엔 돌리지 않는다(한 줄로 제안만).
  - 운영 경로(~/.config/rcm · ~/.local/share/rcm · 운영 체크아웃)를 여는 명령은 어떤 것도 실행하지 않는다 —
    dev 의 rcm 이 DB 를 열면 마이그레이션한다(이 명세의 B2 가 그 사고다).

끝나면 짧게 보고해라: PR 번호와 머지 커밋 · 테스트 수와 mutcheck 결과 · 실기로 확인한 것(어느 포트·어느 잡) ·
다음 PR 의 브랜치 이름 · 오너가 할 일(운영 업그레이드 절차 §6).
```
