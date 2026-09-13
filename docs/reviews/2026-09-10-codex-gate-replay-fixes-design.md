# Codex 크로스리뷰 — 게이트 재현이 찾은 수정과 개선(M5i) 설계 (2026-09-10)

> 대상: `docs/gate-replay-fixes-workplan.md`(초안 v0.1 — 실배치 재현이 찾은 수정·개선) · 오너 지시로 실행 ·
> `codex exec --sandbox read-only`(`gpt-5.6-sol`, reasoning high) · 두 라운드(같은 세션을 `resume`).
> 1라운드 297,403 토큰, 2라운드는 §4. 판정은 **「수정해서 채택」이 대부분** — 방향은 맞지만 초안의 세 곳이
> 틀렸고(§2 의 ✗), 초안에 없던 P0 하나(v16)가 나왔다. 전부 확정본에 반영했다.

## 1. 준 프롬프트 (1라운드 · 전문)

```
이 레포(remote_ci_monitor)는 빌드 머신 한 대의 로컬 잡 서버다. Python 3.11+, 런타임 의존성 0, 표준 라이브러리만.
`PLAN.md` 가 정본이고 `AGENTS.md` 에 집안 규칙이 있다. 지금 체크아웃은 dev(19ac760, M5g·M5h 포함, 미출시)다.

`docs/gate-replay-fixes-workplan.md` 는 오늘(2026-09-10) dev 를 별도 인스턴스로 띄워 운영(main v0.2.5)이 돌린 게이트 커밋 11개를
다시 넣어 본 결과에서 나온 **수정·개선 초안**이다. 이것을 **설계 크로스리뷰** 해 달라. 항목마다 「채택 · 수정해서 채택 ·
보류 · 기각」을 판정하고, 반대하면 근거와 대안을 구체적으로 적어라. 동의하면 짧게.

읽을 것: docs/gate-replay-fixes-workplan.md(전부) · PLAN.md(「fail-open 금지」·「저장소」·「설정」·결정 51~72) ·
src/remote_ci_monitor/store.py(Store.__init__ · migrate · _MIGRATIONS 8~15) · cli.py(_offline_gc · cmd_gc · cmd_check 의
data dir 행 · _wait 끝줄) · janitor.py(_measure_dir · inventory · plan · gc_report · storage) · server.py(App.gc) ·
core/render_text.py(source_ident · failure_lines · render_gc) · core/retention.py · web/app.js(hostCardHtml 1698~1730 ·
renderHost · render · 갱신 루프) · tools/guard_production.py(_segment_verdict · _server_verdict) · docs/operating.md 「Upgrading」 ·
docs/m5g-workplan.md §4.4~§4.5 · §5.5 · 결정 54·55·57·61·62 · docs/m5h-workplan.md §4.2 · §6.

실측(다시 재지 마라, 초안 §2·§3 에 있다): 운영 v0.2.5 의 DB_VERSION 은 7 이고 그 migrate() 도 `version > DB_VERSION` 이면
StoreError 를 낸다(내가 운영 체크아웃에서 확인했다). 운영 DB 는 지금 user_version 15 다. 운영 워크스페이스 77개 54.5 GB 중
17.5 GB 가 nlink>1 인 git pack 이다. dev 웹은 app.js:1722 의 `local` 로 매 렌더마다 ReferenceError 가 난다.

특히 다음을 따져 달라.
 1. B2 — `Store(readonly=True)` 가 옳은 모양인가, 아니면 `_offline_gc` 만 `mode=ro` 로 열고 migrate 를 건너뛰는 얇은 길이
    나은가. `plan()` 이 읽는 컬럼 집합이 v7 DB 에 다 있는가(있다면 옛 DB 에서도 계획을 내도 되는가). 백업을 migrate() 안에
    두는 것과 서버 기동 절차에 두는 것 중 어느 쪽인가. 백업 실패 시 마이그레이션 중단(fail-closed)이 맞나. WAL 이 있는 DB 를
    `mode=ro` 로 여는 함정.
 2. B2 가드 — 「운영 venv 밖의 rcm 이 운영 경로를 열면 deny」의 오탐·미탐. `python -m remote_ci_monitor` · `$RCM_CONFIG` 환경변수 ·
    `./rcm.toml` 경로. `serve`·`worker` 의 기존 규칙과 겹치는 부분.
 3. O1 — 이 Mac 의 운영 DB 를 어떻게 하는 것이 맞나. 초안은 「재시작 금지 → 수정 릴리스로 업그레이드」다. 더 안전한 길이 있나
    (예: 옛 빌드가 `version > DB_VERSION` 을 **경고로 낮추고** 아는 컬럼만 쓰는 것 — 나는 이것이 fail-open 이라 반대한다).
 4. B4 — 공유 바이트를 「합계·회수 예상에서만 빼고 항목 값은 링크마다」 두는 것이 맞나. 바닥 규칙(min_free_bytes)과 결정 62
    latch 와의 상호작용. 예산 규칙은 어느 값을 봐야 하나.
 5. I1 — `fail_patterns` 를 기각한 것이 맞나. 마커만 믿는다는 원칙과 「팀이 스크립트를 안 고치면 M5h 가 죽어 있다」는 현실
    사이에서. 래퍼 예시를 examples/ 에 두는 것의 함정(래퍼가 PIPESTATUS 로 종료 코드를 보존하는 것 · 버퍼링 · 마커 순서).
 6. I5 — `npx eslint no-undef` 를 CI 에 넣는 것 vs headless Chrome 스모크 vs DOM 스텁. 「런타임 의존성 0」 원칙과의 관계,
    CI 시간·네트워크 의존, 실제로 B1 류를 잡는 힘.
 7. B3·I6 — 「would remain」을 계획이 잰 값으로 바꾸면 결정 57(「보여준 것과 실제가 같다」는 보장이 아님)과 어긋나는가.
    회계 나이 표시 문구.
 8. 내 원인 분석이 코드와 맞는지 — B1(app.js 1722) · B2(cli.py _offline_gc → Store.__init__ → migrate) · B3(server.py App.gc 의
    순서) · B4(janitor._measure_dir) · I3(render_text.failure_lines) · I2(source_ident). 틀린 곳을 지적하라.
 9. 초안이 놓친 것 — 이 재현이 드러냈는데 초안에 없는 문제, 또는 채택하면 다른 곳이 깨지는 항목.
10. 우선순위와 PR 묶음이 맞는가. 릴리스 전제(P0)로 삼을 것은 무엇인가.

출력 형식(한국어): 항목마다 `### B1` 처럼 머리말 → 첫 줄에 **판정** → 근거 → 놓친 위험 → 대안(있으면). 마지막에
「추가 발견」과 「우선순위 표」. 코드를 고치지 마라 — 읽기 전용이다. 사실과 추측을 구분해서 적어라.
```

## 2. 1라운드 — 항목별 판정과 대조

「대조」는 Codex 의 말을 내가 코드로 다시 확인한 것이다. ✗ 는 **초안이 틀렸던 곳**.

| 항목 | Codex 판정 | 핵심 근거·대안 | 대조 |
|---|---|---|---|
| B1 | 채택 | 원인 정확. 로컬 표본 `source="local"`, 원격 `source="worker"`. 「모든 실배치」는 「`disk` 가 있는 로컬 배치」로. **이미 있는 `tests/test_web_browser.py` 의 표본에 `disk`·`job_storage` 가 없어 지나쳤다** — 새 체계보다 그 표본을 넓혀라 | ✅ `hostsample.py:262` · `remote_workers.py:817`. CI(ubuntu)에서 그 테스트가 실제로 돈다(3147 passed · 0 skipped) |
| B2 읽기 전용 | 수정해서 채택 | `Store(readonly=True)` 반대 — 쓰기 메서드·`PRAGMA journal_mode=WAL`·`mkdir` 이 읽기 전용과 안 맞는다. 순수 규칙이 요구하는 열은 `id/state/created_at/finished_at`(v1 부터) 이라 **v7 에서 계획은 타당**하지만 `_row_to_job()` 이 `last_step`·`fail_truncated` 를 읽어 지금 구현은 v7 에서 죽는다. 더 나쁘게 그 예외가 `inventory_error` 로 삼켜져 **빈 계획 + exit 0** 이 된다(fail-open). `mode=ro` 는 `-shm` 접근이 필요하고 `immutable=1` 은 금지, URI 는 절대경로·인코딩 | ✅ `store.py:623` · `janitor.py:407~` · `cli.py:685 return 0`. ✗ 초안 결정 73 의 「스키마가 다르면 계획하지 않는다」는 「새 버전 또는 필요한 열이 없는 버전만 거절」로 |
| B2 백업 | 채택 | 위치는 `migrate()` 경계(모든 진입점을 덮는다). 실제 버전 상승 때 한 번 · WAL 변경보다 먼저 · `Connection.backup()` → 임시 파일 → 검증 → 원자적 rename · **생성·검증 실패는 중단** · 3개 정리는 성공 뒤에, 정리 실패는 경고만 · 복원 안내에 서비스 정지와 WAL/SHM 처리 | ✅ |
| B2 가드 | 수정 필요 | 「운영 venv 밖 rcm 전부 deny」는 과도(결정 61 과 충돌 — 새 빌드로 프리뷰하는 것이 목적). 미탐: `RCM_CONFIG=` 접두를 `_argv` 가 떼기만 함 · `env RCM_CONFIG=` · `./rcm.toml` 이나 `--config` 파일 안의 `data_dir` 이 운영일 수 있음. 가드는 방어층일 뿐 핵심은 읽기 전용 코드 | ✅ `guard_production.py:511` 은 `RCM_CONFIG` 를 플래그로만 쓴다 |
| B3 | 수정해서 채택 | `App.gc()` 순서 맞음. 결정 57 과 무충돌. **추가**: 실제 gc 의 `storage_after` 도 삭제 뒤 재측정이 아니고 `freed_bytes` 는 계획상 합이다 → `estimated to free … · projected to remain` 문구, 사후 재측정, 필드 의미 분리 | ✅ `server.py:748~750` |
| B4 | 수정해서 채택 | 「합계·회수에서만 빼자」는 **m5g §4.4 가 이미 정한 `charged_bytes`/`reclaimable_bytes`** 를 조용히 바꾼다 → 그 표대로: 항목·예산은 charged, 바닥·would free·latch 분모는 reclaimable(이름은 `estimated_reclaimable_bytes`). **latch 구멍**: `floor_ran = any(reason == "free")` 라 age/budget 로 먼저 뽑힌 항목이 바닥을 채우면 latch 가 안 선다 → `floor_attempted` | ✅ ✗ **명세의 분리가 코드에 없다**(`reclaimable`·`charged` 가 src 에 없음) — 초안은 이를 새 설계로 적었다. `janitor.py:465` 확인 |
| I1 | 수정해서 채택 | `fail_patterns` 기각 동의(형식 변경으로 거짓 이름 · 병적 정규식이 로그 소비를 막음 · 계약 둘). M5h 가 다 죽은 건 아니다(추론 제거·`last_step`·길 안내는 유효). 래퍼는 bash 고정·PIPESTATUS 즉시 복사·`set -e -o pipefail` 주의·`tee`·버퍼링 문서화·정확한 형식만·제어문자·임시 로그 | ✅ |
| I2 | 수정해서 채택 | 「접두」는 넓다(우연히 sha 접두와 같은 브랜치) → 40-hex 완전 일치만 | ✅ |
| I3 | 수정해서 채택 | `state is None` 만 보면 첫 조회 전 네트워크 끊김(exit 3)에서 로그 길을 잃는다 → 구조화된 종료 사유(`not_found`·`unreachable`·`timeout`), 확정 404 만 생략 | ✅ |
| I4 | 채택 | 서버는 `data_dir` 을 API 로 안 내리니 로컬 사실을 버릴 필요 없음 → `local data dir · from <config>` | ✅ |
| I5 | 수정해서 채택 | 무고정 `npx eslint` 반대(네트워크·버전·재현성). **기존 Chrome 테스트 표본을 넓혀 B1 경로를 실행**하고 ubuntu 잡에서 Chrome 부재를 실패로. 「Chrome 은 릴리스 때만」은 PR 전에 못 막아 부족 | ✅ |
| I6 | 수정해서 채택 | `plan()` 이 캐시(최대 24h)를 써도 `_measured_at = now` 로 덮는다 → 나이를 그대로 보이면 새 거짓말. 집계 `measured_at` 은 기여한 값 중 **가장 오래된** 측정 시각. 종료 시 측정은 보류 동의 | ✅ `janitor.py:426` |
| I7 | 수정해서 채택 | `operating.md` 는 「읽기 전용」 한 줄이 아니라 순서를 바꿔야: 별도 venv 검증 → 읽기 전용 프리뷰 → pause → 백업 → 정지 → pull → 기동 → 확인. CHANGELOG 는 기계·잡 번호 대신 일반화 | ✅ |
| O1 | 수정해서 채택 | 경고 완화안 기각(v15 는 데이터 마이그레이션 포함 — fail-open). **초안이 놓친 P0**: 사고 뒤에도 옛 v0.2.5 가 실패 잡에 추론 라벨을 `failed_step` 에 계속 쓴다. v15 는 재실행되지 않으므로 **v16 복구 마이그레이션** 필요. 「`user_version=7` 이면 duplicate column」은 틀림(`_already_added()` 가 건너뛴다) — 그래도 수동 하향은 기각 | ✅ ✗ 운영 #196·#199·#200 이 `failed_step` 만 있고 `last_step` 없음. `store.py:385` |

### 추가 발견 (Codex)

1. 오프라인 GC 의 DB·스키마 오류가 `inventory_error` → 빈 계획 → exit 0 으로 보인다(fail-open). 불완전한 게이트는 exit 3.
2. 실제 gc 의 `storage_after` 가 사후 측정값이 아니다.
3. `freed_bytes` 는 unlink 에 성공한 항목의 **계획상** 크기다.
4. `_measure_dir()` 의 개별 실패가 `None` 으로 뭉개져 `error_code`·로그에 구체적 원인이 안 남는다(결정 55 미완).
5. 결정 62 latch 가 「첫 사유」 모델 때문에 빠질 수 있다.
6. 초안 결정 73 과 본문 §6 이 모순된다(후자가 맞다).
7. 백업 복원은 DB 만의 다운그레이드 — 그 뒤 생긴 잡 행·디렉터리의 손실 범위와 고아 처리를 절차에 적어야 한다.

## 3. 2라운드에 넘긴 쟁점

Q1 B2 의 모양(전용 리더 vs 임시 사본) · Q2 가드의 최소 구성 · Q3 v16 술어 · Q4 pause 가 필요한가 · Q5 B4 최소 구현과
`floor_attempted` · Q6 ESLint 가 비용을 넘나. 프롬프트 전문과 판정은 §4.

## 4. 2라운드 (같은 세션 `resume` · 31,453 토큰)

### 4.1 준 프롬프트 (전문)

```
1라운드 고맙다. 네 판정을 코드로 대조해 다음을 확인했다(사실): `_already_added()` 가 있어 「user_version=7 로 내리면
duplicate column」은 내 오류였다 · `tests/test_web_browser.py` 는 ubuntu CI 에서 실제로 돈다(3147 passed, 0 skipped;
macOS 러너는 3 skipped) — 표본에 `disk` 가 없어 B1 을 지나쳤다 · `plan()` 은 인벤토리 예외를 `inventory_error` 로 삼키고
`_offline_gc` 는 exit 0 이다 · m5g 명세 §4.4 의 `charged`/`reclaimable` 분리는 **코드에 없다**(retention.py·janitor.py 에
그 이름이 없다) · 사고 뒤 옛 v0.2.5 가 완료한 #196·#199·#200(failed) 은 `failed_step` 만 있고 `last_step` 이 없다 —
네가 말한 v16 이 필요하다. 이제 남은 쟁점 여섯을 결정하자. 항목마다 **하나를 골라** 근거를 적어라.

