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
import shutil
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remote_ci_monitor.config import ServerConfig
from remote_ci_monitor.core.model import TERMINAL_STATES, Job
from remote_ci_monitor.core.retention import RetentionPolicy, blobs_to_purge, due_for_purge
from remote_ci_monitor.materialize import blob_path
from remote_ci_monitor.store import Store

CANDIDATE_LIMIT = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _errname(e: OSError) -> str:
    return errno.errorcode.get(e.errno or 0, type(e).__name__)


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
        self._lock = threading.Lock()

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

    def sweep_once(self, now: datetime | None = None) -> int:
        """기간 지난 잡의 산출물을 지우고 표시한다. 지운 잡 수를 돌려준다."""
        now = now or self.now_fn()
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
        cutoff = now - timedelta(days=self.config.server.metadata_retention_days)
        deleted = self.store.delete_old_jobs(cutoff)
        if deleted:
            self.log(f"retention: deleted {deleted} job records older than {cutoff:%Y-%m-%d}")
        if self.config.server.snapshot_cache:
            gone = self.sweep_blobs(now)
            if gone:
                self.log(f"retention: purged {gone} snapshot blobs")
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
