# M5g 작업 명세 — 실패한 잡의 증거를 얼마나, 얼마나 오래 들고 있을지

> 오너 요청(2026-09-09): 「지금은 조절값이 날짜뿐이고, 그 날짜가 오기 전에 디스크가 먼저 찬다.」
>
> 한 줄 요약: **날짜 하나가 로그와 워크스페이스를 함께 지배하는 것을 끊고**, 날짜와 무관한
> **바이트 예산**과 **여유 공간 바닥**을 둔다. 그리고 그 셋이 무엇을 쥐고 있는지 화면에 싣는다.
>
> 바꾸지 않는 것: 런타임 의존성 0 · 스키마 v1(**키를 더하지 값을 바꾸지 않는다**) · **DB 스키마
> 그대로**(마이그레이션 없음) · 활성 잡 삼중 보호 · 순수 계층은 I/O 도 시계도 안 본다 ·
> `/worker/*` 프로토콜 · 잡 로그는 언제나 토큰.

## 1. 이 기능이 답해야 하는 것

| 질문 | 오늘 | M5g |
|---|---|---|
| 실패한 잡의 로그를 30일 들고 있고 싶다. 워크스페이스도 30일이어야 하나? | 그렇다. `retention_days_failure` 하나가 둘 다 지배한다 | 아니다. 로그 30일 · 워크스페이스 `workspace_retention_days`(1) |
| 하루에 50개씩 실패하면 30일 뒤에 무슨 일이 나나? | 750 GB 가 필요하다. 여유는 628 GB — **보존 기간이 한 번도 발동하기 전에 디스크가 찬다** | 예산(`workspace_storage_max_bytes`)과 바닥(`min_free_bytes`)이 날짜보다 먼저 잡는다 |
| 데이터 디렉터리가 지금 얼마를 쥐고 있나? | **어디에도 안 나온다.** `du` 를 직접 쳐야 안다 | `/api/status` · `/api/health` · `rcm check` · 웹 호스트 카드 |
| 다음 청소가 언제인가? | 안 나온다(`server.artifact_storage.last_sweep_at` 은 **항상 null** — §3.1 A) | `last_sweep_at` · `next_sweep_at` |
| 지금 당장 지우고 싶다 | 서버를 재시작하거나 한 시간을 기다린다 | `rcm gc [--dry-run]` |
| 실패한 잡의 로그에 「왜 깨졌는지」가 없다 | 프리셋이 무거운 구간 출력을 TMPDIR 로 돌리고 지운다 | 정본 권고(§6) + 프리셋 키 `artifacts_on = "failure"` |

## 2. 실측

### 2.1 오너가 잰 값 (2026-09-09, Mac mini 운영 인스턴스)

| 무엇 | 값 |
|---|---|
| 보존 설정 | 운영 `server.toml` 은 `recent_count = 10` 만 바꿨고 나머지는 전부 코드 기본값 |
| 워크스페이스 | 62개 27 GB(평균 435 MB · 최근 게이트 720 MB · 최대 1.6 GB) |
| 잡 로그 | 137개 39 MB |
| 파일 시스템 | 여유 628 GiB / 926 GiB |
| 보존 대상(비성공) 잡 | 하루 11 · 50 · 11 개(9/7 · 9/8 · 9/9 일부) |
| 산수 | 하루 50개 × 30일 × 0.5 GB ≈ **750 GB > 여유 628 GB** |
| 가장 오래된 잡 | 이틀 전 — 아직 아무것도 안 지워졌다(janitor 는 살아 있다: `/api/health.janitor = true`) |
| 실패 잡 #133 의 로그 | 50,788 바이트 안에 `Expected:` 0줄 · `Actual:` 0줄 · 스택트레이스 0줄 |

### 2.2 이 명세가 더 잰 것 (같은 머신, 읽기만)

`~/.local/share/rcm` 을 `du -sk` 로 한 번씩 훑었다(운영 데이터를 **읽기만** 했다).

| 디렉터리 | 크기 | 항목 수 | 전체 대비 |
|---|---|---|---|
| `workspaces/` | 30,215,656 KiB (28.8 GiB) | 65개 · 파일 380,570개 | **98.0%** |
| `jobs/` | 40,704 KiB (39.8 MiB) | 146개 | 0.13% |
| `blobs/` | 279,728 KiB (273 MiB) | 256개 | 0.9% |
| `mirrors/` | 309,564 KiB (302 MiB) | 2개 | 1.0% |

- **부피는 워크스페이스가 전부다.** 로그·blob·미러를 다 합쳐도 2% 다. 예산이 재고 지울 대상은
  워크스페이스이지 「데이터 디렉터리 전체」가 아니다(결정 52).
- `jobs/<id>/` 는 보통 `log.txt` 하나다. 하지만 **`tree.tar.gz` 가 들어 있을 수 있다** — 캐시를
  안 쓰는 클라이언트(`--no-cache`)의 업로드는 tar 로 오고 최대 `max_snapshot_bytes`(512 MB)다.
  이 머신에서 가장 큰 잡 디렉터리는 3.3 MB(`jobs/2/`, tar 3.4 MB)였다. 부피 축에 같이 넣어야
  이 예산이 다른 설치에서도 참이 된다(결정 51·52).
- **재는 비용**: `workspaces/`(65개 · 38만 파일) 한 번 훑는 데 **6.65~7.14초**(sys 2.3~2.6초).
  워크스페이스 하나당 0.11초, 파일 하나당 18 µs. 캐시가 더워도 같다 — 시스템 콜이 비용이다.
  한 시간에 한 번이면 0.2% 지만, **매 sweep 마다 전부 훑는 설계는 데이터가 20배가 되면 140초**다.
  종료된 잡의 워크스페이스는 다시 안 변하므로 **한 번 재고 기억한다**(§4.4).

## 3. 지금 상태 (전수 조사)

| 무엇 | 어디 | 오늘 |
|---|---|---|
| 보존 규칙(순수) | `core/retention.py` `retention_seconds` · `due_for_purge` | 상태 → 기간 하나. 로그와 워크스페이스를 **가르지 않는다** |
| 실행 | `janitor.py` `sweep_once` → `_purge_job` | `jobs/<id>/` 와 `workspaces/<id>/` 를 **함께** 지우고 `artifacts_purged_at` 을 찍는다 |
| 활성 잡 보호 | 순수 규칙(`retention_seconds` → None) · `_purge_job` 재확인 · `mark_artifacts_purged` 의 UPDATE 조건 | 삼중. mutcheck ⑦(`retention-active-guard`)이 지킨다 |
| 설정 | `config.py:91-104` `retention_days_success`(14) · `retention_days_failure`(30) · `metadata_retention_days`(180) · `retention_sweep_interval_seconds`(3600) | 날짜뿐. **바이트 축이 없다** |
| 검증 | `config.py:650-668` | `>= 0` · `metadata_retention_days >= max(sample_days, retention_days_*)` |
| 바이트 예산의 선례 ① | `snapshot_cache_max_bytes`(4 GiB) → `core/retention.blobs_to_purge` | 참조된 blob 은 절대 안 지우고, 넘으면 오래된 것부터 |
| 바이트 예산의 선례 ② | `artifact_storage_max_bytes`(10 GiB) → `store.reserve_bundle_bytes` | 꽉 차면 **새 묶음을 버린다**. 남의 묶음을 쫓아내지 않는다(결정 41) |
| 회계가 DB 에 있는 것 | `job_artifacts.bundle_bytes`·`reserved_bytes`(`store.py:721`) · `blobs.size`(`:1183`) | 번들과 blob 만. **워크스페이스·로그는 회계가 없다** |
| 호스트 표본의 디스크 | `hostsample._disk_usage`(`:86`) → `hosts[].disk.{used,free,total,path}_bytes` | `shutil.disk_usage(data_dir)`. 못 읽으면 그 칸만 None |
| 「가득 참」 판정 | 웹 `app.js:31` `DISK_LOW_FREE = 10 GiB` · `:1159` `disk >= 85` | **경고**용 두 기준. `rcm top` 은 `render_text._disk` 로 숫자만 |
| 산출물 회계 표시 | `server.artifact_storage`(`server.py:652`) → `{stored,reserved,limit}_bytes · last_sweep_at · error_code` | 이 모양을 그대로 따라간다(§5.1) |
| 수동 청소 | 없다 | 서버 재시작(시작 직후 sweep) 말고는 방법이 없다 |
| admin 라우트 | `POST /pause` · `/resume`(`server.py:1941`) · `/jobs/{id}/priority` | 최상위 admin 라우트의 선례가 있다 |
| 프리셋 산출물 | `presets[].artifacts` 글롭(M5e) → 종료 시 수집 → 24시간 묶음 | **성공·실패를 안 가린다**. 조건이 없다 |

### 3.1 설계를 정하는 발견

**A. `server.artifact_storage.last_sweep_at` 은 언제나 `null` 이다 — 기존 버그.**
`server.py:660` 이 `getattr(self, "janitor", None)` 로 청소기를 찾는데 `App` 의 속성 이름은
`self.retention` 이다(`server.py:220`·`:267`). `self.janitor` 는 어디에도 없다(`_janitor` 는 원격
워커 판정 스레드다). 그래서 마지막 청소 시각이 **한 번도 화면에 나온 적이 없다.** 테스트는 키가
있는지만 본다(`tests/test_server_m5e.py:876`). 「다음 청소가 언제인가」를 이 마일스톤이 싣기로 했으니
**PR 2 에서 이 한 줄을 먼저 고친다**(회귀 테스트: 값이 실제로 채워지는지).

**B. 원격 워커는 자기 워크스페이스를 시작할 때 한 번만 지운다.**
`remote_worker._sweep_workspaces`(`:596`)는 `run()` 초입에서만 돌고, 기준은 설정이 아니라 상수
`WORKSPACE_KEEP_DAYS = 7`(`:60`)이다. **launchd 로 몇 주씩 살아 있는 워커는 그 사이 한 번도 청소하지
않는다.** 이 Mac 의 `mac2` 워커는 워크스페이스가 0개라 지금은 안 아프지만, 워커가 무거운 잡을 받는
설치에서는 서버 쪽 예산이 아무리 옳아도 워커 디스크가 찬다. **M5g 범위에 넣지 않는다**(서버가 워커
디스크를 지울 권한이 없다) — 대신 §7 에 적고 `rcm check` 의 워커 행이 볼 수 있게 남긴다. 별도 이슈다.

