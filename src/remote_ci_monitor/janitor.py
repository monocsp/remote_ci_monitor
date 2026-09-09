"""보존 정리 스레드 — 기간 지난 종료 잡의 로그·스냅샷·워크스페이스를 지우고 DB 에 표시한다.

- 무엇을 지울지는 순수 규칙(`core/retention.py`)이 정하고, 여기서는 종료 상태를 **한 번 더**
  확인한다(이중 안전). DB 의 `mark_artifacts_purged` 도 종료 잡만 갱신하므로 삼중이다.
- 경로는 정수 id 로만 만든다. 심볼릭 링크면 링크만 지우고 따라가지 않는다. 실제 경로가
  data_dir 밖이면 건드리지 않는다(프리셋 스크립트가 워크스페이스를 바꿔치기했을 때).
- 삭제에 실패한 잡은 표시하지 않고 다음 sweep 에 다시 시도한다. 오류는 `on_error` 로 표면화.
- 메타데이터(잡 행 · 이벤트 · 합류자)는 `metadata_retention_days` 가 지나고 산출물이 이미
  지워진 잡만 삭제한다 — DB 가 무한히 자라지 않는다.
- 스레드가 예외로 죽으면 `dead` 가 되고 `/api/health` 가 503 을 낸다(조용히 멈추지 않는다).
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from remote_ci_monitor.config import ServerConfig, effective_workspace_retention_days
from remote_ci_monitor.core import artifacts
from remote_ci_monitor.core.model import TERMINAL_STATES, Job
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
from remote_ci_monitor.materialize import blob_path
from remote_ci_monitor.store import Store

#: 이만큼 안 보인 워커는 잊는다(활성 잡이 없을 때만).
WORKER_FORGET_DAYS = 7
CANDIDATE_LIMIT = 1000
#: 워크스페이스 크기를 다시 재기까지의 상한. 최상위 mtime 은 **안쪽 파일의 append 를 못 잡는다**
#: (잡이 남긴 백그라운드 프로세스가 계속 쓰면 오차에 상한이 없다). 하루에 한 번은 오차를 씻는다.
MEASURE_MAX_AGE = timedelta(days=1)
#: 지운 바이트의 이만큼도 여유가 안 늘면 「지워도 소용없다」로 본다(무진전 latch, 결정 62).
PROGRESS_RATIO = 0.5
#: 「인자를 안 줬다」와 「None 을 줬다」를 가르는 표식 — 후자는 「여유를 못 쟀다」는 사실이다.
_UNSET: Any = object()


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
    ):
        self.store = store
        self.config = config
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
        #: job_id → (bytes, 잴 때의 최상위 st_mtime_ns, 잰 시각). 메모리에만 있다.
        self._sizes: dict[int, tuple[int, int, datetime]] = {}
        #: 바닥 규칙으로 지웠는데 여유가 안 늘었다 — 자동 sweep 의 바닥 규칙을 멈춘다(결정 62).
        self.no_progress = False
        self._last_plan: PurgePlan | None = None
        self._measured_at: datetime | None = None
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

    def _remove_tree(self, path: Path) -> None:
        """id 로 만든 경로 하나를 지운다. 링크는 링크만, 밖을 가리키면 손대지 않는다."""
        try:
            st = path.lstat()
        except FileNotFoundError:
            return
        if not (st.st_mode & 0o170000 == 0o040000):  # S_ISDIR 이 아니면(링크 · 파일)
            path.unlink()
            return
        root = self.config.data_dir.resolve()
        real = path.resolve()
        if root != real and root not in real.parents:
            raise OSError(errno.EXDEV, "path resolves outside the data directory")
        shutil.rmtree(path)

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

    def sweep_once(self, now: datetime | None = None) -> int:
        """기간 지난 잡의 산출물을 지우고 표시한다. 지운 잡 수를 돌려준다.

        작업 락을 잡는다 — 손으로 부른 `rcm gc` 와 동시에 돌지 않는다(둘이 같은 잡을 지운다).
        """
        now = now or self.now_fn()
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
        self._sweep_bundles(now)
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
        victims = blobs_to_purge(
            self.store.list_blobs(),
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

    def _measure_dir(self, path: Path) -> int | None:
        """디렉터리 하나가 디스크에서 쥔 바이트. 못 읽으면 None(0 이 아니다).

        `st_blocks × 512` 는 `du` 와 같은 눈금이다 — 희소 파일·APFS 클론에서 `st_size` 는
        디스크가 실제로 쥔 양과 다르다. 심볼릭 링크는 **따라가지 않는다**(`_remove_tree` 와 같은
        규칙). 하드링크는 링크마다 세므로 회계가 보수적으로 커진다.
        """
        total = 0
        stack = [path]
        try:
            while stack:
                current = stack.pop()
                with os.scandir(current) as it:
                    for entry in it:
                        st = entry.stat(follow_symlinks=False)
                        total += getattr(st, "st_blocks", 0) * 512
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
            st = path.lstat()
        except OSError:
            return None
        return total + getattr(st, "st_blocks", 0) * 512

    def _measure_workspace(self, job_id: int, path: Path, now: datetime) -> int | None:
        """캐시를 쓰되 **안 변했는지 확인하고** 쓴다(`lstat` 한 번은 공짜다).

        종료된 잡의 워크스페이스는 보통 다시 안 변하지만 믿고만 있으면 안 된다 — 잡이 남긴
        백그라운드 프로세스나 사람이 손댈 수 있다. 최상위 mtime 이 그대로여도
        `MEASURE_MAX_AGE` 가 지나면 다시 잰다(안쪽 파일의 append 는 mtime 을 안 건드린다).
        """
        try:
            mtime = path.lstat().st_mtime_ns
        except OSError:
            self._sizes.pop(job_id, None)
            return None
        cached = self._sizes.get(job_id)
        if cached is not None and cached[1] == mtime and now - cached[2] < MEASURE_MAX_AGE:
            return cached[0]
        size = self._measure_dir(path)
        if size is None:
            self._sizes.pop(job_id, None)
            return None
        self._sizes[job_id] = (size, mtime, now)
        return size

    def _measure_file(self, path: Path) -> int | None:
        """파일 하나(스냅샷 tar). 없으면 0, 못 읽으면 None. 캐시하지 않는다 — `lstat` 하나다."""
        try:
            return getattr(path.lstat(), "st_blocks", 0) * 512
        except FileNotFoundError:
            return 0
        except OSError:
            return None

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
        ids = self._scan_ids(ws_root)
        tars = {
            i for i in self._scan_ids(jobs_root) if (jobs_root / str(i) / "tree.tar.gz").is_file()
        }
        ids |= tars
        rows = self._jobs_by_id(ids)
        items: list[VolumeItem] = []
        for job_id in sorted(ids):
            job = rows.get(job_id)  # 행이 없다고 DB 가 **말했을 때만** 고아다
            ws_path = ws_root / str(job_id)
            if not ws_path.exists():
                ws_bytes: int | None = 0
            elif job is not None and job.state in TERMINAL_STATES:
                ws_bytes = self._measure_workspace(job_id, ws_path, now)
            else:  # 활성 잡은 자란다. 고아는 주인이 없어 언제 변할지 모른다 — 매번 잰다
                ws_bytes = self._measure_dir(ws_path)
            items.append(
                VolumeItem(
                    job_id=job_id,
                    state=job.state if job is not None else None,
                    finished_at=job.finished_at if job is not None else None,
                    created_at=job.created_at if job is not None else None,
                    workspace_bytes=ws_bytes,
                    snapshot_bytes=self._measure_file(jobs_root / str(job_id) / "tree.tar.gz"),
                )
            )
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
        try:
            items = self.inventory(now)
        except OSError as e:
            items, error = [], f"scan_{_errname(e)}"
        except Exception as e:  # noqa: BLE001 — DB 오류는 「고아」가 아니라 회계 실패다
            items, error = [], f"db_{_errname(e)}"
        if error:
            self.on_error(f"retention: inventory: {error}")
        totals = {
            "workspace_bytes": _sum_or_none(i.workspace_bytes for i in items),
            "snapshot_bytes": _sum_or_none(i.snapshot_bytes for i in items),
            "orphan_bytes": _sum_or_none(i.bytes for i in items if i.state is None),
        }
        with self._lock:
            self._measured_at = None if error else now
            self._last_totals = {k: (None if error else v) for k, v in totals.items()}
        free = self._free_bytes() if free_bytes is _UNSET else free_bytes
        return workspaces_to_purge(
            items, now, self._budget(), free_bytes=free, inventory_error=error
        )

    def _purge_volume(self, job_id: int) -> bool:
        """한 잡의 워크스페이스와 스냅샷 tar. 삭제 직전에 종료 상태를 다시 본다(이중 안전)."""
        if not self._is_terminal(job_id):
            return False
        data = self.config.data_dir
        self._remove_tree(data / "workspaces" / str(job_id))
        self._remove_tree(data / "jobs" / str(job_id) / "tree.tar.gz")
        self._sizes.pop(job_id, None)
        return True

    def apply(self, plan: PurgePlan, now: datetime) -> int:
        """계획을 실행한다. 지운 잡 수. 실패한 잡은 표시하지 않고 다음 회차에 다시 시도한다."""
        gone = 0
        for item in plan.items:
            try:
                if self._purge_volume(item.job_id):
                    gone += 1
            except OSError as e:
                self.on_error(f"retention: volume {item.job_id}: {_errname(e)}")
        return gone

    def sweep_volume(self, now: datetime) -> PurgePlan:
        """부피 한 회차 — 계획 **한 번** → 실행 → 여유 다시 재기 → latch 판정."""
        free_before = self._free_bytes()
        plan = self.plan(now, free_bytes=free_before)
        floor_ran = any(i.reason == "free" for i in plan.items)
        gone = self.apply(plan, now)
        if gone:
            self.log(f"retention: reclaimed volume from {gone} jobs")
        free_after = self._free_bytes()
        if floor_ran and free_before is not None and free_after is not None:
            # 지웠는데 여유가 안 늘면 지우는 게 답이 아니다 — 그때부터는 증거를 태우는 일뿐이다.
            if free_after - free_before < plan.known_freed_bytes * PROGRESS_RATIO:
                if not self.no_progress:
                    self.on_error(
                        "retention: deleted volume but free space did not move — "
                        "the floor rule is paused until `rcm gc`"
                    )
                self.no_progress = True
        with self._lock:
            self._last_plan = plan
        return plan

    def gc(self, now: datetime, *, dry_run: bool) -> PurgePlan:
        """사람이 손으로 부르는 청소. dry-run 은 계획만 낸다.

        실제 실행은 무진전 latch 를 **푼다** — 사람이 보고 부른 것이라 한 번 더 해 본다.
        """
        with self._operation_lock:
            if dry_run:
                return self.plan(now)
            self.no_progress = False
            return self.sweep_volume(now)

    def storage(self, now: datetime) -> dict[str, Any]:
        """화면용 회계(§5.1). **마지막 측정값**을 쓴다 — 상태 요청이 디스크를 훑지 않는다.

        `last_sweep_at` 은 **주기 sweep** 의 것이다. 손으로 부른 `rcm gc` 는 이 값을 갱신하지
        않는다 — 갱신하면 죽은 청소기 스레드가 살아 있는 것처럼 보이고 `/api/health` 가 못 잡는다.
        """
        with self._lock:
            plan, measured_at, last = self._last_plan, self._measured_at, self.last_sweep_at
        s = self.config.server
        doc: dict[str, Any] = {
            "workspace_bytes": None,
            "snapshot_bytes": None,
            "volume_bytes": None,
            "evictable_bytes": None,
            "non_evictable_bytes": None,
            "orphan_bytes": None,
            "limit_bytes": s.workspace_storage_max_bytes or None,
            "min_free_bytes": s.min_free_bytes or None,
            "free_bytes": None,
            "over_budget_bytes": None,
            "projected_short_free_bytes": None,
            "budget_unreachable": False,
            "no_progress": self.no_progress,
            "measured_at": iso(measured_at),
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
            over_budget_bytes=plan.over_budget_bytes,
            projected_short_free_bytes=plan.projected_short_free_bytes,
            budget_unreachable=plan.budget_unreachable,
            error_code=plan.inventory_error,
        )
        with self._lock:
            doc.update(self._last_totals)
        doc["free_bytes"] = self._free_bytes()
        return doc

    # ── 스레드 ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # 잡 하나의 삭제 실패는 sweep_once 가 삼킨다. 그 밖의 예외(DB 오류 등)는 워커처럼
        # 스레드를 죽이고 `dead` 로 남긴다 — /api/health 503. 재시작은 사람이 한다.
        try:
            while True:
                self.sweep_once()
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
