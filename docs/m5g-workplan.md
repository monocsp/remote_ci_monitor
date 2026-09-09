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
| 실패한 잡의 로그를 30일 들고 있고 싶다. 워크스페이스도 30일이어야 하나? | 그렇다. `retention_days_failure` 하나가 둘 다 지배한다 | 아니다. 로그 30일 · 워크스페이스 `workspace_retention_days`(2) |
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
| **부피** | `workspaces/<id>/` · `jobs/<id>/tree.tar.gz` | `workspace_retention_days`(2) | 그렇다. 오래된 종료 잡부터 |
| 잡 산출물 묶음 | `artifacts/<id>/` | `artifact_retention_hours`(24) — 자기 시계 | 아니다(결정 41: 남의 묶음을 쫓아내지 않는다) |
| 스냅샷 캐시 | `blobs/` | `snapshot_cache_days`(30) + `snapshot_cache_max_bytes`(4 GiB) — 자기 예산 | 아니다(자기 예산이 있다) |
| 미러 | `mirrors/` | 없다 — 안 지운다(M3 그대로) | 아니다 |

- **증거를 지우지 않는다**는 것이 이 설계의 안전 성질이다. 압박을 받으면 720 MB 를 내놓지 50 KB 를
  내놓지 않는다. 실패 잡의 로그 30일은 오늘 그대로다.
- 성공 잡의 워크스페이스는 오늘도 끝나자마자 지운다(`worker.py:476`). 그래서 새 날짜 키에
  `_failure` 접미사를 붙이지 않는다 — **성공 잡의 워크스페이스라는 경우가 존재하지 않는다.**
  (오너 프롬프트의 이름은 `workspace_retention_days_failure` 였다. 짧은 쪽을 권한다 — 결정 51.)

### 4.2 설정 키 (`[server]`)

```toml
[server]
retention_days_success = 14              # 이미 있는 키 — 성공 잡의 로그
retention_days_failure = 30              # 이미 있는 키 — 비성공 잡의 로그
workspace_retention_days = 2             # 새 키 — 남겨 둔 워크스페이스와 그 잡의 스냅샷 tar
workspace_storage_max_bytes = 107374182400   # 새 키 — 부피의 상한(100 GiB). 0 = 무제한
min_free_bytes = 10737418240             # 새 키 — 파일 시스템 여유 바닥(10 GiB). 0 = 안 본다
```

**타입**(`_apply_section` 이 기본값의 타입으로 TOML 값을 맞춘다, `config.py:313`):
`workspace_retention_days: int = 2` · `workspace_storage_max_bytes: int = 107_374_182_400` ·
`min_free_bytes: int = 10_737_418_240`. 순수 계층의 `WorkspaceBudget` 은 이 셋을 인자로 받는다
(`core/queue.QueueConfig` · `core/admission.AdmissionConfig` 와 같은 방식 — 순수 계층은 `config.py` 를
모른다).

**검증** — 문구는 섹션·키 이름이 들어가고 작은따옴표를 쓰는 오늘의 방식(`config.py:635`):

| 키 | 규칙 | 문구 |
|---|---|---|
| `workspace_retention_days` | `>= 0` | `[server] workspace_retention_days must be >= 0` |
| `workspace_retention_days` | `<= retention_days_failure` | `[server] workspace_retention_days must be <= retention_days_failure (30)` |
| `workspace_storage_max_bytes` | `0` 또는 `>= 1 GiB` | `[server] workspace_storage_max_bytes must be 0 (no limit) or at least 1 GiB` |
| `min_free_bytes` | `>= 0` | `[server] min_free_bytes must be >= 0` |

- 두 번째 규칙의 이유: 잡 디렉터리가 먼저 지워지면 **워크스페이스도 그때 같이 지워진다**
  (`_purge_job` 이 둘을 함께 지운다). 워크스페이스에 더 긴 값을 줘도 지켜지지 않으므로, 조용히
  무시하는 대신 시작할 때 거절한다.
- `workspace_storage_max_bytes` 의 하한을 1 GiB 로 두는 이유: 게이트 잡 하나가 720 MB 다. 그보다
  작은 예산은 「끝나는 족족 지운다」와 같고, 그건 `workspace_retention_days = 0` 이 이미 표현한다.
  `snapshot_cache_max_bytes` 가 1 MiB 하한을 두는 것과 같은 종류의 방어다.