**C. 부피와 증거의 크기 차가 1만 배다.** 실패 잡 하나가 남기는 것: 로그 50 KB, 워크스페이스 720 MB.
같은 날짜를 줄 이유가 없다는 것이 이 마일스톤의 출발점이고, 숫자가 그것을 그대로 말한다.

**D. 스냅샷 tar 은 종료된 잡에서 아무도 안 읽는다.** `jobs/<id>/tree.tar.gz` 를 읽는 곳은 자재화
(`worker.py:391`)와 원격 워커의 트리 내려받기(`remote_workers.py:496`) 둘뿐이고 **둘 다 활성 잡에서만**
일어난다. 종료된 잡의 tar 은 감사 기록 이상이 아니다 — 부피 축에 넣는다(결정 51).

## 4. 설계

### 4.1 두 시계와 하나의 예산

무엇이 **증거**이고 무엇이 **부피**인지 먼저 가른다. 이 구분이 이 마일스톤의 전부다.

| 분류 | 파일 | 시계 | 예산·바닥이 지우나 |
|---|---|---|---|
| **증거** | `jobs/<id>/log.txt` · `manifest.json` · DB 의 잡 행·이벤트 | `retention_days_success`(14) · `retention_days_failure`(30) · `metadata_retention_days`(180) — **그대로** | **아니다.** 어떤 압박에서도 증거는 안 지운다 |
| **부피** | `workspaces/<id>/` · `jobs/<id>/tree.tar.gz` | `workspace_retention_days`(1) | 그렇다. 오래된 종료 잡부터 |
| 잡 산출물 묶음 | `artifacts/<id>/` | `artifact_retention_hours`(24) — 자기 시계 | 아니다(결정 41: 남의 묶음을 쫓아내지 않는다) |
| 스냅샷 캐시 | `blobs/` | `snapshot_cache_days`(30) + `snapshot_cache_max_bytes`(4 GiB) — 자기 예산 | 아니다(자기 예산이 있다) |
| 미러 | `mirrors/` | 없다 — 안 지운다(M3 그대로) | 아니다 |

- **증거를 지우지 않는다**는 것이 이 설계의 안전 성질이다. 압박을 받으면 720 MB 를 내놓지 50 KB 를
  내놓지 않는다. 실패 잡의 로그 30일은 오늘 그대로다.
- 성공 잡의 워크스페이스는 오늘도 끝나자마자 **지우려고 시도한다**(`worker.py:476` 로컬 ·
  `remote_worker.py:591` 원격). 그래서 새 날짜 키에 `_failure` 접미사를 붙이지 않는다 — 성공 잡의
  워크스페이스는 **보존 대상이 아니라서** 그 이름이 가리킬 경우가 사실상 없다.
  (오너 프롬프트의 이름은 `workspace_retention_days_failure` 였다. 짧은 쪽을 권한다 — 결정 51.)
  ⚠️ 다만 **「존재할 수 없다」고 쓰면 거짓말이다**(리뷰 11번): 두 정리 모두
  `rmtree(ignore_errors=True)` 라 삭제가 조용히 실패할 수 있고, 종료 직전에 프로세스가 죽으면
  그대로 남는다. 그래서 규칙은 **종료 상태 전부**를 후보로 삼는다 — `succeeded` 도 포함이다.
  그런 잔해는 오늘 아무도 안 치운다(§13 D).

### 4.2 설정 키 (`[server]`)

```toml
[server]
retention_days_success = 14              # 이미 있는 키 — 성공 잡의 로그
retention_days_failure = 30              # 이미 있는 키 — 비성공 잡의 로그
workspace_retention_days = 1             # 새 키 — 남겨 둔 워크스페이스와 그 잡의 스냅샷 tar
workspace_storage_max_bytes = 107374182400   # 새 키 — 부피의 상한(100 GiB). 0 = 무제한
min_free_bytes = 10737418240             # 새 키 — 파일 시스템 여유 바닥(10 GiB). 0 = 안 본다
```

**타입**(`_apply_section` 이 기본값의 타입으로 TOML 값을 맞춘다, `config.py:313`):
`workspace_retention_days: int = 1` · `workspace_storage_max_bytes: int = 107_374_182_400` ·
`min_free_bytes: int = 10_737_418_240`. 순수 계층의 `WorkspaceBudget` 은 이 셋을 인자로 받는다
(`core/queue.QueueConfig` · `core/admission.AdmissionConfig` 와 같은 방식 — 순수 계층은 `config.py` 를
모른다).

**검증** — 문구는 섹션·키 이름이 들어가고 작은따옴표를 쓰는 오늘의 방식(`config.py:635`):

| 키 | 규칙 | 문구 |
|---|---|---|
| `workspace_retention_days` | `>= 0` | `[server] workspace_retention_days must be >= 0` |
| `workspace_retention_days` | `<= min(retention_days_success, retention_days_failure)` | `[server] workspace_retention_days must be <= retention_days_success (14)` (더 작은 쪽 키를 이름으로 찍는다) |
| `workspace_storage_max_bytes` | `0` 또는 `>= 1 GiB` | `[server] workspace_storage_max_bytes must be 0 (no limit) or at least 1 GiB` |
| `min_free_bytes` | `>= 0` | `[server] min_free_bytes must be >= 0` |

- 두 번째 규칙의 이유: 잡 디렉터리가 먼저 지워지면 **워크스페이스와 tar 도 그때 같이 지워진다**
  (`_purge_job` 이 함께 지운다). 부피에 더 긴 값을 줘도 지켜지지 않으므로, 조용히 무시하는 대신
  시작할 때 거절한다. **`min` 인 이유**(리뷰 6번): 부피에는 성공 잡의 tar 도 들어가고 그 tar 은
  `retention_days_success`(14) 에 잡 디렉터리째 사라진다. 실패 쪽만 보면
  `retention_days_success = 3` · `workspace_retention_days = 7` 같은 설정이 통과하고, 그 7일은
  지켜지지 않는다.
- `workspace_storage_max_bytes` 의 하한을 1 GiB 로 두는 이유: 게이트 잡 하나가 720 MB 다. 그보다
  작은 예산은 「끝나는 족족 지운다」와 같고, 그건 `workspace_retention_days = 0` 이 이미 표현한다.
  `snapshot_cache_max_bytes` 가 1 MiB 하한을 두는 것과 같은 종류의 방어다.

**기본값의 근거**

| 값 | 왜 |
|---|---|
| `workspace_retention_days = 1` | 하루 50개 × 435 MB ≈ 21.8 GB/일 → 정상 상태 **약 22 GB**(sweep 주기 때문에 실제 상한은 하루+1시간치 ≈ 23 GB). 여유 628 GB 의 3.6%. 깨진 잡의 워크스페이스는 **그날 안 열어 보면 값이 0 이다** — 코드가 이미 앞으로 갔다. 「왜 깨졌나」는 로그(30일)에 남고, 부피 있는 증거는 `artifacts` 로 가져간다(§6). **⛔ 오너 확정 2026-09-09** — 초안은 2일이었다 |
| `workspace_storage_max_bytes = 100 GiB` | 날짜 규칙의 정상 상태(22 GB)의 네 배가 넘는다 — **평소에는 안 발동하고**, 하루에 200개가 들어오거나 1.6 GB 짜리 잡이 몰리는 날에만 천장이 된다. 이 머신 여유의 16%. 예산을 정상 상태 가까이 잡으면 매일 발동해서 「하루 보관」이 거짓말이 된다 |
| `min_free_bytes = 10 GiB` | 웹 호스트 카드가 이미 쓰는 값(`DISK_LOW_FREE`). 「작은 디스크는 80% 에서도 스냅샷을 못 푼다」는 그 카드의 근거가 그대로 삭제 기준의 근거다 |

**비율(`min_free_ratio`)은 두지 않는다.** 화면의 두 기준(85% 또는 10 GiB) 중 **85% 는 사람에게
알리는 기준**이지 지우는 기준이 아니다 — 926 GiB 디스크의 85% 는 여유가 아직 139 GiB 다. 그 상태에서
증거를 지우기 시작하면 아무 이득 없이 증거만 잃는다. `docs/operating.md` 가 이미 그 이유를 적어 두었다:
「a large disk at 90% still has room, a small one at 80% cannot unpack a snapshot」. 지우는 기준은
바이트다(결정 53).

### 4.3 순수 규칙 — `core/retention.py` 에 더하는 것

기존 `due_for_purge`(잡 디렉터리)·`blobs_to_purge`·`bundles_to_expire` 는 **그대로 둔다.**
워크스페이스용 함수를 하나 더한다. I/O 도 시계도 없고, `now` 와 **모든 크기와 여유 공간이 인자다.**

```python
@dataclass(frozen=True)
class VolumeItem:
    """부피 하나 = 한 잡의 워크스페이스 + 그 잡의 스냅샷 tar. **인벤토리 전체**가 들어온다 —
    종료 잡뿐 아니라 활성 잡과 고아(잡 행이 없는 디렉터리)도.

    `workspace_bytes`·`snapshot_bytes` 는 janitor 가 실제로 잰 값이고 **못 쟀으면 None** 이다.
    둘 다 없는 항목은 애초에 만들지 않는다."""

    job_id: int
    state: str | None  # 잡 행이 없으면 None — 고아다
    finished_at: datetime | None
    created_at: datetime | None  # 종료 잡인데 finished_at 이 없을 때의 대체 기준
    workspace_bytes: int | None  # 워크스페이스가 없으면 0, 못 쟀으면 None
    snapshot_bytes: int | None  # tar 이 없으면 0, 못 쟀으면 None

    @property
    def bytes(self) -> int | None:  # 하나라도 모르면 모른다
        ...

    @property
    def evictable(self) -> bool:  # 종료 잡만. 활성·고아는 아니다
        ...


@dataclass(frozen=True)
class WorkspaceBudget:
    days: int  # workspace_retention_days
    max_bytes: int  # workspace_storage_max_bytes. 0 = 무제한
    min_free_bytes: int  # min_free_bytes. 0 = 안 본다


REASON_AGE, REASON_BUDGET, REASON_FREE = "age", "budget", "free"  # 결정 37 — 코드를 내려보낸다


@dataclass(frozen=True)
class PurgeItem:
    job_id: int
    workspace_bytes: int | None
    snapshot_bytes: int | None
    reason: str  # 이 잡을 고른 **첫** 규칙


@dataclass(frozen=True)
class PurgePlan:
    items: tuple[PurgeItem, ...]  # finished_at 오름차순 — 오래된 것부터
    #: 잰 항목만 더한 값과, 크기를 모르는 채 지울 항목 수. 둘을 **섞지 않는다** —
    #: `freed_bytes` 하나로 내면 부분합이 전체 합인 척한다.
    known_freed_bytes: int
    unknown_freed_count: int
    volume_bytes: int | None  # 인벤토리 전체(활성·고아 포함). 하나라도 모르면 None
    evictable_bytes: int | None  # 종료 잡 몫. 하나라도 모르면 None
    non_evictable_bytes: int | None  # 활성 + 고아 몫
    over_budget_bytes: int | None  # 계획을 다 지워도 남는 초과분. 모르면 None
    projected_short_free_bytes: int | None  # 계획대로 지웠을 때 **예상** 부족분. 실측이 아니다
    budget_unreachable: bool  # 지울 수 없는 바이트만으로 이미 예산을 넘었다
    inventory_error: str | None  # 스캔·DB·측정 실패 코드. 있으면 압박 규칙은 안 돈다


def workspaces_to_purge(
    items: Iterable[VolumeItem],
    now: datetime,
    budget: WorkspaceBudget,
    *,
    free_bytes: int | None,
    inventory_error: str | None = None,
) -> PurgePlan: ...
```

