# M5e 작업 명세 — 잡 산출물: 낸 세션이 가져간다

> 오너 요청(2026-09-08): 「산출물을 호출한 다른 세션이 바로 가져가고, 가져간 게 확인되면 바로 삭제
> 처리해서 최적화하고 싶다.」 동기는 Flutter 골든이다 — `flutter test --update-goldens` 가 빌드
> 머신의 워크스페이스에 PNG 64장을 만드는데 **지금은 그게 돌아올 길이 없다.**
>
> 입력: `docs/reviews/2026-09-08-codex-m5e-plan.md`(설계 초안 리뷰) ·
> `docs/reviews/2026-09-08-codex-m5e-spec.md`(이 명세 초고 리뷰). 두 리뷰의 코드 사실 주장은
> 전부 재확인했고, 확인된 것만 반영했다.
>
> 바꾸지 않는 것: **런타임 의존성 0**(표준 라이브러리만) · **API 스키마 v1, 키는 추가만** ·
> 정직성 규칙(모르면 `—`, 실패를 성공으로 포장하지 않는다, `lost` 는 조용히 되살리지 않는다) ·
> 쓰기는 Bearer 토큰, 읽기는 그 잡의 자격자 · 주석은 한국어, 식별자·CLI·문서는 영어 ·
> 웹 문자열은 한국어 기본 + 영어(결정 36) · 서버는 문장이 아니라 코드를 내려보낸다(결정 37).

## 0. 오너 결정 (2026-09-08) — `PLAN.md` 결정 표 39~42

| # | 항목 | 결정 |
|---|---|---|
| 39 | 산출물 되돌려주기 | 프리셋이 선언한 글롭에 맞는 파일을 워커가 **워크스페이스를 지우기 전에** 모아 서버에 불변 묶음으로 두고, 그 잡의 자격자가 받아 간다. 로컬 워커·원격 워커 **둘 다** |
| 40 | 언제 지우나 | **`join_count == 0` 인 잡은 확인(ack)이 오면 즉시 삭제**, 한 번이라도 합류가 있었던 잡은 확인이 와도 **TTL 까지 유지**. TTL 24시간 |
| 41 | 상한 | 전부 설정 키. 잡당 1 GiB · 서버 전체 10 GiB · 파일 10000 · 수집 60초. 넘으면 **잡은 그대로 성공/실패하고** 산출물만 버린다 |
| 42 | 받기·덮어쓰기 | 받기는 옵트인(`--fetch-artifacts`). 제출 당시와 내용이 같은 파일은 덮어쓰고, 제출 뒤 사람이 손댄 파일은 `--force` 여야 덮는다 (**오너 확정 2026-09-09**) |

## 1. 이름 — `artifacts` 는 이미 한 번 쓰였다

`jobs.artifacts_purged_at` · `store.mark_artifacts_purged` 는 M3 것이고 뜻이 **다르다**: 「보존
정리가 그 잡의 **로그·스냅샷·워크스페이스**를 지운 시각」이다(`janitor.py` 머리 주석). 사용자
문서·README·CLI 에는 `artifact` 라는 말이 아직 한 번도 안 나온다.

그래서: **사용자 쪽 말은 `artifacts`**(CI 의 표준 어휘다). **코드 쪽에서 새 것은 항상 `bundle`**
— 표는 `job_artifacts`, 파일은 `bundle.tar`, 해시는 `bundle_sha256`, 지운 시각은
`job_artifacts.purged_at`. M3 의 `jobs.artifacts_purged_at` 은 **그대로 둔다**. `store.py` 스키마와
`janitor.py` 주석에 「이 둘은 다른 것이다」를 적는다. 이 구분을 놓치면 청소기가 남의 것을 지운다.

## 2. 지금 코드가 하는 일 (전수 확인)

- 성공한 잡의 워크스페이스는 완료 직후 **지우려고 시도한다**. 로컬은 `run_job` → `store.finish`
  (`worker.py:368`) → `rmtree`(`worker.py:381`), 원격은 `_finish` → `_cleanup`
  (`remote_worker.py:451,453`). 둘 다 `ignore_errors=True` 다 — **회수된 바이트를 이 동작에서
  추정하지 않는다.**
- 잡에서 세션이 가져갈 수 있는 것은 **로그뿐**이다. `/worker/jobs/{id}/tree` 는 **입력 스냅샷**을
  워커에게 주는 것이지 산출물이 아니다.
- **합류자 표의 키는 `(job_id, name)` 이고 `name` 은 토큰 이름이다**(`store.py:103`). 한 토큰의 두
  세션은 한 줄이고, 요청자가 자기 토큰으로 다시 내면 **줄이 아예 안 생긴다**(`store.py:619`).
  → **지금 모델로는 세션을 셀 수 없다.** 「자격자 전원 확인」은 그대로는 만들 수 없다(결정 40).
- **합류는 `ACTIVE_STATES` 에서만 일어난다** — `uploading·queued·running·**cancelling**`
  (`core/model.py:29`). 판정과 기록은 `join_or_bump` 의 `BEGIN IMMEDIATE` 안이고
  (`store.py:606`), 종료도 같은 직렬화를 쓴다(`store.py:968`).
  → 「몇 번 붙었나」는 **터미널 커밋 시점에** 얼어붙는다(프로세스 종료 시점이 아니다).
  수집 중에 붙은 세션도 세어진다 — 그래서 빈손이 되지 않는다.
- 자재화의 안전 규칙은 초안이 믿은 것과 **다르다**: `tarfile` 의 `data` 필터는 절대 경로를 거부하지
  않고 **앞의 `/` 를 떼어 상대화**하며(`PLAN.md` 「서버 풀기」), 목적지 **안**을 가리키는 링크는
  허용한다. `assemble_from_manifest` 는 심링크를 **만든다**. 그리고 두 자재화 함수 모두 목적지를
  먼저 `rmtree` 한다(`materialize.py:47`, `materialize.py:87`).
  → 클라이언트가 `extract_tree` 를 재사용하면 **사용자의 작업 트리가 지워진다.**
- **`core/manifest.py` 에는 조각 길이 제한이 없다** — 전체 4096자(`MAX_PATH_LEN`)만 본다.
  `_glob_to_regex`(`core/snapshot.py`)는 **앵커 없는 조각**을 돌려주고 `search` 로 쓰인다
  (`snapshot.py:113`). 둘 다 그대로 쓰면 안 된다(§4).
- 원격 완료에는 **이미 경주가 있다**: `_finish` 는 `self.running` 에서 뺀 **뒤에** 완료를 보고하고
  (`remote_worker.py:455`), heartbeat 에 안 실린 활성 잡을 서버가 `lost` 로 닫는다.
- **취소 마감이 짧다**: 살아 있는 워커가 확인하지 않으면 서버가 `kill_at + 2 × heartbeat` 에 닫는다
  (`remote_workers.py:536`). `worker_heartbeat_seconds` 기본값은 **5초**다 — 수집에 60초를 쓰면
  이 마감을 넘긴다(§5).
- **보존 기간은 0 일 수 있다**: `retention_days_success`·`retention_days_failure` 는 `>= 0` 이면
  통과하고(`config.py:594`), 0 은 「다음 sweep 에 바로」다(`core/retention.py:82`).
  → M3 청소에 번들을 얹으면 **24시간 약속이 설정 하나로 깨진다**(§8).
- 잡은 워커 사용자 권한으로 돌고 `HOME` 이 기본 통과다(프리셋의 `env_passthrough` 기본값,
  `core/model.py:132`). → **글롭은 유출 방어 장치가 아니다.** 명세는 「잡은 신뢰한다, 수집기는
  방어적이다」로 쓴다. **경계는 워크스페이스다.**

## 3. 모델

잡 하나에 묶음(bundle) 하나. 불변이다. 한 번 `ready` 가 되면 내용도 해시도 바뀌지 않는다.

```
<data_dir>/artifacts/<job_id>/bundle.tar     비압축 tar (PNG 는 이미 압축돼 있다)
<data_dir>/artifacts/<job_id>/manifest.json  {bundle_sha256, files:[{path,size,sha256,mode}]}
<data_dir>/artifacts/.staging/<무작위>       모으는 중 · 받는 중 (재시작 때 청소)
```

`jobs/<id>/` 와 **분리한다** — 기존 로그 보존이 실수로 지우지 못하게. 스냅샷 blob 캐시에는 넣지
않는다(공유 범위가 다르다).

