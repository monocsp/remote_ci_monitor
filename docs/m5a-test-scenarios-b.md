# M5a 테스트 시나리오 — B (서버 · 클라이언트 · 저장소 · janitor · 알림 스레드 · e2e)

> 명세: `docs/m5-workplan.md` M5a-1 ~ M5a-3 (Codex 리뷰 반영본, 2026-09-06 개정 포함) · PLAN.md 「서버 API」「코드 전달」「저장소」「fail-open 금지」.
> 테스트-퍼스트 — **구현 전이라 전부 빨갛다**(112 케이스 / 86 함수). 각 실패 사유가 「인터페이스 없음」(`create_job() … 'priority'` · 새 라우트 404 · `KeyError: 'cache'` · `Store.record_blobs`/`join_or_bump`/`claim_notification` 없음 · `assemble_from_manifest` 없음 · `DB_VERSION` 2≠3 · 설정 `unknown section(s): notify`)인지 확인했다 — 도우미 버그로 빨간 것은 없다.
> A(순수 규칙)·C(CLI·웹·설정) 와 겹치지 않게 I/O 경계만 본다. A 의 `BlobInfo(sha256, size, last_used_at)` · `core.notify.NotifyRule(on/presets 기본 = 전부)` · C 의 `cfg.notify` 모양에 맞췄다.

실행: `ruff check tests/test_store_m5.py tests/test_server_m5.py tests/test_client_m5.py tests/test_notify.py tests/test_janitor_m5.py tests/test_e2e_m5.py` (통과) · `pytest tests/test_store_m5.py tests/test_server_m5.py tests/test_client_m5.py tests/test_notify.py tests/test_janitor_m5.py tests/test_e2e_m5.py`.

## 파일별 시나리오

### `tests/test_store_m5.py` (18) — 스키마 v3 · claim 순서 · `join_or_bump` · `set_priority` · blobs · notifications

- 새 DB 는 `DB_VERSION == 3` · `user_version() == 3` · `jobs.priority` 열 · `blobs(sha256, size, created_at, last_used_at)` · `notifications(job_id, notify_name, claimed_at, delivered_at, failed)`.
- v2 → v3 마이그레이션: 현재 코드로 만든 DB 에서 `priority` 열(관련 인덱스 포함)·두 테이블을 떼고 `user_version=2` 로 되돌린 뒤 다시 열면 3 이 되고 기존 행은 `priority == 0`. 두 번째 열기는 아무것도 안 바꾼다.
- `create_job(..., priority=)` 기본 0 · `list_active()` 도 priority 를 싣는다.
- `claim`: `priority DESC, id` — normal(오래됨)·high·low·normal·high → high, high, normal, normal, low. 그룹 배제는 priority 보다 우선(high 라도 그룹이 바쁘면 건너뛴다). `uploading` 은 priority 가 높아도 claim 대상이 아니다.
- `join_or_bump(join_key, name, label, priority, now)`: 합류 대상 없으면 `None` · 다른 이름은 합류자로 **한 번만**(두 번째 호출은 중복 없음, `joined_at` 유지) · `max(existing, requested)` 로만 올리고 절대 내리지 않는다 · 요청자 이름이면 합류자 없이 상향만 · running 도 대상, 종료 잡은 `None` · 상향된 잡이 claim 에서 먼저.
- `set_priority`: `uploading`·`queued` 만 `True`, running·cancelling·종료·없는 잡은 `False` 이고 값이 안 바뀐다.
- blobs: `record_blobs` → `list_blobs` → `touch_blobs`(`last_used_at` 만, 없는 해시 무시) → `delete_blobs`(없는 것 섞여도 OK). 같은 sha 재기록은 행 하나. 키에 `<token>/<sha>` 접두가 있어도 문자열로 그대로.
- notifications: `claim_notification` 은 (job, rule) 당 한 번만 `True` · `mark_notification(delivered=True/False)` 가 `delivered_at`/`failed` 를 채운다(표시 뒤에도 claim 은 잡혀 있다) · `list_unnotified_finished(since)` 는 `since` 뒤 종료 잡 중 행이 **하나도 없는** 것만, `finished_at` 오름차순, 활성 잡 제외.

### `tests/test_server_m5.py` (33 함수 / 59 케이스) — 우선순위 라우트 · manifest/blob 캐시 · 범위 · 카운터 · 자재화