Q1. B2 의 모양 — 두 안을 비교하고 하나를 골라라.
  (a) 네 안: 오프라인 전용 얇은 `ReadOnlyVolumeStore`(id/state/created_at/finished_at 만, `PRAGMA table_info` 로 열 확인,
      `mode=ro` URI, WAL 픽스처 테스트).
  (b) 임시 사본 안: 살아 있는 DB 를 `Connection.backup()` 으로 임시 파일에 복사 → 그 사본을 **보통의 `Store`** 로 열어
      마이그레이션(사본만 바뀐다) → `Janitor(plan)` 을 사본 DB + 실제 `workspaces/` 로 돌린다 → 사본 삭제.
      장점: 계획 함수와 행 변환기를 그대로 공유해 결정 57(같은 계획 함수)이 자명하고, v7 이든 v15 든 같은 코드 경로다.
      단점: 임시 파일 쓰기(운영 DB 2 MB · 5만 잡이면 수십 MB), 사본에서 데이터 마이그레이션(v14·v15)이 돌아 계획에
      영향을 주는가(주지 않을 것이다 — 계획은 state·시각·크기만 본다), `backup()` 이 살아 있는 writer 와 경합할 때 재시도.
  어느 쪽이 「올리기 전에 새 규칙으로 본다」에 더 안전하고 단순한가. 둘 다 exit 3 규칙(불완전하면 모른다)은 적용한다.

