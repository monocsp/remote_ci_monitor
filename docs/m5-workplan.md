# M5 작업 명세 — 확장 (우선순위 · 내용 주소 스냅샷 캐시 · 알림 · 원격 워커)

> PLAN.md 「M5 — 확장」의 구현 명세. **Codex 리뷰(`docs/reviews/2026-09-06-codex-m5-design.md`) 반영본** — 바뀐 곳은 「(리뷰 반영)」. PLAN 은 M5 를 「GitHub 백엔드 · 원격 워커 · 우선순위 · 내용 주소 스냅샷 캐시 · 알림」으로 적고 완료 기준을 두지 않았다. 여기서 **범위와 완료 기준을 정한다**: M5 는 두 PR — **M5a**(우선순위 · 스냅샷 캐시 · 알림, 서버 한 대) 와 **M5b**(원격 워커 — 빌드 머신 여러 대). **GitHub 백엔드는 M6 으로 미룬다**(2026-09-04 「GitHub 에 의존하지 않는다」 방향 전환과 상충 — 여전히 원하는지 오너 결정 30). 완료 기준(M5): ① `rcm run gate --priority high` 가 대기 중인 normal 잡보다 먼저 돈다 ② 같은 트리를 두 번째 올릴 때 바뀐 파일만 전송된다(전송 바이트가 트리 크기의 10% 미만) ③ 잡이 끝나면 설정한 알림 명령/URL 이 한 번 호출된다 ④ 다른 머신의 `rcm worker` 가 붙어 그 풀의 잡을 돌리고, 워커가 사라지면 그 잡은 `lost` 로 남는다.
>
> 바꾸지 않는 것: 런타임 의존성 0 · 스키마 v1 의 기존 키(추가만) · 쓰기는 토큰 · 프리셋만 실행 · fail-open 금지 · `test` 잡 이름.

## M5a-1. 우선순위

- 값: `priority ∈ {-1, 0, 1}`(low · normal · high). 세 단계면 충분하다 — 숫자 우선순위는 기아를 만들고 설명이 어렵다.
- 어디서: `rcm run … --priority high|normal|low`(플래그가 없으면 **프리셋의 `priority`**, 프리셋에도 없으면 normal) · 프리셋 `priority = "high"`(그 프리셋 잡의 기본값이자 비-admin 의 상한 — 세션이 낮출 수는 있어도 admin 이 아니면 프리셋 기본보다 **높일 수 없다**; 게이트를 모두 high 로 넣는 일을 막는다) · admin 은 `rcm bump N [--priority high]`(기본 high; `POST /jobs/{id}/priority`, admin 토큰, 대기 잡만; running 은 409). `presets[].priority`(추가 키)를 상태 JSON 에 실어 클라이언트가 403 을 미리 안다.
- 순서(순수 `core/queue.py`): 대기 잡 정렬 키 `(-priority, id)`. `position` 도 이 순서. `store.claim` 은 `ORDER BY priority DESC, id`(리뷰 반영 — FIFO 잔재 제거, 순수 정렬과 같은 키). 합류 판정은 priority 와 무관. (리뷰 반영) 합류 시 priority 상향은 **한 트랜잭션** `store.join_or_bump(join_key, name, label, priority, now)` — `max(existing.priority, requested)` 로만 올린다, 낮으면 그대로.
- (리뷰 반영) `Preset.priority: int = 0`(설정 `priority = "high"|"normal"|"low"`). 서버 `submit`: 비-admin 토큰은 `requested <= preset.priority` 여야 한다(아니면 403 `priority above the preset default needs an admin token`). `POST /jobs/{id}/priority` 는 admin 이고 대기 잡(`uploading`·`queued`)만, 아니면 409.
- 표시: `queue[].priority`(int, 추가 키) · `rcm top` 행 앞에 `↑`(high) / `↓`(low) · 웹 행에 `high`/`low` 칩 · `reason` 은 그대로(우선순위는 이유가 아니다).
- 기아: high 가 계속 들어오면 normal 은 기다린다. 화면이 그것을 보여주므로(대기 시간 · Not moving 의 `overdue` 아님) 별도 보정은 없다. README 에 명시.
- DB: `jobs.priority INTEGER NOT NULL DEFAULT 0`(v3 마이그레이션).