- 제출: `priority` 이름(`"high"`)·정수(`-1`) 둘 다 · 응답 `"cache": true` · `GET /jobs/{id}` 와 `/api/status` `queue[]` 에 `priority`, 순서 `(-priority, id)` 와 `position` · `reason` 은 그대로 `uploading`. 잘못된 값(`"urgent"`, 2, -2, true, list, dict) → 400 이고 잡을 만들지 않는다.
- 상한: 비-admin 이 프리셋 기본(normal)보다 높이면 403 + `admin` 문구, 낮추는 건 201 · admin 은 high 허용 · 프리셋 `priority = "high"` 면 생략 = high(2026-09-06 개정 명세와 일치), 같은 값 허용, 낮추기 허용 · `presets[].priority` 가 상태 JSON 에 실린다.
- 합류: admin 이 high 로 합류하면 잡이 high 로 올라간다(합류자 한 번) · 비-admin 의 high 는 합류 경로에서도 403(합류자 추가 없음) · 요청자가 low 로 다시 넣어도 안 내려간다.
- `POST /jobs/{id}/priority`: 401 / 비-admin 403(요청자라도) / admin 200 `{job_id, priority}` / 잘못된 값 400 / 없는 잡 404 / GET 405 · 대기 잡(uploading·queued) 은 큐 순서·position 이 바뀐다 · running·cancelled 는 409 + `state`.
- manifest: `missing` 은 해시 중복 제거·`missing_bytes` 는 해시별 한 번·`state: "uploading"` · `jobs/<id>/manifest.json` 저장(본문 그대로) · `last_received_at` 갱신 · 401/403(남의 토큰)/404(없는 잡)/405(GET) · admin 은 된다 · `snapshot_cache=false` 서버는 404(구버전처럼) 이고 전체 tar 는 그대로 된다.
- manifest 검증 400 (27 케이스): 절대 경로 · `..` · 빈 조각 · `.` · `.git/` · 백슬래시 · NUL · 해시 형식(길이·hex·대문자) · size 음수/문자열 · mode 문자열 · 키 누락 · files/links 가 리스트 아님 · 링크 target 절대/탈출 · 링크 path 탈출 · 중복 경로 · 파일이면서 디렉터리 · 파일과 링크 같은 경로 · 본문이 객체 아님. **400 은 잡을 죽이지 않는다**(uploading 유지, manifest.json·blob 없음).
- 413: 합계 `size` > `max_snapshot_bytes` → `.rcmignore` 안내 + cancelled, summary `snapshot 20 KB exceeds 10 KB`(기존 문구) · cancelled·queued 잡의 manifest 는 409 + `state`.
- missing 이 비면 `{"missing": [], "missing_bytes": 0, "state": "queued"}` 로 바로 queued(PUT 없이) · `uploaded_bytes == 0`, `cached_bytes == 합계` · 그 뒤 PUT 은 409.
- manifest 가 참조한 blob 의 `last_used_at` 이 올라간다(size 불변).
- 800 파일 manifest(≈100 KB > 일반 JSON 64 KB 상한) 도 200.
- blob PUT(`X-RCM-Tree: blobs`): 검증된 blob 이 `<data_dir>/blobs/<aa>/<sha>` 에 하나씩, `.part` 잔재 없음, `list_blobs` 2 행, `source.uploaded_bytes == PUT 본문 길이`, `cached_bytes == 0`, `received_bytes` 유지, `server.snapshot_cache == {blobs, bytes}`, `tree.tar.gz` 없음.
- 거부: 해시 불일치 → 400 + cancelled + summary `snapshot rejected: … blob hash mismatch`, 틀린 내용 저장 안 함, 카운터 0 · missing 에 없는 멤버 → 400 + cancelled, 그 blob 저장 안 함 · manifest 가 선언한 크기보다 큰 멤버(gzip 폭탄 형태, Content-Length 는 상한 이하) → 400/413 + cancelled, 파일·`.part` 없음 · 멤버 이름에 경로(`../<sha>`) → 400 · manifest 전 PUT → 400/409(전체 tar 경로는 살아 있다) · 401/403/411 · Content-Length 초과 413 + cancelled(기존 문구).
- 동시성: 두 잡이 같은 blob 을 동시에 PUT → 둘 다 200, 파일 하나, 행 하나, 둘 다 queued, `last_error` 없음.
- 전체 tar 경로는 캐시가 켜져 있어도 그대로(`tree.tar.gz`, blob 없음, `uploaded_bytes`/`cached_bytes` 키 존재).
- 범위: `snapshot_cache_scope="token"` 이면 남의 blob 은 `missing` 에 나오고 같은 토큰은 안 나온다(토큰별 사본 2 개) · `global` 은 공유.
- 상태 JSON: 처음 `{"blobs": 0, "bytes": 0}` · `notify_failures == 0` · `schema_version` 1 유지 · 캐시 꺼진 서버는 `snapshot_cache: null`.
- 자재화: `materialize.assemble_from_manifest(manifest_path, blobs_dir, workspace)` 가 복사(하드링크 아님 — 워크스페이스를 고쳐도 blob 그대로, `st_nlink == 1`)·실행 비트·링크를 만든다 · blob 이 없으면 `MaterializeError` 에 `blob missing` + sha7, 경로 없음, 반쯤 만든 워크스페이스 없음 · 워커가 blob 잡을 돌려 succeeded(로그에 파일 내용·링크·실행) · 큐잉 뒤 blob 파일을 지우면(정지→삭제→재개) `failed` + `exit_code null` + summary 에 `blob missing`, `last_error` 는 없음.