판정 순서 — **나이 → 예산 → 바닥**, 셋 다 오래된 것부터:

1. **활성 잡과 고아를 후보에서 뺀다.** `evictable` 이 아닌 항목은 어떤 규칙으로도 안 고른다. 총량
   계산에는 남는다 — 디스크가 그 바이트를 실제로 쥐고 있다. 지울 수 없을 뿐이다.
2. **나이**: `now - (finished_at or created_at) >= days × 86400` → `REASON_AGE`.
   **크기를 안 본다** — 못 잰 워크스페이스도 나이가 되면 지운다.
3. **예산**: `max_bytes > 0` 이고 `volume_bytes` 를 알 때만. 남은 총량이 `max_bytes` 아래로
   내려갈 때까지 오래된 것부터 → `REASON_BUDGET`.
4. **바닥**: `min_free_bytes > 0` 이고 `free_bytes` 를 알 때만. `free_bytes + 지울 바이트` 가
   `min_free_bytes` 이상이 될 때까지 오래된 것부터 → `REASON_FREE`.
5. 남는 초과·부족은 `over_budget_bytes`·`projected_short_free_bytes` 로 **숨기지 않고 돌려준다**.

**모르는 숫자는 `None` 이다.** `volume_bytes` 를 모르면 `over_budget_bytes` 도 모르고,
`free_bytes` 를 모르면 `projected_short_free_bytes` 도 모른다. 0 으로 채우면 「예산을 지키고 있다」는
거짓말이 된다(PLAN 「fail-open 금지」의 「모르는 숫자는 null」).

**`projected_` 접두는 이름값을 한다.** 그 수는 **계획상 예측**이지 실측이 아니다. 실제로 얼마나
회수됐는지는 janitor 가 실행 뒤 다시 재서 따로 싣는다(§4.5).

**못 재면 「압박 삭제」만 멈춘다(fail-open 금지).** `inventory_error` 가 있거나 인벤토리에
`bytes is None` 이 하나라도 있으면 `volume_bytes` 가 None 이 되고 **3·4 는 아무것도 안 고른다.**

- 「하나라도」의 범위는 **인벤토리 전체**다 — 종료 후보뿐 아니라 **활성 잡과 고아와 tar 만 있는
  항목까지**. 활성 디렉터리를 못 쟀는데 종료 항목만 더해 총량을 내면 숫자를 지어낸 것이다.
- `workspaces/`·`jobs/` 의 `scandir` 실패는 「빈 디렉터리」가 **아니라** 측정 실패다. DB 조회
  실패도 「고아」가 **아니라** 회계 실패다. 둘 다 `inventory_error` 로 올라온다(§4.4).

⚠️ **두 삭제를 같은 문장으로 묶어 읽지 마라.** 2(나이)는 **오늘도 도는 정상 보존 정책**이고 크기를
안 본다 — 측정이 실패해도 그대로 돈다. 3·4 는 **압박 삭제**이고 크기를 근거로 하므로 근거가 없으면
멈춘다. 구현할 때 「못 재면 아무것도 안 지운다」로 잘못 잠그면 **디스크가 차는 동안 나이 규칙까지
멈춘다** — 이 마일스톤이 고치려던 바로 그 상태로 돌아간다.

⚠️ 그리고 이것을 **일반적인 의미의 fail-closed 라고 부르지 마라**(리뷰 1번). 「모르면서 데이터를
지우지 않는다」는 뜻에서만 fail-closed 다. 측정이 계속 실패하면 **디스크는 계속 찬다** — 그 상태는
`rcm check` warn 과 `job_storage.error_code` 로 사람에게 넘긴다.

**예산은 못 이룰 목표를 위해 증거를 학살하지 않는다.** 지울 수 없는 바이트(`non_evictable_bytes`
= 도는 잡 + 고아)만으로 이미 `max_bytes` 를 넘으면 `budget_unreachable = True` 이고 **3(예산)은 그
회차에 아무것도 안 고른다** — 종료 잡을 하나도 남김없이 지워도 목표에 못 닿기 때문이다. 그런 설치의
진짜 해법은 상한을 올리거나 고아를 치우는 것이지 어제 실패한 잡의 워크스페이스를 태우는 게 아니다.
`rcm check` 가 **원인을 지목한다**(「N GB 를 도는 잡과 고아 디렉터리가 쥐고 있다」).

**바닥은 다르다 — 4(바닥)는 그래도 돈다.** 여유 공간은 회계가 아니라 응급이고, 지운 바이트만큼
실제로 여유가 는다. 목표에 못 닿아도 한 걸음은 이득이다. 두 규칙이 여기서 갈리는 이유가 그것이다.

### 4.4 인벤토리 — 무엇이 있고 얼마인가 (`janitor.py`)

```python
def _measure_dir(self, path: Path) -> int | None:
    """os.scandir 재귀 · st_blocks × 512 · 심링크는 따라가지 않는다. OSError 면 None."""
```

**스캔 둘을 job id 로 합친다(union).** 부피는 두 자리에 있고 **둘은 독립**이다:

| 스캔 | 무엇 | 왜 따로 |
|---|---|---|
| `workspaces/` 의 정수 이름 | 워크스페이스 | 원격 워커에서 돈 잡은 여기에 **없다**(워커의 `data_dir` 에 있다) |
| `jobs/<n>/tree.tar.gz` | 입력 스냅샷 | 원격 잡도 **서버에 남는다.** 워크스페이스 목록에만 기대면 이 tar 이 부피 규칙을 통째로 비껴가 로그 시계(30일)를 타고, `--no-cache` 클라이언트가 있는 설치에서 최대 512 MB × 잡 수가 조용히 쌓인다 |

합치는 규칙: **두 집합의 합집합**이 인벤토리다. 항목마다 네 모양이 다 나온다 — 워크스페이스만 ·
tar 만(원격 잡) · 둘 다 · 잡 행이 없는 것(고아). 없는 쪽은 **0**(모르는 게 아니다), 못 잰 쪽은
**None**. 그 id 들의 잡 행을 한 번에 붙여 `state`·`finished_at`·`created_at` 을 채운다.

DB 에서 후보를 뽑지 않는 이유는 위의 원격 잡 말고도 하나 더 있다: `list_unpurged_finished` 는 한 번에
`CANDIDATE_LIMIT`(1000)개만 준다. 총량을 그 페이지로 재면 1000개가 넘는 순간 **총량이 작게 나오고
예산이 조용히 안 지켜진다.**

**실패는 실패로 올린다 — 빈 값이나 고아로 바꾸지 않는다(리뷰 1·3번).**

| 무엇이 실패했나 | 잘못된 처리 | 옳은 처리 |
|---|---|---|
| `workspaces/`·`jobs/` 의 `scandir` | 「빈 디렉터리」 → 총량 0 → 예산을 지키는 척 | `inventory_error = "scan"`. 압박 규칙 중단 |
| 잡 행 조회 | 「행이 없으니 고아」 → 지울 수 없는 바이트로 오분류 | `inventory_error = "db"`. 압박 규칙 중단. **행이 없다고 DB 가 말했을 때만 고아다** |
| 디렉터리 하나의 측정 | 0 | 그 항목 `bytes = None` → `volume_bytes = None` |
| `shutil.disk_usage` | 0 이나 무한대 | `free_bytes = None` → 바닥 규칙만 중단(예산은 돈다) |

**측정 캐시 — 워크스페이스와 tar 을 따로 잰다(리뷰 4번).**

- tar 은 파일 하나다. **매번 `lstat`** 한다. 캐시가 필요 없다.
- 워크스페이스는 재귀라 비싸다. `Janitor._sizes: dict[int, WorkspaceMeasure]` 에
  `(bytes, st_mtime_ns, measured_at)` 을 담고, 매 sweep **`lstat` 한 번**으로 최상위 mtime 이
  그대로인지 보고 다르면 다시 잰다. 디렉터리가 사라지면 버린다. 캐시는 **메모리에만** 있고
  프로세스와 함께 죽는다 — 재시작하면 전부 다시 잰다.
- **캐시 최대 수명 `MEASURE_MAX_AGE = 24시간`.** mtime 이 그대로여도 그보다 오래된 값은 다시 잰다.
  최상위 mtime 은 **안쪽 파일이 커지는 것을 못 잡는다**(잡이 남긴 백그라운드 프로세스가 기존 파일에
  계속 쓰면 오차에 상한이 없다). 24시간 상한이 그 오차를 하루에 한 번 씻는다. 하루에 한 잡을 한 번
  더 재는 비용은 0.11초다.
- **`measured_at` 은 재귀 측정을 한 시각이다.** `lstat` 로 「안 변했다」를 확인한 시각이 아니다.
  화면이 그 값을 그대로 보여 주므로 둘을 섞으면 「방금 쟀다」는 거짓말이 된다.
- 활성 잡의 워크스페이스는 자라므로 **매 sweep 다시 잰다**(레인 수만큼이라 싸다).
- §2.2 의 실측: 첫 sweep 6.65초(38만 파일), 이후 sweep 은 새로 종료된 잡 몇 개(하루 50개 · 시간당
  약 2개 → 0.2초). **서버가 뜨자마자 도는 첫 sweep 이 가장 비싸다**는 것을 문서에 적는다.