### 상태 (공개 값)

| 값 | 뜻 |
|---|---|
| `disabled` | 프리셋에 `artifacts` 가 없다 |
| `pending` | 잡이 아직 안 끝났다 |
| `collecting` · `uploading` | 모으는 중 · 원격 워커가 올리는 중 |
| `ready` | 받을 수 있다. `file_count`·`total_bytes`·`expires_at` 이 있다 |
| `empty` | 모았는데 맞는 파일이 0개였다 (`ready` 와도 `unknown` 과도 **다르다**) |
| `dropped` | 상한·경로 충돌로 버렸다. `reason_code` |
| `failed` | 수집·업로드가 깨졌다. `reason_code` |
| `skipped` | 실행이 시작되지 못했다(자재화 실패 등) |
| `purged` | 확인(ack)을 받고 지웠다. **묘비로 `bundle_sha256` 을 남긴다**(§7 재생) |
| `expired` | TTL 이 지나 청소기가 지웠다 |
| `unavailable` | 메타는 있는데 파일이 없다(서버 사고). 조용히 `empty` 로 만들지 않는다 |
| `unknown` | M5e 이전 잡, 또는 산출물을 **보고하지 않은 옛 워커**. `empty` 로 소급하지 않는다 |

### 이유 코드 (결정 37 — 코드와 인자만 내려보낸다)

`over_bytes` · `over_files` · `no_match` · `path_conflict` · `unsafe_path` · `collect_failed` ·
`upload_failed` · `storage_full` · `timed_out` · `cancelled` · `not_run` · `interrupted` ·
`acked` · `expired` · `hash_mismatch` · `not_ready`.

뒤의 둘은 ack 가 409 로 거절할 때의 이유다 — 화면이 문장을 붙이려면 코드가 있어야 한다(결정 37).
`no_match` 는 **글롭에 맞은 것이 하나도 없을 때만** 쓴다. 맞긴 맞았는데 전부 일반 파일이 아니어서
건너뛴 경우는 `empty` + `reason_code = None` 이고 `skipped_count` 가 사정을 말한다 — 「맞은 게
없다」고 말하면 거짓이다.

**공개 `reason_args` 에는 경로를 넣지 않는다.** 거부된 파일 이름은 그 자체로 남의 트리 구조다.
수치(`{limit, seen}`)만 공개하고, 경로는 보호 라우트의 `detail` 에만 싣는다.

**잡의 `summary_code` 는 건드리지 않는다.** 「테스트가 깨졌다」와 「산출물을 못 올렸다」는 다른
사실이다. 산출물 사정은 `artifacts.reason_code` 로만 말한다.

## 4. 무엇을 모으는가 — 프리셋 글롭

```toml
[[presets]]
name = "goldens"
argv = ["flutter", "test", "--update-goldens"]
artifacts = ["test/**/goldens/*.png", "test/failures/*.png"]
```

- 기본값은 `[]` — 선언하지 않은 프리셋은 아무것도 모으지 않는다(`disabled`).
- 문법은 `core/snapshot.py` 의 `_glob_to_regex` 를 쓰되 **`re.fullmatch`(또는 `\A…\Z`)** 로
  워크스페이스 뿌리 기준 **전체 일치**를 만든다. `^…$` 는 안 된다 — 파이썬의 `$` 는 **끝의 개행
  앞**에서도 맞아서 `goldens/evil.png\n` 이라는 (POSIX 에서 가능한) 이름이 통과한다. 그 함수는 앵커 없는 조각을 돌려주고 원래 자리에서는 `search` 로 쓰인다
  (`snapshot.py:113`) — 그대로 쓰면 `goldens/*.png` 가 아무 데나 맞는다.
- 설정 검증(`config.py`)이 거부하는 것: 절대 경로 · `..` 조각 · 빈 조각 · 백슬래시 · NUL ·
  `.git` 조각 · **전체 포괄**(`**`, `*`, `**/*`) · 닫히지 않은 문자 클래스. 리터럴 조각이나
  확장자가 최소 하나 있어야 한다. 전체 포괄을 막는 이유는 유출 방어가 아니라(§2) **워크스페이스를
  통째로 돌려보내는 실수**를 막기 위해서다.
- 경로 규칙은 `core/manifest.py::_check_path` 를 재사용하되 **조각 길이 255바이트(UTF-8 인코딩
  기준)** 를 새로 더한다 — 거기엔 조각 제한이 **없다**(§2).
- `.git` 조각은 글롭이 뭐라고 하든 제외한다.
- 걷기: `os.walk(workspace, followlinks=False)` + 항목마다 `os.lstat`. **일반 파일만** 모은다.
  심링크 · 하드링크(`st_nlink > 1`) · 장치 · FIFO 는 **건너뛰고 센다** — `skipped_count` 는
  「글롭에 맞았지만 일반 파일이 아니어서 건너뛴 수」다. 글롭에 안 맞은 파일은 세지 않는다.
- **여는 방법**: `O_NOFOLLOW` 는 마지막 조각만 지킨다. 검사한 부모 디렉터리가 그 사이에 심링크로
  바뀌면 못 막는다. 그래서 워크스페이스 fd 에서 시작해 조각마다
  `os.open(part, O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC, dir_fd=…)` 로 **닻을 내리며 내려가고**, 마지막에
  `O_NOFOLLOW|O_NONBLOCK` 으로 열어 `os.fstat` 으로 일반 파일임을 다시 확인한다. `resolve()` 뒤에
  따로 `open()` 하지 않는다. **`O_NONBLOCK` 은 필수다** — FIFO 를 그냥 열면 쓰는 쪽이 열릴 때까지
  워커가 그 자리에 선다.
- `build/outputs/**` 처럼 **리터럴 접두가 있는 서브트리 전체**는 받는다. 관리자가 그 디렉터리를
  산출물로 선언한 것이고, 막는 대상은 워크스페이스 전체 포괄이다.
- **충돌 검사**: 목적지 경로를 NFC 정규화 뒤 casefold 해서 겹치면 묶음 전체를 `dropped` +
  `path_conflict` 로 만든다. **디렉터리 접두도 본다**(`A` 와 `a/b.png` 는 충돌이다). 대소문자를
  구분하지 않는 파일 시스템이 흔해서 두는 **이식성 정책**이다 — APFS 의 성질이라서가 아니다.

## 5. 언제 모으는가 — 워커 두 경로

### 로컬 (`worker.py`)

`run_job` 반환(`worker.py:346`)과 `store.finish`(`worker.py:368`) **사이**.

1. 종료 상태를 정한다(`outcome_for`).
2. 실행이 시작됐으면 예산과 상한 안에서 스테이징으로 모은다.
3. 검증하고 `<data_dir>/artifacts/<id>/` 로 `os.replace`.
4. **`store.finish` 와 `job_artifacts` 기록을 한 트랜잭션**으로 커밋한다. 그 트랜잭션 안에서
   취소 상태를 **다시 읽는다** — 수집 중에 수용된 취소가 이긴다. `outcome_for` 도 `Store.finish` 도
   이걸 자동으로 해 주지 않는다(`worker.py:135`, `store.py:970`).
5. 이벤트를 낸다. 6. 그다음 기존 워크스페이스 정리.

수집 오류는 **자기 자리에서** 잡는다. 바깥 워커 예외 경로로 새면 잡이 실패하고 레인이 `down` 이
된다 — 산출물 때문에 그러면 안 된다.

### 원격 (`remote_worker.py`)

`observer.final_flush()` 뒤, `_finish`(`remote_worker.py:451`) **앞**. 오류로 빠지는 경로
(`remote_worker.py:432~438`)도 산출물 처분을 남긴다 — 조용히 비우지 않는다.

1. 로컬에서 모은다(같은 순수 규칙).
2. `PUT /worker/jobs/{id}/artifacts` 로 올리고 **검증된 영수증**(`bundle_sha256`)을 받는다.
3. `finish` 에 **구조화된 처분**을 실어 보고한다(아래). 서버가 묶음과 종료 상태를 한 트랜잭션으로
   커밋하고, 그때 종료 상태를 **다시 확인한다** — 업로드 시작 때의 소유 검사는 발행을 허가하지
   않는다.
4. **`self.running` 에서 빼는 것은 완료 보고가 확정된 뒤**다.
5. 정리. 전송 결과가 불확실하면 스테이징을 짧게 남기고 `GET /worker/jobs/{id}/artifacts` 로
   확인한다.