Q2. 가드의 범위 — 네 6개 대안 중 훅(Claude Code 만 막는 방어층)에 걸맞은 **최소** 구성을 골라라. 내 제안:
  (i) `serve`·`worker` 의 기존 규칙 유지 (ii) 로컬 DB 를 여는 하위 명령만 본다: `token`(쓰기) · `gc --config`(읽기 전용이 된 뒤)
  · `serve` · `worker` (iii) `--config`/`--data-dir`/`RCM_CONFIG=`/`env RCM_CONFIG=` 가 운영 경로면: 실행 파일이 운영 venv 밖일 때
  쓰기 명령은 deny, `gc --dry-run --config` 는 **allow**(B2 이후) (iv) TOML 안의 `data_dir` 해석은 **안 한다**(복잡도 대비 이득이
  작다 — 그 경우는 읽기 전용 코드가 막는다). 이 최소 구성의 구멍 중 실제로 위험한 것이 있나.

Q3. v16 복구 마이그레이션의 술어를 검증해 달라. 새 코드는 `step-end::fail`·`::rcm::fail::` 로 선언된 라벨을 쓸 때 **항상**
  `job_failures` 행도 남긴다(검증 라운드 5). 그러므로 「`failed_step IS NOT NULL` 이고 `job_failures` 에 그 잡의 행이 없는
  failed/timed_out 잡」= 옛 코드가 쓴 추론 라벨이다. 술어:
    UPDATE jobs SET last_step = COALESCE(last_step, failed_step), failed_step = NULL
     WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL
       AND id NOT IN (SELECT job_id FROM job_failures);
    UPDATE jobs SET failed_step = NULL, last_step = NULL WHERE state IN ('cancelled','lost');
  틀린 경우가 있나(예: 새 코드가 선언 라벨을 쓰면서 대장을 안 남기는 경로, 원격 워커 경로, `timed_out`).
  그리고 v16 은 「한 번」이 아니라 「옛 빌드가 다시 쓸 수 있으니 매 기동마다」 돌아야 하나 — 나는 아니라고 본다(v16 이후 옛 빌드는
  `version > DB_VERSION` 으로 못 뜬다).