**눈금 — 회계와 회수 가능량은 다른 수다(리뷰 4번).**

- **`st_blocks × 512`** 를 쓴다(`du` 와 같은 눈금). `st_size` 는 희소 파일·APFS 클론에서 디스크가
  실제로 쥔 양과 다르다.
- **심볼릭 링크는 따라가지 않는다**(`entry.is_dir(follow_symlinks=False)`). `_remove_tree` 가 링크를
  링크로만 지우는 것과 같은 규칙이다.
- ⚠️ **하드링크는 「살짝 크게」가 아니다.** `git_ref` 워크스페이스는 미러의 객체를 하드링크한다
  (`gitops.py` 의 로컬 clone). 같은 inode 를 링크 수만큼 세면 오차가 임의로 커지고, **그 블록은
  지워도 여유가 안 는다**(미러가 아직 잡고 있다). 그래서 두 수를 가른다 — 회계에 싣는
  `charged_bytes`(전부 센다, 예산이 보수적으로 동작한다)와 바닥 규칙이 쓰는
  `reclaimable_bytes`(`st_nlink > 1` 인 블록은 **뺀다**). 바닥이 「지우면 이만큼 는다」고 말할 때
  거짓말하지 않게 하는 것이 목적이다.
- 여유 공간은 `shutil.disk_usage(data_dir)` — 호스트 표본과 같은 함수이지 표본을 재사용하지
  않는다. 표본은 낡을 수 있고(`stale`), 청소는 **지금** 값으로 판단해야 한다.

### 4.5 계획은 한 회차에 한 번. 그리고 진전이 없으면 멈춘다 (결정 54·62)

계획은 **잰 값으로 한 번** 세우고, 실행하고, 끝난 뒤 여유를 **다시 잰다.** 「여유가 바닥을 넘을
때까지 지운다」는 루프를 한 회차 안에 두지 않는다.

이유: 지워도 여유가 안 오르는 파일 시스템이 실제로 있다. macOS 의 Time Machine 로컬 스냅샷은 지운
파일의 블록을 붙잡고 있어 `df` 가 안 움직인다. 루프를 돌면 **워크스페이스를 하나도 남김없이 지우고도**
바닥을 못 넘는다.

⚠️ **그런데 그 보호는 한 회차짜리다(리뷰 2번).** `df` 가 계속 안 움직이면 **여러 회차에 걸쳐** 결국
전부 지운다. 그리고 애초에 부족분이 evictable 전체보다 크면 **첫 계획이 이미 전부를 고른다** —
「한 번만 계획한다」는 삭제량의 상한이 아니다. 그래서 회차 사이에 잠금장치를 하나 둔다:

**무진전 latch.** 바닥 규칙으로 실제로 지운 회차의 끝에 여유를 다시 재서

```
freed_effective = free_after - free_before
```

가 **지운 바이트의 절반에 못 미치면**(`freed_effective < known_freed_bytes // 2`) `no_progress`
latch 를 세운다. latch 가 서 있는 동안 **바닥 규칙은 자동 sweep 에서 안 돈다**(나이·예산은 돈다).
latch 는 `rcm gc`(사람이 명시적으로 부른 것)나 서버 재시작으로만 풀린다. 근거: 두 번 지웠는데 여유가
안 늘었다면 지우는 게 답이 아니라는 뜻이다 — 그때부터는 증거를 태우는 것 말고 하는 일이 없다.
`rcm check` 가 **FAIL** 로 사람을 부른다.

**바닥은 보장이 아니라 회수 시도다.** 이 설계로 `min_free_bytes` 는 「항상 이만큼은 비어 있다」는
불변식이 **아니다**. 문서에 그렇게 쓴다 — 지킬 수 없는 약속을 화면이 하지 않게.

### 4.6 서버 배선과 락 (리뷰 7번)

| 자리 | 무엇 | 락 |
|---|---|---|
| `Janitor.plan(now)` | 인벤토리 → 측정 → `workspaces_to_purge` → `PurgePlan`. **아무것도 안 지운다** | 없다(호출자가 잡는다) |
| `Janitor.sweep_once(now)` | 잡 디렉터리 청소 + `plan()` 실행 + 번들 + 메타데이터 + blob + 사후 측정 | `_operation_lock` |
| `Janitor.gc(now, dry_run)` | dry-run 이면 `plan()` 만, 아니면 `sweep_once` 와 같은 실행 | `_operation_lock` |
| `Janitor.storage(now)` | 화면용 회계(§5.1). **마지막 측정값**을 쓴다 — 상태 요청이 디스크를 훑지 않는다 | `_state_lock` (읽기만) |
| `App.job_storage()` | `server.artifact_storage()` 와 같은 모양 · 같은 실패 규칙 | 없다 |
| `App.gc(dry_run)` | `POST /gc` → **`Janitor.gc()` 를 부르기만 한다** | 없다 |

**락 규칙 — 지금 `Janitor._lock` 은 sweep 직렬화 락이 아니다.** `janitor.py` 의 그 락은
`last_sweep_at`·카운터를 지키는 짧은 상태 락이다. 새로 필요한 것은 **작업 락**이다:

- `_operation_lock` 은 **`Janitor` 안에만** 있고 `sweep_once` 와 `gc` 만 잡는다. `App.gc()` 는
  락을 직접 안 잡는다 — 두 겹 획득과 락 순서 문제가 거기서 생긴다.
- 범위는 **계획 + 삭제 + 사후 측정 전부**다. 계획만 잡고 놓으면 두 실행이 같은 잡을 지운다.
- `_state_lock`(캐시 · `last_sweep_at` 읽기)은 별개이고, **그 락을 쥔 채 파일 스캔이나 DB I/O 를
  하지 않는다.**
- 작업 락을 기다리기 **전에** Store 트랜잭션이나 App 락을 잡지 않는다.
- 예외가 나도 락은 풀린다(`with`).

**삭제 직전에 상태를 다시 읽는다.** 계획 시점과 삭제 시점 사이에 잡이 바뀔 수 있다. 각 항목을
지우기 직전에 `store.get_job(id)` 로 **종료 상태를 재확인**하고, 활성이면 건너뛴다(활성 잡 보호의
두 번째 겹). 삭제 순서는 잡마다 **워크스페이스 → 스냅샷 tar**. 워크스페이스는 지웠는데 tar 이
실패하면 그 잡은 다음 회차에 tar 만 다시 시도한다(멱등).

- 워크스페이스·tar 삭제는 **표시가 필요 없다**. `artifacts_purged_at` 은 지금처럼 「잡 디렉터리까지
  지웠다」에만 찍는다. 이미 없는 경로의 삭제는 `_remove_tree` 가 조용히 통과하므로(FileNotFoundError)
  다시 계획에 올라도 무해하다 — **DB 마이그레이션이 필요 없는 이유가 이것이다.**
- **청소기 스레드가 죽어 있어도 `rcm gc` 는 돈다.** 자동 청소가 멈춘 것을 사람이 손으로 메우는
  통로다(`/api/health` 는 그 사이에도 503 으로 죽음을 말한다).
- `next_sweep_at` 은 `last_sweep_at + retention_sweep_interval_seconds`. 아직 한 번도 안 돌았으면
  둘 다 `null` 이다 — 0 이나 지금 시각으로 채우지 않는다.

## 5. 보이게 하기 (결정 37 — 서버는 문장이 아니라 코드를 내려보낸다)

### 5.1 `/api/status` → `server.job_storage` (스키마 v1, 키 추가)

```json
"job_storage": {
  "volume_bytes": 30944211553, "workspace_bytes": 30940831744, "snapshot_bytes": 3379809,
  "evictable_bytes": 29884170240, "non_evictable_bytes": 1060041313, "orphan_bytes": 0,
  "log_bytes": 41680896,
  "limit_bytes": 107374182400, "free_bytes": 674309865472, "min_free_bytes": 10737418240,
  "over_budget_bytes": 0, "projected_short_free_bytes": 0,
  "budget_unreachable": false, "no_progress": false,
  "measured_at": "2026-09-09T05:00:03Z",
  "last_sweep_at": "2026-09-09T05:00:03Z", "next_sweep_at": "2026-09-09T06:00:03Z",
  "error_code": null
}
```

**산식이 맞아떨어져야 한다**(리뷰 9번 — 초안은 안 맞았다):

```
volume_bytes        = workspace_bytes + snapshot_bytes      ← 예산이 재는 수
volume_bytes        = evictable_bytes + non_evictable_bytes
non_evictable_bytes = 도는 잡 몫 + orphan_bytes
```

- 예산이 견주는 상대는 `volume_bytes` 지 `workspace_bytes` 가 아니다. `workspace_bytes` 만 보면
  tar 이 빠져 「예산을 넘었는데 왜 안 줄지?」의 답이 안 나온다.
- `budget_unreachable` 은 「`non_evictable_bytes` 만으로 이미 `limit_bytes` 를 넘었다」는 사실이다.
  이때 예산 규칙은 아무것도 안 고르고 `rcm check` 가 원인을 지목한다(§4.3).
- `no_progress` 는 무진전 latch 가 서 있다는 사실이다(§4.5).
- `log_bytes` 는 **증거 몫**이고 예산 밖이다. 회계에만 싣는다 — 「부피를 줄여도 이건 안 준다」를
  화면이 말할 수 있게.
- 못 잰 값은 `null`(0 이 아니다) + `error_code`. `queue_error`·`hosts_error` 와 같은 규칙이다.
  `volume_bytes` 가 null 이면 `over_budget_bytes` 도 null 이고, `free_bytes` 가 null 이면
  `projected_short_free_bytes` 도 null 이다.
- `limit_bytes`·`min_free_bytes` 는 0(끔)이면 `null` 로 싣는다 — 화면이 `—` 로 그린다.
- `measured_at` 은 **재귀 측정을 한 시각**이다(§4.4). `lstat` 확인 시각이 아니다.
- 바이트 필드 이름은 전부 `_bytes` 로 끝난다(스키마 규칙).

### 5.2 `/api/health` → `storage`

```json
"storage": {"volume_bytes": 30944211553, "free_bytes": 674309865472,
            "limit_bytes": 107374182400, "min_free_bytes": 10737418240,
            "last_sweep_at": "…", "next_sweep_at": "…",
            "budget_unreachable": false, "no_progress": false, "under_floor": false}
```