`finish` 에 더하는 필드(선택):

```json
"artifacts": {"state":"ready","bundle_sha256":"…","file_count":64,"total_bytes":12812345,
              "skipped_count":0,"reason_code":null,"reason_args":null,"detail":null}
```

**필드가 통째로 없으면 `unknown` 이다**(옛 워커). `{"state":"empty"}` 와 반드시 구분된다.
해시만으로는 「빈 수집·시한 초과·안전하지 않은 경로·건너뛴 수」를 말할 수 없다.

`finish` 본문에 **`finished_at`(ISO Z, 선택)** 도 더한다 — 원격 워커의 **실행 종료 시각**이다.
서버는 그 값이 있으면 그것을 `jobs.finished_at` 에 쓴다(§10 의 `job_seconds` 가 수집·업로드
시간만큼 부풀지 않게). 없으면 지금처럼 수신 시각을 쓴다.

claim 응답에는 **얼린 정책**이 실린다 — 최상위 `artifacts` 객체(`{globs, max_bytes, max_files,
timeout_seconds, cancel_timeout_seconds}`). 워커는 그것만 보고 모으고, 서버는 업로드를 그것으로
검증한다.

업로드 중에도 잡은 **활성**이다. heartbeat 는 계속 그 잡을 싣는다. 산출물 진행은 실행 phase 와
별개 필드로 낸다 — 로그를 지어내서 「안 움직임」 감지를 속이지 않는다.

### 종료 상태별 · 예산

- `succeeded` · `failed` — 모은다. **실패한 골든의 diff 이미지가 가장 필요한 것이다.**
- `timed_out` · `cancelled` — 프로세스가 멈춘 것을 확인한 뒤 **짧은 예산**으로만 시도한다.
  `artifact_cancel_timeout_seconds`(기본 5)를 따로 두고, 설정 검증이
  **`2 × worker_heartbeat_seconds` 보다 작을 것**을 강제한다 — 서버가 미확인 취소를
  `kill_at + 2 × heartbeat` 에 닫기 때문이다(`remote_workers.py:536`, 기본 heartbeat 5초).
- 수집 도중 취소가 **새로** 수용되면 수집을 즉시 중단한다.
- `artifact_timeout_seconds`(60)는 **수집 + 검증 + 설치**를 덮는다. 업로드는 그 예산 밖이고
  전송 시한(`artifact_transfer_timeout_seconds`, 기본 300)이 따로 본다.
- `lost` — 모으지 않는다. 늦게 도착한 업로드는 **409**. 죽은 잡을 산출물로 되살리지 않는다.
- 자재화 실패 — 워크스페이스가 없다. `skipped` + `not_run`.
- 수집 예산 초과는 `dropped` + `timed_out` 이다. **잡을 `timed_out` 으로 바꾸지 않는다.**

## 6. 서버 — 라우트

「자격자」 = 그 잡의 요청자 · 합류자 · 관리자 토큰. **워커 토큰은 명시적으로 거부한다**
(`can_read_log` 은 소유·admin 만 보고 토큰 종류를 안 본다). 읽기는 `authenticate_read`
(`server.py:455`)를 쓰되 **`read_auth = none` 이어도 토큰을 요구한다** — 산출물은 공개 읽기가
아니다. **ack 는 쓰기라 Bearer 만**이다(브라우저가 Basic 을 자동으로 붙이므로 내부망 CSRF 로 남의
산출물을 지울 수 있게 된다).

| 라우트 | 인증 | 응답 |
|---|---|---|
| `GET /jobs/{id}/artifacts` | 자격자 | 200 상태 문서. `ready` 면 **최상위 `files`**(`{path, size, sha256, mode}` 를 경로순으로)와 `detail` 포함. 그 외 상태는 `state`+`reason_code` 만. 401 · 403 · 404 |
| `GET /jobs/{id}/artifacts/archive` | 자격자 | 200 `application/x-tar` 스트리밍(`Content-Length`, `Cache-Control: no-store`, `Content-Disposition: attachment; filename="job-<id>-artifacts.tar"` — 이름에 잡 번호 말고는 아무것도 넣지 않는다) · **409** `pending`·`collecting`·`uploading` · **404** `disabled`·`empty`·`unknown`·`skipped` · **410** `purged`·`expired`·만료 시각 지남 · **503** `unavailable`·전송 슬롯 없음(+`Retry-After`) |
| `HEAD …/archive` | 자격자 | 같은 상태·헤더, 본문 없음, 부수 효과 없음 |
| `POST /jobs/{id}/artifacts/ack` | 자격자, **Bearer 만** | 본문 `{"bundle_sha256": "…"}` → **200** 수용 · **200** 같은 해시 재생(이미 `purged` 여도) · **409** 해시 불일치(`hash_mismatch`) 또는 `ready`/`purged` 가 아님(`not_ready`) · **410** 만료 시각이 지났거나 상태가 `expired` · **400** 본문이 객체가 아님 · `bundle_sha256` 없음 · 64자리 hex 가 아님 · 깨진 JSON |
| `PUT /worker/jobs/{id}/artifacts` | 그 잡을 잡은 워커 | 201 영수증 · **200** 같은 해시 재시도(멱등) · 409 종료된 잡·다른 묶음 · 411 · 413 상한 초과 · 415 · 503 저장·슬롯 불가 |
| `GET /worker/jobs/{id}/artifacts` | 그 잡의 워커 | 200 `{"state", "bundle_sha256"(없으면 null), "job_state"}`. **종료된 잡에도 답한다** — `_owned_active`(`remote_workers.py:364`)를 쓰면 이 라우트가 존재하는 이유인 모호한 질문을 바로 거절한다 |
| `POST /worker/jobs/{id}/finish` | 기존 그대로 | 선택 `artifacts` 처분(§5). 기존 의미는 그대로 |

- 다운로드·업로드는 **스트리밍**이다. 일반 클라이언트 헬퍼는 응답을 통째로 메모리에 올린다 —
  본보기는 `WorkerClient.download_tree`(`client.py:110`)다.
- **전송 슬롯은 절대 기다리지 않는다.** 핸들러는 라우팅 전에 일반 요청 슬롯을 비블로킹으로 잡고
  없으면 503 을 낸다(`server.py:1502`). 전송 슬롯(`max_concurrent_artifact_transfers`)도 같은
  방식으로 잡고, 없으면 **즉시 503 + `Retry-After`** 다. 일반 슬롯을 쥔 채 기다리면 heartbeat·
  cancel·status 가 굶는다.
- 만료 시각이 지나면 새 다운로드는 410 이지만 **이미 열린 전송은 끝나게** 둔다. 파일 핸들을 잡고
  나서 unlink 하고, **마지막 독자가 닫을 때까지 그 바이트를 회계에 남긴다**(unlink 로는 공간이
  돌아오지 않는다).
- URL 에 토큰을 싣지 않는다.

## 7. 삭제 규칙 (결정 40)

```
ack 도착 → 해시 확인 → 자격 확인
  ├ 요청자·합류자   → acked_at 기록
  │                   join_count == 0 이면 즉시 삭제(state=purged, reason=acked, 해시는 묘비로 남김)
  │                   아니면 그대로 둔다(expires_at 까지)
  └ 그 둘이 아닌 admin → **no-op**. acked_at 을 쓰지 않고 `{"purged": false}` 를 돌려준다
```

- `join_count` 는 `jobs` 의 새 열이다. **`join_or_bump` 트랜잭션 안에서** 1 늘린다. 조건 바깥이다 —
  요청자 본인 재제출은 합류자 줄을 안 만들고(`store.py:619`), 같은 합류자의 재제출은
  `INSERT OR IGNORE` 로 줄이 안 늘지만 **둘 다 세어야 한다**.
- `--no-join` 은 그 요청만 합류를 건너뛴다. 그렇게 만들어진 잡도 `join_key` 를 가지므로
  **나중 제출이 거기 합류할 수 있다**(`server.py:838,858`) — 「합류 불가 잡」 표시가 아니다.
  `join_duplicates = false` 면 아무도 합류하지 않는다(`server.py:1179`).
- **합류자가 떠나도 `join_count` 를 되돌리지 않는다.** 이탈 분기는 종료 검사보다 **앞에** 있어서
  끝난 잡에서도 빠져나간다(`server.py:1304`). 되돌리면 남은 사람이 못 받는다.
