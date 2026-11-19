# M5p 작업 명세 — 보관함: 잡이 만든 파일을 공유기 안에서 링크로 연다

> 2026-11-19 오너 요청: 「골든 테스트한 산출물을 저장하고, 접근 가능한 곳 링크로 달라는 스크립트가 오면
> 에이전트 없이 산출물이 특정 폴더에 저장되고, 거기는 공유기 안에서 접근 가능하게. 6개월 뒤 정리될 항목에
> 넣어 두고 1년 뒤 알아서 삭제, 되살리거나 가져오면 6개월 또 유예.」 같은 날 오너가 고른 답(셋 다 추천안):
> 시계는 「마지막으로 쓴 지 6개월이면 정리 대기, 1년이면 삭제」, 저장은 「프리셋 표시 + 실행 플래그」,
> 접근은 「브라우저 링크」(파일 공유 SMB 는 안 함).
>
> 결정 번호는 **125~148**. 전부 추천값으로 구현하고 오너 확인을 기다린다.
> 초안은 격리 설계 검토(P0 7건 · P1 16건 · 의견 9건)를 거쳐 이 판이 되었다. 검토가 바꾼 곳은 §14 에 적었다.
> 테스트 시나리오는 `docs/m5p-test-scenarios-{a,b,c}.md`(역할마다 하나), 구현 중 바뀐 것은 §13 에 남긴다.

## 0. 한 줄

**표시된 잡의 산출물을 서버가 스스로 「보관함」 폴더에 풀어 두고, 공유기 안의 누구든 브라우저 링크로 보고
받아 간다. 6개월 안 쓰면 정리 대기, 1년 안 쓰면 지운다. 쓰면 시계가 다시 돈다.**

## 1. 실측 — 지금 무엇이 있고 무엇이 모자라나

### 1.1 지금 있는 것: 묶음(M5e)은 가져가는 통로다

- 프리셋이 `artifacts` 글롭을 선언하면 워커가 워크스페이스를 지우기 전에 파일을 모아 서버의
  `<data_dir>/artifacts/<job>/bundle.tar`(무압축 GNU tar, 바이트 결정적)에 둔다.
- 받는 길은 `rcm artifacts N --fetch` 와 `rcm run --fetch-artifacts` 뿐이고 **토큰이 있어야 한다**
  (`read_auth = "none"` 이어도 그렇다, `docs/m5e-workplan.md` §6). 웹은 받는 명령을 복사해 줄 뿐이다.
- 수명은 `artifact_retention_hours = 24` 다. 소유자가 받고 ack 하면(합류자 0) **그 요청 안에서 바로 지운다**.
  `rcm run --fetch-artifacts` 는 다 받은 뒤 스스로 ack 한다.
- 결정 58: 「묶음은 보관소가 아니라 가져가는 통로다. 프리셋별 TTL 은 안 만든다.」

### 1.2 운영 DB(2026-09-13 ~ 11-19, `sqlite3 -readonly`)

| 프리셋 | 묶음 | 평균 파일 | 평균 크기 | 최대 |
|---|---|---|---|---|
| game-web-e2e | 196 | 4 | 1.3 MB | 71.5 MB |
| scenario-qa | 74 | 6 | 4.6 MB | 21.1 MB |
| game-web-check | 38 | 25 | 17.8 MB | 308.5 MB |
| build-dev | 33 | 4 | 160.8 MB | 390.4 MB |
| deploy-dev | 29 | 10 | 2.7 MB | 7.0 MB |
| golden-update | 13 | 3 | 0.5 MB | 1.7 MB |

- golden-update 의 묶음은 `goldens.tar.gz`(바뀐 PNG 만), `manifest.json`, `run.log` 셋이다.
  **PNG 가 tar.gz 안에 있어서** 그대로 보관하면 브라우저에서 그림이 안 보인다(§4.4, 결정 133).
- 데이터 디스크 여유는 **199 GiB** 다(2026-11-19. 2026-09 에는 628 GiB). 모든 묶음을 1년 들고 있으면
  build-dev 혼자 한 해 약 18 GB 를 쓴다. 그래서 **표시한 잡만** 보관하고 상한을 둔다(결정 136).

### 1.3 모자란 것

1. 24시간 뒤 사라진다. 「지난주 골든 그림」을 다시 볼 길이 없다.
2. 토큰 있는 CLI 로만 받는다. 폰이나 다른 PC 에서 링크로 볼 수 없다.
3. 받으려면 세션(에이전트)이 `rcm artifacts --fetch` 를 돌려 자기 디스크에 풀어야 한다.

## 2. 모양 — 사용자가 보는 것

```console
$ rcm run golden-update -f paths=test/feature/home/ --keep
#412 queued · golden-update · ~10m
...
#412 succeeded in 9m12s
shelf: http://macmini.local:8787/shelf/412/
```

- 링크를 열면(같은 공유기의 폰 · 노트북 · 다른 Mac) 그 잡의 파일 목록, 그림 미리 보기, 「zip 으로 받기」,
  잡 정보(프리셋 · 입력 · 브랜치 · 커밋 · 끝난 시각 · 요청자)가 보인다. 로그인은 없다.
- `/shelf/` 는 보관함 전체 목록이다. 「보관 중」과 「정리 대기」 두 칸이 있다.
- `/shelf/latest/golden-update?branch=dev` 는 그 프리셋 · 브랜치의 가장 최근 보관 항목으로 보낸다(303).
  「최신 골든 보기」를 북마크로 둘 수 있다(결정 147).
- 6개월 동안 아무도 쓰지 않으면 「정리 대기」로 옮겨 간다(파일은 그대로). 거기서도 6개월 더 쓰지 않으면 지운다.
  정리 대기 항목에서 파일을 받거나 「되살리기」를 누르면 「보관 중」으로 돌아가고 6개월이 다시 시작된다.
- 프리셋에 `keep = true` 면 `--keep` 없이도 늘 보관한다(golden-update 가 첫 대상이다).
- 이미 끝난 잡도 묶음이 살아 있으면 `rcm keep 412` 로 보관함에 넣는다. 다만 `rcm run --fetch-artifacts` 로
  받은 잡은 그 순간 묶음이 지워지므로 뒤늦게 넣을 수 없다. 처음부터 `--keep` 을 쓴다.

## 3. 설정

### 3.1 서버 키 (`[server]`, 재시작해야 바뀐다 — `RELOADABLE_NOT`)

| 키 | 기본 | 뜻 |
|---|---|---|
| `shelf_dir` | `"<data_dir>/shelf"` | 보관함 폴더. 절대 경로와 `~` 를 받는다. data_dir 밖(다른 디스크)이어도 된다 |
| `shelf_max_bytes` | `53687091200` (50 GiB) | 보관함 전체 상한. 넘으면 **새 보관을 거절**하고 남의 것을 쫓아내지 않는다. `0` 이면 보관함이 꺼진다 |
| `shelf_stale_days` | `182` | 마지막으로 쓴 지 이만큼이면 「정리 대기」 |
| `shelf_delete_days` | `365` | 마지막으로 쓴 지 이만큼이면 삭제. `shelf_stale_days` 보다 커야 한다 |

- 검증: `shelf_stale_days ≥ 1`, `shelf_delete_days > shelf_stale_days`, `shelf_max_bytes ≥ 0`.
  `shelf_dir` 이 `data_dir` 의 `artifacts` · `jobs` · `workspaces` · `blobs` · `mirrors` 안이면 설정 오류다
  (그 청소기들이 숫자 이름 폴더를 지운다).
- 여유 공간 바닥은 새 키 없이 기존 `min_free_bytes` 를 쓴다(결정 137).
- **보관함이 꺼졌을 때(`shelf_max_bytes = 0`)** 기존 항목은 그대로 보이고 받을 수 있으며 시계대로 정리된다.
  새 보관만 `failed(disabled)` 로 거절한다.

### 3.2 프리셋 키

```toml
[[presets]]
name = "golden-update"
artifacts = ["build/.golden-update/goldens.tar.gz", "build/.golden-update/manifest.json"]
keep = true              # 늘 보관함에 넣는다 (기본 false)
```

- `keep = true` 인데 `artifacts` 가 없으면 설정 오류(`keep needs artifacts`)다. `artifacts_on = "failure"` 와
  함께면 **실패한 잡만** 보관된다. 오류는 아니다(실패 증거를 오래 두려는 뜻일 수 있다). `rcm check` 의
  `shelf` 행이 그렇게 적는다.
- reload 로 바뀐다. 제출 때 얼린다(§4.1). 이미 낸 잡은 reload 의 영향을 받지 않는다.
- ⚠️ 프리셋은 모르는 키를 거절한다. `keep` 을 include 파일이나 `server.toml` 에 넣는 일은 **서버가 v0.7.0 으로
  오른 뒤에만** 한다. 먼저 넣으면 reload 와 재시작이 실패한다.

## 4. 동작

### 4.1 무엇이 보관되나 (결정 126·127·128)

잡의 `keep` 은 **제출 때 얼려 DB 열 `jobs.keep`(0/1)** 에 둔다(v24). 값은 `프리셋 keep OR 제출 본문 keep` 이고,
재시작과 reload 에 바뀌지 않는다. `Job.keep: bool` 을 `core/model.py` 와 `_row_to_job` 에 더한다.

- `POST /jobs` 본문 `"keep": true`. bool 이 아니면 400, 프리셋에 `artifacts` 가 없으면
  400 `keep_without_artifacts`. 응답에 `keep` 과 `shelf_url`(정해진 URL)을 싣는다.