**기본값의 근거**

| 값 | 왜 |
|---|---|
| `workspace_retention_days = 2` | 하루 50개 × 435 MB ≈ 21.8 GB/일 → 정상 상태 **약 44 GB**. 여유 628 GB 의 7%. 어제 깨진 잡의 워크스페이스를 이틀 안 열어 봤으면 코드가 이미 앞으로 갔다 — 그때부터 그 720 MB 의 값은 0 이다. 「왜 깨졌나」는 로그(30일)에 남는다 |
| `workspace_storage_max_bytes = 100 GiB` | 날짜 규칙의 정상 상태(44 GB)보다 넉넉히 위 — **평소에는 안 발동하고**, 하루에 200개가 들어오는 날이나 1.6 GB 짜리 잡이 몰리는 날에만 천장이 된다. 이 머신 여유의 16%. 예산을 정상 상태 가까이(50 GiB) 잡으면 매일 발동해서 「2일 보관」이 거짓말이 된다 |
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
class WorkspaceInfo:
    """워크스페이스 하나. `bytes` 는 janitor 가 실제로 잰 값이고, 못 쟀으면 None 이다."""

    job_id: int
    state: str
    finished_at: datetime | None
    bytes: int | None


@dataclass(frozen=True)
class WorkspaceBudget:
    days: int  # workspace_retention_days
    max_bytes: int  # workspace_storage_max_bytes. 0 = 무제한
    min_free_bytes: int  # min_free_bytes. 0 = 안 본다


REASON_AGE, REASON_BUDGET, REASON_FREE = "age", "budget", "free"  # 결정 37 — 코드를 내려보낸다


@dataclass(frozen=True)
class PurgeItem:
    job_id: int
    bytes: int | None
    reason: str  # 이 잡을 고른 **첫** 규칙


@dataclass(frozen=True)
class PurgePlan:
    items: tuple[PurgeItem, ...]  # finished_at 오름차순 — 오래된 것부터
    freed_bytes: int  # 잰 것만 더한 값
    total_bytes: int | None  # 못 잰 게 하나라도 있으면 None
    over_budget_bytes: int  # 계획을 다 지워도 남는 초과분(0 이면 해결)
    short_free_bytes: int  # 계획을 다 지워도 모자라는 여유(0 이면 해결)


def workspaces_to_purge(
    items: Iterable[WorkspaceInfo],
    now: datetime,
    budget: WorkspaceBudget,
    *,
    free_bytes: int | None,
) -> PurgePlan: ...
```

`WorkspaceInfo.bytes` 는 **워크스페이스와 그 잡의 스냅샷 tar 을 합한** 값이다(§4.1 의 「부피」). 둘은 같은 시계로 자고 같이 지워지므로 규칙은 하나로 센다 — 화면과 `rcm gc` 만 janitor 가 재 둔 둘을 갈라서 보여 준다(§5.5).

판정 순서 — **나이 → 예산 → 바닥**, 셋 다 오래된 것부터:

1. **활성 잡을 먼저 뺀다.** `state not in TERMINAL_STATES` 는 어떤 규칙으로도 후보가 아니다. 총량
   계산에는 남는다(디스크는 그 바이트를 실제로 쥐고 있다) — 지울 수 없을 뿐이다.
2. **나이**: `now - (finished_at or created_at) >= days × 86400` → `REASON_AGE`.
   **크기를 안 본다** — 못 잰 워크스페이스도 나이가 되면 지운다.
3. **예산**: `max_bytes > 0` 이고 `total_bytes` 를 알 때만. 남은 총량이 `max_bytes` 아래로 내려갈
   때까지 오래된 것부터 → `REASON_BUDGET`.
4. **바닥**: `min_free_bytes > 0` 이고 `free_bytes` 를 알 때만. `free_bytes + 지울 바이트` 가
   `min_free_bytes` 이상이 될 때까지 오래된 것부터 → `REASON_FREE`.
5. 남는 초과·부족은 `over_budget_bytes`·`short_free_bytes` 로 **숨기지 않고 돌려준다**. 지울 게
   없는데 여전히 모자란 상태는 사람이 알아야 하는 상태다(§5.3 의 `rcm check` FAIL).

**못 재면 안 지운다(fail-open 금지).** 후보 중 `bytes is None` 이 하나라도 있으면 `total_bytes` 가
None 이 되고 **3·4 는 아무것도 안 고른다.** 2(나이)는 그대로 돈다. 이유: 총량을 모르는 채로
「대충 넘은 것 같으니 지운다」는 증거를 조용히 잃는 길이다(결정 55).

### 4.4 재는 법 — `janitor.py`

```python
def _measure_dir(self, path: Path) -> int | None:
    """os.scandir 재귀 · st_blocks × 512 · 심링크는 따라가지 않는다. OSError 면 None."""