- **경로는 안 싣는다.** health 는 토큰 없이 열린다.
- **503 조건은 안 바꾼다.** 예산 초과는 고장이 아니라 다음 sweep 이 처리할 일이고, 청소기가 죽는
  것은 이미 503 이다. `under_floor`·`no_progress`·`budget_unreachable` 은 **사실만 싣고** 판단은
  `rcm check` 와 사람에게 맡긴다 — health 를 감시하는 쪽이 자기 기준으로 쓸 수 있다.

### 5.3 `rcm check` — `storage` 한 줄 (영어)

`ok`/`warn`/`FAIL` 은 이미 있는 세 값이다(`cmd_check` 의 `bool | None`). 등급은 **고칠 수 있는가**로
가른다 — 다음 sweep 이 고칠 수 있으면 warn, 사람이 와야 하면 FAIL.

| 등급 | 언제 | 줄 |
|---|---|---|
| ok | 예산 안이고 바닥 위 | `rcm data 30.9 GB of 107.4 GB · 674 GB free · next sweep in 42m` |
| warn | 예산 초과이고 지울 게 남았다 | `118.2 GB over the 107.4 GB budget — the next sweep will trim it` |
| warn | 크기를 못 쟀다 | `size of 2 workspaces could not be measured — the budget is not enforced` |
| **FAIL** | `budget_unreachable` | `118.2 GB over the 107.4 GB budget, held by running jobs and 3 orphan directories — nothing the sweep may delete would bring it under` |
| **FAIL** | 바닥 아래인데 지울 게 없다 | `4.1 GB free, under the 10.7 GB floor, and nothing left to delete` |
| **FAIL** | `no_progress` latch | `4.1 GB free: 12.0 GB was deleted and free space did not move — deleting more will not help` |

마지막 줄이 리뷰 2번의 답이다 — **삭제가 효과가 없었던 상황을 warn 으로 숨기지 않는다.**

### 5.4 웹 호스트 카드 (두 언어)

디스크 미터 아래 한 줄을 더한다. 서버 자신의 호스트 카드에만 그린다(`job_storage` 는 서버의 사실이지
호스트의 사실이 아니다).

```
디스크  120 / 460 GB                              26% · 여유 340 GB
        rcm 데이터 30.9 GB / 107.4 GB · 다음 청소 42분 뒤
```

- 예산 초과·바닥 아래·latch 면 `warn` 색(디스크 미터가 이미 쓰는 `.warn`).
- `error_code` 가 있으면 숫자 대신 「용량을 재지 못했다」. **0 으로 그리지 않는다.**
- 한국어 기본 · 영어 선택(결정 36·38). 카탈로그 두 벌에 키를 더한다.

### 5.5 `rcm gc [--dry-run]` (결정 57)

```
$ rcm gc --dry-run
job    state      workspace   snapshot   reason   finished
#118   failed        1.6 GB       —      age      2026-09-06 11:20
#131   timed_out   720.4 MB    3.4 MB    budget   2026-09-08 22:17
would free 2.3 GB from 2 jobs · 62 workspaces left · 28.9 GB of 107.4 GB
```

- `POST /gc`, **admin 토큰만**(`require_admin`). 본문 `{"dry_run": bool}` — 타입이 아니거나 모르는
  키가 있으면 400(`{"dry_run": "false"}` 는 참이 아니라 오류다).
- **응답은 계획과 결과를 가른다**(리뷰 7번). 계획의 `known_freed_bytes` 를 실제 회수처럼 내면
  거짓이다 — 삭제는 실패할 수 있다.

```json
{"dry_run": false,
 "planned": [{"job_id": 131, "workspace_bytes": 755, "snapshot_bytes": 3379809, "reason": "budget"}],
 "deleted": [{"job_id": 131, "workspace_bytes": 755, "snapshot_bytes": 3379809}],
 "failed":  [{"job_id": 118, "error_code": "EACCES"}],
 "freed_bytes": 3380564,
 "storage_before": { …§5.1… }, "storage_after": { …§5.1… }}
```

- dry-run 이면 `deleted`·`failed` 는 빈 목록이고 `storage_after` 는 **null** 이다. 예측치를
  「실측 뒤」 자리에 넣지 않는다.
- **janitor 와 같은 `plan()` 을 쓴다.** 다만 「dry-run 이 보여준 것과 실제가 다를 수 없다」는
  **틀린 말이라 쓰지 않는다**(리뷰 7번) — 두 요청 사이에 잡이 끝나고 디렉터리가 생긴다. 보장되는
  것은 **같은 입력에 같은 판정**이다. 완료 기준의 「dry-run 목록과 실제가 같다」도 통제된 시험에서만
  참이라고 적는다.
- `Janitor._operation_lock` 을 잡는다(§4.6) — 청소기와도, 다른 gc 와도 동시에 안 돈다.
- **시한**: 일반 클라이언트 타임아웃은 15초(`client.py`)인데 데이터가 20배면 스캔만 140초다.
  `rcm gc` 는 **자기 타임아웃**(`--timeout`, 기본 600초)을 쓰고, 그래도 넘으면 **종료 코드 3
  「모른다」** 로 끝내며 「서버는 계속 지우고 있을 수 있다. `rcm gc --dry-run` 으로 다시 보라」를
  안내한다. 「타임아웃 = 실패」로 찍지 않는다(fail-open 금지의 `rcm wait` 규칙과 같다).
- `--json` 은 응답 그대로. 사람용 표는 `core/render_text` 에 둔다(순수).

**`--dry-run` 은 서버 없이도 돈다(결정 61).** `rcm gc --dry-run --config <server.toml>` 은
`rcm check --config` 처럼 **서버를 띄우지 않고** 설정과 데이터 디렉터리만 읽어 같은 `plan()` 을
돌린다(DB 는 읽기 전용으로 연다). 이게 업그레이드 안전 게이트다 — §10 완료 기준 8.

### 5.6 설정 표면 — 처음 설치한 사람이 이 세 값을 찾을 수 있어야 한다 (결정 60)

오너 요청(2026-09-09): 「이 프로젝트를 쓰는 유저가 처음에 **고급**을 눌러 이것저것 설정할 수 있게
하는데, 이 내용도 있으면 좋겠다.」 오늘 이 레포에 「고급」이라는 화면은 없다. 사람이 값을 만지는
자리는 **`rcm init server` 가 써 주는 `server.toml`** 이고, 그 파일이 곧 고급 설정 화면이다.
그래서 세 키를 **숨기지 않는다**:

| 자리 | 어떻게 |
|---|---|
| `examples/server.toml` = `templates/server.toml` | 다른 보존 키 바로 밑에 **주석 처리하지 않고** 기본값과 한 줄 설명을 함께 쓴다. 둘은 바이트가 같아야 하고 `tests/test_examples.py` 가 잠근다 — `rcm init server` 로 만든 모든 새 설치의 파일에 이 세 줄이 들어간다 |
| `docs/configuration.md` | 「Retention」 절과 **키 표**를 새로 둔다(산출물 표와 같은 모양: 키 · 기본값 · 뜻). 지금은 보존 키가 `operating.md` 산문에만 있고 표가 없다 |
| `docs/operating.md` 「Retention」 | 두 시계 · 예산 · 바닥 · `rcm gc` · **되돌리는 법**을 적는다 |
| `docs/usage.md` §11 「What to do next」 | 「디스크가 차기 전에 무엇이 지워지는지 보라」 한 줄 + `rcm gc --dry-run` |

기본값이 **파일에 적혀 있어야** 하는 이유: 이 셋은 데이터를 지우는 값이다. 「코드 기본값이라
파일에 없다」는 상태에서 사람이 `rcm gc --dry-run` 을 보고 놀라는 것보다, 설치할 때 파일에서 보고
자기 값으로 바꾸는 쪽이 낫다. 실제로 오너의 운영 `server.toml` 은 `recent_count` 한 줄만 바꾼
상태였고, 나머지가 전부 코드 기본값이라는 것을 아무도 파일에서 볼 수 없었다.

⚠️ 나중에 웹/웹뷰에 **진짜 「고급 설정」 화면**이 생기면 이 세 값이 거기 첫 줄에 있어야 한다.
그 화면은 M5g 범위가 아니다(설정은 서버 파일이 정본이고, 화면에서 쓰려면 설정을 쓰는 API 가 먼저
필요하다) — 화면을 만드는 세션이 이 절을 보고 가져간다.

## 6. 프리셋이 증거를 남기는 법 (결정 58·59)

**문제는 rcm 이 아니라 프리셋 쪽이다.** rcm 은 stdout+stderr 를 전량 저장한다. 참고 팀의 게이트
스크립트가 무거운 구간(test·gitleaks·build web)의 출력을 `TMPDIR` 로 돌리고 실패 시 마지막 40줄
(테스트는 5줄)만 흘린 뒤 그 폴더를 지운다. 실측: 실패 잡 #133 의 로그 50,788 바이트 안에 `Expected:`
0줄 · `Actual:` 0줄 · 스택트레이스 0줄. 순차 검사(analyze·format)는 출력을 직접 내보내서 로그만으로
고칠 수 있다 — **같은 스크립트 안에서 두 방식이 갈린다.**

정본 권고 — **새 개념을 만들지 않는다.** 두 층으로 적는다.

1. **한 줄짜리 판정은 stdout 으로** → 잡 로그에 남고 30일 산다. 웹·`rcm logs` 로 폰에서도 읽힌다.
   실패한 테스트의 이름과 `Expected:`/`Actual:` 몇 줄이 여기 있어야 한다. 마지막 40줄이 진행 막대면
   그 40줄은 0의 가치다. 한 줄 요약은 `::rcm::summary::` 로 찍는다.
2. **부피 있는 나머지는 워크스페이스에** 두고 `artifacts` 로 선언한다 → `rcm run --fetch-artifacts`
   나 `rcm artifacts N --fetch` 로 가져간다. `TMPDIR` 은 워커가 지우고 나면 아무도 못 본다.

```bash
# 나쁨 — 증거가 TMPDIR 과 함께 사라진다
log=$(mktemp -d)/test.log; flutter test > "$log" 2>&1 || { tail -5 "$log"; exit 1; }

# 좋음 — 워크스페이스에 두고 프리셋이 artifacts 로 선언한다
mkdir -p .rcm/logs
flutter test > .rcm/logs/test.log 2>&1 || {
  echo "::rcm::summary::2 tests failed"
  grep -A3 -m5 -E '^(Expected|Actual|#[0-9])' .rcm/logs/test.log   # 왜 깨졌는지 로그에
  exit 1
}
```