- **옛 서버 감지**: 옛 서버는 모르는 본문 키를 거절하지 않고 무시한다. 그래서 클라이언트는 `/api/health` 에
  `shelf` 키가 있을 때만 `keep` 을 보낸다. 없으면 `server does not support --keep (rcm ≥ 0.7.0)` 를 찍고
  종료 코드 2 로 끝낸다(M5o 결정 124 의 `reload_dry_run` 과 같은 방식).
- **합류**한 제출에 `keep: true` 가 있으면 기존 잡의 `keep` 을 켠다. 끄지는 않는다(우선순위 max 와 같은 단조
  규칙). `join_or_bump(..., keep)` 과 `join_or_bump_with_submission(..., keep)` 이 **합류 트랜잭션 안에서** 켠다.
- **supersede** 가 옛 잡을 치울 때 같은 트랜잭션에서 `new.keep |= old.keep` 을 한다(우선순위를 넘기는 것과 같다).
- `rcm keep N` 은 `POST /jobs/N/keep` 이다. 그 잡의 로그를 읽을 자격(요청자 · 합류자 · admin, Bearer)이 있어야 한다.
  `Store.set_job_keep(job_id, now)` 하나의 `BEGIN IMMEDIATE` 안에서 판정하고 결과를 돌려준다.

  | 결과 | 조건 | 응답 |
  |---|---|---|
  | `armed` | 잡이 아직 안 끝났다 | 200, keep 을 켠다. 끝나면 보관된다 |
  | `requested` | 묶음이 `ready` 이고 만료 전이며 ack 로 지워질 예정이 아니다 | 202, `pending` 행을 세운다 |
  | `retouched` | 이미 보관 항목이 `active` · `stale` | 200, 시계를 다시 돌린다(= 되살리기) |
  | `retried` | 보관 항목이 `failed` 이고 묶음이 아직 살아 있다 | 202, `pending` 으로 되돌린다 |
  | `no_bundle` | 묶음이 없다(글롭 없음 · empty · dropped · failed · skipped) | 409, 이유 |
  | `gone` | 묶음이 purged · expired 이거나 `expires_at` 이 지났거나, ack 되었고 합류자 0 | 410, 이유 |

### 4.2 언제, 어떻게 보관하나 (결정 129·130)

- **보관 요청 행**은 묶음을 `ready` 로 쓰는 단 하나의 함수 `Store._upsert_bundle` 안에서, ready 를 쓴 직후
  `jobs.keep = 1` 이면 `INSERT OR IGNORE` 로 세운다(`state = 'pending'`). 이 함수가 로컬 `finish(bundle=…)` 과
  원격 `publish_bundle` 을 모두 덮는다. 불변 조기 반환보다 뒤에 둔다. 보관함이 꺼져 있으면 행을 곧장
  `failed(disabled)` 로 세운다(pending 을 거치지 않으니 ack 미루기도 없다).
- 이 시점에 원격 잡은 아직 `running` 이다. 그래서 행을 세울 때는 프리셋 · 입력 · 저장소 · 브랜치 · sha ·
  요청자만 복사하고, **잡 상태와 끝난 시각은 `finish_shelf_item`(active 로 바뀔 때) 잡 행에서 다시 복사한다**.
- **ack 미루기는 store 트랜잭션 안에서**: `Store.ack_bundle` 이 같은 트랜잭션에서 그 잡의 보관 행이 `pending` ·
  `copying` 이면 `purge = False, deferred = True` 를 돌려준다(묶음은 지우지 않고 `acked_at` 만 적는다). 보관이
  끝나면(성공이든 실패든) copier 가 「ack 되었고 합류자 0」인 묶음을 그때 지운다.
- **TTL 청소도 미룬다**: `Store.bundles_due` 쿼리가 보관 행이 `pending` · `copying` 인 묶음을 뺀다. 안전판으로
  `ready_at + artifact_retention_hours + 24h` 를 넘으면 그 보관을 `failed(timeout)` 로 접고 묶음을 정상 청소에
  넘긴다(묶음이 영원히 붙잡히지 않게).
- **copier 를 깨우는 곳**: `_on_job_change` / `_publish_job` 이 종료 잡마다 copier 이벤트를 set 한다(값싸다).
  `publish_artifacts` 가 transit 을 벗어날 때(원격 업로드가 늦게 끝남), `rcm keep` 응답 직후, 기동 직후,
  청소기 회차마다(놓친 것 줍기)도 깨운다.
- **보관 스레드** `ShelfCopier` 는 하나다. 한 번에 한 항목을, `pending` 가운데 오래된 것부터 집는다.
  1. 행을 `copying` 으로 바꾼다(조건부 UPDATE).
  2. `<shelf_dir>/.staging/<job>.<hex>/files/` 에 `bundle.tar` 를 멤버 단위로 푼다. 멤버마다 `safe_member` 를
     다시 적용한다(서버가 받은 tar 를 믿지 않는다). 일반 파일과 디렉터리만 만든다.
  3. 펼치기(§4.4).
  4. **이름 충돌**: 묶음 파일과 펼친 파일의 경로를 **합친 집합**에서 대소문자 · NFC 충돌을 찾는다(정렬 키로,
     O(n log n)). 충돌이 있으면 그 항목을 `failed(name_collision)` 로 둔다. APFS 에서 하나가 다른 것을 덮은
     채로 manifest 가 거짓이 되는 것을 막는다.
  5. 상한: staging 을 다 만든 뒤 **실측 바이트**(원본 + 펼침 + manifest)로 판정한다. `stored + actual >
     shelf_max_bytes` 면 `failed(shelf_full)`, 파일 시스템 여유 `free − actual < min_free_bytes` 면
     `failed(disk_low)`. copier 가 하나뿐이라 예약은 필요 없다.
  6. 파일 목록(`path · size · sha256 · type · expanded_from?`)을 계산해 `manifest.json` 으로 쓰고, 폴더를
     `<shelf_dir>/<job>/` 로 `os.replace` 한다. 대상 폴더가 이미 있으면 덮지 않고 `failed(dest_exists)`.
     그 뒤 행을 `active` 로, `shelved_at = last_used_at = now`, 잡 상태 · 끝난 시각을 다시 복사한다.
  7. 어떤 단계든 예외면 staging 을 지우고 `failed` + `reason_code`. 잡 상태와 묶음 상태는 건드리지 않는다.
- 보관은 잡의 성공 · 실패와 무관하다. 보관이 실패해도 잡이 실패가 되지 않는다(M5e 결정 41 과 같은 원칙).
- copier 스레드가 죽으면 `shelf_storage.error_code = "copier_dead"` 가 되고 `/api/health.shelf` 와 `rcm check`
  가 알린다(health 503 조건은 아니다).

### 4.3 시계 (결정 131·132)

항목마다 `last_used_at` 하나가 시계다. 순수 함수 `core/shelf.py`:

```
stage(last_used_at, now, stale_days, delete_days) ->
  "active"  if now - last_used_at <  stale_days
  "stale"   if stale_days ≤ now - last_used_at < delete_days
  "delete"  if now - last_used_at ≥ delete_days
```

「쓴다」(시계를 다시 돌림)의 정의:

| 행동 | 쓴다? |
|---|---|
| **보관 중** 항목 페이지 `GET /shelf/N/` 열기 | 쓴다(그 페이지가 그림을 띄우므로 연 것은 본 것이다) |
| 파일 받기 `GET /shelf/N/f/<path>` — 200 이든 304 든 | 쓴다 |
| zip 받기 | 쓴다 |
| 「되살리기」 `POST /shelf/N/restore`, `rcm keep N`(이미 있는 항목) | 쓴다 |
| **정리 대기** 항목 페이지 열기 | 안 쓴다. 이 페이지는 그림 격자 없이 파일 표와 「되살리기」만 보인다(지울지 판단하려고 보는 것이 되살리기가 되면 안 된다) |
| 목록 `/shelf/`, `/shelf/latest/…` 리다이렉트, JSON `/api/shelf…`, HEAD | 안 쓴다 |

- 판정은 라우터가 HEAD 를 GET 으로 바꾸기 전의 `self.command` 로 한다.
- 기록 횟수 줄이기: `last_used_at` 은 이전 값보다 **1시간 이상** 늦을 때만 쓴다. 정리 대기 항목은 첫 쓰기에
  바로 `active` 로 바뀐다(1시간 규칙 없음).
- 상태 열(`active` · `stale`)은 청소기가 회차마다 `stage()` 로 다시 맞춘다. 화면은 `stage()` 를 그 자리에서
  계산해 보인다(청소 회차 사이의 어긋남을 없앤다).
- 항목마다 `stale_at = last_used_at + stale_days`, `delete_at = last_used_at + delete_days` 를 계산해 보인다.

### 4.4 tar 펼치기 (결정 133)

묶음 안의 `*.tar` · `*.tar.gz` · `*.tgz` 파일은 보관할 때 **한 단계만** 옆 폴더 `<이름>.d/` 로 펼친다.
원본 파일도 남긴다. golden-update 처럼 PNG 를 tar.gz 로 내는 프리셋이 손대지 않고도 그림을 보여 주게 하려는 것이다.

- `tarfile.open(mode="r|*")` 스트림을 멤버 단위로 돌며(랜덤 접근 금지) 읽은 바이트를 센다. 일반 파일과
  디렉터리만, `safe_member`, 절대 경로 · 링크 · 장치 멤버는 거절한다.
- 상한은 **항목 전체** 합이다. 펼친 바이트 합 ≤ `max_artifact_bytes`, 펼친 파일 수 ≤ `max_artifact_files`
  (M5e 키 재사용). 넘는 순간 그 tar 의 펼침을 지우고 원본만 남긴다. manifest 의 `expand_skipped` 에 이유를 적는다.