Q4. O1 절차에서 「큐를 pause」가 정말 필요한가. 운영은 10분마다 게이트가 들어오는 팀의 머신이다. v16 이 옛 라벨을 정리하므로
  pause 없이 「큐가 빈 순간에 정지–업그레이드–기동」으로 충분하지 않은가. pause 를 빼면 무엇을 잃나.

Q5. B4 — 명세 §4.4 의 분리를 **이제 구현**하는 최소 모양: `VolumeItem` 에 `shared_bytes`(nlink>1 블록) 추가 → 예산은
  charged(기존 값), 바닥·would free·latch 분모는 `bytes - shared_bytes`. 캐시 `_sizes` 를 `(bytes, shared, mtime, at)` 로.
  이름은 `estimated_reclaimable_bytes`. 그리고 latch: `floor_ran` 을 `plan.floor_attempted`(바닥 부족분 > 0 이었나) 로 바꾼다.
  이 모양에 동의하나. 반대급부는?

Q6. I5 — ESLint 없이 「기존 Chrome 테스트 표본에 disk·job_storage 를 넣고 카드 문구·콘솔 예외 0 을 단언 + ubuntu 잡에서
  Chrome 부재를 실패로」만으로 B1 류를 막기에 충분한가. ESLint 를 더할 가치가 그 비용(package.json·lockfile·npm ci)을 넘나.