### `tests/test_client_m5.py` (9) — `Snapshot.entries` · `upload_cached` · 전송 바이트 계측 · 폴백 규칙

- `Snapshot.entries`: 파일/실행 파일/링크가 `path·mode·size·sha256·kind` 로(경로순, `files` 와 같은 목록), mode 는 git 식 `0o100644/0o100755/0o120000`, kind `file`/`link`, 링크는 `target`. 내용이 바뀌면 sha·`tree_hash` 가 바뀐다.
- `Client.manifest` → `{missing(중복 제거), missing_bytes, state}` · `Client.upload_blobs` → queued.
- `upload_cached`: 첫 업로드는 다 보낸다(`uploaded_bytes ≥ 95%`, PUT 1회, progress 단조·마지막 `sent == total`) · 같은 트리 두 번째(`join=False`)는 **PUT 자체가 없다**, `uploaded_bytes == 0`, `cached_bytes == 합계`, `tree.tar.gz` 없음 · 1 MB 하나만 바꾸면 `uploaded_bytes ≈ 1 MB`(0.98~1.05), `cached_bytes == 2 MB`.
- 완료 기준 ②: 난수 50 MB(2.5 MB × 20) 를 두 번 올려 `uploaded_bytes + manifest 길이 < 10%`, 클라이언트가 `_request` 를 감싸 센 본문 합계도 < 10%.
- 폴백: manifest **404 만**(`snapshot_cache=false` 서버) 전체 tar — 호출 순서 `POST, PUT`, `tree.tar.gz` 있음 · 413 → `ClientError(413)`, PUT 없음, 잡 cancelled · 403(남의 잡) → `ClientError(403)`, 잡은 uploading 그대로(주인이 이어서 올린다) · 400(manifest 뒤 파일이 바뀌어 해시 불일치) → `ClientError(400)`, blob PUT 한 번뿐, 전체 tar 재시도 없음.

### `tests/test_notify.py` (14) — `notify.Notifier` 스레드 · 서버 wiring

- argv 규칙: `run(argv=list(rule.argv), env=…, timeout=rule.timeout_seconds)` 로 한 번, 셸 없음 · env 11 키 전부 문자열(`RCM_URL = <base>/#/jobs/<id>`, `RCM_JOB_SECONDS` ≈ started→finished) · None 은 `""`(「None」 아님) · 행이 남아 `claim_notification` 이 `False`.
- 필터: `on`(failed/timed_out/lost) · `presets`(gate) · 전부 — 3 잡 × 규칙 = 6 회, 안 맞는 규칙은 행도 없다.
- 정화: NUL·ESC·BEL 제거, 4096 바이트 상한(`RCM_SUMMARY`·`RCM_FAILED_STEP`·`RCM_REQUESTER`).
- 정확히 한 번: 같은 `job_finished` 3 번 → 1 회 · 스레드를 다시 띄워도(재시작) 0 회 · 시작 스캔: 행 없는 종료 잡만(이미 claim 된 것·`metadata_retention_days` 밖은 제외), 스캔과 recover 이벤트가 겹쳐도 1 회.
- 실패: 종료 ≠ 0 · HTTP 500 · 3xx(리다이렉트 없는 opener 는 `HTTPError`) · 타임아웃(`TimeoutExpired`, `timeout` 인자 전달) → 카운터 +1, 재시도 없음, 로그 한 줄에 규칙 이름(+잡 id).
- url: `POST`, `Content-Type: application/json`, 본문에 `notify` 이름 + 최근 완료 행(평평하든 `job` 아래든), `timeout` 전달, 2xx(204) 는 성공.
- `opener=None` 이면 진짜 `http.server` 로: `/redirect`(302) 는 `/followed` 로 가지 않고 실패 1, `/ok` 는 전달.
- 서버 안: `cfg.notify` 를 두고 `app.start()` → `bad` 잡이 failed 되면 파일 훅은 정확히 한 줄, `exit 3` 규칙은 `server.notify_failures == 1`, `last_error` 는 null, `/api/health` 200 · 성공 잡은 `on=["failed"]` 규칙을 안 건드린다 · shutdown 이 `notify` 스레드를 멈춘다 · 규칙이 없으면 `notify_failures == 0`.

