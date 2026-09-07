# Codex 크로스리뷰 — M5 확장 명세 (2026-09-06)

- 대상: `docs/m5-workplan.md`(우선순위 · 내용 주소 스냅샷 캐시 · 알림 · 원격 워커 · 범위 결정)
- 실행: `codex exec --sandbox read-only`(gpt-5.5). 질문 A~E.
- 반영: 「반드시 고칠 것」 12건 — ① claim `ORDER BY priority DESC, id` + 순수 정렬 일치 ② 합류 시 priority 상향을 한 트랜잭션으로(`join_or_bump`) ③ `Preset.priority` + 비-admin 은 프리셋 기본 이하 · `rcm bump` 는 admin+대기 잡 ④ `Snapshot.entries` 단일 출처(manifest 와 tar 가 같은 입력) ⑤ blob 임시 파일 이름에 잡·스레드 + `os.replace` ⑥ GC 는 활성 잡 manifest 참조 제외 · manifest 저장 시 `last_received_at` 갱신(abandon 경로가 덮음) ⑦ `received_bytes` 유지, `uploaded_bytes`·`cached_bytes` 추가 키 ⑧ missing 목록은 존재 오라클 — 문구 수정 + `snapshot_cache_scope = "global"|"token"` ⑨ `notifications` 테이블(unique) + 시작 시 미전송 스캔 ⑩ 알림 실행 전 DB unique insert 로 claim(이벤트 중복은 정상 입력) ⑪ `pools[0]` 가정 전부 순회로 ⑫ M5b 를 4 PR 로. 「고치면 좋은 것」 8건도 반영(전송 바이트 계측 fixture · 404 만 폴백 · env 문자열 정화 · 리다이렉트 금지 opener · `last_seen_at` 만 · 마커는 서버 파싱 · `lane` int 유지 + `worker` 키 · 워커용 tar 조립).
- 오너에게: 캐시 blob 공유 범위(결정 32) · GitHub 백엔드를 M6 에서 원하는지(결정 30).

---

결론: M5 방향은 맞지만, 지금 명세 그대로 구현하면 **캐시·알림·원격 워커에서 경합/누락/스키마 호환 문제가 난다**. M5a는 한 PR로 가능하되 idempotency와 측정 기준을 먼저 박아야 하고, M5b는 한 PR로 감당하지 말고 `pools/schema` → `/worker/*` → 원격 실행 → UI/CLI 순서로 쪼개야 한다.

**반드시 고칠 것**

1. `M5a-1 / store.claim` · FIFO 잔재 제거 · `jobs.priority INTEGER NOT NULL DEFAULT 0` 추가 후 `claim` SQL을 `ORDER BY priority DESC, id`로 바꾸고, `core/queue.compute_queue`의 waiting 정렬도 `(-priority, id)`로 맞춰라.

2. `M5a-1 / 합류` · high 합류가 기존 잡을 올리는 원자 연산 없음 · `find_joinable + add_joiner + priority bump`를 한 트랜잭션 함수로 묶고, `max(existing.priority, requested.priority)`만 허용하라.

3. `M5a-1 / 프리셋 기본 제한` · 클라이언트가 기본보다 올리는 규칙이 빠짐 · `Preset.priority`를 설정에 추가하고 서버 `submit`에서 비-admin이면 `requested_priority <= preset.priority` 검증, `rcm bump`는 admin+waiting만 허용하라.

4. `M5a-2 / manifest` · 현재 `Snapshot`은 `files`만 있고 manifest entries가 없다 · `Snapshot.entries = [{path, mode, size, sha256, kind}]`로 바꾸고 tar 생성도 이 entries에서만 하게 만들어 manifest와 tar의 입력을 단일화하라.

5. `M5a-2 / 업로드 경합` · 같은 blob 동시 업로드 시 temp 파일 충돌 가능 · blob 저장은 `blobs/aa/.<sha>.<job>.<thread>.part`에 쓰고 sha/size 재검증 뒤 `os.replace`; 이미 blob이 있으면 검증 후 replace 생략해도 된다.

6. `M5a-2 / GC 경합` · 업로드 중 GC가 blob을 지울 수 있음 · janitor는 `uploading|queued|running|cancelling` 잡의 `manifest.json` 참조를 먼저 모아 제외하고, manifest 저장 직후 `last_received_at`을 갱신해 기존 `upload_abandon_seconds` 경로가 PUT 미도착을 덮게 하라.

7. `M5a-2 / 스키마 v1` · `received_bytes`를 바꾸면 호환 위반 · 기존 `source.received_bytes`는 유지하고 `source.uploaded_bytes`, `source.cached_bytes`를 추가 키로만 넣어라.