- 펼친 안에 또 tar 가 있어도 펼치지 않는다. zip 은 펼치지 않는다.

### 4.5 정리 (결정 134·135)

청소기(`Janitor`) 회차 안, `_sweep_bundles` 다음에 `_sweep_shelf(now)` 를 돈다.

1. `active` 인데 `stage = stale` → `stale`, `stale_since = now`. 이벤트를 하나 남긴다.
2. `stage = delete` → 폴더를 지운다. 삭제는 `shelf_dir.resolve()` 를 기준 루트로 받는 변형을 쓴다(기존
   `_remove_tree` 는 data_dir 밖이면 EXDEV 로 거절한다). 심볼릭 링크는 링크만 지우고, 기준 루트 밖을 가리키면
   손대지 않는다. **물리적으로 지운 뒤에** 행을 `deleted`, `deleted_at = now`, 바이트 0 으로 바꾼다. 지우다
   실패하면 행은 그대로 두고 `error_code` 를 남긴 뒤 다음 회차에 다시 한다.
3. `deleted` 행(묘비)은 30일 뒤 지운다. 그동안 링크를 연 사람에게 「언제 지워졌다」를 말한다.
4. `failed` 행은 30일 뒤 지운다(staging 은 이미 없다).

- **손으로 지우기**: `DELETE /shelf/N`(admin Bearer), `rcm shelf rm N`, 그리고 **SPA 잡 상세의 보관함 줄**
  (fetch + Bearer, 토큰이 있을 때만 보인다). 서버가 그리는 페이지에는 지우기 버튼이 없다(토큰을 모르고, form 은
  DELETE 와 Bearer 를 보낼 수 없다).
- **zip 스트리밍과 삭제가 겹칠 때**: zip 응답은 시작할 때 그 항목에 hold 를 건다. 청소기와 손 삭제는 hold 가
  있으면 이번 회차를 건너뛴다(손 삭제는 409 `shelf_item_busy`).
- **잡 행보다 오래 산다**: `delete_old_jobs`(180일)는 잡 행을 지우지만 `shelf_items` 는 자기 열을 **복사해 들고
  있어서** 잡 행이 없어도 페이지가 선다. `shelf_items.job_id` 에 외래 키를 걸지 않는다. 잡 id 는 `AUTOINCREMENT`
  라 다시 쓰이지 않는다(DB 를 백업으로 되돌린 경우는 6번의 「대상 폴더가 있으면 덮지 않는다」가 막는다).
- 보관함 바이트는 M5g 의 `workspace_storage_max_bytes` · 바닥 규칙의 대상이 **아니다**(증거와 같은 대접이다).
  다만 보관함이 data_dir 과 같은 디스크면 보관함이 차지한 만큼 M5g 바닥 규칙이 워크스페이스를 더 일찍 지운다.
  문서에 적는다.

### 4.6 HTTP (결정 138·139·140·145·148)

| 경로 | 권한 | 응답 |
|---|---|---|
| `GET /shelf/` | 읽기 규칙(`_read_only_ok`) | 서버가 그리는 HTML 목록 |
| `GET /shelf/latest/<preset>?branch=` | 읽기 규칙 | 가장 최근 `active` 항목으로 303, 없으면 404 |
| `GET /shelf/N/` | 읽기 규칙 | HTML 항목 페이지 |
| `GET /shelf/N/f/<path>` | 읽기 규칙 | 파일 바이트 |
| `GET /shelf/N.zip` | 읽기 규칙 | 항목 전체 zip(무압축 STORED, 스트리밍, `Connection: close`) |
| `POST /shelf/N/restore` | 읽기 규칙 + 같은 출처 | 시계를 돌리고 303 → `/shelf/N/` |
| `DELETE /shelf/N` | admin Bearer | 즉시 삭제 |
| `GET /api/shelf?state=&preset=&limit=` | 읽기 규칙 | JSON 목록 |
| `GET /api/shelf/N` | 읽기 규칙 | JSON 항목(파일 목록 포함) |
| `POST /jobs/N/keep` | 그 잡의 로그 자격(Bearer) | §4.1 표 |

- **로그인 없는 읽기는 의도한 예외다(결정 138).** 묶음(M5e)은 `read_auth = none` 이어도 토큰을 요구하지만,
  보관함에는 **표시한 잡만** 들어오고 오너가 「공유기 안에서 링크로」를 골랐다. `read_auth = "basic"` 이면
  보관함도 basic 을 요구한다(같은 `_read_only_ok`). 문서에 「보관함에 넣은 파일은 그 네트워크의 누구나, 그리고
  Tailscale 로 이 머신에 닿는 누구나 본다. 비밀을 `artifacts` 에 넣지 마라」를 적는다.
- **Host 허용 목록(결정 148)**: `/shelf*` 와 `/api/shelf*` 는 `Host` 가 `localhost` · `*.local` · 사설 IP
  (10/8 · 172.16/12 · 192.168/16 · 100.64/10 · fc00::/7 · 127/8) · `public_url` 의 호스트 · 서버의 hostname 일
  때만 답한다. 아니면 421. 외부 웹페이지가 DNS rebinding 으로 보관 파일을 읽는 길을 막는다.
- **되살리기의 같은 출처 확인(결정 145)**: 쓰기는 원래 Bearer 만 받는다(CSRF 방지). 되살리기는 시계만 미는
  일이라 토큰 없이 받는다. 대신 `Origin == "http://" + Host` 일 때만 받는다. `Origin` 이 없거나 문자열
  `null` 이면 `Referer` 의 scheme+host 를 같은 방식으로 본다. 둘 다 없으면 403. 보관 HTML 페이지는
  `Referrer-Policy: same-origin` 으로 보내서 브라우저가 form POST 에 Referer 를 싣게 한다.
- **잡이 만든 파일을 같은 출처에서 내보내는 위험(결정 139)**: 웹 UI 의 토큰이 같은 출처
  (`localStorage["rcm.token"]`)에 있다. 그래서 `/shelf/N/f/…` 응답은 언제나 다음을 단다.
  - `Content-Security-Policy: sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'`.
    `allow-same-origin` 이 없으니 문서가 불투명 출처가 되어 저장소를 못 읽는다.
  - `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private, max-age=300`.
  - 인라인은 **허용 목록만**: `image/png` · `image/jpeg` · `image/gif` · `image/webp`, 그리고 `.txt` · `.log` ·
    `.json` · `.md` · `.csv` 를 `text/plain; charset=utf-8` 로. 그 밖(`.html` · `.svg` · `.js` · `.pdf` 포함)은
    `attachment` + `application/octet-stream`.
  - `Content-Disposition` 의 파일 이름은 `filename*=UTF-8''<퍼센트 인코딩>` 과, 비 ASCII · 제어문자 · 따옴표 ·
    역슬래시를 `_` 로 바꾼 ASCII `filename=` 만 쓴다. `send_header` 는 CRLF 를 검사하지 않고 latin-1 로만
    인코딩하므로, 원래 이름을 헤더에 그대로 넣으면 한글 이름은 500, 개행이 든 이름은 헤더 주입이 된다.
- **경로**: `<path>` 는 퍼센트 디코딩 **뒤에** `safe_member` 를 다시 적용하고, 해석한 실제 경로가
  `<shelf_dir>/<N>/files/` 안인지 `resolve()` 로 확인한다. 심볼릭 링크는 따라가지 않는다. manifest 에 없는 경로는
  404 다. manifest 는 (job, mtime) 으로 메모리에 캐시한다.
- `safe_member(path)` = `core/artifacts.check_path` + C0 제어문자(0x00–0x1F, 0x7F) 거절 + `.git` 조각 거절.
  제어문자가 든 멤버는 보관할 때부터 거절한다.
- **크기와 시간**: 파일은 64 KiB 청크로 스트리밍하고 `Content-Length` 를 단다. Range 는 하지 않는다(결정 140).
  `ETag` 는 그 파일의 sha256 앞 16자이고 `If-None-Match` 가 맞으면 304 다. 쓰기 시한은
  `artifact_transfer_timeout_seconds` 를 쓴다.
- **전송 슬롯(결정 148 의 둘째 줄)**: zip 과 8 MiB 를 넘는 단일 파일은 **보관함 전용 세마포어**(4개, 상수)를
  비대기로 잡는다. 없으면 503 + `Retry-After: 5`. M5e 의 `transfer_slots` 는 **쓰지 않는다**. 원격 워커의 묶음
  업로드가 같은 슬롯을 비대기로 잡고 503 을 다시 시도하지 않아서, 보관함 내려받기가 그 슬롯을 차지하면 keep
  잡의 묶음이 원격에서 통째로 사라진다. 작은 파일과 그림은 일반 요청 슬롯(32)만 쓴다.
- 없는 항목 404, 묘비 410(`deleted at …`). `pending` · `copying` 이면 항목 페이지는 「보관하는 중」을 보이고
  파일 경로는 409 다. 보관함 폴더가 `unavailable`(§5.3)이면 503.

### 4.7 페이지 (결정 141)

- 서버가 그리는 HTML 이다(템플릿 문자열, 스크립트 없음). 폰에서 바로 뜨고 토큰이 필요 없다. 언어는 한국어가
  기본이고 `?lang=en` 이면 영어다. 시각은 `[display] timezone` 으로 보인다.
- 이 페이지들의 헤더: `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline';
  form-action 'self'; frame-ancestors 'none'`, `Referrer-Policy: same-origin`.