```

- **`st_blocks × 512`** 를 쓴다(`du` 와 같은 눈금). `st_size` 는 희소 파일·APFS 클론에서 디스크가
  실제로 쥔 양과 다르다. 예산의 목적은 「디스크가 얼마나 찼나」다.
- **심볼릭 링크는 따라가지 않는다**(`entry.is_dir(follow_symlinks=False)`). `_remove_tree` 가 링크를
  링크로만 지우는 것과 같은 규칙이다. 하드링크(같은 레포의 `git_ref` clone)는 링크마다 세므로 총량이
  살짝 크게 나온다 — 예산이 보수적으로 동작하는 쪽이라 그대로 둔다(문서에 적는다).
- **한 번 재고 기억한다.** 종료된 잡의 워크스페이스는 다시 안 변한다. `Janitor._sizes: dict[int, int]`
  에 담고 디렉터리가 사라지면 버린다. 활성 잡은 자라므로 매 sweep 다시 잰다(레인 수만큼이라 싸다).
  §2.2 의 실측: 첫 sweep 6.65초(38만 파일), 이후 sweep 은 **새로 종료된 잡 몇 개**(하루 50개 · 시간당
  약 2개 → 0.2초). 서버가 뜨자마자 도는 첫 sweep 이 가장 비싸다는 것을 문서에 적는다.
- **여유 공간**은 `shutil.disk_usage(data_dir)` — 호스트 표본과 같은 함수이지 표본을 재사용하지
  않는다. 표본은 낡을 수 있고(`stale`), 청소는 **지금** 값으로 판단해야 한다.

### 4.5 한 회차에 한 번만 계획한다 (결정 54)

계획은 **잰 값으로 한 번** 세우고, 실행하고, 끝난 뒤 여유를 **다시 잰다.** 「여유가 바닥을 넘을
때까지 지운다」는 루프를 두지 않는다.

이유: 지워도 여유가 안 오르는 파일 시스템이 실제로 있다. macOS 의 Time Machine 로컬 스냅샷은 지운
파일의 블록을 붙잡고 있어 `df` 가 안 움직인다. 루프를 돌면 **워크스페이스를 하나도 남김없이 지우고도**
바닥을 못 넘는다. 한 번만 계획하면 최악이 「이번 회차에 예측한 만큼만 지우고 `short_free_bytes` 를
보고한다」로 끝난다. 다음 회차에 다시 판단한다.

### 4.6 서버 배선

| 자리 | 무엇 |
|---|---|
| `Janitor.plan(now)` | 후보 수집 → 측정 → `workspaces_to_purge` → `PurgePlan`. **아무것도 안 지운다** |
| `Janitor.sweep_once` | 오늘의 잡 디렉터리 청소 + `plan()` 실행(워크스페이스·tar 삭제) + 번들 + 메타데이터 + blob |
| `Janitor.storage(now)` | 화면용 회계(§5.1). 마지막 측정값을 쓴다 — 상태 요청이 디스크를 훑지 않는다 |
| `App.job_storage()` | `server.artifact_storage()` 와 같은 모양 · 같은 실패 규칙(예외 → 값 None + `error_code`) |
| `App.gc(dry_run)` | `POST /gc`. janitor 의 sweep 락을 잡고 같은 `plan()` 을 돌린다 |

- 삭제 순서는 잡마다 **워크스페이스 → 스냅샷 tar**. 중간에 실패하면 그 잡은 표시하지 않고 다음
  회차에 다시 시도한다(오늘의 규칙 그대로).
- 워크스페이스 삭제는 **표시가 필요 없다**. `artifacts_purged_at` 은 지금처럼 「잡 디렉터리까지
  지웠다」에만 찍는다. 이미 없는 경로의 삭제는 `_remove_tree` 가 조용히 통과하므로(FileNotFoundError)
  다시 계획에 올라도 무해하다 — **DB 마이그레이션이 필요 없는 이유가 이것이다.**
- 활성 잡 보호는 삼중 그대로: 순수 규칙이 거르고 · `_purge_workspace` 가 상태를 다시 보고 ·
  실행 중 잡의 워크스페이스 경로는 워커가 쥐고 있다.

## 5. 보이게 하기 (결정 37 — 서버는 문장이 아니라 코드를 내려보낸다)

### 5.1 `/api/status` → `server.job_storage` (스키마 v1, 키 추가)

```json
"job_storage": {
  "workspace_bytes": 30940831744, "snapshot_bytes": 3379809, "log_bytes": 41680896,
  "evictable_bytes": 29884170240,
  "limit_bytes": 107374182400, "free_bytes": 674309865472, "min_free_bytes": 10737418240,
  "over_budget_bytes": 0, "short_free_bytes": 0,
  "measured_at": "2026-09-09T05:00:03Z",
  "last_sweep_at": "2026-09-09T05:00:03Z", "next_sweep_at": "2026-09-09T06:00:03Z",
  "error_code": null
}
```

- `evictable_bytes` 는 **종료 잡의** 워크스페이스+tar 합이다. `workspace_bytes` 와의 차이가 지금
  도는 잡이 쥔 몫이다 — 「예산을 넘었는데 왜 안 줄지?」의 답이 화면에 있다.
- 못 잰 값은 `null`(0 이 아니다) + `error_code`. `queue_error`·`hosts_error` 와 같은 규칙이다.
- `limit_bytes`·`min_free_bytes` 는 0(끔)이면 `null` 로 싣는다 — 화면이 `—` 로 그린다.
- 바이트 필드 이름은 전부 `_bytes` 로 끝난다(스키마 규칙).

### 5.2 `/api/health` → `storage`

```json
"storage": {"workspace_bytes": 30940831744, "free_bytes": 674309865472,
            "limit_bytes": 107374182400, "min_free_bytes": 10737418240,
            "last_sweep_at": "…", "next_sweep_at": "…", "exhausted": false}