8. `M5a-2 / 프라이버시` · missing 목록은 blob 존재 오라클이다 · “해시만으로 못 읽는다”는 문구를 “존재 여부는 알 수 있다”로 고치고, `snapshot_cache = false`와 가능하면 `snapshot_cache_scope = "global"|"token"` 중 하나를 설정으로 둬라.

9. `M5a-3 / 알림 누락` · 이벤트 버스만 보면 재시작 직후 recover 이벤트를 놓친다 · `notifications(job_id, notify_name, delivered_at, failed_count)` 테이블에 unique key를 두고, notifier 시작 시 미전송 terminal job을 스캔한 뒤 이벤트를 소비하라.

10. `M5a-3 / 중복 호출` · `_publish_job`은 terminal이면 언제든 `job_finished`를 낸다 · 알림 실행 전 DB unique insert로 claim하고 성공/실패를 기록하라. 이벤트 중복은 정상 입력으로 취급해야 한다.

11. `M5b / pools 다중화` · 현재 서버·CLI·웹이 첫 풀만 본다 · `status()`에서 풀별 `Pool`을 만들고, `render_text`, `cmd_eta`, `cmd_jobs`, `web/app.js`의 `pools[0]`/`pool0()` 사용을 전부 순회 구조로 바꿔라.

12. `M5b / PR 범위` · 원격 워커 전체는 한 PR로 너무 큼 · PR을 `DB v3+pool/priority 기반`, `worker token+register/claim/heartbeat/lost`, `runner.py+remote execution/log/tree`, `UI/CLI multi-pool`로 나눠라.

**고치면 좋은 것**

1. `M5a-2 / 완료 기준 ②` · gzip 때문에 10% 기준이 허위 통과 가능 · 난수성 큰 50MB fixture에서 HTTP request body 바이트를 계측해 manifest+blob PUT 합계가 원본 파일 합계의 10% 미만인지 테스트하라.

2. `M5a-2 / 구버전 호환` · 404 fallback 범위 명확화 · `POST manifest`가 404일 때만 전체 tar로 가고, 400/401/403/413/5xx는 절대 fallback하지 말라.

3. `M5a-3 / env 문자열` · summary/env가 너무 크거나 제어문자를 가질 수 있음 · `RCM_SUMMARY`, `RCM_FAILED_STEP` 등은 NUL 제거, 4KB truncate, newline 보존 여부 테스트를 넣어라.

4. `M5a-3 / URL hook` · redirect 금지는 구현 실수 나기 쉬움 · `HTTPRedirectHandler.redirect_request = None`인 opener를 쓰고 3xx는 실패로 세라.

5. `M5b / heartbeat` · 워커 시계 믿지 말 것 · lost 판정은 서버가 받은 시각 `last_seen_at`만 쓰고, 워커 payload의 시각은 표시용으로도 쓰지 말라.

6. `M5b / 로그 마커` · 워커에서 파싱하면 신뢰 경계가 흐려짐 · 원격 워커는 raw log bytes만 보내고 서버가 append하면서 `parse_marker`를 호출하게 하라.

7. `M5b / worker lane 스키마` · `lane` 타입 변경 금지 · `server.workers[].lane`은 int 유지, `worker`와 `display_name`만 추가하라.

8. `M5b / 캐시 잡 tree 전달` · worker GET 응답 크기 계산 필요 · 서버가 manifest+blob으로 임시 tar.gz를 조립해 `Content-Length`를 붙이고 내려주게 하라.

**그대로 둘 것**

1. GitHub 백엔드를 M6으로 미루는 결정은 맞다. M5 완료 기준과 충돌하지 않는다.

2. 우선순위를 `low/normal/high` 3단계로 제한하는 결정은 맞다.

3. 우선순위 기아 보정을 넣지 않는 결정은 유지해도 된다. 대신 README에 “high가 계속 오면 normal은 기다린다”를 명시하라.

4. blob 자재화에서 하드링크 금지는 맞다. 기본은 `shutil.copyfile`, reflink는 성공 시에만 최적화하라.

5. 워커 → 서버 단방향 모델은 맞다. NAT 뒤 빌드 머신 요구와 잘 맞는다.

**오너에게 물어야 할 것**

1. 캐시 blob 공유 범위: 전 토큰 global 공유를 받을지, 토큰별 namespace가 필요한지.

2. GitHub 백엔드를 M6에서도 실제로 원하는지. 현재 제품 방향은 “GitHub 비의존”이다.