### `tests/test_janitor_m5.py` (10) — blob GC

- 30일(경계 `>=`) 안 쓰인 blob 은 파일 → 행 삭제, 어린 것은 유지, 로그에 `blob`, 두 번째 sweep 은 0.
- 활성 잡(uploading·queued·running·cancelling) 의 `manifest.json` 참조는 아무리 오래돼도 유지; 그 잡이 끝나면 다음 sweep 에 대상.
- 종료 잡만 참조하는 blob 은 대상(단, 30일 안 쓰인 것만).
- 나이는 `created_at` 이 아니라 `last_used_at`(touch 하면 산다).
- `snapshot_cache_max_bytes` 초과: `last_used_at` 오래된 순으로, 활성 참조는 건너뛰며, 상한 이하가 될 때까지 · 상한 이하면 아무것도 안 지운다.
- 파일이 이미 없어도 행은 지우고 오류 없음 · `jobs/<id>/manifest.json`·DB·`blobs/README` 같은 다른 파일은 안 건드린다 · 깨진 manifest 는 `on_error`(경로 없음) 로 표면화하되 sweep 은 죽지 않는다 · `snapshot_cache=false` 면 GC 안 함.

### `tests/test_e2e_m5.py` (2) — 진짜 `rcm serve` + `rcm run`

- ① 레인 1 에서 4초 잡이 도는 동안 normal → (비-admin `--priority high` 는 exit 2 + `admin`) → admin `--priority high`: `rcm top --json` 에 `priority` 1/0(둘 다 queued 면 position 1/2), `started_at` 순서 slow → high → normal, high 는 slow 가 끝난 뒤 시작. ③ `[[notify]]` argv 규칙이 잡마다 정확히 한 줄(3 줄, 1초 더 기다려도 그대로), `notify_failures == 0`.
- ② 1 MB 난수 트리: 첫 `rcm run` 은 `uploaded_bytes ≥ 1 MB`, 로그에 파일 크기(자재화 확인) · 두 번째(`--no-join`) 는 stderr 에 `cache`, `uploaded_bytes ≤ 4096`, `cached_bytes ≥ 1 MB`, `tree.tar.gz` 없음 · `--no-cache --no-join` 은 옛 경로(`tree.tar.gz` 있음, stderr 에 `cache` 없음) · `server.snapshot_cache.bytes ≥ 1 MB`.

## 가정 (명세가 정하지 않아 테스트가 정한 것)

1. `POST /jobs` 의 `priority` 생략 = **프리셋 기본**(개정 명세 M5a-1 과 같다). 응답 `cache` 는 켜져 있을 때만 `true`(꺼지면 없거나 `false`).
2. `POST /jobs/{id}/priority` 응답의 `priority` 는 **정수**(`queue[].priority` 와 같은 단위). 409 본문에 `state`. GET 은 405.
3. manifest **검증 실패(400)는 잡을 취소하지 않는다** — 클라이언트 버그라 다시 보낼 수 있어야 한다. 취소는 413(크기)과 blob 거부(PUT 400)뿐(명세 그대로).
4. `missing_bytes` 는 **해시별 한 번**(중복 제거된 blob 바이트). `uploaded_bytes` 는 **PUT 의 HTTP 본문 길이**(PUT 이 없으면 0 — 명세의 「서버가 센 값」 측정과 맞춘다). `cached_bytes` 는 이미 있는 해시를 가진 **파일들의 size 합**(진행 표시 `3.1 / 48.2 MB (cache 94%)` 의 분모와 같은 단위).
5. blob 파일 배치 `<data_dir>/blobs/<aa>/<sha256>`(명세의 `.part` 경로에서 유도). `token` 범위는 토큰별 사본(같은 내용이 2 파일 · 2 행). 실패한 PUT 뒤 `.part` 는 남지 않는다.
6. manifest 없이 온 blob PUT 은 400 또는 409; 캐시 꺼진 서버의 blob PUT 은 400/404/409 중 하나.
7. `server.snapshot_cache` 는 캐시가 꺼져 있으면 **`null`**(「0 개」가 아니라 「꺼짐」 — fail-open 금지의 결).
8. 자재화 함수 이름은 과제가 준 **`assemble_from_manifest(manifest_path, blobs_dir, workspace)`**(명세 본문은 `assemble_manifest`) · `blobs_dir` 아래 배치도 `<aa>/<sha>` · 실패 문구 `… blob missing <sha7>` · 워크스페이스 반쯤 남기지 않음.
9. `Store.list_blobs()` 행은 속성(`sha256`·`size`·`last_used_at`) 또는 매핑 어느 쪽이든 읽는다(A 의 `BlobInfo` 와 호환). `created_at` 은 테이블에서 SQL 로 읽는다.
10. `notifications` 열 이름은 명세 그대로(`job_id, notify_name, claimed_at, delivered_at, failed`). `mark_notification(delivered=False)` 는 `failed` 를 참으로, `delivered_at` 은 NULL.
11. `Notifier` 카운터 이름은 `failures` 또는 `notify_failures` 둘 다 허용(도우미). URL 본문은 평평하든 `job` 아래 중첩이든 내용만 본다. 실패 로그 한 줄에는 규칙 이름과 **잡 id** 가 있어야 한다(없으면 행동할 수 없는 로그). `RCM_URL` 은 `base_url` 인자가 없어도 설정(`public_url` 또는 bind:port)에서 만들어 비지 않는다.
12. `Snapshot.entries` 의 `kind` 는 `file`/`link`, mode 는 `core.snapshot.normalize_mode` 의 git 식 값. `upload_cached` 반환 dict 에 `job_id`·`state`.
13. janitor blob GC 는 `sweep_once` 안에서 돈다(별 스레드 없음) · 로그 한 줄에 `blob` · 깨진 manifest 는 `on_error`.
14. 큰 manifest(800 파일, ≈100 KB) 는 받아야 한다 — 일반 JSON 상한 64 KB 를 manifest 라우트에 그대로 쓰면 팀 트리(수백 파일)가 막힌다.
15. e2e: `rcm run` 의 캐시 경로 stderr 에 `cache` 문자열, `--no-cache` 경로엔 없음. 큐 스냅샷(`rcm top --json`) 은 타이밍상 둘 다 queued 일 때만 position 을 본다.