- **모든 링크는 절대 경로**다: `/shelf/<N>/f/<quote(path, safe='/')>`. 라우터가 끝 `/` 를 떼므로 상대 경로는
  깨진다. Host 로 만든 절대 URL 은 페이지에 넣지 않는다. 화면에 보이는 문자열은 전부 HTML 이스케이프한다.
- 목록: 두 칸(보관 중 · 정리 대기). 행은 `#N · 프리셋 · 입력 요약 · 브랜치 · 끝난 시각 · 파일 수 · 크기 ·
  마지막 사용 · 정리 예정일` 이다. 위에 프리셋 필터 링크를 둔다. 정리 대기 칸의 행에는 「되살리기」 버튼을 둔다.
  맨 아래 「최근 지움(30일)」 목록. 머리에 사용량 `12.3 GB / 50 GB`.
- 보관 중 항목: 잡 정보(잡 행이 있으면 `/#/jobs/N` 링크), 상태와 날짜, 「zip 으로 받기」, 그림 격자(허용 목록의
  이미지를 `width` 로 줄여 보인다. 서버가 썸네일을 만들지 않는다), 파일 표(경로 · 크기 · 받기). 펼친 `*.d/`
  는 원본 tar 바로 밑에 묶어 보인다.
- 정리 대기 항목: 그림 격자 없이 파일 표와 「되살리기」, 「YYYY-MM-DD 에 지워집니다」.
- 기존 웹 UI(해시 SPA): 잡 상세의 산출물 줄에 「보관함 ↗」 링크(보관된 잡만)와, 토큰이 있으면 「보관함에서
  지우기」 버튼. 머리 nav 에 「보관함」 링크(보관함이 켜져 있을 때).

### 4.8 CLI · 알림 · 상태 (결정 142·143·144)

- `rcm run … --keep`: §4.1 의 옛 서버 감지를 거친 뒤 제출 본문에 `keep: true` 를 싣는다. 잡이 끝나면
  `GET /jobs/N` 의 `shelf` 를 본다.
  - `active` 면 `shelf: <url>`.
  - `pending` · `copying` 이면 최대 60초 기다린다. 그래도 안 끝나면 `shelf: <url> (still copying)`.
  - `none` 이면 `shelf: nothing to keep — <묶음 상태>`(예: `artifacts_on=failure` 인데 성공).
  - `failed` 면 `shelf: not kept — shelf_full` 같은 한 줄.
  - 어느 경우든 종료 코드는 잡의 것 그대로다. `--json` 은 `shelf{state,url,reason_code}` 를 싣는다.
    `--no-wait` 와 함께면 제출 응답의 `shelf_url` 만 찍는다.
- `rcm keep N [--json]`: §4.1 의 표. 성공이면 URL 을 찍는다.
- `rcm shelf [--state active|stale|all] [--preset P] [--json]`: 목록. `rcm shelf rm N`: admin.
- URL 의 앞부분은 잡 링크와 같은 `App.base_url`(public_url > 요청 Host > `server_base_url`)이다(결정 120).
- 알림: keep 인 잡의 알림 환경에 `RCM_SHELF_URL`(정해진 URL — 보관이 아직 안 끝났어도)을, 웹훅 본문에
  `shelf_url` 을 싣는다. keep 이 아닌 잡은 빈 값이고 웹훅에는 키가 없다.
- `GET /jobs/N` 과 대기열 · 최근 행: `keep: bool`, `shelf: {state, url, reason_code} | null`. `state` 는
  `pending | copying | active | stale | failed | deleted | none`. keep 이 아니면 `null`. keep 인데 묶음이 ready
  가 아닌 채 끝났으면 `none` 과 묶음 상태를 이유로 싣는다.
- `/api/status` 의 `server.shelf_storage = {enabled, stored_bytes, limit_bytes, items: {active, stale, pending,
  failed}, last_sweep_at, error_code}`. **폴더 경로는 싣지 않는다**(홈 경로가 드러난다). `/api/health.shelf` 는
  같은 모양의 요약이고 정보일 뿐 503 조건이 아니다. 이 키가 있다는 것이 「서버가 keep 을 안다」의 신호다.
- `rcm check` 행: `shelf  ok  3 active · 1 stale · 12.3 GB / 50 GB · http://…/shelf/`. 90% 를 넘으면 warn,
  `unavailable` · copier 죽음 · 폴더에 못 씀은 FAIL, 꺼져 있으면 `shelf  off`. 서버 머신에서 `--config` 로 돌면
  `shelf_dir` 경로와 행 없는 폴더 수(`orphans 2`)도 보인다.

## 5. 저장

### 5.1 DB v24

```sql
ALTER TABLE jobs ADD COLUMN keep INTEGER NOT NULL DEFAULT 0;
CREATE TABLE shelf_items (
  job_id INTEGER PRIMARY KEY,          -- 외래 키 없음 (잡 행보다 오래 산다)
  state TEXT NOT NULL,                 -- pending | copying | active | stale | failed | deleted
  preset TEXT NOT NULL, inputs_json TEXT, repo TEXT, branch TEXT, sha TEXT, source_mode TEXT,
  requester TEXT, job_state TEXT, job_finished_at REAL,
  requested_at REAL NOT NULL,
  shelved_at REAL, last_used_at REAL, stale_since REAL, deleted_at REAL,
  file_count INTEGER, total_bytes INTEGER, reason_code TEXT, reason_args TEXT,
  error_code TEXT
);
CREATE INDEX shelf_items_state ON shelf_items(state, last_used_at);
CREATE INDEX shelf_items_latest ON shelf_items(preset, branch, shelved_at);
```

- `_SCHEMA_V1`(새 DB)과 `_MIGRATIONS[24]` 두 곳에 같은 DDL 을 둔다. 업그레이드는 `backup/rcm.sqlite3.v23.bak`
  을 남긴다. **v24 를 정하기 전에 origin/dev 의 `DB_VERSION` 을 다시 확인한다**(형제 세션과 번호가 겹치지 않게).
- `DB_VERSION == 23` 을 잠근 테스트 네 곳(`test_job_link` · `test_store_migration_releases` · `test_store_m5n` ·
  `test_store_versions`)을 24 로 바꾼다. `tests/test_store_m5p.py` 에 v23 → v24 시험을 둔다(m5n 선례).
- 파일 목록은 DB 가 아니라 `<shelf_dir>/<N>/manifest.json` 에 둔다(펼친 tar 로 수천 줄이 될 수 있다).

### 5.2 폴더

```
<shelf_dir>/
  .rcm-shelf                   -- 처음 만들 때 쓰는 표식 (§5.3)
  .staging/<job>.<hex>/        -- 푸는 중 (기동 때 비운다)
  412/
    manifest.json              -- rcm 이 쓴다: {job, files:[{path,size,sha256,type,expanded_from?}], expand_skipped:[…]}
    files/build/.golden-update/goldens.tar.gz
    files/build/.golden-update/goldens.tar.gz.d/test/feature/home/goldens/home.png
    files/build/.golden-update/manifest.json
```

- `files/` 아래만 잡이 만든 것이라 rcm 의 `manifest.json` 과 부딪히지 않는다. staging 이 `shelf_dir` 안에 있어서
  `os.replace` 는 늘 같은 파일 시스템 안에서 일어난다.
- 폴더 이름은 잡 번호만 쓴다(프리셋 이름은 바뀔 수 있다).

### 5.3 기동 화해

`App.start` 가 레인 · 청소기 · copier 보다 **먼저** `reconcile_shelf_on_start(store, cfg)` 를 부른다. 시험은
`App.start` 를 거쳐 확인한다. `_offline_gc`(`rcm gc --dry-run --config`), `Janitor` 생성자, `Store` 생성자에서는
부르지 않는다(dry-run 이 실제 보관함 폴더를 고치면 안 된다).

- **표식 확인**: `<shelf_dir>/.rcm-shelf` 가 없는데 `shelf_items` 에 active · stale 행이 있으면 보관함을
  `unavailable`(error_code `shelf_dir_missing`)로 둔다. 행을 건드리지 않고 새 보관도 하지 않는다. 다른 디스크가
  안 붙은 채 기동해서 색인을 다 잃거나 빈 마운트 지점 아래 루트 디스크에 쓰는 일을 막는다. 행이 하나도 없으면
  표식을 새로 쓴다.
- `.staging` 을 비운다.
- `copying` 행: `<shelf_dir>/<job>/manifest.json` 이 유효하면 `active` 로 입양한다(`os.replace` 뒤, 커밋 전에
  죽은 경우). 아니면 묶음이 살아 있을 때 `pending` 으로 되돌리고, 없으면 `failed(bundle_gone)`.
- `active` · `stale` 인데 폴더가 없으면 `failed(missing)` + 이벤트.
- **행이 없는 폴더는 지우지 않는다**(모르는 것은 안 지운다). `rcm check --config` 가 `orphans N` 으로 알린다.

**기존 문제(이 마일스톤에서 고친다)**: M5e 의 `reconcile_bundles_on_start` 는 테스트에서만 불리고 `App.start`
가 부르지 않는다. 그래서 「기동 때 묶음 staging 을 비우고 행 없는 숫자 폴더를 지운다」가 운영에서 한 번도 돌지
않았다. P2 에서 `App.start` 가 이것도 부르게 하고 §13 에 적는다.

### 5.4 다른 청소기와의 경계

- `shelf_dir` 은 `data_dir` 의 다른 청소 대상 폴더 안에 둘 수 없다(§3.1).
- `delete_old_jobs` 가 `job_artifacts` 행을 지워도 보관함은 이미 자기 복사본을 가진다.
- `rcm gc` 는 부피 청소만 돌리고 보관함을 건드리지 않는다. `rcm gc --dry-run` 출력에 보관함 한 줄(정리 대기 수 ·
  삭제 예정 수)을 더한다.

