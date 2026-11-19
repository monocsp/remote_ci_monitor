# M5p 테스트 시나리오 A (PR P1 · 보관함의 규칙 · 저장 · 청소, 2026-11-19)

`docs/m5p-workplan.md` 의 §3(설정) · §4.1~§4.5(keep · 보관 요청 · 시계 · 펼치기 후보 · 정리) · §5(DB v24 · 폴더 ·
기동 화해) · §6 의 P1 인터페이스를 시험으로 옮긴다. §7 의 P1 항목은 A1~A87 에 모두 들어 있고, 코드를 읽으며
찾은 경우는 A88 부터 이어 붙였다. 결정 번호는 125~148, 설계 검토 번호는 §14 의 것이다. 시험을 먼저 쓰므로
구현 전에는 빨간 것이 정상이다.

v24 를 쓰기 전에 origin/dev 의 `DB_VERSION` 을 다시 확인했다(2026-11-19, `store.py:79` 가 23). 24 는 비어 있다.

| 파일 | 무엇을 잠그나 |
|---|---|
| `tests/test_shelf_rules.py` | `core/shelf.py` 의 순수 함수 전부 · `SHELF_STATES` · `PUBLIC_SHELF_STATES` · `ShelfItem` 필드 · `Job.keep` 기본값 |
| `tests/test_config_m5p.py` | 서버 키 넷 · 파생 `shelf_enabled` · `shelf_dir` 금지 위치 · 프리셋 `keep` 검증 |
| `tests/test_store_m5p.py` | DB v24 · keep 얼리기 · 합류 · supersede · `_upsert_bundle` 의 보관 행 · `set_job_keep` 여섯 갈래와 경쟁 · `ack_bundle` 미루기 · `bundles_due` · 보관 행 함수(claim · finish · touch · latest · totals · mark · forget) · 잡 행보다 오래 사는 항목 |
| `tests/test_janitor_m5p.py` | `Janitor._sweep_shelf` · 기준 루트를 받는 삭제 · 묘비와 failed 정리 · hold · 안전판 회차 · `Janitor.gc` 가 보관함을 안 건드림 |
| `tests/test_shelf_startup.py` | `reconcile_shelf_on_start` · `_offline_gc` 와 생성자들이 화해를 부르지 않음 |
| 기존 잠금(고친다) | 아래 「갱신할 기존 잠금」 표 |
| 기존 시험(고치지 않는다) | 옛 버전으로 깎는 도우미들(`test_store.py` v1 · `test_store_m5.py` v2 · `test_offline_gc.downgrade_to_v7` · `test_store_m5n.downgrade_to_v22` · `test_job_link` v21 · `test_store_versions` v20). 새 표와 `keep` 열을 남긴 채 버전만 내리므로 v24 DDL 이 멱등이어야 통과한다(A130) |
| `scripts/mutcheck.py` | P1 변이 셋. 「삭제 뒤 표시 순서 뒤집기」는 A38 · A63 이, 「1시간 규칙 제거」는 A71 이, 「ack 미루기 제거」는 A29 · A81 이 잡는다 |

### 갱신할 기존 잠금

명세 §5.1 이 적은 `DB_VERSION == 23` 네 곳 말고도, 열 차례와 `user_version` 을 잠근 줄이 v24 에서 같이 깨진다.
`keep` 은 마이그레이션이 `jobs` 맨 뒤에 붙이므로 새 DB 에서도 마지막 열이어야 한다.

| 파일:줄 | 지금 | 바꿀 것 |
|---|---|---|
| `tests/test_store_m5n.py:197-198` | `test_this_build_is_database_version_23` · `DB_VERSION == 23` | 24 로. 이름도 바꾼다 |
| `tests/test_store_m5n.py:206` · `:244` | `columns(path, "jobs")[-7:] == NEW_JOB_COLUMNS` | `[-8:-1]`. 끝의 `keep` 은 A18 · A19 가 잠근다 |
| `tests/test_store_m5n.py:243` · `:289` | `s.user_version() == 23` | `== DB_VERSION` |
| `tests/test_store_versions.py:86` | `DB_VERSION == 23` | 24 |
| `tests/test_store_migration_releases.py:76` | `DB_VERSION == 23` | 24, 주석에 v24(M5p) 한 줄 |
| `tests/test_job_link.py:237` · `:240` | `DB_VERSION == 23` · `jobs_columns(path)[-9:]` | 24 · `[-10:-1]` |

## 공통 픽스처

- 시각은 고정 `NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)` 이고 순수 함수에는 `T = NOW.timestamp()`, `D = 86400`,
  `H = 3600`, `M = 60` 을 쓴다. sleep 은 쓰지 않는다. 청소를 여러 회차 돌리는 시험은 `sweep_once(now)` 에 시각을
  넘기거나 `test_worker_api.Clock` 을 `Janitor(now_fn=clock)` 에 끼우고 `clock.advance(...)` 로 민다.
- 저장소 시험은 `tests/test_store_m5e.py` 의 도우미를 import 한다: `enqueue`(tree 를 바꿔 합류 키를 가른다) ·
  `finished_job` · `ready_bundle`(진짜 순서: `start_collect` → 파일 설치 → `publish_bundle`) · `install_files` ·
  `collect_result` · `policy` · `bundle_file` · `raw` · `join_count` · `at`. keep 잡은 새 도우미 `enqueue_keep` 이
  `store.create_job(..., keep=True)` 로 만든다. 키워드 자체는 A20 하나가 잠근다.
- 원격 잡은 `store.claim(1, now, worker_name="mac2")` 로 집는다. 원격의 순서는 `remote_workers.worker_finish` 그대로
  재현한다: `publish_bundle(READY)`(잡은 아직 running) 다음에 `finish(...)`(bundle 인자 없음). 업로드 영수증은
  `publish_bundle(UPLOADING)` 이다(`remote_workers.py:732`).
- 보관 행을 copier 없이 세우는 도우미 `shelf_row(store, job_id, state, **cols)` 는 `shelf_items` 에 SQL 로 넣는다
  (`test_store_m5n` 의 `set_cols` 방식). 폴더는 `shelf_folder(cfg, job_id, files={...})` 가
  `<shelf_dir>/<N>/files/...` 와 `manifest.json`(`{"job": N, "files": [...], "expand_skipped": []}`)을 쓴다.
- v23 모양은 새 도우미 `downgrade_to_v23(path)` 가 만든다. `test_store_m5n.downgrade_to_v22` 를 본떠 **있는 것만**
  지운다: 인덱스 `shelf_items_state` · `shelf_items_latest`, 표 `shelf_items`, 열 `jobs.keep`, 그리고
  `PRAGMA user_version=23`. 열 비교는 `test_store_m5n` 의 `columns` · `index_columns` · `file_version` 을 쓴다.
- 청소기는 `tests/test_janitor.py` 의 `make_janitor(store, cfg, now=...)` 와 `Recorder`, 설정은 `test_worker.make_config`
  에 `tests/test_janitor_m5e.ARTIFACT_CONFIG` 를 얹고 `shelf_dir = str(tmp_path / "shelf")` 를 준다. 묶음은
  `test_janitor_m5e.publish` · `bundle_dir`, 끝난 잡은 `test_janitor.finished` 로 만든다.