- `Store.add_joiner` 에는 상태 검사가 **없다**(`store.py:782`) — 이 불변식은 `join_or_bump` 경로에
  걸린다. 픽스처가 합류자만 꽂아 `join_count == 0` 을 만들 수 있으니 테스트는 그 경로를 쓴다.
- ack 는 「응답을 해시했다」가 아니라 **「목적지에 적용까지 끝냈다」**는 뜻이다.
  - **충돌이 하나라도 남았으면 보내지 않는다.** 「충돌 빼고 전부」는 완료가 아니다.
  - `--dry-run` 은 절대 보내지 않는다.
  - 적용 뒤 죽어서 ack 를 못 보냈으면 서버 묶음은 남는다(재시도나 TTL 이 정리한다).
- **검사 순서는 만료 → 자격 → 해시 → 상태**다. 만료와 해시 불일치가 같이 성립하면 **410** 이다 —
  묶음이 이미 없는데 「해시가 틀렸다」고 답하면 클라이언트가 엉뚱한 곳을 본다.
- **재생(replay)**: `purged` 가 된 뒤 같은 해시로 다시 오면 **200** 이다. 응답을 잃은 클라이언트가
  409 를 받지 않게 `bundle_sha256` 과 `acked_at` 을 묘비로 남긴다.
- 삭제가 실패하면 `purged` 로 표시하지 않는다. 예약 바이트를 계속 잡고 다음 sweep 에 다시 시도한다.
  **물리적으로 지우기 전에 지웠다고 말하지 않는다.**

## 8. 보존 · 상한 (전부 설정 키)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `[[presets]].artifacts` | `[]` | 모을 글롭. 없으면 이 기능이 꺼진다 |
| `artifact_retention_hours` | `24` | TTL. **`ready` 가 된 시각(발행)부터** 잰다. 읽어도 연장되지 않는다 |
| `max_artifact_bytes` | `1073741824` (1 GiB) | 잡당 **원본 파일** 합계 상한 |
| `max_artifact_files` | `10000` | 잡당 파일 수(바이트와 따로 건다) |
| `artifact_storage_max_bytes` | `10737418240` (10 GiB) | **서버 전체**: 발행된 아카이브 + 예약 + 스테이징 |
| `artifact_timeout_seconds` | `60` | 수집·검증·설치 예산 |
| `artifact_cancel_timeout_seconds` | `5` | 취소·타임아웃 뒤 예산. `2 × worker_heartbeat_seconds` 미만이어야 한다 |
| `artifact_transfer_timeout_seconds` | `300` | 업로드·다운로드 한 건의 시한 |
| `max_concurrent_artifact_transfers` | `2` | 동시 전송 슬롯. 기다리지 않고 503 |

### 회계

**원본 바이트 · 아카이브 바이트 · 스테이징 바이트를 따로 센다.** tar 헤더·패딩 때문에 셋은 다르다.
`max_artifact_bytes` 는 원본 바이트고, 업로드 admission 은 `Content-Length`(= 아카이브 바이트)로
판단하므로 **아카이브 허용치 = `max_bytes + max_files × 1024 + 10240`** 으로 환산해서 비교한다.
원본 상한과 직접 비교하면 경계 크기의 정상 묶음이 거절된다. 파일당 1024 는 헤더 512 와 데이터
끝의 512 정렬 패딩이고, 10240 은 `tarfile` 이 닫을 때 채우는 **레코드**(20 × 512)다 — 원본 3584
바이트짜리 묶음의 실제 파일이 10240 바이트가 되므로 트레일러 1024 만으로는 모자란다.

- 상한은 **받기 전에** 예약으로 건다. 발행 때 예약을 실측으로 바꾼다. 예약은 **잡당 하나**이고
  같은 잡이 다시 걸면 더하지 않고 **바꾼다** — 더하면 재시도 한 번에 회계가 두 배가 된다.
- 전체 상한을 넘으면 **새 묶음을 받지 않는다**(`dropped` + `storage_full`). **만료되지 않은 남의
  묶음을 쫓아내지 않는다** — 약속한 것을 지키는 쪽으로 실패한다.
- 예약은 **물리적 삭제가 성공하고 마지막 독자가 닫을 때까지** 유지한다.

### 청소 · M3 와의 경계

- **번들은 자기 시계로만 지운다.** `retention_days_success`·`retention_days_failure` 는 **0 이 될
  수 있고**(`config.py:594`) 0 은 「다음 sweep 에 바로」다(`core/retention.py:82`). M3 청소에 번들을
  얹으면 **합류된 잡이 몇 분 만에 산출물을 잃는다** — 결정 40 위반이다.
  → M3 청소는 번들이 **이미 `purged`·`expired` 일 때만** 그 디렉터리를 지운다.
- 청소기(`janitor.py`)에 sweep 을 하나 더 단다: `expires_at <= now` → **`artifacts/<id>/` 를
  디렉터리째** 삭제 → `expired`. 빈 디렉터리를 남기면 「행 없는 설치 디렉터리」(§9 ④)와 구분되지
  않는다. 대상 상태는 `ready` 와 `uploading` 뿐이고 `purged`·`expired` 는 제외한다.
  주기는 기존 `retention_sweep_interval_seconds`(기본 3600)를 쓴다. 문서에는 「**설정된 sweep
  주기만큼** 늦게 실제로 지워지고, 삭제가 실패하면 더 늦어진다」로 적는다.
- **메타데이터 삭제는 둘 다 사라진 뒤**다. `delete_old_jobs` 는 지금 `jobs.artifacts_purged_at` 만
  본다(`store.py:475`) — 조건에 「`job_artifacts` 행이 없거나 파일·예약이 0」을 더한다. 안 그러면
  삭제에 실패한 번들이 소유 기록과 회계를 잃는다.
- `keep_workspace_on_failure` 는 **건드리지 않는다.**

## 9. DB — 스키마 7

`DB_VERSION` · `_SCHEMA_V1` · `_MIGRATIONS[7]` **셋 다** 고친다. 새 DB 는 마이그레이션을 건너뛰고
`_SCHEMA_V1` 을 그대로 실행한다(`store.py:294`) — 마이그레이션만 고치면 새 DB 에 표가 없다.
`_MIGRATIONS` 는 **문장 하나씩의 튜플**이다(`store.py:173`).

```sql
-- _MIGRATIONS[7] = (문장1, 문장2, 문장3)
ALTER TABLE jobs ADD COLUMN join_count INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS job_artifacts (
  job_id INTEGER PRIMARY KEY,
  state TEXT NOT NULL,
  policy_json TEXT,          -- 잡을 만들 때 얼린 프리셋 정책
  manifest_json TEXT,
  bundle_sha256 TEXT,        -- purged 뒤에도 남는다(재생 판정)
  file_count INTEGER, total_bytes INTEGER, bundle_bytes INTEGER,
  reserved_bytes INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER,
  collected_at REAL, ready_at REAL, expires_at REAL,
  acked_at REAL, purged_at REAL,
  reason_code TEXT, reason_args TEXT, detail_json TEXT
);
CREATE INDEX IF NOT EXISTS job_artifacts_expiry ON job_artifacts(state, expires_at);
```

- **행이 없을 때의 상태는 파생이다.** `job_artifacts` 행이 없으면 — 프리셋에 글롭이 없으면
  `disabled` · 잡이 활성이면 `pending` · 잡이 끝났으면 `unknown`. 「보고하지 않은 옛 워커는
  `unknown` 이지 `empty` 가 아니다」(§3)와 같은 규칙이다. 프리셋이 설정에서 사라진 잡은 무엇이었는지
  알 수 없으므로 `unknown` 이다 — 지어내지 않는다.
- **수집 중 취소는 `only_from` 이 막는다.** `Store.finish` 가 조용히 상태를 바꿔치기하면 기존
  호출자의 뜻이 달라진다. `cancelling` 인 잡에 `finish(succeeded, bundle=…, only_from=(running,))`
  는 **False 를 돌려주고 묶음도 안 남긴다**(원자성). 워커가 다시 판정해 `cancelled` 로 부르면 그때
  묶음이 함께 커밋된다 — 모은 것을 버리지 않는다.
- **잡마다 어는 것**: 프리셋의 `artifacts` 글롭과 **잡당** 상한(`max_artifact_bytes`·
  `max_artifact_files`·예산)이다. 워커 claim 응답에 실어 보내고 업로드 검증도 그것으로 한다.
  **전체 용량과 전송 동시성은 얼지 않는다** — 그건 운영자가 지금 정하는 값이다.
  합류자는 기존 잡의 정책을 물려받는다.