## M5a-2. 내용 주소 스냅샷 캐시

목표: 두 번째 업로드부터 **바뀐 파일만** 보낸다. 참고 팀 트리(수십 MB · 에셋)에서 매번 전체 tar 는 Tailscale 에서 수 초 — 캐시로 1초 미만.

### 프로토콜(같은 HTTP · 같은 토큰)

(리뷰 반영) 클라이언트 `Snapshot.entries = [{path, mode, size, sha256, kind}]` 가 **단일 출처** — manifest 도, 전체 tar 도, 부분 tar 도 이 목록에서만 만든다(manifest 와 tar 의 입력이 어긋날 수 없다).

1. `POST /jobs` 응답에 `"cache": true`(서버 `snapshot_cache = true` 일 때). 클라이언트가 `--no-cache` 이거나 서버가 false 면 기존 전체 tar 경로 그대로(호환).
2. `POST /jobs/{id}/tree/manifest` (그 잡의 토큰) 본문 `{"files": [{"path", "mode", "size", "sha256"}], "links": [{"path", "target"}]}` — 경로 규칙은 tar 데이터 필터와 같다(절대 경로·`..`·빈 조각 거부, 링크 target 은 워크스페이스 안 상대 경로만). 합계 `size` 가 `max_snapshot_bytes` 를 넘으면 413 + cancelled(기존 문구). 서버는 `jobs/<id>/manifest.json` 을 저장하고 `{"missing": ["<sha256>", …], "missing_bytes": N}` 을 돌려준다(blobs 에 없는 해시, 중복 제거).
3. `PUT /jobs/{id}/tree` 헤더 `X-RCM-Tree: blobs` — tar.gz 안 멤버 이름이 `<sha256>`(경로가 아니라 해시, 파일마다 하나). 서버는 멤버를 읽으며 sha256·크기를 다시 계산해 (리뷰 반영) `blobs/<aa>/.<sha256>.<job_id>.<thread>.part` 에 쓰고 검증 뒤 `os.replace`(같은 blob 을 두 클라이언트가 동시에 올려도 충돌 없음; 이미 있으면 검증만 하고 replace 생략). 해시가 안 맞거나 manifest 의 missing 에 없는 멤버는 400 + cancelled(`snapshot rejected: blob hash mismatch`). 다 받으면 `queued`. missing 이 비어 있으면 `PUT` 없이 `POST …/manifest` 응답에 `"state": "queued"`(서버가 바로 queued 로). (리뷰 반영) manifest 를 저장하면 `last_received_at` 을 갱신한다 — PUT 이 안 오면 기존 `upload_abandon_seconds` 경로가 cancelled 로 덮는다.
4. 자재화(`materialize.assemble_manifest`): manifest 순서대로 디렉터리 생성 → 파일은 blob 에서 **복사**(`shutil.copyfile` — 하드링크 금지: 잡이 워크스페이스 파일을 고치면 blob 이 깨진다. APFS/btrfs 의 reflink 는 `os.copy_file_range`/`clonefile` 이 되면 쓰고 아니면 복사) → mode 적용(실행 비트만, 0o644/0o755) → 링크 생성. blob 이 없으면 `failed` + `snapshot blob missing <sha7>`(보존 정리가 지웠거나 손상 — 세션은 `--no-cache` 로 재제출).
5. `source` 추가 키(리뷰 반영 — 기존 `received_bytes` 의 의미는 그대로): `uploaded_bytes`(이번에 실제로 받은 바이트) · `cached_bytes`(캐시 히트 바이트). `rcm run` stderr: `uploading #N: 3.1 / 48.2 MB (cache 94%)`.

### 저장 · 정리