- 같은 트랜잭션인지 보는 방법은 둘이다.
  - 실패 주입: `test_store_m5h.test_a_failing_ledger_write_rolls_the_finish_back` 처럼 `raw` 연결로
    `CREATE TRIGGER ... BEFORE INSERT ON shelf_items BEGIN SELECT RAISE(ABORT, 'blocked'); END` 를 걸고, 호출이
    예외로 끝난 뒤 **다른 쓰기도 남지 않았는지** 본다(도우미 `block(store, "INSERT", "shelf_items")`).
  - 끼어들기: 도우미 `interleave(store, after_sql, other)` 가 그 스레드의 `store._conn` 을 프록시로 감싼다.
    `BEGIN IMMEDIATE` 다음 `after_sql` 로 시작하는 첫 문장이 끝난 직후, **같은 파일을 연 다른 `Store` 객체**로
    `other()` 를 새 스레드에서 시작하고 0.3초 `join` 한다. 판정이 쓰기 잠금 안에 있으면 그 스레드는 아직
    살아 있다(busy_timeout 5초에 막힘). 판정을 트랜잭션 밖에서 읽는 구현이면 0.3초 안에 끝나서 시험이 빨개진다.
- 실패 주입은 이렇게 한다. rmtree 실패는 `monkeypatch.setattr(remote_ci_monitor.janitor.shutil, "rmtree", boom)`
  (`boom` 이 `PermissionError(errno.EACCES, ...)` 를 낸다). 디스크 없음은 `shelf_dir` 을 만들지 않은 경로로 둔다.
  표식 없음은 `.rcm-shelf` 를 지운다.

## 베스트