## 6. 인터페이스 계약 (병렬 구현의 경계)

세 PR 이 서로 기다리지 않도록 아래 이름과 모양을 먼저 고정한다.

**`core/shelf.py`(순수, P1)**
- `SHELF_STATES = ("pending","copying","active","stale","failed","deleted")`, 공개 상태에 `"none"` 을 더한
  `PUBLIC_SHELF_STATES`.
- `stage(last_used_at: float, now: float, stale_days: int, delete_days: int) -> Literal["active","stale","delete"]`
- `due_dates(last_used_at, stale_days, delete_days) -> tuple[float, float]`
- `should_touch(prev_last_used: float | None, state: str, now: float) -> bool`
- `touches(method: str, route: Literal["item","file","zip","restore","keep","list","latest","api"], item_state: str) -> bool`
  (§4.3 표)
- `plan_sweep(items, now, stale_days, delete_days, tombstone_days=30) -> SweepPlan(to_stale, to_delete, to_forget)`
- `inline_disposition(path: str) -> tuple[str, bool]` — (content type, inline 여부)
- `content_disposition(path: str, inline: bool) -> str` — §4.6 의 `filename*` 규칙
- `safe_member(path: str) -> bool`
- `expand_candidates(paths) -> list[str]`
- `name_collisions(paths) -> list[tuple[str, str]]` — 정렬 키, O(n log n)
- `capacity_verdict(stored, actual, limit, free, min_free) -> str | None` — `None | "shelf_full" | "disk_low" | "disabled"`
- `host_allowed(host: str, *, public_host: str | None, hostname: str) -> bool` — 결정 148
- `same_origin(host: str, origin: str | None, referer: str | None) -> bool` — 결정 145

**`store.py`(P1)**
- `Job.keep`, `create_job(..., keep)` · `create_job_with_submission(..., keep)`,
  `join_or_bump(..., keep)` · `join_or_bump_with_submission(..., keep)`(합류 트랜잭션 안에서 켬),
  supersede 트랜잭션의 `new.keep |= old.keep`.
- `_upsert_bundle` 이 ready 직후 `keep=1` 이면 `_request_shelf_tx(conn, job_id, now, enabled)`.
- `Store.set_job_keep(job_id, now, *, enabled) -> Literal["armed","requested","retouched","retried","no_bundle","gone"]`
- `Store.ack_bundle(...)` 의 결과에 `deferred: bool`. `Store.bundles_due` 가 pending · copying 보관 묶음을 뺀다.
- `Store.claim_shelf_item(now) -> ShelfItem | None` — pending → copying 조건부
- `Store.finish_shelf_item(job_id, *, ok, file_count, total_bytes, reason_code, now)` — ok 면 잡 상태 · 끝난 시각 재복사
- `Store.touch_shelf_item(job_id, now) -> bool` — `should_touch` 를 써서 쓸 때만 쓴다
- `Store.shelf_items(state=None, preset=None, limit=200)`, `Store.shelf_item(job_id)`,
  `Store.latest_shelf_item(preset, branch) -> ShelfItem | None`
- `Store.mark_shelf(job_id, state, now, **fields)`, `Store.forget_shelf(job_id)`, `Store.shelf_totals() -> dict`
- `ShelfItem` 데이터클래스(`core/model.py`): §5.1 의 열 그대로

**설정(P1)**: `ServerConfig.shelf_dir: Path`, `shelf_max_bytes: int`, `shelf_stale_days: int`,
`shelf_delete_days: int`, `Preset.keep: bool`, 파생 `shelf_enabled`.

**청소기 · 화해(P1)**: `Janitor._sweep_shelf(now)`, 기준 루트를 받는 삭제 함수,
`reconcile_shelf_on_start(store, cfg) -> ShelfStartup(available: bool, error_code: str | None)`.

**서버(P2)**: `ShelfCopier`, copier 깨우기, ack 미루기 연결, §4.6 경로 전부, 보관함 전송 세마포어, §4.7 서버
HTML, `POST /jobs` 의 `keep` 과 응답 `shelf_url`, `POST /jobs/N/keep`, `GET /jobs/N` 과 행의 `keep` · `shelf`
(`core/status.py`), `status.server.shelf_storage`, `health.shelf`, `App.start` 의 두 화해 호출.

**CLI · 웹 SPA · 알림 · 문서(P3)**: `rcm run --keep`(health 감지 포함), `rcm keep`, `rcm shelf`, `rcm check` 행,
SPA 링크와 지우기 버튼, `RCM_SHELF_URL`, 웹훅 `shelf_url`, 문서 전부.

## 7. 테스트 — 반드시 들어갈 경우

역할마다 시나리오 문서가 표를 넓힌다. 여기는 빠지면 안 되는 것만 적는다.

### P1 — 규칙 · 저장 · 청소
- 시계 경계: `last_used + 182d − 1s` 는 active, `+182d` 는 stale, `+365d − 1s` 는 stale, `+365d` 는 delete.
- 쓰기 줄이기: 59분 뒤 touch 는 안 쓰고 61분 뒤는 쓴다. stale 이면 1분 뒤라도 쓴다(→ active).
- `touches` 표 전부(§4.3), HEAD 는 안 씀.
- 정리: stale 표시는 회차마다 한 번, 삭제는 **폴더를 지운 뒤에만** 행 갱신, 지우기 실패면 행 그대로 ·
  `error_code`, 심볼릭 링크, 밖을 가리키는 링크, data_dir 밖 `shelf_dir`(EXDEV 회귀).
- 설정 오류: `delete ≤ stale`, `stale < 1`, `shelf_dir` 이 data_dir/artifacts 안, `keep` 인데 `artifacts` 없음.
- 마이그레이션 v23 → v24(백업 · 기본값 · 이미 있는 열), 새 DB 와 업그레이드 DB 의 스키마가 같은지
  (`sqlite_master` 를 실제로 비교).
- 잡 행이 180일로 지워져도 보관 항목이 산다.
- `_upsert_bundle` 이 로컬과 원격(publish_bundle) 두 길 모두에서 행을 세운다. 원격은 잡이 running 일 때 세우고,
  active 가 될 때 잡 상태 · 끝난 시각이 채워진다.
- `set_job_keep` 여섯 갈래, 그리고 **잡 종료와 경쟁**: 「아직 안 끝남」 판정과 finish 커밋 사이에 끼어도 행이
  빠지지 않는다(한 트랜잭션).
- 합류 keep 단조, supersede 가 keep 을 넘김.
- ack 미루기: `ack_bundle` 이 pending 보관 묶음에 `deferred = True`, `bundles_due` 가 뺌, 안전판 TTL + 24h.
- 이름 충돌(정렬 키), `safe_member` 의 제어문자 · `.git`.
- 기동 화해: 표식 없음 → unavailable · 행 불변, staging 비움, copying 입양 · 되돌림 · bundle_gone,
  폴더 없는 행 → missing, 행 없는 폴더는 안 지움, `_offline_gc` 는 화해를 부르지 않음.

### P2 — 서버 · HTTP · 보안
- 베스트: keep 프리셋 잡(로컬 레인) → 끝 → 보관 active → `/shelf/N/` 200, 그림 인라인, zip 내용 = manifest.
- **원격 워커** 잡도 같은 결과(실제 `RemoteWorker` → 서버 업로드 → finish 경로. 손으로 만든 payload 는 금지,
  M5g §13 D).
- **ack 경쟁**: 로컬 잡이 ready 되자마자 소유자가 받고 ack → 보관이 끝난 뒤에 묶음이 지워진다. 보관이 실패해도
  ack 된 묶음은 결국 지워진다. 안전판.
- **전송 슬롯**: 보관함 zip 4개가 도는 중에도 원격 워커의 묶음 업로드가 성공한다.
- tar 펼치기: golden-update 모양(tar.gz 안 PNG) → `.d/` 에 PNG, 원본 유지. 폭탄(헤더는 작고 실제는 큼 · 파일 수
  초과) → 안 펼침과 이유. 펼친 안의 tar 는 안 펼침. 묶음과 펼침을 합친 대소문자 충돌.
- 보안: `..` · `%2e%2e` · `%2f` · 절대 경로 · NUL · 개행 · 심볼릭 링크 · manifest 밖 경로 → 400/404.
  `.html` · `.svg` · `.pdf` 는 attachment + octet-stream, 모든 파일 응답에 `sandbox` CSP 와 nosniff.
  한글 이름 · 개행이 든 이름의 `Content-Disposition`(500 없음 · 주입 없음). 이름이 `<img src=x onerror=…>` ·
  `?` · `#` 인 파일의 페이지 렌더링(이스케이프 · 절대 링크). Host 가 허용 목록 밖이면 421.
- 되살리기: **실제 브라우저**(CDP 하네스)로 버튼을 눌러 303. Origin 다름 → 403, Origin `null` + Referer
  같음 → 303, 둘 다 없음 → 403.
- 시계: §4.3 표를 HTTP 로 한 번씩(304 는 씀, HEAD · 목록 · JSON 은 안 씀, 정리 대기 페이지는 안 씀).
- 상한: `shelf_full` · `disk_low` · `shelf_max_bytes = 0` 이면 `failed(disabled)` 이고 기존 항목은 보인다.
- 합류 + keep, `rcm keep` 여섯 갈래의 HTTP 코드, `/shelf/latest/…` 303 · 404, zip 도중 삭제 → 409.
- `App.start` 가 두 화해를 부른다(M5e 묶음 화해 회귀 포함).
- 공개 키 잠금 갱신: `test_compat_m5e.py`(queue row · recent row · server block 의 `added`),
  `test_status_schema.py`(`ROW_KEYS` · `RECENT_KEYS`).