- `blobs` 테이블(v3): `sha256 PRIMARY KEY · size · created_at · last_used_at`. manifest 를 받을 때 참조된 blob 의 `last_used_at` 갱신.
- janitor: (리뷰 반영) 먼저 활성 잡(`uploading·queued·running·cancelling`)의 `jobs/<id>/manifest.json` 참조를 모아 **제외**하고, `snapshot_cache_days`(기본 30) 지나도록 안 쓰인 blob 을 지운다(파일 → 행). 업로드 중인 잡은 활성이라 그 blob 은 GC 대상이 아니다.
- `snapshot_cache_max_bytes`(기본 4 GiB): 넘으면 `last_used_at` 오래된 순으로 지운다(활성 참조 제외). 상태 JSON `server.snapshot_cache: {blobs, bytes}`(추가 키).
- 해시: sha256 hex. 클라이언트는 이미 파일별 sha256 을 계산한다(`tree_hash`) — 재사용.
- 보안: manifest 경로 검증 · blob 크기 상한(`max_snapshot_bytes`) · 다른 잡의 manifest 를 못 본다 · 해시로 내용을 읽는 API 는 없다. (리뷰 반영) 그러나 `missing` 목록은 **존재 오라클**이다 — 해시를 아는 클라이언트는 그 내용이 서버에 있는지 알 수 있다. 그래서 `snapshot_cache_scope = "global"|"token"`(기본 `global` — 팀 내부 도구, 같은 트리를 여러 세션이 올린다; `token` 이면 blob 키를 `<token name>/<sha>` 로 나눠 공유하지 않는다) 와 `snapshot_cache = false` 를 둔다(오너 결정 32).

### 클라이언트

- `client.upload_cached(job_id, snapshot)`: manifest → missing → 부분 tar(멤버 이름 = sha256, 같은 해시는 한 번) → PUT. (리뷰 반영) **`POST …/manifest` 가 404 일 때만**(구버전 서버) 전체 tar 로 간다. 400/401/403/413/5xx 는 절대 폴백하지 않는다(잡은 cancelled 로 남고 문구가 `--no-cache` 를 안내한다 — 조용한 폴백은 왜 느린지 숨긴다).
- (리뷰 반영) 완료 기준 ② 측정: gzip 이 잘 되는 텍스트로는 허위 통과가 가능하다 — 테스트 fixture 는 **난수 내용 50 MB**(압축 안 됨) 트리를 두 번 올리고, 두 번째의 HTTP 요청 본문 바이트(manifest + PUT)를 서버가 센 값(`uploaded_bytes` + manifest 길이)으로 잰다: 원본 합계의 10% 미만.

## M5a-3. 알림

```toml
[[notify]]
name = "slack-fail"
on = ["failed", "timed_out", "lost"]        # 종료 상태 부분집합. 기본 = 전부
presets = ["gate", "deploy"]                # 선택. 비면 전부
argv = ["bash", "/opt/rcm/notify.sh"]       # 또는 url = "https://hooks.example/…" (둘 중 하나)
timeout_seconds = 30
```

- 트리거: 이벤트 버스의 `job_finished` **+ (리뷰 반영) 시작 시 스캔**. `notifications(job_id, notify_name, claimed_at, delivered_at, failed)` 테이블(v3, `UNIQUE(job_id, notify_name)`): 알림 스레드(`notify.py`)는 실행 전에 그 행을 **unique insert 로 claim** 한다 — 같은 `job_finished` 가 두 번 와도(recover · finish · 재발행) 한 번만 실행된다(이벤트 중복은 정상 입력). 시작 시 최근 `metadata_retention_days` 안의 종료 잡 중 행이 없는 것을 스캔해 보낸다(재시작 직후 recover 이벤트를 놓치지 않는다). 동시 1. argv 는 env `RCM_JOB_ID · RCM_STATE · RCM_PRESET · RCM_KEY · RCM_REQUESTER · RCM_SUMMARY · RCM_FAILED_STEP · RCM_EXIT_CODE · RCM_JOB_SECONDS · RCM_URL · RCM_NOTIFY`(이름) 로 — (리뷰 반영) 사용자 문자열(summary · failed_step · requester)은 NUL·제어문자 제거, 4 KB 로 자른다. url 은 `POST` JSON(최근 완료 행 + `notify` 이름, `Content-Type: application/json`, `urllib.request` 에 (리뷰 반영) `redirect_request` 가 None 인 opener — 3xx 는 실패, 응답 본문 무시). 셸 없음 · 보간 없음.
- 실패(종료 ≠ 0 · 타임아웃 · HTTP ≠ 2xx): 재시도 없음. 서버 로그 한 줄 + `server.notify_failures`(추가 키, 시작 후 누적 수) — `last_error` 는 안 건드린다(알림 실패로 큐가 아픈 것처럼 보이면 안 된다).
- 설정 검증: argv/url 중 하나만 · `on` 은 종료 상태만 · `presets` 는 존재하는 프리셋 · url 은 `https://` 또는 `http://`(로컬 훅). 알림 명령도 프리셋과 같은 「등록된 명령만」 규칙. `examples/server.toml` 의 주석 예시는 주석을 풀면 그대로 유효해야 한다(`presets` 에 주석 처리된 프리셋을 넣지 않는다).
- README: `--no-cache` 로 같은 트리를 다시 올리려면 합류를 피해야 하므로 `--no-join` 도 같이 쓴다는 것을 적는다.
- 서버 재시작 중 끝난 잡(lost)도 알림 대상(recover_on_start 가 `job_finished` 를 낸다 — 이미 낸다).