- 파일 설치와 SQLite 커밋은 한 트랜잭션이 될 수 없다. **시작할 때 화해시킨다**:
  ① 고아 스테이징 삭제 ② `collecting`/`uploading` 로 남은 행 → `failed`+`interrupted`, 예약 반납
  ③ `ready` 인데 파일 없음 → `unavailable` ④ **행 없이 남은 `artifacts/<id>/` 디렉터리 삭제**
  (커밋 전에 죽은 경우) ⑤ 영수증만 있고 finish 가 없는 업로드는 **영수증 시각 + TTL** 로
  `expires_at` 이 이미 박혀 있어 sweep 이 가져간다(만료 없는 행을 만들지 않는다).
  **번들이 있다고 해서 `lost` 잡이 성공이 되지는 않는다.**
- 서버 재시작은 원격 활성 잡을 일부러 살려 둔다(`store.py:998`). 그 워커는 복구용 GET 으로
  영수증이 살아 있는지 묻고, 없으면 **다시 올릴 수 있다**(같은 해시면 멱등이다).
- **M5e 이전 잡은 활성이든 종료든 `unknown`** 이고 새로 수집하지 않는다. `DEFAULT 0` 인 `join_count`
  는 과거의 합류를 복원하지 못한다.

## 10. API 문서 (추가만 — 스키마 v1 그대로)

`GET /jobs/{id}` · 큐 · 최근 목록의 잡마다 `artifacts` 객체를 **더한다**:

```json
"artifacts": {"state":"ready","file_count":64,"total_bytes":12812345,"bundle_bytes":12820480,
              "skipped_count":0,"ready_at":"…","expires_at":"…","purged_at":null,
              "reason_code":null,"reason_args":null}
```

- 모르는 수는 `null`. `0` 은 「모았는데 없었다」일 때만.
- **파일 목록·경로·해시는 보호 라우트에서만.** 공개 문서와 이벤트에는 집계만. `reason_args` 에도
  경로를 넣지 않는다(§3).
- `server.artifact_storage` 를 더한다: `{stored_bytes, reserved_bytes, limit_bytes,
  last_sweep_at, error_code}`.
- 이벤트는 **새 종류 `artifacts_changed`** 다. `_publish_job` 을 쓰면 종료 잡에 `job_finished` 가
  다시 나간다(`server.py:367`). 새 이벤트도 **상태 캐시를 무효화**해야 한다.
- **v1 시간 필드의 뜻을 바꾸지 않는다.** 로컬은 `result.finished`(`worker.py:371`), 원격은 서버
  수신 시각(`remote_workers.py:448`)을 `finished_at` 에 넣고 `job_seconds` 가 거기서 나온다
  (`core/status.py:188`). 수집·업로드가 끼면 원격의 `finished_at` 이 밀린다 →
  **원격 워커가 실행 종료 시각을 실어 보내고 서버는 그것을 쓴다.** 산출물 시간은
  `artifacts.ready_at` 으로 따로 본다.

## 11. 클라이언트 — 받아서 트리에 쓰기

**`extract_tree` 를 쓰면 안 된다**(§2).

### 기준선

받은 파일을 트리에 쓸 자격을 정하는 것은 **제출 당시 그 경로의 내용**이다. 제출 경로마다 다르다.

| 제출 방식 | 기준선 |
|---|---|
| 전체 tar | 해시를 잰 뒤 tar 를 만들며 다시 읽는다(`client.py:345,367`) — **실제로 보낸 표현**을 기준선으로 저장한다 |
| 캐시(manifest) | manifest 로 보낸 해시가 기준선. 빠진 blob 만 다시 읽는다(`client.py:540,562`) |
| **합류** | 트리를 **아예 안 보낸다**(`cli.py:314`). 합류 시점에 로컬에서 잰 해시를 기준선으로 쓴다 |
| 기준선 없음 (`rcm artifacts … --fetch`) | 없다. `--output DIR` 을 요구하고, 이미 있는 파일은 `--force` 여야 덮는다 |

### 분류 (기준선 · 로컬 · 받은 것 셋으로 정한다)

| 기준선 | 로컬 | 결과 | 기본 동작 |
|---|---|---|---|
| 없음(제출에 없던 경로) | 없음 | `new` | 쓴다 |
| 없음 | 로컬 = 받은 것 | `unchanged` | 안 쓴다. 바이트가 같으면 쓰든 말든 잃을 게 없다 |
| 없음 | 로컬 ≠ 받은 것 | `conflicted` | 안 쓴다. **기준선이 없다는 것은 덮어쓸 자격이 아니다** |
| 있음 | 로컬 = 받은 것 | `unchanged` | 안 쓴다 |
| 있음 | 로컬 = 기준선 ≠ 받은 것 | `changed` | **쓴다** (골든 갱신의 본체) |
| 있음 | 로컬 ≠ 기준선 (손댔다) | `conflicted` | 안 쓴다. `--force` 여야 쓴다 |
| 있음 | **로컬이 지워졌다** | `conflicted` | 안 쓴다 — 지운 것도 사람이 한 변경이다 |

**표는 위에서부터 먼저 맞는 줄이 이긴다.** 그래서 「기준선이 있고 사람이 고쳤는데 하필 받은 것과
같아진」 경우는 `unchanged` 다 — 어차피 안 쓰고, 「이미 그 내용이다」가 더 정직하다.
`conflicted` 가 하나라도 남으면 ack 를 보내지 않는다(§7). gitignore 되는 실패 diff 처럼 **기준선이
없는 경로가 흔하다** — 그것들이 전부 `conflicted` 가 되면 ack 가 영영 안 나가고 결정 40 이 죽는다.

### 절차

1. **스테이징으로 통째로 받는다.** 디렉터리는 `mkdtemp` 로 만든다 — 예측 가능한 이름은 목적지
   검증 **전에** 만들어지므로 심링크·동시 실행·다른 서버의 같은 잡 번호와 부딪힌다.
   `bundle_sha256` 과 파일별 sha256 을 전부 검증한다. 하나라도 어긋나면 아무것도 쓰지 않는다.
2. **쓰기 전에 목적지를 전수 검사한다** — 경로 조각 어디에도 심링크가 없을 것(디렉터리 fd 로
   내려간다), 트리 밖으로 나가지 않을 것, 정규화 충돌이 없을 것. 그리고 위 표로 분류해 보여 준다.
3. **파일마다 쓰기 직전에 다시 확인한다.** 비교와 교체 사이에 사람이 고쳤으면 그 파일은
   `conflicted` 로 세고 건너뛴다(`--force` 여도 「방금 바뀐 것」은 건너뛴다).
4. 같은 디렉터리에 임시 파일을 만들고 `os.replace`. **64개가 통째로 원자적이지는 않다.**
5. **저널**을 남긴다 — 서버 URL · 잡 번호 · `bundle_sha256` · 목적지 뿌리 · 기준선 · 파일별 적용
   상태. 중간에 죽으면 「64개 중 41개 적용됨」이라고 정직하게 말하고,
   **`rcm artifacts JOB_ID --fetch --resume`** 로 이어서 한다. `rcm run` 을 다시 치는 것은 복구가
   아니다 — **새 잡**이 만들어진다.
6. 충돌 없이 전부 적용됐을 때만 ack 를 보낸다(§7).

- 비교는 **바이트**로 한다. 시각·픽셀 비교는 하지 않는다.
- 매니페스트에 없는 로컬 파일은 **지우지 않는다**.

## 12. CLI

| 명령 | 뜻 |
|---|---|
| `rcm run PRESET --fetch-artifacts` | 끝나면 **제출한 그 트리 뿌리**에 받아 쓴다 |
| `… --fetch-artifacts --force` | `conflicted` 까지 덮는다(방금 바뀐 파일은 제외) |
| `… --fetch-artifacts --dry-run` | 분류 표만 찍는다. 쓰지도, ack 하지도 않는다 |
| `rcm artifacts JOB_ID` | 상태와 매니페스트 |
| `rcm artifacts JOB_ID --fetch --output DIR` | **디렉터리를 명시해야** 받는다(기준선이 없다) |
| `rcm artifacts JOB_ID --fetch --resume` | 저널을 읽어 이어서 적용한다 |