```

- **경로는 안 싣는다.** health 는 토큰 없이 열린다.
- **503 조건은 안 바꾼다.** 예산 초과는 고장이 아니라 다음 sweep 이 처리할 일이고, 청소기가 죽는
  것은 이미 503 이다. `exhausted`(= 바닥 아래인데 지울 게 없다)는 **사실만 싣고** 판단은 `rcm check`
  와 사람에게 맡긴다 — health 를 감시하는 쪽이 자기 기준으로 쓸 수 있다.

### 5.3 `rcm check` — `storage` 한 줄 (영어)

```
ok    storage       rcm data 30.9 GB of 107.4 GB · 674 GB free · next sweep in 42m
warn  storage       rcm data 118.2 GB over the 107.4 GB budget — the next sweep will trim it
warn  storage       size of 2 workspaces could not be measured — the budget is not enforced
FAIL  storage       4.1 GB free, under the 10.7 GB floor, and nothing left to delete
```

`ok`/`warn`/`FAIL` 은 이미 있는 세 값이다(`cmd_check` 의 `bool | None`). FAIL 은 **바닥 아래이고
`exhausted`** 일 때만 — 다음 sweep 이 고칠 수 있는 상태는 경고지 실패가 아니다.

### 5.4 웹 호스트 카드 (두 언어)

디스크 미터 아래 한 줄을 더한다. 서버 자신의 호스트 카드에만 그린다(`job_storage` 는 서버의 사실이지
호스트의 사실이 아니다).

```
디스크  120 / 460 GB                              26% · 여유 340 GB
        rcm 데이터 30.9 GB / 107.4 GB · 다음 청소 42분 뒤