### 명세에서

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A1 | 상태 이름 (§6 · §4.8) | | `SHELF_STATES == ("pending","copying","active","stale","failed","deleted")`, `PUBLIC_SHELF_STATES == SHELF_STATES + ("none",)` |
| A2 | 시계의 세 구간 (§4.3 · 결정 131) | `stage(T, now, 182, 365)`, now 는 `T` · `T+100D` · `T+200D` · `T+400D` | `active` · `active` · `stale` · `delete` |
| A3 | 정리 예정일 (§4.3) | `due_dates(T, 182, 365)` | `(T+182D, T+365D)`. 그 두 시각에서 `stage` 는 각각 `stale` · `delete` 다 |
| A4 | 쓰기 줄이기의 쓰는 쪽 (§4.3 · 결정 132) | `should_touch(None, "active", T)`, `should_touch(T, "active", T+61M)` | 둘 다 True |
| A5 | `touches` 표의 「쓴다」 줄 (§4.3 · 결정 132) | `(GET,item,active)` · `(GET,file,active)` · `(GET,file,stale)` · `(GET,zip,active)` · `(GET,zip,stale)` · `(POST,restore,stale)` · `(POST,keep,active)` · `(POST,keep,stale)` | 모두 True. 304 는 라우터가 GET 으로 넘기므로 `(GET,file,…)` 줄이 덮는다(304 자체는 P2) |
| A6 | 정리 계획 (§4.5 · 결정 134 · 135) | `plan_sweep` 에 a=active·`T-200D`, b=stale·`T-400D`, c=deleted·`deleted_at=T-31D`, d=active·`T-10D` | `to_stale == [a]`, `to_delete == [b]`, `to_forget == [c]`, d 는 어디에도 없다 |
| A7 | 인라인 허용 목록 (§4.6 · 결정 139) | `home.png` · `a.jpg` · `a.jpeg` · `a.gif` · `a.webp` · `run.log` · `n.txt` · `m.json` · `r.md` · `t.csv` | 그림은 `(image/png 등, True)`, `.jpg` 와 `.jpeg` 는 `image/jpeg`. 텍스트 다섯은 `("text/plain; charset=utf-8", True)` |
| A8 | ASCII 파일 이름의 Content-Disposition (§4.6 · 결정 139) | `content_disposition("test/home/home.png", True)`, 같은 경로로 `False` | `inline;` 으로 시작하고 `filename="home.png"` 와 `filename*=UTF-8''home.png` 를 담는다. 경로의 마지막 조각만 쓴다. `False` 면 `attachment;` |
| A9 | 정상 멤버 (§4.6) | `build/.golden-update/goldens.tar.gz` · `test/골든/홈.png` · `goldens.tar.gz.d/home.png` | `safe_member` 가 모두 True |
| A10 | 펼칠 후보 (§4.4 · 결정 133) | `["a.tar","b.tar.gz","c.tgz","d.zip","e.gz","f.tar.bz2","g.png"]` | `["a.tar","b.tar.gz","c.tgz"]` |
| A11 | 대소문자 · NFC 충돌 (§4.2-4 · 결정 133) | `["out/A.png","out/a.png","x/é.png"(NFC),"x/é.png"(NFD),"ok.png"]` | 짝 둘. 짝 안은 정렬, 목록도 정렬. `ok.png` 는 없다 |
| A12 | 상한 안 (§4.2-5 · 결정 136 · 137) | `capacity_verdict(stored=10, actual=5, limit=100, free=10**12, min_free=10)` | None |
| A13 | Host 허용 목록 (§4.6 · 결정 148) | `localhost` · `localhost:8787` · `macmini.local:8787` · `10.1.2.3` · `172.16.0.1` · `172.31.255.255` · `192.168.0.10:8787` · `100.64.0.1` · `100.127.255.255` · `127.0.0.1:8787` · `[fd00::1]:8787` · `public_host="ci.example.org"` 일 때 `ci.example.org` · `hostname="macmini"` 일 때 `macmini:8787` | 모두 True |
| A14 | 같은 출처 (§4.6 · 결정 145) | host `macmini.local:8787`. (Origin 같음, Referer 없음) · (Origin `"null"`, Referer `http://macmini.local:8787/shelf/412/`) · (Origin 없음, Referer 같음) | 모두 True |
| A15 | 모델 (§4.1 · §6) | `jobfactory.job(1)` · `ShelfItem` 의 `dataclasses.fields` | `Job.keep is False`. `ShelfItem` 필드 이름과 차례가 §5.1 의 열 21개와 같다(`job_id` … `error_code`) |
| A16 | 서버 키 기본값 (§3.1 · 결정 131 · 136) | `make_config(tmp_path)`, toml `[server] shelf_dir = "~/rcm-shelf"` | 기본 `cfg.shelf_dir == cfg.data_dir / "shelf"`, `shelf_max_bytes == 53687091200`, `182`, `365`, `shelf_enabled is True`. `~` 는 펼친 절대 경로. `data_dir` 만 바꾸면 기본 `shelf_dir` 도 따라간다 |
| A17 | 프리셋 `keep` (§3.2 · 결정 126) | `parse_preset` 에 `artifacts` 와 `keep = true`, 같은 것에서 키 없음 | `keep is True` · `keep is False` |
| A18 | v23 → v24 (§5.1 · 결정 127) | 새 DB 에 잡 하나 → 닫기 → `downgrade_to_v23` → 다시 열기 | `user_version() == 24`. `backup/rcm.sqlite3.v23.bak` 이 있고 그 파일은 버전 23 · `keep` 열 없음. 옛 잡은 `keep` 0 · `Job.keep is False`. `shelf_items` 열이 §5.1 차례, 인덱스 `shelf_items_state(state, last_used_at)` · `shelf_items_latest(preset, branch, shelved_at)`. `jobs` 의 마지막 열이 `keep` |
| A19 | 새 DB 와 올린 DB 의 스키마가 같다 (§5.1) | 새 DB 하나, A18 로 올린 DB 하나 | `SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name='shelf_items'` 가 공백을 접은 뒤 같다. `PRAGMA table_info(jobs)` 의 `keep` 줄(타입 INTEGER · notnull 1 · 기본 `0` · 마지막 차례)이 같다 |
| A20 | 생성 때 keep (§6 · 결정 127) | `create_job(..., keep=True)` · `create_job_with_submission(..., keep=True)` · 인자 없는 `create_job` | 둘은 `keep` 1, 마지막은 0 |
| A21 | 로컬 finish 가 보관 행을 세운다 (§4.2 · 결정 129) | `enqueue_keep` → `claim` → `finish(SUCCEEDED, bundle=collect_result(state=READY, …))`. keep 이 아닌 잡도 같은 순서로 | keep 잡: `shelf_item(N).state == "pending"`, preset · inputs_json · repo · branch · source_mode · requester 가 잡에서 복사, `requested_at == now`, `job_state` · `job_finished_at` 은 아직 비어 있다. keep 이 아닌 잡: 행 없음 |
| A22 | 원격 publish 가 행을 세우고 active 때 잡 상태를 채운다 (§4.2 · 결정 129) | keep 잡을 `worker_name="mac2"` 로 claim → `publish_bundle(READY)` → 행 확인 → `finish(SUCCEEDED)` → `claim_shelf_item` → `finish_shelf_item(ok=True, …)` | publish 직후 잡은 running 이고 행은 pending, `job_state` NULL. active 가 된 뒤 `job_state == "succeeded"`, `job_finished_at` 이 finish 시각 |
| A23 | `armed` (§4.1 표 · 결정 126) | keep 0 인 잡을 uploading · queued · running · cancelling 각각에서 `set_job_keep(N, now, enabled=True)`. queued 것은 이어서 claim → finish(bundle READY) | 모두 `"armed"`, `keep` 1, 보관 행 없음. finish 뒤에 pending 행이 선다 |
| A24 | `requested` (§4.1 표) | 끝난 keep 0 잡 + `ready_bundle`(만료 전 · ack 없음) | `"requested"`, `keep` 1, pending 행 하나 |
| A25 | `retouched` (§4.1 표 · 결정 132) | active 행(`last_used_at = NOW-10D`), stale 행(`stale_since` 있음) | 둘 다 `"retouched"`. `last_used_at == now`, stale 행은 active 로 바뀌고 `stale_since` 가 NULL |
| A26 | `retried` (§4.1 표 · 검토 P1-10) | failed 행(`reason_code="shelf_full"`) + 묶음 ready | `"retried"`, 행이 pending, `reason_code` NULL |
| A27 | 합류가 keep 을 켠다 (§4.1 · 결정 128) | keep 0 잡에 `join_or_bump(key, "bob", "bob@x", 0, now, keep=True)`, 다른 잡에 `join_or_bump_with_submission(..., keep=True)` | 두 잡 모두 `keep` 1, `join_count` 1 |
| A28 | supersede 가 keep 을 넘긴다 (§4.1 · 결정 128) | 옛 잡 keep 1 queued, 새 잡 keep 0 queued(같은 브랜치) → `supersede(new, [old], now, mode="all", grace_seconds=1)` | 반환 `[old]`, 새 잡 `keep` 1, 옛 잡 `keep` 은 그대로 1 |
| A29 | ack 미루기 (§4.2 · 결정 130) | A21 의 잡(행 pending, 합류 0)에 소유자 `ack_bundle(N, sha, owner=True, now)` | `status 200`, `purge False`, `deferred True`, `acked_at == now`. 묶음 상태 ready, `bundle.tar` 그대로 |
| A30 | TTL 청소에서 뺀다 (§4.2 · 결정 130) | 묶음 셋, 모두 `expires_at <= NOW`. 보관 행이 pending · copying · active | `bundles_due(NOW)` 에 active 것만 나온다 |
| A31 | 보관 행 집기 (§4.2-1 · §6) | pending 둘(`requested_at` T, T+60) | 첫 `claim_shelf_item` 이 T 것을 copying 으로 바꿔 돌려준다. 둘째가 나머지, 셋째는 None |
| A32 | 보관 성공 (§4.2-6) | copying 행 → `finish_shelf_item(N, ok=True, file_count=3, total_bytes=1234, reason_code=None, now=…)` | active, `shelved_at == last_used_at == now`, 수와 바이트가 적힘, `job_state` · `job_finished_at` 을 잡 행에서 다시 복사 |
| A33 | touch (§4.3 · 결정 132) | active 행 `last_used_at=T` 에 `T+61M`, stale 행에 아무 시각 | 둘 다 True. 앞의 것은 `last_used_at` 갱신, 뒤의 것은 active · `stale_since` NULL |
| A34 | 고정 링크의 대상 (§4.6 · 결정 147) | golden-update · dev 의 active 셋(shelved T1<T2<T3), main 브랜치의 더 늦은 active 하나, dev 의 더 늦은 stale 하나 | `latest_shelf_item("golden-update", "dev")` 가 T3 항목 |
| A35 | 목록 · 하나 · 고침 · 잊기 · 합계 (§6 · §4.8) | 상태가 섞인 행 다섯 | `shelf_items(state="active")` 와 `preset=` 거르기, `limit`. 없는 번호의 `shelf_item` 은 None. `mark_shelf(N, "stale", now, stale_since=now)` 가 그 열만 바꾼다. `forget_shelf(N)` 뒤 행 없음. `shelf_totals()` 의 `stored_bytes` 는 active · stale 합, `items` 는 `{active, stale, pending, failed}` 수 |
| A36 | 잡 행보다 오래 산다 (§4.5 · 결정 125) | active 행 + 묶음 purged + 잡은 181일 전 종료 → `delete_old_jobs(NOW - 180D)` | 잡 행은 지워지고 `shelf_item(N)` 은 preset · branch · sha · requester · job_state 를 그대로 돌려준다 |
| A37 | stale 표시 (§4.5-1 · 결정 131) | active 행 `last_used_at = NOW-182D` + 폴더 → `sweep_once(NOW)` | stale, `stale_since == NOW`, 폴더 그대로, 이벤트 하나(Q7) |
| A38 | 지운 뒤에 표시한다 (§4.5-2 · 결정 134) | stale 행 `NOW-365D` + 파일 든 폴더. 삭제 함수를 감싸 지우는 순간의 행 상태를 기록 | 지우는 순간 행은 아직 stale. 끝나면 폴더 없음, `deleted`, `deleted_at == NOW`, `total_bytes == 0`, `rec.errors == []` |
| A39 | 묘비와 실패 행 정리 (§4.5-3 · 4 · 결정 135) | deleted 행 `deleted_at = NOW-30D`, failed 행 30일 지남(기준 시각은 Q5) | 두 행 모두 사라진다 |
| A40 | data_dir 밖 보관함 (§4.5-2 · 검토 P1-5) | `shelf_dir = tmp_path/"other-disk"/"shelf"`, 지울 항목 하나 | 폴더가 지워지고 deleted. `EXDEV` 오류 없음(기존 `_remove_tree` 를 그대로 쓰면 여기서 빨갛다) |
| A41 | 보관함이 꺼져도 정리는 돈다 (§3.1 · 결정 136) | `shelf_max_bytes = 0`, stale 대상 하나 · 삭제 대상 하나 | 둘 다 A37 · A38 처럼 처리된다 |
| A42 | 처음 기동 (§5.3) | `shelf_dir` 없음 · 행 없음 → `reconcile_shelf_on_start(store, cfg)` | `ShelfStartup(available=True, error_code=None)`, 폴더와 `.rcm-shelf` 가 생긴다 |
| A43 | staging 비우기 (§5.3) | `.staging/412.ab12/files/x` · `.staging/stray` | 둘 다 사라진다 |
| A44 | copying 입양 (§5.3 · 검토 P1-3) | copying 행 + `<shelf>/N/manifest.json`(유효) | active(채우는 값은 Q18) |
| A45 | copying 되돌림 (§5.3) | copying 행, 폴더 없음, 묶음 ready | pending |
| A46 | 행 없는 폴더는 지우지 않는다 (§5.3) | `<shelf>/999/`(행 없음) · `<shelf>/notes/` | 둘 다 그대로 |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A88 | `AckDecision` 에 `deferred` 를 더해도 옛 생성이 깨지지 않는다 (§6) | `artifacts.AckDecision(200, False, False, None)` 위치 인자, `test_store_m5e` 의 ack 시험들 | `deferred is False`. 기존 시험은 고치지 않고 통과한다 |
| A89 | 오프라인 gc 가 사본을 v24 로 올린다 (§5.4) | `test_offline_gc` 의 v7 + WAL 픽스처 그대로 | 사본은 v24 까지 오르고 원본은 7 그대로. 기존 시험 그대로 통과 |