마지막에 「최종 결정 표」(항목 · 결정 · 릴리스 전제 여부 · PR)를 다시 내라. 코드는 고치지 마라.
```

### 4.2 판정

| 쟁점 | 결정 | 근거·조건 |
|---|---|---|
| Q1 B2 모양 | **(b) 임시 사본** | 원본은 `mode=ro` 연결만(쓰기 PRAGMA 금지) · 사본에서 **실제 `Store.migrate()`** 가 돌아 마이그레이션 실패까지 재시작 전에 드러난다 · 행 변환기·`Janitor.plan()` 을 그대로 써 결정 57 이 자명 · 호환 DTO 를 영구 유지할 필요 없음. 조건: backup 은 페이지 단위 + 전체 마감, 못 끝내면 exit 3 · 임시 디렉터리 0700, DB·`-wal`·`-shm`·자동 백업까지 통째로 삭제 · 파일 스캔은 실제 `data_dir` · 어느 단계든 불완전하면 exit 3. 반대급부: 디스크가 없으면 사본이 실패한다 — 그때 「모름」으로 멈추는 것이 맞다 |
| Q2 가드 | (i)~(iii) 채택, **(iv) 기각** | `token` 은 `list` 도 Store 를 열어 마이그레이션하므로 전부 쓰기로 분류 · `gc --dry-run --config` 는 Q1 뒤 명시 허용 · 나머지 원격 클라이언트 명령은 제외. **TOML 을 안 읽으면 실제 구멍**: `--config /tmp/copied.toml` 안의 `data_dir` 이 운영이면 `token list` 가 통과한다. 쓰기 명령에 한해 CLI 와 같은 우선순위(`--data-dir` → `RCM_SERVER_DATA_DIR` → 선택된 설정의 `[server].data_dir` → 기본)로 유효 `data_dir` 을 계산. `RCM_SERVER_DATA_DIR=`·`env …` 도 본다. TOML 을 못 읽으면 CLI 도 먼저 실패하므로 deny 불필요 · `PYTHONPATH` 우회는 범위 밖 |
| Q3 v16 | **채택**(`NOT EXISTS` 형태) · 한 번짜리 | `step-end::fail`·`::rcm::fail::` 둘 다 `fail_names` → `Store.finish()` 가 대장 삽입을 같은 트랜잭션에 · 원격 워커도 `outcome_for()` 재계산으로 동일 · `timed_out` 은 `labelled=True` · 비마커 실패(자재화·워커 예외)는 `failed_step` 을 안 쓴다 → 「라벨은 있는데 대장 행이 없다」= 옛 추론 라벨. 경계: 이름 100개 상한 뒤의 `step-end::fail` 은 대장에 없어도 그 잡엔 다른 행이 있어 「행이 하나라도」 술어가 맞다(이름 일치로 바꾸면 틀린다). 매 기동 보정은 기각 — 열려 있던 옛 프로세스는 버전을 다시 안 보므로 **「옛 프로세스 정지 확인 후 마이그레이션」이 배포 절차의 필수 조건** |
| Q4 pause | **유지** | v16 은 라벨을 복구할 뿐, 정지 직전 claim 된 잡이 `lost` 되는 경쟁을 못 막는다. pause → `running/cancelling/uploading == 0` 확인 → 즉시 정지. pause 는 제출·업로드는 안 막으므로 완전 무손실이 필요하면 제출원 중지나 별도 drain 상태가 필요(이번 범위 밖) |
| Q5 B4 | **동의 + 보완 셋** | ① 디렉터리의 `st_nlink > 1` 은 정상이라 **일반 파일(`S_ISREG`)만** shared 로 센다(tar 도 같은 규칙) ② latch 분모는 계획 전체가 아니라 **삭제에 성공한 항목**의 예상 회수량(분모 0 이면 판정 안 함) — `apply()` 가 성공 id/바이트를 돌려줘야 한다 ③ `floor_attempted` = `free_bytes 를 알았고 min_free_bytes > 0 이며 계획 시작 시 free < min`. 반대급부: 예상 회수량은 보수적 하한 · 외부 링크 변화는 최상위 mtime 을 안 바꿔 캐시가 낡을 수 있다(첫 회차 뒤 latch 가 피해를 제한) · 필드가 `VolumeItem`·`PurgeItem`·JSON·렌더러·캐시·테스트로 전파 |
| Q6 I5 | **ESLint 보류**, Chrome 테스트 강화 | 로컬 표본에 `disk` + 상태 문서에 `server.job_storage` + 카드 문구(두 언어) 단언 + 초기화 전 `error`·`unhandledrejection` 수집기로 0건 단언 + ubuntu 필수 잡은 Chrome 미발견을 **실패**로(macOS skip 허용). ESLint 는 Node 패키지 체계(lockfile·registry·공급망)를 새로 들이는 비용이 지금은 더 크다 — 정적 규칙이 두셋 필요해지면 pinned 으로 도입. 무고정 `npx` 는 계속 기각 |

최종 표(Codex): PR 1(웹 수정·회귀) → PR 2(DB 안전: 사본 dry-run · 백업 · v16 · 절차) → PR 3(회계: charged/shared · latch · 사후 재측정 · B3) 이 릴리스 전제, PR 4(가드)는 방어층이라 권장. 확정본 `docs/gate-replay-fixes-workplan.md` §5 가 이 순서를 따른다.

## 5. 반영

확정본 v1.0 에 전부 반영했다 — 결정 73~80(§7), 초안이 틀렸던 세 곳(결정 73 문구 · `duplicate column` · B4 를 새 설계로
적은 것)은 본문에서 고치고 이 문서 §2 의 ✗ 로 남긴다.