- `--no-wait --fetch-artifacts` 는 **제출 전에** 거부한다.
- 그냥 `--no-wait` 로 냈으면 끝에 받아 가는 명령 한 줄을 찍는다.
- `git_ref` 잡은 대응하는 로컬 트리가 없다 → `--output DIR` 을 요구한다.
- 결과 줄: `artifacts: 64 files · 12.4 MB · wrote 12, unchanged 51, conflicted 1`.
  **「12」가 「비교해서 다른 것」인지 「실제로 쓴 것」인지 단어로 구분해 적는다**(`wrote`).
- 종료 코드: 기존 `rcm wait` 규칙과 `wait_exit_code`(`cli.py:446`)는 **그대로**다. 전달 실패는
  JSON 의 `artifact_fetch` 필드와 **별도 종료 코드**로 낸다. **실행 실패가 전달 실패보다
  우선한다** — 테스트가 깨진 것을 먼저 알아야 한다. 실행은 성공했는데 전달이 실패하면 전체를
  성공으로 끝내지 않는다.
- 프리셋에 `artifacts` 가 없는데 `--fetch-artifacts` 를 켰으면 **종료 코드 0** 이다 — 받을 것이
  없는 것은 전달 실패가 아니다. 한 줄로 그 사실만 알린다.
- `--dry-run` 은 충돌이 있어도 **종료 코드 0** 이다(미리보기다). 쓰지도, ack 하지도 않는다.
- `--no-wait` 뒤 찍는 안내는 `rcm artifacts <id> --fetch --output <dir>` 형태다.
- CLI 도움말은 영어 그대로다.

## 13. 화면 (마지막 PR)

- 최근 결과 행에 산출물 한 줄: 상태 · 파일 수 · 바이트 · 남은 시간, 그리고 받아 가는 명령
  한 줄(복사용). 매니페스트는 펼쳐서 본다.
- 실패·버림은 원래 결과 **옆에** 보인다. 결과를 가리지 않는다.
- `unknown` 은 `—` 로 찍는다. **`0 files`·`0 MB` 로 찍지 않는다** — 모르는 것과 없는 것은 다르다.
  `empty` 만 `0` 이다.
- `web/i18n.js` 에 한국어·영어 둘 다(결정 36). 상태 12개와 이유 코드마다 문장 하나씩.
- 브라우저에서 트리 복원은 **안 만든다**. 이미지 갤러리도 아니다.
- M5d-2/3 가 `web/` 를 크게 바꾸는 중이다 — **화면 작업은 맨 마지막에 얹는다.**

## 14. PR 순서

각 PR 이 **자기 문서를 같은 PR 안에서** 가져간다(`CONTRIBUTING.md`). `docs/documentation.md` 의
「What a pull request updates」대로 **CHANGELOG `[Unreleased]`(PR 링크 포함)** 와, 설정 키를 더한
PR 은 **`examples/server.toml` 및 같은 내용의 패키지 템플릿**도 함께 고친다(문면 잠금 테스트가
확인한다).

1. **코어·저장** — 글롭 규칙(`core/`), 정책 고정, 스키마 7(`_SCHEMA_V1` 포함), 예약·회계, 불변
   묶음, 재시작 화해, TTL 청소기와 M3 경계. + `docs/configuration.md` · `docs/operating.md` ·
   `examples/server.toml`.
2. **워커 두 경로** — 로컬·원격 수집, 업로드·복구 라우트, 완료·취소 순서, 구조화된 처분 보고,
   상태 노출. **둘을 같이 낸다**(하나만 내면 `pool` 을 쓰는 순간 조용히 빈다).
3. **클라이언트** — 스트리밍 받기·검증·분류·적용·저널·ack, CLI. + `docs/usage.md` ·
   `docs/usage.ko.md`.
4. **화면·인수** — 상태 표시, i18n, 스크린샷, 격리 에이전트 종단 시나리오.

## 15. 테스트 (구현 전에 빨간불로 만든다 — 격리 에이전트, `src/` 금지)

1. **로컬 데이터 보호** — `..` · 절대 경로 · `.git` · 부모가 심링크로 바뀌는 경우 · 하드링크 ·
   특수 파일 · 디렉터리 접두 충돌 · 정규화 충돌 · **비교와 쓰기 사이에 목적지가 바뀐 경우** ·
   로컬에서 지워진 파일 · 기준선 없는 기존 파일. 안전하지 않으면 **아무것도 쓰지 않는다.**
2. **전달 중단과 재시도** — 끊김 · 잘림 · 해시 불일치 · 클라이언트 디스크 꽉 참 · 부분 적용 후
   `--resume`. **TTL 이 지나지 않는 한** ack 전에 안 지운다. 잘못된 해시 ack 는 409, `purged` 뒤
   같은 해시 재생은 200, 충돌이 남았거나 `--dry-run` 이면 ack 를 **안 보낸다**.
3. **자격과 삭제 규칙** — 토큰 둘 · 한 토큰의 두 세션 · 떠난 합류자(종료 뒤 이탈 포함) · 관리자
   ack 는 no-op · 워커 거부 · `read_auth=none` 에서도 토큰 요구. **`join_count == 0` 이면 ack 로
   즉시 사라지고, 합류가 있었으면 ack 뒤에도 남아 둘 다 받는다.** `--no-join` 뒤 남이 합류하는
   경우, 요청자 재제출, 같은 합류자 재제출 모두 카운트가 는다. 파일 이름이 공개 문서·이벤트·
   `reason_args` 로 새지 않는다.
4. **두 워커의 생애** — 진짜 프로세스로 성공 산출물과 실패 diff 를 만든다. 수집 중 취소(취소가
   이긴다) · 타임아웃 · 느린 업로드 · heartbeat 누락 · 모호한 finish 응답 · 복구 GET 이 종료된
   잡에도 답함 · 서버 재시작. **부분 묶음을 노출하거나 `lost` 잡을 되살리지 않는다.**
   보고 필드가 없는 옛 워커는 `unknown` 이지 `empty` 가 아니다.
5. **상한·보존·복구** — 동시 예약 · 원본/아카이브/스테이징 회계가 각각 맞음 · 경계 크기 묶음이
   헤더 때문에 거절되지 않음 · 파일 수 상한 · 디스크 꽉 참 · 커밋 실패 · 고아 스테이징 ·
   **행 없는 설치 디렉터리** · **finish 없는 영수증이 만료로 회수됨** · unlink 실패 · 만료를
   가로지르는 진행 중 다운로드(끝까지 간다, 공간은 그 뒤에 돌아온다) ·
   **`retention_days_success = 0` 에서도 24시간이 지켜진다** · 기존 로그·워크스페이스 보존 불변.
6. **호환과 정직성** — 새 DB 가 스키마 7 · 6→7 마이그레이션 · 재개봉 · 옛 잡은 `unknown` ·
   v1 키와 뜻 유지(원격 `finished_at`·`job_seconds` 가 수집 때문에 밀리지 않음) · 프로세스 실패가
   산출물 성공에 묻히지 않고 전달 실패가 프로세스 성공에 묻히지 않음 · CLI/UI 가 `null` 을 `—` 로.

전체 검사: `ruff check . && ruff format --check . && pytest` · `node --test tests/web/*.test.js` ·
`python scripts/mutcheck.py` · `scripts/smoke_install.sh`.

## 16. 완료 기준

1. 골든 프리셋을 돌리면 낸 세션이 파일을 **자기 트리의 같은 경로**에 받고, 몇 개를 **썼는지**
   화면에 나온다.
2. 확인이 오기 전에는 사라지지 않는다(TTL 전까지). 받다가 끊으면 남아 있고 다시 받을 수 있다.
3. **아무도 안 붙은 잡은 확인 뒤 즉시 사라지고**, 합류가 있었던 잡은 24시간까지 남아 둘 다 받는다.
4. 아무도 안 받으면 TTL 이 지나 청소기가 지운다. `retention_days_*` 를 0 으로 둬도 그렇다.
5. 글롭 밖 · 워크스페이스 밖 · 링크는 애초에 안 모인다.
6. 상한을 넘으면 **잡은 그대로 성공/실패하고** 산출물만 버려졌다는 사실이 결과에 남는다.
7. 원격 워커 풀에서도 똑같이 된다. 옛 워커는 `unknown` 으로 남지 조용히 비지 않는다.
8. 실패한 잡의 diff 이미지도 돌아온다.
9. 기존 테스트 전부 초록 · 스키마 v1 그대로 · 런타임 의존성 0 그대로.