## 에러

### 명세에서

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A47 | 삭제일이 정리 대기일보다 크지 않다 (§3.1) | `(stale, delete) = (182, 182)` · `(365, 100)` | `ConfigError`, 문장에 `shelf_delete_days` |
| A48 | 정리 대기일이 1 보다 작다 (§3.1) | `shelf_stale_days = 0` · `-1` | `ConfigError` |
| A49 | 상한이 음수 (§3.1) | `shelf_max_bytes = -1` | `ConfigError` |
| A50 | 다른 청소기의 폴더 안 (§3.1 · §5.4) | `shelf_dir` 을 `data_dir` 의 `artifacts/s` · `jobs/s` · `workspaces/s` · `blobs/s` · `mirrors/s`, 그리고 `data_dir/artifacts` 자체로 | 여섯 모두 `ConfigError` |
| A51 | keep 인데 산출물 글롭이 없다 (§3.2) | `keep = true`, `artifacts` 없음 | `ConfigError`, 문장에 `keep needs artifacts` |
| A52 | 제어문자 · `.git` (§4.6 · 결정 139 · 검토 P0-7) | `"a\x01b"` · `"a\x1fb"` · `"a\x7fb"` · `"line\nbreak.png"` · `"x/.git/config"` · `".git"` | `safe_member` 가 모두 False |
| A53 | 그 밖의 나쁜 경로 (§4.6) | `""` · `"/etc/passwd"` · `"../x"` · `"a/../b"` · `"a//b"` · `"./a"` · `"a\\b"` · `"a\x00b"` · None · `3` · `b"x"` · 256바이트 조각 · 4097자 경로 | 모두 False, 예외 없음 |
| A54 | 내려받기로만 (§4.6 · 결정 139) | `x.html` · `x.htm` · `x.xhtml` · `x.svg` · `x.js` · `x.pdf` · `x`(확장자 없음) · `x.png.html` | 모두 `("application/octet-stream", False)` |
| A55 | 위험한 파일 이름 (§4.6 · 검토 P0-7) | `결과.png` · `"a\r\nX-Evil: 1.txt"` · `'a"b\\c.txt'` | 결과 문자열이 `encode("latin-1")` 로 성공하고 CR · LF 가 없다. ASCII `filename=` 은 비 ASCII · 제어문자 · 따옴표 · 역슬래시가 `_` 다(`"__.png"`). `unquote(filename*)` 가 원래 이름과 같다 |
| A56 | 상한 판정 (§4.2-5 · 결정 136 · 137) | `limit=0` · `stored+actual > limit` · `free-actual < min_free` · 앞 둘이 함께 · 셋이 함께 | `"disabled"` · `"shelf_full"` · `"disk_low"` · `"shelf_full"` · `"disabled"`(차례: disabled → shelf_full → disk_low) |
| A57 | Host 거절 (결정 148) | `evil.com` · `evil.com:8787` · `10.0.0.1.evil.com` · `localhost.evil.com` · `macmini.local.evil.com` · `172.32.0.1` · `172.15.255.255` · `100.128.0.1` · `100.63.255.255` · `8.8.8.8` · `""` · `[2001:db8::1]` | 모두 False |
| A58 | 다른 출처 (결정 145 · 검토 P0-6) | Origin `http://evil.com` · 둘 다 없음 · Origin `"null"` 에 Referer 없음 · Origin 다름 + Referer 같음 · Referer `http://macmini.local:8787.evil.com/` · Referer `http://macmini.local:8787@evil.com/` · 포트가 다름 | 모두 False. Origin 이 있으면 Referer 로 물러서지 않는다 |
| A59 | `no_bundle` (§4.1 표) | 끝난 잡에 묶음 행 없음(글롭 없는 프리셋), 묶음 상태 `empty` · `dropped` · `failed` · `skipped` · `disabled` | 모두 `"no_bundle"`, `keep` 은 그대로 0, 보관 행 없음 |
| A60 | `gone` (§4.1 표) | 묶음 `purged` · `expired` · ready 인데 `expires_at <= now` · `acked_at` 있음 + 합류 0 | 모두 `"gone"`, 보관 행 없음 |
| A61 | 보관함이 꺼진 채 묶음이 ready (§3.1 · §4.2) | `enabled=False` 로 A21 의 finish, 이어서 소유자 ack | 행이 곧장 `failed`, `reason_code == "disabled"`. ack 는 `purge True` · `deferred False` |
| A62 | 보관 실패 (§4.2-7) | copying 행 → `finish_shelf_item(ok=False, reason_code="shelf_full", …)` | failed + 이유. 잡 행과 `get_bundle(N)` 은 호출 전과 같다 |
| A63 | 지우기 실패 (§4.5-2 · 결정 134) | 삭제 대상 행, `rmtree` 가 `EACCES` 를 낸다 → 회차 하나 → `rmtree` 복구 → 회차 하나 | 첫 회차: 행 그대로 stale, `error_code` 에 `EACCES`, `rec.errors` 에 경로 없는 한 줄, 폴더 남음. 둘째 회차: deleted, `error_code` 비움(제안) |
| A64 | 안전판 (§4.2 · 결정 130) | pending 행, 묶음 `expires_at = NOW-24h` → `sweep_once(NOW)` | 보관 행 `failed`, `reason_code == "timeout"`, 묶음은 정상 청소로 expired · 파일 없음(같은 회차인지는 Q10) |
| A65 | 표식 없음 (§5.3 · 검토 P1-4) | active 행 하나, `.rcm-shelf` 없음. 두 갈래: `shelf_dir` 이 없다 / 있는데 비었다 | `ShelfStartup(False, "shelf_dir_missing")`. 모든 행이 열까지 호출 전과 같다. 없던 `shelf_dir` 을 만들지 않고 표식도 쓰지 않는다(빈 마운트 지점 아래 루트 디스크에 쓰지 않는다) |
| A66 | copying 인데 묶음이 없다 (§5.3) | copying 행, 폴더 없음, 묶음 `purged` 또는 행 없음 | `failed`, `reason_code == "bundle_gone"` |
| A67 | 폴더 없는 항목 (§5.3) | active 행 · stale 행, 둘 다 폴더 없음 | 둘 다 `failed`, `reason_code == "missing"`, 이벤트 하나씩(Q7) |
| A68 | 화해를 부르지 않는 곳 (§5.3) | `reconcile_shelf_on_start` 를 호출되면 터지는 가짜로 바꾼다. `.staging/junk` 와 copying 행을 둔다. `main(["gc", "--dry-run", "--config", path])`, `Store(path)`, `Janitor(store, cfg)` | 셋 다 가짜를 부르지 않는다. `.staging/junk` 와 copying 행이 그대로다 |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A90 | 행 세우기는 finish 와 한 트랜잭션 (§4.2 · 결정 129) | `block(store, "INSERT", "shelf_items")` → keep 잡 `finish(SUCCEEDED, bundle=READY)` | 예외. 잡은 running, 묶음 상태는 `start_collect` 의 collecting 그대로, 보관 행 없음 |
| A91 | 원격 publish 도 같다 | 같은 트리거 → running keep 잡에 `publish_bundle(READY)` | 예외. 묶음이 ready 가 아니다 |
| A92 | supersede 의 keep 넘김이 한 트랜잭션 (결정 128) | `BEFORE UPDATE OF keep ON jobs` 트리거 → A28 | 예외. 옛 잡 queued 그대로, 새 잡 우선순위 그대로 |
| A93 | 합류의 keep 켜기가 한 트랜잭션 (결정 128) | 같은 트리거 → `join_or_bump(..., keep=True)` | 예외. `join_count` 와 합류자 줄이 그대로 |
| A94 | 프리셋 keep 의 타입 (§3.2) | `keep = "yes"` · `1` · `"true"` | `ConfigError`(true 나 false 여야 한다). `sample` 의 검사와 같은 모양 |
| A95 | 서버 키의 타입 (§3.1) | `shelf_max_bytes = true` · `shelf_stale_days = "abc"` · `shelf_dir = 3` | `ConfigError`. bool 이 int 로 새지 않는다(`_coerce_scalar`) |
| A96 | 반쯤 지우다 실패 (결정 134) | 가짜 `rmtree` 가 파일 몇 개를 지운 뒤 예외 → 회차 둘 | 첫 회차 뒤 행은 stale + `error_code`. 둘째 회차에 끝까지 지우고 deleted |
| A97 | DB 오류가 청소기를 죽이지 않는다 | `BEFORE UPDATE ON shelf_items` 트리거, 만료 묶음 하나도 함께 둔다 → `sweep_once` → 트리거 제거 → 다시 | 첫 회차는 예외 없이 끝나고 `rec.errors` 에 shelf 한 줄, `jan.dead is None`, 만료 묶음은 같은 회차에 지워진다. 둘째 회차: 폴더가 이미 없으니 deleted 로 따라잡는다 |
| A98 | `rcm gc` 는 보관함을 안 건드린다 (§5.4) | 삭제 대상 · stale 대상이 있는 상태에서 `Janitor.gc(NOW, dry_run=False)` | 행과 폴더가 그대로 |
| A99 | `mark_shelf` 의 모르는 필드 (§6) | `mark_shelf(N, "stale", now, bogus=1)` · `mark_shelf(N, "nope", now)` | 예외, 행 그대로. 필드 이름이 SQL 로 새지 않는다 |
| A100 | 할 일이 없을 때 | pending 없음에서 `claim_shelf_item` · pending · copying · failed · deleted · 없는 번호에 `touch_shelf_item` | None · 모두 False, 행 그대로 |
| A101 | 없는 잡에 keep | `set_job_keep(9999, now, enabled=True)` | 명세에 없다(Q3). 제안: `StoreError`, 아무것도 안 씀 |

