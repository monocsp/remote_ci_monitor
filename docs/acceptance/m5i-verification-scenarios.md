# M5i 검증 시나리오 (2026-09-10)

> 목적: `docs/gate-replay-fixes-workplan.md`(M5i) 의 PR 들이 **전부 `dev` 에 들어간 뒤**, 명세가 약속한 것이
> 실제로 되는지를 명세를 근거로 다시 본다. 단위 테스트가 초록인 것과 별개다 — 2026-09-10 의 B1 은 3147개가
> 초록인 채로 웹을 통째로 죽였다(표본에 `disk` 가 없었다). 그래서 여기의 판정은 「테스트가 있다」가 아니라
> **「실제 모양의 입력으로 실제 경로를 지났다」** 다. 보고서는 `docs/acceptance/reports/<날짜>-m5i-verify-<영역>.md`.
>
> 실행 주체는 **격리 에이전트**다(§0). 발견한 오류는 보고만 하지 않고 **테스트 → 수정 → PR** 까지 한다.

## 0. 실행 규칙 (격리 에이전트 공통)

| 규칙 | 내용 |
|---|---|
| 워크트리 | `origin/dev`(모든 M5i PR 머지 뒤)에서 `verify/<영역>` 브랜치의 **자기 워크트리** + 자기 `.venv`. 다른 워크트리를 건드리지 않는다 |
| 운영 | `~/Documents/GitHub/remote_ci_monitor` · `~/.config/rcm` · `~/.local/share/rcm` 은 **읽지도 열지도 않는다**. dev 의 `rcm` 이 DB 를 열면 마이그레이션한다 — 그 사고가 이 명세다 |
| 시험 서버 | 자기 설정 파일 · 포트(영역마다 §1 의 배정) · `data_dir` 은 `$HOME` 아래(`~/.local/share/rcm-verify-<영역>`), `/tmp` 아래 금지 · 알림 훅 없음 · 끝나면 서버를 내리고 `data_dir` 을 지운다 |
| 판정 | 기대와 다르면 **오류**다. 「테스트를 약하게 해서 통과」는 금지. 오류는 `fix/<scope>-<what>` 브랜치에 빨간 테스트 + 수정으로 PR(`gh pr create --base dev`), CI 초록 뒤 REST 머지, 보고서에 PR 번호 |
| 모르면 3 | 확인 절차 자체가 불완전하면(Chrome 없음 · pip 없음) 그 항목은 「확인 못 함」으로 적는다 — 통과로 적지 않는다 |
| 보고서 | 항목 ID 마다 통과 / 오류(PR #) / 확인 못 함(이유) · 실측값(바이트 · 시간 · 종료 코드) · 명령 그대로 |

포트 배정: 웹·표본 8794 · DB 안전 8795 · 회계 8796 · 클라 wheel 8797 · 교차 버전 8798(dev 서버)+8799(있으면 옛 서버).

## 1. 영역별 시나리오

### V1 — 웹 렌더 · 호스트 표본 (PR 1 #82 · PR 1b)

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| V1.1 | `data_dir = "~/.local/share/rcm-verify-web"` 로 **`~` 를 써서** 설정 · 8794 | 서버 기동 → `GET /api/status` | `pools[0].hosts[0].disk` 가 `null` 이 아니고 `path` 가 절대경로 · `source == "local"` | 실기 |
| V1.2 | V1.1 + `ok` 잡 둘 완료 | headless Chrome 으로 `/?poll=1&lang=en` 과 `lang=ko` (tests/test_web_browser.py 의 `Chrome` 클래스 재사용) | 호스트 카드에 디스크 막대 + `rcm data …`/`rcm 데이터 …` 줄 · Recent 에 두 잡 · `page_errors() == []` · `undefined`·`NaN` 없음 | 실기 |
| V1.3 | V1.2 + 원격 워커 토큰으로 heartbeat 에 `disk` 실은 표본 | 같은 페이지 | 워커 카드에 디스크 막대만, 회계 줄은 서버 카드에만 | 실기 또는 tests/test_web_browser.py 통과 확인 |
| V1.4 | — | `RCM_CHROME=/nonexistent pytest tests/test_web_browser.py -q -k local_host_card` | **failed**(skip 아님) · 메시지에 `RCM_CHROME` | 자동 |
| V1.5 | — | `.github/workflows/ci.yml` 의 ubuntu 행에 `chrome: google-chrome`, pytest 스텝 `env.RCM_CHROME` · 마지막 dev CI 의 ubuntu 잡 로그에 `skipped` 0 | 둘 다 | CI 로그 |
| V1.6 | 30초 이상 페이지를 열어 둔다 | 헤더 상태 | 「연결이 끊겼습니다」 띠가 뜨지 않는다 · `마지막 갱신` 이 계속 갱신 | 실기 |

### V2 — DB 안전: 오프라인 dry-run · 백업 · v16 · 거절 메시지 (PR 2)

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| V2.1 | 8795 서버가 **돌고 있는** 채(WAL 을 쥔 상태) 잡 몇 개 완료 · `workspace_retention_days = 0` | 다른 셸에서 `rcm gc --dry-run --config <그 설정>` (같은 빌드) | exit 0 · 표에 그 잡들 · stdout 첫 줄 `note: planned on a temporary copy … not changed` · 서버는 계속 정상(`/api/health` 200) · `rcm.sqlite3` 의 mtime 불변 | 실기 |
| V2.2 | v7 픽스처 DB(tests/test_offline_gc.py 의 `downgrade_to_v7` 로 만든 것)를 `data_dir` 에 두고 연결 하나가 WAL 에만 쓴 행을 쥔 상태 | `rcm gc --dry-run --config … --json` | exit 0 · `offline == {schema_version: 7, planned_with_schema: 16, copy: true}` · WAL 에만 있던 잡이 `planned` 에 있다 · 원본 `user_version` 7 · 바이트 동일 · `backup/` 없음 · `$TMPDIR` 에 `rcm-gc-dryrun-*` 없음 | 자동(테스트) + 실기 한 번 |
| V2.3 | V2.2 의 DB | `--timeout 0` | exit 3 · stderr 에 `deadline` · 원본 불변 | 자동 |
| V2.4 | DB 없는 `data_dir` | dry-run | exit 3 · `no database at` · **디렉터리에 아무 파일도 생기지 않는다** | 자동 |
| V2.5 | `user_version = 99` DB | dry-run | exit 3 · `newer than this build` · 메시지에 `backup/rcm.sqlite3.v16.bak` 복원 안내 | 자동 |
| V2.6 | v15 DB 를 그 `data_dir` 에 두고 | 서버 기동 | 기동 전 `backup/rcm.sqlite3.v15.bak` 생성(`user_version` 15 · `integrity_check` ok) · 기동 뒤 `user_version` 16 · 서버 로그에 경고 없음 | 실기 |
| V2.7 | V2.6 뒤 `backup/` 에 `v11~v14.bak` 가짜 파일 넣고 v15 DB 로 다시 | 서버 기동 | 최근 3개(`v13·v14·v15`)만 남는다 · 사람이 만든 `rcm.sqlite3.pre-*.bak` 은 그대로 | 실기 |
| V2.8 | `backup/` 를 읽기 전용 디렉터리로 | v15 DB 로 기동 | **기동 실패** · 메시지 `migration backup failed … not changed` · `user_version` 15 그대로 | 실기 |
| V2.9 | v15 DB 에 옛 빌드 흔적: `failed` 잡 라벨 있음 + `job_failures` 행 없음 · 새 코드 라벨(대장 행 있음) · `cancelled` 라벨 | 기동 → `GET /jobs/<id>` 셋 | 옛 라벨 → `failed_step: null, last_step: <라벨>` · 새 라벨 그대로 · 취소 잡 둘 다 null | 자동 + `rcm top` 육안 |
| V2.10 | — | `python scripts/mutcheck.py --only offline-gc-opens-original` · `--only v16-without-ledger-check` | 둘 다 caught | 자동 |
| V2.11 | — | `docs/operating.md` 「Upgrade」의 순서가 결정 80(검증 → 사본 dry-run → pause → drain → 백업 → 정지 → pull → 기동 → 확인 → resume) · 「Going back to the old build」에 손실 범위 | 문구 있음 | 문서 |

### V3 — 회계: charged/reclaimable · 사후 측정 · 나이 (PR 3)

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| V3.1 | `data_dir` 의 `workspaces/<id>/` 에 **하드링크** 로 같은 파일을 3번 건 워크스페이스(`os.link`) · 종료 잡 | `rcm gc --dry-run` (온라인 · admin) | `charged ≈ 3 × reclaimable` · 「would free」= reclaimable(하한) · 항목 표시는 charged | 실기 |
| V3.2 | `min_free_bytes` 를 현재 여유보다 크게 · 종료 잡 여럿 | sweep 두 번(간격 짧게) | 첫 회차에 바닥 규칙이 시도(`floor_attempted`) · 두 번째 회차 latch(`no_progress: true`) · `rcm check` 가 말한다 · 분모는 **삭제 성공분** | 실기 |
| V3.3 | 기동 직후 | `rcm gc --dry-run` | 「would remain」이 `0 B` 가 아니라 방금 잰 값 | 실기 |
| V3.4 | 실제 `rcm gc` | 응답 | `storage_after.volume_bytes` 가 사후 재측정값 · `freed_bytes` = before − after(측정 차) · 계획 항목 크기 합과 **다를 수 있음**이 문서에 | 실기 + 문서 |
| V3.5 | `_measure_dir` 가 EACCES 인 디렉터리 하나 | status | `job_storage.error_code == "measure_EACCES"` · 서버 로그에 한 줄 | 실기 |
| V3.6 | 24시간 캐시가 섞인 측정 | `rcm check` · 웹 카드 | `measured 57m ago` / `57분 전 측정` 이 **가장 오래된** 측정 시각 기준 | 자동 + 육안 |
| V3.7 | — | `mutcheck --only <S_ISREG 변이 이름>` | caught | 자동 |
| V3.8 | — | `/api/status` 의 `schema_version` 이 그대로(키 추가만) · 옛 키 전부 존재 | 그대로 | 자동 |

### V4 — 클라이언트가 서버 버전을 따라온다 (PR 6 · 7)

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| V4.1 | 8797 서버 | `GET /api/health` | `client_wheel.path == "/client/remote_ci_monitor-<X>-py3-none-any.whl"` · `sha256` · `bytes` · `min_client_version` | 실기 |
| V4.2 | **새 venv**(`python3.11 -m venv`) | `pip install http://127.0.0.1:8797/client/remote_ci_monitor-<X>-py3-none-any.whl` → `<venv>/bin/rcm version` · `rcm --help` · `rcm run ok --server … --token …` | 설치 성공 · 버전 == 서버 · 잡 성공 | 실기 |
| V4.3 | — | 다른 파일명(`…-0.0.1-…whl`) · `If-None-Match: "<sha256>"` · `HEAD` | 404 + `hint` · 304 · HEAD 200 with `Content-Length` | 실기 |
| V4.4 | `read_auth = "basic"` 서버 | 토큰 없이 wheel | 401 · 토큰으로 200 | 실기 |
| V4.5 | 조립 실패 강제(monkeypatch) | health · 엔드포인트 | `client_wheel: null` + `client_wheel_error` · 503 + 이유 · 옛 wheel 을 주지 않는다 | 자동 |
| V4.6 | 옛 클라(0.2.2 또는 0.2.4 릴리스 wheel 을 임시 venv 에) | `rcm check` · `rcm run ok` 를 8797 에 | 둘 다 동작(키 추가만) · 새 클라의 `rcm check` 에 `client` 행(`older — pip install …`) | 실기 |
| V4.7 | — | `docs/operating.md` 의 래퍼 예시를 **그대로** 실행 | health → sha256 확인 → 설치 → `rcm version` 일치 · sha256 을 틀리게 하면 설치 안 함 · 종료 코드 보존 | 자동(테스트가 잠근 예시) + 실기 |
| V4.8 | — | `.github/workflows/tag-release.yml`: `push: branches: [main]` · `contents: write` · 태그 없으면 만들고 `release.yml` 을 `workflow_call` 로 부름 · 같은 버전 재푸시 무동작 · 태그≠`__version__` 거부 | 문자열 잠금 테스트 통과 · 첫 실행은 다음 main 머지(오너가 Actions 에서 확인) | 자동 + 오너 |
| V4.9 | — | 릴리스 자산 URL 패턴 `releases/download/v<X>/remote_ci_monitor-<X>-py3-none-any.whl` 이 문서·워크플로에서 그대로 | 그대로 | 문서 |

### V5 — 가드 (PR 4)

| ID | 준비 | 절차 | 기대 | 방법 |
|---|---|---|---|---|
| V5.1 | `tools/guard_production.py` 의 `decide()` 픽스처 | `rcm token --config <운영 설정 사본> list` · `RCM_SERVER_DATA_DIR=<운영> rcm token add` · `env RCM_SERVER_DATA_DIR=… rcm token add` | 셋 다 deny(유효 `data_dir` 이 운영 · 실행 파일이 운영 venv 밖) | 자동 |
| V5.2 | 같은 픽스처 | `rcm gc --dry-run --config <운영>` | allow(결정 75 · PR 2 뒤 안전) | 자동 |
| V5.3 | 같은 픽스처 | `serve`·`worker` 기존 케이스 | 기존 판정 그대로 | 자동 |
| V5.4 | TOML 이 깨진 설정 | `token` | deny 하지 않는다(CLI 가 먼저 실패) | 자동 |

### V6 — 문서 · 손질 (PR 5)

| ID | 절차 | 기대 | 방법 |
|---|---|---|---|
| V6.1 | `docs/configuration.md` 의 래퍼 예시(I1) 를 그대로 실행 | 마커가 찍히고 종료 코드가 보존 · `fail_patterns` 라는 키가 어디에도 없다 | 자동(잠금 테스트) |
| V6.2 | `rcm run gate --ref <40-hex>` 뒤 `rcm jobs` | sha 가 한 번만(`<ref> @<sha>` 규칙 · 40-hex 완전 일치만) | 실기 |
| V6.3 | `rcm wait 999999` | `log: rcm logs 999999` 를 **찍지 않는다** · 구조화된 사유 · exit 3 | 자동 + 실기 |
| V6.4 | `rcm check` | `local data dir · from <config>` 행(서버 경로가 아님을 말한다) | 실기 |
| V6.5 | `PLAN.md` | 결정 73~83 이 표에 · CLI 예시에 `failed_step_guessed` 없음 | 문서 |

### V7 — 가로지르기 (전 PR)

| ID | 절차 | 기대 | 방법 |
|---|---|---|---|
| V7.1 | `ruff check . && ruff format --check . && pytest` · `node --test tests/web/*.test.js` · `python scripts/mutcheck.py` | 전부 초록 · mutcheck 는 22 + (PR 2 의 2) + (PR 3 의 1) + (PR 6 의 1) = **26** 전부 caught | 자동 |
| V7.2 | `CHANGELOG.md` `[Unreleased]` | PR 1 · 1b · 2 · 3 · 6 의 항목이 각각 PR 링크와 함께 있고, B1·B2·v16 이 기계·잡 번호 없이 일반화돼 있다 | 문서 |
| V7.3 | `scripts/smoke_install.sh dist/*.whl` (새 wheel 빌드 뒤) | README 절차가 새 venv 에서 그대로 된다 | 자동 |
| V7.4 | main 클라이언트(0.2.5 — 옛 빌드 wheel 을 임시 venv 에) ↔ dev 서버 · dev 클라이언트 ↔ 옛 서버(가능하면 8799) | `run`·`wait`·`jobs`·`top`·`check` 동작 · dolomood 의 `scripts/remote_ci.sh` 가 읽는 `url`·`job_id`·`joined`·`state` 키 존재 | 실기 |
| V7.5 | `grep -rn "github" src/remote_ci_monitor/*.py src/remote_ci_monitor/core/*.py` | 런타임 경로에 GitHub 없음(결정 30 · PR 6 뒤에도) | 자동 |

## 2. 오너만 할 수 있는 것 (보고서에 「오너 대기」로)

| ID | 내용 | 근거 |
|---|---|---|
| O1 | v0.2.5 태그(`git tag v0.2.5 <main sha> && git push origin v0.2.5`) — 노트북 래퍼가 오늘 따라오게 | 명세 §3 I8 · 결정 82 |
| O2 | v0.2.6 릴리스(PR 1~3 필수 · 1b·6 권장) — CONTRIBUTING 「Releasing」(PR 7 뒤엔 main 머지가 태그를 만든다) | 명세 §5 |
| O3 | 이 Mac 운영 업그레이드 — 명세 §6 순서 그대로. **v0.2.6 전에는 재시작 금지**(운영 DB 는 스키마 15, 프로세스는 v0.2.5) | 명세 §6 · 결정 80 |
| O4 | 첫 자동 태그·릴리스가 Actions 에서 도는 것 확인(tag-release → release) · 태그 보호 룰셋과의 충돌 여부 | PR 7 |
| O5 | 운영 데이터 **사본**에서 `shared_bytes ≈ 17.5 GB`(하드링크 pack) — 회계 눈금 둘의 실측 | 명세 §5 PR 3 완료 기준 |

## 3. 에이전트 배치 (병렬 · 격리)

| 에이전트 | 영역 | 워크트리 | 포트 |
|---|---|---|---|
| verify-web | V1 · V7.2 | `verify/web-render-smoke` | 8794 |
| verify-db | V2 · V5(PR 4 뒤) | `verify/db-safety-dry-run` | 8795 |
| verify-accounting | V3 | `verify/storage-accounting` | 8796 |
| verify-client | V4 · V7.4 · V7.5 | `verify/client-follows-server` | 8797 · 8798 · 8799 |
| verify-suite | V7.1 · V7.3 · V6(PR 5 뒤) | `verify/full-suite-and-docs` | — |

각 에이전트는 자기 영역의 표를 **위에서 아래로** 전부 돌리고, 오류마다 fix PR 을 낸 뒤 보고서를 쓴다. 보고서 파일은
`docs/acceptance/reports/2026-09-10-m5i-verify-<영역>.md` — 한 PR(`docs/m5i-verification-reports`)로 모아 올린다.