### P3 — CLI · 웹 · 알림 · 문서
- `rcm run --keep` 출력 네 갈래(active · still copying · none · failed), `--json`, `--no-wait`.
- 옛 서버(health 에 `shelf` 없음)에 `--keep` → 제출하지 않고 안내 · 종료 코드 2.
- `rcm keep` · `rcm shelf` · `rcm shelf rm` · `rcm check` 행(ok · warn 90% · FAIL · off).
- 알림 env `RCM_SHELF_URL` · 웹훅 `shelf_url`(keep 이 아닌 잡은 없음).
- SPA: 잡 상세의 보관함 링크와 지우기 버튼(토큰 있을 때만), nav 링크, i18n 두 언어 키 집합 잠금.
- 문서 잠금 테스트(`tests/test_docs_m5p.py`): configuration(서버 키 4 · 프리셋 `keep` · 보안 문단 · Host 허용
  목록) · usage 두 언어 · README 명령 표 · `examples/server.toml` · CHANGELOG.
- **완료 조건**: P2 가 머지된 뒤 P3 를 그 위로 다시 올리고, 실제 서버와 실제 CLI 를 잇는 끝단 시험을 하나 이상
  돌린다(응답 모양을 손으로 흉내 낸 시험만으로 끝내지 않는다 — 잠긴 키 집합이 버그를 숨긴 선례가 있다).

## 8. 실기 확인 (리드)

개발 인스턴스(포트 8790, `~/.local/share/rcm-devtest-m5p`, 알림 훅 없음, `retention_sweep_interval_seconds = 60`,
**운영 설정 · 데이터 금지**):
1. `keep = true` 프리셋으로 PNG 몇 장과 tar.gz 를 내는 잡을 돌린다. 폰(같은 Wi-Fi)에서 링크를 열어 그림을 본다.
2. 원격 워커(같은 머신의 두 번째 워커 프로세스)로 같은 잡을 돌린다.
3. 시계: 개발 DB 의 `last_used_at` 을 당긴 뒤 다음 청소 회차(60초)에서 stale, 다시 당겨 delete 를 본다.
4. `.html` · `.svg` · `.pdf` 가 내려받기로만 열리는 것, iPhone Safari 와 Mac Chrome 에서 되살리기 버튼이 303 인 것.

## 9. 안 만드는 것

- **SMB · AFP 공유**(오너 선택에서 뺐다). 폴더는 사람이 읽을 수 있는 모양이라 나중에 켜도 된다.
- **인터넷 공개 · 공유 링크 토큰.** 공유기 안과 Tailscale 전용이다.
- **썸네일 생성 · 이미지 변환 · PDF 인라인.**
- **Range · 이어 받기.**
- **보관 항목 편집**(파일 추가 · 이름 바꾸기). 잡이 만든 그대로 둔다.
- **프리셋별 보관 기간.** 시계는 서버 하나다.
- **zip 펼치기 · 두 단계 tar 펼치기.**
- **검색.** 지금 양(골든 두 달에 13건)에는 프리셋 필터와 `/shelf/latest/…` 로 충분하다.

## 10. PR 순서 · 완료 기준

| PR | 브랜치 | 만지는 곳 | 의존 |
|---|---|---|---|
| 0 | `docs/m5p-shelf-workplan` | 이 문서 · 시나리오 a/b/c | — |
| P1 | `feat/m5p-shelf-store-and-clock` | `core/shelf.py` · `core/model.py` · `store.py`(v24 · keep · ack · upsert) · `config.py` · `janitor.py` | 0 |
| P2 | `feat/m5p-shelf-server-pages` | `server.py`(copier · 경로 · HTML · 슬롯 · 화해 호출) · `core/status.py` · 공개 키 잠금 | P1 |
| P3 | `feat/m5p-shelf-cli-web-docs` | `cli.py` · `client.py` · `notify.py` · `core/notify.py` · `web/*` · 문서 | P1 (P2 와 병렬, 끝단 시험은 P2 위에서) |

완료 기준 — PR 마다:
1. 시나리오 문서 → 실패하는 테스트 → 구현 → `ruff check . && ruff format --check . && pytest` ·
   `node --test tests/web/*.test.js`(웹) · `python scripts/mutcheck.py` 가 모두 통과한다.
2. mutcheck 에 핵심 변이를 둘 이상 더한다. P1: 삭제 뒤 표시 순서 뒤집기 · 1시간 규칙 제거 · ack 미루기 제거.
   P2: sandbox CSP 제거 · 경로 디코딩 뒤 검사 제거 · 보관함이 M5e 슬롯을 씀. P3: health 감지 제거 · 알림 env 빠짐.
3. **격리 에이전트 검증**: 구현한 에이전트가 아닌 새 에이전트가 이 문서 · 시나리오 · diff 만 보고 표를 실제로
   돌린다. 찾은 결함은 구현 에이전트가 고치고 다시 검증한다.
4. dev 머지 뒤 **v0.7.0**(새 개념 · DB v24 — 마이너). 운영 반영은 기존 절차(DB 백업 `backup/rcm.sqlite3.v23.bak`
   확인), 그 뒤 운영 golden-update 에 `keep = true`(§11).

## 11. 이 마일스톤 밖

- 운영 golden-update 에 `keep = true` — 오너가 첫 대상으로 고른 것이므로 v0.7.0 반영 **뒤에** rcm 세션이 넣는다
  (백업 · `rcm reload --dry-run`). 그때 프리셋이 include 파일로 옮겨 가 있으면 dolomood 정본 PR 로 한다.
- dolomood 정본(`scripts/rcm/presets.dolomood.toml`)과 `check_presets.py` 가 `keep` 을 알게 하는 일 — 그쪽 세션.
- `public_url` 은 비워 둔다(오너 결정 2026-11-19). 링크는 `http://<host>.local:8787` 이다.

## 12. 결정 (125~148 · 추천값으로 구현, 확인 대기)

| # | 무엇 | 결정 |
|---|---|---|
| 125 | 새 개념 | **보관함(shelf)**. 결정 58(묶음은 통로)은 그대로 두고, 표시된 잡의 묶음을 복사해 오래 두는 **두 번째 자리**를 만든다. 묶음의 24시간 · ack 삭제는 바뀌지 않는다(보관 중일 때 미루기만 한다) |
| 126 | 무엇이 들어가나 | 프리셋 `keep = true`, 제출 `keep: true`(`--keep`), 뒤늦게 `rcm keep N`(묶음이 살아 있을 때) |
| 127 | keep 을 얼리는 곳 | 제출 때 `jobs.keep`(DB v24). reload · 재시작과 무관하다 |
| 128 | 합류 · supersede 와 keep | 합류 제출의 keep 은 기존 잡을 켠다(끄지 않는다). supersede 는 옛 잡의 keep 을 새 잡에 넘긴다 |
| 129 | 복사 시점 | 묶음을 ready 로 쓰는 `_upsert_bundle` 안에서 `pending` 행을 세우고, 보관 스레드 하나가 비동기로 푼다. 잡 상태는 active 가 될 때 다시 복사한다 |
| 130 | ack 경쟁 | store 의 ack 트랜잭션 안에서 미룬다. TTL 청소도 뺀다. 안전판은 TTL + 24h |
| 131 | 시계 | 항목마다 `last_used_at` 하나. 182일이면 정리 대기, 365일이면 삭제. 두 값은 서버 설정이다 |
| 132 | 「쓴다」의 뜻 | 보관 중 항목 페이지 열기 · 파일 받기(304 포함) · zip · 되살리기 · `rcm keep`. 정리 대기 항목 페이지 · 목록 · JSON · HEAD 는 아니다. 정리 대기 페이지에는 그림이 없다. 기록은 1시간에 한 번 |
| 133 | tar 펼치기 | 묶음 안 `.tar` · `.tar.gz` · `.tgz` 를 한 단계 `<이름>.d/` 로 펼친다. 상한은 항목 전체 합(M5e 키), 스트림으로 세며 푼다 |
| 134 | 삭제 순서 | 폴더를 지운 뒤에 행을 `deleted` 로. 실패하면 다음 회차 |
| 135 | 묘비 | 지운 항목은 30일 동안 410 과 지운 날 |
| 136 | 상한 | `shelf_max_bytes` 50 GiB. 다 만든 뒤 실측 바이트로 판정하고, 넘으면 새 보관을 거절한다(남의 것 안 쫓아냄). `0` = 꺼짐(기존 항목은 남는다) |
| 137 | 여유 바닥 | 기존 `min_free_bytes` 를 `free − actual` 로 검사한다. 보관함은 M5g 부피 청소의 대상이 아니다 |
| 138 | 로그인 없는 읽기 | 의도한 예외(오너 선택). `read_auth = basic` 이면 basic. 「비밀을 artifacts 에 넣지 마라」를 문서화 |
| 139 | 같은 출처 위험 | 파일 응답은 `sandbox` CSP · nosniff. 인라인은 그림 · 텍스트 허용 목록만(PDF 제외). 파일 이름은 `filename*` 와 안전한 ASCII 로만 |
| 140 | Range | 안 한다 |
| 141 | 페이지 | 서버가 그리는 HTML, 스크립트 없음, 한국어 기본 · `?lang=en`, 절대 경로 링크, `Referrer-Policy: same-origin` |
| 142 | 링크 앞부분 | 잡 링크와 같은 `base_url`(결정 120) |
| 143 | `rcm run --keep` | health 의 `shelf` 키로 옛 서버를 감지한다. 끝나면 보관 상태를 보고 60초까지 기다린다. 묶음이 없으면 `none` 을 바로 말한다 |
| 144 | 알림 | `RCM_SHELF_URL` · 웹훅 `shelf_url` |
| 145 | 되살리기 권한 | 토큰 없이, `Origin`(또는 `Referer`)의 host 가 요청 `Host` 와 같을 때만. 둘 다 없으면 403 |
| 146 | 손으로 지우기 | admin 토큰만. `DELETE /shelf/N` · `rcm shelf rm` · SPA 잡 상세 버튼. 서버 HTML 에는 버튼이 없다 |
| 147 | 고정 링크 | `/shelf/latest/<preset>?branch=` → 가장 최근 active 항목으로 303 |
| 148 | 노출 줄이기 | `/shelf*` 는 Host 허용 목록(로컬 · 사설 · Tailscale · public_url · hostname) 밖이면 421. zip 과 8 MiB 넘는 파일은 보관함 전용 전송 슬롯(4)을 쓰고 M5e 슬롯은 쓰지 않는다 |