## 엣지

### 명세에서

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A69 | 182일 경계 (§7 · 결정 131) | `stage(T, T+182D-1, 182, 365)`, `stage(T, T+182D, …)` | `active` · `stale` |
| A70 | 365일 경계 (§7 · 결정 131) | `T+365D-1`, `T+365D` | `stale` · `delete` |
| A71 | 1시간 규칙 경계 (§4.3 · 결정 132) | active, prev=T. now = `T+59M` · `T+60M` · `T+61M` | False · True(「1시간 이상」) · True. 저장소에서도 `touch_shelf_item` 이 59분에는 행을 안 바꾼다(mutcheck 「1시간 규칙 제거」) |
| A72 | 정리 대기는 1분 뒤라도 쓴다 (§4.3) | stale, prev=`T`, now=`T+1M` | True. 저장소에서는 바로 active |
| A73 | 안 쓰는 길 (§4.3 · 결정 132) | HEAD 로 모든 route · `(GET,item,stale)` · `(GET,list,…)` · `(GET,latest,…)` · `(GET,api,…)` | 모두 False |
| A74 | stale 표시는 한 번 (§7) | A37 뒤 `sweep_once(NOW+1H)` | `stale_since` 는 첫 회차 값 그대로, 이벤트 수도 하나 그대로 |
| A75 | 항목 폴더가 심볼릭 링크 (§4.5-2) | `<shelf>/N` 이 `tmp_path/"outside"` 를 가리키는 링크, 삭제 대상 | 링크만 사라지고 `outside` 의 파일은 그대로. 행은 deleted |
| A76 | 안에서 밖을 가리키는 링크 (§4.5-2) | `<shelf>/N/files/evil` 이 `tmp_path/"outside"` 디렉터리 링크 | 항목은 지워지고 `outside` 안의 파일은 그대로 |
| A77 | 묘비 30일 경계 (결정 135) | `deleted_at = NOW-30D+1s` · `NOW-30D` | 남음 · 사라짐 |
| A78 | hold 중이면 이번 회차를 건너뛴다 (§4.5) | 삭제 대상에 hold(API 는 Q9) → 회차 → hold 풀기 → 회차 | 첫 회차 행 · 폴더 그대로, 오류 없음. 둘째에 deleted |
| A79 | `set_job_keep` 과 잡 종료의 경쟁 (§7 · 검토 P0-2) | keep 0 running 잡. `interleave(store, "SELECT", lambda: other.finish(N, SUCCEEDED, …, bundle=READY))` 안에서 `set_job_keep`. 반대 차례(finish 먼저)도 | 끼어든 스레드는 0.3초 시점에 아직 막혀 있다. 끝나면 결과 `armed` + pending 행. 반대 차례는 `requested` + pending 행. 어느 쪽이든 「keep 1 · 묶음 ready · 행 없음」은 없다 |
| A80 | 합류 keep 은 끄지 않는다 (결정 128) | keep 1 잡에 `join_or_bump(..., keep=False)` | `keep` 1 그대로 |
| A81 | copying 도 미룬다 (결정 130) | 행이 copying 일 때 소유자 ack | `purge False`, `deferred True` |
| A82 | 안전판 경계 (결정 130) | pending 행, 묶음 `expires_at + 24h` 가 `NOW+1s` · `NOW` | 앞: 행 pending, 묶음 남음. 뒤: A64 |
| A83 | 열과 표가 이미 있는 v23 DB (§5.1 · m5n 선례) | `downgrade_to_v23` 뒤 `jobs.keep` 열과 `shelf_items` 표 · 인덱스를 손으로 다시 만든다 | `duplicate column` · `already exists` 없이 24 까지 오른다 |
| A84 | 정렬 키로 찾는 충돌 (§4.2-4 · 결정 133) | 50,000 경로(충돌 한 짝 섞음) | 그 짝을 찾는다. 2초 안(O(n²) 는 여기서 넘는다) |
| A85 | keep + 실패만 수집 (§3.2) | 프리셋 `keep = true`, `artifacts_on = "failure"`. 성공한 잡과 실패한 잡 | 설정은 통과. 실패 잡은 행이 서고, 성공 잡은 묶음이 없어 행이 없다 |
| A86 | 설정의 통과 경계 (§3.1) | `stale=1, delete=2` · `shelf_max_bytes = 0` · `shelf_dir = data_dir/"artifactsX"` | 모두 통과. `max_bytes = 0` 이면 `shelf_enabled is False` |
| A87 | 상한 경계 (결정 136 · 137) | `stored+actual == limit` · `== limit+1`, `free-actual == min_free` · `== min_free-1` | None · `shelf_full` · None · `disk_low` |