## M5b. 원격 워커 (빌드 머신 여러 대)

한 서버(큐 소유)에 여러 머신의 워커가 붙는다. **PLAN 의 `pools[]` 축이 실제가 된다.**

### 모델

- 잡에 `pool`(문자열, 기본 `"default"`) — 프리셋 `pool = "linux"` 로 정하고 세션은 `--pool` 로 프리셋이 허용한 풀(`pools = ["default", "linux"]`) 안에서 고른다. 로컬 워커(같은 프로세스)는 풀 `default`.
- 워커 토큰: `rcm token add build-02 --worker`(`tokens.kind ∈ {client, admin, worker}` — v3). 워커 토큰은 `/worker/*` 만 되고 클라이언트 API 는 안 된다(잡 제출·취소 불가). 클라이언트 토큰은 `/worker/*` 가 안 된다.
- 워커 등록: `workers` 테이블(v3) `name · pool · lanes · host_name · last_seen_at · version`. 상태는 (리뷰 반영) **서버가 받은 시각** `last_seen_at` 로만 만든다(워커 payload 의 시각은 어디에도 쓰지 않는다): `now − last_seen_at > worker_timeout_seconds`(60) 면 down.

### 프로토콜(`/worker/*`, 워커 토큰)

| 라우트 | 동작 |
|---|---|
| `POST /worker/register` `{pool, lanes, host_name, version}` | 등록/갱신. 버전이 서버와 다르면 409(같은 wheel 을 깔라) |
| `POST /worker/claim` `{lane}` | 그 풀의 `queued` 잡 하나를 원자적으로 running 으로(그룹 배제는 **풀 안에서** — 그룹은 풀 단위 자원이다). 없으면 최대 20초 long-poll(서버는 `wake` 를 기다림) 뒤 204 |
| `GET /worker/jobs/{id}/tree` | 그 잡의 tar.gz. 캐시 잡은 (리뷰 반영) 서버가 manifest + blob 으로 임시 tar.gz 를 조립해 `Content-Length` 를 붙여 준다 |
| `POST /worker/jobs/{id}/phase` `{phase}` · `POST /worker/jobs/{id}/log`(raw 바이트 — (리뷰 반영) 마커 파싱은 **서버**가 append 하면서 `parse_marker`; 워커는 파싱하지 않는다) · `POST /worker/jobs/{id}/finish` `{outcome, exit_code}` | 실행 보고. 로그는 `jobs/<id>/log.txt` 에 서버가 append |
| `POST /worker/heartbeat` `{lanes: [{lane, job_id}], host_sample}` | 5초. 응답에 `cancel: [job_id…]`(취소 요청된 잡) · `paused` |