```toml
[[presets]]
name = "gate"
artifacts = [".rcm/logs/*.log"]
artifacts_on = "failure"        # 새 프리셋 키. "always"(기본) | "failure"
```

- **묶음은 보관소가 아니다.** `artifact_retention_hours`(24)는 워크스페이스의 하루와 **거의 같다.**
  가져가는 통로이지 증거의 서랍이 아니다 — 서랍은 로그(30일)다. 문서에 이 순서를 명시한다.
- **프리셋별 TTL 은 만들지 않는다.** 24시간이 「가져간다」는 용도를 덮고, 더 길게 두면
  `artifact_storage_max_bytes`(10 GiB)와 싸운다. 필요하면 전역 값을 올린다(결정 58).
- **`artifacts_on = "failure"` 는 만든다.** 초록 잡마다 40 MB 를 모아 24시간 들고 있을 이유가 없고,
  그것 때문에 사람들이 이 권고를 안 따르게 된다. 기본값은 `"always"` — **오늘의 동작이 그대로다.**

**배선 — 「호출부 두 곳」으로는 원격에서 안 돈다**(리뷰 11번). 원격 워커는 claim 응답에 실린 정책만
보고 모으는데(`remote_workers.py:373` 의 payload → `remote_worker.py:167` 의 파서) **지금 그 payload
에는 산출물 정책이 없다.** 새 키를 프리셋에만 더하면 로컬만 바뀌고 원격은 조용히 예전대로 모은다.
그래서 셋을 함께 고친다:

1. claim 응답의 **얼린 정책**에 `artifacts_on` 을 싣는다(글롭과 같은 자리, 같은 「제출 시점에
   박는다」 규칙).
2. 워커(로컬·원격)는 그 값만 본다. 필드가 없는 옛 서버면 `"always"` — **옛 동작이 기본이다.**
3. 판정 시점은 **종료 상태가 확정된 뒤, 수집 직전**이다(`worker.py` 의 `outcome_for` 뒤,
   `remote_worker.py` 의 `final_flush` 뒤). 「성공인 줄 알고 안 모았는데 그 사이 취소가 수용돼
   실패가 된」 경합을 피하려면 **수집을 여는 판정과 `store.finish` 가 보는 상태가 같아야** 한다 —
   M5e 가 이미 그 트랜잭션 안에서 취소를 다시 읽는다(`docs/m5e-workplan.md` §5). 같은 자리에 붙인다.
4. `succeeded` 가 아닌 **모든** 종료 상태에서 모은다(`failed`·`timed_out`·`cancelled`). `lost` 는
   오늘처럼 안 모은다.

## 7. 안 만드는 것

| 무엇 | 왜 |
|---|---|
| 잡 로그의 크기 상한·회전 | 증거를 자르는 기능이라 별도 결정이 필요하다. 실측 137개 39 MB — 지금 아픈 데가 아니다. 폭주하는 로그는 `stuck` 이 먼저 잡는다. §12 에 위험으로 적는다 |
| 고아 워크스페이스 삭제 | 잡 행이 없는 `workspaces/<n>` 은 회계에 **싣기만** 한다(`rcm gc --dry-run` 이 보여준다). 「주인을 못 찾는 데이터를 지운다」는 규칙은 틀리면 조용히 증거를 잃는다 |
| 원격 워커의 예산 | 서버는 워커 디스크를 지울 권한이 없다. §3.1 B 의 버그(시작할 때 한 번, 상수 7일)는 별도 이슈다 |
| blob·번들을 압박에서 지우기 | 각자 예산이 있고, 번들은 「남의 것을 쫓아내지 않는다」가 결정 41 이다 |
| 비율 기준(`min_free_ratio`) | §4.2 — 큰 디스크에서 아무 이득 없이 증거만 잃는다 |
| 하루 뒤 정확한 재현(replay) | 부피 시계가 지나면 **그 잡을 그대로 다시 돌릴 입력이 서버에 없다**(리뷰 5번). `tree_hash`·`base_sha` 는 신원이지 내용이 아니고, manifest 방식도 blob GC 뒤에는 못 되살린다. 감사 기록은 **로그와 잡 행**이지 입력 트리 전체가 아니라고 문서에 적는다. 정확한 재현이 필요하면 `artifacts` 로 가져간다 |
| 고아·잔해를 지우는 것 | 위와 같다. 성공 잡의 삭제 실패 잔해(`rmtree(ignore_errors=True)`)도 오늘 아무도 안 치운다 — 부피 규칙이 **종료 상태 전부**를 후보로 삼으므로 잡 행이 있는 잔해는 자연히 정리되지만, 잡 행이 없는 고아는 회계에만 남는다 |
| DB 마이그레이션 | 워크스페이스·tar 삭제는 멱등이라 표시가 필요 없다(§4.6). 현재 `DB_VERSION = 8`(M5f 의 claim 인덱스가 v8 이고 이미 머지됐다) — M5g 는 그 번호를 안 쓴다 |

## 8. 테스트 배치

**새로 쓰는 것**

| 파일 | 무엇 |
|---|---|
| `tests/test_retention_workspace.py` | 순수 전수: 나이 경계(`>=`) · 활성 네 상태 + 고아는 **후보 아님** · 예산이 오래된 것부터 · 예산 0 은 무제한 · 바닥이 오래된 것부터 · 바닥 0 은 안 봄 · `free_bytes is None` 이면 바닥 규칙 없음 · **인벤토리 어디든 `bytes is None` 이 하나면**(종료·활성·고아·tar-only 각각) 예산·바닥이 아무것도 안 고르고 **나이는 그대로 돈다** · `inventory_error` 도 같다 · **파생값이 `None` 이 되는 경계**(`over_budget_bytes`·`projected_short_free_bytes`) · `known_freed_bytes`/`unknown_freed_count` 가 안 섞인다 · `budget_unreachable` 이면 예산은 0개, **바닥은 그대로 고른다** · `volume = evictable + non_evictable` 산식 · 같은 잡이 두 규칙에 걸려도 한 번 · 정렬 |
| `tests/test_janitor_m5g.py` | **인벤토리**: 네 모양(워크스페이스만 · tar 만(원격 잡) · 둘 다 · 고아)의 union · `scandir` 실패는 빈 목록이 **아니라** `inventory_error` · DB 오류는 **고아가 아니라** 회계 실패 · `disk_usage` 실패는 바닥만 중단. **측정**: `st_blocks` 눈금 · 심링크 안 따라감 · 하드링크는 `charged` 에 들고 `reclaimable` 에서 빠짐 · 캐시가 mtime 으로 무효화 · **`MEASURE_MAX_AGE` 로도 무효화** · tar 은 캐시 안 함 · `measured_at` 은 재귀 측정 시각. **실행**: 삭제 직전 상태 재확인(계획 뒤 활성으로 바뀐 잡은 건너뛴다) · 워크스페이스 성공 + tar 실패 → 다음 회차 재시도 · **한 회차에 `plan()` 한 번 · `disk_usage` 호출 횟수** · 여유가 안 오르면 **`no_progress` latch** 가 서고 다음 자동 sweep 의 바닥 규칙이 안 돈다 · latch 는 `gc` 로 풀린다 · `last_sweep_at`/`next_sweep_at` 은 한 번도 안 돌았으면 null |
| `tests/test_janitor_evidence.py` | **증거 불변** 통합: 예산·바닥을 아무리 세게 걸어도 `jobs/<id>/log.txt` · `manifest.json` · 잡 행 · 이벤트 · 번들(`artifacts/`) · blob · 미러가 **하나도 안 지워진다** |
| `tests/test_server_m5g.py` | `job_storage` 키 집합·null 규칙·**산식**(`volume = workspace + snapshot = evictable + non_evictable`) · `/api/health.storage` · `POST /gc` 는 admin 만(401/403) · `dry_run` 은 아무것도 안 지우고 `storage_after` 가 null · 실제 gc 의 `planned`/`deleted`/`failed` 분리 · 잘못된 본문(`{"dry_run": "false"}` · 모르는 키) 400 · **락**: 자동 sweep 대 dry-run · 자동 sweep 대 실제 gc · 동시 gc 둘 · 예외 뒤 락 해제 |
| `tests/test_cli_m5g.py` | `rcm gc` 표·`--json` · **서버 없이 `--dry-run --config`** 가 같은 계획을 낸다 · 타임아웃이면 **종료 코드 3** 과 「모른다」 안내 · `rcm check` 의 storage 행 여섯 모양(§5.3) |
| `tests/web/storage.test.js` | 호스트 카드 줄 두 언어 · `error_code` 면 숫자 대신 문구 · 예산 초과·latch 는 `warn` · 옛 문서(키 없음)에서 안 깨짐 |
| `tests/test_docs_m5g.py` | `examples/server.toml` 새 키 3줄(**주석 아님** — 값이 그대로 있어야 한다) · `docs/configuration.md` 보존 키 표 · `docs/operating.md` 「Retention」의 업그레이드 절차 · 프리셋 권고 절 · CHANGELOG `[Unreleased]` |

**고쳐야 하는 기존 테스트**

| 파일 | 왜 |
|---|---|
| `tests/test_config.py` | 새 키 3개의 기본값 · 범위 밖 · **`workspace_retention_days > min(success, failure)`** 교차 검증(양쪽 다) |
| `tests/test_status_schema.py` | `server` 의 키 집합에 `job_storage` 추가 |
| `tests/test_server_m5e.py:876` · `tests/test_compat_m5e.py:171` | `artifact_storage` 키 집합 비교 — `last_sweep_at` 이 **실제로 채워지는** 회귀 테스트를 여기에 더한다(§3.1 A) |
| `tests/test_janitor.py` | tar 이 부피 시계를 탄다 — **성공 잡의 tar 도** 하루에 사라지는 것, 그 잡의 로그는 14일까지 남는 것 |
| `tests/test_artifacts_rules.py` · 워커 테스트 | `artifacts_on` — claim 정책에 실리는 것 · 필드 없는 옛 서버는 `"always"` · 성공 잡에서 `"failure"` 면 안 모으고 상태는 `disabled` 가 아니라 그대로 |
| `tests/test_examples.py` | `examples/server.toml` ↔ `templates/` 바이트 동일 |
| `AGENTS.md:64` | 「12 known mutations」 → 15 |