### 코드를 읽으며 더한 경우

| # | 경우 | 준비 | 기대 |
|---|---|---|---|
| A102 | 원격 잡의 틈: ready 와 finish 사이의 `rcm keep` (Q1) | keep 0 잡을 `mac2` 로 claim → `publish_bundle(READY)`(running) → `set_job_keep` → `finish(SUCCEEDED)`(bundle 없음) | 명세대로면 `armed` 이고 `_upsert_bundle` 은 이미 지나가서 행이 영영 안 선다. 불변식 「keep 1 이고 묶음 ready 인 잡에는 보관 행이 있다」로 잠근다. 제안: 묶음이 ready 면 잡 상태와 무관하게 `requested` |
| A103 | 같은 틈의 합류 (Q1) | A102 의 publish 뒤 `join_or_bump(..., keep=True)` → finish | A102 와 같은 불변식 |
| A104 | `set_job_keep` 과 ack 의 경쟁 (결정 130) | 끝난 keep 0 잡 + ready 묶음, 합류 0. `interleave` 로 `set_job_keep` 판정 중에 소유자 ack, 반대 차례도 | 둘 중 하나만: (`requested` + ack `deferred True`) 또는 (ack `purge True` + `gone`). `requested` 인데 `purge True` 는 없다 |
| A105 | TTL 청소와 `requested` 의 경계 경쟁 (Q13) | `bundles_due(NOW)` 가 묶음을 집은 뒤, 지우기 전에 `set_job_keep(N, NOW-1s)` | 지금 코드 모양이면 pending 행만 남고 묶음은 지워진다. 제안을 정한 뒤 「pending 행의 묶음은 지워지지 않는다」로 잠근다 |
| A106 | ready 가 아닌 쓰기에는 행이 없다 (결정 129) | keep 잡에 `publish_bundle(UPLOADING)`(업로드 영수증) · `publish_bundle(EMPTY)` · `set_bundle_failed` | 셋 다 보관 행 없음 |
| A107 | 불변 묶음 (`_upsert_bundle` 의 조기 반환) | pending 행 뒤 같은 잡에 `publish_bundle(READY)` 한 번 더(finish 재시도) | 행이 그대로(`requested_at` 같음) |
| A108 | `INSERT OR IGNORE` | `shelf_row(N, "failed")` 를 먼저 넣고 keep 잡 finish(bundle READY) | 그 행이 그대로 |
| A109 | `requested` 와 `gone` 의 만료 경계 | `now = expires_at - 1s` · `expires_at` | `requested` · `gone`(ack 판정의 `now >= expiry` 와 같은 쪽) |
| A110 | ack 됐지만 합류가 있었다 | `acked_at` 있음, `join_count` 1 | `requested`(TTL 까지 남는 묶음이다) |
| A111 | 미룬 뒤 다시 온 ack | A29 뒤 같은 소유자 ack | `purge False`. 두 번 지우라고 하지 않는다 |
| A112 | 미루지 않아도 purge 가 아니었던 ack (Q12) | 합류 1 + pending 행에 소유자 ack, 관리자 ack | `purge False`. `deferred` 값은 Q12 의 답 |
| A113 | active 항목의 ack 는 그대로 지운다 | 행 active + 소유자 ack(합류 0) | `purge True`, `deferred False` |
| A114 | 두 copier 가 동시에 집기 | pending 하나, `Store` 객체 둘, `threading.Barrier(2)` | 하나만 행을 받고 다른 하나는 None |
| A115 | 집는 차례의 동점 | `requested_at` 이 같은 pending 둘 | 작은 job_id 먼저 |
| A116 | 아직 안 끝난 잡의 행 (Q14) | A22 의 publish 직후(잡 running) `claim_shelf_item` | Q14 의 답을 잠근다. 집는다면 finish 전에 active 가 되어 `job_state = running` 이 박힌다 |
| A117 | copying 이 아닌 행의 finish (Q10) | 행이 안전판으로 `failed(timeout)` 이 된 뒤 `finish_shelf_item(ok=True, …)` | 행이 바뀌지 않는다(조건부) |
| A118 | 잡 행이 없어진 뒤의 finish | copying 행, 잡 행은 SQL 로 지움 → `finish_shelf_item(ok=True)` | active. 잡에서 복사할 값은 요청 때 것 그대로, 예외 없음 |
| A119 | 동시 touch | active `last_used_at = NOW-2H`, `Store` 둘이 같은 `now` 로 동시에 | 정확히 하나만 True, `last_used_at == now` |
| A120 | 고정 링크의 가장자리 (Q23) | stale 뿐 · pending 뿐 · `shelved_at` 동점 · 다른 프리셋 · `branch=None` | None · None · 큰 job_id · 무시 · Q23 의 답 |
| A121 | 합계의 가장자리 (Q16) | failed 행에 `total_bytes` 가 있다, deleted 행, copying 행 | failed 와 deleted 의 바이트는 안 센다. copying 이 `items.pending` 에 드는지는 Q16 |
| A122 | 이미 없는 폴더의 삭제 | 삭제 대상, 폴더 없음 | deleted, 오류 없음 |
| A123 | 서버가 1년 꺼져 있었다 | active 행 `last_used_at = NOW-400D` | 한 회차에 바로 deleted. `plan_sweep` 은 그 항목을 `to_delete` 에만 넣는다 |
| A124 | 실행 중 디스크가 빠졌다 (Q8) | 기동 뒤 `shelf_dir` 을 통째로 옮겨 없앤다. 삭제 대상 행 하나 | 지금 명세대로면 「지울 것 없음」으로 deleted 가 되어 색인을 잃는다. 제안: 표식이 없으면 sweep 을 건너뛰고 행 그대로 |
| A125 | 설정을 늘려 stale 이 다시 active 구간 (Q6) | stale 행 `last_used_at = NOW-200D`, `shelf_stale_days = 300` 으로 새 Janitor | Q6 의 답을 잠근다 |
| A126 | 잡 행이 없는 항목의 이벤트 (Q7) | 잡 행을 지운 active 항목을 stale 로 | Q7 의 답. events 표라면 고아 행이 남는지 본다 |
| A127 | 계획은 진행 중 · 실패 행을 건드리지 않는다 | pending · copying · failed 행, `last_used_at` 이 아주 옛날 | `to_stale` · `to_delete` 에 없다. failed 는 `to_forget` 에만(30일 뒤) |
| A128 | 미래의 `last_used_at` | `stage(T+H, T, 182, 365)` | `active` |
| A129 | v22 DB 는 한 번에 24 까지 | `test_store_m5n.downgrade_to_v22` → 열기 | `user_version 24`, 백업은 `v22.bak` 하나(`migrate()` 는 시작 버전만 뜬다), `v23.bak` 은 없다 |
| A130 | 옛 버전으로 깎는 도우미들 (Q15) | 「기존 시험」 줄의 도우미들, 고치지 않고 | 모두 통과. v24 DDL 이 `CREATE TABLE IF NOT EXISTS` · `CREATE INDEX IF NOT EXISTS` 가 아니면 여기서 `table shelf_items already exists` 로 죽는다(`_already_added` 는 ADD COLUMN 만 건너뛴다) |
| A131 | 남의 manifest (Q18) | copying 행 N, `<shelf>/N/manifest.json` 이 `{"job": 999, …}` · JSON 아님 · `files` 없음, 묶음 ready | 입양하지 않고 pending |
| A132 | 폴더는 있는데 manifest 가 없다 (Q18) | copying 행, `<shelf>/N/` 에 반쯤 쓴 파일, 묶음 ready | pending, 폴더는 그대로(모르는 것은 안 지운다). copier 는 뒤에 `dest_exists` 로 실패한다 |
| A133 | 표식 없음 + 진행 중 · 실패 행만 (Q19) | `.rcm-shelf` 없음, pending 과 failed 행만 | Q19 의 답(표식을 쓰고 available 인가) |
| A134 | 화해를 두 번 | A44 · A45 · A67 상황에서 두 번 | 둘째 호출은 아무것도 안 바꾼다 |
| A135 | staging 안의 링크 | `.staging/link` 이 `tmp_path/"outside"` 를 가리킨다 | 링크만 사라지고 `outside` 안은 그대로 |
| A136 | 이름이 숫자가 아닌 폴더 · 숫자 폴더 | `<shelf>/412/`(행 없음) · `<shelf>/tmp-notes/` | 둘 다 그대로. 묶음 화해(`reconcile_bundles_on_start`)처럼 숫자 폴더를 지우지 않는다 |
| A137 | pending 항목의 잡은 180일 삭제에서 빠진다 | keep 잡, 행 pending, 묶음 만료(안전판 전), 잡은 181일 전 종료 → `delete_old_jobs` | 잡 행이 남는다(묶음이 purged 가 아니다) |
| A138 | `shelf_dir` 경로의 가장자리 (Q17) | `data_dir/"shelf"/".."/"artifacts"/"x"` · 상대 경로 · `shelf_dir == data_dir` · data_dir 을 품는 상위 폴더 · `artifacts` 를 가리키는 링크 · `RCM_SERVER_DATA_DIR` 로 바꾼 data_dir | 첫 줄은 `ConfigError`(정규화 뒤 판정). 나머지는 Q17 의 답. 환경 변수로 바꾼 data_dir 을 기본 `shelf_dir` 이 따라간다 |
| A139 | 펼친 폴더 이름이 너무 길다 (Q21) | 조각이 253바이트인 `x…x.tar` | `<이름>.d` 가 255바이트를 넘는다. 후보에서 빼는지는 Q21 |
| A140 | 충돌의 모양 (Q20) | `["a","a-b","a/b"]` · 같은 경로 두 번 · `A.png` `a.png` `a.PNG` | `("a","a/b")` 를 찾는다(정렬하면 이웃이 아니다) · 짝 하나 · 짝 셋. 무작위 집합 200개에서 `core/artifacts.collisions` 와 결과가 같다 |
| A141 | Host · Origin 의 표기 (Q22) | `MacMini.LOCAL:8787` · `macmini.local.` · `[::1]:8787` · `169.254.1.1` · `0.0.0.0` · Host 와 Origin 의 대소문자가 다름 · `https://` Origin | 첫 줄 True, `169.254` · `0.0.0.0` 은 False. 나머지는 Q22 의 답 |
| A142 | 파일 이름의 `%` 와 긴 이름 | `100%.png`, UTF-8 255바이트 이름 | `filename*` 에 `%25`, 긴 이름도 예외 없이 latin-1 로 인코딩된다 |
| A143 | 확장자 대소문자 (Q21) | `A.PNG` · `X.TAR.GZ` · `Y.TGZ` | Q21 의 답 |
| A144 | `.GIT` 조각 (Q21) | `.GIT/config` · `x/.Git/HEAD` | APFS 에서는 `.git` 과 같은 이름이다. Q21 의 답 |
| A145 | 여유를 못 쟀다 (Q23) | `capacity_verdict(…, free=None, …)` | Q23 의 답 |
| A146 | touch 판정의 가장자리 (Q23) | `should_touch(T+60, "active", T)`(시계가 뒤로) · 상태 pending · copying · failed · deleted | 제안: 모두 False |
| A147 | supersede 가 넘기는 keep 은 실제로 치운 잡의 것만 | 옛 잡 둘(keep 1). 하나는 다른 요청자의 합류자가 있어 남는다. 별도로 새 잡이 이미 끝난 경우 | 남은 잡이 keep 의 출처면 새 잡은 0 그대로. 새 잡이 끝났으면 `[]` 이고 keep 도 그대로 |
| A148 | 표에 없는 `set_job_keep` 상태 (Q3) | 보관 행 pending · copying(두 번째 `rcm keep`), deleted 행 + 살아 있는 묶음(손으로 지운 직후), 끝난 잡의 묶음이 uploading · collecting · unavailable · unknown, `enabled=False` 의 requested · retried | Q3 의 답을 하나씩 잠근다 |
| A149 | 1시간 안의 `rcm keep` (Q4) | active, `last_used_at = NOW-10M` | `"retouched"`. `last_used_at` 이 바뀌는지는 Q4 |
| A150 | 보관함 폴더 자체가 링크 | `shelf_dir` 이 다른 폴더(다른 디스크 흉내)로 가는 심볼릭 링크, 삭제 대상 하나 | 기준 루트를 `resolve()` 한 값으로 잡으므로 지워진다. 링크 자체는 남는다 |