## 13. 구현하며 바뀐 것

(구현과 격리 검증 뒤 채운다.)

## 14. 설계 검토가 바꾼 것 (초안 → 이 판)

| 검토 | 초안 | 이 판 |
|---|---|---|
| P0-1 | 원격은 `_record_disposition` 에서 행을 세움 | `_upsert_bundle` 하나에서. 잡 상태는 active 때 재복사 |
| P0-2 | `set_job_keep` 은 0→1 만 | 한 트랜잭션에서 판정하는 여섯 갈래, 합류 · 생성에 `keep` 인자 |
| P0-3 | 옛 서버는 400 을 낸다고 가정 | 옛 서버는 무시한다. health `shelf` 키로 감지 |
| P0-4 | zip 이 M5e 전송 슬롯을 같이 씀 | 보관함 전용 슬롯 |
| P0-5 | 서버 HTML 에 지우기 버튼 | SPA 에만 |
| P0-6 | Origin 만 비교 | Origin 또는 Referer 를 Host 와, `Referrer-Policy: same-origin`, 실제 브라우저 시험 |
| P0-7 | 파일 이름을 헤더에 그대로 | `filename*` + 안전한 ASCII, 제어문자 멤버 거절 |
| P1-1 | 항목 페이지는 시계를 안 돌림 | 보관 중 항목은 돌림, 정리 대기 항목 페이지는 그림 없음 · 안 돌림, 304 도 돌림 |
| P1-2 · 3 · 4 | 화해 호출 위치 · 입양 · 표식 없음 | `App.start` 에서, copying 입양, `.rcm-shelf` 표식, M5e 화해 누락도 고침 |
| P1-5 | `_remove_tree` 그대로 | 기준 루트를 받는 삭제 |
| P1-6 · 7 · 8 | 서버 층 미루기 · 깨우기 미정 · CLI 헛기다림 | store 트랜잭션, `_publish_job` 이 깨움, `none` 상태 |
| P1-9 · 10 · 11 | supersede · failed 재시도 · 용량 모호 | keep 넘김, `retried`, 실측 바이트 |
| P1-12 · 13 · 14 · 15 | 큰 파일 · 충돌 · 상대 링크 · PDF | 전용 슬롯 · 합친 집합 충돌 · 절대 경로 · PDF 는 내려받기 |
| P1-16 | `rcm gc` 로 실기 · 잠금 목록 · 입력값 · include 순서 | 청소 주기 60초 · 잠금 목록 명시 · `inputs_json` · 「v0.7.0 뒤에만」 |
| 의견 | — | Host 허용 목록 · status 에 경로 안 실음 · 스트림 펼침 · manifest 캐시 · zip hold · `/shelf/latest` · 끝단 시험 |

## 15. 시나리오 문서가 물은 것 — 답 (구현은 이 절이 앞 절보다 이긴다)

시나리오 a · b · c 가 모두 74개를 물었다. 겹치는 것을 합쳐 답한다. 번호는 `A질문n` · `B질문n` · `C질문n` 이다.

### 15.1 keep 과 보관 행 (A1 · B1 · A3 · A11~14 · B2 · B8 · B11 · C10)

- **keep 이 켜지는 모든 길은 같은 트랜잭션에서 묶음을 본다.** `set_job_keep` · 합류 keep · supersede 의 keep 넘김이
  keep 을 켤 때, 같은 `BEGIN IMMEDIATE` 안에서 그 잡의 묶음이 이미 `ready` 면 `_request_shelf_tx` 로 행을 세운다.
  잡이 끝났는지는 보지 않는다(원격 잡은 묶음 ready 와 finish 가 다른 트랜잭션이다).
- `set_job_keep` 결과는 여덟 갈래다. HTTP 본문은 `{"job": N, "result": "<결과>", "shelf": {state,url,reason_code}}`.

  | 결과 | 조건 | HTTP | `rcm keep` 종료 |
  |---|---|---|---|
  | `armed` | 묶음이 아직 ready 가 아니고(없음 · pending · collecting · uploading) 잡이 안 끝났다 | 200 | 0 |
  | `requested` | 묶음 ready · 만료 60초 이상 남음 · ack 로 지워질 예정 아님. 행이 없거나 `deleted` 면 새로 `pending` | 202 | 0 |
  | `already_requested` | 행이 `pending` · `copying` | 200 | 0 |
  | `retouched` | 행이 `active` · `stale` → 시계를 돌린다(1시간 규칙 없음) | 200 | 0 |
  | `retried` | 행이 `failed` 이고 묶음 ready | 202 | 0 |
  | `no_bundle` | 묶음이 종결이지만 ready 가 아니다(empty · dropped · failed · skipped · 수집 안 함) 또는 프리셋에 artifacts 없음 | 409 | 1 |
  | `gone` | 묶음 purged · expired · unavailable, `expires_at` 이 60초 안, ack 되었고 합류자 0 | 410 | 1 |
  | `disabled` | 보관함 꺼짐(`shelf_max_bytes = 0`) 또는 `unavailable` | 409 / 503 | 1 |

  없는 잡은 404(종료 1). 옛 서버는 CLI 가 health 로 먼저 거른다(종료 2).
- **잡 상태 복사**: `Store.finish`(잡을 종료로 바꾸는 트랜잭션)가 그 잡의 보관 행이 있으면 같은 트랜잭션에서
  `job_state` · `job_finished_at` 을 갱신한다. copier 는 끝나지 않은 잡의 행도 집는다. `finish_shelf_item` 은 잡이
  이미 끝났을 때만 잡 상태를 복사한다. 그래서 순서와 상관없이 마지막 값이 맞다.
- **공개 상태에 `waiting` 을 더한다**: keep 인데 묶음이 아직 ready 가 아니고 종결도 아니면(잡이 도는 중이거나 원격
  묶음이 올라오는 중) `shelf.state = "waiting"`. `none` 은 묶음이 종결인데 ready 가 아닐 때만이다.
  `PUBLIC_SHELF_STATES = (waiting, pending, copying, active, stale, failed, deleted, none)`.
- **`none` 의 이유**: 묶음 행이 없는 경우도 정책으로 계산한다. `artifacts_on = "failure"` 인 성공 잡은
  `not_collected_on_success`, 글롭 없음은 `no_artifacts`, 그 밖은 묶음 상태 이름.
- 묘비 행이 30일 뒤 지워진 keep 잡: 묶음도 없으면 `none(forgotten)`.
- `_upsert_bundle` 은 `Store.shelf_enabled: bool`(기본 False, App 이 설정을 읽고 넣는다)과 `Store.shelf_available`
  을 본다. 꺼짐이면 `failed(disabled)`, unavailable 이면 `failed(unavailable)` 로 곧장 세운다(묶음을 붙잡지 않는다).

### 15.2 ack · TTL · 안전판 (A10~13 · B10)

- `ack_bundle` 의 `deferred` 는 「이 ack 가 원래 묶음을 지울 상황(소유자 · 합류자 0)」이고 보관 행이 pending ·
  copying 일 때만 True 다. 합류한 잡은 원래 ack 로 안 지우므로 False. ack 응답에 `deferred: true` 와
  `reason_code: "shelving"` 을 싣는다.
- 미뤄 둔 삭제: copier 가 `finish_shelf_item` 뒤 `Store.release_deferred_bundle(job_id) -> Path | None` 를 부른다.
  한 트랜잭션에서 「ack 됨 · 합류자 0 · pending/copying 보관 행 없음」이면 purged 로 표시하고 경로를 돌려준다.
  서버가 그 경로를 지운다.
- TTL 청소의 purge 갱신은 조건부다: `WHERE NOT EXISTS (pending · copying 보관 행)`. `bundles_due` 와 `set_job_keep`
  이 엇갈려도 묶음이 pending 행만 남기고 사라지지 않는다.
- 안전판은 청소기 `_sweep_shelf` 가 쓴다: `pending`(copying 은 아님) 행인데 묶음의 `ready_at +
  artifact_retention_hours + 24h` 가 지났으면 `failed(timeout)`. `finish_shelf_item` 은 `state = 'copying'` 일 때만
  바꾼다(조건부).

### 15.3 저장 · 청소 세부 (A2 · A4~9 · A15~19 · A23)

- 열 추가: `failed_at REAL`(실패 30일은 여기서 잰다). v24 DDL 은 `CREATE TABLE IF NOT EXISTS` ·
  `CREATE INDEX IF NOT EXISTS`.
- 시각은 epoch 초 REAL. `sha` 는 git_ref 면 커밋, tree 면 `tree_hash`(`source_mode` 로 구별). `requester` 는
  `jobs.requester` 그대로. 합계의 `pending` 은 copying 을 포함한다.