규칙은 오늘과 같다: **테스트를 먼저 써서 빨간 것을 보고** 구현한다. 실제로 27 GB 를 만들지 않는다 —
크기는 인자이고, janitor 테스트는 몇 KB 짜리 가짜 트리와 가짜 `disk_usage` 로 잰다.

## 9. mutcheck (3종 추가 — **12 → 15**)

| 이름 | 파일 | 변이 | 무엇을 지키나 |
|---|---|---|---|
| ⑬ `retention-budget-active` | `core/retention.py` | `workspaces_to_purge` 의 「활성·고아는 후보 아님」 필터 제거 | 예산·바닥이 **도는 잡의 워크스페이스를 지우는** 사고. 이 기능의 최대 사고다 |
| ⑭ `retention-measure-fail-open` | `core/retention.py` | 못 잰 크기를 `None` 대신 `0` 으로 | 「못 쟀는데 안 넘은 것 같다」로 조용히 예산을 안 지키는 버그(결정 55) |
| ⑮ `retention-budget-unreachable` | `core/retention.py` | `budget_unreachable` 분기 제거(= 못 이룰 목표에도 계속 고른다) | 도는 잡·고아가 예산을 넘겼을 때 **종료 잡 전부를 태우는** 사고(결정 55 후단) |

세 변이로 `pytest` 가 **실제로 빨개지는 것**을 확인한 뒤에만 「검증됨」이라고 쓴다.

## 10. PR 순서 · 완료 기준

브랜치 하나에 워크트리 하나. PR 마다 새 워크트리를 판다.

- **PR 1(이 문서)**: `docs/m5g-workplan.md` + `docs/reviews/2026-09-09-codex-m5g-design.md` +
  `PLAN.md`(마일스톤 M5g · 설정 키 · 결정 51~62). 코드 없음.
- **PR 2 규칙**: 설정 키·검증 + `core/retention` 의 순수 모델 + `Janitor`(인벤토리·측정·계획·실행·
  latch) + §3.1 A 의 `last_sweep_at` 버그 + mutcheck ⑬⑭⑮ + `examples/server.toml` +
  `docs/configuration.md`·`docs/operating.md` + CHANGELOG(**동작 변경**으로 표시).
- **PR 3 표시·gc**: `server.job_storage` · `/api/health.storage` · `rcm check` · 웹 두 언어 ·
  `POST /gc` + `rcm gc [--dry-run] [--json] [--timeout]` + **서버 없는 `--dry-run --config`** +
  문서 + CHANGELOG.
- **PR 4 프리셋 권고**: `artifacts_on`(claim 정책 배선 포함) + `docs/configuration.md` 의 권고 절 +
  `examples/` + CHANGELOG.

**완료 기준**

1. 새 설치는 `rcm init server` 가 써 준 파일에서 세 값을 **보고** 시작한다(결정 60). 로그(14/30)·
   메타데이터(180)·번들(24시간)·blob(30일/4 GiB)은 그대로다. CHANGELOG 에 동작 변경으로 적고
   되돌리는 법(`workspace_retention_days = 30`)을 같은 줄에 적는다.
2. 개발 인스턴스(127.0.0.1:8788 · 자기 `data_dir`)에서 **세 규칙이 각각 발동하는 것을 보인다** —
   ① 나이 ② 예산(작은 `workspace_storage_max_bytes`) ③ 바닥(가짜 `disk_usage`).
   `rcm gc --dry-run` 이 먼저 목록을 내고, 실제 sweep 이 같은 것을 지운다(**통제된 시험에서**).
3. **활성 잡과 고아는 어떤 규칙으로도 안 지워진다.** 그리고 **증거는 어떤 압박에서도 안 지워진다**
   (`test_janitor_evidence.py`).
4. 크기를 못 재거나 스캔·DB 가 실패하면 예산·바닥은 건너뛰고 **나이는 돈다**. `error_code` 와
   `rcm check` warn 이 그 사실을 말한다.
5. `/api/status`·`/api/health`·`rcm check`·웹에 용량과 다음 청소 시각이 보이고 **산식이 맞는다**.
   `schema_version` 은 **1 그대로**(키 추가만). 옛 CLI·옛 페이지가 안 깨진다.
6. `rcm gc --dry-run` 은 아무것도 안 지우고, `rcm gc` 는 admin 토큰만 받으며, 응답이
   `planned`/`deleted`/`failed` 를 가른다. 타임아웃은 **3(모른다)** 이지 실패가 아니다.
7. mutcheck **15종**이 전부 빨개진다. `ruff` · `pytest` · `node --test` · `smoke_install.sh` 통과.
8. **업그레이드 안전 게이트가 실제로 실행 가능하다**(결정 61). 절차는
   `docs/operating.md` 에 이 순서로 적는다 — ① `git pull --ff-only`(서비스는 아직 옛 코드로 돈다)
   ② **`rcm gc --dry-run --config ~/.config/rcm/server.toml`** 으로 새 규칙이 무엇을 지울지 본다
   ③ 납득하면 서비스 재시작. ⚠️ 초안의 「올리기 전에 dry-run」은 **불가능했다**(리뷰 8번): `POST /gc`
   는 새 서버에만 있고 새 서버는 뜨자마자 sweep 한다. 그래서 dry-run 이 **서버 없이** 도는 것이
   이 기준의 전제다.

## 11. 오너 결정 (기본값으로 구현하고 확인 대기)

| # | 결정 | 기본값(⛔ 이대로 구현) |
|---|---|---|
| 51 | 로그와 부피를 따로 재운다 | `workspace_retention_days = 1` 을 새로 두고 로그·메타데이터는 그대로(14 · 30 · 180). 부피에는 **그 잡의 스냅샷 `tree.tar.gz` 도 포함**한다 — 종료된 잡에서 아무도 안 읽는다(§3.1 D). 성공 잡의 tar 이 14일에서 하루로 짧아지는 것이 이 결정의 유일한 부수 효과다. **기본값 1일은 오너 확정(2026-09-09)** 이고 명세 초안은 2일이었다. 이름에 `_failure` 를 안 붙이는 이유: 성공 잡의 워크스페이스는 애초에 남지 않는다 |
| 52 | 바이트 예산 | `workspace_storage_max_bytes = 107374182400`(100 GiB), `0` = 무제한. 재고 지우는 대상은 **워크스페이스 + 스냅샷 tar** 뿐 — 로그·번들·blob 은 각자의 예산이 있고 이 예산이 손대지 않는다. 정상 상태(44 GB)의 두 배가 넘게 잡아 **평소에는 안 발동하는 천장**이다 |
| 53 | 여유 공간 바닥 | `min_free_bytes = 10737418240`(10 GiB), `0` = 안 본다. 웹 카드의 `DISK_LOW_FREE` 와 같은 값. **비율(85%)은 안 쓴다** — 그건 사람에게 알리는 기준이지 지우는 기준이 아니다 |
| 54 | 한 회차에 한 번만 계획한다 | 잰 값으로 계획 → 실행 → 다시 잰다. 「여유가 오를 때까지」 루프는 없다. ⚠️ 그 보호는 **한 회차짜리**다(리뷰 2번) — 회차 사이는 결정 62 의 latch 가 막는다. 그리고 `min_free_bytes` 는 **유지되는 불변식이 아니라 회수 시도**다 |
| 55 | 못 재면 **압박 삭제만** 멈춘다 | 크기를 못 잰 워크스페이스가 하나라도 있으면 그 회차의 **예산·바닥을 건너뛰고** `job_storage.error_code` 와 `rcm check` warn 으로 알린다. **나이 규칙은 그대로 돈다** — 그건 오늘도 도는 정상 보존 정책이고 크기를 안 본다. 둘을 한 문장으로 묶어 잠그면 디스크가 차는 동안 나이 규칙까지 멈춘다. 그리고 **지울 수 없는 바이트(도는 잡 + 고아)만으로 예산을 넘으면 예산 규칙은 아무것도 안 고른다** — 못 이룰 목표를 위해 증거를 태우지 않는다(`budget_unreachable`). 바닥 규칙은 그래도 돈다(응급이고 한 걸음이 이득이다) |
| 56 | 어디에 보여주나 | 셋 다 — `/api/status server.job_storage` · `/api/health storage` · `rcm check` 의 `storage` 행 · 웹 호스트 카드 한 줄. 스키마 v1 에 **키를 더한다** |
| 57 | 손으로 청소하기 | `rcm gc [--dry-run] [--json] [--timeout]` · `POST /gc`(admin). dry-run 은 janitor 와 **같은 계획 함수**를 돌린다 — 보장되는 건 「같은 입력에 같은 판정」이지 **「보여준 것과 실제가 같다」가 아니다**(두 요청 사이에 잡이 끝난다, 리뷰 7번). 실제 gc 응답은 `planned`/`deleted`/`failed` 를 가르고, CLI 타임아웃은 **3(모른다)** 이다 |
| 58 | 프리셋이 증거를 남기는 법 | 새 개념을 안 만든다 — 무거운 구간 출력은 `TMPDIR` 이 아니라 **워크스페이스**에 두고 M5e `artifacts` 로 선언하고, 「왜 깨졌는지」는 stdout 으로 흘려 **로그**(30일)에 남긴다. **프리셋별 TTL 은 안 만든다** — 24시간은 「가져간다」는 용도에 맞고, 늘리면 `artifact_storage_max_bytes` 와 싸운다 |
| 60 | 설정 표면 | 세 키를 `examples/server.toml`(= `templates/server.toml`, `rcm init server` 가 쓰는 파일)에 **주석 없이 기본값과 함께** 넣고, `docs/configuration.md` 에 보존 키 표를 새로 만든다. 데이터를 지우는 값은 설치할 때 파일에서 보여야 한다 — 오너의 운영 설정이 `recent_count` 한 줄만 바뀐 채 나머지가 전부 「보이지 않는 코드 기본값」이었다. 진짜 「고급 설정」 화면이 생기면 이 셋이 거기 첫 줄이다(그 화면은 M5g 범위 밖) |
| 61 | 업그레이드 안전 게이트 | `rcm gc --dry-run` 이 **서버 없이도** 돈다 — `rcm check --config` 처럼 설정과 데이터 디렉터리만 읽어(DB 는 읽기 전용) 같은 계획을 낸다. 절차는 `git pull` → **오프라인 dry-run** → 서비스 재시작. 초안의 「올리기 전에 dry-run」은 **불가능했다**(리뷰 8번): `POST /gc` 는 새 서버에만 있고 새 서버는 뜨자마자 sweep 한다. ⛔ 대안(코덱스 제안): 「기존 설정에 키가 없으면 압박 삭제를 안 켠다」 — 오너가 그쪽을 원하면 바꾼다 |
| 62 | 무진전 latch | 바닥 규칙으로 지웠는데 여유가 **지운 바이트의 절반도 안 늘면** `no_progress` 를 세우고, 그 뒤 자동 sweep 의 **바닥 규칙만** 멈춘다(나이·예산은 돈다). `rcm gc` 나 재시작으로 풀린다. 근거: 지워도 `df` 가 안 움직이면(로컬 스냅샷 · 열린 파일 · 하드링크) 계속 지우는 것은 증거를 태우는 일 말고 아무것도 아니다. `rcm check` 가 FAIL 로 사람을 부른다 |
| 59 | 실패했을 때만 모으기 | 프리셋 키 `artifacts_on = "always"`(기본, 오늘의 동작) `\| "failure"`. 초록 잡마다 무거운 로그를 모아 24시간 들고 있을 이유가 없다 — 그 비용이 §6 의 권고를 안 따르게 만든다 |

