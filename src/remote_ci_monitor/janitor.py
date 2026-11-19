"""보존 정리 스레드 — 기간 지난 종료 잡의 로그·스냅샷·워크스페이스를 지우고 DB 에 표시한다.

- 무엇을 지울지는 순수 규칙(`core/retention.py`)이 정하고, 여기서는 종료 상태를 **한 번 더**
  확인한다(이중 안전). DB 의 `mark_artifacts_purged` 도 종료 잡만 갱신하므로 삼중이다.
- 경로는 정수 id 로만 만든다. 심볼릭 링크면 링크만 지우고 따라가지 않는다. 실제 경로가
  data_dir 밖이면 건드리지 않는다(프리셋 스크립트가 워크스페이스를 바꿔치기했을 때).
- 삭제에 실패한 잡은 표시하지 않고 다음 sweep 에 다시 시도한다. 오류는 `on_error` 로 표면화.
- 메타데이터(잡 행 · 이벤트 · 합류자)는 `metadata_retention_days` 가 지나고 산출물이 이미
  지워진 잡만 삭제한다 — DB 가 무한히 자라지 않는다.
- 스레드가 예외로 죽으면 `dead` 가 되고 `/api/health` 가 503 을 낸다(조용히 멈추지 않는다).
- 보관함(M5p)은 자기 시계로 정리한다(`_sweep_shelf`) — 정리 대기 표시 · 삭제 · 묘비. 삭제는 보관함
  폴더를 기준 루트로 받는 `remove_under_root` 를 쓴다(보관함은 data_dir 밖일 수 있다). 기동 화해
  `reconcile_shelf_on_start` 도 여기 있지만 **서버 기동만** 부른다(dry-run 이 실제 폴더를 고치면
  안 된다).
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from remote_ci_monitor.config import RepoConfig, ServerConfig, effective_workspace_retention_days
from remote_ci_monitor.core import artifacts
from remote_ci_monitor.core import shelf as shelf_rules
from remote_ci_monitor.core.model import TERMINAL_STATES, TOKEN_ADMIN, Job, ShelfItem
from remote_ci_monitor.core.retention import (
    PurgePlan,
    RetentionPolicy,
    VolumeItem,
    WorkspaceBudget,
    _sum_or_none,
    blobs_to_purge,
    due_for_purge,
    workspaces_to_purge,
)
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.materialize import blob_path, stale_blob_keys
from remote_ci_monitor.store import Store, TokenInfo

#: 이만큼 안 보인 워커는 잊는다(활성 잡이 없을 때만).
WORKER_FORGET_DAYS = 7
CANDIDATE_LIMIT = 1000
#: 워크스페이스 크기를 다시 재기까지의 상한. 최상위 mtime 은 **안쪽 파일의 append 를 못 잡는다**
#: (잡이 남긴 백그라운드 프로세스가 계속 쓰면 오차에 상한이 없다). 하루에 한 번은 오차를 씻는다.
MEASURE_MAX_AGE = timedelta(days=1)
#: 지운 바이트의 이만큼도 여유가 안 늘면 「지워도 소용없다」로 본다(무진전 latch, 결정 62).
PROGRESS_RATIO = 0.5
#: 만료 청소가 «버리기» 를 낼 때 쓰는 토큰 이름 — 잡의 `requester_label` 이 `store:janitor` 가
#: 되어 로그에서 사람이 누른 것과 구분된다. DB 에 없는 이름이다(라우트를 거치지 않으므로).
JANITOR_TOKEN_NAME = "janitor"
#: 「인자를 안 줬다」와 「None 을 줬다」를 가르는 표식 — 후자는 「여유를 못 쟀다」는 사실이다.
_UNSET: Any = object()
#: 보관 항목을 지우기 직전에 적어 두는 `error_code` — 지운 뒤 행을 고치기 전에 죽었다는 표시다.
DELETING = "deleting"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _errname(e: BaseException) -> str:
    """오류의 **종류**만. 경로도 메시지도 안 싣는다(로그에 시크릿이 섞이지 않게).

    `errno` 는 `OSError` 에만 있다 — DB 오류를 이걸로 포맷하다 `AttributeError` 가 나면 sweep
    스레드가 죽고 보존 정리가 영구히 멈춘다.
    """
    if isinstance(e, OSError):
        return errno.errorcode.get(e.errno or 0, type(e).__name__)
    return type(e).__name__


def _api_code(e: BaseException) -> str:
    """서버가 거절한 이유의 **코드**(`setup_incomplete` · `version_running` …). `ApiError` 는
    코드를 `extra` 에 담는데, `server` 를 import 하면 순환이라(server → janitor) 구조적으로
    읽는다. 코드가 없으면 오류의 종류만 — 문장도 경로도 로그에 싣지 않는다.
    """
    extra = getattr(e, "extra", None)
    if isinstance(extra, dict) and isinstance(extra.get("code"), str):
        return str(extra["code"])
    return _errname(e)


def _version_name(row: dict[str, Any]) -> str:
    """행의 대표 이름 — iOS 가 있으면 iOS, 없으면 Android(서버의 `version_build_name` 과 같다)."""
    return str(row["ios_version"] or row["android_version"] or "?")


class VersionHost(Protocol):
    """만료 청소가 쓰는 서버의 얼굴. `DELETE …/versions/<id>` 핸들러가 부르는 **바로 그**
    함수라, 청소기만 아는 지름길은 없다(제출된 행 거절 · 도는 중 거절 · 잡 제출이 전부 저기
    한 곳에 있다). 권한은 호출자가 본 것으로 치므로 청소기가 admin 토큰을 지어 넘긴다."""

    def discard_version(
        self, repo: RepoConfig, row: dict[str, Any], token: TokenInfo, host: str | None
    ) -> tuple[int, dict[str, Any]]: ...


def _item_json(item: Any) -> dict[str, Any]:
    """계획 한 줄 → JSON. 사유는 **코드**다(결정 37 — 서버는 문장을 안 보낸다).

    바이트는 charged(링크마다)이고 `shared_bytes` 가 그 안의 공유 몫,
    `estimated_reclaimable_bytes` 가 지우면 실제로 느는 하한이다(결정 76). 셋을 같이 실어야
    화면이 「would free」를 과장하지 않는다.
    """
    return {
        "job_id": item.job_id,
        "workspace_bytes": item.workspace_bytes,
        "snapshot_bytes": item.snapshot_bytes,
        "shared_bytes": item.shared_bytes,
        "estimated_reclaimable_bytes": item.estimated_reclaimable_bytes,
        "reason": item.reason,
    }


class Measured(NamedTuple):
    """디렉터리(또는 파일) 하나의 두 눈금. 둘 다 `st_blocks × 512`."""

    charged: int  # 링크마다 센 값
    shared: int  # 그중 일반 파일의 `nlink > 1` 블록 — 지워도 여유가 안 는다


class Applied(NamedTuple):
    """`apply()` 의 결과 — **실제로 지운 것**만. 계획을 영수증처럼 쓰지 않는다(§5.5)."""

    deleted: tuple[int, ...]
    failed: tuple[tuple[int, str], ...]  # (job_id, error code)
    charged_bytes: int  # 지운 항목의 charged 합(아는 것만)
    estimated_reclaimable_bytes: int  # 지운 항목의 예상 회수량 합(아는 것만) — latch 의 분모
    unknown_count: int  # 크기를 모르는 채 지운 항목 수
    #: 실패했지만 **일부는 사라진** 항목 — (job_id, 사라진 조각 이름들). `failed` 에도 들어
    #: 있다. 디스크가 변했으니 회계는 이것도 보고 다시 재야 한다(리뷰 #88 B2).
    partial: tuple[tuple[int, tuple[str, ...]], ...] = ()


class PurgeOutcome(NamedTuple):
    """`_purge_volume` 한 번의 결과. 조각(`workspace` · `snapshot`)마다 무엇이 지워졌는지."""

    removed: tuple[str, ...]  # 실제로 디스크에서 사라진 조각 — 없었던 조각은 세지 않는다
    error: str | None  # 첫 실패의 코드. None 이면 끝까지 갔다


def _may_have_tar(job_dir: Path) -> bool:
    """`jobs/<id>/tree.tar.gz` 가 있을 수 있나. **못 본 것은 있는 것으로** 친다 — `Path.is_file()`
    은 ENOENT 는 삼키지만 EACCES 는 올려서, 읽을 수 없는 잡 디렉터리 하나가 인벤토리 전체를
    `scan_EACCES` 로 죽이고 나이 규칙까지 멈췄다(M5l L2.2). 항목으로 넣어 두면 `_measure_file`
    이 그 항목만 「모름」으로 두고 코드(`measure_EACCES`)를 남긴다."""
    try:
        return (job_dir / "tree.tar.gz").is_file()
    except OSError:
        return True


def _shared_blocks(st: Any) -> int:
    """이 stat 이 **공유 블록**이면 그 바이트, 아니면 0.

    일반 파일(`S_ISREG`)의 `nlink > 1` 만이다 — 디렉터리의 `nlink > 1` 은 하위 디렉터리 때문에
    정상이고, 그 블록은 디렉터리를 지우면 돌아온다.
    """
    if st.st_nlink > 1 and stat.S_ISREG(st.st_mode):
        return getattr(st, "st_blocks", 0) * 512
    return 0


def remove_under_root(path: Path, root: Path) -> bool:
    """경로 하나를 지운다. 링크(와 파일)는 그것만 지우고, 디렉터리는 실제 경로가 `root` 안일 때만.

    `root` 는 부르는 쪽이 `resolve()` 한 기준 루트다 — 보관함은 data_dir 밖(다른 디스크)일 수
    있어서 data_dir 을 기준으로 삼으면 EXDEV 로 거절된다(M5p 검토 P1-5). 밖을 가리키면 손대지
    않고 `OSError(EXDEV)`. 안의 심볼릭 링크는 `shutil.rmtree` 가 링크만 지운다.
    **있던 것을 지웠으면 True**, 애초에 없었으면 False.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(st.st_mode):  # 링크 · 파일
        path.unlink()
        return True
    real = path.resolve()
    if root != real and root not in real.parents:
        raise OSError(errno.EXDEV, "path resolves outside its root")
    shutil.rmtree(path)
    return True