## 명세 의문 (오너/구현자 확인)

- **함수 이름**: 명세 M5a-2 ④ `materialize.assemble_manifest` vs 과제 인터페이스 `assemble_from_manifest`. 테스트는 후자.
- **manifest 본문 상한**: 서버의 JSON 본문 상한(64 KB)이 manifest 에도 적용되면 수백 파일 트리가 413 이 된다. 별도 상한(예: `max_snapshot_bytes` 비례 또는 수 MB)이 필요하다 — 테스트 14 번 가정.
- **`missing_bytes` 의 중복**: 「중복 제거」가 해시 목록만인지 바이트도인지 명시가 없다. 테스트는 바이트도 해시별 한 번.
- **`uploaded_bytes` 의 단위**: 「이번에 실제로 받은 바이트」를 HTTP 본문 길이로 읽었다(측정 규칙과 일치). 옛 전체 tar 경로에서는 키 존재만 본다.
- **400 manifest 와 잡 상태**: 명세는 413·blob 거부만 cancelled 라고 한다. 검증 400 도 cancelled 로 하고 싶다면 테스트 3 번 가정을 뒤집어야 한다.
- **`server.snapshot_cache` 꺼짐 표현**: null 로 잡았다. 「0 개」로 그리면 꺼진 서버와 빈 캐시를 화면이 구분할 수 없다.
- **알림 URL 본문 모양**: 「최근 완료 행 + notify 이름」 — 평평(`{...row, "notify"}`)인지 중첩(`{"notify", "job": row}`)인지. 둘 다 받게 했지만 README/훅 작성자에게는 하나로 고정해 알려야 한다.
- **알림 실패 로그**: 잡 id 를 포함해야 하는지 명세가 말하지 않는다 — 테스트는 요구한다.
- **`snapshot_cache_scope = "token"` 의 저장 비용**: 같은 내용이 토큰마다 저장되어 `server.snapshot_cache.blobs` 가 토큰 수만큼 는다. 화면·README 에 밝힐지.
- **완료 기준 ② 소요**: 50 MB 난수 × 2 스냅샷(gzip) + 서버 해시로 이 Mac 에서 ~6–10초로 예상. CI 가 느리면 `each=1_000_000`(20 MB) 로 낮춘다(과제가 허용).
- **알림 테스트의 현재 소요**: `Store.claim_notification` 이 없는 동안 알림 스레드가 예외를 삼켜 마감(3초)까지 기다린다 — 저장소가 들어오면 즉시 빨라진다. `notify.py`/`core/notify.py`/`core/manifest.py` 는 이미 트리에 있다(다른 역할이 넣음) — 서버·저장소·클라이언트·janitor·App wiring 이 남았다.