## 12. 위험

| 위험 | 대응 |
|---|---|
| **업그레이드만으로 27 GB 가 사라진다** | 실제로 그렇게 된다(하루 지난 워크스페이스 대부분). 그래서 결정 61 의 **오프라인 dry-run** 이 있고, 완료 기준 8 이 그 절차를 `docs/operating.md` 에 못 박고, CHANGELOG 가 동작 변경으로 적으며 되돌리는 법을 같은 줄에 적는다 |
| 측정 실패가 예산을 영구히 끈다 | 의도한 방향이다(결정 55). 대신 **조용하지 않다** — `error_code` · `rcm check` warn · 서버 로그. ⚠️ 그 상태에서 **디스크는 계속 찬다** — 「fail-closed 니까 안전하다」고 말하지 않는다(리뷰 1번) |
| 바닥을 못 지킨다 | 지킬 수 없다. `min_free_bytes` 는 **회수 시도**지 불변식이 아니다(리뷰 2번). 진전이 없으면 latch 가 서고 `rcm check` 가 FAIL 로 사람을 부른다(결정 62) |
| 하드링크를 지워도 여유가 안 는다 | `charged_bytes` 와 `reclaimable_bytes` 를 갈랐다(§4.4). 바닥의 예상 회수량은 `st_nlink > 1` 블록을 **안 센다** — 「지우면 이만큼 는다」고 거짓말하지 않기 위해서다 |
| 캐시가 안쪽 파일 변화를 놓친다 | 최상위 mtime 은 append 를 못 잡는다. `MEASURE_MAX_AGE`(24시간)가 오차를 하루에 한 번 씻고, `measured_at` 이 화면에 뜬다(§4.4) |
| 첫 sweep 이 느리다 | 서버 시작 직후 한 번 6.65초(38만 파일). 청소기 스레드에서 돌고 큐를 막지 않는다. 문서에 적는다 |
| `rcm gc` 가 CLI 타임아웃 뒤에도 서버에서 계속 돈다 | `--timeout`(기본 600초)과 **종료 코드 3(모른다)** + 「다시 dry-run 으로 보라」 안내. 「타임아웃 = 실패」로 찍지 않는다(리뷰 7번) |
| 폭주하는 로그 하나가 디스크를 채운다 | 이 마일스톤이 안 막는다(§7). `stuck` 이 먼저 잡고, 로그 상한은 증거를 자르는 결정이라 따로 물어야 한다 |
| 원격 워커 디스크는 그대로 찬다 | §3.1 B. 서버가 지울 수 없다. 별도 이슈 |
| 하루 뒤 정확한 재현이 안 된다 | 사실이다(리뷰 5번). 감사 기록은 **로그와 잡 행**이지 입력 트리가 아니라고 문서에 적는다(§7) |
| `rcm gc` 가 사고를 낸다 | admin 토큰 · dry-run 이 같은 계획을 보여 준다 · `_operation_lock` 으로 청소기·다른 gc 와 안 겹친다 · 삭제 직전 상태 재확인 · 규칙이 janitor 와 **한 벌**이라 gc 전용 버그가 안 생긴다 |

## 12.5 리뷰 반영 (`docs/reviews/2026-09-09-codex-m5g-design.md`)

1차는 모델 접근이 끊겨 중단됐고(관찰 넷 반영), **2차가 완주해 「조건부 승인」**을 냈다. P0 넷 · P1
넷 · P2 셋을 전부 반영했다. 큰 것만:

- **P0 ①** 업그레이드 게이트가 **실행 불가능했다** — 「올리기 전에 `rcm gc --dry-run`」인데 `POST /gc`
  는 새 서버에만 있고 새 서버는 뜨자마자 sweep 한다. → dry-run 이 **서버 없이** 돈다(결정 61).
- **P0 ②** 순수 모델이 비어 있었다 — `created_at` 이 없는데 판정이 쓰고, `budget_unreachable` 이
  반환형에 없고, 파생값이 `None` 이 될 수 없었고, 고아·활성이 입력에 없었다. → §4.3 을 다시 썼다.
- **P0 ③** 「dry-run 이 보여준 것과 실제가 다를 수 없다」는 **틀린 말**이다. 그리고 지금
  `Janitor._lock` 은 sweep 직렬화 락이 아니다. → §4.6 에 `_operation_lock` 의 소유자·범위·순서를
  적고, gc 응답을 `planned`/`deleted`/`failed` 로 갈랐다.
- **P0 ④** 검증이 `retention_days_failure` 만 봤다. 성공 잡의 tar 도 부피 시계를 타므로
  **`min(success, failure)`** 여야 한다.
- **P1 ⑤** 「한 번만 계획」은 **한 회차짜리 보호**다 — `df` 가 계속 안 움직이면 여러 회차에 걸쳐
  전부 지운다. → `no_progress` latch(결정 62)와 `rcm check` FAIL.
- **P1 ⑥** 캐시가 tar 을 표현하지 못했고 하드링크를 「살짝 크게」로 낙관했다. → 캐시를 갈랐고
  `charged` 와 `reclaimable` 을 갈랐고 `MEASURE_MAX_AGE` 를 뒀다.
- **P2 ⑨** 회계 산식이 안 맞았다(`workspace_bytes − evictable_bytes` 가 활성 몫이라는 설명은 tar
  때문에 거짓). → `volume_bytes`·`non_evictable_bytes`·`orphan_bytes` 를 넣어 산식을 닫았다.
- **P2 ⑪** `artifacts_on` 은 호출부 두 곳만 고쳐서는 **원격에서 안 돈다** — claim payload 에 정책이
  없다. → §6 에 배선 넷을 적었다.

**틀린 사실로 지적받아 고친 것**: 「성공 잡의 워크스페이스라는 경우가 존재하지 않는다」(두 정리 모두
`rmtree(ignore_errors=True)` 라 남을 수 있다) · 「정상 상태 44 GB」(1일 기준 22 GB) · 「DB v9 는 M5f
몫」(M5f 는 v8 이고 이미 머지됐다) · PLAN 결정 55·57·58 의 낡은 문면.

## 13. 이 명세가 발견한 기존 문제

| # | 무엇 | 어디 | 어떻게 |
|---|---|---|---|
| A | `server.artifact_storage.last_sweep_at` 이 **항상 null** | `server.py:660` 이 없는 속성 `self.janitor` 를 본다(실제 이름은 `self.retention`) | PR 2 에서 고친다 + 값이 채워지는 회귀 테스트 |
| B | 원격 워커는 **시작할 때 한 번만** 워크스페이스를 지우고, 기준이 상수 7일이다 | `remote_worker.py:60`·`:596` | M5g 범위 밖. 별도 이슈 — 오래 사는 워커는 영영 청소하지 않는다 |
| D | **원격 워커는 산출물을 아예 안 모은다 — M5e 가 원격 풀에서 죽어 있다** | `_claim_payload`(`remote_workers.py:373`)의 `preset_doc` 에 `artifacts` 가 **없고** 최상위 `artifacts` 객체도 안 싣는다. 워커의 `_policy_from_claim`(`remote_worker.py:167`)은 둘 다 못 찾아 `None` 을 돌려주고, `None` 이면 수집을 건너뛴다 | **실측으로 확인했다**(아래). 시험은 손으로 만든 claim payload 를 쓰기 때문에(`tests/test_worker_m5e.py:287`) 이 구멍을 못 잡는다. M5g PR 4 가 같은 자리를 고치므로 거기서 함께 고치거나, 더 급하면 별도 hotfix |
| C | **PLAN.md 결정 번호 39~42 가 두 번 나온다** | `PLAN.md:608-611`(M5f) 와 `:621-624`(M5e). 머지 `d26b3f4` 의 흔적이고 양쪽 문서가 이미 각자의 번호로 서로를 가리킨다 | 여기서 고치지 않는다(양쪽 문서의 상호 참조가 깨진다). 오너가 어느 쪽을 옮길지 정하면 그 PR 에서 한 번에 바꾼다. M5g 는 **51번부터** 쓴다 |

**D 의 확인** — 서버가 만드는 payload 를 그대로 재현해 워커의 파서에 넣었다:

```python
preset = Preset(name="goldens", argv=["flutter", "test"], artifacts=("test/**/goldens/*.png",))
preset_doc = {  # remote_workers.py:373-385 그대로
    "name": preset.name,
    "argv": list(preset.argv),
    "timeout_seconds": preset.timeout_seconds,
    "env": dict(preset.env),
    "env_passthrough": list(preset.env_passthrough),
    "source_modes": list(preset.source_modes),
    "repo": preset.repo or None,
}
claimed = {"job": {"id": 7}, "tree_url": "/worker/jobs/7/tree", "preset": preset_doc}
_policy_from_claim(claimed)  # → None   (preset.artifacts 는 ('test/**/goldens/*.png',) 인데도)
```

`None` 이면 워커는 수집을 건너뛴다. **`artifacts` 를 선언한 프리셋이 원격 풀에서 돌면 산출물이 하나도
안 모인다** — 잡은 성공하고 화면도 아무 말을 안 한다. 이 저장소 오너의 Mac 은 `mac2` 워커를 등록해
두었으므로 그 풀에 산출물 프리셋을 걸면 바로 겪는다.