class ShelfStartup(NamedTuple):
    """기동 화해의 결과(§5.3). `available` 이 False 면 새 보관도 청소도 하지 않는다."""

    available: bool
    error_code: str | None


def _valid_manifest(folder: Path, job_id: int) -> tuple[int, int] | None:
    """copying 입양의 「유효한 manifest」(§15.3) — JSON 이 읽히고 `job` 이 맞고, 나열된 파일이 모두
    `files/` 아래에 있고 크기가 같다(해시는 안 잰다). (파일 수, 바이트) 또는 None.

    바이트는 원본 + 펼침 + manifest 자신이다 — 상한 판정의 실측과 같은 눈금(§4.2-5).
    """
    path = folder / "manifest.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        size = path.stat().st_size
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("job") != job_id:
        return None
    files = doc.get("files")
    if not isinstance(files, list):
        return None
    total = size
    for f in files:
        if not isinstance(f, dict):
            return None
        rel, want = f.get("path"), f.get("size")
        if not shelf_rules.safe_member(rel) or not isinstance(want, int):
            return None
        try:
            st = (folder / "files" / rel).lstat()
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode) or st.st_size != want:
            return None
        total += want
    return len(files), total


def _clear_dir(path: Path) -> None:
    """디렉터리 안을 비운다(링크는 링크만). 없으면 할 일이 없다."""
    try:
        entries = list(path.iterdir())
    except FileNotFoundError:
        return
    root = path.resolve()
    for entry in entries:
        remove_under_root(entry, root)


def reconcile_shelf_on_start(
    store: Store,
    config: ServerConfig,
    *,
    now: datetime | None = None,
    log: Callable[[str], None] | None = None,
) -> ShelfStartup:
    """서버가 뜰 때 보관함 폴더와 DB 를 맞춘다(§5.3 · §15.3). 레인 · 청소기 · copier 보다 먼저.

    `rcm gc --dry-run`(`_offline_gc`) · `Janitor` 생성자 · `Store` 생성자는 이것을 부르지 않는다 —
    사본 위의 계획이 실제 보관함 폴더를 고치면 안 된다.

    1. 표식(`.rcm-shelf`)이 없는데 보관 중 · 정리 대기 행이 있으면 `unavailable` — 다른 디스크가 안
       붙은 채 떴다. 행도 폴더도 건드리지 않는다(빈 마운트 지점 아래 루트 디스크에 쓰지 않는다).
       그런 행이 없으면 폴더와 표식을 만든다. 못 만들면 `unavailable(shelf_dir_unwritable)`.
    2. `.staging` 을 비운다.
    3. `copying` 행 — 폴더의 manifest 가 유효하면 `active` 로 입양(`os.replace` 뒤 커밋 전에 죽은
       경우). 폴더는 있는데 manifest 가 무효면 그 폴더는 이 행의 것이므로 지운다. 그다음 묶음이
       살아 있으면 `pending`, 아니면 `failed(bundle_gone)`.
    4. 보관 중 · 정리 대기인데 폴더가 없으면 `failed(missing)` + 로그 한 줄.
    5. 행이 없는 폴더는 지우지 않는다 — 모르는 것은 안 지운다.
    """
    say = log or (lambda msg: None)
    when = now or _utcnow()
    root = config.shelf_dir
    marker = root / shelf_rules.MARKER_NAME
    shelved = store.shelf_items(state=shelf_rules.SHELVED_STATES, limit=None)
    if not marker.is_file():
        if shelved:
            store.shelf_available = False
            say(
                f"shelf: {root} has no {shelf_rules.MARKER_NAME} marker but "
                f"{len(shelved)} items are on the shelf — is the disk mounted? "
                "New items are refused until it is back"
            )
            return ShelfStartup(False, "shelf_dir_missing")
        try:
            root.mkdir(parents=True, exist_ok=True)
            marker.write_text("rcm shelf — do not remove; rcm checks it at start\n")
        except OSError as e:
            store.shelf_available = False
            say(f"shelf: cannot write {root}: {_errname(e)}")
            return ShelfStartup(False, "shelf_dir_unwritable")
    store.shelf_available = True
    real_root = root.resolve()
    _clear_dir(root / shelf_rules.STAGING_NAME)
    for item in store.shelf_items(state=shelf_rules.STATE_COPYING, limit=None):
        folder = root / str(item.job_id)
        found = _valid_manifest(folder, item.job_id)
        if found is not None:
            count, total = found
            store.mark_shelf(
                item.job_id,
                shelf_rules.STATE_ACTIVE,
                when,
                only_from=(shelf_rules.STATE_COPYING,),
                shelved_at=when.timestamp(),
                last_used_at=when.timestamp(),
                file_count=count,
                total_bytes=total,
                **_job_columns(store, item),
            )
            say(f"shelf: #{item.job_id} was copied before the restart — kept")
            continue
        remove_under_root(folder, real_root)
        bundle = store.get_bundle(item.job_id)
        alive = (
            bundle is not None
            and bundle["state"] == artifacts.READY
            and bundle["purged_at"] is None
        )
        if alive:
            store.mark_shelf(
                item.job_id,
                shelf_rules.STATE_PENDING,
                when,
                only_from=(shelf_rules.STATE_COPYING,),
            )
        else:
            store.mark_shelf(
                item.job_id,
                shelf_rules.STATE_FAILED,
                when,
                only_from=(shelf_rules.STATE_COPYING,),
                reason_code="bundle_gone",
            )
    for item in shelved:
        if not (root / str(item.job_id)).is_dir():
            store.mark_shelf(
                item.job_id,
                shelf_rules.STATE_FAILED,
                when,
                only_from=shelf_rules.SHELVED_STATES,
                reason_code="missing",
            )
            say(f"shelf: #{item.job_id} is missing from {root} — marked failed (missing)")
    return ShelfStartup(True, None)