## 17. 이번에 **안** 만드는 것

산출물 캐시·중복 제거(다음에 하면 「64장 중 12장만 전송」이 된다) · 잡당 여러 버전 ·
range/재개 **다운로드**(적용 재개는 §11 에 있다) · `lost` 잡에 늦게 올리기 · 서버측 이미지 비교 ·
임의 경로 매핑 · 브라우저에서 트리 복원 · 매니페스트에 없는 로컬 파일 삭제.

## 18. 잠근 API 표면 — 테스트가 이 이름으로 쓰인다

테스트를 먼저 쓰는 사람들이 서로 다른 이름을 지어내면 나중에 전부 고쳐야 한다. **여기 적힌 이름과
서명이 계약이다.** 구현이 이걸 바꾸려면 이 문서를 먼저 고친다.

### `core/artifacts.py` — 순수 규칙 (I/O 없음)

```python
MAX_COMPONENT_BYTES = 255  # 경로 조각 (UTF-8 인코딩 바이트)
TAR_HEADER_BYTES = 512  # 헤더
TAR_PER_FILE_BYTES = 1024  # 헤더 + 데이터 끝의 512 정렬 패딩
TAR_RECORD_BYTES = 10240  # tarfile 이 닫을 때 채우는 레코드(20 x 512)

DISABLED = "disabled"
PENDING = "pending"
COLLECTING = "collecting"
UPLOADING = "uploading"
READY = "ready"
EMPTY = "empty"
DROPPED = "dropped"
FAILED = "failed"
SKIPPED = "skipped"
PURGED = "purged"
EXPIRED = "expired"
UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"
ARTIFACT_STATES: frozenset[str]  # 위 13개
GONE_STATES = frozenset({PURGED, EXPIRED})  # 아카이브 GET 이 410
PROGRESS_STATES = frozenset({PENDING, COLLECTING, UPLOADING})  # 409
NOTHING_STATES = frozenset({DISABLED, EMPTY, SKIPPED, UNKNOWN})  # 404
REASONS: frozenset[str]  # §3 의 16개


class ArtifactError(Exception): ...


class PolicyError(ValueError): ...  # 글롭·경로가 규칙에 안 맞는다


@dataclass(frozen=True)
class ArtifactPolicy:
    globs: tuple[str, ...] = ()
    max_bytes: int = 1_073_741_824
    max_files: int = 10_000
    timeout_seconds: int = 60
    cancel_timeout_seconds: int = 5

    def enabled(self) -> bool: ...  # globs 가 비어 있지 않다


@dataclass(frozen=True)
class BundleFile:
    path: str
    size: int
    sha256: str
    mode: int  # mode 는 0o644 / 0o755


@dataclass(frozen=True)
class AckDecision:
    status: int  # 200 · 409 · 410 — 검사 순서는 만료 → 자격 → 해시 → 상태 (§7)
    purge: bool  # 지금 지워도 되는가
    record: bool  # acked_at 을 쓸 것인가 (admin no-op 은 False)
    reason_code: str | None


def validate_globs(globs: Sequence[str]) -> tuple[str, ...]: ...  # PolicyError
def compile_globs(globs: Sequence[str]) -> tuple[re.Pattern[str], ...]: ...  # fullmatch 앵커
def check_path(path: str) -> str: ...  # PolicyError
def collision_key(path: str) -> str: ...  # NFC → casefold
def collisions(paths: Iterable[str]) -> list[tuple[str, str]]: ...  # 디렉터리 접두 포함, 정렬
def select(paths: Iterable[str], globs: Sequence[str]) -> list[str]: ...  # 정렬, .git 제외
def archive_allowance(max_bytes: int, max_files: int) -> int:
    ...
    # max_bytes + max_files * TAR_PER_FILE_BYTES + TAR_RECORD_BYTES   (§8)


def classify(baseline: str | None, local: str | None, incoming: str) -> str:
    ...
    # -> "new" | "unchanged" | "changed" | "conflicted"   (§11 표 그대로)


def ack_decision(
    *,
    state: str,
    join_count: int,
    owner: bool,
    stored_sha: str | None,
    sent_sha: str,
    expired: bool,
) -> AckDecision: ...
def expires_at(ready_at: datetime, hours: int) -> datetime: ...
```

### `core/retention.py` — 추가

```python
@dataclass(frozen=True)
class BundleInfo:
    job_id: int
    state: str
    expires_at: datetime | None
    bytes: int


def bundles_to_expire(bundles: Iterable[BundleInfo], now: datetime) -> list[BundleInfo]:
    ...
    # expires_at 이 있고 <= now 인 `ready`·`uploading` 만. 만료 없는 행은 절대 대상이 아니다.
    # purged·expired 는 제외. job_id 오름차순. `bytes` 는 디스크가 실제로 쥔 아카이브 바이트다
```

### `collect.py` — 수집기 (워커 쪽 I/O, 로컬·원격 공용)

```python
@dataclass(frozen=True)
class CollectResult:
    state: str
    files: tuple[BundleFile, ...] = ()
    total_bytes: int = 0
    bundle_bytes: int = 0
    skipped_count: int = 0
    bundle_sha256: str | None = None
    bundle_path: Path | None = None
    reason_code: str | None = None
    reason_args: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None  # 경로가 들어간다 — 공개하지 않는다
    # dropped·failed 는 files 를 비운다(()) — 그 목록이 곧 남의 트리 구조다


def open_anchored(root: Path, relpath: str) -> int:
    ...
    # 워크스페이스 fd 에서 조각마다 O_DIRECTORY|O_NOFOLLOW 로 내려가고,
    # 마지막은 O_NOFOLLOW|O_NONBLOCK 으로 연다(O_NONBLOCK 없이 FIFO 를 열면 워커가 선다).
    # 조각 하나라도 심링크면 ArtifactError. 일반 파일이 아니면 ArtifactError.


def collect(
    workspace: Path,
    policy: ArtifactPolicy,
    staging: Path,
    *,
    now_fn=...,  # -> datetime (이 저장소의 now_fn 은 언제나 datetime 이다)
    clock=time.monotonic,  # -> float. 예산은 이쪽으로 잰다 (벽시계가 튀어도 안전하다)
    budget_seconds: int | None = None,
) -> CollectResult:
    ...
    # 예산·상한을 넘으면 dropped, 워크스페이스가 없으면 skipped,
    # 글롭에 하나도 안 맞으면 empty + no_match,
    # 맞았지만 전부 건너뛰었으면 empty + reason_code None + skipped_count > 0
```

### `store.py` — `DB_VERSION = 7`

```python
def get_bundle(self, job_id: int) -> dict[str, Any] | None:
    ...
    # 시각은 UTC aware datetime, reason_args 는 푼 dict (Store 의 다른 공개 값과 같다)


def start_collect(self, job_id: int, policy: ArtifactPolicy, now: datetime) -> None: ...
def reserve_bundle_bytes(self, job_id: int, want: int, limit: int, now: datetime) -> bool: ...
def release_bundle_bytes(self, job_id: int) -> None: ...
def bundle_storage_totals(self) -> tuple[int, int]: ...  # (stored, reserved)
def publish_bundle(self, job_id: int, result, *, now, expires_at) -> None: ...
def set_bundle_failed(
    self, job_id: int, state: str, reason_code: str, reason_args: dict | None, now: datetime
) -> None: ...
def ack_bundle(self, job_id: int, sent_sha: str, *, owner: bool, now: datetime) -> AckDecision: ...
def bundles_due(self, now: datetime, limit: int = 1000) -> list[BundleInfo]: ...
def mark_bundles_purged(self, job_ids, state: str, now: datetime) -> int: ...
def reconcile_bundles_on_start(self, now: datetime) -> tuple[list[int], list[int]]:
    ...
    # (interrupted 로 닫은 것, unavailable 로 표시한 것)
```

`Store.finish(...)` 에 `bundle: CollectResult | None = None` 을 **추가**한다 — 종료 상태와 묶음이
한 트랜잭션에서 커밋된다. `join_or_bump` 는 합류가 성립할 때마다 `jobs.join_count` 를 1 늘린다.

### 서버 · 설정