```

- 예산 초과·바닥 아래면 `warn` 색(디스크 미터가 이미 쓰는 `.warn`).
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

- `POST /gc`, **admin 토큰만**(`require_admin`). 본문 `{"dry_run": true}`.
- 응답은 계획 그대로: `{"dry_run", "items": [{job_id, state, workspace_bytes, snapshot_bytes,
  reason, finished_at}], "freed_bytes", "storage": {…§5.1…}}`.
- **janitor 와 같은 `plan()` 을 쓴다.** dry-run 이 보여준 것과 실제가 다를 수 없다.
- sweep 락을 잡는다 — 청소기와 동시에 돌지 않는다.
- `--json` 은 응답 그대로. 사람용 표는 `core/render_text` 에 둔다(순수).

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

- **묶음은 보관소가 아니다.** `artifact_retention_hours`(24)는 워크스페이스의 2일보다 **짧다.**
  가져가는 통로이지 증거의 서랍이 아니다 — 서랍은 로그(30일)다. 문서에 이 순서를 명시한다.
- **프리셋별 TTL 은 만들지 않는다.** 24시간이 「가져간다」는 용도를 덮고, 더 길게 두면
  `artifact_storage_max_bytes`(10 GiB)와 싸운다. 필요하면 전역 값을 올린다(결정 58).
- **`artifacts_on = "failure"` 는 만든다.** 초록 잡마다 40 MB 를 모아 24시간 들고 있을 이유가 없고,
  그것 때문에 사람들이 이 권고를 안 따르게 된다. 기본값은 `"always"` — **오늘의 동작이 그대로다.**
  판정은 수집 직전 한 곳(`worker.py` · `remote_worker.py` 의 수집 호출부)에서 `state != succeeded`
  로 한다(결정 59).

## 7. 안 만드는 것

| 무엇 | 왜 |
|---|---|
| 잡 로그의 크기 상한·회전 | 증거를 자르는 기능이라 별도 결정이 필요하다. 실측 137개 39 MB — 지금 아픈 데가 아니다. 폭주하는 로그는 `stuck` 이 먼저 잡는다. §12 에 위험으로 적는다 |
| 고아 워크스페이스 삭제 | 잡 행이 없는 `workspaces/<n>` 은 회계에 **싣기만** 한다(`rcm gc --dry-run` 이 보여준다). 「주인을 못 찾는 데이터를 지운다」는 규칙은 틀리면 조용히 증거를 잃는다 |
| 원격 워커의 예산 | 서버는 워커 디스크를 지울 권한이 없다. §3.1 B 의 버그(시작할 때 한 번, 상수 7일)는 별도 이슈다 |
| blob·번들을 압박에서 지우기 | 각자 예산이 있고, 번들은 「남의 것을 쫓아내지 않는다」가 결정 41 이다 |
| 비율 기준(`min_free_ratio`) | §4.2 — 큰 디스크에서 아무 이득 없이 증거만 잃는다 |
| DB 마이그레이션 | 워크스페이스 삭제는 멱등이라 표시가 필요 없다(§4.6). DB v9 는 M5f 몫으로 비워 둔다 |

## 8. 테스트 배치

**새로 쓰는 것**

| 파일 | 무엇 |
|---|---|
| `tests/test_retention_workspace.py` | 순수 전수: 나이 경계(`>=`) · 활성 네 상태(`uploading`·`queued`·`running`·`cancelling`) 전부 후보 아님 · 예산이 오래된 것부터 · 예산 0 은 무제한 · 바닥이 오래된 것부터 · 바닥 0 은 안 봄 · `free_bytes is None` 이면 바닥 규칙 없음 · **`bytes is None` 이 하나면 예산·바닥이 아무것도 안 고르고 나이는 그대로 돈다** · `over_budget_bytes`·`short_free_bytes` · 같은 잡이 두 규칙에 걸려도 한 번만 · 정렬 |
| `tests/test_janitor_m5g.py` | 측정 캐시(종료 잡은 한 번, 활성 잡은 매번) · `st_blocks` 눈금 · 심링크를 안 따라감 · 측정 실패 → `error_code` 하고 나이만 · **한 회차에 한 번만 계획한다**(여유가 안 오르는 가짜 `disk_usage` 로 전부 지우지 않는 것을 잠근다) · 삭제 실패는 다음 회차 · `last_sweep_at`/`next_sweep_at` |
| `tests/test_server_m5g.py` | `job_storage` 키 집합·null 규칙 · `/api/health.storage` · `POST /gc` 는 admin 만(401/403) · `dry_run` 은 아무것도 안 지운다 · gc 와 sweep 이 겹치지 않는다 |
| `tests/test_cli_m5g.py` | `rcm gc` 표·`--json` · `rcm check` 의 storage 행 세 모양(ok/warn/FAIL) |
| `tests/web/storage.test.js` | 호스트 카드 줄 두 언어 · `error_code` 면 숫자 대신 문구 · 예산 초과 `warn` · 옛 문서(키 없음)에서 안 깨짐 |
| `tests/test_docs_m5g.py` | `examples/server.toml` 새 키 3줄 · `docs/configuration.md` 의 보존 표 · `docs/operating.md` 「Retention」 · 프리셋 권고 절 · CHANGELOG `[Unreleased]` |

**고쳐야 하는 기존 테스트**

| 파일 | 왜 |
|---|---|
| `tests/test_config.py` | 새 키 3개의 기본값 · 범위 밖 · `workspace_retention_days > retention_days_failure` 교차 검증 |
| `tests/test_status_schema.py` | `server` 의 키 집합에 `job_storage` 추가 |
| `tests/test_server_m5e.py:876` · `tests/test_compat_m5e.py:171` | `artifact_storage` 키 집합 비교 — `last_sweep_at` 이 **실제로 채워지는** 회귀 테스트를 여기에 더한다(§3.1 A) |
| `tests/test_janitor.py` | `_purge_job` 이 이제 tar 을 부피 시계로 다룬다 — 성공 잡의 tar 이 2일에 사라지는 것 |
| `tests/test_examples.py` | `examples/server.toml` ↔ `templates/` 바이트 동일 |
| `AGENTS.md:64` | 「12 known mutations」 → 14 |

규칙은 오늘과 같다: **테스트를 먼저 써서 빨간 것을 보고** 구현한다. 실제로 27 GB 를 만들지 않는다 —
크기는 인자이고, janitor 테스트는 몇 KB 짜리 가짜 트리로 잰다.

## 9. mutcheck (2종 추가 — **12 → 14**)

| 이름 | 파일 | 변이 | 무엇을 지키나 |
|---|---|---|---|
| ⑬ `retention-budget-active` | `core/retention.py` | `workspaces_to_purge` 의 「활성 잡은 후보 아님」 필터 제거 | 예산·바닥이 **도는 잡의 워크스페이스를 지우는** 사고. 이 기능의 최대 사고다 |
| ⑭ `retention-measure-fail-open` | `core/retention.py` | 못 잰 크기를 `None` 대신 `0` 으로 | 「못 쟀는데 안 넘은 것 같다」로 조용히 예산을 안 지키는 버그(결정 55) |

두 변이로 `pytest` 가 **실제로 빨개지는 것**을 확인한 뒤에만 「검증됨」이라고 쓴다.

## 10. PR 순서 · 완료 기준

브랜치 하나에 워크트리 하나. PR 마다 새 워크트리를 판다.

- **PR 1(이 문서)**: `docs/m5g-workplan.md` + `PLAN.md`(마일스톤 M5g · `[server]` 설정 키 3개 ·
  결정 51~59). 코드 없음. Codex 크로스리뷰를 받으면 `docs/reviews/2026-09-09-codex-m5g-design.md`.
- **PR 2 규칙**: 설정 키·검증 + `core/retention.workspaces_to_purge` + `Janitor`(측정·계획·실행) +
  §3.1 A 의 `last_sweep_at` 버그 + mutcheck ⑬⑭ + `examples/server.toml` + `docs/configuration.md`·
  `docs/operating.md` 의 보존 절 + CHANGELOG(**동작 변경**으로 표시).
- **PR 3 표시**: `server.job_storage` · `/api/health.storage` · `rcm check` · 웹 두 언어 ·
  `POST /gc` + `rcm gc [--dry-run] [--json]` + 문서 + CHANGELOG.
- **PR 4 프리셋 권고**: `artifacts_on` 프리셋 키 + `docs/configuration.md` 의 권고 절
  (§6 의 좋은/나쁜 예) + `examples/` + CHANGELOG.

**완료 기준**

1. 업그레이드만으로 **워크스페이스만** 2일로 짧아진다. 로그(14/30)·메타데이터(180)·번들(24시간)·
   blob(30일/4 GiB)은 그대로다. CHANGELOG 에 동작 변경으로 적는다.
2. 개발 인스턴스(127.0.0.1:8788 · 자기 `data_dir`)에서 **세 규칙이 각각 발동하는 것을 보인다** —
   ① 나이 ② 예산(작은 `workspace_storage_max_bytes` 로) ③ 바닥(작은 여유를 흉내 낸 `disk_usage`).
   `rcm gc --dry-run` 이 먼저 목록을 내고, 실제 sweep 이 같은 것을 지운다.
3. **활성 잡의 워크스페이스는 어떤 규칙으로도 안 지워진다** — 순수 테스트 + `lanes = 1` e2e.
4. 크기를 못 재면 예산·바닥은 건너뛰고 **나이는 돈다**. `job_storage.error_code` 와 `rcm check` 의
   warn 이 그 사실을 말한다.
5. `/api/status`·`/api/health`·`rcm check`·웹에 데이터 디렉터리 용량과 다음 청소 시각이 보인다.
   `schema_version` 은 **1 그대로**(키 추가만). 옛 CLI·옛 페이지가 안 깨진다.
6. `rcm gc --dry-run` 은 아무것도 안 지우고, `rcm gc` 는 admin 토큰만 받는다.
7. mutcheck **14종**이 전부 빨개진다. `ruff` · `pytest` · `node --test` · `smoke_install.sh` 통과.
8. 실기(오너): 운영에서 **먼저 `rcm gc --dry-run`** 으로 27 GB 중 무엇이 지워질지 보고, 납득한 뒤에
   서비스를 올린다. 「업그레이드했더니 사라졌다」가 되면 안 된다.

## 11. 오너 결정 (기본값으로 구현하고 확인 대기)

| # | 결정 | 기본값(⛔ 이대로 구현) |
|---|---|---|
| 51 | 로그와 부피를 따로 재운다 | `workspace_retention_days = 2` 를 새로 두고 로그·메타데이터는 그대로(14 · 30 · 180). 부피에는 **그 잡의 스냅샷 `tree.tar.gz` 도 포함**한다 — 종료된 잡에서 아무도 안 읽는다(§3.1 D). 성공 잡의 tar 이 14일에서 2일로 짧아지는 것이 이 결정의 유일한 부수 효과다. 이름에 `_failure` 를 안 붙이는 이유: 성공 잡의 워크스페이스는 애초에 남지 않는다 |
| 52 | 바이트 예산 | `workspace_storage_max_bytes = 107374182400`(100 GiB), `0` = 무제한. 재고 지우는 대상은 **워크스페이스 + 스냅샷 tar** 뿐 — 로그·번들·blob 은 각자의 예산이 있고 이 예산이 손대지 않는다. 정상 상태(44 GB)의 두 배가 넘게 잡아 **평소에는 안 발동하는 천장**이다 |
| 53 | 여유 공간 바닥 | `min_free_bytes = 10737418240`(10 GiB), `0` = 안 본다. 웹 카드의 `DISK_LOW_FREE` 와 같은 값. **비율(85%)은 안 쓴다** — 그건 사람에게 알리는 기준이지 지우는 기준이 아니다 |
| 54 | 한 회차에 한 번만 계획한다 | 잰 값으로 계획 → 실행 → 다시 잰다. 「여유가 오를 때까지」 루프는 없다. macOS 로컬 스냅샷처럼 지워도 여유가 안 오르는 경우에 **전부 지우게 되는** 것을 막는다 |
| 55 | 못 재면 안 지운다 | 크기를 못 잰 워크스페이스가 하나라도 있으면 그 회차의 **예산·바닥을 건너뛰고** `job_storage.error_code` 와 `rcm check` warn 으로 알린다. **나이 규칙은 그대로 돈다**(나이는 크기를 안 본다) |
| 56 | 어디에 보여주나 | 셋 다 — `/api/status server.job_storage` · `/api/health storage` · `rcm check` 의 `storage` 행 · 웹 호스트 카드 한 줄. 스키마 v1 에 **키를 더한다** |
| 57 | 손으로 청소하기 | `rcm gc [--dry-run] [--json]` · `POST /gc`(admin). dry-run 은 janitor 와 **같은 계획 함수**를 돌려 지울 목록·바이트·사유를 낸다 |
| 58 | 프리셋이 증거를 남기는 법 | 새 개념을 안 만든다 — 무거운 구간 출력은 `TMPDIR` 이 아니라 **워크스페이스**에 두고 M5e `artifacts` 로 선언하고, 「왜 깨졌는지」는 stdout 으로 흘려 **로그**(30일)에 남긴다. **프리셋별 TTL 은 안 만든다** — 24시간은 「가져간다」는 용도에 맞고, 늘리면 `artifact_storage_max_bytes` 와 싸운다 |
| 59 | 실패했을 때만 모으기 | 프리셋 키 `artifacts_on = "always"`(기본, 오늘의 동작) `\| "failure"`. 초록 잡마다 무거운 로그를 모아 24시간 들고 있을 이유가 없다 — 그 비용이 §6 의 권고를 안 따르게 만든다 |

## 12. 위험

| 위험 | 대응 |
|---|---|
| **업그레이드만으로 27 GB 가 사라진다** | 실제로 그렇게 된다(2일 지난 워크스페이스 대부분). 그래서 완료 기준 8 이 **`rcm gc --dry-run` 을 먼저** 돌리는 것이고, CHANGELOG 가 동작 변경으로 적고, 되돌리는 법(`workspace_retention_days = 30`)을 같은 줄에 적는다 |
| 측정 실패가 예산을 영구히 끈다 | 의도한 fail-closed 다(결정 55). 대신 **조용하지 않다** — `error_code` · `rcm check` warn · 서버 로그 한 줄. 「지우는 쪽이 조용히 틀리는」 것보다 낫다 |
| 첫 sweep 이 느리다 | 서버 시작 직후 한 번 6.65초(38만 파일). 청소기 스레드에서 돌고 큐를 막지 않는다. 문서에 적는다 |
| 하드링크를 두 번 센다 | 같은 레포의 `git_ref` clone 이 미러 객체를 하드링크한다. 총량이 크게 나와 예산이 **보수적으로** 동작한다 — 안전한 방향이라 그대로 두고 문서에 적는다 |
| 폭주하는 로그 하나가 디스크를 채운다 | 이 마일스톤이 안 막는다(§7). `stuck` 이 먼저 잡고, 로그 상한은 증거를 자르는 결정이라 따로 물어야 한다 |
| 원격 워커 디스크는 그대로 찬다 | §3.1 B. 서버가 지울 수 없다. 별도 이슈로 남기고 `rcm check` 가 워커 디스크를 이미 보여 준다 |
| `rcm gc` 가 사고를 낸다 | admin 토큰 · 기본은 실행이지만 dry-run 이 같은 계획을 보여 준다 · sweep 락으로 청소기와 안 겹친다 · 지우는 규칙 자체가 janitor 와 **한 벌**이라 gc 전용 버그가 생기지 않는다 |

## 13. 이 명세가 발견한 기존 문제

| # | 무엇 | 어디 | 어떻게 |
|---|---|---|---|
| A | `server.artifact_storage.last_sweep_at` 이 **항상 null** | `server.py:660` 이 없는 속성 `self.janitor` 를 본다(실제 이름은 `self.retention`) | PR 2 에서 고친다 + 값이 채워지는 회귀 테스트 |
| B | 원격 워커는 **시작할 때 한 번만** 워크스페이스를 지우고, 기준이 상수 7일이다 | `remote_worker.py:60`·`:596` | M5g 범위 밖. 별도 이슈 — 오래 사는 워커는 영영 청소하지 않는다 |
| C | **PLAN.md 결정 번호 39~42 가 두 번 나온다** | `PLAN.md:608-611`(M5f) 와 `:621-624`(M5e). 머지 `d26b3f4` 의 흔적이고 양쪽 문서가 이미 각자의 번호로 서로를 가리킨다 | 여기서 고치지 않는다(양쪽 문서의 상호 참조가 깨진다). 오너가 어느 쪽을 옮길지 정하면 그 PR 에서 한 번에 바꾼다. M5g 는 **51번부터** 쓴다 |