- `SweepPlan(to_stale, to_active, to_delete, to_forget)`. `to_active` 는 설정을 늘려 재시작한 경우의 되돌림.
- 「이벤트」는 `events` 표가 아니라 서버 로그 한 줄과 SSE 잡 갱신이다(잡 행보다 오래 사는 표를 만들지 않는다).
- 청소기는 보관함이 `unavailable` 이면 `_sweep_shelf` 를 통째로 건너뛴다. 삭제 직전 폴더가 없으면 `deleted` 가
  아니라 `failed(missing)`.
- hold: `Janitor(..., shelf_hold: Callable[[int], bool] = lambda _: False)`. 서버가 zip hold 집합을 넘긴다.
- `shelf_dir` 검증: 상대 경로는 `data_dir` 과 같은 규칙으로 푼다. resolve 뒤 비교한다. `data_dir` 과 같거나
  `data_dir` 의 상위 폴더면 설정 오류.
- copying 입양의 「유효한 manifest」: JSON 이 읽히고 `job` 이 맞고, 나열된 파일이 모두 있고 크기가 같다(해시는 안
  잰다). 폴더는 있는데 manifest 가 없거나 무효면 그 폴더는 이 행의 것이므로 지우고 묶음이 살아 있으면 pending,
  아니면 `failed(bundle_gone)`.
- 표식은 `shelf_dir` 을 만들거나 열 수 있고 active · stale 행이 0 일 때 쓴다. unavailable 이면 staging 도 건드리지
  않는다.
- 순수 함수의 경계: free 를 못 재면(None) 바닥 검사를 건너뛴다(M5g 와 같다). 시계가 뒤로 가면 경과를 0 으로 본다.
  모르는 route 는 `touches = False`.

### 15.4 펼치기 · 이름 · 경로 (B3~5 · B16 · B17 · A20 · A21)

- 안전하지 않은 멤버(제어문자 · 링크 · 장치 · `..` · 절대 경로 · `.git`)는 **그 멤버만 건너뛰고** manifest 의
  `skipped[]` 에 이유와 함께 적는다. 남은 파일이 0 이면 `failed(no_safe_files)`.
- 멤버 이름 앞의 `./` 는 떼고 판정한다(`tar -C dir .` 모양). 묶음과 펼침 둘 다.
- 확장자 비교는 대소문자를 가리지 않는다. `.git` 조각도 casefold 로 거절한다. `<이름>.d` 가 255바이트를 넘으면
  펼치지 않는다(`name_too_long`).
- `name_collisions` 는 파일끼리의 충돌과 함께 「파일 경로가 다른 파일의 디렉터리 접두와 같은」 충돌도 잡는다.
  casefold + NFC 키로 파일 집합과 디렉터리 접두 집합을 만들어 대조한다(O(n · 깊이)).
- 경로: 퍼센트 디코딩 뒤 `safe_member` 가 거절하면 400, 안전하지만 manifest 에 없으면 404. `out%2fa.png` 는
  디코딩하면 `out/a.png` 라 있으면 200 이다.
- `Content-Disposition` 의 이름은 basename.
- zip 에는 `files/` 아래만 넣고(경로는 `files/` 기준) rcm 의 `manifest.json` 은 넣지 않는다. zip64 를 허용한다.

### 15.5 HTTP · 보안 세부 (B6 · B7 · B12~15 · B18~22 · C28)

- Host 허용 목록에 더한다: `[::1]`, `*.ts.net`, bind 주소, `base_url` 의 호스트. 비교 전에 소문자로 바꾸고 끝의
  점을 떼고 IPv6 괄호를 푼다. 포트는 따로 본다.
- **유효한 Bearer 가 있는 요청은 Host 검사를 건너뛴다**(rebinding 공격자는 토큰이 없다). 순서는 Host 421 → basic
  401.
- 되살리기 Origin 비교: scheme 은 http(public_url 이 https 면 https 도), host 는 소문자 비교, 포트는 같아야 한다.
- unavailable 이면 `/shelf*` 와 `/api/shelf*` 전부 503(사람이 보는 페이지는 이유 한 줄).
- 삭제 단계가 지났는데 청소기가 아직 안 지운 항목은 stale 로 대한다. 쓰면 다시 산다(드물고 무해하다).
- `touches()` 와 상태 집계는 DB 열이 아니라 `stage()` 값을 쓴다. `copying` 은 `pending` 칸에 센다.
- copier: 항목 하나의 예외는 `failed(copy_error)` + 로그. `sqlite3.OperationalError`(잠김)는 실패로 치지 않고 다음
  깨움에 다시 한다. 루프는 모든 예외를 잡고, 같은 예외가 5번 연달아 나면 `error_code = "copier_stuck"`.
- **깨우기에 5초 폴링을 더한다.** 이벤트를 놓쳐도 5초 안에 pending 을 줍는다(값싼 쿼리 하나). 「transit 을
  벗어날 때 깨운다」 경로는 오늘 코드에 없으므로 이것으로 대신한다.
- 보관 상태가 바뀌면 그 잡의 SSE 갱신을 낸다(SPA 가 링크를 바로 그린다).
- **M5e 묶음 화해(`reconcile_bundles_on_start`)는 이 마일스톤에서 `App.start` 에 넣지 않는다.** 넣으면 재시작을
  넘겨 살아 있는 원격 잡의 uploading 묶음이 failed 가 된다. 그 누락은 §13 에 기존 문제로 남기고 따로 설계한다.
- `/shelf/latest/<preset>`: branch 가 없으면 모든 브랜치 가운데 가장 최근. 「가장 최근」은 active · stale 가운데
  잡 번호가 가장 큰 것.
- `failed` 항목: 페이지는 200 이고 실패 이유를 보인다. 파일 경로는 404 `not_kept`.
- DELETE 성공은 200 `{deleted: true}`, 다시 보내면 410. 묘비에 대한 `/api/shelf/N` 은 410 + `{state:"deleted",
  deleted_at}`. keep 이 아닌 제출 응답에는 `shelf_url` 키가 없다.

### 15.6 CLI · 웹 · 알림 (C1~29)

- `rcm run` 에는 `--json` 이 없다. 마지막 JSON(stdout)에 `shelf` 객체를 싣는다.
- 보관 줄은 job 의 `keep` 으로 정한다. 프리셋 `keep = true` 만으로도 `rcm run` 과 `rcm wait` 가 보관 줄을 찍고 60초까지
  기다린다. `waiting` 이면 계속 기다린다(같은 60초 안에서).
- `--keep` 인데 health 의 `shelf.enabled` 가 false 면 제출하지 않고 `shelf is off on this server` · 종료 2.
  health 를 못 읽으면 다른 명령과 같은 종료 3.
- `/api/status` 의 `preset_json` 에 `has_artifacts: bool` 과 `keep: bool` 을 더한다(P2, 공개 키 잠금 갱신). CLI 는
  스냅샷 전에 이것으로 `keep_without_artifacts` 를 거른다.
- `--keep --fetch-artifacts`: 받기를 먼저 한다(ack 는 미뤄진다), 보관 줄은 마지막.
- `--timeout` · Ctrl-C detach: `shelf: <url> (after the job finishes)` 한 줄. supersede 로 끝나면
  `shelf: moved to #N`.
- `rcm shelf`: 기본은 active + stale, `--state all` 은 deleted 를 뺀 전부. 시각은 서버의 display timezone.
  `rcm shelf rm`: 200 → 종료 0, 410 → `already deleted` 종료 0, 409(pending · copying · busy) → 종료 1.
- `rcm check` 행은 기존 순서(`ok   shelf …`)를 따른다. 꺼짐은 `ok   shelf  off`. 크기는 기존 `_bytes` 표기(십진)를
  따르고 명세 예시의 `50 GB` 는 `53.7 GB` 로 읽는다. URL 은 `client.server + "/shelf/"`.
  `--config` 의 고아 수는 DB 를 `mode=ro` 로 열어 센다(행 이름 `local shelf`). 폴더에 못 쓰면
  `error_code = "shelf_dir_unwritable"`.
- `core/shelf.py` 에 `SHELF_REASON_CODES` 튜플을 둔다(P1). SPA · CLI · 페이지 문구가 이것으로 잠긴다.
- 웹훅: 맨 위 키 `shelf_url`(keep 인 잡만). `job` 행의 `shelf` 객체는 그대로. 알림 시점에 보관이 이미 `none` ·
  `failed` 로 확정이면 `RCM_SHELF_URL` 은 빈 값이고 `shelf_url` 키는 없다.
- SPA 지우기 버튼은 토큰이 있으면 보이고, admin 이 아니면 서버의 403 을 그대로 알린다.
- SPA 링크는 경로만 쓴다(`/shelf/N/`). nav 의 「보관함」은 보관함이 켜졌거나 항목이 있으면 보인다.
  `renderNav` 가 릴리스 저장소가 없을 때 nav 를 통째로 숨기는 것을 고친다. SPA 언어가 영어면 `?lang=en` 을 붙인다.
- 서버 HTML 페이지의 문구 · 카탈로그는 **P2** 가 맡는다(`shelf_pages.py`, ko · en). `?lang=en` 은 링크 · form ·
  303 에 이어진다. 시나리오 c 의 페이지 문구 시험은 P2 로 옮긴다.
- 목록의 날짜 칸: 보관 중 칸은 「정리 대기 예정 `stale_at`」, 정리 대기 칸은 「삭제 예정 `delete_at`」.
- `rcm gc --dry-run` 의 보관함 한 줄: 계획 함수는 P1, 출력은 P3.