def _job_columns(store: Store, item: ShelfItem) -> dict[str, Any]:
    """입양할 때 잡이 끝났으면 잡 상태 · 끝난 시각을 다시 복사한다(잡 행이 없으면 그대로)."""
    job = store.get_job(item.job_id)
    if job is None or job.state not in TERMINAL_STATES or job.finished_at is None:
        return {}
    return {"job_state": job.state, "job_finished_at": job.finished_at.timestamp()}


class Janitor:
    """보존 정리. `sweep_once` 는 동기, `start` 는 주기 스레드."""

    def __init__(
        self,
        store: Store,
        config: ServerConfig,
        *,
        now_fn: Callable[[], datetime] = _utcnow,
        on_error: Callable[[str], None] | None = None,
        log: Callable[[str], None] | None = None,
        stop: threading.Event | None = None,
        versions: VersionHost | None = None,
        shelf_hold: Callable[[int], bool] = lambda _job_id: False,
        on_shelf_change: Callable[[int], None] | None = None,
        on_sweep: Callable[[], None] | None = None,
    ):
        self.store = store
        self.config = config
        #: 보관 항목을 지금 내려보내는 중인가(zip 스트리밍) — 그 항목은 이번 회차에 안 지운다(§15.3)
        self._shelf_hold = shelf_hold
        #: 보관 항목의 상태가 바뀌었다 — 서버가 그 잡의 SSE 갱신을 낸다(§15.3 「이벤트」)
        self._on_shelf_change = on_shelf_change or (lambda _job_id: None)
        #: 회차가 끝났다 — 서버가 보관 copier 를 깨운다(놓친 보관 요청 줍기, M5p §4.2)
        self._on_sweep = on_sweep or (lambda: None)
        #: 마지막 보관함 회차의 시각과 오류 코드(`/api/status.server.shelf_storage`)
        self.shelf_last_sweep_at: datetime | None = None
        self.shelf_error_code: str | None = None
        #: 서버(있을 때만). 없으면 버전 대장은 **건드리지 않는다** — `rcm gc` 처럼 서버 없이
        #: 도는 청소기가 드래프트를 조용히 버리면 안 된다.
        self._versions = versions
        self.now_fn = now_fn
        self.on_error = on_error or (lambda msg: None)
        self.log = log or (lambda msg: None)
        self.stop_event = stop or threading.Event()
        self.interval = float(config.server.retention_sweep_interval_seconds)
        self.last_sweep_at: datetime | None = None
        self.purged_total = 0
        self.dead: str | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # 짧은 상태 락 — 이 락을 쥔 채 I/O 를 하지 않는다
        #: 청소 작업 전체(계획 + 삭제 + 사후 측정)를 직렬화한다. `sweep_once` 와 `gc` 만 잡는다.
        self._operation_lock = threading.Lock()
        #: job_id → (두 눈금, 잴 때의 최상위 st_mtime_ns, 잰 시각). 메모리에만 있다.
        self._sizes: dict[int, tuple[Measured, int, datetime]] = {}
        #: 바닥 규칙으로 지웠는데 여유가 안 늘었다 — 자동 sweep 의 바닥 규칙을 멈춘다(결정 62).
        self.no_progress = False
        self._last_plan: PurgePlan | None = None
        #: 합계에 기여한 측정 중 **가장 오래된** 시각(I6). 캐시된 값이 섞이면 그 값의 시각이다 —
        #: 「방금 쟀다」는 거짓말을 안 하려고. 인벤토리를 본 시각은 `_inventory_checked_at`.
        self._measured_at: datetime | None = None
        self._inventory_checked_at: datetime | None = None
        #: 이번 인벤토리에서 난 개별 측정 실패의 코드(§4-4). 첫 것이 `inventory_error` 가 된다.
        self._measure_failures: list[str] = []
        #: 마지막 인벤토리의 조각 합(화면용). 하나라도 못 쟀으면 그 칸이 None 이다.
        self._last_totals: dict[str, int | None] = {}

    # ── 상태 ────────────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.dead is None

    def stale(self, now: datetime | None = None) -> bool:
        """주기의 두 배가 지나도록 sweep 이 없으면 멈춘 것으로 본다."""
        if self.last_sweep_at is None:
            return False
        now = now or self.now_fn()
        return (now - self.last_sweep_at) > timedelta(seconds=2 * self.interval)

    # ── 삭제 ────────────────────────────────────────────────────────────────

    def _remove_tree(self, path: Path) -> bool:
        """id 로 만든 경로 하나를 지운다. 링크는 링크만, 밖을 가리키면 손대지 않는다.

        **있던 것을 지웠으면 True**, 애초에 없었으면 False — 영수증이 「사라진 조각」을 셀 때
        없었던 조각을 지운 것처럼 적지 않기 위해서다.
        """
        try:
            st = path.lstat()
        except FileNotFoundError:
            return False
        if not (st.st_mode & 0o170000 == 0o040000):  # S_ISDIR 이 아니면(링크 · 파일)
            path.unlink()
            return True
        return remove_under_root(path, self.config.data_dir.resolve())

    def _purge_job(self, job: Job) -> bool:
        if job.state not in TERMINAL_STATES:  # 이중 안전
            return False
        data = self.config.data_dir
        for path in (data / "jobs" / str(job.id), data / "workspaces" / str(job.id)):
            self._remove_tree(path)
        return True

    def _sweep_bundles(self, now: datetime) -> int:
        """TTL 이 지난 잡 산출물 묶음을 디렉터리째 지운다(명세 §8).

        **번들은 자기 시계로만 지운다** — M3 의 보존 기간(0 일 수 있다)과 무관하다. 삭제에
        실패하면 표시하지 않고 예약을 그대로 둔 채 다음 sweep 에 다시 시도한다. 물리적으로
        지우기 전에 지웠다고 말하지 않는다(§7).
        """
        root = self.config.data_dir / "artifacts"
        purged: list[int] = []
        for info in self.store.bundles_due(now, CANDIDATE_LIMIT):
            try:
                self._remove_tree(root / str(info.job_id))
            except OSError as e:
                self.on_error(f"retention: bundle {info.job_id}: {_errname(e)}")
                continue
            if self.store.bundle_is_held(info.job_id):
                # 아직 내려보내는 중이다 — 파일은 갔지만 독자가 닫을 때까지 회계에 남긴다(§6).
                self.store.set_bundle_state(info.job_id, artifacts.EXPIRED)
                continue
            purged.append(info.job_id)
        if purged:
            self.store.mark_bundles_purged(purged, artifacts.EXPIRED, now)
            self.log(f"retention: expired {len(purged)} artifact bundles")
        return len(purged)

    # ── 보관함 (M5p §4.5 · §15.2 · §15.3) ───────────────────────────────────

    def _shelf_timeouts(self, now: datetime) -> list[int]:
        """안전판 — 묶음 만료 + 24시간이 지나도록 못 집은 보관 요청을 `failed(timeout)` 로 접는다.

        보관함이 `unavailable` 이어도 돈다: 그 요청이 묶음을 영원히 붙잡으면 안 된다.
        """
        try:
            ids = self.store.time_out_shelf_requests(now)
        except Exception as e:  # noqa: BLE001 — 보관함 때문에 보존 정리가 죽으면 안 된다
            self.on_error(f"shelf: timeouts: {_errname(e)}")
            return []
        for job_id in ids:
            self.log(f"shelf: #{job_id} was never copied — gave up (timeout), the bundle goes")
            self._on_shelf_change(job_id)
        return ids

    def plan_shelf(self, now: datetime) -> shelf_rules.SweepPlan:
        """이번 회차라면 무엇을 정리 대기로 옮기고 무엇을 지울지 — **아무것도 바꾸지 않는다**.

        `rcm gc --dry-run` 의 보관함 한 줄(정리 대기 수 · 삭제 예정 수)이 이것을 쓴다(§5.4).
        """
        s = self.config.server
        items = self.store.shelf_items(limit=None)
        return shelf_rules.plan_sweep(
            items, now.timestamp(), s.shelf_stale_days, s.shelf_delete_days
        )

    def _sweep_shelf(self, now: datetime) -> None:
        """보관함 한 회차(§4.5). 실패는 삼키고 `on_error` 로 알린다 — 청소기를 죽이지 않는다."""
        if not self.store.shelf_available:
            return  # 기동 화해가 막았다 — 다른 디스크가 안 붙었다(§15.3)
        try:
            marked = (self.config.shelf_dir / shelf_rules.MARKER_NAME).is_file()
            shelved = not marked and self.store.shelf_items(shelf_rules.SHELVED_STATES, limit=1)
        except Exception as e:  # noqa: BLE001 — 보관함 때문에 보존 정리가 죽으면 안 된다
            self.on_error(f"shelf: sweep failed: {_errname(e)}")
            return
        if shelved:
            # 도는 중에 디스크가 빠졌다. 그대로 돌면 「지울 폴더가 없다」로 색인을 다 잃는다.
            # 보관된 항목이 하나도 없으면 지울 폴더도 없으니 그대로 돈다(묘비 · 실패 행만 본다)
            self.shelf_error_code = "shelf_dir_missing"
            self.on_error("shelf: the shelf folder lost its marker — sweep skipped")
            return
        try:
            self._shelf_round(now)
        except Exception as e:  # noqa: BLE001 — 보관함 때문에 보존 정리가 죽으면 안 된다
            self.on_error(f"shelf: sweep failed: {_errname(e)}")
            return
        self.shelf_error_code = None
        with self._lock:
            self.shelf_last_sweep_at = now

    def _shelf_round(self, now: datetime) -> None:
        plan = self.plan_shelf(now)
        for item in plan.to_active:
            if self.store.mark_shelf(
                item.job_id,
                shelf_rules.STATE_ACTIVE,
                now,
                only_from=(shelf_rules.STATE_STALE,),
                stale_since=None,
            ):
                self._on_shelf_change(item.job_id)
        for item in plan.to_stale:
            if self.store.mark_shelf(
                item.job_id,
                shelf_rules.STATE_STALE,
                now,
                only_from=(shelf_rules.STATE_ACTIVE,),
                stale_since=now.timestamp(),
            ):
                self.log(
                    f"shelf: #{item.job_id} unused for {self.config.shelf_stale_days}d — stale"
                )
                self._on_shelf_change(item.job_id)
        root = self.config.shelf_dir.resolve()
        for item in plan.to_delete:
            self._delete_shelf_item(item, root, now)
        for item in plan.to_forget:
            self.store.forget_shelf(item.job_id)

    def _delete_shelf_item(self, item: ShelfItem, root: Path, now: datetime) -> None:
        """항목 하나를 지운다 — **폴더를 지운 뒤에** 행을 `deleted` 로(결정 134).

        지우기 전에 `error_code = deleting` 을 적어 둔다: 지운 뒤 행을 고치기 전에 죽으면 다음
        회차가 「폴더 없음 + 지우던 중」을 보고 `deleted` 로 따라잡는다(그냥 폴더가 없으면
        `failed(missing)` 이다, §15.3). 실패하면 행은 그대로 두고 `error_code` 에 종류만 남긴다.
        """
        job_id = item.job_id
        if self._shelf_hold(job_id):
            return  # 지금 zip 으로 내려보내는 중이다 — 다음 회차에
        live = shelf_rules.SHELVED_STATES
        folder = self.config.shelf_dir / str(job_id)
        exists = folder.exists() or folder.is_symlink()
        if not exists and item.error_code != DELETING:
            if self.store.mark_shelf(
                job_id, shelf_rules.STATE_FAILED, now, only_from=live, reason_code="missing"
            ):
                self.log(f"shelf: #{job_id} was already gone from the shelf folder (missing)")
                self._on_shelf_change(job_id)
            return
        if not self.store.mark_shelf(job_id, item.state, now, only_from=live, error_code=DELETING):
            return  # 그새 누가 썼거나 지웠다
        try:
            remove_under_root(folder, root)
        except OSError as e:
            code = f"delete_{_errname(e)}"
            self.store.mark_shelf(job_id, item.state, now, only_from=live, error_code=code)
            self.on_error(f"shelf: delete #{job_id}: {_errname(e)}")
            return
        self.store.mark_shelf(
            job_id,
            shelf_rules.STATE_DELETED,
            now,
            only_from=live,
            deleted_at=now.timestamp(),
            total_bytes=0,
            error_code=None,
        )
        self.log(f"shelf: #{job_id} unused for {self.config.shelf_delete_days}d — deleted")
        self._on_shelf_change(job_id)

    # ── 버전 드래프트의 만료 (워크플랜 §2.3 · 결정 Q1 · Q2) ──────────────────

    def sweep_versions(self, now: datetime) -> int:
        """만료된 스토어 버전 드래프트를 정리한다. **버린** 행 수를 돌려준다.

        - **손도 안 댄** 드래프트(`open_versions_expired` — `editing` 이나 `failed` ·
          `last_edit_at` NULL · `expires_at` 지남)만 자동으로 버린다. 길은 라우트의 «버리기» 와
          **같다**(`discard_version`): ASC 버전이 있고 `version` 프리셋이 있으면 `mode=delete`
          잡이 나가고, 아니면 바로 `discarded` 다.
        - **만들기가 실패한**(`failed`) 드래프트도 같이 치운다(워크플랜 §14-3). 그 행은 어느
          조회에도 안 걸리면서 이름만 붙잡고 있었다. 스토어에는 아무것도 없다 —
          `asc_version_id` 는 만들기가 **성공했을 때만** 적히므로 그 행에서는 스토어 삭제 잡이
          나가지 않고 그 자리에서 `discarded` 다.
        - **편집한** 드래프트는 만료돼도 지우지 않는다(Q2). `expiry_warned` 만 켜고 로그 한 줄 —
          상태는 그대로다. 지우는 것은 사람이 «버리기» 로만 한다.
        - 제출된 행은 두 조회 어디에도 안 나온다(E14). 회차가 도는 행 · 만드는 중인 행도
          `open_versions_expired`(state `editing`·`failed` 만)에 안 나오므로 버려지지 않고,
          편집이 있으면 경고 표시만 받는다. 그래도 들어오면 `discard_version` 이 409 로 막는다.
        - 지우기 잡은 행마다 **한 번만** 낸다 — `delete_job_id` 가 이미 있으면 건너뛴다. 그러면
          스토어가 계속 거절하는 버전에 매 sweep 마다 새 삭제를 던지지 않는다(그 행의 `error` 를
          보고 사람이 정한다).
        """
        host = self._versions
        if host is None:
            return 0
        token = TokenInfo(name=JANITOR_TOKEN_NAME, admin=True, created_at=now, kind=TOKEN_ADMIN)
        discarded = 0
        for row in self.store.open_versions_expired(now):
            if row["delete_job_id"] is not None:
                continue
            repo = self.config.repo(row["repo"])
            if repo is None or repo.release is None:
                continue  # 설정에서 사라진 저장소 — 대장은 남기고 사람이 본다
            try:
                _status, doc = host.discard_version(repo, row, token, None)
            except Exception as e:  # noqa: BLE001 — 한 행의 거절이 sweep 을 멈추면 안 된다
                self.on_error(f"versions: draft {row['id']} not discarded: {_api_code(e)}")
                continue
            discarded += 1
            job_id = doc.get("job_id")
            how = f"store delete job #{job_id}" if job_id else "discarded"
            hours = repo.release.version_ttl_hours
            self.log(
                f"versions: draft #{row['id']} {_version_name(row)} untouched for {hours}h — {how}"
            )
        for row in self.store.versions_to_warn(now):
            try:
                self.store.update_version(row["id"], expiry_warned=True)
            except Exception as e:  # noqa: BLE001 — 표시 실패가 sweep 을 멈추면 안 된다
                self.on_error(f"versions: draft {row['id']} not warned: {_errname(e)}")
                continue
            self.log(
                f"versions: draft #{row['id']} {_version_name(row)} expired with edits — "
                "kept, discard it by hand when you are done with it"
            )
        return discarded

    def sweep_once(self, now: datetime | None = None) -> int:
        """기간 지난 잡의 산출물을 지우고 표시한다. 지운 잡 수를 돌려준다.

        작업 락을 잡는다 — 손으로 부른 `rcm gc` 와 동시에 돌지 않는다(둘이 같은 잡을 지운다).
        """
        now = now or self.now_fn()
        # 버전 만료는 작업 락 **밖**이다: 잡 제출(미러 fetch 까지)이 섞여 있어서 그 사이
        # `rcm gc` 를 붙잡으면 안 된다. 실패는 여기서 삼킨다 — 버전 대장 때문에 보존 정리
        # 스레드가 죽으면 `/api/health` 가 503 이 되고 산출물이 영원히 안 지워진다.
        try:
            self.sweep_versions(now)
        except Exception as e:  # noqa: BLE001 — 보존 정리는 계속 돈다
            self.on_error(f"versions: sweep failed: {_errname(e)}")
        with self._operation_lock:
            return self._sweep_locked(now)

    def _sweep_locked(self, now: datetime) -> int:
        policy = RetentionPolicy(
            success_days=self.config.server.retention_days_success,
            failure_days=self.config.server.retention_days_failure,
        )
        candidates = self.store.list_unpurged_finished(CANDIDATE_LIMIT)
        purged: list[int] = []
        for job in due_for_purge(candidates, now, policy):
            try:
                if self._purge_job(job):
                    purged.append(job.id)
            except OSError as e:
                self.on_error(f"retention: job {job.id}: {_errname(e)}")
        if purged:
            self.store.mark_artifacts_purged(purged, now)
            self.log(f"retention: purged {len(purged)} jobs")
        # 보관함의 안전판은 묶음 청소 **앞**이다 — 접힌 보관의 묶음이 같은 회차에 정상 청소로 간다
        self._shelf_timeouts(now)
        self._sweep_bundles(now)
        self._sweep_shelf(now)
        # 은퇴한 워커를 잊는다 — 안 지우면 `server.workers[]` 에 `down` 레인이 영원히 쌓인다.
        # 활성 잡이 있으면 안 지운다(그 잡이 큐에서 사라지면 안 된다).
        try:
            gone = self.store.forget_workers(now - timedelta(days=WORKER_FORGET_DAYS))
        except Exception as e:  # noqa: BLE001 — 워커 정리 실패가 sweep 을 막으면 안 된다
            gone = []
            self.on_error(f"retention: workers: {_errname(e)}")
        if gone:
            self.log(f"retention: forgot {len(gone)} workers unseen for {WORKER_FORGET_DAYS}d")
        cutoff = now - timedelta(days=self.config.server.metadata_retention_days)
        deleted = self.store.delete_old_jobs(cutoff)
        if deleted:
            self.log(f"retention: deleted {deleted} job records older than {cutoff:%Y-%m-%d}")
        if self.config.server.snapshot_cache:
            gone = self.sweep_blobs(now)
            if gone:
                self.log(f"retention: purged {gone} snapshot blobs")
        self.sweep_volume(now)  # 부피(워크스페이스 + 스냅샷 tar) — 로그와 다른 시계 (M5g)
        with self._lock:
            self.last_sweep_at = now
            self.purged_total += len(purged)
        return len(purged)

    def _referenced_blob_keys(self) -> set[str]:
        """활성 잡의 manifest 가 참조하는 blob 키 — 이것들은 GC 대상이 아니다."""
        keys: set[str] = set()
        for job in self.store.list_active():
            path = self.config.data_dir / "jobs" / str(job.id) / "manifest.json"
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                prefix = doc.get("blob_prefix") or ""
                for f in doc.get("files", []):
                    sha = f.get("sha256") if isinstance(f, dict) else None
                    if isinstance(sha, str):
                        keys.add(prefix + sha)
            except (OSError, ValueError) as e:
                self.on_error(f"retention: job {job.id} manifest: {type(e).__name__}")
        return keys

    def sweep_blobs(self, now: datetime) -> int:
        """안 쓰인 지 오래된 blob 과 상한 초과분을 지운다(파일 → 행). 참조된 것은 절대 안 지운다."""
        referenced = self._referenced_blob_keys()
        blobs = self.store.list_blobs()
        # 파일 없는 행(유령)은 보존 규칙과 무관하게 지운다 — 협상이 «있다» 고 답해 세션이 안 올리는
        # 원인이다.
        ghosts = stale_blob_keys(self.config.data_dir / "blobs", (b.sha256 for b in blobs))
        if ghosts:
            self.store.delete_blobs(ghosts)
            self.log(f"retention: dropped {len(ghosts)} blob rows without files")
            gone_set = set(ghosts)
            blobs = [b for b in blobs if b.sha256 not in gone_set]
        victims = blobs_to_purge(
            blobs,
            referenced,
            now,
            days=self.config.server.snapshot_cache_days,
            max_bytes=self.config.server.snapshot_cache_max_bytes,
        )
        gone: list[str] = []
        for b in victims:
            path = blob_path(self.config.data_dir / "blobs", b.sha256)
            try:
                path.unlink(missing_ok=True)
            except OSError as e:
                sha7 = b.sha256.rpartition("/")[2][:7]  # token 범위 키(`<token>/<sha>`)도 sha 만
                self.on_error(f"retention: blob {sha7}: {_errname(e)}")
                continue
            gone.append(b.sha256)
        if gone:
            self.store.delete_blobs(gone)
        return len(gone)

    # ── 부피 회수 (M5g) ─────────────────────────────────────────────────────

    def _scan_ids(self, root: Path) -> set[int]:
        """그 디렉터리 밑의 **정수 이름**만. 없으면 빈 집합, 못 읽으면 OSError 를 올린다.

        「못 읽었다」를 「비었다」로 바꾸면 총량이 0 이 되고 예산이 지켜지는 척한다.
        """
        if not root.is_dir():
            return set()
        out: set[int] = set()
        with os.scandir(root) as it:
            for entry in it:
                if entry.name.isdigit():
                    out.add(int(entry.name))
        return out

    def _measure_dir(self, path: Path) -> Measured | None:
        """디렉터리 하나가 디스크에서 쥔 바이트, 두 눈금으로. 못 읽으면 `OSError` 를 올린다 —
        부르는 쪽이 코드를 지켜 `measure_<errname>` 으로 보고한다(§4-4).

        `st_blocks × 512` 는 `du` 와 같은 눈금이다 — 희소 파일·APFS 클론에서 `st_size` 는
        디스크가 실제로 쥔 양과 다르다. 심볼릭 링크는 **따라가지 않는다**(`_remove_tree` 와 같은
        규칙). 하드링크는 링크마다 세어 `charged` 에 넣고, 일반 파일의 `nlink > 1` 블록을
        `shared` 로 따로 센다 — 그 블록은 지워도 여유가 안 는다(미러가 쥐고 있다).
        """
        charged = shared = 0
        stack = [path]
        while stack:
            current = stack.pop()
            with os.scandir(current) as it:
                for entry in it:
                    st = entry.stat(follow_symlinks=False)
                    charged += getattr(st, "st_blocks", 0) * 512
                    shared += _shared_blocks(st)
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
        st = path.lstat()
        return Measured(charged + getattr(st, "st_blocks", 0) * 512, shared)

    def _measure_failed(self, job_id: int, e: OSError) -> None:
        """개별 측정 실패 — 코드를 지킨다. None 으로 뭉개면 `error_code`·로그에 원인이 안 남는다."""
        code = _errname(e)
        self._measure_failures.append(code)
        self.on_error(f"retention: measure {job_id}: {code}")

    def _measure_workspace(
        self, job_id: int, path: Path, now: datetime
    ) -> tuple[Measured | None, datetime]:
        """캐시를 쓰되 **안 변했는지 확인하고** 쓴다(`lstat` 한 번은 공짜다). (값, 잰 시각).

        종료된 잡의 워크스페이스는 보통 다시 안 변하지만 믿고만 있으면 안 된다 — 잡이 남긴
        백그라운드 프로세스나 사람이 손댈 수 있다. 최상위 mtime 이 그대로여도
        `MEASURE_MAX_AGE` 가 지나면 다시 잰다(안쪽 파일의 append 는 mtime 을 안 건드린다).
        캐시에서 왔으면 **그때의 시각**을 돌려준다 — 회계의 나이가 이것으로 정해진다(I6).
        """
        try:
            mtime = path.lstat().st_mtime_ns
        except OSError as e:
            self._sizes.pop(job_id, None)
            self._measure_failed(job_id, e)
            return None, now
        cached = self._sizes.get(job_id)
        if cached is not None and cached[1] == mtime and now - cached[2] < MEASURE_MAX_AGE:
            return cached[0], cached[2]
        measured = self._measure_now(job_id, path)
        if measured is None:
            self._sizes.pop(job_id, None)
            return None, now
        self._sizes[job_id] = (measured, mtime, now)
        return measured, now

    def _measure_now(self, job_id: int, path: Path) -> Measured | None:
        """캐시 없이 잰다. 실패는 코드를 남기고 None."""
        try:
            return self._measure_dir(path)
        except OSError as e:
            self._measure_failed(job_id, e)
            return None

    def _measure_file(self, job_id: int, path: Path) -> Measured | None:
        """파일 하나(스냅샷 tar). 없으면 0, 못 읽으면 None(코드는 남긴다). 캐시하지 않는다 —
        `lstat` 하나다. 공유 규칙은 디렉터리와 같다."""
        try:
            st = path.lstat()
        except FileNotFoundError:
            return Measured(0, 0)
        except OSError as e:
            self._measure_failed(job_id, e)
            return None
        return Measured(getattr(st, "st_blocks", 0) * 512, _shared_blocks(st))

    def _free_bytes(self) -> int | None:
        """데이터 디렉터리가 앉은 파일 시스템의 여유. 호스트 표본을 재사용하지 않는다 —
        표본은 낡을 수 있고(`stale`) 청소는 **지금** 값으로 판단해야 한다."""
        try:
            return int(shutil.disk_usage(self.config.data_dir).free)
        except (OSError, ValueError):
            return None

    def _jobs_by_id(self, ids: set[int]) -> dict[int, Job]:
        """id → 잡 행. **없는 id 는 안 담는다** — 「행이 없다」와 「조회 실패」는 다른 사실이라,
        조회가 터지면 예외가 그대로 올라가 `inventory_error` 가 된다(고아로 바뀌지 않는다)."""
        out: dict[int, Job] = {}
        for job_id in sorted(ids):
            job = self.store.get_job(job_id)
            if job is not None:
                out[job_id] = job
        return out

    def _is_terminal(self, job_id: int) -> bool:
        """삭제 **직전**의 재확인. 계획과 실행 사이에 잡이 바뀔 수 있다."""
        job = self.store.get_job(job_id)
        return job is not None and job.state in TERMINAL_STATES

    def inventory(self, now: datetime) -> list[VolumeItem]:
        """부피 인벤토리. **디렉터리에서** 얻고 잡 행을 붙인다(명세 §4.4).

        두 스캔은 독립이다 — 원격 워커에서 돈 잡은 서버에 워크스페이스가 없지만 **입력 tar 은
        서버에 남는다.** 워크스페이스 목록에만 기대면 그 tar 이 부피 규칙을 통째로 비껴간다.
        DB 에서 후보를 뽑지 않는 다른 이유: `list_unpurged_finished` 는 1000개 상한이라 총량이
        조용히 작게 나온다.
        """
        data = self.config.data_dir
        ws_root, jobs_root = data / "workspaces", data / "jobs"
        self._measure_failures = []
        oldest = now  # 합계에 기여한 측정 중 가장 오래된 시각 — 빈 인벤토리도 「지금 봤다」
        ids = self._scan_ids(ws_root)
        ids |= {i for i in self._scan_ids(jobs_root) if _may_have_tar(jobs_root / str(i))}
        rows = self._jobs_by_id(ids)
        items: list[VolumeItem] = []
        for job_id in sorted(ids):
            job = rows.get(job_id)  # 행이 없다고 DB 가 **말했을 때만** 고아다
            ws_path = ws_root / str(job_id)
            if not ws_path.exists():
                ws: Measured | None = Measured(0, 0)
            elif job is not None and job.state in TERMINAL_STATES:
                ws, at = self._measure_workspace(job_id, ws_path, now)
                oldest = min(oldest, at)
            else:  # 활성 잡은 자란다. 고아는 주인이 없어 언제 변할지 모른다 — 매번 잰다
                ws = self._measure_now(job_id, ws_path)
            tar = self._measure_file(job_id, jobs_root / str(job_id) / "tree.tar.gz")
            items.append(
                VolumeItem(
                    job_id=job_id,
                    state=job.state if job is not None else None,
                    finished_at=job.finished_at if job is not None else None,
                    created_at=job.created_at if job is not None else None,
                    workspace_bytes=None if ws is None else ws.charged,
                    snapshot_bytes=None if tar is None else tar.charged,
                    shared_bytes=None if ws is None or tar is None else ws.shared + tar.shared,
                )
            )
        self._oldest_measured_at = oldest
        return items

    def _budget(self) -> WorkspaceBudget:
        s = self.config.server
        return WorkspaceBudget(
            days=effective_workspace_retention_days(s),
            max_bytes=s.workspace_storage_max_bytes,
            min_free_bytes=0 if self.no_progress else s.min_free_bytes,
        )

    def plan(self, now: datetime, *, free_bytes: int | None = _UNSET) -> PurgePlan:
        """무엇을 지울지 정하기만 한다. **아무것도 안 지운다.**

        `free_bytes` 를 주면 그 값을 쓴다 — 한 회차가 디스크 여유를 두 번(계획 전·실행 뒤)만
        읽게 하려는 것이다.
        """
        error: str | None = None
        self._oldest_measured_at = now
        try:
            items = self.inventory(now)
        except OSError as e:
            items, error = [], f"scan_{_errname(e)}"
        except Exception as e:  # noqa: BLE001 — DB 오류는 「고아」가 아니라 회계 실패다
            items, error = [], f"db_{_errname(e)}"
        if error is None and self._measure_failures:
            # 개별 측정 실패 — 인벤토리 자체는 있다(나이 규칙은 돈다). 코드를 지켜 올린다(§4-4).
            error = f"measure_{self._measure_failures[0]}"
        if error:
            self.on_error(f"retention: inventory: {error}")
        totals = {
            "workspace_bytes": _sum_or_none(i.workspace_bytes for i in items),
            "snapshot_bytes": _sum_or_none(i.snapshot_bytes for i in items),
            "orphan_bytes": _sum_or_none(i.bytes for i in items if i.state is None),
        }
        with self._lock:
            self._measured_at = None if error else self._oldest_measured_at
            self._inventory_checked_at = None if error else now
            self._last_totals = {k: (None if error else v) for k, v in totals.items()}
        free = self._free_bytes() if free_bytes is _UNSET else free_bytes
        plan = workspaces_to_purge(
            items, now, self._budget(), free_bytes=free, inventory_error=error
        )
        # 계획을 세웠다는 것은 **방금 쟀다**는 뜻이다 — dry-run 도 마찬가지다. 회계가 이 값을
        # 쓰므로 여기서 안 담아 두면 `rcm gc --dry-run` 의 요약이 「모른다」로 나온다.
        with self._lock:
            self._last_plan = plan
        return plan

    def _volume_parts(self, job_id: int) -> tuple[tuple[str, Path], ...]:
        """부피 한 항목의 조각과 경로 — 이 순서로 지운다."""
        data = self.config.data_dir
        return (
            ("workspace", data / "workspaces" / str(job_id)),
            ("snapshot", data / "jobs" / str(job_id) / "tree.tar.gz"),
        )

    def _purge_volume(self, job_id: int) -> PurgeOutcome | None:
        """한 잡의 워크스페이스와 스냅샷 tar. 삭제 직전에 종료 상태를 다시 본다(이중 안전).

        활성 잡이면 None(아무것도 안 했다). 조각 하나가 실패해도 **그 앞에서 사라진 조각**은
        결과에 남긴다 — 워크스페이스는 갔는데 tar 에서 막히면 디스크는 이미 변했다. 손을 댄
        뒤에는 실패했든 아니든 크기 캐시를 버린다: 반쯤 지운 트리의 옛 크기는 거짓이다.
        """
        if not self._is_terminal(job_id):
            return None
        removed: list[str] = []
        error: str | None = None
        try:
            for name, path in self._volume_parts(job_id):
                if self._remove_tree(path):
                    removed.append(name)
        except OSError as e:
            error = _errname(e)
        self._sizes.pop(job_id, None)
        return PurgeOutcome(tuple(removed), error)

    def apply(self, plan: PurgePlan, now: datetime) -> Applied:
        """계획을 실행한다. **실제로 지운 것**을 돌려준다 — latch 의 분모와 영수증이 이것이다.
        실패한 잡은 표시하지 않고 다음 회차에 다시 시도한다. 크기를 모르는 채 지운 항목은
        바이트에 0 으로 섞지 않고 `unknown_count` 로 따로 센다(리뷰 #88 B1)."""
        deleted: list[int] = []
        failed: list[tuple[int, str]] = []
        partial: list[tuple[int, tuple[str, ...]]] = []
        charged = reclaimable = unknown = 0
        for item in plan.items:
            try:
                outcome = self._purge_volume(item.job_id)
            except OSError as e:  # 조각 밖의 실패 — 아무것도 안 사라진 것으로 본다
                outcome = PurgeOutcome((), _errname(e))
            if outcome is None:
                continue
            if outcome.error is not None:
                failed.append((item.job_id, outcome.error))
                if outcome.removed:
                    partial.append((item.job_id, outcome.removed))
                self.on_error(f"retention: volume {item.job_id}: {outcome.error}")
                continue
            deleted.append(item.job_id)
            if item.bytes is None:
                unknown += 1
            else:
                charged += item.bytes
            reclaimable += item.estimated_reclaimable_bytes or 0
        return Applied(tuple(deleted), tuple(failed), charged, reclaimable, unknown, tuple(partial))

    def _judge_progress(
        self, plan: PurgePlan, result: Applied, free_before: int | None, free_after: int | None
    ) -> None:
        """무진전 latch(결정 62 · 76). 바닥 아래에서 지웠는데 여유가 **지운 회수량의 절반**도 안
        늘었으면 세운다. 분모는 계획이 아니라 삭제에 성공한 항목의 예상 회수량이다 — 분모 0 이면
        판정하지 않는다(부족은 재측정한 계획의 `projected_short_free_bytes` 가 말한다)."""
        if not plan.floor_attempted or free_before is None or free_after is None:
            return
        if result.estimated_reclaimable_bytes <= 0:
            return
        if free_after - free_before < result.estimated_reclaimable_bytes * PROGRESS_RATIO:
            # 지웠는데 여유가 안 늘면 지우는 게 답이 아니다 — 그때부터는 증거를 태우는 일뿐이다.
            if not self.no_progress:
                self.on_error(
                    "retention: deleted volume but free space did not move — "
                    "the floor rule is paused until `rcm gc`"
                )
            self.no_progress = True

    def sweep_volume(self, now: datetime) -> PurgePlan:
        """부피 한 회차 — 계획 **한 번** → 실행 → 여유 다시 재기 → latch 판정."""
        plan, _ = self._run_volume(now)
        return plan

    def _run_volume(self, now: datetime) -> tuple[PurgePlan, dict[str, Any]]:
        """부피 한 회차의 몸통. (계획, `POST /gc` 본문). 작업 락은 부르는 쪽이 잡는다."""
        free_before = self._free_bytes()
        plan = self.plan(now, free_bytes=free_before)
        before = self.storage(now, free_bytes=free_before)  # 그 계획이 잰 스냅샷(B3)
        result = self.apply(plan, now)
        if result.deleted:
            self.log(f"retention: reclaimed volume from {len(result.deleted)} jobs")
        if result.partial:
            self.log(f"retention: partly deleted volume of {len(result.partial)} jobs")
        free_after = self._free_bytes()
        if result.deleted or result.partial:
            # 지운 뒤 다시 재서 회계를 갱신한다. 안 그러면 화면이 **지우기 전** 숫자를 들고 있어
            # 「예산 초과 — 다음 sweep 이 정리한다」를 이미 정리한 뒤에도 한 시간 동안 말한다.
            # 부분 삭제도 디스크를 바꿨으니 같다(리뷰 #88 B2). 표시용 측정이지 두 번째 삭제
            # 계획이 아니다 — `apply` 는 회차당 한 번뿐이다.
            self.plan(now, free_bytes=free_after)
        self._judge_progress(plan, result, free_before, free_after)
        by_id = {i.job_id: i for i in plan.items}
        removed = dict(result.partial)
        body = {
            "dry_run": False,
            "planned": [_item_json(i) for i in plan.items],
            "deleted": [_item_json(by_id[j]) for j in result.deleted],
            "failed": [
                {"job_id": j, "error_code": code, "removed": list(removed.get(j, ()))}
                for j, code in result.failed
            ],
            "freed_bytes": result.charged_bytes,  # = deleted_charged_bytes. 이름만 옛것이다
            "deleted_charged_bytes": result.charged_bytes,
            "unknown_count": result.unknown_count,  # 크기 모르는 채 지운 수 — 0 으로 안 섞는다
            "estimated_reclaimable_bytes": result.estimated_reclaimable_bytes,
            "free_bytes_before": free_before,
            "free_bytes_after": free_after,
            "storage_before": before,
            "storage_after": self.storage(now, free_bytes=free_after),  # 지운 **뒤** 잰 값
        }
        return plan, body

    def gc_report(self, now: datetime, *, dry_run: bool) -> dict[str, Any]:
        """`POST /gc` 의 본문 — **계획과 결과를 가른다**(§5.5).

        계획의 바이트를 실제 회수처럼 내면 거짓이다. 삭제는 실패할 수 있다. `storage_before` 는
        이 호출의 계획이 잰 스냅샷이고(직전 측정값이 아니다 — B3), `storage_after` 는 지운 뒤 다시
        잰 값이다. dry-run 의 `storage_after` 는 null — 예측치를 「실측 뒤」 자리에 넣지 않는다.
        """
        with self._operation_lock:
            if not dry_run:
                self.no_progress = False  # 사람이 보고 부른 것이라 한 번 더 해 본다
                return self._run_volume(now)[1]
            free_before = self._free_bytes()
            plan = self.plan(now, free_bytes=free_before)
            return {
                "dry_run": True,
                "planned": [_item_json(i) for i in plan.items],
                "deleted": [],
                "failed": [],
                "freed_bytes": 0,
                "deleted_charged_bytes": 0,
                "unknown_count": 0,
                "estimated_reclaimable_bytes": plan.estimated_reclaimable_bytes,  # would free
                "free_bytes_before": free_before,
                "free_bytes_after": None,
                "storage_before": self.storage(now, free_bytes=free_before),
                "storage_after": None,
            }

    def gc(self, now: datetime, *, dry_run: bool) -> PurgePlan:
        """사람이 손으로 부르는 청소. dry-run 은 계획만 낸다.

        실제 실행은 무진전 latch 를 **푼다** — 사람이 보고 부른 것이라 한 번 더 해 본다.
        """
        with self._operation_lock:
            if dry_run:
                return self.plan(now)
            self.no_progress = False
            return self._run_volume(now)[0]

    def storage(self, now: datetime, *, free_bytes: int | None = _UNSET) -> dict[str, Any]:
        """화면용 회계(§5.1). **마지막 측정값**을 쓴다 — 상태 요청이 디스크를 훑지 않는다.

        `last_sweep_at` 은 **주기 sweep** 의 것이다. 손으로 부른 `rcm gc` 는 이 값을 갱신하지
        않는다 — 갱신하면 죽은 청소기 스레드가 살아 있는 것처럼 보이고 `/api/health` 가 못 잡는다.
        `free_bytes` 를 주면 그 값을 싣는다(한 회차가 이미 읽은 값 — 두 번 읽지 않는다).
        """
        with self._lock:
            plan, measured_at, last = self._last_plan, self._measured_at, self.last_sweep_at
            checked_at = self._inventory_checked_at
        s = self.config.server
        doc: dict[str, Any] = {
            "workspace_bytes": None,
            "snapshot_bytes": None,
            "volume_bytes": None,
            "evictable_bytes": None,
            "non_evictable_bytes": None,
            "orphan_bytes": None,
            "shared_bytes": None,
            "estimated_reclaimable_bytes": None,
            "limit_bytes": s.workspace_storage_max_bytes or None,
            "min_free_bytes": s.min_free_bytes or None,
            "free_bytes": None,
            "over_budget_bytes": None,
            "projected_short_free_bytes": None,
            "budget_unreachable": False,
            "no_progress": self.no_progress,
            "measured_at": iso(measured_at),
            "inventory_checked_at": iso(checked_at),
            "last_sweep_at": iso(last),
            "next_sweep_at": iso(last + timedelta(seconds=self.interval)) if last else None,
            "error_code": None,
        }
        if plan is None:
            return doc
        doc.update(
            volume_bytes=plan.volume_bytes,
            evictable_bytes=plan.evictable_bytes,
            non_evictable_bytes=plan.non_evictable_bytes,
            shared_bytes=plan.shared_bytes,
            estimated_reclaimable_bytes=plan.evictable_reclaimable_bytes,
            over_budget_bytes=plan.over_budget_bytes,
            projected_short_free_bytes=plan.projected_short_free_bytes,
            budget_unreachable=plan.budget_unreachable,
            error_code=plan.inventory_error,
        )
        with self._lock:
            doc.update(self._last_totals)
        doc["free_bytes"] = self._free_bytes() if free_bytes is _UNSET else free_bytes
        return doc

    # ── 스레드 ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # 잡 하나의 삭제 실패는 sweep_once 가 삼킨다. 그 밖의 예외(DB 오류 등)는 워커처럼
        # 스레드를 죽이고 `dead` 로 남긴다 — /api/health 503. 재시작은 사람이 한다.
        try:
            while True:
                self.sweep_once()
                try:
                    self._on_sweep()
                except Exception as e:  # noqa: BLE001 — 깨우기 실패로 청소기가 죽으면 안 된다
                    self.on_error(f"shelf: wake after sweep: {_errname(e)}")
                if self.stop_event.wait(self.interval):
                    return
        except BaseException as e:  # noqa: BLE001 — 스레드 죽음은 숨기지 않는다
            self.dead = f"janitor died: {type(e).__name__}"
            self.on_error(self.dead)
            raise

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="rcm-retention", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