- 라우트·상태 코드는 **§6 표가 계약이다.**
- `ServerConfig` 새 필드: `artifact_retention_hours=24` · `max_artifact_bytes=1_073_741_824` ·
  `max_artifact_files=10_000` · `artifact_storage_max_bytes=10_737_418_240` ·
  `artifact_timeout_seconds=60` · `artifact_cancel_timeout_seconds=5` ·
  `artifact_transfer_timeout_seconds=300` · `max_concurrent_artifact_transfers=2`.
  `Preset` 새 필드: `artifacts: tuple[str, ...] = ()`.
- 잡 JSON 의 키는 **§10 이 계약이다**(`artifacts` 객체 · `server.artifact_storage`).
- 이벤트 종류는 `artifacts_changed`.

### `apply.py` — 클라이언트 적용

```python
@dataclass(frozen=True)
class Entry:
    path: str
    verdict: str
    incoming_sha: str
    baseline_sha: str | None = None
    local_sha: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ApplyPlan:
    entries: tuple[Entry, ...]

    def counts(self) -> dict[str, int]: ...  # new · unchanged · changed · conflicted
    def safe(self) -> bool: ...  # unsafe 가 하나도 없다


@dataclass(frozen=True)
class ApplyResult:
    wrote: int
    skipped: int
    conflicted: int
    failed: int
    complete: bool  # 충돌 없이 전부 적용됐다 → ack 자격


def plan(files, baseline: Mapping[str, str], root: Path) -> ApplyPlan: ...
def apply(
    plan: ApplyPlan, staging: Path, root: Path, *, force: bool = False, journal: Path | None = None
) -> ApplyResult: ...
def read_journal(path: Path) -> dict[str, Any] | None: ...
def write_journal(path: Path, doc: dict[str, Any]) -> None: ...
```

### `client.py` · CLI

```python
class Client:
    def artifacts(self, job_id: int) -> dict[str, Any]: ...
    def download_bundle(self, job_id: int, dest: Path) -> int: ...  # 스트리밍, .part → 교체
    def ack_artifacts(self, job_id: int, bundle_sha256: str) -> dict[str, Any]: ...


class WorkerClient:
    def upload_bundle(self, job_id: int, tar_path: Path) -> dict[str, Any]: ...
    def bundle_status(self, job_id: int) -> dict[str, Any]: ...
```

CLI 는 §12 표가 계약이다. `rcm run … --fetch-artifacts [--force] [--dry-run]` ·
`rcm artifacts JOB_ID [--fetch --output DIR] [--resume]`. 전달 실패 종료 코드는 **5**
(`wait_exit_code` 는 그대로 두고 JSON 에 `artifact_fetch` 를 더한다).

## 19. 완료 기준 대조 (2026-09-09)

§16 의 아홉 줄을 실제로 대조했다. 「했다」가 아니라 **무엇이 그것을 잠그는가**를 적는다 — 시험
이름이 없는 줄은 완료가 아니다.

| # | 완료 기준 | 결과 | 잠그는 것 |
|---|---|---|---|
| 1 | 낸 세션이 자기 트리의 같은 경로에 받고, 몇 개를 **썼는지** 화면에 나온다 | ✅ | `test_cli_m5e::test_fetch_artifacts_writes_the_goldens_back_into_the_submitted_tree` · `…::test_the_result_line_says_how_many_were_written_not_just_compared` |
| 2 | 확인 전에는 사라지지 않는다. 받다가 끊으면 남아 다시 받을 수 있다 | ✅ | `test_cli_m5e::test_an_incomplete_apply_never_acks` · `test_apply::test_a_resumed_apply_finishes_the_rest` · `test_server_m5e::test_archive_is_410_after_purge_expiry_or_past_the_expiry_moment` |
| 3 | 아무도 안 붙은 잡은 확인 뒤 즉시 사라지고, 합류가 있었으면 TTL 까지 둘 다 받는다 | ✅ | `test_server_m5e::test_ack_of_an_unjoined_job_purges_the_bundle_immediately` · `…::test_ack_of_a_joined_job_keeps_the_bundle_until_the_ttl` · `test_store_m5e` 의 `join_count` 다섯 줄 |
| 4 | 아무도 안 받으면 TTL 이 지나 청소기가 지운다. `retention_days_* = 0` 이어도 그렇다 | ✅ | `test_janitor_m5e::test_the_expiry_sweep_deletes_at_the_expiry_moment_and_marks_expired` · `…::test_retention_days_success_zero_does_not_destroy_a_bundle_before_its_ttl` |
| 5 | 글롭 밖 · 워크스페이스 밖 · 링크는 애초에 안 모인다 | ✅ | `test_collect::test_a_symlinked_directory_is_not_followed` · `…::test_symlinks_hardlinks_and_fifos_are_skipped_and_counted` · `open_anchored` 9줄 · `test_apply::test_an_unsafe_plan_writes_nothing_at_all_not_even_the_safe_entries` |
| 6 | 상한을 넘으면 잡은 그대로 성공/실패하고 산출물만 버려진 사실이 남는다 | ✅ | `test_collect::test_over_max_bytes_is_dropped_with_over_bytes` · `test_worker_m5e::test_a_collection_budget_overrun_drops_the_bundle_but_not_the_job` · `test_store_m5e::test_finish_records_a_dropped_bundle_next_to_a_successful_job` |
| 7 | 원격 워커 풀에서도 똑같다. 옛 워커는 `unknown` 이지 조용히 비지 않는다 | ✅ | `test_worker_m5e::test_the_remote_worker_uploads_before_finishing` · `…::test_a_finish_without_an_artifacts_field_reads_back_as_unknown` · `test_server_m5e::test_an_old_worker_that_reports_no_artifacts_field_is_unknown_not_empty` |
| 8 | 실패한 잡의 diff 이미지도 돌아온다 | ✅ | `test_worker_m5e::test_a_failed_job_is_collected_too` · `test_cli_m5e::test_failed_jobs_still_deliver_their_diff_images` |
| 9 | 기존 시험 전부 초록 · 스키마 v1 그대로 · 런타임 의존성 0 그대로 | ✅ | 2406 passed · `test_compat_m5e::test_the_artifacts_object_has_exactly_the_documented_keys`(v1 키 추가만) · `test_packaging`(`dependencies == []`) |

**§17 에서 「안 만든다」고 한 것은 전부 안 만들었다.** 산출물 캐시·중복 제거, 잡당 여러 버전,
range 다운로드, `lost` 잡에 늦게 올리기, 서버측 이미지 비교, 브라우저에서 트리 복원, 매니페스트에
없는 로컬 파일 삭제.

### 명세가 틀렸던 곳 (구현하며 고친 것)

| 자리 | 초고 | 고친 뒤 |
|---|---|---|
| §11 2행 | 기준선 없는 파일은 **내용과 무관하게** `conflicted` | 바이트가 같으면 `unchanged`. 아니면 gitignore 되는 실패 diff 때문에 ack 가 영영 안 나가고 결정 40 이 죽는다 |
| §8 아카이브 허용치 | `max_bytes + files×512 + 1024` | `+ files×1024 + 10240`. `tarfile` 이 닫을 때 레코드까지 채워서 원본 3584바이트 묶음의 실제 파일이 10240바이트다 — 옛 식이면 정상 묶음이 413 으로 거절된다 |
| §4 앵커 | `^…$` | `re.fullmatch`. 파이썬의 `$` 는 끝의 개행 앞에서도 맞아 `goldens/evil.png\n` 이 통과한다 |
| §4 여는 방법 | `O_NOFOLLOW` | `O_NOFOLLOW\|O_NONBLOCK`. 없으면 FIFO 하나에 워커가 선다 |
| §5 워커 보고 | `finish` 에 해시 하나 | 구조화된 처분. 해시만으로는 빈 수집·시한 초과·건너뛴 수를 말할 수 없다 |
| §6 ack 검사 순서 | 만료 → 자격 → 해시 | 만료 → **해시** → 상태 → 자격. 남의 잡을 확인하려는 관리자에게도 해시가 틀렸다는 사실을 먼저 알려야 한다 |
| §10 `empty` 의 수 | 미정 | `0`. 「모았는데 없었다」는 아는 사실이라 `null` 이 아니다 |
| — | 매니페스트를 `finish` 본문으로 | 서버가 올라온 tar 에서 본 것으로. 파일 만 개 목록은 JSON 본문 상한(64KB)을 넘는다 |
| — | 업로드한 스풀을 바로 삭제 | 남긴다. 전송 결과가 불확실하면 다시 보내야 한다. 시작할 때 나이로 쓸어 간다 |