## 명세에 묻는 것

명세를 고치지 않고 여기에 적는다. 각 질문 끝에 그 답이 잠글 시나리오 번호를 붙였다.

1. 원격 잡에는 `armed` 와 합류 keep 이 행을 놓치는 틈이 있다. `remote_workers.worker_finish` 는 `_record_disposition` 으로 묶음을 ready 로 쓴 뒤 다른 트랜잭션에서 `store.finish` 를 부른다(`remote_workers.py:821` · `:828`). 그 사이 `rcm keep` 이 오면 잡이 안 끝났으니 `armed` 이고 keep 이 켜지지만, `_upsert_bundle` 은 이미 지나갔고 원격 `finish` 는 bundle 을 넘기지 않으므로 행이 영영 서지 않는다. 합류 `keep: true` 도 같다. 판정을 「묶음이 ready 면 잡 상태와 무관하게 `requested`」로 바꾸거나, keep 을 켜는 모든 곳(합류 포함)이 같은 트랜잭션에서 「묶음이 ready 면 행을 세운다」를 해야 하지 않나. (A102 · A103)
2. `_upsert_bundle` 은 모듈 함수라 설정을 모른다. `enabled` 를 어떻게 받나. `finish(..., shelf_enabled=)` 와 `publish_bundle(..., shelf_enabled=)` 에 인자를 더하나, Store 생성 때 넘기나. (A21 · A61)
3. `set_job_keep` 표에 없는 경우의 결과. 보관 행이 pending · copying 일 때(두 번째 `rcm keep`), deleted 행인데 묶음이 아직 살아 있을 때(손으로 지운 직후), 끝난 잡의 묶음이 uploading · collecting · unavailable · unknown 일 때, `enabled=False` 일 때 requested · retried 가 `failed(disabled)` 가 되는지, 잡 번호가 없을 때. (A101 · A148)
4. `retouched` 도 1시간 규칙을 따르나. 따르면 10분 전에 쓴 항목에 `rcm keep` 을 해도 응답만 `retouched` 이고 `last_used_at` 은 그대로다. (A149)
5. failed 행의 「30일 뒤 지운다」는 어느 시각에서 재나. `shelf_items` 에 실패 시각 열이 없다. `requested_at` 이라면 `retried` 가 `requested_at` 을 새로 쓰는지도 정해야 한다. (A39 · A127)
6. `SweepPlan` 에 「stale → active 되돌림」이 없다. `shelf_stale_days` 를 늘려 재시작하면 stale 행의 `stage()` 가 active 가 되는데, §4.3 은 「상태 열은 청소기가 회차마다 `stage()` 로 다시 맞춘다」고 적었다. `to_active` 를 더하나, stale 은 쓰기로만 되돌아가나. (A125)
7. 「이벤트를 하나 남긴다」(stale 전환 · missing)는 `events` 표인가. 항목은 182일에 stale 이 되는데 잡 행은 `metadata_retention_days`(기본 180일)에 지워지고, `delete_old_jobs` 는 자기가 지우는 잡의 이벤트만 지운다. events 표라면 대부분의 stale 이벤트가 지워지지 않는 고아 행이 된다. 로그 한 줄이나 서버 이벤트 버스인가. (A37 · A67 · A126)
8. 보관함이 `unavailable` 이거나 실행 중에 디스크가 빠졌을 때 `_sweep_shelf` 가 도나. 폴더가 없으면 삭제 대상은 「지울 것 없음」으로 deleted 가 되어 색인을 잃는다. 표식이 없으면 회차를 건너뛰는 규칙이 필요해 보인다. (A124)
9. hold 의 인터페이스가 §6 에 없다. P1 의 `_sweep_shelf` 가 hold 를 봐야 하므로 `Store.hold_shelf(job_id)` · `shelf_is_held(job_id)`(묶음의 `hold_bundle` · `bundle_is_held` 와 같은 모양)를 P1 계약에 넣어야 하지 않나. (A78)
10. 안전판(`failed(timeout)`)은 누가 쓰나. `bundles_due` 는 읽기 쿼리다. janitor 가 `_sweep_bundles` 앞에서 쓰나 `_sweep_shelf` 에서 쓰나(뒤라면 묶음은 다음 회차에 지워진다). copying 행에도 적용되나. 적용된다면 `finish_shelf_item` 은 copying 에서만 바뀌는 조건부여야 한다. (A64 · A82 · A117)
11. 보관이 끝난 뒤 미뤄 둔 ack 를 누가 판정하나. copier 가 「ack 되었고 합류자 0」을 따로 읽으면 그 사이 합류가 끼어들 수 있다. `finish_shelf_item` 이 같은 트랜잭션에서 판정해 `purge: bool` 을 돌려주는 모양이 맞나. (P2 의 ack 경쟁 시험과 이어진다)
12. `deferred` 는 「미루지 않았다면 purge 였다」일 때만 True 인가, 보관 행이 pending · copying 이면 언제나 True 인가(합류한 잡, 관리자 ack). (A112)
13. `_sweep_bundles` 는 `bundles_due` 를 트랜잭션 밖에서 읽고 파일을 지운 뒤 표시한다. 그 사이 요청 시계가 만료 직전인 `set_job_keep` 이 `requested` 를 주면 묶음은 지워지고 pending 만 남는다. `mark_bundles_purged` 가 보관 행을 다시 확인하나, 지우기 직전에 다시 읽나, `set_job_keep` 이 만료 여유를 두나. (A105)
14. `claim_shelf_item` 이 잡이 아직 안 끝난 행(원격 ready 와 finish 사이)도 집나. 집으면 active 가 될 때 `job_state = running`, `job_finished_at = NULL` 이 복사되고 다시 고쳐지지 않는다. (A116)
15. §5.1 의 DDL 은 `CREATE TABLE shelf_items` · `CREATE INDEX` 로 적혀 있다. 옛 버전으로 깎는 기존 시험들은 새 표를 남긴 채 버전만 내리므로 `IF NOT EXISTS` 가 꼭 필요하다. `_SCHEMA_V1` 과 `_MIGRATIONS[24]` 모두 그렇게 쓰는 것으로 읽어도 되나. 같은 이유로 「`DB_VERSION == 23` 네 곳」 말고 열 차례 · `user_version` 잠금 다섯 줄도 고쳐야 한다(위 「갱신할 기존 잠금」). (A83 · A130)
16. `ShelfItem` 과 열의 출처. 시각 필드가 float(UTC 초)인가 datetime 인가(순수 함수는 float, `Job` 은 datetime 이다). tree 잡은 `jobs.sha` 가 NULL 인데 「커밋」으로 `source.base_sha` 를 쓰나. `requester` 는 name 인가 label 인가. `shelf_totals()` 의 `items.pending` 에 copying 이 드나. (A15 · A21 · A121)
17. `shelf_dir` 검증의 가장자리. 상대 경로를 받나, `shelf_dir == data_dir` 과 data_dir 을 품는 상위 폴더를 받나, 금지 폴더를 가리키는 심볼릭 링크를 `resolve()` 로 막나(설정 검증 때 경로가 아직 없을 수 있다). (A138)
18. copying 입양의 세부. 「유효한 manifest」는 JSON 이고 `job == N` 이고 `files` 가 배열인 것인가. 입양할 때 `shelved_at` · `last_used_at` · `file_count` · `total_bytes` 를 무엇으로 채우나(manifest 의 합, 기동 시각). 폴더는 있는데 manifest 가 없으면 pending 으로 되돌려도 copier 가 `dest_exists` 로 실패하는데 그대로 두나. (A44 · A131 · A132)
19. 표식을 새로 쓰는 조건 「행이 하나도 없으면」은 모든 행인가, active · stale 행인가. pending · failed 행만 있을 때 표식을 쓰고 available 로 가나. unavailable 일 때 `.staging` 비우기와 copying 처리도 건너뛰나. (A65 · A133)
20. `name_collisions` 가 디렉터리 접두(`A` 와 `a/b.png`)도 충돌로 보나. `core/artifacts.collisions` 는 본다. 정렬 키만으로는 접두 충돌이 이웃하지 않는다(`a` · `a-b` · `a/b`). 세 개 이상이 겹치면 모든 짝을 돌려주나. (A84 · A140)
21. 대소문자와 이름 길이. `inline_disposition` 과 `expand_candidates` 가 `A.PNG` · `X.TAR.GZ` 를 같은 확장자로 보나. `safe_member` 가 `.GIT` · `.Git` 조각(APFS 에서 `.git` 과 같은 이름)을 거절하나(`check_path` 는 정확히 `.git` 만 본다). 펼친 폴더 이름 `<이름>.d` 가 255바이트를 넘는 tar 는 후보에서 빼나. (A139 · A143 · A144)
22. Host 와 Origin 의 표기. `[::1]`(IPv6 루프백)은 127/8 과 같이 받나, 끝에 점이 붙은 `macmini.local.` 은, Host 와 Origin 의 대소문자가 다르면, 앞단 TLS(Tailscale serve 같은)로 `https://` Origin 이 오면 같은 출처인가. (A141)
23. 순수 함수의 나머지 입력. `capacity_verdict` 의 `free` 가 None(못 쟀다)일 때, `should_touch` 의 상태가 active · stale 이 아니거나 시계가 뒤로 갔을 때, `touches` 에 모르는 route 가 올 때, `latest_shelf_item(preset, None)` 이 「브랜치 무관」인지 「브랜치가 NULL 인 항목」인지. (A120 · A145 · A146)