- 서버 쪽 `lost`: 워커가 `worker_timeout_seconds` 동안 heartbeat 이 없으면 그 워커의 running 잡을 `lost`(summary `worker <name> unreachable`)로, 워커는 down. 워커가 다시 오면 그 잡을 이어 받지 않는다(재현성 — 새로 제출).
- git_ref 잡: 워커 머신에서 fetch 한다(워커 설정 `[[repos]]` 필요 — 서버 것과 이름이 같아야; 없으면 그 풀에선 git_ref 프리셋을 못 받게 설정 검증).
- `hosts[]`: 워커별 항목(`name = 워커 이름`, `source = "worker"`, 워커가 보낸 표본). `server.workers[]` 의 `lane` 은 **int 유지**(스키마) — (리뷰 반영) `worker`(이름, 로컬은 null) 와 `display_name`(`build-02/1`) 만 추가 키로. 웹·`rcm top` 은 `display_name` 을 보인다.
- `pools[]`: 풀마다 `{name, lanes, queue, recent, medians, hosts}`. (리뷰 반영) 지금 서버 `status()` · `render_text` · `cmd_eta` · `cmd_jobs` · `web/app.js` 가 첫 풀만 본다(`pools[0]`/`pool0()`) — **전부 순회 구조로** 바꾼다(M5b-1). 중앙값은 풀별(같은 키라도 머신이 다르면 소요가 다르다).
- 클라이언트 `rcm worker --server URL --pool linux --lanes 1 [--config worker.toml]`: 워커 루프 = 등록 → (claim → tree 받기 → 자재화 → Popen → 로그 스트림(1초 배치) → finish) × lanes 스레드 + heartbeat 스레드(호스트 샘플러 포함). Ctrl-C/SIGTERM: 도는 잡 SIGTERM → grace → KILL, `finish {outcome: "lost"}` 보고. 워커에도 `execute` 로직이 필요하다 → `worker.py` 의 실행 부분을 `runner.py`(자재화·Popen·펌프·신호)로 떼어 로컬 워커와 원격 워커가 공유한다.
- 보안: 워커 토큰은 `/worker/*` 만 · 워커가 보낸 로그/표본은 데이터로만 · 워커는 자기가 claim 한 잡만 보고할 수 있다(`jobs.worker_name`) · 서버가 워커로 접속하지 않는다(워커 → 서버 단방향, NAT 뒤 워커도 된다).

### 완료 기준 확인(실기)

이 Mac 에서 서버 하나 + `rcm worker --pool mac2` 프로세스 하나(다른 data dir · 다른 HOME)로: `pool = "mac2"` 프리셋 잡이 워커에서 돌고 로그·스텝·호스트가 서버 화면에 보인다 · 워커를 `kill -9` → 60초 안에 잡 `lost` · 워커 down 표시 · 다시 띄우면 다음 잡을 받는다. 두 번째 머신 실기는 오너(README 「Verify」 11단계).

## 순서

1. 이 명세 → Codex 리뷰 → 반영.
2. **M5a**: 테스트-퍼스트 A(순수: 우선순위 정렬 · manifest 검증 · blob GC 규칙 · 알림 필터) · B(서버·클라이언트: manifest/blobs 라우트 · 캐시 업로드 · priority 라우트 · 알림 스레드 · janitor blob GC · e2e 전송 바이트 측정) · C(CLI·웹·설정: `--priority` · `rcm bump` · 칩 · `[[notify]]` 검증 · README) → 구현 → 격리 검증 → PR → dev.
3. **M5b** — (리뷰 반영) **4 개의 PR** 로: **M5b-1** DB v3 기반 + `pool` 컬럼 + 풀별 `status()`/`render_text`/`cmd_eta`/`cmd_jobs`/웹의 `pools[]` 순회(풀 하나일 때 화면은 그대로) · **M5b-2** 워커 토큰(`tokens.kind`) + `/worker/register·claim·heartbeat` + `last_seen_at` 기반 down/lost · **M5b-3** `runner.py` 분리 + `rcm worker`(tree 받기 · 실행 · raw 로그 스트림 · finish · 신호) + 캐시 잡 tar 조립 · **M5b-4** UI/CLI 다중 풀 표시(`display_name` · hosts 워커 항목) + README/실기. 각 PR 마다 테스트-퍼스트 → 구현 → 격리 검증.
4. PLAN v2.4: M5 완료 · 결정 30(GitHub 백엔드 M6 여부) 31(우선순위 3단계) 32(캐시 blob 공유) · 스키마 추가 키 목록 · `rcm run` 흐름에 캐시 단계. dev → main → v0.2.0.
